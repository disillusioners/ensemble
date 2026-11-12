# vsss-20261112 — version-scoped snapshot spaces — verification INTERIM (BLOCKED)

> ⚠️ **NOT A VERDICT.** This file is an evidence checkpoint written while the verification was blocked by an environmental LLM wallet exhaustion (`insufficient_balance`, cascade across 12/15 Wave-2 workers). **SHIP / NO-GO has NOT been determined.** Do not treat this as the mission deliverable; the final verdict file is `2026-11-12-version-scoped-snapshot-spaces-verification.md` (to be written after resume).

- Date: 2026-11-12
- Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-vsss`
- Branch: `feature/version-scoped-snapshot-spaces` @ **48e0ab662** (4 commits on base `6b644f770` = origin/latest tip)
- Blocker: LLM `insufficient_balance` cascade — Coding Plan quota exhausted, wallet empty (resets 2026-11-17; wallet does not replenish). Anomaly `15daba64` already filed; mission brief said top-up pending. One revive probe attempted (A/B base leg) → failed identically → retries stopped per recovery guidance.

## Gate evidence obtained (primary, executed)

### Gate 1 — SCOPE FREEZE @ 48e0ab662 → **PASS**
- `git rev-parse HEAD` = `48e0ab6623e30f05f78608fea629168ca1097c2a`; branch `feature/version-scoped-snapshot-spaces`.
- `git status --porcelain` = EMPTY at freeze time (before tester's own PACKS.md registration append).
- `git log --oneline 6b644f770..HEAD` = exactly 4 commits: `ac19e40ce` (feat core+tests+backfill) → `a1f66da38` (docs+SHA re-pin) → `5c81fbbbc` (REVIEW-1 fix: SQLite migration + consume-arm re-check) → `48e0ab662` (post-approve follow-up).
- `git diff --numstat 5c81fbbbc..48e0ab662` = exactly **2 files, +10/−5**: `daemon/migrations/versions/20261112_124200_add_snapshot_agent_space_key.sql` (1/1, header reword) + `tests/unit/test_snapshot_agent_spaces.py` (9/4, wrong-space outcome asserts + docstring).
- `merge-base --is-ancestor 6b644f770 HEAD` → rc=0.
- Blast radius (`6b644f770..48e0ab662`): **23 files** — incl. `daemon/manager.py` (+49), `daemon/registry.py` (+45), `daemon/repositories/snapshot/{models.py +42, repository.py +90/−3}`, `daemon/services/snapshot_{executor.py +49, search_service.py +39/−5, settings_utils.py +5/−2}`, `daemon/tools/{instance.py +32/−7, snapshot_tools.py +76/−8}`, `scripts/backfill_snapshot_agent_space.py` (+262), 3 touched test files, 8 agent-doc files.
- 4 SHA seals re-pinned in `tests/test_chart_image_delivery_e2e.py` (constants.py, ari/rule.md, developer/rule.md, leader/rule.md) — seals validated by the default-lane tripwire test `TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts` (35-entry baseline; NO marker override needed — premise correction vs brief).

### Gate 2 — scoped packs @ TIP
| Pack | Result | Evidence |
|---|---|---|
| `tests/unit/tools/test_snapshot_tools.py` (full) | **3 FAILED / 70 passed**, exit 1 | All in `TestUnifiedSpawnSnapshot`: `test_expired_top_match_cold` (:837), `test_internal_search_hit_warms` (:855), `test_stale_warm_carries_drift_note_in_citation` (:938). Payload: `top match <id> space mismatch on re-read (row=None != spawn='worker') — refused (fail-closed)`. Mechanism: vsss consume-arm belt-and-braces re-check (`daemon/tools/snapshot_tools.py:951-969`, landed 5c81fbbbc) refuses a re-read row whose `agent_space_key` is NULL against spawn key `'worker'`, so warm/expired citation paths never execute. **ATTRIBUTION PENDING (base leg died).** |
| registration-chain pin (gate 2b) | **PASS** (1P solo) | `TestRegistrationChain::test_gate_flag_enabled_on_targets` — expectation `sorted(enabled) == ["developer","developer[v2]","tester"]` @ :1847 |
| `tests/unit/test_sqlite_guards.py` | **PASS** 10/10, exit 0 | full collectable set (no parametrize; 10 methods) |
| `tests/unit/test_mission_record_migrations_sqlite.py` | **PASS** 6/6, exit 0 | collect-only census corroborated 6 nodes |
| `test/packs/concurrency_atomic_unit_test.sh` | **quarantine-aware PASS** | 94P/5F/74S, exit 1 bare (`set -euo pipefail` gotcha). 5 reds = QUARANTINE row 134 `DetachedInstanceError` in `tests/test_instance_metadata_atomic.py` (file ×5), pre-existing/parent-identical — does not fail the requirement |
| `tests/unit/test_snapshot_agent_spaces.py` (new pack, 31 tests) | **NOT RUN (worker died)** | — |
| migration comprehensive / critical-notes / `tests/migration/` lane | **NOT RUN (workers died)** | — |
| integrated regression (touched ∪ snapshot-family, derived; explicit 21-file developer baseline NOT preserved anywhere) | **NOT RUN (workers died)** | — |

## Anchors established (for resume; from read-only orientation)
- `compose_agent_space_key` def `daemon/registry.py:34`; callers `daemon/tools/instance.py:3519/3531`, `daemon/services/snapshot_executor.py:1252/1258`.
- Stage-0 predicate equality leg `daemon/repositories/snapshot/repository.py:596-605` (append `col(Snapshot.agent_space_key) == agent_space_key`; NULL rows never match); threaded from `list_active_by_project` (:481-511) → `list_with_filters` (:515+).
- Consume-arm re-check `daemon/tools/snapshot_tools.py:951-969`; internal search thread :930.
- OQ2 explicit-path tests: `TestExplicitPathVersionPermissive::test_explicit_id_reaches_null_space_row` (:675, NULL-key row → warm) and `::test_explicit_id_reaches_foreign_space_row` (:699, **v1-space row consumed by a v2 caller** — brief's "v2-stamped row from v1 spawn" wording is inverted; actual shape reported).
- Backfill CLI: `--db-url` required (no env default), dry-run default, `--apply`, exit 3 = column absent (pre-flight), exit 2 = bad args/unreachable, CAS-idempotent stamp, resolvable = live `instances` join → `compose_agent_space_key(agent_id, agent_tag)`; deleted-instance rows stay NULL.
- Migration SQL: `daemon/migrations/versions/20261112_124200_add_snapshot_agent_space_key.sql` — `ALTER TABLE snapshots ADD COLUMN agent_space_key TEXT; CREATE INDEX ix_snapshots_project_status_space ON snapshots (project_id, status, agent_space_key);` (SQLite plain DDL, no IF NOT EXISTS; PG path via `_ensure_postgres_columns`).
- New pack was **unregistered** at mission start → tester registered `snapshot_agent_spaces_unit_test` + the re-pinned `snapshot_tools_registration_chain_unit_test` rows in `PACKS.md` (vsss-20261112 block).

## Blocked / not obtained
- **Gate 4 (mutation proof A–E)** — not started (baseline gate died). Mutant specs fully prepared: A drop predicate leg; B bare-agent_id key; C unfiltered fallback; D remove consume-arm re-check (known belt-swallow residual → honest survival + warning-string remedy); E revert pin to bare `meta['id']`.
- **Gate 5 (A/B legs)** — the decisive attribution for the 3 reds (base @6b644f770 must show whether those ids are GREEN there). BASE leg worker died twice (initial + single revive probe).
- **Gate 3 (integrated regression re-baseline)** — not run.
- **Gate 6 (OQ2 shapes, explicit)** — the two tests live in the new pack; not run standalone (bodies read and shape-confirmed above).
- **Gate 7 (backfill scratch-DB matrix)** — not run.
- **ensure.md**: Tier doctrine noted — `daemon/manager.py` is a Tier-2 core-seam path, so a mechanical Tier-2 trigger exists; the mission's gate list defines a scoped dual-side coverage instead (to be documented in the final verdict either way). Critical: changed-packs PASS (partially evidenced), concurrency pack (quarantine-aware PASS), `dev.sh` grep (unrelated — dev.sh untouched, out of blast radius).

## Resume plan (one wave, after top-up)
1. Revive/spawn: new pack (+OQ2 explicit), migration comprehensive, critical-notes, `tests/migration/` shards A/B, integrated regression 1–3, A/B BASE + TIP legs, backfill matrix, mutants A–E (E gated on pin-solo green — already proven).
2. Aggregate → write `2026-11-12-version-scoped-snapshot-spaces-verification.md` with verdict.
3. **Decision still open**: if the 3 `TestUnifiedSpawnSnapshot` reds prove base-green (branch-caused), verdict = NO-GO (verification-only; fix cycle separately commissioned) unless the orchestrator rules otherwise.

---

## UPDATE — A/B BASE LEG COMPLETED (revive #1 succeeded) — KEY FINDING

BASE leg @ `6b644f770` executed on fresh scratch tree `/tmp/vsss-ab-base` (detached worktree; `uv sync --frozen` exit 0, 117 pkgs; Python 3.13.3; dual-cwd import proofs both → `/private/tmp/vsss-ab-base/daemon/__init__.py`; tree removed after capture).

| Cell (identical command both legs) | BASE 6b644f770 | TIP 48e0ab662 | Delta |
|---|---|---|---|
| `tests/unit/tools/test_snapshot_tools.py` | **1 failed / 72 passed** — ONLY `TestRegistrationChain::test_gate_flag_enabled_on_targets` (old pin: `snapshot_enabled targets drifted: ['developer','developer','tester']` vs expected `['developer','tester']`) | **3 failed / 70 passed** — `TestUnifiedSpawnSnapshot::{test_expired_top_match_cold, test_internal_search_hit_warms, test_stale_warm_carries_drift_note_in_citation}`; old pin now GREEN | **3 NET-NEW reds (branch-caused)** + 1 fixed |
| `tests/unit/test_governor_recursion_guard.py` | 10 failed / 13 passed (all `VisionModelNotAllowedError` via `compare_tools._verify_vision_allowed`) | pending (TIP leg revived) | — |
| `tests/unit/tools/test_instance_tools.py` | 2 failed / 207 passed (planner `meta.json tools.allow` missing `subtree_messages` / `subtree_status`) | pending | — |
| `tests/migration/` shard A (10 files) | 2 failed / 86 passed (BOOLEAN-INT-DEFAULT pin; `auth_sessions` PG `InvalidSchemaName`) | pending | — |
| `tests/migration/` shard B (10 files) | 0 failed / 97 passed / 3 deselected | pending | — |

**Decisive:** the three `TestUnifiedSpawnSnapshot` reds at TIP are **NOT pre-existing** — they pass at base and fail only after the vsss consume-arm re-check (`snapshot_tools.py:951-969`, landed in `5c81fbbbc`). Mechanism: the tests feed a canned search hit whose re-read row has `agent_space_key=None` while the spawn key is `'worker'`; the belt-and-braces re-check refuses cold (`space mismatch on re-read (row=None != spawn='worker')`) so the warm/expired citation paths never execute. Whether this is (a) intended fail-closed semantics with STALE test expectations (mission Option A says NULL rows never auto-warm → the tests should be updated to assert cold/no-hit) or (b) an over-strict belt breaking legitimate legacy warm-start is a REVIEW/DESIGN adjudication; from the verification side the fact is: **suite green at base → red at tip = branch-caused regression on gate 2a.**
