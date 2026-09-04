from __future__ import annotations

import hashlib
import json
import os
from collections import deque

from ..server import mcp, browser_manager
from ..utils.frames import list_frame_metadata, persistent_frame_guard, resolve_frame
from ..utils.worlds import evaluate_in_world, wrapped_main_world_init_script


_PERSISTENT_WAIT_TIMEOUT_MS = 5000


def _frame_runtime_helpers(selector: dict | None) -> str:
    """Return JS helpers shared by trace and intercept installers."""
    return rf"""
    const __mcpSelector = {json.dumps(selector)};
    const __mcpFrameMeta = () => {{
        const index = window === window.top ? 0 : null;
        return {{
            url: String(location.href),
            name: String(window.name || ''),
            index,
            is_main: window === window.top
        }};
    }};
    const __mcpGlobMatch = (value, pattern) => {{
        if (pattern === null || pattern === undefined) return true;
        if (!pattern.includes('*') && !pattern.includes('?'))
            return value === pattern;
        const special = '\\.^$+{{}}()|[]';
        let escaped = '';
        for (const char of pattern) {{
            if (char === '*') escaped += '.*';
            else if (char === '?') escaped += '.';
            else escaped += special.includes(char) ? '\\' + char : char;
        }}
        return new RegExp('^' + escaped + '$').test(value);
    }};
    const __mcpFrameMatches = () => {{
        if (!__mcpSelector) return true;
        const meta = __mcpFrameMeta();
        return __mcpGlobMatch(meta.url, __mcpSelector.url)
            && __mcpGlobMatch(meta.name, __mcpSelector.name)
            && (__mcpSelector.index === null || __mcpSelector.index === undefined
                || meta.index === __mcpSelector.index);
    }};
    """


