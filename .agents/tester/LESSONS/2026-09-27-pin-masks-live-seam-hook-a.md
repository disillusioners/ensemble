# Lesson: Unit pins must invoke seams the way production does — the hook-(a) pin-mask (U1 gate 2026-09-27)

**Date:** 2026-09-27
**Gate:** fix/u1-watch-reconcile @ 2b969422 (U1-slice) — E2E P4(a)
**Found by:** E2E worker (live), after the unit pin passed 5/5 in the flake matrix.

## Symptom

Pin `test_p4_terminated_arm_fires_via_helper_hook` (in `tests/job_queue/test_u1_slice_commission_pins.py`) passed green — while the SAME behavior it pins is a **live no-op** in production:

- Hook (a) (`_fire_watcher_notify_for_terminal`) calls `reconcile_held_watches_for_instance(instance_id=<TERMINATED instance>)` — keyed on the mission root.
- The held `job_watchers` row's `instance_id` is the **WATCHER** (who receives the event), not the mission root.
- Filter matches nothing → no fire. Live evidence: no hook log line; delivery carried by the 300s sweep at 27s.
- The pin called the helper **directly with a watcher id**, bypassing the production call-site's key choice → green pin, dead path.

## Root cause (pattern, not bug)

The pin tested the helper's BODY with the correct key, but production reaches the helper through a call-site that computes the key differently. The seam under test was the helper; the seam that broke is the **call-site's argument derivation**. Pins that invoke the real call-site (or assert its arguments) catch this; pins that invoke the callee directly cannot.

## Rule for future pins

1. For every "X fires on event Y" pin where production routes X through a call-site, add an assertion on the **call-site's arguments** (spy the call, match the live key semantics), or invoke through the real dispatcher.
2. Prefer one live E2E probe per new hook/fast-path (the marker-mock harness makes this cheap: arm → hold → trigger → poll) — the U1 gate's P4(a) is the template.
3. When a pin names the helper directly, label it `helper-body pin` in the docstring so reviewers know call-site wiring is untested.

## Follow-up routed

U1-F1 in RESULTS/2026-09-27-u1-watch-reconcile-gate.md §5: fix the reconcile key (watcher-scoped or global) + re-anchor the pin to the live seam. Not starvation-regressing (hook (b) + sweep held the contract on every live path).

## Second instance (cycle-3, 2026-09-27 — same lesson, one level deeper)

Cycle-3 shipped a NEW "live-seam" pin (`test_p4_terminated_arm_live_seam`: real observer, real JQS, real lifecycle event, watcher ≠ mission-root) — and it PASSED while the live path STILL no-oped (RESULTS/2026-09-27-u1f1-cycle3-targeted-reverify.md). The wrapper was re-keyed to the mission-root tree and filters `watch.instance_id ∈ tree(root)`; the pin's topology put the watcher INSIDE the root's subtree (satisfying the hidden assumption), while production's dominant topology (external/ancestor watcher — `job_create(watch=true)` parents, the c7f59aaf incident shape) is outside it.

**Refined rule:** a live-seam pin must reproduce production TOPOLOGY, not just real objects — vary the watcher's tree position (external / ancestor / descendant) across pin cases. The correct candidate key is the WORK side (rows whose receipts belong to the terminated mission), which is topology-invariant; any watcher-position filter is a latent no-op bug.

## Related

- Same-family: P5's terminal went via the `no parent,no children` branch (bypasses `_finalize_job` → hook (b) not the carrier) — hook coverage of ALL terminal branches needs the same call-site-level pin treatment.
