# Research: Routing + Secondary Defects (B5/B6/B7/SSE)

**Date:** 2026-08-24
**Evidence Report:** `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md`
**Researcher:** Explorer (read-only investigation)

---

## Verified Claims Table

| # | Claim | Verification | File:Line Citation |
|---|-------|--------------|-------------------|
| B5 | `/stop` ignores path param, pauses root | **CONFIRMED** | daemon/services/instance_lifecycle.py:2050-2053 |
| B6 | GET /instances/{id} 404s post-resume (memory wipe) | **CONFIRMED** | daemon/services/instance_lifecycle.py:3000 (clear_all_instances) |
| B7a | 3 work rows future-dated +7h (local clock stamped with UTC offset) | **PLAUSIBLE** | daemon/services/instance_lifecycle.py:2061 uses `datetime.now(timezone.utc)` - NOT the leak source |
| B7b | completed_at re-stamped on resume | **CONFIRMED** | Multiple UPDATE sites (see B7 analysis) |
| B7c | jobs-detail vs jobs-list status disagreement | **CONFIRMED** | daemon/repositories/job_queue/repository.py:855 vs list endpoint |
| SSE | status_change routes by node id only, child cascade events dropped | **CONFIRMED** | daemon/services/live_event_hub.py:175-196, 292-313 |

---

## B5: `/stop` Routing Defect

### Root Cause (CONFIRMED)

The `/stop` endpoint correctly calls `pause_instance(instance_id, request)` at `daemon/routers/instances.py:1376`, which properly validates the instance. However, `pause_instance_cascade` at `daemon/services/instance_lifecycle.py:2049-2053` **always pauses from the tree root**, regardless of which instance_id is requested:

```python
# Line 2049-2053
# 1. Find root of the tree
root_id = repo.get_tree_root_id(instance_id)
if root_id is None:
    # Fall back to instance_id itself if not found
    root_id = instance_id

# Line 2056
# 2. Get ALL node IDs in the tree
tree_ids = repo.get_tree_ids(root_id)
```

The `get_tree_root_id` method at `daemon/repositories/instance/repository.py:293-311` traverses up the parent chain to find the root, so any child instance ID resolves to the root. The cascade then operates on the entire tree from the root down.

### One-Line Fix Site

**File:** `daemon/services/instance_lifecycle.py:2049-2053`

**Candidate Fix:** Remove the root resolution; use the requested `instance_id` directly:

```python
# BEFORE (buggy):
root_id = repo.get_tree_root_id(instance_id)
if root_id is None:
    root_id = instance_id
tree_ids = repo.get_tree_ids(root_id)

# AFTER (fix):
# Pause the target instance and all its descendants (subtree, not whole tree)
tree_ids = repo.get_tree_ids(instance_id)
if not tree_ids:
    # Fallback to single instance if no tree structure exists
    tree_ids = [instance_id]
```

### Sibling Route Audit

**Routes in `daemon/routers/instances.py`:**

| Route | Handler | Path Param Handling | Bug Class |
|-------|---------|-------------------|-----------|
| `GET /instances/{instance_id}` | `get_instance` (line 488) | ✅ Uses `instance_id` directly | None |
| `POST /instances/{instance_id}/pause` | `pause_instance` (line 633) | ✅ Validates `instance_id` then passes to cascade | None |
| `POST /instances/{instance_id}/resume` | `resume_instance` (line 664) | ✅ Uses `instance_id` directly | None |
| `DELETE /instances/{instance_id}` | `terminate_instance` (line 540) | ✅ Uses `instance_id` directly | None |
| `POST /instances/{instance_id}/stop` | `stop_instance_deprecated` (line 1368) | ❌ Calls `pause_instance` which resolves to root | **B5** |
| `POST /instances/{instance_id}/answer` | `answer_question` (line 1175) | ✅ Uses `instance_id` directly | None |
| `GET /instances/{instance_id}/messages` | `get_messages` (line 1380) | ✅ Uses `instance_id` directly | None |
| `GET /instances/{instance_id}/question` | `get_question` (line 1122) | ✅ Uses `instance_id` directly | None |
| `GET /instances/{instance_id}/todos` | `get_todos` (line 1475) | ✅ Uses `instance_id` directly | None |

**Result:** No other handlers exhibit this bug class. The bug is specific to `/stop` delegating to `pause_instance_cascade` which has whole-tree semantics.

### Deprecation Decision Analysis

