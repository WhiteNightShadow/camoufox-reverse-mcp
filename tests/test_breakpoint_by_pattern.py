"""断点跨构建定位：`url_pattern` + `find` 正则定位 + 导航/reload 后自动重下。

覆盖：
  * 纯逻辑：URL glob、字符偏移 → (line, column)（含单行 200KB 与代理对）、第 N 个匹配
  * `set_breakpoint(url_pattern=..., find=..., occurrence=...)` 换算坐标并下发
  * reload 后按 url_pattern 重新 sources() → 重算偏移 → 重下（URL 变、偏移偏移都跟得上）
  * 新脚本里正则不再匹配 → `reapply_failed: [{pattern, reason: "regex not found"}]`
  * `enable` 换 thread 时也重下；老的 line/source_url/source 用法不变
  * 工具层：`rdp_debugger(action="set_breakpoint", url_pattern=..., find=...)`
"""
import json
import threading

import pytest

from camoufox_reverse_mcp.debugger_rdp_tools import (
    FireFoxDebugger,
    RdpError,
    count_matches,
    find_match,
    glob_match,
    offset_to_line_column,
)
from camoufox_reverse_mcp.tools import debugger_rdp as tool
from camoufox_reverse_mcp.tools.debugger_rdp import rdp_debugger

FIND = r"case (0x[0-9a-f]+):(_0x[0-9a-f]+)\["
VM_BODY = "case 0x22:_0xa[_0xb[++_0xc]];break;\ncase 0x4b:_0xa[_0xb[++_0xd]];break;\n"


# ---------------------------------------------------------------- 纯逻辑


def test_glob_match_understands_doublestar_and_v_token_suffix():
    url = "https://example.test/assets/vm-bundle.js?v_token=abc123"
    assert glob_match("**/vm-bundle.js*", url)
    assert glob_match("**/vm-bundle.js?v_token=*", url)
    assert glob_match("**/*.js*", url)
    assert not glob_match("**/vm-bundle.js", url)          # 没有尾随 * → 不匹配 query
    assert not glob_match("**/other.js*", url)
    assert not glob_match("*.js*", url)              # 单 * 不跨 /
    assert not glob_match("**/vm-bundle.js*", "")          # eval / new Function 没有 URL
    assert not glob_match("", url)


def test_offset_to_line_column_counts_utf16_units_and_maps_lines():
    text = 'var a = "\U0001F600";\nvar b = 1;\nvar c = 2;\n'
    assert offset_to_line_column(text, 0) == (1, 0)
    # emoji 占 2 个 UTF-16 码元：列号按码元算，不按码点算
    assert offset_to_line_column(text, text.index("\U0001F600")) == (1, 9)
    assert offset_to_line_column(text, text.index("var b")) == (2, 0)
    assert offset_to_line_column(text, len(text)) == (4, 0)
    # \u2028 也是 JS 行终止符
    assert offset_to_line_column("a;\u2028b;", 3) == (2, 0)


def test_offset_to_line_column_on_a_200kb_single_line_script():
    prefix = ("var _0x1=1;" * 20000)[:200_000]
    text = prefix + VM_BODY.replace("\n", "")
    offset = text.index("case 0x4b:")
    assert "\n" not in text[:offset]
    assert offset_to_line_column(text, offset) == (1, offset)
    assert len(text) > 200_000


def test_find_match_reports_occurrence_offset_and_groups():
    first = find_match(VM_BODY, FIND, 1)
    second = find_match(VM_BODY, FIND, 2)
    assert first["line"] == 1 and first["column_0based"] == 0
    assert first["groups"] == ["0x22", "_0xa"]
    assert first["match"] == "case 0x22:_0xa["
    assert second["line"] == 2 and second["column_0based"] == 0
    assert second["groups"] == ["0x4b", "_0xa"]
    assert find_match(VM_BODY, FIND, 3) is None
    assert count_matches(VM_BODY, FIND) == 2
    with pytest.raises(RdpError):
        find_match(VM_BODY, "case (", 1)


