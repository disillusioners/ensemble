# Architecture Recommendation — promote-handback-false-halt (adversarial enrichment of the approved track)

**Commission:** promote-machinery defect fix: healthy-boot false-halt (v0.31.0 promote `r-20261117-171207-e2f9` aftermath)
**Date:** 2026-11-17 (UTC) · **Author:** architect (controller) — 3-worker adversarial fan-out, architect-synthesized
**Worker instances:** `2a9c662e` (bound analysis, skill `adversarial-plan-ledger-verification`) · `99ea32aa` (rc-taxonomy/fences, skill `grep-fence-check-before-design-substrate`) · `02e9054d` (floor/fix-to-live/interplay, deliberate skill-less manual methodology)
**Skill-bank note (DEGRADED):** the canonical design-skill canon (`security-design`/`resilience-design`/`data-flow-design`) is absent from this project's bank; nearest-method project skills were substituted for two workers, one worker ran with an embedded methodology. Method quality was not observably degraded — all three reports are anchor-verified against the tree.
**Inputs:** the committed plan set @ `d2c42b509` (this directory) · code @ `/home/nea/ensemble-src-wt-hbfix` (base `0181dadbf`) · fixture log `r-20261117-171207-e2f9.log` (READ-ONLY, window 17:29:27–17:30:06Z).
**Drift statement (all three workers, independently):** `git diff --stat 0181dadbf..origin/latest -- scripts/upgrade/` ⇒ **empty**. The +55-commit drift touches `daemon/`, tests, PACKS.md — zero overlap with this lane's surgery surface. Every anchor below survives the D-P3-1 sync-merge; all citations are content/symbol-pinned.

---

## 0. Verdict

## **SAFE-WITH-CLOSURES**

**The Emperor's question — "how is B's attribution grace bounded so it CANNOT mask a genuinely wrong release?" — answered:**

> The grace changes only **who may answer the attribution conjunct**, never **whether the release is checked**. The release-identity conjunct lives *outside* the widened gate: in `_handback_serving_probe` the version compare (`lib.sh:6107-6116`) runs **after** attribution on every green; in `gate_version` (`:8150-8160`) it runs **before**; `gate_soak` calls `gate_version` per hard-green tick (`:8383-8388`); `PROMOTE_EXPECTED_VERSION` is unconditionally armed (`promote.sh:541-544` refuses `exit 78` on empty; `:556` sets it). The ladder's accept-set is an enumeration of *containment* predicates — it cannot manufacture an accept the version check would reject. The deferral is a delay on the attribution verdict only: it can flip `2→0` on a later census or exhaust into a refusal, never into an accept. A rogue cgroup member serving a wrong build, a stale-incarnation holder (`KillMode=mixed`), and a unit restarted with the old binary are all caught by the version conjunct at the incident path and at the promote gate.
>
> **Honest caveat (carried, not buried):** `gate_livez`/`gate_readyz` (`:8142-8143`) carry **no** version conjunct, and the restart/rollback surfaces can skip the version check conditionally (`restart.sh:320-336` skipped-no-current-symlink; `rollback.sh:277`). The absolute claim "no call site accepts green without a version check" is **false**; the correct claim is "the promote/hand-back path is version-backed end-to-end, and the promote's `gate_version` (`promote.sh:853`) runs before the commit step (`:868+`)". No wrong release can reach `current` through a widened gate.

