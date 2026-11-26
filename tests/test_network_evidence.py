import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import network, environment
from camoufox_reverse_mcp.utils.network_evidence import compare_requests


def entry(rid, url='https://example.test/api?a=1&a=2&space=hello+world', body='{"x":1}', **kwargs):
    return {'id':rid,'url':url,'method':'POST','request_headers':{'Content-Type':'application/json'},
            'request_post_data':body,'headers_complete':True,**kwargs}


def compare(*rows, **kwargs):
    return compare_requests(list(rows),include_headers=True,include_body=True,max_value_chars=100,max_fields=100,**kwargs)


def test_duplicate_query_values_order_and_raw_encoding_are_preserved():
    result=compare(entry(1),entry(2,url='https://example.test/api?a=2&a=1&space=hello%20world'))
    changed={r['field']:r['values'] for r in result['changed']}
    assert 'query.raw' in changed
    assert json.loads(changed['query["a"]'][0]['preview']) == ['1','2']
    assert json.loads(changed['query["a"]'][1]['preview']) == ['2','1']
    assert 'query["space"]' in result['constant_fields']


def test_body_spelling_is_not_destroyed_by_parsed_projection():
    result=compare(entry(1),entry(2,body='{"x": 1}'))
    assert [r['field'] for r in result['changed']]==['body.raw']
    assert 'body.json["x"]' in result['constant_fields']


def test_missing_json_key_differs_from_null_and_duplicate_keys_remain_raw():
    result=compare(entry(1,body='{}'),entry(2,body='{"x":null}'))
    change=next(r for r in result['changed'] if r['field']=='body.json["x"]')
    assert change['values'][0]=={'request_id':1,'present':False}
    assert change['values'][1]['present'] and change['values'][1]['preview']=='null'
    assert 'body.raw' in {r['field'] for r in compare(entry(1,body='{"x":1,"x":2}'),entry(2,body='{"x":2}'))['changed']}


def test_full_digest_detects_change_outside_preview_and_limits_warn():
    result=compare_requests([entry(1,body='a'*500+'b'),entry(2,body='a'*500+'c')],include_headers=True,include_body=True,max_value_chars=2,max_fields=1)
    assert result['changed'][0]['values'][0]['truncated']
    assert result['changed'][0]['values'][0]['sha256']!=result['changed'][0]['values'][1]['sha256']
    assert result['fields_truncated']
    result=compare(entry(1,headers_complete=False),entry(2,body=None,request_post_data_error='not available'))
    assert len(result['warnings'])==2


async def test_compare_is_read_only_and_rejects_missing_ids(monkeypatch):
    manager=BrowserManager();manager._network_requests.extend([entry(1),entry(2)])
    monkeypatch.setattr(network,'browser_manager',manager)
    assert 'error' in await network.compare_network_requests([1,1])
    assert (await network.compare_network_requests([1,3]))['missing_ids']==[3]
    assert (await network.compare_network_requests([1,2]))['changed_count']==0
    assert manager.browser is None and len(manager._network_requests)==2


@pytest.mark.parametrize('raw',[b'',bytes(range(256)), '字节一致的 JS\n'.encode(), bytes.fromhex('0061736d01000000')])
async def test_save_response_bytes_are_lossless_without_refetch(monkeypatch,tmp_path,raw):
    manager=BrowserManager();row=entry(1)
    manager._network_requests.append(row)
    response=SimpleNamespace(body=AsyncMock(return_value=raw))
    await manager._fetch_response_body(response,row)
    monkeypatch.setattr(network,'browser_manager',manager)
    path=tmp_path/'response.bin'
    result=await network.save_response_body(1,str(path))
    assert path.read_bytes()==raw and result['sha256']==hashlib.sha256(raw).hexdigest()
    assert not result['partial'] and result['bytes_saved']==len(raw)
    assert response.body.await_count==1 and manager.browser is None
    assert 'error' in await network.save_response_body(1,str(path))


async def test_save_partial_requires_explicit_opt_in(monkeypatch,tmp_path):
    manager=BrowserManager();row=entry(1);manager._network_requests.append(row)
    await manager._fetch_response_body(SimpleNamespace(body=AsyncMock(return_value=b'abcdef')),row,3)
    monkeypatch.setattr(network,'browser_manager',manager)
    path=tmp_path/'partial.bin'
    assert (await network.save_response_body(1,str(path)))['partial']
    assert not path.exists()
    assert (await network.save_response_body(1,str(path),allow_partial=True))['partial']
    assert path.read_bytes()==b'abc'
    assert 'error' in await network.save_response_body(2,str(tmp_path/'unknown'))


async def test_environment_reuses_evidence_and_reports_browser_scope(monkeypatch):
    from camoufox_reverse_mcp import camoufox_runtime
    manager=BrowserManager()
    manager.browser=SimpleNamespace(is_connected=lambda:True)
    manager._browser_instance_id='first'
    manager._persistent_scripts=[{'name':'active-hook','content':'fixture'}]
    manager.pages={'default':SimpleNamespace(url='https://example.test/current')}
    manager._network_requests.append(entry(1))
    monkeypatch.setattr(environment,'browser_manager',manager)
    monkeypatch.setattr(camoufox_runtime,'inspect_camoufox_runtime',lambda:{'active':{'selector':'fixture'},'installed':[]})
    first=await environment.check_environment();second=await environment.check_environment()
    assert first['review']['state_fingerprint']==second['review']['state_fingerprint']
    assert first['browser']['has_residuals'] and first['review']['automatic_reset'] is False
    assert not any('reset_browser_state()' in text for text in first['recommendations'])
    assert manager._network_requests[0]['id']==1 and len(manager._persistent_scripts)==1
    manager._browser_instance_id='second'
    assert (await environment.check_environment())['review']['state_fingerprint']!=first['review']['state_fingerprint']


