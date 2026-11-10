from __future__ import annotations

import asyncio
import base64
import os

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..server import browser_manager, mcp
from ..utils.frames import list_frame_metadata
from ..utils.jsvmp_hooks import main_world_init_script, proxy_probe, transparent_probe
from ..utils.worlds import native_main_world_available

_PRE_INJECT_REGISTER_TIMEOUT = 10.0


@mcp.tool()
async def launch_browser(
    headless: bool | None = None,
    os_type: str | None = None,
    locale: str | None = None,
    proxy: str | None = None,
    humanize: bool | None = None,
    geoip: bool | None = None,
    block_images: bool | None = None,
    block_webrtc: bool | None = None,
    enable_trace: bool = False,
    trace_objects: list[str] | None = None,
    trace_max_events: int = 100000,
    ws_endpoint: str | None = None,
    browser_version: str | None = None,
) -> dict:
    """Launch the Camoufox anti-detection browser, or attach to a running one.

    Leave out any option you do not need to change. It then keeps the value the
    server was started with (for example --headless), or the default below if
    the server did not set one. An option passed here wins for this launch.

    Args:
        headless: Run in headless mode (default False).
        os_type: OS fingerprint - "auto", "windows", "macos", or "linux"
            (default "auto").
        locale: Browser locale (e.g. "zh-CN"). "auto" detects system locale
            (default "auto").
        proxy: Proxy server URL (e.g. "http://127.0.0.1:7890").
        humanize: Enable humanized mouse movement.
        geoip: Auto-infer geolocation from proxy IP.
        block_images: Block image loading.
        block_webrtc: Block WebRTC to prevent IP leaks.
        enable_trace: Enable engine-level property access tracing.
            Requires camoufox-reverse custom browser build.
            When enabled, use trace_property_access() to capture DOM access.
        trace_objects: Optional exact native object-name allowlist (for example
            ["navigator", "screen", "webgl"]). Empty traces all native sites
            declared by the selected reverse build.
        trace_max_events: Per Firefox process/session event cap (1..200000).
        browser_version: Select one already-installed Camoufox 0.5+ browser
            without changing its persistent active version. Use a repo-qualified
            selector such as "official/beta.30" or
            "whitenightshadow/152.0.4-beta.30-reverse.5". Omit it to preserve the
            active/default behavior, including Camoufox 0.4.x installations.
            The selected browser must match the active browser's exact
            version/build because Camoufox reads shared resources from active.
        ws_endpoint: Attach to an already-running Camoufox server instead of
            launching a new browser. Start the server with
            `python -m camoufox server`, copy its "Websocket endpoint:
            ws://127.0.0.1:<port>/<guid>" line, and pass that full URL here.
            When set, all other launch args (os_type/locale/proxy/...) are
            ignored — fingerprint config is owned by the running server. Start the
            server with an os fingerprint matching the host for font-metric parity
            (attach mode cannot inject the host/os font-fallback shim that launch
            mode does). close_browser() will only disconnect; the server keeps running.

    Returns:
        dict with status, config, and page list.
    """
    try:
        if ws_endpoint:
            # Attach mode: only the endpoint matters; the server owns the config.
            result = await browser_manager.launch({"ws_endpoint": ws_endpoint})
            if result.get("status") == "already_running":
                result.setdefault("warnings", []).append(
                    "A browser is already active. Call close_browser() before attaching."
                )
            return result

        config = {
            "enable_trace": enable_trace,
            "trace_objects": trace_objects or [],
            "trace_max_events": trace_max_events,
        }
        # Only values the caller passed go in, so the rest keep the startup options.
        options = {
            "headless": headless, "os": os_type, "locale": locale,
            "humanize": humanize, "geoip": geoip,
            "block_images": block_images, "block_webrtc": block_webrtc,
        }
        config.update({key: value for key, value in options.items() if value is not None})
        if browser_version:
            config["browser_version"] = browser_version
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


