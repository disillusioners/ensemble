# rogue-containment-p2 tester gate — Gate 1a: BF+HY mutation matrix RE-RUN evidence (2026-11-15)

Commission: re-run EVERY named counter-mutant row of
`tests/bind_race_rogue_containment_mutation_matrix.md` (BF-M1..M9, HY-M1..M4)
at branch tip `a7aec8582`. Executor: worker (test-pack-execution lane).
**RESULT: PASS — 13/13 mutants RED · 13/13 restores sha256-verified · 13/13 green
controls at recorded baselines · ZERO spawn-seam hits · ZERO real spawns in any
of 27 fenced runs.**

## PROVENANCE — venue reconstruction + incident disclosure (read first)

1. The commissioned worktree `/home/nea/ensemble-src-wt-rogue-contain` was
   **deleted by an external actor mid-turn** (existed 2026-11-15T00:47:57Z —
   HEAD verified `a7aec8582`, matrix file read from it; gone by 00:49Z; the
   stale git worktree admin entry was pruned by another actor minutes later).
   Three foreign UNCOMMITTED files died with the dir (sibling-lane property,
   not touched or resurrected by this lane). Branch tip `a7aec8582` and drift
   pins (`origin/latest` = `bf4bfde6e`, `v0.30.11` = `29f368431`) verified
   UNMOVED. Anomaly report `f6493f80` (cluster `ad9184ac`, 4 reports).
2. Per test-leader incident guidance (effective ~00:52Z): NO worktree
   re-creation at the claimed path; **NO branch writes** (finalize owns the
   tip). This evidence is therefore delivered as a **/tmp rescue pack**
   (this file + raw leg logs + ready-to-apply commit command in
   `APPLY-COMMAND.sh`). The commit command was NOT executed.
3. Venue: built as a REAL detached git worktree (the matrix's own recorded
   venue method, and the prior-lane lesson that `git archive` exports make
   checkout-based mutant restore silently no-op):
   `git worktree add --detach /tmp/rc-p2-bfhy-g1a a7aec8582` + `uv sync
   --frozen` inside the venue. Zero ref mutations; repo untouched.
4. **Tip disclosure:** the matrix's BF+HY section recorded execution at pin
   `26acaf702`; this re-run executes at the CURRENT lane tip `a7aec8582`
   (gate-time truth). Census: `proc_tools.py` / `factory.py` / `cli.py`
   digests are byte-identical to the matrix's recorded census;
   `ensemble_config.py` and `bash.py` MOVED (digests below are this venue's
   own PRE values).

## Venue discipline (as executed)

- Venue: `/tmp/rc-p2-bfhy-g1a`, detached @ `a7aec8582`, `uv sync --frozen`
  (Python 3.14.7).
- Module identity proven before ANY run:
  `<venue>/.venv/bin/python -c "import daemon, tests; print(daemon.__file__)"`
  ⇒ `/tmp/rc-p2-bfhy-g1a/daemon/__init__.py` and
  `/tmp/rc-p2-bfhy-g1a/tests/__init__.py` (inside the venue).
- **Env fence (superset of the recorded per-variable scrub):** each run used
  a minimal clean environment (PATH/HOME/LANG/TMPDIR/TERM only) + fence vars
  — this kills every ambient identity poison present in the executor shell:
  `ENSEMBLE_SELF_ENV=live`, the 7 ambient `ENSEMBLE_AUTH_*` vars (incl.
  issuer), `ENSEMBLE_LAUNCHER_STATE_FILE`, `ENSEMBLE_UPGRADE_SCRIPTS_DIR`,
  `ENSEMBLE_PROMPT_ASSENT_*`, `ENSEMBLE_SKILL_*` — and trivially the whole
  POSTGRES_*/PG*/DATABASE_URL family (none ambient). Deviation from the
  recorded recipe: recorded recipe scrubbed per-variable; this run's
  clean-env fence is strictly stronger (disclosed, deliberate — ambient
  `ENSEMBLE_SELF_ENV=live` was live in the executor shell and the recorded
  recipe does not name it).
