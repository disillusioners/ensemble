# Phase 4: The FE Nest Reports tab

Lane: **nest-anomaly-report** (board item `wi-2d691ed2`)
Phase owner: worker `plan-worker-registry-fe` (phases 3–4)
Status: Draft for the planner's review
Depends on: **phase 3** (the F23 family is registered + rostered; the `/api/nest-reports/*` domain route is mounted and gated).
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-nest-anomaly-report` @ `1f6898ae2`

---

## Objective

Ship ONE new tab on the existing `/metrics` page — **`nest-reports`** — that shows the anomaly data on **two surfaces**: the **F23 metric panels** (the count-over-time by category and the open-cluster gauge, served by the existing central door) and the **incident list** (the decision-ready clusters + their member reports, served by phase 3's **domain** route).

Outcome test: with auth ON, `?tab=nest-reports` renders the F23 step/line panels, the stat strip, and a populated incident table whose rows expand into the cluster's member reports; with auth OFF (the LIVE posture), the tab renders the ONE fail-closed banner (no Retry, runbook pointer), never blanks, and never storms requests.

---

## Binding rulings (restated — binding, not re-litigated)

The lane's ruling SSOT is **`.agents/shared/planning/nest-anomaly-report/decisions.md`** (2026-11-04T22:30Z). The entries that bind this phase:

| Ruling (in `decisions.md`) | Application here |
|---|---|
| **R2** (the FE shape) | *"The FE incident list is a **CENTRAL TAB** on the existing metrics page frame (**custom body**, not the generic chart renderer), fed by a NEW domain REST route `/api/nest-reports/*` — a DOMAIN route, not a second metrics query path."* ⇒ a `slot: 'central'` manifest entry (the data comes from the central door) whose **body is a custom component** (the manifest binds DATA, the component map binds CODE). This phase SPECIFIES R2 — `decisions.md` R2 names `phase4-plan.md` as the spec's home. |
| **R8** (the read-surface split) | The FE incident list is served by the DOMAIN route; the central metrics door stays **FROZEN** (three routes); the metric-shaped half flows through the existing door. |
| **F23** (family identity) | The metric half binds the family **F23** (`source_idiom="registry"`, `durability="durable"`). |
| **R1 / R3** (vocabularies) | The FE carries **NO** category set and **NO** severity set: the door value-enumerates the closed labels for the metric half, and the incident list renders the **raw** stored strings + the route's `severity_rank`. |
| **No new npm deps; charts ride the shared body** | The tab renders charts through the **shared** `app-metrics-central-tab` (which owns the renderer registry, the panel composition and the house SVG renderer). The new FE code contains **ZERO** `chart.js` import sites. |
| **The label-namespace collision** (the phase-5/6 sibling's finding, `phase6-plan.md:328-335` — the authoritative disclosure block's current home) | The Resources tab (tab 1) **already** renders an **"Anomalies"** panel — the resource-anomaly family (`frontend/src/app/pages/metrics/resource/anomalies/anomalies-panel.component.ts:43` renders `<h3>Anomalies</h3>`) — and the daemon config namespace already carries `anomaly_enabled` / `anomaly_interval_s` / `anomaly_mad_threshold` / `anomaly_min_baseline_points` (`daemon/config.py:905-906`, `:948-954`) plus the `ResourceAnomaly` shape (`daemon/routers/resources.py:870`, `:1024-1031`). ⇒ **The tab keeps the slug `anomalies` (no slug collision) but is LABELLED distinctly — see Task 1's label ruling.** |

### THE LABEL RULING (decided, not deferred)

**The tab's user-visible label is `"Nest Reports"`.** The slug is **`nest-reports`** (it is the `?tab=` value and the manifest id) — **the slot and the label now MATCH**, which the old `anomalies` slot did not.

**Why "Nest Reports" and not "Anomaly Reports":**
1. **ZERO shared token with the existing panel.** The Resources tab's anomaly panel is headed literally **"Anomalies"** (`anomalies-panel.component.ts:43`). "Anomaly Reports" still carries the word *Anomaly* and would sit one tab away from a panel headed "Anomalies" — visually confusable, and the two surfaces show **unrelated data** (the resource journal's CPU/memory/FDS spikes vs the durable anomaly-reports store).
2. **"Nest Reports" names the SCOPE, which is the real differentiator.** The reports are filed by agents **inside an instance tree**; the cluster key is the tree root (`decisions.md` R5: the key is `(tree_root_instance_id, canonical category)`). The resource anomalies are **daemon-level** resource signals. The label says which world the tab is about.
3. **It is the lane's own vocabulary** — the commission is the *nest-report* board item (`wi-2d691ed2`), and the sibling's plan-overview calls the phase-4 deliverable "the FE anomalies tab" while the underlying artifact is a **report** feed.
4. **It still names the ARTIFACT** (reports) — an operator reading the tab strip sees what the tab lists.

**What is NOT changed:** the family binding `F23`, the route, the tool categories, and the daemon config namespace (phase 1 owns the config keys — `phase6-plan.md:328-335` requires phase 1 to namespace the new kill-switch distinctly, in the **`nest_report_*` / `ENSEMBLE_NEST_REPORT_*`** namespace per R9 — the earlier `anomaly-`-prefixed spelling is **SUPERSEDED** and must not appear in this plan). **The FE tab introduces no config key of its own.**

---

## Scope

### In scope
- `tabs.manifest.ts` — the union member + the tab entry.
- `tabs/tabs.components.ts` — the import + the map line.
- `tabs/nest-reports-tab.component.ts` (new) — the body.
- `models/nest-reports.models.ts` (new) — the wire types + the domain-local copy.
- `services/nest-reports.service.ts` (new) — the domain-route client.
- `metrics-units.ts` — the three metric-name rows.
- The specs + the bundle re-measure + one pixel item.

**THE SLOT AND THE LABEL NOW MATCH.** The leader's naming adjudication (recorded in `decisions.md` **R9**: *nest- namespace for operational surfaces, anomaly- for data + agent vocabulary*) moved the slot to **`nest-reports`** and the label is **`Nest Reports`** — the internal inconsistency the old slot carried (slug `anomalies` vs label `Nest Reports`) is **gone**. The FE's own new surfaces follow the slot: the body `tabs/nest-reports-tab.component.ts`, the service `services/nest-reports.service.ts`, the models `models/nest-reports.models.ts`, the pack `metrics.nest-reports.spec.ts`, and the tab's `data-test` hooks `nest-reports-*`. **The DATA vocabulary keeps its `anomaly-` names** (the leader's ruling): the wire types `AnomalyCluster` / `AnomalyClusterReport` / `AnomalyClustersPayload` / `AnomalyClusterDetailPayload` / `AnomalyBound` and the raw `category` / `severity` values are unchanged. **The legacy Resources surface is untouched**: `pages/metrics/resource/anomalies/anomalies-panel.component.ts` (selector `app-metrics-anomalies`, hooks `anomalies-panel` / `anomalies-table` / `anomalies-error` / `anomalies-retry`) keeps its name, its selector and its hooks — the two surfaces no longer share any namespace.

### Out of scope
- **Any edit to the central door or the metrics service** — the F23 series arrive through the existing `MetricsService`.
- **Any edit to `tabs/central-tab.component.ts`** — the shared body is consumed as-is (the reason the composition works; see Task 2).
- **The daemon side** — phase 3. This phase fixes the FE-side wire consumption of phase 3's contract.
- **The `resource/anomalies/` panel** (`pages/metrics/resource/anomalies/anomalies-panel.component.ts`) — it is the **resource** anomaly journal (a process-local in-RAM `deque(maxlen=256)`, `daemon/services/resource_anomaly.py:908`), a DIFFERENT data source. It is untouched; the new tab's data is the **durable** anomaly store. **The two must never be conflated in copy** — the new tab's copy names the durable store explicitly (see Task 2's copy discipline).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | **Manifest**: the `F23` union member + the `nest-reports` entry + the spec census amendments | phase 3 | The manifest carries 8 tabs; the ids/spec/census unions are consistent; the map type-check demands the new body |
| 2 | **The tab body** (`tabs/nest-reports-tab.component.ts`) | 1, 3 | The F23 panels render through the shared body; the incident list renders the decision-ready rows; exactly ONE banner per failing source |
| 3 | **The domain client + the wire models** (`services/nest-reports.service.ts`, `models/nest-reports.models.ts`) | phase 3 (the route) | The client sends `Accept: application/json`, has a 15 s deadline, supersedes stale responses, and maps the route's error vocabulary honestly |
| 4 | **The units** (`metrics-units.ts`) | 1 | The three F23 metric names carry honest units inside the closed allowed set |
| 5 | **Register the body** (`tabs/tabs.components.ts`) | 2 | The map covers the manifest EXACTLY (a compile error otherwise) |
| 6 | **The gate packs** + the bundle re-measure | 1-5 | The FE pack is green; the bundle guard is GREEN with the re-measured numbers recorded |
| 7 | **The one pixel item** (Playwright geometry, auth-off) | 2, 3 | The geometry assertions are recorded in the lane's own `pixels/` evidence dir |

---

### Task 1 — The manifest

**File: `frontend/src/app/pages/metrics/tabs.manifest.ts`**

1. **The registered-family union** (`:73-89`) — add `'F23'`. It is a **CLOSED** union (the E-1 census): `'F0.3' | 'F1' | 'F3' | … | 'F21'` → 16 members → **17**. The comment above it (`:70-77`) states the census is re-derived from the registry SSOT (`wiring/registry.py`'s `FAMILY_SPECS` + `adjudication.py`'s `REGISTERED_FAMILIES`) — phase 3 amends both, so F23 is registered by the time this lands.
2. **The tab entry** — APPENDED to `METRICS_TABS` (`:237-330`), **after** `'mcp'`:
   ```ts
   {
     disposition: 'bound',
     slot: 'central',
     id: 'nest-reports',
     // The USER-VISIBLE label is deliberately NOT "Anomalies": the
     // Resources tab (tab 1) already renders an "Anomalies" panel
     // (the resource-anomaly family) and the two show unrelated
     // data. The slug IS `nest-reports` — it now MATCHES the label.
     label: 'Nest Reports',
     // F23 — the anomaly family (phase 3).
     familyIds: ['F23'],
     // F23's snapshot values feed the stat strip (the strip renders the
     // LIVE series of the named families — the shared body fetches
     // GET /api/metrics/snapshot?family=<F>).
     statFamilyIds: ['F23'],
     allowedWindows: ['15m', '30m', '1h', '2h'],
     defaultWindowS: '1h',
     copyVariant: 'auth_not_configured',
   },
   ```
   **Why APPENDED and not inserted:** the tab list order is user-visible (the `?tab=` deep link and the tab strip); inserting would move the shipped tabs' positions. The precedent entries are appended in the same way.
3. **`statFamilyIds: ['F23']` — the justification and its bound.** The stat strip is fed by `GET /api/metrics/snapshot?family=F23` and renders **one card per LIVE series** (`tabs/central-tab.component.ts:895-906` `fetchStatStrip` + `:567-580` `statCards`) — NOT the whole closed space. The closed space of F23 is 13 + 39 + 13 + 1 (the unlabelled AM-13f suppression counter) = **66** series; the LIVE count is bounded by the categories/severities actually present (typically a handful). **Worst case: 66 cards in the strip** — bounded by the closed vocabulary, never open. The precedent is the same shape (`runtime` binds the chart family F3 into its strip).
   **Why F23 is chart-primary (and therefore renders panels, not just a strip):** phase 3 adds F23 to `CHART_PRIMARY_ROSTER`; the family role is then `chart` (`daemon/services/metrics_query/provider.py:249-252`) and the shared body renders its panel groups (`tabs/central-tab.component.ts:514-529`). **If phase 3's roster row is missing, this tab degrades to a strip-only view** — that coupling is stated in Task 2 and in the exit criterion.
4. **The spec amendments** — `frontend/src/app/pages/metrics/tabs.manifest.spec.ts`:
   - `:70-72` `it('carries exactly 7 tabs')` → **8**;
   - `:73-84` the ids array → append `'nest-reports'`;
   - `:140-148` the per-tab stat-family map → add `nest-reports: ['F23']`;
   - `:150-166` the **E-1 census union** (the hardcoded `registered` set) → add `'F23'`.

---

### Task 2 — The tab body

**File: `frontend/src/app/pages/metrics/tabs/nest-reports-tab.component.ts` (NEW)** — selector `app-metrics-nest-reports-tab`, standalone, `ChangeDetectionStrategy.OnPush`.

**RULING: COMPOSE the shared `app-metrics-central-tab` (not a standalone re-implementation).** This is R2's *"central tab … custom body"* shape: the manifest entry is a **central** entry (the data comes from the central door) and the body is a **custom component** — the manifest binds DATA, the component map binds CODE (`tabs/tabs.components.ts:13-26`: *"The manifest is DATA (which family belongs to which module); the map is CODE (which component renders which module)"*).

*Why the composition is mechanically sound (the dispatch asked for the decision to be justified against `central-tab.component.ts`'s inputs):*
1. The shared body's inputs are `entry` (the manifest entry), `state` (the frame's per-tab state), `onState` (the reporter), `window`, `baseline`, `onRangeChange` (`tabs/central-tab.component.ts:413-441`) — **exactly the six inputs the `runtime-tab` wrapper passes** (`tabs/runtime-tab.component.ts:31-47`). The nest-reports body is that wrapper **plus** a second section.
2. The shared body renders its OWN door-state panel from `state().errorClass` (`tabs/central-tab.component.ts:150-155`) — the **central** source's failure.
3. The frame owns **ONE** state slot per tab: `applyTabState` merges a single patch into a single `MetricsTabState` (`metrics.component.ts:440-444`), and that slot has ONE `errorClass` / ONE `lastGood` / ONE `series`, all shaped for the **central** door. ⇒ **Two sources cannot share it.**

*Therefore: the DOMAIN half's state lives in the BODY's own local signals* (the resource tab's own-source pattern, `tabs/resource-tab.component.ts:88-95`), and the central half keeps the frame's slot. **No clobbering, no frame change, no state-model change.**

*The SINGLE-BANNER discipline (the double-banner risk, resolved):*
- The central half's failure is rendered by the **shared body** (its own panel).
- The domain half's failure is rendered by a **domain-local panel** in this body.
- **The domain panel is SUPPRESSED when the central half is also failing** (`state().errorClass !== null` ⇒ the domain panel renders nothing). Rationale: both sources share ONE posture (the daemon's auth configuration); a posture failure that affects both is explained **ONCE**, and the operator's action ("an operator must configure auth on the daemon and restart it") is identical. When the central half is HEALTHY and the domain route fails, the domain panel renders **alone** — a domain-specific failure is never explained by the central door.
- ⇒ **At most ONE door-state banner per source, and at most ONE per tab for a posture failure.** The frame's own catalog banner (the third, page-level one) is the shipped behaviour for every central tab and is unchanged.

*The body's two sections:*

| Section | Source | Transport |
|---|---|---|
| **A — the F23 panels + the stat strip** | the central door (phase 3's family) | the **shared** `app-metrics-central-tab` (it owns the fetch, the panels, the strip, the last-good retention) |
| **B — the incident list** | the **domain** route | the NEW `NestReportsService` |

**Section B — the incident list (a plain HTML table, the `families-tab.component.ts:75-136` precedent):**
- **Columns:** severity chip (PEAK-labelled, AM-7) · category · **occurrence count badge** (AM-6c) · report count · tree-root instance id · status badge (open / closed) · last-report (relative + absolute) · board link (only when `board_item_id` is present).
- **AM-6c — GROUP THE ROWS BY `(tree_root_instance_id, category)` AND BADGE THE OCCURRENCE COUNT.** **THE GROUPING IS DONE SERVER-SIDE** (phase 3's route returns GROUPS — one per (tree root, canonical category); the FE **renders** the groups and **never re-keys or re-windows** them client-side). The FE's row is one **group**, carrying an **occurrence badge** — the number of CLUSTERS in that group (the recurring-incident readability affordance). The rationale (the architect's finding, exact): a recurring hourly incident produces **N=24 clusters** under R5's sliding 15-minute window (each window is a legitimate new cluster), so a raw cluster list reads as *"24 separate incidents"* when it is **one incident recurring** — and the 15-minute window **cannot distinguish "recurred" from "new"**. ⇒ the grouped view makes the recurrence legible ("this tree reported this category 24× today") without touching the data plane.
  - **It is a READ-SIDE GROUPING over clusters, NEVER a re-assignment.** The FE renders the groups the route returns; **no cluster row is merged, re-keyed or re-windowed in the FE** — R5's identity model is untouched (the member cluster ids stay distinct, and clicking one loads that cluster's own members).
  - **Widening W is REJECTED** (the architect's explicit ruling): `W=4h` would chain hourly reports into one cluster whose `last_report_at` rolls forever ⇒ it would **never resolve** under the derived 1-hour rule. The grouping is the fix; the window is not.
  - The **occurrence count is the number of clusters in the group** (not the number of reports) — the two differ (a cluster aggregates many reports), and the count shown must be the one the operator means.
- **AM-7 — THE SEVERITY CHIP LABELS `max_severity` AS THE PEAK.** The chip's text is the house phrasing **"critical (peak)"** — the cluster's `max_severity` is a **max-rank rollup over the cluster's window** (it never de-escalates; a NEW cluster for the same key starts fresh). The FE must render the PEAK wording, never a bare "critical" that asserts a *current* severity the store does not hold. The route supplies the label (`severity_label`, phase 3's Task 6) so the FE renders it verbatim — **no FE-side severity vocabulary**.
- **Severity chip tiering WITHOUT a vocabulary:** the chip colours by the **`severity_rank` integer the route serves** (1 = minor, 2 = major, 3 = critical). **The FE never hard-codes the severity names** — the route owns the rank mapping (it reads the service's `SEVERITY_RANK`, phase 2's map at `phase2-plan.md:37`), so the FE carries no second copy.
- **Category chip:** renders the **raw stored string** verbatim. No FE vocabulary (the door value-enumerates the closed labels for the metric half; the list half renders what the store holds).
- **Row click → expand members:** the expansion loads `GET /api/nest-reports/clusters/{cluster_id}` (the domain detail route) into the SAME row (an inline expansion), with its own supersede token; a second click on another row collapses the first. **With the AM-6c grouping, the group row first expands to its MEMBER CLUSTERS** (ids + counts + last-report + open badge, from the route's `cluster_ids` — **no extra call**), and clicking a member cluster id then loads that cluster's members through the same detail route.
- **AM-12(c) — THE EXCERPT IS RENDERED ≤300 CHARS WITH A REVEAL AFFORDANCE, NEVER RAW.** The excerpt is raw tool stdout harvested from a transcript (AM-8/AM-11's surface). The member list renders **at most 300 characters** of it, with an explicit **reveal** affordance (a "show more" control that reveals the rest of the already-stored, already-scrubbed text). The FE NEVER renders the raw excerpt unbounded, and **the excerpt is opt-in at the wire** (AM-12b: `?include_excerpt=true` — without it the detail payload carries no `excerpt` key and the FE shows nothing). This is **spec-pinned** (see Task 6's tab-pack row).
- **The status badge:** `open` is **derived server-side** (`last_report_at >= now − 1h`, **R5** — no status column exists) — the FE renders the server's boolean, never recomputes it (a client clock would disagree with the server's window).
- **The freshness line:** the route serves the query instant and the window; the FE renders the server's values (never the client clock — the house rule, `tabs/central-tab.component.ts:864-870`).
- **`data-test` hooks** (the house convention): `nest-reports-incident-table`, `nest-reports-row-<cluster_id>`, `nest-reports-severity-chip`, `nest-reports-status-badge`, `nest-reports-members`, `nest-reports-empty`, `nest-reports-error`, `nest-reports-retry`.

---

### Task 3 — The domain client + the wire models

**File: `frontend/src/app/services/nest-reports.service.ts` (NEW)** — the house client shape (`services/metrics.service.ts:36-80`):

| Element | Value / rule |
|---|---|
| `CLUSTERS_URL` | `'/api/nest-reports/clusters'` — a RELATIVE path (the house rule: the door is same-origin) |
| `clusterDetailUrl(id)` | `'/api/nest-reports/clusters/' + encodeURIComponent(id)` — the id is **`anomaly_clusters.id`** (a uuid4 hex, phase 1's model spec), NOT the `cluster_key_hash`; `encodeURIComponent` is applied anyway (the house URL-hygiene habit) |
| `JSON_HEADERS` | `{ Accept: 'application/json' }` — **not decoration**: the auth middleware 302-redirects a wildcard-Accept request, turning a JSON call into an HTML login page (`services/metrics.service.ts:27-34`) |
| `DOOR_TIMEOUT_MS` | `15_000` — the house client deadline (`services/metrics.service.ts:52`); the rxjs `timeout` + a typed `MetricsDoorTimeoutError` |
| supersede tokens | a monotonic request sequence per query class (the clusters list and the member detail are SEPARATE classes); a stale response is discarded, never painted |
| query params | `window_s`, `severity`, `category`, `limit`, `cursor` — forwarded only when set — **plus `include_excerpt` (AM-12b): the DETAIL call sets it ONLY when the operator asks for the excerpt** (the default is FALSE ⇒ the detail payload carries no `excerpt` key) |

**File: `frontend/src/app/models/nest-reports.models.ts` (NEW)** — the wire types: `AnomalyCluster`, `AnomalyClusterReport`, `AnomalyClustersPayload`, `AnomalyClusterDetailPayload`, `AnomalyBound` (the six-member bound block), plus the **domain-local copy** table. **Two additions the amendments require:** (a) the group carries the route's **`severity_label`** (the PEAK phrasing, AM-7) and the FE renders it verbatim; (b) the group carries **`occurrence_count`** (the CLUSTER count) and **`cluster_ids`** — the grouping is **server-side** (phase 3's THE GROUPING RULING), so the FE type is the GROUP shape (`tree_root_instance_id`, `category`, `severity`, `severity_rank`, `severity_label`, `report_count`, `occurrence_count`, `last_report_at`, `open`, `cluster_ids`), and the FE renders it verbatim — **no client-side grouping, no re-keying**.

**THE ERROR VOCABULARY — the honest mapping (the door's union stays frozen):**
`MetricsDoorErrorCode` is a **CLOSED 10-class union** mirrored from the door's frozen contract (`models/metrics.model.ts:385-396`), and its copy SSOT (`models/metrics.model.ts:488-565`) is asserted exhaustively by a spec. **The domain route does NOT widen it.** The mapping:

| the route's answer | the FE renders | why |
|---|---|---|
| `503 AUTH_NOT_CONFIGURED` | `app-metrics-door-state` with `code="auth_not_configured"` | the copy's MEANING and ACTION are exactly right ("an operator must configure auth on the daemon and restart it", pointer `docs/runbooks/oauth2-auth.md`, `retryable:false` ⇒ **no Retry button**). The wording "the metrics door" is the PAGE's door — the panel lives inside the metrics page |
| `403 SCOPE_INSUFFICIENT` | `code="scope_insufficient"` | the same reason, the same action |
| `401` | `code="auth_required"` | the same |
| `400` | `code="bad_request"` | the same |
| client deadline / transport | `code="timeout"` / `code="network"` | the same |
| **`404`** (unknown cluster id) | **a DOMAIN-LOCAL copy**, NOT a door class | the door's `unknown_family` copy says *"the metrics door does not know this family — this page is ahead of the daemon"* — **a false statement** for a cluster id. The domain copy: "this cluster is no longer known (it may have been pruned)" |
| **`503 anomaly_unavailable`** (the anomaly substrate is down) | **a DOMAIN-LOCAL copy**, NOT a door class | the door's `registry_unavailable` copy says *"the metrics door has no provider wired — the metrics service is unavailable"* — **a false statement** for the anomaly substrate. The domain copy: "the anomaly store is unavailable on this daemon" + a Retry button (it IS transient) |

⇒ The domain panel therefore renders the **shared** `app-metrics-door-state` for the six reusable classes and a **small domain-local banner block** for the two domain-only outcomes (own copy, own `data-test`, a Retry for the transient one). **The frozen door vocabulary is untouched.**

---

### Task 4 — The units

**File: `frontend/src/app/pages/metrics/metrics-units.ts`** — add **four rows** to `METRICS_UNIT_VOCABULARY` (the manifest's registration-only contract: a new series = the manifest + a unit row):

```ts
// F23 — the anomaly family (phase 3)
ensemble_anomaly_reports_total: 'count',
ensemble_anomaly_clusters_open: 'count',
ensemble_anomaly_reports_rate_limited_total: 'count',
ensemble_anomaly_board_items_suppressed_total: 'count',
```
plus the provenance comment block above the table (the file's own convention: each entry's provenance names its daemon source).

**The unit honesty census:** the closed allowed set is `{s, count, bytes, MB, %, flag, state, ratio}` (`metrics-units.spec.ts:96-105`) — all **four** rows are `count` ⇒ inside the set (the fourth is AM-13f's unlabelled write-time suppression counter — phase 3's 4th F23 metric). A metric with no row renders `unit unknown` (never a guess) — so **omitting a row would render "unit unknown"**, never a wrong unit. The three names are keyed on the METRIC NAME, never on the family.

---

### Task 5 — Register the body

**File: `frontend/src/app/pages/metrics/tabs/tabs.components.ts`** — **+1 import** and **+1 map line**:
```ts
import { NestReportsTabComponent } from './nest-reports-tab.component';
// …
nest-reports: NestReportsTabComponent,
```
The map is typed `Readonly<Record<MetricsTabId, Type<unknown>>>`, and `MetricsTabId` is DERIVED from the manifest's literal shape (`tabs.manifest.ts:485`) ⇒ **adding the manifest entry without the map line is a COMPILE error** (the exact-cover is enforced by the type, not by a runtime check). The shell's spec additionally asserts the map covers the manifest exactly.

---

### Task 6 — The gate packs + the bundle re-measure

**New/extended specs:**

**The pack FILE NAME follows the leader's rename** (the slot ⇒ `metrics.nest-reports.spec.ts`), and it fits the house convention: the metrics dir's page-level specs are `metrics.<aspect>.spec.ts` — `metrics.component.spec.ts`, `metrics.door-state.spec.ts`, `metrics.units.spec.ts`.

| Spec | File | What it proves |
|---|---|---|
| **tab pack** | **`frontend/src/app/pages/metrics/metrics.nest-reports.spec.ts`** | the tab appears in the tab manifest **with the ruled id `nest-reports` and the ruled label `Nest Reports`** (and the tab does NOT claim the id/label of the Resources tab's existing "Anomalies" panel); the request shapes; a 503 on BOTH sources ⇒ **exactly ONE** banner (the single-banner discipline) and no Retry; the domain-only 404 ⇒ the domain-local copy; the empty state is DISTINCT from the error state; **the empty state is HONEST — no fake data** (a placeholder series on 503 must red this pack); **AM-6c**: the rows are grouped by `(tree_root_instance_id, category)` and each group carries an **occurrence-count badge** (the count of CLUSTERS in the group), and the grouping never mutates a cluster; **AM-7**: the severity chip renders the **PEAK** label ("critical (peak)") — never a bare severity word; **AM-12(c)**: the excerpt renders **≤300 chars** with a **reveal affordance**, never raw; the open/closed badge renders the SERVER's boolean; a row click expands the members; a stale response is discarded (the supersede token); polling supersede tokens; tab-hidden polling stops; **AM-12(d) — THE TEMPLATE-SAFETY PIN**: the new tab's template contains **NO `innerHTML` and NO `bypassSecurityTrust`** (the safety is currently **incidental** — the metrics FE family has zero of both today; this pin makes it a **property**, not a coincidence) |
| service | `frontend/src/app/services/nest-reports.service.spec.ts` (NEW) | the URL shapes; `Accept: application/json`; the 15 s deadline mapping to the typed timeout error; the supersede token discards a stale answer |
| models | `frontend/src/app/models/nest-reports.models.spec.ts` (NEW) | the domain-local copy table is **exhaustive** over the domain-only outcomes (the house "no silent fall-through" rule) |
| manifest | `frontend/src/app/pages/metrics/tabs.manifest.spec.ts` (EXTEND) | 8 tabs; the ids array; the stat-family map; the census union carries F23 |
| bundle guard | `frontend/src/app/pages/metrics/metrics.bundle-guard.spec.ts` (**EXTENDED, not bypassed** — `phase6-plan.md:117`) | the existing guard's contract pins stay green (the artifact is read, not copied) **AND** the new tab's vendor bytes in ANY static fetch set == 0; the chart.js-vendor invariant stays GREEN |

**THE MUTATION LEGS phase 6 expects from phase 4** (`phase6-plan.md:117`) — the phase-4 packs MUST be structured so that these mutations redden:

| Leg | Mutation | Which phase-4 pack must go red |
|---|---|---|
| **M-4a** | remove the tab from the manifest | the tab pack's "the tab appears in the tab manifest" assertion |
| **M-4b** | render a placeholder series on 503 | the tab pack's "renders the degradation state honestly" pin (the empty state is honest — **no fake data**) |
| **M-4c** | static-import the chart vendor in the new tab | the **existing** bundle guard reds (the v0.28.11 N5 vendor-static-edge invariant) — the guard is EXTENDED, never bypassed |
| **M-4d** (AM-12d) | **inject `innerHTML` / `bypassSecurityTrust` into the new tab's template** | the tab pack's **template-safety pin** — the new tab's template contains NO `innerHTML` and NO `bypassSecurityTrust` (the safety is currently incidental; the pin makes it a property) |
| **M-4e** (AM-12c) | render the excerpt **raw / unbounded** (drop the 300-char cap or the reveal affordance) | the tab pack's excerpt-rendering pin (≤300 chars + a reveal affordance) |
| **M-4f** (AM-6c) | render the rows **ungrouped** (one row per cluster, no occurrence badge) | the tab pack's grouping pin (grouped by `(tree_root_instance_id, category)`, badge = the cluster count) |
| **M-4g** (AM-7) | render the severity chip as a bare severity word (no PEAK label) | the tab pack's PEAK-label pin |

**The bundle discipline (the frozen numbers, from `.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.json`):**

| Guard | Frozen baseline | Threshold | This lane's exposure |
|---|---|---|---|
| B1 initial-total | 5 909 018 B (measured at tip: 5 907 717 B, Δ −1 301) | \|Δ\| ≤ 2 048 B | **ZERO** — the `/metrics` route is LAZY (`frontend/src/app/app.routes.ts` lazy-loads it), so nothing new lands in the initial set |
| B2 chart.js marker in the initial set | 0 | 0 hits | **ZERO** — no chart.js import in the new code at all |
| B3 static `from 'chart.js'` sites | 0 | 0 static sites | **ZERO** — the new body imports no chart library; the charts ride the shared body |
| B4a the chart chunk | 206 857 B min / 71 054 B gz | ≤ 82 000 B gz | **ZERO** — the vendor chunk is unchanged (no new dynamic import) |
| **B4b the `/metrics` route fetch set, lazy-only** | **165 185 B** | **≤ 185 000 B** | **THE BINDING ONE — headroom 19 815 B.** Tabs are NOT individually code-split, so the new body + service + models land **inside the route chunk** |
| B4 vendor-static-edge invariant | 0 | 0 | **ZERO** |
| the component-style warning SET | 9 entries | SET DIFFERENCE (0 added, 0 removed) | the new body uses **inline styles** (the house pattern — `tabs/central-tab.component.ts` uses `styles: [\`…\`]`), so it adds **no** `.scss` warning entry |

**The estimated add: ≈ 8–14 kB min** (the body ≈ 5–8 kB, the service ≈ 1.5–2 kB, the models ≈ 1–2 kB, plus the manifest/spec deltas). That is **inside the 19 815 B headroom**, but it is the one number that must be MEASURED, not estimated.
**The re-measure step (mandatory, in this order):**
```bash
cd frontend && npm ci                 # if node_modules is absent (the house prerequisite)
cd frontend && ./node_modules/.bin/ng build --configuration production
node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-baseline.measure.js dist/frontend/browser
node ../.agents/shared/planning/metrics-unification/fe-t1-build/bundle-guard.js <the dist dir>
```
The measured numbers are recorded **against the FROZEN baseline** (the artifact's numbers are the reference — the artifact's own `reproduces_at_tip_note` says a future re-measure must compare against the frozen values and must NOT overwrite them). **Zero new npm deps** (`frontend/package.json` stays untouched — `package.json 0-line` is the precedent from the FE-T1 lane).

---

### Task 7 — The one pixel item

**One Playwright geometry item** (the house harness: **geometry assertions, NOT screenshots** — the precedent is `.agents/shared/planning/metrics-unification/fe-t1-build/pixels/`; this lane's evidence goes to **`.agents/shared/planning/nest-anomaly-report/pixels/`**).

**The capture: the auth-off tab state.**
- **Fixture:** the app with the metrics door answering `503 {error: {detail: {error: 'AUTH_NOT_CONFIGURED'}}}` **and** the domain route answering the same (the LIVE posture), then navigate to `/metrics?tab=nest-reports`.
- **Geometry assertions (the pixel item):**
  1. the tab's central door-state panel is **visible** and its bounding box is **non-degenerate** (width > 0, height > 0) — a banner that collapsed to zero height is a rendering defect, not a styling nicety;
  2. **exactly ONE** door-state banner is visible in the tab (the single-banner discipline: the domain panel is suppressed while the central half is failing) — counted by `data-error-class="auth_not_configured"` occurrences;
  3. **no Retry button** is present (`retryable: false` for the auth class) — the affordance that can never succeed must not be rendered;
  4. the runbook pointer text `docs/runbooks/oauth2-auth.md` is visible inside the banner;
  5. the incident-list section renders a **degraded placeholder** with a non-degenerate bounding box (the tab never blanks);
  6. the banner's box and the incident placeholder's box **do not intersect** (the layout-collision class the FE-T1 lane fixed for the resources tab).
- **Evidence:** the Playwright output + a short markdown receipt in the lane's `pixels/` dir, naming the assertions and the observed boxes.

---

## Coupling

- **Tight with phase 3** — (a) the F23 family must be **rostered** (chart-primary), else the tab renders a strip with **no panel**; (b) the route's wire shape is the FE's contract — the FE needs `severity_rank` (Task 3) and the server-derived `open` boolean and the server's query instant; (c) the route's error tokens are the FE's error vocabulary.
- **Tight with the manifest + the shell** — the tab entry, the union, the units table and the component map are one registration unit; the map's type makes a missing entry a compile error.
- **Loose with the metrics lane** — the shared body and the frame are consumed as-is.
- **Independent of** the daemon's promote machinery, the plugin subsystem, and the resources module.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Phase 3's roster row is missing** ⇒ F23 is stat-primary ⇒ the tab has no trend panel | High | Medium | Phase 3's Task 2 names the mechanism and the rows; the phase-4 exit criterion re-checks `roster_family_names()` contains `"F23"` |
| 2 | **Double banner** under the auth-off posture | Medium | Medium | The single-banner discipline (Task 2) + a pixel-item assertion that counts the banners (Task 7) |
| 3 | The domain panel's error copy claims the wrong thing (a door class applied to a domain outcome) | High | Medium | Only the six reusable classes use `app-metrics-door-state`; the two domain-only outcomes carry a DOMAIN-LOCAL copy; the models spec asserts the copy table is exhaustive |
| 4 | The B4b headroom (19 815 B) is exceeded | Medium | Low | The estimated add is ≈ 8–14 kB; the re-measure is mandatory; **if the measured add exceeds the headroom, the lane must trim (drop the domain-local copy table's verbosity, shrink the service) — it may NOT raise the threshold** |
| 5 | The stat strip explodes (up to 66 cards) when many categories × severities are live | Medium | Medium | The strip renders the LIVE series only; the closed space bounds it at 66; the anomaly categories are a small set in practice; if the strip is judged unusable, the fix is to drop `statFamilyIds` (the panels still carry the values) — a manifest-only change |
| 6 | The FE carries a second copy of the category/severity vocabulary | High | Low | The chips render the raw strings; the severity tier comes from the route's `severity_rank`; the door value-enumerates the metric labels |
| 7 | The new tab's id collides with the existing `resource/anomalies/` panel | Medium | Low | Distinct paths (`tabs/nest-reports-tab.component.ts` vs `resource/anomalies/anomalies-panel.component.ts`); the copy names the data source explicitly (durable store vs the process-local resource journal) |
| 8 | The FE's `metrics.component.spec.ts` pins the tab count and goes red | Medium | Medium | The manifest spec's tab count + the shell's exact-cover spec are amended in the same commit (Task 1 / Task 5) |

---

## Exit criterion

Phase 4 is DONE when, in the lane worktree:

0. the tab's id is `nest-reports` and its **user-visible label is `Nest Reports`** — asserted by the tab pack (the label-namespace collision with the Resources tab's "Anomalies" panel is closed);
1. `?tab=nest-reports` renders: the F23 **step** panel (accepted reports over time, by category), the **line** panel (open clusters over time), the stat strip, and the incident table — with the auth ON;
2. with auth OFF (the LIVE posture): **exactly ONE** banner, **no Retry**, the runbook pointer visible, the incident section degraded-but-present, and **no request storm** (the empty catalog ⇒ the fetch effects return early);
3. the empty state ("no clusters in the window") is **distinguishable** from every error state (different copy, different `data-test`);
4. the FE carries **no** category/severity vocabulary (the chips render the raw values; the severity tier comes from `severity_rank`);
5. the manifest, the units table and the component map are consistent, and the map's exact-cover spec is green (8 tabs);
6. the **bundle guard exits 0** with the re-measured B1/B4a/B4b numbers **recorded against the frozen baseline** (the artifact is NOT overwritten), `frontend/package.json` is untouched, and the component-style warning SET is unchanged (9 entries);
7. the one pixel item is captured and its geometry assertions are recorded in the lane's `pixels/` dir;
8. the FE net-new-red diff against the TRUE pre-fix base is **EMPTY** (or every diff id is adjudicated in writing);
9. **the phase-6 mutation legs are PROVABLE**: M-4a (remove the tab from the manifest) → the tab pack reds; M-4b (render a placeholder series on 503) → the tab pack reds; M-4c (static-import the chart vendor in the new tab) → the existing bundle guard reds (`phase6-plan.md:117`). The tab pack ships as **`frontend/src/app/pages/metrics/metrics.nest-reports.spec.ts`**.
9a. **THE ARCHITECT'S FE AMENDMENTS ARE PINNED AND PROVABLE**: the rows are grouped by `(tree_root_instance_id, category)` with an **occurrence badge** (the cluster count) and the grouping never mutates a cluster (M-4f); the severity chip renders the **PEAK** label (M-4g); the excerpt renders **≤300 chars** with a reveal affordance and never raw (M-4e), and it is absent entirely unless `include_excerpt=true` was passed; the tab's template contains **NO `innerHTML` and NO `bypassSecurityTrust`** (M-4d) — the safety is a **pinned property**, not an accident.
10. **THE VIRGIN-NAMESPACE VERIFICATION (recorded):** `grep -rn "nest-report" frontend/src/ daemon/ agents/ --include=*.ts --include=*.py --include=*.json` ⇒ **0 hits** (verified on the lane base `1f6898ae2`, before any implementation) — the `nest-report*` namespace is virgin, so the leader's rename introduces no collision with an existing identifier. The same grep after implementation must return hits ONLY from this lane's own new surfaces (the category `nest-report-read`, the module `daemon/tools/nest_report_read.py`, the route prefix `/api/nest-reports`, and the three `meta.json` allow entries) — nothing else.
