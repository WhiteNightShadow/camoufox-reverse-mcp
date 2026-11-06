"""Keep the README, tool reference and changelog in step with the code."""
import functools
import re
import subprocess
import sys
from pathlib import Path

import pytest

from camoufox_reverse_mcp import __version__

ROOT = Path(__file__).resolve().parents[1]
READMES = ("README.md", "README_en.md")
TOOL_DOCS = ("docs/TOOLS.md", "docs/TOOLS_en.md")
CHANGELOGS = ("CHANGELOG.md", "CHANGELOG_en.md")
LINKED_DOCS = READMES + TOOL_DOCS + CHANGELOGS


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def registered_tools():
    """Ask a clean process: importing tool modules in tests registers extras."""
    code = (
        "import asyncio\n"
        "from camoufox_reverse_mcp.server import mcp\n"
        "print(' '.join(tool.name for tool in asyncio.run(mcp.list_tools())))\n"
    )
    output = subprocess.check_output([sys.executable, "-c", code], text=True, timeout=120)
    return frozenset(output.strip().splitlines()[-1].split())


def heading_anchors(text):
    outside_code = re.sub(r"```.*?```", "", text, flags=re.S)
    anchors = set()
    for heading in re.findall(r"^#{1,6} +(.+)$", outside_code, re.M):
        cleaned = re.sub(r"[^\w\- ]", "", heading.strip().lower())
        anchors.add(cleaned.replace(" ", "-"))
    return anchors


@pytest.mark.parametrize("name", TOOL_DOCS)
def test_tool_reference_lists_exactly_the_registered_tools(name):
    rows = re.findall(r"^\| (`.+?) \|", read(name), re.M)
    documented = {match for row in rows for match in re.findall(r"`([a-z_]+)", row)}
    assert documented == registered_tools()


@pytest.mark.parametrize("name", READMES)
def test_readme_tool_counts_match_the_server(name):
    text = read(name)
    counts = [int(count) for count in re.findall(r"^\| \[.+?\]\(.+?\) \| (\d+) \|", text, re.M)]
    assert sum(counts) == len(registered_tools())
    assert re.search(rf"^(共 )?{sum(counts)}( 个工具，分| tools in) {len(counts)}", text, re.M)


@pytest.mark.parametrize("name", READMES)
def test_readme_has_no_version_sections_before_the_install_steps(name):
    text = read(name)
    assert not re.search(r"^#{1,3} +v\d", text, re.M)
    install = re.search(r"^## (快速开始|Quick Start)$", text, re.M).start()
    history = re.search(r"^## (更新记录|Changelog)$", text, re.M).start()
    assert install < history
    assert f"v{__version__}" in text[history:]


@pytest.mark.parametrize("name", CHANGELOGS)
def test_changelog_starts_with_the_current_release(name):
    assert re.search(r"^## (v\d+\.\d+\.\d+)", read(name), re.M).group(1) == f"v{__version__}"


@pytest.mark.parametrize("name", LINKED_DOCS)
def test_relative_links_and_anchors_resolve(name):
    source = ROOT / name
    for target in re.findall(r"\]\((?!https?://)([^)\s]+)\)", read(name)):
        path, _, anchor = target.partition("#")
        linked = (source.parent / path).resolve() if path else source
        assert linked.exists(), target
        if anchor:
            assert anchor in heading_anchors(linked.read_text(encoding="utf-8")), target
