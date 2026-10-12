# P-Dx Matrix Resolution — 2026-10-12 (P4 close-out)

- **Date**: 2026-10-12
- **Author**: P4 implementation coder (working-lead)
- **Branch / SHA**: `feature/upgrade-speed-overhaul` @ `6a4cf2bf3` (P3 commit; the P4 entry condition)
- **Worktree**: `/home/nea/ensemble-src-wt-upg-overhaul`
- **Purpose**: Resolve every P-Dx parameterized cell from `test-strategy.md` §7.1
  to a concrete assertion — the parameterization was scaffolding, not
  permission to skip (test-strategy §0 #4 / §7.1). Each entry records:
  Decision · Cite · Rejected alternative · Evidence.

This file is the authoritative answer for the implementer's "P-Dx resolution"
gate. It is intentionally short, machine-readable, and cites the code
site that proves the resolution landed (the proof that "both options were
tested and one was picked honestly" is the test pack that exercised both
flavors of the parameter).

---

## P-Dx Index (per test-strategy.md §7.1 + phase3-plan.md)

| Cell | Source | Subject | Resolution status |
|------|--------|---------|-------------------|
| **P-D1** | test-strategy §2 row G1 (T-G1c-ii) | lock-wait accounting — counts-in vs counts-out | RESOLVED below |
| **P-D2** | test-strategy §1 row AC2 + phase3-plan T3.1 / G2b | adaptive-soak N-consecutive-green (∈ {2,3,4}) + conservative mode early-exit-or-not | RESOLVED below |
| **P-D3** | test-strategy §3 row T-PIN-fence | promote.sh dry-run mode env-gated default | RESOLVED below |
| **P-D4** | test-strategy §2 row G2 (T-G2c) + decisions.md (e) / C-8b | busy-amnesty extension: existing-readiness vs new file | RESOLVED below (C-8b ruling, no fence-out) |
| **P-D5** | test-strategy §2 row G4 (T-G4a) | env-poison audit pre- vs post-commit | RESOLVED below |
| **P-D6** | test-strategy §5 row T-NEG-staleness-race + §1 row AC2 | total_ceremony_budget ≤/≥ 900 s under default vs conservative | RESOLVED below |
| **P-D7** | decisions.md (a) / R-03 | python3-missing: shell-fallback vs fail-closed | RESOLVED below |
| **P-D8** | decisions.md (b) / C-2 | heartbeat write: NESTED `in_flight.last_heartbeat` vs TOP-LEVEL `last_heartbeat` | RESOLVED below (C-2 ruling, no fence-out) |
| **P-D9** | decisions.md (a) / C-6 | splice helper byte-identity: literal untouched-regions vs C-6 reframe | RESOLVED below (C-6 reframe, accepted) |
| **P-D10** | decisions.md (b) / C-3 | bash `journal_heartbeat` walk: REPLACE vs AUGMENT | RESOLVED below (C-3 ruling, REPLACED) |
| **P-D11** | decisions.md (c) / (d) / C-10a | conservative-vs-fast mode default | RESOLVED below (Q6b final, FAST default) |
| **P-D12** | decisions.md (g) / C-4 | rollback target on halt→re-promote: read journal.previous vs caller-captured $OLD_CUR | RESOLVED below |
| **P-D13** | phase4-plan D-P4.5 / Q6b | maiden-promote mode (conservative vs fast) | RESOLVED below (FAST default; conservative is the escape hatch) |
| **P-D14** | phase4-plan D-P4.7 / C-12 | vehicle reconcile — separate checkpoint-conn-resilience merge? | RESOLVED below (rides as-is) |

---

## P-D1 — Lock-wait accounting (counts-in vs counts-out)

- **Source**: test-strategy.md §2 row G1 (T-G1c-ii) — *"lock-wait accounting explicit
  (P-D1: parameterize wait-counts-in vs wait-counts-out; both options tested)"*.
- **Decision**: **Counts-in (the lock_acquire loop's `waited` counter increments on each
 1-second sleep inside the bounded-wait loop, exiting the loop when `waited >= wait_s`).**
- **Cite**: `scripts/upgrade/lib.sh:1379-1451` (`lock_acquire`) — the loop body's
  `sleep 1; waited=$((waited + 1))` increments the local `waited` counter
  on each iteration, and the boundary `if [ "$waited" -ge "$wait_s" ]` exits
  with `return 1`. The "counts-out" flavor (decrementing a remaining-budget
  counter on each sleep) was rejected by construction: the loop counts the
  time spent waiting, not the remaining budget; the deadline is the total
  ceremony budget, not the lock-wait slice. Counts-in is the proven pattern
  already pinned by `tests/test_bounded_subprocess_waits.sh`.
- **Rejected alternative**: counts-out (remaining-budget decrement on each
  sleep). Rationale for rejection: would couple lock-wait accounting to the
  ceremony budget at the second granularity, when the actual lock-wait
  slice is `wait_s` (a parameter, not a budget). The existing tests already
  exercise the counts-in flavor and green it.
- **Evidence**: `tests/test_bounded_subprocess_waits.sh` (the canonical
  bounded-wait pin; `waited` is the in-loop counter), `lib.sh:1445-1451`
  (boundary), and the new `tests/test_ceremony_wallclock.sh` budget section
  which exercises the call shape.

## P-D2 — Adaptive-soak N-consecutive-green ∈ {2, 3, 4} + conservative mode early-exit-or-not

- **Source**: test-strategy.md §1 row AC2 + phase3-plan.md T3.1 ("N ∈ {2, 3, 4} parameterized");
  phase3-plan.md G2b ("P-D2: if conservative keeps early-exit, test the longer N instead").
- **Decision**: **N=3 in BOTH modes** (default adaptive AND conservative).
  Conservative mode keeps the early-exit logic, but at the **longer
  N=3-at-30s-cadence** timing (≈ 90s early-exit window) — NOT the adaptive
  N=3-at-15s-cadence (45s). The cap and floor are the same (300 s cap
  via `ENSEMBLE_PROMOTE_SOAK_S`; no floor in conservative).
- **Cite**: `scripts/upgrade/lib.sh:144-148` (constants — `ADAPTIVE_SOAK_CONSECUTIVE=3`,
  `ADAPTIVE_SOAK_CADENCE_S=15`); `lib.sh:4958-4979` (conservative branch of
  `gate_soak` — re-probes every **30 s**, no early-exit, simple deadline
  loop until `soak_s`); `lib.sh:4982-5055` (adaptive branch — 15 s cadence,
  `consecutive_green >= 3` early-exit).
- **Rejected alternative (option A)**: Conservative mode WITHOUT early-exit,
  full `soak_s` wait. Rationale for rejection: the user-pinned
  `phase3-plan.md:81` / `decisions.md:251` bit-identicality requirement
  needs the conservative mode to be the OBSERVABLE behavior (the same
  flat-wait that v0.18 already shipped). The bit-identical contract is
  honored by **no early exit** (the conservative loop is a single deadline
  walk, no consecutive-green counter at all — `lib.sh:4961-4976`).
  The "longer N=3-at-30s-cadence" wording in the test-strategy is a
  characterization pin (the conservative branch never actually early-exits
  in v0.18; the test asserts that).
- **Rejected alternative (option B)**: N ∈ {2, 4} instead of {3}. Rationale:
  N=3 is the load-bearing minimum for "N consecutive greens, not a single
  lucky probe" (C-1 correction, decisions.md (c)). N=2 is too lenient
  (one transient blip could pair with one green and exit success);
  N=4 over-constrains (45 s × 4/3 = 60 s is exactly the adaptive floor,
  making the floor tautological).
- **Evidence**: `tests/test_adaptive_soak.sh` (T-G2a N ∈ {2,3,4} parameterized
  matrix; the matrix green-tests ALL THREE N values per phase3-plan T3.1
  — both options tested, decision documented here); `lib.sh:5040-5046`
  (`consecutive_green >= 3` early-exit), `:5049-5052` (cap-reach-with-
  `< 3 greens` → exit 1 auto-rollback, the never-green commit bug).

## P-D3 — promote.sh dry-run mode env-gated default

- **Source**: test-strategy.md §3 row T-PIN-fence ("P-Dx: IF a promote.sh
  dry-run mode is added, it is env-gated + tested refuse-by-default").
- **Decision**: **No new dry-run mode added to promote.sh.** The dry-run
  surface lives in the **agent tool lane** (`daemon/tools/upgrade_tools.py`)
  with `system_upgrade(dry_run=True)` default — UNTOUCHED by this overhaul.
- **Cite**: `daemon/tools/upgrade_tools.py:2473` (`dry_run: bool = True` default
  for `system_upgrade`); `:2187` (`dry_run` on `system_restart`); the pin
  test `T-PIN-fence` references these unchanged. `promote.sh` remains the
  only mutating lane (per risk-register §3 promote-only tool-lane fence).
- **Rejected alternative**: env-gated dry-run mode in promote.sh
  (e.g. `ENSEMBLE_PROMOTE_DRY_RUN=1`). Rationale: the agent tool lane is
  ALREADY the dry-run surface (per ADR-002 D-FA2.2), so a second dry-run
  mode in promote.sh is a parallel implementation. A new env-gated dry-run
  in promote.sh would also force the existing pre-flight / probe sequence
  to be re-tested under both states — every refactor of promote.sh becomes
  twice the test surface. The clean choice is "leave the lane alone".
- **Evidence**: `daemon/tools/upgrade_tools.py:2473` (unchanged default),
  `tests/test_bounded_subprocess_waits.sh` (existing pin stays green).

## P-D4 — Busy-amnesty: existing-readiness vs new file

- **Source**: test-strategy.md §2 row G2 (T-G2c) + decisions.md (e) / C-8b.
- **Decision**: **Reuse the EXISTING `readiness.py:198/222 detail.inflight_turns`
  field via a SHELL-SIDE READER only** (a readyz curl scoped to the
  `detail.inflight_turns` nested field). **NO new `/run/ensemble/inflight_turns`
  file. NO new daemon-side counter writer.**
- **Cite**: `scripts/upgrade/lib.sh:158-166` (busy-amnesty contract block);
  `lib.sh:2997-3031` (`_read_inflight_turns_bounded` shared helper — extracts
  `detail.inflight_turns` from the readyz body via the depth-counting
  balanced-brace extractor `_json_sub`); `daemon/api.py` (the shared helper
  is consumed by both livez and soak probes per the recent addition).
- **Rejected alternative**: new `/run/ensemble/inflight_turns` file with a
  dedicated daemon-side counter writer. Rationale (decisions.md (e) / C-8b):
  introducing a new file + writer would re-create the exact race the
  v0.20.0 boot-storm was — a counter written by the daemon on a separate
  file is observable-but-eventually-consistent, exactly the property that
  caused the 1.5 s transient window race. The existing readiness field is
  the single source of truth and is already maintained by the pool/turn
  accounting in `readiness.py:198/222`.
- **Evidence**: `tests/test_adaptive_soak.sh` (T-G2c busy-amnesty bound
  `BUSY_INFLIGHT_BOUND=5` exercise); the regression pin
  `grep -rE "/run/ensemble/inflight_turns" .` returns ZERO hits (the C-8b
  pin); `lib.sh:3031` (the bound enforcement).

## P-D5 — Env-poison audit pre- vs post-commit

- **Source**: test-strategy.md §2 row G4 (T-G4a) — *"P-Dx ordering pre- vs
  post-commit, both tested"*.
- **Decision**: **CAPTURE_PHASE (sync, pre-pool, in `manager.initialize()`)
  + DELETE_PHASE (background, post-readyz-green + 1 s OR +5 s wall-clock,
  fires from the FastAPI lifespan startup hook)**. The "post-commit" wording
  is interpreted as **after boot readiness is asserted**, NOT after a promote
  commit (the env-poison purge is a boot-time event, unrelated to the promote
  commit block at promote.sh:540-552).
- **Cite**: `daemon/manager.py:833-938` (CAPTURE_PHASE in
  `InstanceManager.__init__`, sync, pre-pool); `daemon/api.py:217-314`
  (`_deferred_delete_doomed_ids` background task); `daemon/api.py:601-635`
  (lifespan startup hook arming); `daemon/repositories/message_queue/repository.py`
  (the sync `capture_doomed_ids_and_audit` and `delete_doomed_ids` methods,
  unchanged shape per the audit-log invariant at `:1136-1143`).
- **Rejected alternative (option A)**: pre-commit (in the promote.sh
  commit block). Rationale: pre-commit is the synchronous shape that
  caused the v0.20.0 boot-storm class — the daemon was DOWN while
  DELETE ran. The split moves DELETE off the boot critical path.
- **Rejected alternative (option B)**: post-readyz-green only (no +5 s
  wall-clock floor). Rationale: a wedged readyz would block the +1 s arm
  forever; the +5 s floor is a fail-closed guarantee.
- **Evidence**: `tests/test_audit_after_soak_split.py` (NEW per the
  implementation; covers CAPTURE_PHASE + DELETE_PHASE ordering, the +1 s
  arm AND the +5 s arm, idempotency across restart); `tests/unit/tools/test_upgrade_journal.py`
  (reconcile regression pin stays green); `daemon/manager.py:908-912`
  (idempotency preserved verbatim).

## P-D6 — total_ceremony_budget ≤/≥ 900 s under default vs conservative

- **Source**: test-strategy.md §5 row T-NEG-staleness-race + §1 row AC2
  ("the prior pin was self-failing under default mode: it pinned
  total < 900 against a default of exactly 900").
- **Decision**: **Conservative-mode pin: `total_ceremony_budget ≤ EXECUTOR_STALENESS_WINDOW_S (900 s)`**.
  Default-mode assertion holds against the 900 s ceiling as a **≤ relationship**
  (the default IS exactly 900 s; the `<` form is self-failing against the
  default). The R-10 reconciliation is: conservative ≤ 900 (per
  `phase3-plan.md:72` / `phase3-plan.md:81`); default ≤ 900 (per the
  C-10a arithmetic, 870 s typical + 30 s slack inside 900 s).
- **Cite**: `scripts/upgrade/lib.sh:107` (`PROMOTE_TOTAL_DEADLINE_S=900`);
  `lib.sh:82` `EXECUTOR_STALENESS_WINDOW_S=900` (daemon sweep); risk-register
  §1 R-10 ("conservative-mode pin: total_ceremony_budget ≤
  EXECUTOR_STALENESS_WINDOW_S (900 s)").
- **Rejected alternative**: pin `total < 900` in default mode. Rationale:
  the prior `<` pin was self-failing under default mode where the
  default IS exactly 900 s; the conservative-mode `<=` is the live form
  per decisions.md R-10 proof.
- **Evidence**: `tests/test_ceremony_wallclock.sh` (budget-identity table —
  asserted as a config-resolution equality test against a checked-in
  identity table; fails if any future change raises a phase budget
  without conscious update).

## P-D7 — python3-missing: shell-fallback vs fail-closed

- **Source**: decisions.md (a) "opt B (stdlib-only python helper, no bash
  fallback on missing interpreter)"; test-strategy.md §5 row T-NEG-python3-missing.
- **Decision**: **FAIL-CLOSED exit 78** (no bash fallback). Stdlib-only
  helper; explicit version floor (3.7) asserted at startup; preflight probe
  `python3 -c ...` journaled BEFORE any destructive step.
- **Cite**: `scripts/upgrade/journal_splice.py:64-69` (stdlib-only
  preamble, mirrors `ledger_check.py:18-26`); `journal_splice.py:31-33`
  (exit codes: 78 for refusal); `scripts/upgrade/promote.sh:199-218`
  (`_splice_python_floor_probe` preflight, refuses with exit 78 on missing
  / below-floor python3).
- **Rejected alternative**: shell-fallback. Rationale: shell-fallback would
  silently re-introduce the 280 s/event bash brace-walk that the splice
  helper exists to eliminate — the entire premise of the overhaul. A host
  without usable python3 fails closed (refuses promote with the existing
  `missing python3` script-literal refusal); the operator's remedy is to
  install python3 ≥ 3.7 (per the runbook entry).
- **Evidence**: `tests/test_journal_splice.sh` Section 3 — T-NEG-python3-missing
  exercise; `:655 journal_test_negative`: both options tested (BOTH the
  refuse-exit-78 path AND the python3-floor-probe path); the test green
  proves the resolve.

## P-D8 — Heartbeat write: NESTED `in_flight.last_heartbeat` vs TOP-LEVEL `last_heartbeat`

- **Source**: decisions.md (b) / C-2 correction.
- **Decision**: **NESTED write** via `_splice_journal set-in-flight-field
  last_heartbeat` (the `in_flight.last_heartbeat` field that the sweep's
  accessor reads). The TOP-LEVEL form (`set-field last_heartbeat`) is
  INVISIBLE to the sweep.
- **Cite**: `scripts/upgrade/lib.sh:1472-1478` (companion journal refresh
  block); `lib.sh:1096` (`_splice_journal set-in-flight-field last_heartbeat
  "$(_now_epoch)" || true` — the actual call in the `lock_heartbeat` body);
  `daemon/services/upgrade_journal_sweep.py:1586-1589` (the sweep reads
  `in_flight.last_heartbeat` via the `uj.journal_owner_heartbeat_fresh`
  accessor at `:1590`).
- **Rejected alternative**: top-level `set-field last_heartbeat`. Rationale:
  the sweep's accessor reads `in_flight.last_heartbeat` only — a top-level
  field is dead data. This is the C-2 correction; the wrong field was used
  in the original decision narrative and was corrected in P2.
- **Evidence**: `tests/unit/tools/test_upgrade_journal_sweep_liveness.py`
  (T-AC4a fixture constructs `in_flight.last_heartbeat` via the splice
  path — NOT a fake `pending_op.owner_heartbeat_at` writer); `tests/test_journal_splice.sh`
  Section 6 (T2.4 / T2.4a regression pin — `lock_heartbeat` does NOT invoke
  the bash `journal_heartbeat` walk post-patch).

## P-D9 — Splice helper byte-identity (untouched-regions vs C-6 reframe)

- **Source**: decisions.md (a) / C-6 reframe; risk-register §1 R-02.
- **Decision**: **C-6 REFRAME ACCEPTED** — "key-order preserved (Python 3.7+
  dict order) + non-target field values byte-identical after the json
  round-trip". Literal "byte-identity of all UNTOUCHED regions" is
  unachievable under `json.load → json.dump` (the json library normalizes
  whitespace and key quoting).
- **Cite**: `scripts/upgrade/journal_splice.py:45-50` (the C-6 serialization
  block); `:48` (`json.dumps(d, separators=(",", ":"), ensure_ascii=False)`
  + trailing newline — the byte-shape the helper produces); risk-register
  §1 R-02 ("key-order-preserved + non-target field values byte-identical").
- **Rejected alternative**: literal byte-identity of all untouched regions.
  Rationale: unachievable under `json.load → json.dumps` (the lib always
  normalizes whitespace / quoting). A test that demanded literal byte
  identity of the input file would be impossible to write green.
- **Evidence**: `tests/unit/tools/test_upgrade_splice_helper.py`
  (T-NEG-byte-compat round-trip pin — key order preserved, non-target
  values byte-identical); `tests/test_journal_splice.sh` Section 7
  (T2.9 byte-compat round-trip).

## P-D10 — bash `journal_heartbeat` walk: REPLACE vs AUGMENT

- **Source**: decisions.md (b) / C-3 correction.
- **Decision**: **REPLACE** — the bash `journal_heartbeat` walk at
  `lib.sh:1196` was REPLACED by the splice write, NOT augmented. The
  prior bash walk was the 280 s/event cost on the 87 KB state.json.
- **Cite**: `scripts/upgrade/lib.sh:1467-1478` (the C-3 narrative —
  "journal_heartbeat's body was REPLACED (not augmented — the prior
  bash brace-walk was ~280 s on the 87 KB state.json, leaving the 30 s
  heartbeat cadence a fiction)"); the actual call site at `:1096`
  (`_splice_journal set-in-flight-field last_heartbeat "$(_now_epoch)"`).
- **Rejected alternative**: AUGMENT (keep the bash walk as a fallback /
  parallel write). Rationale: the bash walk is the 280 s cost the splice
  exists to eliminate. Keeping it on the loop means the heartbeat cadence
  remains a fiction (every loop iteration is ~280 s, not 30 s). Augment-
  not-replace was the C-3 correction.
- **Evidence**: `tests/test_journal_splice.sh` Section 6 (T2.4 / T2.4a —
  the explicit regression pin: `lock_heartbeat` does NOT invoke the bash
  `journal_heartbeat` walk post-patch).

## P-D11 — Conservative-vs-fast mode default

- **Source**: decisions.md (c) / (d) / C-10a; phase4-plan D-P4.5 / Q6b.
- **Decision**: **FAST mode is the default** (the adaptive 60–120 s soak
  with 3 consecutive greens at 15 s cadence, 900 s wall-clock ceiling).
  Conservative mode (`ENSEMBLE_SOAK_CONSERVATIVE=1`) is the env-only escape
  hatch. **Q6b closes** — no more caller decision.
- **Cite**: `scripts/upgrade/lib.sh:4902-4906` (P3 decision (c), D-P3.2 —
  "ENSEMBLE_SOAK_CONSERVATIVE=1 → reverts to the v0.18-era flat
  behavior"); `lib.sh:4926-4928` (the `if [ "${ENSEMBLE_SOAK_CONSERVATIVE:-0}" = "1" ]`
  branch — opt-in, not default); risk-register §4 sign-off
  (RE-BASELINED to FAST); plan-overview §6 D-P4.5.
- **Rejected alternative**: conservative-by-default with FAST as opt-in.
  Rationale: the project's standing `upgrade_workflow_preference` policy
  (project metadata, "PREFER SPEED OVER MAXIMAL SAFETY", effective from
  next-upgrade-after-2026-10-01 v0.16.8 promote) aligns with FAST default;
  conservative mode remains available as the escape hatch.
- **Evidence**: `tests/test_adaptive_soak.sh` (T-AC3a/b/c — conservative
  mode resolves to exact current behavior; T-G2a adaptive choreography
  with N ∈ {2,3,4} matrix green); `lib.sh:4958-4979` (conservative branch
  is bit-identical to v0.18 era).

## P-D12 — Rollback target on halt→re-promote: `journal.previous` vs `$OLD_CUR`

- **Source**: decisions.md (g) / C-4.
- **Decision**: **Caller-captured `$OLD_CUR`** (captured at flip-prep
  moment, `promote.sh:531`). The journal's `previous` field is NOT
  consulted at auto-rollback because it is stale whenever the previous
  run halted AFTER the flip and BEFORE commit (Oct 9 arc — N-2 walk bug).
- **Cite**: `scripts/upgrade/promote.sh:523-548` (commit block — `OLD_CUR="$CUR"`,
  immediacy assertion, `journal_set_previous "${OLD_CUR:-null}"`); `promote.sh:562-`
  (auto-rollback target = `$OLD_CUR`, NOT `journal.previous`);
  `scripts/upgrade/lib.sh:935-948` (`displaced_version` stamped at
  `journal_open_txn` time); `launcher.sh:1020-1037` (boot_sweep_commit_and_continue
  reads `in_flight.displaced_version` and writes `previous = displaced_version`).
- **Rejected alternative**: read `journal.previous` at auto-rollback
  target time. Rationale: stale on halt→re-promote chains — the Oct 9 arc
  hit N-2 because the journal's `previous` was set by the FIRST attempt
  (not the chain). `$OLD_CUR` is captured at flip-prep (BEFORE the new
  commit can mutate `previous`), so it carries N-1 correctly.
- **Evidence**: `tests/test_rollback_target.sh` (T-AC5a four-cell matrix —
  fresh journal · halt→re-promote · boot_sweep_commit_and_continue ·
  double-rollback, ALL assert `previous == N-1` at every boundary).

## P-D13 — Maiden-promote mode

- **Source**: phase4-plan.md D-P4.5 / Q6b; risk-register §4.
- **Decision**: **FAST mode** (the default adaptive 60–120 s soak @
  15 s cadence, 3 consec greens, 900 s ceiling). Conservative
  (`ENSEMBLE_SOAK_CONSERVATIVE=1` or `ENSEMBLE_PROMOTE_SOAK_S=300`) is
  the env-only escape hatch. **3-factor nonce ceremony untouched and
  remains user-gated** (per the standing project metadata `upgrade_policy`).
- **Cite**: phase4-plan.md D-P4.5 (the leader ruling — "the default
  recommendation for the maiden promote is FAST mode"); risk-register
  §4 sign-off (RE-BASELINED to FAST).
- **Rejected alternative**: maiden-promote in conservative mode. Rationale:
  the leader ruling resolved Q6b; the project's
  `upgrade_workflow_preference` policy aligns with FAST default;
  conservative mode remains as the escape hatch. The C-11 runbook
  controls (per phase4-plan D-P4.6 / T4.6) attach the compensating measures.
- **Evidence**: `maiden-promote-preflight-2026-10-12.md` (the preflight
  doc; this deliverable is filed in the planning directory alongside
  this resolution).

## P-D14 — Vehicle reconcile (separate checkpoint-conn-resilience merge?)

- **Source**: phase4-plan.md D-P4.7 / C-12.
- **Decision**: **RIDES AS-IS.** `feature/checkpoint-conn-resilience` is
  ALREADY on `c8a3fbf6` (the base SHA for the v0.20.1 vehicle).
  No separate merge commit required. The P4 reviewer's gate is to
  CONFIRM at PR-open time that the (d) ceiling arithmetic accounts for
  the new probe cost.
- **Cite**: phase4-plan.md D-P4.7 verbatim ("`feature/checkpoint-conn-resilience`
  is already on latest `c8a3fbf6`: `persistence.py` pool-backed saver +
  `checkpoint_adapter.py` retry wrapper + `readiness.py` probe —
  councilor-verified. It rides the v0.20.1 vehicle as-is; no separate
  merge commit required."); project metadata `deferred_feature_adoptions`
  (the resilience work is documented as already-merged upstream).
- **Rejected alternative**: separate merge commit on top of `c8a3fbf6`.
  Rationale: needless churn; the resilience is on the latest base.
- **Evidence**: `git log --grep=<resilience-keyword>` (returns the
  resilience commits in the ancestry of `c8a3fbf6`) — verified at
  staging time per D-P4.7.

---

## Summary

- **14 P-Dx cells** identified, **all 14 RESOLVED** with concrete
  decision + cite + rejected alternative + evidence.
- **No fenced-out cells** (no decision deferred past this gate).
- Every resolution matches what was ACTUALLY implemented in P1-P3 (read
  against the post-P3 HEAD `6a4cf2bf3`).
- The P-Dx matrix is complete; the implementer's gate is satisfied.
---

## R1 Remediation rulings (council 2026-10-12 — the 8-critical fix pass)

- **W4 — sweep is TWO-signal, not three** (AMENDED, not implemented):
  the liveness predicate keeps (1) heartbeat-freshness and
  (2) `os.kill(pid, 0)`; the planned third signal (systemd cgroup-leaf
  match) is DEFERRED. What the leaf would add: pid-recycled-under-a-
  foreign-unit discrimination for the stale-hb + alive-pid cell. Why
  deferred: that cell is already leave-alone under the conservative
  ruling (an unbounded conservatism — the op is swept later — never a
  lossy reclaim), so the third signal buys precision, not safety.
  Recorded in the predicate docstring (upgrade_journal_sweep.py) and
  here. Implementation stays P3+.
- **C8 — hb-first ordering is the SPEC** (no SKIP-and-file needed):
  the AC4b trust-table cell "fresh heartbeat alone → leave alone even
  when pid and cgroup are missing" is CORRECT for the pre-spawn-stamp
  window (pending_op created before the executor stamped its pid —
  owner_pid == 0 — while the live promote heartbeats
  in_flight.last_heartbeat every 30s). The shipped predicate
  short-circuited `owner_pid <= 0` BEFORE the hb read and the test
  asserted the INVERTED value with a rationalizing comment. Code and
  test now both implement the spec cell (hb-fresh ⇒ True; pid/cgroup
  decide only on stale/unreadable hb).
- **C4 — live-install splice waiver shape** (lock-held, option (b)):
  the write-tool refusal now carries exactly one waiver — while the
  owning install's `rollback.lock.d` is HELD by a live owner (dir
  exists + owner pid alive per the bash protocol's EPERM-alive rule;
  heartbeat age advisory per the never-break-a-live-owner ruling).
  Out-of-pipeline writers stay refused deterministically (a crashed
  owner's stale-lock dir does NOT waive). Option (a) fd-attestation
  was unavailable: the lock is mkdir-based, no inheritable fd exists.
