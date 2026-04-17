# JSVMP 逆向实战 Playbook

## 第 0 步：识别反爬类型

任何分析开始之前先做识别。不要上来就 `pre_inject_hooks`。

```python
await launch_browser(headless=False)
await start_network_capture(capture_body=False)
r = await navigate("https://target.com/")
```

看返回：

- `r["redirect_chain"]` 有 **412** 或多次同 URL 响应 → 大概率**签名型**反爬
- 跳到特定 `challenge.html` 或加载滑块图片 → **行为型**（行为验证/验证码）
- 直接返回 200 但 JS 很大且压缩严重 → **纯混淆**或无反爬
- `r["initial_status"] != r["final_status"]` → 有 JS 驱动的跳转，进一步查

辅助判断：

```python
scripts = await list_network_requests(resource_type="script")
# 找大于 100KB 的 JS,通常就是 VMP
# 签名型反爬的 VMP 文件名常见: vmp_target*.js / challenge_*.js
# 签名型反爬: 混在 body 里的内联 script 或特定路径
# 行为型反爬: vmp_target.es5.*.js
```

---

## 工作流 A：签名型反爬

**核心原则：绝不在挑战完成前动环境**。观察只能用源码插桩。

### A.1 被动探测

```python
# 1. 让页面按自己的方式走完挑战,不加任何 hook
await navigate("https://target.com/")
# 观察 redirect_chain,确认 412 -> 412 -> 200 的模式
```

### A.2 归因 cookie

```python
# 不需要 cookie_hook,因为签名型反爬 cookie 基本都是 Set-Cookie 响应头
await analyze_cookie_sources()
# 关注 sources=["http_set_cookie"] 的 cookie,它们的 http_responses[].url
# 就是服务端签发 cookie 的端点
```

### A.3 源码插桩观察 VMP 内部

```python
# 定位 VMP 脚本
loops = await find_dispatch_loops(
    script_url="https://target.com/vmp_target-xxx.js",
    min_case_count=30,
)
# case_count 通常 50-200

# 装源码级插桩 — mode="ast" 走 MCP 侧 esprima,不需要 CDN
await instrument_jsvmp_source("**/vmp_target*.js", mode="ast", tag="vmp")

# 重新加载,让改写后的源码执行
await reload_with_hooks()  # 清空 log 再 reload
# 等挑战完成 - 这次 cookie 签名仍然有效,因为改写只在 JS 代码里插 tap,
# 没有碰 navigator/screen 等

# 取 log
log = await get_instrumentation_log(tag_filter="vmp",
                                    type_filter="tap_get", limit=200)
# hot_keys 会告诉你 VMP 读了哪些环境属性
```

### A.4 抽取算法

根据 hot_keys / hot_methods / hot_functions 和 `dump_jsvmp_strings` 的 api_names，
在 Node.js / jsdom 侧复刻环境变量，把 VMP 代码独立跑起来生成有效 cookie。

### A.5 不要做的事

- ❌ `pre_inject_hooks=["jsvmp_probe"]` — 签名会废
- ❌ `hook_jsvmp_interpreter(mode="proxy")` — 同上
- ❌ `trace_property_access(["navigator.*"])` — 内部也用 getter/Proxy 替换,会污染
- ⚠️ `hook_jsvmp_interpreter(mode="transparent")` — 比 proxy 模式安全得多,但某些极严格的签名型反爬版本仍能感知 descriptor 的 getter 函数 identity 变化。只有在源码插桩失败时才退到这里

---

## 工作流 B：行为型反爬

核心原则：怎么方便怎么来，runtime hook 全量打开。

### B.1 一把梭

```python
await launch_browser(headless=False)
await start_network_capture(capture_body=True)
r = await navigate(
    "https://target.com/",
    pre_inject_hooks=["jsvmp_probe", "xhr", "fetch", "crypto"],
)

# 让用户做一次挑战操作(滑块/验证码)
# ...
# 然后看 trace
log = await get_jsvmp_log(type_filter="proxy_get")
xhr = await get_trace_data("XMLHttpRequest.prototype.send")
```

### B.2 抓签名请求

行为型反爬站点的 JSVMP 会在每次 XHR/Fetch 时往 header 里塞签名参数。用 `xhr` 或 `fetch` hook 抓下来就行。

---

## 工作流 C：纯混淆

没啥规矩，想怎么来怎么来。优先 `dump_jsvmp_strings` 看字符串表，
然后 `instrument_jsvmp_source(mode="regex")` 快速撒 tap。

---

## 附：三种工作流的核心差异一句话

- **签名型**：**观察你改不了的东西**（源码里的调用），**别碰你能改的东西**（环境）
- **行为型**：怎么观察都行，问题在参数构造
- **纯混淆**：问题只是读代码，不涉及观察冲突
