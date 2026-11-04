# Phase 3: Page frame — range selector + baseline comparison

## Objective

Put the two cross-cutting controls **above** the tabs: a **time-range
selector** whose options follow the active tab's vocabulary (with **visible**
enforcement, not the door's silent clamp), and a **baseline /
previous-window comparison** implemented FE-only as a 2× window fetch + a
**server-anchored** client-side midpoint split — **on the central tabs only**.

The frame replaces the hardcoded `WINDOW_S = 3600`
(`frontend/src/app/pages/metrics/charts/charts-tab.component.ts:195`).

## Scope note

The range control is the **frame's**; the **window value** is per-tab. A
central tab's window rides the central door (`window_s`); tab 1's window rides
its own ladder. One control, one value, two vocabularies — and the control's
**scope is per panel**, not tab-wide (AM-19).

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 3.1 | Add the range control to the metrics frame (`frontend/src/app/pages/metrics/metrics.component.html` / `.ts`): the options are the **union** of the manifest's per-tab `allowedWindows`; the options the active tab cannot serve are rendered **disabled** (visible enforcement — deliberately contrasting with the door's silent clamp at `daemon/routers/metrics.py:778`, where a 7 d request on a central tab returns a 200 with a 2 h window and no marker). **The control is CENTRAL-SOURCED for the central tabs (AM-1):** its option set and its value come from the **central** door's vocabulary; the custom tab's own ladder is a **separate, labelled** control on its panel ("Timeline range", AM-19) | Phase 2 | The frame spec asserts: a central tab shows 15m/30m/1h/2h with 24h/7d **disabled**; the custom tab's panel shows its own labelled control with 15m/1h/24h/7d and 30m/2h disabled |
| 3.2 | Implement the **switch-on-tab-change** behaviour: the selected window is **kept if the new tab supports it**, otherwise it snaps to that tab's `defaultWindowS` (and the UI says it snapped — a silent snap is a lie about what is on screen) | 3.1 | The spec asserts both branches; the snap is announced in the UI (a one-line meta note), never silent |
| 3.3 | Implement the **range→request** plumbing: the central tabs pass the window as `window_s` to `MetricsService.getSeries({ names, windowS, maxPoints })` (`frontend/src/app/services/metrics.service.ts:71-92`); tab 1's panel maps its window to its own ladder parameter (`frontend/src/app/services/resources.service.ts:54-56`, the `window`/`resolution` params) | 3.1 | A spec asserts the outgoing query for each window of each tab kind |
| 3.4 | Implement the **baseline / previous-window comparison**, **scoped to the central tabs** (AM-14): fetch `window_s = 2W` and split at the midpoint client-side. **The split is anchored on the RESPONSE's `window.from_utc/to_utc`** (parsed with the house `parseNaiveUtc`), **never the client clock** (AM-17: a 60 s client-clock skew would misassign ~8 % of a 2 h window; the epoch points live at `daemon/services/metrics_query/provider.py:384-387` and the window anchor at `envelope.py:201-203,:226-228` — the previously cited `envelope.py:386-388` **does not exist**, the file is 316 lines). **Guarded to W ≤ 1 h** (2W ≤ 7200 — the substrate ceiling) | Phase 2, 3.3 | The spec asserts: the midpoint split maths; the 2W fetch; the disabled-beyond-1h state; the reason text is present; **and** a faked client-clock offset leaves the split point **unchanged** |
| 3.5 | **Request-time guard, not UI-only** (AM-14, defence in depth): (a) the baseline toggle **auto-clears with its reason** when the range crosses 1 h; (b) the request builder **never emits `window_s > 7200`**; (c) the FE compares the response's window span against 2W and **refuses to render a baseline on a clamped payload** (the central door silently clamps to 7200 s and returns 200 — `daemon/routers/metrics.py:778`) | 3.4 | Three spec legs: (a) the toggle clears with its reason at 2 h; (b) no `/api/metrics/*` request ever carries `window_s > 7200`; (c) a response whose span is < 2W renders **no** baseline line and says why |
| 3.6 | **The custom slot renders the baseline toggle DISABLED with its reason** (AM-14): "the resources door serves a fixed 15m/1h/24h/7d vocabulary with no previous-window offset". The resources door accepts exactly those four values (400 otherwise — `daemon/routers/resources.py:120-130`) and `/api/resources/history` takes only `window` + `resolution` (`frontend/src/app/services/resources.service.ts:54-56`), so a "previous window" is **not fetchable** there; a client-side split of one payload would yield two HALF-length windows mislabeled "previous window" | 3.1 | The spec asserts: no `/api/resources/*` request ever carries a window outside `ResourcesWindow`; W = 2 h with the toggle forced on ⇒ **no over-ceiling request and no baseline line** |
| 3.7 | Route the baseline into the **renderer capability**, naming **the renderer that actually draws the T1 baseline** (AM-9/R-2): the **Chart.js panel** is the **normal path** (the baseline is a second dataset over the previous window); the **house SVG** carries the baseline as **fallback capability** (its optional baseline line), exercised through the **mount-failure fallback** and the **renderer specs** — the mini role has **no page surface in T1** | Phase 4 (implementation), 3.4 (contract) | The contract is declared in this phase and implemented in Phase 4; the spec asserts the baseline reaches the Chart.js panel's dataset and the house SVG's baseline capability |
| 3.8 | Surface the **baseline fidelity limits in the UI** (not in a code comment): `bucket_max` downsampling runs over the whole fetched window, so a bucket can straddle the split; the older half sits at the ring's eviction edge; the resolution halves (`resource-refactor-architecture.md` §3.2) | 3.4 | The UI carries the disclosure next to the baseline control; a spec asserts the disclosure element exists when the baseline is on |
| 3.9 | **Never forge cursors.** The cursor is unsigned base64 JSON (`envelope.py:270-289`; validation only of `t`/`o`, `routers/metrics.py:299-307`), so a client *could* mint one to shift the window — out-of-contract and brittle. The implementation must **not** mint one, and the spec must **pin** the absence (a planted-violation check that fails if a cursor is constructed client-side) | 3.4 | The negative pin is mutation-proven (planted → red → restored → green) |
| 3.10 | **Request-generation discipline (AM-16).** The existing supersede token bumps at request **START** only — a response is discarded only when a newer request has started. A range change or a baseline toggle that does not itself start a request lets an in-flight **OLD-shape** response paint under the NEW label (a new range label over an old payload, or a halved-resolution window after a toggle-off) for up to one 15 s poll. Implement a **request-generation bump** (not a start-bump): a change of the window value or the baseline toggle (a) triggers an **immediate** fetch in the active tab and (b) invalidates the in-flight response. **Invariant: the baseline SHARES the tab's single series request** — never a second request per family (this is what prevents the stale-window-pairing hazard); a spec leg pins the single-request invariant | 3.3, 3.4 | A spec asserts: a window change issues exactly **one** series request (not two, not zero); an in-flight response from the previous generation is discarded; the baseline does not add a request |
| 3.11 | **Effective-interval honesty (AM-18).** The meta line prints the door's `interval_s` (the OBSERVED sampler/publisher cadence, 15 s) while the plotted points at `window_s = 7200` are 30 s apart (480 ring points bucket-max-downsampled to 240). Today's hardcoded 3600 s window makes the two coincide; this phase's vocabulary change creates the falseness. The meta must report the **EFFECTIVE point spacing derived from the payload** (span ÷ point count), or explicitly label the door value as the observed cadence | 3.3 | A spec at the 2 h option asserts the printed interval equals the **derived** spacing (not the door's `interval_s`) |
| 3.12 | Record the **retention-tier copy** precisely, so the UI never confuses tiers with picker options: the custom tab's range control help text states that 24 h/7 d are served from the PG ladder whose **retention tiers** are raw 30 s/48 h, minute 60 s/14 d, hour 3600 s/180 d — the tier knobs live in `daemon/config.py:925-931` (the docstring) and `:980-1001` (the Field definitions: 2880 min = 48 h, 336 h = 14 d, 180 d). **CITATION CORRECTION (AM-17):** the previously cited `daemon/services/flusher.py:360-368` names a file that does not exist — the flusher is `daemon/services/resource_monitor/flusher.py` and those lines are the **sweeper's** row-drain body, not tier configuration; and `daemon/config.py:811-827` holds the **metrics** sampler intervals, not the resource tiers. The tiers BACK the ladder; they are **not** picker options | 3.1 | A spec asserts the copy names the tiers and does not offer them as options |

