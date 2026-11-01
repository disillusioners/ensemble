# D2 Write Census — Pinned Artifact (Phase 1 Named Gate)

Date: 2026-10-31
Author: coder lane (Phase 1)
Provenance: leader ruling D2 (2026-10-31) + architect A2 (MUST, CRITICAL, 2026-10-31); census METHOD binding per planner ruling B1(a)-census (approver iteration 001, 2026-10-31) — a STATIC census of (i) base code reachable from index/search/load/feedback on the file path + (ii) the ALREADY-SPECIFIED Phase-3 STEP-0 contract. Verified against worktree `b56444244` + Phase-1 lane code.

---

## Method (binding statement)

Static enumeration of every DB write reachable from index / search / load / feedback on the file-skill path, over:
1. **Base code** as shipped at `b56444244` (the pre-existing injection-tracking + completion-hook machinery), and
2. **The ALREADY-SPECIFIED Phase-3 STEP-0 contract** (decisions.md D-Q5): `inject_explicit_skill` STEP-0 resolves the file skill BEFORE `clone_on_miss_sync`, renders from the fresh file body, returns `injected_skill_ids=[pf-pseudo-id]`, zero DB writes on the hit path itself.

Classification per the gate rule: (a) writes into FK-constrained child tables of `skills` (`skill_usage_records`, `skill_embeddings`, `skill_triggers`, `skill_lineage`, `skill_ab_tests` — all carry `ForeignKey("skills.id", ...)`), (b) writes into the lane's own tables, (c) non-DB writes.

## The census

| # | Write | Table (class) | Code path (verified) | Fires on the file path? |
|---|---|---|---|---|
| W1 | `last_injected_skill_ids` read-merge-write | `instances.instance_metadata` (JSONB UPDATE) — **not** a skills-child table | `daemon/services/instance_messaging.py` `_dedup_merge_skill_ids` (`:173-204`), driven from the injection branch (`:4431-4441`) after `inject_skills` / (Phase 3) STEP-0 return `injected_skill_ids` | **YES** — the pre-existing injection-tracking write the file path RIDES (STEP-0 returns `[pf-…]`; the merge persists the pf-ids into instance metadata). This is the write the census METHOD text names. |
| W2 | `SkillUsageRecord` INSERT | `skill_usage_records` (**FK-child** — `ForeignKey("skills.id", ondelete="CASCADE")`) | `daemon/services/skill_metrics_service.py` `record_task_completion` (`:300`) → `_record_one` (`~:719`, INSERT path `~:848+`) | **REACHABLE-AND-DRIVEN, produces NO row**: `_record_one` does `usage_repo.get_latest_for_skill_instance(pf-id)` → None, then `skill_repo.get(pf-id)` → **None** ⇒ WARNING + `return 0` **before any insert** (the skill-row-miss guard). Under base code the pf- usage signal is silently dropped (one WARNING per completion). The FK wall would reject the row anyway if the guard were removed. |
| W3 | `skills.total_completions` counter bump | `skills` (parent) | `_record_one` post-update branch | **NO** — unreachable when the skill row is missing (the W2 guard returns 0 first). |
| W4 | clone-on-miss / auto-load clone writes | `skills`, `skill_bank` | `daemon/services/skill_clone_service.py:143` (`clone_on_miss_sync`), `ensure_auto_load_skills_sync` | **NO** — the specified STEP-0 ordering makes these UNREACHABLE for file hits (Phase-3 pin: plant same-name bank template, `skills` row count unchanged). On a STEP-0 MISS the DB path runs byte-identically — that is the DB path, not the file path. |
| W5 | `SkillEmbedding` INSERT | `skill_embeddings` (**FK-child**) | `daemon/services/skill_embedding_service.py` `update_skill_embeddings` (per-row catch `:622-627`) | **NO** — the lane's refresh dispatch reuses ONLY `generate_trigger_queries` + `embed_text` (pure generation/embedding calls) and persists into the lane's own `project_skill_embeddings` (A1). The legacy `update_skill_embeddings` is never invoked for a file skill. |
| W6 | Lane-own index/refresh/usage writes | `project_skill_files`, `project_skill_embeddings`, `project_skill_usage_records` (all **NO FK** — A1/D2 by construction) | `daemon/services/project_skill_source/service.py` + `daemon/repositories/project_skill_file/repository.py` | **YES** — Phase-1 index sync + embedding refresh; Phase-3 feedback (companion fork) writes here. |
| W7 | `feedback_applied` / completion updates on usage rows | `skill_usage_records` (**FK-child**) | `record_feedback` / `update_completion` on the legacy repo | **NO for pf- ids** — Phase 3's `skill_feedback` on a `pf:` address writes the COMPANION table (W6 class), never the FK'd table (the fork decision below). |
| W8 | Trigger / lineage / A/B writes | `skill_triggers`, `skill_lineage`, `skill_ab_tests` (all **FK-child**) | evolution machinery | **NO** — P7 hard boundary; no evolution machinery is invoked anywhere on the file path (F1-confirmed for trigger generation: pure LLM call, no evolution imports). |

## Rule verdict (binding)

**The file path performs ZERO writes into FK-constrained child tables — the rule HOLDS.**
FK-child writes (W2, W5, W7, W8) are each blocked by construction: the `_record_one` guard (W2), the A1 dedicated-table dispatch (W5), the companion-fork routing (W7), and the P7 boundary (W8). The only non-lane write on the file path is W1 — an `instances`-table metadata write that pre-dates the lane and carries the pf-ids; it is not FK-constrained and is the usage-tracking signal carrier.

## Fork decision (leader ruling D2: DEFAULT = companion table if any usage write exists)

**A usage write EXISTS on the file path** — W1 (the injection-tracking metadata write carries pf-ids on every STEP-0 hit) and W2's insert path is REACHABLE-AND-DRIVEN (it executes for every pf-id, returning 0 via the guard). The census therefore does NOT prove "injection writes no usage row" — the opposite branch's precondition is not met.

**⇒ FORK = COMPANION TABLE `project_skill_usage_records`** (implemented in Phase 1: model `daemon/repositories/project_skill_file/models.py::ProjectSkillUsageRecord`, repository `::ProjectSkillUsageRecordRepository`, migration `daemon/migrations/versions/20261031_120002_create_project_skill_usage_records.sql` — **lane migration delta +3**). D-Q5's metrics value is preserved: `skill_feedback` on `pf:` addresses (Phase 3) records usage into the companion table (metrics-only — NO lineage rows, NO evolution dispatch, per P7), and the durable signal ("which file skills agents actually use") survives for git-PR evolution decisions.

## Re-open trigger (binding, carried forward)

If Phase-3 implementation deviates from the specified STEP-0 contract in a way that changes the write set (e.g. it stops returning `injected_skill_ids`, or starts writing usage rows itself), the D2 fork RE-OPENS per its own rule (default = companion table if any usage write exists) and must be re-adjudicated before staging. This sentence is duplicated in phase3 where STEP-0 is implemented (plan-set text).

## Phase-1 write-set statement (blast-radius reconciliation)

Phase 1 performs ZERO writes on any live path — the service is UNWIRED at this phase (construction/wiring lands in Phase 4); its only writers are its own tests against scratch DBs. The census itself is a read-only static artifact (this file). The phase1 blast-radius line "TOUCHED: none in existing source" is correct (approver B1).
