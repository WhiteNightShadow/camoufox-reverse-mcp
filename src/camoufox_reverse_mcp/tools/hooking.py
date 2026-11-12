from __future__ import annotations

import hashlib
import json
from collections import Counter, deque

from ..server import mcp, browser_manager
from ..utils.frames import list_frame_metadata, persistent_frame_guard, resolve_frame
from ..utils.jsvmp_hooks import main_world_init_script
from ..utils.presets import (
    PRESET_FILES,
    PRESET_LOGS,
    PRESET_UNINSTALL,
    canonical_preset,
    preset_expression,
)
from ..utils.worlds import (
    evaluate_in_world,
    native_main_world_available,
    wrapped_main_world_init_script,
)


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
    serialization: str = "json",
) -> str:
    """Build an async installer that returns an explicit success/failure record."""
    if mode == "intercept" and position not in {"before", "after", "replace"}:
        raise ValueError("position must be 'before', 'after', or 'replace'")
    if mode == "trace" and serialization not in {"json", "preview"}:
        raise ValueError("serialization must be 'json' or 'preview'")

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

    trace_runtime = ""
    clock_expression = "Date.now()"
    if mode == "trace":
        # One snapshot per realm, before replacing any trace target (including
        # JSON.stringify/Date.now). Later trace installations reuse this snapshot.
        trace_runtime = """
    const __mcpTraceRuntime = __mcpState.traceRuntime || (__mcpState.traceRuntime = {
        apply: Reflect.apply,
        stringify: JSON.stringify, json: JSON,
        now: Date.now, date: Date,
        log: console.log, console,
        string: String, substring: String.prototype.substring,
        Error, isArray: Array.isArray,
        logging: false, installations: 0
    });
        """
        clock_expression = "__mcpTraceRuntime.apply(__mcpTraceRuntime.now, __mcpTraceRuntime.date, [])"
        trace_frame_expression = frame_expression.replace(
            "__mcpFrameMeta()", "__mcpTraceFrameMeta()"
        ).replace("({...", "({__proto__: null, ...")
        wrapper_code = f"""
        const runtime = __mcpTraceRuntime;
        const installation = ++runtime.installations;
        let captureCount = 0;
        const maxCaptures = {max_captures};
        const serialization = {json.dumps(serialization)};
        const __mcpCut = (text, limit) => runtime.apply(runtime.substring, text, [0, limit]);
        const __mcpEncode = value => runtime.apply(runtime.stringify, runtime.json, [value]);
        const __mcpTraceFrameMeta = () => ({{
            __proto__: null,
            url: runtime.string(location.href), name: runtime.string(window.name || ''),
            index: window === window.top ? 0 : null, is_main: window === window.top
        }});
        const __mcpPreview = value => {{
            // No property reads, instanceof, enumeration, or object coercion.
            switch (typeof value) {{
                case 'object': return value === null ? null : '[object]';
                case 'function': return '[function]';
                case 'undefined': return '[undefined]';
                case 'symbol': return '[symbol]';
                case 'bigint': return '[bigint:' + runtime.string(value) + ']';
                case 'number':
                    if (value !== value) return '[NaN]';
                    if (value === Infinity) return '[Infinity]';
                    if (value === -Infinity) return '[-Infinity]';
                    if (value === 0 && 1 / value === -Infinity) return '[-0]';
            }}
            return value;
        }};
        const __mcpSafe = (value, limit) => {{
            if (serialization === 'preview')
                return __mcpCut(__mcpEncode(__mcpPreview(value)), limit);
            try {{
                const encoded = __mcpEncode(value);
                return __mcpCut(encoded === undefined ? runtime.string(value) : encoded, limit);
            }} catch(e) {{
                try {{ return __mcpCut(runtime.string(value), limit); }}
                catch(_) {{ return '[unserializable]'; }}
            }}
        }};
        const __mcpArgs = args => {{
            if (serialization === 'json') return __mcpSafe(args, 2000);
            // Only inspect the wrapper-owned rest array; never serialize it or
            // user objects, even if Array/Object.prototype has a toJSON hook.
            let encoded = '[';
            for (let i = 0; i < args.length && encoded.length < 2000; i++) {{
                if (i) encoded += ',';
                encoded += __mcpSafe(args[i], 2000);
            }}
            return __mcpCut(encoded + ']', 2000);
        }};
        const __mcpRecord = (entry, threw, value) => {{
            runtime.logging = true;
            try {{
                entry.outcome = threw ? 'throw' : 'return';
                try {{
                    if (threw) entry.thrownValue = __mcpSafe(value, 2000);
                    else if ({str(log_return).lower()}) entry.returnValue = __mcpSafe(value, 2000);
                }} catch(e) {{}}
                // The page buffer and console cache are independent sinks.
                // Clearing data must not reset the wrapper or its call counter.
                try {{
                    window.__mcp_traces = window.__mcp_traces || {{}};
                    if (!runtime.isArray(window.__mcp_traces[path]))
                        window.__mcp_traces[path] = [];
                    const entries = window.__mcp_traces[path];
                    entries[entries.length] = entry;
                }} catch(e) {{}}
                try {{
                    runtime.apply(runtime.log, runtime.console, ['__MCP_TRACE__:' +
                        __mcpEncode({{__proto__: null, ...entry, __path__: path}})]);
                }} catch(e) {{}}
                try {{
                    runtime.apply(runtime.log, runtime.console,
                        ['[TRACE:' + path + ']', 'call #' + entry.callIndex]);
                }} catch(e) {{}}
            }} catch(e) {{}}
            finally {{ runtime.logging = false; }}
        }};
        const wrapper = (() => {{
          // A strict wrapper preserves null/undefined/primitive receivers for
          // strict originals. A directive inside a rest-parameter function is illegal.
          'use strict';
          return function(...args) {{
            if (runtime.logging || captureCount >= maxCaptures)
                return runtime.apply(original, this, args);
            const entry = {{
                __proto__: null,
                traceId: {install_json} + ':' + installation + ':' + (++captureCount),
                callIndex: captureCount, serialization, completion: 'sync',
                world: {json.dumps(world)},
            }};
            runtime.logging = true;
            try {{
                try {{ entry.timestamp = runtime.apply(runtime.now, runtime.date, []); }} catch(e) {{}}
                try {{ entry.frame = {trace_frame_expression}; }} catch(e) {{}}
                try {{ if ({str(log_args).lower()}) entry.args = __mcpArgs(args); }} catch(e) {{}}
                try {{ if ({str(log_stack).lower()}) entry.stack = new runtime.Error().stack; }} catch(e) {{}}
            }} finally {{ runtime.logging = false; }}
            // The guard covers logging only: real nested/recursive calls are traced.
            // Never await, read .then, or attach handlers to the returned value.
            let result, threw = false;
            try {{ result = runtime.apply(original, this, args); }}
            catch(value) {{ result = value; threw = true; }}
            __mcpRecord(entry, threw, result);
            if (threw) throw result;
            return result;
          }};
        }})();
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
    const __mcpState = window.__mcp_function_hook_state
        || (window.__mcp_function_hook_state = {{records: [], pending: []}});
    {trace_runtime}
    window.__mcp_function_uninstall = () => {{
        const result = {{restored: [], errors: [], cancelled: 0}};
        for (const pending of __mcpState.pending.splice(0)) {{
            pending.cancelled = true;
            const errors = pending.cleanup();
            if (errors.length) {{
                result.errors.push(...errors);
                __mcpState.pending.push(pending);
            }}
            result.cancelled++;
        }}
        const remaining = [];
        for (const record of __mcpState.records.splice(0).reverse()) {{
            try {{
                // Preserve replacements made by the page after our hook.
                if (record.parent[record.key] !== record.wrapper) continue;
                if (record.descriptor) {{
                    if (record.descriptor.set)
                        record.parent[record.key] = record.original;
                    Object.defineProperty(record.parent, record.key, record.descriptor);
                }} else {{
                    delete record.parent[record.key];
                    if (record.inheritedDescriptor && record.inheritedDescriptor.set)
                        record.parent[record.key] = record.original;
                    if (record.parent[record.key] === record.wrapper)
                        throw new Error('target remains non-configurable');
                }}
                result.restored.push(record.path);
            }} catch(error) {{
                remaining.push(record);
                result.errors.push(record.path + ': ' + error.message);
            }}
        }}
        __mcpState.records.push(...remaining.reverse());
        return result;
    }};
    let __mcpPending;
    const __mcpInstall = (parent, funcName, original) => {{
        if (original.__mcpInstallId === {install_json}) {{
            return {{ ok: true, already_installed: true, target: path,
                frame: {frame_expression} }};
        }}
        const originalDescriptor = Object.getOwnPropertyDescriptor(parent, funcName);
        let inheritedDescriptor;
        if (!originalDescriptor) {{
            for (let proto = Object.getPrototypeOf(parent); proto; proto = Object.getPrototypeOf(proto)) {{
                inheritedDescriptor = Object.getOwnPropertyDescriptor(proto, funcName);
                if (inheritedDescriptor) break;
            }}
        }}
        {wrapper_code}
        if (parent[funcName] !== wrapper) {{
            return {{ ok: false, error: 'target_not_replaceable', target: path,
                frame: {frame_expression} }};
        }}
        __mcpState.records.push({{parent, key: funcName, original,
            descriptor: originalDescriptor, inheritedDescriptor, wrapper, path}});
        return {{ ok: true, target: path, frame: {frame_expression} }};
    }};
    const __mcpTryInstall = () => {{
        if (__mcpPending && __mcpPending.cancelled)
            return {{ok: false, error: 'hook_install_cancelled', target: path}};
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
        const failed = [];
        const errors = [];
        while (__mcpWatchCleanups.length) {{
            const cleanup = __mcpWatchCleanups.pop();
            try {{ cleanup(); }} catch(error) {{
                failed.push(cleanup);
                errors.push(path + ': watcher cleanup failed: ' + error.message);
            }}
        }}
        __mcpWatchCleanups.push(...failed.reverse());
        const index = __mcpState.pending.indexOf(__mcpPending);
        if (!failed.length && index !== -1) __mcpState.pending.splice(index, 1);
        return errors;
    }};
    const __mcpArmWatcher = (parent, key) => {{
        if (!{str(watch_assignments).lower()} || !parent
                || (typeof parent !== 'object' && typeof parent !== 'function')) return false;
        let keys = __mcpWatched.get(parent);
        if (!keys) {{ keys = new Set(); __mcpWatched.set(parent, keys); }}
        if (keys.has(key)) return true;
        const own = Object.getOwnPropertyDescriptor(parent, key);
        if (own && (!own.configurable || own.get || own.set || !own.writable)) return false;
        if (!own) {{
            for (let proto = Object.getPrototypeOf(parent); proto; proto = Object.getPrototypeOf(proto)) {{
                const inherited = Object.getOwnPropertyDescriptor(proto, key);
                if (!inherited) continue;
                // Do not replace inherited setter/read-only assignment semantics.
                if (inherited.get || inherited.set || !inherited.writable) return false;
                break;
            }}
        }}
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
                const remaining = Object.getOwnPropertyDescriptor(parent, key);
                if (remaining && remaining.get === watcherGet && remaining.set === watcherSet)
                    throw new Error('property ' + key + ' is no longer configurable');
            }});
            keys.add(key);
            return true;
        }} catch(e) {{ return false; }}
    }};
    __mcpPending = {{cancelled: false, cleanup: __mcpCleanupWatchers}};
    __mcpState.pending.push(__mcpPending);
    const deadline = {clock_expression} + {wait_timeout_ms};
    try {{
        const initial = __mcpTryInstall();
        if (initial.ok || initial.error) return initial;
        if (initial.missingParent) __mcpArmWatcher(initial.missingParent, initial.missingKey);
        while (true) {{
            if ({clock_expression} >= deadline) {{
                return {{ ok: false, error: 'target_not_found', target: path,
                    waited_ms: {wait_timeout_ms}, frame: __mcpFrameMeta() }};
            }}
            await new Promise(resolve => setTimeout(resolve, {poll_interval_ms}));
            const outcome = __mcpTryInstall();
            if (outcome.ok || outcome.error) return outcome;
            if (outcome.missingParent) __mcpArmWatcher(outcome.missingParent, outcome.missingKey);
        }}
    }} finally {{
        const cleanupErrors = __mcpCleanupWatchers();
        if (cleanupErrors.length)
            return {{ok: false, error: 'watcher_cleanup_failed', target: path,
                details: cleanupErrors}};
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
    serialization: str = "json",
) -> dict:
    """Hook or trace a function (v0.9.0 unified).

    Replaces hook_function + trace_function.

    Args:
        function_path: Full path like "window.encrypt",
            "XMLHttpRequest.prototype.open", "JSON.stringify".
        mode:
          "intercept" — inject custom JS before/after/replace the function.
                        Requires hook_code. (was: hook_function)
          "trace"     — log synchronous returns/throws and optionally args and
                        call stacks. (was: trace_function)
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
        serialization: For "trace": "json" (default) retains JSON text fields,
            but executes getters/toJSON and may fall back to String conversion;
            this can have side effects. "preview" reads no properties of argument,
            return, or thrown objects and never coerces them: objects/functions
            are placeholders, symbols omit their description, and undefined,
            BigInt, NaN, infinities and -0 have string tags. Only the wrapper's
            own argument array is traversed. Text is truncated at 2000 characters
            in both modes and is not necessarily complete/parseable JSON.

    Trace semantics:
        Ordinary synchronous calls keep the original receiver, argument values,
        return/throw identity and one original invocation. Entries retain traceId,
        callIndex, timestamp, world, frame, args/returnValue and optional stack;
        outcome is "return" or "throw", with thrownValue for synchronous throws.
        completion="sync" always: a returned Promise/thenable is only a synchronous
        return, never an observed settlement (no await or attached handlers).
        Logging failures do not replace the original result/exception. Calls made
        by logging/JSON serialization are not recursively traced; real nested
        calls are. Clearing logs does not reset max_captures or callIndex.
        Intrinsics are saved at the first trace installation in each realm, so
        later hooks cannot redirect the logger. Already modified third-party
        intrinsics cannot be recovered. Preview limits value inspection, not
        timing/stack/identity visibility of wrappers or page-controlled sinks;
        optional stack capture may run custom stack formatting. Constructor/new
        semantics are outside this ordinary-call trace contract.

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
        if mode == "trace" and serialization not in {"json", "preview"}:
            return {"error": "serialization must be 'json' or 'preview'"}
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
        try:
            target, frame_meta = resolve_frame(
                page,
                frame_url=frame_url,
                frame_name=frame_name,
                frame_index=frame_index,
            )
        except ValueError as exc:
            if not persistent or not str(exc).startswith("frame_not_found:"):
                raise
            # A future iframe may not exist yet. Register its guarded init
            # script now so its first page-script call can still be captured.
            target, frame_meta = None, None
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
                **({"serialization": serialization} if mode == "trace" else {}),
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
            serialization=serialization,
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
                serialization=serialization,
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

        if target is None:
            install_result = {"ok": False, "error": "frame_not_found"}
            execution_backend, execution_warning = None, None
        else:
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
                **({"serialization": serialization} if mode == "trace" else {}),
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
            if persistent and error in {
                "target_not_found", "frame_selector_mismatch", "frame_not_found"
            }:
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
            **({"serialization": serialization} if mode == "trace" else {}),
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
    Trace IDs use installation/call counters, without page randomness. They are
    local to a realm: frames/reloads may repeat IDs and even complete entries.
    Merge the two sinks as multisets of full entries, removing only one cached
    copy per matching live copy. Repetitions within either sink are preserved.
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
                live_copies = Counter(
                    json.dumps(entry, sort_keys=True, default=str)
                    for entry in destination
                    if isinstance(entry, dict)
                )
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
                    key = json.dumps(entry, sort_keys=True, default=str)
                    if live_copies[key]:
                        live_copies[key] -= 1
                    else:
                        destination.append(entry)

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
    """Inject a pre-built hook into the page main world.

    Presets run where page scripts run (v1.10.0; before, they ran in the
    isolated world and recorded none of the page's own calls). Page code can
    detect the replaced functions; remove_hooks() restores them in the
    current document.

    Available presets (log location in brackets, read it with
    evaluate_js("window.<name> || []", world="main")):
        - "xhr": XMLHttpRequest requests [__mcp_xhr_log].
        - "fetch": fetch requests [__mcp_fetch_log]; get_request_initiator
          also reads its stacks.
        - "crypto": btoa/atob/JSON.stringify inputs and outputs [__mcp_crypto_log].
        - "websocket": WebSocket connections and messages [__mcp_ws_log].
        - "debugger_bypass": strip `debugger` from Function(...) and timers.
        - "cookie" (alias "cookie_hook"): document.cookie reads/writes [__mcp_cookie_log].
        - "runtime_probe": XHR/fetch/canvas/WebGL/navigator/listener calls [__mcp_runtime_log].

    Args:
        preset: One of the above preset names.
        persistent: True (default) also installs it in every new document,
            including same-origin iframes; False installs it in the current
            document only.

    Returns:
        dict with status, preset, world, execution_backend and read_with.
    """
    name = canonical_preset(preset)
    if name is None:
        return {"error": f"Unknown preset: {preset}. Available: {list(PRESET_FILES)}"}
    try:
        page = await browser_manager.get_active_page()
        expression = preset_expression(name)
        install_error = None
        backend = None
        info = None
        backend_warning = None
        try:
            info, backend, backend_warning = await evaluate_in_world(
                page, expression, "main", script_is_function=False)
        except Exception as e:
            install_error = e
        if persistent:
            native = (backend == "camoufox_native" if backend
                      else await native_main_world_available(page))
            await browser_manager.add_persistent_script(
                f"preset:{name}", main_world_init_script(expression, native=native, label="MCP preset"))
        browser_manager._init_scripts.append(f"preset:{name}")
        result = {"status": "injected", "preset": name, "persistent": persistent,
                  "world": "main", "execution_backend": backend}
        if name in PRESET_LOGS:
            result["read_with"] = f'evaluate_js("window.{PRESET_LOGS[name]} || []", world="main")'
        warnings = [backend_warning] if backend_warning else []
        if install_error is not None:
            result["status"] = "partial" if persistent else "failed"
            warnings.append(f"Install in the current document failed: {install_error}"
                            + ("; new documents still get it" if persistent else ""))
        if warnings:
            result["warnings"] = warnings
        return result
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
        uninstall_incomplete = False

        uninstall_js = r"""() => {
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
        }"""
        # The JSVMP probes run in the page main world; before v1.9.3 they ran in
        # the isolated world, so look in both.
        for world in ("main", "isolated"):
            try:
                in_page, _, _ = await evaluate_in_world(page, uninstall_js, world)
                in_page = in_page if isinstance(in_page, dict) else {}
                for item in (in_page.get("uninstalled") or []):
                    hook = item.get("hook")
                    items = item.get("restored") or []
                    if items:
                        restored.extend([f"{hook}:{n}" for n in items])
                    else:
                        restored.append(hook)
                for err in (in_page.get("errors") or []):
                    warnings.append(f"in-page uninstall ({world}): {err}")
                    uninstall_incomplete = True
            except Exception as e:
                warnings.append(f"in-page uninstall eval failed ({world}): {e}")
                uninstall_incomplete = True

        frames = list(getattr(page, "frames", []) or [])
        targets = frames or [page]
        for index, target in enumerate(targets):
            try:
                outcome, _, _ = await evaluate_in_world(target, PRESET_UNINSTALL, "main")
                labels = outcome.get("restored", []) if isinstance(outcome, dict) else []
                restored.extend(f"preset:frame[{index}]:{label}" for label in labels)
            except Exception as e:
                warnings.append(f"preset uninstall failed (frame[{index}]): {e}")
                uninstall_incomplete = True
        cancelled_pending = 0
        generic_uninstall = """() => {
            if (typeof window.__mcp_function_uninstall !== 'function')
                return {restored: [], errors: [], cancelled: 0};
            return window.__mcp_function_uninstall();
        }"""
        for index, target in enumerate(targets):
            for world in ("isolated", "main"):
                try:
                    outcome, _, _ = await evaluate_in_world(
                        target, generic_uninstall, world
                    )
                    if not isinstance(outcome, dict):
                        continue
                    restored.extend(
                        f"{world}:frame[{index}]:{path}"
                        for path in outcome.get("restored", [])
                    )
                    cancelled_pending += outcome.get("cancelled", 0)
                    warnings.extend(
                        f"{world}:frame[{index}]: {error}"
                        for error in outcome.get("errors", [])
                    )
                    if outcome.get("errors"):
                        uninstall_incomplete = True
                except Exception as exc:
                    warnings.append(f"{world}:frame[{index}] uninstall failed: {exc}")
                    uninstall_incomplete = True

        cleared_init = len(browser_manager._init_scripts)
        browser_manager._init_scripts.clear()
        cleared_persistent = 0
        if not keep_persistent:
            cleared_persistent = len(browser_manager._persistent_scripts)
            browser_manager._persistent_scripts.clear()
        if cleared_init or cleared_persistent:
            browser_manager._retired_hook_scripts = True
        retired_scripts = bool(getattr(browser_manager, "_retired_hook_scripts", False))
        if retired_scripts:
            warnings.append(
                "Scripts removed from the MCP registry remain installed in the "
                "existing Playwright Page/BrowserContext and may run on navigation. "
                "Create a new browser/context for complete removal; when attached "
                "to an external browser, disconnecting/reconnecting MCP is insufficient."
            )

        return {
            "status": "hooks_partially_removed" if uninstall_incomplete else "hooks_removed",
            "restored_objects": restored,
            "cleared_init_scripts": cleared_init,
            "cleared_persistent_scripts": cleared_persistent if not keep_persistent else 0,
            "persistent_kept": keep_persistent,
            "cancelled_pending_hooks": cancelled_pending,
            "requires_relaunch": uninstall_incomplete or retired_scripts,
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
