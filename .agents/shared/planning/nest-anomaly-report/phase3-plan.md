# Phase 3: Observability — the F23 metrics family, the `nest-report-read` ops tools, and the anomaly domain route

Lane: **nest-anomaly-report** (board item `wi-2d691ed2`)
Phase owner: worker `plan-worker-registry-fe` (phases 3–4)
Status: Draft for the planner's review
Sibling phase: phases 1–2 (the `report_anomaly` write tool, the `anomaly_reports` / `anomaly_clusters` tables, the clustering, the critical→board link) — owned by a parallel worker; **this phase consumes, never re-specifies, its storage contract.**
Worktree (READ-ONLY source of truth for every anchor below): `/home/nea/ensemble-src-wt-nest-anomaly-report` @ `1f6898ae2`

---

## Objective

Make the sibling's anomaly data **observable without touching the frozen metrics door**: register the anomaly family as **F23** on the central metrics registry (so it appears in `metrics_families` / `metrics_snapshot` / `metrics_history` with **zero tool edit**), expose the **row-shaped, decision-ready** reads through a NEW privileged `nest-report-read` tool category, and expose the same decision-ready projection through a NEW **domain** REST route `/api/nest-reports/*` that is gated **identically** to the metrics door (the same any-of scope set `{maintenance:read | metrics:read}` in the same `dependencies=[scope_required(...)]` form, plus the fail-closed 503 `AUTH_NOT_CONFIGURED` — see Task 6).

Outcome test: with auth ON and a token carrying EITHER `maintenance:read` or `metrics:read` (the door's and the route's any-of set), `GET /api/metrics/families` lists `F23`; `anomaly_clusters` returns decision-ready clusters to a `maintenancer`; `GET /api/nest-reports/clusters` returns the SAME projection shape; with auth OFF, both the route and the door answer `503 AUTH_NOT_CONFIGURED` **before any read**.

---

## Binding inputs — the SSOT is `decisions.md` (the sibling's), cited here, not re-litigated

The lane's ruling SSOT is **`.agents/shared/planning/nest-anomaly-report/decisions.md`** (written 2026-11-04T22:30Z by the sibling plan worker). This phase **conforms** to it. The relevant entries, as applied here:

