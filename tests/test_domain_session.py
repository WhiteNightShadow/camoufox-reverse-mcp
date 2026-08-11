"""Tests for v1.0.0: session module removed, verify clean removal."""
import re
from pathlib import Path

import pytest


def test_session_module_not_importable():
    """v1.0.0: domain_session module should be gone."""
    with pytest.raises(ImportError):
        from camoufox_reverse_mcp.domain_session import DomainSessionStore


def test_session_hooks_not_importable():
    """v1.0.0: session_hooks module should be gone."""
    with pytest.raises(ImportError):
        from camoufox_reverse_mcp.session_hooks import session_record_tool_call


def test_session_tools_removed():
    """v1.0.0: all session/assertion tools should be gone."""
    from camoufox_reverse_mcp.server import mcp
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    removed = {
        "start_reverse_session", "stop_reverse_session", "get_session_snapshot",
        "list_sessions", "attach_domain_readonly", "export_session", "import_session",
        "add_assertion", "verify_assertion", "list_assertions", "remove_assertion",
        "reverify_all_assertions_on_domain", "verify_against_session",
    }
    still_present = removed & tool_names
    assert not still_present, f"Session tools still registered: {still_present}"


def test_verify_signer_offline_exists():
    """v1.0.0: replacement for verify_against_session."""
    from camoufox_reverse_mcp.server import mcp
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "verify_signer_offline" in tool_names


def test_new_tools_count():
    """v1.0.0 should have ~32 tools."""
    from camoufox_reverse_mcp.server import mcp
    tools = mcp._tool_manager.list_tools()
    assert 25 <= len(tools) <= 40, f"Expected 25-40 tools, got {len(tools)}"


def test_version():
    from camoufox_reverse_mcp import __version__
    assert __version__ == "1.2.0"

    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    project_version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert project_version is not None
    assert project_version.group(1) == __version__
