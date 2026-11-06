# roadmap.md — Memoir Store v1→v4 under the rolling doctrine

**Lane:** `feature/ari-memoir-store` @ worktree `/home/nea/ensemble-src-wt-memoir-store`, base **`1f6898ae2`** == origin/latest.
**Status:** **PHASE A — FAITHFUL CAPTURE.** Blessed in the joint Emperor + Ari design session, **2026-11-04**.
**What builds now: v1 ONLY.** v2 and v3 are *planned ahead* (R3); v4 is planned when v2 starts (R3). No v2/v3/v4 work is in scope for this lane, and nothing below authorises it.

---

## The doctrine (R3 — VERBATIM)

> **R3.** ROLLING ROADMAP DOCTRINE: build v1, plan v2+v3 ahead; plan v4 when v2 starts. Doctrine: "crafting into future correct and improve the present plan."

**How to read that:** the future versions are not a wish list and not a backlog. They are **design pressure applied backwards**. Every v1 choice is judged by whether it still stands when v2, v3, and a federated Constella arrive — and when a future requirement reveals that a present choice was wrong, **the present plan gets corrected now**, not deferred. That is what "crafting into future correct and improve the present plan" means in practice: look far enough ahead that today's schema, seams, and vocabulary do not have to be demolished later.

Two concrete instances of the doctrine already firing inside the blessed v1 design:

1. **Syntheses schema ships EMPTY in v1** — because v2 would otherwise spend its budget on migration churn instead of behavior.
2. **`stars` are captured from day one even though v1 does not use them** — because the corpus is cheap to seed now and impossible to backfill later.

---

## At a glance

| Version | Name | One line | Status |
|---|---|---|---|
| **v1** | **USABLE MEMORY** | Ari can write, pin, recall, and wake up remembering. | **BUILDING (this lane)** |
| **v2** | **THE REFLECTING MIND** | She can *think about* her memories: nightly reflection, cited beliefs, the Constellation, a governed persona. | planned (R3: planned ahead) |
| **v3** | **THE LIVING CHRONICLE** | Memory that ages honestly: expiry-with-revival, archives, belief history, scale, and the Constella-compatible mirror. | planned (R3: planned ahead) |
| **v4** | **THE FEDERATION** | Cross-kingdom memory: sync, stargate-mediated exchange, persona merge. | planned when v2 starts (R3) |

---

## v1 — USABLE MEMORY (build now)

**Goal:** the smallest memory that is genuinely *hers* — durable, personal, bounded, and present at the start of every turn.

