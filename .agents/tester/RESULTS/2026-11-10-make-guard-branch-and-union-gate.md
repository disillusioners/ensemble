# 2026-11-10 — `fix/make-live-install-guard`: branch-only baseline + union gate

**Status: COMPLETE — all legs measured.** No `PENDING` sections remain.
Every result is labelled **PRE-UNION BASELINE** (`7b41bf686`) or **UNION GATE** (`a3a6f99c5`).

> **RELEASE CONTEXT (updated 2026-11-10).** Release number for this change set is **v0.29.11**
> (v0.29.9 = Decision Module, v0.29.10 = macOS deployment-topology).
> **Merge instruction: the BRANCH TIP is the release unit — never `9f91c0f90` (the union merge commit) alone.**
> `9f91c0f90` in isolation is a WEAKER suite than either parent (see CLAIM-1 / §4): the merge dropped
> upstream's stronger symlink test for the branch's weaker one; only the follow-up `a3a6f99c5` restored it.
> **Third leg (re-gate at `c50204be8`, BLOCKER-1 fix + package) is recorded in §6 and is SUPERSEDED as the
> release target** — upstream released v0.29.10 (`e06464ee2`, incl. `c788f2591` "guard final refinements —
> F6 absolute-path arm, D1-D3", touching the same `Makefile` + `tests/unit/test_makefile_install_guard.py`),
> and the branch is doing a second linear union against it. The final release gate must run at that post-union-#2 tip.

---

## 0. Verdict at a glance

| Change set | Subject | Verdict |
|---|---|---|
| **Branch-only baseline** | `7b41bf686` (pre-union) | Suites exact; 10/10 regression packs show **zero net-new** failures; **1 net-new red** in a standing pack (`test_release_journal.sh`); **2 fail-open classes** proven empirically |
| **Union gate** | `a3a6f99c5` (= merge `9f91c0f90` of `7b41bf686` + `40e6a842`) | Suites exact (151/150/1, 10/10); classifier matrix **31/31 MATCH**; upstream arms **6/6 present**; no widening; no parent arm dropped; **3 test-quality findings**; **1 release blocker** |

**Release recommendation: 🔴 DO NOT SHIP v0.29.10 with change set A until BLOCKER-1 is resolved.**
(Not a product-logic defect in the guard — a cross-module env-contract break introduced by the demo gate.)

---

## 1. Provenance (measured, not assumed)

| Ref | Role | Guard | `$(INSTALL_DIR)` rm sinks | Raw-value (F-01) arm |
|---|---|---|---|---|
| `424494082` | base (pre-branch) | 327 L | unquoted | absent |
| `7b41bf686` | **branch baseline** | 430 L, sha `406ec7d9…` | **unquoted** (`:474`) | **absent** |
| `9f91c0f90` | union merge | — | — | — |
| `a3a6f99c5` | **union tip = gate target** | 791 L, sha `173bacb9…` | **quoted** (`:319/:324/:374/:523`) | **present** (`:301-350`) |
| `40e6a842` | shipped tip | 719 L | quoted | present |
| `44062a85` | main-worktree `latest` | — | — | **ancestor, 46 commits behind target** |

Ancestry proof: `merge-base a3a6f99c5 44062a85 → 44062a85`; `a3a6f99c5..HEAD` empty; `44062a85..a3a6f99c5` = 46 commits.

**Tree pinning was mandatory.** The authoring worktree was concurrently used for the union merge (conflict writes 11:23:43 `Makefile`, 11:40:26 `install-dir-guard.sh`; merge committed 11:50:36; test fix-up 12:03:00). All measurement ran in detached trees (`/tmp/ens-union-a3a6f99c5`, `/tmp/mg-probe/pin-head-7b41bf686`), never in `…-wt-make-guard`.

---

## 2. Branch-only baseline `7b41bf686`

### 2.1 Unit suites — claim | actual

| Suite | Claimed | Measured | Verdict |
|---|---|---|---|
| `tests/unit/test_makefile_install_guard.py` | 92/91/1 | **92 collected / 91 passed / 1 skipped**, 2/2 reproducible | ✅ EXACT |
| `tests/unit/test_stop_ensemble_default_guard.py` | 10 passed | **10/10** | ✅ EXACT |

Skip id: `TestSudoArm::test_sudo_form_refuses_without_confirm_and_proceeds_with_it` (host lacks `sudo -n`).
Import integrity: `.pth` → pin tree; `daemon.__file__` inside pin. `sys.path` filter alone is **vacuous** for `/tmp` trees (no `agents-ensemble` substring) — always assert `daemon.__file__` + the `.pth`.

### 2.2 Regression sweep — 10 packs, A/B, base leg first

| Pack | Base | HEAD | Claim | Failing-id delta |
|---|---|---|---|---|
| `test_install_root_layer1.sh` | 36/0/0 | 36/0/0 (logs byte-identical) | — | none |
| `test_stop_ownership.sh` | 43/0/0 | 43/0/0 | — | none |
| `stop_ownership_unit_test.sh` | 43/0/0 | 43/0/0 | — | none |
| `test_supervision_stop_handback.sh` | 18/0/4 | 18/0/4 | — | none |
| `test_deploy.sh` | 50/3 | 50/3 | 50/3 | none (sets identical) |
| `test_rollback_target.sh` | 30/2 | 30/2 | 30/2 | none (sets identical) |
| `test_bounded_subprocess_waits.sh` | 36/2/2 | 36/2/2 | 36/2/2 | none (3/3 deterministic) |
| `test_promote_plugin_staleness.sh` | 61/0 | 61/0 | — | none |
| upgrade tools (`upgrade_tool_interlock`) | 294/1s/0 | 294/1s/0 | **177/43** | ❌ **claim not reproduced** |
| `test_release_journal.sh` | 21–28 reds | 22–29 reds | — | ⚠️ **+1 net-new id** |

**`test_deploy.sh` counterfactual** (proves the 5-line suite diff is load-bearing, not masking):
BASE suite + HEAD `deploy.sh` → **30/23**; HEAD suite + BASE `deploy.sh` → 50/3.

**"177/43" is unverifiable on Darwin.** Both candidate packs measured: `upgrade_tool_interlock` 294/1s/0; `release_journal_unit` inner suite 122P/228F (base) / 262P/85F (head). The literal `225/43` matches `QUARANTINE.md` **row 118 — a Linux-host run at old sha `ba295fec`**. Report as *unverifiable here*, not false.

### 2.3 Branch-baseline defects proven empirically

**HOLE-B1 — whitespace/metachar `INSTALL_DIR` reaches the unquoted sink.**
`INSTALL_DIR="<live> "` + `ENSEMBLE_INSTALL_LIVE=1` → guard **allows** (no F-01 arm at `7b41bf686`), and `Makefile:474` `rm -rf $(INSTALL_DIR)` word-splits to the live path. Upstream closed this at `0f60f1ab9`; the union inherits the fix.

**HOLE-B2 — arbitrary command execution:** `INSTALL_DIR="…; touch /tmp/mg-probe/PWNED ;"` → **the injected `touch` RAN** (`PWNED created`). Confined by the probe shim; on the branch the same input reaches the unquoted sink.

**HOLE-B3 — HOME unset ⇒ guard fails OPEN:** `env -u HOME` → guard emits `line 295: HOME: unbound variable` and **exits 0 (allow)**. A guard that crashes must refuse.

**HOLE-B4 — Darwin case variant allowed** (`…/Agents-Ensemble` → rc 0, sentinel deleted).
**HOLE-B5 — nonexistent intermediate allowed**; **ancestor-of-HOME allowed** (shim blocked `/tmp` and `/tmp/mg-probe` deletes — the shim did 100% of the protective work there).

**NOT a hole:** glob (`ENSEMBLE_LIVE_INSTALL_DIRS`) already matched **literally** at the branch.

### 2.4 Branch baseline — six upstream arms

`present=1 absent=5` at `7b41bf686` (u4 glob = already present). Six upstream test names all **ABSENT** at the branch, all **FOUND** at `0f60f1ab9` (:992/:1188/:1285/:1121/:952/:1301).

### 2.5 Branch-baseline safety probes — `RESULT: COMPLETE — HOLES-PRESENT (holes=2)` (predicted)

`safety_probes.sh` (25 probes) at `7b41bf686` (guard 430 L, sha `406ec7d9…`; unquoted sinks `:275/:325/:474`).

```
SUMMARY pass=26 fail=2 skipped=0 holes=2 errors=0 repeats=1
RESULT: COMPLETE — HOLES-PRESENT (holes=2)
```

**The two holes are m1/m2 — the expected pre-union finding, not a harness fault:**
`m1/m2` → `actual_guard_rc=0 actual_make_rc=0 make_refused=no sentinel=GONE verdict=HOLE(allowed-by-guard)`.
The raw-value arm is absent at the pin (`GUARD_RAW_VALUE_ARM_HITS=0`) and the sink is unquoted, so a whitespace-malformed `INSTALL_DIR` is allowed by the guard and word-splits into the sink. **Cross-check at the union tip: the same value is refused end-to-end** (`make rc=2`, `REFUSING — INSTALL_DIR spelling '…' cannot be verified safely … contains whitespace …`, sink never fired) ⇒ the hole is genuinely closed by the union.

**Claim-by-claim (branch baseline):**

| Claim | Probe | Verdict |
|---|---|---|
| a. `make uninstall/install/stop` at live default ⇒ refuses | a1, a3-install, a3-stop | ✅ `make_rc=2` + `Error 78` on stderr (guard rc 78; **make wraps it as 2**) |
| a. assert fails closed: guard wired first | a2-install | ✅ `guard_line_index=1`, `guard_line_is_first=yes` |
| a. confirmed allow | a2-stop | ✅ rc 0, `nothing to stop` |
| a. confirmed allow (uninstall) | a2-uninstall | ❌ **harness bug** (see below) — real containment held (`sentinel_gone=yes`) |
| b. subpath inside live root | b1, b2 | ✅ refused (`Error 78`) |
| b. symlink into live root | b3 | ✅ guard rc 78 |
| c. name-prefix siblings allowed | c1 (`-backup`), c2 (`X`), c3 (sandbox) | ✅ rc 0 each |
| d. bare `stop-ensemble.sh` ⇒ 78 with explicit-dir form | d1, d4 (`""`), d5 (`--force`) | ✅ guard rc 78 each |
| d. explicit dir unchanged | d3 | ✅ rc 0, stderr does not name the live dir |
| d. confirm var opens the bare form | d2 | ❌ **harness bug** (see below) |
| e. demo lanes gated | e1 deploy-demo, e2 stage-demo, e3 promote, e4 rollback | ✅ rc 78 + message match (e5 = repeat, not counted) |
| e. demo confirm opens | e1–e4-allow | ✅ guard rc 0 |
| hygiene: no secret echo | — | ✅ presence-only lines; no value printed |
| b4 real-path classify (read-only) | b4 | ✅ rc 78; write-free proof `0 / 0 / 28` (control non-zero ⇒ non-vacuous) |

**The 2 FAIL rows are harness bugs, not guard defects** (diagnosed, then independently reproduced):
1. `a2-uninstall` — the PASS predicate required `ls -A $HOME_DIR` unchanged **and** the sentinel inside `$HOME_DIR/agents-ensemble` gone. Since `INSTALL_DIR` *is* `$HOME_DIR/agents-ensemble`, the pinned `rm -rf $(INSTALL_DIR)` (`:474`) **necessarily** removes an entry from `ls -A $HOME_DIR` ⇒ the PASS branch is **unsatisfiable by construction**. Real containment held: only the fake home affected, shim log empty.
2. `d2` — bare invocation + confirm var + a **non-existent** default dir ⇒ pinned `stop-ensemble.sh:238` builds its die message with `'$1'` under `set -u`, so it aborts `line 238: $1: unbound variable` (rc 1) instead of a clean refusal. Reproduced independently (same bare call **with** the dir present ⇒ rc 0, `scoping to INSTALL_DIR=…`, nothing to stop). **Same defect exists at the union tip** ⇒ 🟢 minor: a pre-existing message-construction bug on a path the new gate exposes.

Safety receipts: `/tmp` + `/tmp/mg-probe` intact; `~/agents-ensemble` mtime/inode/size **unchanged** (8 entries); daemon PIDs `51569/51591` unchanged; pin sha unchanged + clean; **0 of 10** `make` invocations carried an `INSTALL_DIR` outside `/tmp/mg-probe`; shim log 0 bytes (no rm refusal, no kill/pkill/launchctl/sudo attempt).

**Caveat — the script's own daemon counter is vacuous on macOS:** `pgrep -f "$HOME/agents-ensemble/(launcher\.sh|ensemble-prod)"` printed `0` before and after while the daemon was demonstrably alive (macOS `pgrep -f` does not match a rewritten argv). The receipt was taken instead by PID + start time (`ps -o pid,lstart`). Do not trust that counter in future runs.

### 2.5b Branch A/B decision matrix — `RESULT: PASS` (no widening, no hole)

`ab_matrix.sh` (REV2) at BASE `424494082` (327-line guard, sha `c2ea5510…`) vs HEAD `7b41bf686` (430-line guard, sha `406ec7d9…`). Byte provenance OK (both legs match their tree blobs; `base_blob=9900288…` / `head_blob=4613d3b…`).

```
SUMMARY shapes=11 none=8 newly_refused=3 newly_allowed=0 changed=0 holes=0 outside_intended=0 errors=0
RESULT: PASS
```

| Shape | base | head | delta |
|---|---|---|---|
| `live-root` / `demo-root` / `symlink` | refused (live/demo) | refused | none |
| `inside-d1`, `inside-d3`, `demo-child` | **allowed** | **refused (live/demo)** | newly-refused ✅ intended |
| `backup`, `ensembleX`, `sandbox`, `ext-opt`, `ext-entry` | allowed | allowed | none |

Non-vacuity independently re-run: 5 shapes re-measured outside the pack reproduce the rcs exactly; refusal messages carry the worker's own target name and fixture path (so the legs really executed, not short-circuited); all six allowed rows have **0-byte stderr**.

**Sibling decision is deliberate and pinned, not accidental:** `install-dir-guard.sh:127-134` documents that a name-prefix sibling is a separate tree, and `tests/unit/test_makefile_install_guard.py:1161 test_name_prefix_siblings_are_not_live` (both spellings) + `:424 test_extension_var_no_prefix_false_positive` pin it, with the containment half at `:1118/:1132`.

**Coverage gaps carried forward:** (a) `--require-live` is **not** exercised by this matrix, so the `plist-install`/`watchdog-install` host-launchd arm is untouched, not verified evidence; (b) the `make_wired` column measures *install-dir-guard* wiring only — "not-wired" ≠ "ungated" (deploy/upgrade lanes have their own guard families: `deploy.sh:164-172` `ENSEMBLE_DEPLOY_LIVE`, `upgrade/*` `ENSEMBLE_UPGRADE_LIVE`).

---

## 3. Union gate `a3a6f99c5`

### 3.1 Suites

| Suite | Claim | Measured |
|---|---|---|
| `test_makefile_install_guard.py` | 151/150/1 | ✅ **151 collected / 150 passed / 1 skipped** (3× reproducible) |
| `test_stop_ensemble_default_guard.py` | 10 passed | ✅ **10/10** |

### 3.2 Classifier matrix — 31 rows, **31/31 MATCH**

Strengthened semantics confirmed in the guard's own words:
- **c04/c05** inside-live **+ `ENSEMBLE_INSTALL_LIVE=1`** → still `STRUCTURAL-INSIDE`; message: *"no confirm var opens it … not one that swallows it"*.
- **c12** demo-subpath **+ `DEMO=1`** → still `STRUCTURAL-INSIDE`.
- **c20/c21** malformed **+ confirm** → `STRUCTURAL-UNVERIFIABLE` (**F-01 arm present** in the union).
- **c25** HOME unset → `STRUCTURAL-ENV-HOME`; **c26** ancestor → `STRUCTURAL-CONTAINS`; **c27** Darwin case → `LIVE`.
- **Green controls** c02/c06/c07/c10/c16/c17/c24-iso → rc 0 (guard is not blanket-refusing).
- **c24** was made non-vacuous by isolation: `c24-iso` (glob entry, unrelated HOME) → allow; `c24-ctl` (literal spelling) → refuse LIVE.
- **c99-real** real live path → `LIVE`, read-only; live dir mtime + entry count unchanged.

### 3.3 Upstream arms at the union — `present=6 absent=0`

All six carry primary `REFUSING` stderr text (no absence-inference). `u5` metachar → `PWNED absent` (the injected command does **not** run).

**Anti-finding:** the script's `sink_hits_total 4→1` is a **grep artifact** (union quotes its recipes). All four sinks still exist (`:319/:324/:374/:523`). Do **not** read it as a hardening win.

### 3.4 Static audit — delta shape and additivity

- Delta `40e6a842..a3a6f99c5` = **12 files, +1127/−16** ✅ exact. Framing: this range *also* carries the branch's own delta; union-only contribution is `40e6a842..9f91c0f90` (12 files +1120/−27) + `9f91c0f90..a3a6f99c5` (1 file +19/−1).
- **C5 holds:** no `launcher.sh`, no `daemon/` in the union delta; all launcher/daemon divergence in `424494082..a3a6f99c5` is **upstream-side**.
- **C2 holds:** exactly 3 adaptations, all **strengthenings**; the branch's `test_live_subpath_proceeds_with_confirm` **success arm was inverted** into a refusal arm.
- **Node census reconciles exactly:** 45 shared + 21 shipped + 37 branch = **103 functions** + 48 parametrisation = **151 collected**.
- **F holds:** all 6 upstream arms present, all 4 branch arms retained, **no parent arm missing**.

---

## 4. Findings register

### 🔴 BLOCKER-1 — daemon-armed demo promote is broken (union + branch)

`scripts/upgrade/lib.sh:1387` `require_demo_guard` demands `ENSEMBLE_INSTALL_DEMO=1`; `daemon/tools/upgrade_journal.py` `EXECUTOR_ENV_ALLOWLIST` (`:2524-2540`) does not list it and `EXECUTOR_ENV_PREFIXES=("PG",)` does not cover it; `executor_env` is the **sole** env source (`:3314`); `_verified_arm_extras` is gated on `op.env == "live"` (`:1800`). `promote.sh:128` defaults the target to `demo`; `:130` refuses. **`ENSEMBLE_INSTALL_DEMO` has zero occurrences anywhere under `daemon/`.** No `ENSEMBLE_PROMOTE_DAEMON`/`PROMOTE_DAEMON` bypass exists.

**Empirically confirmed** (both trees, `env -i` with the exact executor allowlist): HEAD exits **78 at the demo gate**; BASE exits 78 at the *version* / *not-staged* gate. **rc alone does not discriminate — the gate does.**

**Remediation:** forward `ENSEMBLE_INSTALL_DEMO` in the daemon's `extra_env` for demo targets (mirroring the live path's `ENSEMBLE_UPGRADE_LIVE`), or exempt the daemon-armed lane inside `require_demo_guard`.

