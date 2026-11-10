/**
 * jsvmp_hook.js - 通用 JSVMP 运行时探针
 *
 * 设计目标:
 *   不依赖 VMP 具体实现,覆盖 JS 访问宿主环境的所有入口通道,
 *   让 RS、AK、TK JSVMP、常见混淆工具等
 *   VMP 执行时的每一次环境读取/API 调用都被记录。
 *
 * 模板变量:
 *   {{SCRIPT_URL}}     - 目标脚本 URL 子串,用于栈过滤。空串 = 不过滤
 *   {{MAX_ENTRIES}}    - 日志条数上限
 *   {{TRACK_CALLS}}    - 是否追踪函数调用 (true/false)
 *   {{TRACK_PROPS}}    - 是否追踪属性读取 (true/false)
 *   {{TRACK_REFLECT}}  - 是否追踪 Reflect.* (true/false)
 *   {{PROXY_OBJECTS}}  - 要装 Proxy 的全局对象名列表 JSON 字符串
 *
 * 输出:
 *   window.__mcp_jsvmp_log - 结构化日志数组(页面主世界)
 *
 * 本文件是一个函数表达式,由 utils/jsvmp_hooks.py 在页面主世界里调用。
 * 返回值 {installed, banner, ...} 交给调用方处理,探针自己不往页面的 console 打印。
 */
