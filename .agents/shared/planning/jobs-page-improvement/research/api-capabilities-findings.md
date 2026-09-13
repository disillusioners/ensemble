# Jobs-Page Redesign — BE/API Capability Inventory + Gap List

Research date: 2026-09-10. Worktree: `agents-ensemble-wt-jobs-page-plan` @ `e72558d1` (branch `feature/jobs-page-improvement`). All file:line refs relative to `daemon/`.

---

## 1. GET /api/jobs — `routers/jobs_crud.py:634-869`

### Query params (jobs_crud.py:638-682)

| Param | Default | Notes |
|---|---|---|
| `status` | None | Comma-separated multi-status (dedup, lowercase, ≤20 tokens → 400; jobs_crud.py:704-711). Aliases resolved via `normalize_statuses` (jobs_crud.py:713, e.g. "running"→"processing"). Validated against `_VALID_LEGACY_STATUSES = {pending, processing, completed, settled, failed, cancelled, dead_letter, paused}` (`repositories/job_queue/models.py:101-104`; `settled` added by M3). |
| `project_id` | None | SQL-level filter. Required if `queue_id` given (422, jobs_crud.py:726-730); queue-belongs-to-project IDOR check (jobs_crud.py:733-747). |
| `queue_id` | None | Only with project_id. |
| `limit` | `DEFAULT_JOB_LIST_LIMIT=50` (`constants.py:17`), clamped `1..MAX_JOB_LIST_LIMIT=100` (`constants.py:20`; jobs_crud.py:723). |
| `include_deleted` | False | Soft-deleted jobs hidden by default. |
| `job_types` | None | M2: comma-separated `task`/`message` filter; unknown values ⇒ honestly-empty page (jobs_crud.py:749-767). |
| `mission_id` | None | **Canonical** single-mission filter (2026-09-07). Identity rule: `mission_id == instance_id` — narrows `job_queue_items.instance_id` in SQL (jobs_crud.py:654-668, 769-785). `min_length=1` → empty string 422s. |
| `instance_id` | None | **Deprecated alias** for `mission_id`; used ONLY when `mission_id` absent (jobs_crud.py:783-785); empty string accepted = filter-by-empty ⇒ empty page. |

**NOT supported:** no `source` filter, no `agent_id` filter, no `offset`, no `cursor`, no `root_only` param, no `kind` param (always JobItem rows).

### Ordering
Newest-first (`created_at DESC`): SQL-side via `service.list_jobs`, resolver side `work_resolver.py:2192` and final sort `work_resolver.py:1452`.

### Response shape — `JobListResponse { jobs: JobResponse[], total: int }` (jobs_crud.py:866-869)

⚠️ `total = len(job_responses)` — page length, **not** a real total (explicit comment at jobs_crud.py:868: "for accurate total, would need a count method").

Per-row `JobResponse` fields (built in `_job_to_response`, jobs_crud.py:67-315):
- `job_id`, `status`, `admission_state`, `priority`, `agent_id`, `agent_dir`, `project_id`, `queue_id`, `instance_id`, `created_at`, `started_at`, `completed_at`, `result_summary`, `error_message`, `source`, `job_metadata`, `cancelled_at` (always `null` since Phase 5; jobs_crud.py:212-215), `idempotency_key`, `position` (only for QUEUED + project_id rows), `message`, `dlq_reason`/`retry_count`/`moved_to_dlq_at` (DEAD rows only, from DeadLetterItem; jobs_crud.py:846-855), `deleted_at`, `terminal_reason` (jobs_crud.py:223-235).
- Fix-C split fields: `job_type` (`"task"` mission vs `"message"` mirror receipt) + `mission_liveness` (canonical instance status, mirror rows only; jobs_crud.py:236-265).
- M1 mission projection: `mission_id`, `mission_epoch`, `mission_terminal_reason` (jobs_crud.py:278-292; always-on, no kill-switch).
- M2 guardrails: `outcome` (always `null` on transport) + `mission_ref {mission_id, agent_id, liveness}` (jobs_crud.py:293-399).

