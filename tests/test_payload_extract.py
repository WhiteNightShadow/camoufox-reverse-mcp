"""Tests for the generic script-extraction helpers (no browser needed)."""
from __future__ import annotations

import json

import pytest

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
