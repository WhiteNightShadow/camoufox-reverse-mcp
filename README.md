# camoufox-reverse-mcp

[中文](README.md) | [English](README_en.md)

> 基于反指纹浏览器的 MCP Server，专为 JavaScript 逆向工程设计。

一个 MCP（Model Context Protocol）服务器，让 AI 编码助手（Claude Code、Cursor、Cline 等）能够通过 **Camoufox** 反指纹浏览器对目标网站进行：接口参数分析、JS 文件静态分析、动态断点调试、函数 Hook 追踪、网络流量拦截、JSVMP 字节码分析、Cookie/存储管理等逆向操作。

## 为什么选择 Camoufox？

| 特性 | chrome-devtools-mcp | **camoufox-reverse-mcp** |
|-----|--------------------|-----------------------|
| 浏览器内核 | Chrome (Puppeteer) | **Firefox (Camoufox)** |
| 反检测方案 | 无 | **C++ 引擎级指纹伪造** |
| 调试能力 | 有限（无断点） | **Playwright + JS Hook** |
| JSVMP 分析 | 无 | **解释器插桩 + 源码级改写** |
| Hook 持久化 | 不支持 | **context 级持久化，导航后自动重注入** |

**核心优势：**
- Camoufox 在 **C++ 层面** 修改指纹信息，不依赖页面 JS patch，避免常见的 descriptor/prototype 泄露
- Juggler 协议沙箱隔离使 Playwright **完全不可被页面 JS 检测到**
- BrowserForge 按 **真实世界流量统计分布** 生成指纹，不是随机拼凑
- 能在 RS、AK、JY、CF 等各类强反爬站点上正常工作
- Hook 使用 `Object.defineProperty` **防覆盖保护**，页面脚本无法恢复原始方法

---

## 快速开始

### 方式一：AI 对话框直接安装（推荐）

在你的 AI 编码工具（Cursor / Claude Code / Codex 等）的对话框中输入：

```
帮我安装下这个mcp工具：camoufox-reverse-mcp
项目地址：https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

AI 会自动完成克隆、安装依赖、配置 MCP Server 的全部流程。

### 方式二：手动安装

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
pip install -e .
```

> v1.1.0 将 MCP Python SDK 固定在兼容的 v1 系列，并自动规范化可选参数
> schema，可兼容 Moonshot/Kimi 等要求每个工具参数都包含 `type` 的严格服务。

### 客户端配置

<details>
<summary><b>Cursor（.cursor/mcp.json）</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": ["-m", "camoufox_reverse_mcp"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": ["-m", "camoufox_reverse_mcp", "--headless"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code（带代理）</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": [
        "-m", "camoufox_reverse_mcp",
        "--proxy", "http://127.0.0.1:7890",
        "--geoip",
        "--humanize"
      ]
    }
  }
}
```

</details>

---

## 可用工具一览

### 浏览器控制
| 工具 | 说明 |
|------|------|
| `launch_browser` | 启动 Camoufox；可用 `browser_version` 单次选择已安装版本，不修改 active 配置 |
| `close_browser` | 关闭浏览器，释放资源 |
| `navigate` | 导航到指定 URL（支持 pre_inject_hooks、redirect_chain 追踪） |
| `reload` | 刷新页面 |
| `take_screenshot` | 截图（支持全页面、指定元素） |
| `take_snapshot` | 获取页面无障碍树（token 高效） |
| `click` / `type_text` | 点击元素 / 输入文本 |
| `wait_for` | 等待元素出现或 URL 匹配 |
| `get_page_info` | 获取当前页面 URL、标题、视口尺寸 |

### JS 执行与调试
| 工具 | 说明 |
|------|------|
| `evaluate_js` | 在页面上下文执行任意 JS 表达式（多策略 JSON 解析） |

### 脚本分析
| 工具 | 说明 |
|------|------|
| `scripts(action)` | 脚本管理：`list` 列出 / `get` 获取源码 / `save` 保存到本地 |
| `search_code` | 搜索关键词（`script_url=None` 全量搜索，指定 URL 则单脚本搜索，自动检测压缩文件用字符级上下文） |

### Hook 与追踪
| 工具 | 说明 |
|------|------|
| `hook_function` | Hook 或追踪函数：`mode="intercept"` 注入代码 / `mode="trace"` 非侵入式追踪 |
| `inject_hook_preset` | 一键注入预置 Hook（xhr / fetch / crypto / websocket / debugger_bypass / cookie / runtime_probe） |
| `remove_hooks` | 移除所有 Hook 并恢复原始对象 |
| `get_console_logs` | 获取页面 console 输出 |

### 网络分析
| 工具 | 说明 |
|------|------|
| `network_capture(action)` | 网络捕获控制：`start` / `stop` / `clear` / `status` |
| `list_network_requests` | 列出已捕获的请求（支持 URL / 域名 / 方法 / 类型 / 状态码过滤） |
| `get_network_request` | 获取请求完整详情（`max_body_size` 控制 body 截断） |
| `get_request_initiator` | 获取请求发起的 JS 调用栈 |
| `intercept_request` | 拦截请求：log / block / modify / mock / stop |

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
| `hook_jsvmp_interpreter` | JSVMP 运行时探针（`mode="proxy"` 全覆盖 / `mode="transparent"` 签名安全） |
| `instrumentation(action)` | 源码级插桩：`install` 注册改写 / `log` 获取日志 / `stop` 停止 / `reload` 重载 / `status` 查看状态 |
| `compare_env` | 浏览器环境指纹收集，用于与 Node.js/jsdom 对比 |

需要对齐浏览器与沙箱中的同一执行点时，可在安装时设置
`include_source_site=True`。事件会增加稳定 `site_id` 与单调 `seq`，`log`
返回 `source_sites` sidecar，映射到拦截脚本的原始字符区间。该能力默认关闭；
它表示混淆后 JS 的源码位置，不会猜测 VM 的 PC、opcode 或保护前源码位置。
对超过 200KB 且需要全量改写的脚本，需显式设置 `on_oversized="force"`；
否则应配合属性过滤并按需关闭 `rewrite_calls` 控制开销。

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

### 引擎层属性追踪（v1.1.0 新增）

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

---

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
9. hook_function("window.getSign", mode="trace")
10. reload() → get_console_logs()          ← 收集追踪数据
```

