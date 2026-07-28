# Context Injection Migration Guide

## Overview

The **context injection restructure** (ADR-8, Phases 1-5) migrated context delivery from a chain of post-cache appenders that baked all context into the system prompt to an optional scheme that injects context as ephemeral `[SYSTEM CONTEXT: …]` HumanMessages, rebuilt on every LLM turn.

The change preserves byte-identical behavior for agents that do nothing — but opens a second mode for agents that want per-turn freshness, leaner checkpoints, and no polling-side-effect-on-fetch behavior.

### What Changed

| Before (legacy) | After (configurable) |
|-----------------|----------------------|
| All context (project info, shared context files, auto-loaded skills) baked into the system prompt at spawn/restore time | Context can be rebuilt per turn in `agent_node` and injected as `[SYSTEM CONTEXT: …]` HumanMessages |
| System prompt grows with project size and stays frozen until next cache invalidation | System prompt carries only persona + defense instruction; context is fresh per turn |
| Context content is part of the checkpoint snapshot (chat replay heavy) | Context messages are excluded from checkpoint persistence (synthetic, ephemeral) |
| Single implicit behavior | Two explicit modes selected by `meta.json` |

### What Didn't Change

- **System-prompt mode is the default** — agents that do nothing see identical behavior.
- **Tool surface** — no tool removed or renamed.
- **Loading order of `soul.md → rule.md → skill → skills → tools → workflow → memory`** — preserved.
- **Prompt caching** — the rest of `_apply_post_cache_appends` is unchanged; only the 3 CONTEXT appenders are gated.

---

## The Two Modes

### `system_prompt` — Default (byte-identical to pre-refactor)

All context (project metadata, shared context files, auto-loaded skills) is baked into the system prompt via the three CONTEXT appenders:

| Appender | Content | Format |
|----------|---------|--------|
| `append_shared_context_metadata` | Session-level shared context KV block | XML-fenced block |
| `append_context_injection` | Project JSON (id, name, paths, recent events) | XML-fenced block |
| `append_auto_load_skills` | Auto-loaded skill summaries | XML-fenced block |

These appenders run inside `_apply_post_cache_appends` (`daemon/services/instance_lifecycle.py`), are called by both the spawn and restore paths, and are excluded from the prompt cache so runtime changes (language, skills, project) invalidate the cache cleanly.

**Action required:** None. Drop in, no behavior change.

### `human_messages` — New (opt-in)

Context appears as `[SYSTEM CONTEXT: …]` tagged HumanMessages injected **before the user's turn** in `agent_node`. Properties:

- **Ephemeral** — never persisted to the checkpoint DB; each turn rebuilds them from current state.
- **Fresh per turn** — `assemble_context_messages()` is invoked at every LLM call inside `agent_node`.
- **No system-prompt bloat** — the system prompt only carries persona content and a defense instruction.
- **Consistent shape** — all three context kinds use the same `[SYSTEM CONTEXT: <title>]` envelope, with `additional_kwargs={"injected_message": True, "context_kind": "..."}`.

