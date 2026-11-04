# Council fold record — FE-only T1 metrics dashboard (wi-32a99618)

**Authority:** `code-review.md` (agentic councilor — APPROVE-WITH-ADVISORIES,
0C/0W/4A) and `code-review-coding-model.md` (coding councilor —
APPROVE-WITH-ADVISORIES, **1 CRITICAL**/1W/6A). Both read in full before any
edit; both were re-read against the tree at every disposition below.

**Tree:** worktree `/home/nea/ensemble-src-wt-fe-metrics-dashboard`, branch
`feature/fe-metrics-dashboard`. Head at fold start `7d50e4a73` (the agentic
review commit on top of `aa3f5c15f`). **FE-only; no daemon, DB, migration or
live-write contact. NO threshold changed** (`bundle-baseline.json` is
byte-identical to the pre-fold file — `git diff` empty for it).

**Verdict carried:** the fold closes the one CRITICAL, remediates the WARNING,
and disposes of every advisory — 8 rows covering all 10 advisory IDs:
**6 folded** (agentic-1, coding-A-2, coding-A-3, agentic-2, coding-A-6,
coding-A-1/agentic-4) · **1 routed** (coding-A-5 → tidier) · **1 noted**
(agentic-3 + coding-A-4 are the same finding). Nothing in the D-1 contract,
the D-2 posture, or D-3's redirect moved.

---

## 1. C-1 — the overlay shadowed the anomaly markers (FOLDED)

### The defect (confirmed, then reproduced under a REAL pointer)

`house-svg-renderer.component.ts` painted the full-plot hover overlay
(`<rect class="house-overlay">`, `fill="transparent"`, CSS
`pointer-events: all`) **after** the anomaly-marker group. SVG hit-testing
delivers the pointer to the **topmost** hit-target, and `pointer-events: all`
captures regardless of paint — so the overlay was the topmost target across
the whole plot, and the marker `<g>` (a **sibling**) never received a real
pointer. The unit leg passed anyway because jsdom performs no SVG hit-testing
and the leg dispatches the click straight at the element.

Reproduced at the pre-fix ordering by the NEW browser leg (see §1.3): at the
marker's rendered centre, `document.elementFromPoint` answered
**`rect.house-overlay`** — the exact predicted signature.

### 1.1 The fix — paint order, not pointer plumbing

```
BEFORE (pre-fold, template order)            AFTER (folded)
──────────────────────────────────           ──────────────────────────────────
<path house-series-area>  × N                <path house-series-area>  × N
<path house-series-line>  × N                <path house-series-line>  × N
<circle house-point>      × N                <circle house-point>      × N
<circle house-last-value> × N                <circle house-last-value> × N
<g house-anomaly-marker>  × N   ← clickable  <rect house-overlay>       ← hover target
<circle house-hover-point> (hover)           <g house-anomaly-marker>  × N ← CLICKABLE
<rect house-overlay>            ← TOPMOST    <circle house-hover-point> (hover)
<text house-empty>                           <text house-empty>
```

**Chosen remedy: (A) move the `<rect>` before the marker loop** — the
council's first option. Why not (B) `pointer-events: none` + listeners on
the plot `<g>`/`<svg>`: with the overlay non-hit-testable, the empty plot
area has no painted descendant, so a `pointermove` over blank plot would
never reach an ancestor listener — the nearest-point hover (ADJ-ii's
extension, which the council required to keep working) would degrade to
"only over drawn geometry". Re-ordering keeps **both** behaviours: the
overlay still sits above the series (a pointer over a line reaches it) and
below the markers (a pointer over a marker reaches the marker).

**CSS diff: NONE.** `.house-overlay { pointer-events: all }` and
`.house-marker-hit { pointer-events: all }` are unchanged; the fix is purely
document order. A template comment now states the invariant and warns that
the template is a TS template literal (no backticks in template comments —
the first edit attempt broke the build on exactly that, caught by `tsc`).

### 1.2 The unit pin (jsdom can assert paint order)

New leg in `house-svg-renderer.spec.ts`: *"paints the hover overlay BENEATH
the anomaly markers (a real pointer must reach them)"*. It asserts the
document-order relation **both ways**:

- the overlay **follows** the series line (`HOUSE_SERIES_LINE` precedes it) —
  else the hover listener is unreachable over a line;
- the overlay **precedes** the marker group — else the marker is shadowed.

**Mutation-verified (executed, then restored):** with the overlay moved back
after the markers, the leg is **RED** at the predicted assertion
(`compareDocumentPosition … DOCUMENT_POSITION_PRECEDING`); restored → green.
Renderer suite `3 suites / 84 tests`, all green (+1 node vs the review's
record — the new leg).

### 1.3 The Playwright REAL-POINTER leg (green-at-fix MANDATORY — green)

