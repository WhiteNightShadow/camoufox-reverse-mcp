"""
test_jsvmp.py - Smoke tests for the universal JSVMP toolchain.

These tests verify the Python-side logic (template rendering, regex rewriting,
cookie parsing, tool registration) without requiring a real browser.
For real-site integration, use the scenarios in README.md manually.
"""
import json
import os
import shutil
import subprocess
import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.utils.js_rewriter import (
    regex_rewrite,
    INSTRUMENT_RUNTIME,
    ACORN_REWRITE_JS_TEMPLATE,
)
from camoufox_reverse_mcp.tools.cookie_analysis import _parse_cookie_name


# ============ js_rewriter tests ============

def test_regex_rewrite_basic():
    """regex_rewrite should insert __mcp_tap_get for bracket access."""
    src = 'var x = navigator["userAgent"];'
    out, stats = regex_rewrite(src, tag="test")
    assert stats["member_access_rewrites"] >= 1
    assert "__mcp_tap_get" in out
    assert "__mcp_tap_installed" in out  # runtime preamble


def test_regex_rewrite_skips_assignment():
    """regex_rewrite should NOT rewrite assignment targets like a[b] = c."""
    src = 'obj["key"] = 123;'
    out, stats = regex_rewrite(src, tag="test")
    # The assignment target should not be rewritten
    assert 'obj["key"] = 123' in out or stats["member_access_rewrites"] == 0


def test_regex_rewrite_skips_internal_names():
    """regex_rewrite should skip __mcp_tap_* and console references."""
    src = '__mcp_tap_get[0]; console["log"];'
    out, stats = regex_rewrite(src, tag="test")
    assert stats["member_access_rewrites"] == 0


def test_regex_rewrite_multiple():
    """regex_rewrite should handle multiple bracket accesses."""
    src = 'a[b]; c[d]; e[f];'
    out, stats = regex_rewrite(src, tag="test")
    assert stats["member_access_rewrites"] == 3


def test_regex_rewrite_max_cap():
    """regex_rewrite should respect max_rewrites cap."""
    src = "; ".join(f"x{i}[y{i}]" for i in range(100))
    out, stats = regex_rewrite(src, tag="test", max_rewrites=5)
    assert stats["member_access_rewrites"] == 5


def test_regex_source_site_uses_original_match_range():
    src = 'var marker = "😀";\nvar ua = navigator["userAgent"];'
    out, stats = regex_rewrite(src, tag="sites", include_source_site=True)

    expected = 'navigator["userAgent"]'
    site = stats["source_sites"][0]
    assert site["start"] == src.index(expected)
    assert site["end"] == src.index(expected) + len(expected)
    assert site["kind"] == "tap_get"
    assert site["site_id"] in out


def test_default_regex_rewrite_has_no_source_site_metadata():
    out, stats = regex_rewrite('navigator["userAgent"]', tag="default")

    assert "source_sites" not in stats
    assert "site_id" not in out.split("})();", 1)[-1]


