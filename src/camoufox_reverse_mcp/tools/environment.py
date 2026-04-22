"""Environment self-check tool (v1.0.0: session fields removed)."""
from __future__ import annotations

import importlib
from typing import Any

from ..server import mcp, browser_manager


@mcp.tool()
async def check_environment() -> dict:
    """One-stop self-check of MCP environment, dependencies, and browser state.

    v1.0.0: session-related checks removed (session mechanism removed).
    Checks MCP version, critical dependencies (esprima, playwright),
    browser state (residuals, captures).

    Returns:
        dict with sections: mcp, deps, browser, overall_ok, recommendations.
    """
    recommendations: list[str] = []

    # MCP version
    try:
        mod = importlib.import_module("camoufox_reverse_mcp")
        version = getattr(mod, "__version__", "unknown")
        parts = tuple(int(x) for x in version.split(".") if x.isdigit())
        version_ok = parts >= (1, 0, 0)
    except Exception:
        version = "unknown"
        version_ok = False
    if not version_ok:
        recommendations.append(f"MCP version is {version}, need >= 1.0.0.")

    # Dependencies
    deps: dict[str, dict] = {}
    for dep in ("esprima", "playwright"):
        try:
            m = importlib.import_module(dep)
            deps[dep] = {"installed": True, "version": getattr(m, "__version__", "unknown"), "ok": True}
        except ImportError:
            deps[dep] = {"installed": False, "version": None, "ok": False}

    # Browser state
    browser_state: dict[str, Any] = {"running": False}
    try:
        if browser_manager.browser is not None:
            browser_state["running"] = True
            ctx = browser_manager.contexts.get("default")
            pages = ctx.pages if ctx else []
            browser_state["page_count"] = len(pages)
            browser_state["persistent_scripts_count"] = len(browser_manager._persistent_scripts)
            browser_state["active_captures"] = browser_manager._capturing
            browser_state["captured_requests_count"] = len(browser_manager._network_requests)
            has_residuals = (
                browser_state["persistent_scripts_count"] > 0
                or browser_state["captured_requests_count"] > 0
            )
            browser_state["has_residuals"] = has_residuals
            if has_residuals:
                recommendations.append("Browser has residual state. Consider reset_browser_state().")
    except Exception as e:
        browser_state["error"] = str(e)

    overall_ok = version_ok and all(d["ok"] for d in deps.values() if d.get("installed"))

    # camoufox-reverse custom browser detection
    from ..property_trace import CACHE_DIR, CONTROL_DIR, TRACES_DIR
    custom_browser: dict[str, Any] = {"installed": False}
    try:
        # Check if trace control files exist (= custom browser running with trace)
        ctrl_files = list(CONTROL_DIR.glob("control-*.cmd")) if CONTROL_DIR.exists() else []
        trace_files = list(TRACES_DIR.glob("*.jsonl")) if TRACES_DIR.exists() else []
        if ctrl_files:
            custom_browser = {
                "installed": True,
                "trace_active": True,
                "control_files": len(ctrl_files),
                "trace_files": len(trace_files),
                "cache_dir": str(CACHE_DIR),
            }
        else:
            custom_browser = {
                "installed": False,
                "install_hint": (
                    "Download camoufox-reverse from "
                    "https://github.com/WhiteNightShadow/camoufox-reverse/releases "
                    "and launch with enable_trace=True"
                ),
            }
    except Exception:
        pass

    return {
        "mcp": {"version": version, "version_ok": version_ok},
        "deps": deps,
        "browser": browser_state,
        "camoufox_reverse": custom_browser,
        "overall_ok": overall_ok,
        "recommendations": recommendations,
    }
