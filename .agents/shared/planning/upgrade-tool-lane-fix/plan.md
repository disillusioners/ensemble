# Plan: v0.15.3 Upgrade Tool-Lane Live Promote Fix

**Date:** 2026-09-26
**Mission:** Tool-lane live promote structurally functional (arm → executor → gate → commit) with loud failures, cut as v0.15.3.
**Branch:** `feature/upgrade-tool-lane-fix`
**Base:** `latest @ 139ba352` (v0.15.2); local `latest == origin/latest` (0/0 divergence at branch creation).
**Release:** v0.15.3 sits on top of v0.15.2. NEVER touch master (DEAD since v0.3.3).
**Workdir constraint:** READ-ONLY on source. ONLY write is `.agents/shared/planning/upgrade-tool-lane-fix/plan.md`.

---

## Artifact Map

| File | Role | Status |
|------|------|--------|
| `research-findings-python.md` (323 lines) | Python anchors: manager.py drain seam, upgrade_journal.py guards, upgrade_tools.py gate, test pins | VERIFIED, read in full |
| `research-findings-shell-e2e.md` (272 lines) | Shell anchors: stage.sh rider + ADR + BSD fences + pack structure + ensure.md lane rule | VERIFIED, read in full |
| `phase1-plan.md` (545 lines) | P1: 5 implementation items + Item 7 ratified branch + ~20 sibling tests (per C3b/C3a) | PRIMARY CONTENT |
| `phase2-plan.md` (490 lines) | P2: ITEM 6 stage.sh rider + ADR-035 mint + 9a-9g rider tests + residual ops gates | PRIMARY CONTENT |
| `plan.md` (this file) | Unified implementation plan; synthesizes P1 + P2; resolves 2 cross-phase items; release-cut + open questions + constraints | THIS FILE |

W3 integrator's downstream artifact: `plan-overview.md` (NOT produced here — absorbs P2's "Residual Ops Gates" section from `phase2-plan.md:234-264`).

---

## 1. Objective + Completion Test

**Objective (carried verbatim from `phase1-plan.md:14-19`).** Make the tool-lane live promote **structurally functional** (arm → executor → gate → commit) with **loud failures** so that the 3-factor-verified arm:
1. actually reaches the gate (today: child exits 78 invisibly — `research-findings-python.md` §1b).
2. is observed end-to-end (today: child exit invisible to daemon, no journal event, `upgrade_status` reports TERMINAL on a non-terminal state).
3. re-arms cleanly after a stale arm (today: ~20min starvation — `reconcile_pending_op` tool-entry-only).
4. closes F2 via the **registered-source nonce-echo attestation** the user ratified 2026-09-26 (single ratified branch; ADR-017 tool-lane posture SUPERSEDED for verified arms only).

**Single-sentence completion test (carried verbatim from `phase1-plan.md:20`).** *"A 3-factor-verified live arm, with no F2 forge, completes arm → spawn → gate → commit → TERMINAL, with the in_flight journal stamped `f2_verified_closed: true` + `f2_verified_note: <source>:<run_id>`; an unverified arm exits 78 at `require_live_guard` and the failure is journaled."*

**Companion outcome (P2, `phase2-plan.md:10`).** `stage.sh:172-180` silent-false derivation path is DEAD; explicit `1|true`/`0|false` honored; unset+DROP refuses exit 78 with actionable WARNING; unset+no-DROP defaults `true` quietly. ADR-035 minted.

---

## 2. Phase Architecture (P1 ∥ P2)

**Coupling posture:** P1 modifies ONLY Python (manager.py, upgrade_journal.py, upgrade_tools.py + Python-side tests). P2 modifies ONLY `scripts/upgrade/stage.sh` + `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` + `tests/test_release_journal.sh` (shell journal suite). Zero file overlap. P1 ∥ P2 parallel; integrator merges both into the v0.15.3 commit batch.

