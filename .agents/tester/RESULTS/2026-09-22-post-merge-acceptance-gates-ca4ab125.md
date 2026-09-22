# Post-Merge Acceptance Gates — latest @ ca4ab125 (midflight-qa-channel merge)

**Date:** 2026-09-22 (13:14–13:41 UTC) · **Target:** `/home/nea/ensemble-src`, branch `latest` @ `ca4ab1259428e42a42bf005bf2283023a4c6cf1e` (2-parent --no-ff merge: `b024bb09` v0.13.10 lineage + `56248e9c` feature/midflight-qa-channel tip, 18 commits)
**Verdict: ✅ ALL GATES PASS — ZERO merge-caused failures.** Both guarantee sets survive simultaneously on the merged base: (A) v0.13.10 result-arm (EventKind.JOB_COMPLETED + content stamping + resolver surfacing + ERROR extraction) and (B) Q&A channel (5 QA event kinds + pause-deadlock resolution + wedge guard).
**Read-only mission honored:** HEAD pinned `ca4ab125` before/after by every worker; no branch switch, no code edits, no commits, no push/tag; ports 9797 (live) / 7979 (demo) untouched (PIDs 30696 / 540168 stable across all legs); worktree `/home/nea/ensemble-worktrees/qa-channel` untouched; no servers left running (8079 + mock-LLM 4124 verified freed after each daemon leg).

## Workers (12 dispatches, 0 re-dispatches)
recon `f5cb2c57` · g1a `b8d44b50` · g1b `f18d6cb3` · g1c `3d9ab4f7` · g1d `9c6f081a` · g1e `302da102` · g2a `82142dff` · g2c `014f822f` · adjA `ea60a6f3` · adjB `248b2c43` · adjC `0657e27c` · g2b `45694bfa`

---

## GATE 1 — Q&A channel acceptance: **8/8 criteria PASS, 54/54 suite tests**

| Criterion | Evidence | Verdict |
|---|---|---|
| (a) Question surfaces as event to watcher | midflight pack: `TestQuestionSurfacesToWatcher` green in 42/42 (5.87s) | ✅ PASS |
| (b) Answer resumes asker in-context | `test_answer_gate_resume_chain.py` **10/10** (1.72s) + `test_answer_resume_real_chain.py` **2/2** (1.14s) — real banner-8/9 chain, real `_schedule_explicit_handle_resume` + `_resume_cascade_db_sync`, exactly-once CAS; the formerly-uncommitted real-chain test is now committed-green on the merged tree | ✅ PASS |
| (c) Report event non-blocking | `TestReportNonBlocking` green in 42/42 | ✅ PASS |
| (d) No polling introduced | `TestNoPollingIntroduced` 6/6 green in 42/42 | ✅ PASS |
| (e) Completed-event Result bodies | `job_completion_acceptance_test` **30/30** (4.49s; both legs 15+15) | ✅ PASS |
| (f) No new failures vs base | jq-full 14F = 11 known-baseline + 3 **v0.13.10-caused pre-existing** (A/B-proven, below); adjacent 4F = 3 pre-existing-both-parents + 1 load artifact; **zero merge-caused** | ✅ PASS (adjudicated) |
| (g) Boot gate | BOOT-PASS (Gate 3, below) + `boot_probes_unit_test` **75/75** (10.21s) | ✅ PASS |
| (h) Wedge guard | `TestWedgeGuardOneShot` 5/5 + cap pins green in 42/42 | ✅ PASS |

