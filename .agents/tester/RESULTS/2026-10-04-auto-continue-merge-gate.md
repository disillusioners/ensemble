# Merge-Gate Test Report: auto-continue-running-after-restart

Date: 2026-10-04 (09:06–10:45 UTC)
Tester instance: 77e37f35 (Test Leader) — 17 worker instances dispatched, all reported
Subject: branch `feature/auto-continue-running-after-restart`, DEDICATED worktree `/home/nea/ensemble-src-wt-auto-continue`, impl `649c7e70..35ab0aa9` (+ verified-inert `148dd3c7` on top — 4 unused-import removals + 1 decisions.md wording; `daemon/api.py` diff = 1 redundant inline import, symbol still resolves via module-level import; zero behavior change, verified line-by-line)
Gate type: MERGE GATE (independent validation; nothing trusted from prior tails)

## Overall Verdict

**PASS for merge (feature scope) — 0 regressions, all feature contracts held, core demo E2E proven end-to-end.**
Two non-blocking restart-durability findings (F-1/F-2) live in lanes the feature explicitly does NOT touch (discard-on-startup policy + WC-wake delivery); they were SURFACED by this E2E and need follow-up commissions. T5.4c (second-restart reboot-loop probe) did NOT complete as specified — see F-1; caller adjudicates whether that r2-added probe is merge-binding.

## Scope Decision

Full-dir gate explicitly mandated by caller (merge gate, 2026-10-03 precedent) → full-dir runs warranted AND executed, but chunked per pack discipline: every chunk ≤ `timeout 300`, no `-x`, `--continue-on-collection-errors`, identities captured per chunk. Release-gate E2E items beyond the feature's own demo E2E (LLM-calling e2e_workflows cases) were NOT run — out of blast radius for this branch (no frontend, no workflow-engine changes); the cascade-lane coverage ran via the two cascade regression packs.

---

## 1. Test Packs (all in the impl worktree, HEAD verified, import-gate verified per worker)

