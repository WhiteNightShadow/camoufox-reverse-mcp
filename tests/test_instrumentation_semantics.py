"""Execute original and instrumented programs in fresh Node vm contexts.

Fixtures deliberately contain getters, proxies, poisoned function properties,
throwing coercions, mutation and deterministic signature inputs. The standalone
runner also exports executable before/after evidence without a browser.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite
from camoufox_reverse_mcp.utils.js_rewriter import regex_rewrite

NODE_RUNNER = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const c = vm.createContext({});
vm.runInContext(`globalThis.window = globalThis;
  var hits = [], randomCalls = 0, result = null;
  Math.random = function() { randomCalls++; return (randomCalls * 17 % 97) / 97; };
  Date.now = function() { return 12345; };`, c);
vm.runInContext(p.setup || '', c);
let exception = null;
try { vm.runInContext(p.source, c, {timeout: 3000}); }
catch (e) { exception = {name: e.name, message: e.message}; }
const randomCalls = vm.runInContext('randomCalls', c);
const observation = vm.runInContext(p.observe || '({result, hits})', c);
const logs = vm.runInContext('window.__mcp_vmp_log || []', c);
process.stdout.write(JSON.stringify({observation, randomCalls, exception, logs}));
"""

# setup is intentionally not rewritten, so target traps exercise the public
# helpers without instrumenting the measurement harness itself.
CASES = {
    'getter_throws': ("var sentinel = new RangeError('getter boom'); var obj = {get x(){hits.push('get'); throw sentinel;}};", "try { result = obj.x; } catch(e) { result = e === sentinel; }"),
    'null_get_throws': ('', "try { result = null.x; } catch(e) { result = e instanceof TypeError; }"),
    'preview_getters': ("var value = {get toJSON(){hits.push('toJSON'); return function(){hits.push('json');};}, get a(){hits.push('a'); return 4;}}; var obj = {get constructor(){hits.push('constructor'); return Object;}, x:value};", 'result = obj.x === value;'),
    'function_name': ("function fn(){return 7;} Object.defineProperty(fn, 'name', {get(){hits.push('name'); throw 9;}}); var obj = {x:fn};", 'var f = obj.x; result = fn();'),
    'proxy_preview': ("var value = new Proxy({}, {get(t,k){hits.push(String(k)); return 1;}, ownKeys(){hits.push('ownKeys'); return [];}}); var obj = {x:value};", 'result = obj.x === value;'),
    'key_coercion_once': ("var key = {[Symbol.toPrimitive](){hits.push('key'); return 'x';}}; var obj={x:42};", 'result = obj[key];'),
    'poisoned_apply': ("function fn(v){hits.push('call'); return v+1;} Object.defineProperty(fn, 'apply', {get(){hits.push('apply'); throw 8;}});", 'result = fn(4);'),
    'bare_strict_this': ("function fn(){'use strict'; return this === undefined;}", 'result = fn();'),
    'program_strict': ('', "'use strict'; function fn(){ return this === undefined; } result = fn();"),
    'strict_directive_asi': ('', "'use strict'\nfunction fn(){ return this === undefined; } result = fn();"),
    'leading_string_continues_next_line': ("var obj={x:'k'};", '"a,b"\n.split(",").forEach(function(k){ hits.push(k); });\nresult = obj.x;'),
    'method_getter_order': ("var obj={get m(){hits.push('get'); return function(x){hits.push(this===obj?'this':'bad-this'); return x;};}}; function arg(){hits.push('arg'); return 6;}", 'result = obj.m(arg());'),
    'method_getter_throw': ("var sentinel={}; var obj={get m(){hits.push('get'); throw sentinel;}}; function arg(){hits.push('arg'); return 6;}", 'try { obj.m(arg()); } catch(e) { result = e === sentinel; }'),
    'method_mutated_by_arg': ("var obj={m:function(){return 'original';}}; function arg(){obj.m=function(){return 'replacement';};}", 'result = obj.m(arg());'),
    'method_receiver_key_once': ("var obj={m:function(x){return this===obj && x;}}; function base(){hits.push('base'); return obj;} var key={[Symbol.toPrimitive](){hits.push('key'); return 'm';}}; function arg(){hits.push('arg'); return 8;}", 'result = base()[key](arg());'),
    'method_null_before_arg': ("function arg(){hits.push('arg');}", 'try { null.m(arg()); } catch(e) { result=e instanceof TypeError; }'),
    'method_noncallable_after_arg': ("var obj={m:7}; function arg(){hits.push('arg');}", 'try { obj.m(arg()); } catch(e) { result=e instanceof TypeError; }'),
    'bare_noncallable_apply': ("var fn={apply(){hits.push('fake apply'); return 9;}}; function arg(){hits.push('arg');}", 'try { fn(arg()); } catch(e) { result=e instanceof TypeError; }'),
    'throw_identity_preview': ("var sentinel={toString(){hits.push('toString'); throw 'replaced';}}; function fn(){throw sentinel;}", 'try { fn(); } catch(e) { result=e===sentinel; }'),
    'random_signature': ("var env={ua:'UA'}; function sign(s){return s + ':' + Math.random();}", 'var ua=env.ua; result=sign(ua) + ":" + Math.random();'),
    'delete': ("var obj={get x(){hits.push('get'); return 1;}};", 'result = [delete obj.x, "x" in obj];'),
    'lvalues_patterns': ("var obj={}; var keys={k:'x'};", 'obj.x=1; obj.x++; obj.x+=2; ({a:obj.x}={a:9}); [obj.y=3,...obj.z]=[undefined,4,5]; for(obj.k in {a:1}){} for(obj.v of [7]){} result=[obj.x,obj.y,obj.z,obj.k,obj.v];'),
    'computed_pattern_key': ("var obj={}, key={get x(){hits.push('key'); return 'a';}};", '({[key.x]:obj.x}={a:9}); result=obj.x;'),
    'super': ('', 'class A {get x(){hits.push("super-get"); return 5;} m(v){return this.x+v;}} class B extends A {constructor(){super();} m(v){return super.m(v)+super.x;}} result=new B().m(2);'),
    'tagged_method_this': ("var obj={tag:function(s){return this===obj;}};", 'result=obj.tag`hello`;'),
    'sequence_expressions': ("var obj={x:9,m:function(a){return [this===obj,a];}}; function mark(){hits.push('mark');}", 'var x=(mark(),obj)[(mark(),"x")]; result=(mark(),obj)[(mark(),"m")]((mark(),x));'),
    'short_circuit': ("var obj={get x(){hits.push('unexpected'); throw 9;}};", 'result=[false && obj.x, true || obj.x, true ? 3 : obj.x];'),
    'direct_eval': ('', 'function f(){var local=4; return eval("local+1");} result=f();'),
    'with_receiver': ("var obj={fn:function(){return this===obj;}};", 'with(obj){ result=fn(); }'),
    'spread_order': ("var obj={get m(){hits.push('get'); return function(x){return x;};}}; var iterable={[Symbol.iterator]:function*(){hits.push('iterate'); yield 8;}};", 'result=obj.m(...iterable);'),
    'yield_argument': ("var obj={get m(){hits.push('get'); return function(x){return x;};}};", 'function* gen(){return obj.m(yield 3);} var it=gen(); var first=it.next(); result=[first.value,it.next(9).value];'),
}

