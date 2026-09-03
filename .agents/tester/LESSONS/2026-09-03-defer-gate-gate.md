# LESSONS — defer-gate FULL gate (2026-09-03)

Branch `fix/defer-gate-post-settle-window` @ `b46c9f8b`/`ab567195`; base `f77fb892`; verdict FINAL PASS. Full report: `RESULTS/2026-09-03-defer-gate-full-gate.md`.

## 1. Advisory-to-undispatched-workers contamination (dispatch defect, recovered)

**What happened:** I spawned 12 partition workers but only 6 received their partition tasks in the first send batch (turn ended on report delivery after 6 `send_message` calls). Later, a mid-gate drift advisory (explaining the gate-owned commit `ab567195`) was injected into the 6 spawned-but-NOT-yet-dispatched workers. For those workers the advisory was their ONLY message — 4 of them interpreted the advisory's pack reference as their task and ran `defer_gate_runtime_matrix_test.sh` instead of their partitions.

**Impact:** Zero correctness impact (the mis-run pack is read-only, and it produced 4× extra PASS 5/5 determinism evidence), but ~4 wasted worker turns + one revive cycle; 2 workers ran their real partition only after a context-reset re-dispatch.

**Rule going forward:**
1. **Never send advisories/status messages to workers that have not yet received their TASK.** An advisory is only meaningful atop a task; alone it becomes the task.
2. **A spawn batch and its dispatch batch must not straddle a turn end.** Spawn N + send N in the same turn, or spawn only what will be messaged this turn.
3. **Recovery pattern that worked:** explicit `CONTEXT RESET` re-dispatch naming the real pack, listing the authorized SHA set, and crediting the mis-run as side-evidence. All 6 recovered; no escape-valve re-dispatches were consumed.

## 2. "Improvement" attribution needs base evidence, not baseline folklore

Three failures present in the M2-era baseline did not reproduce at our HEAD (upgrade_registration, slash_commands, proxy_phase1 8→7). My spot-check hypothesis was "improvements from lineage"; the base A/B refuted or re-attributed ALL of them:
- `upgrade_registration` ×2: F at base AND HEAD → pre-existing (M2's "1F" and the final gate's "2F" both circulate; base says 2F — count variance within family).
- `slash_commands`: 40/40P at base → extinct via the mission program (M2's 1F was mission-lineage).
- `proxy_phase1`: 8F base → 7F HEAD; the single true delta is `test_started_at_sourced_from_instance_last_activity_at` — an unclaimed 🟢 observation.

**Rule:** a disappeared failure is only an "improvement" after a base run confirms it failed at base. Baselines from OTHER branches/lineages are not evidence for this branch's attribution.

## 3. p11's 7F: the standing ledger predicted it

The job_queue partition's 7 deterministic failures (observer-guard ×4 + settled-vocabulary ×3) matched the mission program's documented "7-node stale-fixture migration" ledger item exactly — confirmed by verbatim-signature base A/B. **When a ledger names a node count, check it before hypothesizing branch causation.** The branch's own module failing is scary; the ledger made it a 4-minute worktree run instead of an investigation.

## 4. W3 data-integrity bycatch (follow-up tickets, not gate blockers)

Read-only prod measurement surfaced two pre-existing data conditions the gate correctly tolerates: **1,528 orphan message-mirror rows** (instance_id → vanished instances; excluded by the predicate's 3-valued-logic LEFT JOIN) and **270 duplicate-mirror groups** (top: 56 done mirrors for one (project, instance) pair — the manually-repaired 8b6fd0cf has 40). Neither affects gate correctness (one row suffices to hold; orphans hold nothing), but both deserve owner attention.

## 5. httpx env-class footprint grew (26 vs stated 19) — bucket by signature, not file

p12's vscode setup errors (11E) carry the row-37 httpx private-API signature (`object.__new__()` TypeError at `httpx.AsyncClient`), not a vscode-specific defect. Signature-first bucketing kept the family attribution clean; file-first bucketing would have manufactured a new family. Diff-overlap check (branch touches no httpx/gzip/vscode/wc-wake path) closed the attribution without a base run.
