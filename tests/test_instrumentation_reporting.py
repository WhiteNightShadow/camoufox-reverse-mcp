"""v1.10.0 instrumentation: defaults, skip/cap reporting, seq slicing, offline rewrite."""

import inspect
import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import instrumentation
from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite
from camoufox_reverse_mcp.utils.js_rewriter import regex_rewrite, rewrite_stage_calls

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")


def _node(script: str, stdin: str = "") -> object:
    out = subprocess.run([NODE, "-e", script], input=stdin, capture_output=True,
                         text=True, encoding="utf-8", check=False)
    assert out.returncode == 0, out.stderr[:600]
    return json.loads(out.stdout)


def test_defaults_tap_reads_only_and_allow_two_megabytes():
    params = inspect.signature(instrumentation.instrumentation).parameters
    assert params["rewrite_member_access"].default is True
    assert params["rewrite_calls"].default is False
    assert params["max_file_size"].default == 2_000_000


@pytest.fixture
async def harness(monkeypatch):
    class Context:
        async def route(self, pattern, handler):
            self.handler = handler

    context = Context()
    manager = BrowserManager()
    manager.contexts = {"default": context}
    manager.get_active_page = AsyncMock(return_value=SimpleNamespace(url="about:blank"))
    monkeypatch.setattr(instrumentation, "browser_manager", manager)
    monkeypatch.setattr(instrumentation, "_active_routes", {})
    monkeypatch.setattr(instrumentation, "_install_history", {})
    monkeypatch.setattr(instrumentation, "_source_site_registry", {})
    return context


def _route(body: bytes, url="https://example.test/sdk.js"):
    response = SimpleNamespace(body=AsyncMock(return_value=body), status=200,
                               headers={"content-type": "application/javascript"})
    return SimpleNamespace(request=SimpleNamespace(url=url), fetch=AsyncMock(return_value=response),
                           fulfill=AsyncMock(), abort=AsyncMock())


def _sdk(size: int) -> bytes:
    unit = "var a%d = navigator.userAgent;\n"
    lines, total, i = [], 0, 0
    while total < size:
        line = unit % i
        lines.append(line)
        total += len(line)
        i += 1
    return "".join(lines).encode()


async def test_install_reports_options_and_call_effects(harness):
    result = await instrumentation.instrumentation("install", url_pattern="**/sdk.js")
    assert result["rewrite_calls"] is False and result["max_file_size"] == 2_000_000
    assert "rewrite_calls=True" in result["calls_note"]
    tapped = await instrumentation.instrumentation("install", url_pattern="**/b.js", rewrite_calls=True)
    assert any("callee.caller reads null" in w for w in tapped["warnings"])


async def test_a_315kb_sdk_is_rewritten_by_default(harness):
    await instrumentation.instrumentation("install", url_pattern="**/sdk.js")
    r = _route(_sdk(315_368))
    await harness.handler(r)
    body = r.fulfill.call_args.kwargs["body"]
    assert isinstance(body, str) and "__mcp_tap_get(navigator" in body
    status = instrumentation._get_status()["active_patterns"][0]
    assert status["files_rewritten"] == 1 and status["skipped_files"] == []


async def test_oversized_skip_is_recorded_per_file_and_hinted_in_log(harness, monkeypatch):
    await instrumentation.instrumentation("install", url_pattern="**/*.js", max_file_size=200_000)
    big, small = _route(_sdk(315_368), "https://example.test/big.js"), _route(_sdk(1000), "https://example.test/small.js")
    await harness.handler(big)
    await harness.handler(small)  # the next file used to clear last_skip_reason
    status = instrumentation._get_status()["active_patterns"][0]
    assert status["files_rewritten"] == 1 and status["files_passed_through"] == 1
    [record] = status["skipped_files"]
    assert record["url"].endswith("big.js") and record["reason"] == "oversized_without_selected_properties"
    assert record["bytes"] > 315_000 and record["suggested_max_file_size"] == 400_000
    assert "max_file_size>=400000" in record["hint"]

    monkeypatch.setattr(instrumentation, "evaluate_in_world",
                        AsyncMock(return_value=({"entries": [], "total": 0, "dropped": 0, "next_seq": 0},
                                                "camoufox_native", None)))
    log = await instrumentation.instrumentation("log")
    assert "passed through unchanged" in log["hint"]
    [install] = log["instrumentation"]
    assert install["rewrite_calls"] is False and install["skipped_files"][0]["url"].endswith("big.js")

    await instrumentation.instrumentation("stop", url_pattern="**/*.js")
    after_stop = await instrumentation.instrumentation("log")
    assert after_stop["instrumentation"][0]["active"] is False
    assert after_stop["instrumentation"][0]["rewrite_member_access"] is True


