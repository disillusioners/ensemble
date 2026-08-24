# Plan Overview: Phase 3 — Pause/Resume/Terminate Tree-Propagation: B5 (Router) + B6 (Detail 404) + B7 (Timestamps) + SSE (Assessment)

Date: 2026-08-24
Author: planner[v2] via plan-creation worker
Status: Draft (B5 carries an [ARCHITECT] flag — see §Architect Flags; B6 carries a [TIMEBOXED] flag)
Branch: feature/pause-resume-terminate-tree-fix @ worktree head (parallel with Phase 1 + Phase 2 workers)
Bug Source: `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md` (live-repro evidence, 6 phases)
Research: `.agents/shared/planning/pause-resume-terminate-tree-fix/research-routing.md` (read-only investigation, B5/B6/B7/SSE scope)
Prior Plan: `.agents/shared/planning/stop-instance-button/plan-overview.md` (soft-stop design intent — superseded by cascade-pause implementation)

---

## Defects In Scope

| # | Defect | Severity | Live Evidence | Action |
|---|--------|----------|---------------|--------|
| **B5** | `/stop` ignores path param, pauses project ROOT instead of target subtree | 🟠 important | `phase 5`: `POST /api/instances/c83b46cd-…/stop` → `200 {"paused":true,"paused_ids":["f5e223f1-…"]}`; log 2349 confirms `Pausing instance f5e223f1` immediately after `POST …/c83b46cd…/stop` (2350) | **FIX** — [ARCHITECT] review |
| **B6** | `GET /api/instances/{id}` → 404 for ALL 5 tree instances post-resume | 🟠 important | `phase 4`: list + messages endpoints fine; detail 404 throughout the phase; reproducer isolated this to resume cascade interaction | **TIMEBOXED DIAGNOSIS** — hard 2–4h cap; fix-or-ticket exit |
| **B7(b)** | `completed_at` re-stamped on resume (observed twice) | 🟢 nice-to-have | evidence report §B7 (b); candidate stamp sites at `daemon/repositories/job_queue/repository.py:2275, 2298, 2504` | **FIX** — trivial COALESCE guard |
| **B7(a)** | 3 work rows future-dated `+7h` (local clock stamped with UTC offset) | 🟢 nice-to-have | evidence report §B7 (a); leak vector NOT located in quick scan (`datetime.now(timezone.utc)` correct at `instance_lifecycle.py:2061`) | **ASSESSMENT ONLY** — ticket for next batch |
| **B7(c)** | jobs-detail said `completed` while jobs-list said `processing` for `86b25d35` | 🟢 nice-to-have | evidence report §B7 (c); two status derivation paths (detail direct DB read vs `_derive_legacy_status`) | **ASSESSMENT ONLY** — ticket for next batch |
| **SSE** | `status_change` routed by node id only; child cascade events dropped for parent subscribers; FE self-corrects via 60s polling | 🟡 low-medium | evidence report §SSE; `live_event_hub.py:175-196` (status_change) vs `live_event_hub.py:292-313` (instance_created via parent_id); FE subscribes per-instance at `messages.py:604-630` | **ASSESSMENT ONLY** — ticket for next batch |

**OUT of scope:** B1 (Phase 1 lineage), B2 (Phase 2 obligation), B3 (Phase 2 obligation), B4 (Phase 1 lineage). This phase assumes Phase 1 + Phase 2 fixes are merging in the same worktree.

---

## Objective

Phase 3 closes the **router defect (B5)**, ships a **trivial data-integrity guard (B7(b))**, runs a **timeboxed diagnosis (B6)**, and produces **assessment-only follow-up tickets** for the remaining secondary defects (B7(a), B7(c), SSE). The plan respects the project's hard constraints (canonical terminal reasons, dependency-bus SOLE completion authority, pause writes NOTHING to JobItems, named transitions + `reconcile_turn_mirror(work_id)` authoritative, revive semantics intact), does not gold-plate secondary defects, and never scope-creeps into B6 territory beyond the diagnosis cap.

**Testable completion sentence:** A user calling `POST /api/instances/{mid_tree_child}/stop` receives `200 {"paused":true,"paused_ids":[mid_tree_child, …descendants of mid_tree_child]}` (target subtree, NOT the project root); after a resume cascade, `completed_at` for historical jobs is preserved (no re-stamp); the B6 diagnosis either shipped a small fix or produced a documented ticket; B7(a), B7(c), and SSE each have a follow-up ticket with repro + suspected sites + effort class.

---

## Verified Mechanics (re-cited from research-routing + code re-verification)

All citations re-verified against the worktree at branch head before writing this document. Page/line numbers are stable.

