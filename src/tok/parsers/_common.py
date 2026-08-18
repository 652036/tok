"""Shared best-effort helpers for local usage-log parsers.

Public-safety rules (this repo is MIT / open-source):
- Never copy prompts, completions, thoughts text, or tool arguments.
- Never copy API keys, OAuth tokens, cookies, or other credentials.
- Skip credential-looking filenames entirely (auth.json, secrets, oauth, ...).
- ``UsageEvent.extra`` may only hold small numeric/flag metadata.

These helpers exist so each tool parser can stay small and so outside
contributors have one place to extend key aliases.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tok.models import UsageEvent

# Filenames / path fragments that often hold secrets. Never open these.
_SECRET_NAME_MARKERS = (
    "auth",
    "oauth",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "token.json",
    "api_key",
    "apikey",
    "id_rsa",
    "private_key",
    "cookie",
)

# Directory names we never descend into (noise or huge trees).
_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "bin",
    "crash",
    ".cache",
}

# JSON keys that typically hold prose or secrets. We still *walk* list
# containers named "messages" to find nested usage objects, but we never
# store the string values.
_CONTENT_KEYS = {
    "content",
    "text",
    "prompt",
    "completion",
    "message",
    "body",
    "thoughts",
    "thought",
    "reasoning_content",
    "displaycontent",
    "display_content",
    "toolcalls",
    "tool_calls",
    "tool_arguments",
    "args",
    "arguments",
    "input",  # only skipped when the value is a string
    "output",
    "delta",
    "system",
}

_CREDENTIAL_KEYS = {
    "api_key",
    "apikey",
    "apiKey",
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "secret",
    "password",
    "cookie",
    "credentials",
    "auth",
    "token",  # string tokens only; numeric "token" is ignored here
}

INPUT_KEYS = (
    "input_tokens",
    "inputTokens",
    "uncached_input_tokens",
    "uncachedInputTokens",
    "prompt_tokens",
    "promptTokens",
    "prompt_token_count",
    "promptTokenCount",
    "sent",
    "tokens_input",
    "tokensInput",
)
# "input" is handled separately: numeric only (string "input" is content).

OUTPUT_KEYS = (
    "output_tokens",
    "outputTokens",
    "completion_tokens",
    "completionTokens",
    "candidates_token_count",
    "candidatesTokenCount",
    "received",
    "tokens_output",
    "tokensOutput",
)

CACHE_READ_KEYS = (
    "cache_read_tokens",
    "cacheReadTokens",
    "cachedReadTokens",
    "cache_read",
    "cacheRead",
    "cached_tokens",
    "cachedTokens",
    "cached",
    "cacheReadInputTokens",
    "cache_read_input_tokens",
    "cached_input_tokens",
    "cachedInputTokens",
    "tokens_cache_read",
    "tokensCacheRead",
)

CACHE_WRITE_KEYS = (
    "cache_write_tokens",
    "cacheWriteTokens",
    "cacheCreationTokens",
    "cache_write",
    "cacheWrite",
    "cacheCreationInputTokens",
    "cache_creation_input_tokens",
    "cache_creation_tokens",
    "cacheCreation",
    "tokens_cache_write",
    "tokensCacheWrite",
)

REASONING_KEYS = (
    "reasoning_tokens",
    "reasoningTokens",
    "reasoning",
    "thoughts",
    "thoughts_tokens",
    "thoughtsTokens",
    "thought",
    "reasoning_output_tokens",
    "reasoningOutputTokens",
    "tokens_reasoning",
    "tokensReasoning",
)

COST_USD_KEYS = (
    "raw_cost_usd",
    "cost_usd",
    "costUsd",
    "total_cost_usd",
    "totalCostUsd",
    "total_cost",
    "totalCost",
)

MODEL_KEYS = (
    "model",
    "model_id",
    "modelId",
    "modelID",
    "model_name",
    "modelName",
    "current_model_id",
    "currentModelId",
    "primary_model_id",
    "primaryModelId",
    "main_model",
    "mainModel",
)

SESSION_KEYS = (
    "session_id",
    "sessionId",
    "sessionID",
    "thread_id",
    "threadId",
    "conversation_id",
    "conversationId",
)

PROJECT_KEYS = (
    "project",
    "project_id",
    "projectId",
    "projectID",
    "project_hash",
    "projectHash",
    "cwd",
    "git_root_dir",
    "gitRootDir",
    "directory",
    "worktree",
    "workspace",
)

TIMESTAMP_KEYS = (
    "timestamp",
    "time",
    "created_at",
    "createdAt",
    "startTime",
    "start_time",
    "lastUpdated",
    "last_updated",
    "updated_at",
    "updatedAt",
    "last_active_at",
    "lastActiveAt",
    "created",
    "date",
)

JSON_SUFFIXES = {".json", ".jsonl", ".log"}

# Skip giant files (best-effort; samples are tiny).
_MAX_FILE_BYTES = 32 * 1024 * 1024


def usage_home() -> Path:
    """Return ``$TOK_HOME`` (preferred) or ``$AI_USAGE_HOME``, else ``Path.home()``."""
    from tok.discover import default_home

    return default_home()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def env_roots(*env_names: str) -> list[Path]:
    """Extra roots from official tool env vars.

    When ``TOK_HOME`` / ``AI_USAGE_HOME`` is set (tests / sandboxes), only
    keep env roots that live under that home so we never leak into a real
    user tree.
    """
    from tok.discover import env_home_override

    override = env_home_override()
    override_path = Path(override).expanduser() if override else None
    found: list[Path] = []
    for name in env_names:
        raw = os.environ.get(name)
        if not raw:
            continue
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            p = Path(part).expanduser()
            if override_path is not None and not _is_under(p, override_path):
                continue
            found.append(p)
    return found


def default_roots(*relative: str, env_vars: tuple[str, ...] = ()) -> list[Path]:
    """Typical dirs under ``usage_home()`` plus optional official env roots."""
    home = usage_home()
    roots = [home.joinpath(*rel.split("/")) for rel in relative]
    roots.extend(env_roots(*env_vars))
    # De-dupe while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        key = r
        try:
            key = r.resolve()
        except OSError:
            pass
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out



def _is_home_dir(path: Path) -> bool:
    """True when *path* is ``$TOK_HOME`` / ``$AI_USAGE_HOME`` or the real user home.

    Parsers must not recursively scan a home directory (too slow, and
    aider history files live in every repo).
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for candidate in (usage_home(), Path.home()):
        try:
            if resolved == candidate.resolve():
                return True
        except OSError:
            continue
    return False


