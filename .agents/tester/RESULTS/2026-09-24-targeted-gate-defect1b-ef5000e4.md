# Targeted Live Gate Report: DEFECT-1b closure — fix/watch-notify-delivery-gaps @ ef5000e4
Date: 2026-09-24 (session 05:25Z–06:0xZ)
Stack: 5f4e35b0 → 1e12944a → 89e4b397 → 439ec3e1 → 0d75e068 → **ef5000e4** (DEFECT-1b producer-side fix)
Method: same rails — real dev daemon (ensemble_dev), real Ari (0d0ef1f9), real LLM; byte-level Ari-vs-DB reconciliation; full evidence in worker reports.

## FINAL VERDICT: **GATE NOT PASSABLE — DEFECT-1b NOT CLOSED LIVE**

| Item | Verdict |
|---|---|
| 1. DEFECT-1b closure (task-kind Result: block) | ❌ **FAIL** — 4/4 completed envelopes Result:-less, byte-exact old 57-byte shape; race still observable (67ms) |
| 2. Error-leg asymmetry | ➖ INCOMPLETE-mechanism (3 honest attempts; no failed-terminal reproducer in timebox) |
| 3. Invariant probe (delta-arm + flip-probe) | ✅ PASS |
| 4. Midflight non-regression | ✅ PASS (⟳ 224B w/ CHECKF9; watcher survives; terminal exactly-once) |
| Mechanical (16 pins + 3 full dirs) | ✅ zero new reds (all green modulo established baseline) |

## Item 1 — the decisive evidence
- S1 (work 771b132a, task-kind): terminal [JOB_EVENT] = **57 bytes**, `[JOB_EVENT] Job 771b132a... completed ✓ / Agent: worker` — NO `Result:` — while Task.result carries `{"content": "FINWORD9"}` and the API resolver returns it.
- **Systemic**: 4/4 task-kind completed events this run (771b132a, c27a6c4b, ca309100, bafcc32b terminal) all Result:-less.
- **Race still live**: enqueue 05:36:06.698 vs Task.completed_at 05:36:06.765 (67ms before commit).
- **Discriminator on the SAME job (bafcc32b)**: mid-flight ⟳ envelope = **224 bytes WITH `Result:` + CHECKF9**; completed ✓ envelope = 56 bytes WITHOUT. The ⟳ leg threads content; the completed leg does not.
- Fix-site analysis: `task_processor.on_success` threading IS in source at ef5000e4 and its unit pins pass (incl. `test_on_success_threads_in_hand_result_summary_under_race`) — but it is **not the winning emit path live**. Candidate winners to instrument: `job_feedback_observer` post-commit outbox (:2059) / `_fire_watcher_notify_for_terminal` (:1444) with a checkpoint-lagging pre-fetch → None, CAS-claiming the watcher before on_success arrives; and `worker_pool.py:831` `_schedule_work_notification` (still passes NO `result_summary=`). Daemon-reload ruled out (fresh boot at ef5000e4, source verified in running checkout).
- Recommendation: instrument which site fires the CAS first for process_message completions; the fix must land on (or deduplicate through) the WINNING site.

## Items 2–4 (summary)
- Item 2: mechanisms tried — invalid agent (clean pre-row reject), invalid queue_id (**silently coerced to null** — side finding), context-overflow probe (read_file 96-line hard cap absorbed it — side finding). No failed terminal produced → error-leg carriage unverified; needs a dedicated fail-path harness / max_retries-exhaustion rig.
- Item 3: delta-arm verbatim (`Armed 0 live receipt(s), 1 already-settled receipt(s) skipped — no historical replay`); flip-probe 0/0.
- Item 4: M1 ⟳ delivered with full body + CHECKF9; watcher survived ⟳; terminal exactly-once; 40-line artifact; M1DONE.

## Mechanical (final HEAD ef5000e4)
| Pack | Result | Adjudication |
|---|---|---|
| Pins (defect5 ×6 + defect1 ×7 + defect1b ×3; A1 inside defect5) | ✅ **16/16** (7.7s) | all green incl. the race-modeling defect1b pins |
| Full tests/job_queue/ | 🟢 13F/1,875P/39S (62.6s) | EXACT baseline set; all 4 pin files pass inside sweep; wedge flake = documented jitter (failed this run, passed last) |
| Full tests/unit/services/ | 🟢 13F/1,900P (48.1s) | exact 1:1 baseline match; task_processor/worker_pool/resolver lanes 0 failures |
| Full tests/unit/tools/ | ✅ 2,841P/0F (63.8s) | all green |

**Pattern note (third consecutive commit): DEFECT-1b is unit-green / live-divergent.** The pins model the fixed site in isolation; live, another emit site wins the CAS. Emit-site attribution is the missing piece for the developer.

## Side findings (new, report-only)
1. `job_create` invalid `queue_id` silently coerced to null (job ca309100 ran on default queue) — operator trap; should reject or warn.
2. `read_file` ~96-line hard cap regardless of `limit` kwarg — caller-misleading; needs truncation marker or hard error.

## Safety ledger
Ambient-LIVE POSTGRES leak captured + scrubbed every lane; engine ensemble_dev verified; 9797/7979/8088 untouched; .env backup/restore discipline (restored at teardown, sha256 11db2814…); no fixes, no production edits; parked refs untouched; evidence logs in /tmp (daemon log, ari replies, artifacts).

## Teardown
Dev daemon graceful on single TERM to uvicorn 1050422 (identity-gated: port+cwd+fd checks; full tree exited ≤5s; "Graceful shutdown complete"); 8079 freed. LIVE 9797 (819745) + demo 7979 (800677) alive, lstarts unchanged. `.env` restored byte-identical (sha256 11db2814…; pre-restore real-LLM copy preserved at /tmp/e2ef-env-pre-restore.env). Git: zero tracked modifications; branch fix/watch-notify-delivery-gaps @ ef5000e4 intact. Evidence preserved in /tmp (daemon log 145KB, Ari replies, M1 artifact, probe files).
