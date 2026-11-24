"""Tests for the generic script-extraction helpers (no browser needed)."""
from __future__ import annotations

import asyncio
import json

import pytest

from camoufox_reverse_mcp.server import browser_manager
from camoufox_reverse_mcp.tools.network import extract_script
from camoufox_reverse_mcp.utils.payload import (
    PayloadExtractError,
    extract_json_path,
    looks_like_script,
)


def _envelope(js: str) -> str:
    return json.dumps({"code": 0, "data": {"data": js}})


def test_extract_nested_string():
    js = "var a = function(){ return 1; };"
    assert extract_json_path(_envelope(js), "data.data") == js


def test_extract_through_list_index():
    body = json.dumps({"items": [{"script": "let x = 1;"}]})
    assert extract_json_path(body, "items.0.script") == "let x = 1;"


def test_raw_body_when_path_empty_is_not_parsed():
    body = "function f(){ return 2; }"
    # callers pass json_path='' and use the body directly; helper still parses
    with pytest.raises(PayloadExtractError):
        extract_json_path(body, "data.data")


def test_missing_segment_raises():
    with pytest.raises(PayloadExtractError) as exc:
        extract_json_path(_envelope("x"), "data.missing")
    assert "missing" in str(exc.value)


def test_non_string_target_raises():
    with pytest.raises(PayloadExtractError):
        extract_json_path(json.dumps({"data": {"data": {"nested": 1}}}), "data.data")


def test_empty_string_target_raises():
    with pytest.raises(PayloadExtractError):
        extract_json_path(_envelope("   "), "data.data")


def test_looks_like_script_heuristics():
    assert looks_like_script("(function(){})()")
    assert looks_like_script("var x = 1;")
    assert looks_like_script("e => e + 1")
    assert not looks_like_script("plain words only")
    assert not looks_like_script("")


def test_bracket_segment_and_negative_index_guard():
    body = json.dumps({"items": [{"script": "const v = 1;"}], "scalar": 42})
    assert extract_json_path(body, "items[0].script") == "const v = 1;"
    with pytest.raises(PayloadExtractError, match="negative list index"):
        extract_json_path(body, "items.-1.script")
    with pytest.raises(PayloadExtractError, match="cannot descend into int"):
        extract_json_path(body, "scalar.sub")


def test_extract_script_tool_truncation_surrogates_and_file_output(tmp_path):
    old_reqs = list(browser_manager._network_requests)
    try:
        browser_manager._network_requests = [
            {
                "id": 101,
                "url": "https://example.test/api/sdk",
                "response_body": '{"data":{"data":"function run(){ return \'\\ud800\'; }"}}',
                "response_body_capture_truncated": False,
                "body_state": "captured",
            },
            {
                "id": 102,
                "url": "https://example.test/api/sdk_trunc",
                "response_body": "function half(){",
                "response_body_capture_truncated": True,
                "response_body_total_size": 500000,
                "body_state": "truncated",
            },
        ]
        both = asyncio.run(extract_script(request_id=101, body="var x=1;"))
        assert "not both" in both["error"]

        trunc = asyncio.run(extract_script(request_id=102, json_path=""))
        assert trunc["partial"] is True
        assert "truncated" in trunc["error"]

        allowed = asyncio.run(extract_script(request_id=102, json_path="", allow_partial=True))
        assert allowed["partial"] is True
        assert allowed["script"] == "function half(){"

        out_path = tmp_path / "extracted.js"
        saved = asyncio.run(extract_script(request_id=101, output_file=str(out_path)))
        assert "error" not in saved
        assert saved["output"]["path"] == str(out_path.resolve())
        assert "warnings" in saved
        assert out_path.read_text(encoding="utf-8").startswith("function run()")
    finally:
        browser_manager._network_requests = old_reqs
