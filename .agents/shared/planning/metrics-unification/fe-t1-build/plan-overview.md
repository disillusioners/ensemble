# Plan Overview: FE-only T1 metrics dashboard (wi-32a99618)

Date: 2026-11-03
Author: plan author (giter lane) for commission wi-32a99618
Status: Draft — ready for review. **PLANNING ONLY: no implementation code in this
commit.**

Worktree: `/home/nea/ensemble-src-wt-fe-metrics-dashboard` · branch
`feature/fe-metrics-dashboard` @ `c32029d1c` · base `c4161335b` (= `origin/latest`).

Plan set files (this directory):

| File | What it carries |
|---|---|
| `plan-overview.md` | this file |
| `phase0-plan.md` … `phase9-plan.md` | one per phase, file-level concrete, explicit exit criteria |
| `decisions.md` | D-1/D-2/D-3 verbatim + the six adjudications owed + 17 implementation decisions |
| `research-findings.md` | the evidence digest (see its §0 for the truncation disclosure) |

---

## 1. Objective

One `/metrics` page that answers the two questions the UI exists for — *"why
is this instance slow right now?"* and *"what changed overnight?"* — as **7
module tabs over producer-live data**, with a page frame that owns the time
range and the previous-window baseline, a **Chart.js-backed interactive panel**
role and **one consolidated house SVG renderer** for minis/fallback, an
honest `AUTH_NOT_CONFIGURED` empty state, a same-release retirement of
`/resources` behind a param-preserving redirect, and a chart layer that labels
its units and reconciles its own arithmetic.

**Testable completion sentence:** on a fenced dev daemon with a scoped operator
token, all 7 tabs render their bound families with data, the `/resources` URL
lands on `/metrics?tab=resource` with its query params intact, every chart
carries a unit-labeled y-axis, and the same page in the auth-absent leg shows
the actionable fail-closed empty state instead of two contradictory banners.

## 2. Scope

### In scope

1. **Tab manifest** — FE-local typed config (`frontend/src/app/pages/metrics/tabs.manifest.ts`),
   discriminated `central | custom` slots, per `resource-refactor-architecture.md` §1.1.
   Adding a *series* to a wired module = registration-only.
2. **7 tabs, all producer-live** (§4 below). Modules with no source are
   **hidden entirely** — planned manifest entries document them; they render nothing.
3. **Page frame above the tabs** — range selector with per-tab vocabulary +
   baseline/previous-window comparison (FE-only client split, W ≤ 1 h).
4. **Consolidated SVG renderer** — the two hand-rolled engines merged into ONE
   house primitive, keeping the OLDER engine's capabilities plus the ruled-in
   mini-tier extensions (devicePixelRatio crispness, nearest-point hover via
   overlay+bisect, last-value marker, optional baseline line, viewBox
   responsiveness).
5. **Chart.js v4 wrapper** for the interactive-panel role + the intent→renderer
   registry + the same-commit governance amendment.
6. **Tab 1 = Resources on a custom slot** over its 6 existing support endpoints.
   Per-instance series through the central door: **deferred** (routed (b)).
7. **Chart correctness** — y-axes with units, per-unit panels (no mixed-unit
   unlabeled normalization), shared-scale semantics on the resource timeline,
   attribution math that reconciles or honestly says it cannot, a coverage
   figure consistent with its own numbers.
8. **Event overlay, FE-only shapes only** — visible anomaly-marker labels from
   the API words the endpoint already serves + an honestly-labelled client-side
   data-gap annotation. Durable restart/deploy events: routed (d).
9. **Route fate (D-3)** — retire `/resources`, same-release param-preserving
   redirect to `/metrics?tab=resource`, gear-menu + spec edits.
10. **Empty-state + honesty fixes (D-2)** — distinct actionable
    `AUTH_NOT_CONFIGURED` state; duplicate/self-contradictory banners; dead
    Retry buttons; green header dot during hard failure; the 900 px banner
    collision; the `chartSpecs` dark-filter/silent-slice gap; the inert
    families table; the dead `active` input.

### Out of scope (with reason)

| Out | Reason |
|---|---|
| Any `daemon/` code, migration, or `scripts/upgrade` change | The commission is FE-only. Two useful backend items are therefore ROUTED (see §7). |
| `agents/` (the agent-definitions tree) | Out of scope by commission; the prompt-artifact baseline (161) must stay byte-identical. |
| Any live-daemon contact, live auth config, live `.env` | D-2: enabling auth is a USER MORNING ITEM. Verification runs against a fenced DEV daemon. |
| Staging / promoting | The commission says stage-only at lane end, no promote in setup. |
| `CHANGELOG.md:141` ("FE package.json empty") | Historical record — stays unamended (`resource-refactor-architecture.md` §5.4). |

---

## 3. The three binding rulings, folded as decisions

Full text in `decisions.md` Part I. Summary of what each one binds:

