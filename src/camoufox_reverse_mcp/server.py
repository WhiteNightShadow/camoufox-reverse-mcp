from mcp.server.fastmcp import FastMCP
from .browser import BrowserManager

mcp = FastMCP(
    "camoufox-reverse-mcp",
    instructions="Anti-detection browser MCP server for JavaScript reverse engineering. "
    "Uses Camoufox (C++ engine-level fingerprint spoofing) to bypass bot detection "
    "while performing JS analysis, debugging, hooking, and network interception."
)

browser_manager = BrowserManager()

from .tools import navigation, script_analysis, debugging, hooking, network, storage, fingerprint  # noqa: E402, F401
