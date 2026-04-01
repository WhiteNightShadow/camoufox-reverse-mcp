from __future__ import annotations

import os

from ..server import mcp, browser_manager
from ..utils.js_helpers import render_trace_template


@mcp.tool()
async def trace_function(
    function_path: str,
    log_args: bool = True,
    log_return: bool = True,
    log_stack: bool = False,
    max_captures: int = 50,
) -> dict:
    """Trace all calls to a function without pausing execution.

    Records arguments, return values, and optionally call stacks for each invocation.
    Data is stored in window.__mcp_traces[function_path] and can be retrieved
    with get_trace_data.

    Args:
        function_path: Full path to the function, e.g. "XMLHttpRequest.prototype.open",
            "window.encrypt", "JSON.stringify".
        log_args: Record function arguments (default True).
        log_return: Record return values (default True).
        log_stack: Record call stacks (default False, enable for call chain analysis).
        max_captures: Maximum number of calls to record (default 50).

    Returns:
        dict with status and the target function path.
    """
    try:
        page = await browser_manager.get_active_page()
        trace_js = render_trace_template(
            function_path=function_path,
            max_captures=max_captures,
            log_args=log_args,
            log_return=log_return,
            log_stack=log_stack,
        )
        await page.evaluate(trace_js)
        return {"status": "tracing", "target": function_path}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_trace_data(function_path: str | None = None, clear: bool = False) -> dict:
    """Retrieve trace data collected by trace_function.

    Args:
        function_path: If specified, return traces only for this function.
            If omitted, return all traces.
        clear: If True, clear the trace data after retrieval.

    Returns:
        dict mapping function paths to lists of call records
        (args, return_value, stack, timestamp).
    """
    try:
        page = await browser_manager.get_active_page()
        if function_path:
            data = await page.evaluate(f"""() => {{
                const traces = window.__mcp_traces || {{}};
                return {{ [{repr(function_path)}]: traces[{repr(function_path)}] || [] }};
            }}""")
        else:
            data = await page.evaluate("window.__mcp_traces || {}")
        if clear:
            if function_path:
                await page.evaluate(f"if(window.__mcp_traces) delete window.__mcp_traces[{repr(function_path)}]")
            else:
                await page.evaluate("window.__mcp_traces = {}")
        return data
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def hook_function(
    function_path: str,
    hook_code: str,
    position: str = "before",
) -> dict:
    """Inject custom hook code on a target function.

    Args:
        function_path: Full path to the function, e.g. "window.encrypt".
        hook_code: JavaScript code to execute. Available context variables:
            - arguments: original function arguments
            - __this: the 'this' context
            - __result: original function's return value (only in "after" mode)
        position: When to run hook_code relative to the original function:
            - "before": Run hook_code before the original function.
            - "after": Run hook_code after the original function (can access __result).
            - "replace": Completely replace the original function with hook_code.

    Returns:
        dict with status, target, and position.
    """
    try:
        page = await browser_manager.get_active_page()
        escaped_hook = hook_code.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

        if position == "before":
            js = f"""(() => {{
    const path = {repr(function_path)};
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }}
    const fn = parts[parts.length - 1];
    const _orig = parent[fn];
    if (typeof _orig !== 'function') return;
    parent[fn] = function(...args) {{
        const __this = this;
        (function() {{ {escaped_hook} }}).call(__this);
        return _orig.apply(this, args);
    }};
    console.log('[HOOK:before]', path);
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
    parent[fn] = function(...args) {{
        const __this = this;
        const __result = _orig.apply(this, args);
        (function() {{ {escaped_hook} }}).call(__this);
        return __result;
    }};
    console.log('[HOOK:after]', path);
}})();"""
        elif position == "replace":
            js = f"""(() => {{
    const path = {repr(function_path)};
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {{ parent = parent[parts[i]]; if(!parent) return; }}
    const fn = parts[parts.length - 1];
    parent[fn] = function(...args) {{
        const __this = this;
        {escaped_hook}
    }};
    console.log('[HOOK:replace]', path);
}})();"""
        else:
            return {"error": f"Invalid position: {position}. Use 'before', 'after', or 'replace'."}

        await page.evaluate(js)
        return {"status": "hooked", "target": function_path, "position": position}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def inject_hook_preset(preset: str) -> dict:
    """Inject a pre-built hook template for common reverse engineering tasks.

    Available presets:
        - "xhr": Hook XMLHttpRequest to log all XHR requests (URL, method, headers, body, stack).
        - "fetch": Hook window.fetch to log all fetch requests.
        - "crypto": Hook btoa/atob/JSON.stringify to capture encryption I/O.
        - "websocket": Hook WebSocket to log all WS messages.
        - "debugger_bypass": Bypass anti-debugging traps (infinite debugger loops,
          Function constructor checks, setInterval checks).

    Args:
        preset: One of "xhr", "fetch", "crypto", "websocket", "debugger_bypass".

    Returns:
        dict with status and the preset name.
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

        page = await browser_manager.get_active_page()
        await page.add_init_script(script=hook_js)
        browser_manager._init_scripts.append(f"preset:{preset}")
        return {"status": "injected", "preset": preset}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def remove_hooks() -> dict:
    """Remove all injected hooks by reloading the page.

    WARNING: This reloads the page and loses current page state (form data,
    scroll position, etc.). Consider using a new context if you need to
    preserve state.

    Returns:
        dict with status after reload.
    """
    try:
        page = await browser_manager.get_active_page()
        browser_manager._init_scripts.clear()

        context = page.context
        url = page.url
        await page.close()

        new_page = await context.new_page()
        browser_manager._attach_listeners(new_page)
        browser_manager.pages[browser_manager.active_page_name] = new_page

        if url and url != "about:blank":
            await new_page.goto(url)

        return {"status": "hooks_removed", "url": url}
    except Exception as e:
        return {"error": str(e)}
