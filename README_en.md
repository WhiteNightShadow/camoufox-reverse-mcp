# camoufox-reverse-mcp

[中文](README.md) | [English](README_en.md)

> Anti-detection browser MCP server for JavaScript reverse engineering.

An MCP (Model Context Protocol) server that gives AI coding assistants (Claude Code, Cursor, Cline, etc.) the ability to perform JavaScript reverse engineering through the **Camoufox** anti-detection browser — including API parameter analysis, JS source analysis, dynamic debugging, function hooking, network interception, JSVMP bytecode analysis, and cookie/storage management.

## Why Camoufox?

| Feature | chrome-devtools-mcp | **camoufox-reverse-mcp** |
|---------|--------------------|-----------------------|
| Browser Engine | Chrome (Puppeteer) | **Firefox (Camoufox)** |
| Anti-Detection | None | **C++ engine-level fingerprint spoofing** |
| Debug Capability | Limited (no breakpoints) | **Playwright + JS Hook** |
| JSVMP Analysis | None | **Interpreter instrumentation + property tracing + string extraction** |
| Hook Persistence | Not supported | **Context-level persistence, auto re-inject after navigation** |

**Core Advantages:**
- Camoufox modifies fingerprint information at the **C++ engine level**, not JS patches
- Juggler protocol sandbox isolation makes Playwright **completely undetectable** by page JS
- BrowserForge generates fingerprints based on **real-world traffic distribution**
- Works on sites with strong bot detection: Rui Shu, GeeTest, Cloudflare, etc.
- Hooks use `Object.defineProperty` with **override protection**, page scripts cannot restore original methods

---

## Quick Start

### Option 1: Install via AI Chat (Recommended)

Paste the following into your AI coding tool's chat (Cursor / Claude Code / Codex, etc.):

```
Please configure camoufox-reverse-mcp and refer to this MCP for related operations: https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

The AI will automatically clone, install dependencies, and configure the MCP server.

### Option 2: Manual Installation

**1. Clone the repository**

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
```

**2. Install dependencies**

```bash
pip install -e .
```

Or with uv:

```bash
uv pip install -e .
```

**3. Configure your AI tool**

Add the MCP server config to your tool's configuration file (see "Client Configuration" below).

---

## Usage

### As MCP Server (stdio)

```bash
python -m camoufox_reverse_mcp
```

With options:

```bash
python -m camoufox_reverse_mcp \
  --proxy http://127.0.0.1:7890 \
  --geoip \
  --humanize \
  --os windows
```

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--proxy` | Proxy server URL | None |
| `--headless` | Headless mode | false |
| `--os` | OS fingerprint (windows/macos/linux) | windows |
| `--geoip` | Infer geolocation from proxy IP | false |
| `--humanize` | Humanized mouse movement | false |
| `--block-images` | Block image loading | false |
| `--block-webrtc` | Block WebRTC | false |

### Client Configuration

<details>
<summary><b>Cursor (.cursor/mcp.json)</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": ["-m", "camoufox_reverse_mcp"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": ["-m", "camoufox_reverse_mcp", "--headless"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code (with proxy)</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": [
        "-m", "camoufox_reverse_mcp",
        "--proxy", "http://127.0.0.1:7890",
        "--geoip",
        "--humanize"
      ]
    }
  }
}
```

</details>

---

## Available Tools (65)

### Navigation & Page
- `launch_browser` — Launch Camoufox (returns full session state if already running)
- `close_browser` — Close browser and release resources
- `navigate` — Navigate to URL
- `reload` / `go_back` — Reload / go back
- `take_screenshot` / `take_snapshot` — Screenshot / accessibility tree (Playwright compat)
- `click` / `type_text` — Click / type into elements
- `wait_for` — Wait for element or URL pattern
- `get_page_info` — Get current page info
- `get_page_content` — **[New]** Export rendered HTML + title + meta + visible text in one call
- `get_session_info` — **[New]** View current session: browser/context/page/capture/hook status

### Script Analysis (Reverse Engineering Core)
- `list_scripts` — List all loaded JS scripts
- `get_script_source` — Get full JS source code
- `search_code` — Search keyword across all scripts (returns total matches & script list)
- `search_code_in_script` — Search in a specific script (more precise, avoids full scan)
- `save_script` — Save JS file locally
- `get_page_html` — Get page HTML

### Debugging (Reverse Engineering Core)
- `evaluate_js` — Execute arbitrary JS in page context
- `evaluate_js_handle` — Execute JS and inspect complex objects
- `add_init_script` — Inject scripts that run before page JS (**persistent** support)
- `freeze_prototype` — Freeze prototype methods to prevent hook overrides
- `set_breakpoint_via_hook` — Set pseudo-breakpoints via JS hooks (**persistent** support)
- `get_breakpoint_data` — Get captured breakpoint data
- `get_console_logs` — Get page console output

