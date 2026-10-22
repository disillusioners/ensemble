# DF-4 verification finding: the ari-fallback delivery arm's stated trigger is DEAD (ghost-arming wake silently absorbed)

Date: 2026-10-22 · Found by: W-D (tester lane, DF-4 item 3) · Status: routed to developer lane — candidate defect, pre-existing, NOT a DF-4 regression

## Symptom
A `PendingWake` whose ARMING instance row is missing/dead does NOT take the sweep's `except KeyError → _deliver_wake_ari_fallback` path. The wake is silently absorbed by the DIRECT arm:
- `InstanceMessagingService._prepare_enqueued_message` (`daemon/services/instance_messaging.py:2458-2461`) logs a WARNING for the missing instance and writes the MessageQueue+Task rows **unconditionally**.
- Result (probe-proven on scratch PG): `delivered=1`, queue row lands on the **ghost arming instance_id**, ari fallback never entered, `errors=0`, no `arm_notify_no_instance`, no front-door ari delivery.

## Why the docstring is wrong
`daemon/services/upgrade_journal_sweep.py:1006-1017` claims the enqueue raises `KeyError` "per instance_lifecycle.get_instance:4153". That KeyError (`instance_lifecycle.py:4511`) belongs to the **graph cold-load** path — which enqueue never calls. The existing unit pack (`tests/unit/services/test_post_restart_arm_notify_sweep.py:276-284`) enters the arm only via a stub manager, so nothing ever caught the drift.

## Blast radius
Any post-restart arm-notify wake whose arming instance was hard-deleted between arm and sweep tick: delivered to a ghost id (no consumer), no ari fallback, no abandoned bookkeeping. Exists identically at base `60d7daaf4` and HEAD `9959f9f4b` — DF-4 changed only the literal, so not a DF-4 blocker.

## What the tester lane did (and did not) do
- Behavioural cover for the arm's DELIVERY semantics now exists: `tests/postgres/test_df4_ari_fallback_closure.py` (UNTRACKED as of this writing — stage lane must commit it). It replays the documented gone-row contract (real `KeyError` shape for the gone id ONLY; every other call real) — disclosed in the module docstring.
- Deliberately did NOT pin the ghost-absorption behavior itself — pinning it would bless a probable defect. That adjudication belongs to the developer lane.

## Incidental observation (unasserted)
With a genuine `source="api"` wake and a gone arming row, the direct arm's re-stamp loop (`sweep :1706-1717`) stamps the GHOST arming id in-memory before the warning path — worth checking when the seam is fixed.

## Repro starting point
Arm `PendingWake(source="")` with `arming_iid` whose instance row does not exist; run the real sweep over a tmp journal on scratch PG; observe the queue row's `instance_id` and the absence of `arm_notify_*` history events.
