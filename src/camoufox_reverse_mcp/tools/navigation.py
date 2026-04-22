from __future__ import annotations

import asyncio
import base64
import json as _json
import os

from ..server import mcp, browser_manager

_PRE_INJECT_REGISTER_TIMEOUT = 10.0


@mcp.tool()
async def launch_browser(
    headless: bool = False,
    os_type: str = "auto",
    locale: str = "auto",
    proxy: str | None = None,
    humanize: bool = False,
    geoip: bool = False,
    block_images: bool = False,
    block_webrtc: bool = False,
    enable_trace: bool = False,
) -> dict:
    """Launch the Camoufox anti-detection browser.

    Args:
        headless: Run in headless mode (default False).
        os_type: OS fingerprint - "auto", "windows", "macos", or "linux".
        locale: Browser locale (e.g. "zh-CN"). "auto" detects system locale.
        proxy: Proxy server URL (e.g. "http://127.0.0.1:7890").
        humanize: Enable humanized mouse movement.
        geoip: Auto-infer geolocation from proxy IP.
        block_images: Block image loading.
        block_webrtc: Block WebRTC to prevent IP leaks.
        enable_trace: Enable engine-level property access tracing.
            Requires camoufox-reverse custom browser build.
            When enabled, use trace_property_access() to capture DOM access.

    Returns:
        dict with status, config, and page list.
    """
    try:
        config = {
            "headless": headless, "os": os_type, "locale": locale,
            "humanize": humanize, "geoip": geoip,
            "block_images": block_images, "block_webrtc": block_webrtc,
            "enable_trace": enable_trace,
        }
        if proxy:
            config["proxy"] = {"server": proxy}
        result = await browser_manager.launch(config)

        if result.get("status") == "already_running":
            result["persistent_scripts_count"] = len(browser_manager._persistent_scripts)
            result["active_captures"] = browser_manager._capturing
            result["captured_requests_count"] = len(browser_manager._network_requests)
            from .instrumentation import _active_routes
            result["active_routes"] = len(_active_routes)
            has_residuals = (
                len(browser_manager._persistent_scripts) > 0
                or len(browser_manager._network_requests) > 0
                or len(_active_routes) > 0
            )
            if has_residuals:
                result.setdefault("warnings", []).append(
                    "browser already running with residual state. "
                    "Call reset_browser_state() or close_browser() + launch_browser()."
                )

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def close_browser() -> dict:
    """Close the Camoufox browser and release all resources."""
    try:
        return await browser_manager.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def navigate(
    url: str,
    wait_until: str = "load",
    pre_inject_hooks: list[str] | None = None,
    collect_response_chain: bool = True,
    clear_network_capture: bool = True,
) -> dict:
    """Navigate to a URL, with optional hook pre-injection and redirect tracing.

    Args:
        url: Target URL.
        wait_until: "load", "domcontentloaded", or "networkidle".
        pre_inject_hooks: Hook preset names to register before navigation.
        collect_response_chain: Record responses for final_status resolution.
        clear_network_capture: Clear stale network buffer before navigating.

    Returns:
        dict with url, title, initial_status, final_status, redirect_chain,
        hooks_injected, reloaded, warnings.
    """
    try:
        page = await browser_manager.get_active_page()
        warnings: list[str] = []
        hooks_injected: list[str] = []

        if clear_network_capture:
            try:
                cleared_count = len(browser_manager._network_requests)
                if cleared_count > 0:
                    browser_manager._network_requests.clear()
                    browser_manager._request_id_counter = 0
                    warnings.append(f"cleared {cleared_count} stale network requests")
            except Exception:
                pass

        if collect_response_chain:
            browser_manager.reset_nav_responses()

        if pre_inject_hooks:
            for name in pre_inject_hooks:
                ok, msg = await _inject_hook_by_name(name)
                if ok:
                    hooks_injected.append(name)
                else:
                    warnings.append(f"hook '{name}' failed: {msg}")

        navigation_timed_out = False
        try:
            resp = await page.goto(url, wait_until=wait_until, timeout=30000)
        except Exception as e:
            msg = str(e).lower()
            if "timeout" in msg or "exceeded" in msg or "waiting" in msg:
                warnings.append(f"goto timeout for '{wait_until}'; checking usability")
                try:
                    dom_ready = await page.evaluate("document.readyState")
                    current_url = page.url
                    if dom_ready in ("interactive", "complete") and current_url != "about:blank":
                        warnings.append(f"page usable (readyState={dom_ready})")
                        resp = None
                        navigation_timed_out = True
                    else:
                        raise
                except Exception:
                    raise
            else:
                raise
        initial_status = resp.status if resp else None

        reloaded = False
        if hooks_injected:
            try:
                if collect_response_chain:
                    browser_manager.reset_nav_responses()
                resp2 = await page.reload(wait_until=wait_until)
                reloaded = True
                if resp2:
                    initial_status = resp2.status
            except Exception as e:
                warnings.append(f"auto-reload failed: {e}")

        final_status = None
        chain = []
        if collect_response_chain:
            chain = list(browser_manager._nav_responses)
            for r in reversed(chain):
                if r["url"] == page.url or r.get("resource_type") == "document":
                    final_status = r["status"]
                    break

        return {
            "url": page.url, "title": await page.title(),
            "initial_status": initial_status,
            "final_status": final_status if final_status is not None else initial_status,
            "redirect_chain": chain if collect_response_chain else None,
            "hooks_injected": hooks_injected, "reloaded": reloaded,
            "navigation_timed_out": navigation_timed_out,
            "warnings": warnings if warnings else None,
        }
    except Exception as e:
        return {"error": str(e)}


