# Commission FINAL GATE — WATCHER-LOSS TERMINAL-STRAND (RCA b6e24d78) @ 27d2452a7→d195316a6 (+gap tests bfb242d9) — 2026-10-14 — 🟢 VERDICT: PASS

Worktree `/home/ensemble-worktrees/watcher-loss-terminal-strand` (branch `feature/watcher-loss-terminal-strand`; base `27d2452a7`; 9 developer commits: 4 production fixes — mission_live_guard a05ef5903, child_reports 654d6cc9d, NEW mission_strand_healer 0ec93ea9f, watchdog re-arm 02ec363f3 — plus api/config wiring and 4 test commits; 1 tester gap-commit `bfb242d9` test-only). Independent verification commission: developer numbers were NOT re-read — every count below was re-derived from zero. **The developer filed no RESULTS/PACKS.md entry for this commission; this gate is the baseline record.**

13 workers, 0 re-dispatches. Env fence on EVERY run: `POSTGRES_*`/`ENSEMBLE_*`/`DATABASE_*`/`SSL_CERT_*` scrubbed, fake `HOME=$(mktemp -d)`, worktree `.venv` verified (`daemon.__file__` resolves inside the worktree), dual-layer timeout (outer `timeout 300` + inner `PYTEST_TIMEOUT=280`/`--override-ini timeout=280`), zero daemon boots, zero port touches, zero production-file mutation by the tester.

## Scope decision
Commission scenarios 1–9 (unit/mock level, TrueAuto mock-fidelity rule) + full services neighborhood + full job_queue dir + concurrency core. Release-Gate E2E (daemon `./dev.sh` + real-LLM items, ensure.md Release section) NOT run — deferred to promote ceremony; all commission scenarios validated at unit/mock level with a code-cited fidelity audit (below). Developer's own "final gate 4578 passed" full-suite claim was superseded by this independent net (5,184 collected across 9 packs with deliberate overlap; see table).

## Independent pack runs (all at HEAD d195316a6 unless noted)