async def _close_browser_impl() -> dict:
    """Close the Camoufox browser and release all resources."""
    try:
        from .instrumentation import (
            _active_routes,
            _source_site_registry,
            _stop,
        )

        route_count = len(_active_routes)
        site_count = len(_source_site_registry)
        await _stop(None)
        _source_site_registry.clear()
        result = await browser_manager.close()
        result["instrumentation_routes_cleared"] = route_count
        result["source_sites_cleared"] = site_count
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def close_browser() -> dict:
    """Close the browser after serializing any native trace transition."""
    async with browser_manager._trace_action_lock:
        return await _close_browser_impl()


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
                    browser_manager.clear_network_capture()
                    warnings.append(f"cleared {cleared_count} stale network requests")
            except Exception:
                pass

        if collect_response_chain:
            browser_manager.reset_nav_responses()

        if pre_inject_hooks:
            for name in pre_inject_hooks:
                ok, msg = await _inject_hook_by_name(name, page)
                if ok:
                    hooks_injected.append(name)
                else:
                    warnings.append(f"hook '{name}' failed: {msg}")

        navigation_timed_out = False
        try:
            resp = await page.goto(url, wait_until=wait_until, timeout=30000)
        except Exception as e:
            if isinstance(e, (PlaywrightTimeoutError, asyncio.TimeoutError)):
                warnings.append(f"goto timeout for '{wait_until}'; checking usability")
                try:
                    dom_ready = await asyncio.wait_for(page.evaluate("document.readyState"), timeout=3.0)
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
                # goto logs often contain "waiting until load" even for refused
                # connections. Preserve the original transport error rather than
                # masking it with a failed readyState probe on an error page.
                return {"error": str(e), "phase": "goto", "requested_url": url,
                        "current_url": page.url,
                        "guidance": "Inspect captured document failures and current page state; no automatic navigation replay was attempted."}
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


async def _inject_hook_by_name(name: str, page=None) -> tuple[bool, str]:
    """Register a hook as a persistent context-level script.

    page tells the JSVMP probes whether Camoufox's native main-world channel is
    on; without one they assume it is.
    """
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
    preset_files = {
        "xhr": "xhr_hook.js", "fetch": "fetch_hook.js",
        "crypto": "crypto_hook.js", "websocket": "websocket_hook.js",
        "debugger_bypass": "debugger_trap.js",
        "cookie_hook": "cookie_hook.js", "runtime_probe": "runtime_probe.js",
    }
    try:
        # The JSVMP probes go into the page main world, like hook_jsvmp_interpreter.
        if name in ("jsvmp_probe", "jsvmp_probe_transparent"):
            native = page is None or await native_main_world_available(page)
            probe = proxy_probe() if name == "jsvmp_probe" else transparent_probe()
            js = main_world_init_script(probe, native=native)
            persist_name = f"pre_inject:{name}"
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
        current_url = page.url
        if not current_url or current_url == "about:blank":
            return {"error": "No page loaded to reload"}
        await page.goto(current_url, wait_until=wait_until)
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
async def take_snapshot(timeout_ms: int = 5000, max_nodes: int = 1000) -> dict:
    """Get a bounded accessibility tree, or a labelled DOM fallback.

    Args:
        timeout_ms: Maximum wait for snapshot collection (1..30000). A hung
            error-page accessibility query returns an error without restarting
            the browser or replaying navigation.
        max_nodes: Maximum retained tree containers (1..5000). Depth is capped
            at 16 and individual strings at 2000 characters; truncation is explicit.
    """
    if not 1 <= timeout_ms <= 30000 or not 1 <= max_nodes <= 5000:
        return {"error": "timeout_ms must be 1..30000 and max_nodes 1..5000"}
    try:
        page = await browser_manager.get_active_page()
        async def collect():
            try:
                return await page.accessibility.snapshot(), "accessibility"
            except AttributeError:
                return await page.evaluate("""(limit) => {
                    let visited = 0;
                    function walk(node, depth) {
                        if (!node) return null;
                        if (++visited > limit || depth > 16) return {truncated: true};
                        const item = {};
                        const tag = node.tagName ? node.tagName.toLowerCase() : '';
                        const role = node.getAttribute ? (node.getAttribute('role') || tag) : '';
                        if (role) item.role = role;
                        const name = node.getAttribute ? (node.getAttribute('aria-label')
                            || node.getAttribute('alt') || node.getAttribute('title')
                            || (node.tagName === 'INPUT' ? node.getAttribute('placeholder') : '') || '') : '';
                        if (name) item.name = name;
                        if (['INPUT','TEXTAREA','SELECT'].includes(node.tagName)) item.value = node.value || '';
                        const text = [], children = [];
                        for (const child of (node.childNodes || [])) {
                            if (visited >= limit) { item.truncated = true; break; }
                            if (child.nodeType === 3) { const t = child.textContent.trim(); if (t) text.push(t); }
                            else if (child.nodeType === 1) { const c = walk(child, depth + 1); if (c) children.push(c); }
                        }
                        if (text.length && !children.length) item.text = text.join(' ');
                        if (children.length) item.children = children;
                        return item;
                    }
                    return walk(document.body, 0);
                }""", max_nodes), "dom_fallback"
        snapshot, method = await asyncio.wait_for(collect(), timeout_ms / 1000)
        remaining = max_nodes
        truncated = False
        def trim(value, depth=0):
            nonlocal remaining, truncated
            if isinstance(value, (dict, list)):
                if remaining <= 0 or depth > 16:
                    truncated = True
                    return {"truncated": True}
                remaining -= 1
                if isinstance(value, dict):
                    if value.get("truncated") is True:
                        truncated = True
                    return {key: trim(item, depth + 1) for key, item in value.items()}
                result = []
                for item in value:
                    if remaining <= 0:
                        truncated = True
                        break
                    result.append(trim(item, depth + 1))
                return result
            if isinstance(value, str) and len(value) > 2000:
                truncated = True
                return value[:2000]
            return value
        result = trim(snapshot)
        return {"snapshot": result, "method": method, "truncated": truncated,
                "limits": {"max_nodes": max_nodes, "max_depth": 16, "max_text_chars": 2000}}
    except asyncio.TimeoutError:
        return {"error": "Snapshot timed out; inspect get_page_info and captured network failures instead of repeating the same snapshot.",
                "timeout_ms": timeout_ms, "automatic_retry": False}
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
    """Get page metadata plus a zero-based frame list for targeted tools."""
    try:
        page = await browser_manager.get_active_page()
        viewport = page.viewport_size or {}
        return {
            "url": page.url, "title": await page.title(),
            "viewport_width": viewport.get("width"),
            "viewport_height": viewport.get("height"),
            "frames": list_frame_metadata(page),
        }
    except Exception as e:
        return {"error": str(e)}