- Fence vars set: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`,
  `ENSEMBLE_PORT_CLAIMS_DIR=/tmp/rc-p2-bfhy-g1a-claims`, `PYTHONPATH=<venue>`.
- Timeouts: pytest `--timeout=90` (commission's recorded inner value; the
  BF+HY venue section recorded 300/120 — the commission mandates the tighter
  pairing), leg-internal kill deadline 110s, outer shell wrapper
  `timeout 120`. No leg approached any cap (slowest pytest wall 4.00s;
  slowest leg wall 15.2s).
- Restore per row: `git -C <venue> restore <file>`; PRE==POST sha256 verified
  per row (table below). Tracked porcelain EMPTY at venue close; all five
  census digests byte-identical at close.

## Pre-mutation census (venue tip a7aec8582)

```
e276200e4d4643f0a8b3d961dd43045656a108964872fa41372e278b6e2ec47f  daemon/ensemble_config.py   (moved vs matrix census @26acaf702)
614210f346d4c807ea86e1cc86ec2a1de17af4f7e388e28849eec5cef6852769  daemon/tools/bash.py        (moved vs matrix census)
dc57fdf28c6f543efefc65246937433b13c91ca790568a35d454714b5a8c815f  daemon/tools/proc_tools.py  (== matrix census)
28919557fa6376ffe05644576279b0e4efd272ede2c0bb790be304b5d4191490  daemon/repositories/factory.py (== matrix census)
bbf2170d1be61ce32a27cfc8a74f7fbbcd5bc8c9f51f2b850398360e62ba7361  daemon/auth/cli.py          (== matrix census)
```

## Clean-tree control (non-vacuity)

All four pin files unmutated: **73 passed, 1 warning in 4.00s**
(`test_prod_dsn_fence.py` + `test_db_resolution_guard.py` + 
`test_pkill_overlay_guard.py` + `test_pkill_overlay_pins.py`) — EXACTLY the
matrix's recorded 73-node baseline at `26acaf702`. Watcher: 0 children.
Baseline for green controls: BF rows 44 passed, HY rows 29 passed.

## Per-mutant table

Red-set invocations: BF rows run `tests/unit/test_prod_dsn_fence.py
tests/unit/test_db_resolution_guard.py` (both files, control pins exposed to
the mutant); HY rows run `tests/unit/test_pkill_overlay_guard.py
tests/unit/test_pkill_overlay_pins.py`. Serial, `-q --tb=short -p
no:cacheprovider`. Mutants applied by exact-string replacement with
occurrence==1 assertions (prior-lane no-op lesson); `git diff` verified per
leg; mutant actually applied BEFORE any run.

| Row | File (PRE sha = POST sha) | Mutant (as applied) | MUT sha | Observed RED | vs matrix recorded | Restore | Green after | Recorder/watcher |
|---|---|---|---|---|---|---|---|---|
| BF-M1 | ensemble_config.py (`e276200e…`) | `if resolved_postgres_db_name(ensemble_config) == "ensemble_prod":` → `if False:` | `26123c92…` | **14 failed, 30 passed**: P1[sandbox], P1[dev], P1[demo], P3, P4[explicit-explicit], P4[auto-derived], P4b, P5, P6, P7, `test_prod_dsn_predicate_strings_present`, `test_tier1_flip_side_by_side`, `test_cli_resolve_engine_refuses_and_writes_no_artifact`, `test_boot_preflight_refuses_prod_dsn_pre_tcp` | **14 = 14 EXACT** (same node set incl. strings-pin collateral) | TRUE | 44 passed | 0 children |
| BF-M2 | ensemble_config.py | `if marker == "live":` → `if False:` | `562e1744…` | **3 failed**: P4[explicit-explicit], P4[auto-derived], P4b | **3 = 3 EXACT** | TRUE | 44 passed | 0 children |
| BF-M3 | ensemble_config.py | `return os.environ.get("POSTGRES_DB", config.postgres.db).strip().lower()` → `return config.postgres.db.strip().lower()` | `4c060b3a…` | **4 failed**: P3, P4b, P7, `test_env_wins_positive_control_scratch_name` | **4 = 4 EXACT** | TRUE | 44 passed | 0 children |
| BF-M4 | ensemble_config.py | ALLOW short-circuit inserted at top of prod-DSN gate: `if os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW") == "1": return` after the guard's `try:` | `1d546fcb…` | **2 failed**: P7, `test_prod_dsn_predicate_strings_present` (forbidden read-shape) | **2 = 2 EXACT** | TRUE | 44 passed | 0 children |
| BF-M5 | auth/cli.py (`bbf2170d…`) | `assert_prod_dsn_sanctioned(ensemble_config)` call deleted from `resolve_engine` | `f173781b…` | **2 failed**: `test_cli_resolve_engine_call_site_ordering`, `test_cli_resolve_engine_refuses_and_writes_no_artifact` | **2 = 2 EXACT** | TRUE | 44 passed | 0 children |
| BF-M6 | ensemble_config.py | prod-dsn guard's fail-closed handler tail `_sys.exit(78)` → `return` | `c03b35f5…` | **1 failed**: P6 `test_p6_identity_resolver_raise_fails_closed_exit_78` | **1 = 1 EXACT** | TRUE | 44 passed | 0 children |
| BF-M7 | repositories/factory.py (`28919557…`) | `db = os.environ.get("POSTGRES_DB", pg.db)` → `db = pg.db` (the strings pin's NAMED counter-mutant) | `6aaeed79…` | **1 failed**: `test_prod_dsn_predicate_strings_present` | **1 = 1 EXACT** | TRUE | 44 passed | 0 children |
| BF-M8 | ensemble_config.py | unconditional `return` inserted immediately after the is_postgres early-return (guard dead for postgres shapes; predicate literal stays in source) | `2520c5e5…` | **13 failed**: P1×3, P3, P4×2, P4b, P5, P6, P7, `test_tier1_flip_side_by_side`, CLI refuse, boot preflight — **strings pin stays green as recorded** | **13 = 13 EXACT** | TRUE | 44 passed | 0 children |
| BF-M9 | ensemble_config.py | TWO-SITE: `if marker == "live":` → `if marker == "live" and False:` AND `if marker in ("dev", "demo", "sandbox"):` → `if marker in ("dev", "demo", "sandbox", "live"):` | `eafaa354…` (both sites applied+verified) | **3 failed**: P4[explicit-explicit], P4[auto-derived], P4b — the false-live-refusal catastrophe shape | **3 = 3 EXACT** | TRUE | 44 passed | 0 children |
| HY-M1 | tools/bash.py (`614210f3…`) | demo-port tuple entry REMOVED from the bash port-token loop (`("7979", r"\b7979\b"),` line deleted) | `593d981e…` | **5 failed, 24 passed**: `test_7979_token_refused_unfenced_HY1`, `test_7979_allowed_with_ambient_fence_HY1`, `test_port_family_refused_unfenced`, `test_overlay_fence_does_not_allow_live_pattern_B6`, `test_list_form` | **5 = 5 EXACT** (same node set) | TRUE | 29 passed | 0 children; refusal gate held (no spawn surface bypassed) |
| HY-M2 | tools/proc_tools.py (`dc57fdf2…`) | delegation SEVERED: function-local `from daemon.tools.bash import _pkill_overlay_violation` + delegated call in `create_proc_tools` → `_proc_pkill_violation = None` (except-loud block kept) | `2d6cfce4…` | **2 failed + 1 error, 27 passed**: `test_proc_lane_delegates_to_bash_predicate_HY2` (structural: import absent from factory source), `test_proc_refusal_text_stays_ambient_only_HY4` (behavioral: refusal absent, tool "succeeded" with the RECORDER STUB pid `pid=2000001`), + ERROR at teardown of the HY4 pin: `STRUCTURAL SPAWN GUARD tripped … recorder captured 1 spawn attempt(s) … [{'argv': ['pkill -f <live-name>'], 'cwd': None}]. NOTHING was executed` | **2 + recorder-error = recorded shape EXACT** (matrix spawn-safe re-proof: same 2 nodes, same stub pid, same teardown proof) | TRUE | 29 passed | **Recorder: 1 spawn attempt CAPTURED, ZERO executed** (argv redacted paste-safe); harness watcher 0 children — nothing reached the OS |
| HY-M3 | tools/bash.py | dead env-overlay advertisement RESTORED in the bash refusal string (`in the daemon environment` → `in the tool env (env={"ENSEMBLE_FENCED_TEST_CONTEXT": "1"})`) | `673404fc…` | **1 failed**: `test_bash_refusal_text_names_ambient_channel_only_HY3` (forbidden-phrase assert) | **1 = 1 EXACT** | TRUE | 29 passed | 0 children |
| HY-M4 | tools/proc_tools.py | overlay remedy ADDED to the proc refusal text (same advertisement) | `3e7e500e…` | **1 failed**: `test_proc_refusal_text_stays_ambient_only_HY4` (forbidden-phrase assert) | **1 = 1 EXACT** | TRUE | 29 passed | 0 children |

**Tally: 13/13 mutants RED · 13/13 restores sha256-verified PRE==POST ·
13/13 green controls at recorded baselines (BF 44 passed / HY 29 passed) ·
clean control 73 passed · ZERO rows failed to redden · ZERO spawn-seam hits ·
exceptions: none.** Every red set matches the matrix's recorded node set
EXACTLY — no drift, no extra collateral, no missing reds at tip `a7aec8582`.

## Spawn-seam contract compliance (normative §306-338)

- **Seam presence verified at this tip** (contract rule 1 — seam in the pin
  files, not the harness): `_spawn_seam_recorder` autouse fixture present in
  BOTH `tests/unit/test_pkill_overlay_guard.py` (:38) and
  `tests/unit/test_pkill_overlay_pins.py` (:53) at `a7aec8582`.
- **HY-M2 zero-exec proof rides the red** (contract rule 3): the bypassing
  mutant yielded RED pins whose teardown names the captured argv (redacted
  here paste-safe per the matrix's own discipline) and states NOTHING was
  executed; the stub pid `2000001` in the tool's success text is the
  recorder's inert stub, not a process.
- **Harness-level belt-and-braces:** every one of the 27 fenced pytest runs
  (1 clean control + 13 red + 13 green) ran under a /proc-based child-process
  watcher (observe-only, polls `/proc/<pytest-pid>/task/<pid>/children` at
  150ms): **27/27 `OBSERVED_CHILDREN=0`** — no process was ever created by
  any run, mutant or clean. Best-effort by construction (direct children
  only); the structural recorder remains the normative proof.
- **Row validity** (contract rule 4): HY-M2's red evidence shows BOTH the
  refusal-gate failures AND the recorder proving zero executed processes. ✓
- Live-trio identity: read-only sanity at close — the 9798 binder (pid
  305455, the POK-recorded identity) still alive; this lane's watcher +
  recorder prove it was never signaled. No ports touched; no kill commands
  issued by this lane anywhere at any time.

## Deviations & disclosures (all deliberate, none silent)

1. **Tip:** re-run at `a7aec8582` (gate-time tip), not the historical pin
   `26acaf702`. Two of five census files moved since the matrix recording;
   all 13 red sets still match EXACTLY.
2. **Env fence strengthening:** clean-env fence instead of the recorded
   per-variable `-u` list (strict superset; the ambient `ENSEMBLE_SELF_ENV=live`
   + 7 `ENSEMBLE_AUTH_*` poisons in the executor shell are killed by it).
3. **Timeout pairing:** commission-mandated `timeout 120` outer +
   `--timeout=90` pytest inner (the BF+HY venue section recorded 300/120).
   No leg approached any cap; behavior identical.
4. **Evidence destination changed by leader guidance** (worktree deleted
   mid-turn; branch writes forbidden mid-finalize): this file + raw logs +
   `APPLY-COMMAND.sh` live in a /tmp rescue pack; commit NOT executed.
5. **Harness aborts (clean, pre-application, venue untouched):** first
   BF-M5 attempt (applier's post-replace assertion invalid for
   deletion-mutants — fixed), two BF-M8 attempts (anchor lacked the em-dash
   line suffix — fixed with the exact line), first HY-M1 attempt
   (post-write count check invalid for pure deletion — fixed). Each abort
   was the applier REFUSING to write; no mutant application was skipped or
   assumed. BF-M4's anchor matched as a line-prefix (em-dash suffix
   preserved byte-identical) — applied file verified behaviorally (2 = 2
   exact reds).
6. **Redaction:** the one captured argv literal is redacted
   (`pkill -f <live-name>`) — paste-safe per the matrix's incident
   discipline; this document stays paste-safe.

## Recorded cleanup addendum (POK rogue-cleanup-rider-recorded-addendum)

Non-commit rider cleanup of THIS lane's /tmp scratch, recorded at execution
time immediately before removal:

| Removed path | Note | Size at removal |
| --- | --- | --- |
| `/tmp/rc-p2-bfhy-g1a/` | mutation venue (detached worktree @ a7aec8582, .venv from uv sync) | **559,018,827 B** (~533 MB, mostly .venv) |
| `/tmp/rc-p2-bfhy-g1a-claims/` | port-claims dir for fenced runs (empty) | **0 B** |
| `/tmp/rc-p2-bfhy-g1a-harness/` | applier + leg runner + 27 leg logs (copied into the rescue pack first) | **88,687 B** |

Reclaimed floor: **~533 MB**. Raw-log paste-safety: the single log carrying
the live-name argv literal (`logs/hy-m2-red.log`, 3 occurrences) is scrubbed
to `pkill -f <live-name>` in the rescue-pack copy before this sweep.

Kept: `/tmp/rogue-p2-g1a-evidence-rescue-<ts>/` (the rescue pack — evidence
survives the scratch sweep). Live-claims spared: nothing under /tmp touching
9797/9798/7979 was created, read, or removed by this lane.

## Ready-to-apply commit command (NOT executed — finalize owns the branch tip)

```bash
# To be applied by the finalize owner AFTER the pending merge window, on a
# worktree holding feature/rogue-port-containment (tip must still contain
# a7aec8582); place this file at the shown path first:
git add .agents/tester/RESULTS/2026-11-15-rogue-p2-mutation-bfhy.md && git commit --only -m "test: rogue-p2 gate 1a — BF+HY mutation matrix re-run evidence" -- .agents/tester/RESULTS/2026-11-15-rogue-p2-mutation-bfhy.md
```
