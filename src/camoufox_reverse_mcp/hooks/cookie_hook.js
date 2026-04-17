/**
 * cookie_hook.js - document.cookie 原型链级 hook
 *
 * 关键点:
 *   document.cookie 的 descriptor 定义在 Document.prototype (或 HTMLDocument.prototype)
 *   上,不在 document 实例上。必须沿原型链找到定义 setter/getter 的位置再替换。
 *   直接 Object.defineProperty(document, 'cookie', ...) 会被浏览器忽略或抛错。
 */
(function () {
    if (window.__mcp_cookie_hook_installed) return;
    window.__mcp_cookie_hook_installed = true;
    window.__mcp_cookie_log = window.__mcp_cookie_log || [];

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
    if (!found) {
        console.warn('[COOKIE-HOOK] cookie descriptor not found on prototype chain');
        return;
    }

    var descriptor = found.descriptor;
    var owner = found.owner;
    var origSet = descriptor.set;
    var origGet = descriptor.get;

    if (!origSet || !origGet) {
        console.warn('[COOKIE-HOOK] cookie descriptor missing getter/setter');
        return;
    }

    Object.defineProperty(owner, 'cookie', {
        set: function(value) {
            try {
                window.__mcp_cookie_log.push({
                    op: 'set',
                    value: String(value),
                    stack: new Error().stack,
                    ts: Date.now()
                });
                if (window.__mcp_cookie_log.length > 1000) window.__mcp_cookie_log.shift();
            } catch (e) {}
            return origSet.call(this, value);
        },
        get: function() {
            var v = origGet.call(this);
            try {
                window.__mcp_cookie_log.push({
                    op: 'get',
                    value: String(v),
                    stack: new Error().stack,
                    ts: Date.now()
                });
                if (window.__mcp_cookie_log.length > 1000) window.__mcp_cookie_log.shift();
            } catch (e) {}
            return v;
        },
        configurable: true,
        enumerable: true
    });

    console.log('[COOKIE-HOOK] installed on', owner.constructor.name || 'Document.prototype');
})();
