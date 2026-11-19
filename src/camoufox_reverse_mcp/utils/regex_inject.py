"""
regex_inject.py - 源码级正则插桩（`instrumentation(inject=[...])` 的纯逻辑部分）。

与 `ast` / `regex` 两种改写模式完全独立：这里不解析程序，只按调用方给的正则
在匹配的**起点之前**或**结束之后**插入一段完整语句，用来给「换 build 就换 opcode
编号」的控制流扁平化 VM 打点（例如 `case 0x22:` 这类每个 build 都不同的常量）。

语义
====
* `find`    —— Python 正则（`re.MULTILINE`）。匹配数 = 该 group 的命中次数。
* `before`  —— 在 `find` 匹配**起点之前**插入。
* `after`   —— 在 `find` 匹配**结束之后**插入。
* `$1..$99` —— 引用捕获组；`$0` 是整个匹配；`$$` 是字面 `$`；未参与的组展开为空串。
* `group`   —— 该 group 的名字，用于回报命中次数（缺省 `inject1`、`inject2`…）。

安全策略（对应「不得因插入破坏原脚本语法」）
==========================================
1. **语句化插入**：每段插入文本都被包成一条完整语句——前面补 `;`（防止和上一个 token
   粘成一次调用），末尾不是 `;` / `}` 时补 `;`。所以插入点落在任何语句边界都合法。
2. **换行策略**：片段本身含换行或 `//` 行注释时改用 `;\n…\n` 包裹（否则行注释会吃掉
   同一行后面的原代码）；其余情况保持单行，插入**不改变原脚本的行号**（断点行号仍可用）。
3. **字面量区检查（词法，单遍扫描）**：插入点落在字符串 / 模板 / 注释 / 正则字面量里时
   **跳过这一处插入**并报告（`skipped_insertions` + `literal_skips[].kind`）。这类插入即使
   侥幸不破坏语法也永远不会执行（代码只是字符串内容），所以不能只靠解析校验。
4. **case 标签微调**：`before` 落在 `case <expr>:` / `default:` 标签前时，自动挪到该标签
   的冒号之后（报告里的 `label_shift`）。因为 switch 的 CaseBlock 只允许 case/default
   子句——第一个标签前的语句是语法错误，后面的标签前的语句属于上一个子句（直接跳到这个
   case 时不执行）。挪到冒号后才是调用方想要的「这个状态一进来就打点」。
5. **解析校验（best effort）**：原脚本能被 esprima 解析时，插入后再解析一次；如果插入
   把一份原本合法的文件解析崩了（其它语法位置问题），就**逐个 group 回退**——只保留插入
   后仍可解析的 group，其余整组跳过，并在报告里写 `skipped: true,
   reason: "would break syntax"`。原脚本本来就解析不了（现代语法、混淆产物常见）时不做
   校验，报告 `validated: false` / `validation: "source_unparseable"`，此时依赖策略 1/3。
"""
from __future__ import annotations

import re

#: 超过这个大小的源码不再做解析校验（esprima 太慢），只依赖语句化插入。
VALIDATE_LIMIT = 2_000_000

#: `$0`..`$99` 与 `$$`。
_EXPAND_RE = re.compile(r"\$(\$|\d{1,2})")

#: 一个 group 允许出现的键；其余键当作拼写错误报出来。
_GROUP_KEYS = ("find", "before", "after", "group")

#: `case <expr>:` / `default:` 标签（非贪婪到第一个冒号）。
_CASE_LABEL_RE = re.compile(r"(?:case\b[^:]*?|default)\s*:")

#: 一个 `/` 前面出现这些 token 时，它是正则字面量的开头而不是除号。
_REGEX_PREFIX_CHARS = set("(,=:[!&|?{};+-*%~^<>")
_REGEX_PREFIX_WORDS = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "do",
    "else", "yield", "await", "case", "throw",
})


def _regex_allowed(src: str, index: int) -> bool:
    """`/` 该被当成正则字面量开头吗（经典启发式：看前一个有效 token）。"""
    pos = index - 1
    while pos >= 0 and src[pos] in " \t\r\n":
        pos -= 1
    if pos < 0:
        return True
    char = src[pos]
    if char in _REGEX_PREFIX_CHARS:
        return True
    if char.isalnum() or char in "_$":
        end = pos + 1
        while pos >= 0 and (src[pos].isalnum() or src[pos] in "_$"):
            pos -= 1
        return src[pos + 1:end] in _REGEX_PREFIX_WORDS
    return False


def literal_regions(src: str) -> list[tuple[int, int, str]]:
    """单遍词法扫描：返回 `[(start, end, kind)]`。

    kind 为 `string` / `template` / `line-comment` / `comment` / `regex`。
    best effort：模板字符串整段算字面量（`${}` 里的代码也一起算），正则用经典启发式
    判定。漏判由解析校验兜底；误判只会让一处插入被跳过并报告。
    """
    regions: list[tuple[int, int, str]] = []
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
            start = index
            index += 1
            while index < length:
                current = src[index]
                if current == "\\":
                    index += 2
                    continue
                index += 1
                if current == "`":
                    break
            regions.append((start, min(index, length), "template"))
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
        if char == "/" and _regex_allowed(src, index):
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
            regions.append((start, min(index, length), "regex"))
            continue
        index += 1
    return regions


