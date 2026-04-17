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
| JSVMP 分析 | 无 | **解释器插桩 + 属性追踪 + 字符串提取** |
| Hook 持久化 | 不支持 | **context 级持久化，导航后自动重注入** |

**核心优势：**
- Camoufox 在 **C++ 层面** 修改指纹信息，非 JS 层 patch，从根源不可检测
- Juggler 协议沙箱隔离使 Playwright **完全不可被页面 JS 检测到**
- BrowserForge 按 **真实世界流量统计分布** 生成指纹，不是随机拼凑
- 能在瑞数、极验、Cloudflare 等强反爬站点上正常工作
- Hook 使用 `Object.defineProperty` **防覆盖保护**，页面脚本无法恢复原始方法

---

## 快速开始

### 方式一：AI 对话框直接安装（推荐）

在你的 AI 编码工具（Cursor / Claude Code / Codex 等）的对话框中输入：

```
请帮我配置camoufox-reverse-mcp并在后续触发相关操作的时候查阅该mcp：https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

AI 会自动完成克隆、安装依赖、配置 MCP Server 的全部流程。

### 方式二：手动安装

**1. 克隆项目**

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
```

**2. 安装依赖**

```bash
pip install -e .
```

或使用 uv：

```bash
uv pip install -e .
```

**3. 配置到你的 AI 工具**

根据你使用的工具，将 MCP Server 配置添加到对应的配置文件中（见下方「客户端配置」章节）。

---

## 使用方法

### 作为 MCP Server 启动

```bash
python -m camoufox_reverse_mcp
```

带参数启动：

