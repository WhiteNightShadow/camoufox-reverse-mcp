"""
ast_rewriter.py - MCP-side JS AST rewriter for source-level JSVMP instrumentation.

Uses esprima-python (pure Python, ES2017 coverage). Unlike the v0.4.x page-side
Acorn approach, this runs entirely in the MCP process so it works on pages
that block external CDNs (RS/AK 412 challenges).

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

from .js_rewriter import (  # reuse the same runtime preamble
    INSTRUMENT_RUNTIME,
    build_source_site,
    source_identity,
)


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
    filter_property_names: list[str] | None = None,
    filter_object_names: list[str] | None = None,
    include_source_site: bool = False,
) -> tuple[str | None, dict]:
    """Rewrite JS source via esprima-python AST walk.

    Args:
        filter_property_names: If set, only rewrite member access where the
            property name is in this list (e.g. ['userAgent', 'platform']).
        filter_object_names: If set, only rewrite member access where the
            base object identifier is in this list (e.g. ['navigator', 'screen']).
        include_source_site: Add a stable source site id to each tap and return
            a sidecar map based on the original decoded source ranges.

    Returns:
        (rewritten_source_with_runtime, stats) on success.
        (None, stats) if parse failed — caller should fallback to regex.
    """
    import esprima

    stats: dict[str, Any] = {
        "parsed": False, "edits": 0,
        "member_edits": 0, "call_edits": 0, "method_edits": 0,
        "skipped": 0, "overlap_skipped": 0,
    }

    try:
        parse_options = {"range": True, "tolerant": True}
        if include_source_site:
            parse_options["loc"] = True
        tree = esprima.parseScript(src, options=parse_options)
        stats["parsed"] = True
    except Exception as e:
        stats["error"] = f"parse_failed: {type(e).__name__}: {e}"
        return None, stats

    edits: list[dict] = []
    tag_lit = json.dumps(tag)
    prop_filter = set(filter_property_names) if filter_property_names else None
    obj_filter = set(filter_object_names) if filter_object_names else None
    source_id = ""
    source_sha256 = ""
    if include_source_site:
        source_id, source_sha256 = source_identity(src)

    def site_for(node, kind: str, node_range: list[int]) -> dict | None:
        if not include_source_site:
            return None
        site = build_source_site(source_id, kind, node_range[0], node_range[1])
        loc = getattr(node, 'loc', None)
        if loc is not None:
            start = getattr(loc, 'start', None)
            end = getattr(loc, 'end', None)
            if start is not None:
                site["line"] = getattr(start, 'line', None)
                site["column"] = getattr(start, 'column', None)
            if end is not None:
                site["end_line"] = getattr(end, 'line', None)
                site["end_column"] = getattr(end, 'column', None)
        return site

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

        # v1.0.1: selective filtering for large files
        if prop_filter or obj_filter:
            # Extract property name for filtering
            if node.computed:
                # For computed access obj[key], check if key is a string literal
                prop_type = getattr(prop, 'type', None)
                if prop_type == 'Literal':
                    prop_name = getattr(prop, 'value', None)
                else:
                    prop_name = None
            else:
                prop_name = getattr(prop, 'name', None)

            # Extract object name for filtering
            obj_type = getattr(obj, 'type', None)
            obj_name = getattr(obj, 'name', None) if obj_type == 'Identifier' else None

            if prop_filter and (prop_name is None or prop_name not in prop_filter):
                return False
            if obj_filter and (obj_name is None or obj_name not in obj_filter):
                return False

        site = site_for(node, "tap_get", node_range)
        site_arg = f", {json.dumps(site['site_id'])}" if site else ""
        edits.append({
            "start": node_range[0], "end": node_range[1],
            "replacement": f"__mcp_tap_get({obj_src}, {key_src}, {tag_lit}{site_arg})",
            "kind": "member",
            "source_site": site,
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
            site = site_for(node, "tap_method", node_range)
            site_arg = f", {json.dumps(site['site_id'])}" if site else ""
            edits.append({
                "start": node_range[0], "end": node_range[1],
                "replacement": f"__mcp_tap_method({obj_src}, {key_src}, {args_src}, {tag_lit}{site_arg})",
                "kind": "method",
                "source_site": site,
            })
            return True
        elif ct == 'Identifier':
            fn_name = getattr(callee, 'name', None)
            if fn_name is None or fn_name in _SKIP_CALLEE_NAMES:
                return False
            site = site_for(node, "tap_call", node_range)
            site_arg = f", {json.dumps(site['site_id'])}" if site else ""
            edits.append({
                "start": node_range[0], "end": node_range[1],
                "replacement": f"__mcp_tap_call({fn_name}, null, {args_src}, {tag_lit}{site_arg})",
                "kind": "call",
                "source_site": site,
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

    # Parent and child AST nodes can produce overlapping source ranges. Applying
    # both replacements by their original offsets corrupts chained expressions.
    # Keep the outer edit; its replacement still evaluates the original inner
    # expression, while avoiding any behavior change for non-overlapping edits.
    edits.sort(key=lambda e: (e["start"], -e["end"]))
    non_overlapping: list[dict] = []
    for edit in edits:
        if non_overlapping and edit["end"] <= non_overlapping[-1]["end"]:
            stats["overlap_skipped"] += 1
            continue
        non_overlapping.append(edit)
    edits = non_overlapping

    stats["member_edits"] = sum(e["kind"] == "member" for e in edits)
    stats["call_edits"] = sum(e["kind"] == "call" for e in edits)
    stats["method_edits"] = sum(e["kind"] == "method" for e in edits)
    if include_source_site:
        stats.update({
            "source_id": source_id,
            "source_sha256": source_sha256,
            "source_sites": [
                e["source_site"] for e in edits if e.get("source_site")
            ],
        })

    edits.sort(key=lambda e: -e["start"])
    out = src
    for e in edits:
        out = out[:e["start"]] + e["replacement"] + out[e["end"]:]

    stats["edits"] = len(edits)
    return INSTRUMENT_RUNTIME + "\n" + out, stats
