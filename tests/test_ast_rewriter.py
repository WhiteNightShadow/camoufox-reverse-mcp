import pytest
from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite, INSTRUMENT_RUNTIME


def test_member_access_rewrite():
    src = "var n = navigator; var ua = n['userAgent'];"
    out, stats = ast_rewrite(src, tag="test")
    assert out is not None
    assert stats["parsed"] is True
    assert stats["member_edits"] >= 1
    assert "__mcp_tap_get" in out


def test_method_call_rewrite():
    src = "var r = document.querySelector('a');"
    out, stats = ast_rewrite(src, tag="test")
    assert out is not None
    assert stats["method_edits"] >= 1
    assert "__mcp_tap_method(document" in out


def test_plain_call_rewrite():
    src = "function foo(x) { return x + 1; } var y = foo(5);"
    out, stats = ast_rewrite(src, tag="test")
    assert out is not None
    assert stats["call_edits"] >= 1
    assert "__mcp_tap_call(foo" in out


def test_assignment_target_skipped():
    src = "var arr = []; arr[0] = 1;"
    out, stats = ast_rewrite(src, tag="test")
    assert out is not None
    # LHS of assignment should not be wrapped
    assert "arr[0] = 1" in out or "arr[0]=1" in out or "arr[0] =1" in out


def test_parse_failure_returns_none():
    src = "this is not valid JS ))) {{{{"
    out, stats = ast_rewrite(src)
    assert out is None
    assert stats["parsed"] is False
    assert "error" in stats


def test_runtime_is_prepended():
    src = "var x = 1;"
    out, _ = ast_rewrite(src)
    assert out is not None
    assert "__mcp_tap_installed" in out


def test_tap_self_call_not_rewrapped():
    src = "__mcp_tap_get(navigator, 'userAgent', 'existing');"
    out, stats = ast_rewrite(src, tag="new")
    assert out is not None
    assert "__mcp_tap_call(__mcp_tap_get" not in out


def test_max_edits_cap():
    src = "; ".join(f"a.b{i}" for i in range(100))
    out, stats = ast_rewrite(src, tag="test", max_edits=5)
    assert out is not None
    assert stats["edits"] == 5


@pytest.mark.parametrize(
    "src",
    [
        "var result = new X().m1().m2();",
        "var args = Array.prototype.slice.call(arguments);",
    ],
)
def test_nested_method_chain_rewrite_remains_parseable(src):
    import esprima

    out, stats = ast_rewrite(src, tag="test")

    assert out is not None
    esprima.parseScript(out)
    assert stats["overlap_skipped"] >= 1
    assert stats["edits"] >= 1