```bash
python -m camoufox_reverse_mcp \
  --proxy http://127.0.0.1:7890 \
  --geoip \
  --humanize \
  --os windows
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|-----|------|-------|
| `--proxy` | 代理服务器地址 | 无 |
| `--headless` | 无头模式 | false |
| `--os` | 操作系统伪装（windows/macos/linux） | windows |
| `--geoip` | 根据代理 IP 自动推断地理位置 | false |
| `--humanize` | 人类化鼠标移动 | false |
| `--block-images` | 屏蔽图片加载 | false |
| `--block-webrtc` | 屏蔽 WebRTC | false |

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

## 可用工具一览（65 个）

### 导航 & 页面
| 工具 | 说明 |
|------|------|
| `launch_browser` | 启动 Camoufox 反指纹浏览器（已启动时返回完整会话状态） |
| `close_browser` | 关闭浏览器，释放资源 |
| `navigate` | 导航到指定 URL |
| `reload` / `go_back` | 刷新页面 / 浏览器后退 |
| `take_screenshot` | 截图（支持全页面、指定元素） |
| `take_snapshot` | 获取页面无障碍树（兼容新版 Playwright） |
| `click` / `type_text` | 点击元素 / 输入文本 |
| `wait_for` | 等待元素出现或 URL 匹配 |
| `get_page_info` | 获取当前页面 URL、标题、视口尺寸 |
| `get_page_content` | **[新]** 一键导出渲染后 HTML + title + meta + 可见文本 |
| `get_session_info` | **[新]** 查看当前会话状态：浏览器/上下文/页面/抓包/Hook |

### JS 脚本分析（逆向核心）
| 工具 | 说明 |
|------|------|
| `list_scripts` | 列出页面所有已加载的 JS 脚本 |
| `get_script_source` | 获取指定 JS 文件的完整源码 |
| `search_code` | 在所有已加载脚本中搜索关键词（返回匹配数量和脚本列表，不再截断） |
| `search_code_in_script` | **[新]** 在指定脚本中搜索关键词（更精准，避免全量扫描） |
| `save_script` | 将 JS 文件保存到本地 |
| `get_page_html` | 获取完整页面 HTML 或指定元素 |

### 断点调试（逆向核心）
| 工具 | 说明 |
|------|------|
| `evaluate_js` | 在页面上下文执行任意 JS 表达式 |
| `evaluate_js_handle` | 执行 JS 并检查复杂对象属性 |
| `add_init_script` | 注入在页面 JS 之前执行的脚本（**支持 persistent 持久化**） |
| `freeze_prototype` | **[新]** 冻结原型方法，防止页面脚本覆盖 Hook |
| `set_breakpoint_via_hook` | 通过 Hook 设置伪断点（**支持 persistent 持久化**） |
| `get_breakpoint_data` | 获取伪断点捕获的数据 |
| `get_console_logs` | 获取页面 console 输出 |

### Hook & 追踪（逆向核心）
| 工具 | 说明 |
|------|------|
| `trace_function` | 追踪函数调用（**支持 persistent 持久化，跨导航数据不丢失**） |
| `get_trace_data` | 获取追踪数据（**合并页面内数据和持久化数据**） |
| `hook_function` | 注入自定义 Hook（before/after/replace，**支持 non_overridable 防覆盖**） |
| `inject_hook_preset` | 一键注入预置 Hook（xhr/fetch/crypto/websocket/debugger_bypass/cookie/runtime_probe，**默认 persistent=True 持久化**） |
| `trace_property_access` | **[新]** 追踪属性访问（Proxy 级别），揭示 JSVMP 读取的环境信息 |
| `get_property_access_log` | **[新]** 获取属性访问记录 |
| `remove_hooks` | 移除所有 Hook（**可选保留持久化 Hook**） |

### 网络分析（逆向核心）
| 工具 | 说明 |
|------|------|
| `start_network_capture` | 开始捕获网络请求（**支持 capture_body=True 捕获响应体**） |
| `stop_network_capture` | 停止捕获 |
| `list_network_requests` | 列出已捕获的请求（支持多维过滤） |
| `get_network_request` | 获取指定请求的完整详情（**支持 include_headers=False 省 token**） |
| `get_request_initiator` | 获取请求发起的 JS 调用栈 |
| `intercept_request` | 拦截请求：记录 / 阻断 / 修改 / 模拟响应 |
| `stop_intercept` | 停止拦截 |
| `search_response_body` | **[新]** 在所有已捕获响应体中全文搜索关键词 |
| `get_response_body_page` | **[新]** 分页读取大响应体（避免截断） |
| `search_json_path` | **[新]** 按 JSON 路径提取响应数据（支持 `data[*].id` 通配） |

### JSVMP 逆向分析（新增模块）

> **反爬类型 → 工具路径对照表**
>
> 不同类型的反爬要用不同的工具，用错会导致挑战永远过不去。先识别类型再选工具。
>
> | 反爬类型 | 代表 | ✅ 推荐路径 | ❌ 禁用 |
> |---|---|---|---|
> | **签名型**（环境即签名） | 瑞数 5/6、Akamai sensor_data v3+、Shape Security | `instrument_jsvmp_source(mode="ast")` + `analyze_cookie_sources()` | `pre_inject_hooks`、`hook_jsvmp_interpreter(mode="proxy")` |
> | **行为型**（参数签名） | TikTok webmssdk、极验 gt4 | `hook_jsvmp_interpreter(mode="proxy")` 全量开 | — |
> | **纯混淆** | obfuscator.io、自研 VMP 无反指纹 | 任意组合 | — |
>
> **识别方法**：先 `navigate()`（不加 pre_inject），看 `redirect_chain`。出现重复 412 或 302 循环 → 签名型，走源码插桩。
| 工具 | 说明 |
|------|------|
| `hook_jsvmp_interpreter` | **[增强]** 通用 JSVMP 运行时探针：覆盖 apply/call/bind + Reflect.* + Proxy 属性追踪 |
| `get_jsvmp_log` | 获取 JSVMP 执行日志（含 API 调用统计和属性读取摘要） |
| `dump_jsvmp_strings` | **[修复]** 提取 JSVMP 字符串表：手动括号匹配替代正则，不再死循环 |
| `compare_env` | 浏览器环境指纹收集：用于与 Node.js/jsdom 环境对比 |
| `find_dispatch_loops` | **[新]** 扫描脚本定位字节码分发函数（while+switch） |

### JSVMP 源码级插桩（通用 VMP 利器）
| 工具 | 说明 |
|------|------|
| `instrument_jsvmp_source` | **[新]** 在 JS 下载后、执行前改写源码，对每个 obj[key] / fn(args) 插入 tap，捕获字节码分发循环的每次外部交互。**通用方案**，对瑞数 5/6、akamai sensor_data、webmssdk、obfuscator.io 都有效 |
| `get_instrumentation_log` | **[新]** 获取源码级插桩日志，带 hot_keys / hot_methods / hot_functions 摘要 |
| `get_instrumentation_status` | **[新]** 查看当前激活的源码级插桩 |
| `stop_instrumentation` | **[新]** 停止一个或全部源码插桩 |

### Cookie 归因分析
| 工具 | 说明 |
|------|------|
| `analyze_cookie_sources` | **[新]** 归因每个 Cookie 来源：HTTP Set-Cookie / JS document.cookie，解决"为什么我 hook 不到 cookie 写入"疑惑 |

### 导航增强
| 工具 | 说明 |
|------|------|
| `navigate` | **[增强]** 支持 pre_inject_hooks 在页面 JS 前装 hook、返回 initial_status + final_status + redirect_chain |
| `reload_with_hooks` | **[新]** 重载页面让 persistent hooks 在页面 JS 前执行（清空日志） |
| `get_runtime_probe_log` | **[新]** 获取 runtime_probe.js 捕获的广谱运行时事件 |

### 存储管理
| 工具 | 说明 |
|------|------|
| `get_cookies` / `set_cookies` / `delete_cookies` | Cookie 管理 |
| `get_storage` / `set_storage` | localStorage / sessionStorage 读写 |
| `export_state` / `import_state` | 导出 / 导入完整浏览器状态 |

### 指纹 & 反检测
| 工具 | 说明 |
|------|------|
| `get_fingerprint_info` | 查看当前浏览器指纹详情 |
| `check_detection` | 在 bot 检测站点测试反检测效果并截图 |
| `bypass_debugger_trap` | 一键绕过反调试陷阱 |

---

## 使用场景示例

### 场景 1：逆向登录接口的签名参数

```
AI 操作链：
1. launch_browser(headless=False, os_type="windows")
2. inject_hook_preset("xhr")          ← 注入 XHR Hook（默认持久化）
3. inject_hook_preset("crypto")       ← 注入加密函数 Hook
4. navigate("https://example.com/login")
5. type_text("#username", "test_user")
6. type_text("#password", "test_pass")
7. click("#login-btn")
8. list_network_requests(method="POST") ← 看到带加密参数的请求
9. get_network_request(request_id=3)    ← 查看完整参数
10. get_request_initiator(request_id=3) ← 发现签名函数在 main.js:1234
11. get_script_source("https://example.com/js/main.js")
12. search_code("sign")                 ← 搜索签名相关代码
13. hook_function("window.getSign", ...)
14. 刷新 → get_trace_data("window.getSign")
15. 输出完整签名算法还原结果
```

### 场景 2：对付 JSVMP 保护的站点

```
AI 操作链：
1. launch_browser(headless=False)
2. bypass_debugger_trap()                ← 先绕过反调试
3. inject_hook_preset("xhr")             ← 持久化 Hook
4. inject_hook_preset("fetch")           ← 持久化 Hook
5. hook_jsvmp_interpreter("webmssdk.es5.js")  ← JSVMP 插桩
6. trace_property_access(["navigator.*", "screen.*", "document.cookie"])
7. navigate("https://target.com")
8. 触发目标操作（翻页、搜索等）
9. get_jsvmp_log()                       ← 查看 JSVMP 访问了哪些 API
10. get_property_access_log()            ← 查看读取了哪些环境属性
11. dump_jsvmp_strings("webmssdk.es5.js") ← 提取字符串表
12. compare_env()                        ← 收集浏览器环境，与 Node.js 对比
13. 根据 API 调用和属性访问记录还原算法逻辑
```

### 场景 3：验证反检测效果

```
AI 操作链：
1. launch_browser(os_type="windows", humanize=True)
2. check_detection()                     ← 打开 bot.sannysoft.com 并截图
3. get_fingerprint_info()                ← 查看详细指纹信息
4. navigate("https://browserscan.net")   ← 测试更多检测站点
5. take_screenshot(full_page=True)
```

### 场景 4：持久化 Hook 工作流

```
AI 操作链：
1. launch_browser()
2. inject_hook_preset("xhr", persistent=True)    ← context 级持久化
3. inject_hook_preset("fetch", persistent=True)
4. trace_function("XMLHttpRequest.prototype.open", persistent=True)  ← 持久化追踪
5. navigate("https://page1.com")                 ← Hook 自动生效
6. get_trace_data()                              ← 收集数据
7. navigate("https://page2.com")                 ← Hook 自动重注入！
8. get_trace_data()                              ← 数据包含两个页面的记录
9. freeze_prototype("XMLHttpRequest", "open")    ← 防止页面覆盖
```

### 场景 5：大响应体数据定位 + 渲染态 DOM 导出

```
AI 操作链：
1. launch_browser()
2. start_network_capture(capture_body=True)       ← 开启响应体捕获
3. navigate("https://example.com/data")
4. get_session_info()                             ← 确认当前会话状态和抓包情况
5. list_network_requests(resource_type="xhr")     ← 找到目标接口
6. search_response_body("token")                  ← 在所有响应体中搜索关键词
7. search_json_path(request_id=5, json_path="data.list[*].sign")  ← 精准提取 JSON 数据
8. get_response_body_page(request_id=5, offset=0, length=10000)   ← 分页查看大 body
9. get_page_content()                             ← 一键导出渲染后 HTML + 可见文本
```

### 场景 6：通用 JSVMP 逆向（瑞数6 / Akamai / 自研 VMP）

这是最推荐的 JSVMP 分析流程，不依赖 VMP 实现细节，对几乎所有类型有效。

```
AI 操作链：
1. launch_browser(headless=False)
2. start_network_capture(capture_body=True)
3. # 第一次导航用来定位 VMP 脚本 URL
   navigate("https://target.com/")
