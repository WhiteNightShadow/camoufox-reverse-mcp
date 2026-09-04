from __future__ import annotations

import json
import shutil
import subprocess
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from camoufox_reverse_mcp.browser import (
    BrowserManager,
    MAX_TRACE_EVENTS,
    MAX_TRACE_PATHS,
)
from camoufox_reverse_mcp.tools.debugging import evaluate_js
from camoufox_reverse_mcp.tools.hooking import (
    _build_installer_core,
    get_trace_data,
    hook_function,
)
from camoufox_reverse_mcp.tools.navigation import get_page_info
from camoufox_reverse_mcp.utils.frames import list_frame_metadata, resolve_frame
from camoufox_reverse_mcp.utils.worlds import evaluate_in_world


class FakeFrame:
    def __init__(self, url: str, name: str = "", parent=None):
        self.url = url
        self.name = name
        self.parent_frame = parent
        self.evaluate = AsyncMock()


class FakePage:
    def __init__(self, frames: list[FakeFrame] | None = None):
        self.frames = frames or []
        self.main_frame = self.frames[0] if self.frames else None
        self.url = self.main_frame.url if self.main_frame else "about:blank"
        self.viewport_size = {"width": 1200, "height": 800}
        self.evaluate = AsyncMock()
        self.evaluate_handle = AsyncMock()
        self.title = AsyncMock(return_value="test")


def test_frame_metadata_and_deterministic_selection():
    main = FakeFrame("https://example.test/", "main")
    child = FakeFrame("https://cdn.example.test/frame", "worker", main)
    nested = FakeFrame("https://nested.example.test/", "nested", child)
    page = FakePage([main, child, nested])

    metadata = list_frame_metadata(page)
    assert metadata == [
        {
            "index": 0,
            "url": "https://example.test/",
            "name": "main",
            "is_main": True,
            "parent_index": None,
        },
        {
            "index": 1,
            "url": "https://cdn.example.test/frame",
            "name": "worker",
            "is_main": False,
            "parent_index": 0,
        },
        {
            "index": 2,
            "url": "https://nested.example.test/",
            "name": "nested",
            "is_main": False,
            "parent_index": 1,
        },
    ]
    selected, selected_meta = resolve_frame(page, frame_url="https://cdn.*")
    assert selected is child
    assert selected_meta["index"] == 1
    assert resolve_frame(page, frame_name="nested")[0] is nested
    assert resolve_frame(page, frame_index=0)[0] is main


def test_frame_selection_rejects_ambiguous_and_missing():
    main = FakeFrame("https://example.test/")
    one = FakeFrame("https://cdn.test/a", "same", main)
    two = FakeFrame("https://cdn.test/b", "same", main)
    page = FakePage([main, one, two])

    with pytest.raises(ValueError, match="frame_ambiguous"):
        resolve_frame(page, frame_name="same")
    with pytest.raises(ValueError, match="frame_not_found"):
        resolve_frame(page, frame_url="https://missing.test/")
    with pytest.raises(ValueError, match="out of range"):
        resolve_frame(page, frame_index=9)


def test_frame_glob_supports_only_star_and_question_consistently():
    main = FakeFrame("https://a.test/frame")
    page = FakePage([main])
    assert resolve_frame(page, frame_url="https://?.test/*")[0] is main
    with pytest.raises(ValueError, match="frame_not_found"):
        resolve_frame(page, frame_url="https://[ab].test/*")


@pytest.mark.asyncio
async def test_native_main_world_uses_sync_outer_sentinel():
    target = SimpleNamespace(
        evaluate=AsyncMock(
            return_value={"__mcp_native_ok": True, "value": {"result": 42}}
        )
    )
    value, backend, warning = await evaluate_in_world(
        target, "async () => ({result: 42})", "main"
    )

    sent = target.evaluate.await_args.args[0]
    assert sent.startswith("mw:() => (async () =>")
    assert value == {"result": 42}
    assert backend == "camoufox_native"
    assert warning is None


