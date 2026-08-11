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


def test_source_sites_are_opt_in_and_use_original_unicode_offsets():
    import esprima

    src = 'var marker = "😀";\nvar ua = navigator.userAgent; foo(ua);'
    out, stats = ast_rewrite(src, tag="sites", include_source_site=True)

    assert out is not None
    esprima.parseScript(out)
    assert len(stats["source_id"]) == 16
    assert len(stats["source_sha256"]) == 64
    assert len(stats["source_sites"]) == stats["edits"]

    member = next(s for s in stats["source_sites"] if s["kind"] == "tap_get")
    expected = "navigator.userAgent"
    assert member["start"] == src.index(expected)
    assert member["end"] == src.index(expected) + len(expected)
    assert member["line"] == 2
    assert member["column"] == src.splitlines()[1].index(expected)
    assert member["site_id"] in out


def test_source_site_ids_are_content_stable_and_tag_independent():
    src = "var ua = navigator.userAgent;"

    out_a, stats_a = ast_rewrite(src, tag="browser", include_source_site=True)
    out_b, stats_b = ast_rewrite(src, tag="sandbox", include_source_site=True)
    _, stats_changed = ast_rewrite(src + "\n", include_source_site=True)

    assert out_a is not None and out_b is not None
    assert stats_a["source_id"] == stats_b["source_id"]
    assert stats_a["source_sites"][0]["site_id"] == stats_b["source_sites"][0]["site_id"]
    assert stats_a["source_id"] != stats_changed["source_id"]


def test_default_ast_rewrite_does_not_add_source_site_arguments():
    src = "var ua = navigator.userAgent;"
    out, stats = ast_rewrite(src, tag="default")

    assert out is not None
    assert "source_sites" not in stats
    assert '__mcp_tap_get(navigator, "userAgent", "default")' in out


def test_large_source_site_rewrite_is_unique_and_parseable():
    import esprima

    unit = "var value = env['userAgent'];\n"
    src = "var env={userAgent:'ua'};\n" + unit * 11_000

    out, stats = ast_rewrite(
        src,
        tag="large",
        include_source_site=True,
        max_edits=20_000,
    )

    assert out is not None
    assert len(src.encode("utf-8")) == 330_026
    assert stats["edits"] == 11_000
    assert len(stats["source_sites"]) == 11_000
    assert len({site["site_id"] for site in stats["source_sites"]}) == 11_000
    esprima.parseScript(out)
