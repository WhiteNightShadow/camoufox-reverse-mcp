"""P0-1 源码级正则插桩：`instrumentation(inject=[{find, before/after, group}])`。

覆盖：
  * 纯逻辑：`$n` 展开、语句包裹、before/after 插入位置、每组命中次数
  * 安全策略：命中在字符串字面量里 → 整组跳过并报告；原脚本不可解析 → 不做校验
  * 与 `tap_stage_calls` / `filter_*` 同时使用；`mode='ast'/'regex'` 行为不变
  * 路由侧：命中次数进 `status.inject_hits`，0 命中给出 `no_inject_matches`
  * `rewrite_source` 离线改写（配合 `intercept_request(body_file=...)`）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import shutil

import esprima
import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import instrumentation
from camoufox_reverse_mcp.utils.regex_inject import (
    apply_inject_groups,
    expand_template,
    literal_at,
    literal_regions,
    normalize_snippet,
    validate_inject_specs,
)

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")

# 天御 tdc.js 里实测过的形状：opcode 是 build 绑定的常量，dispatcher 调用被扁平化。
DISPATCH = (
    "var _0xa=0,_0xb=0,_0xc=0,_0xd=1,_0xe=2,_0xf=[3];\n"
    "function l(p){switch(p){\n"
    "case 0x22:_0xa[_0xb[++_0xc]][_0xa[_0xf[0]]](_0xa,_0xd);break;\n"
    "case 0x4b:_0xa[_0xb[++_0xc]][_0xa[_0xf[0]]](_0xa,_0xe);break;\n"
    "}}\n"
    "function d(){return l.apply(this,[9425].concat(Array.prototype.slice.call(arguments)));}\n"
)
FIND = r"case (0x[0-9a-f]+):(_0x[0-9a-f]+)\[(_0x[0-9a-f]+)\[\+\+(_0x[0-9a-f]+)\]\]\[\2\["
SNIPPET = "try{(window.__hit=window.__hit||[]).push([$1,$4]);}catch(e){}"
GROUPS = [{"find": FIND, "before": SNIPPET, "group": "write-opcode"}]


# ---------------------------------------------------------------- 纯逻辑


def test_expand_template_handles_groups_dollar_and_missing_group():
    match = __import__("re").search(r"(a)(b)?", "xa")
    # 第 2 组没有参与匹配（可选组）→ 空串；不存在的组同样是空串，不报错
    assert expand_template("$1-$2-$3", match) == "a--"
    assert expand_template("$0", match) == "a"
    assert expand_template("p$$q", match) == "p$q"
    assert expand_template("$99", match) == ""


def test_normalize_snippet_makes_one_complete_statement():
    assert normalize_snippet("window.__hit=1") == ";window.__hit=1;"
    assert normalize_snippet("try{}catch(e){}") == ";try{}catch(e){}"
    assert normalize_snippet(" ; ") == ";;"
    # 片段自带 `//` 行注释时必须换行，否则会吃掉同一行后面的原代码
    assert normalize_snippet("a=1 // note") == ";\na=1 // note;\n"
    assert normalize_snippet("a=1;\nb=2;") == ";\na=1;\nb=2;\n"


def test_before_inserts_at_match_start_and_counts_each_group():
    result = apply_inject_groups(DISPATCH, GROUPS)
    assert result["insertions"] == 2
    assert result["groups"][0]["matches"] == 2
    assert result["groups"][0]["insertions"] == 2
    # before 落在 `case` 标签前 → 自动挪到冒号后（否则 switch 语法错误）
    assert result["groups"][0]["label_shift"] == 2
    assert result["validated"] is True and result["validation"] == "ok"
    text = result["text"]
    for opcode, reg in (("0x22", "_0xc"), ("0x4b", "_0xc")):
        marker = (f"case {opcode}:;try{{(window.__hit=window.__hit||[]).push("
                  f"[{opcode},{reg}]);}}catch(e){{}}_0xa[")
        assert marker in text, text[:400]
    # 单行片段不引入换行：断点行号不会漂移
    assert result["lines_added"] == 0
    assert text.count("\n") == DISPATCH.count("\n")
    esprima.parseScript(text)


def test_before_outside_a_case_label_stays_at_the_match_start():
    groups = [{"find": r"break;", "before": "window.__hit=(window.__hit||[]).concat(['a'])",
               "group": "stmt"}]
    result = apply_inject_groups(DISPATCH, groups)
    assert result["insertions"] == 2
    assert result["groups"][0]["label_shift"] == 0
    assert ");;window.__hit=(window.__hit||[]).concat(['a']);break;" in result["text"]
    esprima.parseScript(result["text"])


def test_after_inserts_at_match_end():
    groups = [{"find": r"case (0x[0-9a-f]+):", "after": "window.__hit=(window.__hit||[]).concat([$1])",
               "group": "labels"}]
    result = apply_inject_groups(DISPATCH, groups)
    assert result["insertions"] == 2
    assert "case 0x22:;window.__hit=(window.__hit||[]).concat([0x22]);_0xa[" in result["text"]
    esprima.parseScript(result["text"])


def test_insertion_inside_a_string_literal_is_skipped_per_insert():
    src = 'var s = "case 0x22:_0xa[_0xb[++_0xc]][_0xa[";\nvar ok = 1;\n'
    esprima.parseScript(src)  # 原脚本合法
    result = apply_inject_groups(src, GROUPS)
    assert result["insertions"] == 0
    assert result["text"] == src  # 一个字都没改
    assert result["skipped_insertions"] == 1
    assert result["literal_skips"][0]["kind"] == "string"
    assert result["groups"][0]["matches"] == 1  # 命中次数照报：正则确实命中了
    assert result["groups"][0]["skipped"] is False
    esprima.parseScript(result["text"])


def test_insertion_inside_a_comment_or_regex_literal_is_skipped():
    regions = literal_regions('/* case 0x22:_0xa[ */ var re = /case 0x22:_0xa[/g;\n')
    kinds = [kind for _, _, kind in regions]
    assert "comment" in kinds and "regex" in kinds
    assert literal_at(regions, 3) == "comment"
    assert literal_at(regions, len('/* case 0x22:_0xa[ */ var re = /case 0x22') + 1) == "regex"
    assert literal_at(regions, len('/* case 0x22:_0xa[ */ var re')) is None


def test_one_broken_group_does_not_drop_the_healthy_one():
    groups = [
        {"find": r"case (0x[0-9a-f]+):", "before": "window.__good=($1)", "group": "good"},
        {"find": r"_0xf=\[3\]", "before": 'if("', "group": "broken"},
    ]
    result = apply_inject_groups(DISPATCH, groups)
    accepted = [g["group"] for g in result["groups"] if not g["skipped"]]
    assert accepted == ["good"]
    assert result["skipped_groups"][0]["group"] == "broken"
    assert result["skipped_groups"][0]["reason"] == "would break syntax"
    assert result["insertions"] == 2
    assert "case 0x22:;window.__good=(0x22);" in result["text"]
    assert 'if("' not in result["text"]
    esprima.parseScript(result["text"])


def test_unparseable_source_skips_validation_but_still_inserts():
    modern = "const o = a?.b ?? 1;\n" + DISPATCH  # esprima 不认识可选链
    with pytest.raises(Exception):
        esprima.parseScript(modern)
    result = apply_inject_groups(modern, GROUPS)
    assert result["validated"] is False
    assert result["validation"] == "source_unparseable"
    assert result["insertions"] == 2


def test_validate_false_leaves_literal_guard_in_place():
    """validate=False 关掉的是解析校验，不是字面量保护：字符串里的命中照样不该插。"""
    src = 'var s = "case 0x22:_0xa[_0xb[++_0xc]][_0xa[";\n'
    result = apply_inject_groups(src, GROUPS, validate=False)
    assert result["insertions"] == 0
    assert result["skipped_insertions"] == 1
    assert result["validated"] is False
    assert result["skipped_groups"] == []


def test_validate_inject_specs_reports_actionable_problems():
    assert validate_inject_specs([{"find": "a", "before": "b"}]) == []
    problems = validate_inject_specs([{"group": "x"}])
    assert any("find is required" in p for p in problems)
    assert any("before / after" in p for p in problems)
    assert any("invalid find regex" in p for p in validate_inject_specs(
        [{"find": "(", "before": "x"}]))
    assert any("unknown keys" in p for p in validate_inject_specs(
        [{"find": "a", "before": "b", "befor": "typo"}]))
    assert validate_inject_specs("nope")[0].startswith("inject must be a list")


def test_no_match_reports_zero_hits_per_group():
    result = apply_inject_groups("var a = 1;", GROUPS)
    assert result["insertions"] == 0
    assert result["validation"] == "no_matches"
    assert result["groups"][0]["matches"] == 0


# ---------------------------------------------------------------- 路由侧


@pytest.fixture
async def harness(monkeypatch):
    class Context:
        async def route(self, pattern, handler):
            self.handler = handler

    context = Context()
    manager = BrowserManager()
    manager.contexts = {"default": context}
    manager.get_active_page = AsyncMock(return_value=SimpleNamespace(url="about:blank"))
    monkeypatch.setattr(instrumentation, "browser_manager", manager)
    monkeypatch.setattr(instrumentation, "_active_routes", {})
    monkeypatch.setattr(instrumentation, "_install_history", {})
    monkeypatch.setattr(instrumentation, "_source_site_registry", {})
    return context


def _route(body: bytes, url="https://example.test/tdc.js?js_data=abc"):
    response = SimpleNamespace(body=AsyncMock(return_value=body), status=200,
                               headers={"content-type": "application/javascript"})
    return SimpleNamespace(request=SimpleNamespace(url=url), fetch=AsyncMock(return_value=response),
                           fulfill=AsyncMock(), abort=AsyncMock())


async def test_install_reports_groups_and_serves_inserted_code(harness):
    result = await instrumentation.instrumentation(
        "install", url_pattern="**/tdc.js*", tag="tdcx", mode="regex",
        inject=GROUPS, tap_stage_calls=True)
    assert result["status"] == "instrumenting"
    assert result["inject_groups"] == ["write-opcode"]
    assert result["inject"][0]["group"] == "write-opcode"

    route = _route(DISPATCH.encode())
    await harness.handler(route)
    body = route.fulfill.call_args.kwargs["body"]
    assert "window.__hit=window.__hit||[]).push([0x22,_0xc])" in body
    # 与 tap_stage_calls 同时生效
    assert "__mcp_tap_stage(9425" in body

    status = instrumentation._get_status()["active_patterns"][0]
    assert status["inject_hits"] == {"write-opcode": 2}
    assert status["inject_skipped"] == {}
    assert status["files_rewritten"] == 1 and status["files_passed_through"] == 0
    assert status["last_inject"]["groups"][0]["matches"] == 2


async def test_zero_inject_hits_is_reported_as_such(harness):
    await instrumentation.instrumentation(
        "install", url_pattern="**/tdc.js*", mode="regex",
        inject=[{"find": r"case 0xff:", "before": "window.__hit=1", "group": "nope"}])

    route = _route(b"var a = 1;")
    await harness.handler(route)
    assert route.fulfill.call_args.kwargs["body"] == b"var a = 1;"  # 原样透传
    status = instrumentation._get_status()["active_patterns"][0]
    [record] = status["skipped_files"]
    assert record["reason"] == "no_inject_matches"
    assert "build-bound" in record["hint"]
    assert status["inject_hits"] == {"nope": 0}


async def test_literal_skip_is_visible_in_status(harness):
    """命中落在字符串里：不插（插了也不执行），但命中数和跳过数都要在 status 里看得到。"""
    src = b'var s = "case 0x22:_0xa[_0xb[++_0xc]][_0xa[";\n'
    await instrumentation.instrumentation("install", url_pattern="**/tdc.js*",
                                         mode="regex", inject=GROUPS)
    route = _route(src)
    await harness.handler(route)
    assert route.fulfill.call_args.kwargs["body"] == src
    status = instrumentation._get_status()["active_patterns"][0]
    assert status["inject_literal_skips"] == 1
    assert status["inject_hits"] == {"write-opcode": 0}
    assert status["inject_skipped"] == {}
    [record] = status["skipped_files"]
    assert record["reason"] == "no_inject_matches"


async def test_install_rejects_an_invalid_inject_spec(harness):
    result = await instrumentation.instrumentation(
        "install", url_pattern="**/tdc.js*", inject=[{"group": "broken"}])
    assert "invalid inject spec" in result["error"]
    assert result["inject_problems"]


async def test_ast_mode_still_works_with_inject(harness):
    await instrumentation.instrumentation("install", url_pattern="**/tdc.js*",
                                         inject=GROUPS)
    route = _route(DISPATCH.encode())
    await harness.handler(route)
    body = route.fulfill.call_args.kwargs["body"]
    assert "push([0x22,_0xc])" in body
    # 默认 ast 模式照旧改写成员访问
    status = instrumentation._get_status()["active_patterns"][0]
    assert status["last_mode_used"] == "ast"
    assert status["total_edits"] > 2


@needs_node
async def test_ast_parse_validates_the_insertion_for_modern_syntax(harness):
    """esprima 读不了的现代语法：模式改写用 acorn 解析成功 ⇒ 注入结果等于被校验过。"""
    modern = "const o = a?.b ?? 1;\n" + DISPATCH
    await instrumentation.instrumentation("install", url_pattern="**/tdc.js*",
                                         inject=GROUPS)
    route = _route(modern.encode())
    await harness.handler(route)
    report = instrumentation._get_status()["active_patterns"][0]["last_inject"]
    assert report["validated"] is True
    assert "parsed by the mode rewrite" in report["validation"]
    assert "push([0x22,_0xc])" in route.fulfill.call_args.kwargs["body"]


async def test_rewrite_source_applies_inject_offline(tmp_path):
    src = tmp_path / "tdc.js"
    src.write_text(DISPATCH, encoding="utf-8")
    out = tmp_path / "tdc.instrumented.js"
    result = await instrumentation.instrumentation(
        "rewrite_source", source_path=str(src), output_path=str(out),
        mode="regex", inject=GROUPS)
    assert result["status"] == "rewritten"
    assert result["inject"]["groups"][0]["matches"] == 2
    assert result["inject"]["validated"] is True
    written = out.read_text(encoding="utf-8")
    assert "push([0x4b,_0xc])" in written
    esprima.parseScript(written)


async def test_rewrite_source_reports_no_inject_matches(tmp_path):
    src = tmp_path / "tdc.js"
    src.write_text("var a = 1;", encoding="utf-8")
    result = await instrumentation.instrumentation(
        "rewrite_source", source_path=str(src),
        inject=[{"find": r"case 0xff:", "before": "x()", "group": "g"}])
    assert result["status"] == "unchanged"
    assert result["reason"] == "no_inject_matches"
    assert result["inject"]["groups"][0]["matches"] == 0
