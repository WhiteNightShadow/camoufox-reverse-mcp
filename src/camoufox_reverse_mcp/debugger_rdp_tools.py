"""Firefox RDP 协议层：断点 / 单步 / 调用栈 / 作用域 / 暂停中求值。

MCP 工具入口在 `tools/debugger_rdp.py`（`rdp_debugger(action=...)`）；本模块只放协议，
可单独测试和复用，不依赖 MCP。

Firefox 152 实测协议（camoufox 152 与官方 Firefox 用同一套 devtools 协议）：

  root.listTabs                      → {tabs:[tabDescriptor]}
  tabDescriptor.getTarget()          → {frame:{actor, threadActor, consoleActor, ...}}
                                       threadActor 直接在 target form 里，不需要 watcher
  thread.attach {options:{}}         → {}      （必须带 options，否则不进入 RUNNING）
  thread.isAttached                  → {value:true}
  thread.sources                     → {sources:[{actor, url, sourceStartLine, sourceStartColumn, ...}]}
  sourceActor.getBreakableLines      → {lines:[...]}
  sourceActor.getBreakpointPositionsCompressed {query:{start:{line,column},end:{...}}}
                                     → {positions:{line:[columns...]}}   ← 列号可停位置
  sourceActor.source                 → {source:<str 或 longString grip>, contentType}
  longStringActor.substring          → {substring}   （偏移按 UTF-16 码元）
  thread.setBreakpoint {location, options}       → {from}（无 actualLocation，装不上也不报错）
  thread.removeBreakpoint {location}             → {from}
  thread.setXHRBreakpoint / removeXHRBreakpoint {path, method} → {value:bool}
  thread.resume {resumeLimit, frameActorID}      → {from}（回复是空包）+ resumed 事件
  thread.interrupt {when:"onNext"|{}}            → {from}（when 非 onNext 时先回 interrupt 包再发 paused）
  thread.frames {start,count}        → {frames:[{actor, displayName, where:{...}}]}
  frameActor.getEnvironment          → 递归 scope form（bindings = 作用域变量）
  consoleActor.evaluateJSAsync {text, frameActor} → {resultID} + evaluationResult 事件
  paused 事件：{type:"paused", frame:{actor, where:{line, column}}, why:{...}}
  windowGlobalTarget.detach          → 销毁该 target 的调试会话（含它上面的断点）

坐标约定（来自 devtools/server/actors/source.js、utils/sources-manager.js）：
  * 行号 1-based；列号 0-based，而且必须正好落在 `getBreakpointPositionsCompressed`
    报出来的位置上，否则 setBreakpoint 什么都不装却照样回成功。
  * 内联脚本的行号是 **HTML 文档行号**（`sourceStartLine` 是 <script> 所在行，
    可断行从它的下一行开始）；`eval` / `new Function` 这类没有 URL 的 source
    行号从 1 起算自己的源码。`breakable_lines` / `breakable_positions` 报出来的
    就是这套坐标系，下断点用同一套。
  * JS 侧的 `Error.stack` / DevTools 界面列号是 1-based，用之前要减 1。

安全（review #11）：
  * RDP 端口能打开的前提是 `devtools.debugger.remote-enabled` **和**
    `devtools.chrome.enabled` 都为 true（`DevToolsStartup._isRemoteDebuggingEnabled`）。
    后者意味着连上端口的人可以调试浏览器主进程，也就是 **能以当前系统用户的身份
    执行任意代码**；端口没有认证，同一台机器上任何用户都能连 127.0.0.1。
  * 所以默认端口取随机的空闲端口；端口只监听 127.0.0.1；本模块只清理由自己启动的
    进程（按临时 profile 精确匹配），不会去杀别的浏览器。
  * macOS / Linux 上 `-start-debugger-server <path>` 也支持 Unix socket；把路径放进
    权限 0700 的临时目录，就只有当前用户能连（`unix_socket=True`）。

自测（不依赖 MCP、不访问外网）：python -m camoufox_reverse_mcp.debugger_rdp_tools --self-test
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------- 常量

#: 端口能开必须的两个 pref，加上免交互确认（review #1）。
RDP_PREFS = {
    "devtools.debugger.remote-enabled": True,
    "devtools.chrome.enabled": True,
    "devtools.debugger.prompt-connection": False,
}

#: 事件缓冲上限：`newSource` 洪水时不能无限增长（review #4）。
MAX_EVENTS = 512

#: 服务端主动推送的包类型。同一 actor 推来的事件即使 `from` 对得上也**不是**回复，
#: 必须按 type 分流（review #4）。`resumed` 虽然是 `thread.resume` 的产物，但它是事件：
#: `resume` 的回复是空包 `{from}`。
_EVENT_TYPES = frozenset({
    "paused", "resumed", "newSource", "newGlobal", "frameUpdate",
    "evaluationResult", "detached", "will-navigate", "tabNavigated",
    "target-available-form", "target-destroyed-form", "resource-available-form",
    "progress", "documentEvent", "inspectObject", "lastPrivateContextExited",
    "documentUnload", "breakpointConditionThrown", "switched-target",
})

_PROXY_RE = re.compile(r"^(?:(?P<scheme>[a-zA-Z0-9+.-]+)://)?(?P<host>[^:/?#]+)(?::(?P<port>\d+))?$")

_STEP_LIMITS = {"over": {"type": "next"}, "into": {"type": "step"}, "out": {"type": "finish"}}


class RdpError(RuntimeError):
    """协议层错误（启动失败、超时、服务端错误回复、非法参数）。"""


# ---------------------------------------------------------------- 小工具

def _expand(path: str | os.PathLike) -> Path:
    """支持 `~/...` 与 `$HOME/...`，review #14。"""
    return Path(os.path.expandvars(str(path))).expanduser()


def _error_of(reply) -> dict | None:
    """把服务端错误回复转成 `{"error": code, "message": text}`；不是错误返回 None。"""
    if not isinstance(reply, dict):
        return None
    code = reply.get("error", reply.get("errorCode"))
    if code is None and "message" not in reply:
        return None
    if code is None:
        return None
    return {"error": str(code), "message": str(reply.get("message") or "")}


def _is_high_surrogate(ch: str) -> bool:
    return len(ch) == 1 and 0xD800 <= ord(ch) <= 0xDBFF


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _wait_port_free(port: int, timeout: float = 5.0, host: str = "127.0.0.1") -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _port_in_use(port, host):
            return True
        time.sleep(0.1)
    return not _port_in_use(port, host)


# ---------------------------------------------------------------- 浏览器可执行文件

def _browser_binary_name() -> str:
    if os.name == "nt":
        return "camoufox.exe"
    if sys.platform == "darwin":
        return "camoufox"
    return "camoufox-bin"


def _glob_launchers(root: Path, name: str) -> list[Path]:
    """在 camoufox 缓存目录里找启动器。macOS 的可执行文件在
    `Camoufox.app/Contents/MacOS/camoufox`，要比 `<ver>/<name>` 深两层（review #1）。"""
    out: list[Path] = []
    patterns = (
        f"browsers/*/*/{name}",
        f"*/*/{name}",
        name,
        f"browsers/*/*/Camoufox.app/Contents/MacOS/{name}",
        f"*/*/Camoufox.app/Contents/MacOS/{name}",
        f"Camoufox.app/Contents/MacOS/{name}",
    )
    for pat in patterns:
        try:
            out.extend(p for p in root.glob(pat) if p.is_file())
        except OSError:
            continue
    return out


def _runtime_launch_paths(runtime: dict) -> list[str]:
    """`inspect_camoufox_runtime()` 里的安装目录，**当前激活的排第一**。

    review #1：装了多个版本时不能按 mtime 取最新，要取 `camoufox set` 选中的那个。
    """
    installed = [item for item in (runtime.get("installed") or []) if item.get("path")]
    installed.sort(key=lambda item: (not item.get("active"), str(item.get("path"))))
    return [str(item["path"]) for item in installed]


def _launch_path_for(root: str | Path) -> str | None:
    """用 camoufox 自己的 `launch_path()` 解析某个安装目录（含 macOS app 包布局）。"""
    try:
        from camoufox import pkgman

        path = pkgman.launch_path(Path(root))
    except Exception:
        return None
    return str(path) if path and Path(path).is_file() else None


