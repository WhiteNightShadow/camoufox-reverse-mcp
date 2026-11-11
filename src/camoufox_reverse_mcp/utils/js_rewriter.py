"""
js_rewriter.py - JS 源码插桩改写器。

提供两个模式:
  regex_rewrite:  无依赖的保守语法子集；无法证明上下文安全时原样跳过。
  ast_rewrite:    MCP 侧 esprima AST 改写（见 ast_rewriter.py）。

无全量语义等价或覆盖率承诺。日志只预览 primitive，对象/函数使用类型占位；
源码/堆栈/耗时及 MCP 全局名称仍可被观察，运行时应在业务修改内建函数前安装。

实际改写会在原 directive prologue 后插入 __mcp_tap_get / __mcp_tap_call 的运行时定义,
并通过 window.__mcp_vmp_log 输出。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Tuple


INSTRUMENT_RUNTIME = r"""
(function(){
  'use strict';
  if (window.__mcp_tap_installed) return;
  window.__mcp_tap_installed = true;
  window.__mcp_vmp_log = window.__mcp_vmp_log || [];
  // Capture intrinsics once; never consult a target function's .apply/.name.
  var apply = Reflect.apply;
  var arrayPush = Array.prototype.push;
  var slice = Array.prototype.slice;
  var stringSlice = String.prototype.slice;
  var stringIndexOf = String.prototype.indexOf;
  var stringifyPrimitive = String;
  var now = Date.now;
  var CAP = 20000;
  var siteSeq = 0;
  var sampleCredit = 0;
  window.__mcp_tap_cfg = window.__mcp_tap_cfg || { sampling: 1, tagFilter: null };
  function _push(e, siteId){
    var log = window.__mcp_vmp_log;
    if (log.length >= CAP) return;
    if (typeof siteId === 'string' && siteId) {
      e.site_id = siteId;
      e.seq = siteSeq++;
    }
    e.ts = now();
    apply(arrayPush, log, [e]);
  }
  function _enabled(tag, error){
    var cfg = window.__mcp_tap_cfg;
    var f = cfg.tagFilter;
    if (f && (typeof f !== 'string' || typeof tag !== 'string' ||
              apply(stringIndexOf, tag, [f]) === -1)) return false;
    if (error) return true; // Preserve the existing unsampled call-error event.
    var sampling = cfg.sampling;
    if (typeof sampling !== 'number' || !(sampling > 0)) return false;
    if (sampling >= 1) return true;
    // Deterministic sampling has no access to the program's random stream.
    sampleCredit += sampling;
    if (sampleCredit < 1) return false;
    sampleCredit -= 1;
    return true;
  }
  function _preview(v){
    if (v === null) return 'null';
    var t = typeof v;
    // No enumeration, constructor/name, toJSON or user-defined coercion.
    if (t === 'object') return '[object]';
    if (t === 'function') return '[fn]';
    var s = stringifyPrimitive(v);
    return s.length > 120 ? apply(stringSlice, s, [0, 120]) + '...' : s;
  }
  function _getLog(obj, key, value, tag, siteId){
    try {
      if (_enabled(tag, false)) {
        _push({ type:'tap_get', tag:tag, key:_preview(key),
                objType:typeof obj, value:_preview(value) }, siteId);
      }
    } catch (ignored) {} // A broken log sink must not replace program results.
  }
  function _invoke(fn, thisArg, args, tag, siteId, name, method){
    var r;
    try { r = apply(fn, thisArg, args); } catch (e) {
      try {
        if (!method && _enabled(tag, true)) {
          _push({ type:'tap_call_err', tag:tag, name:name, err:_preview(e) }, siteId);
        }
      } catch (ignored) {}
      throw e;
    }
    try {
      if (_enabled(tag, false)) {
        var event = { type:method ? 'tap_method' : 'tap_call', tag:tag,
                      argc:args.length, arg0:args.length ? _preview(args[0]) : null,
                      ret:_preview(r) };
        if (method) {
          event.objType = typeof thisArg;
          event.method = name;
        } else {
          event.name = name;
        }
        _push(event, siteId);
      }
    } catch (ignored) {}
    return r;
  }
  window.__mcp_tap_get = function(obj, key, tag, siteId){
    var val = obj[key]; // Throw the original getter/coercion exception unchanged.
    _getLog(obj, key, val, tag, siteId);
    return val;
  };
  window.__mcp_tap_call = function(fn, thisArg, args, tag, siteId, name){
    return _invoke(fn, thisArg, args, tag, siteId,
                   typeof name === 'string' ? name : 'anon', false);
  };
  window.__mcp_prepare_method = function(obj, key, tag, siteId){
    // This phase runs before argument evaluation, including spread/yield/await.
    // Only the actual property read coerces key; previews never coerce objects.
    var fn = obj[key];
    return function(args){
      return _invoke(fn, obj, args, tag, siteId, _preview(key), true);
    };
  };
  // Legacy helper signature retained. Already-evaluated args cannot recover
  // getter-before-arguments ordering; new rewrites use prepare_method instead.
  window.__mcp_tap_method = function(obj, key, args, tag, siteId){
    return window.__mcp_prepare_method(obj, key, tag, siteId)(args);
  };
  // Dispatcher-level tap for flattened VMPs: one event per state, pushed when
  // the state returns (nested states first; a state that throws is not logged).
  // Arguments are copied before the call so the trace keeps the original list.
  window.__mcp_tap_stage = function(p, fn, thisArg, args, tag, siteId){
    var list = apply(slice, args, [0]);
    var r = apply(fn, thisArg, [p].concat(list));
    try {
      if (_enabled(tag, false)) {
        var n = list.length > 3 ? 3 : list.length;
        var previews = [];
        for (var i = 0; i < n; i++) {
          try { apply(arrayPush, previews, [_preview(list[i])]); }
          catch (e) { apply(arrayPush, previews, ['?']); }
        }
        _push({ type:'tap_stage', tag:tag, p:p, argc:list.length,
                args:previews, ret:_preview(r) }, siteId);
      }
    } catch (ignored) {}
    try { _maybeBreak(p, list, r, tag, siteId); } catch (ignored) {}
    return r;
  };
  // ---- stage break: bounded deep capture for selected states ----
  // Opt-in only: configured through stage_break(), which writes __mcp_tap_cfg.breaks.
  var SNAP_MAX_STR = 200;
  function _snap(v, depth, budget){
    if (v === null) return null;
    var t = typeof v;
    if (t === 'number' || t === 'boolean') return v;
    if (t === 'undefined') return {__t:'undefined'};
    if (t === 'string') {
      return v.length > SNAP_MAX_STR
        ? apply(stringSlice, v, [0, SNAP_MAX_STR]) + '...'
        : v;
    }
    if (t === 'function' || t === 'symbol' || t === 'bigint') return {__t:t};
    if (depth <= 0) return {__t:'object'};
    var i, out;
    try {
      if (Object.prototype.toString.call(v) === '[object Array]') {
        out = [];
        var len = v.length;
        var take = len > budget ? budget : len;
        for (i = 0; i < take; i++) apply(arrayPush, out, [_snap(v[i], depth - 1, budget)]);
        if (len > take) apply(arrayPush, out, [{__t:'truncated', length:len}]);
        return out;
      }
      out = {};
      var keys = Object.keys(v);
      var count = keys.length > budget ? budget : keys.length;
      for (i = 0; i < count; i++) out[keys[i]] = _snap(v[keys[i]], depth - 1, budget);
      if (keys.length > count) out.__truncated = keys.length;
      return out;
    } catch (e) { return {__t:'unreadable'}; }
  }
  function _stageStack(){
    var out = [];
    try {
      var lines = String(new Error().stack || '').split('\n');
      for (var i = 0; i < lines.length && out.length < 20; i++) {
        var line = lines[i];
        // Drop the bridge frames: they describe this probe, not the target code.
        if (apply(stringIndexOf, line, ['chrome://juggler']) !== -1) continue;
        if (apply(stringIndexOf, line, ['__mcp_tap_stage']) !== -1) continue;
        if (apply(stringIndexOf, line, ['_maybeBreak']) !== -1) continue;
        apply(arrayPush, out, [line.replace(/^\s+/, '')]);
      }
    } catch (e) {}
    return out;
  }
  function _maybeBreak(p, list, ret, tag, siteId){
    var cfg = window.__mcp_tap_cfg;
    var breaks = cfg && cfg.breaks;
    if (!breaks) return;
    var b = breaks[p];
    if (!b) return;
    if (b.limit > 0 && (b.hits || 0) >= b.limit) return;
    b.hits = (b.hits || 0) + 1;
    var depth = b.depth || 2, budget = b.maxItems || 50;
    var event = { type:'tap_stage_break', tag:tag, p:p, hit:b.hits, argc:list.length };
    event.args = _snap(list, depth, budget);
    if (b.capture !== 'args_only') event.ret = _snap(ret, depth, budget);
    if (b.capture === 'args_ret_stack') event.stack = _stageStack();
    _push(event, siteId);
  }
})();
"""


# ============ Regex-based rewrite ============

# This is a whole-program whitelist, not a search/replace over arbitrary JS.
# Only semicolon-separated primitive/identifier/bracket reads and initialized
# single variable declarations are accepted. All other programs pass through.
_IDENT = r'[A-Za-z_$][A-Za-z0-9_$]*'
_STRING = r"\"(?:[^\"\\\r\n]|\\[^\r\n])*\"|'(?:[^'\\\r\n]|\\[^\r\n])*'"
_ATOM = rf'(?:{_STRING}|[0-9]+|{_IDENT})'
_MEMBER_BRACKET_RE = re.compile(
    rf'(?P<object>{_IDENT})\s*\[\s*(?P<key>{_ATOM})\s*\]'
)
_TRIVIA = r'(?:\s|/\*[^*]*\*+(?:[^/*][^*]*\*+)*/|//[^\r\n]*(?:\r?\n|$))*'
_SIMPLE_STATEMENT_RE = re.compile(
    rf'{_TRIVIA}(?P<declaration>(?:var|let|const)\s+{_IDENT}\s*=\s*)?'
    rf'(?P<expression>{_MEMBER_BRACKET_RE.pattern}|{_ATOM})'
    rf'{_TRIVIA}(?:;|$)'
)
_RESERVED = frozenset("""
await break case catch class const continue debugger default delete do else
export extends finally for function if import in instanceof let new return
super switch this throw try typeof var void while with yield enum implements
interface package private protected public static null true false
""".split())
_REGEX_BOUNDARY = (
    "Only whole programs of simple reads and single initialized declarations "
    "are recognized; calls, targets, operators, templates, regex literals and "
    "other syntax are passed through unchanged. This is not general JS equivalence."
)


def _simple_statements(src: str) -> list[re.Match] | None:
    statements = []
    pos = 0
    while pos < len(src):
        if re.fullmatch(_TRIVIA, src[pos:]):
            break
        match = _SIMPLE_STATEMENT_RE.match(src, pos)
        if match is None:
            return None
        statements.append(match)
        pos = match.end()
    return statements


def source_identity(src: str) -> tuple[str, str]:
    """Return a compact content id and the full SHA-256 for decoded JS text."""
    digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
    return digest[:16], digest


def build_source_site(source_id: str, kind: str, start: int, end: int) -> dict:
    """Build deterministic source-site metadata for one original text range."""
    return {
        "site_id": f"{source_id}:{start}:{end}:{kind}",
        "kind": kind,
        "start": start,
        "end": end,
    }


# ============ Stage / dispatcher-call rewrite ============

# Control-flow-flattened JSVMP dispatchers funnel every state through a single
# call shape, e.g. ``return l.apply(this, [9425].concat(Array.prototype.slice
# .call(arguments)))``. Rewriting that shape gives one event per state (state id
# ``p``) without touching the dispatcher body, which is usually the most useful
# evidence when a VM must be matched stage-by-stage.
_STAGE_CALL_RE = re.compile(
    r"(?<![\w$.])return\s+(?P<fn>[A-Za-z_$][A-Za-z0-9_$]*)\.apply\(\s*this\s*,\s*"
    r"\[\s*(?P<p>\d+)\s*\]\s*\.concat\(\s*"
    r"Array\.prototype\.slice\.call\(\s*arguments\s*\)\s*\)\s*\)"
)

_LEADING_STRING_RE = re.compile(
    rf"{_TRIVIA}(?:{_STRING}){_TRIVIA}(?:;|(?=\r?\n|$))"
)
_TRIVIA_RE = re.compile(_TRIVIA)
# A line break only ends a string statement when the next token cannot continue
# the expression: '"a"\n.split(",")' is one statement, not a directive.
_CONTINUES_EXPRESSION_RE = re.compile(
    r"[.\[(`*/%<>=&|^?,:]|!=|([+-])(?!\1)|in(?![\w$])|instanceof(?![\w$])"
)


def shebang_end(src: str) -> int:
    """Return the offset just past a leading ``#!`` line."""
    if not src.startswith("#!"):
        return 0
    newline = src.find("\n")
    return len(src) if newline == -1 else newline + 1


