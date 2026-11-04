# Architecture Review — FE-only T1 metrics dashboard plan set (wi-32a99618)

Date: 2026-11-03 · Reviewer: Architect (controller); findings aggregated from three dispatched
analysts (structural-design / data-flow-design / resilience-design), each grounded read-only in the
worktree `/home/nea/ensemble-src-wt-fe-metrics-dashboard` @ `3a6bf7ba5`.

Inputs: `fe-t1-build/` plan set (plan-overview + phase0..9 + decisions + research-findings), the four
binding design docs (`fe-ui-audit-and-resource-refactor.md`, `charting-engine-decision{,-independent}.md`,
`resource-refactor-architecture.md`), `frontend/` source, the daemon routers the FE talks to,
`angular.json`, and the shipped `releases/v0.28.9` FE dist.

Mode: **verify-and-amend**. D-1 (Chart.js v4 sole vendor, lazy-chunk-only, interactive panels, one
consolidated house SVG), D-2 (auth fail-closed + actionable empty-state), D-3 (/resources retired
same-release with param-preserving redirect), and per-instance top-N routing were treated as FINAL
throughout. **No amendment below re-opens any ruling** — every amendment is a mechanics fix inside
the ruled rails.

---

## Verdict

**SOUND-WITH-AMENDMENTS.**

The architecture direction is sound, confirmed independently by all three analysts: the engine
consolidation is achievable (both engines are single-consumer, single-module; zero non-metrics
consumers), the lazy-chunk seam is mechanically available (both routes are `loadComponent`-lazy),
the `custom` slot honestly models the resources door, the redirect mechanism choice is right, phase 0
is properly first with a non-vacuous exit, and the live-contact posture is sound (no 🔴 breach found).

The defects are in **mechanics**: 29 amendments (14 🔴, 14 🟡, 1 🟢). Each 🔴 would, if implemented
as written, ship a defect, break the build/merge, invalidate the verification route, or invert the
lane's stated value on the default install. All are foldable plan edits; none changes direction.

**Confidence: High.** Cross-corroboration: the bundle-budget defect was derived independently from
builder-source semantics (analyst A) and a real dist measurement (analyst C); the per-source state
defect was derived independently from the data-flow side (analyst B) and the resilience side
(analyst C). The assumption that would change this verdict: a fresh lane-base build contradicting
the dist-derived baseline numbers (see AM-2) — it would change the numbers, not the mechanism.

---

## Rulings on the analysts' open questions (decided here, binding for the fold)

**R-1 — Panel granularity (central tabs): one panel per metric-kind + unit group, kind read from
the WIRE.** Registered families mix kinds (F4 registers a gauge *and* a counter —
`daemon/services/metrics_query/wiring/w1.py:194-228`; F9 likewise — `w2.py:120-150`), and the wire
already carries `chart_role` per metric (`metrics.model.ts:164`, resolved per series at
`charts-tab.component.ts:222`). Per-family panels would break counter-honesty and falsify unit
labels; per-raw-series panels lose the family grouping the manifest promises. Granularity therefore
derives at panel-composition time from wire kinds; the manifest stays registration-only on the
family axis. (Lands in AM-9/AM-12.)

**R-2 — House-SVG mini role in T1: capability-retained, NO page surface.** No mini consumer exists
anywhere in the plan's consumer map (grep "sparkline" = 0 hits in `frontend/src`; phases 4–5 name
exactly two renderer consumers, both panel/fallback). D-1/ADJ-ii's five mini-tier extensions stay IN
the consolidated renderer and are exercised via (a) the mount-failure fallback path and (b)
renderer-level specs — not via a live page surface. Phase 3.7 must name the renderer that actually
draws the baseline in T1 (the Chart.js panel is the normal path; the house SVG carries the baseline
as fallback capability). (Lands in AM-9.)

---

## Amendments

Severity: 🔴 ships a defect / breaks build or merge / invalidates verification · 🟡 unsound
mechanic, will bite · 🟢 hardening. Each amendment names the phase(s) its edit lands in.

### 🔴 AM-1 — Door state must be PER-SOURCE, not frame-level (folds analyst F5-B + F8-1)
**Finding.** The frame-level `auth_not_configured` panel (8.1/8.2/8.6) blanks the ONLY tab with live
data under the default install posture. Live reality (2026-11-03, v0.28.9): auth OFF ⇒
`/api/metrics/*` = 503 `AUTH_NOT_CONFIGURED` for everyone while `/api/resources/*` = 200 open. Read
literally, 8.6 ("the body shows the frame's state") renders the auth panel in tab 1's body — the
inverse of phase 5's own premise ("the lane's most valuable tab"). The manifest's `central|custom`
slot is the natural seam and no task consumes it for state; the mixed shape is untested because L1
runs auth-ON only.
**Evidence.** `phase8-plan.md:21-26`; `decisions.md` IMPL-09; `phase5-plan.md:10-12`;
`plan-overview.md:107`; `models/metrics.model.ts:429-432`; `phase9-plan.md:25`.
**Required change.** Door state becomes per source: the frame holds per-tab state; the
`auth_not_configured` panel renders on central-door surfaces only (catalog drawer + the 6 central
tab bodies); each `custom` slot renders its own source's state; under a central-dark posture tab 1
renders its live strip + timeline. Add verification leg **L4 (auth OFF)**: tab 1 renders real data
while central tabs show the actionable state. State in 3.1 that the range control is central-sourced.
**Phase.** 2 (state model), 8 (8.1/8.2/8.6), 9 (L4).