def detect_browser_executable(explicit: str | None = None) -> str:
    """探测可用的 camoufox / Firefox 可执行文件。

    优先级（和 `launch_browser` 用同一个浏览器，review #1）：
      1. 显式传入的路径；
      2. `CAMOUFOX_EXE`（支持 `~/...` 展开，review #14）；
      3. `camoufox.pkgman.launch_path()` —— 当前**激活**版本，macOS 自动落到
         `Camoufox.app/Contents/MacOS/camoufox`；
      4. `inspect_camoufox_runtime()` 里其它已安装版本（激活的优先，不按 mtime）；
      5. 已知缓存目录里的启动器（兜底 0.4 扁平布局 / 非标准安装）；
      6. 系统 Firefox（RDP 是 Firefox 原生功能，任何带该参数的 Firefox 都能用）。
    """
    for cand in (explicit, os.environ.get("CAMOUFOX_EXE")):
        if cand:
            path = _expand(cand)
            if path.is_file():
                return str(path)

    # 3. 激活版本（pkgman 自己处理 macOS 的 app 包路径）
    try:
        from camoufox import pkgman

        path = pkgman.launch_path()
        if path and Path(path).is_file():
            return str(path)
    except Exception:
        pass

    # 4. 其它已安装版本，激活的排第一
    runtime: dict = {}
    try:
        from .camoufox_runtime import inspect_camoufox_runtime

        runtime = inspect_camoufox_runtime()
    except Exception:
        runtime = {}
    for root in _runtime_launch_paths(runtime):
        path = _launch_path_for(root)
        if path:
            return path

    # 5. 缓存目录兜底
    name = _browser_binary_name()
    roots: list[Path] = []
    try:
        from camoufox.pkgman import INSTALL_DIR  # type: ignore

        roots.append(Path(INSTALL_DIR))
    except Exception:
        pass
    for cand in (
        os.environ.get("CAMOUFOX_INSTALL_DIR"),
        os.environ.get("LOCALAPPDATA") and Path(os.environ["LOCALAPPDATA"]) / "camoufox",
        Path.home() / ".cache" / "camoufox",
        Path.home() / "Library" / "Caches" / "camoufox",
    ):
        if cand:
            roots.append(Path(cand))
    found: list[Path] = []
    for root in roots:
        found.extend(_glob_launchers(root, name))
    if found:
        # 仍然激活优先（browsers/<repo>/<ver> 里名字与激活 selector 一致的那个排前面），
        # 但没有可靠元信息时只按路径稳定排序，绝不"按 mtime 取最新"。
        active = str((runtime.get("active") or {}).get("folder") or "")
        found.sort(key=lambda p: (active not in str(p), str(p)))
        return str(found[0])

    # 6. 系统 Firefox
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
    raise RdpError(
        "找不到 Camoufox/Firefox 可执行文件：请设置环境变量 CAMOUFOX_EXE 指向它，"
        "或先执行 `camoufox fetch` 安装浏览器。"
    )


# ---------------------------------------------------------------- 进程清理

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


# ---------------------------------------------------------------- 启动配置（review #1 #2）

def resolve_launch_config(config: dict | None = None) -> dict:
    """服务端配置 + 调用方覆盖。

    与 `launch_browser` 读的是同一份 `BrowserManager.default_config`
    （`__main__.py` 从 `--proxy/--headless/--os/--locale/--geoip/--humanize/
    --block-images/--block-webrtc` 填进去），这样调试浏览器和 Playwright 那边的
    浏览器指纹、代理、headless 完全一致。
    """
    base: dict = {}
    try:
        from .browser import BrowserManager

        base = dict(getattr(BrowserManager, "default_config", None) or {})
    except Exception:
        base = {}
    return {**base, **(config or {})}


def _proxy_prefs(proxy) -> tuple[dict, list[str]]:
    """把 Playwright 格式的代理转成 Firefox pref。

    直接启动（不走 Playwright）时没有 `Browser.setBrowserProxy` 可用，只能写 pref。
    代理认证需要交互弹窗，pref 做不到 —— 这种情况显式告警，不让它静默直连。
    """
    prefs: dict = {}
    warnings: list[str] = []
    if not proxy:
        return prefs, warnings
    server = str(proxy.get("server") or "").strip()
    if not server:
        return prefs, warnings
    credentials = False
    match_creds = re.match(r"^(?P<scheme>[a-zA-Z0-9+.-]+://)?(?P<creds>[^/@]*@)?(?P<rest>.*)$",
                            server)
    if match_creds and match_creds.group("creds"):
        credentials = True
        scheme_part = match_creds.group("scheme") or ""
        server_wo_creds = scheme_part + match_creds.group("rest")
    else:
        server_wo_creds = server
    match = _PROXY_RE.match(server_wo_creds)
    if not match:
        warnings.append(
            "proxy server %r 解析不了，调试浏览器这次按直连启动（review #2）" % server)
        return prefs, warnings
    scheme = (match.group("scheme") or "http").lower()
    host = match.group("host")
    default_port = 1080 if scheme.startswith("socks") else 8080
    port = int(match.group("port") or default_port)
    prefs["network.proxy.type"] = 1
    if scheme.startswith("socks"):
        prefs["network.proxy.socks"] = host
        prefs["network.proxy.socks_port"] = port
        prefs["network.proxy.socks_version"] = 4 if scheme == "socks4" else 5
        prefs["network.proxy.socks_remote_dns"] = True
        # 让 http/https 也走同一个 socks
        prefs["network.proxy.http"] = host
        prefs["network.proxy.http_port"] = port
        prefs["network.proxy.ssl"] = host
        prefs["network.proxy.ssl_port"] = port
    else:
        prefs["network.proxy.http"] = host
        prefs["network.proxy.http_port"] = port
        prefs["network.proxy.ssl"] = host
        prefs["network.proxy.ssl_port"] = port
        prefs["network.proxy.share_proxy_settings"] = True
    bypass = str(proxy.get("bypass") or "").strip()
    if bypass:
        prefs["network.proxy.no_proxies_on"] = bypass
    if proxy.get("username") or proxy.get("password") or credentials:
        warnings.append(
            "代理认证（username/password）不会继承：直连启动没有 Playwright 的 "
            "Browser.setBrowserProxy，Firefox 的代理认证需要交互弹窗。带认证的代理上"
            "请求会失败而不是泄漏真实 IP。")
    return prefs, warnings


def _launch_option_kwargs(cfg: dict) -> dict:
    """按 `BrowserManager.launch()` 的映射生成 `camoufox.utils.launch_options` 参数。"""
    kwargs: dict = {}
    if cfg.get("proxy"):
        kwargs["proxy"] = cfg["proxy"]
    os_type = cfg.get("os") or "auto"
    if os_type == "auto":
        try:
            from .browser import detect_host_os

            os_type = detect_host_os()
        except Exception:
            os_type = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
    kwargs["os"] = os_type
    locale = cfg.get("locale") or "auto"
    if locale == "auto":
        try:
            from .browser import detect_system_locale

            locale = detect_system_locale()
        except Exception:
            locale = "en-US"
    kwargs["locale"] = locale
    for flag in ("humanize", "geoip", "block_images", "block_webrtc"):
        if cfg.get(flag):
            kwargs[flag] = True
    return kwargs


def write_profile_prefs(profile: str | Path, prefs: dict) -> Path:
    """把 pref 写进 profile 的 `user.js`。

    review #1：`-start-debugger-server` 只有 `devtools.debugger.remote-enabled` 和
    `devtools.chrome.enabled` 都为 true 时才开端口，官方构建里后者默认 false；
    空 profile 下 `MOZ_HEADLESS=1` / `-headless` 都开不了端口，写了 user.js 才开。
    """
    lines: list[str] = []
    for key in sorted(str(k) for k in prefs):
        value = prefs[key]
        if value is None:
            continue
        if isinstance(value, bool):
            literal = "true" if value else "false"
        elif isinstance(value, int):
            literal = str(value)
        elif isinstance(value, float):
            literal = repr(value)
        else:
            literal = json.dumps(str(value))
        lines.append("user_pref(%s, %s);" % (json.dumps(key), literal))
    path = Path(profile) / "user.js"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_launch_plan(
    *,
    port: int | None = 0,
    unix_path: str | None = None,
    profile: str | Path | None = None,
    url: str = "about:blank",
    exe: str | None = None,
    config: dict | None = None,
) -> dict:
    """生成直连启动所需的全部东西：可执行文件、argv、env、user.js pref。

    和 `launch_browser` 共享配置（`CAMOU_CONFIG_*` 指纹、代理、headless、os、locale），
    只额外加调试端口和 RDP pref（review #2）。返回的结构里带 `warnings`，
    写明哪些配置继承不了（不静默降级）。
    """
    cfg = resolve_launch_config(config)
    warnings: list[str] = []
    prefs: dict = {}
    env: dict = {k: str(v) for k, v in os.environ.items()}
    extra_args: list[str] = []
    headless_requested = cfg.get("headless", False)
    headless = bool(headless_requested)
    executable = ""

    kwargs = _launch_option_kwargs(cfg)
    options: dict = {}
    try:
        from camoufox.utils import launch_options

        options = launch_options(headless=headless, **kwargs) or {}
    except Exception as exc:  # 官方 Firefox 或 camoufox 未安装时退化
        warnings.append(
            "camoufox launch_options 失败（%s），这次用裸 profile 启动：指纹/代理/语言"
            "配置没有继承（review #2）。" % exc)
        options = {}

    if options:
        prefs.update(options.get("firefox_user_prefs") or {})
        env.update({str(k): str(v) for k, v in (options.get("env") or {}).items()})
        extra_args.extend(str(a) for a in (options.get("args") or []))
        if not headless and options.get("headless"):
            headless = True
        if isinstance(headless_requested, str) and headless_requested.startswith("virtual"):
            warnings.append(
                "headless=%r（虚拟显示）不会继承：本工具直接启动浏览器，没有 Xvfb "
                "包装，按普通 headless 处理。" % headless_requested)

    proxy_prefs, proxy_warnings = _proxy_prefs(cfg.get("proxy"))
    prefs.update(proxy_prefs)
    warnings.extend(proxy_warnings)

    # RDP 的三个 pref 永远写进去（review #1）
    prefs.update(RDP_PREFS)

    if exe:
        executable = str(_expand(exe))
        if not Path(executable).is_file():
            raise RdpError("指定的可执行文件不存在: %s" % executable)
    else:
        env_exe = os.environ.get("CAMOUFOX_EXE")
        if env_exe and _expand(env_exe).is_file():
            executable = str(_expand(env_exe))
        elif options.get("executable_path") and Path(str(options["executable_path"])).is_file():
            executable = str(options["executable_path"])
        else:
            executable = detect_browser_executable()

    cmd: list[str] = [executable, "-no-remote"]
    if profile:
        cmd += ["-profile", str(profile)]
    if port:
        cmd += ["-start-debugger-server", str(port)]
    elif unix_path:
        cmd += ["-start-debugger-server", str(unix_path)]
    else:
        raise RdpError("build_launch_plan 需要 port 或 unix_path（否则调试端口不会打开）")
    if headless:
        cmd.append("-headless")
    cmd += extra_args
    cmd.append(url or "about:blank")

    return {
        "executable": executable,
        "cmd": cmd,
        "env": env,
        "prefs": prefs,
        "warnings": warnings,
        "headless": headless,
        "config": cfg,
        "os": kwargs.get("os"),
        "locale": kwargs.get("locale"),
        "proxy": bool(proxy_prefs),
        "fingerprint_config": any(k.startswith("CAMOU_CONFIG_") for k in env),
    }


