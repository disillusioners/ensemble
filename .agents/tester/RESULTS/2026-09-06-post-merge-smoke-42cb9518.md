# POST-MERGE SMOKE — `latest` @ 42cb9518 (LCA × ReviveGuard true merge)

Date: 2026-09-06 · Tester lane · Verdict: ✅ **PASS** — merge semantically sound on the merged commit; zero failures, zero merge-introduced candidates.

Merge under test: `42cb9518` = true merge of `feature/leader-completion-attestation` (tip `d4b9dd84`, gate suite passed at `6ab16261`) × ReviveGuard scope fix (`8aef1c52..bfc628c0`). Overlap files auto-merged non-overlapping: `daemon/manager.py` + `daemon/tools/instance.py` (AST-verified pre-dispatch). Gap closed by this smoke: no prior test run existed ON `42cb9518` itself.

Workers: smoke-attest `bc471d13` (test-pack-execution) · smoke-revive-pg `918feb8e` (test-pack-execution) · smoke-neighbor `a2999687` (test-pack-execution) · smoke-boot `efb17a71` (no skill, infra). 4 workers, verification-only, 0 repo code changes, 0 commits.

## Scope Decision

Scoped smoke per mission — the 15.6k full-suite regression was NOT re-run (attribution stands from the LCA gate at 6ab16261; ReviveGuard R3 re-gate at 1d166d54). Ran: 4 targeted items on the merged commit. Full suite not warranted: both lineages independently gated; this smoke proves the merge seam.

## Summary

| # | Item | Result | Counts | Runtime |
|---|------|--------|--------|---------|
| 1 | Attestation matrix (33-file glob incl. tests/migration/) | ✅ PASS | **314/314** (296 glob + 18 migration; delta 0 vs H4 baseline) | 7.90s |
| 2 | Revive harness under real PG | ✅ PASS | **2/2 nodes PASSED, executed (not skipped)** on PG 14.22 | 1.62s |
| 3 | Overlap neighborhood (manager.py + tools/instance.py) | ✅ PASS | **345/345** (5 files; 0F/0S) | 18.84s |
| 4 | Boot smoke (disposable daemon, port 8079) | ✅ PASS | boot→healthy ≈4s; /livez 200 v0.12.0; /docs 200; clean shutdown; 8088 untouched | 3m49s wall |

Drift gate (shared-worktree discipline): `latest` @ `42cb9518` confirmed by ALL FOUR workers (pre- and post-run where specified). No drift incidents.

## Item detail

### 1 — Attestation matrix (worker bc471d13)
- 33-file composition verified pre-invocation: `tests/unit/test_attestation_*.py` ×9 + `tests/unit/tools/test_attestation_*.py` ×3 + `tests/integration/test_attestation_*.py` ×20 + `tests/migration/test_attestation_migration.py` (explicit). No zero-file glob.
- 314 passed / 0 failed / 0 skipped — identical to the 6ab16261 H4 baseline (incl. guard node `TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default`).

### 2 — PG revive harness (worker 918feb8e)
- File: `tests/integration/test_message_metadata_send_message_revive.py` — contains exactly the 2 gated nodes (`--collect-only` confirmed).
- PG gating mechanism: `_probe_pg` fixture (file :302-304) → `require_postgres()` (`tests/helpers/checkpoint_prune_pg.py:83`), probes `postgresql://ensemble:…@localhost:5432/ensemble_test`, skips on connection failure. Both nodes PASSED ⇒ real PG execution (a PG outage would have surfaced as SKIPPED — it did not).
- Both test-created disposable DBs (`ensemble_blob_prune_<uuid>`) dropped via in-test `finally`; pre/post straggler parity (7 pre-existing stragglers unchanged). `ensemble_prod`/`ensemble_dev` never touched.
- Meaning: the 6ab16261 PG boolean-default hotfix is intact on the merged commit; the ReviveGuard scope fix does not regress the send_message→COMPLETED→RUNNING revive path on real PG.

