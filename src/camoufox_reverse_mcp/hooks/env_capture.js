/**
 * env_capture.js - browser environment snapshot for compare_env.
 *
 * A function expression called through page.evaluate(fn, opts) in the
 * isolated world: it reads what the browser itself provides, not what page
 * scripts patched. opts = {full, sections, custom}.
 *
 *   full=false  summary: navigator/screen (fixed keys), canvas, webgl, audio,
 *               timing, misc, custom
 *   full=true   bundle: every Navigator/Screen getter, window metrics and
 *               globals, getComputedStyle names and values, CSS declaration
 *               prototype names with enumerable flags, the full canvas
 *               toDataURL, WebGL parameters/extensions/precisions, audio,
 *               storage keys (never values), an about:blank iframe compared
 *               with the top window, timing and Intl
 *
 * Every section is wrapped: one failing API reports {error} in its place.
 */
async (opts) => {
    const full = !!opts.full;
    const wanted = new Set(opts.sections || []);
    const want = (name) => wanted.size === 0 || wanted.has(name);
    const errors = {};

    function plain(v, depth) {
        depth = depth || 0;
        if (v === null || v === undefined) return v === null ? null : { __t: 'undefined' };
        const t = typeof v;
        if (t === 'number') return Number.isFinite(v) ? v : String(v);
        if (t === 'string' || t === 'boolean') return v;
        if (t === 'bigint' || t === 'symbol') return String(v);
        if (t === 'function') return { __t: 'function', name: v.name || '' };
        const tag = Object.prototype.toString.call(v).slice(8, -1);
        if (ArrayBuffer.isView(v)) return { __t: tag, values: Array.from(v) };
        if (Array.isArray(v)) return depth > 2 ? { __t: 'Array', length: v.length } : v.map((x) => plain(x, depth + 1));
        if (tag === 'PluginArray' || tag === 'MimeTypeArray') return { __t: tag, length: v.length };
        return { __t: tag };
    }
    function read(fn) {
        try { const v = fn(); return { type: typeof v, value: plain(v) }; }
        catch (e) { return { error: String(e && e.message || e) }; }
    }
    // Every getter on the prototype chain of a platform object, in order.
    function getters(obj) {
        const out = {};
        let proto = Object.getPrototypeOf(obj);
        while (proto && proto !== Object.prototype) {
            for (const name of Object.getOwnPropertyNames(proto)) {
                if (name === 'constructor' || Object.prototype.hasOwnProperty.call(out, name)) continue;
                const d = Object.getOwnPropertyDescriptor(proto, name);
                if (d && typeof d.get === 'function') out[name] = read(() => obj[name]);
            }
            proto = Object.getPrototypeOf(proto);
        }
        return out;
    }
    async function section(name, fn) {
        if (!want(name)) return undefined;
        try { return await fn(); }
        catch (e) { errors[name] = String(e && e.message || e); return { error: errors[name] }; }
    }

    const NAV_KEYS = ['userAgent', 'platform', 'language', 'languages', 'hardwareConcurrency',
                      'deviceMemory', 'maxTouchPoints', 'vendor', 'cookieEnabled', 'webdriver'];
    const SCREEN_KEYS = ['width', 'height', 'availWidth', 'availHeight', 'colorDepth'];
    const result = {};

    result.navigator = await section('navigator', () => {
        if (!full) {
            const out = {};
            for (const p of NAV_KEYS) {
                try { out[p] = { value: String(navigator[p]), type: typeof navigator[p] }; }
                catch (e) { out[p] = { value: null, error: e.message }; }
            }
            return out;
        }
        const out = getters(navigator);
        out.plugins = { type: 'object', value: Array.from(navigator.plugins || [], (p) => ({
            name: p.name, filename: p.filename, description: p.description,
            mimeTypes: Array.from(p, (m) => ({ type: m.type, suffixes: m.suffixes, description: m.description })) })) };
        out.mimeTypes = { type: 'object', value: Array.from(navigator.mimeTypes || [], (m) => ({
            type: m.type, suffixes: m.suffixes, description: m.description })) };
        return out;
    });

    result.screen = await section('screen', () => {
        if (!full) {
            const out = {};
            for (const p of SCREEN_KEYS) {
                try { out[p] = { value: screen[p], type: typeof screen[p] }; }
                catch (e) { out[p] = { value: null, error: e.message }; }
            }
            out.devicePixelRatio = { value: window.devicePixelRatio, type: 'number' };
            return out;
        }
        const out = getters(screen);
        out.orientation = read(() => screen.orientation && { type: screen.orientation.type, angle: screen.orientation.angle });
        return out;
    });

    if (full) {
        result.window = await section('window', () => {
            const metrics = {};
            for (const k of ['innerWidth', 'innerHeight', 'outerWidth', 'outerHeight', 'screenX', 'screenY',
                             'screenLeft', 'screenTop', 'devicePixelRatio', 'scrollX', 'scrollY',
                             'isSecureContext', 'crossOriginIsolated', 'origin']) {
                metrics[k] = read(() => window[k]);
            }
            const doc = {};
            for (const k of ['characterSet', 'compatMode', 'contentType', 'readyState', 'hidden',
                             'visibilityState', 'designMode', 'dir']) {
                doc[k] = read(() => document[k]);
            }
            // The isolated world lists only some globals; read the page window's
            // own names through the waiver, from descriptors, so no getter runs.
            // Globals that page scripts defined are included.
            const win = window.wrappedJSObject || window;
            const globals = Object.getOwnPropertyNames(win).filter((name) => !name.startsWith('__mcp_')).sort().map((name) => {
                let type;
                try {
                    const d = Object.getOwnPropertyDescriptor(win, name);
                    type = !d ? 'missing' : ('value' in d ? typeof d.value : 'accessor');
                } catch (e) { type = 'error'; }
                return [name, type];
            });
            const nav = performance.getEntriesByType ? performance.getEntriesByType('navigation')[0] : null;
            return {
                metrics, document: doc, globals,
                history_length: read(() => history.length),
                location: { origin: location.origin, protocol: location.protocol, pathname: location.pathname },
                navigation_type: nav ? nav.type : null,
            };
        });

        result.css = await section('css', () => {
            const cs = getComputedStyle(document.documentElement);
            const props = Array.from(cs);
            const values = {};
            for (const name of props) values[name] = cs.getPropertyValue(name);
            const style = document.documentElement.style;
            let forIn = 0;
            for (const k in style) forIn++;  // eslint-disable-line no-unused-vars
            const protoEntries = [];
            let proto = Object.getPrototypeOf(style);
            while (proto && proto !== Object.prototype) {
                const names = Object.getOwnPropertyNames(proto).map((name) => {
                    const d = Object.getOwnPropertyDescriptor(proto, name);
                    return [name, !!(d && d.enumerable)];
                });
                protoEntries.push({ proto: (proto.constructor && proto.constructor.name) || '?', names });
                proto = Object.getPrototypeOf(proto);
            }
            return { props, values, for_in_count: forIn, protoEntries };
        });
    }

    result.canvas = await section('canvas', () => {
        const c = document.createElement('canvas');
        c.width = 240; c.height = 60;
        const ctx = c.getContext('2d');
        const sample = 'Cwm fjordbank glyphs vext quiz, \u{1F603}';
        ctx.textBaseline = 'top';
        ctx.font = "14px 'Arial'";
        ctx.fillStyle = '#f60';
        ctx.fillRect(125, 1, 62, 20);
        ctx.fillStyle = '#069';
        ctx.fillText(sample, 2, 15);
        ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
        ctx.fillText(sample, 4, 17);
        const dataUrl = c.toDataURL();
        const fonts = ["14px 'Arial'", '16px serif', '16px sans-serif', '16px monospace'];
        const measure = {};
        for (const f of fonts) {
            ctx.font = f;
            const m = ctx.measureText(sample);
            measure[f] = { width: m.width, actualBoundingBoxAscent: m.actualBoundingBoxAscent,
                           actualBoundingBoxDescent: m.actualBoundingBoxDescent,
                           fontBoundingBoxAscent: m.fontBoundingBoxAscent,
                           fontBoundingBoxDescent: m.fontBoundingBoxDescent };
        }
        ctx.rect(0, 0, 10, 10);
        ctx.rect(2, 2, 6, 6);
        // compare_env hashes data_url and drops it from the summary.
        return { data_url_length: dataUrl.length, data_url: dataUrl,
                 data_url_prefix: dataUrl.slice(0, 40), measure_text: measure,
                 winding_evenodd: ctx.isPointInPath(5, 5, 'evenodd'),
                 sample_text: sample, font: "14px 'Arial'", size: [c.width, c.height] };
    });

    result.webgl = await section('webgl', () => {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
        if (!gl) return { available: false };
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        const out = {
            available: true,
            vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER),
            version: gl.getParameter(gl.VERSION),
            shading_language_version: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
            unmasked_vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
            unmasked_renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
        };
        const extensions = gl.getSupportedExtensions() || [];
        out.extension_count = extensions.length;
        if (!full) return out;
        out.extensions = extensions;
        out.context_attributes = gl.getContextAttributes();
        const params = {};
        for (const name of ['MAX_TEXTURE_SIZE', 'MAX_CUBE_MAP_TEXTURE_SIZE', 'MAX_RENDERBUFFER_SIZE',
                            'MAX_VIEWPORT_DIMS', 'MAX_VERTEX_ATTRIBS', 'MAX_VERTEX_UNIFORM_VECTORS',
                            'MAX_VARYING_VECTORS', 'MAX_FRAGMENT_UNIFORM_VECTORS',
                            'MAX_TEXTURE_IMAGE_UNITS', 'MAX_VERTEX_TEXTURE_IMAGE_UNITS',
                            'MAX_COMBINED_TEXTURE_IMAGE_UNITS', 'ALIASED_LINE_WIDTH_RANGE',
                            'ALIASED_POINT_SIZE_RANGE', 'RED_BITS', 'GREEN_BITS', 'BLUE_BITS',
                            'ALPHA_BITS', 'DEPTH_BITS', 'STENCIL_BITS', 'SUBPIXEL_BITS']) {
            params[name] = read(() => gl.getParameter(gl[name])).value;
        }
        out.parameters = params;
        const precision = {};
        for (const shader of ['VERTEX_SHADER', 'FRAGMENT_SHADER']) {
            for (const kind of ['HIGH_FLOAT', 'MEDIUM_FLOAT', 'LOW_FLOAT', 'HIGH_INT', 'MEDIUM_INT', 'LOW_INT']) {
                const f = gl.getShaderPrecisionFormat(gl[shader], gl[kind]);
                precision[shader + '.' + kind] = f ? [f.rangeMin, f.rangeMax, f.precision] : null;
            }
        }
        out.shader_precision = precision;
        const gl2 = document.createElement('canvas').getContext('webgl2');
        out.webgl2 = gl2 ? { version: gl2.getParameter(gl2.VERSION),
                             max_samples: gl2.getParameter(gl2.MAX_SAMPLES),
                             extensions: gl2.getSupportedExtensions() } : null;
        return out;
    });

    result.audio = await section('audio', async () => {
        const out = {};
        const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
        if (Offline) {
            const ctx = new Offline(1, 5000, 44100);
            const osc = ctx.createOscillator();
            osc.type = 'triangle';
            osc.frequency.value = 10000;
            const comp = ctx.createDynamicsCompressor();
            comp.threshold.value = -50; comp.knee.value = 40; comp.ratio.value = 12;
            comp.attack.value = 0; comp.release.value = 0.25;
            osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
            const buffer = await ctx.startRendering();
            // Xrays forbid reading a page TypedArray; the waived wrapper reads it.
            const channel = buffer.getChannelData(0);
            const data = channel.wrappedJSObject || channel;
            let sum = 0;
            for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i]);
            out.offline_fingerprint = sum;
            out.offline_sample_rate = buffer.sampleRate;
        }
        if (typeof AudioContext === 'function') {
            const ctx = new AudioContext();
            try {
                out.sample_rate = ctx.sampleRate;
                out.base_latency = ctx.baseLatency;
                out.output_latency = ctx.outputLatency;
                out.max_channel_count = ctx.destination.maxChannelCount;
                out.state = ctx.state;
            } finally { try { await ctx.close(); } catch (e) {} }
        }
        return out;
    });

    if (full) {
        result.storage = await section('storage', () => {
            function keys(get) {
                try {
                    const s = get();
                    const out = [];
                    for (let i = 0; i < s.length; i++) {
                        const k = s.key(i);
                        out.push([k, (s.getItem(k) || '').length]);
                    }
                    return { available: true, keys: out };
                } catch (e) { return { available: false, error: String(e && e.message || e) }; }
            }
            let cookieNames = [];
            try { cookieNames = document.cookie.split(';').map((c) => c.split('=')[0].trim()).filter(Boolean); } catch (e) {}
            return { localStorage: keys(() => localStorage), sessionStorage: keys(() => sessionStorage),
                     indexedDB: typeof indexedDB, caches: typeof caches, cookie_names: cookieNames,
                     note: 'keys and value lengths only; values are not captured' };
        });

        result.iframe = await section('iframe', () => {
            const frame = document.createElement('iframe');
            frame.style.display = 'none';
            (document.body || document.documentElement).appendChild(frame);
            try {
                const w = frame.contentWindow;
                const pick = (win) => {
                    const fields = {
                        userAgent: () => win.navigator.userAgent, platform: () => win.navigator.platform,
                        language: () => win.navigator.language,
                        languages: () => Array.from(win.navigator.languages || []),
                        hardwareConcurrency: () => win.navigator.hardwareConcurrency,
                        plugins: () => (win.navigator.plugins ? win.navigator.plugins.length : null),
                        webdriver: () => win.navigator.webdriver,
                        screen: () => [win.screen.width, win.screen.height, win.screen.colorDepth],
                        devicePixelRatio: () => win.devicePixelRatio,
                        timezoneOffset: () => new (win.wrappedJSObject || win).Date(2020, 0, 1).getTimezoneOffset(),
                    };
                    const out = {};
                    for (const k of Object.keys(fields)) {
                        try { out[k] = fields[k](); } catch (e) { out[k] = { error: String(e && e.message || e) }; }
                    }
                    return out;
                };
                const inner = pick(w), top = pick(window);
                const differs = Object.keys(inner).filter((k) => JSON.stringify(inner[k]) !== JSON.stringify(top[k]));
                return { values: inner, differs_from_top: differs,
                         note: 'a hidden about:blank iframe was added and removed again' };
            } finally { frame.remove(); }
        });
    }

    result.timing = await section('timing', () => {
        const dtf = Intl.DateTimeFormat().resolvedOptions();
        const out = {
            timezoneOffset: { value: new Date().getTimezoneOffset(), type: 'number' },
            timezone: { value: dtf.timeZone, type: 'string' },
        };
        if (full) {
            out.intl = { locale: dtf.locale, calendar: dtf.calendar, numberingSystem: dtf.numberingSystem,
                         hourCycle: dtf.hourCycle, numberLocale: Intl.NumberFormat().resolvedOptions().locale };
            out.date_sample = new Date(2020, 0, 1, 12, 0, 0).toString();
        }
        return out;
    });

    if (!full) {
        result.misc = await section('misc', () => ({
            innerSize: [window.innerWidth, window.innerHeight],
            outerSize: [window.outerWidth, window.outerHeight],
            plugins: navigator.plugins ? navigator.plugins.length : null,
            mimeTypes: navigator.mimeTypes ? navigator.mimeTypes.length : null,
            doNotTrack: navigator.doNotTrack, pdfViewerEnabled: navigator.pdfViewerEnabled,
            historyLength: history.length, characterSet: document.characterSet,
            isSecureContext: window.isSecureContext,
        }));
    }

    result.custom = {};
    for (const prop of opts.custom || []) {
        try {
            const val = (0, eval)(prop);
            result.custom[prop] = { value: typeof val === 'object' ? JSON.stringify(val).substring(0, 500) : String(val),
                                    type: typeof val };
        } catch (e) { result.custom[prop] = { value: null, error: e.message }; }
    }
    for (const k of Object.keys(result)) if (result[k] === undefined) delete result[k];
    if (Object.keys(errors).length) result.errors = errors;
    return result;
}
