# camoufox-reverse-mcp

[中文](README.md) | [English](README_en.md)

> Anti-detection browser MCP server for JavaScript reverse engineering.

An MCP (Model Context Protocol) server that gives AI coding assistants (Claude Code, Cursor, Cline, etc.) the ability to perform JavaScript reverse engineering through the **Camoufox** anti-detection browser — including API parameter analysis, JS source analysis, dynamic debugging, function hooking, network interception, JSVMP bytecode analysis, and cookie/storage management.

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

---

## v1.8.0: Real Upstream Cases and Observation Semantics

- Real KProtect VM, javascript-obfuscator CFF, CryptoJS and FingerprintJS cases drive fixes and independent retesting through MCP stdio and local services.
- Source taps preserve evaluation order and exceptions, use primitive previews, and expose main-world/frame logs. Bundled Acorn adds optional local Node parsing; unsupported transformations are skipped conservatively.
- `evaluate_js(result_format="json_ascii")` returns explicit ASCII JSON text. Tag special numbers/undefined yourself; default `auto` keeps legacy cleanup.
- `hook_function(serialization="preview")` avoids inspecting user objects. `outcome`/`thrownValue` record synchronous throws; `completion="sync"` never claims Promise settlement. Default JSON serialization remains available and may invoke getters/toJSON.
- Routes use the current response and do not replay requests after rewrite failure. Native snapshot metadata identifies the active page/main frame, not the event window or event-time value.

See [release notes](docs/releases/v1.8.0.md), [actual validation and limits](docs/REAL_SOURCE_VALIDATION.md), and the companion Skill's [reproducible case CLI](https://github.com/WhiteNightShadow/hello_js_reverse_skill/tree/v3.9.0/scripts/real_cases). These are local simulations, not commercial anti-bot success claims.

## v1.7.0: Evidence and Diagnostics Validated by Independent Agents

- `compare_network_requests` and `save_response_body` preserve raw inputs and distinguish comparison hashes from entity-byte hashes.
- Task-scoped environment reviews preserve existing evidence; navigation keeps original failures and snapshots have explicit time/output limits.
- Three rounds of nine independent tasks retained failures and drove corrections. All final-round signer, WASM and pagination artifacts passed independent checks.

See [release notes](docs/releases/v1.7.0.md) and the [research and validation record](docs/RESEARCH_AND_VALIDATION.md), including limits.

## v1.6.0: General Capture and Standalone Verification

- Associate concurrent responses by Request identity, collect full cookie headers, and report failures, eviction, pending work and truncation explicitly.
- Optional `limit`/`after_id` pagination and `export_network_capture` JSON snapshots; existing list calls remain compatible. Export masks header/query values and omits bodies by default.
- Optional `verify_signer_offline(..., runtime="node")` uses an independent Node.js process. The browser runtime remains the default; empty assertions and missing keys no longer pass.
- Cookie name/domain filters now intersect. Older Playwright versions expire only selected cookies. Capture IDs remain monotonic until browser close.

See the [collection contract](docs/GENERAL_COLLECTION.md) and [Chinese release notes](docs/releases/v1.6.0.md) for limits, examples and validation.

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

> v1.1.0 pins the MCP Python SDK to the compatible v1 line and automatically
> normalizes optional-parameter schemas for strict providers such as
> Moonshot/Kimi that require a literal `type` on every tool property.

### Option 3: uvx (no clone, pin a version)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and git first, then run these once in a terminal:

```bash
uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0" camoufox-reverse-mcp --version
uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0" camoufox fetch
```

The first command downloads the dependencies and confirms the version, which keeps your client from timing out on the first start. The second downloads the Camoufox browser and its data, about 1.3 GB to download and about 2.5 GB unpacked on macOS. They go into your user cache, so clearing uvx environments does not remove them. If you skip this step, the first `launch_browser` downloads them and returns only when that finishes, about 4 minutes in our test, so your client may time out first.