## Coupling

- **Tight with:** Phase 2 (the manifest owns the vocabulary — the frame reads
  it), Phase 4 (the baseline is a renderer capability; the frame drives it),
  Phase 5 (tab 1's window rides its own ladder, not `window_s`; the baseline
  toggle is disabled there), Phase 6 (the effective-interval meta line).
- **Loose with:** Phase 9 (the pixel list verifies PX-11/PX-12).
- **Independent of:** Phase 1, Phase 7, Phase 8.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The baseline's 2× fetch doubles the door load on every poll (10–15 s cadence × 6 central tabs) | Medium | The baseline fetch is **on-demand** (only while the toggle is on) and **shares the tab's single series request** (3.10's invariant + spec leg) |
| The midpoint split straddles a downsample bucket | Medium (fidelity, not correctness) | Disclosed in the UI (3.8); the honest first-class alternative (`end_offset_s`/`compare`) is routed out (routed (f)) |
| A central tab is asked for 2 h and the door silently clamps to something else | Low (2 h IS the ceiling) — but the **baseline** case is real | 3.5's request-time guard: the builder never exceeds 7200 s, and a clamped payload is detected by the span check and renders **no** baseline |
| The client clock mis-splits the window under skew | Medium | 3.4 anchors the split on the response's `from_utc/to_utc`; a faked-offset spec leg pins it |
| An old-shape response paints under the new range label | Medium | 3.10's request-generation bump + the single-request invariant |
| The meta line becomes false at exactly the 2 h option | Medium (a false statement about the data) | 3.11's derived-spacing meta |

## Exit Criterion

- The frame renders a range control whose option set follows the active tab,
  with the unsupported options visibly disabled; the custom tab's control is
  **separately labelled** and drives only its own panel.
- Switching tabs keeps the window when supported and **announces** the snap
  when not.
- The baseline toggle works for W ≤ 1 h (2W fetch + **server-anchored**
  midpoint split) on the **central tabs**, and is **disabled with a stated
  reason** on the custom tab.
- The three request-time guards hold: the toggle auto-clears past 1 h; no
  request ever exceeds `window_s = 7200`; a clamped payload renders no
  baseline line.
- The baseline **shares the tab's single series request** (one request per
  change) and an in-flight old-generation response is discarded.
- The fidelity disclosure is visible next to the baseline control.
- The negative "no forged cursors" pin is mutation-proven.
- The meta line reports the **derived** effective spacing, not the door's
  observed cadence.
- The retention-tier copy names 48 h/14 d/180 d as **tiers**, with the
  corrected cites, and the picker never offers them.
- The existing polling-hygiene specs stay green (the visibility gate, the
  supersede token, the skip-if-in-flight guard).
- **E-1 re-derivation:** this phase gates on **no** MEASURED artifact figure.
  Its constants (2 h = 7200 s, the 15 s effective interval, the 4-value
  resources ladder) are **code facts** re-verified at lane base
  (`daemon/services/metrics_query/constants.py:33`; `daemon/config.py:825-826`;
  `frontend/src/app/models/resources.models.ts:16`), not measurements — and
  they are re-read at this phase's freeze.