def resolve_tool_roots(
    root: Path | None,
    *relative: str,
    env_vars: tuple[str, ...] = (),
    undotted_name: str | None = None,
    extra_home_files: tuple[str, ...] = (),
) -> list[Path]:
    """Map ``parse(root)`` onto tool data dirs.

    Accepts the same shapes as the Claude/Codex parsers so
    ``load_all_events(home)`` works:

    * ``root is None`` — ``$TOK_HOME`` / ``Path.home()`` plus *relative*
    * a home directory that contains ``.tool`` or undotted ``tool/``
    * the tool data directory itself (``~/.grok`` or ``sample_data/grok``)
    * a single file

    A real home directory is never returned as a walk root.
    """
    if root is None:
        roots = default_roots(*relative, env_vars=env_vars)
        home = usage_home()
        for name in extra_home_files:
            cand = home / name
            if cand.is_file():
                roots.append(cand)
        return _dedupe_paths(roots)

    root = Path(root)
    try:
        if root.is_file():
            return [root]
    except OSError:
        return [root]

    tool_names = {rel.split("/")[-1] for rel in relative}
    if undotted_name:
        tool_names.add(undotted_name)
    # sample_data/grok or ~/.grok — already a tool tree
    if root.name in tool_names:
        return [root]

    found: list[Path] = []
    for rel in relative:
        cand = root.joinpath(*rel.split("/"))
        try:
            if cand.exists():
                found.append(cand)
        except OSError:
            continue
    if undotted_name:
        cand = root / undotted_name
        try:
            if cand.exists():
                found.append(cand)
        except OSError:
            pass
    for name in extra_home_files:
        cand = root / name
        try:
            if cand.is_file():
                found.append(cand)
        except OSError:
            continue
    if found:
        return _dedupe_paths(found)

    # Explicit project / data dir (not a home). Caller may walk it.
    if _is_home_dir(root):
        return []
    return [root]


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for r in paths:
        key = r
        try:
            key = r.resolve()
        except OSError:
            pass
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in _SECRET_NAME_MARKERS)


