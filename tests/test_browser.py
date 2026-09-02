import pytest
from camoufox_reverse_mcp.browser import BrowserManager, detect_system_locale


def test_browser_manager_init():
    mgr = BrowserManager()
    assert mgr.browser is None
    assert mgr.active_page_name is None
    assert len(mgr.contexts) == 0
    assert len(mgr.pages) == 0
    assert mgr._capturing is False


def test_default_config():
    assert isinstance(BrowserManager.default_config, dict)


def test_console_logs_maxlen():
    mgr = BrowserManager()
    assert mgr._console_logs.maxlen == 2000


def test_network_requests_maxlen():
    mgr = BrowserManager()
    assert mgr._network_requests.maxlen == 2000


def test_persistent_scripts_init():
    mgr = BrowserManager()
    assert isinstance(mgr._persistent_scripts, list)
    assert len(mgr._persistent_scripts) == 0


def test_persistent_traces_init():
    mgr = BrowserManager()
    assert isinstance(mgr._persistent_traces, dict)
    assert len(mgr._persistent_traces) == 0


def test_capture_body_default():
    mgr = BrowserManager()
    assert mgr._capture_body is False


def test_init_scripts_list():
    mgr = BrowserManager()
    assert isinstance(mgr._init_scripts, list)
    assert len(mgr._init_scripts) == 0


def test_detect_system_locale_ignores_c_utf8(monkeypatch):
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.delenv("LC_MESSAGES", raising=False)

    assert detect_system_locale() == "en-US"


def test_detect_system_locale_normalizes_language_region(monkeypatch):
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)

    assert detect_system_locale() == "zh-CN"