def test_raw_body_digest_is_distinct_from_comparison_serialization():
    raw = '{"name": "雪"}\n'
    result = compare(entry(1, body=raw), entry(2, body=raw+' '))
    value = next(row for row in result['changed'] if row['field']=='body.raw')['values'][0]
    assert value['length_unit']=='serialized_json_characters'
    assert value['sha256_scope']=='canonical_json_utf8'
    assert value['raw_utf8']=={'bytes':len(raw.encode()), 'sha256':hashlib.sha256(raw.encode()).hexdigest()}
    assert value['sha256']!=value['raw_utf8']['sha256']


@pytest.mark.parametrize('stack,confidence',[(None,'unavailable'),('caller@demo.js:1','heuristic')])
async def test_initiator_does_not_claim_exact_identity(monkeypatch,stack,confidence):
    manager=BrowserManager();manager._network_requests.append(entry(1))
    manager.get_active_page=AsyncMock(return_value=SimpleNamespace(evaluate=AsyncMock(return_value={'url':'https://example.test/api','stack':stack,'type':'fetch'})))
    monkeypatch.setattr(network,'browser_manager',manager)
    result=await network.get_request_initiator(1)
    assert result['match_confidence']==confidence
    assert result['matching_basis']=='hook_log_url'
    assert result['initiator_stack']==stack


async def test_body_size_units_are_explicit(monkeypatch):
    manager=BrowserManager();row=entry(1);manager._network_requests.append(row)
    await manager._fetch_response_body(SimpleNamespace(body=AsyncMock(return_value='中文'.encode())),row)
    monkeypatch.setattr(network,'browser_manager',manager)
    summary=(await network.list_network_requests())[0]
    assert summary['size']==2 and summary['size_unit']=='characters' and summary['body_bytes']==6
    full=await network.get_network_request(1,include_body=True)
    assert full['response_body_size_unit']=='characters' and full['response_body_total_bytes']==6


async def test_list_network_requests_url_preview_boundaries_and_full_url_filter(monkeypatch):
    from camoufox_reverse_mcp.tools.navigation import _request_summary

    short_url = 'https://api.example.test/ping?x=1'
    prefix = 'https://api.example.test/v2/item/detail?pad='
    exact_200 = prefix + ('a' * (network.URL_PREVIEW_CHARS - len(prefix)))
    cut_201 = exact_200 + 'Z'
    long_signed = exact_200 + '&nonce=n1&sig=tail_only_after_200'
    assert len(exact_200) == 200 and len(cut_201) == 201

    manager = BrowserManager()
    manager._network_requests.extend([
        entry(1, url=short_url),
        entry(2, url=exact_200),
        entry(3, url=cut_201),
        entry(4, url=long_signed),
    ])
    monkeypatch.setattr(network, 'browser_manager', manager)

    rows = {row['id']: row for row in await network.list_network_requests()}
    assert rows[1]['url'] == short_url and rows[1]['url_truncated'] is False and rows[1]['url_length'] == len(short_url)
    assert rows[2]['url'] == exact_200 and rows[2]['url_truncated'] is False and rows[2]['url_length'] == 200
    assert rows[3]['url'] == exact_200 + '…' and rows[3]['url_truncated'] is True and rows[3]['url_length'] == 201
    assert rows[4]['url'] == exact_200 + '…' and rows[4]['url_truncated'] is True and rows[4]['url_length'] == len(long_signed)

    filtered = await network.list_network_requests(url_filter='sig=tail_only_after_200')
    assert [r['id'] for r in filtered] == [4]
    assert filtered[0]['url_truncated'] is True and 'sig=tail_only_after_200' not in filtered[0]['url']

    full = await network.get_network_request(4)
    assert full['url'] == long_signed

    wait_summary = _request_summary(manager._network_requests[3])
    assert wait_summary['url'] == rows[4]['url']
    assert wait_summary['url_truncated'] is True and wait_summary['url_length'] == len(long_signed)


async def test_url_preview_handles_missing_url_and_lone_surrogates(monkeypatch):
    prefix = 'https://api.example.test/beacon?blob='
    surrogate_url = prefix + ('x' * (199 - len(prefix))) + '\ud800' + 'tail'
    manager = BrowserManager()
    manager._network_requests.extend([
        {'id': 10, 'method': 'GET'},
        {'id': 11, 'url': None, 'method': 'GET'},
        entry(12, url=surrogate_url),
    ])
    monkeypatch.setattr(network, 'browser_manager', manager)

    rows = {row['id']: row for row in await network.list_network_requests()}
    assert rows[10]['url'] == '' and rows[10]['url_truncated'] is False and rows[10]['url_length'] == 0
    assert rows[11]['url'] == '' and rows[11]['url_truncated'] is False and rows[11]['url_length'] == 0
    assert rows[12]['url'].endswith('\ufffd…')
    assert rows[12]['url_truncated'] is True and rows[12]['url_length'] == len(surrogate_url)
    json.dumps(rows, ensure_ascii=False).encode('utf-8')

    by_domain = await network.list_network_requests(url_contains_domain='example.test')
    assert [r['id'] for r in by_domain] == [12]
