from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager


def _fill_shared_placeholders(template: str, script_url: str, max_entries: int) -> str:
    """Fill the placeholders both JSVMP hook templates share.

    json.dumps turns script_url into a complete JS string literal, so quotes,
    backslashes and line breaks in it cannot end the string early.
    """
    return (template
        .replace("'{{SCRIPT_URL}}'", json.dumps(script_url))
        .replace("{{MAX_ENTRIES}}", str(int(max_entries))))


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
    """Install a JSVMP runtime probe.

    Multi-path instrumentation for JSVMP interpreters. Wraps Reflect.get/apply,
    installs Proxies on globals (navigator, screen, etc.), intercepts timing APIs.

    LIMITATIONS: "proxy" mode is DETECTABLE by RS/AK-style signature-based anti-bot.
    For those, use instrumentation(action='install') (source-level rewrite) or
    mode='transparent' instead.

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
        mode: "proxy" (full coverage, detectable) or "transparent" (safe, lower coverage).
        track_calls, track_props, track_reflect: Only for mode="proxy".
        proxy_objects: Objects to proxy (default: navigator, screen, etc.).
        max_entries: Log buffer cap (default 10000).

    Returns:
        dict with status, mode, coverage summary.
    """
    try:
        hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
        page = await browser_manager.get_active_page()

        if mode == "transparent":
            hook_path = os.path.join(hooks_dir, "jsvmp_transparent_hook.js")
            if not os.path.exists(hook_path):
                return {"error": "jsvmp_transparent_hook.js not found"}
            with open(hook_path, "r", encoding="utf-8") as f:
                template = f.read()
            hook_js = _fill_shared_placeholders(template, script_url, max_entries)
            if persistent:
                await browser_manager.add_persistent_script(
                    f"jsvmp_transparent:{script_url or 'all'}", hook_js)
            # v1.0.1: detect if page already loaded (timing issue)
            page_already_loaded = page.url and page.url != "about:blank"
            try:
                await page.evaluate(hook_js)
            except Exception as e:
                return {"status": "partial", "mode": "transparent",
                        "warning": f"Evaluate failed: {e}", "persistent": persistent}
            result = {
                "status": "instrumented", "mode": "transparent",
                "script_url": script_url or "(all)", "persistent": persistent,
                "data_location": "window.__mcp_jsvmp_log",
            }
            if page_already_loaded:
                result["warnings"] = [
                    "Hooks installed on already-loaded page. If the target "
                    "SDK (e.g. webmssdk) was loaded BEFORE this call, it "
                    "likely captured native references at startup (closure "
                    "capture) and won't trigger your hooks. Call "
                    "instrumentation(action='reload') or re-navigate to "
                    "force SDK re-init with hooks in place."
                ]
            return result

        elif mode == "proxy":
            if proxy_objects is None:
                proxy_objects = ["navigator", "screen", "history",
                                 "localStorage", "sessionStorage", "performance"]
            with open(os.path.join(hooks_dir, "jsvmp_hook.js"), "r", encoding="utf-8") as f:
                template = f.read()
            hook_js = (_fill_shared_placeholders(template, script_url, max_entries)
                .replace("{{TRACK_CALLS}}", "true" if track_calls else "false")
                .replace("{{TRACK_PROPS}}", "true" if track_props else "false")
                .replace("{{TRACK_REFLECT}}", "true" if track_reflect else "false")
                .replace("'{{PROXY_OBJECTS}}'", json.dumps(json.dumps(proxy_objects))))
            if persistent:
                await browser_manager.add_persistent_script(
                    f"jsvmp_probe:{script_url or 'all'}", hook_js)
            # v1.0.1: detect if page already loaded (timing issue)
            page_already_loaded = page.url and page.url != "about:blank"
            try:
                await page.evaluate(hook_js)
            except Exception as e:
                return {"status": "partial", "mode": "proxy",
                        "warning": f"Evaluate failed: {e}", "persistent": persistent}
            result = {
                "status": "instrumented", "mode": "proxy",
                "script_url": script_url or "(all)", "persistent": persistent,
                "data_location": "window.__mcp_jsvmp_log",
                "warning": "proxy mode is detectable by RS/AK-style anti-bot.",
            }
            if page_already_loaded:
                result.setdefault("warnings", [])
                if isinstance(result.get("warning"), str):
                    result["warnings"].append(result.pop("warning"))
                result["warnings"].append(
                    "Hooks installed on already-loaded page. If the target "
                    "SDK was loaded BEFORE this call, it likely captured "
                    "native references at startup (closure capture) and "
                    "won't trigger your hooks. Call "
                    "instrumentation(action='reload') to re-trigger."
                )
            return result
        else:
            return {"error": f"unknown mode '{mode}', use 'proxy' or 'transparent'"}
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