def iter_files(
    roots: Iterable[Path],
    *,
    suffixes: Iterable[str] = JSON_SUFFIXES,
    extra_names: Iterable[str] = (),
    extra_suffixes: Iterable[str] = (),
) -> Iterator[Path]:
    """Yield readable candidate files under ``roots``.

    ``roots`` may be files or directories. Unreadable entries are skipped.
    """
    suffix_set = {s.lower() if s.startswith(".") else f".{s.lower()}" for s in suffixes}
    suffix_set.update(
        s.lower() if s.startswith(".") else f".{s.lower()}" for s in extra_suffixes
    )
    extra = {n.lower() for n in extra_names}

    for root in roots:
        try:
            if root.is_file():
                if not is_secret_path(root):
                    yield root
                continue
            if not root.is_dir():
                continue
        except OSError:
            continue
        yield from _walk_dir(root, suffix_set, extra)


def _walk_dir(root: Path, suffix_set: set[str], extra: set[str]) -> Iterator[Path]:
    try:
        iterator = root.rglob("*")
    except OSError:
        return
    for path in iterator:
        try:
            if not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            if is_secret_path(path):
                continue
            name = path.name.lower()
            if path.suffix.lower() in suffix_set or name in extra:
                yield path
        except OSError:
            continue


def read_text(path: Path) -> str | None:
    """Read a text file. Returns None on any I/O / decode error or if huge."""
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def iter_json_objects(path: Path) -> Iterator[Any]:
    """Yield JSON values from a ``.json`` document or a ``.jsonl``/``.log`` file.

    Malformed lines / documents are skipped.
    """
    text = read_text(path)
    if text is None:
        return
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".log"} or _looks_like_jsonl(text):
        yield from _iter_jsonl(text)
        return
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Some "json" session files are actually JSONL (Gemini 2026).
        yield from _iter_jsonl(text)
        return
    if value is not None:
        yield value


