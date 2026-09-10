# Jobs Page — Current-State Findings (ground-truth audit)

Worktree: `agents-ensemble-wt-jobs-page-plan` @ `feature/jobs-page-improvement` (base `e72558d1` per meta-KV lease, 2026-09-10T19:58:10Z). Drift check: no bash available to Explorer; verified via worktree `.git` pointer → `HEAD = ref: refs/heads/feature/jobs-page-improvement` — branch matches, no drift detected.

Scope: read-only audit of `frontend/src/app/pages/jobs/` + consumed services/models/dialogs.

---

## 1. File inventory, component tree, data flow

| Path (under `frontend/src/app/`) | LOC | Role |
|---|---|---|
| `pages/jobs/jobs.component.ts` | 1252 | Page container: signals, filters, view modes, actions, SSE glue, preflight, dialogs |
| `pages/jobs/jobs.component.html` | 298 | Template: header, filter bar, queue sidebar, card list, detail drawer |
| `pages/jobs/jobs.component.scss` | 603 | Styles; 768px breakpoint (:573), prefers-reduced-motion (:492) |
| `pages/jobs/jobs.component.spec.ts` | 2085 | Plain-TS logic-mirror spec (no TestBed), 42 describe blocks |
| `services/job.service.ts` | 446 | `/api/jobs` CRUD + DLQ + cleanup + defer-holder actions |
| `services/work.service.ts` | 98 | Single read method `getWork` → `GET /api/work` |
| `services/job-sse.service.ts` | 249 | Per-job EventSource with backoff reconnect |
| `models/job.model.ts` | 443 | `Job`, `JobStatus` (incl. `settled`), `JobFilters`, `isTerminalStatus`, `missionLivenessChip` |
| `models/work.model.ts` | 153 | `Work`, `WorkKind` ('job'\|'report'), chip helpers |
| `models/defer-blocked.model.ts` | 177 | Defer holder payload + severity/tooltip/action helpers |
| `models/cleanup-preflight.model.ts` | 89 | Preflight wire type + verbatim-pinned dialog copy consts |
| `components/job-card/` | 262 TS + 179 html | Card with expandable details + 5 actions |
| `components/job-detail-drawer/` | 150 TS + 227 html | Drawer content (reused standalone too) |
| `components/queue-list/` | ~343 TS | Queue sidebar (creates/starts/stops/deletes queues via `QueueService`) |
| `components/system-cleanup-confirm-dialog/` | 166 (inline template) | Two-stage destructive confirm |
| `components/job-create-dialog/`, `confirm-dialog/`, `searchable-select/`, `mission-liveness-chip/` | — | Supporting dialogs/selects |

Route: `app.routes.ts:21` — `{ path: 'jobs', loadComponent: … JobsComponent }`.

**Data flow (queues view):** `JobService.listJobs(filters)` (`jobs.component.ts:528-543`) → `jobs` signal (:104) → `filteredJobs` client-side re-filter (:230-249) → `displayedJobs` (:262-267) → `@for (job of displayedJobs())` renders `<app-job-card>` (`html:261-272`). Queue sidebar (`html:166-175`) → `QueueService` internally; emits `queueSelected` → `selectedQueueId` + `filters.queue_id` (:865-872).

**Data flow (all-work view):** `WorkService.getWork({project_id, status, root_only:false})` (`jobs.component.ts:614-642`) → `works` signal (:190) → `workToJob` mapping (:292-317, lossy: `message: undefined`, `priority: 0`, `source: undefined`, `queue_id: null`) → same `displayedJobs` → same cards.

**SSE:** `JobSseService.latestStatus` → constructor `effect` (:389-394) → `updateJobFromSse` patches BOTH `jobs[]` (:695-734) and `works[]` (:746-760) arrays in place.

**Other inputs:** `ProjectService.projects` (project filter + pause map, :199-217), `ApiService.listAgents` (agent filter, :644-653), raw `HttpClient` for preflight (:95, :560-590).

---

## 2. View modes, endpoints, filters

**Switching:** `mat-button-toggle-group` Queues / All Work (`html:15-28`) → `onViewModeChange` (:784-797) sets `viewMode` signal, persists to `localStorage['job-page-view-mode']`, restores on load (:493-508). Sidebar hidden in all-work (`html:166`). New Job button disabled in all-work (`html:88`).

