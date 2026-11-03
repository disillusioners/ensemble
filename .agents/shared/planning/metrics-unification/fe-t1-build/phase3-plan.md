# Phase 3: Page frame — range selector + baseline comparison

## Objective

Put the two cross-cutting controls **above** the tabs: a **time-range
selector** whose options follow the active tab's vocabulary (with **visible**
enforcement, not the door's silent clamp), and a **baseline /
previous-window comparison** implemented FE-only as a 2× window fetch + a
client-side midpoint split.

The frame replaces the hardcoded `WINDOW_S = 3600`
(`frontend/src/app/pages/metrics/charts/charts-tab.component.ts:195`).

## Scope note

The range control is the **frame's**; the **window value** is per-tab. A
central tab's window rides the central door (`window_s`); tab 1's window rides
its own ladder. One control, one value, two vocabularies.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 3.1 | Add the range control to the metrics frame (`frontend/src/app/pages/metrics/metrics.component.html` / `.ts`): the options are the **union** of the manifest's per-tab `allowedWindows`; the options the active tab cannot serve are rendered **disabled** (visible enforcement — deliberately contrasting with the door's silent clamp at `daemon/routers/metrics.py:778`, where a 7 d request on a central tab returns a 200 with a 2 h window and no marker) | Phase 2 | The frame spec asserts: a central tab shows 15m/30m/1h/2h with 24h/7d **disabled**; tab 1 shows 15m/1h/24h/7d with 30m/2h disabled |
| 3.2 | Implement the **switch-on-tab-change** behaviour: the selected window is **kept if the new tab supports it**, otherwise it snaps to that tab's `defaultWindowS` (and the UI says it snapped — a silent snap is a lie about what is on screen) | 3.1 | The spec asserts both branches; the snap is announced in the UI (a one-line meta note), never silent |
| 3.3 | Implement the **range→request** plumbing: the central tabs pass the window as `window_s` to `MetricsService.getSeries({ names, windowS, maxPoints })` (`frontend/src/app/services/metrics.service.ts:71-92`); tab 1 maps its window to its own ladder parameter (`resources.service.ts:57`, the `window`/`resolution` params) | 3.1 | A spec asserts the outgoing query for each window of each tab kind |
| 3.4 | Implement the **baseline / previous-window comparison**: fetch `window_s = 2W` and split at the midpoint client-side (points carry epoch seconds — `daemon/services/metrics_query/envelope.py:386-388`). **Guarded to W ≤ 1 h** (2W ≤ 7200 — the substrate ceiling). When the active window exceeds 1 h, the baseline toggle is **disabled with a stated reason** ("previous-window comparison needs a 1 h window or less — the door retains 2 h") | Phase 2, 3.3 | The spec asserts: the midpoint split maths; the 2W fetch; the disabled-beyond-1h state; the reason text is present |
| 3.5 | Surface the **baseline fidelity limits in the UI** (not in a code comment): `bucket_max` downsampling runs over the whole fetched window, so a bucket can straddle the split; the older half sits at the ring's eviction edge; the resolution halves (`resource-refactor-architecture.md` §3.2) | 3.4 | The UI carries the disclosure next to the baseline control; a spec asserts the disclosure element exists when the baseline is on |
| 3.6 | **Never forge cursors.** The cursor is unsigned base64 JSON (`envelope.py:270-289`; validation only of `t`/`o`, `routers/metrics.py:299-307`), so a client *could* mint one to shift the window — out-of-contract and brittle. The implementation must **not** mint one, and the spec must **pin** the absence (a planted-violation check that fails if a cursor is constructed client-side) | 3.4 | The negative pin is mutation-proven (planted → red → restored → green) |
| 3.7 | Route the baseline into the **renderer capability** (the optional baseline line in the house SVG; the baseline dataset in Chart.js) via the frame's baseline toggle — the frame owns the toggle, the renderer owns the drawing | Phase 4 (implementation), 3.4 (contract) | The contract is declared in this phase and implemented in Phase 4; the exit criterion here is the contract + the frame wiring |
| 3.8 | Record the **retention-tier copy** precisely, so the UI never confuses tiers with picker options: the tab-1 range control's help text states that 24 h/7 d are served from the PG ladder whose **retention tiers** are raw 30 s/48 h, minute 60 s/14 d, hour 3600 s/180 d (`daemon/services/flusher.py:360-368`, `daemon/config.py:811-827`) — the tiers BACK the ladder, they are **not** picker options | 3.1 | A spec asserts the copy names the tiers and does not offer them as options |

## Coupling

- **Tight with:** Phase 2 (the manifest owns the vocabulary — the frame reads
  it), Phase 4 (the baseline is a renderer capability; the frame drives it),
  Phase 5 (tab 1's window rides its own ladder, not `window_s`).
- **Loose with:** Phase 9 (the pixel list verifies PX-11/PX-12).
- **Independent of:** Phase 1, Phase 6, Phase 7.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The baseline's 2× fetch doubles the door load on every poll (10–15 s cadence × 7 tabs) | Medium | The baseline fetch is **on-demand** (only while the toggle is on) and the baseline shares the tab's series request rather than issuing a second one per family |
| The midpoint split straddles a downsample bucket | Medium (fidelity, not correctness) | Disclosed in the UI (3.5); the honest first-class alternative (`end_offset_s`/`compare`) is routed out (routed (f)) |
| A central tab is asked for 2 h and the door silently clamps to something else | Low (2 h IS the ceiling) | The frame only ever asks for windows ≤ 7200 s, so the silent clamp cannot fire |
| The snapshot/series polls multiply with 7 tabs | Medium | The visibility gate (a tab body polls only while selected) is preserved from the current shell (`:177-197`); the frame's snapshot fetch is shared, not per-tab |

## Exit Criterion

- The frame renders a range control whose option set follows the active tab,
  with the unsupported options visibly disabled.
- Switching tabs keeps the window when supported and **announces** the snap
  when not.
- The baseline toggle works for W ≤ 1 h (2× fetch + midpoint split) and is
  disabled with a stated reason beyond it.
- The fidelity disclosure is visible next to the baseline control.
- The negative "no forged cursors" pin is mutation-proven.
- The retention-tier copy names 48 h/14 d/180 d as **tiers**, and the picker
  never offers them.
- The existing polling-hygiene specs stay green (the visibility gate, the
  supersede token, the skip-if-in-flight guard).
