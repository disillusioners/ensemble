# Phase 5: Tab 1 — Resources on the custom slot

## Objective

Mount the resource module as **tab 1** of the consolidated page, over its
**6 existing support endpoints**, with the panel's own range control driving
its window — and with the anomalies facts finally rendered instead of hovering
invisible.

This is the only tab that renders data on the live install today (the
`/api/resources/*` door is fail-open while the central door is fail-closed),
which makes it the lane's most valuable tab and its most scrutinized one —
**and the reason the state model is per-source (AM-1): under the default
install posture, tab 1 MUST stay lit while the central tabs carry the
actionable state.**

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 5.1 | Relocate the resource module from `frontend/src/app/pages/resources/` to `frontend/src/app/pages/metrics/resource/` (`live/`, `timeline/`, plus the new anomalies panel). D-3 retires the route, and a directory named after a retired route is a lie the next reader will trust (IMPL-02). **The ordering interacts with Phase 4's engine deletion (AM-27):** Phase 4's 4.12 may delete `pages/resources/svg-timeline/` only **after** this `git mv`; the lane states which ordering it took and keeps every commit green | Phase 2, Phase 4 | The move is a `git mv` (history preserved); no import path outside the module changed shape; the specs move with the code |
| 5.2 | Rebuild tab 1's body as a **flat column** (IMPL-03): **Live stat strip → Timeline panel → anomalies panel**. The existing nested `mat-tab-group` shell (`frontend/src/app/pages/resources/resources.component.ts:47-51`) is retired — nested tab groups inside a tab are a collision surface (the audit already found a 900 px banner/button collision) | 5.1, Phase 2 | The tab-1 spec asserts the three sections render in order; there is no nested tab group in the tab body |
| 5.3 | Re-point the Live strip: it keeps its own **8 s poll** cadence (a different question, a different cadence) and its `self-cost` chip, and it consumes `/api/resources/live` + `/api/resources/self-cost` | 5.2 | The existing live-tab specs stay green; the poll cadence is unchanged (8 s) |
| 5.4 | Re-point the Timeline panel: it consumes `/api/resources/history` with the **panel's own** range control (15m/1h/24h/7d — its own ladder, `frontend/src/app/pages/resources/timeline/timeline-tab.component.ts:60`, `frontend/src/app/models/resources.models.ts:16`), and the panel's **own window-picker UI is removed** — the frame's central-sourced control does **not** govern it (AM-1/AM-19). **The control's scope is per-panel (AM-19):** it drives the **timeline panel + the anomalies window** only; `live`, `self-cost` and the anomaly drill-down are **window-independent** | 5.2, Phase 3 | The old window picker is gone (the **pinned** residue check below); the panel's own labelled control is the only range UI for this module; the spec asserts a range change issues **exactly one** `/api/resources/history` request and **none** to `/live` or `/self-cost` |
| 5.5 | Ship the **anomalies panel** (ADJ-v): a table of `/api/resources/anomalies` rows (`id`, `kind`, `severity`, `peak`, `t_start`, `t_end`) whose row click opens the **existing** drill-down (`/api/resources/anomalies/{id}/evidence` + `/api/resources/traceback?t0&t1`) | 5.2 | The panel spec asserts the rows render, the click issues the two drill-down calls, and the padded window uses the ported `anomalyDrilldownWindow` policy (now **outside** the chart) |
| 5.6 | Ship the **standing process-local disclosure** on the anomalies panel (ADJ-v): the journal is an in-RAM `deque(maxlen=256)`, **reset on daemon restart** — it can never span overnight BY CONSTRUCTION. The panel's header states this in one line | 5.5 | The disclosure element exists and is asserted by the spec; a copy review confirms it does NOT claim durability |
| 5.7 | Ship the **visible anomaly-marker labels** (ADJ-iv shape 1): the API words the endpoint already serves — `kind`, `severity`, `series_key`, `peak` — become **visible** on the marker (today they exist only in a hover-only native `<title>`: `frontend/src/app/pages/resources/svg-timeline/svg-timeline.component.ts:530-532`) | Phase 4, 5.4 | The marker carries a visible label/tooltip with those words; a spec asserts the four fields are present |
| 5.8 | Ship the **client-side data-gap annotation** (ADJ-iv shape 2): a discontinuity derived from `/history` itself (a series that drops to zero or to a missing sample and regrows) is annotated **"DATA GAP — series unavailable in this interval"**. It is **never** labelled "restart", "deploy" or "promote" — the FE says only what it derived | 5.4 | The annotation spec asserts the label text contains "data gap" and does **not** contain restart/deploy/promote; a planted "restart" wording fails the spec |
| 5.9 | **Second re-point of the resource timeline (AM-27).** The timeline moves from the **registry** (where Phase 4 left it) to the **panel role** — the Chart.js wrapper with the house anomaly plugin — so the resource timeline is the lane's first real interactive panel (it carries the anomaly overlay, the drill-down click, and the baseline capability) | Phase 4, 5.4 | The timeline renders through the Chart.js panel; the anomaly markers survive as the house plugin; the drill-down click still opens the evidence + traceback |
| 5.10 | **Per-source state for the custom slot (AM-1).** The custom slot renders **its own source's** state — the resources door is **fail-open** (200), so under the default install posture tab 1 shows **real data**, not the central door's `auth_not_configured` panel. The tab-1 body reads its own state and never the central door's | 5.2 | A spec asserts: with the central door refusing (503) and the resources door answering 200, the tab-1 body renders the live strip + timeline with data and **no** auth panel |
| 5.11 | **Request-generation discipline for tab 1 (AM-16).** Tab 1 gains **three concurrent request classes** (the 8 s live poll, the frame/panel-driven window load, the anomalies load) with no stated token discipline. Apply the Phase-3 request-generation rule: a **window change invalidates the window + anomalies loads** but **NOT** the live strip's 8 s poll (the poll is not gated by the window generation) | 5.3, 5.4 | A spec asserts: a window change discards an in-flight window/anomalies response; the live strip's poll response is not discarded by a window change |
| 5.12 | Remove the tab-1 **placeholder** left by Phase 2 and wire the custom slot's `loadComponent` to the real tab-1 body | 5.2 | Grep: 0 hits for the placeholder string; the manifest's custom slot resolves to the real component |
| 5.13 | **Residue check pinned (AM-23).** The exact command for "the old window picker is gone" is `grep -rn "TIMELINE_WINDOWS\|window-picker" frontend/src/app/pages/metrics/resource/` with the allowlist row `frontend/src/app/models/resources.models.ts:16` (the frozen union definition) — every other hit must be zero | 5.4 | The pinned grep returns **zero** hits outside its allowlist row |

