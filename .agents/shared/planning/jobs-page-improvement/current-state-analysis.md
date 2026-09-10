# Jobs Page — Current-State Analysis (sections 1–2, part a)

Date: 2026-09-10
Author: worker (requirements analysis) for the jobs-page-improvement plan
Status: Draft — input to the phased-plan worker
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-jobs-page-plan` @ `feature/jobs-page-improvement` (base `e72558d1`), verified `git rev-parse` before writing.

Evidence base (do not re-research; this document cites, does not supersede):
- `research/current-state-findings.md` — FE ground-truth audit
- `research/alignment-patterns-findings.md` — header-panel design language
- `research/api-capabilities-findings.md` — BE contracts + gap table
- Worker spot-verifications are marked **[spot-verified]**; all other cites come verbatim from the three research files.

---

## 0. Positioning (binding for the plan)

The Jobs page and the header job-queue panel are **deliberately different surfaces**:

| | Header panel (`job-queue-panel` / `job-queue-indicator`) | Jobs page (`/jobs`) |
|---|---|---|
| Role | **Glanceable live status** — "what is working right now" | **Deep inspection + operations** — filter, drill in, act |
| IA | Instances-primary tree ("Live conversations") | Flat job list, queue sidebar, filter bar |
| Depth | Top-10 roots slice, receipt rows only | Full rows, drawer with timeline/metadata/DLQ, bulk ops (DLQ replay, System Cleanup) |

**The plan must not turn the page into a second glanceable panel.** Every redesign option is judged against this split: the page's reason to exist is the things the panel *cannot* do — exhaustive filtered search, per-job forensics, destructive and bulk operations, deep-linkable state.

---

## 1. What the page does today

### 1.1 Surface & shape
- Route `app.routes.ts:21` (`/jobs`, lazy `loadComponent`).
- Monolith container: `pages/jobs/jobs.component.ts` **1,252 LOC**, injecting 6 dependencies **including raw `HttpClient` for a single preflight call** (`:95`, `:560-590`); template 298 lines; styles 603 lines; spec 2,085 lines.
- Supporting components under `pages/jobs/`: `job-card`, `job-detail-drawer` (shared, also used standalone), `queue-list`, plus dialogs (system-cleanup-confirm, job-create, confirm, searchable-select, mission-liveness-chip).
- Services consumed: `JobService` (`/api/jobs` CRUD + DLQ + defer-holder actions), `WorkService` (`GET /api/work`, single read method), `JobSseService` (per-job EventSource), `ProjectService`, `ApiService.listAgents`, `QueueService` (via queue-list).

### 1.2 View modes (2)
- Toggle `Queues | All Work` (`html:15-28`); persisted in `localStorage['job-page-view-mode']` (`onViewModeChange :784-797` **[spot-verified]**), restored on load.
- **Queues view:** `JobService.listJobs(filters)` (`:528-543`) → `jobs` signal → client-side `filteredJobs` (`:230-249` **[spot-verified]**: re-filters status/source/agent/queue over `jobs()` only) → `displayedJobs` (`:262-267`) → full `@for` render of `<app-job-card>` (`html:261-272`).
- **All-work view:** `WorkService.getWork({project_id, status, root_only:false})` (`:614-642` **[spot-verified]** — only project+status forwarded; `root_only:false` intentional so child-instance rows stay visible) → lossy `workToJob` mapping (`:292-317` **[spot-verified]**) → `displayedJobs` returns `worksAsJobs()` **bypassing `filteredJobs` entirely**.
- Queue sidebar hidden in all-work; New Job button disabled in all-work (`html:88`).

### 1.3 Filters (7, with 3 dead in all-work)
Status chips (8 options incl. `Settled (receipt)`, `:349-358` **[spot-verified]**), Source select (`:361-367`), Agent select, Project searchable-select (persisted `localStorage['job-page-selected-project']`), Queue (sidebar, queues view only), Show-Deleted checkbox.
- **Dead in all-work:** source / agent / show-deleted controls render and accept input but have no effect — `loadWorks` forwards only project+status (`:614-624`), `filteredJobs` never touches `works()`, and `workToJob` nulls `source` (`:297`) so the agent filter could never match anyway.
- **No URL query-param binding anywhere** — filters are not deep-linkable; only project id + view mode survive a reload (localStorage). **[spot-verified: no `ActivatedRoute`/`queryParamMap` in `jobs.component.ts`]**.
- Two vocabularies coexist on `GET /api/jobs`: the page sends FE status names while `listActiveJobs` maps to BE-internal `status=queued,active` (`job.service.ts:108-128`).

### 1.4 Refresh & scale posture
- `setInterval` 30s (`:655-670` **[spot-verified: `:669`, and no `visibilitychange` anywhere in the file]**), mode-aware, skips only when a fetch is in flight. **No pause on hidden tab, no pause when drawer/dialog open.** Each tick wholesale-replaces both arrays.
- **Silent server cap on Queues view:** the FE sends **no `limit`** (`job.service.ts:85-106` **[spot-verified]**), so BE applies `DEFAULT_JOB_LIST_LIMIT=50` (clamp 1..100, `constants.py:17/20`; api findings §1). Result: at most the newest 50 rows, with **no total, no `has_more`, no truncation indicator**. Client-side filters therefore operate on an already-truncated page — a compound correctness hazard, not just a scale one.
- **All-work view is genuinely unbounded:** `GET /api/work` has no pagination at all (api findings §2).
- **Full-array render, no virtualization, no row cap** (`html:261-272`).
- Client-side-only mutations between polls: SSE patches (`:679-761`) and local delete/cancel updates (`:1006-1033`).

### 1.5 Cards & actions
- Card badges: priority (hidden for task-backed rows), status chip, kind chip, receipt chip (`message`), mission-liveness chip, paused, deleted (`job-card.component.html:1-56`); 100-char message preview falling back to `result_summary` (`job-card.component.ts:92-100`); expandable details (ids, full message, timestamps).
- Card actions: Cancel (pending/processing), Retry (failed/dead_letter), Delete (soft, with Undo snackbar → Restore), View (drawer). Cancel and soft-delete **share the identical `DELETE /api/jobs/{id}`** (`job.service.ts:304` vs `:342`) — server disambiguates by state; FE keeps two divergent optimistic-update paths.
- Page-only actions: New Job (`POST /api/jobs`), Retry-All-DLQ (gated on `dead_letter` status + project chosen, `html:114-134`), System Cleanup (two-stage destructive confirm, `POST /api/jobs/cleanup`).

### 1.6 Drawer + per-job SSE
- `onViewJobDetails` (`:1180-1193`) opens the mat-drawer and **subscribes SSE only if the job is non-terminal** (`!isTerminalStatus`); terminal jobs get no stream at all. `JobSseService` is a **single-job singleton** (`currentJobId`, `job-sse.service.ts:19`) — opening another row tears down the previous stream.
- Drawer sections: sticky action bar (Cancel/Retry/View Instance/Copy Job ID), Overview, Timeline (created/started/completed/cancelled/DLQ/duration), Message (`pre`), Result, Error, Dead Letter Info, Metadata (`job-detail-drawer.component.html`).
- **Content gates (bug-grade):**
  - Result section renders only when `status === 'completed'` (drawer html `:164` **[spot-verified]**): `settled` rows and all-work `report` rows **never show their result** even when `result_summary` is present (the card preview shows it; the drawer hides it).
  - Message never renders for all-work rows (`workToJob` sets `message: undefined`, `:296`).
  - **Timeline is broken in all-work view** [worker finding, spot-verified]: `workToJob` also hard-nulls `started_at`/`completed_at` (`:297-298`), so the drawer Timeline shows only `created_at` for every all-work row and duration is never computable.
- While a stream IS live, `updateJobFromSse` patches the whole list in place (both `jobs[]` and `works[]`), preserving array order.

### 1.7 Defer-blocked surface
- On this page, defer-blocked data appears **only inside the System-Cleanup dialog**: `refreshBadStateCount` (`:560-590`) calls `GET /api/jobs/cleanup/preflight` + `GET /api/queues/defer-blocked`, FE-composing `defer_blocked_count` with holder kinds (preflight does not emit them; documented `cleanup-preflight.model.ts:10-30`).
- The remediation endpoints — `POST /api/jobs/defer-holders/{id}/force-complete` and `.../resend-foreground` (`job.service.ts:240-272`) — **exist in the service but are wired nowhere on this page** (their consumer is the header indicator).
- Preflight failures are silently swallowed (`:587-589`), leaving a stale red-glow badge.

### 1.8 Tests
- Plain-TS logic-mirror convention (zero TestBed). `jobs.component.spec.ts` 2,085 lines, ~40 describe blocks **[spot-verified grep; research file says 42 — treat as ≈40]**. Notable pins: `root_only=false` contract, bad-state visibility + cleanup dialog copy, `updateJobFromSse` liveness propagation, project persistence scenarios 1–6, drawer nav URL pattern, DLQ retry-all.
- **Gaps:** no spec for `work.service.ts`, `system-cleanup-confirm-dialog` (only its model consts), `queue-list`, or `job-create-dialog`.

---

## 2. Pain-point inventory (grouped by theme; each maps to a phase-fixable hook)

Severity legend: 🔴 blocks core usefulness · 🟠 degrades trust/scale · 🟡 polish.

### Theme P1 — Information architecture & vocabulary mismatch with the instances-primary model
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P1.1 | 🔴 Page speaks "Jobs/Queues/All Work"; the shipped design language speaks "Live conversations" with instances-primary tree. Four mental models collide on one page (FE status names vs BE `queued,active`; `settled (receipt)` vs `completed`; `report` kind chip). | current-state §8.6; `job.service.ts:108-128`; alignment §6 | Vocabulary unification phase |
| P1.2 | 🔴 Users think in instances/conversations (user-locked V1 axiom); the page has **zero mission/instance grouping** — receipts and work rows interleave in one flat list, so "what is conversation X doing" is unanswerable. | alignment §1 (`instance-node.model.ts:1-26`); current-state §1 | Mission-context phase (grouping headers or lens) |
| P1.3 | 🟠 Job rows carry `mission_id` (== `instance_id`) + `mission_ref`, but the page never uses them for structure; the coalesced grouping key `mission_id ?? instance_id` already exists and is proven in the panel. | alignment §1 (`route()` `instance-node.model.ts:382-394`); api findings §4 | Reuse the coalesced key, do not invent one |
| P1.4 | 🟠 No human titles: rows show raw ids/agent ids; titles live only on `/api/missions`/`/api/instances` (gap d). Panel already ships an honest fallback chain (`job_metadata.instance_name → agent_id → first-8-chars`, `job-queue-panel.component.ts:508-517`). | api findings gap (d); alignment §2 | Reuse fallback chain; optional title enrichment |
| P1.5 | 🟡 Task vs receipt personalities (ADR-MISSION-01) are present as chips but not as structure; nothing separates "work done" (task) from "transport handled" (message/settled) at scan level. | api findings §7; alignment §6 | Grouping/lens phase |

### Theme P2 — Data-scale posture (unbounded fetch & render)
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P2.1 | 🔴 Queues view silently capped at newest 50 (FE sends no `limit`; BE default 50), no total/has_more/truncation notice; client-side filters run over the truncated page, so **filters can silently miss matching rows**. | current-state §3 + api findings §1; `job.service.ts:85-106` **[spot-verified]** | Explicit `limit=100` + full-window honesty banner; optional BE pagination ask |
| P2.2 | 🔴 All-work view truly unbounded (`GET /api/work` no pagination at all) — response grows with fleet history. | api findings §2 | Window policy or BE `limit` ask |
| P2.3 | 🟠 Full-array render, no virtualization, no row-count guard (`html:261-272`); every 30s tick replaces whole arrays. | current-state §3 | Virtualization / windowed render phase |
| P2.4 | 🟠 30s poll never pauses on hidden tab, drawer, or open dialog. | `:655-670` **[spot-verified no visibilitychange]** | Poll-discipline phase (visibility-gated tick) |
| P2.5 | 🟠 Lossy `workToJob` mapping discards message, priority, source, queue_id **and started_at/completed_at** — all-work rows are second-class citizens downstream (cards, drawer, timeline). | `:292-317` **[spot-verified]** | Row-parity fix in the all-work path |

### Theme P3 — Defer-blocked discoverability
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P3.1 | 🔴 Stuck defers are visible only as a count inside the destructive System-Cleanup dialog; no drill-down list of holders on-page. | current-state §6, §8.7 (`:1125-1133`) | Page-level defer banner + holders panel phase |
| P3.2 | 🟠 Remediation actions (`force-complete` / `resend-foreground`) exist in `JobService` but are unwired here — an operator must leave the page to act. | `job.service.ts:240-272` | Wire actions behind confirm (safety-reviewed) |
| P3.3 | 🟡 Preflight failures silently swallowed → stale red-glow (dishonest degraded state). | `:587-589` | Degraded-state discipline phase |

### Theme P4 — Live-update gaps
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P4.1 | 🔴 List freshness rests entirely on the 30s poll: SSE exists per-job only, only while the drawer is open **and** the job is non-terminal; terminal jobs never stream. | `:1184-1192`; `job-sse.service.ts:19` | Poll discipline now; list-SSE as a BE ask (gap e1) |
| P4.2 | 🟠 Single-job singleton stream — opening another row tears down the previous stream; no multi-row watching strategy. | `job-sse.service.ts:19,42-53` | Same phase as P4.1 |
| P4.3 | 🟡 Daemon-side terminal-receipt wake latency is a known sibling risk (~15min under pool saturation) — the page's poll will inherit it. | critical notes (emit_terminal watcher gap) | Note as external risk; do not design around |

### Theme P5 — Dead / no-op controls
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P5.1 | 🔴 All-work view: source / agent / show-deleted controls render, accept input, do nothing. Silently misleading. | `:614-624`; `filteredJobs` bypass **[spot-verified]** | Hide or wire (wiring needs BE: gap e5) |
| P5.2 | 🟡 `workToJob` nulls `source`, so the agent filter could never match in all-work even if wired client-side. | `:297` | Row-parity fix (P2.5) |
| P5.3 | 🟡 New Job disabled in all-work with no explanation. | `html:88` | Copy/UX pass |

### Theme P6 — Narrow / mobile
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P6.1 | 🟠 Single 768px breakpoint; queue sidebar never collapses in queues view; chip filter row has wrap-only strategy. | `jobs.component.scss:573` (also `:492` reduced-motion) | Responsive phase |

### Theme P7 — Accessibility
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P7.1 | 🔴 Exactly one `aria-label` on the whole page template; card expand state not `aria-expanded`; icon-only semantics covered by `title` only; no keyboard shortcuts. | `html:19`; current-state §8.10 | A11y phase — port the panel's WAI-ARIA patterns (alignment §7) |
| P7.2 | 🟡 Contrast: the panel already demonstrates the full pattern (role="tree", aria-level/expanded, chevron buttons, clamped arrow-key nav, Enter-preventDefault in menus). The page predates all of it. | alignment §2, §7 | Same phase; reuse, don't reinvent |

### Theme P8 — Maintainability of the monolith
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P8.1 | 🔴 1,252-LOC container owning filters + view modes + SSE patching + 3 dialogs + DLQ + cleanup + raw HttpClient preflight. | current-state §8.1 | Service-layer extraction phase |
| P8.2 | 🟠 Filter state not in URL (localStorage only) — nothing shareable, back-button hostile. | `:97-98` | URL-state phase |
| P8.3 | 🟠 No `mission.service.ts` exists; `/api/missions` consumed only as a badge count. The page would be the first rich missions consumer. | alignment §5 | Dedicated mission service in the plan |
| P8.4 | 🟡 cancel/delete share `DELETE /api/jobs/{id}` — any API evolution must disambiguate server-side; FE maintains divergent optimistic paths for one wire call. | `job.service.ts:304/:342` | Note as constraint; do not change BE |

### Theme P9 — Test gaps
| # | Pain | Evidence | Fix hook |
|---|------|----------|----------|
| P9.1 | 🟠 Missing suites: `work.service.ts`, system-cleanup-confirm-dialog, queue-list, job-create-dialog. | current-state §7 | Test-completion phase |
| P9.2 | 🟡 Plain-TS logic-mirror convention means template bindings are unverifiable by the spec suite — any redesign must pair with the template-extraction audit (diff-audit deleted/added `(click)` hunks). | FE conventions (Testing & QC) | Mandatory step in every phase touching templates |

---

## 3. What users cannot do today that the mental model demands

1. **See jobs grouped by conversation/mission** — the flat list interleaves receipts and work with no structural answer to "what is mission X doing".
2. **See a conversation's title** on the page — titles exist only on missions/instances endpoints (gap d); the page shows raw ids.
3. **Answer "is anything stuck?" from the page** — defer-blocked is a count buried in a destructive dialog; no holders list, no on-page remediation.
4. **Know whether the list is complete** — no total, no has_more, no truncation notice; the newest-50 cap is invisible.
5. **Reach older history** — no pagination UI of any kind; scrolling ends where the (silent) cap ends.
6. **Watch any row update live** — only the single drawer-open non-terminal row streams; everything else is 30s-stale.
7. **Trust the filter bar in all-work view** — three of its controls are no-ops.
8. **Share or bookmark a filtered view** — no URL state; reload loses everything but project + view mode.
9. **Deep-link to a specific job** — the drawer opens only from list state; no `/jobs/:id` route, no query-param handling.
10. **Distinguish work from receipts at scan level** — task vs message personalities exist only as small chips in an undifferentiated list.

---

## 4. Precisions vs the dispatch summary (for the downstream planner)

1. **"Unbounded listJobs" is half-right.** The FE sends no `limit`, but BE clamps to default 50 — queues view is *silently capped*, not unbounded. The all-work `/api/work` fetch is the truly unbounded one. Different fixes.
2. **"Per-job SSE only while drawer open"** — narrower still: drawer open **AND** selected job non-terminal; single-job singleton stream.
3. **"3 dead filters"** — confirmed exactly: source, agent, show-deleted in all-work. Status survives server-side; queue is structurally hidden there.
4. **`workToJob` also nulls `started_at`/`completed_at`** — the all-work drawer Timeline is broken, beyond the Message/Result gates already documented.
5. **7 filters total**, not the summary's implied 5 (queue + show-deleted are missed).
6. **"Settled + failed combo drop"** — external parallel arc `fix/jobs-status-combo-filter` (base e72558d1). **Dependency, not design input**: phases that ship settled-involving multi-status filters must note the version gate, and the plan must not "fix" it FE-side.

---

## 5. Binding constraints for the downstream plan

- **ADR-MISSION-01 vocabulary:** jobs are tickets with two personalities — `task` = work (its status is the lifecycle answer), `message` = receipt (status is the delivery answer; `mission_liveness` carries the parent answer). Users think in instances/conversations. **Never call mission rows "settled"** — mission-side prose must avoid the word (M3 rule, `job.model.ts:289-297`); `settled` = teal + `receipt_long`, distinct from `completed` green + `check_circle`.
- **Merge-order rule** is a chat-transcript rule (never re-sort by `created_at`); it does not forbid the jobs page's server-ordered `created_at DESC` list, but SSE patches must keep patching in place (today's behavior) rather than re-sorting client-side.
- **Plain-TS logic specs (no TestBed):** new logic must live in pure models/services to stay testable; every template-touching phase carries the template-extraction audit note.
- **Positioning split (§0):** page = deep inspection + operations; it must not become a second glanceable panel.
- **FE verify gate:** `npx tsc --noEmit -p tsconfig.app.json` + targeted jest + `npm run build` (10 pre-existing warnings known).
