# Phase 2 evidence — the tab manifest + the 7-tab shell (T1, wi-32a99618)

**Lane:** `feature/fe-metrics-dashboard` · **Phase base:** `e529d5823` (phase 1 landed) ·
**Freeze commits:** `33e2ffc00` (the manifest) · `4be56a5da` (the door timeout) ·
`1269c3820` (the shell + the tab bodies) · the pack/evidence commits.

**Scope:** FE-ONLY. `frontend/` + `.agents/`. No `daemon/`, no migration, no `scripts/upgrade`.
`agents/` untouched — the prompt-artifact baseline still verifies **161/161**.

---

## 1. E-1 — the 16-family census, RE-DERIVED at this phase's freeze

The census is re-derived from the registry SSOT — the two independent registrations agree, and
the manifest's union matches them exactly.

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard
grep -rn "family=\"" daemon/services/metrics_query/wiring/w*.py \
  | sed 's/.*family="\([^"]*\)".*/\1/' | sort -u
```

```
F0.3 F1 F10 F11 F12 F13 F17 F18 F21 F3 F4 F5 F6 F7 F8 F9
count = 16
```

```bash
sed -n '/^REGISTERED_FAMILIES/,/^)/p' daemon/services/metrics_query/wiring/adjudication.py
```

```
REGISTERED_FAMILIES: tuple[str, ...] = (
    "F0.3", "F1", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
    "F10", "F11", "F12", "F13", "F17", "F18", "F21",
)
```

**The manifest's bindings, re-checked against the census:**

```
census (registry SSOT)      : ['F0.3','F1','F10','F11','F12','F13','F17','F18','F21','F3','F4','F5','F6','F7','F8','F9'] count = 16
manifest REGISTERED union   : ['F0.3','F1','F10','F11','F12','F13','F17','F18','F21','F3','F4','F5','F6','F7','F8','F9'] count = 16
manifest BOUND ids          : ['F1','F10','F11','F12','F13','F17','F3','F4','F5','F6','F8','F9'] count = 12
manifest PLANNED-unbound ids: ['F0.3','F18','F21','F7'] count = 4
census == manifest union    : True
bound + planned == census   : True
bound ∩ planned (must be ∅) : []
```

**The two taxonomies are NOT conflated.** The census above is the **registered** partition (what
the door can serve). The **availability** partition (LIVE/DARK — what the tabs bind) is a RUNTIME
fact read from the wire; the manifest carries no availability field at all (the manifest spec
asserts it). The bracketed families are bound NOW and are dark today for the producer-side
reasons below — the reason each one is dark comes from the door's own derivation
(`daemon/services/metrics_query/availability.py:65-98`):

| family | `source_idiom` (the registration) | dark because |
|---|---|---|
| F1, F3 | `wedge_capture` | `capture_off` — `facts.capture_active` is unwired (**R-ii**) |
| F6 | `postgres_census` | `census_not_running` / `census_gated` — the census polarity is inverted (**R-i**) |

```bash
grep -n "family=\"F1\"\|family=\"F3\"\|family=\"F6\"" -A6 daemon/services/metrics_query/wiring/w1.py | grep source_idiom
```

→ `F1 → wedge_capture`, `F3 → wedge_capture`, `F6 → postgres_census`.

---

## 2. The bundle guard — GREEN at the freeze

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend \
  && ./node_modules/.bin/ng build --configuration production 2>&1 | tee /tmp/fe-t1-p2-prod.log
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend \
  && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js \
       dist/frontend/browser /tmp/fe-t1-p2-prod.log
```

```
(a) B1 — the numeric initial-total delta
    initial_total_bytes = 5909053
    baseline_bytes      = 5909018
    delta               = 35 B (limit |2048| B)
    initial_file_count  = 36
    measurement_tool    = 5909053 (cross-check)

(b) B2 — the chart.js marker scan over the initial set
    initial_chunks_scanned = 36
    marker_hits            = 0

(c) B3 — the import shape + import site
    chart_js_import_sites  = 1
    static_sites           = 0
    outside_metrics_tree   = 0
      src/app/pages/metrics/charts/chart-panel.component.ts: dynamic=1 static=0

(d) B6 — the anyComponentStyle warning SET (not a count)
    baseline_set_size = 9
    measured_set_size = 9
    added   = 0
    removed = 0

RESULT: GREEN (B1 + B2 + B3 + B6)
```

The warning SET is captured mechanically from the build log:

```bash
grep -E 'scss exceeded maximum budget' /tmp/fe-t1-p2-prod.log | sed 's/^▲ \[WARNING\] //' | sort | wc -l
```

