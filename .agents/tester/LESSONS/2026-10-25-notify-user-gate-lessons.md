# Lessons — notify_user functional gate (2026-10-25)

## 1. Spawn-result ordering ≠ call order — map instance ids from REPORT HEADERS, not spawn outputs
Dispatching 4 workers in one block returned spawn results whose order did not match call order ("Child N of 50" is completion order, not call order). Consequence: an addendum intended for the mutation worker (G3) was sent to the regression worker's id (G2), wedging context mid-run. Recovery that worked: `get_instance_info`+`subtree_messages` inspection → ONE wake+redirect `send_message` that (a) explicitly supersedes the stray message, (b) re-asserts the ONE task, (c) names the surviving artifacts to resume from. The stalled graph took the injection at its next pass and completed cleanly.
**Rule:** when dispatching a parallel wave, record each instance id against its task from the spawn calls in the SAME block you read them — and before any follow-up send, re-verify the id↔name mapping against a delivered report header or `get_instance_info.title`.

## 2. "Wedged vs slow" adjudication needs turn cadence, not metadata timestamps
`get_instance_info.last_activity_at` was frozen at creation time on a stalled instance (unreliable). The real signals: (a) subtree_messages turn timestamps — zero NEW turns for 1h22m past any legitimate 1800s bash ceiling = wedged; ~9-10 min turns = healthy grind. (b) message COUNT growth (58→69) reveals progress even when timestamps lie.
**Rule:** count + timestamp the message tail, never trust a single metadata field.

## 3. Boot-smoke pack opt-in gates that read AMBIENT env are false-PASS traps
`test/packs/notify_user_boot_smoke_test.sh` sets its smoke flags only inside the pytest subprocess `env`, but its own skip-gate reads the caller's ambient env → without caller-side `ENSEMBLE_NOTIFY_SMOKE=1 ENSEMBLE_NOTIFY_SMOKE_UNIT=1` it prints `RESULT: SKIP` and exits 0 — indistinguishable from PASS by exit code.
**Rule:** for opt-in packs, verdict = literal `RESULT:` line + assertion evidence, never rc alone; export the gates caller-side; owning lane should make the pack read its OWN exported vars (defect disclosed in RESULTS, unfixed from the gate lane).

## 4. Whole-tree FAILED-diff gates must be chunk-resumable
A 19k-node monolith bash call is what wedged G2's first attempt. The resumable shape that finished: file-list shards ≤ ~1200 nodes, each its own `timeout 300` call, logs + per-shard rc to disk, resume-by-manifest, auto-split on 124, tainted-log quarantine + mirror-exact re-coverage. Wedge blast radius = one chunk, not the gate.
**Rule:** dispatch long diffs with an explicit chunk-loop contract (one chunk per tool call), not "run the suite."

## 5. This repo's suite hazards re-confirmed (all symmetric both legs — diff-neutral but budget-relevant)
`tests/packs/g7_unique_index_smoke_test.py` module-level `sys.exit(0)` aborts its whole shard session (INTERNALERROR, rc=3); `tests/test_prompt_cache_invalidation.py` hard-aborts on `ENSEMBLE_SELF_ENV='live'` T12 guard in bare worktrees; 6 attestation files hang to the pytest-timeout os._exit shape; `tests/performance/test_message_api_cost.py` >300s solo. Shard around them; never let one offender sink a chunk.
