# DELTA RE-GATE — promote hand-back recovery, BUMP COMMIT B = 667625a3a (v0.31.58)

**Date**: 2026-11-25T19:40Z · **Tester gate** (cold-spawn, operating freeze) · **Lane**: promote-machinery / release cut v0.31.58
**Worktree**: `/home/nea/ensemble-src-wt-maintsys-cut2` (branch `latest`)
**Gated tree**: **B = `667625a3a710727435375bf104da62ec45cbef16`** ("chore(release): bump version to 0.31.58"), single parent **P = `e68e26d25a7152f187014329f2b7cc23ffe01643`** (the merged tree that passed the predecessor's scoped re-gate).
This report adds ONE further DOCS-ONLY commit on top of B (this file). No code, no test, no payload path is touched by that commit — the gate evidence for B carries to the tagged tree unchanged.

---

## 0. VERDICT

**READY — PASS.** The bump commit B is exactly the 5-file version delta on the gated tree, declares `0.31.58` in all five
version-bearing files (textual AND runtime), and every lane leg at B reproduces the e68e26d25 baseline with
**NET-NEW = 0 by FAILED-name set diff** (never a count comparison). The cure is proven not to have leaked backward
(three bases stay RED with byte-identical FAILED sets). Fences held on every leg; live-install sentinels are
byte-identical before/after every leg; the worktree stayed clean at HEAD = B throughout.

| Gate | Subject | Result |
|---|---|---|
| D1 | delta identity P..B + five-file version assertion + runtime version proof @ B | ✅ PASS — 5 paths (+10/−4); all five files `0.31.58`; 0 residue; `daemon.__version__ == 0.31.58` + `test_health_check` PASS |
| D2 | the lane's legs @ B (14 packs, 17 runs incl. 3 base legs + P11 shape 2) | ✅ NET-NEW 0 on every leg; all three bases RED; P11 both shapes 43P/1F (the one pre-existing pin) |
| D3 | fences + live-install sentinels | ✅ all legs fenced; sentinels byte-identical; porcelain empty; HEAD unchanged (2 voided first runs disclosed, §5.3) |
| D4 | docs-only audit commit on `latest` | ✅ this file — the single docs-only delta above B (sha reported to the leader) |

---

## 1. SCOPE

The commission enumerated the scope (D1–D4); I executed **all of it** and added nothing. Every pack ran as ONE pack per
worker under `timeout 300` (the PACKS.md rows' 420/600 s figures exceed the house 5-min cap and were not used — every
pack finished well inside 300 s). Not run, by commission: the five `por_*` packs (unfenced — they print credentials
to stdout) and `soak_gate_mcp_warmup_acceptance.sh` (only scanned by the lint). No quick fixes were authorised or
applied anywhere (release gate — report-only).

---

## 2. BASELINE — the e68e26d25 scoped re-gate (predecessor `850c72a1`)

Verdict as commissioned and used as this gate's baseline: **cure 62/62 @ e68e26d25; cure legs 15P/31F RED at all three
bases `35ec50e3c` / `dd007f16d` / `ed8ab508c`; evil-merge 0 with a live control; `lib.sh`/`status.sh` byte-identical to
the fix tip `5f866fd41`; net-new 0 on every lane leg by FAILED-name diff.**

Provenance note: no separate RESULTS file for the e68e26d25 run exists under `.agents/tester/RESULTS/` (only
`2026-11-25-promote-handback-merged-tree-regate.md`, which documents the F = `7ab616612` gate). That file's R5 table
carries the identical per-leg numbers the commission cites (86/86 · 519/0/1 · 97/0 · 50/0 · 41/0 · 120/0 · 136/4 ·
232/33/1 · 35/0 · 74/1 · 62/62 · P11 43P/1F). This gate reproduces every one of them at B (§4). One gap in the written
baseline is closed here: the **33 `supervision-33` names were never enumerated** in PACKS.md/QUARANTINE.md — this gate
derived them by a true dual leg (HEAD @ B vs BASE @ P) and lists all 33 in §4.10.

---

## 3. D1 — DELTA IDENTITY + VERSION ASSERTION + RUNTIME PROOF (worker `49d5cfae`)

| Check | Evidence | Result |
|---|---|---|
| Identity | `rev-parse HEAD` = B; branch `latest`; `rev-list --parents -n1 B` = `667625a3a… e68e26d25…` (exactly ONE parent P); porcelain `--untracked-files=all` EMPTY | ✅ |
| Delta stat `P..B` | `CHANGELOG.md \| 6 ++++++` · `daemon/__init__.py \| 2 +-` · `pyproject.toml \| 2 +-` · `tests/test_api.py \| 2 +-` · `uv.lock \| 2 +-` → **5 files changed, 10 insertions(+), 4 deletions(-)**; `--name-status` = 5 × `M`, nothing else | ✅ exactly the 5 paths |
| uv.lock hunk | `[[package]] name = "ensemble"` → `-version = "0.31.56"` / `+version = "0.31.58"` — the ensemble version line ONLY | ✅ |
| pyproject.toml @ B | line 3 `version = "0.31.58"` | ✅ |
| daemon/__init__.py @ B | line 3 `__version__ = "0.31.58"` | ✅ |
| tests/test_api.py @ B | line 227 `assert data["version"] == "0.31.58"` (the only version assertion; :253/:308 are `version_tag=None` params) | ✅ |
| uv.lock @ B | `name = "ensemble"` block, line 482 `version = "0.31.58"` | ✅ |
| CHANGELOG.md @ B | first heading line 8 `## [v0.31.58] — 2026-11-25`; second line 14 `## [v0.31.56]` (strictly newer on top) | ✅ |
| Residue | `git grep -n '0\.31\.56' B -- pyproject.toml daemon/__init__.py tests/test_api.py uv.lock` → rc=1, **zero hits** | ✅ |
| Runtime proof (a) | under the fence: `daemon.__file__` = `/home/nea/ensemble-src-wt-maintsys-cut2/daemon/__init__.py` (INSIDE WT) and `daemon.__version__` = **`0.31.58`** | ✅ |
| Runtime proof (b) | the version-asserting node is `tests/test_api.py::test_health_check` (the keyword `-k version` selects 0 nodes — collection-keyword mismatch, reported, not a failure); direct node run → **1 passed** in 3.84 s (the API's `/health` version equals `0.31.58`) | ✅ |

Sentinels before/after: `.launcher-state` mtime `2026-11-23 16:44:36.521921185 +0000` size 96; `current` →
`/home/nea/agents-ensemble/releases/v0.31.52` — identical.

---

## 4. D2 — THE LANE'S LEGS AT B (the tree that will be tagged)

**NET-NEW method (every leg):** the FAILED set at B is compared by **NAME** against the registered/baseline set —
`serving_green-4` (PACKS.md:1685/5719), `bounded_waits-1` = `R happy-path` (PACKS.md:2845/2846/5717, QUARANTINE.md:190),
the lint's 7 pinned occurrences (`tests/shell_continuation_scan.py` KNOWN_PRE_EXISTING), the P11 frontend allowlist pin
(PACKS.md:372/5726), and — for the pack whose 33 names were never written down — against an **executed BASE leg at P**
(§4.10). Count-only comparison was not accepted anywhere.

| # | Leg (pack) | @ B | rc | wall | Baseline @ e68e26d25 | NET-NEW | Verdict |
|---|---|---|---|---|---|---|---|
| 4.1 | `tests/packs/promote_handback_false_halt_repro_test.sh` — HEAD @ B | **RESULT: PASS · 62/62** (46 cure + 16 merge-defect) | 0 | inside 300 s | 62/62 | 0 (FAILED set ∅) | ✅ |
| 4.1b | same harness — cure legs at the three bases | **31P/31F RED ×3** (cure a–h 15/31; d2,d3 16/0) | 1 ×3 | ~34–44 s each incl. setup | 31P/31F | bases MUST stay red — they do | ✅ |
| 4.2 | `tests/test_bounded_subprocess_waits.sh` | **74P / 1F / 0S** | 1 | 38 s | 74P/1F | 0 — the 1 = `R happy-path: rc 0` only | ✅ |
| 4.3 | `test/packs/shell_continuation_comment_lint_test.sh` | **leg 1 RESULT: PASS — 7 occurrences, all [PINNED], 0 [NEW]; leg 2 PASS=13 FAIL=0** | 0 | ~10 s | same | 0 (set-diff vs the 7 pins ∅/∅) | ✅ |
| 4.4 | `tests/test_handback_start_timeout_journal_row_test.sh` | **PASS=13 FAIL=0** (summary present; `pre-fix shape reconstructed`) | 0 | 7 s | 13/0 | 0 | ✅ |
| 4.5 | `tests/packs/promote_handback_halt_gate_legs_test.sh` | **86/86**, 6/6 legs (g2 10/10 · g4 19/19 · g5 21/21 · d5 16/16 · d6 15/15 · d7 5/5) | 0 | 74 s | 86/86 | 0 | ✅ |
| 4.6 | `tests/test_launcher.sh` | **519 passed / 0 failed / 1 skipped** + `ALL PASS`; skip = `8b-r2` (opt-in live-install comparison, not a convergence proof) | 0 | 86 s | 519/0/1 | 0 | ✅ |
| 4.7 | `tests/test_supervision_twins.sh` | **PASS=97 FAIL=0** | 0 | 27 s | 97/0 | 0 | ✅ |
| 4.8 | `tests/test_ceremony_wallclock.sh` | **50 passed / 0 failed** | 0 | 4 s | 50/0 | 0 | ✅ |
| 4.9 | `tests/test_hbk_err_inode_churn.sh` | **41 passed / 0 failed** | 0 | ~13 s | 41/0 | 0 | ✅ |
| 4.10 | `tests/test_supervision_stop_handback.sh` — HEAD @ B | **232P / 33F / 1S** | 1 | ~2 min | 232/33/1 | **0** — HEAD ≡ BASE by name (sha `6550ee46…` both, cmp rc 0) | ✅ |
| 4.10b | same pack — BASE @ P (`git archive`, the base's own pack) | **232P / 33F / 1S** | 1 | ~2 min | — | NET-NEW ∅, RESOLVED ∅ | ✅ |
| 4.11 | `tests/packs/promote_handback_halt_serving_green_test.sh` | **PASS=136 FAIL=4** | 1 | 186 s | 136/4 | **0** — exactly the registered `serving_green-4` (sorted sha `72732900…`; NET-NEW ∅, RESOLVED ∅) | ✅ |
| 4.12 | `tests/packs/handback_verify_budget_test.sh` — 3× consecutive (PACKS.md:1701 rule) | **120P/0F ×3** (174.1 / 170.5 / 177.0 s); 17-mutation matrix 17/17 detected each run | 0 ×3 | 3 × ~3 min | 120/0 ×3 | 0; **L8b boundary flake did not reproduce (0 `FAIL: L8b` lines in 3 runs)** | ✅ 3/3 |
| 4.13 | `tests/test_adaptive_soak.sh` at the SHIPPED cap (`ADAPTIVE_SOAK_CAP_S` unset → 120) | **35 passed / 0 failed**; C6 section ran, zero failure markers | 0 | 200.9 s | 35/0 | 0 | ✅ |
| 4.14a | P11 drift guard `tests/unit/repositories/test_source_gc_repository.py` — **shape 1 @ B** | **44 collected · 43P / 1F**; class `TestP11BootNoBehaviorChange` 13P/1F | — | 147 s (121.84 s pytest) | 43P/1F | 0 — the one red is the pre-existing frontend allowlist pin | ✅ |
| 4.14b | P11 drift guard — **shape 2 above B** (empty commit `8192636f` on a `--shared` clone under mktemp) | **44 collected · 43P / 1F**; class 13P/1F | — | 80 s (60.72 s pytest) | — | 0 — identical FAILED set | ✅ |

### 4.1 notes — cure matrix (worker `97e6c8c2`)
- Base method: `git -C WT archive <sha> | tar -x -C $(mktemp -d)` (no worktree metadata); ONLY the two harness files
  were dropped in at the same relative paths — `tests/packs/promote_handback_false_halt_repro_test.sh`
  (sha256 `52e9c620…`) and `tests/upgrade_test_lib.sh` (sha256 `df21f371…`) — identical WT↔base; the bases' own
  production files were never overwritten.
- Production identity: `scripts/upgrade/lib.sh` sha256 WT `b44d8b80…` ≠ every base `e3a44fc1…` (identical across the
  three bases) ⇒ the cure is present at B and absent at all three bases.
- Cure per-leg at each base: a 4/4 · b 0/6 · c 3/0 · d 5/0 · e 1/5 · f 1/8 · g 1/4 · h 0/4 = 15 pass / 31 FAIL;
  merge-defect legs d2,d3 = 16/0 on every tree.
- FAILED-name sets at the three bases: **byte-identical** (sha256 `c4c07d5e…` each; `cmp` rc 0/0/0) — the 31 cure ids
  `a1 a2 a6 a8 b1 b2 b3 b4 b5 b6 e1 e2 e3 e5 e6 f1 f2 f3 f4 f5 f6 f7 f9 g1 g2 g4 g5 h1 h2 h3 h4`. HEAD's FAILED set is
  empty. Shell-noise (command-not-found / syntax-error / unbound-variable) = 0 on all four legs ⇒ clean reds.

### 4.2 notes — the C-1 boundary stays cured (worker `c6612fe3`)
`PASS=74 FAIL=1 SKIP=0`; the single red verbatim `FAIL: R happy-path: rc 0 · expected: hb-rc=0 · actual: hb-rc=1`
(the registered host-class base red — the shared `LSOF_OK` stub prints no listener pid, pack :468-471). S5 (:450) and
S5b (:496) emit no failure line (the pack prints `FAIL: <name>` for every failed assertion; `_pass` is silent) —
arithmetic corroboration: the defect state at PACKS.md:5717 was 72P/3F with exactly S5/S5b net-new; this run is 74P/1F.

### 4.3 notes — the lint pins (worker `bc0cb1a1`)
The 7 `[PINNED]` occurrences printed by the scan = the registered set exactly:
`test/packs/por_b2_wedge_repro_pins_unit_test.sh:137` · `test/packs/por_b_boot_consistency_unit_test.sh:133` ·
`test/packs/por_b3_receipt_supervisor_unit_test.sh:136` · `test/packs/por_b4_stale_running_sweep_unit_test.sh:139` ·
`test/packs/por_c_checkpoint_retention_unit_test.sh:136` · `tests/packs/soak_gate_mcp_warmup_acceptance.sh:389` ·
`tests/packs/soak_gate_mcp_warmup_acceptance.sh:442`. 0 `[NEW]`; nothing disappeared (baseline drift = 0).

### 4.11 notes — `serving_green-4` exact (worker `a9fbf5f2`)
`P-13 a request + not-green ⇒ the chain executes` · `P-13 the executed rollback names its disposition` ·
`P-30 (=0) the chain still fires when the escape does not apply` · `P-30 =0 restores the unconditional rollback`.
`comm -23` (NET-NEW) ∅ · `comm -13` (RESOLVED) ∅.

### 4.10 notes — `supervision-33`, enumerated for the first time (worker `b431cbed`)
Both legs ran the SAME pack (sha256 `f142d6d6…`, identical WT↔base; `lib.sh` `b44d8b80…` identical both sides — correct,
B is a version bump on P). SKIP=1 on both legs = `P3-PIN-09-base` (base lib.sh copy not supplied). The 33 names,
identical on both legs (sorted sha256 `6550ee46043c0e0101eb85a8f9a469bdfa6eddbcc59bd9307667c651db03f6ee`):
B1 stop dispatched through the stub · B1h healed unit-path stop succeeds (rc 0) · B1h stop dispatched through the stub ·
B1h2 cites the failing-loud policy · B1h2 failing loud: STILL not confirmed stopped · B1h2 kill escalation dispatched via
stub · B1h2 stop dispatched via stub · C1 hand-back COMPLETE with new MainPID + prestop · C1 outcome surfaced · C1 unit
happy: rc 0 · C5 event carries declared×verified + outcome=degraded · C5 event detail: scope→unit lineage heal · C5
hand-back COMPLETE · C5 scope→unit self-heal: rc 0 · C5 supervision_handback journal event · C12a env-var pin: rc 0 ·
C12a hand-back COMPLETE on the pin · C12b .env pin: rc 0 · C12b absent pin: rc 0 · C12b invalid pin
(non-conforming): rc 0 · C12b missing-unit-file: rc 0 · C12b2 .env pin only (env stripped): rc 0 · M11 fence: the stub
PATH truly lacks lsof · M11 no lsof: gate skips attribution (rc 0, degraded-loud) · M11b exactly ONE skip warn across two
gates (one-time) · N1 hand-back halts fail-safe on the bootstrap failure · N1 the stub launchctl stderr is CAPTURED ·
N1 the WARN surfaces the capture · N2 hand-back fails fail-safe via the PRODUCTION $() consumption site · N2 the capture
rides the WARN captured: field · N2 the halt WARN carries the stub stderr (out-of-band, subshell-proof) · P3-PIN-09a the
hand-back announces the unit return · P3-PIN-09a the slow-boot hand-back COMPLETES inside the dedicated budget.

### 4.14 notes — P11 drift guard, BOTH shapes (worker `b021f8d7`) — the commission's shape-1 premise corrected
- The span base is `git rev-list --first-parent --merges -1 HEAD` → `span = {merge}..HEAD` (test source :1243-1247;
  zero `environ`/`getenv`/`argv` hits in the file ⇒ no override exists). The tree-level frontend pin uses the FIXED
  base `858a85e31…` over `frontend/`.
- **Shape 1 @ B is NOT vacuous.** "B..B empty" holds only when HEAD *is* the merge; B is a non-merge bump on top of
  merge `e68e26d25`, so the measured span is **`e68e26d25..B` = the 5-file bump delta (NON-EMPTY)**. The guard
  therefore judged the bump: the span diff over the six NOT_TOUCHED paths (`daemon/sources/cleanup.py`,
  `daemon/constants.py`, `daemon/manager.py`, `daemon/services/scheduling_service.py`, `daemon/routers/sources.py`,
  `frontend/`) and over `daemon/repositories/task/repository.py` is **EMPTY** — the bump touches none of them.
- **Shape 2 above B**: `--shared` clone under mktemp, empty commit `8192636f` ⇒ `B..HEAD` EMPTY (as required), same
  merge base `e68e26d25`, identical span content, identical result. (The clone was a plain clone — no worktree was
  registered: `git worktree list | grep -c p11-relegate` = 0.)
- The single red, both shapes, verbatim:
  `FAILED tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_frontend_ui_lane_drift_is_confined_to_the_allowlist_at_the_tree_level`
  — the pre-existing `frontend/` allowlist pin (68 offending hunks vs the fixed base). Its diff input
  (`git diff --unified=0 858a85e31..HEAD -- frontend/`) hashes to **`93dc0d2c797cd8e874933c12827f342c6ae0b74bfdf6db540f8fe23999df4dd8`**
  (458,929 bytes) in both shapes — identical to the predecessor's recorded value ⇒ base-attributed, net-new 0.

---

## 5. D3 — FENCES, SENTINELS, DEVIATIONS

### 5.1 Fence applied at run time on every leg (pack-native fences were NOT edited)
mktemp `FAKE_HOME` + `export HOME=` (never the ambient HOME; the literal mktemp path baked into each bash runner) ·
per-variable `unset` of `POSTGRES_DB/USER/PASSWORD/HOST/PORT/URL`, `PGHOST/PORT/USER/PASSWORD/DATABASE`,
`DATABASE_URL`, `ENSEMBLE_DB_DSN`, `ENSEMBLE_UPGRADE_SCRIPTS_DIR`, `ENSEMBLE_LAUNCHER_STATE_FILE` (ambient value
pointed into the live install), `PORT`, `DAEMON_PORT`, `OPENAI_API_KEY` — never the `env -u POSTGRES_*` no-op ·
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly (never `env -u`'d) · `ENSEMBLE_SELF_ENV=dev` ·
`PYTHONDONTWRITEBYTECODE=1` · `timeout 300` outer layer (pack-internal timers left enabled) · **no bare `env` anywhere**
· every git call `-c gc.auto=0` (+ `GIT_CONFIG_COUNT=1 / KEY_0=gc.auto / VALUE_0=0` so in-pack git subprocesses
inherit it) · the `por_*` packs UNRUN · no push / promote / tag / branch delete-rename / force / lock-file removal /
`rm -rf` outside mktemp · ports 8088 / 8079 / 9797 never bound or killed.

Fence proof printed inside every runner: `HOME=/tmp/…` · `POSTGRES_NAMES=0` · `BOOT_SWEEP=0 SELF_ENV=dev`
(P02/P14 also printed `OPENAI_API_KEY_set=0`, `PORT_set=0 DAEMON_PORT_set=0`).

### 5.2 Sentinels — byte-identical before AND after every leg
- `/home/nea/agents-ensemble/.launcher-state` → `mtime=2026-11-23 16:44:36.521921185 +0000 | size=96`
- `readlink -f /home/nea/agents-ensemble/current` → `/home/nea/agents-ensemble/releases/v0.31.52`
- WT `HEAD` = `667625a3a710727435375bf104da62ec45cbef16` (B) before and after every leg (independently re-confirmed
  after the last legs by `281d2879` ×3 and `b431cbed`).
- WT `git status --porcelain --untracked-files=all` EMPTY before and after every leg; no tracked file modified; no
  new worktree registered. `/home/nea/ensemble-src` and the three fix worktrees were never touched.
- Host ambient (names only, inventory `b4635710`): `ENSEMBLE_LAUNCHER_STATE_FILE` and `ENSEMBLE_UPGRADE_SCRIPTS_DIR`
  are set ambiently (the former under the live install — hence the explicit unset); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`
  and `ENSEMBLE_SELF_ENV` are set ambiently (overridden explicitly to 0 / dev); no `POSTGRES_*`/`PG*`/`DATABASE_URL`
  names in this shell's ambient.

### 5.3 Disclosed deviations (all in the safe direction; all voided where they mattered)
1. **Two first runs were VOIDED for a broken HOME fence and re-run fence-clean** — `c76f3909` (gate-legs): the runner's
   `export HOME="$FAKE_HOME"` saw an unexported parent variable ⇒ `HOME=` (empty); `43ccfb54` (twins): the first run
   inherited the ambient `HOME=/home/nea`. Both discarded the first result per the fence contract, fixed the runner
   (literal mktemp path) and re-ran; the fence-clean re-runs are the authoritative results above. The twins pack uses a
   scratch dir only (no HOME-derived install path — predecessor audit §10) and left porcelain empty / sentinels
   identical, but the breach is recorded, not hidden. All later briefs carried the explicit "literal path" warning.
2. **`c6612fe3` (bounded)**: the runner's trailing `echo PACK_EXIT=$?` made the outer `timeout` return 0; the pack's
   own rc (1) was captured losslessly inside. No re-run needed.
3. **Report-format notes**: none of the shell packs emits a literal `RESULT: PASS` token (each prints its own tally);
   verdicts above are the packs' own summary lines + rc, quoted verbatim — no `RESULT:` line was fabricated.
4. **`584ab0c7` (soak)**: one benign pack-internal stderr line (`tests/test_adaptive_soak.sh:587`, first write into a
   not-yet-created stub dir; the following write succeeds) — filed as a minor anomaly `9f9e9831`; the pack blob
   `009e34b1…` and `lib.sh` `fc2c0f66…` are byte-identical at P and B ⇒ pre-existing, not a regression.
5. **`584ab0c7` (soak) report transcription**: its report quotes HEAD as `…435375df104…` (one hex char off B). HEAD was
   independently confirmed = B by two later workers; a read-only re-check was dispatched to that worker (§7 item 3).
6. **`b021f8d7` (P11)** wrote one additive shared-meta-KV row (`cut.v0.31.58.regate.p11`) mirroring its report — not a
   repo mutation; disclosed.
7. **`c6612fe3` (bounded)** removed two of its own pack-litter files under `/tmp` (`bswaits-derive.KaWqMu.sh`,
   `bswaits-derive.out`); 15 pre-existing `/tmp/bswaits-derive.*.sh` from earlier lanes were left untouched.
8. **Skill attribution**: `b431cbed` reported "no skill matched" although dispatched with `load_skill=test-pack-execution`
   (ran DIY under the brief); the other 13 pack workers applied the skill. `c6612fe3` filed `skill_feedback` (8/10) and a
   `skill_fix` (pack-native summary vs `RESULT:` contract).

---

## 6. D4 — DOCS-ONLY DELTA STATEMENT

The only change on top of B made by this gate is **this file**
(`.agents/tester/RESULTS/2026-11-25-promote-handback-v0318-delta-regate.md`), committed on `latest` in the worktree with a
targeted `git add` (no push). The delta `B..HEAD` is therefore **docs-only — no code, no test, no payload path** — so
every D1/D2/D3 measurement taken at B carries unchanged to the tree that will be tagged `v0.31.58` and staged. The
commit sha is reported to the leader in the final report (it cannot be embedded in its own content). No PACKS.md fold-in
was made here (shared surface; PACKS.md is a separate maintenance step).

---

## 7. RESIDUAL RISKS & ROUTED FOLLOW-UPS

1. 🟡 **`supervision-33` is now enumerated here but still not in PACKS.md/QUARANTINE.md** — the registry rows carry only
   the set name + counts. Suggested: fold the 33 names (§4.10) into the quarantine ledger so future gates can do a
   registry-based name diff without a base leg. (PACKS.md maintenance; not done under this docs-only commission.)
2. 🟡 **Shape-1 vacuity is a property of HEAD being the merge, not of "the bump".** A gate at a non-merge HEAD (such as a
   bump commit) gets a NON-vacuous shape 1 (here it judged the bump delta and passed). Gate recipes should state the
   measured span explicitly rather than assume "HEAD..HEAD".
3. 🟡 **`584ab0c7`'s HEAD transcription discrepancy** — resolved in substance (HEAD = B re-confirmed by `281d2879` and
   `b431cbed` after that run; D4's commit worker hard-asserts HEAD = B before committing). The worker's own read-only
   re-check is in flight at the time of writing.
4. 🟡 The `frontend/` allowlist pin (P11's single red) is **not in QUARANTINE.md** — it lives in the tester RESULTS
   ledger + PACKS.md:372 (predecessor residual #5, unchanged).
5. 🟢 The L8b boundary flake (PACKS.md:1701) did not reproduce in 3 runs — consistent with the predecessor's 3/3; still
   registered as a disclosed flake, so a future single red there must still be re-run before being read as a regression.
6. 🟢 Pre-existing `/tmp/bswaits-derive.*.sh` litter (15 files) from earlier lanes — not this gate's; flagged for the
   house sweep.
7. 🟢 The shell packs' verdict is their own tally line, not a `RESULT:` token (disclosed; a gate that requires a RESULT
   line must read the tally + rc).

---

## 8. WORKER INSTANCES

| Leg | Worker | Skill |
|---|---|---|
| Inventory (paths, PACKS.md rows, registered sets, P11 recipe, sentinels, venv) | `b4635710` | none (read-only infra) |
| D1 delta identity + version assertion + runtime proof | `49d5cfae` | none (infra + scoped node run) |
| 4.1/4.1b repro matrix (HEAD + 3 bases) | `97e6c8c2` | test-pack-execution |
| 4.2 bounded waits | `c6612fe3` | test-pack-execution |
| 4.3 lint | `bc0cb1a1` | test-pack-execution |
| 4.4 journal row | `efa257bd` | test-pack-execution |
| 4.5 gate legs | `c76f3909` | test-pack-execution |
| 4.6 launcher | `57afd548` | test-pack-execution |
| 4.7 twins | `43ccfb54` | test-pack-execution |
| 4.8 wallclock | `491ea7fd` | test-pack-execution |
| 4.9 hbk churn | `b415fadb` | test-pack-execution |
| 4.10/4.10b stop-handback dual leg | `b431cbed` | (dispatched test-pack-execution; reported DIY) |
| 4.11 serving green | `a9fbf5f2` | test-pack-execution |
| 4.12 budget ×3 | `281d2879` | test-pack-execution |
| 4.13 soak (shipped cap) | `584ab0c7` | test-pack-execution |
| 4.14 P11 both shapes | `b021f8d7` | test-pack-execution |
| D4 docs commit | (see final report) | none (git infra) |
