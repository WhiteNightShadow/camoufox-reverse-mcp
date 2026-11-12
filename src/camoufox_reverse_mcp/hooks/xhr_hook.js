// Preset "xhr": records XMLHttpRequest calls in window.__mcp_xhr_log.
// Runs in the page main world inside utils/presets.py PRELUDE.
(function() {
    if (window.__mcp_xhr_hooked) return;
    __mcpHide('__mcp_xhr_hooked', true);
    if (!window.__mcp_xhr_log) __mcpHide('__mcp_xhr_log', []);

    var _apply = Reflect.apply;
    var _toTag = Object.prototype.toString;
    var proto = XMLHttpRequest.prototype;
    var _open = proto.open;
    var _send = proto.send;
    var _setReqHeader = proto.setRequestHeader;
    var _addListener = EventTarget.prototype.addEventListener;
    var _now = Date.now;
    // Per-request state lives here, not in an expando the page could see.
    var requests = new WeakMap();
    var _wmGet = WeakMap.prototype.get;
    var _wmSet = WeakMap.prototype.set;

    function record(info) {
        var log = window.__mcp_xhr_log;
        log[log.length] = info;
        if (log.length > 500) log.splice(0, log.length - 500);
    }

    var hookedOpen = function open(method, url) {
        try {
            _apply(_wmSet, requests, [this, { method: String(method), url: String(url),
                                              headers: {}, timestamp: _now() }]);
        } catch (e) {}
        return _apply(_open, this, arguments);
    };
    var hookedSetHeader = function setRequestHeader(name, value) {
        try {
            var info = _apply(_wmGet, requests, [this]);
            if (info) info.headers[String(name)] = String(value);
        } catch (e) {}
        return _apply(_setReqHeader, this, arguments);
    };
    var hookedSend = function send(body) {
        try {
            var info = _apply(_wmGet, requests, [this]);
            if (info) {
                info.body = typeof body === 'string' ? body.slice(0, 5000)
                    : (body == null ? null : '[' + _apply(_toTag, body, []).slice(8, -1) + ']');
                info.stack = new Error().stack;
                var xhr = this;
                // Recorded when sent, so pending and failed requests show up too.
                record(info);
                _apply(_addListener, xhr, ['loadend', function () {
                    try {
                        info.status = xhr.status;
                        if (xhr.responseType === '' || xhr.responseType === 'text') {
                            info.response_length = xhr.responseText.length;
                        }
                    } catch (e) {}
                }]);
            }
        } catch (e) {}
        return _apply(_send, this, arguments);
    };

    __mcpDefine(proto, 'open', __mcpNative(hookedOpen, 'open'), 'XMLHttpRequest.prototype.open');
    __mcpDefine(proto, 'setRequestHeader', __mcpNative(hookedSetHeader, 'setRequestHeader'),
                'XMLHttpRequest.prototype.setRequestHeader');
    __mcpDefine(proto, 'send', __mcpNative(hookedSend, 'send'), 'XMLHttpRequest.prototype.send');
})();
