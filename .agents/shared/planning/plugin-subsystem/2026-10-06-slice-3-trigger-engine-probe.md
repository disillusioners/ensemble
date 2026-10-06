# Slice ③ / ⑥ Trigger-Engine Payload Probe — Decision Document

**Date:** 2026-10-06
**Probe owner:** slice ③ coder
**Decision owner:** slice ⑥ implementor (this is a probe; no trigger-engine EDITS happen in ③)
**Source of truth:** REC §1.2 comp 7 (`drift_event_publisher`); REC §9 OQ "trigger-engine drift-payload acceptance"; CON §5 drift-event payload core.
**Audience:** slice ⑥ implementor (1-page read)

---

## TL;DR (the decision)

**The drift-event payload is emitted in CON §5's VERBATIM shape
— no envelope.**

The shape is:
```python
{
    "plugin": "<name>",         # "opendesign"
    "class": "<class>",         # "snapshot_with_drift_alarm"
    "divergence_id": int,       # 1, 2, 3, ...
    "files": [str, ...],        # 1-10 sample file paths
    "delta": str,               # one-line
    "rationale": str,           # one-line
    "pinning_test": str,        # test-name-or-path
    "observed_at": str,         # ISO 8601 UTC
    "observed_tag": str,        # "open-design-v0.24.1"
}
```

This is the exact CON §5 "Drift-event emission" payload core,
FROZEN per the contracts doc §8 versioning rule (additive-only
on 1.0.x).

The `slice ⑥` wiring: add a new `condition_type` to
`daemon/services/skill_trigger_seed.py` (e.g.
`drift_event_observed`) AND/OR a dedicated drift-event store the
resolver polls. The condition body checks the divergence-id and
the observed_at; the rule fires when the alarm is fresh. Slice ⑥
also wires `drift_event_publisher` to enqueue the event from the
sync-runner's `emit_drift_event` call site.

No trigger-engine EDIT happens in slice ③. The sync-runner's
`emit_drift_event(...)` is a NO-OP STUB that logs the payload
shape; the payload is built by `build_drift_event_payload(...)`
which the tests pin verbatim.

---

## Why verbatim (and not an envelope)

The trigger engine
(`daemon/services/skill_trigger_engine.py`) is a rule evaluator:
it walks enabled `SkillTrigger` rows, applies a type-specific
condition, and returns flagged skills.  Each rule's
`condition_json` is free-form (typed by `condition_type`); the
engine itself does NOT impose a payload envelope on the rule
body.  Examples of current `condition_type` discriminators
(`daemon/services/skill_trigger_seed.py:65-112`):

- `low_completion_rate`, `high_fallback_rate`,
  `consecutive_failures`, `periodic_scan`, `task_count_scan`,
  `low_usefulness` — all are condition-body types that read
  per-skill stats; the engine reads its own data sources, not
  external events.

For the drift payload, two design paths were on the table:

### Path A: VERBATIM CON §5 payload as the condition body

```json
{
  "name": "drift_event_observed",
  "condition_type": "drift_event_observed",
  "condition_json": {
    "plugin": "opendesign",
    "class": "snapshot_with_drift_alarm",
    "divergence_id": 5,
    "observed_tag": "open-design-v0.24.1"
  },
  "action": "analyze"
}
```

The condition body is the verbatim payload (or a subset of it).
The engine's resolver walks every drift event and checks the
condition against the payload fields.  This is the lowest-friction
extension: no new bus, no new store, no envelope.

### Path B: envelope `{event_type, payload, ...}` wrapping CON §5

```json
{
  "event_type": "drift",
  "source": "plugin_sync",
  "version": 1,
  "payload": {
    "plugin": "opendesign",
    "class": "snapshot_with_drift_alarm",
    ...
  }
}
```

The envelope is generic; future event types can share the same
bus.  But the trigger engine is rule-based and would have to
unpack the envelope to read the payload — pure overhead for v1.

### Decision: Path A (verbatim) for v1

Reasons:

1. **CON §5 frozen surface.** The payload is frozen in the
   contracts doc; adding an envelope on top creates a v1
   surface that is NOT in CON §5 and would need its own
   versioning rule. The CON §8 versioning rule already covers
   the verbatim shape.
2. **Single event type.** v1 has one event type (drift from
   sync); the envelope pays abstraction cost for zero present
   benefit.
3. **Trigger engine shape.** The engine reads `condition_json`
   per-rule; the verbatim shape slots in directly.  An envelope
   would require a new `condition_type` discriminator AND a
   payload-unpack helper.
4. **dsh borrow item (a).** "Adopt divergence-register log
   into manifest spec v1" — the register is the canonical
   representation; the drift-event payload is the SYNCHRONIZED
   trigger-side view.  Sharing the verbatim shape across
   manifest and trigger keeps the discipline: the manifest is
   the source of truth, the trigger engine is the
   consumer-of-truth.

---

## What slice ⑥ must wire (the contract)