@pytest.mark.asyncio
async def test_native_empty_result_uses_explicit_wrapped_fallback():
    target = SimpleNamespace(evaluate=AsyncMock())
    target.evaluate.side_effect = [
        {},
        {"result": "page-global"},
    ]

    value, backend, warning = await evaluate_in_world(
        target, "() => ({result: window.siteGlobal})", "main"
    )

    fallback = target.evaluate.await_args_list[1].args[0]
    assert "window.wrappedJSObject" in fallback
    assert ")()" in fallback
    assert value == {"result": "page-global"}
    assert backend == "wrappedJSObject"
    assert "fallback" in warning.lower()


@pytest.mark.asyncio
async def test_main_world_fail_closed_when_both_channels_fail():
    target = SimpleNamespace(
        evaluate=AsyncMock(side_effect=[RuntimeError("native"), RuntimeError("xray")])
    )
    with pytest.raises(RuntimeError, match="both Camoufox native channel"):
        await evaluate_in_world(target, "() => 1", "main")


@pytest.mark.asyncio
async def test_evaluate_js_main_preserves_cleaning_and_reports_backend():
    page = FakePage()
    page.evaluate.return_value = {
        "__mcp_native_ok": True,
        "value": {"result": '{"ok": true}', "type": "string"},
    }
    manager = SimpleNamespace(get_active_page=AsyncMock(return_value=page))

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager", manager):
        result = await evaluate_js("window.siteGlobal", world="main")

    assert result["type"] == "json"
    assert result["value"] == {"ok": True}
    assert result["world"] == "main"
    assert result["execution_backend"] == "camoufox_native"


@pytest.mark.asyncio
async def test_evaluate_js_targets_selected_cross_origin_frame():
    main = FakeFrame("https://host.test/")
    child = FakeFrame("https://cross.test/frame", "target", main)
    child.evaluate.return_value = {"result": 7, "type": "number"}
    page = FakePage([main, child])
    manager = SimpleNamespace(get_active_page=AsyncMock(return_value=page))

    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager", manager):
        result = await evaluate_js("window.answer", frame_name="target")

    assert result["value"] == 7
    child.evaluate.assert_awaited_once()
    page.evaluate.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"result": "9007199254740993", "type": "bigint"}, "9007199254740993"),
        ({"result": "NaN", "type": "number", "number_special": "NaN"}, "NaN"),
        ({"result": "Infinity", "type": "number", "number_special": "Infinity"}, "Infinity"),
        ({"result": "-Infinity", "type": "number", "number_special": "-Infinity"}, "-Infinity"),
        ({"result": "-0", "type": "number", "number_special": "-0"}, "-0"),
    ],
)
@pytest.mark.asyncio
async def test_evaluate_js_special_primitives(payload, expected):
    page = FakePage()
    page.evaluate.return_value = payload
    manager = SimpleNamespace(get_active_page=AsyncMock(return_value=page))
    with patch("camoufox_reverse_mcp.tools.debugging.browser_manager", manager):
        result = await evaluate_js("0")
    assert result["value"] == expected
    json.dumps(result, allow_nan=False)
    if payload.get("number_special"):
        assert result["number_special"] == payload["number_special"]


@pytest.mark.asyncio
async def test_intercept_persistent_is_registered_and_waits_bounded():
    page = FakePage()
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        add_persistent_script=AsyncMock(),
        _persistent_scripts=[],
    )
    captured = {}

    async def fake_evaluate(target, script, world, **kwargs):
        captured["script"] = script
        captured["world"] = world
        return {"ok": True}, "isolated", None

    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch(
            "camoufox_reverse_mcp.tools.hooking.evaluate_in_world",
            side_effect=fake_evaluate,
        ),
    ):
        result = await hook_function(
            "window.lateFn",
            mode="intercept",
            hook_code="window.seen = arguments[0]",
            persistent=True,
        )

    assert result["status"] == "hooked"
    assert result["waited_ms"] == 5000
    manager.add_persistent_script.assert_awaited_once()
    init_script = manager.add_persistent_script.await_args.args[1]
    assert init_script.lstrip().startswith("(async () =>")
    assert "window.wrappedJSObject" not in init_script  # isolated-compatible default
    assert "window.seen = arguments[0]" in init_script
    assert "Date.now() + 5000" in captured["script"]