def test_find_match_on_a_minified_line_reports_a_big_column():
    minified = ("case 0x22:_0xa[_0xb[++_0xc]];" * 3)
    found = find_match(minified, r"case 0x4b:_0xa\[", 1)
    assert found is None  # 这份样本里没有 0x4b
    found = find_match(minified, r"case 0x22:_0xa\[", 2)
    assert found["line"] == 1
    assert found["column_0based"] == len("case 0x22:_0xa[_0xb[++_0xc]];")


# ---------------------------------------------------------------- 假 RDP


class FakeRdp:
    """按请求类型返回可变状态的假客户端（脚本/URL 可以在 reload 前换掉）。"""

    def __init__(self):
        self.requests = []
        self.events = []
        self._seq = 100
        self._cancel = threading.Event()
        self.closed = False
        self.sources_list = []
        self.texts = {}          # actor -> source text
        self.columns = {}        # actor -> {line: [columns]}
        self.thread_name = "thread2"

    def sent(self, type_name):
        return [request for request in self.requests if request.get("type") == type_name]

    def mark(self):
        return self._seq

    def drain(self, idle=0.0, budget=0.0):
        return 0

    def send(self, obj):
        self.requests.append(dict(obj))

    def cancel(self, reason="cancelled"):
        self._cancel.set()

    def resume_after_cancel(self):
        self._cancel.clear()

    def close(self):
        self.closed = True

    def call(self, obj, timeout=20.0, aliases=()):
        self.requests.append(dict(obj))
        kind = obj.get("type")
        if kind == "sources":
            return {"from": obj["to"], "sources": list(self.sources_list)}
        if kind == "source":
            return {"from": obj["to"], "source": self.texts.get(obj["to"], "")}
        if kind == "getBreakpointPositionsCompressed":
            line = obj["query"]["start"]["line"]
            columns = self.columns.get(obj["to"], {}).get(line) or []
            return {"from": obj["to"], "positions": {str(line): columns} if columns else {}}
        if kind == "getBreakableLines":
            return {"from": obj["to"],
                    "lines": sorted(self.columns.get(obj["to"], {}) or {})}
        if kind in ("setBreakpoint", "removeBreakpoint", "detach", "reload"):
            return {"from": obj["to"]}
        if kind == "setXHRBreakpoint":
            return {"from": obj["to"], "value": True}
        if kind == "isAttached":
            return {"from": obj["to"], "value": True}
        if kind == "getTarget":
            return {"from": obj["to"], "frame": {"actor": "target2",
                                                 "threadActor": self.thread_name,
                                                 "consoleActor": "console2"}}
        raise AssertionError("unexpected request: %r" % (obj,))

    def wait_event(self, predicate, timeout=20.0, since=None):
        floor = 0 if since is None else since
        for event in self.events:
            if event["__seq"] > floor and predicate(event):
                return event
        return None


def _dbg(fake):
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    dbg.thread = "thread1"
    dbg.target = "target1"
    dbg.tab = "tab1"
    dbg.console = "console1"
    return dbg


def _positions_for(text: str) -> dict:
    """假的可停位置：按正则命中列给出（第 1 行），另加一个邻居列测吸附。"""
    return {1: [0, len("case 0x22:_0xa[")], 2: [0]}


@pytest.fixture(autouse=True)
def _clean_tool_state():
    tool.cleanup_session()
    yield
    tool.cleanup_session()


# ---------------------------------------------------------------- set_breakpoint


def test_set_breakpoint_by_url_pattern_and_find():
    dbg = FireFoxDebugger(port=1)
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    fake.columns = {"s1": _positions_for(VM_BODY)}
    dbg = _dbg(fake)

    out = dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND, occurrence=2)
    assert out["installed"] is True
    request = fake.sent("setBreakpoint")[0]
    assert request["location"] == {"sourceUrl": "https://cdn.test/vm-bundle.js?v_token=aaa",
                                   "line": 2, "column": 0}
    assert out["find"]["occurrence"] == 2
    assert out["find"]["match"] == "case 0x4b:_0xa["
    assert out["find"]["groups"] == ["0x4b", "_0xa"]
    assert out["find"]["source_url"] == "https://cdn.test/vm-bundle.js?v_token=aaa"
    assert "url_pattern + find" in out["reload_hint"]
    record = dbg.breakpoints[0]
    assert record["locator"]["find"] == FIND
    assert record["locator"]["occurrence"] == 2
    assert record["locator"]["url_pattern"] == "**/vm-bundle.js*"
    assert dbg.status()["breakpoints"][0]["locator"]["occurrence"] == 2