def _build_installer_core(
    *,
    function_path: str,
    mode: str,
    hook_code: str,
    position: str,
    non_overridable: bool,
    log_args: bool,
    log_return: bool,
    log_stack: bool,
    max_captures: int,
    wait_timeout_ms: int,
    poll_interval_ms: int,
    frame_selector: dict | None,
    frame_metadata: dict | None,
    world: str,
    install_id: str,
    watch_assignments: bool,
) -> str:
    """Build an async installer that returns an explicit success/failure record."""
    if mode == "intercept" and position not in {"before", "after", "replace"}:
        raise ValueError("position must be 'before', 'after', or 'replace'")

    path_json = json.dumps(function_path)
    install_json = json.dumps(install_id)
    helpers = _frame_runtime_helpers(frame_selector)
    if frame_metadata is not None:
        frame_expression = (
            "({...__mcpFrameMeta(), index: "
            + json.dumps(frame_metadata.get("index"))
            + ", parent_index: "
            + json.dumps(frame_metadata.get("parent_index"))
            + "})"
        )
    else:
        frame_expression = "__mcpFrameMeta()"
    freeze_code = ""
    if non_overridable:
        freeze_code = """
        try {
            Object.defineProperty(parent, funcName, {
                value: wrapper, writable: false, configurable: false
            });
        } catch(e) {
            parent[funcName] = wrapper;
        }
        """
    else:
        freeze_code = "parent[funcName] = wrapper;"

    if mode == "trace":
        wrapper_code = f"""
        window.__mcp_traces = window.__mcp_traces || {{}};
        window.__mcp_traces[path] = window.__mcp_traces[path] || [];
        let captureCount = 0;
        const maxCaptures = {max_captures};
        const __mcpSafe = (value, limit) => {{
            try {{
                const encoded = JSON.stringify(value);
                return encoded === undefined ? String(value).substring(0, limit)
                    : encoded.substring(0, limit);
            }} catch(e) {{
                try {{ return String(value).substring(0, limit); }}
                catch(_) {{ return '[unserializable]'; }}
            }}
        }};
        const wrapper = function(...args) {{
            if (captureCount >= maxCaptures) return original.apply(this, args);
            captureCount++;
            const entry = {{
                traceId: Date.now() + ':' + captureCount + ':' + Math.random().toString(36).slice(2),
                callIndex: captureCount,
                timestamp: Date.now(),
                world: {json.dumps(world)},
                frame: {frame_expression}
            }};
            if ({str(log_args).lower()}) entry.args = __mcpSafe(args, 2000);
            if ({str(log_stack).lower()}) entry.stack = new Error().stack;
            const result = original.apply(this, args);
            if ({str(log_return).lower()}) entry.returnValue = __mcpSafe(result, 2000);
            window.__mcp_traces[path].push(entry);
            try {{
                console.log('__MCP_TRACE__:' + JSON.stringify({{...entry, __path__: path}}));
            }} catch(e) {{}}
            console.log('[TRACE:' + path + ']', 'call #' + captureCount);
            return result;
        }};
        try {{ Object.defineProperty(wrapper, 'name', {{ value: funcName }}); }} catch(e) {{}}
        try {{ Object.defineProperty(wrapper, 'length', {{ value: original.length }}); }} catch(e) {{}}
        wrapper.toString = function() {{ return original.toString(); }};
        try {{ Object.defineProperty(wrapper, '__mcpInstallId', {{ value: {install_json} }}); }}
        catch(e) {{}}
        {freeze_code}
        """
    elif position == "before":
        wrapper_code = f"""
        const wrapper = function(...args) {{
            const __this = this;
            (function() {{ {hook_code} }}).apply(__this, args);
            return original.apply(this, args);
        }};
        wrapper.toString = function() {{ return original.toString(); }};
        try {{ Object.defineProperty(wrapper, '__mcpInstallId', {{ value: {install_json} }}); }}
        catch(e) {{}}
        {freeze_code}
        """
    elif position == "after":
        wrapper_code = f"""
        const wrapper = function(...args) {{
            const __this = this;
            const __result = original.apply(this, args);
            (function() {{ {hook_code} }}).apply(__this, args);
            return __result;
        }};
        wrapper.toString = function() {{ return original.toString(); }};
        try {{ Object.defineProperty(wrapper, '__mcpInstallId', {{ value: {install_json} }}); }}
        catch(e) {{}}
        {freeze_code}
        """
    else:
        wrapper_code = f"""
        const wrapper = function(...args) {{
            const __this = this;
            {hook_code}
        }};
        try {{ Object.defineProperty(wrapper, '__mcpInstallId', {{ value: {install_json} }}); }}
        catch(e) {{}}
        {freeze_code}
        """

    return f"""(async () => {{
    {helpers}
    const path = {path_json};
    const parts = path.split('.');
    if (!__mcpFrameMatches()) {{
        return {{ ok: false, error: 'frame_selector_mismatch', target: path,
            frame: __mcpFrameMeta() }};
    }}
    const __mcpInstall = (parent, funcName, original) => {{
        if (original.__mcpInstallId === {install_json}) {{
            return {{ ok: true, already_installed: true, target: path,
                frame: {frame_expression} }};
        }}
        {wrapper_code}
        if (parent[funcName] !== wrapper) {{
            return {{ ok: false, error: 'target_not_replaceable', target: path,
                frame: {frame_expression} }};
        }}
        return {{ ok: true, target: path, frame: {frame_expression} }};
    }};
    const __mcpTryInstall = () => {{
        let parent = window;
        for (let i = 0; i < parts.length - 1; i++) {{
            if (!parent) return {{ ok: false }};
            const next = parent[parts[i]];
            if (next === undefined || next === null) {{
                return {{ ok: false, missingParent: parent, missingKey: parts[i] }};
            }}
            parent = next;
        }}
        if (!parent) return {{ ok: false }};
        const funcName = parts[parts.length - 1];
        const original = parent[funcName];
        if (typeof original !== 'function') {{
            return {{ ok: false, missingParent: parent, missingKey: funcName }};
        }}
        return __mcpInstall(parent, funcName, original);
    }};
    const __mcpWatched = new WeakMap();
    const __mcpWatchCleanups = [];
    const __mcpCleanupWatchers = () => {{
        while (__mcpWatchCleanups.length) {{
            try {{ __mcpWatchCleanups.pop()(); }} catch(e) {{}}
        }}
    }};
    const __mcpArmWatcher = (parent, key) => {{
        if (!{str(watch_assignments).lower()} || !parent
                || (typeof parent !== 'object' && typeof parent !== 'function')) return false;
        let keys = __mcpWatched.get(parent);
        if (!keys) {{ keys = new Set(); __mcpWatched.set(parent, keys); }}
        if (keys.has(key)) return true;
        const own = Object.getOwnPropertyDescriptor(parent, key);
        if (own && (!own.configurable || own.get || own.set || !own.writable)) return false;
        let current = own ? own.value : undefined;
        if (current !== undefined && current !== null) return false;
        const enumerable = own ? own.enumerable : true;
        const writable = own ? own.writable : true;
        try {{
            const watcherGet = () => current;
            const watcherSet = (value) => {{
                current = value;
                keys.delete(key);
                Object.defineProperty(parent, key, {{
                    value, configurable: true, enumerable, writable
                }});
                const outcome = __mcpTryInstall();
                if (!outcome.ok && outcome.missingParent)
                    __mcpArmWatcher(outcome.missingParent, outcome.missingKey);
            }};
            Object.defineProperty(parent, key, {{
                configurable: true,
                enumerable,
                get: watcherGet,
                set: watcherSet
            }});
            __mcpWatchCleanups.push(() => {{
                const active = Object.getOwnPropertyDescriptor(parent, key);
                if (!active || active.get !== watcherGet || active.set !== watcherSet) return;
                keys.delete(key);
                if (own) Object.defineProperty(parent, key, own);
                else delete parent[key];
            }});
            keys.add(key);
            return true;
        }} catch(e) {{ return false; }}
    }};
    const deadline = Date.now() + {wait_timeout_ms};
    let initial = __mcpTryInstall();
    if (initial.ok) {{ __mcpCleanupWatchers(); return initial; }}
    if (initial.error) {{ __mcpCleanupWatchers(); return initial; }}
    if (initial.missingParent) __mcpArmWatcher(initial.missingParent, initial.missingKey);
    while (true) {{
        if (Date.now() >= deadline) {{
            __mcpCleanupWatchers();
            return {{ ok: false, error: 'target_not_found', target: path,
                waited_ms: {wait_timeout_ms}, frame: __mcpFrameMeta() }};
        }}
        await new Promise(resolve => setTimeout(resolve, {poll_interval_ms}));
        const outcome = __mcpTryInstall();
        if (outcome.ok) {{ __mcpCleanupWatchers(); return outcome; }}
        if (outcome.error) {{ __mcpCleanupWatchers(); return outcome; }}
        if (outcome.missingParent) __mcpArmWatcher(outcome.missingParent, outcome.missingKey);
    }}
}})()"""


