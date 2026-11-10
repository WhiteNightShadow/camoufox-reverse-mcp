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
import time
import hashlib

from ..server import mcp, browser_manager
from ..utils.worlds import evaluate_in_world
from ..utils.frames import resolve_frame
from ..utils.js_rewriter import (
    regex_rewrite,
    INSTRUMENT_RUNTIME,
    ACORN_REWRITE_JS_TEMPLATE,
)
from ..utils.ast_rewriter import ast_rewrite as _ast_rewrite_py

# Module-level state for active instrumentation routes
_active_routes: dict[str, dict] = {}
# Content-addressed source sites outlive a route so callers can stop rewriting
# before reading the final page log. Entries are resolved on demand by site_id.
_source_site_registry: dict[str, dict] = {}


# Headers that must be stripped before route.fulfill() — Playwright's
# route.fetch() auto-decodes the body; reusing the original encoding/length
# headers would corrupt the response and Firefox would drop it silently.
_FULFILL_STRIP_HEADERS = (
    "content-length", "Content-Length",
    "content-encoding", "Content-Encoding",
    "transfer-encoding", "Transfer-Encoding",
)


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
    rewrite_calls: bool = True,
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
    max_file_size: int = 200_000,
    on_oversized: str = "selective",
    include_source_site: bool = False,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
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
          "status"  — show active instrumentations and stats.
                      (was: get_instrumentation_status)
        url_pattern: For "install"/"stop" — glob pattern matching VMP script URLs.
        mode: For "install" — "ast" (esprima then local bundled Acorn) or
            "regex" (conservative whole-program subset; unsupported input skipped).
        tag: For "install"/"log" — group identifier.
        rewrite_member_access: For "install" — tap obj[key] reads.
        rewrite_calls: For "install" — tap fn(args) calls.
        tap_stage_calls: For "install" — rewrite the flattened-VMP dispatcher
            shape `return fn.apply(this, [<int>].concat(Array.prototype.slice
            .call(arguments)))` into per-state `tap_stage` events (state id `p`,
            argc, first args previews, return preview). This is a literal match,
            so it also works when the rest of the file is not parseable, and it
            is independent of `mode`/`filter_*`. Use it to compare a VM's state
            sequence between a real browser and an emulated environment.
        include_source_site: For "install" — attach a stable site_id and
            monotonic seq to tap events, plus an original-source range map in
            the log response. Default False.
        max_rewrites: For "install" — hard cap on rewrites per file.
        fallback_on_error: For "install" — try conservative regex on AST failure
            only without property/object filters; otherwise pass through unchanged.
        ignore_csp: For "install" — skip CSP pre-flight check.
        clear_log: For "reload" — clear JSVMP logs before reload.
        wait_until: For "reload" — "load" / "domcontentloaded" / "networkidle".
        tag_filter: For "log" — filter by tag.
        type_filter: For "log" — "tap_get", "tap_call", "tap_method",
            "tap_call_err", "tap_stage".
        key_filter: For "log" — substring match on property/method name.
        limit: For "log" — max entries to return.
        clear: For "log" — clear log after retrieval in the selected main world.
        frame_url: For log; select a target frame by URL pattern.
        frame_name: For log; select a target frame by name.
        frame_index: For log; current frame snapshot index, not a persistent identity.
            Source scripts run in the main world; logs are always read there.
        filter_property_names: For AST "install" — only rewrite reads/methods of these
            property names (e.g. ['userAgent', 'platform', 'webdriver']).
            Dramatically reduces overhead for large files like webmssdk.
        filter_object_names: For AST "install" — only rewrite when the static base
            object path matches (e.g. ['navigator', 'this.bytecode']). Dynamic
            object identity is not inferred. Regex mode rejects nonempty filters.
        max_file_size: For "install" — files larger than this (bytes) trigger
            on_oversized behavior. Default 200KB.
        on_oversized: For "install" — "selective" (require filters), "skip",
            or "force" (full rewrite anyway). Default "selective".

    Returns:
        dict with action-specific results.

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
        return await _get_log(tag_filter, type_filter, key_filter, limit, clear, frame_url, frame_name, frame_index)
    elif action == "stop":
        return await _stop(url_pattern or None)
    elif action == "reload":
        return await _reload_with_hooks(clear_log, wait_until)
    elif action == "status":
        return _get_status()
    else:
        return {"error": f"unknown action: {action}. Use install/log/stop/reload/status"}


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
                "cached_urls": len(info["cache"]),
                "include_source_site": info.get("include_source_site", False),
                "source_site_count": len(info.get("source_sites", {})),
            }
            for pat, info in _active_routes.items()
        ],
        "total_patterns": len(_active_routes),
    }


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

        cache: dict[str, dict] = {}
        source_sites: dict[str, dict] = {}
        stats = {"files_rewritten": 0, "total_edits": 0, "last_url": None,
                 "last_mode_used": None, "files_seen": 0, "files_passed_through": 0,
                 "cache_hits": 0, "last_parser_backend": None, "last_skip_reason": None, "last_error": None}

        # Build filter sets for selective instrumentation
        prop_filter_set = set(filter_property_names) if filter_property_names else None
        obj_filter_set = set(filter_object_names) if filter_object_names else None

        async def route_handler(route):
            resp = None
            body_bytes = None
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
                    stats["files_passed_through"] += 1
                    stats["last_skip_reason"] = "non_success_response"
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
                    await route.fulfill(status=resp.status, headers=headers, body=cached["body"])
                    return
                if len(body_bytes) > max_file_size:
                    skip = on_oversized == "skip" or (on_oversized == "selective" and not (prop_filter_set or obj_filter_set))
                    if skip:
                        stats["files_passed_through"] += 1
                        stats["last_mode_used"] = "passthrough"
                        stats["last_skip_reason"] = "oversized_without_selected_properties" if on_oversized == "selective" else "oversized"
                        await route.fulfill(status=resp.status, headers=headers, body=body_bytes)
                        return
                rewritten = None
                rewrite_stats = {}
                mode_used = mode
                if mode == "ast":
                    rewritten, rewrite_stats = _ast_rewrite_py(
                        src, tag=tag, rewrite_member_access=rewrite_member_access,
                        rewrite_calls=rewrite_calls, max_edits=max_rewrites,
                        filter_property_names=list(prop_filter_set) if prop_filter_set else None,
                        filter_object_names=list(obj_filter_set) if obj_filter_set else None,
                        include_source_site=include_source_site,
                        tap_stage_calls=tap_stage_calls,
                    )
                    if rewritten is None:
                        stats["last_error"] = rewrite_stats.get("error", "AST parse/rewrite failed")
                        if fallback_on_error and not (prop_filter_set or obj_filter_set):
                            mode_used = "regex (fallback)"
                            rewritten, rewrite_stats = regex_rewrite(src, tag=tag,
                                rewrite_member_access=rewrite_member_access,
                                max_rewrites=max_rewrites, include_source_site=include_source_site,
                                tap_stage_calls=tap_stage_calls)
                elif mode == "regex":
                    rewritten, rewrite_stats = regex_rewrite(src, tag=tag,
                        rewrite_member_access=rewrite_member_access,
                        max_rewrites=max_rewrites, include_source_site=include_source_site,
                        tap_stage_calls=tap_stage_calls)
                else:
                    raise ValueError("mode must be ast or regex")
                edit_count = rewrite_stats.get("edits")
                if edit_count is None:
                    edit_count = (rewrite_stats.get("member_access_rewrites", 0)
                                  + rewrite_stats.get("stage_rewrites", 0))
                stats["last_parser_backend"] = rewrite_stats.get("parser_backend")
                if rewritten is None or not edit_count:
                    stats["files_passed_through"] += 1
                    stats["last_mode_used"] = "passthrough"
                    stats["last_skip_reason"] = rewrite_stats.get("skipped_reason", "no_eligible_rewrites")
                    await route.fulfill(status=resp.status, headers=headers, body=body_bytes)
                    return
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
                await route.fulfill(status=resp.status, headers=headers, body=rewritten)
            except Exception as exc:
                stats["last_error"] = str(exc)
                # Never continue_ after route.fetch: that could execute a POST
                # twice. Preserve the original response or fail this request.
                try:
                    if resp is not None and body_bytes is not None:
                        stats["files_passed_through"] += 1
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
        }
        result = {
            "status": "instrumenting", "pattern": url_pattern,
            "mode": mode, "tag": tag,
            "route_level": "context",
            "selective": bool(prop_filter_set or obj_filter_set),
            "include_source_site": include_source_site,
            "tap_stage_calls": tap_stage_calls,
            "filter_property_names": filter_property_names,
            "filter_object_names": filter_object_names,
            "note": "Route active. Navigate or reload to trigger rewrite.",
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


async def _get_log(tag_filter, type_filter, key_filter, limit, clear, frame_url=None, frame_name=None, frame_index=None) -> dict:
    try:
        page = await browser_manager.get_active_page()
        if not 1 <= limit <= 20000:
            return {"error": "limit must be between 1 and 20000"}
        target, frame = (resolve_frame(page, frame_url=frame_url, frame_name=frame_name, frame_index=frame_index)
                         if any(value is not None for value in (frame_url, frame_name, frame_index)) else (page, None))
        data, backend, warning = await evaluate_in_world(target, "() => window.__mcp_vmp_log || []", "main")
        if not isinstance(data, list):
            return {"error": "Instrumentation log is not an array in the selected main world"}
        raw_count = len(data)
        if tag_filter:
            data = [d for d in data if d.get("tag") == tag_filter]
        if type_filter:
            data = [d for d in data if d.get("type") == type_filter]
        if key_filter:
            data = [d for d in data
                    if key_filter in (d.get("key") or "")
                    or key_filter in (d.get("method") or "")
                    or key_filter in (d.get("name") or "")
                    or key_filter in str(d.get("p") or "")]

        key_count: dict[str, int] = {}
        method_count: dict[str, int] = {}
        function_count: dict[str, int] = {}
        stage_count: dict[str, int] = {}
        site_count: dict[str, int] = {}
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
            elif e.get("type") in ("tap_call", "tap_call_err"):
                fn = e.get("name", "?")
                function_count[fn] = function_count.get(fn, 0) + 1
            site_id = e.get("site_id")
            if site_id:
                site_count[site_id] = site_count.get(site_id, 0) + 1

        entries = data[-limit:] if len(data) > limit else data
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
            await evaluate_in_world(target, "() => {window.__mcp_vmp_log = []; return true;}", "main")
        result = {
            "entries": entries,
            "world": "main", "frame": frame, "execution_backend": backend,
            "warning": warning, "raw_total_entries": raw_count,
            "possibly_capped": raw_count >= 20000,
            "capture_limit": 20000,
            "total_entries": len(data), "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "hot_keys": dict(sorted(key_count.items(), key=lambda x: -x[1])[:30]),
                "hot_methods": dict(sorted(method_count.items(), key=lambda x: -x[1])[:30]),
                "hot_functions": dict(sorted(function_count.items(), key=lambda x: -x[1])[:30]),
                **({"hot_stages": dict(sorted(stage_count.items(),
                                             key=lambda x: -int(x[1]))[:60])}
                   if stage_count else {}),
            },
        }
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
