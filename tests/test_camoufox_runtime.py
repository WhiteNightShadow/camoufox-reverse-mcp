import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from camoufox_reverse_mcp import camoufox_runtime
from camoufox_reverse_mcp.browser import BrowserManager

REVERSE4_TRACE_FEATURES = [
    "async_buffered_io",
    "event_kind",
    "native_site",
    "wall_time_us",
    "sequence",
    "exclusive_session_files",
    "control_ack",
    "utf8_paths",
    "process_scope",
]


def _write_version(root: Path, *, version="152.0.4", build=None, release=None):
    root.mkdir(parents=True)
    payload = {"version": version}
    if build is not None:
        payload["build"] = build
    if release is not None:
        payload["release"] = release
    (root / "version.json").write_text(json.dumps(payload), encoding="utf-8")


def _installed(root: Path, repo: str, *, active=False):
    metadata = camoufox_runtime._read_browser_metadata(root)
    version = SimpleNamespace(
        version=metadata["version"],
        build=metadata["build"],
    )
    return SimpleNamespace(
        path=root,
        repo_name=repo,
        is_active=active,
        version=version,
    )


def _mock_multiversion(monkeypatch, tmp_path, installs, config=None):
    cache = tmp_path / "cache"
    browsers = cache / "browsers"
    browsers.mkdir(parents=True, exist_ok=True)
    compat_flag = cache / ".0.5_FLAG"
    compat_flag.touch()
    module = SimpleNamespace(
        BROWSERS_DIR=browsers,
        COMPAT_FLAG=compat_flag,
        list_installed=lambda: installs,
        load_config=lambda: dict(config or {}),
    )
    monkeypatch.setattr(camoufox_runtime, "_package_version", lambda: "0.5.5")
    monkeypatch.setattr(camoufox_runtime, "_load_multiversion_module", lambda: module)
    return module


def test_reads_release_or_build_and_capabilities_marker(tmp_path):
    legacy = tmp_path / "legacy"
    _write_version(legacy, release="beta.25")
    (legacy / camoufox_runtime.CAPABILITIES_FILE).write_text(
        json.dumps({"capabilities": ["property_trace"]}),
        encoding="utf-8",
    )

    current = tmp_path / "current"
    _write_version(current, build="beta.28")

    legacy_meta = camoufox_runtime._read_browser_metadata(legacy)
    current_meta = camoufox_runtime._read_browser_metadata(current)

    assert legacy_meta["build"] == "beta.25"
    assert legacy_meta["property_trace"] is True
    assert legacy_meta["property_trace_compatible"] is False
    assert current_meta["build"] == "beta.28"
    assert current_meta["property_trace"] is False


@pytest.mark.parametrize(
    ("reverse_release", "hook_count"),
    [("reverse.4", 75), ("reverse.5", 77)],
)
def test_reads_trace_protocol_and_features(tmp_path, reverse_release, hook_count):
    root = tmp_path / "reverse"
    _write_version(root, build="beta.30")
    (root / camoufox_runtime.CAPABILITIES_FILE).write_text(
        json.dumps({
            "schema": 1,
            "distribution": "WhiteNightShadow/camoufox-reverse",
            "upstream_version": "152.0.4-beta.30",
            "reverse_release": reverse_release,
            "property_trace": True,
            "property_trace_protocol": 1,
            "property_trace_hooks": hook_count,
            "property_trace_features": REVERSE4_TRACE_FEATURES,
        }),
        encoding="utf-8",
    )
    metadata = camoufox_runtime._read_browser_metadata(root)
    assert metadata["property_trace_compatible"] is True
    assert metadata["property_trace_protocol"] == 1
    assert metadata["property_trace_hooks"] == hook_count
    assert metadata["property_trace_features"] == REVERSE4_TRACE_FEATURES
    assert metadata["reverse_release"] == reverse_release


def test_multiversion_runtime_is_read_only_and_resolves_exact_repo(monkeypatch, tmp_path):
    official_root = tmp_path / "cache" / "browsers" / "official" / "152.0.4-beta.28-aabbccdd"
    custom_root = tmp_path / "cache" / "browsers" / "whitenightshadow" / "152.0.4-beta.28-11223344"
    _write_version(official_root, build="beta.28")
    _write_version(custom_root, release="beta.28")
    (custom_root / camoufox_runtime.CAPABILITIES_FILE).write_text(
        json.dumps({"property_trace": True}), encoding="utf-8"
    )
    config_path = tmp_path / "cache" / "config.json"
    config_path.write_text(
        json.dumps({"active_version": "browsers/official/152.0.4-beta.28-aabbccdd"}),
        encoding="utf-8",
    )
    before = config_path.read_bytes()
    installs = [
        _installed(official_root, "official", active=True),
        _installed(custom_root, "whitenightshadow"),
    ]
    _mock_multiversion(
        monkeypatch,
        tmp_path,
        installs,
        {"active_version": "browsers/official/152.0.4-beta.28-aabbccdd"},
    )

    selected = camoufox_runtime.resolve_browser_version("whitenightshadow/beta.28")

    assert selected["path"] == str(custom_root)
    assert selected["firefox_major"] == 152
    assert selected["property_trace"] is True
    assert selected["launch_selector"] == "whitenightshadow/152.0.4-beta.28-11223344"
    assert config_path.read_bytes() == before