### B5 — Router Defect

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/routers/instances.py:1366-1376` | `stop_instance_deprecated` | **BUG SITE.** `@router.post("/{instance_id}/stop", deprecated=True)`; body: `return await pause_instance(instance_id, request)`. Path param `instance_id` is passed through unchanged. |
| `daemon/routers/instances.py:631-659` | `pause_instance` | Reads `instance_id` from path, calls `manager.pause_instance_cascade(instance_id)` (line 654). Returns `{paused:true, paused_ids: result["paused_ids"], skipped_ids: result["skipped_ids"]}`. |
| `daemon/services/instance_lifecycle.py:2047-2090` | `pause_instance_cascade` | **BUG SITE (composition).** Resolves `root_id = repo.get_tree_root_id(instance_id)` (line 2050); falls back to `instance_id` (line 2053); then `tree_ids = repo.get_tree_ids(root_id)` (line 2056). **The re-root is by design** (per tree-aware-pause-resume invariant) — `/pause` semantics intentionally pause the WHOLE tree from root. |
| `daemon/repositories/instance/repository.py:293-311` | `get_tree_root_id` | Walks `parent_id` chain to find the topmost ancestor; any non-root child resolves to project root. |
| `daemon/repositories/instance/repository.py` (B4 enumeration) | `get_tree_ids` | Phase 1 fixes the lineage enumeration bug; after P1 lands, `get_tree_ids(root_id)` returns `[root, …all_descendants]`. **Before P1 lands**, `get_tree_ids(<non-root>)` returns `[<non-root>]` only (because enumeration misses permanent `parent_id` rows). |

**Composition analysis (resolved by reading the handler):**

1. `stop_instance_deprecated` (instances.py:1367-1376) delegates to `pause_instance(instance_id, request)` (line 1376) — does NOT re-root itself.
2. `pause_instance` (line 631) calls `manager.pause_instance_cascade(instance_id)` (line 654) with the path param.
3. `pause_instance_cascade` (instance_lifecycle.py:2047) re-roots via `repo.get_tree_root_id(instance_id)` (line 2050) — this is WHERE the wrong-target behavior originates.

→ **Composition answer: case (b)** — `/stop` passes the path param into a re-rooting service. The "fix" is a **SEMANTICS decision**, not a mechanical one-liner:

| Option | What `/stop X` does | Why considered | Why rejected |
|--------|---------------------|----------------|---------------|
| Whole-tree (current bug) | Pause root + all descendants | Matches `/pause` current behavior | Wrong target — user expects `X` to be paused |
| Subtree (recommended fix) | Pause `X` + `X`'s descendants | Matches user expectation; "cascade to children" semantics | Requires parameter addition or new helper; changes `/pause` if not isolated |
| Soft-stop (original stop-instance-button plan) | Cancel active request on `X`, no cascade | Matches original "stop button" intent (cancel current request, instance stays alive) | Larger change; deviates from current `/stop` implementation |

**Chosen behavior: SUBTREE** — `POST /api/instances/{X}/stop` pauses `X` + all descendants of `X` (target subtree). Rationale:
- Intuitive: user named the instance, the subtree rooted at that instance pauses.
- Cascade behavior preserved: descendants still get cancelled.
- Minimal behavior change from current `/pause` semantics: only the entry-point changes (subtree vs whole tree).
- Compatible with Phase 1 lineage fix: after P1 lands, `get_tree_ids(X)` correctly enumerates `[X, …X's descendants]`.

**Isolation strategy:** Do NOT modify `pause_instance_cascade` (would silently change `/pause` semantics, which has been the long-standing whole-tree behavior). Instead, add a new parameter `cascade_to_root: bool = True` (default = current whole-tree semantics, preserves `/pause` compatibility) and call with `cascade_to_root=False` from the `/stop` handler. **OR** (cleaner): add a new public helper `pause_subtree_cascade(instance_id)` and route `/stop` to it. See Tasks 3.1–3.2.

### B7(b) — `completed_at` Re-Stamp

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/repositories/job_queue/repository.py:2260-2277` | `complete_job` | `now = datetime.now(timezone.utc).isoformat(); atomic_transition(job_id, from_status="processing", to_status="completed", completed_at=now, …)` — **BUG SITE**: unconditional `completed_at=now` would re-stamp if re-finalized |
| `daemon/repositories/job_queue/repository.py:2292-2301` | `fail_job` | Same pattern: `completed_at=now` on PROCESSING → FAILED |
| `daemon/repositories/job_queue/repository.py:2497-2506` | `terminate_job` (cancel-from-terminal) | Same pattern: `completed_at=now` on PROCESSING → CANCELLED |
| `daemon/services/instance_lifecycle.py:3698-3856` | `_resume_cascade_db_sync` | **Verified: does NOT touch `completed_at`.** UPDATE at `:3783-3798` only flips `status`, `paused_at`, `updated_at` on `instances`. Task PAUSED→PENDING via `ResumeTurn` at `:3821-3826` does NOT touch `completed_at` (per the docstring at `:3721-3723` — explicit design decision). |
| `daemon/repositories/task/repository.py:855` | Task completion COALESCE | `completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)` — **THE PATTERN TO FOLLOW** (Task table already does this). JobItem table does NOT. |
| `daemon/repositories/task/repository.py:2035` | Task sync UPDATE | `completed_at = :now` — Task table also has unconditional stamp sites (out of scope; same risk; ticket for follow-up) |

**Verified leak vector:** `_resume_cascade_db_sync` itself does NOT stamp `completed_at`. The re-stamp must come from a downstream reconciliation or re-finalization path that calls `complete_job` / `fail_job` / `terminate_job` again on a row that was already finalized. The exact caller was not isolated in the research window, but the fix is identical regardless: **guard the unconditional stamp with COALESCE** so a re-finalization does not overwrite history.

**Verified F9 re-arm path:** `grep` for `rearm` / `_rearm` / `rearm_with_lock` / `atomic_retry done→active` returned ZERO matches in the daemon. F9 is documented as DEFERRED in `.agents/tester/QUARANTINE.md` (F9 still open) — no production code path relies on overwriting `completed_at`. **The COALESCE guard is safe.**

### B6 — Detail 404 Post-Resume (diagnosis stage only)

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/routers/instances.py:488-505` | `get_instance` handler | `manager.get_instance_info(instance_id)` → `_lifecycle_service.get_instance_info(instance_id)` |
| `daemon/services/instance_lifecycle.py:2966-2991` | `get_instance_info` | `meta = instance_repository.get(instance_id)` (line 2983); raises `KeyError(f"Instance not found: {instance_id}")` if `meta is None` |
| `daemon/repositories/instance/repository.py:222-226` | `instance_repository.get` | Direct SQLModelSession query — **DB-backed, NOT in-memory**. Evidence report's "in-memory wipe" hypothesis does NOT hold. |
| `daemon/services/instance_lifecycle.py:3000` | `clear_all_instances` | `self._manager.instances.clear()` — clears in-memory cache; does NOT delete DB rows. This is a red herring. |
| `daemon/services/instance_lifecycle.py:3698-3856` | `_resume_cascade_db_sync` | UPDATE at `:3783-3798` only modifies `status`, `paused_at`, `updated_at` on `instances` — does NOT delete rows. |