def _wrap_installer_world(core: str, world: str) -> str:
    if world == "isolated":
        return core
    if world != "main":
        raise ValueError("world must be 'isolated' or 'main'")
    return wrapped_main_world_init_script(core)


def _validated_wait(
    persistent: bool,
    wait_timeout_ms: int | None,
    poll_interval_ms: int,
) -> tuple[int, int]:
    wait_ms = _PERSISTENT_WAIT_TIMEOUT_MS if persistent and wait_timeout_ms is None else (
        0 if wait_timeout_ms is None else wait_timeout_ms
    )
    if isinstance(wait_ms, bool) or not 0 <= wait_ms <= 60_000:
        raise ValueError("wait_timeout_ms must be between 0 and 60000")
    if isinstance(poll_interval_ms, bool) or not 10 <= poll_interval_ms <= 1000:
        raise ValueError("poll_interval_ms must be between 10 and 1000")
    return wait_ms, poll_interval_ms


@mcp.tool()
async def hook_function(
    function_path: str,
    mode: str = "intercept",
    hook_code: str = "",
    position: str = "before",
    non_overridable: bool = False,
    persistent: bool = False,
    log_args: bool = True,
    log_return: bool = True,
    log_stack: bool = False,
    max_captures: int = 50,
    world: str = "isolated",
    wait_timeout_ms: int | None = None,
    poll_interval_ms: int = 50,
    watch_assignments: bool | None = None,
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
) -> dict:
    """Hook or trace a function (v0.9.0 unified).

    Replaces hook_function + trace_function.

    Args:
        function_path: Full path like "window.encrypt",
            "XMLHttpRequest.prototype.open", "JSON.stringify".
        mode:
          "intercept" — inject custom JS before/after/replace the function.
                        Requires hook_code. (was: hook_function)
          "trace"     — non-invasive trace logging args, return values,
                        and optionally call stacks. (was: trace_function)
        hook_code: JS code for "intercept" mode. Context vars:
            - arguments: original args
            - __this: the 'this' context
            - __result: return value (only in position="after")
        position: For "intercept": "before", "after", or "replace".
        non_overridable: For "intercept": use Object.defineProperty to lock.
        persistent: If True, survives page navigation.
        log_args: For "trace": record arguments (default True).
        log_return: For "trace": record return values (default True).
        log_stack: For "trace": record call stacks (default False).
        max_captures: For "trace": max calls to record (default 50).
        world: "isolated" (compatible default) or Firefox page "main" world.
        wait_timeout_ms: How long to wait for a late-bound target. Defaults to
            5000 for persistent hooks and 0 for non-persistent hooks.
        poll_interval_ms: Late-binding polling interval (10..1000ms).
        watch_assignments: Install a temporary setter on the first missing path
            segment so assignment-and-immediate-call in one JS task is captured.
            Defaults to True for persistent hooks and False otherwise.
        frame_url: Optional exact frame URL or shell-style wildcard.
        frame_name: Optional exact frame name or shell-style wildcard.
        frame_index: Optional zero-based index from get_page_info().frames.

    Returns:
        dict with status, target, mode.
    """
    try:
        if mode not in {"trace", "intercept"}:
            return {"error": f"unknown mode: {mode}. Use 'intercept' or 'trace'"}
        if world not in {"isolated", "main"}:
            return {"error": "world must be 'isolated' or 'main'"}
        if not function_path.strip():
            return {"error": "function_path must be non-empty"}
        if mode == "trace" and (isinstance(max_captures, bool) or max_captures < 1):
            return {"error": "max_captures must be at least 1"}
        if persistent and frame_index is not None:
            return {
                "error": (
                    "persistent frame_index is not supported because indexes are only "
                    "a current frame-tree snapshot; use frame_url or frame_name"
                )
            }
        wait_ms, poll_ms = _validated_wait(persistent, wait_timeout_ms, poll_interval_ms)
        watch_late = persistent if watch_assignments is None else watch_assignments
        if not isinstance(watch_late, bool):
            return {"error": "watch_assignments must be true or false"}
        page = await browser_manager.get_active_page()
        target, frame_meta = resolve_frame(
            page,
            frame_url=frame_url,
            frame_name=frame_name,
            frame_index=frame_index,
        )
        selector = persistent_frame_guard(
            frame_url=frame_url,
            frame_name=frame_name,
            frame_index=frame_index,
        )
        install_signature = json.dumps(
            {
                "function_path": function_path,
                "mode": mode,
                "hook_code": hook_code,
                "position": position,
                "non_overridable": non_overridable,
                "world": world,
                "selector": selector,
                "log_args": log_args,
                "log_return": log_return,
                "log_stack": log_stack,
                "max_captures": max_captures,
                "watch_assignments": watch_late,
            },
            sort_keys=True,
        )
        install_id = hashlib.sha256(install_signature.encode()).hexdigest()[:16]

        immediate_core = _build_installer_core(
            function_path=function_path,
            mode=mode,
            hook_code=hook_code,
            position=position,
            non_overridable=non_overridable,
            log_args=log_args,
            log_return=log_return,
            log_stack=log_stack,
            max_captures=max_captures,
            wait_timeout_ms=wait_ms,
            poll_interval_ms=poll_ms,
            frame_selector=None,
            frame_metadata=frame_meta,
            world=world,
            install_id=install_id,
            watch_assignments=watch_late,
        )

        persistent_registered = False
        persistent_already_registered = False
        if persistent:
            persistent_core = _build_installer_core(
                function_path=function_path,
                mode=mode,
                hook_code=hook_code,
                position=position,
                non_overridable=non_overridable,
                log_args=log_args,
                log_return=log_return,
                log_stack=log_stack,
                max_captures=max_captures,
                wait_timeout_ms=wait_ms,
                poll_interval_ms=poll_ms,
                frame_selector=selector,
                frame_metadata=None,
                world=world,
                install_id=install_id,
                watch_assignments=watch_late,
            )
            persistent_js = _wrap_installer_world(persistent_core, world)
            script_name = f"hook:{install_id}"
            persistent_already_registered = any(
                item.get("name") == script_name and item.get("content") == persistent_js
                for item in browser_manager._persistent_scripts
            )
            if not persistent_already_registered:
                await browser_manager.add_persistent_script(script_name, persistent_js)
                persistent_registered = True

        install_result, execution_backend, execution_warning = await evaluate_in_world(
            target, immediate_core, world, script_is_function=False
        )
        if not isinstance(install_result, dict) or not install_result.get("ok"):
            error = (
                install_result.get("error", "hook_install_failed")
                if isinstance(install_result, dict)
                else "hook_install_failed"
            )
            result = {
                "target": function_path,
                "mode": mode,
                "world": world,
                "persistent": persistent,
                "persistent_registered": persistent_registered,
                "persistent_already_registered": persistent_already_registered,
                "waited_ms": wait_ms,
                "watch_assignments": watch_late,
                "frame": frame_meta,
                "execution_backend": execution_backend,
                "warnings": [execution_warning] if execution_warning else None,
            }
            if persistent and error in {"target_not_found", "frame_selector_mismatch"}:
                result.update({
                    "status": "pending",
                    "install_state": "pending",
                    "pending_reason": error,
                })
            else:
                result["error"] = error
            return result
        return {
            "status": "tracing" if mode == "trace" else "hooked",
            "install_state": "installed",
            "target": function_path,
            "mode": mode,
            "position": position if mode == "intercept" else None,
            "non_overridable": non_overridable if mode == "intercept" else None,
            "persistent": persistent,
            "persistent_registered": persistent_registered,
            "persistent_already_registered": persistent_already_registered,
            "world": world,
            "waited_ms": wait_ms,
            "watch_assignments": watch_late,
            "frame": frame_meta,
            "execution_backend": execution_backend,
            "warnings": [execution_warning] if execution_warning else None,
        }
    except Exception as e:
        return {"error": str(e)}