**Endpoints per mode:**
- Queues: `GET /api/jobs` — params `status` (comma-joined), `source`, `agent_id`, `project_id`, `queue_id`, `include_deleted` (`job.service.ts:85-106`).
- All Work: `GET /api/work` — params `status`, `project_id`, `instance_id`, `kind`, `root_only` (`work.service.ts:51-78`); the page forwards ONLY `project_id`, `status` (comma-joined), and hard-codes `root_only:false` (`jobs.component.ts:614-624`).

**Filter state:** lives in the `filters` signal (`jobs.component.ts:117`) + `showDeleted` signal (:179) + `selectedQueueId` (:113). Project id persisted in `localStorage['job-page-selected-project']` (:97, :823-841) — **no URL query-param binding anywhere** (filters are not deep-linkable).

**Filter inventory:**
- Status: multi-select chips, 8 options incl. `{ value: 'settled', label: 'Settled (receipt)' }` (:349-358) — caller claim confirmed.
- Source: `all|api|telegram|scheduler|webhook` (:361-367).
- Agent: derived from `agents()` with `all` sentinel (:382-385).
- Project: searchable select, required-ish (hint when unset, `html:31-36`; persisted).
- Queue: via sidebar (queues view only).
- Show Deleted checkbox (`html:154-159`) → `include_deleted=true` (:856-863).

**Corrections/nuances vs caller summary:**
1. There are effectively **7 filters** (queue + show-deleted are missed by the summary).
2. **In the all-work view, source/agent/show-deleted are dead controls**: `loadWorks` forwards only project+status (:614-624), `filteredJobs` only touches `jobs()` not `works()`, and `workToJob` nulls `source` (:297) so the agent filter could never match anyway. The controls stay visible and clickable with no effect.
3. **Vocabulary split against one endpoint**: `listActiveJobs` maps to BE-internal `status=queued,active` (`job.service.ts:108-128`) while the page sends FE names `pending,processing,…` — two vocabularies coexist on `GET /api/jobs`.

---

## 3. Refresh & scale

- Auto-refresh: `setInterval(30000)` in `startAutoRefresh` (:655-670), mode-aware (all-work → `loadWorks`, queues → `jobService.refreshJobs`). Skips only when a fetch is already in flight. **No pause on hidden tab** (no `visibilitychange` handling), **no pause when drawer/dialog open**. ngOnInit fires `loadJobs` + `loadAgents` + `loadProjects` + `loadWorks` + `refreshBadStateCount` in parallel (:443-455).
- **Unbounded confirmed**: `listJobs` sets no `limit`/`offset`/cursor (`job.service.ts:85-96`; only `listRecentJobs` uses `limit=10`, and that feeds the header indicator, not this page, :138-145). `getWork` likewise has no paging params (`work.service.ts:51-64`).
- **Full-array render confirmed**: `@for (job of displayedJobs(); track job.job_id)` (`html:261-272`) renders every row; no `cdk-virtual-scroll*`, no slicing, **no row-count guard anywhere**. Each 30s tick replaces the whole array (`jobs.set(jobs)` :534 / `works.set(works)` :626).
- Only client-side mutation between polls: SSE patches (:679-761) and local delete/cancel updates (:1006-1033).

---

## 4. Job cards & actions

Card (`components/job-card/`): badges (priority hidden for task-backed rows, status chip, kind chip, receipt chip `message`, mission-liveness chip, paused, deleted; `job-card.component.html:1-56`), message preview (100-char, falls back to `result_summary`, `job-card.component.ts:92-100`), expand toggle (`html:74-80`) revealing **Job ID, Instance ID, Project ID, Source, full Message, Created/Started/Completed** (`html:82-132`).

Actions (card `html:135-178` → page handlers):
| Action | Guard | Service method | Endpoint | Page handler |
|---|---|---|---|---|
| Cancel | pending/processing | `JobService.cancelJob` | `DELETE /api/jobs/{id}` (`job.service.ts:304-320`) | `onCancelJob` :944-983 (ConfirmDialog first) |
| Retry | failed/dead_letter | `JobService.retryJob` | `POST /api/jobs/{id}/retry` (:325-337) | `onRetryJob` :985-1004 |
| Delete | not deleted | `JobService.softDeleteJob` | `DELETE /api/jobs/{id}` (:342-354) | `onDeleteJob` :1006-1033 (Undo snackbar → restore) |
| Restore | deleted | `JobService.restoreJob` | `POST /api/jobs/{id}/restore` (:359-371) | `onRestoreJob` :1035-1045 |
| View | always | opens drawer | — | `onViewJobDetails` :1180-1193 |

