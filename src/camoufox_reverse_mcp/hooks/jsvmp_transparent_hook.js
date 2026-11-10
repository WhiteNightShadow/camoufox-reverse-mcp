/**
 * jsvmp_transparent_hook.js - Low-footprint runtime observation.
 *
 * Only replaces getter functions on prototype objects (Navigator.prototype,
 * Screen.prototype, Document.prototype, etc.). Proxy is never used.
 * Function.prototype is never touched.
 *
 * Detection resistance:
 *   - typeof navigator, navigator.constructor, Navigator itself, and the
 *     prototype chain are all identical to pristine.
 *   - the-new-getter.toString() returns the original getter's string.
 *     Function.prototype.toString.call(the-new-getter) still shows this
 *     file's source, and the getter has an own toString property.
 *   - No Proxy objects exist, so proxy-detection heuristics all fail.
 *   - The getter function object identity differs from the original.
 *
 * Template variables:
 *   {{SCRIPT_URL}}   - target script URL substring for stack filtering
 *   {{MAX_ENTRIES}}  - log buffer cap
 *
 * Output:
 *   window.__mcp_jsvmp_log - structured log array in the page main world
 *   (shared with proxy hook)
 *
 * This file is a function expression. utils/jsvmp_hooks.py calls it in the
 * page main world and handles the returned {installed, banner, ...}; the
 * probe never prints to the page's console itself.
 */
