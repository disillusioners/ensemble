# Deep Review — WC wake/resilience fix program (2026-09-11)

## Target
- Branch `feature/fix-wc-wake-resilience` @ 30602cd9, base 8a30f75b (verified = latest tip)
- 11 commits A1–A5 / B1–B4 / C1–C2 (1:1 with sub-fixes), 32 files, +7894/−998
- Mode: 🔴 Deep-Review council — governor 7708497b, 2 councilors (`agentic` / `coding`), councilor skill `code-review`

## Verdict
**0 blockers; 4 warnings (W-A docs / W-B freshness-not-liveness / W-C watcher-cancel race / W-D eternal notify); merge-eligible on runtime correctness.** All 10 adjudication points PASS (5 and 10 with warnings attached). Owner policy compliant except stale docs row.

## Key adjudications (evidence)
- **A1 divergence judged STRONGER than the brief**: upstream-at-birth single choke point (`instance_messaging.py:1755` — `is_deferred_for_task = bool(is_deferred) and not is_terminal_revival`; status read `:1725` pre-dates the terminal→RUNNING flip `:1841-1856`, same WriteGuardSession). Repo-wide enumeration found exactly ONE live PROCESS_MESSAGE `Task(` constructor (`:1790`, shared prelude of `enqueue_message:1967` / `enqueue_message_job:2125`); only `True`-direction writer is a pre-branch boot backfill (`manager.py:5623`).
- **B3 forced release is FRESHNESS-gated, not LIVENESS-gated**: hung predicate keys on instance `last_activity_at` (`repositories/instance/repository.py:3001-3100`) which no writer refreshes during a running turn (only `task.last_heartbeat_at` moves). Dev's "cannot fire while genuinely working" claim overstated.
- **C2 periodic sweep re-opens a race the startup-only design closed**: terminal-commit→emit window lets a 90s tick CANCEL the not-yet-fired watcher (`dependency_bus.py:1943-1951`). Bounded: FollowUp dropped, report still delivers via PROCESS_REPORT lane, parent released not wedged.
- **A3 sweep lacks liveness join**: unclaimable rows (paused/terminated instances) notify once per 90s forever (`eligible_pending_sweep.py:212-238` vs claim gate `task/repository.py:1533-1538`).
- **Owner policy**: runtime airtight (ImportError pin `test_b1_wc_durable_send.py:145-148`; `ENSEMBLE_DEFER_AUTOPROMOTE_ENABLED` pre-existing default-ON — intro f7804a9e + flip 4a64690e, both pre-base), but `docs/setup.md:368` still carries an operator-actionable row for the removed `ENSEMBLE_WC_WAKE_ENQUEUE`.
- **Base-comparison methodology worked**: detached worktree at base; the two `test_injection_api.py` waiting-children tests fail with identical `MagicMock can't be used in 'await' expression` TypeError at base AND tip (`messages.py:258` base vs `:249` tip — line shift = B1 branch collapse) → pre-existing fixture defect confirmed.

## Reusable patterns
- Birth-site enumeration via "grep for the single `Task(` constructor" is decisive when adjudicating claim-gate vs birth-site design divergences.
- Freshness-proxy vs true-liveness distinction must be checked whenever a watchdog claims "won't fire on working children" — check WHO refreshes the predicate's column during a running turn.
- Periodic-izing a startup-only sweep re-opens races the startup design structurally closed — audit window interleavings (commit→emit, claim→notify).
- Flag-removal changes (B1-class) must sweep `docs/` too: stale operator rows for dead flags are actionable hazards (operator flips a flag that no longer exists / gets false confidence).

## Housekeeping
- Detached base worktree left for reaping: `agents-ensemble-wt-base-wc-wake-resilience` (councilor 2; caller/giter decision — untouched by review).


