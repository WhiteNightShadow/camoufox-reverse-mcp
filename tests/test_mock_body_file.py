"""`intercept_request(mock_response={"body_file": ...})` 单元测试。

覆盖：
  * 读 UTF-8 文件、相对路径按服务器工作目录解析、每次请求重读
  * `body` 与 `body_file` 互斥
  * 文件不存在 / 是目录 / 非 UTF-8 → 可读错误（不抛裸异常）
  * `headers` 缺省时按后缀推 content-type
  * `modify` / `block` / `log` / `stop` 行为不变
  * 服务期读失败 → abort 该请求 + 控制台记一条 error
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.tools import network

INSTRUMENTED = "window.__hit=[];try{(window.__hit).push(['0x22']);}catch(e){}\n"


class FakeRoute:
    def __init__(self, url="https://example.test/vm-bundle.js?v_token=abc", method="GET"):
        self.request = SimpleNamespace(method=method, url=url, headers={"accept": "*/*"})
        self.fulfilled = None
        self.aborted = None
        self.continued = None

    async def fulfill(self, **kwargs):
        self.fulfilled = kwargs

    async def abort(self, reason=None):
        self.aborted = reason or "aborted"

    async def continue_(self, **kwargs):
        self.continued = kwargs


class FakePage:
    def __init__(self):
        self.routes = {}

    async def route(self, pattern, handler):
        self.routes[pattern] = handler

    async def unroute(self, pattern):
        self.routes.pop(pattern, None)


@pytest.fixture
def fake_browser(monkeypatch):
    page = FakePage()
    manager = SimpleNamespace(get_active_page=AsyncMock(return_value=page),
                              _console_logs=[])
    monkeypatch.setattr(network, "browser_manager", manager)
    return page, manager


async def test_body_file_is_served_as_the_response(fake_browser, tmp_path):
    page, _ = fake_browser
    body_file = tmp_path / "vm-bundle.instrumented.js"
    body_file.write_text(INSTRUMENTED, encoding="utf-8", newline="\n")
    result = await network.intercept_request(
        "**/vm-bundle.js*", action="mock",
        mock_response={"body_file": str(body_file), "status": 200,
                       "headers": {"content-type": "application/javascript"}})
    assert result["status"] == "intercepting"
    assert result["mock"]["body_file"] == str(body_file.resolve())
    assert result["mock"]["body_file_bytes"] == len(INSTRUMENTED.encode("utf-8"))
    assert result["mock"]["body_reread_per_request"] is True

    route = FakeRoute()
    await page.routes["**/vm-bundle.js*"](route)
    assert route.fulfilled == {
        "status": 200,
        "headers": {"content-type": "application/javascript"},
        "body": INSTRUMENTED,
    }


async def test_body_file_is_reread_for_every_request(fake_browser, tmp_path):
    """改文件 → 下一次请求就生效，不用重新装 intercept。"""
    page, _ = fake_browser
    body_file = tmp_path / "vm-bundle.js"
    body_file.write_text("var v = 1;\n", encoding="utf-8", newline="\n")
    await network.intercept_request("**/vm-bundle.js*", action="mock",
                                    mock_response={"body_file": str(body_file)})
    first = FakeRoute()
    await page.routes["**/vm-bundle.js*"](first)
    assert first.fulfilled["body"] == "var v = 1;\n"

    body_file.write_text("var v = 2;\r\n", encoding="utf-8", newline="")
    second = FakeRoute()
    await page.routes["**/vm-bundle.js*"](second)
    assert second.fulfilled["body"] == "var v = 2;\r\n"


async def test_relative_body_file_resolves_against_the_working_directory(
        fake_browser, tmp_path, monkeypatch):
    page, _ = fake_browser
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "vm-bundle.js").write_text(INSTRUMENTED, encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path / "work")
    result = await network.intercept_request("**/vm-bundle.js*", action="mock",
                                            mock_response={"body_file": "vm-bundle.js"})
    assert result["mock"]["body_file"] == str((tmp_path / "work" / "vm-bundle.js").resolve())
    route = FakeRoute()
    await page.routes["**/vm-bundle.js*"](route)
    assert route.fulfilled["body"] == INSTRUMENTED


async def test_content_type_is_inferred_from_the_suffix(fake_browser, tmp_path):
    page, _ = fake_browser
    body_file = tmp_path / "vm-bundle.js"
    body_file.write_text(INSTRUMENTED, encoding="utf-8")
    result = await network.intercept_request("**/vm-bundle.js*", action="mock",
                                            mock_response={"body_file": str(body_file)})
    assert result["mock"]["headers"] == {"content-type": "application/javascript; charset=utf-8"}
    route = FakeRoute()
    await page.routes["**/vm-bundle.js*"](route)
    assert route.fulfilled["headers"]["content-type"] == "application/javascript; charset=utf-8"


async def test_body_and_body_file_are_mutually_exclusive(fake_browser, tmp_path):
    page, _ = fake_browser
    body_file = tmp_path / "vm-bundle.js"
    body_file.write_text("x", encoding="utf-8")
    result = await network.intercept_request(
        "**/vm-bundle.js*", action="mock",
        mock_response={"body": "{}", "body_file": str(body_file)})
    assert "exactly one of body or body_file" in result["error"]
    assert page.routes == {}


async def test_missing_body_file_returns_a_readable_error(fake_browser, tmp_path):
    page, _ = fake_browser
    missing = tmp_path / "nope.js"
    result = await network.intercept_request("**/vm-bundle.js*", action="mock",
                                            mock_response={"body_file": str(missing)})
    assert "not found" in result["error"]
    assert "server working directory" in result["error"]
    assert result["body_file"] == str(missing.resolve())
    assert page.routes == {}  # 没有装上任何路由


async def test_directory_and_non_utf8_body_files_are_reported(fake_browser, tmp_path):
    page, _ = fake_browser
    directory = await network.intercept_request("**/x.js", action="mock",
                                               mock_response={"body_file": str(tmp_path)})
    assert "is a directory" in directory["error"]

    binary = tmp_path / "bin.js"
    binary.write_bytes(b"\xff\xfe\x00\x00")
    bad = await network.intercept_request("**/x.js", action="mock",
                                          mock_response={"body_file": str(binary)})
    assert "not valid UTF-8" in bad["error"]
    assert page.routes == {}


async def test_non_string_body_is_reported(fake_browser):
    result = await network.intercept_request("**/x.js", action="mock",
                                            mock_response={"body": {"a": 1}})
    assert "body_file" in result["error"]


async def test_inline_body_still_uses_the_json_default(fake_browser):
    page, _ = fake_browser
    result = await network.intercept_request("**/api.json", action="mock",
                                            mock_response={"body": '{"ok":1}'})
    assert result["mock"]["inline_body"] is True
    assert result["mock"]["body_file"] is None
    route = FakeRoute(url="https://example.test/api.json")
    await page.routes["**/api.json"](route)
    assert route.fulfilled == {"status": 200,
                               "headers": {"content-type": "application/json"},
                               "body": '{"ok":1}'}


async def test_read_failure_while_serving_aborts_the_request(fake_browser, tmp_path):
    page, manager = fake_browser
    body_file = tmp_path / "vm-bundle.js"
    body_file.write_text(INSTRUMENTED, encoding="utf-8")
    await network.intercept_request("**/vm-bundle.js*", action="mock",
                                    mock_response={"body_file": str(body_file)})
    body_file.unlink()  # 装好之后文件没了
    route = FakeRoute()
    await page.routes["**/vm-bundle.js*"](route)
    assert route.fulfilled is None
    assert route.aborted == "failed"
    assert any("not found" in entry["text"] and entry["level"] == "error"
               for entry in manager._console_logs)


async def test_log_block_modify_and_stop_are_unchanged(fake_browser):
    page, manager = fake_browser
    await network.intercept_request("**/api/*", action="log")
    logged = FakeRoute(url="https://example.test/api/x")
    await page.routes["**/api/*"](logged)
    assert logged.continued == {}
    assert "INTERCEPT:log" in manager._console_logs[0]["text"]

    await network.intercept_request("**/api/*", action="block")
    blocked = FakeRoute(url="https://example.test/api/x")
    await page.routes["**/api/*"](blocked)
    assert blocked.aborted == "aborted"

    await network.intercept_request("**/api/*", action="modify",
                                    modify_headers={"x-token": "t"}, modify_body="a=1")
    modified = FakeRoute(url="https://example.test/api/x")
    await page.routes["**/api/*"](modified)
    assert modified.continued["post_data"] == "a=1"
    assert modified.continued["headers"]["x-token"] == "t"

    stopped = await network.intercept_request("**/api/*", action="stop")
    assert stopped["status"] == "stopped"
    assert page.routes == {}


async def test_empty_or_oversized_body_file_and_bad_headers_or_status_rejected_before_route(
        fake_browser, tmp_path, monkeypatch):
    page, _ = fake_browser
    empty_path = await network.intercept_request("**/x.js", action="mock",
                                                 mock_response={"body_file": ""})
    assert "non-empty path string" in empty_path["error"]
    assert page.routes == {}

    both_with_empty = await network.intercept_request(
        "**/x.js", action="mock", mock_response={"body": "1", "body_file": ""})
    assert "exactly one" in both_with_empty["error"]
    assert page.routes == {}

    bad_hdrs = await network.intercept_request(
        "**/x.js", action="mock", mock_response={"body": "1", "headers": "not-a-dict"})
    assert "headers" in bad_hdrs["error"]
    assert page.routes == {}

    bad_status = await network.intercept_request(
        "**/x.js", action="mock", mock_response={"body": "1", "status": "abc"})
    assert "status" in bad_status["error"]
    assert page.routes == {}

    monkeypatch.setattr(network, "MAX_MOCK_BODY_BYTES", 32)
    big = tmp_path / "big.js"
    big.write_text("x" * 64, encoding="utf-8")
    too_big = await network.intercept_request("**/x.js", action="mock",
                                              mock_response={"body_file": str(big)})
    assert "exceeds" in too_big["error"]
    assert page.routes == {}