async def _inject_hook_by_name(name: str) -> tuple[bool, str]:
    """Register a hook as a persistent context-level script."""
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
    preset_files = {
        "xhr": "xhr_hook.js", "fetch": "fetch_hook.js",
        "crypto": "crypto_hook.js", "websocket": "websocket_hook.js",
        "debugger_bypass": "debugger_trap.js",
        "cookie_hook": "cookie_hook.js", "runtime_probe": "runtime_probe.js",
    }
    try:
        if name == "jsvmp_probe":
            with open(os.path.join(hooks_dir, "jsvmp_hook.js"), "r", encoding="utf-8") as f:
                tpl = f.read()
            default_proxy = ["navigator", "screen", "history", "localStorage",
                             "sessionStorage", "performance"]
            js = (tpl.replace("{{SCRIPT_URL}}", "").replace("{{MAX_ENTRIES}}", "10000")
                .replace("{{TRACK_CALLS}}", "true").replace("{{TRACK_PROPS}}", "true")
                .replace("{{TRACK_REFLECT}}", "true")
                .replace("'{{PROXY_OBJECTS}}'", _json.dumps(_json.dumps(default_proxy))))
            persist_name = "pre_inject:jsvmp_probe"
        elif name == "jsvmp_probe_transparent":
            hook_path = os.path.join(hooks_dir, "jsvmp_transparent_hook.js")
            if not os.path.exists(hook_path):
                return False, "jsvmp_transparent_hook.js not found"
            with open(hook_path, "r", encoding="utf-8") as f:
                tpl = f.read()
            js = tpl.replace("{{SCRIPT_URL}}", "").replace("{{MAX_ENTRIES}}", "10000")
            persist_name = "pre_inject:jsvmp_probe_transparent"
        elif name in preset_files:
            fpath = os.path.join(hooks_dir, preset_files[name])
            if not os.path.exists(fpath):
                return False, f"hook file not found: {preset_files[name]}"
            with open(fpath, "r", encoding="utf-8") as f:
                js = f.read()
            persist_name = f"pre_inject:{name}"
        else:
            return False, f"unknown hook name: {name}"
    except Exception as e:
        return False, f"prepare failed: {e}"
    try:
        await asyncio.wait_for(
            browser_manager.add_persistent_script(persist_name, js),
            timeout=_PRE_INJECT_REGISTER_TIMEOUT,
        )
        return True, "ok"
    except asyncio.TimeoutError:
        return False, "add_persistent_script timed out (10s)"
    except Exception as e:
        return False, f"add_persistent_script failed: {e}"


