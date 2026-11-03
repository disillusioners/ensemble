# Plan Overview: FE-only T1 metrics dashboard (wi-32a99618)

Date: 2026-11-03 (fold #2: architecture-review amendments AM-1..29 + E-1/E-2 folded)
Author: plan author (giter lane) for commission wi-32a99618
Status: Draft — ready for review, **post-architecture-review fold**.
**PLANNING ONLY: no implementation code in this plan set.**

Worktree: `/home/nea/ensemble-src-wt-fe-metrics-dashboard` · branch
`feature/fe-metrics-dashboard` · plan set committed at `3a6bf7ba5`, the
architect review at `c26cec612`.

Plan set files (this directory):

| File | What it carries |
|---|---|
| `plan-overview.md` | this file |
| `phase0-plan.md` … `phase9-plan.md` | one per phase, file-level concrete, explicit exit criteria |
| `decisions.md` | D-1/D-2/D-3 verbatim · the **accepted analyst rulings R-1/R-2** · the six adjudications owed · **20** implementation decisions |
| `research-findings.md` | the evidence digest + **§14: the lane-base bundle measurement** (the AM-2 re-derivation) |
| `architecture-review.md` | the architect's SOUND-WITH-AMENDMENTS review (29 amendments) — folded, not modified |

---

## 0. Conventions, standing rules, and the gated-figures ledger

### 0.1 E-1 — THE STANDING LANE RULE (every MEASURED figure is re-derived at lane base)

> **Every MEASURED figure a phase exit criterion depends on must be
> RE-DERIVED at lane base (this worktree's tree) before that phase freezes —
> and a label without a COMMAND is not a re-derivation.**

**Why (the architect's escalation, E-1):** the research digest's MEASURED
ledger carries a **wrong-tree provenance problem** — the bundle baseline was
almost certainly measured on the **main checkout's v0.27.8-era FE**, and its
internal arithmetic never self-checked. The process concern is that **every**
MEASURED figure, not just the one that was wrong, must be re-derived before it
becomes a gate.

**Why the COMMAND clause (the plan-review's C-3, generalised):** C-3 caught a
row that was labelled *"(a) re-derived at lane base"* while carrying a **false
census** (8 warnings instead of 9). A label is an assertion about a
re-derivation; only a **command plus its real output** is the re-derivation
itself. **Every row marked (a) in §0.2 therefore carries the exact command
that produced the number and the output that command returned.**

**How it is honoured in this plan set:**

1. Each phase's **Exit Criterion** section ends with an explicit **"E-1
   re-derivation"** line naming the MEASURED figures that phase gates on and
   stating, for each, whether it is **(a) re-derived at lane base** — with
   the command and its output — or **(b) a re-derivation task in that phase**
   (with the task id).
2. §0.2 below is the **audit table**: every MEASURED figure any exit criterion
   depends on, with its status **and its command + output**.
3. The **named baseline artifact** (plan-review A-8) makes the numbers
   machine-readable in one place: **`bundle-baseline.json`** (the single source
   the guards read) + **`bundle-baseline.measure.js`** (the re-derivation tool,
   living in the plan dir — not `scripts/`, not the shipped tree). The §0.2
   rows are generated from that artifact, so the ledger and the guard cannot
   drift apart.
4. Where a figure could not be re-derived at lane base (because it is a
   **post-implementation** artifact — a new chunk's size, a new component's
   style bytes), the plan carries an explicit **phase-freeze re-derivation
   task** instead of inheriting a digest number.

### 0.2 The gated-figures ledger (the E-1 audit table)

**Every row marked (a) carries its exact COMMAND and the OUTPUT that command
returned.** The G1–G7 rows share one command; the JSON output is reproduced
verbatim in `bundle-baseline.json` → `reproduce.output`.

**The re-derivation command (G1–G7), run from the lane worktree:**

```
cd frontend
npm ci                                  # node_modules absent in the fresh worktree
./node_modules/.bin/ng build --configuration production
node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
```

**Its verbatim output:**

```json
{
  "initial_total_bytes": 5909018,
  "initial_file_count": 36,
  "error_budget_bytes": 6000000,
  "headroom_bytes": 90982,
  "index_html_bytes": 9257,
  "whole_initial_payload_bytes": 5918275,
  "lazy_chunk_count": 52,
  "lazy_bytes_total": 1702633,
  "metrics_route_chunk": "chunk-ZA3RV4HY.js",
  "metrics_route_chunk_bytes": 27672,
  "metrics_route_fetch_set_bytes": 496338,
  "largest_lazy": "chunk-QS2K3YLH.js",
  "largest_lazy_bytes": 157141,
  "second_largest_lazy": "chunk-HNKUMJNH.js",
  "second_largest_lazy_bytes": 135690,
  "resources_route_chunk": "chunk-FOLCEMN3.js",
  "resources_route_chunk_bytes": 38914
}
```

**The G10 command (the `anyComponentStyle` census) and its verbatim output:**

```
grep -E "scss exceeded maximum budget" <build-log> | sed 's/^▲ \[WARNING\] //'
```

```
src/app/components/agent-selector/agent-selector.scss exceeded maximum budget. Budget 8.00 kB was not met by 765 bytes with a total of 8.77 kB.
src/app/pages/snapshots/snapshots.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 4.29 kB with a total of 12.29 kB.
src/app/pages/jobs/jobs.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 6.77 kB with a total of 14.77 kB.
src/app/components/add-source-modal/add-source-modal.scss exceeded maximum budget. Budget 8.00 kB was not met by 318 bytes with a total of 8.32 kB.
src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 697 bytes with a total of 8.70 kB.
src/app/pages/blueprint/blueprint.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 10.32 kB with a total of 18.32 kB.
src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.scss exceeded maximum budget. Budget 8.00 kB was not met by 701 bytes with a total of 8.70 kB.
src/app/components/instance-list/instance-list.scss exceeded maximum budget. Budget 8.00 kB was not met by 2.17 kB with a total of 10.17 kB.
src/app/components/chat-interface/chat-interface.scss exceeded maximum budget. Budget 8.00 kB was not met by 8.15 kB with a total of 16.15 kB.
```

(the same log also carries the pre-existing
`bundle initial exceeded maximum budget. Budget 1.00 MB was not met by 4.91 MB with a total of 5.91 MB.`)

| # | MEASURED figure | Value | Status under E-1 (with command + output above) | Gates |
|---|---|---|---|---|
| G1 | **Initial total** (the builder's own "Initial total" basis — 36 chunk files; `index.html` is an asset, not a chunk) | **5,909,018 B** | **(a) re-derived at lane base** — command + output above; **two independent derivations agreeing byte-for-byte** (the builder's own 36-row table summed from disk; an independent transitive-static-import closure), cross-checked against the shipped v0.28.9 dist which measures **identically**. Method: `research-findings.md` §14 | Phase 1's bundle guard (1.9), Phase 4's re-run (4.14), Phase 5's re-run (5.14), Phase 9's B1 |
| G2 | **Headroom** vs the 6,000,000 B decimal `initial` error budget | **90,982 B** | **(a) derived from G1** by the same command. The digest's `89,982 B` was a **1,000 B digit slip**; AM-2's `884,180 B` undercounts by 793,198 B (it summed only the index.html-referenced files, omitting the builder's 23 transitive static-import chunks) | the eager-leak reasoning; the Phase 1/4/5/9 guards |
| G3 | **Lazy chunk count** | **52** | **(a) re-derived at lane base** — the same command; reproduces the digest **exactly** | Phase 9's B-table (the baseline the new chunk is measured against) |
| G4 | **`/metrics` lazy route chunk size** | **27,672 B** (`chunk-ZA3RV4HY.js`) | **(a) re-derived at lane base** — the same command; reproduces the digest **exactly**. **No longer an orphan (C-1):** it is now **B4's declared baseline**, and its consumer is Phase 9's 9.8(d) route-fetch-set comparison | Phase 9's B4 (the new-chunk comparison) |
| **G4b** | **`/metrics` route FETCH SET** (the route chunk + its **direct** static deps) | **496,338 B** | **(a) re-derived at lane base** — the same command (`metrics_route_fetch_set_bytes`). **Newly measured** (neither the digest nor AM-2 carried it). **This is the threshold that actually catches a static-shape leak**: on the real dist the route chunk statically imports `chunk-6Y6F4JKW.js` (43,688 B) and `chunk-AOZCSBSC.js` (1,348 B), **both LAZY-ONLY** — so a static `chart.js` import would land in a lazy-only sibling and the route chunk alone would not grow | Phase 9's B4 (the route fetch set's growth bound) |
| G5 | **`/resources` lazy route chunk size** (the PRE-retirement route chunk) | **38,914 B** (`chunk-FOLCEMN3.js`) | **(a) re-derived at lane base** — the same command; **newly measured**. **RESTATED (B3):** Phase 7 retires the `/resources` route, so this chunk **does not survive the lane** — G5 is the **pre-retirement baseline** of the retirement's bundle delta, **not** a Phase-9 gate; the Phase-9 re-derivation covers **the surviving route chunk(s)** only (G4/G4b), and the measurement tool is **absent-tolerant** so a retired route is reported as **absent**, never mis-resolved | the retirement's bundle delta (pre-retirement baseline); **not** a Phase-9 exit criterion |
| G6 | **Lazy bytes total** | **1,702,633 B** (≈1.70 MB) | **(a) re-derived at lane base** — the same command; reproduces the digest | Phase 9 |
| G7 | **Largest lazy chunk** | **157,141 B** (`chunk-QS2K3YLH.js`); second 135,690 B (`chunk-HNKUMJNH.js`) | **(a) re-derived at lane base** — the same command. The digest's 135,690 B is now the **second** largest — a one-position drift, disclosed | Phase 9's context |
| G8 | **Per-component `anyComponentStyle` figures** for the new components | *post-implementation* | **(b) re-derivation task** — Phase 4's task 4.11 measures and records them at that phase's freeze (the 8 kB warn / 24 kB ERROR caps are config constants, not measurements) | Phase 4's exit criterion; Phase 9's B5 |
| G9 | **The new lazy chart chunk's real min + gz bytes** | *post-implementation* | **(b) re-derivation task** — Phase 9's task 9.8(d) measures it (this closes governance item §9-6; the ~65 kB figure was an ESTIMATE) | Phase 9's B4 |
| **G10** | **The `anyComponentStyle` warning SET** (not a count) | **9 paths** — `agent-selector` 8.77 kB · `snapshots` 12.29 · `jobs` 14.77 · `add-source-modal` 8.32 · `snapshot-detail-drawer` 8.70 · **`blueprint` 18.32 (the LARGEST)** · `checkpoint-cleanup` 8.70 · `instance-list` 10.17 · `chat-interface` 16.15 — **plus** the pre-existing `initial` warning | **(a) re-derived at lane base** — command + verbatim output above. **CORRECTED (C-3): the census is 9, NOT 8** — `blueprint.component.scss` (18.32 kB) had been omitted. The guard is a **set difference** against this set (0 added, 0 removed), **never a count ceiling** — a ceiling can be loosened and silently lose detection | Phase 1's baseline capture (1.9), Phase 9's B6 |
| G11 | **The 16-family registry census** the 7-tab table binds to | 16 registered / **10 LIVE** / **6 DARK** | **(a) AND (b) — a DUAL status** (A-2): **(a)** re-derived at lane base (the registry SSOT `daemon/services/metrics_query/wiring/registry.py:64` + `wiring/adjudication.py:51-78`, read at `c26cec612`); **(b)** re-derived again at **Phase 2's freeze** (the registry file is re-read and the manifest's bindings re-checked). Counted **once** in the (a) bucket. **THE TWO TAXONOMIES ARE DISAMBIGUATED (W11) — they are coincidental 10/6 splits and MUST NOT be conflated:** **LIVE/DARK (availability — the axis the tabs bind):** LIVE = F0.3, F4, F5, F8, F9, F10, F11, F12, F13, F17 (10); DARK = F1, F3, F6, F7, F18, F21 (6). **FULL/PARTIAL (adjudication coverage — a DIFFERENT partition: `adjudication.py:45` "10 full + 6 partial = 16 of 26"):** FULL = F0.3, F1, F3, F4, F5, F6, F9, F10, F12, F17; PARTIAL = **F7, F8, F11, F13, F18, F21**. **"6 partial" is NOT "6 dark"** — F8, F11 and F13 are **PARTIAL** yet **LIVE**, which is exactly why tabs 5 (Instances) and 6 (Checkpoint) must render: their families are live. Reading the two splits together would hide two live tabs | the tab table; Phase 2's exit criterion |
| G12 | **Spec line counts** (241 / 237 / 519 / 316 / 1,645) | informational | **NOT a gate** — no exit criterion depends on a line count. AM-26 replaced the line-count framing with a **per-assertion disposition table** (the real gate) | Phase 4's exit criterion |
| G13 | **Code constants** (2 h = 7200 s, the 15 s effective interval, the 4-value resources ladder, the 256-entry anomaly cap, the 8 s live poll, the 30 s/60 s/3600 s tiers) | n/a | **Not measurements** — code facts, re-read at lane base and re-read at each owning phase's freeze | Phases 3, 5, 6 |

**Audit conclusion:** **no MEASURED figure gating an exit criterion is
un-re-derived — 11/13 rows are (a) or (b)** (G1, G2, G3, G4, G4b, G5, G6, G7,
G10 are (a); G8 and G9 are (b); G11 is **(a) and (b)**, counted once in (a));
**G12 is explicitly not a gate** and **G13 is a code-fact set**. The split is
**9 (a) / 2 (b) / 1 not-a-gate / 1 code-facts = 13** (G11 counted once — the
circulating "9/3/1/1" figure double-counted it).

### 0.3 House conventions binding on this lane

Commit style `type: summary` · `.agents/tester/PACKS.md` registration for any
new pack (keep-both, newest-first) · TypeScript only via `frontend/`'s local
devDependency (never bare `tsc`/`npx tsc` outside `frontend/`) · the `agents/`
prompt-artifact tree stays untouched (161/161) while `.agents/` is in scope ·
targeted `git add` of new paths only · frozen-binary boot smokes pin
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly · `env -u POSTGRES_*` is
a silent NO-OP (scrub per variable) · never resolve live-shaped paths in test
scripts.

### 0.4 DISCLOSURE — the plan-review council's model mapping (carried into the release narrative)

> **The plan-review council's requested model `decision` is NOT in this
> deployment's allowed set** (`agentic`, `coding` only), so the governor
> **mapped it to `coding`** — the alias recorded in project metadata as the
> **known-poisoned upstream row** (D7: a deprecated-row drop, ~210 errors/day,
> which killed 6+ lane instances during the v0.27.15 gates).
>
> **The findings STAND.** All 4 criticals were independently re-verified by
> direct commands against the tree; 2 models converged over 2 refinement rounds
> with mutual concessions; and the fold author **independently re-verified
> C-1, C-3, C-4, W-7, W-9, W-12, W-14, W-6 and A-5** against this tree (the
> command/output pairs are recorded in `fold-audit.md` §G). **But the mapping
> fact belongs in the disclosure set and on the user's morning list** — the
> **D7 proxy-side repair remains user-owned.**
>
> **The approver's independent cold-context cross-check worker**
> (`82404cdb-8458-4a83-a5ba-00f1286e3a35`) found **B4** (the operator recipe
> omitted `ENSEMBLE_AUTH_ISSUER`) and **B5** (Phase 5's and Phase 7's exit
> criteria contradict each other on `pages/resources/`) — both **independently
> re-verified by the approver against the tree** before being folded. **The
> independent cold-context check is earning its keep.**
>
> **Three further disclosures ride the staged-release notes (see §9.1):** the
> **mini role has no T1 page surface** (W2 — capability-retained, exercised via
> the fallback and the renderer specs); the **baseline comparison is scoped to
> the central tabs** (W3 — a disclosed narrowing of commission scope item 3);
> and **`docs/runbooks/oauth2-auth.md` §1b is env-INCOMPLETE** (B4 — it omits
> `ENSEMBLE_AUTH_ISSUER`, so a user following it literally hits the exit-78
> refusal; **§9 is the canonical recipe source**; the runbook gap is a routed
> documentation item, owner: the auth-docs owner).

---

## 1. Objective

One `/metrics` page that answers the two questions the UI exists for — *"why
is this instance slow right now?"* and *"what changed overnight?"* — as **7
module tabs over producer-live data**, with a page frame that owns the central
time range and the previous-window baseline, a **Chart.js-backed interactive
panel** role and **one consolidated house SVG renderer** for the mini + fallback
roles, an honest `AUTH_NOT_CONFIGURED` empty state **on the central surfaces
only** (the resources tab stays lit), a same-release retirement of
`/resources` behind a param-preserving redirect, and a chart layer that labels
its units and reconciles its own arithmetic.

**Testable completion sentence:** on a fenced dev daemon with a scoped operator
token, all 7 tabs render their bound families with data, the `/resources` URL
lands on `/metrics?tab=resource` with its query params intact, **every panel**
carries a unit-labeled y-axis (the mini role renders no axis and no T1 surface
renders a mini), and — with auth OFF — the central tabs show the actionable
fail-closed state **while tab 1 still shows live data**.

## 2. Scope

### In scope

1. **Tab manifest** — FE-local typed config (`frontend/src/app/pages/metrics/tabs.manifest.ts`),
   discriminated `central | custom` slots, per `resource-refactor-architecture.md` §1.1,
   **widened to express all four module dispositions** (AM-8). **"Adding a
   series to a wired module is registration-only" is stated HERE, ONCE, next to
   the manifest (W4)** — and it means exactly two **FE-local typed-table**
   edits: **the family binding** (the manifest) **and a metric→unit vocabulary
   entry** (the unit map, Phase 6's 6.3). A genuinely new *metric name* that
   has no vocabulary entry renders **"unit unknown"** (never a guess) until
   the vocabulary names it — a typed-table edit, not a code change (A-3).
2. **7 tabs, all producer-live** (§4). Modules with no source are **hidden
   entirely** — planned manifest entries document them; they render nothing.
3. **Page frame above the tabs** — the central range selector + the
   previous-window baseline (FE-only client split, central tabs only, W ≤ 1 h).
4. **Consolidated SVG renderer** — the two hand-rolled engines merged into ONE
   house primitive, keeping the OLDER engine's capabilities plus the ruled-in
   mini-tier extensions (devicePixelRatio crispness, nearest-point hover via
   overlay+bisect, last-value marker, optional baseline line, viewBox
   responsiveness) — **capability-retained, with no page surface in T1** (R-2).
5. **Chart.js v4 wrapper** for the interactive-panel role + the intent→renderer
   registry + the same-commit governance amendment.
6. **Tab 1 = Resources on a custom slot** over its 6 existing support endpoints.
   Per-instance series through the central door: **deferred** (routed (b)).
7. **Chart correctness** — unit-labeled axes, per kind+unit-group panels, the
   attribution basis, a coverage figure displayed **verbatim** (never computed
   by the FE).
8. **Event overlay, FE-only shapes only** — visible anomaly-marker labels from
   the API words the endpoint already serves + an honestly-labelled client-side
   data-gap annotation. Durable restart/deploy events: routed (d).
9. **Route fate (D-3)** — retire `/resources`, same-release param-preserving
   redirect to `/metrics?tab=resource`, gear-menu + spec + **runbook** edits.
10. **Empty-state + honesty fixes (D-2)** — the widened door-state class table,
    the actionable `AUTH_NOT_CONFIGURED` state on the central surfaces, the
    per-source state model, the duplicate banners, dead Retry buttons, the
    page-scoped posture chip, the 900 px collision, the `chartSpecs`
    dark-filter/silent-slice gap, the inert families table, the dead `active`
    input, the 403 predicate, the copy SSOT.

### Out of scope (with reason)

| Out | Reason |
|---|---|
| Any `daemon/` code, migration, or `scripts/upgrade` change | The commission is FE-only. Two useful backend items are therefore ROUTED (see §7). |
| `agents/` (the agent-definitions tree) | Out of scope by commission; the prompt-artifact baseline (161) must stay byte-identical. `.agents/` IS in scope. |
| Any live-daemon contact, live auth config, live `.env` | D-2: enabling auth is a USER MORNING ITEM. Verification runs against a fenced DEV daemon. |
| Staging / promoting | The commission says stage-only at lane end, no promote in setup. |
| `CHANGELOG.md:141` ("FE package.json empty") | Historical record — stays unamended. |
| **A mini/sparkline surface** | R-2: no mini consumer exists in T1; the mini role is capability-retained and exercised via the fallback + renderer specs. |
| **Per-instance central series, the top-N attribution list** | Routed (b) and (m). |

---

## 3. The three binding rulings, folded as decisions

Full text in `decisions.md` Part I. **Unchanged by this fold** — the architect
review re-opens none of them, and neither does the fold.

| Ruling | What this plan does | Where |
|---|---|---|
| **D-1 CHARTING** | Chart.js v4 = the ONLY vendor engine, **interactive panels only**, **lazy-chunk-only**. ONE consolidated house SVG renderer for the mini + fallback roles. The "drop Chart.js if interaction is deferred" flip-condition is **void**. The governance amendment lands in the **same commit** as the dependency. **"FIRST PAINT" = THE APP SHELL** (LEADER RULING 1 — normative; it matches §10 criterion 8's operationalisation and the builder's `initial` budget basis). **The corrected safety mechanism (AM-2 + C-1):** a **lazy** leak trips **no** budget — the build stays green and both FE packs stay green — so the guards are (i) a numeric initial-total delta, (ii) an initial-chunk marker scan, (iii) an **import-shape + import-site** assertion, and (iv) a **route-fetch-set growth bound**. The page-scoped import-graph marker assertion is **retained anyway**, so the D-1 guarantee holds under **both** readings (app shell **and** the `/metrics` page). The guards are **co-located in Phases 1, 4, 5 and 9** — the exposure window is Phases 1–8. | Phase 1 (dependency + amendment + the first guard), Phase 4 (the engines + the guard re-run), Phase 5 (the guard re-run), Phase 9 (B1–B8), Phase 0 (the audit-doc amendment) |
| **D-2 AUTH POSTURE** | The central door's fail-closed posture is **unchanged**. The FE distinguishes the door's failure **classes** (widened to ten, AM-4) and renders an **actionable** empty state for `AUTH_NOT_CONFIGURED` — **on the central surfaces only** (AM-1). The operator recipe lives in §9 and is **not executed** by the lane. | Phase 8 (the class table + the actionable state), Phase 2 (the timeout), Phase 9 (L1b/L2/L4), §9 (the recipe) |
| **D-3 ROUTE FATE** | `/resources` is retired in the same release, behind a **param-preserving** redirect to `/metrics?tab=resource` (mechanism + precedence named, AM-3). | Phase 7 |

---

## 4. The derived 7-tab table + the surface→intent→renderer→granularity map

### 4.1 The 7 tabs (all **producer**-live)

"DARK" means the door is unreachable in production because live auth is OFF —
an install posture, not a producer fact.

| # | Tab | Slot | Bound families (planned in brackets) | Producer (in-tree) | Source endpoints |
|---|---|---|---|---|---|
| 1 | **Resources** | `custom` | — (own module; F0.3 spine is a stat-only row-count family) | resource_monitor facade: in-process `TierRing` + the PG retention ladder | `GET /api/resources/live`, `/history`, `/anomalies`, `/anomalies/{id}/evidence`, `/traceback`, `/self-cost` (**6**) |
| 2 | **Runtime** | `central` | **F9** [F1, F3] | readiness composite (`w2.py:120-182`); [wedge_capture (`w1.py:94-189`) — joins when R-ii lands] | `/api/metrics/families` · `/snapshot` · `/series` |
| 3 | **DB Pool** | `central` | **F4, F5** [F6] | readiness pool sampling (`w1.py:194-228`); wedge collector (`w1.py:238-268`); [PG census (`w1.py:275-334`) — joins when R-i lands] | same |
| 4 | **Jobs / Queue** | `central` | **F10, F12** | job_queue admission (`w2.py:188-229`); sweeps (`w2.py`) | same |
| 5 | **Instances** | `central` | **F13, F11** | instances census (`w2.py:392-414`); worker_pool `get_stats` (`w2.py`) | same |
| 6 | **Checkpoint** | `central` | **F8** | maintenance gauges (`w2.py:89-114`) | same |
| 7 | **MCP** | `central` | **F17** | mcp/init_guard (`w3.py:51-88`) | same |

**Tab 1's window ladder:** `15m / 1h / 24h / 7d` (its own vocabulary,
`timeline-tab.component.ts:60`). **Central tabs' ladder:** `15m / 30m / 1h / 2h`
(2 h = the substrate ceiling — ADJ-iii).

### 4.2 Surface → intent → renderer → granularity (AM-9, per R-1/R-2)

The roles are assigned to **surfaces**, not to engines in the abstract. **This
table is the mapping the implementation must satisfy.** The manifest's own
contract — *"adding a series to a wired module is registration-only"* — is
stated in §2 (W4: **family binding + metric→unit vocabulary entry**, both
FE-local typed tables) and is **not** restated here.

| Surface | Intent | Renderer | Granularity | Role of the axis | Notes |
|---|---|---|---|---|---|
| Central tab panels (tabs 2–7) | `trend` | **Chart.js panel** | **one panel per metric-kind + unit group**, the kind read from the **wire** (`chart_role` per metric — `metrics.model.ts:164`, resolved per series at `charts-tab.component.ts:222`) | unit-labeled y-axis | A family registering both a gauge and a counter (F4 `w1.py:194-228`; F9 `w2.py:120-150`) yields **two** panels — a mixed-kind panel breaks counter-honesty and falsifies the unit |
| Resource timeline panel (tab 1) | `trend` | **Chart.js panel** | **three unit classes** → three panels: memory (MB, shared scale), CPU (%), counts (`threads`, `instances_active` — `svg-timeline.component.ts:56-63`) | unit-labeled y-axis | The anomaly overlay rides as a **house plugin**; the drill-down click rides the wrapper's `pointClick` |
| The tab-1 **baseline** | `trend` (compare) | **Chart.js panel** (normal path) — the house SVG carries the baseline as **fallback capability** | per panel | — | **The custom slot's baseline toggle is DISABLED with a reason** — the resources door has no previous-window offset (AM-14) |
| Per-tab stat strips (stat-role families) | `stat` | the panel wrapper's stat rendering (no chart, no axis) | per family | no axis (a stat is a value) | Fed by `/api/metrics/snapshot?family=<F>` |
| Family catalog drawer | `stat` | table (no chart) | per family | n/a | The dark families are **findable** (sort/filter, `aria-sort`) |
| **Mini / sparkline** | `sparkline` | **house SVG** | n/a | **no axis** | **NO page surface in T1** (R-2) — capability-retained; exercised via the **mount-failure fallback** + the **renderer specs** |
| Chart.js mount failure | `trend`/`sparkline` | **house SVG fallback** | the failed panel's shape | the fallback's own rules | **Surfaced with a badge — never a silent downgrade** (the house forbids it) |

**The completion sentence follows from this table:** "every **panel** carries a
unit-labeled y-axis; the mini role renders no axis."

### 4.3 Hidden and dropped modules — with reasons

| Module / family | Disposition | Reason | Owner |
|---|---|---|---|
| LLM token usage (F14) | **HIDDEN entirely** (planned manifest entry, **no-family variant**) | Adjudicated OUT; `usage_metadata` is captured then dropped, "zero consumers ref-wide". | metrics lane (Tier-3) |
| LLM request latency (F15) | **HIDDEN entirely** (planned, no-family variant) | Adjudicated OUT; only WARNING log lines exist. | metrics lane (Tier-3) |
| Source circuit-breakers / token buckets | **HIDDEN entirely** (planned, no-family variant) | Uncatalogued; denials are uncounted and both are process-local. | metrics lane (Tier-3) |
| F1 loop lag, F3 wedge captures | **BOUND to Runtime**, availability-gated (dark today — false-dark) | The manifest already binds them; when R-ii wires `capture_active` they appear with **ZERO FE change**. | R-ii (already routed) |
| F6 PostgreSQL census | **BOUND to DB Pool**, availability-gated (dark today — false-dark) | The census polarity inversion (R-i) is the only blocker. | R-i (already routed) |
| F7 checkpoint op bytes | **PLANNED, unbound** | `series_absent` — no series published. | metrics lane |
| F18 mcps gateway sessions | **PLANNED, unbound** | `series_absent`. | metrics lane |
| F21 upgrade journal twin | **PLANNED, unbound** | `series_absent` (needs an install dir). | metrics lane |
| F0.3 resource spine | **PLANNED, unbound** | A stat-only row-count family — never a resource measurement. | metrics lane |
| F2 thread CPU/RSS | **DROPPED** (never planned) | Adjudicated OUT + roster-dropped. | — |
| F16, F19, F20, F22 | **DROPPED** (never planned) | Adjudicated OUT — names force-absent. | — |

**Rendering rule (ADJ-vi):** a bound family is charted **only while it is
live**; a bound family the door reports dark is **listed with its reason**,
never charted and never silently dropped; the FE's own selector-ceiling slice
is **surfaced**, never silent.

---

## 5. Phase map

| Phase | Name | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| 0 | Housekeeping — the audit-doc what-changed-and-why amendment | Record the 4-engine proposal → measured reality → D-1 supersession chain, incl. the not-chosen zero-dep counter-option | 6 | independent | pending |
| 1 | Charting dependency + same-commit governance amendment + the **first bundle guard** | Land Chart.js v4 + the canvas-mock devDep with the pins/pack/doc amendments; extract `chart-theme.ts`; install the numeric bundle guard + the warning-baseline capture | 9 | tight with 0, 4, 5, 9 | pending |
| 2 | Tab manifest + 7-tab shell + `?tab=` deep links + the **per-source state model** + the **route-chunk marker constant** (B3) | FE-local typed manifest (4 dispositions), the 7-tab shell, deep-links, the door timeout, the per-source state model, the marker constant | 14 | tight with 3, 5, 7, 8, 9 | pending |
| 3 | Page frame — range selector + baseline | The central-sourced range control with per-tab vocabulary; the server-anchored client-split baseline (central tabs only); the request-generation discipline | 12 | tight with 2, 4, 5, 6 | pending |
| 4 | Renderer consolidation — house SVG + Chart.js wrapper + registry + the **second bundle guard** + the per-tab panel composition | Merge the two engines into one; build the panel wrapper; the intent→renderer registry; re-specify the canvas-fatal e2e legs; **compose the per-tab panels** (C-2's moved half of 2.4) | 15 | tight with 1, 2, 3, 5, 6, 9 | pending |
| 5 | Tab 1 (Resources, custom slot) | Relocate the resource module; Live strip + Timeline panel + anomalies panel; the panel's own range control; the per-source state; **the third bundle-guard re-run** (W-4) | 14 | tight with 2, 3, 4, 6, 7, 9 | pending |
| 6 | Chart correctness — units, panel granularity, attribution | Per kind+unit-group panels; the metric-name unit vocabulary; the coverage displayed verbatim | 7 | tight with 4, 5, 3 | pending |
| 7 | Route fate (D-3) — retire `/resources` + redirect | Param-preserving redirect (mechanism named); gear menu; the widened sweep; the operator runbooks | 9 | tight with 2, 5, **8** | pending |
| 8 | Empty state + honesty (D-2) | The widened class table; the actionable `AUTH_NOT_CONFIGURED` state on the central surfaces; the page-scoped posture chip; the copy SSOT | 12 | tight with 2, 4, 6, **7** | pending |
| 9 | Verification + close-out | The **fenced mint** (9.3a) + the boot (9.2) + the four legs; the pixel list split by posture (9.7a/9.7b); the bundle guard B1–B8; the dev-DB teardown; the routed-out register; the operator recipe | 13 | tight with all | pending |

### Coupling map (AM-7: row 8 corrected; the 7↔8 edge is now in the task graph)

| | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| **0** | — | loose | independent | independent | loose | independent | independent | independent | independent | loose |
| **1** | loose | — | independent | independent | **tight** | loose | independent | independent | independent | tight |
| **2** | independent | independent | — | **tight** | **tight** | **tight** | independent | **tight** | **tight** | loose |
| **3** | independent | independent | **tight** | — | **tight** | **tight** | **tight** | independent | loose | loose |
| **4** | loose | **tight** | **tight** | **tight** | — | **tight** | **tight** | independent | independent | loose |
| **5** | independent | loose | **tight** | **tight** | **tight** | — | **tight** | **tight** | independent | loose |
| **6** | independent | independent | independent | **tight** | **tight** | **tight** | — | independent | independent | loose |
| **7** | independent | independent | **tight** | independent | independent | **tight** | independent | — | **tight** | loose |
| **8** | independent | independent | **tight** | loose | **tight** | independent | **tight** | **tight** | — | loose |
| **9** | loose | tight | loose | loose | loose | loose | loose | loose | loose | — |

**The corrected couplings (AM-7 + C-2):**

- **2 ↔ 4 is TIGHT (C-2).** Phase 2's task 2.4 originally required the
  **renderer registry** that only Phase 4's task 4.8 creates, while four
  declarations asserted 2↔4 independence — a numeric-order execution hit a
  phase-2 freeze whose tab bodies could not compile. **Task 2.4 is now SPLIT**
  (the tab bodies + the family binding + the fetch + the availability
  surfacing stay in Phase 2; the registry hand-off and the per-kind+unit-group
  panel composition MOVE to Phase 4's new task **4.15**), and the map's 2↔4
  cells read **tight** in both directions. Phase 4's exit re-asserts 2.4's
  two-panels property, mirroring the plan's own two-step-migration idiom
  (AM-27).
- **8 ↔ 4 and 8 ↔ 6 are TIGHT.** Task 8.10's data-end marker is a **renderer**
  capability (Phase 4) and its meta interacts with Phase 6's derived-spacing
  meta — a lane sequenced by the map would otherwise reach Phase 8 before the
  capability exists. Row 8 of the map now declares both, and Phase 8's
  Coupling block says so.
- **7 ↔ 8 is TIGHT and now has a TASK-GRAPH EDGE:** task **7.1 `Depends On:
  Phase 8`** — the redirect may not land before the empty state is fixed, or
  `/resources` lands on the contradictory double banner the phase-8 work
  exists to remove.

---

## 6. Verification plan

### 6.1 The data-bearing environment route (RESOLVED)

**Never the live daemon; never `:9797`; live config out of bounds.**
Everything runs in the lane worktree against a **fenced DEV daemon on
`127.0.0.1:8079`**.

**Pre-flight (any failure ⇒ STOP, not "proceed and watch"):** — P1–P9,
corrected by the plan-review's **C-4** (a quiet-failure class: no daemon module
loads `.env`, so a scrub alone leaves the daemon with **no** `POSTGRES_*` and
the sqlite fallback gets **persisted**), **W-7** (there is no `config.yaml`
database stanza), **W-8/W-9** (the `ensemble.json` assertion must be pinned to
the JSON path and the pre-seed must be specified), and **A-6** (`DAEMON_PORT` is
inert).

| # | Assertion | Check | Fail ⇒ |
|---|---|---|---|
| P1 | The worktree `.env` is the dev fence | `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5432`, `POSTGRES_DB=ensemble_dev`, `POSTGRES_USER=ensemble_dev_app`, `POSTGRES_PASSWORD` present, and **NO `PORT` at all** (so no live-port binding path exists). **`DAEMON_PORT=8079` is NOT asserted** — it is inert (A-6); the port clause is P7's | STOP |
| P2 | The **loaded** `POSTGRES_DB` is the dev DB, never prod | after the `.env` source (step 2 of the boot sequence): the loaded `POSTGRES_DB == ensemble_dev` **and** `!= ensemble_prod` | STOP |
| P3 | The `ensemble.json` pin is pinned **to the JSON path** (W-9) | `jq -e '.database == "postgres" and .postgres.db == "ensemble_dev"' <data-dir>/ensemble.json` — **never** a literal `ensemble_prod` substring check (the live file's `$.postgres.db` is `"ensemble"`, which a substring checker passes) | STOP |
| P4 | A scratch data dir, **with an exact pre-seed** (W-8) | `ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data` with `ensemble.json` pre-seeded to **exactly** `{"database":"postgres","postgres":{"host":"127.0.0.1","port":5432,"db":"ensemble_dev","user":"ensemble_dev_app","password":"<the dev password>"}}` — the **full** `postgres` block, because a pre-seed without host/user/password leaves the connection parameters to the file's own (possibly prod) values. **P3 is re-run after P4** with the same `jq` expression | STOP |
| P5 | The sweep guard is pinned EXPLICITLY | `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` — **never** `env -u` on it | STOP |
| P6 | The env is scrubbed **per variable**, following the house **family rule** (AM-29) | prefix `POSTGRES_*` **and** `PG*` (PGPASSWORD is a known ambient vector), plus `BLUEPRINT_*` and suffix `*_API_KEY` — scrubbed one by one; `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names) | STOP |
| P7 | The **effective** port is `PORT=8079`, re-exported **AFTER** the `.env` source and **unconditionally** (LEADER RULING 3) | `PORT` is re-exported **after** the source (the live install's `.env` carries `PORT=9797` — sourcing first and exporting after is what closes the live-port bind); the export is **unconditional** (never inherited from a sourced env); the **effective** port is then asserted **from the running daemon's `/livez`**, never from the intent | STOP |
| P8a | `config.yaml`'s effective **port** stanza (W-7) | `config.yaml` carries **no** database stanza (`grep -i "postgres\|database" config.yaml` = 0 hits) — only `daemon.host`/`daemon.port` (`:111-113`); the port stanza is inspected and must resolve to 8079 | STOP |
| P8b | The running daemon's **actual database**, read back (the load-bearing half of AM-29's P8) — **the mechanism is the STARTUP LOG** (W1): `daemon/ensemble_config.py:75` logs `Loaded ensemble config: database=postgres`, and the daemon prints the engine DSN it resolves; **no endpoint exposes the DB identity** (`daemon/routers/database.py` exposes only `POST /api/database/switch`), and **no new endpoint is added** — a daemon change would break the FE-only constraint | the daemon's resolved database identity is read back **from the running daemon's startup log** (not from a file) and must be `ensemble_dev` | STOP |
| P9 | The boot path is the one that can reach the dev DB (C-4) | the boot sequence below is executed in the **stated order**; a 200-with-empty-catalog from `/api/metrics/families` is a **FAIL**, not a success (see L1's non-emptiness threshold) | STOP |

**The boot sequence (C-4 — ORDERED; the order is the fix):**

```
(1) scrub the ambient env per variable                    # P6
(2) source the WORKTREE's .env into the daemon's env      # set -a; . ./.env; set +a
                                                        #   (the house dev.sh:70-76 idiom)
(3) assert the LOADED POSTGRES_DB == ensemble_dev          # P2  (!= ensemble_prod)
(4) re-export PORT=8079 — UNCONDITIONALLY, AFTER the source  # P7 (LEADER RULING 3)
(5) boot:  python -m daemon                              # the __main__ path (preflight + boot sweep run)
(6) assert the EFFECTIVE port from /livez                # P7's second half
    assert the daemon's ACTUAL database == ensemble_dev   # P8b
    assert the ensemble.json in $ENSEMBLE_DATA_DIR        # P3, re-run after P4
    assert the anyComponentStyle + initial warning SET    # the Phase-1 captured baseline set
```

**Why step (2) is load-bearing (C-4's code-path conclusion, falsifiable at
implementation time by three cheap checks):** no daemon module loads `.env` —
`grep -rnE "dotenv|load_dotenv" daemon/ --include=*.py` returns **2 hits, both
prose in docstrings** (`daemon/config.py:3698`, `:3897`); `daemon/config.py:6571`
substitutes `${VAR}` from `os.environ` only; `daemon/ensemble_config.py:81-83`
auto-detects postgres only when **both** `POSTGRES_HOST` **and** `POSTGRES_DB`
are present; `:90-91` logs *"No PostgreSQL ENV vars detected, defaulting to
sqlite"*; and `:95` **persists** that choice into `ensemble.json`, which then
**outranks the env on every later boot from that tree**. Without step (2), the
scrub of step (1) removes the **only** source of `POSTGRES_*` and the daemon
silently runs on sqlite — a **quiet** failure that still answers 200 with an
empty catalog.

**C-4 was closed on CODE-PATH evidence, not on an executed boot.** The three
implementation-time assertions that would falsify it (named in Phase 9's task
9.2): `env | grep POSTGRES` (expect **nothing** before the `.env` load) · the
boot log line `No PostgreSQL ENV vars detected, defaulting to sqlite` (must
**not** appear) · `cat $ENSEMBLE_DATA_DIR/ensemble.json` (must show
`.database == "postgres"` and `.postgres.db == "ensemble_dev"`).

**Leg L1 — data-bearing (auth ON, scoped operator bearer).** **THE MINT IS
A DATABASE WRITE (B1)** — it INSERTs a row into `auth_bearer_tokens`
(`daemon/repositories/auth_session/models.py:173`) in whichever database it
resolves — and the mint resolves its engine **independently of the daemon
boot** (`daemon/auth/cli.py:127 resolve_engine()` →
`EnsembleConfig.load_or_create(data_dir)`), with the **env winning over the
file's block** at the factory (`daemon/repositories/factory.py:346-350`) and the
auto-detect **persisting** a prod-pinned `ensemble.json` when no `data/` dir
exists (`daemon/ensemble_config.py:81-95`). The lane's **only** write is
therefore inside the **dev** database, and Phase 9's task **9.3a** proves it
four ways: the mint runs **inside the scrubbed shell** with
`ENSEMBLE_DATA_DIR=/tmp/fe-t1-dev-data`; the **resolved** target is proved
before the mint from **both** resolution inputs (no `POSTGRES_*` in the scrubbed
env **and** `$.postgres.db == "ensemble_dev"` in the pre-seed) — never from the
env var alone; the token is read back **from `ensemble_dev`**; and a
**write-detecting live fence** records the live `ensemble_prod` bearer-row count
**before and after** the mint and requires it to be **identical** (the lane's
other fence — symlink / `state.json` / uptime / "0 daemon requests" — **cannot**
see a DB write, so this assertion is what makes a live write impossible to
pass as green). The mint uses the **narrower** `--scopes "metrics:read"`.

Auth ON requires
`client_id` + `client_secret` + `issuer` (https) + non-empty `allowed_origins`
— else the daemon refuses to boot with exit 78 (`daemon/config.py:3930-3965`,
`:4009`). The bearer lane **never contacts the IdP** (the middleware verifies a
static bearer locally — `daemon/middleware/auth.py:358-377`, `_verify_bearer` →
`lookup_bearer` `:440-447`; no OIDC discovery fetch at boot), so dev-local
**placeholder IdP config values** satisfy the validator without a live IdP.
**Disclosure:** the issuer/discovery URL is a placeholder and the OIDC login
flow is NOT exercised. Mint: `python -m daemon.auth.cli issue-token --label
fe-t1-operator --scopes "maintenance:read"` (the door accepts
`maintenance:read` OR `metrics:read` — `daemon/routers/metrics.py:57-63`; the
plaintext prints ONCE and never enters a repo file). The browser presents it
via the Playwright context's `extraHTTPHeaders: { Authorization: 'Bearer
<token>' }` — **no production FE change**. **The auth env vars are exported
BEFORE the boot (Phase 9's task graph), not after it** — auth has no live
toggle (`config.yaml:133`), so enabling it after a boot would leave the daemon
serving the auth-OFF posture.

**Leg L1 — the non-emptiness threshold (W-11; a 200-with-empty-catalog is a
FAIL):** `/api/metrics/families` must return **≥ 1** registered family **and**
**≥ 1** of the 7 bound families; `/api/metrics/snapshot?family=F9` must return
**≥ 1** series; `/api/resources/live` must return a **non-zero**
`daemon_rss_mb`. The observed counts are recorded in the evidence.

**Leg L1b — auth required, no bearer (AM-4).** Auth ON, **no** credential ⇒
**401** ⇒ the **sign-in panel** (`auth_required`) — not `unexpected`, not a
dead Retry. This is the FIRST state every real user meets.

**Leg L2 / L4 — auth-absent (AM-1).** The same dev env with auth **disabled**
⇒ 503 `AUTH_NOT_CONFIGURED` ⇒ the actionable empty state renders **on the
central surfaces** (the catalog drawer + the 6 central tab bodies), while
**tab 1 renders its live strip + timeline with real data** (the resources door
is fail-open). **L4 is the AM-1 capture proving exactly that.**

**Leg L3 — mixed-unit correctness.** The memory panel's axis is labelled in MB,
the CPU panel's in %, the counts panel's in counts, and the three do not share
an axis.

### 6.2 Pixel-verification list

Captured via the canonical script shape (`research-findings.md` §7 — Node
Playwright 1.60.0 from `frontend/node_modules`, `chromium --no-sandbox`,
`domcontentloaded` → best-effort `networkidle` (15 s cap — never settles) →
**fixed 3 s settle** → screenshot), against the **dev** daemon at
`127.0.0.1:8079`.

**The capture order is by auth posture (AM-6):** **9.7a** = PX-1…7 + PX-9…14
with **auth ON**; then the auth-OFF restart; **9.7b** = PX-8. Each restart is
followed by a **`/livez` re-assertion**.

| # | Pixel list item | Posture | Viewport | Assert |
|---|---|---|---|---|
| PX-1 | **Tab 1 Resources — WITH DATA**: Live strip + Timeline panel + anomalies panel | auth ON | 1600×1000 | real values render; the anomalies panel lists rows; the process-local disclosure is visible |
| PX-2 | **Tab 2 Runtime** | auth ON | 1600×1000 | F9 panels render; the family line lists the bound families with their availability |
| PX-3 | **Tab 3 DB Pool** | auth ON | 1600×1000 | F4/F5 panels render |
| PX-4 | **Tab 4 Jobs/Queue** | auth ON | 1600×1000 | F10/F12 render |
| PX-5 | **Tab 5 Instances** | auth ON | 1600×1000 | F13/F11 render |
| PX-6 | **Tab 6 Checkpoint** | auth ON | 1600×1000 | F8 renders |
| PX-7 | **Tab 7 MCP** | auth ON | 1600×1000 | F17 renders |
| PX-8 | **AUTH_NOT_CONFIGURED actionable empty state** | **auth OFF** | 1600×1000 **and 900×1000** | one panel on the central surfaces; cause named; runbook pointer; NO Retry; no duplicate banner; the posture chip (not the global dot) is not green; no 900 px collision; **and tab 1 still shows live data** |
| PX-9 | **`/resources` redirect** | auth ON | 1600×1000 | lands on `/metrics?tab=resource`; **query params preserved** (e.g. `/resources?window=24h&x=1` ⇒ `/metrics?tab=resource&window=24h&x=1`), asserted on the **parsed** params |
| PX-10 | **Mixed-unit y-axis correctness** | auth ON | 1600×1000 | memory panel axis labelled (MB); CPU panel axis labelled (%); counts panel labelled; separate scales; no unlabeled mixed axis |
| PX-11 | **Baseline / previous-window comparison** (central tab) | auth ON | 1600×1000 | the baseline series renders and is labelled as the previous window; the fidelity disclosure is visible; **the custom tab's toggle is disabled with its reason** |
| PX-12 | **Range switch** | auth ON | 1600×1000 | switching tabs swaps the option set (visible enforcement) — a central tab offers no 24h/7d; the custom tab has its own labelled control |
| PX-13 | **Anomaly marker labels + data-gap annotation** | auth ON | 1600×1000 | marker labels carry the API words (kind/severity/peak); any gap annotation says "data gap", never "restart" |
| PX-14 | **Dark-family listing** | auth ON | 1600×1000 | a bound-but-dark family appears in the family line **with its reason**, not charted |

Every capture is GET-only (assert 0 non-GET in the API census) and the live
daemon is never contacted.

### 6.3 Bundle guard (AM-2 + C-1 — the architecture fixed three ways, then four)

**The corrected mechanism (stated plainly):** the Angular `initial` budget
counts entry points + their transitive **static** imports; **lazy chunks are
counted by NO budget** (only `initial` + `anyComponentStyle` are configured).
Therefore **a lazy Chart.js leak trips nothing** — the production build stays
green and both FE packs stay green. The plan's original claim ("a static-import
leak hard-fails the production build … and both FE packs") was **false for the
lazy case** and is withdrawn. Worse (C-1): **a STATIC `from 'chart.js'` inside
the lazily-loaded metrics tree satisfies B1, B2 and B3** — the initial total
does not move, no initial chunk carries the marker, and the import site is
inside the tree B3 requires — because a module reachable only from a lazy
graph is extracted into a **lazy-only sibling chunk**, not into the initial
set. On the real dist the `/metrics` route chunk statically imports
`chunk-6Y6F4JKW.js` (43,688 B) and `chunk-AOZCSBSC.js` (1,348 B), **both
lazy-only** (absent from the builder's 36-row initial table) — so the route
chunk need not even grow. **Hence B3 gates the SHAPE and B4 measures the
route FETCH SET** (the route chunk + its direct static deps), which is the
measurement that actually catches the static form.

**"First paint" = the APP SHELL** (LEADER RULING 1, normative — it matches
§10 criterion 8's operationalisation and the builder's `initial` budget basis).
The page-scoped import-graph marker assertion is **retained anyway**, so the
D-1 guarantee holds under **both** readings.

| # | Guard | Threshold | Where |
|---|---|---|---|
| B1 | **Numeric initial-total delta** vs the **frozen lane-base baseline 5,909,018 B** (the builder's own "Initial total" basis, 36 chunk files) | **\|Δ\| ≤ 2 kB** | Phase 1 (1.9), Phase 4 (4.14), **Phase 5 (5.14)**, Phase 9 (9.8a) — **co-located**, because the leak window spans Phases 1–8 and Phase 5 is the other chunk-graph-changing phase (W-4) |
| B2 | **No `chart.js` marker in ANY initial chunk** | **0** hits across the 36-file initial set | Phase 1 (1.9), Phase 4 (4.14), Phase 5 (5.14), Phase 9 (9.8b) |
| B3 | **Import SHAPE + import SITE** (C-1, AM-15) | **every** `chart.js` import is the **dynamic** `await import('chart.js')` form — **0** static `from 'chart.js'` sites anywhere; the wrapper's own home is **under `src/app/pages/metrics/**`** (a named relocation task, 1.7/4.4), so the location rule holds by construction. The **planted variants** are now **both**: an eager import (in the initial set) **and** a static import inside the lazy metrics tree (the case that slips through B1/B2) | Phase 1 (1.2c, 1.7, 1.9), Phase 4 (4.4, 4.14), Phase 5 (5.14), Phase 9 (9.8c) |
| **B4** | **The new lazy chart chunk's size AND the `/metrics` route FETCH SET's growth** (C-1: B4 had no threshold and no baseline; G4 was an orphan) | the new chart chunk's real **min + gz ≤ 65,536 B (64 KiB)**; **AND** the `/metrics` route fetch set grows by **≤ 65,536 B** vs the **496,338 B** baseline (G4b). **The route chunk is located by a ROUTE-KEYED locator, never by a content marker (B3)** — the pre-lane tool keyed on `'metrics-empty-panel'` / `'anomaly-marker'`, which Phases 7 and 8 invalidate; the key the lane controls is the **dedicated route-chunk marker constant the metrics shell exports** (Phase 2's task), and the locator asserts the key resolves to **exactly one** chunk, reporting **absent** (never mis-resolved) when it does not | Phase 9 (9.8d) — the comparison leg that G4's "Phase 9's new-chunk comparison" named but that did not exist |
| B5 | The per-component **`anyComponentStyle`** figures for the new components | each **< 8 kB** (the 24 kB cap is a hard build error) | Phase 4 (4.11), Phase 9 (9.8e) |
| B6 | The budget **warnings** as a **SET DIFFERENCE** against the baseline set captured **mechanically** from the Phase-1 production build log (C-3) — **never a count ceiling** (a ceiling can be loosened and silently lose detection) | the warning set must equal the captured baseline set **exactly**: **0 added, 0 removed** | Phase 1 (1.9 captures the baseline), Phase 9 (9.8f) |
| B7 | **Both FE packs green** + the **161/161** prompt-artifact baseline | 2/2 green; 161/161 | Phase 9 (9.9, 9.10) |
| **B8** | **The baseline artifact is the single source** (A-8) | the guards read `bundle-baseline.json`; a number duplicated only in plan prose is **not** accepted as a baseline | Phase 1 (1.9), Phase 9 (9.8) |

**The baseline's provenance (E-1, re-derived at lane base, with the command and
its verbatim output in §0.2):** the fresh production build at lane base
measures **5,909,018 B** initial / **90,982 B** headroom. The digest's
5,909,018 B **total** was correct; its `89,982 B` headroom had a 1,000 B digit
slip; AM-2's `5,115,820 / 884,180` **undercounts by 793,198 B** (it summed
only the index.html-referenced files, omitting the builder's 23 transitive
static-import chunks). The G10 census is **9** (C-3), not 8. Full method:
`research-findings.md` §14; the machine-readable artifact:
`bundle-baseline.json` + `bundle-baseline.measure.js`.

**An EAGER leak does break the build** against the corrected baseline: the
headroom is 90,982 B and chart.js's raw minified size is ~250–400 kB ⇒ the
`initial` **error** budget trips. The unsafe case is the LAZY one — and, per
C-1, the **static-inside-a-lazy-tree** case, which is why B3 gates the shape
and B4 measures the route fetch set.

### 6.4 Expected FE pack / test additions (SPECIFIED here — not built by this plan)

**Jest units (new):** the manifest shape + the four dispositions; the shell's
deep-link read/write and the unknown-id fallback; the range frame (per-tab
vocabulary, the client-split baseline maths, the server-anchored split under a
faked clock offset, the ≤1 h guard, the clamped-payload refusal); the renderer
registry (the intent→renderer map, `supports()`, the never-silent fallback);
the **house SVG renderer** (ported from the 241 L + 237 L specs, with the
mini extensions); the **Chart.js panel wrapper** (the four contract clauses,
the DOM proxy's `data-render-mode`); the resource tab (the body order, the
panel-scoped range, the anomaly disclosure, the drill-down policy); the
timeline correctness (the three unit classes, the toggle path, the attribution
basis, the coverage); the empty state (each class's copy/action/Retry
policy, the 403 branches); the redirect (the end-to-end **parsed**-params
assertion, the null fallback, the `tab` precedence); the chart-specs honesty
(dark families do not consume the ceiling; the FE's own truncation is
surfaced); the door timeout (the `timeout` class); and the per-source state
(a central failure does not blank a custom tab).

**Playwright legs (new, in `frontend/e2e/`):** the 7-tab deep links; the
`/resources` redirect (+ query-param preservation); the `AUTH_NOT_CONFIGURED`
empty state; the mixed-unit y-axis correctness. The existing gate configs are
the templates; the resources gate must be retired or re-pointed with the
route, and the new legs need their own config or a widened `testMatch`
**without** sweeping the daemon-dependent specs.

**`PACKS.md` registration (keep-both, newest-first):** the amended/new FE gate
pack rows, with the node counts **re-derived mechanically at gate time**.

**The Leg-7 amendment mechanics (exact):** the pack's `package_json_dependency_diff`
leg is re-purposed from *zero-diff* to *expected-dependency-set* — the
`frontend/package.json` diff must contain exactly `chart.js` (dependencies) and
`jest-canvas-mock` (devDependencies), asserted **by name**, never by a raw
line count.

---

## 7. Routed-out register

Each item: owner + reason + the exact handoff. **The lane does not build any
of these — except (n), which Phase 7 retires** (the `/resources` Playwright gate
must die with the route it drives).

| # | Routed item | Owner | Reason / handoff |
|---|---|---|---|
| a | **Enable auth on live** (the operator recipe) | **USER (morning item)** | D-2. The recipe is §9; the lane writes it and never executes it against live. |
| b | **Per-instance CPU/mem through the central door** | metrics lane (own commission) | A rule + budget + retention collision: A10/A18 excludes per-instance labels; the AM-2 closed-cardinality guard forbids an open label key; `GLOBAL_SERIES_CAP = 2000` vs ~6,324 prod instances; the ring is 2 h in-RAM while the durable tier is PG. Tab 1 covers the need over `/api/resources/*` with zero backend work. |
| c | **LLM tokens / LLM latency / source-breaker families** | metrics lane (Tier-3 waves) | New families + instrumentation; the FE cost after each lands is one manifest flip. |
| d | **Durable anomaly/restart events spanning restarts** | metrics lane (small commission) | The journal is process-local. The lane ships an honestly-labelled **data-gap** annotation instead and says plainly what it knows. |
| e | **Mermaid's 972 kB gz initial-bundle elephant** | FE debt lane | It is the real initial-set elephant (~80 % of the initial bundle, loaded on every page) — not this lane's surface. |
| f | **T1.5 backend `end_offset_s` / `compare` params** on `/api/metrics/series` | metrics lane (small, optional) | T1 uses the FE-only client split (central tabs, W ≤ 1 h). |
| g | **Retire `/api/resources/control-capabilities` + `POST /api/resources/profile`** | backend hygiene commission | Dead code, but retiring them rewrites the scope-manifest census pin — a daemon change, out of this lane's scope. |
| h | **F1/F3/F6 false-dark fixes (R-i, R-ii)** | metrics lane (already routed) | The manifest already binds the three families; they appear with **zero FE change** when the fixes land. |
| i | **F0.3 spine-health `TypeError`** | metrics lane (already routed) | A daemon defect; the FE renders the family's value as the API serves it. |
| j | **Central windows > 2 h** | metrics lane (unpaved) | Multi-axis: ring depth, `POINT_BUDGET`, eviction, cursor expiry. |
| k | **BE-served manifest** (shape A) | metrics lane (T2+) | Flip only when module-onboarding cadence makes FE commits a real bottleneck. Shape (B) is forbidden. |
| l | **ECharts / ApexCharts / Gridster2** | deferred with triggers | ECharts: (t1) A10/A18 relaxation, (t2) raised `window_s`/`max_points` ceilings, or (t3) a brushing panel class. ApexCharts: needs an aesthetic report surface. Gridster2: dropped. |
| m | **The per-instance top-N list on tab 1** | follow-up commission | The payload is already fetched, so the deferral is cheap; but rendering it is a NEW capability beyond the commission's scope item 6. |
| n | **The `/resources` gate pack** (`frontend/playwright.resources-gate.config.ts`, port 14202) | this lane, Phase 7 | It must be retired or re-pointed with the route — recorded so it is not lost. |

---

## 8. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **A LAZY Chart.js leak reaches the initial set** — and **no budget counts lazy chunks**, so the build stays green and both FE packs stay green (the original plan's "hard-fails the build" claim was **false** for the lazy case) | High | Medium | B1's numeric `\|Δ initial-total\| ≤ 2 kB` against the frozen 5,909,018 B baseline + B2's initial-chunk marker scan + B3's import-graph assertion, **co-located in Phases 1 and 4** (the leak window spans Phases 1–8). The **eager** case does trip the `initial` error budget (headroom 90,982 B vs chart.js ~250–400 kB raw) |
| 2 | **A frame-level state blanks the only live-lit tab** — the default install posture would show an empty page everywhere | High | Medium | AM-1: the per-source state model (Phase 2's 2.11), the central-only actionable panel (Phase 8's 8.2), and the **L4 verification leg** |
| 3 | **The canvas redraw fights the zoneless Angular runtime** | High | Medium | The rule is written into the wrapper's contract (Phase 1's 1.7, Phase 4's 4.4) and pinned by a spec; the Angular-rendered DOM (legend, axes proxy, hidden data table) is owned by Angular, not by Chart.js |
| 4 | **The FE cannot be verified with data** — auth is OFF in the dev env and enabling it wants an IdP | High | Medium | The bearer lane never contacts the IdP (locally verified): placeholder IdP *config* satisfies the validator while a minted operator bearer authenticates the API. If the bearer leg proves impossible in practice, the lane **stops that leg and reports it** rather than weakening the door (D-2 forbids weakening). |
| 5 | **Scope creep** (7 tabs × 2 engines × 3 defect classes) | Medium | High | Sequenced phases with exit criteria; exact file names per task; "a tab needing a backend touch ⇒ STOP THAT TAB, record it, ship the rest"; the routed-out register is closed at Phase 9. |
| 6 | **The false-dark families (F1/F3/F6) are charted as if dark** | Medium | Medium | ADJ-vi: rendering filters by availability BEFORE the ceiling; a dark bound family is listed with its reason; the FE's own slice is surfaced, never silent. |
| 7 | **The consolidation LOSES capability** (the house's divergence precedent repeating) | Medium | Medium | The port is a **port, not a rewrite**: 12 exported symbols enumerated by name + a **per-assertion disposition table**; the capability checklist is **6/6** (including counter STEP rendering) and is an exit criterion. |
| 8 | **`/resources` retirement breaks a bookmarked deep link** | Medium | Low | The redirect is `canMatch → UrlTree` with the param-reading **mechanism named** and an explicit null-fallback; PX-9 asserts the params survive on the **parsed** params; `?tab=` is written back so the landed URL is shareable. |
| 9 | **The env-poison family reaches the dev daemon** | High | Medium | P1–P8: the family-rule scrub (prefix `POSTGRES_*` + `PG*`, `BLUEPRINT_*`, suffix `*_API_KEY`), the explicit sweep guard, the scratch `ENSEMBLE_DATA_DIR`, the `data/ensemble.json` assertion, the **effective-port** assertion (the daemon binds `PORT`, not `DAEMON_PORT`), and P8's read-back of the daemon's actual DB. |
| 10 | **The mini-tier extension decision (ADJ-ii) is read as a licence to grow the mini into a second engine** | Medium | Medium | The scope guard is binding and enumerated (five extensions, closed set; no axes grid / zoom / brush / legend language / export on the mini; Chart.js stays panels-only) **and per R-2 the mini has no page surface in T1 at all** — it is exercised via the mount-failure fallback and the renderer specs. |
| 11 | **The new inline styles breach the 24 kB `anyComponentStyle` error cap** | Medium | Medium | Phase 4's < 8 kB bound + the recorded per-component figure (B5). |
| 12 | **A MEASURED figure from the digest gates a phase without re-derivation** | Medium | Medium | The E-1 standing rule + the §0.2 gated-figures ledger + a per-phase "E-1 re-derivation" line in every exit criterion. |

---

## 9. Close-out — the operator auth recipe (USER MORNING ITEM, NOT EXECUTED BY THE LANE)

D-2 reserves this decision for the user. The lane **hands over** the recipe; it
does not run it, and it does not touch the live config. Source:
`docs/runbooks/oauth2-auth.md` §1a–§1d, §3.

**Steps (live install):**

1. **Register an OAuth2/OIDC client at the IdP** — a *confidential* client,
   Authorization-Code + PKCE. Register the callback URI **byte-for-byte** as
   the daemon derives it: `https://<daemon-host>/api/auth/callback` (derive
   FIRST per runbook §1c, then register that exact value). Issue a client secret.
2. **Set the daemon environment** — **six** vars, and the sixth is the one the
   cited runbook omits (B4):
   - `ENSEMBLE_AUTH_ENABLED=true`
   - `ENSEMBLE_AUTH_CLIENT_ID=…`
   - `ENSEMBLE_AUTH_CLIENT_SECRET=…` (never in the repo; never logged)
   - **`ENSEMBLE_AUTH_ISSUER=https://<idp>/`** — **REQUIRED and NOT derivable
     from the discovery URL**: `AuthConfig.issuer` has **no default**
     (`daemon/config.py:3736-3741`) and it is the issuer the discovery URL is
     derived FROM, not the reverse (`discovery_url`'s description:
     *"Default derives as `<issuer>/.well-known/openid-configuration`"* —
     `daemon/config.py:3742-3748`); `daemon/config.py:3953-3954` appends
     `"issuer"` to the `missing` list, so without it the daemon **refuses to
     boot with exit 78**.
   - `ENSEMBLE_AUTH_DISCOVERY_URL=https://<idp>/.well-known/openid-configuration`
     — **origin rule**: the discovery document's origin must equal the issuer's
     origin (`daemon/config.py:3984-3988` raises otherwise); a same-origin /
     different-path discovery URL stays legal.
   - `ENSEMBLE_AUTH_ALLOWED_ORIGINS=https://<daemon-host>` (**REQUIRED** — with
     auth ON the CORS posture must replace wildcard+credentials; an empty list
     refuses the boot with exit 78).

   **The cited runbook is env-INCOMPLETE (B4, disclosed — it lives outside the
   plan dir, so this lane records the gap rather than editing it):**
   `docs/runbooks/oauth2-auth.md` §1b lists five vars and omits
   `ENSEMBLE_AUTH_ISSUER`, so a user following the runbook literally would hit
   the exit-78 refusal. **This §9 is the CANONICAL operator-recipe source**
   (steps + the restart implication + the narrow scope); the runbook's gap is a
   routed documentation item (owner: the auth-docs owner) and is recorded in the
   disclosure set (§0.4).
3. **Mint the operator token** with a scope the metrics door accepts — the
   three query routes take the **ANY-OF** set `maintenance:read` OR
   `metrics:read` (`daemon/routers/metrics.py:56-63`). **Use the narrower
   `metrics:read`** (B1e, least privilege): `maintenance:read` ALSO confers the
   destructive checkpoint-cleanup capability (`daemon/auth/scopes.py:32-39`),
   which this recipe does not need. For a browser session, the session lane
   grants the OIDC scopes ∪ the operator family; for a machine reader, mint a
   static bearer with the **pinned interpreter** (W9 — bare `python` does not
   exist on this host):
   `/home/nea/ensemble-src/.venv/bin/python -m daemon.auth.cli issue-token
   --label operator --scopes "metrics:read"` (the plaintext prints ONCE; the row
   stores only a SHA-256). **The mint IS a database write** — it INSERTs a row
   into `auth_bearer_tokens` in the database it resolves; run it against the
   intended database (B1's fence governs the lane's own dev mint).
4. **RESTART implication (explicit):** auth has **no live toggle in v1** — the
   `auth.enabled` flip and the new env vars require a **daemon restart**
   (`config.yaml:129-152`: "restart required, no live toggle in v1"). The
   restart is the moment the central door flips from 503
   `AUTH_NOT_CONFIGURED` to serving. Plan the restart window; the pre-restart
   state is unchanged for every other surface.
5. **Verify** — `GET /api/metrics/families` returns 200 with a bearer; without
   one it returns **401** (`AUTH_REQUIRED` — the FE's first-class sign-in
   state, AM-4). With auth OFF the same URL returns 503 `AUTH_NOT_CONFIGURED`.
6. **Also decide (user-owned, out of the lane):** the `/api/resources/*` door
   is fail-**open** by design (scope manifest `*`) while the metrics door is
   fail-**closed** — merging the surfaces under one page forces a conscious
   choice about that posture.

### 9.1 E-2 — THE RELEASE NARRATIVE (T1 ships MOSTLY DARK on live until the user-owned auth enablement)

**This text is expected to ride the staged-release disclosures and the morning
summary.**

> **T1 ships mostly DARK on the live install — by design, not by regression.**
> The central door is **fail-closed by design** (D-2) and the live install
> currently runs with **auth OFF**, so `/api/metrics/*` answers **503
> `AUTH_NOT_CONFIGURED`** for everyone and the six **central** tabs (Runtime, DB
> Pool, Jobs/Queue, Instances, Checkpoint, MCP) render the **actionable
> fail-closed state** with the runbook pointer. **Only tab 1 (Resources) is lit
> on live today**, because its own door (`/api/resources/*`) is fail-**open** and
> answers 200 — and under the per-source state model (AM-1) it stays lit even
> while the central tabs are dark. The moment the user completes the §9
> enablement (auth ON + a scoped token + **one daemon restart**), the central
> tabs light up with no FE change. **This is the expected auth-timeline
> outcome under D-2, not a defect and not a regression.**
>
> **Also carried in the disclosure set (see §0.4):** the plan-review council's
> requested model `decision` was **mapped to `coding`** — the known-poisoned
> upstream alias (D7) — and the **D7 proxy-side repair remains user-owned**.
>
> **Three disclosed deviations from the commission's literal scope**, so the
> morning summary is not surprised:
> 1. **The mini role has NO T1 page surface (W2).** Requirement 4 is delivered
>    as specified — ONE consolidated house SVG renderer carrying the older
>    engine's capabilities plus the five mini-tier extensions — but the mini is
>    **capability-retained**: it is exercised through the **mount-failure
>    fallback** and the **renderer specs**, not by a user-visible surface
>    (R-2). Nothing on the 7 tabs renders a mini.
> 2. **The baseline / previous-window comparison is scoped to the CENTRAL tabs
>    (W3, AM-14).** On tab 1 (Resources) the baseline toggle is **disabled
>    with a stated reason** — the resources door serves a fixed
>    15m/1h/24h/7d vocabulary with **no previous-window offset**, and a
>    client-side split of one payload would mislabel two half-length windows as
>    "previous window". This narrows commission scope item 3 for one tab.
> 3. **The approver's cross-check worker found B4 and B5** (both independently
>    re-verified by the approver against the tree) — the independent
>    cold-context check is earning its keep. Both are folded. The FE's job in this
> state is to be **honest and actionable** — which is exactly what it now does:
> one panel, the real cause, the runbook, and no dead Retry button.

---

## 10. Success criteria

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | The page ships **7 module tabs**, every one producer-live | the manifest spec + PX-1…PX-7 | 7 tabs, 0 dark shells, 0 planned entries rendered |
| 2 | Every bound family is rendered or **listed with its reason** — never silently dropped | the chart-specs honesty spec + PX-14 | 0 silent omissions; the FE's own slice is surfaced whenever it truncates |
| 3 | `/resources` lands on `/metrics?tab=resource` **with its query params** (except `tab`, which the redirect overrides) | PX-9 + the end-to-end **parsed**-`queryParams` spec | 100 % of the non-`tab` params preserved; the landing tab is tab 1 |
| 4 | The `AUTH_NOT_CONFIGURED` state is **distinct, actionable, and central-only** | PX-8 at 1600 px and 900 px | 1 panel on the central surfaces; the runbook pointer; 0 Retry buttons; **tab 1 still lit**; the global health dot untouched |
| 5 | **Every PANEL carries a unit-labeled y-axis**; the mini role renders no axis and has **no page surface** | PX-10 + the correctness spec + the surface map | 0 unlabeled panel axes; 3 unit classes on the resource timeline (incl. the toggle path); 0 mini surfaces in T1 |
| 6 | The attribution card **reconciles or says it cannot** — and the FE computes **no** coverage percentage | the correctness spec + a pixel read | the coverage is the payload's value verbatim; the disagreement is stated in words; no FE-side coverage arithmetic exists |
| 7 | The consolidation keeps the older engine's capabilities | the capability checklist + the per-assertion disposition table | the checklist passes **6/6**; the disposition table accounts for **every** assertion of both original specs |
| 8 | **Zero first-paint growth** from the charting dependency — **first paint = the APP SHELL** (LEADER RULING 1) — **and** the `/metrics` page's fetch set does not grow by the vendor payload | B1/B2/B3 (the initial-total delta, the initial-chunk scan, the import **shape** + site assertion) **and B4** (the new chunk's min+gz **and** the route fetch set's growth vs the 496,338 B baseline) | \|Δ initial-total\| **≤ 2 kB** vs 5,909,018 B; 0 `chart.js` markers in any initial chunk; **0** static `from 'chart.js'` sites; the new chunk **≤ 64 KiB** min+gz; the route fetch set grows **≤ 64 KiB** |
| 9 | The new lazy chart chunk is **measured**, not estimated | B4 | real min + gz bytes recorded in the lane's evidence |
| 10 | Both FE packs are green, with Leg 7 asserting the **expected dependency set** | the packs | 2/2 green; the assertion is by name, not by count |
| 11 | The prompt-artifact baseline is untouched | `agents/.prompt-artifacts.sha256` | **161/161 clean**; the `agents/` tree has zero diffs |
| 12 | The lane touches **no live daemon** | the capture log's API census + the boot fence | 0 requests to `:9797`; 0 non-GET anywhere; the live symlink + `state.json` byte-identical; the live uptime **monotone** |
| 13 | The routed-out register is **closed out** — 14 items, each with an owner | Phase 9's exit criterion | 14/14 recorded with an owner and a reason |
| 14 | The operator recipe is handed over, not executed, and the **mostly-dark live posture** is narrated as expected | §9 + §9.1 + Phase 9's exit criterion | the recipe exists with the restart implication named; 0 live config writes; the E-2 narrative rides the release disclosure |
| 15 | **No MEASURED figure gating an exit criterion is un-re-derived** | the §0.2 gated-figures ledger (each (a) row carries its **command + output**) + each phase's "E-1 re-derivation" line | **11/13** rows are (a) or (b); G12 is explicitly **not a gate** and G13 is a **code-fact set** |

---

## 11. Research insights that shaped this plan

- **The central door is dark in production for a reason that is not the
  chart's fault.** Live auth is OFF ⇒ 503 `AUTH_NOT_CONFIGURED` ⇒ the whole
  central-metrics FE serves nobody today, while `/resources` (fail-open) is the
  only surface with live data. Hence the **per-source** state model (AM-1) and
  the E-2 release narrative.
- **"Live" means producer-live, not door-reachable.** The 7 tabs are chosen by
  producer liveness; the three false-dark families are bound but gated.
- **The newer chart engine is strictly less capable than the older one it
  duplicated** — the house's own divergence precedent. Hence the **port** (12
  symbols + a disposition table), not a rewrite.
- **The mixed-unit axis is not a styling defect — it is a false statement.**
  Two units, one unlabeled axis, each series independently min-max normalized.
  The fix is a **split** (per kind+unit group, R-1), not a label.
- **The FE's own selector slice is a silent-omission class** — the same
  discipline as the door: **a bound is surfaced, never silent**.
- **The charting numbers were ESTIMATED, never measured** — §9-6 asks the build
  lane to measure the real min+gz (B4).
- **The bundle elephant is mermaid, not a chart library** (3.56 MB raw /
  972 kB gz ≈ 80 % of the initial set, loaded on every page). It is routed (e),
  and the charting work must not make it worse.
- **The lazy-chunk budget hole is the real safety risk** — the `initial`
  budget counts entry points + static imports only, so a lazy leak is
  invisible to every configured budget. Hence B1–B3 (AM-2).
- **The daemon's `coverage_pct` is computed on a basis the FE cannot see** —
  `attributed / anon_bytes`, where `anon_bytes` is not the sum of the displayed
  components. So the FE **displays** it and **states** the disagreement; it
  never computes it (AM-13).

---

## 12. Open questions

| # | Question | Why it is still open | What the plan does meanwhile |
|---|---|---|---|
| 1 | Should the **family catalog drawer** be a drawer, or a small popover? | A UI affordance call with no functional consequence. | Built as a **frame-level drawer** (ADJ-i). A popover is a CSS variant of the same component and does not change the decision record. |
| 2 | Does the **operator recipe** need to cover the `metrics:read` scope for the session lane too? | The session lane grants the OIDC scopes ∪ the operator family; whether the IdP must emit `maintenance:read`/`metrics:read` is an IdP-side question the user answers when they enable auth. | §9 states both paths (session lane + static bearer) and names the door's ANY-OF scope set. |
| 3 | Should the **data-gap annotation** also appear on the central tabs' series? | The central series are ring-backed (2 h, in-RAM) and can drop a sample for reasons the FE cannot distinguish from a gap. | Ship it on tab 1 only; the central tabs show the door's own `stale` flag. |
| 4 | Is the **500-row snapshot ceiling** enough for the per-tab stat strips on a large install? | The snapshot route is bounded (`MAX_SNAPSHOT_SERIES`, `offset`/`limit`, `truncated` + `next_cursor`). | IMPL-08 handles it honestly (a family absent from a bounded page renders "not on this page" + the truncation hint) **and** the FE service gains the optional `?family=` scoping as the preferred path. |
| 5 | **Which** of the two bundle baselines does the promotion use — the builder's 36-chunk "Initial total" (5,909,018 B) or the whole initial payload including `index.html` (5,918,275 B)? | Both are defensible readings of "the initial set"; the **budget's own basis is the former** (`InitialCalculator` sums chunks; `index.html` is an asset). | The plan uses the **builder's basis** (5,909,018 B) for every guard and states the whole-payload figure alongside it, so a reviewer can re-derive either. A house-level wording decision on "initial set" would be a nice-to-have, not a blocker. |
