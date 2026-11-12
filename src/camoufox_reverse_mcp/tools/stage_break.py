from __future__ import annotations

import json

from ..server import browser_manager, mcp
from ..utils.frames import resolve_frame
from ..utils.worlds import evaluate_in_world

CAPTURES = ("args_only", "args_ret", "args_ret_stack")

# States armed for this MCP session. instrumentation writes them into every
# script it serves, so a break also fires for states that run during page load.
_ARMED: dict[int, dict] = {}


def armed_breaks_literal() -> str | None:
    """JS object literal of the armed states, or None when nothing is armed."""
    if not _ARMED:
        return None
    return json.dumps({str(p): {**spec, "hits": 0} for p, spec in sorted(_ARMED.items())},
                      separators=(",", ":"))


def _target(page, frame_url, frame_name, frame_index):
    if any(v is not None for v in (frame_url, frame_name, frame_index)):
        return resolve_frame(page, frame_url=frame_url, frame_name=frame_name, frame_index=frame_index)
    return page, None


@mcp.tool()
async def stage_break(
    action: str = "list",
    p: int | None = None,
    capture: str = "args_ret",
    limit: int = 0,
    depth: int = 2,
    max_items: int = 50,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
) -> dict:
    """Per-state deep capture for `instrumentation(tap_stage_calls=True)`.

    A stage tap only previews the first arguments. When one state has to be
    inspected properly, arm a break for that state id: every hit then records a
    bounded deep snapshot of the arguments (and optionally the return value and
    a filtered call stack) as a `tap_stage_break` event.

    Armed states stay armed for this MCP session until "clear": the live page is
    updated at once, and every instrumented script served afterwards carries the
    armed set, so states that run while a page loads are captured after a reload.
    The `limit` counts hits per page load. Snapshots read own data properties
    only and report getters as {"__t": "accessor"} without calling them; a Proxy
    argument still sees its ownKeys/getOwnPropertyDescriptor traps.

    Args:
        action: "set" arm one state, "clear" disarm (all when `p` is omitted),
            "list" show armed states and their hit counters.
        p: Dispatcher state id (the integer from `tap_stage` events).
        capture: "args_only" | "args_ret" (default) | "args_ret_stack".
        limit: Maximum recorded hits, 0 means unlimited (default 0).
        depth: Snapshot depth for nested objects/arrays (1-5, default 2).
        max_items: Maximum keys/elements kept per object/array (1-500, default 50).

    Returns:
        dict with the states armed for later loads (`session_armed`), the live
        page's armed set and hit counters, and whether the instrumentation
        runtime is present on the page.
    """
    if action not in ("set", "clear", "list"):
        return {"error": "action must be one of: set, clear, list"}
    if action == "set":
        if p is None:
            return {"error": "p is required for action='set'"}
        if not 0 <= p <= 10**7:
            return {"error": "p must be between 0 and 10000000"}
        if capture not in CAPTURES:
            return {"error": f"capture must be one of: {', '.join(CAPTURES)}"}
        if not 0 <= limit <= 1000:
            return {"error": "limit must be between 0 and 1000"}
        if not 1 <= depth <= 5:
            return {"error": "depth must be between 1 and 5"}
        if not 1 <= max_items <= 500:
            return {"error": "max_items must be between 1 and 500"}

    if action == "set":
        _ARMED[p] = {"capture": capture, "limit": limit, "depth": depth, "maxItems": max_items}
    elif action == "clear":
        if p is None:
            _ARMED.clear()
        else:
            _ARMED.pop(p, None)
    session_armed = sorted(_ARMED)

    try:
        page = await browser_manager.get_active_page()
        target, frame = _target(page, frame_url, frame_name, frame_index)
    except Exception as e:
        return {"action": action, "session_armed": session_armed,
                "error": f"live page not updated: {e}"}

    if action == "set":
        spec = json.dumps({"p": p, "capture": capture, "limit": limit,
                           "depth": depth, "maxItems": max_items})
        script = f"""() => {{
            var spec = {spec};
            var cfg = window.__mcp_tap_cfg;
            if (!cfg) {{
                // Same non-enumerable global the instrumentation runtime creates.
                cfg = {{ sampling: 1, tagFilter: null }};
                Object.defineProperty(window, '__mcp_tap_cfg',
                    {{ value: cfg, writable: true, configurable: true, enumerable: false }});
            }}
            cfg.breaks = cfg.breaks || {{}};
            cfg.breaks[spec.p] = {{ capture: spec.capture, limit: spec.limit,
                                    depth: spec.depth, maxItems: spec.maxItems, hits: 0 }};
            return {{ armed: Object.keys(cfg.breaks).map(Number),
                      runtime_installed: typeof window.__mcp_tap_stage === 'function' }};
        }}"""
    elif action == "clear":
        script = f"""() => {{
            var cfg = window.__mcp_tap_cfg;
            var p = {json.dumps(p)};
            if (cfg && cfg.breaks) {{
                if (p === null) cfg.breaks = {{}}; else delete cfg.breaks[p];
            }}
            return {{ armed: cfg && cfg.breaks ? Object.keys(cfg.breaks).map(Number) : [],
                      runtime_installed: typeof window.__mcp_tap_stage === 'function' }};
        }}"""
    else:
        script = """() => {
            var cfg = window.__mcp_tap_cfg;
            return { armed: cfg && cfg.breaks ? Object.keys(cfg.breaks).map(Number) : [],
                     breaks: (cfg && cfg.breaks) || {},
                     runtime_installed: typeof window.__mcp_tap_stage === 'function' };
        }"""

    try:
        data, backend, warning = await evaluate_in_world(target, script, "main")
    except Exception as e:
        return {"action": action, "session_armed": session_armed,
                "error": f"live page not updated: {e}"}

    result = {
        "action": action,
        "session_armed": session_armed,
        "world": "main",
        "frame": frame,
        "execution_backend": backend,
        "warning": warning,
        **(data if isinstance(data, dict) else {"result": data}),
    }
    if not result.get("runtime_installed"):
        result["note"] = (
            "instrumentation runtime is not present on this page; install "
            "instrumentation(action='install', tap_stage_calls=True) and reload/navigate. "
            "Armed states are applied to the scripts served from then on."
        )
    return result
