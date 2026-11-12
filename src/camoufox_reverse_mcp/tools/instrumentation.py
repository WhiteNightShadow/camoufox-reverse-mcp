"""
instrumentation.py - Source-level JSVMP instrumentation (v1.0.0 unified).

v1.0.1 bugfix: route uses context.route() instead of page.route(),
selective instrumentation for large files, timing diagnostics.
v1.0.2 bugfix: strip Content-Encoding / Transfer-Encoding from fulfill()
headers; Playwright already decoded the body, so passing back the original
encoding header makes Firefox decode plaintext again and silently drops the
response (no responseReceived / loadingFinished event, size 0).
See https://github.com/WhiteNightShadow/camoufox-reverse-mcp/issues/3
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import pathlib
import time

from ..server import mcp, browser_manager
from ..utils.worlds import evaluate_in_world
from ..utils.frames import resolve_frame
from ..utils.js_rewriter import (
    regex_rewrite,
    INSTRUMENT_RUNTIME,
    ACORN_REWRITE_JS_TEMPLATE,
    BREAKS_PLACEHOLDER,
)
from ..utils.ast_rewriter import ast_rewrite as _ast_rewrite_py

# Module-level state for active instrumentation routes
_active_routes: dict[str, dict] = {}
# Content-addressed source sites outlive a route so callers can stop rewriting
# before reading the final page log. Entries are resolved on demand by site_id.
_source_site_registry: dict[str, dict] = {}
# Options of every install in this MCP session. They outlive "stop", so a log
# read afterwards still tells whether calls were tapped at all.
_install_history: dict[str, dict] = {}

DEFAULT_MAX_FILE_SIZE = 2_000_000
_SKIP_RECORD_LIMIT = 50
# Rewritten text above this size is only written to output_path: returned
# inline it would flood the client's context.
_INLINE_REWRITE_LIMIT = 100_000
_CALL_TAP_EFFECTS = (
    "Call taps run each call through MCP helper frames: callee.caller reads null, "
    "the maximum recursion depth drops and stack traces gain frames. Scripts that "
    "fingerprint these can produce different values."
)


# Headers that must be stripped before route.fulfill() — Playwright's
# route.fetch() auto-decodes the body; reusing the original encoding/length
# headers would corrupt the response and Firefox would drop it silently.
_FULFILL_STRIP_HEADERS = (
    "content-length", "Content-Length",
    "content-encoding", "Content-Encoding",
    "transfer-encoding", "Transfer-Encoding",
)


def _with_stage_breaks(body: str) -> str:
    """Write the states armed by stage_break() into a served runtime."""
    from .stage_break import armed_breaks_literal

    literal = armed_breaks_literal()
    if literal is None:
        return body
    return body.replace(BREAKS_PLACEHOLDER, literal, 1)


def _clean_response_headers(headers) -> dict:
    """Return a copy of upstream response headers safe for route.fulfill()."""
    cleaned = dict(headers)
    for key in _FULFILL_STRIP_HEADERS:
        cleaned.pop(key, None)
    return cleaned


@mcp.tool()
async def instrumentation(
    action: str,
    url_pattern: str = "",
    mode: str = "ast",
    tag: str = "vmp",
    rewrite_member_access: bool = True,
    rewrite_calls: bool = False,
    tap_stage_calls: bool = False,
    max_rewrites: int = 20000,
    fallback_on_error: bool = True,
    ignore_csp: bool = False,
    clear_log: bool = True,
    wait_until: str = "load",
    tag_filter: str | None = None,
    type_filter: str | None = None,
    key_filter: str | None = None,
    limit: int = 500,
    clear: bool = False,
    filter_property_names: list[str] | None = None,
    filter_object_names: list[str] | None = None,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    on_oversized: str = "selective",
    include_source_site: bool = False,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
    after_seq: int | None = None,
    seq_window: list[int] | None = None,
    stage: int | None = None,
    source: str | None = None,
    source_path: str | None = None,
    output_path: str | None = None,
) -> dict:
    """JSVMP source-level instrumentation (v0.9.0 unified).

    Replaces instrument_jsvmp_source / get_instrumentation_log /
    stop_instrumentation / reload_with_hooks.

    Args:
        action:
          "install" — register route + AST/regex rewrite on matched scripts.
                      Requires url_pattern. (was: instrument_jsvmp_source)
          "log"     — fetch accumulated tap events from instrumented code.
                      (was: get_instrumentation_log)
          "stop"    — unregister instrumentation route.
                      (was: stop_instrumentation)
          "reload"  — reload page so persistent hooks fire before page JS.
                      (was: reload_with_hooks)
          "status"  — show active instrumentations, their options and the
                      files passed through unchanged, with reasons.
                      (was: get_instrumentation_status)
          "rewrite_source" — apply the same rewrite to local JS without a
                      browser: give `source` (text) or `source_path`, and
                      `output_path` for anything larger than ~100KB. The
                      output runs in Node/vm/jsdom too; events collect in
                      globalThis.__mcp_vmp_log. No size limit applies.
        url_pattern: For "install"/"stop" — glob pattern matching VMP script URLs.
        mode: For "install"/"rewrite_source" — "ast" (esprima then local
            bundled Acorn) or "regex" (conservative whole-program subset;
            unsupported input skipped).
        tag: For "install"/"rewrite_source"/"log" — group identifier.
        rewrite_member_access: tap obj[key] reads (default True). Start here.
        rewrite_calls: tap fn(args) and obj.m(args) calls (default False since
            v1.10.0). Turn it on only when call events are needed: call taps
            add helper frames, so callee.caller reads null and the maximum
            recursion depth drops, which fingerprinting code can notice.
        tap_stage_calls: rewrite the flattened-VMP dispatcher shape
            `return fn.apply(this, [<int>].concat(Array.prototype.slice
            .call(arguments)))` into per-state `tap_stage` events (state id `p`,
            argc, first args previews, return preview), pushed when the state
            returns. Events recorded while a state runs carry it as `stage`.
            This is a literal match, so it also works when the rest of the
            file is not parseable (status then shows "(stage taps only)"), and
            it is independent of `mode`/`filter_*`.
        include_source_site: attach a stable site_id to tap events, plus an
            original-source range map in the log response. Default False.
        max_rewrites: hard cap on rewrites per file; status lists capped files.
        fallback_on_error: try conservative regex on AST failure only without
            property/object filters; otherwise pass through unchanged.
        ignore_csp: For "install" — skip CSP pre-flight check.
        clear_log: For "reload" — clear JSVMP logs before reload.
        wait_until: For "reload" — "load" / "domcontentloaded" / "networkidle".
        tag_filter: For "log" — filter by tag.
        type_filter: For "log" — "tap_get", "tap_call", "tap_method",
            "tap_call_err", "tap_stage", "tap_stage_break".
        key_filter: For "log" — substring match on property/method name.
        after_seq: For "log" — only events with seq > after_seq, oldest first;
            page with the returned next_after_seq.
        seq_window: For "log" — [start, end], events with start <= seq <= end.
        stage: For "log" — events recorded while dispatcher state `stage` ran,
            plus that state's own tap_stage events.
        limit: For "log" — max entries: the most recent ones, or the first
            ones when after_seq/seq_window is set.
        clear: For "log" — clear log after retrieval in the selected main world.
        frame_url: For log; select a target frame by URL pattern.
        frame_name: For log; select a target frame by name.
        frame_index: For log; current frame snapshot index, not a persistent identity.
            Source scripts run in the main world; logs are always read there.
            Same-origin child frames log into the top frame's log with
            frame_url/frame_depth, so a removed iframe keeps its events.
        filter_property_names: For AST — only rewrite reads/methods of these
            property names (e.g. ['userAgent', 'platform', 'webdriver']).
        filter_object_names: For AST — only rewrite when the static base
            object path matches (e.g. ['navigator', 'this.bytecode']). Dynamic
            object identity is not inferred. Regex mode rejects nonempty filters.
        max_file_size: For "install" — files larger than this (bytes) trigger
            on_oversized behavior. Default 2,000,000.
        on_oversized: For "install" — "selective" (require filters), "skip",
            or "force" (full rewrite anyway). Default "selective".
        source: For "rewrite_source" — JS text to rewrite.
        source_path: For "rewrite_source" — local JS file to rewrite.
        output_path: For "rewrite_source" — where to write the rewritten JS
            (must differ from source_path); with include_source_site the site
            map goes to `<output_path>.sites.json`.

    Returns:
        dict with action-specific results. "log" also returns seq, dropped
        (events lost to the 20000-entry cap) and the options each install used.

    IMPORTANT — timing for sync-loaded scripts (e.g. webmssdk):
        Route interception only catches requests made AFTER the route is
        registered. For scripts loaded via <script src> during page load,
        you MUST call instrumentation(action='install') BEFORE navigate().
        Pattern:
          1. launch_browser()
          2. instrumentation(action='install', url_pattern='**/webmssdk*')
          3. navigate("https://www.douyin.com/...")
        If called after navigate, use instrumentation(action='reload') to
        re-trigger page load with routes active.
    """
    if action == "install":
        return await _install(
            url_pattern=url_pattern,
            mode=mode,
            tag=tag,
            rewrite_member_access=rewrite_member_access,
            rewrite_calls=rewrite_calls,
            tap_stage_calls=tap_stage_calls,
            max_rewrites=max_rewrites,
            fallback_on_error=fallback_on_error,
            ignore_csp=ignore_csp,
            filter_property_names=filter_property_names,
            filter_object_names=filter_object_names,
            max_file_size=max_file_size,
            on_oversized=on_oversized,
            include_source_site=include_source_site,
        )
    elif action == "log":
        return await _get_log(tag_filter, type_filter, key_filter, limit, clear, frame_url,
                              frame_name, frame_index, after_seq=after_seq,
                              seq_window=seq_window, stage=stage)
    elif action == "stop":
        return await _stop(url_pattern or None)
    elif action == "reload":
        return await _reload_with_hooks(clear_log, wait_until)
    elif action == "status":
        return _get_status()
    elif action == "rewrite_source":
        return await asyncio.to_thread(
            _rewrite_source,
            source=source, source_path=source_path, output_path=output_path,
            mode=mode, tag=tag, rewrite_member_access=rewrite_member_access,
            rewrite_calls=rewrite_calls, tap_stage_calls=tap_stage_calls,
            max_rewrites=max_rewrites, fallback_on_error=fallback_on_error,
            filter_property_names=filter_property_names,
            filter_object_names=filter_object_names,
            include_source_site=include_source_site,
        )
    else:
        return {"error": f"unknown action: {action}. Use install/log/stop/reload/status/rewrite_source"}


def _skip_hint(reason: str, size: int | None = None) -> str | None:
    if reason == "oversized_without_selected_properties":
        return (f"larger than max_file_size: reinstall with max_file_size>={_suggested_size(size or 0)}, "
                "or pass filter_property_names/filter_object_names, or on_oversized='force'")
    if reason == "oversized":
        return (f"larger than max_file_size: reinstall with max_file_size>={_suggested_size(size or 0)} "
                "or on_oversized='force'")
    if reason == "non_success_response":
        return "the server did not answer 2xx; the response was passed on unchanged"
    if reason in ("no_eligible_rewrites", "unsupported_program_syntax"):
        return "nothing to tap with these options (check filters, mode and last_error)"
    return None


def _suggested_size(size: int) -> int:
    return (size // 100_000 + 1) * 100_000


def _record_file(stats: dict, key: str, url: str, entry: dict) -> None:
    """Keep the latest record per URL, bounded, in insertion order."""
    records = stats.setdefault(key, {})
    records.pop(url, None)
    records[url] = {"url": url, **entry}
    while len(records) > _SKIP_RECORD_LIMIT:
        records.pop(next(iter(records)))


def _record_skip(stats: dict, url: str, size: int, reason: str, **extra) -> None:
    stats["files_passed_through"] += 1
    stats["last_skip_reason"] = reason
    entry = {"bytes": size, "reason": reason, **extra}
    if reason.startswith("oversized"):
        entry["suggested_max_file_size"] = _suggested_size(size)
    hint = _skip_hint(reason, size)
    if hint:
        entry["hint"] = hint
    _record_file(stats, "skipped_files", url, entry)


def _route_summary(pattern: str, info: dict | None, options: dict) -> dict:
    """Options and per-file outcomes of one install, for status and log."""
    summary = {"pattern": pattern, "active": info is not None, **options}
    if info is not None:
        stats = info["stats"]
        summary.update({
            "files_seen": stats.get("files_seen", 0),
            "files_rewritten": stats.get("files_rewritten", 0),
            "files_passed_through": stats.get("files_passed_through", 0),
            "skipped_files": list(stats.get("skipped_files", {}).values()),
            "capped_files": list(stats.get("capped_files", {}).values()),
        })
    return summary


def _get_status() -> dict:
    return {
        "active_patterns": [
            {
                "pattern": pat, "mode": info["mode"], "tag": info["tag"],
                "files_rewritten": info["stats"]["files_rewritten"],
                "total_edits": info["stats"]["total_edits"],
                "last_url": info["stats"]["last_url"],
                "last_mode_used": info["stats"]["last_mode_used"],
                "files_seen": info["stats"].get("files_seen", 0),
                "files_passed_through": info["stats"].get("files_passed_through", 0),
                "cache_hits": info["stats"].get("cache_hits", 0),
                "last_parser_backend": info["stats"].get("last_parser_backend"),
                "last_skip_reason": info["stats"].get("last_skip_reason"),
                "last_error": info["stats"].get("last_error"),
                "skipped_files": list(info["stats"].get("skipped_files", {}).values()),
                "capped_files": list(info["stats"].get("capped_files", {}).values()),
                "cached_urls": len(info["cache"]),
                "include_source_site": info.get("include_source_site", False),
                "source_site_count": len(info.get("source_sites", {})),
                "options": info.get("options", {}),
            }
            for pat, info in _active_routes.items()
        ],
        "total_patterns": len(_active_routes),
    }


def _rewrite_text(src: str, *, mode: str, tag: str, rewrite_member_access: bool,
                  rewrite_calls: bool, tap_stage_calls: bool, max_rewrites: int,
                  fallback_on_error: bool, prop_filter: list[str] | None,
                  obj_filter: list[str] | None, include_source_site: bool) -> dict:
    """Run the configured rewriter on one decoded source text.

    Returns rewritten (None when the parser failed and no fallback applied),
    the rewriter stats, the mode actually used, the edit count and the parser
    error, if any. Shared by the route handler and rewrite_source.
    """
    rewritten = None
    rewrite_stats: dict = {}
    mode_used = mode
    error = None
    if mode == "ast":
        rewritten, rewrite_stats = _ast_rewrite_py(
            src, tag=tag, rewrite_member_access=rewrite_member_access,
            rewrite_calls=rewrite_calls, max_edits=max_rewrites,
            filter_property_names=prop_filter, filter_object_names=obj_filter,
            include_source_site=include_source_site, tap_stage_calls=tap_stage_calls,
        )
        if rewritten is None:
            error = rewrite_stats.get("error", "AST parse/rewrite failed")
            if fallback_on_error and not (prop_filter or obj_filter):
                mode_used = "regex (fallback)"
                rewritten, rewrite_stats = regex_rewrite(
                    src, tag=tag, rewrite_member_access=rewrite_member_access,
                    max_rewrites=max_rewrites, include_source_site=include_source_site,
                    tap_stage_calls=tap_stage_calls)
    elif mode == "regex":
        rewritten, rewrite_stats = regex_rewrite(
            src, tag=tag, rewrite_member_access=rewrite_member_access,
            max_rewrites=max_rewrites, include_source_site=include_source_site,
            tap_stage_calls=tap_stage_calls)
    else:
        raise ValueError("mode must be ast or regex")
    edit_count = rewrite_stats.get("edits")
    if edit_count is None:
        edit_count = (rewrite_stats.get("member_access_rewrites", 0)
                      + rewrite_stats.get("stage_rewrites", 0))
    if rewrite_stats.get("stage_only"):
        # The parser could not read this file; only stage taps were added.
        mode_used = f"{mode_used} (stage taps only)"
        error = rewrite_stats.get("error")
    return {"rewritten": rewritten, "stats": rewrite_stats, "mode_used": mode_used,
            "edit_count": edit_count or 0, "error": error,
            "capped": bool(rewrite_stats.get("edits_capped") or rewrite_stats.get("stage_capped"))}


def _rewrite_source(*, source, source_path, output_path, mode, tag, rewrite_member_access,
                    rewrite_calls, tap_stage_calls, max_rewrites, fallback_on_error,
                    filter_property_names, filter_object_names, include_source_site) -> dict:
    """rewrite_source: the install rewrite, applied to local text or a file."""
    try:
        if (source is None) == (source_path is None):
            return {"error": "pass exactly one of source (JS text) or source_path (a file)"}
        if mode not in ("ast", "regex"):
            return {"error": "mode must be 'ast' or 'regex'"}
        if mode == "regex" and (filter_property_names or filter_object_names):
            return {"error": "Property/object filters require mode='ast'; regex cannot preserve selective scope"}
        in_path = None
        if source_path is not None:
            in_path = pathlib.Path(source_path).expanduser().resolve()
            raw = in_path.read_bytes()
            try:
                src = raw.decode("utf-8")
            except UnicodeDecodeError:
                src = raw.decode("latin-1")
        else:
            src = source
        out_path = pathlib.Path(output_path).expanduser().resolve() if output_path else None
        if out_path is not None and in_path is not None and out_path == in_path:
            return {"error": "output_path must differ from source_path; the original is kept"}

        outcome = _rewrite_text(
            src, mode=mode, tag=tag, rewrite_member_access=rewrite_member_access,
            rewrite_calls=rewrite_calls, tap_stage_calls=tap_stage_calls,
            max_rewrites=max_rewrites, fallback_on_error=fallback_on_error,
            prop_filter=filter_property_names or None, obj_filter=filter_object_names or None,
            include_source_site=include_source_site)
        rewrite_stats = outcome["stats"]
        result = {
            "action": "rewrite_source",
            "source_path": str(in_path) if in_path else None,
            "source_bytes": len(src.encode("utf-8")),
            "source_sha256": hashlib.sha256(src.encode("utf-8")).hexdigest(),
            "mode_used": outcome["mode_used"],
            "parser_backend": rewrite_stats.get("parser_backend"),
            "options": {"mode": mode, "tag": tag, "rewrite_member_access": rewrite_member_access,
                        "rewrite_calls": rewrite_calls, "tap_stage_calls": tap_stage_calls,
                        "include_source_site": include_source_site, "max_rewrites": max_rewrites,
                        "filter_property_names": filter_property_names,
                        "filter_object_names": filter_object_names},
            "edits": outcome["edit_count"],
            "edits_capped": outcome["capped"],
        }
        for key in ("member_edits", "call_edits", "method_edits", "stage_edits",
                    "member_access_rewrites", "stage_rewrites"):
            if key in rewrite_stats:
                result[key] = rewrite_stats[key]
        if outcome["error"]:
            result["parser_error"] = outcome["error"]
        rewritten = outcome["rewritten"]
        if rewritten is None or not outcome["edit_count"]:
            result["status"] = "unchanged"
            result["reason"] = (rewrite_stats.get("skipped_reason") or outcome["error"]
                                or "no_eligible_rewrites")
            return result

        sites = rewrite_stats.get("source_sites", []) if include_source_site else []
        result["status"] = "rewritten"
        result["output_bytes"] = len(rewritten.encode("utf-8"))
        result["log_location"] = "globalThis.__mcp_vmp_log (window.__mcp_vmp_log in a browser)"
        if out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rewritten, encoding="utf-8", newline="")
            result["output_path"] = str(out_path)
            if include_source_site:
                sites_path = out_path.with_name(out_path.name + ".sites.json")
                sites_path.write_text(json.dumps({
                    "source_id": rewrite_stats.get("source_id"),
                    "source_sha256": rewrite_stats.get("source_sha256"),
                    "offset_unit": "unicode_code_point", "range_semantics": "half_open",
                    "sites": sites}, ensure_ascii=False), encoding="utf-8")
                result["source_sites_path"] = str(sites_path)
                result["source_site_count"] = len(sites)
        else:
            if result["output_bytes"] > _INLINE_REWRITE_LIMIT:
                result["status"] = "not_returned"
                result["error"] = (f"rewritten source is {result['output_bytes']} bytes; "
                                   "pass output_path to write it to a file")
                return result
            result["rewritten"] = rewritten
            if include_source_site:
                result["source_sites"] = sites
        return result
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def _install(url_pattern, mode, tag, rewrite_member_access,
                   rewrite_calls, max_rewrites, fallback_on_error, ignore_csp,
                   filter_property_names, filter_object_names,
                   max_file_size, on_oversized,
                   include_source_site=False,
                   tap_stage_calls=False) -> dict:
    try:
        if mode not in ("ast", "regex") or on_oversized not in ("selective", "skip", "force"):
            return {"error": "Invalid mode or on_oversized policy"}
        if not url_pattern:
            return {"error": "url_pattern is required for action='install'"}
        if mode == "regex" and (filter_property_names or filter_object_names):
            return {"error": "Property/object filters require mode='ast'; regex cannot preserve selective scope"}

        # v1.0.1: use context.route() instead of page.route() for timing
        ctx = browser_manager.contexts.get("default")
        if ctx is None:
            await browser_manager._ensure_browser()
            ctx = browser_manager.contexts.get("default")
        if ctx is None:
            return {"error": "no browser context available"}

        # Check if page already navigated (timing warning)
        page_url = None
        warnings: list[str] = []
        try:
            page = await browser_manager.get_active_page()
            page_url = page.url
            if page_url and page_url != "about:blank":
                warnings.append(
                    "Route registered after page already loaded. Scripts "
                    "fetched during the initial page load will NOT be "
                    "intercepted. Call instrumentation(action='reload') "
                    "to re-trigger page load with this route active, or "
                    "install BEFORE navigate() next time."
                )
        except Exception:
            pass
        if rewrite_calls:
            warnings.append(_CALL_TAP_EFFECTS)

        cache: dict[str, dict] = {}
        source_sites: dict[str, dict] = {}
        stats = {"files_rewritten": 0, "total_edits": 0, "last_url": None,
                 "last_mode_used": None, "files_seen": 0, "files_passed_through": 0,
                 "cache_hits": 0, "last_parser_backend": None, "last_skip_reason": None,
                 "last_error": None, "skipped_files": {}, "capped_files": {}}

        # Build filter sets for selective instrumentation
        prop_filter_set = set(filter_property_names) if filter_property_names else None
        obj_filter_set = set(filter_object_names) if filter_object_names else None
        options = {
            "mode": mode, "tag": tag,
            "rewrite_member_access": rewrite_member_access,
            "rewrite_calls": rewrite_calls,
            "tap_stage_calls": tap_stage_calls,
            "include_source_site": include_source_site,
            "filter_property_names": filter_property_names,
            "filter_object_names": filter_object_names,
            "max_file_size": max_file_size,
            "on_oversized": on_oversized,
            "max_rewrites": max_rewrites,
        }

        async def route_handler(route):
            resp = None
            body_bytes = None
            req_url = None
            try:
                req_url = route.request.url
                stats["files_seen"] += 1
                stats["last_url"] = req_url
                stats["last_error"] = None
                stats["last_skip_reason"] = None
                # Obtain the current response exactly once. URL-only caches
                # suppress cookies/status and silently reuse an old SDK revision.
                resp = await route.fetch(max_redirects=0)
                body_bytes = await resp.body()
                headers = _clean_response_headers(resp.headers)
                if not 200 <= resp.status < 300:
                    _record_skip(stats, req_url, len(body_bytes), "non_success_response",
                                 status=resp.status)
                    await route.fulfill(status=resp.status, headers=headers, body=body_bytes)
                    return
                try:
                    src = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    src = body_bytes.decode("latin-1")
                digest = hashlib.sha256(body_bytes).hexdigest()
                cached = cache.get(req_url)
                if cached and cached["sha256"] == digest:
                    stats["cache_hits"] += 1
                    stats["last_mode_used"] = cached["mode"]
                    stats["last_parser_backend"] = cached["parser_backend"]
                    headers["content-type"] = "application/javascript; charset=utf-8"
                    await route.fulfill(status=resp.status, headers=headers,
                                        body=_with_stage_breaks(cached["body"]))
                    return
                if len(body_bytes) > max_file_size:
                    skip = on_oversized == "skip" or (on_oversized == "selective" and not (prop_filter_set or obj_filter_set))
                    if skip:
                        stats["last_mode_used"] = "passthrough"
                        _record_skip(stats, req_url, len(body_bytes),
                                     "oversized_without_selected_properties"
                                     if on_oversized == "selective" else "oversized",
                                     max_file_size=max_file_size)
                        await route.fulfill(status=resp.status, headers=headers, body=body_bytes)
                        return
                # Parsing a large file takes seconds; keep the event loop free.
                outcome = await asyncio.to_thread(
                    _rewrite_text, src, mode=mode, tag=tag,
                    rewrite_member_access=rewrite_member_access,
                    rewrite_calls=rewrite_calls, tap_stage_calls=tap_stage_calls,
                    max_rewrites=max_rewrites, fallback_on_error=fallback_on_error,
                    prop_filter=list(prop_filter_set) if prop_filter_set else None,
                    obj_filter=list(obj_filter_set) if obj_filter_set else None,
                    include_source_site=include_source_site,
                )
                rewritten = outcome["rewritten"]
                rewrite_stats = outcome["stats"]
                mode_used = outcome["mode_used"]
                edit_count = outcome["edit_count"]
                stats["last_error"] = outcome["error"]
                stats["last_parser_backend"] = rewrite_stats.get("parser_backend")
                if rewritten is None or not edit_count:
                    stats["last_mode_used"] = "passthrough"
                    _record_skip(stats, req_url, len(body_bytes),
                                 rewrite_stats.get("skipped_reason", "no_eligible_rewrites"),
                                 **({"error": outcome["error"]} if outcome["error"] else {}))
                    await route.fulfill(status=resp.status, headers=headers, body=body_bytes)
                    return
                stats["skipped_files"].pop(req_url, None)
                if outcome["capped"]:
                    _record_file(stats, "capped_files", req_url, {
                        "edits": edit_count, "max_rewrites": max_rewrites,
                        "hint": "later sites in this file are not tapped; raise max_rewrites "
                                "or narrow with filter_property_names"})
                else:
                    stats["capped_files"].pop(req_url, None)
                if include_source_site:
                    for site in rewrite_stats.get("source_sites", []):
                        metadata = {**site, "source_id": rewrite_stats.get("source_id"),
                                    "source_sha256": rewrite_stats.get("source_sha256"),
                                    "urls": [req_url], "offset_unit": "unicode_code_point",
                                    "range_semantics": "half_open"}
                        existing = _source_site_registry.get(site["site_id"])
                        if existing:
                            metadata["urls"] = list(dict.fromkeys([*existing.get("urls", []), req_url]))
                        source_sites[site["site_id"]] = metadata
                        _source_site_registry[site["site_id"]] = metadata
                if len(cache) >= 128 and req_url not in cache:
                    cache.pop(next(iter(cache)))
                cache[req_url] = {"sha256": digest, "body": rewritten,
                                  "mode": mode_used, "parser_backend": stats["last_parser_backend"]}
                stats["files_rewritten"] += 1
                stats["total_edits"] += edit_count
                stats["last_mode_used"] = mode_used
                headers["content-type"] = "application/javascript; charset=utf-8"
                await route.fulfill(status=resp.status, headers=headers,
                                    body=_with_stage_breaks(rewritten))
            except Exception as exc:
                stats["last_error"] = str(exc)
                # Never continue_ after route.fetch: that could execute a POST
                # twice. Preserve the original response or fail this request.
                try:
                    if resp is not None and body_bytes is not None:
                        _record_skip(stats, req_url or "?", len(body_bytes), "rewrite_error",
                                     error=str(exc))
                        await route.fulfill(status=resp.status,
                                            headers=_clean_response_headers(resp.headers),
                                            body=body_bytes)
                    else:
                        await route.abort("failed")
                except Exception:
                    pass

        # v1.0.1: context-level route (catches requests from page load)
        await ctx.route(url_pattern, route_handler)
        _active_routes[url_pattern] = {
            "handler": route_handler, "cache": cache, "stats": stats,
            "mode": mode, "tag": tag, "context": ctx,
            "include_source_site": include_source_site,
            "source_sites": source_sites,
            "options": options,
        }
        _install_history[url_pattern] = options
        result = {
            "status": "instrumenting", "pattern": url_pattern,
            "mode": mode, "tag": tag,
            "route_level": "context",
            "selective": bool(prop_filter_set or obj_filter_set),
            "include_source_site": include_source_site,
            "tap_stage_calls": tap_stage_calls,
            "rewrite_member_access": rewrite_member_access,
            "rewrite_calls": rewrite_calls,
            "max_file_size": max_file_size,
            "on_oversized": on_oversized,
            "max_rewrites": max_rewrites,
            "filter_property_names": filter_property_names,
            "filter_object_names": filter_object_names,
            "note": ("Route active. Navigate or reload to trigger rewrite. "
                     "instrumentation(action='status') lists files passed through unchanged."),
        }
        if not rewrite_calls:
            result["calls_note"] = ("Calls are not tapped (default since v1.10.0); pass "
                                    "rewrite_calls=True when tap_call/tap_method events are needed.")
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


_LOG_READER = """() => {
    const opts = %s;
    const log = Array.isArray(window.__mcp_vmp_log) ? window.__mcp_vmp_log : [];
    const meta = window.__mcp_vmp_meta;
    let entries = log;
    if (opts.after !== null || opts.lo !== null || opts.hi !== null) {
        entries = log.filter((e) => {
            const s = e && typeof e.seq === 'number' ? e.seq : null;
            if (s === null) return false;
            if (opts.after !== null && !(s > opts.after)) return false;
            if (opts.lo !== null && s < opts.lo) return false;
            if (opts.hi !== null && s > opts.hi) return false;
            return true;
        });
    }
    return {entries: entries, total: log.length,
            dropped: meta && typeof meta.dropped === 'number' ? meta.dropped : 0,
            next_seq: meta && typeof meta.seq === 'number' ? meta.seq : null};
}"""

_LOG_CLEAR = """() => {
    window.__mcp_vmp_log = [];
    if (window.__mcp_vmp_meta) window.__mcp_vmp_meta.dropped = 0;
    return true;
}"""


async def _get_log(tag_filter, type_filter, key_filter, limit, clear, frame_url=None, frame_name=None,
                   frame_index=None, after_seq=None, seq_window=None, stage=None) -> dict:
    try:
        if not 1 <= limit <= 20000:
            return {"error": "limit must be between 1 and 20000"}
        lo = hi = None
        if seq_window is not None:
            if (not isinstance(seq_window, (list, tuple)) or len(seq_window) != 2
                    or not all(isinstance(v, int) for v in seq_window) or seq_window[0] > seq_window[1]):
                return {"error": "seq_window must be [start, end] with integers start <= end"}
            lo, hi = seq_window
        page = await browser_manager.get_active_page()
        target, frame = (resolve_frame(page, frame_url=frame_url, frame_name=frame_name, frame_index=frame_index)
                         if any(value is not None for value in (frame_url, frame_name, frame_index)) else (page, None))
        reader = _LOG_READER % json.dumps({"after": after_seq, "lo": lo, "hi": hi})
        raw, backend, warning = await evaluate_in_world(target, reader, "main")
        if isinstance(raw, list):
            # A page whose runtime predates v1.10.0 (or a bare array).
            data, raw_count, dropped, next_seq = raw, len(raw), None, None
        elif isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            data, raw_count = raw["entries"], raw.get("total", len(raw["entries"]))
            dropped, next_seq = raw.get("dropped"), raw.get("next_seq")
        else:
            return {"error": "Instrumentation log is not an array in the selected main world"}
        if tag_filter:
            data = [d for d in data if d.get("tag") == tag_filter]
        if type_filter:
            data = [d for d in data if d.get("type") == type_filter]
        if key_filter:
            data = [d for d in data
                    if key_filter in (d.get("key") or "")
                    or key_filter in (d.get("method") or "")
                    or key_filter in (d.get("name") or "")
                    or (d.get("p") is not None and key_filter in str(d.get("p")))]
        if stage is not None:
            data = [d for d in data
                    if d.get("stage") == stage
                    or (d.get("type") in ("tap_stage", "tap_stage_break") and d.get("p") == stage)]

        key_count: dict[str, int] = {}
        method_count: dict[str, int] = {}
        function_count: dict[str, int] = {}
        stage_count: dict[str, int] = {}
        break_count: dict[str, int] = {}
        site_count: dict[str, int] = {}
        frame_count: dict[str, int] = {}
        for e in data:
            if e.get("type") == "tap_get":
                k = e.get("key", "?")
                key_count[k] = key_count.get(k, 0) + 1
            elif e.get("type") == "tap_method":
                m = f"{e.get('objType', '?')}.{e.get('method', '?')}"
                method_count[m] = method_count.get(m, 0) + 1
            elif e.get("type") == "tap_stage":
                k = str(e.get("p", "?"))
                stage_count[k] = stage_count.get(k, 0) + 1
            elif e.get("type") == "tap_stage_break":
                k = str(e.get("p", "?"))
                break_count[k] = break_count.get(k, 0) + 1
            elif e.get("type") in ("tap_call", "tap_call_err"):
                fn = e.get("name", "?")
                function_count[fn] = function_count.get(fn, 0) + 1
            site_id = e.get("site_id")
            if site_id:
                site_count[site_id] = site_count.get(site_id, 0) + 1
            if e.get("frame_url") is not None:
                f = str(e.get("frame_url"))
                frame_count[f] = frame_count.get(f, 0) + 1

        forward = after_seq is not None or seq_window is not None
        entries = (data[:limit] if forward else data[-limit:]) if len(data) > limit else data
        returned_site_ids = {
            e["site_id"] for e in entries if e.get("site_id")
        }
        hot_sites = dict(
            sorted(site_count.items(), key=lambda x: -x[1])[:30]
        )
        wanted_site_ids = returned_site_ids | set(hot_sites)
        source_site_map: dict[str, dict] = {}
        if wanted_site_ids:
            for site_id in wanted_site_ids:
                site = _source_site_registry.get(site_id)
                if site:
                    source_site_map[site_id] = site
            for info in _active_routes.values():
                for site_id, site in info.get("source_sites", {}).items():
                    if site_id in wanted_site_ids and site_id not in source_site_map:
                        source_site_map[site_id] = site
        unresolved_site_ids = sorted(wanted_site_ids - set(source_site_map))

        if clear:
            await evaluate_in_world(target, _LOG_CLEAR, "main")
        seqs = [e["seq"] for e in entries if isinstance(e.get("seq"), int)]
        result = {
            "entries": entries,
            "world": "main", "frame": frame, "execution_backend": backend,
            "warning": warning, "raw_total_entries": raw_count,
            "dropped": dropped,
            "possibly_capped": bool(dropped) or raw_count >= 20000,
            "capture_limit": 20000,
            "next_seq": next_seq,
            "total_entries": len(data), "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "hot_keys": dict(sorted(key_count.items(), key=lambda x: -x[1])[:30]),
                "hot_methods": dict(sorted(method_count.items(), key=lambda x: -x[1])[:30]),
                "hot_functions": dict(sorted(function_count.items(), key=lambda x: -x[1])[:30]),
                **({"hot_stages": dict(sorted(stage_count.items(),
                                             key=lambda x: -int(x[1]))[:60])}
                   if stage_count else {}),
                **({"stage_breaks": dict(sorted(break_count.items(),
                                                key=lambda x: -int(x[1]))[:30])}
                   if break_count else {}),
                **({"frames": frame_count} if frame_count else {}),
            },
        }
        if seqs:
            result["seq_range"] = [seqs[0], seqs[-1]]
        if forward and len(data) > limit and seqs:
            result["next_after_seq"] = seqs[-1]
        installs = [_route_summary(pattern, _active_routes.get(pattern), options)
                    for pattern, options in _install_history.items()]
        if installs:
            result["instrumentation"] = installs
            if not data:
                skipped = [f for i in installs for f in i.get("skipped_files", [])]
                if skipped:
                    result["hint"] = (f"{len(skipped)} matching script(s) were passed through "
                                      "unchanged; see instrumentation[].skipped_files for the reasons")
        if site_count:
            result["summary"]["hot_sites"] = hot_sites
            result["source_sites"] = source_site_map
            if unresolved_site_ids:
                result["unresolved_source_site_ids"] = unresolved_site_ids
        return result
    except Exception as e:
        return {"error": str(e)}


async def _stop(url_pattern) -> dict:
    try:
        removed = []
        if url_pattern is not None:
            if url_pattern in _active_routes:
                info = _active_routes[url_pattern]
                ctx = info.get("context")
                try:
                    if ctx:
                        await ctx.unroute(url_pattern)
                    else:
                        page = await browser_manager.get_active_page()
                        await page.unroute(url_pattern)
                except Exception:
                    pass
                del _active_routes[url_pattern]
                removed.append(url_pattern)
        else:
            for pat in list(_active_routes.keys()):
                info = _active_routes[pat]
                ctx = info.get("context")
                try:
                    if ctx:
                        await ctx.unroute(pat)
                    else:
                        page = await browser_manager.get_active_page()
                        await page.unroute(pat)
                except Exception:
                    pass
                del _active_routes[pat]
                removed.append(pat)
        return {"status": "stopped", "removed": removed}
    except Exception as e:
        return {"error": str(e)}


async def _reload_with_hooks(clear_log: bool = True, wait_until: str = "load") -> dict:
    try:
        page = await browser_manager.get_active_page()
        if clear_log:
            try:
                await page.evaluate("""() => {
                    if (window.__mcp_jsvmp_log) window.__mcp_jsvmp_log.length = 0;
                    if (window.__mcp_prop_access_log) window.__mcp_prop_access_log.length = 0;
                    if (window.__mcp_cookie_log) window.__mcp_cookie_log.length = 0;
                }""")
            except Exception:
                pass
        browser_manager.reset_nav_responses()
        resp = await page.reload(wait_until=wait_until)
        chain = list(browser_manager._nav_responses)
        final_status = None
        for r in reversed(chain):
            if r["url"] == page.url or r.get("resource_type") == "document":
                final_status = r["status"]
                break
        return {
            "url": page.url, "title": await page.title(),
            "initial_status": resp.status if resp else None,
            "final_status": final_status or (resp.status if resp else None),
            "redirect_chain": chain,
        }
    except Exception as e:
        return {"error": str(e)}
