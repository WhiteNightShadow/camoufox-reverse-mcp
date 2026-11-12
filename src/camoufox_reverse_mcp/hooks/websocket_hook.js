// Preset "websocket": records WebSocket connections and messages in
// window.__mcp_ws_log. Runs in the page main world inside PRELUDE.
(function() {
    if (window.__mcp_ws_hooked) return;
    __mcpHide('__mcp_ws_hooked', true);
    if (!window.__mcp_ws_log) __mcpHide('__mcp_ws_log', []);

    var _apply = Reflect.apply;
    var _toTag = Object.prototype.toString;
    var _construct = Reflect.construct;
    var _WebSocket = window.WebSocket;
    var _send = _WebSocket.prototype.send;
    var _addListener = EventTarget.prototype.addEventListener;
    var _now = Date.now;
    var conns = new WeakMap();
    var _wmGet = WeakMap.prototype.get;
    var _wmSet = WeakMap.prototype.set;

    function preview(data) {
        return typeof data === 'string' ? data.slice(0, 5000)
            : '[' + _apply(_toTag, data, []).slice(8, -1) + ']';
    }

    var HookedWebSocket = function WebSocket(url, protocols) {
        if (!new.target) return _apply(_WebSocket, this, arguments);  // throws like the original
        var ws = _construct(_WebSocket, arguments, new.target);
        try {
            var conn = { url: String(url), messages: [], timestamp: _now() };
            var log = window.__mcp_ws_log;
            log[log.length] = conn;
            if (log.length > 200) log.splice(0, log.length - 200);
            _apply(_wmSet, conns, [ws, conn]);
            _apply(_addListener, ws, ['message', function (event) {
                try { conn.messages[conn.messages.length] = { direction: 'recv',
                      data: preview(event.data), timestamp: _now() }; } catch (e) {}
            }]);
            _apply(_addListener, ws, ['close', function (event) {
                try { conn.closedAt = _now(); conn.closeCode = event.code; } catch (e) {}
            }]);
        } catch (e) {}
        return ws;
    };
    HookedWebSocket.prototype = _WebSocket.prototype;
    var states = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'];
    for (var i = 0; i < states.length; i++) {
        try { __mcpDefProp(HookedWebSocket, states[i], { value: _WebSocket[states[i]], enumerable: true }); } catch (e) {}
    }

    __mcpDefine(window, 'WebSocket', __mcpNative(HookedWebSocket, 'WebSocket'), 'WebSocket');
    __mcpDefine(_WebSocket.prototype, 'send', __mcpNative(function send(data) {
        try {
            var conn = _apply(_wmGet, conns, [this]);
            if (conn) conn.messages[conn.messages.length] = { direction: 'send',
                data: preview(data), timestamp: _now() };
        } catch (e) {}
        return _apply(_send, this, arguments);
    }, 'send'), 'WebSocket.prototype.send');
})();