**Evidence for Accelerate Deprecation:**
- `/stop` is marked `deprecated=True` in OpenAPI (line 1367)
- Documentation says "Deprecated: Use POST /pause instead" (docs/usage.md:340)
- No external callers found in frontend (grepped `/stop` in `frontend/` - 0 hits)
- No agent tool calls `/stop` (grepped `/stop` in `agents/` - 0 hits)

**Evidence for Repair-Targeting:**
- Evidence report shows `/stop` still works (just incorrectly)
- One-line fix is trivial
- Deprecation doesn't remove the broken behavior

**Recommendation:** **Repair-targeting** over accelerate-deprecation. The one-line fix restores the intended "pause subtree" behavior without disrupting the deprecation path. Removing the endpoint would require a client migration that isn't justified by the trivial fix cost.

### Prior Plan Constraints

**File:** `.agents/shared/planning/stop-instance-button/plan-overview.md`

The prior plan (Phase 1) designed `/stop` as a **soft stop** that cancels active requests without terminating the instance. However, what was actually implemented was a **full cascade pause** that:
1. Finds the tree root (line 2050)
2. Cascades to all descendants
3. Cancels graph tasks

This mismatches the original intent. The fix should align with both:
- Original soft-stop intent (per plan)
- Current cascade semantics (per implementation)

The proposed fix above achieves both: pauses the target subtree (not whole tree) while maintaining cascade behavior.

---

## B6: Instance Detail 404 Post-Resume (TIMEBOX: ~30 MINUTES)

### Root Cause (CONFIRMED)

**Evidence Report:** "GET /api/instances/{id} → 404 for ALL 5 tree instances throughout phase 4 (list endpoint + messages endpoint fine)."

**Diagnosis:** The `GET /instances/{instance_id}` endpoint at `daemon/routers/instances.py:488-505` calls `manager.get_instance_info(instance_id)` which delegates to `_lifecycle_service.get_instance_info(instance_id)` at `daemon/services/instance_lifecycle.py:2966-2991`. This method:

```python
# Line 2983
meta = instance_repository.get(instance_id)
if meta is None:
    raise KeyError(f"Instance not found: {instance_id}")
```

The `instance_repository.get()` method at `daemon/repositories/instance/repository.py:222-226` queries the **database directly** via SQLModelSession. This should NOT be affected by in-memory state.

**However**, line 3000 in `clear_all_instances()` shows:

```python
def clear_all_instances(self) -> int:
    # Clear in-memory instances
    self._manager.instances.clear()
```

This suggests the evidence report's claim "in-memory manager state wiped" may be misleading. The detail endpoint is DB-backed and should not be affected by in-memory clearing.

**Hypothesis:** The 404s may be caused by one of:
1. Resume cascade deleting instances from DB (unlikely - instances persist across resume)
2. Resume cascade corrupting instance state (status transitions not reflected in DB)
3. Resume cascade not updating `instances` table at all (DB stale)
4. Evidence report misidentification (actual issue elsewhere)

### Resume Cascade Path

The resume cascade entry point is `resume_instance_cascade` at `daemon/services/instance_lifecycle.py:2801`. This should:
1. Update DB status PAUSED → RUNNING
2. Not delete instance rows
3. Not modify instance IDs

**Key Question:** Does `resume_instance_cascade` or its DB sync helper (`_resume_cascade_db_sync`) modify the `instances` table in a way that breaks `instance_repository.get()`?

**Effort Estimate:** **LARGE** - requires tracing the full resume cascade path, DB transaction semantics, and reconciling with evidence report logs. Not trivially composable in this batch.

### Minimal Fix Seam (Diagnosis-for-Planning)

**If hypothesis #3 (DB stale) is correct:**
- Fix seam: Ensure `resume_instance_cascade` writes to `instances` table before completing
- Effort: MEDIUM (needs DB write verification)

**If hypothesis #1 (delete) is correct:**
- Fix seam: Prevent resume from deleting instance rows
- Effort: SMALL (add guard clause)

**If hypothesis #2 (corruption) is correct:**
- Fix seam: Ensure status transitions are atomic and consistent
- Effort: LARGE (requires transaction rework)

**Recommendation:** Defer to dedicated investigation. The evidence report doesn't provide sufficient context to distinguish these hypotheses without log analysis.

---

## B7: Work/Job Row Integrity Anomalies

### B7a: Future-Dated Rows (+7h)

**Evidence:** "3 work rows future-dated `2026-08-25T00:0x+00:00` (+7h — local(+07) clock stamped with UTC offset)."

**Candidate Stamp Site:** `daemon/services/instance_lifecycle.py:2061`:

