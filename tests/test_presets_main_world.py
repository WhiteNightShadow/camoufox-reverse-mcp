"""v1.10.0 hook presets: installed in the page main world, restorable, page-safe."""

import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.tools import hooking, navigation
from camoufox_reverse_mcp.utils.presets import PRESET_FILES, canonical_preset, preset_expression

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")

# A vm context with small stand-ins for the browser APIs the presets wrap.
RUNNER = r"""
const vm = require('vm');
const {expression, scenario} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = vm.createContext({});
vm.runInContext(`
var window = globalThis;
window.EventTarget = class EventTarget {
  addEventListener(type, fn) { (this.__l || (Object.defineProperty(this, '__l', {value: {}}), this.__l))[type] =
    ((this.__l[type]) || []).concat([fn]); }
  _fire(type, ev) { ((this.__l || {})[type] || []).forEach((fn) => fn.call(this, ev || {})); }
}
window.XMLHttpRequest = class XMLHttpRequest extends EventTarget {
  open(m, u) { this._m = m; } setRequestHeader() {}
  send(b) { this.status = 200; this._fire('loadend'); }
  get responseText() { if (this.responseType && this.responseType !== 'text') throw new Error('InvalidStateError'); return 'ok'; }
}
window.WebSocket = class WebSocket extends EventTarget { constructor(url) { super(); this.url = url; } send(d) { this._sent = d; } }
WebSocket.CONNECTING = 0; WebSocket.OPEN = 1; WebSocket.CLOSING = 2; WebSocket.CLOSED = 3;
window.Headers = class Headers { constructor(o) { this._o = o; } forEach(fn) { for (const k in this._o) fn(this._o[k], k); } }
window.Request = class Request { constructor(u) { this.url = u; this.method = 'PUT'; } }
var fetch = async function fetch(url) { if (url === 'fail') throw new Error('nope'); return {status: 201, ok: true}; };
window.Document = class Document { get cookie() { return this._c || ''; } set cookie(v) { this._c = v; } }
var document = new Document();
window.Navigator = class Navigator { get userAgent() { return 'UA'; } get platform() { return 'P'; } }
var navigator = new Navigator();
window.HTMLCanvasElement = class HTMLCanvasElement { toDataURL() { return 'data:x'; } getContext(t) { return {t}; } }
var btoa = function btoa(s) { return 'b64:' + s; };
var atob = function atob(s) { return String(s).slice(4); };
`, ctx);
const before = vm.runInContext(`({open: XMLHttpRequest.prototype.open, fetch: window.fetch, F: Function,
  stringify: JSON.stringify, WS: window.WebSocket, cookie: Object.getOwnPropertyDescriptor(Document.prototype, 'cookie').get})`, ctx);
const info = vm.runInContext(expression, ctx);
(async () => {
  const out = await vm.runInContext('(async () => {' + scenario + '})()', ctx);
  const restored = vm.runInContext('window.__mcp_preset_uninstall()', ctx);
  const after = vm.runInContext(`({open: XMLHttpRequest.prototype.open, fetch: window.fetch, F: Function,
    stringify: JSON.stringify, WS: window.WebSocket, cookie: Object.getOwnPropertyDescriptor(Document.prototype, 'cookie').get})`, ctx);
  const same = Object.keys(before).filter((k) => before[k] === after[k]);
  const keys = vm.runInContext('Object.keys(window).filter(k => k.startsWith("__mcp_"))', ctx);
  process.stdout.write(JSON.stringify({info, out, restored, same, keys}));
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _run(preset: str, scenario: str) -> dict:
    payload = json.dumps({"expression": preset_expression(preset), "scenario": scenario})
    out = subprocess.run([NODE, "-e", RUNNER], input=payload, capture_output=True,
                         text=True, encoding="utf-8", check=False)
    assert out.returncode == 0, out.stderr[:800]
    data = json.loads(out.stdout)
    assert data["info"] == {"installed": True, "preset": canonical_preset(preset)}
    assert data["keys"] == [], "preset globals stay out of Object.keys(window)"
    assert set(data["same"]) == {"open", "fetch", "F", "stringify", "WS", "cookie"}, "uninstall restores"
    return data


@needs_node
def test_xhr_preset_records_page_requests_without_expandos():
    data = _run("xhr", """
      const desc = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, 'open');
      const x = new XMLHttpRequest(); x.open('POST', '/api'); x.setRequestHeader('X-Sign', 's'); x.send('a=1');
      const bin = new XMLHttpRequest(); bin.responseType = 'arraybuffer'; bin.open('GET', '/bin'); bin.send();
      return {log: window.__mcp_xhr_log, own: Object.getOwnPropertyNames(x).filter(k => k.startsWith('__mcp')),
              writable: desc.writable, configurable: desc.configurable,
              str: XMLHttpRequest.prototype.open.toString()};
    """)
    out = data["out"]
    assert [(e["method"], e["url"], e["status"]) for e in out["log"]] == [("POST", "/api", 200), ("GET", "/bin", 200)]
    assert out["log"][0]["headers"] == {"X-Sign": "s"} and out["log"][0]["body"] == "a=1"
    assert "response_length" not in out["log"][1], "arraybuffer responses are not read as text"
    assert out["own"] == [] and out["writable"] is True and out["configurable"] is True
    assert "[native code]" in out["str"]
    assert "XMLHttpRequest.prototype.open" in data["restored"]["restored"]


@needs_node
def test_fetch_preset_keeps_promise_behaviour():
    data = _run("fetch", """
      const ok = await fetch('/a', {method: 'POST', headers: new Headers({'x-a': '1'}), body: 'b'});
      let err = null; try { await fetch('fail'); } catch (e) { err = e.message; }
      await fetch(new Request('/r'));
      return {status: ok.status, err, log: window.__mcp_fetch_log, init: window.__mcp_fetch_initiator_log.length};
    """)
    out = data["out"]
    assert out["status"] == 201 and out["err"] == "nope"
    assert [(e["url"], e["method"]) for e in out["log"]] == [("/a", "POST"), ("fail", "GET"), ("/r", "PUT")]
    assert out["log"][0]["headers"] == {"x-a": "1"} and out["log"][0]["ok"] is True
    assert out["log"][1]["error"] == "nope" and out["init"] == 3


@needs_node
def test_crypto_preset_calls_stringify_once():
    data = _run("crypto", """
      let calls = 0;
      const v = {toJSON() { calls++; return {k: 1}; }};
      const s = JSON.stringify(v);
      let circular = null; const c = {}; c.self = c;
      try { JSON.stringify(c, (k, val) => k === 'self' ? undefined : val); } catch (e) { circular = e.message; }
      const b = btoa(123);
      return {s, calls, circular, b, log: window.__mcp_crypto_log.map(e => [e.func, e.input || null])};
    """)
    out = data["out"]
    assert out["s"] == '{"k":1}' and out["calls"] == 1 and out["circular"] is None
    assert out["b"] == "b64:123" and ["btoa", "[number]"] in out["log"]


@needs_node
def test_cookie_websocket_debugger_and_runtime_presets():
    cookie = _run("cookie_hook", """
      document.cookie = 'a=1'; const v = document.cookie;
      return window.__mcp_cookie_log.map(e => [e.op, e.value]);
    """)
    assert cookie["out"] == [["set", "a=1"], ["get", "a=1"]]

    ws = _run("websocket", """
      const s = new WebSocket('wss://x/'); s.send('hi'); s._fire('message', {data: 'yo'});
      let plain = null; try { WebSocket('wss://y/'); } catch (e) { plain = 'threw'; }
      return {log: window.__mcp_ws_log, own: Object.keys(s), open: WebSocket.OPEN, plain};
    """)
    assert ws["out"]["log"][0]["url"] == "wss://x/" and ws["out"]["open"] == 1
    assert [m["direction"] for m in ws["out"]["log"][0]["messages"]] == ["send", "recv"]
    assert "send" not in ws["out"]["own"] and ws["out"]["plain"] == "threw"

    dbg = _run("debugger_bypass", """
      const f = Function('debugger; return 7');
      return {r: f(), src: f.toString().includes('debugger'), timer: setInterval(function () { debugger; }, 10)};
    """)
    assert dbg["out"] == {"r": 7, "src": False, "timer": 0}

    probe = _run("runtime_probe", """
      navigator.userAgent; const x = new XMLHttpRequest(); x.open('GET', '/p'); x.send();
      return window.__mcp_runtime_log.map(e => e.type);
    """)
    assert probe["out"] == ["nav_read", "xhr_open", "xhr_send"]


class _Page:
    url = "https://example.test/"

    def __init__(self):
        self.sent = []
        self.frames = []

    async def evaluate(self, script):
        assert script.startswith("mw:"), "presets must not run in the isolated world"
        if "Promise.resolve(null)" in script:
            return {"__mcp_native_ok": True, "value": None}
        self.sent.append(script)
        return {"__mcp_native_ok": True, "value": {"installed": True, "preset": "xhr"}}

    async def add_init_script(self, script=None):
        raise AssertionError("persistent=False installs in the current document only")


@pytest.mark.parametrize("persistent", [True, False])
async def test_inject_hook_preset_uses_the_main_world(monkeypatch, persistent):
    page = _Page()
    registered = []

    class Manager:
        _init_scripts: list = []

        async def get_active_page(self):
            return page

        async def add_persistent_script(self, name, content):
            registered.append((name, content))

    monkeypatch.setattr(hooking, "browser_manager", Manager())
    result = await hooking.inject_hook_preset("xhr", persistent=persistent)
    assert result["world"] == "main" and result["execution_backend"] == "camoufox_native"
    assert "__mcp_xhr_log" in result["read_with"]
    assert len(page.sent) == 1 and "__mcpDefine" in page.sent[0]
    if persistent:
        [(name, content)] = registered
        assert name == "preset:xhr" and content.startswith("mw:")
        assert "[MCP preset] main-world install failed" in content
    else:
        assert registered == []


async def test_pre_inject_names_go_to_the_main_world(monkeypatch):
    calls = []

    class Manager:
        async def add_persistent_script(self, name, content):
            calls.append((name, content))

    monkeypatch.setattr(navigation, "browser_manager", Manager())
    for name in ("cookie", "cookie_hook", "xhr"):
        ok, msg = await navigation._inject_hook_by_name(name)
        assert ok, msg
    assert [c[0] for c in calls] == ["pre_inject:cookie", "pre_inject:cookie_hook", "pre_inject:xhr"]
    assert all(c[1].startswith("mw:") and "__mcp_preset_restore" in c[1] for c in calls)
    assert set(PRESET_FILES) >= {"cookie", "xhr"}


async def test_request_initiator_reads_preset_logs_in_the_main_world(monkeypatch):
    from camoufox_reverse_mcp.tools import network

    seen = []

    async def fake_eval(target, script, world, **kw):
        seen.append(world)
        return {"url": "https://a/x", "stack": "f@x:1:1", "type": "xhr", "method": "GET"}, "camoufox_native", None

    manager = SimpleNamespace(_network_requests=[{"id": 1, "url": "https://a/x"}],
                              get_active_page=AsyncMock(return_value=object()))
    monkeypatch.setattr(network, "browser_manager", manager)
    monkeypatch.setattr(network, "evaluate_in_world", fake_eval)
    result = await network.get_request_initiator(1)
    assert seen == ["main"] and result["initiator_stack"] == "f@x:1:1"
