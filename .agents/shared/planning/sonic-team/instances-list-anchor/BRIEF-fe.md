# SONIC-MODULE-BRIEF — instances-anchor-fe

Commission: instances-list-anchor — the left-side instance list must always contain the
directly-linked (selected) instance and its children. Direct link
`/projects/:projectId/instances/:instanceId` (the routed component is an inert stub; the App root
mounts the chat component as the detail view, with the instance list as its left sidebar —
chat.html:8 renders `<app-instance-list>`).

## TASK
Thread the route-selected instance id (the "anchor") into EVERY instance-list request the FE
issues while that instance stays selected, so the list always contains the selection + subtree.
The BE module (merge slot 1) provides the param; you code against the FROZEN CONTRACT below.

## FROZEN BE CONTRACT (module instances-anchor-be, slot 1)
`GET /api/instances` gains optional query param `anchor` (uuid string). When present + resolvable,
the response's `instances` array ADDITIONALLY contains the anchor row + all its descendants
(appended after the normal rows, deduped, filters bypassed). Unknown anchor degrades silently.
Envelope otherwise unchanged (`total`/`limit`/`offset`/`has_more`/`truncated` — normal page
semantics). Absent param => byte-compatible back-compat.

## FILES (exact — touch nothing else)
- `frontend/src/app/services/api.service.ts` — `listInstances(...)` gains a trailing optional
  `anchor?: string`; set the `anchor` query param ONLY when truthy.
- `frontend/src/app/services/api.service.spec.ts` — pin the URL with/without anchor.
- `frontend/src/app/services/instance.service.ts` — inject `InstancesViewStateService`; in
  `loadInstances()` read the anchor at fetch time and pass it to `api.listInstances`.
- `frontend/src/app/services/instance.service.spec.ts` — pin the anchor threading.
- `frontend/src/app/services/instances-view-state.service.ts` — expected NO edit (read-only anchor
  source: `activeInstanceId` + `detailVisible` signals). Touch ONLY if strictly required.

## DESIGN (pinned)
- Anchor value: `detailVisible() ? (activeInstanceId() ?? null) : null` — evaluated INSIDE
  `loadInstances()` at fetch time. This mirrors the EXISTING `searchQuery()` threading precedent
  (instance.service.ts ~:96-101: "read here inside loadInstances() so the active filter is
  automatically threaded into every fetch (initial load, pagination, and the 60s polling tick)").
  Initial load, pagination (`loadMore`) and the 60s poll tick all inherit it automatically.
  Do NOT add an anchor param to `startPolling`.
- `InstancesViewStateService.activeInstanceId` is written ONLY by the App root's
  `syncDetailVisibility` (route regex, app.ts ~:690) — you add NO writers. Cold deep-link ordering
  is already safe: the App constructor runs syncDetailVisibility before child effects; the chat
  page's `tabEffect` (chat.component.ts ~:307) startPolling immediate load re-fetches with the
  anchor on show; in-app row clicks select an instance that is already in the list.
- `ApiService.listInstances` is called positionally today — add `anchor?: string` as the LAST
  positional param; set the param only when truthy (absent => URL byte-identical to today).
- The anchor rows render through the EXISTING list template + tree builder (parent_id grouping,
  instance-list.component.ts ~:142-162) — NO component/template change. The anchor nests
  automatically.
- mergeInstances' local-only preservation + the append dedupe (existingIds filter) already handle
  the anchor family rows on subsequent polls — no new merge logic.

## FENCES (hard)
- ZERO new npm deps. Angular 21 standalone conventions: services own WritableSignal; components
  read via getters/effect(). NO component changes — pages/chat/**, pages/instances/**, app.ts,
  app.routes.ts, components/instance-list/** are ALL off-limits. The gc-fe joined-rooms button in
  the list rows must NOT regress (its spec:
  `frontend/src/app/components/instance-list/instance-list.rooms-btn.spec.ts`).
- Do NOT add the anchor to ANY other API call path — the 8s badge poll / listInstanceTree stays
  anchor-free (never point a high-frequency poller at an expanded response).
- No regression to: sort/filter/pagination, the 60s poll + visibility gate + refocus debounce
  (REFOCUS_DEBOUNCE_MS), the SSE optimistic-update merge paths.
- Time-based fixtures rot — use relative dates in specs.

## STORED EXPERIENCE (distilled — you cannot query the knowledge base)
- `InstanceService` poll cadence: 60s; a hidden tab stops ISSUING polls; refocus = ONE debounced
  catch-up. Don't disturb this machinery.
- The list request from this service: PAGE_SIZE=10, include_descendants=true (back-compat) — keep.
- Jest ^30.3.0 via `npm test` (frontend/jest.config.js, testMatch **/*.spec.ts; zoneless app —
  constructor effect() flushes on detectChanges(), not bare tick()).
- api.service.spec.ts PINS list-URLs param-by-param today — extend those pins, don't loosen them.

## TESTS
api.service.spec.ts: URL contains `anchor=<id>` when passed; omits the key entirely when not
passed (byte-compat).
instance.service.spec.ts: loadInstances passes the anchor while `detailVisible` +
`activeInstanceId` are set; drops it when detailVisible=false; the poll tick keeps passing it
(mock ApiService, assert call args).
REGRESSION (must re-run, must stay green): instance.service.spec.ts, api.service.spec.ts,
instances-view-state.service.spec.ts, instance-list.component.spec.ts,
instance-list.rooms-btn.spec.ts.
Command (fresh worktree has no node_modules — `npm ci` first, once):
cd /home/nea/ensemble-src-wt-instances-anchor-fe/frontend && npm ci && npm test -- --runTestsByPath src/app/services/api.service.spec.ts src/app/services/instance.service.spec.ts src/app/services/instances-view-state.service.spec.ts src/app/components/instance-list/instance-list.component.spec.ts src/app/components/instance-list/instance-list.rooms-btn.spec.ts

## WORKTREE
/home/nea/ensemble-src-wt-instances-anchor-fe (absolute path)
branch `feature/instances-anchor-fe`, base `bcebe77250ab6ae07212cfafc33b2a8e82608d64` (origin/latest)

## MERGE ORDER
Slot 2 — merges AFTER module `instances-anchor-be`.

## GIT DISCIPLINE
Commit ONLY the files above, pathspec-limited (NEVER `git add -A`, NEVER `git stash`, NEVER push,
NEVER merge). Leave the worktree CLEAN at report time.

## REPORT
Reply to sonic-developer with [sonic-module-report-v2], <=40 lines:
MODULE-REPORT: DONE|PARTIAL|FAILED
branch + tip sha / files touched / what changed / tests run (exact commands + counts) / what is
UNTESTED / known risks / bash-raises used / complexity self-assessment.