def test_set_breakpoint_by_find_on_a_single_line_script():
    """单行 200KB：列号就是字符偏移，行号恒为 1。"""
    text = ("var _0x1=1;" * 16000)[:200_000] + VM_BODY.replace("\n", "")
    expected = text.index("case 0x4b:")
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js", "sourceStartLine": 1}]
    fake.texts = {"s1": text}
    fake.columns = {"s1": {1: [expected, expected + 4]}}
    dbg = _dbg(fake)

    out = dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=r"case 0x4b:", occurrence=1)
    assert out["location"]["line"] == 1
    assert out["location"]["column_0based"] == expected
    assert fake.sent("setBreakpoint")[0]["location"]["column"] == expected


def test_set_breakpoint_snaps_to_the_nearest_breakable_column():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js", "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    fake.columns = {"s1": {1: [0, 40]}}  # 命中列 13 不在表里 → 吸到最近的 0
    dbg = _dbg(fake)
    out = dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND, occurrence=1)
    assert out["location"]["column_0based"] == 0
    assert fake.sent("setBreakpoint")[0]["location"]["column"] == 0


def test_find_that_matches_nothing_raises_a_clear_error():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js", "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    dbg = _dbg(fake)
    with pytest.raises(RdpError) as excinfo:
        dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=r"case 0xff:", occurrence=1)
    assert "第 1 个匹配" in str(excinfo.value)
    assert fake.sent("setBreakpoint") == []


def test_url_pattern_that_matches_nothing_lists_known_urls():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/other.js",
                          "sourceStartLine": 1}]
    dbg = _dbg(fake)
    with pytest.raises(RdpError) as excinfo:
        dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND)
    assert "已知 URL" in str(excinfo.value)


def test_explicit_line_and_source_url_still_work():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js",
                          "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    fake.columns = {"s1": {2: [0, 7]}}
    dbg = _dbg(fake)
    out = dbg.set_breakpoint(2, source_url="https://cdn.test/vm-bundle.js")
    assert out["installed"] is True
    assert fake.sent("setBreakpoint")[0]["location"] == {
        "sourceUrl": "https://cdn.test/vm-bundle.js", "line": 2, "column": 0}
    assert "locator" not in dbg.breakpoints[0]


# ---------------------------------------------------------------- reload 重下


def test_reload_recomputes_the_offset_and_redownloads_the_breakpoint():
    """URL 换了（v_token 变）、脚本前面多了一段（偏移整体后移）→ 仍要命中同一处代码。"""
    fake = FakeRdp()
    fake.sources_list = [{"actor": "old", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"old": VM_BODY}
    fake.columns = {"old": {1: [0, 13], 2: [0]}}
    dbg = _dbg(fake)
    dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND, occurrence=1)
    assert fake.sent("setBreakpoint")[0]["location"]["line"] == 1

    shifted = "var pad = 1;\n" + VM_BODY
    fake.sources_list = [{"actor": "new", "url": "https://cdn.test/vm-bundle.js?v_token=bbb",
                          "sourceStartLine": 1}]
    fake.texts = {"new": shifted}
    fake.columns = {"new": {2: [0, 26], 3: [0]}}
    info = dbg.reload(wait=2.0)

    assert info["thread"] == "thread2"
    assert "reapply_failed" not in info
    [location] = info["reapplied"]
    assert location["sourceUrl"] == "https://cdn.test/vm-bundle.js?v_token=bbb"
    assert location["line"] == 2
    last = fake.sent("setBreakpoint")[-1]
    assert last["location"] == location
    assert last["location"]["line"] == 2
    # 记录跟着更新：下一次 reload 用的是新坐标
    assert dbg.breakpoints[0]["line"] == 2


