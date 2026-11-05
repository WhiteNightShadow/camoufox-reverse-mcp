"""Behaviour that an isolated uvx install depends on."""
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from camoufox_reverse_mcp import __main__ as entry
from camoufox_reverse_mcp import __version__, _playwright_patch, _stdio_guard

ROOT = Path(__file__).resolve().parents[1]
BUGGY = "x = pageError.location.url; y = pageError.location.lineNumber;\n"


def run_python(code):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)


def test_package_version_matches_project_metadata():
    declared = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    assert declared.group(1) == __version__


def test_version_flag_reports_without_starting_or_patching(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sys, "argv", ["camoufox-reverse-mcp", "--version"])
    monkeypatch.setattr(entry, "patch_playwright_pageerror", lambda: calls.append("patch"))
    monkeypatch.setattr(entry.mcp, "run", lambda **kwargs: calls.append("run"))
    with pytest.raises(SystemExit) as stop:
        entry.main()
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"camoufox-reverse-mcp {__version__}"
    assert calls == []


def test_server_path_still_patches_guards_then_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["camoufox-reverse-mcp", "--headless"])
    monkeypatch.setattr(entry, "patch_playwright_pageerror", lambda: calls.append("patch"))
    monkeypatch.setattr(entry, "reserve_stdout_for_protocol", lambda: calls.append("guard"))
    monkeypatch.setattr(entry.mcp, "run", lambda **kwargs: calls.append(kwargs))
    entry.main()
    assert calls == ["patch", "guard", {"transport": "stdio"}]


def test_guard_sends_text_to_stderr_and_keeps_protocol_bytes():
    result = run_python(
        "import sys\n"
        "from camoufox_reverse_mcp._stdio_guard import reserve_stdout_for_protocol as guard\n"
        "guard(); guard()\n"
        "print('Downloading package')\n"
        "sys.stdout.write('progress 10%\\r')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.buffer.write(b'{\"jsonrpc\":\"2.0\"}\\n')\n"
        "sys.stdout.buffer.flush()\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'{"jsonrpc":"2.0"}\n'
    assert b"Downloading package" in result.stderr
    assert b"progress 10%" in result.stderr


def test_guard_covers_rich_consoles_created_before_it_runs():
    pytest.importorskip("rich")
    result = run_python(
        "from rich.console import Console\n"
        "console = Console()\n"
        "from camoufox_reverse_mcp._stdio_guard import reserve_stdout_for_protocol as guard\n"
        "guard()\n"
        "console.print('Downloading package')\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert b"Downloading package" in result.stderr


def test_guard_leaves_streams_without_buffer_alone(monkeypatch):
    class Plain:
        def write(self, text):
            return len(text)

    plain = Plain()
    monkeypatch.setattr(sys, "stdout", plain)
    _stdio_guard.reserve_stdout_for_protocol()
    assert sys.stdout is plain


def test_transport_reads_the_real_buffer():
    result = run_python(
        "import sys\n"
        "real = sys.stdout.buffer\n"
        "from camoufox_reverse_mcp._stdio_guard import reserve_stdout_for_protocol as guard\n"
        "guard()\n"
        "assert sys.stdout.buffer is real\n"
        "assert sys.stdout.fileno() == sys.stderr.fileno()\n"
    )
    assert result.returncode == 0, result.stderr


def test_patch_does_not_write_through_hardlinks(tmp_path):
    installed = tmp_path / "coreBundle.js"
    installed.write_text(BUGGY, encoding="utf-8")
    shared_cache = tmp_path / "cached.js"
    try:
        os.link(installed, shared_cache)
    except OSError:
        pytest.skip("hard links are not available here")

    assert _playwright_patch._patch_file(installed) is True

    assert "pageError.location?.url ?? ''" in installed.read_text(encoding="utf-8")
    assert shared_cache.read_text(encoding="utf-8") == BUGGY
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cached.js", "coreBundle.js"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_patch_keeps_file_mode(tmp_path):
    installed = tmp_path / "coreBundle.js"
    installed.write_text(BUGGY, encoding="utf-8")
    installed.chmod(0o640)
    assert _playwright_patch._patch_file(installed) is True
    assert stat.S_IMODE(installed.stat().st_mode) == 0o640


def test_patch_is_idempotent(tmp_path):
    installed = tmp_path / "coreBundle.js"
    installed.write_text(BUGGY, encoding="utf-8")
    assert _playwright_patch._patch_file(installed) is True
    assert _playwright_patch._patch_file(installed) is False


def test_patch_failure_keeps_the_original_and_never_raises(tmp_path, monkeypatch, capsys):
    installed = tmp_path / "coreBundle.js"
    installed.write_text(BUGGY, encoding="utf-8")

    def refuse(source, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(_playwright_patch.os, "replace", refuse)
    assert _playwright_patch._patch_file(installed) is False
    assert installed.read_text(encoding="utf-8") == BUGGY
    assert [path.name for path in tmp_path.iterdir()] == ["coreBundle.js"]
    assert "could not write Playwright patch" in capsys.readouterr().err


async def test_check_environment_explains_how_to_fetch_a_missing_browser(monkeypatch):
    from camoufox_reverse_mcp import camoufox_runtime
    from camoufox_reverse_mcp.tools import environment

    monkeypatch.setattr(camoufox_runtime, "inspect_camoufox_runtime",
                        lambda: {"active": None, "installed": []})
    result = await environment.check_environment()
    assert result["overall_ok"] is False
    assert any("camoufox fetch" in text and "uvx" in text for text in result["recommendations"])


async def test_check_environment_does_not_nag_when_a_browser_is_active(monkeypatch):
    from camoufox_reverse_mcp import camoufox_runtime
    from camoufox_reverse_mcp.tools import environment

    monkeypatch.setattr(camoufox_runtime, "inspect_camoufox_runtime",
                        lambda: {"active": {"selector": "fixture"}, "installed": []})
    result = await environment.check_environment()
    assert not any("camoufox fetch" in text for text in result["recommendations"])
