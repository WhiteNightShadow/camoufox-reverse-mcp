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
| JSVMP Analysis | None | **Interpreter instrumentation + source-level rewriting** |
| Hook Persistence | Not supported | **Context-level persistence, auto re-inject after navigation** |

**Core Advantages:**
- Camoufox modifies fingerprint information at the **C++ engine level**, not JS patches — fundamentally undetectable
- Juggler protocol sandbox isolation makes Playwright **completely undetectable** by page JS
- BrowserForge generates fingerprints based on **real-world traffic distribution**
- Works on sites with strong bot detection: RS, AK, JY, CF, etc.
- Hooks use `Object.defineProperty` with **override protection**

---

## Quick Start

### Option 1: Install via AI Chat (Recommended)

Paste the following into your AI coding tool's chat (Cursor / Claude Code / Codex, etc.):

```
Please install this MCP tool: camoufox-reverse-mcp
Project URL: https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

The AI will automatically clone, install dependencies, and configure the MCP server.

### Option 2: Manual Installation

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
pip install -e .
```

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

## Available Tools (32)

### Browser Control
| Tool | Description |
|------|-------------|
| `launch_browser` | Launch Camoufox anti-detection browser |
| `close_browser` | Close browser and release resources |
| `navigate` | Navigate to URL (supports pre_inject_hooks, redirect_chain tracking) |
| `reload` | Reload current page |
| `take_screenshot` | Screenshot (full page or specific element) |
| `take_snapshot` | Get accessibility tree (token-efficient) |
| `click` / `type_text` | Click element / type text |
| `wait_for` | Wait for element or URL pattern |
| `get_page_info` | Get current page URL, title, viewport |

### JS Execution & Debugging
| Tool | Description |
|------|-------------|
| `evaluate_js` | Execute arbitrary JS in page context (multi-strategy JSON parsing) |

### Script Analysis
| Tool | Description |
|------|-------------|
| `scripts(action)` | Script management: `list` / `get` source / `save` to local file |
| `search_code` | Search keyword (`script_url=None` for all scripts, or specify URL for single-script with auto char-mode for minified files) |

### Hooking & Tracing
| Tool | Description |
|------|-------------|
| `hook_function` | Hook or trace a function: `mode="intercept"` for custom code / `mode="trace"` for non-invasive tracing |
| `inject_hook_preset` | One-click preset hooks (xhr / fetch / crypto / websocket / debugger_bypass / cookie / runtime_probe) |
| `remove_hooks` | Remove all hooks and restore original objects |
| `get_console_logs` | Get page console output |

### Network Analysis
| Tool | Description |
|------|-------------|
| `network_capture(action)` | Capture control: `start` / `stop` / `clear` / `status` |
| `list_network_requests` | List captured requests (filter by URL / domain / method / type / status) |
| `get_network_request` | Get full request details (`max_body_size` controls body truncation) |
| `get_request_initiator` | Get JS call stack that initiated a request |
| `intercept_request` | Intercept requests: log / block / modify / mock / stop |

### JSVMP Reverse Analysis

> **Anti-Bot Type → Tool Path**
>
> | Type | Examples | ✅ Recommended | ❌ Avoid |
> |---|---|---|---|
> | **Signature-based** | RS 5/6, AK sensor_data | `instrumentation(action="install")` | `pre_inject_hooks`, `hook_jsvmp_interpreter(mode="proxy")` |
> | **Behavior-based** | TK JSVMP, JY gt4 | `hook_jsvmp_interpreter(mode="proxy")` | — |
> | **Pure obfuscation** | JS obfuscation tools | Any combination | — |

| Tool | Description |
|------|-------------|
| `hook_jsvmp_interpreter` | JSVMP runtime probe (`mode="proxy"` full coverage / `mode="transparent"` signature-safe) |
| `instrumentation(action)` | Source-level instrumentation: `install` / `log` / `stop` / `reload` / `status` |
| `compare_env` | Collect browser env fingerprint for Node.js/jsdom comparison |

### Cookies & Storage
| Tool | Description |
|------|-------------|
| `cookies(action)` | Cookie management: `get` / `set` / `delete` |
| `get_storage` | Get localStorage / sessionStorage |
| `export_state` / `import_state` | Save / restore full browser state |

### Verification & Environment
| Tool | Description |
|------|-------------|
| `verify_signer_offline` | Offline signer verification: provide samples, get char-level diff at first divergence |
| `check_environment` | One-stop self-check: MCP version, dependencies, browser state |
| `reset_browser_state` | Clear residuals (hooks / capture / routes) without closing browser |

