// Preset "debugger_bypass": strips `debugger` from Function(...) bodies and
// drops setInterval/setTimeout callbacks that contain `debugger`.
// Runs in the page main world inside utils/presets.py PRELUDE.
(function() {
    if (window.__mcp_debugger_trap_installed) return;
    __mcpHide('__mcp_debugger_trap_installed', true);

    var _apply = Reflect.apply;
    var _Function = Function;
    var _toString = Function.prototype.toString;
    var _setInterval = window.setInterval;
    var _setTimeout = window.setTimeout;
    var _slice = Array.prototype.slice;
    var DEBUGGER = /\bdebugger\b/;
    var DEBUGGER_ALL = /\bdebugger\b/g;

    function hasDebugger(fn) {
        if (typeof fn === 'string') return DEBUGGER.test(fn);
        if (typeof fn !== 'function') return false;
        try { return DEBUGGER.test(_apply(_toString, fn, [])); } catch (e) { return false; }
    }

    var HookedFunction = function Function() {
        var args = _apply(_slice, arguments, []);
        if (args.length > 0) {
            var body = args[args.length - 1];
            if (typeof body === 'string' && DEBUGGER.test(body)) {
                args[args.length - 1] = body.replace(DEBUGGER_ALL, '');
            }
        }
        return _apply(_Function, this, args);
    };
    HookedFunction.prototype = _Function.prototype;

    __mcpDefine(window, 'Function', __mcpNative(HookedFunction, 'Function'), 'Function');
    __mcpDefine(window, 'setInterval', __mcpNative(function setInterval(fn, delay) {
        if (hasDebugger(fn)) return 0;
        return _apply(_setInterval, this, arguments);
    }, 'setInterval'), 'setInterval');
    __mcpDefine(window, 'setTimeout', __mcpNative(function setTimeout(fn, delay) {
        if (hasDebugger(fn) && (typeof fn === 'string' || delay < 5000)) return 0;
        return _apply(_setTimeout, this, arguments);
    }, 'setTimeout'), 'setTimeout');
})();