(function () {
    if (window.__mcp_jsvmp_transparent_installed) {
        return { installed: false, banner: '[JSVMP-T] Already installed, skipping' };
    }
    // The probe runs in the page main world: keep its globals out of
    // for...in and Object.keys(window).
    var _hide = Object.defineProperty;
    function hide(name, value) {
        try {
            _hide(window, name, { value: value, writable: true, configurable: true, enumerable: false });
        } catch (e) { window[name] = value; }
    }
    hide('__mcp_jsvmp_transparent_installed', true);
    if (!window.__mcp_jsvmp_log) hide('__mcp_jsvmp_log', []);

    var CFG = {
        scriptUrl: '{{SCRIPT_URL}}',
        maxEntries: {{MAX_ENTRIES}}
    };

    var _Error = Error;
    var _JSON_stringify = JSON.stringify;
    var _FP_toString = Function.prototype.toString;
    var _Object_defineProperty = Object.defineProperty;
    var _Object_getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
    var _Object_getOwnPropertyNames = Object.getOwnPropertyNames;
    var _Object_getPrototypeOf = Object.getPrototypeOf;
    var _Reflect_apply = Reflect.apply;
    var _Date_now = Date.now;
    // busy: do not record the probe's own reads, or re-enter while recording.
    var busy = true;
    var originals = [];

    function preview(v, maxLen) {
        maxLen = maxLen || 150;
        try {
            if (v === null) return 'null';
            if (v === undefined) return 'undefined';
            var t = typeof v;
            if (t === 'function') return '[Function ' + (v.name || '') + ']';
            if (t === 'object') {
                var s = _JSON_stringify(v);
                return s && s.length > maxLen ? s.substring(0, maxLen) + '...' : s;
            }
            var ss = String(v);
            return ss.length > maxLen ? ss.substring(0, maxLen) + '...' : ss;
        } catch (e) {
            try { return String(v).substring(0, maxLen); } catch (e2) { return '[unprintable]'; }
        }
    }

    function shortStack() {
        try {
            var s = new _Error().stack || '';
            return s.split('\n').slice(2, 8).join('\n');
        } catch (e) { return ''; }
    }

    function inTargetScript(stack) {
        if (!CFG.scriptUrl) return true;
        return stack && stack.indexOf(CFG.scriptUrl) !== -1;
    }

    function log(entry) {
        if (window.__mcp_jsvmp_log.length >= CFG.maxEntries) return;
        entry.ts = _Date_now();
        window.__mcp_jsvmp_log.push(entry);
    }

    function tapGetter(ownerName, owner, prop) {
        try {
            var desc = _Object_getOwnPropertyDescriptor(owner, prop);
            if (!desc) return false;
            if (desc.configurable === false) return false;
            if (typeof desc.get !== 'function') return false;

            var origGet = desc.get;
            var origToStringSrc;
            try { origToStringSrc = _Reflect_apply(_FP_toString, origGet, []); }
            catch (e) { origToStringSrc = 'function ' + prop + '() { [native code] }'; }

            var fakeGet = function () {
                var v = _Reflect_apply(origGet, this, []);
                if (!busy) {
                    busy = true;
                    try {
                        var stack = shortStack();
                        if (inTargetScript(stack)) {
                            log({
                                type: 'transparent_get',
                                owner: ownerName,
                                key: prop,
                                value: preview(v),
                                stack: stack
                            });
                        }
                    } catch (e) {} finally { busy = false; }
                }
                return v;
            };

            try {
                _Object_defineProperty(fakeGet, 'toString', {
                    value: function () { return origToStringSrc; },
                    writable: true, configurable: true, enumerable: false
                });
            } catch (e) {}

            try {
                _Object_defineProperty(fakeGet, 'name', {
                    value: origGet.name || prop,
                    writable: false, configurable: true
                });
            } catch (e) {}

            // v0.6.0: save original descriptor for uninstall
            originals.push({ owner: owner, prop: prop, desc: desc });

            _Object_defineProperty(owner, prop, {
                get: fakeGet,
                set: desc.set,
                configurable: true,
                enumerable: desc.enumerable
            });
            return true;
        } catch (e) {
            return false;
        }
    }

    var targets = [];
    function addTarget(name, obj) {
        if (obj && typeof obj === 'object') targets.push([name, obj]);
    }
    try { addTarget('Navigator', _Object_getPrototypeOf(navigator)); } catch (e) {}
    try { addTarget('Screen', _Object_getPrototypeOf(screen)); } catch (e) {}
    try { addTarget('History', _Object_getPrototypeOf(history)); } catch (e) {}
    try { addTarget('Performance', _Object_getPrototypeOf(performance)); } catch (e) {}
    try { addTarget('Location', _Object_getPrototypeOf(location)); } catch (e) {}
    try {
        var docProto = _Object_getPrototypeOf(document);
        addTarget('HTMLDocument', docProto);
        var docProto2 = _Object_getPrototypeOf(docProto);
        if (docProto2) addTarget('Document', docProto2);
    } catch (e) {}

    var tapStats = { total: 0, tapped: 0 };
    for (var i = 0; i < targets.length; i++) {
        var ownerName = targets[i][0];
        var owner = targets[i][1];
        var props;
        try { props = _Object_getOwnPropertyNames(owner); } catch (e) { continue; }
        for (var j = 0; j < props.length; j++) {
            var p = props[j];
            if (p === 'constructor' || p === '__proto__' ||
                p === 'toString' || p === 'valueOf') continue;
            tapStats.total++;
            if (tapGetter(ownerName, owner, p)) tapStats.tapped++;
        }
    }

    busy = false;

    // v0.6.0: uninstall function
    hide('__mcp_transparent_uninstall', function() {
        busy = true;
        var restored = [];
        for (var i = 0; i < originals.length; i++) {
            try {
                _Object_defineProperty(originals[i].owner, originals[i].prop, originals[i].desc);
                restored.push(originals[i].prop);
            } catch (e) {}
        }
        originals = [];
        window.__mcp_jsvmp_transparent_installed = false;
        return { restored: restored };
    });

    return {
        installed: true,
        tapped: tapStats.tapped,
        total: tapStats.total,
        banner: '[JSVMP-T] Transparent probe installed. Tapped ' +
                tapStats.tapped + '/' + tapStats.total + ' getters. ' +
                'scriptUrl=' + (CFG.scriptUrl || '(all)')
    };
})
