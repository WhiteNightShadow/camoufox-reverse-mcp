"""Property access trace helpers for camoufox-reverse custom builds."""
from __future__ import annotations

import json
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path

# File system conventions (must match C++ PropertyTracer)
CACHE_DIR = Path.home() / ".cache" / "camoufox-reverse"
CONTROL_DIR = CACHE_DIR / "control"
TRACES_DIR = CACHE_DIR / "traces"
RUNS_DIR = CACHE_DIR / "runs"

DEFAULT_TRACED_OBJECTS = [
    "Navigator", "Screen", "Document", "HTMLDocument",
    "Window", "Performance", "History", "Location",
    "HTMLCanvasElement", "WebGLRenderingContext", "AudioContext",
]


def _base_dir(base_dir: Path | str | None = None) -> Path:
    return Path(base_dir).expanduser().resolve() if base_dir else CACHE_DIR


def control_dir(base_dir: Path | str | None = None) -> Path:
    return _base_dir(base_dir) / "control"


def traces_dir(base_dir: Path | str | None = None) -> Path:
    return _base_dir(base_dir) / "traces"


def values_dir(base_dir: Path | str | None = None) -> Path:
    return _base_dir(base_dir) / "values"


def ensure_dirs(base_dir: Path | str | None = None) -> Path:
    base = _base_dir(base_dir)
    control_dir(base).mkdir(parents=True, exist_ok=True, mode=0o700)
    traces_dir(base).mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def create_trace_run() -> Path:
    """Create one private trace root for a single owned browser launch."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _ in range(10):
        token = f"{time.time_ns()}-{os.getpid()}-{secrets.token_hex(4)}"
        base = RUNS_DIR / token
        try:
            base.mkdir(mode=0o700)
        except FileExistsError:
            continue
        ensure_dirs(base)
        return base.resolve()
    raise RuntimeError("unable to allocate a unique PropertyTracer run directory")


def control_path_for(pid: int, base_dir: Path | str | None = None) -> Path:
    return control_dir(base_dir) / f"control-{pid}.cmd"


def status_path_for(pid: int, base_dir: Path | str | None = None) -> Path:
    return control_dir(base_dir) / f"status-{pid}.state"


def read_control_status(
    pid: int,
    base_dir: Path | str | None = None,
) -> tuple[str, int | None, str | None] | None:
    try:
        parts = status_path_for(pid, base_dir).read_text(encoding="utf-8").split()
    except OSError:
        return None
    if not parts or parts[0] not in {"on", "off", "error"}:
        return None
    try:
        session_id = int(parts[1]) if len(parts) > 1 else None
    except ValueError:
        session_id = None
    detail = parts[2] if len(parts) > 2 else None
    return parts[0], session_id, detail


def write_desired_state(
    state: str,
    base_dir: Path | str | None = None,
) -> Path:
    if state not in {"on", "off"}:
        raise ValueError(f"unsupported desired trace state: {state}")
    base = ensure_dirs(base_dir)
    path = base / "desired.state"
    path.write_text(state, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def build_property_trace_config(
    base_dir: Path | str | None = None,
    *,
    objects: list[str] | None = None,
    max_events: int = 100000,
) -> dict:
    """Build the propertyTrace config block for CAMOU_CONFIG."""
    base = ensure_dirs(base_dir)
    write_desired_state("on", base)
    return {
        "enabled": True,
        "logDir": str(base),
        "objects": list(objects or []),
        "maxEventsPerSession": max_events,
    }


def _control_pid(path: Path) -> int | None:
    try:
        return int(path.stem.removeprefix("control-"))
    except ValueError:
        return None


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows os.kill(pid, 0) is not a harmless existence probe: signals
        # other than CTRL events are implemented with TerminateProcess.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        get_exit_code = kernel32.GetExitCodeProcess
        close_handle = kernel32.CloseHandle
        try:
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            get_exit_code.restype = wintypes.BOOL
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
        except AttributeError:
            # Test doubles may expose plain bound methods instead of ctypes funcs.
            pass
        handle = open_process(
            process_query_limited_information, False, wintypes.DWORD(pid)
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            state = proc_stat.read_text(encoding="utf-8").split()[2]
        except (OSError, IndexError):
            state = ""
        if state in {"Z", "X"}:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def list_control_files(
    base_dir: Path | str | None = None,
    *,
    live_only: bool = False,
) -> list[Path]:
    directory = control_dir(base_dir)
    if not directory.exists():
        return []
    result = []
    for path in directory.glob("control-*.cmd"):
        pid = _control_pid(path)
        if pid is None or (live_only and not pid_is_alive(pid)):
            continue
        result.append(path)
    return sorted(result)


def write_control(
    pid: int,
    cmd: str,
    base_dir: Path | str | None = None,
) -> bool:
    try:
        if cmd not in {"on", "off"}:
            raise ValueError(f"unsupported trace command: {cmd}")
        path = control_path_for(pid, base_dir)
        path.write_text(cmd, encoding="utf-8")
        return True
    except Exception:
        return False


def write_control_all(
    cmd: str,
    base_dir: Path | str | None = None,
    *,
    live_only: bool = True,
) -> int:
    """Write a command only to controls owned by one trace run."""
    count = 0
    for path in list_control_files(base_dir, live_only=live_only):
        try:
            pid = _control_pid(path)
            if pid is not None and write_control(pid, cmd, base_dir):
                count += 1
        except Exception:
            pass
    return count


def cleanup_stale_controls(base_dir: Path | str | None = None) -> int:
    count = 0
    for path in list_control_files(base_dir, live_only=False):
        pid = _control_pid(path)
        if pid is None or pid_is_alive(pid):
            continue
        try:
            path.unlink()
            status_path_for(pid, base_dir).unlink(missing_ok=True)
            count += 1
        except OSError:
            pass
    return count


async def set_trace_state(
    state: str,
    base_dir: Path | str,
    *,
    features: list[str] | None = None,
    timeout: float = 5.0,
) -> dict:
    """Set one run's desired state and wait for stable native acknowledgement."""
    import asyncio

    base = _base_dir(base_dir)
    write_desired_state(state, base)
    feature_set = set(features or [])
    if "control_ack" not in feature_set:
        count = write_control_all(state, base)
        await asyncio.sleep(0.45 if state == "off" else 0.25)
        return {"count": count, "acknowledged": False}

    deadline = time.monotonic() + timeout
    previous_pids: set[int] | None = None
    stable_rounds = 0
    touched: set[int] = set()
    data_loss: set[int] = set()
    while time.monotonic() < deadline:
        controls = list_control_files(base, live_only=True)
        pids: set[int] = set()
        pending: list[int] = []
        errors: list[int] = []
        for path in controls:
            pid = _control_pid(path)
            if pid is None:
                continue
            pids.add(pid)
            status = read_control_status(pid, base)
            if status and status[2] == "write_error":
                data_loss.add(pid)
            if status and status[0] == "error" and state == "on":
                errors.append(pid)
                continue
            if not status or status[0] != state:
                pending.append(pid)
                if write_control(pid, state, base):
                    touched.add(pid)
        if errors:
            return {
                "error": "PropertyTracer native process reported an I/O error.",
                "pids": errors,
            }
        all_acknowledged = bool(pids) and not pending
        if all_acknowledged and pids == previous_pids:
            stable_rounds += 1
        else:
            stable_rounds = 0
        if stable_rounds >= 2:
            return {
                "count": len(touched or pids),
                "acknowledged": True,
                "pids": sorted(pids),
                "data_loss_pids": sorted(data_loss),
            }
        previous_pids = pids
        await asyncio.sleep(0.05)
    return {
        "error": f"Timed out waiting for PropertyTracer '{state}' acknowledgement.",
        "pids": sorted(previous_pids or set()),
        "data_loss_pids": sorted(data_loss),
    }


