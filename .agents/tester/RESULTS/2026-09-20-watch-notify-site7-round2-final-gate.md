# Final Verification Gate — Watch-Notify SITE-7 (Round 2)

- **Date**: 2026-09-20
- **Branch**: `feature/watch-notify-site7` @ `cc3a458b` · **Base**: `61cdfb28` (ANCESTOR-OK; 4 commits — see provenance note)
- **Mode**: VERIFY-ONLY — 0 commits, 0 repo modifications; 5 sanctioned mutation cycles all restored sha256-verified; both worktrees porcelain-empty at close.
- **Environments**: `/private/tmp/watch-notify-gate-r2/wt-head` (@cc3a458b) + `wt-base` (@61cdfb28); pytest 9.0.2 + pytest-timeout; dual-layer `timeout 300` + `--timeout=270` on every pack.
- **Workers**: 8 (infra ce37a4b0 · jq 63294a00 · new2 5be79bbf · adj fb544c1a · conc 4d7d08c3 · basejq a3aad7ce · baseadj ee699ea1 · static a782b6f4 · sim 6280d3be).

## VERDICT: ✅ SHIP

All 8 must-cover items green. Zero branch-caused failures (node-for-node differential both scopes). Census proven genuine + bite-proven for both new hooks. Site-7 pin proven by full RED matrix. Zero polling. ensure.md Core criticals PASS.

---

## 1. Packs + differential

| Pack | Leg | Counts | Runtime |
|---|---|---|---|
| `site7_jq_full` | HEAD | 1750P / **7F** / 38S / 3des | 69.41s |
| `site7_jq_full` | BASE | 1740P / **7F** / 39S / 3des | 64.25s |
| `site7_new2` (2 modified suites) | HEAD | **25/25 PASS** (21+4) | 1.58s |
| `site7_adjacent` (89 files, split 45/44) | HEAD | 2171P / **1F** / 34S | ~168s |
| `site7_adjacent` | BASE | 2171P / **1F** / 34S | ~170s |
| `concurrency_atomic_unit_test` | HEAD | **PASS** 98P/0F/74S | 11.64s |

**Differential (node-for-node):**
- jq 7F ≡ 7F — identical SET: watcher_repository_concurrent ×2 (:158/:315), job_feedback_observer:327, in_progress_guard ×2 (:428/:453), jober_watch_integration:935, phase2_feedback_verify:497. All known families; **zero UNKNOWN**.
- adjacent 1F ≡ 1F — `test_phase4_manager_decomposition.py:834` exact-kwarg pin rot (`pause_instance_cascade` now forwards `cascade_to_root=True`; manager.py untouched by delta). Known bug class; recommend test-debt fix.
- Passed delta jq +10 = 9 new/expanded tests + 1 skip→pass (38S vs 39S, non-material). Adjacent totals identical — the daemon delta (+131/−56) changed zero family outcomes.

## 2. Incident replay v2 (ac317f1c / stranded row 8ade5d22): PASS

`TestRootCompletionHook` 3/3 — drives REAL `_dispatch_post_commit_side_effects(outcome="root_completed")` on conftest in-memory SQLite (StaticPool): real fan-out over Task work_ids ∪ JobItem receipts (real `get_by_instance` + `find_jobs_by_instance`), per-kind resolver tokens, `get_watchers_for_job == []` post-flow proves **CAS-claim within the completion flow**, `[JOB_EVENT]` deliverable asserted at the message-queue boundary. Re-entrancy: second dispatch → len==1. The sequence that stranded live twice today now delivers end-to-end.

## 3. F10 token battery: PASS

`TestSiteF10ForceComplete` 3/3 on real `reconcile_drift_states`: done+failed → `failed` (:932); message-mirror → `settled` (:852); task-kind → `completed` default (:896). CAS-empty asserted each.

## 4. Resilience pins + Hook-B structure: PASS

