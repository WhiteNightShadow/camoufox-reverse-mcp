// Preset "fetch": records fetch() calls in window.__mcp_fetch_log and their
// call stacks in window.__mcp_fetch_initiator_log (get_request_initiator).
// Runs in the page main world inside utils/presets.py PRELUDE.
(function() {
    if (window.__mcp_fetch_hooked) return;
    __mcpHide('__mcp_fetch_hooked', true);
    if (!window.__mcp_fetch_log) __mcpHide('__mcp_fetch_log', []);
    if (!window.__mcp_fetch_initiator_log) __mcpHide('__mcp_fetch_initiator_log', []);

    var _apply = Reflect.apply;
    var _toTag = Object.prototype.toString;
    var _hasOwn = Object.prototype.hasOwnProperty;
    var _fetch = window.fetch;
    var _then = Promise.prototype.then;
    var _now = Date.now;
    var _Request = typeof Request === 'function' ? Request : null;
    var _Headers = typeof Headers === 'function' ? Headers : null;

    function push(log, entry) {
        log[log.length] = entry;
        if (log.length > 500) log.splice(0, log.length - 500);
    }
    function headersOf(h) {
        var out = {};
        try {
            if (!h) return out;
            if (_Headers && h instanceof _Headers) {
                h.forEach(function (value, name) { out[name] = value; });
            } else if (Array.isArray(h)) {
                for (var i = 0; i < h.length; i++) out[String(h[i][0])] = String(h[i][1]);
            } else if (typeof h === 'object') {
                for (var k in h) if (_apply(_hasOwn, h, [k])) out[k] = String(h[k]);
            }
        } catch (e) {}
        return out;
    }

    var hookedFetch = function fetch(input, init) {
        var info = null;
        try {
            var isRequest = _Request && input instanceof _Request;
            var url = typeof input === 'string' ? input : (isRequest ? input.url : String(input));
            var method = (init && init.method) || (isRequest ? input.method : 'GET') || 'GET';
            var body = init && init.body != null ? init.body : null;
            var stack = '';
            try { stack = new Error().stack || ''; } catch (e) {}
            info = {
                url: url, method: String(method),
                headers: headersOf(init && init.headers),
                body: typeof body === 'string' ? body.slice(0, 5000)
                    : (body == null ? null : '[' + _apply(_toTag, body, []).slice(8, -1) + ']'),
                stack: stack, timestamp: _now()
            };
            push(window.__mcp_fetch_log, info);
            push(window.__mcp_fetch_initiator_log, { url: String(url), method: String(method),
                stack: stack.split('\n').slice(0, 15).join('\n'), ts: info.timestamp });
        } catch (e) {}
        var pending = _apply(_fetch, this, arguments);
        if (!info) return pending;
        // The page receives this derived promise; it settles like the original.
        return _apply(_then, pending, [
            function (response) {
                try { info.status = response.status; info.ok = response.ok; } catch (e) {}
                return response;
            },
            function (error) {
                try { info.error = String(error && error.message || error); } catch (e) {}
                throw error;
            }
        ]);
    };

    __mcpDefine(window, 'fetch', __mcpNative(hookedFetch, 'fetch'), 'fetch');
})();