# ---------------------------------------------------------------- RDP 客户端

class RdpClient:
    """最小 RDP 客户端：长度前缀 JSON 帧 + 按 actor 排队的回复匹配 + 事件缓冲。

    review #3（并发）：同一会话的所有收发都在 `self._lock` 里串行化；`cancel()`
    可以被另一个线程调用，正在读 socket 的循环会立刻抛 `RdpError` 退出。
    review #4（消息分发）：
      * 事件类型（`_EVENT_TYPES`）优先 —— 同一 actor 推来的 `paused` / `resumed`
        即使 `from` 对得上也不会被当成回复；
      * 错误回复按 actor 的请求队列匹配：`message` 里点名了*别的*请求类型就是
        迟到的错误（留在缓冲里），点名了本次类型或者没点名就归本次请求；
      * 事件缓冲有上限（`MAX_EVENTS`），`drain()` 有总预算，`newSource` 洪水既不会
        无限增长也不会挡住后续请求。
    """

    def __init__(self, port: int = 0, timeout: float = 20.0, sock=None,
                 unix_path: str | None = None):
        if sock is not None:
            self.sock = sock
        elif unix_path:
            self.sock = _connect_unix(unix_path, timeout)
        else:
            if not port:
                raise RdpError("RdpClient 需要 port 或 unix_path")
            self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        try:
            self.sock.settimeout(timeout)
        except OSError:
            pass
        self.buf = b""
        self.prefix: str | None = None
        self.events: deque = deque(maxlen=MAX_EVENTS)
        self._lock = threading.RLock()
        self._pending: dict[str, list[dict]] = {}
        self._seq = 0
        self._cancel = threading.Event()
        self._closed = False

    # ---------- 传输 ----------
    def _read_packet(self, timeout: float) -> dict:
        """读一个完整包；socket 超时抛 TimeoutError，连接断开/cancel 抛 RdpError。"""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self._cancel.is_set():
                raise RdpError("rdp operation cancelled")
            i = self.buf.find(b":")
            if i > 0:
                try:
                    n = int(self.buf[:i])
                except ValueError:
                    raise RdpError("malformed rdp packet length: %r" % self.buf[:i][:32])
                if len(self.buf) >= i + 1 + n:
                    payload = self.buf[i + 1: i + 1 + n]
                    self.buf = self.buf[i + 1 + n:]
                    try:
                        return json.loads(payload.decode("utf-8", "replace"))
                    except ValueError:
                        raise RdpError("malformed rdp json packet")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("rdp read timeout")
            try:
                self.sock.settimeout(min(0.25, remaining))
            except OSError:
                pass
            try:
                chunk = self.sock.recv(1 << 20)
            except (TimeoutError, socket.timeout):
                if time.monotonic() >= deadline:
                    raise TimeoutError("rdp read timeout")
                continue
            except OSError as exc:
                raise RdpError("rdp socket error: %s" % exc)
            if not chunk:
                raise RdpError("rdp socket closed")
            self.buf += chunk

    def send(self, obj: dict) -> None:
        with self._lock:
            if self._closed:
                raise RdpError("rdp connection is closed")
            packet = dict(obj)
            if self.prefix and "prefix" not in packet:
                packet = dict(packet, prefix=self.prefix)
            data = json.dumps(packet).encode("utf-8")
            try:
                self.sock.sendall(str(len(data)).encode("ascii") + b":" + data)
            except OSError as exc:
                raise RdpError("rdp send failed: %s" % exc)

    def recv(self, timeout: float = 20.0) -> dict:
        """读一个包（不参与回复匹配，调用方自己处理）。"""
        with self._lock:
            return self._read_packet(timeout)

    def hello(self) -> dict:
        msg = self.recv(20)
        if isinstance(msg, dict):
            self.prefix = msg.get("testConnectionPrefix")
        return msg if isinstance(msg, dict) else {}

    # ---------- 事件与回复 ----------
    def mark(self) -> int:
        """当前事件序号；`wait_event(..., since=mark)` 只接受之后到的包。"""
        with self._lock:
            return self._seq

    def cancel(self, reason: str = "cancelled") -> None:
        """从别的线程打断正在读 socket 的调用（review #3：取消后工作线程要退出）。"""
        self._cancel.set()

    def resume_after_cancel(self) -> None:
        """清掉取消标记并丢弃可能迟到的回复包（下次操作用）。"""
        with self._lock:
            self._cancel.clear()
            self._resync()

    def _resync(self) -> None:
        """取消/超时之后把连接拉回干净状态：清 pending，丢掉回复形状的陈旧包。"""
        self._pending.clear()
        if self._closed:
            return
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            try:
                msg = self._read_packet(0.1)
            except TimeoutError:
                break
            except (RdpError, OSError):
                break
            self._seq += 1
            if isinstance(msg, dict) and msg.get("type") in _EVENT_TYPES:
                self._remember(dict(msg, __seq=self._seq))

    def _remember(self, msg) -> None:
        self.events.append(msg)

    def _dispatch(self, msg) -> None:
        """分流一个到达的包：回复进 pending，事件进缓冲。"""
        self._seq += 1
        if not isinstance(msg, dict):
            self._remember({"__raw": msg, "__seq": self._seq})
            return
        msg = dict(msg, __seq=self._seq)
        src = msg.get("from")
        ptype = str(msg.get("type") or "")
        queue = self._pending.get(src)
        if queue:
            if "error" in msg or "errorCode" in msg:
                target = self._match_error(queue, msg)
                if target is not None:
                    target["reply"] = msg
                    return
                self._remember(msg)
                return
            # 事件优先：同一 actor 推来的事件不是回复
            if ptype in _EVENT_TYPES:
                self._remember(msg)
                return
            for entry in queue:  # 老 request 先匹配
                if ptype and (ptype == entry["type"] or ptype in entry.get("aliases", ())):
                    entry["reply"] = msg
                    return
            # 空 type（Firefox 有些回复就是 `{from}`）或未列出的类型 -> 归最早的那条请求
            queue[0]["reply"] = msg
            return
        self._remember(msg)

    @staticmethod
    def _match_error(queue: list[dict], msg: dict) -> dict | None:
        text = str(msg.get("message") or "")
        quoted = re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", text)
        for entry in queue:
            if entry["type"] and entry["type"] in text:
                return entry
        if quoted:
            known = {entry["type"] for entry in queue} | {
                alias for entry in queue for alias in entry.get("aliases", ())}
            if not any(name in known for name in quoted):
                # 点名了别的请求类型：上一条请求迟到的错误，别当成本次失败
                return None
        # 没点名（例如 noSuchActor）：按 actor 队列归给最早的那条请求
        return queue[0] if queue else None

    def drain(self, idle: float = 0.05, budget: float = 0.4) -> int:
        """把已到达的包收进缓冲/回复；有总预算，缓冲区满就停（review #4）。"""
        with self._lock:
            if self._closed:
                return 0
            end = time.monotonic() + budget
            count = 0
            while time.monotonic() < end:
                if len(self.events) >= (self.events.maxlen or MAX_EVENTS):
                    break
                try:
                    msg = self._read_packet(min(idle, max(0.0, end - time.monotonic())))
                except TimeoutError:
                    break
                except (RdpError, OSError):
                    break
                self._dispatch(msg)
                count += 1
            return count

    def call(self, obj: dict, timeout: float = 20.0, aliases=()):
        """发请求并返回属于它的回复。

        回复匹配（review #4）：按 `to` actor 排队、按事件 type 分流、错误回复按
        message 里的请求类型分辨是不是迟到的。超时/断连统一抛 `RdpError`。
        """
        with self._lock:
            if self._closed:
                raise RdpError("rdp connection is closed")
            if self._cancel.is_set():
                self.resume_after_cancel()
            self.drain()
            entry = {"type": str(obj.get("type") or ""),
                     "aliases": tuple(aliases), "reply": None}
            actor = obj.get("to")
            queue = self._pending.setdefault(actor, [])
            queue.append(entry)
            self.send(obj)
            deadline = time.monotonic() + timeout
            try:
                while entry["reply"] is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RdpError("timeout waiting reply for %r" % obj.get("type"))
                    try:
                        msg = self._read_packet(min(1.0, remaining))
                    except TimeoutError:
                        continue
                    self._dispatch(msg)
                return entry["reply"]
            finally:
                if entry in queue:
                    queue.remove(entry)
                if not queue:
                    self._pending.pop(actor, None)

    def wait_event(self, predicate, timeout: float = 20.0, since: int | None = None):
        """等一个满足谓词的事件；只接受 `__seq > since` 的新包（review #4）。

        命中的事件**不从缓冲里删**：`paused` 是状态而不是可消费的消息，重复调用
        由 `since` 推进来保证拿到的是"新的那一次"。
        """
        with self._lock:
            found = self._scan(predicate, since)
            if found is not None:
                return found
            end = time.monotonic() + timeout
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    msg = self._read_packet(min(0.5, remaining))
                except TimeoutError:
                    continue
                except (RdpError, OSError):
                    return None
                self._dispatch(msg)
                found = self._scan(predicate, since)
                if found is not None:
                    return found

    def _scan(self, predicate, since: int | None):
        floor = 0 if since is None else int(since)
        for event in list(self.events):
            if not isinstance(event, dict):
                continue
            if int(event.get("__seq") or 0) <= floor:
                continue
            try:
                if predicate(event):
                    return event
            except Exception:
                continue
        return None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self.sock.close()
            except Exception:
                pass


