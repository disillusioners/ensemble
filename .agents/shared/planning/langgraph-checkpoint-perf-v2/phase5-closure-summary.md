# Phase 5 — T5.18 PR5 Closure Summary (branch READY-FOR-USER-REVIEW)

> Recorded by: coder (Phase-5 closure implementer)
> Date: 2026-09-04 (UTC)
> Branch: `feature/langgraph-checkpoint-perf-v2`
> Branch tip at closure: `de7b3f78` (3 new closure commits atop the pre-existing v2 base; 382 v2-only commits since v1 fork `c37c870c`)
> DSN discipline: every DSN-resolving invocation carried BOTH `POSTGRES_URL=postgresql://ensemble@localhost:5432/ensemble_cpv2_test` AND `POSTGRES_DB=ensemble_cpv2_test`. `ensemble_prod` / `ensemble_dev` never referenced.
> PG version: PostgreSQL 14.22 (Homebrew) on aarch64-apple-darwin — matches Phase 0 T0.2 + T0.3 baseline and the PIN-PARITY ≥14.22 requirement.
> Standing constraints honored: DSN pinning on every DSN-resolving command; disposables only; v1 branch READ-ONLY; NO merge, NO push; explicit-path staging; NEVER touch `.agents/approver/active.md`, `.agents/shared/planning/job-task-retrospective/`, `.agents/shared/planning/defer-gate-fix/`, `QUARANTINE.md`, `.agents/tester/RESULTS/**`; never `except BaseException:`; prior commits (incl. honest-red history at `98d0df49`) stay untouched — everything I do is ADDITIVE.

## Closing Statement

The `feature/langgraph-checkpoint-perf-v2` branch is **READY-FOR-USER-REVIEW** following Phase 5 closure. The implementation work (Phases 1-5) is complete; the user decides promotion to `latest` per C-1; the tester validation phase is pending. No merge or push was performed; branch has no upstream.

---

## Phase 5 Task Outcomes

### T5.1 — Binding Gate (FR-1 / AC-1.1) — **PASS**

- **Doc:** `phase5-binding-gate-results.md`
- **Result:** 9/9 GREEN on real disposable PG (`postgresql://ensemble@localhost:5432/ensemble_cpv2_test`, PostgreSQL 14.22, PIN-PARITY satisfied). Matches v1 `7a7998fe` baseline (9/9). 0 failed, 0 skipped (SKIP-LOUDLY contract did not fire; PG was reachable). DSN-pinned for every command; `ensemble_prod`/`ensemble_dev` never referenced.
- **Reviewed code SHA (art. §6):** `e52d845e`
- **Tip at T5.1 run time:** `d5f3a2b0`
- **Post-T5.19 regression re-run (B5):** 9/9 GREEN — no regression from the prune work.
- **Closure re-run (this commit):** 9/9 GREEN — same DSN-pinned command, exit 0.

### T5.7 — PR4 Formal Re-Review (FR-8 / AC-8.1..8.4 / NFR-11) — **PASS, VERDICT APPROVED, LOOP CLOSED**

- **Artifact:** `.agents/reviewer/memories/2026-09-04-pr4-blob-prune-race-fold-re-review.md` (171 lines, 7 sections per architect §2.2)
- **Verdict stamp:** ✅ **APPROVED** — 0 🔴 / 0 🟡 / 0 🟢 new findings from the port.
- **Reviewer-instance:** `reviewer[v2]` controller under tree-root `347fa33b-b135-4f20-b1f4-f61aad722924`; council governor `06ac38f1-3156-4d36-89e1-6e6ec5c33b8e` (per artifact §6 — authorship is NOT the implementer, per AC-8.4)
- **Reviewer-personal 9/9 GREEN @ `e52d845e`** on disposable PG; reviewer-bracketed (`e52d845e` BEFORE and AFTER the run — no drift); exit 0; 4.04s.
- **Artifact commit SHAs:** `e2c15f99` (initial commit, 171 insertions, 1 file) + `9edd57ac` (§6 commit-SHA fill-in per spec; 1 insertion / 1 deletion). Both committed by explicit path.
- **Loop closure:** per artifact §7 — APPROVED ⟹ v1 PR4 NEEDS_CHANGES loop is CLOSED; no NOT-RESOLVED or REGRESSED entries.
- **Pointer doc:** `phase5-rereview-results.md` (developer-side cross-reference for the closure summary).

