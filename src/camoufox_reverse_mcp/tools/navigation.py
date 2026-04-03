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
        headless: Headless mode (default False).
        os_type: OS fingerprint: "auto", "windows", "macos", "linux".
        locale: Browser locale ("auto" detects system).
        proxy: Proxy URL (e.g. "http://127.0.0.1:7890").
        humanize: Enable humanized mouse movement.
        geoip: Auto-infer geolocation from proxy IP.
        block_images: Block image loading.
        block_webrtc: Block WebRTC.
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
        # Try modern Playwright API first, fall back to legacy
        try:
            snapshot = await page.accessibility.snapshot()
        except AttributeError:
            # Playwright >= 1.42 removed page.accessibility, use JS fallback
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
                    if (['INPUT','TEXTAREA','SELECT'].includes(node.tagName)) {
                        item.value = node.value || '';
                    }
                    const text = [];
                    const children = [];
                    for (const child of (node.childNodes || [])) {
                        if (child.nodeType === 3) {
                            const t = child.textContent.trim();
                            if (t) text.push(t);
                        } else if (child.nodeType === 1) {
                            const c = walk(child);
                            if (c) children.push(c);
                        }
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


@mcp.tool()
async def get_page_content() -> dict:
    """Export the rendered DOM content of the current page in one call.

    Returns rendered HTML, page title, meta tags, and visible text.
    Saves writing ad-hoc evaluate_js scripts for common extraction needs.

    Returns:
        dict with title, meta tags, rendered_html (first 50KB), and visible_text (first 20KB).
    """
    try:
        page = await browser_manager.get_active_page()
        result = await page.evaluate("""() => {
            const title = document.title || '';
            const metas = Array.from(document.querySelectorAll('meta')).map(m => {
                const o = {};
                for (const a of m.attributes) o[a.name] = a.value;
                return o;
            });
            const html = document.documentElement.outerHTML;
            // Extract visible text: walk body, skip script/style/hidden
            function visibleText(node) {
                if (!node) return '';
                if (node.nodeType === 3) return node.textContent;
                if (node.nodeType !== 1) return '';
                const tag = node.tagName;
                if (['SCRIPT','STYLE','NOSCRIPT','SVG'].includes(tag)) return '';
                const style = window.getComputedStyle(node);
                if (style.display === 'none' || style.visibility === 'hidden') return '';
                let text = '';
                for (const child of node.childNodes) text += visibleText(child);
                if (['P','DIV','BR','LI','H1','H2','H3','H4','H5','H6','TR','DT','DD'].includes(tag)) {
                    text = '\\n' + text + '\\n';
                }
                return text;
            }
            let visible = visibleText(document.body).replace(/\\n{3,}/g, '\\n\\n').trim();
            return { title, metas, html, visible };
        }""")
        html = result.get("html", "")
        visible = result.get("visible", "")
        MAX_HTML = 50000
        MAX_TEXT = 20000
        resp = {
            "title": result.get("title"),
            "meta": result.get("metas"),
            "rendered_html": html[:MAX_HTML],
            "visible_text": visible[:MAX_TEXT],
        }
        if len(html) > MAX_HTML:
            resp["html_truncated"] = True
            resp["html_total_size"] = len(html)
        if len(visible) > MAX_TEXT:
            resp["text_truncated"] = True
            resp["text_total_size"] = len(visible)
        return resp
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_session_info() -> dict:
    """Get the current session state: browser, contexts, pages, capture status.

    Shows exactly what browser/context/page is active and what hooks/captures
    are in place. Use this to understand the current debugging environment.

    Returns:
        dict with browser status, context list, page list, active page,
        capture state, persistent scripts, and init scripts.
    """
    try:
        running = browser_manager.browser is not None
        contexts = list(browser_manager.contexts.keys())
        pages = {}
        for name, p in browser_manager.pages.items():
            try:
                pages[name] = {"url": p.url, "title": await p.title()}
            except Exception:
                pages[name] = {"url": "unknown", "title": "unknown"}
        persistent = [s["name"] for s in browser_manager._persistent_scripts]
        return {
            "browser_running": running,
            "contexts": contexts,
            "pages": pages,
            "active_page": browser_manager.active_page_name,
            "network_capture": {
                "active": browser_manager._capturing,
                "pattern": browser_manager._capture_pattern,
                "capture_body": browser_manager._capture_body,
                "captured_count": len(browser_manager._network_requests),
            },
            "persistent_scripts": persistent,
            "init_scripts_count": len(browser_manager._init_scripts),
            "console_log_count": len(browser_manager._console_logs),
        }
    except Exception as e:
        return {"error": str(e)}