### 🟠 REGRESSION-1 — standing pack red for a NEW reason
`tests/test_release_journal.sh:344-348` asserts `promote.sh demo` reaches the not-staged refusal; the demo gate now fires first. **HEAD-only id:** `f2 gate: demo reaches not-staged, not f2`. Test file byte-identical across legs ⇒ pure product signal. The branch updated `test_deploy.sh` + `test_promote_plugin_staleness.sh` for the same guard but **missed this one**.

### 🟠 TEST-1 — assertion-free test at the union tip
`tests/unit/test_makefile_install_guard.py:756` — `TestMakefileWiring::test_stop_script_never_executed_by_this_suite` now has **0 asserts**; `text` is an unused local. `assert text.startswith("#!/bin/bash")` was deleted though it **would still have passed** (shebang unchanged), and **nothing else in the repo pins that shebang**.

### 🟠 TEST-2 — partial weakening
`:1494-1506` `test_no_repo_prod_port_leaks_into_the_kill` lost `kill_lines[0].rstrip().endswith(str(sbx))` (the "carries **no port token at all**" property) for a substring check. `assert "9797" not in r.stdout` covers the prod-port case only; the empty-token property the docstring still claims is asserted nowhere.

### 🟡 CLAIM-1 — author's C3 framing inaccurate at the tip — **ATTRIBUTION CORRECTED 2026-11-10**
"Exactly 1 test was REMOVED … true duplicate" describes merge `9f91c0f90`, **not** `a3a6f99c5`. At `9f91c0f90` only **upstream's 1-assert** symlink test existed — the **branch's stronger 7-assert** version was **lost in the merge resolution**, and the fix-up `a3a6f99c5` restored it byte-identically. ⇒ the merge commit is **weaker than the BRANCH parent** (equal to the shipped parent), not "weaker than either parent". The **tip** carries **both** versions, one per class, neither weakened ⇒ **merge the branch tip, never `9f91c0f90` alone**.
⚠️ **CORRECTION:** the *stronger* (7-assert) symlink test is the **BRANCH's**; upstream's is the 1-assert one (`TestGuardCanonicalization`, body byte-identical to base). The wording in this section (and in the leg-2/leg-3 briefs) attributed the stronger test to upstream — **inverted**; superseded by this paragraph.

