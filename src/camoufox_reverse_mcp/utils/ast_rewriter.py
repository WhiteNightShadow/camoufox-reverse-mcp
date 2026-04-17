"""
ast_rewriter.py - MCP-side JS AST rewriter for source-level JSVMP instrumentation.

Uses esprima-python (pure Python, ES2017 coverage). Unlike the v0.4.x page-side
Acorn approach, this runs entirely in the MCP process so it works on pages
that block external CDNs (signature-based anti-bot 412 challenges).

Usage:
    from .ast_rewriter import ast_rewrite, INSTRUMENT_RUNTIME

    rewritten, stats = ast_rewrite(src, tag="vmp_target")
    if rewritten is None:
        # parse failed, caller should fallback to regex
        ...
"""
from __future__ import annotations

import json
from typing import Any, Callable

from .js_rewriter import INSTRUMENT_RUNTIME  # reuse the same runtime preamble


# ============ AST walker ============

def _walk(node: Any, parent: Any, callback: Callable[[Any, Any], None]) -> None:
    """Depth-first walker over an esprima AST."""
    if node is None or not hasattr(node, 'type'):
        return
    callback(node, parent)
    try:
        attrs = vars(node)
    except TypeError:
        return
    for key, val in attrs.items():
        if key in ('type', 'range', 'loc') or key.startswith('_'):
            continue
        if isinstance(val, list):
            for child in val:
                if child is not None and hasattr(child, 'type'):
                    _walk(child, node, callback)
        elif hasattr(val, 'type'):
            _walk(val, node, callback)


# Names that must never be tap-wrapped
_SKIP_CALLEE_NAMES = frozenset({
    '__mcp_tap_get', '__mcp_tap_call', '__mcp_tap_method',
    'require', 'eval',
})


def ast_rewrite(
    src: str,
    tag: str = "vmp",
    rewrite_member_access: bool = True,
    rewrite_calls: bool = True,
    max_edits: int = 20000,
) -> tuple[str | None, dict]:
    """Rewrite JS source via esprima-python AST walk.

    Returns:
        (rewritten_source_with_runtime, stats) on success.
        (None, stats) if parse failed — caller should fallback to regex.
    """
    import esprima

    stats: dict[str, Any] = {
        "parsed": False, "edits": 0,
        "member_edits": 0, "call_edits": 0, "method_edits": 0, "skipped": 0,
    }

    try:
        tree = esprima.parseScript(src, options={"range": True, "tolerant": True})
        stats["parsed"] = True
    except Exception as e:
        stats["error"] = f"parse_failed: {type(e).__name__}: {e}"
        return None, stats

    edits: list[dict] = []
    tag_lit = json.dumps(tag)

    def emit_member_tap(node, parent):
        pt = getattr(parent, 'type', None) if parent else None
        if pt == 'AssignmentExpression' and getattr(parent, 'left', None) is node:
            return False
        if pt == 'UpdateExpression':
            return False
        if pt in ('ArrayPattern', 'ObjectPattern'):
            return False
        if pt == 'CallExpression' and getattr(parent, 'callee', None) is node:
            return False
        if pt == 'NewExpression' and getattr(parent, 'callee', None) is node:
            return False
        if pt in ('ExportSpecifier', 'ImportSpecifier'):
            return False

        obj = node.object
        prop = node.property
        obj_range = getattr(obj, 'range', None)
        if obj_range is None:
            return False
        obj_src = src[obj_range[0]:obj_range[1]]

        if node.computed:
            prop_range = getattr(prop, 'range', None)
            if prop_range is None:
                return False
            key_src = src[prop_range[0]:prop_range[1]]
        else:
            name = getattr(prop, 'name', None)
            if name is None:
                return False
            key_src = json.dumps(name)

        node_range = getattr(node, 'range', None)
        if node_range is None:
            return False

        edits.append({
            "start": node_range[0], "end": node_range[1],
            "replacement": f"__mcp_tap_get({obj_src}, {key_src}, {tag_lit})",
            "kind": "member",
        })
        return True

    def emit_call_tap(node):
        callee = node.callee
        ct = getattr(callee, 'type', None)
        args = node.arguments or []
        args_parts: list[str] = []
        for a in args:
            arange = getattr(a, 'range', None)
            if arange is None:
                return False
            args_parts.append(src[arange[0]:arange[1]])
        args_src = "[" + ",".join(args_parts) + "]" if args_parts else "[]"
        node_range = getattr(node, 'range', None)
        if node_range is None:
            return False

        if ct == 'MemberExpression':
            obj = callee.object
            obj_range = getattr(obj, 'range', None)
            if obj_range is None:
                return False
            obj_src = src[obj_range[0]:obj_range[1]]
            if callee.computed:
                prange = getattr(callee.property, 'range', None)
                if prange is None:
                    return False
                key_src = src[prange[0]:prange[1]]
            else:
                name = getattr(callee.property, 'name', None)
                if name is None:
                    return False
                key_src = json.dumps(name)
            edits.append({
                "start": node_range[0], "end": node_range[1],
                "replacement": f"__mcp_tap_method({obj_src}, {key_src}, {args_src}, {tag_lit})",
                "kind": "method",
            })
            return True
        elif ct == 'Identifier':
            fn_name = getattr(callee, 'name', None)
            if fn_name is None or fn_name in _SKIP_CALLEE_NAMES:
                return False
            edits.append({
                "start": node_range[0], "end": node_range[1],
                "replacement": f"__mcp_tap_call({fn_name}, null, {args_src}, {tag_lit})",
                "kind": "call",
            })
            return True
        return False

    def on_node(node, parent):
        if len(edits) >= max_edits:
            return
        ntype = node.type
        if ntype == 'MemberExpression' and rewrite_member_access:
            if emit_member_tap(node, parent):
                stats["member_edits"] += 1
            else:
                stats["skipped"] += 1
        elif ntype == 'CallExpression' and rewrite_calls:
            if emit_call_tap(node):
                if edits[-1]["kind"] == "method":
                    stats["method_edits"] += 1
                else:
                    stats["call_edits"] += 1
            else:
                stats["skipped"] += 1

    _walk(tree, None, on_node)

    edits.sort(key=lambda e: -e["start"])
    out = src
    for e in edits:
        out = out[:e["start"]] + e["replacement"] + out[e["end"]:]

    stats["edits"] = len(edits)
    return INSTRUMENT_RUNTIME + "\n" + out, stats
