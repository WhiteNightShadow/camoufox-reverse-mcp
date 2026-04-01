from __future__ import annotations

import base64

from ..server import mcp, browser_manager


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
) -> dict:
    """Launch the Camoufox anti-detection browser.

    Args:
        headless: Run in headless mode (default False for debugging visibility).
        os_type: OS fingerprint to emulate - "auto" (detect host OS),
            "windows", "macos", or "linux". Using "auto" ensures CJK fonts
            render correctly on the host system.
        locale: Browser locale such as "zh-CN", "en-US". Defaults to "auto"
            which detects the system locale. Affects Accept-Language headers
            and content language preferences.
        proxy: Proxy server URL (e.g. "http://127.0.0.1:7890").
        humanize: Enable humanized mouse movement to mimic real users.
        geoip: Auto-infer geolocation from proxy IP.
        block_images: Block image loading for faster page loads.
        block_webrtc: Block WebRTC to prevent IP leaks.

    Returns:
        dict with status, headless flag, os type, locale, and page list.
    """
    try:
        config = {
            "headless": headless,
            "os": os_type,
            "locale": locale,
            "humanize": humanize,
            "geoip": geoip,
            "block_images": block_images,
            "block_webrtc": block_webrtc,
        }
        if proxy:
            config["proxy"] = {"server": proxy}
        return await browser_manager.launch(config)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def close_browser() -> dict:
    """Close the Camoufox browser and release all resources.

    Returns:
        dict with status "closed".
    """
    try:
        return await browser_manager.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def navigate(url: str, wait_until: str = "load") -> dict:
    """Navigate to the specified URL.

    Args:
        url: Target URL to navigate to.
        wait_until: When to consider navigation complete -
            "load", "domcontentloaded", or "networkidle".

    Returns:
        dict with url, title, and HTTP status.
    """
    try:
        page = await browser_manager.get_active_page()
        resp = await page.goto(url, wait_until=wait_until)
        return {
            "url": page.url,
            "title": await page.title(),
            "status": resp.status if resp else None,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def reload(wait_until: str = "load") -> dict:
    """Reload the current page, preserving any init scripts.

    Args:
        wait_until: "load", "domcontentloaded", or "networkidle".

    Returns:
        dict with url and title after reload.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.reload(wait_until=wait_until)
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def go_back() -> dict:
    """Navigate back in browser history.

    Returns:
        dict with url and title after going back.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.go_back()
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def take_screenshot(full_page: bool = False, selector: str | None = None) -> dict:
    """Take a screenshot of the current page or a specific element.

    Args:
        full_page: Capture the entire scrollable page (default False).
        selector: CSS selector of a specific element to capture.

    Returns:
        dict with base64-encoded PNG image data.
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
        return {
            "screenshot_base64": base64.b64encode(data).decode(),
            "format": "png",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def take_snapshot() -> dict:
    """Get the accessibility tree of the current page as a structured text representation.

    More token-efficient than screenshots for AI analysis. Returns the page's
    semantic structure including roles, names, and values of UI elements.

    Returns:
        dict with the accessibility tree snapshot.
    """
    try:
        page = await browser_manager.get_active_page()
        snapshot = await page.accessibility.snapshot()
        return {"snapshot": snapshot}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def click(selector: str) -> dict:
    """Click on a page element.

    Args:
        selector: CSS selector of the element to click.

    Returns:
        dict with status and the selector that was clicked.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.click(selector)
        return {"status": "clicked", "selector": selector}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def type_text(selector: str, text: str, delay: int = 50) -> dict:
    """Type text into an input field with realistic keystroke delays.

    Args:
        selector: CSS selector of the input element.
        text: Text to type.
        delay: Delay between keystrokes in milliseconds (default 50).

    Returns:
        dict with status, selector, and the text typed.
    """
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
    """Wait for an element to appear or a network request matching a URL pattern.

    Args:
        selector: CSS selector to wait for (element appearance).
        url_pattern: URL glob pattern to wait for (network request completion).
        timeout: Maximum wait time in milliseconds (default 30000).

    Returns:
        dict with status and what was waited for.
    """
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
    """Get information about the current page including URL, title, and viewport size.

    Returns:
        dict with url, title, and viewport dimensions.
    """
    try:
        page = await browser_manager.get_active_page()
        viewport = page.viewport_size or {}
        return {
            "url": page.url,
            "title": await page.title(),
            "viewport_width": viewport.get("width"),
            "viewport_height": viewport.get("height"),
        }
    except Exception as e:
        return {"error": str(e)}
