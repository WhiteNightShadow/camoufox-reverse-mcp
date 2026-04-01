(function() {
    const _fetch = window.fetch;
    window.__mcp_fetch_log = [];

    window.fetch = async function(input, init) {
        init = init || {};
        const url = typeof input === 'string' ? input : input.url;
        const method = init.method || (input.method) || 'GET';
        const info = {
            url, method,
            headers: init.headers || {},
            body: init.body,
            stack: new Error().stack,
            timestamp: Date.now()
        };
        try {
            const response = await _fetch.apply(this, arguments);
            info.status = response.status;
            info.ok = response.ok;
            window.__mcp_fetch_log.push(info);
            console.log('[FETCH]', method, url, response.status);
            return response;
        } catch (e) {
            info.error = e.message;
            window.__mcp_fetch_log.push(info);
            throw e;
        }
    };
})();
