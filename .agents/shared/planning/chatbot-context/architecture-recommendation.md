# Architecture Recommendation: Chatbot Platform Context Injection

**Date:** 2026-08-12
**Status:** Complete
**Mode:** Standard Design (competitive fan-out, N=2)

---

## Question

How should chatbot platform context (Discord/Slack/Telegram) be injected into the agent context so the root instance knows which platform it's talking to and formats responses correctly, while children agents remain unaffected?

**Constraint:** Only the ROOT instance (the one directly handling the chatbot message) should receive platform context. Children spawned by the root must NOT receive it.

---

## Recommendation

**Adopt a HYBRID of Option B and Option D:**

1. **Store `source_type` in `Instance.instance_metadata` JSONB** at spawn time (the prerequisite both options identified).
2. **Inject platform formatting rules as a NEW PERSONA-LEVEL APPENDER** in the system prompt construction chain (Option B's mechanism), specifically because platform rules are persona shaping, not per-turn context.
3. **DO NOT inject as a `[SYSTEM CONTEXT: Chat Platform]` HumanMessage** (Option D rejected for reasons below).

**Rationale in one sentence:** Platform formatting constraints are stable across the instance's lifetime — the root of a Telegram conversation always talks to Telegram — and like other persona sections (current_time, user_language, allowed_models) belong in the cached system prompt, not in the per-turn context tail that burns tokens on every turn.

---

## Approach Comparison

| Axis | Option A: Context HumanMessage (every turn) | Option B: System Prompt Appender | Option C: IncomingMessage metadata | Option D: Persistent `[SYSTEM CONTEXT]` (turn 1 only) |
|------|------------------------------------------|--------------------------------|-----------------------------------|------------------------------------------------------|
| **Complexity** | Low — single field add; runtime overhead per turn | Low-Med — single appender following existing pattern | Low — but metadata doesn't reach system prompt | Med — new slot, new branch, interaction with `project_already_injected` |
| **Scalability** | Poor — re-emit identical content every turn; burns tokens | Excellent — appended once at spawn/restore; cached prompt reuse | Poor — metadata lifecycles differ across layers | Good — checkpointed once, read free thereafter |
| **Maintainability** | Med — risks drift if content needs to change mid-session | Excellent — matches `append_user_language` / `append_current_time` pattern | Poor — implicit type flow, hard to debug | Med — orchestrator grows another branch |
| **Risk** | 🟡 Token cost scales with conversation length; redundant in extended chats | 🟡 Spawn/restore output parity must be verified (verifiably identical in practice) | 🔴 Metadata may not survive into the LLM call surface | 🟡 Branching on `project_already_injected` adds a second gate to keep in sync |
| **Cost** | 🟡 Token cost on every turn | 🟢 One-time | 🟢 Minimal | 🟢 One-time then free |
| **Recommendation** | **Reject** — cost scales badly | **RECOMMENDED (with required guardrails)** | **Reject** — bypasses the system prompt where the LLM most reliably reads role instructions | **Secondary** — valid fallback if anyone insists on HumanMessage semantics |

**Trade-off summary:** Option B wins decisively on **Cost**, **Scalability**, and **Maintainability** (it slots into an existing, well-tested appender pattern). The Risk gap (spawn-vs-restore parity) is verifiably closed because `_apply_post_cache_appends` is called identically from both `instance_lifecycle.py:844-855` (spawn) and `persistence.py:876-890` (restore) with identical arguments, and `instance_metadata["source_type"]` is reloaded from the same JSONB column in both paths.

---

## Final Design (Hybrid: B-mechanism + D-prerequisite)

### Prerequisite (must be done first — both workers converged on this)

**`source_type` is currently NOT in `Instance.instance_metadata`.** It is captured at `registry.py:809` and stored only in the `source_mapping` table. The appender must read from the Instance row (cheap, single-row read) — not from source_mapping (would force a join inside an appender). So we must:

| # | File:Method | Change |
|---|-------------|--------|
| 1 | `daemon/sources/registry.py:dispatch_message` | Already extracts `adapter.source_type` (line 809). Pass `source_type=source_type` into `mapper.get_or_create_instance` (line 840-846). |
| 2 | `daemon/sources/mapper.py:get_or_create_instance` | Add `source_type: str \| None = None` parameter. Forward into `spawn_instance_with_mcp` (line 357-360). |
| 3 | `daemon/manager.py:spawn_instance_with_mcp` | Accept `source_type` in kwargs forwarding. |
| 4 | `daemon/services/instance_lifecycle.py:spawn_instance` (sync) | Write `instance_metadata["source_type"] = source_type` into the JSONB dict (additive, no migration; matches existing `instance_metadata["mcp_tool_names"]` pattern). |

**No SQL migration** — `instance_metadata` JSONB is already present on `instances` (per critical note: PostgreSQL `_ensure_postgres_columns` for new columns, but adding a JSONB key is additive and requires no migration).

### New appender

**Location:** `daemon/services/instance_lifecycle.py`, immediately after `append_user_language` (after current line 458).

**Signature** (mirrors `append_context_key`):

```python
def append_platform_context(
    system_prompt: str,
    instance_id: str,
    instance_repository,
    parent_id: str | None,
) -> str:
    """Append platform-specific formatting instructions to system prompt.

    Active ONLY when:
      - parent_id is None (root instance), AND
      - instance_metadata["source_type"] is a recognized platform.

    Children (parent_id set) and instance rows lacking source_type pass through.
    """
    if parent_id is not None:
        return system_prompt
    try:
        meta = instance_repository.get(instance_id)
        source_type = (getattr(meta, "instance_metadata", None) or {}).get("source_type")
    except Exception as exc:
        logger.debug(f"append_platform_context: metadata read failed: {exc}")
        return system_prompt
    section = _PLATFORM_INSTRUCTIONS.get(source_type)
    if not section:
        return system_prompt
    return system_prompt + section
```

**Whitelist behavior:** Unknown `source_type` values silently skip. This protects against stale rows (e.g., an adapter renamed) and against malicious/buggy writes.

### Where it slots in `_apply_post_cache_appends`

**Order:** append_context_key → append_current_time → append_allowed_models → append_user_language → **append_platform_context** → append_context_injection_defense.

Group with other always-on PERSONA appenders. Runs at both spawn (`instance_lifecycle.py:844`) and restore (`persistence.py:876`) with identical args.

### Root-instance gate

Two-condition gate, evaluated inside the appender:
1. `parent_id is None` — root of the instance tree. Verified by `instance_lifecycle.py:818-824` (`if parent_id:` truthy check on child count) and `persistence.py:883` (`getattr(instance_meta, "parent_id", None)`).
2. `instance_metadata["source_type"]` is a recognized platform — prevents spurious injection for API-spawned roots that have no `source_type`.

**Edge cases:**
- **Crash recovery / restore:** `parent_id` and `instance_metadata` both survive DB round-trip. ✅
- **Child-of-source-root children:** Their `parent_id` is the root, NOT `None`. Gate holds. ✅
- **Agent-spawned root via `spawn_instance` tool:** `parent_id=None` but no `source_type`. Second gate holds. ✅
- **Pre-feature instances** (DB existed before this change): `instance_metadata["source_type"]` absent → second gate holds → no spurious block. ✅ No backfill needed.

---

## Platform Context Format

Use a static constant table `_PLATFORM_INSTRUCTIONS` (whitelist-only). Drawn from Worker B's format pattern, simplified:

```text
---

## Platform Context

You are responding through the **discord** chat platform.

- **Discord** supports full Markdown: `**bold**`, `*italic*`, `` `inline code` ``,
  ```` ```code blocks``` ````, # headings, lists, links.
- Mention users with `<@user_id>`; emoji via `<:name:id>` or unicode emoji.
- Message length cap is **2000 characters**. Split long replies into multiple
  short messages, NOT a single >2000-char block.
- Do not echo private IDs (channel_id, guild_id, user_id) verbatim in replies.
- Children you spawn inherit the same formatting target.

Source: discord
```

Per-platform variants (concise, <300 chars each):

| Source | Format | Length cap | Mentions |
|--------|--------|-----------|----------|
| `discord` | Full Markdown | 2000 chars | `<@user_id>`, `<:emoji:id>` |
| `slack` | `mrkdwn` (`*bold*`, `_italic_`, `~strike~`, ` `code` `) | 4000 chars (blocks up to ~50K) | `<@user_id>`, `<!channel>`, `<!here>` |
| `telegram` | HTML subset (`<b>`, `<i>`, `<code>`, `<pre>`) or MarkdownV2 with escaping | 4096 chars | `@username` (text only) |

**Constants table structure** (single source of truth — easy to update, no string scattering):

```python
_PLATFORM_INSTRUCTIONS: dict[str, str] = {
    "discord": _PLATFORM_TEMPLATE.format(name="discord", ...),
    "slack":   _PLATFORM_TEMPLATE.format(name="slack", ...),
    "telegram": _PLATFORM_TEMPLATE.format(name="telegram", ...),
}
```

---

## Required Changes (complete)

| # | File:Method | Line | Purpose |
|---|-------------|------|---------|
| 1 | `daemon/sources/registry.py:dispatch_message` | 840-846 | Pass `source_type=adapter.source_type` to mapper |
| 2 | `daemon/sources/mapper.py:get_or_create_instance` | 282 + 357-360 | Accept + forward `source_type` |
| 3 | `daemon/manager.py:spawn_instance_with_mcp` | kwargs section | Forward `source_type` kwarg |
| 4 | `daemon/services/instance_lifecycle.py:spawn_instance` (sync, ~line 1001-1033) | Write `instance_metadata["source_type"] = source_type` into JSONB |
| 5 | `daemon/services/instance_lifecycle.py` (new) | Near `append_context_key` (line 183-214) | Define `_PLATFORM_INSTRUCTIONS` constant + `append_platform_context` |
| 6 | `daemon/services/instance_lifecycle.py:_apply_post_cache_appends` | 458-463 | Insert `append_platform_context` call after `append_user_language` |
| 7 | **No change** to `persistence.py:_reconstruct_full_system_prompt` | 876-890 | Same append chain runs on restore; gate picks up `source_type` automatically |
| 8 | **No change** to `daemon/graph.py:ContextSlot` | 485-495 | Does not need to know about `source_type` |

**Total surface area: 5 files touched, ~6 discrete changes.**

---

## Risks

### 🟡 [Significant] Spawn-vs-restore parity must be empirically verified

The appender runs at both `instance_lifecycle.py:844` (spawn) and `persistence.py:876` (restore). Both paths pass identical args today, so output IS identical in theory. **Mitigation:** Add a test asserting byte-equal system prompts across `spawn_instance(...)` and `_reconstruct_full_system_prompt(...)` for a source-typed instance.

### 🟡 [Significant] `parent_id is None` gate relies on Instance row state, not tree depth

A future "swap parent" feature that clears `parent_id` without clearing `instance_metadata.source_type` could let a child suddenly inherit platform context. **Mitigation:** Record `instance_metadata["platform_context_emitted"] = True` at first system prompt build so the gate can additionally check the flag (avoids any tree mutation re-firing the appender on restore).

### 🟢 [Improvement] Free-form `source_type` value

The appender whitelists known platforms and skips unknown values silently. Add a log line at INFO (not WARNING, to avoid log spam) when an unrecognized value appears, so we notice adapter renames.

### 🟢 [Improvement] Restore behavior on legacy data

Pre-feature `Instance` rows will lack `instance_metadata["source_type"]`; the appender silently no-ops. This is correct (no spurious injection). **Mitigation:** None required, but a metric `chat_platform_context_emitted_total{source_type}` would surface coverage in prod.

---

## Decisions Pending

None. The architecture is fully specified. **Implementation can begin once the leader approves.**

---

## Open Questions

1. **Should children inherit ANY platform awareness at all?** Both workers noted that the appender's text should explicitly tell the root to "pass the same formatting target to children you spawn" — but actual inheritance is via the LLM's instructions, not a system mechanism. If we want hard inheritance (a child of a Discord root sees Discord instructions in its own prompt), that's a follow-up feature.

2. **Should the formatting rules be added as AGENT-LEVEL persona (per agent) or only as INSTANCE-LEVEL context (only the instances that came from sources)?** The recommendation above is instance-level (only the chatbot-handling instances). If we want every instance of, say, the `ari` agent to think in Discord markdown, that's a meta-level change to the agent's soul/rule files — different feature, different ticket.

3. **Slack mrkdwn depth:** Slack's `mrkdwn` parser has subtle quirks (no nested formatting, link syntax conflicts with angle-bracket channels). Should we ship the basic rule only, or include a per-edge-case dictionary? Recommendation: ship the basic rules, expand later from real user reports.

---

## Appendix: Why Option D was rejected

| Concern | Why it loses to Option B |
|---------|--------------------------|
| Adds a new branch inside `assemble_context_messages` that must interact with the `project_already_injected` gate (line 1206) — a second gate to keep in sync | Option B's gate is one condition (`parent_id is None`) without a per-turn dependency |
| HumanMessage block repeats invisibly in messages list during chat history view (parent sees `[SYSTEM CONTEXT: Chat Platform]` on every turn) | Option B is invisible in the chat thread — it lives in the system prompt |
| Adds a context kind, message formatter, and slot — touches the orchestrator's most-edited file | Option B touches one new function + one line in `_apply_post_cache_appends` |
| Misclassifies stable persona content as per-turn context | Option B classifies it correctly as PERSONA, alongside user_language and allowed_models |

**The single case for Option D:** if we wanted formatting to vary per turn (e.g., user toggles "send next reply in plain text"), then per-turn context would be the right surface. **Current requirements are static-per-instance → Option B wins.**

