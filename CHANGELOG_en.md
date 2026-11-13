# Changelog

[中文](CHANGELOG.md) | [English](CHANGELOG_en.md)

Full notes for v1.1.1 and later are in [docs/releases](docs/releases) (Chinese). Packages are on [Releases](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases).

## Unreleased — Firefox RDP debugging

- New `rdp_debugger(action=...)` driving Firefox's native RDP (`-start-debugger-server`) to add real breakpoints, stepping, call stacks, scopes, paused-frame evaluation and request breakpoints. Firefox has no CDP and the Playwright side can only replay after the fact, so this covers "stop on a line and read locals"; `action` accepts `launch`, `attach`, `enable`, `sources`, `source`, `breakable_lines`, `breakable_positions`, `set_breakpoint`, `remove_breakpoint`, `wait_paused`, `frames`, `scopes`, `evaluate`, `step`, `resume`, `interrupt`, `reload`, `xhr_breakpoint`, `status`, `close`
- `evaluate` drives page logic without pausing (clicking, calling page functions), so Playwright interaction is no longer required for that; `xhr_breakpoint` pauses right before a request is sent and matches both `fetch` and XHR, which is the practical way to capture payloads built inside obfuscated code
- The tool is `async def`; socket I/O and waits run inside `asyncio.to_thread`, so the MCP event loop and Playwright's browser events never stall. Calls on one session are serialized by a lock (concurrent calls no longer swap results), and cancelling a request makes the worker thread exit
- Launching now shares the `launch_browser` configuration: the executable comes from `camoufox.pkgman.launch_path()` for the **currently active** build (on macOS that is `Camoufox.app/Contents/MacOS/camoufox`; no more "newest mtime"), and the browser inherits the `CAMOU_CONFIG` fingerprint, the proxy, `--headless`, `--os` and `--locale`. The temporary profile gets a `user.js` (`devtools.debugger.remote-enabled`, `devtools.chrome.enabled`, `devtools.debugger.prompt-connection`) — without it `-start-debugger-server` never opens the port. The default port is now a random free port, a repeated `launch`/`attach` closes the previous session, `atexit` plus signal handlers clean up on MCP exit, and macOS/Linux can use a Unix socket inside a 0700 temp directory
- Message dispatch fixed: after `resume`/`step`/`interrupt` only a newly arrived `paused` counts (a buffered one is never treated as new); `call()` matches replies per actor queue, events pushed by the same actor are no longer taken as replies, and error replies are classified by the request type named in their `message`; `drain()` has a total budget and the event buffer is capped, so a `newSource` flood can neither grow unbounded nor block requests
- Breakpoints fixed: locations now use `location.sourceUrl` (the second and later inline scripts of one HTML document, and sources created after navigation, get them automatically) and a source actor can be passed directly for `eval` / `new Function`. `remove_breakpoint` deletes with the exact original location; `xhr_breakpoint` removes with the path+method it was set with (`"remove"` is no longer sent as a method); new `breakable_positions` lists breakable columns, `column` is accepted 1-based, converted to 0-based and snapped, and a line without breakable positions returns `installed: false` instead of ok; `reload` detaches the old target and re-applies breakpoints while the page is still loading
- The tool layer no longer turns errors into success: `frames` / `scopes` / `step` / `evaluate` return `{"error": ...}` for `wrongState`, `noSuchActor` and friends; an expression that hits a breakpoint returns `paused-while-evaluating` immediately instead of timing out with a pile of `null`; `source` fetches long scripts in UTF-16 code units (emoji cannot shift or split a character) and flags `max_chars` truncation; a bad `direction` on `step` errors out instead of raising `KeyError`
- `--self-test` now uses `about:blank` plus an injected script instead of a third-party site; both the smoke test and the self-test really run against camoufox 152 reverse.5 on the development machine, while **stock Firefox was not exercised there** (opening the port depends only on the two prefs above); protocol unit tests cover framing, reply matching, serialized concurrency, cancellation, fresh-`paused` filtering, `newSource` floods, emoji longStrings, breakpoint install/snap/remove, xhr removal, reload re-application, launch-config inheritance and macOS path resolution

## v1.10.0 (2026-11-12) — Instrumentation Defaults, Offline Rewrite, Iframe Logs, Presets in the Main World

