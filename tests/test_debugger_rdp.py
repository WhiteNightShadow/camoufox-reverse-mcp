"""RDP 协议层与工具层单元测试：不需要浏览器、不需要网络。

覆盖 review 逐条要求的点：
  * 分帧（半包 / 粘包）
  * `call` 按 actor 排队匹配回复：事件不是回复、错误回复按 message 里的类型分辨
  * 并发调用串行化（同一 socket 不会被两个线程同时读写）
  * 取消后工作线程退出
  * `resume` / `step` / `interrupt` 之后只接受**新到的** paused
  * `drain` 有预算、事件缓冲有上限（newSource 洪水）
  * longString 分片按 UTF-16 码元拼接（含 emoji、代理对边界、截断）
  * 启动配置继承 + user.js pref（端口能开的前提）
  * 可执行文件解析：macOS app 包、取激活版本而不是 mtime 最新
  * 断点：sourceUrl / sourceId、列号吸附、行上不可停时不报 ok、按原 location 删除
  * xhr 断点移除用当初设置时的 method
  * reload 先 detach 旧 target、重下断点
  * 工具层不把错误变成成功（evaluate / step / frames）
"""
import asyncio
import json
import os
import socket
import threading
import time
from collections import deque
from pathlib import Path

import pytest

from camoufox_reverse_mcp import camoufox_runtime
from camoufox_reverse_mcp.debugger_rdp_tools import (
    RDP_PREFS,
    FireFoxDebugger,
    RdpClient,
    RdpError,
    SELF_TEST_PROBE,
    build_launch_plan,
    detect_browser_executable,
    make_unix_socket_path,
    write_profile_prefs,
)
from camoufox_reverse_mcp.tools import debugger_rdp as tool
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


def _read_frame(conn) -> dict | None:
    """从 socket 读一个 RDP 帧（服务端视角的测试辅助）。"""
    buf = b""
    while b":" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            return None
        buf += chunk
    head, _, rest = buf.partition(b":")
    need = int(head)
    data = rest
    while len(data) < need:
        chunk = conn.recv(65536)
        if not chunk:
            return None
        data += chunk
    return json.loads(data[:need].decode("utf-8"))


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
            for frame in frames:
                conn.sendall(_frame(frame))
            time.sleep(2.0)  # 保持连接，等客户端读完
        except OSError:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


class FakeRdp:
    """按请求路由返回固定回复的假客户端，用来驱动 FireFoxDebugger 的会话逻辑。"""

    def __init__(self, routes=None):
        self.routes = list(routes or [])
        self.requests = []
        self.events = []
        self._seq = 100
        self._cancel = threading.Event()
        self.closed = False

    def route(self, type_name, reply):
        self.routes.append((lambda obj, t=type_name: obj.get("type") == t, reply))

    def types(self):
        return [request.get("type") for request in self.requests]

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

    def push_event(self, obj):
        self._seq += 1
        event = dict(obj, __seq=self._seq)
        self.events.append(event)
        return event

    def call(self, obj, timeout=20.0, aliases=()):
        self.requests.append(dict(obj))
        for predicate, reply in self.routes:
            if predicate(obj):
                return reply(obj) if callable(reply) else reply
        raise AssertionError("unexpected request: %r" % (obj,))

    def wait_event(self, predicate, timeout=20.0, since=None):
        floor = 0 if since is None else since
        for event in self.events:
            if event["__seq"] > floor and predicate(event):
                return event
        return None


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后都清掉工具层的全局会话，避免互相污染。"""
    tool.cleanup_session()
    yield
    tool.cleanup_session()


def _dbg_with_fake(**routes):
    dbg = FireFoxDebugger(port=1)
    fake = FakeRdp()
    for type_name, reply in routes.items():
        fake.route(type_name, reply)
    dbg.rdp = fake
    dbg.thread = "thread1"
    dbg.target = "target1"
    dbg.tab = "tab1"
    dbg.console = "console1"
    return dbg, fake


SOURCES = [
    {"actor": "source1", "url": "https://example.com/app.js", "introductionType": "scriptElement",
     "sourceStartLine": 1, "sourceStartColumn": 0, "sourceLength": 120},
    {"actor": "source2", "url": "https://example.com/app.js", "introductionType": "scriptElement",
     "sourceStartLine": 9, "sourceStartColumn": 8, "sourceLength": 40},
    {"actor": "source3", "url": None, "introductionType": "eval",
     "sourceStartLine": 1, "sourceStartColumn": 0, "sourceLength": 30},
]


# ---------------------------------------------------------------- 分帧
def test_framing_splits_partial_packet():
    """长度前缀协议：半个帧到达时不能吞掉，必须等齐。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    m1 = {"from": "root", "type": "hello", "n": 1}
    m2 = {"from": "root", "type": "world", "n": 2}
    raw = _frame(m1) + _frame(m2)

    b.sendall(raw[: len(_frame(m1)) + 4])  # 第二个帧只给了 4 字节
    assert client.recv(2) == m1
    b.sendall(raw[len(_frame(m1)) + 4:])
    assert client.recv(2) == m2
    client.close()
    b.close()


def test_framing_handles_sticky_packets():
    """两个帧一次到达：第一次 recv 只应返回第一个，剩余留在缓冲。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    b.sendall(_frame({"type": "one"}) + _frame({"type": "two"}))
    assert client.recv(2) == {"type": "one"}
    assert client.recv(2) == {"type": "two"}
    client.close()
    b.close()


def test_hello_reads_test_connection_prefix():
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    b.sendall(_frame({"from": "root", "applicationType": "browser",
                      "traits": {}, "testConnectionPrefix": "pfx-"}))
    msg = client.hello()
    assert msg["applicationType"] == "browser"
    assert client.prefix == "pfx-"

    captured = {}

    def reader():
        captured["raw"] = b.recv(65536)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    client.send({"to": "root", "type": "listTabs"})
    thread.join(3)
    payload = json.loads(captured["raw"].split(b":", 1)[1])
    assert payload["prefix"] == "pfx-"
    client.close()
    b.close()


# ---------------------------------------------------------------- 回复匹配
def test_call_skips_unrelated_events():
    """等待回复期间收到的广播事件应进 events，而不是被当成本次回复。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    _serve(b, {"from": "thread1", "type": "paused"},
           {"from": "root", "type": "listTabs", "tabs": []}, delay=0.05)
    reply = client.call({"to": "root", "type": "listTabs"}, 3)
    assert reply["type"] == "listTabs"
    assert any(event.get("type") == "paused" for event in client.events)
    client.close()