| | **P1 — Python subsystem** | **P2 — Shell rider + ADR** |
|---|---|---|
| **Scope** | Items 1-5 (5 implementation items, internal seq 7→1→3→5→4→2) + Item 7 ratified branch (doc-only). See `phase1-plan.md:51-58`. | ITEM 6 stage.sh rider + ADR-035 mint + 9a-9g rider tests + residual ops gates doc. See `phase2-plan.md:27-39`. |
| **Implementer instance** | **ONE** implementer instance, same-subsystem grouping, internal sequencing groups tight-coupled items (1→3 share verified-arm predicate; Item-4 sweep service owns the reaper queue+worker — Item 2 publishes to Item 4 instead of running an ad-hoc InstanceManager reaper; 5, 7 independent). | **ONE** implementer instance; rider is one shell block + tests + ADR insert + GNU-debt exclusion fence (triple-encoded). |
| **Sequencing** | Internal: 7 → 1 → 3 → 5 → 4 → 2 (decision-table first, sets contract; enabler second, opens seam; preflight uses predicate from Item 1; class filter independent; **Item 4 lands BEFORE Item 2** — the sweep service is the reaper owner, so Item 2's journal events publish through the sweep's reaper worker rather than introducing a second observation path; safe-by-construction beats safe-by-coincidence). | Rider (T1) → log line (T2) → ADR insert (T3) → tests 9a-9g (T4+T5) → Residual Ops Gates doc (T6) → GNU-debt fence comment (T7) → BSD portability review (T8). |
| **Exit criterion** | `phase1-plan.md:389-395`: all 5 items merged, two-sided contract matrix green both sides, per-item new tests pass, packs extended, **ADR-035 minted** (refs P2's mint — see §8 RESOLUTIONS), no live touched. | `phase2-plan.md:459-467` 7-item checklist: rider lands at stage.sh:172-180, log line :381 carries `source=`, 9a-9g + 9 sections remain green, ADR-035 inserted between ADR-032 (:213) and pre-freeze checklist (:215), Decision Index table updated, no opportunistic lib.sh GNU-debt fixes. |

---

## 3. Consolidated File-Level Change Map

| File | Change | Phase | Anchors |
|------|--------|-------|---------|
| `daemon/manager.py` | ADD argv flag slot + extra_env fields on verified arm; MOVE `read_pending_op` BEFORE spawn; **ENQUEUE `(pid, run_id)` to Item-4 sweep service's reaper queue** (NO ad-hoc InstanceManager reaper; NO module-global Popen registry; reaper owner = Item-4 sweep worker) | P1-I1, P1-I2, P1-I4 (enqueue only) | `manager.py:3795-3799`, `:3808-3815`, `:3823`, `:3832`, `:3843-3846` (enqueue site; worker lives in Item-4 service) |
| `daemon/tools/upgrade_journal.py` | Extend `_gc_pending_actions` core to public `gc_pending_actions(keep_run_id)`; new event class `executor_exit` (emit-NOTHING ordinary, additive journal_history_append at `:301-316`) | P1-I2, P1-I4 | `upgrade_journal.py:789-821`, `:301-316`, `:890` (NOT added to _TERMINAL_EVENTS), `:994-1005` (NO widening; extras passthrough via :1003-1004 stays the seam), `:1012-1037` |
| `daemon/tools/upgrade_tools.py` | Filter `_terminal_outcome` to `_TERMINAL_EVENTS` (skip `nonce_consumed`); rename label `"live-confirmation nonce consumed"` → `"awaiting executor (pending)"`; add preflight block BEFORE burn at `:2672-2688` | P1-I3, P1-I5 | `upgrade_tools.py:1021-1036`, `:1039-1048`, `:2672-2688`, `:2691-2706` |
| `daemon/api.py` | Register new `UpgradeJournalSweepService` in lifespan after `_boot_db_preflight`; SAME pattern as `JobLockSweepService` (:845-868) with own ServicesConfig knob | P1-I4 | `daemon/api.py:845-868` (pattern), `:1847` (sibling reference), `:1864` (shutdown) |
| `daemon/services/job_lock_sweep.py` | NO change — pattern source only | P1-I4 pattern | `:128-188` (start :144, stop :168, `_run` :222) |
| `tests/unit/tools/test_upgrade_tools.py` | SIBLING TESTS — ZERO existing pins flip (C3b): `:3388-3391` (argv) + `:3439-3444` (allowed-set) STAY GREEN as the unverified side, NOT parametrized; verified side = NEW standalone sibling tests (§5c: `test_promote_argv_includes_f2_flag_when_verified`, `test_extra_env_allowed_when_verified_includes_live_and_note`); STAY GREEN `:3423-3434` poison | P1-I1, P1-I2 (poison) | `:3388-3391`, `:3439-3444`, `:3423-3434` |
| `tests/unit/tools/test_upgrade_journal.py` | SIBLING TESTS — ZERO existing pins flip (C3b): `:902` (extras set) STAYS GREEN as the unverified side (no parametrize); verified-side extras passthrough asserted by NEW standalone sibling test `test_executor_env_extras_passthrough_when_verified` (§5c); STAY GREEN `:935/:962` poison + `:910-977` real-spawn + `:867-908` allowlist purity | P1-I1, P1-I2 | `:902`, `:935`, `:962`, `:867-908`, `:910-977` |
| `tests/test_release_journal.sh` | NEW 6+1 cases (9a-9g) for rider; STAY GREEN `:188/:200` (string-contains on promote F2 gate) and `:297-301` (`manifest has rollback_safe`) and `:821-826` (`adopt_stale_txn` halt) | P2-T4+T5, P1-I2 (stays green on bash side) | `:188`, `:200`, `:297-301`, `:821-826` |
| `test/packs/upgrade_tool_interlock_unit_test.sh` | EXTEND — verified-arm tests (fake marker + /tmp fixture, same style as LIVE-gate PASS); refusal-token tests for arm-preflight-before-nonce-burn; child-exit watcher tests patching the spawn SEAM (`daemon.tools.upgrade_journal.spawn_executor`), NEVER `subprocess.Popen` | P1-I1, I2, I3 (pack layer) | pack header `:6-30`; `:28-29` (still-stripped side) |
| `test/packs/upgrade_registration_unit_test.sh` | NO extension (registration unchanged) | — | — |
| `test/packs/release_journal_unit_test.sh` | NO change to wrapper (transparent); new test cases ride via inner suite | P2-T4+T5 | `:36` (existing timeout 120s wrapper) |
| `scripts/upgrade/stage.sh` | REPLACE `:172-180` with guarded-default branch; EXTEND `:381` log line with `source=$ROLLBACK_SAFE_SRC`; ADD fence comment block at top of new branch naming 3 GNU-debt sites OUT OF SCOPE | P2-T1, T2, T7 | `stage.sh:163-180`, `:277-293` (manifest write unchanged), `:381` (log) |
| `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` | INSERT `## ADR-035` section after ADR-032 (:213), before Pre-Freeze Assumption-Closure Checklist (:215); APPEND row to Decision Index table (:288-308) | P2-T3 | `:213-215`, `:27-36` (ADR-017 template), `:175-181` (ADR-029 style), `:288-308` |

NO CHANGES to: `promote.sh` (argv case `:72-77` already handles `--f2-verified-closed`; unknown-flag trap `:79` stays dormant per `research-findings-python.md` §5(D) "no promote.sh edit needed for the flag itself"); `lib.sh` (F2 note logger `:584-589` already first-class; GNU-debt sites `:84-89`, `:706-712`, `:1238-1295` FENCE OUT); `rollback.sh:98-101`; `lib.sh:1446-1449` (adopt_stale_txn sweep guard); `launcher.sh:783-797` (journal sweep guard); `status.sh:88-90` (read-only); `daemon/tools/upgrade_tools.py:2286-2293` + `:1345-1370` (Python tool-layer manifest entry-time gates — read field, unchanged); `EXECUTOR_ENV_ALLOWLIST` `:988-991` (no widening; extras passthrough via `:1003-1004` is the seam); any job-source anti-forgery code (`job_queue.py:1256-1257` / `:1993` — drifted anchors, OUT OF SCOPE per `phase1-plan.md:42`).

---

## 4. Item-7 Decision Table (verbatim from `phase1-plan.md:232-237`)

| Q | Verdict | Rationale |
|---|---------|-----------|
| **Q1.** Does merge 4db90d74 (registry-backed `classify_user_origin` at `upgrade_journal.py:1130-1202`) close F2? | **NO** | The unauth loopback forge lane remains open: `jobs_crud.py:275-278` (POST /jobs body.source pass-through) and `messages.py:391` (source="api" stamp). ADR-032's textual disclaimer (upgrade_journal.py:1060-1073) is explicit: anti-forgery structural at the stamp site does NOT close F2 loopback forge. (Per `research-findings-shell-e2e.md` EXTRA ASK 1 (i).) |
| **Q2.** Is the live rung otherwise policy-gated for live rung promotes (user-executed-only, PRE-LIVE checklist, ADR-017)? | **YES** | Runbook §9 (per `research-findings-shell-e2e.md` EXTRA ASK 1): "the live rung remains USER-EXECUTED — automation stops at demo permanently (ADR-017); promotion-ladder.md S4–S6 are USER rows." Four-part PRE-LIVE checklist = runbook §8.4(ii) :484 + §9 ledger + f2 gate + user-executed. |
| **User ratification 2026-09-26 (TOOL LANE ONLY):** Does the 3-factor-verified arm (user-confirmed + nonce via registered chat source + nonce/action binding) constitute F2-equivalent attestation for the tool-lane live promote? | **YES — RATIFIED** | User nonce-echo via registered source = accepted attestation; the 3-factor arm ceremony itself = the attestation source; NO additional auth layer; rationale "feature-first, revisit if threat model changes." (Per `research-findings-shell-e2e.md` EXTRA ASK 1 (ii) + project_history 2026-09-26 + critical note "POLICY RATIFIED".) |
| **[COLLAPSED — superseded by user ratification 2026-09-26]** Alternative: add an extra auth layer (HMAC, signed challenge, or extra factor) on the tool-lane live promote arm. | **COLLAPSED** | Superseded by the user ratification above. Rationale preserved: feature-first; revisit if threat model changes. ADR-017 tool-lane enforcement posture is superseded FOR VERIFIED ARMS ONLY. |

---

## 5. Cross-Cutting Test Matrix

### 5a. Stays-green / unverified-side pins (P1 side; verified side lands as sibling tests in §5c)

| Test site | Pin style | Unverified side (TODAY) | Verified side (POST) | Action |
|-----------|-----------|-------------------------|----------------------|--------|
| `test_upgrade_tools.py:3388-3391` | argv exact-list equality (UNVERIFIED side) | `["bash", promote.sh, env, "--version", "1.2.3"]` (no F2 flag — UNVERIFIED baseline) | (verified side lands as new sibling test in §5c) | **STAY GREEN — ZERO existing pins flip**. This test IS the unverified side; verified behavior is asserted by `test_promote_argv_gains_f2_flag_when_verified` in §5c. |
| `test_upgrade_tools.py:3439-3444` | allowed-set subset (UNVERIFIED side) | `{allowlist} ∪ {INSTALL_DIR, PORT} ∪ PG-prefix` (NO extras) | (verified side lands as new sibling test in §5c) | **STAY GREEN — ZERO existing pins flip**. Verified subset is asserted by `test_extra_env_gains_live_and_note_when_verified` in §5c. |
| `test_upgrade_journal.py:902` | extras set (UNVERIFIED side / allowlist purity) | `{RUN_ID}` only | (verified side lands as new sibling test in §5c) | **STAY GREEN — ZERO existing pins flip**. Purity test pins allowlist structural containment only; the `F2_VERIFIED_NOTE` extra is asserted separately. |

### 5b. Stays-green (must remain unbroken; both sides)

| Test site | Pin style | What | Action |
|-----------|-----------|------|--------|
| `test_upgrade_journal.py:935 / :962` | poison strip | `setenv ENSEMBLE_UPGRADE_LIVE=1` → child env must NOT have it | STAYS GREEN (unverified path still strips ambient) |
| `test_upgrade_tools.py:3423-3434` | poison strip | `setenv ENSEMBLE_UPGRADE_LIVE=1` → composed-exclusion | STAYS GREEN |
| `test_release_journal.sh:188 / :200` | string-contains | `--f2-verified-closed` refusal/proceed text | STAYS GREEN (promote gate text unchanged — flag still honored, refusal text unchanged) |
| `test_release_journal.sh:297-301` | field presence | `manifest has rollback_safe` | STAYS GREEN (rider changes VALUE derivation, not field existence; consumer-gate at `:821-826` unaffected) |
| Sole `spawn_executor` caller pin | AST + grep | `daemon.manager.drain_pending_system_execution` is the SOLE production caller of `_uj.spawn_executor`; no second caller introduced by Item 2 (Item 2 publishes events through the Item-4 sweep service) | **NEW PIN** — assertion lands in this mission via the upstream-only-caller AST/grep check (registered alongside the §5c sibling tests) |

### 5c. Sibling tests (P1, ~20 sibling tests; verified-side landings as STANDALONE sibling tests — NOT parametrize-over-existing per C3b)

**SIBLING-SHAPE RULE:** every verified-side test below is a new standalone pytest function. NO parametrize-over-existing of the §5a pins. Each sibling proves ONE additional behavioral claim about the verified path; the §5a unverified-pin continues to exist unchanged as the unverified baseline.

| Item | Sibling tests (verified side) |
|------|-------|
| 1 | `test_promote_argv_gains_f2_flag_when_verified` (sibling of `test_upgrade_tools.py:3388-3391`), `test_extra_env_gains_live_and_note_when_verified` (sibling of `:3439-3444`), `test_extra_env_poison_stripped_when_unverified` |
| 2 | `test_reaper_journals_exit_code_on_child_exit_78`, `test_reaper_benign_detach_at_timeout_journals_executor_still_running` (C2 timeout), `test_reaper_does_not_kill_child_at_timeout` |
| 3 | `test_arm_preflight_refuses_unresolvable_scripts`, `test_arm_preflight_refuses_unconstructable_argv` |
| 4 | `test_boot_sweep_clears_stale_pending_op`, `test_periodic_sweep_skips_live_executor`, `test_pending_actions_gc_prunes_expired_unconsumed` |
| 5 | `test_terminal_outcome_filters_nonce_consumed`, `test_terminal_outcome_returns_none_when_armed`, `test_outcome_label_nonce_consumed_awaiting` |

All patch `daemon.tools.upgrade_journal.spawn_executor` (NEVER `subprocess.Popen` per P2.2 testing gotcha). Item 2 sibling tests exercise the Item-4 sweep service's reaper worker — the mock/fake is the sweep service's queue, NOT an ad-hoc InstanceManager reaper.

### 5d. Per-item NEW tests (P2, 9a-9g; full bash at `phase2-plan.md:282-405`)

| ID | Case | What it pins |
|----|------|--------------|
| 9a | explicit `1` honored | rc=0; manifest `rollback_safe:true` |
| 9b | explicit `true` honored | rc=0; manifest `rollback_safe:true` |
| 9c | explicit `0` honored | rc=0; manifest `rollback_safe:false` |
| 9d | explicit `false` honored | rc=0; manifest `rollback_safe:false` |
| 9e | unset + DROP-detected → REFUSE exit 78 | rc=78; stderr contains BOTH override forms + "migration delta" + "v0.14.2" + "ADR-035"; NO manifest written |
| 9f | unset + no-DROP → quiet default `true` (IMPLEMENTED) | rc=0; manifest `rollback_safe:true`; runs against benign `FAKE_REPO` per `phase2-plan.md` §"new test cases" (test 9f block at :391-404); covers the rider's no-DROP branch (trivially-correct default-true) |
| 9g | **REGRESSION PIN: silent-false path DEAD** (v0.14.2 + v0.15.1 accident class) | rc=78 + NO manifest written; if rc=0 with `rollback_safe:false`, test FAILS (catches regression to pre-rider behavior) |

### 5e. Pack extensions

| Pack | Change |
|------|--------|
| `upgrade_tool_interlock_unit_test.sh` | Add verified-arm tests (fake marker + /tmp fixture per existing LIVE-gate PASS style); refusal-token tests for arm-preflight-before-nonce-burn; child-exit watcher tests (patch spawn seam). Stamp both sides of the two-sided contract. |
| `upgrade_registration_unit_test.sh` | NO extension (registration unchanged per `research-findings-shell-e2e.md` Q5). |
| `release_journal_unit_test.sh` | Wrapper unchanged (transparent, propagates inner suite exit); new test cases ride the inner `tests/test_release_journal.sh` invocation. |

### 5f. e2e: ZERO lane intersection per `ensure.md` rule (per-change classification, `phase1-plan.md:289-295`)

| Planned change | Lane modules touched? | Inside lane task execution? | Verdict |
|----------------|----------------------|---------------------------|---------|
| (i) manager.py executor spawn + async reaper | NO | ADJACENT (post-turn finally path, shielded) | N/A — outside lane |
| (ii) upgrade_tools.py boot-time + periodic reconcile sweep | NO | NO (timer/boot-driven loop, JobLockSweep-style) | N/A — outside lane |
| (iii) argv/env passthrough at spawn seam | NO | NO (same post-turn context) | N/A — outside lane |
| (iv) upgrade_journal.py pending_actions GC expansion | NO | NO (journal-file helpers only; callers are store/consume/sweep — none in lane modules) | N/A — outside lane |
| (v) _terminal_outcome class filter | NO | NO (pure fn refactor) | N/A — outside lane |
| (vi) `stage.sh` rider (P2) | NO (bash only; not Python) | NO (stage.sh is daemon-out-of-band) | N/A — outside lane |

**No full e2e release gate required** (per `phase1-plan.md:297`: "Per-change e2e verdict: N/A. Lane intersection empty by import audit"). However, **the boot-sweep registration (Item 4)** warrants shaped validation analogous to `lane_gate_boot_probe` (per `research-findings-shell-e2d.md` §Q3 Q4 lane-probe pack shape: scrubbed `./dev.sh` boot, 30s must-not-crash, dev-DB engine-line verification, teardown). Acceptance: dev boot proceeds without crash after sweep service starts; reconcile runs once at boot; periodic ticker ticks; pending_actions GC tick observable in log; reaper worker processes one enqueued (pid, run_id) within the 660s grace.

### 5g. Predicate + name-freeze invariants (M-11; per W2 §5 additions)

| # | Invariant | Pin | Consequence |
|---|-----------|-----|-------------|
| 1 | **M-11 — predicate is 5-conjunct** | `kind=="promote" AND nonce_consumed AND confirmed_by_human AND confirmed_source AND env=="live"` | The 5th conjunct (`env=="live"`) is the **safe-by-construction** addition over the previous 4-conjunct "safe-by-coincidence" — verified passthrough NEVER fires on demo/dev/sandbox even with nonce + source; falsifying rows are pinned by the truth-table test below |
| 2 | **`is_verified_arm` truth-table pin** (incl. `env=demo` falsifying rows) — citation: `phase1-plan.md` §"ITEM 1 — ENABLER (core)" / verified-arm predicate test_table (9-row coverage equivalent) | 9-row coverage-equivalent truth table covering all falsifying classes + canonical True row:<br>**False rows (falsifying classes — each conjunct absent or wrong env):**<br>1. `op=None` → False<br>2. `op.kind≠"promote"` (e.g. `kind="restart"`) → False<br>3. `op.nonce_consumed=False` → False<br>4. `op.confirmed_by_human=False` → False<br>5. `op.confirmed_source` is None / empty → False<br>6. `op.env=="demo"` with everything else True → False (M-11 5th conjunct)<br>7. `op.env=="sandbox"` with everything else True → False<br>8. 4-conjunct legacy (no `env=="live"` conjunct — pre-M-11 demo bypass hypothetical) → False<br>**True row (canonical — all 5 conjuncts present):**<br>9. `op.kind=="promote" AND nonce_consumed=True AND confirmed_by_human=True AND confirmed_source="<chat-source>" AND env=="live"` → True<br>Without the 5th conjunct (M-11), F2 forgers could craft an `env=demo` bypass; truth-table pins demote the predicate from coincidence to construction. Falsifying rows covered by integration tests; row 9 is the LIVE-rung positive case. |
| 3 | **Name-freeze on shared helper `_verified_arm_extras(op)`** | AST + grep pin: every verified-arm expansion (argv flag, env var, F2 note) routes through this single helper; rename requires a paired truth-table update | Prevents the "verified-arm predicate drift" risk (R-P1-1): one helper, one truth table, all call sites match |

The helper signature is `(op: PendingOp) -> tuple[list[str], dict[str, str]]` returning `(argv_extension, extra_env_extension)` — Items 1, 2, 3 (and any future verified-side code) MUST call this helper rather than rebuilding the predicate inline.

**Helper home (R7ii):** both `is_verified_arm` and `_verified_arm_extras` are **defined in `daemon/tools/upgrade_journal.py`** (sibling module). **Circular-import proof:** `manager.py` already imports `upgrade_journal` as `_uj` (`daemon/tools/upgrade_journal.py` is the canonical home for all upgrade journal/pen predicates); the helpers consume `PendingOp` only (no manager-side types), so the import is one-directional and acyclic. Test-side, `upgrade_journal` is imported normally by the existing unit suites — no fixture gymnastics needed.

---

## 6. Risks Roll-Up (merged from both phases, deduped, highest-severity + most-specific wins)

| # | Risk | Impact | Likelihood | Source | Mitigation |
|---|------|--------|------------|--------|------------|
| **R-P1-1** | Verified-arm predicate drift across Items 1/2/3 (shared helper `is_verified_arm`) | High | Medium | P1-R1 | Single shared helper (public name `is_verified_arm`; companion helper `_verified_arm_extras`); unit test covers all FIVE literal conjuncts (per M-11) |
| **R-P1-2** | Reaper task leaks under daemon shutdown — stale pending_op persists past cancel | Medium | Medium | P1-R2 | `task.cancel()` + CancelledError handler; shutdown seq `cancel THEN await`; child reaped by OS via `start_new_session=True` |
| **R-P1-3** | Boot sweep races arm — sweep clears a fresh arm if `expires_at + RECONCILE_GRACE_S` crossed mid-flight | High | Low | P1-R3 | Sweep liveness test uses **`os.kill(pid, 0)` + a time-bound predicate** (kill -0 fails on a non-existent pid AND is cheap; cross-platform acceptable per a fallback path on platforms where kill -0 semantics differ); the instance-match check (`op.armed_by_instance == current_instance_id`) is **downgraded to defense-in-depth** (instance ids go stale across reboots, so this is a sanity pin, not the safety gate); locked-safe per `upgrade_journal.py:925-927` |
| **R-P1-4** | pending_actions GC deletes an in-flight nonce that hasn't been consumed yet | High | Low | P1-R4 | Background sweep passes **`keep_run_id=None`**; safety is provided by the TTL+consumed logic in `_gc_pending_actions` (`:789-821`) — `consumed_at` rows are kept (audit), TTL-expired unconsumed rows are pruned only when the parsed expiry is provably past; `keep_run_id` exemption remains in the helper signature for any in-progress arm path that needs to spare a specific row |
| **R-P1-6** | `nonce_consumed` label rename breaks `status.sh` or external grep for `"live-confirmation nonce consumed"` | Medium | Medium | P1-R6 | Grep before commit; rename + grep-verify + update consumers in same commit (recommendation in §10) |
| **R-P1-7** | Reaper (now owned by Item-4 sweep service) benign-detach at 660s timeout keeps child running in its own process group (`start_new_session=True`); subsequent sweep ticks MUST refrain from clearing while the pid is alive | Medium | Low | P1-R7 (redesigned) | Reaper pattern follows `asyncio.wait_for(asyncio.shield(...))` from `manager.py:4148-4149` (FM-11-compliant); at timeout the worker journals `executor_still_running` (per C2) and returns cleanly — does NOT kill the child (kill would orphan it via process-group detachment); tests pin: (a) CancelledError handled cleanly per FM-11; (b) timeout path emits `executor_still_running` event and worker returns within bounded time; (c) subsequent periodic sweep tick still sees pid alive and skips reconciliation; (d) the only-`spawn_executor`-caller pin (§5b) ensures no parallel observation path |
| **R-P1-8** | `F2_VERIFIED_NOTE` could leak `confirmed_source` (chat-source id, NOT a secret — `research-findings-python.md` §6 "(no secrets… safe to embed)") | Low | Low | P1-R8 | Cite verbatim; note IS the audit trail; bounded `<source>:<run_id>` format |
| **R-P1-9** | P2 (shell/e2e) is NOT in scope — bundling validation (FL-23), 3 fresh ari cycles, runbook §8.4(ii)-(iii) obligations remain ops-side | High | High | P1-R9 | Out-of-scope fence; ADR-035 documents residual ops gates; integrate checklist into next live-rung promote runbook |
| **R-P1-10** | Daemon self-derivation to "live" on post-1b3f0795 build → future restart onto v0.15.3 could auto-promote onto LIVE rung if not yet user-confirmed | Medium | Low | P1-R10 | Verified-arm passthrough is per-CALL-SITE (Item 1); 3-factor gate `:2452-2620` refuses `user_confirmed=false` (Factor 1) regardless of self-derivation |
| **R-P1-11** | `r-20260926-100210-53ca` is a `pending_actions` (nonce registry) row, NOT a `pending_op` row — coder may conflate | Medium | Medium | P1-R11 | Cite `research-findings-python.md` §7 verbatim in task description; separate code path for each; test asserts both cleared by ONE sweep tick |
| **R-P2-1** | Existing CI pipelines call `stage.sh` without `ENSEMBLE_ROLLBACK_SAFE` set on repos with destructive DDL → refuse exit 78 (breaking for any pipeline relying on silent behavior) | Medium | High | P2-R1 | WARNING actionable + both override forms; refuse BEFORE release dir write (no half-staged payload); migration mechanical (`=0` for unsafe, `=1` for clean); v0.15.2 release notes / CHANGELOG names breaking change |
| **R-P2-2** | Rider is a strict mode change — operators who DO want `rollback_safe=false` (genuine schema-drift) must affirm `=0` explicitly | Medium | Medium | P2-R2 | Same mitigation as R-P2-1; WARNING text + ADR-035 name override legitimacy test |
| **R-P2-3** | ADR-035 supersedes a portion of ADR-017's tool-lane enforcement posture — future reviewer may reject feature-first posture | Medium | Low | P2-R3 | ADR-017 citation in ADR-035's consequence section is explicit; supersession narrowly scoped ("feature-first, revisit if threat model changes"); revisit criterion named |
| **R-P2-4** | RESOLVED (R6): 9f IMPLEMENTED — runs against benign `FAKE_REPO` per `phase2-plan.md:391-404`; liability retired (no longer an out-of-scope test debt) | — | — | P2-R4 (resolved) | Rider's no-DROP branch is trivially-correct default-true, NOW covered by 9f; 9a-d baseline + existing stage idempotency tests remain green |
| **R-P2-5** | Future commission closes F2 → ADR-035 revisit anchor; policy hook for stricter pass becomes stale if not revisited | Low | Low | P2-R5 | Consequence section names F2 closure as revisit criterion; supersession posture is "feature-first" precisely because F2 closure is uncertain |
| **R-P2-7** | GNU-debt fence misinterpreted as "this phase fixes BSD portability" → opportunistic uname-dispatch on lib.sh:84-89, :706-712, :1238-1295 | Medium | Low | P2-R7 | Triple-encode fence: (i) rider code comment block (Task 7), (ii) plan "Out of Scope" section with table (§8 below), (iii) ADR-035 consequence section |
| **R-M5-1** (R1) | `journal_history_append` `OSError` during reaper's executor-exit write (e.g. disk-full, ENOSPC, EROFS) raises per `upgrade_journal.py:276-283` — would surface to the reaper worker and trigger FM-11 violation if unhandled | Medium | Low | R1 | Wrap each executor-exit journal write in `try/except OSError` — log a WARNING carrying `run_id` + `pid` + exception repr; **NO retry** (retry risks unbounded I/O failure cascade; the journal event is observability-class, not lifecycle-critical); process continues; the boot-sweep path remains the durable recovery channel. Tests pin: (a) injected `OSError(ENOSPC)` from `journal_history_append` produces a `log.warning` with the right fields and the worker returns cleanly; (b) the periodic sweep still ticks after a write failure. |
| **R-M5-2** (R2) | Orphaned-executor scenario: drain reaches spawn site but `_uj.read_pending_op(install_dir)` returns `op is None` (journal write lost, or external operator edit between arm and drain) — Item 1's pre-spawn check would be vacuous, leaving an untracked child | Medium | Low | R2 | Pre-spawn refusal at `phase1-plan.md` §"ITEM 1 — ENABLER (core)" :85 region (and `manager.py:3823` vicinity): if `op is None` at the spawn seam → emit `executor_orphaned` event (`{spec_kind, install_dir, ts}`) + refuse to spawn (return ARMED-with-warning); operator investigates via upgrade.log + journal history; **2 lines of code**, no new tests required beyond a fixture that mocks `_uj.read_pending_op` to return None and asserts the orphan event + the no-spawn outcome |
| **R-M5-3** (R3) | **ACCEPTED-DECLINE** — synthetic `executor_exit` re-emit from any source other than the live-path reaper worker | — | — | R3 (declined) | Per `phase1-plan.md` §"ITEM 2 — LOUD EXIT JOURNALING" / daemon-restart durability note (the reaper queue is in-memory; child reaped by OS via `start_new_session=True`; the live-path `executor_exit` event from the reaper worker is the authoritative observability channel): **DECLINED**. Synthetic re-emit of `executor_exit` (e.g., at boot sweep when reaping a residual journal reference) ADDS duplication risk — the boot sweep already journals `executor_still_running` if needed, and a synthetic re-emit could be misread as "this child exited NOW" rather than "this child was observed ended at boot time." Single source of truth = live-path reaper worker only. No code change; this row is the audit trail. |

**Deduped:** No duplicate risks across phases. P1-R5 (job-source anti-forgery drift awareness, OUT OF SCOPE) merged into §8 Out of Scope fences, not surfaced here. P2-R6 (multi-line `_warn` style review) downgraded — style nit, no functional impact. R-M5-1/R-M5-2/R-M5-3 are R-round mechanical additions (cross-phase / hygiene); R-M5-3 is ACCEPTED-DECLINE per R3 directive.

---

## 7. Out of Scope (explicit)

### 7a. CODE: fenced out for this mission

1. **P2.1 GNU-debt sites** — `lib.sh:84-89` `_iso_to_epoch` (BSD `date -ju -f …`); `lib.sh:706-712` cooldown arm (BSD `date -v…` order); `lib.sh:1238-1295` retention eviction (`stat -f '%m'` garbles release-name var). Fix family = uname-dispatch (like `atomic_flip` at `lib.sh:1147-1171`); **backlog, not this mission.** Triple-encoded fence: (i) rider code comment at `stage.sh` new derivation branch top, (ii) §7a table here, (iii) ADR-035 consequence section. See `phase2-plan.md:181-191`.
2. **F2 unauth-loopback forge closure** — `daemon/routers/jobs_crud.py:275-278` POST /jobs body.source verbatim pass-through; `daemon/routers/messages.py:391` `source="api"` stamp. Corroborated `daemon/tools/upgrade_journal.py:1060-1073`. **Separate commission.** ADR-035 names this as the **revisit anchor** for the user-ratified supersession, not the fix here.
3. **Any promote/live touch during this mission** — no live touches; live stale `r-20260926-100210-53ca` clears itself once v0.15.3 runs on live.
4. **FE work** — no FE changes; tool surface unchanged.
5. **Manifest schema changes** — field name `rollback_safe` + JSON shape (literal boolean) + consumer field-extraction (`manifest_field` lib.sh helper) all unchanged. Only the value derivation moves (P2 rider).
6. **`ENSEMBLE_ROLLBACK_SAFE` enum extension** — today's `1|true|0|false|""|unset` set preserved; no new tokens (`delta-scoped`, etc.).
7. **Python consumer changes for `rollback_safe`** — `daemon/tools/upgrade_tools.py:2286-2293` + `_target_release_state:1345-1370` keep reading the manifest's `rollback_safe` field as-is. Rider changes the **derivation** (how the field gets written), NOT the **gates** (how it's read).
8. **promote.sh argv case edits** — flag already handled at `:72-77`; unknown-flag trap `:79` stays dormant (no edit per `research-findings-python.md` §5(D)).
9. **`EXECUTOR_ENV_ALLOWLIST` widening** — allowlist `:988-991` stays exact; verified-arm passthrough rides extras merge at `:1003-1004`.
10. **`job_source` anti-forgery** — `job_queue.py:1256-1257` / `:1993` (callers' `:526-538` is stale; drifted anchors per `research-findings-python.md` §2d) — separate commission.

### 7b. RESIDUAL OPS GATES (out of CODE scope; operator obligations before live eligibility)

| Gate | Spec citation | Mechanic |
|------|---------------|----------|
| **Scripts-bundling validation (FL-23)** | `docs/runbooks/upgrade-drills.md` §8.4(i) :482 + §8.4(ii) :484 items (1)-(2) | `stage.sh` bundles `scripts/upgrade/` → `releases/<ver>/scripts/upgrade/`; resolution order env override → release-local bundle → repo (dev only); one script-driven demo promote validates bundled path e2e |
| **3 fresh ari cycles on bundled release** | `docs/runbooks/upgrade-drills.md` §8.4(ii)(3) :484; §8.4(iii) :486 provenance | Cycles #1-#3 ran with disclosed `ENSEMBLE_UPGRADE_SCRIPTS_DIR` provisioning; `.env` bit-exact restored after each (cmp + md5 proofs B7 §2 / B8a §2); the qualifying cycles must run on the mechanism being certified |
| **`ledger_check.py` does NOT verify bundling** | `scripts/upgrade/ledger_check.py:277-296` `gate()`; `:230-274` `classify()` | `f2_state="open"` → BLOCKED BEFORE cycle-count; count ≥ 3 (ADR-021) → ELIGIBLE. **Bundling + cycle-mechanism provenance are runbook/evidence obligations (§8.4(ii)-(iii), decisions.md Gate Rulings & Fences items 1-7), NOT script-enforced.** |
| **`--f2-verified-closed` + user-executed-only** | `scripts/upgrade/promote.sh:91/103` per runbook :499; `f2-not-verified` token + `journal_mark_f2_verified` stamp in `lib.sh:584-591` | In-code but operator-executed factors; `ENSEMBLE_UPGRADE_LIVE=1` guard remains separate, still-required factor; flag additive, NOT replacement; live rung remains USER-EXECUTED (ADR-017, promotion-ladder.md S4-S6 are USER rows) |

**Partial coverage caveat:** tester dev/demo e2e exercises parts of the mechanism; the runbook requires the cycles ON the bundled release specifically. Full text in `phase2-plan.md:234-264`.

---

## 8. CROSS-PHASE RESOLUTIONS (this plan adjudicates; both phase plans must cite)

### 8a. ADR-035 minting ownership collision

**Collision:** W1's dependency table (per `phase1-plan.md` §"Dependencies on P2" (:498-508); now resolved at `:375` — P2 owns the mint) says "P1 mints ADR-035"; W2's task T3 (`phase2-plan.md:33`) says P2 mints it.

**Resolution: P2 owns the mint.** Rationale:
1. The user addendum placed the ADR documentation task with the rider (P2 T3: "Insert as `## ADR-035 (minted 2026-09-26, P2.5 tool-lane-fix): …`"); the doc task is co-located with the shell-side change that names the rider.
2. `phase2-plan.md` §"ADR-035 Draft Text" (~193-234) drafts the FULL ADR-035 text — Context. → Options. → Decision. → Recommended default / If the user picks otherwise. → Consequence. — ready to paste. P1's task description only references the mint as an exit-criterion item.
3. P1's exit criterion text (`phase1-plan.md:394`) softens the mint to "ADR-035 minted" without prescribing the mintage site or content; the gate is "policy hook exists at the right citation point," not "P1 adds it."
4. ADR-035 is a policy posture document about BOTH halves (verifier-verdict + user-ratified supersession); P2's rider text needs to cite ADR-035 in the WARNING (`phase2-plan.md:144-146`), so P2's coin flips first naturally — the WARNING itself is the first visible artifact.

**Action items:**
- **P2** mints ADR-035 per `phase2-plan.md` §"ADR-035 Draft Text" (~193-234) draft. Decision Index row appended per `phase2-plan.md` §"ADR-035 Draft Text" Decision-Index-row sub-block (~:228-233). Insertion point: after ADR-032 block (`decisions.md:213`), before Pre-Freeze Assumption-Closure Checklist (`:215`).
- **P1**'s exit criterion (`:394`) cites the P2 mint; no additional P1 ADR work. P1's Item 7 decision table (`phase1-plan.md:230-244`) is the POLICY RECORD portion — it cites ADR-035 as the supersession hook, not mints it.

### 8b. 53ca row-type discrepancy

**Conflict:** E1 (`research-findings-python.md` §7 + `phase1-plan.md:333` R-11) adjudicates `r-20260926-100210-53ca` as a `pending_actions` entry (nonce registry row, `upgrade_journal.py:756+`); E2's pass labels it a `pending_op` row (armed-op dataclass `:695-729`).

**Resolution: implementation-time verification item.** Both mechanisms (pending_op reconcile sweep + pending_actions GC) are IN SCOPE for Item 4, so clearance is covered EITHER way. Mark as:
1. **Inspection trigger:** When v0.15.3 runs on live, operator inspects `$INSTALL_DIR/releases/state.json` once to confirm which field the row actually lives under. The `pending_op` is the **single field** keyed at root; `pending_actions` is the **map** keyed by run_id. One `jq` query disambiguates: `jq 'if .pending_op then "pending_op" elif .pending_actions["r-20260926-100210-53ca"] then "pending_actions" else "neither" end' releases/state.json`.
2. **No live touch during this mission** — per task directive and `phase1-plan.md:371`. The row clears itself once v0.15.3 boots and the new sweep runs.
3. **Both code paths in Item 4** — `gc_pending_actions(keep_run_id)` (extension, `:789-821`) + periodic `reconcile_pending_op` (calls original `:909-980`) — together handle the family. The 53ca row's KB note ("inert, expired ⇒ refusal path `nonce-expired`/expiry branch handles it at next touch") means even without Item 4 the row is harmless on next touch; Item 4 makes clearance automatic instead of opportunistic.
4. **Operator instruction (live cut):** **Reboot the live daemon onto the v0.15.3 build after staging** — the row clears on the first reconcile sweep tick (≤ 90s after boot, per the sweep service interval knob). Operators are NOT required to manually inspect or delete the row. Confidence: the row is expired/inert per the KB note, so the only failure mode is "row persists one extra sweep interval after live promote" — harmless.

---

## 9. Open Questions (carried verbatim from `phase1-plan.md:379-383` with recommendations)

**Defaults apply unless caller overrides before implementation dispatch.**

1. **Refusal token for preflight.** Use existing `pipeline-busy` or mint a new `arm-preflight-refused:<token>` family? The preflight BLOCKS the arm before burn; the caller has not yet armed. **Recommendation: mint a new `arm-preflight-refused:<sub-token>` family; pin each sub-token individually in tests** (3 tokens: `preflight-scripts-unresolvable`, `preflight-argv-unconstructable`, `preflight-argv-malformed` per `phase1-plan.md:182-184`). New token is more honest than overloading `pipeline-busy`.
2. **`nonce_consumed` label rename scope.** Rename `"live-confirmation nonce consumed"` → `"awaiting executor (pending)"` everywhere, or additive (keep old, add new with deprecation)? **Recommendation: rename + grep-verify + update consumers in the same commit.** Grep `status.sh:88-90` and any external script that consumes this string before commit; update in lockstep. Additive mapping adds a long-tail deprecation with no payoff.
3. **Reaper timeout value.** What is the expected upper bound for a live promote? Demo observed 30-60s. Live could be longer. **Default changes to 660s + benign-detach `executor_still_running`**: at timeout the reaper does NOT kill or fail — it detaches benignly and journals `executor_still_running`. Floor math: `livez` 60 + `readyz` 120 + soak 300 + overhead ≈ 490s minimum → 660s. **ServicesConfig knob stays** (`ServicesConfig.upgrade_journal_reaper_timeout_seconds Field(ge=60)` for operator override; unify with phase1's 4 sites — floor rejects <60s as nonsensical).
4. **Sweep service interval.** Match `job_lock_sweep_interval_seconds` (90s default) or different? **Recommendation: 90s default, own knob `upgrade_journal_sweep_interval_seconds` for operator override.**
5. **Boot-time sweep placement.** In `api.py` lifespan BEFORE or AFTER `_boot_db_preflight()` (DR-1 seam at `daemon/__main__.py:104`)? **Recommendation: AFTER preflight + AFTER app.state construction but BEFORE first request.** DB-backed reads must be safe; sweep service starts once its dependencies are wired.

---

## 10. v0.15.3 Release-Cut Notes

**Giter (dedicated instance, idle until mission end) executes the following:**

### 10a. PRE-COMMIT the 13 pre-existing `.agents/` evidence notes FIRST

**Why:** `scripts/bump_version.py:121` runs `git add -A` which sweeps `.agents/*` scratch into the bump commit. Goal: the v0.15.3 bump commit carries ONLY v0.15.3 files. **User flagged this explicitly 2026-09-26 — do not surprise user with a sweep.**

**The 13 paths** (per the meta KV directive, verified at branch creation):
- **2 MODIFIED:** `.agents/tester/MOCK_TESTS.md` (+12), `.agents/tidier/notes.md` (+7)
- **11 UNTRACKED:** 8 in `tester/RESULTS/` (2026-09-22..2026-09-26), 2 in `charter/memories/` (mermaid), 1 in `shared/` (e2ejw preflight)

**Commit shape:** single dedicated evidence commit, e.g. `chore: import 2026-09-22..26 .agents evidence notes`. All 13 paths staged explicitly (NOT `git add -A`). Commit message cites the user-flag scope so future reviewers can grep it.

### 10b. BUMP commit (only after 10a is merged into local)

Run `python scripts/bump_version.py v0.15.3` per the established bump flow. Verify the resulting bump commit's diff: ONLY `pyproject.toml` + `__init__.py` + version-bearing manifests; zero `.agents/*` files touched (those live in 10a's commit, which is an ancestor).

### 10c. Release line hygiene

- **Base:** `latest @ 139ba352` (v0.15.2); local `latest == origin/latest` (0/0 divergence at branch creation).
- **Merge:** `feature/upgrade-tool-lane-fix` → `latest` (integrator's call for order; P1 and P2 parallel with zero file overlap — either lands first).
- **NEVER touch master** (DEAD since v0.3.3; merge-base 997c670d, 3,609 behind).
- **Tag:** `v0.15.3` annotated, pushed & verified per the established flow.
- **Release point:** `releases/v0.15.3/` built from tag `v0.15.3` per stage.sh; pre-stage `rm -f dist/ensemble-prod` (stale-dist trap armed AGAIN — see critical note v0.15.2 staging). Rollback net, manifest invariants, journal anchored per the P2.1 substrate.
- **Stage command with explicit `ENSEMBLE_ROLLBACK_SAFE=1`:** invoke as `ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh <env> --version v0.15.3 --skip-build` (or with a real build). **Legitimacy rationale:** the v0.15.2→v0.15.3 migration delta is EMPTY — this mission adds zero migrations; the full-history DROP grep in stage.sh is irrelevant to this delta and would otherwise refuse exit 78. Override is legitimate, NOT a bypass. ADR-035 documents this as the override-legitimacy pattern (WARNING text + rider design).

---

## 11. Constraints

### 11a. Format compatibility

- **Journal format compatibility** — `journal_history_append` (`upgrade_journal.py:301-316`) is **additive**. New event class `"executor_exit"` follows the `:380-383` "emit-NOTHING ordinary event" comment style — NOT added to `_TERMINAL_EVENTS` (lifecycle vs observability separation; per `phase1-plan.md:153`). Python twin asserts semantically (per blueprint; shell parsers tolerate hand-edit divergence per ADR-034).
- **Manifest format compatibility** — P2 rider writes a literal JSON boolean into `manifest.json:284`; source tag `ROLLBACK_SAFE_SRC` captured for stage log line only, NEVER enters manifest (manifest schema unchanged).

### 11b. Language/runtime conventions

- **py3.13 patterns** — `dict[str, str]` (PEP 585), `str | None` (PEP 604), `tuple[...]` generics. All in current tree (research verified at `upgrade_journal.py:994`, `upgrade_tools.py:2271`, `manager.py:3795`).
- **PEP 735 dev deps** — plain `uv sync` INCLUDES dev deps by default. **DO NOT** use `uv sync --extra dev` (obsolete; extra no longer exists per critical note).
- **BSD portability for P2 only** — P1 is Python-only (no shell touches). P2 follows lib.sh house patterns: chained-`local` separation (`lib.sh:1148-1149`, `:1251-1252`); no `set -f` (`:39-44`); no new uname-dispatch; no `flock(1)` (`:37`); no `date -v` operands (BSD order); no `mv -hf` calls (rider is read-only against filesystem after manifest write).

### 11c. Security invariants preserved (P1 only; full table `phase1-plan.md:303-314`)

| Invariant | Anchor | Preserved by |
|-----------|--------|--------------|
| Job-source anti-forgery | `job_queue.py:1256-1257 / :1993` | OUT OF SCOPE — no change |
| Registry-backed user-origin classification | `classify_user_origin` `:1130-1202` (merge 4db90d74) | NO change to classification; verified-arm reads `op.confirmed_source` (already validated at arm time via Factor 2 at `:2465-2512`) |
| Nonce instance/action binding | `upgrade_tools.py:2514-2620` (factor 3 + nonce validation) | NO change |
| `pending_op` as durable serializer | `PendingOp` `:695-729`, `write_pending_op` `:732-737` | Verified-arm predicate reads from `pending_op` literal fields (`:2701-2704` write; `:3832` drain read); no new path bypasses `pending_op` |
| Live restart refusal | `upgrade_tools.py` (env-self-match fail-closed) | UNCHANGED — verified-arm passthrough applies to promote, NOT to restart; restart argv `:3801-3807` untouched |
| `EXECUTOR_ENV_ALLOWLIST` purity | `:988-991` | **NO widening**; extras passthrough `:1003-1004` is the seam |
| Fence: ambient env never reaches child for unverified arms | `executor_env` `:994-1005` | UNCHANGED — verified-arm passthrough is per-call-site, NOT ambient |
| Fence: child exit path stays in `upgrade.log` | `spawn_executor` `:1024-1030` parent-creates file before Popen | UNCHANGED — reaper reads the same file, no new write path |
| Two-sided test contract | argv + allowed-set equality pins | Both sides pinned; ambient-strip side stays green |

**NO new path** where an unverified context reaches the child env or argv flag.

---

## 12. Cross-References (navigability)

> **Note:** Phase-plan line refs are written as section anchors (not exact line numbers) since the phase plans are receiving their own remediation edits in parallel; research-file refs use §-anchors (stable). Cross-walk this table against the current phase plan if any section is renamed.

| Topic | Where to read (section anchor) |
|-------|---------------|
| Two-sided contract matrix | `phase1-plan.md` §"Test Plan (two-sided contract matrix)" |
| ~20 sibling tests (P1; per C3b sibling-shape) | `phase1-plan.md` §"Item-Level Design" / per-item test plans |
| Per-change lane classification | `phase1-plan.md` §"Phases" + §"Lane intersection" |
| P1 security invariants table | `phase1-plan.md` §"Security Invariants" |
| P1 internal sequencing rationale | `phase1-plan.md` §"Internal Sequencing" |
| 9a-9g rider test bash fixtures | `phase2-plan.md` §"New test cases in `tests/test_release_journal.sh`" |
| ADR-035 draft text (paste-ready) | `phase2-plan.md` §"ADR-035 Draft Text" |
| Stage.sh rider implementation (BSD-safe) | `phase2-plan.md` §"Stage.sh Rider Design" / §"Implementation" |
| WARNING text (lib.sh multi-line pattern) | `phase2-plan.md` §"WARNING Text Draft" |
| Residual Ops Gates (full text) | `phase2-plan.md` §"Residual Ops Gates" |
| Drift report (caller anchor → current line) | `research-findings-python.md` §4 |
| Verifier-verified mechanics (5/5 PASS) | `research-findings-python.md` §5 |
| `F2_VERIFIED_NOTE` site survey | `research-findings-python.md` §6 |
| Stale row `r-20260926-100210-53ca` adjudication | `research-findings-python.md` §7 |
| ADR series numbering highest-minted | `research-findings-python.md` §8 |
| Consolidated fix-anchor list | `research-findings-python.md` §9 |
| Test-pin inventory (per-file exact anchors) | `research-findings-python.md` §3 |
| Q4 pack extensions design | `research-findings-shell-e2e.md` §Q4 |
| ADR numbering + insertion point | `research-findings-shell-e2e.md` EXTRA ASK 1 |
| Pending_actions GC current state | `research-findings-shell-e2e.md` EXTRA ASK 2 |
| E2E sizing (lane rule) | `research-findings-shell-e2e.md` §Q3 |
| Boot-time + periodic sweep pattern | `research-findings-shell-e2e.md` §6(a) |
| asyncio subprocess patterns | `research-findings-shell-e2e.md` §6(b) |
| Stage.sh rider design history | `research-findings-shell-e2e.md` §Q1 |
| Verifier-verdict fold-in B (residual ops gates) | `research-findings-shell-e2e.md` VERIFIER-VERDICT FOLD-IN B |
| Existing periodic GC sweep interval | `research-findings-shell-e2e.md` EXTRA ASK 4 |

---

**END OF plan.md.** Integrator merges P1 + P2 into the v0.15.3 commit batch; giter follows §10 release-cut sequence (10a evidence commit FIRST, then 10b bump, then 10c hygiene). Subsequent W3 produces the `plan-overview.md` absorbing P2's Residual Ops Gates section.