## Delta re-review cycle 1 (2026-09-11, delta 30602cd9..d12288d4) — ✅ MERGE-READY
All four warnings CLOSED on fresh evidence (governor 7708497b revived; 2 fresh councilors, verdicts re-derived, tests re-executed):
- **W-A CLOSED** — setup.md:368 row removed; stability-backlog.md:86 struck with FIXED→88a27f71; job-task-system.md:1179 durable-enqueue-only; zero surviving operator rows repo-wide.
- **W-B CLOSED** — indexed UTC-bind probe (task/repository.py:864-956); gate before release (watchdog :1104-1107) AND escalation (:1157-1160); suppressed-but-counting (:1041-1044); reuses hang_threshold_seconds (:1073-1077); DB-error → release withheld = correct fail-closed; writer = TaskHeartbeat thread (worker_pool.py:60-160, 30s cadence vs 3600s threshold = 120 beats/window); no counter/episode leak (purge :1372-1385). 18/18.
- **W-C CLOSED** — grace folded into atomic UPDATE (dependency_bus.py:2022-2060); 30s adequate (terminal-commit→emit window contains NO LLM work — repair pre-commit child_reports.py:1884-1897, emit post-commit :3874-3900; seconds-scale); pins legitimate (kwarg-additions only, ZERO assertion changes). 7/7 + knob mechanics (default 30, 0 accepted, −1 rejected).
- **W-D CLOSED** — strict superset verified side-by-side (claim gate excludes {paused,terminated} task/repository.py:1613-1619 vs sweep {paused,completed,error,terminated,failed} instance/repository.py:2856-2861,:3193-3205); bounded PK IN-list probe; skip class exists but NON-STRANDING via notify_work() global-pulse mechanism (worker_pool.py:1300-1308 — no task ids; any worker wake rescans full PENDING queue; revival re-admits within one tick; pre-existing resume notify instance_lifecycle.py:3541). 18/18 (5 new + 13 existing).

**New non-gating finding W-1-doc:** `orphan_watcher_sweep_grace_seconds` docstring incomplete — the STARTUP sweep hard-defaults 30s regardless of the knob (config.py:1379-1383 vs dependency_bus.py:1553); grace=0 governs only the periodic sweep. Should-fix docstring.

**Governor policy adjudication (reusable):** "no off-switch" policy = no value disables the FIX PRIMITIVE. grace=0 only reverts W-C hardening to the cycle-0-merge-eligible state (interval floors at 1s keep the sweep running) — a policy-wording question, not a bound question; ge=1 would change nothing.

**Known accepted residual:** wedged-turn-alive-child — heartbeat thread is independent of worker execution, so a wedged-but-heartbeating child suppresses release indefinitely; deliberate, matches system-wide liveness stance (find_stale_running_tasks same predicate); ungated base hang notice preserves visibility.

**Flake adjudicated pre-existing:** TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert — matched signatures (sqlite3.InterfaceError bad-parameter, InvalidRequestError refresh race) reproduced at base 30602cd9 (2/10) AND tip d12288d4 (1/10); SQLite StaticPool concurrency class.

Cross-cutting: census zero-diff 10/10; no new ENSEMBLE_* flags (W-C knob uses SERVICES_* tuning convention); adjacent spot-run 249 passed / 10 files; worktree clean at d12288d4.


