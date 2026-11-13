"""Firefox RDP 断点调试：`rdp_debugger(action=...)`。

Playwright 的 Firefox 走 Juggler，没有 CDP，所以 Playwright 侧拿不到「断点 / 单步 /
调用栈 / 作用域 / 暂停中求值」。这里直接走 Firefox 原生 RDP
（`-start-debugger-server`）补齐，全程不经过 Playwright，也不需要浏览器扩展。

因为每个 action 都可能要等几百毫秒到几十秒（启动浏览器、等断点命中、分片取源码），
所有阻塞动作都放进 `asyncio.to_thread`，不会卡住 MCP 事件循环或 Playwright 那边的
浏览器事件。同一会话的调用在协议层用一把锁串行化（并发调用不会串结果）；客户端
取消请求时会给工作线程发取消信号，读 socket 的循环随即退出。

启动的浏览器继承服务端配置（与 `launch_browser` 同一份 `BrowserManager.default_config`：
`CAMOU_CONFIG` 指纹、代理、`--headless`、`--os`、`--locale`），只额外加调试端口和
RDP 需要的 pref。

安全：RDP 端口没有认证。要开端口必须先打开 `devtools.chrome.enabled`，也就是
说 **连上端口 = 能用当前系统用户的身份在浏览器主进程里执行任意代码**；同一台机器上
其他用户也能连 127.0.0.1。默认端口是随机的空闲端口，只监听 loopback；
macOS/Linux 可以用 `unix_socket=true` 把调试口放进 0700 临时目录里的 Unix socket。
本工具只清理由自己启动的进程。
"""
from __future__ import annotations

import asyncio
import atexit
import os
import signal
import threading

from ..server import mcp
from ..debugger_rdp_tools import (
    FireFoxDebugger,
    RdpError,
    make_unix_socket_path,
)

#: 当前会话。`dbg` 只由本模块改，`last_paused` 记录最近一次暂停。
_STATE: dict = {"dbg": None, "last_paused": None}
_STATE_LOCK = threading.RLock()
_CLEANUP_INSTALLED = False
_PREVIOUS_SIGNAL_HANDLERS: dict = {}

ACTIONS = (
    "launch", "attach", "enable", "sources", "source", "breakable_lines",
    "breakable_positions", "set_breakpoint", "remove_breakpoint",
    "wait_paused", "frames", "scopes", "evaluate", "step",
    "resume", "interrupt", "reload", "xhr_breakpoint", "status", "close",
)


# ---------------------------------------------------------------- 会话与清理

def _dbg() -> FireFoxDebugger:
    dbg = _STATE.get("dbg")
    if dbg is None:
        raise RdpError("调试器未启动：先用 action=\"launch\" 或 action=\"attach\"")
    return dbg


def cleanup_session(reason: str = "") -> None:
    """关闭并丢掉当前会话（MCP 退出、收到信号、重复 launch 时都走这里）。"""
    with _STATE_LOCK:
        dbg = _STATE.get("dbg")
        _STATE["dbg"] = None
        _STATE["last_paused"] = None
    if dbg is not None:
        try:
            dbg.close()
        except Exception:
            pass


def _signal_cleanup(signum, frame):  # pragma: no cover - 进程级收尾
    cleanup_session("signal %s" % signum)
    previous = _PREVIOUS_SIGNAL_HANDLERS.get(signum)
    if callable(previous):
        return previous(signum, frame)
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except Exception:
        pass


def install_cleanup() -> None:
    """atexit + 信号处理：MCP 进程退出时把调试浏览器一起收掉（review #10）。"""
    global _CLEANUP_INSTALLED
    with _STATE_LOCK:
        if _CLEANUP_INSTALLED:
            return
        _CLEANUP_INSTALLED = True
    atexit.register(cleanup_session)
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            _PREVIOUS_SIGNAL_HANDLERS[sig] = signal.getsignal(sig)
            signal.signal(sig, _signal_cleanup)
        except (ValueError, OSError, RuntimeError):  # 非主线程 / Windows 上的 SIGHUP
            continue


def _reset_last_paused() -> None:
    with _STATE_LOCK:
        _STATE["last_paused"] = None