- `TestResolverRaiseFallback` 2/2 — `_RaisingPerKindResolver` (raises only `per_kind_status_for`, everything else real): Hook A → fallback `completed` delivered + caplog `"root_completed ... per-kind resolve failed"`; Hook B → notify STILL FIRES + caplog `"F10_zombie_task ... per-kind resolve failed"`.
- **Structure verified verbatim** (job_recovery_service.py): resolve-try :1280-1292 and notify-try :1293+ are **SIBLINGS** — resolver raise → warning + token stays `"completed"` → notify reached unconditionally. Same shape in Hook A (child_reports.py:3926-3936 resolve / :3940-3948 notify). The re-stranding window is closed by construction.
- **Provenance note**: delta is 4 commits (leader's note said 5). Guard-rails (logged fallbacks) introduced by cc3a458b — a mixed docs+fix commit titled "docs(engine): site7 tidier"; f53f7098 (M3 per-kind) briefly introduced the unprotected window WITHIN the unmerged branch only.

## 5. Census: green + genuine + bite-proven

- Green 4/4; fixture = 30 CensusEntry (source-verified); walker probe: **TOTAL DISCOVERED 30 / FIXTURE 30 / UNCLASSIFIED 0**, output CONTAINS both new sites (`child_reports.py:3941` root_completed; `job_recovery_service.py:1294` F10) discovered FROM SOURCE (S4 `outcome="root_completed"` literal regex + `INSTANCE_COMPLETION_METHODS` M-surface). Growth 24→30: +5 hooked (root_completed, 3 manager M-surface wrappers, F10) + 1 exempt (new `layering` kind: `_process_child_completion_db_sync` @ :2634).
- **RED sims**: Hook-A notify-try deletion → `test_hooked_entries_point_at_live_notify_calls` FAILS naming `daemon/services/child_reports.py:3941` (×4 census entries) + 4 EVT co-failures (all TestRootCompletionHook + hook-a resilience). F10 deletion → FAILS naming `daemon/services/job_recovery_service.py:1294` + 4 EVT co-failures (all TestSiteF10ForceComplete + hook-b resilience). Bite is site-specific both directions.

## 6. Site-7 pin RED matrix: PASS (all arms fire)

`test_site7_exemption_note_stands_on_specific_evidence`: (a) note-deletion (27 lines) → FAIL `site-7 note missing required fragment: RE-AUDITED fix round 2`; (b) surgical fragment-removal (`round 2`) → same present-arm FAIL (proves non-self-satisfying split-concat needles); (c) retired-insertion (`Task-side writes all notify`) → FAIL `RETIRED site-7 fragment reappeared`. Intact → PASS. Every cycle restored sha256-verified.

## 7. Zero-polling: PASS

+131 added daemon lines: 0 mechanism hits (full battery incl. widened secondary set). Only 2 one-shot `asyncio.to_thread` sync-read offloads inside the completion flow. No new services/loops. Event-driven purity held.

## 8. Mock honesty: CLEAN

Census suite: 0 mocks/0 daemon imports (pure AST). Event-driven: `enqueue_message` AsyncMock at the delivery boundary (shape-correct), `queue_repo.get` MagicMock (None-semantics match), inert `lock_repository` ctor mock (F10 block never reads it), signature-identical sync test double delegating to real `complete_task` atomic write. Real repos + real notify chain underneath; no monkeypatch anywhere.

## 9. ensure.md (Core, scoped)

Criticals 4/4 PASS: scoped packs green (modulo differential-proven pre-existing) · concurrency pack 98P/0F/74S · sync-DB-on-loop via same pack · `dev.sh:102 --timeout-graceful-shutdown 10` PRESENT. Release Gate not run (surgical 2-file delta; not big/critical/architecture).

## 10. Gaps / deviations (disclosed, non-blocking)

1. 🟢 **Gap-1**: no dedicated RED node drives a NON-TERMINAL token through Hook A/B (terminal-filter at child_reports.py:3937-3939 exercised only implicitly). Follow-up candidate.
2. 🟢 **Gap-2**: census equality is one-directional in tests (walker→fixture); no reverse orphan pin / `== 30` cardinality assertion. Probe-true today (0 orphans). Follow-up candidate.
3. Leader's commit-count note (5) vs actual (4); guard-rails attribution resolved via `git log -S` (cc3a458b). Cosmetic.
4. jq skip-delta 39S→38S (one skip→pass migration, non-material).
5. `test_phase4_manager_decomposition.py` kwarg-rot — pre-existing both legs; test-debt follow-up (documented bug class).
6. Worktrees `/tmp/watch-notify-gate-r2/*` retired post-gate.

## 11. Verify-only attestation

Main checkout untouched (foreign `.agents/` drift pre-existing). All runs pinned HEAD + empty porcelain pre/post. 5 mutation cycles: one-live-at-a-time, never two dirty files, restore + porcelain + sha256 + green re-run between every cycle. Zero commits by the gate. RESULTS/PACKS = working-tree files only.