New leg in `frontend/e2e/resources-gate.spec.ts` (the resources gate config,
`playwright.resources-gate.config.ts`, port 14202 — the module's own gate):

```
[C-1] a REAL pointer reaches an anomaly marker on the house-SVG fallback
      and opens the drill-down
```

How it works:

1. **The fallback is entered for real** — `page.addInitScript` makes
   `HTMLCanvasElement.prototype.getContext('2d')` throw. Chart.js 4.6.1 calls
   `platform.acquireContext` **inside** the constructor, so the throw
   propagates out of `new Chart(...)` → the panel's `mount()` catch →
   `onMountFailed` → the house SVG **with its badge**. Nothing is stubbed in
   the chart layer (a `null` context, by contrast, does NOT throw in 4.6.1 —
   it `console.error`s and returns — which is why the first attempt never
   reached the fallback; recorded so a future author does not repeat it).
2. **Viewport 1600×1200 + `scrollIntoViewIfNeeded`** — raw mouse coordinates
   are viewport coordinates; below the fold both `page.mouse.*` and
   `elementFromPoint` no-op (the second thing the first run taught us).
3. **(a) the hover extension still works** — a real `page.mouse.move` into
   an empty patch of the plot publishes `[data-test="house-hover-point"]`.
4. **(b) a REAL pointer at the marker's rendered centre** — the
   `.house-marker-hit` circle's bounding-box centre, then
   `elementFromPoint` must answer a `house-marker*` element and **never**
   `house-overlay`, then `page.mouse.click(x, y)`.
5. **The drill-down opens** — `[data-test="drilldown-panel"]` visible, kind
   `memory`, severity `high`, and the evidence round-trip renders
   (`[data-test="contributors"]`), i.e. the click travelled the whole
   marker → consumer → request path.

**Results (executed):**

| run | ordering | outcome |
|---|---|---|
| green-at-fix (mandatory) | folded | **PASS** — full gate `4 passed (38.3s)` |
| red-at-base-class (optional, taken) | overlay restored to after-markers | **FAIL** at `expect(topmost).not.toContain('house-overlay')` — received `"rect.house-overlay"` |

The gate's other three legs (AC8 gear-menu/live strip, AC8 deep-link, AC8
degraded-200) run alongside and stay green; `metrics-gate` (4 legs) also
re-ran green (unchanged).

---

## 2. W-1 — the prod credential at rest in `/tmp` (REMEDIATED)

Full record with the corrected fence pattern, the pre-deletion inventory,
the per-class disposition table, the credential-scan hits and the relocation
proof: **`phase9-evidence.md` §9.10** (added by this fold). Summary:

- **(a) corrected pattern**: the fence helper SOURCES the five `POSTGRES_*`
  values from the install's own `.env` (verified mode `0600`, exactly one
  occurrence of each key — so it *is* the five values the old helper inlined)
  inside a subshell, maps them to libpq names for ONE `psql` command, and
  never puts the secret on argv or in any lane-written file.
  `<the prod password>` appears in no artifact of this fold.
- **(b) sweep — inventory first, then remove.** 50 top-level entries / 74
  files / ~3.1 MB. **25 files RELOCATED** (`pixels/`, committed — explicit
  leader override of the "binary evidence stays out" convention, because both
  reviews consume it), **49 files REMOVED**, **0 kept**. `lsof` was empty
  before the sweep (no standing process), and nothing needed keeping.
- **(c) verification**: `ls -d /tmp/fe-t1-*` → no match; a `/tmp`-wide
  credential scan (`PROD_PW=` / `PGPASSWORD=<value>` / `postgres://user:pass@`
  / `Bearer <token>`) → zero hits from this lane; the only other `Bearer`
  strings were the `www-authenticate: Bearer` scheme word and a runtime
  template in a capture driver (no literal token).
- **No credential rotated** — user-owned, untouched.

---

## 3. Advisory dispositions — every one recorded

