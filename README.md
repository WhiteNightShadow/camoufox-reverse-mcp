# camoufox-reverse-mcp

[English](README.md) | [中文](README_zh.md)

> Anti-detection browser MCP server for JavaScript reverse engineering.

An MCP (Model Context Protocol) server that gives AI coding assistants (Claude Code, Cursor, Cline, etc.) the ability to perform JavaScript reverse engineering through the **Camoufox** anti-detection browser — including API parameter analysis, JS source analysis, dynamic debugging, function hooking, network interception, and cookie/storage management.

## Why Camoufox?

| Feature | chrome-devtools-mcp | **camoufox-reverse-mcp** |
|---------|--------------------|-----------------------|
| Browser Engine | Chrome (Puppeteer) | **Firefox (Camoufox)** |
| Anti-Detection | None | **C++ engine-level fingerprint spoofing** |
| Debug Capability | Limited (no breakpoints) | **Playwright + JS Hook** |

**Core Advantages:**
- Camoufox modifies fingerprint information at the **C++ engine level**, not JS patches
- Juggler protocol sandbox isolation makes Playwright **completely undetectable** by page JS
- BrowserForge generates fingerprints based on **real-world traffic distribution**
- Works on sites with strong bot detection: Rui Shu, GeeTest, Cloudflare, etc.

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

## Available Tools (44)

### Navigation & Page
- `launch_browser` — Launch Camoufox with anti-detection config
- `close_browser` — Close browser and release resources
- `navigate` — Navigate to URL
- `reload` / `go_back` — Reload / go back
- `take_screenshot` / `take_snapshot` — Screenshot / accessibility tree
- `click` / `type_text` — Click / type into elements
- `wait_for` — Wait for element or URL pattern
- `get_page_info` — Get current page info

### Script Analysis (Reverse Engineering Core)
- `list_scripts` — List all loaded JS scripts
- `get_script_source` — Get full JS source code
- `search_code` — Search keyword across all scripts
- `save_script` — Save JS file locally
- `get_page_html` — Get page HTML

### Debugging (Reverse Engineering Core)
- `evaluate_js` — Execute arbitrary JS in page context
- `evaluate_js_handle` — Execute JS and inspect complex objects
- `add_init_script` — Inject scripts that run before page JS
- `set_breakpoint_via_hook` — Set pseudo-breakpoints via JS hooks
- `get_breakpoint_data` — Get captured breakpoint data
- `get_console_logs` — Get page console output

### Hooking (Reverse Engineering Core)
- `trace_function` — Trace function calls without pausing
- `get_trace_data` — Get collected trace data
- `hook_function` — Inject custom hook code (before/after/replace)
- `inject_hook_preset` — One-click preset hooks (xhr/fetch/crypto/websocket/debugger_bypass)
- `remove_hooks` — Remove all hooks

### Network Analysis (Reverse Engineering Core)
- `start_network_capture` / `stop_network_capture` — Capture network traffic
- `list_network_requests` — List captured requests with filters
- `get_network_request` — Get full request details
- `get_request_initiator` — Get JS call stack that initiated a request
- `intercept_request` — Intercept: log / block / modify / mock
- `stop_intercept` — Stop interception

### Storage Management
- `get_cookies` / `set_cookies` / `delete_cookies` — Cookie management
- `get_storage` / `set_storage` — localStorage / sessionStorage
- `export_state` / `import_state` — Save / restore browser state

### Fingerprint & Anti-Detection
- `get_fingerprint_info` — Inspect current browser fingerprint
- `check_detection` — Test anti-detection on bot detection sites
- `bypass_debugger_trap` — Bypass anti-debugging traps

## License

MIT