### 🔴 AM-2 — Bundle-guard architecture is wrong three ways (folds analyst F7 + F9-3/F9-4/F10-5)
**Finding.** The headline D-1 safety claim — "a static-import leak hard-fails the production build
at 6 MB (headroom 89,982 B) and both FE packs go red" — is false on three independent grounds.
(a) The Angular `initial` budget counts only entry points + static imports; **lazy chunks are
counted by no budget** (only `initial` + `anyComponentStyle` exist) — a lazy Chart.js leak trips
nothing. (b) The baseline is wrong by ~793 kB: the lane's tree (v0.28.9 FE) measures **5,115,820 B**
initial (main 215,495 + styles 655,429 + mermaid 3,559,319 + 11 initial chunks 691,361) against the
decimal **6,000,000 B** ceiling — real headroom **884,180 B**; a full eager Chart.js leak
(~250–400 kB) fits and the build stays green. (c) B1's "byte-identical file list" is unsatisfiable
under `outputHashing: all` (any change renames chunks), the "≈0 delta" has no threshold or named
baseline artifact, and the only bundle guard runs at Phase 9 — the leak window spans Phases 1–8
unguarded. Corroborating (aggregation check): the plan's own figures are internally inconsistent —
5,909,018 + 89,982 = 5,999,000 ≠ 6,000,000.
**Evidence.** `plan-overview.md:282,:376`; `phase1-plan.md:45`; `phase9-plan.md:29`;
`research-findings.md:214-215`; `angular.json` (budgets; `:73` outputHashing);
`releases/v0.28.9/frontend/dist/frontend/browser/index.html` chunk sum;
`@angular/build/src/utils/bundle-calculator.js:18,:155-170,:248-250`.
**Required change.** (1) Re-measure the baseline at the lane base commit with the builder's own
stats; freeze the real numbers + a named baseline artifact. (2) Restate the risk mechanism: a lazy
leak trips no budget — the guards are a measured initial-total delta + an import-graph assertion.
(3) Redefine B1 as (a) numeric |Δ initial-total| ≤ 2 kB vs the named base artifact and (b) no
`chart.js` marker in any initial chunk. (4) Co-locate the guard: add the initial-set measurement to
**Phase 1's and Phase 4's** exit criteria. (5) Delete the "hard-fails the build + both packs" claim
from plan-overview §6.3 and §8 risk 1.
**Phase.** 1, 4, 9, plan-overview.

### 🔴 AM-3 — Redirect param preservation has no specified mechanism; `tab` collides (folds F7-A/F7-B/F7-E)
**Finding.** `canMatch` guards receive `(route, segments)` — **not query params**. The plan's
"all preserved" guarantee has no mechanism; the obvious implementation
(`Router.getCurrentNavigation()?.extractedUrl.queryParams`) silently drops every param when null —
a silent failure of the exact guarantee 7.1 asserts, and a spec that builds the UrlTree directly
cannot catch it. Separately, once phase 2 makes `tab` a first-class `/metrics` param, a preserved
incoming `tab` collides with the redirect's own `tab=resource` — "all preserved" and "the redirect
writes tab=resource" cannot both hold, and the acceptance fixture contains no `tab`. The raw-URL
string assertion is order-brittle.
**Evidence.** `phase7-plan.md:17-18`; `phase2-plan.md:21-22`; `decisions.md` IMPL-06;
`app.routes.ts:22-29` (existing `canMatch` precedent reads no params); Angular 21
(`frontend/package.json`).
**Required change.** 7.1 names the mechanism — the guard reads
`getCurrentNavigation()?.extractedUrl.queryParams` with an explicit null-fallback (log + degrade,
never crash); precedence rule: `tab=resource` **overrides** an incoming `tab` (claim narrows to
"all params except `tab`"); acceptance is an end-to-end `router.navigateByUrl('/resources?window=24h&x=1')`
landed-URL assertion comparing **parsed** `queryParams` (not a raw string), plus a leg
`/resources?tab=charts` ⇒ lands on `?tab=resource`. Do not accept a unit assertion on a hand-built
UrlTree.
**Phase.** 7.