### 场景 2：通用 JSVMP 逆向（RS / AK / 自研 VMP）

```
1. launch_browser()
2. network_capture(action="start")
3. navigate("https://target-site.com/")
4. list_network_requests(resource_type="script")  ← 找到 VMP 脚本
5. instrumentation(action="install", url_pattern="**/vmp_target*.js", mode="ast")
6. inject_hook_preset("cookie", persistent=True)
7. instrumentation(action="reload")               ← 让插桩生效
8. instrumentation(action="log", type_filter="tap_get")  ← 看 VMP 读了什么环境
9. instrumentation(action="log", type_filter="tap_method") ← 看 VMP 调了什么 API
10. compare_env()                                  ← 收集环境用于 Node.js 补齐
```

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

> 👉 完整的反爬类型识别与工作流见 [docs/JSVMP_PLAYBOOK.md](docs/JSVMP_PLAYBOOK.md)

### 场景 4：引擎层追踪 JSVMP 环境指纹（v1.1.0 新增）

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
- `compare_env`：采集一组 JS 层环境基线；可通过 `properties` 或分批
  `evaluate_js` 扩展，不能视为全量枚举
- 路径 B 环境伪装时，用 trace 命中确定优先调查/补齐对象，再结合
  `compare_env` 与动态验证确认完整范围，避免把覆盖集之外的未命中误作否定证据

---

## 技术架构

```
┌─────────────────────────────────────────────────┐
│           AI 编码助手 (Cursor / Claude)          │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│              camoufox-reverse-mcp                │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Navigation│ Script   │Debugging │ Hooking  │  │
│  │          │ Analysis │          │          │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Network  │ JSVMP    │  Cookie  │  Verify  │  │
│  │ Capture  │ Analysis │ Storage  │  Signer  │  │
│  ├──────────┴──────────┴──────────┴──────────┤  │
│  │ ★ PropertyTracer (trace_property_access)  │  │
│  │   Gecko 原生固定点追踪（不改写页面 JS）       │  │
│  └───────────────────────────────────────────┘  │
│                    ↕ Playwright API               │
├─────────────────────────────────────────────────┤
│      Camoufox (反指纹 Firefox, Juggler 协议)      │
│  C++ 引擎级指纹伪造 · BrowserForge 真实指纹分布     │
└─────────────────────────────────────────────────┘
```

---

## 更新记录

### v1.4.1（2026-09-03）— Firefox 152 LocalStorage 追踪路径对齐

- 配套 `camoufox-reverse` reverse.5，将既有 `localStorage.getItem/setItem`
  两点迁移到 Firefox 152 默认 LSNG `LSObject` 路径
- PropertyTracer 扩为 77 点、protocol 1 不变，事件 object/property/kind 与
  MCP 聚合行为不变；LSObject 与 partitioned 路径由 native site 区分
- MCP 解析器无需行为改动；默认 Trace-off、135/reverse.3/reverse.4 兼容路径不变

### v1.4.0（2026-09-03）— PropertyTracer 正确性、隔离与交互追踪