**Verified hypotheses (from research, not yet reproduced):**
1. Resume cascade deleting instances from DB (unlikely — instances persist across resume)
2. Resume cascade corrupting instance state (status transitions not reflected in DB)
3. Resume cascade not updating `instances` table at all (DB stale)
4. Evidence report misidentification (actual issue elsewhere)

**Effort class:** LARGE per research (full resume cascade trace required). Phase 3 budgets this as **timeboxed diagnosis** (2–4h hard cap) — see Tasks 3.4–3.6.

### SSE — Status Change Fan-Out

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/services/live_event_hub.py:175-196` | `_stream_to_connections` | Routes by `instance_id` only — connections for `C` look up `_connections.get("C")`; FE subscribed to `P` (parent view) → `C`'s events dropped silently |
| `daemon/services/live_event_hub.py:292-313` | `stream_instance_created` | Fans out via `parent_id` — precedent for parent-aware routing, BUT only called at spawn time when parent is fresh in context |
| `daemon/routers/messages.py:604-630` | `stream_events` | FE SSE subscribe per-instance — `_connections[instance_id]` keyed by subscriber's instance |
| `live_event_hub.py:175-196` + every status_change caller | `status_change` event emit sites | Caller audit required to determine parent_id availability — NOT all call sites have parent_id in context (e.g., internal JobItem terminal transitions on unrelated instances) |

**Effort class:** MEDIUM (hub routing change + parent_id lookup at all `status_change` callers). **Self-correction precedent:** evidence report confirms FE self-corrects via 60s polling — acceptable UX today.

**Verdict:** DEFER with ticket. See §Out of Scope and Tasks 3.7–3.9 (assessment-only).

---

## B5 Composition Analysis (resolved)

This plan chooses **SUBTREE semantics** for `/stop`:

### Behavior contract (pinned by tests)

| Request | Response | Paused Ids |
|---------|----------|------------|
| `POST /api/instances/{root}/stop` | `200 {"paused":true,"paused_ids":[root, …all_descendants], "skipped_ids":[]}` | Whole tree (matches user expectation when calling on root) |
| `POST /api/instances/{mid_tree_child}/stop` | `200 {"paused":true,"paused_ids":[mid_tree_child, …descendants_of_mid_tree_child], "skipped_ids":[]}` | **Target subtree only** (NOT the project root) |
| `POST /api/instances/{leaf}/stop` | `200 {"paused":true,"paused_ids":[leaf], "skipped_ids":[]}` | Single instance |
| `POST /api/instances/{instance}/pause` | `200 {"paused":true,"paused_ids":[root, …all_descendants], "skipped_ids":[]}` | **Whole tree** (unchanged — long-standing behavior; only `/stop` changes) |

### Why isolate the change to `/stop`

- `/pause` has been documented as "pause an instance and cascade to children" — but in practice re-roots to the whole tree. Long-standing users may rely on this (whole-tree) behavior even though it's surprising on a non-root path param.
- `/stop` is documented as **DEPRECATED** (`deprecated=True` in OpenAPI at `instances.py:1367`); behavior change is low-risk because the endpoint is on a deprecation path anyway.
- No external callers found (research line 86-88 — 0 hits in `frontend/`, 0 hits in `agents/`); only manual API consumers today.

### Implementation shape (selected)

**Add a parameter to `pause_instance_cascade`:**

```python
# daemon/services/instance_lifecycle.py:2047
async def pause_instance_cascade(
    self,
    instance_id: str,
    *,
    cascade_to_root: bool = True,  # NEW — defaults preserve /pause semantics
) -> dict:
    repo = self._manager._instance_repository
    if cascade_to_root:
        # Existing whole-tree behavior (B5 bug class source, unchanged for /pause)
        root_id = repo.get_tree_root_id(instance_id)
        if root_id is None:
            root_id = instance_id
        tree_ids = repo.get_tree_ids(root_id)
    else:
        # New: target subtree only (used by /stop after fix)
        tree_ids = repo.get_tree_ids(instance_id)
    # ... rest unchanged
```

**Update `/stop` handler to pass the new flag:**

```python
# daemon/routers/instances.py:1366-1376
@router.post("/{instance_id}/stop", deprecated=True)
async def stop_instance_deprecated(instance_id: str, request: Request) -> dict:
    manager = _get_manager(request)
    if manager.is_write_paused:
        raise HTTPException(status_code=503, detail="Writes are paused for database migration")
    # B5 fix: subtree-only pause (target + descendants), NOT whole-tree re-root.
    # /pause keeps the whole-tree semantics via default cascade_to_root=True.
    result = await manager.pause_instance_cascade(instance_id, cascade_to_root=False)
    return {
        "paused": True,
        "paused_ids": result["paused_ids"],
        "skipped_ids": result["skipped_ids"],
    }