REGEX_CASES = {
    'regex_string_comment': ('var obj={x:4};', 'var s="obj[x]"; /* obj[x] */ // obj[x]\nresult=s;'),
    'regex_method_this': ("var obj={m:function(){return this===obj;}};", 'result=obj["m"]();'),
    'regex_tag_this': ("var obj={m:function(){return this===obj;}};", 'result=obj["m"]`a`;'),
    'regex_delete': ("var obj={x:1};", 'result=delete obj["x"];'),
    'regex_update_pattern': ('var obj={x:1};', '++obj["x"]; ({x:obj["x"]}={x:3}); result=obj.x;'),
    'regex_template_regexp': ('', 'var x=`obj[k]`; var r=/obj[k]/; result=[x,r.source];'),
    'regex_safe_getter': ("var obj={get x(){hits.push('get'); return 4;}};", 'var result=obj["x"];'),
    'regex_safe_coercion': ("var obj={x:4}; var key={[Symbol.toPrimitive](){hits.push('key'); return 'x';}};", 'var result=obj[key];'),
}

CASES.update({
    'empty_directive_then_strict': ('', "''; 'custom'; 'use strict'; function fn(){return this===undefined;} result=fn();"),
    'strict_delete_error': ("var obj={}; Object.defineProperty(obj,'x',{value:1,configurable:false});", "'use strict'; try { delete obj.x; } catch(e) { result=e instanceof TypeError; }"),
    'getter_uncaught_error': ("var obj={get x(){hits.push('get'); throw new RangeError('original getter error');}};", 'result=obj.x;'),
    'method_apply_poison': ("var fn=function(x){return this===obj && x;}; Object.defineProperty(fn,'apply',{get(){hits.push('apply'); throw 9;}}); var obj={m:fn};", 'result=obj.m(7);'),
    'primitive_method_this': ("Number.prototype.m=function(){'use strict'; return typeof this;};", 'result=(3).m();'),
    'bound_call': ("function f(){'use strict'; return this===null;} var fn=f.bind(null);", 'result=fn();'),
    'call_proxy': ("var fn=new Proxy(function(){}, {apply(t,receiver,args){hits.push(receiver===undefined?'undefined':'receiver'); return args[0];},get(t,key){hits.push(String(key)); throw 9;}});", 'result=fn(8);'),
    'short_circuit_mutation': ("var obj={get x(){hits.push('get'); return 4;}};", 'var a=0; result=[(a++ && obj.x),(a++ || obj.x),(a ? obj.x : 9),a];'),
})