### 🟢 Process-1 — gate target is on no remote ref
`a3a6f99c5` lives only on `refs/heads/fix/make-live-install-guard` + the detached pin; `origin/latest` is 5 commits behind. If `latest` advances past the union base, the merge base moves and the 31/31 matrix must be re-measured. **A green gate on an unpushed sha is perishable.**

---

## 5. Test-architecture defects found (pre-existing, not branch-caused)

1. **`test_release_journal.sh` cannot complete under the 5-min cap** — 23 sections, ~128 s explicit sleeps, and the O(N) journal fast path is structurally unavailable in-fixture (`lib.sh:1495-1513` wants `journal_read.py`; the fixture copies only `journal_splice.py`) ⇒ every read pays the 120 s legacy budget. Measured 202 s (base) / 810 s (head).
2. **Fixed fixture ports** (18399/18411/18453/18467) collide across concurrent lanes — two lanes running the same suite race them.
3. **`test_deploy.sh` fixture omits `scripts/upgrade/lib.sh`** ⇒ the deploy/stop phase fails for *fixture* reasons on both legs; `deploy.sh`'s stop path is validated by nothing.
4. **`test_release_journal.sh` is red at BASE in this environment** (21–28 ids), ~18 cascading from `PIPELINE_SCRIPTS_FLOOR_COMMIT` being unset because the fixture never creates `daemon/tools/`.
5. **Interpreter asymmetry** between A/B legs when only one worktree has a `.venv` (3.14.6 vs 3.13.3). Provision **both** trees identically.