```

**Rationale for parameter over new helper:** A new helper (`pause_subtree_cascade`) would duplicate the cascade logic (loop, classification, batched UPDATE, `_pause_cascade_db_sync`, cleared-injections, SSE emission). A boolean parameter is a 3-line change with the same observable effect. **Counter-argument:** a new helper is cleaner long-term (one method per intent) — the **architect decision** is which to choose. The parameter approach is recommended here for minimal blast radius.

**Composition interaction with Phase 1:** Phase 1 fixes the `get_tree_ids` enumeration bug. After P1 lands, `repo.get_tree_ids(instance_id)` called on a non-root returns `[instance_id, …all_descendants_of_instance_id]`. BEFORE P1, it returns `[instance_id]` only. **This means the B5 fix behaves correctly ONLY after P1 lands** — sequencing note in §Coupling.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **3.1** | B5 — Add `cascade_to_root: bool = True` parameter to `pause_instance_cascade` at `daemon/services/instance_lifecycle.py:2047` | none | Parameter added with default `True`; signature passes type-checker; `pause_instance` (which calls `manager.pause_instance_cascade(instance_id)`) keeps whole-tree semantics unchanged; new branch `else: tree_ids = repo.get_tree_ids(instance_id)` added |
| **3.2** | B5 — Update `/stop` handler at `daemon/routers/instances.py:1366-1376` to call `manager.pause_instance_cascade(instance_id, cascade_to_root=False)` and return the same response shape | 3.1 | Handler no longer delegates to `pause_instance`; passes `cascade_to_root=False`; response keys (`paused`, `paused_ids`, `skipped_ids`) preserved exactly |
| **3.3** | B5 — Unit + e2e tests for `/stop` subtree semantics | 3.1, 3.2; P1 lands | See §Test Strategy — pinned test for mid-tree → subtree paused_ids content |
| **3.4** | B6 — Timeboxed diagnosis setup: stand up a controlled tree, exercise pause→resume→GET-detail, capture DB state + log timeline | P1 lands, P2 lands (post-cascade behavior stable) | Diagnosis bundle saved to `/tmp/p3-b6-diagnosis-<timestamp>/` with: (a) DB snapshots before/after resume, (b) `GET /instances/{id}` response for each tree instance post-resume, (c) full resume log slice with timestamps |
| **3.5** | B6 — Bisect the read path: trace `get_instance_info` → `instance_repository.get` → SQLModel session, check whether `instances` row exists post-resume and whether `meta.status` reflects the post-resume state | 3.4 | One of: (a) DB row missing → hypothesize + reproduce the missing-row cause; (b) DB row stale → locate the DB-sync seam that failed; (c) `get_instance_info` swallows the exception → find the raise-vs-return-None mismatch |
| **3.6** | B6 — Exit decision: fix OR ticket. Fix path is SMALL only (single seam, <100 LOC, no cascade-rework). Anything larger is a ticket. | 3.4, 3.5 | Decision recorded with evidence; either a follow-up commit (with unit test) OR a follow-up ticket in §Out of Scope with repro + suspected sites + effort class |
| **3.7** | B7(b) — Add `preserve_completed_at: bool = False` flag to `atomic_transition` at `daemon/repositories/job_queue/repository.py:1134`; when True, the UPDATE uses `completed_at = COALESCE(completed_at, :completed_at)` instead of `completed_at = :completed_at` | none | Signature updated; SQL builder branches on flag; F9 re-arm path verified not used (grep returns 0); existing callers (complete_job, fail_job, terminate_job) continue to work without changes |
| **3.8** | B7(b) — Update call sites at `daemon/repositories/job_queue/repository.py:2275, 2298, 2504` to pass `preserve_completed_at=True` | 3.7 | Three sites updated; `now` value still passed via `completed_at=now`; behavior change is conditional-only (COALESCE skips when already set) |
| **3.9** | B7(b) — Unit test: second `complete_job` call on an already-completed row does NOT update `completed_at` | 3.7, 3.8 | New unit test in `tests/unit/repositories/test_job_queue_atomic_transition.py` (or co-located) — 3 cases (complete, fail, terminate); assertion: original `completed_at` preserved after re-call |
| **3.10** | Assessment tickets — Write follow-up tickets for B7(a), B7(c), SSE (no implementation in this phase) | 3.6 (B6 diagnostic results) | See §Out of Scope — each ticket has: repro, suspected sites with file:line, effort class, recommended approach |
| **3.11** | Mandatory e2e pack runs (per `.agents/tester/rules/ensure.md`): full 5-pack suite + the 4 e2e tests called out in ensure.md:47-53 | 3.3, 3.6, 3.9 | All packs green on dev daemon (`./dev.sh`); no regressions; PYTEST_TIMEOUT=280 set per ensure.md:40; one-by-one execution per ensure.md:41; no `-x` per ensure.md:8; queue cleanup pre-flight per ensure.md:40 |
| **3.12** | Documentation note in `docs/usage.md:340` clarifying `/stop` subtree semantics | 3.2 | One-line edit: replace deprecated blurb with: "Pauses the target instance and its descendants (subtree). Use `POST /pause` for whole-tree pause." |

---

## Test Strategy

### B5 Tests (Tasks 3.3)

**Unit test** (`tests/unit/routers/test_stop_instance_subtree.py` — NEW):
- Case 1: `POST /instances/{mid_tree_child}/stop` on a tree with `[root, mid_tree_child, leaf_of_mid]` → `paused_ids == [mid_tree_child, leaf_of_mid]`, `skipped_ids == []`. **No root in paused_ids.**
- Case 2: `POST /instances/{root}/stop` on the same tree → `paused_ids == [root, mid_tree_child, leaf_of_mid]`.
- Case 3: `POST /instances/{leaf_of_mid}/stop` → `paused_ids == [leaf_of_mid]`.
- Case 4: `POST /instances/{mid_tree_child}/stop` when `mid_tree_child` is already paused → `paused_ids == []`, `skipped_ids == [mid_tree_child, leaf_of_mid]` (skipped classification unchanged).
- Case 5: `POST /instances/{nonexistent}/stop` → 404 (existing behavior preserved).
- Case 6: regression guard — `POST /instances/{mid_tree_child}/pause` (NOT /stop) still pauses whole tree. **Pins /pause semantics are unchanged.**

**E2E test** (`tests/e2e/test_stop_instance_subtree_pause.py` — NEW, modeled on `test_pause_after_spawn_then_resume`):
- Build a tree: leader → tester → worker.
- Trigger worker `sleep 60` (long enough to observe).
- `POST /instances/{tester}/stop` → confirm only `[tester, worker]` paused (leader running).
- Wait 5s, assert leader still polling / has NOT entered paused state.
- `POST /instances/{leader}/pause` (not /stop) → confirm whole tree pauses (`paused_ids == [leader, tester, worker]`).
- This proves BOTH /stop subtree semantics AND /pause whole-tree semantics unchanged.

**Regression pack** (per `.agents/tester/rules/ensure.md:47-53`):
- `test_pause_after_spawn_then_resume` (ensure.md:49) — `/pause` whole-tree semantics unchanged.
- `test_terminate_after_spawn_then_revive` (ensure.md:51) — `/stop` is unrelated to terminate.
- `test_parent_child_workflow_happy_path` (ensure.md:47) — happy path unaffected.
- `test_three_level_cascade_reports` (ensure.md:53) — cascade behavior unchanged.

### B7(b) Tests (Task 3.9)

**Unit test** (`tests/unit/repositories/test_job_queue_atomic_transition.py` — NEW):
- Case 1: `complete_job` called twice on same row — first call sets `completed_at = T1`, second call (re-finalize via identical from_status="processing", to_status="completed") → rowcount=0 (no-op, but if it DID match, `completed_at` stays `T1`). **Uses `preserve_completed_at=True`** at the test driver.
- Case 2: `fail_job` after `complete_job` — terminal_reason re-stamping test. **Conditional: only meaningful if fail_job is reachable from completed.** Skip if state machine forbids it.
- Case 3: `terminate_job` (cancel) on already-cancelled row — `completed_at` preserved.
- Case 4: regression — existing callers of `complete_job` (without `preserve_completed_at`) still stamp `completed_at` on first call. Confirms the flag is opt-in.

### B6 Diagnosis (Tasks 3.4–3.6)

**Setup (3.4):**
- Reuse repro project `09b6c42d-5865-404f-b7fa-04dbe816bb11` from `/tmp/pause-repro-20260824/state.json`.
- Build small tree (root + 2 children), pause root, resume root, capture `GET /instances/{each_id}` responses for 60s sweep.
- Log every SQL statement via SQLAlchemy echo for the resume transaction.

**Bisect (3.5):**
- Run a fresh resume, then `SELECT * FROM instances WHERE instance_id IN (<tree_ids>)` IMMEDIATELY after resume returns 200.
- If rows exist → DB-write is fine; look at `_get_instance_info` exception handler (probably swallows `KeyError` and returns 404 — verify the route at `instances.py:488-505`).
- If rows missing → bisect `_resume_cascade_db_sync` and `_status_write_guard` to find which path missed the write.
- If rows stale → check `instance_repository.get()` SELECT vs the POST-resume UPDATE.

**Exit (3.6):**
- HARD TIMEBOX: 2h effective work, 4h absolute cap (diagnose + report writing + ticket drafting).
- If a small seam is found (<100 LOC, no cascade rework): fix it, add unit test, ship in this phase.
- If a seam requires cascade rework OR >100 LOC: stop, write the ticket (Task 3.10), do NOT scope-creep.

### Mandatory E2E Pack (Task 3.11)

Per `.agents/tester/rules/ensure.md:47-53`, the full 5-pack suite applies because B5 touches the pause path (same family as cascade). Exact commands:

```bash
# Prerequisite (per ensure.md:39-40)
unset SSL_CERT_FILE SSL_CERT_DIR
export PYTEST_TIMEOUT=280

