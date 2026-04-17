/**
 * jsvmp_hook.js - 通用 JSVMP 运行时探针
 *
 * 设计目标:
 *   不依赖 VMP 具体实现,覆盖 JS 访问宿主环境的所有入口通道,
 *   让瑞数 6、akamai sensor_data、webmssdk、自研 obfuscator 等
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
 *   window.__mcp_jsvmp_log - 结构化日志数组
 */
(function() {
    if (window.__mcp_jsvmp_installed) {
        console.log('[JSVMP] Already installed, skipping');
        return;
    }
    window.__mcp_jsvmp_installed = true;
    window.__mcp_jsvmp_log = window.__mcp_jsvmp_log || [];

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
    var _Array = Array;
    var _JSON_stringify = JSON.stringify;
    var _Object_defineProperty = Object.defineProperty;
    var _Object_getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
    var _Object_getPrototypeOf = Object.getPrototypeOf;
    var _FP_apply = Function.prototype.apply;
    var _FP_call = Function.prototype.call;
    var _FP_bind = Function.prototype.bind;
    var _FP_toString = Function.prototype.toString;
    var _Reflect_apply = Reflect.apply;
    var _Reflect_get = Reflect.get;
    var _Reflect_set = Reflect.set;
    var _Reflect_construct = Reflect.construct;

    function preview(v, maxLen) {
        maxLen = maxLen || 200;
        try {
            if (v === null) return 'null';
            if (v === undefined) return 'undefined';
            var t = typeof v;
            if (t === 'function') {
                var src = '';
                try { src = _FP_call.call(_FP_toString, v); } catch (e) {}
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
        entry.ts = Date.now();
        window.__mcp_jsvmp_log.push(entry);
    }

    // ========== 1. Function.prototype.apply / call / bind ==========
    if (CFG.trackCalls) {
        Function.prototype.apply = function (thisArg, argsArray) {
            try {
                var stack = shortStack();
                if (inTargetScript(stack)) {
                    log({
                        type: 'fn_apply',
                        name: this.name || 'anonymous',
                        args: argsArray ? preview(_Array.from(argsArray), 300) : '[]',
                        thisType: thisArg ? (thisArg.constructor && thisArg.constructor.name) || typeof thisArg : 'null',
                        stack: stack
                    });
                }
            } catch (e) {}
            return _FP_apply.call(this, thisArg, argsArray);
        };
        Function.prototype.apply.toString = function () { return 'function apply() { [native code] }'; };

        Function.prototype.call = function (thisArg) {
            try {
                var stack = shortStack();
                if (inTargetScript(stack)) {
                    var argsArr = _Array.prototype.slice.call(arguments, 1);
                    log({
                        type: 'fn_call',
                        name: this.name || 'anonymous',
                        args: preview(argsArr, 300),
                        thisType: thisArg ? (thisArg.constructor && thisArg.constructor.name) || typeof thisArg : 'null',
                        stack: stack
                    });
                }
            } catch (e) {}
            return _FP_apply.call(this, thisArg, _Array.prototype.slice.call(arguments, 1));
        };
        Function.prototype.call.toString = function () { return 'function call() { [native code] }'; };

        Function.prototype.bind = function (thisArg) {
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
            } catch (e) {}
            return _FP_apply.call(_FP_bind, this, arguments);
        };
    }

    // ========== 2. Reflect.apply / get / set / construct ==========
    if (CFG.trackReflect) {
        Reflect.apply = function (target, thisArg, args) {
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
            } catch (e) {}
            return _Reflect_apply(target, thisArg, args);
        };

        Reflect.get = function (target, key, receiver) {
            var val = arguments.length >= 3 ? _Reflect_get(target, key, receiver) : _Reflect_get(target, key);
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
            } catch (e) {}
            return val;
        };

        Reflect.set = function (target, key, value, receiver) {
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
            } catch (e) {}
            return arguments.length >= 4 ? _Reflect_set(target, key, value, receiver) : _Reflect_set(target, key, value);
        };

        Reflect.construct = function (target, args, newTarget) {
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
            } catch (e) {}
            return arguments.length >= 3 ? _Reflect_construct(target, args, newTarget) : _Reflect_construct(target, args);
        };
    }

    // ========== 3. Proxy 式属性读取追踪 ==========
    if (CFG.trackProps) {
        function wrapObjectWithProxy(parent, propName) {
            var orig = parent[propName];
            if (!orig || (typeof orig !== 'object' && typeof orig !== 'function')) return false;

            var proxy = new Proxy(orig, {
                get: function(target, key, receiver) {
                    var val;
                    try {
                        val = _Reflect_get(target, key, receiver);
                    } catch (e) {
                        val = target[key];
                    }
                    try {
                        if (typeof key === 'string' && !key.startsWith('__mcp_')) {
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
                        }
                    } catch (e) {}
                    // 方法调用保持 this 正确:如果是函数,绑定到原对象
                    if (typeof val === 'function') {
                        return val.bind(target);
                    }
                    return val;
                },
                set: function(target, key, value, receiver) {
                    try {
                        if (typeof key === 'string' && !key.startsWith('__mcp_')) {
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
                        }
                    } catch (e) {}
                    return _Reflect_set(target, key, value, receiver);
                },
                has: function(target, key) {
                    try {
                        var stack = shortStack();
                        if (inTargetScript(stack)) {
                            log({
                                type: 'proxy_has',
                                obj: propName,
                                key: String(key),
                                stack: stack
                            });
                        }
                    } catch (e) {}
                    return key in target;
                }
            });

            try {
                _Object_defineProperty(parent, propName, {
                    value: proxy,
                    writable: true,
                    configurable: true,
                    enumerable: true
                });
                return true;
            } catch (e) {
                try { parent[propName] = proxy; return true; } catch (e2) { return false; }
            }
        }

        for (var i = 0; i < CFG.proxyObjects.length; i++) {
            try {
                wrapObjectWithProxy(window, CFG.proxyObjects[i]);
            } catch (e) {
                console.warn('[JSVMP] Failed to proxy', CFG.proxyObjects[i], e.message);
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
                    apiRef.obj[apiRef.name] = function () {
                        var r = _FP_apply.call(origFn, this, arguments);
                        try {
                            var stack = shortStack();
                            if (inTargetScript(stack)) {
                                log({
                                    type: 'api_call',
                                    name: (apiRef.obj.constructor ? apiRef.obj.constructor.name : 'Object') + '.' + apiRef.name,
                                    args: preview(_Array.from(arguments), 100),
                                    returnValue: preview(r, 100),
                                    stack: stack
                                });
                            }
                        } catch (e) {}
                        return r;
                    };
                    try { apiRef.obj[apiRef.name].toString = function () { return 'function ' + apiRef.name + '() { [native code] }'; }; } catch (e) {}
                })(api, orig);
            } catch (e) {}
        }
    }

    console.log('[JSVMP] Probe installed. scriptUrl=' + (CFG.scriptUrl || '(all)') +
                ' calls=' + CFG.trackCalls + ' props=' + CFG.trackProps +
                ' reflect=' + CFG.trackReflect + ' proxyObjects=' + CFG.proxyObjects.length);
})();