def _trace_file_identity(path: Path) -> tuple[int, int]:
    try:
        pid_text, session_text = path.stem.rsplit("_", 1)
        return int(pid_text), int(session_text)
    except (ValueError, IndexError):
        return -1, -1


def _trace_directories(
    base_dir: Path | str | None = None,
    *,
    include_all_runs: bool = False,
) -> list[Path]:
    if base_dir is not None:
        return [traces_dir(base_dir)]
    directories = [TRACES_DIR]
    if include_all_runs and RUNS_DIR.exists():
        directories.extend(path / "traces" for path in RUNS_DIR.iterdir() if path.is_dir())
    return directories


def list_session_files(
    pid: int | None = None,
    base_dir: Path | str | None = None,
    *,
    include_all_runs: bool = False,
) -> list[Path]:
    pattern = f"{pid}_*.jsonl" if pid else "*.jsonl"
    files: list[tuple[float, int, int, Path]] = []
    for directory in _trace_directories(base_dir, include_all_runs=include_all_runs):
        if not directory.exists():
            continue
        for f in directory.glob(pattern):
            file_pid, session_id = _trace_file_identity(f)
            if file_pid < 0 or session_id < 0:
                continue
            try:
                modified = f.stat().st_mtime
            except OSError:
                continue
            files.append((modified, file_pid, session_id, f))
    files.sort()
    return [f for _, _, _, f in files]


