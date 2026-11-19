# TESTER DELTA RE-VERIFICATION — wake-path-carrier-fix (board wi-8f60e6f1)

**Date:** 2026-11-19 (UTC) · **Worktree:** `/home/nea/ensemble-src-wt-wake-path-carrier-fix` · **Branch:** `feature/wake-path-carrier-fix` @ **`3367bcc659eb1f0397030530f12bad023fdbd4f1`** (delta = 1 commit over prior-gate tip `00e6fd936`: 15 files, +397/−58, 3 production / 12 test-doc / 0 unclassified)
**Verdict: PASS** — all six mandate items green; the reviewer-flagged mutation gap is CLOSED (mandated counter-mutations redden R3); r3 fence byte-identical; PG null-delta at the new tip; F-1 stamps non-vacuous; zero drift. Zero edits / zero commits / zero live contact; prior gate artifacts left untouched.

**Prior gate:** `RESULTS/2026-11-19-wake-path-carrier-fix-tester-gate.md` (verdict PASS @ `00e6fd936`). This file records ONLY the delta re-verification.

**Workers:** rever-pack (reuse `e180ff68`) · rever-suites (reuse `1c3c0b34`) · rever-mutation `066c8b19` · rever-fence `8fe6542b` · rever-pg (reuse `2dc03d7b`) · rever-f1 `5575674e` · rever-diff `6c3fcdfe`

---

## 1. Item 1 — primary pack + core suites @ 3367bcc65

| Run | Verbatim | Verdict |
|---|---|---|
| Pack `test/packs/wedge_fix_suites_unit_test.sh` | `113 passed, 107 warnings in 10.89s` · `RESULT: PASS` · EXIT 0 | ✅ (≥113 met; +3 over prior 110) |
| `tests/unit/services/test_waiting_children_watchdog.py` | `51 passed, 5 warnings in 4.24s` · EXIT 0 | ✅ (node set byte-identical to prior tip, AST-diffed, delta 0) |
| `tests/unit/test_reconciler_wedge_fix.py` | `45 passed, 21 warnings in 8.93s` · EXIT 0 | ✅ (+3) |
| `tests/unit/services/test_a5_wedge_notify_work.py` | `9 passed, 1 warning in 0.99s` · EXIT 0 | ✅ |

R3 `TestS3BlipPaths` located at `tests/unit/test_reconciler_wedge_fix.py:2946` — nodes: `test_r3a_subject_read_failure_leaves_the_episode_untouched`, `test_r3b_subject_read_failure_emits_no_classic_wedge_row`, `test_r3c_carrier_read_exception_completes_the_tick` (all PASSED). T5 clauses now explicit: `TestS4NoticeSurface::{test_t5a_same_episode_notifies_exactly_once, test_t5b_wc_to_running_flip_does_not_end_the_episode, test_t5c_row_check_turning_false_ends_the_episode}` — all PASSED. B3 node PASSED verbatim.

**Wording note (doc-only):** the commission said "R1 fail-open read semantics"; the landed code is fail-**closed** (`waiting_children_watchdog.py:970-974` "a read failure may not end an episode — it may only postpone the verdict"; `:2038` "no notice on an unknown carrier state"). Commit subject + R3 test names agree with fail-closed.

## 2. Item 2 — MUTATION LEG (reviewer-flagged gap CLOSED)

Scratch-copy only (`/tmp/wpc-mut-3367*`, `git archive 3367bcc65`, `daemon.__file__` asserted per copy). Baseline: 3 R3 nodes GREEN (`3 passed, 42 deselected`); full file `45 passed`.

| Mutant | Reddened | Full-file signature |
|---|---|---|
| **M1** tri-state `None`→`False` + UNKNOWN-guard deleted (the mandated None-verdict→bool revert) | **r3a, r3c** | 2 failed / 43 passed — discriminating |
| **M2** `_in_scope` fail-closed→pre-R1 fail-open (mandated) | **r3a, r3b** | 2 failed / 43 passed — discriminating |
| M3a carrier-read `None`→`False` | none | 45 passed — ⚠️ gap (below) |
| M3b carrier-read `None`→`True` | r3c | 1 failed / 44 passed |
| M3c literal pre-R1 (exception propagates, no local try) | none | 45 passed — ⚠️ gap (below) |