Page-only actions: New Job → `POST /api/jobs` (:289-299 via `createJob` :895-923, dialog at :878-893); Retry-All-DLQ (visible only when `dead_letter` status selected AND project chosen, `html:114-134` → `POST /api/projects/{projectId}/dlq/replay-all` :403-410, handler :1047-1081); System Cleanup → `POST /api/jobs/cleanup` (:420-427, handler :1110-1178).

**Note:** cancel and soft-delete share the identical `DELETE /api/jobs/{id}` verb+URL (`:304` vs `:342`) — server disambiguates by state; FE keeps two distinct code paths and optimistic updates.

---

## 5. Side drawer + per-job SSE

Opened by `onViewJobDetails` (:1180-1193): sets `selectedJob` + `drawerOpen`, then **SSE only if `!isTerminalStatus(job.status)`** (terminal jobs get NO SSE at all). Otherwise disconnect + `clearEvents` + `streamJobEvents(job.job_id)`. Closed via `onCloseDrawer` (:1195-1203): clears selection, disconnects, unsubscribes. Drawer is `mat-drawer position="end"` with backdrop (`html:278-296`).

Drawer sections (`job-detail-drawer.component.html`): sticky action bar (Cancel/Retry/View Instance/Copy Job ID, :32-64); **Overview** (agent, priority, source, project, mission-liveness chip, :68-107); **Timeline** (created/started/completed/cancelled/moved-to-DLQ/duration, :111-150); **Message** (`pre`, :154-161); **Result** (only `status==='completed' && result_summary`, :163-178); **Error** (failed|dead_letter, :180-187); **Dead Letter Info** (reason/retry-count/moved-at, :189-215); **Metadata** (`job_metadata` pretty-printed JSON, :217-224). Header has status chip + copy-id (:1-27).

SSE mechanics (`job-sse.service.ts`): `EventSource` on `GET /api/jobs/{job_id}/events` (:65); handles `connected`, `status_update`, `completed`, `error`, `keepalive` (:70-142); exponential reconnect 1s→30s capped at 5 attempts then `failed` (:144-168); debounced error surfacing. Component shows Live/Reconnecting (n/5)/Offline pill while drawer open (`html:38-60`) and snackbars SSE errors (:397-421).

**Correction to caller summary:** it's not just "only while drawer open" — it's "drawer open **AND** the selected job is non-terminal". Also: while the drawer IS open, `updateJobFromSse` patches the whole list, not only the drawer. The service is a single-job singleton (`currentJobId`, :19) — opening another row tears down the previous stream. "View Instance" navigates `/projects/{tabState.activeProjectId|'all'}/instances/{instanceId}` (:1219-1222).

**Bug-grade detail:** Result section gates on `status === 'completed'` only (drawer html:164) — `settled` rows and all-work `report` rows never render their result even when `result_summary` is present (card preview shows it, drawer hides it). Similarly `Message` never renders for all-work rows (`workToJob` sets `message: undefined`, :296).

---

## 6. Defer-blocked surface

On THIS page, defer-blocked data appears **only inside the System-Cleanup dialog**, exactly as the caller claimed:
- `refreshBadStateCount` (:560-590) fires `GET /api/jobs/cleanup/preflight` (raw `HttpClient`, :562) **plus** `GET /api/queues/defer-blocked` (:567-569), merges `defer_blocked_count` (preflight) with `defer_holder_kind` (composed FE-side from the sibling endpoint — preflight does NOT emit it; documented at `cleanup-preflight.model.ts:10-30` and :166-176 of the component).
- Dialog data passes counts + live-instance split (:1125-1133); `SystemCleanupConfirmDialogComponent` renders the defer note + remediation copy (`system-cleanup-confirm-dialog.component.ts:108-117`; copy from `cleanupDeferNote` `cleanup-preflight.model.ts:79-89`; verbatim-pinned consts `CLEANUP_TRUTH_SPLIT_COPY` :45-49, `CLEANUP_TRUTH_SURVIVOR_NOTE` :68-72).
- The actual defer remediation endpoints — `POST /api/jobs/defer-holders/{id}/force-complete` and `POST /api/jobs/defer-holders/{id}/resend-foreground` (`job.service.ts:240-272`) — **exist in the service but are not wired anywhere on this page** (their consumer is the header `JobQueueIndicator`). Discoverability of stuck defers from within the Jobs page is therefore: open System Cleanup, read the count.

---

## 7. Tests

All specs are **plain-TS logic-mirror style — zero `TestBed`** in the page spec (grep confirms). Services are mocked as object literals with `jest.fn()` + real Angular signals (`jobs.component.spec.ts:59-80+`); `localStorage` globally mocked with injected failure modes (:14-57).