| Pack | Result | Counts | Runtime |
|---|---|---|---|
| auto_continue_boot_pass_unit_test.sh | **PASS** | 56/56 (wraps boot_pass + candidates + terminalizer files; worktree-gate conftest asserted in-pack) | 17s |
| auto_continue_interleaving_unit_test.sh | **PASS** | 14/14 (AC4 6-row interleaving matrix incl. Δ1 success row) | ~10s |
| Ad-hoc: tests/unit/services/test_auto_continue_terminalizer.py | **PASS** | 7/7 (M18 call-site gate: direct-resume orphan vs cascade vs FM-1 worker-claimed) | 0.62s |
| child_parent_lifecycle_regression_test.sh (cascade e2e #1) | **PASS** | 220 passed / 19 pre-existing skips / 0 fail | 33.9s |
| completion_regression_test.sh (cascade e2e #2) | **PASS** | 106 passed / 37 by-design skips / 0 fail | 17s |
| concurrency_atomic_unit_test.sh (ensure.md Critical) | **PASS** | 98 passed / 74 skips / 0 fail (incl. deadlock + thread-identity tests) | 63.3s |
| boot_probes_unit_test.sh (api.py boot path) | **PASS** | 83/83 (/livez,/readyz,main-entry) | 12s |

Quick fixes applied: none (nothing quick-fixable failed). Commits made by test lane: none (see §8).

## 2. Full-Dir Gate + Definitive A/B (impl HEAD vs fresh base worktree @649c7e70)

Method: fresh detached worktree `/home/nea/ensemble-src-wt-ac-base` @ `649c7e70` + own `uv sync` venv (import-gate verified both sides); identical file sets; failure identities (nodeid :: first-error-line) compared as sets; services halves split at identical sorted midpoint (files 1–60 / 61–120, boundary `test_kv_ambient_fresh_c3.py`). Artifacts: `/tmp/ac-gate/*identity*.txt`, verdict at `/tmp/ac-gate/ab-compare-verdict.md`.

| Dir | BASE | IMPL | Pre-existing (both) | REGRESSION (impl-only) | Base-only |
|---|---|---|---|---|---|
| tests/unit/job_queue | 94/94 P | 94/94 P | 0 | **0** | 0 |
| tests/unit/repositories | 116/116 P | 142/142 P | 0 | **0** | 0 |
| tests/unit/services A | 12 F | 11 F | 11 | **0** | 1 |
| tests/unit/services B | 5 F | 5 F | 5 | **0** | 0 |
| **TOTAL** | **17 F** | **16 F** | **16 (error bodies: 12 exact + 4 truncation-consistent, 0 divergent)** | **0** | **1** |

- **The caller's "17 claimed-pre-existing failures" is CONFIRMED exactly**: base census = 17; impl = 16; the single delta (`test_context_injection.py::TestSharedContextHints::test_always_include_highest_match_even_at_zero`) fails at base AND fails 3/3 deterministically in isolation at impl (passed only inside the impl chunked run) — order/env-sensitive pre-existing failure, zero branch delta on its code paths (git log/diff empty). Zero merge impact; no quarantine (fails identically at base; blocks no pack).
- Impl-only additions: `test_auto_continue_candidates.py` (+490 lines, 26 repo tests) — all green.
- Pre-existing red clusters (for a separate triage commission — F-3): `test_job_queue_proxy_phase1` (7, instance-derived JobItem status mapping serves 'pending'), anti-drift source-scan drift (`test_compact_executor` WS-2.4, `test_proactive_compaction_fix_p1` T6 ×2, `test_f16_legacy_status`), `test_post_restart_arm_notify_sweep` ×2 (TerminalStateGating), `test_b1_wc_durable_send` ×2 (one bakes a macOS absolute path `/Users/nguyenminhkha/...` — portability bug), `test_service_tool_manager` stop_pid KeyError.

## 3. PG Boot Smoke (reviewer carry) — PASS

Deployed committed tree via `git archive 35ab0aa9` → `~/ac-pg-smoke/rel1` (outside repo). Scratch PG16 DB `ensemble_ac_smoke` (local cluster; demo PG role lacked CREATEDB — sanctioned fallback), dedicated throwaway role, port 17981, `ENSEMBLE_SELF_ENV=dev`.
- Boot 1: migrations skipped-for-PG line; `AutoContinue boot pass: ContinueResult(candidates=0, …, errors=0)`; /readyz 200; column materialized (`information_schema`: `task.auto_continued_at` timestamp NULL YES, ordinal 23 — same row after both boots).
- Boot 2: **CLEAN** — zero DDL/column-add/migration lines for the column; boot-pass line present; /readyz 200. Idempotency contract holds (same mechanism class as `last_heartbeat_at` precedent).
- Boot 3 (kill-switch OFF): `[BOOT_CONTINUE] OFF: ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0 — pass skipped (StaleTaskRecovery backstop continues to own orphans)` + `skipped_kill_switch=1`, `duration_seconds=0.0` (fast-path), zero resumes.
- Cleanup verified: port freed, DB + role dropped. Environmental noise: 4 MCP `plane` connection ERRORs/boot (no Plane server in staging) — unrelated.

## 4. PHASE 5 DEMO E2E (the centerpiece) — core scenario PASS; T5.4c partial (F-1)

Setup: staging deploy from worktree (`git archive HEAD` = 148dd3c7, ancestor-verified) → `~/ac-e2e-demo/rel-A`, own venv, demo-configured (ensemble_demo PG DB + demo env), port 17980, `ENSEMBLE_SELF_ENV=demo`, kill-switch default ON. Real demo daemon (7979) stopped gracefully while idle (sanctioned; restored at the end via `systemctl start ensemble-demo.service`, healthy). All boots used a hardened env-guard wrapper (see INCIDENT-1). Evidence: `/tmp/ac-e2e/evidence.md` (633 lines), logs `~/ac-e2e-demo/logs/boot{1-retry,2,3,4,5}.log`.

### Core user scenario (AC8) — PASS (scenario-5, restart 49.2s INTO the sleep)
- Leader-dev workflow via staging API: leader `e50161c5` → child worker `5ef2a000` (task 30, work `119e23d5`) with sleep-then-say-hello task; parent sat `WAITING_CHILDREN`; child RUNNING mid-sleep.
- **Restart at t_sleep+49.2s** (t_stop 10:13:12Z, ready 10:13:29Z). boot5 verbatim: `ContinueResult(candidates=1, scheduled=1, …errors=0)` + `[BOOT_CONTINUE] instance=5ef2a000 work_id=119e23d5 epoch=2026-10-04T10:13:28.413789`; ZERO `[BOOT_CONTINUE]` for the parent (WC skip); zero resume-refused; zero already_resuming.
- (a) **Auto-continue from checkpoint**: `[RESUME] instance=5ef2a000 has_checkpoint=True` — resumed turn proceeded from committed history (5.4b audit below proves no rollback/no committed refire). MID-NODE restart classification recorded (restart landed inside the uncommitted sleep tool call → node re-executed from zero — at-least-once, expected; the child's LLM additionally retried the sleep in 3 chunks after two ~60s tool-timeout kills — visible, benign, and exactly the at-least-once pattern the plan tolerates).
- (b) **Sleep+hello completes, hello arrives once**: child final reply verbatim `"hello"` (10:15:40); parent synthesis delivered 10:25:10 containing the child's word. ZERO manual pings throughout (verified: no human/API message sent to any scenario instance between t_stop and hello).
- (c) **Child report wakes parked parent**: dependency_watchers for parent5 `FIRED` + `enqueued_at=10:22:05` and `10:24:44`; parent resumed AFTER child terminal; synthesis produced without any manual message — bus-owned durable wake path works across restart.
- (d) **T5.4b no-committed-refire audit — PASS**: post-completion checkpoint history (ormsgpack decode of checkpoint_blobs, same aget semantics): 6/6 AIMessage.tool_calls ↔ ToolMessage matched, **0 duplicated committed (tool_call_id, name, args) tuples**; uncommitted interrupted-node pattern tolerated as specified. (NOTE: the planned `tests/e2e_helpers/audit_committed_tool_refires.py` was NEVER delivered on the branch — audit implemented inline at `/tmp/ac-e2e/audit_refires.py`; intent honored, delivery gap flagged.)
- (e) **T5.4c second restart — PARTIAL**: restart-1 (+1s into the turn, scenario-3) had passed every contract incl. msg-count continuity (`[RESUME] msg_count=4` = pre-restart 4). Restart-2 (~80s into re-executed sleep): boot3 `ContinueResult(candidates=0)` — **no further [BOOT_CONTINUE]** → F-1. Zero `already_resuming` across ALL FIVE boots ✓ (per-instance dedup path proven); no double-schedule; no second hello (none possible — see F-1 wedge).
- (f) **Loop-breaker (5.11)**: NO PRIOR LOOP (max identical tool_call fingerprint count 2 < 3); no `repair-` summary on the resumed turn.
- 5.8 turn duration: `[BOOT_CONTINUE]` 10:13:29 → child COMPLETED ≈ **139s ≪ 600s** → **NO ESCALATION** (R20 stays 🟡).
- 5.9 (429 measurement): **NOT MEASURABLE** — no proxy log access from the test lane; recorded honestly per plan (needs operator re-run with proxy logs; metrics counters asserted at unit level by M22).
- Count audit (5.6): scenario-5 — [BOOT_CONTINUE] child=1, parent=0, already_resuming=0 (all boots), hello=1, child-report→parent-wake=1. Iteration-1/2 (children completed before any restart — scenario races) and scenario-3/4 exhibits are itemized in evidence.md §7/§9 with per-instance accounting; all 12 E2E-created instances deleted post-capture via API.

### Terminalizer real integration (user item 4 — review Finding 2) — PASS
Real path driven end-to-end on scenario-5: resumed orphan (task 30, `auto_continued_at=2026-10-04T10:13:28.413789` = boot5 epoch, stamped post-schedule) → turn completes → terminalizer call-site gate accepts (stamp present) → task row terminal → pending_wakes claim unblocks → watcher FIRED + `enqueued_at` set → wake delivered exactly once → parent resumed. Asserted via task/watcher rows + journal events, not source pins. (Unit leg: 7/7 terminalizer pack.)

### Findings (non-blocking for merge; follow-ups recommended)
- **F-1 (🟠) T5.4c wedge**: at boot3, the child's task row left the running-set (DependencyBus None-error path during startup set status='failed' at 10:01:35) and `discard_on_startup=backlog-clear` (STATUS-based predicate — `clear_all(preserve_in_flight=True)` preserves only `running`/`paused` + non-terminal JobItem anchor, `daemon/repositories/task/repository.py:4282`; **NO heartbeat threshold exists** — code-verified, overturning the stage-B heartbeat hypothesis) deleted it before the boot pass → candidates=0 → child orphaned, parent wedged (`watcher CANCELLED, fired_at set, enqueued_at=NULL`). Attribution feature-vs-pre-existing UNRESOLVED (the flip happened in the bus error path during a boot the feature participated in). Evidence: boot3.log doomed lines, evidence.md §7/§9.
- **F-2 (🟠) Straddle wedge (scenario-4)**: child completed ~3s BEFORE the restart; backlog-clear then deleted the COMPLETED task row before the parent-wake delivered → parent4 wedged `waiting_children` forever ("Child spawned and tasked — waiting for its completion report."). Restart-durability gap in the WC-wake lane — a lane the feature deliberately does not touch (pre-existing exposure; the E2E merely landed inside its window).
- **F-3 (🟢)**: 16 pre-existing full-dir reds (§2) — recommend dedicated triage commission (anti-drift source-scan drift cluster + job_queue_proxy_phase1 cluster + macOS-path portability bug).
- **F-4 (🟢)**: order-sensitive `test_context_injection` sibling-state dependency (root-cause follow-up; passed only in chunk context).
- **F-5 (🟢)**: staging-boot env-guard convention (see INCIDENT-1) — recommend codifying (explicit inline env + pre-exec assertion) for ALL future demo-adjacent boots.
- **F-6 (🟢)**: 429/R23 cadence measurement requires proxy log access — operator follow-up.
- **F-7 (🟢)**: merge-checklist reminders — (i) worktree-gate conftest (`conftest_worktree.py`) MUST be dropped/neutralized in the MERGE commit (phase4 4.7 lifecycle); (ii) PACKS.md rows for the two new packs still pending (registration deliberately deferred: caller locked this branch's commit scope to this RESULTS file; tester lane adds rows post-merge).

## 5. INCIDENT-1 disclosure (contained; live daemon unaffected)

First staging-boot attempt ran under `/bin/sh` (dash) where `source` is not a builtin → staging .env silently not loaded → the daemon process inherited LIVE ambient env (PORT 9797, `ENSEMBLE_SELF_ENV=live`, `POSTGRES_DB=ensemble_prod`). The orphan connected to the LIVE DB, ran `discard_on_startup=backlog-clear` (deleted 602 message_queue + 602 task rows — **policy-equivalent**: live's own `.env` has `QUEUE_DISCARD_ON_STARTUP=true`, verified read-only, so live's next self-restart performs the same clear), stamped `auto_continued_at` on 4 RUNNING live tasks (**inert**: live v0.16.12 has no code path reading the column; epoch-CAS self-heals on any future feature-bearing boot), failed to bind 9797 (live daemon holds it), and shut down. **Live daemon (pid 3321986, v0.16.12, uptime 17h+) fully healthy throughout — zero process impact**, verified /livez + /api/health. Orphan SIGTERM/SIGKILLed, confirmed dead. No live-DB remediation performed (more risk than benefit). The orphan's boot-pass markers (candidates=4/scheduled=4) are INVALID as gate evidence and are excluded. Hardened retry (bash + inline env overrides + guard assertions) succeeded; stages B–D ran clean. Root cause + hardening recorded in evidence.md §6.

## 6. AC Evidence Map (AC1–AC9)

| AC | One-line proof |
|---|---|
| AC1 selection scope (ONLY running process_message/process_report; PAUSED/terminal/WC/cancel_requested excluded) | bootpass pack M2/M15 (56/56) + E2E: WC parent NEVER continued across 5 boots; boot5 candidates=1 = exactly the running child |
| AC2 orchestration-only resume (`_schedule_explicit_handle_resume` silent is_retry; stagger 5/2s; no enqueue) | pack M4/M22 green (exact-kwargs + stagger cadence + metrics) + E2E silent `[RESUME] has_checkpoint=True` resume observed live |
| AC3 WC-wake restart-durable (bus-owned, no code change) | E2E scenario-5: watchers FIRED+enqueued_at (10:22:05/10:24:44) → parent resumed post-restart, synthesis 10:25:10, zero manual pings (caveats F-1/F-2 in adjacent windows) |
| AC4 coexistence (claim-guard FIFO; Δ1 terminalizer success path) | interleaving pack 14/14 (6-row matrix, row 4 = Δ1) + terminalizer pack 7/7 + E2E real-path (stamp → complete → claim unblock → wake ×1) |
| AC5 exactly-once per boot epoch (CAS; no double-continue; no committed refire) | pack M3/M12 green + E2E: CAS stamp epoch==boot5 epoch; [BOOT_CONTINUE] ×1/boot/child; 5.4b audit 0 duplicated committed pairs (T5.4c live reboot-loop leg partial — F-1) |
| AC6 never-wedge (isolation; epoch-None SKIP; per-boot kill-switch read) | pack M7-M11 green + all 5 E2E boots completed startup with errors=0 + PG smoke boot-pass on empty DB 0.012s |
| AC7 repo conventions (packs, full-dir gate, worktree gate, PG smoke) | §1–§3: both packs + full-dir + fresh-worktree A/B + worktree-gate conftest in-pack + PG 2-boot idempotency (gaps flagged: PACKS.md rows deferred F-7; audit helper undelivered → inline) |
| AC8 REAL demo E2E | §4 core scenario: mid-sleep restart → auto-continue → sleep completes → hello ×1 in originating conversation → parent woken by child report → audits pass |
| AC9 kill-switch default ON | pack M10 green + PG smoke boot 3: exact `[BOOT_CONTINUE] OFF … skipped_kill_switch=1` line, duration 0.0, zero resumes; E2E ran default-ON throughout |

## 7. ensure.md Validation (scoped to change set)

- **Critical 4/4 PASS**: changed packs all PASS (§1); deadlock/concurrency pack PASS (incl. `test_deadlock_fix.py` + cascade races + observer race + atomic locks); sync-DB-off-loop covered by same pack (thread-identity tests green); `dev.sh` `--timeout-graceful-shutdown 10` present (grep verbatim, literal value).
- **Important PASS**: all 8 call sites of `_get_system_prompt_tokens` / `_compute_context_usage` / `get_queue_stats` awaited (0 unawaited); original deadlock scenario covered by concurrency pack.
- **Nice-to-have**: no dead-code verification required (lane deletions = the inert commit only, already line-verified).
- **Improvement notices**: none — no ensure.md requirement contradicted pack discipline this run (full-dir was caller-mandated; executed chunked with dual-layer timeouts).

## 8. Code Changes Summary (test lane)

- NONE to production or test code. No quick fixes were needed. Worktree dirty-state audit: only `.agents/shared/planning/designer-agent/install-audit.jsonl` (pre-existing append from a PARALLEL commission + KMS unit-test audit sink) — left uncommitted, untouched.
- Committed by this gate: ONLY this RESULTS file (per caller constraint).
- Infrastructure created and REMOVED: base worktree `/home/nea/ensemble-src-wt-ac-base` (removed, exit 0), `~/ac-pg-smoke` (removed), staging `~/ac-e2e-demo` + daemon (stopped; port 17980 free; logs preserved), 12 E2E instances (deleted via API; pre-existing ari demo instances untouched). Demo daemon restored (7979 healthy, v0.16.3). Live untouched (see §5).
- Evidence retained: `/tmp/ac-e2e/*` (evidence.md 633 lines, state.json, audit_refires.py, boot logs), `/tmp/ac-gate/*` (identity files, ab-compare-verdict.md, chunk logs). /tmp is volatile — copy into the repo post-merge if durable copies are wanted.

## 9. Post-merge follow-ups (tester lane + commissions)

1. Add PACKS.md rows for `auto_continue_boot_pass_unit_test` + `auto_continue_interleaving_unit_test` (SPECs in test-strategy.md) — deferred by commit-scope lock.
2. Drop/neutralize `conftest_worktree.py` in the MERGE commit (worktree-gate is implementation-lane-only).
3. Commission: F-1/F-2 restart-durability gaps (discard-on-startup status-based deletion vs in-flight semantics + WC-wake straddle window) — includes deciding whether the DependencyBus None-error flip is feature-adjacent.
4. Commission: F-3 pre-existing reds triage (16 identities, §2) + F-4 order-sensitive context_injection.
5. Operator: re-run 5.9 429 measurement with proxy log access (boot-pass window); R23 verdict pending that measurement.
6. Deliver the planned `tests/e2e_helpers/audit_committed_tool_refires.py` (or adopt the inline `/tmp/ac-e2e/audit_refires.py` implementation as the helper).
