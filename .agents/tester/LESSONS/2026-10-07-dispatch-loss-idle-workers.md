# Dispatch-loss: spawned workers that never consumed their first message

**Date:** 2026-10-07 (upgrade-resilience final gate)

## Symptom
4 of 30 spawned workers (w6-k1, w7-plugin-dir, w8-drift-parity, w14-k2 — all skill-dispatch spawns from one wave) sat for 60-120+ min with NO completion report. `get_instance_info` showed the signature: `status=idle`, `last_activity_at=null`, `created_at == updated_at`. Their queued `send_message(load_skill=...)` was effectively lost — never dispatched.

## Detection
One-shot `get_instance_info` probe on workers >2× past estimate. Distinguish from "slow worker": idle+null-activity = never started (replace); running = wait. Sibling workers from the SAME spawn batch dispatched fine — not systematic per-batch, per-instance loss.

## Recovery (Fan-In Escape Valve)
1. `terminate_instance` the stale shell (prevents a late-firing duplicate dispatch racing the replacement — critical when the task commits to git).
2. Spawn ONE replacement + re-send the same strict single-pack message with a "previous attempt stalled — re-verify before trusting pre-existing output" note.
3. Max 1 re-dispatch (Cardinal #3). All 4 replacements delivered first-try.

## Lesson
Long-silence workers are not always slow — probe metadata before waiting indefinitely. `last_activity_at=null` + `idle` at T+60min means the message never landed; no report will ever come despite the "guaranteed delivery" contract.
