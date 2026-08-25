# Plan Overview: Phase 3 — Pause/Resume/Terminate Tree-Propagation: B5 (Router) + B6 (Detail 404) + B7 (Timestamps) + SSE (Assessment)

Date: 2026-08-24 (Rev 2.1 — 2026-08-24, reviewer-corrected per council 2bb126df; W6 (mock-migration scope — phase1-only), W7 (T9→T10 daemon-restart sequencing — phase1-only), W8 (`preserve_completed_at` zero-caller reservation comment spec at `repository.py:1134`), and §1.7 cross-ref fix folded — Rev 2 prior: architect-corrected per architecture-recommendation.md 8abca8b5)
Author: planner[v2] via plan-creation worker
Status: Rev 2.1 Draft (W8 reservation comment spec added to Task 3.7; §1.7 cross-ref in Task 3.1 acceptance repointed to `architecture-recommendation.md §1.6-1.7` because phase1-plan §1.7 does not exist; AF-B5, AF-B6, AF-P3-7 architect flags RESOLVED; B5 carries no open flags; B6 carries [TIMEBOXED] cap confirmed; B7(b) re-scoped as verify+pin — see §Architect Flags and §Rev 2.1 Changelog)
Branch: feature/pause-resume-terminate-tree-fix @ worktree head (parallel with Phase 1 + Phase 2 workers)
Bug Source: `.agents/tester/RESULTS/2026-08-24-pause-resume-terminate-tree-propagation-repro.md` (live-repro evidence, 6 phases)
Research: `.agents/shared/planning/pause-resume-terminate-tree-fix/research-routing.md` (read-only investigation, B5/B6/B7/SSE scope)
Prior Plan: `.agents/shared/planning/stop-instance-button/plan-overview.md` (soft-stop design intent — superseded by cascade-pause implementation)
Prior Rev: phase3-plan.md Rev 1 preserved in git at commit cefb9798 (frozen for diff); Rev 2 preserved at commit 8abca8b5 (architect corrections); Rev 2.1 = reviewer-council corrections folded (current, planning-only)

---

## Defects In Scope

