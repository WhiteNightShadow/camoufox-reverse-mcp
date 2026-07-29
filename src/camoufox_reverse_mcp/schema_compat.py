"""Normalize advertised tool JSON schemas for strict OpenAI-compatible providers.

Some providers (e.g. Moonshot/Kimi) reject tool parameters whose JSON Schema
has no literal ``type`` key: ``400 ... At path 'properties.x': type is not
defined``. FastMCP renders every ``Optional[X]`` parameter as
``anyOf: [{...X}, {"type": "null"}]``, which trips that validation.

This module collapses only top-level, non-required tool parameters whose schema
is exactly ``T | null`` with a null default. Nested schemas, required nullable
values, and real multi-type unions are intentionally left untouched.

Only the *advertised* schema is changed. Argument validation at call time still
uses the original function signatures, so omitting a parameter (or passing null
from a lenient client) keeps working exactly as before.
"""

from typing import Any


def _collapse_nullable_anyof(node: Any) -> bool:
    """Collapse one exact ``T | null`` property schema in place.

    The caller is responsible for ensuring the property is not required. A
    boolean is returned so registration tests can assert what was rewritten.
    """
    if not isinstance(node, dict):
        return False

    any_of = node.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return False
    if "default" not in node or node["default"] is not None:
        return False

    null_branches = [
        branch
        for branch in any_of
        if isinstance(branch, dict) and branch.get("type") == "null"
    ]
    concrete_branches = [branch for branch in any_of if branch not in null_branches]
    if len(null_branches) != 1 or len(concrete_branches) != 1:
        return False

    concrete = concrete_branches[0]
    if not isinstance(concrete, dict) or not isinstance(concrete.get("type"), str):
        return False

    merged = dict(concrete)
    for key, value in node.items():
        if key not in {"anyOf", "default"} and key not in merged:
            merged[key] = value
    node.clear()
    node.update(merged)
    return True


def _normalize_input_schema(schema: Any) -> int:
    """Normalize safe top-level properties and return the rewrite count."""
    if not isinstance(schema, dict):
        return 0

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return 0

    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    rewritten = 0
    for name, property_schema in properties.items():
        if name not in required_names and _collapse_nullable_anyof(property_schema):
            rewritten += 1
    return rewritten


def normalize_tool_schemas(mcp: Any) -> int:
    """Rewrite registered tools' input schemas in place. Returns rewrite count."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    list_tools = getattr(tool_manager, "list_tools", None)
    tools = list_tools() if callable(list_tools) else []
    rewritten = 0
    for tool in tools:
        params = getattr(tool, "parameters", None)
        rewritten += _normalize_input_schema(params)
    return rewritten
