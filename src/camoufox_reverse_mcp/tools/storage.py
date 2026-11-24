from __future__ import annotations

import asyncio
import inspect
import os
import re
import time

from ..server import mcp, browser_manager
from ..utils.evidence import resolve_output_path, write_text_evidence
from ..utils.domains import domain_matches


@mcp.tool()
async def cookies(
    action: str,
    domain: str | None = None,
    cookies_list: list[dict] | None = None,
    name: str | None = None,
    url: str | None = None,
    output_file: str | None = None,
    overwrite: bool = False,
) -> dict | list:
    """Cookie management (v0.9.0 unified).

    Replaces get_cookies / set_cookies / delete_cookies.

    Args:
        action:
          "get"    — return cookies (optionally filtered by url, domain, and/or name)
          "header" — build a ready-to-use ``Cookie:`` header value (deduplicated by name)
          "set"    — set cookies (requires cookies_list: [{name, value, domain, ...}])
          "delete" — delete cookies (filter by name and/or domain; no filter = clear all)
        domain: Host or parent domain for get/header/delete (boundary match, includes subdomains).
            With name, both filters must match. No filters on delete clears all cookies.
        cookies_list: List of cookie dicts for "set".
        name: Optional exact cookie name filter for get/header/delete.
        url: Optional request URL for get/header so Playwright applies RFC 6265
            domain, path, Secure, and expiry matching. When action="header" is called
            without url or domain on an http(s) page, defaults to the active page URL.
        output_file: For action="header", write the Cookie header to this file (mode 0600)
            and omit the raw header string from the inline tool response.
        overwrite: Allow replacing an existing output_file.

    Returns:
        For "get": list of cookie dicts.
        For "header": {"action", "count", "names", "header", "domain", "url"}
            plus "output": {path, bytes, sha256, ...} (with "header": None) when output_file is used.
        For "set"/"delete": dict with status and count.
    """
    try:
        if output_file is not None:
            if action != "header":
                return {"error": "output_file is only supported for action='header'"}
            resolve_output_path(output_file, overwrite=overwrite, create_parents=False)

        page = await browser_manager.get_active_page()
        ctx = page.context

        if action == "get":
            all_cookies = await (ctx.cookies([url]) if url else ctx.cookies())
            if domain:
                all_cookies = [c for c in all_cookies if domain_matches(c.get("domain", ""), domain)]
            if name:
                all_cookies = [c for c in all_cookies if c.get("name") == name]
            return all_cookies

        elif action == "header":
            effective_url = url
            if not effective_url and not domain:
                page_url = getattr(page, "url", "") or ""
                if page_url.startswith(("http://", "https://")):
                    effective_url = page_url
            all_cookies = await (ctx.cookies([effective_url]) if effective_url else ctx.cookies())
            if domain:
                now = time.time()
                all_cookies = [
                    c for c in all_cookies
                    if domain_matches(c.get("domain", ""), domain)
                    and not (isinstance(c.get("expires"), (int, float)) and 0 < c["expires"] < now)
                ]
                all_cookies.sort(
                    key=lambda c: (
                        -len(str(c.get("path") or "/")),
                        -len(str(c.get("domain") or "").lstrip(".")),
                    )
                )
            if name:
                all_cookies = [c for c in all_cookies if c.get("name") == name]
            header, used = build_cookie_header(all_cookies)
            result: dict = {
                "action": "header",
                "count": len(used),
                "names": [c.get("name") for c in used],
                "header": None if output_file is not None else header,
                "domain": domain,
                "url": effective_url,
            }
            if output_file is not None:
                result["output"] = await asyncio.to_thread(
                    write_text_evidence,
                    output_file,
                    header,
                    overwrite=overwrite,
                    mode=0o600,
                )
            return result

        elif action == "set":
            if not cookies_list:
                return {"error": "cookies_list is required for action='set'"}
            await ctx.add_cookies(cookies_list)
            return {"status": "set", "count": len(cookies_list)}

        elif action == "delete":
            all_cookies = await ctx.cookies()
            selected = [c for c in all_cookies
                        if (not name or c["name"] == name)
                        and (not domain or domain_matches(c.get("domain", ""), domain))]
            if not name and not domain:
                await ctx.clear_cookies()
            else:
                # Filtering was added in Playwright 1.43. Older installations
                # expire only the selected cookies, never clear/rebuild the jar.
                params = inspect.signature(ctx.clear_cookies).parameters
                supports_filters = all(key in params for key in ("name", "domain", "path")) or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
                for c in selected:
                    if supports_filters:
                        await ctx.clear_cookies(
                            name=re.compile("^" + re.escape(c["name"]) + "$"),
                            domain=re.compile("^" + re.escape(c["domain"]) + "$"),
                            path=re.compile("^" + re.escape(c.get("path", "/")) + "$"),
                        )
                    else:
                        await ctx.add_cookies([{**c, "expires": 1}])

            return {"status": "deleted", "count": len(selected)}

        else:
            return {"error": f"unknown action: {action}. Use get/header/set/delete"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_storage(storage_type: str = "local") -> dict:
    """Get the contents of localStorage or sessionStorage.

    Args:
        storage_type: "local" for localStorage, "session" for sessionStorage.

    Returns:
        dict with all key-value pairs in the storage.
    """
    try:
        page = await browser_manager.get_active_page()
        if storage_type == "local":
            data = await page.evaluate("""() => {
                const obj = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    obj[key] = localStorage.getItem(key);
                }
                return obj;
            }""")
        elif storage_type == "session":
            data = await page.evaluate("""() => {
                const obj = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    obj[key] = sessionStorage.getItem(key);
                }
                return obj;
            }""")
        else:
            return {"error": f"Invalid storage_type: {storage_type}. Use 'local' or 'session'."}
        return {"storage_type": storage_type, "data": data, "count": len(data)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def export_state(save_path: str) -> dict:
    """Export the complete browser state (cookies + storage) to a JSON file.

    Args:
        save_path: Local file path to save the state JSON.

    Returns:
        dict with status and the save path.
    """
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        await ctx.storage_state(path=save_path)
        return {"status": "exported", "path": save_path}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def import_state(state_path: str) -> dict:
    """Import browser state from a JSON file by creating a new context.

    Args:
        state_path: Path to the state JSON file (exported by export_state).

    Returns:
        dict with status and the new context name.
    """
    try:
        await browser_manager._ensure_browser()
        ctx = await browser_manager.browser.new_context(storage_state=state_path)
        ctx_name = f"imported_{len(browser_manager.contexts)}"
        browser_manager.contexts[ctx_name] = ctx
        for script in browser_manager._persistent_scripts:
            await ctx.add_init_script(script=script["content"])
        page = await ctx.new_page()
        browser_manager._attach_listeners(page)
        browser_manager.pages[ctx_name] = page
        browser_manager.active_page_name = ctx_name
        return {"status": "imported", "context": ctx_name, "path": state_path}
    except Exception as e:
        return {"error": str(e)}


def build_cookie_header(cookies: list[dict]) -> tuple[str, list[dict]]:
    """Join cookies into one ``Cookie:`` header value safely.

    Duplicates (same name, different domain/path) keep the first occurrence, so
    deterministic ordering is preserved; entries with control characters or
    invalid names are skipped to prevent HTTP header injection.
    """
    ctl_re = re.compile(r"[\x00-\x1f\x7f]")
    seen: set[str] = set()
    used: list[dict] = []
    pairs: list[str] = []
    for c in cookies:
        raw_name = c.get("name")
        if not isinstance(raw_name, str) or not raw_name or raw_name in seen:
            continue
        raw_val = "" if c.get("value") is None else str(c.get("value"))
        if ctl_re.search(raw_name) or ";" in raw_name or "=" in raw_name or ctl_re.search(raw_val):
            continue
        seen.add(raw_name)
        used.append(c)
        pairs.append(f"{raw_name}={raw_val}")
    return "; ".join(pairs), used