def test_selector_must_be_repo_qualified(monkeypatch, tmp_path):
    root = tmp_path / "cache" / "browsers" / "official" / "152.0.4-beta.28"
    _write_version(root, build="beta.28")
    _mock_multiversion(monkeypatch, tmp_path, [_installed(root, "official", active=True)])

    with pytest.raises(ValueError, match="repo-qualified"):
        camoufox_runtime.resolve_browser_version("beta.28")


def test_ambiguous_same_repo_build_requires_folder_selector(monkeypatch, tmp_path):
    one = tmp_path / "cache" / "browsers" / "official" / "152.0.4-beta.28-aaaaaaaa"
    two = tmp_path / "cache" / "browsers" / "official" / "152.0.4-beta.28-bbbbbbbb"
    _write_version(one, build="beta.28")
    _write_version(two, build="beta.28")
    _mock_multiversion(
        monkeypatch,
        tmp_path,
        [_installed(one, "official", active=True), _installed(two, "official")],
    )

    with pytest.raises(ValueError, match="matches multiple"):
        camoufox_runtime.resolve_browser_version("official/beta.28")

    selected = camoufox_runtime.resolve_browser_version(
        "official/152.0.4-beta.28-bbbbbbbb"
    )
    assert selected["path"] == str(two)


def test_selector_requires_active_browser_with_same_major(monkeypatch, tmp_path):
    selected_root = (
        tmp_path / "cache" / "browsers" / "whitenightshadow" / "152.0.4-beta.30"
    )
    _write_version(selected_root, build="beta.30")
    selected_item = _installed(selected_root, "whitenightshadow")

    _mock_multiversion(monkeypatch, tmp_path, [selected_item])
    with pytest.raises(ValueError, match="requires one active"):
        camoufox_runtime.resolve_browser_version("whitenightshadow/beta.30")

    active_root = tmp_path / "cache" / "browsers" / "official" / "135.0.1-beta.24"
    _write_version(active_root, version="135.0.1", build="beta.24")
    _mock_multiversion(
        monkeypatch,
        tmp_path,
        [_installed(active_root, "official", active=True), selected_item],
    )
    with pytest.raises(ValueError, match="Cross-major"):
        camoufox_runtime.resolve_browser_version("whitenightshadow/beta.30")


def test_selector_requires_exact_active_build(monkeypatch, tmp_path):
    active_root = tmp_path / "cache" / "browsers" / "official" / "152.0.4-beta.29"
    selected_root = (
        tmp_path / "cache" / "browsers" / "whitenightshadow" / "152.0.4-beta.30"
    )
    _write_version(active_root, build="beta.29")
    _write_version(selected_root, build="beta.30")
    _mock_multiversion(
        monkeypatch,
        tmp_path,
        [
            _installed(active_root, "official", active=True),
            _installed(selected_root, "whitenightshadow"),
        ],
    )

    with pytest.raises(ValueError, match="must match exactly"):
        camoufox_runtime.resolve_browser_version("whitenightshadow/beta.30")


def test_camoufox_04_explicit_selector_has_clear_error(monkeypatch, tmp_path):
    root = tmp_path / "legacy-cache"
    _write_version(root, version="135.0.1", release="beta.25")
    monkeypatch.setattr(camoufox_runtime, "_package_version", lambda: "0.4.11")
    monkeypatch.setattr(
        camoufox_runtime,
        "_load_pkgman_module",
        lambda: SimpleNamespace(INSTALL_DIR=root),
    )

    runtime = camoufox_runtime.inspect_camoufox_runtime()
    assert runtime["active"]["full_version"] == "135.0.1-beta.25"

    with pytest.raises(ValueError, match="requires Camoufox Python >= 0.5.0"):
        camoufox_runtime.resolve_browser_version("official/beta.25")


class _FakePage:
    url = "about:blank"

    def on(self, *_args):
        pass


class _FakeContext:
    def __init__(self):
        self.pages = [_FakePage()]

    async def add_init_script(self, *_args, **_kwargs):
        pass


class _FakeBrowser:
    def __init__(self):
        self.contexts = [_FakeContext()]