| # | Defect | Severity | Live Evidence | Action |
|---|--------|----------|---------------|--------|
| **B5** | `/stop` ignores path param, pauses project ROOT instead of target subtree | 🟠 important | `phase 5`: `POST /api/instances/c83b46cd-…/stop` → `200 {"paused":true,"paused_ids":["f5e223f1-…"]}`; log 2349 confirms `Pausing instance f5e223f1` immediately after `POST …/c83b46cd…/stop` (2350) | **FIX** — [ARCHITECT] review |
| **B6** | `GET /api/instances/{id}` → 404 for ALL 5 tree instances post-resume | 🟠 important | `phase 4`: list + messages endpoints fine; detail 404 throughout the phase; reproducer isolated this to resume cascade interaction | **TIMEBOXED DIAGNOSIS** — hard 2–4h cap; fix-or-ticket exit |
| **B7(b)** | `completed_at` re-stamped on resume (observed twice) | 🟢 nice-to-have | evidence report §B7 (b); candidate stamp sites at `daemon/repositories/job_queue/repository.py:2275, 2298, 2504`, plus `job_feedback_observer.py:1885-1891` (observer fail-safe — **Rev 2: 4th site added**) | **Rev 2 (AF-P3-7):** VERIFY + PIN last-settle semantics (NOT Rev 1's "FIX — trivial COALESCE guard"). See §Rev 2 Changelog for rationale (verified re-arm path `repository.py:1974-2167` would freeze false timestamps under COALESCE) |
| **B7(a)** | 3 work rows future-dated `+7h` (local clock stamped with UTC offset) | 🟢 nice-to-have | evidence report §B7 (a); leak vector NOT located in quick scan (`datetime.now(timezone.utc)` correct at `instance_lifecycle.py:2061`) | **ASSESSMENT ONLY** — ticket for next batch |
| **B7(c)** | jobs-detail said `completed` while jobs-list said `processing` for `86b25d35` | 🟢 nice-to-have | evidence report §B7 (c); two status derivation paths (detail direct DB read vs `_derive_legacy_status`) | **ASSESSMENT ONLY** — ticket for next batch |
| **SSE** | `status_change` routed by node id only; child cascade events dropped for parent subscribers; FE self-corrects via 60s polling | 🟡 low-medium | evidence report §SSE; `live_event_hub.py:175-196` (status_change) vs `live_event_hub.py:292-313` (instance_created via parent_id); FE subscribes per-instance at `messages.py:604-630` | **ASSESSMENT ONLY** — ticket for next batch |

**OUT of scope:** B1 (Phase 1 lineage), B2 (Phase 2 obligation), B3 (Phase 2 obligation), B4 (Phase 1 lineage). This phase assumes Phase 1 + Phase 2 fixes are merging in the same worktree.

---

## Objective

Phase 3 closes the **router defect (B5)**, runs a **timeboxed probe-first diagnosis (B6)**, ships a **verify+pin last-settle test for B7(b) (re-scoped from Rev 1's COALESCE guard)**, and produces **assessment-only follow-up tickets** for the remaining secondary defects (B7(a), B7(c), SSE). The plan respects the project's hard constraints (canonical terminal reasons, dependency-bus SOLE completion authority, pause writes NOTHING to JobItems, named transitions + `reconcile_turn_mirror(work_id)` authoritative, revive semantics intact), does not gold-plate secondary defects, and never scope-creeps into B6 territory beyond the diagnosis cap.

**Testable completion sentence:** A user calling `POST /api/instances/{mid_tree_child}/stop` receives `200 {"paused":true,"paused_ids":[mid_tree_child, …descendants of mid_tree_child]}` (target subtree, NOT the project root) AND `/pause` keeps whole-tree semantics for the 5 internal callers (`instance_messaging.py:1119, :3748`, `watchover_service.py:1004, :1470`, manager facade `manager.py:7690`); re-arm→re-complete on a JobItem stamps `completed_at=T2` (last-settle) by design — pinned by Task 3.9 unit test cases 1–3, with the 30-min D2 repro-DB check recording no-anomalous-update evidence; the B6 diagnosis either shipped a small fix or produced a documented ticket with §5.3 minimum content; B7(a), B7(c), and SSE each have a follow-up ticket with repro + suspected sites + effort class.

---

## Verified Mechanics (re-cited from research-routing + code re-verification)

All citations re-verified against the worktree at branch head before writing this document. Page/line numbers are stable.

### B5 — Router Defect

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/routers/instances.py:1366-1376` | `stop_instance_deprecated` | **BUG SITE.** `@router.post("/{instance_id}/stop", deprecated=True)`; body: `return await pause_instance(instance_id, request)`. Path param `instance_id` is passed through unchanged. |
| `daemon/routers/instances.py:631-659` | `pause_instance` | Reads `instance_id` from path, calls `manager.pause_instance_cascade(instance_id)` (line 654). Returns `{paused:true, paused_ids: result["paused_ids"], skipped_ids: result["skipped_ids"]}`. |
| `daemon/services/instance_lifecycle.py:2047-2090` | `pause_instance_cascade` | **BUG SITE (composition).** Resolves `root_id = repo.get_tree_root_id(instance_id)` (line 2050); falls back to `instance_id` (line 2053); then `tree_ids = repo.get_tree_ids(root_id)` (line 2056). **The re-root is by design** (per tree-aware-pause-resume invariant) — `/pause` semantics intentionally pause the WHOLE tree from root. **Rev 2 note:** `:2056` becomes `repo.get_cascade_tree_ids(root_id)` after Phase 1 lands (P3 rebases on P1 — both branches must use the wrapper, NOT raw `get_tree_ids`, else `/pause`, messaging, and watchover silently bypass P1's kill-switch; see §Architect Flags AF-B5 and AF-B5 correction register entry). |
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

### B7(b) — `completed_at` Re-Stamp — **Rev 2 re-scoped as VERIFY+PIN (not "FIX trivial COALESCE guard")**

| File:Line | Symbol | Verified role |
|-----------|--------|---------------|
| `daemon/repositories/job_queue/repository.py:2260-2277` | `complete_job` | `now = datetime.now(timezone.utc).isoformat(); atomic_transition(job_id, from_status="processing", to_status="completed", completed_at=now, …)` — **STAMP SITE**: unconditional `completed_at=now`. Guarded by `admission_state='active'` predicate at the SQL level (per `atomic_transition`'s guarded-UPDATE pattern); cannot re-stamp a row whose `admission_state` is not `'active'` |
| `daemon/repositories/job_queue/repository.py:2292-2301` | `fail_job` | Same pattern: `completed_at=now` on PROCESSING → FAILED. Same `admission_state='active'` guard |
| `daemon/repositories/job_queue/repository.py:2497-2506` | `terminate_job` (cancel-from-terminal) | Same pattern: `completed_at=now` on PROCESSING → CANCELLED. Same `admission_state='active'` guard |
| `daemon/services/job_feedback_observer.py:1885-1891` | Observer fail-safe finalize | **Rev 2 — 4th stamp site the plan missed:** observer fail-safe path stamps `completed_at=now` on a recovering job; same `admission_state='active'` guard. Refer to the §Rev 2 Changelog for why this was missed |
| `daemon/services/instance_lifecycle.py:3698-3856` | `_resume_cascade_db_sync` | **Verified: does NOT touch `completed_at`.** UPDATE at `:3783-3798` only flips `status`, `paused_at`, `updated_at` on `instances`. Task PAUSED→PENDING via `ResumeTurn` at `:3821-3826` does NOT touch `completed_at` (per the docstring at `:3721-3723` — explicit design decision). |
| `daemon/repositories/task/repository.py:855` | Task completion COALESCE | `completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)` — Task table pattern (not JobItem). JobItem has no equivalent — and per §Rev 2, should not get one (last-settle semantics). |
| `daemon/repositories/task/repository.py:2035` | Task sync UPDATE | `completed_at = :now` — Task table also has unconditional stamp sites (out of scope; same risk; ticket for follow-up) |

**Verified re-arm path (Rev 2 — replaces Rev 1's false "ZERO matches / F9 DEFERRED" claim):** `rearm_with_lock` EXISTS and is in active production use.
- **Definition:** `daemon/repositories/job_queue/repository.py:1974-2167`.
- **Call site:** `daemon/services/job_feedback_observer.py:1470-1474` (orphan-race post-commit re-check).
- **Reference:** `daemon/services/job_recovery_service.py:211-216`.
- **Semantics:** the UPDATE at `:2126-2144` sets `admission_state='active'` + `instance_id` — it does **NOT** clear `completed_at`. `atomic_retry` (`:1278-1298`) clears `failed_at` but not `completed_at`. Every stamp site is guarded by `admission_state='active'` (complete_job `:2271-2277`, fail_job `:2294-2301`, terminate_job `:2500-2506`, observer fail-safe `job_feedback_observer.py:1885-1891`).
- **F9 is CLOSED via this path** (Rev 1's claim that F9 was DEFERRED was factually false — F9 closed when `rearm_with_lock` shipped). The repo also confirms this: `.agents/tester/QUARANTINE.md` F9 still-open entry refers to a *different* F9 (related but distinct concept) — the re-arm seam itself is live.

**Leak vector re-analysis (Rev 2):** a DONE row cannot be re-stamped without a real re-entry first. The observed B7(b) re-stamps are most plausibly **F9 re-arm + C1 `_process_resume_finalize` composition — likely working as designed, not corruption.** A COALESCE/preserve guard would actually FREEZE the stale first-settle timestamp on a legitimately re-run job — and on the retry flow it freezes the **failure-time** stamp on a row that later shows `completed`. **The correct fix is the OPPOSITE of Rev 1's COALESCE guard:** keep the unconditional stamp, document last-settle semantics as the design intent, and add a test to pin them.

**Decision (Rev 2):** Re-scope B7(b) from "FIX — trivial COALESCE guard" to **"VERIFY + PIN last-settle semantics"** (see §Architect Flag #3 and §Rev 2 Changelog for rationale). No code change to stamp sites; no `preserve_completed_at` flag wired. Tasks 3.7–3.9 re-scoped accordingly.

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

**Add a parameter to `pause_instance_cascade`** (both branches use P1's `get_cascade_tree_ids(...)` wrapper — NOT raw `get_tree_ids(...)` — to avoid silently bypassing P1's kill-switch from `/pause`, messaging, and watchover):

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
        # Existing whole-tree behavior (B5 bug class source, unchanged for /pause).
        # Rev 2 (AF-B5): True-branch inherits P1's swap at :2056 — uses the wrapper,
        # NOT raw get_tree_ids, to honor P1's kill-switch (ENSEMBLE_CASCADE_LINEAGE).
        root_id = repo.get_tree_root_id(instance_id)
        if root_id is None:
            root_id = instance_id
        tree_ids = repo.get_cascade_tree_ids(root_id)
    else:
        # New: target subtree only (used by /stop after fix).
        # Rev 2 (AF-B5): else-branch also uses the wrapper — P3 rebases on P1.
        tree_ids = repo.get_cascade_tree_ids(instance_id)
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
| **3.1** | B5 — Add `cascade_to_root: bool = True` parameter to `pause_instance_cascade` at `daemon/services/instance_lifecycle.py:2047`. **Rev 2 (AF-B5):** BOTH branches must call `repo.get_cascade_tree_ids(...)` (P1's kill-switch wrapper) — NOT raw `repo.get_tree_ids(...)` — True-branch inherits P1's swap at `:2056` (P3 rebases on P1). | none | Parameter added with default `True`; signature passes type-checker; `pause_instance` (which calls `manager.pause_instance_cascade(instance_id)`) keeps whole-tree semantics unchanged; new branch `else: tree_ids = repo.get_cascade_tree_ids(instance_id)` added; the existing True-branch at `:2056` is replaced with `tree_ids = repo.get_cascade_tree_ids(root_id)` (NOT raw `get_tree_ids`); mock migration of 11 suites per phase1-plan Task 1 acceptance (W6 corrected path list — Rev 2.1; the Rev 2 citation `phase1-plan §1.7` was wrong because phase1-plan has no §1.7 — see `architecture-recommendation.md §1.6-1.7` for the blast-radius + kill-switch verdict that the citation was trying to reach) covers the True-branch rename; service-level case 7 (Task 3.3) pins default semantics for the 5 internal callers |
| **3.2** | B5 — Update `/stop` handler at `daemon/routers/instances.py:1366-1376` to call `manager.pause_instance_cascade(instance_id, cascade_to_root=False)` and return the same response shape | 3.1 | Handler no longer delegates to `pause_instance`; passes `cascade_to_root=False`; response keys (`paused`, `paused_ids`, `skipped_ids`) preserved exactly |
| **3.3** | B5 — Unit + e2e tests for `/stop` subtree semantics | 3.1, 3.2; P1 lands | See §Test Strategy — pinned test for mid-tree → subtree paused_ids content |
| **3.4** | B6 — Probe 1 (≤5min, decisive classifier): reproduce one detail-404, inspect the response **body** | P1 lands, P2 lands (post-cascade behavior stable) | Classification recorded in the diagnosis bundle: `{"detail":"Not Found"}` → H1 routing/harness artifact (skip to probe 2); `INSTANCE_NOT_FOUND` (or any non-routing 404 body) → row-level path (skip to probe 4). Bundle saved to `/tmp/p3-b6-diagnosis-<timestamp>/probe1.md` |
| **3.5** | B6 — Probes 2–5 (≤15min, ≤20min, ≤30min, ≤30min — see `architecture-recommendation.md §5.2`): one-script back-to-back list+detail+messages sweep (falsifies H2 stale-comparison / H3 two-processes), verify script URLs/ports vs live daemon + `ps` for second daemon, direct DB 5-id SELECT + byte-compare ids (only if `INSTANCE_NOT_FOUND`), SQLAlchemy echo + engine log split-brain check | 3.4 | Each probe records a one-line elimination evidence per hypothesis (H1–H5 in §5.1). Bundle saved to `/tmp/p3-b6-diagnosis-<timestamp>/probes2-5.md`. **Static analysis conclusion (recorded up front):** divergence CANNOT be at the DB-read seam — detail `session.get(Instance, pk)` (`repository.py:222-226` → `_enrich_instance:105-107`; no subclass overrides; factory `factory.py:380` returns base) and list `select(Instance)` (`repository.py:444-488`, no status/deleted_at filter) are **structurally equivalent on the same engine** → H1 (harness artifact) is the TOP hypothesis |
| **3.6** | B6 — Exit decision: fix OR ticket. **Hardened exit condition (Rev 2, AF-B6):** 404-body class identified AND (seam classified small/large **OR** harness artifact confirmed with corrected-repro green). Fix path is SMALL only (single seam, <100 LOC, no cascade-rework). 4h hard cap (2h effective work). Anything larger is a ticket. | 3.4, 3.5 | Decision recorded with evidence; either a follow-up commit (with unit test) OR a follow-up ticket. **Ticket minimum content (per §5.3):** exact curl repro set including 404-body capture; DB snapshot queries (5-id SELECT + hierarchy rows + engine log line); eliminated-hypotheses table (H1–H5) with one-line evidence each; effort class per surviving hypothesis; corrected repro script; "possibly NOT-A-DEFECT" recommendation if H1 confirms |
| **3.7** | B7(b) — Add `preserve_completed_at: bool = False` flag to `atomic_transition` at `daemon/repositories/job_queue/repository.py:1134`. **Rev 2 (AF-P3-7):** default `False` is **MANDATORY** (not just caller-compatible) — verified re-arm path (`rearm_with_lock` `repository.py:1974-2167`, observer call `job_feedback_observer.py:1470-1474`) means preserve-on-default would freeze stale/failure-time stamps on legitimately re-run jobs. SQL builder branches on flag (when True, UPDATE uses `completed_at = COALESCE(completed_at, :completed_at)` instead of `completed_at = :completed_at`). **🔵 Rev 2.1 (council 2bb126df W8) — RESERVATION COMMENT SPEC at `repository.py:1134`:** the flag ships with **ZERO callers in Phase 3** (Task 3.8 DELETED per AF-P3-7 — see §Rev 2 Changelog row 9). To prevent future well-meaning wiring of `preserve_completed_at=True` from re-introducing the false-timestamp corruption Rev 1 was trying to prevent, the implementation MUST attach a reservation comment directly above the `atomic_transition` signature (at `:1134`) with the following literal wording (verbatim — copy/paste from this spec): `# Reserved for a deliberate first-touch-timestamp caller; do NOT wire without re-reading architecture-recommendation.md §6.2 — preserve-on-re-complete freezes failure-time stamps on re-armed jobs. See Phase 3 plan Rev 2.1 (council 2bb126df W8) for the rationale. Cross-ref §6.2 (Decision table — Approach A: Default False, no call-site wiring; re-scope B7(b) as verify+pin).` The comment must (a) NAME the reservation intent ("deliberate first-touch-timestamp caller"), (b) include the explicit "do NOT wire without re-reading" guard, (c) cross-reference `architecture-recommendation.md §6.2` so the next reader finds the rationale without grepping plan files, and (d) repeat the load-bearing "preserve-on-re-complete freezes failure-time stamps on re-armed jobs" warning so the risk is in the source-of-truth surface, not only in the plan. **Acceptance evidence:** the diff for Task 3.7 MUST include the comment block at `:1134` (reviewer will check the line is committed). The flag remains DEFINED but UNWIRED at all 4 stamp sites (`complete_job:2275`, `fail_job:2298`, `terminate_job:2504`, observer fail-safe `job_feedback_observer.py:1885-1891`) — the comment is the safety net until a deliberate first-touch caller arrives. | none | Flag added with default `False`; signature passes type-checker; existing callers (complete_job, fail_job, terminate_job, observer fail-safe) continue to stamp `completed_at=now` unchanged on first settle; flag is **RESERVED for a future deliberate first-touch caller** — NO call site in Phase 3 wires `True` (see Task 3.8 deletion rationale and §Rev 2.1 Changelog); **reservation comment block at `repository.py:1134` matches the verbatim spec above (W8 acceptance evidence in diff)** |
| **3.8** | **DELETED in Rev 2 (AF-P3-7) — see §Rev 2 Changelog for rationale.** Original Rev 1 task: "Update call sites at `repository.py:2275, 2298, 2504` to pass `preserve_completed_at=True`". Wiring `True` at the 3 call sites produces the same false-timestamp corruption Rev 1 was trying to prevent — preserves stale first-settle on legitimately re-armed jobs, and freezes failure-time stamps on retry-flow re-completed rows. Renumber map: nothing renumbers (3.9 follows 3.7 with no 3.8 gap). | n/a | n/a |
| **3.9** | B7(b) — Pin last-settle semantics. **Rev 2 (AF-P3-7) inversion:** instead of asserting "second `complete_job` on DONE row preserves `completed_at`", pin that re-arm→re-complete stamps `completed_at=T2` (last-settle). Also fold **D2 working-as-designed pending repro check (leader-accepted):** 30-min repro-DB check — query the twice-re-stamped jobs' admission history for re-arm evidence BEFORE concluding "not a defect". **Documented FLIP CONDITION (per `architecture-recommendation.md §9`):** if the re-stamped jobs never transited `admission_state='active'`, an unguarded raw UPDATE exists somewhere the audit missed, and option-B wiring becomes correct — record this flip in the implementation log. | 3.7 (flag reserved, not wired) | New unit test in `tests/unit/repositories/test_job_queue_atomic_transition.py` — 3 cases (complete, fail, terminate), each exercising a re-arm→re-complete cycle: (a) row in DONE → `rearm_with_lock` flips admission_state to ACTIVE (does NOT clear completed_at), (b) `complete_job` stamps `completed_at=T2 > T1`, (c) assertion: `completed_at` reflects LAST settle, not first. Plus the 30-min repro-DB check recorded as a separate file `tests/manual/b7b_rearm_admission_history.md` (regression evidence; not a pytest) |
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
- Case 7 (**Rev 2, AF-B5 new**): `manager.pause_instance_cascade(mid_tree_child)` called directly (no kwarg, no router) returns `paused_ids == [root, mid_tree_child, leaf_of_mid]` — the **default `True`** is load-bearing for the 5 internal callers (`instance_messaging.py:1119, :3748`, `watchover_service.py:1004, :1470`, manager facade `manager.py:7690`), not just `/pause`. Pin explicitly so a future "default = False" mistake doesn't silently break messaging/watchover.
- Case 8 (**Rev 2, AF-B5 optional**): kill-switch mode propagation — set `ENSEMBLE_CASCADE_LINEAGE=hierarchy` (the fallback), call `pause_instance_cascade(mid)`, assert enumeration switches to hierarchy table (negative-mode test; ensures the wrapper honors P1's kill-switch semantics end-to-end).

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

### B7(b) Tests (Task 3.9) — **Rev 2 inverted from "preserve" to "pin last-settle"**

**Unit test** (`tests/unit/repositories/test_job_queue_atomic_transition.py` — NEW):
- **Case 1 (Rev 2 — pin last-settle):** exercise the full re-arm→re-complete cycle.
  1. Insert row in PENDING → start_job → ACTIVE (T1 set as `started_at`).
  2. `complete_job(job_id)` → admission_state=DONE, `completed_at=T1`.
  3. `rearm_with_lock(job_id)` → admission_state=ACTIVE; `completed_at=T1` PRESERVED (per `repository.py:2126-2144` — re-arm does NOT clear).
  4. `complete_job(job_id)` (second call) → admission_state=DONE; **`completed_at=T2 > T1` (last-settle semantics)**.
  5. Assertion: `completed_at == T2`, NOT T1.
  - **Note (Rev 2 — raise-vs-noop mechanics):** Rev 1's original case-1 spec was mechanically wrong. A second `complete_job` on a DONE row does NOT no-op — `atomic_transition` (`repository.py:1245-1259`) **raises `InvalidTransitionError`** after rowcount=0 when `from_status='processing'` does not match a row whose status is `processing`. The re-arm step (3) is REQUIRED to re-enter the transition path; without re-arm, the test as originally written cannot pass (the second call raises).
- **Case 2 (Rev 2 — fail_job re-arm→re-fail):** same shape as Case 1, but `fail_job` instead of `complete_job` after re-arm. Assertion: `completed_at` reflects last FAILURE timestamp (not the original completion's success timestamp). Documents that `completed_at` is overloaded (covers completed/failed/cancelled), and re-arm→re-fail correctly re-stamps to the new failure time.
- **Case 3 (Rev 2 — terminate_job re-arm→re-cancel):** same shape with `terminate_job`. Assertion: same invariant.
- **Case 4 (Rev 2 — regression):** existing callers of `complete_job` (without `preserve_completed_at`, default `False`) still stamp `completed_at` on first settle (no COALESCE applied). Confirms the flag is opt-in and the default preserves existing behavior on first-touch flows.
- **Manual evidence (D2 — Rev 2 fold-in):** `tests/manual/b7b_rearm_admission_history.md` records the 30-min repro-DB check per leader-accepted "likely working as designed, pending repro" disposition (`architecture-recommendation.md §10 decision 2`). Queries the twice-re-stamped jobs' admission history for re-arm evidence (transitioned through `admission_state='active'` via `rearm_with_lock` between the two settles). **FLIP CONDITION:** if the re-stamped jobs **never** transited `active`, an unguarded raw UPDATE exists somewhere the audit missed, and option-B wiring becomes correct. Record this flip in the implementation log per `architecture-recommendation.md §9`.

### B6 Diagnosis (Tasks 3.4–3.6) — Rev 2 probe-first timebox

**Rev 2 (AF-B6) — adopted from `architecture-recommendation.md §5.1-5.2` (option A, weighted 4.00).** Ordered probes, each ≤30min (~2h of the 2–4h cap). The architect confirms "no small seam found → ticket only" is an acceptable Phase 3 outcome — B6 is 🟠 with a live workaround (list + messages serve the data); the bounded elimination has durable value.

**Static analysis conclusion (recorded up front, from §5.1):** the divergence CANNOT be at the DB-read seam.
- Detail path: `instances.py:488-505` → `manager.py:9015` (pass-through) → `instance_lifecycle.py:2966-2991` → `repository.get:222-226` (`session.get` + `_enrich_instance:105-107`; no subclass overrides — factory `factory.py:380` returns the base class).
- List path: `instances.py:387` → `lifecycle:2906-2964` → `repository.list:444-488` (`select(Instance)`, no status/deleted_at filter) + identical post-processing.
- **A row visible to `select(Instance)` on the same engine is structurally visible to `session.get(Instance, pk)`.** The all-5-uniform, state-independent pattern favors request-level hypotheses (H1 harness artifact / H2 stale comparison / H3 two-processes), NOT row-level invisibility.

**Probe checklist (architect-confirmed order):**

1. **(≤5 min, decisive classifier) — Task 3.4.** Reproduce one 404; inspect the response **body**.
   - Plain `{"detail":"Not Found"}` → routing/harness artifact → H1 → skip to probe 2.
   - `INSTANCE_NOT_FOUND` (or any non-routing body) → row-level path → skip to probe 4.

2. **(≤15 min) — Task 3.5 first sweep.** One-script back-to-back sweep: same client, same base URL, list + detail + messages for all 5 ids in one loop. **Falsifies H2 (stale comparison — list captured pre-resume vs live detail) and H3 (two-processes / port confusion).**

3. **(≤20 min) — Task 3.5 second sweep.** Verify script URLs/ports vs live daemon; `ps` for a second daemon; `grep` repro script for the detail URL. **Eliminates H3 definitively; tightens H1.**

4. **(≤30 min, only if `INSTANCE_NOT_FOUND`) — Task 3.5 third sweep.** Direct DB check on the daemon's DB: `SELECT instance_id, status, parent_id FROM instances WHERE instance_id IN (<5 ids>)`; byte-compare ids. **Eliminates H4 (row invisibility / id drift) if rows are present with matching bytes.**

5. **(≤30 min) — Task 3.5 final sweep.** SQLAlchemy echo on one detail request + engine log `Creating PostgreSQL engine:` line (split-brain check). **Eliminates H5 (engine/connection split-brain) definitively.**

**Hypothesis ledger (from §5.1):**

| # | Hypothesis | Likelihood |
|---|------------|------------|
| H1 | Harness artifact — wrong path/port/base-URL (FastAPI routing-404 `{"detail":"Not Found"}`) or stale daemon | **High** |
| H2 | Stale comparison — list captured pre-resume vs live detail (F-DR1-2 split-brain class precedent) | Medium-High |
| H3 | Two processes / port confusion | Medium-Low |
| H4 | Row invisibility (id drift, delete+recreate) — `_resume_cascade_db_sync:3783-3798` touches status/paused_at/updated_at only | Low-Medium |
| H5 | KeyError misattribution | Low |

**Hardened exit condition (Rev 2):** 404-body class identified **AND** (seam classified small/large **OR** harness artifact confirmed with corrected-repro green). Each eliminated hypothesis gets one evidence line in the bundle.

**Setup (pre-probe, not counted toward cap):**
- Reuse repro project `09b6c42d-5865-404f-b7fa-04dbe816bb11` from `/tmp/pause-repro-20260824/state.json` (per §11 open question — verify file exists before budgeting).
- Build small tree (root + 2 children), pause root, resume root, capture `GET /instances/{each_id}` responses for 60s sweep.
- Log every SQL statement via SQLAlchemy echo for the resume transaction.

**Ticket minimum content (per §5.3, if exit is ticket-only):**
- Exact curl repro set including 404-body capture.
- DB snapshot queries (5-id SELECT + hierarchy rows + engine log line).
- Eliminated-hypotheses table (H1–H5) with one-line evidence each.
- Effort class per surviving hypothesis.
- Corrected repro script + "possibly NOT-A-DEFECT" recommendation if H1 confirms.

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

**Rev 2 (AF-B5):** `get_tree_ids(...)` references below conceptualize the wrapper's pre-P1 vs post-P1 behavior; once P1 lands, both branches of the new `pause_instance_cascade` code call `repo.get_cascade_tree_ids(...)` directly (NOT raw `get_tree_ids`) — the wrapper IS the function in production. Use the wrapper name consistently when reasoning about enumeration.

Task 3.3 (B5 e2e) depends on Phase 1 landing. Before P1:
- `repo.get_cascade_tree_ids(mid_tree_child)` resolves to `repo.get_tree_ids(mid_tree_child)` (wrapper unwraps to legacy), which returns `[mid_tree_child]` only (B4 enumeration bug — missing permanent `parent_id` rows).
- B5 fix correctly pauses the target but does not pause descendants (because descendants aren't enumerated).
- E2E test will FAIL until P1 lands.

After P1 lands:
- `repo.get_cascade_tree_ids(mid_tree_child)` returns `[mid_tree_child, …descendants]` (P1's wrapper handles permanent `parent_id` enumeration).
- B5 fix pauses the whole subtree rooted at `mid_tree_child`.
- E2E test passes.

**Hard sequencing:** P1 must be merged (or in the same atomic worktree commit) before Task 3.3 can be verified. The unit tests in Task 3.3 (cases 1, 4, 6) can run before P1 because they only test the response shape and parameter routing. The e2e test (mid-tree subtree enumeration) is the one that requires P1.

---

## Architect Flags

1. **[ARCHITECT] B5 — Subtree vs Whole-Tree vs Soft-Stop decision** — **RESOLVED in Rev 2** per `architecture-recommendation.md §4.1` (option i, weighted 3.90): **SUBTREE via `cascade_to_root: bool = True` boolean parameter**. Node-only rejected outright — pausing X while descendants run is the precise parent/child divergence state B2's machinery exists to survive. Helper = post-merge refactor if a second flag appears.
   - **Rev 2 (AF-B5) — both-branch wrapper mandate:** the `cascade_to_root` sketch's BOTH branches must call `repo.get_cascade_tree_ids(...)` (P1's kill-switch wrapper), NOT raw `repo.get_tree_ids(...)`. The plan as written (Rev 1) only corrected the else-branch — the True-branch at `:2056` is the EXISTING whole-tree path that `/pause`, messaging, and watchover traverse. Implemented verbatim, those callers silently bypass P1's kill-switch.
   - **Rev 2 (AF-B5) — 5 internal callers:** `pause_instance_cascade` is called from 5 internal sites besides the router: `instance_messaging.py:1119, :3748`, `watchover_service.py:1004, :1470`, manager facade `manager.py:7690`. The default `True` is load-bearing for watchover and messaging, not just `/pause`. Test case 7 (Task 3.3) pins this directly; case 8 covers kill-switch propagation.
   - **User-visible behavior change for `/stop`** (was whole-tree by accident, now subtree by design). Mitigation:
     - `/stop` is documented `deprecated=True` (line 1367); behavior change is on a deprecation path.
     - `/pause` semantics are UNCHANGED (the parameter default preserves whole-tree behavior).
     - No external callers found (research line 86-88).
     - Doc note added (Task 3.12) clarifies the new contract.
   - **Open for review (preserved from Rev 1):** the architect may prefer to (a) leave `/stop` broken-on-deprecated (defer until removal), (b) implement soft-stop (the original stop-instance-button plan), or (c) keep whole-tree (matches `/pause`, avoid surprise). If (c), this task becomes "no code change, just doc note + accelerate deprecation".

2. **[TIMEBOXED] B6 — Diagnosis scope** — **RESOLVED in Rev 2** per `architecture-recommendation.md §5.1-5.3` (probe-first timebox, option A, weighted 4.00). 4h hard cap. Probe checklist adopted into Tasks 3.4–3.6 (5 ordered probes, each ≤30min); exit condition hardened to require 404-body class identification AND seam classification OR harness-artifact confirmation. The architect confirms "no small seam found → ticket only" is an acceptable Phase 3 outcome (B6 is 🟠 with a live workaround; bounded elimination has durable value).

3. **[ASSESSMENT-ONLY] B7(a), B7(c), SSE + **[RE-SCOPED] B7(b)** — **RESOLVED in Rev 2** per `architecture-recommendation.md §6`. B7(a), B7(c), SSE remain assessment-only follow-up tickets. **B7(b) re-scoped from "FIX — trivial COALESCE guard" to "VERIFY + PIN last-settle semantics"** — see Rev 2 corrections to Task 3.7 (default `False` mandatory), Task 3.8 (DELETED), Task 3.9 (inverted to pin re-arm→re-complete semantics), §B7(b) site table (4th stamp site added), Risk rows 4/5 (rewritten — real risk is opposite direction), and §Rev 2 Changelog.

---

## Risk Table

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | B5 behavior change confuses users who relied on the "always pauses root" behavior | Medium | Low | `/stop` is `deprecated=True`; 0 external callers (research); doc note added (Task 3.12); `/pause` unchanged |
| 2 | B5 subtree semantics conflict with `/pause` whole-tree semantics in user's mental model | Medium | Medium | The two endpoints now have DISTINCT semantics; docs need to clarify (Task 3.12); unit test case 6 pins `/pause` unchanged |
| 3 | B6 diagnosis destabilizes the resume cascade (instrumentation side-effects) | Medium | Low | Read-only instrumentation first (SQLAlchemy echo, SELECT snapshots); do NOT mutate any DB row during diagnosis; if instrumentation reveals a need to mutate, stop and write ticket |
| 4 | B7(b) **preserve-on-default** (Rev 1's intended COALESCE behavior) freezes false `completed_at` on legitimately re-armed/retried jobs — opposite direction of Rev 1's risk premise | High | Low (default is `False` per Rev 2) | Default `False` MANDATORY per `architecture-recommendation.md §6.2`; Task 3.8 (wiring `True`) DELETED in Rev 2 — produces same false-timestamp corruption. Verified re-arm path (`repository.py:1974-2167`, `job_feedback_observer.py:1470-1474`) means re-arm→re-complete stamps `completed_at=T2` (last-settle) by design; preserve-on-default would freeze stale T1 |
| 5 | B7(b) reclassification rests on an inferred mechanism (re-arm + resume-finalize composition); the **30-min repro-DB check** (D2) must be completed BEFORE concluding "not a defect". **FLIP CONDITION:** if re-stamped jobs never transited `admission_state='active'`, an unguarded raw UPDATE exists somewhere the audit missed → revert to option B (wire `True` at 3 sites) | Medium | Low | Manual evidence file `tests/manual/b7b_rearm_admission_history.md` documents the repro-DB check; Case 1 test in Task 3.9 pins last-settle semantics; if FLIP CONDITION triggers, re-enable Task 3.8 wiring |
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
| 4 | B7(b): re-arm→re-complete stamps `completed_at=T2` (last-settle semantics), NOT T1 (first-settle) | Task 3.9 unit test case 1 | 100% pass; assertion `completed_at == T2 > T1`; verified via `rearm_with_lock` cycle in the test driver |
| 5 | B7(b): re-arm→re-fail and re-arm→re-cancel both re-stamp `completed_at` to last terminal event | Task 3.9 unit test cases 2–3 | 100% pass; same invariant as criterion 4 |
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
| `daemon/repositories/job_queue/repository.py` | **Rev 2:** Add `preserve_completed_at: bool = False` to `atomic_transition` at `:1134` (flag DEFINED, NO call-site wiring in Phase 3 — Task 3.8 DELETED per AF-P3-7). 3 stamp sites (`complete_job:2275`, `fail_job:2298`, `terminate_job:2504`) UNCHANGED in behavior; 4th stamp site (`job_feedback_observer.py:1885-1891` observer fail-safe) documented but not modified | 3.7, 3.9 (3.8 DELETED) |
| `docs/usage.md` | One-line update at `:340` clarifying `/stop` subtree semantics | 3.12 |
| `tests/unit/routers/test_stop_instance_subtree.py` | NEW — 6 unit cases | 3.3 |
| `tests/e2e/test_stop_instance_subtree_pause.py` | NEW — 1 e2e case | 3.3 |
| `tests/unit/repositories/test_job_queue_atomic_transition.py` | NEW — 4 unit cases (Rev 2: re-arm→re-complete pinning, re-arm→re-fail, re-arm→re-cancel, regression) | 3.9 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/FT-001-b7a-future-dated-rows.md` | NEW — follow-up ticket | 3.10 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/FT-002-b7c-status-disagreement.md` | NEW — follow-up ticket | 3.10 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/tickets/FT-003-sse-status-change-fanout.md` | NEW — follow-up ticket | 3.10 |
| `.agents/shared/planning/pause-resume-terminate-tree-fix/p3-b6-diagnosis-bundle/` | NEW — diagnosis evidence (if B6 fix path) OR ticket draft (if ticket path, with §5.3 minimum content) | 3.4–3.6 |
| `tests/manual/b7b_rearm_admission_history.md` | NEW — Rev 2 D2 repro-DB evidence (manual; not pytest); documents the 30-min repro-DB check before concluding "not a defect" and the FLIP CONDITION if re-stamped jobs never transited `admission_state='active'` | 3.9 |

**Total production-code change:** 3 files modified (`instance_lifecycle.py`, `instances.py`, `job_queue/repository.py` — but Rev 2: `job_queue/repository.py` change is flag-add-only, NO call-site wiring) + 1 doc edit (`usage.md`). **Total test addition:** 3 new test files (subtree unit, subtree e2e, atomic-transition unit — Rev 2: last has inverted semantics) + 1 manual evidence file (`b7b_rearm_admission_history.md`) + 1+ new unit tests + 1 new e2e test. **Optional B6 fix:** 1–2 files if seam is small (<100 LOC); otherwise, ticket only with §5.3 minimum content.

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
| 3.7 (B7(b) flag reserved) | `git revert <commit>` — removes the `preserve_completed_at` flag from `atomic_transition`. NO call-site wiring exists (Task 3.8 DELETED in Rev 2), so no call-site revert needed. Existing stamp behavior at `:2275, :2298, :2504` unchanged. |
| 3.9 (B7(b) test) | `git revert <commit>` — removes test file + manual evidence file. No production impact. |
| 3.12 (doc note) | `git revert <commit>` — single-line revert. No code change. |
| Tests (3.3, 3.9) | `git revert <commit>` — removes test files. No production impact. |

**Response-shape compatibility for `/stop`:** preserved exactly. The handler returns `{paused: true, paused_ids: result["paused_ids"], skipped_ids: result["skipped_ids"]}` before AND after the fix; only the CONTENT of `paused_ids` changes (subtree vs whole-tree). No client-side breaking change for consumers reading the response keys.

---

## Defect-to-Task Mapping (cross-reference) — **Rev 2 (3.8 DELETED)**

| Defect | Tasks |
|--------|-------|
| B5 | 3.1, 3.2, 3.3, 3.11, 3.12 |
| B6 | 3.4, 3.5, 3.6 (probe-first timeboxed diagnosis) |
| B7(b) | 3.7 (flag reserved), 3.9 (pin last-settle + D2 repro-DB check) — **Rev 2: Task 3.8 DELETED** (wiring `True` produces false-timestamp corruption); renumber map: no renumbering — 3.9 follows 3.7 directly |
| B7(a) | 3.10 (FT-001 ticket only) |
| B7(c) | 3.10 (FT-002 ticket only) |
| SSE | 3.10 (FT-003 ticket only) |

---

## Open Questions

1. **B5 architect decision** (see §Architect Flags): is subtree semantics acceptable, or should `/stop` be (a) left broken on deprecation, (b) made into soft-stop, or (c) kept as whole-tree?
2. **B6 exit** (see Task 3.6): is "no small seam found, ticket only" an acceptable Phase 3 outcome? (Recommended: yes; cap at 4h.)
3. **B7(b) flag scope** (see Task 3.7 + §Rev 2 Changelog) — **Rev 2:** default `False` is MANDATORY (not just caller-compatible) because the verified re-arm path (`rearm_with_lock` `repository.py:1974-2167`, observer call `job_feedback_observer.py:1470-1474`) means preserve-on-default would freeze stale/failure-time stamps on legitimately re-run jobs. The flag is DEFINED but **NOT wired at any call site in Phase 3** (Task 3.8 DELETED per AF-P3-7). **D2 pending repro check** (leader-accepted "likely working as designed"): 30-min repro-DB query of the twice-re-stamped jobs' admission history for re-arm evidence, BEFORE concluding "not a defect". **FLIP CONDITION** (documented in Task 3.9 and Risk row 5): if the re-stamped jobs **never** transited `admission_state='active'`, an unguarded raw UPDATE exists somewhere the audit missed, and option-B wiring (re-enable Task 3.8) becomes correct — record this flip in the implementation log.

---

## Rev 2 Changelog

**Rev 2 of `phase3-plan.md` — architect-corrected per `architecture-recommendation.md 8abca8b5` (2026-08-24).** Rev 1 preserved in git at commit `cefb9798` for diff. All corrections originate from the architect's P3-cluster review (`§0`, `§4` AF-B5, `§5` AF-B6, `§6` AF-P3-7, `§7` register, `§9-10` decisions/risks). The corrections split into three correctness-gating blocks (AF-B5, AF-B6, AF-P3-7) — each individually shippable as a clean rebase on Rev 1.

### Summary table

| # | Cluster | Severity | What changed in Rev 2 |
|---|---------|----------|-----------------------|
| 1 | AF-B5 | 🔴 correctness-gating | Both-branch wrapper substitution (`get_tree_ids` → `get_cascade_tree_ids`) in the B5 sketch + Task 3.1 acceptance + §Verified Mechanics `:2056` annotation + §Sequencing (lines 349-358) |
| 2 | AF-B5 | 🔴 new finding | 5 internal `pause_instance_cascade` callers (`instance_messaging.py:1119, :3748`, `watchover_service.py:1004, :1470`, `manager.py:7690`) — default `True` is load-bearing; Test case 7 pins, case 8 covers kill-switch |
| 3 | AF-B5 | 🟢 architect flag | §Architect Flags #1 → **RESOLVED in Rev 2** (option i, weighted 3.90, helper-as-post-merge-refactor) |
| 4 | AF-B6 | 🟡 timeboxed | Tasks 3.4–3.6 rewritten with 5-probe checklist (≤5min, ≤15min, ≤20min, ≤30min, ≤30min); static-analysis conclusion recorded (H1 TOP hypothesis); hardened exit condition + §5.3 ticket minimum content |
| 5 | AF-B6 | 🟢 architect flag | §Architect Flags #2 → **RESOLVED in Rev 2** (option A, weighted 4.00; probe-first timebox acceptable at 4h cap) |
| 6 | AF-P3-7 | 🔴 factual correction | §B7(b) factual claim replaced: "ZERO matches / F9 DEFERRED" → verified `rearm_with_lock` citations (`repository.py:1974-2167`, `job_feedback_observer.py:1470-1474`, `job_recovery_service.py:211-216`); F9 CLOSED via this path |
| 7 | AF-P3-7 | 🔴 site table | 4th stamp site added: `job_feedback_observer.py:1885-1891` (observer fail-safe — the plan missed this) |
| 8 | AF-P3-7 | 🔴 task change | Task 3.7 acceptance rewritten: default `False` is MANDATORY (not just caller-compatible) due to verified re-arm finding |
| 9 | AF-P3-7 | 🔴 task deletion | **Task 3.8 DELETED entirely** — wiring `True` produces false-timestamp corruption. Renumber map: nothing renumbers (3.9 follows 3.7 directly) |
| 10 | AF-P3-7 | 🔴 task inversion | Task 3.9 inverted: pin re-arm→re-complete stamps `completed_at=T2` (last-settle), NOT preserve T1. Raise-vs-noop mechanics noted (Rev 1's case-1 spec was mechanically wrong — `atomic_transition` raises `InvalidTransitionError` after rowcount=0) |
| 11 | AF-P3-7 | 🔴 D2 fold-in | 30-min repro-DB check folded into Task 3.9 (leader-accepted "likely working as designed, pending repro"); FLIP CONDITION documented (if re-stamped jobs never transited `admission_state='active'`, option-B wiring becomes correct) |
| 12 | AF-P3-7 | 🔴 risk rewrite | Risk rows 4/5 rewritten: real risk is OPPOSITE direction (preserve freezes failure-time stamps on retry flow) |
| 13 | AF-P3-7 | 🟢 architect flag | §Architect Flags #3 → **RESOLVED in Rev 2** (Approach A adopted: default `False`, NO call-site wiring, B7(b) re-scoped as verify+pin) |
| 14 | (scope) | 🟢 scope discipline | §Defects In Scope B7(b) action column rewritten: "FIX — trivial COALESCE guard" → "Rev 2: VERIFY + PIN last-settle semantics" |
| 15 | (scope) | 🟢 objective | §Objective rewritten: "trivial data-integrity guard (B7(b))" → "verify+pin last-settle test for B7(b) (re-scoped from Rev 1's COALESCE guard)"; testable completion sentence updated to last-settle + D2 check |
| 16 | (test strategy) | 🟢 unit tests | §B7(b) Tests section inverted: Case 1 re-arm→re-complete pinning (Rev 1 was "preserve T1"); Cases 2–3 re-arm→re-fail/re-cancel; Case 4 regression |
| 17 | (cross-ref) | 🟢 cross-refs | Defect-to-Task Mapping, Files Touched, Rollback Story, Open Questions #3, Success Criteria rows 4–5 — all updated for the 3.8 deletion + B7(b) re-scope |
| 18 | (plan-overview mirror) | 🟢 cross-doc | This Rev 2 changelog also serves as the mirror for `plan-overview.md §5` AF-P3-7 row correction (default `False` MANDATORY) and `plan-overview.md §4.4` AF-B5 wrapper extension to BOTH branches — those plan-overview edits are filed separately |

### Renumber map (Task 3.8 deletion)

Per the discipline rule "Do NOT renumber tasks except where structurally forced (task 3.8 deletion)", the renumber map is trivially:

| Rev 1 task | Rev 2 task | Reason |
|------------|------------|--------|
| 3.7 | 3.7 | unchanged |
| 3.8 | **DELETED** | wiring `True` corrupts timestamps |
| 3.9 | 3.9 | unchanged number; semantics inverted (was "preserve T1", now "pin T2 last-settle") |
| 3.10 | 3.10 | unchanged |
| 3.11 | 3.11 | unchanged |
| 3.12 | 3.12 | unchanged |

No downstream renumbering required. Test-case numbering in §Test Strategy (B5 cases 1–8, B7(b) cases 1–4) and §B6 Diagnosis probes (1–5) is independent of task numbering and unchanged.

### Citation verification

All Rev 2 citations were re-verified by direct code-read in this session before this plan was finalized:

- `rearm_with_lock` definition: `daemon/repositories/job_queue/repository.py:1974` ✅
- `rearm_with_lock` call site: `daemon/services/job_feedback_observer.py:1470-1474` ✅
- `rearm_with_lock` reference: `daemon/services/job_recovery_service.py:211-216` ✅
- `rearm_with_lock` UPDATE semantics: `daemon/repositories/job_queue/repository.py:2126-2144` (does NOT clear `completed_at`) ✅
- `atomic_retry` semantics: `daemon/repositories/job_queue/repository.py:1278-1298` (clears `failed_at`, NOT `completed_at`) ✅
- 4th stamp site (observer fail-safe): `daemon/services/job_feedback_observer.py:1885-1891` ✅
- `pause_instance_cascade` internal callers: `daemon/services/instance_messaging.py:1119, :3748`, `daemon/services/watchover_service.py:1004, :1470`, `daemon/manager.py:7690` ✅
- `atomic_transition` raise-vs-noop mechanics: `daemon/repositories/job_queue/repository.py:1245-1259` (raises `InvalidTransitionError` on rowcount=0) ✅
- Static-analysis conclusion for B6: detail `session.get` (`daemon/repositories/instance/repository.py:222-226` + `_enrich_instance:105-107`) vs list `select(Instance)` (`daemon/repositories/instance/repository.py:444-488`) — structurally equivalent on same engine ✅

### Provenance

| Item | Source |
|------|--------|
| AF-B5 corrections | `architecture-recommendation.md §4.1-4.2` (worker `70f6581e`, skill `trade-off-analysis`) |
| AF-B6 corrections | `architecture-recommendation.md §5.1-5.3` (worker `70f6581e`) |
| AF-P3-7 corrections | `architecture-recommendation.md §6.1-6.3` (worker `70f6581e`) |
| Architect aggregation | `architecture-recommendation.md §0` decision table + §7 register |
| Verification | All citations re-read in-code this session; `rearm_with_lock`, `pause_instance_cascade` callers, 4th stamp site, atomic_transition raise mechanics all confirmed live |

### Action required before implementation

1. **Leader approval of Rev 2 disposition** — particularly AF-P3-7 (B7(b) re-scope from "FIX" to "VERIFY+PIN") and AF-B5 (both-branch wrapper mandate).
2. **D2 repro-DB check** (30 min) — must complete BEFORE B7(b) "not a defect" conclusion is locked in. If FLIP CONDITION triggers, revert to option-B wiring (re-enable Task 3.8).
3. **plan-overview.md mirror edits** — `§4.4` (AF-B5 wrapper extended to BOTH branches) and `§5` (AF-P3-7 default `False` MANDATORY) — file separately.
4. **No commits** from this plan per dispatch rules; implementation worker merges all three phases atomically into `feature/pause-resume-terminate-tree-fix`.

---

**End of Phase 3 Plan — Rev 2**
---

## Rev 2.1 Changelog — reviewer-corrected (council 2bb126df)

Rev 2.1 folded two reviewer corrections applicable to phase3 (W8 + §1.7 cross-ref fix) plus the housekeeping header update. W6 and W7 are phase1-only corrections — they DO NOT appear in this phase3 plan; the phase1-plan Rev 2.1 changelog records them. No source changes; planning-only revision. Rev 2.1 preserves all Rev 2 content; corrections applied in-place at Task 3.1 (§1.7 cross-ref repoint) and Task 3.7 (W8 reservation comment spec).

### W8 — `preserve_completed_at` zero-caller reservation comment spec (Task 3.7)

| # | Issue | Fix |
|---|-------|-----|
| W8.1 | **Flag ships with ZERO callers** (Task 3.8 DELETED at Rev 2 — see Rev 2 Changelog row 9). Future well-meaning wiring of `preserve_completed_at=True` re-introduces the false-timestamp corruption Rev 1 was trying to prevent (preserve-on-re-complete freezes failure-time stamps on re-armed jobs — see `architecture-recommendation.md §6.2` Approach A rationale). The plan alone cannot prevent the wiring — the comment must live in the source-of-truth surface | **Picked strong-reservation-comment spec (reviewer's option A; remove-and-ticket rejected):** Task 3.7 now requires a verbatim reservation comment block at `daemon/repositories/job_queue/repository.py:1134` (the `atomic_transition` signature) — copy/paste exact text: `# Reserved for a deliberate first-touch-timestamp caller; do NOT wire without re-reading architecture-recommendation.md §6.2 — preserve-on-re-complete freezes failure-time stamps on re-armed jobs. See Phase 3 plan Rev 2.1 (council 2bb126df W8) for the rationale. Cross-ref §6.2 (Decision table — Approach A: Default False, no call-site wiring; re-scope B7(b) as verify+pin).` Acceptance evidence: the diff for Task 3.7 MUST include this comment block at `:1134`; reviewer will check the line is committed. The flag remains DEFINED but UNWIRED at all 4 stamp sites (`complete_job:2275`, `fail_job:2298`, `terminate_job:2504`, observer fail-safe `job_feedback_observer.py:1885-1891`) |
| W8.2 | **Alternative considered but rejected (noted for transparency):** remove the flag entirely (reviewer's option B; remove-and-ticket) — file ticket for future "first-touch" caller to re-introduce it. Rejected because the flag is cheap (one parameter, one SQL branch), the architectural intent is preserved by the reservation comment, and removing + re-adding later is more invasive than keeping + guarding | Documented as the rejected alternative in Task 3.7's Rev 2.1 annotation |

**W8 reservation surface (Rev 2.1, source-of-truth):** the comment at `repository.py:1134` is the safety net until a deliberate first-touch caller arrives. The comment must (a) NAME the reservation intent ("deliberate first-touch-timestamp caller"), (b) include the explicit "do NOT wire without re-reading" guard, (c) cross-reference `architecture-recommendation.md §6.2` so the next reader finds the rationale without grepping plan files, and (d) repeat the load-bearing "preserve-on-re-complete freezes failure-time stamps on re-armed jobs" warning so the risk is in the source-of-truth surface, not only in the plan.

### §1.7 cross-ref fix — Task 3.1 acceptance (suggestion #8)

| # | Issue | Fix |
|---|-------|-----|
| §1.7.1 | **Rev 2 Task 3.1 acceptance cited `phase1-plan §1.7`** for the mock-migration scope. Section §1.7 does NOT exist in `phase1-plan.md` (verified: phase1 sections are `## Objective`, `## Research Corrections`, `## Recommended Approach`, `## Scope Decision Table`, `## Tasks`, `## Exact Files / Functions Changed`, `## Coupling`, `## Risks`, `## Test Strategy`, `## Rollback Story`, `## Architect Flags — RESOLVED`, `## Success Criteria`, `## Open Questions / Follow-up Tickets`, `## Exit Criterion`, `## Rev 2 Changelog`; no §1.7 subsection under any). The citation was meant to point at the kill-switch / blast-radius content | **Repointed to `architecture-recommendation.md §1.6-1.7`** per reviewer's fallback rule ("if no correct target exists in phase1-plan, repoint to architecture-recommendation.md §1.6-1.7"). §1.6 is "Kill-switch verdict — KEEP, time-boxed, hardened" and §1.7 is "Blast-Radius Inventory" — the two sections the citation was actually trying to reach. Task 3.1 acceptance now reads: `mock migration of 11 suites per phase1-plan Task 1 acceptance (W6 corrected path list — Rev 2.1; the Rev 2 citation 'phase1-plan §1.7' was wrong because phase1-plan has no §1.7 — see architecture-recommendation.md §1.6-1.7 for the blast-radius + kill-switch verdict that the citation was trying to reach)`. Cross-ref is now both correct AND self-explains why it points outside phase1-plan |
| §1.7.2 | **Bonus surface:** the W6 correction (11 suites, not 8) is also folded here. The Rev 2 "mock migration of ~8 suites per phase1-plan §1.7" line is updated to "mock migration of 11 suites per phase1-plan Task 1 acceptance (W6 corrected path list — Rev 2.1)" so the cross-ref points to the new authoritative mock-migration scope from the phase1 Rev 2.1 | One-line replacement in Task 3.1 acceptance; no other Rev 2 line references the broken §1.7 citation |

### Rev 2.1 housekeeping

- **Header (top of file):** `Date:` line rewritten — Rev 2.1 prefix added (`reviewer-corrected per council 2bb126df; W6 (mock-migration scope — phase1-only), W7 (T9→T10 daemon-restart sequencing — phase1-only), W8 (preserve_completed_at zero-caller reservation comment spec at repository.py:1134), and §1.7 cross-ref fix folded — Rev 2 prior: architect-corrected per architecture-recommendation.md 8abca8b5`); `Status:` line updated to Rev 2.1 Draft with W8 + §1.7 cross-ref noted; `Prior Rev:` line extended to enumerate Rev 1 (`cefb9798`) → Rev 2 (`8abca8b5`) → Rev 2.1 (council corrections, planning-only).
- **All corrections applied in-place** — no task renumbering required, no new tasks added, no sections removed.
- **Plan-only revision:** no source changes, no commit. Files re-verified after each edit batch per multi-edit write-verification discipline (grep target patterns + duplicate-adjacent-line scan + tail-completeness check + section-heading frequency).

### Rev 2.1 verification (byte-level read-back after every edit batch)

- **Header:** Rev 2.1 prefix present at lines 3 + 5; Rev 2 prior line preserved for traceability; `## Rev 2 Changelog` reference still present (line 567); `## Rev 2.1 Changelog` cross-ref present (line 5 — points to "§Rev 2.1 Changelog" appended below).
- **Task 3.1 acceptance:** `phase1-plan Task 1 acceptance (W6 corrected path list — Rev 2.1; the Rev 2 citation 'phase1-plan §1.7' was wrong because phase1-plan has no §1.7 — see architecture-recommendation.md §1.6-1.7 for the blast-radius + kill-switch verdict that the citation was trying to reach)` exact text matches the W8-cross-ref fix contract.
- **Task 3.7 acceptance:** `🔵 Rev 2.1 (council 2bb126df W8) — RESERVATION COMMENT SPEC at repository.py:1134` exact text matches the W8 acceptance contract; verbatim comment wording present (copy/paste-able to the implementation diff).
- **File length delta:** Rev 2 = 639 lines; Rev 2.1 = 675 lines (header +2 lines, Task 3.1 acceptance +1 line expanded in-place, Task 3.7 acceptance +1 line expanded to ~3x, Rev 2.1 Changelog section appended ~36 lines including the trailing verification sub-section). The Rev 2 "End of Phase 3 Plan" line is preserved at 640; the Rev 2.1 Changelog is appended AFTER it (not in-place).
- **No regression:** the Rev 2 Changelog (line 567) is intact and still describes the architect corrections; the Rev 2.1 Changelog describes the reviewer corrections separately so Rev 2 vs Rev 2.1 attribution is unambiguous.
