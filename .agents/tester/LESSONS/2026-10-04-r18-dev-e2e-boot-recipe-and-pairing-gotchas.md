# R18 dev E2E — missing pattern doc, reconstructed boot recipe, agent-pairing gotchas

**Date:** 2026-10-04 · **Commission:** R18 spawn_hot_instance auto-dispatch (branch `feature/spawn-hot-auto-enqueue`, tip `6cb0a2ec`, base `2ae91046`) · **Verdict: PASS** (unit 135/0/0/0 + E2E 5/5)

## 1. Referenced pattern doc was GONE — reconstruct from KB + /proc

The commissioned reference `.agents/tester/RESULTS/2026-10-04-agent-snapshot-v1-dev-e2e.md` does not exist
on disk, in ANY branch (`git log --all --diff-filter=A` empty), in stashes, or in any worktree — yet a
commit message (1090f308) and a project critical note both cite it. **Uncommitted evidence does not survive
branch/worktree churn.** Always commit E2E evidence to the branch immediately.

Recovery path that worked (in order): `explore()` KB query → live daemon `/proc/<pid>/environ` of the
previous 8079 source-run (the previous commission's daemon was still running — its exact env IS the proven
boot recipe) → `/home/nea/dev-daemon-8079-v0.16.11/HANDOFF.md` + `boot.env` (frozen fallback + creds).

## 2. Proven dev-lane source-run recipe (8079), validated again 2026-10-04

```
data dir (OUTSIDE the worktree, survives worktree removal):
  /home/nea/dev-daemon-8079-<tag>/data/ensemble.json  # copy verbatim from /home/nea/dev-daemon-8079-srcfix/data/ensemble.json
  /home/nea/dev-daemon-8079-<tag>/logs/                # nohup stdout capture + e2e artifacts
wrapper (/tmp/<tag>-boot.sh): scrub POSTGRES_*/ENSEMBLE_*/PORT/HOST/SSL_CERT_* →
  source /home/nea/dev-daemon-8079-v0.16.11/boot.env (ENSEMBLE_SELF_ENV=dev, PG ensemble_dev @127.0.0.1
  user ensemble_dev_app, PORT=8079 HOST=127.0.0.1 QUEUE_DISCARD_ON_STARTUP=true) →
  export ENSEMBLE_DATA_DIR=<data dir> → cd <worktree> → exec .venv/bin/python -m daemon
isolation evidence (all 4): factory engine line "127.0.0.1:5432/ensemble_dev" + /livez version ==
  branch version (NOT 0.16.11) + /readyz components true + /proc/<pid>/environ dev-clean.
  NEVER trust /api/health current_database (misleading "postgres" on every daemon).
revert: kill pid (ss-verified 8079 owner) → bash /home/nea/dev-daemon-8079-v0.16.11/boot.sh → livez 0.16.11.
```

## 3. Agent-pairing gotchas for spawn_hot_instance E2E on dev

- **tester is the only agent holding BOTH `spawn_hot_instance` + `snapshot_create`** (ari excluded from the former). Use tester as parent; same-parent snapshot create + spawn guarantees identical project scope for the warm match.
- **tester CANNOT spawn wanderer** — live denial text: `Allowed team members: ['explorer','image-reader','kb-writer','worker']`. Child must be `worker` (or another allow-listed lightweight agent). This asymmetry is itself the ready-made E4 blocked-scenario pairing (tester→wanderer = guaranteed `started:"blocked"`, no row, no enqueue).
- R15 gate: `PUT /api/settings/snapshot-create {"enabled": true}` before snapshot_create; spawn_hot_instance/snapshot_search are never-gated.
- Census greps: `[SnapshotSpawnWarm]` (warm consume) + `[SnapshotAutoDispatch]` (F2; carries caller_iid/target_iid/content_len/message_id — the message_id is the ONLY place the enqueue's message id surfaces).

## 4. Process gotchas

- **Branch advanced mid-commission:** reviewer landed test-only `d4eeecad` after worktree creation; our evidence commit `ea2a4573` parented onto it. Counts of record are pinned to the commissioned tip `6cb0a2ec` — state the commit explicitly in every count. (A later test-only commit can shift counts; re-run if tip counts are needed.)
- **Full `tests/unit/tools/` sweep (59 files) exceeds 300s** at `test_upgrade_tools.py::TestSystemRestartMatrix::test_pipeline_busy_in_flight_txn_names_run_id` (~96% through). Snapshot-adjacent subset is the sanctioned fallback. Unrelated out-of-scope failures seen in the sweep window (2× knowledge explore-caller, 3× prompt-section bare-md) verified pre-existing on base.
- Pre-existing failure family is **5 tests**, not 1: the whole `TestAccessMemoryArchive` class fails "Access denied" on base `2ae91046` identically (same lines, same messages). Expect 5, not 1, when confirming isolation.
- Worktree removal after a source-run daemon: runtime residue (untracked) blocks clean remove → verify evidence commit is on the branch ref FIRST, then `git worktree remove --force`.

**Artifacts:** evidence `.agents/tester/RESULTS/2026-10-04-r18-spawn-hot-auto-dispatch-e2e.md` @ `ea2a4573` (on branch); raw payloads `/home/nea/dev-daemon-8079-r18/logs/e2e-artifacts/`; Phase-1 detail `/tmp/r18-phase1-results.md` (volatile).