def load_events(
    jsonl_path: Path,
    *,
    annotate: bool = False,
    limit: int | None = None,
    max_bytes: int | None = None,
) -> list[dict]:
    events = []
    if not jsonl_path.exists():
        return events
    file_pid, session_id = _trace_file_identity(jsonl_path)
    bytes_read = 0
    with open(jsonl_path, "rb") as f:
        for raw_line in f:
            if limit is not None and len(events) >= limit:
                break
            if max_bytes is not None and bytes_read + len(raw_line) > max_bytes:
                break
            bytes_read += len(raw_line)
            try:
                line = raw_line.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if annotate:
                event = {
                    **event,
                    "_pid": file_pid,
                    "_session": session_id,
                    "_file": str(jsonl_path),
                }
            events.append(event)
    return events


def sort_events(events: list[dict]) -> list[dict]:
    """Deterministically merge protocol-v1 and extended events."""
    ordered = sorted(
        events,
        key=lambda event: (
            event.get("w", 0) if event.get("w") is not None else 0,
            event.get("t", 0),
            event.get("_pid", -1),
            event.get("_session", -1),
            event.get("q", 0),
        ),
    )
    session_origins = [
        event["w"] - event.get("u", 0)
        for event in ordered
        if event.get("w") is not None
    ]
    if session_origins:
        origin = min(session_origins)
        for event in ordered:
            if event.get("w") is not None:
                event["_global_ms"] = max(0, (event["w"] - origin) // 1000)
            else:
                event["_global_ms"] = event.get("t", 0)
    return ordered


def _event_ms(event: dict) -> int:
    return int(event.get("_global_ms", event.get("t", 0)))


def cleanup_old_traces(keep_days: int = 7) -> int:
    cutoff = time.time() - keep_days * 86400
    count = 0
    for f in list_session_files(include_all_runs=True):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                count += 1
        except OSError:
            continue
    return count


def cleanup_old_runs(keep_days: int = 7) -> int:
    """Remove expired private runs only when none of their PIDs are alive."""
    if not RUNS_DIR.exists():
        return 0
    import shutil

    cutoff = time.time() - keep_days * 86400
    count = 0
    for run in RUNS_DIR.iterdir():
        if not run.is_dir() or list_control_files(run, live_only=True):
            continue
        try:
            newest = max(
                [run.stat().st_mtime]
                + [path.stat().st_mtime for path in run.rglob("*")]
            )
        except OSError:
            continue
        if newest >= cutoff:
            continue
        try:
            shutil.rmtree(run)
            count += 1
        except OSError:
            pass
    return count


def cleanup_traces(base_dir: Path | str | None = None) -> int:
    """Clean trace files for one run only (never other browser launches)."""
    count = 0
    for f in list_session_files(base_dir=base_dir):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


def cleanup_values(base_dir: Path | str | None = None) -> int:
    directory = values_dir(base_dir)
    if not directory.exists():
        return 0
    count = 0
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    return count


# ====================== Aggregation views ======================

def build_summary(events: list[dict], duration_s: int) -> dict:
    by_path: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "first_ms": None, "last_ms": None,
    })
    by_object: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    by_site: dict[str, int] = defaultdict(int)
    by_process: dict[str, int] = defaultdict(int)

    for e in events:
        obj = e.get("o", "")
        prop = e.get("p", "")
        path = f"{obj}.{prop}"
        ts = _event_ms(e)

        entry = by_path[path]
        entry["count"] += 1
        if entry["first_ms"] is None or ts < entry["first_ms"]:
            entry["first_ms"] = ts
        if entry["last_ms"] is None or ts > entry["last_ms"]:
            entry["last_ms"] = ts
        by_object[obj] += 1
        kind = {0: "get", 1: "set", 2: "call"}.get(e.get("k", 0), "unknown")
        by_kind[kind] += 1
        if e.get("s"):
            by_site[str(e["s"])] += 1
        if e.get("_pid", -1) >= 0:
            by_process[str(e["_pid"])] += 1

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
        "by_kind": dict(sorted(by_kind.items(), key=lambda x: -x[1])),
        "by_site": dict(sorted(by_site.items(), key=lambda x: -x[1])),
        "by_process": dict(sorted(by_process.items(), key=lambda x: -x[1])),
    }


