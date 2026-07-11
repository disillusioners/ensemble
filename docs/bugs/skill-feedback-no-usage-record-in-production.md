# Bug: `skill_feedback` returns "No usage record found" in production

**Date:** 2026-07-11
**Status:** Investigated (pending fix)
**Severity:** Medium (silent skill-evolution data loss — feedback never persists, counter bumps don't fire, A/B comparison stats stay flat)

---

## Summary

The `skill_feedback` tool almost always returns
`⚠️ No usage record found for skill <id>... Feedback cannot be applied.`
in production. The warning is not a DB-mismatch or Postgres/SQLite issue
(it is sometimes reported as one). The root cause is a missing persistence
step in the Phase 3 → Phase 4 skill flow: `SkillInjectionService` tracks
injected skill IDs in an in-memory dict only, and **nothing** ever writes
them to `instance_metadata["last_injected_skill_ids"]` — the key the
Phase 4 `SkillMetricsService` reads at task-completion time. The
happy-path integration test passes only because it manually does the
metadata write that production code skips.

Reported message:
```
Input:  {"skill_id": "950f7f4e-...", "applied": true, "note": "Skill rất hữu ích..."}
Output: ⚠️ No usage record found for skill 950f7f4e... Feedback cannot be applied.
```

---

## Root Cause

| Location | Issue |
|----------|-------|
| `daemon/services/skill_injection_service.py:265-287` | `track_injection()` only writes to `self._injected_skills[instance_id][message_id]` (in-memory). |
| `daemon/services/instance_messaging.py:1809-1813` | Production caller invokes `track_injection` but never persists the IDs to instance metadata. |
| `daemon/services/skill_metrics_service.py:354-356` | `record_task_completion()` reads `instance_metadata["last_injected_skill_ids"]` — always empty in production, so 0 usage rows are written. |
| `daemon/tools/skill_tools.py:672-674` | `skill_feedback` formats the "No usage record found" warning when the `record_feedback` lookup returns no row. |
| `tests/integration/test_skill_cross_phase_flow_a.py:604-608` | The integration test that passes writes the metadata manually, hiding the bug from CI. |

### Broken flow

1. A user message arrives → `SkillInjectionService.inject_skills(...)` searches the skill corpus and returns `(injection_text, injected_skill_ids)` (line 177).
2. The caller (`instance_messaging.py:1809`) calls `injection_service.track_injection(instance_id, message_id, injected_skill_ids)`.
3. `track_injection` populates `self._injected_skills[instance_id][message_id]` — and that's it. It does **not** call `instance_repo.set_metadata(...)`. Restart loses this; nothing else can read it.
4. The task eventually completes → `JobQueueService` triggers `record_task_completion(instance_id=..., ...)` (`job_queue_service.py:1844`).
5. `record_task_completion` reads `instance_metadata["last_injected_skill_ids"]` (`skill_metrics_service.py:354`). The key is never written by production code → `raw = meta.get(INJECTED_SKILLS_METADATA_KEY)` is always falsy → early-return `0`.
6. No `SkillUsageRecord` rows are written.
7. The agent eventually calls `skill_feedback(skill_id, applied, note)`.
8. `record_feedback` calls `usage_repo.get_latest_for_skill_instance(skill_id, instance_id)` (`repository.py:1126`) → returns `None`.
9. The tool prints `⚠️ No usage record found for skill <short_id>... Feedback cannot be applied.` (`skill_tools.py:672-674`).

### Why this is not a Postgres/SQLite mismatch

The hypothesis "instance messages should read dev postgres db (.env)" was raised on the report. Both producer (`SkillMetricsService.record_task_completion` → `_instance_repository.get/set_metadata`) and consumer (`skill_feedback` → `_get_project_id` → `_instance_repository.get`, plus `record_feedback` → `usage_repo`) read from the **same** `manager._engine` selected by `EnsembleConfig.is_postgres` in `daemon/manager.py:538-543`:

```python
if self._ensemble_config is not None and self._ensemble_config.is_postgres:
    self._engine = create_postgres_engine(self._ensemble_config)
else:
    db_config = DatabaseConfig.sqlite(db_path=str(self.db_path))
    self._engine = create_engine_from_config(db_config)
```

All repositories (instance, skill, usage) are constructed off `self._engine`. The `POSTGRES_*` env vars are honored once and only once, at manager init. There is no separate "dev postgres DB" for instance messages vs metrics — the bug reproduces identically on SQLite and Postgres deployments.

The only side-channel engine is `self._opencode_engine` (`manager.py:765`), which exists solely for the `opencode_sessions` table and never touches the skill/instants schema.

The actual dev/SQLite-vs-Postgres failure mode people see is the same bug, not a separate one: empty `last_injected_skill_ids` metadata on whatever backend is active.

### Why the integration test passed

`tests/integration/test_skill_cross_phase_flow_a.py:604-608` mirrors what production is supposed to do:

```python
instance_repo.set_metadata(
    instance_id,
    "last_injected_skill_ids",
    list(injected_skill_ids),
)
```

The test never calls `track_injection` (the production path); it bypasses the integration service entirely and writes metadata directly. So the test exercises `record_task_completion`'s reader, but not the writer side of the flow. CI looks green while production silently drops every usage record.

### The TODO that was never closed

`skill_injection_service.py:54-56` documents the intent in the class docstring:

> *"Tracking. Stores `{instance_id: {message_id: [skill_ids]}}` in memory so the Phase 4 metrics service can attribute a future feedback signal back to the skills that were offered for the task. The in-memory dict is intentionally lightweight — **Phase 4 will refresh it from the `skills.last_injected` metadata key when persisting**."*

Phase 4 was implemented (`SkillMetricsService`) and reads the metadata — the persistence step on the producer side was never added.

---

## Symptoms (what the user sees)

1. `skill_feedback` tool always returns the soft-fail warning in production, including for skills that were obviously injected into the prompt and applied by the agent.
2. `SkillRepository` counters (`total_selections`, `total_applied`, `total_completions`, `total_fallbacks`, `consecutive_failures`) never advance — they stay at the seed/test values.
3. The Phase 5 CAPTURED eligibility gate (`_check_capture_eligibility`) is unaffected by feedback because it queries `usage_repo.has_applied_for_instance` — but with no usage rows at all, the gate often takes the cheap no-op path for a different reason (the skill was injected but never recorded).
4. A/B test `comparisons` counter never accumulates from the `record_feedback` path (only `increment_comparison` in `_select_ab_variant` advances it), so the engine reports `needs_more_data: false` and `comparisons: 0` indefinitely.
5. Warning is logged once per missing record:

   ```
   SkillMetricsService.record_feedback: no usage record found for skill=<id>, instance=<id>
   ```

---

## Reproduction

Easiest deterministic reproduction (sandbox / dev mode):

1. Start the daemon (SQLite or Postgres; behavior is identical).
2. Create an instance on the `developer` agent, project with `skill_injection: true`.
3. Send a user message that the skill search resolves to a real skill (e.g. a trigger with a high-priority regex).
4. Wait for the task to complete.
5. In the same session, call the `skill_feedback` tool with the skill ID returned in the injection block.
6. Observe:

```
⚠️ No usage record found for skill <short_id>... Feedback cannot be applied.
```

7. Inspect the instance row in `instances.instance_metadata`:
   - SQLite: `sqlite3 ./data/ensemble.db "select metadata from instances where id='<id>';"`
   - Postgres: `select metadata from instances where id='<id>';`
8. The key `last_injected_skill_ids` is absent (or empty) — confirming the missing write.

The Phase 4 reader side is correct; the bug is the missing Phase 3 producer-side write.

---

## Solution

Add the missing persistence call in `daemon/services/instance_messaging.py`, immediately after `track_injection(...)` returns. Mirror the pattern the integration test uses, with merge-with-existing to handle multiple injections per task (e.g. retries or re-emits of the same user message).

### Patch sketch

In `daemon/services/instance_messaging.py`, around line 1809:

```python
# Existing in-memory tracking (Phase 4 attribution source #1)
injection_service.track_injection(
    instance_id,
    message_id,
    injected_skill_ids,
)

# NEW: persist to instance metadata so SkillMetricsService.record_task_completion
# can read injected skill IDs back at task-completion time. Mirrors the
# pattern in tests/integration/test_skill_cross_phase_flow_a.py:604.
if injected_skill_ids:
    try:
        INJECTED_SKILLS_KEY = "last_injected_skill_ids"  # reuse the constant
        def _merge_injected() -> list[str]:
            cur = self._manager._instance_repository.get(instance_id)
            existing: list[str] = []
            if cur is not None and cur.instance_metadata:
                raw = cur.instance_metadata.get(INJECTED_SKILLS_KEY) or []
                if isinstance(raw, list):
                    existing = [str(x) for x in raw if x]
            merged = list(dict.fromkeys(existing + list(injected_skill_ids)))
            self._manager._instance_repository.set_metadata(
                instance_id, INJECTED_SKILLS_KEY, merged,
            )
            return merged
        await asyncio.to_thread(_merge_injected)
    except Exception as e:
        logger.warning(
            f"Failed to persist last_injected_skill_ids for "
            f"{instance_id[:8]}...: {e}"
        )
```

Import the constant from the metrics service rather than hard-coding the
string in two places:

```python
from .skill_metrics_service import INJECTED_SKILLS_METADATA_KEY
```

The merge step (`dict.fromkeys(...)`) preserves insertion order and dedupes
so that a task with multiple injections accumulates unique skill IDs
instead of clobbering earlier ones.

### Why merge instead of overwrite

`InstanceRepository.set_metadata` (`daemon/repositories/instance/repository.py:655`) is a single-statement dialect-aware UPDATE that replaces the top-level key. A naïve call would clobber any previously persisted set on the same instance — which happens when the same task injects twice (e.g. a resume that re-emits the user message, or a sub-task that runs injection independently).

The merge-then-set strategy preserves the semantics expected by `record_task_completion`:
1. Read existing `last_injected_skill_ids`.
2. Union with the newly injected IDs.
3. Write back.

For a single-inject task (the common case) the result is identical to a plain overwrite.

### Verification checklist

1. **Unit-level regression test** (must fail before the fix):
   - Drive `instance_messaging._process_message_with_tracking` end-to-end against an in-memory SQLite engine.
   - Assert `instance_metadata["last_injected_skill_ids"]` is set after a successful injection.
   - Call `skill_feedback` and assert the response contains `"Feedback recorded"` (not `"No usage record found"`).
2. **Multiple-inject merge test**: send two messages that both inject skills (or trigger the same code path twice on one task) and assert the second persistence preserves the first set via union.
3. **Restart test**: write the metadata, restart the daemon, complete a task, and assert `record_task_completion` still finds the IDs and `skill_feedback` still records.
4. **Counter regression**: assert `SkillRepository.total_applied` advances after a positive `skill_feedback`.
5. **CAPTURED-flow regression**: with no `skill_injection` applied, the gate should NOT short-circuit on the absence of rows incorrectly. Existing tests in `tests/services/test_skill_metrics_service.py` cover this and should continue to pass.

---

## Follow-ups

1. **Drop the in-memory cache as the single source of truth.**
   Once persistence is reliable, `SkillInjectionService._injected_skills` becomes redundant (the comment at `skill_injection_service.py:142-146` already calls this out: *"Not persisted — a daemon restart drops the cache."*). Either remove it or rehydrate from metadata on daemon startup so the in-memory dict is a derived view.
2. **Backfill is unnecessary** — the data was never persisted before, so there are no orphan rows. Old feedback signals cannot be retroactively applied.
3. **Logging diagnostics**: `record_feedback` logs `WARNING: no usage record found for skill=..., instance=...` today (`skill_metrics_service.py:771`). Extend the log line to include the daemon's view of `record_task_completion`'s `last_injected_skill_ids` (`len(injected_ids)`) so operators can disambiguate "no skill was ever injected" from "injection happened but persistence was skipped" in the next incident of this class.
4. **Test-coverage gap**: `tests/integration/test_skill_cross_phase_flow_a.py` covers `record_task_completion`'s read path but uses a manual `set_metadata` to seed it. Add an additional test that drives the full `instance_messaging` → `track_injection` path without any manual `set_metadata`, so that the missing-persistence bug is caught at the right layer.
5. **Constant location**: today the metadata-key string lives in `daemon/services/skill_metrics_service.py:85` as `INJECTED_SKILLS_METADATA_KEY`. After the fix, both the producer (`instance_messaging`) and the consumer (`skill_metrics_service`) need it — keep it in one place and import rather than re-declaring.

---

## Why "Look like a bug, bad design" is the right framing

The design itself relies on two independent storage paths
(in-memory dict vs persistent metadata) for the same fact, with no
producer-side enforcement that the persistent path is kept up to date.
The integration test happened to exercise the persistent path directly
and pass, masking that the in-memory path never feeds it. The fix is a
single producer-side write; the structural concern is that future
Phase 5 / Phase 6 contributors could re-introduce the same drift unless
the constant + helper live in one place and both sides reference them.

---

## Affected code paths

| Path | Direction | Effect |
|------|-----------|--------|
| `instance_messaging.py → SkillInjectionService.inject_skills` | Producer | Returns `(injection_text, injected_skill_ids)`. |
| `instance_messaging.py:1809 track_injection` | Producer | In-memory only. |
| **`instance_messaging.py → instance_repo.set_metadata(last_injected_skill_ids, ...)`** | **Producer (MISSING)** | **The bug.** |
| `job_queue_service.py:1844 record_task_completion` | Consumer | Reads metadata, finds it empty, returns 0. |
| `skill_feedback tool` | Surface error | Prints the soft-fail warning to the agent loop. |
| `skill_metrics_service.py:771 log WARNING` | Telemetry | Single WARNING per failed feedback; correlated with instance ID. |

---

## Related

- `docs/skill-evolution.md` — overall design.
- `docs/skill-evolution-config.md` — Phase 4/5 config knobs (no change required for this fix).
- `tests/integration/test_skill_cross_phase_flow_a.py` — happy-path integration test (currently green only because it bypasses the in-memory path; will continue to pass after the fix).
- `daemon/services/skill_metrics_service.py:48-52` — explicit design note that the metadata key is the source of truth cleared after recording.