(function() {
    if (window.__mcp_jsvmp_installed) {
        return { installed: false, banner: '[JSVMP] Already installed, skipping' };
    }
    // 探针在页面主世界运行,自己的全局变量设为不可枚举,不出现在 for...in 和 Object.keys(window) 里
    var _hide = Object.defineProperty;
    function hide(name, value) {
        try {
            _hide(window, name, { value: value, writable: true, configurable: true, enumerable: false });
        } catch (e) { window[name] = value; }
    }
    hide('__mcp_jsvmp_installed', true);
    if (!window.__mcp_jsvmp_log) hide('__mcp_jsvmp_log', []);

    var CFG = {
        scriptUrl: '{{SCRIPT_URL}}',
        maxEntries: {{MAX_ENTRIES}},
        trackCalls: {{TRACK_CALLS}},
        trackProps: {{TRACK_PROPS}},
        trackReflect: {{TRACK_REFLECT}},
        proxyObjects: JSON.parse('{{PROXY_OBJECTS}}')
    };

    // 保存一组"原始引用" - 所有 hook 内部使用这组,避免被页面覆盖后互相污染
    var _Error = Error;
    var _JSON_stringify = JSON.stringify;
    var _Object_defineProperty = Object.defineProperty;
    var _Object_getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
    var _Object_getPrototypeOf = Object.getPrototypeOf;
    var _FP_bind = Function.prototype.bind;
    var _FP_toString = Function.prototype.toString;
    var _Reflect_apply = Reflect.apply;
    var _Reflect_get = Reflect.get;
    var _Reflect_set = Reflect.set;
    var _Reflect_construct = Reflect.construct;
    var _Array_slice = Array.prototype.slice;
    var _Array_from = Array.from;
    var _Date_now = Date.now;

    // 下面会替换 Function.prototype.call/apply/bind,探针自己的代码一律走保存好的
    // Reflect.apply,不经过被替换的方法,否则会无限递归。
    // busy 为 true 时不记录,避免记录探针自身的调用或在记录过程中再次进入。
    var busy = true;
    var replaced = [];
    var proxied = [];
    function replace(owner, key, wrapper, label) {
        try {
            var desc = _Object_getOwnPropertyDescriptor(owner, key);
            owner[key] = wrapper;
            replaced.push({ owner: owner, key: key, desc: desc, label: label });
            return true;
        } catch (e) { return false; }
    }

    function preview(v, maxLen) {
        maxLen = maxLen || 200;
        try {
            if (v === null) return 'null';
            if (v === undefined) return 'undefined';
            var t = typeof v;
            if (t === 'function') {
                var src = '';
                try { src = _Reflect_apply(_FP_toString, v, []); } catch (e) {}
                return '[Function ' + (v.name || 'anonymous') + (src.length < 80 ? ': ' + src : '') + ']';
            }
            if (t === 'object') {
                var s = _JSON_stringify(v);
                return s && s.length > maxLen ? s.substring(0, maxLen) + '...' : s;
            }
            var s2 = String(v);
            return s2.length > maxLen ? s2.substring(0, maxLen) + '...' : s2;
        } catch (e) {
            try { return String(v).substring(0, maxLen); } catch (e2) { return '[unprintable]'; }
        }
    }

    function shortStack() {
        try {
            var s = new _Error().stack || '';
            // 只保留前 6 层
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

    // ========== 1. Function.prototype.apply / call / bind ==========
    if (CFG.trackCalls) {
        replace(Function.prototype, 'apply', function (thisArg, argsArray) {
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'fn_apply',
                            name: this.name || 'anonymous',
                            args: argsArray ? preview(_Array_from(argsArray), 300) : '[]',
                            thisType: thisArg ? (thisArg.constructor && thisArg.constructor.name) || typeof thisArg : 'null',
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return _Reflect_apply(this, thisArg, argsArray == null ? [] : argsArray);
        }, 'Function.prototype.apply');
        Function.prototype.apply.toString = function () { return 'function apply() { [native code] }'; };

        replace(Function.prototype, 'call', function (thisArg) {
            var argsArr = _Reflect_apply(_Array_slice, arguments, [1]);
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'fn_call',
                            name: this.name || 'anonymous',
                            args: preview(argsArr, 300),
                            thisType: thisArg ? (thisArg.constructor && thisArg.constructor.name) || typeof thisArg : 'null',
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return _Reflect_apply(this, thisArg, argsArr);
        }, 'Function.prototype.call');
        Function.prototype.call.toString = function () { return 'function call() { [native code] }'; };

        replace(Function.prototype, 'bind', function (thisArg) {
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'fn_bind',
                            name: this.name || 'anonymous',
                            boundThisType: thisArg ? (thisArg.constructor && thisArg.constructor.name) || typeof thisArg : 'null',
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return _Reflect_apply(_FP_bind, this, arguments);
        }, 'Function.prototype.bind');
    }

    // ========== 2. Reflect.apply / get / set / construct ==========
    if (CFG.trackReflect) {
        replace(Reflect, 'apply', function (target, thisArg, args) {
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'reflect_apply',
                            name: (target && target.name) || 'anonymous',
                            args: preview(args, 300),
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return _Reflect_apply(target, thisArg, args);
        }, 'Reflect.apply');

        replace(Reflect, 'get', function (target, key, receiver) {
            var val = arguments.length >= 3 ? _Reflect_get(target, key, receiver) : _Reflect_get(target, key);
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'reflect_get',
                            targetType: (target && target.constructor && target.constructor.name) || typeof target,
                            key: String(key),
                            value: preview(val, 150),
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return val;
        }, 'Reflect.get');

        replace(Reflect, 'set', function (target, key, value, receiver) {
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'reflect_set',
                            targetType: (target && target.constructor && target.constructor.name) || typeof target,
                            key: String(key),
                            value: preview(value, 150),
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return arguments.length >= 4 ? _Reflect_set(target, key, value, receiver) : _Reflect_set(target, key, value);
        }, 'Reflect.set');

        replace(Reflect, 'construct', function (target, args, newTarget) {
            if (!busy) {
                busy = true;
                try {
                    var stack = shortStack();
                    if (inTargetScript(stack)) {
                        log({
                            type: 'reflect_construct',
                            name: (target && target.name) || 'anonymous',
                            args: preview(args, 300),
                            stack: stack
                        });
                    }
                } catch (e) {} finally { busy = false; }
            }
            return arguments.length >= 3 ? _Reflect_construct(target, args, newTarget) : _Reflect_construct(target, args);
        }, 'Reflect.construct');
    }

    // ========== 3. Proxy 式属性读取追踪 ==========
    var proxyFailures = [];
    if (CFG.trackProps) {
        var wrapObjectWithProxy = function (parent, propName) {
            var orig;
            try { orig = parent[propName]; } catch (e) { return false; }
            if (!orig || (typeof orig !== 'object' && typeof orig !== 'function')) return false;
            // 卸载时按原描述符还原(window.navigator 等本来是 getter)
            var origDesc;
            try { origDesc = _Object_getOwnPropertyDescriptor(parent, propName); } catch (e) {}

            // v0.6.0: per-proxy reentrance guard
            var _inGetTrap = false;
            var _inSetTrap = false;

            var proxy = new Proxy(orig, {
                get: function (target, key, receiver) {
                    if (_inGetTrap) {
                        try { return target[key]; } catch (e) { return undefined; }
                    }
                    _inGetTrap = true;
                    var val;
                    try {
                        try {
                            val = _Reflect_get(target, key, receiver);
                        } catch (e) {
                            try { val = target[key]; } catch (e2) { val = undefined; }
                        }
                        if (!busy && typeof key === 'string' && key.indexOf('__mcp_') !== 0) {
                            busy = true;
                            try {
                                var stack = shortStack();
                                if (inTargetScript(stack)) {
                                    log({
                                        type: 'proxy_get',
                                        obj: propName,
                                        key: key,
                                        value: preview(val, 150),
                                        stack: stack
                                    });
                                }
                            } catch (e) {} finally { busy = false; }
                        }
                    } finally {
                        _inGetTrap = false;
                    }
                    if (typeof val === 'function') {
                        try { return _Reflect_apply(_FP_bind, val, [target]); } catch (e) { return val; }
                    }
                    return val;
                },
                set: function (target, key, value, receiver) {
                    if (_inSetTrap) {
                        try { return _Reflect_set(target, key, value, receiver); }
                        catch (e) { try { target[key] = value; } catch (e2) {} return true; }
                    }
                    _inSetTrap = true;
                    try {
                        if (!busy && typeof key === 'string' && key.indexOf('__mcp_') !== 0) {
                            busy = true;
                            try {
                                var stack = shortStack();
                                if (inTargetScript(stack)) {
                                    log({
                                        type: 'proxy_set',
                                        obj: propName,
                                        key: key,
                                        value: preview(value, 150),
                                        stack: stack
                                    });
                                }
                            } catch (e) {} finally { busy = false; }
                        }
                        return _Reflect_set(target, key, value, receiver);
                    } finally {
                        _inSetTrap = false;
                    }
                },
                has: function (target, key) {
                    if (!busy) {
                        busy = true;
                        try {
                            var stack = shortStack();
                            if (inTargetScript(stack)) {
                                log({ type: 'proxy_has', obj: propName, key: String(key), stack: stack });
                            }
                        } catch (e) {} finally { busy = false; }
                    }
                    return key in target;
                }
            });

            try {
                _Object_defineProperty(parent, propName, {
                    value: proxy, writable: true, configurable: true, enumerable: true
                });
            } catch (e) {
                try { parent[propName] = proxy; } catch (e2) { return false; }
            }
            proxied.push({ owner: parent, key: propName, desc: origDesc, value: orig });
            return true;
        };

        for (var i = 0; i < CFG.proxyObjects.length; i++) {
            try {
                if (!wrapObjectWithProxy(window, CFG.proxyObjects[i])) proxyFailures.push(CFG.proxyObjects[i]);
            } catch (e) {
                proxyFailures.push(CFG.proxyObjects[i] + ': ' + e.message);
            }
        }
    }

    // ========== 4. 常见环境探测 API ==========
    if (CFG.trackCalls) {
        var sensitiveApis = [
            { obj: Date, name: 'now', kind: 'static' },
            { obj: performance, name: 'now', kind: 'instance' },
            { obj: Math, name: 'random', kind: 'static' }
        ];
        for (var j = 0; j < sensitiveApis.length; j++) {
            try {
                var api = sensitiveApis[j];
                var orig = api.obj[api.name];
                if (typeof orig !== 'function') continue;
                (function(apiRef, origFn) {
                    var wrapper = function () {
                        var r = _Reflect_apply(origFn, this, arguments);
                        if (!busy) {
                            busy = true;
                            try {
                                var stack = shortStack();
                                if (inTargetScript(stack)) {
                                    log({
                                        type: 'api_call',
                                        name: (apiRef.obj.constructor ? apiRef.obj.constructor.name : 'Object') + '.' + apiRef.name,
                                        args: preview(_Array_from(arguments), 100),
                                        returnValue: preview(r, 100),
                                        stack: stack
                                    });
                                }
                            } catch (e) {} finally { busy = false; }
                        }
                        return r;
                    };
                    var ownerName = apiRef.obj === Date ? 'Date' : apiRef.obj === Math ? 'Math' : 'performance';
                    if (replace(apiRef.obj, apiRef.name, wrapper, ownerName + '.' + apiRef.name)) {
                        try { wrapper.toString = function () { return 'function ' + apiRef.name + '() { [native code] }'; }; } catch (e) {}
                    }
                })(api, orig);
            } catch (e) {}
        }
    }

    busy = false;

    // remove_hooks 调用:按原描述符还原替换过的函数和装了 Proxy 的全局对象
    hide('__mcp_jsvmp_uninstall', function() {
        busy = true;
        var restored = [];
        function restore(item, label) {
            try {
                if (item.desc) _Object_defineProperty(item.owner, item.key, item.desc);
                else delete item.owner[item.key];
                restored.push(label);
            } catch (e) {
                try { item.owner[item.key] = item.value; restored.push(label + '(fallback)'); } catch (e2) {}
            }
        }
        for (var r = replaced.length - 1; r >= 0; r--) restore(replaced[r], replaced[r].label);
        for (var q = proxied.length - 1; q >= 0; q--) restore(proxied[q], proxied[q].key);
        replaced = [];
        proxied = [];
        window.__mcp_jsvmp_installed = false;
        return { restored: restored };
    });

    return {
        installed: true,
        proxyFailures: proxyFailures,
        banner: '[JSVMP] Probe installed. scriptUrl=' + (CFG.scriptUrl || '(all)') +
                ' calls=' + CFG.trackCalls + ' props=' + CFG.trackProps +
                ' reflect=' + CFG.trackReflect + ' proxyObjects=' + CFG.proxyObjects.length
    };
})
