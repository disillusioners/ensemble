# FE Polling Interval Survey — `frontend/src`

**Commission:** `fe-home-poll-120s` (module `sonic/instances-optd-fe-poll-fe`)
**Tree:** `/home/nea/ensemble-src-wt-optd-fe-poll-fe` — branch `sonic/instances-optd-fe-poll-fe`, base `43e8d8db6`
**Scope:** `frontend/src` ONLY. No `daemon/` change, no runtime/visual change beyond the single literal below.
**Date:** 2026-11-19

---

## 1. The change this module lands

| Surface | Was | Now | Site |
|---|---|---|---|
| Home page — instances list | `10000` ms (10 s) | **`120000` ms (120 s)** | `frontend/src/app/pages/home/home.component.ts:110` (literal inside `setInterval` at `:108`) |

Diff = ONE numeric literal. The `setInterval` / `clearInterval` pair, the
`ngOnDestroy` → `stopPolling()` teardown, and `loadInstances()` are untouched.
No supersede-token / tab-hide logic was added or removed.

---

## 2. Survey method

Greps run over `frontend/src` (all `*.ts`, `*.html` cross-checked where a
template can bind a timer):

```
grep -rn "setInterval"   frontend/src --include=*.ts
grep -rn "timer("        frontend/src --include=*.ts   # rxjs
grep -rn "\binterval("   frontend/src --include=*.ts   # rxjs  -> ZERO hits
grep -rn "setTimeout"    frontend/src --include=*.ts
```

Zero hits for rxjs `interval(`. No aliased/renamed `interval` import exists
(the only `rxjs` `timer` importers are `checkpoint-cleanup.service.ts`,
`mcps-gateway.component.ts`, `blueprint.component.ts`, `image-upload.service.ts`).
No **recursive** `setTimeout` poll exists: every `setTimeout` site in
`frontend/src` is a one-shot debounce, auto-clear, focus-restore, render tick,
or an SSE reconnect backoff (see §5).

`*.spec.ts` files are excluded from the counting but inspected for interval
pins (§6).

---

## 3. Full polling-interval table

16 polling surfaces. Every interval value below was read from the ACTUAL
constant / literal on this tree — not from the brief's seed.