# Queue cleanup (ensure.md:40) — GET /api/jobs?status=pending, cancel any leftover before running.

# Critical packs (ensure.md:16-18)
timeout 300 bash test/packs/concurrency_atomic_unit_test.sh   # deadlock / atomic

# Release gate e2e (ensure.md:43-53) — one by one, with PYTEST_TIMEOUT=280
PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py \
    --override-ini="addopts=" --override-ini="timeout=280" -m integration \
    -k "test_parent_child_workflow_happy_path" --tb=short -q
PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py \
    --override-ini="addopts=" --override-ini="timeout=280" -m integration \
    -k "test_pause_after_spawn_then_resume" --tb=short -q
PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py \
    --override-ini="addopts=" --override-ini="timeout=280" -m integration \
    -k "test_terminate_after_spawn_then_revive" --tb=short -q
PYTEST_TIMEOUT=280 timeout 320 .venv/bin/pytest tests/e2e/test_e2e_workflows.py \
    --override-ini="addopts=" --override-ini="timeout=280" -m integration \
    -k "test_three_level_cascade_reports" --tb=short -q

# New tests from this phase (3.3, 3.9)
PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/unit/routers/test_stop_instance_subtree.py \
    --override-ini="addopts=" --override-ini="timeout=280" --tb=short -q
PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/unit/repositories/test_job_queue_atomic_transition.py \
    --override-ini="addopts=" --override-ini="timeout=280" --tb=short -q