### 3 — Overlap neighborhood (worker a2999687)
- Lineage from `git log --name-only e866c116..bfc628c0`: 3 code commits touch tests — `8aef1c52`, `1683cd40`, `1d166d54` (the two `.agents/**`-only doc commits excluded by scope).
- Files run: `tests/test_job_queue_tools.py`, `tests/unit/tools/test_instance_tools.py`, `tests/unit/tools/test_upgrade_registration.py`, `tests/helpers/send_message_fixtures.py` (helper, not collected) + `tests/test_governor_recursion_acceptance_walk.py` + `tests/unit/test_governor_recursion_guard.py` (both governor files confirmed present).
- 345/345 (0F/0S). Count growth vs R3 baseline (285P) is expected — LCA merge adds tests; the zero-failure assertion holds. No QUARANTINE.md addendum row triggered.

### 4 — Boot smoke (worker efb17a71)
- Port 8079 verified free pre-boot; booted `nohup ./dev.sh` (BOOT_PID 30469, BOOT_TS 2026-09-06 05:26:25); PID tree collected via inline `pgrep -P` (6 PIDs incl. uvicorn reloader 30475, server 30477, mcp children).
- Health: /livez 200 first poll (~4s to healthy), body `{"status":"alive","version":"0.12.0"}`; /docs 200; bonus /api/instances?limit=5 → 200.
- Boot-log assertions (time-bracketed 05:26:25→05:28:00, 354 lines; never line windows):
  - `Creating PostgreSQL engine: localhost:5432/ensemble_dev` ✓
  - Attestation line VERBATIM (05:26:28): `Leader completion attestation resolved: mode=dry window=3 deny_bound=3 attestation_enabled=true N_le_min_recent_window=PASS (env ENSEMBLE_LEADER_ATTESTATION_MODE=<unset>, ENSEMBLE_LEADER_ATTESTATION_WINDOW=<unset>, ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND=<unset>). Restart required to flip. See docs/setup.md (ENSEMBLE_LEADER_ATTESTATION_MODE).` ✓
  - **Migration evidence (methodology finding — see LESSONS/2026-09-06-pg-boot-migration-evidence-channel.md)**: SQL runner logs `Skipping migrations for non-SQLite database (schema evolution handled by EnsembleManager._ensure_postgres_columns)` — the shipped `.sql` is the SQLite companion; PG columns evolve via the ensure-path (`ALTER TABLE instances ADD COLUMN IF NOT EXISTS … attestation_denied_count INTEGER NOT NULL DEFAULT 0 / completion_gate_escalated BOOLEAN NOT NULL DEFAULT FALSE`). Proof by read-only catalog query on the 138-row pre-existing `instances` table: `attestation_denied_count|integer|0`, `completion_gate_escalated|boolean|false` ⇒ applied by this boot, PG-valid. ✓
  - Error scan: NO ERROR/Traceback/Exception/CRITICAL in the window. ✓
- Shutdown: SIGTERM to own tree only; all 6 PIDs gone in 2s, no SIGKILL; 8079 freed; 8088 never signaled. Prod straggler (code-server PID 30752, `~/agents-ensemble`) correctly left untouched.

## Failure classification

**Zero failures observed across all 4 items — nothing to classify.** No QUARANTINE.md row triggered (2026-09-06 addendum families — messages.py:258 ×32, job_queue_proxy ×7, stale-contract singles ×24, U11 ×11 — are all OUTSIDE this smoke's scope; the 5 TestAccessMemoryArchive exclusions likewise untouched). **Merge-introduced candidates: 0.**

## Deviations (adjudicated)

- smoke-revive-pg used the file's own auto-managed disposable DB pattern (`ensemble_blob_prune_<uuid>` + in-test drop) instead of the literal `smoke42cb9518_revive` name — intent (disposable, dropped, protected DBs untouched) fully satisfied.
- smoke-boot's migration evidence delivered via PG catalog query rather than "apply" log lines — the log channel does not exist on PG by design (dual-driver); catalog proof is the correct, stronger evidence.

## Follow-ups (non-blocking)

- [ ] 🟢 Standing from LCA gate §8: register the 33rd attestation file in the attestation pack glob (this smoke again included it explicitly).
- [ ] 🟢 Observation only: checkpointer reports `ensemble_test` while engine names `ensemble_dev` on dev boot (pre-existing quirk, behavior unaffected).

## Overall

- Item 1 ✅ · Item 2 ✅ · Item 3 ✅ · Item 4 ✅ · Quarantine delta 0 · **Verdict: PASS — `42cb9518` cleared; the merge is semantically sound on the merged commit.**
