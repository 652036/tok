# tok

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Build](https://img.shields.io/badge/build-placeholder-lightgrey.svg)](#开发)

在本机统计各 AI 编程 CLI 的 token 用量，并按官方 API 标价换算美元金额。

`tok` 只读取 Claude Code、Codex、Grok、Gemini CLI、Aider、OpenCode、Amp、GitHub Copilot CLI 等工具**已经写在本机磁盘上的用量元数据**。它会计数 token，再用**公开的 API 标价**估一个 USD。数据不会上传。

[English README](README.md)

---

## 这是什么

一个 Python 3.11+ 命令行工具。在你自己的电脑上运行：扫描常见的本地数据目录，解析用量事件，打印表格（或 JSON）。

它可以回答：

- 这个月各工具、各模型烧掉了多少 token？
- 按官方 API 标价大约值多少钱？
- 哪个项目或哪次会话用得最多？

## 为什么做

订阅后台往往不完整、分散在各家，或本地 CLI 根本没有账单页。磁盘上的日志其实已经有 token（有时还有写入时的 cost）。`tok` 把这些文件汇总成一份本地报告。

## 功能

- **多 CLI** — 一条命令看多家编程助手
- **纯本地** — 不用登录、无遥测、不需要网络
- **Token 拆分** — input、output、cache 读/写、reasoning
- **API 等价 USD** — 优先用日志自带的 cost，否则查 `pricing_data.json`
- **未知模型仍可见** — 照计 token，金额标为未计价，绝不编造官方价格
- **多种视图** — 按工具、模型、日期（Asia/Shanghai）、项目、会话
- **过滤** — `-S/--since`、`-U/--until`、`-t/--tool`
- **JSON** — `-j/--json` 方便脚本
- 安装了可选依赖 `rich` 时用 Rich 表格，否则纯文本对齐

## 支持的 CLI

| CLI | `tool` 标识 | 常见本机目录（以 `discover` 为准） |
| --- | --- | --- |
| Claude Code | `claude` | `~/.claude/` |
| OpenAI Codex CLI | `codex` | `~/.codex/` |
| Grok CLI | `grok` | `~/.grok/` |
| Gemini CLI | `gemini` | `~/.gemini/` |
| Aider | `aider` | `~/.aider/` 以及项目内 `.aider*` |
| OpenCode | `opencode` | `~/.local/share/opencode/` 或 `~/.opencode/` |
| Amp | `amp` | `~/.amp/` |
| GitHub Copilot CLI | `copilot` | `~/.copilot/` |

具体路径随版本和操作系统而变。请在本机运行 `tok w`。若仓库里有 `docs/SOURCES.md`，里面是各解析器的路径说明。

## 安装

需要 Python 3.11 或更高版本。

```bash
git clone https://github.com/OWNER/ai-usage.git
cd ai-usage
pip install -e .
```

可选，表格更好看：

```bash
pip install rich
```

仓库中的 `pyproject.toml` 会注册 `tok` 命令（旧名 `ai-usage` 仍可用）。安装后：

```bash
tok --help
```

未安装、直接从源码运行：

```bash
PYTHONPATH=src python -m ai_usage --help
```

## 用法

```bash
# 按工具汇总（默认命令）
tok

# 其他视图
tok m            # 按模型   (别名: model, by-model)
tok d            # 按日期   (别名: day, by-day)
tok p            # 按项目   (别名: proj, project, by-project)
tok s            # 按会话   (别名: sess, session, sessions)
tok w            # 发现目录 (别名: where, discover)

# 日期范围 — 按 Asia/Shanghai 当天 0 点理解
# -U/--until 包含该日全天
tok -S 2026-08-01
tok -S 2026-08-01 -U 2026-08-18

# 只看某个工具（逗号分隔多个）
tok m -t claude
tok -t claude,codex

# 给脚本用
tok d -j -S 2026-08-01

# 数据在另一个 home 目录下（--home 可放在子命令前或后）
tok --home /path/to/home
tok m -H /path/to/home
tok w -H /path/to/home

# 本机有哪些数据目录？
tok w
```

运行成功时退出码为 `0`，**即使没有找到任何用量数据**（会打印简短提示）。

### 表格列

视命令而定，可能包含：tool、model、project、day、session、days、sessions、input、output、cache read、cache write、reasoning、total tokens、API USD。

末行固定为 **TOTAL**。Token 使用千分位（`1,234,567`）。金额格式为 `$1,234.5678`（默认 4 位小数，小于 1 美元时用 5–6 位）。

若部分事件无法计价，脚注会说明有多少 **unpriced tokens**。

## 工作原理（只读本地文件）

全程不离开本机磁盘。

1. **发现** home（或 `-H/--home`）下的常见数据目录。
2. **解析** 用量元数据：时间戳、模型名、token 计数、可选的已记录费用、项目/会话 id。解析器不读提示词或回复正文，也绝不打印 API key。
3. **计价**：日志里已有 cost 就用它，否则查 `src/ai_usage/pricing_data.json` 里的公开标价。
4. **汇总** 后打印表格或 JSON。

不访问任何云账号。可以离线运行。

## 计价说明

- 金额是 **API 标价估算（USD）**，不是发票，也不是你的订阅账单。
- 日志自带的 cost（`raw_cost_usd`）优先于价目表。
- 否则使用 `pricing_data.json`。该文件与 [sub2API](https://github.com/Wei-Shaw/sub2api) 使用同一份 LiteLLM 风格价目表（[Wei-Shaw/model-price-repo](https://github.com/Wei-Shaw/model-price-repo)）的快照，**不是**厂商实时官方 API。会过时；价格会变。更新方式：刷新 `docs/vendor/model_prices_and_context_window.json` 后重新运行 `scripts/import_litellm_prices.py`（见 `docs/PRICING_SOURCES.md`）。
- 价目表没有的模型：照计 token，USD 标为 **未计价**。本项目不会编造官方价格。
- Cache / reasoning token 仅在价目表有对应费率时才计入金额。
- 不建模包月或包含额度。请把 USD 列理解成「按 API 标价大概值多少」，不是「我实际付了多少」。
- 与 Anthropic、OpenAI、Google、xAI、Sourcegraph、GitHub 等厂商无隶属关系。

## 隐私

- **只读。** 不会改写各 CLI 的日志。
- **只用用量字段。** Token、模型名、时间戳、项目/会话标识、日志文件路径。
- **不读提示词内容。** 解析器跳过消息正文和附件。
- **不读取、不打印 API key 或凭据。**
- **不上传。** 没有遥测、没有崩溃上报、不会把你的数据发到网上。
- JSON 只打到本机 stdout，是否保存或分享由你决定。

## 配置

无需配置文件。

| 参数 / 环境变量 | 含义 |
| --- | --- |
| `-H` / `--home PATH` | 把 `PATH` 当作 home 来查找 CLI 数据 |
| `AI_USAGE_HOME` | 未传 `--home` 时等同于 `--home` |
| `-S` / `--since YYYY-MM-DD` | 含该日 0 点（Asia/Shanghai）及之后 |
| `-U` / `--until YYYY-MM-DD` | 含该日全天（Asia/Shanghai） |
| `-t` / `--tool NAME` | 只统计指定工具 |
| `-j` / `--json` | 输出 JSON |
| `AI_USAGE_DEBUG=1` | 意外错误时打印 traceback |

`-S` / `--since` 与 `-U` / `--until` 以及 **by-day** 视图的默认时区是 **Asia/Shanghai**。事件时间在内部按带时区的 UTC 存储。

## 开发

```bash
git clone https://github.com/OWNER/ai-usage.git
cd ai-usage
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check
pytest -q
```

共享类型和字段名见 [DESIGN.md](DESIGN.md)。不要随意改 `UsageEvent` / `CostBreakdown` 的字段名。

建议布局：

- `src/ai_usage/models.py` — `UsageEvent`、`CostBreakdown`
- `src/ai_usage/pricing.py` + `pricing_data.json` — `price_event()`
- `src/ai_usage/parsers/` — 每个 CLI 一个模块，导出 `parse(root=None) -> list[UsageEvent]`
- `src/ai_usage/cli.py` / `report.py` — 本 CLI 与表格
- `docs/SOURCES.md` — 各解析器在磁盘上的查找位置（若有）

## 许可证

[MIT](LICENSE)。Copyright (c) ai-usage contributors.

## 参与贡献

欢迎 Issue 和 Pull Request。

1. 解析器必须 **只读**，且只碰用量元数据（不要提示词，不要 API key）。
2. 不要编造官方价格。把模型加进 `pricing_data.json` 并注明公开来源，或保持未计价。
3. 为新命令和新解析器补充测试。
4. 遵守 [DESIGN.md](DESIGN.md) 中的字段名。

详见 [CONTRIBUTING.md](CONTRIBUTING.md)（环境、解析器约定与隐私约定）。