def _trace_entry_matches(
    entry: dict,
    *,
    world: str,
    frame_url: str | None,
    frame_name: str | None,
    frame_index: int | None,
) -> bool:
    from ..utils.frames import metadata_matches

    if entry.get("world", "isolated") != world:
        return False
    return metadata_matches(
        entry.get("frame") or {},
        frame_url=frame_url,
        frame_name=frame_name,
        frame_index=frame_index,
    )


def _enrich_trace_entry(
    entry: dict,
    frame_snapshot: list[dict],
) -> dict:
    """Attach a current frame index to persistent child-frame events when unique."""
    enriched = dict(entry)
    frame = dict(enriched.get("frame") or {})
    if frame.get("index") is None:
        matches = [
            item for item in frame_snapshot
            if str(item.get("url") or "") == str(frame.get("url") or "")
            and str(item.get("name") or "") == str(frame.get("name") or "")
            and bool(item.get("is_main")) == bool(frame.get("is_main"))
        ]
        if len(matches) == 1:
            frame.update(matches[0])
    enriched["frame"] = frame
    return enriched


@mcp.tool()
async def get_trace_data(
    function_path: str | None = None,
    clear: bool = False,
    include_persistent: bool = True,
    world: str = "isolated",
    frame_url: str | None = None,
    frame_name: str | None = None,
    frame_index: int | None = None,
) -> dict:
    """Retrieve in-page traces and BrowserManager's cross-navigation cache.

    The Python-side cache is the authoritative source after reload/navigation.
    Every new trace entry includes ``frame`` metadata (URL, name, best-effort
    index, and main-frame flag).
    """
    try:
        if world not in {"isolated", "main"}:
            return {"error": "world must be 'isolated' or 'main'"}
        page = await browser_manager.get_active_page()
        frame_snapshot = list_frame_metadata(page)
        has_selector = (
            frame_url is not None or frame_name is not None or frame_index is not None
        )
        if has_selector:
            target, target_meta = resolve_frame(
                page,
                frame_url=frame_url,
                frame_name=frame_name,
                frame_index=frame_index,
            )
            targets = [(target, target_meta)]
        else:
            frames = list(getattr(page, "frames", []) or [])
            if frames and len(frames) == len(frame_snapshot):
                targets = list(zip(frames, frame_snapshot))
            else:
                target, target_meta = resolve_frame(page)
                targets = [(target, target_meta)]
        path_json = json.dumps(function_path) if function_path is not None else None
        read_core = (
            f"(() => {{ const traces = window.__mcp_traces || {{}}; "
            f"return {{[{path_json}]: traces[{path_json}] || []}}; }})()"
            if function_path is not None
            else "(() => window.__mcp_traces || {})()"
        )
        merged: dict[str, list[dict]] = {}
        for target, _target_meta in targets:
            try:
                value, _, _ = await evaluate_in_world(
                    target, read_core, world, script_is_function=False
                )
            except Exception:
                if has_selector:
                    raise
                continue
            if not isinstance(value, dict):
                continue
            for path, entries in value.items():
                destination = merged.setdefault(path, [])
                for entry in entries or []:
                    if isinstance(entry, dict):
                        destination.append(_enrich_trace_entry(entry, frame_snapshot))
        if include_persistent:
            for path, entries in browser_manager._persistent_traces.items():
                if function_path is not None and path != function_path:
                    continue
                destination = merged.setdefault(path, [])
                seen = {
                    entry.get("traceId") or json.dumps(entry, sort_keys=True, default=str)
                    for entry in destination
                    if isinstance(entry, dict)
                }
                for raw_entry in entries:
                    if not isinstance(raw_entry, dict):
                        continue
                    entry = _enrich_trace_entry(raw_entry, frame_snapshot)
                    if not _trace_entry_matches(
                        entry,
                        world=world,
                        frame_url=frame_url,
                        frame_name=frame_name,
                        frame_index=frame_index,
                    ):
                        continue
                    key = entry.get("traceId") or json.dumps(entry, sort_keys=True, default=str)
                    if key not in seen:
                        destination.append(entry)
                        seen.add(key)

        if frame_url is not None or frame_name is not None or frame_index is not None:
            for path, entries in list(merged.items()):
                merged[path] = [
                    entry for entry in entries
                    if isinstance(entry, dict) and _trace_entry_matches(
                        entry,
                        world=world,
                        frame_url=frame_url,
                        frame_name=frame_name,
                        frame_index=frame_index,
                    )
                ]

        if clear:
            delete_core = (
                f"(() => {{ if (window.__mcp_traces) delete window.__mcp_traces[{path_json}]; }})()"
                if function_path is not None
                else "(() => { window.__mcp_traces = {}; })()"
            )
            for target, _target_meta in targets:
                try:
                    await evaluate_in_world(
                        target, delete_core, world, script_is_function=False
                    )
                except Exception:
                    if has_selector:
                        raise

            paths = [function_path] if function_path is not None else list(
                browser_manager._persistent_traces
            )
            for path in paths:
                if path not in browser_manager._persistent_traces:
                    continue
                cache = browser_manager._persistent_traces[path]
                remaining = []
                for raw_entry in cache:
                    entry = _enrich_trace_entry(raw_entry, frame_snapshot)
                    if not _trace_entry_matches(
                        entry,
                        world=world,
                        frame_url=frame_url,
                        frame_name=frame_name,
                        frame_index=frame_index,
                    ):
                        remaining.append(raw_entry)
                if remaining:
                    cache.clear()
                    cache.extend(remaining)
                else:
                    browser_manager._persistent_traces.pop(path, None)
            if hasattr(browser_manager, "_persistent_trace_order"):
                valid = {
                    (path, id(entry))
                    for path, entries in browser_manager._persistent_traces.items()
                    for entry in entries
                }
                browser_manager._persistent_trace_order = deque(
                    (path, entry)
                    for path, entry in browser_manager._persistent_trace_order
                    if (path, id(entry)) in valid
                )
        return merged
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def inject_hook_preset(preset: str, persistent: bool = True) -> dict:
    """Inject a pre-built hook template for common reverse engineering tasks.

    Available presets:
        - "xhr": Hook XMLHttpRequest to log all XHR requests.
        - "fetch": Hook window.fetch to log all fetch requests.
        - "crypto": Hook btoa/atob/JSON.stringify to capture encryption I/O.
        - "websocket": Hook WebSocket to log all WS messages.
        - "debugger_bypass": Bypass anti-debugging traps.
        - "cookie": Hook document.cookie writes.
        - "runtime_probe": Full runtime probe.

    Args:
        preset: One of the above preset names.
        persistent: If True (default), survives page navigation.

    Returns:
        dict with status and the preset name.
    """
    preset_map = {
        "xhr": "xhr_hook.js",
        "fetch": "fetch_hook.js",
        "crypto": "crypto_hook.js",
        "websocket": "websocket_hook.js",
        "debugger_bypass": "debugger_trap.js",
        "cookie": "cookie_hook.js",
        "runtime_probe": "runtime_probe.js",
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
async def remove_hooks(keep_persistent: bool = False) -> dict:
    """Remove installed hooks and restore original objects in-place.

    Args:
        keep_persistent: If True, keep persistent init_scripts registered.

    Returns:
        dict with status, restored_objects, cleared counts.
    """
    try:
        page = await browser_manager.get_active_page()
        warnings: list[str] = []
        restored: list[str] = []

        uninstall_js = r"""
        (function() {
          var out = { uninstalled: [], errors: [] };
          if (typeof window.__mcp_jsvmp_uninstall === 'function') {
            try {
              var r = window.__mcp_jsvmp_uninstall();
              out.uninstalled.push({ hook: 'jsvmp_proxy',
                                     restored: (r && r.restored) || [] });
            } catch (e) { out.errors.push('jsvmp_uninstall: ' + e.message); }
          }
          if (typeof window.__mcp_transparent_uninstall === 'function') {
            try {
              var r = window.__mcp_transparent_uninstall();
              out.uninstalled.push({ hook: 'jsvmp_transparent',
                                     restored: (r && r.restored) || [] });
            } catch (e) { out.errors.push('transparent_uninstall: ' + e.message); }
          }
          return out;
        })();
        """
        try:
            in_page = await page.evaluate(uninstall_js)
            for item in (in_page.get("uninstalled") or []):
                hook = item.get("hook")
                items = item.get("restored") or []
                if items:
                    restored.extend([f"{hook}:{n}" for n in items])
                else:
                    restored.append(hook)
            for err in (in_page.get("errors") or []):
                warnings.append(f"in-page uninstall: {err}")
        except Exception as e:
            warnings.append(f"in-page uninstall eval failed: {e}")

        cleared_init = len(browser_manager._init_scripts)
        browser_manager._init_scripts.clear()
        cleared_persistent = 0
        if not keep_persistent:
            cleared_persistent = len(browser_manager._persistent_scripts)
            browser_manager._persistent_scripts.clear()
            if cleared_persistent:
                warnings.append(
                    "Persistent scripts were removed from the MCP registry, but "
                    "Playwright cannot unregister init scripts from an existing "
                    "BrowserContext. Close and relaunch the browser before relying "
                    "on their complete removal."
                )

        return {
            "status": "hooks_removed",
            "restored_objects": restored,
            "cleared_init_scripts": cleared_init,
            "cleared_persistent_scripts": cleared_persistent if not keep_persistent else 0,
            "persistent_kept": keep_persistent,
            "warnings": warnings if warnings else None,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_console_logs(
    level: str | None = None,
    keyword: str | None = None,
    clear: bool = False,
) -> list[dict]:
    """Get console output collected from the page.

    Args:
        level: Filter by log level - "log", "warn", "error", or "info".
        keyword: Filter logs containing this keyword in the text.
        clear: If True, clear the log buffer after retrieval.

    Returns:
        List of dicts with level, text, timestamp, and location.
    """
    try:
        logs = list(browser_manager._console_logs)
        if level:
            logs = [l for l in logs if l["level"] == level]
        if keyword:
            logs = [l for l in logs if keyword in (l.get("text") or "")]
        if clear:
            browser_manager._console_logs.clear()
        return logs
    except Exception as e:
        return [{"error": str(e)}]
