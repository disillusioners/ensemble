# 2026-09-30 — Mid-gate external tree mutation: evidence-pinning playbook (supervisor-war-r3 gate)

## Event
During the report-only gate of `3ad48b9a` (supervisor war r3), an **external actor** mutated the shared checkout mid-gate: at 19:31:02.142Z a third-state partial unwind was staged across `scripts/upgrade/lib.sh` + `tests/test_supervision_classify.sh` + `tests/test_supervision_stop_handback.sh` (−182/+6; matches neither `3ad48b9a` nor `fc285a27`), accompanied by a foreign rebase→amend→reset reflog cycle, a parked `stash@{0}`, and a foreign process running `test_release_journal.sh` in the same checkout. No gate worker caused it (all report-only, verified via status snapshots).

## What saved the gate (reuse this pattern)
1. **Snapshot git state at worker start** — hygiene census at 19:25–19:30Z proved the tree was clean when suites ran.
2. **Artifact mtimes order events** — every suite output file carried an mtime proving completion BEFORE the mutation window. Cheap, decisive.
3. **Blob-hash pinning beats HEAD labels** — `sha256sum <file>` vs `git show <commit>:<file> | sha256sum` for every at-risk leg. The simulation worker sourced `git show 3ad48b9a:scripts/upgrade/lib.sh` extracts instead of the working tree and hash-matched both legs (fix `4af0bdfe…`, base `de6ba7fe…`, tree `f3d2c883…` = third state).
4. **Detached worktrees are immune** to main-checkout index churn — base legs stay valid by construction.
5. **Never touch a foreign in-flight index** — report-only means observe + disclose + adjudicate; unstaging/restoring someone else's staged work would destroy their state and violate the gate.

## Rule of thumb
Any gate on a **shared checkout** must assume concurrent mutation: pin content by blob hash, timestamp every leg, and treat "HEAD says X" as a claim, not evidence. If a mid-gate mutation is detected, spawn a read-only forensic leg (status/stash/reflog + mtimes + hashes) before trusting any in-flight worker's result.

## Recurrence note (env-poison family, PB-F1)
5 of 11 gate workers independently flagged inherited ambient `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`, `ENSEMBLE_RESTART_UNIT=ensemble-main.service`, and live `POSTGRES_*` credentials (incl. real password) in their shells. All supervision packs stayed hermetic (fixture-owned env; no live contact; live-install mtimes verified untouched) — but every worker dispatch on this host should keep requiring ambient-env disclosure, and any pack that boots a daemon must harden with `export ENSEMBLE_SELF_ENV=dev` per the 2026-09-30 LESSONS recurrence.
