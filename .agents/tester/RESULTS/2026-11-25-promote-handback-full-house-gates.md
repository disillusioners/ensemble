# FULL HOUSE GATE — promote hand-back false-halt, landing merge M = 8d306f2e

**Date**: 2026-11-25 · **Tester lane**: promote-machinery · **Tree**: `/home/nea/ensemble-src-wt-maintsys-cut2` (branch `latest`)
**Landing merge M** = `8d306f2e31b7ed8b26ce1f273e5c75db5982cb4b` (parents P1 = `35ec50e3c` = origin/latest pre-merge = RED base; P2 = `6056d1d05`)
**Refresh merge R** = `5ba3223edda631669f392f081e4c66473fbf0013` (parents `573c5c4b1` + `35ec50e3c`) — 3 hand-resolved conflicts H1–H3 in `scripts/upgrade/lib.sh`
**Commits added by this gate**: `dc92b8b4` (harness), `2b0ad740` (P-38 fixture re-anchor), `4dd98327` (d3 nounset-on leg) — all unpushed; no promote, no live-install mutation.

---

## 0. VERDICT

| Gate | Verdict |
|---|---|
| **G1 — evil-merge check** | ✅ **PASS** — M: 0 evil. R: 39 evil-add lines, **all** inside H1/H2/H3, 0 elsewhere; 19 removed lines independently recounted = 19, 1:1 to the ledger. |
| **G2 — RED-at-base / GREEN-at-merge** | ✅ harness built and committed; the **cure** is 46/46 green at M and red at base across a/b/e/f/g/h. ⚠️ but M is **RED on the merge-defect leg (d2)** — see G2b. |
| **G2b — C1 independent verification** | 🔴 **CONFIRMED — merge-introduced, real-lane severity = CALLER ABORT** |
| **G3 — lane gate packs** | 🟡 **10 of 12 legs green; 1 leg RED with 2 NET-NEW (the C1 defect); 1 leg carries 33 pre-existing (registered/quarantined) reds with net-new 0** |
| **G4 — fail honestly** | 🔴 **OVERALL: FAIL — M is not green.** One blocking defect (C1), one accepted test-side quick fix, zero unclassified failures. |

**Blocking finding (one):** `scripts/upgrade/lib.sh:7515-7523` — the H3 conflict resolution severed a bash
line continuation. Introduced at **R** (`5ba3223ed`), inherited unchanged through P2 → M. Under `set -u`
— which `promote.sh:69`, `restart.sh:63` and `rollback.sh:32` all set before sourcing lib.sh — the call
aborts at `lib.sh:1890` (`$2: unbound variable`), so the **promote shell dies mid-hand-back**, bypassing
the clean-halt arms (no `halt` event, no side-channel cleanup). Fix specified and sandbox-validated
(one-line comment relocation); **NOT applied** — production is frozen; the call is the lane owner's.

---

## 1. SCOPE DECISION

**Blast radius (mechanically derived, W0):** `git diff --name-only 35ec50e3c...8d306f2e` = **14 files** —
`scripts/upgrade/lib.sh` (+828/−76), `scripts/upgrade/status.sh` (+19/−4), `tests/test_adaptive_soak.sh`
(+11/−1), 11 additive planning docs under `.agents/shared/planning/promote-handback-false-halt/`.
**Zero `daemon/` files, zero `frontend/` files, zero migrations** ⇒ Tier 1 (blast-radius leg), not Tier 2
(the core-seam list in ensure.md is not touched).

**Ran (12 legs + 1 new harness):** the lane-gate pack, the deferred regression set (bounded-waits, budget,
serving-green, stop-handback, adaptive-soak), `test_launcher.sh`, the P11 drift guard (BOTH shapes), and
four blast-radius-driven additions (§1.1). One new harness pack committed.

**§1.1 Scope ADDITION (blast-radius-driven, not count-driven):** `supervision_journal`,
`supervision_twins`, `ceremony_wallclock`, `hbk_err_inode_churn` were added because the change rewrote
lib.sh's **journal-writer**, **budget-identity** and **reason-channel** surfaces — exactly where C1 landed.
Each is cheap (4–22 s) and registered. NOT run: the other ~37 PACKS.md rows naming `scripts/upgrade`
(mostly read-only scans, privileged/systemd-dependent legs, and `test_release_journal` which is
PRE-EXCLUDED at rc=124 on this host) — count-based expansion is forbidden, and the enumerated lane set is
the leader's standard.