def directive_end(src: str) -> int:
    """Return the offset just past a leading directive prologue.

    Best effort, for sources the parser cannot read; parsed sources take the
    prologue from the AST instead.
    """
    pos = shebang_end(src)
    while True:
        match = _LEADING_STRING_RE.match(src, pos)
        if match is None:
            return pos
        if not match.group().endswith(";"):
            following = _TRIVIA_RE.match(src, match.end()).end()
            if _CONTINUES_EXPRESSION_RE.match(src, following):
                return pos
        pos = match.end()


def stage_call_edits(
    src: str,
    tag: str = "vmp",
    max_rewrites: int = 20000,
    include_source_site: bool = False,
    source_id: str = "",
    allowed_starts: set[int] | None = None,
) -> list[dict]:
    """Find dispatcher entry calls and build their ``__mcp_tap_stage`` edits.

    Only the exact ``return fn.apply(this, [<int>].concat(...arguments))`` shape
    is matched. ``allowed_starts`` keeps only matches at those offsets, i.e. the
    starts of real return statements when the source could be parsed.
    """
    if include_source_site and not source_id:
        source_id, _ = source_identity(src)
    edits: list[dict] = []
    for match in _STAGE_CALL_RE.finditer(src):
        if len(edits) >= max_rewrites:
            break
        start, end = match.span()
        if allowed_starts is not None and start not in allowed_starts:
            continue
        site = None
        site_arg = ""
        if include_source_site:
            site = build_source_site(source_id, "tap_stage", start, end)
            site_arg = ", " + json.dumps(site["site_id"])
        edits.append({
            "start": start, "end": end, "call_start": match.start("fn"), "site": site,
            "replacement": (f"return __mcp_tap_stage({match.group('p')}, {match.group('fn')}, "
                            f"this, arguments, {json.dumps(tag)}{site_arg})"),
        })
    return edits