def _install_fake_camoufox(monkeypatch, captured, *, launch_options=None):
    async_api = types.ModuleType("camoufox.async_api")

    class FakeAsyncCamoufox:
        def __init__(self, **kwargs):
            captured["async_kwargs"] = kwargs

        async def __aenter__(self):
            return _FakeBrowser()

        async def __aexit__(self, *_args):
            pass

    async_api.AsyncCamoufox = FakeAsyncCamoufox
    package = types.ModuleType("camoufox")
    package.__path__ = []
    package.async_api = async_api
    monkeypatch.setitem(sys.modules, "camoufox", package)
    monkeypatch.setitem(sys.modules, "camoufox.async_api", async_api)

    if launch_options is not None:
        utils = types.ModuleType("camoufox.utils")
        utils.launch_options = launch_options
        package.utils = utils
        monkeypatch.setitem(sys.modules, "camoufox.utils", utils)


@pytest.mark.asyncio
async def test_browser_launch_passes_selected_browser_without_manual_ff_version(monkeypatch):
    selected = {
        "launch_selector": "whitenightshadow/152.0.4-beta.28-11223344",
        "firefox_major": 152,
        "repo": "whitenightshadow",
    }
    monkeypatch.setattr(
        camoufox_runtime,
        "launch_overrides",
        lambda _selector: (
            {"browser": selected["launch_selector"]},
            selected,
        ),
    )
    captured = {}
    _install_fake_camoufox(monkeypatch, captured)

    manager = BrowserManager()
    result = await manager.launch(
        {"headless": True, "browser_version": "whitenightshadow/beta.28"}
    )

    assert captured["async_kwargs"]["browser"] == selected["launch_selector"]
    assert "ff_version" not in captured["async_kwargs"]
    assert result["browser_runtime"] == selected
    assert (await manager.launch())["browser_runtime"] == selected


