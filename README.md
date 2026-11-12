# camoufox-reverse-mcp

[![Release](https://img.shields.io/github/v/release/WhiteNightShadow/camoufox-reverse-mcp)](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases/latest)
[![CI](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[中文](README.md) | [English](README_en.md)

基于 [Camoufox](https://github.com/daijro/camoufox) 反指纹浏览器的 MCP Server，专为 JavaScript 逆向工程设计。AI 编码助手（Claude Code、Cursor、Cline 等）可以用它分析接口参数和 JS 文件、Hook 函数、拦截网络流量、调查 JSVMP，并离线验证签名代码。

[工具参考](docs/TOOLS.md) | [JSVMP 实战](docs/JSVMP_PLAYBOOK.md) | [通用采集](docs/GENERAL_COLLECTION.md) | [更新记录](CHANGELOG.md)

## 特性

- **反指纹浏览器**：Camoufox 在 C++ 层修改指纹，不依赖页面 JS patch；BrowserForge 按真实流量的统计分布生成指纹。
- **运行时分析**：网络捕获、脚本搜索、函数 Hook 和 JS 执行；Hook 和 JS 执行可以指定主世界和 Frame。
- **JSVMP 调查**：运行时探针和源码插桩。配合[定制版浏览器](https://github.com/WhiteNightShadow/camoufox-reverse)，还能在 Gecko 原生层追踪属性访问，不改写页面 JS。
- **结果可验证**：用样本离线验证签名函数，定位首个偏差字符；抓到的请求可以对比和导出。

## 使用须知

- 只用于自有或已获授权的目标。
- MCP 客户端能读取和修改浏览器里的数据，包括 Cookie 和本地存储。不要在这个浏览器里登录不想让客户端接触的账号。
- 反指纹不等于不被识别。Hook、环境差异和时序仍可能被页面观察到，具体站点能否使用要用实际样本验证。
- 开启引擎追踪（`enable_trace=True`）时会临时关闭 Firefox content sandbox，普通启动不受影响。

## 环境要求

- Python 3.10 及以上，支持 macOS、Linux、Windows
- 支持 stdio 的 MCP 客户端；工具 schema 兼容 Moonshot/Kimi 等严格校验的服务
- Camoufox 浏览器，下载量约 1.3GB，macOS 上解压后约 2.5GB

## 快速开始

### 方式一：AI 对话框直接安装（推荐）

在你的 AI 编码工具（Cursor / Claude Code / Codex 等）的对话框中输入：

```
帮我安装下这个mcp工具：camoufox-reverse-mcp
项目地址：https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

AI 会自动完成克隆、安装依赖、配置 MCP Server 的全部流程。

### 方式二：手动安装

适合已有 Python 环境或需要改代码的情况。

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
pip install -e .
python -m camoufox fetch
```

最后一条下载浏览器。跳过的话，第一次 `launch_browser` 会自动下载，要等下载结束才返回，实测约 4 分钟，客户端可能先超时。

写入客户端的 MCP 配置，例如 Cursor 的 `.cursor/mcp.json`、Claude Code 项目里的 `.mcp.json`：

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

这里的 `python` 要用安装时的同一个解释器。

### 方式三：uvx（无需克隆，可指定版本）

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 git，再在终端各运行一次：

```bash
uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.10.0" camoufox-reverse-mcp --version
uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.10.0" camoufox fetch
```

第一条下载依赖并确认版本，可以避免客户端首次启动时等待超时。第二条下载浏览器，保存在用户缓存目录，不随 uvx 环境清理。

写入客户端的 MCP 配置：

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.10.0",
        "camoufox-reverse-mcp"
      ]
    }
  }
}
```

Claude Code 也可以用一条命令添加：

```bash
claude mcp add --scope user camoufox-reverse -- uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.10.0" camoufox-reverse-mcp
```

<details>
<summary>更新版本与常驻安装</summary>

**更新版本**：在 [Releases](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases) 查看最新标签，把配置里的 `@v1.10.0` 改成新标签，再重启客户端。标签是固定的，不改就不会变。去掉 `@v1.10.0` 会在每次启动时跟随 main 的最新提交，不建议在日常使用时这样配置。如果 `check_environment` 提示缺少浏览器，用新标签再运行一次 `camoufox fetch`。

**常驻安装**：配置里的 `command` 写 `camoufox-reverse-mcp`，不需要 `args`。浏览器仍用上面的 `uvx ... camoufox fetch` 下载，两种方式共用同一份浏览器。

```bash
uv tool install "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.10.0"
uv tool install --force "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@<新标签>"
```

第二条用于更新。`uv tool upgrade` 不会移动已固定的标签，要用 `--force` 重新安装。

</details>

### 验证安装

重启客户端，然后输入：

```
用 camoufox-reverse 打开 https://example.com，抓取网络请求，告诉我页面标题和请求数量
```

正常情况下客户端会启动浏览器，并返回标题 `Example Domain`。出问题时让 AI 调用 `check_environment`，它会列出依赖和浏览器的状态。

## 工具

共 41 个工具，分 9 类。参数说明和使用场景见[工具参考](docs/TOOLS.md)。

| 类别 | 数量 | 用途 |
|---|---:|---|
| [浏览器控制](docs/TOOLS.md#浏览器控制) | 10 | 启动、导航、截图、快照、点击、输入 |
| [JS 执行与调试](docs/TOOLS.md#js-执行与调试) | 2 | 在隔离上下文或页面主世界执行 JS，录制可回放的会话 |
| [脚本分析](docs/TOOLS.md#脚本分析) | 2 | 列出、读取、保存脚本，搜索代码 |
| [Hook 与追踪](docs/TOOLS.md#hook-与追踪) | 5 | 函数 Hook 和 Trace、预置 Hook、控制台日志 |
| [网络分析](docs/TOOLS.md#网络分析) | 8 | 抓包、拦截、发起调用栈、请求对比、导出 |
| [JSVMP 逆向分析](docs/TOOLS.md#jsvmp-逆向分析) | 4 | 运行时探针、源码插桩、状态级深挖、环境指纹采集与导出 |
| [Cookie 与存储](docs/TOOLS.md#cookie-与存储) | 4 | Cookie、Storage、浏览器状态导入导出 |
| [验证与环境](docs/TOOLS.md#验证与环境) | 3 | 离线验签、环境自检、残留清理 |
| [引擎层属性追踪](docs/TOOLS.md#引擎层属性追踪) | 3 | Gecko 原生属性追踪，需要定制版浏览器 |

## 启动参数

写在启动命令后面，uvx 方式写在 `camoufox-reverse-mcp` 后面：

```json
"args": ["-m", "camoufox_reverse_mcp", "--headless", "--proxy", "http://127.0.0.1:7890"]
```

| 参数 | 说明 |
|---|---|
| `--headless` | 无头模式 |
| `--proxy <url>` | 代理地址，例如 `http://127.0.0.1:7890` |
| `--geoip` | 根据代理推断地理信息 |
| `--humanize` | 模拟人类的鼠标移动 |
| `--os <系统>` | 模拟的系统指纹：`auto`、`windows`、`macos`、`linux`，默认 `auto` |
| `--locale <语言>` | 浏览器语言，例如 `zh-CN`，默认 `auto` |
| `--block-images` | 不加载图片 |
| `--block-webrtc` | 禁用 WebRTC |
| `--version` | 输出版本后退出 |

> **注意**：调用 `launch_browser` 时传了的参数优先，没传的沿用这里的启动参数。

## 文档

| 文档 | 内容 |
|---|---|
| [工具参考](docs/TOOLS.md) | 全部工具、使用场景、技术架构 |
| [JSVMP 实战](docs/JSVMP_PLAYBOOK.md) | 反爬类型识别与工作流 |
| [通用采集](docs/GENERAL_COLLECTION.md) | 采集行为、限制与示例 |
| [真实源码与验证](docs/REAL_SOURCE_VALIDATION.md) | 真实上游源码的实操记录 |
| [公开研究与实操记录](docs/RESEARCH_AND_VALIDATION.md) | 公开方案取舍与 Agent 验证方法 |

## 更新记录

- **v1.10.0**（2026-11-12）：插桩默认只改写成员读取、文件上限提到 2MB，能离线改写本地源码，日志能按序号和状态切片；同源 iframe 的事件汇总到顶层日志；preset 改装到页面主世界；`compare_env` 能导出环境包；新增 `stage_break` 和 `trace_session`。[详情](docs/releases/v1.10.0.md)
- **v1.9.4**（2026-11-10）：`instrumentation` 新增 `tap_stage_calls`，按状态记录平坦化 VMP 的分发调用，方便和本地补环境逐段对照。[详情](docs/releases/v1.9.4.md)
- **v1.9.3**（2026-11-10）：`hook_jsvmp_interpreter` 改装到页面主世界，能记录页面脚本；proxy 模式不再无限递归。[详情](docs/releases/v1.9.3.md)

全部版本见 [CHANGELOG.md](CHANGELOG.md)。

## 社区贡献者

- [@tuntun1337](https://github.com/tuntun1337) — 严格 JSON Schema 兼容性
- [@Code-xy](https://github.com/Code-xy) — Windows 引擎追踪问题定位与验证
- [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) — JSVMP 执行点关联需求与分析材料，`instrumentation` 的 `tap_stage_calls` 阶段插桩，`stage_break` 与 `trace_session`，以及 v1.10.0 处理的 #19–#25 问题报告
- [@dsaw1111](https://github.com/dsaw1111) — Camoufox 152 版本差距与 PropertyTracer 升级需求
- [@chung1912](https://github.com/chung1912) — `uvx` 安装建议与 `uv tool install` 配置
- [@tunguyen1993](https://github.com/tunguyen1993) — `hook_jsvmp_interpreter` 的 `script_url` 转义问题与修复建议

## 反馈 / 交流

使用过程中遇到 bug、想要新的 Hook 预设、或者想交流 JS 逆向思路，欢迎加微信：

- **微信号**：`han8888v8888`

> 加好友时烦请备注「camoufox-reverse」，方便快速通过。

## 许可证

[MIT](LICENSE)
