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
