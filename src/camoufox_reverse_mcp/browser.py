from __future__ import annotations

import os as _os
import platform
import time
from collections import deque
from typing import Any

from playwright.async_api import Page, BrowserContext

MAX_LOG_SIZE = 500


def detect_host_os() -> str:
    """Return the Camoufox os identifier matching the current host."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "windows"


def detect_system_locale() -> str:
    """Best-effort detection of the host's locale (e.g. 'zh-CN')."""
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = _os.environ.get(var, "")
        if val and val not in ("C", "POSIX"):
            return val.split(".")[0].replace("_", "-")
    return "en-US"


class BrowserManager:
    """Manages the Camoufox browser lifecycle, contexts, and pages."""

    default_config: dict[str, Any] = {}

    def __init__(self) -> None:
        self.browser = None
        self.contexts: dict[str, BrowserContext] = {}
        self.pages: dict[str, Page] = {}
        self.active_page_name: str | None = None
        self._cm = None  # AsyncCamoufox context manager
        self._console_logs: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._network_requests: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._capturing = False
        self._capture_pattern: str = "**/*"
        self._init_scripts: list[str] = []

    async def launch(self, config: dict | None = None) -> dict:
        """Launch the Camoufox browser with the given or default config."""
        if self.browser is not None:
            return {"status": "already_running"}

        from camoufox.async_api import AsyncCamoufox

        cfg = {**self.default_config, **(config or {})}

        kwargs: dict[str, Any] = {}

        if cfg.get("proxy"):
            kwargs["proxy"] = cfg["proxy"]

        os_type = cfg.get("os", "auto")
        host_os = detect_host_os()
        if os_type == "auto":
            os_type = host_os
        kwargs["os"] = os_type

        if cfg.get("humanize"):
            kwargs["humanize"] = True
        if cfg.get("geoip"):
            kwargs["geoip"] = True
        if cfg.get("block_images"):
            kwargs["block_images"] = True
        if cfg.get("block_webrtc"):
            kwargs["block_webrtc"] = True

        locale = cfg.get("locale", "auto")
        if locale == "auto":
            locale = detect_system_locale()
        kwargs["locale"] = locale

        headless = cfg.get("headless", False)
        kwargs["headless"] = headless

        self._cm = AsyncCamoufox(**kwargs)
        self.browser = await self._cm.__aenter__()

        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.contexts["default"] = ctx

        if os_type != host_os:
            from .utils.js_helpers import get_font_fallback_script
            await ctx.add_init_script(get_font_fallback_script())

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        self._attach_listeners(page)
        self.pages["default"] = page
        self.active_page_name = "default"

        return {
            "status": "launched",
            "headless": headless,
            "os": os_type,
            "locale": locale,
            "pages": list(self.pages.keys()),
        }

    async def _ensure_browser(self) -> None:
        """Lazy-launch the browser if not already running."""
        if self.browser is None:
            await self.launch()

    def _attach_listeners(self, page: Page) -> None:
        """Attach console and network listeners to a page."""
        page.on("console", self._on_console)
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_console(self, msg) -> None:
        self._console_logs.append({
            "level": msg.type,
            "text": msg.text,
            "timestamp": int(time.time() * 1000),
            "location": str(msg.location) if hasattr(msg, "location") else None,
        })

    def _on_request(self, req) -> None:
        if not self._capturing:
            return
        import fnmatch
        if not fnmatch.fnmatch(req.url, self._capture_pattern):
            return
        entry = {
            "id": len(self._network_requests),
            "url": req.url,
            "method": req.method,
            "resource_type": req.resource_type,
            "request_headers": dict(req.headers),
            "request_post_data": req.post_data,
            "timestamp": int(time.time() * 1000),
            "status": None,
            "response_headers": None,
            "response_body": None,
            "duration": None,
        }
        self._network_requests.append(entry)

    def _on_response(self, resp) -> None:
        if not self._capturing:
            return
        for entry in reversed(self._network_requests):
            if entry["url"] == resp.url and entry["status"] is None:
                entry["status"] = resp.status
                entry["response_headers"] = dict(resp.headers)
                entry["duration"] = int(time.time() * 1000) - entry["timestamp"]
                break

    async def create_context(self, name: str, cookies: list[dict] | None = None) -> dict:
        """Create a new isolated browser context with optional cookies."""
        await self._ensure_browser()
        ctx = await self.browser.new_context()
        if cookies:
            await ctx.add_cookies(cookies)
        self.contexts[name] = ctx
        page = await ctx.new_page()
        self._attach_listeners(page)
        self.pages[name] = page
        self.active_page_name = name
        return {"status": "created", "context": name}

    async def get_active_page(self) -> Page:
        """Get the currently active page, launching the browser if needed."""
        await self._ensure_browser()
        if self.active_page_name and self.active_page_name in self.pages:
            return self.pages[self.active_page_name]
        raise RuntimeError("No active page available. Call launch_browser first.")

    async def close(self) -> dict:
        """Close the browser and clean up all resources."""
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.browser = None
        self.contexts.clear()
        self.pages.clear()
        self.active_page_name = None
        self._cm = None
        self._console_logs.clear()
        self._network_requests.clear()
        self._capturing = False
        self._init_scripts.clear()
        return {"status": "closed"}