def apply_stage_edits(src: str, edits: list[dict]) -> str:
    """Apply edits from :func:`stage_call_edits` (sorted, non-overlapping)."""
    parts: list[str] = []
    pos = 0
    for edit in edits:
        parts.append(src[pos:edit["start"]])
        parts.append(edit["replacement"])
        pos = edit["end"]
    parts.append(src[pos:])
    return "".join(parts)


def rewrite_stage_calls(
    src: str,
    tag: str = "vmp",
    max_rewrites: int = 20000,
    include_source_site: bool = False,
    source_id: str = "",
) -> Tuple[str, int, list[dict]]:
    """Rewrite dispatcher entry calls into ``__mcp_tap_stage`` taps.

    Only the exact ``return fn.apply(this, [<int>].concat(...arguments))`` shape
    is matched; every other call site is left byte-for-byte unchanged.
    """
    edits = stage_call_edits(src, tag, max_rewrites, include_source_site, source_id)
    return apply_stage_edits(src, edits), len(edits), [e["site"] for e in edits if e["site"]]


def _rewrite_member_access(
    src: str,
    tag: str,
    max_rewrites: int = 5000,
    include_source_site: bool = False,
    source_id: str = "",
) -> Tuple[str, int, list[dict]]:
    statements = _simple_statements(src)
    if statements is None:
        return src, 0, []
    edits = []
    source_sites: list[dict] = []
    for statement in statements:
        if len(edits) >= max_rewrites:
            break
        match = _MEMBER_BRACKET_RE.fullmatch(statement.group('expression'))
        if match is None:
            continue
        obj, key = match.group('object', 'key')
        if obj in _RESERVED or obj in ('require', 'module', 'exports', 'console') or obj.startswith('__mcp_'):
            continue
        # Reserved tokens cannot stand for an object binding. Literal keys are
        # fine; unsupported syntax is never transformed into newly valid code.
        if key in _RESERVED and key not in ('null', 'true', 'false'):
            continue
        start, end = statement.span('expression')
        site_arg = ""
        if include_source_site:
            site = build_source_site(source_id, "tap_get", start, end)
            source_sites.append(site)
            site_arg = f",{json.dumps(site['site_id'])}"
        edits.append((start, end, f"__mcp_tap_get({obj},{key},{json.dumps(tag)}{site_arg})"))
    for start, end, replacement in reversed(edits):
        src = src[:start] + replacement + src[end:]
    return src, len(edits), source_sites


