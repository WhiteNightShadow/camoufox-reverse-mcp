import os
import pytest
from camoufox_reverse_mcp.utils.js_helpers import render_trace_template
from camoufox_reverse_mcp.utils.response_fmt import format_response, truncate_str


def test_render_trace_template():
    js = render_trace_template(
        function_path="window.encrypt",
        max_captures=10,
        log_args=True,
        log_return=False,
        log_stack=True,
    )
    assert "window.encrypt" in js
    assert "10" in js
    assert "true" in js


def test_render_trace_template_defaults():
    js = render_trace_template(function_path="JSON.stringify")
    assert "JSON.stringify" in js
    assert "50" in js


def test_format_response_dict():
    result = format_response({"key": "value"})
    assert '"key"' in result
    assert '"value"' in result


def test_format_response_truncation():
    data = "x" * 100000
    result = format_response(data, max_length=100)
    assert "truncated" in result


def test_truncate_str_short():
    assert truncate_str("hello", 10) == "hello"


def test_truncate_str_long():
    result = truncate_str("a" * 100, 50)
    assert len(result) < 100
    assert "chars total" in result


def test_hook_files_exist():
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    expected_files = [
        "xhr_hook.js",
        "fetch_hook.js",
        "crypto_hook.js",
        "websocket_hook.js",
        "debugger_trap.js",
        "trace_template.js",
    ]
    for f in expected_files:
        assert os.path.exists(os.path.join(hooks_dir, f)), f"Missing hook file: {f}"
