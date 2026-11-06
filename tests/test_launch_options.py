"""launch_browser must keep the server's startup options unless a call overrides them."""

import asyncio
import sys
import types

import pytest

from camoufox_reverse_mcp import __main__ as server_main
from camoufox_reverse_mcp import browser as browser_module
from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.server import mcp
from camoufox_reverse_mcp.tools import navigation

HOST_OS = "macos"
HOST_LOCALE = "fr-FR"
SWITCHES = ("humanize", "geoip", "block_images", "block_webrtc")


class FakePage:
    url = "about:blank"

    def on(self, *_args):
        pass


class FakeContext:
    pages = [FakePage()]

    def on(self, *_args):
        pass

    async def add_init_script(self, *_args, **_kwargs):
        pass


@pytest.fixture
def launched(monkeypatch):
    """Capture what would reach Camoufox, using the real launch and merge code."""
    captured = {}

    class FakeCamoufox:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

        async def __aenter__(self):
            return types.SimpleNamespace(contexts=[FakeContext()])

        async def __aexit__(self, *_args):
            pass

    async_api = types.ModuleType("camoufox.async_api")
    async_api.AsyncCamoufox = FakeCamoufox
    monkeypatch.setitem(sys.modules, "camoufox.async_api", async_api)
    monkeypatch.setattr(browser_module, "detect_host_os", lambda: HOST_OS)
    monkeypatch.setattr(browser_module, "detect_system_locale", lambda: HOST_LOCALE)
    monkeypatch.setattr(navigation, "browser_manager", BrowserManager())
    return captured


def start_server(monkeypatch, *flags):
    """Run the real CLI parsing, stopping before the server starts serving."""
    monkeypatch.setattr(BrowserManager, "default_config", {})
    monkeypatch.setattr(sys, "argv", ["camoufox-reverse-mcp", *flags])
    monkeypatch.setattr(server_main, "patch_playwright_pageerror", lambda: None)
    monkeypatch.setattr(server_main, "reserve_stdout_for_protocol", lambda: None)
    monkeypatch.setattr(server_main.mcp, "run", lambda **_kwargs: None)
    server_main.main()


ALL_FLAGS = (
    "--headless", "--os", "windows", "--locale", "en-US", "--proxy", "http://flag:1",
    "--geoip", "--humanize", "--block-images", "--block-webrtc",
)


async def test_omitted_arguments_keep_every_startup_option(monkeypatch, launched):
    start_server(monkeypatch, *ALL_FLAGS)

    result = await navigation.launch_browser()

    assert result["status"] == "launched"
    assert (result["headless"], result["os"], result["locale"]) == (True, "windows", "en-US")
    assert launched["headless"] is True
    assert launched["os"] == "windows"
    assert launched["locale"] == "en-US"
    assert launched["proxy"] == {"server": "http://flag:1"}
    assert all(launched[name] is True for name in SWITCHES)


@pytest.mark.parametrize(
    ("flags", "key", "expected"),
    [
        (("--headless",), "headless", True),
        (("--os", "linux"), "os", "linux"),
        (("--locale", "de-DE"), "locale", "de-DE"),
        (("--geoip",), "geoip", True),
        (("--humanize",), "humanize", True),
        (("--block-images",), "block_images", True),
        (("--block-webrtc",), "block_webrtc", True),
    ],
)
async def test_each_startup_option_reaches_the_browser(
    monkeypatch, launched, flags, key, expected
):
    start_server(monkeypatch, *flags)

    await navigation.launch_browser()

    assert launched[key] == expected


async def test_passed_arguments_win_over_startup_options(monkeypatch, launched):
    start_server(monkeypatch, *ALL_FLAGS)

    result = await navigation.launch_browser(
        headless=False,
        os_type="linux",
        locale="zh-CN",
        proxy="http://call:2",
        humanize=False,
        geoip=False,
        block_images=False,
        block_webrtc=False,
    )

    assert (result["headless"], result["os"], result["locale"]) == (False, "linux", "zh-CN")
    assert launched["proxy"] == {"server": "http://call:2"}
    assert not any(name in launched for name in SWITCHES)


async def test_passing_auto_still_overrides_a_startup_value(monkeypatch, launched):
    start_server(monkeypatch, "--os", "windows", "--locale", "en-US")

    await navigation.launch_browser(os_type="auto", locale="auto")

    assert (launched["os"], launched["locale"]) == (HOST_OS, HOST_LOCALE)


async def test_one_argument_leaves_the_other_startup_options_alone(monkeypatch, launched):
    start_server(monkeypatch, *ALL_FLAGS)

    await navigation.launch_browser(locale="zh-CN")

    assert launched["locale"] == "zh-CN"
    assert launched["headless"] is True
    assert launched["os"] == "windows"
    assert all(launched[name] is True for name in SWITCHES)


@pytest.mark.parametrize("configured", [True, False], ids=["no-flags", "library-use"])
async def test_without_startup_options_the_defaults_are_unchanged(
    monkeypatch, launched, configured
):
    if configured:
        start_server(monkeypatch)
    else:
        monkeypatch.setattr(BrowserManager, "default_config", {})

    result = await navigation.launch_browser()

    assert (result["headless"], result["os"], result["locale"]) == (False, HOST_OS, HOST_LOCALE)
    assert launched["headless"] is False
    assert "proxy" not in launched
    assert not any(name in launched for name in SWITCHES)


async def test_lazy_launch_and_launch_browser_start_the_same_browser(monkeypatch, launched):
    start_server(monkeypatch, *ALL_FLAGS)

    await navigation.launch_browser()
    explicit = dict(launched)
    monkeypatch.setattr(navigation, "browser_manager", BrowserManager())
    await navigation.browser_manager.get_active_page()

    assert launched == explicit


def test_schema_stays_strict_and_advertises_no_competing_default():
    tools = asyncio.run(mcp.list_tools())
    schema = next(tool for tool in tools if tool.name == "launch_browser").inputSchema
    properties = schema["properties"]

    assert not schema.get("required")
    expected = {
        "headless": "boolean",
        "os_type": "string",
        "locale": "string",
        "humanize": "boolean",
        "geoip": "boolean",
        "block_images": "boolean",
        "block_webrtc": "boolean",
    }
    for name, kind in expected.items():
        assert properties[name]["type"] == kind
        assert "default" not in properties[name]
        assert "anyOf" not in properties[name]


def test_omitted_and_null_arguments_validate_to_unset():
    tool = next(
        item for item in mcp._tool_manager.list_tools() if item.name == "launch_browser"
    )
    model = tool.fn_metadata.arg_model

    assert model.model_validate({}).headless is None
    assert model.model_validate({"headless": None, "locale": None}).locale is None
    assert model.model_validate({"headless": False}).headless is False
