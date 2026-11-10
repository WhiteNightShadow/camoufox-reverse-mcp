"""hook_jsvmp_interpreter must pass script_url into the probe as data, never as code."""

import json
import shutil
import subprocess

import pytest

from camoufox_reverse_mcp.tools import jsvmp

# Runs what the tool sends to the browser in a fresh vm context with enough
# browser globals for the probe to finish: the main-world payload (native mw:
# channel) and the persistent init script, both as Camoufox runs it in the main
# world and as an older build runs it in the isolated world, where it falls back
# to window.wrappedJSObject.eval. Reports the banner and whether a payload ran.
NODE_RUNNER = r"""
const vm = require('vm');
const items = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const STUBS = 'var window = globalThis; var performance = {now: function () { return 0; }};' +
  'var navigator = {userAgent: "x"}; var screen = {}; var history = {};' +
  'var localStorage = {}; var sessionStorage = {}; var document = {};';
(async () => {
  const results = [];
  for (const item of items) {
    const result = {syntaxError: null, error: null, injected: false, banner: null};
    const code = item.kind === 'native' ? '(' + item.code + ')()' : item.code;
    try { new vm.Script(code); } catch (e) { result.syntaxError = String(e.message); results.push(result); continue; }
    const logs = [];
    const context = vm.createContext({
      console: {log: (...args) => logs.push(args.map(String).join(' ')), warn() {}, error() {}},
    });
    vm.runInContext(STUBS, context);
    if (item.kind === 'init-fallback') {
      context.wrappedJSObject = {eval: (source) => vm.runInContext(source, context)};
    }
    try {
      const value = await vm.runInContext(code, context, {timeout: 5000});
      result.banner = item.kind === 'native' ? value.value.banner : logs[logs.length - 1];
    } catch (e) { result.error = String(e.message); }
    result.injected = vm.runInContext('globalThis.__INJECTED__ === 1', context);
    results.push(result);
  }
  process.stdout.write(JSON.stringify(results));
})();
"""

URLS = {
    "plain": "https://example.com/static/sdk.js",
    "single quote": "sdk.js?name='a'",
    "double quote": 'sdk.js?name="a"',
    "backslashes": "C:\\sdk\\track.js",
    "trailing backslash": "sdk.js\\",
    "line breaks": "sdk.js\nnext\rline\u2028end\u2029",
    "non-ascii": "中文/签名.js",
}

# The first breaks out of the string literal, the second out of the comment
# at the top of each template, where v1.9.1 also wrote script_url.
PAYLOADS = {
    "string break-out": "\\'+(globalThis.__INJECTED__=1),//",
    "comment break-out": "*/ globalThis.__INJECTED__ = 1; /*",
}


class FakePage:
    """Answers the Camoufox mw: main-world channel and keeps what was sent."""

    url = "about:blank"

    def __init__(self):
        self.main_world = []

    async def evaluate(self, script):
        assert script.startswith("mw:"), "the probe must not run in the isolated world"
        payload = script[3:]
        if "Promise.resolve(null)" in payload:  # evaluate_in_world checks the channel first
            return {"__mcp_native_ok": True, "value": None}
        self.main_world.append(payload)
        return {"__mcp_native_ok": True, "value": {"installed": True}}


class FakeBrowserManager:
    def __init__(self):
        self.page = FakePage()
        self.persistent = []

    async def get_active_page(self):
        return self.page

    async def add_persistent_script(self, name, content):
        self.persistent.append(content)


async def render(monkeypatch, script_url, mode, **kwargs):
    manager = FakeBrowserManager()
    monkeypatch.setattr(jsvmp, "browser_manager", manager)
    result = await jsvmp.hook_jsvmp_interpreter(
        script_url=script_url, mode=mode, persistent=True, **kwargs)
    return result, manager


def run_in_node(items):
    completed = subprocess.run(
        [shutil.which("node"), "-e", NODE_RUNNER],
        input=json.dumps(items), capture_output=True, text=True, check=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def banner_script_url(banner, mode):
    value = banner.split("scriptUrl=", 1)[1]
    return value.split(" calls=", 1)[0] if mode == "proxy" else value


KINDS = ("native", "init-main", "init-fallback")


async def both_paths(monkeypatch, values, mode):
    """Install each value through the tool; return what every install path reports."""
    items = []
    for value in values:
        result, manager = await render(monkeypatch, value, mode)
        assert result["status"] == "instrumented"
        assert result["world"] == "main"
        for kind in KINDS:
            code = manager.page.main_world[0] if kind == "native" else manager.persistent[0]
            items.append({"kind": kind, "code": code})
    outcomes = run_in_node(items)
    n = len(KINDS)
    return [(value, *outcomes[n * i:n * i + n]) for i, value in enumerate(values)]


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_script_url_reaches_the_probe_unchanged(monkeypatch, mode):
    for script_url, *outcomes in await both_paths(monkeypatch, list(URLS.values()), mode):
        for outcome in outcomes:
            assert outcome["syntaxError"] is None, script_url
            assert outcome["error"] is None, script_url
            assert banner_script_url(outcome["banner"], mode) == script_url


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_script_url_cannot_run_code(monkeypatch, mode):
    for payload, *outcomes in await both_paths(monkeypatch, list(PAYLOADS.values()), mode):
        for outcome in outcomes:
            assert outcome["syntaxError"] is None, payload
            assert outcome["injected"] is False, payload
            assert banner_script_url(outcome["banner"], mode) == payload


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_empty_script_url_still_matches_every_script(monkeypatch, mode):
    result, manager = await render(monkeypatch, "", mode)
    assert result["status"] == "instrumented"
    assert result["script_url"] == "(all)"
    assert 'scriptUrl: "",' in manager.page.main_world[0]
    [_, *outcomes] = (await both_paths(monkeypatch, [""], mode))[0]
    for outcome in outcomes:
        assert outcome["error"] is None
        assert banner_script_url(outcome["banner"], mode) == "(all)"


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_max_entries_must_be_a_number(monkeypatch, mode):
    result, manager = await render(monkeypatch, "sdk.js", mode, max_entries=500)
    assert "maxEntries: 500" in manager.page.main_world[0]

    result, manager = await render(
        monkeypatch, "sdk.js", mode, max_entries="1, x: globalThis.__INJECTED__ = 1")
    assert "error" in result
    assert manager.page.main_world == [] and manager.persistent == []
