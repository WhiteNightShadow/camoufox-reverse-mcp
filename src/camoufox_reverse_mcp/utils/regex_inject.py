"""
regex_inject.py - 源码级正则插桩（`instrumentation(inject=[...])` 的纯逻辑部分）。

与 `ast` / `regex` 两种改写模式完全独立：这里不依赖重写 AST，只按调用方给的正则
在匹配的**起点之前**或**结束之后**插入一段完整语句，用来给「换 build 就换 opcode
编号」的控制流扁平化 VM 打点（例如 `case 0x22:` 这类随构建变化的常量）。

语义
====
* `find`    —— Python 正则（`re.MULTILINE`）。匹配数 = 该 group 的命中次数。
* `before`  —— 在 `find` 匹配**起点之前**插入。
* `after`   —— 在 `find` 匹配**结束之后**插入。
* `$1..$99` —— 引用捕获组；`$0` 是整个匹配；`$$` 是字面 `$`；未参与的组展开为空串。
* `group`   —— 该 group 的名字，用于回报命中次数（缺省 `inject1`、`inject2`…）。

安全策略（对应「不得因插入破坏原脚本语法」）
==========================================
1. **语句化插入**：每段插入文本都被包成一条完整语句——首尾均加 `;` 守卫（包括以 `}`
   结尾的对象字面量或箭头函数；含换行或 `//` 行注释时改用 `;\\n…\\n;` 包裹，防止注释吞掉
   尾分号或同一行后续源码）。单行片段插入**不增加换行符**。
2. **字面量区检查（词法扫描 + 二分查找）**：插入点落在字符串 / 模板文本段 / 注释 /
   正则字面量里时**逐处跳过**并报告（`skipped_insertions` + `literal_skips[].kind`）；
   支持模板字符串内嵌 `${...}` 表达式的花括号深度追踪、区分后缀 `++`/`--` 与属性访问
   `obj.return / 2` 除号、以及 `if (...) /re/` 条件括号后的正则字面量。
3. **case / default 标签词法微调**：`before` 落在 `switch` 的 `case <expr>:` / `default:`
   标签处时，跳过字面量、括号与三元运算符 `? :` 自动挪到该标签冒号之后（`label_shift`），
   同时排除对象字面量属性 `{default: ...}`。
4. **现代语法解析校验（esprima + 本地随包 Acorn）**：原脚本可解析（含 `?.`、`??`、`#priv`、
   BigInt、ES Module）时，插入后再校验一次；若整体不通过则**逐个 group 回退**，只保留插入后
   仍可解析的 group，其余整组跳过并标记 `reason: "would break syntax"`。
"""
from __future__ import annotations

import bisect
from pathlib import Path
import re
import shutil
import subprocess

#: 超过这个大小的源码不再做解析校验，只依赖语句化插入与词法守卫。
VALIDATE_LIMIT = 2_000_000

#: 单文件最多允许注入的片段条数上限（防零宽/高频正则失控）。
DEFAULT_MAX_INJECTIONS = 50_000

#: `$0`..`$99` 与 `$$`。
_EXPAND_RE = re.compile(r"\$(\$|\d{1,2})")

#: 一个 group 允许出现的键；其余键当作拼写错误报出来。
_GROUP_KEYS = ("find", "before", "after", "group")

#: 一个 `/` 前面出现这些单字符 token 时（排除 `++` / `--` 后），它是正则字面量开头。
_REGEX_PREFIX_CHARS = set("(,=:[!&|?{};+-*%~^<>")
_REGEX_PREFIX_WORDS = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "do",
    "else", "yield", "await", "case", "throw",
})
_COND_PAREN_WORDS = frozenset({"if", "while", "for", "with"})
_LABEL_KEYWORD_RE = re.compile(r"(case|default)(?![0-9A-Za-z_$])")