def regex_rewrite(src: str, tag: str = "vmp",
                  rewrite_member_access: bool = True,
                  max_rewrites: int = 5000,
                  include_source_site: bool = False,
                  tap_stage_calls: bool = False) -> Tuple[str, dict]:
    """Conservative dependency-free fallback, with explicit semantic boundaries.

    Unrecognized programs are returned byte-for-byte, without a preamble. In
    particular modern syntax parse failures do not trigger heuristic rewrites.

    ``tap_stage_calls`` matches the dispatcher call shape literally in programs
    outside the simple-statement whitelist. A whitelisted program has no
    functions, so a match there is text inside a string and is left alone.
    Without a parser, a match inside a string literal elsewhere is rewritten
    too; ``ast_rewrite`` checks matches against the parsed program instead.
    """
    statements = _simple_statements(src)
    stats = {"member_access_rewrites": 0, "stage_rewrites": 0,
             "semantic_boundary": _REGEX_BOUNDARY}
    if statements is None:
        stats["skipped_reason"] = "unsupported_program_syntax"
    new_src = src
    source_id = ""
    source_sha256 = ""
    source_sites: list[dict] = []
    if include_source_site:
        source_id, source_sha256 = source_identity(src)
    if rewrite_member_access and statements is not None:
        new_src, n, source_sites = _rewrite_member_access(
            src, tag, max_rewrites,
            include_source_site=include_source_site, source_id=source_id,
        )
        stats["member_access_rewrites"] = n
    if tap_stage_calls and statements is None:
        new_src, n, stage_sites = rewrite_stage_calls(
            new_src, tag, max_rewrites,
            include_source_site=include_source_site, source_id=source_id,
        )
        stats["stage_rewrites"] = n
        source_sites.extend(stage_sites)
    if include_source_site:
        stats.update({
            "source_id": source_id,
            "source_sha256": source_sha256,
            "source_sites": source_sites,
        })
    if not stats["member_access_rewrites"] and not stats["stage_rewrites"]:
        return src, stats
    insert_at = directive_end(new_src)
    return (new_src[:insert_at] + ";\n" + INSTRUMENT_RUNTIME + "\n"
            + new_src[insert_at:]), stats