## Delta re-review cycle 2 (2026-09-11, delta 35f72930..01cf157e) — ✅ MERGE-READY (final cycle)
All 5 items PASS on fresh cross-verified evidence (2 fresh councilors; two INDEPENDENT derivations of the B4 hazard-class analysis converged on every point — divergence was line-anchor drift only):
- **B4 re-mint PASS** — three-condition gate necessary+sufficient both directions: cond1 `parent_id is not None` (child_reports.py:3844), cond2 fresh re-read `status==COMPLETED` (:3845-3852, fetch-failure → silent self-healing no-op), cond3 PENDING-watcher implicit INSIDE the bus helper (dependency_bus.py:874-887) and atomically re-verified at write — stronger than a pre-check, no TOCTOU. All 8 idempotency_skip producers enumerated and ruled (P1/P1b/P1c/P1d/P2/P2′/P3/P4/P5). Exactly-once hop chain traced hop-by-hop: re-mint :3843-3863 → `_emit_terminal_for_child_instance_via_bus` :563-641 → bus fetch :874-877 → per-task lock :900-902 → guarded UPDATE inside lock :903-907 → repository rowcount :736-743 → loser `continue` :909-919; second emitter rowcount==0 no-ops (no append/stamp/delivery). Structural mutual exclusion between inline site (:3882 early-return) and :4195 backstop (reachable only for unknown outcome strings). Interleavings hunted: re-mint∥re-mint, ∥task-keyed emit (same lock family), ∥W-C sweep cancel (guarded transition wins once). Flipped e2e pin PASSED through REAL DependencyBus + real SQLite tmp_path asserting actual row.state PENDING→FIRED; zero surviving xfails; 15/15.
- **B3 explicit fail-closed PASS** — True=assume-alive; consumer `continue` (:1142) sits before release (:1143-1183) and escalation (:1187-1233) → both withheld; WARNING with `probe_exc!r` (:879-885); `except Exception` judged correct (catches all sync DB shapes; correctly does NOT swallow CancelledError/BaseException). Gap-fill narrowing adjudicated TRUE-semantics: dropped `await_count==0` was an artifact of cycle-1's incidental fail-closed; `release==0 ∧ escalation==0` preserved verbatim + strengthened messages. 3/3.
- **D2 logger PASS** — hunk exactly −2/+1, event/level unchanged; AST scan of all daemon/ at HEAD = 0 remaining invalid logger-kwargs sites (independent confirmation).
- **PACKS PASS** — docs-only; 88a27f71 pointer verbatim-accurate; claimed 90-line deletion verified via diff-filter=D.
- **Cross-cutting PASS** — census zero-diff 10/10; zero new ENSEMBLE_*; intermediate tester/tidier range (d12288d4..35f72930) untouched (per-commit file-list proof; 4 shared files additive-only); 15/15 + 3/3 + 228 adjacent / 0 failed.

**New warnings (non-gating):** W1 — unconditional wedge-heal WARNING at re-mint site (child_reports.py:3864-3870) fires on routine legit-skip shapes (dup completion, watcher already FIRED → helper returns [] which is discarded) → recurring misleading ops-log noise under RESUME_ROUTER ×4-dup shapes; log-only fix (warn only when returned list non-empty). W2 (for the record) — cond2 is COMPLETED-only: ERROR-terminal child with wedged PENDING watcher NOT healed by this site (error lane has own hook error_reporting.py:636); needs explicit accept/defer decision. W3 — cosmetic docstring anchor drift (watchdog.py:819 cites :1267+, actual :1297).

**Dev-claim caveat:** "escalation clause not triggered — traced conflict candidate found benign" UNVERIFIABLE as written (phrase greps to zero hits anywhere) but immaterial — both code-derivable candidates independently re-derived benign (inline-vs-backstop: structurally excluded; re-mint-vs-watchdog-escalation: no shared state, escalation keys on hung non-terminal children).

**Scope caveat (adopted):** branch honors the bus obligation (parent job-finalization gate); full un-park in PROD additionally requires merge + pause-first restart — prod WC-park family (P1–P6, pending user decision per critical notes) persists until then. Deploy timing, not a branch defect.

**Final: MERGE-READY.** Cycle-3 highest-value item if spent: W1 (log-only); W2 = the one explicit design decision.

## Reusable patterns (cycle 2)
- "Condition implicit inside the guarded-write helper + atomic re-verification at write time" = stronger-than-precheck pattern — prefer when adjudicating gate-check-vs-write races (kills TOCTOU by construction).
- Unconditional WARNING at a multi-shape site where a DISCARDED return value distinguishes outcomes = recurring ops-log noise class; check whether the discarded list carries the signal needed to condition the log.
- When a dev cites an unlocatable phrase, re-derive both plausible referents independently rather than blocking on the wording — adjudicate the code, not the anecdote.


