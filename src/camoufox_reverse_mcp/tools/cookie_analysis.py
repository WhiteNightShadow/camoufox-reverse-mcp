"""
cookie_analysis.py - Attribute every cookie to its source.

Cookies can enter the jar via:
  1. HTTP response Set-Cookie header (server-side)
  2. JS document.cookie = "..." (client-side)
  3. navigator.cookieStore API (modern, rare)

This module correlates captured network responses with client-side
cookie writes to explain where each cookie came from. Critical for
understanding Rui Shu / Akamai cookie flows where the JS computes
a token but the HTTP layer is where the cookie actually gets set.
"""
from __future__ import annotations
import re

from ..server import mcp, browser_manager


_COOKIE_NAME_RE = re.compile(r'^\s*([^=;\s]+)\s*=')


def _parse_cookie_name(header_value: str) -> str | None:
    m = _COOKIE_NAME_RE.match(header_value or "")
    return m.group(1) if m else None


@mcp.tool()
async def analyze_cookie_sources(
    name_filter: str | None = None,
) -> dict:
    """Attribute every observed cookie to its source (HTTP header vs JS).

    Combines:
      - Captured network responses' Set-Cookie headers
        (requires start_network_capture was active)
      - document.cookie write log from cookie_hook
        (requires cookie_hook.js was injected)
      - Currently-present cookies via page.context.cookies()

    Args:
        name_filter: Only return cookies with this substring in name.

    Returns:
        dict mapping cookie_name -> {
            sources: ["http_set_cookie" | "js_document_cookie"],
            first_set_ts: ms timestamp of first observation,
            http_response_urls: [urls that sent Set-Cookie for this name],
            js_stacks: [JS stack traces that wrote this cookie],
            current_value: present value in the cookie jar,
        }
    """
    try:
        # 1. Collect HTTP-set cookies from captured responses
        http_sources: dict[str, list[dict]] = {}
        for req in browser_manager._network_requests:
            headers = req.get("response_headers") or {}
            sc = None
            for k, v in headers.items():
                if k.lower() == "set-cookie":
                    sc = v
                    break
            if not sc:
                continue
            lines = sc.split("\n") if "\n" in sc else [sc]
            for line in lines:
                name = _parse_cookie_name(line)
                if not name:
                    continue
                http_sources.setdefault(name, []).append({
                    "url": req.get("url"),
                    "ts": req.get("timestamp"),
                    "header": line.strip()[:300],
                })

        # 2. Collect JS-set cookies from cookie_hook log
        js_sources: dict[str, list[dict]] = {}
        try:
            page = await browser_manager.get_active_page()
            log = await page.evaluate("window.__mcp_cookie_log || []")
            for entry in log:
                if entry.get("op") != "set":
                    continue
                name = _parse_cookie_name(entry.get("value", ""))
                if not name:
                    continue
                js_sources.setdefault(name, []).append({
                    "value": (entry.get("value") or "")[:300],
                    "stack": (entry.get("stack") or "")[:800],
                    "ts": entry.get("ts"),
                })
        except Exception:
            pass

        # 3. Current cookie jar
        current: dict[str, str] = {}
        try:
            page = await browser_manager.get_active_page()
            ctx = page.context
            for c in await ctx.cookies():
                current[c["name"]] = c.get("value", "")
        except Exception:
            pass

        # 4. Merge
        all_names = set(http_sources) | set(js_sources) | set(current)
        if name_filter:
            all_names = {n for n in all_names if name_filter in n}

        result = {}
        for name in sorted(all_names):
            sources = []
            if name in http_sources:
                sources.append("http_set_cookie")
            if name in js_sources:
                sources.append("js_document_cookie")
            ts_candidates = []
            ts_candidates.extend(h.get("ts") for h in http_sources.get(name, []) if h.get("ts"))
            ts_candidates.extend(j.get("ts") for j in js_sources.get(name, []) if j.get("ts"))
            result[name] = {
                "sources": sources or ["unknown_or_preexisting"],
                "first_set_ts": min(ts_candidates) if ts_candidates else None,
                "http_responses": http_sources.get(name, []),
                "js_writes": js_sources.get(name, []),
                "current_value": (current.get(name) or "")[:300],
            }

        return {
            "cookies": result,
            "total_cookies": len(result),
            "hint": (
                "If a cookie appears only in 'http_set_cookie' (e.g. acw_tc, "
                "NfBCSins2Oyw), it was set by the server via a response header, "
                "typically AFTER the JS challenge computed some token. Look at "
                "the http_responses[].url to find the server endpoint that sets it, "
                "and work backwards from there."
            ),
        }
    except Exception as e:
        return {"error": str(e)}
