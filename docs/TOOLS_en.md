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
| `trace_session(action)` | Playwright trace recording: `start` (screenshots/snapshots/sources), `stop` writes a `.zip` to open in trace.playwright.dev or `playwright show-trace`, `status` reports state. Records the default browser context; relaunching the browser loses an unfinished recording |

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
| `inject_hook_preset` | One-click preset hooks (xhr / fetch / crypto / websocket / debugger_bypass / cookie / runtime_probe) in the page main world, so the page's own requests and calls are recorded; read a log as the result's `read_with` shows, e.g. `evaluate_js("window.__mcp_xhr_log \|\| []", world="main")` |
| `remove_hooks` | Remove all hooks and restore original objects (presets and JSVMP probes included) |
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
| `hook_jsvmp_interpreter` | JSVMP runtime probe in the page main world (`mode="proxy"` full coverage / `mode="transparent"` replaces getters only, fewer changes); read the log with `evaluate_js("window.__mcp_jsvmp_log \|\| []", world="main")`. The result's `coverage` lists what the probe records and what it cannot see, so a missing entry means "not observed", not "not called"; events from same-origin iframes (including about:blank ones the page creates and removes) go to the top frame's log with `frame_url`/`frame_depth` |
| `instrumentation(action)` | Source-level instrumentation: `install` / `log` (slice with `after_seq`, `seq_window`, `stage`) / `stop` / `reload` / `status` (with files passed through unchanged) / `rewrite_source` (rewrite local source without a browser) |
| `stage_break(action)` | State-level deep capture for `tap_stage_calls`: `set` records a bounded deep snapshot for one state `p` (`args_only`/`args_ret`/`args_ret_stack`), `clear` disarms, `list` shows hit counters; emits `tap_stage_break` events. Armed states last for the MCP session and ship with every script rewritten afterwards, so states that run during page load are captured after a reload; snapshots read own data properties and never call getters |
| `compare_env` | Collect browser env fingerprint for Node.js/jsdom comparison; with `output_path`, export a full environment bundle (navigator, screen, window, CSS, canvas, WebGL, audio, storage, iframe, timing) |

By default only member reads are rewritten (`rewrite_member_access=True`,
`rewrite_calls=False`). Call taps run every call through MCP helper functions:
the callee reads `caller` as null, the maximum recursion depth drops and stacks
gain frames, so fingerprinting code can compute different values. Turn them on
when `tap_call`/`tap_method` events are needed.

Scripts larger than `max_file_size` (default 2 MB) follow `on_oversized`; by
default they pass through unchanged unless property filters are set. `status`
and `log` list them in `skipped_files` with URL, size, reason and a suggested
`max_file_size`; files that hit `max_rewrites` are listed in `capped_files`.

Every event has a `seq`, numbered across the page and its same-origin child
frames; events recorded while a `tap_stage_calls` state runs carry it as
`stage` (the innermost state). `log` pages forward with `after_seq` (returns
`next_after_seq`), slices with `seq_window=[start, end]` and selects a state's
events with `stage`; `dropped` counts events lost after the log reached 20000
entries. Rewritten scripts in same-origin child frames log into the top frame
with `frame_url`/`frame_depth`, so the events stay after the page removes the iframe.

`instrumentation(action="rewrite_source")` needs no browser: it applies the same
rewrite to `source` (text) or `source_path` (a file) and writes `output_path`
(required when the result exceeds ~100 KB). The output runs in Node, vm and
jsdom too, and events collect in `globalThis.__mcp_vmp_log`.

Set `include_source_site=True` when browser and sandbox traces need stable
execution-site alignment. Events receive a content-addressed `site_id`;
`log` returns a `source_sites` sidecar for the intercepted script's original
character ranges (`rewrite_source` writes `<output_path>.sites.json`). This is
off by default. It does not guess a VM PC, opcode, or pre-protection source location.

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

### Firefox RDP Debugging

> **Why this group exists**: camoufox is Firefox, and the Playwright side runs on
> Juggler with no CDP, so Playwright cannot stop on a line, read locals, or step.
> This tool drives Firefox's native RDP (`-start-debugger-server`) directly and
> fills that gap — no Playwright, no browser extension.
>
> **Applies to**: the Firefox 140+ devtools protocol. The development machine runs
> camoufox 152 / reverse.5, and both the smoke test and `--self-test` really run
> against that build; stock Firefox was not exercised on this machine (whether the
> port opens depends only on the two prefs below, not on the build).

