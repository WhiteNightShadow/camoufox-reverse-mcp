(function() {
    window.__mcp_crypto_log = [];

    const _btoa = window.btoa;
    const _atob = window.atob;
    window.btoa = function(s) {
        const result = _btoa.call(this, s);
        const info = { func: 'btoa', input: s, output: result, stack: new Error().stack, timestamp: Date.now() };
        window.__mcp_crypto_log.push(info);
        console.log('[CRYPTO] btoa:', s.substring(0, 100), '->', result.substring(0, 100));
        return result;
    };
    window.atob = function(s) {
        const result = _atob.call(this, s);
        const info = { func: 'atob', input: s, output: result, stack: new Error().stack, timestamp: Date.now() };
        window.__mcp_crypto_log.push(info);
        return result;
    };

    const _stringify = JSON.stringify;
    JSON.stringify = function() {
        const result = _stringify.apply(this, arguments);
        if (result && result.length < 2000) {
            window.__mcp_crypto_log.push({
                func: 'JSON.stringify',
                input: _stringify(arguments[0]).substring(0, 500),
                output: result.substring(0, 500),
                timestamp: Date.now()
            });
        }
        return result;
    };
})();
