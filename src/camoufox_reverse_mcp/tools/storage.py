from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager


@mcp.tool()
async def get_cookies(domain: str | None = None) -> list[dict]:
    """Get all cookies from the current browser context.

    Args:
        domain: Optional domain filter (e.g. ".example.com").

    Returns:
        List of cookie dicts with name, value, domain, path, expires, etc.
    """
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context
        cookies = await ctx.cookies()
        if domain:
            cookies = [c for c in cookies if domain in c.get("domain", "")]
        return cookies
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def set_cookies(cookies: list[dict]) -> dict:
    """Add cookies to the current browser context.

    Args:
        cookies: List of cookie dicts. Each should contain at minimum:
            - name: Cookie name
            - value: Cookie value
            - domain: Cookie domain (e.g. ".example.com")
            Optional: path, expires, httpOnly, secure, sameSite.

    Returns:
        dict with status and count of cookies set.
    """
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context
        await ctx.add_cookies(cookies)
        return {"status": "set", "count": len(cookies)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def delete_cookies(name: str | None = None, domain: str | None = None) -> dict:
    """Delete cookies by name and/or domain.

    Args:
        name: Specific cookie name to delete.
        domain: Delete all cookies for this domain.

    Returns:
        dict with status and count of cookies deleted.
    """
    try:
        page = await browser_manager.get_active_page()
        ctx = page.context
        cookies = await ctx.cookies()

        to_keep = []
        deleted = 0
        for c in cookies:
            should_delete = False
            if name and c["name"] == name:
                should_delete = True
            if domain and domain in c.get("domain", ""):
                should_delete = True
            if not name and not domain:
                should_delete = True
            if should_delete:
                deleted += 1
            else:
                to_keep.append(c)

        await ctx.clear_cookies()
        if to_keep:
            await ctx.add_cookies(to_keep)
        return {"status": "deleted", "count": deleted}
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
async def set_storage(storage_type: str, key: str, value: str) -> dict:
    """Set a value in localStorage or sessionStorage.

    Args:
        storage_type: "local" or "session".
        key: Storage key.
        value: Storage value.

    Returns:
        dict with status, storage type, and the key set.
    """
    try:
        page = await browser_manager.get_active_page()
        if storage_type == "local":
            await page.evaluate(f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
        elif storage_type == "session":
            await page.evaluate(f"sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
        else:
            return {"error": f"Invalid storage_type: {storage_type}"}
        return {"status": "set", "storage_type": storage_type, "key": key}
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

    Creates a new browser context with the saved state (cookies, localStorage,
    sessionStorage) and switches to it.

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
        page = await ctx.new_page()
        browser_manager._attach_listeners(page)
        browser_manager.pages[ctx_name] = page
        browser_manager.active_page_name = ctx_name
        return {"status": "imported", "context": ctx_name, "path": state_path}
    except Exception as e:
        return {"error": str(e)}