_ACORN_CHECK_SNIPPET = (
    "const fs=require('node:fs'),acorn=require(process.argv[1]);"
    "const s=fs.readFileSync(0,'utf8'),o={ecmaVersion:'latest',allowHashBang:true};"
    "try{acorn.parse(s,{...o,sourceType:'script'});}"
    "catch(e){acorn.parse(s,{...o,sourceType:'module'});}"
)


def _prev_word(src: str, pos: int) -> tuple[str, int]:
    """给定标识符末尾下标 `pos`，返回 `(word, before_word_non_ws_pos)`。"""
    end = pos + 1
    while pos >= 0 and (src[pos].isalnum() or src[pos] in "_$"):
        pos -= 1
    word = src[pos + 1:end]
    before = pos
    while before >= 0 and src[before] in " \t\r\n":
        before -= 1
    return word, before


def _regex_allowed(src: str, index: int, close_paren_allows_regex: set[int] | None = None) -> bool:
    """`/` 该被当成正则字面量开头吗（词法前驱判定）。"""
    pos = index - 1
    while pos >= 0 and src[pos] in " \t\r\n":
        pos -= 1
    if pos < 0:
        return True
    char = src[pos]
    if char in "+-" and pos >= 1 and src[pos - 1] == char:
        # JS 语法里 `++` / `--` 后面绝不可能是正则字面量（如 `i++/2` 必为除号）。
        return False
    if char == ")":
        return bool(close_paren_allows_regex and pos in close_paren_allows_regex)
    if char in _REGEX_PREFIX_CHARS:
        return True
    if char.isalnum() or char in "_$":
        word, before = _prev_word(src, pos)
        if before >= 0 and src[before] == ".":
            # 成员属性访问（如 `obj.return / 2`、`a?.in / 2`），不是关键字。
            return False
        return word in _REGEX_PREFIX_WORDS
    return False


def _scan_template_chunk(src: str, start: int, index: int,
                         regions: list[tuple[int, int, str]],
                         template_brace_stack: list[int]) -> int:
    """从 `index` 继续扫描模板字符串文本段，遇到未转义 `${` 压栈转回常规词法扫描。"""
    length = len(src)
    while index < length:
        current = src[index]
        if current == "\\":
            index += 2
            continue
        if current == "$" and index + 1 < length and src[index + 1] == "{":
            regions.append((start, index + 2, "template"))
            template_brace_stack.append(0)
            return index + 2
        index += 1
        if current == "`":
            regions.append((start, min(index, length), "template"))
            return index
    regions.append((start, length, "template"))
    return length


def literal_regions(src: str) -> list[tuple[int, int, str]]:
    """单遍词法扫描：返回按起点升序互不重叠的 `[(start, end, kind)]`。

    kind 为 `string` / `template` / `line-comment` / `comment` / `regex`。
    """
    regions: list[tuple[int, int, str]] = []
    paren_stack: list[bool] = []
    close_paren_allows_regex: set[int] = set()
    template_brace_stack: list[int] = []
    length = len(src)
    index = 0
    while index < length:
        char = src[index]
        if char in "'\"":
            start = index
            index += 1
            while index < length:
                current = src[index]
                if current == "\\":
                    index += 2
                    continue
                index += 1
                if current == char or current in "\r\n":
                    break
            regions.append((start, min(index, length), "string"))
            continue
        if char == "`":
            index = _scan_template_chunk(src, index, index + 1, regions, template_brace_stack)
            continue
        if src.startswith("//", index):
            start = index
            end = src.find("\n", index)
            index = length if end == -1 else end
            regions.append((start, index, "line-comment"))
            continue
        if src.startswith("/*", index):
            start = index
            end = src.find("*/", index + 2)
            index = length if end == -1 else end + 2
            regions.append((start, index, "comment"))
            continue
        if char == "/" and _regex_allowed(src, index, close_paren_allows_regex):
            start = index
            index += 1
            in_class = False
            while index < length:
                current = src[index]
                if current == "\\":
                    index += 2
                    continue
                index += 1
                if current in "\r\n":
                    break
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    break
            while index < length and src[index].isalpha():
                index += 1
            regions.append((start, min(index, length), "regex"))
            continue
        if char == "(":
            pos = index - 1
            while pos >= 0 and src[pos] in " \t\r\n":
                pos -= 1
            is_cond = False
            if pos >= 0 and (src[pos].isalnum() or src[pos] in "_$"):
                word, before = _prev_word(src, pos)
                is_cond = (word in _COND_PAREN_WORDS) and not (before >= 0 and src[before] == ".")
            paren_stack.append(is_cond)
        elif char == ")":
            if paren_stack and paren_stack.pop():
                close_paren_allows_regex.add(index)
        elif char == "{" and template_brace_stack:
            template_brace_stack[-1] += 1
        elif char == "}" and template_brace_stack:
            if template_brace_stack[-1] > 0:
                template_brace_stack[-1] -= 1
            else:
                template_brace_stack.pop()
                index = _scan_template_chunk(src, index, index + 1, regions, template_brace_stack)
                continue
        index += 1
    return regions


