# 工具参考

[中文](TOOLS.md) | [English](TOOLS_en.md)

共 39 个工具，按用途分 9 类。安装方式和启动参数见 [README](../README.md)。

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
- `compare_env`：采集一组 JS 层环境基线；可通过 `properties` 或分批
  `evaluate_js` 扩展，不能视为全量枚举
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
