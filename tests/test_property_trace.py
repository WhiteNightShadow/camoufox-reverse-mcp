import asyncio
import ctypes
import json
import os
from pathlib import Path

import pytest

from camoufox_reverse_mcp import property_trace


def _configure_cache(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setattr(property_trace, "CACHE_DIR", cache)
    monkeypatch.setattr(property_trace, "CONTROL_DIR", cache / "control")
    monkeypatch.setattr(property_trace, "TRACES_DIR", cache / "traces")
    monkeypatch.setattr(property_trace, "RUNS_DIR", cache / "runs")
    return cache


def test_trace_runs_are_isolated_and_controls_ignore_dead_pids(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    one = property_trace.create_trace_run()
    two = property_trace.create_trace_run()
    assert one != two
    config = property_trace.build_property_trace_config(one)
    assert config["logDir"] == str(one)
    assert (one / "desired.state").read_text(encoding="utf-8") == "on"

    live = property_trace.control_path_for(os.getpid(), one)
    dead = property_trace.control_path_for(99999999, one)
    other = property_trace.control_path_for(os.getpid(), two)
    live.write_text("off", encoding="utf-8")
    dead.write_text("off", encoding="utf-8")
    other.write_text("off", encoding="utf-8")

    assert property_trace.write_control_all("on", one) == 1
    assert live.read_text(encoding="utf-8") == "on"
    assert dead.read_text(encoding="utf-8") == "off"
    assert other.read_text(encoding="utf-8") == "off"
    assert property_trace.cleanup_stale_controls(one) == 1
    assert not dead.exists()


def test_windows_pid_probe_never_uses_os_kill(monkeypatch):
    class Kernel32:
        def OpenProcess(self, _access, _inherit, _pid):
            return 123

        def GetExitCodeProcess(self, _handle, pointer):
            pointer._obj.value = 259
            return 1

        def CloseHandle(self, _handle):
            return 1

    monkeypatch.setattr(property_trace.os, "name", "nt")
    monkeypatch.setattr(
        property_trace.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("os.kill is destructive")),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32(), raising=False)
    assert property_trace.pid_is_alive(1234) is True


@pytest.mark.skipif(not Path("/proc").exists(), reason="Linux /proc zombie check")
def test_linux_zombie_pid_is_not_treated_as_live():
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        assert property_trace.pid_is_alive(pid) is False
    finally:
        os.waitpid(pid, 0)


def test_extended_events_are_sorted_annotated_and_aggregated(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    run = property_trace.create_trace_run()
    first = property_trace.traces_dir(run) / "20_0.jsonl"
    second = property_trace.traces_dir(run) / "10_0.jsonl"
    first.write_text(
        json.dumps({"o": "document", "p": "cookie.set", "v": "", "t": 1,
                    "k": 1, "u": 1000, "w": 1051000, "q": 0,
                    "s": "document.cookie.set@dom/base/Document.cpp"}) + "\n",
        encoding="utf-8",
    )
    second.write_text(
        json.dumps({"o": "canvas", "p": "getContext", "v": "", "t": 99,
                    "k": 2, "u": 99000, "w": 1099000, "q": 0,
                    "s": "canvas.getContext@dom/html/HTMLCanvasElement.cpp"}) + "\n",
        encoding="utf-8",
    )

    events = []
    for path in property_trace.list_session_files(base_dir=run):
        events.extend(property_trace.load_events(path, annotate=True))
    events = property_trace.sort_events(events)
    assert [event["o"] for event in events] == ["document", "canvas"]
    assert [event["_pid"] for event in events] == [20, 10]
    assert [event["_global_ms"] for event in events] == [51, 99]

    summary = property_trace.build_summary(events, 1)
    assert summary["by_kind"] == {"set": 1, "call": 1}
    assert summary["by_process"] == {"10": 1, "20": 1}
    assert len(summary["by_site"]) == 2

    calls = property_trace.filter_events(events, filter_kind="call")
    assert [event["o"] for event in calls] == ["canvas"]
    sequence = property_trace.build_sequence(events, 10)
    assert [event["ms"] for event in sequence["events"]] == [51, 99]


def test_timeline_caps_bucket_allocation():
    events = [
        {"o": "window", "p": "innerWidth", "t": 0, "_global_ms": 0},
        {"o": "window", "p": "innerHeight", "t": 1, "_global_ms": 86_400_000},
    ]
    timeline = property_trace.build_timeline(events, 86400, 1)
    assert timeline["bucket_resized"] is True
    assert len(timeline["buckets"]) <= 10000


def test_cleanup_only_removes_selected_run(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    one = property_trace.create_trace_run()
    two = property_trace.create_trace_run()
    one_file = property_trace.traces_dir(one) / "1_0.jsonl"
    two_file = property_trace.traces_dir(two) / "2_0.jsonl"
    one_file.write_text("{}\n", encoding="utf-8")
    two_file.write_text("{}\n", encoding="utf-8")

    assert property_trace.cleanup_traces(one) == 1
    assert not one_file.exists()
    assert two_file.exists()


def test_cleanup_old_traces_never_deletes_from_a_live_run(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    live_run = property_trace.create_trace_run()
    expired_run = property_trace.create_trace_run()
    live_file = property_trace.traces_dir(live_run) / f"{os.getpid()}_0.jsonl"
    expired_file = property_trace.traces_dir(expired_run) / "99999999_0.jsonl"
    live_file.write_text("{}\n", encoding="utf-8")
    expired_file.write_text("{}\n", encoding="utf-8")
    os.utime(live_file, (1, 1))
    os.utime(expired_file, (1, 1))
    property_trace.control_path_for(os.getpid(), live_run).write_text(
        "on", encoding="utf-8"
    )

    assert property_trace.cleanup_old_traces(keep_days=7) == 1
    assert live_file.exists()
    assert not expired_file.exists()


@pytest.mark.asyncio
async def test_trace_tool_start_stop_is_run_scoped(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    from camoufox_reverse_mcp.tools import trace

    run = property_trace.create_trace_run()
    other = property_trace.create_trace_run()
    property_trace.control_path_for(os.getpid(), run).write_text("on", encoding="utf-8")
    property_trace.control_path_for(os.getpid(), other).write_text("on", encoding="utf-8")
    current_file = property_trace.traces_dir(run) / f"{os.getpid()}_0.jsonl"
    other_file = property_trace.traces_dir(other) / f"{os.getpid()}_0.jsonl"
    event = {"o": "canvas", "p": "getContext", "v": "", "t": 1,
             "k": 2, "u": 1000, "w": 100, "q": 0, "s": "canvas.site"}
    current_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
    other_file.write_text(json.dumps(event) + "\n", encoding="utf-8")
    property_trace.values_dir(run).mkdir()
    snapshot = property_trace.values_dir(run) / "old.txt"
    snapshot.write_text("old", encoding="utf-8")

    monkeypatch.setattr(trace.browser_manager, "browser", object())
    monkeypatch.setattr(trace.browser_manager, "_trace_base_dir", run)
    monkeypatch.setattr(trace.browser_manager, "_trace_started_at", None)
    monkeypatch.setattr(
        trace.browser_manager,
        "_runtime_browser",
        {
            "property_trace": True,
            "property_trace_protocol": 1,
            "property_trace_compatible": True,
            "property_trace_hooks": 75,
        },
    )
    monkeypatch.setattr(trace.browser_manager, "_trace_max_events", 100000)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(trace.asyncio, "sleep", no_sleep)
    result = await trace.trace_property_access(action="stop", mode="summary")
    assert result["total_events"] == 1
    assert result["by_kind"] == {"call": 1}
    assert other_file.exists()
    assert property_trace.control_path_for(os.getpid(), other).read_text() == "on"

    started = await trace.trace_property_access(action="start")
    assert started["status"] == "started"
    assert not current_file.exists()
    assert not snapshot.exists()
    assert other_file.exists()


@pytest.mark.asyncio
async def test_trace_tool_waits_for_native_control_ack(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    from camoufox_reverse_mcp.tools import trace

    run = property_trace.create_trace_run()
    pid = os.getpid()
    property_trace.control_path_for(pid, run).write_text("on", encoding="utf-8")
    property_trace.status_path_for(pid, run).write_text("on 0\n", encoding="utf-8")
    monkeypatch.setattr(trace.browser_manager, "browser", object())
    monkeypatch.setattr(trace.browser_manager, "_trace_base_dir", run)
    monkeypatch.setattr(trace.browser_manager, "_trace_started_at", None)
    monkeypatch.setattr(
        trace.browser_manager,
        "_runtime_browser",
        {
            "property_trace": True,
            "property_trace_protocol": 1,
            "property_trace_compatible": True,
            "property_trace_hooks": 75,
            "property_trace_features": ["control_ack"],
        },
    )

    def acknowledged_write(target_pid, state, base_dir):
        assert target_pid == pid
        property_trace.control_path_for(pid, base_dir).write_text(state, encoding="utf-8")
        property_trace.status_path_for(pid, base_dir).write_text(
            f"{state} 1\n", encoding="utf-8"
        )
        return True

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(property_trace, "write_control", acknowledged_write)
    monkeypatch.setattr(trace.asyncio, "sleep", no_sleep)
    result = await trace.trace_property_access(action="start")
    assert result["status"] == "started"
    assert (run / "desired.state").read_text(encoding="utf-8") == "on"
    assert property_trace.read_control_status(pid, run)[0] == "on"


@pytest.mark.asyncio
async def test_cancelled_trace_action_stops_native_run(monkeypatch, tmp_path):
    _configure_cache(monkeypatch, tmp_path)
    from camoufox_reverse_mcp.tools import trace

    run = property_trace.create_trace_run()
    pid = os.getpid()
    property_trace.control_path_for(pid, run).write_text("on", encoding="utf-8")
    property_trace.status_path_for(pid, run).write_text("on 0\n", encoding="utf-8")
    monkeypatch.setattr(trace.browser_manager, "browser", object())
    monkeypatch.setattr(trace.browser_manager, "_trace_base_dir", run)
    monkeypatch.setattr(trace.browser_manager, "_trace_started_at", None)
    monkeypatch.setattr(
        trace.browser_manager,
        "_runtime_browser",
        {"property_trace_features": ["control_ack"]},
    )

    def acknowledged_write(target_pid, state, base_dir):
        property_trace.control_path_for(target_pid, base_dir).write_text(
            state, encoding="utf-8"
        )
        property_trace.status_path_for(target_pid, base_dir).write_text(
            f"{state} 1\n", encoding="utf-8"
        )
        return True

    async def cancelled_impl(**_kwargs):
        trace.browser_manager._trace_started_at = 1.0
        raise asyncio.CancelledError

    monkeypatch.setattr(property_trace, "write_control", acknowledged_write)
    monkeypatch.setattr(trace, "_trace_property_access_impl", cancelled_impl)
    with pytest.raises(asyncio.CancelledError):
        await trace.trace_property_access(action="capture", duration=100)

    assert (run / "desired.state").read_text(encoding="utf-8") == "off"
    assert property_trace.read_control_status(pid, run)[0] == "off"
    assert trace.browser_manager._trace_started_at is None