### 🔴 AM-4 — Empty-state class table misses the FIRST state real users will meet (folds F8-2/F8-4)
**Finding.** The five-class table misses **401 `AUTH_REQUIRED`**: the FE sends
`Accept: application/json` so the auth middleware's 401 branch applies (not the browser 302), and
bearer rejection is also 401 — yet 401 decodes to `unexpected` + a dead Retry
(`models/metrics.model.ts:438-441`). The user's "enable auth" morning item makes this the first
state every user meets. Also missing: **timeout-but-alive** (`MetricsService` has no rxjs timeout —
a hung read leaves the page in loading state with NO state at all), **404 `unknown_family`**
(version skew; Retry is wrong), and **400** `invalid_label_filter`/`invalid_cursor` (an FE bug
class; Retry is wrong). The D-2 *ruling* is untouched — its class TABLE is implementation beneath it.
**Evidence.** `decisions.md` D-2; `metrics.model.ts:359-364,:438-441`;
`daemon/routers/metrics.py:586,:636,:709` (401 declared on all three routes), `:643,:697` (404),
`:736` (400); `daemon/middleware/auth.py:555-566,:596-607,:355-380`;
`services/metrics.service.ts:47-87` (no timeout; house pattern at `services/api.service.ts:366`).
**Required change.** Widen the table: `auth_required` (401 — cause + "sign in / obtain a scoped
token" action; NO Retry), `timeout` (client-side ~15 s → explicit class; no Retry), `unknown_family`
("this daemon does not publish that family — version skew"; no Retry), `bad_request` (logged FE-bug
state; no Retry). Phase 2 adds the service timeout; Phase 9 adds leg **L1b** (no bearer ⇒ 401 ⇒ the
sign-in panel, not "unexpected").
**Phase.** 8 (+ a D-2 consequence clause in decisions.md), 2 (timeout), 9 (L1b).

### 🔴 AM-5 — The fence boots the daemon on the wrong port: `DAEMON_PORT` is inert, and the scrub kills `PORT`
**Finding.** P6 scrubs `PORT` while P7 sets `DAEMON_PORT=8079` — but the daemon reads `PORT` via
`config.yaml` `port: ${PORT:-8088}`; `DAEMON_PORT` appears nowhere under `daemon/` (the house's own
launcher warns it is inert). The fenced dev daemon binds **8088** while every verification step
targets **8079**: the phase-9 route validates against the wrong daemon or nothing. The boot command
is the placeholder "`<house dev boot>`".
**Evidence.** `plan-overview.md:210-213`; `daemon/api.py:1953`; `daemon/__main__.py:529`;
`config.yaml:112-113`; `launcher.sh:692-702`; `dev.sh:141` (`PORT=8079`), `:155` (uvicorn `--reload`
bypasses `__main__` preflight/sweep).
**Required change.** Name the boot command explicitly; set **`PORT=8079`** (not `DAEMON_PORT`)
*after* the scrub; assert the **effective** port from the running daemon (boot log / `/livez`
body), not an env var; state whether the boot is `python -m daemon` (preflight/sweep live) or
`dev.sh`.
**Phase.** 9 (9.1/9.2), plan-overview §6.1.

### 🔴 AM-6 — PX capture order is impossible: nothing re-enables auth after 9.4
**Finding.** 9.4 (auth-absent) precedes 9.6 (full pixel list, depends on 9.4), but PX-1…PX-7 assert
real rendered values and need the auth-ON state of 9.3; auth posture is restart-required daemon
config and no task names a restart — the data-bearing captures are impossible as ordered.
**Evidence.** `phase9-plan.md:26,:28`; `plan-overview.md:260-266`; `config.yaml:129-152`.
**Required change.** Split 9.6 into **9.6a** (PX-1…7, 9…14 — auth ON) and **9.6b** (PX-8 — auth OFF)
with explicit "restart the dev daemon with auth enabled/disabled" steps and a `/livez`
re-assertion after each restart.
**Phase.** 9.

### 🔴 AM-7 — Phase-8 coupling declarations contradict the task graph; 7↔8 coupling is prose-only
**Finding.** Task 8.9 `Depends On: Phase 4, Phase 6` while the phase Coupling block and the overview
coupling map declare phase 8 **independent** of 4 and 6 (and the same row contradicts phase 8's own
"Tight with: Phase 4"). A lane sequencing by the map runs phase 8 before the renderer capability
(data-end marker) exists ⇒ 8.9 unsatisfiable or silently skipped. Separately, the 7↔8 tight coupling
("the redirect's landing surface IS the empty state") exists in prose only — no task-graph edge in
either direction, so the redirect can land on the contradictory double banner.
**Evidence.** `phase8-plan.md:29,:33-34,:36`; `plan-overview.md:170-171`; `phase7-plan.md:30`.
**Required change.** Correct map row 8 and the phase-8 Coupling block to "tight with 4 and 6"; add
the task-graph edge (7.1 `Depends On: Phase 8`, or a phase-8 task depends on 7) so map and graph
agree.
**Phase.** plan-overview §5, 7, 8.

### 🔴 AM-8 — The manifest type cannot express the four dispositions the plan needs
**Finding.** The two-variant union (`CentralDataTab | CustomDataTab`, non-optional `familyIds`,
single `availability` axis) cannot express: HIDDEN (source CB — not a registered family, so no
`familyIds` exist), PLANNED-unbound-but-registered, BOUND-but-dark, DROPPED. "Registered but no
series yet" and "not instrumented" are type-indistinguishable — and D-2 has no door-class for the
latter. Phase 2.3's planned entries "render nothing" with a `trackingNote` but no variant.
**Evidence.** `resource-refactor-architecture.md:31,:44,:58`; `plan-overview.md` §2.2/§4;
`phase2-plan.md:17`.
**Required change.** Widen the union (a third variant, or a `source: 'none'|'registered'|'planned'`
discriminant); make `familyIds` optional (or add a `noFamily` variant) so a non-family module is
type-expressible without empty-array sentinels; 2.3 binds each hidden module to its variant.
**Phase.** 2.

### 🔴 AM-9 — No surface→intent→renderer→granularity table; mini role has no T1 consumer; completion sentence vacuous-or-false (folds F12/F18 + Rulings R-1/R-2)
**Finding.** Roles are assigned to ENGINES (IMPL-04) but no mapping exists for the 7 tabs' surfaces:
the central tabs' panels are unassigned (today's charts tab renders one chart per series — the
small-multiple pattern — while 6.3's unit vocabulary implies per-family panels). The house-SVG
**mini role has no consumer in T1** (grep "sparkline" = 0 hits; phases 4–5 name two consumers, both
panel/fallback), yet ADJ-ii's five mini extensions are ruled in and 3.7 routes the frame's baseline
into "the house SVG". The completion sentence "every chart carries a unit-labeled y-axis" is
vacuous (no mini renders) or false (6.2 exempts the mini) as written.
**Evidence.** `decisions.md:250-278,:84-104,:426`; `plan-overview.md:31` (§1), §10 criterion 5;
`phase2-plan.md` (2.4); `phase4-plan.md` (4.10); `charts-tab.component.ts:109-115`.
**Required change.** Per Rulings R-1/R-2: add a surface→intent→renderer→granularity table to
plan-overview §4 (central-tab panels = Chart.js `trend` role, one panel per kind+unit group, kind
from the wire; mini = capability-only, exercised via fallback + renderer specs); 3.7 names the
renderer that draws the baseline in T1 (Chart.js panel normal-path; house SVG fallback capability);
reword §1/§10 to "every **panel** carries a unit-labeled y-axis; the mini role renders no axis".
**Phase.** 2, 3, 4, plan-overview §1/§10.

### 🔴 AM-10 — Relocation breakage: the pins spec reads a deleted file; the scoped type-check silently loses the moved specs (folds F1/F3)
**Finding.** (a) The D2 zero-dependency guard pins spec READS the engine file from disk
(`readFileSync … svg-line-chart.component.ts`) — task 4.9 deletes that file and task 1.2 amends
only the banned list, not the path: the spec crashes at merge. (b)
`tsconfig.metrics-fe.json:11` includes the OLD spec path; tsc silently ignores stale includes ⇒ the
relocated 241-line + 237-line specs fall OUT of the scoped type-check (P5-M9) with no signal.
**Evidence.** `metrics.bindings.pins.spec.ts:76-88,:94`; phase 4 task 4.9; phase 1 task 1.2;
`tsconfig.metrics-fe.json:11`.
**Required change.** 1.2 re-points the `readFileSync` path to the consolidated renderer and scopes
the banned-list check to the house-SVG file only (so the wrapper's Chart.js import does not trip
it); 4.1 updates the tsconfig include to the new spec path(s); 4.9 removes the stale entry; the
phase-4 exit criterion states the relocated specs are INSIDE the scoped type-check.
**Phase.** 1, 4.

### 🔴 AM-11 — Three canvas-fatal e2e legs; the plan re-specifies at most one
**Finding.** `metrics-gate.spec.ts:277` (count assertion on `[data-test="svg-line-chart"]` — the
component and selector are deleted by 4.9), `:281-288` (SVG path `d`-attribute geometry: H/V present,
no diagonal), `:290-296` (mirror for `line`), `:298` (`chart-tick` SVG text visible). Under a
canvas renderer there is no `d` and no `chart-tick`: a DOM proxy carrying `data-render-mode`
satisfies the selector but not the geometry or tick assertions. Phase 4.4 re-specifies only the
render-mode distinction; the binding source requires the whole range re-specified.
**Evidence.** `frontend/e2e/metrics-gate.spec.ts:277-298`; phase 4.4;
`resource-refactor-architecture.md:390`.
**Required change.** Add a phase-4 task enumerating all three legs with replacement assertions
(count → per-series proxy count; `d`-geometry → a renderer-level step-vs-linear assertion;
`chart-tick` → the wrapper's UTC tick text in the hidden data table / DOM proxy) and state
explicitly that the `d`-attribute assertion is **retired**, not preserved.
**Phase.** 4.

### 🔴 AM-12 — Unit vocabulary keyed on families, but families MIX kinds; "2 panels" leaves the count class unplaced (folds F14/F16 + Ruling R-1)
**Finding.** The unit vocabulary is family→unit while families mix kinds: F4 registers
`ensemble_db_pool_native` (gauge) *and* `ensemble_db_pool_requests_errors_total` (counter); F9 a
counter + 0/1 gauge. One F4 panel on a shared axis mixes a LINEAR gauge with a STEPPED counter
(counter-honesty broken) and one unit-per-family label is false. And 6.1's "2 panels" (memory, CPU)
leaves Engine A's count-class series (`threads`, `instances_active` — user-toggleable) unplaced:
enabling one lands a count series on an MB/% panel — the exact defect class 6.1 exists to kill.
**Evidence.** `w1.py:194-228`; `w2.py:120-150`; `svg-timeline.component.ts:56-63` (5 series: MB, MB,
%, count, count), `:50-54` (3 default-enabled); phase 6.1/6.3; `decisions.md:123`.
**Required change.** Per Ruling R-1: the unit vocabulary keys on the **metric (series) name**;
panels compose per kind+unit group with kind read from the wire (never guessed from FE tables);
6.1 gains a third panel rule (counts) and its acceptance covers the toggle path (5 series, 3 unit
classes), not just the 3 defaults.
**Phase.** 2, 6.

### 🔴 AM-13 — 6.5 asks the FE to compute a coverage figure it cannot compute
**Finding.** The daemon computes `coverage_pct = min(1.0, attributed / anon_bytes)`; `anon_bytes`
is NOT the sum of the displayed components (`attributed_mb` + `unattributed_mb`), so "the coverage
is computed from the same basis as the components shown next to it" is unsatisfiable — and 6.4's
"the FE cannot invent a basis" pulls the other way.
**Evidence.** `daemon/services/resource_monitor/sampler.py:759,:771,:1026`;
`daemon/routers/resources.py:369`; `live-tab.component.html:144-157`; phase 6.4/6.5.
**Required change.** 6.5 picks one layer: the FE displays the payload's `coverage_pct` **verbatim**
next to the components and renders an explicit non-reconciliation sentence when the payload's own
fields disagree; pin "the FE computes NO coverage percentage".
**Phase.** 6.

### 🔴 AM-14 — Baseline has no valid implementation on tab 1, and the W≤1h guard is a UI affordance the door silently defeats (folds F4-A/F4-B — one phase-3 revision, the two interact)
**Finding.** (a) The resources door accepts exactly `{15m,1h,24h,7d}` (400 otherwise) and
`/api/resources/history` takes only `window` + `resolution` — no end-offset ⇒ a "previous window"
is not fetchable on that door; a client-side split of one payload yields two HALF-length windows
mislabeled "previous window" (or 400s the only live-lit tab). (b) The W ≤ 1 h guard is UI-only:
nothing re-evaluates the toggle when the range moves to 2 h; the builder can still emit 2W = 4 h;
the central door SILENTLY CLAMPS to 7200 s and returns 200 — the FE splits a 2 h payload at a fake
midpoint while the UI claims two 2 h windows: the exact dishonesty class phase 3.1 condemns.
**Evidence.** `phase3-plan.md:24,:27,:47`; `decisions.md:70,:305-315`; `resources.models.ts:16`;
`daemon/routers/resources.py:120-130,:458-462`; `resources.service.ts:50-58`;
`daemon/routers/metrics.py:778`; `daemon/services/metrics_query/constants.py:33`.
**Required change.** Scope the baseline to **central tabs** (the 2W overfetch is defined against the
central door's `window_s`); the `custom` slot renders the toggle **disabled with reason** ("the
resources door serves a fixed 15m/1h/24h/7d vocabulary with no previous-window offset"). Make the
guard request-time regardless (defense in depth): the toggle auto-clears with its reason when the
range crosses 1 h; the request builder never emits `window_s > 7200`; the FE compares the
response's window span against 2W and refuses to render a baseline on a clamped payload. Spec legs:
no `/api/resources/*` request ever carries a window outside `ResourcesWindow`; W=2 h with the
toggle forced on ⇒ no over-ceiling request and no baseline line.
**Phase.** 3 (3.1/3.2/3.4/3.7 + the risk row).

### 🟡 AM-15 — Anti-static-import guard unenforceable as written; wrapper import shape unspecified (folds F8/F9)
**Finding.** Task 1.2's "a static import of chart.js reachable by an entry point fails the spec"
is an import-graph property, but the house pin mechanism is a source grep; there is **no eslint
config and no CI workflow** in the repo, and 1.1 forbids new dependencies — so no lint tier and no
CI gate exist. A wrapper in shared `components/charts/` that re-exports defeats a path allowlist
(an eager import of the wrapper carries chart.js into the initial set while the grep passes). And
the wrapper's own chart.js import shape (static vs dynamic) is unspecified — the two shapes have
different chunk topologies and leak paths; "the wrapper is imported ONLY by lazy components"
constrains importers, not the wrapper's own import.
**Evidence.** phase 1 task 1.2/1.7; `plan-overview.md:376`; repo-wide absence of eslint/CI files;
`metrics.bindings.pins.spec.ts:4-6`.
**Required change.** Specify the strongest enforceable guard: a grep pin over **resolved import
sites** — every `from 'chart.js'` / `import('chart.js')` occurrence must live inside
`src/app/pages/metrics/**` (where the wrapper lands after phase 5) — plus the B3 build-output
assertion as the only reachability proof, moved earlier than Phase 9 (folds into AM-2's co-located
guard). 1.7 fixes the import shape explicitly (chart.js import site inside `pages/metrics/**`, or
`await import('chart.js')` so the vendor is its own lazy chunk).
**Phase.** 1, 4, 9.

### 🟡 AM-16 — Supersede token doesn't cover range/baseline changes; tab-1 token discipline unstated (folds F4-D/F5-E)
**Finding.** The existing token bumps at request START only — a response is discarded only when a
newer request has started. A range change or toggle flip that does not itself start a request lets
an in-flight OLD-shape response paint under the NEW label (new range label over old range payload,
or a halved-resolution window after toggle-off) for up to one 15 s poll. Tab 1 gains three
concurrent request classes (8 s live poll, frame-driven window load, anomalies load) with no stated
token discipline.
**Evidence.** `charts-tab.component.ts:282-315`, `:192`; `phase3-plan.md:26,:31,:45`;
`phase5-plan.md:20,:44`; `timeline-tab.component.ts:48-53` (existing per-path tokens).
**Required change.** A change of window value or baseline toggle (a) triggers an immediate fetch
in the active tab and (b) invalidates the in-flight response via a **request-generation bump**
(not a start-bump); make "the baseline shares the tab's single series request" an explicit
invariant with a spec leg (it is what prevents the stale-window-pairing hazard); a window change
invalidates the window + anomalies loads but NOT the live strip's 8 s poll (the poll is not gated
by the frame's generation).
**Phase.** 3, 5.

### 🟡 AM-17 — Baseline midpoint anchor unspecified; three broken cites (folds F4-C/F4-F)
**Finding.** The split anchor is unstated; a client-clock midpoint mis-splits under clock skew
(60 s skew misassigns ~8 % of a 2 h window) — the response carries the server-anchored
`window.from_utc/to_utc`, which is the correct anchor. Also three broken cites: `envelope.py:386-388`
(file is 316 lines; epoch-points live at `provider.py:385-388` + `envelope.py:201-231`),
`resources.service.ts:57` (params at `:54-56`), `daemon/services/flusher.py:360-368` (no such file
— it is `resource_monitor/flusher.py`) with `config.py:811-827` holding metrics intervals, not the
resource tiers (`:925-931,:980-1001`).
**Evidence.** `phase3-plan.md:26-27`; `envelope.py:201-231`; `provider.py:385-388`.
**Required change.** Anchor the split at the RESPONSE's `window.from_utc/to_utc` midpoint (parsed
with the house `parseNaiveUtc`), never the client clock; add a spec leg with a faked client-clock
offset asserting the split point is unchanged; correct the four cites.
**Phase.** 3.

### 🟡 AM-18 — Interval meta line becomes false at exactly the 2 h option
**Finding.** The meta prints the door's `interval_s` (the OBSERVED sampler/publisher cadence, 15 s)
while the plotted points at `window_s=7200` are 30 s apart (480 ring points bucket-max-downsampled
to 240). Today's hardcoded 3600 s window makes the two coincide; the plan's own vocabulary change
creates the falseness.
**Evidence.** `charts-tab.component.ts:97`; `envelope.py:149-151`; `daemon/api.py:2510`;
`downsample.py:32-46`; `provider.py:378-388`; `decisions.md:123`.
**Required change.** The meta line reports the EFFECTIVE point spacing derived from the payload
(span ÷ point count), or explicitly labels the door value as observed cadence; spec leg at 2 h
asserting the printed interval equals the derived spacing.
**Phase.** 3, 6.

### 🟡 AM-19 — Tab-1 range-control scope unstated; window vocabulary not pinned to the frozen union (folds F5-C/F5-D)
**Finding.** The frame's range control governs only 2 of tab 1's 6 endpoints (`history`,
`anomalies`); `live`, `self-cost`, and the anomaly drill-down are window-independent — but 5.4
reads as tab-wide. And the manifest's `allowedWindows` agrees with the frozen `ResourcesWindow`
union only by hand; drift becomes a runtime 400 on the only live-lit tab.
**Evidence.** `phase5-plan.md:20-21`; `phase3-plan.md:16-18`; `resources.service.ts:41-88`;
`resources.models.ts:16`; `daemon/routers/resources.py:120-130`; `phase7-plan.md:18` (id pin has no
window equivalent).
**Required change.** State the control's per-panel scope (drives the timeline panel + anomalies
window; live strip / self-cost chip / drill-down are window-independent) and label it ("Timeline
range"); spec leg — a range change issues exactly one `/api/resources/history` request and none to
`/live` or `/self-cost`; acceptance that the custom tab's `allowedWindows` ⊆ `ResourcesWindow`
(compile-time or spec) so drift fails the build, not the live tab.
**Phase.** 2, 3, 5.

### 🟡 AM-20 — 403 predicate contradicts its own comment; copy has three homes and no SSOT (folds F8-3/F8-5)
**Finding.** A CSRF-guard 403 (non-null `rawCode`) is labelled `scope_insufficient` — a false
statement about the operator's session, contradicting the code's own doc comment. Copy: base copy
is a flat string `Record` that cannot hold the 3-part (cause/meaning/action) copy 8.1 wants; the
degraded suffix is duplicated in two components; phase 2 adds per-tab manifest copy competing with
the frame-level panel copy.
**Evidence.** `models/metrics.model.ts:426-428,:406-412,:384-392`;
`daemon/middleware/auth.py:610-615`; `overview-tab.component.ts:295`;
`charts-tab.component.ts:311`; `metrics.component.ts:50`; `phase2-plan.md` (2.1).
**Required change.** Narrow the predicate to `rawCode === 'SCOPE_INSUFFICIENT'` (case-insensitive)
+ a `forbidden_other` class ("rejected before the scope check"); copy SSOT = one structured const
per class in `metrics.model.ts` (cause/meaning/action/pointer); move both duplicated suffixes in;
state precedence — the manifest carries per-tab **variants** of frame states, never a parallel copy
source; pin both 403 branches in the spec.
**Phase.** 8, 2.

### 🟢 AM-21 — Green-header-dot fix couples a page-local posture to the GLOBAL health indicator
**Finding.** 8.4 recolors the app-shell dot, which is bound to `/api/health` — the DAEMON's health.
The daemon is healthy when the door is deliberately fail-closed; a red dot is a false global
statement leaking one page's state into every page.
**Evidence.** `phase8-plan.md:24`; `app.html:92-94`; `app.ts:103,:780-785`.
**Required change.** Keep the dot bound to `/api/health`; render the door posture as a page-scoped
chip in the metrics header; drop 8.4's shell-level mechanism (which the plan never specifies).
**Phase.** 8.

### 🟡 AM-22 — The /resources sweep misses the Playwright spec leg, the gate config, the README, and the operator docs (folds F7-C/F10-4)
**Finding.** The sweep (`frontend/src frontend/e2e`, three extensions) cannot see
`frontend/playwright.resources-gate.config.ts` (outside both dirs) — the very file 7.7 names; the
gear-menu e2e leg breaks at NAVIGATION (7.3 removes the entry) and 7.7's acceptance covers only the
config; `e2e/README.md` (.md) is outside the includes; `.scss/.json/.mjs` are missed; and operator
docs (`docs/runbooks/resource-cost-control.md`, `resource-hygiene-operator-checklist.md`) keep
directing operators at the retired route.
**Evidence.** `phase7-plan.md:21-23`; `frontend/e2e/resources-gate.spec.ts:24,:189`;
`frontend/e2e/README.md:42`; the two runbooks.
**Required change.** Widen the sweep to `frontend/` (excluding `node_modules`/`dist`) with a
per-extension include list plus `docs/`; classify every hit; add a docs task updating both runbooks
to `/metrics?tab=resource`; 7.7's acceptance requires the SPEC's gear-menu leg and URL assertion
re-pointed (not only the config) and an explicit no-playwright-config-targets-retired-route
assertion.
**Phase.** 7.

### 🟡 AM-23 — Three zero-hit greps are vacuous as written (no pattern, scope, or allowlist)
**Finding.** 2.8 ("zero references to the removed input"), 4.10 ("0 hits for a literal 3600"), 5.4
("0 hits in the resource module") specify neither pattern nor path set nor excludes — an over-broad
pattern fails, an over-narrow one passes vacuously; the house residue-gate rule requires an
allowlist row PLUS a matching `:(exclude)` pathspec for every intentional-left surface.
**Evidence.** `phase2-plan.md:24`; `phase4-plan.md:28`; `phase5-plan.md:21`; house residue-gate
ZERO rule.
**Required change.** Pin the exact command (pattern + path set + excludes) for all three and list
the allowlist rows.
**Phase.** 2, 4, 5.

### 🟡 AM-24 — Governance amendment misses 2 of the 4 "zero FE deps" sites and leaves falsified statements standing (folds F19)
**Finding.** The binding source requires same-commit amendment of four live sites; phase 1.4 covers
pack Leg 7 and `metrics-promote-disclosure.md:122` but misses
`.agents/shared/planning/metrics-agent-tools/stage-prep-checklist.md:28` and
`central-metrics-v1/stage-prep-checklist.md:102` + `central-metrics-v1/phase6-plan.md:23`. It
instead amends `metrics-fe-charts.md` zero-dep sites not in the binding four — while leaving that
doc's `:126-128` PATH pointing at the directory phase 4 deletes and its `:130-131` "feature-frozen
… Zoom, brush, crosshair and gradients are explicitly OUT" sentence, which phase 4.6 (crosshair)
and ADJ-ii (nearest-point hover) falsify.
**Evidence.** `resource-refactor-architecture.md:345,:390`; `charting-engine-decision.md:20,:41`;
phase 1 task 1.4; `docs/architecture/metrics-fe-charts.md:126-131`.
**Required change.** Amend or record-historical the two missed checklist sites (the
`CHANGELOG.md:141` treatment); amend `metrics-fe-charts.md`'s path reference and its
feature-frozen/crosshair-OUT sentence (not just the zero-dep claim).
**Phase.** 1 (+4 for the path).

### 🟡 AM-25 — Phase-1 exit criteria contradict phase-1 tasks
**Finding.** The exit says "package.json diff = exactly 2 added lines" while 1.3/IMPL-05 exist to
replace count assertions with by-name expected sets (a transitive bump must not break it); the exit
also says no `agents/` file is touched while task 1.8 edits `.agents/tester/PACKS.md`.
**Evidence.** `phase1-plan.md:50-65`; tasks 1.3/1.8; `decisions.md` IMPL-05.
**Required change.** Replace the 2-line criterion with the by-name expected-set assertion; exempt
`.agents/tester/PACKS.md` explicitly from the "agents/ untouched" clause (or move 1.8's
registration to phase 9).
**Phase.** 1.

### 🟡 AM-26 — Helper-port inventory under-counts; "specs relocated" is false for component assertions (folds F4/F5)
**Finding.** Task 4.1 names 6 helpers + "equivalents (:108-271)" — the range holds **12** exports
including `buildChartStepPath` (the geometric half of counter-honesty, spec-pinned), and 4.2's
checklist lacks STEP rendering. And the two engines' different component APIs mean moved
component-level specs (8 timeline assertions + path/coord/tick/value-label assertions) are
REWRITTEN specs, not relocated ones.
**Evidence.** `svg-line-chart.component.ts:108-271`; `svg-line-chart.component.spec.ts:139-171`;
`svg-timeline.component.spec.ts:135-233`; phase 4.1/4.2.
**Required change.** 4.1 enumerates all 12 exported symbols by name; 4.2's checklist gains
"counter STEP rendering"; 4.1 requires a per-assertion disposition table (port / re-point /
drop-with-reason) against the consolidated component's API, and that table becomes a phase-4 exit
criterion.
**Phase.** 4.

### 🟡 AM-27 — The resource timeline is re-pointed twice across phases 4–5, unstated
**Finding.** 4.9 deletes `pages/resources/svg-timeline/` BEFORE 5.1 relocates `pages/resources/` →
`pages/metrics/resource/`; 4.10 re-points the timeline to the registry, then IMPL-04/phase 5
re-point it again to the panel role — the double migration is never stated.
**Evidence.** Phase 4 tasks 4.9/4.10; phase 5 task 5.1; `decisions.md` IMPL-04.
**Required change.** State the two-step migration explicitly (registry in 4, panel in 5), or
reorder 4.9 to delete the engine only after 5.1's `git mv`.
**Phase.** 4, 5.

### 🟡 AM-28 — The `anyComponentStyle` budget (8 kB warn / 24 kB ERROR) is never considered
**Finding.** The consolidated renderer (one primitive + five mini extensions + toggles + markers +
legend + overlay + baseline + last-value), the Chart.js wrapper, the house anomaly plugin, and the
hidden data table are all new/expanded inline-style surfaces; the 24 kB error cap hard-fails the
production build. B6 records warnings only.
**Evidence.** `angular.json` (anyComponentStyle budgets); `svg-line-chart.component.ts:372`
(existing styles block); phase 9 B6.
**Required change.** B6/9.7 records the per-component `anyComponentStyle` figure; phase-4's exit
criterion bounds the consolidated renderer's inline styles under the 8 kB warning.
**Phase.** 4, 9.

### 🟡 AM-29 — Fence scrub under-inclusive vs the house family rule; config.yaml never inspected; "uptime byte-identical" unsatisfiable (folds F9-5/F9-6)
**Finding.** P6's per-var scrub misses the house FAMILY rule (prefix `POSTGRES_*` **and `PG*`**
— `PGPASSWORD` is a known ambient vector — plus `BLUEPRINT_*`, suffix `*_API_KEY`) and adds
`DAEMON_PORT` (inert, see AM-5); P1–P7 never inspect `config.yaml`, the second config source (port
interpolation + DB stanza; the untracked `data/ensemble.json` pin is checked but config.yaml is
not). 9.10's "uptime … byte-identical" cannot hold — uptime is monotone.
**Evidence.** `plan-overview.md:210`; house family-scrub rule
(`tests/integration/test_empty_guard_checkpoint_roundtrip.py:114-133`); `config.yaml:112-113`;
`phase9-plan.md:32,:61`.
**Required change.** Widen P6 to the family rule (+ `DAEMON_PORT`); add **P8**: assert
`config.yaml`'s effective port/database AND read the dev daemon's actual DB back from the running
daemon (not from a file); reword 9.10 to "symlink target + `state.json` byte-identical pre/post,
live uptime MONOTONE (no restart)".
**Phase.** 9, plan-overview §6.1.

---

## Verified sound (coverage statement — mechanics confirmed on evidence, not implied)

1. **Engine consolidation is achievable**: newer engine has exactly 2 consumers (metrics charts tab
   + its pins spec disk-read), older exactly 1 (resources timeline tab); zero non-metrics
   consumers across all 30 exported symbols; both engines' pure helpers are exported and
   unit-asserted — porting, not rewriting, is right.
2. **Lazy-route seam available**: `/metrics` and `/resources` are both `loadComponent`-lazy; the
   dev configuration carries no budgets (the P5-M11 note is consistent).
3. **Range vocabulary ↔ door**: 15m/30m/1h/2h map 1:1 to real `window_s`; none clamped (2 h IS the
   ceiling); visible disabled options are strictly better than the door's silent clamp.
4. **No-forged-cursor pin correctly targeted**: the FE client has no cursor parameter at all
   (client-side minting structurally impossible today); the door's cursor cites are exact.
5. **Single-fetch 2W discipline** eliminates the mismatched-window-pairing hazard *provided* the
   shares-one-request clause is enforced (AM-16); ring retention (7200 s) backs the 2 h baseline —
   the plan's "eviction edge" statement is accurate.
6. **Custom slot honesty**: the binding source's own §1.3 scopes config-not-code to central tabs;
   the resources door's cursorless, bucketless, 4-value-window shape is forced through nothing.
7. **Anomalies panel grounded** in the real endpoint shape; tier-vs-option copy accurate
   (`_resolve_resolution` auto-falls to minute tier; tiers back the ladder, not picker options).
8. **Redirect mechanism choice right**: no loop possible (paths differ), no `canActivate` fight,
   `UrlTree`-from-`canMatch` supported in Angular 21, house-consistent idiom; the tab-id agreement
   pin (7.2) is real; external bookmark residue honestly handled via the permanent redirect.
9. **Live-contact posture SOUND**: no exit criterion depends on live state; 0-writes/0-requests
   assertions present; all four house fence rules encoded (`env -u POSTGRES_*` no-op called out,
   the boot-sweep pin explicit, `data/ensemble.json` outranking checked, scratch
   `ENSEMBLE_DATA_DIR`). The fence defects found (AM-5/AM-29) are mechanics, not breaches.
10. **Phase 0 properly FIRST** with a machine-checkable, non-vacuous exit (only the §8 placement
    check is missing); deep-link cases enumerated with a no-navigation-storm assertion; redirect
    fixtures enumerated; the registry's 3-layer fallback forbids silent downgrade with a
    planted-violation leg; the never-forge-cursors negative pin; unit mapping refuses inference;
    the data-gap annotation forbids restart/deploy claims with a planted-violation leg.
11. **Phase-8's factual premises are TRUE against the tree** (two banners, the contradictory
    suffix, "No families are registered.", the degraded badge path, the empty-panel suppression —
    which 2.9's preservation clause correctly protects).
12. **The door's scope contract, bearer path, and runbook pointer all resolve** (maintenance:read
    OR metrics:read; DB-local bearer; `issue-token --scopes` exists; `docs/runbooks/oauth2-auth.md`
    exists); the routed register is a real 14 items; the 9.7 threshold (decimal 6 MB) is correct —
    only the baseline is wrong.