| Pack | Target | Collected | Passed | Failed | Skipped | Verdict | Runtime |
|---|---|---|---|---|---|---|---|
| `wlts-new-suite` | tests/job_queue/test_watcher_loss_terminal_strand.py (NEW suite) | 26 | 26 | 0 | 0 | ✅ PASS | 6.7s |
| `wlts-wedge` | test/packs/wedge_fix_suites_unit_test.sh (registered) | 78 | 78 | 0 | 0 | ✅ PASS | 3.5s |
| `wlts-resolver` | tests/unit/services/test_mission_resolver.py | 63 | 63 | 0 | 0 | ✅ PASS | 3.7s |
| `wlts-childrep` | tests/unit/services/test_child_reports.py | 48 | 48 | 0 | 0 | ✅ PASS | 6.4s |
| `wlts-watchdog` | tests/unit/services/test_waiting_children_watchdog.py | 51 | 51 | 0 | 0 | ✅ PASS | 3.0s |
| `wlts-concurrency` | test/packs/concurrency_atomic_unit_test.sh (ensure.md Core #2/#3) | 173 | 99 | 0 | 74 | ✅ PASS — EXACT baseline 99P/0F/74S, zero drift | 89s |
| `wlts-services` | tests/unit/services dir-wide (`-n auto`) | 2649 | 2640 | 9 | 0 | ✅ PASS-with-preexisting (all 9F = documented families; 0 new) | 61s |
| `wlts-jobqueue` | tests/job_queue dir-wide (`-n auto`) | 2086 | 2033 | 15 | 38 | ✅ PASS-with-preexisting (all 15F = documented families; 0 new) | 76s |
| `wlts-gap` | tests/job_queue/test_watcher_loss_terminal_strand_gap.py (TESTER-AUTHORED, @ bfb242d9) | 10 | 10 | 0 | 0 | ✅ PASS | 3.6s |

Totals (with deliberate focused/dirwide overlap): **5,048 passed / 24 failed (all pre-existing) / 112 skipped / 0 TIMEOUT / 0 branch-caused failures.**

Claim reconciliation: new-suite 26/26 ✅ · wedge 78 ✅ · mission_resolver "63" ✅ (48 `def test_` + 15 parametrize expansions — no drift) · child_reports 48 ✅ · watchdog 51 ✅ (incl. both `TestWedgeRearmAfterCooldown` direct-hits) · job_queue A/B "2031" → actual 2033P; +2 delta fully explained by branch test-authoring timeline (u7 W6b pin + suite growth to 26); the A/B's substantive claim — base-vs-branch identical failure set — HOLDS.

Pre-existing failure sets (node-for-node vs documentation):
- services 9F = job_queue_proxy_phase1 instance-derived-status ×7 + scheduling once-anchor time-bomb ×1 + service_tool pid_dead KeyError ×1 (== 2026-10-12 consolidated QUARANTINE row, exactly).
- job_queue 15F = event_driven_completion MagicMock-JSON ×3, in_progress_guard observer-guard ×2, jober_watch_integration ×3, n8_hot_path_pin ×1, job_answer tool-order ×1, round2_council event-loop ×1, terminal_write_census ×2, watcher_repository_concurrent vocab ×2 (== 2026-09-22 + 2026-10-04/05 RESULTS A/B families).
- A/B base re-run NOT triggered (no new failure to attribute — conditional node closed).

## Commission scenario adjudication (1–9)

| # | Scenario | Verdict | Evidence (test, file:line) |
|---|---|---|---|
| 1 | MINT-RACE SHAPE (epoch read-model pin) | ✅ PASS | `test_epoch_is_constant_one_and_started_tracks_activity` (new-suite :1317 — epoch==1, started_at tracks last_activity_at, no ended_at pre-terminal) + schema pins :1349 (instances has NO terminal_reason column) / :1358 (job_queue_items has NO result_summary column) — no storage behind the read-model. |
| 2 | LEASE-WRAPPED COMPLETION | ✅ PASS | `test_lease_metadata_is_inert_to_completion` (:1377 — lease-shaped metadata vs {}: both legs COMPLETED + watcher CAS-claimed). Fidelity audit F: completion path NEVER reads the KV lease row or lease metadata keys → proxy inert-by-construction (acceptable; re-audit if a lease read is ever added). |
| 3 | SPAWN-BLOCKED FINAL TURN (zero residue) | ✅ PASS | `test_denied_spawn_leaves_zero_residue` (:1437 — COUNT-unchanged across instances, instance_hierarchy, dependency_watchers, message_queue, task, job_queue_items; parent status + last_activity_at byte-unchanged; `spawn_instance` never called; REAL ACL team-membership gate, denial precedes the manager seam) + TESTER `test_denied_spawn_then_completion_publishes_and_fires` (gap G1 — the missing conjunct: denial on the final turn still → root COMPLETED + lifecycle event + watcher CAS-claimed). Denial exercised leader→leader (same team-membership gate family as leader→ari). |
| 4 | CONTRAST/NORMAL PATH | ✅ PASS | `test_c1_clean_tree_publishes_and_fires_watcher` (:746 — no ghost → COMPLETED + lifecycle completed event + watcher claimed). [JOB_EVENT] hop: unit precursor asserted (`test_receipt_finalizes_with_fresh_summary` :1112 — observer `_process_event` → JobItem ACTIVE→DONE + fresh envelope enqueue); transport delivery pinned by pre-existing e2e `tests/e2e/test_u7s15_realseat_acceptance.py` (unchanged on branch; release-gate class). Known pre-existing DEFECT (global /api/notifications/stream zero job-completion notifications, public-API jobs) is a separate documented lane — not this gate's surface. |
| 5 | WEDGE (stamp→downgrade→no publish→healer one-tick→publish→events→fresh result) | ✅ PASS | Post-fix final turn `test_stranded_grandchild_must_not_hold_mission` (:469) + no stamp/downgrade churn (:534) + mid-wedge holds (f2 skip :678; watch-reconcile lane cannot close :1592; u7 W6 :1153) + healer ONE sweep `test_sweep_once_heals_in_single_invocation` (:923 — scanned=1, healed=1, terminalized_orphans=1, root COMPLETED) + publish/events/result refresh `test_receipt_finalizes_with_fresh_summary` (:1112 — stale-seeded task.result → delivered envelope carries FRESH content; observer → DONE) + event-driven handoff (:1201 schedules heal / :1256 non-liveness does NOT / :1297 unwired healer silent no-op). Controls: C1 :746 (no-ghost fires normally) + C2 :774 (ghost terminalized → next drive publishes). Caveat (documented in-suite): 300s bound is pinned as sweep-once semantics + config wiring (`mission_strand_heal_interval_seconds` default 300, ge=1 fail-fast, config.py:2047-2061; first tick immediate, api.py:1380-1415), not wall-clock sleep. tasks.result refresh = fresh-at-finalize READ (column stays stale by design, DELTA 3) — delivery content is final. |
| 6 | HEALER SAFETY | ✅ PASS | Live-parent ghost NOT terminalized `test_live_parent_ghost_never_terminalized` (:959) + genuinely-live member blocks whole heal (:1005, :606) + u7 W6/W6b (idle-with-evidence holds; ghost excluded) + TESTER: G2a healer probe-RAISE fails closed (healed=False, no terminate, root stays WAITING_CHILDREN), G2b guard probe-RAISE keeps mission held (strand exclusion does NOT fire on unprovable signature), G3 PAUSED straggler under terminal parent aborts heal, no termination, row untouched. |
| 7 | WATCHDOG re-arm | ✅ PASS | `TestWedgeRearmAfterCooldown::test_repark_rearms_and_refires_after_cooldown` (watchdog :1597-1663 — re-parking wake re-fires after `WEDGE_REARM_COOLDOWN_SECONDS=1800` (30×60, :199); no permanent one-shot silence) + `test_cooldown_entry_cleared_on_leave_wc` (:1665) + migrated vgap episode-hygiene/a5-b3 suites green. |
| 8 | MOCK FIDELITY (TrueAuto) | ✅ PASS — 6 MATCH / 1 bounded-risk pinned / 0 high-risk | See audit table below. |
| 9 | EDGE CASES | ✅ PASS | Age guard `<60s` `test_race_guard_young_orphan_skipped` (:1035, `ORPHAN_MIN_AGE_SECONDS=60` healer :102) + TESTER: G4-guard unknown-parent → mission held; G4-healer unknown-parent → abort; G5 root-already-terminal → idempotent no-op (wedge-posture check :254-262, no walk/no terminate/no drive); G6 double-heal → second sweep no-op (scanned=0, no new events); G7 PENDING task row → leg-(3) abort before walk. |

## Mock-fidelity audit (scenario 8) — harness vs REAL contracts @ d195316a6

| Target | Verdict | Evidence |
|---|---|---|
| A. `evaluate_mission_live` semantics (ALL-TERMINAL→live=False immediate :446-461; IDLE-under-live-parent→live :380-385; unknown-parent counts live :357-358/:421-430; strand-probe fail-CLOSED :414-420) | MATCH (low risk) | Suite invokes REAL guard (:594/:639/:1534/:1567); load-bearing wedge/hold tests (:469/:534/:678) run the gate UN-stubbed. |
| B. `_root_completion_gate` emission gate | MATCH (low) | Forced only at :1234/:1280 (hand-off wiring); gate's own decision exercised REAL in 6 tests (:469/:534/:566/:605/:746/:773). |
| C. Ghost-signature predicate (`_ghost_child_filter` child_reports:2715 ↔ `filter_never_dispatched_ids` repository:720 — identical SQL: idle+version==1+0 mq+0 tasks) | MATCH (low) | Suite seeds REAL rows flowing through REAL predicate in both lanes; anti-drift pin test_pause_never_dispatched_ghosts.py:195-211 confirms agreement at HEAD. |
| D. `InstanceLifecycleService.terminate_instance` (healer :420-438, terminal_reason="orphan_retired") | MATCH (low) | REAL service wired in harness (:385-391); sanctioned lane (snapshot + WriteGuardSession txn + cancellation_reason + events), never raw UPDATE. |
| E. Pre-stamp eval fail-OPEN (:2613-2650) vs healer/guard probes fail-CLOSED | INTENTIONAL asymmetry; REAL but BOUNDED defect risk (medium) — now PINNED | Simultaneous transient exception in pre-stamp eval + gate tree-liveness leg (both fail-open, C3 at-least-once contract :2607-2609) can stamp+publish with uncounted live descendants; at-least-once preserved; healer fail-closed probe is the backstop; residual window = transient duration. Pinned by TESTER G8 (fail-open proceeds-to-stamp + gate defense-in-depth), G2a/G2b (fail-closed lanes). |
| F. Lease proxy (`instance_metadata` vs KV row) | INERT-BY-CONSTRUCTION (low) | Completion path never reads `blueprint_lease`/`run_token` from any surface; KV lease belongs to the blueprints subsystem. |
| G. `_DrivePatchStack` seams (async get_instance_messages / async get_last_assistant_timestamp / SYNC run_async_no_wait) | MATCH (low) | Signatures verified against persistence.py:389 / completion_content.py:59 / main_loop_bridge.py:99; stubs preserve async/sync semantics; binding-site patch correct. |

Low-priority pin gaps (documented, non-blocking): gate-leg fail-open pin (partially covered by G8); heal-scheduling reason-discrimination for non-strand tree_liveness blocks; lease-read re-audit trigger; `run_async_no_wait` sync-signature check per commit; predicate third-clause drift (covered by existing anti-drift pin).

## Findings / defects

**New branch-caused defects: ZERO.**

1. 🟠 (advisory, pinned) Target-E bounded risk — see above. No action required now; if a wedge-class recurrence is ever observed, harden the gate tree-liveness leg's fail-open with a bounded retry before publish.
2. 🟢 (developer debt) Terminal-write census: branch ADDS `mission_strand_healer.py:466` (`heal_instance` → `_process_child_completion_and_notify_parent`) to the census test's unclassified-site inventory (8 sites now; census test pre-existing RED at base). When the census fixture is refreshed, add a classification row (`hooked`/`exempt` + reason) for the healer site.
3. 🟢 (lane hygiene) Developer filed no RESULTS/PACKS.md entry for this commission — this gate registers both (pathspec docs commit alongside this file).
4. 🟢 (claims) All developer claims independently reproduced (26/26, 78, 63, 48, 51, A/B-identical-set). mission_resolver "63" = 48 defs + 15 parametrize (no drift). job_queue 2031→2033 (+2 branch authoring).

## ensure.md status (Core, blast-radius scoped)

- ✅ Critical #1 no regressions in changed packs — all scoped packs PASS (pre-existing failures quarantined/documented, zero new).
- ✅ Critical #2/#3 concurrency/thread-identity — `concurrency_atomic_unit_test` PASS, exact baseline 99P/0F/74S.
- ✅ Critical #4 dev.sh `--timeout-graceful-shutdown 10` — static grep (see gate close-out commit).
- ✅ Important #2 original deadlock scenario — covered by concurrency pack.
- ➖ Important #1 (async-await conversions) — out of blast radius (no async conversions in this diff).
- Release Gate: E2E items deferred to promote ceremony (scope note above).

## Documentation updated

- RESULTS/2026-10-14-watcher-loss-terminal-strand-final-gate.md (this file) — baseline record.
- PACKS.md — commission header + 9-pack registration (newest-first).
- QUARANTINE.md — no changes (no flaky tests encountered; pre-existing families already documented).

## Test-artifact commits

- `bfb242d925c9c10d54eb11d6e2a319dc6bfcf4d5` — test(wlts-gap): commission gap tests G1–G8 (tests/job_queue/test_watcher_loss_terminal_strand_gap.py only).
- docs commit (this file + PACKS.md pathspec) — see close-out.

**Overall: 🟢 VERDICT PASS — all four commission-mandated scenarios exist and pass (3 completed by tester-authored G1), fix-specific behaviors 5–7 verified, mock fidelity confirmed with one bounded pinned risk, edge cases closed, zero branch-caused regressions across 5,184 independently-collected tests.**