## 6. Harness lessons (for reuse)

- **Pin the subject; never measure in a tree another lane is mutating.** The authoring worktree was mid-union-merge during the first legs (`UU` on 3 files).
- **A vanished tree is `ENVIRONMENTAL`, never FAIL.** A peer swept the baseline pin mid-run; the spurious `No rule to make target 'uninstall'` was correctly classified as environmental.
- **`make` does not exit 78.** The guard exits 78; make wraps it as `Error 78` on stderr and exits **2**. Assert **both**.
- **A PATH shim cannot intercept bash's `kill` builtin** ⇒ `DRY_RUN=1` is the only structural no-signal guarantee for stop-path probes.
- **`sys.path` substring filters are vacuous for `/tmp` trees** ⇒ assert `daemon.__file__` + the editable `.pth` target.
- **`git merge-base --is-ancestor A B` is directional** — check **both** ways before concluding "the tip moved".
- **Report raw counters + failing-id SETS.** Two legs can both read `30 passed / 2 failed` while failing different tests.

## 7. Not verified (stated plainly)

- Make-level integration for the union's `--require-live` mode (`plist-install`/`watchdog-install`).
- Linux-only branches: `getent` SUDO_USER resolution, case-**sensitive** comparison path.
- `SUDO_USER` home-derivation; multi-entry / whitespace-padded `ENSEMBLE_LIVE_INSTALL_DIRS`; relative `INSTALL_DIR`; symlink depth > 1.
- The `test_release_journal.sh` 122P/228F vs 262P/85F swing (146 ids flip) — **unadjudicated**: the suite has fixed ports and shared fixture state and a foreign lane was observed running it concurrently. Not evidence that the guard improves anything.
- POK is at **65 active / 64 cap** (lockout) — knowledge capture is blocked pending curator action.

## 8. Legacy note

