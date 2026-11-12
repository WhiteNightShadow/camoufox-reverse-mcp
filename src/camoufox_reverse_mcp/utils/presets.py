"""Build the hook presets and install them in the page main world.

Before v1.10.0 the presets ran in Playwright's isolated world. There they
patched Xray views that page scripts never use, so a page's own XHR, fetch,
cookie or btoa calls were not recorded. Each preset file now runs inside
PRELUDE, which gives it:

  __mcpDefine(owner, key, value)  replace owner[key], keep its attributes and
                                  remember the original for remove_hooks()
  __mcpHide(name, value)          a non-enumerable window property

window.__mcp_preset_uninstall() restores everything the presets replaced in
the current document.
"""
from __future__ import annotations

import json
import os

HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")

PRESET_FILES = {
    "xhr": "xhr_hook.js",
    "fetch": "fetch_hook.js",
    "crypto": "crypto_hook.js",
    "websocket": "websocket_hook.js",
    "debugger_bypass": "debugger_trap.js",
    "cookie": "cookie_hook.js",
    "runtime_probe": "runtime_probe.js",
}
# launch_browser/navigate(pre_inject_hooks=...) used to call the cookie preset
# "cookie_hook"; both names work everywhere now.
PRESET_ALIASES = {"cookie_hook": "cookie"}
PRESET_LOGS = {
    "xhr": "__mcp_xhr_log",
    "fetch": "__mcp_fetch_log",
    "crypto": "__mcp_crypto_log",
    "websocket": "__mcp_ws_log",
    "cookie": "__mcp_cookie_log",
    "runtime_probe": "__mcp_runtime_log",
}

PRELUDE = r"""
    var __mcpDefProp = Object.defineProperty;
    var __mcpGetDesc = Object.getOwnPropertyDescriptor;
    function __mcpHide(name, value) {
        try {
            __mcpDefProp(window, name, { value: value, writable: true, configurable: true, enumerable: false });
        } catch (e) { window[name] = value; }
    }
    if (!window.__mcp_preset_restore) {
        __mcpHide('__mcp_preset_restore', []);
        __mcpHide('__mcp_preset_uninstall', function () {
            var list = window.__mcp_preset_restore || [];
            var restored = [];
            for (var i = list.length - 1; i >= 0; i--) {
                var item = list[i];
                try {
                    if (item.desc) __mcpDefProp(item.owner, item.key, item.desc);
                    else delete item.owner[item.key];
                    restored[restored.length] = item.label;
                } catch (e) {}
            }
            window.__mcp_preset_restore = [];
            var flags = ['__mcp_xhr_hooked', '__mcp_fetch_hooked', '__mcp_crypto_hooked',
                         '__mcp_ws_hooked', '__mcp_debugger_trap_installed',
                         '__mcp_cookie_hook_installed', '__mcp_runtime_probe_installed'];
            for (var f = 0; f < flags.length; f++) {
                try { if (window[flags[f]]) window[flags[f]] = false; } catch (e) {}
            }
            return { restored: restored };
        });
    }
    // Replace owner[key]. A data property keeps its attributes and gets the new
    // value; pass an accessor descriptor ({get, set}) to replace an accessor.
    function __mcpDefine(owner, key, value, label) {
        var desc = __mcpGetDesc(owner, key);
        var next;
        if (value && typeof value === 'object' && (value.get || value.set)) {
            next = { get: value.get, set: value.set, configurable: true,
                     enumerable: desc ? desc.enumerable : false };
        } else {
            next = { value: value, writable: desc ? desc.writable !== false : true,
                     configurable: true, enumerable: desc ? !!desc.enumerable : false };
        }
        __mcpDefProp(owner, key, next);
        var list = window.__mcp_preset_restore;
        list[list.length] = { owner: owner, key: key, desc: desc, label: label || String(key) };
    }
    // fn.toString() of a wrapper reads like the native function it replaces.
    function __mcpNative(fn, name) {
        try {
            __mcpDefProp(fn, 'toString', {
                value: function toString() { return 'function ' + name + '() {\n    [native code]\n}'; },
                writable: true, configurable: true, enumerable: false });
        } catch (e) {}
        return fn;
    }
"""


def canonical_preset(name: str) -> str | None:
    """Return the preset's canonical name, or None when it is unknown."""
    name = PRESET_ALIASES.get(name, name)
    return name if name in PRESET_FILES else None


def preset_expression(name: str) -> str:
    """One JS expression that installs a preset in the current realm.

    It evaluates to {installed: true, preset}. Run it in the page main world
    with evaluate_in_world(..., script_is_function=False), or wrap it with
    jsvmp_hooks.main_world_init_script for new documents.
    """
    canonical = canonical_preset(name)
    if canonical is None:
        raise ValueError(f"unknown preset: {name}")
    with open(os.path.join(HOOKS_DIR, PRESET_FILES[canonical]), "r", encoding="utf-8") as f:
        source = f.read()
    return ("(function () {\n" + PRELUDE + ";\n" + source
            + "\n;return {installed: true, preset: " + json.dumps(canonical) + "};\n})()")


PRESET_UNINSTALL = """() => {
    if (typeof window.__mcp_preset_uninstall !== 'function') return {restored: []};
    return window.__mcp_preset_uninstall();
}"""
