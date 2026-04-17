"""
instrumentation.py - Source-level JSVMP instrumentation tools.

Hooks into the HTTP response for target JS files, rewrites the source to
insert taps at every member access and function call, then continues the
response. The rewritten code still executes normally but records every
bytecode-dispatch-loop interaction with the host environment.

This is the most powerful VMP analysis technique available because it
doesn't rely on the VMP routing through hookable JS APIs - it instruments
the bytecode dispatcher itself.
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


async def _detect_csp_risk(page) -> dict:
    """Detect if the page's CSP would block injected inline runtime."""
    try:
        probe = await page.evaluate(r"""
          (async () => {
            return new Promise(resolve => {
              var marker = '__mcp_csp_probe_' + Math.random().toString(36).slice(2);
              var s = document.createElement('script');
              s.textContent = 'window["' + marker + '"] = 1;';
              var violated = false;
              var handler = function(e) {
                if (e.violatedDirective && e.violatedDirective.indexOf('script-src') !== -1) {
                  violated = true;
                }
              };
              document.addEventListener('securitypolicyviolation', handler, { once: true });
              try { document.head.appendChild(s); } catch (e) {}
              try { document.head.removeChild(s); } catch (e) {}
              setTimeout(() => {
                document.removeEventListener('securitypolicyviolation', handler);
                var ran = window[marker] === 1;
                try { delete window[marker]; } catch (e) {}
                var meta = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
                resolve({
                  ran: ran,
                  violated: violated,
                  csp_meta: meta ? meta.content : null
                });
              }, 80);
            });
          })()
        """)
    except Exception:
        return {"blocks": False, "csp_meta": None, "reasons": []}

    reasons: list[str] = []
    blocks = bool(probe.get("violated") or not probe.get("ran"))
    if blocks:
        reasons.append("inline <script> execution blocked or violated CSP")
    csp_meta = probe.get("csp_meta")
    if csp_meta and "unsafe-inline" not in csp_meta:
        reasons.append(f"CSP meta missing 'unsafe-inline': {str(csp_meta)[:200]}")

    return {"blocks": blocks, "csp_meta": csp_meta, "reasons": reasons}