### Hooking (Reverse Engineering Core)
- `trace_function` — Trace function calls without pausing (**persistent**, cross-navigation)
- `get_trace_data` — Get collected trace data (merges page + persistent data)
- `hook_function` — Inject custom hook code (before/after/replace, **non_overridable**)
- `inject_hook_preset` — One-click preset hooks (xhr/fetch/crypto/websocket/debugger_bypass/cookie/runtime_probe, **persistent by default**)
- `trace_property_access` — Track property access (Proxy-level), reveals JSVMP env reads
- `get_property_access_log` — Get property access records
- `remove_hooks` — Remove all hooks (optionally keep persistent ones)

### Network Analysis (Reverse Engineering Core)
- `start_network_capture` / `stop_network_capture` — Capture network traffic (**capture_body** support)
- `list_network_requests` — List captured requests with filters
- `get_network_request` — Get full request details (**include_headers=False** to save tokens)
- `get_request_initiator` — Get JS call stack that initiated a request
- `intercept_request` — Intercept: log / block / modify / mock
- `stop_intercept` — Stop interception
- `search_response_body` — **[New]** Full-text search across all captured response bodies
- `get_response_body_page` — **[New]** Paginated reading of large response bodies
- `search_json_path` — **[New]** Extract JSON data by dot-notation path (`data[*].id` wildcard)

### JSVMP Reverse Analysis

> **Anti-Bot Type → Tool Path Decision Table**
>
> Different anti-bot types require different tools. Using the wrong one causes the challenge to never pass.
>
> | Anti-Bot Type | Examples | ✅ Recommended | ❌ Do NOT Use |
> |---|---|---|---|
> | **Signature-based** (env = signature) | Rui Shu 5/6, Akamai sensor_data v3+, Shape Security | `instrument_jsvmp_source(mode="ast")` + `analyze_cookie_sources()` | `pre_inject_hooks`, `hook_jsvmp_interpreter(mode="proxy")` |
> | **Behavior-based** (param signature) | TikTok webmssdk, GeeTest gt4 | `hook_jsvmp_interpreter(mode="proxy")` full coverage | — |
> | **Pure obfuscation** | obfuscator.io, custom VMP without fingerprinting | Any combination | — |
>
> **How to identify**: `navigate()` without pre_inject, check `redirect_chain`. Repeated 412 or 302 loops → signature-based, use source instrumentation.
- `hook_jsvmp_interpreter` — **[Enhanced]** Universal JSVMP runtime probe: apply/call/bind + Reflect.* + Proxy property tracking
- `get_jsvmp_log` — Get JSVMP execution log (with API call stats & property summary)
- `dump_jsvmp_strings` — **[Fixed]** Extract JSVMP string table: manual bracket matching, no more regex hangs
- `compare_env` — Collect browser env fingerprint for comparison with Node.js/jsdom
- `find_dispatch_loops` — **[New]** Scan script for bytecode dispatch loop candidates (while+switch)

### JSVMP Source-Level Instrumentation (Universal VMP Weapon)
- `instrument_jsvmp_source` — **[New]** Rewrite JS source at HTTP layer, tap every obj[key] / fn(args) inside bytecode dispatch loops
- `get_instrumentation_log` — **[New]** Retrieve source-level instrumentation logs with hot_keys / hot_methods summary
- `get_instrumentation_status` — **[New]** Show active source-level instrumentations
- `stop_instrumentation` — **[New]** Stop one or all source instrumentations

### Cookie Attribution
- `analyze_cookie_sources` — **[New]** Attribute each cookie to HTTP Set-Cookie or JS document.cookie

### Navigation Enhancements
- `navigate` — **[Enhanced]** pre_inject_hooks, initial_status + final_status + redirect_chain
- `reload_with_hooks` — **[New]** Reload page so persistent hooks run before page JS
- `get_runtime_probe_log` — **[New]** Retrieve runtime_probe.js broad-spectrum events

### Storage Management
- `get_cookies` / `set_cookies` / `delete_cookies` — Cookie management
- `get_storage` / `set_storage` — localStorage / sessionStorage
- `export_state` / `import_state` — Save / restore browser state

### Fingerprint & Anti-Detection
- `get_fingerprint_info` — Inspect current browser fingerprint
- `check_detection` — Test anti-detection on bot detection sites
- `bypass_debugger_trap` — Bypass anti-debugging traps

---

## Usage Scenarios

### Scenario 1: Reverse Engineer Login API Signing

