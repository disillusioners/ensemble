# Approach Comparison: Chatbot Platform Context Injection

**Date:** 2026-08-12
**Question:** Best injection point for Discord/Slack/Telegram platform context, gated on root instance only.

## Comparison Axes

| Axis | A: Context HumanMessage (every turn) | B: System Prompt Appender | C: IncomingMessage metadata | D: Persistent `[SYSTEM CONTEXT]` (turn 1 only) |
|------|--------------------------------------|---------------------------|------------------------------|----------------------------------------------------|
| **Complexity** | Low | Low-Med | Low | Med |
| **Scalability** | Poor | Excellent | Poor | Good |
| **Maintainability** | Med | Excellent | Poor | Med |
| **Risk** | 🟡 Token bloat; no rebuild hook | 🟡 Spawn/restore parity (verifiable) | 🔴 Metadata often doesn't reach prompt | 🟡 Adds branch to orchestrator |
| **Cost** | 🟡 Per-turn tokens | 🟢 Once-at-spawn | 🟢 Minimal | 🟢 One-time |
| **Recommendation** | **Reject** | **RECOMMENDED** | **Reject** | **Secondary** (if HumanMessage semantics insisted) |

## Detailed Notes per Axis

### Complexity
- **A** adds 1 field + runs every turn. Low code complexity, high runtime complexity cost.
- **B** adds 1 appender (~30 lines) + 1 line in the chain. Matches existing patterns (context_key, user_language).
- **C** is the smallest diff, but the metadata often doesn't reach the LLM (proven by Discord metadata gap experience).
- **D** adds a new context kind, a new builder, and a branch interaction with `project_already_injected` — touches the orchestrator's most-edited file.

### Scalability
- **A** scales with conversation length — every turn re-emits identical text. Multi-turn conversations waste tokens.
- **B** scales perfectly — appended at spawn/restore, cached by mtime, retrieved once per session.
- **C** doesn't scale because metadata lifecycle confusion creates drift.
- **D** scales well — checkpointed once on turn 1, read free thereafter (matches ADR-15 hybrid model).

### Maintainability
- **A** risks drift between content sources if the rule ever changes mid-session.
- **B** is the most maintainable — slots into the well-documented `append_*` family in `instance_lifecycle.py`.
- **C** is the worst — implicit type flow across mapper → spawn → system prompt is hard to debug.
- **D** adds another special-case branch to `assemble_context_messages`, which is already complex.

### Risk
- **A** 🟡 token cost scales with conversation length. No mid-flight rebuild hook.
- **B** 🟡 spawn-vs-restore output parity must be empirically verified. Both paths run `_apply_post_cache_appends` with identical args today, so output IS identical in theory.
- **C** 🔴 the critical notes explicitly warn `extra_mapping_metadata` may not flow all the way to the system prompt. The Discord metadata gap (registry.py:811-845 dispatch branch was missing until recently) is precedent.
- **D** 🟡 adds a second state-machine branch (`project_already_injected`); risk of forgetting the gate on restore.

### Cost
- **A** 🟡 tokens on every turn — 200+ char block repeated 50+ times per long conversation.
- **B** 🟢 one-time at spawn / per restore.
- **C** 🟢 minimal.
- **D** 🟢 one-time at turn 1, free from turn 2 onward via checkpoint.

## Why Option D Loses Despite Sharing Some DNA with Option B

Both options store `source_type` in `instance_metadata` JSONB and gate on `parent_id is None`. The mechanism diverges:

| | Option B (chosen) | Option D |
|---|---|---|
| Surface | System prompt (concatenated strings) | `state['messages']` (HumanMessage objects) |
| Inject point | `_apply_post_cache_appends()` once at spawn/restore | `assemble_context_messages()` once at turn 1 |
| Persistence | Cached via `load_and_cache_prompt()` + post-cache appenders | LangGraph checkpoint via `add_messages` reducer |
| Visible in chat history? | NO | YES — appears as a message in the user's thread |
| Token cost per turn | 0 (in system prompt, cached) | 0 from turn 2 (read from checkpoint) — but adds ~1 message to LLM message ordering |
| Branching complexity | Appender has 1 gate (`parent_id + source_type`) | Orchestrator has 2 gates (above + `project_already_injected`) |

Option B is structurally simpler. Option D would be appropriate if we wanted formatting to change per turn — current requirements are static-per-instance.

## Why Option A Loses Decisively

Re-emits an identical ~200-char block on EVERY turn of a conversation. For a 20-turn conversation, that's ~4000 tokens burned on instructions that never change. This is a textbook "anti-pattern: stale context messages per turn" that the system prompt architecture explicitly moved away from in 2026-07-29 (per critical note and context-injection-persistence-model).

## Why Option C Is Risky

The existing pattern (`extra_mapping_metadata` at `registry.py:816-820`) puts platform-specific keys (channel_id, thread_ts) in `source_mapping.metadata`, separate from `Instance.instance_metadata`. The appender needs `source_type` on the Instance row (cheap, single-row read). Promoting `source_type` to Instance metadata is a one-line change at spawn, but it requires adding it through the **same** channel as the other JSONB keys — and Option C does the opposite (proposes passing via IncomingMessage metadata, which has the documented risk of being dropped in transit).

Option C is rejected on trust: the metadata flow has failed before (Discord gap, registry.py:811 history), and the cost of getting it wrong (silent absence of platform context for every root) is high. Options B and D promote the value to the most reliable storage path — Instance.instance_metadata JSONB — and both workers converged on this prerequisite.
