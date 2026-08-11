# Phase 1: Core Adapter Scaffold

## Objective

Create the `DiscordAdapter` class implementing the `MessageSourceAdapter` ABC, wire up `discord.py` client lifecycle (Gateway connect, heartbeat, reconnect/resume), validate credentials and configuration, and register the adapter in the source registry so it can be started/stopped via the API.

## Precondition: Python 3.13 Compatibility Gate (Task 0)

Phase 1 work MUST NOT begin until **Task 0 (compatibility gate)** has passed. If both `discord.py` and `py-cord` fail, STOP and escalate to developer — do NOT write any adapter code against an unverified library.

| Step | Action | Acceptance |
|------|--------|------------|
| 0.1 | `pip install discord.py` | Package installs without error |
| 0.2 | `python -c "import discord"` | Exits 0 on Python 3.13 |
| 0.3 | `python -c "import discord; print(discord.__version__)"` | `discord.__version__ >= 2.4.0` |
| 0.4 | If 0.2 or 0.3 fails (incompatible with Python 3.13) | Fall back to `pip install py-cord`; re-verify `import discord` (py-cord exposes the `discord` namespace) |
| 0.5 | If BOTH discord.py AND py-cord fail | STOP — escalate to developer. Do NOT proceed to any adapter code. Phase 1 is blocked until compatibility is resolved. |

**Gate outcome:** Recorded in the Phase 1 PR description. The library chosen (discord.py or py-cord) is pinned in `pyproject.toml`.

## Files to Create

| # | File | Purpose |
|---|------|---------|
| 1 | `daemon/sources/adapters/discord/__init__.py` | Package init; exports `DiscordAdapter` |
| 2 | `daemon/sources/adapters/discord/adapter.py` | Core adapter class (partial — lifecycle only this phase) |
| 3 | `daemon/sources/adapters/discord/rate_limiter.py` | Stub `DiscordRateLimiter` (concurrency semaphore, filled in Phase 3) |
| 4 | `daemon/sources/adapters/discord/thread_manager.py` | Stub `DiscordThreadManager` (filled in Phase 3) |
| 5 | `daemon/sources/adapters/discord/formatting.py` | Stub `_strip_llm_artifact_tags` + `_clean_discord_text` (filled in Phase 2) |

## Files to Modify