**ensure.md scoping:** Core #1 ("no regressions in changed packs") is satisfied by the G3 set (below).
Core #2/#3 (`concurrency_atomic_unit_test`) and #4 (`dev.sh` grep) are **out of blast radius** (no daemon/
or `dev.sh` change) — not run, by ensure.md's own "scoped by blast radius" rule. The **Release Gate** (full
non-integration suite + 4 live-daemon E2E cells) is **not warranted**: the change is shell scripts under
`scripts/upgrade/` + one test script; no daemon code, no live daemon required.

---

## 2. G1 — EVIL-MERGE CHECK (worker `phh-g1-evilmerge`)

| Merge | Verdict | EVIL_ADD | EVIL_DEL | Files scanned |
|---|---|---|---|---|
| **R = 5ba3223ed** | ✅ **PASS** on the stated criterion | **39** (all inside H1/H2/H3) | 0 | 1704 |
| **M = 8d306f2e3** | ✅ **PASS** | **0** | **0** | 14 |

**M — zero hand-resolution, proven three ways.** `git show --remerge-diff 8d306f2e3` → **empty** (0 hunks);
`diff(35ec50e3c→M)` and `diff(35ec50e3c→6056d1d05)` are **byte-identical** (`cmp` rc=0, sha `ffe1e3c1ac926e73`
both); `rev-parse M^{tree}` == `rev-parse P2^{tree}` == `dc964bc75148674f3c1261170bd647b11cc08a4a`.
M is a **trivial** merge (`merge-base P1 P2` = P1; P1 is an ancestor of P2) — recorded as `--no-ff` for
the first-parent chain. Per-file set-compare over all 14 changed files: EVIL_ADD=0, EVIL_DEL=0 each.