> **Launch path (shared with the server's launch_browser)**: `launch` does not
> assemble its own command line. It reuses `camoufox.pkgman.launch_path()` to
> resolve the **currently active** build (on macOS that is
> `Camoufox.app/Contents/MacOS/camoufox`; it never falls back to "newest mtime"),
> and it inherits `BrowserManager.default_config`: the `CAMOU_CONFIG` fingerprint,
> the proxy, `--headless`, `--os`, `--locale`. The temporary profile gets a
> `user.js` containing at least `devtools.debugger.remote-enabled=true`,
> `devtools.chrome.enabled=true` and `devtools.debugger.prompt-connection=false`,
> because `DevToolsStartup` only opens the debug port when the first two are true
> (stock builds default `devtools.chrome.enabled` to false; an empty profile with
> `MOZ_HEADLESS=1` or `-headless` never opens the port).
>
> **Not inherited** (stated, not silently degraded): proxy authentication
> (`username`/`password` needs an interactive prompt, so requests through an
> authenticated proxy fail instead of connecting directly and leaking the real
> IP), `headless="virtual"` (no Xvfb wrapper here, treated as plain headless), and
> the Playwright attach modes `browser_version` / `ws_endpoint`. When `camoufox`
> itself is unavailable the launch degrades to a bare profile and says so in the
> returned `warnings`.

> **Security**: the debug port has no authentication, and opening it requires
> `devtools.chrome.enabled=true`. That means **anyone who can connect to the port
> can debug the browser's main process, i.e. execute arbitrary code as the current
> OS user**, and other users on the same machine can connect to `127.0.0.1:<port>`
> too. Therefore: the default port is a random free port on loopback only; on
> macOS/Linux `unix_socket=true` puts the debugger behind a Unix socket inside a
> 0700 temp directory (current user only); a busy port reports who holds it (this
> session's browser still exiting, or another process) and suggests `port=0`; the
> tool only kills processes it started, `atexit` + signal handlers close its own
> browser when the MCP process exits, and a repeated `launch` closes the previous
> session first.

| Tool | Description |
|------|-------------|
| `rdp_debugger` | One tool with `action` dispatch (20 actions). Every blocking action runs inside `asyncio.to_thread`, so it never stalls the MCP event loop or Playwright's browser events; calls on one session are serialized, so concurrent calls cannot swap results, and cancelling a request makes the worker exit |

`action` reference:

| action | What it does |
|---|---|
| launch | Start a browser with a debug port (random free port by default; inherits the server config; closes a previous session first) |
| attach | Connect to an already running debug port (`port`, or `socket_path` on macOS/Linux) |
| enable | `listTabs -> getTarget -> threadActor`, then attach the thread; detaches the previous target first and never jumps back to the first tab |
| sources | List known scripts (with `sourceStartLine` / `sourceLength`), filterable by `url_substr` |
| source | Fetch full script source; longString chunks advance by UTF-16 code units and `truncated` is explicit past `max_chars` |
| breakable_lines | Breakable line numbers of a script |
| breakable_positions | Breakable positions of a script (or one line): `columns_0based` / `columns_1based` |
| set_breakpoint | Set a breakpoint: `source_url` uses `sourceUrl` (inline scripts and post-navigation sources get it automatically), `source` uses `sourceId` (eval / `new Function`); `column` is 1-based and snaps to the nearest breakable column; a line without breakable positions returns `installed: false` |
| remove_breakpoint | Remove line breakpoints using the exact location they were set with; pass `line`/`source`/`source_url`/`url_substr`, or `remove_all=true` to clear everything |
| wait_paused | Wait for the next pause; only accepts a `paused` that arrived after `resume`/`step`/`interrupt`; returns frame / where / why |
| frames | Full call stack of the current pause; returns `{"error": "wrongState"}` when not paused |
| scopes | Recursive scope chain and bindings of one frame; requires `frame_actor` |
| evaluate | With `frame_actor`, evaluate in that paused frame (locals readable); otherwise global page eval. A breakpoint hit returns `paused-while-evaluating` immediately instead of timing out; longString results are materialized |
| step | Step `over` / `into` / `out` (a bad direction errors out); `wrongState` is not reported as stepped |
| resume | Resume execution |
| interrupt | Pause without a line breakpoint (single-line JSVMP). `on_next=true` sends the string `when="onNext"` and pauses on the next statement |
| reload | Reload and rebuild the session: detaches the old target first (otherwise xhr breakpoints on the old thread freeze the page), polls until the new threadActor exists, then attaches and re-applies every breakpoint without a fixed sleep. Breakpoints set by `sourceId` only (eval / `new Function`) cannot survive navigation and are reported in `reapply_skipped` |
| xhr_breakpoint | Pause when a request is about to be sent; matches fetch and XHR; `remove=true` removes it with the exact path+method it was set with |
| status | Session state (port / unix socket, tab, target, thread, last pause reason, breakpoints) |
| close | End the session and close the browser this session launched |

Coordinates and verified gotchas:

- **Lines are 1-based**; `breakable_lines` / `breakable_positions` report exactly the
  coordinate space breakpoints use: inline scripts are in **HTML document lines**
  (`sourceStartLine` is the `<script>` line, breakable lines start on the next one),
  while `eval` / `new Function` use their own source line numbering.
- **Columns are 0-based and must land on a breakable position** (the server looks up
  `column + columnBase` exactly), while `Error.stack` and the DevTools UI are
  1-based. The tool's `column` argument is 1-based, subtracts one internally, snaps
  to the nearest breakable column and reports `snapped_from_column_1based` /
  `breakable_columns_0based`. For minified single-line files and JSVMP, look at
  `breakable_positions` first.
- With `location.sourceUrl`, Firefox keys the breakpoint by URL, so the second and
  later inline scripts of the same HTML document — and sources created after a
  reload/navigation — get it applied automatically. Removal is keyed by
  `sourceUrl:sourceId:line:column`, so `remove_breakpoint` re-sends the original
  location verbatim. Conversely, a breakpoint set by `source` (sourceId only) is
  bound to one source actor that disappears on navigation; `reload` reports those in
  `reapply_skipped` — use `source_url` for anything that must survive a reload.
- `thread.attach` must carry `options:{}`, otherwise the thread stays DETACHED,
  `sources` stays 0 and nothing is reported.
- `setXHRBreakpoint` requires `method` to be `"ANY"` or a real verb; an empty string
  never matches. Removal is an exact path+method match too, so `"remove"` cannot be
  used as the method (the tool re-sends the pair recorded at set time).
- Navigation recreates target/thread actors and the old target must be detached;
  `reload` does both.
- Long strings (script source, big evaluation results) arrive as a `longString` grip
  (`{type:"longString", actor, length}`); the body must be fetched with `substring`
  in UTF-16 code units. `source` and `evaluate` fetch it for you and set
  `truncated: true` past `max_chars`.
- Concurrency: calls on one session are serialized by a lock, and when the client
  cancels a request the worker thread exits instead of continuing to read the same
  socket.


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
5. instrumentation(action="install", url_pattern="**/vmp_target*.js", mode="ast")  ← reads only by default
6. inject_hook_preset("cookie", persistent=True)
7. instrumentation(action="reload")               ← Activate instrumentation
8. instrumentation(action="log", type_filter="tap_get")  ← See env reads
9. For API calls: install with rewrite_calls=True, reload, then type_filter="tap_method"
10. compare_env(output_path="env.json")            ← Export an env bundle for Node.js
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
5. One stretch only: instrumentation(action="log", seq_window=[590, 610]);
   what one state read: instrumentation(action="log", stage=<p>);
   a state's full arguments: stage_break(action="set", p=<p>), then instrumentation(action="reload")
6. Offline or for many runs: instrumentation(action="rewrite_source", source_path="sdk.js",
   output_path="sdk.tapped.js", tap_stage_calls=True), then compare in local emulation
```

`tap_stage_calls` is a literal match: it is independent of `mode`/`filter_*` and still
applies when the file cannot be parsed (`last_mode_used` in `instrumentation(action="status")`
then ends with `(stage taps only)` and `last_error` holds the parse error). Events are
recorded when a state returns, so nested states come first; a state that throws is not recorded.

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
