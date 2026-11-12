"""v1.10.0 JSVMP probes: child frames log into the top frame, seq/dropped, coverage."""

import json
import shutil
import subprocess

import pytest

from camoufox_reverse_mcp.tools import jsvmp
from camoufox_reverse_mcp.utils.jsvmp_hooks import proxy_probe, transparent_probe

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")

# Two vm contexts stand for a page and a same-origin iframe: the child's
# `parent` is the top global. A third context has a parent that throws like a
# cross-origin WindowProxy.
RUNNER = r"""
const vm = require('vm');
const {probe, maxEntries} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const STUBS = (href) =>
  'var window = globalThis; var performance = {now: function () { return 0; }};' +
  'class Navigator { get userAgent() { return "UA"; } get platform() { return "P"; } }' +
  'var navigator = new Navigator(); var screen = {}; var history = {};' +
  'var localStorage = {}; var sessionStorage = {}; var document = {};' +
  'var location = {href: ' + JSON.stringify(href) + '};';
function frame(href) {
  const ctx = vm.createContext({console: {log() {}}});
  vm.runInContext(STUBS(href), ctx);
  return ctx;
}
const top = frame('https://example.test/');
vm.runInContext('window.parent = window;', top);
const child = frame('about:blank');
child.parent = vm.runInContext('globalThis', top);
const hidden = frame('https://other.test/');
hidden.parent = new Proxy({}, {get(t, k) { if (k === 'parent') return undefined; throw new Error('SecurityError'); }});
const installs = [top, child, hidden].map((ctx) => vm.runInContext(probe, ctx));
for (const ctx of [child, hidden]) {
  vm.runInContext('navigator.userAgent; (function f(a) { return a; }).call(null, 1);', ctx);
}
vm.runInContext('navigator.platform;', top);
const read = (ctx) => vm.runInContext('JSON.stringify([window.__mcp_jsvmp_log, window.__mcp_jsvmp_meta, Object.keys(window).filter(k => k.startsWith("__mcp_"))])', ctx);
process.stdout.write(JSON.stringify({installs, top: JSON.parse(read(top)), child: JSON.parse(read(child)), hidden: JSON.parse(read(hidden))}));
"""


def _run(probe: str) -> dict:
    out = subprocess.run([NODE, "-e", RUNNER], input=json.dumps({"probe": probe, "maxEntries": 0}),
                         capture_output=True, text=True, encoding="utf-8", check=False)
    assert out.returncode == 0, out.stderr[:800]
    return json.loads(out.stdout)


@pytest.mark.parametrize("mode", ["proxy", "transparent"])
def test_child_frame_events_land_in_the_top_log(mode):
    probe = proxy_probe() if mode == "proxy" else transparent_probe()
    data = _run(probe)
    top_log, top_meta, top_keys = data["top"]
    child_log, _, _ = data["child"]
    hidden_log, _, _ = data["hidden"]
    assert data["installs"][1]["frameDepth"] == 1 and data["installs"][0]["frameDepth"] == 0
    assert child_log == [], "the child keeps nothing of its own"
    from_child = [e for e in top_log if e.get("frame_url") == "about:blank"]
    assert from_child and all(e["frame_depth"] == 1 for e in from_child)
    assert any(e.get("key") == "userAgent" for e in from_child)
    own = [e for e in top_log if "frame_url" not in e]
    assert any(e.get("key") == "platform" for e in own)
    # One counter for the page: seq increases across frames without gaps.
    assert [e["seq"] for e in top_log] == list(range(len(top_log)))
    assert top_meta["seq"] == len(top_log) and top_meta["dropped"] == 0
    assert top_keys == []
    # A frame whose parent cannot be read keeps its own log.
    assert any(e.get("key") == "userAgent" for e in hidden_log)
    assert all("frame_url" not in e for e in hidden_log)


def test_proxy_probe_reports_what_it_replaced():
    data = _run(proxy_probe())
    info = data["installs"][0]
    assert "Function.prototype.call" in info["replaced"]
    assert info["proxied"][:2] == ["navigator", "screen"]


def test_cap_counts_dropped_events():
    script = r"""
const vm = require('vm');
const probe = require('fs').readFileSync(0, 'utf8');
const ctx = vm.createContext({});
vm.runInContext('var window = globalThis; var performance = {now(){return 0;}};' +
  'var navigator = {userAgent: "UA"}; var screen = {}; var history = {};' +
  'var localStorage = {}; var sessionStorage = {}; var document = {}; window.parent = window;', ctx);
vm.runInContext(probe, ctx);
vm.runInContext('for (var i = 0; i < 5; i++) navigator.userAgent;', ctx);
process.stdout.write(vm.runInContext('JSON.stringify([window.__mcp_jsvmp_log.length, window.__mcp_jsvmp_meta])', ctx));
"""
    out = subprocess.run([NODE, "-e", script], input=proxy_probe(max_entries=2),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    length, meta = json.loads(out.stdout)
    assert length == 2 and meta == {"seq": 5, "dropped": 3, "cap": 2}


class _Page:
    url = "about:blank"

    def __init__(self, info):
        self.info = info

    async def evaluate(self, script):
        if "Promise.resolve(null)" in script:
            return {"__mcp_native_ok": True, "value": None}
        return {"__mcp_native_ok": True, "value": self.info}


@pytest.mark.parametrize("mode,info,expect", [
    ("proxy", {"installed": True, "replaced": ["Function.prototype.call"], "proxied": ["navigator"]},
     "get/set/has of properties on the proxied globals: navigator"),
    ("transparent", {"installed": True, "tapped": 3, "total": 4, "tappedByOwner": {"Navigator": 3}},
     "Navigator (3)"),
])
async def test_install_result_lists_coverage(monkeypatch, mode, info, expect):
    page = _Page(info)

    class Manager:
        async def get_active_page(self):
            return page

        async def add_persistent_script(self, name, content):
            pass

    monkeypatch.setattr(jsvmp, "browser_manager", Manager())
    result = await jsvmp.hook_jsvmp_interpreter(mode=mode, persistent=False)
    coverage = result["coverage"]
    assert any(expect in line for line in coverage["records"])
    assert "not observed" not in coverage["absence"] and "did not observe it" in coverage["absence"]
    assert any("measureText" in line or "getExtension" in line for line in coverage["does_not_record"])