def _region_at(regions: list[tuple[int, int, str]], offset: int) -> tuple[int, int, str] | None:
    """二分查找 `offset` 所在的字面量/注释区间，时间复杂度 `O(log R)`。"""
    if not regions:
        return None
    idx = bisect.bisect_right(regions, (offset, float("inf"), "\uffff")) - 1
    if idx >= 0:
        region = regions[idx]
        if region[0] <= offset < region[1]:
            return region
    return None


def literal_at(regions: list[tuple[int, int, str]], offset: int) -> str | None:
    """偏移是否落在某个字面量区里；是则返回该区的 kind。"""
    region = _region_at(regions, offset)
    return region[2] if region is not None else None


def validate_inject_specs(groups) -> list[str]:
    """返回 spec 的问题列表（空列表 = 全部可用）。用于工具层快速失败。"""
    if not isinstance(groups, (list, tuple)):
        return ["inject must be a list of {find, before|after, group} objects"]
    problems: list[str] = []
    for index, spec in enumerate(groups):
        where = f"inject[{index}]"
        if not isinstance(spec, dict):
            problems.append(f"{where}: must be an object")
            continue
        unknown = [key for key in spec if key not in _GROUP_KEYS]
        if unknown:
            problems.append(f"{where}: unknown keys {sorted(unknown)}; "
                            f"expected {list(_GROUP_KEYS)}")
        find = spec.get("find")
        if not isinstance(find, str) or not find:
            problems.append(f"{where}: find is required and must be a non-empty regex")
        else:
            try:
                re.compile(find, re.MULTILINE)
            except re.error as exc:
                problems.append(f"{where}: invalid find regex: {exc}")
        if not spec.get("before") and not spec.get("after"):
            problems.append(f"{where}: pass at least one of before / after")
        for key in ("before", "after"):
            value = spec.get(key)
            if value is not None and not isinstance(value, str):
                problems.append(f"{where}: {key} must be a string")
    return problems


def expand_template(template: str, match: re.Match) -> str:
    """把 `$1` / `$0` / `$$` 展开成捕获组内容。未参与的组展开为空串。"""

    def replace(found: re.Match) -> str:
        token = found.group(1)
        if token == "$":
            return "$"
        try:
            value = match.group(int(token))
        except (IndexError, re.error):
            return ""
        return "" if value is None else value

    return _EXPAND_RE.sub(replace, template)


def normalize_snippet(text: str) -> str:
    """把片段包成一条完整语句，返回可直接内联插入的文本。"""
    body = text.strip()
    if not body:
        return ""
    multiline = ("\n" in body) or ("//" in body)
    if multiline:
        # 尾分号放在换行之后，避免被 `//` 行注释吞掉导致粘连后续 `(expr)` / `[idx]`。
        return ";\n" + body + "\n;"
    if not body.endswith(";"):
        # 即使以 `}` 结尾（如 `window.__x={op:1}` 或 `()=>{}`）也必须补分号；
        # 若原本是块语句 `if(c){...}`，多一个空语句 `;` 在语句位置同样合法。
        body += ";"
    return ";" + body


