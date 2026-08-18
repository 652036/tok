# Pricing sources

`src/tok/pricing_data.json` is **not** pulled from a live official vendor API.
It is a snapshot of the same LiteLLM-style table [sub2API](https://github.com/Wei-Shaw/sub2api) caches:

1. Remote (what we vendor): https://raw.githubusercontent.com/Wei-Shaw/model-price-repo/main/model_prices_and_context_window.json
2. Upstream LiteLLM fallback: https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
3. sub2API also has a copy at `backend/resources/model-pricing/model_prices_and_context_window.json` and a few hardcoded fallbacks — we do not copy those.

[Wei-Shaw/model-price-repo](https://github.com/Wei-Shaw/model-price-repo) syncs from LiteLLM and applies prefix filters / aliases. LiteLLM fields are USD **per token**; the importer multiplies by `1e6` and writes USD **per 1 million tokens** (`unit: per_1m_tokens`). Missing LiteLLM fields stay `null`. Values are rounded to at most 6 decimals. This project does not invent rates.

The checked-in dump is `docs/vendor/model_prices_and_context_window.json`. Runtime pricing is offline: `tok` only reads the bundled JSON.

## Refresh

```bash
# 1. Refresh the vendor dump (Wei-Shaw first; LiteLLM if that URL fails)
curl -fsSL https://raw.githubusercontent.com/Wei-Shaw/model-price-repo/main/model_prices_and_context_window.json \
  -o docs/vendor/model_prices_and_context_window.json \
|| curl -fsSL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
  -o docs/vendor/model_prices_and_context_window.json

# 2. Rebuild pricing_data.json
python scripts/import_litellm_prices.py
```

`scripts/import_litellm_prices.py`:

- skips keys starting with `_` (`sample_spec`, etc.)
- includes `chat` / `completion` / `responses`
- skips `embedding` / `image_generation` / `audio*` / `realtime` unless both `input_cost_per_token` and `output_cost_per_token` are present
- skips rows with neither input nor output token cost
- maps `vertex_ai-language-models` → `google`, `text-completion-openai` → `openai`; Bedrock Anthropic ids stay `bedrock`
- adds aliases: undated id when the key has a date suffix (`-YYYYMMDD` or `-YYYY-MM-DD`); `provider/id` when the key is not already prefixed

## Field mapping

| Our field | LiteLLM field | Notes |
| --- | --- | --- |
| `input` | `input_cost_per_token` | × 1e6 |
| `output` | `output_cost_per_token` | × 1e6 |
| `cache_read` | `cache_read_input_token_cost` | × 1e6 |
| `cache_write` | `cache_creation_input_token_cost` | × 1e6 (standard / 5-minute write when LiteLLM publishes one) |
| `reasoning` | `output_cost_per_reasoning_token` | × 1e6; `null` if omitted (then `price_event` bills reasoning at the output rate) |
| `provider` | `litellm_provider` | normalized as above |

## Official vendor pages (reference only)

These pages are useful for spot-checking a rate. They are **not** what `pricing_data.json` is generated from. Retrieved 2026-08-18.

### Anthropic

- https://platform.claude.com/docs/en/about-claude/pricing
- https://platform.claude.com/docs/en/about-claude/models/overview
- https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions
- https://claude.com/pricing
- https://www.anthropic.com/news/claude-sonnet-5
- https://www.anthropic.com/claude/opus

### OpenAI

- https://developers.openai.com/api/docs/pricing
- https://openai.com/api/pricing/
- https://developers.openai.com/api/docs/models/compare

### xAI

- https://docs.x.ai/developers/pricing
- https://docs.x.ai/developers/models

### Google

- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/pricing.md.txt

## Not a live API

Do not treat this table as an invoice, a subscription bill, or a guaranteed official rate card. LiteLLM / model-price-repo can lag or disagree with a vendor page. If a model is missing, `tok` counts tokens and leaves USD unpriced.
