# Phase 0: Dedicated worktree + fresh uv venv + import-resolution gate (Δ7 / D20 — MUST precondition)

## Objective

The implementation lane and the demo-E2E lane run in a dedicated git worktree at `../ensemble-src-wt-auto-continue` with a fresh uv venv, gated by the import-resolution check (`python -c "import daemon; print(daemon.__file__)"` must resolve INSIDE the worktree). Failure to set this up = silent tests-on-wrong-tree (R21), ungrounded commits on the shared main checkout, and risk of cross-commission branch collision. **This phase MUST pass before any Phase 1 work begins.** P1-P5 all assume it has passed; existing P1-P5 task numbers and AC traceability are unchanged (P0 is additive — does not renumber existing refs).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 0.1 | Create the dedicated worktree: from the main repo at `feature/auto-continue-running-after-restart` @ `ce148ad2` (HEAD at the time this plan revision lands), `git worktree add ../ensemble-src-wt-auto-continue -b feature/auto-continue-running-after-restart`. Verify `git -C ../ensemble-src-wt-auto-continue rev-parse --abbrev-ref HEAD` = `feature/auto-continue-running-after-restart`; `git -C ../ensemble-src-wt-auto-continue log -1 --oneline` matches the parent HEAD. **DO NOT work on the main checkout from this point forward** (per `agents/developer/rule.md:164` — "Assigned wt_path? cd into the worktree before any git op; never commit on the main checkout") | none | Worktree exists at `../ensemble-src-wt-auto-continue`; tracked branch matches; `git -C ../ensemble-src-wt-auto-continue status` clean |
| 0.2 | Create a fresh uv venv INSIDE the worktree: `cd ../ensemble-src-wt-auto-continue && uv sync` (installs the `[dependency-groups].dev` group per `pyproject.toml:49-53`; no pip; bare `uv sync` per the repo's PEP 735 dev-group convention). Verify: `../ensemble-src-wt-auto-continue/.venv/bin/python --version` = CPython 3.13.x (matches `.venv/pyvenv.cfg:4` → `version_info = 3.13`); `../ensemble-src-wt-auto-continue/.venv/bin/python -c "import daemon; print(daemon.__file__)"` prints a path that STARTS with `/home/nea/ensemble-src-wt-auto-continue/` (i.e. resolves INSIDE the worktree, NOT `/home/nea/ensemble-src/`). **If the import path resolves to the main checkout, the editable-install trap bit** (R21) — delete the worktree venv and re-create it; do NOT proceed with the wrong-tree venv (silent tests-on-wrong-tree = ungrounded green = ungrounded promote) | 0.1 | Import-resolution gate green: `python -c "import daemon; print(daemon.__file__)"` resolves inside the worktree; `pytest --collect-only` succeeds in the worktree's venv |
| 0.3 | Document explicit env exports for any daemon run from the worktree (prod-defaults trap, `agents/developer/rule.md:164`): every shell that boots a daemon in the worktree MUST export `ENSEMBLE_SELF_ENV=dev` (or whichever lane target) explicitly — a daemon in the worktree hits prod defaults if not exported (the documented trap). DB pinning for the worktree daemon MUST override a `POSTGRES_*` **part** (e.g. `POSTGRES_PORT`), NOT `POSTGRES_URL` — the F-DR1-2 split-brain (`daemon/persistence.py:79-89` honors `POSTGRES_URL`; `daemon/repositories/factory.py:189-198` reads `POSTGRES_*` parts only); a part override moves BOTH, an URL override moves neither | 0.2 | An `ENSEMBLE_SELF_ENV=dev` export line + `POSTGRES_*` part-override line documented in the worktree's `RUNNING.md` (a brief notes file under the worktree's plan dir) so any developer/tester re-creating the lane finds it without re-deriving |
| 0.4 | Record the worktree path in the implementation leader's notes (and as a Phase 1 verification step): `git -C ../ensemble-src-wt-auto-continue worktree list --porcelain | grep -E '^worktree|^branch' > .agents/shared/planning/auto-continue-running-after-restart/worktree-claim.txt` — the file is the canonical "I am in the worktree" receipt; P1 verification step 6 greps for this file to confirm the lane is on the worktree before any Phase 1 commit lands | 0.1, 0.2, 0.3 | `.agents/shared/planning/auto-continue-running-after-restart/worktree-claim.txt` exists with the worktree path + branch; git status on the main checkout shows only the planning-dir changes (no implementation commits) |

## Coupling

- **Precondition for P1-P5** — every subsequent phase runs from `../ensemble-src-wt-auto-continue`. P1's verification step 6 grep-verifies the worktree-claim.txt; if absent, P1 fails the pre-flight and the developer returns to P0.
- **Independent of any code** — no daemon code touched; only repo/venv + a notes file. Phase 0 commits land BEFORE any Phase 1 commit on the same branch (commit order matters for the rollout: `P0-receipts → P1-schema → … → P5-E2E`).
- **Touches (none of the planned code):** this phase adds ZERO files to `daemon/`, `tests/`, or `daemon/migrations/` — only `.agents/shared/planning/auto-continue-running-after-restart/worktree-claim.txt` (planning artifact, not code).

## Verification Steps

1. `git -C ../ensemble-src-wt-auto-continue rev-parse --abbrev-ref HEAD` → `feature/auto-continue-running-after-restart` (or a successor branch — what matters is that the worktree is on the feature branch, NOT on `latest`/`master`).
2. `git -C ../ensemble-src-wt-auto-continue log -1 --oneline` → matches the parent branch HEAD or a later commit on the same feature branch.
3. `../ensemble-src-wt-auto-continue/.venv/bin/python -c "import daemon; print(daemon.__file__)"` → prints a path INSIDE the worktree.
4. `git -C ../ensemble-src-wt-auto-continue status` → clean (or only planning-dir changes staged).
5. `cat ../ensemble-src-wt-auto-continue/.agents/shared/planning/auto-continue-running-after-restart/worktree-claim.txt` → contains the worktree path and branch.
6. **Read-back verification (trap #2):** confirm the worktree-claim.txt file is the ONLY new artifact on the main checkout's `feature/auto-continue-running-after-restart` branch from Phase 0 (no other files staged on the shared checkout).

## AC Mapping

- **AC7** — P0 IS the worktree precondition that all execution-lane work must satisfy (R21 mitigation); M23 (test-strategy) pins the import-resolution check as a pytest fixture-level assertion.
- **AC9** — no DC, but the worktree gate is part of the operator's "feature lane" discipline.

## Exit Criterion

`../ensemble-src-wt-auto-continue` exists, has a fresh uv venv whose `import daemon` resolves inside it, and the worktree-claim.txt is present. The first Phase 1 commit (`models.py` + `migrations/.../*.sql` + `_ensure_postgres_columns` entry) MUST be authored from this worktree — verified by the branch HEAD-of-commit trail showing commits authored on `feature/auto-continue-running-after-restart` from the worktree's filesystem.

## Rollback Note

`git worktree remove ../ensemble-src-wt-auto-continue` reclaims the directory. The worktree-claim.txt is planning-dir evidence and rolls back by deletion. P0 leaves NO state on the main checkout beyond the planning-dir file (which can be deleted in the same commit that reverts P0).