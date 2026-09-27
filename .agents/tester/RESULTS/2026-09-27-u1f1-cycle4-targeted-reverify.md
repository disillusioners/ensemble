# TARGETED RE-VERIFY — U1-F1 cycle-4 (work-side candidate key) @ 741048df — ✅ PASS (cleared for merge → v0.16.1 → stage)

Date: 2026-09-27 · Branch fix/u1-watch-reconcile @ `741048df` (single commit on a3b1173d; diff = exactly job_feedback_observer.py + job_queue_service.py −219 net [wrapper removal] + the pin file) · Workers: pins/scoped `943df4e9`, P4(a) live `7f9fc254`.

## Step 1 — pytest (ALL GREEN)
- Wrapper-deletion proof: `grep -rn "reconcile_held_watches_for_mission_root" daemon/` → ZERO matches. New hook-(a) call site: `job_feedback_observer.py:1607` → `reconcile_held_watches_for_instance(instance_id=None)` (global work-side scan, same shape the sweep uses; hook (b) untouched at :2434; signature `instance_id: str | None = None` @ job_queue_service.py:699).
- Pin pack **20/20** (estimate ~22 was an overcount: cycle-4 added exactly 2 defs, removed 0) — incl. the THREE watcher-position live-seam variants: `test_p4_terminated_arm_live_seam` (DESCENDANT) + `…_external` (EXTERNAL parent_id=NULL) + `…_ancestor` (ANCESTOR) — all green.
- Re-anchored terminated tests **4/4**. Scoped collateral run: 59P + exactly the 3 declared known-reds (24≠22 / macOS path / settled-token) — **zero collateral**. POSTGRES-scrubbed throughout.

## Step 2 — P4(a) LIVE on the EXTERNAL-watcher shape — **PASS**
- Topology: W=tester mission root; C=worker child running (mission live); **ari = top-level watcher parent_id=NULL** armed on W's live turn-2 receipt (`Armed 1 live receipt`); row HELD at settle (18:48:12).
- **Trigger:** `DELETE /api/instances/{W}` @ 18:48:23.26 (cascade TERMINATED).
- **Carrier proof (verbatim, decisive):**
  ```
  18:48:23 - daemon.services.job_queue_service - INFO - reconcile_held_watches_for_instance: scanned=2 fired=1 retired=0 (instance_filter=<global>)
  ```
  Same second as the DELETE, **no `WatchReconcileSweep … tick` companion** (ticks at 18:38:47/18:43:47/**18:48:47**/18:53:47 — the reconcile sits 24s before tick 3). `instance_filter=<global>` is the cycle-4 signature.
- **Delivery: [JOB_EVENT] in ari's message queue by 18:48:24 — ~1 second**, vs 234s (sweep) in the cycle-3 FAIL on this same topology.
- **Exactly-once across the next TWO sweep ticks (18:48:47, 18:53:47): count still 1; DB row CAS-consumed → 0.**
- Body verbatim: `[JOB_EVENT] Job a9b91aad... settled ✓ / Agent: tester` — status-only, consistent with the same path's shape in prior runs: J1 is a messages-API-revive receipt (`result_summary=None` per U6 — declared OUT of scope by the main-gate commission; not a cycle-4 regression, and content-on-paths-where-content-exists was P6-proven in the main gate).
- Safety envelope: engine marker `ensemble_dev`; hygiene 0 pending; teardown ports clean; 8088/live/demo untouched (launcher-lineage verified); `.env` round-trip byte-identical (flip-script MATCH; note: the live file's current sha differs in trailing digits from the 09-25-recorded `…9490` — `.env` is mutable/gitignored and evolved across sessions; within-run round-trip integrity is the verified invariant).
- Honesty notes: run 1 missed the mission-live window (bash overhead → natural-path delivery) — worker detected, shortened sleeps, and the PASS evidence is from a clean run with DELETE inside the live window. Optional ancestor-variant live run skipped (not required). Procedural: daemon tree cleanup needed a second pass (own tree only).

## Verdict
**PASS — `741048df` CLEARED FOR MERGE → v0.16.1 cut → stage (STOP at staged-READY).** Cycle-4 closes U1-F1: hook (a) delivers in-session (~1s) on the external-watcher production topology, work-side/topology-invariant candidate key, exactly-once preserved across carrier overlap. Residual (declared out of scope, carried in the main gate's register): U6 status-only bodies on messages-API receipts; sweep-interval-bound worst case 300s for terminal branches that bypass both hooks.