## Coupling

- **Tight with:** Phase 2 (the custom slot + the manifest entry + the per-source
  state model), Phase 3 (the central-sourced frame control vs this module's own
  panel control), Phase 4 (the timeline's registry re-point + the anomaly
  plugin), Phase 6 (the correctness fixes land on these panels), Phase 7 (the
  redirect's target is this tab), Phase 9 (the L4 leg proves it stays lit).
- **Loose with:** Phase 9 (PX-1, PX-13).
- **Independent of:** Phase 1, Phase 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| A frame-level state blanks tab 1 — the inverse of the lane's value (the AM-1 defect) | High | 5.10's per-source state spec + the L4 verification leg (Phase 9) |
| The data-gap annotation is read as a restart record | Medium — it would repeat the audit's "unmoored event dots" defect in a new form | 5.8's spec forbids the words; the label states "data gap"; the copy names the derivation |
| The anomalies panel implies durability it does not have | Medium | 5.6's standing disclosure, asserted by a spec |
| Relocating the module breaks the retired-route specs mid-phase | Medium | The relocation is a `git mv`; the specs move with the code; the old specs are deleted in Phase 7 with the route |
| The double re-point (Phase 4 → registry, Phase 5 → panel) is forgotten or double-applied | Medium | 5.9 names the second re-point explicitly; the Phase-4 exit criterion records which ordering was taken (AM-27) |
| The 8 s Live poll keeps running while the tab is not selected | Medium | The existing visibility gate (the tab body polls only while selected) is preserved — the tab-1 body follows the shell's `active` input contract |
| A window change discards the live strip's response (over-invalidation) | Low | 5.11's spec asserts the poll is NOT gated by the window generation |

## Exit Criterion

- The resource module lives at `frontend/src/app/pages/metrics/resource/`; the
  old directory is gone.
- Tab 1 renders **Live strip → Timeline panel → anomalies panel**, flat, with
  no nested tab group.
- The module's **own labelled** range control drives the timeline + the
  anomalies window; a range change issues exactly one `/history` request and
  none to `/live` or `/self-cost`; the pinned residue grep is clean.
- The anomalies panel lists rows, opens the existing drill-down, and carries
  the process-local disclosure.
- Anomaly markers carry **visible** labels with the API words.
- Any gap annotation says "data gap" and never "restart"/"deploy"/"promote".
- The timeline renders through the **Chart.js panel** (its second re-point),
  with the anomaly markers surviving as the house plugin.
- **Tab 1 stays lit under a central-door failure** (5.10's spec): the custom
  slot renders its own source's state, never the central door's.
- A window change invalidates the window/anomalies loads but **not** the live
  strip's poll.
- The Phase-2 placeholder is gone.
- The relocated specs are green; the existing live/timeline specs were **moved
  or dispositioned**, not dropped (the disposition is listed per assertion).
- **E-1 re-derivation:** this phase gates on **no** MEASURED artifact figure.
  Its constants (the 8 s poll cadence, the 4-value ladder, the 256-entry
  journal cap) are **code facts** re-verified at lane base and re-read at this
  phase's freeze.
