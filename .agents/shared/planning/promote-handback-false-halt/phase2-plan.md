# Phase 2: Test + Rehearsal Strategy (incl. the negative proof)

Date: 2026-11-17
Author: plan-creation worker (Phase 2 of 3)
Status: Draft — for leader review. **Phase 1's plan LANDED while this file was being written** (phase1-plan.md,
19:27Z, §14 "Hand-off: design invariants Phase 2 must prove"), so §5.2 is no longer a list of unknowns — it is a
**mapping table** from Phase 1's named invariants (`I-A1…I-E3`) to this phase's legs. Legs are now authored
against the landed design; the residual-unknowns list shrank from 9 assumptions to **4** (§5.2.3).
Plan set: `.agents/shared/planning/promote-handback-false-halt/`
Worktree: `/home/nea/ensemble-src-wt-hbfix` (branch `fix/promote-handback-false-halt`, base `0181dadbf` == `origin/latest`)
Sibling phases: Phase 1 (`f40b3929`) = code-fix design A/B/C + restart arm; Phase 3 (`946569ab`) = floor/version/staging.

---

## 1. Objective

Produce the **verification surface** for the false-halt fix so that three properties are provable, each
with a named counter-mutation that reddens it:

1. **Honesty** — every journal row the promote/restart machinery writes about a `systemctl` probe states
   the child's *actual* rc and a *truthful* kind (rc 3 never again journaled as a budget timeout; rc 4 and
   any other nonzero never called `timeout(`).
2. **Survival** — a **healthy** boot whose listening holders are only *partially* inside the argv-anchored
   owned-pid set (the incident shape: `{daemon, mcp-child-1, mcp-child-2}` vs `owned {launcher,
   ensemble-prod, …}`) **completes** the hand-back instead of halting.
3. **Failure preservation** — a genuinely **wrong** release (wrong version, absent version, non-2xx,
   no listener, genuinely-foreign holder) **still halts**, with the same B4 leave-txn-open semantics and
   the same "NO nohup fallback" absolute.

Plus: the restart arm's seven pre-adoption refusal classes each carry a cause row (the five pre-`journal_init`
exits stay silent **and allowlisted**, because a journal write is structurally impossible there).

Single testable sentence: *Phase 1's named invariants (`I-A1…I-E3`) each map to at least one leg, every leg
has a named counter-mutation that reddens it, and the negative-proof section reddens specifically when the
containment ladder's accept-set is widened.*

**Design state.** Phase 1's plan **landed** (19:27Z) with a named "Hand-off: design invariants" section. This
file is therefore authored **against that design**, not against a candidate space; §5.2 is the mapping table
and §5.2.3 lists the only four residual unknowns.

---

## 2. Scope

### In scope (Phase 2 owns)

