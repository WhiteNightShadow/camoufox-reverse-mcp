"""Firefox RDP 断点调试：`rdp_debugger(action=...)`。

Playwright 的 Firefox 走 Juggler，没有 CDP，所以 Playwright 侧拿不到「断点 / 单步 /
调用栈 / 作用域 / 暂停中求值」。这里直接走 Firefox 原生 RDP
（`-start-debugger-server`）补齐，全程不经过 Playwright，也不需要浏览器扩展。

因为每个 action 都可能要等几百毫秒到几十秒（启动浏览器、等断点命中、分片取源码），
所有阻塞动作都放进 `asyncio.to_thread`，不会卡住 MCP 事件循环或 Playwright 那边的
浏览器事件。

安全提示：RDP 端口没有认证，能连上 127.0.0.1:<port> 的本机进程都可以用浏览器权限执行
代码。只在 127.0.0.1 上使用，不要把端口暴露到外网；本工具也不会去杀不是自己启动的进程。
"""
from __future__ import annotations

import asyncio

from ..server import mcp
from ..debugger_rdp_tools import FireFoxDebugger, RdpError

_STATE: dict = {"dbg": None, "last_paused": None}

ACTIONS = (
    "launch", "attach", "enable", "sources", "source", "breakable_lines",
    "set_breakpoint", "wait_paused", "frames", "scopes", "evaluate", "step",
    "resume", "interrupt", "reload", "xhr_breakpoint", "status", "close",
)


def _dbg() -> FireFoxDebugger:
    dbg = _STATE.get("dbg")
    if dbg is None:
        raise RdpError("调试器未启动：先用 action=\"launch\" 或 action=\"attach\"")
    return dbg


def _frames(fs: list[dict]) -> list[dict]:
    return [{"actor": f.get("actor"), "displayName": f.get("displayName"),
             "where": f.get("where")} for f in fs]


