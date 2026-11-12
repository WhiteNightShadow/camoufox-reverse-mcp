"""stage_break runtime: bounded deep capture for selected dispatcher states."""

import json
import os
import shutil
import subprocess

import pytest

from camoufox_reverse_mcp.utils.js_rewriter import INSTRUMENT_RUNTIME

# The runtime is fed through stdin: this keeps the harness ASCII-only and avoids
# temp paths that may contain non-ASCII characters on Windows.
HARNESS = r"""
const fs = require('fs');
const runtime = fs.readFileSync(0, 'utf8');
global.window = { __mcp_vmp_log: [] };
eval(runtime);

const BREAK_STATE = Number(process.env.BREAK_STATE || 7);
const MAX_ITEMS = Number(process.env.MAX_ITEMS || 3);
const MAX_DEPTH = Number(process.env.MAX_DEPTH || 4);
window.__mcp_tap_cfg = { sampling: 1, tagFilter: null };
window.__mcp_tap_cfg.breaks = {};
window.__mcp_tap_cfg.breaks[BREAK_STATE] = {
  capture: 'args_ret_stack', limit: 2, depth: MAX_DEPTH, maxItems: MAX_ITEMS, hits: 0,
};

const fn = function (state, a, b) {
  return { state: state, nested: { deep: { deeper: 1 } }, list: [1,2,3,4,5] };
};
for (let i = 0; i < 4; i++) {
  window.__mcp_tap_stage(7, fn, null, [{ x: i }, 'abc'], 'tag');
}

const events = window.__mcp_vmp_log.filter((e) => e.type === 'tap_stage_break');
process.stdout.write(JSON.stringify({
  breaks: events.length,
  first: events[0] || null,
  stage_taps: window.__mcp_vmp_log.filter((e) => e.type === 'tap_stage').length,
  hits: window.__mcp_tap_cfg.breaks[BREAK_STATE].hits,
}));
"""


def _run(env_overrides=None):
    if shutil.which("node") is None:
        pytest.skip("Node.js unavailable")
    env = dict(os.environ)
    env.update({"BREAK_STATE": "7", "MAX_ITEMS": "3", "MAX_DEPTH": "4"})
    env.update(env_overrides or {})
    out = subprocess.run(["node", "-e", HARNESS], input=INSTRUMENT_RUNTIME,
                         capture_output=True, text=True, encoding="utf-8", env=env)
    assert out.returncode == 0, out.stderr[:400]
    return json.loads(out.stdout.lstrip("\ufeff"))


def test_stage_break_records_bounded_snapshot():
    data = _run()
    assert data["stage_taps"] == 4, "every call still produces a stage tap"
    assert data["breaks"] == 2, "limit caps the recorded breaks"
    assert data["hits"] == 2

    first = data["first"]
    assert first["p"] == 7
    assert first["argc"] == 2
    assert isinstance(first["args"], list) and first["args"][0] == {"x": 0}
    assert first["ret"]["nested"]["deep"] == {"deeper": 1}
    assert isinstance(first.get("stack"), list)


def test_stage_break_is_opt_in():
    data = _run({"BREAK_STATE": "99"})
    assert data["breaks"] == 0
    assert data["stage_taps"] == 4


def test_stage_break_snapshot_caps_size():
    # maxItems=3 keeps the three object keys and caps the 5-element list
    data = _run({"MAX_ITEMS": "3"})
    lst = data["first"]["ret"]["list"]
    assert len(lst) == 4, "3 elements plus a truncation marker"
    assert lst[-1] == {"__t": "truncated", "length": 5}


# ---- fixes on top of the original stage_break commit ----

FIX_HARNESS = r"""
const fs = require('fs');
const runtime = fs.readFileSync(0, 'utf8');
global.window = { __mcp_vmp_log: [] };
eval(runtime);
const cfg = window.__mcp_tap_cfg;
if (process.env.ARM !== '0') {
  cfg.breaks = cfg.breaks || {};
  cfg.breaks[7] = { capture: 'args_ret_stack', limit: 0, depth: 3, maxItems: 10, hits: 0 };
}
let getterCalls = 0;
const arg = { plain: 1 };
Object.defineProperty(arg, 'secret', { enumerable: true, get() { getterCalls++; return 'x'; } });
const proto = JSON.parse('{"__proto__": {"polluted": true}, "ok": 2}');
const fn = function (state, a, b) { return { echoed: a.plain }; };
window.__mcp_tap_stage(7, fn, null, [arg, proto], 'tag');
const ev = window.__mcp_vmp_log.find((e) => e.type === 'tap_stage_break') || null;
process.stdout.write(JSON.stringify({
  ev, getterCalls,
  protoKept: ev ? Object.getPrototypeOf(ev.args[1]) === Object.prototype : null,
}));
"""


def _run_fix(runtime=INSTRUMENT_RUNTIME, env=None):
    if shutil.which("node") is None:
        pytest.skip("Node.js unavailable")
    out = subprocess.run(["node", "-e", FIX_HARNESS], input=runtime, capture_output=True,
                         text=True, encoding="utf-8", env={**os.environ, **(env or {})})
    assert out.returncode == 0, out.stderr[:400]
    return json.loads(out.stdout.lstrip("﻿"))