def build_timeline(events: list[dict], duration_s: int, bucket_ms: int) -> dict:
    if not events:
        return {"mode": "timeline", "duration_s": duration_s,
                "bucket_ms": bucket_ms, "buckets": []}

    requested_bucket_ms = bucket_ms
    max_ms = max(_event_ms(e) for e in events)
    max_buckets = 10000
    if (max_ms // bucket_ms) + 1 > max_buckets:
        bucket_ms = max(bucket_ms, (max_ms // max_buckets) + 1)
    n_buckets = (max_ms // bucket_ms) + 1
    buckets = [
        {"from_ms": i * bucket_ms, "to_ms": (i + 1) * bucket_ms,
         "events": 0, "new_properties": []}
        for i in range(n_buckets)
    ]

    seen: set[str] = set()
    for e in events:
        ts = _event_ms(e)
        idx = ts // bucket_ms
        if idx >= n_buckets:
            continue
        path = f"{e.get('o', '')}.{e.get('p', '')}"
        buckets[idx]["events"] += 1
        if path not in seen:
            seen.add(path)
            buckets[idx]["new_properties"].append(path)

    return {
        "mode": "timeline",
        "duration_s": duration_s,
        "bucket_ms": bucket_ms,
        "requested_bucket_ms": requested_bucket_ms,
        "bucket_resized": bucket_ms != requested_bucket_ms,
        "buckets": buckets,
    }


def build_sequence(events: list[dict], limit: int) -> dict:
    truncated = len(events) > limit
    shown = events[:limit]
    return {
        "mode": "sequence",
        "total_events": len(events),
        "returned": len(shown),
        "truncated": truncated,
        "events": [
            {"idx": i, "ms": _event_ms(e),
             "us": e.get("u"), "wall_us": e.get("w"),
             "seq": e.get("q"), "pid": e.get("_pid"),
             "session": e.get("_session"), "site": e.get("s"),
             "path": f"{e.get('o', '')}.{e.get('p', '')}",
             "kind": {0: "get", 1: "set", 2: "call"}.get(e.get("k", 0), "?"),
             "v": e.get("v", "")}
            for i, e in enumerate(shown)
        ],
    }


def filter_events(
    events: list[dict],
    filter_object: str | None = None,
    search_query: str | None = None,
    filter_kind: str | None = None,
    filter_site: str | None = None,
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
            or q in str(e.get("s", "")).lower()
        ]
    if filter_kind:
        expected = {"get": 0, "set": 1, "call": 2}.get(filter_kind.lower())
        if expected is None:
            return []
        events = [e for e in events if e.get("k", 0) == expected]
    if filter_site:
        q = filter_site.lower()
        events = [e for e in events if q in str(e.get("s", "")).lower()]
    return events
