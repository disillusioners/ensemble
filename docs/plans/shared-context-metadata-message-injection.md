# Shared Context Metadata — Message-Body Injection Gap

**Date**: 2026-07-12
**Status**: Investigation → Draft plan
**Impact**: `daemon/services/instance_lifecycle.py`, `daemon/services/instance_messaging.py`, `daemon/tools/shared_context_tools.py`, `daemon/services/context_injection.py`
**Source**: Investigation of "shared context is not displayed" in a leader→dev message

---

## Problem

When a leader agent sends a message to a dev (child) instance, the dev's
incoming message is expected (per the feature's product intent) to contain an
injected `[shared context]` block **prepended to the leader's request**, separated
from it by a divider:

```
[shared context]          ← shared_context_metadata KV, injected
─────────────────────     ← separator
[the request from leader] ← leader's actual message
```

In practice the dev receives **only** the leader's request (plus the existing
`## Related Project` block). The `[shared context]` section is **not displayed**
in the message. This doc records why, and whether it is a bug or missing
implementation.

---

## Verdict

**Missing implementation (design gap), not a bug in existing code.**

The `shared_context_metadata` feature was designed and built to inject into the
**system prompt only** — it has no message-body injection path. The
`[shared context] / separator / request` layout was never implemented for
internal agent-to-agent messages. The system-prompt injection itself works as
designed and matches the design docs.

A secondary timing factor can also hide it from the system prompt: the prompt is
built once at spawn/restore time, so metadata written **after** the child is
spawned never reaches the child's system prompt.

---

## Root Cause

### 1. Two different "shared context" systems, two different injection targets

| System | Injected into | Code path | Visible in message? |
|--------|---------------|-----------|---------------------|
| **Project context** (`## Related Project`) | **Message body** — prepended to the request | `instance_messaging.py:1690` & `:1722` (`message = project_context + message`) | ✅ Yes |
| **Shared context metadata KV** (`# Shared Context`) | **System prompt** — at spawn/restore only | `instance_lifecycle.py:678` (spawn) & `:1652` (restore) | ❌ No |

This is why `## Related Project` shows up in the leader→dev message but
`# Shared Context` does not: project context is message-body-injected; shared
context metadata is system-prompt-injected.

### 2. No message-body injection of shared context metadata exists

- `append_shared_context_metadata()` (`instance_lifecycle.py:208`) appends the KV
  to the **system prompt**, producing a `# Shared Context` / `## Metadata KV`
  block wrapped in `<shared_context_metadata>` tags with a `---` separator
  (`instance_lifecycle.py:305-310`). It is called **only** at spawn
  (`instance_lifecycle.py:678`) and restore (`instance_lifecycle.py:1652`) —
  never in the message-delivery path.
- The message-delivery path sends the raw message:
  `send_message` → `graph.ainvoke({"messages": [message]}, config)`
  (`instance_messaging.py:689`). The only prepend there is project context
  (`instance_messaging.py:1690`). **Nothing prepends shared context metadata.**

### 3. Design intent was system-prompt injection

The design docs confirm system-prompt injection was the **only** planned
mechanism:

- `.agents/shared/planning/shared-context-metadata/plan-overview.md:5` —
  *"inject that metadata into ALL agent types' **system prompts**"*.
- `.agents/shared/planning/shared-context-metadata/phase3-injection-layer.md` —
  the entire phase is `append_shared_context_metadata()` appending to the system
  prompt. The `---` separator in the design separates the metadata from the
  time/language sections **within the system prompt**, not a separator between
  shared context and a leader's request.

The commit `fb230c15` is literally titled *"feat: add shared_context_metadata
agent tool and system prompt injection"*.

### 4. Secondary timing issue (why it may be absent even from the system prompt)

The system-prompt injection is **static** — built once at spawn/restore and not
rebuilt on each incoming message. If the leader sets `test_message` /
`test_timestamp` **after** the dev instance is already spawned, the dev's system
prompt predates the metadata and will not contain the `# Shared Context` section
(until the instance is restored/restarted).

The dev can still read the values live via the `shared_context_metadata` tool
(`shared_context_tools.py:144`, which queries the repo directly) — which is what
the leader's test message asks for. So the **tool** works; the **injection**
does not reflect post-spawn writes.

### 5. Contrast: the one place message-body shared-context injection DOES exist

`get_shared_context()` (`context_injection.py:743`) formats a `# Shared Context`
block from **directory `.md` files** (a *different* system — the shared-context
directory, not the metadata KV) and prepends it to messages — but only for:

