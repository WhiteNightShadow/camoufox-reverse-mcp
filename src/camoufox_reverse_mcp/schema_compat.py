"""Normalize advertised tool JSON schemas for strict OpenAI-compatible providers.

Some providers (e.g. Moonshot/Kimi) reject tool parameters whose JSON Schema
has no literal ``type`` key: ``400 ... At path 'properties.x': type is not
defined``. FastMCP renders every ``Optional[X]`` parameter as
``anyOf: [{...X}, {"type": "null"}]``, which trips that validation.

This module collapses those nullable unions into the plain branch in the
*advertised* schema only. Argument validation at call time still uses the
original function signatures, so omitting a parameter (or passing null from a
lenient client) keeps working exactly as before.
"""

from typing import Any


def _collapse_nullable_anyof(node: Any) -> None:
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            non_null = [
                branch
                for branch in any_of
                if not (isinstance(branch, dict) and branch.get("type") == "null")
            ]
            if len(non_null) < len(any_of):
                if len(non_null) == 1:
                    merged = dict(non_null[0])
                    for key, value in node.items():
                        if key != "anyOf" and key not in merged:
                            merged[key] = value
                    node.clear()
                    node.update(merged)
                else:
                    node["anyOf"] = non_null
                # A null default on a now non-nullable property confuses
                # strict validators; the parameter is optional anyway.
                if node.get("default") is None and "default" in node:
                    del node["default"]
        for value in node.values():
            _collapse_nullable_anyof(value)
    elif isinstance(node, list):
        for item in node:
            _collapse_nullable_anyof(item)


def normalize_tool_schemas(mcp: Any) -> int:
    """Rewrite registered tools' input schemas in place. Returns tool count."""
    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
    for tool in tools.values():
        params = getattr(tool, "parameters", None)
        if isinstance(params, dict):
            _collapse_nullable_anyof(params)
    return len(tools)