def test_call_does_not_take_same_actor_event_as_reply():
    """同一 actor 推来的事件（from 对得上）不能当回复（review #4）。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    _serve(b,
           {"from": "thread1", "type": "resumed"},
           {"from": "thread1", "type": "paused", "why": {"type": "breakpoint"}},
           {"from": "thread1", "frames": [{"displayName": "f"}]},
           delay=0.05)
    reply = client.call({"to": "thread1", "type": "frames"}, 3)
    assert reply.get("frames") == [{"displayName": "f"}]
    assert [event.get("type") for event in client.events if event.get("type")] == [
        "resumed", "paused"]
    client.close()


def test_call_ignores_stale_error_for_other_request():
    """上一条请求的延迟错误（message 点名了别的请求类型）不能判本次失败。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    _serve(b,
           {"from": "root", "type": "error", "error": "unrecognizedPacketType",
            "message": "unknown type 'sources'"},
           {"from": "root", "type": "listTabs", "tabs": ["ok"]},
           delay=0.05)
    reply = client.call({"to": "root", "type": "listTabs"}, 3)
    assert reply.get("tabs") == ["ok"]
    assert "error" not in reply
    client.close()


def test_call_returns_matching_error():
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    _serve(b, {"from": "root", "type": "error", "error": "badRequest",
               "message": "unknown type 'listTabs'"}, delay=0.05)
    reply = client.call({"to": "root", "type": "listTabs"}, 3)
    assert reply["error"] == "badRequest"
    client.close()


def test_call_returns_error_without_request_type_in_message():
    """review #4：错误回复的 message 里不含请求 type 时，不能当迟到的错误吞掉。

    `frame` actor 被回收后调 `getEnvironment` 收到 `noSuchActor`，消息里没有请求
    类型；必须立刻把它当成本次回复返回，而不是等满超时报一个误导性的 timeout。
    """
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    _serve(b, {"from": "frame9", "error": "noSuchActor",
               "message": "No such actor server1.conn0.child1/frame9"}, delay=0.05)
    started = time.monotonic()
    reply = client.call({"to": "frame9", "type": "getEnvironment"}, 3)
    assert reply["error"] == "noSuchActor"
    assert time.monotonic() - started < 2.0
    client.close()


def test_call_times_out_without_reply():
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    with pytest.raises(RdpError):
        client.call({"to": "root", "type": "listTabs"}, 0.3)
    client.close()
    b.close()


def test_drain_is_bounded_and_buffer_capped():
    """review #4：newSource 洪水既不能挡住请求发出，也不能让缓冲无限增长。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    client.events = deque(maxlen=3)

    def flood():
        payload = b"".join(_frame({"from": "thread1", "type": "newSource", "n": i})
                           for i in range(40))
        b.sendall(payload)
        time.sleep(0.05)
        _read_frame(b)
        b.sendall(_frame({"from": "root", "type": "listTabs", "tabs": ["ok"]}))
        time.sleep(1.0)

    threading.Thread(target=flood, daemon=True).start()
    started = time.monotonic()
    reply = client.call({"to": "root", "type": "listTabs"}, 5)
    elapsed = time.monotonic() - started
    assert reply.get("tabs") == ["ok"]
    assert len(client.events) <= 3, "事件缓冲必须封顶"
    assert elapsed < 4.0, "洪水不能让请求发不出去"
    client.close()
    b.close()


# ---------------------------------------------------------------- 新到的 paused
def test_wait_event_only_accepts_new_packets():
    """`since` 之前的 paused 不能被当成新的（review #4）。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=5)
    b.sendall(_frame({"from": "thread1", "type": "paused", "why": {"type": "step"}}))
    first = client.wait_event(lambda e: e.get("type") == "paused", 2)
    assert first["why"]["type"] == "step"
    mark = client.mark()
    b.sendall(_frame({"from": "thread1", "type": "paused", "why": {"type": "breakpoint"}}))
    second = client.wait_event(lambda e: e.get("type") == "paused", 2, since=mark)
    assert second["why"]["type"] == "breakpoint"
    # 旧事件照样在缓冲里，只是不会被当成新的
    assert any(event["why"]["type"] == "step" for event in client.events)
    client.close()
    b.close()


def test_resume_drops_stale_paused_before_new_pause():
    """review #4 的实测场景：单步后残留的 paused 不能被下一次等待当成新的。"""
    dbg, fake = _dbg_with_fake()
    stale = fake.push_event({"from": "thread1", "type": "paused",
                             "frame": {"actor": "f1", "where": {"line": 3}},
                             "why": {"type": "resumeLimit"}})
    assert dbg.wait_paused(0.1) is not None  # 先把它读掉
    fake.route("resume", {"from": "thread1"})
    assert dbg.resume()["status"] == "resumed"
    # 缓冲里还有这条旧的 paused，但不能被当成本次 resume 之后的暂停
    assert dbg.wait_paused(0.01) is None, "旧的 paused 被当成新的了"
    fresh = fake.push_event({"from": "thread1", "type": "paused",
                             "frame": {"actor": "f2", "where": {"line": 2}},
                             "why": {"type": "breakpoint"}})
    got = dbg.wait_paused(0.01)
    assert got is not None and got["__seq"] == fresh["__seq"]
    assert got["frame"]["where"]["line"] == 2
    assert stale["__seq"] < fresh["__seq"]


def test_step_raises_floor_and_rejects_buffered_paused():
    dbg, fake = _dbg_with_fake()
    fake.push_event({"from": "thread1", "type": "paused", "frame": {"actor": "f1"},
                     "why": {"type": "step"}})
    fake.route("resume", {"from": "thread1"})
    assert dbg.step("over", "f1")["status"] == "stepped"
    assert dbg.wait_paused(0.01) is None
    assert fake.sent("resume")[0]["resumeLimit"] == {"type": "next"}


def test_step_rejects_unknown_direction():
    """review #14：direction 写错要报错，不能抛 KeyError。"""
    dbg, _ = _dbg_with_fake()
    with pytest.raises(RdpError) as excinfo:
        dbg.step("sideways")
    assert "over / into / out" in str(excinfo.value)


def test_interrupt_sends_on_next_string():
    """review #9：Firefox 比较的是字符串 when == "onNext"。"""
    dbg, fake = _dbg_with_fake()

    def interrupt_on_next(obj):
        # 真实服务器在 onNext 模式下先回空包，之后（下一个语句）才推 paused
        fake.push_event({"from": "thread1", "type": "paused",
                         "frame": {"actor": "f1", "where": {"line": 1}},
                         "why": {"type": "interrupted", "onNext": True}})
        return {"from": "thread1"}

    fake.route("interrupt", interrupt_on_next)
    paused = dbg.interrupt(on_next=True, timeout=5)
    request = fake.sent("interrupt")[0]
    assert request["when"] == "onNext", request
    assert paused is not None and paused["why"]["onNext"] is True

    dbg2, fake2 = _dbg_with_fake()

    def interrupt_now(obj):
        fake2.push_event({"from": "thread1", "type": "paused", "frame": {"actor": "f1"},
                          "why": {"type": "interrupted", "onNext": False}})
        return {"from": "thread1"}

    fake2.route("interrupt", interrupt_now)
    dbg2.interrupt(on_next=False, timeout=5)
    assert fake2.sent("interrupt")[0]["when"] == {}