| Ruling | What this plan does | Where |
|---|---|---|
| **D-1 CHARTING** | Chart.js v4 = the ONLY vendor engine, **interactive panels only**, **lazy-chunk-only** (zero first-paint growth). ONE consolidated house SVG renderer for the mini + fallback roles. The "drop Chart.js if interaction is deferred" flip-condition is **void** — interaction is in scope. The governance amendment (pins spec + pack Leg 7 + the "zero FE deps" doc sites) lands in the **same commit** as the dependency. | Phase 1 (dependency + amendment), Phase 4 (engines), Phase 0 (audit-doc amendment — the lane's FIRST housekeeping commit) |
| **D-2 AUTH POSTURE** | The central door's fail-closed posture is **unchanged**. The FE distinguishes 503 `AUTH_NOT_CONFIGURED` from every other dark cause and renders an **actionable** empty state (cause + what it means + runbook pointer + NO Retry button). The operator recipe is written into §9 (close-out) and is **not executed** by the lane. | Phase 8 (empty state), Phase 9 (verification L2 leg), §9 (recipe) |
| **D-3 ROUTE FATE** | `/resources` is retired in the same release, behind a **param-preserving** redirect to `/metrics?tab=resource`. The predefined-tab direction stands; the Gridster2 door stays closed. | Phase 7 |

---

## 4. The derived 7-tab table

All rows are **producer-live** (DARK means the door is unreachable in
production because live auth is OFF — an install posture, not a producer fact).
Producers are cited from `daemon/services/metrics_query/wiring/` as recorded in
`resource-refactor-architecture.md` §2.1.

| # | Tab | Slot | Bound families (planned in brackets) | Producer (in-tree) | Source endpoints |
|---|---|---|---|---|---|
| 1 | **Resources** | `custom` | — (own module; F0.3 spine is a stat-only row-count family, never a resource measurement) | resource_monitor facade: in-process `TierRing` + the PG retention ladder | `GET /api/resources/live`, `/history`, `/anomalies`, `/anomalies/{id}/evidence`, `/traceback`, `/self-cost` (**6** support endpoints) |
| 2 | **Runtime** | `central` | **F9** [F1, F3] | readiness composite (`w2.py:120-182`); [wedge_capture (`w1.py:94-189`) — joins when R-ii lands] | `/api/metrics/families` · `/snapshot` · `/series` |
| 3 | **DB Pool** | `central` | **F4, F5** [F6] | readiness pool sampling (`w1.py:194-228`); wedge collector (`w1.py:238-268`); [PG census (`w1.py:275-334`) — joins when R-i lands] | same |
| 4 | **Jobs / Queue** | `central` | **F10, F12** | job_queue admission (`w2.py:188-229`); sweeps (`w2.py`) | same |
| 5 | **Instances** | `central` | **F13, F11** | instances census (`w2.py:392-414`); worker_pool `get_stats` (`w2.py`) | same |
| 6 | **Checkpoint** | `central` | **F8** | maintenance gauges (`w2.py:89-114`) | same |
| 7 | **MCP** | `central` | **F17** | mcp/init_guard (`w3.py:51-88`) | same |

**Tab 1's window ladder:** `15m / 1h / 24h / 7d` (its own vocabulary,
`timeline-tab.component.ts:60`). **Central tabs' ladder:** `15m / 30m / 1h / 2h`
(2 h = the substrate ceiling — ADJ-iii).

### Hidden and dropped modules — with reasons

| Module / family | Disposition | Reason | Owner |
|---|---|---|---|
| LLM token usage (F14) | **HIDDEN entirely** (planned manifest entry) | Adjudicated OUT; `usage_metadata` is captured then dropped, "zero consumers ref-wide". A new family + instrumentation is required first. | metrics lane (Tier-3) |
| LLM request latency (F15) | **HIDDEN entirely** (planned manifest entry) | Adjudicated OUT; only WARNING log lines exist. | metrics lane (Tier-3) |
| Source circuit-breakers / token buckets | **HIDDEN entirely** (planned manifest entry) | Uncatalogued; denials are uncounted and both are process-local. | metrics lane (Tier-3) |
| F1 loop lag, F3 wedge captures | **BOUND to Runtime**, availability-gated (dark today — false-dark) | The manifest already binds them; when R-ii wires `capture_active` they appear with **ZERO FE change**. | R-ii (metrics lane, already routed) |
| F6 PostgreSQL census | **BOUND to DB Pool**, availability-gated (dark today — false-dark) | Same: the census polarity inversion (R-i) is the only blocker. | R-i (metrics lane, already routed) |
| F7 checkpoint op bytes | **PLANNED, unbound** | `series_absent` — no series published. | metrics lane |
| F18 mcps gateway sessions | **PLANNED, unbound** | `series_absent`. | metrics lane |
| F21 upgrade journal twin | **PLANNED, unbound** | `series_absent` (needs an install dir). | metrics lane |
| F0.3 resource spine | **PLANNED, unbound** | A stat-only row-count family — never a resource measurement; it must not masquerade as one. | metrics lane |
| F2 thread CPU/RSS | **DROPPED** (never planned) | Adjudicated OUT + roster-dropped (`adjudication.py:57`, `roster.py:204-206`). | — |
| F16, F19, F20, F22 | **DROPPED** (never planned) | Adjudicated OUT — names force-absent. | — |

**Rendering rule (ADJ-vi):** a bound family is charted **only while it is
live**; a bound family the door reports dark is **listed with its reason**, never
charted and never silently dropped; the FE's own selector-ceiling slice is
**surfaced**, never silent.

---

## 5. Phase map

