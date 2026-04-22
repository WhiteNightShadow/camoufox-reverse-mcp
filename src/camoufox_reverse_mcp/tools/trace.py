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
) -> dict:
    """Engine-level DOM property access tracing (JSVMP-undetectable).

    Traces which DOM properties (navigator, screen, window, canvas, webgl, etc.)
    are accessed by page JavaScript including JSVMP bytecode interpreters.
    Operates at the C++ SpiderMonkey engine level — completely invisible to JS.

    Requires camoufox-reverse custom browser launched with enable_trace=True.
    Falls back to compare_env when using official Camoufox.

    Args:
        duration: Trace duration in seconds (default 10).
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

    Returns:
        summary mode: {mode, duration_s, total_events, unique_properties, by_property, by_object}
        timeline mode: {mode, duration_s, bucket_ms, buckets}
        sequence mode: {mode, total_events, returned, truncated, events}
        fallback: {mode: "fallback_compare_env", reason, result}
    """
    if not _is_trace_enabled():
        return {
            "error": "engine_trace_not_available",
            "message": "当前浏览器不支持引擎层 DOM 属性追踪，需要安装 camoufox-reverse 定制版浏览器。",
            "install_guide": "https://github.com/WhiteNightShadow/camoufox-reverse/releases",
        }

    # Clean old traces
    cleanup_traces()

    # Force new session: off → on cycle
    write_control_all("off")
    await asyncio.sleep(0.5)
    write_control_all("on")
    await asyncio.sleep(0.3)

    # Wait for trace duration
    await asyncio.sleep(duration)

    # Write "off" to all control files
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
        return build_summary(events, duration)
    elif mode == "timeline":
        return build_timeline(events, duration, bucket_ms)
    elif mode in ("sequence", "search"):
        return build_sequence(events, limit)
    else:
        return {"mode": "error", "reason": f"Unknown mode: {mode}"}


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
