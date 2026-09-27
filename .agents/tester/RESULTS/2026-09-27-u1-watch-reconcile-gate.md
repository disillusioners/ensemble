# TESTER GATE — U1-Slice Fix: fix/u1-watch-reconcile @ 2b969422 (watch-reconcile sweep + notify hooks + zombie-GC)

Date: 2026-09-27 · Branch: `fix/u1-watch-reconcile` @ `2b969422` (commits 81cdba55 → 6254ab1b → 2b969422; local-only) · Base: `6011721d`
Commission: U1-slice (closes live-confirmed starvation, incident c7f59aaf); acceptance suite = P1–P6 from RESULTS/2026-09-27-test-gap-analysis-scenario-b-u1.md
Workers: base-leg `82905e64`, feature-leg `556f714c`, boot/harness `32afa3a6`, flake/parity `201679f3`, e2e P1P2/P3P4/P5P6 (see §4)

## VERDICT: (pending — all sections final except §4/§5)

---

## 1. A/B vs base — FULL tests/job_queue/ (identical invocation both legs, POSTGRES-scrubbed, `timeout 1500` documented full-dir deviation)

- **Base leg** (worktree /tmp/u1gate-base-6011721d, PYTHONPATH-pinned, import-root verified): 1941 collected → **20F / 1880P / 0E / 38S / 3desel**, 274.86s. 11 red families.
- **Feature leg** (main checkout @ 2b969422, HEAD/branch proven, import-root verified): 1958 collected → **15F / 1902P / 0E / 38S / 3desel**, 310.42s. (+17 = new pin file.)

### Adjudication table

| Bucket | Count | Disposition |
|---|---|---|
| **Feature-only (NEW) failures** | **0** | **No blockers.** |
| **Both-red (pre-existing)** | 15 | Site1InlineMirrorFinalize ×3 (repository.py:2602 MagicMock-JSON, RED-since-v0.13.10) · fake_sync 5-vs-6 arity ×3 (in_progress_guard ×2 + n8_hot_path_pin ×1; call-site line shifted :1732→:1796 = branch code added above, same family) · watch-events `'settled'` token ×3 (vocabulary predates 39607e12; fixtures pin old canonical) · terminal-write census ×2 (unlisted manager.py:5622 + 15 stale anchors, fixture rot) · tool-surface drift ×2 (24≠22; job_resume-vs-job_answer) · macOS hardcoded path ×1 · deprecated get_event_loop ×1 |
| **Base-only reds** | 5 | (a) **2 re-anchored terminated-silence tests — BY DESIGN**: RED at base under old names (`TestObserverSkipsTerminated::test_observer_skips_terminated_status` @ test_job_feedback_observer.py:332; `test_observer_completion_then_termination_skips_termination` @ test_phase2_feedback_verify.py:497 — both asserted get_job_by_instance NOT called) → GREEN at feature under re-anchored contract (`TestObserverTerminalArmFiresWatchers::test_observer_terminated_status_routes_through_helper` + `…terminated_arm_routes_through_helper` — now assert routing THROUGH the notify helper). Exactly the claimed re-anchor. (b) 3× TestRecoveryServiceBothStateParity — worktree `.venv` artifact, parity disposal in §3. |

**Feature scope 100% green:** 17/17 commission pin pack (TestP1StarvationIncidentVerbatim 4/4 · P2ToolEntry 1/1 · P3RevivalFlip 1/1 · P4TerminatedFailedArms 2/2 · P5CrossBootStale 1/1 · P6Content 1/1 · U1Slice310msRace 1/1 · ZombieRowGC 3/3 · ConfigKnobAndServiceShape 3/3) + 2/2 re-anchored.

## 2. dev.sh boot gate — PASS