| Phase | Name | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| 0 | Housekeeping — the audit-doc what-changed-and-why amendment | Record the 4-engine proposal → measured reality → D-1 supersession chain, incl. the not-chosen zero-dep counter-option | 6 | independent | pending |
| 1 | Charting dependency + same-commit governance amendment | Land Chart.js v4 + the canvas-mock devDep together with the pins-spec / pack Leg 7 / doc-site amendment; extract `chart-theme.ts` | 8 | tight with 0, 4, 9 | pending |
| 2 | Tab manifest + 7-tab shell + `?tab=` deep links | FE-local typed manifest, discriminated slots, the 7-tab shell, deep-link read/write | 9 | tight with 3, 5 | pending |
| 3 | Page frame — range selector + baseline | Per-tab range vocabulary (visible enforcement) + the FE-only client-split baseline | 7 | tight with 2, 4 | pending |
| 4 | Renderer consolidation — house SVG + Chart.js wrapper + registry | Merge the two engines into one; build the panel wrapper; the intent→renderer registry | 10 | tight with 1, 3, 5 | pending |
| 5 | Tab 1 (Resources, custom slot) | Relocate the resource module; Live strip + Timeline panel + anomalies panel; the window ladder; the honest anomaly disclosure | 9 | tight with 2, 3, 4 | pending |
| 6 | Chart correctness — units, shared scale, attribution math | Per-unit panels with labeled y-axes; the attribution basis; the coverage figure | 6 | tight with 4, 5 | pending |
| 7 | Route fate (D-3) — retire `/resources` + redirect | Param-preserving redirect; gear menu; the spec sweep; delete the retired page | 8 | tight with 2, 5 | pending |
| 8 | Empty state + honesty (D-2) | The actionable `AUTH_NOT_CONFIGURED` state; banner dedupe; dead Retry; health dot; collision; the `chartSpecs` gap; the inert catalog table | 9 | tight with 2, 7 | pending |
| 9 | Verification + close-out | The pixel list, the bundle guard, the packs, the dev data-bearing route, the routed-out register, the operator recipe | 10 | tight with all | pending |

Phase files: `phase0-plan.md` … `phase9-plan.md`.

### Coupling map

| | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| **0** | — | loose | independent | independent | loose | independent | independent | independent | independent | loose |
| **1** | loose | — | independent | independent | **tight** | loose | independent | independent | independent | tight |
| **2** | independent | independent | — | **tight** | independent | **tight** | independent | **tight** | **tight** | loose |
| **3** | independent | independent | **tight** | — | **tight** | **tight** | independent | independent | loose | loose |
| **4** | loose | **tight** | independent | **tight** | — | **tight** | **tight** | independent | independent | loose |
| **5** | independent | loose | **tight** | **tight** | **tight** | — | **tight** | **tight** | independent | loose |
| **6** | independent | independent | independent | independent | **tight** | **tight** | — | independent | independent | loose |
| **7** | independent | independent | **tight** | independent | independent | **tight** | independent | — | **tight** | loose |
| **8** | independent | independent | **tight** | loose | independent | independent | independent | **tight** | — | loose |
| **9** | loose | tight | loose | loose | loose | loose | loose | loose | loose | — |

**Tight couplings and their contracts:**

- **1 ↔ 4** — the Chart.js dependency and the wrapper land together; the
  wrapper's contract (DOM proxy + hidden data table + `destroy()` + the
  no-Angular-overlay rule) is written in Phase 1 and implemented in Phase 4.
- **2 ↔ 3** — the manifest's `allowedWindows` / `defaultWindowS` ARE the
  frame's range options; the frame reads them, it does not own a second list.
- **3 ↔ 4** — the baseline line is a **renderer capability** (the optional
  baseline line in the house SVG; the baseline dataset in Chart.js) driven by
  the frame's baseline toggle.
- **2 ↔ 5** — tab 1 is a `custom` slot; its window vocabulary and its
  component binding come from the manifest, its data from its own endpoints.
- **5 ↔ 6** — the correctness fixes land ON the tab-1 panels (the timeline's
  mixed-unit axis is a tab-1 panel defect).
- **7 ↔ 8** — the redirect's landing surface IS the empty state: `/resources`
  must land on a page that says something true.

---

## 6. Verification plan

### 6.1 The data-bearing environment route (RESOLVED)

**Never the live daemon; never `:9797`; live config out of bounds.**
Everything runs in the lane worktree against a **fenced DEV daemon on
`127.0.0.1:8079`**.

**Pre-flight (all assertions must pass before a boot — any failure = STOP):**

| # | Assertion | Command / check | Fail ⇒ |
|---|---|---|---|
| P1 | The worktree `.env` is the dev fence | `grep -E '^(ENSEMBLE_SELF_ENV|POSTGRES_DB|DAEMON_PORT)=' .env` ⇒ `dev` / `ensemble_dev` / `8079` | STOP |
| P2 | `POSTGRES_DB != ensemble_prod` | `grep '^POSTGRES_DB=' .env` | STOP |
| P3 | The untracked `data/ensemble.json` pin does not point at prod | `cat data/ensemble.json 2>/dev/null` — it must be absent, or `database == "postgres"` with the **DB name** = `ensemble_dev`. The file **outranks the env** for the engine choice (`daemon/ensemble_config.py:56-89`) and is persisted on first boot, so a stale file silently pins every later boot from that tree. | STOP (do not boot) |
| P4 | A scratch data dir is used so the live install's `data/` is unreachable | `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data` (pre-seed `ensemble.json` = `{"database":"postgres"}`) | STOP |
| P5 | The sweep guard is pinned EXPLICITLY | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` — **never** `env -u` on it (the frozen-binary sweep SIGTERM/SIGKILLs live processes when the ambient `=0` guard is stripped) | STOP |
| P6 | The env is scrubbed **per variable** | `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT` — `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) | STOP |
| P7 | The daemon binds the dev port only | `DAEMON_PORT=8079`; assert nothing listens on 9797 from this boot | STOP |

**Boot:** `cd <worktree> && <house dev boot>` with the scrubbed env + the
scratch data dir + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + `DAEMON_PORT=8079`.

**Leg L1 — data-bearing (auth ON, scoped operator bearer):**