### T5.9 — D-1 prod `channel_versions` JSONB shape verification (FR-11 / AC-11.1, AC-11.2) — **PASS, ACTUAL EVIDENCE FILED**

- **Doc:** `docs/runbooks/prod-channel-versions-evidence-2026-09-XX.md` (commits `b537dfbd` + `237d3eba` per phase5-results history)
- **Outcome:** upgraded from defer-with-signoff (`237d3eba`) to ACTUAL evidence (`b537dfbd`) — read-only `SELECT jsonb_pretty(checkpoint->'channel_versions') ... LIMIT 5` against `ensemble_prod` per C-3 (the only write-path to `ensemble_prod` allowed, and it's a SELECT). Blob-row round-trip count via `jsonb_each_text` join included.
- **Status:** evidence filed; AC-11.1 + AC-11.2 PASS.

### T5.10 — D-2 seq-index decision (FR-12 / AC-12.1) — **DEFER with EXPLICIT TRIGGER**

- **Doc:** `phase5-seq-index-decision.md`
- **Decision:** DEFER per architect §2.3. Do NOT add `ix_message_metadata_seq` in Phase 5 closure.
- **Re-trigger conditions:** (a) a seq-ordering consumer lands (OOS-1 cursor pagination), OR (b) `EXPLAIN ANALYZE` on `get_for_thread` at measured N (1k / 100k / 1M) shows degradation.
- **Cost numbers:** ~10 MB index at 1M rows; 10-15% INSERT overhead on every tap (2-4 inserts per turn per Phase 2 review §3); buys nothing today.
- **Row-growth data feeding T5.19:** 10k rows / 992 KB table + 96 KB index at the binding-gate PG; with `delete_for_thread` (T5.19) the steady-state is bounded.

### T5.11 — D-3 retry-recovery test (FR-13 / AC-13.1..13.3) — **PASS, 4/4 PART-2**

- **Part 1 (Risk-6 pause-injection):** documented deviation — pause-injection infeasible in v2 (no pause entry-path tap); flagged for v3 follow-up.
- **Part 2 (AC-13.3 read→revive→read on real PG):** 4/4 PASS per `tests/integration/test_message_metadata_retry_recovery.py::TestReadReviveRead`:
  1. pre-revive snapshot byte-identical post-revive ✅
  2. new tail message non-null `created_at` ✅
  3. `synthetic-system-{iid}` id identical both reads ✅
  4. `alist_count == 0` both reads ✅
- **send_message-revive coverage gap flagged** for the tester phase (NOT a Phase 5 closure blocker).
- **DSN-pinned, real PG 14.22; exit 0.**

### T5.14 — FR-14 backfill disposition (architect §2.4 corrected Criteria A′/B′/C′) — **DROP**

- **Doc:** `phase5-backfill-disposition.md`
- **Decision:** DROP the backfill (Solution N). Pre-side-table history stays as-is; pre-PR2 messages display with their `state.ts` fallback timestamps.
- **Criteria:**
  - **A′ TRUE** — `state.ts` fallback suffices for UI display (accepted degradation, non-breaking per `daemon/persistence.py:368-371`).
  - **B′ TRUE** — `created_at` is the only consumer; covers first-appearance for any tapped message.
  - **C′ TRUE** — §3 row-growth defect addressed by `delete_for_thread` prune (T5.19), NOT by backfill.
- **All three true ⟹ DROP** (architect §2.4 expected outcome).

### T5.16 — Final drift-regression suite — **PASS, 0 new deltas (after additive fix)**

- **Doc:** `phase5-final-results.md`
- **Result:** 31 suites enumerated and executed; 0 new deltas vs Phase 0/2/3/4 baselines (after the additive test fix below). 9 pre-existing failures (7-node mission stale-fixture family + compaction-guard sentinel + queue-routing MagicMock-await) unchanged from Phase 0.
- **WC-wake kill-switch:** **default-OFF confirmed** at `daemon/services/instance_messaging.py:147` (`raw = os.environ.get(_WC_WAKE_ENV, "0")`); no enabling env in repo.
- **6 vocabulary grep guards:** ALL PASS (settled ratification, `'done'` alias, canonical `TERMINAL_STATUSES`, exactly 4 active `tap_node_return` call sites, migration ordering 0819→0825, no re-introduced atomicity claim).
- **Facade-forwarding guards:** both PASS.
- **Gate suite self-test:** 2/2 PASS (manifest 41 files / 535 tests, current per `tests/integration/gate_suites/GATE_SUITES.txt` post-T5.8 regen at `e1d3e630`).
- **T5.16 NEW DELTA FOUND + FIXED:** `tests/unit/persistence/test_get_instance_messages_no_alist.py::TestZeroAlist::test_manager_without_repo_attribute_degrades` caplog filter expected legacy wording `"message_metadata_repo missing/None"`, but commit `8281acc2` (Phase 5 T5.12 FR-6 degradation path) refactored `daemon/persistence.py:497` to emit a structured reason category (`repo_missing` etc.). Minimal additive one-line test filter swap committed at `de7b3f78` with inline comment citing the causing commit + closure doc pointer + emitting site. 16/16 PASS post-fix; full PG-bound sweep re-run at 114/114 PASS.

### T5.17 — Branch discipline audit — **PASS**

- **Doc:** `phase5-branch-audit.md`
- **Result:** 382 v2-only commits; zero merges on the v2-only delta; no `git merge latest` rewrites; no rebase; zero mass-stage commits (zero commits touch >50 files); zero forbidden paths staged or modified by the closure work; all 5 forbidden paths per the brief remain as-found (`.agents/approver/active.md`, `.agents/shared/planning/job-task-retrospective/`, `.agents/shared/planning/defer-gate-fix/`, `QUARANTINE.md`, `.agents/tester/RESULTS/**`); my 3 new closure commits are single-file, explicit-path, minimal-diff; branch has no upstream; no push performed.

### T5.19 — `message_metadata` side-table prune (MERGE PRECONDITION per risk R1) — **PASS, 4/4 + 9/9 regression re-run + reviewer APPROVE**

- **Doc:** T5.19 wired per architecture-recommendation.md §3 + plan-overview.md "MERGE PRECONDITION" bullet; the prune itself was implemented in commit `41347ee4 feat(perf): T5.19 message_metadata side-table prune — delete_for_thread + _cleanup_instance wiring (merge precondition)`.
- **Repo + wiring:** `MessageMetadataRepository.delete_for_thread(thread_id)` (PG + SQLite); wired into `daemon/services/maintenance.py::_cleanup_instance` AFTER `adelete_thread`, BEFORE the in-memory callback (architect §3 cited anchor — exact function `_cleanup_instance`, NOT `_cleanup_instance_state`; S3 disambiguation). Never-raise wrap per W3.
- **Real-PG acceptance test:** `tests/integration/test_message_metadata_prune.py` — populate → tap → assert N rows → `_cleanup_instance` → assert 0 rows + checkpoints gone.
- **Post-T5.19 binding-gate regression re-run (B5):** 9/9 GREEN per `phase5-binding-gate-results.md`.
- **Reviewer-approved:** per the v2 PR4 re-review (T5.7), the prune work is in scope and approved at `e2c15f99`.
- **ORPHAN SWEEP NOTE (closes the reviewer's doc-coverage flag):** the optional orphan sweep query (`SELECT mm.* FROM message_metadata mm LEFT JOIN checkpoints ck ON ck.thread_id = mm.thread_id WHERE ck.thread_id IS NULL`) is documented in `phase5-plan.md T5.19 W3` as a non-gating follow-up; `delete_for_thread` carries the never-raise semantics so orphans are preferable to broken instance teardown. The orphan sweep is NOT a Phase 5 merge gate.

### AC-3.2 RESOLUTION (dispatcher Option a, 2026-09-04) — **RESOLVED**

Per `phase5-perf-results.md` §AC-3.2 RESOLUTION + `phase5-perf-depth-diagnosis.md`:

1. **ANALYZE precondition** — harness runs `ANALYZE checkpoints / checkpoint_blobs / checkpoint_writes` on the disposable DB's pool connection after `_populate_thread` and before every measurement. Diagnosis H1 — post-ANALYZE the cached prepared statement on the saver connection re-plans against fresh stats, blob subplan collapses from `Seq Scan on checkpoint_blobs (20001 rows)` to `Index Scan using checkpoint_blobs_pkey (probe)`, and the saver connection's read `Execution Time` collapses from 8.557 ms to 0.064 ms at depth 10000 (a 133× drop).
2. **Component-gated variance (AC-3.2 / NFR-4)** — gated metric is `aget_ms` from `_measure_aget_component(n_iter=N_TIMED)` — the depth-sensitive component per diagnosis H1/H2. Wall-clock stays reported-not-gated.
3. **OR-logic threshold rule** — `< 0.10 relative CoV OR < 2.0 ms absolute delta` between `component(depth=10000)` and `component(depth=150)` at page_size=100. Gate passes if EITHER form holds.
4. **2.0 ms choice justification** — ≫ the observed depth-spreads across multiple pilot runs (0.06–1.4 ms; 2× the worst observed); ≪ the pre-fix regime (12 ms wall-clock at depth 10000); ≪ the wall-clock budget at the 1000-msg cell (~12 ms).
5. **AC-3.3 policy (per W1d)** — `(100, 400)` gate moves to component basis; `(100, 150)` stays wall-clock-gated. Wall-clock reported regardless.
6. **Honest-red history at `98d0df49`** (variance-cell realism + N_TIMED=10) — preserved; new commits land as additive.

### Commit SHAs for AC-3.2 RESOLUTION + dispatcher Option a

- `e52d845e` — `docs(perf): T5.5 depth-growth diagnosis — STOP-gate root-cause evidence`
- `98d0df49` — `fix(perf): variance-cell realism (real history depth + N_TIMED=10) + review doc-truth fixes`
- `ecae4003` — `fix(perf): AC-3.2 harness re-basis per dispatcher Option (a) — ANALYZE precondition + component-gated variance`
- `d3fa0d97` — `docs: AC-3.2/NFR-4 measurement-basis fold + runbook planner-cache ops note`

---

## Runbook Ops-Note Reference

- `docs/runbooks/checkpoint-blob-prune-restore.md` — §7 intra-process race disclosure + §6 backup covers recovery (operational destructive-enable remains governed by this runbook; the SERIALIZABLE wrap narrows but does not eliminate the µs window; §6 backup remains the recovery of record).
- `docs/runbooks/checkpoint-blob-prune-restore.md` §7 — also cites `aio.py:82, 280-304, 393-399` (the aput-non-atomicity retraction).
- Planner-cache ops-note added at `d3fa0d97` (docs commit) per AC-3.2 RESOLUTION.

---

## DEVIATIONS LEDGER (complete)

| # | Deviation | Where | Resolution | Notes |
|---|---|---|---|---|
| 1 | **T5.11 Part-1 Risk-6** — pause-injection infeasible in v2 | `phase5-plan.md T5.11` | Documented + flagged for v3 follow-up | NOT a v2 deliverable; v3 may revisit when pause-injection entry-path lands |
| 2 | **send_message-revive coverage gap** | `tests/integration/test_message_metadata_retry_recovery.py` | Flagged for tester phase | `daemon/services/instance_messaging.py:1506` revive path verified by code inspection; dedicated test recommended for the tester validation phase |
| 3 | **3cadcaf2 message carries superseded 0.065× claim** | `feat(perf): Phase 5 T5.3+T5.4+T5.5+T5.6+T5.8+T5.15 ...` (commit message) | Doc supersedes (`phase5-perf-results.md` RETROACTIVE-FIX SUPERSESSION NOTICE); history not rewritten | Honest-red pattern: doc supersedes; the commit message history stays as-is per the "everything ADDITIVE" constraint |
| 4 | **F9 checkpoint_metrics `_bucket` hoist** | `daemon/services/checkpoint_perf.py` | Deliberately not churned | Per phase5-plan.md the F9 hoist was a follow-up scope; v2 keeps the in-line form; v3+ may revisit if profiling shows hot-path impact |
| 5 | **median-vs-mean pilot rejected** | `tests/performance/test_message_api_cost.py` harness design | Documented in harness (commit `98d0df49` honest-red) | Mean-of-N_TIMED is the documented baseline; median pilot rejected due to estimator-floor variance |
| 6 | **AC-3.3 implemented as per-run OR rather than fixed policy** | `tests/performance/test_message_api_cost.py` `test_2x_baseline_anchor` | Documented in `phase5-perf-results.md` + dispatcher adjudication | Wall-clock OR component; per-run logging shows which basis holds; recorded policy |
| 7 | **T5.19 sync-method D14 + `manager.py` 5th-file deviations** | `daemon/services/maintenance.py::_cleanup_instance` | Resolved (per phase4-results.md phase-5 plan §D14) | Sync-method verified, manager.py touch-point resolved against Phase 2's `_db_connection_repository` block |
| 8 | **T5.16 NEW DELTA — caplog filter staleness** | `tests/unit/persistence/test_get_instance_messages_no_alist.py:201-206` | Fixed via additive commit `de7b3f78` | Test filter aligned with FR-6 structured reason category emitted by `daemon/persistence.py:497` post-`8281acc2` |
| 9 | **D1-D14 across phases** (consolidated) | Phase 0/1/2/3/4/5 results docs | All D-rows resolved per their respective phase plan | D-1 prod evidence ACTUAL (T5.9), D-2 seq-index DEFER w/ triggers (T5.10), D-3 retry-recovery PART-2 PASS (T5.11), D-4 reviewer artifact APPROVED (T5.7), D-5..14 closed across Phases 0-5 per their phase plan |

---

## SC-1..14 Status Table (per plan-overview.md)

| SC | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | PR5 binding gate GREEN on real PG | **PASS** | 9/9 GREEN per `phase5-binding-gate-results.md` (T5.1 + B5 + this closure re-run); 0 skips |
| 2 | Invariant `message_api_checkpoint_list_total == 0` OBSERVED | **PASS** | 3/3 GREEN per `tests/integration/test_get_instance_messages_observed_count_zero.py` (N=10 random thread ids + AST vacuous-literal guard) |
| 3 | Perf matrix — cost ∝ page size, NOT history | **PASS** | 12/12 GREEN per `tests/performance/test_message_api_cost.py` (6-cell matrix + variance + 2× baseline + decomposition + NFR summary); AC-3.2 / NFR-4 via dispatcher Option a (component-gated variance) |
| 4 | Armed-absence alist test | **PASS** | 9/9 GREEN per `tests/integration/test_armed_absence_alist.py` (AST call-func scan + repo-wide grep guard + live-path exercise + armed fixture self-check) |
| 5 | Every saver op emits structured log + metric exposed | **PASS** | 32/32 GREEN per `tests/unit/persistence/test_checkpoint_perf_logging.py` (4 ops + env + caplog + metrics surface + counter + histogram + log-suppress-even-when-suppressed + Prometheus exposition) |
| 6 | Degradation-path WARNING | **PASS** | 5/5 GREEN per `tests/unit/persistence/test_checkpoint_perf_logging.py::TestDegradationWarning` (manager_missing + repo_missing + repo_exception + row_absent + AST guard pinning `except Exception:` + `except BaseException:` rejection). [Correction 2026-09-04, Fix 2]: The `repo_exception` test case asserts that the emitted token IS the exception's class name (e.g. `reason=OperationalError`), NOT the literal string `repo_exception`. Implemented contract at `daemon/persistence.py:481` (`reason={type(exc).__name__}`); the literal-token set is `manager_missing` | `repo_missing` | `row_absent` (`:467-469, 491-496`). The 5/5 GREEN claim remains true — tests assert the class-name emission contract. |
| 7 | Guardrail AST scan | **PASS** | 6/6 GREEN per `tests/integration/test_no_saver_imports_in_routers.py` (0 `.alist(` calls in `daemon/routers/**`; allowlist EMPTY) |
| 8 | PR4 re-review artifact exists | **PASS** | `.agents/reviewer/memories/2026-09-04-pr4-blob-prune-race-fold-re-review.md` at `e2c15f99` + `9edd57ac`; APPROVED verdict; reviewer-instance ID `reviewer[v2]` (NOT the implementer); all 7 sections present per architect §2.2 |
| 9 | Gate manifest regenerated | **PASS** | 4 `chore(gate): regen manifest at <sha>` commits (one per PR closure cycle) + final regen at `e1d3e630` (T5.8); current manifest is 41 files / 535 tests per `tests/integration/gate_suites/GATE_SUITES.txt` |
| 10 | Deferred-item disposition filed | **PASS** | D-1 ACTUAL evidence (T5.9); D-2 DEFER w/ triggers + numbers (T5.10); D-3 retry-recovery PART-2 4/4 PASS (T5.11); AC-13.3 read→revive→read 4/4 sub-cases |
| 11 | Backfill criteria evaluated | **PASS (DROP)** | All three Criteria A′/B′/C′ TRUE per `phase5-backfill-disposition.md`; live-path backfill UNACCEPTABLE; offline-only shape documented but NOT needed |
| 12 | Drift-regression verification suites PASS | **PASS** | 31 suites enumerated + executed per `phase5-final-results.md`; 0 new deltas (after additive fix); 9 expected pre-existing failures unchanged from Phase 0 |
| 13 | 5 quarantined pre-existing failures stay quarantined | **PASS** | `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive` ×5 failures remain in `QUARANTINE.md` rows 25-29; no port-introduced failures |
| 14 | Branch discipline preserved | **PASS** | 382 v2-only commits; 0 merges on v2-only delta; 0 mass-stage commits; 0 forbidden paths staged/modified per `phase5-branch-audit.md` |

**Total: 14/14 PASS** (no FAILs, no PARTIALs).

### Plus the architecturally-required supplementary criteria:

| Criterion | Status | Evidence |
|---|---|---|
| AC-13.3 read→revive→read (architect §5 guardrail row 2) | **PASS** | 4/4 sub-cases per `tests/integration/test_message_metadata_retry_recovery.py::TestReadReviveRead` (per T5.11 Part-2) |
| AC-3.2 RESOLUTION (dispatcher Option a) | **RESOLVED** | ANALYZE precondition + component-gated variance + OR-logic threshold (relative <0.10 OR absolute <2.0 ms); commits `ecae4003` + `d3fa0d97` + honest-red `98d0df49` preserved |
| T5.7 PR4 re-review loop closed | **CLOSED** | APPROVED verdict per artifact §7; v1 PR4 NEEDS_CHANGES loop no longer open |
| WC-wake kill-switch default-OFF | **CONFIRMED** | `daemon/services/instance_messaging.py:147` default `"0"`; no enabling env in repo |

---

## Phase 5 Closure Commit Trail

| SHA | Subject | Files | Purpose |
|---|---|---|---|
| `e2c15f99` | `docs(review): T5.7 PR4 re-review artifact — reviewer-authored, APPROVED, loop closed` | `.agents/reviewer/memories/2026-09-04-pr4-blob-prune-race-fold-re-review.md` (+171) | Initial commit of the reviewer-authored artifact by the developer (per dispatch constraint) |
| `9edd57ac` | `docs(review): T5.7 artifact §6 commit-SHA fill-in` | `.agents/reviewer/memories/2026-09-04-pr4-blob-prune-race-fold-re-review.md` (+1 / −1) | Post-commit bookkeeping per phase5-plan.md T5.7 §6 spec (artifact commit SHA inline field); fills in `e2c15f99`; the reviewer's verdict was recorded against `e52d845e` and is unchanged |
| `de7b3f78` | `fix(perf): T5.16 closure — align no-alist caplog filter with FR-6 structured reason category` | `tests/unit/persistence/test_get_instance_messages_no_alist.py` (+1 / −1) | Additive one-line test filter update to align with the structured reason category emitted by `daemon/persistence.py:497` post-`8281acc2`; 16/16 PASS post-fix; full PG-bound sweep re-run at 114/114 PASS |

(T5.18 closure summary + audit + final results doc + rereview-results doc are staged together in the final closure commit per the brief's T5.18 instruction.)

---

## Residual Risks for the Tester Phase (named)

1. **Perf noise sensitivity** — the `(100, 400)` anchor cell is documented noise-flaky on the wall-clock basis; component basis is the load-bearing gate per dispatcher Option a. The harness methodology (ANALYZE precondition + OR-logic threshold) is locked in `ecae4003`. The tester phase should be aware that single-run wall-clock violations on (100, 400) are within the documented ±2-6 ms process-noise floor; the gate moves to component basis. See `phase5-perf-results.md §AC-3.2 RESOLUTION` for the OR-logic threshold justification.

2. **Pre-existing quarantined failures** — 9 failures stay in the v2 baseline (7-node mission stale-fixture family in QUARANTINE.md row 44 + 1 compaction-guard sentinel in row 42 + 1 queue-routing MagicMock-await in row 42). The tester should NOT flag these as regressions; they are unchanged from Phase 0.

3. **Part-1 pause-injection gap (T5.11 Risk-6)** — pause-injection was not testable in v2 (no pause entry-path tap); flagged for v3 follow-up. The tester should NOT attempt to test the pause-injection path on v2; the documented deviation per `phase5-plan.md T5.11` covers this.

4. **send_message-revive coverage gap** — the `daemon/services/instance_messaging.py:1506` revive path is verified by code inspection but does not have a dedicated test. Recommended for the tester phase to add a targeted test.

5. **Honest-red history at `98d0df49`** — variance-cell realism + N_TIMED=10 — the tester should preserve this commit verbatim; the closure does NOT rewrite history.

6. **T5.19 ORPHAN SWEEP NOTE** — the optional orphan sweep query (`SELECT mm.* FROM message_metadata mm LEFT JOIN checkpoints ck ON ck.thread_id = mm.thread_id WHERE ck.thread_id IS NULL`) is documented in `phase5-plan.md T5.19 W3` as a non-gating follow-up; `delete_for_thread` carries never-raise semantics so orphans are preferable to broken instance teardown. The tester should NOT add this to the binding gate.

7. **PR4 SERIALIZABLE wrap narrows but does not eliminate the µs aput-race window** — per artifact §7 + runbook §7; the §6 backup remains the recovery of record. The tester should NOT attempt to test the µs window elimination directly; the wrap + race tests in `tests/integration/checkpoint_prune_real_saver.py` cover the structural narrowing + retraction, NOT the window elimination.

9. **WC-wake kill-switch default-OFF** — `ENSEMBLE_WC_WAKE_ENQUEUE` is OFF in the repo. The tester should NOT flip it ON without explicit operator sign-off per the dispatcher's standing risk note; the kill-switch is the instant-revert path for ≤2wk soak or on incident.

---

## Final Branch State

```
Branch: feature/langgraph-checkpoint-perf-v2
Tip:   de7b3f78  (closure end-of-work)
Base:  feature/langgraph-checkpoint-perf @ c37c870c  (READ-ONLY v1 fork)
Commits since v1 fork: 382
Merge commits on v2-only delta: 0
Mass-stage commits: 0
Forbidden paths staged/modified by closure: 0
Upstream (push target): NONE (no push performed)
Untracked-junk status: unchanged (5 pre-existing untracked items + 2 new closure docs)
```

---

## End-of-Closure Statement

The branch is **READY-FOR-USER-REVIEW** following Phase 5 closure. The user decides promotion to `latest` per C-1. The tester validation phase is pending.

No merge was performed. No push was performed. The branch has no upstream. All work is local on `feature/langgraph-checkpoint-perf-v2`.