async def test_capped_files_are_reported(harness):
    await instrumentation.instrumentation("install", url_pattern="**/sdk.js", max_rewrites=3)
    r = _route(_sdk(2000))
    await harness.handler(r)
    [capped] = instrumentation._get_status()["active_patterns"][0]["capped_files"]
    assert capped["edits"] == 3 and capped["max_rewrites"] == 3


def test_rewriters_flag_caps():
    src = "; ".join(f"a.b{i}" for i in range(20))
    assert ast_rewrite(src, max_edits=5)[1]["edits_capped"] is True
    assert "edits_capped" not in ast_rewrite(src, max_edits=50)[1]
    regex_src = "".join(f"var v{i}=o['k{i}'];" for i in range(10))
    assert regex_rewrite(regex_src, max_rewrites=4)[1]["edits_capped"] is True
    stage = "function d(){return l.apply(this,[%d].concat(Array.prototype.slice.call(arguments)));}"
    stats = {}
    rewrite_stage_calls("".join(stage % i for i in range(5)), max_rewrites=2, stats=stats)
    assert stats["stage_capped"] is True


@needs_node
def test_runtime_numbers_every_event_and_counts_drops():
    from camoufox_reverse_mcp.utils.js_rewriter import INSTRUMENT_RUNTIME

    script = r"""
const runtime = require('fs').readFileSync(0, 'utf8');
eval(runtime);
const inner = function (p) { __mcp_tap_get({a: 1}, 'a', 't'); return 1; };
const outer = function (p) { __mcp_tap_stage(8, inner, null, [], 't'); return 2; };
__mcp_tap_stage(5, outer, null, [], 't');
for (let i = 0; i < 20005; i++) __mcp_tap_get({k: i}, 'k', 't');
const log = globalThis.__mcp_vmp_log, meta = globalThis.__mcp_vmp_meta;
process.stdout.write(JSON.stringify({
  first: log.slice(0, 3).map(e => [e.type, e.seq, e.stage === undefined ? null : e.stage, e.p === undefined ? null : e.p]),
  length: log.length, dropped: meta.dropped, nextSeq: meta.seq,
  keys: Object.keys(globalThis).filter(k => k.startsWith('__mcp_')),
}));
"""
    data = _node(script, INSTRUMENT_RUNTIME)
    # The read inside state 8 (inside 5), then 8's own event (inside 5), then 5's.
    assert data["first"] == [["tap_get", 0, 8, None], ["tap_stage", 1, 5, 8], ["tap_stage", 2, None, 5]]
    assert data["length"] == 20000 and data["dropped"] == 8 and data["nextSeq"] == 20008
    assert data["keys"] == []


@needs_node
def test_log_reader_slices_by_seq_in_the_page():
    reader = instrumentation._LOG_READER % json.dumps({"after": None, "lo": 3, "hi": 5})
    script = (
        "globalThis.window = globalThis;"
        "window.__mcp_vmp_log = [0,1,2,3,4,5,6].map(s => ({type:'tap_get', seq:s})).concat([{type:'legacy'}]);"
        "window.__mcp_vmp_meta = {seq: 9, dropped: 2};"
        f"const r = ({reader})();"
        "process.stdout.write(JSON.stringify([r.entries.map(e => e.seq), r.total, r.dropped, r.next_seq]));"
    )
    assert _node(script) == [[3, 4, 5], 8, 2, 9]