1. Auth ON requires `client_id` + `client_secret` + `issuer` (https) +
   non-empty `allowed_origins` — else the daemon refuses to boot (exit 78)
   (`daemon/config.py:3930-3965`, `:4009`). The bearer lane **never contacts
   the IdP** (the middleware verifies a static bearer locally —
   `daemon/middleware/auth.py:358-377`, `_verify_bearer` → `lookup_bearer`
   `:440-447`; no OIDC discovery fetch at boot), so dev-local **placeholder IdP
   config values** satisfy the validator without a live IdP. **Disclosure:** the
   discovery/issuer URL is a placeholder and the OIDC login flow is NOT
   exercised in this lane — the bearer lane is.
2. Mint the operator token against the **dev** database:
   `python -m daemon.auth.cli issue-token --label fe-t1-operator --scopes "maintenance:read"`.
   The scope is valid: the three query routes accept `maintenance:read` OR
   `metrics:read` (`daemon/routers/metrics.py:57-63`); the v1 scope taxonomy is
   closed (`daemon/auth/scopes.py:66-120`). The plaintext prints ONCE and is
   never written into a repo file.
3. The browser presents it with the Playwright context's
   `extraHTTPHeaders: { Authorization: 'Bearer <token>' }` — **no production FE
   change** is made to carry tokens.
4. Assertions: `/livez` 200; `/api/metrics/families` 200 with a non-empty
   catalog; `/api/metrics/snapshot?family=F9` 200; `/api/metrics/series?names=…&window_s=…`
   200; `/api/resources/live` 200 with real values.

**Leg L2 — auth-absent (the D-2 empty state):** the same dev env with auth
**disabled** ⇒ `/api/metrics/*` answers **503 `AUTH_NOT_CONFIGURED`** ⇒ the
actionable empty state renders. Assert: exactly ONE actionable panel, the
runbook pointer is present and correct, **no Retry button** on that panel, the
tabs do not each render their own banner, and the header dot is not green.

**Leg L3 — mixed-unit correctness:** on the dev daemon's resource timeline,
assert the memory panel's y-axis is labelled in MB and the CPU panel's in %,
and that the two panels do NOT share an axis.

### 6.2 Pixel-verification list

Each item: capture via the canonical script shape
(`research-findings.md` §7 — Playwright 1.60.0 from `frontend/node_modules`,
`chromium --no-sandbox`, `domcontentloaded` → best-effort `networkidle` (15 s
cap, never settles) → **fixed 3 s settle** → screenshot), against the **dev**
daemon at `127.0.0.1:8079`.

| # | Pixel list item | Viewport | Assert |
|---|---|---|---|
| PX-1 | **Tab 1 Resources — WITH DATA**: Live strip + Timeline panel + anomalies panel | 1600×1000 | real values render; the anomalies panel lists rows; the process-local disclosure is visible |
| PX-2 | **Tab 2 Runtime** | 1600×1000 | F9 panels render; the family line lists the bound families with their availability |
| PX-3 | **Tab 3 DB Pool** | 1600×1000 | F4/F5 panels render |
| PX-4 | **Tab 4 Jobs/Queue** | 1600×1000 | F10/F12 render |
| PX-5 | **Tab 5 Instances** | 1600×1000 | F13/F11 render |
| PX-6 | **Tab 6 Checkpoint** | 1600×1000 | F8 renders |
| PX-7 | **Tab 7 MCP** | 1600×1000 | F17 renders |
| PX-8 | **AUTH_NOT_CONFIGURED actionable empty state** (leg L2) | 1600×1000 **and 900×1000** | one panel; cause named; runbook pointer; NO Retry; no duplicate banner; no green header dot; no 900 px collision |
| PX-9 | **`/resources` redirect** | 1600×1000 | lands on `/metrics?tab=resource`; **query params preserved** (e.g. `/resources?window=24h&x=1` ⇒ `/metrics?tab=resource&window=24h&x=1`) |
| PX-10 | **Mixed-unit y-axis correctness** | 1600×1000 | memory panel axis labelled (MB); CPU panel axis labelled (%); separate scales; no unlabeled mixed axis |
| PX-11 | **Baseline / previous-window comparison** | 1600×1000 | the baseline series renders and is labelled as the previous window; the fidelity disclosure is visible |
| PX-12 | **Range switch** | 1600×1000 | switching tabs swaps the option set (visible enforcement) — a central tab offers no 24h/7d |
| PX-13 | **Anomaly marker labels + data-gap annotation** | 1600×1000 | marker labels carry the API words (kind/severity/peak); any gap annotation says "data gap", never "restart" |
| PX-14 | **Dark-family listing** | 1600×1000 | a bound-but-dark family appears in the family line **with its reason**, not charted |

Every capture is GET-only (assert 0 non-GET in the API census) and the live
daemon is never contacted.

### 6.3 Bundle guard

| # | Guard | Threshold |
|---|---|---|
| B1 | **Initial-set total, base vs HEAD** | **~0 delta asserted.** The initial set is 5,909,018 B against a 6,000,000 B error budget (89,982 B headroom). A leak of Chart.js into the initial set hard-fails the production build **and both FE packs**. |
| B2 | **The new lazy chart chunk measured** | The real **min + gz** bytes of the tree-shaken controller set, recorded in the lane's evidence. This closes governance item §9-6 ("verify Chart.js v4 size before the budget claim locks") — the ~65 kB figure was an ESTIMATE, never measured. |
| B3 | **Lazy-chunk-only proof** | The Chart.js import graph is reachable ONLY from the lazy `/metrics` route tree; the initial bundle's file list is byte-identical to base. |
| B4 | **Both FE packs green** | `test/packs/metrics_fe_dual_side_test.sh` (Leg 7 amended — §6.4) and `test/packs/fe_static_typecheck_build_test.sh`. |
| B5 | **Prompt-artifact baseline** | `agents/.prompt-artifacts.sha256` — **161/161 clean**. |
| B6 | **Budget warnings** | Recorded verbatim as adjudication data (the house convention — `fe_static_typecheck_build_test.sh` Stage 2), never auto-fixed. |

