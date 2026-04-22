"""Property access trace helpers for camoufox-reverse custom builds."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

# File system conventions (must match C++ PropertyTracer)
CACHE_DIR = Path.home() / ".cache" / "camoufox-reverse"
CONTROL_DIR = CACHE_DIR / "control"
TRACES_DIR = CACHE_DIR / "traces"

DEFAULT_TRACED_OBJECTS = [
    "Navigator", "Screen", "Document", "HTMLDocument",
    "Window", "Performance", "History", "Location",
    "HTMLCanvasElement", "WebGLRenderingContext", "AudioContext",
]


def ensure_dirs() -> None:
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)


def control_path_for(pid: int) -> Path:
    return CONTROL_DIR / f"control-{pid}.cmd"


def build_property_trace_config() -> dict:
    """Build the propertyTrace config block for CAMOU_CONFIG."""
    ensure_dirs()
    return {
        "enabled": True,
        "logDir": str(CACHE_DIR),
        "objects": [],
        "maxEventsPerSession": 100000,
    }


def write_control(pid: int, cmd: str) -> bool:
    try:
        control_path_for(pid).write_text(cmd)
        return True
    except Exception:
        return False


def write_control_all(cmd: str) -> int:
    """Write command to all control files. Returns count."""
    count = 0
    for f in CONTROL_DIR.glob("control-*.cmd"):
        try:
            f.write_text(cmd)
            count += 1
        except Exception:
            pass
    return count


def list_session_files(pid: Optional[int] = None) -> list[Path]:
    if not TRACES_DIR.exists():
        return []
    pattern = f"{pid}_*.jsonl" if pid else "*.jsonl"
    files = []
    for f in TRACES_DIR.glob(pattern):
        try:
            parts = f.stem.split("_")
            session_id = int(parts[1]) if len(parts) > 1 else 0
            files.append((session_id, f))
        except (IndexError, ValueError):
            continue
    files.sort()
    return [f for _, f in files]


def load_events(jsonl_path: Path) -> list[dict]:
    events = []
    if not jsonl_path.exists():
        return events
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def cleanup_old_traces(keep_days: int = 7) -> int:
    if not TRACES_DIR.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    count = 0
    for f in TRACES_DIR.glob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                count += 1
        except Exception:
            continue
    return count


def cleanup_traces() -> None:
    """Clean trace files only (not control files)."""
    for f in TRACES_DIR.glob("*.jsonl"):
        try: f.unlink()
        except: pass


# ====================== Aggregation views ======================

def build_summary(events: list[dict], duration_s: int) -> dict:
    by_path: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "first_ms": None, "last_ms": None,
    })
    by_object: dict[str, int] = defaultdict(int)

    for e in events:
        obj = e.get("o", "")
        prop = e.get("p", "")
        path = f"{obj}.{prop}"
        ts = e.get("t", 0)

        entry = by_path[path]
        entry["count"] += 1
        if entry["first_ms"] is None or ts < entry["first_ms"]:
            entry["first_ms"] = ts
        if entry["last_ms"] is None or ts > entry["last_ms"]:
            entry["last_ms"] = ts
        by_object[obj] += 1

    by_property_list = [
        {"path": path, **stats}
        for path, stats in sorted(by_path.items(), key=lambda x: -x[1]["count"])
    ]

    return {
        "mode": "summary",
        "duration_s": duration_s,
        "total_events": len(events),
        "unique_properties": len(by_path),
        "by_property": by_property_list,
        "by_object": dict(sorted(by_object.items(), key=lambda x: -x[1])),
    }


def build_timeline(events: list[dict], duration_s: int, bucket_ms: int) -> dict:
    if not events:
        return {"mode": "timeline", "duration_s": duration_s,
                "bucket_ms": bucket_ms, "buckets": []}

    max_ms = max(e.get("t", 0) for e in events)
    n_buckets = (max_ms // bucket_ms) + 1
    buckets = [
        {"from_ms": i * bucket_ms, "to_ms": (i + 1) * bucket_ms,
         "events": 0, "new_properties": []}
        for i in range(n_buckets)
    ]

    seen: set[str] = set()
    for e in events:
        ts = e.get("t", 0)
        idx = ts // bucket_ms
        if idx >= n_buckets:
            continue
        path = f"{e.get('o', '')}.{e.get('p', '')}"
        buckets[idx]["events"] += 1
        if path not in seen:
            seen.add(path)
            buckets[idx]["new_properties"].append(path)

    return {"mode": "timeline", "duration_s": duration_s,
            "bucket_ms": bucket_ms, "buckets": buckets}


def build_sequence(events: list[dict], limit: int) -> dict:
    truncated = len(events) > limit
    shown = events[:limit]
    return {
        "mode": "sequence",
        "total_events": len(events),
        "returned": len(shown),
        "truncated": truncated,
        "events": [
            {"idx": i, "ms": e.get("t", 0),
             "path": f"{e.get('o', '')}.{e.get('p', '')}",
             "kind": {0: "get", 1: "set", 2: "call"}.get(e.get("k", 0), "?"),
             "v": e.get("v", "")}
            for i, e in enumerate(shown)
        ],
    }


def filter_events(
    events: list[dict],
    filter_object: Optional[str] = None,
    search_query: Optional[str] = None,
) -> list[dict]:
    if filter_object:
        events = [e for e in events if e.get("o") == filter_object]
    if search_query:
        q = search_query.lower()
        events = [
            e for e in events
            if q in str(e.get("p", "")).lower()
            or q in str(e.get("v", "")).lower()
            or q in str(e.get("o", "")).lower()
        ]
    return events