---

## Usage Scenarios

### Scenario 1: Reverse Engineer Login API Signing

```
1. launch_browser()
2. inject_hook_preset("xhr")
3. inject_hook_preset("crypto")
4. navigate("https://example.com/login")
5. type_text("#username", "test") → click("#login-btn")
6. list_network_requests(method="POST")
7. get_request_initiator(request_id=3)     ← Find signing function
8. search_code("sign")                     ← Search signing code
9. hook_function("window.getSign", mode="trace")
10. reload() → get_console_logs()          ← Collect trace data
```

### Scenario 2: Universal JSVMP Reverse (RS / AK / Custom VMP)

```
1. launch_browser()
2. network_capture(action="start")
3. navigate("https://target-site.com/")
4. list_network_requests(resource_type="script")  ← Find VMP script
5. instrumentation(action="install", url_pattern="**/vmp_target*.js", mode="ast")
6. inject_hook_preset("cookie", persistent=True)
7. instrumentation(action="reload")               ← Activate instrumentation
8. instrumentation(action="log", type_filter="tap_get")  ← See env reads
9. instrumentation(action="log", type_filter="tap_method") ← See API calls
10. compare_env()                                  ← Collect env for Node.js
```

### Scenario 3: Verify Signing Code

```
1. launch_browser() → navigate("https://target.com")
2. network_capture(action="start")
3. # Trigger target actions, collect signed requests
4. reqs = list_network_requests(url_filter="api/search")
5. # Extract samples
6. verify_signer_offline(
     signer_code="(s) => ({'X-Bogus': mySign(s.url)})",
     samples=[{"id": "r1", "input": {...}, "expected": {"X-Bogus": "..."}}]
   )
```

> 👉 Full anti-bot type identification and workflow guide: [docs/JSVMP_PLAYBOOK.md](docs/JSVMP_PLAYBOOK.md)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│           AI Coding Assistant (Cursor / Claude)  │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│           camoufox-reverse-mcp (32 tools)        │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Navigation│ Script   │Debugging │ Hooking  │  │
│  │          │ Analysis │          │          │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Network  │ JSVMP    │  Cookie  │  Verify  │  │
│  │ Capture  │ Analysis │ Storage  │  Signer  │  │
│  └──────────┴──────────┴──────────┴──────────┘  │
│                    ↕ Playwright API               │
├─────────────────────────────────────────────────┤
│      Camoufox (Anti-detection Firefox, Juggler)  │
│  C++ engine-level fingerprint spoofing           │
└─────────────────────────────────────────────────┘
```

---

## Changelog

### v1.0.0 (2026-04-18) — Streamline + Pure JS Reverse Toolkit

> **Major release**: 80 → 32 tools, schema tokens halved. Session/assertion system removed. Pure JS reverse engineering toolkit.

**Tool Merges (v0.9.0)**
- `network_capture(action)` ← start/stop_network_capture
- `scripts(action)` ← list_scripts / get_script_source / save_script
- `search_code(keyword, script_url)` ← search_code / search_code_in_script
- `hook_function(path, mode)` ← hook_function / trace_function
- `instrumentation(action)` ← instrument_jsvmp_source / get_instrumentation_log / stop_instrumentation / reload_with_hooks / get_instrumentation_status
- `cookies(action)` ← get_cookies / set_cookies / delete_cookies

**Removed**: Session archive (7 tools), assertion system (4 tools), 37 cold tools

**Added**: `verify_signer_offline` — stateless signer verification

**Bug Fixes (v0.8.1)**: evaluate_js multi-strategy JSON parse, navigate auto-clear network buffer, get_network_request max_body_size, launch_browser residual diagnostics

**Removed dependency**: `tldextract`

### v0.6.0 — Bug Fixes
### v0.5.0 — Signature-Based Anti-Bot Compatibility
### v0.4.0 — Universal JSVMP Adaptation
### v0.3.0 — Stability Fixes
### v0.2.0 — Hook Persistence + JSVMP Analysis
### v0.1.0 — Initial Release (44 tools)

---

## Feedback / Contact

Hit a bug, want a new hook preset, or just want to chat about JS reverse engineering? Add me on WeChat:

- **WeChat ID**: `han8888v8888`

> Please note "camoufox-reverse" in your friend request.

## License

MIT