@pytest.mark.asyncio
async def test_main_persistent_hook_creates_wrapper_through_wrapped_eval():
    page = FakePage()
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        add_persistent_script=AsyncMock(),
        _persistent_scripts=[],
    )
    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch(
            "camoufox_reverse_mcp.tools.hooking.evaluate_in_world",
            new=AsyncMock(return_value=({"ok": True}, "camoufox_native", None)),
        ),
    ):
        result = await hook_function(
            "window.pageFn", mode="trace", persistent=True, world="main"
        )

    init_script = manager.add_persistent_script.await_args.args[1]
    assert init_script.lstrip().startswith("(async () =>")
    assert "window.wrappedJSObject" in init_script
    assert "mainWindow.eval" in init_script
    assert result["world"] == "main"


@pytest.mark.asyncio
async def test_missing_hook_target_is_not_reported_as_success():
    page = FakePage()
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        add_persistent_script=AsyncMock(),
        _persistent_scripts=[],
    )
    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch(
            "camoufox_reverse_mcp.tools.hooking.evaluate_in_world",
            new=AsyncMock(
                return_value=(
                    {"ok": False, "error": "target_not_found"},
                    "isolated",
                    None,
                )
            ),
        ),
    ):
        result = await hook_function("window.missing", wait_timeout_ms=25)

    assert result["error"] == "target_not_found"
    assert "status" not in result


@pytest.mark.asyncio
async def test_persistent_missing_target_reports_pending_registration():
    page = FakePage()
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        add_persistent_script=AsyncMock(),
        _persistent_scripts=[],
    )
    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch(
            "camoufox_reverse_mcp.tools.hooking.evaluate_in_world",
            new=AsyncMock(
                return_value=(
                    {"ok": False, "error": "target_not_found"},
                    "camoufox_native",
                    None,
                )
            ),
        ),
    ):
        result = await hook_function(
            "window.lateFunction",
            mode="trace",
            persistent=True,
            world="main",
        )

    assert result["status"] == "pending"
    assert result["install_state"] == "pending"
    assert result["pending_reason"] == "target_not_found"
    assert result["persistent_registered"] is True
    assert "error" not in result


@pytest.mark.asyncio
async def test_persistent_frame_index_rejected_as_unstable():
    result = await hook_function("window.fn", persistent=True, frame_index=1)
    assert "current frame-tree snapshot" in result["error"]