- **external opencode sessions** (`external_opencode.py` `_preload_shared_context`), and
- the **Explorer** agent (`knowledge_tools.py`).

It is not wired into internal leader→dev delivery, and it reads directory files,
not the metadata KV.

---

## Evidence (file:line)

| Claim | Location |
|-------|----------|
| Metadata KV injected into system prompt | `daemon/services/instance_lifecycle.py:678` (spawn), `:1652` (restore) |
| Injection function definition | `daemon/services/instance_lifecycle.py:208` |
| Injected section format (`# Shared Context` / `<shared_context_metadata>` / `---`) | `daemon/services/instance_lifecycle.py:305-310` |
| Internal message sent raw (no shared-context prepend) | `daemon/services/instance_messaging.py:689` |
| Project context IS prepended to message body | `daemon/services/instance_messaging.py:1690`, `:1722` |
| `## Related Project` formatter | `daemon/manager.py:234` (returns at `:301`) |
| Tool reads KV live (works regardless of injection) | `daemon/tools/shared_context_tools.py:144` |
| Directory-file shared context (external/Explorer only) | `daemon/services/context_injection.py:743` |
| Design: system-prompt injection only | `.agents/shared/planning/shared-context-metadata/plan-overview.md:5`, `phase3-injection-layer.md` |

---

## Options

If the product intent is for the child agent to see shared context metadata
**in the message body** (prepended, like project context), one of these is
needed. All are additive — the existing system-prompt injection can stay.

### Option A — Prepend metadata KV to the first message (mirror project context)

In the message-delivery path (`instance_messaging.py`, near the project-context
prepend at `:1690`), prepend a formatted `# Shared Context` block resolved from
the child's `context_key` to the first (non-report) message.

- **Pro**: matches the user's expected `[shared context] / separator / request`
  layout; consistent with how project context already works; always reflects
  the latest KV at delivery time (no spawn-time staleness).
- **Con**: duplicates the KV in two places (message body + system prompt) unless
  the system-prompt injection is removed for child instances; need a
  once-per-instance guard (like `project_injected`) to avoid re-prepending.
- **Reuses**: `SharedContextMetadataRepository.get_all_as_dict(context_key)` and
  the fence/escaping logic already in `append_shared_context_metadata()`.

### Option B — Keep system-prompt injection, fix the timing

Leave injection in the system prompt but make it reflect post-spawn writes, e.g.
re-resolve the metadata KV when restoring the instance, or document that
metadata must be set **before** spawning children.

- **Pro**: smallest change; preserves the design's "metadata at top of system
  prompt" placement.
- **Con**: does **not** produce the message-body layout the user expects; still
  not visible in the conversation log; staleness remains for long-lived
  instances unless restored.

### Option C — Both: message-body for child handoff, system prompt for ambient

System-prompt injection gives every agent ambient awareness; a one-time
message-body prepend on the leader→child handoff gives explicit, visible context
at the point of delegation. Gate the message-body prepend with a
`shared_context_injected` flag to run once per instance.

- **Pro**: best UX; visible at delegation, ambient thereafter.
- **Con**: most code; risk of KV appearing twice (mitigated by making the
  message-body block a terse summary + pointer to the tool).

---

## Recommendation

If the `[shared context] / separator / request` layout is the real product
intent (which the user's report implies), pursue **Option A** or **Option C**.
The message-body prepend should reuse the existing fence/escaping in
`append_shared_context_metadata()` to keep the prompt-injection defenses
(`ensure_ascii=True` + `<`/`>`/`&` replacement + 32k cap, fixed in `17828cba`).

If the design intent really was system-prompt-only, this is **not a bug** — but
the timing limitation in §4 should be documented so leaders know to set
metadata before spawning children, or instances must be restored to pick up
new metadata.

---

## Open Questions

1. Is the `[shared context] / separator / request` message-body layout the
   intended product behavior, or was system-prompt injection the deliberate
   final design? (The design docs say the latter.)
2. Should the message-body block appear on **every** message or only the
   **first** (mirroring the `project_injected` once-per-instance guard)?
3. For long-lived child instances, should the system prompt be refreshed when
   metadata changes, or is live tool reads sufficient?

---

## Tracking

- **Created**: 2026-07-12
- **Last Updated**: 2026-07-12
- **Status**: draft — pending decision on Option A/B/C