@mcp.tool()
async def instrument_jsvmp_source(
    url_pattern: str,
    mode: str = "ast",
    tag: str = "vmp",
    rewrite_member_access: bool = True,
    rewrite_calls: bool = True,
    max_rewrites: int = 20000,
    cache_rewritten: bool = True,
    fallback_on_error: bool = True,
    ignore_csp: bool = False,
) -> dict:
    """Intercept a JSVMP script and instrument its source at the HTTP layer.

    ★ RECOMMENDED DEFAULT for signature-based anti-bot systems. Unlike
    runtime hooks, this tool rewrites the JS source before the browser
    executes it. The environment stays pristine — the VMP reads the real
    navigator, computes the real signature, and the server accepts it.
    Meanwhile every internal operation of the VMP is logged through the
    injected taps.

    This is the only observation technique compatible with cookie/signature
    schemes that hash environment properties.

    v0.6.0: Pre-flight CSP check — if the page's CSP blocks inline script
    execution, refuses to register (because the injected tap runtime would
    be silently dropped) and recommends alternatives.

    Args:
        url_pattern: Glob pattern matching the VMP script URL(s), e.g.:
            "**/vmp_target.es5.js"
            "**/challenge_*.js"
            "https://target-site.example.com/vmp_target*.js"
        mode: One of:
            - "ast"       — (default) AST rewrite via MCP-side esprima.
                            Zero page-side dependencies, works on 412 pages
                            that block external CDNs.
            - "regex"     — Faster, no AST parse; ~80% coverage, good for
                            minified code. Used as automatic fallback when
                            AST fails and fallback_on_error=True.
            - "ast_page"  — DEPRECATED. v0.4.x behavior: loads Acorn from
                            a CDN into the target page. Breaks on challenge
                            pages that block CDNs. Kept only for A/B
                            comparison.
        tag: String tag attached to every log entry.
        rewrite_member_access: Tap every obj[key] / obj.key read.
        rewrite_calls: Tap every fn(args) / obj.method(args) call.
        max_rewrites: Hard cap on rewrites per file (safety).
        cache_rewritten: Cache the rewritten source.
        fallback_on_error: If mode="ast" and AST parse fails, automatically
            retry with regex mode instead of shipping unmodified source
            (default True).
        ignore_csp: If True, skip the CSP pre-flight check. Use only when
            you've verified the rewrite works on this site (rare).

    Returns:
        dict with status and pattern. The actual rewrite happens when the
        browser loads the matched URL. Watch console for "[INSTRUMENT]" logs.
    """
    try:
        page = await browser_manager.get_active_page()

        if not ignore_csp:
            csp = await _detect_csp_risk(page)
            if csp["blocks"]:
                return {
                    "status": "refused_csp_blocks_inline",
                    "csp_meta": csp.get("csp_meta"),
                    "reasons": csp["reasons"],
                    "recommended": (
                        "This page's CSP blocks the injected tap runtime. "
                        "Source rewrite would succeed but log output would be empty. "
                        "Alternatives:\n"
                        "  1) hook_jsvmp_interpreter(mode='transparent') — patches "
                        "     prototype getters in-memory, no inline script needed\n"
                        "  2) hook_function(function_path=...) — patches specific "
                        "     functions in-memory\n"
                        "  3) pass ignore_csp=True to skip this check"
                    ),
                }

        cache: dict[str, str] = {}
        stats = {"files_rewritten": 0, "total_edits": 0, "last_url": None,
                 "last_mode_used": None}

        async def route_handler(route):
            try:
                req_url = route.request.url
                if cache_rewritten and req_url in cache:
                    await route.fulfill(
                        status=200,
                        headers={"content-type": "application/javascript; charset=utf-8"},
                        body=cache[req_url],
                    )
                    return

                resp = await route.fetch()
                resp_status = resp.status
                body_bytes = await resp.body()
                try:
                    src = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    src = body_bytes.decode("latin-1")

                rewritten = src
                edit_count = 0
                mode_used = mode

                if mode == "ast":
                    # MCP-side esprima
                    ast_out, ast_stats = _ast_rewrite_py(
                        src, tag=tag,
                        rewrite_member_access=rewrite_member_access,
                        rewrite_calls=rewrite_calls,
                        max_edits=max_rewrites,
                    )
                    if ast_out is not None:
                        rewritten = ast_out
                        edit_count = ast_stats.get("edits", 0)
                    elif fallback_on_error:
                        browser_manager._console_logs.append({
                            "level": "warn",
                            "text": f"[INSTRUMENT] AST parse failed for {req_url}: "
                                    f"{ast_stats.get('error')}. Falling back to regex.",
                            "timestamp": int(time.time() * 1000),
                            "location": None,
                        })
                        mode_used = "regex (fallback)"
                        rw, rstats = regex_rewrite(
                            src, tag=tag,
                            rewrite_member_access=rewrite_member_access,
                            max_rewrites=max_rewrites,
                        )
                        rewritten = rw
                        edit_count = rstats.get("member_access_rewrites", 0)
                    else:
                        rewritten = src

                elif mode == "regex":
                    rw, rstats = regex_rewrite(
                        src, tag=tag,
                        rewrite_member_access=rewrite_member_access,
                        max_rewrites=max_rewrites,
                    )
                    rewritten = rw
                    edit_count = rstats.get("member_access_rewrites", 0)

                elif mode == "ast_page":
                    # DEPRECATED: v0.4.x behavior, kept for A/B comparison
                    opts = {
                        "rewriteMemberAccess": rewrite_member_access,
                        "rewriteCalls": rewrite_calls,
                    }
                    try:
                        result = await page.evaluate(
                            ACORN_REWRITE_JS_TEMPLATE,
                            [src, tag, opts]
                        )
                        if result.get("ok"):
                            rewritten = INSTRUMENT_RUNTIME + "\n" + result["src"]
                            edit_count = result.get("edit_count", 0)
                        else:
                            rewritten = src
                    except Exception as e:
                        rewritten = src
                        browser_manager._console_logs.append({
                            "level": "error",
                            "text": f"[INSTRUMENT] ast_page threw for {req_url}: {e}",
                            "timestamp": int(time.time() * 1000),
                            "location": None,
                        })
                else:
                    rewritten = src

                if cache_rewritten:
                    cache[req_url] = rewritten

                stats["files_rewritten"] += 1
                stats["total_edits"] += edit_count
                stats["last_url"] = req_url
                stats["last_mode_used"] = mode_used

                headers = dict(resp.headers)
                headers.pop("content-length", None)
                headers.pop("Content-Length", None)
                headers["content-type"] = "application/javascript; charset=utf-8"

                await route.fulfill(
                    status=resp_status,
                    headers=headers,
                    body=rewritten,
                )
                browser_manager._console_logs.append({
                    "level": "info",
                    "text": f"[INSTRUMENT] rewrote {req_url} "
                            f"({edit_count} edits, mode={mode_used})",
                    "timestamp": int(time.time() * 1000),
                    "location": None,
                })
            except Exception as e:
                browser_manager._console_logs.append({
                    "level": "error",
                    "text": f"[INSTRUMENT] handler error: {e}",
                    "timestamp": int(time.time() * 1000),
                    "location": None,
                })
                try:
                    await route.continue_()
                except Exception:
                    pass

        await page.route(url_pattern, route_handler)
        _active_routes[url_pattern] = {
            "handler": route_handler,
            "cache": cache,
            "stats": stats,
            "mode": mode,
            "tag": tag,
        }

        return {
            "status": "instrumenting",
            "pattern": url_pattern,
            "mode": mode,
            "tag": tag,
            "note": "Navigate or reload to trigger rewrite. "
                    "Check get_instrumentation_status() for stats.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_instrumentation_status() -> dict:
    """Show currently active source-level instrumentations and their stats."""
    try:
        return {
            "active_patterns": [
                {
                    "pattern": pat,
                    "mode": info["mode"],
                    "tag": info["tag"],
                    "files_rewritten": info["stats"]["files_rewritten"],
                    "total_edits": info["stats"]["total_edits"],
                    "last_url": info["stats"]["last_url"],
                    "last_mode_used": info["stats"].get("last_mode_used"),
                    "cached_urls": len(info["cache"]),
                }
                for pat, info in _active_routes.items()
            ],
            "total_patterns": len(_active_routes),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def stop_instrumentation(url_pattern: str | None = None) -> dict:
    """Stop a specific or all source-level instrumentation routes.

    Args:
        url_pattern: The exact pattern passed to instrument_jsvmp_source.
            If omitted, stops all.
    """
    try:
        page = await browser_manager.get_active_page()
        removed = []
        if url_pattern is not None:
            if url_pattern in _active_routes:
                try:
                    await page.unroute(url_pattern)
                except Exception:
                    pass
                del _active_routes[url_pattern]
                removed.append(url_pattern)
        else:
            for pat in list(_active_routes.keys()):
                try:
                    await page.unroute(pat)
                except Exception:
                    pass
                del _active_routes[pat]
                removed.append(pat)
        return {"status": "stopped", "removed": removed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_instrumentation_log(
    tag_filter: str | None = None,
    type_filter: str | None = None,
    key_filter: str | None = None,
    limit: int = 500,
    clear: bool = False,
) -> dict:
    """Retrieve logs emitted by instrumented VMP source.

    Args:
        tag_filter: Filter by instrumentation tag (the tag= argument you
            passed to instrument_jsvmp_source).
        type_filter: "tap_get", "tap_call", "tap_method", or "tap_call_err".
        key_filter: Substring match on accessed property/method name.
        limit: Max entries to return.
        clear: If True, clear the log after retrieval.

    Returns:
        dict with entries, summary (hottest keys / methods), and counts.
    """
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
        fn_count: dict[str, int] = {}
        for e in data:
            if e.get("type") == "tap_get":
                k = e.get("key", "?")
                key_count[k] = key_count.get(k, 0) + 1
            elif e.get("type") == "tap_method":
                m = f"{e.get('objType', '?')}.{e.get('method', '?')}"
                method_count[m] = method_count.get(m, 0) + 1
            elif e.get("type") == "tap_call":
                n = e.get("name", "anon")
                fn_count[n] = fn_count.get(n, 0) + 1

        if clear:
            await page.evaluate("window.__mcp_vmp_log = []")

        return {
            "entries": data[-limit:] if len(data) > limit else data,
            "total_entries": len(data),
            "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "hot_keys": dict(sorted(key_count.items(), key=lambda x: -x[1])[:30]),
                "hot_methods": dict(sorted(method_count.items(), key=lambda x: -x[1])[:30]),
                "hot_functions": dict(sorted(fn_count.items(), key=lambda x: -x[1])[:30]),
            },
        }
    except Exception as e:
        return {"error": str(e)}