1. **Add `condition_type` `drift_event_observed` to the
   default trigger catalogue.**  In
   `daemon/services/skill_trigger_seed.py:DEFAULT_TRIGGERS`,
   add a new entry:
   ```python
   {
       "name": "drift_event_observed",
       "condition_type": "drift_event_observed",
       "condition_json": {
           "plugin": "opendesign",  # or "*" with per-plugin filter
           "min_divergence_id": 1,
       },
       "action": "analyze",
   }
   ```

2. **Add the matching handler in
   `daemon/services/skill_trigger_engine.py`.**  The new
   `_evaluate_condition` branch reads the drift-event table
   (or the in-memory event list, depending on storage
   choice), filters by the `condition_json` fields, and
   returns True if a matching fresh event exists.  The
   trigger name + reason use the verbatim payload fields.

3. **Wire `drift_event_publisher` (REC §1.2 comp 7).**  The
   publisher subscribes to the sync-runner's
   `emit_drift_event` call site.  Choices:
   - (a) Extend the trigger engine to read a new
     `drift_events` table (DB-backed; one row per emitted
     event; resolution deletes the row).
   - (b) In-memory event bus between the sync-runner and the
     trigger engine (per the existing
     `daemon/services/dispatch_event_bus.py` pattern; the
     `concurrent` semantics need a test).

   Recommendation: (a) for durability + replay capability;
   the event is small (one row, 9 fields) and the table
   fits cleanly with the existing `skill_triggers` /
   `skill_usage_records` schema family.

4. **The sync-runner's `emit_drift_event(...)` is the
   no-op stub today; slice ⑥ REPLACES the stub with the
   actual call to the publisher.**  The signature stays
   stable: `emit_drift_event(plugin, target_class, entry,
   observed_tag)` returning `None`.  The build-time
   `build_drift_event_payload` helper is the single source
   of truth for the payload shape; the publisher calls it
   to build the row, then persists + dispatches.

5. **Promote-staleness-check integration.**  The
   `staleness_age_days` field on the `sync_result` is a
   SEPARATE signal (it does not require a drift event;
   staleness is a continuous measure, drift is binary).
   The slice ⑥ promote-staleness-check predicate
   (`REC §1.2 comp 13`) consumes the `sync_result` directly,
   not the drift-event stream.  Drift events are for the
   trigger engine's analyze/escalate path; staleness is for
   the promote-gate's argv-only override path.  Both share
   the same `alarm_owner` (per-class, CON §2); the
   `block-promote-after-days` escalation applies to BOTH.

---

## Payload shape pin (for slice ⑥ reference + the test corpus)

Tests in `tests/unit/plugin_subsystem/test_sync_runner.py::TestDriftEventPayload`
assert the verbatim shape:

```python
from daemon.plugin_subsystem import build_drift_event_payload
from datetime import datetime, timezone

entry = {
    "id": 5,
    "files": ["prompts/contracts/od-next-intent-resolution.ts"],
    "delta": "+1 file in v0.24.1",
    "rationale": "Snapshot pulled at sync time; re-apply or drop per CON §2",
    "pinning_test": "tests/unit/plugin_subsystem/test_sync_runner.py::TestSyncSnapshotDrift",
}
payload = build_drift_event_payload(
    "opendesign", "snapshot_with_drift_alarm", entry, "open-design-v0.24.1",
    now=datetime(2026, 10, 6, 19, 45, tzinfo=timezone.utc),
)
assert payload == {
    "plugin": "opendesign",
    "class": "snapshot_with_drift_alarm",
    "divergence_id": 5,
    "files": ["prompts/contracts/od-next-intent-resolution.ts"],
    "delta": "+1 file in v0.24.1",
    "rationale": "Snapshot pulled at sync time; re-apply or drop per CON §2",
    "pinning_test": "tests/unit/plugin_subsystem/test_sync_runner.py::TestSyncSnapshotDrift",
    "observed_at": "2026-10-06T19:45:00+00:00",
    "observed_tag": "open-design-v0.24.1",
}
```

This is the contract.  Slice ⑥ must NOT change the field set
without bumping the manifest `schema_version` (CON §8
additive-only rule on 1.0.x means a new field is allowed; a
field removal is major).

---

## Open question: per-plugin vs cross-plugin

The example trigger above filters by `plugin: "opendesign"`.  A
cross-plugin filter (`plugin: "*"`) would require the engine to
walk ALL drift events.  Slice ⑥'s trigger design picks one:

- **Per-plugin (default for v1):** the trigger has a specific
  `plugin` field.  Each plugin gets its own trigger row; the
  alarm_owner is implicit in the per-class manifest section
  (CON §2).  This is the most common case (one plugin, one
  per-class alarm owner).
- **Cross-plugin (future):** a single trigger with `plugin: "*"`
  walks every drift event.  Useful for a "sweep all
  plugins and escalate any unresolved drift after N days" rule.
  v1 can defer this; the schema accommodates it.

Slice ⑥'s call: pick per-plugin for v1; document the future
cross-plugin path in `daemon/services/skill_trigger_seed.py`.

---

*Document created 2026-10-06 as the slice ③ deliverable for
REC §9 OQ "trigger-engine drift-payload acceptance".  No code
changes to the trigger engine in ③; slice ⑥ executes the
wire-up per this contract.*
