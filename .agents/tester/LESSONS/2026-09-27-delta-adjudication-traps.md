# Delta-adjudication traps: worktree sockets + parallel-load flakes (2026-09-27)

**Context:** maintenance-console base-delta (HEAD `3fb798d1` vs base `666c089d`). Raw node diff produced 3 "NEW-FAILURE" nodes; retry-budget adjudication (3×/3× both sides) reduced them to ZERO branch-caused. Two distinct traps:

## Trap 1 — Worktree-state contamination (lcancheck census)
`test_lcancheck_family_separation.py::TestS3WholeTreeCensus::test_s3_whole_tree_census` failed 3×/3× at HEAD, passed 3×/3× at base — *looks* deterministic-branch-caused. Root cause: the census `grep -rn` walks the working tree; a **live code-server Unix socket** (`data_dev/vscode-user-data/code-server-*`, created by local testing the same morning) makes grep exit rc=2 ("Operation not supported on socket") → RuntimeError at test:1014. The base leg ran in a FRESH detached worktree (no `data_dev`) → passed. The A/B asymmetry was **worktree dirt, not commit diff**.
- Fix follow-up (open): add `"data_dev"` to `_CENSUS_EXCLUDES` (test:140-148). Any test that greps the worktree must exclude runtime-state dirs (`.git`, `__pycache__`, … AND `data_dev`).
- Rule: an A/B "regression" in an unchanged test file with a *filesystem-walking* assertion → suspect worktree state before blaming the commit. Check for sockets/runtime dirs: `find . -type s -not -path './.git/*'`.

## Trap 2 — Parallel-load flakes masquerading as regressions
`test_skill_evolution_service.py::...force_resolve` (0.13s solo) and `test_mcp_tool_timeout.py::...tool_node_handles_timeout` (0.33s solo) failed exactly once each — inside full-shard runs executed under **12-way parallel pytest contention**. They pass 3×/3× solo at BOTH commits and in 20-file chunk context at base. Timing-sensitive tests (timeout handling, resolution racing) trip under machine saturation.
- Rule: when shards run in parallel, treat single-occurrence failures of *sub-second timing-sensitive* tests as load-flake candidates; adjudicate with node-scoped 3× runs on both sides BEFORE classifying as regression. Not quarantined (insufficient repeat evidence) — watch-list.
- Scheduling note: cap concurrent pytest shard workers (~≤6) or accept that ~1-2 sub-second timing tests per 23k may flake; the retry budget is the cheap countermeasure.

## Meta-lesson
The raw A/B diff (fail@HEAD ∧ pass@base = 3 nodes) OVERSTATED regressions 3→0. Always run the 3× retry-budget leg before reporting "new failures" — the retry budget is cheap (seconds for solo-fast tests) and it converts scary headlines into accurate ones.
