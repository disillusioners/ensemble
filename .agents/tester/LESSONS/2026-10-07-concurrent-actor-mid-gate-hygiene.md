# Concurrent actor committing production mid-gate — evidence hygiene protocol

**Date:** 2026-10-07 (upgrade-resilience final gate)

## Event
A concurrent actor (giter/reviewer, stale claim heartbeat "READ-ONLY audit done" @16:40Z) wrote UNCOMMITTED production edits into the fix worktree mid-gate: `scripts/upgrade/lib.sh` (+232: `_resolve_wait_s_mirror`/`_derive_parent_stop_budget`/`_effective_stop_script_budget` — post-review hardening) + `tests/test_bounded_subprocess_waits.sh` (+309: D1-D8 derivation + sync-guard cases), committed ~80 min later as `dc01af6a1` + `83f0b53a5`. Also authored an untracked `test_job_processor_status_guard.py` (later gone).

## What saved the gate
1. **Never touch foreign dirt** — every worker instructed: no stash/revert/checkout; pathspec-only commits. Zero incidents across 16 concurrent committers.
2. **Read-only dirt attribution** (dedicated investigator): `git status` + full diff + mtimes + HEAD-movement timeline attributed every dirty file (mine vs sibling-lane vs foreign vs daemon-generated audit rows).
3. **Commit-pinned evidence claims** — each pack report states the sha it ran at; when the production commit landed after scenario-(d)'s green run, a RE-RUN at the new tip (57/57 incl. the actor's own new D1-D8 cases) kept the evidence current.
4. **Serialization gates** — stage dry-run held until the last committer finished; fixture-copying shell packs held during dirt ambiguity.

## Lesson
Final gates on shared worktrees MUST assume concurrent actors: pin every result to a sha, attribute all dirt read-only before acting, re-run packs whose production surface moved under them, and hold artifact-producing runs (stage/promote dry-runs) until the tree quiesces. The caller's "tree is stable" premise is a snapshot, not a guarantee.
