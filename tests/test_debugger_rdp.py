"""RDP 协议层单元测试：不需要浏览器、不需要网络。

覆盖 review 关注的几点：
  * 分帧（半包 / 粘包）
  * `call` 只接受属于自己的回复，无关事件进缓冲
  * 错位的 error 回复不会被当成本次请求的失败
  * 大脚本 `longString` 分片取回
  * `rdp_debugger` action 分发的基本行为（无会话时报错、未知 action 报错）
"""
import json
import os
import socket
import threading
import time

import pytest

from camoufox_reverse_mcp.debugger_rdp_tools import (
    _self_test,
    FireFoxDebugger,
    RdpClient,
    RdpError,
    SELF_TEST_PROBE,
    detect_browser_executable,
)
from camoufox_reverse_mcp.tools.debugger_rdp import ACTIONS, rdp_debugger


# ---------------------------------------------------------------- helpers
def _frame(obj) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data


def _pair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    return a, b


def _serve(conn, *frames, delay=0.0):
    """后台线程：先等到客户端发出请求，再按顺序回帧。

    先读请求是必要的——`call()` 开头会 drain 一次，若帧在请求前就到齐，
    会被 drain 收进 events 缓冲，测试就测不到「等待期间收到广播」的行为。
    """
    def run():
        try:
            conn.settimeout(5)
            try:
                conn.recv(65536)  # 丢掉请求本身
            except OSError:
                pass
            if delay:
                time.sleep(delay)
            for f in frames:
                conn.sendall(_frame(f))
            time.sleep(3.0)  # 保持连接，等客户端读完
        except OSError:
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- 分帧
def test_framing_splits_partial_packet():
    """长度前缀协议：半个帧到达时不能吞掉，必须等齐。"""
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    m1 = {"from": "root", "type": "hello", "n": 1}
    m2 = {"from": "root", "type": "world", "n": 2}
    raw = _frame(m1) + _frame(m2)

    b.sendall(raw[: len(_frame(m1)) + 4])  # 第二个帧只给了 4 字节
    assert c.recv(2) == m1
    b.sendall(raw[len(_frame(m1)) + 4:])
    assert c.recv(2) == m2
    c.close()
    b.close()


def test_framing_handles_sticky_packets():
    """两个帧一次到达：第一次 recv 只应返回第一个，剩余留在缓冲。"""
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    b.sendall(_frame({"type": "one"}) + _frame({"type": "two"}))
    assert c.recv(2) == {"type": "one"}
    assert c.recv(2) == {"type": "two"}
    c.close()
    b.close()


def test_hello_reads_test_connection_prefix():
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    b.sendall(_frame({"from": "root", "applicationType": "browser",
                      "traits": {}, "testConnectionPrefix": "pfx-"}))
    msg = c.hello()
    assert msg["applicationType"] == "browser"
    assert c.prefix == "pfx-"

    # 之后的请求要自动带上 prefix
    captured = {}

    def reader():
        captured["raw"] = b.recv(65536)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    c.send({"to": "root", "type": "listTabs"})
    t.join(3)
    payload = json.loads(captured["raw"].split(b":", 1)[1])
    assert payload["prefix"] == "pfx-"
    c.close()
    b.close()


# ---------------------------------------------------------------- call 匹配
def test_call_skips_unrelated_events():
    """等待回复期间收到的广播事件应进 events，而不是被当成本次回复。"""
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    _serve(b, {"from": "server1.conn0.child1/thread1", "type": "paused"},
           {"from": "root", "type": "listTabs", "tabs": []}, delay=0.05)
    reply = c.call({"to": "root", "type": "listTabs"}, 3)
    assert reply["type"] == "listTabs"
    assert any(e.get("type") == "paused" for e in c.events)
    c.close()


def test_call_ignores_stale_error_for_other_request():
    """上一条请求的延迟错误（from==to 但 message 不含本次 type）不能判本次失败。"""
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    _serve(b,
           {"from": "root", "type": "error", "error": "unrecognizedPacketType",
            "message": "unknown type 'sources'"},
           {"from": "root", "type": "listTabs", "tabs": ["ok"]},
           delay=0.05)
    reply = c.call({"to": "root", "type": "listTabs"}, 3)
    assert reply.get("tabs") == ["ok"]
    assert "error" not in reply
    c.close()


def test_call_returns_matching_error():
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    _serve(b, {"from": "root", "type": "error", "error": "badRequest",
               "message": "unknown type 'listTabs'"}, delay=0.05)
    reply = c.call({"to": "root", "type": "listTabs"}, 3)
    assert reply["error"] == "badRequest"
    c.close()


