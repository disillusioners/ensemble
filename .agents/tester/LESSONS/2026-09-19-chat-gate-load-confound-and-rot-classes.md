# 2026-09-19 — chat-source-worker-lane merge gate: parallel-fan-out load confound + two branch-introduced rot classes

**Arc:** final verification gate, `feature/chat-source-worker-lane` @ d016aeef (base c8855e8a). Verdict FAIL — 16 branch-introduced reds; full report: `RESULTS/2026-09-19-chat-source-worker-lane-merge-gate.md`.

## 1. Parallel-fan-out load confound is REAL and measurable at 6-7 concurrent xdist packs

- Wave-2 ran 6 xdist packs + 3 light workers concurrently: every pack ran **3-10× its registered runtime** (P-8: 209.7s vs ~20s registered; P-9: 97.5s vs 21s; P-10: 83.0s vs 18s).
- Consequences observed: (a) `regression_integration_opencode_e2e_test.sh` (P-12) TIMED OUT at 300s under load AND **again solo** — the pack has genuinely outgrown the 300s cap (registered 2026-09-03 at ~710 tests; scope grew); (b) 3 concurrency-sensitive tests (`atomic_dequeue::…under_concurrency`, `atomic_status_transitions::…only_one_increments`, `skill_evolution_service::test_ab_resolution_force_resolve`) red in census but **3/3 PASS solo at HEAD** — load-induced, not regressions; (c) `test_jsonb_migration` ×3 "worker gwN crashed" census reds = xdist-worker crashes via the pre-existing fresh-SQLite migration trap — **green solo at BOTH base and HEAD** (adjudicator re-verified both trees).
- Rules that saved us: (1) run timing-sensitive verification (headline ≤3.0s assertions) on a DRAINED machine — headline 3/3 PASS with ~4s runtimes; (2) adjudicate every census red by base A/B or ≥3× solo before believing it; (3) a pack that times out under load must be re-run SOLO once before declaring pack rot — both matter here (load made it worse; solo proved intrinsic breach).
- **P-12 maintenance debt:** needs a split/re-registration (e.g. integration/ vs opencode+e2e slices executed fine at 283s + 17s). Frozen tree forbade editing the script; ad-hoc sub-slices mirroring the pack's exact flags delivered the census.

## 2. Branch-introduced rot classes the gate caught (developer fix list)

- **`_notify_all_pools` holder rot (6 nodes, base-green → HEAD-red):** Phase-2 replaced direct wake calls with `manager._notify_all_pools()` fan-out; `test_task_only_create_notify_work.py` (×4) and `test_reconciler_wedge_fix.py` (×2) holder stubs never got the method → `AttributeError`. The branch's own rot-fix 42ed8f7f fenced only `worker_notification*` + `message_queue_redesign` — **wake-contract rot lives in OTHER files**; when replacing a manager-surface method, grep ALL tests for holder/stub classes approximating that surface (`grep -rn "class.*Holder" tests/`), not just the files that referenced the old method name.
- **Validator-vs-fixture contract rot (10 nodes in `tests/test_api.py`):** the new registration-time `source_id` validator 422s legacy create fixtures, cascading 404s to 8 downstream endpoint tests. Base test_api.py was 45P/2F (the known await-rot pair) — the delta was instantly attributable. Lesson: when adding an input-validation gate, run the DIRECT API-surface test file at base first; fixture payloads encode the old contract.

## 3. Misc settle
- worker_notification pair collection: **37** (14+23). "64" matches no current selection — stale/wider-scope ledger figure. Rot-fix commits can be mock-only with zero count delta; count discrepancies are selection-scope questions, always re-collect fresh.
- The P-11 job_queue 7-red set reproduced **node-for-node, signature-for-signature** vs its 2026-09-10 registration — cheap powerful waiving evidence when a pack's red set is stable across weeks.