### 6.4 Expected FE pack / test additions (SPECIFIED here — not built by this plan)

**Jest units (new):**

| Spec | What it pins |
|---|---|
| `frontend/src/app/pages/metrics/tabs.manifest.spec.ts` | the manifest shape: exactly 7 `live` tabs; slot kinds; planned entries render nothing; `?tab=` slugs are unique and stable |
| `frontend/src/app/pages/metrics/metrics-shell.spec.ts` (or extend `metrics.component.spec.ts`) | the deep-link read/write; an unknown `?tab=` falls back without a loop; the catalog drawer replaces the Families tab |
| `frontend/src/app/pages/metrics/range-frame.spec.ts` | the per-tab vocabulary swap; the visible disabling; the client-split baseline maths (the midpoint split, the W ≤ 1 h guard) |
| `frontend/src/app/components/charts/renderer-registry.spec.ts` | the intent→renderer mapping; `supports()`; the never-silent fallback (a Chart.js mount failure ⇒ a surfaced badge + SVG) |
| `frontend/src/app/components/charts/house-svg-renderer.spec.ts` | **ported** from the existing 241 L (`svg-line-chart.component.spec.ts`) + 237 L (`svg-timeline.component.spec.ts`) pure-helper specs — the helpers stay exported and unit-asserted; plus the mini extensions (devicePixelRatio, bisect, last-value, baseline) |
| `frontend/src/app/pages/metrics/resource/resource-tab.spec.ts` | the tab-1 body order; the window ladder; the anomaly disclosure; the drill-down policy (`anomalyDrilldownWindow` moves OUT of the chart into the panel) |
| `frontend/src/app/pages/metrics/resource/timeline-correctness.spec.ts` | the per-unit split; the labeled axes; the shared-scale semantics; the attribution basis; the coverage consistency |
| `frontend/src/app/pages/metrics/empty-state.spec.ts` | the `AUTH_NOT_CONFIGURED` copy (actionable + runbook pointer); NO Retry on that state; the other four classes keep theirs |
| `frontend/src/app/pages/metrics/redirect.spec.ts` | the `canMatch → UrlTree` redirect preserves the query params |
| `frontend/src/app/pages/metrics/chart-specs-honesty.spec.ts` | dark families do NOT consume the selector ceiling; the FE's own truncation IS surfaced |

**Playwright legs (new, in `frontend/e2e/`):** 7-tab deep links; the
`/resources` redirect (+ query-param preservation); the `AUTH_NOT_CONFIGURED`
empty state; mixed-unit y-axis correctness. The existing gate configs
(`playwright.metrics-gate.config.ts`, port 14203;
`playwright.resources-gate.config.ts`, port 14202) are the templates — the
resources gate must be retired with the route (or re-pointed), and the new
legs need their own config or a widened `testMatch` **without** sweeping the
daemon-dependent specs.

**`PACKS.md` registration (keep-both, newest-first):** the new/updated pack
rows for the FE gate (the amended `metrics_fe_dual_side` and the new
playwright pack), with the node counts re-derived at gate time (the house
convention: **MECHANICAL `pytest --collect-only -q` counts**, never a
declared number).

**The Leg-7 amendment mechanics (exact):** `test/packs/metrics_fe_dual_side_test.sh:275-286`
currently hard-FAILs on **ANY** `frontend/package.json` diff. It is flipped
from *zero-diff* to *expected-dependency-set*:

```
EXPECTED_DEPS="chart.js"                       # dependencies
EXPECTED_DEV_DEPS="jest-canvas-mock"            # devDependencies
# package.json diff must contain EXACTLY those two added direct entries
# (assert by NAME, not by a raw line count — a transitive bump must not break it)
# the lockfile closure may additionally carry @kurkle/color, cssfontparser, moo-color
```

The pack's `package_json_dependency_diff` leg is re-purposed (its log becomes
the evidence) and the `metrics.bindings.pins.spec.ts:94` banned-list is
amended in the SAME commit (D-1) — the ban becomes
`['d3-', "from 'd3'", 'ngx-charts', 'moment', 'dayjs']` plus a **positive** pin
that `chart.js` is imported ONLY from the lazy metrics chunk (never from a
static import reachable by an entry point).

### 6.5 The pixel-verification + pack gates are the lane's exit criteria

Phase 9 owns the final assembly: every PX item captured, every pack green, the
bundle guard measured, the routed-out register closed out, and the operator
recipe handed over.

---

## 7. Routed-out register

Each item: owner + reason + the exact handoff. **The lane does not build any
of these.**