## Nano-review cycle 3 (2026-09-11, delta dc73fb6f..e18f8c46) — ✅ MERGE-READY — review chain CLOSED
- Single standard worker (`code-review` skill) — proportionate to a log/docs/test-scoped delta; no council.
- **All 5 items PASS, 0 findings of any severity:**
  - W1 conditional: `fired_followups` captured + gated at `child_reports.py:3924`; helper call byte-identical (same kwargs/status/summary); dup-completion shape silent (helper `[]` falsy); `TestReMintConditionalHealWarning` 2 tests NON-tautological — real bus + real engine + real `FollowUp.from_payload`, assert real DB `row.state==FIRED` before caplog; non-empty case pins child/parent id prefixes in warning text.
  - N1: DEBUG (not WARNING+) with `{fetch_exc!r}` at `:3872-3883` (re-mint) and `:4339-4352` (backstop); `except Exception as fetch_exc:` rebind preserves catch shape; no propagation change.
  - Fixture disclosure: latent KeyError REAL — `FollowUp.from_payload` (`dependency_bus.py:226-248`) requires `target_instance_id`/`message` with no `.get()` fallback; old fixture lacked them; KeyError fired AFTER `transition_state` committed FIRED and was absorbed by the defensive try/except (`:3933-3943`/`:4369-4383`). Earlier assertions valid under both shapes (row.state commits before from_payload); what earlier evidence proved less: FollowUp construction — which NO earlier verdict rested on. Zero assertion weakening (test diff purely additive; 1 removed line = diff header). 18/18 pass (16 pre-existing + 2 new).
  - Docs: W2 scope note matches code (gate `:3884-3886` COMPLETED-only; error lane `error_reporting.py:635-647` own hook); all 9 backstop early-return anchors verified line-by-line (`:4274-4308` comment accurate); W3 now search-based drift-proof anchor (old pin was ~:1300 actual, not :1297); N4 PACKS points at REAL successor `InstanceManager.enqueue_message` (`manager.py:6656`) + live watchdog call site (`:1263`), removed symbol flagged as such.
  - Cross-cutting: 2-commit shape as claimed; constitution zero-diff; zero new flags (only PACKS docs mention of removed flag); tester lineage (dc73fb6f, 159ce3a2) untouched; worktree clean; 18/18 touched file + 18/18 W-B watchdog companion.

**CHAIN VERDICT (close-out): cycle 0** (deep review @ 30602cd9: 0 blockers, 4 warnings W-A..W-D) **→ cycle 1** (delta @ d12288d4: 4/4 warnings CLOSED, MERGE-READY, 1 new non-gating W-1-doc) **→ cycle 2** (delta @ 01cf157e: 5/5 items PASS incl. B4 re-mint exactly-once re-verification, MERGE-READY, W1/W2 non-gating) **→ cycle 3** (delta @ e18f8c46: 5/5 PASS, 0 findings, MERGE-READY). Branch approved through final gate: W-A..W-D closed, W1 closed, N1/N2/N3/N5/W3 closed, W2 documented as accepted design scope (C1 owns ERROR-terminal; backlog residual).

**Deploy caveat (final reminder for approver):** branch honors the bus obligation; full PROD un-park additionally requires merge + pause-first restart — prod WC-park family (P1–P6, pending user decision per project critical notes) persists until then.

## Reusable patterns (cycle 3)
- Defensive try/except around a POST-COMMIT payload construction silently masks fixture-shaped KeyErrors: row-state assertions survive (state commits first) while construction coverage vanishes. When auditing an "additively enhanced" shared fixture: diff required-keys against the model's `from_payload`, then audit whether any earlier assertion rested on the masked shape.
- Line-pinned docstring anchors rot (cycle-2 :1297 claim was actually ~:1300); search-based anchors ("search for X near Y") are drift-proof — prefer them for comments referencing shifting code.
- Proportionality discipline held: full council for concurrency-semantics deltas (cycles 0-2), single skilled worker for log/docs/test-scoped nano-delta (cycle 3).