def _acorn_parses(src: str) -> bool | None:
    """使用本地随包 Acorn 校验现代 JS（ES2025 script/module），不走任何网络。"""
    node = shutil.which("node")
    acorn_path = Path(__file__).resolve().parents[1] / "vendor" / "acorn.cjs"
    if not node or not acorn_path.is_file():
        return None
    data = src.encode("utf-8", errors="surrogatepass")
    if len(data) > VALIDATE_LIMIT * 2:
        return None
    try:
        proc = subprocess.run(
            [node, "--max-old-space-size=256", "-e", _ACORN_CHECK_SNIPPET, str(acorn_path)],
            input=data, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode == 0


def _default_parses(src: str) -> bool | None:
    """先用进程内 esprima 快速判定，遇到现代语法（`?.`、`??`、`#priv`、ESM）回退本地 Acorn。"""
    has_esprima = False
    try:
        import esprima
        has_esprima = True
        try:
            esprima.parseScript(src, options={"tolerant": False})
            return True
        except Exception:
            try:
                esprima.parseModule(src, options={"tolerant": False})
                return True
            except Exception:
                pass
    except Exception:
        pass

    acorn_ok = _acorn_parses(src)
    if acorn_ok is not None:
        return acorn_ok
    return False if has_esprima else None


def _apply_edits(src: str, edits: list[dict]) -> str:
    """按 start 顺序插入零宽片段（同一位置的片段按收集顺序）。"""
    parts: list[str] = []
    pos = 0
    for edit in sorted(edits, key=lambda item: (item["start"], item["order"])):
        if edit["start"] < pos:
            continue
        parts.append(src[pos:edit["start"]])
        parts.append(edit["text"])
        pos = edit["start"]
    parts.append(src[pos:])
    return "".join(parts)


def _prev_code_pos(src: str, pos: int, regions: list[tuple[int, int, str]]) -> int:
    """从 `pos - 1` 向前跳过空白与注释，返回前一个有效代码字符下标（无则 -1）。"""
    cur = pos - 1
    while cur >= 0:
        if src[cur] in " \t\r\n":
            cur -= 1
            continue
        reg = _region_at(regions, cur)
        if reg is not None and reg[2] in ("comment", "line-comment"):
            cur = reg[0] - 1
            continue
        return cur
    return -1


def _is_switch_block_open(src: str, brace_pos: int, regions: list[tuple[int, int, str]]) -> bool:
    """判断 `{`（位于 `brace_pos`）是否紧跟在 `switch (...)` 之后。"""
    close_paren = _prev_code_pos(src, brace_pos, regions)
    if close_paren < 0 or src[close_paren] != ")":
        return False
    depth = 1
    cur = close_paren - 1
    while cur >= 0 and depth > 0:
        reg = _region_at(regions, cur)
        if reg is not None:
            cur = reg[0] - 1
            continue
        ch = src[cur]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                break
        cur -= 1
    if depth != 0:
        return False
    word_end = _prev_code_pos(src, cur, regions)
    if word_end < 0 or not (src[word_end].isalnum() or src[word_end] in "_$"):
        return False
    word, before = _prev_word(src, word_end)
    if word != "switch":
        return False
    return not (before >= 0 and src[before] == ".")


def _enclosing_brace_pos(src: str, pos: int, regions: list[tuple[int, int, str]]) -> int:
    """向左查找包围 `pos` 的最近未闭合 `{` 下标（找不到返回 -1）。"""
    depth = 0
    cur = pos - 1
    while cur >= 0:
        reg = _region_at(regions, cur)
        if reg is not None:
            cur = reg[0] - 1
            continue
        ch = src[cur]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return cur
            depth -= 1
        cur -= 1
    return -1


def _find_switch_label_end(src: str, start: int,
                           regions: list[tuple[int, int, str]] | None = None,
                           require_switch_context: bool = True) -> int | None:
    """若 `src[start:]` 位于 `switch` 的 `case <expr>:` / `default:` 标签头，返回冒号后绝对偏移。

    自动跳过字面量/注释、圆括号/方括号/花括号嵌套以及三元表达式 `? :`，
    并排除对象字面量属性 `{default: expr}` / `{case: expr}`。
    """
    m = _LABEL_KEYWORD_RE.match(src, start)
    if not m:
        return None
    if regions is None:
        regions = literal_regions(src)
    if literal_at(regions, start) is not None:
        return None
    keyword = m.group(1)

    if require_switch_context:
        prev = _prev_code_pos(src, start, regions)
        if prev >= 0 and src[prev] not in "{;}:":
            return None
        if keyword == "default":
            brace = _enclosing_brace_pos(src, start, regions)
            if brace >= 0 and not _is_switch_block_open(src, brace, regions):
                return None

    pos = m.end()
    length = len(src)
    if keyword == "default":
        while pos < length:
            if src[pos] in " \t\r\n":
                pos += 1
                continue
            reg = _region_at(regions, pos)
            if reg is not None and reg[2] in ("comment", "line-comment"):
                pos = reg[1]
                continue
            break
        return pos + 1 if pos < length and src[pos] == ":" else None

    # `case <expr>:` —— 必须有非空表达式（排除对象字面量 `{case: 1}`）。
    saw_expr_token = False
    bracket_depth = 0
    ternary_depth = 0
    while pos < length:
        reg = _region_at(regions, pos)
        if reg is not None:
            if reg[2] not in ("comment", "line-comment"):
                saw_expr_token = True
            pos = reg[1]
            continue
        ch = src[pos]
        if ch in " \t\r\n":
            pos += 1
            continue
        if ch in "([{":
            saw_expr_token = True
            bracket_depth += 1
            pos += 1
            continue
        if ch in ")]}":
            if bracket_depth == 0:
                return None
            bracket_depth -= 1
            pos += 1
            continue
        if bracket_depth == 0:
            if ch == ";":
                return None
            if ch == "?" and src[pos:pos + 2] not in ("?.", "??"):
                ternary_depth += 1
                saw_expr_token = True
                pos += 1
                continue
            if ch == ":":
                if ternary_depth > 0:
                    ternary_depth -= 1
                    pos += 1
                    continue
                return pos + 1 if saw_expr_token else None
        saw_expr_token = True
        pos += 1
    return None


def case_label_end(match_text: str) -> int | None:
    """匹配文本以 `case <expr>:` / `default:` 开头时，返回冒号之后的相对偏移。"""
    return _find_switch_label_end(match_text, 0, require_switch_context=False)


def collect_inject_edits(src: str, groups, *,
                         max_injections: int = DEFAULT_MAX_INJECTIONS) -> tuple[list[dict], list[dict], bool]:
    """收集所有 group 的插入点，返回 `(edits, 每组报告, capped)`。"""
    edits: list[dict] = []
    report: list[dict] = []
    regions: list[tuple[int, int, str]] | None = None
    order = 0
    capped = False
    cap = max(1, int(max_injections or DEFAULT_MAX_INJECTIONS))
    for index, spec in enumerate(groups or []):
        name = str(spec.get("group") or f"inject{index + 1}")
        entry = {"group": name, "index": index, "find": spec.get("find"),
                 "matches": 0, "insertions": 0, "skipped": False, "reason": None,
                 "error": None, "label_shift": 0, "skipped_insertions": 0,
                 "literal_skips": []}
        report.append(entry)
        pattern = spec.get("find")
        if not isinstance(pattern, str) or not pattern:
            entry.update({"error": "find is empty", "skipped": True,
                          "reason": "invalid regex"})
            continue
        try:
            regex = re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            entry.update({"error": f"invalid find regex: {exc}", "skipped": True,
                          "reason": "invalid regex"})
            continue
        if regions is None:
            regions = literal_regions(src)
        group_edits: list[dict] = []
        for match in regex.finditer(src):
            entry["matches"] += 1
            for kind in ("before", "after"):
                template = spec.get(kind)
                if not template:
                    continue
                if len(edits) + len(group_edits) >= cap:
                    capped = True
                    break
                snippet = normalize_snippet(expand_template(str(template), match))
                if not snippet:
                    continue
                at = match.start() if kind == "before" else match.end()
                shifted = False
                if kind == "before":
                    inside_match = literal_at(regions, match.start())
                    if inside_match is None:
                        abs_label_end = _find_switch_label_end(src, match.start(), regions)
                        if abs_label_end is not None:
                            at = abs_label_end
                            shifted = True
                            entry["label_shift"] += 1
                inside = literal_at(regions, at)
                if inside is not None:
                    entry["skipped_insertions"] += 1
                    if len(entry["literal_skips"]) < 20:
                        entry["literal_skips"].append({"kind": inside, "offset": at,
                                                       "kind_of_insert": kind})
                    continue
                group_edits.append({"start": at, "end": at, "text": snippet,
                                    "group": name, "index": index, "kind": kind,
                                    "order": order, "label_shift": shifted})
                order += 1
            if capped:
                break
        entry["insertions"] = len(group_edits)
        edits.extend(group_edits)
        if capped:
            entry["capped"] = True
            break
    return edits, report, capped


def apply_inject_groups(src: str, groups, *, validate: bool = True,
                        parser=None, max_injections: int = DEFAULT_MAX_INJECTIONS) -> dict:
    """对一份源码应用 `inject` spec。"""
    parses = parser or _default_parses
    edits, report, capped = collect_inject_edits(src, groups, max_injections=max_injections)
    literal_skips = [{"group": entry["group"], **skip}
                     for entry in report for skip in entry.get("literal_skips") or []]
    result = {"text": src, "insertions": 0, "groups": report, "skipped_groups": [],
              "validated": False, "validation": "no_groups", "lines_added": 0,
              "capped": capped,
              "skipped_insertions": sum(int(entry.get("skipped_insertions") or 0)
                                        for entry in report),
              "literal_skips": literal_skips}
    if not edits:
        result["validation"] = "no_matches"
        result["skipped_groups"] = [{"group": entry["group"], "reason": entry.get("reason"),
                                     "matches": entry.get("matches", 0),
                                     "error": entry.get("error")}
                                    for entry in report if entry.get("skipped")]
        return result

    accepted = edits
    all_text = _apply_edits(src, edits)
    result["validation"] = "skipped"
    if validate and len(src) <= VALIDATE_LIMIT:
        baseline = parses(src)
        if baseline is True:
            if parses(all_text) is True:
                result["validation"] = "ok"
                result["validated"] = True
            else:
                accepted = []
                for entry in report:
                    own = [edit for edit in edits if edit["index"] == entry["index"]]
                    if not own:
                        continue
                    if parses(_apply_edits(src, accepted + own)) is True:
                        accepted = accepted + own
                    else:
                        entry.update({"skipped": True, "insertions": 0,
                                      "reason": "would break syntax",
                                      "error": "inserting this group makes the source "
                                               "unparseable (check snippet syntax or match "
                                               "boundary) — narrow `find`, or move the "
                                               "insert with `after`"})
                result["validation"] = "group_fallback"
                result["validated"] = True
                all_text = _apply_edits(src, accepted)
        elif baseline is False:
            result["validation"] = "source_unparseable"
        else:
            result["validation"] = "parser_unavailable"

    result["text"] = all_text
    result["insertions"] = len(accepted)
    result["lines_added"] = all_text.count("\n") - src.count("\n")
    result["skipped_insertions"] = sum(int(entry.get("skipped_insertions") or 0)
                                        for entry in report)
    result["skipped_groups"] = [
        {"group": entry["group"], "reason": entry.get("reason"),
         "matches": entry.get("matches", 0), "error": entry.get("error")}
        for entry in report if entry.get("skipped")
    ]
    return result