Sensitivity map: r3a←M1,M2 · r3b←M2 · r3c←M1,M3b. **Mandated mutations (M1, M2) both redden R3 ⇒ R3 is empirically non-vacuous.** Verbatim assertion messages captured (e.g. `R3(a): a subject-read failure must leave the episode map UNCHANGED (got ('no_live_carrier',), expected ('no_live_carrier', 'pending_watchers'))`; `R3(b): a failed subject read must NOT admit the classic-wedge arm`; `assert 2 == 1`).

**RESTORE PROOF:** sha256 `waiting_children_watchdog.py` `5dc5491…5cab8` identical before/after; HEAD `3367bcc65`; `git status --porcelain` identical; `git status --porcelain daemon/ tests/ test/` EMPTY.

**⚠️ NEW FINDING RV-1 (🟠 coverage gap, R1(3)):** the carrier-read exception arm is only NARROWLY pinned. Reverting R1(3) to its literal pre-R1 shape (M3c — no local try/except) is **undetected by the entire 45-node file**: the outer per-tick `try/except` (`run_once` :1860/:2200) swallows the blip, so r3c cannot distinguish local handling from outer-guard handling; behavioral cost (one carrier blip aborts the remaining wedge pass for that tick) is invisible because r3c seeds a single parent. M3a (`unknown→False`, the primary R1(3) harm) also goes undetected (single-parent dedupe suppresses the would-be false notice). Hardening (not applied): (a) r3c multi-parent variant — parent A's carrier read fails while parent B must still be processed; (b) carrier-blip whose fingerprint would otherwise change, so a `False` collapse mints a detectable notice.

## 3. Item 3 — r3 fence (independent): BYTE-IDENTICAL

Four healthy code-sets = the four wedge reason codes (`pending_watchers`/`no_live_carrier`/`queued_report`/`non_terminal_children`, `waiting_children_watchdog.py:222-225`, plan §d.1 `:536-541`; plan↔code agreement) → single renderer `_build_wedge_notice` (sole production call site `:2098`, always passes the count explicitly).

| Code-set | old↔new sha256 | Verdict |
|---|---|---|
| pending_watchers / no_live_carrier / queued_report / non_terminal_children + composite + empty-set | `9b0ab970…` / `77bf5967…` / `3d2c0b92…` / `da1ebd1d…` (all equal both trees) | **IDENTICAL ×4 (+2)** |

