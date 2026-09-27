# KMS test family dirties tracked install-audit.jsonl — porcelain gates in shared worktrees (2026-09-27)

**Context:** whole-tree A/B sweep for the dry-run-projection-v3.2 merge gate (HEAD main checkout vs detached base worktree /tmp/ens-base-v320-1989b3b5). FIVE shard workers independently tripped their pre-flight "porcelain clean" gate on the same file: `.agents/shared/planning/designer-agent/install-audit.jsonl`.

**Root cause:** the KMS test family — `tests/unit/test_install_audit.py`, `tests/unit/test_builtin_mcp_servers.py`, `tests/unit/services/test_kms_lite.py`, `tests/unit/services/test_kms_raw_row_migration.py`, plus designer-KMS installer mocks inside `tests/integration/*` — APPENDS `kms_issue`/`opendesign` events to that TRACKED file as a normal side effect of pytest execution. Observed batches: +24 lines (12:13–12:14Z, from S5c-BASE integration designer-KMS tests) and +7 lines (12:23:37Z, single <10ms cluster, from S2-BASE's own tests/unit/services KMS tests mid-run).

**Cost this cycle:** 5 stop-and-report cycles + dispatcher adjudications + a falsification leg (a guard condition "no new events after 12:15Z" set on a wrong premise — sibling scopes DO run KMS tests — was legitimately falsified by S2-BASE's batch; attribution closed only when S2's own report owned the burst).

**Rules going forward:**
1. Whole-tree sweeps in worktrees: WHITELIST this file in porcelain pre-flight gates (or scope-isolate KMS-containing shards; or restore-before-run + disclose-after).
2. "Dirty porcelain" in a shared worktree during parallel sweeps = test-exercise artifact FIRST, foreign writer second. Attribution test: do the event timestamps cluster inside a live sibling shard's window, and is the emitter file in that sibling's scope?
3. Never treat this file's dirt as baseline contamination of test inventories — no interference channel (separate pytest processes; the file is not read by compared tests).
4. When setting falsifiable STOP conditions on shared state, enumerate which sibling scopes can emit — the "no other scope runs KMS tests" premise failed because KMS tests live in ordinary unit dirs, not just designer/integration scopes.
