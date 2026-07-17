import asyncio

from camoufox_reverse_mcp.schema_compat import _collapse_nullable_anyof
from camoufox_reverse_mcp.server import mcp


def test_collapse_nullable_anyof():
    schema = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "title": "Proxy",
    }
    _collapse_nullable_anyof(schema)
    assert schema == {"type": "string", "title": "Proxy"}


def test_collapse_nullable_anyof_keeps_siblings():
    schema = {
        "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
        "description": "urls",
    }
    _collapse_nullable_anyof(schema)
    assert schema["type"] == "array"
    assert schema["description"] == "urls"
    assert "anyOf" not in schema


def test_collapse_keeps_real_unions():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    _collapse_nullable_anyof(schema)
    assert schema["anyOf"] == [{"type": "string"}, {"type": "integer"}]


def test_registered_tool_properties_have_type():
    tools = asyncio.run(mcp.list_tools())
    assert tools
    for tool in tools:
        props = tool.inputSchema.get("properties") or {}
        for name, prop in props.items():
            assert "type" in prop, f"{tool.name}.{name} has no type"
