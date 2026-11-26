# DELTA RE-GATE — promote hand-back recovery, BUMP COMMIT B59 = 6ee363fb (v0.31.59)

**Date**: 2026-11-25/26Z · **Tester gate** (cold-spawn, operating freeze) · **Lane**: promote-machinery / release cut v0.31.59
**Worktree**: `/home/nea/ensemble-src-wt-maintsys-cut2` (branch `latest`)
**Gated tree**: **B59 = `6ee363fb5726b42542718dc63ba280a28bc97bbd`** ("chore(release): bump version to 0.31.59"), single parent **P = `4a17398d28c1f570c76055a3b4814a8c71b202b6`** (= origin/latest integration tip — a MERGE commit, two parents `40301a5e3` + `4c252adf5`).
**Baseline**: the committed RESULTS at the previously-gated tree `f418bdd6e` — `.agents/tester/RESULTS/2026-11-25-promote-handback-v0318-delta-regate.md`; blob `17fc6129d7ef9474dc48100d5581d0ba659d6545` — **identical at f418bdd6e, at B59, and in the worktree** (re-derived this gate).
This report adds ONE further DOCS-ONLY commit on top of B59 (this file). No code, no test, no payload path is touched by that commit — the gate evidence for B59 carries to the tagged tree unchanged.

---

## 0. VERDICT

**READY — PASS.** B59 is exactly the 5-file version delta on the gated tree, declares `0.31.59` in all five version-bearing files (textual AND runtime), and **every lane leg at B59 reproduces the f418bdd6e baseline with NET-NEW = 0 by FAILED-name set diff** (never a count comparison). The cure is proven not to have leaked backward (three bases stay RED with byte-identical FAILED sets). Fences held on every leg; live-install sentinels are byte-identical before/after every leg; the worktree stayed clean at HEAD = B59 throughout.

| Gate | Subject | Result |
|---|---|---|
| D1 | delta identity P..B59 + five-file version assertion + runtime version proof @ B59 | ✅ PASS — 5 paths (+10/−4); all five files `0.31.59`; 0 residue; `daemon.__version__ == 0.31.59` + `test_health_check` 1 passed |
| D2 | the lane's legs @ B59 (14 packs, 17 runs incl. 3 base legs, the 4.12 re-runs and P11 shape 2) | ✅ NET-NEW 0 on every leg; all three bases RED; P11 both shapes 43P/1F (the one pre-existing pin) |
| D3 | foreign-delta boundary + C-1 lib.sh blob + continuation-comment scan count | ✅ the 6 foreign paths touch NO gated surface; lib.sh blob `fc2c0f667…` == fix tip; scan count 0 |
| D4 | docs-only audit commit on `latest` | ✅ this file — the single docs-only delta above B59 (sha reported to the leader) |
| D5 | fences + live-install sentinels | ✅ all legs fenced (HOME / PG / boot-sweep / self-env / timeouts); sentinels byte-identical; porcelain empty; HEAD unchanged |

---

## 1. SCOPE