**Suite totals (branch suite re-run on merged base):** midflight 42/42 + answer_gate 10/10 + real_chain 2/2 = **54/54** (mission's "~52" estimate → actual 54 collected, 54 passed; recon collect-only confirmed 42+10+2).

**Static merge-resolution check (recon):** `daemon/repositories/event/models.py:12-54` — all 6 required EventKind members present and co-existing: `JOB_COMPLETED` (v0.13.10) + `QUESTION_REQUESTED`, `QUESTION_ANSWERED`, `MIDFLIGHT_REPORT`, `STUCK_AWAITING_ANSWER`, `CHILD_QUESTION_STILL_PENDING` (branch). Enum docstring documents lane safety (QA kinds excluded from JobFeedbackObserver accepted set).

### Criterion (f) detail — job_queue full-dir regression
`tests/job_queue/` serial, scrubbed, `timeout 300`: **14F / 1817P / 38S / 3DS in 247.89s** (baseline 11F/1820P/38S/3DS @ branch gate; delta exactly +3F/−3P).

- **11 = known pre-existing baseline** (node-level reconciliation): terminal_write_census ×2 (drift content now includes NEW unlisted site `manager.py:5047 _on_stale_task_permanent_failure` — merge-added code inside an already-red census; census refresh = existing test-debt) · wedge_resolver event-loop RuntimeError ×1 · dev_sh hardcoded-path env-defect ×1 · instance-derived-status ×4 (`test_in_progress_guard.py` ×2 + `test_job_feedback_observer.py` ×1 + `test_phase2_feedback_verify.py` ×1 — observer routes `notify_watchers` status through `_work_resolver.per_kind_status_for()`; test mocks don't stub it) · default-watch-events `'settled'` ×3 (ancestry-proven in branch base, commit `05618c55`).
- **3 = NEW at this gate, PRE-EXISTING on the v0.13.10 lineage — NOT merge-caused** (adjC A/B proof):
  `test_event_driven_completion.py::TestSite1InlineMirrorFinalize::{test_hook_fires_settled_and_dual_fire_is_deduped, test_pre_terminal_task_completes_on_success_fully, test_hook_skips_when_finalize_races_to_none}`
  Verbatim signature (all 3 identical): `await callbacks.on_success(MagicMock(name="ProcessingResult"))` → `task_processor.py:979 on_success` → `asyncio.to_thread` → `task/repository.py:2602 complete_task` → `result_json = json.dumps(result)` → `TypeError: Object of type MagicMock is not JSON serializable`.
  **Discrimination:** FAIL ×3 at base `b024bb09` (lineage where v0.13.10 restored the content arm `{"success":…,"content": result.result_content,…}` → Mock reaches `json.dumps`); PASS ×3 at feature tip `56248e9c` (payload `{"success":…,"message_id":…}` only — no content arm). Any lineage containing b024bb09 + these tests fails identically; the merge merely united them. **These 3 have been failing on latest since v0.13.10 landed** (the v0.13.10 gate ran the 30/30 acceptance pack, not the full job_queue dir). Fix family: make the tests' ProcessingResult payloads JSON-serializable (real string/dict or spec'd fake) — test-side fix commission.

### Criterion (f) detail — adjacent units (60-file derived set + 3 cap-dropped files)
**1231P / 4F / 27S in 82.87s** + dropped-files sweep **94P / 0F** (`test_pause_instance_cascade` 2P/17S, `test_child_outcome_payload_surfacing` 5P, `test_upgrade_tools` 87P).

4 failing nodes — full adjudication (solo 3× retry budget on merged tree + A/B legs at both parents):
| Node | Solo (merged) | b024bb09 | 56248e9c | Attribution |
|---|---|---|---|---|
| `test_in_progress_guard.py::…Guard::test_completed_with_no_waiting_runs_normal_path` | F/F/F | FAIL | FAIL | **Pre-existing** (member of the known instance-derived-status baseline family) |
| `test_in_progress_guard.py::…Guard::test_waiting_for_none_treated_as_zero` | F/F/F | FAIL | FAIL | **Pre-existing** (same family) |
| `test_worker_notification.py::…::test_multi_worker_notification` | P/P/P | PASS | PASS | **Load artifact** — parallel-pack execution race; 1F/9P across all legs, 6/6 solo. Not classically flaky under identical conditions; document as load-sensitive |
| `test_enqueue_shared.py::…::test_triggers_title_on_idle_to_running` | F/F/F | FAIL | FAIL | **Pre-existing on both parents** (drift: `run_async_no_wait` bridge called 2× vs expected 1; plain-MagicMock patch + unawaited-coroutine warnings). Outside the branch gate's coverage — surfaced only because this adjacent set is broader |

Verbatim tracebacks for the three deterministic pre-existing failures captured in worker adjA report (in-tree `--tb=long`): in_progress_guard pair → `assert <MagicMock name='mock._work_resolver.per_kind_status_for()'> == 'completed'` at `tests/job_queue/test_in_progress_guard.py:428|453` (+4 MagicMock-await WARNINGs at job_feedback_observer 3108/3217/2175/4622); enqueue_shared → `assert 2 == 1` at `tests/test_enqueue_shared.py:498` (+`RuntimeWarning: coroutine '_maybe_store_initiative_message' was never awaited`).

**→ Zero merge-caused failures. All exceptions pre-existing with A/B or ancestry proof.**

---

## GATE 2 — v0.13.10 acceptance + four emission surfaces: **PASS (30/30 + Intent5 1/1, 4/4 surfaces baseline-equivalent)**

**Acceptance pack re-run (mock layer, no daemon):** `RESULT: PASS-WITH-SKIP (mock layer PASS; intent5=SKIPPED: Daemon not running at localhost:8079)` — exit 0, **30/30** in 4.49s (15+15 across `test_job_result_summary_and_gate.py` + `test_round2_council_fixes.py`). All 4 intent points green. Skip shape = the pack's documented F5 contract.

**Intent5 live-mock leg (sanctioned `./dev_with_mock.sh` on 8079, mock LLM on 4124):** livez 200 at t=2.1s; **Intent5 1/1 in 12.94s** (baseline 12.96s — noise-level drift). Per-surface vs v0.13.10 baseline (93804b1b-era, all-green incl. DB row non-null):

| Surface | Merged @ ca4ab125 | Baseline | Δ |
|---|---|---|---|
| (a) Per-job SSE payload (non-empty Result body, F6a envelope content) | ✅ PASS | ✅ | none |
| (b) `GET /api/jobs/{id}` result_summary (non-null, ≠ fallback, content truthy) | ✅ PASS | ✅ | none |
| (c) DB `job_completed` event row persisted PRE-emission, `data.result_summary` non-null | ✅ PASS | ✅ | none |
| (d) Global `/api/notifications/stream` notification carries result_summary | ✅ PASS (mock) | ✅ (mock) | none |

KNOWN pre-existing live-demo-only defect (global stream zero-delivery on live demo, distinct emitter path) correctly did NOT trigger at mock level and was not chased, per commission.
Safety note: the F1 env-guard refused the worker's first Intent5 attempt (ambient `POSTGRES_DB=ensemble_prod` inherited by the TEST process — scrub had wrapped only the daemon boot). Corrected invocation scrubs BOTH daemon and test. Zero live-DB contact at any point (guard fired before any query).

**Live demo check: NOT warranted** — demo (7979) runs promoted v0.13.10 (`releases/v0.13.10`), not the merged tip `ca4ab125`; a live check would validate the wrong code. Mock level is the bar for this mission (and mock Intent5(d) passing vs live-demo zero-delivery remains the documented env divergence, unchanged by this merge).

---

## GATE 3 — Boot gate: **BOOT-PASS**

- ensure.md citations: Release-Gate prerequisite line 37 (`./dev.sh`, health at localhost:8079), line 38 (SSL unset), Core #4 static (dev.sh:99/102 `--timeout-graceful-shutdown 10` present).
- Scrubbed boot (`env -u POSTGRES_* -u DATABASE_URL`, `SSL_CERT_FILE= SSL_CERT_DIR=`): port 8079 verified free pre-boot → **livez 200 at t=3s** `{"status":"alive","version":"0.13.10"}` (self-ID pre-bump — informational, no version commit on the merged tip), **readyz 200 all-green** (database/queue_freshness/services true), `Application startup complete`. DB resolved = **ensemble_dev @ localhost** (connection rows verified) — scrub proven load-bearing; the known SQLite-migration `20260714_000001` adjudication path was not even exercised (dev resolves PG).
- Teardown: process tree collected (pgrep -P), every PID port-verified 8079 before TERM; 8079 freed; 9797/7979 untouched (PIDs stable).
- Corroboration: `boot_probes_unit_test` **75/75**; import sweep of all **22 merge-diff-touched production modules** → `IMPORT_SWEEP_OK` (0 ImportError).

---

## Findings for follow-up commissions (ALL pre-existing, none merge-caused, no fixes applied per mission)

1. 🟠 **v0.13.10 test regression on latest (highest value):** 3 × `TestSite1InlineMirrorFinalize` fail on any post-b024bb09 lineage (MagicMock payload × restored content arm). Failing on latest since 2026-09-22 morning; invisible to the 30/30 acceptance pack. Test-side fix: JSON-serializable result payloads.
2. 🟠 `test_enqueue_shared.py::test_triggers_title_on_idle_to_running` — pre-existing on both parents (bridge call_count 2≠1 + unawaited-coroutine warnings); likely needs AsyncMock bridge patch. Outside prior gate coverage.
3. 🟢 Census drift content extended by the merge (`manager.py:5047` unlisted terminal-write site) — known family; census refresh remains open test-debt.
4. 🟢 `test_multi_worker_notification` — load-sensitive (1F/9P; 6/6 solo). Not quarantined (ad-hoc pack, not recurring; no identical-condition flake in 3× budget).
5. 🟢 Baseline family-label drift: v0.13.9-era "proxy_phase1 ×7" decomposes on the merged tree as instance-derived-status ×4 + default-watch-events ×3 (node-level reconciliation done above).

## Scope Decision
Full re-run of the commission-prescribed packs on the merged base (this gate's purpose); no full-repo suite — blast radius = merge footprint (45 files: 22 production + 8 test), covered by the 5 commission packs + acceptance + boot_probes + boot probe + A/B legs. ensure.md Core #2/#3 concurrency pack out of scope per ensure.md's own blast-radius rule (no concurrency-surface change in footprint); Core #4 static PASS.

## Documentation state
- PACKS.md: Active-commission outcome line appended (post-merge gate, 2026-09-22).
- LESSONS/2026-09-22-postmerge-gate-lessons.md: written (F1-guard-scrub-wraps-test-too gotcha; A/B worktree attribution pattern; family-label drift).
- Files intentionally LEFT UNCOMMITTED per mission guard (read-only, no commits).
