# FT-2 — Stale [child_outcome: terminated] markers surviving instance revive (P2 closure-council follow-up)

**Source:** P2 closure-council follow-up (separate from FT-001/002/003 deferred tickets).
**Filed:** 2026-08-25 (P3 documentation pass) · **Effort class:** SMALL
**Status:** OPEN (deferred to future batch)

> **Pre-filing check:** No prior filing of `FT-2*` under `.agents/shared/planning/pause-resume-terminate-tree-fix/` or `.agents/tester/`. Existing ticket IDs `FT-001..FT-005` (B7a / B7c / SSE / kill-switch / Lane-5) are distinct from this `FT-2`. Safe to file.

---

## Problem

When an instance is revived — per the `send_message` revive semantics at `daemon/services/instance_messaging.py:1486-1510` (revive `COMPLETED` / `TERMINATED` / `ERROR` / `FAILED` → `RUNNING` and reuse the existing checkpoint) — the parent's already-recorded `[child_outcome: terminated]` markers (one per previously-terminated child) survive the revive. On the next child-event delivery, the parent sees stale outcome suffixes from children that, in the pre-revive tree, were already terminated.

The stale markers do not break the revive itself (the parent transitions cleanly to `RUNNING`), but they cause **stale outcome suffixes on revived parents**: the parent's report-framing logic accumulates the survivor markers, and downstream consumers see `terminated` suffixes for children whose state may have changed post-revive.

## Why this is non-blocking today

- Revive is opt-in via `send_message`; trees that are not revived never hit the path.
- The downstream consumers (caller of `report_payload_surfacing`) tolerate stale outcome suffixes in practice — they are interpreted as "this child is currently in `terminated`," which is correct for the pre-revive snapshot but not the post-revive ground truth.
- Existing test `tests/unit/services/test_child_outcome_payload_surfacing.py` does not exercise revive-after-terminate, so the drift is not surfaced.

## Recommended approach

- **On revive**, clear the parent's `[child_outcome: …]` marker set before the first post-revive child event. (Marker store location TBD by the implementer — likely in `child_reports.py` keyed by parent_id + child_id, same surface that the FIRED-scan in FT-1 reads from.)
- The clear is a single bounded operation scoped to the reviving instance — no cross-instance side effects.
- Document the clear-on-revive invariant in the revive-path docstring at `instance_messaging.py:1486-1510`.

## Acceptance criteria

- New unit test: build a parent + 2 children, terminate the children, terminate the parent (so `[child_outcome: terminated]` markers are present on the parent), then revive the parent via `send_message` and dispatch a new child event. Assert: the parent's `report_payload` does NOT carry the stale `terminated` suffixes.
- Re-run existing `tests/unit/services/test_child_outcome_payload_surfacing.py` — all pass with the clear-on-revive change.

## Risk

- If a future feature relies on the parent retaining the pre-revive marker history (e.g. for an audit log), the clear is destructive. Mitigate by gating the clear behind the revive path only (not in the normal child-outcome delivery path).
