from __future__ import annotations

import re
import inspect
import os

from ..server import mcp, browser_manager
from ..utils.evidence import preview, write_bytes_evidence, write_text_evidence
from ..utils.domains import domain_matches


@mcp.tool()
async def cookies(
    action: str,
    domain: str | None = None,
    cookies_list: list[dict] | None = None,
    name: str | None = None,
    output_file: str | None = None,
    overwrite: bool = False,
) -> dict | list:
    """Cookie management (v0.9.0 unified).

    Replaces get_cookies / set_cookies / delete_cookies.

    Args:
        action:
          "get"   — return cookies (optionally filtered by domain)
          "set"   — set cookies (requires cookies_list: [{name, value, domain, ...}])
          "delete" — delete cookies (filter by name and/or domain; no filter = clear all)
        domain: Host or parent domain for get/delete (boundary match, includes subdomains).
            With name, both filters must match. No filters deletes all cookies.
        cookies_list: List of cookie dicts for "set".
        name: Cookie name filter for "delete".

    Returns:
        For "get": list of cookie dicts.
        For "header": {"action", "count", "names", "header", "domain"}
            plus file metadata when output_file is used.
        For "set"/"delete": dict with status and count.

    Notes:
        action="header" returns a ready-to-use Cookie header value and
        deduplicates by name; pass output_file to persist it instead of
        copying a multi-kilobyte header through the conversation.
    """
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context

        if action == "get":
            all_cookies = await ctx.cookies()
            if domain:
                all_cookies = [c for c in all_cookies if domain_matches(c.get("domain", ""), domain)]
            return all_cookies

        elif action == "header":
            all_cookies = await ctx.cookies()
            if domain:
                all_cookies = [c for c in all_cookies if domain_matches(c.get("domain", ""), domain)]
            header, used = build_cookie_header(all_cookies)
            result = {
                "action": "header",
                "count": len(used),
                "names": [c.get("name") for c in used],
                "header": header,
                "domain": domain,
            }
            if output_file:
                result.update(write_text_evidence(output_file, header, overwrite=overwrite))
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
            return {"error": f"unknown action: {action}. Use get/set/delete"}
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
    """Join cookies into one ``Cookie:`` header value.

    Duplicates (same name, different domain/path) keep the first occurrence, so
    deterministic ordering is preserved; the host's own jar order still decides
    which value wins for the request that matters.
    """
    seen: set[str] = set()
    used: list[dict] = []
    for c in cookies:
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        used.append(c)
    header = "; ".join(f"{c['name']}={c.get('value', '')}" for c in used)
    return header, used