def _sync_dispatch(action: str, kw: dict) -> dict:
    """阻塞实现（在 asyncio.to_thread 里跑）。"""
    dbg = _STATE.get("dbg")

    if action == "launch":
        d = FireFoxDebugger(port=kw["port"],
                            exe=kw["executable"] or None)
        try:
            d.launch(kw["url"], wait_page=kw["wait_page"])
        except Exception:
            # 启动到一半失败时也要把自己拉起的浏览器清掉，否则端口会被占住
            d.close()
            raise
        _STATE["dbg"] = d
        return {"status": "launched", "port": d.port, "url": kw["url"],
                "profile": d.profile, "executable": d.exe}

    if action == "attach":
        d = FireFoxDebugger(port=kw["port"])
        d.attach(kw["port"])
        _STATE["dbg"] = d
        return {"status": "connected", "port": kw["port"]}

    if action == "status":
        if dbg is None:
            return {"running": False}
        return {"running": True, "port": dbg.port, "profile": dbg.profile,
                "tab": dbg.tab, "target": dbg.target, "thread": dbg.thread,
                "console": dbg.console,
                "last_paused": (_STATE.get("last_paused") or {}).get("why"),
                "breakpoints": dbg.breakpoints}

    if action == "close":
        if dbg is None:
            return {"status": "not_running"}
        dbg.close()
        _STATE["dbg"] = None
        _STATE["last_paused"] = None
        return {"status": "closed"}

    # 以下是需要已有会话的动作
    dbg = _dbg()

    if action == "enable":
        return dbg.enable(kw["url_substr"] or None, kw["target_type"])

    if action == "sources":
        srcs = dbg.sources()
        if kw["url_substr"]:
            srcs = [s for s in srcs if kw["url_substr"] in str(s.get("url", ""))]
        out = [{"actor": s.get("actor"), "url": s.get("url"),
                "introductionType": s.get("introductionType"),
                "isInlineSource": s.get("isInlineSource"),
                "sourceStartLine": s.get("sourceStartLine")} for s in srcs[:kw["max_items"]]]
        return {"total": len(srcs), "sources": out}

    if action == "source":
        src = next((s for s in dbg.sources()
                    if kw["url_substr"] in str(s.get("url", ""))), None)
        if not src:
            raise RdpError("没找到脚本: %r" % kw["url_substr"])
        txt = dbg.source_text(src["actor"], max_chars=kw["max_chars"])
        out = {"actor": src["actor"], "url": src.get("url"),
               "length": len(txt), "lines": txt.count("\n") + 1}
        if kw["save_path"]:
            with open(kw["save_path"], "w", encoding="utf-8", newline="") as f:
                f.write(txt)
            out["saved"] = kw["save_path"]
        else:
            out["head"] = txt[:400]
        return out

    if action == "breakable_lines":
        src = next((s for s in dbg.sources()
                    if kw["url_substr"] in str(s.get("url", ""))), None)
        if not src:
            raise RdpError("没找到脚本: %r" % kw["url_substr"])
        return {"actor": src["actor"], "url": src.get("url"),
                "lines": dbg.breakable_lines(src["actor"])[:kw["max_items"]]}

    if action == "set_breakpoint":
        if not kw["line"]:
            raise RdpError("action=\"set_breakpoint\" 需要 line")
        r = dbg.set_breakpoint(kw["url_substr"], kw["line"], kw["column"], kw["condition"])
        return {"status": "ok" if "error" not in r else "error", "reply": r}

    if action == "wait_paused":
        p = dbg.wait_paused(kw["timeout_ms"] / 1000.0, require_frame=kw["require_frame"])
        if not p:
            return {"paused": False}
        _STATE["last_paused"] = p
        fr = p.get("frame") or {}
        return {"paused": True, "frame": fr.get("actor"), "where": fr.get("where"),
                "why": p.get("why"), "thread": p.get("from")}

    if action == "frames":
        fs = dbg.frames()
        return {"count": len(fs), "frames": _frames(fs)}

    if action == "scopes":
        if not kw["frame_actor"]:
            raise RdpError("action=\"scopes\" 需要 frame_actor（来自 wait_paused/frames）")
        return dbg.scopes(kw["frame_actor"])

    if action == "evaluate":
        if not kw["expression"]:
            raise RdpError("action=\"evaluate\" 需要 expression")
        if kw["frame_actor"]:
            r = dbg.frame_eval(kw["frame_actor"], kw["expression"],
                               kw["timeout_ms"] / 1000.0)
            return {"hasException": r.get("hasException"), "result": r.get("result"),
                    "input": r.get("input"), "frame": kw["frame_actor"]}
        r = dbg.evaluate(kw["expression"], kw["timeout_ms"] / 1000.0)
        return {"hasException": r.get("hasException"), "result": r.get("result"),
                "input": r.get("input")}

    if action == "step":
        dbg.step(kw["direction"], kw["frame_actor"] or None)
        return {"status": "stepped", "direction": kw["direction"]}

    if action == "resume":
        return {"status": "resumed", "reply": dbg.resume()}

    if action == "interrupt":
        p = dbg.interrupt(kw["on_next"], kw["timeout_ms"] / 1000.0)
        if not p:
            return {"paused": False}
        _STATE["last_paused"] = p
        fr = p.get("frame") or {}
        return {"paused": True, "frame": fr.get("actor"), "where": fr.get("where"),
                "why": p.get("why")}

    if action == "reload":
        return dbg.reload(kw["wait_page"])

    if action == "xhr_breakpoint":
        if kw["method"] in ("", "remove"):
            ok = dbg.remove_xhr_breakpoint(kw["path"], kw["method"])
            return {"status": "removed" if ok else "failed", "path": kw["path"]}
        ok = dbg.set_xhr_breakpoint(kw["path"], kw["method"])
        return {"status": "ok" if ok else "failed", "path": kw["path"],
                "method": kw["method"] or "ANY"}

    raise RdpError("unknown action %r; expected one of %s" % (action, ", ".join(ACTIONS)))