- Branch-baseline probe scripts cleared review with non-blocking advisories: the `rm` shim is physical-only with a broad `/tmp/mg-probe/*` allowlist (safe for today's static inputs, worth hardening before reuse); b4's write-free proof is "no **known** write primitive" (verb list omits `chmod/chown/install`).

---

# 6. THIRD LEG — RE-GATE AT `c50204be8` (BLOCKER-1 fix + package)

> **Status: measured, and SUPERSEDED as the release target.**
> This leg validates the BLOCKER-1 fix and the package commit. It is **not** the release gate:
> upstream then released v0.29.10 (`e06464ee2`, incl. `c788f2591` "guard final refinements — F6
> absolute-path arm, D1-D3", touching the same `Makefile` + `tests/unit/test_makefile_install_guard.py`),
> and the branch is performing a second linear union against it. **The release gate for change set A
> must run at the post-union-#2 tip.** The merge unit is the **branch tip — never `9f91c0f90` alone**
> (see CLAIM-1 in §4).

Tip: `c50204be8572fe0a325896f4a089c45888ba5108` · chain `424494082 → e1879f695 → 3d89ea66b →
7b41bf686 → 9f91c0f90 → a3a6f99c5 → 8339e12f8 (BLOCKER-1) → c50204be8 (package)`.
All eight commits verified as ancestors of the tip; worktree clean at inspection.

## 6.1 Suites

| Suite | Claimed | Measured | Verdict |
|---|---|---|---|
| guard + stop + daemon-preflight **together** | 204 collected / 203 passed / 1 skipped | static derivation **151 + 12 + 41 = 204** collected (103+48, 12+0, 38+3) | ✅ collected exact; the 203/1 split is a runtime partition — **not independently confirmed** (no execution leg run for the trio at this tip) |
| journal (`tests/unit/tools/test_upgrade_journal.py`) | 117 passed / 1 skipped | static: 104 tests + 8 + 6 extras = **118 collected** | ✅ consistent with 117/1 |
| **survivorship 117/1** | a second suite | **NO SUCH SUITE EXISTS** in this tree — the only "survivor" artifacts are a **0-node standalone script** (`test_promote_cgroup_survivorship_python.py`) and a `PASS=/FAIL=` shell suite | ⚠️ brief figure does not resolve; the 117/1 signature maps **uniquely** to the journal suite |
| **adopt-unit 43/37** | 43 passed / 37 failed | `tests/test_adopt_unit.sh` located; **43+37 = 80 = the pack's canonical assert total**; **no registered 43/37 receipt** exists; the pack's production target `scripts/upgrade/adopt-unit.sh` is **NOT in the delta** | ⚠️ unverifiable as stated; any red there is **out-of-delta-scope / environment-induced** (standing GNU `date` host-arch debt), **not branch-caused** |

## 6.2 Classification matrix — 33/33 MATCH

Guard blob **byte-identical** since `a3a6f99c5` (sha `173bacb9…`, 791 L); `Makefile` diff **empty** over
`a3a6f99c5..HEAD` ⇒ no classification arm was touched by the fix package. Sorted row-set difference
between the two tips: **EMPTY** — zero rows changed class. All four `$(INSTALL_DIR)` sinks remain
**quoted** (`:319/:324/:374/:523`; `:514` is a comment).

All 31 legacy rows byte-identical to the union run (same `rc=`, `bucket=`, and quoted classifier first
line), including the strengthened semantics: `c04/c05` inside-live **+ `ENSEMBLE_INSTALL_LIVE=1`** →
`STRUCTURAL-INSIDE`; `c12` demo-subpath **+ `DEMO=1`** → `STRUCTURAL-INSIDE`; `c20/c21` malformed
**+ confirm** → `STRUCTURAL-UNVERIFIABLE`; `c25` HOME unset → `STRUCTURAL-ENV-HOME`; `c26` ancestor →
`STRUCTURAL-CONTAINS`; `c27` Darwin case → `LIVE`; green controls `c02/c06/c07/c10/c16/c17/c24-iso` → rc 0.

**New rows for GUARD-1:** `c30` (bare + confirm + dir-absent) → **rc 78**, named refusal;
`c30-ctl` (dir present) → **rc 0**, `no processes owned … nothing to stop` ⇒ the refusal is the
**dir-absent** path, not a blanket gate. Signal tripwire armed (`SIGTERM|SIGKILL|systemctl stop|
launchctl|pkill|killall`) — never fired.

## 6.3 BLOCKER-1 — CLOSED (empirical, end-to-end)

> **Method correction carried forward:** the fix **forwards** the confirm var; it does **not** disarm
> the guard. A synthetic allowlist-only env must therefore **still** trip the demo gate — and it does.
> The closure test is the **semantic** daemon arm (allowlist env ∪ what the tree's manager seam mints).

| Row | Tree | rc | Gate reached |
|---|---|---|---|
| A1a literal allowlist-only | tip | 78 | **demo-gate** (correctly still armed) |
| A1b + tree-minted extras | tip | 78 | **version-gate** (past the demo gate) |
| A2 faithful-argv `--version` | tip | 78 | **not-staged** |
| **A3 daemon-real** | tip | 78 | **not-staged** |
| E1a / E1b / E3 (same construction) | **baseline `a3a6f99c5`** | 78 | **demo-gate** |

A3 env carries **8** keys incl. `ENSEMBLE_INSTALL_DEMO`; E3 carries **7** and does not. Same harness,
same construction rule, different tree, different outcome ⇒ the differential *is* the closure proof.

**Fail-closed matrix — 8/8, so closed without widening:** `demo`+demo-candidate ⇒ `{ENSEMBLE_INSTALL_DEMO}`;
`live` / `sandbox` / `dev` / `''` / `None` / `demo`+**non-candidate** dir / `demo`+empty dir ⇒ `{}`.

**Allowlist discipline + ambient-leak pin (empirical):** the var occurs only in a docstring (`:1809`)
and inside `demo_arm_extras` (`:1867`) — **0×** in `EXECUTOR_ENV_ALLOWLIST` (`:2593-2609`) and unmatched
by `EXECUTOR_ENV_PREFIXES=("PG",)`. Ambient `ENSEMBLE_INSTALL_DEMO=1` → `executor_env(None)` → **absent**;
explicit extra → **present**. The var can only arrive via the explicit-extra merge.

```
BLOCKER-1 CLOSED: YES · LIVE LANE INTACT: YES · FAIL-CLOSED HOLDS: YES · ALLOWLIST CLEAN: YES
```

**Live lane:** `C1` → live-gate 78; `C2` (live arm) → version-gate, **no demo var**.

## 6.4 Regression sweep at the tip — every leg delta-free

| Pack | Base | Tip | Failing-id delta |
|---|---|---|---|
| `test_install_root_layer1.sh` | 36/0 | 36/0 | **empty** (stdout logs **byte-identical**, sha `76c346e5…`) |
| `test_stop_ownership.sh` | 43/0 | 43/0 | **empty** |
| `stop_ownership_unit_test.sh` | 43/0 | 43/0 | **empty** (symmetric diff 0 bytes) |
| `test_supervision_stop_handback.sh` | 18/0/4 | 18/0/4 | **empty** (skip lists byte-identical, 4/4 `FENCE: no /run/systemd/system`) |
| `test_bounded_subprocess_waits.sh` | 36/2/2 | 36/2/2 | **empty** (same 2 host-conditioned ids; timing assert `S2 hang-stop: bounded` **passed both legs**) |
| `test_deploy.sh` | 50/3 | 50/3 | **empty** (same 3 ids) |
| `test_rollback_target.sh` | 30/2 | 30/2 | **empty** (same 2 ids) |
| `test_promote_plugin_staleness.sh` | 61/0 | 61/0 | **empty** (stdout byte-identical, `fe6bbc31…`) |
| `test_release_journal.sh` (d) | rc 124 | rc 124 | **tip-only ∅ ⇒ NET-NEW: NO** |

**Not re-run (label retained):** the `177/43` leg — **unverifiable on Darwin**; both candidate packs
measured; the literal `225/43` matches `QUARANTINE.md` row 118, a **Linux-host** run at old sha `ba295fec`.

**Scope honesty on two of these legs:**
- `test_install_root_layer1.sh` is a **no-regression gate for install-root resolution, NOT a guard gate** —
  its `eval`'d functions (`_deploy_install_root` `42f62752…`, `_default_install_dir` `1a7c92cb…`) are
  **byte-identical** base↔tip even though `deploy.sh`/`stop-ensemble.sh`/`lib.sh` all changed. Not
  independent evidence that the guards work.
- `test_stop_ownership.sh` and `stop_ownership_unit_test.sh` **cannot** be cited as GUARD-1 coverage: all
  28 `stop-ensemble.sh` calls pass an explicit `mktemp` dir ⇒ **0 bare-form calls** ⇒ the refusal path is
  structurally unreachable from them. Real GUARD-1 coverage = the new
  `test_stop_ensemble_default_guard.py::TestUnresolvableInstallDir` (2 tests) + matrix rows `c30/c30-ctl`.

**`test_release_journal.sh` — my dispatch premise was WRONG on the BASE half, and the correction matters.**
The id `f2 gate: demo reaches not-staged, not f2` is **GREEN at base** (`424494082` has no
`require_demo_guard` at all — 0 grep hits) **and green at the tip**; it was **introduced by the branch**
at `3d89ea66b` and **fixed by the (d)-leg**. The red I recorded in §2 belongs to the **intermediate
commit `7b41bf686`**, which is *not* an ancestor of base. ⇒ the (d)-leg **restored parity with base**.
Also: the 3 "base-only" ids were a **cap-reach artifact** (disproved by an equal-cap 330 s re-run and an
isolated cell replay); the `8c` reds are a **host-TZ fixture bug** — `strptime` naive ⇒ `.timestamp()`
reads UTC digits as local ⇒ **7 h skew** on `Asia/Ho_Chi_Minh` ⇒ stale-heartbeat reclaim; **green under
`TZ=UTC`**, identical on both trees, and the section-8 block is byte-identical across legs.

**`test_rollback_target.sh` — two corrections to the stated mechanism:** only **C7** is a stale window
(`sed -n '555,600p'` vs the literal at :793/:794); **C6 is a fixture gap** (the fixture never mints
`releases/v-prev/manifest.json` — proven constructively: minting it makes the halt fire). And **C7's paired
negative assert passes VACUOUSLY** (that token occurs 0× in the whole file) — a stale window disarms the
positive arm loudly and the absence-pin **silently**.

**`launcher.sh` disjointness:** vs the **shipped** tip `40e6a842` ⇒ **absent** ⇒ change sets A and B are
disjoint on the launcher surface (release-composition statement). Against the **pre-branch base**
`424494082` the launcher DOES differ (+616/−92) — those commits (`d9274053d`, `510e72f64`, `c535ab89f`)
are ancestors of upstream `40e6a842` and **not** of the guard branch, i.e. **upstream lane content
inherited via the union merge**. The guard-unique commits touch **zero** `launcher.sh`.

## 6.5 Item-by-item verification of the fix package

| Item | Verdict | Evidence |
|---|---|---|
| (4) REGRESSION-1 (d)-leg fix | ✅ | `+ENSEMBLE_INSTALL_DEMO=1 TARGET=demo`; stale comment corrected ("the F2 flag is NEVER required there (the F2 gate is live-only) — but the DEMO lane's OWN confirm gate … IS required"); the two asserts are **unchanged** (still rc 78 + not-staged, now reached *past* the demo confirm) |
| (5) TEST-1 restored | ✅ | `assert text.startswith("#!/bin/bash")` back at `:758`; assert count **0 → 1** |
| (5) TEST-2 restored | ✅ | `shlex.split` **token-shape** pin: `parts[:3] == ["STOP_BY_PORT=1","bash","scripts/stop-ensemble.sh"]`, `len(parts) == 4`, `parts[3] == str(sbx)` ⇒ **no port token in any position** (the surviving `"9797" not in stdout` only catches the literal 9797 and is now secondary) |
| (6) GUARD-1 | ✅ | named refusal **rc 78**; prior path was `set -u` unbound-`$1` **rc 1** (not `_die`'s exit 2). Also fixes the **second** bare-path site at `:164` ⇒ both bare paths are now clean |
| (7) delta shape | ✅ | vs shipped `40e6a842`: **16 files, +1465/−23** exact; `daemon/manager.py` **+19/−0**, `daemon/tools/upgrade_journal.py` **+69/−0**; **`launcher.sh` ABSENT** |
| (8) CLAIM-1 | ✅ **(attribution CORRECTED 2026-11-10)** | At `9f91c0f90` only **upstream's 1-assert** symlink test existed — the **branch's stronger 7-assert** version was lost in the merge resolution and restored by the fix-up `a3a6f99c5`. ⇒ the merge commit is **weaker than the BRANCH parent** (equal to the shipped parent). The **tip** carries **both** (branch's 7-assert in `TestCaseAndPathResolutionArm` + upstream's 1-assert in `TestGuardCanonicalization`, bodies demonstrably different ⇒ genuinely distinct classes) ⇒ **merge the branch tip, never `9f91c0f90` alone**. ⚠️ The earlier phrasing in this artifact (and in the leg-2/leg-3 briefs) attributed the *stronger* test to upstream — **inverted**; superseded by this row. |
| (2) 9 new daemon tests | ✅ non-vacuous | every negative test asserts `drain.arm_and_drain(...) is True` **before** the negative assert; live/sandbox/unknown/empty vary only `env` against an identical demo dir (discriminating matrix); the allowlist/ambient-leak pins are distinct asserts |

## 6.6 What change set A adds on top of the shipped guard (factual, for release composition)

Product code only — test code excluded (tests are +1141/−14 across 6 files).

**Scripts / Makefile (+236/−9 across 8 files):**
- `Makefile` (+23/−6) — guard wired as the **first** action of `install`, `uninstall`, `install-deps`,
  `stop`, `stop-by-port`, `plist-install`, `watchdog-install`; `INSTALL_DIR` default left **inert**
  (no implicit live path); the `rm -rf` install sinks **quoted**; `--require-live` added for the
  launchd targets.
- `scripts/install-dir-guard.sh` (+74/−2 over shipped) — adds `--require-live`; the **demo** lane class
  and its `ENSEMBLE_INSTALL_DEMO=1` confirm; **root-or-inside** live/demo classification (so a path
  *inside* a protected root is refused **structurally** — no confirm var opens it); the bare-stop
  live/demo gate; plus upstream's own arms (raw-value/metachar, HOME-unset fail-closed, multi-entry
  live set, case/ghost resolution) which shipped separately.
- `scripts/stop-ensemble.sh` (+93/−1) — **guard-02**: a **bare** invocation resolving to the live/demo
  install refuses (78) and names the explicit-dir form; **GUARD-1**: an unresolvable install dir now
  yields a **named** refusal (78) instead of a `set -u` unbound-`$1` error (rc 1).
- `scripts/deploy.sh` (+20) — demo deploy requires `ENSEMBLE_INSTALL_DEMO=1` (live deploy already
  required `ENSEMBLE_DEPLOY_LIVE=1`).
- `scripts/upgrade/lib.sh` (+23) — `require_demo_guard()`: a demo upgrade requires
  `ENSEMBLE_INSTALL_DEMO=1` (78 otherwise).
- `scripts/upgrade/{promote,rollback,stage}.sh` (+1 each) — call `require_demo_guard`.

**Daemon (+88/−0 across 2 files) — the BLOCKER-1 env forwarding:**
- `daemon/tools/upgrade_journal.py` (+69) — `demo_arm_extras(target, install_dir)`: mints
  `{"ENSEMBLE_INSTALL_DEMO": "1"}` **only** when the target is `demo` **and** the install dir provably
  resolves to `$HOME/agents-ensemble-demo` (or the Layer-1 service-user shape); **explicit-extra only**
  (never the allowlist/prefix path); every other shape returns `{}` fail-closed.
- `daemon/manager.py` (+19) — merges that dict into `extra_env` at the promote seam
  (`daemon/manager.py:6386`), so a **daemon-armed demo promote** carries the confirm and proceeds, while
  the **live lane** is untouched (`ENSEMBLE_UPGRADE_LIVE` remains gated solely by `is_verified_arm`).

**Net effect, stated plainly:** change set A makes the install/deploy/upgrade/stop lanes **refuse by
default** on a live, demo, or protected-dir target and require an explicit per-path confirm to proceed;
it closes the fail-open paths upstream had already hardened (raw-value/metachar, HOME-unset, ancestor,
case, glob-literal, nonexistent-intermediate); and it **restores the daemon's own demo lane**, which the
demo gate would otherwise have killed. It does **not** change any guard *decision* for a well-formed
absolute path that is not live/demo/protected — the name-prefix sibling rule (`agents-ensemble-backup`,
`agents-ensembleX`) stays **allowed by design** in both base and tip.

**Two documented semantic notes (not defects, but part of the shipped behaviour):**
1. **Release-composition wording (SETTLED 2026-11-10 — use this text verbatim; do NOT inherit the
   docstring's phrase):**
   > *"explicit-extra forwarding scoped to demo-target ∧ demo-candidate dir; not gated on the live
   > verified-arm predicate (that predicate requires `op.env=="live"`), so an unverified daemon-armed
   > demo promote also receives it — live protection unaffected."*

   The **mechanism** mirrors the live lane — explicit-extra forwarding, never the allowlist/prefix path,
   scoped to demo-target ∧ demo-candidate dir, fail-closed 8/8 — but the **gate** is NOT live's, and
   structurally cannot be: `is_verified_arm` requires `op.env=="live"`, so it does not apply to a demo op.
   Blast radius is confined to a **provably-demo** dir; live protection is untouched.
   ⚠️ The in-code docstring's *"mirrors the live lane exactly"* is accurate for the **MECHANISM** and
   **inaccurate for the GATE** — the composition text above must not inherit that phrase.
2. An empty spec env defaults to `demo` inside `promote.sh`, while `demo_arm_extras("")` returns `{}` ⇒
   net **fail-closed** (rc 78), but a demo arm with an empty env stays dead rather than reaching the
   not-staged refusal.

## 6.7 Residual list at `c50204be8` (all non-blocking, all pre-existing or out-of-delta)

| Sev | Residual |
|---|---|
| 🟠 | `test_release_journal.sh` **cannot complete under the 5-min cap** (23 sections, ~128 s sleeps, two ~145 s boundaries; the O(N) journal fast path is unavailable **in-fixture** — `journal_read.py` is not copied by the fixture) ⇒ the pack is structurally un-runnable in-cap on this host. **Fixture fix:** copy `journal_read.py` (or the pack must be split) |
| 🟠 | `test_release_journal.sh` `8c` reds: **TZ-SENSITIVE** — the fixture stamps `last_heartbeat` via `strptime` **without** `tzinfo` ⇒ `.timestamp()` reads UTC digits as **local** ⇒ **7 h skew** on `Asia/Ho_Chi_Minh` ⇒ stale-heartbeat reclaim. **Green under `TZ=UTC`** on both trees (identical behaviour). **ATTRIBUTION OPEN (2026-11-10):** the launcher lane is determining **FIXTURE-vs-PRODUCER** — the repo's convention sanctions naive stamps only for PG `timestamp without time zone` columns, whereas `releases/state.json` is **JSON** and should carry offset-aware ISO. Until that lands this row is labelled **TZ-sensitive, producer-vs-fixture UNRESOLVED**; if a producer defect is confirmed it becomes a **PRODUCER** finding (and, if it ships, a new commit + a re-gate leg) — **not** a fixture red. Final-leg action: re-check the `8c` reds under `TZ=UTC` at the final tip (they should be green, being TZ-sensitive); if a producer defect is confirmed, keep them labelled **producer**. |
| 🟡 | `test_deploy.sh` fixture omits `scripts/upgrade/lib.sh` ⇒ the deploy/stop phase fails for fixture reasons on both legs ⇒ **that phase is validated by nothing**; 1 of its 3 reds is a separate **stale oracle** (the generator appends a 2-line `PLANE_MCP_WORKSPACE_SLUG=e2e` pin ⇒ `'='` count 11 vs oracle 9) |
| 🟡 | `test_promote_plugin_staleness.sh` writes **3 hardcoded global `/tmp` paths** outside its mktemp sandbox ⇒ **two concurrent runs corrupt each other's section-7 assertions** |
| 🟡 | `test_rollback_target.sh`: C6 = **fixture gap** (no `releases/v-prev/manifest.json`); C7 = **stale window**; and C7's paired negative assert passes **vacuously** (token absent from the whole file) |
| 🟡 | `test_supervision_stop_handback.sh`: 4 systemd arms (A/B/C/D′ = **135 assertion sites**) are **fenced out on this host** (macOS) ⇒ the A/B covers the **macOS-executable arms only** (A′ 13 sites + D 5 sites = **18 = the reported PASS count exactly** — an arithmetic proof of the executed span). **CORRECTION (2026-11-10):** the nested `P3-PIN-09-base` red-at-base arm is **not** merely "not exercised because `HB_BASE_LIBSH` wasn't supplied" — it sits **inside** D′'s `HOST_HAS_SYSTEMD` gate (pack lines 1261-1405), so that knob is **irrelevant on this host**: re-running with `HB_BASE_LIBSH` supplied yields the identical 4 skips and no `P3-PIN-09-base` line ⇒ SKIP is always exactly **4**, never 5, and this pack **cannot** produce red-at-base evidence for P3-PIN-09 on macOS. |
| 🟢 | `test_bounded_subprocess_waits.sh` 2 reds are **host-conditioned** (no systemd ⇒ pid-scoped degradation) and 2 skips are the matching host fences |
| 🟢 | `test_install_root_layer1.sh` is a **no-regression gate only** (its eval'd subjects are byte-identical base↔tip) — not guard coverage |
| 🟢 | `test_stop_ownership.sh` / `stop_ownership_unit_test.sh` cannot be cited as **GUARD-1** coverage (0 bare-form calls) |
| 🟢 | `test_stop_ownership.sh` has **no script-internal timer** ⇒ only the command-level `timeout` was available (dual-layer not satisfiable without modifying the pack) |
| ⚙️ | **POK is at its 64-active cap** (refuse-never-evict) — new operating facts are being refused project-wide; a curator archive is required (candidate: oldest/least-verified `deploy-01-make-install-live-dir-hazard`) |
| ⚙️ | `tests/unit/tools/test_upgrade_preflight_daemon_side.py` +177/−5 — the 9 daemon tests plus `_DrainHarness` plumbing |

## 6.8 Not verified at this leg

- The **203-passed / 1-skipped split** of the 204-node trio (collected total confirmed statically; the
  pass/skip partition needs an execution leg — **the trio was not executed at `c50204be8`**).
- The **survivorship 117/1** and **adopt-unit 43/37** figures — no such suites/receipts exist as stated.
- Make-level `--require-live` (`plist-install`/`watchdog-install`) — untouched evidence, not verified.
- Linux-only guard branches (`getent` SUDO_USER resolution; case-**sensitive** comparison).
- `SUDO_USER` home-derivation; multi-entry / whitespace-padded `ENSEMBLE_LIVE_INSTALL_DIRS`; relative
  `INSTALL_DIR`; symlink depth > 1.
- `test_release_journal.sh` counts (28 vs 25 at the 300 s cap) — **cap-reach artifacts**, not a defect
  delta; use the equal-cap 330 s run + isolated cell replay for attribution.
- The daemon-side **end-to-end** path through `manager.py`'s real promote branch (the A3 probe emulates the
  seam from the tree's own `executor_env` + `demo_arm_extras`; it does not drive the live manager loop).

## 6.9 Artifact placement

The RESULTS artifact was committed **path-limited on the branch** as `10c01d741`
(`docs(tester): make-guard branch + union + fix-package gate`) — exactly **one** file changed, HEAD moved
`c50204be8 → 10c01d741`, `status --porcelain` empty afterwards, nothing left staged, no push/merge/tag/
reset/checkout/stash/amend. The **untracked MAIN copy was removed**; MAIN HEAD unchanged
(`9a54a836c`). One foreign untracked entry (`?? added`, another lane's, dated hours before this run)
remains in MAIN — **reported, not deleted**: the merge target must end with zero untracked entries, but
that entry is its owning lane's to clear.

**This section (6) is appended to the same artifact.** Its commit is **deferred** while the branch
worktree is mid-union-#2 (git refuses a partial commit during a merge, and the index there belongs to the
author); it will be committed path-limited with the same protocol and the same `MERGE_HEAD` precondition
re-checked at the moment of the commit.