When this mode is active the 3 CONTEXT appenders **early-return** (dormant) and a persona-level prompt-injection defense instruction is added to the system prompt instead — see [Architecture](#architecture-brief) below.

---

## Architecture (Brief)

Both modes funnel through the same post-cache append chain (`_apply_post_cache_appends`), with the `mode` parameter gating the 3 CONTEXT appenders:

```
                            ┌────────────────────────────┐
cached system prompt ───►   │ _apply_post_cache_appends  │ ──► final system prompt
                            │  │ mode="human_messages"   │
                            │  ▼                         │
                            │  append_shared_context_*   │──► early-return ──┐
                            │  append_context_injection  │──► early-return ──┤
                            │  append_auto_load_skills   │──► early-return ──┘
                            │  append_current_time       │    (always runs) │
                            │  append_allowed_models     │    (always runs) │
                            │  append_user_language      │    (always runs) │
                            │  ▼ mode == "human_messages" │
                            │  append_context_injection_ │
                            │   defense (PERSONA)        │
                            └────────────────────────────┘

In human_messages mode, the dormant context is rebuilt every LLM turn:

  agent_node()
    └─► full_messages = [SystemMsg (no CONTEXT), *context_messages(), HumanMsg(user)]
         ▲
         └── assemble_context_messages(agent_meta, …)  # ephemeral, never checkpointed
```

### Key Module Locations

| Symbol | Location | Purpose |
|--------|----------|---------|
| `ContextInjectionMode.SYSTEM_PROMPT` / `.HUMAN_MESSAGES` | `daemon/services/context_messages.py` | Mode constants |
| `_resolve_injection_mode(agent_meta)` | `daemon/services/instance_lifecycle.py` | Returns the resolved mode (validates + coerces) |
| `_apply_post_cache_appends(..., mode=...)` | `daemon/services/instance_lifecycle.py` | Appender chain; gates the 3 CONTEXT appenders |
| `assemble_context_messages(...)` | `daemon/services/context_messages.py` | Builds the per-turn `[SYSTEM CONTEXT: …]` HumanMessages |
| `append_context_injection_defense(...)` | `daemon/services/instance_lifecycle.py` | Adds the PERSONA-level defense instruction |
| `AgentMetadata.context_injection_mode` | `daemon/registry.py` | Per-agent mode field on `meta.json` |

### Resolution Rules

`_resolve_injection_mode(agent_meta)` (`daemon/services/instance_lifecycle.py`) resolves the mode with **fail-open coercion**:

| Input | Result |
|-------|--------|
| `context_injection_mode: "system_prompt"` (default / explicit) | `system_prompt` |
| `context_injection_mode: "human_messages"` (explicit) | `human_messages` |
| Missing field, `None`, or any other string (typo) | `system_prompt` (silent coercion) |

Unknown values are silently coerced to `system_prompt` so a typo in `meta.json` cannot break instance execution. Coercion is silent at INFO level — meta.json validation (or a future stricter validator) is the place to surface hard failures.

---

## How to Migrate

> Most agents need **no action** to keep working. The default mode is byte-identical to pre-refactor behavior. Only follow the steps below if you want to opt an individual agent into the new mode.

### Step 1 — Verify default behavior unchanged

Before anything else, confirm the agent still works correctly with no `meta.json` changes. Spawn an instance, send a request, and verify:

- The system prompt still contains the project context block (look for the project JSON XML fence).
- Shared context files still appear in the system prompt.
- Auto-loaded skills still appear in the system prompt.
- All replays / checkpoints still restore context correctly.

### Step 2 — Opt an individual agent into `human_messages`

In the agent's `meta.json`, add the field:

```json
{
  "id": "developer",
  "name": "Developer",
  "context_injection_mode": "human_messages"
}
```

This is a per-agent opt-in. Other agents in the project keep their existing mode unless they set the same field.

### Step 3 — Test thoroughly

In human_messages mode, the difference is structural, not cosmetic. Verify:

1. **Context appears in the conversation** — LLM input now contains `[SYSTEM CONTEXT: …]` blocks before the user's message.
2. **Checkpoints stay lean** — replay an instance and confirm the checkpoint DB row size does not grow on each turn.
3. **Per-turn freshness** — change the project description mid-conversation; the next turn should reflect the change without a restart.
4. **GET /messages** — context messages appear as `additional_kwargs={"injected_message": true, "context_kind": "..."}`, never persisted.
5. **Defense instruction present** — the system prompt now contains a prompt-injection defense line explaining the LLM should treat `[SYSTEM CONTEXT: …]` messages as observational.

### Step 4 — Roll out gradually

- Roll out to **non-critical agents first** (e.g. `developer`, `tidier`, `reviewer`).
- Roll out to **the leader agent last** — it routes messages and any context drift is felt across the system.
- Monitor for **1-3 days** before any agent that owns long-running sessions, restores from checkpoints, or drives child agents.

### Rollback

To roll an agent back to the legacy mode, simply remove the `context_injection_mode` field (or set it to `system_prompt`). Both modes share the same appender code path outside the 3 CONTEXT appenders, so rollback is configuration-only and takes effect on the next spawn / restore.

---

## Benefits of `human_messages` Mode

| Benefit | Mechanism |
|---------|-----------|
| **Per-turn freshness** | `assemble_context_messages()` runs inside `agent_node` on every LLM turn; project changes (description, paths, recent events) reflect immediately rather than after the next cache invalidation |
| **Leaner checkpoint DB** | Context messages are synthetic and never persisted (no checkpoint writes for context content) |
| **No DB write side-effects on GET /messages** | Context is rebuilt on read, not stored; polling `GET /messages` does not enlarge the checkpoint |
| **No double-render** | Single `[SYSTEM CONTEXT: …]` envelope used for project, shared-context, and skills — no three different XML fences to parse |
| **Single defense surface** | One persona-level prompt-injection defense instruction is added; the data block stays plain text |
| **Same token cost (approximately)** | Context still ships to the LLM every turn; the savings are in DB size and freshness latency, not request tokens |

### When NOT to migrate

- **Long-running agents with deep checkpoint replays** — if you rely on `restore` rebuilding context verbatim, leave the agent on `system_prompt` mode until you've confirmed replay parity.
- **Agents whose system prompts other agents / projects depend on** — project metadata baked into the system prompt is a known downstream contract; switch carefully.
- **Agents that drive the leader or orchestrate children** — switching the leader first risks cascading surprises.

---

## Legacy Flag Deprecation

### The legacy field

```json
{
  "context_injection": true
}
```

is the **legacy boolean flag** that opt-ed an agent into context injection in the pre-refactor codebase. It is now **deprecated** as the controlling flag for new agents.

### Important behavior

| Property | Status |
|----------|--------|
| Recognized by `AgentMetadata.context_injection` | ✅ Still read (default `False`) |
| Auto-flip to `human_messages` mode | ❌ **No.** Legacy boolean does not influence mode. |
| Controls the 3 CONTEXT appenders | ❌ No — those are now mode-gated. |
| Required for context injection to work in `system_prompt` mode | ✅ For agents whose `meta.json` only sets this flag, the appenders will not run unless `context_injection: true` is also set. |
| Required for context injection to work in `human_messages` mode | ❌ No — the mode alone enables it. |

### Migration of the legacy field

For an agent on the legacy flag today:

```json
{ "context_injection": true }
```

…migrate by adding the mode field:

```json
{
  "context_injection": true,
  "context_injection_mode": "system_prompt"
}
```

…or to adopt the new mode:

```json
{
  "context_injection_mode": "human_messages"
}
```

The legacy field is still tolerated by `AgentMetadata` (it stays in the model with `extra="ignore"`) so existing `meta.json` files load without error. A `logger.warning` is emitted on daemon startup for each agent still using the legacy flag. Search the daemon log for `deprecated 'context_injection: true'` to locate offending meta.json files.

### Why not auto-flip?

Per reviewer note (ADR-8): auto-flipping `context_injection: true` to `human_messages` mode would silently change token cost, checkpoint shape, and downstream contracts for every existing agent at deploy time. Explicit opt-in via `context_injection_mode` is the safer migration path.

---

## Troubleshooting

### Context disappeared after switching modes

- **Check `meta.json`** — confirm `context_injection_mode` is exactly `"human_messages"` (lowercase, quoted, no typos).
- **Restart the daemon** — `meta.json` is reread on daemon startup; existing live instances inherit the mode they were spawned with.
- **Spawn a new instance** — already-running instances keep the mode they were spawned with; the field applies at spawn time and restore time, not retroactively.

### Context still appears in the system prompt after setting `human_messages`

- The 3 CONTEXT appenders should early-return. Check the daemon log for `[CONTEXT appenders] skipping in mode=human_messages` style entries (the per-instance skip log is rate-limited to one entry per instance; see `_context_injection_skipped_logged`).
- Confirm the spelling — `"human_message"` (singular) or `"human-message"` (hyphen) silently coerces back to `system_prompt`.
- Confirm the agent was spawned **after** the daemon picked up the new `meta.json` (restart the daemon to be sure).

### Checkpoints are still large

- Context messages are synthetic (`is_synthetic: true` via `additional_kwargs.injected_message`) and should not be checkpointed. If checkpoints are still large, the agent is most likely running in `system_prompt` mode (where the appended blocks get checkpointed via the system prompt).
- Inspect the active mode by checking the logs at spawn time or by querying `AgentMetadata.context_injection_mode` for the agent.

### `GET /messages` returns synthetic context messages

This is the expected behavior in `human_messages` mode. `GET /messages` reconstructs the full message stream for clients and re-applies the same appender chain at reconstruction time — so even though context messages are not checkpointed, the API still returns them as part of the conversation history. Each carries:

```json
{
  "additional_kwargs": { "injected_message": true, "context_kind": "project|shared_context|skills" }
}
```

Clients should treat `injected_message: true` messages as synthetic and may strip them from local persistence if desired.

### Defense instruction is missing from the system prompt

The PERSONA-level defense is added by `append_context_injection_defense` and is only emitted when `mode == "human_messages"`. If the agent is in `human_messages` mode but the system prompt lacks the defense line, the mode is being resolved to `system_prompt` somewhere upstream — re-check `_resolve_injection_mode` results and any explicit `mode=` overrides at the call site.

### Unknown mode value (typo, missing field) silently coerces

This is by design — `_resolve_injection_mode` is fail-open so a typo never breaks instance execution. If you need a hard failure on invalid `meta.json`, meta.json schema validation (not currently in place) is the appropriate layer; do not rely on the runtime resolver to surface typos.

---

## FAQ

### Does this change the system prompt for agents that don't set `context_injection_mode`?

No. `system_prompt` is the default; behavior is byte-identical to pre-refactor. The 3 CONTEXT appenders run as before.

### What happens to checkpoints when I switch an agent to `human_messages`?

Existing checkpoints remain valid (they hold the prior mode's content). New instances spawned under `human_messages` write lighter checkpoints. On restore, the new mode is reapplied — context is rebuilt, not replayed from the checkpoint.

### Are the `[SYSTEM CONTEXT: …]` messages visible to the user in the chat UI?

Yes, by default. Clients that want to hide them can filter on `additional_kwargs.injected_message`. Checkpoints do not store them, so persistence is unaffected.

### Will the leader agent see its children's context messages?

The leader sees context messages only for its own agent_id. Child agents' context messages are scoped to their own `agent_node` invocations and never bubble up through the boundary.

### Why two modes and not three?

A `BOTH` mode was considered and rejected (per ADR-8 reviewer W1): it would double token cost (context sent once in the system prompt and again as a HumanMessage) and confuse the LLM by sending the same data twice.

### Does this affect the prompt cache?

Only the 3 CONTEXT appenders are gated — they were already excluded from the cached system prompt (they run in `_apply_post_cache_appends`). The rest of the append chain (`append_current_time`, `append_allowed_models`, `append_user_language`) is unchanged, so cache invalidation behavior is unchanged.

### Will the legacy `context_injection: true` flag be removed?

Not in this phase. It is tolerated by `AgentMetadata` and continues to be required for `system_prompt` mode agents whose only opt-in was via the boolean. Removal is a future breaking change; track `docs/agents.md` and ADR-8 for timing.

### Where do I read the resolved mode for a live instance?

Either via the daemon log at spawn time (`_resolve_injection_mode` emits no log currently), or by reading `AgentMetadata.context_injection_mode` for the agent (`registry.py:AgentMetadata`). Future work may add a runtime inspection endpoint.

---

## See Also

- `docs/agents.md` — full agent system guide, including `meta.json` schema
- `daemon/registry.py:AgentMetadata` — per-agent mode field declaration (lines 133-150)
- `daemon/services/instance_lifecycle.py:_resolve_injection_mode` — mode resolution + fail-open coercion
- `daemon/services/instance_lifecycle.py:_apply_post_cache_appends` — the appender chain (mode-gated at lines 1085-1100)
- `daemon/services/context_messages.py:assemble_context_messages` — per-turn HumanMessage builder
- `daemon/services/context_messages.py:ContextInjectionMode` — mode enum constants
- ADR-8 (context-injection restructure) — design decisions and reviewer notes