Enrichment: rows are batch-resolved through `WorkResolverService.list_work` — ONE `SELECT … WHERE instance_id IN (...)` (jobs_crud.py:798-833), resolver-supplied `status`/timing are instance-authoritative.

### root_only behavior (implicit)
`/api/jobs` has **no** root_only param, but the enrichment call at jobs_crud.py:820-824 uses the resolver default `root_only=True` (`work_resolver.py:1124`). Child-instance JobItems still appear in the page (the SQL page fetch has no parent filter) but their resolver match is filtered out ⇒ `work_record=None` ⇒ `mission_id=None`, `mission_liveness=None`, status falls back to `_derive_legacy_status` off JobItem mirror columns (jobs_crud.py:147-182, 247-255) — i.e. **child-bound mission_id drops to null**; `mission_ref` degrades to the legacy shape keyed off `job.instance_id` (jobs_crud.py:340-369).

### KNOWN DEFECT — multi-status combo with 'settled' (external dependency; parallel arc `fix/jobs-status-combo-filter`)
Symptom (live-measured, critical notes): `status=settled,failed` returns fewer rows than `status=failed`; settled rows with `terminal_reason=NULL` are dropped entirely.
Exact code path:
- Router passes statuses into `service.list_work(status=..., kind="job")` at jobs_crud.py:819-824.
- Resolver splits tokens and builds filters: canonical→source union at `work_resolver.py:1283-1286` (`unioned.update(_CANONICAL_TO_SOURCES.get(canonical, {canonical}))` — note: **no source maps TO `settled`**, so the Task-side token resolves via the `{canonical}` fallback to a never-matching set) and JobItem-side `job_status_filters = _canonical_to_job_filters(...)` at `work_resolver.py:1293`.
- **M3 predicate** (`_canonical_to_job_filters`, `work_resolver.py:602-683`):
  - `completed` → `JobStatusFilter(admission_state='done', terminal_reason='completed', terminal_reason_null_allowed=True, job_type='task')` (work_resolver.py:622-627) — now TASK-only.
  - `settled` → `JobStatusFilter(admission_state='done', terminal_reason='completed', terminal_reason_null_allowed=False, job_type='message')` (work_resolver.py:628-645) — STRICT, no NULL hedge. A mirror row with `terminal_reason=NULL` matches NEITHER `completed` (job_type gate) NOR `settled` (NULL hedge off) ⇒ dropped.
  - `failed`/`cancelled` → `admission_state='done' AND terminal_reason=<value>`, kind-agnostic (work_resolver.py:646-665).
- SQL assembly: OR-of-ANDs in `_query_jobs`, with the M3 `AND job_type = ?` clause (work_resolver.py:2149-2191, job_type clause at :2187-2188). Suspicion recorded in critical notes: the settled-branch `job_type='message'` OR-interaction drops `failed` rows in combos (settled,failed=2 vs failed=3, live-measured).
Plan should cite this as an external dependency being fixed in `fix/jobs-status-combo-filter` (worktree `agents-ensemble-wt-jobs-combo`) — do NOT plan around it; the fix will land independently.

---

## 2. GET /api/work — `routers/work.py:109-191`

Params (work.py:109-136): `status` (canonical, comma-multi OK), `project_id`, `instance_id` (single), `kind` (`"job"` | `"report"` only — `"turn"`/`"task"` removed, 400 on unknown; work.py:99-106), `root_only` (default **True** — drops work whose backing instance has `parent_id`; work.py:127-134).

Response: `list[dict]` — `WorkRecord.to_dict()` verbatim (work_resolver.py:315-389): `work_id` (NOT `job_id`), `kind`, `status` (canonical incl. `settled`), `instance_id`, `project_id`, `agent_id`, `result_summary`, `error`, `created_at`, `started_at`, `completed_at`, `message_id`, `job_type`, `mission_liveness`, `mission_id`, `mission_epoch`, `mission_terminal_reason`, `outcome` (always null), `mission_ref`.

