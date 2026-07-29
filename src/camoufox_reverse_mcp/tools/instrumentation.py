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

from ..server import mcp, browser_manager
from ..utils.js_rewriter import (
    regex_rewrite,
    INSTRUMENT_RUNTIME,
    ACORN_REWRITE_JS_TEMPLATE,
)
from ..utils.ast_rewriter import ast_rewrite as _ast_rewrite_py

# Module-level state for active instrumentation routes
_active_routes: dict[str, dict] = {}


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
        mode: For "install" — "ast" (default) or "regex".
        tag: For "install"/"log" — group identifier.
        rewrite_member_access: For "install" — tap obj[key] reads.
        rewrite_calls: For "install" — tap fn(args) calls.
        max_rewrites: For "install" — hard cap on rewrites per file.
        fallback_on_error: For "install" — auto-fallback to regex if AST fails.
        ignore_csp: For "install" — skip CSP pre-flight check.
        clear_log: For "reload" — clear JSVMP logs before reload.
        wait_until: For "reload" — "load" / "domcontentloaded" / "networkidle".
        tag_filter: For "log" — filter by tag.
        type_filter: For "log" — "tap_get", "tap_call", "tap_method", "tap_call_err".
        key_filter: For "log" — substring match on property/method name.
        limit: For "log" — max entries to return.
        clear: For "log" — clear log after retrieval.
        filter_property_names: For "install" — only rewrite access to these
            property names (e.g. ['userAgent', 'platform', 'webdriver']).
            Dramatically reduces overhead for large files like webmssdk.
        filter_object_names: For "install" — only rewrite when base object
            matches (e.g. ['navigator', 'screen', 'document']).
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
        return await _install(url_pattern, mode, tag, rewrite_member_access,
                              rewrite_calls, max_rewrites, fallback_on_error,
                              ignore_csp, filter_property_names,
                              filter_object_names, max_file_size, on_oversized)
    elif action == "log":
        return await _get_log(tag_filter, type_filter, key_filter, limit, clear)
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
                "cached_urls": len(info["cache"]),
            }
            for pat, info in _active_routes.items()
        ],
        "total_patterns": len(_active_routes),
    }