→ **9** — byte-identical to the frozen baseline set (0 added / 0 removed).

### 2.1 The route measurement (task 2.13's marker constant)

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend \
  && node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js \
       dist/frontend/browser
```

```json
{
  "initial_total_bytes": 5909053,
  "initial_file_count": 36,
  "error_budget_bytes": 6000000,
  "headroom_bytes": 90947,
  "index_html_bytes": 9257,
  "whole_initial_payload_bytes": 5918310,
  "lazy_chunk_count": 53,
  "lazy_bytes_total": 1714492,
  "route_chunk_locator": "route-key",
  "route_chunk_key": "ensemble-metrics-route-v1",
  "route_chunk_resolved_to_exactly_one": true,
  "metrics_route_chunk": "chunk-H2NJDIQB.js",
  "metrics_route_chunk_bytes": 39332,
  "metrics_route_fetch_set_bytes": 591467,
  "largest_lazy": "chunk-N36B3UOO.js",
  "largest_lazy_bytes": 157141,
  "second_largest_lazy": "chunk-37F72IHG.js",
  "second_largest_lazy_bytes": 135690,
  "pre_retirement_resources_route_chunk": "chunk-MQAZWIUE.js",
  "pre_retirement_resources_route_chunk_bytes": 38895
}
```

| figure | lane base (phase 1 freeze) | phase-2 freeze | delta |
|---|---|---|---|
| `initial_total_bytes` | 5,909,018 | 5,909,053 | **+35 B** (B1 limit 2 kB) |
| `route_chunk_locator` | `legacy-content-marker` | **`route-key`** | the phase-2 marker constant resolves it |
| `route_chunk_resolved_to_exactly_one` | true | **true** | — |
| `metrics_route_chunk_bytes` | 27,614 | 39,332 | **+11,718 B** (this phase's FE code) |
| `metrics_route_fetch_set_bytes` | 496,498 | 591,467 | **+94,969 B** — see the caveat below |

**The caveat (measured, not asserted).** The phase-1 freeze was reproduced EXACTLY by rebuilding
the phase-1 tree (`git checkout e529d5823 -- frontend/` + a production build): `initial_total_bytes
= 5,909,018`, `metrics_route_chunk = chunk-TLUSWWAH.js (27,614 B)`, `metrics_route_fetch_set_bytes
= 496,498` — so the two figures above are apples-to-apples. The fetch-set member lists were then
diffed mechanically:

| | phase-1 freeze | phase-2 freeze |
|---|---|---|
| route chunk | chunk-TLUSWWAH.js (27,614) | chunk-H2NJDIQB.js (39,332) |
| direct static deps | 9 chunks / 468,884 B | 11 chunks / 552,135 B |
| **the new dep** | — | **chunk-HU2ZQP43.js (83,216 B) = the Angular ROUTER chunk** |

`chunk-HU2ZQP43.js` **is in the initial 36-file set** (it carries `ActivationStart`,
`GuardsCheckStart`, `RoutesRecognized`, `routerOutlet`) — it is downloaded at startup. The metrics
route now references it **directly** because the shell injects `Router` + `ActivatedRoute` for the
`?tab=` deep link (the house pattern, `snapshots.component.ts:376`). So the metric re-counts an
already-loaded file: the **genuine** new payload of the `/metrics` route is the route chunk's
**+11,718 B**, not +94,969 B.

> **Phase-4/9 decision (routed, NOT taken here).** The B4 threshold in `bundle-baseline.json`
> ("the `/metrics` route fetch set grows by ≤ 65,536 B") is measured over a set that now includes
> an initial-set chunk. The honest restatement is to measure the **lazy-only** portion of the
> route fetch set. That is a plan-level wording change; this phase records it and does NOT make
> it.

---

## 3. The pinned residue check (task 2.8 / AM-23)

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard \
  && grep -rn "\bactive\b" frontend/src/app/pages/metrics/families/ frontend/src/app/pages/metrics/metrics.component.ts
```

```
(no output — exit status 1: zero hits)
```

