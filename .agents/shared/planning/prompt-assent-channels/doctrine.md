# Doctrine — prompt-assent Phase 1.5 (designated channels)

> The Emperor's Word, relayed. The working path is conversation; the FE-UI is a deeper view.

**This extension inherits the parent lane's doctrine verbatim** —
[`.agents/shared/planning/prompt-assent/doctrine.md`](../prompt-assent/doctrine.md)
(*"FE-UI is a deeper view; the working path is conversation. The Emperor's word, relayed by C2
with provenance, is a first-class approval lane."*). Nothing in Phase 1.5 weakens it: the FE
approval lane stays byte-identical, the conversational path stays the primary path, and every
approval keeps its reconstructable provenance chain.

**This document records the ONE ruling that Phase 1.5 exists to implement.**

---

## The ruling — carried verbatim

> "this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now"

**Verbatim, quote-character for quote-character**, as relayed (Discord, 2026-11-11). The exact
string, fenced to preserve every character (no smart quotes, no re-flowing, no ellipsis):

```
this is channel, i often use channel to split tasks category in my mind, we consider channel is private/dm for now
```

---

## Context (recorded, not part of the quote)

The Emperor's real working surfaces are **Discord server channels** — one channel per task
category, deliberately used to split tasks in his mind. His ruling extends the assent lane's
privacy premise from *"only a 1:1 DM counts"* to *"a channel the operator designates counts as a
private/dm surface"* — **provided the designated channel is pinned to a specific human approver**.
Designated channels count as private assent surfaces; **designation is an operator act**, and the
designated channel's **pinned approver** is the only human whose turn inside it can mint or
approve.

The ruling is **newer than the staged v0.29.18 ceremony notes** — those notes carry the DM-only
K-3 probe and contain **no** designated-channels mention. This document is therefore the **first
tracked record** of the ruling anywhere in the repository.

---

## Status

| Field | Value |
|---|---|
| Status | **Ratified** (commission input, carried verbatim) |
| Ruling date | 2026-11-11 (UTC), Discord |
| Commission | `prompt-assent` Phase 1.5 — designated channels (pinned-approver model) |
| Date | 2026-11-11 (UTC) |
| Worktree | `/home/nea/ensemble-src-wt-assent-p15-channels` (branch `feature/assent-designated-channels`, base `efa8aaae28153b8e9c49b86abe19e92be5458ebe` = `origin/latest`) |
| Referenced by | `plan-overview.md`, `decisions.md`, `risks.md`, `refusal-cell-matrix.md`, `phase1-plan.md` … `phase4-plan.md` |
| Supersedes | the DM-only reading of the K-3 probe in `../prompt-assent/ceremony-notes.md:236-244` and `docs/runbooks/prompt-assent.md:160-185` — **this set's** adapted K-3 wording governs once ratified |

---

## How the rest of this planning set reads this doctrine

The ruling is a **north star**, not a spec. Where an implementation detail and the doctrine
disagree, the doctrine wins and the plan must be revised. Four consequences are load-bearing
throughout this set:

1. **"i often use channel to split tasks category in my mind"** ⇒ the designated channel is a
   **working surface**, not a test fixture. The acceptance test is *the Emperor minting in his
   real working channel*, not a synthetic guild channel. This is why the live probe
   (`phase4-plan.md` §4) names the Emperor's actual channel rather than a throwaway one.
2. **"we consider channel is private/dm for now"** ⇒ the privacy factor (AR-1 leg 2 — the parent's
   `../prompt-assent/architecture-recommendation.md:63`; **amended by this lane** — **D-13**) is
   satisfied by **either** a proven 1:1 DM **or** a designated channel. "For now" marks the
   channel-as-private posture as **provisional** — the channel is a proxy for privacy, not a
   privacy guarantee, which is exactly why the designated channel is **pinned to one human**.
3. **"private/dm"** ⇒ the designated channel is treated as **private-equivalent**, not merely
   "allowed". The ledger, the channel-binding leg (AR-1 leg 1) and the approver-binding leg
   (AR-1 leg 3) — both at `../prompt-assent/architecture-recommendation.md:63`, **amended by this
   lane** (**D-13**) — apply to it **unchanged**: a designated-channel approval is bound to the same
   channel and the same human as a DM approval.
4. **The channel is a category, not a room of record** ⇒ **threads** (Discord's sub-conversations
   inside a channel) are **not** designated by inheritance. A thread has its own id, its own
   visibility and its own lifetime; inheriting the parent's designation would widen the trust
   surface beyond the channel the operator actually named. See `decisions.md` **D-9**.

## What the doctrine does NOT say (deliberate scope)

- It does **not** say *any* channel may mint. The channel must be **operator-designated** and
  **pinned to one approver** — see `decisions.md` **D-3** (designation is an operator act) and
  **D-6** (the pinned-approver binding).
- It does **not** waive the three conjunctive factors, the single-use TTL'd token, the
  `render_sha256` binding, the append-only provenance, or the default-OFF kill-switch. Every
  one of them is **unchanged** (`decisions.md` **D-7**).
- It does **not** arm the lane. Arming stays a **separate, post-promote, Emperor decision**,
  gated on the **adapted** live K-3 probe (`decisions.md` **D-11**).
- It does **not** name a channel id, a guild, or a user id. The designation map is **operator
  configuration**, never an argument, never an agent act, never a tracked constant
  (`decisions.md` **D-4**).