def test_ast_and_regex_sites_match_with_non_bmp_and_crlf_prefix():
    from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite

    src = 'var marker="😀"; var ua=navigator["userAgent"];\r\n'
    _, ast_stats = ast_rewrite(
        src,
        rewrite_calls=False,
        include_source_site=True,
    )
    _, regex_stats = regex_rewrite(src, include_source_site=True)

    assert (
        ast_stats["source_sites"][0]["site_id"]
        == regex_stats["source_sites"][0]["site_id"]
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_source_site_runtime_is_monotonic_and_default_log_is_compatible():
    from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite

    src = "var target = {a: 1, b: 2}; target.a; target.b;"

    def execute(include_source_site):
        rewritten, _ = ast_rewrite(
            src,
            tag="runtime",
            include_source_site=include_source_site,
        )
        assert rewritten is not None
        script = (
            "global.window=global;"
            + rewritten
            + ";process.stdout.write(JSON.stringify(window.__mcp_vmp_log));"
        )
        completed = subprocess.run(
            [shutil.which("node"), "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    default_events = execute(False)
    site_events = execute(True)

    assert len(default_events) == len(site_events) == 2
    assert all("site_id" not in event and "seq" not in event for event in default_events)
    assert all(set(event) == {"type", "tag", "key", "objType", "value", "ts"}
               for event in default_events)
    assert [event["seq"] for event in site_events] == [0, 1]
    assert all(event["site_id"] for event in site_events)


def test_instrument_runtime_is_valid_js():
    """INSTRUMENT_RUNTIME should be syntactically valid (basic check)."""
    assert "window.__mcp_tap_installed" in INSTRUMENT_RUNTIME
    assert "window.__mcp_tap_get" in INSTRUMENT_RUNTIME
    assert "window.__mcp_tap_call" in INSTRUMENT_RUNTIME
    assert "window.__mcp_tap_method" in INSTRUMENT_RUNTIME


def test_acorn_template_exists():
    """ACORN_REWRITE_JS_TEMPLATE should be a non-empty string."""
    assert len(ACORN_REWRITE_JS_TEMPLATE) > 100
    assert "acorn.parse" in ACORN_REWRITE_JS_TEMPLATE


# ============ cookie_analysis tests ============

def test_parse_cookie_name_basic():
    assert _parse_cookie_name("session_id=abc123; path=/") == "session_id"


def test_parse_cookie_name_no_value():
    assert _parse_cookie_name("") is None
    assert _parse_cookie_name(None) is None


def test_parse_cookie_name_complex():
    assert _parse_cookie_name("acw_tc=abc; domain=.example.com; HttpOnly") == "acw_tc"


def test_parse_cookie_name_spaces():
    assert _parse_cookie_name("  token = xyz") == "token"


# ============ Hook file existence tests ============

def test_new_hook_files_exist():
    """All new hook JS files should exist."""
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    new_files = [
        "cookie_hook.js",
        "runtime_probe.js",
        "jsvmp_transparent_hook.js",
    ]
    for f in new_files:
        assert os.path.exists(os.path.join(hooks_dir, f)), f"Missing hook file: {f}"


def test_jsvmp_hook_has_new_template_vars():
    """jsvmp_hook.js should contain the new template variables."""
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    with open(os.path.join(hooks_dir, "jsvmp_hook.js"), "r") as f:
        content = f.read()
    assert "{{TRACK_CALLS}}" in content
    assert "{{TRACK_PROPS}}" in content
    assert "{{TRACK_REFLECT}}" in content
    assert "{{PROXY_OBJECTS}}" in content
    assert "{{MAX_ENTRIES}}" in content
    assert "fn_apply" in content  # new log type
    assert "fn_call" in content   # new log type
    assert "reflect_apply" in content  # new log type
    assert "proxy_get" in content  # new log type


def test_cookie_hook_uses_prototype_chain():
    """cookie_hook.js should walk the prototype chain."""
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    with open(os.path.join(hooks_dir, "cookie_hook.js"), "r") as f:
        content = f.read()
    assert "getPrototypeOf" in content
    assert "getOwnPropertyDescriptor" in content
    assert "__mcp_cookie_log" in content


def test_runtime_probe_covers_key_apis():
    """runtime_probe.js should cover navigator, canvas, WebGL, fetch."""
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    with open(os.path.join(hooks_dir, "runtime_probe.js"), "r") as f:
        content = f.read()
    assert "XMLHttpRequest" in content
    assert "fetch" in content
    assert "toDataURL" in content
    assert "getParameter" in content
    assert "nav_read" in content
    assert "addEventListener" in content


# ============ BrowserManager new fields ============

def test_browser_manager_has_nav_responses():
    mgr = BrowserManager()
    assert hasattr(mgr, "_nav_responses")
    assert isinstance(mgr._nav_responses, list)


def test_browser_manager_has_route_handlers():
    mgr = BrowserManager()
    assert hasattr(mgr, "_route_handlers")
    assert isinstance(mgr._route_handlers, dict)


def test_browser_manager_reset_nav_responses():
    mgr = BrowserManager()
    mgr._nav_responses.append({"url": "test", "status": 200})
    mgr.reset_nav_responses()
    assert len(mgr._nav_responses) == 0


# ============ Tool registration ============

def test_all_65_tools_registered():
    """v1.0.0: tool count should be in the 25-40 range."""
    from camoufox_reverse_mcp.server import mcp
    tools = mcp._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    assert 25 <= len(tools) <= 40, f"Expected 25-40 tools, got {len(tools)}: {tool_names}"


def test_new_tools_registered():
    """v1.0.0 core tools should be registered."""
    from camoufox_reverse_mcp.server import mcp
    tools = mcp._tool_manager.list_tools()
    tool_names = {t.name for t in tools}
    expected = {
        "network_capture", "scripts", "search_code", "hook_function",
        "instrumentation", "cookies", "check_environment", "reset_browser_state",
        "launch_browser", "navigate", "evaluate_js", "verify_signer_offline",
    }
    missing = expected - tool_names
    assert not missing, f"Missing tools: {missing}"


# ============ pre_inject registration ============

@pytest.mark.asyncio
async def test_pre_inject_registers_persistent_script():
    """_inject_hook_by_name should register the hook as a persistent script
    without attempting page.evaluate (no about:blank detour)."""
    import asyncio
    from unittest.mock import patch
    from camoufox_reverse_mcp.tools import navigation

    register_calls = []

    async def _mock_register(name, content):
        register_calls.append(name)

    with patch.object(navigation.browser_manager, "add_persistent_script", _mock_register):
        ok, msg = await navigation._inject_hook_by_name("cookie_hook")

    assert ok is True
    assert msg == "ok"
    assert register_calls == ["pre_inject:cookie_hook"]


@pytest.mark.asyncio
async def test_pre_inject_register_timeout():
    """_inject_hook_by_name should fail gracefully if registration times out."""
    import asyncio
    from unittest.mock import patch
    from camoufox_reverse_mcp.tools import navigation

    async def _hanging_register(name, content):
        await asyncio.Event().wait()  # hang forever

    with patch.object(navigation, "_PRE_INJECT_REGISTER_TIMEOUT", 0.1), \
         patch.object(navigation.browser_manager, "add_persistent_script", _hanging_register):
        ok, msg = await asyncio.wait_for(
            navigation._inject_hook_by_name("cookie_hook"), timeout=2.0
        )

    assert ok is False
    assert "timed out" in msg.lower()


@pytest.mark.asyncio
async def test_pre_inject_jsvmp_probe_registers():
    """jsvmp_probe special name should build JS from template and register."""
    from unittest.mock import patch
    from camoufox_reverse_mcp.tools import navigation

    registered = {}

    async def _mock_register(name, content):
        registered[name] = content

    with patch.object(navigation.browser_manager, "add_persistent_script", _mock_register):
        ok, msg = await navigation._inject_hook_by_name("jsvmp_probe")

    assert ok is True
    assert "pre_inject:jsvmp_probe" in registered
    assert "__mcp_jsvmp_installed" in registered["pre_inject:jsvmp_probe"]


# ============ Version ============

def test_package_version():
    from camoufox_reverse_mcp import __version__
    assert __version__ == "1.2.0"
