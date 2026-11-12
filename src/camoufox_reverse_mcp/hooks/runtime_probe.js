/**
 * Preset "runtime_probe": low-overhead observer of APIs anti-bot code uses.
 * Records into window.__mcp_runtime_log (max 5000 entries):
 *   - XMLHttpRequest.open / send, fetch(...)
 *   - HTMLCanvasElement.toDataURL, getContext (2d/webgl)
 *   - WebGLRenderingContext.getParameter
 *   - navigator userAgent/platform/language/webdriver/... getters
 *   - addEventListener for mouse/keyboard/touch/device-motion events
 * Runs in the page main world inside utils/presets.py PRELUDE; remove_hooks()
 * restores every replaced function.
 */
(function () {
    if (window.__mcp_runtime_probe_installed) return;
    __mcpHide('__mcp_runtime_probe_installed', true);
    if (!window.__mcp_runtime_log) __mcpHide('__mcp_runtime_log', []);
    var MAX = 5000;
    var _apply = Reflect.apply;
    var _now = Date.now;
    var _stringify = JSON.stringify;
    var _toTag = Object.prototype.toString;
    var requests = new WeakMap();
    var _wmGet = WeakMap.prototype.get;
    var _wmSet = WeakMap.prototype.set;

    function log(e) {
        var arr = window.__mcp_runtime_log;
        if (arr.length >= MAX) return;
        e.ts = _now();
        arr[arr.length] = e;
    }
    function preview(v) {
        try {
            if (v == null) return String(v);
            var t = typeof v;
            if (t === 'function') return '[fn]';
            if (t === 'object') {
                // Plain data only: no toJSON or getters of page objects.
                var tag = _apply(_toTag, v, []);
                if (tag !== '[object Object]' && tag !== '[object Array]') return tag;
                var s = _stringify(v);
                return s && s.length > 120 ? s.slice(0, 120) + '...' : s;
            }
            if (t !== 'string' && t !== 'number' && t !== 'boolean') return '[' + t + ']';
            var s2 = String(v);
            return s2.length > 120 ? s2.slice(0, 120) + '...' : s2;
        } catch (e) { return '[err]'; }
    }
    function shortStack() {
        try { return (new Error().stack || '').split('\n').slice(1, 5).join('\n'); } catch (e) { return ''; }
    }
    function wrap(owner, key, label, make) {
        try {
            var orig = owner[key];
            if (typeof orig !== 'function') return;
            __mcpDefine(owner, key, __mcpNative(make(orig), key), label);
        } catch (e) {}
    }

    wrap(XMLHttpRequest.prototype, 'open', 'XMLHttpRequest.prototype.open', function (orig) {
        return function open(method, url) {
            try {
                _apply(_wmSet, requests, [this, { method: String(method), url: String(url) }]);
                log({ type: 'xhr_open', method: String(method), url: String(url), stack: shortStack() });
            } catch (e) {}
            return _apply(orig, this, arguments);
        };
    });
    wrap(XMLHttpRequest.prototype, 'send', 'XMLHttpRequest.prototype.send', function (orig) {
        return function send(body) {
            try {
                var req = _apply(_wmGet, requests, [this]);
                log({ type: 'xhr_send', method: req ? req.method : '?', url: req ? req.url : '?',
                      body: preview(body), stack: shortStack() });
            } catch (e) {}
            return _apply(orig, this, arguments);
        };
    });
    wrap(window, 'fetch', 'fetch', function (orig) {
        return function fetch(input, init) {
            try {
                var url = typeof input === 'string' ? input : (input && typeof input.url === 'string' ? input.url : '?');
                log({ type: 'fetch', url: String(url), method: (init && init.method) || 'GET',
                      body: init && init.body != null ? preview(init.body) : null, stack: shortStack() });
            } catch (e) {}
            return _apply(orig, this, arguments);
        };
    });
    wrap(HTMLCanvasElement.prototype, 'toDataURL', 'HTMLCanvasElement.prototype.toDataURL', function (orig) {
        return function toDataURL() {
            var r = _apply(orig, this, arguments);
            try { log({ type: 'canvas_toDataURL', length: r.length, prefix: r.slice(0, 50), stack: shortStack() }); } catch (e) {}
            return r;
        };
    });
    wrap(HTMLCanvasElement.prototype, 'getContext', 'HTMLCanvasElement.prototype.getContext', function (orig) {
        return function getContext(type) {
            try { log({ type: 'canvas_getContext', context_type: preview(type), stack: shortStack() }); } catch (e) {}
            return _apply(orig, this, arguments);
        };
    });
    if (window.WebGLRenderingContext) {
        wrap(WebGLRenderingContext.prototype, 'getParameter', 'WebGLRenderingContext.prototype.getParameter', function (orig) {
            return function getParameter(p) {
                var r = _apply(orig, this, arguments);
                try { log({ type: 'webgl_getParameter', param: p, value: preview(r) }); } catch (e) {}
                return r;
            };
        });
    }
    var navProto = Object.getPrototypeOf(navigator);
    var watched = ['userAgent', 'platform', 'language', 'languages',
                   'webdriver', 'hardwareConcurrency', 'deviceMemory',
                   'vendor', 'appVersion', 'plugins', 'mimeTypes'];
    for (var i = 0; i < watched.length; i++) {
        (function (p) {
            try {
                var desc = Object.getOwnPropertyDescriptor(navProto, p);
                if (!desc || !desc.get) return;
                var origGet = desc.get;
                __mcpDefine(navProto, p, {
                    get: __mcpNative(function () {
                        var v = _apply(origGet, this, []);
                        try { log({ type: 'nav_read', prop: p, value: preview(v), stack: shortStack() }); } catch (e) {}
                        return v;
                    }, p),
                    set: desc.set
                }, 'navigator.' + p);
            } catch (e) {}
        })(watched[i]);
    }
    var WATCHED_EVENTS = ['mousemove', 'mousedown', 'mouseup', 'click', 'keydown', 'keyup',
                          'devicemotion', 'deviceorientation', 'touchstart', 'touchmove'];
    wrap(EventTarget.prototype, 'addEventListener', 'EventTarget.prototype.addEventListener', function (orig) {
        return function addEventListener(type) {
            try {
                if (WATCHED_EVENTS.indexOf(type) !== -1) {
                    log({ type: 'addEventListener', event: String(type),
                          target: _apply(_toTag, this, []).slice(8, -1), stack: shortStack() });
                }
            } catch (e) {}
            return _apply(orig, this, arguments);
        };
    });
})();