4. list_network_requests(resource_type="script")
5. # 找到可疑的大型 JS（通常 100KB+），如 sdenv-*.js / FuckCookie_*.js
6. find_dispatch_loops(script_url="https://target.com/sdenv-xxx.js")
   # 确认是 VMP（case_count 通常 >50）
7. # 装源码级插桩
   instrument_jsvmp_source("**/sdenv-*.js", mode="ast", tag="vmp1")
8. # 同时装其他兜底 hook
   inject_hook_preset("cookie", persistent=True)
   inject_hook_preset("xhr", persistent=True)
9. # 重新跑一次，让插桩生效
   reload_with_hooks()
10. # 看 VMP 访问了哪些环境信息
    get_instrumentation_log(tag_filter="vmp1", type_filter="tap_get", limit=100)
    # hot_keys 会揭示 userAgent / webdriver / plugins 等访问频次
11. # 看 VMP 调用了哪些 API
    get_instrumentation_log(tag_filter="vmp1", type_filter="tap_method")
12. # 归因 Cookie 来源
    analyze_cookie_sources()
13. # 根据插桩数据在 Node.js / jsdom 侧补齐环境差异，跑通算法
```

> 👉 完整的反爬类型识别与工作流见 [docs/JSVMP_PLAYBOOK.md](docs/JSVMP_PLAYBOOK.md)

---

## 技术架构

```
┌─────────────────────────────────────────────────┐
│           AI 编码助手 (Cursor / Claude)          │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│              camoufox-reverse-mcp               │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Navigation│ Script   │Debugging │ Hooking  │  │
│  │          │ Analysis │          │          │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Network  │ JSVMP    │Fingerprint│  Utils  │  │
│  │          │ Analysis │          │          │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Storage  │ Persistent Scripts  │          │  │
│  └──────────┴──────────┴──────────┴──────────┘  │
│                    ↕ Playwright API               │
├─────────────────────────────────────────────────┤
│      Camoufox (反指纹 Firefox, Juggler 协议)      │
│  C++ 引擎级指纹伪造 · BrowserForge 真实指纹分布     │
└─────────────────────────────────────────────────┘
```

## 更新记录

### v0.5.0（2026-04-18）— 签名型反爬兼容改造

> 解决 `pre_inject_hooks` 对瑞数/Akamai 签名型反爬不可用的架构性问题。新增 MCP 侧 AST 改写、transparent 观察模式、反爬类型决策表和实战 Playbook。

**架构性改进**
- **`instrument_jsvmp_source` 默认改为 MCP 侧 esprima AST 改写**：不再依赖页面内 CDN（挑战页加载不到），AST 失败自动回落 regex
- **`hook_jsvmp_interpreter` 新增 `mode="transparent"`**：只替换原型 getter，不装 Proxy、不动 Function.prototype，签名型反爬可用
- **反爬类型决策表**：签名型/行为型/纯混淆各自推荐工具路径，避免用错工具导致挑战永远过不去
- **JSVMP Playbook**：按反爬类型给出完整工作流（`docs/JSVMP_PLAYBOOK.md`）

**新增文件**
- `hooks/jsvmp_transparent_hook.js` — 签名安全的运行时观察器
- `utils/ast_rewriter.py` — MCP 侧 esprima AST 改写器
- `docs/JSVMP_PLAYBOOK.md` — 反爬类型识别与工作流指南
- `tests/test_ast_rewriter.py` — AST 改写器单元测试

**文档诚实度修正**
- `hook_jsvmp_interpreter` docstring 加 LIMITATIONS 段，明确签名型反爬不可用
- `navigate` 的 `pre_inject_hooks` 参数加 WARNING，说明症状和替代方案
- `instrument_jsvmp_source` docstring 标注为签名型反爬首选
- README 工具列表前插入反爬类型→工具路径对照表

**新增依赖**
- `esprima>=4.0.1`（纯 Python，零 C 扩展）

### v0.4.0（2026-04-17）— 通用 JSVMP 适配改造

> 让本 MCP 成为通用 JSVMP 逆向武器。新增源码级插桩、Cookie 归因、运行时探针等核心能力，修复 jsvmp_hook 多路径覆盖和 dump_jsvmp_strings 正则问题。工具总数从 57 个增长至 65 个。

**新增工具（8 个）**
| 工具 | 说明 |
|------|------|
| `instrument_jsvmp_source` | 源码级插桩：在 JS 下载后执行前改写源码，对每个 obj[key] / fn(args) 插入 tap |
| `get_instrumentation_log` | 获取源码级插桩日志，带 hot_keys / hot_methods / hot_functions 摘要 |
| `get_instrumentation_status` | 查看当前激活的源码级插桩 |
| `stop_instrumentation` | 停止一个或全部源码插桩 |
| `find_dispatch_loops` | 扫描脚本定位字节码分发函数（while+switch） |
| `reload_with_hooks` | 重载页面让 persistent hooks 在页面 JS 前执行 |
| `analyze_cookie_sources` | 归因每个 Cookie 来源：HTTP Set-Cookie / JS document.cookie |
| `get_runtime_probe_log` | 获取 runtime_probe.js 捕获的广谱运行时事件 |

**重大改进**
- **hook_jsvmp_interpreter 重写**：多路径覆盖 apply/call/bind + Reflect.apply/get/set/construct + Proxy 属性追踪 + 计时/随机 API，对瑞数 6、Akamai、webmssdk 等 VMP 有效
- **navigate 增强**：支持 `pre_inject_hooks` 在页面 JS 前装 hook、返回 `initial_status` + `final_status` + `redirect_chain`，解决 412 挑战页 status 歧义
- **dump_jsvmp_strings 修复**：用手动括号匹配替代嵌套正则，不再死循环或漏匹配
- **新增 cookie_hook.js**：原型链级 document.cookie hook，正确处理 Document.prototype 上的 descriptor
- **新增 runtime_probe.js**：低开销广谱运行时观察器，覆盖 XHR/fetch/canvas/WebGL/navigator/addEventListener
- **inject_hook_preset 新增预设**：`cookie`、`runtime_probe`

### v0.3.0（2026-04-03）— 稳定性修复 + 响应体检索 + DOM 导出 + 会话管理

> 修复实战中的稳定性问题，补全响应体检索、渲染态 DOM 导出、会话管理等缺失能力。工具总数从 52 个增长至 57 个。

**新增工具（5 个）**
| 工具 | 说明 |
|------|------|
| `search_response_body` | 在所有已捕获响应体中全文搜索关键词 |
| `get_response_body_page` | 分页读取大响应体，避免截断丢失数据 |
| `search_json_path` | 按 JSON 路径提取响应数据（支持 `[*]` 通配） |
| `get_page_content` | 一键导出渲染后 HTML + title + meta + 可见文本 |
| `get_session_info` | 查看当前会话状态：浏览器/上下文/页面/抓包/Hook |

**Bug 修复**
- **take_snapshot**：修复 `Page object has no attribute accessibility` 错误，兼容新版 Playwright（>= 1.42），无障碍 API 移除后自动 fallback 到 JS 实现
- **trace_property_access**：修复 `JSON.parse` 报错，原因是模板替换时把 JS 引号也替换掉了，导致 `JSON.parse(["..."])` 而非 `JSON.parse('[...]')`

**改进项**
- **launch_browser**：已启动时返回完整会话状态（页面 URL、上下文列表、抓包状态），不再只返回 `already_running`
- **get_network_request**：新增 `include_headers=False` 选项，省略 headers 节约 token
- **list_network_requests**：URL 截断到 200 字符，响应字段名缩短（`resource_type` → `type`，`duration` → `ms`）
- **工具描述优化**：梳理所有工具的描述文案，使参数说明和使用场景更清晰明了
- **大响应体可观测性**：`search_response_body` 支持按关键词搜索全部已捕获响应 body；`get_response_body_page` 支持分页读取；`search_json_path` 支持按路径直接提取 JSON 数据

### v0.2.0（2026-04-01）— Hook 持久化 + JSVMP 专项分析

> 一句话：解决 Hook 导航后失效的核心痛点，新增 JSVMP 解释器插桩 / 属性追踪 / 字符串提取等专项逆向工具，工具总数从 44 个增长至 52 个。

**新增工具（8 个）**
| 工具 | 说明 |
|------|------|
| `freeze_prototype` | 冻结原型方法，防止页面脚本覆盖 Hook |
| `search_code_in_script` | 在指定脚本中搜索关键词（精准 + 更多上下文） |
| `trace_property_access` | Proxy 级属性访问追踪，揭示 JSVMP 读取的环境信息 |
| `get_property_access_log` | 获取属性访问记录 |
| `hook_jsvmp_interpreter` | JSVMP 解释器插桩：追踪 API 调用和敏感属性读取 |
| `get_jsvmp_log` | 获取 JSVMP 执行日志（含调用统计与属性摘要） |
| `dump_jsvmp_strings` | 提取 JSVMP 字符串表，解密混淆字符串，发现 API 名称 |
| `compare_env` | 收集浏览器环境指纹，用于与 Node.js/jsdom 对比 |

**改进项**
- **Hook 持久化**：`inject_hook_preset` 默认 `persistent=True`，context 级注入，导航后自动重注入
- **Hook 防覆盖**：XHR/Fetch Hook 使用 `Object.defineProperty(configurable: false)` + `toString` 伪装
- **trace_function 持久化**：新增 `persistent=True`，通过 console 事件收集数据到 Python 端，导航不丢失
- **get_request_initiator 修复**：改进 URL 匹配（pathname 级别）+ 添加诊断信息
- **search_code 修复截断**：返回 `total_matches` 和 `scripts_with_matches`，结果不再静默 omit
- **网络捕获响应体**：`start_network_capture(capture_body=True)` 支持捕获响应体
- **缓冲区扩容**：日志和网络请求缓冲区从 500 增大到 2000
- **请求 ID 稳定**：使用全局递增计数器，不再因 deque 弹出导致 ID 重复

### v0.1.0（2026-03-31）— 初始版本

> 一句话：基于 Camoufox 反指纹浏览器的 MCP Server，44 个工具覆盖 JS 逆向分析全链路。

- 浏览器控制：启动 / 导航 / 截图 / 交互（11 个工具）
- 脚本分析：列出 / 获取 / 搜索 / 保存脚本（5 个工具）
- 调试：JS 执行 / init_script / 伪断点 / 控制台（6 个工具）
- Hook：函数追踪 / 自定义 Hook / 预设 Hook（5 个工具）
- 网络：捕获 / 过滤 / 详情 / 调用栈 / 拦截（7 个工具）
- 存储：Cookie / Storage / 状态导入导出（7 个工具）
- 指纹：指纹检查 / 检测测试 / 反调试绕过（3 个工具）

## 反馈 / 交流

使用过程中遇到 bug、想要新的 Hook 预设、或者想交流 JS 逆向思路，欢迎加微信：

- **微信号**：`han8888v8888`

> 加好友时烦请备注「camoufox-reverse」，方便快速通过。

## 许可证

MIT
