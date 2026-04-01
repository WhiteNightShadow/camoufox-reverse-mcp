from __future__ import annotations

import base64

from ..server import mcp, browser_manager


@mcp.tool()
async def get_fingerprint_info() -> dict:
    """Get the current browser fingerprint information.

    Reads navigator, screen, WebGL, and canvas fingerprint data from the browser
    to verify what fingerprint the anti-detection engine is presenting.

    Returns:
        dict with userAgent, platform, language, screen dimensions, WebGL info,
        canvas fingerprint hash, and other fingerprint markers.
    """
    try:
        page = await browser_manager.get_active_page()
        info = await page.evaluate("""() => {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            let webglRenderer = 'N/A', webglVendor = 'N/A';
            if (gl) {
                const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                if (dbg) {
                    webglRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
                    webglVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
                }
            }

            const canvas2d = document.createElement('canvas');
            const ctx = canvas2d.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillText('fingerprint', 2, 2);
            const canvasHash = canvas2d.toDataURL().slice(-32);

            return {
                userAgent: navigator.userAgent,
                platform: navigator.platform,
                language: navigator.language,
                languages: navigator.languages,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory,
                maxTouchPoints: navigator.maxTouchPoints,
                screenWidth: screen.width,
                screenHeight: screen.height,
                screenColorDepth: screen.colorDepth,
                devicePixelRatio: window.devicePixelRatio,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                webglRenderer,
                webglVendor,
                canvasHash,
                cookieEnabled: navigator.cookieEnabled,
                doNotTrack: navigator.doNotTrack,
                webdriver: navigator.webdriver,
            };
        }""")
        return info
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def check_detection(url: str = "https://bot.sannysoft.com") -> dict:
    """Navigate to a bot detection site and take a screenshot to verify anti-detection.

    Opens a fingerprint/bot detection site (default: bot.sannysoft.com) and
    captures a screenshot for visual inspection of detection results.

    Args:
        url: Detection site URL. Common choices:
            - "https://bot.sannysoft.com" (default)
            - "https://browserscan.net"
            - "https://abrahamjuliot.github.io/creepjs/"

    Returns:
        dict with screenshot_base64 and any detected issues.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(3000)

        screenshot_data = await page.screenshot(full_page=True)

        webdriver_check = await page.evaluate("""() => {
            const issues = [];
            if (navigator.webdriver === true) issues.push('navigator.webdriver is true');
            if (window.chrome === undefined && navigator.userAgent.includes('Chrome'))
                issues.push('window.chrome is undefined');
            if (document.querySelector('.failed, .warn, [class*="fail"]'))
                issues.push('Page shows failed/warn elements');
            return issues;
        }""")

        return {
            "url": url,
            "screenshot_base64": base64.b64encode(screenshot_data).decode(),
            "detected_issues": webdriver_check,
            "title": await page.title(),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def bypass_debugger_trap() -> dict:
    """Inject anti-debugging bypass script to neutralize debugger traps.

    Bypasses common anti-debugging techniques:
    - Infinite debugger loops via Function constructor
    - setInterval/setTimeout debugger checks
    - Function.prototype.toString detection

    This is a convenience wrapper around inject_hook_preset("debugger_bypass").

    Returns:
        dict with injection status.
    """
    try:
        from .hooking import inject_hook_preset
        return await inject_hook_preset("debugger_bypass")
    except Exception as e:
        return {"error": str(e)}