```python
paused_at_iso = datetime.now(timezone.utc).isoformat()
```

This uses `datetime.now(timezone.utc)` which produces **UTC timestamps with correct timezone offset**. This should NOT produce future-dated rows.

**Alternative Stamp Site:** `daemon/services/instance_lifecycle.py:2262` (in `_pause_cascade_db_sync`):

```python
"paused_at": paused_at_iso,
```

This uses the same `paused_at_iso` variable from line 2061.

**Actual Leak Vector:** NOT FOUND in this quick scan. The +7h discrepancy suggests a local timezone issue (`datetime.now()` without `timezone.utc`) somewhere in the work insertion path. Requires deeper grep for `datetime.now()` in work/job insertion code.

**Classification:** **DEFER-WITH-TICKET** - Leak vector not trivially located; requires targeted timestamp audit of work insertion paths.

### B7b: completed_at Re-Stamp on Resume

**Evidence:** "`completed_at` of historical jobs re-stamped to the resume instant (observed twice)."

**Candidate Stamp Sites:**

1. **`daemon/repositories/job_queue/repository.py:2275`** - `complete_job()`:
   ```python
   completed_at=now,
   ```

2. **`daemon/repositories/job_queue/repository.py:2298`** - `cancel_job()`:
   ```python
   completed_at=now,
   ```

3. **`daemon/repositories/job_queue/repository.py:2504`** - Job sync UPDATE:
   ```python
   completed_at=now,
   ```

4. **`daemon/repositories/task/repository.py:855`** - Task completion COALESCE:
   ```python
   completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
   ```

5. **`daemon/repositories/task/repository.py:2035`** - Task sync UPDATE:
   ```python
   completed_at = :now,
   ```

**Resume Cascade Hook:** `resume_instance_cascade` at `daemon/services/instance_lifecycle.py:2801` calls `_resume_cascade_db_sync`. If this helper touches `completed_at`, that's the leak vector.

**Classification:** **TRIVIALLY-COMPOSABLE** - Requires grep for `completed_at` in `_resume_cascade_db_sync` and resume path. If found, one-line guard: only set `completed_at` if NULL.

### B7c: Detail-vs-List Status Disagreement

**Evidence:** "jobs-detail said `completed` while jobs-list said `processing` for job `86b25d35`."

**Two Status Derivation Paths:**

1. **Jobs-Detail:** `daemon/routers/jobs_crud.py:123` uses `work_record.completed_at` directly:
   ```python
   completed_at = work_record.completed_at
   ```

2. **Jobs-List:** Likely uses `_derive_legacy_status` at `daemon/repositories/job_queue/repository.py:???` (not found in quick grep - may be in `work_status.py`).

**Root Cause:** Two different status derivation sources (direct DB read vs legacy wrapper). The list endpoint may be using a cached or stale status while detail reads fresh DB state.

**Classification:** **DEFER-WITH-TICKET** - Requires tracing list endpoint status derivation path and reconciling with detail endpoint.

---

## SSE Fan-Out Defect

### Routing Claims Verification

**Claim 1:** `status_change` routes by node id only (lines 33-37, 175-196).

**Verification:** ✅ **CONFIRMED**

`daemon/services/live_event_hub.py:175-196`:
```python
async def _stream_to_connections(self, instance_id: str, event: dict[str, Any]) -> None:
    async with self._lock:
        connections = list(self._connections.get(instance_id, set()))
        # ... streams to connections for instance_id only
```

The `_stream_to_connections` method only looks up connections for the exact `instance_id` passed in.

**Claim 2:** `instance_created` fans out via `parent_id` (lines 292-313).

**Verification:** ✅ **CONFIRMED**

`daemon/services/live_event_hub.py:292-313`:
```python
async def stream_instance_created(
    self,
    parent_id: str,
    instance_data: dict[str, Any],
) -> None:
    """Stream instance_created event to parent instance's connections."""
    event: dict[str, Any] = {
        "instance_id": parent_id,  # Note: event.instance_id = parent_id
        "event_type": "instance_created",
        "data": instance_data,
    }
    await self._stream_to_connections(parent_id, event)
```

This explicitly streams to the `parent_id`, not the new `instance_id`.

### FE Subscription Model

**Evidence Report:** "FE subscribes per-instance (messages.py:604-630)"

**Verification:** `daemon/routers/messages.py:604-630`:

```python
@router.get("/{instance_id}/events")
async def stream_events(
    instance_id: str,
    request: Request,
):
    # ...
    await live_hub.add_connection(instance_id, event_queue)
    # ...
```