async def _install(url_pattern, mode, tag, rewrite_member_access,
                   rewrite_calls, max_rewrites, fallback_on_error, ignore_csp,
                   filter_property_names, filter_object_names,
                   max_file_size, on_oversized) -> dict:
    try:
        if not url_pattern:
            return {"error": "url_pattern is required for action='install'"}

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

        cache: dict[str, str] = {}
        stats = {"files_rewritten": 0, "total_edits": 0, "last_url": None,
                 "last_mode_used": None}

        # Build filter sets for selective instrumentation
        prop_filter_set = set(filter_property_names) if filter_property_names else None
        obj_filter_set = set(filter_object_names) if filter_object_names else None

        async def route_handler(route):
            try:
                req_url = route.request.url
                if req_url in cache:
                    await route.fulfill(
                        status=200,
                        headers={"content-type": "application/javascript; charset=utf-8"},
                        body=cache[req_url],
                    )
                    return
                resp = await route.fetch()
                body_bytes = await resp.body()
                try:
                    src = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    src = body_bytes.decode("latin-1")

                file_size = len(src.encode("utf-8"))
                rewritten = src
                edit_count = 0
                mode_used = mode

                # v1.0.1: large file handling
                if file_size > max_file_size:
                    if on_oversized == "skip":
                        await route.fulfill(
                            status=resp.status,
                            headers=_clean_response_headers(resp.headers),
                            body=src,
                        )
                        stats["last_url"] = req_url
                        stats["last_mode_used"] = "skipped (oversized)"
                        return
                    elif on_oversized == "selective":
                        if not prop_filter_set:
                            # Can't do selective without filters, pass through
                            await route.fulfill(
                                status=resp.status,
                                headers=_clean_response_headers(resp.headers),
                                body=src,
                            )
                            stats["last_url"] = req_url
                            stats["last_mode_used"] = "skipped (oversized, no filters)"
                            return
                        # Selective: use filtered AST rewrite
                        ast_out, ast_stats = _ast_rewrite_py(
                            src, tag=tag,
                            rewrite_member_access=rewrite_member_access,
                            rewrite_calls=rewrite_calls,
                            max_edits=max_rewrites,
                            filter_property_names=list(prop_filter_set) if prop_filter_set else None,
                            filter_object_names=list(obj_filter_set) if obj_filter_set else None,
                        )
                        if ast_out is not None:
                            rewritten = ast_out
                            edit_count = ast_stats.get("edits", 0)
                            mode_used = "ast (selective)"
                        elif fallback_on_error:
                            mode_used = "regex (selective fallback)"
                            rw, rstats = regex_rewrite(
                                src, tag=tag,
                                rewrite_member_access=rewrite_member_access,
                                max_rewrites=max_rewrites,
                            )
                            rewritten = rw
                            edit_count = rstats.get("member_access_rewrites", 0)
                    # else "force" — fall through to normal rewrite

                # Normal rewrite (small files or force mode)
                if rewritten is src:  # not yet rewritten
                    if mode == "ast":
                        ast_out, ast_stats = _ast_rewrite_py(
                            src, tag=tag,
                            rewrite_member_access=rewrite_member_access,
                            rewrite_calls=rewrite_calls,
                            max_edits=max_rewrites,
                            filter_property_names=list(prop_filter_set) if prop_filter_set else None,
                            filter_object_names=list(obj_filter_set) if obj_filter_set else None,
                        )
                        if ast_out is not None:
                            rewritten = ast_out
                            edit_count = ast_stats.get("edits", 0)
                        elif fallback_on_error:
                            mode_used = "regex (fallback)"
                            rw, rstats = regex_rewrite(
                                src, tag=tag,
                                rewrite_member_access=rewrite_member_access,
                                max_rewrites=max_rewrites,
                            )
                            rewritten = rw
                            edit_count = rstats.get("member_access_rewrites", 0)
                    elif mode == "regex":
                        rw, rstats = regex_rewrite(
                            src, tag=tag,
                            rewrite_member_access=rewrite_member_access,
                            max_rewrites=max_rewrites,
                        )
                        rewritten = rw
                        edit_count = rstats.get("member_access_rewrites", 0)

                cache[req_url] = rewritten
                stats["files_rewritten"] += 1
                stats["total_edits"] += edit_count
                stats["last_url"] = req_url
                stats["last_mode_used"] = mode_used

                headers = _clean_response_headers(resp.headers)
                headers["content-type"] = "application/javascript; charset=utf-8"
                await route.fulfill(status=resp.status, headers=headers, body=rewritten)
            except Exception as e:
                try:
                    await route.continue_()
                except Exception:
                    pass

        # v1.0.1: context-level route (catches requests from page load)
        await ctx.route(url_pattern, route_handler)
        _active_routes[url_pattern] = {
            "handler": route_handler, "cache": cache, "stats": stats,
            "mode": mode, "tag": tag, "context": ctx,
        }
        result = {
            "status": "instrumenting", "pattern": url_pattern,
            "mode": mode, "tag": tag,
            "route_level": "context",
            "selective": bool(prop_filter_set or obj_filter_set),
            "filter_property_names": filter_property_names,
            "filter_object_names": filter_object_names,
            "note": "Route active. Navigate or reload to trigger rewrite.",
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


async def _get_log(tag_filter, type_filter, key_filter, limit, clear) -> dict:
    try:
        page = await browser_manager.get_active_page()
        data = await page.evaluate("window.__mcp_vmp_log || []")
        if tag_filter:
            data = [d for d in data if d.get("tag") == tag_filter]
        if type_filter:
            data = [d for d in data if d.get("type") == type_filter]
        if key_filter:
            data = [d for d in data
                    if key_filter in (d.get("key") or "")
                    or key_filter in (d.get("method") or "")
                    or key_filter in (d.get("name") or "")]

        key_count: dict[str, int] = {}
        method_count: dict[str, int] = {}
        for e in data:
            if e.get("type") == "tap_get":
                k = e.get("key", "?")
                key_count[k] = key_count.get(k, 0) + 1
            elif e.get("type") == "tap_method":
                m = f"{e.get('objType', '?')}.{e.get('method', '?')}"
                method_count[m] = method_count.get(m, 0) + 1

        if clear:
            await page.evaluate("window.__mcp_vmp_log = []")
        return {
            "entries": data[-limit:] if len(data) > limit else data,
            "total_entries": len(data), "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "hot_keys": dict(sorted(key_count.items(), key=lambda x: -x[1])[:30]),
                "hot_methods": dict(sorted(method_count.items(), key=lambda x: -x[1])[:30]),
            },
        }
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
