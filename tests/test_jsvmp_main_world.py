"""The JSVMP probes run in the page main world and leave page behaviour intact."""

import json
import shutil
import subprocess

from camoufox_reverse_mcp.tools import hooking, jsvmp, navigation
from camoufox_reverse_mcp.utils.jsvmp_hooks import proxy_probe, transparent_probe

# A page-like realm: navigator/screen/performance behave like browser globals
# (accessors on window, getters on prototypes), the probe is loaded as hook.js
# and page code as sdk.js or other.js, so stack filtering sees real file names.
NODE_PAGE = r"""
const vm = require('vm');
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const context = vm.createContext({});
const run = (code, filename) => new vm.Script(code, {filename}).runInContext(context, {timeout: 5000});
run(`
  var window = globalThis;
  class Navigator { get userAgent() { return 'UA/1.0'; } get platform() { return 'MacIntel'; } }
  class Screen { get width() { return 1440; } }
  class Performance { now() { return 42.5; } }
  class Document { get title() { return 'page'; } }
  for (const [name, value] of [['navigator', new Navigator()], ['screen', new Screen()],
                               ['performance', new Performance()], ['document', new Document()]]) {
    Object.defineProperty(window, name, {get: () => value, configurable: true, enumerable: true});
  }
  var history = {}; var localStorage = {}; var sessionStorage = {};
  var logs = [];
  var console = {log: (...a) => logs.push(a.join(' ')), warn: (...a) => logs.push(a.join(' '))};
  var before = {
    call: Function.prototype.call, apply: Function.prototype.apply, bind: Function.prototype.bind,
    rApply: Reflect.apply, rGet: Reflect.get, rSet: Reflect.set, rConstruct: Reflect.construct,
    now: Date.now, random: Math.random,
    navigator: Object.getOwnPropertyDescriptor(window, 'navigator').get,
    performanceObject: performance, ua: Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgent').get,
  };
`, 'setup.js');
const out = {};
try {
  out.install = run(job.probe, 'hook.js');
  for (const [name, code] of job.page) out[name] = run(code, 'https://example.com/static/' + name);
  out.keys = run('Object.keys(window).filter(k => k.indexOf("__mcp_") === 0)', 'check.js');
  out.log = run('(window.__mcp_jsvmp_log || []).map(e => ({type: e.type, key: e.key, stack: e.stack}))', 'check.js');
  if (job.uninstall) out.uninstall = run(job.uninstall, 'check.js');
  if (job.after) out.after = run(job.after, 'check.js');
} catch (e) { out.error = String(e && e.message || e); }
process.stdout.write(JSON.stringify(out));
"""

PAGE_CALLS = """(() => {
  const r = {};
  function add(a, b) { return this.base + a + b; }
  function Point(x) { this.x = x; }
  r.call = add.call({base: 1}, 2, 3);
  r.apply = add.apply({base: 1}, [2, 3]);
  r.applyNull = (function () { return arguments.length; }).apply(null, null);
  r.bind = add.bind({base: 10}, 1)(2);
  r.bindNew = new (Point.bind(null, 7))().x;
  r.reflect = Reflect.apply(add, {base: 2}, [3, 4]);
  r.construct = Reflect.construct(Point, [5]).x;
  try { (function () { throw new TypeError('boom'); }).call(null); } catch (e) { r.thrown = e.message; }
  r.ua = navigator.userAgent;
  r.width = screen.width;
  r.now = typeof Date.now();
  r.perf = performance.now();
  r.random = typeof Math.random();
  r.slice = Array.prototype.slice.call([1, 2, 3], 1).join(',');
  return r;
})()"""

EXPECTED = {"call": 6, "apply": 6, "applyNull": 0, "bind": 13, "bindNew": 7, "reflect": 9,
            "construct": 5, "thrown": "boom", "ua": "UA/1.0", "width": 1440, "now": "number",
            "perf": 42.5, "random": "number", "slice": "2,3"}