def test_stage_break_stack_has_no_probe_frames():
    stack = _run_fix()["ev"]["stack"]
    assert stack, "the caller frames remain"
    assert not stack[0].startswith("Error")
    assert not any(name in line for line in stack
                   for name in ("_stageStack", "_maybeBreak", "__mcp_tap_stage"))


def test_stage_break_snapshot_does_not_call_getters():
    data = _run_fix()
    assert data["getterCalls"] == 0
    args = data["ev"]["args"]
    assert args[0] == {"plain": 1, "secret": {"__t": "accessor"}}
    assert args[1]["ok"] == 2 and args[1]['["__proto__"]'] == {"polluted": True}
    assert data["protoKept"] is True


def test_breaks_armed_in_served_runtime_apply_at_load():
    from camoufox_reverse_mcp.utils.js_rewriter import BREAKS_PLACEHOLDER

    armed = '{"7":{"capture":"args_only","limit":1,"depth":2,"maxItems":5,"hits":0}}'
    data = _run_fix(INSTRUMENT_RUNTIME.replace(BREAKS_PLACEHOLDER, armed, 1), {"ARM": "0"})
    assert data["ev"]["p"] == 7 and "ret" not in data["ev"]


async def test_route_serves_states_armed_by_stage_break(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from camoufox_reverse_mcp.browser import BrowserManager
    from camoufox_reverse_mcp.tools import instrumentation, stage_break
    from camoufox_reverse_mcp.utils.js_rewriter import BREAKS_PLACEHOLDER

    class Context:
        async def route(self, pattern, handler):
            self.handler = handler

    context = Context()
    manager = BrowserManager()
    manager.contexts = {"default": context}
    manager.get_active_page = AsyncMock(return_value=SimpleNamespace(url="about:blank"))
    monkeypatch.setattr(instrumentation, "browser_manager", manager)
    monkeypatch.setattr(instrumentation, "_active_routes", {})
    monkeypatch.setattr(stage_break, "browser_manager", manager)
    monkeypatch.setattr(stage_break, "_ARMED", {})
    monkeypatch.setattr(stage_break, "evaluate_in_world",
                        AsyncMock(return_value=({"armed": [7], "runtime_installed": False}, "camoufox_native", None)))

    def route():
        response = SimpleNamespace(body=AsyncMock(return_value=b"var a=navigator.userAgent;"),
                                   status=200, headers={"content-type": "application/javascript"})
        return SimpleNamespace(request=SimpleNamespace(url="https://example.test/sdk.js"),
                               fetch=AsyncMock(return_value=response), fulfill=AsyncMock())

    await instrumentation.instrumentation("install", url_pattern="**/sdk.js")
    first = route()
    await context.handler(first)
    assert BREAKS_PLACEHOLDER in first.fulfill.call_args.kwargs["body"]

    result = await stage_break.stage_break(action="set", p=7, capture="args_only", limit=3)
    assert result["session_armed"] == [7]
    cached = route()
    await context.handler(cached)
    body = cached.fulfill.call_args.kwargs["body"]
    assert BREAKS_PLACEHOLDER not in body
    assert '"7":{"capture":"args_only","limit":3,"depth":2,"maxItems":50,"hits":0}' in body

    await stage_break.stage_break(action="clear")
    cleared = route()
    await context.handler(cleared)
    assert BREAKS_PLACEHOLDER in cleared.fulfill.call_args.kwargs["body"]


async def test_stage_break_arms_session_without_a_page(monkeypatch):
    from unittest.mock import AsyncMock

    from camoufox_reverse_mcp.tools import stage_break

    manager = type("M", (), {})()
    manager.get_active_page = AsyncMock(side_effect=RuntimeError("browser not launched"))
    monkeypatch.setattr(stage_break, "browser_manager", manager)
    monkeypatch.setattr(stage_break, "_ARMED", {})
    result = await stage_break.stage_break(action="set", p=3)
    assert result["session_armed"] == [3] and "live page not updated" in result["error"]
    assert '"3"' in stage_break.armed_breaks_literal()


async def test_trace_session_drops_recording_of_a_closed_context(monkeypatch):
    from unittest.mock import AsyncMock

    from camoufox_reverse_mcp.tools import trace_session

    class Tracing:
        def __init__(self):
            self.start = AsyncMock()
            self.stop = AsyncMock()

    old_ctx = type("Ctx", (), {})()
    old_ctx.tracing = Tracing()
    new_ctx = type("Ctx", (), {})()
    new_ctx.tracing = Tracing()
    manager = type("M", (), {})()
    manager.contexts = {"default": old_ctx}
    monkeypatch.setattr(trace_session, "browser_manager", manager)
    monkeypatch.setattr(trace_session, "_TRACES", {})

    assert (await trace_session.trace_session(action="start"))["recording"] is True
    manager.contexts = {"default": new_ctx}  # browser relaunched
    status = await trace_session.trace_session(action="status")
    assert status["recording"] is False and "lost" in status["note"]
    started = await trace_session.trace_session(action="start")
    assert started["recording"] is True
    new_ctx.tracing.start.assert_awaited_once()