- HEAD 2b969422 on fix/u1-watch-reconcile; POSTGRES_*/SSL_CERT_* scrubbed (env -u, zero survivors); port 8079 pre-checked free; engine marker `Creating PostgreSQL engine: localhost:5432/ensemble_dev` ✓.
- **New service markers:** `WatchReconcileSweepService started: interval=300s (default 300s)` (watch_reconcile_sweep.py:180, wired at api.py:951-965) — immediate first tick verified inside the 1.23s-uptime window of the first health response (`_run()` calls `sweep_once()` before any sleep, :333-335); clean shutdown logged `WatchReconcileSweepService stopped` (:205, stop wired at api.py:2026+). Config knob `watch_reconcile_sweep_interval_seconds` default 300 floor 1 fail-fast (config.py:1730). First tick silent on fresh dev DB (0 held rows; sweep logs on exceptions only) — correct.
- Static: dev.sh:102 `--timeout-graceful-shutdown 10` ✓ (ensure.md Core).
- Harness: /tmp marker-mock files SURVIVED (2026-09-26 session's copies); tool round-trip re-validated at this HEAD (`TOOLCALL bash → U1-HARNESS-OK` in transcript + MOCK-ACK dedup path); `.env` restored to baseline sha256 `11db2814…9490` MATCH.
- **Operational finding (env-rot, non-blocking):** repo `.env` carries stale `POSTGRES_PASSWORD=testpw` while local dev PG's password is `ensemble` → first boot attempt died at PG-auth (engine marker still proved dev targeting). Worker mitigated within the flip/restore carve-out (byte-exact backup, patched for runtime, restored; final sha MATCH). Recommend a dev-env refresh follow-up (canonical per docker-compose.test.yml says `ensemble_dev`; local uses `ensemble`).

## 3. Flake matrix + base-parity disposal + collection — ALL CLEAN

**Flake matrix (5× serial each, feature HEAD, timeout 300 per run):** pin pack `test_u1_slice_commission_pins.py` 17P ×5 (14.3–15.1s each) · re-anchored observer test ×5 (0.28–0.41s) · re-anchored phase2 test ×5 (0.29–0.41s) → **15/15 green, zero flakes.** (No resilience-pin failure occurred, so the inner-catch/outer-handler layer adjudication was not exercised live.)

**Base-parity disposal:** killswitch ×3 at base with `.venv` symlink → **29 passed in 32.22s** incl. all 3 `TestRecoveryServiceBothStateParity` → base-only reds fully accounted (2 by-design re-anchor + 3 env artifacts). Symlink removed; incidental subprocess `.pytest_cache` in the worktree caught + removed (pristine restored).

**Collection @ feature HEAD** (paired `--ignore` for the two known sys.exit pack files): **23,562/24,323 collected (761 deselected), 2 errors — both exactly the known pre-existing set** (playwright missing; live-HTTP probe with no daemon). **Zero NEW.**

## 4. Real E2E on dev — P1–P6 + zombie-GC (marker-mock harness, real notify lane)

**P1 STARVATION — PASS (the incident, closed).** Incident topology replayed: W(mission)/C(child) via spawn_instance; ari armed on W's live turn-2 receipt; J1 settled early with C non-terminal → row HELD (DB-proven); P2 tool calls rode the hold window; mission fully terminal (C terminated) at T0 with the daemon UP (uptime 1505s continuity at delivery). **Delivery at T0+287s — bounded, inside the 300s contract — via `WatchReconcileSweepService` tick 6** (log: `reconcile_held_watches_for_instance: scanned=1 fired=1` + `tick 6 scanned=1 fired=1`). Body verbatim:
```
[JOB_EVENT] Job cb7a1c58... settled ✓
Agent: tester
Result:
MOCK-ACK
```
Exactly-once preserved across a post-delivery restart (recount still 1; row consumed; no sweep duplicate). Base (6011721d) behavior for this shape was UNBOUNDED silence — the fix converts it to ≤300s. **Hook path not the carrier in this run** (W auto-completed via observe→finalize, bypassing the lifecycle-event hook — anomaly logged; hooks remain pin-covered; P3/P4 exercise them live). 310ms-race: **pin-covered, not constructed live** (declared honestly).

**P2 TOOL-ENTRY — PASS.** During the hold window, as ari: `job_get`, `job_progress`, `job_messages`, `watch_mission` re-call — row survived all four (still HELD), zero [JOB_EVENT] attributable to any tool call; the re-call correctly short-circuited ("Armed 0 live, 2 already-settled skipped — no historical replay") WITHOUT deleting the held row. Tools are pure reads; delivery comes from hooks/sweep only — contract pinned live.

**P3 REVIVAL FLIP — PASS via hook (b), in-session (~1s).** True 36be8aef topology exercised end-to-end: C's child-report revived W → report-turn settle → `_finalize_job` post-commit outbox → `reconcile_held_watches_for_instance` → CAS-claim + deliver. Body verbatim: `[JOB_EVENT] Job 36752b4f... settled ✓ / Agent: tester / Result: … no-marker ack`. Exactly-once held (1 event, row consumed). The common-case fast path works LIVE.

**P4 TERMINATED/FAILED arms — (a) PASS via sweep 27s + 🟠 hook-(a) carrier-gap finding; (b) pin-covered-not-live (honest).**
(a) `DELETE /api/instances/{W2}` (cascade-terminate) → row delivered 27s later by sweep tick 2, body with content, exactly-once — stated bar met. **BUT the shipped fast path is a live no-op:** hook (a) (`_fire_watcher_notify_for_terminal` → `reconcile_held_watches_for_instance(instance_id=TERMINATED_instance)`) keys the helper on the TERMINATED mission-root's id, while the held row's `instance_id` is the WATCHER's id → filter matches nothing → no fire (no hook-(a) log line; sweep carried). The unit pin `test_p4_terminated_arm_fires_via_helper_hook` masks this by calling the helper directly with the watcher id. Not starvation-regressing (contract min(hook,300s) held via sweep; hook (b) covers the common finalize path) — routed as must-fix follow-up: correct the helper key (watcher-scoped or global) + re-anchor the pin to the live seam.
(b) FAILED arm unreachable from the mock harness (dead-letter path only; bogus-tool/no-op, malformed-body, nonzero-exit all tried honestly) — pin 2/2 green at unit level; declared pin-covered-not-live.

**P5 CROSS-BOOT STALE — PASS.** Held row survived **two full daemon restarts** (liveness held by a PAUSED child — pause/resume is the cross-boot holder): each boot's immediate first tick **scanned the held row and correctly did NOT fire (mission live) and did NOT retire (guard correct)** — `reconcile_boot_sweep alive=0 reaped=0 errors=0` both times, row still present. Resume → mission terminal → **sweep tick 2 delivered** (hook (b) not the carrier: W3's terminal went via the `no parent,no children` branch, bypassing `_finalize_job` — same carrier-coverage family as the hook-(a) finding). Exactly-once across the entire timeline (2 restarts + resume + delivery): 1 event total, row consumed. Body verbatim (status-only — see P6 note): `[JOB_EVENT] Job ac344ef4... settled ✓ / Agent: tester`.