RESTORED = """(() => ({
  call: Function.prototype.call === before.call, apply: Function.prototype.apply === before.apply,
  bind: Function.prototype.bind === before.bind, rApply: Reflect.apply === before.rApply,
  rGet: Reflect.get === before.rGet, rSet: Reflect.set === before.rSet,
  rConstruct: Reflect.construct === before.rConstruct, now: Date.now === before.now,
  random: Math.random === before.random,
  navigator: Object.getOwnPropertyDescriptor(window, 'navigator').get === before.navigator,
  performance: performance === before.performanceObject && !Object.getOwnPropertyNames(performance).includes('now'),
  ua: Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgent').get === before.ua,
}))()"""


def run_page(probe, page, uninstall=None, after=None):
    job = {"probe": probe, "page": page, "uninstall": uninstall, "after": after}
    completed = subprocess.run(
        [shutil.which("node"), "-e", NODE_PAGE], input=json.dumps(job),
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_proxy_probe_keeps_page_calls_working_and_records_only_the_target_script():
    out = run_page(
        proxy_probe(script_url="/static/sdk.js"),
        [["sdk.js", PAGE_CALLS], ["other.js", PAGE_CALLS]],
        uninstall="window.__mcp_jsvmp_uninstall()",
        after=RESTORED,
    )
    assert "error" not in out, out.get("error")
    assert out["install"]["installed"] is True
    assert out["sdk.js"] == EXPECTED and out["other.js"] == EXPECTED
    types = {entry["type"] for entry in out["log"]}
    assert {"fn_call", "fn_apply", "fn_bind", "reflect_apply", "reflect_construct",
            "proxy_get", "api_call"} <= types
    assert all("/static/sdk.js" in entry["stack"] for entry in out["log"])
    assert out["keys"] == []
    assert "Function.prototype.call" in out["uninstall"]["restored"]
    assert all(out["after"].values()), out["after"]


def test_proxy_probe_without_a_filter_finishes_and_stops_at_max_entries():
    loop = """(() => { function f(x) { return x + 1; } let s = 0;
      for (let i = 0; i < 20000; i++) s = f.call(null, s); return s; })()"""
    out = run_page(proxy_probe(script_url="", max_entries=300), [["sdk.js", loop]])
    assert "error" not in out, out.get("error")
    assert out["sdk.js"] == 20000
    assert len(out["log"]) == 300


def test_proxy_probe_installs_once_per_document():
    probe = proxy_probe(script_url="/static/sdk.js")
    out = run_page(probe, [["sdk.js", probe]])
    assert out["install"]["installed"] is True
    assert out["sdk.js"]["installed"] is False


def test_transparent_probe_records_target_getters_and_restores_them():
    reads = "(() => [navigator.userAgent, navigator.platform, screen.width, document.title])()"
    out = run_page(
        transparent_probe(script_url="/static/sdk.js"),
        [["sdk.js", reads], ["other.js", reads]],
        uninstall="window.__mcp_transparent_uninstall()",
        after=RESTORED,
    )
    assert "error" not in out, out.get("error")
    assert out["install"]["installed"] is True and out["install"]["tapped"] >= 4
    assert out["sdk.js"] == out["other.js"] == ["UA/1.0", "MacIntel", 1440, "page"]
    assert sorted(entry["key"] for entry in out["log"]) == ["platform", "title", "userAgent", "width"]
    assert all("/static/sdk.js" in entry["stack"] for entry in out["log"])
    assert out["keys"] == []
    assert out["after"]["ua"] is True and out["after"]["call"] is True


class MainWorldPage:
    """Camoufox page double: mw: is the main world, plain evaluate the isolated one."""

    url = "https://example.com/"
    frames = []

    def __init__(self, native=True):
        self.native = native
        self.main_world = []
        self.isolated = []

    async def evaluate(self, script):
        if script.startswith("mw:"):
            if not self.native:
                raise RuntimeError("main_world_eval is off")
            payload = script[3:]
            if "Promise.resolve(null)" in payload:
                return {"__mcp_native_ok": True, "value": None}
            self.main_world.append(payload)
            if "__mcp_jsvmp_uninstall" in payload:
                value = {"uninstalled": [{"hook": "jsvmp_proxy",
                                          "restored": ["Function.prototype.call"]}], "errors": []}
            elif "__mcp_function_uninstall" in payload:
                value = {"restored": [], "errors": [], "cancelled": 0}
            else:
                value = {"installed": True, "tapped": 3, "total": 5}
            return {"__mcp_native_ok": True, "value": value}
        self.isolated.append(script)
        if "window.wrappedJSObject" in script:
            return {"installed": True}
        return {"uninstalled": [], "errors": [], "restored": [], "cancelled": 0}


class Manager:
    def __init__(self, page):
        self.page = page
        self.persistent = {}
        self._persistent_scripts = []
        self._init_scripts = []

    async def get_active_page(self):
        return self.page

    async def add_persistent_script(self, name, content):
        self.persistent[name] = content


async def test_hook_jsvmp_interpreter_installs_in_the_main_world(monkeypatch):
    for mode in ("transparent", "proxy"):
        manager = Manager(MainWorldPage())
        monkeypatch.setattr(jsvmp, "browser_manager", manager)
        result = await jsvmp.hook_jsvmp_interpreter(script_url="sdk.js", mode=mode)
        assert result["status"] == "instrumented"
        assert result["world"] == "main" and result["execution_backend"] == "camoufox_native"
        assert result["read_with"] == 'evaluate_js("window.__mcp_jsvmp_log || []", world="main")'
        assert len(manager.page.main_world) == 1 and manager.page.isolated == []
        [init_script] = manager.persistent.values()
        # Camoufox runs mw: init scripts in the main world.
        assert init_script.startswith("mw:")
        assert ("__mcp_jsvmp_transparent_installed" if mode == "transparent"
                else "__mcp_jsvmp_installed") in init_script


async def test_without_the_native_channel_the_probe_uses_wrapped_js_object(monkeypatch):
    manager = Manager(MainWorldPage(native=False))
    monkeypatch.setattr(jsvmp, "browser_manager", manager)
    result = await jsvmp.hook_jsvmp_interpreter(mode="transparent")
    assert result["status"] == "instrumented"
    assert result["execution_backend"] == "wrappedJSObject"
    assert any("wrappedJSObject" in warning for warning in result["warnings"])
    assert all("window.wrappedJSObject" in script for script in manager.page.isolated)
    # Without main_world_eval Camoufox drops mw: init scripts, so the persistent
    # probe must be a plain one that installs through wrappedJSObject.
    [init_script] = manager.persistent.values()
    assert not init_script.startswith("mw:")
    assert "window.wrappedJSObject.eval(" in init_script


async def test_pre_inject_probes_install_in_the_main_world(monkeypatch):
    for page, prefixed in ((None, True), (MainWorldPage(), True), (MainWorldPage(native=False), False)):
        manager = Manager(page)
        monkeypatch.setattr(navigation, "browser_manager", manager)
        for name, marker in (("jsvmp_probe", "__mcp_jsvmp_installed"),
                             ("jsvmp_probe_transparent", "__mcp_jsvmp_transparent_installed")):
            ok, _ = await navigation._inject_hook_by_name(name, page)
            assert ok
            script = manager.persistent[f"pre_inject:{name}"]
            assert script.startswith("mw:") is prefixed
            assert "window.wrappedJSObject.eval(" in script and marker in script


async def test_remove_hooks_uninstalls_the_probes_in_the_main_world(monkeypatch):
    manager = Manager(MainWorldPage())
    monkeypatch.setattr(hooking, "browser_manager", manager)
    result = await hooking.remove_hooks()
    assert "jsvmp_proxy:Function.prototype.call" in result["restored_objects"]
    assert any("__mcp_jsvmp_uninstall" in script for script in manager.page.main_world)
