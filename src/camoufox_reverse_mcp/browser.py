from __future__ import annotations

import asyncio
import os as _os
import platform
import time
from collections import deque
from typing import Any

from playwright.async_api import BrowserContext, Page

MAX_LOG_SIZE = 2000
MAX_BODY_SIZE = 200_000
MAX_TRACE_PATHS = 128
MAX_TRACE_EVENTS = 2000
MAX_TRACE_MESSAGE_SIZE = 32_000


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
        if val:
            locale = val.split(".", 1)[0]
            if locale not in ("C", "POSIX"):
                return locale.replace("_", "-")
    return "en-US"


class BrowserManager:
    """Manages the Camoufox browser lifecycle, contexts, and pages."""

    default_config: dict[str, Any] = {}

    def __init__(self) -> None:
        self.browser = None
        self.contexts: dict[str, BrowserContext] = {}
        self.pages: dict[str, Page] = {}
        self.active_page_name: str | None = None
        self._cm = None  # AsyncCamoufox context manager (owned-launch mode)
        self._pw = None  # async_playwright instance (connect/attach mode)
        self._connected = False  # True when attached to an external Camoufox server
        self._console_logs: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._network_requests: deque[dict] = deque(maxlen=MAX_LOG_SIZE)
        self._request_id_counter = 0
        self._capturing = False
        self._capture_pattern: str = "**/*"
        self._capture_body = False
        self._init_scripts: list[str] = []
        self._persistent_scripts: list[dict] = []
        self._persistent_traces: dict[str, deque[dict]] = {}
        self._persistent_trace_order: deque[tuple[str, dict]] = deque()
        self._nav_responses: list[dict] = []  # 最近一次 navigate 记录到的响应链路
        self._route_handlers: dict[str, Any] = {}  # 已注册的 route handler 映射
        self._runtime_browser: dict[str, Any] | None = None
        self._trace_base_dir = None
        self._trace_max_events = 100000
        self._trace_objects: list[str] = []
        self._trace_started_at: float | None = None
        self._trace_action_lock = asyncio.Lock()

    async def launch(self, config: dict | None = None) -> dict:
        """Launch the Camoufox browser with the given or default config."""
        if self.browser is not None:
            pages_info = {}
            for name, p in self.pages.items():
                try:
                    pages_info[name] = p.url
                except Exception:
                    pages_info[name] = "unknown"
            result = {
                "status": "already_running",
                "active_page": self.active_page_name,
                "pages": pages_info,
                "contexts": list(self.contexts.keys()),
                "capturing": self._capturing,
            }
            if self._runtime_browser is not None:
                result["browser_runtime"] = self._runtime_browser
            return result

        cfg = {**self.default_config, **(config or {})}

        # Attach mode: connect to an already-running Camoufox Playwright server
        # (started via `python -m camoufox server`, which prints a ws:// endpoint)
        # instead of launching a new browser. Fingerprint spoofing stays intact
        # because the server itself was launched through Camoufox's launch_options.
        ws_endpoint = cfg.get("ws_endpoint")
        if ws_endpoint:
            return await self._connect(ws_endpoint)

        from camoufox.async_api import AsyncCamoufox

        kwargs: dict[str, Any] = {}
        runtime_browser: dict[str, Any] | None = None

        browser_version = cfg.get("browser_version")
        if browser_version:
            from .camoufox_runtime import launch_overrides

            overrides, runtime_browser = launch_overrides(str(browser_version))
            kwargs.update(overrides)

        # Camoufox 0.4.5+ exposes an opt-in ``mw:`` Playwright evaluate prefix.
        # Keep the declared 0.4.0 dependency usable by feature-detecting the
        # launch option; older builds use the explicit wrappedJSObject fallback.
        main_world_eval_supported = False
        try:
            import inspect

            from camoufox.utils import launch_options as _capability_launch_options

            main_world_eval_supported = (
                "main_world_eval"
                in inspect.signature(_capability_launch_options).parameters
            )
        except Exception:
            pass
        if main_world_eval_supported:
            # Opening the channel does not change ordinary evaluate() semantics;
            # each tool must still explicitly request world="main".
            kwargs["main_world_eval"] = True

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

        # Property trace support. Explicit official/multiversion builds fail
        # closed without changing the normal launch or browser sandbox.
        enable_trace = bool(cfg.get("enable_trace", False))
        trace_warnings: list[str] = []
        trace_base_dir = None
        trace_runtime = runtime_browser
        if enable_trace and trace_runtime is None:
            try:
                from .camoufox_runtime import inspect_camoufox_runtime

                trace_runtime = inspect_camoufox_runtime().get("active")
            except Exception:
                trace_runtime = None
        if enable_trace and trace_runtime is not None:
            marker = trace_runtime.get("capabilities_marker")
            known_official = str(trace_runtime.get("repo") or "").lower() == "official"
            if (marker or known_official) and not trace_runtime.get("property_trace", False):
                enable_trace = False
                trace_warnings.append(
                    "enable_trace was ignored because the selected browser capability "
                    "marker does not declare PropertyTracer support."
                )
            elif (
                trace_runtime.get("property_trace_protocol") is not None
                and not trace_runtime.get("property_trace_compatible", False)
            ):
                enable_trace = False
                trace_warnings.append(
                    "enable_trace was ignored because the selected browser uses an "
                    "unsupported PropertyTracer protocol."
                )

        if enable_trace:
            from camoufox.utils import launch_options as _cfx_launch_options

            from .camou_config import merge_camou_config_env
            from .property_trace import (
                build_property_trace_config,
                cleanup_old_runs,
                cleanup_old_traces,
                create_trace_run,
            )
            cleanup_old_traces(keep_days=7)
            cleanup_old_runs(keep_days=7)
            trace_objects = cfg.get("trace_objects") or []
            if not isinstance(trace_objects, list) or not all(
                isinstance(item, str) and item.strip() for item in trace_objects
            ):
                raise ValueError("trace_objects must be a list of non-empty object names")
            trace_objects = [item.strip() for item in trace_objects]
            trace_max_events = int(cfg.get("trace_max_events", 100000))
            if not 1 <= trace_max_events <= 200_000:
                raise ValueError("trace_max_events must be between 1 and 200000")
            trace_base_dir = create_trace_run()
            trace_config = build_property_trace_config(
                trace_base_dir,
                objects=trace_objects,
                max_events=trace_max_events,
            )

            # Build from_options ourselves, then inject propertyTrace
            from_options = _cfx_launch_options(headless=headless, **{
                k: v for k, v in kwargs.items() if k != "headless"
            })
            env = merge_camou_config_env(
                from_options.get("env", {}),
                {"propertyTrace": trace_config},
            )
            env["MOZ_DISABLE_CONTENT_SANDBOX"] = "1"
            from_options["env"] = env

            # Pass pre-built from_options to skip launch_options() call
            kwargs["from_options"] = from_options

        self._cm = AsyncCamoufox(**kwargs)
        try:
            self.browser = await self._cm.__aenter__()
        except Exception:
            if trace_base_dir is not None:
                import shutil

                shutil.rmtree(trace_base_dir, ignore_errors=True)
            self._cm = None
            raise
        self._runtime_browser = trace_runtime if trace_base_dir is not None else runtime_browser
        self._trace_base_dir = trace_base_dir
        if trace_base_dir is not None:
            self._trace_max_events = trace_max_events
            self._trace_objects = trace_objects

        ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.contexts["default"] = ctx

        if os_type != host_os:
            from .utils.js_helpers import get_font_fallback_script
            await ctx.add_init_script(get_font_fallback_script())

        for script_info in self._persistent_scripts:
            await ctx.add_init_script(script=script_info["content"])

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        self._attach_listeners(page)
        self.pages["default"] = page
        self.active_page_name = "default"

        result = {
            "status": "launched",
            "headless": headless,
            "os": os_type,
            "locale": locale,
            "pages": list(self.pages.keys()),
            "main_world_eval": main_world_eval_supported,
        }
        if runtime_browser is not None:
            result["browser_runtime"] = runtime_browser
        result["engine_trace"] = {
            "requested": bool(cfg.get("enable_trace", False)),
            "enabled": trace_base_dir is not None,
            "run_dir": str(trace_base_dir) if trace_base_dir is not None else None,
            "objects": self._trace_objects if trace_base_dir is not None else [],
            "max_events_per_process": (
                self._trace_max_events if trace_base_dir is not None else None
            ),
        }
        if trace_base_dir is not None:
            trace_warnings.append(
                "Engine trace temporarily disables the Firefox content sandbox so "
                "content processes can write the private trace run; use it only for "
                "short analysis sessions."
            )
        if trace_warnings:
            result["warnings"] = trace_warnings
        return result

    async def _connect(self, ws_endpoint: str) -> dict:
        """Attach to an already-running Camoufox Playwright server via its ws:// endpoint.

        The server is started externally with `python -m camoufox server`, which prints
        a `Websocket endpoint: ws://127.0.0.1:<port>/<guid>` line. Pass that full URL here.

        Note: fingerprint config (including os) belongs to the running server. Unlike
        owned launch(), this path cannot inject the host/os font-fallback shim, so the
        server should be started with an os fingerprint matching the host for parity.
        """
        from playwright.async_api import async_playwright

        async def _teardown():
            # Disconnect the local client only — never browser.close(), which would
            # kill the user's external server. Stopping the driver tears down the
            # client transport while the server keeps running.
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self.browser = None
            self._connected = False
            self.contexts.clear()
            self.pages.clear()
            self.active_page_name = None

        self._pw = await async_playwright().start()
        try:
            self.browser = await self._pw.firefox.connect(ws_endpoint)
        except Exception:
            # Handshake failed — nothing attached yet, just drop the driver.
            try:
                await self._pw.stop()
            finally:
                self._pw = None
            raise

        # From here the browser is live; any failure must disconnect cleanly so we
        # don't leak the driver+connection or wedge the next launch into already_running.
        try:
            ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
            self.contexts["default"] = ctx

            for script_info in self._persistent_scripts:
                await ctx.add_init_script(script=script_info["content"])

            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            self._attach_listeners(page)
            self.pages["default"] = page
            self.active_page_name = "default"
            self._connected = True  # only flip once fully wired
        except Exception:
            await _teardown()
            raise

        result = {
            "status": "connected",
            "ws_endpoint": ws_endpoint,
            "contexts": list(self.contexts.keys()),
            "pages": list(self.pages.keys()),
        }
        # Persistent scripts only run on the NEXT navigation; if we attached to a page
        # already on a real site, existing hooks aren't live yet — tell the caller.
        try:
            current = page.url
        except Exception:
            current = ""
        if current and current != "about:blank":
            result["warnings"] = [
                f"attached to a page already at {current}; persistent scripts and hooks "
                "apply on the next navigation — call reload() to activate them now."
            ]
        return result

    async def _ensure_browser(self) -> None:
        """Lazy-launch the browser if not already running."""
        if self.browser is None:
            await self.launch()

    async def add_persistent_script(self, name: str, content: str) -> None:
        """Register a script that persists across all navigations via context-level injection."""
        for s in self._persistent_scripts:
            if s["name"] == name:
                s["content"] = content
                break
        else:
            self._persistent_scripts.append({"name": name, "content": content})
        for ctx in self.contexts.values():
            await ctx.add_init_script(script=content)

    def remove_persistent_script(self, name: str) -> bool:
        """Remove a persistent script by name. Returns True if found."""
        before = len(self._persistent_scripts)
        self._persistent_scripts = [s for s in self._persistent_scripts if s["name"] != name]
        return len(self._persistent_scripts) < before

    def _attach_listeners(self, page: Page) -> None:
        """Attach console, network, and trace-collection listeners to a page."""
        page.on("console", self._on_console)
        page.on("request", self._on_request)
        page.on("response", self._on_response_async)
        page.on("response", self._on_response_for_nav)

    def _on_console(self, msg) -> None:
        text = msg.text
        if text and text.startswith("__MCP_TRACE__:"):
            try:
                import json

                if len(text) > MAX_TRACE_MESSAGE_SIZE:
                    return
                payload = json.loads(text[len("__MCP_TRACE__:"):])
                path = payload.pop("__path__", "unknown")
                if (
                    not isinstance(payload, dict)
                    or not isinstance(path, str)
                    or not path
                    or len(path) > 512
                ):
                    return
                if path not in self._persistent_traces:
                    if len(self._persistent_traces) >= MAX_TRACE_PATHS:
                        oldest_path = next(iter(self._persistent_traces))
                        self._persistent_traces.pop(oldest_path, None)
                        self._persistent_trace_order = deque(
                            (saved_path, entry)
                            for saved_path, entry in self._persistent_trace_order
                            if saved_path != oldest_path
                        )
                    self._persistent_traces[path] = deque()
                else:
                    # Keep active paths at the end so path-cap eviction is LRU-like.
                    cache = self._persistent_traces.pop(path)
                    self._persistent_traces[path] = cache
                self._persistent_traces[path].append(payload)
                self._persistent_trace_order.append((path, payload))
                while len(self._persistent_trace_order) > MAX_TRACE_EVENTS:
                    old_path, old_entry = self._persistent_trace_order.popleft()
                    cache = self._persistent_traces.get(old_path)
                    if cache and cache[0] is old_entry:
                        cache.popleft()
                        if not cache:
                            self._persistent_traces.pop(old_path, None)
            except Exception:
                pass
            return

        self._console_logs.append({
            "level": msg.type,
            "text": text,
            "timestamp": int(time.time() * 1000),
            "location": str(msg.location) if hasattr(msg, "location") else None,
        })

    def _on_request(self, req) -> None:
        if not self._capturing:
            return
        import fnmatch
        if not fnmatch.fnmatch(req.url, self._capture_pattern):
            return
        self._request_id_counter += 1
        entry = {
            "id": self._request_id_counter,
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

    def _on_response_async(self, resp) -> None:
        """Handle response events, optionally capturing body asynchronously."""
        if not self._capturing:
            return
        for entry in reversed(self._network_requests):
            if entry["url"] == resp.url and entry["status"] is None:
                entry["status"] = resp.status
                entry["response_headers"] = dict(resp.headers)
                entry["duration"] = int(time.time() * 1000) - entry["timestamp"]
                if self._capture_body:
                    asyncio.ensure_future(self._fetch_response_body(resp, entry))
                break

    async def _fetch_response_body(self, resp, entry: dict) -> None:
        """Asynchronously fetch and store the response body."""
        try:
            body_bytes = await resp.body()
            try:
                body_text = body_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = body_bytes.decode("latin-1")
            if len(body_text) > MAX_BODY_SIZE:
                entry["response_body"] = body_text[:MAX_BODY_SIZE]
                entry["response_body_truncated"] = True
                entry["response_body_total_size"] = len(body_text)
            else:
                entry["response_body"] = body_text
        except Exception:
            entry["response_body"] = None

    def _on_response_for_nav(self, resp) -> None:
        """Record every response during a navigation for final_status resolution."""
        try:
            self._nav_responses.append({
                "url": resp.url,
                "status": resp.status,
                "resource_type": getattr(resp.request, "resource_type", None) if resp.request else None,
                "ts": int(time.time() * 1000),
            })
            # Keep only the last 100
            if len(self._nav_responses) > 100:
                self._nav_responses = self._nav_responses[-100:]
        except Exception:
            pass

    def reset_nav_responses(self) -> None:
        self._nav_responses = []

    async def create_context(self, name: str, cookies: list[dict] | None = None) -> dict:
        """Create a new isolated browser context with optional cookies."""
        await self._ensure_browser()
        ctx = await self.browser.new_context()
        if cookies:
            await ctx.add_cookies(cookies)
        for script_info in self._persistent_scripts:
            await ctx.add_init_script(script=script_info["content"])
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
        """Close the browser and clean up all resources.

        Owned-launch mode: fully shuts the browser down.
        Attach mode (connected to an external server): only disconnects the local
        Playwright client — the user's browser and server are left running.
        """
        trace_base_dir = self._trace_base_dir
        if trace_base_dir is not None:
            try:
                from .property_trace import set_trace_state

                await set_trace_state(
                    "off",
                    trace_base_dir,
                    features=(self._runtime_browser or {}).get(
                        "property_trace_features", []
                    ),
                    timeout=2.0,
                )
            except Exception:
                pass
        if self._connected and self._pw is not None:
            # Disconnect without closing the remote browser: stopping the driver
            # tears down the client transport but leaves the external server alive.
            try:
                await self._pw.stop()
            except Exception:
                pass
        elif self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        stale_controls_removed = 0
        if trace_base_dir is not None:
            try:
                from .property_trace import cleanup_stale_controls

                stale_controls_removed = cleanup_stale_controls(trace_base_dir)
            except Exception:
                pass
        self.browser = None
        self.contexts.clear()
        self.pages.clear()
        self.active_page_name = None
        self._cm = None
        self._pw = None
        self._connected = False
        self._runtime_browser = None
        self._trace_base_dir = None
        self._trace_max_events = 100000
        self._trace_objects = []
        self._trace_started_at = None
        self._console_logs.clear()
        self._network_requests.clear()
        self._request_id_counter = 0
        self._capturing = False
        self._capture_body = False
        self._init_scripts.clear()
        self._persistent_scripts.clear()
        self._persistent_traces.clear()
        self._persistent_trace_order.clear()
        self._nav_responses.clear()
        self._route_handlers.clear()
        return {
            "status": "closed",
            "stale_trace_controls_removed": stale_controls_removed,
        }
