# FT-003 — SSE: status_change fan-out to parent subscribers

**Source spec:** `phase3-plan.md` §Out of Scope → Ticket FT-003 (Task 3.10 deliverable)
**Filed:** 2026-08-25 (P3 documentation pass) · **Effort class:** MEDIUM
**Status:** OPEN (deferred to future batch)

---

## Repro

Parent subscribed to SSE; child transitions status (cascade event); FE misses the event; FE self-corrects via 60s polling. Hub routing at `daemon/services/live_event_hub.py:175-196` only looks up connections for the exact `instance_id`.

## Suspected sites

- `daemon/services/live_event_hub.py:175-196` — `_stream_to_connections` (route by node id only).
- `daemon/services/live_event_hub.py:292-313` — `stream_instance_created` (precedent for parent_id fan-out).
- All `stream_status_change` callers — audit for `parent_id` availability in context.

## Effort class

MEDIUM (hub routing change + caller audit + `parent_id` resolution in every call site that lacks it).

## Recommended approach

- **Mirror the `instance_created` pattern** (precedent at `live_event_hub.py:292-313`): when `parent_id` is known at emit time, fan out to `parent_id`'s connections AS WELL AS the target's connections.
- For call sites without `parent_id`, add a `parent_id` lookup via `instance_repository.get(instance_id).parent_id` (cached) — small DB read at most call sites.
- Audit all `stream_status_change` callers for `parent_id` availability in the emit context.
- Document the change in `docs/sse-events.md`.
- (Self-correction via 60s polling currently masks the missing fan-out for end users, but each missed event costs one polling cycle's perceived latency.)