@mcp.tool()
async def rdp_debugger(
    action: str,
    port: int = 6123,
    url: str = "about:blank",
    executable: str | None = None,
    wait_page: float = 5.0,
    url_substr: str = "",
    target_type: str = "frame",
    line: int | None = None,
    column: int | None = None,
    condition: str | None = None,
    frame_actor: str | None = None,
    expression: str = "",
    direction: str = "over",
    timeout_ms: int = 30000,
    path: str = "",
    method: str = "ANY",
    on_next: bool = False,
    require_frame: bool = True,
    save_path: str = "",
    max_chars: int = 4_000_000,
    max_items: int = 200,
) -> dict:
    """Firefox 原生 RDP 调试器：断点、单步、调用栈、作用域、暂停中求值。

    camoufox 是 Firefox，Playwright 走 Juggler 没有 CDP；这个工具直接开 RDP
    （`-start-debugger-server`）把调试能力补齐。端口没有认证，只在 127.0.0.1 使用。

    action:
      launch          启动带调试端口的浏览器（独立 profile；端口被占用时报错，不会杀别人的进程）
      attach          连到已在运行的调试端口
      enable          listTabs → getTarget → threadActor，并 attach 线程
      sources         列出脚本（url_substr 过滤）
      source          取脚本全文（大脚本自动 longString 分片；save_path 可落盘）
      breakable_lines 某个脚本的可断行号
      set_breakpoint  下断点（需要 line；支持 column / condition）
      wait_paused     等下一次命中（返回 frame / where / why）
      frames          当前暂停的完整调用栈
      scopes          某帧的递归作用域链与 bindings（需要 frame_actor）
      evaluate        求值：给了 frame_actor 就在该暂停帧内求值（能读局部变量），否则页面全局求值
      step            单步 over / into / out
      resume          继续运行
      interrupt       不等断点直接暂停（单行 JSVMP 用）
      reload          刷新并重建会话 + 重下此前所有断点
      xhr_breakpoint  在请求发出前暂停（path / method；method="remove" 移除）
      status          会话状态
      close           结束会话并关闭本次启动的浏览器

    Args:
        action: 见上。
        port: RDP 端口，默认 6123。
        url: launch 时打开的页面。
        executable: 自定义 Firefox/camoufox 路径；默认自动探测。
        wait_page: launch/reload 后额外等待页面加载的秒数。
        url_substr: 选标签页 / 过滤脚本的 URL 子串（enable/sources/source/breakable_lines/set_breakpoint）。
        target_type: enable 的 watcher 兜底目标类型（frame/process/worker）。
        line: 断点行号（1-based）。
        column: 断点列号。
        condition: 断点条件表达式，如 `i === 3`。
        frame_actor: 栈帧 actor（scopes/evaluate/step 用）。
        expression: evaluate 的 JS 表达式。
        direction: step 方向 over / into / out。
        timeout_ms: wait_paused / interrupt / evaluate 的超时毫秒。
        path: xhr_breakpoint 的 URL 子串，空表示任意请求。
        method: xhr_breakpoint 的 HTTP 方法，"ANY" 表示全部；"remove" 表示移除断点。
        on_next: interrupt 是否暂停在下一个语句。
        require_frame: wait_paused 是否要求带 frame（interrupt 空闲暂停时 frame 可能为 null）。
        save_path: source 落盘路径。
        max_chars: source 最多取多少字符。
        max_items: sources / breakable_lines 返回条数上限。
    """
    from .debugger_rdp import ACTIONS as _A  # noqa: F401  (便于 introspection)

    act = (action or "").strip().lower()
    if act not in ACTIONS:
        raise RdpError("unknown action %r; expected one of %s" % (action, ", ".join(ACTIONS)))
    kw = dict(port=port, url=url, executable=executable, wait_page=wait_page,
              url_substr=url_substr, target_type=target_type, line=line, column=column,
              condition=condition, frame_actor=frame_actor, expression=expression,
              direction=direction, timeout_ms=timeout_ms, path=path, method=method,
              on_next=on_next, require_frame=require_frame, save_path=save_path,
              max_chars=max_chars, max_items=max_items)
    return await asyncio.to_thread(_sync_dispatch, act, kw)