**P6 CONTENT — PASS.** Fix-path body-content table: P1 sweep ✅ content · P3 hook-(b) ✅ content · P4(a) sweep ✅ content · fresh task-kind watch ✅ content (`[JOB_EVENT] Job 34f5e9da... completed ✓ / Agent: tester / Result: …`). One exception: P5's body was status-only — root-caused to **source-data loss, not delivery regression**: J1's receipt settled during the mock-LLM crash window → `result_summary=None` → the sweep contract correctly omits `Result:` when the source has none (adjacent-U6 note: sweep delivery has no enrichment fallback; register as follow-up consideration). No status-only regression on any path where content existed.

**Zombie-GC — bonus-not-constructed (honest).** 4 setup attempts blocked by a script-internal JSON-extraction failure in the bash harness (heredoc-escaping with nested TOOLCALL args); without a minted row there was nothing to orphan. Unit pins `TestZombieRowGC` 3/3 green. Live-zombie retirement remains unit-covered only — noted for the follow-up E2E when the harness gains a driver script.

**Harness/ops anomalies (register):** marker-mock crashed once under a daemon LLM-retry storm (60s backoff burst) — no persistence/recovery in the harness; multi-tool-call markers replay on post-tool re-invoke (single-tool-per-marker is the stable protocol); ghost-child race on freshly spawned idle children (pre-existing, guard handles it).

## 5. UNKNOWN UNKNOWNS / findings register