def _connect_unix(path: str, timeout: float):
    if not hasattr(socket, "AF_UNIX"):
        raise RdpError("这个平台不支持 AF_UNIX，只能用 TCP 端口调试")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
    except OSError as exc:
        sock.close()
        raise RdpError("连接 Unix socket %s 失败: %s" % (path, exc))
    return sock


# ---------------------------------------------------------------- 高层封装

class FireFoxDebugger:
    """高层封装：launch → enable → breakpoint → paused → scopes。

    所有公开方法都在 `self._lock` 里执行，多个线程（`asyncio.to_thread` 的并发调用）
    不会同时读写同一个 socket（review #3）。
    """

    def __init__(self, port: int = 0, exe: str | None = None,
                 unix_path: str | None = None, config: dict | None = None):
        self.port = int(port or 0)
        self.unix_path = unix_path
        self.exe = exe or None
        self.config = dict(config or {})
        self.proc: subprocess.Popen | None = None
        self.profile: str | None = None
        self.rdp: RdpClient | None = None
        self.thread: str | None = None
        self.watcher: str | None = None
        self.target: str | None = None
        self.tab: str | None = None
        self.console: str | None = None
        self.tab_url: str | None = None
        #: 下过的行断点：`location` 原样保存，删除时必须一字不差地重发（服务端按
        #: sourceUrl:sourceId:line:column 做 key，review #6）。
        self.breakpoints: list[dict] = []
        #: 下过的 XHR 断点：保存设置时的 path+method 组合，移除时照发（review #6）。
        self.xhr_breakpoints: list[dict] = []
        self.last_pause: dict | None = None
        self.launch_info: dict | None = None
        self._lock = threading.RLock()
        self._fresh_floor = 0
        self._detached_targets: set[str] = set()
        self._cancel = threading.Event()

    # ---------- 生命周期 ----------
    def request_cancel(self, reason: str = "cancelled") -> None:
        """从别的线程打断当前操作（客户端取消请求时用，review #3）。"""
        self._cancel.set()
        if self.rdp is not None:
            self.rdp.cancel(reason)

    def _release_cancel(self) -> None:
        if self._cancel.is_set():
            self._cancel.clear()
            if self.rdp is not None and self.rdp._cancel.is_set():
                self.rdp.resume_after_cancel()

    def launch(self, url: str = "about:blank", wait_page: float = 6.0,
               launch_timeout: float = 45.0, config: dict | None = None,
               exe: str | None = None) -> dict:
        """启动带调试端口的浏览器。

        和 `launch_browser` 共享启动配置（指纹 / 代理 / headless / os / locale），
        只额外加调试端口与 RDP pref。已有会话时先 close，不覆盖孤儿进程
        （review #1 #2 #10）。
        """
        with self._lock:
            had_session = bool(self.rdp or self.profile or
                               (self.proc is not None and self.proc.poll() is None))
            if had_session:
                self.close()
            if self.unix_path is None and self.port <= 0:
                self.port = _free_port()
            if self.unix_path is None and not _wait_port_free(self.port, 5.0):
                raise RdpError(
                    "RDP 端口 %d 还被占用%s。传 port=0 让工具自己选空闲端口，"
                    "或者用 action=\"attach\" 连到正在监听它的那个浏览器。"
                    % (self.port,
                       "（刚关掉的本会话浏览器可能还在退出）" if had_session else
                       "（多半是别的进程/别的 MCP 实例）"))

            profile = Path(tempfile.mkdtemp(prefix="rdp_dbg_"))
            self.profile = str(profile)
            plan = build_launch_plan(
                port=self.port if self.unix_path is None else None,
                unix_path=self.unix_path,
                profile=profile,
                url=url,
                exe=exe or self.exe,
                config={**self.config, **(config or {})},
            )
            self.exe = plan["executable"]
            prefs_path = write_profile_prefs(profile, plan["prefs"])
            if self.unix_path:
                _prepare_unix_dir(self.unix_path)
            try:
                self.proc = subprocess.Popen(
                    plan["cmd"], env=plan["env"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                self.close()
                raise RdpError("启动浏览器失败（%s）: %s" % (plan["executable"], exc))

            deadline = time.monotonic() + launch_timeout
            exited_at: float | None = None
            last_error = ""
            connected = False
            # camoufox.exe 在某些平台上只是启动器：它拉起浏览器进程后自己 exit(0)，
            # 所以"启动器退出"不等于失败，判据只能是端口能不能连上。
            while time.monotonic() < deadline:
                try:
                    self.rdp = RdpClient(
                        port=self.port,
                        unix_path=self.unix_path)
                    self.rdp.hello()
                    connected = True
                    break
                except (OSError, RdpError) as exc:
                    last_error = str(exc)
                    self.rdp = None
                code = self.proc.poll()
                if code is not None:
                    if exited_at is None:
                        exited_at = time.monotonic()
                    elif time.monotonic() - exited_at > 10.0:
                        break
                time.sleep(0.2)
            if not connected:
                info = {
                    "executable": plan["executable"],
                    "headless": plan["headless"],
                    "launcher_exit": self.proc.poll() if self.proc else None,
                    "last_error": last_error,
                }
                self.close()
                raise RdpError(
                    "启动 %.0fs 后仍连不上调试端口 %s：%s。pref 已写入 user.js"
                    "（devtools.chrome.enabled 等），如果浏览器在建端口前就退出，"
                    "通常是可执行文件/显示环境的问题。"
                    % (launch_timeout, self.port if self.unix_path is None else self.unix_path,
                       json.dumps(info, ensure_ascii=False)[:300]))

            info = {
                "port": self.port if self.unix_path is None else None,
                "unix_socket": self.unix_path,
                "profile": str(profile),
                "user_js": str(prefs_path),
                "executable": plan["executable"],
                "headless": plan["headless"],
                "os": plan.get("os"),
                "locale": plan.get("locale"),
                "proxy_applied": plan.get("proxy"),
                "fingerprint_config": plan.get("fingerprint_config"),
            }
            if plan["warnings"]:
                info["warnings"] = plan["warnings"]
            if had_session:
                info["closed_previous_session"] = True
            self.launch_info = info
            if wait_page:
                self._wait_first_page(wait_page)
            return info

    def _wait_first_page(self, wait_page: float) -> None:
        """等第一个标签页离开 about:blank（比固定 sleep 更稳），再留一点加载时间。"""
        end = time.monotonic() + wait_page
        while time.monotonic() < end:
            try:
                tabs = (self.rdp.call({"to": "root", "type": "listTabs"}, 10) or {}).get("tabs") or []
            except RdpError:
                break
            if any(str(t.get("url") or "") not in ("", "about:blank") for t in tabs):
                break
            time.sleep(0.1)
        time.sleep(min(0.3, wait_page))

    def attach(self, port: int | None = None, unix_path: str | None = None,
               timeout: float = 20.0) -> dict:
        """连到已经在跑的调试端口。"""
        with self._lock:
            if self.rdp is not None:
                self.close()
            if unix_path:
                self.unix_path = unix_path
                self.rdp = RdpClient(unix_path=unix_path, timeout=timeout)
            else:
                self.port = int(port or self.port or 0)
                if not self.port:
                    raise RdpError(
                        "action=\"attach\" 需要 port（或 unix_socket）：端口没有认证也"
                        "没有广播，必须知道正在监听的端口号。")
                self.rdp = RdpClient(port=self.port, timeout=timeout)
            hello = self.rdp.hello()
            return {"port": None if unix_path else self.port,
                    "unix_socket": unix_path,
                    "application_type": hello.get("applicationType"),
                    "traits": hello.get("traits")}

    def close(self) -> dict:
        """关闭会话与本次启动的浏览器（不碰别人的进程）。"""
        acquired = self._lock.acquire(timeout=15.0)
        try:
            rdp, self.rdp = self.rdp, None
            if rdp is not None:
                rdp.close()
            proc, self.proc = self.proc, None
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            profile, self.profile = self.profile, None
            # terminate 只结束启动器；按 profile 把子进程一并清掉，避免端口被残留实例占用
            killed = _kill_by_profile(profile)
            if self.unix_path:
                try:
                    os.unlink(self.unix_path)
                except OSError:
                    pass
                parent = os.path.dirname(self.unix_path)
                if parent and os.path.basename(parent).startswith("rdp_sock_"):
                    shutil.rmtree(parent, ignore_errors=True)
            if profile:
                time.sleep(0.2)
                shutil.rmtree(profile, ignore_errors=True)
            self.thread = self.watcher = self.target = self.tab = self.console = None
            self.tab_url = None
            self.last_pause = None
            self.breakpoints.clear()
            self.xhr_breakpoints.clear()
            self._detached_targets.clear()
            return {"killed_pids": killed, "profile_removed": bool(profile)}
        finally:
            if acquired:
                self._lock.release()

    # ---------- 会话 ----------
    def _call(self, obj: dict, timeout: float = 20.0, aliases=()):
        self._release_cancel()
        return self.rdp.call(obj, timeout, aliases=aliases)

    def _detach_target(self, target: str | None, timeout: float = 2.0) -> bool:
        """detach 旧 target，否则它上面的 XHR 断点会继续让页面冻住（review #5）。"""
        if not target or target in self._detached_targets or self.rdp is None:
            return False
        self._detached_targets.add(target)
        try:
            self.rdp.call({"to": target, "type": "detach"}, timeout)
            return True
        except (RdpError, OSError):
            return False

    def enable(self, url_substr: str | None = None, target_type: str = "frame") -> dict:
        """tab → target form → threadActor → attach。

        * 旧 target 先 detach（review #5）；
        * 不再静默绑第一个标签页：`url_substr` 匹配不到时列出可用 URL 并报错
          （review #14），空 `url_substr` 优先沿用当前绑定的 tab。
        """
        with self._lock:
            rdp = self._require_rdp()
            old_target, old_thread = self.target, self.thread
            tabs = (rdp.call({"to": "root", "type": "listTabs"}, 25) or {}).get("tabs") or []
            if not tabs:
                raise RdpError("浏览器里没有标签页")
            tab = self._pick_tab(tabs, url_substr)
            self.tab = tab["actor"]
            self.tab_url = tab.get("url")

            tgt = rdp.call({"to": self.tab, "type": "getTarget"}, 25) or {}
            frame = tgt.get("frame") or {}
            new_target = frame.get("actor")
            if old_target and old_target != new_target:
                # 导航/换标签页后旧 target 还在：不 detach 的话它上面的 XHR 断点会
                # 继续让页面暂停，而 frames/resume 都发给新 thread（review #5）。
                self._detach_target(old_target)
            self.target = new_target
            self.console = frame.get("consoleActor")
            info = {"tab": self.tab, "target": self.target, "url": tab.get("url")}

            th = frame.get("threadActor")
            if th:
                self.thread = th
                info.update({"thread": th, "via": "getTarget.frame.threadActor"})
            else:
                watch = rdp.call({"to": self.tab, "type": "getWatcher"}, 25) or {}
                self.watcher = watch.get("actor")
                if not self.watcher:
                    raise RdpError("getWatcher 失败: %s" % json.dumps(watch)[:200])
                rdp.send({"to": self.watcher, "type": "watchTargets",
                          "targetType": target_type})
                form = rdp.wait_event(
                    lambda e: "target-available-form" in str(e.get("type", "")), timeout=20)
                th = self._find_thread(form) if form else None
                if not th:
                    raise RdpError("找不到 threadActor; info=%s" % json.dumps(info)[:300])
                self.thread = th
                info.update({"watcher": self.watcher, "via": "watcher", "thread": th})

            info.update(self.attach_thread())
            info["detached_previous_thread"] = bool(old_thread and old_thread != self.thread)
            self._fresh_floor = rdp.mark()
            return info

    def _pick_tab(self, tabs: list[dict], url_substr: str | None) -> dict:
        if url_substr:
            for tab in tabs:
                if url_substr in str(tab.get("url") or ""):
                    return tab
            raise RdpError(
                "url_substr=%r 没有匹配的标签页；当前标签页: %s"
                % (url_substr, json.dumps([t.get("url") for t in tabs], ensure_ascii=False)[:400]))
        for tab in tabs:  # 优先沿用已绑定的 tab，reload 后不会跳到第一个标签页
            if self.tab and tab.get("actor") == self.tab:
                return tab
        if len(tabs) == 1:
            return tabs[0]
        raise RdpError(
            "有 %d 个标签页，无法判断要调试哪个：请传 url_substr。当前标签页: %s"
            % (len(tabs), json.dumps([t.get("url") for t in tabs], ensure_ascii=False)[:400]))

    def attach_thread(self) -> dict:
        """thread.attach（必须带 options）→ isAttached 校验。"""
        rdp = self._require_rdp()
        if not self.thread:
            raise RdpError("先执行 enable")
        out: dict = {}
        r = rdp.call({"to": self.thread, "type": "isAttached"}, 10) or {}
        if not r.get("value"):
            out["attach"] = (rdp.call(
                {"to": self.thread, "type": "attach", "options": {}}, 20) or {}).get("error", "ok")
        r = rdp.call({"to": self.thread, "type": "isAttached"}, 10) or {}
        out["attached"] = bool(r.get("value"))
        if not out["attached"]:
            raise RdpError("线程没有 attach 上: %s" % json.dumps(r)[:200])
        return out

    @staticmethod
    def _find_thread(obj) -> str | None:
        if isinstance(obj, dict):
            if isinstance(obj.get("threadActor"), str):
                return obj["threadActor"]
            for value in obj.values():
                found = FireFoxDebugger._find_thread(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = FireFoxDebugger._find_thread(value)
                if found:
                    return found
        return None

    def _require_rdp(self) -> RdpClient:
        if self.rdp is None:
            raise RdpError("没有会话：先 action=\"launch\" 或 action=\"attach\"")
        return self.rdp

    # ---------- source ----------
    def sources(self) -> list[dict]:
        with self._lock:
            r = self._call({"to": self.thread, "type": "sources"}, 40) or {}
            err = _error_of(r)
            if err:
                raise RdpError("%s: %s" % (err["error"], err["message"]))
            return list(r.get("sources") or [])

    def resolve_sources(self, source_actor: str | None = None, source_url: str | None = None,
                        url_substr: str | None = None) -> list[dict]:
        """按 source actor / 精确 URL / URL 子串定位脚本，绝不静默取第一个（review #7）。"""
        with self._lock:
            all_sources = self.sources()
            if source_actor:
                for item in all_sources:
                    if item.get("actor") == source_actor:
                        return [item]
                raise RdpError(
                    "source actor %r 不在当前线程的脚本列表里（导航后 actor 会全部换新，"
                    "先 enable / sources）。" % source_actor)
            if source_url:
                matches = [s for s in all_sources if str(s.get("url") or "") == source_url]
                if not matches:
                    raise RdpError("没有 URL 为 %r 的脚本；已知 URL: %s"
                                   % (source_url, _urls_preview(all_sources)))
                return matches
            if url_substr:
                matches = [s for s in all_sources
                           if url_substr in str(s.get("url") or "")]
                if not matches:
                    raise RdpError("url_substr=%r 没有匹配的脚本；已知 URL（含无 URL 的 "
                                   "eval/new Function）: %s"
                                   % (url_substr, _urls_preview(all_sources)))
                return matches
            raise RdpError(
                "需要 source（source actor）、source_url（精确 URL）或 url_substr 之一；"
                "已知脚本: %s" % _urls_preview(all_sources))

    def breakable_lines(self, source_actor: str) -> list[int]:
        with self._lock:
            r = self._call({"to": source_actor, "type": "getBreakableLines"}, 20) or {}
            err = _error_of(r)
            if err:
                raise RdpError("%s: %s" % (err["error"], err["message"]))
            return list(r.get("lines") or [])

    def breakable_positions(self, source_actor: str, line: int | None = None) -> dict:
        """某脚本的可停位置：`{line: [column(0-based), ...]}`（review #8）。

        单行压缩文件和 JSVMP 只有落在这张表里的列才停得住。
        """
        with self._lock:
            query = None
            if line:
                query = {"start": {"line": int(line), "column": 0},
                         "end": {"line": int(line), "column": 1 << 30}}
            r = self._call({"to": source_actor, "type": "getBreakpointPositionsCompressed",
                            "query": query}, 30) or {}
            err = _error_of(r)
            if err:
                raise RdpError("%s: %s" % (err["error"], err["message"]))
            positions = r.get("positions") or {}
            return {int(k): sorted(int(c) for c in v) for k, v in positions.items()}

    def source_text(self, source_actor: str, timeout: float = 120.0,
                    max_chars: int = 4_000_000) -> dict:
        """取脚本全文（大脚本走 longString 分片）。

        偏移按 **UTF-16 码元** 推进，分片不切断代理对，超过 `max_chars` 显式截断
        （review #13）。
        """
        with self._lock:
            r = self._call({"to": source_actor, "type": "source"}, timeout) or {}
            err = _error_of(r)
            if err:
                raise RdpError("%s: %s" % (err["error"], err["message"]))
            value = r.get("source")
            if isinstance(value, str):
                truncated = len(value) > max_chars
                text = value[:max_chars] if truncated else value
                return {"text": _trim_dangling_surrogate(text)[0], "truncated": truncated,
                        "length": len(value), "contentType": r.get("contentType")}
            if isinstance(value, dict) and value.get("type") == "longString" and value.get("actor"):
                text, info = self._read_long_string(
                    value["actor"], int(value.get("length") or 0), max_chars, timeout)
                info["contentType"] = r.get("contentType")
                return info | {"text": text}
            return {"text": "", "truncated": False, "length": 0,
                    "contentType": r.get("contentType")}

    def _read_long_string(self, actor: str, length: int, max_chars: int,
                          timeout: float, step: int = 200_000) -> tuple[str, dict]:
        want = min(int(length or 0), int(max_chars or 0))
        chunks: list[str] = []
        pos = 0
        truncated = int(length or 0) > want
        while pos < want:
            end = min(pos + step, want)
            part = self._substring(actor, pos, end, timeout)
            if not part:
                break
            if end < length and _is_high_surrogate(part[-1]):
                # 分片边界正好切在代理对中间：多取一个码元把这一对补齐，
                # 否则拼出来的字符串会带半个字符（review #13）。
                if end + 1 > want:
                    # 截断点正好切在代理对中间：丢掉这个不完整的字符并显式标记截断
                    part = part[:-1]
                    truncated = True
                    if part:
                        chunks.append(part)
                    pos = end
                    break
                part = self._substring(actor, pos, end + 1, timeout)
                if not part:
                    break
                end += 1
            chunks.append(part)
            pos = end
        text = "".join(chunks)
        text, dropped = _trim_dangling_surrogate(text)
        if dropped:
            truncated = True
        return text, {"truncated": truncated, "length": int(length or 0),
                      "units_read": pos, "long_string_actor": actor}

    def _substring(self, actor: str, start: int, end: int, timeout: float) -> str:
        r = self._call({"to": actor, "type": "substring",
                        "start": int(start), "end": int(end)}, timeout) or {}
        err = _error_of(r)
        if err:
            raise RdpError("%s: %s" % (err["error"], err["message"]))
        value = r.get("substring")
        return value if isinstance(value, str) else ""

    def materialize(self, value, max_chars: int = 200_000, timeout: float = 60.0):
        """grip → 文本。longString 只带前一小段，这里分片取回全文（review #14）。"""
        if not isinstance(value, dict):
            return value, None
        if value.get("type") == "longString" and value.get("actor"):
            text, info = self._read_long_string(
                value["actor"], int(value.get("length") or 0), max_chars, timeout)
            return text, info
        return value, None

    # ---------- 断点 ----------
    def set_breakpoint(self, line: int, column: int | None = None,
                       condition: str | None = None, source_actor: str | None = None,
                       source_url: str | None = None, url_substr: str | None = None) -> dict:
        """下断点（review #7 #8）。

        * `source_url` / `url_substr` 走 `location.sourceUrl`（Firefox 客户端就是这么做的）：
          同一 HTML 里第二段及以后的内联脚本、页面刷新/跳转后新建的 source 都会自动挂上；
        * `source_actor` 走 `location.sourceId`，可以断到 `eval` / `new Function`
          这类没有 URL 的代码；
        * `line` 是 1-based，坐标就是 `breakable_lines` / `breakable_positions` 报出来的那套；
        * `column` 按 **1-based** 语义接收（`Error.stack` / DevTools 界面的习惯），
          内部转成 0-based 并吸附到该行最近的可停列；
        * 行上没有可停位置时**不报 ok**：返回 `installed=False` 和附近的可断行。
        """
        with self._lock:
            target_sources = self.resolve_sources(source_actor, source_url, url_substr)
            line = int(line)
            if line <= 0:
                raise RdpError("line 必须 >= 1（1-based）")

            location: dict
            if source_actor:
                location = {"sourceId": source_actor}
            else:
                urls = {str(s.get("url") or "") for s in target_sources}
                if len(urls) != 1:
                    raise RdpError(
                        "url_substr=%r 匹配到多个 URL: %s；请用 source 或 source_url 指明"
                        % (url_substr, json.dumps(sorted(urls), ensure_ascii=False)[:300]))
                location = {"sourceUrl": urls.pop()}

            positions = {}
            for src in target_sources:
                try:
                    positions = self.breakable_positions(src["actor"], line)
                except RdpError:
                    positions = {}
                if positions.get(line):
                    break
            columns = positions.get(line) or []

            if not columns:
                lines: list[int] = []
                for src in target_sources:
                    try:
                        lines = self.breakable_lines(src["actor"])
                    except RdpError:
                        lines = []
                    if lines:
                        break
                nearest = _nearest_lines(lines, line)
                return {"installed": False,
                        "error": "no-breakable-position",
                        "message": "第 %d 行没有可停位置（%s）"
                                   % (line, "该行没有可停的列" if positions else "行号超出/不可断"),
                        "nearest_lines": nearest,
                        "breakable_lines_sample": lines[:20]}

            requested_col = None
            if column is not None:
                requested_col = int(column) - 1  # 1-based → RDP 的 0-based
                if requested_col < 0:
                    requested_col = 0
                # 吸附到"最近的可停位置"：距离相同的时候优先往后再往前（语句的可停
                # offset 通常落在语句内部，往前会吸到上一条语句上去）。
                snapped = min(columns,
                              key=lambda c: (abs(c - requested_col), c < requested_col))
            else:
                snapped = columns[0]
            location["line"] = line
            location["column"] = snapped

            options = {"condition": condition, "logValue": None, "showStacktrace": None}
            reply = self._call({"to": self.thread, "type": "setBreakpoint",
                                "location": location, "options": options}, 30) or {}
            err = _error_of(reply)
            if err:
                return {"installed": False, **err, "location": location}

            record = {"location": dict(location), "options": options,
                      "source_actor": source_actor,
                      "source_url": location.get("sourceUrl"),
                      "line": line, "column_0based": snapped}
            if record not in self.breakpoints:
                self.breakpoints.append(record)
            out = {"installed": True, "status": "ok",
                   "location": {"line": line, "column_0based": snapped,
                                "column_1based": snapped + 1,
                                "sourceUrl": location.get("sourceUrl"),
                                "sourceId": location.get("sourceId")},
                   "breakable_columns_0based": columns,
                   "reply": reply}
            if requested_col is not None and requested_col != snapped:
                out["snapped_from_column_1based"] = requested_col + 1
                out["message"] = ("列 %d 不是可停位置，已吸附到最近的 %d（RDP 列号 0-based，"
                                  "可停列见 breakable_columns_0based）"
                                  % (requested_col + 1, snapped + 1))
            return out

    def remove_breakpoints(self, line: int | None = None, source_actor: str | None = None,
                           source_url: str | None = None, url_substr: str | None = None,
                           remove_all: bool = False) -> dict:
        """删行断点（review #6）。用**当初下发时一模一样的 location** 才能删掉。

        服务端的 key 是 `sourceUrl:sourceId:line:column`，所以这里只按记录匹配，
        支持按 line / source / url 过滤，`remove_all=True` 清空。
        """
        with self._lock:
            records = list(self.breakpoints)
            if not remove_all:
                has_filter = any(value not in (None, "")
                                 for value in (line, source_actor, source_url, url_substr))
                if not has_filter:
                    return {"removed": 0, "locations": [],
                            "message": "没有过滤条件；传 line / source / source_url / "
                                       "url_substr 之一，或者 remove_all=True 清空全部"}
                filtered = []
                for rec in records:
                    if line is not None and rec["line"] != int(line):
                        continue
                    if source_actor and rec.get("source_actor") != source_actor:
                        continue
                    if source_url and rec.get("source_url") != source_url:
                        continue
                    if url_substr and url_substr not in str(rec.get("source_url") or ""):
                        continue
                    filtered.append(rec)
                records = filtered
            if not records:
                return {"removed": 0, "locations": [],
                        "message": "没有匹配的已记录断点（status 里的 breakpoints 才是准的）"}
            removed, failed = [], []
            for rec in records:
                try:
                    reply = self._call({"to": self.thread, "type": "removeBreakpoint",
                                        "location": rec["location"]}, 15) or {}
                    err = _error_of(reply)
                    if err:
                        failed.append({"location": rec["location"], **err})
                        continue
                    removed.append(rec["location"])
                    if rec in self.breakpoints:
                        self.breakpoints.remove(rec)
                except RdpError as exc:
                    failed.append({"location": rec["location"], "error": str(exc)})
            out = {"removed": len(removed), "locations": removed}
            if failed:
                out["failed"] = failed
            return out

    def set_xhr_breakpoint(self, path: str = "", method: str = "ANY") -> dict:
        """在"发起网络请求"处暂停（比行断点更适合压缩/混淆代码）。

        method 必须是 `"ANY"` 或真实方法名（GET/POST）；空串永不匹配
        （服务端判断是 `method !== "ANY" && method !== requestMethod`）。
        """
        with self._lock:
            method_value = method or "ANY"
            reply = self._call({"to": self.thread, "type": "setXHRBreakpoint",
                                "path": path, "method": method_value}, 15) or {}
            err = _error_of(reply)
            if err:
                return {"status": "error", **err}
            ok = bool(reply.get("value"))
            record = {"path": path, "method": method_value}
            if ok and record not in self.xhr_breakpoints:
                self.xhr_breakpoints.append(record)
            return {"status": "ok" if ok else "failed", "path": path, "method": method_value}

    def remove_xhr_breakpoints(self, path: str | None = None, method: str | None = None,
                              remove_all: bool = False) -> dict:
        """移除 XHR 断点：用**设置时的 path+method** 逐个删（review #6）。

        服务端 `_findXHRBreakpointIndex(path, method)` 是 path 与 method 的精确匹配，
        把 `"remove"` 原样当 method 发过去什么都删不掉（还照样回 `value: true`）。
        """
        with self._lock:
            sent: list[dict] = []
            explicit = method not in (None, "", "remove", "ANY")
            if explicit and path is not None and not remove_all:
                # 调用方给了具体方法：就按 (path, method) 精确删
                candidates = [{"path": path or "", "method": method}]
            else:
                candidates = []
                for rec in list(self.xhr_breakpoints):
                    if not remove_all:
                        if path is not None and rec["path"] != path:
                            continue
                        if explicit and rec["method"] != method:
                            continue
                    candidates.append(dict(rec))
                if not candidates and path is not None:
                    # 没记录（比如 attach 到别人起的会话）：把 ANY 也试一遍
                    candidates = [{"path": path or "", "method": "ANY"}]
            for candidate in candidates:
                reply = self._call({"to": self.thread, "type": "removeXHRBreakpoint",
                                    "path": candidate["path"],
                                    "method": candidate["method"]}, 15) or {}
                err = _error_of(reply)
                entry = {"path": candidate["path"], "method": candidate["method"],
                         "value": bool(reply.get("value"))}
                if err:
                    entry.update(err)
                sent.append(entry)
                for rec in list(self.xhr_breakpoints):
                    if rec["path"] == candidate["path"] and rec["method"] == candidate["method"]:
                        self.xhr_breakpoints.remove(rec)
            return {"removed": sum(1 for item in sent if item["value"]), "requests": sent}

    # ---------- 执行控制 ----------
    def _require_thread(self) -> str:
        if not self.thread:
            raise RdpError("先执行 enable")
        return self.thread

    def resume(self, frame_actor: str | None = None, resume_limit=None) -> dict:
        """继续运行。调用前把 floor 抬到当前序号，之后的 `paused` 才算"新的"。"""
        with self._lock:
            thread = self._require_thread()
            self._fresh_floor = self.rdp.mark()
            reply = self._call({"to": thread, "type": "resume",
                                "resumeLimit": resume_limit,
                                "frameActorID": frame_actor}, 20) or {}
            err = _error_of(reply)
            if err:
                return err
            return {"status": "resumed", "reply": reply}

    def step(self, direction: str = "over", frame_actor: str | None = None) -> dict:
        if direction not in _STEP_LIMITS:
            raise RdpError("direction 必须是 over / into / out，收到 %r" % (direction,))
        result = self.resume(frame_actor, _STEP_LIMITS[direction])
        if "error" in result and "status" not in result:
            return result
        return {"status": "stepped", "direction": direction, "reply": result.get("reply")}

    def wait_paused(self, timeout: float = 30.0, require_frame: bool = True) -> dict | None:
        """等下一次暂停。只接受 `resume`/`step`/`interrupt` 之后**新到的** `paused`
        （review #4：缓冲里旧的暂停不会被当成新的）。"""
        with self._lock:
            floor = self._fresh_floor
            found = self.rdp.wait_event(
                lambda e: (e.get("type") == "paused"
                           and (bool(e.get("frame")) or not require_frame)),
                timeout, since=floor)
            if found is None:
                return None
            self._fresh_floor = int(found.get("__seq") or floor)
            self.last_pause = found
            return found

    def interrupt(self, on_next: bool = False, timeout: float = 15.0) -> dict | None:
        """不依赖行断点地暂停（JSVMP 只有一行、无从下断时用这个）。

        review #9：`thread.interrupt` 比较的是**字符串** `when == "onNext"`；
        传 `{"onNext": true}` 会被当成"立即暂停"。Firefox 在 onNext 模式下的回复是
        空包，真正的 `paused` 在下一个语句执行时才到。
        """
        with self._lock:
            thread = self._require_thread()
            self._fresh_floor = self.rdp.mark()
            reply = self._call({"to": thread, "type": "interrupt",
                                "when": "onNext" if on_next else {}}, 20) or {}
            err = _error_of(reply)
            if err:
                raise RdpError("%s: %s" % (err["error"], err["message"]))
            found = self.wait_paused(timeout, require_frame=False)
            if found is None and on_next:
                return None
            return found

    def frames(self) -> dict:
        with self._lock:
            thread = self._require_thread()
            reply = self._call({"to": thread, "type": "frames",
                               "start": 0, "count": 100}, 20) or {}
            return reply

    def scopes(self, frame_actor: str) -> dict:
        with self._lock:
            return self._call({"to": frame_actor, "type": "getEnvironment"}, 25) or {}

    # ---------- 求值 ----------
    def evaluate(self, expr: str, timeout: float = 25.0) -> dict:
        """全局求值（不暂停）：consoleActor.evaluateJSAsync + evaluationResult 事件。"""
        return self.frame_eval(None, expr, timeout)

    def frame_eval(self, frame_actor: str | None, expr: str, timeout: float = 25.0) -> dict:
        """暂停中求值。表达式自己命中断点（页面停住）时立刻返回 `paused` 错误，
        而不是干等到超时再返回全 null（review #12）。"""
        with self._lock:
            console = self.console
            if not console:
                tgt = self._call({"to": self.tab, "type": "getTarget"}, 20) or {}
                console = (tgt.get("frame") or {}).get("consoleActor")
                self.console = console
            if not console:
                raise RdpError("没有 consoleActor")
            floor = self.rdp.mark()
            reply = self._call({"to": console, "type": "evaluateJSAsync",
                                "text": expr, "frameActor": frame_actor}, timeout) or {}
            err = _error_of(reply)
            if err:
                return err
            rid = reply.get("resultID")
            if not rid:
                return reply
            event = self.rdp.wait_event(
                lambda e: ((e.get("type") == "evaluationResult" and e.get("resultID") == rid)
                           or e.get("type") == "paused"),
                timeout, since=floor)
            if event is None:
                return {"error": "no-evaluation-result",
                        "message": "等 evaluationResult 超时；表达式可能还在跑，或者页面已经停住",
                        "resultID": rid,
                        "paused": self.last_pause}
            if event.get("type") == "paused":
                self.last_pause = event
                return {"error": "paused-while-evaluating",
                        "message": "求值过程中页面停住了（表达式命中断点 / debugger 语句）",
                        "paused": {"why": event.get("why"),
                                   "where": (event.get("frame") or {}).get("where"),
                                   "frame": (event.get("frame") or {}).get("actor")},
                        "resultID": rid}
            return event

    # ---------- reload ----------
    def reload(self, wait: float = 6.0) -> dict:
        """刷新页面并重建调试会话。

        实测：reload 之后 target/thread actor 全部换新（child2 → child3），旧 thread
        上的断点随之失效，必须重新 enable + 重新下发断点。

        review #5：
          * 旧 target 先 detach —— 否则它上面的 XHR 断点还会继续暂停页面，而
            `frames`/`resume` 都发给新 thread，页面就恢复不了；
          * 不重新绑到"第一个标签页"（沿用当前 tab）；
          * 不等固定 sleep：轮询到新 threadActor 出现就立刻 attach + 重下断点，
            页面加载期间执行的代码也断得到。
        """
        with self._lock:
            rdp = self._require_rdp()
            thread = self._require_thread()
            old_target, old_thread = self.target, self.thread
            old_floor = self._fresh_floor
            started = time.monotonic()
            try:
                rdp.call({"to": self.target, "type": "reload",
                          "options": {"force": False}}, 30)
            except RdpError:
                try:
                    rdp.call({"to": self.target, "type": "reload"}, 30)
                except RdpError:
                    pass

            new_thread = None
            deadline = time.monotonic() + max(1.0, wait)
            while time.monotonic() < deadline:
                try:
                    tgt = rdp.call({"to": self.tab, "type": "getTarget"}, 10) or {}
                except RdpError:
                    time.sleep(0.05)
                    continue
                frame = tgt.get("frame") or {}
                candidate = frame.get("threadActor")
                if candidate and candidate != old_thread:
                    new_thread = candidate
                    self.target = frame.get("actor") or self.target
                    self.console = frame.get("consoleActor") or self.console
                    break
                time.sleep(0.1)
            if not new_thread:
                raise RdpError("reload 后 %.1fs 内没有拿到新的 threadActor" % wait)
            self.thread = new_thread
            self._detach_target(old_target)
            self.attach_thread()
            self._fresh_floor = rdp.mark()

            reapplied, xhr_reapplied, failures, skipped = [], [], [], []
            for rec in list(self.breakpoints):
                if "sourceId" in rec["location"] and "sourceUrl" not in rec["location"]:
                    # sourceId 指向的是旧 thread 的 source actor，导航后必然失效；
                    # 明说做不到，而不是假装重下成功（review #5 / #7）。
                    skipped.append({"location": rec["location"],
                                    "hint": "这个断点只按 sourceId 下的（eval / new Function "
                                            "这类没有 URL 的代码），导航后 source actor 会消失；"
                                            "要跨导航请用 source_url 下断点"})
                    continue
                try:
                    reply = rdp.call({"to": self.thread, "type": "setBreakpoint",
                                      "location": rec["location"],
                                      "options": rec["options"]}, 20) or {}
                    err = _error_of(reply)
                    (failures if err else reapplied).append(
                        {"location": rec["location"], **err} if err else rec["location"])
                except RdpError as exc:
                    failures.append({"location": rec["location"], "error": str(exc)})
            for rec in list(self.xhr_breakpoints):
                try:
                    reply = rdp.call({"to": self.thread, "type": "setXHRBreakpoint",
                                      "path": rec["path"], "method": rec["method"]}, 15) or {}
                    if reply.get("value"):
                        xhr_reapplied.append(dict(rec))
                    else:
                        failures.append({"xhr": dict(rec), "error": "not-applied"})
                except RdpError as exc:
                    failures.append({"xhr": dict(rec), "error": str(exc)})

            info = {"thread": self.thread, "target": self.target,
                    "console": self.console, "tab": self.tab, "url": self.tab_url,
                    "elapsed": round(time.monotonic() - started, 2),
                    "detached_previous_target": old_target != self.target,
                    "reapplied": reapplied, "xhr_reapplied": xhr_reapplied}
            if failures:
                info["reapply_failed"] = failures
            if skipped:
                info["reapply_skipped"] = skipped
            if self._fresh_floor <= old_floor:
                self._fresh_floor = rdp.mark()
            return info

    # ---------- 其它 ----------
    def status(self) -> dict:
        with self._lock:
            return {
                "running": self.rdp is not None,
                "port": self.port if self.unix_path is None else None,
                "unix_socket": self.unix_path,
                "profile": self.profile,
                "executable": self.exe,
                "tab": self.tab,
                "url": self.tab_url,
                "target": self.target,
                "thread": self.thread,
                "console": self.console,
                "last_paused": (self.last_pause or {}).get("why"),
                "breakpoints": [{"line": rec["line"],
                                 "column_0based": rec["column_0based"],
                                 "source_url": rec.get("source_url"),
                                 "source_actor": rec.get("source_actor")}
                                for rec in self.breakpoints],
                "xhr_breakpoints": [dict(rec) for rec in self.xhr_breakpoints],
            }


def _prepare_unix_dir(path: str) -> None:
    """macOS/Linux 的 Unix socket：放进 0700 目录，只有当前用户能连（review #11）。"""
    parent = os.path.dirname(os.path.abspath(path))
    if not parent:
        return
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass


def make_unix_socket_path() -> str:
    base = tempfile.mkdtemp(prefix="rdp_sock_")
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return os.path.join(base, "rdp.sock")


def _trim_dangling_surrogate(text: str) -> tuple[str, bool]:
    """去掉结尾孤立的代理项，否则 UTF-8 编码会炸（review #13）。"""
    if text and _is_high_surrogate(text[-1]):
        return text[:-1], True
    return text, False


def _nearest_lines(lines: list[int], line: int, count: int = 5) -> list[int]:
    if not lines:
        return []
    ordered = sorted(lines)
    before = [item for item in ordered if item <= line][-count:]
    after = [item for item in ordered if item > line][:count]
    return sorted(set(before + after))


def _urls_preview(sources: list[dict], limit: int = 8) -> str:
    items = []
    for src in sources[:limit]:
        url = src.get("url")
        intro = src.get("introductionType")
        items.append(str(url) if url else "<no-url:%s:%s>" % (intro, src.get("actor", "").split("/")[-1]))
    if len(sources) > limit:
        items.append("...(共 %d 个)" % len(sources))
    return json.dumps(items, ensure_ascii=False)


# ---------------------------------------------------------------- 自测
SELF_TEST_PROBE = (
    "var s=document.createElement('script');"
    "s.textContent=('window.__dbg_probe=function(arg){var tag=\"rdp-ok\";debugger;"
    "return arg};\\n' + 'window.__dbg_target=function(a){var b=a*2;return b+1};\\n');"
    "document.documentElement.appendChild(s);'injected'"
)


def _self_test_source_for(dbg: "FireFoxDebugger") -> tuple[dict | None, list[str]]:
    """找注入脚本对应的 source（about:blank 上的内联脚本没有 URL，只能按 actor 定位）。"""
    for src in reversed(dbg.sources()):
        if src.get("introductionType") in ("scriptElement", "debugger eval"):
            got = dbg.source_text(src["actor"], max_chars=200_000)
            lines = (got.get("text") or "").splitlines()
            if any("__dbg_target" in line for line in lines):
                return src, lines
    return None, []


def _self_test() -> int:
    """离线自测：about:blank + 注入脚本，不访问任何外部站点。"""
    failures: list[str] = []

    def check(condition, message: str) -> None:
        if not condition:
            failures.append(message)

    dbg = FireFoxDebugger(port=0)
    try:
        info = dbg.launch("about:blank", wait_page=1.0)
        print("[ok] launch:", {k: info.get(k) for k in
                               ("port", "headless", "os", "locale", "fingerprint_config")})
        check(info.get("port"), "launch 没有拿到端口")
        check(info.get("user_js"), "launch 没有写 user.js")

        enabled = dbg.enable()
        print("[ok] enable:", {k: enabled.get(k) for k in
                               ("target", "thread", "via", "attached")})

        result = dbg.evaluate(SELF_TEST_PROBE)
        print("[ok] evaluate:", result.get("result"))
        check(result.get("result") == "injected", "evaluate 返回 %r" % (result.get("result"),))

        src, lines = _self_test_source_for(dbg)
        print("[ok] source:", (src or {}).get("actor"), "lines:", len(lines))
        check(src is not None, "找不到注入脚本对应的 source")

        if src:
            # 行断点：sourceId + 可停列（about:blank 的内联脚本没有 URL）
            target_line = next((i + 1 for i, line in enumerate(lines) if "var b=a*2" in line), 0)
            line_text = lines[target_line - 1] if target_line else ""
            column_1based = line_text.index("var b") + 1 if "var b" in line_text else None
            positions = dbg.breakable_positions(src["actor"], target_line)
            print("[ok] breakable_positions:", {target_line: positions.get(target_line)},
                  "column_1based:", column_1based)
            bp = dbg.set_breakpoint(target_line, column=column_1based,
                                    source_actor=src["actor"])
            print("[ok] set_breakpoint:", {k: bp.get(k) for k in
                                           ("installed", "status", "location", "message")})
            check(bp.get("installed"), "set_breakpoint 没装上: %r" % (bp,))
            got_col = (bp.get("location") or {}).get("column_0based")
            line_columns = (positions.get(target_line) or [])
            if column_1based is not None:
                check(got_col in line_columns,
                      "吸附后的列 %r 不在可停位置 %r 里" % (got_col, line_columns))
                expected = min(line_columns,
                               key=lambda c: (abs(c - (column_1based - 1)),
                                              c < column_1based - 1)) if line_columns else None
                check(got_col == expected,
                      "列号吸附错了：%r -> %r，期望 %r" % (column_1based, got_col, expected))

            # 触发：evaluate 过程中命中断点 → 立刻返回 paused 错误（review #12）
            hit = dbg.evaluate("window.__dbg_target(3)")
            print("[ok] evaluate-hits-breakpoint:", hit.get("error"),
                  (hit.get("paused") or {}).get("why"))
            check(hit.get("error") == "paused-while-evaluating",
                  "表达式命中断点时没有立刻返回 paused: %r" % (hit,))
            paused = dbg.wait_paused(10)
            check(paused is not None, "断点没有暂停")
            where = ((paused or {}).get("frame") or {}).get("where") or {}
            check(where.get("line") == target_line,
                  "断点停在第 %r 行，期望 %r" % (where.get("line"), target_line))
            print("[ok] paused at breakpoint:", (paused or {}).get("why"), where)

            frame_actor = ((paused or {}).get("frame") or {}).get("actor")
            frames = dbg.frames()
            print("[ok] frames:", [(f.get("displayName"),
                                    (f.get("where") or {}).get("line"))
                                   for f in frames.get("frames") or []])
            check(bool(frames.get("frames")), "frames 为空: %r" % (frames,))
            print("[ok] scopes:", json.dumps(dbg.scopes(frame_actor))[:160])
            print("[ok] frame_eval:", dbg.frame_eval(frame_actor, "a + 1").get("result"))
            stepped = dbg.step("over", frame_actor)
            print("[ok] step:", stepped.get("status"), stepped.get("direction"))
            dbg.wait_paused(10)
            print("[ok] resume:", dbg.resume().get("status"))

            removed = dbg.remove_breakpoints(line=target_line)
            print("[ok] remove_breakpoint:", removed)
            check(removed.get("removed") == 1, "行断点没有删掉: %r" % (removed,))
            again = dbg.evaluate("window.__dbg_target(4)")
            print("[ok] evaluate after remove:", again.get("result"))
            check(again.get("result") == 9,
                  "删掉断点后求值结果异常: %r" % (again,))

        # debugger 语句仍然能停 + 单步 + 暂停中求值
        dbg.evaluate("window.__dbg_probe(7)")
        paused = dbg.wait_paused(20)
        check(paused is not None, "debugger 语句没有暂停")
        print("[ok] paused:", (paused or {}).get("why"))
        frame_actor = ((paused or {}).get("frame") or {}).get("actor")
        print("[ok] frame_eval:", dbg.frame_eval(frame_actor, "arg + '|' + tag").get("result"))
        dbg.resume()

        # interrupt onNext（字符串 when，review #9）
        dbg.evaluate("window.__t=setInterval(function(){var z=1;z++;},20)")
        time.sleep(0.2)
        paused = dbg.interrupt(on_next=True, timeout=8)
        why = (paused or {}).get("why") or {}
        print("[ok] interrupt onNext:", why)
        check(bool(why.get("onNext")), "interrupt on_next 没有按 onNext 暂停: %r" % (why,))
        dbg.resume()
        dbg.evaluate("clearInterval(window.__t)")
        time.sleep(0.1)

        # xhr 断点设置 + 用设置时的 method 移除（review #6）
        print("[ok] xhr set:", dbg.set_xhr_breakpoint("/api/x", "POST"))
        removed = dbg.remove_xhr_breakpoints(path="/api/x")
        print("[ok] xhr remove:", removed)
        check(removed.get("requests") and removed["requests"][0]["method"] == "POST",
              "xhr 移除没有按 POST 精确发送: %r" % (removed,))

        # reload：新 thread + 重下断点（review #5）
        dbg.set_xhr_breakpoint("/api/y", "ANY")
        reload_info = dbg.reload(wait=8)
        print("[ok] reload:", {k: reload_info.get(k) for k in
                               ("thread", "elapsed", "detached_previous_target",
                                "xhr_reapplied")})
        check(reload_info.get("xhr_reapplied"), "reload 后 XHR 断点没有重下")
        print("[ok] status:", {k: dbg.status().get(k) for k in
                               ("port", "thread", "breakpoints", "xhr_breakpoints")})
    except Exception as exc:  # noqa: BLE001 - 自测要把失败原因打出来
        failures.append("%s: %s" % (type(exc).__name__, exc))
        print("[!!]", failures[-1])
    finally:
        closed = dbg.close()
        print("[ok] close:", closed)
    if failures:
        print("[fail]", failures)
        return 1
    print("[done] self-test ok")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    print(__doc__)