```
AI workflow:
1. launch_browser(headless=False, os_type="windows")
2. inject_hook_preset("xhr")           ← Inject XHR hook (persistent by default)
3. inject_hook_preset("crypto")        ← Inject crypto hooks
4. navigate("https://example.com/login")
5. type_text("#username", "test_user")
6. type_text("#password", "test_pass")
7. click("#login-btn")
8. list_network_requests(method="POST") ← Find encrypted request
9. get_network_request(request_id=3)    ← View full params
10. get_request_initiator(request_id=3) ← Find signing at main.js:1234
11. get_script_source("https://example.com/js/main.js")
12. search_code("sign")                 ← Search signing code
13. hook_function("window.getSign", ...)
14. reload → get_trace_data("window.getSign")
15. Output complete signing algorithm reconstruction
```

### Scenario 2: JSVMP-Protected Sites

```
AI workflow:
1. launch_browser(headless=False)
2. bypass_debugger_trap()                ← Bypass anti-debugging first
3. inject_hook_preset("xhr")             ← Persistent hook
4. inject_hook_preset("fetch")           ← Persistent hook
5. hook_jsvmp_interpreter("webmssdk.es5.js")  ← JSVMP instrumentation
6. trace_property_access(["navigator.*", "screen.*", "document.cookie"])
7. navigate("https://target.com")
8. Trigger target actions (pagination, search, etc.)
9. get_jsvmp_log()                       ← See which APIs JSVMP accesses
10. get_property_access_log()            ← See which env properties are read
11. dump_jsvmp_strings("webmssdk.es5.js") ← Extract string table
12. compare_env()                        ← Collect browser env for Node.js comparison
13. Reconstruct algorithm from API calls and property access records
```

### Scenario 3: Verify Anti-Detection

```
AI workflow:
1. launch_browser(os_type="windows", humanize=True)
2. check_detection()                     ← Open bot.sannysoft.com and screenshot
3. get_fingerprint_info()                ← View detailed fingerprint
4. navigate("https://browserscan.net")   ← Test more detection sites
5. take_screenshot(full_page=True)
```

### Scenario 4: Persistent Hook Workflow

```
AI workflow:
1. launch_browser()
2. inject_hook_preset("xhr", persistent=True)    ← Context-level persistence
3. inject_hook_preset("fetch", persistent=True)
4. trace_function("XMLHttpRequest.prototype.open", persistent=True)
5. navigate("https://page1.com")                 ← Hooks auto-active
6. get_trace_data()                              ← Collect data
7. navigate("https://page2.com")                 ← Hooks auto re-inject!
8. get_trace_data()                              ← Data includes both pages
9. freeze_prototype("XMLHttpRequest", "open")    ← Prevent page override
```

### Scenario 5: Large Response Body Analysis + DOM Export

```
AI workflow:
1. launch_browser()
2. start_network_capture(capture_body=True)       ← Enable body capture
3. navigate("https://example.com/data")
4. get_session_info()                             ← Confirm session & capture state
5. list_network_requests(resource_type="xhr")     ← Find target API
6. search_response_body("token")                  ← Search keyword in all bodies
7. search_json_path(request_id=5, json_path="data.list[*].sign")  ← Extract JSON data
8. get_response_body_page(request_id=5, offset=0, length=10000)   ← Paginated large body
9. get_page_content()                             ← Export rendered HTML + visible text
```

### Scenario 6: Universal JSVMP Reverse Engineering (Rui Shu 6 / Akamai / Custom VMP)

The recommended JSVMP analysis workflow — works on virtually all VMP types.

```
AI workflow:
1. launch_browser(headless=False)
2. start_network_capture(capture_body=True)
3. navigate("https://target.com/")           ← First visit to locate VMP script
4. list_network_requests(resource_type="script")
5. find_dispatch_loops(script_url="https://target.com/sdenv-xxx.js")
6. instrument_jsvmp_source("**/sdenv-*.js", mode="ast", tag="vmp1")
7. inject_hook_preset("cookie", persistent=True)
8. inject_hook_preset("xhr", persistent=True)
9. reload_with_hooks()                       ← Re-run with instrumentation hot
10. get_instrumentation_log(tag_filter="vmp1", type_filter="tap_get", limit=100)
11. get_instrumentation_log(tag_filter="vmp1", type_filter="tap_method")
12. analyze_cookie_sources()
13. Reconstruct algorithm from instrumentation data
```

> 👉 Full anti-bot type identification and workflow guide: [docs/JSVMP_PLAYBOOK.md](docs/JSVMP_PLAYBOOK.md)

---

## Changelog