def _remember_paused(event: dict | None) -> None:
    if event:
        with _STATE_LOCK:
            _STATE["last_paused"] = event


# ---------------------------------------------------------------- 小工具

def _frames(fs: list[dict]) -> list[dict]:
    out = []
    for frame in fs:
        where = frame.get("where") if isinstance(frame.get("where"), dict) else {}
        out.append({"actor": frame.get("actor"), "displayName": frame.get("displayName"),
                    "where": frame.get("where"), "this": frame.get("this"),
                    "sourceUrl": where.get("sourceUrl")})
    return out


def _error_of(reply) -> dict | None:
    if not isinstance(reply, dict):
        return None
    code = reply.get("error", reply.get("errorCode"))
    if code is None:
        return None
    return {"error": str(code), "message": str(reply.get("message") or "")}


def _paused_payload(event: dict) -> dict:
    frame = event.get("frame") or {}
    return {"paused": True, "frame": frame.get("actor"), "where": frame.get("where"),
            "why": event.get("why"), "thread": event.get("from")}


# ---------------------------------------------------------------- 阻塞实现

def _sync_dispatch(action: str, kw: dict) -> dict:
    """阻塞实现（在 asyncio.to_thread 里跑）。"""
    dbg = _STATE.get("dbg")

    if action == "launch":
        install_cleanup()
        with _STATE_LOCK:
            previous = _STATE.get("dbg")
            _STATE["dbg"] = None
        if previous is not None:
            # 重复 launch 先收掉旧会话，否则旧浏览器没人关、端口一直被占（review #10）
            try:
                previous.close()
            except Exception:
                pass
        unix_path = None
        if kw["unix_socket"]:
            unix_path = make_unix_socket_path()
        d = FireFoxDebugger(port=kw["port"], exe=kw["executable"] or None,
                            unix_path=unix_path)
        try:
            info = d.launch(kw["url"], wait_page=kw["wait_page"])
        except Exception:
            # 启动到一半失败时也要把自己拉起的浏览器清掉，否则端口会被占住
            d.close()
            raise
        with _STATE_LOCK:
            _STATE["dbg"] = d
            _STATE["last_paused"] = None
        out = {"status": "launched"}
        out.update({k: v for k, v in info.items() if k != "warnings"})
        if info.get("warnings"):
            out["warnings"] = info["warnings"]
        return out

    if action == "attach":
        install_cleanup()
        with _STATE_LOCK:
            previous = _STATE.get("dbg")
            _STATE["dbg"] = None
        if previous is not None:
            try:
                previous.close()
            except Exception:
                pass
        unix_path = kw["socket_path"] or None
        if not unix_path and kw["unix_socket"]:
            raise RdpError(
                "action=\"attach\" + unix_socket 需要 socket_path：Unix socket 没有广播，"
                "要用 launch 返回的 unix_socket 路径。")
        d = FireFoxDebugger(port=kw["port"])
        try:
            info = d.attach(kw["port"] or None, unix_path=unix_path)
        except Exception:
            d.close()
            raise
        with _STATE_LOCK:
            _STATE["dbg"] = d
        return {"status": "connected", **info}

    if action == "status":
        if dbg is None:
            return {"running": False}
        out = dbg.status()
        out["last_paused"] = (_STATE.get("last_paused") or {}).get("why")
        return out

    if action == "close":
        if dbg is None:
            return {"status": "not_running"}
        info = dbg.close()
        with _STATE_LOCK:
            _STATE["dbg"] = None
            _STATE["last_paused"] = None
        return {"status": "closed", **info}

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
                "sourceStartLine": s.get("sourceStartLine"),
                "sourceStartColumn": s.get("sourceStartColumn"),
                "sourceLength": s.get("sourceLength")} for s in srcs[:kw["max_items"]]]
        return {"total": len(srcs), "sources": out}

    if action == "source":
        matches = dbg.resolve_sources(kw["source"] or None, kw["source_url"] or None,
                                      kw["url_substr"] or None)
        src = matches[0]
        got = dbg.source_text(src["actor"], max_chars=kw["max_chars"])
        text = got.get("text") or ""
        out = {"actor": src["actor"], "url": src.get("url"),
               "length": got.get("length", len(text)),
               "lines": text.count("\n") + 1,
               "truncated": bool(got.get("truncated")),
               "returned_chars": len(text),
               "source_start_line": src.get("sourceStartLine")}
        if kw["save_path"]:
            with open(kw["save_path"], "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            out["saved"] = kw["save_path"]
        else:
            out["head"] = text[:400]
        if got.get("truncated"):
            out["message"] = ("超过 max_chars=%d 已截断（Firefox 的 length/substring 按 "
                              "UTF-16 码元计）" % kw["max_chars"])
        return out

    if action == "breakable_lines":
        matches = dbg.resolve_sources(kw["source"] or None, kw["source_url"] or None,
                                      kw["url_substr"] or None)
        src = matches[0]
        return {"actor": src["actor"], "url": src.get("url"),
                "lines": dbg.breakable_lines(src["actor"])[:kw["max_items"]]}

    if action == "breakable_positions":
        matches = dbg.resolve_sources(kw["source"] or None, kw["source_url"] or None,
                                      kw["url_substr"] or None)
        src = matches[0]
        positions = dbg.breakable_positions(src["actor"], kw["line"])
        out_lines: dict = {}
        for line_no in sorted(positions):
            columns = positions[line_no]
            out_lines[str(line_no)] = {
                "columns_0based": columns,
                "columns_1based": [c + 1 for c in columns],
            }
        out = {"actor": src["actor"], "url": src.get("url"),
               "source_start_line": src.get("sourceStartLine"),
               "positions": out_lines,
               "note": "RDP 列号 0-based；Error.stack / DevTools 界面的列号是 1-based，"
                       "set_breakpoint 收 1-based 的 column 并自动吸附到最近可停列"}
        if kw["line"] and not out_lines:
            out["message"] = "第 %s 行没有可停位置（不是所有行/列都能下断点）" % kw["line"]
        return out

    if action == "set_breakpoint":
        if not kw["line"]:
            raise RdpError("action=\"set_breakpoint\" 需要 line（1-based）")
        return dbg.set_breakpoint(
            kw["line"], kw["column"], kw["condition"],
            source_actor=kw["source"] or None,
            source_url=kw["source_url"] or None,
            url_substr=kw["url_substr"] or None)

    if action == "remove_breakpoint":
        return dbg.remove_breakpoints(
            line=kw["line"], source_actor=kw["source"] or None,
            source_url=kw["source_url"] or None,
            url_substr=kw["url_substr"] or None,
            remove_all=kw["remove_all"])

    if action == "wait_paused":
        event = dbg.wait_paused(kw["timeout_ms"] / 1000.0, require_frame=kw["require_frame"])
        if not event:
            return {"paused": False}
        _remember_paused(event)
        return _paused_payload(event)

    if action == "frames":
        reply = dbg.frames()
        err = _error_of(reply)
        if err:
            # review #12：wrongState 不能变成 count: 0
            return err
        fs = reply.get("frames") or []
        return {"count": len(fs), "frames": _frames(fs)}

    if action == "scopes":
        if not kw["frame_actor"]:
            raise RdpError("action=\"scopes\" 需要 frame_actor（来自 wait_paused/frames）")
        reply = dbg.scopes(kw["frame_actor"])
        err = _error_of(reply)
        if err:
            return err
        return reply

    if action == "evaluate":
        if not kw["expression"]:
            raise RdpError("action=\"evaluate\" 需要 expression")
        if kw["frame_actor"]:
            reply = dbg.frame_eval(kw["frame_actor"], kw["expression"],
                                   kw["timeout_ms"] / 1000.0)
        else:
            reply = dbg.evaluate(kw["expression"], kw["timeout_ms"] / 1000.0)
        err = _error_of(reply)
        if err:
            code = err["error"]
            if code == "paused-while-evaluating":
                out = {"error": code, "message": err.get("message"),
                       "paused": reply.get("paused"),
                       "hint": "页面已停住：用 wait_paused / frames / scopes 看现场，"
                               "然后 resume 或 step"}
                _remember_paused(reply.get("paused"))
                return out
            return err
        result = reply.get("result")
        materialized, long_info = dbg.materialize(result, max_chars=kw["max_chars"])
        out = {"hasException": reply.get("hasException"),
               "result": result, "input": reply.get("input")}
        if kw["frame_actor"]:
            out["frame"] = kw["frame_actor"]
        if long_info is not None:
            # review #14：长字符串结果是 longString grip，这里顺带把全文取回来
            out["result_text"] = materialized
            out["longString"] = {k: long_info.get(k) for k in
                                 ("length", "truncated", "long_string_actor")}
        if reply.get("exception"):
            out["exception"] = reply.get("exception")
        if reply.get("exceptionMessage"):
            out["exceptionMessage"] = reply.get("exceptionMessage")
        return out

    if action == "step":
        result = dbg.step(kw["direction"], kw["frame_actor"] or None)
        if "error" in result and "status" not in result:
            return result  # review #12：wrongState 不能报 stepped
        return {"status": "stepped", "direction": kw["direction"],
                "reply": result.get("reply")}

    if action == "resume":
        result = dbg.resume(frame_actor=kw["frame_actor"] or None)
        if "error" in result:
            return result
        return {"status": "resumed", "reply": result.get("reply")}

    if action == "interrupt":
        event = dbg.interrupt(kw["on_next"], kw["timeout_ms"] / 1000.0)
        if not event:
            return {"paused": False}
        _remember_paused(event)
        out = _paused_payload(event)
        if (event.get("why") or {}).get("type") == "alreadyPaused":
            out["message"] = "线程本来就处于暂停状态（why=alreadyPaused）"
        return out

    if action == "reload":
        _reset_last_paused()
        return dbg.reload(kw["wait_page"])

    if action == "xhr_breakpoint":
        if kw["remove"] or kw["method"] in ("remove",):
            # review #6：用当初设置时的 path+method 精确删除，不能把 "remove" 当 method 发
            path_filter = None if kw["remove_all"] else kw["path"]
            method_filter = None if kw["method"] in ("", "remove") else kw["method"]
            return dbg.remove_xhr_breakpoints(path=path_filter, method=method_filter,
                                              remove_all=kw["remove_all"])
        return dbg.set_xhr_breakpoint(kw["path"], kw["method"])

    raise RdpError("unknown action %r; expected one of %s" % (action, ", ".join(ACTIONS)))


async def _run_blocking(fn, *args):
    """把阻塞调用放进线程；外层被取消时给工作线程发取消信号（review #3）。"""
    task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await task
    except asyncio.CancelledError:
        dbg = _STATE.get("dbg")
        if dbg is not None:
            try:
                dbg.request_cancel("client cancelled the request")
            except Exception:
                pass
        raise


@mcp.tool()
async def rdp_debugger(
    action: str,
    port: int = 0,
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
    source: str = "",
    source_url: str = "",
    remove_all: bool = False,
    remove: bool = False,
    unix_socket: bool = False,
    socket_path: str = "",
) -> dict:
    """Firefox 原生 RDP 调试器：断点、单步、调用栈、作用域、暂停中求值。

    camoufox 是 Firefox，Playwright 走 Juggler 没有 CDP；这个工具直接开 RDP
    （`-start-debugger-server`）把调试能力补齐。启动的浏览器继承服务端配置
    （指纹 / 代理 / headless / os / locale），只额外加调试端口与 RDP pref。

    安全：端口没有认证，开端口必须先打开 `devtools.chrome.enabled`，也就是连上端口
    就能用当前系统用户身份在浏览器主进程执行任意代码；同机其他用户也能连
    127.0.0.1。默认端口是随机的空闲端口，只监听 loopback。

    action:
      launch              启动带调试端口的浏览器（随机端口；已有会话会先关掉；继承服务端配置）
      attach              连到已在运行的调试端口（需要 port 或 unix_socket）
      enable              listTabs → getTarget → threadActor，attach 线程并 detach 旧 target
      sources             列出脚本（url_substr 过滤；含 sourceStartLine/sourceLength）
      source              取脚本全文（longString 分片按 UTF-16 码元推进；超长显式截断）
      breakable_lines     某个脚本的可断行号
      breakable_positions 某脚本/某一行的可停位置（columns_0based / columns_1based）
      set_breakpoint      下断点（source_url 走 sourceUrl，source 走 sourceId；列号 1-based 自动吸附）
      remove_breakpoint   删行断点（按当初下发的 location 精确删除；remove_all 清空）
      wait_paused         等下一次命中（只接受 resume/step/interrupt 之后新到的 paused）
      frames              当前暂停的完整调用栈（出错时返回 {"error": ...}）
      scopes              某帧的递归作用域链与 bindings（需要 frame_actor）
      evaluate            求值：给了 frame_actor 就在该暂停帧内求值（能读局部变量）
      step                单步 over / into / out
      resume              继续运行
      interrupt           不等断点直接暂停（on_next=true 暂停在下一个语句）
      reload              刷新并重建会话 + 重下此前全部断点（先 detach 旧 target）
      xhr_breakpoint      在请求发出前暂停（path / method；remove=true 移除）
      status              会话状态
      close               结束会话并关闭本次启动的浏览器

    Args:
        action: 见上。
        port: RDP 端口。0（默认）= launch 时随机选空闲端口；attach 必须给具体端口。
        url: launch 时打开的页面。
        executable: 自定义 Firefox/camoufox 路径；默认用 camoufox 激活版本（与 launch_browser 一致）。
        wait_page: launch 后等页面开始加载的秒数上限；reload 时是新会话的最长等待。
        url_substr: 选标签页 / 过滤脚本的 URL 子串。enable 时匹配不到会报错并列出可用标签页。
        target_type: enable 的 watcher 兜底目标类型（frame/process/worker）。
        line: 断点行号，1-based；坐标就是 breakable_lines/breakable_positions 报出来的那套。
        column: 断点列号，**1-based**（Error.stack / DevTools 界面习惯），会吸附到最近可停列。
        condition: 断点条件表达式，如 `i === 3`。
        frame_actor: 栈帧 actor（scopes/evaluate/step/resume 用）。
        expression: evaluate 的 JS 表达式。
        direction: step 方向 over / into / out（写错会报错，不会 KeyError）。
        timeout_ms: wait_paused / interrupt / evaluate 的超时毫秒。
        path: xhr_breakpoint 的 URL 子串，空表示任意请求。
        method: xhr_breakpoint 的 HTTP 方法，"ANY" 表示全部；"remove" 视为移除。
        on_next: interrupt 是否暂停在下一个语句（发送字符串 "onNext"）。
        require_frame: wait_paused 是否要求带 frame（interrupt 空闲暂停时 frame 可能为 null）。
        save_path: source 落盘路径。
        max_chars: source/evaluate 最多取多少字符（UTF-16 码元计，超过会显式提示）。
        max_items: sources / breakable_lines 返回条数上限。
        source: source actor（set_breakpoint 用它走 sourceId，可断 eval / new Function）。
        source_url: 精确 URL（set_breakpoint 用它走 sourceUrl，内联脚本/导航后都能挂上）。
        remove_all: remove_breakpoint / xhr_breakpoint 里清空所有已记录断点。
        remove: xhr_breakpoint 用它移除（比 method="remove" 更明确）。
        unix_socket: macOS/Linux 用 0700 临时目录里的 Unix socket 代替 TCP 端口。
        socket_path: attach 时连已有的 Unix socket 路径（取自 launch 返回的 unix_socket）。
    """
    act = (action or "").strip().lower()
    if act not in ACTIONS:
        raise RdpError("unknown action %r; expected one of %s" % (action, ", ".join(ACTIONS)))
    kw = dict(port=port, url=url, executable=executable, wait_page=wait_page,
              url_substr=url_substr, target_type=target_type, line=line, column=column,
              condition=condition, frame_actor=frame_actor, expression=expression,
              direction=direction, timeout_ms=timeout_ms, path=path, method=method,
              on_next=on_next, require_frame=require_frame, save_path=save_path,
              max_chars=max_chars, max_items=max_items, source=source,
              source_url=source_url, remove_all=remove_all, remove=remove,
              unix_socket=unix_socket, socket_path=socket_path)
    return await _run_blocking(_sync_dispatch, act, kw)
