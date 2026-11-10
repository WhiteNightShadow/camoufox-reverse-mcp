# 更新记录

[中文](CHANGELOG.md) | [English](CHANGELOG_en.md)

v1.1.1 起每个版本的完整说明在 [docs/releases](docs/releases)，发布包在 [Releases](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases)。

## v1.9.2（2026-11-10）— JSVMP 探针转义修复

- `hook_jsvmp_interpreter` 改用 `json.dumps` 把 `script_url` 写进 hook，引号、反斜杠和换行都只是字符串内容，不会再跳出字符串执行代码。以前含反斜杠或换行的值会被改坏或让 hook 装不上
- 模板开头的注释不再写入 `script_url`，值里带 `*/` 也不会再跳出注释
- `max_entries` 先转成整数再写入 hook
- 感谢 [@tunguyen1993](https://github.com/tunguyen1993) 报告问题并给出修复建议

详情见 [v1.9.2](docs/releases/v1.9.2.md)。

## v1.9.1（2026-11-06）— 启动参数修复

- 修复 `launch_browser` 覆盖启动参数的问题：`--headless`、`--os`、`--locale`、`--geoip`、`--humanize`、`--block-images`、`--block-webrtc` 现在调用它时也生效，传了的参数优先
- README 改版，版本记录移到 `CHANGELOG.md`，工具表和使用场景移到 `docs/TOOLS.md`
- 新增 MIT `LICENSE` 文件

详情见 [v1.9.1](docs/releases/v1.9.1.md)。

## v1.9.0（2026-11-05）— uvx 安装支持

- 支持用 `uvx` 按标签固定版本安装和启动，不需要克隆仓库；新增 `--version`
- 第三方库写到 `stdout` 的文本改走 `stderr`，缺少浏览器时的下载提示不再混进 MCP 协议流
- Playwright 启动补丁改为写临时文件后原子替换，不再改到 uv 全局缓存里其他环境共用的文件
- `check_environment` 在没有可用浏览器时给出 `camoufox fetch` 命令
- 感谢 [@chung1912](https://github.com/chung1912) 提出 `uvx` 安装建议，并给出 `uv tool install` 的可用配置

详情见 [v1.9.0](docs/releases/v1.9.0.md)。

## v1.8.0（2026-09-08）— 真实源码实操与观测语义修复

- 真实 KProtect VM、javascript-obfuscator CFF、CryptoJS/FingerprintJS 多轮本地实操；失败反馈进入修复与复验。
- 插桩保留异常、this 和求值顺序，主世界/Frame 日志可读；本地 Acorn 支持现代语法，保守跳过不安全改写。
- 精确字符串可选 `evaluate_js(result_format="json_ascii")`；低副作用函数日志可选 `hook_function(serialization="preview")`，同步异常有明确记录。
- 动态脚本按当前响应处理，不因改写失败重放请求；原生快照明确当前页面与世界。

详情见 [v1.8.0](docs/releases/v1.8.0.md)、[真实源码与验证](docs/REAL_SOURCE_VALIDATION.md)。

## v1.7.0（2026-09-08）— 经多轮 Agent 实操的证据与诊断

- 新增 `compare_network_requests`、`save_response_body`，保留原始请求语义并明确哈希/字节口径。
- 环境检查按任务范围提示复用与失效，不默认清理已有证据；导航保留原始失败，快照有界等待与输出。
- 三轮九个独立任务保留失败并反馈修正，最终一轮签名/WASM/分页全部通过独立验收。

详情见 [v1.7.0](docs/releases/v1.7.0.md)、[公开研究与实操记录](docs/RESEARCH_AND_VALIDATION.md)。

## v1.6.0（2026-09-07）— 通用采集与独立验签

- 同 URL 并发响应按 Request 对象关联，完整采集 Cookie/Set-Cookie，并明确失败、淘汰、未完成任务和截断状态。
- `list_network_requests(limit=100, after_id=0)` 增量读取；`export_network_capture` 导出有版本号的 JSON，默认掩码，保留旧调用方式。
- `verify_signer_offline(..., runtime="node")` 可不启动浏览器验证独立签名函数；默认 browser 路径不变，无有效期望值的样本不会再误通过。
- Cookie 组合过滤只删除同时匹配的条目，老运行库通过逐条过期兼容；导出不会覆盖旧文件，采集清理不会复用旧请求 ID。

详细行为、限制与示例见 [通用采集契约](docs/GENERAL_COLLECTION.md) 和 [v1.6.0 版本说明](docs/releases/v1.6.0.md)。

## v1.5.2（2026-09-07）— 旧主世界通道兼容性

- 使用与真实调用相同的 Promise 形式探测主世界通道，识别只能返回同步结果的旧构建
- 不支持时在执行用户代码前选择 `wrappedJSObject` 回退，继续避免失败后重复执行

## v1.5.1（2026-09-07）— Trace 清理与 Hook 生命周期修复

- 修复清空 Trace 后再次调用目标函数抛错，保留页面原函数的返回行为
- 主世界通道先做无副作用探测，执行失败后不再通过另一通道重复运行用户代码
- 持久 Hook 可预先注册尚未创建的目标 Frame，返回明确的 `pending` 状态
- 补齐当前 Frame 各执行世界的 Hook 卸载；不可恢复的锁定属性明确报告，无需重编浏览器

## v1.5.0（2026-09-04）— 主世界、Frame 与可靠持久 Hook

- `evaluate_js/hook_function` 新增显式 `world="main"`，默认隔离上下文保持不变；Camoufox 原生 `mw:` 优先，旧版可安全回退
- 增加 Frame 清单与 `frame_url/frame_name/frame_index`，多匹配和不稳定持久索引明确拒绝
- 持久 Hook 增加有界等待和通用赋值监听，可捕获动态函数首次调用；缺失目标返回 `pending/target_not_found`，不再假报成功
- 恢复 `get_trace_data`，Trace 缓存按 world/Frame 隔离且有界；修复 intercept 忽略 `persistent` 的问题
- Firefox 135、官方 152、reverse.5 及真实 FeiLin 页面验证通过，无需重新编译浏览器

## v1.4.1（2026-09-03）— Firefox 152 LocalStorage 追踪路径对齐

- 配套 `camoufox-reverse` reverse.5，将既有 `localStorage.getItem/setItem`
  两点迁移到 Firefox 152 默认 LSNG `LSObject` 路径
- PropertyTracer 扩为 77 点、protocol 1 不变，事件 object/property/kind 与
  MCP 聚合行为不变；LSObject 与 partitioned 路径由 native site 区分
- 实时事件解析与聚合无需行为改动；默认 Trace-off、135/reverse.3/reverse.4
  兼容路径不变。历史 JSONL 无构建 marker，查询时 `hook_count` 改为明确未知

## v1.4.0（2026-09-03）— PropertyTracer 正确性、隔离与交互追踪

- 配套正式版 `camoufox-reverse` reverse.4：75 个原路径不变，正确区分 get/set/call，并解析 native site、进程序列和微秒时间扩展字段
- `trace_property_access` 新增兼容的 `action=start/stop/query/capture/clear/status`；可在 start 后继续执行页面操作，再 stop 聚合
- 每次浏览器启动分配独立 trace run，控制、清理和历史查询不再影响并发 MCP 实例
- 修复无浏览器时旧 control 文件被误报为 `installed/trace_active`；`check_environment` 现在拆分 installed、trace_capable、trace_active
- 新窗口严格执行 off → drain → cleanup → on，避免 Windows 开放文件混入旧事件
- 新增 kind/site 过滤、跨进程确定性排序、cap 提示及 capability 协商
- `collect_values` 明确为追踪后的安全快照；Cookie 与可能产生副作用的 API 默认跳过
- 可由 Camoufox 0.5 元数据识别的官方浏览器请求 `enable_trace=True` 时，不再注入无效配置或关闭 sandbox；普通启动保持不变
- Camoufox 0.4 的官方 135 与早期无 marker 的定制 135 无法在启动前区分；为兼容旧定制版，显式请求 Trace 时仍尝试旧握手，默认 `enable_trace=False` 不受影响

## v1.3.0（2026-09-02）— Camoufox 152 与无侵入多版本选择

- `launch_browser` 新增可选 `browser_version`，单次选择 Camoufox 0.5+ 已安装版本且不修改 active 配置
- 选择器强制使用 repo 限定并拒绝歧义；active/selected version+build 不一致时明确拒绝
- `check_environment` 新增 Camoufox Python、active、installed selectors、能力标记及旧缓存迁移风险诊断
- 修复主机为 `C.UTF-8` 时生成无效 `locale="C"`、导致浏览器无法启动的问题
- 默认参数保持不变；Camoufox 0.4.x + Firefox 135 用户无需迁移，官方浏览器仍可使用全部非 PropertyTracer 工具
- 配套可选的 [Camoufox Reverse 152 beta.30](https://github.com/WhiteNightShadow/camoufox-reverse/releases) 预发布构建
- 感谢 [@dsaw1111](https://github.com/dsaw1111) 报告定制版与官方版本差距

## v1.2.0（2026-08-11）— 通用源码执行点映射

- `instrumentation(action="install")` 新增默认关闭的 `include_source_site`
- AST 与 regex 插桩事件可携带内容寻址的稳定 `site_id` 和单调 `seq`
- `instrumentation(action="log")` 返回原始脚本 SHA-256、URL、字符区间与 AST 行列 sidecar，并补齐 `hot_functions`
- 默认 tap 事件字段保持不变；不执行用户提供的任意 AST 脚本，也不把单个 VM 的变量名猜成通用 PC/opcode
- 感谢 [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) 提出执行点关联需求并提供分析材料

## v1.1.2（2026-08-11）— Windows 引擎追踪配置修复

- 修复 Windows 上 Camoufox 将配置拆为 `CAMOU_CONFIG_1..n` 后，`enable_trace=True` 无法注入 `propertyTrace`、持续返回 `engine_trace_not_available` 的问题
- 完整重组原始 JSON 后再合并追踪配置，并按平台限制重新生成任意数量的连续分块；同时清理旧分块，保留全部原始指纹配置
- 对数字顺序、十块以上配置、Unicode、分块扩缩容、无效配置及调用真实 `camoufox.utils.get_env_vars()` 模拟 Windows 分块增加回归测试
- 针对本问题，已安装支持属性追踪的 `camoufox-reverse` 用户只需升级 MCP，无需重新编译或替换浏览器
- 感谢 [@Code-xy](https://github.com/Code-xy) 报告问题、完成 Windows 实机定位并提供修复思路

## v1.1.1（2026-07-29）— AST 链式调用插桩修复

- 修复 `new X().m1().m2()`、`Array.prototype.slice.call(arguments)` 等嵌套调用因父子 AST 编辑区间重叠而生成损坏代码的问题
- 仅在编辑区间重叠时保留外层插桩；普通成员访问和函数调用的现有改写行为保持不变
- `instrumentation(action="status")` 新增 `last_mode_used`，可区分 AST、regex 回退和超大文件跳过路径

## v1.1.0（2026-07-29）— 引擎追踪、浏览器接管与 Schema 兼容

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

## v1.0.0（2026-04-18）— 工具精简 + 回归纯 JS 逆向工具集

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

## v0.6.0 — 实战 Bug 修复

- `hook_jsvmp_interpreter(mode="proxy")`：修复 Proxy 递归导致 `too much recursion`
- `remove_hooks`：真正恢复 Proxy 对象
- `evaluate_js`：BOM / lone surrogate / whitespace 自动清理
- `instrument_jsvmp_source`：CSP 预检
- `navigate`：超时优雅降级

## v0.5.0 — 签名型反爬兼容

- `instrument_jsvmp_source` 默认 MCP 侧 AST 改写
- `hook_jsvmp_interpreter` 新增 `mode="transparent"`
- 反爬类型决策表 + JSVMP Playbook

## v0.4.0 — 通用 JSVMP 适配

- 源码级插桩、Cookie 归因、运行时探针
- hook_jsvmp_interpreter 多路径覆盖重写

## v0.3.0 — 稳定性修复

## v0.2.0 — Hook 持久化 + JSVMP 分析

## v0.1.0 — 初始版本（44 工具）
