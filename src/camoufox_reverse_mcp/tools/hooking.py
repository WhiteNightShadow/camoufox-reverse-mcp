from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager
from ..utils.js_helpers import render_trace_template, render_persistent_trace_template


@mcp.tool()
async def trace_function(
    function_path: str,
    log_args: bool = True,
    log_return: bool = True,
    log_stack: bool = False,
    max_captures: int = 50,
    persistent: bool = False,
) -> dict:
    """Trace all calls to a function without pausing execution.
    Retrieve data with get_trace_data.

    Args:
        function_path: Full path e.g. "XMLHttpRequest.prototype.open".
        log_args: Record arguments (default True).
        log_return: Record return values (default True).
        log_stack: Record call stacks (default False).
        max_captures: Max calls to record (default 50).
        persistent: If True, survives navigation. Data collected Python-side.
    """
    try:
        if persistent:
            trace_js = render_persistent_trace_template(
                function_path=function_path,
                max_captures=max_captures,
                log_args=log_args,
                log_return=log_return,
                log_stack=log_stack,
            )
            trace_name = f"trace:{function_path}"
            await browser_manager.add_persistent_script(trace_name, trace_js)
            page = await browser_manager.get_active_page()
            await page.evaluate(trace_js)
            return {"status": "tracing", "target": function_path, "persistent": True}
        else:
            page = await browser_manager.get_active_page()
            trace_js = render_trace_template(
                function_path=function_path,
                max_captures=max_captures,
                log_args=log_args,
                log_return=log_return,
                log_stack=log_stack,
            )
            await page.evaluate(trace_js)
            return {"status": "tracing", "target": function_path, "persistent": False}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_trace_data(
    function_path: str | None = None,
    clear: bool = False,
    include_persistent: bool = True,
) -> dict:
    """Retrieve trace data collected by trace_function.

    Args:
        function_path: If specified, return traces only for this function.
            If omitted, return all traces.
        clear: If True, clear the trace data after retrieval.
        include_persistent: Include trace data collected across navigations
            (stored Python-side). Default True.

    Returns:
        dict mapping function paths to lists of call records
        (args, return_value, stack, timestamp).
    """
    try:
        page = await browser_manager.get_active_page()
        if function_path:
            page_data = await page.evaluate(f"""() => {{
                const traces = window.__mcp_traces || {{}};
                return {{ [{repr(function_path)}]: traces[{repr(function_path)}] || [] }};
            }}""")
        else:
            page_data = await page.evaluate("window.__mcp_traces || {}")

        if include_persistent and browser_manager._persistent_traces:
            merged = dict(page_data) if page_data else {}
            for path, entries in browser_manager._persistent_traces.items():
                if function_path and path != function_path:
                    continue
                existing = merged.get(path, [])
                seen_ts = {e.get("timestamp") for e in existing}
                for entry in entries:
                    if entry.get("timestamp") not in seen_ts:
                        existing.append(entry)
                merged[path] = existing
            page_data = merged

        if clear:
            if function_path:
                await page.evaluate(f"if(window.__mcp_traces) delete window.__mcp_traces[{repr(function_path)}]")
                browser_manager._persistent_traces.pop(function_path, None)
            else:
                await page.evaluate("window.__mcp_traces = {}")
                browser_manager._persistent_traces.clear()
        return page_data
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def hook_function(
    function_path: str,
    hook_code: str,
    position: str = "before",
    non_overridable: bool = False,
) -> dict:
    """Inject custom hook code on a target function.

    Args:
        function_path: Full path e.g. "window.encrypt".
        hook_code: JS code to execute. Context vars: arguments, __this, __result (after mode).
        position: "before", "after", or "replace".
        non_overridable: Use Object.defineProperty to prevent override.
    """
    try:
        page = await browser_manager.get_active_page()
        escaped_hook = hook_code.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

        freeze_code = ""
        if non_overridable:
            freeze_code = """
    try {
        Object.defineProperty(parent, fn, {
            value: parent[fn], writable: false, configurable: false
        });
    } catch(e) {}"""

        if position == "before":
            js = f"""(() => {{
    const path = {repr(function_path)};
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }}
    const fn = parts[parts.length - 1];
    const _orig = parent[fn];
    if (typeof _orig !== 'function') return;
    const wrapper = function(...args) {{
        const __this = this;
        (function() {{ {escaped_hook} }}).call(__this);
        return _orig.apply(this, args);
    }};
    wrapper.toString = function() {{ return _orig.toString(); }};
    parent[fn] = wrapper;{freeze_code}
}})();"""
        elif position == "after":
            js = f"""(() => {{
    const path = {repr(function_path)};
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }}
    const fn = parts[parts.length - 1];
    const _orig = parent[fn];
    if (typeof _orig !== 'function') return;
    const wrapper = function(...args) {{
        const __this = this;
        const __result = _orig.apply(this, args);
        (function() {{ {escaped_hook} }}).call(__this);
        return __result;
    }};
    wrapper.toString = function() {{ return _orig.toString(); }};
    parent[fn] = wrapper;{freeze_code}
}})();"""
        elif position == "replace":
            js = f"""(() => {{
    const path = {repr(function_path)};
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }}
    const fn = parts[parts.length - 1];
    const wrapper = function(...args) {{
        const __this = this;
        {escaped_hook}
    }};
    parent[fn] = wrapper;{freeze_code}
}})();"""
        else:
            return {"error": f"Invalid position: {position}. Use 'before', 'after', or 'replace'."}

        await page.evaluate(js)
        return {"status": "hooked", "target": function_path, "position": position,
                "non_overridable": non_overridable}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def inject_hook_preset(preset: str, persistent: bool = True) -> dict:
    """Inject a pre-built hook template for common reverse engineering tasks.

    Available presets: "xhr", "fetch", "crypto", "websocket", "debugger_bypass".
    Hooks are protected with Object.defineProperty and persistent by default.

    Args:
        preset: One of "xhr", "fetch", "crypto", "websocket", "debugger_bypass".
        persistent: If True (default), survives page navigation.
    """
    preset_map = {
        "xhr": "xhr_hook.js",
        "fetch": "fetch_hook.js",
        "crypto": "crypto_hook.js",
        "websocket": "websocket_hook.js",
        "debugger_bypass": "debugger_trap.js",
    }

    if preset not in preset_map:
        return {"error": f"Unknown preset: {preset}. Available: {list(preset_map.keys())}"}

    try:
        hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
        hook_file = os.path.join(hooks_dir, preset_map[preset])

        with open(hook_file, "r", encoding="utf-8") as f:
            hook_js = f.read()

        if persistent:
            script_name = f"preset:{preset}"
            await browser_manager.add_persistent_script(script_name, hook_js)
            page = await browser_manager.get_active_page()
            await page.evaluate(hook_js)
        else:
            page = await browser_manager.get_active_page()
            await page.add_init_script(script=hook_js)

        browser_manager._init_scripts.append(f"preset:{preset}")
        return {"status": "injected", "preset": preset, "persistent": persistent}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def trace_property_access(
    targets: list[str],
    persistent: bool = False,
    max_entries: int = 2000,
) -> dict:
    """Track property access on specified objects to reveal environment info being read.
    Use ".*" suffix for all properties (e.g. "navigator.*", "screen.*").

    Args:
        targets: List of property paths to monitor.
        persistent: If True, tracking survives page navigation.
        max_entries: Maximum access records (default 2000).
    """
    try:
        hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
        with open(os.path.join(hooks_dir, "property_access_hook.js"), "r", encoding="utf-8") as f:
            template = f.read()

        hook_js = template.replace("{{TARGETS}}", json.dumps(targets))

        if persistent:
            await browser_manager.add_persistent_script("trace_property_access", hook_js)
            page = await browser_manager.get_active_page()
            await page.evaluate(hook_js)
        else:
            page = await browser_manager.get_active_page()
            await page.evaluate(hook_js)

        return {"status": "tracking", "targets": targets, "persistent": persistent}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_property_access_log(
    property_filter: str | None = None,
    clear: bool = False,
) -> dict:
    """Retrieve property access records collected by trace_property_access.

    Args:
        property_filter: Optional substring filter for property names.
        clear: If True, clear the log after retrieval.

    Returns:
        dict with access records and count.
    """
    try:
        page = await browser_manager.get_active_page()
        data = await page.evaluate("window.__mcp_prop_access_log || []")
        if property_filter:
            data = [d for d in data if property_filter in d.get("property", "")]
        if clear:
            await page.evaluate("window.__mcp_prop_access_log = []")
        return {"entries": data, "count": len(data)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def remove_hooks(keep_persistent: bool = False) -> dict:
    """Remove all injected hooks by reloading the page.

    Args:
        keep_persistent: If True, persistent (context-level) hooks will be
            preserved and re-applied after reload. If False, all hooks
            including persistent ones are removed.

    Returns:
        dict with status after reload.
    """
    try:
        page = await browser_manager.get_active_page()
        browser_manager._init_scripts.clear()

        if not keep_persistent:
            browser_manager._persistent_scripts.clear()

        context = page.context
        url = page.url
        await page.close()

        new_page = await context.new_page()
        browser_manager._attach_listeners(new_page)
        browser_manager.pages[browser_manager.active_page_name] = new_page

        if url and url != "about:blank":
            await new_page.goto(url)

        return {"status": "hooks_removed", "url": url,
                "persistent_kept": keep_persistent,
                "persistent_count": len(browser_manager._persistent_scripts)}
    except Exception as e:
        return {"error": str(e)}
