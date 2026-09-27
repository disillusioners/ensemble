# TARGETED RE-VERIFY — U1-F1 cycle-3 (hook (a) mission-root wrapper) @ a3b1173d — ❌ FAIL (hold merge)

Date: 2026-09-27 · Branch fix/u1-watch-reconcile @ `a3b1173d` (additive on 2b969422, single commit, diff = exactly the 3 claimed files) · Workers: pins/scoped `85a1e9a7`, P4(a) live `54347cc1`.

## Step 1 — pytest (ALL GREEN)
Pin pack 18/18 (incl. NEW `test_p4_terminated_arm_live_seam` PASS) · re-anchored 4/4 · scoped adjacent suites 59P + exactly the 3 declared known-reds (24≠22, macOS path, settled-token) — **zero collateral**. POSTGRES-scrubbed, read-only.

## Step 2 — P4(a) LIVE (the decisive test) — **FAIL per the commission's own step-8 criteria**
- Topology: W=tester mission root, C=worker child (running, non-ghost), **ari = external top-level watcher** (parent_id NULL — the incident/production shape: watcher ≠ mission-root AND not in the root's subtree), armed on W's live turn-2 receipt (`Armed 1 live receipt`), row HELD at settle (C running).
- Trigger: `DELETE /api/instances/{W}` at **18:05:39.466** (cascade TERMINATED; lifecycle event published to EventBus, id=3167 @ 18:05:39.549).
- **73s tight poll: ZERO delivery. Delivered at 18:09:34 by sweep tick 3 — ~234s latency.**
- **Carrier proof (verbatim, the only two matching log lines):**
  ```
  18:09:34 - daemon.services.job_queue_service - INFO - reconcile_held_watches: scanned=2 fired=1 retired=0 (scope=<global>)
  18:09:34 - daemon.services.watch_reconcile_sweep - INFO - WatchReconcileSweepService: tick 3 scanned=2 fired=1 retired=0 (cumulative_fired=1, cumulative_retired=0)
  ```
  `scope=<global>` = the sweep's scan; **no `reconcile_held_watches_for_mission_root` line at delivery** (or anywhere).
- Body (contract-valid envelope, no Result — settled-kind receipt, result_summary unthreaded on this path): `[JOB_EVENT] Job cd39ac99... settled ✓ / Agent: tester`. Exactly-once held (1 event; row CAS-claimed → 0 rows).
- Safety envelope clean: engine marker ensemble_dev, sweep marker present, hygiene 0 pending, teardown ports free, `.env` restored byte-exact (sha `11db2814…9490`).

## Root cause (worker diagnosis, load-bearing)
The wrapper resolves the axis as `tree_set = get_tree_ids_permanent(W) = [W, *descendants]` and filters `watch.instance_id in tree_set`. The held row's `instance_id` is the WATCHER. **An external or ancestor watcher (the dominant production topology — `job_create(watch=true)` parents, the c7f59aaf incident shape) is NEVER in the mission root's subtree → 0 candidates → silent no-op.** The wrapper's own comment assumes "watcher = child of the mission root" — the rare topology. The unit live-seam pin passed because its constructed topology evidently satisfies that hidden assumption (watcher ≠ mission-root BUT inside the tree) — pin-masks-seam, second instance of the 2026-09-27 lesson.

## Adjudication & recommendation (verification-only; no fixes attempted)
**Verdict: FAIL — hold the merge.** Cycle-3 has the same live defect class as cycle-1/2 for external/ancestor watchers; the min(hook,300s) contract still holds ONLY via the sweep backstop (234s observed), so the starvation incident remains *bounded* but the shipped fast path is still dead in the dominant topology.
Fix direction for cycle-4 (dev's call): the reconcile candidate set must be keyed on the **work side** (rows whose job/receipt belongs to the terminated instance's mission — the axis `evaluate_mission_live` and the global sweep already use), NOT on the watcher's tree position; watcher ∈ tree should at most be an optimization subset, never the filter. Pin requirement: vary the watcher's tree position (external, ancestor, descendant) — "live-seam" must mean production TOPOLOGY, not just real objects.

## Follow-ups register
- LESSONS/2026-09-27-pin-masks-live-seam-hook-a.md — second instance appended (topology-blind pin).
- Sweep-interval note: delivery 234s ≈ the 300s cadence (prior gate's "27s" reference was that run's tick alignment, not an interval).