The commission enumerated D1–D5; I executed **all of it** and added nothing. Every pack ran as **ONE pack per worker** under `timeout 300` (the PACKS.md rows' 420/600 s figures exceed the house 5-min cap and were not used — every pack finished inside 300 s). Not run, by commission: the five `por_*` packs (unfenced — they print credentials to stdout) and `soak_gate_mcp_warmup_acceptance.sh` (only scanned by the lint). No quick fixes were authorised or applied anywhere (release gate — report-only).

---

## 2. D1 — DELTA IDENTITY + VERSION ASSERTION + RUNTIME PROOF (workers `7e1e97bd`, `14bf53e6`)

| Check | Evidence | Result |
|---|---|---|
| Identity | `rev-parse HEAD` = B59; branch `latest`; `rev-list --parents -n1 B59` = `6ee363fb… 4a17398d2…` (exactly ONE parent P); porcelain `--untracked-files=all` EMPTY | ✅ |
| Delta stat `P..B59` | `CHANGELOG.md \| 6 ++++++` · `daemon/__init__.py \| 2 +-` · `pyproject.toml \| 2 +-` · `tests/test_api.py \| 2 +-` · `uv.lock \| 2 +-` → **5 files changed, 10 insertions(+), 4 deletions(-)**; `--name-status` = 5 × `M` | ✅ exactly the 5 paths |
| uv.lock hunk | `[[package]] name = "ensemble"` → `-version = "0.31.58"` / `+version = "0.31.59"` — the ensemble version line ONLY | ✅ |
| pyproject.toml @ B59 | line 3 `version = "0.31.59"` | ✅ |
| daemon/__init__.py @ B59 | line 3 `__version__ = "0.31.59"` | ✅ |
| tests/test_api.py @ B59 | line 227 `assert data["version"] == "0.31.59"` (the only version assertion; :253/:308 are `version_tag=None` params) | ✅ |
| uv.lock @ B59 | `name = "ensemble"` block, line 482 `version = "0.31.59"` | ✅ |
| CHANGELOG.md @ B59 | line 8 `## [v0.31.59] — 2026-11-25`; line 14 `## [v0.31.58]`; line 20 `## [v0.31.56]` (strictly newer on top) | ✅ |
| Residue | `git grep -n '0\.31\.58' B59 -- pyproject.toml daemon/__init__.py tests/test_api.py uv.lock` → rc=1, **zero hits**; same for `0\.31\.57` and `0\.31\.60` | ✅ |
| Runtime proof (a) | under the fence: `daemon.__file__` = `/home/nea/ensemble-src-wt-maintsys-cut2/daemon/__init__.py` (INSIDE WT) and `daemon.__version__` = **`0.31.59`** (python 3.14.7) | ✅ |
| Runtime proof (b) | the version-asserting node is `tests/test_api.py::test_health_check` (assert at :227, enclosing `async def test_health_check(client):` at :219) → direct node run → **1 passed** in 4.39 s (pytest), 12 s wall | ✅ |

Sentinels before/after: `.launcher-state` mtime `2026-11-23 16:44:36.521921185 +0000` size 96; `current` → `<LIVE>/releases/v0.31.52` — identical.

---

## 3. D2 — THE LANE'S LEGS AT B59 (the tree that will be tagged)

**NET-NEW method (every leg):** the FAILED set at B59 is compared by **NAME** against the registered/baseline set — `serving_green-4` (PACKS.md:5736), `bounded_waits-1` = `R happy-path: rc 0` (PACKS.md:5735 / QUARANTINE.md), the lint's 7 pinned occurrences (`tests/shell_continuation_scan.py` KNOWN_PRE_EXISTING), the 33 `supervision-33` names (baseline §4.10), the 31 cure ids at the three bases (baseline §4.1), and the P11 frontend allowlist pin. Count-only comparison was not accepted anywhere.

| # | Leg (pack) | @ B59 | rc | wall | Baseline @ f418bdd6e | NET-NEW | Verdict | Worker |
|---|---|---|---|---|---|---|---|---|
| 4.1 | `tests/packs/promote_handback_false_halt_repro_test.sh` — HEAD @ B59 | **RESULT: PASS · 62/62** (cure 46/46 + merge-defect 16/16) | 0 | 45 s | 62/62 | 0 (FAILED set ∅) | ✅ | `fe0cb7d7` |
| 4.1b | same harness — cure legs at the three bases | **31P/31F RED ×3** (cure a 4/4 · b 0/6 · c 3/0 · d 5/0 · e 1/5 · f 1/8 · g 1/4 · h 0/4 = 15 pass/31 FAIL; d2,d3 16/0) | 1 ×3 | 20/19/17 s | 31P/31F | bases MUST stay red — they do; the 3 FAILED sets are **byte-identical** (sha `c4c07d5e…`) and equal the baseline's 31 ids | ✅ | `1824c76f` |
| 4.2 | `tests/test_bounded_subprocess_waits.sh` | **PASS=74 FAIL=1 SKIP=0** — the 1 = `FAIL: R happy-path: rc 0` (`expected: hb-rc=0` / `actual: hb-rc=1`); S5/S5b emit no FAIL line | 1 | 36 s | 74P/1F | 0 (∅/∅ by `comm`) | ✅ | `eef74b9c` |
| 4.3 | `test/packs/shell_continuation_comment_lint_test.sh` | **leg 1 RESULT: PASS — 7 occurrence(s), all [PINNED], 0 [NEW], 0 [STALE PIN]**; leg 2 `PASS=13 FAIL=0` | 0 | 9 s | same | 0 — the 7 pinned `path:line` are unchanged vs baseline (no line shift) | ✅ | `63e41286` |
| 4.4 | `tests/test_handback_start_timeout_journal_row_test.sh` | **`PASS=13 FAIL=0`** (+ `pre-fix shape reconstructed`) | 0 | 8 s | 13/0 | 0 | ✅ | `37b57653` |
| 4.5 | `tests/packs/promote_handback_halt_gate_legs_test.sh` | **86/86**, 6/6 legs (g2 10/10 · g4 19/19 · g5 21/21 · d5 16/16 · d6 15/15 · d7 5/5) | 0 | 75 s | 86/86 | 0 | ✅ | `32393987` |
| 4.6 | `tests/test_launcher.sh` | **`launcher tests: 519 passed, 0 failed, 1 skipped`** + `ALL PASS`; skip = `8b-r2` (opt-in live-install comparison, not a convergence proof) | 0 | 88 s | 519/0/1 | 0 | ✅ | `06e4b4ad` |
| 4.7 | `tests/test_supervision_twins.sh` | **`PASS=97 FAIL=0`** | 0 | 29 s | 97/0 | 0 | ✅ | `7b1725f9` |
| 4.8 | `tests/test_ceremony_wallclock.sh` | **`Test summary: 50 passed / 0 failed`** | 0 | 4 s | 50/0 | 0 | ✅ | `059f9d61` |
| 4.9 | `tests/test_hbk_err_inode_churn.sh` | **`41 passed / 0 failed`** | 0 | 13 s | 41/0 | 0 | ✅ | `f21981a3` |
| 4.10 | `tests/test_supervision_stop_handback.sh` | **`PASS=232 FAIL=33 SKIP=1`** (SKIP = `P3-PIN-09-base`) | 1 | 103 s | 232/33/1 | **0** — HEAD's FAILED set is **byte-identical** to the baseline's 33 (both sha `6550ee46043c0e0101eb85a8f9a469bdfa6eddbcc59bd9307667c651db03f6ee`; `comm` ∅/∅) | ✅ | `766c0c0d` |
| 4.11 | `tests/packs/promote_handback_halt_serving_green_test.sh` | **`PASS=136 FAIL=4`** | 1 | 186 s | 136/4 | **0** — the 4 = the registered `serving_green-4` (both sides sha `72732900fb4596bc26d4ac08d332bf40742c6c0f296aab64b7f8e9972d2f9d7c`; `comm` ∅/∅) | ✅ | `a5b3c3bf` |
| 4.12 | `tests/packs/handback_verify_budget_test.sh` — 3× + 3× flake isolation (PACKS.md:1719 re-run rule) | **120P/0F, 120P/0F, 119P/1F** (run 3: `FAIL: L8b the squeeze is observable (350−120=230 at the defaults)`) then **120P/0F ×3**; mutation matrix 17/17 detected in ALL 6 runs | 0,0,1,0,0,0 | 170/167/174/170/169/170 s | 120/0 ×3 | 0 — the single red is the REGISTERED L8b 1-second boundary flake; it did NOT reproduce in the 3 re-runs (flake confirmed, not a regression) | ✅ | `ee874db4` |
| 4.13 | `tests/test_adaptive_soak.sh` at the SHIPPED cap (`ADAPTIVE_SOAK_CAP_S` unset → 120) | **`Test summary: 35 passed / 0 failed`**; C6 section ran, zero failure markers | 0 | 194 s | 35/0 | 0 | ✅ | `66878882` |
| 4.14a | P11 drift guard `tests/unit/repositories/test_source_gc_repository.py` — **shape 1 @ B59** | **44 collected · 43P / 1F**; class `TestP11BootNoBehaviorChange` 13P/1F | 1 | 49 s | 43P/1F | 0 — the one red is the pre-existing frontend allowlist pin | ✅ | `33662251` |
| 4.14b | P11 drift guard — **shape 2 above B59** (empty commit on a scratch `--shared` clone under mktemp) | **44 collected · 43P / 1F**; class 13P/1F | 1 | 54 s | — | 0 — identical FAILED line; frontend diff input byte-identical | ✅ | `33662251` |

### 4.1b notes — the cure did not leak backward (worker `1824c76f`)
- Base method (the baseline §4.1 method, replicated): `git -C WT -c gc.auto=0 archive <sha> | tar -x -C "$(mktemp -d)"` for each of `35ec50e3c`, `dd007f16d`, `ed8ab508c` — gitless extractions (no `.git` in any of them), **no worktree registered** (`worktree list | wc -l` 110 → 110, empty diff). ONLY the two harness files were dropped in at the same relative paths (`tests/packs/promote_handback_false_halt_repro_test.sh` sha256 `52e9c620…` and `tests/upgrade_test_lib.sh` sha256 `df21f371…`, identical WT↔each extraction); the bases' own production files were never overwritten.
- Production identity: `scripts/upgrade/lib.sh` sha256 at every base = `e3a44fc1af98d4680a5fda05b9db0debd42c2709032f688ac06ab22c03254655` (identical across the three bases) ≠ WT `b44d8b805aa453922fd48923707d1a8b0c35ac1d182c94bcddd433b0fe2e1058` ⇒ the cure is present at B59 and absent at all three bases.
- FAILED-name sets at the three bases: **byte-identical** — sha256 `c4c07d5ef51ff18336c24a11938041c4bddd2f2d19042ee0a4cd97cc16a10825` each, `cmp` rc 0/0/0 — and equal to the baseline's 31 ids: `a1 a2 a6 a8 b1 b2 b3 b4 b5 b6 e1 e2 e3 e5 e6 f1 f2 f3 f4 f5 f6 f7 f9 g1 g2 g4 g5 h1 h2 h3 h4`. NET-NEW ∅ and RESOLVED ∅ ×3. Shell-noise (command-not-found / syntax error / unbound variable) = 0 on all three ⇒ clean reds. HEAD's FAILED set is empty.
- Structural note (pre-flight finding, not a defect): the C-1 defect commit `5ba3223ed` is not an ancestor of any of the three bases (nor is the fix `a231ba06a`), so `d2,d3 = 16/0` at the bases is structural, not incidental.

### 4.10 notes — `supervision-33` (worker `766c0c0d`)
The baseline's 33 names were extracted from the committed baseline file itself (lines 136–147, ` · `-separated, trailing sentence period stripped) and hashed to `6550ee46…` — matching the commissioned value, count 33. The pack's 33 FAILED names hash to the SAME value (33 lines each) ⇒ set equality, NET-NEW 0, RESOLVED 0. The single SKIP is `P3-PIN-09-base` (the base lib.sh copy is not supplied), as in the baseline. The 33 names are still not written into PACKS.md/QUARANTINE.md — see §7.

### 4.11 notes — `serving_green-4` exact (worker `a5b3c3bf`)
`P-13 a request + not-green ⇒ the chain executes` · `P-13 the executed rollback names its disposition` · `P-30 (=0) the chain still fires when the escape does not apply` · `P-30 =0 restores the unconditional rollback` — extracted from the baseline (§4.11) and cross-checked against the pack's 4 FAIL lines: byte-identical, both sides sha `72732900…`. `comm -23` (NET-NEW) ∅ · `comm -13` (RESOLVED) ∅.

### 4.12 notes — the L8b flake (worker `ee874db4`)
The registered L8b 1-second boundary flake reproduced **once in six runs** (run 3): `FAIL: L8b the squeeze is observable (350−120=230 at the defaults)` — the effective squeeze read 229 s against the asserted 230 s on a live clock tick. The three re-runs mandated by PACKS.md:1719 were clean (120P/0F ×3), so the red is the documented flake, not a regression. The 17-mutation matrix detected 17/17 in every run (non-vacuous). No other FAIL line and no timeout occurred in any of the six runs.

### 4.14 notes — P11 drift guard, BOTH shapes (worker `33662251`) — the commission's shape-1 premise corrected by measurement
- The span base is `git rev-list --first-parent --merges -1 HEAD` (test source ~:1243-1247; no env/argv override exists). Measured: `SPAN_BASE_SHAPE1 = SPAN_BASE_SHAPE2 = 4a17398d28c1f570c76055a3b4814a8c71b202b6` (= P), and P has **two parents** (`40301a5e3` + `4c252adf5`) ⇒ P is a merge.
- **Shape 1 @ B59 is NOT vacuous.** "B59..B59 empty" holds only when HEAD *is* the merge; B59 is a non-merge bump on top of merge P, so the measured span is **`4a17398d2..B59` = the 5-file bump delta (NON-VACUOUS)** — the same correction the predecessor's §4.14 made. The guard judged that span: the diff over its seven judged paths (`daemon/sources/cleanup.py`, `daemon/constants.py`, `daemon/manager.py`, `daemon/services/scheduling_service.py`, `daemon/routers/sources.py`, `frontend/`, `daemon/repositories/task/repository.py`) is **EMPTY** — the bump touches none.
- **Shape 2 above B59**: a `--shared` clone under mktemp with one empty commit (no worktree registered) ⇒ `B59..HEAD` EMPTY as required, same span base `4a17398d2`, same span content, same verdict. The clone's import resolved to the clone (`daemon.__file__` = `<clone>/daemon/__init__.py`, version `0.31.59`).
- The single red, both shapes, verbatim:
  `FAILED tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level`
  — the pre-existing `frontend/` allowlist pin (68 offending hunks vs the fixed base `858a85e31`). Its diff input (`git diff --unified=0 858a85e31..HEAD -- frontend/`) hashes to **`93dc0d2c797cd8e874933c12827f342c6ae0b74bfdf6db540f8fe23999df4dd8`** (458,929 bytes) in BOTH shapes — identical to the predecessor's recorded value ⇒ base-attributed, net-new 0. No other node failed.

---

## 4. D3 — FOREIGN-DELTA BOUNDARY (worker `7e1e97bd`)

**The boundary, stated.** B59's parent P (`4a17398d2`) carries a **6-path foreign delta** vs the previously-gated tree `f418bdd6e`, all under `.agents/` or `tests/`:
```
A .agents/shared/analysis-20261026-v0278-soak-failure.md
M .agents/tester/PACKS.md
M .agents/tester/RESULTS/2026-10-28-soak-gate-mcp-warmup-functional-gate.md
A .agents/tester/RESULTS/2026-11-25-soak-gate-evidence-carryin-f11-repin-gate.md
M tests/helpers/preflight_mutation_apply.py
M tests/unit/test_secret_do_not_touch_lane_static.py
```
These are **NOT this lane's surface** — they are the soak-gate lane's evidence carry-in (the F-11 re-pin gate's own RESULTS record rides in the tip). Commit trail: `55e46617a` (soak RCA carry-in) → `263029e83` (re-pin the F-11 spawn-site static pin) → `0e2b02bac` (re-pin the M22 mutation-plant literal) → `53252119f` (register the F-11 pin as a named pack) → `991c41e8c` (align the F-11 row prose) → `4c252adf5` + `40301a5e3` (the two landing merges) → `4a17398d2`.

**Two of the six are test files, so the comparison had to be against the baseline's FAILED-NAME sets — which it was, on every leg.** Additionally, the gated-surface check was run directly: the diff over every pack/script/launcher surface this gate touches
(`tests/packs/promote_handback_false_halt_repro_test.sh`, `tests/upgrade_test_lib.sh`, `tests/test_bounded_subprocess_waits.sh`, `test/packs/shell_continuation_comment_lint_test.sh`, `tests/shell_continuation_scan.py`, `tests/test_handback_start_timeout_journal_row_test.sh`, `tests/packs/promote_handback_halt_gate_legs_test.sh`, `tests/test_launcher.sh`, `tests/test_supervision_twins.sh`, `tests/test_ceremony_wallclock.sh`, `tests/test_hbk_err_inode_churn.sh`, `tests/test_supervision_stop_handback.sh`, `tests/packs/promote_handback_halt_serving_green_test.sh`, `tests/packs/handback_verify_budget_test.sh`, `tests/test_adaptive_soak.sh`, `tests/unit/repositories/test_source_gc_repository.py`, `scripts/upgrade/lib.sh`, `scripts/upgrade/status.sh`, `scripts/upgrade/promote.sh`, `scripts/upgrade/stage.sh`, `launcher.sh`)
across `f418bdd6e..6ee363fb` is **EMPTY** — i.e. **no foreign delta touched any gated surface** (the only test file in the whole span besides the two above is `tests/test_api.py`, which the BUMP itself changes by design). Consequence: **no leg's FAILED set differs from the baseline**, so there is no foreign-caused difference to adjudicate. (Disclosed: my recon brief's expectation "only tests/test_api.py" for that pathspec was not producible — `tests/test_api.py` was absent from the pathspec list; the substantive result — zero gated surfaces touched — is stronger and holds.)

**C-1 cure identity at B59.**
- `git rev-parse 6ee363fb:scripts/upgrade/lib.sh` = **`fc2c0f667648994ffe37689ceac48a5e9b968241`** = `git rev-parse 5f866fd41:scripts/upgrade/lib.sh` (the fix tip) — **equal** ⇒ the C-1 fix's lib.sh is exactly the blob in the tree that will be tagged.
- sha256 of the worktree `scripts/upgrade/lib.sh` = `b44d8b805aa453922fd48923707d1a8b0c35ac1d182c94bcddd433b0fe2e1058` (unchanged vs the baseline tree).
- `git diff --stat 6ee363fb 4a17398d2 -- scripts/upgrade/lib.sh scripts/upgrade/status.sh scripts/upgrade/promote.sh scripts/upgrade/stage.sh launcher.sh daemon/tools/upgrade_tools.py daemon/manager.py` = **EMPTY** — the foreign delta touched none of the promote-machinery surfaces.

**Continuation-comment scan count in `scripts/upgrade/lib.sh` at B59 = 0**, by two independent methods: (a) the repo's own scanner `tests/shell_continuation_scan.py` on lib.sh → `0 occurrence(s)`, `RESULT: PASS` (the tree-wide scan still reports 7 occurrences, all pre-existing `[PINNED]` in the `por_*` packs and `soak_gate_mcp_warmup_acceptance.sh`, none in lib.sh); (b) a mechanical awk check (line ending in `\` whose next line matches `^[[:space:]]*#`) → count 0.

---

## 5. D5 — FENCES, SENTINELS, DEVIATIONS

### 5.1 Fence applied at run time on every leg (pack-native fences were NOT edited)
mktemp `FAKE_HOME` + `export HOME=` (never the ambient HOME; the literal mktemp path baked into each runner via a `__FAKE_HOME__` placeholder + `sed`) · per-variable `unset` of `POSTGRES_DB/USER/PASSWORD/HOST/PORT/URL`, `PGHOST/PORT/USER/PASSWORD/DATABASE`, `DATABASE_URL`, `ENSEMBLE_DB_DSN`, `ENSEMBLE_UPGRADE_SCRIPTS_DIR`, `ENSEMBLE_LAUNCHER_STATE_FILE`, `PORT`, `DAEMON_PORT`, `OPENAI_API_KEY` (and `ADAPTIVE_SOAK_CAP_S` on 4.13) — never the `env -u POSTGRES_*` no-op · `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly (never `env -u`'d) · `ENSEMBLE_SELF_ENV=dev` · `PYTHONDONTWRITEBYTECODE=1` · `GIT_CONFIG_COUNT=1 / GIT_CONFIG_KEY_0=gc.auto / GIT_CONFIG_VALUE_0=0` (so in-pack git subprocesses inherit it) · `umask 077` · `timeout 300` outer layer on every run (pack-internal timers left enabled) · **no bare `env` anywhere**, no `#` comment inside a backslash continuation, no `env \` continuations (the por_* defect class) · every git call `-c gc.auto=0` · the `por_*` packs UNRUN · no push / promote / tag / force / branch delete-rename / lock-file removal / `rm -rf` outside mktemp · ports 8088 / 8079 / 9797 never bound or killed, no process killed by name.

Fence proof printed inside every runner: `HOME=/tmp/…` (non-empty, never `/home/nea`) · `POSTGRES_NAMES=0` · `PG_NAMES=0` · the seven unsets · `BOOT_SWEEP=0 SELF_ENV=dev`. A void check was in force: a run whose `HOME` printed empty or `/home/nea` was to be discarded and redone.

### 5.2 Sentinels — byte-identical before AND after every leg
- `<LIVE>/.launcher-state` → `mtime=2026-11-23 16:44:36.521921185 +0000 | size=96`
- `readlink -f <LIVE>/current` → `<LIVE>/releases/v0.31.52`
- WT `HEAD` = `6ee363fb5726b42542718dc63ba280a28bc97bbd` (B59) before and after every leg.
- WT `git status --porcelain --untracked-files=all` EMPTY before and after every leg; no tracked file modified.
- `git worktree list | wc -l` unchanged across the gate's legs (110 → 110); no worktree registered by this lane; no `/tmp`-rooted worktree belongs to this lane (the 2 present — `/tmp/wt-base-c2`, `/tmp/wt-stage-v03155` — belong to other lanes).
- `/home/nea/ensemble-src` and the three fix worktrees and the stage worktree were never touched. The live install was read-only (names + stat on two sentinels); `state.json`, the journal and everything else under it were never read or written.

### 5.3 Disclosed deviations (all in the safe direction)
1. **D1 runtime leg**: one earlier attempt was VOIDED (pytest rc=4 = node-not-found — the worker's detector matched `def ` but not `async def `, so it resolved to a fixture). The worker corrected the detector and re-ran fence-clean; the clean re-run is the authoritative result.
2. **4.1b / 4.14 briefs carried two wrong premises about the host, corrected by measurement**: (a) the worktree count is **110**, not the 109 stated in the brief — unchanged before/after each run; (b) `worktree list | grep -c /tmp/` is **2, not 0** — those two worktrees belong to other lanes. The enforced invariant in both legs was "UNCHANGED + empty diff", which held.
3. **4.12 ran 6 times, not 3**: the third run reproduced the registered L8b boundary flake, and PACKS.md:1719 requires a re-run before a red there is read as a regression — three further runs were executed (all clean). The commissioned 3× tally is runs 1–3; runs 4–6 are the flake isolation.
4. **4.13** emitted one benign pack-internal stderr line (`tests/test_adaptive_soak.sh:587` — a first write into a not-yet-created stub dir; the following write succeeds). Pre-existing, filed, not fixed.
5. **4.12** leaked 6 throwaway fixture dirs (`/tmp/hbv-l11.*`, 8 → 14) — the pack's known hygiene defect (one per run, `:723`). Nothing outside the mktemp dirs was removed.
6. **Three workers** reported "PACKS.md not registered" from a repo-root lookup; PACKS.md lives at `.agents/tester/PACKS.md` and the recon proved **all 14 packs registered with ≥1 row** (row counts 1–11 each; the L8b rule at :1719; `serving_green-4`/`supervision-33` at :5736-5737). Informational only.
7. **Additive out-of-repo writes** (disclosed, no repo mutation): the P11 worker recorded POK row `p11-01-span-base-nonmerge-bump` (the span-base invariant) and filed `skill_feedback` on `test-pack-execution`; several workers filed skill feedback. The 4.10 worker's run log was leak-scanned: zero hits for `OPENAI_API_KEY`, `sk-`, `postgres://`, `postgresql://`, `password`, `SYSTEM_ENCRYPTION_KEY`.
8. **Report-format note**: none of the shell packs emits a literal `RESULT: PASS` token — each prints its own tally line; the verdicts above are the packs' own summary lines + rc, quoted verbatim. The one pack that DOES emit `RESULT:` is the repro harness (`RESULT: PASS`).

---

## 6. D4 — DOCS-ONLY DELTA STATEMENT

The only change on top of B59 made by this gate is **this file** (`.agents/tester/RESULTS/2026-11-25-promote-handback-v0319-delta-regate.md`), committed on `latest` in the worktree with a targeted `git add` (no push). The delta `B59..HEAD` is therefore **docs-only — no code, no test, no payload path** — so every D1/D2/D3/D5 measurement taken at B59 carries unchanged to the tree that will be tagged `v0.31.59` and staged. The commit sha is reported to the leader in the final report (it cannot be embedded in its own content). No PACKS.md fold-in was made here (shared surface; PACKS.md is a separate maintenance step).

---

## 7. RESIDUAL RISKS & ROUTED FOLLOW-UPS

1. 🟡 **`supervision-33` is enumerated in the baseline RESULTS but still not in PACKS.md/QUARANTINE.md** — the registry rows carry only the set name + counts. Suggested: fold the 33 names (baseline §4.10) into the quarantine ledger so future gates can do a registry-based name diff without an executed base leg. (PACKS.md maintenance; not done under this docs-only commission.)
2. 🟡 **Shape-1 vacuity is a property of HEAD being the merge, not of "the bump".** A gate at a non-merge HEAD (a bump commit) gets a NON-vacuous shape 1 — here it judged the 5-file bump delta and passed. Gate recipes should state the MEASURED span base (`git rev-list --first-parent --merges -1 HEAD`) instead of assuming "HEAD..HEAD". (Recorded as POK `p11-01-span-base-nonmerge-bump`.)
3. 🟡 **The L8b 1-second boundary flake reproduced at B59** (1 of 6 runs) after NOT reproducing in the predecessor's 3 runs. It is the registered flake and did not reproduce in the 3 re-runs, so it is not a regression — but it is live: a future single red there must still be re-run before being read as a regression. The pack's 1-second tolerance remains the weakest timing margin in the lane.
4. 🟡 **The `frontend/` allowlist pin (P11's single red) is not in QUARANTINE.md** — it lives in the tester RESULTS ledger + PACKS.md (predecessor residual, unchanged).
5. 🟡 **`handback_verify_budget_test.sh` leaks one `/tmp/hbv-l11.*` dir per run** (6 leaked in this gate) and has **no inner timeout layer** — routed to the pack owner.
6. 🟢 Pre-existing `/tmp` litter from earlier lanes was left untouched (not this gate's).
7. 🟢 `tests/test_adaptive_soak.sh:587`'s benign stderr line is pre-existing and unrelated to this lane.

---

## 8. WORKER INSTANCES

| Leg | Worker | Skill |
|---|---|---|
| D1 static + D3 static + PACKS.md/script inventory + sentinels | `7e1e97bd` | none (read-only infra) |
| D1 runtime version proof (tests/test_api.py node) | `14bf53e6` | test-pack-execution |
| 4.1 repro harness HEAD @ B59 | `fe0cb7d7` | test-pack-execution |
| 4.1b repro harness at the three bases | `1824c76f` | test-pack-execution |
| 4.2 bounded waits | `eef74b9c` | test-pack-execution |
| 4.3 continuation-comment lint | `63e41286` | test-pack-execution |
| 4.4 journal row | `37b57653` | test-pack-execution |
| 4.5 gate legs | `32393987` | test-pack-execution |
| 4.6 launcher | `06e4b4ad` | test-pack-execution |
| 4.7 twins | `7b1725f9` | test-pack-execution |
| 4.8 wallclock | `059f9d61` | test-pack-execution |
| 4.9 hbk churn | `f21981a3` | test-pack-execution |
| 4.10 stop-handback (33-name diff) | `766c0c0d` | test-pack-execution |
| 4.11 serving green (4-name diff) | `a5b3c3bf` | test-pack-execution |
| 4.12 budget ×3 (+3 flake isolation) | `ee874db4` | test-pack-execution |
| 4.13 adaptive soak (shipped cap) | `66878882` | test-pack-execution |
| 4.14 P11 both shapes | `33662251` | test-pack-execution |
| D4 docs commit | (see final report) | none (git infra) |
