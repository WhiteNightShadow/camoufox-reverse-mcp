(function() {
    const path = '{{FUNCTION_PATH}}';
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {
        parent = parent[parts[i]];
        if (!parent) { console.warn('[TRACE] Cannot find:', path); return; }
    }
    const funcName = parts[parts.length - 1];
    const original = parent[funcName];
    if (typeof original !== 'function') {
        console.warn('[TRACE] Not a function:', path);
        return;
    }

    window.__mcp_traces = window.__mcp_traces || {};
    window.__mcp_traces[path] = [];
    let captureCount = 0;
    const maxCaptures = {{MAX_CAPTURES}};

    parent[funcName] = function(...args) {
        if (captureCount >= maxCaptures) return original.apply(this, args);
        captureCount++;

        const entry = { callIndex: captureCount, timestamp: Date.now() };
        if ({{LOG_ARGS}}) entry.args = JSON.stringify(args).substring(0, 2000);
        if ({{LOG_STACK}}) entry.stack = new Error().stack;

        const result = original.apply(this, args);

        if ({{LOG_RETURN}}) {
            try { entry.returnValue = JSON.stringify(result).substring(0, 2000); }
            catch(e) { entry.returnValue = String(result).substring(0, 500); }
        }

        window.__mcp_traces[path].push(entry);
        console.log('[TRACE:' + path + ']', 'call #' + captureCount);
        return result;
    };

    Object.defineProperty(parent[funcName], 'name', { value: funcName });
    Object.defineProperty(parent[funcName], 'length', { value: original.length });
    console.log('[TRACE] Started tracing:', path, '(max', maxCaptures, 'captures)');
})();