# ============ AST-based rewrite (via page-side Acorn) ============

ACORN_REWRITE_JS_TEMPLATE = r"""
async (src, tag, opts) => {
  // Load Acorn if not loaded
  if (!window.acorn) {
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/acorn/8.11.3/acorn.min.js';
      s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/acorn-walk/8.3.2/walk.min.js';
      s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
  }
  let ast;
  try {
    ast = acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'script', allowReturnOutsideFunction: true });
  } catch (e) {
    return { ok: false, error: 'parse_error: ' + e.message };
  }

  const edits = [];
  const tagLit = JSON.stringify(tag);
  const references = new Set();
  const skipNames = new Set(['eval', 'require', '__mcp_tap_get', '__mcp_tap_call',
                             '__mcp_tap_method', '__mcp_prepare_method']);
  const semanticSkips = [];
  function walk(node, parent, visit) {
    if (!node || typeof node.type !== 'string' || visit(node, parent) === false) return;
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key === 'range') continue;
      const val = node[key];
      if (Array.isArray(val)) {
        for (const child of val) walk(child, node, visit);
      } else if (val && typeof val === 'object') walk(val, node, visit);
    }
  }
  function markTarget(node) {
    if (!node) return;
    if (node.type === 'MemberExpression') references.add(node);
    else if (node.type === 'ObjectPattern') {
      for (const p of node.properties) markTarget(p.type === 'RestElement' ? p.argument : p.value);
    } else if (node.type === 'ArrayPattern') {
      for (const e of node.elements) markTarget(e);
    } else if (node.type === 'AssignmentPattern') markTarget(node.left);
    else if (node.type === 'RestElement') markTarget(node.argument);
  }
  walk(ast, null, node => {
    if (['AssignmentExpression', 'ForInStatement', 'ForOfStatement'].includes(node.type)) markTarget(node.left);
    if (node.type === 'UpdateExpression' ||
        (node.type === 'UnaryExpression' && node.operator === 'delete')) markTarget(node.argument);
  });
  function expression(node) {
    const value = src.slice(node.start, node.end);
    return node.type === 'SequenceExpression' ? `(${value})` : value;
  }
  function keySource(node) {
    return node.computed ? expression(node.property) : JSON.stringify(node.property.name);
  }
  walk(ast, null, (node, parent) => {
    const member = node.type === 'CallExpression' ? node.callee : node;
    if (node.type === 'WithStatement' || node.type === 'ChainExpression' ||
        node.type === 'OptionalMemberExpression' || node.type === 'OptionalCallExpression' ||
        node.optional || member.optional ||
        (member.type === 'MemberExpression' &&
         (member.object.type === 'Super' || ['PrivateIdentifier', 'PrivateName'].includes(member.property.type)))) {
      semanticSkips.push(node.type);
      return false;
    }
    if (node.type === 'MemberExpression' && opts.rewriteMemberAccess) {
      if (references.has(node)) return;
      if (parent && ((['CallExpression', 'NewExpression'].includes(parent.type) && parent.callee === node) ||
                     (parent.type === 'TaggedTemplateExpression' && parent.tag === node))) return;
      edits.push({start:node.start, end:node.end,
                  replacement:`__mcp_tap_get(${expression(node.object)}, ${keySource(node)}, ${tagLit})`});
    } else if (node.type === 'CallExpression' && opts.rewriteCalls) {
      const argsSrc = '[' + node.arguments.map(expression).join(',') + ']';
      if (node.callee.type === 'MemberExpression') {
        const me = node.callee;
        edits.push({start:node.start, end:node.end,
                    replacement:`__mcp_prepare_method(${expression(me.object)}, ${keySource(me)}, ${tagLit})(${argsSrc})`});
      } else if (node.callee.type === 'Identifier' && !skipNames.has(node.callee.name)) {
        const fn = expression(node.callee);
        edits.push({start:node.start, end:node.end,
                    replacement:`__mcp_tap_call(${fn}, void 0, ${argsSrc}, ${tagLit}, void 0, ${JSON.stringify(fn)})`});
      }
    }
  });
  // Keep only outer edits. Original source offsets cannot apply nested edits.
  edits.sort((a, b) => a.start - b.start || b.end - a.end);
  const kept = [];
  for (const e of edits) {
    if (!kept.length || e.start >= kept[kept.length - 1].end) kept.push(e);
  }
  let out = src;
  for (const e of kept.slice().reverse()) {
    out = out.slice(0, e.start) + e.replacement + out.slice(e.end);
  }
  // This legacy template returns only the body. Expose the safe insertion point
  // for callers installing INSTRUMENT_RUNTIME in the same script.
  let directiveEnd = 0;
  for (const statement of ast.body) {
    const e = statement.expression;
    if (statement.type !== 'ExpressionStatement' || !e || e.type !== 'Literal' ||
        typeof e.value !== 'string' || statement.start !== e.start) break;
    directiveEnd = statement.end;
  }
  return { ok:true, src:out, edit_count:kept.length,
           runtime_insert_offset:directiveEnd, semantic_skips:semanticSkips };
}
"""
