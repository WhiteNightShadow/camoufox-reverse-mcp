from __future__ import annotations

import json
import os

from ..server import mcp, browser_manager


@mcp.tool()
async def hook_jsvmp_interpreter(
    script_url: str = "",
    persistent: bool = True,
    mode: str = "proxy",
    track_calls: bool = True,
    track_props: bool = True,
    track_reflect: bool = True,
    proxy_objects: list[str] | None = None,
    max_entries: int = 10000,
) -> dict:
    """Install a JSVMP runtime probe.

    Multi-path instrumentation that covers how JSVMP interpreters interact
    with the host environment. Unlike simple apply-hook approaches, this
    probe also wraps Reflect.get/apply, installs Proxies on critical global
    objects (navigator, screen, etc.), and intercepts timing/random APIs.

    Works on:
        - TikTok webmssdk.es5 (parameter-based signature)
        - obfuscator.io style VMPs
        - Custom VMPs using Reflect.* or direct invocation
        - Any VMP that does NOT hash the environment into a cookie/signature

    Scope: broad runtime probe. For VMP internals that bypass all hookable
    JS APIs, use instrument_jsvmp_source for source-level instrumentation.

    LIMITATIONS — READ BEFORE USING:
        This tool's default "proxy" mode installs Proxies on global objects
        and overrides Function.prototype.apply/call/bind. These modifications
        are DETECTABLE by signature-based anti-bot (Rui Shu 5/6, Akamai
        sensor_data v3+, Shape Security). Symptoms: repeated 412 in
        redirect_chain, challenge never passes.

        Recommended alternatives for signature-based anti-bot:
          1. instrument_jsvmp_source(mode="ast") — rewrites JS source,
             leaves the environment untouched. Signature stays valid.
          2. hook_jsvmp_interpreter(mode="transparent") — uses prototype-
             getter replacement only; no Proxy, no Function.prototype
             changes. Lower coverage but typically undetectable.

    Args:
        script_url: Target script URL substring for stack filtering.
        persistent: Survive navigation via context-level init_script.
        mode: Observation strategy:
            - "proxy"        (default) - full coverage using Proxy on globals
              + Function.prototype.apply/call override. DETECTABLE by
              signature-based anti-bot. Use for TikTok / obfuscator.io.
            - "transparent"  - prototype-getter replacement only. No Proxy,
              no Function.prototype changes. Lower coverage but dramatically
              less detectable. Try this first for Rui Shu / Akamai.
        track_calls, track_props, track_reflect, proxy_objects:
            Only apply when mode="proxy". Ignored for "transparent".
        max_entries: Log buffer cap (default 10000).

    Returns:
        dict with status, mode, coverage summary, data location.
    """
    try:
        hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")
        page = await browser_manager.get_active_page()

        if mode == "transparent":
            hook_path = os.path.join(hooks_dir, "jsvmp_transparent_hook.js")
            if not os.path.exists(hook_path):
                return {"error": "jsvmp_transparent_hook.js not found"}
            with open(hook_path, "r", encoding="utf-8") as f:
                template = f.read()

            hook_js = (template
                .replace("{{SCRIPT_URL}}", script_url.replace('"', '\\"').replace("'", "\\'"))
                .replace("{{MAX_ENTRIES}}", str(max_entries))
            )

            if persistent:
                await browser_manager.add_persistent_script(
                    f"jsvmp_transparent:{script_url or 'all'}", hook_js)
            try:
                await page.evaluate(hook_js)
            except Exception as e:
                return {
                    "status": "partial",
                    "mode": "transparent",
                    "warning": f"Evaluate on current page failed: {e}",
                    "persistent": persistent,
                    "note": "Hook is registered for future pages via add_init_script.",
                }
            return {
                "status": "instrumented",
                "mode": "transparent",
                "script_url": script_url or "(all scripts)",
                "persistent": persistent,
                "coverage": {
                    "prototype_getters": True,
                    "function_prototype": False,
                    "reflect_apis": False,
                    "proxy_objects": [],
                    "note": "Only prototype getters on Navigator/Screen/"
                            "Document etc. are tapped. Signature-safe.",
                },
                "data_location": "window.__mcp_jsvmp_log",
            }

        elif mode == "proxy":
            if proxy_objects is None:
                proxy_objects = ["navigator", "screen", "history",
                                 "localStorage", "sessionStorage", "performance"]

            with open(os.path.join(hooks_dir, "jsvmp_hook.js"), "r", encoding="utf-8") as f:
                template = f.read()

            hook_js = (template
                .replace("{{SCRIPT_URL}}", script_url.replace('"', '\\"').replace("'", "\\'"))
                .replace("{{MAX_ENTRIES}}", str(max_entries))
                .replace("{{TRACK_CALLS}}", "true" if track_calls else "false")
                .replace("{{TRACK_PROPS}}", "true" if track_props else "false")
                .replace("{{TRACK_REFLECT}}", "true" if track_reflect else "false")
                .replace("'{{PROXY_OBJECTS}}'", json.dumps(json.dumps(proxy_objects)))
            )

            if persistent:
                await browser_manager.add_persistent_script(
                    f"jsvmp_probe:{script_url or 'all'}", hook_js)
            try:
                await page.evaluate(hook_js)
            except Exception as e:
                return {
                    "status": "partial",
                    "mode": "proxy",
                    "warning": f"Evaluate on current page failed: {e}",
                    "persistent": persistent,
                    "script_url": script_url,
                }
            return {
                "status": "instrumented",
                "mode": "proxy",
                "script_url": script_url or "(all scripts)",
                "persistent": persistent,
                "coverage": {
                    "function_prototype": track_calls,
                    "reflect_apis": track_reflect,
                    "property_proxies": track_props,
                    "proxy_objects": proxy_objects if track_props else [],
                    "timing_apis": track_calls,
                },
                "data_location": "window.__mcp_jsvmp_log",
                "warning": "proxy mode is detectable by signature-based anti-bot. "
                           "If target is Rui Shu/Akamai, use mode='transparent' "
                           "or instrument_jsvmp_source instead.",
            }

        else:
            return {"error": f"unknown mode '{mode}', use 'proxy' or 'transparent'"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_jsvmp_log(
    type_filter: str | None = None,
    property_filter: str | None = None,
    func_filter: str | None = None,
    clear: bool = False,
    limit: int = 500,
) -> dict:
    """Retrieve JSVMP interpreter execution log.

    Args:
        type_filter: Filter by entry type: "api_call" or "prop_read".
        property_filter: Filter property read entries by property name substring.
        func_filter: Filter API call entries by function name substring.
        clear: If True, clear the log after retrieval.
        limit: Maximum entries to return (default 500).

    Returns:
        dict with entries list, counts, and summary of accessed APIs/properties.
    """
    try:
        page = await browser_manager.get_active_page()
        data = await page.evaluate("window.__mcp_jsvmp_log || []")

        if type_filter:
            data = [d for d in data if d.get("type") == type_filter]
        if property_filter:
            data = [d for d in data if property_filter in d.get("property", "")]
        if func_filter:
            data = [d for d in data if func_filter in d.get("func", "")]

        api_calls = {}
        prop_reads = {}
        for entry in data:
            if entry.get("type") == "api_call":
                func = entry.get("func", "unknown")
                api_calls[func] = api_calls.get(func, 0) + 1
            elif entry.get("type") == "prop_read":
                prop = entry.get("property", "unknown")
                prop_reads[prop] = prop_reads.get(prop, 0) + 1

        if clear:
            await page.evaluate("window.__mcp_jsvmp_log = []")

        return {
            "entries": data[:limit],
            "total_entries": len(data),
            "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "api_calls": dict(sorted(api_calls.items(), key=lambda x: -x[1])),
                "property_reads": dict(sorted(prop_reads.items(), key=lambda x: -x[1])),
                "unique_apis": len(api_calls),
                "unique_properties": len(prop_reads),
            },
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def dump_jsvmp_strings(script_url: str) -> dict:
    """Extract and categorize strings from a JSVMP-protected script.

    Finds large string arrays (common in JSVMP/obfuscator), collects all
    string literals, and flags suspicious VMP patterns (XOR decryption,
    while+switch dispatch loops, eval/Function constructors).

    Args:
        script_url: URL of the JSVMP-protected script.

    Returns:
        dict with string_arrays, decoded_strings, api_names, and
        suspicious_patterns.
    """
    try:
        page = await browser_manager.get_active_page()

        # Write the entire extractor as a standalone function to avoid
        # f-string + regex double-escaping issues
        extractor = r"""
        async (url) => {
            let source;
            try {
                const resp = await fetch(url);
                source = await resp.text();
            } catch (e) {
                return { error: 'Failed to fetch script: ' + e.message };
            }

            const results = {
                script_url: url,
                source_length: source.length,
                string_arrays: [],
                decoded_strings: [],
                api_names: [],
                suspicious_patterns: []
            };

            // Pattern 1: var/let/const X = [...]
            // We scan manually with bracket matching to avoid regex depth issues
            function findArrayLiterals(src) {
                const arrs = [];
                const re = /(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*\[/g;
                let m;
                while ((m = re.exec(src)) !== null) {
                    const nameEnd = m.index + m[0].length - 1;
                    let depth = 1, i = nameEnd + 1;
                    while (i < src.length && depth > 0) {
                        const c = src[i];
                        if (c === '"' || c === "'" || c === '`') {
                            const q = c;
                            i++;
                            while (i < src.length && src[i] !== q) {
                                if (src[i] === '\\') i += 2;
                                else i++;
                            }
                            i++;
                        } else if (c === '[') { depth++; i++; }
                        else if (c === ']') { depth--; i++; }
                        else i++;
                    }
                    if (depth === 0) {
                        arrs.push({ name: m[1], start: nameEnd, end: i,
                                    body: src.slice(nameEnd + 1, i - 1) });
                    }
                }
                return arrs;
            }

            function extractQuotedStrings(body) {
                const strs = [];
                let i = 0;
                while (i < body.length) {
                    const c = body[i];
                    if (c === '"' || c === "'") {
                        const q = c;
                        let s = '';
                        i++;
                        while (i < body.length && body[i] !== q) {
                            if (body[i] === '\\' && i + 1 < body.length) {
                                s += body[i] + body[i + 1];
                                i += 2;
                            } else {
                                s += body[i];
                                i++;
                            }
                        }
                        i++;
                        strs.push(s);
                    } else {
                        i++;
                    }
                }
                return strs;
            }

            const arrays = findArrayLiterals(source);
            for (const arr of arrays) {
                const strs = extractQuotedStrings(arr.body);
                if (strs.length >= 10) {
                    results.string_arrays.push({
                        variable: arr.name,
                        count: strs.length,
                        position: arr.start,
                        preview: strs.slice(0, 30)
                    });
                    results.decoded_strings.push(...strs);
                }
            }

            // Collect ALL string literals (bounded) for api-name detection
            const allStrings = new Set();
            const allLits = extractQuotedStrings(source);
            for (const s of allLits) {
                if (s.length >= 3 && s.length <= 100) {
                    try {
                        const d = JSON.parse('"' + s.replace(/"/g, '\\"') + '"');
                        allStrings.add(d);
                    } catch (e) { allStrings.add(s); }
                }
            }

            const apiKeywords = [
                'navigator', 'screen', 'document', 'window', 'location',
                'userAgent', 'platform', 'language', 'cookie', 'webdriver',
                'encrypt', 'decrypt', 'hash', 'md5', 'sha256', 'hmac', 'aes', 'base64',
                'fromCharCode', 'charCodeAt', 'btoa', 'atob',
                'encodeURIComponent', 'toString', 'valueOf',
                'apply', 'call', 'bind',
                'getTimezoneOffset', 'toISOString', 'toLocaleString',
                'addEventListener', 'dispatchEvent'
            ];
            for (const s of allStrings) {
                if (apiKeywords.some(api => s.includes(api))) {
                    results.api_names.push(s);
                }
            }

            // Suspicious patterns
            if (source.length > 100000 &&
                /while\s*\(\s*(?:!\s*!\s*\[\s*\]|true|1)\s*\)/.test(source) &&
                /switch\s*\(/.test(source)) {
                results.suspicious_patterns.push('JSVMP interpreter loop (while+switch, large source)');
            }
            if (/eval\s*\(/.test(source)) {
                results.suspicious_patterns.push('eval() usage detected');
            }
            if (/\bnew\s+Function\s*\(/.test(source) || /\bFunction\s*\(\s*['"]/.test(source)) {
                results.suspicious_patterns.push('Dynamic Function constructor');
            }
            const xorMatches = source.match(/\^\s*0x[0-9a-fA-F]+/g);
            if (xorMatches && xorMatches.length > 5) {
                results.suspicious_patterns.push('XOR decryption (' + xorMatches.length + ' ops)');
            }
            if (/atob\s*\(/.test(source) && /fromCharCode/.test(source)) {
                results.suspicious_patterns.push('Base64 + fromCharCode decoder');
            }

            results.api_names = [...new Set(results.api_names)].sort();
            results.decoded_strings = [...new Set(results.decoded_strings)].slice(0, 500);
            results.total_unique_strings = allStrings.size;

            return results;
        }
        """

        return await page.evaluate(extractor, script_url)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def find_dispatch_loops(
    script_url: str,
    min_case_count: int = 20,
) -> dict:
    """Scan a JSVMP script for bytecode-dispatch-loop candidates.

    A dispatch loop is a giant while(true) or for(;;) containing a switch
    with many cases, typical of VMP interpreters. This tool extracts such
    candidates so you can target them with instrument_jsvmp_source or
    hook_function.

    Args:
        script_url: Full URL of the JS file (will be fetched by the browser).
        min_case_count: Only report switches with at least this many cases.

    Returns:
        dict with candidates: [{fn_name, case_count, char_range, preview}]
    """
    try:
        page = await browser_manager.get_active_page()

        extractor = r"""
        async (url, minCaseCount) => {
            const resp = await fetch(url);
            const src = await resp.text();

            // Find every `switch(` and count its `case ` occurrences until
            // matching `}` - naive but works for most minified VMPs.
            const results = [];
            const switchRe = /switch\s*\(/g;
            let m;
            while ((m = switchRe.exec(src)) !== null) {
                // Find matching `{` after the switch's `)`
                let i = m.index + m[0].length;
                let depth = 1;
                while (i < src.length && src[i] !== ')') i++;
                if (i >= src.length) continue;
                while (i < src.length && src[i] !== '{') i++;
                if (i >= src.length) continue;
                const start = i;
                depth = 1; i++;
                while (i < src.length && depth > 0) {
                    if (src[i] === '{') depth++;
                    else if (src[i] === '}') depth--;
                    i++;
                }
                const end = i;
                const body = src.slice(start, end);
                const caseCount = (body.match(/\bcase\s+/g) || []).length;
                if (caseCount >= minCaseCount) {
                    // Look backwards for enclosing function name
                    let back = m.index;
                    const backSlice = src.slice(Math.max(0, back - 500), back);
                    const fnMatch = backSlice.match(/function\s+([A-Za-z_$][\w$]*)/);
                    const fnExprMatch = backSlice.match(/(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*function/);
                    results.push({
                        fn_name: (fnMatch && fnMatch[1]) || (fnExprMatch && fnExprMatch[1]) || null,
                        case_count: caseCount,
                        char_range: [start, end],
                        preview: body.slice(0, 200).replace(/\s+/g, ' ')
                    });
                }
            }
            return { candidates: results, total: results.length,
                     source_length: src.length };
        }
        """

        return await page.evaluate(extractor, [script_url, min_case_count])
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def compare_env(
    properties: list[str] | None = None,
) -> dict:
    """Collect browser environment fingerprint data for comparison with Node.js/jsdom.

    Runs a comprehensive set of environment checks in the browser and returns
    structured results. Compare these with your Node.js/jsdom environment to
    identify fingerprint differences that cause JSVMP validation failures.

    Args:
        properties: Optional list of specific properties to check.
            If omitted, checks a comprehensive default set including navigator,
            screen, canvas, WebGL, audio, and more.

    Returns:
        dict with categorized environment data (navigator, screen, canvas,
        webgl, audio, timing, etc.) and their values.
    """
    try:
        page = await browser_manager.get_active_page()

        custom_props_js = ""
        if properties:
            custom_props_js = f"""
            const customProps = {json.dumps(properties)};
            for (const prop of customProps) {{
                try {{
                    const val = eval(prop);
                    result.custom[prop] = {{
                        value: typeof val === 'object' ? JSON.stringify(val).substring(0, 500) : String(val),
                        type: typeof val
                    }};
                }} catch(e) {{
                    result.custom[prop] = {{ value: null, error: e.message }};
                }}
            }}"""

        result = await page.evaluate(f"""() => {{
            const result = {{ navigator: {{}}, screen: {{}}, canvas: {{}}, webgl: {{}},
                             audio: {{}}, timing: {{}}, misc: {{}}, custom: {{}} }};

            // Navigator
            const navProps = ['userAgent', 'platform', 'language', 'languages',
                'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints',
                'vendor', 'appVersion', 'cookieEnabled', 'doNotTrack',
                'webdriver', 'pdfViewerEnabled'];
            for (const p of navProps) {{
                try {{
                    const v = navigator[p];
                    result.navigator[p] = {{ value: typeof v === 'object' ? JSON.stringify(v) : String(v), type: typeof v }};
                }} catch(e) {{ result.navigator[p] = {{ value: null, error: e.message }}; }}
            }}
            try {{
                result.navigator.plugins_count = {{ value: navigator.plugins.length, type: 'number' }};
                result.navigator.mimeTypes_count = {{ value: navigator.mimeTypes.length, type: 'number' }};
            }} catch(e) {{}}

            // Screen
            const screenProps = ['width', 'height', 'availWidth', 'availHeight',
                'colorDepth', 'pixelDepth'];
            for (const p of screenProps) {{
                try {{
                    result.screen[p] = {{ value: screen[p], type: typeof screen[p] }};
                }} catch(e) {{ result.screen[p] = {{ value: null, error: e.message }}; }}
            }}
            result.screen.devicePixelRatio = {{ value: window.devicePixelRatio, type: 'number' }};

            // Canvas fingerprint
            try {{
                const canvas = document.createElement('canvas');
                canvas.width = 200; canvas.height = 50;
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillStyle = '#f60';
                ctx.fillRect(0, 0, 200, 50);
                ctx.fillStyle = '#069';
                ctx.fillText('fingerprint test 🎨', 2, 15);
                result.canvas.dataURL_prefix = {{ value: canvas.toDataURL().substring(0, 100), type: 'string' }};
                result.canvas.dataURL_length = {{ value: canvas.toDataURL().length, type: 'number' }};
                result.canvas.support = {{ value: true, type: 'boolean' }};
            }} catch(e) {{
                result.canvas.support = {{ value: false, error: e.message }};
            }}

            // WebGL
            try {{
                const gl = document.createElement('canvas').getContext('webgl');
                if (gl) {{
                    result.webgl.vendor = {{ value: gl.getParameter(gl.VENDOR), type: 'string' }};
                    result.webgl.renderer = {{ value: gl.getParameter(gl.RENDERER), type: 'string' }};
                    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                    if (dbg) {{
                        result.webgl.unmasked_vendor = {{ value: gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL), type: 'string' }};
                        result.webgl.unmasked_renderer = {{ value: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL), type: 'string' }};
                    }}
                    result.webgl.max_texture_size = {{ value: gl.getParameter(gl.MAX_TEXTURE_SIZE), type: 'number' }};
                    result.webgl.extensions_count = {{ value: gl.getSupportedExtensions().length, type: 'number' }};
                    result.webgl.support = {{ value: true, type: 'boolean' }};
                }}
            }} catch(e) {{
                result.webgl.support = {{ value: false, error: e.message }};
            }}

            // Audio
            try {{
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                result.audio.support = {{ value: !!AudioCtx, type: 'boolean' }};
                if (AudioCtx) {{
                    const ctx = new AudioCtx();
                    result.audio.sampleRate = {{ value: ctx.sampleRate, type: 'number' }};
                    result.audio.state = {{ value: ctx.state, type: 'string' }};
                    ctx.close();
                }}
            }} catch(e) {{
                result.audio.support = {{ value: false, error: e.message }};
            }}

            // Timing
            result.timing.timezoneOffset = {{ value: new Date().getTimezoneOffset(), type: 'number' }};
            result.timing.timezone = {{ value: Intl.DateTimeFormat().resolvedOptions().timeZone, type: 'string' }};
            try {{
                result.timing.performance_now = {{ value: typeof performance.now === 'function', type: 'boolean' }};
            }} catch(e) {{}}

            // Misc
            result.misc.localStorage_available = {{ value: !!window.localStorage, type: 'boolean' }};
            result.misc.sessionStorage_available = {{ value: !!window.sessionStorage, type: 'boolean' }};
            result.misc.indexedDB_available = {{ value: !!window.indexedDB, type: 'boolean' }};
            result.misc.webrtc_available = {{ value: !!(window.RTCPeerConnection || window.webkitRTCPeerConnection), type: 'boolean' }};
            result.misc.webworker_available = {{ value: !!window.Worker, type: 'boolean' }};
            result.misc.service_worker_available = {{ value: !!navigator.serviceWorker, type: 'boolean' }};
            result.misc.document_cookie = {{ value: document.cookie.substring(0, 200), type: 'string' }};

            {custom_props_js}

            return result;
        }}""")
        return result
    except Exception as e:
        return {"error": str(e)}