"""
Regression tests for v1.0.1 evaluate_js fixes.

Bug 5: undefined/null return values no longer crash with JSON.parse error.
Bug 6: friendlier error hints for common failure modes.

These tests verify the Python-side logic without requiring a real browser,
using mocks for page.evaluate().
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from camoufox_reverse_mcp.tools.debugging import evaluate_js, _build_error_response


# ============= Bug 5 regression tests (undefined handling) =============


@pytest.mark.asyncio
async def test_undefined_returns_primitive_none():
    """Direct undefined return does not crash with JSON.parse error."""
    mock_page = AsyncMock()
    # Simulate what the new JS wrapper returns for `undefined`
    mock_page.evaluate = AsyncMock(return_value={
        "result": None, "type": "undefined", "is_undefined": True
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("undefined")

    assert r["type"] == "primitive"
    assert r["value"] is None
    assert r.get("value_raw") == "undefined"
    assert r["warnings"] is not None
    assert any("undefined" in w.lower() for w in r["warnings"])


@pytest.mark.asyncio
async def test_void_0_returns_primitive_none():
    """void 0 returns None without crash."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": None, "type": "undefined", "is_undefined": True
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("void 0")

    assert r["type"] == "primitive"
    assert r["value"] is None


@pytest.mark.asyncio
async def test_null_returns_primitive_none_no_warning():
    """null returns None without undefined warning."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": None, "type": "object", "is_undefined": False
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("null")

    assert r["type"] == "primitive"
    assert r["value"] is None
    assert r["warnings"] is None  # null is intentional, no warning


@pytest.mark.asyncio
async def test_iife_without_return_not_crash():
    """IIFE without return yields undefined, does not crash."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": None, "type": "undefined", "is_undefined": True
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("(() => { const x = 1; })()")

    assert r["type"] == "primitive"
    assert r["value"] is None


@pytest.mark.asyncio
async def test_symbol_returns_none_with_description():
    """Symbol returns None with symbol description in value_raw."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": None, "type": "symbol", "symbol_desc": "Symbol(test)"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("Symbol('test')")

    assert r["type"] == "primitive"
    assert r["value"] is None
    assert r["value_raw"] == "Symbol(test)"
    assert any("Symbol" in w for w in r["warnings"])


# ============= Previously passing cases (no regression) =============


@pytest.mark.asyncio
async def test_number_still_works():
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": 42, "type": "number"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("42")

    assert r["type"] == "primitive"
    assert r["value"] == 42


@pytest.mark.asyncio
async def test_boolean_still_works():
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": True, "type": "boolean"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("true")

    assert r["type"] == "primitive"
    assert r["value"] is True


@pytest.mark.asyncio
async def test_string_still_works():
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": "hello", "type": "string"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("'hello'")

    assert r["type"] == "primitive"
    assert r["value"] == "hello"


@pytest.mark.asyncio
async def test_large_string_still_works():
    """10000 char string does not crash."""
    big_str = "x" * 10000
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": big_str, "type": "string"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("'x'.repeat(10000)")

    assert r["type"] == "primitive"
    assert isinstance(r["value"], str)
    assert len(r["value"]) == 10000


@pytest.mark.asyncio
async def test_object_still_works():
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": {"a": 1, "b": "hello", "c": True}, "type": "object"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("({a: 1, b: 'hello', c: true})")

    assert r["type"] == "json"
    assert r["value"] == {"a": 1, "b": "hello", "c": True}


@pytest.mark.asyncio
async def test_array_still_works():
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": [1, 2, 3], "type": "object"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("[1, 2, 3]")

    assert r["type"] == "json"
    assert r["value"] == [1, 2, 3]


# ============= Bug 6 error hint tests =============


@pytest.mark.asyncio
async def test_toplevel_var_gives_hint():
    """Top-level var error includes IIFE hint."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(
        side_effect=Exception("Page.evaluate: expected expression, got keyword 'var'")
    )

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("var x = 1; x")

    assert r["type"] == "error"
    assert r.get("hint") is not None
    assert "IIFE" in r["hint"]


@pytest.mark.asyncio
async def test_timeout_error_gives_hint():
    """Timeout error includes await_promise hint."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(
        side_effect=Exception("Timeout 30000ms exceeded")
    )

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("new Promise(() => {})")

    assert r["type"] == "error"
    assert r.get("hint") is not None
    assert "timeout" in r["hint"].lower() or "await_promise" in r["hint"]


@pytest.mark.asyncio
async def test_page_closed_gives_hint():
    """Page closed error includes launch_browser hint."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(
        side_effect=Exception("Target closed")
    )

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("1+1")

    assert r["type"] == "error"
    assert r.get("hint") is not None
    assert "launch_browser" in r["hint"]


@pytest.mark.asyncio
async def test_js_error_returns_error_with_hint_field():
    """JS throw returns error type with hint field present (may be None)."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "error": "custom error", "type": "error"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("throw new Error('custom')")

    assert r["type"] == "error"
    assert "hint" in r  # field must exist
    # hint may be None for unknown errors, that's fine


# ============= _build_error_response unit tests =============


def test_build_error_response_var_keyword():
    r = _build_error_response("expected expression, got keyword 'var'")
    assert r["type"] == "error"
    assert "IIFE" in r["hint"]


def test_build_error_response_json_parse():
    r = _build_error_response("JSON.parse: unexpected character at line 1 column 1")
    assert r["hint"] is not None
    assert "serializable" in r["hint"].lower()


def test_build_error_response_timeout():
    r = _build_error_response("Timeout 30000ms exceeded")
    assert r["hint"] is not None
    assert "timed out" in r["hint"].lower() or "await_promise" in r["hint"]


def test_build_error_response_target_closed():
    r = _build_error_response("Target closed")
    assert r["hint"] is not None
    assert "launch_browser" in r["hint"]


def test_build_error_response_unknown():
    r = _build_error_response("some random error nobody expects")
    assert r["type"] == "error"
    assert r["hint"] is None


# ============= Serialization warning passthrough =============


@pytest.mark.asyncio
async def test_serialization_warning_passthrough():
    """When JS side falls back to String(r), warning is passed through."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={
        "result": "[object HTMLElement]", "type": "object",
        "serialization_warning": "Converting circular structure to JSON"
    })

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager") as mock_bm:
        mock_bm.get_active_page = AsyncMock(return_value=mock_page)
        r = await evaluate_js("document.body")

    assert r["type"] == "primitive"
    assert r["value"] == "[object HTMLElement]"
    assert r["warnings"] is not None
    assert any("serialization" in w.lower() for w in r["warnings"])
