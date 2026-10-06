# Design Spec Amendment — Contract Reconciliation

> **Type:** Amendment to the FROZEN design spec. Does NOT modify the
> frozen spec body (`design-spec.md`).
>
> **Original spec:** `.agents/shared/planning/snapshot-uiux/design/design-spec.md`
> **pinned_spec_sha:** `2ca69147b51383e452ba9d4185cb43b573ee5575`
> **Amendment scope:** BE↔FE contract reconciliation per
> `sequencing.md §1` (D-1…D-5) + reviewer-pinned D-6/D-7 specifics.
> **Author:** designer
> **Branch:** `feature/snapshot-uiux`
> **Status:** `approved` (effective on commit)

---

## 0 · Why this amendment exists

After the design spec was frozen at `2ca69147`, the BE and FE plans
were authored in parallel, and a contract reconciliation was performed
in `sequencing.md §1`. That reconciliation resolved five deltas
between the two plans (D-1…D-5) and surfaced two additional
specifics the reviewer pinned (D-6, D-7). The frozen design spec was
drafted BEFORE the reconciliation and therefore carries the
pre-canon contract details in §7.3 (envelope, param names, sort
enums, mandatory warm-spawn join) and the pre-canon age default in
AC-4.1.

This amendment records every supersession, maps the affected
acceptance criteria to the amended contract, and pins the effective
basis. From this point, the effective contract for implementation is
**frozen spec SHA `2ca69147` + this amendment**. Any further change
rides another amendment file.

---

## 1 · Supersession list (D-1 … D-7)

Each row is binding. Citations reference the frozen spec where the
PRE-reconciliation text lives; the post-reconciliation contract is
the new ground truth.

### D-1 — List envelope key → `items`

| Field | Value |
|---|---|
| Pre-reconciliation (spec §7.3 line 663) | `Response: { "snapshots": [SnapshotRow, ...], "total": int }` |
| Post-reconciliation | `Response: { "items": [SnapshotRow, ...], "total": int }` |
| Source of truth | `sequencing.md §1.1` D-1; mirrors `daemon/routers/blueprints.py:122-126` `BlueprintListResponse` |
| Rationale | BE canon wins; blueprints pattern is the project-wide convention for list endpoints. |
| Addendum | A-1 — `SnapshotListResponse` (BE) and `SnapshotListResponse` (FE TS interface) must use the same key name `items`. |

### D-2 — Agent filter param name → `agent` (wire)

| Field | Value |
|---|---|
| Pre-reconciliation (spec §7.3 line 654) | `agent_id string (optional — exact match)` |
| Post-reconciliation | `agent string (optional — exact match against created_by_agent_id)`. FE `SnapshotFilters.agent_id: string \| null` maps to `?agent=`. TS interface field name stays `agent_id` (matches the row field) — ONLY the wire-param name is `agent`. |
| Source of truth | `sequencing.md §1.1` D-2 |
| Rationale | BE canon wins; matches the BE allow-list param spelling. |

### D-3 — Sort enum consolidation + `warm_desc` dropped

| Field | Value |
|---|---|
| Pre-reconciliation (spec §7.3 line 658 + §1.4 AC-4.1) | `sort: one of: created_desc, created_asc, title_asc, warm_desc; default created_desc` |
| Post-reconciliation | Wire enum: 8 keys (BE allow-list wins). FE renders 4 sort options in v1: **`created_at_desc`** (default), **`created_at_asc`**, **`title_asc`**, **`status_asc`** (lowest-risk 4th from BE extras). The FE's `warm_desc` is NOT implementable in BE v1 (no warm-spawn join per D-5). |
| Source of truth | `sequencing.md §1.1` D-3 |
| Rationale | BE allow-list (8 keys) gives status/project dimensions at no extra cost. `warm_desc` deferred until a future Phase-2 join (D-5 deferred). |
| Rename map | `created_desc` → `created_at_desc`; `created_asc` → `created_at_asc`; `title_asc` unchanged; `warm_desc` → DROPPED; +1 new FE-visible key `status_asc`. |
| Addendum | FE `SnapshotFilters.sort` type carries a one-line comment documenting the rename + drop. |