**But the fix as specified today would land the WRONG predicate.** Two blocking spec defects (§1.1: C-0, C-1) must be closed before surgery, and one ratified mechanism (T7's stage assertion) is circular as written (§4.2). None of these contests the architecture; all are specification-integrity closures.

---

## 1. The load-bearing safety bound (focus 1 — centerpiece)

### 1.1 Blocking defects (must close before implementation)

**C-0 — BLOCKING. Body/ledger divergence on the very predicate the binding amendment exists to fix.**
`decisions.md` §2.1 (binding, D-P2-3) specifies segment-aware R2: `pcg == cg` **or** `pcg` contains `"$cg/"`. But `phase1-plan.md` §5.3 row R2 still reads *"…contains the unit ControlGroup"* (raw substring), and `phase2-plan.md` NP-6 still describes R2 as *"a substring test"* with NP-6 **two-armed**. `decisions.md` §2.2 item 4 declares the phase file unedited and the ledger "of record" — which inverts the house rule `plan-body-ledger-agreement` ("body wins; predicate strings must match character-for-character"). A coder implementing "phase1 §5.3" ships the over-accept the amendment was created to remove; the prefix-collision class (`/system.slice/ensemble-main.service-evil.scope`) survives.
**Closure:** make body and ledger agree character-for-character. Given the commission constraint (phase files frozen), the minimum is: line-anchored `⚠️ SUPERSEDED — decisions.md §2.1 (segment-aware)` markers at `phase1-plan.md` §5.3 row 2 and `phase2-plan.md` NP-6, NP-6 made **arm (b)-only**, and the literal predicate `pcg == cg || pcg contains "$cg/"` carried verbatim in the coding commission's task text. **⚠️ SUPERSEDED WORDING (2026-11-17, review wave 2 / BL-4):** the binding predicate's canonical form is `pcg == cg || pcg PREFIX-matches "$cg"/` (a left-anchored prefix test, `case "$pcg" in "$cg"/*)`), whose fenced source of record is `phase1-plan.md` §5.3. This file is the architect's historical input and is left as written apart from this marker — read the phase files for the shipped form. Phase 2 gains a drivable NP-6 without `/proc` stubbing: stub the unit ControlGroup to a **non-segment-aligned prefix of the test's real cgroup leaf** (raw substring matches ⇒ accept ⇒ NP-6 RED; segment-aware refuses ⇒ NP-6 GREEN; `M-NP1b` reddens).

**C-1 — BLOCKING. The rung-composition rule is self-contradictory, and the two readings give different accept-sets.**
`phase1` §5.3 intro: *"Rungs, in cost order, first `0` wins; a rung that cannot answer falls through to the next"* vs rung 5: *"if any rung answered and said no ⇒ `1`"*. Under "first 0 wins", R3's `0` overrides R2's answered `no` — any ppid-descendant of an owned pid is accepted regardless of cgroup, and R2 (the authoritative, amended rung) becomes decorative on the mixed case. Under "any answered-no ⇒ 1", R2's no is decisive. The plan gives both.
**Closure (the ONE normative rule, folding in C-3 below):**
> Rungs evaluate in cost order. The **first rung that ANSWERS decides** (`0` accept / `1` refuse); only a `2` (cannot-answer) falls through. An answered `1` from a rung is **downgraded to `2`** when a *stronger* rung was unanswerable — an incomplete determination is not positive foreignness. All rungs `2` ⇒ holder is `2` (deferral).

Add counter-mutation **M-NP1c** ("R2's negative falls through to R3" / "downgrade removed") which must redden NP-4 and NP-6.
*Rationale for the downgrade clause (C-3):* on a host with a wedged `systemctl`, R2 is unanswerable; R3's negative alone must not convert the ladder into a *stricter* check than today's — that would preserve the false-halt class on exactly the partially-broken hosts. (Correction of record: the incident host itself was **not** wedged — see §1.3 — so this clause is robustness for a real host class, not the incident replay.)

### 1.2 Acceptance-path verdicts (scenario ledger, condensed)

| Path | Verdict | Load-bearing findings |
|---|---|---|
| **R1** owned-pids | **SAFE** | Unchanged; refusal prefix byte-preserved (M1/M3 pins stay green). |
| **R2** segment-aware cgroup | **SAFE-WITH-CLOSURES** | C-0, C-1 above. Child-scope-of-unit acceptance is sound (only systemd/delegation creates cgroups under a unit path — the declared P12 residual's twin). **Upgrade executor is NOT self-accepted**: it lives in a sibling cgroup (`lib.sh:6481-6486` scope-escape note; `ensemble-upgrade-<run_id>.service` is a separate unit) ⇒ segment test refuses. Stale-incarnation holders under `KillMode=mixed` ARE accepted — and caught by the version conjunct (document as P12 sibling, closure C-7). MainPID arm: stale/activating MainPID yields `''`/`0` ⇒ unprobeable-skip, not accept; pre-stop-pid equality separately refused (`:6219-6221`). |
| **R3** ppid-ancestry | **SAFE-WITH-CLOSURES** | pid reuse: judged on the *current* occupant — no false accept reachable (INV4 not needed); reparented orphan ⇒ walk completes ⇒ answered-`1` (must be stated explicitly — closure C-5); `ps` absent ⇒ `2`. **R3e:** on a ladder accept the plan continues to Layer 2, whose legacy substring arm can *veto* an R3 acceptance in the mixed case (unit probes resolve, holder's `/proc/<pid>/cgroup` unreadable) ⇒ silent false refusal. Layer 2 is refusal-only (never accepts) ⇒ closure C-2: skip Layer 2's unit-arm re-test for holders the ladder already accepted; state the correct rationale (refusal-only), replacing the ledger's now-false "when nonmatch is empty every holder is owned" reasoning. |
| **R4** launchd lineage | **SAFE** | Pidless-job refusal preserved (M7 `tests/test_supervision_stop_handback.sh:1697`); job-pid reuse judged on current ancestry; probe failure ⇒ fall-through, never evidence. |
| **Deferral** | **SAFE-WITH-CLOSURES** | §2 below: bound mis-stated (arithmetic ×2 error; per-process scope false at `$( )` call sites), seam unclamped, and **exhaustion is terminal** (decision pending §7-D1). |

### 1.3 Adjudication of record — the incident host's survival path is R2, not R3

Workers A and B diverged on fixture-log line 21 (`17:29:28Z`: *"is-active probe exceeded budget (5s) … treating as NOT-active"*). Adjudicated by the architect from the log itself: lines 20 and 21 carry the **same second** — the probe consumed no 5s wall clock. The "exceeded budget" wording is the mislabel wrapping a **fast rc=3 return** (the plan's own root-cause finding). **There was no systemctl wedge on the incident host.** Consequence: on replay, R2 answers (MCP children are in the unit cgroup) and accepts — the plan's original design path. C-3's downgrade clause remains correct design for genuinely-wedged hosts (a class the WARN path proves exists) but is not load-bearing on the incident replay.

### 1.4 The invariant set (derived; implied-by-design status)

| Inv | Statement | Implied by ratified design? |
|---|---|---|
| INV1 | Version-gate primacy: no accept path yields a green that bypasses the version compare | **YES** on the promote/hand-back path (structural — conjunct outside the gate; `I-B6`); **as an absolute: NO** — `gate_livez`/`gate_readyz` lack the conjunct; restart/rollback can skip it (disclosed caveat, caught downstream by `promote.sh:853` before commit) |
| INV2 | First-census refusal for positively-foreign holders; no deferral burn on a positive `1` | **YES once C-1's rule is pinned** (contradictory as written) |
| INV3 | Deferral is bounded and applies only to unattributable-yet; never converts version-wrong or positive-foreign | **PARTIAL** — bound half exists (wrong arithmetic/scope, §2); "only-unattributable-yet" half requires C-1's downgrade clause |
| INV4 | pid-freshness/atomicity between census and classification | **NOT established and NOT needed** — acceptance judges the current occupant; worst case is a false refusal |
| INV5 | Segment-boundary cgroup matching | **Ratified but inconsistently specified** — C-0 |

---

## 2. Deferral seam containment (focus 2)

Three defects in the §5.4 spec, then the containment spec.

1. **Arithmetic ×2 error.** Pool=5 with `sleep 2` per iteration ⇒ 5×2s = **10s**, not the claimed "+5s". The pool counts iterations, not seconds. Fix: decrement by slept duration; the bound becomes honestly "+5s".
2. **"Process-global" is false at production call sites.** `_GATE_ATTR_DEFER_REMAIN` cannot write back through `$( )`: `promote.sh:790/799/1060/1065`, `rollback.sh:267/272`, `restart.sh:284/294` each fork a fresh pool ⇒ **per-call ≤5s (≤10s pre-fix) at each**, only the hand-back verifier (one enclosing subshell) gets a true per-shell pool. Worst case ≈ +20s across the promote flow (still inside budgets) — **but the plan's "+5s max" and I-B8's wording are wrong and will mislead the next budget audit**. Fix: restate scope per-shell; update the C-10a BOUND TABLE arithmetic in **both** copies byte-identically (no test pins the block — verified — but the table's "all-worst ≈1340s" sum must absorb the new sub-budget line, and Phase 2's B10 pin is the guard).
3. **The seam is unclamped.** A leaked `_GATE_ATTR_DEFER_S=300` yields a 600s pool consuming the entire verify budget (verdict degrades to a mislabeled budget abort); `0`/negative are loop-form-dependent (an `until`-form loops forever); non-numeric is a noisy bash error. The cited precedent `_COOLDOWN_DATE_UNAME` is a *selection* seam — it cannot extend a window — and is not precedent for a numeric budget seam.

**Containment spec (REQUIRED — pure containment, no ratified decision re-opened):**
- integer-parse the env (house `_numeric_knob` router for the loud-garbage note; see D3 for the D-P1-2 letter),
- **clamp to `[0,5]`** — the load-bearing line; a leaked 300 becomes 5,
- explicit `while [ "$_GATE_ATTR_DEFER_REMAIN" -gt 0 ]` guard — kills the `until`-form infinite loop,
- count the pool in **seconds**,
- never export; plain shell var initialized per shell,
- GNU/BSD-safe throughout (integer arithmetic only).

**Recommended (defence in depth, needs D3):** dual-key — honour the override only when an explicit test marker env (e.g. `ENSEMBLE_TEST_GATE_DEFER=1`) is also present; absent ⇒ always 5. Closes the false-halt direction of a leaked `0` too. No test in the plan needs a value >5 ⇒ the ceiling costs zero testability.

---

## 3. rc taxonomy surface (focus 3)

**Verdict: SOUND — no defect in the taxonomy itself.** Verified against `systemctl(1)` exit-status semantics and the incident log (rc=3 is the *routine* pre-start answer — the mislabel fired on every clean ceremony).

- **Coverage:** every reachable rc at every site is covered (`0` no row; `124` budget byte-identical; `3` INFO-no-row at sites 2/3; `4`/other ⇒ `kind=nonzero_exit` + true rc; the hard-coded `rc=124` at the reset-failed site dies). No `is-enabled`/`is-failed` sites exist; the third `is-active` probe (`_df_systemctl`) discards rc — no twin.
- **Forensics of dropped rc=3 rows — one REQUIRED closure:** the planned INFO wording carries **no site token**; post-mortem greps for `handback_is_active`/`scope_arm_is_active` (the exact tokens `tests/test_bounded_subprocess_waits.sh:327/343/428` assert) would find nothing. **Closure:** INFO line carries `site=…`, the state token, and the rc; fix the `${isact:-inactive|activating}` fallback literal; do not assert "expected pre-start answer" for the `failed` state. Pre-`journal_init` allowlist residual (sites `:82`/`:96` are stderr-only before `resolve_env`) is acceptable; cheap optional closure: set the log file before arg-parse refusals.
- **Consumer census: `kind=nonzero_exit` needs NO registration.** `ALERT_KIND_BY_EVENT` is event-keyed and `subprocess_wait_timeout` is absent from it and from `_TERMINAL_EVENTS` (size-10 pin `tests/unit/tools/test_upgrade_serving_green_closer.py:564` untouched); `journal_read.py`/`ledger_check.py` read zero row tokens; the sweep has no budget/nonzero predicate; no consumer counts or waits on budget rows. `nonzero_exit` already exists as a house row token (`lib.sh:5290/:5320`).
- **Fence register (survives):** rc=124 arms keep rows and WARN wording byte-identical (`:327/:343/:428-429`); the 7b banned literal (`assert_not_contains "systemctl start"`, 14 pins) obligates the wording "the start attempt" — an implementer writing "the systemctl start attempt" trips it.
- **REQUIRED — stub fidelity:** all three systemctl stubs exit 0 while printing `inactive` ⇒ **no existing test exercises rc=3 at any site** and the `:376` "no timeout events" pin is currently **vacuous**. Phase 2's `SC_IS_ACTIVE_RC`/`SC_RESET_FAILED_RC` knobs do not exist yet (P2-T1 prerequisite, already planned); additionally make the stub default **rc-faithful** (exit 3 on `inactive`) so `:376` becomes a live policy pin and the incident is reproducible in-test.
- **scope_arm_is_active minimality: PROVEN** — labels/journaling only; every non-zero rc blanks `_sc_isact` ⇒ identical accept/refuse outcome before and after; one `lib.sh` edit covers all five `restart_via_launcher` callers (promote/restart/rollback twins).
- **Halt chain: UNCHANGED** — walked end-to-end; no new 0-on-refuse path; the ladder's "unprobeable ⇒ 2" is strictly more conservative than Layer 2's "unprobeable ⇒ 0". Anchor correction: the halt span is `promote.sh:684-700` (exit at `:700`).

---

## 4. Floor/stamp mechanics (focus 4)

**Verdict: SOUND-WITH-CLOSURES.** P-1…P-10 all verified in code (`lib.sh:740-787`, `stage.sh:774/795/817/835`, `upgrade_tools.py:3938-4348`); `1aa0065eb` ancestry arm-clean state independently re-verified; family set exactly 17; the 7-file bump set is conflict-free against the +55 drift.

### 4.1 The chicken-and-egg resolution is right — with one wording fix

The two-commit minimum is structural **given the truthfulness requirement** (a commit cannot embed its own sha ⇒ the value cannot name the commit that carries it — but a one-commit release naming a *pre-fix ancestor* is constructible; that is option D). State it this way to prevent a future "why not one commit?" re-litigation. `--no-ff` is pinned (P3-D11) but is **not load-bearing**: if M degenerated to a fast-forward, P-1…P-6 still pass and the value names the lane tip; only the docstring wording and (accidentally) the coverage property degrade — document, don't gate.

### 4.2 BLOCKING — T7/AC-P3-8 is circular as written

Stage-time knows nothing of M: `FLOOR_COMMIT`/`STAMP_REV` are sed-extracted **from the tree being staged** and validated **against that same tree**; the manifest floor field has **zero consumers**; P-6 (content of the named commit) passes for `1aa0065eb` too ⇒ **option D and sequence S6 (fix hunks missing at M — dropped in conflict resolution / re-pin carried by mistake / B cut before the re-pin) pass every existing gate silently.**
**Closure — replace "assert pair == M" with graph+content facts (none reference the pair):**
1. `merge-base --is-ancestor <X> HEAD` (existing P-5), **plus**
2. **content-at-X**: `git show <X>:scripts/upgrade/lib.sh | grep -q '_GATE_ATTR_DEFER_S'` (and the promote.sh gate-widening + restart.sh journaling markers) — the only check that reddens option D and S6,
3. **minimal introduction**: X is the first `--first-parent origin/latest` commit satisfying (2),
4. **shape proof**: `git show <X>:…lib.sh` (line ~95) and `…upgrade_tools.py` (line ~4145) still carry the **pre-bump** `1aa0065eb` ⇒ proves the re-pin rode B, not M (closes S7),
5. `git rev-parse <X>^2` == the lane tip Phase 2 reddened/greened (proves X *is* the landing merge),
and record `M_sha=<40-hex>` in the **tag annotation** and the handoff — the audit trail must be external to the tree. Restate AC10 as the non-circular chain `sidecar.git_head == B` ∧ `git show B:daemon/tools/upgrade_tools.py` floor `==` manifest floor (the re-pin compiles into the frozen binary via `ensemble.spec` hiddenimports; it is NOT in `daemon_slice_tree_sha256` — the PYZ symbol method is under-specified; use the chain).

### 4.3 Stale re-pin ledger

S1 (rebase rewrites M) ⇒ P-5 CLOSED · S2 (lane lands between M and B) ⇒ **NOT A DEFECT** (named-commit design is exactly why it's benign) · S3 (M re-created) ⇒ P-5 CLOSED · S4 (T moved) ⇒ exact-match + tip-identity CLOSED · S5 (B cherry-picked) ⇒ closed/benign · **S6 (fix missing at M) ⇒ closed only by §4.2(2)** · S7 (re-pin inside M) ⇒ closed by §4.2(4). Operational note: `tests/test_pipeline_scripts_floor_stamp.sh` P3-T08 is circular by construction and **will redden in the lane worktree after B** (tree without M) — run post-B validation in the stage worktree or after the FF, or the lane chases a phantom red. ⚠️ **Mapping marker (wave 4; this historical file is otherwise left as written):** "P3-T08" was renamed **`P3-PK-FLOORSTAMP`** at its definition site in `phase3-plan.md` (wave 3) — the label here refers to that same pin.

### 4.4 Merge-agnosticism (D-P3-1 = MERGE)

Every predicate is sha/ancestry-based, none first-parent- or type-sensitive. What changes identity-wise: **only M's sha** (the two constants). The plan set is stale on the ruling (`decisions.md:262`, §9, overview §9 still read "escalated") — record the ruling and merge-shape consequences in `decisions.md` (append, don't renumber).

---

## 5. Fix-to-live path (focus 5)

**Division (confirmed):** the pin is the executing tree — corroborated by the incident log itself (`:17` resolves the stop script from the scripts-pin worktree); the payload's `scripts/` copy is not a resolution candidate (`_resolve_scripts_dir:2049-2058`; `scripts/` absent from `ensemble.spec` datas).

- **Lane-verifiable, zero live contact:** the §4.2 assertion set in the stage tree; `M_sha` in tag annotation + handoff; offline `git merge-base --is-ancestor M T` dry-check that the pin target satisfies the guard; stage argv (`live`, `ENSEMBLE_ROLLBACK_SAFE=1`), 4-way sha, sidecars; CHANGELOG disclosure #5.
- **Operator/Emperor-owned:** the actual `git -C <pin-worktree> checkout <T-sha>`; any `.env` edit; the promote itself.
- **REQUIRED — pin preconditions (the silent-arming modes):** non-git pin ⇒ arms WARN-only (`:4406-4414`) ⇒ precondition asserts `<pin>/.git` exists and `rev-parse HEAD` resolves; dirty pin ⇒ arms with `dirty=yes` journaled only ⇒ precondition `diff-index --quiet HEAD --` + **exact-HEAD == T's sha** (not the tag name); `.env`-value change without restart is invisible (no dotenv reload in the daemon) ⇒ **prefer advancing the checkout** (no `.env` edit, no restart) and audit via the `arm-scripts-provenance` journal row.
- **REQUIRED — ordering (provable, strengthens D-P3-2):** advance the pin to T **BEFORE** the promote. Safe under the old binary: T ⊇ `0181dadbf` ⊃ floor `1aa0065eb` ⇒ the v0.31.0 arm guard sees an ancestor and arms; safe in content: scripts drift across the 55 commits is zero ⇒ T's scripts are exactly v0.31.0's + this fix. Inverted order ⇒ this ceremony runs the defective executor and the fix's delivery slips a ceremony.
- **REQUIRED — disclosure:** the restart lane has **no provenance guard** (`system_restart` resolves the dir but never runs `scripts_dir_provenance_verdict`) — until the pin advances, the restart arm executes pre-fix `restart.sh` unjournaled; state it in the window's disclosure. Also correct `docs/runbooks/upgrade-drills.md:550` (claims a release-local bundle rung that does not exist in code — the pin is the *only* path; record the drift-0 mixing proof).

---

## 6. Missed interplay (focus 6)

- **`halt` rows are NOT inert — the one reader that changes:** `ledger_check.py:74` `VIOLATION_EVENTS` includes `halt` ⇒ the 7 newly-journaled restart refusals mark the drill cycle VIOLATION (break the N-clean streak), and `ALERT_KIND_BY_EVENT["halt"]` pages each as `upgrade_cap_halt`. Defensible (post-adoption restarts already journal `halt`) but it is a **behaviour change to a machine-checkable gate** — DISCLOSE in CHANGELOG/tag annotation and PIN in Phase 2.
- **adopt-unit: unaffected** — one-shot verify, sed-extracts the last `supervision_boot` from `state.json`, no gate calls, no timing coupling with the deferral.
- **Journal readers: inert** — event-keyed alert map; `subprocess_wait_timeout` in neither `_TERMINAL_EVENTS` nor the alert map; fewer budget rows changes no behavior.
- **Sweep/closer contract: untouched** — P1–P9 are txn-based (`in_flight`/`flipped`/`intent_flip`); no predicate waits on a row the fix stops producing; fewer false-halt rows ⇒ simply nothing to close.
- **Grace-floor coupling: verified** — `_closer_boot_grace_s = max(BOOT_GRACE, HANDBACK_VERIFY_BUDGET_S)`; no numeric knob changes ⇒ the "no daemon change" claim holds.
- **Executor family: unchanged** — Phase 1 edits only existing members; Phase 2 adds non-members.
- **`_run_bounded` residual (doc):** 137/143→124 means an OOM-killed probe still reads "timeout" — `_run_bounded` stays byte-unchanged (I-A5); document next to the taxonomy table.
- **Sites 4/5 asymmetry (decision D2):** the *start* arms journal nothing on a genuine non-124 rc — add symmetric `kind=nonzero_exit` rows (recommended) or record the omission in §4.3.
- **Stage-time facts (one line in the handoff):** `bump_version.py`'s `uv sync` materializes a `.venv` in the lazy worktree; the stage build needs a warm uv cache/network.

---

## 7. Consolidated closure register

**BLOCKING (before surgery):**
1. C-0 body/ledger predicate agreement (§1.1) — markers + verbatim predicate in the coding commission + NP-6 arm-(b)-only.
2. C-1 single normative composition rule incl. the downgrade clause + M-NP1c (§1.1).
3. T7/AC-P3-8 non-circular assertion set + `M_sha` external record + AC10 chain restatement (§4.2).

**REQUIRED (cheap, additive; no ratified decision re-opened):**
4. Seam containment: parse + clamp `[0,5]` + seconds-counted pool + `>0` loop guard; honest per-shell scope; I-B8 + C-10a arithmetic corrected (§2).
5. INFO line carries `site=` token + state + rc; fallback literal fixed (§3).
6. Stub fidelity: `SC_*_RC` knobs + rc-faithful default (de-vacuous `:376`) (§3).
7. C-2: skip Layer 2's unit-arm re-test for ladder-accepted holders; correct rationale recorded (§1.2).
8. C-5: R3 completion semantics stated (walk completes ⇒ answered-1) (§1.2).
9. Pin preconditions (git-shape, exact-HEAD, clean tree) + pin-BEFORE-promote ordering + restart-window and runbook disclosures (§5).
10. `halt`-row VIOLATION/paging widening disclosed + pinned (§6).
11. Plan-set updates: D-P3-1 ruling recorded; halt-span anchor `:684-700`; S2-benign note (§4).

**RECOMMENDED:**
12. Dual-key test marker for the seam (needs D3) (§2). 13. Sites 4/5 symmetric rows (D2) (§6). 14. C-7 KillMode=mixed stale-incarnation doc as P12 sibling (§1.2). 15. Drivable NP-6 equivalent (stub cg = non-segment-aligned prefix) (§1.1). 16. Allowlist stderr-only closure (log file before arg-parse) (§3). 17. Two-commit-minimum truthfulness wording (§4.1).

## 8. Plan changes required

The plan's load-bearing structure (A/B/C adoption, ladder-as-second-accept-path, segment-aware R2, pooled deferral, F→M→B→T with value=M, pin-as-the-only-path) **stands**. Required changes: items 1–11 above, routed as: 1–2, 7–8 → Phase-1 task text + Phase-2 NP amendments; 3 → Phase-3 P3-T7/AC-P3-8/AC10 rewrite; 4–6 → Phase-1 §4/§5.4 + Phase-2 stub prerequisites; 9–11 → Phase-3 preconditions + disclosures + decisions.md append.

## 9. Decisions pending (leader)

- **D1 — deferral exhaustion semantics.** Ratified: exhaustion ⇒ terminal refuse (fail-closed at ≤5s). Worker C's finding: exhaustion is *terminal through the probe's fail-fast*, so a boot that becomes attributable at t+8s halts with ~290s of verify budget unspent — re-conflating exactly the two classes the ladder separates. Proposal: exhausted-`2` ⇒ **transient** (gate returns 2; poll-loop consumers keep polling within their existing budgets; distinct exhaustion marker journaled). Trade-off: never-attributable pathological boots burn the full verify budget before halting (bounded, rare) instead of +5s. **Architect recommendation: ADOPT** — it serves the commission's #1 objective, preserves every safety property (class-2 never touches the version conjunct; positively-foreign still refuses first-census), and the cost is bounded by budgets that already exist. Not adopted unilaterally: it re-opens ratified D-P1-2/P10 semantics.
- **D2 — sites 4/5 journal symmetry** (recommend: add the rows).
- **D3 — D-P1-2 letter amendment**: "not a `_numeric_knob`" → "not an *exported production knob*; garbage-parse via the `_numeric_knob` router + mandatory `[0,5]` ceiling" (recommend: amend; the ceiling is required regardless — only the router routing needs the letter change).

## 10. Confidence and assumptions

**High** on the bound verdict (three independent anchor-verified analyses; the centerpiece claim is structural, not procedural; the two blocking defects are corroborated by the architect's own reads of the plan files). **Assumption that would flip it:** the version conjunct's position at every accept surface — verified at base and stable across the drift (zero `scripts/upgrade/` files in the +55), but must be re-grepped after the sync-merge per house ANCHORS-ARE-LEADS. The fixture-model risk (plan risk 4) remains Phase 2's obligation; this analysis used the real fixture log, not stubs.
