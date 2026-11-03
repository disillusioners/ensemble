# Phase 5: Tab 1 — Resources on the custom slot

## Objective

Mount the resource module as **tab 1** of the consolidated page, over its
**6 existing support endpoints**, with the frame's range selector driving its
window — and with the anomalies facts finally rendered instead of hovering
invisible.

This is the only tab that renders data on the live install today (the
`/api/resources/*` door is fail-open while the central door is fail-closed),
which makes it the lane's most valuable tab and its most scrutinized one.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 5.1 | Relocate the resource module from `frontend/src/app/pages/resources/` to `frontend/src/app/pages/metrics/resource/` (`live/`, `timeline/`, plus the new anomalies panel). D-3 retires the route, and a directory named after a retired route is a lie the next reader will trust (IMPL-02) | Phase 2 | The move is a `git mv` (history preserved); no import path outside the module changed shape; the specs move with the code |
| 5.2 | Rebuild tab 1's body as a **flat column** (IMPL-03): **Live stat strip → Timeline panel → anomalies panel**. The existing nested `mat-tab-group` shell (`frontend/src/app/pages/resources/resources.component.ts:47-51`) is retired — nested tab groups inside a tab are a collision surface (the audit already found a 900 px banner/button collision) | 5.1, Phase 2 | The tab-1 spec asserts the three sections render in order; there is no nested tab group in the tab body |
| 5.3 | Re-point the Live strip: it keeps its own **8 s poll** cadence (a different question, a different cadence) and its `self-cost` chip, and it consumes `/api/resources/live` + `/api/resources/self-cost` | 5.2 | The existing live-tab specs stay green; the poll cadence is unchanged (8 s) |
| 5.4 | Re-point the Timeline panel: it consumes `/api/resources/history` with the frame's window (15m/1h/24h/7d — its own ladder, `timeline-tab.component.ts:60`), and its **own window-picker UI is removed** (the frame owns the range — one control for the page) | 5.2, Phase 3 | The old window picker is gone (grep: 0 hits in the resource module); the frame's control is the only range UI; the panel reads the frame's value |
| 5.5 | Ship the **anomalies panel** (ADJ-v): a table of `/api/resources/anomalies` rows (`id`, `kind`, `severity`, `peak`, `t_start`, `t_end`) whose row click opens the **existing** drill-down (`/api/resources/anomalies/{id}/evidence` + `/api/resources/traceback?t0&t1`) | 5.2 | The panel spec asserts the rows render, the click issues the two drill-down calls, and the padded window uses the ported `anomalyDrilldownWindow` policy (now **outside** the chart) |
| 5.6 | Ship the **standing process-local disclosure** on the anomalies panel (ADJ-v): the journal is an in-RAM `deque(maxlen=256)`, **reset on daemon restart** — it can never span overnight BY CONSTRUCTION. The panel's header states this in one line | 5.5 | The disclosure element exists and is asserted by the spec; a copy review confirms it does NOT claim durability |
| 5.7 | Ship the **visible anomaly-marker labels** (ADJ-iv shape 1): the API words the endpoint already serves — `kind`, `severity`, `series_key`, `peak` — become **visible** on the marker (today they exist only in a hover-only native `<title>`: `svg-timeline.component.ts:530-532`) | Phase 4, 5.4 | The marker carries a visible label/tooltip with those words; a spec asserts the four fields are present |
| 5.8 | Ship the **client-side data-gap annotation** (ADJ-iv shape 2): a discontinuity derived from `/history` itself (a series that drops to zero or to a missing sample and regrows) is annotated **"DATA GAP — series unavailable in this interval"**. It is **never** labelled "restart", "deploy" or "promote" — the FE says only what it derived | 5.4 | The annotation spec asserts the label text contains "data gap" and does **not** contain restart/deploy/promote; a planted "restart" wording fails the spec |
| 5.9 | Remove the tab-1 **placeholder** left by Phase 2 and wire the custom slot's `loadComponent` to the real tab-1 body | 5.2 | Grep: 0 hits for the placeholder string; the manifest's custom slot resolves to the real component |

## Coupling

- **Tight with:** Phase 2 (the custom slot + the manifest entry), Phase 3 (the
  frame's window drives tab 1), Phase 4 (the timeline migrates to the panel
  role; the markers become the house plugin), Phase 6 (the correctness fixes
  land on these panels), Phase 7 (the redirect's target is this tab).
- **Loose with:** Phase 9 (PX-1, PX-13).
- **Independent of:** Phase 1, Phase 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The data-gap annotation is read as a restart record | Medium — it would repeat the audit's "unmoored event dots" defect in a new form | 5.8's spec forbids the words; the label states "data gap"; the copy names the derivation |
| The anomalies panel implies durability it does not have | Medium | 5.6's standing disclosure, asserted by a spec |
| Relocating the module breaks the retired-route specs mid-phase | Medium | The relocation is a `git mv`; the specs move with the code; the old specs are deleted in Phase 7 with the route |
| The 8 s Live poll keeps running while the tab is not selected | Medium | The existing visibility gate (the tab body polls only while selected) is preserved — the tab-1 body follows the shell's `active` input contract |

## Exit Criterion

- The resource module lives at `frontend/src/app/pages/metrics/resource/`; the
  old directory is gone.
- Tab 1 renders **Live strip → Timeline panel → anomalies panel**, flat, with
  no nested tab group.
- The frame's range control drives the timeline's window; the old window picker
  is gone.
- The anomalies panel lists rows, opens the existing drill-down, and carries
  the process-local disclosure.
- Anomaly markers carry **visible** labels with the API words.
- Any gap annotation says "data gap" and never "restart"/"deploy"/"promote".
- The Phase-2 placeholder is gone.
- The relocated specs are green; the existing live/timeline specs were **moved,
  not dropped** (a diff review lists every assertion's new home).