### D-5 — `project_name` and `warm_spawn_count` NOT in list payload (v1)

| Field | Value |
|---|---|
| Pre-reconciliation (spec §7.3 lines 671 + 684, §7.4 lines 688-703) | List payload carries `project_name` (joined from `projects`) and `warm_spawn_count` (LEFT JOIN `snapshot_usage_counters`); SQL sketch at §7.4. |
| Post-reconciliation | List payload carries `project_id` ONLY (no `project_name` join). `warm_spawn_count` is NOT in the list payload (no join in v1). The detail endpoint still returns the full `to_dict()` shape (no `project_name` join, no `warm_spawn_count` field at all). |
| Source of truth | `sequencing.md §1.1` D-5 |
| Rationale | "Server-light per be-plan §3 D1." The `warm_spawn_count` join is a future Phase-2 item (design-spec §6.3 originally described it; that description is now superseded). |
| FE consequence | Table renders `project_id` (truncated UUID) in the Project column when the project name is unknown. Table renders `—` in the Warm column. Drawer "Warm-spawn count" section is OMITTED in v1 (1 template branch + 1 drawer section removal per fe-plan §6.6). |
| Addendum | A-2 — `d.pop("task_summary", None)` is added to BE `_to_list_item` (between be-plan §7 line 572-573) to keep list payloads lean. |

### D-6 — `limit` semantics (BE default 50 max 200; FE pageSize 25 with [10,25,50])