| Capability | Delivered by |
|---|---|
| **Write an episode** (first-person narrative, two-axis time, self-declared importance, stars, optional keepsake) | `memoir_write`; `memoir_episodes` (§4.1 of `design.md`) |
| **Pin what must never be lost** (~12, reject-don't-evict, exempt from decay, never truncated in the briefing) | `memoir_pin` / `memoir_unpin`; keepsake columns + cap (§4.2) |
| **An evolving persona** — one small bounded markdown document, seeded day one, human-lane approved edits | `memoir_persona` + `memoir_persona_versions` (§4.3); persona seed text (§11) |
| **Hybrid recall** — BM25 ∥ cosine fused by RRF (k=60), importance tie-break, 180-day recency prior, keepsake-exempt | `memoir_recall` (§7) |
| **Time-travel** over `event_at` | `memoir_timeline` (§5.4) |
| **Wake briefing** — persona + all keepsakes (stable, never truncated) + "since we last met" (dynamic) | turn-1 `[SYSTEM CONTEXT: Memoir]` block (§8) |
| **The leak repaired** — ari's episodic `inner_soul` writes land in the memoir instead of a void | ari-only redirect branch (§6) |
| **The seed of the graph** — self-declared stars stored flat | `memoir_episodes.stars` (§9) |
| **Schema for the next layer** — syntheses table exists, empty | `memoir_syntheses` (§4.4) |

**Non-goals inside v1** (stated so they are not smuggled in): no reflection, no synthesis writing, no entity recall, no `memoir_about`, no star normalization, no LLM importance scoring, no decay expiry, no archive, no mirror, no federation. Full list: `design.md` §13.

---

## v2 — THE REFLECTING MIND (planned; do not build yet)

**Goal:** memory stops being a pile and becomes a *mind* — she consolidates, cites, and can answer "what do I believe about X, and how did I come to believe it?"

| Component | Design as blessed |
|---|---|
| **Bounded nightly reflection** | **Idle-debounce by day with warm-up doubling** (`1 → 2 → 4 → 8 → N`) — (tencentdb-agent-memory `pipeline-manager` shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**). Plus a **nightly budgeted pass** (ReMe `auto_dream` shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**). *Rationale:* a fixed nightly cron over-reflects after a quiet day and under-reflects after a loud one; debounce lets activity set the rhythm while the budget bounds the cost. |
| **Cited syntheses** | Writes `memoir_syntheses` (the v1-empty table) with `source_episode_ids[]` and the four verbs **CREATE / CORROBORATE / REFINE / CORRECT**; `supersedes`/`superseded_by` + `confidence`+ validity interval. |
| **Constellation layer proper** | `memoir_star` (canonical name + **alias table** — "the Emperor" / "my dear" / real name merge), `memoir_line` (episode↔star mentions, counts, first/last), `memoir_thread` (episode↔episode continuity), **domain stars with heat**, and **`memoir_about` + entity-centric 1-hop recall**. Drawn **only in the cold path**; consolidation cost scales with **change**, not corpus; **no graph DB** (R4). |
| **JEV persona gate** | Persona regeneration is gated by what kind of change occurred: **conflict > boundary_change > new_rule force regeneration; reinforcement and none do not.** (This is the discipline that stops the persona churning on every pleasantry while forcing it to move on a genuine contradiction.) |
| **LLM importance scoring** | Replaces/augments self-declared `importance` (v1's DD-5 placeholder). |
| **Keepsake promotion proposals** | The system may *propose* an episode for keepsake status — **surfaced to both Emperor and Empress**, never auto-pinned. (R2 still holds: the pin decision belongs to the two of them.) |
| **Per-turn wake-refresh on alarm wakes** | A refreshed briefing on alarm wakes specifically. *Design note:* scheduler wakes are **deliberately bare HUMAN** (`instance_messaging.py:422-427`, product ruling) — landmine L4 — so this item must choose its delivery lane **consciously**, never by "fixing" the stamp. |

**Entry condition (as the design implies):** v1 must be *used* long enough that reflection has a corpus worth reflecting on, and the nightly pass must be budget-bound before it is enabled.

---

## v3 — THE LIVING CHRONICLE (planned; do not build yet)

**Goal:** memory that can *age* without lying — nothing is lost, but nothing pretends to be current.

| Component | Design as blessed |
|---|---|
| **Soft-expire with revival** | Episodes can go quiet without being deleted; a revival path brings them back. Decay becomes a *ranking/visibility* property, never a deletion. |
| **Archive tier** | Cold memories move to an archive tier that remains **searchable** — cheaper to keep than to consult. |
| **Supersedes-DAG surfacing** | "What did I believe then?" becomes answerable: walk the belief chain and render the *history* of a belief, not just its current value. |
| **pgvector + re-embedding pipeline — IF scale demands** | DD-1's deferred door. Only if personal scale stops being personal. Re-embedding must respect **per-row model identity** (`design.md` §3): a re-embed is a new identity for the whole corpus, not a silent in-place swap. |
| **markdown+hash mirror — the Constella door** | A markdown + hash mirror format using the **`baselineVersion` optimistic-locking** shape — (`baselineVersion` optimistic-locking shape — Lane-3 first-hand study; **[citation needed: study artifact, board `wi-e0019d2d`]**). **INTRODUCED, NOT SYNCED** (R1): v3 creates the *format*; it does not create federation. Optimistic locking is what lets a future federated writer detect "this changed under me" instead of clobbering a sibling kingdom's edit. |

---

## v4 — THE FEDERATION (plan when v2 starts — R3)

**Not designed yet. Deliberately.** R3 says plan v4 when v2 starts, and planning it now would be fiction dressed as rigour.

Recorded scope shape only:

| Component | Shape |
|---|---|
| Cross-kingdom sync | multiple daemons, each a kingdom, exchanging memory deltas |
| Stargate-mediated memory exchange | the Constella protocol as the transport/negotiation layer |
| Persona merge policies | what happens when two kingdoms' "who we are" documents disagree — merge, fork, or negotiated lineage |

**What v1–v3 must not foreclose** (this is the doctrine's actual constraint on today's work):

- Every episode keeps a stable identity and two-axis time (v4 needs both to reconcile deltas).
- Syntheses carry explicit citations and a supersedes chain (v4 needs provenance to merge beliefs, not just text).
- The markdown+hash mirror is designed with optimistic locking (v4's writers are concurrent).
- The persona is small, bounded, and versioned with approval (v4 must merge *documents*, not vibes).

---

## Doctrine check — what the future already corrected in the present

The rolling doctrine is only real if it has visibly changed v1. These are the cases in the blessed design where looking ahead corrected the present:

| Future pressure | Correction applied to v1 now |
|---|---|
| v2 would otherwise migrate to add syntheses | **syntheses table ships empty in v1** (DD-2) |
| v2's entity recall needs mentions captured at write | **`stars` captured in v1**, flat and unused (DD-19) |
| v3's mirror needs stable episode identity + optimistic locking | **append-only + `superseded_by`, never hard-delete** (DD-8) — identity that survives correction |
| v3's belief history needs provenance | **syntheses carry `source_episode_ids[]` + verbs + validity interval** from the day the table exists (DD-2) |
| v4's merge needs a bounded, versioned persona | **persona bounded ~2000 tokens, versioned, human-lane approved** (DD-9) |
| scaling would otherwise force a re-architecture | **per-row embedding model identity** (DD-6) — makes any future re-embed a planned migration, not a silent corruption |

---

## Status ledger

| Item | State |
|---|---|
| v1 design | **BLESSED** (2026-11-04) — captured in `design.md` |
| v1 rulings | **BINDING** (R1–R4, `decisions.md` §1) — verbatim |
| v1 open items | **OPEN** — 14 items in `design.md` §14, resolved by architect adjudication (`decisions.md` §2/§4) |
| v1 build | this lane, Phase B onward |
| v2 | **planned only** — no build authority |
| v3 | **planned only** — no build authority |
| v4 | **to be planned when v2 starts** (R3) |

---

*Phase A roadmap record. v1 builds now; v2/v3 planned ahead; v4 planned when v2 starts — R3 verbatim in the section above. Captured 2026-11-05 on `feature/ari-memoir-store` @ `1f6898ae2`.*