- 配套正式版 `camoufox-reverse` reverse.4：75 个原路径不变，正确区分 get/set/call，并解析 native site、进程序列和微秒时间扩展字段
- `trace_property_access` 新增兼容的 `action=start/stop/query/capture/clear/status`；可在 start 后继续执行页面操作，再 stop 聚合
- 每次浏览器启动分配独立 trace run，控制、清理和历史查询不再影响并发 MCP 实例
- 修复无浏览器时旧 control 文件被误报为 `installed/trace_active`；`check_environment` 现在拆分 installed、trace_capable、trace_active
- 新窗口严格执行 off → drain → cleanup → on，避免 Windows 开放文件混入旧事件
- 新增 kind/site 过滤、跨进程确定性排序、cap 提示及 capability 协商
- `collect_values` 明确为追踪后的安全快照；Cookie 与可能产生副作用的 API 默认跳过
- 可由 Camoufox 0.5 元数据识别的官方浏览器请求 `enable_trace=True` 时，不再注入无效配置或关闭 sandbox；普通启动保持不变
- Camoufox 0.4 的官方 135 与早期无 marker 的定制 135 无法在启动前区分；为兼容旧定制版，显式请求 Trace 时仍尝试旧握手，默认 `enable_trace=False` 不受影响

### v1.3.0（2026-09-02）— Camoufox 152 与无侵入多版本选择