| Rank | Sev | Finding | Evidence / repro |
|---|---|---|---|
| U1-F1 | 🟠 must-fix follow-up | **Hook (a) is a live no-op for the mission-terminated case**: `_fire_watcher_notify_for_terminal` calls `reconcile_held_watches_for_instance(instance_id=TERMINATED_instance)` — keyed on the mission root, while the held row's `instance_id` is the WATCHER → filter matches nothing → no fire (no hook log line; sweep carried at 27s). **The unit pin masks it** by calling the helper directly with the watcher id (`test_p4_terminated_arm_fires_via_helper_hook`). Same family: P5's terminal went via the `no parent,no children` branch — bypasses `_finalize_job` → hook (b) also not the carrier; sweep backstop everywhere. Fix direction: watcher-scoped (or global) reconcile key + re-anchor the pin to the live seam. NOT starvation-regressing (contract min(hook,300s) held on every live path). | P4(a) timeline + log absence; P5 carrier; pin source |
| U1-F2 | 🟡 | **Sweep-delivered bodies have no enrichment fallback**: when the receipt's `result_summary=None` (e.g., receipt finalized during an LLM outage window), the sweep body is status-only (P5). Contract-consistent (omit Result when source has none) but the `_enrich_terminal_record` fallback exists only on the tool-reply path — consider sweep-time enrichment. | P5 body + J1 row (`result_summary=None, completed_at=None, admission_state=done`) |
| U1-F3 | 🟢 | Stale `.env` `POSTGRES_PASSWORD=testpw` vs local dev PG (`ensemble`) — first boot dies at PG-auth; boot gate worker + E2E workers each had to patch within the flip/restore carve-out. Dev-env refresh follow-up. | §2; /tmp backups |
| U1-F4 | 🟢 | Tester artifact loss (pre-gate): my 2026-09-25 E2E RESULTS + 2026-09-26 sub-reports + gap-analysis file were removed by a later session; gap analysis re-materialized. Recommend identifying the remover — tester artifacts are gate provenance. | §5 note; RESULTS listing |
| U1-F5 | 🟢 | Harness: marker-mock crashes under LLM-retry storms (no recovery); multi-tool markers replay on re-invoke; ghost-child race (pre-existing, guard-correct). | E2E anomaly logs |

## 6. Combined verdict

**PASS — CLEARED FOR MERGE → v0.16.1 cut → stage (STOP at staged-READY per commission).**

- A/B full-dir: **0 feature-only failures** (15 both-red pre-existing, adjudicated + consolidated into QUARANTINE.md; 5 base-only = 2 by-design re-anchors + 3 disposed env artifacts).
- Feature scope: 17/17 commission pins + 2/2 re-anchored tests green; flake matrix 15/15; boot gate PASS (service markers, immediate first tick, clean shutdown); collection zero NEW.
- E2E acceptance (real daemon, real notify lane): **P1 PASS** (incident c7f59aaf replayed and closed — bounded 287s delivery with the daemon UP, vs unbounded silence at base) · **P2 PASS** (tools pure reads) · **P3 PASS** (hook (b) in-session ~1s, true incident topology) · **P4(a) PASS + 🟠 hook-(a) live-no-op finding (pin-masks-seam)** · **P4(b) pin-covered-not-live** · **P5 PASS** (cross-boot exactly-once, first-tick correctness ×2) · **P6 PASS** (content on all fix paths; 1 source-data anomaly explained, not a regression) · **zombie-GC unit-covered, bonus-not-constructed (honest)**.
- The delivery-latency contract min(in-session hook, 300s sweep) held on EVERY live path; exactly-once (CAS) held across restarts, resumes, and multi-carrier surfaces.
- **Lead follow-up (not merge-blocking): U1-F1** — hook (a) key mismatch + pin re-anchor; recommend folding into this slice if cheap, else immediate next commission. U1-F2 sweep-enrichment consideration rides with it.

No fixes attempted; no repo mutations; `.env` byte-restored (sha `11db2814…9490`); all ports freed; 8088/live/demo untouched across 11 daemon boots.

**Testing status: COMPLETE — READY.**