### v0.5.0 (2026-04-18) — Signature-Based Anti-Bot Compatibility

> Fix the architectural issue where `pre_inject_hooks` breaks signature-based anti-bot (Rui Shu/Akamai). Add MCP-side AST rewriting, transparent observation mode, anti-bot type decision table, and JSVMP Playbook.

**Architectural Improvements**
- **`instrument_jsvmp_source` default changed to MCP-side esprima AST**: No CDN dependency, auto-fallback to regex on parse failure
- **`hook_jsvmp_interpreter` new `mode="transparent"`**: Prototype-getter replacement only, no Proxy, no Function.prototype changes
- **Anti-bot type decision table**: Signature/behavior/obfuscation types with recommended tool paths
- **JSVMP Playbook**: Per-anti-bot-type workflow guide (`docs/JSVMP_PLAYBOOK.md`)

**New Files**
- `hooks/jsvmp_transparent_hook.js`, `utils/ast_rewriter.py`, `docs/JSVMP_PLAYBOOK.md`, `tests/test_ast_rewriter.py`

**New Dependency**: `esprima>=4.0.1` (pure Python)

### v0.4.0 (2026-04-17) — Universal JSVMP Adaptation

> Make this MCP a universal JSVMP reverse engineering weapon. Add source-level instrumentation, cookie attribution, runtime probe. Fix jsvmp_hook multi-path coverage and dump_jsvmp_strings regex. Tools: 57 → 65.

**New Tools (8)**
| Tool | Description |
|------|-------------|
| `instrument_jsvmp_source` | Source-level instrumentation: rewrite JS before execution, tap every obj[key] / fn(args) |
| `get_instrumentation_log` | Retrieve source-level instrumentation logs with hot_keys / hot_methods summary |
| `get_instrumentation_status` | Show active source-level instrumentations |
| `stop_instrumentation` | Stop one or all source instrumentations |
| `find_dispatch_loops` | Scan script for bytecode dispatch loop candidates |
| `reload_with_hooks` | Reload page so persistent hooks run before page JS |
| `analyze_cookie_sources` | Attribute each cookie to HTTP Set-Cookie or JS document.cookie |
| `get_runtime_probe_log` | Retrieve runtime_probe.js broad-spectrum events |

**Major Improvements**
- **hook_jsvmp_interpreter rewrite**: Multi-path coverage (apply/call/bind + Reflect.* + Proxy + timing APIs)
- **navigate enhanced**: pre_inject_hooks, initial_status + final_status + redirect_chain
- **dump_jsvmp_strings fixed**: Manual bracket matching replaces nested regex
- **New cookie_hook.js**: Prototype-chain level document.cookie hook
- **New runtime_probe.js**: Low-overhead universal runtime observer
- **inject_hook_preset new presets**: `cookie`, `runtime_probe`

### v0.3.0 (2026-04-03) — Stability Fixes + Response Search + DOM Export + Session Management

> Fix real-world stability issues, add response body search, rendered DOM export, and session management. Tools: 52 → 57.

**New Tools (5)**
| Tool | Description |
|------|-------------|
| `search_response_body` | Full-text search across all captured response bodies |
| `get_response_body_page` | Paginated reading of large response bodies |
| `search_json_path` | Extract JSON data by dot-notation path (supports `[*]` wildcard) |
| `get_page_content` | One-click export rendered HTML + title + meta + visible text |
| `get_session_info` | View current session: browser/context/page/capture/hook status |

**Bug Fixes**
- **take_snapshot**: Fix `Page object has no attribute accessibility` for Playwright >= 1.42, auto fallback to JS implementation
- **trace_property_access**: Fix `JSON.parse` error caused by template substitution stripping JS quotes

**Improvements**
- **launch_browser**: Returns full session state when already running (page URLs, contexts, capture status)
- **get_network_request**: New `include_headers=False` option to save tokens
- **list_network_requests**: Shorter field names, URL truncated to 200 chars
- **Tool descriptions refined**: Clearer parameter docs and usage guidance for all tools

### v0.2.0 (2026-04-01) — Hook Persistence + JSVMP Analysis

> Solve the core pain point of hooks lost after navigation. Add JSVMP interpreter instrumentation, property tracing, string extraction. Tools: 44 → 52.

### v0.1.0 (2026-03-31) — Initial Release

> Camoufox anti-detection browser MCP server, 44 tools covering the full JS reverse engineering workflow.

## Feedback / Contact

Hit a bug, want a new hook preset, or just want to chat about JS reverse
engineering? Add me on WeChat:

- **WeChat ID**: `han8888v8888`

> Please note "camoufox-reverse" in your friend request so I can approve it quickly.

## License

MIT