- `instrumentation`: `rewrite_calls` is now off by default (call taps change `caller` and the recursion depth), and `max_file_size` defaults to 2 MB instead of 200 KB. Files passed through unchanged or capped at `max_rewrites` are listed with reasons and suggested values. New `action="rewrite_source"` rewrites local source without a browser. Every event has a `seq`, events inside a flattened-VMP state carry `stage`, and `log` slices with `after_seq`, `seq_window` and `stage` and reports `dropped` (#19 #20 #21 #25)
- Probe and instrumentation events from same-origin iframes go to the top frame's log with `frame_url`/`frame_depth`; `hook_jsvmp_interpreter` returns `coverage`, listing what the probe records and what it cannot see (#23 #24)
- Presets from `inject_hook_preset` and `pre_inject_hooks` run in the page main world and record the page's own XHR, fetch and cookie access; `remove_hooks` restores them and `get_request_initiator` reads their main-world logs
- `compare_env` fills canvas, WebGL and audio and exports a full environment bundle with `output_path` (#22); adds `stage_break` and `trace_session` (#26), 41 tools in total
- Thanks to [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) for the reports and contributions

See the [Chinese release notes](docs/releases/v1.10.0.md).

## v1.9.4 (2026-11-10) — Stage Taps for Flattened VMPs

- `instrumentation(action="install")` adds `tap_stage_calls` (off by default). It rewrites the dispatcher calls of control-flow-flattened JSVMPs into per-state `tap_stage` events without changing call results; `action="log"` adds `hot_stages` counts and can filter by state id
- Stage taps still apply when a file cannot be parsed, and `last_mode_used` in the status then shows `ast (stage taps only)`. When a file parses, only real return statements are rewritten. With `include_source_site`, stage and regular tap sites point into the original script
- Thanks to [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) for contributing the feature (#18)

See the [Chinese release notes](docs/releases/v1.9.4.md).

## v1.9.3 (2026-11-10) — JSVMP Probe Fix

- The probes from `hook_jsvmp_interpreter` and `navigate(pre_inject_hooks=["jsvmp_probe"])` now run in the page main world. They used to run in the isolated world and recorded nothing from page scripts. Read the log with `evaluate_js("window.__mcp_jsvmp_log || []", world="main")`
- Proxy mode no longer recurses forever: after wrapping `Function.prototype.call/apply/bind`, the probe calls through a saved `Reflect.apply`. Before, any later JS in that world failed with too much recursion, and without `script_url` tool calls hung until they timed out
- `remove_hooks` fully restores the functions, getters and globals the probes replaced. The probes' own globals are no longer enumerable, and the install banner is printed only at document start or returned by the tool, so page code that wraps console methods never sees it

See the [Chinese release notes](docs/releases/v1.9.3.md).

## v1.9.2 (2026-11-10) — JSVMP Probe Escaping Fix

- `hook_jsvmp_interpreter` writes `script_url` into the hook with `json.dumps`, so quotes, backslashes and line breaks stay inside the string and can no longer run as code. Values with a backslash or line break used to be changed or stop the hook from installing
- The comment at the top of each template no longer receives `script_url`, so `*/` in the value can no longer end the comment
- `max_entries` is converted to an integer before it goes into the hook
- Thanks to [@tunguyen1993](https://github.com/tunguyen1993) for reporting the problem and suggesting the fix

See the [Chinese release notes](docs/releases/v1.9.2.md).

## v1.9.1 (2026-11-06) — Server Options Fix

- `launch_browser` no longer overrides server options: `--headless`, `--os`, `--locale`, `--geoip`, `--humanize`, `--block-images` and `--block-webrtc` now apply unless the call passes its own value
- README rebuilt, with version history moved to `CHANGELOG_en.md` and tool details to `docs/TOOLS_en.md`
- Added the MIT `LICENSE` file

See the [Chinese release notes](docs/releases/v1.9.1.md).

## v1.9.0 (2026-11-05) — uvx Install Support

- Install and start with `uvx` at a pinned tag, without cloning; adds `--version`
- Text that third-party libraries write to `stdout` now goes to `stderr`, so the download notice for a missing browser no longer enters the MCP protocol stream
- The Playwright startup patch writes a temporary file and replaces atomically, so it no longer changes files shared through uv's global cache
- `check_environment` prints the `camoufox fetch` command when no browser is available
- Thanks to [@chung1912](https://github.com/chung1912) for suggesting `uvx` support and sharing a working `uv tool install` setup

See the [Chinese release notes](docs/releases/v1.9.0.md).

## v1.8.0 (2026-09-08) — Real Upstream Cases and Observation Semantics

- Real KProtect VM, javascript-obfuscator CFF, CryptoJS and FingerprintJS cases drive fixes and independent retesting through MCP stdio and local services.
- Source taps preserve evaluation order and exceptions, use primitive previews, and expose main-world/frame logs. Bundled Acorn adds optional local Node parsing; unsupported transformations are skipped conservatively.
- `evaluate_js(result_format="json_ascii")` returns explicit ASCII JSON text. Tag special numbers/undefined yourself; default `auto` keeps legacy cleanup.
- `hook_function(serialization="preview")` avoids inspecting user objects. `outcome`/`thrownValue` record synchronous throws; `completion="sync"` never claims Promise settlement. Default JSON serialization remains available and may invoke getters/toJSON.
- Routes use the current response and do not replay requests after rewrite failure. Native snapshot metadata identifies the active page/main frame, not the event window or event-time value.

See [release notes](docs/releases/v1.8.0.md), [actual validation and limits](docs/REAL_SOURCE_VALIDATION.md), and the companion Skill's [reproducible case CLI](https://github.com/WhiteNightShadow/hello_js_reverse_skill/tree/v3.9.0/scripts/real_cases). These are local simulations, not commercial anti-bot success claims.

## v1.7.0 (2026-09-08) — Evidence and Diagnostics Validated by Independent Agents

- `compare_network_requests` and `save_response_body` preserve raw inputs and distinguish comparison hashes from entity-byte hashes.
- Task-scoped environment reviews preserve existing evidence; navigation keeps original failures and snapshots have explicit time/output limits.
- Three rounds of nine independent tasks retained failures and drove corrections. All final-round signer, WASM and pagination artifacts passed independent checks.

See [release notes](docs/releases/v1.7.0.md) and the [research and validation record](docs/RESEARCH_AND_VALIDATION.md), including limits.

## v1.6.0 (2026-09-07) — General Capture and Standalone Verification

- Associate concurrent responses by Request identity, collect full cookie headers, and report failures, eviction, pending work and truncation explicitly.
- Optional `limit`/`after_id` pagination and `export_network_capture` JSON snapshots; existing list calls remain compatible. Export masks header/query values and omits bodies by default.
- Optional `verify_signer_offline(..., runtime="node")` uses an independent Node.js process. The browser runtime remains the default; empty assertions and missing keys no longer pass.
- Cookie name/domain filters now intersect. Older Playwright versions expire only selected cookies. Capture IDs remain monotonic until browser close.

See the [collection contract](docs/GENERAL_COLLECTION.md) and [Chinese release notes](docs/releases/v1.6.0.md) for limits, examples and validation.

## v1.5.2 (2026-09-07) — Legacy Main-World Channel Compatibility

- Probe the same Promise-returning contract used by real calls, detecting synchronous-only bridges
- Select the wrappedJSObject fallback before executing caller code, without replaying failed calls

## v1.5.1 (2026-09-07) — Trace Clearing and Hook Lifecycle Fixes

- Preserve target function behavior after clearing Trace data
- Probe main-world support before executing caller code; never replay it through another channel after execution fails
- Register persistent hooks for frames that do not exist yet and return an explicit `pending` status
- Restore hooks in current frames and execution worlds, and report locked properties that cannot be restored; no browser rebuild is required

## v1.5.0 (2026-09-04) — Main World, Frames, and Reliable Persistent Hooks

- Add explicit `world="main"` to `evaluate_js` and `hook_function` while preserving the isolated-world default
- Add frame metadata and `frame_url/frame_name/frame_index` targeting with explicit ambiguity errors
- Add bounded waiting and assignment watching for late-bound functions, including same-task first calls
- Restore `get_trace_data` with bounded, world/frame-scoped caches and fix persistent intercept registration
- Validate on Firefox 135, official Camoufox 152, reverse.5, and the real FeiLin reproduction without rebuilding the browser

## v1.4.1 (2026-09-03) — Firefox 152 LocalStorage Trace Path

- Pair with `camoufox-reverse` reverse.5, which moves the existing
  `localStorage.getItem/setItem` sites to Firefox 152's default LSNG `LSObject`
  path
- Expand the build-declared set to 77 sites while keeping protocol 1 and event
  object/property/kind fields unchanged; native sites distinguish LSObject and
  the separately reachable partitioned implementation
- Live parsing and aggregation remain unchanged, as do trace-off and
  135/reverse.3/reverse.4 compatibility. Historical JSONL has no build marker,
  so its hook count is now explicitly unknown instead of assumed to be 75

## v1.4.0 (2026-09-03) — Correct, Isolated, Interactive PropertyTracer

- Pair with the production `camoufox-reverse` reverse.4 build while preserving all 75 Firefox 135 paths and protocol-v1 core fields
- Decode correct get/set/call kinds plus native site, per-process sequence, and microsecond ordering extensions
- Add backward-compatible `start/stop/query/capture/clear/status` actions so page operations can run inside an explicit trace window
- Isolate every launch's controls, traces, values, and cleanup; reject stale controls and never operate on another MCP instance
- Fix the Windows fresh-window order to off → drain → cleanup → on
- Split `installed`, `trace_capable`, and `trace_active` environment status and negotiate capability metadata
- Treat `collect_values` as a safe post-trace snapshot and skip cookies or APIs that would create side effects
- Ignore unsupported tracing on official browsers identified by Camoufox 0.5 metadata without injecting config or disabling their sandbox; default launches remain unchanged
- Camoufox 0.4 official 135 and early markerless custom 135 builds are indistinguishable before launch; explicit tracing still attempts the legacy handshake for compatibility, while the default `enable_trace=False` path is unaffected

## v1.3.0 (2026-09-02) — Camoufox 152 and Non-Invasive Version Selection

- Add optional `browser_version` to select an installed Camoufox 0.5+ build for one launch without changing active config
- Require a repo-qualified, unambiguous selector and reject mismatched active/selected version builds
- Extend `check_environment` with Camoufox Python, active/installed selectors, capability markers, and legacy-cache migration warnings
- Fix browser launch on hosts using `C.UTF-8` instead of emitting invalid `locale="C"`
- Keep defaults unchanged; Camoufox 0.4.x + Firefox 135 users do not need to migrate, and official builds retain every non-PropertyTracer MCP tool
- Pair with the opt-in [Camoufox Reverse 152 beta.30](https://github.com/WhiteNightShadow/camoufox-reverse/releases) prerelease
- Thanks to [@dsaw1111](https://github.com/dsaw1111) for reporting the browser-version gap

## v1.2.0 (2026-08-11) — Generic Source-Site Mapping

- Add opt-in `include_source_site` to `instrumentation(action="install")`
- Let AST and regex tap events carry a stable content-addressed `site_id` and monotonic `seq`
- Return original script SHA-256, URL, character ranges, AST line/column sidecars, and `hot_functions` from `instrumentation(action="log")`
- Keep default tap event fields unchanged; arbitrary user AST scripts and single-VM PC/opcode guessing are intentionally out of scope
- Thanks to [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) for reporting the execution-site correlation gap and providing analysis materials

## v1.1.2 (2026-08-11) — Windows Engine Trace Configuration Fix

- Fix `enable_trace=True` returning `engine_trace_not_available` on Windows when Camoufox splits its JSON across `CAMOU_CONFIG_1..n`
- Reassemble the complete JSON before merging `propertyTrace`, then rebuild any number of contiguous platform-sized chunks while preserving the original fingerprint configuration
- Add regression coverage for numeric ordering, more than ten chunks, Unicode, chunk growth and shrinkage, invalid configs, and actual `camoufox.utils.get_env_vars()` output in simulated Windows mode
- For this issue, users who already have a property-tracing `camoufox-reverse` build only need to upgrade the MCP; the browser does not need to be rebuilt or replaced
- Thanks to [@Code-xy](https://github.com/Code-xy) for the report, Windows validation, root-cause analysis, and proposed direction

## v1.1.1 (2026-07-29) — AST Instrumentation Fix for Chained Calls

- Fix corrupted output caused by overlapping parent/child AST edit ranges in expressions such as `new X().m1().m2()` and `Array.prototype.slice.call(arguments)`
- Keep the outer instrumentation only when edit ranges overlap; existing behavior for ordinary member access and calls remains unchanged
- Expose `last_mode_used` from `instrumentation(action="status")` to distinguish AST, regex fallback, and oversized-file paths

## v1.1.0 (2026-07-29) — Engine Tracing, Browser Attach, and Schema Compatibility

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

## v1.0.0 (2026-04-18) — Streamline + Pure JS Reverse Toolkit

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

## v0.6.0 — Bug Fixes
## v0.5.0 — Signature-Based Anti-Bot Compatibility
## v0.4.0 — Universal JSVMP Adaptation
## v0.3.0 — Stability Fixes
## v0.2.0 — Hook Persistence + JSVMP Analysis
## v0.1.0 — Initial Release (44 tools)
