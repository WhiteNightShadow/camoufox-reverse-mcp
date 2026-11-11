"""Stage/dispatcher taps: literal rewrite of the flattened-VMP call shape."""
import hashlib
import json
import shutil
import subprocess

import esprima
import pytest

from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite
from camoufox_reverse_mcp.utils.js_rewriter import (
    INSTRUMENT_RUNTIME,
    directive_end,
    regex_rewrite,
    rewrite_stage_calls,
)

STAGE_CALL = (
    "function d(a){switch(a){case 1:return l.apply(this,[9425].concat("
    "Array.prototype.slice.call(arguments)));}}"
)


def test_runtime_defines_stage_tap():
    assert "__mcp_tap_stage" in INSTRUMENT_RUNTIME
    assert "type:'tap_stage'" in INSTRUMENT_RUNTIME


def test_stage_rewrite_matches_exact_dispatcher_shape():
    out, count, sites = rewrite_stage_calls(STAGE_CALL, tag="t")
    assert count == 1
    assert '__mcp_tap_stage(9425, l, this, arguments, "t")' in out
    assert sites == []


def test_stage_rewrite_leaves_other_calls_untouched():
    src = "return x.apply(this, [1, 2]);"
    out, count, _ = rewrite_stage_calls(src, tag="t")
    assert count == 0
    assert out == src


def test_stage_rewrite_adds_source_sites_when_requested():
    out, count, sites = rewrite_stage_calls(STAGE_CALL, tag="t", include_source_site=True)
    assert count == 1
    assert len(sites) == 1
    assert sites[0]["kind"] == "tap_stage"
    assert sites[0]["site_id"].endswith(":tap_stage")


def test_regex_rewrite_stage_only_still_injects_runtime():
    out, stats = regex_rewrite(STAGE_CALL, tag="t", tap_stage_calls=True)
    assert stats["stage_rewrites"] == 1
    assert "__mcp_tap_stage(9425" in out
    assert "__mcp_tap_installed" in out


def test_ast_rewrite_adds_stage_taps():
    out, stats = ast_rewrite(
        STAGE_CALL, tag="t", tap_stage_calls=True,
        rewrite_member_access=False, rewrite_calls=False,
    )
    assert out is not None
    assert stats["stage_edits"] == 1
    assert stats["edits"] == 1
    assert "__mcp_tap_stage(9425" in out


def test_ast_rewrite_keeps_stage_taps_when_parser_fails(monkeypatch):
    import camoufox_reverse_mcp.utils.js_parser as js_parser

    def boom(src, locations=False):  # noqa: ARG001
        raise ValueError("parser unavailable")

    monkeypatch.setattr(js_parser, "parse_source", boom)
    out, stats = ast_rewrite(STAGE_CALL, tag="t", tap_stage_calls=True)
    assert out is not None
    assert stats.get("stage_only") is True
    assert stats["stage_edits"] == 1
    assert "__mcp_tap_stage(9425" in out


STAGE_BODY = "return l.apply(this,[7].concat(Array.prototype.slice.call(arguments)))"


def test_directive_end_only_counts_strings_that_end_a_statement():
    assert directive_end('"use strict";\nvar a;') == len('"use strict";')
    assert directive_end('"use strict"\nvar a;') == len('"use strict"')
    assert directive_end("'a'; 'use strict'\nf();") == len("'a'; 'use strict'")
    assert directive_end('"x"\n++y;') == len('"x"')
    assert directive_end('#!/usr/bin/env node\n"use strict";') == len('#!/usr/bin/env node\n"use strict";')
    for source in ('"a,b"\n.split(",")', '"x"\n+ y', '"x"\n[0]', '"x"\n(f)', '"x"\n!== y', '"x"\nin y'):
        assert directive_end(source) == 0, source


def test_default_ast_rewrite_keeps_runtime_before_a_leading_expression():
    src = '"a,b"\n.split(",").forEach(function (k) { var v = navigator[k]; });'
    out, _ = ast_rewrite(src, tag="t")
    assert out.startswith(";\n" + INSTRUMENT_RUNTIME + "\n")


def test_stage_shape_inside_a_string_does_not_break_a_parseable_file():
    src = 'var gen = new Function("l", "' + STAGE_BODY + '");\n' + STAGE_CALL
    out, stats = ast_rewrite(src, tag="t", tap_stage_calls=True,
                             rewrite_member_access=False, rewrite_calls=False)
    esprima.parseScript(out)
    assert stats["stage_edits"] == 1
    assert "stage_only" not in stats and "error" not in stats
    assert '"' + STAGE_BODY + '"' in out
    assert "__mcp_tap_stage(9425" in out


