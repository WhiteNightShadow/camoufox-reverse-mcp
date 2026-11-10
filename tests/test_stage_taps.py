"""Stage/dispatcher taps: literal rewrite of the flattened-VMP call shape."""
from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite
from camoufox_reverse_mcp.utils.js_rewriter import (
    INSTRUMENT_RUNTIME,
    regex_rewrite,
    rewrite_stage_calls,
)

STAGE_CALL = (
    "function d(a){switch(a){case 1:return l.apply(this,[9425].concat("
    "Array.prototype.slice.call(arguments)));}}"
)


def test_runtime_defines_stage_tap():
    assert "__mcp_tap_stage" in INSTRUMENT_RUNTIME
    assert "type:'tap_stage'" in INSTRUMENT_RUNTIME


def test_stage_rewrite_matches_exact_dispatcher_shape():
    out, count, sites = rewrite_stage_calls(STAGE_CALL, tag="t")
    assert count == 1
    assert '__mcp_tap_stage(9425, l, this, arguments, "t")' in out
    assert sites == []


def test_stage_rewrite_leaves_other_calls_untouched():
    src = "return x.apply(this, [1, 2]);"
    out, count, _ = rewrite_stage_calls(src, tag="t")
    assert count == 0
    assert out == src


def test_stage_rewrite_adds_source_sites_when_requested():
    out, count, sites = rewrite_stage_calls(STAGE_CALL, tag="t", include_source_site=True)
    assert count == 1
    assert len(sites) == 1
    assert sites[0]["kind"] == "tap_stage"
    assert sites[0]["site_id"].endswith(":tap_stage")


def test_regex_rewrite_stage_only_still_injects_runtime():
    out, stats = regex_rewrite(STAGE_CALL, tag="t", tap_stage_calls=True)
    assert stats["stage_rewrites"] == 1
    assert "__mcp_tap_stage(9425" in out
    assert "__mcp_tap_installed" in out


def test_ast_rewrite_adds_stage_taps():
    out, stats = ast_rewrite(
        STAGE_CALL, tag="t", tap_stage_calls=True,
        rewrite_member_access=False, rewrite_calls=False,
    )
    assert out is not None
    assert stats["stage_edits"] == 1
    assert stats["edits"] == 1
    assert "__mcp_tap_stage(9425" in out


def test_ast_rewrite_keeps_stage_taps_when_parser_fails(monkeypatch):
    import camoufox_reverse_mcp.utils.js_parser as js_parser

    def boom(src, locations=False):  # noqa: ARG001
        raise ValueError("parser unavailable")

    monkeypatch.setattr(js_parser, "parse_source", boom)
    out, stats = ast_rewrite(STAGE_CALL, tag="t", tap_stage_calls=True)
    assert out is not None
    assert stats.get("stage_only") is True
    assert stats["stage_edits"] == 1
    assert "__mcp_tap_stage(9425" in out
