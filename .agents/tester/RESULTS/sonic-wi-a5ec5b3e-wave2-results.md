# Sonic Tester — wi-a5ec5b3e WAVE-2 independent verification (RE-DISPATCH #1)

**Verdict: TEST: PASS**

Worktree: /home/nea/ensemble-src-wt-wia5ec5b3e-int3 (branch sonic/wi-a5ec5b3e/integration-2)
Tip verified: c36c3e07c2f00947cd1257a7d1fc01190c56c376 == tag v0.31.5 == origin/latest
Round delta: 04b3d50ac..c36c3e07c (laundering closure + PACKS REV3 + verb widen + v0.31.5 bump + docs drift)
Interpreter: worktree .venv (CPython 3.14.7); per-variable POSTGRES_* pins; ENSEMBLE_SELF_ENV=dev
Clean guard sha: 37bbf0d93fea66ea55de63049ff87f06ba777c09a3f8a9ab47355860c81b666b

## 1. Full pack REV3 — 173/173 GREEN
`tests/unit/tools/test_systemctl_intent_guard.py` + `test_pkill_overlay_pins.py` + `test_pkill_overlay_guard.py` + `test_spawn_boundary_scrub.py`
collected 173; **173 passed, 1 warning, 0 failed** (2.41s). Log /tmp/sonic-p2-pack-head-run.txt.
REV3 registration cross-checked against MAIN-repo .agents/tester/PACKS.md row (134 in file, 173 in pack).

## 2. Mutation legs on the FINAL tip (throwaway detached worktree @ c36c3e07c; sha256 plant/restore)
Every restore byte-identical to clean 37bbf0d9…; porcelain clean (aside from untracked symlink).
- M1 (deny leg → old text scan): 58 F + 2 E, restore identical
- M2 (empty read-only allowlist): **24 F**, restore identical
- M3 (over-block → in-word text scan): 5 F, restore identical
- M4 (proc lane delegation neutered): **3 F + 3 E**, restore identical
- M5a (wrapper check removed): 28 F, restore identical
- M5b (marker detector removed): 7 F, restore identical
- M5c (wrapper + pipeline leg removed): 30 F, restore identical
- M6a (shape matcher neutralised): **12 F**, restore identical
- M6b (runner branches neutralised): **11 F**, restore identical
- M6c (xargs fail-closed disabled): **4 F**, restore identical
- M6d (over-broad SHAPE → read-only-through-pipe refused): **8 F**, restore identical
- M6e (over-broad RUNNERS → every payload refused): **9 F**, restore identical
- M6f (-c payload recursion disabled): **4 F**, restore identical

M6a–f match the developer's claimed 12/11/4/8/9/4 EXACTLY. M2 and M4 match exactly.
M1/M3/M5 counts differ from the developer's (79/20/31/8/37) because the developer's exact
plant bodies for those legs were not preserved in /tmp; my independently reconstructed plants
each produce a non-empty RED set with a byte-identical restore, which is the load-bearing
property (pins are non-vacuous under counter-mutation). Logs: /tmp/w2_m1m5-legs-*.txt, /tmp/w2_m6-legs-*.txt.

## 3. Base-vs-head failing-set (04b3d50ac vs c36c3e07c), 3× isolated each
Scoped files: tests/tools/test_bash_spawn_stamp.py + tests/tools/test_proc_secret_env.py
Both files BYTE-IDENTICAL base..head; only daemon/tools/bash.py differs (COMMENT-ONLY, +11/−2 docstring).
- HEAD (3 runs): 3 failed — identical set each run
- BASE (3 runs): 3 failed — identical set each run
- net-new reds = 0
Pre-existing ambient reds (identical both sides):
- tests/tools/test_bash_spawn_stamp.py::TestStampContract::test_explicit_off_byte_identical_env_no_row
- tests/tools/test_bash_spawn_stamp.py::TestWriterUnsetOrdering::test_instance_id_none_never_stamps
- tests/tools/test_proc_secret_env.py::TestProcZeroChange::test_no_overlay_byte_identical_to_p1
(Note: the two extra ids the developer's env showed — test_env_none_materializes_inherit_copy_plus_one,
test_stamp_present_in_child_env_when_on — are an ambient/load flake pair: they failed on my very first
head run under load and passed on all 3× isolated runs on BOTH sides. Test file is byte-identical
base..head, so they are not lane-attributable.) Logs: /tmp/w2_head_bashproc.txt, /tmp/w2_base_bashproc.txt.

## 4. Both-direction proof on the NEW laundering class
`-k "pipe_into_executor or xargs_stdin or runner_shapes_refused or scheduler_handoff or
laundering_deny_is_scoped or readonly_through_runner or bash_seam_refuses_laundering or
proc_lane_refuses_laundering or shape_matcher"` → **45 passed, 89 deselected** (/tmp/w2_bothdir.txt).
- REFUSED: 6 pipe-into-executor + 4 xargs-stdin + 10 runner (su/runuser/systemd-run/watch/ssh/bash -c) + 3 scheduler (at/batch/crontab) params — all assert a non-None violation.
- ALLOWED: 18 `_LAUNDER_ALLOW` legs (read-only through every runner + data-only payloads) — all assert None.

## Fence
Unit tests only; no real systemctl mutation. Throwaway worktrees in /tmp removed after use.
Frozen worktree left clean (git status empty).