| ID | Doc | Disposition | Where |
|---|---|---|---|
| **agentic-1** (uniqueness pin is path-scoped) | agentic | **FOLDED — widened** | `house-svg-engine-uniqueness.spec.ts`: the offender pattern is now BASENAME-anchored (`^svg-line-chart(.|$)` / `^svg-timeline(.|$)`) over the whole `src/app` walk — extension-agnostic, directory-agnostic — exactly the reviewer's preferred form. Mutation-proven: a probe engine planted at `src/app/pages/__mutation-probe/svg-line-chart.component.ts` (no `charts` segment) is **RED** under the widened pin and would have been **green** under the old one; probe removed, `2/2` green. |
| **coding-A-2** (`statFamilyIds` optional array) | coding | **FOLDED** | `tabs.manifest.ts`: `statFamilyIds?: NonEmpty<MetricsRegisteredFamilyId>` (the file's own helper name), matching `familyIds`; the empty-array sentinel is now unrepresentable. Consumers (`?? []` at two call sites, one spec projection) type-check unchanged; `tsc -p tsconfig.metrics-fe.json` rc 0. |
| **coding-A-3** (137 vs 141) | coding | **FOLDED** | `phase7-evidence.md:542` summary row → **141** (matching §6, §6.1 and both reviewers' re-runs). |
| **agentic-2** (35-JS wording) | agentic | **FOLDED** | `phase9-evidence.md` §9.8 B2 row → "0 hits across the **36-file** initial set (35 JS + 1 CSS)". |
| **coding-A-6** (duplicated B4 fragment) | coding | **FOLDED** | `plan-overview.md` §6.3 row B4: the second copy dropped (phrase now occurs once in the file). |
| **coding-A-1 / agentic-4** (`payload.daemon.pid` unguarded) | both | **FOLDED (optional-taken)** | `live-tab.component.html:75` → `payload.daemon?.pid ?? '—'` — one-character symmetry, pre-existing and unreachable through the real door; zero behavioural change, asymmetry gone. |
| **coding-A-5** (duplicated banner colours) | coding | **ROUTED — owner: the tidier** | `charts-tab.component.ts:138` and `overview-tab.component.ts:118` still carry inline `#2a1414/#7f1d1d/#fca5a5`; they should collapse onto `MetricsDoorStateComponent`'s CSS custom properties. Not folded here (both reviewers classed it tidier work; it touches two retired surfaces). **Owner: tidier lane (this lane's tidier pass, next commission).** |
| **agentic-3 / coding-A-4** (brief count labels) | both | **NOTED — no repo change** | The drift is in the dispatch brief's metadata (file/commit counts, suite-count labels), not in the tree or the evidence's own recorded runs. Noted for future briefs: cite suite PATHS, and re-derive counts at brief-freeze time. |

---

## 4. Record correction — the phase-6/phase-8 concurrency incident

**Corrected in BOTH documents** (`phase6-evidence.md` §"Consequences" and
`phase8-evidence.md` §14.1). The earlier wording — *"the file sets were
disjoint"* — was **imprecise**: `frontend/src/app/pages/metrics/tabs/
central-tab.component.spec.ts` was touched by **both** phases.

Re-derived mechanically at the fold (not from prose):

```
phase-6 commits (c59d44434,b14f3a5f7,aa7c0b04e,fd6322639) → 13 files
phase-8 commits (b67d0ba13,6566c0111,79892f345,f7b88f56c,14b143c9f,c899371ec) → 20 files
intersection → exactly ONE:
  frontend/src/app/pages/metrics/tabs/central-tab.component.spec.ts
```

**Nothing was lost.** Each phase added its own `it` blocks to that file; both
sets survive at the tip and the spec is green — `44 passed / 44 total`,
including phase 6's unit-vocabulary legs (6.3) *and* phase 8's 8.2–8.5 /
8.7 / 8.8 / 8.9 / 8.10 legs.

---

## 5. Post-fold re-runs (all executed in this worktree)

| check | command | result |
|---|---|---|
| production build | `cd frontend && ./node_modules/.bin/ng build --configuration production` | **rc 0** |
| measurement tool | `node …/bundle-baseline.measure.js dist/frontend/browser` | `initial_total_bytes=5907717`, `initial_file_count=36`, `vendor_static_edge_count=0`, `vendor_in_initial_set=false`, `vendor_invariant_ok=true`, route key resolves to exactly one chunk |
| bundle guard | `node …/bundle-guard.js dist/frontend/browser <build log>` | **GREEN** — B1 Δ **−1,301 B** (limit ±2,048 vs 5,909,018); B2 **0/36**; B3 **0 static**, 1 dynamic site (2 dynamic forms) inside `src/app/pages/metrics/**`; B6 set diff **0/0** |
| B4a (unchanged threshold) | `gzip -9 -c chunk-TXUCW6LI.js \| wc -c` | **71,054 B gz** ≤ 82,000 |
| B4b (unchanged threshold) | measurement tool | lazy-only **165,207 B** ≤ 185,000 (the +22 B vs the phase-9 record is this fold's own route-chunk source delta — the template comment plus the re-ordering; still ~19.8 kB under the pin) |
| `tsc` app + metrics-fe | `--noEmit -p tsconfig.app.json` / `-p tsconfig.metrics-fe.json` | **rc 0 / rc 0** |
| renderer suites | `jest src/app/components/charts` | **3 suites / 84 tests** green |
| resource suites | `jest src/app/pages/metrics/resource` | **6 suites / 65 tests** green |
| metrics tree | `jest src/app/pages/metrics` | **27 suites / 434 tests** green |
| central-tab spec (shared file) | `jest …/tabs/central-tab.component.spec.ts` | **44/44** green |
| whole FE suite | `jest --ci --silent --runInBand` | **4 failed suites / 153**, **9 failed / 4,711** — the **SAME 9 base-proven names** (settings timezone ×2, schedule-create-dialog ×3, jobs-grouping ×3, jobs-filter-state ×1) ⇒ **net-new = 0** |
| NEW Playwright leg | `playwright test -c playwright.resources-gate.config.ts` | **4 passed (38.3 s)** — incl. `[C-1]` |
| metrics gate | `playwright test -c playwright.metrics-gate.config.ts` | **4 passed (37.3 s)** |
| prompt-artifact baseline | `python scripts/protection/generate_prompt_artifact_baseline.py --check` | **OK — 161/161** |

**Thresholds: unchanged** (B1 ±2,048 vs 5,909,018; B2 0; B3 0 static; B4a
82,000; B4b 185,000). `bundle-baseline.json` has no diff. D-1/D-2/D-3 are
untouched by this fold.
