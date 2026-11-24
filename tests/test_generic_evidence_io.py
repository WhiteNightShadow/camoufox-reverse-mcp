"""Unit and async tool tests for generic evidence-IO additions."""
from __future__ import annotations

import base64
import hashlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.tools import debugging, navigation, storage
from camoufox_reverse_mcp.tools.navigation import _request_summary, find_captured_request
from camoufox_reverse_mcp.tools.storage import build_cookie_header
from camoufox_reverse_mcp.utils.evidence import (
    preview,
    resolve_output_path,
    write_bytes_evidence,
    write_text_evidence,
)


def test_cookie_header_joins_dedupes_and_sanitizes():
    header, used = build_cookie_header([
        {"name": "session_id", "value": "x1", "domain": ".example.com"},
        {"name": "csrf_token", "value": None, "domain": ".example.com"},
        {"name": "session_id", "value": "dup", "domain": "www.example.com"},
        {"name": "bad\r\ncookie", "value": "1"},
        {"name": "bad_val", "value": "a\nb"},
        {"name": "", "value": "skip"},
    ])
    assert header == "session_id=x1; csrf_token="
    assert [c["name"] for c in used] == ["session_id", "csrf_token"]


def test_cookie_header_empty():
    header, used = build_cookie_header([])
    assert header == "" and used == []


def test_find_request_substring_after_id_and_settled():
    entries = [
        {"id": 3, "url": "https://example.com/api/items", "method": "POST", "state": "pending"},
        {"id": 5, "url": "https://example.com/api/items", "method": "POST", "state": "responded", "body_state": "complete"},
        {"id": 7, "url": "https://example.com/api/config", "method": "GET", "state": "responded"},
    ]
    assert find_captured_request(entries, "/api/items")["id"] == 3
    assert find_captured_request(entries, "/api/items", require_settled=True)["id"] == 5
    assert find_captured_request(entries, "/api/items", after_id=5, require_settled=True) is None


def test_find_request_glob_escapes_brackets():
    entries = [{"id": 9, "url": "https://cdn.example.com/api?items[0]=main.abc123.js"}]
    assert find_captured_request(entries, "**/api?items[0]=*.js")["id"] == 9
    assert find_captured_request(entries, "*/other.*.js") is None


def test_request_summary_maps_browser_manager_fields():
    raw = {
        "id": 11,
        "url": "https://example.com/api/list",
        "method": "GET",
        "status": 200,
        "duration": 42,
        "resource_type": "fetch",
        "response_body": '{"ok":true}',
        "response_body_bytes": 11,
        "body_state": "complete",
        "state": "responded",
    }
    s = _request_summary(raw)
    assert s["ms"] == 42
    assert s["type"] == "fetch"
    assert s["size"] == 11
    assert s["body_bytes"] == 11
    assert s["has_body"] is True


def test_write_text_digest_and_surrogates(tmp_path):
    target = tmp_path / "nested" / "blob.txt"
    meta = write_text_evidence(str(target), "你好abc\ud800")
    expected = "你好abc\ufffd".encode("utf-8")
    assert meta["path"] == str(target)
    assert meta["bytes"] == len(expected)
    assert meta["sha256"] == hashlib.sha256(expected).hexdigest()
    assert meta["lone_surrogates_replaced"] == 1
    assert target.read_bytes() == expected


def test_write_refuses_overwrite_and_symlinks(tmp_path):
    target = tmp_path / "x.txt"
    write_text_evidence(str(target), "one")
    with pytest.raises(FileExistsError):
        write_text_evidence(str(target), "two")
    assert write_text_evidence(str(target), "two", overwrite=True)["bytes"] == 3

    if hasattr(os, "symlink"):
        link = tmp_path / "dangling_or_real.lnk"
        try:
            os.symlink(target, link)
        except OSError:
            pytest.skip("symlinks unavailable on this runner")
        with pytest.raises(ValueError, match="symlink"):
            write_text_evidence(str(link), "evil", overwrite=True)


def test_write_bytes_evidence(tmp_path):
    payload = bytes(range(256))
    meta = write_bytes_evidence(str(tmp_path / "raw.bin"), payload)
    assert meta["bytes"] == 256
    assert (tmp_path / "raw.bin").read_bytes() == payload


def test_resolve_output_path_rejects_empty_and_dir(tmp_path):
    with pytest.raises(ValueError):
        resolve_output_path("   ")
    with pytest.raises(IsADirectoryError):
        resolve_output_path(str(tmp_path))


def test_preview_is_bounded_and_surrogate_safe():
    assert preview("a" * 500, limit=20) == "a" * 20 + "..."
    assert preview("short\ud800") == "short\ufffd"
    assert preview(None) is None


