"""
ast_rewriter.py - MCP-side JS AST rewriter for source-level JSVMP instrumentation.

Uses esprima-python for ES2017 and bundled Acorn through local Node.js for
modern syntax. No parser code is fetched into the page or evaluated remotely.

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
    directive_end,
    rewrite_stage_calls,
    source_identity,
)


# ============ AST walker ============

def _walk(node: Any, parent: Any, callback: Callable[[Any, Any], Any]) -> None:
    """Depth-first walker over an esprima AST."""
    if node is None or not hasattr(node, 'type'):
        return
    if callback(node, parent) is False:
        return
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
    '__mcp_prepare_method', '__mcp_tap_stage',
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
    tap_stage_calls: bool = False,
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
        (None, stats) if parse failed; callers must report the limitation rather than assume a regex fallback is safe.
    """
    from .js_parser import parse_source

    stats: dict[str, Any] = {
        "parsed": False, "edits": 0, "stage_edits": 0,
        "member_edits": 0, "call_edits": 0, "method_edits": 0,
        "skipped": 0, "overlap_skipped": 0,
    }

    # Dispatcher taps run before the AST pass: the rewrite is a literal match of
    # one call shape, so it does not depend on esprima understanding modern
    # syntax, and member/call taps then see stable offsets in the same source.
    stage_sites: list[dict] = []
    if tap_stage_calls:
        stage_source_id = ""
        if include_source_site:
            stage_source_id, _ = source_identity(src)
        src, stage_edits, stage_sites = rewrite_stage_calls(
            src, tag, max_edits,
            include_source_site=include_source_site, source_id=stage_source_id,
        )
        stats["stage_edits"] = stage_edits

    try:
        tree, parser_backend = parse_source(src, locations=include_source_site)
        stats["parser_backend"] = parser_backend
        stats["source_type"] = getattr(tree, "sourceType", "script")
        stats["parsed"] = True
    except Exception as e:
        stats["error"] = f"parse_failed: {type(e).__name__}: {e}"
        if not stats["stage_edits"]:
            return None, stats
        # Stage taps are literal rewrites, so they stay usable even when the
        # parser cannot handle this file. Report the reduced scope explicitly.
        stats["edits"] = stats["stage_edits"]
        stats["stage_only"] = True
        insert_at = directive_end(src)
        if include_source_site:
            stats.update({
                "source_sites": stage_sites,
                "source_id": source_identity(src)[0],
            })
        return (src[:insert_at] + ";\n" + INSTRUMENT_RUNTIME + "\n"
                + src[insert_at:]), stats

    edits: list[dict] = []
    # A MemberExpression can be an assignment reference several pattern levels
    # below its enclosing assignment/loop. Protect the reference, while leaving
    # computed keys, defaults and the base object's reads eligible for taps.
    references: set[int] = set()

    def mark_target(node):
        if node is None:
            return
        kind = node.type
        if kind == 'MemberExpression':
            references.add(id(node))
        elif kind == 'ObjectPattern':
            for prop in node.properties:
                mark_target(prop.argument if prop.type == 'RestElement' else prop.value)
        elif kind == 'ArrayPattern':
            for element in node.elements:
                mark_target(element)
        elif kind == 'AssignmentPattern':
            mark_target(node.left)
        elif kind == 'RestElement':
            mark_target(node.argument)

    def find_references(node, parent):
        if node.type in ('AssignmentExpression', 'ForInStatement', 'ForOfStatement'):
            mark_target(node.left)
        elif node.type == 'UpdateExpression' or (
            node.type == 'UnaryExpression' and node.operator == 'delete'
        ):
            mark_target(node.argument)

    _walk(tree, None, find_references)

    def expression(node):
        value = src[node.range[0]:node.range[1]]
        # Esprima ranges omit grouping parentheses. A comma expression must
        # stay one helper argument / array element, not become several.
        return f'({value})' if node.type == 'SequenceExpression' else value

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

    def static_object_path(node):
        kind = getattr(node, 'type', None)
        if kind == 'Identifier':
            return node.name
        if kind == 'ThisExpression':
            return 'this'
        if kind == 'MemberExpression' and not getattr(node, 'optional', False):
            base = static_object_path(node.object)
            prop = node.property
            name = getattr(prop, 'value', None) if node.computed else getattr(prop, 'name', None)
            if base and isinstance(name, str) and name and (name.replace('$', '_').isidentifier()):
                return base + '.' + name
        return None

    def matches_filters(member):
        if prop_filter:
            prop = member.property
            name = getattr(prop, 'value', None) if member.computed else getattr(prop, 'name', None)
            if not isinstance(name, str) or name not in prop_filter:
                return False
        if obj_filter and static_object_path(member.object) not in obj_filter:
            return False
        return True

    def emit_member_tap(node, parent):
        if id(node) in references or node.object.type == 'Super':
            return False
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
        if pt == 'TaggedTemplateExpression' and parent.tag is node:
            return False
        if pt in ('ExportSpecifier', 'ImportSpecifier'):
            return False

        obj = node.object
        prop = node.property
        obj_range = getattr(obj, 'range', None)
        if obj_range is None:
            return False
        obj_src = expression(obj)

        if node.computed:
            prop_range = getattr(prop, 'range', None)
            if prop_range is None:
                return False
            key_src = expression(prop)
        else:
            name = getattr(prop, 'name', None)
            if name is None:
                return False
            key_src = json.dumps(name)

        node_range = getattr(node, 'range', None)
        if node_range is None:
            return False

        if not matches_filters(node):
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
            args_parts.append(expression(a))
        args_src = "[" + ",".join(args_parts) + "]" if args_parts else "[]"
        node_range = getattr(node, 'range', None)
        if node_range is None:
            return False

        if ct == 'MemberExpression':
            if not matches_filters(callee):
                return False
            obj = callee.object
            if obj.type == 'Super':
                return False
            obj_range = getattr(obj, 'range', None)
            if obj_range is None:
                return False
            obj_src = expression(obj)
            if callee.computed:
                prange = getattr(callee.property, 'range', None)
                if prange is None:
                    return False
                key_src = expression(callee.property)
            else:
                name = getattr(callee.property, 'name', None)
                if name is None:
                    return False
                key_src = json.dumps(name)
            site = site_for(node, "tap_method", node_range)
            site_arg = f", {json.dumps(site['site_id'])}" if site else ""
            edits.append({
                "start": node_range[0], "end": node_range[1],
                "replacement": f"__mcp_prepare_method({obj_src}, {key_src}, {tag_lit}{site_arg})({args_src})",
                "kind": "method",
                "source_site": site,
            })
            return True
        elif ct == 'Identifier':
            if prop_filter or obj_filter:
                return False
            fn_name = getattr(callee, 'name', None)
            if fn_name is None or fn_name in _SKIP_CALLEE_NAMES:
                return False
            site = site_for(node, "tap_call", node_range)
            site_arg = f", {json.dumps(site['site_id'])}" if site else ", void 0"
            edits.append({
                "start": node_range[0], "end": node_range[1],
                "replacement": f"__mcp_tap_call({fn_name}, void 0, {args_src}, {tag_lit}{site_arg}, {json.dumps(fn_name)})",
                "kind": "call",
                "source_site": site,
            })
            return True
        return False

    def on_node(node, parent):
        # Fail closed if a future parser emits these node shapes. Optional
        # chains carry short-circuit state, and private names are not keys.
        ntype = node.type
        member = node.callee if ntype in ('CallExpression', 'OptionalCallExpression') else node
        special_member = getattr(member, 'type', None) in ('MemberExpression', 'OptionalMemberExpression')
        private_or_super = special_member and (
            getattr(member.object, 'type', None) == 'Super'
            or getattr(member.property, 'type', None) in ('PrivateIdentifier', 'PrivateName')
        )
        if (ntype in ('ChainExpression', 'OptionalMemberExpression', 'OptionalCallExpression')
                or getattr(node, 'optional', False)
                or getattr(member, 'optional', False)
                or private_or_super):
            stats["skipped"] += 1
            stats.setdefault("semantic_skips", []).append("optional_private_or_super")
            return False
        # Identifier calls inside `with` may carry an implicit object receiver.
        # Without lexical resolution a bare-call rewrite cannot retain it.
        if node.type == 'WithStatement':
            stats["skipped"] += 1
            stats.setdefault("semantic_skips", []).append("with_statement")
            return False
        if len(edits) >= max_edits:
            return
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
            ] + stage_sites,
        })

    edits.sort(key=lambda e: -e["start"])
    out = src
    for e in edits:
        out = out[:e["start"]] + e["replacement"] + out[e["end"]:]

    stats["edits"] = len(edits) + stats["stage_edits"]
    # Keep the original directive prologue active (also when terminated by ASI).
    directive_at = directive_end(src)
    for statement in tree.body:
        expr = getattr(statement, 'expression', None)
        # esprima-python drops directive annotations after an empty string.
        # Recognize unparenthesized string statements from their source ranges.
        if not (statement.type == 'ExpressionStatement'
                and getattr(expr, 'type', None) == 'Literal'
                and isinstance(expr.value, str)
                and statement.range[0] == expr.range[0]):
            break
        directive_at = statement.range[1]
    return (out[:directive_at] + ";\n" + INSTRUMENT_RUNTIME + "\n"
            + out[directive_at:]), stats