def _looks_like_jsonl(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return False
    # Two top-level objects on separate lines => JSONL.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    return lines[0].startswith("{") and lines[1].startswith("{")


def _iter_jsonl(text: str) -> Iterator[Any]:
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def as_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        if value < 0:
            return 0
        return int(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return 0
        try:
            n = float(s)
        except ValueError:
            return 0
        if n < 0:
            return 0
        return int(n)
    return 0


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def file_mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def parse_timestamp(value: Any, fallback: datetime | None = None) -> datetime:
    """Parse ISO-8601, unix seconds/ms, or ``YYYY-MM-DD[ HH:MM:SS]`` as UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:  # milliseconds
            ts /= 1000.0
        elif ts > 1e11:  # microseconds-ish; treat as ms
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return fallback or datetime.fromtimestamp(0, tz=timezone.utc)
    if isinstance(value, dict):
        for key in TIMESTAMP_KEYS:
            if key in value:
                return parse_timestamp(value[key], fallback)
        return fallback or datetime.fromtimestamp(0, tz=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return fallback or datetime.fromtimestamp(0, tz=timezone.utc)
        if s.replace(".", "", 1).isdigit():
            return parse_timestamp(float(s), fallback)
        iso = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return fallback or datetime.fromtimestamp(0, tz=timezone.utc)


def pick(obj: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
    return None


def _numeric(obj: dict[str, Any], keys: Iterable[str]) -> int:
    for key in keys:
        if key in obj and not isinstance(obj[key], (str, dict, list)):
            n = as_int(obj[key])
            # as_int returns 0 for junk; accept explicit 0.
            if obj[key] is not None:
                return n
    return 0


def _nested_cache(obj: dict[str, Any]) -> tuple[int, int]:
    cache = obj.get("cache")
    if not isinstance(cache, dict):
        return 0, 0
    read = _numeric(cache, ("read", "cache_read", "cacheRead") + CACHE_READ_KEYS)
    write = _numeric(cache, ("write", "cache_write", "cacheWrite") + CACHE_WRITE_KEYS)
    return read, write


def extract_tokens(obj: dict[str, Any]) -> dict[str, int]:
    """Pull token counts from a usage-like dict (many vendor aliases)."""
    # Prefer an inner usage/tokens object when the outer dict is a wrapper.
    inner = None
    for key in ("usage", "tokens", "token_usage", "tokenUsage", "usage_metadata", "usageMetadata"):
        cand = obj.get(key)
        if isinstance(cand, dict) and _has_token_key(cand):
            inner = cand
            break
    src = inner or obj

    input_tokens = _numeric(src, INPUT_KEYS)
    if input_tokens == 0 and isinstance(src.get("input"), (int, float)):
        input_tokens = as_int(src["input"])
    output_tokens = _numeric(src, OUTPUT_KEYS)
    if output_tokens == 0 and isinstance(src.get("output"), (int, float)):
        output_tokens = as_int(src["output"])
    cache_read = _numeric(src, CACHE_READ_KEYS)
    cache_write = _numeric(src, CACHE_WRITE_KEYS)
    if cache_read == 0 and cache_write == 0:
        cr, cw = _nested_cache(src)
        cache_read, cache_write = cr, cw
    reasoning = _numeric(src, REASONING_KEYS)
    if reasoning == 0 and isinstance(src.get("reasoning"), (int, float)):
        reasoning = as_int(src["reasoning"])

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
    }


def extract_cost_usd(obj: dict[str, Any]) -> float | None:
    """Return a recorded USD cost if one is clearly present.

    ``costUsdTicks`` (Grok Build, 1e-10 USD) is converted here.
    Amp-style ``credits`` are *not* treated as USD.
    """
    for key in COST_USD_KEYS:
        if key in obj:
            val = as_float(obj[key])
            if val is not None:
                return val
    if "cost" in obj and not isinstance(obj["cost"], dict):
        val = as_float(obj["cost"])
        if val is not None:
            return val
    ticks = obj.get("costUsdTicks")
    if ticks is None:
        ticks = obj.get("cost_usd_ticks")
    if ticks is not None:
        n = as_float(ticks)
        if n is not None:
            return n / 1e10
    inner = obj.get("usage")
    if isinstance(inner, dict):
        return extract_cost_usd(inner)
    return None


def _has_token_key(obj: dict[str, Any]) -> bool:
    token_keys = (
        set(INPUT_KEYS)
        | set(OUTPUT_KEYS)
        | set(CACHE_READ_KEYS)
        | set(CACHE_WRITE_KEYS)
        | set(REASONING_KEYS)
        | {"input", "output", "tokens", "usage", "cache"}
    )
    return any(k in obj for k in token_keys)


def looks_like_usage(obj: dict[str, Any]) -> bool:
    if not _has_token_key(obj):
        return False
    tokens = extract_tokens(obj)
    return any(tokens.values()) or extract_cost_usd(obj) is not None


def walk_usage_dicts(obj: Any, *, _depth: int = 0) -> Iterator[dict[str, Any]]:
    """Yield nested dicts that look like usage records.

    Recurses through ``messages`` / ``events`` lists so Gemini/Amp-style
    session files work, but does not yield or retain string content.
    """
    if _depth > 12:
        return
    if isinstance(obj, dict):
        if looks_like_usage(obj):
            yield obj
        for key, value in obj.items():
            low = str(key).lower()
            if low in _CREDENTIAL_KEYS:
                continue
            if low in _CONTENT_KEYS and isinstance(value, str):
                continue
            if isinstance(value, (dict, list)):
                yield from walk_usage_dicts(value, _depth=_depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_usage_dicts(item, _depth=_depth + 1)


def _stringify_model(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        inner = pick(value, "id", "name", "model", "model_id", "modelId", "modelID")
        if isinstance(inner, str) and inner.strip():
            provider = pick(value, "providerID", "provider_id", "provider", "providerId")
            if isinstance(provider, str) and provider.strip() and "/" not in inner:
                return f"{provider.strip()}/{inner.strip()}"
            return inner.strip()
    return ""


def safe_model(obj: dict[str, Any], default: str = "unknown") -> str:
    for key in MODEL_KEYS:
        if key in obj:
            name = _stringify_model(obj[key])
            if name:
                return name
    # OpenCode SQLite / session.model is itself {"id", "providerID"}.
    if "id" in obj and any(k in obj for k in ("providerID", "provider_id", "provider")):
        name = _stringify_model(obj)
        if name:
            return name
    for wrapper in ("usage", "assistant", "metadata", "info"):
        inner = obj.get(wrapper)
        if isinstance(inner, dict):
            name = safe_model(inner, default="")
            if name:
                return name
    return default


def safe_session_id(obj: dict[str, Any], fallback: str | None = None) -> str | None:
    for key in SESSION_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return str(int(val))
    for wrapper in ("metadata", "info", "assistant"):
        inner = obj.get(wrapper)
        if isinstance(inner, dict):
            found = safe_session_id(inner, None)
            if found:
                return found
    # Bare "id" only when it looks like a session/thread id, not a message id.
    ident = obj.get("id")
    if isinstance(ident, str) and ident.strip():
        low = ident.lower()
        if low.startswith(("ses", "sess", "session", "t-", "thread", "conv")):
            return ident.strip()
    return fallback


def safe_project(obj: dict[str, Any], fallback: str | None = None) -> str | None:
    for key in PROJECT_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return _project_label(val.strip())
    for wrapper in ("metadata", "info", "path", "assistant"):
        inner = obj.get(wrapper)
        if isinstance(inner, dict):
            found = safe_project(inner, None)
            if found:
                return found
    return fallback


def _project_label(raw: str) -> str:
    """Keep a short project label; never a home-directory dump of secrets."""
    raw = raw.strip()
    if "/" in raw or raw.startswith("~"):
        return Path(raw).name or raw
    return raw


def split_cache_inclusive_input(input_tokens: int, cache_read: int) -> tuple[int, int]:
    """If ``input`` already includes cache-read tokens, peel them apart."""
    if cache_read and input_tokens >= cache_read:
        return input_tokens - cache_read, cache_read
    return input_tokens, cache_read


def make_event(
    tool: str,
    path: Path,
    obj: dict[str, Any],
    *,
    timestamp: datetime | None = None,
    model: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
    inclusive_input: bool = False,
    tokens: dict[str, int] | None = None,
    raw_cost_usd: float | None = None,
) -> UsageEvent | None:
    """Build a UsageEvent from a usage-like dict. Returns None if empty."""
    tok = dict(tokens) if tokens is not None else extract_tokens(obj)
    if inclusive_input:
        inp, cr = split_cache_inclusive_input(
            tok["input_tokens"], tok["cache_read_tokens"]
        )
        tok["input_tokens"] = inp
        tok["cache_read_tokens"] = cr
    cost = raw_cost_usd if raw_cost_usd is not None else extract_cost_usd(obj)
    if not any(tok.values()) and cost is None:
        return None
    fallback_ts = file_mtime(path)
    ts = timestamp or parse_timestamp(pick(obj, *TIMESTAMP_KEYS), fallback_ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    safe_extra: dict[str, Any] = {}
    if extra:
        for k, v in extra.items():
            if isinstance(v, (int, float, bool, str)) and k.lower() not in _CREDENTIAL_KEYS:
                if isinstance(v, str) and len(v) > 200:
                    continue
                safe_extra[k] = v
    return UsageEvent(
        tool=tool,
        timestamp=ts,
        model=model or safe_model(obj),
        project=project if project is not None else safe_project(obj),
        input_tokens=tok["input_tokens"],
        output_tokens=tok["output_tokens"],
        cache_read_tokens=tok["cache_read_tokens"],
        cache_write_tokens=tok["cache_write_tokens"],
        reasoning_tokens=tok["reasoning_tokens"],
        raw_cost_usd=cost,
        source_path=str(path),
        session_id=session_id if session_id is not None else safe_session_id(obj),
        extra=safe_extra,
    )


def sibling_json(path: Path, *names: str) -> dict[str, Any]:
    """Load a sibling JSON object (e.g. Grok ``summary.json``)."""
    for name in names:
        cand = path.parent / name
        try:
            if not cand.is_file() or is_secret_path(cand):
                continue
        except OSError:
            continue
        for obj in iter_json_objects(cand):
            if isinstance(obj, dict):
                return obj
    return {}
