# Metrics FE Charts page — the operator-facing metrics surface

> **Status:** published by the `central-metrics-v1` lane (Phase 6, task 6.4).
> **Date:** 2026-11-02 (UTC).
> **Lane:** `feature/central-metrics-v1` (worktree `/home/nea/ensemble-src-wt-central-metrics-v1`).
> **Backend contract:** [`docs/architecture/metrics-query-api.md`](metrics-query-api.md) (the door)
> and [`docs/architecture/metrics-query-contract.md`](metrics-query-contract.md) (the frozen
> contract).
> **Counts and vocabularies:** `.agents/shared/planning/metrics-collection/metric-catalog.md` §14
> is the single source of truth — this page **points** at it, never restates it.

The Metrics page is the developer-facing view over the central-metrics query door. This page
describes what it renders, how it polls, and the states it distinguishes.

---

## 1. Where it lives

| Thing | Value |
|---|---|
| Route | `/metrics` — a **lazy** `loadComponent` route (`frontend/src/app/app.routes.ts`), placed **before** any `:id` route |
| Gear-menu entry | `Metrics` (`query_stats` icon) in the static `SettingsMenuItem[]` array (`frontend/src/app/app.ts`) — appended **after** `MCP Gateway` and **before** the conditional `Database`/`Maintenance` appends, so those appends still land at the menu tail (the resources ordering pin and the gateway ordering pin must stay green). The entry is **static** — the query door is never switch-gated, so it is always reachable |
| Shell | `frontend/src/app/pages/metrics/metrics.component.ts` (the section-registry pattern, modelled on `resources.component.ts`) |
| Bodies | three **lazy** tab bodies — `overview/`, `charts/`, `families/` |
| Service | `frontend/src/app/services/metrics.service.ts` — the single FE↔BE surface truth |
| Model mirror | `frontend/src/app/models/metrics.model.ts` — the typed mirror of the envelope |
| Chart primitive | `frontend/src/app/components/charts/svg-line-chart/svg-line-chart.component.ts` — the **shared** SVG primitive (it lives under `components/`, not under the page) |

---

## 2. The three tabs

| Tab | Route it polls | Poll interval | What it renders |
|---|---|---|---|
| **Overview** | `GET /api/metrics/snapshot` | **10 s** | **stat cards** — the current value per live series, with the family name and the series name |
| **Charts** | `GET /api/metrics/series` | **15 s** | the **line/step** charts of the **chart-primary roster** (`role === 'chart'` families) |
| **Families** | `GET /api/metrics/families` | on demand | the **family catalog as served** — the names, the label vocabularies, the bucket boundaries, the availability, the cardinality contribution |

The service's URL constants are the single FE↔BE surface truth:

```
static readonly FAMILIES_URL = '/api/metrics/families';
static readonly SNAPSHOT_URL = '/api/metrics/snapshot';
static readonly SERIES_URL   = '/api/metrics/series';
static readonly JSON_HEADERS: { readonly Accept: string } = { Accept: 'application/json' };
```

`JSON_HEADERS` is not decoration: the auth middleware **302-redirects** a wildcard-`Accept`
browser, so the FE must ask for `application/json` explicitly or it will be redirected away from
the API.

The **families** catalog is fetched once per page load, not polled — it changes only when the
daemon restarts or a family is wired.

---

## 3. The chart mode

A series is drawn as a **line** or a **step**. The mode comes from **two** places, in this
order:

1. **The catalog's per-name `chart_role`** — the door's per-name metadata member
   (`chart_role ∈ {line, step}`), carried at registration time. It is the **chart-primary
   roster's** declaration of intent.
2. **The door's kind-derived fallback** — `metricsChartRoleForKind(kind)`: a **counter** renders
   **step**, anything else renders **line**. (`frontend/src/app/models/metrics.model.ts`)

The fallback is what makes a family chartable **without a door edit**: a newly wired family with
no explicit `chart_role` still renders correctly. The step rendering is not cosmetic — a
counter that is read at a point in time is a staircase, and drawing it as a straight line would
imply values that were never observed.

---

## 4. Polling discipline

The polling rules are pinned by the shipped bindings pack — removing a constant or the teardown
turns that pack red:

- **Named constants.** Every polling tab body declares a named
  `readonly POLL_INTERVAL_MS` (**10 000** for Overview, **15 000** for Charts). The value is a
  named constant, never an inline literal.
- **Teardown on unmount.** Polling stops when the tab body is destroyed.
- **Tab-hide gating.** A hidden tab stops polling; it does not keep asking.
- **Skip-if-in-flight.** A poll that is still in flight is **skipped**, not queued — a slow
  response must never build a backlog.
- **Monotonic supersede tokens.** A slower earlier response **cannot** overwrite a newer one.
- **One error banner + a retry CTA that retains the last-good data.** A failed poll does **not**
  blank the page; the last-good view stays and is marked.

---

## 5. The window the Charts tab asks for

The Charts tab requests `window_s = 3600` (60 minutes). Against the door's
`max_points` default of **240**, that yields **240 points at the door's 15 s observed effective
spacing** — i.e. **15 s effective resolution out of the box**, with no client-side downsampling.

This is why the door's `max_points` **default** is 240 and not lower: 60 min × 4/min at the 15 s
effective spacing *is* 240, so the default and the acceptance criteria agree by construction.

---

## 6. The states the page distinguishes

The page is **availability-driven**. It never infers "disabled" from an empty series set alone,
and it never shows a "disabled" banner — the door is not switch-gated, so an empty body does
not mean "disabled".

| State | What the page shows |
|---|---|
| **At least one family live** | the normal view — stat cards and/or charts |
| **No family live** | the **empty panel**: *"No metrics available yet"* (`METRICS_EMPTY_PANEL_COPY`) — an availability-driven condition, **never** an error banner and **never** a "disabled" banner |
| **A family is dark** | the family renders **dark with its reason** (from the envelope's per-family `availability` block) — the reason is the census being gated, the census not running, the capture being off, or the series simply being absent so far |
| **The door is unreachable / the session expired** | the page **degrades to its cached, last-good view, marked stale** — it does not poll harder and it does not blank |
| **The catalog load failed** | a **failed load is not an empty state** — it is an error with a retry CTA that retains the last-good data |

**The copy rule:** the empty panel says *"No metrics available yet"* and **never** says
"metrics disabled". A reader who saw "disabled" would conclude the operator had switched
something off; in fact the door simply has no observations yet.

---

## 7. The chart primitive

The charts are drawn by a **hand-rolled SVG primitive**
(`frontend/src/app/components/charts/svg-line-chart/` — the **shared** primitive, it lives under
`components/` and not under the page) — the page adds **no new frontend dependency**.

The primitive is **feature-frozen**: it renders **lines, steps, areas, stat cards and tooltips**
and nothing else. **Zoom, brush, crosshair and gradients are explicitly OUT** — each is a
feature with its own scale/axis/test surface, and admitting one is a deliberate decision, not an
increment.

The primitive exposes **pure** scale and axis functions (unit-assertable, the house
`svg-timeline` pattern), normalizes per-series min/max, renders native `<title>` tooltips, and
labels the axes in **UTC**.

---

## 8. What the page does not do

- It does **not** poll the exposition listener (`:9798`) — it polls the **query door** only.
- It does **not** add a charting dependency, a date library, or a state library.
- It does **not** create any server-side state, and it does not change any managed key.
- It does **not** read the kill-switch: the door is never switch-gated, so the page's states are
  driven by **availability**, not by the switch.