def _watcher_core(path: str, wait_timeout_ms: int = 100) -> str:
    return _build_installer_core(
        function_path=path,
        mode="trace",
        hook_code="",
        position="before",
        non_overridable=False,
        log_args=True,
        log_return=True,
        log_stack=False,
        max_captures=5,
        wait_timeout_ms=wait_timeout_ms,
        poll_interval_ms=10,
        frame_selector=None,
        frame_metadata={"index": 0, "parent_index": None},
        world="main",
        install_id="node-watcher-test",
        watch_assignments=True,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_assignment_watcher_captures_same_task_first_call():
    core = _watcher_core("window.FEILIN.initFeiLin")
    script = f"""
    globalThis.window = globalThis;
    window.top = window; window.frames = []; window.name = '';
    globalThis.location = {{href: 'https://test/'}};
    const oldLog = console.log; console.log = () => {{}};
    const pending = {core};
    window.FEILIN = {{}};
    window.FEILIN.initFeiLin = function(value) {{ return value + 1; }};
    const returned = window.FEILIN.initFeiLin(41);
    pending.then(result => oldLog(JSON.stringify({{
        result, returned, traces: window.__mcp_traces
    }}))).catch(error => {{ console.error(error); process.exitCode = 1; }});
    """
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed.stdout)
    assert result["returned"] == 42
    assert result["result"]["ok"] is True
    traces = result["traces"]["window.FEILIN.initFeiLin"]
    assert len(traces) == 1
    assert traces[0]["args"] == "[41]"
    assert traces[0]["returnValue"] == "42"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_nonreplaceable_target_fails_instead_of_reporting_hooked():
    core = _watcher_core("window.locked", wait_timeout_ms=20)
    script = f"""
    globalThis.window = globalThis;
    window.top = window; window.frames = []; window.name = '';
    globalThis.location = {{href: 'https://test/'}};
    const original = function(value) {{ return value + 1; }};
    Object.defineProperty(window, 'locked', {{
      value: original, configurable: false, writable: false
    }});
    {core}.then(result => console.log(JSON.stringify({{
        result,
        unchanged: window.locked === original,
        returned: window.locked(41),
        traces: window.__mcp_traces || {{}}
    }}))).catch(error => {{ console.error(error); process.exitCode = 1; }});
    """
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed.stdout)
    assert result["result"]["error"] == "target_not_replaceable"
    assert result["unchanged"] is True
    assert result["returned"] == 42
    assert not result["traces"].get("window.locked")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_assignment_watcher_restores_missing_property_after_timeout():
    core = _watcher_core("window.NEVER.fn", wait_timeout_ms=20)
    script = f"""
    globalThis.window = globalThis;
    window.top = window; window.frames = []; window.name = '';
    globalThis.location = {{href: 'https://test/'}};
    {core}.then(result => console.log(JSON.stringify({{
        result,
        hasOwn: Object.prototype.hasOwnProperty.call(window, 'NEVER'),
        descriptor: Object.getOwnPropertyDescriptor(window, 'NEVER') || null
    }}))).catch(error => {{ console.error(error); process.exitCode = 1; }});
    """
    completed = subprocess.run(
        ["node", "-e", script], check=True, capture_output=True, text=True
    )
    result = json.loads(completed.stdout)
    assert result["result"]["error"] == "target_not_found"
    assert result["hasOwn"] is False
    assert result["descriptor"] is None


@pytest.mark.asyncio
async def test_trace_cache_is_world_and_frame_scoped_and_clear_stays_bounded():
    main_frame = FakeFrame("https://a.test/")
    main_frame.evaluate.return_value = {"__mcp_native_ok": True, "value": {}}
    page = FakePage([main_frame])
    page.evaluate.return_value = {}
    cache = deque(
        [
            {
                "traceId": "iso",
                "world": "isolated",
                "frame": {"url": "https://a.test/", "name": "", "index": 0},
            },
            {
                "traceId": "main-a",
                "world": "main",
                "frame": {"url": "https://a.test/", "name": "", "index": 0},
            },
            {
                "traceId": "main-b",
                "world": "main",
                "frame": {"url": "https://b.test/", "name": "child", "index": 1},
            },
        ],
        maxlen=MAX_TRACE_EVENTS,
    )
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        _persistent_traces={"window.fn": cache},
    )
    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch(
            "camoufox_reverse_mcp.tools.hooking.evaluate_in_world",
            new=AsyncMock(return_value=({}, "camoufox_native", None)),
        ),
    ):
        result = await get_trace_data(
            "window.fn",
            world="main",
            frame_url="https://a.test/",
            clear=True,
        )

    assert [item["traceId"] for item in result["window.fn"]] == ["main-a"]
    remaining = manager._persistent_traces["window.fn"]
    assert isinstance(remaining, deque)
    assert remaining.maxlen == MAX_TRACE_EVENTS
    assert [item["traceId"] for item in remaining] == ["iso", "main-b"]


