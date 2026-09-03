"""Environment self-check tool (v1.0.0: session fields removed)."""
from __future__ import annotations

import importlib
from typing import Any

from ..server import browser_manager, mcp


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

    # Camoufox Python/browser runtime discovery is strictly read-only. In
    # particular, this does not call get_active_path(), which may auto-select
    # and persist a browser in Camoufox 0.5.
    try:
        from ..camoufox_runtime import inspect_camoufox_runtime

        camoufox_runtime = inspect_camoufox_runtime()
        if camoufox_runtime.get("legacy_cache_migration_risk"):
            recommendations.append(
                "Camoufox 0.5 detected a legacy flat cache without its compatibility flag. "
                "Back up or migrate it before running `camoufox fetch`; upstream may remove old data."
            )
        if camoufox_runtime.get("error"):
            recommendations.append(
                f"Camoufox runtime discovery failed: {camoufox_runtime['error']}"
            )
    except Exception as e:
        camoufox_runtime = {
            "python_version": "unknown",
            "multiversion_supported": False,
            "active": None,
            "installed": [],
            "error": str(e),
        }

    overall_ok = (
        version_ok
        and all(d["ok"] for d in deps.values())
        and not camoufox_runtime.get("error")
        and camoufox_runtime.get("active") is not None
    )

    # camoufox-reverse capability and active-session detection. Installation is
    # derived from browser markers; control files only prove a live trace when
    # they belong to this BrowserManager launch and their PIDs still exist.
    from ..property_trace import (
        CACHE_DIR,
        CONTROL_DIR,
        RUNS_DIR,
        list_control_files,
        pid_is_alive,
    )
    custom_browser: dict[str, Any] = {"installed": False, "trace_active": False}
    try:
        reverse_installs = [
            item for item in camoufox_runtime.get("installed", [])
            if item.get("property_trace")
        ]
        selected = getattr(browser_manager, "_runtime_browser", None)
        selected = selected or camoufox_runtime.get("active")
        trace_base = getattr(browser_manager, "_trace_base_dir", None)
        live_controls = list_control_files(trace_base, live_only=True) if trace_base else []
        all_controls = list(CONTROL_DIR.glob("control-*.cmd")) if CONTROL_DIR.exists() else []
        if RUNS_DIR.exists():
            for run in RUNS_DIR.iterdir():
                if run.is_dir():
                    directory = run / "control"
                    if directory.exists():
                        all_controls.extend(directory.glob("control-*.cmd"))
        stale_controls = []
        for path in all_controls:
            try:
                pid = int(path.stem.removeprefix("control-"))
            except ValueError:
                stale_controls.append(path)
                continue
            if not pid_is_alive(pid):
                stale_controls.append(path)
        protocol = selected.get("property_trace_protocol") if selected else None
        live_legacy_handshake = bool(
            browser_manager.browser is not None
            and trace_base
            and live_controls
            and selected
            and not selected.get("capabilities_marker")
            and not selected.get("repo")
        )
        trace_capable = bool(
            selected
            and selected.get("property_trace")
            and (protocol is None or selected.get("property_trace_compatible"))
        ) or live_legacy_handshake
        custom_browser = {
            "installed": bool(reverse_installs or live_legacy_handshake),
            "trace_capable": trace_capable,
            "trace_active": bool(
                browser_manager.browser is not None and trace_base and live_controls
            ),
            "active_selector": selected.get("selector") if selected else None,
            "available_selectors": [item.get("selector") for item in reverse_installs],
            "protocol": protocol,
            "hook_count": selected.get("property_trace_hooks") if selected else None,
            "features": selected.get("property_trace_features", []) if selected else [],
            "run_dir": str(trace_base) if trace_base else None,
            "live_control_files": len(live_controls),
            "stale_control_files": len(stale_controls),
            "cache_dir": str(CACHE_DIR),
        }
        if not reverse_installs:
            custom_browser["install_hint"] = (
                "Install a verified camoufox-reverse release side-by-side, then "
                "select it with browser_version and enable_trace=True."
            )
    except Exception:
        pass

    return {
        "mcp": {"version": version, "version_ok": version_ok},
        "deps": deps,
        "browser": browser_state,
        "camoufox": camoufox_runtime,
        "camoufox_reverse": custom_browser,
        "overall_ok": overall_ok,
        "recommendations": recommendations,
    }
