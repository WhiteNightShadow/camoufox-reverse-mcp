import asyncio
from types import SimpleNamespace

from camoufox_reverse_mcp.schema_compat import (
    _collapse_nullable_anyof,
    normalize_tool_schemas,
)
from camoufox_reverse_mcp.server import SchemaCompatibleFastMCP, mcp


def test_collapse_nullable_anyof():
    schema = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "title": "Proxy",
    }
    assert _collapse_nullable_anyof(schema)
    assert schema == {"type": "string", "title": "Proxy"}


def test_collapse_nullable_anyof_keeps_siblings():
    schema = {
        "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
        "default": None,
        "description": "urls",
    }
    assert _collapse_nullable_anyof(schema)
    assert schema["type"] == "array"
    assert schema["description"] == "urls"
    assert "anyOf" not in schema


def test_collapse_keeps_real_unions():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert not _collapse_nullable_anyof(schema)
    assert schema["anyOf"] == [{"type": "string"}, {"type": "integer"}]


def test_collapse_keeps_nullable_multi_type_union():
    schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
            {"type": "null"},
        ],
        "default": None,
    }
    original = schema.copy()
    assert not _collapse_nullable_anyof(schema)
    assert schema == original


def test_normalize_skips_required_and_nested_nullable_properties():
    required_nullable = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
    }
    nested_nullable = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
    }
    parameters = {
        "type": "object",
        "required": ["required_value"],
        "properties": {
            "required_value": required_nullable,
            "payload": {
                "type": "object",
                "properties": {"nested_value": nested_nullable},
            },
        },
    }
    tool = SimpleNamespace(parameters=parameters)
    manager = SimpleNamespace(list_tools=lambda: [tool])

    assert normalize_tool_schemas(SimpleNamespace(_tool_manager=manager)) == 0
    assert "anyOf" in required_nullable
    assert "anyOf" in nested_nullable


def test_runtime_validation_still_accepts_omitted_and_null_optional_values():
    launch_tool = next(
        tool for tool in mcp._tool_manager.list_tools() if tool.name == "launch_browser"
    )
    omitted = launch_tool.fn_metadata.arg_model.model_validate({})
    explicit_null = launch_tool.fn_metadata.arg_model.model_validate({"proxy": None})

    assert omitted.proxy is None
    assert explicit_null.proxy is None


def test_tools_registered_late_are_normalized_when_listed():
    test_mcp = SchemaCompatibleFastMCP("schema-compat-test")

    @test_mcp.tool()
    async def late_tool(value: str | None = None) -> str | None:
        return value

    listed = asyncio.run(test_mcp.list_tools())
    value_schema = listed[0].inputSchema["properties"]["value"]
    registered = test_mcp._tool_manager.list_tools()[0]

    assert value_schema == {"title": "Value", "type": "string"}
    assert registered.fn_metadata.arg_model.model_validate({}).value is None
    assert registered.fn_metadata.arg_model.model_validate({"value": None}).value is None


def test_registered_tool_properties_have_type():
    tools = asyncio.run(mcp.list_tools())
    assert tools
    for tool in tools:
        props = tool.inputSchema.get("properties") or {}
        for name, prop in props.items():
            assert "type" in prop, f"{tool.name}.{name} has no type"


def test_instrumentation_advertises_opt_in_source_sites():
    tool = next(tool for tool in asyncio.run(mcp.list_tools())
                if tool.name == "instrumentation")
    source_site = tool.inputSchema["properties"]["include_source_site"]

    assert source_site["type"] == "boolean"
    assert source_site["default"] is False