Client configuration:

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0",
        "camoufox-reverse-mcp"
      ]
    }
  }
}
```

Put server options after `camoufox-reverse-mcp`, for example `"--headless"` or `"--proxy", "http://127.0.0.1:7890"`.

**Updating**: find the latest tag on [Releases](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases), change `@v1.9.0` in the configuration to it, and restart your client. A tag never moves on its own. Without `@v1.9.0`, every start follows the latest commit on main, which is not recommended for daily use. If `check_environment` reports a missing browser, run `camoufox fetch` again with the new tag.

You can also install a persistent command. Set `command` to `camoufox-reverse-mcp` and leave out `args`. Fetch the browser with the `uvx ... camoufox fetch` command above, because both setups share the same browser:

```bash
uv tool install "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0"
uv tool install --force "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@<new-tag>"
```

The second command updates it. `uv tool upgrade` does not move a pinned tag, so reinstall with `--force`.

Existing `pip install -e .` and `python -m camoufox_reverse_mcp` setups keep working and need no changes.

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

## Available Tools (36)

### Browser Control
| Tool | Description |
|------|-------------|
| `launch_browser` | Launch Camoufox; optionally select one installed version without changing active config |
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

---

## Changelog

### v1.5.2 (2026-09-07) — Legacy Main-World Channel Compatibility

- Probe the same Promise-returning contract used by real calls, detecting synchronous-only bridges
- Select the wrappedJSObject fallback before executing caller code, without replaying failed calls

### v1.5.1 (2026-09-07) — Trace Clearing and Hook Lifecycle Fixes

- Preserve target function behavior after clearing Trace data
- Probe main-world support before executing caller code; never replay it through another channel after execution fails
- Register persistent hooks for frames that do not exist yet and return an explicit `pending` status
- Restore hooks in current frames and execution worlds, and report locked properties that cannot be restored; no browser rebuild is required

### v1.5.0 (2026-09-04) — Main World, Frames, and Reliable Persistent Hooks

- Add explicit `world="main"` to `evaluate_js` and `hook_function` while preserving the isolated-world default
- Add frame metadata and `frame_url/frame_name/frame_index` targeting with explicit ambiguity errors
- Add bounded waiting and assignment watching for late-bound functions, including same-task first calls
- Restore `get_trace_data` with bounded, world/frame-scoped caches and fix persistent intercept registration
- Validate on Firefox 135, official Camoufox 152, reverse.5, and the real FeiLin reproduction without rebuilding the browser

### v1.4.1 (2026-09-03) — Firefox 152 LocalStorage Trace Path

- Pair with `camoufox-reverse` reverse.5, which moves the existing
  `localStorage.getItem/setItem` sites to Firefox 152's default LSNG `LSObject`
  path
- Expand the build-declared set to 77 sites while keeping protocol 1 and event
  object/property/kind fields unchanged; native sites distinguish LSObject and
  the separately reachable partitioned implementation
- Live parsing and aggregation remain unchanged, as do trace-off and
  135/reverse.3/reverse.4 compatibility. Historical JSONL has no build marker,
  so its hook count is now explicitly unknown instead of assumed to be 75

### v1.4.0 (2026-09-03) — Correct, Isolated, Interactive PropertyTracer

- Pair with the production `camoufox-reverse` reverse.4 build while preserving all 75 Firefox 135 paths and protocol-v1 core fields
- Decode correct get/set/call kinds plus native site, per-process sequence, and microsecond ordering extensions
- Add backward-compatible `start/stop/query/capture/clear/status` actions so page operations can run inside an explicit trace window
- Isolate every launch's controls, traces, values, and cleanup; reject stale controls and never operate on another MCP instance
- Fix the Windows fresh-window order to off → drain → cleanup → on
- Split `installed`, `trace_capable`, and `trace_active` environment status and negotiate capability metadata
- Treat `collect_values` as a safe post-trace snapshot and skip cookies or APIs that would create side effects
- Ignore unsupported tracing on official browsers identified by Camoufox 0.5 metadata without injecting config or disabling their sandbox; default launches remain unchanged
- Camoufox 0.4 official 135 and early markerless custom 135 builds are indistinguishable before launch; explicit tracing still attempts the legacy handshake for compatibility, while the default `enable_trace=False` path is unaffected

### v1.3.0 (2026-09-02) — Camoufox 152 and Non-Invasive Version Selection

- Add optional `browser_version` to select an installed Camoufox 0.5+ build for one launch without changing active config
- Require a repo-qualified, unambiguous selector and reject mismatched active/selected version builds
- Extend `check_environment` with Camoufox Python, active/installed selectors, capability markers, and legacy-cache migration warnings
- Fix browser launch on hosts using `C.UTF-8` instead of emitting invalid `locale="C"`
- Keep defaults unchanged; Camoufox 0.4.x + Firefox 135 users do not need to migrate, and official builds retain every non-PropertyTracer MCP tool
- Pair with the opt-in [Camoufox Reverse 152 beta.30](https://github.com/WhiteNightShadow/camoufox-reverse/releases) prerelease
- Thanks to [@dsaw1111](https://github.com/dsaw1111) for reporting the browser-version gap

### v1.2.0 (2026-08-11) — Generic Source-Site Mapping

- Add opt-in `include_source_site` to `instrumentation(action="install")`
- Let AST and regex tap events carry a stable content-addressed `site_id` and monotonic `seq`
- Return original script SHA-256, URL, character ranges, AST line/column sidecars, and `hot_functions` from `instrumentation(action="log")`
- Keep default tap event fields unchanged; arbitrary user AST scripts and single-VM PC/opcode guessing are intentionally out of scope
- Thanks to [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) for reporting the execution-site correlation gap and providing analysis materials

### v1.1.2 (2026-08-11) — Windows Engine Trace Configuration Fix

- Fix `enable_trace=True` returning `engine_trace_not_available` on Windows when Camoufox splits its JSON across `CAMOU_CONFIG_1..n`
- Reassemble the complete JSON before merging `propertyTrace`, then rebuild any number of contiguous platform-sized chunks while preserving the original fingerprint configuration
- Add regression coverage for numeric ordering, more than ten chunks, Unicode, chunk growth and shrinkage, invalid configs, and actual `camoufox.utils.get_env_vars()` output in simulated Windows mode
- For this issue, users who already have a property-tracing `camoufox-reverse` build only need to upgrade the MCP; the browser does not need to be rebuilt or replaced
- Thanks to [@Code-xy](https://github.com/Code-xy) for the report, Windows validation, root-cause analysis, and proposed direction

### v1.1.1 (2026-07-29) — AST Instrumentation Fix for Chained Calls

- Fix corrupted output caused by overlapping parent/child AST edit ranges in expressions such as `new X().m1().m2()` and `Array.prototype.slice.call(arguments)`
- Keep the outer instrumentation only when edit ranges overlap; existing behavior for ordinary member access and calls remains unchanged
- Expose `last_mode_used` from `instrumentation(action="status")` to distinguish AST, regex fallback, and oversized-file paths

### v1.1.0 (2026-07-29) — Engine Tracing, Browser Attach, and Schema Compatibility

> Stable release adding engine-level property tracing and attachment to an
> already running Camoufox browser, with stronger cross-provider compatibility.

**Added**
- `trace_property_access`, `list_trace_files`, and `query_trace_file`
- `launch_browser(ws_endpoint=...)` for attaching to a running browser

**Compatibility and stability**
- Normalize safe top-level optional parameter schemas for strict providers such as Moonshot/Kimi (contributed by [@tuntun1337](https://github.com/tuntun1337))
- Pin the MCP Python SDK to `mcp>=1.29,<2`; v2 migration will be handled separately
- Fix Windows Camoufox/Playwright import deadlock
- Fix Playwright Firefox driver `pageError` crashes
- Fix lost rewritten responses caused by stale `Content-Encoding`

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

## Community Contributors

- [@tuntun1337](https://github.com/tuntun1337) — strict JSON Schema compatibility
- [@Code-xy](https://github.com/Code-xy) — Windows engine-trace diagnosis and validation
- [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) — JSVMP execution-site requirements and analysis materials
- [@dsaw1111](https://github.com/dsaw1111) — Camoufox 152 version-gap and PropertyTracer upgrade request

## Feedback / Contact

Hit a bug, want a new hook preset, or just want to chat about JS reverse engineering? Add me on WeChat:

- **WeChat ID**: `han8888v8888`

> Please note "camoufox-reverse" in your friend request.

## License

MIT
