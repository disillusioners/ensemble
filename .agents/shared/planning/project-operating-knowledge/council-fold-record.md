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
material, no code changes)". They exist **only** as identifiers in that commit
body; no planning doc, PACKS row, or receipt in this tree states their
substance.

| Id | Substance | Status |
|---|---|---|
| W2 | **OPEN — substance not reconstructable from any committed artifact.** | ACTION: the leader holds the code-council report; paste the one-line finding here before the ceremony so the disclosure is auditable. |
| A4 | **OPEN — same.** | ACTION: as above. |
| A8 | **OPEN — same.** | ACTION: as above. |
| A10 | **OPEN — same.** | ACTION: as above. |

> A disclosure whose only record is a commit body is not durable: commit
> messages are not grepped by the ceremony gates and are not part of any
> promote payload. Recording the identifiers here converts "four unknown
> labels" into an explicit, checkable gap — the substance still has to be
> pasted in by whoever holds the council report.

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