def literal_at(regions: list[tuple[int, int, str]], offset: int) -> str | None:
    """偏移是否落在某个字面量区里；是则返回该区的 kind。"""
    for start, end, kind in regions:
        if start >= offset:
            break
        if offset < end:
            return kind
    return None


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
    if not body.endswith(";") and not body.endswith("}"):
        body += ";"
    return ";\n" + body + "\n" if multiline else ";" + body


def _esprima_parses(src: str) -> bool | None:
    """True / False；esprima 不可用时返回 None（不知情）。"""
    try:
        import esprima
    except Exception:
        return None
    try:
        esprima.parseScript(src, options={"range": True, "tolerant": False})
        return True
    except Exception:
        return False


def _apply_edits(src: str, edits: list[dict]) -> str:
    """按 start 顺序插入零宽片段（同一位置的片段按收集顺序）。"""
    parts: list[str] = []
    pos = 0
    for edit in sorted(edits, key=lambda item: (item["start"], item["order"])):
        if edit["start"] < pos:  # 理论上不会发生：全部是零宽插入
            continue
        parts.append(src[pos:edit["start"]])
        parts.append(edit["text"])
        pos = edit["start"]
    parts.append(src[pos:])
    return "".join(parts)


def case_label_end(match_text: str) -> int | None:
    """匹配文本以 `case <expr>:` / `default:` 开头时，返回冒号之后的偏移。

    JS 的 CaseBlock 语法只允许 `case`/`default` 子句，**第一个** `case` 标签前面的
    语句是 SyntaxError；而落在后面某个 `case` 前的语句会属于上一个子句（直接跳到这个
    case 时不执行）。两种情况都不是调用方想要的，所以 `before` 落在标签前时统一挪到
    冒号之后（`label_shift`）。
    """
    found = _CASE_LABEL_RE.match(match_text)
    return found.end() if found else None


def collect_inject_edits(src: str, groups) -> tuple[list[dict], list[dict]]:
    """收集所有 group 的插入点，返回 (edits, 每组报告)。

    落在字符串 / 模板 / 注释 / 正则字面量里的插入点会被跳过并记进
    `skipped_insertions` / `literal_skips`（那份代码永远不会执行）。
    """
    edits: list[dict] = []
    report: list[dict] = []
    regions: list[tuple[int, int, str]] | None = None
    order = 0
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
            # `before` 先收集：同一偏移上保证 before 落在 after 之前。
            for kind in ("before", "after"):
                template = spec.get(kind)
                if not template:
                    continue
                snippet = normalize_snippet(expand_template(str(template), match))
                if not snippet:
                    continue
                at = match.start() if kind == "before" else match.end()
                shifted = False
                if kind == "before":
                    label_end = case_label_end(match.group(0))
                    if label_end is not None:
                        # 挪到 `case <expr>:` 冒号之后：既避免 switch 里第一个标签前的
                        # 语法错误，也保证直接跳到这个 case 时真的执行。
                        at = match.start() + label_end
                        shifted = True
                        entry["label_shift"] += 1
                inside = literal_at(regions, at)
                if inside is not None:
                    # 插进字符串/注释/正则字面量：即使不破坏语法也不会执行，跳过这一处。
                    entry["skipped_insertions"] += 1
                    if len(entry["literal_skips"]) < 20:
                        entry["literal_skips"].append({"kind": inside, "offset": at,
                                                       "kind_of_insert": kind})
                    continue
                group_edits.append({"start": at, "end": at, "text": snippet,
                                    "group": name, "index": index, "kind": kind,
                                    "order": order, "label_shift": shifted})
                order += 1
        entry["insertions"] = len(group_edits)
        edits.extend(group_edits)
    return edits, report


def apply_inject_groups(src: str, groups, *, validate: bool = True,
                        parser=None) -> dict:
    """对一份源码应用 `inject` spec。

    Args:
        src: 原始解码文本。
        groups: `[{find, before|after, group}]`。
        validate: 是否做解析校验（原脚本本身解析不了时自动退化为不校验）。
        parser: 注入点（测试用）：`callable(src) -> bool | None`。

    Returns:
        `{"text", "insertions", "groups", "skipped_groups", "skipped_insertions",
          "literal_skips", "validated", "validation", "lines_added"}`。
        `text` 是插入后的文本（无插入时原样返回）。
    """
    parses = parser or _esprima_parses
    edits, report = collect_inject_edits(src, groups)
    literal_skips = [{"group": entry["group"], **skip}
                     for entry in report for skip in entry.get("literal_skips") or []]
    result = {"text": src, "insertions": 0, "groups": report, "skipped_groups": [],
              "validated": False, "validation": "no_groups", "lines_added": 0,
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
                # 逐个 group 回退：只保留插入后仍然可解析的 group。
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
                                               "unparseable (match likely inside a string, "
                                               "comment or regex literal; a statement before "
                                               "the first `case` label is invalid too) — "
                                               "narrow `find`, or move the insert with `after`"})
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