def test_reload_reports_regex_not_found_in_the_new_build():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "old", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"old": VM_BODY}
    fake.columns = {"old": {1: [0, 13], 2: [0]}}
    dbg = _dbg(fake)
    dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND, occurrence=1)

    fake.sources_list = [{"actor": "new", "url": "https://cdn.test/vm-bundle.js?v_token=ccc",
                          "sourceStartLine": 1}]
    fake.texts = {"new": "case 0x99:_0xzz[_0xy[++_0xz]];break;\n"}  # 换 build：opcode 全变
    fake.columns = {"new": {1: [0]}}
    info = dbg.reload(wait=2.0)

    assert info["reapplied"] == []
    [failure] = info["reapply_failed"]
    assert failure["reason"] == "regex not found"
    assert failure["pattern"] == FIND
    assert failure["url_pattern"] == "**/vm-bundle.js*"
    assert failure["searched"][0]["matches"] == 0
    assert failure["searched"][0]["url"].endswith("v_token=ccc")


def test_reload_reports_a_missing_source():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "old", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"old": VM_BODY}
    fake.columns = {"old": {1: [0, 13], 2: [0]}}
    dbg = _dbg(fake)
    dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND, occurrence=1)

    fake.sources_list = [{"actor": "new", "url": "https://cdn.test/other.js",
                          "sourceStartLine": 1}]
    info = dbg.reload(wait=2.0)
    [failure] = info["reapply_failed"]
    assert failure["reason"] == "source not found"
    assert "已知 URL" in failure["detail"]


def test_reload_still_handles_plain_locations_and_source_id_skips():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/app.js",
                          "sourceStartLine": 1},
                         {"actor": "s2", "url": None, "sourceStartLine": 1}]
    fake.texts = {"s1": "var a = 1;", "s2": "var b = 2;"}
    fake.columns = {"s1": {1: [0]}, "s2": {1: [0]}}
    dbg = _dbg(fake)
    dbg.set_breakpoint(1, source_url="https://cdn.test/app.js")
    dbg.set_breakpoint(1, source_actor="s2")

    info = dbg.reload(wait=2.0)
    assert len(info["reapplied"]) == 1          # url 断点照旧重下
    [skipped] = info["reapply_skipped"]         # eval 的 sourceId 断点仍然明说做不到
    assert "url_pattern + find" in skipped["hint"]


def test_enable_reapplies_when_the_thread_is_rebuilt():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    fake.columns = {"s1": {1: [0, 13], 2: [0]}}
    fake.route_tabs = [{"actor": "tab1", "url": "https://cdn.test/"}]
    dbg = _dbg(fake)
    dbg.set_breakpoint(url_pattern="**/vm-bundle.js*", find=FIND, occurrence=1)

    before = len(fake.sent("setBreakpoint"))
    fake.sources_list = [{"actor": "s1b", "url": "https://cdn.test/vm-bundle.js?v_token=bbb",
                          "sourceStartLine": 1}]
    fake.texts = {"s1b": "var pad = 1;\n" + VM_BODY}
    fake.columns = {"s1b": {2: [0, 26]}}

    def call(obj, timeout=20.0, aliases=()):
        if obj.get("type") == "listTabs":
            fake.requests.append(dict(obj))
            return {"from": "root", "tabs": fake.route_tabs}
        return FakeRdp.call(fake, obj, timeout, aliases)

    fake.call = call
    info = dbg.enable("cdn.test")
    assert info["thread"] == "thread2"
    assert len(fake.sent("setBreakpoint")) == before + 1
    assert info["reapplied"][0]["sourceUrl"] == "https://cdn.test/vm-bundle.js?v_token=bbb"
    assert info["reapplied"][0]["line"] == 2


# ---------------------------------------------------------------- 工具层


async def test_tool_set_breakpoint_with_url_pattern_and_find():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    fake.columns = {"s1": {1: [0, 13], 2: [0]}}
    dbg = _dbg(fake)
    tool._STATE["dbg"] = dbg

    out = await rdp_debugger(action="set_breakpoint", url_pattern="**/vm-bundle.js*",
                             find=r"case (0x[0-9a-f]+):", occurrence=2,
                             condition="p === 1")
    assert out["installed"] is True
    assert out["location"]["line"] == 2
    assert fake.sent("setBreakpoint")[0]["options"]["condition"] == "p === 1"