| # | Item | Anchor (worktree-verified) |
|---|------|----------------------------|
| 1 | Candidate **A** tests — per-site label honesty at 3 conflating sites + a direct unit leg on Phase 1's 2 new pure-string helpers | `lib.sh:6332-6339` (reset-failed), `:6356-6360` (is-active), `:6697-6701` (restart scope-arm), `_bounded_rc_kind`/`_bounded_rc_reason` (new, near `:4883`) |
| 2 | Candidate **B** tests — survival, exhaustion, the 4 ladder rungs, **ordering invariant**, **pooled deferral**, **gate-surface parity**, portability, C-10a sync | `lib.sh:6093-6133`, `:6170-6298`, `:8004-8137`, `:6921-6950`; Phase 1's `_gate_attr_containment` / `_supervision_pid_ancestor_of` / `_gate_attr_unit_ctx` |
| 3 | **NEGATIVE PROOF** (§9 — commission-mandated, own section) — 11 legs + 6 over-accept counter-mutations, mapped to phase1 §5.7 P1–P12 / phase1 §14 N1–N9 | `lib.sh:6097-6126` refusal arms |
| 4 | Candidate **C** tests — systemd-lane error stash write/clear/read | `lib.sh:5592-5614`, `:6376-6389`, `:6395-6397`; templates `promote.sh:692-698`, `restart.sh:257-261`, `rollback.sh:256-260` |
| 5 | Restart-arm cause-row tests — the **7** classes Phase 1 journals, plus R0's completeness pin over every `exit 78` site (18) | `scripts/upgrade/restart.sh` (census §10.1) |
| 6 | Rehearsal tiers + the DANGER LIST (§11), all scratch/sandbox-only | precedent `tests/helpers/preflight_pack_common.sh:7,76,78,89,106` |
| 7 | Registration/wiring plan (GAP #2) + evidence-pack (RESULTS) shape | `.agents/tester/PACKS.md`, `tests/test_upgrade_overhaul_all.sh:120-196` |
| 8 | The new fixtures: inherited-LISTEN-fd holder model (GAP #1, §4.2) and the rc-emitting `systemctl` stub extension (GAP #1b, §4.3) | §4.2, §4.3 |

### Out of scope

| # | Item | Why out |
|---|------|---------|
| 1 | Any change to Phase 1's chosen design or code shape | Phase 1 owns it. Phase 2 proves Phase 1's **named invariants** (§5.2.1) and reports deviations as findings |
| 2 | Any `scripts/upgrade/**` edit — including a shared test helper living under `scripts/` | Phase 2 adds **only** `tests/**` files, so `executor_family_paths` (`scripts/upgrade/executor_family.sh:78-98`) is untouched and Phase 3 carries **no new family-registration obligation** from this phase (directly satisfies Phase 3's P3-R4 cross-phase contract). Corollary Phase 2 must respect: **no test helper may live under `scripts/`** — the fence helpers used here are `tests/upgrade_test_lib.sh` and `tests/helpers/preflight_pack_common.sh`, both already outside the family |
| 3 | `PIPELINE_SCRIPTS_REV` / `PIPELINE_SCRIPTS_FLOOR_COMMIT` re-pin, version bump, stage, tag | Phase 3 |
| 4 | Live/sandbox **promote execution** beyond the Tier-3 rehearsal (scratch-only, leader-gated) | §11.4 |
| 5 | Fixing the ambient-`POSTGRES_*` / auth-scrub debt in the standing suites | Pre-existing, owns by those lanes; Phase 2 only *fences* its own runs |
| 6 | Consolidating the three drifted `make_systemctl_stub` copies | Out of scope (would churn 3 suites). Recorded as a routed registration task (RG-6, §12.2) with the exact knob contract (§4.3) so a future consolidation is mechanical |

**Sibling couplings (explicit):**

- **With Phase 1:** Phase 1 owns `scripts/upgrade/**`; Phase 2 owns `tests/**`. The only shared artifact is the
  *contract* in §5.2 (Phase 1's named invariants), and the only shared risk is R-3 (Phase 1's text edits near
  pinned assertions), which §11.2's regression matrix converts into a named-delta discipline.
- **With Phase 3:** Phase 3's `M` (the merge commit) contains this phase's tests; Phase 3's P3-R4 (family
  registration) is **vacuous for Phase 2** by the out-of-scope row 2 above. Phase 2 must not introduce a
  `scripts/` file — verified by A7's `git diff --stat` assertion.
- **With the planner:** this file owns only `phase2-plan.md`. `plan-overview.md` and `decisions.md` remain the
  planner's; corrections this phase discovered (Phase-1 line drifts, the C-10a unpinned hole, the
  `make_systemctl_stub` gap) are listed in §13 item 8 for the planner to carry.

---

## 3. Incidents this phase must be able to fail on

| # | Defect | Evidence anchor |
|---|--------|-----------------|
| D1 | `is-active` rc 3 (inactive — *the normal state right after `start` is dispatched*) journaled as `kind=budget reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=5) … rc=3` | fixture log 17:29:28Z; `lib.sh:6356-6360` |
| D2 | The reset-failed site **hard-codes** `rc=124` regardless of the child's rc | `lib.sh:6337-6339` |
| D3 | The restart scope-arm site carries the identical conflation | `lib.sh:6699-6701` |
| D4 | Serving attribution **fail-fasts on the FIRST green** when a LISTEN holder is outside the argv-anchored owned set — while Layer 2 (`unit` cgroup containment) *would have accepted it* | `lib.sh:6097-6106`, refusal `:8039-8047`; census by argv+cwd only `:6921-6950` |
| D4-note | **Honest limit of the incident evidence:** the fixture logs *do* name the holders (`1191207 1191605` vs owned `1190778 1190919 1190932`) but they **do NOT record pid→process identity**, so "MCP children with inherited listen fds" is a **hypothesis**, not a proven fact; the same run's own annotation records `:9797` held by `kubectl`/monitor while the daemon served `:9798`. Consequence for Phase 2: §7.1's survival leg asserts **the accept criterion Phase 1 chooses** (containment), not a claim about what those two pids were; §9's NP-4 keeps the *genuinely* foreign holder a **halt** under every design | `research-findings-lane.md:36`, `:171` ("Pid→process identity … NOT-DETERMINABLE") |
| D5 | Zero unit-arm retry (`start` once, verify once) | `lib.sh:6377`, `:6395-6397` |
| D6 | `HB_ERR` is written **only** on the launchd branches, so the systemd lane's halt says `hand-back error: none captured` | `lib.sh:5592-5614` writers `:5634-5635`, `:5720-5721`; template `promote.sh:698` |
| D7 | Restart-arm twins died "clean audits, no cause" — the pre-adoption refusals journal nothing. Census: **7 silent exits inside the `:135-175` family** (`:136` sibling, `:145` init, `:149` torn, `:156` no-pending_op, `:162` pop-kind, `:166` pop-run, `:174` inf-kind) + **5 more pre-preflight** (`:82`, `:96`, `:104`, `:108`, `:122`) = **12 silent** | `scripts/upgrade/restart.sh` (verified by `grep -n "exit 78"`) |

---

## 4. Test surface inventory (what exists, what is new)

### 4.1 Reuse as-is (verified contracts)

| Asset | Location | What it gives |
|---|---|---|
| `assert_contains` / `assert_not_contains`, fixture registry + ONE EXIT trap, F1 fake-HOME fence | `tests/upgrade_test_lib.sh:32-38`, `:59-107` | assertions + live-install safety |
| `make_curl_stub` — `CURL_MODE=ok\|wrongver\|noversion\|non2xx\|busy503\|unreachable`, `CURL_DELAY_S`/`CURL_START_EPOCH` | `tests/packs/handback_verify_budget_test.sh:272-319` | every negative-proof green-shape |
| `make_lsof_stub <dir> <rc>` printing pid `424242` (rc-0-empty deliberately means "no listener") | `:252-270` | holder census replacement |
| Call-counting / delayed census stub (`GD_DELAY_CALLS`, `GD_HOLDERS`, `GD_NEVER`, counter file) + `gd_run` | `:86-160` | **counter-token** grace proofs (clock-free) |
| Owned-set function override seam (`_supervision_owned_pids() { printf …; }`) | `:386-392`; `tests/test_release_journal.sh:1147` | make the fixture the "owned daemon" without pgrep/ps |
| Real listener via `python3 http.server` | `tests/test_release_journal.sh:1114-1138` | genuine LISTEN socket (model-fidelity leg) |
| `uname` PATH stub delegating to `$REAL_UNAME` | `tests/test_atomic_flip.sh:229-237`, `:265-269` | Darwin / Plan9 arm selection on a GNU host |
| Slow-boot end-to-end harness (`run_slow`, `run_probe`, scaled budgets, `SUPERVISION_*` inline) | `tests/test_supervision_stop_handback.sh:1289-1345` | the closest existing shape to the incident |
| `make_mut` (mutation on a **copy** of lib.sh, refuses a vacuous plant via `cmp -s`) | `tests/packs/handback_verify_budget_test.sh:409-421` | the whole counter-mutation doctrine, already implemented |
| FAKE_HOME + escape guard + per-variable scrub + self-test | `tests/helpers/preflight_pack_common.sh:7,76,78,89,106` | the canonical fence |

### 4.2 GAP #1 — the inherited-LISTEN-fd fixture (new)

**Finding (grep-verified):** no existing fixture models a holder whose listening socket was
**inherited** from an owned parent. Every current stub reports the *same* pid it arms as owned
(`make_lsof_stub` → `424242`, and the owned seam arms `424242`), so the incident shape is
**structurally unreachable** in the current corpus.

**Design — two layers.**

*Layer 1 (workhorse, deterministic, host-agnostic): a multi-pid census stub.*

```
SYSCTL-less; lsof stub prints two lines:  $HB_OWNED_PID  and  $HB_CHILD_PID
owned-set seam prints ONLY $HB_OWNED_PID
```

- `HB_OWNED_PID` = the daemon (owned by construction), `HB_CHILD_PID` = the inherited-fd child
  (absent from the owned set) — exactly `holders {1191207 1191605}` vs `owned {1190778 …}`.
- Because the census stub is call-counted (reuse `gd_run`), every leg can additionally assert **how
  many** census calls the code made — the clock-free replacement for "the grace waited".
- This layer needs no sockets, no `/proc`, no systemd; it runs on every host.

*Layer 2 (model-fidelity leg, ONE leg, Linux+lsof only): a real forking server.*

Precedent for the fork shape: `tests/test_promote_launchd_survivorship.sh:684-700` (`os.fork()` +
`sys.stdout.flush()` + pid printed to the parent). Shape:

```
python3 - <<'PY'          # binds 127.0.0.1:0, prints the port, forks
# parent: exec/sleep with argv containing "$INSTALL_DIR/ensemble-prod" (owned-shape)
# child : inherits the LISTEN fd, exec "sleep 60" with a NON-owned argv (mcp-child shape)
PY
lsof -ti:<port> -sTCP:LISTEN      # MUST list BOTH pids
```

**Why Layer 2 is not optional:** it proves the *premise* of Layer 1 — that
`lsof -ti:<port> -sTCP:LISTEN` really does report an inheriting child, which is the whole reason the
incident refused. If Layer 2 disproves the premise, the incident analysis itself is wrong and Phase 1
must learn that before writing `lib.sh` code. Layer 2 asserts no `lib.sh` behaviour at all; it asserts
**the fixture model**. Named fence: skipped (loud) on non-Linux / no-lsof hosts.

### 4.3 GAP #1b — the `systemctl` stub cannot emit a nonzero `is-active` rc (new, blocking)

**Finding (grep-verified):** all three existing stubs answer `is-active` with `echo "<status>"; exit 0`
(`tests/packs/handback_verify_budget_test.sh:218`, `tests/test_supervision_stop_handback.sh:120`,
`tests/test_bounded_subprocess_waits.sh:100`). **No existing fixture can produce rc 3, which is the
defect's core input.** A new stub generation is therefore mandatory, not optional.

Contract to add (superset — every existing `SC_*` knob keeps its meaning; unspecified rc defaults to 0):

| Knob | Meaning |
|---|---|
| `SC_IS_ACTIVE_RC` | rc for `is-active` (default 0). Pair it with `SC_IS_ACTIVE` for the status word |
| `SC_IS_ACTIVE_ERR` | optional stderr line for `is-active` (models a D-Bus error, not just inactivity) |
| `SC_RESET_FAILED_RC` / `SC_RESET_FAILED_ERR` | same for `reset-failed` |
| `SC_SHOW_RC` / `SC_SHOW_ERR` | `show -p MainPID/-p ControlGroup` tool failure (the unprobeable arm) |
| `SC_START_ERR` **(exists)** | already carries the start-failure first stderr line |
| `SC_IS_ACTIVE_N` / `SC_IS_ACTIVE2` **(exist)** | per-call status change (kept) |

House rule applied: a stub that answers "inactive" with `exit 0` no longer models the tool. The new stub
also **logs argv to `SC_LOG`** (existing behaviour) so every leg can assert the *call sequence* and
**which** subcommands ran — that is how the portability legs prove a Linux-only probe was *not* issued.

**REQUIRED closure — rc-faithful defaults (architect §3):** all three existing `make_systemctl_stub` copies exit `0`
while printing `inactive`, so **no existing test exercises rc=3 at any site** and the `:376` "no timeout events" pin is
**currently vacuous**. The new stub must therefore be **rc-faithful by default** (`is-active` ⇒ exit **3** when the status
word is `inactive`/`activating`; `reset-failed` likewise), so `:376` becomes a live policy pin and the incident is
**reproducible in-test**. The `SC_*_RC` knobs above override the faithful default for the legs that need a specific rc.

---

## 5. Test-design doctrine for this phase

### 5.1 House rules that bind every leg

1. **Mutation-verified or it isn't a pin.** A green assertion is worth nothing unless a named
   counter-mutation turns it red. `make_mut` already refuses a vacuous plant (`cmp -s` guard,
   `handback_verify_budget_test.sh:409-421`). Every leg in §6–§9 carries a mutation id (each section's own mutation table: §6.2, §7.9, §8.2, §9.2).
2. **FAILED-list diff vs TRUE pre-fix base.** For standing suites the gate is not "green", it is
   "failed set byte-identical to the pre-fix base (or a declared, attributed delta)".
3. **Geometry over clocks.** Agent-bash clocks freeze across checkpoint interruptions (craft note in
   `handback_verify_budget_test.sh:79-83`) — so **call counters**, not wall-clock deltas, prove that a
   grace waited or that a fail-fast did not.
4. **Clocks where unavoidable**, with the disclosed-flake protocol: any red is **re-run once** before
   being read as a regression (precedent: the L8b boundary flake, `PACKS.md:1364`).
5. **Non-vacuity needlers.** Every negative leg asserts that the code *reached* the gate it is supposed
   to exercise (a needle from the gate's own WARN / `GATE_ATTRIBUTION_DETAIL`, **and** a census-call
   counter from the stub). Without that, a leg can pass because an earlier step failed.
6. **Frozen pins must not break** (§5.3).
7. **Never delete a string to make a negative guard green.** If a leg reddens, the answer is a fixture
   fix or a code fix — never a removed assertion.

### 5.2 Phase 1 dependency map — what Phase 2 must NOT hard-code

#### 5.2.0 Phase 1's LANDED design (the input this phase now builds on)

Read from `phase1-plan.md` (19:27Z). Facts that change this phase's legs:

| Phase 1 decision | Value | Consequence for Phase 2 |
|---|---|---|
| **A taxonomy at the sites** (not in `_run_bounded`) | `124 → kind=budget` (legacy WARN **byte-identical**); `3 → no journal row, _log only`; `4/other → kind=nonzero_exit` with the true rc | §6 legs assert **"no row at all"** for rc 3 (not merely "no `kind=budget`"), and a `_log` line instead. The rc-124 leg asserts **byte-identical** legacy text |
| **New micro-helpers** `_bounded_rc_kind` / `_bounded_rc_reason` next to `_run_bounded` (T1) | pure string functions | §6 gains a **direct unit leg** on these helpers — cheaper and sharper than driving three call sites for the pure-string half |
| **Event kind unchanged** (`subprocess_wait_timeout`); only the `kind=` row token changes | no new event kind, no `_OUTCOME_LABELS` edit, no daemon change | `I-E3` (no new event kind) becomes the assertion; the earlier A7 "new kind needs a label" leg collapses into it |
| **B = containment ladder** R1 owned → R2 systemd cgroup → R3 ppid-ancestry → R4 launchd job, attempted **only when Layer 1 found a nonmatch** | B-primary | §7.1 asserts the ladder; §7.4 gains the **ordering invariant** (no extra `systemctl show` when nonmatch is empty — Phase 1 `I-B2`); §7.3's rung legs are now **required**, not conditional; §7.6 adds **gate-surface parity** (Phase 1 §5.8 asks for it explicitly) |
| **B-secondary = pooled deferral** `_GATE_ATTR_DEFER_S` (default 5 s, **test-only seam**, env-visible, NOT an exported production knob — D3: integer-parsed via the `_numeric_knob` router, **clamped `[0,5]`**, **seconds-counted**, per-shell) + distinct "UNATTRIBUTABLE … deferring to the verify budget" detail; exhaustion ⇒ **transient (2)**, refusal only at budget end (leader D1) | inside `_gate_serving_attribution` only | §7.5 adds the deferral legs (`I-B8`); the seam being **env-visible** is what makes them drivable (`=0` and default). **Review wave 1 (BL-1) additions:** the exhaustion marker journals event kind `subprocess_wait_timeout` with row token `kind=attr_deferred`, fire-once latched (phase1 §4.2 — `I-E3`/A7/M-A7 intact); the transient `2` is consumed per the rc=2 consumer contract (phase1 §5.4a, `D-P1-4`): probe maps 2→poll (**T7b**), one-shot consumers map 2→bounded retry + transient wording (**T7c**) — proven by leg **B-defer-e** under **M-B-defer-E** |
| **No budget knob changes** (A21 untouched) ⇒ C-10a **no numeric edit**, only a one-line comment in **both** copies (T9) | B10 stays mandatory, B9 (knob delta) becomes **N/A unless** the numbers move | §7.7 keeps **B10** mandatory (Phase 1's T9 edits both copies, so the sync pin is the proof that both edits landed); B9 is demoted to a **guard** (§7.10): `ceremony_wallclock` must stay green **with zero edits** |
| **C = `_handback_stash` wrapper** reusing `HANDBACK_LAST_ERROR` + `$$`-file, touchpoints C-1 clear-on-entry, C-2 reset-failed, C-3 is-active (stashes **both** arms incl. benign rc 3 — "context of a later failure"), C-4 start's `first`, C-5 verifier reason via new global `HB_VERIFY_REASON` + explicit file write from inside the verifier, C-6 clear on success | readers **unchanged** | §8's legs map 1:1 to C-1…C-6 (plus `I-C5` subshell survival, which is the file channel — the sharpest leg) |
| **Restart arm = 7 journaled classes** at `restart.sh:122, 136, 148, 155, 161, 165, 173`, kind `halt`, `\|\| true`, class slug | `I-D1` | §10's silent set **shrinks from 12 to 5** (`:82`, `:96`, `:104`, `:108`, `:145`); those plus the post-adoption advisory `:192` form the **6-entry R0 allowlist** (see §10.1) |
| **Optional T11 / T12** | **RULED (D-P1-3 + D-P2-4, 2026-11-17): T11 RIDES the lane** (`promote.sh:1051-1056` rollback halt gains `HB_ERR`); **T12 is DEFERRED** to a named follow-up lane (future id `143a045c`) and `NP-11` is **dropped** as a declared parity gap | T11 **in** → C-7 gains a rollback-template row; T12 **deferred** → §9 NP-11 is dropped and the gap is declared in the evidence pack |

#### 5.2.1 Phase 1 invariant → Phase 2 leg map

Leg names below are the ones actually used in §6–§10 (re-cut after the design landed):

| Phase 1 invariant | Phase 2 leg(s) | Mutation |
|---|---|---|
| `I-A1` rc 3 ⇒ **no** journal row, a `_log` line, behaviour unchanged | **A3** (is-active), **A6** (scope-arm) | M-A3; M-A3b simulates the *policy flip* (row added) |
| `I-A2` rc 4 / other nonzero ⇒ exactly one row, `kind=nonzero_exit`, true rc, never `timeout(` | **A0** (helpers), **A1** (reset-failed), **A4** (is-active) | M-A0, M-A1b, M-A3 |
| `I-A3` rc 124 ⇒ **legacy WARN + `kind=budget … rc=124` byte-identical** | **A2** (reset-failed), **A5** (is-active) | M-A5 (one-character wording change) |
| `I-A4` no literal `rc=124` for a child that returned otherwise | **A1**, **A4** | M-A1 |
| `I-A5` `_run_bounded` unchanged | **A0**'s structural half | — (a diff assertion needs no mutation) |
| **D2** sites 4/5 start-arm symmetric `kind=nonzero_exit` rows (leader ruling; decisions.md §10.1; phase1 §4.3 note) | **A8** (start arm), **A9** (scope-arm start) | M-A8, M-A9 |
| `I-B1` incident replay ⇒ gate 0, hand-back 0, no halt | **B1**, **B1b** (delayed attribution), **B1c** (real pids) | M-B1 |
| `I-B2` ordering: no extra `systemctl show` when Layer 1 finds no nonmatch | **B-order** | M-B-order |
| `I-B3` refusal detail prefix byte-preserved | **NP-4** / **NP-9** (prefix assertions) + §11.2's must-survive matrix | — (regression obligation; the M-section must stay green untouched) |
| `I-B2b` **(added — REVIEW WAVE 1, BL-2)** gate-call stdout purity: every `$()`-captured gate call keeps byte-identical stdout (captured pid = a single numeric token; JSON bodies unpolluted); all gate-emitted INFO — accept line AND transient/exhaustion marker — rides STDERR (`printf … >&2`, the `:6210` precedent; `_log` is stdout when the logfile is unset, `:434-443`) | **B-parity-capture** (§7.6) | **M-B2b** (ACCEPTED line rides `_log` ⇒ capture corrupts) |
| `I-B4` foreign holder ⇒ refusal **at the first census** (no deferral burn) | **NP-4** | M-NP1, M-NP5 |
| `I-B5` pidless launchd job + served port ⇒ refuse | **B6** (third cell) — reuses the existing M7 shape | — (existing pin) |
| `I-B6` wrong / absent version ⇒ refuse with today's wording | **NP-1**, **NP-2** | M-NP2 |
| `I-B7` green with no listener ⇒ refuse | **NP-5** | M-NP3 |
| `I-B8` deferral ≤ `_GATE_ATTR_DEFER_S` **per shell** (≤5 s, seconds-counted, clamped `[0,5]`), exhaustion ⇒ distinct UNATTRIBUTABLE detail **and a transient (2) verdict** — refusal only at budget end (leader D1) | **B-defer-a**, **B-defer-b** | M-B-defer |
| `I-B8a` **(added — architect closures C-1/C-2, leader D1)** composition rule: the first rung that ANSWERS decides; an answered `1` is downgraded to `2` when a stronger rung was unanswerable; ladder-accepted holders **skip** Layer 2's unit-arm re-test (Layer 2 is refusal-only) | **B-order**, **B3**, **B-defer-a** | **M-NP1c** (downgrade removed ⇒ NP-4/NP-6 red), M-NP1b |
| `I-B8b` **(added — leader D1)** deferral is a **budget-bounded delay that can never accept**; attribution acceptance is evidence-only; the version conjunct is absolute and outside the widening | **B-defer-a**, **NP-1**, **NP-2**, **NP-8** | M-B-defer |
| `I-B13` **(added — REVIEW WAVE 1, BL-1; phase1 §5.4a / `D-P1-4`)** rc=2 consumer contract at EVERY gate call site: the probe maps transient 2 → probe `return 2` (T7b — the verifier keeps polling); the one-shot consumers (`gate_version:8159`, promote/restart/rollback gate blocks, `gate_soak` per-tick) map 2 → bounded in-budget retry + the distinct transient wording (T7c) — never a refusal rc 1, never `gate_fail_reason`/`GATE_FAIL`/`REGATE_FAIL` → 8b auto-rollback, never the false `/livez unreachable >Ns` / `version verify mismatch` labels; persistent-2 halts only at budget end with the exhaustion detail in `HB_VERIFY_REASON` | **B-defer-e** (§7.5) | **M-B-defer-E** (the `if !` consumer form restored at `:6103-6106` ⇒ must redden) |
| `I-B9` gate-surface parity across `gate_livez` / `gate_readyz` / `gate_version` | **B-parity** (+ **NP-9** for the refusal side) | M-NP4 |
| `I-B10` halt chain untouched (probe 1 → verifier 1 → unit 1 → promote halt+B4, no nohup) | **B-halt**, **B2**, **NP-7a/NP-7b** | M-B2 |
| `I-B11` portability: Darwin ⇒ no `/proc`; Plan9 ⇒ fail-closed | **B7** | — (the `SC_LOG` "no ControlGroup probe" assertion is the proof) |
| `I-B12` every new acceptance has a counter-mutation | the whole §9.2 matrix + SC-1 | — |
| `I-C1` verify refusal ⇒ halt detail carries the reason; `none captured` impossible on the unit path | **C-5a**, **C-7** | M-C3 |
| `I-C2` start failure ⇒ first stderr line reaches the halt detail | **C-4** | M-C1 |
| `I-C3` success clears both channels | **C-6** | M-C2 |
| `I-C4` entry clears: a pre-hand-back seed cannot surface as this failure's cause | **C-1** | M-C1 |
| `I-C5` subshell survival (the file channel) | **C-5a**'s `$( )` read | **M-C4** (variable-only stash) |
| `I-D1` each of the 7 refusal classes ⇒ one `halt` row with a stable slug + run_id, before `exit 78` | **R-PY, R-SIBLING, R-TORN, R-NOARM, R-POPKIND, R-POPRUN, R-INF** + **R0** | M-NOARM, M-TORN, M-R0 |
| `I-D2` exit codes and `_warn` wording unchanged | every R-* leg's (e) assertion vs a captured baseline | — |
| `I-D3` TORN journal: best-effort, exit path unchanged | **R-TORN** | M-TORN |
| `I-D4` scope-arm rc 3 no longer journals `kind=budget … timeout` | **A6** | M-A3 |
| `I-E1` no `daemon/**` logic change | A7 (iv) structural | — |
| `I-E2` C-10a blocks byte-identical | **B10** | M-B10 |
| `I-E3` no new exported env, no new `_numeric_knob`, no new journal event kind | **A7** | M-A7 |

#### 5.2.2 Phase 1's negative-proof template (Phase 1 §14) → this phase's NP ids

| Phase 1 template | Phase 2 leg |
|---|---|
| N1 incident (accept) | B1 / B1b / B1c |
| N2 foreign (refuse) | NP-4 (+ NP-9 for the gate surfaces) |
| N3 dual-supervisor launchd pidless | B6 third cell (reuses the existing M7 pin) |
| N4 wrong version | NP-1 (+ NP-8 for the timing shape) |
| N5 no listener | NP-5 |
| N6 unattributable after ≤ defer | B-defer-a (exhaustion ⇒ **transient 2**, refusal only at budget end — leader D1) |
| N7 rc matrix per site | A0…A6 |
| N8 stash | C-5a / C-7 |
| N9 restart classes | the R-* set |

#### 5.2.3 Residual unknowns (4 — everything else is resolved by the landed Phase 1 plan)

| # | Still open | Where it is open | How the leg stays robust |
|---|---|---|---|
| **U-1** | **rc=3 policy**: Phase 1 recommends *no journal row, `_log` only* (phase1 §4.1) but lists it as an **open decision** (phase1 §15.1), with the alternative "journal `kind=nonzero_exit` for rc 3 too" | `phase1-plan.md` §15.1 | A3/A6 assert the **invariant that holds under both**: rc 3 must produce **no `kind=budget` row** and **no `timeout(` token**; if a row exists it must carry `kind=nonzero_exit` and `rc=3`. One extra assertion ("zero rows at all") is written **separately and marked policy-pinned**, so a decision flip reddens exactly one named assertion instead of the leg |
| **U-2** | ~~Deferral exposure~~ **RESOLVED (leader D1/D3, 2026-11-17):** the seam is **not an exported production knob**; it is integer-parsed via the house `_numeric_knob` router (loud-garbage note), **clamped to `[0,5]`**, counted in **seconds**, per-shell, never exported; a non-default override is honoured **only** under the dual-key test marker `ENSEMBLE_TEST_GATE_DEFER=1` | phase1 §5.4 (amended), decisions.md §10 (D1/D3) | B-defer-a drives the bound through one indirection (`P2_DEFER_SETTING`, resolved by a single grep at authoring time: the env var). If it is not settable, B8 falls back to a **wall-clock bound** (total defer ≤ 6 s measured) and the gap is a routed finding. **New assertions:** a leaked `300` clamps to 5; a non-numeric value raises the loud-garbage path (not a silent 0); the `until`-form infinite loop is impossible (the `>0` guard) |
| **U-3** | ~~**T11 / T12 optional items**~~ **RESOLVED (D-P1-3 + D-P2-4, 2026-11-17): T11 IN** (rollback-halt `HB_ERR`, `promote.sh:1051-1056`) → C-7 gains a rollback-template row; **T12 DEFERRED** (launchd flip-verify widening) → **NP-11 dropped**, the parity gap is **declared** in the evidence pack, never a silent omission | phase1 §15.3 (resolved marker), `decisions.md` §6 | No residual open question; the deferred item carries a named follow-up (future id `143a045c`) |
| **U-4** | **Exact restart class slugs** — Phase 1 fixes the *shape* ("a stable class slug + the run_id") but not the strings | phase1 §7.2 | R-* legs assert (a) exactly **one** new `halt` row, (b) it carries `run_id=${RUN_ID}`, (c) it carries `class=<[a-z0-9_.-]{3,40}>` (shape, not literal), (d) distinct classes produce **distinct** slugs (cross-leg comparison, no hard-coded vocabulary). A separate **vocabulary-lock** sub-leg pins the literals once, after they land |

**Resolved-by-citation (no longer assumptions):** A-ASSUME-1 (labels: phase1 §4.1/§4.3), A-ASSUME-2 (event kind
unchanged: phase1 §4.2), A-ASSUME-3 (containment predicate: phase1 §5.3 R2 = `MainPID`-equality OR `/proc/<pid>/cgroup` last-line
**substring-contains** the unit ControlGroup — so NP-6's prefix-collision is a **real** risk, see §9.1), A-ASSUME-4
(grace = `_GATE_ATTR_DEFER_S` pooled, not a knob), A-ASSUME-5 (order: ladder runs **only** on a non-empty `nonmatch`;
no rescue), A-ASSUME-6 (stash: `_handback_stash` over the existing two channels), A-ASSUME-7 (restart rows: `halt`
kind + slug, 7 sites), A-ASSUME-8 (fail-fast: Phase 1 does **not** touch `:6107-6126` — so the "exactly one MainPID
poll" assertion **holds unqualified**), A-ASSUME-9 (accept criterion: containment ladder).

### 5.3 Frozen pins Phase 2's own additions must respect

| Pin | Sites | Consequence for new WARN/log text |
|---|---|---|
| **7b substring trap** — no success-path log line may contain the literal `systemctl start` | `tests/test_supervision_stop_handback.sh:288/:768/:840/:862`, `tests/test_supervision_e2e.sh:474/:548`, `tests/test_supervision_journal.sh:456`, `tests/packs/handback_verify_budget_test.sh:522/:534/:603`, `tests/test_bounded_subprocess_waits.sh:377`, `tests/test_promote_cgroup_survivorship.sh:577` | Every new label string is checked against the trap **before** landing; the new pack carries an explicit `assert_not_contains "systemctl start"` on **each** success path it drives (if a B-design adds a success path) |
| **7c byte-pinned failure wording** — `falling back to nohup launcher (comp7 opt-in path)` | `lib.sh:6748`, `:6753`; pinned `tests/test_bounded_subprocess_waits.sh:448`, `tests/test_supervision_stop_handback.sh:894` | The restart-arm label change at `:6699-6701` must not touch the 7c tail; the leg asserts the frozen tail is still present **and** the new cause token is present |
| **Source-textual knob defaults** | `tests/test_ceremony_wallclock.sh:160-186` (11 knobs × shipped-default + resolved-value + table equality), `:183` (≥6 self-refs), `:199` (all-worst 1340), `:208` (−440) | If Candidate B introduces or changes a knob, `ceremony_wallclock` reddens by design — the plan's C-10a leg (§7.5) makes that red an **expected, planned** delta with the table edit in the same commit |
| **Prompt-scale / test wall-clock** | `tests/test_ceremony_wallclock.sh` asserts budget **values**, not runtime; but `test_adaptive_soak.sh:489-506` still carries a *different* C-10a arithmetic (870/30/1400) | Do **not** touch `test_adaptive_soak.sh` (out of scope); note the divergence in the evidence pack's routed findings (§13 item 8) |

---

## 6. Candidate A — label-honesty tests

**Files:** new `tests/packs/handback_false_halt_test.sh` §A (all legs in one pack, one fixture family).

### 6.1 Leg matrix (re-cut against Phase 1's LANDED taxonomy, `phase1-plan.md` §4.1/§4.3)

Phase 1's taxonomy: **124 →** `kind=budget` (legacy WARN **byte-identical**); **3 →** *no journal row*, a `_log`
line only; **4 / other →** `kind=nonzero_exit` with the true rc. Applied at three sites via two new **pure string** helpers
(`_bounded_rc_kind`, `_bounded_rc_reason`, T1 in phase1). Legs are therefore split: the helpers get a **direct
unit leg** (cheap, sharp), and each call site gets a **driven** leg (proves the wiring).

| Leg | Target | Fixture | Assertions |
|---|---|---|---|
| **A0** | `_bounded_rc_kind` / `_bounded_rc_reason` (new, next to `_run_bounded`) | source lib.sh, direct call | `(124) → budget`; `(3)(4)(7)(1)(0) → nonzero_exit` / the corresponding reason strings; **no I/O** (no fork: assert by wall-clock < 50 ms × 100 calls, and no file writes); **`_run_bounded` itself untouched** (`I-A5`: assert `git diff` has no hunk in `:4856-4882`) |
| **A1** | `lib.sh` reset-failed site (Phase 1 §4.3 item 1) | `SC_RESET_FAILED_RC=1`, `SC_RESET_FAILED_ERR="Failed to reset failed state: Access denied"` | (i) `rc=1` in the row (not the hard-coded `124`); (ii) `kind=nonzero_exit`; (iii) **no** `timeout(`; (iv) the hand-back **continues** (best-effort by design); (v) **stderr reachability** — Phase 1's C-2 adds an errfile capture to this site (today `lib.sh:6332` redirects both streams to `/dev/null`), so the child's first stderr line is available to the stash (leg C-2 carries the assertion; A1 only checks the row is not poisoned by it) |
| **A2** | reset-failed, timeout arm | `SC_HANG=1` on `reset-failed` | legacy: `kind=budget`, `rc=124`, legacy WARN text preserved ⇒ the honest branch is **not** silenced (anti-over-correction) |
| **A3** | `is-active` pre-flight site | `SC_IS_ACTIVE_RC=3`, `SC_IS_ACTIVE=inactive`, holders attributable, curl `ok` + expected version | (i) **no `kind=budget` row anywhere**; (ii) **no `timeout(` token**; (iii) **policy-pinned (U-1):** zero new journal rows at all, and a `_log` line naming the state (`inactive`/`activating`); (iv) the run **completes** (rc 0); (v) **REQUIRED (architect §3):** the INFO line carries the **site token** (`handback_is_active` / `scope_arm_is_active` — the tokens `tests/test_bounded_subprocess_waits.sh:327/343/428` assert), the **state**, and the **rc**, and the fallback literal is `unknown` (never the literal string `inactive|activating`) |
| **A4** | `is-active`, rc 4 / other | `SC_IS_ACTIVE_RC=4` (state `unknown`) and `SC_IS_ACTIVE_RC=1` (state empty) | exactly **one** row, `kind=nonzero_exit`, `rc=<true rc>`, state hint present; never `timeout(`; behaviour unchanged (blank `isact`, continue into the start attempt) |
| **A5** | `is-active`, timeout arm | `SC_HANG=1` on `is-active` | `kind=budget` + `rc=124` + the **legacy WARN verbatim** (this is what `tests/test_bounded_subprocess_waits.sh:429` pins — it must stay green untouched) |
| **A6** | restart scope-arm site (`lib.sh` `restart_via_launcher` comp7 arm) | `SC_IS_ACTIVE_RC=3` with `ENSEMBLE_RESTART_UNIT` set, Linux + `/run/systemd/system` + the unit env (reachability precondition) | same as A3 (no budget row, no timeout token, `_log` line, `_sc_isact` blanked, arm proceeds); frozen **7c** tail still present on the failure path; success path **7b-clean** |
| **A7** | hygiene lock (`I-E3`) | source-level | (i) **no new journal event kind** (the event stays `subprocess_wait_timeout`; the `kind=` row token is the changed surface); (ii) no new `_numeric_knob`; (iii) no new exported env var (contrast: `_GATE_ATTR_DEFER_S` is env-visible but **not exported** and **not** a knob — assert exactly that shape); (iv) `daemon/**` untouched (`git diff --stat` empty for `daemon/`) |
| **A8** | start arm (`lib.sh:6383-6391`), non-124 arm — **D2 symmetric row** (added, review wave 1 fold) | `SC_START_RC=1`, `SC_START_ERR=…` (the §4.3 knob contract) | exactly **one** `kind=nonzero_exit` row naming the true rc beside the unchanged WARN (D2: the non-timeout start failure becomes machine-visible); the rc-124 arm keeps its legacy `kind=budget` text verbatim (`SC_HANG=1` sub-case) |
| **A9** | scope-arm start (`lib.sh:6747-6753`), non-124 arm — **D2 symmetric row** (added, review wave 1 fold) | same knobs on the comp7 path | same as A8 on the scope-arm site; frozen **7c** tail intact; success path 7b-clean |

### 6.2 Counter-mutations (each must redden exactly the named leg)

| Mutation | Plant (on a **copy** of lib.sh) | Expected red |
|---|---|---|
| **M-A0** | make `_bounded_rc_kind` return `budget` unconditionally | A0 |
| **M-A1** | re-hardcode `rc=124` at the reset-failed row (today's defect, restored) | A1 (i) |
| **M-A1b** | collapse the reset-failed branch back to `-ne 0 → kind=budget` | A1 (ii)(iii) |
| **M-A3** | collapse the `is-active` branch back to the single `-ne 0 → kind=budget` body | A3, A4, A6 |
| **M-A3b** | *policy flip simulation*: add a `kind=nonzero_exit` row for rc 3 | A3 (iii) only — proving the policy-pinned assertion is the one that bites (U-1) |
| **M-A5** | change the rc-124 arm's wording at all (one character) | A2, A5 (the `I-A3` byte-identity pin) |
| **M-A6** | same single-branch collapse at the scope-arm site | A6 |
| **M-A7** | introduce a NEW event kind for the nonzero arm (e.g. `subprocess_nonzero`) | A7 (i) — and this is the mutation that proves `I-E3` is enforced, not merely documented |
| **M-A8** | delete the non-124 `kind=nonzero_exit` row at the start arm (restore the WARN-only shape) | A8 |
| **M-A9** | same plant at the scope-arm start | A9 |

**Guard against a vacuous A-leg:** every driven A-leg also asserts the stub's `SC_LOG` shows the probe actually
ran (`is-active` / `reset-failed`), so a leg cannot pass on a path that never probed.

### 6.3 Two structural notes that replace old assumptions

1. **The version conjunct's logic is untouched.** Phase 1's edits are confined to `lib.sh:6332-6339`,
   `:6356-6360`, `:6695-6703`, the attribution gate (`:8004-8137`), the **stash writes** inside
   `_supervision_unit_serving`'s failure arms (C-5), **plus the rc=2 consumer sites (REVIEW WAVE 1, BL-1 —
   phase1 §5.4a): `_handback_serving_probe`'s gate consumer (`lib.sh:6103-6106`, T7b), `gate_version`'s
   attribution arm (`:8159`, T7c), and the one-shot consumer blocks (`promote.sh:790-794`, `:799-848`, `:853`,
   `:864`, `:1060-1072`; `restart.sh:284-300`; `rollback.sh:267-278`, T7c)**. The version comparison itself
   (`_handback_serving_probe`, `lib.sh:6107-6117`) keeps its logic, so the "exactly one MainPID poll"
   fail-fast assertion in NP-1/NP-2 **holds unqualified** — the earlier A-ASSUME-8 conditional is retired.
   The T7b restructure sits at `:6103-6106`, BEFORE the version conjunct, and must not alter the poll count:
   on every all-attributable green the probe still answers rc 0 on the first census.
   *(Caveat, verified: C-5 does add a stash write inside the SAME function's return-1 arms, so NP-1/NP-2's
   assertions must tolerate added **stderr/stash** text — they assert the WARN needle and the poll count,
   never a byte-identical whole transcript. `B-order`'s byte-identical assertion applies to the
   **success** path only, where C-6 clears rather than writes and the accept INFO rides stderr (`I-B2b`).)*
2. **`_run_bounded` is untouched** (phase1 `I-A5`), so every rc the sites see arrives exactly as today —
   including the `137|143 → 124` collapse at `lib.sh:4876-4878`, which is what makes `kind=budget` honest
   for a killed child.

---

## 7. Candidate B — survival / exhaustion / attribution-path / portability

Phase 1's landed design (B-primary): a **containment ladder** `R1 owned → R2 systemd cgroup → R3 ppid-ancestry →
R4 launchd job`, consulted **only when Layer 1 found a non-match**; any `1` ⇒ today's refusal detail prefix
preserved; all `0` ⇒ `_log` ACCEPTED + skip the Layer-2 unit-arm re-test (closure C-2); no `1` but some `2` ⇒ the
**pooled deferral** (`_GATE_ATTR_DEFER_S`, default 5 s, **per-shell**, seconds-counted, clamped `[0,5]`, test-only env seam —
NOT an exported production knob per D3), whose **exhaustion is transient → budget-bound** (leader D1: the gate returns 2 and
polling continues within the existing budgets; refusal/halt only at budget end). Rung composition follows the C-1 rule: the
**first rung that ANSWERS decides**, and an answered `1` is **downgraded to `2`** when a stronger rung was unanswerable.
This section's legs are re-cut to that design — there is no longer a "which accept criterion?" question.

### 7.1 B1 — HEALTHY-BOOT SURVIVAL (the incident, positive direction)

| Item | Value |
|---|---|
| Fixture | census stub prints `HB_OWNED_PID` + `HB_CHILD_PID` + `HB_CHILD_PID2` (three lines = the incident's two-holder shape `1191207 1191605` plus one); owned-set seam prints **only** `HB_OWNED_PID` |
| Containment | `SC_MAINPID=$HB_OWNED_PID`; `SC_CONTROLGROUP` = the runner's **real** cgroup string (resolved as in B3); `SUPERVISION_MODE=unit`, `SUPERVISION_UNIT=ensemble-hb.service` |
| Green | curl `ok` + `PROMOTE_EXPECTED_VERSION` matching |
| Assertions | (i) `restart_via_launcher` → **rc 0**; (ii) `unit hand-back COMPLETE` present; (iii) **no** `attribution … REFUSED` anywhere (`I-B1`); (iv) the **ACCEPTED log line** phase1 §5.3 specifies (`attribution [<what>]: ACCEPTED — port-holder(s):… contained in unit 'Y' (cgroup containment; the argv-anchored owned set does not cover them)`) is present; (v) success path **7b-clean** (`assert_not_contains "systemctl start"`) |
| Mutation | **M-B1** — rip the ladder out of the non-match branch (restore the pre-fix accept-set: `nonmatch` non-empty ⇒ refuse) ⇒ B1 reddens on (i)/(iii) |
| Non-vacuity | census counter ≥ 1 **and** `SC_LOG` shows `show -p MainPID` **and** `show -p ControlGroup` were issued ⇒ the R2 rung really ran (a "pass" that never probed would be vacuous) |

**B1b — the "mid-spawn" timing shape.** Holders become attributable only after `N` census calls (counter stub,
`GD_DELAY_CALLS=N`). Assert rc 0 **and** counter ≥ N+1 ⇒ the code waited rather than got lucky on the first
census. This is the clock-free stand-in for the incident's mid-spawn window.

**B1c — holder-identity honesty (new, cheap, high value).** The *same* fixtures as B1, but the two children are
**real pids** (`sleep 30` fork-children of the test shell, so `/proc/<pid>/cgroup` is genuine) with an argv that
is **deliberately not** ensemble-shaped. This is the closest possible reproduction of the incident *given* that
the fixture logs never recorded pid→process identity (research-findings-lane §Q1 limitation). Assertions: rc 0 via
**R2 cgroup containment only** (assert `SC_LOG` shows the ControlGroup probe; assert the owned seam printed a set
that provably excludes both pids).

### 7.2 B2 — EXHAUSTION (positively-foreign refuses immediately; deferral exhaustion is transient — leader D1)

| Item | Value |
|---|---|
| Fixture | census yields holders that are **positively not contained** at every rung (owned seam excludes them; `SC_CONTROLGROUP` points at a non-matching suffix; there is no owned ancestor) |
| Assertions | (i) rc **1** (`I-B10`); (ii) `NO nohup fallback` present; (iii) the verifier's bounded verdict present (`not confirmed`) **or** the ladder's refusal detail; (iv) **no** `hand-back COMPLETE`; (v) **bounded**: total census calls ≤ a ceiling **and** total wall-clock ≤ a generous sanity bound (never an equality) |
| Variant **B2b** (caller contract) | `_supervision_handback_unit` returns 1 ⇒ caller halt + B4 leave-txn-open (`promote.sh:692-699`). Proven structurally here; driven in Tier-3 (§11.4) |
| Mutation | **M-B2** — make exhaustion succeed (`return 0` at the deadline) ⇒ B2 (i)(iv) redden |

### 7.3 B3–B8 — the ladder rungs + portability, one leg each (all now REQUIRED)

| Leg | Rung | How to drive it (no `/proc` stubbing) | Assertions |
|---|---|---|---|
| **B3** | **R2 systemd cgroup** | Two **real** `sleep 30` children of the test shell; feed `SC_CONTROLGROUP` = their real cgroup (last line of `/proc/self/cgroup`, after the final `:`); `SC_MAINPID` = an unrelated real pid | contained ⇒ ladder `0` ⇒ accept. Then re-point `SC_CONTROLGROUP` to a non-matching suffix ⇒ not contained ⇒ refuse with `OUTSIDE unit … lineage` (or Phase 1's suffix text) |
| **B4** | **R2 MainPID equality** | `SC_MAINPID` = the single holder pid | equal ⇒ accept; holder ≠ MainPID and not contained ⇒ refuse |
| **B5** | **R3 ppid ancestry into the owned set** | `_supervision_pid_ancestor_of`'s *positive* case needs no systemd at all: a **real child** of the test shell as the holder, owned seam prints `$$` ⇒ reachable ⇒ accept. Then a **`setsid`-escaped** child (precedent `tests/test_promote_launchd_survivorship.sh:684-700`), owned seam still `$$` ⇒ unreachable ⇒ refuse | accept / refuse as stated; **`ps` absent** (PATH without `ps`) ⇒ rung answers `2` (a missing tool is never refusal evidence) ⇒ the deferral rung, not a refusal |
| **B6** | **R4 launchd job lineage** | `uname` stub → Darwin; `SUPERVISION_MODE=launchd`; launchctl stub printing `pid = <real pid>`; holder = a real grand-child of it ⇒ accept; holder **outside** that lineage ⇒ refuse; **pidless** job (`pid = 0`) + a served port ⇒ refuse naming the crash-loop shape (`I-B5`; reuses the M7 pin shape at `test_supervision_stop_handback.sh:1690-1700`) | three cells, each asserted separately |
| **B7** | **Portability arm selection** | `uname` stub → Darwin: assert via `SC_LOG` that **no** `show … ControlGroup` was issued and no `/proc` read path ran (`I-B11`). `uname` stub → `Plan9`: `_supervision_host_allows_unit` false ⇒ the unit arm is unreachable and there is no crash | two cells; the SC_LOG assertion **is** the proof (no separate instrumentation needed) |
| **B8** | **Unprobeable / degraded arm** | census stub `exit 2` with stderr ⇒ gate returns 0 with the **one-time** loud skip warn (existing behaviour, `_GATE_ATTR_WARNED`); `SC_SHOW_RC=124` / `SC_MAINPID=0` ⇒ the R2 rung answers `2` (unresolvable) and the deferral rung applies | exactly **one** skip warn across two gate calls (precedent M11b, `:1758-1766`) |

**Non-goal retained:** Phase 2 does not assert *which* rung answers when several could — only that the
**classification** (`0` / `1` / `2`) is right per phase1 §5.3's contract. If a rung is absent from a future
lib.sh, its leg **skips loudly with the rung named**, never silently passes.

### 7.4 B-order — the ordering invariant (`I-B2`; NEW, and it protects the whole corpus)

Phase 1 §5.2 rejected pure reordering precisely because it would change the common path. `I-B2` makes that a
pinned property:

- **Fixture:** the all-owned shape (holder ∈ owned seam ⇒ `nonmatch` empty).
- **Assertion 1 (no extra probes):** `SC_LOG` contains **no** `show … -p ControlGroup` (and no MainPID probe
  beyond what the verifier already does) for the accept path. A pure-reorder design could not pass this.
- **Assertion 2 (byte-identical output):** capture the success transcript and compare against a
  **pre-fix baseline transcript** (generated once from the base lib.sh in a base worktree, stored as a fixture;
  the diff must be empty).
- **Mutation M-B-order:** move the ladder **before** Layer 1 (the rejected pure-reorder) ⇒ assertion 1 reddens.

This leg is the reason the fix's blast radius stays small; it must not be dropped.

### 7.5 B-defer-a / B-defer-b — the pooled deferral (`I-B8`)

| Leg | Fixture | Assertion |
|---|---|---|
| **B-defer-a (bound)** | rung answers `2` forever: `SC_CONTROLGROUP=""` (unobservable) **and** no `ps` on PATH **and** owned seam empty | total deferral per **shell** ≤ `_GATE_ATTR_DEFER_S` (resolve the setting via `P2_DEFER_SETTING`: the env var — one grep at authoring time; U-2). Drive **three** gate calls in one shell and assert total slept ≤ the bound (not 3× it) — the pool, not per-call patience. Then the distinct detail `… containment UNATTRIBUTABLE within <n>s …` **and a transient verdict (rc 2, NOT rc 1)** — leader D1: exhaustion ⇒ transient; the refusal happens **only at budget end** through the floor-wins path. Additional assertions (architect §2): a leaked `300` clamps to 5; a non-numeric value takes the loud-garbage path; the pool is counted in **seconds**; the `until`-form infinite loop is impossible |
| **B-defer-b (short-circuit)** | rung answers `0` on the second census | the loop **stops** on `0` (no exhaustion, rc 0) — and the counter shows exactly 2 censuses, not the full pool |
| **B-defer-c (dual-key, added)** | `ENSEMBLE_TEST_GATE_DEFER` marker absent | a non-default `_GATE_ATTR_DEFER_S` is **ignored** (the pool stays at the 5 s default) — the leaked-`0` false-halt direction is closed |
| **Mutation M-B-defer** | make the pool per-**call** instead of per-shell (the rejected shape) | B-defer-a reddens on the 3-call total |
| **Mutation M-B-defer-D1** | restore the **terminal** fail-fast exhaustion (exhausted `2` ⇒ rc 1) | B-defer-a reddens on its transient verdict (leader D1 — terminal exhaustion is rejected) |
| **B-defer-e (rc=2 consumer contract — REVIEW WAVE 1, BL-1; `I-B13`; phase1 §5.4a / T7b+T7c)** | persistent `2` from the gate: same forever-`2` fixture as B-defer-a, driven **through the probe** and, as a one-shot twin, through `gate_version`/the livez consumer block | **(i)** the probe returns **2**, not 1 — no `attribution … REFUSED` line anywhere (the T7b rc capture at `:6103-6106` maps the transient per the `:6235`/`:6256` contract); **(ii)** the verifier polls **≥2 iterations** after the first transient (poll/census counter); **(iii)** the halt happens **only** at budget end through the floor-wins verdict (`:6288-6297`) — never a fail-fast; **(iv)** the halt detail carries the `UNATTRIBUTABLE … deferring` exhaustion marker via `HB_VERIFY_REASON` (C-5) — `none captured` never appears; **(v)** one-shot twin: `gate_version`'s consumer (and one livez consumer block) retries within the phase budget and surfaces the **distinct transient wording** — zero occurrences of `version verify mismatch` / `"/livez unreachable >Ns"` / `gate_fail_reason` set from an rc-2 (the mislabel fold), no 8b auto-rollback |
| **Mutation M-B-defer-E** | restore the pre-fix consumer form `if ! _gate_serving_attribution …` at `lib.sh:6103-6106` (any nonzero gate rc ⇒ probe rc 1) | **B-defer-e reddens on (i)/(ii)** — proving the rc=2 mapping is enforced by a test, not merely documented (the counter-mutation the wave demands) |

### 7.6 B-parity — gate surface parity (`I-B9`; phase1 §5.8 explicitly requests this)

Because the fix lives in the shared gate, the same widening reaches `gate_livez`, `gate_readyz` and
`gate_version` (and hence `gate_soak`). Legs:

- **B-parity-accept:** the B1 fixture shape, but driven through `gate_livez` **and** `gate_version` ⇒ both rc 0.
- **B-parity-refuse:** the NP-4 fixture shape ⇒ both rc 1 with the attribution detail.
- **B-parity-capture (added — REVIEW WAVE 1, BL-2; `I-B2b`):** on the accepted-widened green, capture the consumers'
  payloads exactly as production does — `LIVEZ_JSON="$(gate_livez)"`, `READYZ_JSON="$(gate_readyz)"`, and the verifier's
  `newpid="$(_supervision_unit_serving …)"` — and assert (a) each captured payload is the probe body alone (parses as one
  JSON object; no `attribution […]` INFO prefix), (b) the captured `newpid` is a **single numeric token**, and (c) the
  `ACCEPTED` line is present on **stderr** only (capture stderr separately and grep it there). The gate-emitted
  transient/exhaustion marker obeys the same rule (phase1 §5.4).
- **Mutation M-NP4** (drop the attribution call) reddens both.
- **Mutation M-B2b** (the ACCEPTED line rides `_log` — stdout-when-unset, `:434-443`) reddens **B-parity-capture** (a)
  and (b): the captured payload gains an INFO prefix / the pid token becomes multi-line — proving stdout purity is
  enforced, not documentary.

Without this leg the post-hand-back half of the same ceremony could re-fire the incident and no test would see it.

### 7.7 B10 — the C-10a two-copy sync pin (NEW; fills a real hole)

**Finding (grep + byte-diff verified):** the C-10a bound table exists **twice** — `lib.sh:157-173` and
`promote.sh:309-325` (17 lines each, **byte-identical at `0181dadbf`**; proven by an in-planning range-diff,
zero hunks) — and `promote.sh:326-327` claims *"This block is byte-identical to the copy in lib.sh (the 'single
source of truth' holds only while BOTH carry the same table)"*. But **no pack compares the two**:
`tests/test_ceremony_wallclock.sh` binds the table to `lib.sh` only, and `grep -rl "BOUND TABLE" tests/` → **zero
files**. Silent drift is therefore invisible today.

- **Leg B10:** extract both blocks by anchor comment (`# C-10a BOUND TABLE` … the `downstream boundary with the
  daemon up` line) and assert **byte equality**.
- **Mutation M-B10:** plant one word into the `promote.sh` copy ⇒ B10 reddens.
- **Trigger:** mandatory here **because Phase 1's T9 edits both copies** (a one-line deferral note) — this leg is
  the proof that the "same edit in two places" actually happened in both.

### 7.8 B-halt — the halt chain is untouched (`I-B10`)

Structural + driven: the chain must be probe rc 1 → verifier rc 1 → `_supervision_handback_unit`
rc 1 → caller halt + B4, with **no** nohup fallback and **no** new intermediate exit path. Assertions: the
greppable chain sites are unchanged (`git diff` shows no hunk introducing a new `return`/`exit` between the
verifier and the caller), and the driven B2 leg still prints `NO nohup fallback`. **Amended by leader D1:** the
chain is entered **at budget end** (the floor-wins verdict), not by a terminal deferral exhaustion — a deferral
exhaustion yields the **transient** rc 2 and the chain fires later, through the unchanged budget-end path. The
leg asserts **both**: exhaustion ⇒ rc 2 (no early halt), and budget-end ⇒ the full chain above.

### 7.9 Consolidated counter-mutation table for §7 (one row per acceptance)

| Mutation | Plant (on the copy) | Must redden |
|---|---|---|
| **M-B1** | in `_gate_serving_attribution`'s non-match branch, restore the pre-fix verdict (`nonmatch` non-empty ⇒ `return 1` before the ladder) | B1, B1b, B1c |
| **M-B1c** | make only the **R3 ppid** rung respond (skip R2) | B1c (which asserts R2 specifically) — proves B1c discriminates the rung, not just the outcome |
| **M-B2** | make grace/deadline exhaustion return 0 | B2 |
| **M-B3** | invert R2's containment test (`case "$pcg" in *"$cg"*) ;; *) accept ;; esac`) | B3 (positive cell), NP-4 |
| **M-B4** | drop the `[ "$h" = "$main_pid" ] && continue` equality arm | B4 |
| **M-B5** | make `_supervision_pid_ancestor_of` return 0 unconditionally | B5 (refuse cell), NP-4 |
| **M-B6** | treat an empty launchd job pid as acceptance | B6 (pidless cell), and the existing M7 pin in `test_supervision_stop_handback.sh` |
| **M-B7** | force the unit arm path on any `uname` (drop the host guard) | B7 (Darwin cell) — the `SC_LOG` "no ControlGroup probe" assertion flips |
| **M-B8** | make an rc-2 census return 1 instead of 0 (a missing tool becomes refusal evidence) | B8 — reverses the house "a missing tool is never refusal evidence" doctrine |
| **M-B-order** | move the ladder **before** Layer 1 | B-order assertion 1 (a ControlGroup probe appears on the all-owned path) |
| **M-B-defer** | make the deferral pool per-**call** instead of per-process | B-defer-a (3-call total) |
| **M-B-defer-E** | restore the pre-fix `if ! _gate_serving_attribution …` consumer at `lib.sh:6103-6106` (any nonzero ⇒ probe rc 1) | B-defer-e (i)/(ii) — the rc=2 mapping is enforced (REVIEW WAVE 1, BL-1) |
| **M-B2b** | emit the ACCEPTED INFO via `_log` (stdout when `ENSEMBLE_UPGRADE_LOG_FILE` unset, `:434-443`) instead of the `printf … >&2` idiom (`:6210` precedent) | B-parity-capture (a)/(b) — stdout purity enforced (REVIEW WAVE 1, BL-2) |
| **M-B10** | one word into the `promote.sh` C-10a copy | B10 |
| **M-NP4** | delete the `_gate_serving_attribution` call in `_handback_serving_probe` | B-parity-refuse, NP-4, NP-9 |

### 7.10 No `ceremony_wallclock` delta expected (B9 retired to a guard)

Phase 1 does **not** change any budget number (A21 untouched; the deferral is a non-knob seam and the C-10a edit
is comment-only). So `tests/test_ceremony_wallclock.sh` should stay **green with zero edits**. That is itself the
assertion: if it reddens, a number moved that Phase 1 said it would not — a finding, not a delta to absorb.
`TABLE_<KNOB>` recomputation is only needed if the numbers do move.

---

## 8. Candidate C — systemd-lane error stash

Phase 1's landed design (§6): a `_handback_stash()` wrapper writing **both** existing channels
(`HANDBACK_LAST_ERROR` + the `$$`-keyed file), with six touchpoints **C-1…C-6**; readers unchanged. This
section's legs map 1:1 onto those touchpoints, plus the subshell-survival leg (`I-C5`) that only the file
channel can pass.

### 8.1 Leg matrix (mapped to Phase 1's touchpoints)

| Leg | Phase 1 touchpoint | Fixture | Assertion |
|---|---|---|---|
| **C-1** | **clear on entry** to `_supervision_handback_unit` | seed both channels with a poison string, then run a hand-back | at the first stash-worthy event the text is **this run's**, never the seed ⇒ the poison string must be absent from `_handback_last_error_text` after a failed hand-back |
| **C-2** | reset-failed site, **both** arms | `SC_RESET_FAILED_RC=1` (+ `SC_RESET_FAILED_ERR` once Phase 1 adds the errfile capture), then `SC_HANG=1` | text names `reset-failed` + the true rc (or `timeout(<budget>s)` for the hang arm) |
| **C-3** | `is-active` site, **both** arms — *including the benign rc 3* | `SC_IS_ACTIVE_RC=3` then `4` | stash carries `is-active: rc=3 state=inactive` (the *context* of a later failure, per Phase 1 §6 C-3) and `is-active: rc=4 state=unknown` on the next |
| **C-4** | **start** non-124 arm — the `first` stderr line (already captured, today discarded) | `SC_START_RC=1`, `SC_START_ERR="Job for ensemble-x.service failed because the control process exited with error code."` | the first stderr line is in the stash (highest-value lost diagnosis) |
| **C-5a** | **verifier refusal** — `HB_VERIFY_REASON` + an explicit file write from **inside** the verifier | foreign holder (NP-4 fixture) | stash carries the verifier's reason (attribution detail / floor-band verdict), **and** `_handback_last_error_text` returns it from a **subshell** (`$( )`) read ⇒ this is `I-C5` (the MAJOR #1 regression must stay fixed) |
| **C-5b** | floor-band verdicts | `PROMOTE_CEREMONY_DEADLINE_EPOCH` squeezed into band 2/3 | stash names `ceremony time exhausted`, not a generic failure. **Extended (REVIEW WAVE 1, BL-1):** when budget end follows a persistent-2 stretch (B-defer-e fixture), `HB_VERIFY_REASON` carries the gate's `UNATTRIBUTABLE … deferring` exhaustion marker — the halt text says WHY the budget ran out, not just that it ran out |
| **C-6** | **clear on success** | run a green hand-back after a failed one | both channels empty (mirror of `lib.sh:6558`; precedent N5) |
| **C-7** | end-to-end template render | a failed unit hand-back driven through the lib, then the halt template composed | the detail contains the captured text and **does not** contain `none captured` (`I-C1`) |
| **C-8** | the accessor contract (**regression obligation, not new**) | structural | the three templates still read `HB_ERR="$(_handback_last_error_text)"` and render `hand-back error: ${HB_ERR:-none captured}` — pinned by `test_supervision_stop_handback.sh` §N3 (`:2114-2124`), which must stay **green with zero edits** |

### 8.2 Mutations

| Mutation | Plant (on the copy) | Expected red |
|---|---|---|
| **M-C1** | delete the entry clear (C-1) | C-1 (the seed surfaces) |
| **M-C2** | delete the success clear (C-6) | C-6 |
| **M-C3** | delete the verifier-refusal stash (C-5) | C-5a, C-7 |
| **M-C4** | make `_handback_stash` write **only** the variable channel (drop the file write) | C-5a only — this is the mutation that proves `I-C5`'s subshell-survival requirement is enforced, not just asserted |

**Regression obligation:** `test_supervision_stop_handback.sh` §N (N1/N2/N2b/N3/N5, `:2020-2135`) pins the
**launchd** stash channel. Those nodes must remain green; a red there means the new systemd writer broke the
Darwin path (both share `HANDBACK_LAST_ERROR` / the file).

---

## 9. NEGATIVE PROOF — a genuinely wrong release must still halt

> **Commission-mandated section.** Two-sided obligation: (a) every wrong-release class still halts, and
> (b) the containment ladder never **over-accepts** — demonstrated by counter-mutations that widen the
> accept-set and **must** redden this section. Phase 1's own §5.7 "Protection matrix" (P1–P12) and its §14 negative-proof template (N1–N9) are the design's promise; this section is where the promise is
> converted into red-able legs.

### 9.1 Scenario matrix (each row = one leg)

**NP-6's binding predicate — the canonical string, carried character-for-character identical to `phase1-plan.md` §5.3 and
`decisions.md` §2.2(7) (architect closure C-0). The table cell below carries the markdown-escaped rendering form of the same
string (`\|\|` = `||`); this block is the canonical, copy-verbatim source:**

```
pcg == cg || pcg contains "$cg/"
```

| id | Scenario | Fixture | Protection that must catch it | Expected outcome |
|---|---|---|---|---|
| **NP-1** | **wrong-version green** | `CURL_MODE=wrongver` (`version:"0.0.0"`), holders **all attributable** | version conjunct, `lib.sh:6113-6116` | verifier rc **1**; `version=0.0.0`; `NOT serving the right release`; **no** `hand-back COMPLETE`; **exactly one** MainPID poll (Phase 1 does not touch this block ⇒ the count assertion holds **unqualified**) |
| **NP-2** | **version-absent green** | `CURL_MODE=noversion`, `PROMOTE_EXPECTED_VERSION` set | `lib.sh:6109-6111` | rc 1; `version field is absent/unparseable` |
| **NP-3** | **non-2xx** | `CURL_MODE=non2xx` (404) | `_probe_once_classified` red (`:4488-4490`) → probe rc 1 (`:6120-6126`) | rc 1; `hard fail (red …)` |
| **NP-4** | **genuinely-foreign holder** — positively `1` on every rung: not owned, not in the unit cgroup, no owned ancestor | holder = a real `setsid`-escaped pid (precedent `tests/test_promote_launchd_survivorship.sh:684-700`); owned seam excludes it; `SC_CONTROLGROUP` = a non-matching suffix | ladder returns `1` on **R2** and / or **R3** ⇒ Layer-1 refusal path | rc 1; detail **starts with** the live prefix `port-holder(s):… NOT in the supervision-owned pid set (owned:…)` (`I-B3`) plus Phase 1's ladder suffix; **census polled exactly ONCE** — `I-B4` explicitly forbids a deferral burn on a positive `1` (phase1 §5.4: the loop "stop[s] on `0`/`1`") |
| **NP-5** | **no-listener green** | census rc 0 **empty** while curl answers green | `lib.sh:8012-8019` `finds NO listener` | rc 1; `unattributable` |
| **NP-6** | **prefix-collision containment — HARD PIN (arm (b)-only; binding amendment D-P2-3, architect closure C-0)** | **drivable without `/proc` stubbing**: stub the unit ControlGroup (`SC_CONTROLGROUP`) to a **non-segment-aligned prefix of the test's real cgroup leaf** (e.g. real leaf `…/ensemble-main.service-evil.scope`, stubbed unit CG `/system.slice/ensemble-main.service`) ⇒ raw substring matches (accept), segment-aware refuses. (Adopted architect recommended item 15.) | R2's binding predicate — **character-for-character identical to phase1-plan.md §5.3 and decisions.md §2.2(7)**: `pcg == cg \|\| pcg contains "$cg/"` (canonical form in the block above) ⇒ the prefix-extension collision is **refused** | Leg asserts the **refusal** (rc 1, today's refusal-detail prefix + the ladder suffix) and prints the arm for the evidence pack; **reddens under M-NP1b** (permissive rung) and under **M-NP1c** (composition downgrade removed). The former two-armed text (arm (a) substring ⇒ advisory) is **SUPERSEDED** — arm (b) is the contract (decisions.md §2.2(1)) |
| **NP-7** | **halt end-to-end** — the commission's literal demand | NP-1 or NP-4 driven to the caller | `_supervision_handback_unit` rc 1 (`:6395-6397`) → caller halt + B4 leave-txn-open (`promote.sh:684-699`) | **NP-7a (lib):** `restart_via_launcher` rc **1**, `NO nohup fallback` present, **no** `hand-back COMPLETE`. **NP-7b (promote, Tier-3 §11.4):** non-zero exit; a `halt` history row containing `unit hand-back failed`; `in_flight` **still open** (B4); no `commit` row; `current` unchanged |
| **NP-8** | **a late-attributable holder must not convert a wrong version into a pass** | `CURL_MODE=wrongver` **plus** holders that only become attributable after `N` censuses (or a deferral-setting bump) | version conjunct runs **after** attribution on every green (`:6107-6126`) | rc 1 + `version=0.0.0` — and the counter shows the code **did** wait (≥N+1 censuses), i.e. the extra time was spent and *still* correctly refused |
| **NP-9** | **gate-surface parity on the refusal** (from `I-B9`) | the NP-4 shape driven through `gate_livez` **and** `gate_version` | the same shared gate | both rc 1 with the attribution detail — the widening must not have softened the *other* gates' refusals |
| **NP-10** | **pre-stop MainPID equality** (phase1 §5.7 P9) | `mainpid == prestop` | verifier `lib.sh:6218-6221` (**drift note:** Phase 1 §6 C-5 cites `:6207-6213` for this verdict, but those lines are the *budget printf*; the prestop-equal WARN + `return 1` are at `:6219-6220`) | rc 1 (`EQUALS the pre-stop pid`) — must stay untouched by the ladder work |
| **NP-11** | ~~**Darwin variant of NP-4** (only if Phase 1 takes optional **T12**)~~ — **DROPPED (leader ruling D-P2-4, follows the T12 deferral D-P1-3): declared parity gap**, recorded in the evidence pack, never a silent omission | — (dropped) | — | — | (deferred; future id `143a045c`) |

### 9.2 The over-accept counter-mutations (the commission's explicit demand)

**NP-6's binding predicate — character-for-character copy (identical to `phase1-plan.md` §5.3 and `decisions.md` §2.2(7); the
table cell above carries the markdown-escaped rendering form `\|\|` = `||`):**

```
pcg == cg || pcg contains "$cg/"
```

Phase 1 lands a **ladder**, not a grace — so the "widen the accept-set" plant is expressed against the
ladder. Each row names the plant in Phase 1's terms:

| Mutation | Plant (on the **copy** of lib.sh) | Must redden |
|---|---|---|
| **M-NP1 — remove the `1` classification** | make `_gate_attr_containment` return `0` unconditionally (phase1 §5.3's "Exhausted ⇒ if any rung answered and said no ⇒ `1`" deleted) | **NP-4**, **NP-9**, and B1 becomes vacuous (which is why B1 carries the counter non-vacuity assertion) |
| **M-NP1b — permissive rung** | make the cgroup test unconditional (`case "$pcg" in *) ;;`) | **NP-4** (R2 path) and **NP-6** |
| **M-NP1c — composition downgrade removed** (architect closure C-1) | delete the answered-`1`→`2` downgrade clause / let R2's negative fall through to R3 un-downgraded (phase1 §5.3 composition rule) | **NP-4**, **NP-6** |
| **M-NP2 — version bypass** | `if [ "$running" != "$PROMOTE_EXPECTED_VERSION" ]; then` → `if false; then` (identical to the existing pack's `M3`, `tests/packs/handback_verify_budget_test.sh:811`) | **NP-1**, **NP-8** |
| **M-NP3 — no-listener acceptance** | treat an rc-0-empty census as success | **NP-5** |
| **M-NP4 — drop the attribution conjunct** | remove the `_gate_serving_attribution` call in `_handback_serving_probe` | **NP-4**, **NP-9**, B-parity-refuse |
| **M-NP5 — burn patience on a positive `1`** | let the deferral loop continue when a rung answered `1` (i.e. drop the `1` short-circuit) | **NP-4**'s "exactly one census" assertion, B-defer-a's bound |

**The load-bearing property to state in the evidence pack (verbatim):**
> *A genuinely wrong release still halts, and the ladder's widenings are each pinned by a counter-mutation
> that turns the corresponding negative leg RED. Specifically: **NP-4 goes RED under M-NP1** (the
> `1`-classification removed) and **NP-4's "exactly one census" assertion goes RED under M-NP5** (patience
> burned on a positively-foreign holder).*

That is the commission's demand expressed as a two-sided, mechanically-checkable claim rather than a slogan.

### 9.3 Why this is a real proof and not a tautology

1. **Positive controls.** Every NP leg has one: B1 for NP-4/NP-6/NP-9; `CURL_MODE=ok` + right version for
   NP-1/NP-2/NP-3; a delayed-attribution fixture for NP-8; the pre-fix M7 shape for NP-11. A red in an NP
   leg therefore cannot be blamed on a broken fixture.
2. **Reach assertions.** Every NP leg asserts the gate was **reached**: a needle from the gate's own detail
   (`GATE_ATTRIBUTION_DETAIL`, the WARN line) **plus** a stub call count. A leg cannot pass because an
   earlier step failed.
3. **Non-vacuous plants.** `make_mut` refuses a plant that changed nothing (`cmp -s`, `:417-420`).
4. **Byte-preserved prefix.** `I-B3` means the M-section pins (`M1/M3/M6/M7`, `test_supervision_stop_handback.sh:1663-1700`; M1 `:1666`, M7 `:1697`)
   must stay green **without edits**; if any M-pin needs an edit, that is a **design deviation**, reported as such.
5. **The wrong-release set is exhaustive w.r.t. the incident.** The incident was *either* a false refusal of a
   healthy boot (covered by B1) *or* a wrong release (covered by NP-1..NP-5). There is no third class that the
   fix could silently accept — and NP-6/NP-10/NP-11 close the seams adjacent to the widening.

### 9.4 Residual risk carried into the report (not hidden)

**Cross-reference: §15 R-5 (cgroup prefix-collision).** Phase 1's R2 uses "cgroup path **contains** the unit ControlGroup"
(a substring test — the same `case "$pcg" in *"$cg"*)` shape as today's `lib.sh:8127-8134`). A sibling unit
whose cgroup path is a **suffix-extension** of ours (`…/ensemble-main.service-evil`) would be *accepted*.
Consequences stated honestly:

- It requires a **second systemd unit whose cgroup name extends ours** to hold our port — an operator-hostile
  shape, not a plausible accident.
- The acceptance is **not silent**: Phase 1's accept path logs `attribution [<what>]: ACCEPTED — … contained
  in unit 'Y' …`, so a post-mortem sees it.
- NP-6 records which behaviour the shipped code has (arm (a) or (b)) so the residual is **named**, and
  OQ-P2-3 asks whether to harden now.

---

## 10. Restart-arm tests — "clean audits, no cause" must be impossible

### 10.0 REQUIRED — the `halt`-row widening is a behaviour change to a machine-checkable gate (architect §6)

`halt` rows are **not inert**: `ledger_check.py:74` `VIOLATION_EVENTS` includes `halt`, and
`ALERT_KIND_BY_EVENT["halt"]` pages each as `upgrade_cap_halt`. The seven newly-journaled restart refusals therefore
mark the drill cycle **VIOLATION** (breaking the N-clean streak) and page — a **behaviour change to a machine-checkable
gate**, not merely a new log line. It is defensible (post-adoption restarts already journal `halt`), but it must be
**disclosed** (CHANGELOG + tag annotation) and **pinned** here:

- **Leg R-WIDENING (added):** drive one refusal class and assert (a) exactly one `halt` row is written, (b) the
  drill-cycle ledger verdict flips to VIOLATION for that cycle, (c) the alert kind resolves to `upgrade_cap_halt`, and
  (d) the **pre-existing** clean-cycle count is unaffected by cycles with no restart refusal.
- **Mutation M-R-WIDENING:** remove the `halt` kind from the refusal rows ⇒ R-WIDENING reddens on (a)/(b).

### 10.1 The exit-78 census, re-counted against Phase 1's landed scope

Phase 1 §7.2 journals **seven** classes, cited there by their **`_warn` line** (`restart.sh:122`, `:136`,
`:148`, `:155`, `:161`, `:165`, `:173`); the corresponding **`exit 78`** lines are `:124`, `:137`, `:149`,
`:156`, `:162`, `:166`, `:174`. Kind `halt`, `class=<slug>`, `|| true`. Everything else is unchanged.
(The census table below uses the **exit** lines, because that is what R0's parser anchors on.)

| Site | Class | Before Phase 1 | After Phase 1 |
|---|---|---|---|
| `:82` | unknown flag | silent (arg parse) | **silent — allowlist** |
| `:96` | explicit target required | silent | **silent — allowlist** |
| `:104` | explicit `--run-id` required | silent | **silent — allowlist** |
| `:108` | invalid `--grace-s` | silent | **silent — allowlist** |
| `:122` | python3 ≥ 3.7 unavailable | silent | **journaled, best-effort** (`class=python_floor`) — **row cannot land when python3 is absent** (the writer IS python3; see the ⚠️ below) |
| `:136` | stop-script sibling not usable | silent | **journaled** (`class=sibling_unusable`) |
| `:145` | `journal_init` failed | silent | **silent — allowlist (cannot journal: init failed)** |
| `:149` | journal unreadable / TORN | silent | **journaled** (`class=journal_torn`, best-effort `\|\| true`) |
| `:156` | **no `pending_op` — "nothing armed"** (the twins) | silent | **journaled** (`class=no_pending_op`) |
| `:162` | `pending_op.kind != restart` | silent | **journaled** (`class=pending_kind`) |
| `:166` | `pending_op.run_id` mismatch | silent | **journaled** (`class=pending_run_id`) |
| `:174` | `in_flight.kind != restart` | silent | **journaled** (`class=inflight_kind`) |
| `:182` / `:188` | lock missing / run_id mismatch | journaled (`halt`) | unchanged |
| `:192` | lock owner write failed | silent | **silent — allowlist (advisory; a journal write races the failed write)** |
| `lib.sh:1494` | live-restart-refused | journaled | unchanged |
| `supervision_preflight` (`restart.sh:213`) | own `_refuse` | journaled | unchanged |

**Allowlist = 6 sites: `:82`, `:96`, `:104`, `:108`, `:145`, `:192`.**

- `:82`, `:96`, `:104`, `:108` run **before `journal_init`** (`:143`) — they cannot write to a journal that
  is not yet initialised. (`:82` uses a bare `echo >&2`, the other three a `_warn`.)
- `:145` **is** the `journal_init` failure — the write path is the thing that failed.
- `:192` is post-adoption advisory: a journal append there would race the very lock write that just failed.

The allowlist is **pinned by line set + size** so adding a new silent exit reddens R0.

**⚠️ A structural catch on the `:122` class (verified in code — Phase 1 should see this).**
`journal_history_append` routes through `_splice_journal` → `journal_splice.py` → **python3**
(`lib.sh:1935-1950`), and `:122` is the *python3-floor refusal*. So in the exact shape that triggers it,
the journal write **cannot land**: there is no working interpreter to write it with. Phase 1's `|| true` is
correct and honest, but the row is **best-effort-vacuous in the no-python3 case** (it CAN land when the
failure is e.g. a broken interpreter path that `command -v` still resolves — but not when python3 is
absent). Consequence for Phase 2: leg **R-PY** must assert

1. the **exit code** (78) and the **stderr** text, which are the real observables in every sub-case;
2. **and** either a `halt` row **or** a documented "row unwritable" marker — never a bare "row exists";
3. with a **positive sub-case** that DOES produce a row: put a `python3` on PATH that fails the floor
   probe (`-c "… assert sys.version_info >= (3,7)"` → exit 1) but can still run the splice helper — then
   the row is expected; and a **negative sub-case** with **no** python3 at all — then the row is expected
   **absent** and only the exit code/stderr carry the cause.

That two-sided treatment is what makes R-PY a real pin instead of a vacuous one.

**Residual surfaced to the dispatcher (OQ-P2-2):** after Phase 1 these 5 classes still journal nothing
(`:122` is journaled best-effort, and `:82`/`:96`/`:104`/`:108`/`:145` cannot be). Their cause *is* visible on
**stderr** (`_warn` text or the `:82` echo) and in the **client log** (`Running as unit … code=exited/status=78`),
just not in the journal. That is the honest limit of the landed design; R0 makes it **explicit and pinned**
rather than invisible.

### 10.2 Leg design

**R0 — the completeness pin (structural, the strongest and the cheapest).** A grep/AST pin over
`restart.sh`:

- every `exit 78` must be **preceded by a journal-emitting call** within a bounded window, **or** be on the
  pinned allowlist;
- the allowlist is asserted as an **exact line set** (not a count), so both an addition and a *deletion with
  a journal added* are visible;
- **and** the census is asserted to cover **every** `exit 78` in the file (the parser must not miss one —
  self-check: `grep -c "exit 78"` equals the number of classified sites).

*Mutation **M-R0**:* delete one journal append from a journaled site ⇒ R0 reddens. *Mutation **M-R0b**:* add
a new `exit 78` (e.g. a new guard) ⇒ R0 reddens on the census-count self-check.

**R-\<class\> — one behavioural leg per newly-journaled class** (`tests/packs/restart_arm_cause_test.sh`),
named by **class, not sequence** (`R-PY`, `R-SIBLING`, `R-TORN`, `R-NOARM`, `R-POPKIND`, `R-POPRUN`,
`R-INF`). **R-PY is two-sided** (see the ⚠️ note in §10.1): one sub-case where the row must land and one
where only exit-code + stderr can be observed. Each leg:

1. builds a scratch install (FAKE_HOME + `mktemp`), a minimal `releases/state.json`, and only the `.env`
   fields the class needs;
2. constructs exactly its precondition (e.g. `R-NOARM`: journal initialised, `pending_op: null`);
3. runs `bash scripts/upgrade/restart.sh sandbox` (never `live`; never a real dir) with PATH stubs first;
4. asserts:
   (a) exit **78** (`I-D2`);
   (b) **exactly one** new `halt` history row (`I-D1`);
   (c) the row carries `run_id=${RUN_ID}`;
   (d) the row carries `class=<[a-z0-9_.-]{3,40}>` — asserted as a **shape**, not a literal (U-4);
   (e) the `_warn` text is **unchanged** (`I-D2`) — compared against a captured pre-fix baseline;
   (f) distinct classes produce **distinct** slugs (cross-leg comparison, no hard-coded vocabulary).

**R-NOARM** additionally asserts the row lands **before any mutation**: no lock adopted, no txn opened,
`pending_op` untouched (the exact shape the twins failed to leave behind).

**R-TORN** additionally asserts the **best-effort degradation** (`I-D3`): a torn journal must still produce
exit 78 with the *same* exit path, and the failed append must not change behaviour.

*Mutations:* **M-NOARM** (delete the `R-NOARM` append) ⇒ R-NOARM reddens; **M-TORN** likewise; a single
mutation deleting a hypothetical shared emitter body reddens all R-* **only if** R0 also reddens — R0 is the
primary because it sees *presence*, while the R-* legs see *correctness of content*.

### 10.3 Honest-label interaction with the frozen 7b/7c pins

The scope-arm site is **both** an A-site and a restart-arm site. Its label change must: keep the frozen 7c
tail byte-identical on the failure path (`lib.sh:6748`/`:6753`, pinned at
`tests/test_bounded_subprocess_waits.sh:448` and `tests/test_supervision_stop_handback.sh:894`), stay
7b-clean on success paths (literal `systemctl start` absent), and add the honest rc classification.
**Leg A6 asserts all three in one run.**

### 10.4 Where the promote-level halt is proven

`NP-7b` and `B2b`'s caller contract need a real `promote.sh` run — **Tier-3 rehearsal only** (§11.4). That is
the one place this phase touches the machinery end-to-end; everything else is lib-level or structural.

---

## 11. Rehearsal strategy — SCRATCH/SANDBOX ONLY

> **Absolute boundary:** no live promote fields, no `releases/state.json` of a real install, no
> `releases/current` symlink contact, no `systemctl start/stop` against a real unit, no daemon boot from
> the live install. Every path below is a `mktemp` product with an escape guard.

### 11.1 Tier 0 — static (seconds)

`bash -n` on every touched script; the **five** structural pins from P2-T3 (R0 census, A7 hygiene lock, C-8
template accessor, B10 C-10a two-copy sync, A0 `_run_bounded`-untouched); `test_ceremony_wallclock.sh`'s
source-textual knob extraction. **Gate:** Tier 0 green before any execution tier — a syntax error must never
be discovered by a fixture.

### 11.2 Tier 1 — bash suites only (no daemon, no systemd, PATH stubs)

**Packs (default fast subset if ≤ 120 s each, else `--full`):**

- NEW `tests/packs/handback_false_halt_test.sh` (§6–§9)
- NEW `tests/packs/restart_arm_cause_test.sh` (§10)
- STANDING regression, run in **this order** (§M/§N first — they are the most likely to be disturbed):
  1. `tests/test_supervision_stop_handback.sh` (166 nodes at HEAD per `PACKS.md:4848`) — §M attribution pins
     (M1/M3/M6/M7 detail strings, `I-B3`; M1 at `:1666`, M7 at `:1697`) and §N launchd-stash pins
     (`:2020-2135`) must be green **without edits**
  2. `tests/test_bounded_subprocess_waits.sh` — the site-token pins (`:327`/`:343`/`:359`/`:428`), the
     timeout WARN literal (`:429`), and the happy-path silence pin (`:376`)
  3. `tests/packs/handback_verify_budget_test.sh` (L* + P1-M*) — its own `is-active` timing pins
  4. `tests/test_ceremony_wallclock.sh` — must be green **with zero edits** (§7.10)
  5. `tests/test_atomic_flip.sh` (the `uname` seam), `tests/test_promote_cgroup_survivorship.sh` (7b pin)
- OPTIONAL: `tests/test_release_journal.sh --full` (long; it exercises the journal surface Phase 1 touches)

**Regression matrix (authored before P2-T5, per R-3).** One table listing every existing assertion that
touches the three label sites, the start site, the attribution gate, or the stash, each classified:

- **must-survive** — site tokens, rc-124 legacy wording, happy-path silence, M-section detail strings,
  N-section launchd stash, `ceremony_wallclock` numbers;
- **planned delta** — a needle that names the *mislabel* as if it were correct (there should be few; each
  needs a one-line justification in the evidence pack).

**Gate:** every pack green or a declared, attributed delta; `bash tests/test_upgrade_overhaul_all.sh`
(default) green.

### 11.3 Tier 2 — scratch "supervisee" boot smoke (no real systemd, no real daemon binary)

A `mktemp` install root + a **shell supervisee**:

```
$SANDBOX/releases/vTEST/ensemble-prod   → a tiny bash script that binds PORT and serves /livez + /readyz
$SANDBOX/releases/state.json            → fixture
systemctl stub                          → MainPID = the supervisee pid, ControlGroup = the runner's cgroup
```

Drive `_supervision_handback_unit` / `_gate_serving_attribution` against it. Purpose: prove the
**composition** (real pid → real `/proc/<pid>/cgroup` → real `lsof` census → real curl) rather than stub
shapes alone. Still no systemd, still no daemon binary.

**If** a leg ever runs a **frozen ensemble binary** (none planned in Phase 2): from a daemon-child shell
it **must** pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **explicitly** — `env -u` on it strips the
ambient guard and the boot sweep SIGTERM→SIGKILLs live processes (2026-10-31 incident). Also note
`DAEMON_PORT` is an **inert** knob (port resolves from config `PORT`; wi-2454ede3) — never rely on it as
a fence.

### 11.4 Tier 3 — sandbox staged promote against a scratch install (leader-gated)

The only tier that runs `scripts/upgrade/promote.sh`. Preconditions:

- `TARGET=sandbox` (the resolver's own guards apply: `resolve_env` refuses `7979`/`8079`/the live staged
  port, requires an explicit existing `INSTALL_DIR` + numeric `PORT`, and replaces a foreign
  `POSTGRES_DB` — `lib.sh:1348-1428`);
- `INSTALL_DIR=$(mktemp -d /tmp/…)`, `PORT` an ephemeral high port (SIM_PORT precedent: 39713, refusing
  9797/7979/8079/8088 — `tests/test_agent_restart_sim.sh:106-109`);
- PATH-first stubs for `systemctl`, `lsof`, `curl`, `launchctl`, `uname`;
- **no** `ENSEMBLE_UPGRADE_LIVE=1`; `ENSEMBLE_SELF_ENV=sandbox`;
- a `pg_dump`/DB is **not** needed (the promote lane this phase touches is pre/post-flip shell only).

**Purpose:** NP-7b (the literal "wrong release still halts" + B4 leave-txn-open) and B2b's caller
contract. **Gate:** Tier 0–2 green + leader approval + a written statement of which fields the run may
touch (only the `mktemp` install's own `releases/`).

### 11.5 Tier 3b (optional, highest risk) — a disposable real transient unit

Precedent exists (tester R1/R2/R6 disposable `ensemble-test-<uuid>` units, zero strays). Phase 2 does
**not** require it; it is listed so the leader can choose it for the *end-to-end* proof of the unit arm
against real systemd. **Gate:** explicit leader approval, sequential, teardown by unit name, and a
`systemctl list-units 'ensemble-test-*'` residue check at the end.

### 11.6 THE DANGER LIST (applies to every tier; embed verbatim in each pack's header)

1. **No live-shaped paths, ever.** `mktemp` FAKE_HOME + a **4-stage escape guard** that aborts loudly
   before any create/destructive op (precedent `tests/upgrade_test_lib.sh:59-107`,
   `tests/helpers/preflight_pack_common.sh:7,76,78,89,106`). Two live-install deletions happened on
   2026-10-12 from an unfenced fixture; `/home/nea/agents-ensemble` must be **unresolvable** from any
   fixture. A guard self-test (poisoned empty `HOME` / `/etc/passwd`) is part of the pack.
2. **Never `env -u POSTGRES_*`** — it is a silent NO-OP (bash does not glob env names). Scrub
   **per variable**: `POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT`
   (+ `DATABASE_URL`, `DATABASE_URL_RO`, `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` where relevant)
   and set `ENSEMBLE_SELF_ENV=dev` (or `sandbox` for Tier 3). A pack-level abort on
   `POSTGRES_DB=ensemble_prod` is mandatory (`tests/packs/handback_verify_budget_test.sh:46-49` precedent).
3. **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned EXPLICITLY** if any frozen-binary boot runs from a
   daemon-child shell (never `env -u`).
4. **`ENSEMBLE_AUTH_*` full-family scrub or none** (7 vars) — partial scrubs cause phantom reds.
5. **Worktree `.env` fence** per `agents/giter/workflow.md:82-94` (explicit `POSTGRES_*` + `DAEMON_PORT`
   lines; `POSTGRES_DB != ensemble_prod`). Phase 2 adds no daemon run, so this is a check, not a change.
6. **No bare `python`** — `python3` or the worktree `.venv/bin/python`; a `timeout` on every python leg.
7. **Transcripts named `*.txt`** — `.gitignore:58-59` hides `*.log` and `logs/`; a `*.log` evidence file
   silently never lands.
8. **Never `pkill -f "ensemble-prod"`** or any broad pattern kill (wi-2454ede3: an agent's `pkill -f
   ensemble-prod` killed the LIVE daemon). Kill only pids this pack spawned, by pid.
9. **Never bind** 9797 / 7979 / 8079 / 8088 / 5432+; refuse a `SIM_PORT` in that set.
10. **`sed -i` portability**: `sed -i.bak … && rm -f …` (GNU/BSD-safe) — a bare `sed -i ''` dies on GNU.
11. **`systemctl` must never resolve to the real binary** in any leg: assert
    `command -v systemctl` → the stub dir (precedent M11's `fence-broken` check,
    `test_supervision_stop_handback.sh:1749-1757`).
12. **No `timeout`-free long loops**; every pack self-caps (`timeout` inside the pack + outside).

---

## 12. Registration / wiring plan (GAP #2) — with a correction to the commission

### 12.1 Correction (carried per plan doctrine)

The commission states that `test_release_journal.sh`, `test_stage_freshness_guard.sh` and
`handback_verify_budget_test.sh` are "NOT registered in `.agents/tester/PACKS.md`".
**Grep evidence contradicts this as stated:**

- `test_release_journal.sh` — **6 rows**: `PACKS.md:1826`, `:1827`, `:1828` (CURRENT tip state),
  `:2514`, `:3021`, `:3123`;
- `test_stage_freshness_guard.sh` — **2 rows**: `:1829` (`shell_stage_freshness`, 110P/8F ≡ base) and
  `:4618` (`stage_freshness_guard`, **labelled "registered 2026-10-05 — direct suite"**, 118/0). The
  2026-10-05 RESULTS file records F-2 as open and its disposition section
  (`:126-130`) states "`test_stage_freshness_guard` = zero rows — remains UNREGISTERED". Both are true
  *at their own moments*: the registration row `:4618` **post-dates** the F-2 report, so the F-2 is now
  satisfied — the residual is that the row sits inside a historical commission block, not in a canonical
  lane section (G-1);
- `handback_verify_budget_test.sh` — **3 mentions**: `:1364` (flake disclosure), `:4813`, `:4854`
  (two registration rows);
- `test_supervision_stop_handback.sh` — **8 rows** (`:2958`, `:2976`, `:2985`, `:3020`, `:3237`, `:4798`,
  `:4814`, `:4848`).

So the F-2 class named in the 2026-10-05 RESULTS file is **closed for all three**; what remains is
registry *shape*, not absence.

**The real gaps are two other things**, and those are what Phase 2 plans:

| Real gap | Evidence |
|---|---|
| **G-1 — no canonical "current" section** for this lane: the existing rows are historical blocks scattered across 5k lines; a reader cannot find today's invocation + observed count in one place | the rows above are per-commission blocks, not a per-lane index |
| **G-2 — no aggregator wiring**: `tests/test_upgrade_overhaul_all.sh` runs `release_journal` only under `--full` (`:184-196`) and never runs `test_stage_freshness_guard.sh` **or** `handback_verify_budget_test.sh` (grep: 0 hits) | `grep -n "stage_freshness" tests/test_upgrade_overhaul_all.sh` → 0 |

### 12.2 Phase 2 registration tasks

| T | Task | Acceptance |
|---|---|---|
| **RG-1** | Register `handback_false_halt_test` in `PACKS.md`: invocation, scope, **observed node count**, the mutation-matrix id range (`M-A*`, `M-B*`, `M-C*`, `M-NP*`), and the fence line | one row; the count matches the run (never declared) |
| **RG-2** | Register `restart_arm_cause_test` likewise — including **R0's allowlist** (its 6 pinned sites) and the exit-78 census table (§10.1) | one row |
| **RG-3** | Add a **canonical "promote-handback-false-halt lane" section** to `PACKS.md` holding the lane's packs in their CURRENT state: 2 new + `supervision_stop_handback`, `bounded_subprocess_waits`, `handback_verify_budget`, `ceremony_wallclock`. Append-only, newest-first; it supersedes nothing (the historical blocks stay as measured-at-their-rev) | section header + 6 rows, each with an **observed** count |
| **RG-4** | Wire both new packs into `tests/test_upgrade_overhaul_all.sh`: default subset if each ≤ 120 s, else `--full` | aggregator green; the skip lines honest |
| **RG-5** | Wire `test_stage_freshness_guard.sh` + `handback_verify_budget_test.sh` into the aggregator (`--full`) — closes **G-2** | two `run_bash_pack` lines; `--full` run green or attributed |
| **RG-6** | Record the `make_systemctl_stub` drift as a **routed finding**: 3 copies (`tests/packs/handback_verify_budget_test.sh:202-250`, `tests/test_supervision_stop_handback.sh:104-160`, `tests/test_bounded_subprocess_waits.sh:88-131`), **none able to emit a nonzero `is-active` rc** — with the §4.3 knob contract as the consolidation spec | a `PACKS.md` note + the contract in the lane section; **no consolidation in this phase** (it would churn 3 suites) |
| **RG-7** | Record the **B10 hole** as a permanent lane row: the C-10a two-copy identity was claimed in `promote.sh:326-327` and asserted by **no pack** until this lane | a `PACKS.md` note pointing at `handback_false_halt_test.sh`'s B10 leg |

### 12.3 What Phase 2 deliberately does NOT register

- **No registration of the Phase-1 code fixes themselves** — a `scripts/upgrade/**` change is gated by the
  rolling packs (RG-3's lane section), not by a per-commit pack.
- **No aggregator change to the 600 s cap** — `test_release_journal.sh` exceeds it on this host
  (`PACKS.md:1827`: mandated 600 s ⇒ RC=124; completing leg needs 1200 s). It stays `--full`-only with its
  existing disclosure; Phase 2 only *files* the fact.

---

## 13. Evidence pack (RESULTS) shape

`.agents/tester/RESULTS/2026-11-<dd>-promote-handback-false-halt-phase2-test-gate.md`, with raw
transcripts under `.agents/tester/RESULTS/evidence/<date>-hbfix-phase2/*.txt`:

1. **Verdict** (PASS / PASS-WITH-NOTES / FAIL) + net-new red count.
2. **Pack matrix** — pack | invocation | observed nodes | wall | rc. Every count **observed**, never
   declared.
3. **Mutation table** — id | plant (one line) | expected red assertion | observed red | restore sha256
   before==after (a mutation whose restore is not byte-proven is a failed leg).
4. **FAILED-list diff** vs the TRUE pre-fix base (base worktree or `git archive <base>`), byte-identical
   or attributed.
5. **NEGATIVE-PROOF matrix** — NP-1..NP-11 | protection | outcome | counter-mutation | observed.
   One line per row, **plus** the two-sided statement (NP-4 reddens under M-NP1/M-NP1b; its census-count
   assertion reddens under M-NP5). This is the section the Emperor's directive is answered by.
6. **Fence attestation** — env scrub proven (e.g. `/proc/<self>/environ` shows no `POSTGRES_*`), ports
   never bound, all fixture paths under `/tmp`, no live-install resolution, `systemctl` resolved to stubs
   in every leg, residue gate = zero (with an explicit allowlist for pre-existing foreign files).
7. **Disclosed flake log** — every re-run and its outcome (L8b protocol).
8. **Routed findings** — the four residual unknowns (U-1..U-4) as resolved, NP-6's observed arm, the
   stub-drift finding (RG-6), the B10 hole (RG-7), any `ceremony_wallclock`
   delta.

---

## 14. Task breakdown (ordering + dependencies)

Phase 1's design has landed, so the "wait for the hand-off" gate is **open**; the ordering below is now
purely about test-code dependencies and the serialization rule for `lib.sh`-copy mutation work.

| # | Task | Depends on | Acceptance |
|---|------|-----------|------------|
| **P2-T1** | New `systemctl` stub generation (§4.3 contract: `SC_IS_ACTIVE_RC`, `SC_RESET_FAILED_RC`, `SC_SHOW_RC`, `*_ERR`, argv log) + a micro-smoke proving it models the tool | — | 5 rc arms return their rc **and** the argv log records them; a deliberate `SC_IS_ACTIVE_RC=3` arm prints `inactive` **and** exits 3 |
| **P2-T2** | Fixtures: **Layer 1** multi-pid census stub (real pids + stub pids) and **Layer 2** model-fidelity leg (real forking server, real `lsof`) | T1 | Layer 2 lists **both** pids on this host (Linux+lsof); Layer 1 reproduces `owned ⊊ holders` |
| **P2-T3** | Tier-0 static pins: **R0** completeness (`restart.sh` census), **A7** hygiene lock (`I-E3`), **C-8** template accessor, **B10** C-10a two-copy sync, **A0** `_run_bounded` untouched (`I-A5`) | — (no Phase 1 dependency) | each pin RED under its own one-line plant (5 plants) |
| **P2-T4** | Pack scaffolding: FAKE_HOME fence + escape guard self-test + per-variable scrubs + `systemctl`-is-the-stub assertion + fixture registry | T1 | the skeleton **aborts** on `POSTGRES_DB=ensemble_prod`; guard self-test refuses a poisoned `HOME` |
| **P2-T5** | **§6 Candidate-A legs** (A0–A9, incl. the D2 symmetric-row legs A8/A9) + mutations M-A0..M-A9 | T1, T3, T4 | each A-leg green unmutated, red under its named mutation; A5/A2 prove the legacy rc-124 text is **byte-identical** |
| **P2-T6** | **§7.1/§7.2** survival (B1, B1b, B1c) + exhaustion (B2) | T2, T4 | B1/B1b/B1c rc 0 with the ACCEPTED log line; B2 rc 1 + bounded |
| **P2-T7** | **§7.3 rung legs** (B3 cgroup, B4 MainPID, B5 ppid, B6 launchd, B7 Darwin/Plan9, B8 unprobeable) | T2, T4 | each rung classifies `0/1/2` per Phase 1 §5.3 |
| **P2-T8** | **§7.4/§7.5/§7.6/§7.8** ordering (B-order), deferral (B-defer-a/b/c/**e**), parity (B-parity incl. **B-parity-capture**), halt chain (B-halt) | T5, T6 | B-order's byte-identical transcript + "no ControlGroup probe" both hold; B-defer-a proves the **pool** (3 calls ≤ 1× bound) and the **transient** exhaustion verdict (leader D1); **B-defer-e proves the rc=2 consumer contract end-to-end (probe maps 2→poll, ≥2 iterations, halt only at floor-wins, exhaustion marker in `HB_VERIFY_REASON`; M-B-defer-E reddens it) and B-parity-capture proves gate stdout purity (M-B2b reddens it)** |
| **P2-T9** | **§9 NEGATIVE PROOF** (NP-1..NP-11) + M-NP1..M-NP1c/M-NP5 | T2, T6, T7 | **NP-4 reddens under M-NP1** and under **M-NP1c**; **NP-6 reddens under M-NP1b and M-NP1c**; NP-4's census-count assertion reddens under M-NP5; NP-1/NP-8 redden under M-NP2 |
| **P2-T10** | **§8 Candidate-C legs** (C-1..C-8) + M-C1..M-C4 | T4 | C-5a passes a **subshell** read (`I-C5`); M-C4 (variable-only stash) reddens it |
| **P2-T11** | **§10 restart-arm legs** (R-PY, R-SIBLING, R-TORN, R-NOARM, R-POPKIND, R-POPRUN, R-INF) + R0 behaviour matrix + M-NOARM/M-TORN/M-R0 | T3, T4 | one `halt` row per class with the slug shape; `_warn` text byte-unchanged; R0 allowlist exact |
| **P2-T12** | Regression sweep: standing suites at **FAILED-diff-empty** vs the pre-fix base; `ceremony_wallclock` **green with zero edits** (§7.10) | T5–T11 | diff empty or attributed; §M/§N of `test_supervision_stop_handback.sh` green **without edits** (`I-B3`) |
| **P2-T13** | Tier-2 scratch supervisee smoke (real pid + real `/proc` + real `lsof` + real curl) | T4, T6 | completes on the healthy fixture, refuses on the foreign one — the **composition**, not stub shapes |
| **P2-T14** | Registration + wiring (RG-1..RG-6) | T5–T11 | `PACKS.md` rows + aggregator green |
| **P2-T15** | Tier-3 sandbox promote rehearsal (NP-7b + B2b) — **leader-gated** (OQ-P2-1) | T9, T12, T13 | wrong release ⇒ halt + `in_flight` **open** (B4) + `current` unchanged |
| **P2-T16** | Evidence pack (§13) | all | 8 sections present; mutation restores byte-proven |

**Critical path:** T1 → T2 → T4 → {T5, T6} → T7 → T8 → T9 → T12 → T15.
**Immediately startable (no Phase 1 dependency, no fixture):** T3 (all five structural pins).
**Serialization rule (carried from Phase 1 §10):** every mutation operates on a **copy** of `lib.sh`; never
two writes to the same copy in one block; each mutation's restore is sha256-attested.

---

## 15. Phase risks (top 5) + mitigations

Phase 1's design has landed, so the earlier "the design may land differently" risk is **retired**. What
remains is the risk of writing a test that *looks* green without proving anything, and the risk of breaking
the pins that already hold the machinery honest.

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| **R-1** | **Fixture-model fiction (GAP #1).** The multi-pid census stub does not correspond to how `lsof -ti:<port> -sTCP:LISTEN` reports an inherited-fd child, so B1's survival leg proves something that never happens on a real host | High — the whole point of the phase | Medium | **Layer 2 (real forking server, real `lsof`) is mandatory, not optional** (§4.2). It asserts the *model*, not `lib.sh`. If it disproves the premise, that is Phase-1 feedback and §3's D4 row must be re-derived before the fix is trusted. B1c (real pids + real `/proc/<pid>/cgroup` via R2) is the bridge between stub and reality |
| **R-2** | **Vacuity.** A negative leg passes because the code never reached the gate (an earlier step failed), or because a stub answered in a way that made the path unreachable | High — a false negative proof is worse than none | Medium | Every leg carries (a) a **gate needle** (`GATE_ATTRIBUTION_DETAIL`/WARN), (b) a **stub call counter**, (c) a **positive control**. `make_mut`'s `cmp -s` refuses a plant that changed nothing. A mutation that fails to redden its leg is reported as a **failed leg**, never as "no-op" |
| **R-3** | **Collateral pin breakage** — Phase 1 edits text near pinned sites. The concrete collisions: `tests/test_bounded_subprocess_waits.sh:429` (literal `is-active probe exceeded budget` on a genuine *timeout* leg — must survive), `:327/:343/:359/:428` (site tokens `handback_reset_failed` / `handback_is_active` / `handback_start` / `scope_arm_is_active` — must survive), `:376` (happy path journals **no** `subprocess_wait_timeout`), plus the whole §M/§N blocks of `test_supervision_stop_handback.sh` (M1/M3/M6/M7 detail strings; N1/N2/N2b/N3/N5 launchd stash) | High — blocks the merge | Medium-High | A **regression matrix** is authored **before** T5 (§11.2), classifying every touching assertion as *must-survive* vs *planned delta*. §M/§N run **first** in every regression sweep. Rule: a red is a planned delta **or** a defect — never "fix" it by deleting the assertion. `I-B3` (refusal **prefix** byte-preserved) is the design-side guarantee that M1/M3/M6/M7 need no edits |
| **R-4** | **Host-class gaps.** R2 needs Linux `/proc`; R3 needs `ps -o ppid=`; the unit arm needs `/run/systemd/system`. On a Darwin runner the fix's *primary* signal (cgroup) is untestable, so a Darwin regression could ship behind a Linux-green | Medium | Medium (this host: Linux, `/run/systemd/system` present, own cgroup under `/system.slice/`) | Every leg **names its host class**; a skipped leg is a loud `_skip` with the named fence, never a silent pass; the evidence pack's verdict line must state which lanes ran where. NP-11 + B7 cover the Darwin *selection* logic natively on this host via the `uname` stub |
| **R-5** | **NP-6 residual (cgroup prefix-collision) — CLOSED by the binding amendment (D-P2-3 / C-0).** R2 now matches at segment boundaries; the collision shape is refused. Residual is only "implementation drifts back to substring" — caught by the NP-6 hard pin + M-NP1b/M-NP1c | Low (narrow, operator-hostile shape) | Low | NP-6 is a **hard pin** (arm (b)-only) with the **character-identical predicate** carried in phase1 §5.3, phase2 §9.1 and decisions.md §2.2(7); the accept path is **logged** (Phase 1's `ACCEPTED` line) so it is never silent; M-NP1b/M-NP1c redden the permissive and the downgraded variants |

**Secondary (tracked, not blocking):** the 5 pre-`journal_init` restart exits stay journal-silent by
structure (OQ-P2-2); T11/T12 are optional items whose absence is a **declared parity gap** (U-3).

---

## 16. Success criteria (verifiable)

| # | Criterion | How measured | Threshold |
|---|-----------|--------------|-----------|
| **SC-1** | Every new pin has a named counter-mutation, and the plant is non-vacuous | the evidence pack's mutation table: one row per assertion family + `restore sha256` before==after | **100%** of new pins; **zero** rows lacking an observed red |
| **SC-2** | The negative proof is two-sided | run M-NP1 and M-NP1b against NP-4; run **M-NP1c** against NP-4 and NP-6; run M-NP5 against NP-4's census-count assertion | **NP-4 RED under M-NP1 / M-NP1b / M-NP1c**; **NP-6 RED under M-NP1b / M-NP1c**; NP-4's count assertion **RED under M-NP5** |
| **SC-3** | A healthy boot with partially-outside holders completes | B1, B1b, B1c | rc 0 **and** the `ACCEPTED … contained in unit` log line **and** (B1b) census counter ≥ N+1 |
| **SC-4** | A wrong release still halts, at both layers | NP-1..NP-5, NP-9, NP-10 at lib level; NP-7b at Tier 3 | every NP leg's rc/verdict as tabulated; the Tier-3 run leaves `in_flight` **open** (B4), `current` unchanged, **no** `commit` row |
| **SC-5** | No mislabel survives | A0–A5 (helpers + all three sites) | rc 124 ⇒ `kind=budget` + `timeout(` **byte-identical**; rc 3 ⇒ **no** `kind=budget`/`timeout(`; rc 4/other ⇒ `kind=nonzero_exit` with the true rc; no hard-coded `rc=124` for a non-124 child |
| **SC-6** | The systemd lane can never say `none captured` again | C-1..C-7 | the halt detail contains the captured text on every class in C-2..C-5; C-5a survives a `$( )` read |
| **SC-7** | "Clean audits, no cause" is impossible for the classes Phase 1 journaled | R0 + the 7 R-* legs | R0 green (allowlist exact, no unjournaled `exit 78` outside it); one `halt` row per class with a distinct slug |
| **SC-8** | The ordering invariant holds (the fix must not change the common path) | B-order | **zero** ControlGroup probes on the all-owned path **and** a byte-identical success transcript vs the pre-fix baseline |
| **SC-9** | Standing suites unregressed | FAILED-list diff vs the TRUE pre-fix base | byte-identical, or every delta named + attributed; §M/§N green **without edits**; `ceremony_wallclock` green **with zero edits** |
| **SC-10** | Registration + wiring complete | `PACKS.md` rows + aggregator run | the lane's 6 packs registered with **observed** counts; `--full` includes the newly wired packs |
| **SC-11** | No live contact, no residue | fence attestation (§13 item 6) | zero live-install resolutions; zero reserved-port binds; residue gate zero (explicit allowlist only); `systemctl` resolved to a stub in every leg |
| **SC-12** | **rc=2 consumer contract + gate stdout purity hold (REVIEW WAVE 1, BL-1/BL-2 — `I-B13`/`I-B2b`)** | **B-defer-e** + **B-parity-capture** with their counter-mutations | B-defer-e green (probe returns 2; ≥2 poll iterations; halt only at the floor-wins verdict; exhaustion marker in `HB_VERIFY_REASON`; one-shot twins surface the transient wording, zero `unreachable >Ns` / `version verify mismatch` mislabels) **and red under M-B-defer-E**; B-parity-capture green (captured payloads pure, pid single numeric token, INFO on stderr) **and red under M-B2b** |

---

## 17. Open questions for the leader

| # | Question | Options + what I do under each |
|---|---|---|
| **OQ-P2-1** | Does Phase 2 own the **Tier-3** sandbox promote rehearsal (NP-7b + B2b's caller contract), or does it route to the tester gate? | (a) Phase 2 owns it → I execute §11.4 under the stated gate. (b) Route to the tester gate → NP-7b ships as a **declared gap**, lib-level NP-7a is the compensating pin, and the evidence pack states the promote-level halt is proven only structurally + by the existing template pin |
| **OQ-P2-2** | Phase 1's restart scope journals **7** classes and leaves **5** pre-`journal_init` exits silent (`:82`, `:96`, `:104`, `:108`, `:145`). Is that acceptable (with R0's pinned allowlist as the visible record), or must those also produce a cause somewhere? | (a) acceptable → R0 pins the 6-entry allowlist and the evidence pack states the limit. (b) must-journal → a design change Phase 1 must own: `:145` cannot journal (init failed) and the arg-parse class runs before any journal exists, so this needs a **non-journal** channel (e.g. a stderr-audit store) and a new Phase-1 task |
| **OQ-P2-3** | **NP-6 (cgroup prefix-collision)** — **RULED (a) GATE NOW** (leader D-P2-3 + architect C-0): Phase-1's R2 gains segment-aware matching and NP-6 is a **hard pin** (arm (b)-only); M-NP1b **and** M-NP1c must redden it. The predicate is carried character-identically in phase1 §5.3, phase2 §9.1 and decisions.md §2.2(7). *(Resolved — this row is retained as the ruling trail.)* |
| **OQ-P2-4** | ~~If Phase 1 takes optional **T12**, do I own the **Darwin** NP-11 leg?~~ **RULED (b) — NO T12 ⇒ NP-11 DROPPED, parity gap DECLARED in the evidence pack** (leader rulings D-P1-3 + D-P2-4, 2026-11-17). *(Resolved — this row is retained as the ruling trail.)* |
