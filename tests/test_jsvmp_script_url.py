"""hook_jsvmp_interpreter must pass script_url into the hook as data, never as code."""

import json
import shutil
import subprocess

import pytest

from camoufox_reverse_mcp.tools import jsvmp

# Runs each rendered hook in its own vm context with just enough browser globals
# for the hook to finish, and reports what it logged and whether a payload ran.
NODE_RUNNER = r"""
const vm = require('vm');
const scripts = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const results = scripts.map((code) => {
  const result = {syntaxError: null, error: null, injected: false, logs: []};
  try { new vm.Script(code); } catch (e) { result.syntaxError = String(e.message); return result; }
  const context = vm.createContext({
    console: {log: (...args) => result.logs.push(args.map(String).join(' ')), warn() {}, error() {}},
  });
  vm.runInContext(
    'var window = globalThis; var performance = {now: function () { return 0; }};' +
    'var navigator = {userAgent: "x"}; var screen = {}; var history = {};' +
    'var localStorage = {}; var sessionStorage = {}; var document = {};', context);
  try { vm.runInContext(code, context, {timeout: 5000}); }
  catch (e) { result.error = String(e.message); }
  result.injected = vm.runInContext('globalThis.__INJECTED__ === 1', context);
  return result;
});
process.stdout.write(JSON.stringify(results));
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
# at the top of each template, where the old code also wrote script_url.
PAYLOADS = {
    "string break-out": "\\'+(globalThis.__INJECTED__=1),//",
    "comment break-out": "*/ globalThis.__INJECTED__ = 1; /*",
}


class FakePage:
    url = "about:blank"

    def __init__(self):
        self.evaluated = []

    async def evaluate(self, script):
        self.evaluated.append(script)


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


def run_in_node(scripts):
    completed = subprocess.run(
        [shutil.which("node"), "-e", NODE_RUNNER],
        input=json.dumps(scripts), capture_output=True, text=True, check=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def logged_script_url(logs, mode):
    line = [entry for entry in logs if "scriptUrl=" in entry][-1]
    value = line.split("scriptUrl=", 1)[1]
    return value.split(" calls=", 1)[0] if mode == "proxy" else value


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_script_url_reaches_the_hook_unchanged(monkeypatch, mode):
    scripts = []
    for script_url in URLS.values():
        result, manager = await render(monkeypatch, script_url, mode)
        assert result["status"] == "instrumented"
        assert manager.persistent == manager.page.evaluated
        scripts.append(manager.page.evaluated[0])

    for script_url, outcome in zip(URLS.values(), run_in_node(scripts)):
        assert outcome["syntaxError"] is None, script_url
        assert outcome["error"] is None, script_url
        assert logged_script_url(outcome["logs"], mode) == script_url


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_script_url_cannot_run_code(monkeypatch, mode):
    scripts = []
    for payload in PAYLOADS.values():
        result, manager = await render(monkeypatch, payload, mode)
        assert result["status"] == "instrumented"
        scripts.append(manager.page.evaluated[0])

    for payload, outcome in zip(PAYLOADS.values(), run_in_node(scripts)):
        assert outcome["syntaxError"] is None, payload
        assert outcome["injected"] is False, payload
        assert logged_script_url(outcome["logs"], mode) == payload


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_empty_script_url_still_matches_every_script(monkeypatch, mode):
    result, manager = await render(monkeypatch, "", mode)
    assert result["status"] == "instrumented"
    assert result["script_url"] == "(all)"
    assert 'scriptUrl: "",' in manager.page.evaluated[0]


@pytest.mark.parametrize("mode", ["transparent", "proxy"])
async def test_max_entries_must_be_a_number(monkeypatch, mode):
    result, manager = await render(monkeypatch, "sdk.js", mode, max_entries=500)
    assert "maxEntries: 500" in manager.page.evaluated[0]

    result, manager = await render(
        monkeypatch, "sdk.js", mode, max_entries="1, x: globalThis.__INJECTED__ = 1")
    assert "error" in result
    assert manager.page.evaluated == [] and manager.persistent == []
