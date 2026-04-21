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
- Camoufox 在 **C++ 层面** 修改指纹信息，非 JS 层 patch，从根源不可检测
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

## 可用工具一览（32 个）

### 浏览器控制
| 工具 | 说明 |
|------|------|
| `launch_browser` | 启动 Camoufox 反指纹浏览器 |
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
| `check_environment` | 一站式自检：MCP 版本、依赖、浏览器状态 |
| `reset_browser_state` | 清理残留（hooks / capture / routes），不关浏览器 |

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

---

## 技术架构

```
┌─────────────────────────────────────────────────┐
│           AI 编码助手 (Cursor / Claude)          │
│                    ↕ MCP (stdio)                 │
├─────────────────────────────────────────────────┤
│           camoufox-reverse-mcp (32 tools)        │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Navigation│ Script   │Debugging │ Hooking  │  │
│  │          │ Analysis │          │          │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Network  │ JSVMP    │  Cookie  │  Verify  │  │
│  │ Capture  │ Analysis │ Storage  │  Signer  │  │
│  └──────────┴──────────┴──────────┴──────────┘  │
│                    ↕ Playwright API               │
├─────────────────────────────────────────────────┤
│      Camoufox (反指纹 Firefox, Juggler 协议)      │
│  C++ 引擎级指纹伪造 · BrowserForge 真实指纹分布     │
└─────────────────────────────────────────────────┘
```

---

## 更新记录

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

## 反馈 / 交流

使用过程中遇到 bug、想要新的 Hook 预设、或者想交流 JS 逆向思路，欢迎加微信：

- **微信号**：`han8888v8888`

> 加好友时烦请备注「camoufox-reverse」，方便快速通过。

## 许可证

MIT