The scope covers the whole `families/` directory — **including the spec file**, so the word it
hunts does not appear in `families-tab.component.spec.ts` either (not even in a comment). The
check is ALSO pinned in-process by `metrics.component.spec.ts` ("the pinned residue check returns
ZERO hits"), so a regression is caught by jest, not only by the shell command.

---

## 4. The jest census (mechanical, per file)

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard/frontend \
  && CI=1 ./node_modules/.bin/jest --ci --silent --runInBand <file>
```

| spec file | nodes | note |
|---|---|---|
| `src/app/pages/metrics/tabs.manifest.spec.ts` | **30** | NEW |
| `src/app/pages/metrics/tabs/central-tab.component.spec.ts` | **18** | NEW (6 of them loop over the six central tabs — the W-10 exit criterion) |
| `src/app/pages/metrics/metrics.component.spec.ts` | **44** | was 26 |
| `src/app/pages/metrics/families/families-tab.component.spec.ts` | **13** | was 7 |
| `src/app/services/metrics.service.spec.ts` | **16** | was 9 |
| `src/app/models/metrics.model.spec.ts` | **32** | was 30 |
| **the whole metrics FE surface** | **226 over 12 suites** | 0 failed |

The scoped type-check is clean: `tsc --noEmit -p tsconfig.metrics-fe.json` → exit 0, and
`tsc --noEmit -p tsconfig.app.json` → exit 0.

---

## 5. The deep link — the four cases

`metrics.component.spec.ts` drives the shell with a replaying `ActivatedRoute` stub and a
`Router` stub that replays the parameter back (so a rewrite is observable exactly as the real
router would make it).

| case | the parameter | the selected tab | the navigations |
|---|---|---|---|
| known | `?tab=mcp` | `mcp` (index 6) | 0 |
| unknown | `?tab=no-such-tab` | `resource` (index 0) | 1 (one `replaceUrl` rewrite) |
| legacy | `?tab=overview` | `resource` (index 0) | **1 — no storm** |
| absent | (none) | `resource` (index 0) | 0 |

The rewrite is `replaceUrl: true` + `queryParamsHandling: 'merge'`, so the parameter becomes known
and a known parameter writes nothing — the loop terminates after one write.

---

## 6. The re-pointed assertions (task 2.9) — none was silently deleted

| the retired assertion | its new home |
|---|---|
| the shell carries exactly THREE tabs | the shell carries exactly SEVEN manifest tabs |
| the production registry declares exactly three entries | the production source drives the list from the manifest (no local registry) |
| the template iterates `sections` | the template iterates `tabs` |
| one tab label per registry entry (3 labels) | one tab label per manifest entry (7 labels) |
| the chart-primary projection: chart-only families | the SAME assertion, in `tabs/central-tab.component.spec.ts` |
| the chart-primary projection: the counter's STEP mode | the SAME assertion, in `tabs/central-tab.component.spec.ts` |
| the chart-primary projection: the selector ceiling | the SAME assertion, in `tabs/central-tab.component.spec.ts` |

**The file names `families-tab.component.ts` / `families-tab.component.spec.ts` are KEPT** even
though the component is no longer a tab: renaming them would move the registered pack's D1 spec
path (`test/packs/metrics_fe_dual_side_test.sh`, outside this phase's FE-only fence). The class
was renamed to `MetricsFamilyCatalogComponent`; the file-name debt is routed.

---

## 7. The prompt-artifact baseline (161/161)

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard \
  && /home/nea/ensemble-src/.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check
```

```
PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256
```

```bash
cd /home/nea/ensemble-src-wt-fe-metrics-dashboard && sha256sum -c agents/.prompt-artifacts.sha256 | grep -c ": OK"
```

→ **161** (0 mismatches).

---

## 8. The deviations and the open items

1. **`families-tab.component.*` keeps its file name** (the class was renamed) — the file rename
   needs a `test/packs/` list update, which the FE-only fence forbids in this phase. **Routed.**
2. **The registered `metrics_fe_dual_side` pack's D1 list does not carry the two new spec files**
   (the list is hard-coded in `test/packs/`). The new specs are registered as their own pack row
   (`t1_fe_phase2`). **Routed to the lane owner.**
3. **The `copyVariant` is declared and asserted, but it does not yet change any rendered copy** —
   the copy SSOT (one structured const per class) is phase 8's work (IMPL-18); phase 2 fixes the
   precedence rule and the manifest's role in it, as the task states.
4. **The `timeout` door class was added to the five-class table** (phase 8 widens the table to the
   closed ten-class union). The class + its copy + the decode are landed; the per-class empty
   state (which classes carry a Retry) is phase 8's.
5. **The route fetch set grew +94,969 B by the metric, of which +83,216 B is an already-initial
   chunk** — see §2.1. The B4 wording is a phase-4/9 decision.
6. **The "currently dark" half of the bracketed-families acceptance is NOT FE-assertable** — it is
   a runtime fact read from the wire. The FE half (the BINDING) is asserted; the dark half is
   recorded in §1 from the registration metadata + the availability derivation.
