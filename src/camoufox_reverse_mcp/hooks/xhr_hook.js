(function() {
    const _open = XMLHttpRequest.prototype.open;
    const _send = XMLHttpRequest.prototype.send;
    const _setReqHeader = XMLHttpRequest.prototype.setRequestHeader;

    window.__mcp_xhr_log = [];

    XMLHttpRequest.prototype.open = function(method, url, async, user, password) {
        this.__mcp_info = { method, url, headers: {}, timestamp: Date.now() };
        return _open.apply(this, arguments);
    };

    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
        if (this.__mcp_info) this.__mcp_info.headers[name] = value;
        return _setReqHeader.apply(this, arguments);
    };

    XMLHttpRequest.prototype.send = function(body) {
        if (this.__mcp_info) {
            this.__mcp_info.body = body;
            this.__mcp_info.stack = new Error().stack;
            const info = this.__mcp_info;
            this.addEventListener('load', function() {
                info.status = this.status;
                info.response_length = this.responseText?.length;
                window.__mcp_xhr_log.push(info);
                console.log('[XHR]', info.method, info.url, info.status);
            });
        }
        return _send.apply(this, arguments);
    };
})();
