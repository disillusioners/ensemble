# 2026-10-12 — Branch moved mid-phase + worktree venv traps (install-dir-protection test phase)

## Context
TEST phase for `feature/install-dir-protection` (9 workers). Dispatch named HEAD `f0c394388`; a review-round-3 commit `ffb4acd6a` landed on the branch WHILE the phase was running. Separately, two environment traps recurred across workers.

## Lesson 1 — Branch can move under an in-flight test phase; absorb, don't restart
- Symptom: W8's before-proof showed HEAD `ffb4acd6a` while W1/W2/W3 had all verified `f0c394388` minutes earlier.
- Protocol that worked (generalize):
  1. Every worker verifies + REPORTS HEAD at start (already standard).
  2. When a later worker reports a different HEAD: confirm ancestry (`git merge-base --is-ancestor <old> <new>`), diff the new commit's file list against the packs already run.
  3. If the new commit touches tested surfaces → re-run ONLY the affected packs at the tip (here: layer-1 packs, ~7s total, counts unchanged).
  4. If it touches untested-surface files (here: wrapper + runbook) → re-diff deployed-vs-repo artifacts and record drift (here: deployed wrapper was ALREADY redeployed — 0 executable-line delta).
  5. State the dual-commit evidence explicitly in the final report ("PASS @ both HEADs").
- Cheap insurance: keep pack runtimes small so tip re-verification costs seconds, not minutes.

## Lesson 2 — venv traps on this host (recurred 3× this phase)
1. **Editable-install shadowing**: `python -c "import daemon"` from a foreign cwd resolves `daemon/` via the sys.path cwd entry — ALWAYS `cd` into the target worktree before the import proof, else the proof lies (W8 caught this on the base-prove leg).
2. **uv not on default PATH**: use `/home/nea/.local/bin/uv` (W4/W8 both hit it).
3. Temp base-prove worktrees need their own `uv sync` — the feature worktree's `.venv` editable-installs to the FEATURE tree, silently wrong for base legs.

## Lesson 3 — "reasoned pre-existing" vs base-proven
W3's commit-scope reasoning ("branch touches only upgrade files") was correct but insufficient for the dispatch standard. The cheap closure: re-run ONLY the failing files at the base commit in a temp worktree (17 files, ~4 min) — turned "looks pre-existing" into "82≡82 identical, zero branch-new". For tools-side reds, an exact node-ID match against standing QUARANTINE rows is equally acceptable evidence (deterministic families) and costs zero runs.

## Facts worth remembering (host armor)
- L3 snapshot restore of the full install tree (5,358 files, hardlink-backed): **~3s** via `cp -a` — the plan's "~2 min" claim was conservative; incident baseline was ~2h manual rebuild.
- Treecheck is nea-runnable (`logger`-only writes): `$1` path arg or `ENSEMBLE_TREECHECK_PATH`; absent path → CRIT + exit 2 within seconds.
- Snapshot dir mtimes are rsync-preserved (all equal) — `ls -1t` picks the lexically-FIRST snapshot; use `ls -1d snap-* | sort | tail -1` for newest (retention script already sorts correctly).