| # | Routed item | Owner | Reason / handoff |
|---|---|---|---|
| a | **Enable auth on live** (the operator recipe) | **USER (morning item)** | D-2. The recipe is §9 of this plan; the lane writes it and never executes it against live. |
| b | **Per-instance CPU/mem through the central door** | metrics lane (own commission) | A rule + budget + retention collision: A10/A18 excludes per-instance labels; the AM-2 closed-cardinality guard forbids an open label key; `GLOBAL_SERIES_CAP = 2000` vs ~6,324 prod instances; the ring is 2 h in-RAM while the durable tier is PG. Tab 1 covers the need over `/api/resources/*` with zero backend work (`resource-refactor-architecture.md` §6.1). |
| c | **LLM tokens / LLM latency / source-breaker families** | metrics lane (Tier-3 waves) | New families + instrumentation; the FE cost after each lands is one manifest flip. |
| d | **Durable anomaly/restart events spanning restarts** | metrics lane (small commission) | The journal is process-local (`deque(maxlen=256)`, reset per restart). The lane ships an honestly-labelled **data-gap** annotation instead and says plainly what it knows. |
| e | **Mermaid's 972 kB gz initial-bundle elephant** | FE debt lane | It is the real initial-set elephant (~80 % of the initial bundle, loaded on every page) — but it is not this lane's surface. Recorded with the measured figure (3.56 MB raw / 972 kB gz vs 5,909,018 B initial). |
| f | **T1.5 backend `end_offset_s` / `compare` params** on `/api/metrics/series` | metrics lane (small, optional) | T1 uses the FE-only client split (W ≤ 1 h). The backend param is additive with no schema/vocab bump (`resource-refactor-architecture.md` §3.2) — a later, better-commissioned change. |
| g | **Retire `/api/resources/control-capabilities` + `POST /api/resources/profile`** | backend hygiene commission | Dead code, but retiring them rewrites the scope-manifest census pin (`tests/unit/test_router_scope_manifest.py:106-113,:288-292`) — a daemon change, out of this lane's scope (IMPL-15). |
| h | **F1/F3/F6 false-dark fixes (R-i census polarity, R-ii `capture_active` wiring)** | metrics lane (already routed) | The manifest already binds the three families; they appear with **zero FE change** when the fixes land. |
| i | **F0.3 spine-health `TypeError`** (`w1.py:366-367` — `@property` counters called with parens ⇒ always "not accepting") | metrics lane (already routed) | A daemon defect; the FE renders the family's value as the API serves it. |
| j | **Central windows > 2 h** (ring growth or a PG tier) | metrics lane (unpaved) | Multi-axis: ring depth, `POINT_BUDGET`, eviction, cursor expiry — or a PG tier that breaks the read-path purity contract (`resource-refactor-architecture.md` §6.4). |
| k | **BE-served manifest** (shape A: one optional field + one pin update) | metrics lane (T2+) | Flip only when module-onboarding cadence makes FE commits a real bottleneck. Shape (B) is forbidden — it breaks the frozen `query-routes` fence. |
| l | **ECharts / ApexCharts / Gridster2** | deferred with triggers | ECharts: fires only on (t1) A10/A18 relaxation, (t2) raised `window_s`/`max_points` ceilings, or (t3) a brushing panel class. ApexCharts: needs an aesthetic report surface. Gridster2: dropped — a composable-dashboards product goal is the trigger, and the layout layer is orthogonal to the engine seam. |
| m | **The per-instance top-N list on tab 1** (the `instances[]`/`children[]` payload fetched every 8 s and never rendered) | follow-up commission | The payload is already fetched, so the deferral is cheap; but rendering it is a NEW capability (top-N? sort? click-through?) beyond the commission's scope item 6 (math + coverage). |
| n | **The `/resources` gate pack** (`frontend/playwright.resources-gate.config.ts`, port 14202) | this lane, Phase 7 | It must be retired or re-pointed with the route — not a separate deferral, but recorded here so it is not lost. |

---

