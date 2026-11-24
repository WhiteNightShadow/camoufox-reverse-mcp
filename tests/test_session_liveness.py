"""Tests for stale-session detection and RDP tab polling (no browser needed)."""
from __future__ import annotations

import asyncio

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.debugger_rdp_tools import FireFoxDebugger, RdpError


class _Page:
    def __init__(self, closed: bool = False):
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class _Browser:
    def __init__(self, connected: bool = True):
        self._connected = connected
        self.closed = False

    def is_connected(self) -> bool:
        return self._connected

    async def close(self):
        self.closed = True


def test_alive_session_is_usable():
    mgr = BrowserManager()
    mgr.browser = _Browser(connected=True)
    mgr.pages = {"default": _Page()}
    assert asyncio.run(mgr._session_is_usable()) is True


def test_disconnected_browser_is_dead():
    mgr = BrowserManager()
    mgr.browser = _Browser(connected=False)
    mgr.pages = {"default": _Page()}
    assert asyncio.run(mgr._session_is_usable()) is False


def test_all_pages_closed_is_dead():
    mgr = BrowserManager()
    mgr.browser = _Browser(connected=True)
    mgr.pages = {"default": _Page(closed=True)}
    assert asyncio.run(mgr._session_is_usable()) is False


def test_untracked_pages_are_not_called_dead():
    mgr = BrowserManager()
    mgr.browser = _Browser(connected=True)
    mgr.pages = {}
    assert asyncio.run(mgr._session_is_usable()) is True


def test_objects_without_liveness_methods_count_as_alive():
    """Fakes/older Playwright must not be treated as a dead session."""

    class _Bare:
        pass

    class _BarePage:
        pass

    mgr = BrowserManager()
    mgr.browser = _Bare()
    mgr.pages = {"default": _BarePage()}
    assert asyncio.run(mgr._session_is_usable()) is True


def test_get_active_page_rejects_closed_page():
    mgr = BrowserManager()

    async def _ensure():
        return None

    mgr._ensure_browser = _ensure  # type: ignore[assignment]
    mgr.active_page_name = "default"
    mgr.pages = {"default": _Page(closed=True)}
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(mgr.get_active_page())
    assert "dead session" in str(exc.value)


class _FakeRdp:
    def __init__(self, tabs_sequence):
        self._tabs = list(tabs_sequence)
        self.calls = 0

    def call(self, request, timeout):
        self.calls += 1
        tabs = self._tabs.pop(0) if self._tabs else []
        return {"tabs": tabs}


def test_wait_for_tabs_polls_until_a_tab_appears():
    dbg = object.__new__(FireFoxDebugger)
    rdp = _FakeRdp([[], [], [{"actor": "tab1", "url": "http://x/", "title": "x"}]])
    tabs = dbg._wait_for_tabs(rdp, timeout=2.0)
    assert tabs and tabs[0]["actor"] == "tab1"
    assert rdp.calls >= 3


def test_wait_for_tabs_returns_empty_after_timeout():
    dbg = object.__new__(FireFoxDebugger)
    rdp = _FakeRdp([])
    assert dbg._wait_for_tabs(rdp, timeout=0.2) == []


def test_wait_for_tabs_survives_rdp_errors():
    class _Boom:
        def call(self, request, timeout):
            raise RdpError("boom")

    dbg = object.__new__(FireFoxDebugger)
    assert dbg._wait_for_tabs(_Boom(), timeout=0.1) == []


class _NoTabsRdp:
    def call(self, request, timeout):
        return {"tabs": []}


class _OneTabRdp:
    def call(self, request, timeout):
        return {"tabs": [{"actor": "t1", "url": "http://x/", "title": "x"}]}


def _debugger_with(rdp):
    import threading

    dbg = object.__new__(FireFoxDebugger)
    dbg.rdp = rdp
    dbg.port = 1234
    dbg._cancel = threading.Event()
    return dbg


def test_wait_first_page_raises_when_no_tab_appears():
    dbg = _debugger_with(_NoTabsRdp())
    with pytest.raises(RdpError) as exc:
        dbg._wait_first_page(0.2)
    assert "no usable tab" in str(exc.value)


def test_wait_first_page_returns_when_a_tab_exists():
    dbg = _debugger_with(_OneTabRdp())
    dbg._wait_first_page(0.2)  # must not raise


def test_active_page_fallback_repairs_closed_active_tab():
    mgr = BrowserManager()

    async def _ensure():
        return None

    mgr._ensure_browser = _ensure  # type: ignore[assignment]
    open_page = _Page(closed=False)
    mgr.pages = {"default": open_page, "secondary": _Page(closed=True)}
    mgr.active_page_name = "secondary"
    mgr.browser = _Browser(connected=True)

    assert asyncio.run(mgr._session_is_usable()) is True
    resolved = asyncio.run(mgr.get_active_page())
    assert resolved is open_page
    assert mgr.active_page_name == "default"
    assert "secondary" not in mgr.pages


def test_enable_uses_wait_for_tabs_and_respects_cancel():
    import threading

    class _EnableRdp:
        def __init__(self):
            self.list_calls = 0

        def call(self, request, timeout):
            t = request.get("type")
            if t == "listTabs":
                self.list_calls += 1
                if self.list_calls < 3:
                    return {"tabs": []}
                return {"tabs": [{"actor": "tabA", "url": "https://example.test/", "title": "t"}]}
            if t == "getTarget":
                return {"frame": {"actor": "frameA", "consoleActor": "conA", "threadActor": "thA"}}
            if t == "attach":
                return {"type": "paused"}
            if t == "isAttached":
                return {"value": True}
            if t == "resume":
                return {}
            return {}

        def mark(self):
            return 0

        def wait_event(self, pred, timeout=0):
            return None

    dbg = FireFoxDebugger()
    dbg.rdp = _EnableRdp()  # type: ignore[assignment]
    info = dbg.enable()
    assert info["tab"] == "tabA"
    assert dbg.rdp.list_calls == 3

    cancelled_dbg = object.__new__(FireFoxDebugger)
    cancelled_dbg._cancel = threading.Event()
    cancelled_dbg._cancel.set()
    with pytest.raises(RdpError, match="cancelled"):
        cancelled_dbg._wait_for_tabs(_FakeRdp([[]]), timeout=1.0)