async def test_evaluate_js_output_file_validates_before_eval_and_rejects_non_string(monkeypatch, tmp_path):
    existing = tmp_path / "exists.txt"
    existing.write_text("keep", encoding="utf-8")
    get_page = AsyncMock()
    monkeypatch.setattr(debugging.browser_manager, "get_active_page", get_page)

    res = await debugging.evaluate_js("sideEffect()", output_file=str(existing), overwrite=False)
    assert res["type"] == "error"
    assert get_page.await_count == 0  # validated before touching browser/JS!

    fake_page = SimpleNamespace(frames=[], main_frame=SimpleNamespace(url="https://example.com/", name=""))
    monkeypatch.setattr(debugging.browser_manager, "get_active_page", AsyncMock(return_value=fake_page))
    monkeypatch.setattr(debugging, "resolve_frame", lambda page, **kw: (fake_page.main_frame, {"url": "https://example.com/"}))

    # Circular/window fallback produces Python str + js_type="object" -> must be rejected!
    monkeypatch.setattr(
        debugging,
        "evaluate_in_world",
        AsyncMock(return_value=({"result": "[object Window]", "type": "object", "serialization_warning": "fallback"}, "isolated", None)),
    )
    out = tmp_path / "win.txt"
    bad_obj = await debugging.evaluate_js("window", output_file=str(out))
    assert bad_obj["type"] == "error"
    assert "got object" in bad_obj["error"]
    assert not out.exists()

    # Valid base64 vs invalid base64
    raw_bytes = b"\x00\x01\xfe\xffwasm"
    monkeypatch.setattr(
        debugging,
        "evaluate_in_world",
        AsyncMock(return_value=({"result": "data:application/octet-stream;base64,AAAA", "type": "string"}, "isolated", None)),
    )
    bad_b64 = await debugging.evaluate_js("x", output_file=str(out), output_encoding="base64")
    assert bad_b64["type"] == "error"
    assert "data:" in bad_b64["error"]

    monkeypatch.setattr(
        debugging,
        "evaluate_in_world",
        AsyncMock(return_value=({"result": base64.b64encode(raw_bytes).decode("ascii"), "type": "string"}, "isolated", None)),
    )
    ok_b64 = await debugging.evaluate_js("x", output_file=str(out), output_encoding="base64")
    assert ok_b64["type"] == "file"
    assert out.read_bytes() == raw_bytes


async def test_cookies_header_output_file_redacts_inline_header_and_scopes_url(monkeypatch, tmp_path):
    jar = [
        {"name": "sid", "value": "secret123", "domain": ".example.com", "path": "/"},
        {"name": "sid", "value": "sub456", "domain": "app.example.com", "path": "/app"},
    ]
    ctx = SimpleNamespace(cookies=AsyncMock(return_value=jar))
    page = SimpleNamespace(url="https://app.example.com/app/home", context=ctx)
    monkeypatch.setattr(storage.browser_manager, "get_active_page", AsyncMock(return_value=page))

    dest = tmp_path / "cookie_header.txt"
    res = await storage.cookies(action="header", domain="example.com", output_file=str(dest))
    assert res["header"] is None  # never leaked inline when output_file is requested
    assert res["count"] == 1
    assert res["output"]["path"] == str(dest)
    # Longest path (/app) wins over root (/)
    assert dest.read_text(encoding="utf-8") == "sid=sub456"


async def test_wait_for_auto_and_request_modes(monkeypatch):
    entries = [
        {"id": 1, "url": "https://example.com/api/token", "method": "POST", "state": "pending", "body_state": "pending"},
    ]

    async def settle_later():
        await debugging.asyncio.sleep(0.03)
        entries[0]["state"] = "responded"
        entries[0]["status"] = 200
        entries[0]["duration"] = 18
        entries[0]["body_state"] = "complete"
        entries[0]["response_body"] = '{"token":"ok"}'

    page = SimpleNamespace(wait_for_url=AsyncMock(), wait_for_selector=AsyncMock())
    monkeypatch.setattr(navigation.browser_manager, "get_active_page", AsyncMock(return_value=page))
    monkeypatch.setattr(navigation.browser_manager, "_capturing", False)
    monkeypatch.setattr(navigation.browser_manager, "_network_requests", entries)

    # When _capturing is False, mode="auto" uses wait_for_url immediately even if _network_requests is non-empty!
    nav_res = await navigation.wait_for(url_pattern="**/dashboard", timeout=200)
    assert nav_res["mode"] == "navigation"
    assert page.wait_for_url.await_count == 1

    # Explicit mode="request" waits for pending entry to settle
    task = debugging.asyncio.create_task(settle_later())
    req_res = await navigation.wait_for(url_pattern="/api/token", mode="request", timeout=500, poll_interval_ms=10)
    await task
    assert req_res["status"] == "matched"
    assert req_res["request"]["status"] == 200
    assert req_res["request"]["ms"] == 18

    # Timeout in mode="request" includes both status="timeout" and "error"
    to_res = await navigation.wait_for(url_pattern="/api/never", mode="request", timeout=30, poll_interval_ms=10)
    assert to_res["status"] == "timeout"
    assert "error" in to_res
