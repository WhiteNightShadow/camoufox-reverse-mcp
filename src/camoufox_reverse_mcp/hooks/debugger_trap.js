(function() {
    const _Function = Function;
    Function = function(...args) {
        if (args.length > 0) {
            const body = args[args.length - 1];
            if (typeof body === 'string' && /\bdebugger\b/.test(body)) {
                args[args.length - 1] = body.replace(/\bdebugger\b/g, '');
            }
        }
        return _Function.apply(this, args);
    };
    Function.prototype = _Function.prototype;
    Object.defineProperty(Function, 'name', { value: 'Function' });

    const _setInterval = window.setInterval;
    window.setInterval = function(fn, delay, ...args) {
        if (typeof fn === 'string' && /debugger/.test(fn)) return 0;
        if (typeof fn === 'function') {
            const src = fn.toString();
            if (/debugger/.test(src)) return 0;
        }
        return _setInterval.call(this, fn, delay, ...args);
    };

    const _setTimeout = window.setTimeout;
    window.setTimeout = function(fn, delay, ...args) {
        if (typeof fn === 'string' && /debugger/.test(fn)) return 0;
        if (typeof fn === 'function') {
            const src = fn.toString();
            if (/debugger/.test(src) && delay < 5000) return 0;
        }
        return _setTimeout.call(this, fn, delay, ...args);
    };

    const _toString = Function.prototype.toString;
    Function.prototype.toString = function() {
        const result = _toString.call(this);
        if (this === Function.prototype.toString) return 'function toString() { [native code] }';
        return result;
    };

    console.log('[ANTI-DEBUG] Debugger traps bypassed');
})();
