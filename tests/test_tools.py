import os
import pytest
from camoufox_reverse_mcp.utils.js_helpers import render_trace_template
from camoufox_reverse_mcp.utils.response_fmt import format_response, truncate_str


def test_render_trace_template():
    js = render_trace_template(
        function_path="window.encrypt",
        max_captures=10,
        log_args=True,
        log_return=False,
        log_stack=True,
    )
    assert "window.encrypt" in js
    assert "10" in js
    assert "true" in js


def test_render_trace_template_defaults():
    js = render_trace_template(function_path="JSON.stringify")
    assert "JSON.stringify" in js
    assert "50" in js


def test_format_response_dict():
    result = format_response({"key": "value"})
    assert '"key"' in result
    assert '"value"' in result


def test_format_response_truncation():
    data = "x" * 100000
    result = format_response(data, max_length=100)
    assert "truncated" in result


def test_truncate_str_short():
    assert truncate_str("hello", 10) == "hello"


def test_truncate_str_long():
    result = truncate_str("a" * 100, 50)
    assert len(result) < 100
    assert "chars total" in result


def test_render_persistent_trace_template():
    from camoufox_reverse_mcp.utils.js_helpers import render_persistent_trace_template
    js = render_persistent_trace_template(
        function_path="XMLHttpRequest.prototype.open",
        max_captures=20,
        log_args=True,
        log_return=True,
        log_stack=True,
    )
    assert "XMLHttpRequest.prototype.open" in js
    assert "__MCP_TRACE__" in js
    assert "20" in js


def test_hook_files_exist():
    hooks_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "camoufox_reverse_mcp", "hooks"
    )
    expected_files = [
        "xhr_hook.js",
        "fetch_hook.js",
        "crypto_hook.js",
        "websocket_hook.js",
        "debugger_trap.js",
        "trace_template.js",
        "trace_persistent_template.js",
        "property_access_hook.js",
        "jsvmp_hook.js",
    ]
    for f in expected_files:
        assert os.path.exists(os.path.join(hooks_dir, f)), f"Missing hook file: {f}"