```

Per ensure.md:8 — **NO `-x`** for suite runs; review all failures.

### Sequencing note for B5 verification

Task 3.3 (B5 e2e) depends on Phase 1 landing. Before P1:
- `get_tree_ids(mid_tree_child)` returns `[mid_tree_child]` only (B4 enumeration bug — missing permanent `parent_id` rows).
- B5 fix correctly pauses the target but does not pause descendants (because descendants aren't enumerated).
- E2E test will FAIL until P1 lands.

After P1 lands:
- `get_tree_ids(mid_tree_child)` returns `[mid_tree_child, …descendants]`.
- B5 fix pauses the whole subtree rooted at `mid_tree_child`.
- E2E test passes.

**Hard sequencing:** P1 must be merged (or in the same atomic worktree commit) before Task 3.3 can be verified. The unit tests in Task 3.3 (cases 1, 4, 6) can run before P1 because they only test the response shape and parameter routing. The e2e test (mid-tree subtree enumeration) is the one that requires P1.

---

## Architect Flags

1. **[ARCHITECT] B5 — Subtree vs Whole-Tree vs Soft-Stop decision** — RESOLVED in this plan: **subtree**. This is a user-visible behavior change for `/stop` (was whole-tree by accident, now subtree by design). Mitigation:
   - `/stop` is documented `deprecated=True` (line 1367); behavior change is on a deprecation path.
   - `/pause` semantics are UNCHANGED (the parameter default preserves whole-tree behavior).
   - No external callers found (research line 86-88).
   - Doc note added (Task 3.12) clarifies the new contract.
   - **Open for review:** the architect may prefer to (a) leave `/stop` broken-on-deprecated (defer until removal), (b) implement soft-stop (the original stop-instance-button plan), or (c) keep whole-tree (matches `/pause`, avoid surprise). If (c), this task becomes "no code change, just doc note + accelerate deprecation".

2. **[TIMEBOXED] B6 — Diagnosis scope** — 2–4h hard cap. If the seam is small (<100 LOC), fix ships in this phase. Otherwise, ticket only. The architect should confirm that "no small seam found" is an acceptable exit condition for Phase 3.

3. **[ASSESSMENT-ONLY] B7(a), B7(c), SSE** — these are NOT implemented in Phase 3. They become follow-up tickets. The architect should confirm that deferring them is consistent with the batch's scope discipline (don't gold-plate secondary defects).

---

## Risk Table

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | B5 behavior change confuses users who relied on the "always pauses root" behavior | Medium | Low | `/stop` is `deprecated=True`; 0 external callers (research); doc note added (Task 3.12); `/pause` unchanged |
| 2 | B5 subtree semantics conflict with `/pause` whole-tree semantics in user's mental model | Medium | Medium | The two endpoints now have DISTINCT semantics; docs need to clarify (Task 3.12); unit test case 6 pins `/pause` unchanged |
| 3 | B6 diagnosis destabilizes the resume cascade (instrumentation side-effects) | Medium | Low | Read-only instrumentation first (SQLAlchemy echo, SELECT snapshots); do NOT mutate any DB row during diagnosis; if instrumentation reveals a need to mutate, stop and write ticket |
| 4 | B7(b) COALESCE guard breaks a future re-arm path (F9 was deferred but may be re-introduced) | High | Low | `grep` audit confirms NO re-arm path exists today; if F9 lands, callers must use `preserve_completed_at=False` explicitly (default unchanged) |
| 5 | B7(b) guard changes observable behavior of `complete_job` / `fail_job` / `terminate_job` for any caller that relied on the re-stamp | Low | Very Low | Research confirms no caller relies on re-stamping; existing callers without the flag continue to behave identically (default `preserve_completed_at=False`) |
| 6 | B5 fix breaks the existing `test_pause_after_spawn_then_resume` e2e (which exercises `/pause`, NOT `/stop`) | Low | Very Low | B5 only changes `/stop`; `/pause` is untouched; if the test breaks, it's a regression caught early |
| 7 | B5 fix composes incorrectly with Phase 1's enumeration fix (subtree enumeration misses live descendants) | High | Medium | Task 3.11 mandatory e2e runs the cascade tests; Task 3.3 e2e specifically asserts subtree enumeration; if P1 not yet merged, defer Task 3.3 verification but ship 3.1–3.2 |
| 8 | SSE deferral leaves UX gap (60s polling) | Low | Medium | Evidence report confirms polling self-corrects; ticket documents the lag and the fix shape for next batch; no active user complaint cited |
| 9 | B6 ticket output is vague / lacks repro | Medium | Medium | Ticket template in §Out of Scope enforces repro + suspected sites + effort class; diagnosis bundle (Task 3.4) provides the evidence |
| 10 | Test pack regressions caused by B5 subtree semantics in legacy callers | Medium | Low | Task 3.11 includes the full ensure.md:47-53 e2e suite; pack failures are caught before merge |

---

## Hard Constraints (project rules — every task honors)

- **Canonical `terminal_reason` only** (`_STATUS_CANONICAL_MAP`, `daemon/services/work_status.py:60-125`) — B5/B7(b) do not touch terminal_reason; B6 diagnosis may surface a terminal_reason issue (note in ticket if so).
- **DependencyBus is SOLE completion authority** — B5's `pause_instance_cascade` cascade uses bus interactions via `cancel_by_instance` + graph_task cancellation; no new bus state created. B7(b) does not touch the bus.
- **Pause writes NOTHING to JobItems** — verified at `_pause_cascade_db_sync` (Phase 4 invariant). B5's subtree fix preserves this. B7(b) is a read-side guard, not a pause-time write.
- **Named transitions + `reconcile_turn_mirror(work_id)` authoritative** — 8 mirror tables. B5/B7(b) do not introduce transitions; they use existing paths. B6 diagnosis will check that resume's `ResumeTurn` at `:3821-3826` is not corrupted by the 404 path.
- **Revive semantics intact** — `send_message` at `instance_messaging.py:1486-1510` revives `COMPLETED/TERMINATED/ERROR/FAILED` instances by auto-transitioning to `RUNNING`. B5's subtree pause does NOT revive instances (it's a pause, not a resume). B7(b) is terminal-only and does not interact with revive.
- **No new state on DependencyWatcherState** — B5/B7(b) add no state. B6 diagnosis may surface a watcher-state issue (note in ticket).
- **`reasoning_echo_disabled_models` denylist invariant** — unrelated to Phase 3.
- **Pause-First Then Quiesce Convention** — this is the IMPLEMENTATION convention, not a Phase 3 plan constraint. Phase 3 changes are additive + small-fixes, not config flips or in-place migrations.
- **`/livez` / `/readyz` health probes** — unrelated to Phase 3.
- **`PG` prefix and `ENSEMBLE_UPGRADE_LIVE` env strip** — unrelated to Phase 3 (this is live-rung gate territory, only invoked by P2.3 release work).
- **`F2-verified-closed` flag for `promote.sh`** — unrelated to Phase 3.
- **`--f2-verified-closed` argv flag** — unrelated to Phase 3.

---

## Out of Scope (Assessment-Only Follow-Up Tickets)

The following defects are diagnosed-but-not-implemented in Phase 3. Each gets a follow-up ticket content that a future batch can execute.

### Ticket FT-001 — B7(a): Future-dated work rows (+7h)

**Repro:** 3 work rows observed with `created_at` or `paused_at` stamped `2026-08-25T00:0x+00:00` (local clock +07 with UTC offset applied). Likely from `datetime.now()` without `timezone.utc`.

**Suspected sites (not located in research scan):**
- All `datetime.now()` call sites in work insertion paths: grep `daemon/repositories/work/` and `daemon/services/work_insertion*` for `datetime.now()` (NOT `.utcnow()` and NOT `datetime.now(timezone.utc)`).
- Cross-check: `paused_at` stamping at `daemon/services/instance_lifecycle.py:2061` is correct (`datetime.now(timezone.utc)`), so the leak is in a DIFFERENT insert path.

**Effort class:** SMALL–MEDIUM (grep + 1–2 line fix per site + unit test).

**Recommended approach:**
- Add a `created_at = :now_utc` parameter pattern to ALL work insertion paths (force UTC).
- OR add a `pytest --strict-timezone` static check that fails any `datetime.now()` without `timezone.utc` in work/job/task write paths.
- Unit test: insert work row, assert `created_at` is within 5s of `datetime.now(timezone.utc)` regardless of system TZ.

### Ticket FT-002 — B7(c): Detail-vs-List Status Disagreement

**Repro:** job `86b25d35` — jobs-detail endpoint says `completed`, jobs-list endpoint says `processing`. Detail uses `work_record.completed_at` directly (`daemon/routers/jobs_crud.py:123`); list uses legacy status derivation (likely `_derive_legacy_status` in `daemon/repositories/job_queue/work_status.py` or equivalent).

**Suspected sites:**
- `daemon/routers/jobs_crud.py:123` — detail endpoint direct read.
- `daemon/repositories/job_queue/repository.py:855` — list endpoint status derivation (verify exact line).
- `_derive_legacy_status` at `daemon/repositories/job_queue/work_status.py` (path TBD; legacy wrapper).

**Effort class:** SMALL (likely 1–2 line normalization) to MEDIUM (if derivation paths diverge in semantic).

**Recommended approach:**
- Pick ONE status derivation path (canonical: `_derive_legacy_status`) as the single source.
- Both endpoints call the canonical path.
- Unit test: same row, both endpoints return identical status.

### Ticket FT-003 — SSE: status_change fan-out to parent subscribers

**Repro:** parent subscribed to SSE; child transitions status (cascade event); FE misses event; self-corrects via 60s polling. Hub routing at `daemon/services/live_event_hub.py:175-196` only looks up connections for the exact `instance_id`.

**Suspected sites:**
- `daemon/services/live_event_hub.py:175-196` — `_stream_to_connections` (route by node id only).
- `daemon/services/live_event_hub.py:292-313` — `stream_instance_created` (precedent for parent_id fan-out).
- All `stream_status_change` callers — audit for parent_id availability in context.

**Effort class:** MEDIUM (hub routing change + caller audit + parent_id resolution in every call site that lacks it).

**Recommended approach:**
- Mirror the `instance_created` pattern: when `parent_id` is known at emit time, fan out to `parent_id`'s connections AS WELL AS the target's connections.
- For call sites without `parent_id`, add a `parent_id` lookup via `instance_repository.get(instance_id).parent_id` (cached) — small DB read at most call sites.
- Document the change in `docs/sse-events.md`.

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | B5: `/stop` mid-tree pauses target subtree (target + descendants), NOT project root | Task 3.3 unit test case 1 + e2e | 100% pass; `paused_ids` does NOT contain root when called on non-root |
| 2 | B5: `/pause` semantics unchanged (whole-tree pause preserved) | Task 3.3 unit test case 6 + existing `test_pause_after_spawn_then_resume` | 100% pass; `paused_ids` contains root + all descendants when `/pause` called on non-root |
| 3 | B5: `/stop` on root pauses whole tree | Task 3.3 unit test case 2 | 100% pass; behavior matches `/pause` when called on root |
| 4 | B7(b): second `complete_job` call on already-completed row preserves original `completed_at` | Task 3.9 unit test case 1 | 100% pass; `completed_at` value unchanged after re-call |
| 5 | B7(b): `fail_job` and `terminate_job` (cancel) preserve `completed_at` on re-call | Task 3.9 unit test cases 2–3 | 100% pass; same invariant |
| 6 | B6: either a small fix ships OR a documented follow-up ticket | Task 3.6 exit decision | One of the two deliverables present |
| 7 | B6 (if fix path): GET /instances/{id} returns 200 for all tree instances post-resume | Manual smoke + new unit test | 100% pass; no 404s in 60s post-resume sweep |
| 8 | B7(a), B7(c), SSE follow-up tickets exist with repro + suspected sites + effort class | Tasks 3.10 | All three tickets present in `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/` |
| 9 | All 5 mandatory packs + 4 e2e green per `.agents/tester/rules/ensure.md:47-53` | Task 3.11 | 100% pass; no regressions; PYTEST_TIMEOUT=280; one-by-one; no `-x` |
| 10 | `/stop` documentation in `docs/usage.md:340` reflects subtree semantics | Task 3.12 | Doc note present and matches Task 3.3 contract |
| 11 | No new `JobItem`-creation sites added in pause/resume paths | grep audit | Pause still writes NOTHING to JobItems (Phase 4 invariant preserved) |
| 12 | No new `DependencyWatcherState` value added | grep audit on `dependency_bus/models.py` | State enum unchanged |
| 13 | DependencyBus remains SOLE completion authority | grep audit + test | No new completion-authority sites added |
| 14 | Terminal reason for any stranded rows from this fix is canonical | grep audit (per `work_status.py:60-125`) | 0 instances of non-canonical reason in new code paths |

---

## Files Touched (consolidated)

| File | Change Type | Tasks |
|------|-------------|-------|
| `daemon/services/instance_lifecycle.py` | Add `cascade_to_root: bool = True` parameter to `pause_instance_cascade` at `:2047` | 3.1 |
| `daemon/routers/instances.py` | Update `/stop` handler at `:1366-1376` to call with `cascade_to_root=False`; preserve response shape | 3.2 |
| `daemon/repositories/job_queue/repository.py` | Add `preserve_completed_at: bool = False` to `atomic_transition` at `:1134`; update 3 callers at `:2275, 2298, 2504` | 3.7, 3.8 |
| `docs/usage.md` | One-line update at `:340` clarifying `/stop` subtree semantics | 3.12 |
| `tests/unit/routers/test_stop_instance_subtree.py` | NEW — 6 unit cases | 3.3 |
| `tests/e2e/test_stop_instance_subtree_pause.py` | NEW — 1 e2e case | 3.3 |
| `tests/unit/repositories/test_job_queue_atomic_transition.py` | NEW — 4 unit cases | 3.9 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/FT-001-b7a-future-dated-rows.md` | NEW — follow-up ticket | 3.10 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/FT-002-b7c-status-disagreement.md` | NEW — follow-up ticket | 3.10 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/FT-003-sse-status-change-fanout.md` | NEW — follow-up ticket | 3.10 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/p3-b6-diagnosis-bundle/` | NEW — diagnosis evidence (if B6 fix path) OR ticket draft (if ticket path) | 3.4–3.6 |

**Total production-code change:** 3 files modified (`instance_lifecycle.py`, `instances.py`, `job_queue/repository.py`) + 1 doc edit (`usage.md`). **Total test addition:** 3 new test files, 11+ unit tests + 1 new e2e test. **Optional B6 fix:** 1–2 files if seam is small (<100 LOC); otherwise, ticket only.

---

## Coupling to Other Phases (within this worktree)

| Phase | Defect | Coupling | Sequencing |
|-------|--------|----------|------------|
| Phase 1 | B1, B4 (lineage enumeration) | **TIGHT** — B5's subtree fix depends on `get_tree_ids` correctly enumerating descendants. Before P1, B5 fix only pauses the target (no descendants). After P1, B5 fix pauses the whole subtree. | Phase 1 must land (or be in same atomic merge) before Task 3.3 e2e can pass |
| Phase 2 | B2, B3 (watcher/obligation) | Loose — B5's pause path goes through `pause_instance_cascade` (same code as Phase 2's pause/bus interaction); Phase 2's watcher fixes are independent of B5's re-root removal | After Phase 1; concurrent with Phase 3 |
| Phase 3 (this) | B5, B6, B7(a), B7(b), B7(c), SSE | Self-coupled: B5 + B7(b) are independent fixes; B6 is timeboxed-diagnosis; B7(a)/B7(c)/SSE are assessment-only tickets | Phase 3 ships after Phase 1 + Phase 2 land |
| Future batches | FT-001 (B7a), FT-002 (B7c), FT-003 (SSE), B6 ticket (if any) | Independent | Next batch |

**Hard sequencing:** Phase 1 + Phase 3's Task 3.1/3.2 can land independently. **Phase 3's Task 3.3 e2e requires Phase 1's `get_tree_ids` fix.** Phase 3's Task 3.9 (B7(b) unit test) can land independently.

**Worktree pattern:** All three phases (1, 2, 3) merge atomically into `feature/pause-resume-terminate-tree-fix` per the dispatch notes. A separate worker commits atomically; this plan produces no commits.

---

## Rollback Story

Per-task rollback is straightforward (small fixes, all revert cleanly):

| Task | Rollback |
|------|----------|
| 3.1 (`pause_instance_cascade` parameter) | `git revert <commit>` — removes the parameter; default behavior of `/pause` and `/stop` is restored to whole-tree-pause. No response-shape change. |
| 3.2 (`/stop` handler update) | `git revert <commit>` — restores the `await pause_instance(instance_id, request)` delegation; re-introduces the B5 bug class but `/stop` returns to known-buggy state (matches pre-batch behavior). No response-shape change. |
| 3.7, 3.8 (B7(b) COALESCE guard) | `git revert <commit>` — removes the `preserve_completed_at` flag and reverts the 3 call sites to unconditional `completed_at=now`. No behavior change for first-time finalization; re-stamp regression restored. |
| 3.12 (doc note) | `git revert <commit>` — single-line revert. No code change. |
| Tests (3.3, 3.9) | `git revert <commit>` — removes test files. No production impact. |

**Response-shape compatibility for `/stop`:** preserved exactly. The handler returns `{paused: true, paused_ids: result["paused_ids"], skipped_ids: result["skipped_ids"]}` before AND after the fix; only the CONTENT of `paused_ids` changes (subtree vs whole-tree). No client-side breaking change for consumers reading the response keys.

---

## Defect-to-Task Mapping (cross-reference)

| Defect | Tasks |
|--------|-------|
| B5 | 3.1, 3.2, 3.3, 3.11, 3.12 |
| B6 | 3.4, 3.5, 3.6 (timeboxed diagnosis) |
| B7(b) | 3.7, 3.8, 3.9 |
| B7(a) | 3.10 (FT-001 ticket only) |
| B7(c) | 3.10 (FT-002 ticket only) |
| SSE | 3.10 (FT-003 ticket only) |

---

## Open Questions

1. **B5 architect decision** (see §Architect Flags): is subtree semantics acceptable, or should `/stop` be (a) left broken on deprecation, (b) made into soft-stop, or (c) kept as whole-tree?
2. **B6 exit** (see Task 3.6): is "no small seam found, ticket only" an acceptable Phase 3 outcome? (Recommended: yes; cap at 4h.)
3. **B7(b) flag scope** (see Task 3.7): should the flag default to `True` (preserve-by-default, safer for existing callers that rely on the unconditional stamp)? Current plan: default `False` (opt-in) — but the caller audit confirms no caller relies on re-stamping, so default `True` would also be safe. **Recommend default `True`** (preserve-by-default) for safety; document in the commit message.

---

**End of Phase 3 Plan**