- `launch_browser` 新增可选 `browser_version`，单次选择 Camoufox 0.5+ 已安装版本且不修改 active 配置
- 选择器强制使用 repo 限定并拒绝歧义；active/selected version+build 不一致时明确拒绝
- `check_environment` 新增 Camoufox Python、active、installed selectors、能力标记及旧缓存迁移风险诊断
- 修复主机为 `C.UTF-8` 时生成无效 `locale="C"`、导致浏览器无法启动的问题
- 默认参数保持不变；Camoufox 0.4.x + Firefox 135 用户无需迁移，官方浏览器仍可使用全部非 PropertyTracer 工具
- 配套可选的 [Camoufox Reverse 152 beta.30](https://github.com/WhiteNightShadow/camoufox-reverse/releases) 预发布构建
- 感谢 [@dsaw1111](https://github.com/dsaw1111) 报告定制版与官方版本差距

### v1.2.0（2026-08-11）— 通用源码执行点映射

- `instrumentation(action="install")` 新增默认关闭的 `include_source_site`
- AST 与 regex 插桩事件可携带内容寻址的稳定 `site_id` 和单调 `seq`
- `instrumentation(action="log")` 返回原始脚本 SHA-256、URL、字符区间与 AST 行列 sidecar，并补齐 `hot_functions`
- 默认 tap 事件字段保持不变；不执行用户提供的任意 AST 脚本，也不把单个 VM 的变量名猜成通用 PC/opcode
- 感谢 [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) 提出执行点关联需求并提供分析材料

### v1.1.2（2026-08-11）— Windows 引擎追踪配置修复

- 修复 Windows 上 Camoufox 将配置拆为 `CAMOU_CONFIG_1..n` 后，`enable_trace=True` 无法注入 `propertyTrace`、持续返回 `engine_trace_not_available` 的问题
- 完整重组原始 JSON 后再合并追踪配置，并按平台限制重新生成任意数量的连续分块；同时清理旧分块，保留全部原始指纹配置
- 对数字顺序、十块以上配置、Unicode、分块扩缩容、无效配置及调用真实 `camoufox.utils.get_env_vars()` 模拟 Windows 分块增加回归测试
- 针对本问题，已安装支持属性追踪的 `camoufox-reverse` 用户只需升级 MCP，无需重新编译或替换浏览器
- 感谢 [@Code-xy](https://github.com/Code-xy) 报告问题、完成 Windows 实机定位并提供修复思路

### v1.1.1（2026-07-29）— AST 链式调用插桩修复

- 修复 `new X().m1().m2()`、`Array.prototype.slice.call(arguments)` 等嵌套调用因父子 AST 编辑区间重叠而生成损坏代码的问题
- 仅在编辑区间重叠时保留外层插桩；普通成员访问和函数调用的现有改写行为保持不变
- `instrumentation(action="status")` 新增 `last_mode_used`，可区分 AST、regex 回退和超大文件跳过路径

### v1.1.0（2026-07-29）— 引擎追踪、浏览器接管与 Schema 兼容

> 稳定版：新增引擎层属性追踪和已运行浏览器接管，并提升严格 JSON Schema
> 服务及跨平台运行的兼容性。

**新增工具**
- `trace_property_access` — Gecko 原生 DOM/Web API 定点追踪，不改写页面 JS 对象；支持 summary/timeline/sequence/search 四种视图
- `list_trace_files` — 列出本地 trace 文件
- `query_trace_file` — 查询历史 trace 文件

**变更**
- `launch_browser` 新增 `enable_trace` 参数，启用后自动注入 `CAMOU_CONFIG` 和 `MOZ_DISABLE_CONTENT_SANDBOX`
- `launch_browser` 新增 `ws_endpoint`，可接管已运行的 Camoufox 浏览器
- `check_environment` 新增 `camoufox_reverse` 字段，检测定制版浏览器安装状态
- 自动规范化顶层可选参数 schema，兼容 Moonshot/Kimi 等严格校验服务（感谢 [@tuntun1337](https://github.com/tuntun1337) 的贡献）

**稳定性修复**
- 修复 Windows 环境下 Camoufox/Playwright 导入死锁
- 修复 Playwright Firefox driver 的 `pageError` 崩溃
- 修复插桩改写响应后因 `Content-Encoding` 导致的内容丢失

**依赖**
- MCP Python SDK 固定为 `mcp>=1.29,<2`；v2 迁移将在后续版本单独进行

- 需要 [camoufox-reverse](https://github.com/WhiteNightShadow/camoufox-reverse) 定制版浏览器（可选，不装不影响其他工具）

### v1.0.0（2026-04-18）— 工具精简 + 回归纯 JS 逆向工具集

> **重大版本**：80 → 32 工具，schema tokens 减半。移除 Session 档案/断言系统，回归纯 JS 逆向工具定位。

**工具合并（v0.9.0）**
- `network_capture(action=start/stop/clear/status)` ← start/stop_network_capture
- `scripts(action=list/get/save)` ← list_scripts / get_script_source / save_script
- `search_code(keyword, script_url=None)` ← search_code / search_code_in_script
- `hook_function(path, mode=intercept/trace)` ← hook_function / trace_function
- `instrumentation(action=install/log/stop/reload/status)` ← instrument_jsvmp_source / get_instrumentation_log / stop_instrumentation / reload_with_hooks / get_instrumentation_status
- `cookies(action=get/set/delete)` ← get_cookies / set_cookies / delete_cookies

**移除的工具**
- Session 档案系统（7 个）：start/stop_reverse_session、list_sessions、get_session_snapshot、attach_domain_readonly、export/import_session
- 断言系统（4 个）：add/verify/list/remove_assertion
- 冷工具（37 个）：trace_property_access、freeze_prototype、find_dispatch_loops、get_page_content、bypass_debugger_trap、check_detection、get_fingerprint_info、dump_jsvmp_strings、evaluate_js_handle、add_init_script、set_breakpoint_via_hook、get_breakpoint_data 等

**新增**
- `verify_signer_offline` — 无状态签名函数验证（替代 verify_against_session）

**Bug 修复（v0.8.1）**
- `evaluate_js`：多策略 JSON 解析（控制字符清理、双重编码解包）
- `navigate`：默认清理网络缓存，防止跨导航请求污染
- `get_network_request`：`max_body_size` 参数控制 body 截断（默认 5000）
- `launch_browser`：already_running 时返回残留状态诊断

**移除的依赖**：`tldextract`（仅 Session 使用）

**设计理念**：MCP 是纯工具集（stateless），不做工作流管理。分析项目的记忆/累积属于 skill 层和用户工作区。

### v0.6.0 — 实战 Bug 修复

- `hook_jsvmp_interpreter(mode="proxy")`：修复 Proxy 递归导致 `too much recursion`
- `remove_hooks`：真正恢复 Proxy 对象
- `evaluate_js`：BOM / lone surrogate / whitespace 自动清理
- `instrument_jsvmp_source`：CSP 预检
- `navigate`：超时优雅降级

### v0.5.0 — 签名型反爬兼容

- `instrument_jsvmp_source` 默认 MCP 侧 AST 改写
- `hook_jsvmp_interpreter` 新增 `mode="transparent"`
- 反爬类型决策表 + JSVMP Playbook

### v0.4.0 — 通用 JSVMP 适配

- 源码级插桩、Cookie 归因、运行时探针
- hook_jsvmp_interpreter 多路径覆盖重写

### v0.3.0 — 稳定性修复

### v0.2.0 — Hook 持久化 + JSVMP 分析

### v0.1.0 — 初始版本（44 工具）

---

## 社区贡献者

- [@tuntun1337](https://github.com/tuntun1337) — 严格 JSON Schema 兼容性
- [@Code-xy](https://github.com/Code-xy) — Windows 引擎追踪问题定位与验证
- [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) — JSVMP 执行点关联需求与分析材料
- [@dsaw1111](https://github.com/dsaw1111) — Camoufox 152 版本差距与 PropertyTracer 升级需求

## 反馈 / 交流

使用过程中遇到 bug、想要新的 Hook 预设、或者想交流 JS 逆向思路，欢迎加微信：

- **微信号**：`han8888v8888`

> 加好友时烦请备注「camoufox-reverse」，方便快速通过。

## 许可证

MIT
