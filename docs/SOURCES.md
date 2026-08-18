# Local log sources and pricing notes

Research date: **2026-08-18**. Official prices live in `src/tok/pricing_data.json`; every official URL is listed in `docs/PRICING_SOURCES.md`.

This file documents **default local paths**, **JSON/JSONL field names**, **how community tools parse them**, and **pitfalls**. Field names below are taken from official docs or from parser source that was actually opened. If a field is not cited, it is marked **unknown** — do not invent it.

Community tools referenced throughout:

| Tool | URLs |
| --- | --- |
| ccusage | https://github.com/ccusage/ccusage · https://github.com/ryoppippi/ccusage · https://www.npmjs.com/package/ccusage · https://ccusage.com/ |
| ccusage Claude guide | https://ccusage.com/guide/claude/ · https://github.com/ccusage/ccusage/blob/main/docs/guide/claude/index.md |
| ccusage Codex guide | https://github.com/ccusage/ccusage/blob/main/docs/guide/codex/index.md |
| ccusage Gemini commit | https://github.com/ccusage/ccusage/commit/4a7c880d1a4a2fa3ddc37403a4b511b78eb7c0a7 |
| ccusage Amp guide | https://github.com/ccusage/ccusage/blob/main/docs/guide/amp/index.md |
| ccusage Copilot guide | https://github.com/ccusage/ccusage/blob/main/docs/guide/copilot/index.md |
| ccusage env vars | https://github.com/ccusage/ccusage/blob/main/docs/guide/environment-variables.md |
| grokscope | https://github.com/daniel-farina/grokscope |
| tokenuse Gemini notes | https://tokenuse.app/docs/development/tools/gemini/ |
| agenthud Codex schema | https://github.com/neochoon/agenthud/blob/main/docs/schemas/codex-session.md |
| claudeops-tui JSONL notes | https://github.com/FullFran/claudeops-tui/blob/main/docs/jsonl-format.md |
| openusage Grok issue | https://github.com/robinebers/openusage/issues/646 |
| Aider analytics | https://aider.chat/docs/more/analytics.html |