| # | Surface | Interval | file:line (timer site / const site) | Pattern | Driven API call(s) |
|---|---|---|---|---|---|
| 1 | **Home — instances list** | **120 000 ms (120 s)** *(was 10 000)* | `app/pages/home/home.component.ts:108` / literal `:110` | `setInterval` → `loadInstances()`; teardown `ngOnDestroy` → `stopPolling()` → `clearInterval`; **NO** visibilitychange gate | `GET /api/instances` (`ApiService.listInstances()`) |
| 2 | Job-queue badge indicator (header) | 8 000 ms | `app/components/job-queue-indicator/job-queue-indicator.component.ts:577` / const `:130` | plain `setInterval`; `ngOnDestroy` → `clearInterval`; no visibility gate; 5-leg `forkJoin`, per-leg `catchError` retains last-good | `GET /api/jobs` (active + recent), `GET /api/missions?liveness=processing,pending,paused&limit=20`, `GET /api/instances` (root-paginated, `include_descendants=false`), `GET /api/queues/defer-blocked` |
| 3 | Jobs page — auto-refresh | 30 000 ms | `app/pages/jobs/jobs.component.ts:1702` / const `app/pages/jobs/jobs-poll.model.ts:65` | `setInterval` + **visibilitychange gate** — pure `shouldTick()` (tabVisible ∧ ¬drawerOpen ∧ ¬modalOpen ∧ ¬fetchInFlight); refocus → immediate refresh, debounced 2 000 ms; `ngOnDestroy` → `clearInterval` + listener removal | `GET /api/jobs` or `/api/works` (`store.refreshActive()`, view-mode leg), `GET /api/queues/defer-blocked` (`store.fetchDeferBlocked()`) |
| 4 | Metrics — charts tab | 15 000 ms | `app/pages/metrics/charts/charts-tab.component.ts:262` / const `:155` | `setInterval`; **visibilitychange gate** (`browserVisible`); skip-if-in-flight; `ngOnDestroy` → `clearInterval` | `GET /api/metrics/series?names=…&window=…` |
| 5 | Metrics — overview tab | 10 000 ms | `app/pages/metrics/overview/overview-tab.component.ts:230` / const `:157` | `setInterval`; **visibilitychange gate**; skip-if-in-flight; `ngOnDestroy` → `clearInterval` | `GET /api/metrics/snapshot` |
| 6 | Metrics — resources live tab | 8 000 ms | `app/pages/metrics/resource/live/live-tab.component.ts:86` / const `:57` | `setInterval`; skip-if-in-flight (`fetchInFlight` signal); **no** visibility gate; `ngOnDestroy` → `clearInterval` | `GET /api/resources/live`, `GET /api/resources/self-cost` |
| 7 | Metrics — nest-reports tab | 15 000 ms | `app/pages/metrics/tabs/nest-reports-tab.component.ts:507` / const `:409` | `setInterval` + **visibilitychange gate**; skip-if-in-flight; supersede token (`listSeq`); `ngOnDestroy` → `clearInterval` + listener removal | `GET /api/nest-reports/clusters?window=…` |
| 8 | Settings page — editor / VS Code status | 2 000 ms | `app/pages/settings/settings.component.ts:486` / const `:55` | `setInterval`; `clearInterval` teardown; no visibility gate; armed only while the status request is outstanding | `GET /api/settings/editor/status` |
| 9 | Command-state service | 5 000 ms | `app/services/command-state.service.ts:498` / const `:88` | `setInterval` armed/disarmed **conditionally** (only while an instance's command is tracked); `clearInterval` teardown; no visibility gate | `GET /api/instances/{instance_id}/commands/active` (via the `wireFetch` seam → `ApiService.getActiveCommand()`) |
| 10 | Instance service — instance list paging | 60 000 ms | `app/services/instance.service.ts:418` / const `:31` | `setInterval`; `stopPolling()` → `clearInterval`; no visibility gate | `GET /api/instances?limit=…&offset=…&include_descendants=…` |
| 11 | Migration service — status poll | 2 000 ms *(inline literal — no named const)* | `app/services/migration.service.ts:377` | `setInterval`; `clearInterval` teardown; no visibility gate; armed only while a migration is running | `GET /api/migration/status` |
| 12 | Room service — open-room messages | 10 000 ms | `app/services/room.service.ts:374` / const `:63` | `setInterval` + **visibilitychange gate** (`document.hidden` → `browserVisible`); skip-if-in-flight; skip-if-no-open-room; `clearInterval` + listener removal | `GET /api/rooms/{room_id}/messages` |
| 13 | MCP gateway page | 10 000 ms | `app/pages/mcps-gateway/mcps-gateway.component.ts:84` / const `app/pages/mcps-gateway/mcps-gateway.service.ts:84` | rxjs `timer(delay, period)` + `takeUntilDestroyed(destroyRef)`; no visibility gate; skip-if-in-flight inside `refreshAll()` | `GET /api/mcps-gateway/machines`, `GET /api/mcps-gateway/sessions`, `GET /api/mcps-gateway/audit?limit=&offset=` |
| 14 | Blueprint page — single-blueprint rebuild poll | 10 000 ms | `app/pages/blueprint/blueprint.component.ts:767` | rxjs `timer(0, 10_000)` + `take(30)` (5 min cap) + `switchMap`; subscription stored, `unsubscribe()`d on restart/destroy; no visibility gate | `GET /api/projects/{project_id}/blueprints?…` (`quiet=true`) |
| 15 | Blueprint page — rebuild / update poll | 10 000 ms | `app/pages/blueprint/blueprint.component.ts:806` | rxjs `timer(0, 10_000)` + `take(30)` + `switchMap`; same teardown discipline; no visibility gate | `GET /api/projects/{project_id}/blueprints?…` (`quiet=true`) |
| 16 | Checkpoint-cleanup service — run poll | ACTIVE **2 000 ms** → BACKOFF ramp 2 s step, ceiling **30 000 ms** | `app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.service.ts:351` (ACTIVE `timer(0, intervalMs)`), `:386` (BACKOFF seed `timer(intervalMs)`), `:398` (`expand` → `timer(computeBackoffMs(tickIdx, intervalMs))`) — consts `:59` / `:104` / `:111` | rxjs `timer` chain: `takeWhile(terminal, inclusive)` + `takeUntil(timer(budgetMs))`, then `concat(ACTIVE, BACKOFF)`; budget floor 15 min / ceiling 60 min (`:78` / `:92`); caller owns teardown (`takeUntilDestroyed` / `unsubscribe`); no visibility gate | `GET /api/maintenance/checkpoint-cleanup/runs/{run_id}` |

### Cadence ranking (fastest → slowest)

```
  2 s   settings status (#8) · migration status (#11) · checkpoint-cleanup ACTIVE (#16)
  5 s   command-state (#9)
  8 s   job-queue badge (#2) · resources live (#6)
 10 s   metrics overview (#5) · room service (#12) · MCP gateway (#13) · blueprint ×2 (#14,#15)
 15 s   metrics charts (#4) · nest-reports (#7)
 30 s   jobs page (#3)
 60 s   instance service (#10)
120 s   home instances list (#1)  ← THIS MODULE
```

### Visibility-gate census

- **Gated** (a `visibilitychange` listener suppresses ticks on a hidden tab): #3 jobs, #4 charts, #5 overview, #7 nest-reports, #12 room.
- **NOT gated** (keep ticking on a hidden tab): #1 home *(this module — unchanged gate posture)*, #2 job-queue badge, #6 resources live, #8 settings, #9 command-state, #10 instance service, #11 migration, #13 MCP gateway, #14/#15 blueprint, #16 checkpoint-cleanup.

---

## 4. Seed verification (brief §2)

Every seed entry was re-derived on this tree. **All 15 seed entries VERIFIED** —
line numbers in the brief refer to the *constant* site for some entries and the
*timer* site for others; both are given in §3.

| Seed entry | Verdict | Constant value found |
|---|---|---|
| home 10 s (`:108`) | VERIFIED — timer at `:108`, literal at `:110` → now `120000` | 10 000 → **120 000** |
| job-queue badge 8 s | VERIFIED — const `:130`, timer `:577` | 8 000 |
| jobs page 30 s + visibilitychange gate | VERIFIED — const `jobs-poll.model.ts:65`, timer `jobs.component.ts:1702` | 30 000 |
| metrics charts 15 s | VERIFIED — const `:155`, timer `:262` | 15 000 |
| metrics overview 10 s | VERIFIED — const `:157`, timer `:230` | 10 000 |
| resources live 8 s | VERIFIED — const `:57`, timer `:86` | 8 000 |
| nest-reports 15 s | VERIFIED — const `:409`, timer `:507` | 15 000 |
| settings status 2 s | VERIFIED — const `:55`, timer `:486` | 2 000 |
| command-state 5 s | VERIFIED — const `:88`, timer `:498` | 5 000 |
| `services/instance.service.ts:418` | VERIFIED — timer `:418`, const `POLLING_INTERVAL = 60_000` `:31` | 60 000 |
| `services/migration.service.ts:377` | VERIFIED — timer `:377`, inline literal `2000` | 2 000 |
| room service 10 s | VERIFIED — const `:63`, timer `:374` | 10 000 |
| blueprint `timer(0, 10_000)` ×2 | VERIFIED — `:767` and `:806` | 10 000 |
| checkpoint-cleanup timer phases | VERIFIED — `:351` / `:386` / `:398` | 2 000 → 30 000 ceiling |
| mcps-gateway | VERIFIED — timer `:84` (component), const `mcps-gateway.service.ts:84` | 10 000 |

**Completions beyond the seed:** none. The seed covered every recurring poller;
the sweep found no additional polling surface in `frontend/src`. Two
non-poll `rxjs timer` sites exist and are excluded (§5): the image-upload
retry backoff (250 ms) and the checkpoint-cleanup budget timer (a deadline,
not a cadence).

---

## 5. Excluded — one-shot timers / debounces / reconnect backoffs (NOT pollers)

| Site | Value | Why it is not a poll |
|---|---|---|
| `app/services/image-upload.service.ts:222` | `timer(ImageUploadService.RETRY_BACKOFF_MS)` = 250 ms | one-shot rxjs retry backoff per attempt |
| `app/components/vscode-viewer/vscode-viewer.component.ts:145` | 300 ms | debounce on workdir change (effect-driven re-arm, not recursive) |
| `app/services/job-sse.service.ts:161` | `INITIAL_RECONNECT_DELAY(1000) * 2^(n-1)`, capped `MAX_RECONNECT_DELAY=30000`, max 5 attempts | SSE reconnect backoff — event-driven, re-arms only on stream error |
| `app/services/notification.service.ts:171` | `1000 * 2^attempts`, capped 30 000 ms | SSE reconnect backoff |
| `app/pages/jobs/jobs.component.ts:1742` | 2 000 ms | one-shot refocus debounce (feeds the same 30 s gate) |
| `app/pages/jobs/jobs.component.ts:2229` | render tick | one-shot CDK render macrotask |
| `app/pages/snapshots/snapshots.component.ts:797` | — | search debounce |
| `app/components/instance-list/instance-list.component.ts:209` | — | search debounce |
| `app/components/message-input/message-input.component.ts:670,689` | 4 000 ms | validation-error auto-clear |
| `app/components/notification-bell/notification-bell.component.ts:64` | — | animation timeout |
| `app/components/mermaid-fullscreen-dialog/mermaid-fullscreen-dialog.component.ts:113` | 2 200 ms | status-message auto-clear |
| `app/components/chat-interface/chat-interface.component.ts:280,286,1478` | — | scroll / measurement timers |
| `app/components/agent-switcher/agent-switcher.component.ts:159,164,226,243` | 0 ms | focus restore |
| `app/components/migration/migration.component.ts:106` | — | one-shot |
| `app/services/command-state.service.ts:468` | `TERMINAL_DISPLAY_MS` | per-command eviction timer |

Event-driven (SSE) surfaces are NOT polling and are out of the survey's
method: `sse`, `notification`, `job-sse`, `workspace`, `migration` streams.

---

## 6. Spec pins on intervals

| Spec | Pin | Effect of THIS change |
|---|---|---|
| `app/pages/home/home.component.spec.ts` | **NO interval pin.** The spec is a MIRRORED `TestableHomeComponent` copy — it does not import the production component and never touches the poll. | **none** — no home spec asserts on `10000` / `120000` |
| `app/pages/jobs/jobs-poll.model.spec.ts:86,103` | `POLL_INTERVAL_MS = 30_000` + source-text match on `jobs-poll.model.ts` | none (untouched surface) |
| `app/pages/metrics/metrics.bindings.pins.spec.ts:65-66` | overview `POLL_INTERVAL_MS = 10_000`, charts `= 15_000` | none (untouched surfaces) |
| `app/pages/metrics/overview/overview-tab.component.spec.ts:89` | `POLL_INTERVAL_MS` is `10_000` | none |
| `app/pages/metrics/charts/charts-tab.component.spec.ts:119` | `POLL_INTERVAL_MS` is `15_000` | none |
| `app/pages/metrics/resource/live/live-tab.component.spec.ts:109` | `POLL_INTERVAL_MS` is `8000` | none |
| `app/pages/mcps-gateway/mcps-gateway.service.spec.ts:659` + `mcps-gateway.component.spec.ts:418` | `POLL_INTERVAL_MS` is `10000` | none |
| `app/services/room.service.spec.ts:410-411` | `POLL_INTERVAL_MS` is `10_000` | none |
| `app/services/instance.service.spec.ts:1139-1141` | `POLLING_INTERVAL` is `60000` | none |
| `app/pages/maintenance/maintenance.bindings.pins.spec.ts:328-343` | checkpoint-cleanup `POLL_INTERVAL_MS = 2000` static + default-arg source pins | none |
| `app/pages/settings/settings.component.spec.ts:52,945` | `STATUS_POLL_INTERVAL_MS = 2000` (local constant in the spec) | none |
| `app/pages/jobs/jobs-page.bindings.pins.spec.ts:811` | source-text match on the jobs tick body | none |
| job-queue indicator / nest-reports / command-state | **no interval pin found** | none |

**Net:** no spec pins the home poll interval, so `10000 → 120000` breaks no
existing test.

---

## 7. Risk notes for the consumer of this survey

1. **Home is now the slowest poller in the app (120 s).** The home instances
   list will visibly lag by up to 2 minutes. There is **no** visibility gate and
   **no** supersede token on this surface, so a hidden tab also stops
   refreshing for the full 2 minutes after it is shown again.
2. **Two independent instance-list pollers exist** — `instance.service.ts`
   (#10, 60 s) and `home.component.ts` (#1, now 120 s). They hit the same
   `GET /api/instances` endpoint with different parameters. If the goal of the
   120 s change was to halve instance-list traffic, #10 still runs at 60 s and
   will dominate.
3. **The badge indicator (#2, 8 s) is the heaviest poller** — 5 legs per tick
   including a missions list and an instance tree. It is also ungated.
4. Six surfaces have **no visibility gate** at all; on a hidden tab they keep
   consuming the backend. A follow-up pass could gate them the way #3/#4/#5/#7/#12
   already do.
5. `migration.service.ts:377` uses an **inline literal** `2000` rather than a
   named constant — the only poller in the app that does, which makes it
   invisible to constant-based greps.