| Field | Value |
|---|---|
| Pre-reconciliation (spec §7.3 line 659) | `limit int (default 25, max 50)` |
| Post-reconciliation | **BE:** `limit: int = Query(default=50, ge=1, le=200)` with the double-clamp idiom at `daemon/routers/skills.py:1235-1240` (`effective_limit = min(int(limit), 200)`). **FE:** `pageSize` signal default = 25; `mat-paginator` pageSizeOptions = `[10, 25, 50]`. When the FE sends a value >50, the BE clamps to 200. When the FE sends a value ≤50, the BE honours it as-is. |
| Source of truth | `be-plan.md §4.1` line 103 + `be-plan.md §7` line 511; reviewer-pinned (D-6 not in sequencing.md §1; the values are taken from be-plan + the project precedent) |
| Rationale | Server-side defence in depth (`ge=1, le=200` validator + runtime clamp) plus a friendly FE default that matches the existing `skill-usage-table` convention. |
| FE consequence | Unchanged from the frozen spec (FE was already at pageSize 25, options [10,25,50]). The only contract change is the BE cap (200 vs 50 in the frozen spec's `max 50` text). |

### D-7 — Age filter: 4 presets, `all` default, `90d` dropped

| Field | Value |
|---|---|
| Pre-reconciliation (spec §1.4 AC-4.1 line 64) | `Age (5-button toggle group: 24h / 7d / 30d / 90d / All, default 30d)` |
| Post-reconciliation | `Age (4-button toggle group: 24h / 7d / 30d / All, default All)`. The `90d` preset is **dropped** in v1. The `all` preset is the **default** (the FE's "no age filter" sentinel; BE omits the `created_after` param when `all` is selected). |
| Source of truth | `fe-plan.md §5.1` line 243 (`age: '24h' \| '7d' \| '30d' \| 'all'; // default 'all'`) + reviewer-pinned (D-7 not in sequencing.md §1; the values are taken from fe-plan). |
| Rationale | (a) `all` as default matches the "see all snapshots" framing the page sets (matches be-plan D5: "default = ALL 5 statuses — no implicit active-only filter"). (b) `90d` is dropped in v1 to keep the toggle set tight; can be added in v1.1 if user feedback shows demand. |
| FE consequence | Filter bar shows 4 buttons. Active-filter count is unaffected (default `all` doesn't increment the count). Clear-filters resets Age to `all`. |
| Deferral note | `90d` recorded as a deferred option in the implementation hand-off (D-7 follow-up). |

---

## 2 · Reconciled acceptance criteria

The frozen spec carries ACs that reference the pre-reconciliation
contract. Each affected AC is reconciled to the amended contract
below. ACs not listed are unchanged.

### AC-1.4 (Settings removal) — unchanged

No contract effect. Keep as-is.

### AC-2.x (Header toggle) — unchanged

No contract effect. Keep as-is.

### AC-3.x (Metrics strip) — unchanged

`getSnapshotUsageMetrics` shape is unchanged (the endpoint may be
relocated to `GET /api/snapshots/metrics` per OK-1, but the response
shape `SnapshotUsageMetrics { capture_counts, spawn_counts_per_snapshot }`
is the same).

### AC-4.1 (Filter bar) — AMENDED

**Was:**
> A horizontal filter bar with: … Age (5-button toggle group: 24h / 7d / 30d / 90d / All, default 30d), Sort (searchable select: "Newest first" / "Oldest first" / "Title A–Z" / "Most warmed").

**Amended (D-3, D-7):**
> A horizontal filter bar with: Project (searchable select, default "All projects"), Agent (searchable select, default "All agents"), Status (5-chip multi-select, default none selected = all), Tags (chip input, `dim:value` format), Age (4-button toggle group: **24h / 7d / 30d / All, default `all`**), Sort (searchable select: **"Newest first" / "Oldest first" / "Title A–Z" / "Status (A→Z)"**).

### AC-4.5 (Status chip multi-select) — unchanged shape, but `created_after` BE-side effect

When the user picks a non-default Age (`24h` / `7d` / `30d`), the FE
computes the cutoff ISO string and sends it as `?created_after=...`
(per fe-plan §6.1 `computeAgeCutoff`). When the user picks `all`
(default), the FE OMITS the `created_after` param (no `?created_after=`
in the URL). The Status chip's "all" sentinel (empty selection) is
unchanged.

### AC-5.1 (List table columns) — AMENDED (D-5)

**Was:**
> The table uses Material `mat-table` with sticky header and 8 columns: Title, Project, Agent, Status, Tags, Created, **Warm count**, Actions.

**Amended (D-5):**
> The table uses Material `mat-table` with sticky header and 8 columns: Title, Project, Agent, Status, Tags, Created, **Warm count (renders `—` in v1)**, Actions. The Warm count column is preserved as a column slot so the future Phase-2 join is a non-breaking addition; the cell renders `—` for every row in v1.

### AC-5.4 (Status chip) — unchanged

### AC-5.5 (Tags overflow) — unchanged

### AC-5.6 (Title ellipsis) — unchanged

### AC-5.7 (Project column) — AMENDED (D-5)

**Was:**
> Project + Agent columns show the human-readable names (not the UUID). When a UUID is the only available form, fall back to truncated UUID.

**Amended (D-5):**
> Project + Agent columns render the BEST AVAILABLE form. In v1 the list endpoint returns `project_id` (UUID) only, so the Project column always shows the truncated UUID (per fe-plan §3.6 fallback at lines 145-146). A `count_by_project` cross-reference is available server-side but the page stays server-light in v1.

### AC-5.9 (Row keyboard a11y) — unchanged

### AC-6.1–AC-6.3, AC-6.5, AC-6.6, AC-6.8, AC-6.9 (Drawer) — AMENDED §6.4 only (D-5)

**AC-6.4 (Drawer sections, 8 sections) — AMENDED (D-5):**

**Was:**
> Drawer body sections (in order): Task summary, Git anchor, Runtime / Model, Supersedes chain, Tags, Timestamps, **Warm-spawn count**, Context (project + agent).

**Amended (D-5):**
> Drawer body sections (in order): Task summary, Git anchor, Runtime / Model, Supersedes chain, Tags, Timestamps, Context (project + agent). **The "Warm-spawn count" section is OMITTED in v1** (1 template branch + 1 drawer section removal per fe-plan §6.6 fallback "Does not implement `warm_spawn_count` join"). The list endpoint does not carry the value, so a static `—` would be the only render — a section heading with a single `—` is noise, so the section is dropped.

Drawer section count is therefore **7**, not 8. The first 6 sections
and the Context section are unchanged.

### AC-7.x (States) — unchanged

### AC-8.1 (Service ownership) — unchanged

### AC-8.2 (list(params)) — AMENDED (D-2, D-5)

**Was:**
> `SnapshotService.list(params)` calls a NEW backend endpoint (see §5 Tradeoffs for the contract; expected at `GET /api/snapshots`). The request is a single `HttpParams` object — query params built via `HttpParams` (NOT string-concat) for safety.

**Amended (D-2, D-5):**
> `SnapshotService.list(filters: SnapshotFilters)` calls `GET /api/snapshots` with a single `HttpParams` object built via `HttpParams.append(key, value)` in a loop (NOT string-concat). The wire-param name for the agent filter is **`agent`** (NOT `agent_id`); the TS interface field name stays `agent_id`. The response shape is `{ items: SnapshotRow[], total: number }` (D-1). The list payload does NOT carry `project_name` or `warm_spawn_count` (D-5); the page renders `project_id` (truncated UUID) in the Project column and `—` in the Warm column.

### AC-8.3 (getById) — AMENDED (D-5)

**Was:**
> `SnapshotService.getById(id)` returns a single snapshot row for the drawer (id-based fetch — the list endpoint already gives us the row, but id-based supports deep-link from a future `/snapshots/:id` route).

**Amended (D-5):**
> `SnapshotService.getById(id, opts: { includeDigest: boolean })` returns the full snapshot detail. The detail endpoint (separate from the list endpoint) returns the `to_dict()` shape MINUS the joined columns (`project_name`, `warm_spawn_count`) — the detail still has `task_summary` (D-4 reconciliation; the digest is opt-in via `?include=digest` per OK-4). The drawer's task-summary section still works (D-4 only affects the LIST payload; detail is full).

### AC-8.4 (Response shape) — AMENDED (D-1, D-5)

**Was:**
> The list endpoint is server-side paginated. Server returns `{ snapshots: SnapshotRow[], total: number }` — `total` drives the paginator; `snapshots` is the current page only.

**Amended (D-1, D-5):**
> The list endpoint is server-side paginated. Server returns `{ **items**: SnapshotRow[], total: number }` — `total` drives the paginator; `items` is the current page only. The `SnapshotRow` shape in the list payload does NOT include `project_name` (D-5) or `warm_spawn_count` (D-5) or `task_summary` (D-4 addendum A-2). The detail endpoint returns the full `to_dict()` shape with `task_summary` and (via `?include=digest`) the digest.

### AC-9.x (Aesthetic / non-functional) — unchanged

---

## 3 · Reconciled spec body (where the prose needs to be re-read)

These are pointers for the implementer — the frozen spec body is NOT
edited; this section re-states the post-amendment value so the
implementer reads the correct contract in one place.

| Frozen spec location | Was | Amended to |
|---|---|---|
| §2.3 Service tree: `list(params): Observable<{ snapshots: SnapshotRow[], total: number }>` | `{ snapshots, total }` | `{ items: SnapshotRow[], total: number }` |
| §4.4 Filter bar default state: "Age: 30d (default)" | 30d default | `all` default (omit `created_after` from the URL) |
| §4.4 Filter bar control list: "Age: 30d (default)" + 5 toggles | 5 toggles | 4 toggles (24h / 7d / 30d / all) |
| §4.5 Columns table row 7: "Warm count" + "from metrics join" | Server join | Renders `—` in v1; column slot preserved for Phase-2 |
| §4.6 Drawer sections table row 7: "Warm-spawn count" | Number from metrics join | **Section OMITTED in v1** |
| §4.6 "Sections (in order)" list: 8 sections | 8 sections | 7 sections (Task summary, Git anchor, Runtime/Model, Supersedes chain, Tags, Timestamps, Context) |
| §5.4 Wireframe: 5 age toggles, "30d" selected | 5 toggles, 30d | 4 toggles, `All` selected |
| §5.5 Wireframe: warm counts in column (3, 8, 12) | Numbers | All `—` |
| §5.6 Wireframe: 8 drawer sections including "WARM-SPAWN COUNT" | 8 sections | 7 sections; "WARM-SPAWN COUNT" absent |
| §6.3 Tradeoff "warm-spawn count" | "Adopted" server join | **Superseded by D-5** — the join is deferred to Phase-2; v1 is "no join, FE renders `—`". The spec's "reason for A" is now historical context, not the v1 plan. |
| §7.3 BE endpoint contract (entire block) | Old envelope, params, sort enum, limit cap, row shape, SQL | See §4 of THIS amendment (full restatement) |
| §7.4 SQL sketch with LEFT JOIN | Server join | DROP entirely — replaced by the bare `to_dict()` shape; the SQL becomes the basic `SELECT s.* FROM snapshots s WHERE … ORDER BY … LIMIT :limit OFFSET :offset` with no joins. |

---

## 4 · Restated BE endpoint contract (post-amendment)

This is the implementation contract. The implementer reads THIS
section, not the frozen spec §7.3 (which is now historical).

```
GET /api/snapshots
  Query params:
    project_id     string   (optional — exact match against snapshots.project_id)
    agent          string   (optional — exact match against snapshots.created_by_agent_id)
    status         string   (optional, REPEAT for multi — WHERE status IN (...); no implicit default)
    tags           string   (optional, REPEAT for multi — see tag_mode)
    tag_mode       string   (optional, one of: all | any; default all)
    created_after  string   (optional — ISO-8601; validated by datetime.fromisoformat)
    sort           string   (optional, one of: created_at_desc (default), created_at_asc,
                                    title_asc, title_desc, status_asc, status_desc,
                                    project_id_asc, project_id_desc)
    limit          int      (Query(default=50, ge=1, le=200) + runtime clamp min(int(limit), 200))
    offset         int      (Query(default=0, ge=0))

  Response 200:
    {
      "items": [SnapshotRow, ...],   // current page only
      "total": int                    // total matching rows, drives the paginator
    }

  SnapshotRow shape (list payload — LEAN per D-4 addendum A-2):
    {
      id: string,
      project_id: string,             // UUID; FE renders truncated (D-5 — no project_name in v1)
      created_by_agent_id: string,
      target_instance_id: string,
      title: string,
      // task_summary: OMITTED in list (D-4 addendum A-2: d.pop("task_summary", None))
      domain_tags: string[],
      status: 'active'|'superseded'|'running'|'failed'|'interrupted',
      supersedes_snapshot_id: string | null,
      git_sha: string | null,
      git_branch: string | null,
      git_dirty: boolean,
      repo_path: string | null,
      runtime_version: string,
      effective_model: string | null,
      created_at: string,             // ISO-8601
      // warm_spawn_count: OMITTED in v1 (D-5 — Phase-2 join)
    }

  422: tag_mode not in {all, any}; status not in SNAPSHOT_STATUSES;
       sort not in allow-list; limit/offset out of range; created_after invalid ISO.

  Validation (test pack):
    test_list_envelope_items: response.items is an array; .snapshots is undefined.
    test_list_filter_agent_param: ?agent=foo → query matches created_by_agent_id='foo'; ?agent_id=foo → 422.
    test_list_sort_warm_desc_422: ?sort=warm_desc → 422 (not in allow-list).
    test_list_sort_allowlist: all 8 values accepted.
    test_list_default_sort: ?sort omitted → SQL ORDER BY created_at DESC.
    test_list_limit_default: ?limit omitted → SQL LIMIT 50.
    test_list_limit_max: ?limit=200 → accepted; ?limit=201 → 422.
    test_list_limit_clamp: ?limit=999 → effective_limit=200 (runtime clamp).
    test_list_age_default: ?age omitted (FE doesn't send age; sends created_after only when not 'all').
    test_list_tag_repeat: ?tags=domain:api&tags=runtime:py → IN-clause with 2 values; tag_mode=all.
    test_list_no_warm_count: response.items[0] does NOT have a warm_spawn_count key.
    test_list_no_project_name: response.items[0] does NOT have a project_name key.
    test_list_no_task_summary: response.items[0] does NOT have a task_summary key.
```

```
GET /api/snapshots/{snapshot_id}
  Query params:
    include        string   (optional, one of: digest; if 'digest', include the digest field populated)

  Response 200: full to_dict() shape MINUS warm_spawn_count and project_name (D-5). task_summary is INCLUDED.
                If ?include=digest, the digest field is populated; otherwise digest is present but empty {}.

  404: snapshot_id not found.
  422: include not in {digest}.
```

```
GET /api/snapshots/metrics
  Response 200: same shape as the legacy GET /api/settings/snapshot-usage-metrics
                (SnapshotUsageMetricsResponse: { capture_counts, spawn_counts_per_snapshot }).
                The legacy path stays as a 1-line re-export for one release (be-plan D8);
                the Deprecation: true header is added (OK-1 / RX-14).
```

---

## 5 · Reconvened test pack (delta vs. frozen spec §7.6)

The frozen spec's §7.6 test pack referenced ACs that the amendment
reconciles. The implementer applies the AC changes first, then
re-derives the test pack lines. The net delta to the test pack:

| Test pack line | Was (frozen spec) | Amended |
|---|---|---|
| `e2e_snapshots_list_table` | "8 columns render in order; status chip colors; tag overflow +N chip" | **8 columns still render** (D-5 keeps the Warm count column slot; cell renders `—`); status chip + tag overflow unchanged |
| `e2e_snapshots_drawer` | "drawer opens; **8 sections present**" | "drawer opens; **7 sections present**" (D-5: warm-spawn section omitted) |
| `e2e_snapshots_filter_bar` | "5 filter controls; …" | "**5 filter controls (4 age toggles + 1 sort + 1 project + 1 agent + 1 status + 1 tags = 6 controls actually, 1 set of 5 in original counting)**" — the age toggle count drops from 5 to 4; clear-all-filters now resets Age to `all` |
| `static_snapshots_no_new_tokens` | "No new CSS color variables beyond the 5 status colors." | Unchanged |

The test pack lines for the list endpoint (AC-8.x) are extended:

| Test pack line | Added in v1 (this amendment) |
|---|---|
| `unit_snapshots_list_envelope` | Response envelope is `{ items, total }`; `.snapshots` is undefined. |
| `unit_snapshots_list_agent_param` | Wire param is `?agent=`; `?agent_id=` returns 422. |
| `unit_snapshots_list_sort_allowlist` | All 8 BE sort values accepted; `?sort=warm_desc` returns 422. |
| `unit_snapshots_list_limit_clamp` | `?limit=200` accepted; `?limit=201` 422; `?limit=999` clamps to 200. |
| `unit_snapshots_list_default_limit` | `?limit` omitted → server uses 50. |
| `unit_snapshots_list_no_warm_count` | `response.items[0].warm_spawn_count` is undefined. |
| `unit_snapshots_list_no_project_name` | `response.items[0].project_name` is undefined. |
| `unit_snapshots_list_no_task_summary` | `response.items[0].task_summary` is undefined. |
| `unit_snapshots_list_age_default` | When FE sends no `created_after` (Age = `all`), server returns all rows; when FE sends `?created_after=<ISO>`, server filters. |

---

## 6 · Effective pinned basis

From this point, the implementation contract is:

> **FROZEN SPEC** (`.agents/shared/planning/snapshot-uiux/design/design-spec.md`, SHA `2ca69147b51383e452ba9d4185cb43b573ee5575`) **+ THIS AMENDMENT** (D-1…D-7 supersessions, AC reconciliation in §2, restated endpoint contract in §4).

The frozen spec body is NOT modified. The implementer reads BOTH
files. Where the two disagree, the amendment wins. Where neither
speaks, the binding `sequencing.md §1` decisions apply.

Future contract changes ride a new amendment file
(`design-spec-amendment-<reason>-2.md` or similar). The frozen spec
SHA `2ca69147` is a permanent reference; the spec body is immutable.

---

## 7 · Commit + traceability

| Commit | Purpose |
|---|---|
| `2ca69147` | Frozen spec (pre-reconciliation draft) |
| `0aba9924` | Mockup + SHA pin in spec front-matter |
| `<this>` | This amendment — contract reconciliation D-1…D-7 |

The amendment file path is
`.agents/shared/planning/snapshot-uiux/design/design-spec-amendment-contract-reconciliation.md`.
The plan file `sequencing.md §1` will receive a reference to this
amendment (the planner adds the cross-link, not the designer).