Rendered text == `message_queue.content`. Observable state on real `run_once()` + real repos: notice rows 1→1, row-text shas equal, episode maps equal; no healthy code-set evaluates to `None` (True/True and False/False both directions). **Non-vacuity:** 3 discriminating counter-mutation legs DIFFER (subject-read blip 1→0 notices; carrier blip aborts-old vs parent#2-notified-new; queue-read blip narrows-old vs untouched-new). Disclosed out-of-fence renders = intentional fold-in #8 (`0→None` default; "at least one child (the exact count is unknown)") — unreachable on the healthy path; the one no-arg test call renders IDENTICAL.

## 4. Item 4 — PG list @ 3367bcc65: NULL DELTA

`git diff --name-only 00e6fd936..3367bcc65 -- tests/postgres/ daemon/repositories/dependency_bus/` = **empty**; sole adjacent edit (`daemon/services/dependency_bus.py`) is **docstring-only** (R2/B1). Suites (role `ensemble_dev_app`, DB `ensemble_test`, `--override-ini="addopts=" -m postgres`): dep_bus_pg `6 passed` ✅ · phase2_pg `14 passed` ✅ · 06f500af `1 failed, 2 passed` — SAME node/line as prior tip (`:489 assert 'PENDING' == 'CANCELLED'`), still QUARANTINE-registered grace-gated pre-existing. Probes reproduce byte-for-byte: (i) True/False + 5/5 exactly-one-winner (guarded CAS UPDATE `… IS NULL`, SQL captured); (ii) IN-batch exact; (iii) **Seq Scan cost 0.00..12.25, est rows=1 vs actual 11** (Res8, identical); (iv) guard live at `dependency_bus.py:724-745`. **Integrity:** `ensemble_dev` 111→111 tables, probe rows 0; `ensemble_test` 0→0; 12 rows deleted (count 0); status sha256 identical; HEAD unchanged.

## 5. Item 5 — F-1 non-vacuity: NO VACUITY DEFECT

- **Census:** 10 mock/double stamps across 10 files (+1 `assert_not_called` pin) — exact match to the brief. Production consumer invocations **measured 14** (12 direct + 2 via fail-open helpers; the brief's "11" is the plan's older sync-reader census — the untouched claim is the load-bearing one and is airtight: the delta contains **zero** call-site lines; the 3 changed production files' hunks touch none of the 14).
- **Empirical exercise map** (sentinel instrumentation, 73 tests): only 4 of 10 files reach a qualified read; the other 6 stamps are **provably inert** (nothing on their paths reads the name → cannot mask an assertion; pass under both polarity mutants).
- **Counter-mutations (scratch copy):** BLOCK mutant (heal/completion provably does NOT happen, name still truthy) → **14 RED** — every positive-asserting node reddens with verbatim messages. PASS mutant → **1 RED** (the deny-asserting `test_the_bus_pending_leg_is_untouched`). Pre-F1 stamp-revert → **4 RED incl. the exact `TypeError: '>' not supported between 'MagicMock' and 'int'`** the stamps prevent ⇒ stamps are **load-bearing** for question-pause/report-deferred/pr2_l3; `report_obligation_emission_pins`' stamps redundant-but-harmless (bus singleton None in fixture).
- **Healer scope note:** the delta's 10 stamps cover completion-gate/completion-path reads, NOT the healer probes (`mission_strand_healer.py:567/:1250`) — the healer stamps live in base commit `62cbac8ec` (ancestor of `00e6fd936`, outside this delta) and were separately verified non-vacuous (healer-block mutant → `test_positive_heals_the_live_shape` RED with `reason: 'bus-pending: bus_pending=1'`).
- Observation (not a defect): `test_obligation_leg_holds_before_wedge_resolver` is reason-agnostic — would pass if the bus leg (not the obligation leg) blocks; consider tightening.

## 6. Item 6 — zero-drift: CONFIRMED

Cumulative `eca98397d..3367bcc65`: **54 files, +4836/−327** (1 A / 53 M / 0 D / 0 R). Migrations **0** (no `.sql`; `daemon/migrations/` wholly untouched; added-line DDL/ORM/alembic scan = 0 → `rollback_safe` untouched). Hard exclusions **a–k all 0** (`skill_*`×2, `job_processor.py`, `config.py`, `manager.py`, `graph.py`, `scripts/upgrade/**`, `frontend/**`, `agents/video-*`, `repair/**`); non-vacuity controls fired (`^daemon/`=10, `^tests/`=42). Arithmetic reconciled exactly: 43 + 15 − 4 overlap = 54; line residual −41/−41 confined to the 4 rewritten-overlap files.

## 7. Findings register (delta re-gate)

| # | Sev | Finding |
|---|---|---|
| RV-1 | 🟠 | R1(3) carrier-read exception arm narrowly pinned — literal pre-R1 reversion undetected by the whole 45-node file (M3a/M3c); hardening spec in §2 |
| RV-2 | 🟠 (carried, sharpened) | S1 arm-refusal guard (`dependency_bus.py:724-745`) has **no behavioural test anywhere** — 125 `.watch(` sites in `tests/`, all positive, zero assert the refusal ⇒ a future arm lane forgetting `child_id` is caught only by a runtime WARNING |
| RV-3 | 🟡 | No test pins that `delivered_watch_id` and `enqueued_at` markers stay independent (R2/B1 docstring claim untested) |
| RV-4 | 🟡 | `test_obligation_leg_holds_before_wedge_resolver` reason-agnostic (assertion strength) |
| RV-5 | 🟡 | Census correction: 11 → 14 consumer invocations (plan-doc number, not load-bearing) |
| RV-6 | 🟢 | Brief wording "fail-open" vs landed fail-closed; orphan-sweep test stale prose + QUARANTINE misattribution (carried) |

## 8. Integrity

Zero edits (all legs: status/sha256 identical before/after; `daemon/ tests/ test/` porcelain empty); zero commits/branches/tags/stashes (39 pre-existing stashes untouched); no process signals; port 8088 untouched; `ensemble_prod` never contacted; `ensemble_dev` proven untouched (111→111, probe rows 0); prior gate artifacts (RESULTS + 2 LESSONS, mtimes 21:18Z) left exactly as they were.

## 9. Recommendation (post-gate, lane owner's call)

1. RV-1 hardening (two r3c variants — small test-side commission; the reviewer explicitly cares about this surface).
2. RV-2: one negative PG/unit pin for the arm-refusal guard (watch() without child_id ⇒ WARNING + zero rows).
3. RV-3: independence pin for the two delivery markers.
4. RV-4 assertion tightening; RV-5 plan-doc census refresh.
