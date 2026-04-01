# camoufox-reverse-mcp

> 基于反指纹浏览器的 MCP Server，专为 JavaScript 逆向工程设计。

一个 MCP（Model Context Protocol）服务器，让 AI 编码助手（Claude Code、Cursor、Cline 等）能够通过 **Camoufox** 反指纹浏览器对目标网站进行：接口参数分析、JS 文件静态分析、动态断点调试、函数 Hook 追踪、网络流量拦截、Cookie/存储管理等逆向操作。

## 为什么选择 Camoufox？

| 特性 | chrome-devtools-mcp | js-reverse-mcp | **camoufox-reverse-mcp** |
|-----|--------------------|-----------------|-----------------------|
| 浏览器内核 | Chrome (Puppeteer) | Chrome (Patchright) | **Firefox (Camoufox)** |
| 反检测方案 | 无 | JS 级 60+ 参数 | **C++ 引擎级指纹伪造** |
| 调试能力 | 有限（无断点） | 完整 CDP | **Playwright + JS Hook** |

**核心优势：**
- Camoufox 在 **C++ 层面** 修改指纹信息，非 JS 层 patch，从根源不可检测
- Juggler 协议沙箱隔离使 Playwright **完全不可被页面 JS 检测到**
- BrowserForge 按 **真实世界流量统计分布** 生成指纹，不是随机拼凑
- 能在瑞数、极验、Cloudflare 等强反爬站点上正常工作

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

## 可用工具一览（44 个）

### 导航 & 页面
| 工具 | 说明 |
|------|------|
| `launch_browser` | 启动 Camoufox 反指纹浏览器 |
| `close_browser` | 关闭浏览器，释放资源 |
| `navigate` | 导航到指定 URL |
| `reload` / `go_back` | 刷新页面 / 浏览器后退 |
| `take_screenshot` | 截图（支持全页面、指定元素） |
| `take_snapshot` | 获取页面无障碍树（更省 token） |
| `click` / `type_text` | 点击元素 / 输入文本 |
| `wait_for` | 等待元素出现或 URL 匹配 |
| `get_page_info` | 获取当前页面 URL、标题、视口尺寸 |

### JS 脚本分析（逆向核心）
| 工具 | 说明 |
|------|------|
| `list_scripts` | 列出页面所有已加载的 JS 脚本 |
| `get_script_source` | 获取指定 JS 文件的完整源码 |
| `search_code` | 在所有已加载脚本中搜索关键词 |
| `save_script` | 将 JS 文件保存到本地 |
| `get_page_html` | 获取完整页面 HTML 或指定元素 |

### 断点调试（逆向核心）
| 工具 | 说明 |
|------|------|
| `evaluate_js` | 在页面上下文执行任意 JS 表达式 |
| `evaluate_js_handle` | 执行 JS 并检查复杂对象属性 |
| `add_init_script` | 注入在页面 JS 之前执行的脚本（Hook 核心） |
| `set_breakpoint_via_hook` | 通过 Hook 设置伪断点，捕获参数/调用栈/返回值 |
| `get_breakpoint_data` | 获取伪断点捕获的数据 |
| `get_console_logs` | 获取页面 console 输出（Hook 的主要输出通道） |

### Hook & 追踪（逆向核心）
| 工具 | 说明 |
|------|------|
| `trace_function` | 追踪任意函数调用，记录参数和返回值 |
| `get_trace_data` | 获取追踪数据 |
| `hook_function` | 注入自定义 Hook（before / after / replace） |
| `inject_hook_preset` | 一键注入预置 Hook（xhr/fetch/crypto/websocket/debugger_bypass） |
| `remove_hooks` | 移除所有 Hook（通过刷新页面） |

### 网络分析（逆向核心）
| 工具 | 说明 |
|------|------|
| `start_network_capture` | 开始捕获网络请求 |
| `stop_network_capture` | 停止捕获 |
| `list_network_requests` | 列出已捕获的请求（支持多维过滤） |
| `get_network_request` | 获取指定请求的完整详情 |
| `get_request_initiator` | 获取请求发起的 JS 调用栈（定位加密函数黄金路径） |
| `intercept_request` | 拦截请求：记录 / 阻断 / 修改 / 模拟响应 |
| `stop_intercept` | 停止拦截 |

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
2. inject_hook_preset("xhr")          ← 注入 XHR Hook
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
3. inject_hook_preset("xhr")
4. inject_hook_preset("fetch")
5. navigate("https://target.com")
6. list_scripts()                        ← 找到 JSVMP 相关 JS 文件
7. get_script_source("https://target.com/js/vmp.js")
8. search_code("interpreter")            ← 搜索虚拟机解释器入口
9. trace_function("window._vmp.exec", log_args=True, log_return=True)
10. 触发目标操作
11. get_trace_data("window._vmp.exec")   ← 查看字节码执行流
12. 根据执行流还原算法逻辑
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
│  │ Network  │ Storage  │Fingerprint│  Utils  │  │
│  └──────────┴──────────┴──────────┴──────────┘  │
│                    ↕ Playwright API               │
├─────────────────────────────────────────────────┤
│      Camoufox (反指纹 Firefox, Juggler 协议)      │
│  C++ 引擎级指纹伪造 · BrowserForge 真实指纹分布     │
└─────────────────────────────────────────────────┘
```

## 许可证

MIT
