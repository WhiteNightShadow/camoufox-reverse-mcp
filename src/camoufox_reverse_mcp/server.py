from mcp.server.fastmcp import FastMCP
from .browser import BrowserManager

mcp = FastMCP(
    "camoufox-reverse-mcp",
    instructions="Anti-detection browser MCP server for JavaScript reverse engineering. "
    "Uses Camoufox (C++ engine-level fingerprint spoofing) to bypass bot detection "
    "while performing JS analysis, debugging, hooking, network interception, "
    "and JSVMP bytecode analysis."
)

browser_manager = BrowserManager()

# v1.0.0: pure JS reverse-engineering toolkit (session/assertions removed)
from .tools import navigation      # noqa: E402, F401  — browser control + page interaction
from .tools import script_analysis  # noqa: E402, F401  — scripts() + search_code()
from .tools import debugging        # noqa: E402, F401  — evaluate_js
from .tools import hooking          # noqa: E402, F401  — hook_function + inject_hook_preset + remove_hooks
from .tools import network          # noqa: E402, F401  — network_capture + list/get requests
from .tools import storage          # noqa: E402, F401  — cookies() + get_storage + export/import state
from .tools import jsvmp            # noqa: E402, F401  — hook_jsvmp_interpreter + compare_env
from .tools import instrumentation  # noqa: E402, F401  — instrumentation(action=...)
from .tools import environment      # noqa: E402, F401  — check_environment
from .tools import verification     # noqa: E402, F401  — verify_signer_offline
from .tools import trace            # noqa: E402, F401  — trace_property_access + list/query