Differences vs /api/jobs:
- Resolver-native union: JobItems (`kind="job"`) + report Tasks (`kind="report"`); no legacy status vocabulary (canonical only, no validation/400 on unknown tokens — unknown falls back per-token).
- No `limit` at all — full unpaginated union, `created_at DESC` (work_resolver.py:1452).
- No DLQ enrichment, no queue position, no `total`, no `include_deleted` nuance (soft-deleted invisible via `_query_jobs`, work_resolver.py:2144), no job_types filter; but `root_only` IS exposed (jobs surface only gets it implicitly).
- Thin wrapper over `WorkResolverService.list_work` (signature work_resolver.py:1118-1125).

---

## 3. GET /api/missions — `routers/missions.py:172-269`

**Paged**: `limit` (default `DEFAULT_PAGE_LIMIT=10`, clamp 1..`MAX_PAGE_LIMIT=100`), `offset` (≥0) — missions.py:181-182, 248-249.
Other params: `liveness` (comma-multi OR; values = canonical targets minus dead_letter = {pending, processing, paused, completed, failed, cancelled}; 400 on unknown — missions.py:102-141, 183-191; `mission_resolver.py:149-153`), `agent_id` (exact match).
Ordering (SQL): `last_activity_at DESC NULLS LAST`, tiebreak `mission_id ASC` (missions.py:207-210).
Identity: **`mission_id == instance_id`** (missions.py:200-201).
Response `MissionListResponse { missions, total, limit, offset, has_more, degraded }` — honesty-carrying pagination; transient DB error ⇒ 200 + empty page + `total=null`, `has_more=null`, `degraded=true` (missions.py:221-227, 258-269).
Per-mission fields (`MissionResponse`, missions.py:144-166): `mission_id`, `agent_id`, `parent_mission_id`, `liveness`, `terminal_reason`, `epoch` (always 1 today — mission_events log is M4(ii); mission_resolver.py:78-90), `linked_jobs`, `started_at`, `last_activity_at`, `title`, `initiative_preview` (honest nulls, no server-fabricated fallbacks).
List is deliberately UNSCOPED (all instances' missions); subtree filtering via `parent_mission_id` client-side is the sanctioned pattern (missions.py:202-205).

### GET /api/missions/{mission_id} — missions.py:272-284+
Single mission by id (identity == instance_id); 404 unknown; 503 unwired resolver. Same `MissionResponse` shape.

---

## 4. GET /api/instances — `routers/instances.py:384-518`

Params: `limit` (default 10, clamp 1..100), `offset`, `project_id`, `exclude_kb` (default True — drops experiencer/kb-importer), `include_descendants` (default True: paginate by ROOT then BFS-load all descendants of each root in page; False = cheap flat paginated rows), `search` (case-insensitive substring over instance_metadata.title / initiative_message / agent_name / agent_id), `order` (`pinned` default | `activity` — live non-terminal roots first so pins can't push them off-page; 422 on other values) — instances.py:387-423.
Response `InstanceListResponse { instances, total (real count), limit, offset, has_more, truncated }` (instances.py:509-518).
Per-instance: `instance_id`, `agent_id`, `agent_tag`, `agent_dir`, `status`, `parent_id`, `title`, `initiative_message`, `children`, `mcp_tool_names`, `model`, `watchover_*`, `created_at`, `updated_at`, `project_id`, `pinned`, `color_tag`, `icon_tag`, `pinned_at` (UI prefs merged at API layer, instances.py:468-507).

Mission-grouped jobs view — client-side join feasibility:
- Job rows carry `mission_id` (== instance_id) and `mission_ref` ⇒ client can group by `mission_id`.
- `title`/`initiative_preview` do NOT exist on job rows or WorkRecords — they must come from `GET /api/instances` (title, initiative_message) or `GET /api/missions` (title, initiative_preview, linked_jobs, parent_mission_id). Both are offset-paged (10/100) with no batch-by-ids endpoint ⇒ a jobs page spanning many missions needs either several missions/instances page fetches or a per-group lazy fetch. Grouping itself (subtree via parent_mission_id, liveness) is client-side feasible; anything wanting server-side "jobs grouped by mission in one call" would need BE.

---

## 5. Defer-blocked endpoint — `GET /api/queues/defer-blocked`

Route: `routers/queues.py:599-659` (`system_queues_router`, mounted under `/api` — see queues.py:41, 665). No params.
Response `DeferBlockResponse` (`routers/schemas.py:1525-1553`):
- `defer_blocked: bool` — the gate's busy predicate (display truth == gate truth; witness SELECT derived from the same `_idle_predicate_sql` constants the gate evaluates, queues.py:613-620).
- `pending_count: int` — PENDING non-deleted JobItems on defer-type queues system-wide.
- `holders: list[DeferBlockHolderResponse]` (schemas.py:1454-1522): each `{instance_id, agent, status, since, kind}` where `kind ∈ {paused, stalled, live}`; ordering paused > stalled > live (queues.py:648-649).
- FE severity is a client-side conjunction (queues.py:622-632): AMBER if any holder kind is paused/stalled; INFO if all live; RED anomaly = `pending_count > 0 && holders == []`.
Zero DML; DB errors propagate (fail-closed, no degrade shape). Related actions exist: `POST /api/jobs/defer-holders/{instance_id}/force-complete` and `.../resend-foreground` (jobs_management.py:951-953, 1021 area); cleanup preflight `GET /api/jobs/cleanup/preflight` also reports `defer_blocked_count` (jobs_management.py:599-607, 790-828).

---

## 6. SSE surfaces

- **Per-job stream**: `GET /api/jobs/{job_id}/events` (`routers/jobs_streaming.py:247-385`). SSE via sse-starlette `EventSourceResponse`, keepalive ping 5s (jobs_streaming.py:384). Event vocabulary: `connected` (initial full payload), `status_update` (payload + `previous_status`), `completed` (terminal payload), `error` (job deleted / stream error). Internally a 2s poll loop that closes on terminal status (jobs_streaming.py:321-363); terminal detection uses `TERMINAL_STATUSES = {completed, settled, failed, cancelled, dead_letter}` (jobs_crud.py:53-59).
- **List-level / global jobs stream: does NOT exist.** The only other SSE surface is notifications.
- **Notifications stream**: `GET /api/notifications/stream` (`routers/notifications.py:29-90`). Global SSE; events: `connected`, `instance_created`, `notification` (root-instance completion notifications carrying instance_id, agent_id, name, status, timestamp). Relevance to a jobs page: it can signal "a root mission finished" but carries no job-level rows/statuses — page-level live job updates are not feedable from it today.
- Internal-only (not HTTP): `work_notifier.notify_work_watchers` (`services/work_notifier.py`) — watcher `[JOB_EVENT]` enqueue-message notifications with display glyph map (`completed ✓` / `settled ✓`, work_notifier.py:61-70). This is where per-status display strings live; there is **no `_STATUS_DISPLAY_MAP`** anywhere in `daemon/` (grep-verified — only `_STATUS_CANONICAL_MAP` in work_status.py).

---

## 7. Status vocabulary — two-layer story

- **Canonical vocabulary** (`services/work_status.py`): `pending`, `processing`, `paused`, `completed`, `settled`, `failed`, `cancelled`, `dead_letter`. Terminal set = `{completed, settled, failed, cancelled, dead_letter}` (work_status.py:144-146); `paused` NOT terminal.
- `_STATUS_CANONICAL_MAP` (work_status.py:66-127) maps BOTH source domains onto it:
  - Task side: pending/running→processing/paused/completed/failed/cancelled.
  - Instance side (execution authority for JobItem rows): idle/waiting/waiting_children/queued/running → `processing`; error → `failed`; terminated → `cancelled`.
  - JobItem admission side: `dead` → `dead_letter`; terminal_reason discriminators `aborted`/`orphan_retired`/`watchover_terminated` → `cancelled`.
- **Transport vs work two-layer terminal (M3, ADR-MISSION-01 §6.6 I3)**: a mirror receipt row (`job_type='message'`) terminalizes as **`settled`**; a task/mission row's terminal is **`completed`** (the row IS its own mission). Per-kind dispatch lives in `_canonical_to_job_filters` (work_resolver.py:602-683) and `_derive_legacy_status` (work_status.py:227+, takes `job_type`). So the same `admission_state='done', terminal_reason='completed'` underlying row reads as `settled` (mirror) or `completed` (task) depending on `job_type`.
- **dead_letter**: produced only by JobItem `admission_state='dead'`; DLQ enrichment adds `dlq_reason`, `retry_count`, `moved_to_dlq_at` on both single GET (jobs_crud.py:606-615) and list rows (jobs_crud.py:846-855). `terminal_reason` is the discriminator for `done` rows (completed/failed/cancelled/aborted/…; jobs_crud.py:223-235).
- Wire-accepted filter vocabulary on /api/jobs: `_VALID_LEGACY_STATUSES` incl. `settled` and `paused` (models.py:101-104). Reverse map `_CANONICAL_TO_SOURCES` + `_JOB_CANONICAL_TO_ADMISSION` (work_resolver.py:433-495).

---

## 8. GAP LIST for the plan

| # | Need | Verdict |
|---|---|---|
| a | Fetch all jobs of one mission/instance in a single call | **Already possible**: `GET /api/jobs?mission_id=<id>` (canonical; instance_id alias deprecated) and `GET /api/work?instance_id=<id>`. Composes with status/job_types/project filters. |
| b | Pagination on /api/jobs (offset or cursor) | **Needs BE.** Only `limit` (1..100, default 50) exists — no `offset`, no cursor anywhere on /api/jobs; /api/work has no pagination at all. No cursor-style pagination exists anywhere in the API (grep-verified): the house pattern is limit/offset. |
| c | Real total-count / has_more / cursor fields | **Needs BE.** `JobListResponse.total` is just `len(page)` (jobs_crud.py:866-869, self-admitted); no `has_more`. Copy the house response shape from missions (`total/limit/offset/has_more` + `degraded`, missions.py:262-269) or instances (adds `truncated`, instances.py:511-518). |
| d | Instance title / initiative preview on job rows | **Needs BE for efficiency** (fields don't exist on JobResponse/WorkRecord). Client-side workaround already possible: group jobs by `mission_id`, then join against `GET /api/missions` (`title`, `initiative_preview`, `parent_mission_id`, `linked_jobs`) or `GET /api/instances` (`title`, `initiative_message`) — but both are 10/100-offset-paged with no batch-by-ids endpoint, so wide job pages force multiple join fetches. |
| e1 | Page-level live updates | **Needs BE** for a true live page: only per-job SSE exists (`/api/jobs/{job_id}/events`, 2s-poll, closes on terminal) plus the root-instance-completion notifications stream (no job rows). FE-only alternative today = polling /api/jobs on an interval. |
| e2 | Mission-grouped server-side view | **Needs BE** if "one call → missions with their jobs nested" is desired; today grouping is client-side via `mission_id` on rows + `parent_mission_id`/`linked_jobs` on missions rows (sanctioned client-side pattern, missions.py:202-205). |
| e3 | root_only control on /api/jobs | **Needs BE** on the jobs surface (child-instance rows currently render with degraded/null mission fields, see §1 root_only). Client-side, `GET /api/work?root_only=false` already returns the full root+child union if the FE can consume canonical statuses + work_id naming. |
| e4 | Multi-status `settled` combo defect | **External dependency** — being fixed in parallel arc `fix/jobs-status-combo-filter` (worktree `agents-ensemble-wt-jobs-combo`, base e72558d1). Pin as cited in §1; do not design around it; plan phases that filter `settled` combos should note the dependency. |
| e5 | source/agent filters on /api/jobs | **Needs BE** if the redesign wants them (neither exists); `job_types` + status + project + queue + mission are the only row filters today. |
| e6 | Single-call union of jobs + reports | **Already possible** via `GET /api/work` (kind omitted) — but note different field names (`work_id` vs `job_id`, `error` vs `error_message`, canonical-only statuses, no pagination). |

### Copy-worthy conventions
- Offset pagination + honesty metadata: missions.py:212-215, 258-269 (total/limit/offset/has_more/degraded); instances.py:387-456, 509-518 (adds root-based pagination + truncated).
- 503-if-unwired service DI pattern for any new list surface: work.py:52-84.
- Unknown filter values ⇒ 400 (liveness, missions.py:129-137) or honestly-empty page (job_types, jobs_crud.py:749-767) — pick one deliberately per the §8.2 precedent.
