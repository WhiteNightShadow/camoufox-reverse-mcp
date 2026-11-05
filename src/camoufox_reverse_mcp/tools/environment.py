"""Environment self-check tool (v1.0.0: session fields removed)."""
from __future__ import annotations

import importlib
import hashlib
import json
import shutil
from pathlib import Path
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

    # Read-only live state. Captured data and installed hooks may be the user's
    # current evidence, not disposable residue. Never recommend a blanket reset.
    browser_state: dict[str, Any] = {"running": False, "has_residuals": False}
    try:
        browser = browser_manager.browser
        if browser is not None:
            connected = getattr(browser, "is_connected", None)
            browser_state["running"] = bool(connected()) if callable(connected) else True
            browser_state["instance_id"] = getattr(browser_manager, "_browser_instance_id", None)
            browser_state["ownership"] = "attached" if browser_manager._connected else "owned"
            pages = [{"name": name, "url": page.url} for name, page in browser_manager.pages.items()]
            browser_state["pages"] = pages
            browser_state["page_count"] = len(pages)
            browser_state["persistent_scripts_count"] = len(browser_manager._persistent_scripts)
            browser_state["active_captures"] = browser_manager._capturing
            browser_state["captured_requests_count"] = len(browser_manager._network_requests)
            browser_state["has_residuals"] = bool(browser_manager._persistent_scripts or browser_manager._network_requests)
            if browser_state["has_residuals"]:
                recommendations.append("Existing hooks/captures may belong to the current task. Reuse relevant evidence; inspect scope before clearing or replacing anything.")
            if not browser_state["running"]:
                recommendations.append("Browser transport is disconnected. Re-launch explicitly and collect fresh page evidence.")
        browser_state["review_policy"] = "Check at task start and when relevant state changes; do not reset or recheck the full environment before every tool call."
    except Exception as exc:
        browser_state["error"] = str(exc)

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

    if camoufox_runtime.get("active") is None and not camoufox_runtime.get("error"):
        recommendations.append(
            "No Camoufox browser is installed. Run `python -m camoufox fetch` in this "
            "environment, or `uvx --from <same source as this MCP> camoufox fetch` for "
            "uvx installs. Otherwise the first launch_browser downloads it during the call."
        )

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

    fingerprint = hashlib.sha256(json.dumps({
        "mcp_version": version,
        "browser_instance": browser_state.get("instance_id"),
        "running": browser_state.get("running"),
        "pages": browser_state.get("pages", []),
        "persistent_scripts": [s.get("name") for s in browser_manager._persistent_scripts],
        "runtime": (browser_manager._runtime_browser or camoufox_runtime.get("active") or {}).get("selector"),
    }, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "review": {"state_fingerprint": fingerprint,
                   "scope": "task", "automatic_reset": False,
                   "fingerprint_is_complete_state": False,
                   "invalidate_on": ["browser restart/disconnect", "target or frame changed", "SDK or signer changed", "auth state changed", "hook/trace mode changed", "relevant operation failed"],
                   "note": "Fingerprint is a scope hint, not proof of unchanged page/auth/SDK state. Refresh only the checks affected by an observed change."},
        "javascript_parser": {"default": "esprima", "modern_backend": "acorn-8.15.0",
                              "modern_available": shutil.which("node") is not None and (Path(__file__).parents[1] / "vendor" / "acorn.cjs").is_file(),
                              "network_required": False, "executes_source": False},
        "task_readiness": {"browser_analysis": overall_ok,
                           "captured_evidence_review": True,
                           "node_signer": shutil.which("node") is not None},
        "mcp": {"version": version, "version_ok": version_ok},
        "deps": deps,
        "browser": browser_state,
        "camoufox": camoufox_runtime,
        "camoufox_reverse": custom_browser,
        "overall_ok": overall_ok,
        "recommendations": recommendations,
    }
