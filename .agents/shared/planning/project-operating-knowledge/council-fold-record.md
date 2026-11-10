# POK — code-council fold record (durable companion to `7404949ef`)

**Purpose.** The council fold batch `7404949ef` ("W1/W3/W4 + A1/A2/A3/A9/A11/A12
(0C/4W/12A, all disclose+behavioral)") carries its adjudication **only in its
commit body**. Two classes of information in that body had no durable home:
the DISCLOSE-ONLY findings, and the pack-run claim. This file gives both a
home so the ceremony material does not depend on reading git history.

Lane: `feature/project-operating-knowledge`, base `6b571771d`.
Council verdict folded here: **0 critical / 4 warning / 12 advisory**.
Later commits: `0ae815e08` (the `POKRepository.stamp_last_verified` helper the
fold batch failed to stage).

---

## 1. Folded WITH code (implemented in `7404949ef`)

| Id | Substance (one line) | Where |
|---|---|---|
| W1 | ari seam once-per-project slot: single slot → per-project SET (`pok_ari_seam_injected_projects`); A→B→A no longer re-injects A | `daemon/services/pok_ari_seam.py` |
| W3 | stamp `last_verified` at INSERT **and** on the no-op echo, so `[stale Nd]` decay is reachable without a count bump | `daemon/services/pok_service.py`, `daemon/repositories/pok/repository.py` |
| W4 | the §4.8 stage-2 section-cap re-render loop moved off the event loop (`asyncio.to_thread`) | `daemon/services/context_messages.py` |
| A1 | pin the `_fits` derivation arithmetic in a test instead of reserving the 47c wrapper inside the admission budget | `tests/unit/services/test_pok_injection_unit.py` |
| A2 | render-time flatten of the slug slot (defense-in-depth) instead of a slug CHECK on the table (would drift dev-vs-live PG) | `daemon/services/pok_injection.py` |
| A3 | `uq_pok_active_slug` loser surfaces as the typed `pok_slug_conflict` envelope, not a raw `IntegrityError` | `daemon/services/pok_service.py`, `daemon/tools/pok.py` |
| A9 | `_stable_id_for` docstring id-format table gains the pok row | `daemon/services/context_messages.py` |
| A11 | AC11 max-entropy leg re-run with the true worst row (44c slug + stale-marked escaped body) | `tests/unit/services/test_pok_injection_unit.py` |
| A12 | comment-only: the per-variable env scrub is HYGIENE, the fence is file-only env + `SELF_ENV=dev` + `current_database()`/`current_user()` | `scripts/pok/seed_llmproxy_payload.py` |
| (A-8/W3 helper) | `POKRepository.stamp_last_verified` — landed one commit LATE (`0ae815e08`) | `daemon/repositories/pok/repository.py` |

## 2. DISCLOSE-ONLY — no code change (ceremony material)

Recorded in `7404949ef` as "W2 + A4/A8/A10 are DISCLOSE-ONLY (ceremony
material, no code changes)". The four identifiers were originally
substance-less (see the gap note below); they have since been filled in
from the code-council report during the final micro-fold
(`6cc250e9c` + `0ae815e08`), plus the D-3/D-4/D-5/M9/M10/M11/L11-13
disclosures relayed in the same micro-fold batch.

| Id | Substance (one line) | Status |
|---|---|---|
| W2 | Per-dispatch assembly is the design's freshness mechanism (§4.5.1); the ari-seam adaptation delivers the operation view ONCE per (instance×project) — no refresh on repeat dispatches, and post-compaction suppression applies. Routed: restore the freshness mechanism via the `set_injection` stable-id/supersede channel in Phase 2. | DISCLOSE (no code) |
| A4 | `pok_curate` pin-cap-2 check-then-act TOCTOU — two concurrent curators can overshoot to 3. Curator-only, bounded (≤3). | DISCLOSE (no code) |
| A8 | Per-entry overhead comment assumes a 10-digit stale mark; a 6-digit day-mark costs 15-16c — the 332c/entry bound still holds inside the 92c slack. | DISCLOSE (no code) |
| A10 | Every `pok_query` pays an up-to-the-active-cap-row bookkeeping UPDATE + an N+1 lineage walk; the insertion path does not. Bound derives from the admission cap, now 4x the v1-at-64 judgement (live value: `decisions.md` **A-13** `[active cap 256 — Emperor directive 2026-11-10]`) — re-disclosed at the 2026-11-10 raise. v1 acceptable; Phase-2 perf pass. | DISCLOSE (no code) |
| D-3 | `POK_ARI_SEAM_INJECTED_PROJECTS` is an unbounded set (typical usage is one project per instance; optional cap/LRU is a Phase-2 option). | DISCLOSE (no code) |
| D-4 | No-op-echo in-memory `last_verified` may trail the DB re-stamp (conservative — clock-skew only). | DISCLOSE (no code) |
| D-5 | A11 asserts ≤2,900 + per-entry ≤332, not the exact 2,803. | DISCLOSE (no code) |
| M9 | `stamp_last_verified` return value is discarded — in-memory state advertises "refreshed" even if the row vanished. | DISCLOSE (no code) |
| M10 | `ensure_embedding` never-raise docstring vs `upsert_embedding` outside the try + sync-in-async (dormant — the hook is not yet invoked). | DISCLOSE (no code) |
| M11 | Query-path read/rank is unwrapped; embedding-lag swallowing hides the lag. | DISCLOSE (no code) |
| L11-13 | Unwrapped laundering scan; raw exception text in agent-visible envelopes; no counter on the degraded path. | DISCLOSE (no code) |

> The four originally-OPEN rows (W2/A4/A8/A10) were substance-less at
> `7404949ef` time; their substance was reconstructed from the
> code-council report during the final micro-fold and recorded here so the
> disclosure is auditable without reading git history. The additional
> D-3/D-4/D-5/M9/M10/M11/L11-13 rows are the micro-fold's own disclose set.

## 3. Record-integrity correction — the pack-run claim

`7404949ef` ends with:

> `Packs re-run: ari-seam 13/13, injection 37/37, service 43/43, tools 20/20,
> context_messages 82/1 (1 skip). Tree clean.`

Three of those four are consistent with the tip counts and with the fold's own
deltas (ari-seam 12→13, injection 35→37, tools 19→20). The `service 43/43`
claim **cannot have been true at `7404949ef`**: that commit's tree calls
`POKRepository.stamp_last_verified`, and the helper only exists from
`0ae815e08` — the immediately following commit says so itself ("the helper …
was not staged with the rest of the change; without it the no-op path raises
`AttributeError` at runtime"). The no-op-echo leg is green only from
`0ae815e08` onward.

Verified re-derivation at tip (`0ae815e08`), `--collect-only`: service **43**,
injection **37**, ari-seam **13**, tools **20**. `context_messages 82/1` is
reproducible: `tests/unit/test_context_messages.py` collects **83** (82 passed
+ 1 skipped).

**Lesson (house-reusable):** a fold that stages a helper used by its own new
leg must re-run the pack in the SAME staging step, or the recorded result is
fiction. `git status` clean ≠ the change is complete — only the pack run says so.

## 4. Cross-references

* Plan set: `plan-overview.md`, `architecture-recommendation.md`,
  `decisions.md`, `discharge-round-1.md`, `discharge-round-2.md`,
  `implementation-readiness.md`, `trust-tier-addendum.md`.
* Pack registrations + counts: `.agents/tester/PACKS.md` (POK sections).
* Phase-3 seeding receipts: `seed-payload-llmproxy.md`, `hygiene-actions-llmproxy.md`.
