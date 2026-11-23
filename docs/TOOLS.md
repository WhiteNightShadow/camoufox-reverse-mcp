# 工具参考

[中文](TOOLS.md) | [English](TOOLS_en.md)

共 43 个工具，按用途分 10 类。安装方式和启动参数见 [README](../README.md)。

- [可用工具一览](#可用工具一览)
- [使用场景示例](#使用场景示例)
- [技术架构](#技术架构)
- [为什么选择 Camoufox？](#为什么选择-camoufox)

## 可用工具一览

### 浏览器控制
| 工具 | 说明 |
|------|------|
| `launch_browser` | 启动 Camoufox；没传的参数沿用服务启动参数；可用 `browser_version` 单次选择已安装版本，不修改 active 配置 |
| `close_browser` | 关闭浏览器，释放资源 |
| `navigate` | 导航到指定 URL（支持 pre_inject_hooks、redirect_chain 追踪） |
| `reload` | 刷新页面 |
| `take_screenshot` | 截图（支持全页面、指定元素） |
| `take_snapshot` | 获取页面无障碍树（token 高效） |
| `click` / `type_text` | 点击元素 / 输入文本 |
| `wait_for` | 等待元素出现或 URL 匹配 |
| `get_page_info` | 获取当前页面 URL、标题、视口尺寸和 Frame 清单 |

### JS 执行与调试
| 工具 | 说明 |
|------|------|
| `evaluate_js` | 执行 JS 表达式；默认隔离上下文，可显式选择主世界和目标 Frame |
| `trace_session(action)` | Playwright 会话录制：`start` 开始（screenshots/snapshots/sources）、`stop` 导出 `.zip`（可用 trace.playwright.dev 或 `playwright show-trace` 打开）、`status` 查看状态。只记录默认浏览器上下文；重新启动浏览器后未停止的录制会丢失 |

### 脚本分析
| 工具 | 说明 |
|------|------|
| `scripts(action)` | 脚本管理：`list` 列出 / `get` 获取源码 / `save` 保存到本地 |
| `search_code` | 搜索关键词（`script_url=None` 全量搜索，指定 URL 则单脚本搜索，自动检测压缩文件用字符级上下文） |

### Hook 与追踪
| 工具 | 说明 |
|------|------|
| `hook_function` | Hook 或追踪函数；支持主世界、Frame、持久化和动态目标等待 |
| `get_trace_data` | 读取/清理函数 Trace，按 world 与 Frame 过滤 |
| `inject_hook_preset` | 一键注入预置 Hook（xhr / fetch / crypto / websocket / debugger_bypass / cookie / runtime_probe），装在页面主世界，能记到页面自己发的请求和调用；日志用返回值里 `read_with` 的写法读取，例如 `evaluate_js("window.__mcp_xhr_log \|\| []", world="main")` |
| `remove_hooks` | 移除所有 Hook 并恢复原始对象（含 preset 和 JSVMP 探针） |
| `get_console_logs` | 获取页面 console 输出 |

### 网络分析
| 工具 | 说明 |
|------|------|
| `network_capture(action)` | 网络捕获控制：`start` / `stop` / `clear` / `status` |
| `list_network_requests` | 列出已捕获的请求（支持 URL / 域名 / 方法 / 类型 / 状态码过滤） |
| `get_network_request` | 获取请求完整详情（`max_body_size` 控制 body 截断） |
| `get_request_initiator` | 获取请求发起的 JS 调用栈 |
| `extract_script` | 从已捕获响应里取出一段脚本（按 `json_path` 走 JSON，如 `data.data`）；可写 `output_file`。用于 fetch+eval 型 SDK：这类代码没有 script 资源，源码搜索与路由改写都够不到 |
| `intercept_request` | 拦截请求：log / block / modify / mock / stop。`mock_response` 支持 `body`（内联文本）或 `body_file`（≤10 MiB 的常规 UTF-8 文件，相对路径按服务器工作目录解析、每次请求在工作线程重读，两者互斥；注意响应内容对页面脚本可读，勿指向本地敏感凭据文件） |
| `export_network_capture` | 导出带版本号的 JSON 快照；默认掩码请求头和查询参数、不含 body，不覆盖已有文件 |
| `compare_network_requests` | 对比 2 到 10 个已捕获的请求，不发请求，也不需要启动浏览器 |
| `save_response_body` | 保存已捕获的响应字节（JS / WASM / JSON / 二进制），不重新请求，不覆盖已有文件 |

### JSVMP 逆向分析

> **反爬类型 → 工具路径对照表**
>
> | 反爬类型 | 代表 | ✅ 推荐路径 | ❌ 禁用 |
> |---|---|---|---|
> | **签名型**（环境即签名） | RS 5/6、AK sensor_data | `instrumentation(action="install")` | `pre_inject_hooks`、`hook_jsvmp_interpreter(mode="proxy")` |
> | **行为型**（参数签名） | TK JSVMP、JY gt4 | `hook_jsvmp_interpreter(mode="proxy")` | — |
> | **纯混淆** | 常见 JS 混淆工具 | 任意组合 | — |

| 工具 | 说明 |
|------|------|
| `hook_jsvmp_interpreter` | JSVMP 运行时探针，装在页面主世界（`mode="proxy"` 全覆盖 / `mode="transparent"` 只替换 getter，改动较少）；日志用 `evaluate_js("window.__mcp_jsvmp_log \|\| []", world="main")` 读取。返回值的 `coverage` 写明能记录和记录不到的范围，日志里没有不等于页面没调用；同源 iframe（包括页面临时建了又删掉的 about:blank）里的事件写进顶层日志，带 `frame_url`/`frame_depth` |
| `instrumentation(action)` | 源码级插桩：`install` 注册改写 / `log` 获取日志（可按 `after_seq`、`seq_window`、`stage` 切片） / `stop` 停止 / `reload` 重载 / `status` 查看状态和原样放行的文件 / `rewrite_source` 不开浏览器改写本地源码 |
| `stage_break(action)` | 配合 `tap_stage_calls` 的状态级深挖：`set` 对指定状态 `p` 记录有界深拷贝（`args_only`/`args_ret`/`args_ret_stack`）、`clear` 撤销、`list` 查看命中计数；产出 `tap_stage_break` 事件。布下的状态在本次 MCP 会话内一直有效，之后改写的脚本都带上它，刷新后页面加载阶段执行的状态也能记到；快照只读自有数据属性，不调用 getter |
| `compare_env` | 浏览器环境指纹收集，用于与 Node.js/jsdom 对比；传 `output_path` 导出完整环境包（navigator、screen、window、CSS、canvas、WebGL、audio、storage、iframe、timing） |

默认只改写成员读取（`rewrite_member_access=True`、`rewrite_calls=False`）。调用改写会让每次调用经过 MCP 的辅助函数：
被调函数读到的 `caller` 变成 null，最大递归深度下降，调用栈多出几层，指纹脚本可能因此算出不同的值。需要 `tap_call`/`tap_method`
事件时再打开。

超过 `max_file_size`（默认 2MB）的脚本按 `on_oversized` 处理，默认在没有属性过滤时原样放行。`status` 和 `log` 的
`skipped_files` 逐个列出这些文件的 URL、大小、原因和建议的 `max_file_size`；改写处数到 `max_rewrites` 上限的文件列在
`capped_files`。

每个事件都带 `seq`，同一页面（含同源子 frame）统一编号；在 `tap_stage_calls` 的状态里发生的事件带 `stage`（最内层的状态号）。
`log` 用 `after_seq` 往后翻页（返回 `next_after_seq`），用 `seq_window=[起, 止]` 取一段，用 `stage` 取某个状态里的事件；
日志满 20000 条后丢掉的条数在 `dropped`。同源子 frame 里改写过的脚本写进顶层日志，带 `frame_url`/`frame_depth`，
页面删掉 iframe 后事件仍在。

`instrumentation(action="rewrite_source")` 不需要浏览器：对 `source`（文本）或 `source_path`（文件）做同样的改写，写到
`output_path`（改写结果超过约 100KB 时必须给）。改写后的脚本在 Node、vm 和 jsdom 里也能运行，事件在 `globalThis.__mcp_vmp_log`。

需要对齐浏览器与沙箱中的同一执行点时，可在安装时设置
`include_source_site=True`。事件会增加稳定 `site_id`，`log`
返回 `source_sites` sidecar，映射到拦截脚本的原始字符区间（`rewrite_source` 写到 `<output_path>.sites.json`）。该能力默认关闭；
它表示混淆后 JS 的源码位置，不会猜测 VM 的 PC、opcode 或保护前源码位置。

#### 跨 build 的正则插桩（`inject`）

`tap_stage_calls` 只认一种固定的扁平化 dispatcher 形态；而 `case 0x22 / 0x4b / 0x4e / 0x51` 这类 opcode
编号是 **build 绑定**的，换一份脚本就不命中。`inject` 让你用「这段源码长什么样」直接打点，与形态无关：

```text
instrumentation(
  action="install",
  url_pattern="**/vm-bundle*.js*",
  tag="vm",
  inject=[{
    "find": r"case (0x[0-9a-f]+):(_0x[0-9a-f]+)\[(_0x[0-9a-f]+)\[\+\+(_0x[0-9a-f]+)\]\]\[\2\[",
    "before": "try{(window.__hit=window.__hit||[]).push([$1,$4]);}catch(e){}",
    "group": "write-opcode"
  }]
)
```

- `find` 是 Python 正则（`re.MULTILINE`），同一脚本内**全部匹配**都插（受 `max_rewrites` 上限保护）；`$1..$99`/`$0` 引用捕获组，`$$` 是字面 `$`
- `before` = 匹配起点之前，`after` = 匹配结束之后；两者可同时给
- 命中与跳过统计：返回值 `inject.groups[].insertions`（及原始匹配数 `.matches`）、`status`/`log` 的 `inject_hits`（按 `group` 在路由生命周期内跨新改写脚本累加）、`inject_skipped`、`inject_literal_skips`；0 处插入时进 `skipped_files`，按实情区分 `no_inject_matches`、`inject_literal_only` 或 `inject_would_break_syntax`
- 不破坏语法的策略（顺序即优先级）：
  1. 片段一律包成完整语句（首尾 `;` 守卫，即使以 `}` 结尾也补 `;`；含换行或 `//` 时改用 `;\n…\n;` 包裹，防止行注释吞掉尾分号）。单行片段自身不新增换行（`lines_added == 0`；注：`mode="ast"` 会在文件顶部注入 runtime 头使行号整体下移，仅 `mode="regex"` 且无成员改写时保留原始行号）
  2. 命中落在字符串 / 模板文本段（支持 `${...}` 嵌套追踪） / 注释 / 正则字面量（区分 `i++/2`、`obj.return / 2` 与 `if (...) /re/`）里 → 逐处跳过并计入 `inject_literal_skips`
  3. `before` 落在 `switch` 的 `case <expr>:` / `default:` 标签处 → 词法跳过字面量、括号与三元运算符 `? :` 自动挪到该标签冒号之后（`label_shift`），并排除对象字面量属性 `{default: ...}`
  4. 原脚本可解析（先进程内 `esprima`，现代语法回退本地随包 Acorn）时，插入后再解析校验；若整体不通过则**逐组回退**，坏组标记 `skipped_groups[].reason: "would break syntax"`
- 注入跑在模式改写**之前**：`find` 始终对着原始脚本匹配，偏移不受 `ast`/`regex` 改写影响；`inject_validate=False` 仅跳过第 4 条重解析校验，第 1/2/3 条词法与语句守卫仍然生效
- 插入的代码运行在页面主世界（和现有插桩一致），读它挂的全局要用 `evaluate_js("window.__hit || []", world="main")`
- 想离线预览再注回页面：`instrumentation(action="rewrite_source", source_path=..., output_path=..., inject=[...])`，然后 `intercept_request(action="mock", url_pattern=..., mock_response={"body_file": output_path})`

### Cookie 与存储
| 工具 | 说明 |
|------|------|
| `cookies(action)` | Cookie 管理：`get` / `set` / `delete` |
| `get_storage` | 获取 localStorage / sessionStorage |
| `export_state` / `import_state` | 导出 / 导入完整浏览器状态 |

### 验证与环境
| 工具 | 说明 |
|------|------|
| `verify_signer_offline` | 离线验证签名函数：传入样本列表，逐样本字符级对比，定位首偏差点 |
| `check_environment` | 一站式自检：MCP、依赖、Camoufox Python/active/已安装版本及定制版状态 |
| `reset_browser_state` | 清理残留（hooks / capture / routes / 当前 engine trace），不关浏览器 |

### 引擎层属性追踪

> 需要 [camoufox-reverse](https://github.com/WhiteNightShadow/camoufox-reverse) 定制版浏览器。未安装时返回错误提示，不影响其他工具使用。

从 v1.3.0 起，Camoufox Python 0.5+ 用户可让官方版和定制版并存，且不改变
持久化 active 版本：

```text
check_environment()
launch_browser(
  browser_version="whitenightshadow/152.0.4-beta.30-reverse.5",
  enable_trace=True
)
```

`browser_version` 必须使用 `repo/版本或精确目录`，存在同版本多份资产时必须传
精确目录。省略该参数时启动行为与 v1.2.0 完全相同；Camoufox 0.4.x 用户继续
使用原来的平铺缓存。所选版本必须与 active 浏览器具有相同完整 version/build，避免
上游共享资源产生版本混用。MCP 不下载浏览器、不修改 `config.json`，也不会触发缓存迁移。

| 工具 | 说明 |
|------|------|
| `trace_property_access` | Gecko 原生 DOM/Web API 定点追踪；reverse.5 声明 77 点，旧兼容构建为 75 点，不改写页面 JS 对象/描述符/原型。支持 `action=capture/start/stop/query/clear/status`、summary/timeline/sequence/search、get/set/call、对象与 native site 过滤。`collect_values=True` 仅做追踪后的安全快照并列出跳过项，不代表事件发生时的值 |
| `list_trace_files` | 跨独立 run 目录列出 trace 文件（用于事后分析） |
| `query_trace_file` | 查询 trace 缓存内的历史文件，支持对象、关键词、kind 与 site 过滤 |

每次 `enable_trace=True` 启动都会使用独立 run 目录，只控制本次浏览器的
PID，不会清理或开关其他 MCP 实例。开启 engine trace 时会临时关闭 Firefox
content sandbox 以允许内容进程写入；普通启动不会改动 sandbox。

### Firefox RDP 断点调试

> **为什么需要这一组**：camoufox 是 Firefox，Playwright 侧走 Juggler，没有 CDP。
> Playwright 拿不到「停在某一行、看局部变量、单步」。这一组直接开 Firefox 原生
> RDP（`-start-debugger-server`），把等价能力补齐，全程不经过 Playwright，
> 也不需要浏览器扩展。
>
> **适用**：Firefox 140+ 的 devtools 协议。已在 Windows（camoufox 152 / reverse.5）与
> macOS（官方 Camoufox 158 `Camoufox.app`、非 Camoufox 的原版 Firefox Nightly，以及
> `unix_socket=true` 模式）实测通过 `--self-test` 与真实浏览器冒烟。

> **启动方式（和服务端的 launch_browser 保持一致）**：`launch` 不自己拼命令行，
> 而是复用 `camoufox.pkgman.launch_path()` 解析**当前激活**版本的可执行文件
> （禁止自动下载浏览器；macOS 自动落到 `Camoufox.app/Contents/MacOS/camoufox`，不会
> 退化成按 mtime 取最新），并继承 `BrowserManager.default_config`：`CAMOU_CONFIG`
> 指纹、代理、`--headless`、`--os`、`--locale`、`browser_version`。临时代理 profile
> 里会写 `user.js`，至少包含
> `devtools.debugger.remote-enabled=true`、`devtools.chrome.enabled=true`、
> `devtools.debugger.prompt-connection=false` —— `DevToolsStartup` 要求前两个都为
> true 才开调试端口（官方构建里 `devtools.chrome.enabled` 默认 false，空 profile 下
> `MOZ_HEADLESS=1` / `-headless` 都开不了端口）。
>
> **不继承的部分**（明说，不静默降级）：代理认证（`username`/`password` 需要交互
> 弹窗，带认证的代理上请求会失败，不会直连泄漏真实 IP）、`headless="virtual"`
> （没有 Xvfb 包装，按普通 headless 处理）、`ws_endpoint` 这类 Playwright attach
> 模式。传入非 Camoufox 的原版 Firefox（无 `properties.json`）或 `camoufox` 不可用时，
> 退化成裸 profile 启动（代理与 RDP pref 仍写入 `user.js`），并在返回值的 `warnings`
> 里说明指纹/语言没有继承。

> **安全**：调试端口没有认证，而且它能打开的前提是 `devtools.chrome.enabled=true`
> —— 也就是说**能连上端口的人可以调试浏览器主进程，可以用当前系统用户的身份执行
> 任意代码**；同一台机器上其他用户也能连 `127.0.0.1:<port>`。因此：默认端口取随机的
> 空闲端口、只监听 loopback；macOS/Linux 可以用 `unix_socket=true` 把调试口放进权限
> 0700 临时目录里的 Unix socket（只有当前用户能连）；端口被占用时报错会说明是谁占着
> （自己的浏览器还没退完 / 别的进程），并建议 `port=0`；本工具只清理自己启动的进程，
> MCP 退出时（`atexit` + 信号处理）会把自己的浏览器一起关掉，重复 `launch` 会先关旧会话。

| 工具 | 说明 |
|------|------|
| `rdp_debugger` | 单个工具 + `action` 分发（20 个动作），所有阻塞动作都在 `asyncio.to_thread` 里执行，不会卡住 MCP 事件循环和 Playwright 那边的浏览器事件；同一会话的调用在协议层串行化，并发调用不会串结果，客户端取消请求会让工作线程退出 |

`action` 一览：

| action | 作用 |
|---|---|
| launch | 启动带调试端口的浏览器（默认随机空闲端口；继承服务端配置；已有会话先关掉） |
| attach | 连到已在运行的调试端口（`port`，或 macOS/Linux 的 `socket_path`） |
| enable | `listTabs → getTarget → threadActor` 并 attach 线程；先 detach 旧 target，不跳回第一个标签页 |
| sources | 列出线程已知脚本（含 `sourceStartLine` / `sourceLength`），可按 `url_substr` 或 `url_pattern`（glob）过滤 |
| source | 取脚本全文；longString 分片按 UTF-16 码元拼接，超 `max_chars` 显式标记 `truncated` |
| breakable_lines | 某脚本的可断行号 |
| breakable_positions | 某脚本（或某一行）的可停位置：`columns_0based` / `columns_1based` |
| set_breakpoint | 下断点：`url_pattern` + `find` 按 URL glob 与正则定位（`occurrence` ≥ 1 选第几个匹配，内部把偏移换算成 `line/column`；适合 URL 带随机查询参数的目标），`source_url` 走 `sourceUrl`（内联脚本、导航后都挂得上），`source` 走 `sourceId`（eval / `new Function`）；`column` 收 1-based 并吸附到最近可停列；行上不可停时返回 `installed: false` |
| remove_breakpoint | 删行断点：按当初下发的 location 精确删除；要给 `line`/`source`/`source_url`/`url_substr`/`url_pattern`/`find` 之一，或 `remove_all=true` 清空 |
| wait_paused | 等下一次命中，只接受 `resume`/`step`/`interrupt` 之后新到的 `paused`，返回 frame / where / why |
| frames | 当前暂停的完整调用栈；不处于暂停态时返回 `{"error": "wrongState"}` |
| scopes | 某帧的递归作用域链与各层 bindings（变量值），需要 `frame_actor` |
| evaluate | 给了 `frame_actor` 就在暂停帧内求值（能读局部变量），否则页面全局求值；表达式命中断点时立刻返回 `paused-while-evaluating` 而不是等超时；longString 结果自动取回全文 |
| step | 单步：`over` / `into` / `out`（写错报错），`wrongState` 不报 stepped |
| resume | 继续运行 |
| interrupt | 不依赖行断点直接暂停（单行 JSVMP 无从下断时用）；`on_next=true` 发字符串 `when="onNext"`，暂停在下一个语句 |
| reload | 刷新页面并重建会话：先 detach 旧 target（否则旧 thread 的 XHR 断点会把页面冻住），轮询到新 threadActor 就 attach 并重下断点，不等固定 sleep；`url_pattern` + `find` 的断点会重新 `sources()` → 重算偏移 → 重下，正则不再命中时写进 `reapply_failed`；只按 `sourceId` 下的断点（eval / `new Function`）导航后必然失效，会在 `reapply_skipped` 里说明 |
| xhr_breakpoint | 在「请求即将发出」处暂停，fetch 与 XHR 都命中；`remove=true` 用设置时的 path+method 精确移除 |
| status | 会话状态（端口 / unix socket、tab、target、thread、最近一次暂停原因、已下断点） |
| close | 结束会话并关闭本次启动的浏览器 |

坐标与实测坑：

- **行号 1-based**；`breakable_lines` / `breakable_positions` 报出来的就是下断点用的
  那套坐标：内联脚本是 **HTML 文档行号**（`sourceStartLine` 是 `<script>` 所在行，
  可断行从下一行开始），`eval` / `new Function` 用它自己源码的行号。
- **列号 0-based，而且必须正好落在可停位置**（服务端按 `column + columnBase` 精确
  查找），`Error.stack` / DevTools 界面的列号是 1-based。工具的 `column` 参数按
  1-based 收，内部减 1 再吸附到最近可停列，并在 `snapped_from_column_1based` /
  `breakable_columns_0based` 里说明；单行压缩文件和 JSVMP 先用 `breakable_positions`
  看清有哪些列。
- `set_breakpoint` 用 `location.sourceUrl` 时，Firefox 会把断点**按 URL** 记下来，
  同一 HTML 里第二段及以后的内联脚本、页面刷新/跳转后新建的 source 都会自动应用；
  删除时服务端的 key 是 `sourceUrl:sourceId:line:column`，所以必须原样重发，
  `remove_breakpoint` 就是这么做的。反过来，只用 `source`（sourceId）下的断点绑定在
  某个 source actor 上，导航后那个 actor 就没了，`reload` 会在 `reapply_skipped` 里
  明确报告重下不了 —— 需要跨导航的断点请用 `url_pattern` + `find` 或 `source_url`。
- 最小用法（URL 带动态哈希/时间戳、opcode 换 build 的通用写法）：

  ```text
  rdp_debugger(
    action="set_breakpoint",
    url_pattern="**/vm-bundle*.js*",
    find=r"case (0x[0-9a-f]+):(_0x[0-9a-f]+)\[",
    occurrence=1,
    condition="p === 0x22"
  )
  # 之后每次 reload 都会自动重新定位重下；结果看 reapplied / reapply_failed
  rdp_debugger(action="reload", wait_page=8)
  ```

- **URL 每次请求都变化的目标（URL 带动态查询串或版本哈希）用 `url_pattern` + `find`**：
  每轮下发不同字节哈希的脚本时，精确 URL 和 `sourceId` 都撑不过一次 `reload`。
  断点记录会保存 glob + 正则 + `occurrence`，`reload` / `enable` 重建线程后自动
  重新 `sources()` → 找到新 actor → 重算偏移 → 重下；新构建里正则不再命中时，
  `reapply_failed` 里给出 `{pattern, url_pattern, reason: "regex not found", searched}`。
  位置由正则表达，因此目标代码在脚本整体平移、行号/列号全变的情况下仍然对得上。
- `thread.attach` 必须带 `options:{}`，否则线程停在 DETACHED，`sources` 恒为 0 且不报错。
- `setXHRBreakpoint` 的 `method` 必须是 `"ANY"` 或真实方法名，传空串永不匹配；移除
  同样是 path+method 精确匹配，所以不能用 `"remove"` 当 method（工具会重发当初设置
  时的那一对）。
- 导航后 target/thread actor 会全部换新，旧 target 必须 detach；`reload` 会自动完成。
- 长字符串（脚本源码、大的求值结果）是 `longString` grip（`{type:"longString",
  actor, length}`），正文要用 `substring` 按 UTF-16 码元分片取；`source` 和
  `evaluate` 都会自动取回，超过 `max_chars` 时返回值里 `truncated: true`。
- 并发：同一会话的调用被一把锁串行化；客户端取消请求后工作线程会退出（不会再继续
  读同一个 socket）。


## 使用场景示例

### 场景 1：逆向登录接口的签名参数

```
1. launch_browser()
2. inject_hook_preset("xhr")
3. inject_hook_preset("crypto")
4. navigate("https://example.com/login")
5. type_text("#username", "test") → click("#login-btn")
6. list_network_requests(method="POST")
7. get_request_initiator(request_id=3)     ← 定位签名函数
8. search_code("sign")                     ← 搜索签名代码
9. hook_function("window.getSign", mode="trace", world="main", persistent=True)
10. reload() → get_trace_data("window.getSign", world="main") ← 收集追踪数据
```

### 场景 2：通用 JSVMP 逆向（RS / AK / 自研 VMP）

```
1. launch_browser()
2. network_capture(action="start")
3. navigate("https://target-site.com/")
4. list_network_requests(resource_type="script")  ← 找到 VMP 脚本
5. instrumentation(action="install", url_pattern="**/vmp_target*.js", mode="ast")  ← 默认只记成员读取
6. inject_hook_preset("cookie", persistent=True)
7. instrumentation(action="reload")               ← 让插桩生效
8. instrumentation(action="log", type_filter="tap_get")  ← 看 VMP 读了什么环境
9. 要看 VMP 调了什么 API：install 时加 rewrite_calls=True，reload 后用 type_filter="tap_method"
10. compare_env(output_path="env.json")            ← 导出环境包，用于 Node.js 补齐
```

### 场景 2b：控制流平坦化 VMP 的阶段对照

把「每个状态的分发调用」变成可对照的事件流，适合在真实浏览器与本地补环境之间逐阶段比对：

```
1. instrumentation(action="install", url_pattern="**/security-js/*.js",
                   tap_stage_calls=True, tag="vmp")   ← 改写 return fn.apply(this,[p].concat(...))
2. navigate("https://target-site.com/")               ← 必须 install 之后再导航
3. instrumentation(action="log", type_filter="tap_stage")
   → summary.hot_stages 给出每个状态 p 的调用次数，entries 里带 argc / args / ret 预览
4. 同一份 seed 在另一端（本地补环境）重复一次，按 p 序列和 ret 摘要逐段对齐
5. 只看一段：instrumentation(action="log", seq_window=[590, 610])；
   某个状态里读了什么：instrumentation(action="log", stage=<p>)；
   某个状态的完整参数：stage_break(action="set", p=<p>) 后 instrumentation(action="reload")
6. 断网或需要反复跑：instrumentation(action="rewrite_source", source_path="sdk.js",
   output_path="sdk.tapped.js", tap_stage_calls=True)，在本地补环境里执行同样的对照
```

要点：`tap_stage_calls` 是**字面量匹配**，与 `mode`/`filter_*` 无关，文件解析失败时仍然生效
（此时 `instrumentation(action="status")` 的 `last_mode_used` 带 `(stage taps only)`，`last_error` 是解析失败原因）。
事件在状态返回时记录，嵌套的状态排在前面；抛异常的状态不记录。

### 场景 3：验证协议代码

```
1. launch_browser() → navigate("https://target.com")
2. network_capture(action="start")
3. # 触发目标操作，收集带签名的请求
4. reqs = list_network_requests(url_filter="api/search")
5. # 提取样本
6. verify_signer_offline(
     signer_code="(s) => ({'X-Bogus': mySign(s.url)})",
     samples=[{"id": "r1", "input": {...}, "expected": {"X-Bogus": "..."}}]
   )
```

> 👉 完整的反爬类型识别与工作流见 [JSVMP_PLAYBOOK.md](JSVMP_PLAYBOOK.md)

### 场景 4：引擎层追踪 JSVMP 环境指纹

> 需要 [camoufox-reverse 定制版浏览器](https://github.com/WhiteNightShadow/camoufox-reverse/releases)

```
1. launch_browser(
     browser_version="whitenightshadow/152.0.4-beta.30-reverse.5",
     enable_trace=True
   )                                           ← 显式启动定制版
2. trace_property_access(action="start")       ← 清空旧窗口并立即返回
3. navigate("https://www.douyin.com/video/xxx") ← 在新窗口内触发 JSVMP
4. # 继续执行 click/evaluate 等目标操作
5. trace_property_access(action="stop", mode="summary", collect_values=True)
   → 返回覆盖范围内命中的属性、频次、get/set/call、进程与 native site
   → snapshot_values 是操作后的安全快照；Cookie、Canvas、WebGL、Audio 等
     敏感或有副作用的路径会进入 values_skipped

# 按时间线查看属性访问节奏
6. trace_property_access(action="query", mode="timeline", bucket_ms=500)

# 按对象过滤
7. trace_property_access(action="query", filter_object="webgl")

# 搜索特定属性
8. trace_property_access(action="query", mode="search", search_query="cookie")
```

**与 compare_env 的区别**：
- `trace_property_access`：对当前构建声明的固定 Gecko 原生注入点提供强证据；
  reverse.5 为 77 点，未命中不能证明
  覆盖范围外的属性未被读取，也可能有高负载时间侧信道
- `compare_env`：采集一组 JS 层环境基线；`output_path` 导出完整环境包（navigator 全部 getter、
  CSS 属性和原型名、canvas、WebGL 等），可通过 `properties` 或分批 `evaluate_js` 扩展，不能视为全量枚举
- 路径 B 环境伪装时，用 trace 命中确定优先调查/补齐对象，再结合
  `compare_env` 与动态验证确认完整范围，避免把覆盖集之外的未命中误作否定证据

## 技术架构

```
┌─────────────────────────────────────────────────┐
│           AI 编码助手 (Cursor / Claude)          │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│              camoufox-reverse-mcp                │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Navigation│ Script   │Debugging │ Hooking  │  │
│  │          │ Analysis │ / RDP    │          │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Network  │ JSVMP    │  Cookie  │  Verify  │  │
│  │ Capture  │ Analysis │ Storage  │  Signer  │  │
│  ├──────────┴──────────┴──────────┴──────────┤  │
│  │ ★ PropertyTracer (trace_property_access)  │  │
│  │   Gecko 原生固定点追踪（不改写页面 JS）       │  │
│  └───────────────────────────────────────────┘  │
│       ↕ Playwright API      ↕ Firefox Native RDP │
├─────────────────────────────────────────────────┤
│      Camoufox (反指纹 Firefox, Juggler / RDP)    │
│  C++ 引擎级指纹伪造 · BrowserForge 真实指纹分布     │
└─────────────────────────────────────────────────┘
```

## 为什么选择 Camoufox？

| 能力 | 实现与边界 |
|---|---|
| 浏览器 | Firefox / Camoufox |
| 运行时分析 | 显式执行世界、Frame、Hook 与网络捕获 |
| JSVMP 调查 | 运行时探针与源码插桩，按具体目标验证 |
| 持久 Hook | Context 初始化，明确 pending 与卸载边界 |

**核心优势：**
- Camoufox 在 **C++ 层面** 修改指纹信息，不依赖页面 JS patch，避免常见的 descriptor/prototype 泄露
- Juggler 隔离减少部分页面侧自动化痕迹；Hook、环境差异与时序仍可能被观察
- BrowserForge 按 **真实世界流量统计分布** 生成指纹，不是随机拼凑
- 可用于反爬与环境依赖分析；具体站点的可用性必须通过实际样本验证
- Hook 可选 `Object.defineProperty` 防覆盖保护；锁定属性可能需要重建 Context 才能恢复