@mcp.tool()
async def reload(wait_until: str = "load") -> dict:
    """Reload the current page, preserving any init scripts."""
    try:
        page = await browser_manager.get_active_page()
        await page.reload(wait_until=wait_until)
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def take_screenshot(full_page: bool = False, selector: str | None = None) -> dict:
    """Take a screenshot of the current page or a specific element.

    Args:
        full_page: Capture the entire scrollable page.
        selector: CSS selector of a specific element to capture.
    """
    try:
        page = await browser_manager.get_active_page()
        if selector:
            elem = await page.query_selector(selector)
            if not elem:
                return {"error": f"Element not found: {selector}"}
            data = await elem.screenshot()
        else:
            data = await page.screenshot(full_page=full_page)
        return {"screenshot_base64": base64.b64encode(data).decode(), "format": "png"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def take_snapshot() -> dict:
    """Get the accessibility tree of the current page (token-efficient)."""
    try:
        page = await browser_manager.get_active_page()
        try:
            snapshot = await page.accessibility.snapshot()
        except AttributeError:
            snapshot = await page.evaluate("""() => {
                function walk(node) {
                    if (!node) return null;
                    const item = {};
                    const tag = node.tagName ? node.tagName.toLowerCase() : '';
                    const role = node.getAttribute ? (node.getAttribute('role') || tag) : '';
                    if (role) item.role = role;
                    const name = node.getAttribute ? (node.getAttribute('aria-label')
                        || node.getAttribute('alt') || node.getAttribute('title')
                        || (node.tagName === 'INPUT' ? node.getAttribute('placeholder') : '')
                        || '') : '';
                    if (name) item.name = name;
                    if (['INPUT','TEXTAREA','SELECT'].includes(node.tagName)) item.value = node.value || '';
                    const text = [], children = [];
                    for (const child of (node.childNodes || [])) {
                        if (child.nodeType === 3) { const t = child.textContent.trim(); if (t) text.push(t); }
                        else if (child.nodeType === 1) { const c = walk(child); if (c) children.push(c); }
                    }
                    if (text.length && !children.length) item.text = text.join(' ');
                    if (children.length) item.children = children;
                    if (!item.role && !item.name && !item.text && !children.length) return null;
                    return item;
                }
                return walk(document.body);
            }""")
        return {"snapshot": snapshot}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def click(selector: str) -> dict:
    """Click on a page element."""
    try:
        page = await browser_manager.get_active_page()
        await page.click(selector)
        return {"status": "clicked", "selector": selector}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def type_text(selector: str, text: str, delay: int = 50) -> dict:
    """Type text into an input field with realistic keystroke delays."""
    try:
        page = await browser_manager.get_active_page()
        await page.type(selector, text, delay=delay)
        return {"status": "typed", "selector": selector, "text": text}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def wait_for(
    selector: str | None = None,
    url_pattern: str | None = None,
    timeout: int = 30000,
) -> dict:
    """Wait for an element to appear or a network request matching a URL pattern."""
    try:
        page = await browser_manager.get_active_page()
        if selector:
            await page.wait_for_selector(selector, timeout=timeout)
            return {"status": "found", "selector": selector}
        elif url_pattern:
            await page.wait_for_url(url_pattern, timeout=timeout)
            return {"status": "matched", "url_pattern": url_pattern}
        else:
            return {"error": "Provide either selector or url_pattern"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_page_info() -> dict:
    """Get current page URL, title, and viewport size."""
    try:
        page = await browser_manager.get_active_page()
        viewport = page.viewport_size or {}
        return {
            "url": page.url, "title": await page.title(),
            "viewport_width": viewport.get("width"),
            "viewport_height": viewport.get("height"),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def reset_browser_state(
    clear_persistent_hooks: bool = True,
    clear_network_capture: bool = True,
    clear_active_routes: bool = True,
    clear_cookies: bool = False,
    clear_storage: bool = False,
) -> dict:
    """Reset MCP-side browser residual state without closing the browser.

    Args:
        clear_persistent_hooks: Remove all persistent init scripts.
        clear_network_capture: Clear network request buffer and stop captures.
        clear_active_routes: Clear instrumentation routes.
        clear_cookies: ALSO clear browser cookies (destructive; default False).
        clear_storage: ALSO clear localStorage/sessionStorage (default False).
    """
    from typing import Any
    result: dict[str, Any] = {"status": "reset"}
    try:
        if clear_persistent_hooks:
            try:
                from .hooking import remove_hooks
                r = await remove_hooks(keep_persistent=False)
                result["hooks_removed"] = r
            except Exception as e:
                result["hooks_remove_error"] = str(e)
        if clear_network_capture:
            count = len(browser_manager._network_requests)
            browser_manager._network_requests.clear()
            browser_manager._request_id_counter = 0
            browser_manager._capturing = False
            browser_manager._capture_body = False
            result["network_requests_cleared"] = count
        if clear_active_routes:
            try:
                from .instrumentation import _active_routes, _stop
                count = len(_active_routes)
                await _stop(None)
                result["instrumentation_routes_cleared"] = count
            except Exception as e:
                result["instrumentation_clear_error"] = str(e)
        if clear_cookies:
            try:
                ctx = browser_manager.contexts.get("default")
                if ctx:
                    await ctx.clear_cookies()
                    result["cookies_cleared"] = True
            except Exception as e:
                result["cookies_clear_error"] = str(e)
        if clear_storage:
            try:
                page = await browser_manager.get_active_page()
                await page.evaluate(
                    "() => { try { localStorage.clear(); } catch(e) {} "
                    "try { sessionStorage.clear(); } catch(e) {} }"
                )
                result["storage_cleared"] = True
            except Exception as e:
                result["storage_clear_error"] = str(e)
        return result
    except Exception as e:
        return {"error": str(e)}