def execute(source, setup='', observe=None):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for executable semantics tests')
    p = subprocess.run([node, '-e', NODE_RUNNER], input=json.dumps({'source':source, 'setup':setup, 'observe':observe}), capture_output=True, text=True, timeout=10, check=True)
    return json.loads(p.stdout)


def behavior(output):
    return {k:v for k,v in output.items() if k != 'logs'}


@pytest.mark.parametrize('name', CASES)
def test_ast_semantics(name):
    setup, source = CASES[name]
    rewritten, stats = ast_rewrite(source, tag='semantics', include_source_site=True)
    assert rewritten is not None, stats
    original = execute(source, setup)
    actual = execute(rewritten, setup)
    assert behavior(actual) == behavior(original), {'original':original, 'actual':actual, 'stats':stats}
    if name != 'getter_uncaught_error':
        assert original['exception'] is None


@pytest.mark.parametrize('name', REGEX_CASES)
def test_regex_semantics(name):
    setup, source = REGEX_CASES[name]
    rewritten, stats = regex_rewrite(source, tag='semantics', include_source_site=True)
    original = execute(source, setup)
    actual = execute(rewritten, setup)
    assert behavior(actual) == behavior(original), {'original':original, 'actual':actual, 'stats':stats}


def export_evidence(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'runner.cjs').write_text(NODE_RUNNER)
    rows=[]
    for mode,cases,rewrite in [('ast',CASES,ast_rewrite),('regex',REGEX_CASES,regex_rewrite)]:
        for name,(setup,source) in cases.items():
            rewritten,stats=rewrite(source, tag='semantics', include_source_site=True)
            original=execute(source,setup)
            actual=execute(rewritten,setup) if rewritten is not None else None
            for variant,code in [('original',source),('rewritten',rewritten)]:
                if code is not None:
                    (directory/f'{name}.{variant}.js').write_text(code)
                    (directory/f'{name}.{variant}.json').write_text(json.dumps({'source':code,'setup':setup},ensure_ascii=False,indent=2))
            rows.append({'case':name,'mode':mode,'equal':actual is not None and behavior(original)==behavior(actual),'original':original,'rewritten':actual,'stats':stats})
    (directory/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    print(json.dumps({'total':len(rows),'equal':sum(r['equal'] for r in rows),'failures':[r['case'] for r in rows if not r['equal']]},indent=2))


if __name__ == '__main__':
    export_evidence(sys.argv[1])


@pytest.mark.parametrize('sampling,expected', [(0,0), (0.25,2), (1,8), (2,8)])
def test_sampling_does_not_consume_program_randomness(sampling, expected):
    setup = f'window.__mcp_tap_cfg={{sampling:{sampling},tagFilter:null}}; var obj={{x:7}};'
    source = 'var a=obj.x;' * 8 + 'result=Math.random();'
    rewritten, _ = ast_rewrite(source, rewrite_calls=False)
    original, actual = execute(source,setup), execute(rewritten,setup)
    assert behavior(actual) == behavior(original)
    assert len(actual['logs']) == expected


@pytest.mark.parametrize('source', ['result=obj.x;', 'result=fn(obj);', 'result=obj.m(obj);', 'try { bad(); } catch(e) { result=e===sentinel; }'])
def test_logging_failure_does_not_replace_return_or_exception(source):
    setup = """
      var sentinel={}; function bad(){throw sentinel;}
      var obj={x:5,m:function(x){return x===obj;}};
      function fn(x){return x===obj;}
      var log=new Proxy([], {set(){throw new Error('sink failed');}});
      window.__mcp_vmp_log=log;
    """
    rewritten,_=ast_rewrite(source)
    assert behavior(execute(rewritten,setup)) == behavior(execute(source,setup))


def test_objects_and_functions_are_not_inspected_in_call_logs():
    setup = """
      var value=new Proxy(function(){}, {
        get(t,k){hits.push(String(k)); throw new Error('unexpected access');},
        ownKeys(){hits.push('ownKeys'); throw 9;}
      });
      function fn(x){return x;}
      var obj={m:fn};
    """
    source='var a=fn(value); var b=obj.m(value); result=a===value && b===value;'
    rewritten,_=ast_rewrite(source, include_source_site=True)
    actual=execute(rewritten,setup)
    assert behavior(actual)==behavior(execute(source,setup))
    assert [event['type'] for event in actual['logs']]==['tap_call','tap_method']
    assert actual['logs'][0]['name']=='fn'
    assert all(event['arg0']=='[fn]' and event['ret']=='[fn]' for event in actual['logs'])


def test_intrinsics_are_not_looked_up_again_after_installation():
    setup="var obj={x:4,m:function(x){return this===obj && x;}}; function fn(){return 5;}"
    source="""
      Reflect.apply=function(){hits.push('replaced apply'); throw 8;};
      Function.prototype.apply=function(){hits.push('fn apply'); throw 8;};
      JSON.stringify=function(){hits.push('stringify'); throw 8;};
      result=[obj.x,fn(),obj.m(6)];
    """
    rewritten,_=ast_rewrite(source)
    assert behavior(execute(rewritten,setup))==behavior(execute(source,setup))


@pytest.mark.parametrize('source', [
    'var x="obj[key]"; /* obj[key] */ var result=obj[key];',
    'var x="escaped\\\"obj[key]"; // obj[key]\nvar result=obj[key];',
])
def test_regex_rewrites_only_actual_read_ranges(source):
    setup="var obj={get x(){hits.push('get'); return 4;}}; var key='x';"
    rewritten,stats=regex_rewrite(source, include_source_site=True)
    assert stats['member_access_rewrites']==1
    assert source[stats['source_sites'][0]['start']:stats['source_sites'][0]['end']]=='obj[key]'
    assert behavior(execute(rewritten,setup))==behavior(execute(source,setup))


@pytest.mark.parametrize('source', [
    'obj[key]();', 'delete obj[key];', '++obj[key];', 'obj[key] += 1;',
    '({x:obj[key]}=value);', 'obj[key]`a`;', 'obj?.[key];',
    'class C { #x=1; m(){return this.#x;} }',
    'var x=`obj[key]`;', 'var x=/obj[key]/;',
])
def test_regex_unsupported_programs_are_explicit_unchanged_skips(source):
    rewritten,stats=regex_rewrite(source,include_source_site=True)
    assert rewritten==source
    assert stats['member_access_rewrites']==0
    assert stats['source_sites']==[]
    assert stats['skipped_reason']=='unsupported_program_syntax'
    assert 'not general JS equivalence' in stats['semantic_boundary']


def test_regex_preserves_directive_prologue():
    source="'custom'; 'use strict'; var result=obj[key];"
    setup="var key='x'; var obj={get x(){hits.push('get'); return 4;}};"
    rewritten,stats=regex_rewrite(source)
    assert stats['member_access_rewrites']==1
    assert rewritten.startswith("'custom'; 'use strict';")
    assert behavior(execute(rewritten,setup))==behavior(execute(source,setup))


@pytest.mark.parametrize('member_access,calls', [(True,False), (False,True), (True,True)])
@pytest.mark.parametrize('case', ['lvalues_patterns','super','method_getter_order','tagged_method_this','sequence_expressions','short_circuit'])
def test_independent_rewrite_flags_preserve_semantics(case,member_access,calls):
    setup,source=CASES[case]
    rewritten,stats=ast_rewrite(source,rewrite_member_access=member_access,rewrite_calls=calls)
    assert rewritten is not None,stats
    assert behavior(execute(rewritten,setup))==behavior(execute(source,setup))


@pytest.mark.parametrize('kind', ['ChainExpression','OptionalMemberExpression','OptionalCallExpression','private_get','private_call','super_get','super_call','optional_call'])
def test_future_ast_reference_shapes_are_explicit_skips(monkeypatch,kind):
    # Supply prospective ESTree shapes; esprima's modern-syntax parser support
    # is deliberately outside this change. No alternate parser/dependency.
    import esprima
    from types import SimpleNamespace as N
    obj=N(type='Identifier',name='obj',range=[0,3])
    prop=N(type='Identifier',name='x',range=[4,5])
    member=N(type='MemberExpression',object=obj,property=prop,computed=False,range=[0,5])
    source='obj.x'
    node=member
    if kind.startswith('private'):
        prop.type='PrivateIdentifier'
        source='obj.#x'
        member.range=[0,len(source)]
    if kind.startswith('super'):
        obj.type='Super'
        source='super.x'
        member.range=[0,len(source)]
    if kind.endswith('call') or kind=='OptionalCallExpression':
        node=N(type='CallExpression',callee=member,arguments=[],range=[0,len(source)+2])
        source+='()'
    if kind=='ChainExpression':
        node=N(type=kind,expression=member,range=member.range)
    elif kind=='OptionalMemberExpression':
        member.type=kind
    elif kind=='OptionalCallExpression':
        node.type=kind
    elif kind=='optional_call':
        node.optional=True
    tree=N(type='Program',body=[node])
    monkeypatch.setattr(esprima,'parseScript',lambda *a,**k:tree)
    rewritten,stats=ast_rewrite(source)
    assert rewritten.endswith(source)
    assert stats['edits']==0
    assert 'optional_private_or_super' in stats['semantic_skips']


def legacy_rewrite(source, tree=None):
    """Exercise the retained Acorn code generator using standard ESTree input.

    Parsing is performed by the existing esprima dependency; Acorn/CDN loading
    itself is not tested and no parser is installed for these offline checks.
    """
    import esprima
    from camoufox_reverse_mcp.utils.js_rewriter import ACORN_REWRITE_JS_TEMPLATE, INSTRUMENT_RUNTIME
    tree=tree or esprima.parseScript(source, options={'range':True}).toDict()

    def ranges(node):
        if isinstance(node,dict):
            if 'range' in node:
                # Acorn uses UTF-16 string offsets, esprima-python code points.
                node['start'],node['end']=[len(source[:n].encode('utf-16-le'))//2 for n in node['range']]
            for child in node.values():
                ranges(child)
        elif isinstance(node,list):
            for child in node:
                ranges(child)
    ranges(tree)
    script=r"""
      const fs=require('node:fs');
      const p=JSON.parse(fs.readFileSync(0,'utf8'));
      global.acorn={parse(){return p.tree;}};
      global.window={acorn};
      (async()=>{
        const transform=eval('('+p.template+')');
        const out=await transform(p.source,'legacy',{rewriteCalls:true,rewriteMemberAccess:true});
        process.stdout.write(JSON.stringify(out));
      })().catch(e=>{console.error(e); process.exit(1);});
    """
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    completed=subprocess.run([node,'-e',script],input=json.dumps({'tree':tree,'template':ACORN_REWRITE_JS_TEMPLATE,'source':source}),text=True,capture_output=True,check=True,timeout=10)
    out=json.loads(completed.stdout)
    assert out['ok'],out
    offset=out['runtime_insert_offset']
    # Convert returned Acorn UTF-16 offset back to Python's code points.
    prefix=out['src'].encode('utf-16-le')[:offset*2].decode('utf-16-le')
    return prefix+';\n'+INSTRUMENT_RUNTIME+'\n'+out['src'][len(prefix):],out


@pytest.mark.parametrize('case', CASES)
def test_legacy_estree_codegen_semantics(case):
    setup,source=CASES[case]
    rewritten,_=legacy_rewrite(source)
    assert behavior(execute(rewritten,setup))==behavior(execute(source,setup))