def test_instrumentation_status_reports_last_mode_used(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation

    monkeypatch.setattr(instrumentation, "_active_routes", {
        "**/vmp.js": {
            "mode": "ast",
            "tag": "vmp",
            "cache": {},
            "stats": {
                "files_rewritten": 1,
                "total_edits": 2,
                "last_url": "https://example.test/vmp.js",
                "last_mode_used": "ast",
            },
        },
    })

    status = instrumentation._get_status()

    assert status["active_patterns"][0]["last_mode_used"] == "ast"


def test_instrumentation_status_reports_source_site_count(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation

    monkeypatch.setattr(instrumentation, "_active_routes", {
        "**/vmp.js": {
            "mode": "ast",
            "tag": "vmp",
            "cache": {},
            "include_source_site": True,
            "source_sites": {"source:1:2:tap_get": {}},
            "stats": {
                "files_rewritten": 1,
                "total_edits": 1,
                "last_url": "https://example.test/vmp.js",
                "last_mode_used": "ast",
            },
        },
    })

    status = instrumentation._get_status()["active_patterns"][0]

    assert status["include_source_site"] is True
    assert status["source_site_count"] == 1


@pytest.mark.asyncio
async def test_instrumentation_log_returns_source_sidecar_and_hot_functions(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation

    site_id = "0123456789abcdef:10:20:tap_call"

    class FakePage:
        async def evaluate(self, expression):
            if expression == "window.__mcp_vmp_log || []":
                return [
                    {
                        "type": "tap_call",
                        "tag": "vmp",
                        "name": "dispatch",
                        "site_id": site_id,
                        "seq": 0,
                    },
                    {
                        "type": "tap_call",
                        "tag": "vmp",
                        "name": "dispatch",
                        "site_id": site_id,
                        "seq": 1,
                    },
                ]
            raise AssertionError(f"unexpected evaluate: {expression}")

    async def get_active_page():
        return FakePage()

    monkeypatch.setattr(
        instrumentation.browser_manager,
        "get_active_page",
        get_active_page,
    )
    monkeypatch.setattr(instrumentation, "_active_routes", {
        "**/vmp.js": {
            "source_sites": {
                site_id: {
                    "site_id": site_id,
                    "kind": "tap_call",
                    "start": 10,
                    "end": 20,
                    "offset_unit": "unicode_code_point",
                },
            },
        },
    })

    result = await instrumentation._get_log(None, None, None, 500, False)

    assert result["summary"]["hot_functions"] == {"dispatch": 2}
    assert result["summary"]["hot_sites"] == {site_id: 2}
    assert result["source_sites"][site_id]["start"] == 10


@pytest.mark.asyncio
async def test_instrumentation_route_registers_original_source_sidecar(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation

    class FakePage:
        url = "about:blank"

    class FakeContext:
        handler = None

        async def route(self, pattern, handler):
            self.handler = handler

    class FakeResponse:
        status = 200
        headers = {"content-type": "application/javascript"}

        async def body(self):
            return b'var ua = navigator["userAgent"];'

    class FakeRequest:
        url = "https://example.test/vmp.js"

    class FakeRoute:
        request = FakeRequest()
        fulfilled = None

        async def fetch(self):
            return FakeResponse()

        async def fulfill(self, **kwargs):
            self.fulfilled = kwargs

    context = FakeContext()

    async def get_active_page():
        return FakePage()

    monkeypatch.setattr(instrumentation, "_active_routes", {})
    monkeypatch.setattr(instrumentation, "_source_site_registry", {})
    monkeypatch.setattr(instrumentation.browser_manager, "contexts", {"default": context})
    monkeypatch.setattr(
        instrumentation.browser_manager,
        "get_active_page",
        get_active_page,
    )

    result = await instrumentation._install(
        url_pattern="**/vmp.js",
        mode="ast",
        tag="vmp",
        rewrite_member_access=True,
        rewrite_calls=True,
        max_rewrites=20_000,
        fallback_on_error=True,
        ignore_csp=False,
        filter_property_names=None,
        filter_object_names=None,
        max_file_size=200_000,
        on_oversized="selective",
        include_source_site=True,
    )
    route = FakeRoute()
    await context.handler(route)
    second_route = FakeRoute()
    second_route.request = type(
        "SecondRequest",
        (),
        {"url": "https://cdn.example.test/vmp.js"},
    )()
    await context.handler(second_route)

    info = instrumentation._active_routes["**/vmp.js"]
    site = next(iter(info["source_sites"].values()))
    assert result["include_source_site"] is True
    assert site["urls"] == [FakeRequest.url, second_route.request.url]
    assert site["offset_unit"] == "unicode_code_point"
    assert site["range_semantics"] == "half_open"
    assert site["source_sha256"]
    assert site["site_id"] in route.fulfilled["body"]

    monkeypatch.setattr(
        instrumentation,
        "_ast_rewrite_py",
        lambda *args, **kwargs: (None, {"parsed": False}),
    )
    fallback_result = await instrumentation._install(
        url_pattern="**/fallback.js",
        mode="ast",
        tag="fallback",
        rewrite_member_access=True,
        rewrite_calls=True,
        max_rewrites=20_000,
        fallback_on_error=True,
        ignore_csp=False,
        filter_property_names=None,
        filter_object_names=None,
        max_file_size=200_000,
        on_oversized="selective",
        include_source_site=True,
    )
    fallback_route = FakeRoute()
    fallback_route.request = type(
        "FallbackRequest",
        (),
        {"url": "https://example.test/fallback.js"},
    )()
    await context.handler(fallback_route)

    fallback_info = instrumentation._active_routes["**/fallback.js"]
    assert fallback_result["include_source_site"] is True
    assert fallback_info["stats"]["last_mode_used"] == "regex (fallback)"
    assert fallback_info["source_sites"]


@pytest.mark.asyncio
async def test_log_resolves_truncated_hot_sites_after_route_stop(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation

    hot_site = "aaaaaaaaaaaaaaaa:1:2:tap_get"
    returned_site = "bbbbbbbbbbbbbbbb:3:4:tap_get"

    class FakePage:
        async def evaluate(self, expression):
            assert expression == "window.__mcp_vmp_log || []"
            return [
                {"type": "tap_get", "key": "hot", "site_id": hot_site},
                {"type": "tap_get", "key": "hot", "site_id": hot_site},
                {"type": "tap_get", "key": "hot", "site_id": hot_site},
                {"type": "tap_get", "key": "last", "site_id": returned_site},
            ]

    async def get_active_page():
        return FakePage()

    monkeypatch.setattr(
        instrumentation.browser_manager,
        "get_active_page",
        get_active_page,
    )
    monkeypatch.setattr(instrumentation, "_active_routes", {})
    monkeypatch.setattr(instrumentation, "_source_site_registry", {
        hot_site: {"site_id": hot_site, "start": 1, "end": 2},
        returned_site: {"site_id": returned_site, "start": 3, "end": 4},
    })

    result = await instrumentation._get_log(None, None, None, 1, False)

    assert result["entries"][0]["site_id"] == returned_site
    assert result["summary"]["hot_sites"][hot_site] == 3
    assert set(result["source_sites"]) == {hot_site, returned_site}
    assert "unresolved_source_site_ids" not in result


@pytest.mark.asyncio
async def test_stop_preserves_source_site_registry_for_final_log(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation

    pattern = "**/vmp.js"
    site_id = "cccccccccccccccc:5:12:tap_get"
    site = {"site_id": site_id, "start": 5, "end": 12}

    class FakeContext:
        def __init__(self):
            self.unrouted = []

        async def unroute(self, route_pattern):
            self.unrouted.append(route_pattern)

    class FakePage:
        async def evaluate(self, expression):
            assert expression == "window.__mcp_vmp_log || []"
            return [{"type": "tap_get", "key": "userAgent", "site_id": site_id}]

    async def get_active_page():
        return FakePage()

    context = FakeContext()
    monkeypatch.setattr(instrumentation, "_active_routes", {
        pattern: {"context": context, "source_sites": {site_id: site}},
    })
    monkeypatch.setattr(instrumentation, "_source_site_registry", {site_id: site})
    monkeypatch.setattr(
        instrumentation.browser_manager,
        "get_active_page",
        get_active_page,
    )

    stopped = await instrumentation._stop(pattern)

    assert stopped == {"status": "stopped", "removed": [pattern]}
    assert context.unrouted == [pattern]
    assert instrumentation._active_routes == {}
    assert instrumentation._source_site_registry == {site_id: site}

    result = await instrumentation._get_log(None, None, None, 500, False)

    assert result["source_sites"] == {site_id: site}
    assert "unresolved_source_site_ids" not in result


@pytest.mark.asyncio
async def test_reset_browser_state_clears_source_site_registry(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation, navigation

    monkeypatch.setattr(instrumentation, "_active_routes", {})
    monkeypatch.setattr(instrumentation, "_source_site_registry", {
        "site": {"site_id": "site"},
    })

    result = await navigation.reset_browser_state(
        clear_persistent_hooks=False,
        clear_network_capture=False,
        clear_active_routes=True,
    )

    assert result["source_sites_cleared"] == 1
    assert instrumentation._source_site_registry == {}


@pytest.mark.asyncio
async def test_close_browser_clears_instrumentation_routes_and_sites(monkeypatch):
    from camoufox_reverse_mcp.tools import instrumentation, navigation

    class FakeContext:
        unrouted = []

        async def unroute(self, pattern):
            self.unrouted.append(pattern)

    context = FakeContext()

    async def close():
        return {"status": "closed"}

    monkeypatch.setattr(instrumentation, "_active_routes", {
        "**/vmp.js": {"context": context},
    })
    monkeypatch.setattr(instrumentation, "_source_site_registry", {
        "site": {"site_id": "site"},
    })
    monkeypatch.setattr(navigation.browser_manager, "close", close)

    result = await navigation.close_browser()

    assert result["status"] == "closed"
    assert result["instrumentation_routes_cleared"] == 1
    assert result["source_sites_cleared"] == 1
    assert context.unrouted == ["**/vmp.js"]
    assert instrumentation._active_routes == {}
    assert instrumentation._source_site_registry == {}