## 8. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Chart.js leaks into the INITIAL bundle** (a static import reachable by an entry point) → the production build hard-fails at 6 MB (the headroom is 89,982 B) and both FE packs go red | High | Medium | B1/B3 assert the initial file list is byte-identical to base; the positive pin in `metrics.bindings.pins.spec.ts` bans a static chart import outside the lazy metrics tree; the wrapper is imported ONLY by a lazy route component. |
| 2 | **The canvas redraw fights the zoneless Angular runtime** (no overlay may depend on a library redraw) | High | Medium | The rule is written into the wrapper's contract (IMPL-04) and the wrapper's spec pins that the Angular-rendered DOM (legend, axes proxy, hidden data table) is owned by Angular, not by Chart.js. |
| 3 | **The FE cannot be verified with data** — auth is OFF in the dev env and enabling it needs an IdP | High | Medium | The bearer lane never contacts the IdP (locally verified): placeholder IdP *config* satisfies the validator while a minted operator bearer authenticates the API. Leg L1 uses a browser-context `Authorization` header — no production FE change. If the bearer leg proves impossible in practice, the lane **stops that verification leg and reports it** rather than weakening the door (D-2 forbids weakening). |
| 4 | **Scope creep from the 7 tabs × 2 engines × 3 defects** — the lane grows past a reviewable size | Medium | High | The phase map is sequenced with explicit exit criteria; the phase files name exact files; "a tab needing a backend touch ⇒ STOP THAT TAB, record it, ship the rest" is the standing rule; the routed-out register is closed at Phase 9. |
| 5 | **The false-dark families (F1/F3/F6) are charted as if dark** (the audit's exact honesty defect) | Medium | Medium | ADJ-vi: rendering filters by availability BEFORE the ceiling; a dark bound family is listed with its reason; the FE's own slice is surfaced, never silent. |
| 6 | **The consolidation LOSES capability** (the audit's "the newer engine is strictly less capable than the older" precedent repeating) | Medium | Medium | The port is a **port, not a rewrite**: the exported pure helpers and their 241 L + 237 L specs move with them; Phase 4's exit criterion is a capability checklist (window ladder, series toggles, anomaly markers, drill-down, UTC formatting) proven present in the consolidated renderer. |
| 7 | **`/resources` retirement breaks a bookmarked deep link** (the query params are the point) | Medium | Low | The redirect is `canMatch → UrlTree` (query-preserving); PX-9 asserts the params survive; `?tab=` is written back on tab change so the landed URL is shareable. |
| 8 | **The env-poison family reaches the dev daemon** (ambient `POSTGRES_DB=ensemble_prod` / `PORT=9797` in the agent shell) | High | Medium | P1–P7 pre-flight, the per-variable scrub (never `env -u POSTGRES_*`), the explicit `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, the scratch `ENSEMBLE_DATA_DIR`, and the `data/ensemble.json` assertion. Any P-failure = STOP, not "proceed and watch". |
| 9 | **The governance amendment is incomplete** — a "zero FE deps" site is missed, so the next lane inherits a contradiction | Medium | Medium | The amendment enumerates the sites (pins spec + pack Leg 7 + `docs/architecture/metrics-fe-charts.md:126-128,:144` + `docs/architecture/metrics-promote-disclosure.md:122` item #2; `CHANGELOG.md:141` stays historical); Phase 1's exit criterion is a repo-wide grep for the zero-dep claim. |
| 10 | **The mini-tier extension decision (ADJ-ii) is read as a licence to grow the mini into a second engine** | Medium | Medium | The scope guard is binding and enumerated (five extensions, closed set; no axes grid / zoom / brush / legend language / export on the mini; Chart.js stays panels-only). A new extension requires a new decision entry. |

---

## 9. Close-out — the operator auth recipe (USER MORNING ITEM, NOT EXECUTED BY THE LANE)

D-2 reserves this decision for the user. The lane **hands over** the recipe; it
does not run it, and it does not touch the live config. Source:
`docs/runbooks/oauth2-auth.md` (the authoritative runbook) §1a–§1d, §3.

**Steps (live install):**

1. **Register an OAuth2/OIDC client at the IdP** — a *confidential* client,
   Authorization-Code + PKCE. Register the callback URI **byte-for-byte** as
   the daemon derives it: `https://<daemon-host>/api/auth/callback` (derive
   FIRST per runbook §1c, then register that exact value). Issue a client secret.
2. **Set the daemon environment** (runbook §1b) —
   `ENSEMBLE_AUTH_ENABLED=true`, `ENSEMBLE_AUTH_CLIENT_ID=…`,
   `ENSEMBLE_AUTH_CLIENT_SECRET=…` (never in the repo; never logged),
   `ENSEMBLE_AUTH_DISCOVERY_URL=https://<idp>/.well-known/openid-configuration`,
   `ENSEMBLE_AUTH_ALLOWED_ORIGINS=https://<daemon-host>` (**REQUIRED** — with
   auth ON the CORS posture must replace wildcard+credentials, and an empty
   list refuses the boot with exit 78).
3. **Mint the operator token** with the scope the metrics door accepts —
   the three query routes take `maintenance:read` OR `metrics:read`
   (`daemon/routers/metrics.py:57-63`). For a browser session, the session lane
   grants the OIDC scopes ∪ the operator family
   (`daemon/auth/scopes.py:127`); for a machine reader, mint a static bearer:
   `python -m daemon.auth.cli issue-token --label operator --scopes "metrics:read"`
   (plaintext prints ONCE; the row stores only a SHA-256).
4. **RESTART implication (explicit):** auth has **no live toggle in v1** — the
   `auth.enabled` flip and the new env vars require a **daemon restart**
   (`config.yaml:129-152`: "restart required, no live toggle in v1"). The
   restart is the moment the central door flips from 503
   `AUTH_NOT_CONFIGURED` to serving. Plan the restart window; the pre-restart
   state is unchanged for every other surface.
5. **Verify** — `GET /api/metrics/families` returns 200 with a bearer; without
   one it returns 401 (`AUTH_REQUIRED`). With auth OFF the same URL returns 503
   `AUTH_NOT_CONFIGURED` (unchanged posture).
6. **Also decide (user-owned, out of the lane):** the `/api/resources/*` door
   is fail-**open** by design (scope manifest `*`) while the metrics door is
   fail-**closed** — merging the surfaces under one page forces a conscious
   choice about that posture (the audit's incidental finding;
   `resource-tab-forensics.md` Part C (d)).

---

## 10. Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | The page ships **7 module tabs**, every one producer-live | the manifest spec + PX-1…PX-7 | 7 tabs, 0 dark shells, 0 planned entries rendered |
| 2 | Every bound family is rendered or **listed with its reason** — never silently dropped | the chart-specs honesty spec + PX-14 | 0 silent omissions; the FE's own slice is surfaced whenever it truncates |
| 3 | `/resources` lands on `/metrics?tab=resource` **with its query params** | PX-9 (a parametrized URL) | 100 % of the params preserved; the landing tab is tab 1 |
| 4 | The `AUTH_NOT_CONFIGURED` state is **distinct and actionable** | PX-8 at 1600 px and 900 px | 1 panel; the runbook pointer; 0 Retry buttons on that state; 0 duplicate banners; the header dot is not green |
| 5 | Charts carry **units**; the resource timeline's mixed-unit axis is gone | PX-10 + the correctness spec | 0 unlabeled axes; memory and CPU on separate labeled scales |
| 6 | The attribution card **reconciles or says it cannot** | the correctness spec + a pixel read | the coverage figure and the displayed components are consistent, or the card states the non-reconciliation in words |
| 7 | The consolidation keeps the older engine's capabilities | the consolidated-renderer spec | the capability checklist (window ladder, series toggles, anomaly markers, drill-down, UTC) — 5/5 present; the pure-helper specs ported |
| 8 | **Zero first-paint growth** from the charting dependency | B1/B3 (the initial file list vs base) | the initial set is byte-identical to base; the initial total delta ≈ 0 |
| 9 | The new lazy chart chunk is **measured**, not estimated | B2 | real min + gz bytes recorded in the lane's evidence |
| 10 | Both FE packs are green, with Leg 7 asserting the **expected dependency set** | the packs | 2/2 green; the assertion is by name, not by count |
| 11 | The prompt-artifact baseline is untouched | `agents/.prompt-artifacts.sha256` | **161/161 clean**; the `agents/` tree has zero diffs |
| 12 | The lane touches **no live daemon** | the capture log's API census + the boot fence | 0 requests to `:9797`; 0 non-GET anywhere; live config byte-identical |
| 13 | The routed-out register is **closed out** — 14 items, each with an owner | Phase 9's exit criterion | 14/14 recorded with an owner and a reason |
| 14 | The operator recipe is handed over, not executed | this plan's §9 + the Phase 9 exit criterion | the recipe exists with the restart implication named; 0 live config writes |

---

## 11. Research insights that shaped this plan

- **The central door is dark in production for a reason that is not the
  chart's fault.** Live auth is OFF ⇒ 503 `AUTH_NOT_CONFIGURED` ⇒ the whole
  central-metrics FE serves nobody today, while `/resources` (fail-open) is the
  only surface with live data (`live-capture-log.md` §2, §4). The plan
  therefore verifies on a **dev** daemon with a bearer, and hands the
  enable-auth decision to the user (D-2).
- **"Live" means producer-live, not door-reachable.** The 7 tabs are chosen by
  producer liveness; the three false-dark families (F1/F3/F6) are bound but
  gated so they appear with zero FE change when R-i/R-ii land.
- **The newer chart engine is strictly less capable than the older one it
  duplicated** — the divergence precedent the house itself warns about. That
  is why the consolidation is a **port** (helpers + specs move) and why the
  capability checklist is an exit criterion, not a hope.
- **The mixed-unit axis is not a styling defect — it is a false statement.**
  Two units, one unlabeled axis, each series independently min-max normalized
  ⇒ the fills read as a stacked-area chart. The fix is a **split** (per-unit
  panels), not a label.
- **The FE's own selector slice is a silent-omission class** — dark families
  consume the ceiling and later live families are cut, while the FE surfaces
  only the *door's* truncation (`metrics.component.ts:162-175`;
  `fe-metrics-source-audit.md` §7.4). Same discipline as the door: **a bound
  is surfaced, never silent**.
