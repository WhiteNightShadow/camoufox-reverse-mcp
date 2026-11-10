from __future__ import annotations

import json

from ..server import mcp, browser_manager
from ..utils.jsvmp_hooks import (
    READ_WITH,
    main_world_init_script,
    proxy_probe,
    transparent_probe,
)
from ..utils.worlds import evaluate_in_world, native_main_world_available


@mcp.tool()
async def hook_jsvmp_interpreter(
    script_url: str = "",
    persistent: bool = True,
    mode: str = "proxy",
    track_calls: bool = True,
    track_props: bool = True,
    track_reflect: bool = True,
    proxy_objects: list[str] | None = None,
    max_entries: int = 10000,
) -> dict:
    """Install a JSVMP runtime probe in the page main world.

    The probe runs where page scripts run, so their property reads and calls
    are recorded. Read the log with
    evaluate_js("window.__mcp_jsvmp_log || []", world="main"); the default
    isolated world cannot see it. remove_hooks() restores what the probe replaced.

    mode="proxy" wraps Function.prototype.apply/call/bind, Reflect.*,
    Date.now, performance.now and Math.random, and puts Proxies on globals
    (navigator, screen, etc.). Page code can detect all of this, so RS/AK-style
    signature-based anti-bot will notice; use instrumentation(action='install')
    (source-level rewrite) for those. mode="transparent" only replaces getters
    on prototypes (Navigator, Screen, Document, ...). It changes less, but the
    getters are no longer the originals: Function.prototype.toString.call(getter)
    shows JS source.

    IMPORTANT — timing for sync-loaded SDKs (e.g. webmssdk):
        JSVMP interpreters capture native references at startup via closures.
        If you install hooks AFTER the SDK has loaded, the SDK's closures
        already hold the original (un-hooked) references — your hooks will
        never fire. You MUST install hooks BEFORE navigate():
          1. launch_browser()
          2. hook_jsvmp_interpreter(mode='transparent', persistent=True)
          3. navigate("https://www.douyin.com/...")
        If already navigated, call instrumentation(action='reload') after
        installing hooks to force a page reload with hooks active.

    Args:
        script_url: Target script URL substring for stack filtering.
        persistent: Survive navigation (default True).
        mode: "proxy" (full coverage, detectable) or "transparent" (replaces getters only).
        track_calls, track_props, track_reflect: Only for mode="proxy".
        proxy_objects: Objects to proxy (default: navigator, screen, etc.).
        max_entries: Log buffer cap (default 10000).

    Returns:
        dict with status, mode, world, read_with and install details.
    """
    try:
        page = await browser_manager.get_active_page()
        if mode == "transparent":
            probe = transparent_probe(script_url, max_entries)
            script_name = f"jsvmp_transparent:{script_url or 'all'}"
        elif mode == "proxy":
            probe = proxy_probe(script_url, max_entries, track_calls, track_props,
                                track_reflect, proxy_objects)
            script_name = f"jsvmp_probe:{script_url or 'all'}"
        else:
            return {"error": f"unknown mode '{mode}', use 'proxy' or 'transparent'"}

        # v1.0.1: detect if page already loaded (timing issue)
        page_already_loaded = page.url and page.url != "about:blank"
        install_error = None
        try:
            info, backend, backend_warning = await evaluate_in_world(
                page, probe, "main", script_is_function=False)
        except Exception as e:
            install_error, backend = e, None
        if persistent:
            # The init script takes the same channel the immediate install could use.
            native = (backend == "camoufox_native" if backend
                      else await native_main_world_available(page))
            await browser_manager.add_persistent_script(
                script_name, main_world_init_script(probe, native=native))
        if install_error is not None:
            return {"status": "partial", "mode": mode, "world": "main",
                    "warning": f"Evaluate failed: {install_error}", "persistent": persistent}
        info = info if isinstance(info, dict) else {}

        result = {
            "status": "instrumented", "mode": mode,
            "script_url": script_url or "(all)", "persistent": persistent,
            "world": "main", "execution_backend": backend,
            "data_location": "window.__mcp_jsvmp_log",
            "read_with": READ_WITH,
        }
        if info.get("installed") is False:
            result["already_installed"] = True
        warnings = [backend_warning] if backend_warning else []
        if mode == "transparent":
            if "tapped" in info:
                result["getters_tapped"] = info["tapped"]
                result["getters_seen"] = info["total"]
            if page_already_loaded:
                warnings.append(
                    "Hooks installed on already-loaded page. If the target "
                    "SDK (e.g. webmssdk) was loaded BEFORE this call, it "
                    "likely captured native references at startup (closure "
                    "capture) and won't trigger your hooks. Call "
                    "instrumentation(action='reload') or re-navigate to "
                    "force SDK re-init with hooks in place."
                )
        else:
            result["warning"] = "proxy mode is detectable by RS/AK-style anti-bot."
            # Failures on about:blank (e.g. storage) say nothing about the target page.
            if info.get("proxyFailures") and page_already_loaded:
                warnings.append("Could not proxy: " + ", ".join(map(str, info["proxyFailures"])))
            if page_already_loaded:
                warnings.insert(0, result.pop("warning"))
                warnings.append(
                    "Hooks installed on already-loaded page. If the target "
                    "SDK was loaded BEFORE this call, it likely captured "
                    "native references at startup (closure capture) and "
                    "won't trigger your hooks. Call "
                    "instrumentation(action='reload') to re-trigger."
                )
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def compare_env(properties: list[str] | None = None) -> dict:
    """Collect browser environment fingerprint data for comparison with Node.js/jsdom.

    Args:
        properties: Optional list of specific properties to check.
            If omitted, checks navigator, screen, canvas, WebGL, audio, timing.

    Returns:
        dict with categorized environment data and their values.
    """
    try:
        page = await browser_manager.get_active_page()
        custom_props_js = ""
        if properties:
            custom_props_js = f"""
            const customProps = {json.dumps(properties)};
            for (const prop of customProps) {{
                try {{
                    const val = eval(prop);
                    result.custom[prop] = {{
                        value: typeof val === 'object' ? JSON.stringify(val).substring(0, 500) : String(val),
                        type: typeof val
                    }};
                }} catch(e) {{
                    result.custom[prop] = {{ value: null, error: e.message }};
                }}
            }}"""

        result = await page.evaluate(f"""() => {{
            const result = {{ navigator: {{}}, screen: {{}}, canvas: {{}}, webgl: {{}},
                             audio: {{}}, timing: {{}}, misc: {{}}, custom: {{}} }};
            const navProps = ['userAgent', 'platform', 'language', 'languages',
                'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints',
                'vendor', 'cookieEnabled', 'webdriver'];
            for (const p of navProps) {{
                try {{ result.navigator[p] = {{ value: String(navigator[p]), type: typeof navigator[p] }}; }}
                catch(e) {{ result.navigator[p] = {{ value: null, error: e.message }}; }}
            }}
            const screenProps = ['width', 'height', 'availWidth', 'availHeight', 'colorDepth'];
            for (const p of screenProps) {{
                try {{ result.screen[p] = {{ value: screen[p], type: typeof screen[p] }}; }}
                catch(e) {{ result.screen[p] = {{ value: null, error: e.message }}; }}
            }}
            result.screen.devicePixelRatio = {{ value: window.devicePixelRatio, type: 'number' }};
            result.timing.timezoneOffset = {{ value: new Date().getTimezoneOffset(), type: 'number' }};
            result.timing.timezone = {{ value: Intl.DateTimeFormat().resolvedOptions().timeZone, type: 'string' }};
            {custom_props_js}
            return result;
        }}""")
        return result
    except Exception as e:
        return {"error": str(e)}