def test_stage_taps_keep_source_sites_on_the_original_text():
    src = ('var marker = "\U0001F600";\n'
           "function d(k){ return l.apply(this,[9425].concat(Array.prototype.slice.call(arguments)))[k]; }\n"
           'var ua = navigator["userAgent"]; foo(ua);')
    out, stats = ast_rewrite(src, tag="t", include_source_site=True, tap_stage_calls=True)
    digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
    assert stats["source_sha256"] == digest and stats["source_id"] == digest[:16]
    assert len(stats["source_sites"]) == stats["edits"]
    texts = {}
    for site in stats["source_sites"]:
        assert site["site_id"].startswith(stats["source_id"] + ":")
        assert site["site_id"] in out
        texts.setdefault(site["kind"], set()).add(src[site["start"]:site["end"]])
    stage = "return l.apply(this,[9425].concat(Array.prototype.slice.call(arguments)))"
    assert texts["tap_stage"] == {stage}
    assert texts["tap_get"] == {stage[len("return "):] + "[k]", 'navigator["userAgent"]'}
    assert texts["tap_call"] == {"foo(ua)"}
    member = next(s for s in stats["source_sites"]
                  if s["kind"] == "tap_get" and src[s["start"]:s["end"]] == 'navigator["userAgent"]')
    assert (member["line"], member["column"]) == (3, src.splitlines()[2].index("navigator"))


def test_regex_simple_program_leaves_the_shape_inside_strings_alone():
    src = 'var a = b["' + STAGE_BODY + '"];'
    out, stats = regex_rewrite(src, tag="t", tap_stage_calls=True)
    assert stats["stage_rewrites"] == 0
    assert stats["member_access_rewrites"] == 1
    esprima.parseScript(out)


VM_SDK = """
var l = function (p) {
  var a = Array.prototype.slice.call(arguments, 1);
  switch (p) {
    case 101: return (s2(a[0] * 31 + 7) ^ s3(a[1])) >>> 0;
    case 202: return (a[0] >>> 3) + s3(a[0] & 255);
    case 303: return Math.imul(a[0] | 0, 2654435761) >>> 0;
    case 404: return this.base + a.length;
    case 505: throw new TypeError('stage 505');
    case 606: a.push('extra'); return a.length;
  }
};
function s1() { return l.apply(this, [101].concat(Array.prototype.slice.call(arguments))); }
function s2() { return l.apply(this, [202].concat(Array.prototype.slice.call(arguments))); }
function s3() { return l.apply(this, [303].concat(Array.prototype.slice.call(arguments))); }
var obj = {base: 40, m: function () { return l.apply(this, [404].concat(Array.prototype.slice.call(arguments))); }};
function bad() { return l.apply(this, [505].concat(Array.prototype.slice.call(arguments))); }
function mut() { return l.apply(this, [606].concat(Array.prototype.slice.call(arguments))); }
var arr = [1, 2], thrown = null;
try { bad(1); } catch (e) { thrown = e.name + ':' + e.message; }
var result = [s1(123, 456), obj.m(1, 2), thrown, mut.apply(null, arr), arr.length];
"""
NODE_RUNNER = """
const vm = require('node:vm');
const c = vm.createContext({});
vm.runInContext('globalThis.window = globalThis;', c);
vm.runInContext(require('node:fs').readFileSync(0, 'utf8'), c);
const log = (c.__mcp_vmp_log || []).filter(e => e.type === 'tap_stage');
process.stdout.write(JSON.stringify({result: vm.runInContext('result', c),
  stages: log.map(e => [e.p, e.argc, e.args, e.ret])}));
"""


@pytest.mark.parametrize("rewrite", ["ast", "ast_default_taps", "regex"])
def test_stage_taps_keep_vm_results_and_record_each_state(rewrite):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for executable semantics tests")
    if rewrite == "regex":
        out, _ = regex_rewrite(VM_SDK, tag="vmp", tap_stage_calls=True)
    else:
        default_taps = rewrite == "ast_default_taps"
        out, _ = ast_rewrite(VM_SDK, tag="vmp", tap_stage_calls=True,
                             rewrite_member_access=default_taps, rewrite_calls=default_taps)

    def run(code):
        done = subprocess.run([node, "-e", NODE_RUNNER], input=code, capture_output=True,
                              text=True, timeout=10, check=True)
        return json.loads(done.stdout)

    original, actual = run(VM_SDK), run(out)
    assert actual["result"] == original["result"]
    assert [s[0] for s in actual["stages"]] == [303, 202, 303, 101, 404, 606]
    assert actual["stages"][3] == [101, 2, ["123", "456"], str(original["result"][0])]