**R — every evil line maps 1:1 to the ledger.** 39 resolver-written lines: **H1 = 13** (region 6887-6917),
**H2 = 19** (7405-7417 and 7419-7445), **H3 = 7** (7513-7525) — *programmatically contained: 39/39 inside the
three regions, 0 outside*. The **19 removed L-side lines** were independently recounted by set-difference
(`dropped_L 56 − removed_bb 41 = 19` — note the ledger's own `76−63=13` arithmetic is wrong) and map
**1:1 both directions**: H1=7, H2=11, H3=1 ⇒ **no unmapped evil line, no unmatched ledger row**. Files
carrying evil content: `scripts/upgrade/lib.sh` only — plus the merge-added ledger `.md` (142 unique lines),
which is the **disclosed audit artifact** (in neither parent by construction, disclosed in the merge message
and §10) — accepted as documentation, not covert content.

**Integrity cross-checks (all green):** 0 conflict markers at R and M (marker-content multiset identical to
P1); `bash -n` clean on both changed scripts; **no duplicate function definitions**; every §6
mechanism-survival anchor verified at M (`_bounded_rc_kind` :5264, `_bounded_rc_reason` :5278,
`_gate_attr_containment` :9520, ladder vars :9638/:9691-9701, deferral pool :9477-9492, `gate_defer_reason`
:9370 + `_gate_consume`/`_gate_outcome`/`_gate_defer_reset` :9391/:9443/:9469, `_handback_stash` **0 hits**,
`__attr_rc` local :6881 / capture :6895 / case :6896, `return 2` producer :9813 + `kind=attr_deferred` :9808).
**ROW 20 carried through byte-identical** (blob `5dcce777…` at M == at `6056d1d05`; `6056d1d05` is an
ancestor of M; `git show --stat 6056d1d05` touches only `scripts/upgrade/status.sh`).

**⚠️ LIMIT OF THIS GATE (cross-referenced to G2b):** the evil-merge method compares **line sets**. It cannot
see that mapped, ledger-justified content is *semantically* broken — H3's lines were all correctly mapped
and the defect still shipped. See LESSONS L2.

---

## 3. G2 — RED-AT-BASE / GREEN-AT-MERGE HARNESS (worker `phh-g2-harness`)

New pack **`tests/packs/promote_handback_false_halt_repro_test.sh`** (821 lines, 9 repros / 51 asserts),
committed **`dc92b8b4`** on `latest`, subject exactly
`test(upgrade): pin promote hand-back false-halt cure — RED-at-base/GREEN-at-merge harness`.
Drives the **real** `scripts/upgrade/lib.sh` + `status.sh` with stubbed externals (never a copy), inside a
`mktemp` FAKE_HOME with the house escape guard; per-variable env scrubs; ports > 10000 only.

| Repro | Assertion (abridged) | M | P1 (RED base) | Label |
|---|---|---|---|---|
| (a) | is-active rc=3 → INFO-class, `kind=state reason=state:<state>`, **no failure-shaped row**; rc=4 ⇒ `kind=nonzero_exit` | 4/8 | 4/8 | — |
| (b) | transient (fd-inheriting MCP child) → no `attribution-refused`, `kind=attr_deferred`, ladder ACCEPT, green | 6/6 | 0/6 | — |
| (c) | genuine refusal (foreign uncontained holder) → **still halts** | 3/3 | 3/3 | **PIN-only** |
| (d) | rc=124 is still a real timeout (`kind=budget`, `reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=2)`) | 5/5 | 5/5 | **PIN-only** |
| **(d2)** | **start-timeout row carries its detail** (nounset-OFF regime) | **0/5 FAIL** | 5/5 | **merge-defect (C1)** |
| **(d3)** | **the caller does not abort** (nounset-ON regime — the REAL promote lane): no `$2: unbound variable`, the caller reaches its arms, the row carries its detail | **3/11 FAIL** | 11/11 | **merge-defect (C1)** |
| (e) | ladder → rc=2 → probe case → caller `case "$hsp_rc"` re-probes → green | 6/6 | 1/6 | — |
| (f) | rc classes, **one** reason writer, transient leaves the reason channel empty | 9/9 | 1/9 | — |
| (g) | ROW 20 / T7e: transient note present, plain exit 0 | 5/5 | 1/5 | — |
| (h) | soak tick rc=2 ⇒ INCONCLUSIVE, non-fatal (base: FATAL "version drifted") | 4/4 | 0/4 | — |

**Cure gate at M: 46 passed / 0 failed** (a 8/8, b 6/6, c 3/3, d 5/5, e 6/6, f 9/9, g 5/5, h 4/4).
**Cure gate at P1: 15 passed / 31 failed** (red legs a 4/8, b 0/6, e 1/6, f 1/9, g 1/5, h 0/4; c and d are
PIN-only and green on both sides) — a clean red: 0 `command not found`, 0 syntax errors, 0 unbound-variable noise.
**Merge-defect gate (d2+d3): 3 passed / 13 failed at M, 16 passed / 0 failed at P1** — the inverse signature of a
merge-introduced defect, now proven in BOTH severity regimes.

**(d3) abort evidence captured by the committed pin at M** (the "caller abort" regime, verbatim from the
confirmation run):

```
[d3] NOUNSET-PROOF: nounset 	on
…/scripts/upgrade/lib.sh: line 1890: $2: unbound variable
```

nounset proven ON in the same shell that sources lib.sh and calls `restart_via_launcher`; the probe died at
lib.sh:1890 — **no `d3-rc=` was ever printed** (the shell aborted before the `echo`), and **neither
`D3-HALT-ARM-REACHED` nor `D3-SUCCESS-ARM-REACHED` appeared** ⇒ the abort bypasses BOTH arms; the journal
history is **empty** (`{"current":null,…,"history":[]}`) ⇒ no row at all. At P1 the same leg prints
`NOUNSET-PROOF: nounset on | D3-SUCCESS-ARM-REACHED | D3-POST-HANDBACK-REACHED | d3-rc=0` — clean.

**Measurement correction (the leader's framing of P1 was imprecise):** the base does **not** label rc=3 as a
timeout — L's two-way split (`865ec4ad3`) already landed. What the base does is **WARN** rc=3 (M logs it at
INFO class), omit `reason=state:`, label rc=4 as `kind=state` (M: `kind=nonzero_exit`), treat the soak
tick's rc=2 as **FATAL** ("soak FAILURE: version drifted") where M treats it as INCONCLUSIVE, and — at the
base — **poison the reason channel** with an `attribution-refused` string from a transient.

---

## 4. G2b — C1: INDEPENDENT VERIFICATION (worker `phh-c1-verify`) 🔴

**VERDICT: CONFIRMED — every clause, plus the severity refinement.**

```
7515  journal_history_append subprocess_wait_timeout \
7516      # RESOLVED H3 (branch dynamic reason × L 0f133d2b9/865ec4ad3
…     # (six comment lines, 7516-7521)
7522      "site=handback_start kind=budget reason=child: $(_bounded_rc_reason "$sbrc" …) unit=$unit rc=$sbrc — …" \
7523      || _warn "subprocess_wait_timeout journal event append FAILED (best-effort)"
```

Bash removes the `\`+newline, then `#` comments to EOL ⇒ the payload argument is never passed.
`journal_history_append()` (lib.sh:1889-1891) is `_splice_journal append-history "$1" "$2"` — `$2` unguarded.

| Regime | Measured |
|---|---|
| nounset OFF (harnesses) | `argc=1` ⇒ payload LOST; payload string executes as a command (rc 127); `\|\| _warn` fires a misleading "append FAILED"; row = `{"event":"subprocess_wait_timeout","detail":""}` — no `site=`/`kind=`/`reason=`/`rc=` |
| **nounset ON (THE REAL LANE)** | aborts at **lib.sh:1890** `$2: unbound variable` ⇒ **NO ROW AT ALL**, `\|\| _warn` never fires, and **the abort propagates**: the shell exits rc=1 inside the hand-back, **bypassing** the clean-halt arms (`halt` event + side-channel cleanup lost). Exit code 1 coincides with the clean-halt arm's `exit 1` ⇒ a supervisor sees the same code with less evidence. The txn was already `flipped:true`, so the B4 leave-txn-open contract still holds and the launcher sweep can still roll back. |

**Real callers run with nounset ON** (`set -u` before `source lib.sh`, no `set +u` anywhere):

| Caller | `set -u` | source | reaches the branch? | Severity |
|---|---|---|---|---|
| `promote.sh` | :69 | :123 | **YES** (`restart_via_launcher` :950, :1431) | 🔴 **CALLER ABORT** |
| `restart.sh` | :63 | :88 | **YES** (:279) | 🔴 **CALLER ABORT** |
| `rollback.sh` | :32 | :58 | **YES** (:269) | 🔴 **CALLER ABORT** |
| `status.sh` | :26 | :43 | **NO** (no `restart_via_launcher`) | not affected |

**Entry point:** introduced at **R = 5ba3223ed** (the H3 resolution). Parents clean (`573c5c4b1` blob
`f01714d9`; P1 blob `edc14878`); R, P2 and M all carry blob `bf5cb77e`. **Whole-file scan: exactly ONE
severed continuation** (`7515 → 7516`); of 68 `journal_history_append` calls only this one has comment lines
between `\` and payload; `status.sh` = 0; **no other `$2`-style unbound-abort site exists**.

**Per-reader blast radius:**

| Reader | Fire? |
|---|---|
| `tests/test_bounded_subprocess_waits.sh:450` (S5) / `:496` (S5b) | 🔴 **FIRES RED** (2 net-new) |
| `tests/packs/promote_handback_false_halt_repro_test.sh` d2 | 🔴 **FIRES RED** (0/5) |
| `promote_handback_halt_gate_legs_test.sh` D6-A (:611-614) | ⚪ no fire — different, intact path (coverage gap, not a contradiction) |
| AC-D6-4 behavioural contract ("start timeout NOT terminal") | ⚪ **intact** — control flow unaffected; only the journaling evidence is lost |
| `test_supervision_twins.sh` | ⚪ no fire — its scope never inspects a journal-history call's argv/payload (**coverage gap**) |
| `test_supervision_journal.sh` | ⚪ no fire — its J-stamps are txn fields (`supervision`/`unit`/`outcome`) by design; the severed row is outside its coverage and unreachable under its stub |

**Fix (validated in sandbox, NOT applied):** **option A** — move the six comment lines 7516-7521 **above**
the `journal_history_append` line (result: `argc=2`, full payload, nounset-ON no longer aborts).
**Option B ("move the trailing `\` to :7521") is WRONG** — a `\` on a comment line does not rescue the call
(still `argc=1`, payload still runs as a command rc=127).

**Ledger fidelity:** FALSIFIED — §4 H3 "byte-identical … no L-side parser loses its token"; §4 H3 "KEPT"
(dynamic reason + true `$sbrc` — present as source text, never executed); §5c row 19; §8's `:450`/`:496`
"satisfied". REMAIN TRUE — §5 "no runtime control decision reads those rows"; §5c's static counts; H3's
*string* property; §6's mechanism anchors. **A dated correction (§11) was appended to the ledger by the
tester gate** — the original text is left intact.

---

## 5. G3 — LANE GATE PACK RESULTS

AC coverage was **grepped, not assumed** (W0): D6-6 / D2-5 / D5-8 / D5-10 (halt, green, Tier-B) live in
`promote_handback_halt_gate_legs_test.sh`; AC-D1-5 (B7c :854, B7d :882) and **M19** (:2157) in
`test_supervision_stop_handback.sh`; **8w** (:886-932) in `test_launcher.sh`; S4/S4b/S4c + the **:404**
state-row reader in `test_bounded_subprocess_waits.sh`; **D6-A** + the **:614** reader in the legs pack.

| # | Pack | At M | At base | Net-new | Verdict |
|---|---|---|---|---|---|
| 1 | `promote_handback_halt_gate_legs_test.sh` | **86/86**, 74 s, rc=0 | not run (green) | 0 | ✅ **PASS** — D6-A2 (zero timeout rows) + D6-A4 (state row) green ⇒ the H2 ruling holds |
| 2 | `test_bounded_subprocess_waits.sh` | 72P/**3F**, 41 s, rc=1 | 74P/1F, 38 s | **2 (S5 :450, S5b :496)** | 🔴 **FAIL — C1** (3rd failure `R happy-path` is the registered pre-existing host-class red) |
| 3 | `test_supervision_stop_handback.sh` | 232P/**33F**/1S, 110 s, rc=1 | 233P/33F/0S, 105 s | 0 | 🟡 **FAIL (pre-existing)** — the 33 are the REGISTERED QUARANTINE set `supervision-33` (232P/33F/1S, empty diff); B7c/B7d/M19 green |
| 4 | `test_launcher.sh` | **519P/0F/1S**, 99 s, rc=0 | 519P/0F/1S, 84 s | 0 | ✅ **PASS** — but the pack is **blind to the cure** (`launcher.sh` sources nothing from lib.sh; subjects byte-identical) ⇒ regression evidence only |
| 5 | `handback_verify_budget_test.sh` | **120P/0F**, 171/172/181 s, rc=0 ×3 | not run (green) | 0 | ✅ **PASS** — the disclosed L8b boundary flake did **not** fire in 3/3 (byte-identical logs); the 17-mutation matrix executed ⇒ non-vacuous |
| 6 | `promote_handback_halt_serving_green_test.sh` | 136P/**4F**, 198 s, rc=1 (after the accepted fix) | 136P/4F (same pack), 196 s | 0 | 🟡 **FAIL (known base set)** — exactly the registered `serving_green-4` (136P/4F); pre-fix HEAD was 134P/6F ⇒ 2 net-new, resolved by the accepted fixture re-anchor |
| 7 | `test_adaptive_soak.sh` | **35P/0F**, 202 s, rc=0 | not run (green) | 0 | ✅ **PASS** (first registered run — the script had NO PACKS.md row; the tester registered it) |
| 8 | P11 drift guard — **shape 1** @ `8d306f2e` | 13P/**1F**/14 nodes, 5.8 s, rc=1 | n/a | 0 | 🟡 the RED is the pre-existing `frontend/` allowlist pin (diff input byte-identical at P1 and HEAD; the landing touches zero `frontend/` files) |
| 9 | P11 drift guard — **shape 2** @ the post-harness tree (`dc92b8b47`) | 13P/**1F**/14 nodes | n/a | 0 | 🟡 the same single pre-existing RED; **the six merge-relative nodes regained their teeth** (span = `8d306f2e..dc92b8b47`, non-empty) and all six path filters are empty over it ⇒ genuine green, not vacuous. See §5.1. |
| 10 | `test_supervision_journal.sh` | 11P/**26F**, 13 s, rc=1 | 11P/26F, 12 s | 0 | 🟡 **FAIL (pre-existing fixture rot)** — `stage.sh:344` requires a `docs/guidelines` payload the fixture never creates (since `ccb7962d5`); the registered green rows (39/0/0) are stale ⇒ **the pack certifies nothing about this landing and is OFF the gate set** |
| 11 | `test_supervision_twins.sh` | **97P/0F**, 22 s, rc=0 | not run (green) | 0 | ✅ **PASS** — and it **cannot** see C1 (coverage gap) |
| 12 | `test_ceremony_wallclock.sh` | **50P/0F**, 4 s, rc=0 | 50P/0F, 5 s (byte-identical output) | 0 | ✅ **PASS** — all 11 shipped budget knobs bind (shipped == resolved == documented); C1 invisible here (budget table only) |
| 13 | `test_hbk_err_inode_churn.sh` | **41P/0F**, 15 s, rc=0 | not run (green) | 0 | ✅ **PASS** — the reason-channel side-file writers are intact (a different writer family) |
| — | `promote_handback_false_halt_repro_test.sh` (new) | **RED** — exit 1, `RESULT: FAIL`, **49P/13F**; cure gate **46/46 green**; merge-defect gate **3P/13F red** (d2 0/5, d3 3/11) | **RED** — exit 1, `RESULT: FAIL`, **31P/31F**; cure gate **15/46 red**; merge-defect gate **16/16 green** (d2 5/5, d3 11/11) | — | the C1 repro, **non-vacuous in both regimes** (RED at M, GREEN at P1) |

**Net-new accounting: 2 net-new failures, both = C1, both from the pre-existing landed reader. Zero
unclassified failures across all 13 legs.**

### §5.1 The P11 drift guard must be run in TWO shapes — at the merge it is half-vacuous

The guard resolves its span as `git rev-list --first-parent --merges -1 HEAD` → `span = merge..HEAD`
(POK `land-01`). Measured:

| shape | HEAD | span | 6 merge-relative nodes | verdict |
|---|---|---|---|---|
| **shape 1** | `8d306f2e` (HEAD *is* the landing merge) | `8d306f2e..8d306f2e` = **EMPTY** | **vacuous** — they judge nothing and pass trivially | 13P/1F |
| **shape 2** | `dc92b8b47` (one commit above the merge) | `8d306f2e..dc92b8b47` = **non-empty** | **teeth on** — all six path filters empty over a real span ⇒ genuine green | 13P/1F |

Both shapes: **13 passed / 1 failed** — the same single RED, the pre-existing `frontend/` allowlist pin
(`test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level`), whose input diff is
**byte-identical at P1 and at HEAD** and whose target path set the landing never touches ⇒ base-attributed,
**net-new 0**. The other 8 nodes are shape-independent (2 file-state, 4 synthetic, 1 fixed-span, 1 fixed-base).
**Practical rule: a landing gate that measures the P11 guard must ALSO measure it one commit above the
merge, or six of its fourteen pins are reporting nothing.** (This vacuity is by design — the guard is a
post-merge drift guard, not a merge-shape checker — but it must not be read as protection at the merge.)

---

## 6. QUICK FIXES APPLIED (test-code only)

| Commit | File | Change | Justification | Verification |
|---|---|---|---|---|
| `2b0ad740a` | `tests/packs/promote_handback_halt_serving_green_test.sh` | 3 inserted / 2 deleted lines — the P-38 **stub fixture** re-anchored to the ratified containment-ladder shape (`ControlGroup` + holder cgroup) | the merged ladder needs `ControlGroup`; the stub answered only `MainPID`, so the fake holder `999999` became unattributable and the C-1 downgrade turned R3's answered negative into an rc=2 deferral (the halt read `handback-budget-exhausted` instead of the ratified `attribution-refused`). The **assertion** was NOT weakened — only the fixture shape | re-run: 136P/4F, byte-identical to the base leg with the same pack ⇒ net-new 0 |
| `dc92b8b47` | `tests/packs/promote_handback_false_halt_repro_test.sh` + PACKS.md | the new harness pack (821 lines) + its registration row | the G2 gap-filling deliverable the leader authorised | RED at base / GREEN-at-merge (cure) + the C1 repro |
| `4dd98327` | `tests/packs/promote_handback_false_halt_repro_test.sh` + PACKS.md (+120/−6) | the **d3** leg — the NOUNSET-ON / caller-abort regime (the first harness run under-reported real-lane severity because its `phh_run` had no `set -u`) | the committed harness must pin the regime the REAL promote lane runs in | confirmed **non-vacuous**: 3/11 RED at M with the verbatim `$2: unbound variable` abort + no journal row + neither arm reached; **11/11 GREEN at P1** with `d3-rc=0` and both arms reached |

**No production file under `scripts/upgrade/` was modified by any lane in this gate.**

---

## 7. FENCE COMPLIANCE

- **Never** read / `cd`'d into / ran anything under `/home/nea/ensemble-src` (detached HEAD, drifts by
  design) — except the house-sanctioned read-only use of its `.venv/bin/python` **interpreter binary** where
  the worktree venv was absent (the worktree venv exists: Python 3.14.7). The two forbidden fix worktrees
  (`…-wt-fix-promote-handback`, `…-wt-hbfix`) were never touched.
- **No promote; no live-install mutation** — nothing under `/home/nea/agents-ensemble` was written (`current`,
  `state.json`, journal, `releases/`); two lanes recorded the live install's mtimes before/after as proof.
- **No branch/tag deleted or renamed; no push; no fetch; no amend; no rebase.** Two commits were added on
  `latest` (`dc92b8b4`, `2b0ad740`), unpushed.
- **FAKE_HOME + escape guard** on every sandbox; no live-shaped HOME-derived install path resolved; no
  `rm -rf` outside a temp dir (one pack's pre-existing fixture leak was cleaned with `rmdir`, empty-only).
- **Per-variable env scrubs only** — `env -u POSTGRES_*` never used (bash does not glob env names);
  `[ "$POSTGRES_DB" != "ensemble_prod" ]` asserted before each run; `PG_TEST_ALLOWED_DBS` set explicitly.
- **Ports**: 9797 / 8079 never bound or touched; all mock/ephemeral ports > 10000.
- **Boot sweeps**: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly wherever a boot sweep was
  in reach (never `env -u`'d).
- **Ambient hazard caught and neutralised**: `ENSEMBLE_UPGRADE_SCRIPTS_DIR` is set daemon-side to a live
  scripts-pin worktree that `stage.sh:854` would `git checkout --detach` — the lane scrubbed it and proved
  the foreign worktree's HEAD unchanged (`35ec50e3c` before and after). Recorded as POK
  `fence-01-upgrade-scripts-dir-scrub`.
- **Worktree metadata**: base legs used `git archive … | tar -x -C $(mktemp -d)` wherever possible (no
  worktree metadata written at all); two lanes used a detached temp worktree and removed it (`git worktree
  list | grep -c phh-p1` = 0).

---

## 8. RESIDUAL RISKS & ROUTED FOLLOW-UPS

1. 🔴 **C1 is unfixed in the landed tree** — promote/restart/rollback abort mid-hand-back on the
   start-timeout branch. One-line fix available (option A); the gate did not apply it (production frozen).
   **Re-run set after the fix**: bounded-waits (S5/S5b), the harness (d2/d3), and any journal-row reader.
2. 🟠 **`promote_handback_halt_gate_legs_test.sh` D6-A does not cover the start-timeout row** (a different,
   intact path) — a coverage gap, not a contradiction.
3. 🟠 **`test_supervision_twins.sh` cannot detect a journal writer that stops emitting its payload** — needs
   a new arg-count/payload-emission guard over `journal_history_append` call sites.
4. 🟠 **`test_supervision_journal.sh` certifies nothing** (fixture rot since `stage.sh` began requiring
   `docs/guidelines`) — the fixture needs a populated `docs/guidelines` in `FAKE_REPO` with real content
   (the per-file re-hash leg wants content, not an empty dir). Owner: that pack's lane.
5. 🟠 **The house digest canon is vacuous for bash packs** — `^(FAILED|ERROR)` matches only the
   `FAILED TESTS:` header, so two legs with different failure sets produce byte-identical digests. Use the
   **pack-native** failure-id form. (LESSONS L1; POK `gate-digest-01-bash-pack-native-form`.) A
   `bash -n`-equivalent for merge resolutions is missing too — LESSONS L2.
6. 🟡 **Dual-layer timeout inner layer unmet** on two registered packs (`handback_verify_budget_test.sh`,
   `test_ceremony_wallclock.sh`) — the caller's outer timeout is the only guard.
7. 🟡 **Pack hygiene**: `handback_verify_budget_test.sh:723` mints a `mktemp` dir inside a `bash -c`
   subshell without registering it ⇒ one empty `/tmp/hbv-l11.*` per run (pre-existing, byte-identical at base).
8. 🟡 **`gate_defer_reason` family is DORMANT** on both sides (defined, unwired — ledger §10 #4); the harness
   pins its presence, not its wiring.
9. 🟡 **The 12 out-of-scope gate call sites** in `promote.sh`/`restart.sh`/`rollback.sh` still treat any
   nonzero (incl. 2) as definitive — out of scope by the leader's ruling, and a known gap.
10. 🟡 **P11 vacuity**: 6 of the 14 nodes judge nothing when HEAD *is* the landing merge (span `HEAD..HEAD`).
    Shape 2 (§5.1) closes it for the post-merge tree; any future commit above the merge re-opens it.
11. 🟡 **PACKS.md annotation drift** to correct: `ceremony_wallclock`'s D2 rows still record the pre-fix
    `36/0` (the pack measures 50); the P11 class is 14 nodes, not the 6 last recorded; `adaptive_soak_rc2`
    was unregistered until this gate.

---

## 9. OPEN QUESTIONS FOR THE ORCHESTRATOR

1. **Land the C1 fix now?** The exact diff is validated (option A: move comment lines 7516-7521 above
   `lib.sh:7515`; option B is wrong). It is a production edit to the audited landing tree, so the gate did
   not make it. On the leader's word I will land it and re-run the affected legs (bounded-waits S5/S5b,
   harness d2/d3, journal/twins/wallclock readers) in one cycle.
2. **P11's pre-existing `frontend/` allowlist red** — base-attributed and outside the landing's footprint
   (the diff input is byte-identical at P1 and HEAD). The correct remedy is a separate lane re-basing
   `FRONTEND_UI_LANE_SANCTIONED_BASE` or allow-listing the drifted `frontend/` paths — **not** an edit
   inside this landing. Confirm it stays out of this gate.
3. **`supervision_journal` stays OFF the landing's gate set** (fixture rot predates the landing; the pack
   certifies nothing either way) — confirm, or authorise a fixture-repair leg.
4. **PACKS.md doc-drift corrections** (the annotation drift in §8.11) — the tester will apply them in a
   doc-only commit unless the leader prefers them folded into the fix commit.

---

## 10. WORKER INSTANCES

`phh-pre-inventory` · `phh-g1-evilmerge` · `phh-g2-harness` (×2 — harness + d3 follow-up) · `phh-c1-verify` ·
`phh-harness-confirm` · `phh-g3-p11` (×2 — shape 1 + shape 2) · `phh-g3-legs` · `phh-g3-stop` ·
`phh-g3-launcher` · `phh-g3-bounded` · `phh-g3-budget` · `phh-g3-green` · `phh-g3-soak` ·
`phh-x-supervision-journal` · `phh-x-supervision-twins` · `phh-x-ceremony-wallclock` · `phh-x-hbk-churn` —
all `worker`.

**Evidence-integrity note.** Two children (`phh-g3-p11` shape 2, `phh-g2-harness` d3) **completed their work but
their completion reports were never delivered** (a revive-path delivery gap) and then sat in non-terminal
states. Their results were recovered from the subtree message record (their own final reports + tool-call
trail), the stuck instances were terminated, and the harness was **re-run end-to-end by a fresh worker**
(`phh-harness-confirm`) to re-establish the d3 evidence first-hand. The P11 shape-2 numbers come from the
child's own completed report, recovered from the message record — it is not re-verified by a second run; the
shape-2 leg is cheap and the leader may re-run it for independent confirmation.