---

## Escalations (for the leader — not folded)

**E-1 — The research digest's MEASURED ledger has a wrong-tree provenance problem.** The bundle
baseline (5,909,018 B) almost certainly was measured on the MAIN CHECKOUT's FE (v0.27.8-era), not
the lane tree (v0.28.9 FE ≈ 5,115,820 B) — the same provenance trap `research-findings.md` §0
itself flags, and the internal arithmetic slip (5,909,018 + 89,982 = 5,999,000 ≠ 6,000,000)
corroborates that the number set was never self-checked. AM-2 fixes this one number; the process
concern is that **every other MEASURED figure a phase exit criterion depends on should be
re-derived at lane base before its phase freezes**. Escalated rather than folded because it spans
the whole digest's trust, not one figure.

**E-2 — Confirmed with review evidence: the dashboard ships mostly DARK on live by default, and
only AM-1 keeps even tab 1 alive.** Auth OFF (the current live posture) ⇒ central door 503 for
everyone ⇒ the default live experience is the empty-state, with tab 1 (resources, 200-open) the
only live-lit surface — and as the plan is written, the frame-level auth panel blanks that tab too
(AM-1). The plan accepts the dark-by-default posture under D-2 and defers auth enablement to the
user — that is ratified. Escalated as an awareness item: carry it into the release narrative so
the flagship T1 page's mostly-dark live appearance is an expected outcome of the user-owned auth
timeline, not a perceived regression.

---

## Analysts' unverified items (carried honestly)

- The 5,909,018 B baseline's producing tree is inference, not proof (only v0.28.7/8/9 dists exist
  on disk; the lane-base fresh build awaits AM-2's re-measure — no `node_modules` in the worktree).
- Analyst A cited the builder's lazy-chunk semantics and the 89,982 B figure from the
  research-findings MEASURED ledger rather than re-deriving them; analyst C's independent dist
  measurement supersedes the baseline and confirms the semantics — the mechanism finding stands.
- Analyst C verified `config.yaml`'s port interpolation but not its DB stanza (covered by AM-29's
  P8 assertion).
- Live-posture facts (503-for-everyone / 200-open) were taken from the dispatch brief + the FE
  decode constants, not probed — consistent with the read-only mandate and the recorded
  `promote_state_2026_11_03` live capture.

*No plan files were modified by this review; amendments ride this document. The planner folds.*