| Ruling (in `decisions.md`) | Content as applied here |
|---|---|
| **R1** (category vocabulary) | The vocabulary is **phase 1's**: `daemon/services/metrics_query/wiring/vocab.py` beside `WEDGE_REASONS` (`:66`), `__all__` (`:296` — the live position; `decisions.md` R1's text cites `:318`, the pre-existing drift) — `ANOMALY_CATEGORIES` (12 canonical + the reserved `other` = **13**) and `ANOMALY_SEVERITIES` (3). Phase 3 **consumes** it and edits `vocab.py` **zero** times. Fold-at-write-time ⇒ the stored `category` is already chart-legal ⇒ F23's AM-2 `allowed_values` passes with **no translation layer**. |
| **R3** (severity) | `minor \| major \| critical`, closed 3-tier; `SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}` (phase 2's service) is the **rank home** the FE's chip tiering consumes. |
| **R5** (clustering) | Write-time assignment; the cluster key is `(tree_root_instance_id, canonical category)`; `cluster_id` is assigned ONCE; **open ⇔ `last_report_at >= now − 1h`, DERIVED — no status column**; the rollup columns are `report_count`, `max_severity`, `first_report_at`, `last_report_at`. R5 also states WHY the second table exists: *"The F23 metric family's pull adapter needs ONE cheap aggregate (GROUP BY over cluster columns)"* — the metric reads the MATERIALIZED cluster row, never a query-time fold. |
| **R5b** (rate limits) | Only `outcome='accepted'` rows count; refusals are ledgered with `outcome='rate_limited'` and never consume budget. ⇒ the F23 counters are **two separate metrics** (accepted / rate-limited), never one blended number. |
| **R8** (read-surface split) | metric-shaped → `MetricsQueryProvider` via the EXISTING metrics-read tools (F23 appears **automatically**, zero tool edit); row-shaped → NEW privileged category `nest-report-read` **through the anomaly service** (never raw engine handles, never direct repository access from tool bodies); FE incident list → NEW **DOMAIN** route `/api/nest-reports/*` gated identically to the metrics door — a DOMAIN route, **NOT** a second metrics query path; the metrics door stays **FROZEN**. |
| **F23** (family identity) | id **F23**, `source_idiom="registry"`, `durability="durable"`. F23's slot is FREE (no `F23` registration anywhere under `daemon/services/metrics_query/`). **The disclosure:** R_i (census gated polarity inverted, `daemon/services/metrics_census.py:322-335` / `:347-355`) and R_ii (`capture_active` never wired, `daemon/api.py:2528-2533`) are **STILL LIVE**; the **registry idiom is UNAFFECTED** by both, so F23 proceeds — **DISCLOSE, do NOT fix**. F23's verification MUST assert the family actually **PUBLISHES series**, not merely registers (a silent-dark F23 must not ship looking green). |
| **R4** (the write category) | The sibling's `nest-report` category is **NON-privileged** and **does NOT touch** `PRIVILEGED_TOOL_CATEGORIES` (R4 keeps the 17-entry set + its pin sites frozen). **Phase 3 is the first lane to touch that set** (17 → **18**, `nest-report-read` = entry #18). |
| **Sequencing** | The sibling's phase 1 bumps `VOCABULARY_VERSION` **3 → 4** (with the artifact regen, R4). **Phase 3 bumps 4 → 5** (with its own artifact regen). Two commits, never one 3 → 5 jump. |

---

## Scope

### In scope
- `daemon/services/metrics_query/wiring/w4.py` (new) + the 2-line wire-in in `wiring/__init__.py`.
- The adjudication census extension for F23 (`wiring/adjudication.py`) + the matching pack amendment.
- `daemon/tools/nest_report_read.py` (new) + the `nest-report-read` privileged category + every registration/pin site.
- `daemon/routers/nest_reports.py` (new) + the mount + the two router manifest cages.
- The FE-facing wire contract (the response shapes the phase-4 FE consumes) — **defined here, implemented in phase 4**.

### Out of scope
- **Any edit to the central metrics door** (`daemon/routers/metrics.py`) — it is FROZEN at three routes (`tests/unit/metrics/test_central_metrics_contract_doc.py:146-147` pins the route block). F23 needs **no** door edit: the door discovers families from the registration-time metadata (`wiring/registry.py:47-58` `FamilySpec`).
- **Any edit to the existing metrics-read tools** (`daemon/tools/metrics_read.py`) — F23 appears in their output automatically.
- **The sibling's storage** — the tables, the clustering, the write tool, the board link are phase 1/2's.
- **The FE** — phase 4. This phase fixes the wire contract only.
- **The R_i / R_ii census defects** — disclosed, routed, not fixed.

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | **Single-source the anomaly vocabulary** + fix the phase-1→3 seam contract | phase 1 (tables) | One canonical constant; `wiring/vocab.py` re-exports it; the tool, the family and the phase-4 FE read the SAME set |
| 2 | **Register family F23** (`wiring/w4.py` + the CHART-PRIMARY ROSTER + wiring wire-in) | 1 | F23 appears in `registry.registered_families()`; F23 is in `roster_family_names()` (chart-primary ⇒ the FE renders a trend panel); **FIVE** metrics (three pull-fed + the AM-13f write-time suppression counter + C3's capacity-exhausted counter); closed labels; the band is **67** with the zero-headroom comment; the adapter degrades to no-op when `dsn is None` |
| 3 | **Extend the adjudication census** + amend the adjudication pack | 2 | The pack's F0.1–F22 census assertions are amended to F0.1–F23 in the SAME commit; the pack is green |
| 4 | **Build `daemon/tools/nest_report_read.py`** (2 tools, two-layer gate, budget, projection) | 1 | `anomaly_clusters` + `anomaly_report_detail` refuse correctly, build off-loop, and trim over the SHIPPED projection |
| 5 | **Register the `nest-report-read` category** (registry + construction + loader + 18 pin sites + 3 meta.json + artifact + VOCABULARY_VERSION) | 4 | The category is privileged (entry #18); all 18 privileged-set assertion sites are updated in ONE commit; the artifact is regenerated by the generator (never hand-edited) |
| 6 | **Build + mount the domain REST route** (+ the two router cages) | 4 | 503 fail-closed fires BEFORE any read; 403 on a scope miss; the mount/route/family cages are updated in the SAME commit |
| 7 | **Write the gate packs** + net-new-red discipline | 2,4,6 | Tier-1 packs green; the FAILED-list diff against the TRUE pre-fix base is EMPTY (or every diff id is adjudicated) |

---

### Task 1 — Consume the phase-1 vocabulary + fix the phase-1→3 read seam

**CONFORM: the sibling's rulings are the SSOT (`decisions.md` R1 / R5 / R8 / F23). This task does not re-litigate them — it states what phase 3 CONSUMES and what phase 3 ADDS.**

**(a) The vocabulary home — PHASE 1's, NOT phase 3's.**
`decisions.md` **R1** fixes the home: *"Initial vocabulary (≤15, defined in `daemon/services/metrics_query/wiring/vocab.py` beside `WEDGE_REASONS` at `:66`, `__all__` at `:318`)"* — **(quote reproduced verbatim; the LIVE `__all__` is at `:296` — the quote's `:318` is the decisions.md text and is not silently corrected here)** — 12 canonical categories + the reserved `other` (**13**), and `ANOMALY_SEVERITIES` (3: `minor|major|critical`, per R3).

⇒ **Phase 3 edits `wiring/vocab.py` ZERO times.** Phase 3 **imports** `vocab.ANOMALY_CATEGORIES` and `vocab.ANOMALY_SEVERITIES` — the same `vocab` module every other family uses (`wiring/w1.py:47` `from ...wiring import db, vocab`). The phase-3 family pack MUST assert that both tuples exist, that the reserved `"other"` is a member of `ANOMALY_CATEGORIES` (the fold target, `wiring/vocab.py:36` `RESERVED = "other"`), and that `len(ANOMALY_SEVERITIES) == 3`.
*(One observation, not a re-litigation: `wiring/vocab.py:5-12` states the module's doctrine "Every set below is derived FROM CODE, never hand-listed". R1 authors the INITIAL set by hand and treats extension as a later lane. Phase 3 neither challenges nor amends that — the initial authored set plus a documented extension path is R1's ruling.)*

**(b) The read seam — the SERVICE, per R8.**
`decisions.md` **R8** fixes the shape: *"Row-shaped … → NEW privileged category `nest-report-read`, an ops-trio through the anomaly service — never raw engine handles, never direct repository access from tool bodies."*

⇒ The seam, exactly as the sibling names it:
- **`daemon/services/anomaly_service.py`** — the sibling's service (phase 1 builds it, phase 2 extends it with clustering / rate limits / the critical→board gate / retention). **PHASE 3 ADDS THE READ HALF** to it: `list_clusters(...)` and `list_cluster_reports(...)`, returning the **decision-ready projections** (Task 4 + Task 6 consume them; the tools and the route share them — ONE projection, two transports).
- **`daemon/repositories/anomaly/repository.py`** — the sibling's repository (phase 1: insert + count + cluster helpers; phase 2: cluster lookup/insert/rollup + counting + retention). **PHASE 3 ADDS THE READ QUERIES** here (the bounded, paginated cluster list + the member-report list), because the service delegates persistence to the repository (the house pattern: `daemon/repositories/notify_audit/repository.py` under `daemon/tools/notify_user.py`).
- **`daemon/repositories/anomaly/models.py`** — `AnomalyReport`, `AnomalyCluster` (phase 1's SQLModel spec) — the column names phase 3's SQL targets.

**(c) The phase-1→3 seam contract (the binding this phase requires of phase 1/2):**
1. `anomaly_clusters` columns (phase-1's verbatim spec): `id` (uuid4 hex, PK — **the cluster identity**), `cluster_key_hash`, `tree_root_instance_id`, `category` (canonical), `first_report_at` (TEXT), `last_report_at` (TEXT), `report_count` (int), `max_severity` (TEXT), `board_item_id` (TEXT, NULL — set by phase 2's critical gate), `created_at` (TEXT). The composite index is `idx_anomaly_clusters_key_window (tree_root_instance_id, category, last_report_at)`.
2. `anomaly_reports` columns phase 3 reads: `agent_id`, `instance_id`, `tree_root_instance_id`, `category` (canonical), `category_raw`, `severity`, `description`, `excerpt`, `cluster_id`, `outcome`, `created_at` (TEXT), plus the harvest columns phase 3 does NOT project. The indexes include `idx_anomaly_reports_outcome_created (outcome, created_at)` and `idx_anomaly_reports_category_created (category, created_at)` — **both serve phase 3's counter aggregates**.
3. **"open" is DERIVED** (`last_report_at >= now − RESOLVE_WINDOW (1h)`, R5) and is **NOT** a stored column — no status column exists, so there is nothing to drift.
4. **`SEVERITY_RANK = {"minor": 1, "major": 2, "critical": 3}`** already exists in phase 2's service (`phase2-plan.md:37`) ⇒ the `severity_rank` the FE needs has a **home**; phase 3's projection reads it from there (one source; the FE carries no severity set).
5. **`cluster_id` = `anomaly_clusters.id`** (the uuid4 hex) — NOT `cluster_key_hash` (the sha256 of `"{tree_root}|{canonical category}"`, phase 2's `cluster_key`). The key hash is the **grouping** key; the id is the **identity**. The read surfaces expose the id (the board item points at the cluster, and the id is what a member's `cluster_id` column carries — phase-1's model spec: `cluster_id TEXT` on the report row).

*If the sibling's implementation names these differently, THIS phase binds to the CONTRACT, not the name — and the phase-1/3 interface test is written against the contract.*

---

### Task 2 — Register family F23 (`daemon/services/metrics_query/wiring/w4.py`, NEW)

**File inventory (exact):**

| File | Action | Purpose |
|---|---|---|
| `daemon/services/metrics_query/wiring/w4.py` | NEW | the W4 cluster: 3 registrations + `adapt_f23` + `register_w4_adapters()` |
| `daemon/services/metrics_query/wiring/__init__.py` | EDIT (2 lines) | import `register_w4_adapters` (beside the w1/w2/w3 imports at `:41-43`) + call it (beside the three calls at `:49-51`) |
| `daemon/services/metrics_query/wiring/vocab.py` | **NO EDIT** | phase 1 owns the vocabulary (R1) — phase 3 IMPORTS `vocab.ANOMALY_CATEGORIES` / `vocab.ANOMALY_SEVERITIES` |
| `daemon/services/metrics_query/roster.py` | EDIT | **F23 MUST join the chart-primary roster** (see the sub-block below) |
| `tests/unit/metrics/test_metrics_query_provider.py` | EDIT | `test_the_roster_carries_twelve_families` (`:843-847`): `12` → `13` |

**THE ROSTER IS LOAD-BEARING — a family OUTSIDE the roster renders NO TREND PANEL.**
`daemon/services/metrics_query/roster.py:20-22`: *"a family NOT in this roster is stat-primary (current value only) — the FR-C2 default"*. The mechanism, end to end:
1. `register_family_metric` derives `chart_role` **solely** from `roster_role_for(family, name)` (`wiring/registry.py:116`) — **there is NO other way to declare a chart role**;
2. a metric with `chart_role is None` is **stat-primary** (`daemon/checkpoint_metrics.py:662-669` `is_chart_primary`);
3. the FAMILY's role is `chart` iff **any** member is chart-primary (`daemon/services/metrics_query/provider.py:249-252`);
4. the FE's panel builder skips any family whose `role !== 'chart'` (`frontend/src/app/pages/metrics/tabs/central-tab.component.ts:514-529` `chartSpecs()`) — so a non-roster family renders the header-only "stat family — the values are in the strip below" section and **no panel at all**.

⇒ **F23 MUST be registered in `CHART_PRIMARY_ROSTER`**, otherwise the phase-4 tab has no count-over-time panel (the dispatch's explicit phase-4 requirement) and the family is stat-only.

**The roster rows (FIVE series, one family — the fourth is AM-13f's write-time suppression counter and the fifth is C3's capacity-exhausted counter):**
```python
RosterFamily(
    "F23",
    (
        RosterSeries("ensemble_anomaly_reports_total", CHART_ROLE_STEP, "{category}"),
        RosterSeries("ensemble_anomaly_reports_rate_limited_total", CHART_ROLE_STEP, "{category}"),
        RosterSeries("ensemble_anomaly_clusters_open", CHART_ROLE_LINE, "{category,severity}"),
        RosterSeries("ensemble_anomaly_board_items_suppressed_total", CHART_ROLE_STEP, ""),
        RosterSeries("ensemble_anomaly_capacity_exhausted_total", CHART_ROLE_STEP, ""),
    ),
),
```
- **The counters render STEP** (a counter rendered LINEAR would assert values that were never observed — `roster.py:17-18`); **the open-cluster gauge renders LINEAR** (a gauge rises and falls; a line is the honest mode). The suppression counter is **unlabelled** ⇒ its label template is `""` (the house precedent for an unlabelled rostered series, `wiring/w1.py:159` `ensemble_waiting_children_count`).
- **WHY THE UNLABELLED ONES ARE ROSTERED TOO (AM-2c's general pin):** the roster coverage is per (family, NAME) and the architect's general pin — *"every registered metric of a roster family has a roster entry"* — is **family-scoped**: if an unlabelled F23 metric were NOT rostered, the general pin REDS (or, if the pin were weakened to "the labelled ones", the un-rostered metric would silently degrade to stat-primary INSIDE a chart family — the exact invisible failure AM-2c names). ⇒ **ALL FIVE F23 metrics are rostered**; the family pack asserts **all five** roles (the architect's "ALL THREE" covered the three label-bearing series — the two write-time counters are covered by the same general pin, and the statements together are the fence; C3's capacity counter extends the count from four to five).
- The roster's `labels` field is the family's own label template (documentation of the series' label shape — `roster.py:39-46`); the door never consumes it (the labels come from the registry's `allowed_values`).
- **The roster's own invariant must be amended:** `assert_roster_is_well_formed` asserts exactly **12** chart-primary families (`roster.py:236-238`) → **13**; the module docstring's "= **12 families**" (`roster.py:7`) → 13. The `F2` absence check (`roster.py:241-245`) and the role-validity check (`roster.py:246-251`) are untouched.
- **The pack that pins it:** `tests/unit/metrics/test_metrics_query_provider.py:843-847` `test_the_roster_carries_twelve_families` → `13` (same commit).
- **Effect on the FE (the phase-4 hand-off):** F23's family role is `chart` ⇒ the shared central body renders **TWO panel groups**, not four: `step::count` (the two labelled counters — the count-over-time by category — AND BOTH unlabelled write-time counters (AM-13f's suppression + C3's capacity) share the same kind+unit key, so they land in ONE step panel) and `line::count` (the open-cluster gauge). The FE derives the per-name role from the wire (`chart_role` in the catalog projection, `provider.py:239`); the FE carries NO role table.

**The FIVE registrations (module TOP LEVEL, so the import IS the registration — `wiring/__init__.py:19-25`):**

| metric | kind | label keys | allowed_values | verdict | durability |
|---|---|---|---|---|---|
| `ensemble_anomaly_reports_total` | counter | `("category",)` | `{category: ANOMALY_CATEGORIES}` (13) | `IN` | `durable` |
| `ensemble_anomaly_clusters_open` | gauge | `("category", "severity")` | both (13 × 3) | `IN` | `durable` |
| `ensemble_anomaly_reports_rate_limited_total` | counter | `("category",)` | `{category: ANOMALY_CATEGORIES}` (13) | `IN` | `durable` |
| **`ensemble_anomaly_board_items_suppressed_total`** (AM-13f) | counter | **NONE** | — (no label keys ⇒ no `allowed_values` entry) | `IN` | **`process`** |
| **`ensemble_anomaly_capacity_exhausted_total`** (**C3**) | counter | **NONE** | — (no label keys ⇒ no `allowed_values` entry) | `IN` | **`process`** |

**THE FOURTH METRIC (AM-13f) — the board-suppression counter.** AM-13's ceiling fence (`max_board_items_per_tree_root_per_hour`, default 3; `max_board_items_per_hour_global`, default **20** — **W-6: 50→20 (global ceiling; per-tree 3/h unchanged)**: 20/h ⇒ 480/day, the architect's stated harm bound, and a uniform multi-tree flood at 20/h/type now trips it; `decisions.md` AM-13 records the W-6 audit trail) makes `ensure_board_surface` **append a NOTE to the tree's most recent anomaly board item instead of creating a new item** when a ceiling is hit — and that suppression must be **COUNTED**. It is a **write-time** counter (incremented in the anomaly service at the suppression point), **not** a pull-adapter aggregate ⇒ **the adapter does NOT set it**.
- **Registration shape (the family's pattern, with the one honest difference):** registered in the SAME `w4.py` module through the SAME `register_family_metric` helper (so it carries `family="F23"`, `cluster="W4"`, `verdict="IN"`, `source_idiom="registry"`, `cardinality_weight=1`) with `kind="counter"`, **NO label keys** and therefore **NO `allowed_values` entry** (AM-2 only requires an `allowed_values` entry for a DECLARED label key — `wiring/registry.py:102-107` — so an unlabelled counter is legal), and `durability="process"` (it is an **in-RAM** counter that resets on restart — honest for a suppression count; the OTHER three are durable because they read rows).
- **The increment path (layering):** the service increments the handle with a **LAZY import** (`from daemon.services.metrics_query.wiring.w4 import board_items_suppressed` inside the write path) — a top-level import would drag the whole wiring package (w1/w2/w3) into the service's import graph for one counter. The handle is the module-level name the registration returns.
- **Band impact:** +1 series (an unlabelled counter contributes exactly ONE closed-label-space series) ⇒ **66 with AM-13f ALONE**; C3's capacity counter adds the second `+1` ⇒ the declared band is **67** (see the arithmetic below).
- **A family-projection disclosure:** the family's projected `durability` comes from the **first member** (`daemon/services/metrics_query/provider.py:246-247`), so F23's projected durability is whichever member sorts first — the family's members are mixed (3 durable + 1 process). This is the house's per-family projection rule, not an F23 defect; the family pack records it.

**THE FIFTH METRIC (C3(i)) — the CAPACITY-EXHAUSTED counter (the approver's LIVENESS + DoS condition).**
AM-9's fences refuse on capacity (`max_rows_global` 50,000 / `max_rows_per_tree_root` 2,000 / `max_accepted_per_hour_global` 5,000) by returning the **`capacity-exhausted`** token with **NO ledger row** — and, before this amendment, **NOTHING COUNTED those refusals**. Consequence the approver named: the nest can go **DARK** (a per-tree capacity wall blocking a tree — *including its criticals* — until the idle-gated 30-day sweep drains it) with **NO signal anywhere**. The counter is that signal.
- **Registration shape — identical to AM-13f's, through the same helper:** `family="F23"`, `cluster="W4"`, `verdict="IN"`, `source_idiom="registry"`, `cardinality_weight=1`, `kind="counter"`, **NO label keys** (⇒ no `allowed_values` entry; AM-2 only constrains DECLARED label keys — `wiring/registry.py:102-107`), `durability="process"` (in-RAM, resets on restart — honest for a refusal count), cardinality class **`none`**.
- **The increment path:** the **SERVICE** increments it at the **capacity-refusal point** (a lazy import of the handle, exactly like AM-13f's: `from daemon.services.metrics_query.wiring.w4 import capacity_exhausted` inside the write path) — **the pull adapter does NOT set it**, because it is not a table aggregate.
- **THE SIGNAL DOMAIN — say this in the `help` text:** **the GLOBAL capacity refusal (the nest-wide wall)**, NOT the per-tree one. Reason (C3's fence side): the **per-tree cap now TRIMS** — it sheds the tree's oldest rows (batched, decrementing the cluster counts) instead of refusing — so a per-tree hit no longer produces a `capacity-exhausted` refusal at all. The counter therefore measures **the one wall that can still darken the whole nest**: the global `max_rows_global` / `max_accepted_per_hour_global` refusal. A help text that said "capacity refusals" without that scoping would misreport the counter's meaning.
- **Band impact:** `+1` series (unlabelled ⇒ exactly one) ⇒ **the band arithmetic becomes 67** (see below).
- **THE HELP TEXT (wording requirement):** *"Refusals at the GLOBAL capacity wall (`max_rows_global` / `max_accepted_per_hour_global`) — the nest-wide bound. The per-tree cap does not fire here: it TRIMS the tree's oldest rows instead of refusing. A non-zero rate means the nest is at its storage ceiling and new reports (including criticals) are being refused."*

**THE `help` TEXTS (AM-7) — `max_severity` IS THE PEAK SEVERITY, and every surface must SAY so.**
The cluster's `max_severity` is a **max-rank rollup** (it never de-escalates; a NEW cluster for the same key starts fresh — AM-7). ⇒ the `help` of `ensemble_anomaly_clusters_open` (and of every read surface that renders it) must phrase it as the PEAK: the house phrasing is **"critical (peak)"** — e.g. *"the number of OPEN clusters by canonical category, labelled by their PEAK severity (critical (peak) = the highest severity seen in that cluster's window)"*. A bare "max_severity" or "severity" label would assert a current severity the store does not hold.

Every registration carries: `family="F23"`, `cluster="W4"`, `source_idiom="registry"`, `durability="durable"`, `cardinality_weight=1`, and a `seam` string naming **the owning table + the adapter** (house style — `wiring/w1.py:63` names `daemon/services/resource_monitor/flusher.py:161 + sampler.py`). Proposed seams:
- `ensemble_anomaly_reports_total` → `"anomaly_reports (the durable table) + daemon/services/metrics_query/wiring/w4.py::adapt_f23"`
- `ensemble_anomaly_clusters_open` → `"anomaly_clusters (the durable table) + daemon/services/metrics_query/wiring/w4.py::adapt_f23"`
- `ensemble_anomaly_reports_rate_limited_total` → `"anomaly_reports.outcome (the durable table) + daemon/services/metrics_query/wiring/w4.py::adapt_f23"`
- `ensemble_anomaly_board_items_suppressed_total` → `"the anomaly service's board-suppression point + daemon/services/metrics_query/wiring/w4.py (write-time increment)"` (no adapter seam — it is not a table aggregate)
- `ensemble_anomaly_capacity_exhausted_total` → `"the anomaly service's capacity-refusal point + daemon/services/metrics_query/wiring/w4.py (write-time increment)"` (no adapter seam — it is not a table aggregate)

**The `help` text MUST state the semantics honestly.** `ensemble_anomaly_reports_total` is `kind=counter` but is fed by a `COUNT(*)` over stored rows, so its value can **decrease** when retention prunes. This is the **house precedent** — `ensemble_wedge_capture_total` is `kind=counter` fed by `SELECT trigger_reason, COUNT(*) FROM wedge_captures GROUP BY trigger_reason` (`wiring/w1.py:157-173` + `:477-500`) — so the shape is sanctioned. The help text must say "the number of accepted anomaly reports **currently stored** (a stored-row count, not a monotonic increment — retention can lower it)". The FE renders it as a **step** panel (the FE maps `counter → step`, `gauge → line` via `metricsChartRoleForKind`, `frontend/src/app/models/metrics.model.ts`), and a step chart of a non-monotonic value is honest as long as the label says "stored".

**`adapt_f23` — the house pull-adapter SHAPE, with ONE deliberate difference from the older `wiring/w1.py:477-500` adapter: the fold step is NOT pre-done.** The shape is `current_sources()` → `dsn is None` early-return → `db.one_shot` (closed inside the tick) → `db.fetch_rows` → `int()` guards → `.set()`. **The label step differs: the RAW stored token goes to `.set()` and the REGISTRY folds it** (`daemon/checkpoint_metrics.py:704-723` — `_fold_labels` folds the disallowed value to the reserved one and `_count_invalid_label` counts it in `ensemble_metrics_invalid_label_total{metric,label}`) — that is AM-2d, and it is the OPPOSITE of the older adapter's pre-fold, which AM-2d supersedes.**
1. `sources = current_sources()`; **`if not sources.dsn: return`** (no DSN ⇒ no series; `wiring/sources.py:118` / `:368`);
2. `with db.one_shot(sources.dsn, application_name="ensemble-metrics-w4-anomaly", connect=sources.db_connect) as conn:` — one-shot psycopg, closed inside the tick, **never a pool** (`wiring/db.py:104-152`; the pack `tests/unit/metrics/test_metrics_family_adjudication.py:252-258` asserts the wiring package never imports sqlalchemy — a raw-SQL adapter is therefore MANDATORY, the ORM/repository path is impossible here);
3. `rows = db.fetch_rows(conn, sql)` — a failing query is a rate-limited WARNING, never a dead tick (`wiring/db.py:157-180`);
4. `int()` guards on every count (`try: value = int(count) / except (TypeError, ValueError): continue`);
5. **AM-2d — THE REGISTRY FOLDS, THE ADAPTER DOES NOT.** The adapter passes the **RAW stored token** to `.set()` and lets the **registry** fold it: `metric.set(value, category=str(raw))`. **The adapter's own pre-fold-to-`other` step is REMOVED.**
   *Why (the architect's finding, exact):* `other` **IS** a vocabulary member, so a pre-folded category is **indistinguishable** from a genuine `other` — the registry's own invalid-label counter (`ensemble_metrics_invalid_label_total`, `daemon/checkpoint_metrics.py:706-720`) can then **NEVER fire**, and future vocabulary drift (a category removed; old rows still carry the now-invalid value) is **silently absorbed**. Letting the registry fold gives **identical output** and turns that counter into a **real drift signal at zero cost**.
   *The anchor is the REGISTRY'S OWN LABEL HANDLING — `daemon/checkpoint_metrics.py:704-723`: `_fold_labels` folds a disallowed label value to the reserved value and returns the list of folded label keys, and `_count_invalid_label` counts each folded key in `ensemble_metrics_invalid_label_total{metric,label}`.* **F3 IS NOT THAT PRECEDENT — F3 IS THE OLD SHAPE AM-2d SUPERSEDES:** `wiring/w1.py:488-500` **PRE-FOLDS** (`if token not in vocab.WEDGE_REASONS: token = vocab.RESERVED` — the clamp happens BEFORE `.set()`), so F3's registry-side invalid-label counter can never fire for a pre-folded token. **AM-2d reverses that shape for F23.** The phase-3 family pack **must pin** that the adapter contains NO pre-fold (`token not in vocab.ANOMALY_CATEGORIES` must NOT appear in `w4.py`) and that an out-of-vocabulary stored value **increments `ensemble_metrics_invalid_label_total`** (the drift signal), while the published series still shows `other`;
6. `.set(value, category=token)` — the metric handle is a module-level name, exactly as `wiring/w1.py:497` does.

**The three queries (the family is ONE adapter, three statements — the TWO unlabelled write-time counters (AM-13f's suppression + C3's capacity) are NOT adapter-fed), against phase 1's verbatim column names:**

```sql
-- ensemble_anomaly_reports_total
SELECT category, COUNT(*) FROM anomaly_reports WHERE outcome = 'accepted' GROUP BY category;

-- ensemble_anomaly_reports_rate_limited_total
SELECT category, COUNT(*) FROM anomaly_reports WHERE outcome = 'rate_limited' GROUP BY category;

-- ensemble_anomaly_clusters_open
SELECT category, max_severity, COUNT(*)
  FROM anomaly_clusters
 WHERE last_report_at::timestamptz >= now() - interval '1 hour'
 GROUP BY category, max_severity;
```

- **`outcome = 'accepted'` ONLY** for the report counter — R5b's rule: *"Refused rows never consume budget (counting queries filter `outcome='accepted'`)"* (`decisions.md` R5b). The rate-limited counter is the **refusal census** (R5b: *"the refusal IS the spam signal"*) — a separate metric, never folded into the accepted total.
- **The "open" predicate is computed IN THE QUERY**, from the DERIVED rule (R5: *"Resolution is DERIVED, never stored: open ⇔ `last_report_at >= now − RESOLVE_WINDOW` (1h) — no status column"*), so no stored boolean can drift from the definition.
- **AM-4 (the architect's CORRECTED PREMISE — the cast is retained as BELT-AND-BRACES, not as the primary defence): `last_report_at::timestamptz >= now() - interval '1 hour'`.** Two workers independently verified all four boundary cases: a **lexicographic ISO compare IS order-safe for the current producer** (`'+'` 0x2B `<` `'.'` 0x2E puts the zero-microsecond form exactly at its chronological position). **This plan's earlier premise (that the TEXT form is not fixed-width and therefore not comparable) was WRONG and is withdrawn.**
  *The REAL hazards, per AM-4:* (i) the **PG-RENDERED right-hand side** — `DateStyle` renders `timestamptz` with a **SPACE** separator, so a TEXT compare degenerates to "all rows match"; **the explicit cast removes that hazard**; (ii) **fragility to producer drift** — which is why **phase 1's fixed-width fence** (`datetime.now(timezone.utc).isoformat(timespec='microseconds')` — always 32 chars, always `+00:00`, plus the `timestamp_shape_is_fixed_width` pin) is the actual protection; (iii) the **phase-2 rate COUNTs as written ERROR on PG** (`created_at >= now() - rate_window_seconds` compares TEXT against timestamptz — no such operator) — **that is phase 2's fence** (compute the cutoff in Python and bind it, the house idiom at `daemon/repositories/notify_audit/repository.py:246-253`); it does not touch phase 3.
  PG-only syntax is acceptable: the adapter only ever runs on PG — `resolve_dsn` returns `None` for a non-PostgreSQL deployment (`daemon/services/metrics_census.py:189-200` — *"a SQLite backend ⇒ the census is simply unavailable"*) and the adapter's `dsn is None` early return covers SQLite. The PG-specific precedent already exists in the wiring package (`wiring/w2.py:459-460` `'checkpoints'::regclass`).
- The index `idx_anomaly_reports_category_created (category, created_at)` serves the first aggregate; `idx_anomaly_reports_outcome_created (outcome, created_at)` serves the outcome filter; the cluster aggregate is a scan (a `GROUP BY` over a small table — the index is irrelevant for it).

**Cardinality arithmetic (the closed space — the honest upper bound, `tests/unit/metrics/test_metrics_family_adjudication.py:75-95` counting convention):**
`13 + (13 × 3 = 39) + 13 + 1 (the unlabelled AM-13f suppression counter) + 1 (C3's capacity-exhausted counter) =` **67 series**, with `allowed_values` for EVERY declared label ⇒ `_cardinality_class` returns `"closed"` for the three labelled metrics (`daemon/services/metrics_query/provider.py:183-191`); the two unlabelled write-time counters are `"none"` by definition. Per-metric cap is 2000 (`daemon/checkpoint_metrics.py:129` `DEFAULT_CARDINALITY_CAP`) ⇒ 13/39/13/1/1 are **negligible** against it. ⇒ **`FAMILY_SERIES_BANDS["F23"] = 67`** is the band to declare (Task 3).

**N1 — THE AUTHORITATIVE CLOSED-SPACE FIGURES, WITH THEIR PROGRESSION (and the D-5 sourcing rule).**
- **The lane's closed-space total = 425 → 492**, and **the F23 band = 67** — the figures the ceremony text and the stage manifest carry.
- **PROGRESSION (record it, so a reader is never surprised by an older number):** `architecture-recommendation.md:205` records **425 → 490** at the **4-member** state (three pull-fed + AM-13f's suppression counter, band 66 / total 491); **C3's capacity-exhausted counter adds the fifth member ⇒ band 67 / total 492.**
- **D-5 SOURCING RULE (binding):** the ceremony text and the morning report **derive the closed-space figure from `decisions.md`** (the source of truth), **never from `architecture-recommendation.md`** — whose `425 → 490` / 66 figures are the **HISTORICAL RECORD** of the pre-C3 state and **must not be edited** (the amendment file is append-only history). **This plan states 492 / 67 as the CURRENT authoritative pair.**

**AM-2(a) — THE BAND HAS ZERO HEADROOM, AND THAT MUST BE WRITTEN DOWN.**
The band equals the measured value **exactly** (67 = 67) ⇒ **ANY** future vocabulary extension (R1's later lane adds a category ⇒ 13 → 14 ⇒ the closed space grows) **REDS the band assertion** — by design, but the implementer must know it. Two obligations, both binding:
1. **The band is RE-DECLARED WITH THE VOCABULARY, in the same change** — R1's later lane (a vocabulary extension) MUST bump `FAMILY_SERIES_BANDS["F23"]` in the same commit as the `vocab.py` extension, and the family pack's band assertion is what proves it happened.
2. **The zero-headroom fact goes in the CODE COMMENT** on the band entry (`wiring/adjudication.py`) — e.g. *"F23's band equals its measured closed space exactly; a vocabulary extension MUST re-declare it in the same change (AM-2a)"* — so the next implementer reads the constraint at the site, not in a plan.

**One adapter, not five:** `register_w4_adapters()` registers `("F23", adapt_f23)` — the adapter runs the three aggregate queries; **BOTH unlabelled write-time counters** (AM-13f's `ensemble_anomaly_board_items_suppressed_total` and C3's `ensemble_anomaly_capacity_exhausted_total`) are incremented by the SERVICE at their write points (a lazy import of each handle), never by the adapter — the sampler's stage A calls the adapters in registration order and a broken adapter never kills the tick (`daemon/services/metrics_query/sampler.py`, stage A/B split).

---

### Task 3 — Extend the adjudication census + amend the adjudication pack

**The census is a HARD gate: the pack as shipped asserts the family universe is exactly F0.1–F22.**

| File | Action | Detail |
|---|---|---|
| `daemon/services/metrics_query/wiring/adjudication.py` | EDIT | (a) `CATALOG_FAMILY_VERDICTS` — add `"F23": ("IN", "<the anomaly tables> + w4.py::adapt_f23>")`; (b) `REGISTERED_FAMILIES` — append `"F23"`; (c) `FAMILY_SERIES_BANDS` — add **`"F23": 67`** (AM-13f's suppression counter AND C3's capacity counter included) **with the AM-2(a) zero-headroom comment**; (d) the module docstring's "10 full + 6 partial = 16 of 26" line → "11 full + 6 partial = 17 of 27" |
| `tests/unit/metrics/test_metrics_family_adjudication.py` | **EDIT — AM-2(b)** | **ADD the band-coverage pin** `assert set(adjudication.FAMILY_SERIES_BANDS) ⊇ set(adjudication.REGISTERED_FAMILIES)` — the band check (`test_each_family_stays_inside_its_declared_band`, `:421-429`) iterates the BANDS, so a registered family with **no band entry is silently SKIPPED** — the family would carry NO cap at all |
| `tests/unit/metrics/test_metrics_family_adjudication.py` | EDIT | `test_every_catalog_family_carries_exactly_one_verdict` (`:104-111`): `len(verdicts) == 26` → `27`, and `expected` gains `"F23"`; `test_the_binding_subset_is_sixteen_of_twenty_six` (`:113-119`): `len(full) == 10` → `11`, and the method name/prose becomes "seventeen of twenty-seven"; `test_the_registered_families_match_the_verdicts` (`:121-123`) passes unchanged once `REGISTERED_FAMILIES` carries F23 |

**Why the census amendment is MANDATORY and not optional:** `test_the_binding_subset_is_sixteen_of_twenty_six` asserts `sorted(full + partial) == sorted(REGISTERED_FAMILIES)`. Registering F23 **without** the verdict-table entry makes that equality fail — i.e. the census is a two-sided pin, and a family cannot be registered without a verdict. This is the house's "every family carries exactly ONE verdict with a seam anchor" discipline (`wiring/adjudication.py:1-9`), not an obstacle.

**Pack files that need NO change (verified):** `tests/unit/metrics/test_metrics_registry_narrowing.py:133-136` reads `REGISTERED_FAMILIES` dynamically ⇒ F23 is covered automatically; `tests/unit/metrics/test_central_metrics_contract_doc.py` pins the door's three routes ⇒ untouched (the door is frozen).

---

### Task 4 — Build `daemon/tools/nest_report_read.py` (the two ops tools)

**Factory signature (house parity with `daemon/tools/metrics_read.py:1332-1354`):**
`create_nest_report_read_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None) -> list`

**The two tools:**

| tool | arguments | returns |
|---|---|---|
| `anomaly_clusters` | `window_s?` (default 3600, clamp ≤ 7200 = `WINDOW_SECONDS`), `severity?` (one of the 3), `category?` (one of the 13), `limit?` (default 20, **≤ 50** = `MAX_TOP_N`) | **decision-ready GROUPS** (AM-6c — the SAME grouped projection the route returns; one projector in the service, two transports): `tree_root_instance_id`, `category`, `severity` + `severity_label` (the group's **PEAK across its clusters**, AM-7) + `severity_rank` (from the service's `SEVERITY_RANK`), `report_count` (summed), `occurrence_count` (the **CLUSTER** count in the group), `cluster_ids` (the member ids), `last_report_at` (the newest member), `open: bool` (**server-derived**, R5 — ANY member open), `board_item_ids?` (nullable — set by phase 2's critical gate), **plus** a per-category breakdown and a freshness line |
| `anomaly_report_detail` | `cluster_id` (required — the `anomaly_clusters.id`), `limit?` (default 20, **≤ 50**), **`include_excerpt?` (default FALSE — AM-12b)** | the cluster's member reports: `agent_id`, `instance_id`, `created_at`, `category` (the canonical value) + `category_raw` (the preserved authored text), `severity`, `description` (the already-scrubbed stored text — **never a re-scrubbed re-render**), `outcome` — **plus `excerpt` ONLY when `include_excerpt=true` was passed** (AM-12b: the stored, bounded, scrubbed excerpt — never a fresh transcript read; the excerpt is the heaviest field and is opt-in) |

**The nine disciplines, copied VERBATIM from `daemon/tools/metrics_read.py`:**

1. **Two-layer gate.** The category is PRIVILEGED (entry #18) **AND** every body calls one shared module-level helper — `NEST_REPORT_READ_AUTHORIZED_AGENTS = frozenset({"maintenancer", "devops", "wanderer"})` (`metrics_read.py:204-209` precedent) — because a LITERAL tool name in an allow list bypasses the category gate outright (`metrics_read.py:9-19`). The helper is fail-closed on a blank/non-`str` `agent_id` (`metrics_read.py:403-412`).
2. **Refusal ladder (4 tokens).** `unknown-agent` · `not-authorized` · `anomaly-unavailable` · `invalid-input`, mirroring the door's status semantics IN-PROCESS (`metrics_read.py:230-240` precedent). Authorization fires FIRST — an unauthorized caller must never learn whether the substrate is mounted.
3. **The house refusal envelope.** `Error: {label} REFUSED — reason={token}: {message}` (`metrics_read.py:417-424`).
4. **Tools never raise.** Every failure returns a typed refusal string.
5. **Off-loop body build.** `await _offload(fn, ...)` on the dedicated `db-offload` executor (`metrics_read.py:1320-1329` → `daemon/services/db_offload.py:386`). The event loop never pays for the projection build.
6. **Never raw engine handles — read through the ANOMALY SERVICE.** Per R8, the tools call the **service** (`daemon/services/anomaly_service.py`), never the repository and never `manager.engine`: the service owns the scrub / rate / audit / board invariants, and a tool body that reached past it would bypass them. **Phase 3 adds the service's read half** (`list_clusters(...)` / `list_cluster_reports(...)`) — the service delegates persistence to `daemon/repositories/anomaly/repository.py` (phase 3 adds the read queries there). The house pattern is the notify_audit pair: `daemon/tools/notify_user.py` → `daemon/repositories/notify_audit/repository.py`. **No REST-scraping, no direct repository access, no raw SQL in a tool body.**
7. **OUTPUT BUDGET, copied whole.** `RESPONSE_BYTE_CEILING` = 1 048 576 (`daemon/services/metrics_query/constants.py:87`, imported — never a duplicated literal, `metrics_read.py:118`); `_CAVEAT_HEADROOM_BYTES` = 2048 (`metrics_read.py:254`); `MAX_NAMES_PER_FAMILY` = 200 (`:244`); `MAX_NAMES_TOTAL` = 2000 (`:249`); the trim is a **binary search over the SHIPPED projection** (`metrics_read.py:451-481` — the C1 regression handed the helper the RAW rows while the payload carried the PROJECTION; the probe must measure the form the tool actually returns, `json.dumps(..., indent=2)` — `metrics_read.py:437-449`).
8. **The SIX-member bound block**, identical in both tools: `matched_count`, `returned_count`, `dropped_count`, `more_available`, `truncated`, `narrow_hint` (`metrics_read.py:645-658`).
9. **The narrow_hint must name the levers THIS body accepts** (`metrics_read.py:262-270` `_BODY_NARROW_LEVERS`): `anomaly_clusters` accepts `window_s=`, `severity=`, `category=`, `limit=`; `anomaly_report_detail` accepts `limit=` (and the cluster id).

**Two more rules the sibling's phase-6 gate expects (conformed here):**
- **FRESHEST-FIRST ORDER.** `anomaly_clusters` orders by `last_report_at DESC` (then `report_count DESC`, then `id` for a stable tie-break) — the decision-ready order is "the most recent incident first", not the insertion order. `anomaly_report_detail` orders its members by `created_at DESC`.
- **THE DEGRADED-READ BLOCK.** When the anomaly substrate is unavailable (the service cannot reach the store), the tools do NOT vanish and do NOT raise: they return the typed `anomaly-unavailable` refusal PLUS a degraded block that states the read's scope and when it happened (the `metrics_read` RULING-1 shape: the tools stay PRESENT and refuse LOUDLY — "for a diagnosis surface, silent absence is the worst failure mode").

### THE ONE-DOOR INVARIANT (pinned)

> **ONE DOOR PER DOMAIN.**
> - The **row-shaped** anomaly read surfaces resolve through **THE ANOMALY SERVICE** (`daemon/services/anomaly_service.py`) and nothing else: **never a raw engine handle** (the read-path purity rule — mutation **M-3c** → red), **never direct repository access from a tool body**, and **never REST-scraping any door** (a tool body that constructs an HTTP client is a mutation → **red**; the negative pin is part of the read-tool pack).
> - The **metric-shaped** surfaces resolve through **`MetricsQueryProvider`** — via the **EXISTING** `metrics-read` tools, which phase 3 does **not** touch (F23 appears in them automatically).

### THE CONTRADICTION FLAG (survey vs ruling — RECORDED, not silently resolved)

> The leader's item **8(a)**, as literally worded — *"the anomaly-read tools consume `MetricsQueryProvider` ONLY"* — is **UNSATISFIABLE for row-shaped data**:
> - the provider publishes **SERIES only** (the read-path purity rule — `daemon/services/metrics_query/provider.py`; the frozen door has **three series-shaped routes and no incident-row route**);
> - forcing cluster rows through the provider would either put **ids in labels** (⇒ **OPEN cardinality**, an AM-2 violation) or require a series-per-cluster projection (an unbounded label space);
> - and it **CONTRADICTS the plan's own R8 split**, which the leader's fold item **8(b)** presupposes: the domain route exists **precisely because rows are not series**.
>
> **PLANNER ADJUDICATION (the plan's binding form): the one-door invariant is PER-DOMAIN** — the **anomaly service** for the row-shaped anomaly surface; **`MetricsQueryProvider`** for the series-shaped metric surface (through the existing metrics-read tools). **The literal provider-ONLY reading is impossible for cluster rows and is recorded as a survey-vs-ruling discrepancy, not silently resolved.** Under EITHER reading, an implementation that lets the read tools reach a raw engine handle — or scrape a door — is wrong.

**AM-12(a)/(b) — THE `excerpt` FIELD IS OPT-IN AND LIST-EXCLUDED (binding).**
- **(a) The LIST projection EXCLUDES `excerpt` ENTIRELY.** Both `anomaly_clusters` (the tool) and `GET /api/nest-reports/clusters` (the route) project **NO `excerpt` key at all** — not "an empty excerpt", not a truncated one: **the key is absent**. The excerpt is raw tool stdout harvested from a transcript; the list is the operator's morning scan, and the excerpt belongs only to a deliberate drill-down.
- **(b) `excerpt` appears ONLY in the DETAIL projection, behind an explicit `include_excerpt=true`** (the tool's argument; the route's `?include_excerpt=true` query parameter). The default is **FALSE** ⇒ the key is absent from the detail payload too.
- **The pins (both in the read-tool pack AND the route pack):** `list_clusters_payload_has_no_excerpt_key` (mutation: add the key → **red**) and `excerpt_requires_include_excerpt_true` (mutation: emit the excerpt without the flag → **red**).
- **Why this matters (the architect's framing):** the excerpt is the field that AM-11's scrub hardening and AM-8's bounded read exist to protect. Making it opt-in at the projection boundary is the structural half of that fence — the surface does not carry it by default, so a list consumer cannot leak it by accident.

**AM-7 (the PEAK labelling) on the read surfaces too:** every projection that renders the cluster's severity labels it as the **PEAK** (the house phrasing "critical (peak)") — the tool bodies, the route payloads (the field carries a `severity` string AND a `severity_label` that says "critical (peak)"), and the FE chip (phase 4, AM-7). A bare "severity" label would assert a current severity the store does not hold (the rollup is a max-rank over the cluster's window).

**AM-5 (a note, not a change):** with AM-5's terminal outcome `unclustered`, **`outcome='accepted'` MEANS clustered** — the orphan rows are terminalized by the bounded age-gated sweep and NEVER reassigned. ⇒ phase 3's **accepted-only filters are now EXACTLY RIGHT**: the metric counts accepted (clustered) rows, and the route serves by `cluster_id` — the metric/route visibility split is **repaired** (an orphan used to make both disagree). The phase-3 pack asserts `accepted` rows all carry a `cluster_id` (the `no_orphan_accepted_rows` pin, AM-5, is phase 2's; phase 3 asserts the consequence on the READ side).

**Never-restricted semantics (the honest-disclosure duties):** a `board_item_id` that is present means the critical→board link exists (phase 1); `open` is derived (R5) and the body states the derivation; the freshness line names the window and the query instant (server-anchored, never the client clock).

---

### Task 5 — Register the `nest-report-read` category (the 10-step checklist, EVERY site)

**Step-by-step, with the exact file/line every step touches:**

| Step | File : line | Action |
|---|---|---|
| 1 | `daemon/tools/nest_report_read.py` (new) | `@register_tool_category("nest-report-read")` **ABOVE** `@tool` on both tools (decorator order is pinned — `tests/unit/tools/test_metrics_read_registration.py:185`) |
| 2 | `daemon/tools/_tool_registry.py` `CATEGORY_MODULES` (~`:1125`, beside `"metrics-read": "daemon.tools.metrics_read"`) | add `"nest-report-read": "daemon.tools.nest_report_read"` — the value MUST be a `daemon.tools.*` path (the AST scanner resolves `daemon/tools/<parts[2:]>.py`; a `daemon.services.*` value is never discovered and the exact-equality drift pin fails at boot — `_tool_registry.py:1105-1112`) |
| 3 | `daemon/tools/_tool_registry.py` `DYNAMIC_TOOL_NAMES` (~`:280`, beside the three metrics names) | add `"anomaly_clusters"`, `"anomaly_report_detail"` (factory-built ⇒ dynamic) |
| 4 | `daemon/tools/_tool_registry.py` `KNOWN_TOOL_NAMES` (~`:1627`) | add the same two names — **REGENERATE** via `uv run python -c "from daemon.tools._tool_registry import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"` (the checklist step 4, `_tool_registry.py:150-184`); the drift test `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` must pass |
| 5 | `daemon/tools/instance.py` `create_instance_tools` (`:6624-6663`, immediately after the metrics extend) | the CRITICAL `tools.extend(create_nest_report_read_tools(manager, current_instance_id=..., agent_id=agent_id, agent_tag=version_tag))` — it MUST precede `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` (a later extend is invisible to the help tool or **BYPASSES the privileged strip**) |
| 6 | `agents/{maintenancer,devops,wanderer}/meta.json` `tools.allow` | add `"nest-report-read"` — **`maintenancer`** (`agents/maintenancer/meta.json:12-23`), **`devops`** (`agents/devops/meta.json:12`), **`wanderer`** (`agents/wanderer/meta.json:12`). **ari does NOT get it** ⇒ the ari allow-array TRAILING-ENTRY pin (`tests/unit/test_board_registration.py:280-291`) is **UNAFFECTED**. **THE EDIT ORDER IS RULED: PHASE 3 FIRST, PHASE 5 APPENDS `nest-report` AFTER** (`phase6-plan.md` item 11; phase 6's A11 expects the three files to carry BOTH entries, and phase 5's grant pin asserts the entry is the LAST element — a mis-ordered array is caught mechanically). ⇒ Phase 3 must therefore leave a clean append point at the END of each array |
| 7 | `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:367`) | add `"nest-report-read"` (entry **#18**, after `"metrics-read"`) |
| 8 | the **11 exact-set pin sites** | see the table below — **ALL in the SAME commit** (the SAME-PR rule, `_tool_registry.py:324-366`) |
| 9 | the **7 count pin sites** | `len(PRIVILEGED_TOOL_CATEGORIES) == 17` → `18` — same commit |
| 10 | `test/packs/metrics_read_mutation_matrix_test.py:92-93` | **MT-01-count-pin's find/replace strings** — rewrite for 18 (see the sub-block below). Same commit, or the mutation pack FAILS |
| 11 | `daemon/loader.py` warm-list (`:528` import + `:566` stub + `:594` flatten) | `from .tools.nest_report_read import create_nest_report_read_tools` + `nest_report_read_tools = create_nest_report_read_tools(None)` + the factory list entry — the cold-boot metadata scan needs the decorator to RUN here or the category is missing from the rendered prompt until an instance build repopulates the cache (the W1-FIX-NOW class, `loader.py:520-528`) |

### THE PIN-SITE TABLE — EVERY site the 17 → 18 change touches (the implementer must NOT discover these one RED at a time)

**Derived by RE-GREPping THIS worktree** (`grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ test/`, `grep -rn "PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET" tests/ test/`, `grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ test/`, plus the pack string) — **not** from prose. **33 rows: 18 PIN sites + 1 production definition + 1 comment block + 13 registration rows.**

| # | File : line | Kind | Exact change |
|---|---|---|---|
| 1 | `daemon/tools/_tool_registry.py:367` | **PRODUCTION DEFINITION** | add `"nest-report-read"` to `PRIVILEGED_TOOL_CATEGORIES` (entry **#18**, immediately after `"metrics-read"` at `:650`) |
| 2 | `daemon/tools/_tool_registry.py:324-366` | **COMMENT BLOCK (stale)** | rewrite the SAME-PR RULE prose: the truth is **11 comparison + 6 count + 1 pack string = 18 sites across 11 test files + the pack**; today it says *"TEN sites … SIXTEEN assertion sites across TEN files"* |
| 3 | `daemon/tools/_tool_registry.py:1125` | registration, not pin | `CATEGORY_MODULES`: add `"nest-report-read": "daemon.tools.nest_report_read"` beside `"metrics-read": "daemon.tools.metrics_read"` |
| 4 | `daemon/tools/_tool_registry.py:280-282` | registration, not pin | `DYNAMIC_TOOL_NAMES`: add `"anomaly_clusters"`, `"anomaly_report_detail"` — the **TOOL names keep the data vocabulary** (the leader's ruling renames the CATEGORY, not the tool names) |
| 5 | `daemon/tools/_tool_registry.py:1627-1629` | registration, not pin | `KNOWN_TOOL_NAMES`: add the same two names — **REGENERATE** via `discover_source_only_tool_names()`; the drift test `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` must pass |
| 6 | `daemon/tools/instance.py:292` | registration, not pin | the import → `from .nest_report_read import create_nest_report_read_tools` |
| 7 | `daemon/tools/instance.py:6657` | registration, not pin | the extend → `tools.extend(create_nest_report_read_tools(manager, current_instance_id=..., agent_id=agent_id, agent_tag=version_tag))` — after the metrics extend, BEFORE `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` |
| 8 | `daemon/loader.py:528` | registration, not pin | `from .tools.nest_report_read import create_nest_report_read_tools` (the cold-boot metadata scan) |
| 9 | `daemon/loader.py:566` | registration, not pin | `nest_report_read_tools = create_nest_report_read_tools(None)` |
| 10 | `daemon/loader.py:594` | registration, not pin | the flatten-list entry `nest_report_read_tools,` |
| 11 | `agents/maintenancer/meta.json:22` (array `:12-23`) | registration, not pin | append `"nest-report-read"` after `"metrics-read"` in `tools.allow` |
| 12 | `agents/devops/meta.json:12` | registration, not pin | append `"nest-report-read"` after `"metrics-read"` (single-line array) |
| 13 | `agents/wanderer/meta.json:12` | registration, not pin | append `"nest-report-read"` after `"metrics-read"` (single-line array) |
| 14 | `tests/unit/test_board_registration.py:70` (const) + `:207` (assert) | PIN — exact-set (`EXPECTED_PRIVILEGED_SET`) | add `"nest-report-read"` to the constant; the assert at `:207` then compares the registry against it |
| 15 | `tests/unit/test_board_registration.py:222` | PIN — count | `assert len(PRIVILEGED_TOOL_CATEGORIES) == 17` → `== 18` |
| 16 | `tests/unit/test_wedge_capture_registration.py:72` (const) + `:177` (assert) | PIN — exact-set | add `"nest-report-read"` to the constant |
| 17 | `tests/unit/test_wedge_capture_registration.py:180` | PIN — count | `== 17` → `== 18` |
| 18 | `tests/unit/test_self_alarm_registration.py:76` (const) + `:215` (assert) | PIN — exact-set | add `"nest-report-read"` to the constant |
| 19 | `tests/unit/test_self_alarm_registration.py:218` | PIN — count | `== 17` → `== 18` |
| 20 | `tests/unit/test_notify_user_registration.py:55` (const) + `:179` (assert) | PIN — exact-set | add `"nest-report-read"` to the constant |
| 21 | `tests/unit/test_notify_user_registration.py:182` | PIN — count | `== 17` → `== 18` |
| 22 | `tests/unit/test_metrics_read_registration.py:91` (const) + `:218` (assert) | PIN — exact-set | add `"nest-report-read"` to the constant |
| 23 | `tests/unit/test_metrics_read_registration.py:221` | PIN — count | `== 17` → `== 18` |
| 24 | `tests/unit/test_instance_ops_registration.py:88` (const) + `:206` (assert) | PIN — exact-set | add `"nest-report-read"` to the constant |
| 25 | `tests/unit/test_instance_ops_registration.py:209` | PIN — count | `== 17` → `== 18` |
| 26 | `tests/unit/tools/test_secrets_registration.py:226` | PIN — exact-set (inline `frozenset({…})`) | add `"nest-report-read"` to the literal |
| 27 | `tests/unit/tools/test_upgrade_registration.py:152` | PIN — exact-set (inline) | add `"nest-report-read"` to the literal |
| 28 | `tests/unit/tools/test_attestation_registration.py:192` | PIN — exact-set (inline) | add `"nest-report-read"` to the literal |
| 29 | `tests/integration/test_service_tool_flag_off_byte_identical.py:834` | PIN — exact-set (inline, `test_privileged_category_set_is_pinned`) | add `"nest-report-read"` to the literal |
| 30 | `tests/integration/test_maintenancer_spawn_resolves_tools.py:382` | PIN — exact-set (inline) | add `"nest-report-read"` to the literal |
| 31 | `test/packs/metrics_read_mutation_matrix_test.py:92-93` | PIN — **the pack string** | rewrite the MT-01 find/replace pair to `18` / `17` (or derive the strings from the live constant) — see the block below |
| 32 | `daemon/checkpoint_metrics.py:218` | VOCABULARY MARKER | `VOCABULARY_VERSION` 4 → 5 (the SAME change as the artifact regen) |
| 33 | `.agents/shared/metrics/tool-categories.json` | ARTIFACT | regenerate with the house generator (never hand-edited) |

**THE 18 PIN SITES = 11 exact-set comparison sites + 6 count assertions + 1 mutation-pack string.** (Rows 14-30 are the 11 comparison sites — 6 via the `EXPECTED_PRIVILEGED_SET` constants and 5 via inline `frozenset({…})` literals; rows 15/17/19/21/23/25 are the 6 count assertions; row 31 is the pack string.)

**THE 18TH SITE — THE MUTATION-PACK STRING (a hardcoded literal in a pack, NOT an assertion).**
`test/packs/metrics_read_mutation_matrix_test.py:92-93` (MT-01-count-pin) hardcodes the count as a **string literal**:
```python
"assert len(PRIVILEGED_TOOL_CATEGORIES) == 17",   # find
"assert len(PRIVILEGED_TOOL_CATEGORIES) == 16",   # replace
```
When this phase moves the count to 18, that find-string no longer matches the target file (`tests/unit/test_board_registration.py`) ⇒ **the mutation cannot be planted any more**.
**THE ACTUAL FAILURE MODE (verified in the pack's own harness, `:598-607`):** the leg does NOT go silently green — the harness does `if text.count(find) != 1: print(f"{label}: SKIPPED …"); failures += 1; continue` ⇒ the pack prints **`RESULT: FAIL`** and exits 1. **So the trap is a LOUD FAILURE WITH A MISLEADING DIAGNOSIS** ("the find-string is not unique: 0" reads like a duplication problem, not like a stale literal), not a false green. Either way the pack MUST be updated in the SAME change or the lane's gate is red.
⇒ **THE FIX (this phase's obligation):** rewrite the MT-01 pair to `find = "assert len(PRIVILEGED_TOOL_CATEGORIES) == 18"` and `replace = "assert len(PRIVILEGED_TOOL_CATEGORIES) == 17"` — the mutation still drives the count pin RED (the set has 18 members, the planted pin claims 17) and still carries the same red marker (`test_board_is_the_ninth_entry`), so the leg keeps its meaning. **A stronger alternative (recommended): derive the literal from the live constant** so the next count bump cannot stale it again — e.g. build the find/replace strings from `len(PRIVILEGED_TOOL_CATEGORIES)` at pack-import time instead of hardcoding `17`. If the pack is refactored that way, the find-string assertion becomes structural and the class cannot recur.

**THE CENSUS — DERIVE IT WITH BOTH GREPS (the `== frozenset`-only grep UNDER-counts).**
The house lesson (`.agents/tester/LESSONS/2026-10-20-v0261-tip-verification-gotchas.md:11`): *"Grepping only for `== frozenset` finds 5 and can produce a false 'pins missing'. Derive the set with BOTH greps … and cross-check against the pin-ceremony commit."* The re-derived census for this change:

| kind | count | where |
|---|---|---|
| exact-set COMPARISON sites | **11** | 5 inline `== frozenset({…})` literals + 6 `== EXPECTED_PRIVILEGED_SET` constants |
| count ASSERTION sites | **6** | the six `assert len(PRIVILEGED_TOOL_CATEGORIES) == 17` lines above |
| the pack STRING site | **1** | `test/packs/metrics_read_mutation_matrix_test.py:92` |
| the production definition | **1** | `daemon/tools/_tool_registry.py:367` |
| the stale comment block | **1** | `daemon/tools/_tool_registry.py:324-366` (the SAME-PR RULE prose) |
| **TOTAL touch points** | **20** | ⇒ **18 assertion/pack sites** (11 + 6 + 1) + the def + the comment block — the count phase 6's acceptance quotes |

**The comment block (`:324-366`) is ALREADY STALE and must be corrected in the SAME change:** it says *"pinned by exact-equality asserts at TEN sites — five inline ``frozenset({...})`` literals + five ``EXPECTED_PRIVILEGED_SET`` constants"* and *"a ``len(PRIVILEGED_TOOL_CATEGORIES) == N`` count assertion at FIVE phase-local pins"* and *"SIXTEEN assertion sites across TEN files"*. The truth today is **11 comparison sites + 6 count sites + 1 pack string = 18 across 11 files**; after this change it is **18 across 11 files** (the same 11 files — the pack string lives in `test/packs/`, a 12th file that also carries a site). A stale count in the registry docstring is exactly the drift hazard the docstring itself warns about.

**Prose hygiene (same commit, not optional — the count is stated in prose in these files):** `tests/unit/test_metrics_read_registration.py:15` ("the frozenset LENGTH is 17"), `:90` ("seventeen members"); `tests/unit/test_board_registration.py:201/:209`; `tests/unit/test_wedge_capture_registration.py:176`; `tests/unit/test_self_alarm_registration.py:214`; `tests/unit/test_notify_user_registration.py:11/:178`; `tests/unit/test_instance_ops_registration.py:205`; `tests/integration/test_maintenancer_spawn_resolves_tools.py:315` (the docstring) **and `:343` (the method name `test_privileged_categories_contains_seventeen_entries` — a PROSE occurrence, not a count assertion; renaming it is part of the honesty pass)**; **`tests/integration/test_service_tool_flag_off_byte_identical.py:819-826`** (the `test_privileged_category_set_is_pinned` docstring still says the exact-set pin lives at "TEN sites" — the same stale-count class this row targets). **`daemon/tools/_tool_registry.py:324-366` is ALREADY STALE** (it says "TEN sites — five inline + five EXPECTED_PRIVILEGED_SET" and "SIXTEEN assertion sites across TEN files"; the truth today is 11 comparison + 6 count + 1 pack string = 18 sites across 11 test files plus the pack). Correct it in the same commit — a stale count in the registry docstring is exactly the drift hazard the docstring itself warns about.

**The artifact + the vocabulary bump (same change):**
- `.agents/shared/metrics/tool-categories.json` — **REGENERATE**, never hand-edited, with the house generator (the artifact's own docstring, `tests/unit/tools/test_tool_category_artifact.py:19-27`):
  ```bash
  ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json \
      uv run python -m pytest tests/unit/tools/test_tool_category_artifact.py -q
  cp /tmp/tool-categories.json .agents/shared/metrics/tool-categories.json
  ```
  **The counts (verified on the base tree):** the committed artifact carries **57** categories at `vocabulary_version: 3`. The sibling's phase 1 adds `nest-report` → **58** categories at version **4**; this phase adds `nest-report-read` → **59** categories at version **5**. The artifact's `reserved: "other"` is the fold target for any category outside the derived set (the drift check fails the moment a `register_tool_category` site is added without a regeneration — the v0.28.9 **F-1 trap** class).
- `daemon/checkpoint_metrics.py:218` `VOCABULARY_VERSION` — **4 → 5** (the sibling's phase 1 moves it 3 → 4). The bump contract: the frozen label set changed ⇒ the marker moves (`checkpoint_metrics.py:200-217`).

**AM-3 — THE VOCABULARY-BUMP SITES THE PLAN MISSED (all binding; the architect's finding).** The bump is not one constant — it is a constant PLUS two pinned literals PLUS two history docstrings, and an exposition baseline that renders the LIVE value:

| # | Site | Exact change |
|---|---|---|
| 1 | `daemon/checkpoint_metrics.py:218` | `VOCABULARY_VERSION: int = 4` → `5` (the constant) |
| 2 | **`daemon/checkpoint_metrics.py:200-217`** | the **VOCABULARY_VERSION docstring history block** — append the lane's entry in the house shape (the current entries narrate the 3→2→3 moves with their reasons). The plan named only the CONSTANT at `:218`; the docstring is a separate site |
| 3 | **`tests/unit/metrics/test_metrics_family_adjudication.py:419`** | the literal `assert cm.VOCABULARY_VERSION == 3` → `== 5`, **and** its `:401-418` history docstring (the block that narrates why the marker moved) gains the lane's entry |
| 4 | **`tests/unit/metrics/test_metrics_vocabulary_amendment.py:26`** | the same literal → `== 5`, **and** its `:17-25` prose gains the lane's entry |
| 5 | **`tests/helpers/metrics_exposition_baseline.py`** | **RE-RUN after EACH bump** (it renders the LIVE `VOCABULARY_VERSION` into the exposition baseline) — a re-render, never a hand edit |

**NO FE compat concern (AM-3):** the FE types `vocabulary_version: number` and a spec **PINS** that it must never be a literal (`frontend/src/app/models/metrics.model.ts:20-21`/`:140`, `frontend/src/app/models/metrics.model.spec.ts:177-179`) ⇒ the FE renders it dynamically and needs **no** change for the 3→4→5 lane. **`schema_version` does NOT move** — the envelope axis and the vocabulary axis are independent (`daemon/services/metrics_query/constants.py`; pinned by `test_metrics_vocabulary_amendment.py:91-99`).

**THE ARTIFACT SETTLEMENT (AM-3 — the plan was NOT gapped):** `.agents/shared/metrics/tool-categories.json` carries `{categories, count, vocabulary_version, reserved, source}` derived from `registry.CATEGORY_MODULES` + the AST-scanned `@register_tool_category` call sites — **ZERO per-agent grant information** (`tests/unit/tools/test_tool_category_artifact.py:190-209`). ⇒ **phase 5's 40-meta.json grant ceremony requires NO further artifact regen** (and the prompt-artifact baseline covers only the versioned `.md` files — `meta.json` is not in that set, so phase 3's three `meta.json` edits need **no** re-stamp). The two regens remain **mandatory**, each in the SAME commit as its bump: **phase 1 (57 → 58 categories @ v4)** and **phase 3 (58 → 59 categories @ v5)**.

---

### Task 6 — The domain REST route `/api/nest-reports/*`

**File inventory:**

| File | Action | Detail |
|---|---|---|
| `daemon/routers/nest_reports.py` | NEW | the router: `APIRouter(prefix="/nest-reports", tags=["nest-reports"])` |
| `daemon/services/anomaly_service.py` | EDIT | the router reads through the **service** (the sibling's file — phase 3's read half), NOT through the repository and NOT through raw SQL |
| `daemon/routers/__init__.py` | EDIT | `from .nest_reports import router as nest_reports_router` + the `__all__` entry |
| `daemon/api.py` | EDIT | `api_router.include_router(nest_reports_router)  # /api/nest-reports` — **one line**, inside `api_router` and **BEFORE the SPA catch-all** (the `metrics_router` include at `:5387` is the placement precedent) |
| `tests/unit/test_router_scope_manifest.py` | EDIT | the scope-manifest family entry + the mount census + the declared-route census |
| `tests/integration/test_router_class_manifest.py` | EDIT | the class-manifest family entry + the family count |

**The two routes:**

| route | method | query | response |
|---|---|---|---|
| `/api/nest-reports/clusters` | GET | `window_s?`, `severity?`, `category?`, `limit?` (clamped ≤ 50), `cursor?` (opaque, resumes) | **`{groups: [...], bound: {6-member}, category_breakdown: {...}, freshness: {...}, generated_at_utc}`** — the list is **GROUPED** (AM-6c, see below): one entry per `(tree_root_instance_id, category)` with `occurrence_count` (the number of CLUSTERS in the group), `report_count` (the summed reports), `severity`/`severity_label`/`severity_rank` (the group's **PEAK across its clusters** — labelled as a peak, AM-7), `last_report_at` (the newest member), `open` (ANY member open), `board_item_ids?`, and `cluster_ids` (the member cluster ids, bounded by the same page limit) — **NO `excerpt` key anywhere in the list payload** (AM-12a) |
| `/api/nest-reports/clusters/{cluster_id}` | GET | `limit?` (≤ 50), `cursor?`, **`include_excerpt?` (default FALSE — AM-12b)** | `{cluster_id, category, severity, severity_rank, severity_label (the PEAK phrasing, AM-7), report_count, tree_root_instance_id, first_report_at, last_report_at, open, board_item_id, reports: [...], bound: {6-member}}` — **`excerpt` appears ONLY with `?include_excerpt=true`** |

**THE GROUPING RULING (AM-6c) — THE ROUTE GROUPS, SERVER-SIDE (one decision, no alternatives).**
The list route returns **GROUPS**, not raw clusters: one entry per `(tree_root_instance_id, canonical category)`. **Why server-side, decisively:** (a) **pagination** — the cursor pages GROUPS, so a FE-side grouping would make the page size count clusters while the rows are groups (the operator would see fewer rows than the limit, and the cursor's semantics would lie); (b) **one projection** — the route and the read tool call the SAME projector in the service (the AM-1 advisory pin), so the grouping cannot diverge between the two transports; (c) the FE stays a renderer.
- **The occurrence count is the number of CLUSTERS in the group** — NOT the number of reports (a cluster already aggregates many reports; the two counts differ and the badge must mean *"this tree reported this category N times"*).
- **The group's severity is the PEAK across its member clusters** (a peak of peaks) — and it is labelled as a peak (AM-7's "critical (peak)" phrasing).
- **A group row expands WITHOUT a second call for the group**: the row carries the member `cluster_ids` (bounded by the same page limit) with each member's count + last-report + open badge; **clicking a member cluster id** calls the existing detail route (`/clusters/{cluster_id}`) for that cluster's members. **No new route, no FE-side re-keying, no re-windowing** — R5's identity model is untouched (the member clusters stay distinct; the grouping is a READ-SIDE view over them).
- **Widening W is REJECTED** (the architect's explicit ruling): `W=4h` would chain hourly reports into one cluster whose `last_report_at` rolls forever ⇒ never resolving under the derived 1-hour rule. The grouping is the fix; the window is not.

**THE RESPONSE SHAPE IS THE PHASE-4 CONTRACT — two fields are MANDATORY:**

1. **`severity_rank` (an integer 1–3, alongside the raw `severity` string).** The phase-4 FE renders the severity CHIP TIER from the rank, so the FE carries **no severity vocabulary** (the FE renders the raw category string verbatim and never hard-codes a severity set). The rank mapping is the service's **`SEVERITY_RANK`** map (phase 2's, `phase2-plan.md:37`: `minor → 1`, `major → 2`, `critical → 3`) — one source. Without it, the FE would have to carry a second copy of the severity set, which the lane's single-sourcing discipline forbids.
2. **`open` is the SERVER's derived boolean** (`last_report_at >= now − 1h`, **R5**) — the FE renders it and NEVER recomputes it (a client clock disagrees with the server's window).

Also mandatory: the freshness block carries the **server's** query instant and the effective window (the FE never uses the client clock for a server-anchored value — the house rule, `frontend/src/app/pages/metrics/tabs/central-tab.component.ts:864-870`).

### THE ROUTE AUTH POSTURE (one box)

> - **SCOPE** — the door's **any-of QUERY set** `{maintenance:read | metrics:read}`, declared as `dependencies=[scope_required("maintenance:read", "metrics:read")]` (`daemon/routers/metrics.py:583`/`:632`/`:705`) — the **planner ruling on worker OQ1, 2026-11-04**.
> - **AUTH UNCONFIGURED (the LIVE posture)** — an unauthenticated live daemon (auth not configured) answers **503 `AUTH_NOT_CONFIGURED`**, **fail-closed BEFORE ANY READ** (mutation **M-3e** + the repository-stub-raises pin: a stub that RAISES if touched proves the ordering).
> - **AUTHENTICATED BUT SCOPE-MISSED** — **403 `SCOPE_INSUFFICIENT`** (the shipped body token, `daemon/auth/decorators.py`).
> - **THE FE DEGRADES PER SOURCE** — see phase 4's degradation matrix: the incident list's own failure **never blanks the tab**, and the tab **never blanks**. The route carries the **same posture** the central door carries, so a posture failure is explained **ONCE** (phase 4's single-banner discipline).

**The gate — IDENTICAL to the metrics door, copied VERBATIM:**
- `_require_auth_on(request)` — the inline `_err(503, _ERR_AUTH_NOT_CONFIGURED, "Auth not configured (fail-closed)")` helper (`daemon/routers/metrics.py:124-153`), called **as the FIRST statement of the handler** — before any repository read. The route is NOT switch-gated (the door's posture: "the door is NEVER switch-gated (AM-17/AM-18)" — `daemon/routers/metrics.py:211-220`).
- `dependencies=[scope_required("maintenance:read", "metrics:read")]` (`daemon/auth/decorators.py:160-184`) — the door's EXACT any-of invocation shape: the three query routes declare `dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)]` (`daemon/routers/metrics.py:583`, `:632`, `:705`) with `_SCOPE_READ = "maintenance:read"` (`:106`) and `_SCOPE_READ_METRICS = "metrics:read"` (`:115`).
- **Scope decision (planner ruling on worker OQ1, 2026-11-04): the route accepts the ANY-OF SET `{maintenance:read | metrics:read}` — exactly the door's QUERY-route form.**
  1. **R8's operative word is IDENTICALLY.** The door's three query routes accept the any-of set in the `dependencies=[scope_required(...)]` form (`daemon/routers/metrics.py:583`/`:632`/`:705`); identical gating means the **same set**, not a stricter one. The earlier citation of `:106-137` was the **TEXT-EXPOSITION** route (`maintenance:read` ALONE — deliberately, that route is the D6 step-3 exception, `daemon/routers/metrics.py:101-106`), which is **not** the model for this route.
  2. **One surface, two doors.** The FE tab calls the metrics door AND the anomaly route from ONE surface. A caller holding `maintenance:read` but not `metrics:read` — every operator token minted **BEFORE** the 2026-11-03 v0.28.9 split, i.e. exactly the population that runs the morning review — would see charts render and the incident list 403: a per-source degradation inconsistency that the shipped per-source state machinery exists to PREVENT.
  3. **Accepting the broader `maintenance:read` grants ZERO new capability.** It is a superset token held only by operators who already hold more. The `daemon/auth/scopes.py:33-39` narrowing rationale governs **which tokens to MINT for machine lanes** ("carries this narrower scope instead"), **NOT which tokens a route may ACCEPT** — the accept-side contract is the door's own any-of set.
  4. The subset-check scope cage (`tests/unit/test_router_scope_manifest.py:224-232`) stays green with the any-of row (the door's own family carries the same OR-set row at `:177`).
- The route carries **NO class tag** ⇒ it classifies `protected-default` (`"pd"`) — the metrics-router precedent (`tests/integration/test_router_class_manifest.py:137-147`).

**The body build is OFF-LOOP** (`daemon/services/db_offload.py:386`), byte-bounded by the same `RESPONSE_BYTE_CEILING`, and the response shapes **mirror the read tools** (Task 4) — one projection, two transports, zero divergence.

**The route's error vocabulary (closed, and DELIBERATELY not the door's):**
`400 bad_request` · `401` (auth middleware) · `403 SCOPE_INSUFFICIENT` · `404 anomaly_cluster_not_found` · `503 AUTH_NOT_CONFIGURED` · `503 anomaly_unavailable` (the anomaly substrate is down).
The **FE maps the first four + `AUTH_NOT_CONFIGURED` onto the door's existing classes** (the closed 10-class union, `frontend/src/app/models/metrics.model.ts:388-399`) and renders the **two domain-only outcomes** with its own copy — the door's `unknown_family` / `registry_unavailable` copy would be a **false statement** about a cluster id or about the anomaly substrate. The phase-4 mapping is fixed in `phase4-plan.md` Task 3; the route's tokens are fixed here. **The frozen door vocabulary is NOT widened.**

**The three cage updates (all in the SAME commit — an undeclared mount fails both cages loudly):**

| Cage | File : line | Change |
|---|---|---|
| scope manifest | `tests/unit/test_router_scope_manifest.py:177` (beside `"/api/metrics": "maintenance:read\|metrics:read"`) | add `"/api/nest-reports": "maintenance:read\|metrics:read"` (the door's any-of set — the SAME spelling as the `:177` row) |
| scope manifest — MOUNT census | `tests/unit/test_router_scope_manifest.py:~318` | `api_router.include_router(` count **37 → 38** |
| scope manifest — DECLARED-ROUTE census | `tests/unit/test_router_scope_manifest.py:417` | **111 → 113** (two decorated routes; the census counts `scope_required` dependency-form routes too — `:10-13`) |
| class manifest — family | `tests/integration/test_router_class_manifest.py:147` (beside `"/api/metrics": frozenset({"pd"})`) | add `"/api/nest-reports": frozenset({"pd"})` |
| class manifest — family count | `tests/integration/test_router_class_manifest.py:383` | `assert len(api_families) == 33` → **34** |

`PINNED_MANIFEST_AUTH_OFF` is **derived** from `PINNED_MANIFEST` by a comprehension (`tests/integration/test_router_class_manifest.py:174-184`) ⇒ the new family is picked up automatically as `"pd"` (the route is registered unconditionally and refuses in auth-OFF with 503, exactly like the metrics family). **No auth-off edit needed.**

---

### Task 7 — The gate packs + net-new-red discipline

**New packs (Tier-1, all green required):**

**The pack FILE NAMES are the sibling's phase-6 contract** (`phase6-plan.md:110-111` names them) — phase 3 ships them under those exact names so phase 6's gates find them.

| Pack | File | What it proves |
|---|---|---|
| family pack | `tests/unit/metrics/test_metrics_family_w4.py` **(the council's W-4 ruling: the `w4.py`-matching name WINS — this file matches the module it tests; **one path everywhere**: phase 3 and phase 6's `:109` + A7 all name `tests/unit/metrics/test_metrics_family_w4.py` — the sibling's conformance is DONE)** | F23 is registered with its **FIVE** metrics; every DECLARED label key has `allowed_values` (AM-2); the adapter returns early when `dsn is None`; a failing query is a rate-limited WARNING and the tick survives; the closed label space ≤ the declared band (**67**); **F23 is ROSTERED** — and, per **AM-2(c)**, the roster pack asserts **ALL FIVE** charted series' roles (`roster_role_for("F23", "ensemble_anomaly_reports_total") == CHART_ROLE_STEP`, `"…rate_limited_total" == CHART_ROLE_STEP`, `"…clusters_open" == CHART_ROLE_LINE`, `"…board_items_suppressed_total" == CHART_ROLE_STEP`, `"…capacity_exhausted_total" == CHART_ROLE_STEP`) — **not one** — plus the general pin **"every registered metric of a roster family has a roster entry"** (the coverage is per (family, NAME); a typo in one series name silently degrades that metric to stat-primary INSIDE a chart family — invisible on the wire, and `assert_roster_is_well_formed` (`roster.py:228-253`) never checks it); **AM-2d**: the adapter contains **NO pre-fold** (the raw token reaches `.set()`) and an out-of-vocabulary stored value **increments `ensemble_metrics_invalid_label_total`** while the published series still shows `other`; **AM-2(e) NR-11 DISCLOSURE**: F23's two stored-row counters carry `monotonic: true` although the value can DECREASE (retention prunes rows) — the house-wide `monotonic` derivation is F3's exact precedent (`ensemble_wedge_capture_total`, `w1.py:158-173`) and the FE does not consume the flag; the **general fix is routed to the central-metrics lane**, not this one |
| **F23 PUBLISHES** (phase 6's A7) | the same pack — `tests/unit/metrics/test_metrics_family_w4.py` (the W-4 ruling's path — **one path everywhere**: phase 3 and phase 6's `:109` + A7 all name it; the sibling's conformance is DONE) | *"F23 present in the registry **and publishing** (a series is actually returned, not just registered)"* (`phase6-plan.md:69`) ⇒ the pack asserts a **real published series** (a seeded row ⇒ a live series on the provider), not merely a registry entry. **A silent-dark F23 must not ship looking green** (`decisions.md` F23's disclosure). |
| read-tool pack | **`tests/unit/test_nest_report_read_tools.py`** | the tools resolve **through the anomaly service** (never raw engine handles — the read-path purity pin); the output-budget clamp; the **freshest-first** sort; the **degraded-read block**; the two-layer gate (the trio passes; `ari` and `worker` are refused); the refusal ladder tokens; the six-member bound block; the tools stay present and refuse loudly on a degraded substrate (the RULING-1 shape); **AM-12(a)**: the LIST payload has **NO `excerpt` key** (mutation: add it → red); **AM-12(b)**: `excerpt` appears ONLY with `include_excerpt=true` (mutation: emit it without the flag → red); **the AM-1 ADVISORY pin**: the route and the read tools call the **SAME projection function** in the service (single-sourcing — stronger than a shape-equality assertion: the route imports the same projector the tool body calls, so the two CANNOT drift) |
| registration pack | `tests/unit/tools/test_nest_report_read_registration.py` | the category is privileged; the AST discovery finds both tools; `CATEGORY_MODULES` / `DYNAMIC_TOOL_NAMES` / `KNOWN_TOOL_NAMES` carry them; the `instance.py` extend precedes the filter and the doc scan; the decorator order is register-then-tool; the default-deny leg resolves ZERO tools for an empty-allow agent; the body gate and the category gate are the same set |
| route pack | **`tests/unit/test_nest_report_rest_route.py`** | the route is gated identically to the metrics door (`daemon/routers/metrics.py:583`/`:632`/`:705` — the any-of query routes, the model; the text-exposition route at `:155-160` carries `maintenance:read` ALONE by design and is NOT the model); 503 `AUTH_NOT_CONFIGURED` fail-closed with no auth; 200 with auth; **the route accepts EXACTLY the door's any-of query scope {maintenance:read \| metrics:read} — nothing broader**; the 503 fires **BEFORE** any repository read (a repository stub that RAISES if touched proves the ordering); 403 `SCOPE_INSUFFICIENT` on a scope miss; the bounded limit; the cursor is never silent; the response shape equals the read tools' shape; **AM-12(a)/(b)**: the list payload has **NO `excerpt` key**, and `excerpt` appears ONLY with `?include_excerpt=true`; **AM-1 (the ROUTE-SET PIN — mandatory):** assert the **METRICS** router's path set is **EXACTLY** `{`/families`, `/snapshot`, `/series`}` — a **4th** metrics route ⇒ **RED**. Rationale: `tests/unit/metrics/test_metrics_query_scope_cage.py:158-165` iterates the router's routes but only *checks* the three named paths — a 4th is **SKIPPED, not rejected** — and the global declared-route census (which **phase 3 itself bumps 111 → 113**, degrading that diagnostic) is the ONLY tripwire. This pin is what keeps the door **FROZEN** while phase 3 adds a NEW router next to it |

**THE MUTATION LEGS phase 6 expects from phase 3** (`phase6-plan.md:110-111`) — the phase-3 packs MUST be structured so that these mutations redden:
| Leg | Mutation | Which phase-3 pack must go red |
|---|---|---|
| **M-3c** | read the engine handle directly (bypass the service) | the read-tool pack's read-path purity pin |
| **M-3d** | remove the output clamp | the read-tool pack's budget pin |
| **M-3e** | make the route open (drop the fail-closed gate) | the route pack's fail-closed pin |
| **M-3f** | **broaden the scope beyond the any-of set** (accept an unrelated scope, or drop to a single broader/unrelated token set) | the route pack's scope pin (the route accepts EXACTLY `{maintenance:read \| metrics:read}` — nothing broader) |
| **M-3g** (AM-1) | **add a 4th route to the METRICS router** | the route pack's **route-set pin** — the metrics router's path set must be EXACTLY `{`/families`, `/snapshot`, `/series`}` |
| **M-3h** (AM-12b) | emit the `excerpt` WITHOUT `include_excerpt=true` | the route pack + the read-tool pack's excerpt-opt-in pin |
| **M-3i** (AM-12a) | add an `excerpt` key to the LIST payload | the route pack + the read-tool pack's list-exclusion pin |
| **M-3j** (AM-2d) | re-introduce the adapter's pre-fold to `other` (the raw token no longer reaches `.set()`) | the family pack's no-pre-fold + invalid-label-counter pins |
| **M-3k** (AM-2c) | typo one F23 series' name in the roster (it silently degrades to stat-primary) | the family pack's roster-coverage pin |

**Net-new-red discipline (mandatory):** the FAILED-list diff is taken against the **TRUE pre-fix base** (the merge-base of the branch, NOT the branch tip), per lane. Every diff id must be either fixed or adjudicated in writing. The pre-existing reds on this tree (the `settings/timezone` family, the `app.component.spec.ts` TS1128, the base-proven seal tripwire) are **not** this phase's and must not be counted as net-new.

**TWO disclosures to carry into the ceremony text (from the planner's rulings — DISCLOSE, do NOT fix):**
1. **R_i** — the census-gated polarity defect (`daemon/services/metrics_census.py:322-335` / `:347-355`) is STILL LIVE: it darkens the `postgres_census`-idiom families only; **F23's `registry` idiom is UNAFFECTED** (only `series_absent` applies). The defect therefore does not dim F23, but it IS a live defect in the same subsystem and belongs in the disclosure.
2. **R_ii** — `capture_active` is never wired (`daemon/api.py:2527-2532`), so the `wedge_capture`-idiom families stay dark. F23 is `registry`-idiom and is unaffected.

---

## Coupling

- **Tight with phase 1** — the vocabulary (Task 1(a): phase 1's `wiring/vocab.py` tuples), the `anomaly_reports` / `anomaly_clusters` column names (Task 1(c): phase 1's verbatim model spec), and the service seam (Task 1(b): `daemon/services/anomaly_service.py` + `daemon/repositories/anomaly/`). **The binding is the CONTRACT, not a file name** — `decisions.md` R1/R5/R8 is the SSOT; the phase-1/3 interface test is the gate.
- **Tight with phase 2** — the clustering writes the rows phase 3 reads (`anomaly_clusters`' rollup columns + `board_item_id`); the `SEVERITY_RANK` map is phase 2's; the rate-limited ledger rows are phase 2's (`outcome='rate_limited'`), and phase 3's rate-limited counter reads them.
- **Tight with phase 4 (FE)** — the response shapes (Task 4 + Task 6) ARE the FE's contract. The FE consumes `/api/nest-reports/clusters` (the **grouped** list — AM-6c) and `/api/nest-reports/clusters/{id}` (one cluster's members); it never touches the tools.
- **Loose with the metrics lane** — F23 is a normal family on the normal registry; the census amendment + the roster amendment are shared-file edits (the adjudication module, the roster, and their packs), which is why the merge order matters.
- **Independent of** the FE bundle budget, the promote machinery, and the plugin subsystem.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | The sibling's phase 1 names its tables/columns/service differently than this plan assumes | High | Medium | The plan binds to the **contract** (Task 1), not to a file name; a phase-1/3 interface test is a gate; the planner's decisions.md records the seam |
| 2 | The adjudication census amendment is missed ⇒ the pack is RED at commit time | High | Medium | Task 3 lists the exact assert lines; the pack run is a gate before commit |
| 3 | A privileged-set pin site is missed ⇒ the SAME-PR rule is violated (the set is red between two commits) | High | Medium | Task 5 lists **all 11 comparison + 6 count + 1 pack-string sites (18)**, derived with BOTH greps (`PRIVILEGED_TOOL_CATEGORIES == frozenset` AND `PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET` AND `len(PRIVILEGED_TOOL_CATEGORIES)`, over `tests/` AND `test/`) per the house lesson (`.agents/tester/LESSONS/2026-10-20-v0261-tip-verification-gotchas.md:11` — the `== frozenset`-only grep UNDER-counts); the implementer re-greps and diffs the site list |
| 4 | The new router is mounted but not declared ⇒ BOTH router cages fail loudly | Medium | Low | Task 6 lists all five cage edits; the cage packs are Tier-1 gates |
| 5 | The `VOCABULARY_VERSION` bump collides with the sibling's 3 → 4 bump | High | Medium | The sequencing rule is in the binding table; the two bumps land in two commits, and the artifact regeneration is done by the generator in each |
| 6 | The counter semantics (a stored-row count that can decrease) mislead an operator | Medium | Medium | The `help` text states it; the F3 house precedent is cited; the FE renders it as a step chart of a stored count |
| 7 | The adapter's SQL drifts from the schema (silent no-series) | Medium | Medium | The phase-1/3 interface test; a failing query is a rate-limited WARNING (visible, not silent) |
| 8 | Two surfaces for the same data drift (tools vs route) | Medium | Low | ONE projection function (in the service), two transports; the route pack asserts the shape equality against the tool pack's fixture |
| 9 | The `nest-report-read` tools are exposed over MCP with the wrong privilege stamp | Low | Low | The MCP stamp only lands for `DYNAMIC_TOOL_PREFIXES` members (`daemon/mcp/tool_adapter.py:183-197`) — these are local `@tool`s, so no stamp is applied; the category is privileged regardless |
| 10 | **F23 is registered but NOT rostered** ⇒ the family is stat-primary ⇒ the phase-4 tab renders **no trend panel** (only the strip) | High | Medium | Task 2 names the roster rows and the mechanism end to end (registry → `is_chart_primary` → family role → `chartSpecs()` gate); the family pack asserts `roster_family_names()` contains `"F23"` and `roster_role_for("F23", "ensemble_anomaly_reports_total") == CHART_ROLE_STEP`; the roster's own 12 → 13 invariant and its pack are amended in the same commit |
| 11 | **Phase 3 and phase 1 edit the SAME three registration files** (`daemon/tools/_tool_registry.py`, `daemon/tools/instance.py`, `daemon/loader.py`) plus `daemon/api.py` — a merge conflict, or worse, a silently DROPPED extend | High | Medium | The lane's MERGE-FIRST ruling applies: phase 3 merges `origin/latest` (which carries phase 1) INTO the branch **before** its own edits, and the registration pack asserts BOTH extends exist (phase 1's `report_anomaly` AND phase 3's two nest-report-read tools) — a dropped extend is a compile- or behaviour-level failure, not a silent one |
| 12 | The `severity_rank` the FE needs has no home | Medium | Low | Phase 2's `SEVERITY_RANK` map (`phase2-plan.md:37`) IS the home; phase 3's projection reads it from the service; the phase-4 FE never hard-codes a severity set |
| 13 | The "open" predicate compares a TEXT timestamp | Medium | Medium | The explicit `::timestamptz` cast is retained as **belt-and-braces** (AM-4's corrected premise: the lexicographic ISO compare IS order-safe for the current producer; the real hazard was the PG-rendered right-hand side, which the cast removes). The predicate lives in ONE place (the service's projection), so the tools and the route cannot disagree; **phase 1's fixed-width timestamp fence** (`isoformat(timespec='microseconds')` + the `timestamp_shape_is_fixed_width` pin) is the durable protection against producer drift |
| 14 | **The F23 band has ZERO headroom** (67 = the measured value exactly) ⇒ any later vocabulary extension REDS the band assertion — by design, but a surprise to whoever extends the vocabulary | Low | Medium | The zero-headroom fact is **written into the code comment** on the band entry (AM-2a) and the band is **re-declared in the same change** as any vocabulary extension (R1's later lane); the band-coverage pin `set(FAMILY_SERIES_BANDS) ⊇ set(REGISTERED_FAMILIES)` (AM-2b) ensures a family can never lose its band silently |
| 15 | **A typo in one F23 roster series name** silently degrades that metric to stat-primary INSIDE a chart family — invisible on the wire (no panel, no error) | Medium | Medium | The family pack asserts **all five** roles AND the general roster-coverage pin ("every registered metric of a roster family has a roster entry") — `assert_roster_is_well_formed` (`roster.py:228-253`) never checks that; the mutation leg M-3k proves it |
| 16 | The adapter pre-folds to `other` (the plan's earlier shape) ⇒ the registry's invalid-label counter can NEVER fire ⇒ vocabulary drift is silently absorbed | Medium | Medium | AM-2d: the adapter passes the **RAW** stored token to `.set()` and the **REGISTRY** folds it (`daemon/checkpoint_metrics.py:704-723` — `_fold_labels` folds the disallowed value to the reserved one and `_count_invalid_label` counts the folded key in `ensemble_metrics_invalid_label_total{metric,label}`), so the counter becomes a real drift signal; the family pack pins "no pre-fold in `w4.py`" AND "an out-of-vocabulary stored value increments `ensemble_metrics_invalid_label_total`"; mutation leg M-3j |

---

## Exit criterion

Phase 3 is DONE when, in the lane worktree (never the shared checkout):

1. `python -c "from daemon.services.metrics_query.wiring import registry; print(sorted(registry.registered_families()))"` lists **`F23`**, and `python -c "from daemon.services.metrics_query.roster import roster_family_names; print(roster_family_names())"` lists **`F23`** (chart-primary ⇒ the FE renders a trend panel);
2. `GET /api/metrics/families` (auth ON, a `metrics:read` or `maintenance:read` token) lists `F23` with its 3 metric names, and `/snapshot` + `/series` serve them — **with ZERO edits to `daemon/routers/metrics.py` and `daemon/tools/metrics_read.py`** (prove it: `git diff --stat` on those two files is empty);
3. `anomaly_clusters` / `anomaly_report_detail` are reachable ONLY by `maintenancer` / `devops` / `wanderer`, and every other agent is refused by the body gate;
4. `GET /api/nest-reports/clusters` answers **503 `AUTH_NOT_CONFIGURED`** with auth OFF **before** any repository read, and answers the decision-ready projection with auth ON;
5. the three new packs — **`tests/unit/metrics/test_metrics_family_w4.py`** (the family pack), `tests/unit/test_nest_report_read_tools.py`, `tests/unit/test_nest_report_rest_route.py` — + the amended adjudication pack + the two router cages + the privileged-set pins are **GREEN**, and the net-new-red diff against the TRUE pre-fix base is **EMPTY** (or every diff id is adjudicated in writing);
5a. **the mutation pack RUNS and its MT-01 leg is PLANTED, not SKIPPED** — `test/packs/metrics_read_mutation_matrix_test.py` prints `MT-01-count-pin: OK (RED via test_board_is_the_ninth_entry)` and `RESULT: PASS (N RED/GREEN legs …)`. A `SKIPPED` line means the find-string is stale ⇒ the pack is RED and the change is incomplete;
5b. **F23 PUBLISHES a real series** (not merely registers): a seeded `anomaly_reports` row ⇒ a live series on the provider (phase 6's A7: *"F23 present in the registry **and publishing**"*), proven by **`tests/unit/metrics/test_metrics_family_w4.py`** — the W-4-ruled pack path (**one path everywhere**: phase 3 and phase 6's `:109` + A7 all name it; the sibling's conformance is DONE). **A silent-dark F23 must not ship looking green**;
5c. **THE AMENDMENT PINS ARE GREEN:** the metrics router's path set is EXACTLY `{`/families`, `/snapshot`, `/series`}` (AM-1 — a 4th route reds); the F23 band is **67** (closed space 425 → **492**, the D-5-sourced pair) and the band-coverage pin `set(FAMILY_SERIES_BANDS) ⊇ set(REGISTERED_FAMILIES)` holds (AM-2a/b); **all five** F23 charted series' roles are asserted and the roster-coverage pin holds (AM-2c); the adapter contains **NO pre-fold** and an out-of-vocabulary stored value increments `ensemble_metrics_invalid_label_total` (AM-2d); the LIST payload has **no `excerpt` key** and `excerpt` appears only with `include_excerpt=true` (AM-12a/b); the FOUR VOCAB bump sites (`:200-217` docstring, `:419` literal + its `:401-418` history, `test_metrics_vocabulary_amendment.py:26` + its `:17-25` prose) are updated and `tests/helpers/metrics_exposition_baseline.py` is re-rendered after each bump (AM-3);
5d. **THE FIVE F23 METRICS ARE REGISTERED** (three pull-fed + `ensemble_anomaly_board_items_suppressed_total` (AM-13f) + `ensemble_anomaly_capacity_exhausted_total` (C3) — both write-time counters incremented by the service with a lazy import, both rostered, both pinned) and the band's **67** zero-headroom comment is in the code (AM-2a);
6. the vocabulary bump is **4 → 5** with the artifact regenerated by the generator, and the sibling's 3 → 4 is intact in history;
7. the two disclosures (R_i, R_ii) are written into the lane's disclosure section, un-fixed.
