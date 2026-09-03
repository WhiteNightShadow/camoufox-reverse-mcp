"""Native Gecko property access tracing tools.

Requires camoufox-reverse custom browser build.
Official Camoufox returns an explicit capability error.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ..property_trace import (
    CACHE_DIR,
    RUNS_DIR,
    TRACES_DIR,
    build_sequence,
    build_summary,
    build_timeline,
    cleanup_traces,
    cleanup_values,
    filter_events,
    list_control_files,
    list_session_files,
    load_events,
    read_control_status,
    set_trace_state,
    sort_events,
)
from ..server import browser_manager, mcp

MAX_LOADED_EVENTS = 200_000
MAX_LOADED_BYTES = 128 * 1024 * 1024


def _is_trace_enabled() -> bool:
    """Check this BrowserManager-owned launch, never global/stale controls."""
    if browser_manager.browser is None:
        return False
    trace_base = getattr(browser_manager, "_trace_base_dir", None)
    if trace_base is None:
        return False
    runtime_browser = getattr(browser_manager, "_runtime_browser", None)
    if runtime_browser is not None:
        marker = runtime_browser.get("capabilities_marker")
        known_official = str(runtime_browser.get("repo") or "").lower() == "official"
        if (marker or known_official) and not runtime_browser.get("property_trace", False):
            return False
        protocol = runtime_browser.get("property_trace_protocol")
        if protocol is not None and not runtime_browser.get("property_trace_compatible", False):
            return False
    if not Path(trace_base).exists():
        return False
    return bool(list_control_files(trace_base, live_only=True))


async def _trace_property_access_impl(
    duration: int = 10,
    action: str = "capture",
    mode: str = "summary",
    filter_object: str | None = None,
    search_query: str | None = None,
    filter_kind: str | None = None,
    filter_site: str | None = None,
    limit: int = 1000,
    bucket_ms: int = 500,
    collect_values: bool = False,
) -> dict:
    """Native Gecko DOM/Web API access tracing without page-object rewriting.

    The custom browser records a fixed native coverage set (75 injection sites).
    A hit is strong evidence of an access; a miss is not proof that an unhooked
    property was unused. The tracer does not install JS getters, Proxies, or
    globals, although high-volume tracing can still create a timing side channel.

    Requires camoufox-reverse custom browser launched with enable_trace=True.
    Official Camoufox returns an explicit error; callers may then use compare_env.

    Args:
        action: "capture" (default blocking window), "start" (return immediately),
            "stop" (stop and aggregate), "query" (aggregate without stopping),
            "clear" (stop and remove this run's traces), or "status".
        duration: Trace duration in seconds (default 10).
            Set to 0 to read existing trace data from browser startup
            (useful when you want to capture navigate() events).
        mode: Aggregation view type:
            - "summary" (default): Property access frequency ranking.
              Best for deciding which properties to patch in env emulation.
            - "timeline": Time-bucketed view showing when properties are first accessed.
            - "sequence": Raw event sequence with timestamps.
            - "search": Same as sequence but filtered by search_query.
        filter_object: Only include events from this object (e.g. "navigator").
        search_query: Only include events matching this string in property/value.
        filter_kind: Only include "get", "set", or "call" events.
        filter_site: Match the optional native injection-site identifier.
        limit: Max events for sequence/search mode (default 1000).
        bucket_ms: Bucket size for timeline mode (default 500ms).
        collect_values: Compatibility name for an after-the-fact safe JS snapshot.
            It is not the value at event time; sensitive or side-effectful paths
            are skipped and reported separately.

    Returns:
        summary mode: {mode, duration_s, total_events, unique_properties, by_property, by_object}
            If collect_values=True, adds "values" dict: {property_path: value_or_filepath}
        timeline mode: {mode, duration_s, bucket_ms, buckets}
        sequence mode: {mode, total_events, returned, truncated, events}
    """
    valid_actions = {"capture", "start", "stop", "query", "clear", "status"}
    valid_modes = {"summary", "timeline", "sequence", "search"}
    action = action.lower()
    mode = mode.lower()
    if action not in valid_actions:
        return {"mode": "error", "reason": f"Unknown action: {action}"}
    if mode not in valid_modes:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}
    if duration < 0 or duration > 3600:
        return {"mode": "error", "reason": "duration must be between 0 and 3600"}
    if not 1 <= limit <= 100000:
        return {"mode": "error", "reason": "limit must be between 1 and 100000"}
    if not 1 <= bucket_ms <= 60000:
        return {"mode": "error", "reason": "bucket_ms must be between 1 and 60000"}
    if filter_kind and filter_kind.lower() not in {"get", "set", "call"}:
        return {"mode": "error", "reason": "filter_kind must be get, set, or call"}

    trace_base = getattr(browser_manager, "_trace_base_dir", None)
    if browser_manager.browser is None or trace_base is None:
        return {
            "error": "engine_trace_not_available",
            "message": "当前浏览器会话未启用 Gecko 原生属性追踪。",
            "install_guide": "https://github.com/WhiteNightShadow/camoufox-reverse/releases",
        }
    trace_base = Path(trace_base)
    for _ in range(10):
        if _is_trace_enabled():
            break
        await asyncio.sleep(0.1)
    if not _is_trace_enabled():
        return {
            "error": "engine_trace_not_available",
            "message": "本次启动没有建立有效的 PropertyTracer control 握手。",
            "run_dir": str(trace_base),
        }

    runtime = getattr(browser_manager, "_runtime_browser", None) or {}
    controls = list_control_files(trace_base, live_only=True)
    if action == "status":
        files = list_session_files(base_dir=trace_base)
        enabled_controls = 0
        acknowledged_pids: list[int] = []
        unacknowledged_pids: list[int] = []
        error_pids: list[int] = []
        data_loss_pids: list[int] = []
        use_ack = "control_ack" in set(runtime.get("property_trace_features", []))
        for path in controls:
            try:
                pid = int(path.stem.removeprefix("control-"))
                if use_ack:
                    status = read_control_status(pid, trace_base)
                    enabled_controls += bool(status and status[0] == "on")
                    if status and status[0] in {"on", "off"}:
                        acknowledged_pids.append(pid)
                    else:
                        unacknowledged_pids.append(pid)
                    if status and status[0] == "error":
                        error_pids.append(pid)
                    if status and len(status) > 2 and status[2] == "write_error":
                        data_loss_pids.append(pid)
                else:
                    enabled_controls += path.read_text(encoding="utf-8").strip() == "on"
            except (OSError, ValueError):
                pass
        return {
            "status": "running" if controls else "unavailable",
            "run_dir": str(trace_base),
            "live_processes": len(controls),
            "trace_files": len(files),
            "trace_enabled": enabled_controls > 0,
            "enabled_processes": enabled_controls,
            "error_processes": error_pids,
            "data_loss_processes": data_loss_pids,
            "ack_supported": use_ack,
            "acknowledged": bool(controls) and use_ack and not unacknowledged_pids,
            "acknowledged_processes": len(acknowledged_pids),
            "unacknowledged_processes": sorted(unacknowledged_pids),
        }

    async def set_control_state(state: str) -> dict:
        return await set_trace_state(
            state,
            trace_base,
            features=runtime.get("property_trace_features", []),
        )

    async def stop_controls() -> dict:
        return await set_control_state("off")

    async def start_fresh() -> dict | None:
        stopped = await stop_controls()
        if stopped.get("error"):
            return stopped
        cleanup_traces(trace_base)
        cleanup_values(trace_base)
        remaining = list_session_files(base_dir=trace_base)
        if remaining:
            return {
                "mode": "error",
                "reason": "Trace files remained open after stop; fresh window not started.",
                "files": [str(path) for path in remaining],
            }
        started = await set_control_state("on")
        if started.get("error"):
            return started
        if started.get("count", 0) == 0:
            return {"mode": "error", "reason": "No live trace controls accepted start."}
        browser_manager._trace_started_at = time.monotonic()
        return None

    if action == "clear":
        stopped = await stop_controls()
        if stopped.get("error"):
            return stopped
        browser_manager._trace_started_at = None
        removed = cleanup_traces(trace_base)
        values_removed = cleanup_values(trace_base)
        return {
            "status": "cleared",
            "run_dir": str(trace_base),
            "processes_stopped": stopped.get("count", 0),
            "acknowledged": stopped.get("acknowledged", False),
            "files_removed": removed,
            "values_removed": values_removed,
            "data_loss_pids": stopped.get("data_loss_pids", []),
        }
    if action == "start":
        error = await start_fresh()
        if error:
            return error
        return {
            "status": "started",
            "run_dir": str(trace_base),
            "live_processes": len(list_control_files(trace_base, live_only=True)),
            "next": "Run page actions, then call trace_property_access(action='stop').",
        }

    measured_duration: float | None = None
    if action == "capture" and duration > 0:
        error = await start_fresh()
        if error:
            return error
        await asyncio.sleep(duration)
        stopped = await stop_controls()
        if stopped.get("error"):
            return stopped
        browser_manager._trace_started_at = None
        if stopped.get("data_loss_pids"):
            return {
                "error": "engine_trace_incomplete",
                "message": "Native trace write failed; captured data may be incomplete.",
                "pids": stopped["data_loss_pids"],
            }
        measured_duration = float(duration)
    elif action == "capture":
        # duration=0 preserves the auto-start navigation trace.
        stopped = await stop_controls()
        if stopped.get("error"):
            return stopped
        browser_manager._trace_started_at = None
        if stopped.get("data_loss_pids"):
            return {
                "error": "engine_trace_incomplete",
                "message": "Native trace write failed; captured data may be incomplete.",
                "pids": stopped["data_loss_pids"],
            }
    elif action == "stop":
        stopped = await stop_controls()
        if stopped.get("error"):
            return stopped
        started_at = getattr(browser_manager, "_trace_started_at", None)
        browser_manager._trace_started_at = None
        if stopped.get("data_loss_pids"):
            return {
                "error": "engine_trace_incomplete",
                "message": "Native trace write failed; captured data may be incomplete.",
                "pids": stopped["data_loss_pids"],
            }
        if started_at is not None:
            measured_duration = max(0.0, time.monotonic() - started_at)
    elif action == "query":
        # Allow the native 100ms batch interval to make recent events visible.
        await asyncio.sleep(0.12)
        if "control_ack" in set(runtime.get("property_trace_features", [])):
            failed = []
            for path in list_control_files(trace_base, live_only=True):
                try:
                    pid = int(path.stem.removeprefix("control-"))
                except ValueError:
                    continue
                status = read_control_status(pid, trace_base)
                if status and (
                    status[0] == "error" or status[2] == "write_error"
                ):
                    failed.append(pid)
            if failed:
                return {
                    "error": "engine_trace_incomplete",
                    "message": "Native trace I/O failed; query data may be incomplete.",
                    "pids": failed,
                }
        started_at = getattr(browser_manager, "_trace_started_at", None)
        if started_at is not None:
            measured_duration = max(0.0, time.monotonic() - started_at)

    events: list[dict] = []
    file_counts: dict[str, int] = {}
    trace_files = list_session_files(base_dir=trace_base)
    file_sizes: dict[Path, int] = {}
    for path in trace_files:
        try:
            file_sizes[path] = path.stat().st_size
        except OSError:
            file_sizes[path] = 0
    total_trace_bytes = sum(file_sizes.values())
    loaded_bytes_budget = 0
    for path in trace_files:
        file_size = file_sizes[path]
        remaining_events = MAX_LOADED_EVENTS - len(events)
        if remaining_events <= 0:
            break
        remaining_bytes = MAX_LOADED_BYTES - loaded_bytes_budget
        if remaining_bytes <= 0:
            break
        loaded = load_events(
            path,
            annotate=True,
            limit=remaining_events,
            max_bytes=remaining_bytes,
        )
        events.extend(loaded)
        loaded_bytes_budget += min(file_size, remaining_bytes)
        file_counts[str(path)] = len(loaded)
    events = sort_events(events)
    input_truncated = bool(
        len(events) >= MAX_LOADED_EVENTS
        or total_trace_bytes > loaded_bytes_budget
    )

    coverage = {
        "scope": "fingerprint-native",
        "hook_count": runtime.get("property_trace_hooks", 75),
        "protocol": runtime.get("property_trace_protocol", 1),
        "features": runtime.get("property_trace_features", []),
        "negative_result_is_conclusive": False,
    }
    if not events:
        return {
            "mode": "error",
            "reason": "No trace events captured during the window.",
            "hint": "Ensure the page action touches one of the 75 covered native sites.",
            "run_dir": str(trace_base),
            "possibly_capped": False,
            "input_truncated": input_truncated,
            "coverage": coverage,
        }

    if measured_duration is None:
        measured_duration = max(
            event.get("_global_ms", event.get("t", 0)) for event in events
        ) / 1000
    measured_duration = round(measured_duration, 3)

    raw_total = len(events)
    events = filter_events(
        events,
        filter_object,
        search_query,
        filter_kind,
        filter_site,
    )

    if mode == "summary":
        result = build_summary(events, measured_duration)
    elif mode == "timeline":
        result = build_timeline(events, measured_duration, bucket_ms)
    elif mode in ("sequence", "search"):
        result = build_sequence(events, limit)
        result["mode"] = mode

    if collect_values and action == "query":
        result["values_error"] = (
            "collect_values is disabled for action='query' because the native "
            "tracer may still be active and the snapshot would pollute it; use stop first"
        )
        result["snapshot_values"] = {}
        result["values_skipped"] = {"*": "active trace not mutated"}
    elif collect_values and result.get("by_property"):
        snapshot = await _collect_property_values(result["by_property"], trace_base)
        result["values"] = snapshot["values"]
        result["snapshot_values"] = snapshot["values"]
        result["values_skipped"] = snapshot["skipped"]
        result["values_semantics"] = "post-trace safe snapshot; not event-time values"
        if snapshot.get("error"):
            result["values_error"] = snapshot["error"]

    max_events = getattr(browser_manager, "_trace_max_events", 100000)
    result["raw_total_events"] = raw_total
    result["filtered_total_events"] = len(events)
    result["run_dir"] = str(trace_base)
    result["trace_files"] = len(file_counts)
    result["trace_bytes"] = total_trace_bytes
    result["input_truncated"] = input_truncated
    result["loaded_event_limit"] = MAX_LOADED_EVENTS
    result["loaded_byte_limit"] = MAX_LOADED_BYTES
    result["possibly_capped"] = any(count >= max_events for count in file_counts.values())
    result["coverage"] = coverage

    return result


@mcp.tool()
async def trace_property_access(
    duration: int = 10,
    action: str = "capture",
    mode: str = "summary",
    filter_object: str | None = None,
    search_query: str | None = None,
    filter_kind: str | None = None,
    filter_site: str | None = None,
    limit: int = 1000,
    bucket_ms: int = 500,
    collect_values: bool = False,
) -> dict:
    """Control/query the current Gecko native PropertyTracer run.

    action supports capture, start, stop, query, clear, and status. Results can
    use summary, timeline, sequence, or search views and optional object, kind,
    site, and keyword filters. collect_values is a safe post-trace snapshot,
    not an event-time value capture.
    """
    lock = browser_manager._trace_action_lock
    async with lock:
        if (
            action.lower() in {"start", "capture"}
            and browser_manager._trace_started_at is not None
        ):
            return {
                "error": "engine_trace_already_started",
                "message": "Stop or clear the current interactive trace before starting another.",
            }
        try:
            return await _trace_property_access_impl(
                duration=duration,
                action=action,
                mode=mode,
                filter_object=filter_object,
                search_query=search_query,
                filter_kind=filter_kind,
                filter_site=filter_site,
                limit=limit,
                bucket_ms=bucket_ms,
                collect_values=collect_values,
            )
        except asyncio.CancelledError:
            trace_base = getattr(browser_manager, "_trace_base_dir", None)
            if trace_base is not None:
                cleanup = asyncio.create_task(
                    set_trace_state(
                        "off",
                        trace_base,
                        features=(browser_manager._runtime_browser or {}).get(
                            "property_trace_features", []
                        ),
                        timeout=5.0,
                    )
                )
                try:
                    await asyncio.wait_for(asyncio.shield(cleanup), timeout=6.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            browser_manager._trace_started_at = None
            raise


@mcp.tool()
async def list_trace_files(limit: int = 20) -> dict:
    """List all trace files on disk (for post-hoc analysis).

    Returns:
        dict with traces_dir, total file count, and file details.
    """
    if not 1 <= limit <= 1000:
        return {"mode": "error", "reason": "limit must be between 1 and 1000"}

    all_files = []
    for f in list_session_files(include_all_runs=True):
        try:
            file_pid, session_id = (int(item) for item in f.stem.rsplit("_", 1))
        except (IndexError, ValueError):
            continue

        size_kb = f.stat().st_size / 1024
        all_files.append({
            "path": str(f),
            "pid": file_pid,
            "session_id": session_id,
            "size_kb": round(size_kb, 1),
            "mtime": f.stat().st_mtime,
            "run_dir": str(f.parent.parent),
            "active_run": str(f.parent.parent) == str(
                getattr(browser_manager, "_trace_base_dir", "")
            ),
        })

    all_files.sort(key=lambda x: x["mtime"], reverse=True)
    return {
        "traces_dir": str(TRACES_DIR),
        "runs_dir": str(RUNS_DIR),
        "total": len(all_files),
        "returned": min(len(all_files), limit),
        "files": all_files[:limit],
    }


@mcp.tool()
async def query_trace_file(
    file_path: str,
    mode: str = "summary",
    filter_object: str | None = None,
    search_query: str | None = None,
    filter_kind: str | None = None,
    filter_site: str | None = None,
    limit: int = 1000,
    bucket_ms: int = 500,
) -> dict:
    """Query a specific historical trace file (post-hoc analysis).

    Args:
        file_path: Path to the .jsonl trace file.
        mode: Same as trace_property_access (summary/timeline/sequence/search).
        filter_object: Filter by object name.
        search_query: Filter by search string.
        limit: Max events for sequence mode.
        bucket_ms: Bucket size for timeline mode.
    """
    if mode not in {"summary", "timeline", "sequence", "search"}:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}
    if not 1 <= limit <= 100000 or not 1 <= bucket_ms <= 60000:
        return {"mode": "error", "reason": "invalid limit or bucket_ms"}
    if filter_kind and filter_kind.lower() not in {"get", "set", "call"}:
        return {"mode": "error", "reason": "filter_kind must be get, set, or call"}
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        return {"mode": "error", "reason": f"File not found: {file_path}"}
    allowed = False
    for root in (CACHE_DIR.resolve(),):
        try:
            path.relative_to(root)
            allowed = True
        except ValueError:
            pass
    if not allowed or path.suffix != ".jsonl":
        return {
            "mode": "error",
            "reason": "file_path must be a PropertyTracer JSONL under the trace cache",
        }

    file_size = path.stat().st_size
    events = sort_events(
        load_events(
            path,
            annotate=True,
            limit=MAX_LOADED_EVENTS,
            max_bytes=MAX_LOADED_BYTES,
        )
    )
    input_truncated = bool(
        len(events) >= MAX_LOADED_EVENTS or file_size > MAX_LOADED_BYTES
    )
    raw_total = len(events)
    events = filter_events(
        events, filter_object, search_query, filter_kind, filter_site
    )

    duration_s = 0
    if events:
        duration_s = (max(event.get("_global_ms", 0) for event in events) // 1000) + 1

    if mode == "summary":
        result = build_summary(events, duration_s)
    elif mode == "timeline":
        result = build_timeline(events, duration_s, bucket_ms)
    elif mode in ("sequence", "search"):
        result = build_sequence(events, limit)
        result["mode"] = mode
    result["input_truncated"] = input_truncated
    result["loaded_event_limit"] = MAX_LOADED_EVENTS
    result["loaded_byte_limit"] = MAX_LOADED_BYTES
    result["raw_total_events"] = raw_total
    result["filtered_total_events"] = len(events)
    result["possibly_capped"] = None
    result["cap_known"] = False
    result["coverage"] = {
        "scope": "fingerprint-native",
        "hook_count": 75,
        "protocol": 1,
        "features": [],
        "negative_result_is_conclusive": False,
        "source": "historical_file",
    }
    return result


async def _collect_property_values(
    by_property: list[dict],
    trace_base: Path,
) -> dict:
    """Take a conservative post-trace snapshot without sensitive APIs."""
    from ..property_trace import values_dir as get_values_dir

    snapshot_dir = get_values_dir(trace_base)
    snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Build JS expression to read all unique properties
    # Map trace paths to JS expressions
    path_to_js = {
        "navigator.userAgent": "navigator.userAgent",
        "navigator.platform": "navigator.platform",
        "navigator.language": "navigator.language",
        "navigator.languages": "JSON.stringify(navigator.languages)",
        "navigator.hardwareConcurrency": "navigator.hardwareConcurrency",
        "navigator.maxTouchPoints": "navigator.maxTouchPoints",
        "navigator.cookieEnabled": "navigator.cookieEnabled",
        "navigator.onLine": "navigator.onLine",
        "navigator.pdfViewerEnabled": "navigator.pdfViewerEnabled",
        "navigator.doNotTrack": "navigator.doNotTrack",
        "navigator.appVersion": "navigator.appVersion",
        "navigator.appCodeName": "navigator.appCodeName",
        "navigator.appName": "navigator.appName",
        "navigator.product": "navigator.product",
        "navigator.productSub": "navigator.productSub",
        "navigator.oscpu": "navigator.oscpu",
        "navigator.buildID": "navigator.buildID",
        "navigator.globalPrivacyControl": "navigator.globalPrivacyControl",
        "screen.rect": "JSON.stringify({w:screen.width,h:screen.height})",
        "screen.availRect": "JSON.stringify({w:screen.availWidth,h:screen.availHeight,l:screen.availLeft,t:screen.availTop})",
        "screen.pixelDepth": "screen.pixelDepth",
        "screen.colorDepth": "screen.colorDepth",
        "window.innerWidth": "window.innerWidth",
        "window.innerHeight": "window.innerHeight",
        "window.outerWidth": "window.outerWidth",
        "window.outerHeight": "window.outerHeight",
        "window.screenX": "window.screenX",
        "window.screenY": "window.screenY",
        "window.devicePixelRatio": "window.devicePixelRatio",
        "window.scrollX": "window.scrollX",
        "window.scrollY": "window.scrollY",
        "history.length": "history.length",
        "navigator.plugins.indexedGetter": "navigator.plugins.length",
        "navigator.mimeTypes.indexedGetter": "navigator.mimeTypes.length",
        "performance.timing": "JSON.stringify(performance.timing)",
    }

    blocked_reasons = {
        "document.cookie.get": "sensitive value",
        "document.cookie.set": "setter; no safe snapshot",
        "canvas.toDataURL": "would create and fingerprint a new canvas",
        "canvas.toBlob": "would create and invoke a new canvas",
        "canvas.getContext": "would create a new rendering context",
        "canvas2d.getImageData": "would create and read a new canvas",
        "webgl.getParameter": "would create a new WebGL context",
        "webgl.getSupportedExtensions": "would create a new WebGL context",
        "webgl.getExtension": "would create a new WebGL context",
        "webgl.getShaderPrecisionFormat": "would create a new WebGL context",
        "audioContext.sampleRate": "would create a new AudioContext",
        "audioContext.outputLatency": "would create a new AudioContext",
    }

    # Get unique property paths from trace
    paths = [p["path"] for p in by_property]

    # Build batch JS
    js_parts = []
    skipped: dict[str, str] = {}
    for path in paths:
        if path in blocked_reasons:
            skipped[path] = blocked_reasons[path]
            continue
        js_expr = path_to_js.get(path)
        if js_expr:
            safe_key = path.replace(".", "_").replace("-", "_")
            js_parts.append(f'try{{r.{safe_key}={js_expr}}}catch(e){{r.{safe_key}="ERROR:"+e.message}}')
        else:
            skipped[path] = "no side-effect-free snapshot mapping"

    if not js_parts:
        return {"values": {}, "skipped": skipped}

    js_code = "(() => { var r = {}; " + ";".join(js_parts) + "; return r; })()"

    try:
        page = await browser_manager.get_active_page()
        raw = await page.evaluate(js_code)
    except Exception as e:
        return {"values": {}, "skipped": skipped, "error": f"evaluate_js failed: {e}"}

    # Process results: save large values to files
    values = {}
    for path in paths:
        safe_key = path.replace(".", "_").replace("-", "_")
        val = raw.get(safe_key)
        if val is None:
            continue
        val_str = str(val)
        if len(val_str) > 500:
            # Save to file
            filename = f"{safe_key}.txt"
            filepath = snapshot_dir / filename
            filepath.write_text(val_str, encoding="utf-8")
            filepath.chmod(0o600)
            values[path] = f"[file:{filepath}] ({len(val_str)} chars)"
        else:
            values[path] = val

    return {"values": values, "skipped": skipped}