- **The audit's "no per-instance attribution" is a FE omission, not a data
  gap** — the `instances[]`/`children[]` payload is fetched every 8 s and
  never rendered. Deferred (routed m) because rendering it is a new
  capability; the arithmetic fix is in scope.
- **The anomaly facts are all in the API already** — the marker `<title>`
  composes `kind · shape · severity · peak`
  (`svg-timeline.component.ts:530-532`); the defect is that they are
  hover-only. The event overlay ships as **labelling**, not as new data.
- **The charting numbers were ESTIMATED, never measured** — §9-6 of the
  architecture doc asks the build lane to measure the real min+gz of the
  tree-shaken controller set. That is bundle guard B2.
- **The bundle elephant is mermaid, not a chart library** (3.56 MB raw /
  972 kB gz ≈ 80 % of the initial set, loaded on every page). It is routed (e),
  and the charting work must not make it worse.

---

## 12. Open questions

| # | Question | Why it is still open | What the plan does meanwhile |
|---|---|---|---|
| 1 | Should the **family catalog drawer** be a drawer, or a small tab-like popover? | A UI affordance call with no functional consequence; the audit recorded "a real composable-dashboards product requirement" as the only legitimate trigger for a layout layer, and a drawer is the cheapest thing that is not a layout layer. | Built as a **frame-level drawer** (ADJ-i). A popover is a CSS variant of the same component and does not change the decision record. |
| 2 | Does the **operator recipe** need to cover the `metrics:read` scope for the session lane too? | The session lane grants the OIDC scopes ∪ the operator family; whether the IdP must be configured to emit `maintenance:read`/`metrics:read` is an IdP-side question the user answers when they enable auth. | §9 states both paths (session lane + static bearer) and names the door's ANY-OF scope set; the IdP-side mapping is flagged as an operator decision. |
| 3 | Should the **data-gap annotation** also appear on the central tabs' series? | The central series are ring-backed (2 h, in-RAM) and can drop a sample for reasons the FE cannot distinguish from a gap; the resource timeline is the surface where the gap is meaningful. | Ship it on tab 1 only; the central tabs show the door's own `stale` flag, which is the door's honest signal. Revisit if a central gap is proven to be actionable. |
| 4 | Is the **500-row snapshot ceiling** enough for the per-tab stat strips on a large install? | The snapshot route is bounded (`MAX_SNAPSHOT_SERIES`, `offset`/`limit`, `truncated` + `next_cursor`). The 7 tabs' stat families are a small subset, but the FE fetches the snapshot **unscoped** and filters client-side (IMPL-08). | IMPL-08 handles it honestly (a family absent from a bounded page renders "not on this page" + the truncation hint, never a fake zero) **and** the FE service gains the optional `?family=` scoping as the preferred path; the unscoped fetch is the fallback. |