| # | File | Change |
|---|------|--------|
| 1 | `pyproject.toml` | Add `discord.py>=2.4.0` (or `py-cord>=2.6.0` if Task 0 fallback applied) |
| 2 | `daemon/sources/registry.py` (~line 423) | Add discord dispatch branch with `manager=self._manager`, inject `_source_repo` |
| 3 | `daemon/sources/mapper.py` (lines 27-31, 89-106) | Add `SOURCE_TYPE_DISCORD`, add to `VALID_SOURCE_TYPES`, add ID validation using `DISCORD_ID_PATTERN = ^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$` |
| 4 | `daemon/routers/sources.py` (lines 207-209) | Replace stub with `DiscordAdapter.test_connection()` |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 0 | **Precondition: discord.py Python 3.13 Compatibility Gate (GO/NO-GO)** — `pip install discord.py`; verify `import discord` works on Python 3.13; check installed version. If incompatible, fall back to `pip install py-cord` and re-verify. If BOTH fail: STOP — escalate to developer. Do NOT proceed to any adapter code. | none | `python -c "import discord; print(discord.__version__)"` exits 0 with version >= 2.4.0; if discord.py failed, py-cord provides working `discord` namespace |
| 1 | Add `discord.py>=2.4.0` to `pyproject.toml` dependencies; run `uv sync` / `pip install -e .` to verify import works | 0 | `import discord` succeeds; `discord.__version__ >= 2.4.0` |
| 2 | Create `daemon/sources/adapters/discord/__init__.py` with `from .adapter import DiscordAdapter; __all__ = ["DiscordAdapter"]` | 1 | Package importable: `from daemon.sources.adapters.discord import DiscordAdapter` |
| 3 | Create `DiscordAdapter.__init__()`: extract `config.credentials["bot_token"]`, validate non-empty; set `self._default_agent = config.config.get("agent", "ari")`, `self._require_mention = config.config.get("require_mention", True)`, `self._allowed_guild_ids = config.config.get("allowed_guild_ids")`, `self._allowed_channels = config.config.get("allowed_channels")`, `self._allowed_bot_ids = config.config.get("allowed_bot_ids")`, `self._channel_mention_config = config.config.get("channel_mention_config", {})`, `self._ignore_bot_messages = config.config.get("ignore_bot_messages", True)`, `self._strip_llm_artifact_tags_enabled = config.config.get("strip_llm_artifact_tags", True)`, and `self._intents_config = config.config.get("intents")` (dict format); init circuit breaker `CircuitBreaker(5, 60.0)`, per-channel locks `OrderedDict` (capped at `MAX_CHANNEL_LOCKS = 1000`), `_source_repo = None` | 2 | Adapter constructs with valid config; raises `ValueError` on missing bot_token (matches Telegram pattern: telegram.py:89-91) |
| 4 | **Create `start()` (blocking-until-Gateway-confirmed)** — configure discord.py `Intents` from config (dict format); FAIL CLOSED if `MESSAGE_CONTENT` requested but not granted — log clear error and raise; create `discord.Client`; create `asyncio.Event` (`self._ready_event`); register `on_ready` callback that captures bot user ID, sets status RUNNING, and **sets the event** to signal Gateway confirmation; create client task `self._client_task = asyncio.create_task(client.start(token))`; **block until Gateway confirmed** via `await asyncio.wait_for(self._ready_event.wait(), timeout=30.0)`; on timeout, BEFORE raising `RuntimeError("Discord Gateway connection timed out")`, cancel `self._client_task` via `self._client_task.cancel()` then `await asyncio.gather(self._client_task, return_exceptions=True)` to ensure the failed start is self-contained — no orphaned Gateway task remains; transition STARTING→RUNNING | 3 | `start()` returns only after `on_ready` fires; status=RUNNING; invalid token → ERROR status with clear message; 30s Gateway timeout → `RuntimeError`; satisfies SourceRegistry supervisor contract (registry expects `start()` to return only when adapter is RUNNING) |
| 5 | **Create `stop()` (close-then-await-task)** — call `await client.close()` FIRST to cleanly disconnect the WebSocket, THEN `await self._client_task` to ensure the client task has fully terminated; cancel pending background tasks; release per-channel locks; set STOPPED status; guard against double-stop | 4 | `stop()` cleanly disconnects; status=STOPPED; no dangling tasks; idempotent |
| 6 | **Register placeholder `on_message` handler (Phase 1 stub)** — register a no-op stub (`async def _on_message_stub(self, message): pass`) as the `on_message` event handler on the discord.py client during `start()`. This ensures the client has the event registered from the start; the real handler implementation lands in Phase 2. | 4 | Client has `on_message` event registered from Phase 1 start; handler is a no-op stub; replaced by real handler in Phase 2 |
| 7 | Create `health_check()`: check `status == RUNNING`, client exists, `client.is_ready()` returns True (Gateway connected + identified); return False otherwise | 4 | Returns True when running+ready; False when stopped/error/disconnected |
| 8 | **Create `test_connection()` classmethod (direct aiohttp)** — create an `aiohttp.ClientSession`; GET `https://discord.com/api/v10/users/@me` with header `Authorization: Bot {token}`. This is the canonical pre-flight check — NOT `client.fetch_user('self')`. Response handling: 200 → `(True, "Connected as {username}")`; 401 → `(False, "Invalid bot token")`; 4xx → `(False, "Discord API error: {status}")`; network error or 15s timeout → `(False, "Connection failed: {error}")`. Always close the session in `finally`. | 4 | Valid token → `(True, "Connected as BotName")`; invalid token → `(False, "Invalid bot token...")`; network error → `(False, "Connection failed...")`; completes within 15s |
| 9 | Register adapter in `registry.py`: add `elif source_type == "discord": from .adapters.discord import DiscordAdapter; adapter = DiscordAdapter(config, on_message, manager=self._manager); adapter._source_repo = self._source_repo; return adapter`. Update `mapper.py`: add `SOURCE_TYPE_DISCORD` constant, add to `VALID_SOURCE_TYPES`, add ID validation branch using `DISCORD_ID_PATTERN = ^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$`. Update `routers/sources.py` test_connection branch to call `DiscordAdapter.test_connection()`. | 3, 8 | Creating a Discord source via API succeeds; registry instantiates adapter; test_connection endpoint returns real result instead of "not implemented"; mapper accepts all three canonical external user ID formats (DM, channel, thread) |

## Configuration Model

The Discord `SourceConfig.config` dict supports these keys:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `agent` | str | `"ari"` | Agent to route Discord messages to |
| `require_mention` | bool | `True` | If True, guild channel messages require @mention to activate |
| `allowed_guild_ids` | list[str] or None | `None` | If set, only process messages from these guild IDs |
| `allowed_channels` | list[str] or None | `None` | If set, only process messages from these channel IDs |
| `allowed_bot_ids` | list[str] or None | `None` | If set, messages from these bot IDs bypass the default bot-skip filter; empty/unset skips all bot messages |
| `channel_mention_config` | dict[str, str] | `{}` | Per-channel mention mode: `"always_active"`, `"require_mention"`, or `"disabled"` |
| `ignore_bot_messages` | bool | `True` | Whether messages from bots are skipped by default, except IDs in `allowed_bot_ids` |
| `strip_llm_artifact_tags` | bool | `True` | Whether outgoing LLM artifact tags are stripped |
| `max_message_length` | int | `2000` | Discord message character limit used for splitting |
| `intents` | dict | see below | Discord Gateway intent configuration (dict format matching discord.py Intents API — NOT a list) |