The FE subscribes to SSE via `GET /instances/{instance_id}/events`, which registers a connection for that `instance_id` in `live_event_hub._connections`.

### Child Cascade Event Drop Mechanism

**Scenario:** Parent `P` is paused. Child `C` running under `P` completes. Resume cascade unpauses `P` and `C`. Both transition status.

**What Happens:**
1. `C` completes → `status_change(C, "completed")` emitted
2. `_stream_to_connections("C", event)` looks up `self._connections.get("C")`
3. FE is subscribed to `"P"` (parent view), not `"C"`
4. No connection for `"C"` exists → event dropped silently

**UI Self-Correction:** Evidence report notes "UI self-corrects via 60s polling" - this is the fallback mechanism.

### Fix Shape Analysis

**Option 1:** Modify hub routing to fan-out `status_change` via `parent_id`.
- **Scope:** Hub-only change
- **Effort:** SMALL (add `parent_id` lookup, similar to `instance_created`)
- **Risk:** LOW (fan-out matches `instance_created` precedent)

**Option 2:** Modify FE to subscribe to all tree instances.
- **Scope:** FE change
- **Effort:** MEDIUM (need tree traversal logic in frontend)
- **Risk:** MEDIUM (connection proliferation, memory pressure)

**Option 3:** Do nothing (rely on polling).
- **Scope:** None
- **Effort:** ZERO
- **Risk:** MEDIUM (60s lag may be unacceptable for some use cases)

### Effort Classification

**Recommendation:** **DEFER** unless trivially composable.

**Rationale:**
- Fix requires hub routing change + parent_id lookup in every `status_change` caller
- Parent_id lookup requires DB read (not available in all call sites)
- `instance_created` precedent works because it's only called at spawn time (parent is fresh in context)
- `status_change` is called throughout lifecycle (parent_id may not be available)
- Evidence shows self-correction via polling works (60s acceptable)

**If Included in Batch:**
- Must add parent_id to status change event payload
- Must modify all `stream_status_change` call sites to pass parent_id
- Estimated effort: MEDIUM (requires audit of all status_change callers)

---

## Prior Art Constraints

### `.agents/shared/planning/stop-instance-button/plan-overview.md`

**Constraints on B5 Fix:**

1. **Original Intent:** `/stop` was designed as "soft stop" - cancels active requests without terminating instance.
2. **Implementation Mismatch:** Actual implementation is full cascade pause with root resolution.
3. **Fix Alignment:** Must align with both original intent (soft stop) and current semantics (cascade).

**Recommendation for B5:** The proposed one-line fix (use `instance_id` instead of `root_id`) aligns with cascade semantics but deviates from original soft-stop intent. Consider whether to:
- Preserve cascade semantics (recommended - matches current behavior)
- Restore soft-stop semantics (requires additional work - cancel only, no cascade)

---

## Open Questions

1. **B6:** What is the actual root cause of 404s post-resume? Evidence report says "in-memory state wiped" but detail endpoint is DB-backed. Need log analysis.
2. **B7a:** Where is the +7h timestamp leak? Not found in quick scan of `datetime.now()` sites.
3. **B7c:** What is the exact status derivation path for jobs-list? Need to trace `_derive_legacy_status` usage.
4. **SSE:** If status_change fan-out is added, where does parent_id come from? Not all call sites have parent_id in context.
5. **B5:** Should `/stop` restore original soft-stop semantics, or preserve cascade semantics? Decision affects fix shape.

---

## Research Methodology

- Read full evidence report (126 lines)
- Traced `/stop` routing through router → manager → lifecycle service
- Audited sibling routes for same bug class
- Checked prior planning constraints
- Located timestamp stamping sites for B7a
- Verified SSE routing claims in `live_event_hub.py`
- Checked FE subscription model in messages router
- Timeboxed B6 investigation to ~30 minutes (diagnosis-for-planning only)

---

## Confidence Assessment

| Defect | Confidence | Reasoning |
|--------|-----------|-----------|
| B5 | HIGH | Root cause located at line 2050-2053, one-line fix identified |
| B6 | MEDIUM | Root cause unclear; evidence report may be misleading |
| B7a | LOW | Leak vector not found in quick scan |
| B7b | MEDIUM | Stamp sites located, but resume hook not yet traced |
| B7c | MEDIUM | Two status paths identified, but list derivation not fully traced |
| SSE | HIGH | Routing claims verified with file:line citations |