async def test_log_pages_forward_and_filters_by_stage(monkeypatch):
    events = [{"type": "tap_get", "seq": s, "key": "k", "stage": 7 if 3 <= s <= 5 else None} for s in range(10)]
    events.append({"type": "tap_stage", "seq": 10, "p": 7})
    reader = AsyncMock(return_value=({"entries": events, "total": 11, "dropped": 0, "next_seq": 11},
                                     "camoufox_native", None))
    monkeypatch.setattr(instrumentation, "evaluate_in_world", reader)
    monkeypatch.setattr(instrumentation, "browser_manager",
                        SimpleNamespace(get_active_page=AsyncMock(return_value=object())))
    monkeypatch.setattr(instrumentation, "_install_history", {})

    page = await instrumentation.instrumentation("log", after_seq=-1, limit=4)
    assert [e["seq"] for e in page["entries"]] == [0, 1, 2, 3]
    assert page["next_after_seq"] == 3 and page["truncated"] is True
    assert '"after": -1' in reader.call_args.args[1]

    tail = await instrumentation.instrumentation("log", limit=2)
    assert [e["seq"] for e in tail["entries"]] == [9, 10] and "next_after_seq" not in tail

    staged = await instrumentation.instrumentation("log", stage=7)
    assert [e["seq"] for e in staged["entries"]] == [3, 4, 5, 10]

    bad = await instrumentation.instrumentation("log", seq_window=[5, 1])
    assert "seq_window" in bad["error"]


# ---- rewrite_source (#19) ----

async def test_rewrite_source_inline_runs_offline_without_window():
    result = await instrumentation.instrumentation(
        "rewrite_source", source="var ua = navigator.userAgent; out = ua;")
    assert result["status"] == "rewritten" and result["edits"] == 1
    assert result["options"]["rewrite_calls"] is False
    if NODE is None:
        pytest.skip("Node.js is unavailable")
    # A vm context whose `window` is a different object: helpers must still
    # resolve as bare identifiers, and the log lives on the context global.
    script = r"""
const vm = require('vm');
const code = require('fs').readFileSync(0, 'utf8');
const ctx = vm.createContext({ navigator: { userAgent: 'UA' }, window: {} });
vm.runInContext(code, ctx);
process.stdout.write(JSON.stringify([ctx.out, vm.runInContext('globalThis.__mcp_vmp_log', ctx), ctx.window]));
"""
    out, log, fake_window = _node(script, result["rewritten"])
    assert out == "UA" and fake_window == {}
    assert [(e["type"], e["key"], e["value"], e["seq"]) for e in log] == [("tap_get", "userAgent", "UA", 0)]


async def test_rewrite_source_file_to_file_with_sites(tmp_path):
    src = tmp_path / "sdk.js"
    src.write_text("var a = navigator.platform;\n", encoding="utf-8")
    out = tmp_path / "out" / "sdk.instrumented.js"
    result = await instrumentation.instrumentation(
        "rewrite_source", source_path=str(src), output_path=str(out), include_source_site=True)
    assert result["status"] == "rewritten" and result["output_path"] == str(out.resolve())
    assert "__mcp_tap_get(navigator" in out.read_text(encoding="utf-8")
    sites = json.loads(open(result["source_sites_path"], encoding="utf-8").read())
    assert sites["sites"][0]["kind"] == "tap_get" and result["source_site_count"] == 1
    assert "rewritten" not in result


async def test_rewrite_source_guards(tmp_path):
    src = tmp_path / "sdk.js"
    src.write_text("var a = navigator.platform;", encoding="utf-8")
    same = await instrumentation.instrumentation("rewrite_source", source_path=str(src), output_path=str(src))
    assert "must differ" in same["error"] and src.read_text() == "var a = navigator.platform;"
    both = await instrumentation.instrumentation("rewrite_source", source="1", source_path=str(src))
    assert "exactly one" in both["error"]
    regex = await instrumentation.instrumentation("rewrite_source", source="a.b", mode="regex",
                                                  filter_property_names=["b"])
    assert "require mode='ast'" in regex["error"]
    big = await instrumentation.instrumentation("rewrite_source", source=_sdk(120_000).decode())
    assert big["status"] == "not_returned" and "output_path" in big["error"] and "rewritten" not in big
    nothing = await instrumentation.instrumentation("rewrite_source", source="var x = 1;")
    assert nothing["status"] == "unchanged" and nothing["reason"] == "no_eligible_rewrites"