# ---------------------------------------------------------------- 并发 / 取消
def test_concurrent_calls_are_serialized():
    """review #3：两个线程同时调用不能串结果（同一 socket 只能有一个读者）。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=10)
    state = {"unanswered": False, "overlap": False}
    lock = threading.Lock()

    def server():
        try:
            b.settimeout(10)
            while True:
                request = _read_frame(b)
                if request is None:
                    return
                with lock:
                    if state["unanswered"]:
                        state["overlap"] = True
                    state["unanswered"] = True
                time.sleep(0.15)
                b.sendall(_frame({"from": request.get("to"), "type": request.get("type"),
                                  "echo": request.get("n")}))
                with lock:
                    state["unanswered"] = False
        except OSError:
            return

    threading.Thread(target=server, daemon=True).start()
    results = {}

    def worker(n):
        results[n] = client.call({"to": "root", "type": "ping", "n": n}, 10)

    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2, 3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert results and all(reply.get("echo") == n for n, reply in results.items()), results
    assert state["overlap"] is False, "两个请求在服务端同时未回复 -> 调用没有串行化"
    client.close()
    b.close()


def test_cancel_stops_blocked_call_quickly():
    """review #3：客户端取消后，正在等回复的工作线程要能退出。"""
    a, b = _pair()
    client = RdpClient(sock=a, timeout=10)
    errors = []

    def worker():
        try:
            client.call({"to": "root", "type": "listTabs"}, 30)
        except RdpError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(0.3)
    started = time.monotonic()
    client.cancel("client cancelled")
    thread.join(5)
    assert not thread.is_alive(), "取消后工作线程还在读 socket"
    assert errors and "cancel" in errors[0].lower()
    assert time.monotonic() - started < 3.0
    client.close()
    b.close()


