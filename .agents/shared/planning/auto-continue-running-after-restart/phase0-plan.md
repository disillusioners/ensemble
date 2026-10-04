# Phase 0: Dedicated worktree + fresh uv venv + import-resolution gate (Δ7 / D20 — MUST precondition)

## Phase 0.5 (r2): Citation-drift grep-verify sweep (item 15, reviewer-mandated)

This phase is ADDITIVE — it does NOT renumber the existing P0 tasks (0.1, 0.2, 0.3, 0.4). It runs ONCE at the start of the implementation lane, BEFORE any P1 work begins, and its deliverable is a verified-against-live-tree citation set in every plan file. The reviewer's r2 fold (item 15) is the source of the corrections; the live tree at `4f50c34d` wins any disagreement.

**Checklist (one row per citation, ALL must be grep-verified + corrected in the plan files):**

| # | Plan citation (reviewer's correction) | Live-tree check (grep-verify at `4f50c34d`) | Pinned value | Status |
|---|----------------------------------------|------------------------------------------------|-------------|--------|
| a | `job_feedback_observer.py:316` → `:110` | `grep -n "_TERMINAL_INSTANCE_STATUSES\b" daemon/services/job_feedback_observer.py` | `:110` (the frozenset definition) | ✅ verified |
| b | `models.py:284` → `task/models.py:287` | `grep -n "last_heartbeat_at" daemon/repositories/task/models.py` | `:284` (the field def) — reviewer said `:287` but the live tree says `:284`; per "tree wins" rule, pin **`:284`**, and qualify the path as `daemon/repositories/task/models.py` (the `task/` prefix is the reviewer's correction; the line number stays `:284` per tree) | ⚠️ disagreement, tree wins: pin `daemon/repositories/task/models.py:284` |
| c | `daemon/instance_messaging.py` → `daemon/services/instance_messaging.py` (6+ refs) | `ls daemon/instance_messaging.py 2>/dev/null`; bare `daemon/instance_messaging.py` does NOT exist (only `daemon/services/instance_messaging.py` does) | `daemon/services/instance_messaging.py` everywhere | ✅ verified — fix 6+ active-plan-file references (see Phase 0.5.1 below) |
| d | `worker_pool.py:61-97` → `:60-152` | `grep -n "class TaskHeartbeat\|def _run\|update_heartbeat" daemon/services/worker_pool.py` | `:60-152` (class def through `_run` body that calls `update_heartbeat`) | ✅ verified |
| e | `execution_gate.py:108-144` → `:108-155` | `grep -n "_locks\b\|_lock_for\|is_held" daemon/services/execution_gate.py` | `:108-155` (`_locks` decl at :108, second live spot in `is_held` at :155) | ✅ verified |
| f | claim-guard span `:2230-2294` (3 live spots) vs D23's `:2230-2310` | `grep -n "instance_id NOT IN" daemon/repositories/task/repository.py` | `:2230-2294` (first `instance_id NOT IN` block; SELECT subquery closes at `:2293-2294`) — the `:2230-2310` in D23 includes the S3-PAUSED carve-out comment block that continues past the guard; **reconcile D23 to `:2230-2294` for the guard itself** (the `:2310` is a comment, not the guard) | ⚠️ partial disagreement: guard closes at `:2294`; D23's `:2230-2310` is the broader comment range. Pin guard as `:2230-2294`; update D23 to clarify |
| g | T2.8 except-clause `:11688-11697` → `:11698` | `sed -n '11690,11705p' daemon/manager.py` | The actual `except Exception as e:` handler starts at `:11699`; the comment block that the plan called the "exception handler at `:11688-11697`" is actually the END of the resume-success comment block (`:11665-11697`). The actual `except` is at `:11699`. **Pin: insert terminalizer BEFORE line `:11699` (i.e. the line just before the except)**. Reviewer's `:11698` is the blank line between the comment and the except — correct location | ✅ verified — use `:11698` (blank line before except) as the "before the exception handler" anchor |
| h | phase0 `pyproject.toml:49-53` → `:62` | `grep -n "dependency-groups" pyproject.toml` | `:62` (the `[dependency-groups]` section header) | ✅ verified |
| i | "10 min" STR threshold = CONFIG default (`config.py:1271`) vs CODE default 15 min (`stale_task_recovery.py:25`) | `grep -n "stale_task_recovery_threshold_minutes\|DEFAULT_STALE_THRESHOLD_MINUTES" daemon/config.py daemon/services/stale_task_recovery.py` | CONFIG default = 10 min (`daemon/config.py:1271`); CODE default = 15 min (`daemon/services/stale_task_recovery.py:25`). **Knob shift rescales D24's reap-window reasoning**: a config flip changes the effective threshold; the CODE default applies only if the config is unset. Correct D24/row-5 text to name BOTH values and which applies by default (CONFIG = 10 min wins) | ✅ verified — update D24 + R20 to name both |
| j | `_task_repo` not-None assertion (add to T2.2 where the repo handle is acquired) | `grep -n "_task_repo" daemon/manager.py | head` | The T2.2 step "(3) `task_repo = manager._task_repo`" currently has NO None check. **Add an assertion** (defensive — `manager._task_repo` MUST be wired by `initialize()` before boot subsystems run; the assertion catches a misordered init regression) | ✅ verified — add to T2.2 |
| k | R4 → D24 pointer fix in risk-register | (cross-ref check) | R4 says "no code coupling; M12 documents the boundary" — but the actual escalation path is D24 (no-heartbeat window + STR reap at boot+10 min) for long turns, NOT R4. **Fix: R4 should point to D24 for the long-turn case** | ✅ verified — update R4 cross-ref |
| l | interleaving Row-4 ↔ M14 pairing cross-ref (phase3/test-strategy) | (cross-ref check) | Row 4 of M13 is the Δ1 success-path test; M14 row 2 is the Δ1 complement regression test. They pair (row 4 = "Δ1 fires"; M14 row 2 = "Δ1 removed → test passes"). **Add explicit cross-refs** in phase3 T3.2 (Row 4) and test-strategy M14 (Row 2) | ✅ verified — add cross-refs |
| m | AC9 → M10 cross-ref | (cross-ref check) | AC9 = kill-switch; M10 = kill-switch test. The plan-overview AC traceability has "AC9→P2 (T2.7)" but no M10 cross-ref. **Add M10 cross-ref to plan-overview AC traceability** | ✅ verified — add cross-ref |
| n | T4.6 exact grep pin (spell out the literal grep command) | (T4.6 = cascade e2e; the literal grep is the pack-name discovery) | T4.6 says "pick the current canonical cascade e2e pack from `test/packs/` by grepping `child_parent_lifecycle` / `completion_regression` at execution time" — spell out: `grep -rl 'child_parent_lifecycle\|completion_regression' test/packs/*.sh | head -1` | ✅ verified — add literal grep to T4.6 |
| o | `_has_checkpoint` full-blob `aget` cost note (P5 scale observation) | `grep -n "self._checkpointer.aget" daemon/services/instance_messaging.py` | `_has_checkpoint` at `:1438-1450` calls `self._checkpointer.aget(config)` (full state blob) just to get a bool; the per-call cost is the full blob fetch + Python `len(messages)`. At N=100+ candidates this is a latency concern. **P5 scale observation: log a one-line `[BOOT_CONTINUE] instance=<id8> checkpoint_aget_duration_ms=<N>` line per candidate and observe the distribution in the evidence file**; if P95 > 200ms, follow-up commission investigates an `is_fresh`-only check | ✅ verified — add P5 observation step |

### Phase 0.5.1 — citation corrections to apply to plan files

After grep-verifying all 15 citations above, the following corrections must be applied to the ACTIVE plan files (NOT to `investigation.md` / `technical-analysis.md` / `architecture-recommendation.md` — those are historical inputs, cite-where-superseded only):

1. `daemon/instance_messaging.py` → `daemon/services/instance_messaging.py` (6+ refs in plan-overview.md, decisions.md, phase2-plan.md, test-strategy.md, risk-register.md)
2. `models.py:284` → `daemon/repositories/task/models.py:284` (qualify the path; the line number stays per tree) — affects decisions.md D21
3. `worker_pool.py:61-97` → `daemon/services/worker_pool.py:60-152` — affects decisions.md D24
4. `execution_gate.py:108-144` → `daemon/services/execution_gate.py:108-155` — affects plan-overview.md, decisions.md
5. `claim_pending_task :2230-2310` → reconcile to `:2230-2294` for the guard span; update D23 to clarify
6. T2.8 except-clause `:11688-11697` → `:11699` (or `:11698` blank line) — affects phase2-plan.md T2.8
7. `pyproject.toml:49-53` → `:62` — affects phase0-plan.md
8. D24/R20 "10 min STR" → "CONFIG default 10 min (`daemon/config.py:1271`), CODE default 15 min (`daemon/services/stale_task_recovery.py:25`)" — affects decisions.md D24, risk-register.md R20
9. R4 → D24 cross-ref — affects risk-register.md R4
10. Add `_task_repo` not-None assertion to T2.2 — affects phase2-plan.md T2.2
11. Add interleaving Row-4 ↔ M14 cross-refs — affects phase3-plan.md T3.2 + test-strategy.md M14
12. Add AC9 → M10 cross-ref — affects plan-overview.md AC traceability
13. Add literal grep to T4.6 — affects phase4-plan.md T4.6
14. Add P5 `_has_checkpoint` aget-cost observation step — affects phase5-plan.md (task 5.12, NEW r2)
15. Add R24 loop-breaker risk row — affects risk-register.md (already done in item 9)

### Phase 0.5 exit criterion

Every row in the checklist above is grep-verified at `4f50c34d`, every disagreement is recorded (and the tree wins), every citation correction is applied to the active plan files, and the plan files read back with no stale citations. The P1 verification step (the worktree-gate re-check) is the implicit re-verification — any P1 commit that uses a citation must re-grep-verify it (P1 step 6, phase1-plan.md).

### Phase 0.5 r3b addendum — Git-object-only verification (WT contamination, 2026-10-04 ~06:22 UTC)

**Incident (dispatcher-verified):** the r3 fold (committed at `d1a1421`, 2026-10-04) grep-verified its "anchor corrections" against the **WORKING TREE** on the shared checkout — but a concurrent lane mutated the shared checkout's `daemon/` tree to an OLD-sha state (~06:22 UTC). The result: every "Live-tree anchor @ `cf95a1a5`" line in the r3 table was a **WT-derived drift snapshot**, NOT a committed-tree value. The r3b fold re-pins every anchor against the COMMITTED tree at `d1a1421` via `git grep` + `git show`.

**Verification method rule (r3b — supersedes r3's "verify at use time"):**

On the `feature/auto-continue-running-after-restart` branch, ALL execution-lane anchor verification MUST use git OBJECTS, NEVER the shared checkout's working tree, until the WT is adjudicated (see R25). Required verification command set:

| Verification type | Command |
|---|---|
| Existence + line number | `git grep -n '<pattern>' <rev> -- <path>` |
| Content + region | `git show <rev>:<path> \| sed -n 'X,Yp'` |
| File existence (negative verify, catches "X is NOT at path Y" claims) | `git ls-tree -r <rev> -- <path>/` |
| Multi-file caller sets | `git grep -rn '<pattern>' <rev> -- <scope>/` |

**Why the rule upgrade (r3b):** the r3 fold's "verify-at-use at the current HEAD" rule was correct in spirit but underspecified — a planner who reads "verify at HEAD" can verify against the WT, and the WT is a concurrent lane's state on this branch. The r3b rule pins the verification method (git objects only) and adds the negative-verify command for file-existence claims (catches the "X is NOT at path Y" claim that the r3 D27 fold asserted via WT inspection without `git ls-tree` confirmation).

**Checklist invariant (r3b — add to every future sweep on this branch):**

| # | Check | Pass criterion |
|---|-------|----------------|
| 1 | For every anchor cite in the plan file, the verifier command is one of the four above (not bare `grep` / `sed` on the WT) | All anchors carry a `git grep -n '...' <rev> -- <path>` or equivalent git-object-only verifier in the table |
| 2 | Every row's `<rev>` matches the current HEAD of the plan's target branch (or the documented snapshot SHA the row was pinned against) | `git rev-parse <rev>` returns a valid SHA; for "at HEAD" claims, `<rev>` = `HEAD`'s SHA at the time of writing |
| 3 | File-existence negative claims (e.g. "X is NOT at path Y") are backed by `git ls-tree` output, not by WT inspection | Every "not at" claim has a `git ls-tree -r <rev> -- <path>/` line in the verifier |
| 4 | Anchor-correction tables include BOTH the r2 cite AND the r3 fold's WT-derived "correction" alongside the d1a1421 verified value, so the r3b provenance chain is auditable | Every corrected row has 3 columns (r2 cite | r3 fold pin | r3b verified @ d1a1421), not just the r3b value alone |

**Exit criterion (r3b addendum):** all four checks above pass on every anchor cite in the r3b-revised plan files. The P1 verification step (worktree-gate re-check) is updated to: when running P1, re-run the r3b checklist on every anchor cite the P1 commit uses, with `<rev> = d1a1421` (or the current P1-branch HEAD if it has advanced past d1a1421 via a legitimate plan-touch commit, NOT via WT contamination).

**See also:** D29 r3b incident note (decisions.md) + R25 (risk-register.md) + test-strategy.md lane-drift rule (r3b — supersedes r3).

## Phase 0 (canonical, unchanged)

## Objective

The implementation lane and the demo-E2E lane run in a dedicated git worktree at `../ensemble-src-wt-auto-continue` with a fresh uv venv, gated by the import-resolution check (`python -c "import daemon; print(daemon.__file__)"` must resolve INSIDE the worktree). Failure to set this up = silent tests-on-wrong-tree (R21), ungrounded commits on the shared main checkout, and risk of cross-commission branch collision. **This phase MUST pass before any Phase 1 work begins.** P1-P5 all assume it has passed; existing P1-P5 task numbers and AC traceability are unchanged (P0 is additive — does not renumber existing refs).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 0.1 | Create the dedicated worktree: from the main repo at `feature/auto-continue-running-after-restart` @ `ce148ad2` (HEAD at the time this plan revision lands), `git worktree add ../ensemble-src-wt-auto-continue -b feature/auto-continue-running-after-restart`. Verify `git -C ../ensemble-src-wt-auto-continue rev-parse --abbrev-ref HEAD` = `feature/auto-continue-running-after-restart`; `git -C ../ensemble-src-wt-auto-continue log -1 --oneline` matches the parent HEAD. **DO NOT work on the main checkout from this point forward** (per `agents/developer/rule.md:164` — "Assigned wt_path? cd into the worktree before any git op; never commit on the main checkout") | none | Worktree exists at `../ensemble-src-wt-auto-continue`; tracked branch matches; `git -C ../ensemble-src-wt-auto-continue status` clean |
| 0.2 | Create a fresh uv venv INSIDE the worktree: `cd ../ensemble-src-wt-auto-continue && uv sync` (installs the `[dependency-groups].dev` group per `pyproject.toml:62` (r2-corrected; previous draft cited `:49-53` which is the pre-3.13 layout; the current PEP 735 dev group lives at `:62`); no pip; bare `uv sync` per the repo's PEP 735 dev-group convention). Verify: `../ensemble-src-wt-auto-continue/.venv/bin/python --version` = CPython 3.13.x (matches `.venv/pyvenv.cfg:4` → `version_info = 3.13`); `../ensemble-src-wt-auto-continue/.venv/bin/python -c "import daemon; print(daemon.__file__)"` prints a path that STARTS with `/home/nea/ensemble-src-wt-auto-continue/` (i.e. resolves INSIDE the worktree, NOT `/home/nea/ensemble-src/`). **If the import path resolves to the main checkout, the editable-install trap bit** (R21) — delete the worktree venv and re-create it; do NOT proceed with the wrong-tree venv (silent tests-on-wrong-tree = ungrounded green = ungrounded promote) | 0.1 | Import-resolution gate green: `python -c "import daemon; print(daemon.__file__)"` resolves inside the worktree; `pytest --collect-only` succeeds in the worktree's venv |
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