async def test_tool_set_breakpoint_requires_line_or_find():
    fake = FakeRdp()
    dbg = _dbg(fake)
    tool._STATE["dbg"] = dbg
    with pytest.raises(RdpError) as excinfo:
        await rdp_debugger(action="set_breakpoint", url_pattern="**/vm-bundle.js*")
    assert "line" in str(excinfo.value)
    assert "find" in str(excinfo.value)
    assert fake.sent("setBreakpoint") == []


async def test_tool_sources_and_breakable_positions_accept_url_pattern():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js?v_token=aaa",
                          "sourceStartLine": 1},
                         {"actor": "s2", "url": "https://cdn.test/other.js",
                          "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY, "s2": ""}
    fake.columns = {"s1": {1: [0, 13]}}
    dbg = _dbg(fake)
    tool._STATE["dbg"] = dbg

    sources = await rdp_debugger(action="sources", url_pattern="**/vm-bundle.js*")
    assert [s["actor"] for s in sources["sources"]] == ["s1"]
    positions = await rdp_debugger(action="breakable_positions", url_pattern="**/vm-bundle.js*",
                                   line=1)
    assert positions["positions"]["1"]["columns_0based"] == [0, 13]


async def test_remove_breakpoint_supports_url_pattern_and_find_and_occurrence_validation():
    fake = FakeRdp()
    fake.sources_list = [{"actor": "s1", "url": "https://cdn.test/vm-bundle.js?v=aaa",
                          "sourceStartLine": 1}]
    fake.texts = {"s1": VM_BODY}
    fake.columns = {"s1": {1: [0, 13], 2: [0]}}
    dbg = _dbg(fake)
    tool._STATE["dbg"] = dbg

    with pytest.raises(RdpError, match="occurrence"):
        await rdp_debugger(action="set_breakpoint", url_pattern="**/vm-bundle.js*",
                           find=r"case (0x[0-9a-f]+):", occurrence=0)

    installed = await rdp_debugger(action="set_breakpoint", url_pattern="**/vm-bundle.js*",
                                   find=r"case (0x[0-9a-f]+):", occurrence=1)
    assert installed["installed"] is True

    removed = await rdp_debugger(action="remove_breakpoint", url_pattern="**/vm-bundle.js*")
    assert removed["removed"] == 1
    assert dbg.breakpoints == []


def test_cancel_during_reapply_locator_propagates_immediately_without_scanning_remaining_sources():
    fake = FakeRdp()
    fake.sources_list = [
        {"actor": f"s{i}", "url": f"https://cdn.test/vm-{i}.js?v={i}", "sourceStartLine": 1}
        for i in range(6)
    ]
    fake.texts = {f"s{i}": "var x = 1;\n" for i in range(6)}
    dbg = _dbg(fake)
    dbg.breakpoints.append({
        "location": {"sourceUrl": "https://cdn.test/vm-0.js?v=old", "line": 1, "column": 0},
        "options": {"condition": None, "logValue": None, "showStacktrace": None},
        "source_actor": None,
        "source_url": "https://cdn.test/vm-0.js?v=old",
        "line": 1,
        "column_0based": 0,
        "locator": {"find": r"case 0x99:", "occurrence": 1, "url_pattern": "**/vm-*.js*"},
    })

    pulled: list[str] = []
    orig_source_text = dbg.source_text

    def cancelling_source_text(actor, *args, **kwargs):
        pulled.append(actor)
        if actor == "s0":
            dbg.request_cancel("user cancelled reload")
        return orig_source_text(actor, *args, **kwargs)

    dbg.source_text = cancelling_source_text
    with pytest.raises(RdpError, match="cancel"):
        dbg._reapply_breakpoints()
    # s0 触发取消后绝不能吞掉异常继续拉 s1..s5，且 _cancel 保持置位直到下一次新操作入口
    assert pulled == ["s0"], pulled
    assert dbg._cancel.is_set() is True