def test_call_times_out_without_reply():
    a, b = _pair()
    c = RdpClient(sock=a, timeout=5)
    with pytest.raises(RdpError):
        c.call({"to": "root", "type": "listTabs"}, 0.3)
    c.close()
    b.close()


# ---------------------------------------------------------------- longString
class _FakeRdp:
    """只实现 call()，用来驱动 source_text 的 longString 分支。"""

    def __init__(self, source_reply, chunks):
        self.source_reply = source_reply
        self.chunks = chunks
        self.calls = []

    def call(self, obj, timeout=20.0):
        self.calls.append(dict(obj))
        if obj.get("type") in ("source", "getSourceText"):
            return dict(self.source_reply, **{"from": obj["to"]})
        if obj.get("type") == "substring":
            start, end = obj["start"], obj["end"]
            return {"from": obj["to"], "substring": self.chunks[start:end]}
        raise AssertionError("unexpected request: %r" % (obj,))


def test_source_text_reassembles_long_string():
    text = "var a=1;" * 50
    actor = "server1.conn0.child1/longstractor9"
    fake = _FakeRdp(
        {"source": {"type": "longString", "actor": actor, "length": len(text)}},
        text,
    )
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    out = dbg.source_text("server1.conn0.child1/source4", max_chars=10_000)
    assert out == text
    substrings = [c for c in fake.calls if c["type"] == "substring"]
    assert substrings, "longString 必须走 substring 分片"
    assert all(c["to"] == actor for c in substrings)
    assert substrings[0]["start"] == 0


def test_source_text_plain_string_source():
    fake = _FakeRdp({"source": "console.log(1)"}, "")
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    assert dbg.source_text("src1") == "console.log(1)"


# ---------------------------------------------------------------- 工具分发
def test_self_test_stays_offline():
    """自测必须在 about:blank 上注入脚本，不能再去打第三方站点。"""
    import inspect

    src = inspect.getsource(_self_test)
    assert "about:blank" in src
    assert "http://" not in src and "https://" not in src
    assert "http://" not in SELF_TEST_PROBE and "https://" not in SELF_TEST_PROBE


def test_action_list_matches_expected_surface():
    assert "launch" in ACTIONS and "set_breakpoint" in ACTIONS
    assert len(ACTIONS) == 18


@pytest.mark.asyncio
async def test_status_without_session():
    r = await rdp_debugger(action="status")
    assert r["running"] is False


@pytest.mark.asyncio
async def test_unknown_action_rejected():
    with pytest.raises(RdpError):
        await rdp_debugger(action="definitely_not_an_action")


@pytest.mark.asyncio
async def test_actions_requiring_session_raise_clear_error():
    for act in ("enable", "sources", "frames", "wait_paused"):
        with pytest.raises(RdpError):
            await rdp_debugger(action=act)


@pytest.mark.asyncio
async def test_set_breakpoint_requires_line():
    with pytest.raises(RdpError):
        await rdp_debugger(action="set_breakpoint", url_substr="app.js")


# ---------------------------------------------------------------- 浏览器冒烟
def _browser_executable() -> str | None:
    try:
        return detect_browser_executable()
    except Exception:
        return None


SMOKE_ENABLED = (
    os.environ.get("CAMOUFOX_RDP_SMOKE") == "1" and _browser_executable() is not None
)


@pytest.mark.skipif(
    not SMOKE_ENABLED,
    reason="browser smoke test: set CAMOUFOX_RDP_SMOKE=1 and have a Firefox/camoufox build",
)
@pytest.mark.asyncio
async def test_smoke_launch_enable_sources_close():
    """真实浏览器冒烟：启动 → enable → sources 有内容 → close。

    默认跳过（CI 上没有浏览器/显示），需要时设 CAMOUFOX_RDP_SMOKE=1。
    """
    import random
    port = random.randint(6200, 6400)
    page = "data:text/html,<meta charset=utf-8><script>var smoke=1;</script><h1>smoke</h1>"
    info = await rdp_debugger(action="launch", url=page, port=port, wait_page=1.5)
    try:
        assert info["status"] == "launched"
        enabled = await rdp_debugger(action="enable")
        assert enabled["thread"], enabled
        evaluated = await rdp_debugger(action="evaluate", expression="1 + 1")
        assert evaluated["result"] == 2
        # 注入一个新脚本，确保 sources 里确实能看到脚本（data: 页的内联脚本
        # 在 attach 之前就执行完并可能被回收，不保证能被列举）
        await rdp_debugger(
            action="evaluate",
            expression=("var s=document.createElement('script');"
                        "s.textContent='window.__rdp_smoke=1;';"
                        "document.documentElement.appendChild(s);'ok'"))
        time.sleep(0.5)
        srcs = await rdp_debugger(action="sources")
        assert srcs["total"] >= 1, srcs
    finally:
        await rdp_debugger(action="close")
