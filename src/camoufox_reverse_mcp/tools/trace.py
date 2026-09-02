"""Engine-level property access tracing tools (v1.1.0).

Requires camoufox-reverse custom browser build.
Falls back gracefully when using official Camoufox.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from ..server import mcp, browser_manager
from ..property_trace import (
    CONTROL_DIR, TRACES_DIR,
    list_session_files, load_events,
    build_summary, build_timeline, build_sequence,
    filter_events, write_control_all, cleanup_traces,
)


def _is_trace_enabled() -> bool:
    """Check if any control files exist (= custom browser with trace enabled)."""
    runtime_browser = getattr(browser_manager, "_runtime_browser", None)
    if runtime_browser is not None and not runtime_browser.get("property_trace", False):
        return False
    if runtime_browser is None:
        try:
            from ..camoufox_runtime import inspect_camoufox_runtime

            runtime = inspect_camoufox_runtime()
            active = runtime.get("active")
            if (
                runtime.get("multiversion_supported")
                and active is not None
                and not active.get("property_trace", False)
            ):
                return False
        except Exception:
            pass
    if not CONTROL_DIR.exists():
        return False
    return len(list(CONTROL_DIR.glob("control-*.cmd"))) > 0


@mcp.tool()
async def trace_property_access(
    duration: int = 10,
    mode: str = "summary",
    filter_object: Optional[str] = None,
    search_query: Optional[str] = None,
    limit: int = 1000,
    bucket_ms: int = 500,
    collect_values: bool = False,
) -> dict:
    """Engine-level DOM property access tracing (JSVMP-undetectable).

    Traces which DOM properties (navigator, screen, window, canvas, webgl, etc.)
    are accessed by page JavaScript including JSVMP bytecode interpreters.
    Operates at the C++ SpiderMonkey engine level — completely invisible to JS.

    Requires camoufox-reverse custom browser launched with enable_trace=True.
    Falls back to compare_env when using official Camoufox.

    Args:
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
        limit: Max events for sequence/search mode (default 1000).
        bucket_ms: Bucket size for timeline mode (default 500ms).
        collect_values: If True, after trace completes, use evaluate_js to read
            real values of all traced properties from the browser. Large values
            (Canvas dataURL, WebGL params etc.) are saved to files under
            ~/.cache/camoufox-reverse/values/ and returned as file paths.

    Returns:
        summary mode: {mode, duration_s, total_events, unique_properties, by_property, by_object}
            If collect_values=True, adds "values" dict: {property_path: value_or_filepath}
        timeline mode: {mode, duration_s, bucket_ms, buckets}
        sequence mode: {mode, total_events, returned, truncated, events}
    """
    if not _is_trace_enabled():
        return {
            "error": "engine_trace_not_available",
            "message": "当前浏览器不支持引擎层 DOM 属性追踪，需要安装 camoufox-reverse 定制版浏览器。",
            "install_guide": "https://github.com/WhiteNightShadow/camoufox-reverse/releases",
        }

    if duration > 0:
        # Active trace: off→on→wait→off cycle for a fresh window
        cleanup_traces()
        write_control_all("off")
        await asyncio.sleep(0.5)
        write_control_all("on")
        await asyncio.sleep(0.3)
        await asyncio.sleep(duration)
        write_control_all("off")
        await asyncio.sleep(0.5)
    else:
        # duration=0: read existing trace files from auto-start (includes navigate events)
        write_control_all("off")
        await asyncio.sleep(0.5)

    # Collect events from all process trace files
    events: list[dict] = []
    for f in list_session_files():
        events.extend(load_events(f))

    if not events:
        return {
            "mode": "error",
            "reason": "No trace events captured during the window.",
            "hint": "Ensure the page has loaded and JSVMP is executing.",
        }

    # Filter
    events = filter_events(events, filter_object, search_query)

    # Aggregate
    if mode == "summary":
        result = build_summary(events, duration)
    elif mode == "timeline":
        result = build_timeline(events, duration, bucket_ms)
    elif mode in ("sequence", "search"):
        result = build_sequence(events, limit)
    else:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}

    # Collect real values from browser
    if collect_values and result.get("by_property"):
        values = await _collect_property_values(result["by_property"])
        result["values"] = values

    return result


@mcp.tool()
async def list_trace_files(limit: int = 20) -> dict:
    """List all trace files on disk (for post-hoc analysis).

    Returns:
        dict with traces_dir, total file count, and file details.
    """
    if not TRACES_DIR.exists():
        return {"files": [], "total": 0, "traces_dir": str(TRACES_DIR)}

    all_files = []
    for f in TRACES_DIR.glob("*.jsonl"):
        try:
            parts = f.stem.split("_")
            file_pid = int(parts[0]) if parts else -1
            session_id = int(parts[1]) if len(parts) > 1 else -1
        except (IndexError, ValueError):
            continue

        size_kb = f.stat().st_size / 1024
        all_files.append({
            "path": str(f),
            "pid": file_pid,
            "session_id": session_id,
            "size_kb": round(size_kb, 1),
            "mtime": f.stat().st_mtime,
        })

    all_files.sort(key=lambda x: x["mtime"], reverse=True)
    return {
        "traces_dir": str(TRACES_DIR),
        "total": len(all_files),
        "returned": min(len(all_files), limit),
        "files": all_files[:limit],
    }


@mcp.tool()
async def query_trace_file(
    file_path: str,
    mode: str = "summary",
    filter_object: Optional[str] = None,
    search_query: Optional[str] = None,
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
    path = Path(file_path)
    if not path.exists():
        return {"mode": "error", "reason": f"File not found: {file_path}"}

    events = load_events(path)
    events = filter_events(events, filter_object, search_query)

    duration_s = 0
    if events:
        duration_s = (events[-1].get("t", 0) // 1000) + 1

    if mode == "summary":
        return build_summary(events, duration_s)
    elif mode == "timeline":
        return build_timeline(events, duration_s, bucket_ms)
    elif mode in ("sequence", "search"):
        return build_sequence(events, limit)
    else:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}


async def _collect_property_values(by_property: list[dict]) -> dict:
    """Read real values of traced properties from the browser via evaluate_js.
    Large values (>500 chars) are saved to files."""
    import json as _json
    from ..property_trace import CACHE_DIR

    values_dir = CACHE_DIR / "values"
    values_dir.mkdir(parents=True, exist_ok=True)

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
        "document.cookie.get": "document.cookie",
        "history.length": "history.length",
        "navigator.plugins.indexedGetter": "navigator.plugins.length",
        "navigator.mimeTypes.indexedGetter": "navigator.mimeTypes.length",
        "performance.timing": "JSON.stringify(performance.timing)",
        "canvas.toDataURL": "(()=>{var c=document.createElement('canvas');c.width=200;c.height=50;var x=c.getContext('2d');x.fillText('trace',10,30);return c.toDataURL()})()",
        "canvas2d.getImageData": "(()=>{var c=document.createElement('canvas');c.width=10;c.height=10;var x=c.getContext('2d');x.fillRect(0,0,5,5);return JSON.stringify(Array.from(x.getImageData(0,0,1,1).data))})()",
        "webgl.getParameter": "(()=>{var c=document.createElement('canvas');var g=c.getContext('webgl');if(!g)return null;return JSON.stringify({renderer:g.getParameter(g.RENDERER),vendor:g.getParameter(g.VENDOR)})})()",
        "webgl.getSupportedExtensions": "(()=>{var c=document.createElement('canvas');var g=c.getContext('webgl');if(!g)return null;return JSON.stringify(g.getSupportedExtensions())})()",
        "webgl.getShaderPrecisionFormat": "(()=>{var c=document.createElement('canvas');var g=c.getContext('webgl');if(!g)return null;var p=g.getShaderPrecisionFormat(g.VERTEX_SHADER,g.HIGH_FLOAT);return JSON.stringify({rangeMin:p.rangeMin,rangeMax:p.rangeMax,precision:p.precision})})()",
        "AudioContext.sampleRate": "(()=>{try{var a=new AudioContext();var r=a.sampleRate;a.close();return r}catch(e){return null}})()",
    }

    # Get unique property paths from trace
    paths = [p["path"] for p in by_property]

    # Build batch JS
    js_parts = []
    for path in paths:
        js_expr = path_to_js.get(path)
        if js_expr:
            safe_key = path.replace(".", "_").replace("-", "_")
            js_parts.append(f'try{{r.{safe_key}={js_expr}}}catch(e){{r.{safe_key}="ERROR:"+e.message}}')

    if not js_parts:
        return {}

    js_code = "(() => { var r = {}; " + ";".join(js_parts) + "; return r; })()"

    try:
        page = await browser_manager.get_active_page()
        raw = await page.evaluate(js_code)
    except Exception as e:
        return {"error": f"evaluate_js failed: {e}"}

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
            filepath = values_dir / filename
            filepath.write_text(val_str, encoding="utf-8")
            values[path] = f"[file:{filepath}] ({len(val_str)} chars)"
        else:
            values[path] = val

    return values


async def _fallback_compare_env(reason: str) -> dict:
    """Fallback to compare_env when custom browser is not available."""
    try:
        from .jsvmp import compare_env
        result = await compare_env()
    except Exception as e:
        result = {"error": f"compare_env also failed: {e}"}

    return {
        "mode": "fallback_compare_env",
        "reason": reason,
        "install_hint": (
            "To use engine-level tracing:\n"
            "1. Download camoufox-reverse from GitHub Releases\n"
            "2. Launch with: launch_browser(enable_trace=True)\n"
            "3. Then call: trace_property_access(duration=10)"
        ),
        "releases_url": "https://github.com/WhiteNightShadow/camoufox-reverse/releases",
        "result": result,
    }
