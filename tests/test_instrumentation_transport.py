from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import instrumentation


@pytest.fixture
async def harness(monkeypatch):
    class Context:
        async def route(self,pattern,handler): self.handler=handler
    context=Context();manager=BrowserManager();manager.contexts={'default':context}
    manager.get_active_page=AsyncMock(return_value=SimpleNamespace(url='about:blank'))
    monkeypatch.setattr(instrumentation,'browser_manager',manager)
    monkeypatch.setattr(instrumentation,'_active_routes',{})
    monkeypatch.setattr(instrumentation,'_source_site_registry',{})
    await instrumentation.instrumentation('install',url_pattern='**/sdk.js',include_source_site=True)
    return context


def route(body=b'var a=navigator.userAgent;',status=200,headers=None,error=None):
    response=SimpleNamespace(body=AsyncMock(return_value=body),status=status,headers=headers or {'content-type':'application/javascript'})
    return SimpleNamespace(request=SimpleNamespace(url='https://example.test/sdk.js',method='POST'),
                           fetch=AsyncMock(return_value=response,side_effect=error),fulfill=AsyncMock(),abort=AsyncMock(),continue_=AsyncMock())


async def test_current_response_headers_status_and_sdk_revision(harness):
    a=route(headers={'set-cookie':'rev=1','content-type':'application/javascript'})
    b=route(headers={'set-cookie':'rev=2','content-type':'application/javascript'})
    c=route(b'var a=navigator.language;',headers={'set-cookie':'rev=3'})
    d=route(b'busy',503,{'retry-after':'1'})
    for r in [a,b,c,d]:
        await harness.handler(r)
        r.fetch.assert_awaited_once_with(max_redirects=0)
        r.continue_.assert_not_called()
    assert b.fulfill.call_args.kwargs['headers']['set-cookie']=='rev=2'
    assert 'language' in c.fulfill.call_args.kwargs['body']
    assert d.fulfill.call_args.kwargs['status']==503 and d.fulfill.call_args.kwargs['body']==b'busy'
    stats=instrumentation._active_routes['**/sdk.js']['stats']
    assert stats['files_seen']==4 and stats['cache_hits']==1 and stats['files_rewritten']==2


async def test_rewrite_failure_does_not_replay_request(harness,monkeypatch):
    def fail(*a,**kw):raise RuntimeError('rewriter fixture failure')
    monkeypatch.setattr(instrumentation,'_ast_rewrite_py',fail)
    r=route();await harness.handler(r)
    assert r.fulfill.call_args.kwargs['body']==b'var a=navigator.userAgent;'
    r.fetch.assert_awaited_once();r.continue_.assert_not_called()
    failed=route(error=RuntimeError('response unavailable'))
    await harness.handler(failed)
    failed.abort.assert_awaited_once_with('failed');failed.continue_.assert_not_called()


async def test_unsupported_filtered_script_stays_original(harness,monkeypatch):
    monkeypatch.setattr(instrumentation,'_ast_rewrite_py',lambda *a,**k:(None,{'error':'modern parser missing'}))
    await instrumentation.instrumentation('install',url_pattern='**/filtered.js',filter_property_names=['userAgent'])
    r=route(b'var value=navigator["platform"];')
    await harness.handler(r)
    assert r.fulfill.call_args.kwargs['body']==b'var value=navigator["platform"];'
    stats=instrumentation._active_routes['**/filtered.js']['stats']
    assert stats['files_rewritten']==0 and stats['last_error']=='modern parser missing'


@pytest.mark.parametrize('filters', [
    {'filter_property_names': ['userAgent']},
    {'filter_object_names': ['this.bytecode']},
])
async def test_explicit_regex_cannot_silently_ignore_scope(harness, filters):
    handler = harness.handler
    result = await instrumentation.instrumentation(
        'install', url_pattern='**/filtered.js', mode='regex', **filters)
    assert "require mode='ast'" in result['error']
    assert harness.handler is handler
    assert '**/filtered.js' not in instrumentation._active_routes


async def test_log_targets_selected_frame_main_world(monkeypatch):
    child=object();page=object();manager=SimpleNamespace(get_active_page=AsyncMock(return_value=page))
    monkeypatch.setattr(instrumentation,'browser_manager',manager)
    monkeypatch.setattr(instrumentation,'resolve_frame',lambda *a,**k:(child,{'index':1,'name':'worker'}))
    execute=AsyncMock(side_effect=[([{'type':'tap_get','key':'language'}],'camoufox_native',None),(True,'camoufox_native',None)])
    monkeypatch.setattr(instrumentation,'evaluate_in_world',execute)
    result=await instrumentation.instrumentation('log',frame_name='worker',clear=True)
    assert result['total_entries']==1 and result['world']=='main' and result['frame']['index']==1
    assert all(call.args[0] is child and call.args[2]=='main' for call in execute.call_args_list)


async def test_stage_only_rewrite_is_visible_in_status(harness, monkeypatch):
    from camoufox_reverse_mcp.utils import js_parser

    def unparseable(source, locations=False):
        raise ValueError("parser unavailable")

    monkeypatch.setattr(js_parser, "parse_source", unparseable)
    await instrumentation.instrumentation("install", url_pattern="**/stage.js", tap_stage_calls=True)
    r = route(b"function d(){return l.apply(this,[7].concat(Array.prototype.slice.call(arguments)));}")
    r.request.url = "https://example.test/stage.js"
    await harness.handler(r)
    assert "__mcp_tap_stage(7, l, this, arguments" in r.fulfill.call_args.kwargs["body"]
    status = next(p for p in instrumentation._get_status()["active_patterns"] if p["pattern"] == "**/stage.js")
    assert status["last_mode_used"] == "ast (stage taps only)"
    assert "parser unavailable" in status["last_error"]
