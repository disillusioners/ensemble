# OpenSpace Custom LLM Base URL Injection

**Date**: 2026-07-09
**Status**: Draft
**Impact**: OpenSpace built-in MCP server (STDIO transport)

## Problem

OpenSpace supports pointing its embedded LLM agent at a **custom provider base URL**
via the `OPENSPACE_LLM_API_BASE` env var (Tier-1 explicit override, highest priority
in OpenSpace's `host_detection/resolver.py`). This is required for:

- Self-hosted / on-prem LLM gateways (OpenAI-compatible endpoints, vLLM, LocalAI)
- Regional proxies and enterprise API gateways
- LiteLLM-proxy / Helicone / custom OpenRouter-style routers

**Ensemble's OpenSpace built-in does NOT forward `OPENSPACE_LLM_API_BASE`** to the
STDIO subprocess. As a result, even when a user sets it in `.env`, it never reaches
OpenSpace and the custom endpoint is silently ignored.

### Root cause

The MCP SDK's `stdio_client` calls `get_default_environment()`, which forwards only a
hardcoded **6-variable POSIX whitelist** (`HOME, LOGNAME, PATH, SHELL, TERM, USER`) —
**not** the full `os.environ`. Ensemble's `OpenSpaceServerDefinition.build_config()`
works around this for credentials, but its injection loop is hard-coded to only two
vars:

```python
# daemon/mcp/builtin_servers/openspace.py:258
for cred_env in ("OPENSPACE_LLM_API_KEY", "OPENSPACE_API_KEY"):
    value = os.environ.get(cred_env, "").strip()
    if value:
        config["env"][cred_env] = value
```

`OPENSPACE_LLM_API_BASE` is not in that loop, and there is no config-schema field
for it either. So it is dropped at the subprocess boundary.

### Why prefix-routing is insufficient

A partial workaround is `OPENSPACE_MODEL=<provider>/<model>` (LiteLLM prefix routing,
e.g. `openrouter/anthropic/claude-sonnet-4.5`). This works for **known providers**
whose base URLs LiteLLM has baked in, but fails for:

- Custom / private hostnames (`https://llm.internal.corp/v1`)
- Self-hosted OpenAI-compatible servers (vLLM, LocalAI, LM Studio)
- Proxies that don't map to any LiteLLM provider id

These cases explicitly require `OPENSPACE_LLM_API_BASE`.

## OpenSpace LLM env resolution (reference)

From `openspace/host_detection/resolver.py::build_llm_kwargs()` (Tier 1 → 3 priority):

| Env var | Maps to (litellm kwarg) | Priority tier |
|---------|-------------------------|---------------|
| `OPENSPACE_LLM_API_KEY` | `api_key` | 1 (explicit override) |
| **`OPENSPACE_LLM_API_BASE`** | **`api_base`** | **1 (explicit override)** |
| `OPENSPACE_LLM_EXTRA_HEADERS` | `extra_headers` (JSON) | 1 |
| `OPENSPACE_LLM_CONFIG` | any kwarg (JSON catch-all) | 1 |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / ... | provider-native | 2 |
| nanobot/openclaw host config | host config | 3 (fallback) |

`openspace/.env.example` confirms usage:
```bash
OPENSPACE_MODEL=openrouter/anthropic/claude-sonnet-4.5
OPENSPACE_LLM_API_KEY=sk-xxx
OPENSPACE_LLM_API_BASE=https://openrouter.ai/api/v1
```

## Proposed Solution

Extend `OpenSpaceServerDefinition.build_config()` to also inject the full set of
OpenSpace Tier-1 LLM-control env vars from `os.environ`, with `OPENSPACE_LLM_API_BASE`
as the primary addition. This mirrors the existing credential-injection pattern and
keeps ensemble's STDIO subprocess env consistent with OpenSpace's documented override
surface.

### Design

```
User sets OPENSPACE_LLM_API_BASE in .env
  → ensemble loads .env into os.environ at startup
  → build_config() reads os.environ, injects into config["env"]
  → MCP SDK merges {6 POSIX vars, **server.env} → includes the base URL
  → OpenSpace subprocess receives OPENSPACE_LLM_API_BASE=<url>
  → OpenSpace resolver Tier 1 sets litellm api_base=<url>
```

The injected env must be **redacted** by `redact_secrets()` because the value may
embed host/path metadata a user does not want exposed via `GET /api/mcp-servers`.
Existing redaction already covers any key containing `KEY|TOKEN|SECRET|PASSWORD`
(case-insensitive) — `OPENSPACE_LLM_API_BASE` does **not** match that pattern, so
it would leak. The redaction list must be extended.

## Implementation

### 1. Extend the env injection loop — `daemon/mcp/builtin_servers/openspace.py`

Replace the two-var loop with a tuple covering the OpenSpace Tier-1 surface. Keep
empty/whitespace filtering (no empty-string injection):

```python
# In build_config(), STDIO branch (after OPENSPACE_MCP_TRANSPORT pin):

# Inject OpenSpace Tier-1 LLM-control env vars. The MCP stdio_client uses
# get_default_environment() which only forwards 6 POSIX vars — without this,
# the OpenSpace subprocess has no base URL / key / headers even when the
# daemon process has them set.
_OPENSPACE_TIER1_ENV = (
    "OPENSPACE_LLM_API_KEY",
    "OPENSPACE_API_KEY",
    "OPENSPACE_LLM_API_BASE",
    "OPENSPACE_LLM_EXTRA_HEADERS",
    "OPENSPACE_LLM_CONFIG",
)
for env_name in _OPENSPACE_TIER1_ENV:
    value = os.environ.get(env_name, "").strip()
    if value:
        config["env"][env_name] = value
```

> **Decision point:** `OPENSPACE_LLM_CONFIG` is a JSON catch-all that can override
> `api_key`/`api_base`. Injecting it is powerful but broad. If a conservative scope
> is preferred, ship only `OPENSPACE_LLM_API_BASE` now and document the others as
> future work. Recommend: include `OPENSPACE_LLM_API_BASE` + `OPENSPACE_LLM_EXTRA_HEADERS`
> in v1; defer `OPENSPACE_LLM_CONFIG`.

### 2. HTTP-mode warning parity

The existing HTTP-mode (`ENS_OPENSPACE_REMOTE_URL`) warning loop currently warns only
about `OPENSPACE_LLM_API_KEY` / `OPENSPACE_API_KEY`. Extend it to warn about the new
Tier-1 vars so users aren't confused when their base URL is silently ignored in
remote mode:

```python
for cred_env in (
    "OPENSPACE_LLM_API_KEY",
    "OPENSPACE_API_KEY",
    "OPENSPACE_LLM_API_BASE",
    "OPENSPACE_LLM_EXTRA_HEADERS",
):
    if os.environ.get(cred_env, "").strip():
        logger.warning(
            "OpenSpace: %s is set but ignored in HTTP mode "
            "(ENS_OPENSPACE_REMOTE_URL); configure on the remote instance instead",
            cred_env,
        )
```

### 3. Redaction coverage — `daemon/routers/mcp_servers.py::redact_secrets()`

Add `BASE` to the sensitive-substring pattern (or add an explicit allowlist carve-out
for the known-safe non-secret keys). Two options:

**Option A — broaden the substring match** (preferred, simple):
```python
_SENSITIVE_SUBSTRINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "BASE")
```
Risk: over-redacts unrelated `*_BASE*` keys. Acceptable given none currently exist.

**Option B — explicit key allowlist** (safer, more verbose):
Treat any key in `("OPENSPACE_LLM_API_BASE",)` as redactable regardless of substring.

Recommend Option A.

## Testing

Add/extend cases in `tests/unit/mcp/test_openspace_builtin.py`:

1. **`OPENSPACE_LLM_API_BASE` injected in STDIO mode** — mirror the existing
   `test_llm_api_key_injected` assertion shape:
   ```python
   monkeypatch.setenv("OPENSPACE_LLM_API_BASE", "https://llm.internal/v1")
   config = definition.build_config({})
   assert config["env"]["OPENSPACE_LLM_API_BASE"] == "https://llm.internal/v1"
   ```
2. **Empty / whitespace value → not injected** (no empty-string entries).
3. **HTTP mode** — `OPENSPACE_LLM_API_BASE` set → warning logged, key absent from config.
4. **Existing credential tests still pass** (`OPENSPACE_LLM_API_KEY`,
   `OPENSPACE_API_KEY`) — verify the loop change did not regress them.

Add a redaction test in `tests/unit/test_mcp_server_crud.py`:
```python
def test_redact_openspace_llm_api_base():
    # OPENSPACE_LLM_API_BASE value → '[REDACTED]'
```

## Documentation Updates

- `docs/openspace-setup.md` §5 (Credentials Configuration): add `OPENSPACE_LLM_API_BASE`
  to the STDIO env table and explain when prefix-routing is insufficient.
- `docs/mcp-integration.md` OpenSpace section: note that Tier-1 OpenSpace LLM env vars
  are auto-forwarded in STDIO mode.
- `docs/features/openspace-skill-engine.md`: update the env table row.

Add a worked example for the self-hosted case:
```bash
OPENSPACE_LLM_API_KEY=sk-xxx
OPENSPACE_MODEL=openai/gpt-4o          # litellm provider prefix
OPENSPACE_LLM_API_BASE=https://llm.internal.corp/v1
```

## Alternatives Considered

| Alternative | Why not |
|-------------|---------|
| Document `OPENSPACE_MODEL` prefix-routing only | Insufficient — fails for custom/private hostnames with no LiteLLM provider id |
| Forward entire `os.environ` to the subprocess | Security regression — leaks all daemon secrets into every MCP subprocess |
| Schema field for base URL | Adds UI surface for a niche case; env-var injection is consistent with existing credential pattern |
| Do nothing (HTTP mode only) | Forces all custom-endpoint users to run a separate OpenSpace process — high ops burden |

## Open Questions

1. Ship `OPENSPACE_LLM_CONFIG` (JSON catch-all) in v1, or defer? **Recommend defer**
   (powerful, harder to redact, low immediate demand).
2. Should `OPENSPACE_LLM_API_BASE` validation reject embedded userinfo
   (`user:pass@host`), matching the `ENS_OPENSPACE_REMOTE_URL` rule? **Recommend yes**
   — same persistence/leak rationale applies since it surfaces in API responses.