def test_session_lock_serializes_operations():
    """同一 FireFoxDebugger 的公开方法被一把锁串起来（review #3）。"""
    dbg = FireFoxDebugger(port=1)
    fake = FakeRdp()
    dbg.rdp = fake
    dbg.thread = "thread1"
    active = {"n": 0, "max": 0}
    lock = threading.Lock()
    original = fake.call

    def slow_call(obj, timeout=20.0, aliases=()):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        try:
            time.sleep(0.1)
            return original(obj, timeout, aliases)
        finally:
            with lock:
                active["n"] -= 1

    fake.call = slow_call
    fake.route("frames", {"from": "thread1", "frames": []})
    threads = [threading.Thread(target=dbg.frames) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert active["max"] == 1, "同一会话的调用没有串行化（并发进入 %d）" % active["max"]


# ---------------------------------------------------------------- longString
class _Utf16Rdp:
    """按 UTF-16 码元实现 substring 的假客户端（模拟 Firefox LongStringActor）。"""

    def __init__(self, text, source_reply=None):
        self.units = text.encode("utf-16-le")
        self.text = text
        self.source_reply = source_reply
        self.calls = []
        self._cancel = threading.Event()

    def call(self, obj, timeout=20.0, aliases=()):
        self.calls.append(dict(obj))
        if obj.get("type") in ("source", "getSourceText"):
            return dict(self.source_reply, **{"from": obj["to"]})
        if obj.get("type") == "substring":
            start, end = int(obj["start"]), int(obj["end"])
            raw = self.units[start * 2:end * 2]
            return {"from": obj["to"],
                    "substring": raw.decode("utf-16-le", "surrogatepass")}
        raise AssertionError("unexpected request: %r" % (obj,))

    def mark(self):
        return 0

    def drain(self, idle=0.0, budget=0.0):
        return 0

    def wait_event(self, predicate, timeout=20.0, since=None):
        return None


def test_source_text_reassembles_long_string():
    """review #15：文本要真的跨多片（原来的用例一片就到头，测不到拼接）。"""
    text = "var a=1;" * 60000 + "\n" + "console.log('x');" * 60000
    actor = "server1.conn0.child1/longstractor9"
    fake = _Utf16Rdp(text, {"source": {"type": "longString", "actor": actor,
                                       "length": len(text)}})
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    got = dbg.source_text("source9", max_chars=2_000_000)
    assert got["text"] == text
    assert got["truncated"] is False
    substrings = [call for call in fake.calls if call["type"] == "substring"]
    assert len(substrings) > 1, "必须真的分片"
    assert substrings[0]["start"] == 0
    assert all(call["to"] == actor for call in substrings)


def test_long_string_handles_emoji_across_chunk_boundaries():
    """review #13：偏移按 UTF-16 码元推进；代理对跨分片时不能错位/半个字符。"""
    text = "var a=1;" * 20 + "😀😀" + "var b=2;" * 20 + "𝒳" + "tail" * 10
    units = len(text.encode("utf-16-le")) // 2
    fake = _Utf16Rdp(text, {"source": {"type": "longString",
                                       "actor": "longstring1", "length": units}})
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    got = dbg.source_text("source1", max_chars=1_000_000)
    assert got["text"] == text, "含 emoji 的 longString 拼接错位"
    got["text"].encode("utf-8")  # 不能有孤立代理项


def test_long_string_chunk_boundary_uses_code_units():
    """分片大小故意设成会把代理对切开，拼接结果仍然正确。"""
    text = "a" + "😀" + "b" * 10
    units = len(text.encode("utf-16-le")) // 2
    fake = _Utf16Rdp(text, {"source": {"type": "longString", "actor": "ls1",
                                       "length": units}})
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    text_out, info = dbg._read_long_string("ls1", units, 1000, 20, step=1)
    assert text_out == text, "step=1 时也不能把代理对切开"
    assert info["truncated"] is False


def test_long_string_truncation_is_explicit_and_safe():
    """review #13：超过 max_chars 要显式提示，而且不能留下半个代理对。"""
    text = "x" * 10 + "😀" + "y" * 10
    units = len(text.encode("utf-16-le")) // 2
    fake = _Utf16Rdp(text, {"source": {"type": "longString",
                                       "actor": "ls2", "length": units}})
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    # 11 个码元正好切在 emoji 的代理对中间
    text_out, info = dbg._read_long_string("ls2", units, 11, 20, step=200_000)
    assert info["truncated"] is True
    assert text_out == "x" * 10, "截断必须丢掉不完整的代理对"
    text_out.encode("utf-8")  # 不炸 UnicodeEncodeError


def test_source_text_plain_string_source():
    fake = FakeRdp()
    fake.route("source", {"from": "source1", "source": "console.log(1)"})
    dbg = FireFoxDebugger(port=1)
    dbg.rdp = fake
    got = dbg.source_text("source1")
    assert got["text"] == "console.log(1)"
    assert got["truncated"] is False


# ---------------------------------------------------------------- 启动配置
def test_write_profile_prefs_contains_rdp_prefs(tmp_path):
    """review #1：user.js 里必须有让调试端口能开的三个 pref。"""
    path = write_profile_prefs(tmp_path, {**RDP_PREFS, "intl.locale.requested": "fr-FR",
                                          "network.proxy.type": 1,
                                          "media.peerconnection.enabled": False})
    body = Path(path).read_text(encoding="utf-8")
    assert 'user_pref("devtools.debugger.remote-enabled", true);' in body
    assert 'user_pref("devtools.chrome.enabled", true);' in body
    assert 'user_pref("devtools.debugger.prompt-connection", false);' in body
    assert 'user_pref("intl.locale.requested", "fr-FR");' in body
    assert 'user_pref("network.proxy.type", 1);' in body
    assert 'user_pref("media.peerconnection.enabled", false);' in body


def test_build_launch_plan_inherits_server_config(tmp_path, monkeypatch):
    """review #2：调试浏览器要继承 launch_browser 的配置（指纹/代理/headless/os/locale）。"""
    from camoufox_reverse_mcp.browser import BrowserManager

    monkeypatch.setattr(BrowserManager, "default_config", {
        "proxy": {"server": "http://127.0.0.1:7890", "bypass": "localhost"},
        "headless": True,
        "os": "macos",
        "locale": "fr-FR",
    }, raising=False)
    monkeypatch.setenv("CAMOUFOX_EXE", str(tmp_path / "camoufox.exe"))
    (tmp_path / "camoufox.exe").write_bytes(b"")

    def fake_launch_options(**kwargs):
        assert kwargs["headless"] is True
        assert kwargs["os"] == "macos"
        assert kwargs["locale"] == "fr-FR"
        assert kwargs["proxy"] == {"server": "http://127.0.0.1:7890", "bypass": "localhost"}
        return {"executable_path": str(tmp_path / "camoufox.exe"),
                "args": ["-foreground"],
                "env": {"CAMOU_CONFIG_1": '{"a":1}'},
                "firefox_user_prefs": {"intl.locale.requested": "fr-FR"},
                "headless": True}

    import camoufox.utils as cfx_utils

    monkeypatch.setattr(cfx_utils, "launch_options", fake_launch_options)

    plan = build_launch_plan(port=6123, profile=tmp_path, url="about:blank")
    cmd = plan["cmd"]
    assert cmd[0].endswith("camoufox.exe")
    assert "-headless" in cmd
    assert "-no-remote" in cmd
    assert "-profile" in cmd
    assert "-start-debugger-server" in cmd and "6123" in cmd
    assert "about:blank" in cmd
    assert "-foreground" in cmd
    assert plan["env"]["CAMOU_CONFIG_1"] == '{"a":1}'
    for key in RDP_PREFS:
        assert key in plan["prefs"]
    assert plan["prefs"]["intl.locale.requested"] == "fr-FR"
    assert plan["prefs"]["network.proxy.type"] == 1
    assert plan["prefs"]["network.proxy.http"] == "127.0.0.1"
    assert plan["prefs"]["network.proxy.http_port"] == 7890
    assert plan["prefs"]["network.proxy.no_proxies_on"] == "localhost"
    assert plan["fingerprint_config"] is True
    assert plan["warnings"] == []


def test_build_launch_plan_warns_on_proxy_auth(tmp_path, monkeypatch):
    from camoufox_reverse_mcp.browser import BrowserManager

    monkeypatch.setattr(BrowserManager, "default_config", {
        "proxy": {"server": "http://user:pass@127.0.0.1:7890"},
    }, raising=False)
    (tmp_path / "camoufox.exe").write_bytes(b"")
    monkeypatch.setenv("CAMOUFOX_EXE", str(tmp_path / "camoufox.exe"))

    import camoufox.utils as cfx_utils

    monkeypatch.setattr(cfx_utils, "launch_options",
                        lambda **kwargs: {"executable_path": str(tmp_path / "camoufox.exe"),
                                          "args": [], "env": {},
                                          "firefox_user_prefs": {}, "headless": False})
    plan = build_launch_plan(port=6123, url="about:blank")
    assert any("认证" in warning for warning in plan["warnings"]), plan["warnings"]
    # 认证信息不会被写进 pref，也不会泄漏成明文
    assert "user:pass" not in json.dumps(plan["prefs"])


def test_build_launch_plan_degrades_without_camoufox(tmp_path, monkeypatch):
    """camoufox launch_options 失败时也要能起（退化成裸 profile + 告警）。"""
    (tmp_path / "firefox.exe").write_bytes(b"")
    monkeypatch.setenv("CAMOUFOX_EXE", str(tmp_path / "firefox.exe"))

    import camoufox.utils as cfx_utils

    def boom(**kwargs):
        raise RuntimeError("no camoufox")

    monkeypatch.setattr(cfx_utils, "launch_options", boom)
    plan = build_launch_plan(port=6123, profile=tmp_path, url="about:blank")
    assert plan["cmd"][0].endswith("firefox.exe")
    assert all(key in plan["prefs"] for key in RDP_PREFS)
    assert any("launch_options" in warning for warning in plan["warnings"])


def test_launch_plan_requires_a_debug_transport():
    """既没有 port 也没有 unix_path 时命令行不会带调试端口 —— 必须直接报错。"""
    with pytest.raises(RdpError) as excinfo:
        build_launch_plan(port=0, url="about:blank")
    assert "port" in str(excinfo.value)


def test_launch_plan_marks_headless_arg(tmp_path, monkeypatch):
    (tmp_path / "camoufox.exe").write_bytes(b"")
    monkeypatch.setenv("CAMOUFOX_EXE", str(tmp_path / "camoufox.exe"))
    import camoufox.utils as cfx_utils

    monkeypatch.setattr(cfx_utils, "launch_options",
                        lambda **kwargs: {"executable_path": str(tmp_path / "camoufox.exe"),
                                          "args": [], "env": {}, "firefox_user_prefs": {},
                                          "headless": False})
    plan = build_launch_plan(port=6123, url="about:blank", config={"headless": True})
    assert "-headless" in plan["cmd"]


# ---------------------------------------------------------------- 可执行文件解析
def test_macos_app_bundle_launch_path(tmp_path, monkeypatch):
    """review #1 / 验收 4：macOS 的可执行文件在 Camoufox.app/Contents/MacOS/camoufox。"""
    pkgman = pytest.importorskip("camoufox.pkgman")
    if "mac" not in (getattr(pkgman, "LAUNCH_FILE", None) or {}):
        pytest.skip("installed camoufox has no macOS launcher entry")

    root = tmp_path / "official" / "158.0.1-beta.41"
    macos = root / "Camoufox.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "camoufox").write_bytes(b"")
    monkeypatch.setattr(pkgman, "OS_NAME", "mac")

    try:
        resolved = pkgman.launch_path(root)
    except TypeError:  # 老版本 camoufox 的 launch_path 不接受参数
        pytest.skip("installed camoufox launch_path() takes no browser_path")
    assert Path(resolved) == macos / "camoufox"

    # detect_browser_executable() 用的也是这条路
    monkeypatch.delenv("CAMOUFOX_EXE", raising=False)
    monkeypatch.setattr(pkgman, "launch_path",
                        lambda browser_path=None: str(macos / "camoufox"))
    assert detect_browser_executable() == str(macos / "camoufox")


def test_detect_executable_prefers_active_build_not_mtime(tmp_path, monkeypatch):
    """review #1：装了多个版本时取当前激活的那个，不是 mtime 最新的。"""
    pkgman = pytest.importorskip("camoufox.pkgman")

    old_active = tmp_path / "zzz-old-active"
    new_other = tmp_path / "aaa-new-other"
    for root in (old_active, new_other):
        root.mkdir()
        (root / "camoufox.exe").write_bytes(b"")
    # 让"非激活"的那个 mtime 更新
    future = time.time() + 3600
    os.utime(new_other / "camoufox.exe", (future, future))

    runtime = {
        "active": {"path": str(old_active), "active": True},
        "installed": [
            {"path": str(new_other), "active": False},
            {"path": str(old_active), "active": True},
        ],
    }
    monkeypatch.setattr(camoufox_runtime, "inspect_camoufox_runtime", lambda: runtime)
    monkeypatch.delenv("CAMOUFOX_EXE", raising=False)
    monkeypatch.setattr(pkgman, "launch_path",
                        lambda browser_path=None: str(Path(browser_path) / "camoufox.exe"))
    assert detect_browser_executable() == str(old_active / "camoufox.exe")


def test_camoufox_exe_expands_home(tmp_path, monkeypatch):
    """review #14：CAMOUFOX_EXE 写 ~/... 也要能用。"""
    exe = tmp_path / "bin" / "camoufox.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("CAMOUFOX_EXE", "~/bin/camoufox.exe")
    assert detect_browser_executable() == str(exe)


def test_unix_socket_path_is_private():
    path = Path(make_unix_socket_path())
    assert path.parent.is_dir()
    if os.name != "nt":
        assert (path.parent.stat().st_mode & 0o077) == 0, "临时目录必须是 0700"
    for child in Path(path.parent).iterdir():
        child.unlink(missing_ok=True)
    Path(path.parent).rmdir()


def test_unix_socket_requires_platform_support(tmp_path):
    if hasattr(socket, "AF_UNIX"):
        pytest.skip("本平台支持 AF_UNIX")
    with pytest.raises(RdpError) as excinfo:
        RdpClient(unix_path=str(tmp_path / "rdp.sock"))
    assert "AF_UNIX" in str(excinfo.value)


# ---------------------------------------------------------------- 断点
def _breakpoint_dbg():
    dbg, fake = _dbg_with_fake()
    fake.route("sources", {"from": "thread1", "sources": SOURCES})

    def fake_positions(obj):
        data = {"10": [27, 30, 44], "11": [0]}
        query = obj.get("query")
        if query:
            line = str(query["start"]["line"])
            data = {line: data[line]} if line in data else {}
        return {"from": obj["to"], "positions": data}

    fake.route("getBreakpointPositionsCompressed", fake_positions)
    fake.route("getBreakableLines", lambda obj: {"from": obj["to"], "lines": [10, 11]})
    fake.route("setBreakpoint", lambda obj: {"from": obj["to"]})
    fake.route("removeBreakpoint", lambda obj: {"from": obj["to"]})
    return dbg, fake


def test_set_breakpoint_uses_source_url():
    """review #7：sourceUrl 形式，让同一 HTML 的后续内联脚本和导航后的新 source 都能挂上。"""
    dbg, fake = _breakpoint_dbg()
    out = dbg.set_breakpoint(10, source_url="https://example.com/app.js")
    assert out["installed"] is True
    request = fake.sent("setBreakpoint")[0]
    assert request["location"]["sourceUrl"] == "https://example.com/app.js"
    assert request["location"]["line"] == 10
    assert request["location"]["column"] == 27  # 该行第一个可停位置
    assert "sourceId" not in request["location"]


def test_set_breakpoint_accepts_source_actor_for_eval():
    """review #7：eval / new Function 没有 URL，必须能用 source actor 直接下断。"""
    dbg, fake = _dbg_with_fake()
    fake.route("sources", {"from": "thread1", "sources": SOURCES})
    fake.route("getBreakpointPositionsCompressed",
               lambda obj: {"from": obj["to"], "positions": {"1": [0, 7]}})
    fake.route("setBreakpoint", lambda obj: {"from": obj["to"]})
    out = dbg.set_breakpoint(1, source_actor="source3")
    assert out["installed"] is True
    request = fake.sent("setBreakpoint")[0]
    assert request["location"] == {"sourceId": "source3", "line": 1, "column": 0}


def test_set_breakpoint_snaps_column_and_reports():
    """review #8：列号 1-based -> 0-based，吸附到最近可停位置并说明。"""
    dbg, fake = _breakpoint_dbg()
    out = dbg.set_breakpoint(10, column=30, source_url="https://example.com/app.js")
    assert out["location"]["column_0based"] == 30
    assert out["snapped_from_column_1based"] == 30
    assert "吸附" in out["message"]
    assert out["breakable_columns_0based"] == [27, 30, 44]
    assert fake.sent("setBreakpoint")[0]["location"]["column"] == 30


def test_set_breakpoint_reports_unbreakable_line_instead_of_ok():
    """review #8：行上不可停时不能报 ok（原来的实现会静默什么都不装）。"""
    dbg, fake = _breakpoint_dbg()
    out = dbg.set_breakpoint(12, source_url="https://example.com/app.js")
    assert out["installed"] is False
    assert out["error"] == "no-breakable-position"
    assert 11 in out["nearest_lines"] and 12 not in out["nearest_lines"]
    assert fake.sent("setBreakpoint") == []


def test_set_breakpoint_requires_source_argument():
    """review #7 / #14：url_substr 为空时不能静默断到第一个脚本。"""
    dbg, fake = _breakpoint_dbg()
    with pytest.raises(RdpError) as excinfo:
        dbg.set_breakpoint(10, url_substr="")
    assert "source" in str(excinfo.value)
    assert fake.sent("setBreakpoint") == []


def test_set_breakpoint_rejects_ambiguous_url_substr():
    dbg, fake = _dbg_with_fake()
    fake.route("sources", {"from": "thread1", "sources": SOURCES + [
        {"actor": "source9", "url": "https://example.com/other.js", "sourceStartLine": 1}]})
    with pytest.raises(RdpError) as excinfo:
        dbg.set_breakpoint(1, url_substr="example.com")
    assert "多个 URL" in str(excinfo.value)


def test_remove_breakpoint_uses_exact_location():
    """review #6：服务端按 sourceUrl:sourceId:line:column 精确匹配，删除必须原样重发。"""
    dbg, fake = _breakpoint_dbg()
    set_out = dbg.set_breakpoint(10, column=28, source_url="https://example.com/app.js")
    assert set_out["installed"]
    removed = dbg.remove_breakpoints(line=10)
    assert removed["removed"] == 1
    request = fake.sent("removeBreakpoint")[0]
    assert request["location"] == fake.sent("setBreakpoint")[0]["location"]
    assert dbg.status()["breakpoints"] == []


def test_remove_breakpoint_without_filter_requires_explicit_choice():
    """没有过滤条件时不能顺手删光：要么给过滤条件，要么 remove_all=True。"""
    dbg, fake = _breakpoint_dbg()
    dbg.set_breakpoint(10, source_url="https://example.com/app.js")
    out = dbg.remove_breakpoints()
    assert out["removed"] == 0
    assert fake.sent("removeBreakpoint") == []
    out = dbg.remove_breakpoints(remove_all=True)
    assert out["removed"] == 1


def test_breakable_positions_shape():
    dbg, _ = _breakpoint_dbg()
    positions = dbg.breakable_positions("source1", 10)
    assert positions == {10: [27, 30, 44]}


# ---------------------------------------------------------------- xhr 断点
def test_remove_xhr_breakpoint_uses_recorded_method():
    """review #6：移除要用设置时的 method，不能把 "remove" 原样发过去。"""
    dbg, fake = _dbg_with_fake()
    fake.route("setXHRBreakpoint", {"from": "thread1", "value": True})
    fake.route("removeXHRBreakpoint", {"from": "thread1", "value": True})
    dbg.set_xhr_breakpoint("/api/x", "POST")
    assert fake.sent("setXHRBreakpoint")[0]["method"] == "POST"

    out = dbg.remove_xhr_breakpoints(path="/api/x")
    sent = fake.sent("removeXHRBreakpoint")
    assert len(sent) == 1
    assert sent[0]["method"] == "POST", sent
    assert sent[0]["path"] == "/api/x"
    assert out["removed"] == 1
    assert dbg.status()["xhr_breakpoints"] == []


def test_remove_all_xhr_breakpoints():
    dbg, fake = _dbg_with_fake()
    fake.route("setXHRBreakpoint", {"from": "thread1", "value": True})
    fake.route("removeXHRBreakpoint", {"from": "thread1", "value": True})
    dbg.set_xhr_breakpoint("/a", "ANY")
    dbg.set_xhr_breakpoint("/b", "GET")
    dbg.remove_xhr_breakpoints(remove_all=True)
    sent = [(item["path"], item["method"]) for item in fake.sent("removeXHRBreakpoint")]
    assert sorted(sent) == [("/a", "ANY"), ("/b", "GET")]
    assert dbg.status()["xhr_breakpoints"] == []


def test_remove_xhr_breakpoint_without_record_falls_back_to_any():
    dbg, fake = _dbg_with_fake()
    fake.route("removeXHRBreakpoint", {"from": "thread1", "value": True})
    out = dbg.remove_xhr_breakpoints(path="/api/x")
    assert fake.sent("removeXHRBreakpoint")[0]["method"] == "ANY"
    assert out["removed"] == 1


# ---------------------------------------------------------------- enable / reload
def test_enable_rejects_unmatched_url_substr():
    """review #14：匹配不到时列出可用标签页，不要静默绑第一个。"""
    dbg, fake = _dbg_with_fake()
    fake.route("listTabs", {"from": "root", "tabs": [
        {"actor": "tab1", "url": "https://a.example"},
        {"actor": "tab2", "url": "https://b.example"}]})
    with pytest.raises(RdpError) as excinfo:
        dbg.enable("nope.example")
    assert "没有匹配的标签页" in str(excinfo.value)
    assert "https://a.example" in str(excinfo.value)


def test_enable_requires_url_substr_with_multiple_tabs():
    dbg, fake = _dbg_with_fake()
    fake.route("listTabs", {"from": "root", "tabs": [
        {"actor": "tab1", "url": "https://a.example"},
        {"actor": "tab2", "url": "https://b.example"}]})
    dbg.tab = None
    with pytest.raises(RdpError) as excinfo:
        dbg.enable()
    assert "url_substr" in str(excinfo.value)


def test_enable_keeps_current_tab_and_detaches_old_target():
    """review #5：enable 不能跳回第一个标签页，换 target 时要 detach 旧的。"""
    dbg, fake = _dbg_with_fake()
    dbg.tab = "tab2"
    dbg.target = "old_target"
    fake.route("listTabs", {"from": "root", "tabs": [
        {"actor": "tab1", "url": "https://a.example"},
        {"actor": "tab2", "url": "https://b.example"}]})
    fake.route("getTarget", {"from": "tab2", "frame": {
        "actor": "new_target", "threadActor": "new_thread", "consoleActor": "console9"}})
    fake.route("detach", {"from": "old_target"})
    fake.route("isAttached", {"from": "new_thread", "value": True})
    info = dbg.enable()
    assert info["tab"] == "tab2"
    assert info["thread"] == "new_thread"
    assert fake.sent("detach")[0]["to"] == "old_target"
    assert fake.sent("detach")[0]["type"] == "detach"


def test_enable_twice_does_not_detach_live_target():
    """同一个 target 再 enable 一次不能把正在用的会话 detach 掉。"""
    dbg, fake = _dbg_with_fake()
    fake.route("listTabs", {"from": "root", "tabs": [{"actor": "tab1", "url": "u"}]})
    fake.route("getTarget", {"from": "tab1", "frame": {
        "actor": "target1", "threadActor": "thread1", "consoleActor": "console1"}})
    fake.route("isAttached", {"from": "thread1", "value": True})
    dbg.target = "target1"
    dbg.enable()
    assert fake.sent("detach") == []


def test_reload_reapplies_breakpoints_and_keeps_xhr():
    """review #5：reload 立刻 attach 新 thread 并重下断点，不等固定 sleep。"""
    dbg, fake = _dbg_with_fake()
    fake.route("sources", {"from": "thread1", "sources": SOURCES})
    fake.route("getBreakpointPositionsCompressed",
               lambda obj: {"from": obj["to"], "positions": {"10": [27]}})
    fake.route("setBreakpoint", lambda obj: {"from": obj["to"]})
    fake.route("setXHRBreakpoint", {"from": "thread1", "value": True})
    fake.route("reload", {"from": "target1"})
    fake.route("detach", {"from": "target1"})
    fake.route("isAttached", {"from": "thread2", "value": True})

    def get_target(obj):
        return {"from": obj["to"], "frame": {"actor": "target2", "threadActor": "thread2",
                                             "consoleActor": "console2"}}

    dbg.set_breakpoint(10, source_url="https://example.com/app.js")
    dbg.set_xhr_breakpoint("/api/x", "ANY")
    fake.route("getTarget", get_target)
    info = dbg.reload(wait=2.0)
    assert info["thread"] == "thread2"
    assert info["target"] == "target2"
    assert info["reapplied"], info
    assert info["xhr_reapplied"] == [{"path": "/api/x", "method": "ANY"}]
    assert fake.sent("detach")[0]["to"] == "target1"
    assert info["elapsed"] < 2.0
    set_requests = fake.sent("setBreakpoint")
    assert set_requests[-1]["location"] == set_requests[0]["location"]


def test_reload_reports_source_id_breakpoints_it_cannot_reapply():
    """sourceId 断点属于旧 thread，导航后不可能自动重下：要明确说出来而不是假装成功。"""
    dbg, fake = _dbg_with_fake()
    fake.route("sources", {"from": "thread1", "sources": SOURCES})
    fake.route("getBreakpointPositionsCompressed",
               lambda obj: {"from": obj["to"], "positions": {"1": [0]}})
    fake.route("setBreakpoint", lambda obj: {"from": obj["to"]})
    fake.route("reload", {"from": "target1"})
    fake.route("detach", {"from": "target1"})
    fake.route("isAttached", {"from": "thread2", "value": True})
    dbg.set_breakpoint(1, source_actor="source3")
    fake.route("getTarget", lambda obj: {"from": obj["to"], "frame": {
        "actor": "target2", "threadActor": "thread2", "consoleActor": "console2"}})
    info = dbg.reload(wait=2.0)
    assert info["reapply_skipped"], info
    assert "source_url" in info["reapply_skipped"][0]["hint"]


# ---------------------------------------------------------------- 工具层
def test_action_list_matches_expected_surface():
    assert len(ACTIONS) == 20
    for action in ("launch", "attach", "breakable_positions", "remove_breakpoint",
                   "xhr_breakpoint", "close"):
        assert action in ACTIONS


def test_self_test_stays_offline():
    """自测必须在 about:blank 上注入脚本，不能再去打第三方站点。"""
    import inspect

    from camoufox_reverse_mcp.debugger_rdp_tools import _self_test

    source = inspect.getsource(_self_test)
    assert "about:blank" in source
    assert "http://" not in source and "https://" not in source
    assert "http://" not in SELF_TEST_PROBE and "https://" not in SELF_TEST_PROBE


@pytest.mark.asyncio
async def test_status_without_session():
    result = await rdp_debugger(action="status")
    assert result["running"] is False


@pytest.mark.asyncio
async def test_unknown_action_rejected():
    with pytest.raises(RdpError):
        await rdp_debugger(action="definitely_not_an_action")


@pytest.mark.asyncio
async def test_actions_requiring_session_raise_clear_error():
    for action in ("enable", "sources", "frames", "wait_paused", "breakable_positions"):
        with pytest.raises(RdpError):
            await rdp_debugger(action=action)


@pytest.mark.asyncio
async def test_set_breakpoint_requires_line_with_real_session():
    """review #15：原来的用例因为没有会话，根本没走到"缺 line"的检查。"""
    dbg, fake = _breakpoint_dbg()
    tool._STATE["dbg"] = dbg
    with pytest.raises(RdpError) as excinfo:
        await rdp_debugger(action="set_breakpoint", url_substr="app.js")
    assert "line" in str(excinfo.value)
    assert fake.sent("setBreakpoint") == []


@pytest.mark.asyncio
async def test_tool_breakpoint_roundtrip_and_remove():
    dbg, fake = _breakpoint_dbg()
    tool._STATE["dbg"] = dbg
    created = await rdp_debugger(action="set_breakpoint", line=10,
                                 source_url="https://example.com/app.js")
    assert created["installed"] is True
    removed = await rdp_debugger(action="remove_breakpoint", line=10)
    assert removed["removed"] == 1


@pytest.mark.asyncio
async def test_tool_breakable_positions_reports_both_bases():
    dbg, fake = _breakpoint_dbg()
    tool._STATE["dbg"] = dbg
    out = await rdp_debugger(action="breakable_positions", source="source1", line=10)
    assert out["positions"]["10"]["columns_0based"] == [27, 30, 44]
    assert out["positions"]["10"]["columns_1based"] == [28, 31, 45]


@pytest.mark.asyncio
async def test_tool_xhr_remove_uses_recorded_method():
    dbg, fake = _dbg_with_fake()
    fake.route("setXHRBreakpoint", {"from": "thread1", "value": True})
    fake.route("removeXHRBreakpoint", {"from": "thread1", "value": True})
    tool._STATE["dbg"] = dbg
    await rdp_debugger(action="xhr_breakpoint", path="/api/x", method="POST")
    out = await rdp_debugger(action="xhr_breakpoint", path="/api/x", remove=True,
                             method="POST")
    assert out["removed"] == 1
    assert fake.sent("removeXHRBreakpoint")[0]["method"] == "POST"


@pytest.mark.asyncio
async def test_tool_xhr_legacy_remove_keyword_also_works():
    """`method="remove"` 仍然被当成移除，但发出去的是当初的 method。"""
    dbg, fake = _dbg_with_fake()
    fake.route("setXHRBreakpoint", {"from": "thread1", "value": True})
    fake.route("removeXHRBreakpoint", {"from": "thread1", "value": True})
    tool._STATE["dbg"] = dbg
    await rdp_debugger(action="xhr_breakpoint", path="/api/x", method="POST")
    out = await rdp_debugger(action="xhr_breakpoint", path="/api/x", method="remove")
    assert out["removed"] == 1
    assert fake.sent("removeXHRBreakpoint")[0]["method"] == "POST"


@pytest.mark.asyncio
async def test_tool_frames_returns_error_not_zero_count():
    """review #12：wrongState 不能变成 count: 0。"""
    dbg, fake = _dbg_with_fake()
    fake.route("frames", {"from": "thread1", "error": "wrongState",
                          "message": "Stack frames are only available while the debuggee is paused."})
    tool._STATE["dbg"] = dbg
    out = await rdp_debugger(action="frames")
    assert out["error"] == "wrongState"
    assert "count" not in out


@pytest.mark.asyncio
async def test_tool_step_returns_error_on_wrong_state():
    dbg, fake = _dbg_with_fake()
    fake.route("resume", {"from": "thread1", "error": "wrongState",
                          "message": "Can't resume when debuggee isn't paused."})
    tool._STATE["dbg"] = dbg
    out = await rdp_debugger(action="step", direction="into")
    assert out["error"] == "wrongState"
    assert "stepped" not in json.dumps(out)


@pytest.mark.asyncio
async def test_tool_scopes_returns_error():
    dbg, fake = _dbg_with_fake()
    fake.route("getEnvironment", {"from": "frame9", "error": "noSuchActor",
                                  "message": "No such actor"})
    tool._STATE["dbg"] = dbg
    out = await rdp_debugger(action="scopes", frame_actor="frame9")
    assert out["error"] == "noSuchActor"


@pytest.mark.asyncio
async def test_tool_evaluate_reports_paused_instead_of_nulls():
    """review #12：表达式命中断点时不能等满超时后返回一堆 null。"""
    dbg, fake = _dbg_with_fake()

    def evaluate_stops(obj):
        fake.push_event({"from": "thread1", "type": "paused",
                         "frame": {"actor": "f1", "where": {"line": 10, "column": 27}},
                         "why": {"type": "breakpoint"}})
        return {"from": "console1", "resultID": "rid-1"}

    fake.route("evaluateJSAsync", evaluate_stops)
    tool._STATE["dbg"] = dbg
    started = time.monotonic()
    out = await rdp_debugger(action="evaluate", expression="boom()", timeout_ms=5000)
    assert out["error"] == "paused-while-evaluating"
    assert out["paused"]["where"]["line"] == 10
    assert "result" not in out
    assert time.monotonic() - started < 3.0, "命中断点后应立刻返回，不能等满超时"


@pytest.mark.asyncio
async def test_tool_evaluate_materializes_long_string():
    """review #14：长字符串结果是 grip，工具顺手取回全文。"""
    dbg, fake = _dbg_with_fake()

    def evaluate_long(obj):
        fake.push_event({"from": "console1", "type": "evaluationResult", "resultID": "rid-2",
                         "result": {"type": "longString", "actor": "lsactor", "length": 10}})
        return {"from": "console1", "resultID": "rid-2"}

    fake.route("evaluateJSAsync", evaluate_long)
    fake.route("substring", {"from": "lsactor", "substring": "hello-long"})
    tool._STATE["dbg"] = dbg
    out = await rdp_debugger(action="evaluate", expression="bigString")
    assert out["result_text"] == "hello-long"
    assert out["longString"]["length"] == 10


@pytest.mark.asyncio
async def test_tool_launch_closes_previous_session(monkeypatch):
    """review #10：重复 launch 不能覆盖 `_STATE["dbg"]` 留下孤儿进程。"""
    closed = {}

    class FakeDebugger:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False

        def launch(self, url, wait_page=5.0):
            return {"port": 6123, "user_js": "/tmp/user.js", "executable": "camoufox",
                    "headless": True}

        def close(self):
            self.closed = True
            closed["called"] = True
            return {"killed_pids": 1, "profile_removed": True}

    previous = FakeDebugger()
    tool._STATE["dbg"] = previous
    monkeypatch.setattr(tool, "FireFoxDebugger", FakeDebugger)
    out = await rdp_debugger(action="launch", port=0, wait_page=0)
    assert closed.get("called") is True, "旧会话没有被关掉"
    assert out["status"] == "launched"
    assert tool._STATE["dbg"] is not previous
    assert tool._STATE["dbg"] is not None


@pytest.mark.asyncio
async def test_tool_launch_reports_cleanup_when_launch_fails(monkeypatch):
    """启动失败也要把自己拉起的浏览器清掉。"""
    state = {}

    class FakeDebugger:
        def __init__(self, **kwargs):
            state["instance"] = self

        def launch(self, url, wait_page=5.0):
            raise RdpError("port never opened")

        def close(self):
            state["closed"] = True
            return {}

    monkeypatch.setattr(tool, "FireFoxDebugger", FakeDebugger)
    with pytest.raises(RdpError):
        await rdp_debugger(action="launch", wait_page=0)
    assert state.get("closed") is True
    assert tool._STATE["dbg"] is None


@pytest.mark.asyncio
async def test_tool_status_and_close_roundtrip():
    dbg, fake = _dbg_with_fake()
    fake.route("sources", {"from": "thread1", "sources": SOURCES})
    tool._STATE["dbg"] = dbg
    status = await rdp_debugger(action="status")
    assert status["running"] is True
    assert status["thread"] == "thread1"
    closed = await rdp_debugger(action="close")
    assert closed["status"] == "closed"
    assert tool._STATE["dbg"] is None


@pytest.mark.asyncio
async def test_tool_attach_requires_port():
    with pytest.raises(RdpError) as excinfo:
        await rdp_debugger(action="attach", port=0)
    assert "port" in str(excinfo.value)


@pytest.mark.asyncio
async def test_tool_attach_unix_socket_needs_path():
    with pytest.raises(RdpError) as excinfo:
        await rdp_debugger(action="attach", unix_socket=True)
    assert "socket_path" in str(excinfo.value)


def test_cleanup_session_is_idempotent():
    tool._STATE["dbg"] = None
    tool.cleanup_session()
    assert tool._STATE["dbg"] is None


def test_install_cleanup_is_idempotent():
    """atexit / 信号处理只注册一次（review #10）。"""
    tool.install_cleanup()
    tool.install_cleanup()
    assert tool._CLEANUP_INSTALLED is True


def test_cleanup_session_closes_the_debugger():
    closed = {}

    class Dummy:
        def close(self):
            closed["called"] = True
            return {}

    tool._STATE["dbg"] = Dummy()
    tool.cleanup_session("test")
    assert closed.get("called") is True
    assert tool._STATE["dbg"] is None


@pytest.mark.asyncio
async def test_cancelled_tool_call_signals_the_worker(monkeypatch):
    """review #3：MCP 客户端取消请求时，要给工作线程发取消信号。"""
    dbg = FireFoxDebugger(port=1)
    calls = {}
    dbg.request_cancel = lambda reason="cancelled": calls.setdefault("reason", reason)
    tool._STATE["dbg"] = dbg

    def blocking(action, kw):
        time.sleep(1.0)
        return {"status": "late"}

    monkeypatch.setattr(tool, "_sync_dispatch", blocking)
    task = asyncio.ensure_future(rdp_debugger(action="status"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls.get("reason"), "取消后没有给工作线程发取消信号"


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
    reason="browser smoke test: set CAMOUFOX_RDP_SMOKE=1 and have a camoufox build",
)
@pytest.mark.asyncio
async def test_smoke_launch_breakpoint_remove_close(tmp_path):
    """真实浏览器冒烟：随机端口启动 → enable → 下断点 → 命中 → 删除 → close。"""
    page = tmp_path / "smoke.html"
    page.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        "<h1>smoke</h1><script>window.smoke=function(a){var b=a*2;return b+1};</script>"
        "</body></html>", encoding="utf-8")
    url = page.as_uri()
    info = await rdp_debugger(action="launch", url=url, port=0, wait_page=2.0)
    try:
        assert info["status"] == "launched", info
        assert info["port"], info
        assert info["headless"] in (True, False), info
        enabled = await rdp_debugger(action="enable")
        assert enabled["thread"], enabled
        evaluated = await rdp_debugger(action="evaluate", expression="1 + 1")
        assert evaluated["result"] == 2, evaluated

        sources = await rdp_debugger(action="sources")
        target = next((item for item in sources["sources"]
                       if str(item["url"] or "").startswith(url)), None)
        assert target, sources
        positions = await rdp_debugger(action="breakable_positions",
                                       source=target["actor"])
        lines = sorted(int(key) for key in positions["positions"])
        assert lines, positions
        created = await rdp_debugger(action="set_breakpoint", line=lines[0],
                                     source=target["actor"])
        assert created["installed"], created
        removed = await rdp_debugger(action="remove_breakpoint", line=lines[0])
        assert removed["removed"] == 1, removed
        status = await rdp_debugger(action="status")
        assert status["breakpoints"] == [], status
    finally:
        await rdp_debugger(action="close")