@pytest.mark.asyncio
async def test_clear_without_selector_clears_every_current_frame_and_cache():
    main = FakeFrame("https://host.test/")
    child = FakeFrame("https://child.test/", "child", main)
    main.evaluate.side_effect = [
        {"__mcp_native_ok": True, "value": {"window.fn": [{"traceId": "main", "world": "main", "frame": {"url": main.url, "name": "", "index": 0, "is_main": True}}]}},
        {"__mcp_native_ok": True, "value": None},
    ]
    child.evaluate.side_effect = [
        {"__mcp_native_ok": True, "value": {"window.fn": [{"traceId": "child", "world": "main", "frame": {"url": child.url, "name": "child", "index": None, "is_main": False}}]}},
        {"__mcp_native_ok": True, "value": None},
    ]
    page = FakePage([main, child])
    cached_child = {"traceId": "cached", "world": "main", "frame": {"url": child.url, "name": "child", "index": None, "is_main": False}}
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        _persistent_traces={"window.fn": deque([cached_child])},
        _persistent_trace_order=deque([("window.fn", cached_child)]),
    )
    with patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager):
        result = await get_trace_data("window.fn", world="main", clear=True)

    assert {item["traceId"] for item in result["window.fn"]} == {"main", "child", "cached"}
    assert main.evaluate.await_count == 2
    assert child.evaluate.await_count == 2
    assert manager._persistent_traces == {}
    assert list(manager._persistent_trace_order) == []
    main.evaluate.side_effect = None
    child.evaluate.side_effect = None
    main.evaluate.return_value = {"__mcp_native_ok": True, "value": {}}
    child.evaluate.return_value = {"__mcp_native_ok": True, "value": {}}
    with patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager):
        after_clear = await get_trace_data("window.fn", world="main", frame_index=1)
    assert not after_clear.get("window.fn")


@pytest.mark.asyncio
async def test_persistent_child_trace_can_be_selected_by_current_frame_index():
    main = FakeFrame("https://host.test/")
    child = FakeFrame("https://child.test/", "child", main)
    child.evaluate.return_value = {"__mcp_native_ok": True, "value": {}}
    page = FakePage([main, child])
    cached_child = {"traceId": "child", "world": "main", "frame": {"url": child.url, "name": "child", "index": None, "is_main": False}}
    manager = SimpleNamespace(
        get_active_page=AsyncMock(return_value=page),
        _persistent_traces={"window.fn": deque([cached_child])},
        _persistent_trace_order=deque([("window.fn", cached_child)]),
    )
    with patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager):
        result = await get_trace_data("window.fn", world="main", frame_index=1)

    assert len(result["window.fn"]) == 1
    assert result["window.fn"][0]["frame"]["index"] == 1
    assert result["window.fn"][0]["frame"]["parent_index"] == 0


def test_browser_manager_trace_cache_is_bounded_and_keeps_frame_metadata():
    manager = BrowserManager()
    for index in range(MAX_TRACE_EVENTS + 25):
        payload = {
            "__path__": "window.fn",
            "traceId": str(index),
            "world": "main",
            "frame": {"url": "https://example.test/", "name": "", "index": 0},
        }
        manager._on_console(
            SimpleNamespace(
                text="__MCP_TRACE__:" + json.dumps(payload),
                type="log",
                location={},
            )
        )

    entries = manager._persistent_traces["window.fn"]
    assert isinstance(entries, deque)
    assert len(entries) == MAX_TRACE_EVENTS
    assert len(manager._persistent_trace_order) == MAX_TRACE_EVENTS
    assert entries[-1]["frame"]["url"] == "https://example.test/"


def test_browser_manager_trace_path_count_is_globally_bounded():
    manager = BrowserManager()
    for index in range(MAX_TRACE_PATHS + 25):
        payload = {
            "__path__": f"window.fn{index}",
            "traceId": str(index),
            "world": "main",
            "frame": {"url": "https://example.test/", "name": "", "index": 0},
        }
        manager._on_console(
            SimpleNamespace(
                text="__MCP_TRACE__:" + json.dumps(payload),
                type="log",
                location={},
            )
        )

    assert len(manager._persistent_traces) == MAX_TRACE_PATHS
    assert "window.fn0" not in manager._persistent_traces
    assert sum(len(entries) for entries in manager._persistent_traces.values()) <= MAX_TRACE_EVENTS


@pytest.mark.asyncio
async def test_get_page_info_exposes_current_frame_snapshot():
    main = FakeFrame("https://example.test/")
    child = FakeFrame("https://frame.test/", "child", main)
    page = FakePage([main, child])
    manager = SimpleNamespace(get_active_page=AsyncMock(return_value=page))
    with patch("camoufox_reverse_mcp.tools.navigation.browser_manager", manager):
        result = await get_page_info()

    assert result["frames"][0]["is_main"] is True
    assert result["frames"][1]["parent_index"] == 0
    assert result["frames"][1]["name"] == "child"