Inventory:
- `pages/jobs/jobs.component.spec.ts` — 2085 lines, 42 describes. Notable pins: `root_only=false` contract for `loadWorks` (:1517+); bad-state visibility + cleanup dialog data + snackbar text (:1666-1974); `updateJobFromSse` mission_liveness propagation + M3 terminal `completed_at` stamping (:1975-2085); project persistence scenarios 1–6 incl. storage failures (:1126-1420); drawer navigation URL pattern (:1420-1516); filteredJobs with deleted jobs (:1033); cancel confirm-dialog flow (:672); DLQ retry-all (:740); view-mode agnosticism of template via `displayedJobs` (implied).
- `services/job.service.spec.ts`, `services/job-sse.service.spec.ts`
- `models/job.model.spec.ts`, `models/job-queue.model.spec.ts`, `models/defer-blocked.model.spec.ts`, `models/cleanup-preflight.model.spec.ts` (const-verbatim pins)
- `components/job-card/job-card.component.spec.ts`, `components/job-detail-drawer/job-detail-drawer.component.spec.ts`
- Sibling surfaces: `components/job-queue-panel/…spec.ts`, `components/job-queue-indicator/…spec.ts`

**Gaps:** no spec for `work.service.ts`, none for `system-cleanup-confirm-dialog` component (only its model consts are pinned), none for `queue-list` component, none for `job-create-dialog` verified at this path level.

---

## 8. Pain points (evidence)

1. **Monolith page component** — 1,252 lines injecting 6 dependencies incl. raw `HttpClient` for one preflight call (:95, :560-590), owning filters + view mode + SSE + 3 dialogs + DLQ + cleanup. Filter/view-mode logic, preflight composition, and SSE patching are all candidates for extraction.
2. **Filter state not in URL** — only `localStorage` for project + view mode (:97-98); status/source/agent/queue/show-deleted unshareable, lost on reload; back-button doesn't restore.
3. **Unbounded fetch + full render + 30s wholesale refetch** — no paging params (`job.service.ts:85-96`, `work.service.ts:51-64`), no virtualization, no row cap (`html:261`); every 30s replaces both arrays regardless of tab visibility (:655-670). At BE scale this is the page's primary scalability ceiling.
4. **All-work view filter inconsistency** — source/agent/show-deleted render but no-op (:614-624); silently misleading.
5. **SSE coverage gaps** — no stream for terminal jobs or for any row while the drawer is closed (:1184-1192); single-job singleton stream (`job-sse.service.ts:19,42-53`); list freshness between polls rests entirely on the 30s poll. (Known sibling risk: daemon-side wake latency for terminal receipts.)
6. **Vocabulary mismatch** — page/cards say "Jobs"/"Queues"/"All Work"; sibling `job-queue-panel` says "Live conversations" with instances-primary tree; BE internal `queued/active` vs FE `pending/processing` (`job.service.ts:108-128`); `settled (receipt)` vs `completed`; `WorkKind` chip adds "Report". Four mental models collide on one page.
7. **Defer-blocked discoverability** — remediation endpoints exist (`job.service.ts:240-272`) but the page only shows a count inside the destructive-cleanup dialog (:1125-1133); no drill-down list of holders on-page.
8. **Drawer content gates** — Result hidden for `settled`/report rows (drawer html:164); Message hidden for all-work rows (`workToJob` :296); duration computed only from started+completed (:76-97).
9. **Responsive/mobile** — single 768px breakpoint (:573-603) reflows header/filter and makes the drawer full-width; the queue sidebar is never collapsed/hidden at narrow widths in queues view; no horizontal-scroll strategy for the chip filter row beyond wrap.
10. **A11y/keyboard** — exactly one `aria-label` on the whole page template (`html:19`); status chip listbox has a visible label but no programmatic one; icon-only semantics (spinning `sync`, `wifi`) covered only by `title`; no keyboard shortcuts; card actions are real buttons (good) but the expand state isn't communicated via `aria-expanded`.
11. **Degraded-state handling** — per-view error blocks with retry (`html:204-226`), `isEmptyWorkState` correctly excludes error (:322-327); but preflight failures are silently swallowed (:587-589) leaving a stale red-glow; WorkService failure is snackbar-only (:628-641).
12. **cancel/delete endpoint collision** — both `DELETE /api/jobs/{id}` (`job.service.ts:304`, `:342`); any future API change must disambiguate server-side; FE maintains divergent optimistic-update paths for the same wire call.
