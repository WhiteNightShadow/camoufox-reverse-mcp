"""Firefox RDP 协议层：断点 / 单步 / 调用栈 / 作用域 / 暂停中求值。

MCP 工具入口在 `tools/debugger_rdp.py`（`rdp_debugger(action=...)`）；本模块只放协议，
可单独测试和复用，不依赖 MCP。

Firefox 152 实测协议（全部离线验证过）：
  root.listTabs                      → tabDescriptor
  tabDescriptor.getTarget()           → {frame:{actor, threadActor, consoleActor, ...}}
                                        threadActor 直接在 target form 里，不需要 watcher / attach 目标
  thread.attach {options:{}}          → {}            （必须带 options，否则不进入 RUNNING）
  thread.isAttached                   → {value:true}
  thread.sources                      → {sources:[{actor, url, sourceStartLine, ...}]}
  sourceActor.getBreakableLines       → {lines:[...]}
  thread.setBreakpoint {location:{sourceId|sourceUrl, line, column}, options:{...}}
  thread.resume {resumeLimit, frameActorID}
  thread.interrupt {when:{}}          → 触发 paused（线程空闲时 frame 为 null）
  thread.frames {start, count}        → {frames:[...]}
  frameActor.getEnvironment           → 递归 form，含 bindings（= 作用域变量）
  暂停中求值：consoleActor.evaluateJSAsync {text, frameActor}
              → {resultID} + `evaluationResult` 事件
  paused 事件：{type:"paused", frame:{actor, where:{...}}, why:{...}}

安全提示：RDP 端口没有任何认证。能连上 127.0.0.1:<port> 的本机进程都可以用浏览器
权限执行代码，所以本模块只在 127.0.0.1 上启动、端口由调用方指定，并且不会去杀不是
自己启动的进程。不要把该端口暴露到外网。

自测（不依赖 MCP、不访问外网）：python -m camoufox_reverse_mcp.debugger_rdp_tools --self-test
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

def _browser_binary_name() -> str:
    if os.name == "nt":
        return "camoufox.exe"
    if sys.platform == "darwin":
        return "camoufox"
    return "camoufox-bin"


def _glob_launchers(root: Path, name: str) -> list[Path]:
    """在 camoufox 缓存目录里找所有已安装的启动器（Cache/browsers/<repo>/<ver>/<name>）。"""
    out: list[Path] = []
    for pat in (f"browsers/*/*/{name}", f"*/*/{name}", name):
        try:
            out.extend(p for p in root.glob(pat) if p.is_file())
        except OSError:
            continue
    return out


def detect_browser_executable() -> str:
    """探测可用的 Firefox/camoufox 可执行文件。

    优先级：环境变量 CAMOUFOX_EXE > camoufox 缓存里最新安装的构建 >
    系统 Firefox。RDP (`-start-debugger-server`) 是 Firefox 原生功能，
    所以不光 camoufox，任何带该参数的 Firefox 都能用。
    """
    env = os.environ.get("CAMOUFOX_EXE")
    if env and Path(env).expanduser().is_file():
        return str(Path(env).expanduser())

    name = _browser_binary_name()
    roots: list[Path] = []
    try:  # 与 camoufox 包自己的缓存位置保持一致（含自定义 CAMOUFOX_INSTALL_DIR）
        from camoufox.pkgman import INSTALL_DIR  # type: ignore

        roots.append(Path(INSTALL_DIR))
    except Exception:
        pass
    for p in (
        os.environ.get("CAMOUFOX_INSTALL_DIR"),
        os.environ.get("LOCALAPPDATA") and Path(os.environ["LOCALAPPDATA"]) / "camoufox",
        Path.home() / ".cache" / "camoufox",
        Path.home() / "Library" / "Caches" / "camoufox",
    ):
        if p:
            roots.append(Path(p))

    found: list[Path] = []
    for root in roots:
        found.extend(_glob_launchers(root, name))
    if found:
        try:
            found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            pass
        return str(found[0])

    # 兜底：系统 Firefox（任意 Firefox 都支持 RDP 调试端口）
    for cand in (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Mozilla Firefox" / "firefox.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Mozilla Firefox" / "firefox.exe",
        Path("/Applications/Firefox.app/Contents/MacOS/firefox"),
        Path("/usr/lib/firefox/firefox"),
        Path("/usr/bin/firefox"),
    ):
        if cand.is_file():
            return str(cand)
    which = shutil.which("firefox")
    if which:
        return which
    raise RuntimeError(
        "找不到 Firefox/camoufox 可执行文件：请设置环境变量 CAMOUFOX_EXE 指向它。"
    )


CAMOUFOX_EXE = os.environ.get("CAMOUFOX_EXE") or ""


def _pids_matching(fragment: str) -> list[int]:
    """返回命令行里包含 fragment 的浏览器进程 PID（跨平台）。

    注意 POSIX 上的写法：`pgrep -f -start-debugger-server` 会被 pgrep 当成自己的
    选项（报 illegal option），必须以 `--` 结束选项解析，即 `pgrep -f -- <pattern>`。
    """
    if os.name == "nt":
        esc = fragment.replace("'", "''")
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='camoufox.exe' OR Name='firefox.exe'\" | "
            "Where-Object { $_.CommandLine -like '*" + esc + "*' } | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=30).stdout or ""
        except Exception:
            return []
        return [int(p) for p in out.split() if p.strip().isdigit()]
    try:
        out = subprocess.run(["pgrep", "-f", "--", fragment], capture_output=True,
                             text=True, timeout=30).stdout or ""
    except Exception:
        return []
    pids = [int(p) for p in out.split() if p.strip().isdigit()]
    # pgrep -f 可能匹配到执行本查询的 shell（命令行里同样含 fragment），排除自身链条
    me = {os.getpid(), os.getppid()}
    return [p for p in pids if p not in me]


def _kill_pids(pids: list[int]) -> int:
    n = 0
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, text=True, timeout=15)
            else:
                os.kill(pid, 9)
            n += 1
        except Exception:
            pass
    return n


def _kill_by_profile(profile: str | None) -> int:
    """只杀 **本会话自己启动的** 浏览器进程。

    terminate() 结束的只是启动器，Firefox 真进程会继续跑并占着调试端口。
    这里按本次随机生成的临时 profile 路径匹配命令行——别的 MCP 实例、用户自己开的
    Firefox、其他工具开的调试实例都不会被误杀（`-start-debugger-server` 太容易撞）。
    """
    if not profile:
        return 0
    return _kill_pids(_pids_matching(profile))


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


class RdpError(RuntimeError):
    pass


class RdpClient:
    """最小 RDP 客户端：长度前缀 JSON 帧 + prefix 适配 + 事件缓冲。

    构造时可以直接传入已连接的 socket（`sock=`），便于测试用 socketpair
    回放 RDP 帧，不需要真实浏览器或网络。
    """

    def __init__(self, port: int = 6123, timeout: float = 20.0, sock=None):
        self.sock = sock or socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        self.prefix: str | None = None
        self.events: list[dict] = []

    # ---------- 传输 ----------
    def _packet(self) -> dict:
        while True:
            i = self.buf.find(b":")
            if i > 0:
                try:
                    n = int(self.buf[:i])
                except ValueError:
                    i = -1
                else:
                    if len(self.buf) >= i + 1 + n:
                        payload = self.buf[i + 1: i + 1 + n]
                        self.buf = self.buf[i + 1 + n:]
                        return json.loads(payload.decode("utf-8", "replace"))
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise RdpError("rdp socket closed")
            self.buf += chunk

    def send(self, obj: dict) -> None:
        if self.prefix and "prefix" not in obj:
            obj = dict(obj, prefix=self.prefix)
        data = json.dumps(obj).encode("utf-8")
        self.sock.sendall(str(len(data)).encode("ascii") + b":" + data)

    def recv(self, timeout: float = 20.0) -> dict:
        self.sock.settimeout(timeout)
        return self._packet()

    def hello(self) -> dict:
        msg = self.recv(20)
        self.prefix = msg.get("testConnectionPrefix")
        return msg

    def drain(self, timeout: float = 0.12) -> int:
        """把当前已到达的所有消息收进 events（非阻塞），避免“上一条请求的延迟错误”错位。"""
        n = 0
        while True:
            try:
                msg = self.recv(timeout)
            except (TimeoutError, socket.timeout):
                break
            except OSError:
                break
            except RdpError:
                break
            self.events.append(msg)
            n += 1
        return n

    def call(self, obj: dict, timeout: float = 20.0):
        """发请求并返回属于它的回复。

        修复点：
          1) 发送前先 drain，清掉前序请求的延迟错误；
          2) 若收到 from==to 的 error，但错误消息里不含本次 packet type，
             说明是别的请求的延迟错误 -> 归入 events 继续等；
          3) 部分请求（如 dumpPools）回复是 list，不能假定 dict；
          4) 超时与连接中断统一转成 RdpError，调用方只需处理一种异常。
        """
        self.drain()
        self.send(obj)
        ptype = str(obj.get("type") or "")
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self.recv(max(1.0, end - time.time()))
            except (TimeoutError, socket.timeout):
                continue
            except OSError as exc:
                raise RdpError("rdp socket error while waiting for %r: %s" % (ptype, exc))
            if not isinstance(msg, dict):
                self.events.append(msg)
                continue
            if msg.get("from") != obj.get("to"):
                self.events.append(msg)
                continue
            if "error" in msg and ptype and ptype not in str(msg.get("message", "")):
                self.events.append(msg)
                continue
            return msg
        raise RdpError(f"timeout waiting reply for {obj.get('type')}")

    def wait_event(self, predicate, timeout: float = 20.0) -> dict | None:
        """等待满足谓词的事件；先扫缓冲。非 dict 包（如 list 回复）直接丢弃。"""
        for e in list(self.events):
            if isinstance(e, dict):
                try:
                    if predicate(e):
                        self.events.remove(e)
                        return e
                except Exception:
                    pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self.recv(max(1.0, end - time.time()))
            except (TimeoutError, socket.timeout):
                return None
            if isinstance(msg, dict):
                try:
                    if predicate(msg):
                        return msg
                except Exception:
                    pass
            self.events.append(msg)
        return None

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class FireFoxDebugger:
    """高层封装：launch → enable → breakpoint → paused → scopes。"""

    def __init__(self, port: int = 6123, exe: str | None = None):
        self.port = port
        self.exe = exe or CAMOUFOX_EXE  # 空则 launch 时自动探测
        self.proc: subprocess.Popen | None = None
        self.profile: str | None = None
        self.rdp: RdpClient | None = None
        self.thread: str | None = None
        self.watcher: str | None = None
        self.target: str | None = None
        self.tab: str | None = None
        self.console: str | None = None
        # 记录用过的断点，导航后 thread actor 会被重建，需要重新下发
        self.breakpoints: list[dict] = []

    # ---------- 生命周期 ----------
    def launch(self, url: str = "about:blank", wait_page: float = 6.0,
               launch_timeout: float = 45.0) -> None:
        """启动带调试端口的浏览器。

        只清理本会话之前启动的进程（按 profile 精确匹配），不碰别的实例；
        端口已被占用时直接报错，让调用方换端口（而不是去杀别人的浏览器）。
        """
        if _port_in_use(self.port):
            raise RdpError(
                "RDP port %d is already in use. Pick another port (port=...) — "
                "this session will not kill processes it did not start." % self.port)
        # 上一次 launch 残留（同一实例重复 launch 时）
        if self.profile:
            _kill_by_profile(self.profile)
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = None
        if not self.exe:
            self.exe = detect_browser_executable()
        self.profile = tempfile.mkdtemp(prefix="rdp_dbg_")
        cmd = [self.exe, "-no-remote", "-profile", self.profile,
               "-start-debugger-server", str(self.port), url]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + launch_timeout
        # camoufox.exe 是启动器：它把真正的浏览器进程拉起来后自己就 exit(0)，
        # 所以“启动器退出”不等于失败，判据只能是端口能否连上。
        # 但如果启动器退出一段时间后端口仍然没开（无浏览器/无显示/参数错误），
        # 就提前失败，不必干等到 launch_timeout。
        exited_at: float | None = None
        while time.time() < deadline:
            time.sleep(0.25)
            try:
                self.rdp = RdpClient(self.port)
                self.rdp.hello()
                break
            except OSError:
                code = self.proc.poll()
                if code is not None:
                    if exited_at is None:
                        exited_at = time.time()
                    elif time.time() - exited_at > 10.0:
                        raise RdpError(
                            "browser launcher exited (code %s) and RDP port %d never opened: %s"
                            % (code, self.port, self.exe))
                continue
        if not self.rdp:
            raise RdpError("cannot connect to RDP port %d within %.0fs"
                           % (self.port, launch_timeout))
        if wait_page:
            time.sleep(wait_page)

    def attach(self, port: int | None = None) -> None:
        self.rdp = RdpClient(port or self.port)
        self.rdp.hello()

    def close(self) -> None:
        if self.rdp:
            self.rdp.close()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        # terminate 只结束启动器；按 profile 把子进程一并清掉，避免端口被残留实例占用
        _kill_by_profile(self.profile)
        if self.profile:
            time.sleep(1.0)
            shutil.rmtree(self.profile, ignore_errors=True)

    # ---------- debugger ----------
    def enable(self, url_substr: str | None = None, target_type: str = "frame") -> dict:
        """tab → target form → threadActor → attach。

        Firefox 152 的正解：getTarget() 返回的 frame form 里直接带 threadActor
        （同一个 form 里还有 consoleActor，暂停中求值要用）。attach 必须带
        `options:{}`，否则 thread 不会从 DETACHED 进入 RUNNING，sources 恒为 0。
        watcher.watchTargets 只对“之后新建”的 frame 有用，仅作兜底。
        """
        tabs = self.rdp.call({"to": "root", "type": "listTabs"}, 25)
        tab_list = tabs.get("tabs") or []
        if not tab_list:
            raise RdpError("no tabs")
        tab = None
        for t in tab_list:
            if url_substr and url_substr in str(t.get("url", "")):
                tab = t
                break
        tab = tab or tab_list[0]
        self.tab = tab["actor"]

        tgt = self.rdp.call({"to": self.tab, "type": "getTarget"}, 25)
        frame = (tgt or {}).get("frame") or {}
        self.target = frame.get("actor")
        self.console = frame.get("consoleActor")
        info = {"tab": self.tab, "target": self.target, "url": tab.get("url")}

        th = frame.get("threadActor")
        if th:
            self.thread = th
            info.update({"thread": th, "via": "getTarget.frame.threadActor"})
        else:
            # 兜底：watcher 只推新建 frame
            watch = self.rdp.call({"to": self.tab, "type": "getWatcher"}, 25)
            self.watcher = (watch or {}).get("actor")
            if not self.watcher:
                raise RdpError("getWatcher failed: %s" % json.dumps(watch)[:200])
            self.rdp.send({"to": self.watcher, "type": "watchTargets",
                           "targetType": target_type})
            form = self.rdp.wait_event(
                lambda e: "target-available-form" in str(e.get("type", "")), timeout=20)
            th = self._find_thread(form) if form else None
            if not th:
                raise RdpError("no threadActor; info=%s" % json.dumps(info)[:300])
            self.thread = th
            info.update({"watcher": self.watcher, "via": "watcher", "thread": th})

        info.update(self.attach_thread())
        return info

    def attach_thread(self) -> dict:
        """thread.attach（必须带 options）→ isAttached 校验。"""
        if not self.thread:
            raise RdpError("enable() first")
        out = {}
        r = self.rdp.call({"to": self.thread, "type": "isAttached"}, 10)
        if not (r or {}).get("value"):
            out["attach"] = self.rdp.call(
                {"to": self.thread, "type": "attach", "options": {}}, 20).get("error", "ok")
        r = self.rdp.call({"to": self.thread, "type": "isAttached"}, 10)
        out["attached"] = bool((r or {}).get("value"))
        if not out["attached"]:
            raise RdpError("thread not attached: %s" % json.dumps(r)[:200])
        return out

    @staticmethod
    def _find_thread(obj) -> str | None:
        if isinstance(obj, dict):
            if isinstance(obj.get("threadActor"), str):
                return obj["threadActor"]
            for v in obj.values():
                r = FireFoxDebugger._find_thread(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = FireFoxDebugger._find_thread(v)
                if r:
                    return r
        return None

    def sources(self) -> list[dict]:
        r = self.rdp.call({"to": self.thread, "type": "sources"}, 40)
        return (r or {}).get("sources", [])

    def breakable_lines(self, source_actor: str) -> list[int]:
        r = self.rdp.call({"to": source_actor, "type": "getBreakableLines"}, 20)
        return list((r or {}).get("lines") or [])

    def source_text(self, source_actor: str, timeout: float = 120.0, max_chars: int = 4_000_000) -> str:
        """取脚本全文。

        大脚本的 `source` 回复是 longString（{type:"longString", actor, length}），
        必须再对 longString actor 发 `substring` 分片取回。
        """
        r = None
        for ptype in ("source", "getSourceText"):
            try:
                r = self.rdp.call({"to": source_actor, "type": ptype}, timeout)
            except RdpError:
                continue
            if isinstance(r, dict):
                break
        if not isinstance(r, dict):
            return ""
        v = r.get("source") or r.get("text")
        if isinstance(v, str):
            return v
        if not isinstance(v, dict):
            return ""
        if v.get("type") == "longString" and v.get("actor"):
            actor, length = v["actor"], int(v.get("length") or 0)
            out, pos, step = [], 0, 200_000
            while pos < min(length, max_chars):
                try:
                    part = self.rdp.call({"to": actor, "type": "substring",
                                          "start": pos, "end": min(pos + step, length)}, timeout)
                except RdpError:
                    break
                s = (part or {}).get("substring")
                if not s:
                    break
                out.append(s)
                pos += len(s)
            return "".join(out)
        for k in ("source", "text"):
            if isinstance(v.get(k), str):
                return v[k]
        return ""

    def set_breakpoint(self, url_substr: str, line: int, column: int | None = None,
                       condition: str | None = None) -> dict:
        src = next((s for s in self.sources() if url_substr in str(s.get("url", ""))), None)
        if not src:
            raise RdpError("source not found for %r" % url_substr)
        loc = {"sourceId": src.get("id") or src.get("actor"), "line": line}
        if column is not None:
            loc["column"] = column
        opts = {"condition": condition, "logValue": None, "showStacktrace": None}
        req = {"to": self.thread, "type": "setBreakpoint", "location": loc, "options": opts}
        r = self.rdp.call(req, 30)
        if "error" not in r:
            rec = {"url_substr": url_substr, "line": line, "column": column,
                   "condition": condition}
            if rec not in self.breakpoints:
                self.breakpoints.append(rec)
        return r

    def reload(self, wait: float = 6.0) -> dict:
        """刷新页面并重建调试会话。

        实测：reload 之后 target/thread actor 全部换新（child2 → child3），
        旧 thread 上的断点随之失效，必须重新 enable + 重新下发断点。
        这里自动完成，返回新的会话信息。
        """
        if not self.thread:
            raise RdpError("enable() first")
        try:
            self.rdp.call({"to": self.target, "type": "reload"}, 30)
        except RdpError:
            pass
        time.sleep(wait)
        info = self.enable()
        reapplied = []
        for bp in list(self.breakpoints):
            try:
                self.rdp.call({"to": self.thread, "type": "sources"}, 40)
                self.set_breakpoint(bp["url_substr"], bp["line"], bp["column"], bp["condition"])
                reapplied.append(bp)
            except RdpError as e:
                reapplied.append({"error": str(e), **bp})
        info["reapplied"] = reapplied
        return info

    def set_xhr_breakpoint(self, path: str = "", method: str = "ANY") -> bool:
        """在“发起网络请求”处暂停（比行断点更适合压缩/混淆代码）。

        method 必须传 `"ANY"` 或真实方法名（GET/POST）；传空串永不匹配
        （服务端判断是 `method !== "ANY" && method !== requestMethod`）。
        实测：fetch 与 XHR 都会命中，paused.why.type == "XHR"，
        此时整条调用栈可用 frames 逐层读作用域 —— 抓 `data` 这类入参正合适。
        """
        r = self.rdp.call({"to": self.thread, "type": "setXHRBreakpoint",
                           "path": path, "method": method or "ANY"}, 15)
        return bool((r or {}).get("value"))

    def remove_xhr_breakpoint(self, path: str = "", method: str = "ANY") -> bool:
        r = self.rdp.call({"to": self.thread, "type": "removeXHRBreakpoint",
                           "path": path, "method": method or "ANY"}, 15)
        return bool((r or {}).get("value"))

    def interrupt(self, on_next: bool = False, timeout: float = 15.0) -> dict | None:
        """不依赖行断点地暂停（JSVMP 只有一行、无从下断时用这个）。

        注意：在页面空闲/无 JS 帧时暂停，paused.frame 会是 null。
        """
        self.rdp.drain(0.1)
        self.rdp.send({"to": self.thread, "type": "interrupt",
                       "when": {"onNext": bool(on_next)}})
        return self.rdp.wait_event(lambda e: e.get("type") == "paused", timeout)

    def resume(self, frame_actor: str | None = None, resume_limit=None) -> dict:
        return self.rdp.call({"to": self.thread, "type": "resume",
                              "resumeLimit": resume_limit,
                              "frameActorID": frame_actor}, 20)

    def wait_paused(self, timeout: float = 30.0, require_frame: bool = True) -> dict | None:
        return self.rdp.wait_event(
            lambda e: e.get("type") == "paused"
            and (bool(e.get("frame")) or not require_frame), timeout)

    def frames(self) -> list[dict]:
        r = self.rdp.call({"to": self.thread, "type": "frames", "start": 0, "count": 100}, 20)
        return (r or {}).get("frames", [])

    def evaluate(self, expr: str, timeout: float = 25.0) -> dict:
        """全局求值（不暂停）：consoleActor.evaluateJSAsync，等价于在页面控制台执行。

        这是"纯协议驱动页面"的入口——不需要 Playwright 就能触发页面自己的
        init/click 逻辑，让后续脚本（如 dynamicJS）真正加载出来。
        """
        return self.frame_eval(None, expr, timeout)

    def frame_eval(self, frame_actor: str | None, expr: str, timeout: float = 25.0) -> dict:
        """暂停中求值：consoleActor.evaluateJSAsync + frameActor → evaluationResult 事件。"""
        ca = self.console
        if not ca:
            tg = self.rdp.call({"to": self.tab, "type": "getTarget"}, 20)
            ca = ((tg or {}).get("frame") or {}).get("consoleActor")
            self.console = ca
        if not ca:
            raise RdpError("no consoleActor")
        self.rdp.drain(0.1)
        r = self.rdp.call({"to": ca, "type": "evaluateJSAsync",
                           "text": expr, "frameActor": frame_actor}, timeout)
        rid = (r or {}).get("resultID")
        if not rid:
            return r
        ev = self.rdp.wait_event(
            lambda e: e.get("type") == "evaluationResult" and e.get("resultID") == rid, timeout)
        return ev or {"error": "no evaluationResult", "resultID": rid}

    def scopes(self, frame_actor: str) -> dict:
        """frame.getEnvironment → 递归 scope form（含 bindings）。"""
        return self.rdp.call({"to": frame_actor, "type": "getEnvironment"}, 25)

    def step(self, direction: str = "over", frame_actor: str | None = None) -> dict:
        limit = {"over": {"type": "next"},
                 "into": {"type": "step"},
                 "out": {"type": "finish"}}[direction]
        return self.resume(frame_actor, limit)


# ---------------------------------------------------------------- 自测
SELF_TEST_PROBE = (
    "var s=document.createElement('script');"
    "s.textContent='window.__dbg_probe=function(arg){var tag=\"rdp-ok\";debugger;return arg};';"
    "document.documentElement.appendChild(s);'injected'"
)


def _self_test() -> int:
    """离线自测：about:blank + 注入脚本，不访问任何外部站点。"""
    dbg = FireFoxDebugger(port=6130)
    try:
        dbg.launch("about:blank", wait_page=1.0)
        info = dbg.enable()
        print("[ok] enable:", {k: info.get(k) for k in ("target", "thread", "via", "attached")})
        print("[ok] evaluate:", dbg.evaluate(SELF_TEST_PROBE).get("result"))
        srcs = dbg.sources()
        print("[ok] sources:", len(srcs), [str(s.get("url"))[:40] for s in srcs[:3]])

        dbg.rdp.drain(0.1)
        dbg.rdp.send({"to": dbg.console, "type": "evaluateJSAsync",
                      "text": "window.__dbg_probe(7)", "frameActor": None})
        p = dbg.wait_paused(20)
        print("[ok] paused:", bool(p), (p or {}).get("why"))
        if not p:
            return 1
        fa = (p.get("frame") or {}).get("actor")
        print("[ok] frames:", [(f.get("displayName"), (f.get("where") or {}).get("line"))
                              for f in dbg.frames()])
        print("[ok] scopes:", json.dumps(dbg.scopes(fa))[:160])
        print("[ok] frame_eval:", dbg.frame_eval(fa, "arg + '|' + tag").get("result"))
        dbg.step("over", fa)
        dbg.wait_paused(10)
        print("[ok] resume:", dbg.resume())
        return 0
    finally:
        dbg.close()


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    print(__doc__)