**Example `SourceConfig.config`:**
```json
{
    "agent": "ari",
    "allowed_guild_ids": ["111222333444555666"],
    "allowed_channels": ["222333444555666777"],
    "allowed_bot_ids": ["333444555666777888"],
    "require_mention": true,
    "channel_mention_config": {
        "222333444555666777": "always_active"
    },
    "ignore_bot_messages": true,
    "strip_llm_artifact_tags": true,
    "max_message_length": 2000,
    "intents": {
        "guilds": true,
        "guild_messages": true,
        "message_content": true,
        "dm_messages": true
    }
}
```

**Intents config schema** (dict format, matching discord.py `Intents` API):
```json
{
    "guilds": true,
    "guild_messages": true,
    "message_content": true,
    "dm_messages": true
}
```

### MESSAGE_CONTENT Intent — FAIL-CLOSED

The `message_content` intent is **privileged** — it must be enabled for the bot in the Discord Developer Portal. If the operator configures `intents.message_content = true` but the bot does not actually have the intent granted/verified at `start()`, the adapter **FAILS CLOSED**:

- `start()` logs a clear, actionable error message identifying which intent is missing.
- `start()` raises (does NOT return successfully).
- **No silent empty-message processing** is permitted under any circumstance.

This protects operators from a confusing runtime state where the bot connects but cannot read message bodies.

### Sharding

**Sharding is deferred to v2.** Phase 1 implements a single bot client only. Multi-shard Gateway support is out of scope until guild scale requires it.

Credentials:
```python
{
    "bot_token": "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.Gabcde.xxx..."  # Required
}
```

## External User ID Validation

The mapper uses the canonical Discord external user ID regex for validation:

```python
DISCORD_ID_PATTERN = r"^(dm:\d{17,19}|\d{17,19}:\d{17,19}(:\d{17,19})?)$"
```

Matches the three canonical forms:

| Context | Format | Example |
|---------|--------|---------|
| DM | `dm:{user_id}` | `dm:123456789012345678` |
| Guild channel | `{guild_id}:{channel_id}` | `987654321:555444333222` |
| Thread | `{guild_id}:{parent_channel_id}:{thread_id}` | `987654321:555444333:777888999` |

`\d{17,19}` matches Discord snowflake IDs (17–19 digit integers).

## Coupling

- **Tight with:** Phase 2 — the adapter class created here is where message flow methods are added
- **Loose with:** Phase 3 — lifecycle hooks (`start`/`stop`) will integrate with rate limiter and thread manager
- **Independent of:** Phase 4 — tests are written against all phases

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| discord.py `Client.start()` blocks the event loop | High | Run client task via `asyncio.create_task`; `start()` awaits an `asyncio.Event` (set by `on_ready`) with 30s timeout for Gateway confirmation |
| Privileged MESSAGE_CONTENT intent missing at runtime | High | FAIL-CLOSED in Task 4: if `message_content` requested in config but not granted/verified at `start()`, log clear error and raise — never silently process empty messages |
| discord.py version incompatibility with Python 3.13 | High | Task 0 compatibility gate: verify `import discord` on Python 3.13 before any adapter code is written; fall back to `py-cord`; STOP and escalate to developer if both fail |

## Exit Criterion

- **Task 0 (compatibility gate) has passed** and the chosen library is pinned in `pyproject.toml`
- Discord source can be created via API (`POST /sources` with `source_type="discord"`)
- Adapter starts and connects to Discord Gateway (`status=RUNNING`, `start()` returns only after `on_ready` fires within 30s)
- `test_connection` endpoint returns real connection result via direct `aiohttp` GET to `/users/@me` (not `client.fetch_user('self')`)
- Adapter stops cleanly (`status=STOPPED`, `client.close()` called before awaiting the client task)
- `health_check()` returns correct boolean for adapter state
- Registry, mapper (using `DISCORD_ID_PATTERN`), and router all recognize the discord source type
- Placeholder `on_message` no-op handler is registered (replaced in Phase 2)
- `MAX_CHANNEL_LOCKS = 1000` constant is in place (matches NFR-9 and Telegram's `MAX_CHAT_LOCKS = 1000`)
