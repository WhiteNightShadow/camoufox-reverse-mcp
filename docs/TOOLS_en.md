# Tool Reference

[中文](TOOLS.md) | [English](TOOLS_en.md)

39 tools in 9 groups. See the [README](../README_en.md) for installation and server options.

- [Available Tools](#available-tools-39)
- [Usage Scenarios](#usage-scenarios)
- [Architecture](#architecture)
- [Why Camoufox?](#why-camoufox)

## Available Tools (39)

### Browser Control
| Tool | Description |
|------|-------------|
| `launch_browser` | Launch Camoufox; arguments left out use the server options; optionally select one installed version without changing active config |
| `close_browser` | Close browser and release resources |
| `navigate` | Navigate to URL (supports pre_inject_hooks, redirect_chain tracking) |
| `reload` | Reload current page |
| `take_screenshot` | Screenshot (full page or specific element) |
| `take_snapshot` | Get accessibility tree (token-efficient) |
| `click` / `type_text` | Click element / type text |
| `wait_for` | Wait for element or URL pattern |
| `get_page_info` | Get current page URL, title, viewport, and frame list |

### JS Execution & Debugging
| Tool | Description |
|------|-------------|
| `evaluate_js` | Evaluate JS in the compatible isolated world or an explicit page main world/frame |

### Script Analysis
| Tool | Description |
|------|-------------|
| `scripts(action)` | Script management: `list` / `get` source / `save` to local file |
| `search_code` | Search keyword (`script_url=None` for all scripts, or specify URL for single-script with auto char-mode for minified files) |

### Hooking & Tracing
| Tool | Description |
|------|-------------|
| `hook_function` | Hook or trace a function with main-world, frame, persistence, and late-target support |
| `get_trace_data` | Read or clear function traces filtered by world and frame |
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
| `export_network_capture` | Export a versioned JSON snapshot; masks header and query values and omits bodies by default, never overwrites a file |
| `compare_network_requests` | Compare 2 to 10 captured requests without sending requests or launching a browser |
| `save_response_body` | Save captured response bytes (JS / WASM / JSON / binary) without refetching, never overwrites a file |

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
| `hook_jsvmp_interpreter` | JSVMP runtime probe in the page main world (`mode="proxy"` full coverage / `mode="transparent"` replaces getters only, fewer changes); read the log with `evaluate_js("window.__mcp_jsvmp_log \|\| []", world="main")` |
| `instrumentation(action)` | Source-level instrumentation: `install` / `log` / `stop` / `reload` / `status` |
| `compare_env` | Collect browser env fingerprint for Node.js/jsdom comparison |

Set `include_source_site=True` when browser and sandbox traces need stable
execution-site alignment. Events receive a content-addressed `site_id` and a
monotonic `seq`; `log` returns a `source_sites` sidecar for the intercepted
script's original character ranges. This is off by default. It does not guess a
VM PC, opcode, or pre-protection source location.
For scripts over 200KB that require a full rewrite, explicitly set
`on_oversized="force"`; otherwise use property filters and disable
`rewrite_calls` when call tracing is unnecessary.

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
| `check_environment` | MCP/dependency checks plus Camoufox Python, active and installed-version diagnostics |
| `reset_browser_state` | Clear residuals (hooks / capture / routes / current engine trace) without closing browser |

Starting with v1.3.0, Camoufox Python 0.5+ can keep the official and reverse
builds side by side without changing the persistent active browser:

```text
check_environment()
launch_browser(
  browser_version="whitenightshadow/152.0.4-beta.30-reverse.5",
  enable_trace=True
)
```

`browser_version` must be repo-qualified, and an exact folder is required when
multiple assets share a version. Omitting it preserves v1.2.0 behavior,
including Camoufox 0.4.x flat-cache installations. The selected and active
browsers must share the exact version/build to prevent upstream resource mixing. The MCP never downloads a
browser, changes `config.json`, or initiates cache migration.

### Native PropertyTracer

Reverse.5 covers 77 fingerprint-relevant Gecko DOM/Web API native
sites without rewriting page JavaScript objects, descriptors, or prototypes.
A hit is strong evidence; a miss is not proof that an unhooked property was not
used. High event rates can still create a timing side channel.

| Tool | Description |
|------|-------------|
| `trace_property_access` | `action=capture/start/stop/query/clear/status`; summary/timeline/sequence/search; get/set/call, object, keyword, and native-site filters |
| `list_trace_files` | List historical files across isolated trace-run directories |
| `query_trace_file` | Query one PropertyTracer JSONL inside the trace cache |

Each traced launch gets a private run directory, so controls and cleanup never
touch another MCP instance. `collect_values=True` is a conservative post-trace
snapshot, not the value at event time; sensitive and side-effectful paths are
reported in `values_skipped`. Engine tracing temporarily disables the Firefox
content sandbox so content processes can write the private trace; normal
launches retain the upstream sandbox.

Interactive capture:

```text
trace_property_access(action="start")
# perform click/evaluate/navigation actions
trace_property_access(action="stop", mode="summary")
```

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
9. hook_function("window.getSign", mode="trace", world="main", persistent=True)
10. reload() → get_trace_data("window.getSign", world="main") ← Collect trace data
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

### Scenario 2b: Stage comparison for control-flow-flattened VMPs

Turn each dispatcher call into a comparable event stream, so a real browser run and
a locally emulated run can be aligned state by state:

```
1. instrumentation(action="install", url_pattern="**/security-js/*.js",
                   tap_stage_calls=True, tag="vmp")   ← rewrite return fn.apply(this,[p].concat(...))
2. navigate("https://target-site.com/")               ← install must come before navigate
3. instrumentation(action="log", type_filter="tap_stage")
   → summary.hot_stages counts every state `p`; entries carry argc / args / ret previews
4. Repeat with the same seed on the other side (local env emulation) and align by p sequence
```

`tap_stage_calls` is a literal match: it is independent of `mode`/`filter_*` and still
applies when the file cannot be parsed (`stats.stage_only=true` means stage taps only).

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

> 👉 Full anti-bot type identification and workflow guide: [JSVMP_PLAYBOOK.md](JSVMP_PLAYBOOK.md)

## Architecture

```
┌─────────────────────────────────────────────────┐
│           AI Coding Assistant (Cursor / Claude)  │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│              camoufox-reverse-mcp                │
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

## Why Camoufox?

| Capability | Implementation |
|---|---|
| Browser | Firefox / Camoufox |
| Runtime investigation | Explicit execution worlds, frames, hooks and network capture |
| JSVMP investigation | Runtime probes and source instrumentation, validated per target |
| Persistent hooks | Context initialization with explicit pending/removal boundaries |

**Core Advantages:**
- Camoufox modifies fingerprint information at the **C++ engine level**, avoiding common JS descriptor/prototype patch artifacts
- Juggler protocol isolation reduces page-world automation artifacts
- BrowserForge generates fingerprints based on **real-world traffic distribution**
- Supports investigation of protected applications; success must be verified for each target and is not guaranteed by the browser choice
- Optional hook override protection uses `Object.defineProperty`; locked properties may require a new context to restore
