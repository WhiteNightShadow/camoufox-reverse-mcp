// Preset "crypto": records btoa/atob and JSON.stringify results in
// window.__mcp_crypto_log. Runs in the page main world inside PRELUDE.
(function() {
    if (window.__mcp_crypto_hooked) return;
    __mcpHide('__mcp_crypto_hooked', true);
    if (!window.__mcp_crypto_log) __mcpHide('__mcp_crypto_log', []);

    var _apply = Reflect.apply;
    var _btoa = window.btoa;
    var _atob = window.atob;
    var _stringify = JSON.stringify;
    var _now = Date.now;

    function push(entry) {
        var log = window.__mcp_crypto_log;
        log[log.length] = entry;
        if (log.length > 2000) log.splice(0, log.length - 2000);
    }
    // Never coerce page objects a second time: that would run their toString.
    function text(v, max) {
        if (typeof v !== 'string') return '[' + typeof v + ']';
        return v.length > max ? v.slice(0, max) : v;
    }

    __mcpDefine(window, 'btoa', __mcpNative(function btoa(s) {
        var result = _apply(_btoa, this, arguments);
        try { push({ func: 'btoa', input: text(s, 2000), output: text(result, 2000),
                     stack: new Error().stack, timestamp: _now() }); } catch (e) {}
        return result;
    }, 'btoa'), 'btoa');
    __mcpDefine(window, 'atob', __mcpNative(function atob(s) {
        var result = _apply(_atob, this, arguments);
        try { push({ func: 'atob', input: text(s, 2000), output: text(result, 2000),
                     stack: new Error().stack, timestamp: _now() }); } catch (e) {}
        return result;
    }, 'atob'), 'atob');
    // One native call per page call: the output is the stringified input.
    __mcpDefine(JSON, 'stringify', __mcpNative(function stringify(value, replacer, space) {
        var result = _apply(_stringify, this, arguments);
        try {
            if (typeof result === 'string' && result.length < 2000) {
                push({ func: 'JSON.stringify', output: result.slice(0, 500), timestamp: _now() });
            }
        } catch (e) {}
        return result;
    }, 'stringify'), 'JSON.stringify');
})();