async def _reset_browser_state_impl(
    clear_persistent_hooks: bool = True,
    clear_network_capture: bool = True,
    clear_active_routes: bool = True,
    stop_engine_trace: bool = True,
    clear_cookies: bool = False,
    clear_storage: bool = False,
) -> dict:
    """Reset MCP-side browser residual state without closing the browser.

    Args:
        clear_persistent_hooks: Remove all persistent init scripts.
        clear_network_capture: Clear network request buffer and stop captures.
        clear_active_routes: Clear instrumentation routes.
        stop_engine_trace: Stop only this launch's native PropertyTracer session.
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
            browser_manager.clear_network_capture()
            browser_manager._capturing = False
            browser_manager._capture_body = False
            result["network_requests_cleared"] = count
        if clear_active_routes:
            try:
                from .instrumentation import (
                    _active_routes,
                    _source_site_registry,
                    _stop,
                )
                count = len(_active_routes)
                site_count = len(_source_site_registry)
                await _stop(None)
                _source_site_registry.clear()
                result["instrumentation_routes_cleared"] = count
                result["source_sites_cleared"] = site_count
            except Exception as e:
                result["instrumentation_clear_error"] = str(e)
        if stop_engine_trace:
            try:
                from ..property_trace import set_trace_state

                trace_base = getattr(browser_manager, "_trace_base_dir", None)
                if trace_base:
                    transition = await set_trace_state(
                        "off",
                        trace_base,
                        features=(browser_manager._runtime_browser or {}).get(
                            "property_trace_features", []
                        ),
                    )
                    result["engine_trace_processes_stopped"] = transition.get(
                        "count", 0
                    )
                    result["engine_trace_acknowledged"] = transition.get(
                        "acknowledged", False
                    )
                    if transition.get("error"):
                        result["engine_trace_stop_error"] = transition["error"]
                    browser_manager._trace_started_at = None
                else:
                    result["engine_trace_processes_stopped"] = 0
            except Exception as e:
                result["engine_trace_stop_error"] = str(e)
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


@mcp.tool()
async def reset_browser_state(
    clear_persistent_hooks: bool = True,
    clear_network_capture: bool = True,
    clear_active_routes: bool = True,
    stop_engine_trace: bool = True,
    clear_cookies: bool = False,
    clear_storage: bool = False,
) -> dict:
    """Reset browser residual state after serializing trace transitions."""
    async with browser_manager._trace_action_lock:
        return await _reset_browser_state_impl(
            clear_persistent_hooks=clear_persistent_hooks,
            clear_network_capture=clear_network_capture,
            clear_active_routes=clear_active_routes,
            stop_engine_trace=stop_engine_trace,
            clear_cookies=clear_cookies,
            clear_storage=clear_storage,
        )
