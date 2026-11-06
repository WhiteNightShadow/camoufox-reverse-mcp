# camoufox-reverse-mcp

[![Release](https://img.shields.io/github/v/release/WhiteNightShadow/camoufox-reverse-mcp)](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases/latest)
[![CI](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[中文](README.md) | [English](README_en.md)

An MCP server built on the [Camoufox](https://github.com/daijro/camoufox) anti-detection browser, designed for JavaScript reverse engineering. AI coding assistants (Claude Code, Cursor, Cline, etc.) use it to analyze API parameters and JS files, hook functions, intercept network traffic, investigate JSVMP, and verify signing code offline.

[Tool reference](docs/TOOLS_en.md) | [JSVMP playbook](docs/JSVMP_PLAYBOOK.md) | [General capture](docs/GENERAL_COLLECTION.md) | [Changelog](CHANGELOG_en.md)

## Features

- **Anti-detection browser**: Camoufox changes fingerprints in C++ rather than through page-level JS patches, and BrowserForge generates them from real-world traffic distributions.
- **Runtime analysis**: network capture, script search, function hooks and JS execution. Hooks and JS execution can target the main world and specific frames.
- **JSVMP investigation**: runtime probes and source instrumentation. With the [custom browser build](https://github.com/WhiteNightShadow/camoufox-reverse), property access can also be traced in native Gecko code without rewriting page JS.
- **Verifiable results**: check a signer offline against samples and find the first differing character. Captured requests can be compared and exported.

## Before You Use It

- Use it only on targets you own or are authorized to test.
- MCP clients can read and change browser data, including cookies and local storage. Do not sign in to accounts you do not want the client to reach.
- Anti-detection does not mean undetectable. Pages can still observe hooks, environment differences and timing, so verify each site with real samples.
- Engine tracing (`enable_trace=True`) temporarily disables the Firefox content sandbox. Normal launches are unaffected.

## Requirements

- Python 3.10 or later on macOS, Linux or Windows
- An MCP client with stdio support. Tool schemas work with strict providers such as Moonshot/Kimi.
- The Camoufox browser, about 1.3 GB to download and about 2.5 GB unpacked on macOS

## Quick Start

### Option 1: Install via AI Chat (Recommended)

Paste the following into your AI coding tool's chat (Cursor / Claude Code / Codex, etc.):

```
Please install this MCP tool: camoufox-reverse-mcp
Project URL: https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

The AI will automatically clone, install dependencies, and configure the MCP server.

### Option 2: Manual Installation

Use this if you already have a Python environment or want to change the code.

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
pip install -e .
python -m camoufox fetch
```

The last command downloads the browser. If you skip it, the first `launch_browser` downloads it and returns only when that finishes, about 4 minutes in our test, so your client may time out first.

Add this to your client's MCP configuration, such as `.cursor/mcp.json` for Cursor or a project `.mcp.json` for Claude Code:

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

`python` must be the interpreter you installed into.

### Option 3: uvx (no clone, pin a version)

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and git first, then run these once in a terminal:

```bash
uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0" camoufox-reverse-mcp --version
uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0" camoufox fetch
```

The first command downloads the dependencies and confirms the version, which keeps your client from timing out on the first start. The second downloads the browser into your user cache, so clearing uvx environments does not remove it.

Add this to your client's MCP configuration:

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0",
        "camoufox-reverse-mcp"
      ]
    }
  }
}
```

Claude Code can add it with one command:

```bash
claude mcp add --scope user camoufox-reverse -- uvx --from "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0" camoufox-reverse-mcp
```

<details>
<summary>Updating and persistent installs</summary>

**Updating**: find the latest tag on [Releases](https://github.com/WhiteNightShadow/camoufox-reverse-mcp/releases), change `@v1.9.0` in the configuration to it, and restart your client. A tag never moves on its own. Without `@v1.9.0`, every start follows the latest commit on main, which is not recommended for daily use. If `check_environment` reports a missing browser, run `camoufox fetch` again with the new tag.

**Persistent install**: set `command` to `camoufox-reverse-mcp` and leave out `args`. Fetch the browser with the `uvx ... camoufox fetch` command above, because both setups share the same browser.

```bash
uv tool install "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@v1.9.0"
uv tool install --force "git+https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git@<new-tag>"
```

The second command updates it. `uv tool upgrade` does not move a pinned tag, so reinstall with `--force`.

</details>

### Check the Install

Restart your client, then enter:

```
Use camoufox-reverse to open https://example.com, capture the network requests, and tell me the page title and how many requests were made
```

The client should launch the browser and return the title `Example Domain`. If something fails, ask the AI to call `check_environment`, which reports the state of the dependencies and the browser.

## Tools

39 tools in 9 groups. See the [tool reference](docs/TOOLS_en.md) for details and usage scenarios.

| Group | Count | What it covers |
|---|---:|---|
| [Browser Control](docs/TOOLS_en.md#browser-control) | 10 | Launch, navigate, screenshot, snapshot, click, type |
| [JS Execution & Debugging](docs/TOOLS_en.md#js-execution--debugging) | 1 | Run JS in the isolated context or the page main world |
| [Script Analysis](docs/TOOLS_en.md#script-analysis) | 2 | List, read and save scripts, search code |
| [Hooking & Tracing](docs/TOOLS_en.md#hooking--tracing) | 5 | Function hooks and traces, preset hooks, console logs |
| [Network Analysis](docs/TOOLS_en.md#network-analysis) | 8 | Capture, intercept, initiator stacks, compare, export |
| [JSVMP Reverse Analysis](docs/TOOLS_en.md#jsvmp-reverse-analysis) | 3 | Runtime probes, source instrumentation, environment fingerprints |
| [Cookies & Storage](docs/TOOLS_en.md#cookies--storage) | 4 | Cookies, storage, browser state import and export |
| [Verification & Environment](docs/TOOLS_en.md#verification--environment) | 3 | Offline signer checks, self-check, residual cleanup |
| [Native PropertyTracer](docs/TOOLS_en.md#native-propertytracer) | 3 | Native Gecko property tracing, needs the custom browser build |

## Server Options

Put them after the start command. With uvx, put them after `camoufox-reverse-mcp`:

```json
"args": ["-m", "camoufox_reverse_mcp", "--headless", "--proxy", "http://127.0.0.1:7890"]
```

| Option | Description |
|---|---|
| `--headless` | Run headless |
| `--proxy <url>` | Proxy URL, for example `http://127.0.0.1:7890` |
| `--geoip` | Infer geolocation from the proxy |
| `--humanize` | Humanize mouse movement |
| `--os <system>` | OS fingerprint to emulate: `auto`, `windows`, `macos` or `linux`. Default `auto` |
| `--locale <locale>` | Browser locale, for example `en-US`. Default `auto` |
| `--block-images` | Do not load images |
| `--block-webrtc` | Block WebRTC |
| `--version` | Print the version and exit |

> **Note**: All of these apply only when the browser starts on its own, for example when `navigate` runs before `launch_browser`. A `launch_browser` call uses its own arguments instead, and only `--proxy` still applies if no `proxy` argument is passed. To run headless, say so in the chat, for example "launch it headless".

## Documentation

| Document | Contents |
|---|---|
| [Tool reference](docs/TOOLS_en.md) | All tools, usage scenarios, architecture |
| [JSVMP playbook](docs/JSVMP_PLAYBOOK.md) (Chinese) | Anti-bot type identification and workflow |
| [General capture](docs/GENERAL_COLLECTION.md) (Chinese) | Capture behavior, limits and examples |
| [Real source validation](docs/REAL_SOURCE_VALIDATION.md) (Chinese) | Hands-on records with real upstream source |
| [Research and validation](docs/RESEARCH_AND_VALIDATION.md) (Chinese) | Public approaches compared and agent validation method |

## Changelog

- **v1.9.0** (2026-11-05): `uvx` install support; third-party output no longer enters the MCP protocol stream. [Notes](docs/releases/v1.9.0.md)
- **v1.8.0** (2026-09-08): Real upstream cases and observation semantics. [Notes](docs/releases/v1.8.0.md)
- **v1.7.0** (2026-09-08): Evidence and diagnostics validated by independent agents. [Notes](docs/releases/v1.7.0.md)

See [CHANGELOG_en.md](CHANGELOG_en.md) for every version.

## Community Contributors

- [@tuntun1337](https://github.com/tuntun1337) — strict JSON Schema compatibility
- [@Code-xy](https://github.com/Code-xy) — Windows engine-trace diagnosis and validation
- [@Moojing-jianchuan](https://github.com/Moojing-jianchuan) — JSVMP execution-site requirements and analysis materials
- [@dsaw1111](https://github.com/dsaw1111) — Camoufox 152 version-gap and PropertyTracer upgrade request
- [@chung1912](https://github.com/chung1912) — `uvx` install suggestion and `uv tool install` setup

## Feedback / Contact

Hit a bug, want a new hook preset, or just want to chat about JS reverse engineering? Add me on WeChat:

- **WeChat ID**: `han8888v8888`

> Please note "camoufox-reverse" in your friend request.

## License

[MIT](LICENSE)
