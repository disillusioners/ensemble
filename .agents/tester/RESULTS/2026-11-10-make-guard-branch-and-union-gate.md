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

### 🟡 CLAIM-1 — author's C3 framing inaccurate at the tip
"Exactly 1 test was REMOVED … true duplicate" describes merge `9f91c0f90`, **not** `a3a6f99c5`. The merge dropped upstream's **stronger** symlink test (7 asserts) for the branch's **weaker** one (1 assert) — a real coverage loss **at that commit** — and the fix-up re-added upstream's copy byte-identically. "True duplicate" holds only *because of* the fix-up. Bisecting to `9f91c0f90` yields a weaker suite than either parent.

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
