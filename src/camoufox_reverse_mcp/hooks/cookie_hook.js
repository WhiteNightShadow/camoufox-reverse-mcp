/**
 * Preset "cookie": records document.cookie reads and writes in
 * window.__mcp_cookie_log. Runs in the page main world inside PRELUDE.
 *
 * The cookie accessor lives on Document.prototype (or HTMLDocument.prototype),
 * not on the document object: walk the prototype chain to the owner with
 * getPrototypeOf / getOwnPropertyDescriptor and replace it there.
 */
(function () {
    if (window.__mcp_cookie_hook_installed) return;
    __mcpHide('__mcp_cookie_hook_installed', true);
    if (!window.__mcp_cookie_log) __mcpHide('__mcp_cookie_log', []);

    var _apply = Reflect.apply;
    var _now = Date.now;

    function findCookieDescriptor() {
        var proto = Object.getPrototypeOf(document);
        while (proto) {
            var d = Object.getOwnPropertyDescriptor(proto, 'cookie');
            if (d) return { descriptor: d, owner: proto };
            proto = Object.getPrototypeOf(proto);
        }
        return null;
    }

    var found = findCookieDescriptor();
    if (!found || !found.descriptor.get || !found.descriptor.set) return;
    var origGet = found.descriptor.get;
    var origSet = found.descriptor.set;

    function push(entry) {
        var log = window.__mcp_cookie_log;
        log[log.length] = entry;
        if (log.length > 1000) log.splice(0, log.length - 1000);
    }

    __mcpDefine(found.owner, 'cookie', {
        get: __mcpNative(function cookie() {
            var v = _apply(origGet, this, []);
            try { push({ op: 'get', value: String(v), stack: new Error().stack, ts: _now() }); } catch (e) {}
            return v;
        }, 'cookie'),
        set: __mcpNative(function cookie(value) {
            try {
                push({ op: 'set', value: typeof value === 'string' ? value : '[' + typeof value + ']',
                       stack: new Error().stack, ts: _now() });
            } catch (e) {}
            return _apply(origSet, this, [value]);
        }, 'cookie')
    }, 'document.cookie');
})();
