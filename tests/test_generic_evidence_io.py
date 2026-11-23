"""Unit tests for the generic evidence-IO additions.

No browser is required: every case exercises a pure helper or a path-level
contract that other projects can rely on.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from camoufox_reverse_mcp.tools.navigation import find_captured_request
from camoufox_reverse_mcp.tools.storage import build_cookie_header
from camoufox_reverse_mcp.utils.evidence import (
    preview,
    resolve_output_path,
    write_bytes_evidence,
    write_text_evidence,
)


def test_cookie_header_joins_and_dedupes():
    header, used = build_cookie_header([
        {"name": "a1", "value": "x1", "domain": ".example.com"},
        {"name": "sid", "value": "s2", "domain": ".example.com"},
        {"name": "a1", "value": "dup", "domain": "www.example.com"},
        {"name": "", "value": "skip"},
    ])
    assert header == "a1=x1; sid=s2"
    assert [c["name"] for c in used] == ["a1", "sid"]


def test_cookie_header_empty():
    header, used = build_cookie_header([])
    assert header == "" and used == []


def test_find_request_substring_and_after_id():
    entries = [
        {"id": 3, "url": "https://example.com/api/homefeed", "method": "POST"},
        {"id": 7, "url": "https://example.com/api/config", "method": "GET"},
    ]
    assert find_captured_request(entries, "homefeed")["id"] == 3
    assert find_captured_request(entries, "homefeed", after_id=3) is None
    assert find_captured_request(entries, "api/")["id"] == 3


def test_find_request_glob():
    entries = [{"id": 9, "url": "https://cdn.example.com/app/main.abc123.js"}]
    assert find_captured_request(entries, "*/main.*.js")["id"] == 9
    assert find_captured_request(entries, "*/other.*.js") is None


def test_write_text_and_digest(tmp_path):
    target = tmp_path / "nested" / "blob.txt"
    meta = write_text_evidence(str(target), "你好abc")
    assert meta["path"] == str(target)
    assert meta["bytes"] == len("你好abc".encode("utf-8"))
    assert meta["sha256"] == hashlib.sha256("你好abc".encode("utf-8")).hexdigest()
    assert target.read_text(encoding="utf-8") == "你好abc"


def test_write_refuses_overwrite(tmp_path):
    target = tmp_path / "x.txt"
    write_text_evidence(str(target), "one")
    with pytest.raises(FileExistsError):
        write_text_evidence(str(target), "two")
    assert write_text_evidence(str(target), "two", overwrite=True)["bytes"] == 3


def test_write_bytes_evidence(tmp_path):
    payload = bytes(range(256))
    meta = write_bytes_evidence(str(tmp_path / "raw.bin"), payload)
    assert meta["bytes"] == 256
    assert (tmp_path / "raw.bin").read_bytes() == payload


def test_resolve_output_path_rejects_empty():
    with pytest.raises(ValueError):
        resolve_output_path("   ")


def test_preview_is_bounded():
    assert preview("a" * 500, limit=20) == "a" * 20 + "..."
    assert preview("short") == "short"
    assert preview(None) is None