@pytest.mark.asyncio
async def test_trace_launch_passes_selection_to_launch_options(monkeypatch, tmp_path):
    selected = {
        "launch_selector": "whitenightshadow/152.0.4-beta.28-11223344",
        "firefox_major": 152,
        "repo": "whitenightshadow",
    }
    monkeypatch.setattr(
        camoufox_runtime,
        "launch_overrides",
        lambda _selector: (
            {"browser": selected["launch_selector"]},
            selected,
        ),
    )
    captured = {}

    def fake_launch_options(**kwargs):
        captured["launch_options_kwargs"] = kwargs
        return {"env": {"CAMOU_CONFIG_1": "{}"}, "headless": kwargs["headless"]}

    _install_fake_camoufox(monkeypatch, captured, launch_options=fake_launch_options)

    from camoufox_reverse_mcp import property_trace

    monkeypatch.setattr(property_trace, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(property_trace, "cleanup_old_traces", lambda **_kwargs: 0)
    monkeypatch.setattr(property_trace, "cleanup_old_runs", lambda **_kwargs: 0)
    trace_run = tmp_path / "run"
    trace_run.mkdir()
    monkeypatch.setattr(property_trace, "create_trace_run", lambda: trace_run)
    monkeypatch.setattr(
        property_trace,
        "build_property_trace_config",
        lambda base, *, objects, max_events: {
            "enabled": True,
            "logDir": str(base),
            "objects": objects,
            "maxEventsPerSession": max_events,
        },
    )

    manager = BrowserManager()
    result = await manager.launch(
        {
            "headless": True,
            "enable_trace": True,
            "browser_version": "whitenightshadow/beta.28",
        }
    )

    assert captured["launch_options_kwargs"]["browser"] == selected["launch_selector"]
    assert "ff_version" not in captured["launch_options_kwargs"]
    assert captured["async_kwargs"]["from_options"]["env"]["CAMOU_CONFIG_1"]
    assert result["browser_runtime"] == selected


@pytest.mark.asyncio
async def test_official_browser_ignores_trace_without_disabling_sandbox(monkeypatch):
    selected = {
        "launch_selector": "official/152.0.4-beta.30-aabbccdd",
        "firefox_major": 152,
        "repo": "official",
        "property_trace": False,
        "capabilities_marker": None,
    }
    monkeypatch.setattr(
        camoufox_runtime,
        "launch_overrides",
        lambda _selector: ({"browser": selected["launch_selector"]}, selected),
    )
    captured = {}
    _install_fake_camoufox(monkeypatch, captured)

    manager = BrowserManager()
    result = await manager.launch(
        {
            "headless": True,
            "enable_trace": True,
            "browser_version": "official/beta.30",
        }
    )

    assert "from_options" not in captured["async_kwargs"]
    assert result["engine_trace"]["requested"] is True
    assert result["engine_trace"]["enabled"] is False
    assert "ignored" in result["warnings"][0]


@pytest.mark.asyncio
async def test_launch_browser_tool_forwards_browser_version(monkeypatch):
    from camoufox_reverse_mcp.tools import navigation

    captured = {}

    async def fake_launch(config):
        captured.update(config)
        return {"status": "launched"}

    monkeypatch.setattr(navigation.browser_manager, "launch", fake_launch)

    result = await navigation.launch_browser(
        headless=True,
        browser_version="official/beta.28",
    )

    assert result == {"status": "launched"}
    assert captured["browser_version"] == "official/beta.28"


@pytest.mark.asyncio
async def test_check_environment_includes_read_only_camoufox_runtime(monkeypatch):
    from camoufox_reverse_mcp.tools import environment

    runtime = {
        "python_version": "0.5.5",
        "multiversion_supported": True,
        "active": {"selector": "official/152.0.4-beta.28-aabbccdd"},
        "installed": [],
        "legacy_cache_migration_risk": False,
    }
    monkeypatch.setattr(
        camoufox_runtime,
        "inspect_camoufox_runtime",
        lambda: runtime,
    )

    result = await environment.check_environment()

    assert result["camoufox"] == runtime
    assert result["overall_ok"] is True


def test_explicit_official_browser_ignores_stale_trace_controls(monkeypatch, tmp_path):
    from camoufox_reverse_mcp.tools import trace

    monkeypatch.setattr(trace.browser_manager, "browser", None)
    monkeypatch.setattr(trace.browser_manager, "_trace_base_dir", tmp_path)
    monkeypatch.setattr(
        trace.browser_manager,
        "_runtime_browser",
        {"repo": "official", "property_trace": False},
    )

    assert trace._is_trace_enabled() is False


def test_default_multiversion_official_ignores_stale_trace_controls(
    monkeypatch, tmp_path
):
    from camoufox_reverse_mcp.tools import trace

    monkeypatch.setattr(trace.browser_manager, "browser", None)
    monkeypatch.setattr(trace.browser_manager, "_trace_base_dir", tmp_path)
    monkeypatch.setattr(trace.browser_manager, "_runtime_browser", None)

    assert trace._is_trace_enabled() is False


def test_markerless_legacy_135_uses_live_run_handshake(monkeypatch, tmp_path):
    from camoufox_reverse_mcp.tools import trace

    control = tmp_path / "control"
    control.mkdir()
    (control / f"control-{os.getpid()}.cmd").write_text("on", encoding="utf-8")
    monkeypatch.setattr(trace.browser_manager, "browser", object())
    monkeypatch.setattr(trace.browser_manager, "_trace_base_dir", tmp_path)
    monkeypatch.setattr(
        trace.browser_manager,
        "_runtime_browser",
        {
            "repo": None,
            "property_trace": False,
            "capabilities_marker": None,
            "property_trace_protocol": None,
        },
    )
    assert trace._is_trace_enabled() is True


@pytest.mark.asyncio
async def test_check_environment_fails_when_camoufox_has_no_active_browser(monkeypatch):
    from camoufox_reverse_mcp.tools import environment

    monkeypatch.setattr(
        camoufox_runtime,
        "inspect_camoufox_runtime",
        lambda: {
            "python_version": "0.5.5",
            "multiversion_supported": True,
            "active": None,
            "installed": [],
        },
    )

    result = await environment.check_environment()

    assert result["overall_ok"] is False


@pytest.mark.asyncio
async def test_check_environment_does_not_treat_stale_control_as_active(
    monkeypatch, tmp_path
):
    from camoufox_reverse_mcp import property_trace
    from camoufox_reverse_mcp.tools import environment

    cache = tmp_path / "trace-cache"
    control = cache / "control"
    control.mkdir(parents=True)
    (control / "control-99999999.cmd").write_text("on", encoding="utf-8")
    monkeypatch.setattr(property_trace, "CACHE_DIR", cache)
    monkeypatch.setattr(property_trace, "CONTROL_DIR", control)
    monkeypatch.setattr(property_trace, "RUNS_DIR", cache / "runs")
    monkeypatch.setattr(environment.browser_manager, "browser", None)
    monkeypatch.setattr(environment.browser_manager, "_trace_base_dir", None)
    monkeypatch.setattr(environment.browser_manager, "_runtime_browser", None)
    monkeypatch.setattr(
        camoufox_runtime,
        "inspect_camoufox_runtime",
        lambda: {
            "python_version": "0.5.6",
            "multiversion_supported": True,
            "active": {"selector": "official/beta.30", "property_trace": False},
            "installed": [],
            "legacy_cache_migration_risk": False,
        },
    )

    result = await environment.check_environment()
    assert result["camoufox_reverse"]["installed"] is False
    assert result["camoufox_reverse"]["trace_active"] is False
    assert result["camoufox_reverse"]["stale_control_files"] == 1