ccusage does **not** support Aider or Grok/xAI CLI as of the 2026-08 docs index (https://ccusage.com/ — lists Claude Code, Codex, OpenCode, Amp, Droid, Codebuff, Hermes, pi-agent, Goose, OpenClaw, Kilo, Kimi, Qwen, Copilot CLI, Gemini CLI).

---

## 1. Claude Code — schema **confirmed**

### Default paths / globs

| Location | Notes |
| --- | --- |
| `~/.config/claude/projects/**/*.jsonl` | Current XDG default. ccusage checks this first. |
| `~/.claude/projects/**/*.jsonl` | Legacy home path. Combined with the XDG tree. |
| `$CLAUDE_CONFIG_DIR` | Replaces defaults. Comma-separated list allowed. |

tok also looks at `~/.config/claude` (XDG, same as ccusage) and `~/.claude`. On Windows the official home equivalent is `%USERPROFILE%\.claude` (https://code.claude.com/docs/en/claude-directory.md).

Sources: https://ccusage.com/guide/claude/ · https://github.com/ccusage/ccusage/blob/main/apps/ccusage/src/data-loader.ts · https://allaboutcoding.ghinda.com/where-ai-coding-clis-store-session-logs/

Layout: `projects/<project-slug>/<session-id>.jsonl`. Project name is the directory under `projects/`. Session id is the filename without `.jsonl`.

### JSONL fields (one object per line)

Confirmed by ccusage `usageDataSchema` (https://github.com/ccusage/ccusage/blob/main/apps/ccusage/src/data-loader.ts) and by a 2026 transcript dump in https://github.com/FullFran/claudeops-tui/blob/main/docs/jsonl-format.md:

| Purpose | Field |
| --- | --- |
| Timestamp | top-level `timestamp` (ISO-8601, typically UTC `Z`) |
| Session | top-level `sessionId` |
| Project / cwd | top-level `cwd` (optional). Project also from path `projects/<slug>/` |
| Model | `message.model` |
| Input tokens | `message.usage.input_tokens` |
| Output tokens | `message.usage.output_tokens` |
| Cache write | `message.usage.cache_creation_input_tokens` |
| Cache read | `message.usage.cache_read_input_tokens` |
| Cache write split (newer) | `message.usage.cache_creation.ephemeral_5m_input_tokens`, `ephemeral_1h_input_tokens` |
| Precomputed cost | top-level `costUSD` (optional) |
| Dedup keys | `message.id` (`msg_*`), top-level `requestId` (`req_*`), `uuid` |
| Speed / fast mode | `message.usage.speed` (`standard` or `fast`) — present in current ccusage schema |
| Line type | `type` (e.g. `"assistant"`) |

Example shape (from claudeops-tui, 2026-04): assistant line with sessionId, cwd, timestamp, message.model, message.usage.input_tokens / output_tokens / cache_creation_input_tokens / cache_read_input_tokens, and optional cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens. Full dump: https://github.com/FullFran/claudeops-tui/blob/main/docs/jsonl-format.md

### How community tools parse it

ccusage loads project jsonl, validates usageDataSchema, dedupes message.id+requestId, prices via LiteLLM unless costUSD.


### Pitfalls
- Streaming double-count: one JSONL line per content block; take last output_tokens per requestId. See claudeops-tui jsonl-format.md
- input_tokens may be 0/1 placeholders (gille.ai 2026 analysis). Cache fields look accurate.
- Cache vs input: input_tokens is uncached. Cache read 0.1x; 5m write 1.25x; 1h write 2x.
- Fast mode Opus 5/4.8 is 2x. Field message.usage.speed. Timezone UTC Z. Retention 30 days.

---

## 2. OpenAI Codex CLI — schema **confirmed**

### Default paths / globs

- `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (filename rollout-<ISO-with-colons-as-dashes>-<uuid>.jsonl)
- `$CODEX_HOME/sessions/...` — CODEX_HOME defaults to `~/.codex`. ccusage accepts a comma-separated list.
- `CODEX_SESSIONS_DIR` observed by agenthud; not documented by OpenAI.

Path writeups (format reverse-engineered; OpenAI has no formal schema): https://codex.danielvaughan.com/2026/06/05/codex-cli-session-forensics-jsonl-post-mortems-codex-trace-cass-ccusage/ · https://inventivehq.com/knowledge-base/openai/how-to-resume-sessions · https://github.com/neochoon/agenthud/blob/main/docs/schemas/codex-session.md · https://github.com/ccusage/ccusage/blob/main/docs/guide/codex/index.md

### JSONL fields

Each line is an envelope with `type`, `timestamp`, `payload`.

- Record type: `session_meta` / `turn_context` / `event_msg` / `response_item` / `input_item` / `config_snapshot`
- Timestamp: top-level `timestamp`
- Session: UUID in filename; also session_meta payload
- Project/cwd: `session_meta.payload.cwd` (agenthud)
- Model: latest `turn_context` payload.model
- Token event: type==event_msg and payload.type==token_count
- Per-turn: `payload.info.last_token_usage`; cumulative: `payload.info.total_token_usage`

Token object (ccusage Codex parser https://github.com/ccusage/ccusage/blob/main/rust/crates/ccusage/src/adapter/codex/parser.rs): `input_tokens` (includes cached share), `cached_input_tokens`, `output_tokens` (includes reasoning), `reasoning_output_tokens` (already inside output), `total_tokens`.

Non-cached input = input_tokens - cached_input_tokens. ccusage clamps cached_input_tokens <= input_tokens.

### How community tools parse it

- ccusage `ccusage codex daily`: subtracts consecutive total_token_usage when last_token_usage missing; skips events with no turn_context model; does not bill reasoning_output_tokens separately.
- https://github.com/crisxuan/codex-usage — same token_count events.
- Token events first appeared in openai/codex commit 0269096 (2025-09-06). Older rollouts have no usage.

### Pitfalls

- Cumulative vs delta: summing total_token_usage double-counts. Prefer last_token_usage or adjacent-total subtraction.
- Reasoning double-count: reasoning_output_tokens is already in output_tokens. Official OpenAI pricing has no separate reasoning rate.
- No cache-write in these logs; only cached_input_tokens (reads).
- Timezone: per-record timestamp is the billing clock; convert to UTC.
- Sub-agents are separate rollout files (session_meta.source / parent_thread_id). Whether parent totals include children is unknown.

---

## 3. Grok / xAI CLI — paths **confirmed**, billable token schema **partial / conflicting**

### Default paths / globs

Official: https://docs.x.ai/build/features/sessions · https://docs.x.ai/build/cli/reference · https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/17-sessions.md

`${GROK_HOME:-~/.grok}/sessions/<encoded-cwd>/<session-id>/` contains `summary.json`, `updates.jsonl`, `chat_history.jsonl`, `signals.json`. grokscope also mentions `events.jsonl` (not in the grok-build tree listing).

GROK_HOME overrides the base. cwd is URL-encoded; if over 255 bytes, slug+hash plus a `.cwd` file.

Community extras (not official):
- `~/.grok/logs/unified.jsonl` — https://github.com/robinebers/openusage/issues/646 claims prompt_tokens / cached_prompt_tokens / completion_tokens
- `~/.grok-tap/index.jsonl` — grokscope reverse-proxy tap; per-API-call usage only if the tap is running

### Fields

- `summary.json` (official): summary/title, model ID (`current_model_id` in community parsers), created/updated timestamps, message counts, parent session
- `signals.json` (official: token/tool/turn counters). Exact keys not fully specified. Community (https://raw.githubusercontent.com/getagentseal/codeburn/master/docs/providers/grok.md): modelsUsed, toolsUsed, contextTokensUsed
- `updates.jsonl` (official ACP stream). Community: params._meta.totalTokens (running context size), params._meta.promptId
- unified.jsonl (openusage #646 only, unconfirmed until seen on disk): msg, ts, sid, ctx.model, prompt_tokens, cached_prompt_tokens, completion_tokens

### How community tools parse it

- ccusage: no Grok adapter.
- grokscope https://github.com/daniel-farina/grokscope — summary.json + updates.jsonl totalTokens + optional tap
- codeburn grok.md — estimates I/O from per-turn totalTokens curve; flags costIsEstimated; cache forced to 0
- openusage #646 — proposes joining unified.jsonl rows to model via model-changed events / summary.json

### Pitfalls

- No official billable I/O split. Official session docs do not document prompt_tokens / completion_tokens. Using totalTokens as input+output overcounts (context fill including cached replay).
- Do not invent a schema. If unified.jsonl is absent, do not assume OpenAI-style usage objects exist.
- Long-context pricing: official xAI rates rise for the entire request once prompt is >=200k (https://docs.x.ai/developers/pricing). One model id has two rate cards.
- Timezone: ts in the issue sample is ISO with offset; some estimators use session updated_at for the whole session (coarse).

---

## 4. Gemini CLI — paths **confirmed**, token keys **confirmed with aliases**

Official session docs: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/session-management.md · https://geminicli.com/docs/cli/session-management/

`${GEMINI_DATA_DIR:-~/.gemini/tmp}/<project_hash>/chats/session-*.jsonl` and `session-*.json`. project_hash is from the project root. Settings: `~/.gemini/settings.json`. Default retention 30 days.

ccusage GEMINI_DATA_DIR: https://github.com/ccusage/ccusage/blob/main/docs/guide/environment-variables.md

Official docs say sessions store token usage (input, output, cached) but do not publish a JSON schema.

Confirmed by ccusage Gemini parser https://github.com/ccusage/ccusage/blob/main/rust/crates/ccusage/src/adapter/gemini/parser.rs and tokenuse https://tokenuse.app/docs/development/tools/gemini/

Envelope keys: sessionId or session_id; model; timestamp / created_at / startTime / lastUpdated; tokens object (or nested under stats / result / messages[]).

Token aliases (ccusage parse_tokens):
- input: input, prompt, input_tokens, prompt_tokens
- output: output, candidates, output_tokens, candidates_tokens
- cache read: cached, cached_tokens
- reasoning: thoughts, reasoning, thoughts_tokens, reasoning_tokens
- tool: tool, tool_tokens
- total: total, total_tokens

tokenuse mapping (not official): uncached input = tokens.input - tokens.cached; they fold thoughts into output for billing; cache write always 0.

Community example message: type=gemini, timestamp ISO Z, model=gemini-2.5-pro, tokens={input, output, cached, thoughts}. Session has sessionId, projectHash, startTime.

### How community tools parse it

- ccusage gemini daily: JSON+JSONL, alias-tolerant, cache pulled out of input, reasoning as metadata, LiteLLM prices. Added in https://github.com/ccusage/ccusage/commit/4a7c880d1a4a2fa3ddc37403a4b511b78eb7c0a7
- tokenuse: session-*.json(l) under each hash; ignores JSONL $set lines.

### Pitfalls

- Thinking billed as output (official: Output price including thinking tokens). Adding thoughts on top of output double-counts.
- Cached often included in input; subtract cached before the input rate.
- Long context: 2.5 Pro and 3.1 Pro Preview have a higher tier above 200k. Table stores <=200k. https://ai.google.dev/gemini-api/docs/pricing
- Project label is a hash, not cwd. Extra project fields unknown.
- Timestamps in samples are UTC Z. Retention 30 days.

---

## 5. Aider — analytics schema **confirmed** (opt-in); chat history is **not** JSON

Official: https://aider.chat/docs/config/options.html · https://aider.chat/docs/more/analytics.html

- `<repo>/.aider.chat.history.md` — Markdown transcript (default; --chat-history-file / AIDER_CHAT_HISTORY_FILE)
- `<repo>/.aider.input.history` — input history (default)
- path from `--analytics-log` — JSONL, **opt-in**
- `~/.aider/analytics.jsonl` — conventional in Aider scripts (https://github.com/Aider-AI/aider/blob/main/scripts/my_models.py), not a documented default

ccusage: no Aider adapter.

Structured fields (analytics JSONL only). Official sample: https://github.com/Aider-AI/aider/blob/main/aider/website/assets/sample-analytics.jsonl

event == message_send:
- time: Unix **seconds** (not ms, not ISO)
- properties.main_model (also weak_model, editor_model)
- properties.prompt_tokens / completion_tokens / total_tokens
- properties.cost (USD this send); properties.total_cost (running)
- session/project: **unknown**. user_id is an anonymous UUID. cwd not in the sample.

Enable without uploading: `aider --analytics-log filename.jsonl --no-analytics`.

Chat-history fallback is unstructured. Lines like `> Tokens: 38k sent, 1.1k received.` (https://github.com/Aider-AI/aider/issues/2219). No JSON schema; parsing is heuristic and not confirmed stable.

### Pitfalls

- No structured log unless opted in. Most installs have only markdown history.
- total_cost is cumulative; summing it double-counts. Use per-event cost or token x table.
- time is Unix epoch UTC.
- weak_model / editor_model may consume tokens. Whether prompt_tokens is only main_model is unknown.
- Cache / reasoning not in the sample analytics event.

---

## 6. OpenCode — schema **confirmed** (SQLite + legacy JSON)

- `${OPENCODE_DATA_DIR:-~/.local/share/opencode}/opencode.db` — current (v1.2.2+ / v1.14+)
- `opencode-*.db` also scanned by ccusage Rust loader
- legacy `storage/message/*.json` and `storage/session/` — empty on new installs

Sources: https://github.com/ccusage/ccusage/issues/966 · https://github.com/ccusage/ccusage/pull/982 · https://github.com/ccusage/ccusage/blob/main/rust/crates/ccusage/src/adapter/opencode/loader.rs

SQLite tables (issue 966 dump): session (id, title, project_id, directory, time_created, time_updated); message (id, session_id, time_created, data JSON); part (content, not tokens).

message.data JSON (same as legacy files): role, modelID, providerID, cost, tokens.total / input / output / reasoning, tokens.cache.read / write. Legacy also uses sessionID and time.created (Unix ms).

ccusage: SQLite first (SELECT id, session_id, data FROM message), then legacy JSON; DB wins on id collision; skip rows without billable tokens/model.

### Pitfalls

- cost is often 0 for subscription providers. Prefer table + tokens.
- modelID plus providerID (e.g. claude-sonnet-4.6 via github-copilot) can mean different bills.
- time.created is Unix ms UTC. Do not also sum tokens.total (already includes input/output/cache).

---

## 7. Amp — schema **confirmed** via ccusage (not an official Amp spec)

`${AMP_DATA_DIR:-~/.local/share/amp}/threads/T-*.json` (recursive **/*.json under threads/). `~/.cache/amp/logs/cli.log` is log text, not usage.

Sources: https://github.com/ccusage/ccusage/blob/main/docs/guide/amp/index.md · https://github.com/ccusage/ccusage/blob/main/apps/amp/src/data-loader.ts · https://allaboutcoding.ghinda.com/where-ai-coding-clis-store-session-logs/

Amp public docs emphasize the server copy. Local files are a mirror. No official field spec. Schema below is ccusage Valibot + fixtures.

Thread: id (T-uuid), created (Unix ms), title, messages[], usageLedger.events[].
Message usage (camelCase): model, inputTokens, outputTokens, cacheCreationInputTokens, cacheReadInputTokens, totalInputTokens, credits.
Ledger event: timestamp ISO, model, credits, tokens.input / tokens.output (no cache), operationType.
Project/cwd: unknown in the ccusage fixture (blog mentions env/meta, not typed).

ccusage prefers ledger events for input/output and message usage for cache + credits.

### Pitfalls

- Ledger vs message double-count: do not sum both.
- Credits are not USD. Credit-to-USD rate unknown.
- Server is source of truth; local mirror may be incomplete. Ledger timestamps UTC Z; created is ms epoch.

---

## 8. GitHub Copilot CLI — export **must be enabled**; OTEL field names **confirmed**

No usage JSONL is written by default. ccusage: https://github.com/ccusage/ccusage/blob/main/docs/guide/copilot/index.md

Paths: `~/.copilot/otel/*.jsonl` and `$COPILOT_OTEL_FILE_EXPORTER_PATH` (single file).

Enable before the session (and before copilot --resume): COPILOT_OTEL_ENABLED=true, COPILOT_OTEL_EXPORTER_TYPE=file, COPILOT_OTEL_FILE_EXPORTER_PATH=...jsonl

Official OTEL config: https://docs.github.com/en/copilot/how-tos/copilot-sdk/observability/opentelemetry
Same spans described at https://last9.io/docs/integrations/github-copilot/ and https://openobserve.ai/docs/integration/ai/github-copilot-tracing/

Spans: invoke_agent (turn), chat (LLM call), execute_tool.
- model: gen_ai.request.model / gen_ai.response.model
- input: gen_ai.usage.input_tokens
- output: gen_ai.usage.output_tokens
- cache read: gen_ai.usage.cache_read.input_tokens (ccusage commit 14658ff)
- cache write: gen_ai.usage.cache_creation.input_tokens
- session: gen_ai.conversation.id
- project: github.copilot.git_repository (OpenObserve; may be absent)
- timestamp: OTEL span start (UTC)

Prefer chat spans. Exact JSONL envelope is OTEL JSON-lines, not a Copilot-specific object. ccusage commit https://github.com/ccusage/ccusage/commit/14658ff5b61b06bbb1315e2c200907bbe587065e subtracts cached from input and ignores exported Copilot cost fields.

### Pitfalls

- Silent empty if env vars were off. Subscription vs API: table prices are API-equivalent estimates.
- Subtract cache-read from input before the input rate.
- Do not enable COPILOT_OTEL_CAPTURE_CONTENT (captures prompts/responses). This project reads usage metadata only.

---

## Pricing notes (how the JSON table was filled)

Retrieved 2026-08-18 from official pages only. See docs/PRICING_SOURCES.md.

### Anthropic (https://platform.claude.com/docs/en/about-claude/pricing)

cache_write = 5-minute cache write. 1-hour writes are 2x base input (not stored). Cache hits = 0.1x input.
Sonnet 5 $2/$10 is permanent (increase cancelled). IDs: https://platform.claude.com/docs/en/about-claude/models/overview and model-ids-and-versions. Pre-4.6 IDs are dated snapshots; 4.6+ IDs are dateless pinned snapshots.
Opus 4.1 / Opus 4 / Sonnet 4 / Haiku 3.5 are retired on the first-party API except Bedrock / Google Cloud, but still listed with prices.
Thinking is billed as output. Fast mode (Opus 5 / 4.8) is a separate 2x card, not a reasoning field.

### OpenAI (https://developers.openai.com/api/docs/pricing + per-model pages)

Flagship 2026-08-18: gpt-5.6-sol / terra / luna. Table stores short-context rates. Long context (second column; >272K often 2x input / 1.5x output) is not in the table.
cache_write filled only for the 5.6 family (pricing page Cache writes column). Older model pages list Input / Cached input / Output only.
Codex: gpt-5.3-codex (Specialized models), gpt-5.2-codex, gpt-5.1-codex (model pages).
o-series: o3 $2 / $0.50 cached / $8; o4-mini $1.10 / $0.275 / $4.40. Reasoning billed as output.
Omitted: GPT-4.1 nano (official page fetch failed); GPT-5.4 / 5.5 families (not on the flagship table retrieved today — do not copy third-party dumps).

### xAI (https://docs.x.ai/developers/pricing)

Stored rates are the <200k prompt card. At >=200k, official docs bill all tokens in the request at the higher row (typically 2x). No cache-write column. Reasoning billed at output.

### Google (https://ai.google.dev/gemini-api/docs/pricing · https://ai.google.dev/gemini-api/docs/gemini-3)

Stored rates are paid-tier <=200k (or the single flat rate). 2.5 Pro output $10 (<=200k) / $15 (>200k). 3.1 Pro Preview $2/$12 and $4/$18. Thinking included in output.
Cache-read filled where the official table lists it (2.5 Pro $0.125, 2.5 Flash $0.03, 2.5 Flash-Lite $0.01, 3.5 Flash $0.15). 3.1 / 3-flash-preview cache-read unknown on pages retrieved -> null. Cache storage is $/hour, not a write token.
Audio input for Flash is a higher rate ($1.00 on 2.5 Flash) — not in the table (text/image/video rate stored).

---

## Confirmed vs guessed (summary for implementers)

| Tool | Paths | Field names | Parser reference |
| --- | --- | --- | --- |
| Claude Code | Confirmed (ccusage + widespread) | Confirmed (message.usage.*) | ccusage data-loader |
| Codex CLI | Confirmed (~/.codex/sessions/...) | Confirmed (token_count / last_token_usage) | ccusage Codex parser; not an OpenAI spec |
| Grok CLI | Confirmed session dir (official) | Partial. Official: model + totalTokens-style context. Billable I/O only claimed in openusage 646 | grokscope / codeburn estimates |
| Gemini CLI | Confirmed (~/.gemini/tmp/<hash>/chats/) | Confirmed aliases via ccusage, not an official schema | ccusage Gemini parser |
| Aider | Confirmed defaults; analytics path opt-in | Confirmed for message_send analytics; chat history unstructured | Aider sample JSONL |
| OpenCode | Confirmed | Confirmed tokens.{input,output,reasoning,cache} | ccusage + issue 966 dump |
| Amp | Confirmed conventional path | Confirmed only via ccusage fixtures | ccusage amp data-loader |
| Copilot CLI | Confirmed if OTEL file export on | Confirmed GenAI OTEL attributes | GitHub OTEL docs + ccusage |

Do not guess Grok prompt_tokens if unified.jsonl is missing. Do not parse Aider markdown as JSON. Do not expect Copilot JSONL without the env vars.
