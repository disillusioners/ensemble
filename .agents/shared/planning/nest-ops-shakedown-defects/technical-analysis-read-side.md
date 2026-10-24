# Technical Analysis (READ SIDE): nest-ops shakedown defect patch

Date: 2026-10-22
Author: planner[v2] via technical-analysis worker (read-side lane)
Worktree: `/home/nea/ensemble-src-wt-nestops-shakedown`
Branch: `fix/nest-ops-shakedown-defects` — base `origin/latest` `b257842c9`
Analysis depth: deep-dive (implementation-grade: a developer implements from this, a tester writes pins against it)
Status: Ready for Review
Scope: the READ side only — `daemon/tools/instance_read.py` + the repository surface it reads. The WRITE side (`daemon/tools/instance_ops.py`) is out of this document.

---

> **⚖️ ARCHITECT ENRICHMENT 2026-10-24 — PARTIAL SUPERSESSION MAP (architecture-recommendation.md is AUTHORITY where it differs).**
> This dated snapshot is preserved as history; the following sections are SUPERSEDED IN PART — implement from the AMENDED decisions
> (decisions.md D-1/D-2/D-3/D-4/D-6 + D-18/D-19/D-20) and phase1-plan.md, not from the statements below:
> - **§3 (F3 husk)** — the "canonical-legs reuse" premise is FALSE as stated: `filter_never_dispatched_ids` is `idle ∧ v1 ∧ ¬∃msg ∧ ¬∃task`
>   (`repository.py:785-789`), NOT a pure evidence probe; the lockstep pin also pins the blindness (`test_pause_never_dispatched_ghosts.py:140-153`).
>   Amended shape (arch §1.2): module-level `never_dispatched_evidence_expr()` factory + NEW evidence-only `filter_without_any_evidence_ids`
>   (chunk 500); the husk detector calls ONLY the new method; containment pin added; disagreement_pin states the evidence-conjunct containment.
> - **§4 (F2 census)** — ONE method / TWO statements / ONE session (COUNT first; no GROUPING SETS); shared `_kb_filter` helper used by BOTH
>   `list()` and the new method; `idle` is a STATUS bucket only (liveness maps idle→processing); race disclosure mandatory (arch §2).
> - **§5 (F4 scan, incl. §5.1's flag gloss)** — the sketched loop can silently skip the oldest rows under concurrent writes; §5.1's empty-page gloss is likewise corrected to the QUALIFIED form (an empty page is `true` ONLY when `total > offset`; the exhausted case `len(rows)==0 ∧ total ≤ offset` reads `false`); corrected shape: first-window `total`
>   (never reassigned), dict dedupe by `instance_id`, break ONLY on end-of-set/cap, `scan_truncated = (distinct_read < total_first) OR (cap
>   fired without end-of-set)`, `scan_stop_reason`, 10s `FLEET_SCAN_TIME_BUDGET_SECONDS`, STREAM per window (the "memory bounded per window"
>   claim was FALSE — whole-fleet materialization ≈ 20–40 MB @20k); the `:543` formula is exact only AFTER F12 (arch §3).
> - **§6 (F8)** — disclose the `agent_tag`/`agent_dir` payload delta (cold reads stop promoting); the resolver must reproduce ALL FOUR
>   ValueError texts byte-for-byte + empty-id guard + WARNING (`instance.py:1472-1516`); the feared question-pack regression does NOT exist
>   (arch §5).
> - **§8 (F1)** — TWO LITERAL `@tool` closures required (AST discovery scans `@tool`; a code-generated alias would NOT be discovered) (arch §6).
> - **§9 (test strategy)** — the net-new ladder and the >window fixture are corrected in phase4-plan.md (two-tier fixture; TEXT-ISO trap;
>   leg-0/A/B ladder) (arch §11). Also superseded per reviewer N-8: §9.3's F3 pin-direction phrasing — use D-1's rewritten EVIDENCE-CONJUNCT containment ("every husk-flagged row satisfies ¬∃msg ∧ ¬∃task (via the shared expression) ∧ non-terminal ∧ last_activity_at IS NULL ∧ age > threshold"), NOT the old 'husk ⊆ canonical ghost set' phrasing (false once a paused evidence-free row can be flagged); and §9.3's F2/F5 mutation shapes — F2 SPLITS into three single-behavior mutations (windowed loop → coverage pin; dropped independent total → reconciled pin; hard-coded scan_truncated=True → literal-False pin) and F5 pins the `dead_jobs_total == Σ per-queue dead` EQUALITY plus the docstring two-table distinction (a key-presence mutation proves nothing), per arch §11.3.
> - **NEW F12** (arch §3.1): the `status` filter is dropped at the lifecycle→repo seam (`instance_lifecycle.py:5086-5095`) — F4's `truncated`
>   and F2's reconciliation rest on false claims without the 1-line forward; included as the 12th defect (leader decision 2026-10-24).

---

## Question

How must the six read-side defects (F3, F2, F4, F8, F1, F5) be fixed on the live `instance_read` surface so that:

1. an **evidence-free husk** instance (spawned, never dispatched, no activity ever) is DETECTED by `instance_health` — including the two known production targets `5ba21b2d-43b7-4378-be53-cca781f9c124` and `7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f`;
2. the census **counts every instance** and its totals **reconcile** with what `instance_list` reports;
3. the pagination flags are **honest** and the health scan covers the **full age range**;
4. `instance_get` is **side-effect-free** (no MCP discovery traffic, no DB write);
5. the `fleet_capacity_summary` / `instance_fleet_summary` name mismatch is resolved **without breaking the live name**;
6. `dlq_depth` and the per-queue `dead` counts stop reading as a contradiction.

---

## Context Summary

`daemon/tools/instance_read.py` (1415 lines) is the privileged `instance_read` category: four read tools built by one factory, `create_instance_read_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None)` (`daemon/tools/instance_read.py:397-420`), returning four `@tool` closures — `instance_list` (`:462-464`), `instance_get` (`:617-619`), `instance_health` (`:866-868`), `fleet_capacity_summary` (`:946-948`). Every body carries the same fail-closed authorization helper `_is_instance_read_authorized` against `INSTANCE_READ_AUTHORIZED_AGENTS = frozenset({"maintenancer"})` (`:115-168`), and every failure returns a typed refusal string rather than raising (`:171-183`). The `maintenancer` grants the CATEGORY, not tool names (`agents/maintenancer/meta.json:11-21`), so a new tool name inside the category needs no agent-file change.

The surface was built on 2026-10-21 (nest-ops, live in v0.27.1). A day later the shakedown found six read-side defects. The root cause of the sharpest one (F3) is structural: **all six existing detectors are evidence-keyed** — a row with *no* evidence has no hook, and `NULL last_activity_at` defeats the staleness arithmetic (`_seconds_since` returns `None` for an absent stamp, `:211-216`; the census renders `stale = idle_seconds is not None and idle_seconds > stale_seconds`, `:374`), so a 16-day-old husk reports `stale: false`. The six detectors are `stale-running` (`:1074-1107`), `waiting-on-a-dead-parent` (`:1109-1142`), `question-paused-without-task-row` (`:1144-1179`), `zombie-pending` (`:1181-1234`), `orphans` (`:1236-1269`), `failed-with-unreported-result` (`:1271-1298`) — each is a *projection* over evidence that already exists (a task row, a JobItem, a child, a parent, a status).

The decisive discovery for this design is that **the husk shape is already a named, canonical, twice-written concept in this codebase** — the "never-dispatched ghost": `ChildReportsService._ghost_child_filter()` (`daemon/services/child_reports.py:2864-2901`) and its batched twin `InstanceRepository.filter_never_dispatched_ids()` (`daemon/repositories/instance/repository.py:735-790`), kept in lockstep by `tests/unit/test_pause_never_dispatched_ghosts.py`. The predicate is `status == 'idle' AND version == 1 AND no message_queue rows AND no task rows`. The husk class is that shape plus age, minus the two narrow legs that the canonical signature does not carry. The design below therefore **reuses** the canonical evidence legs instead of forking them — the same anti-fork discipline the codebase applies to `ALIVE_INSTANCE_STATUSES` / `TERMINAL_INSTANCE_STATUSES` (`daemon/constants.py:695-766`, whose comment states plainly that a duplicated status set is "a silent-divergence risk").

Two further structural facts shape everything:

- The health scan reads the fleet through `manager.list_instances(limit=FLEET_SCAN_LIMIT, offset=0)` with `FLEET_SCAN_LIMIT = 1000` (`daemon/tools/instance_read.py:129-133`, `:1066-1068`), while the flat repository path returns a **constant `truncated=False`** (`daemon/repositories/instance/repository.py:1176-1177`) and orders rows **pinned-first, then `created_at DESC`** (`:1158-1174`). At 1730 rows the scan therefore sees the newest 1000 and never reaches the oldest 730 — where day-scale husks live. **F3 without F4 is still blind to the two known targets.** This coupling is the single most important cross-defect constraint in this document and is called out again in §4.
- `instance_get` resolves its target through `_resolve_target` → `_resolve_instance_id` (`daemon/tools/instance.py:1451-1477`), whose *exact-match fast path* calls `await manager.get_instance(instance_id)`. On a cold instance that call runs `ensure_mcp_preloaded` (`daemon/services/instance_lifecycle.py:4505` → `daemon/manager.py:9288-9359` → `daemon/services/mcp_service.py:630-826`) and then `_restore_instance` (`:4513`), which can **write** the instances row during version re-elevation (`daemon/services/instance_lifecycle.py:4671-4688`). The docstrings nevertheless claim "Side effects: NONE. Read-only; no audit row." (`daemon/tools/instance_read.py:594-595`, `:843-844`, `:934-935`, `:982-983`). The claim is currently false.

---

## 1. Executive summary — one paragraph per defect

### F3 — `instance_health` is blind to the evidence-free HUSK class 🔴

**Root cause.** Every detector is *evidence-keyed*: each one fires because some artifact exists (a task row, a `job_queue_items` row, a child, a parent, a status transition). A husk is the **absence** of all of them. On top of that, the time axis is blind: `instances.last_activity_at` is nullable (`daemon/repositories/instance/models.py:72`) and is stamped only by real dispatch/turn/revive activity (`daemon/services/instance_messaging.py:2455`, `daemon/services/job_feedback_observer.py:3632`/`:4510`, `daemon/services/error_reporting.py:245`, `daemon/services/child_reports.py:1065`/`:4453`, `daemon/manager.py:9832`) — never by `repository.update()` or `transition_status_if()`, which touch `updated_at` only (`daemon/repositories/instance/repository.py:1466-1479`, `:1502-1517`). So a never-dispatched row has `last_activity_at IS NULL` forever, `_seconds_since` returns `None` (`:211-216`), and both the staleness math (`:374`, `:1087`) and the question-pause detector's `if idle is None … continue` (`:1151`) silently skip it. **Chosen design.** Add **detector #7, `never-dispatched-husk`**: a non-terminal row whose `last_activity_at IS NULL`, whose age since `created_at` exceeds a new constant `DEFAULT_HUSK_MIN_AGE_SECONDS = 86_400` (24 h), and which carries **no evidence artifact of any kind** — no task row, no `job_queue_items` row, no `message_queue` row, no child instance, no question pack, no pause-cause marker. The two canonical evidence legs (no task row, no message row) are taken from the existing ghost signature rather than re-derived; the extra legs are one batched JobItem query plus row-local checks. See §3.

### F2 — the census has no `idle` bucket and its totals do not reconcile 🟠

**Root cause.** `counts_by_status` / `counts_by_agent` / `counts_by_liveness` are built by looping over the **scanned window only** (`daemon/tools/instance_read.py:1309-1318`), and the window is `manager.list_instances(limit=1000, offset=0)` (`:1305-1307`) — the newest 1000 of 1730 rows, minus the KB-agent exclusion the repository applies (`daemon/repositories/instance/repository.py:33`, `:1141-1143`). The 730 invisible rows are the OLDEST ones (`created_at DESC` ordering, `:1158-1174`), and the husk class lives entirely there. The sum of `counts_by_status` (1000) therefore contradicts `instance_list.total_matching` (1730, same `COUNT(*)` over the same WHERE, `:1133-1153`). **Chosen design.** Compute the breakdown with **one grouped SQL query** over the whole fleet, marginalizing `GROUP BY status, agent_id` in the tool; derive `counts_by_liveness` from `counts_by_status` via the canonical map; and expose an explicit reconciliation block (`total_rows`, `counted_rows`, `scan_truncated`, `reconciled`). The `idle` bucket then appears **naturally** — `idle` is one of the ten `InstanceStatus` values (`daemon/repositories/instance/models.py:21`) and the default status of a created row (`:62`) — so full-range counting *is* the idle-bucket fix; no new bucket type is invented. See §4.

### F4 — pagination flags are self-contradictory and the health scan is age-blind 🟠

**Root cause.** `truncated` is copied straight out of the repository's flat path, which returns a literal `False` (`daemon/repositories/instance/repository.py:1176-1177`) because only the `include_descendants=True` BFS path can set it (`:1117-1119`) — and `instance_read` never passes that flag. So `instance_list` reports `count=200, total_matching=1730, truncated=false`: two of the three numbers are honest and the third contradicts them. Meanwhile the health scan reads the newest 1000 rows only, and the first page of an `instance_list` page spans ≈14.4 h, so a naive week-old-zombie scan sees nothing. **Chosen design.** Compute truncation honestly as `bool(total > offset + len(rows))` at every surface that exposes it (`instance_list.truncated`, `instance_health.scan_truncated`, `fleet_capacity_summary.scan_truncated`), and replace the single 1000-row window with an **offset-window loop** that covers the whole fleet, bounded by a loud cap (`FLEET_SCAN_MAX_WINDOWS`). See §5.

### F8 — `instance_get` is not side-effect-free 🟠

**Root cause.** `_resolve_target` (`:334-348`) delegates to `_resolve_instance_id` (`daemon/tools/instance.py:1451-1477`), whose exact-match fast path calls `await manager.get_instance(instance_id)`. For a **cold** instance (not in `manager.instances`) that runs `ensure_mcp_preloaded` (`daemon/services/instance_lifecycle.py:4505`), which lists every active MCP server and calls `get_schemas_for_server` per server (`daemon/services/mcp_service.py:715-717`, `:236-319`); the schema cache is keyed by **server name and shared globally across instances** (`:258-262`, `:200`), and an **empty** discovery result is deliberately **never cached** (the "negative-cache poisoning" fix, `:286-293`), so only a 30 s monotonic throttle (`EMPTY_DISCOVERY_RETRY_THROTTLE_S = 30.0`, `:53`, checked `:294-305`) bounds re-discovery. A dead `plane` server therefore re-runs **external discovery** on every cold `instance_get`, more than 30 s apart — a read generating external traffic against dead instances. The same cold path then calls `_restore_instance`, whose version re-elevation path **mutates the instances row** (`agent_tag`/`agent_dir` update + `delete_metadata`, `daemon/services/instance_lifecycle.py:4671-4688`). **Chosen design.** Introduce a **read-scoped resolver** that resolves the target with repository-only primitives (`repo.get` exact + `manager.find_near_instance` fuzzy — the latter is already repo-only, `daemon/manager.py:13817-13836`) and never calls `manager.get_instance`; the read path then provably issues no MCP discovery and no write, and the "Side effects: NONE" docstring becomes **true** rather than aspirational. See §6.

### F1 — tool-name mismatch: live `fleet_capacity_summary` vs specified `instance_fleet_summary` 🟢

**Root cause.** The ratified rename is recorded at `.agents/shared/planning/nest-ops/architecture-recommendation.md:59` (verdict), `:268` (Q1 row), `decisions.md:121`, `plan-overview.md:401`/`:564` — but the **pre-rename** rows at `architecture-recommendation.md:37` and `:57` still say `instance_fleet_summary`, and the shipped tool is named `fleet_capacity_summary` (`daemon/tools/instance_read.py:946-948`). The registry carries the live name in **two** lists — `DYNAMIC_TOOL_NAMES` (`daemon/tools/_tool_registry.py:100-109`) and `KNOWN_TOOL_NAMES` (`:1167-1176`) — and a bidirectional drift pin (`tests/unit/tools/test_frozen_tool_name_discovery.py:223-242`) requires any new `@tool` name to join **both**. No alias mechanism exists anywhere under `daemon/tools/`. **Chosen design.** Do **not** rename. Add a **second delegating `@tool` closure** `instance_fleet_summary` in the same factory (same category decorator, same auth gate, same payload builder `_build_capacity_summary`), register it in both registry lists, give it its own `_full_doc_`, and annotate the two stale planning-doc rows so the docs stop asserting the old name as if it were the shipped one. See §8.

### F5 — `dlq_depth = 0` while the per-queue depths show 5 dead items 🟠

**Root cause.** The two numbers measure **different tables with different write paths**. `dlq_depth` is `SELECT COUNT(*) FROM dead_letter_items` (`daemon/tools/instance_read.py:1380-1387` → `daemon/services/dead_letter_service.py:575-592` → `daemon/repositories/job_queue/dead_letter_repository.py:183-205`) — a **snapshot** table written only by the DLQ service's move paths (`daemon/services/job_queue_service.py:3235`, `:5151`, `daemon/services/job_retry_engine.py:437`) and pruned by retention (`dead_letter_repository.py:152-181`). The per-queue `dead` is a grouped count over `job_queue_items.admission_state = 'dead'` (`daemon/tools/instance_read.py:1328-1342`) — the job row's **canonical** terminal state, which can be reached **without** any DLQ snapshot, e.g. through `atomic_transition(..., to_status="dead_letter")`, whose legacy-status map turns `"dead_letter"` into `AdmissionState.DEAD` (`daemon/repositories/job_queue/repository.py:36`) — the "structurally silent terminal write" of the drift-recovery path (`daemon/services/job_recovery_service.py:4076-4086`). Both numbers are real; they answer different questions. **Chosen design.** **Document, do not unify** — keep `dlq_depth` (a shipped field) but state precisely what it measures, and add one derived canonical field `dead_jobs_total` (the sum of the already-computed per-queue dead counts, zero extra queries) plus an explicit note that `dlq_depth ≤ dead_jobs_total` is expected. See §7.

---

## 2. Module map (what the developer is editing)

```
daemon/tools/instance_read.py            (1415 lines — the whole read surface)
├── constants           :115-152   INSTANCE_READ_AUTHORIZED_AGENTS, MAX_LIST_LIMIT=200,
│                                 DEFAULT_LIST_LIMIT=50, DEFAULT_STALE_MINUTES=30,
│                                 DEFAULT_STALE_SECONDS=1800, FLEET_SCAN_LIMIT=1000
├── auth + envelopes    :155-183   _is_instance_read_authorized, _refusal, _error
├── field helpers       :185-295   _liveness_for, _parse_iso, _seconds_since,
│                                 _redact_metadata, _pause_marker_of, _question_pack_of,
│                                 _pending_counts_by_instance (one grouped query)
├── lineage aggregate    :298-328   _lineage_max_activity (R4)
├── resolver            :334-348   _resolve_target  ← F8 seam
├── census row          :351-394   _census_row (age_seconds, idle_seconds, stale)
├── factory             :397-420   create_instance_read_tools
│   ├── instance_list   :462-601   census (F2/F4 surface)
│   ├── instance_get    :617-848   hydration (F8 surface)
│   ├── instance_health :866-940   six detectors (F3 surface)
│   └── fleet_capacity  :946-987   counts/queue/pool/DLQ (F2/F5 surface)
├── report builders     :1010-1300 _flagged_row, _parent_status_of, _has_live_task,
│                                 _build_health_report (SIX detectors)
└── capacity builder    :1303-1415 _build_capacity_summary (counts, queue depth,
                                  worker pool, dlq_depth, audit_pending)

daemon/repositories/instance/repository.py
├── list(...)           :1008-1177 flat path — count stmt :1133-1153, KB exclusion
│                                 :1141-1143, ORDER BY pinned-first :1158-1174,
│                                 CONSTANT truncated=False :1176-1177
├── create(...)         :403-455   row birth: status='idle', last_activity_at NOT set
├── filter_never_dispatched_ids :735-790  CANONICAL never-dispatched ghost predicate
├── list_child_ids_permanent :262-282      permanent children (parent_id record)
└── update / transition_status_if :1422-1517  touch updated_at, NEVER last_activity_at

daemon/services/child_reports.py
└── _ghost_child_filter :2864-2901  CANONICAL ghost SQL (lockstep twin of the above)

daemon/tools/_tool_registry.py
├── register_tool_category :529-554  (sets _tool_category + first-party marker)
├── DYNAMIC_TOOL_NAMES :100-109 · KNOWN_TOOL_NAMES :1167-1176  (F1: BOTH lists)
└── scan_tools_for_full_docs :818-873 (keyed on the tool NAME — F1 needs its own doc)

daemon/tools/instance.py
├── create_instance_tools → tools.extend(create_instance_read_tools(...)) :6058
└── _resolve_instance_id :1451-1477  (exact leg calls manager.get_instance — F8)
```

---

## 3. F3 — HUSK DETECTOR SPEC (detector #7)

### 3.1 The predicate

```python
# Pseudo-code — close to the implementation shape.
# Constant: DEFAULT_HUSK_MIN_AGE_SECONDS = 86_400   (instance_read.py, next to
#                                                       DEFAULT_STALE_SECONDS :121-125)

def is_husk(row, *, now, husk_min_age_seconds) -> bool:
    # ── leg 1: non-terminal ────────────────────────────────────────────────
    # TERMINAL_INSTANCE_STATUSES = {completed, terminated, error, failed}
    #   (daemon/constants.py:702-707).  "waiting" is in NEITHER the terminal nor
    #   the alive set (daemon/constants.py:760-766) — see ADJACENT (a).
    if row.status in TERMINAL_INSTANCE_STATUSES:
        return False

    # ── leg 2: no activity EVER (not merely old activity) ─────────────────
    # last_activity_at is stamped only by dispatch/turn/revive writers
    # (instance_messaging.py:2455, job_feedback_observer.py:3632/:4510,
    #  error_reporting.py:245, child_reports.py:1065/:4453, manager.py:9832).
    # repository.update() and transition_status_if() touch updated_at only
    # (instance/repository.py:1466-1479, :1502-1517).
    if row.last_activity_at is not None:
        return False

    # ── leg 3: age ────────────────────────────────────────────────────────
    # created_at is NON-NULL (models.py:74) and is the row's birth stamp
    # (repository.py:428-431).  age_seconds is already computed by the census
    # row helper (_census_row :369).
    age = seconds_since(row.created_at)          # never None for a real row
    if age is None or age <= husk_min_age_seconds:
        return False

    # ── legs 4-9: evidence (see 3.3) ─────────────────────────────────────
    return has_no_evidence(row)
```

`has_no_evidence(row)` is the conjunction of:

| # | Evidence kind | Probe | Vacuous? |
|---|---|---|---|
| E1 | **task rows** | `repo.filter_never_dispatched_ids([...])` — the canonical `~exists(select(Task.id) …)` leg (`daemon/repositories/instance/repository.py:757-760`) | no |
| E2 | **message rows** | same call — the canonical `~exists(select(MessageQueue.message_id) …)` leg (`:753-756`) | no |
| E3 | **JobItem rows** | one batched grouped query over `job_queue_items` for the candidate id set (`daemon/tools/instance_read.py:1328-1342` is the module's own precedent for a grouped one-round-trip query) | no |
| E4 | **child instances** | `len(row["children"]) == 0` — the children list is already on every row (`daemon/services/instance_lifecycle.py:5103-5106`) | no |
| E5 | **question pack** | `_question_pack_of(manager, instance_id) is None` (`:254-267`, RAM-only, never touches the DB) | no |
| E6 | **pause-cause marker** | `_pause_marker_of(row["metadata"]) is None` (`:241-251`) — the marker lives at `instances.metadata["question_paused"]` and is either a question marker (`{"pack_id", "paused_at"}`) or a **deliberate-park tombstone** (`{"deliberate": true, "cleared_at": …}`, `daemon/services/question_pause_set.py:612-641`) | no |
| E7 | **mission_ref** | **NOT a predicate leg — see 3.4** | yes (structurally) |

### 3.2 Threshold: `DEFAULT_HUSK_MIN_AGE_SECONDS = 86_400` (24 h)

**Where it lives.** `daemon/tools/instance_read.py`, immediately after `DEFAULT_STALE_SECONDS` (`:121-125`), as a module-level constant:

```python
#: The minimum age (seconds) of a NEVER-ACTIVATED instance before the husk
#: detector reports it. 24h — see technical-analysis §3.2.
DEFAULT_HUSK_MIN_AGE_SECONDS: int = 86_400
```

**Why 24 h.** The requirement is a threshold that (a) fires on the two known 12-day and 16-day targets, (b) is clearly not "normal spawn latency", (c) has house precedent. The house precedent ladder, all verified in this worktree:

| House constant | Value | Site | What it bounds |
|---|---|---|---|
| `ORPHAN_MIN_AGE_SECONDS` | 60 s | `daemon/services/mission_live_guard.py:294` | the strand **race guard** — "a freshly spawned child may be about to receive its first message" |
| `DEFAULT_STALE_SECONDS` | 1 800 s (30 min) | `daemon/tools/instance_read.py:121-125` | *this surface's* staleness threshold |
| `DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS` | 7 200 s (2 h) | `daemon/services/stale_pause_sweep.py:126` | a parked instance is stale |
| `DEFAULT_RUNNING_ZOMBIE_AGE_BOUND_SECONDS` | 7 200 s (2 h) | `daemon/services/mission_strand_healer.py:197` | a completed turn whose root still says `running` |
| `usage_limit_window_seconds` | 21 600 s (6 h) | `daemon/config.py:1776-1783` | the quota-window horizon |

24 h is **43×** the 2 h sweep bounds, **4×** the 6 h horizon, and **1 440×** the 60 s race guard — three orders of magnitude above the longest conceivable legitimate spawn→dispatch latency (the ritual is "spawn then immediately message", seconds to minutes; the 60 s race guard was itself calibrated as the *floor* for that window). It fires on a 12-day target with a 12× margin and a 16-day target with a 16× margin, so fixture age jitter cannot flip the verdict. Operationally it also matches the daily cadence an operator reads a wedge report at: a husk will not appear and vanish inside one working session.

**Why it is NOT derived from `stale_minutes`.** The caller-supplied `stale_minutes` (default 30, `:854-864`) means "a **running** instance with no activity for N minutes" — a *turn-in-flight* judgement. The husk threshold means "this instance **never had a turn**" — an *evidence* judgement. Coupling them would fire the husk detector at 30 minutes and turn every legitimate "spawned, waiting for the parent's first message" row into a false positive. They are separate concepts and get separate constants.

**Why it is not a tool argument.** Keeping it a module constant keeps the tool schema stable (no new argument to document, no new refusal token) and lets the tester drive the boundary by **backdating `created_at` in the fixture** instead of by a knob. If the plan wants operator-tunability later, the house pattern is a config field (as `usage_limit_window_seconds` does), not a tool argument — that is a separate change.

### 3.3 Why the evidence set is exactly this set

- **E1/E2 (task, messages) are the canonical legs.** They are *not* re-derived here. `InstanceRepository.filter_never_dispatched_ids` (`:735-790`) and `ChildReportsService._ghost_child_filter` (`daemon/services/child_reports.py:2864-2901`) already express "zero `message_queue` rows, zero `task` rows", and the two are pinned in lockstep by `tests/unit/test_pause_never_dispatched_ghosts.py` (the docstring at `:743-746` states the lockstep requirement explicitly). The husk detector **calls the repository twin** for its candidate set rather than writing its own EXISTS clauses — a third copy of this predicate is exactly the fork hazard the codebase warns about for status sets (`daemon/constants.py:730-756`).
- **E3 (JobItem) is the extra leg the canonical signature lacks.** A `job_queue_items` row is real evidence that a dispatch was attempted and mirrored; a husk has none. The repository's own helpers cannot answer this ("any row, any state"): `JobRepository.get_by_instance` filters `deleted_at IS NULL` and returns only the newest (`daemon/repositories/job_queue/repository.py:543-563`), and `find_jobs_by_instance` filters to `ACTIVE_ADMISSION_STATES` (`:1569-1594`). So the probe must be a raw grouped query — the module's own established pattern (`:1328-1342`). Soft-deleted rows **count as evidence** (a soft-deleted job still proves a dispatch happened).
- **E4 (children)** is free: `manager.list_instances` already attaches `children` per row (`daemon/services/instance_lifecycle.py:5103-5106`). A husk that spawned children is not evidence-free.
- **E5 (question pack)** is a RAM-only lookup (`:254-267`), free, and a pending question is by definition evidence of a live turn.
- **E6 (pause marker)** excludes the two deliberate states: a question pause (marker with `pack_id`) and a **deliberate operator park** (the tombstone `{"deliberate": true, "cleared_at": …}` — written by `mark_deliberate_pause_override`, `daemon/services/question_pause_set.py:612-641`, and persisting for the instance's life). The tombstone is precisely the "this instance is intentionally parked" signal; a husk must not carry it.

### 3.4 `mission_ref`: the leg is structurally vacuous — and that is a finding, not a defect to fix

There is **no per-instance mission projection** in the tree. The probe was deleted rather than wired (M1, `daemon/tools/instance_read.py:772-774`), the payload key is kept for shape with a constant `None` (`:810-812`), and the only mission linkage is *work-side* — a job/work row keyed projection (`daemon/services/mission_resolver.py:1086-1128`). Therefore a "no mission_ref" leg is **constant-true**: it can never be false, so including it in the conjunction would be decorative and — worse — would look like evidence coverage that does not exist.

**Decision:** the husk predicate does **not** carry a `mission_ref` leg. It is recorded here as a **documented gap**: a row that a mission *intended* to use but never dispatched is still a husk, and the detector cannot tell the difference, because the distinction does not exist in the data model. If a mission projection is ever built, this leg must be added then — and this section is the anchor for that follow-up.

### 3.5 Why it fires on BOTH known targets

Walk each target through the predicate. Both are 12–16-day-old zombie missions (`5ba21b2d-43b7-4378-be53-cca781f9c124`, `7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f`); the facts below are what the design *requires* of them, and the tester must assert each one against the live-shaped fixture:

| Leg | Requirement on the fixture | Why it holds for a spawned-but-never-dispatched row |
|---|---|---|
| 1 non-terminal | `status ∉ {completed, terminated, error, failed}` | a never-dispatched spawn row is created with the **default** `status='idle'` (`daemon/repositories/instance/models.py:62`; `repository.py:403-431`) |
| 2 no activity ever | `last_activity_at IS NULL` | the column is stamped only by dispatch/turn/revive writers (see §3.1 leg 2); a row that never had a turn never had one |
| 3 age | `age > 86 400 s` | the targets are 12–16 days old (1 036 800 s … 1 382 400 s) — 12×–16× the threshold |
| E1 no task rows | zero `task` rows | a dispatch never landed, so no task was ever created for the instance |
| E2 no messages | zero `message_queue` rows | same |
| E3 no JobItem | zero `job_queue_items` rows | no dispatch → no job mirror |
| E4 no children | zero rows with `parent_id = target` | never dispatched → never spawned further |
| E5 no question pack | no RAM pack | no turn ever asked a question |
| E6 no pause marker | `metadata["question_paused"]` absent | never paused |

**Critically — the detector alone is NOT sufficient.** The health scan reads `manager.list_instances(limit=1000, offset=0)` (`:1066-1068`) over a fleet of 1730 rows ordered `created_at DESC` (`:1158-1174`); a 12–16-day-old row is in the oldest 730, i.e. **outside the window**. F3 therefore has a hard dependency on **F4's full-age-range scan** (§5). If F4 slips, the detector is correct and still blind. The plan must sequence F4 before or with F3, and the pin for F3 must be written against a fleet fixture **larger than the scan window** so the dependency cannot silently regress.

### 3.6 Integration into `_build_health_report`

- **Position:** appended as the **seventh and last** detector, after `failed-with-unreported-result` (`:1271-1298`), before the `return detectors, bool(truncated)` (`:1300`). Appending (not inserting) keeps the six existing detector indices stable for any consumer that reads them positionally and keeps the diff a pure addition.
- **Detector name:** `"never-dispatched-husk"`. It matches the house vocabulary — the codebase already calls this shape a "never-dispatched ghost" (`daemon/repositories/instance/repository.py:740`, `daemon/services/child_reports.py:2867`) and a "never-dispatched ghost child" (`daemon/services/instance_lifecycle.py:3625`) — and the existing detector names are kebab-case class names (`stale-running`, `waiting-on-a-dead-parent`, `question-paused-without-task-row`, `zombie-pending`, `orphans`, `failed-with-unreported-result`). The commission calls the class "husk"; the detector name keeps the house term as its head and the commission's term as its modifier. **Flag name:** `never-dispatched-husk` (the `detector` string). A separate boolean `husk` flag is **not** added to census rows — see §4.4.
- **`disagreement_pin`** (the house convention: name the observable counterpart, or declare an honest gap — see `:1000-1008`):

```python
"disagreement_pin": (
    "ChildReportsService._ghost_child_filter / "
    "InstanceRepository.filter_never_dispatched_ids "
    "(never-dispatched evidence signature — zero message_queue rows, "
    "zero task rows); the husk set is the NARROWER subset: it adds the "
    "no-JobItem, no-children, non-terminal, null-last_activity_at and "
    "age > DEFAULT_HUSK_MIN_AGE_SECONDS legs"
),
```

The pin is **directional and enforceable**: every husk-flagged row must satisfy the canonical *evidence* legs (no message row, no task row). Because the husk predicate narrows the canonical signature rather than widening it, the pin can never be violated by a husk row; the test asserts the containment, not an equality (an equality is impossible — the canonical signature has no age leg and does not look at JobItems, children, or `last_activity_at`).

- **How `idle_seconds` is reported for a husk:** as the **age since `created_at`** (`age_seconds`), *not* `None`. Rationale: `idle_seconds` means "how long since this instance last did anything". For a husk, "anything" never happened, so the truthful value of "how long has it been doing nothing" is "how long has it existed" — and `None` would render the row as un-interpretable (it is exactly the `None` that made the current detectors skip these rows). The row's `detail` string states the mechanism explicitly so no consumer mistakes this for an activity-derived idle:

  ```
  "never dispatched: no activity ever (last_activity_at is null), no task, "
  "no job item, no messages, no children, no question pack, no pause marker; "
  "age {age}s (threshold {threshold}s)"
  ```

  The `_flagged_row` shape (`:1010-1024`) already carries `idle_seconds`, so no shape change is needed — only the value.
- **Cost shape.** The detector must **not** run seven probes per scanned row. Three stages:
  1. **Row-local filter** over the scanned rows (no I/O): non-terminal ∧ `last_activity_at is None` ∧ age > threshold → a small candidate set.
  2. **One batched call** to `repo.filter_never_dispatched_ids(candidate_ids)` → the subset with no task and no message rows (one round trip for the whole set; the repository method is already batch-shaped, `:735-790`).
  3. **One batched grouped query** for JobItems over the surviving ids, plus per-row `children` / question-pack / marker checks (the last two are RAM/dict reads).

  Total: **2 extra round trips per health report**, independent of the fleet size.

### 3.7 False-positive analysis

| Candidate that could match | Verdict | Why |
|---|---|---|
| **A freshly spawned, not-yet-dispatched instance** (the legitimate "spawn then message" ritual) | **Excluded by the threshold** | the ritual window is seconds-to-minutes; `ORPHAN_MIN_AGE_SECONDS = 60` (`daemon/services/mission_live_guard.py:294`) was calibrated as the floor for it, and the husk threshold is 1 440× that. A row that has waited 24 h without a message is not mid-ritual. |
| **A pinned instance** | **Not excluded — and should not be** | `pinned` is a UI preference (`InstanceUiPrefs`, joined at `daemon/repositories/instance/repository.py:1159-1174`), orthogonal to evidence. A pinned row with no evidence is still a husk; pinning is not a health signal. |
| **An instance between turns with genuinely null activity** | **Cannot occur** | `last_activity_at` is written by every dispatch/turn/revive path (`instance_messaging.py:2455`, `job_feedback_observer.py:3632`/`:4510`, `error_reporting.py:245`, `child_reports.py:1065`/`:4453`, `manager.py:9832`) and by **no other writer** — `repository.update()` and `transition_status_if()` set `updated_at` only (`:1466-1479`, `:1502-1517`). `NULL` therefore means "no turn activity has ever been recorded", not "the last turn was a while ago". A row that *has* run a turn has a non-NULL stamp and is excluded by leg 2. |
| **A deliberately parked instance** | **Excluded (E6)** | the tombstone `{"deliberate": true, "cleared_at": …}` persists in `metadata["question_paused"]` (`daemon/services/question_pause_set.py:612-641`) |
| **A question-paused instance** | **Excluded (E5/E6)** | a pending pack is a RAM hit, and the marker carries `pack_id` |
| **A live-but-waiting instance under a live parent** (`idle`, no work yet, parent alive) | **Excluded by the threshold** unless it is >24 h old — and then it is a *true* husk | the legitimate variant is the strand-healer's protected class ("never terminalize a never-dispatched row under a LIVE direct parent — the legitimate spawn-then-message ritual", `daemon/services/mission_strand_healer.py:54-56`); a 24 h-old one has exceeded every plausible ritual |
| **A husk with a soft-deleted JobItem** | **Excluded (E3)** | a soft-deleted job row still proves a dispatch was mirrored |
| **A revived instance** | **Excluded (leg 2)** | revival writes `last_activity_at = now_utc_naive()` (`daemon/manager.py:9832`) |

**Residual false-positive surface (declared, not closed):** a spawn that is *intentionally* held for more than 24 h before its first message (e.g. a queued work item awaiting a window). No such pattern exists in the current codebase — the spawn path enqueues nothing on its own (`daemon/services/instance_lifecycle.py:2330-2387` writes only metadata), so the first message always comes from the parent — but a future "spawn now, dispatch on a schedule" pattern would need to raise the threshold or stamp a dispatch-intent artifact. This is recorded in the plan's risk register, not solved here.

---

## 4. F2 — CENSUS DESIGN (full-fleet counts + honest reconciliation)

### 4.1 Option A vs Option B

| | **Option A — page the summary over the full fleet** | **Option B — one grouped SQL count in the repository** |
|---|---|---|
| Shape | loop `manager.list_instances` windows until `total` covered, count in Python | one `SELECT status, agent_id, COUNT(*) … GROUP BY status, agent_id` |
| Round trips at 1 730 rows | 2 windows | 1 |
| Round trips at 10 000 rows | 10 windows | 1 |
| Child lookups | 1 730 / 10 000 extra queries (the N+1 at `daemon/services/instance_lifecycle.py:5103-5106` is paid per row) | **0** (the census never needs children) |
| Shipped-code surface | none (tool-only) | one new read-only repository method |
| Drift risk | low — it reuses the same source `instance_list` uses | **the KB-exclusion predicate must be byte-identical** to `repo.list`'s (`daemon/repositories/instance/repository.py:1141-1143`) |

**Recommendation: Option B**, with the drift risk closed by construction — the new method lives in the **same repository class**, uses the **same `KB_AGENT_IDS` constant** (`daemon/repositories/instance/repository.py:33`) and the **same `exclude_kb` flag**, and therefore cannot diverge from `repo.list`'s WHERE clause without failing a shared pin. Rationale beyond round trips: the census is a **count** surface — it does not need the per-row `children` enrichment at all, so Option B deletes the census's share of the N+1 entirely, and it makes the census **fleet-size independent** (a census that silently degrades at 1 000 rows is the defect that created F2 in the first place).

### 4.2 The new repository method

```python
# daemon/repositories/instance/repository.py — new read-only method.
def count_fleet_by_status_and_agent(self, *, exclude_kb: bool = True) -> list[tuple[str, str, int]]:
    """SELECT status, agent_id, COUNT(*) … GROUP BY status, agent_id.

    One round trip; cardinality ≤ |statuses| × |agents| (10 × ~20).
    The KB exclusion mirrors ``list()`` exactly (same constant, same flag).
    """
```

The tool then marginalizes the grouped rows:

- `counts_by_status[status] = Σ count` over agents.
- `counts_by_agent[agent] = Σ count` over statuses.
- `counts_by_liveness[liveness] = Σ counts_by_status[status]` for each status, using the canonical map `_liveness_for` (`daemon/tools/instance_read.py:185-195`) — **no extra query**; liveness is a pure function of status.

### 4.3 The `idle` bucket — confirmed as a *consequence*, not a new concept

`InstanceStatus` has ten members — `idle`, `running`, `waiting`, `paused`, `completed`, `error`, `terminated`, `queued`, `waiting_children`, `failed` (`daemon/repositories/instance/models.py:20-31`) — and `InstanceStatus.IDLE` is the **default** status a created row carries (`models.py:62`; `repository.py:403-411`, `:431-442`). A `GROUP BY status` over the full fleet therefore emits an `idle` key **naturally**; there is no "idle bucket" to add and no bucket vocabulary to introduce. The current census has no `idle` key not because `idle` is missing from the vocabulary but because the newest-1000 window happened to contain few idle rows while the invisible oldest tail is largely idle (the husk class among it). Full-range counting *is* the idle-bucket fix.

**Honest caveat for the plan author:** the exact composition of the invisible tail is a *measurement*, not a design output. The design guarantees that the breakdown accounts for **every** row; it does not assert how many of the 730 are `idle` versus `completed`. The test must assert **coverage** (sum == total), not a specific composition.

### 4.4 Totals reconciliation — exact fields

The census payload gains one block (and keeps every existing key):

```json
{
  "counts_by_agent":   { "...": 123 },
  "counts_by_status":  { "idle": 640, "completed": 512, "...": 0 },
  "counts_by_liveness":{ "...": 123 },
  "queue_depth":  [ … ],
  "worker_pool":  { … },
  "dlq_depth":    0,
  "dead_jobs_total": 5,
  "audit_pending": { … },
  "totals": {
    "total_rows": 1730,
    "counted_rows": 1730,
    "scan_truncated": false,
    "reconciled": true,
    "note": "counts_by_status is computed over the FULL fleet with one GROUP BY query (no scan window). total_rows is the COUNT(*) over the same WHERE (KB agents excluded) — the same number instance_list reports as total_matching. counted_rows is the sum of counts_by_status. reconciled = (counted_rows == total_rows)."
  },
  "generated_at": "…"
}
```

- `total_rows` — the `COUNT(*)` over the census's WHERE. The census must obtain it **independently** of the GROUP BY sum (a second, cheap `func.count()` in the same repository class, mirroring `repo.list`'s count statement at `:1133-1153`), because the whole point of the defect is that two computations of the same number disagreed. Two independent computations + an explicit equality flag is the reconciliation.
- `counted_rows` — `sum(counts_by_status.values())`.
- `reconciled` — `counted_rows == total_rows`.
- `scan_truncated` — **`false` by construction** after this fix (the census no longer reads a bounded window). The key **stays** (shape stability for consumers and for the `_full_doc_` contract at `:960-987`); its value becomes truthful rather than constant-true-by-luck.
- The rule the plan must encode: **`counts_by_status` sums to the same number `instance_list` reports as `total_matching`**, because both are computed over the same WHERE (KB agents excluded, no status/project filter — `manager.list_instances` defaults `exclude_kb=True`, `daemon/manager.py:13520-13534`, and `instance_list` passes no status filter unless the caller supplies one, `daemon/tools/instance_read.py:504-510`). With a `status` filter, `instance_list.total_matching` is the *filtered* count — the census (unfiltered) must not be compared to a filtered page; the note must say so.

**Non-goal (explicit):** the census row shape (`_census_row`, `:351-394`) is **not** changed by this patch. In particular the `stale` field keeps its current meaning ("no activity for longer than the threshold", which is **undefined** without an activity stamp — `idle_seconds is None` ⇒ `stale = False`, `:374`). A husk therefore still renders as `stale: false` in the *census*; the husk class is surfaced by `instance_health`, which is the tool whose stated job is "what looks wedged" (`:903-906`). Adding a `never_active` census field is a reasonable follow-up but is **not** in this patch's scope; if the commission wants the census to carry the class too, that is a separate decision with its own pin.

---

## 5. F4 — PAGINATION FLAGS + FULL-AGE-RANGE SCAN

### 5.1 Honest `truncated` semantics

The repository's flat path returns a **constant** `False` (`daemon/repositories/instance/repository.py:1176-1177`; the docstring at `:1117-1119` says so explicitly: "In `include_descendants=False` mode the total reflects all matching instances and `truncated` is always False"). The `instance_read` surface never passes `include_descendants=True` (`:504-510`, `:1066-1068`), so `truncated` is constant-false across the entire surface and `scan_truncated` (`:899`, `:1414`) is **dead code**.

**The formula:**

```python
# total   = repo.list's COUNT(*) over the same WHERE (all matching rows, NOT
#           the remaining-after-offset count)
# offset  = the applied (clamped) offset
# rows    = the rows actually returned in this page
truncated = bool(total > offset + len(rows))
```

`total` counts **all** matching rows (not the tail), so `offset + len(rows)` is the number of matching rows consumed so far, and the comparison is exact. When `len(rows) == 0` the flag is `True` ONLY when `total > offset`; the exhausted case (`len(rows) == 0 ∧ total ≤ offset`) reads `false` (iteration-003 remedy 4).

**Where each flag lives:**

| Flag | Site | Value after the fix |
|---|---|---|
| `instance_list.truncated` | payload `:543` | `bool(total_matching > applied_offset + len(census))` |
| `instance_health.scan_truncated` | payload `:899` | `True` iff the scan loop stopped before covering `total` (i.e. the window cap fired) |
| `fleet_capacity_summary.scan_truncated` | payload `:1414` | `False` after the F2 fix (the census reads the whole fleet in one grouped query) |

The payload-level `filters_applied.offset` (`:548`) already exposes the offset, so the flag is re-derivable by the consumer — the formula is verifiable from the payload alone, which is what makes it a *pin-able* contract.

### 5.2 Full-age-range scan — Option A (offset-window loop) vs Option B (repo-level full scan)

| | **Option A — offset-window loop over `manager.list_instances`** | **Option B — new repo-level "full fleet" method** |
|---|---|---|
| Mechanism | `for offset in range(0, total, FLEET_SCAN_LIMIT): rows, total, _ = manager.list_instances(limit=FLEET_SCAN_LIMIT, offset=offset)` | one repo call returning every matching row |
| Shipped surface | tool-only | new repository method + a new manager/lifecycle method (the dict conversion and the children enrichment live in `daemon/services/instance_lifecycle.py:5101-5106`, i.e. the manager is the only dict-shaped fleet source) |
| Round trips @1 730 | 2 | 1 |
| Round trips @10 000 | 10 | 1 |
| Child lookups | 1 730 / 10 000 (the pre-existing N+1 — unchanged by either option) | identical |
| Memory | bounded per window | the whole fleet materialized as dicts |
| Truncation expressible | yes — the window cap is exactly `scan_truncated` | awkward (no cap ⇒ unbounded; a cap would reintroduce the same flag) |
| Determinism | stable — the flat ORDER BY ends in `instance_id ASC` (a unique tiebreaker, `:1158-1174`), so offset paging neither skips nor duplicates rows | n/a |

**Recommendation: Option A (offset-window loop).** Rationale: (i) it reuses the existing public API and therefore ships **no new repository surface** — the right blast radius for a defect-fix patch; (ii) it keeps memory bounded per window, whereas Option B materializes the entire fleet as Python dicts (10 k+ rows with a children list each) inside a tool call; (iii) its truncation semantics fall out of the loop bound and map directly onto the existing `scan_truncated` flag; (iv) the cost difference between the options is dominated by the pre-existing children N+1, which **neither** option changes — so the extra repository surface of Option B buys nothing measurable.

**Loop shape (tool-side, no repository change):**

```python
FLEET_SCAN_LIMIT = 1000            # unchanged (:129-133) — now the WINDOW size
FLEET_SCAN_MAX_WINDOWS = 20        # new — hard cap ⇒ ≤ 20 000 rows per report

windows = []
total = None
for i in range(FLEET_SCAN_MAX_WINDOWS):
    rows, total, _ = manager.list_instances(limit=FLEET_SCAN_LIMIT, offset=i * FLEET_SCAN_LIMIT)
    windows.extend(rows)
    if len(windows) >= (total or 0) or not rows:
        break
scan_truncated = bool(total is not None and len(windows) < total)
```

The `_total` that the current code **discards** at `:1066` and `:1305` is exactly the signal needed — the honest `total > len(rows)` test is already in hand.

### 5.3 The pinned-tier / ordering concern — resolved

The flat path's ORDER BY is fixed: pinned tier DESC → `pinned_at` DESC NULLS LAST → `created_at` DESC → `instance_id` ASC (`daemon/repositories/instance/repository.py:1158-1174`), and the `order` parameter ("pinned" | "activity") is **ignored on the flat path** — the docstring says so verbatim: "Root-page ordering (root-based pagination ONLY — the flat path and all descendant loading ignore it)" (`:1092-1094`), and the implementation hardcodes the ORDER BY above. `manager.list_instances` with `include_descendants=True` would change the pagination *semantics* (root pagination + BFS descendant loading, `:5021-5106`) and is therefore **not usable** for a fleet scan.

**Consequences:**

1. **A full cover is order-independent.** Pinned rows float to the front of the order, but a full offset walk eventually reads every row regardless of tier. So the husk class **is** covered — provided the loop is not cut short.
2. **A partial cover starves the oldest rows.** If the window cap fires, the rows still unread are at the **tail** of a `created_at DESC` order — i.e. exactly the day-scale husks. Therefore `scan_truncated=True` must be read as *"the oldest rows are not covered"*, and the cap must be generous (20 windows = 20 000 rows). This is the precise statement of the residual blind spot, and it is the reason `scan_truncated` must stay **loud** rather than be smoothed away.
3. **No neutralization of the pinned tier is required** for the full cover, and none is proposed: introducing an un-pinned ordering would mean a new repository sort tier (new shipped surface, new drift surface) to solve a problem that a complete walk already solves.

---

## 6. F8 — READ-PATH PURITY DESIGN

### 6.1 What actually happens today

```
instance_get(id)
  └─ _resolve_target(manager, id)                      instance_read.py:334-348
       └─ _resolve_instance_id(manager, instance_id)    daemon/tools/instance.py:1451-1477
            └─ await manager.get_instance(instance_id)  instance.py:1471   ← THE SEAM
                 └─ InstanceLifecycleService.get_instance  instance_lifecycle.py:4478-4513
                      ├─ cache hit (instance in manager.instances) → returns the graph, NO side effect
                      └─ COLD path:
                           ├─ await manager.ensure_mcp_preloaded(instance_id)   :4505
                           │    └─ mcp_service.preload_mcp_tools                mcp_service.py:630-826
                           │         └─ per server: get_schemas_for_server      :236-319
                           │              ├─ cache key = server.name (GLOBAL)   :258-262
                           │              ├─ EMPTY result NEVER cached        :286-293
                           │              └─ 30 s throttle only                :294-305, :53
                           │         ⇒ EXTERNAL DISCOVERY against a dead server,
                           │           re-run on every cold read > 30 s apart
                           ├─ instance_repository.get(instance_id)                :4510
                           └─ await self._restore_instance(instance_id, meta)    :4513
                                └─ version re-elevation WRITES the row
                                   (update agent_tag/agent_dir + delete_metadata)
                                                                          instance_lifecycle.py:4671-4688
```

Two consequences the maintainer needs to know:

- **The side effect only bites on the COLD path.** A live instance is in `manager.instances`, so `get_instance` returns the cached graph and hydrates nothing. The population that *does* get hydrated — and that generates external discovery traffic against dead instances — is exactly the **cold/dead population**: husks, ghosts, and completed rows. A read of the husk class is what triggered the observed uncached `plane` retry.
- **`instance_get` does not need the graph at all.** After resolution it reads the row itself (`repo.get`, `:637`), the children (`:654-656`), the tree (`:660-662`), the ancestors (`:672-695`), the task rows (`:707-710`), the checkpoint (`:728-744`) and the question pack (`:750-758`). None of that requires a compiled graph or an MCP preload.

### 6.2 The read-scoped resolver (recommended)

```python
async def _resolve_target_readonly(manager: Any, instance_id: str) -> str:
    """Resolve an instance id WITHOUT hydrating the instance.

    Repo-only exact match, then the existing fuzzy matcher. Never calls
    manager.get_instance — so a read never triggers MCP schema discovery
    (mcp_service.get_schemas_for_server) and never takes the
    _restore_instance path that can WRITE the instances row
    (instance_lifecycle.py:4671-4688).
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        raise ValueError("ERROR: instance repository unavailable")

    # exact match — repository read only
    if repo.get(instance_id) is not None:
        return instance_id

    # WARNING parity: the current path logs a loud WARNING when the exact
    # lookup misses (daemon/tools/instance.py:1487-1497) because that
    # failure is otherwise silent. Keep it.
    logger.warning("instance resolution failed (exact repo lookup miss): ...")

    near_matches = manager.find_near_instance(instance_id, max_distance=DEFAULT_FUZZY_MATCH_DISTANCE)
    if len(near_matches) == 1:
        return near_matches[0]
    if len(near_matches) > 1:
        raise ValueError("... Multiple similar instances found: ...")
    raise ValueError(f"ERROR: instance '{instance_id}' not found.")
```

Facts that make this safe:

- `manager.find_near_instance` is **already repo-only**: it calls `self._instance_repository.list(limit=50, offset=0)` and computes edit distance in Python (`daemon/manager.py:13817-13836`) — no hydration, no MCP, no write. So the fuzzy leg needs no change.
- The refusal surface is unchanged: `instance_get` maps `ValueError` to `_refusal("INSTANCE_GET", "instance-not-found", str(exc))` (`:626-627`), and the ambiguity/no-match message shapes are preserved verbatim so the operator-facing text does not change.
- `DEFAULT_FUZZY_MATCH_DISTANCE` is imported from the same module the current path uses (`daemon/tools/instance.py`), so the fuzzy radius is identical.

### 6.3 The `_restore_instance` DB-write residual — DECISION

**Decision: eliminate it, do not document it away.** The read path will use the read-scoped resolver and will therefore never enter `_restore_instance`, so the re-elevation write (`instance_lifecycle.py:4671-4688`) cannot be triggered by a read.

Justification, and the honest accounting of what is lost:

- **What breaks if `manager.get_instance` is not called:** (a) **cache warming** — a cold instance stays cold; the next real operation on it pays the restore cost then. This is *desirable* for a read: warming the cache means compiling a graph and preloading MCP schemas for an instance nobody asked to run. (b) **tag re-elevation** — the re-elevation write is deferred to the next genuine restore instead of happening because somebody read a row. Deferring is correct: a read must not promote an agent version. (c) Nothing else: `instance_get`'s outputs are all repo/RAM/checkpoint reads.
- **Why not "scope it out with documentation":** the docstring already claims "Side effects: NONE. Read-only; no audit row." (`:843-844`). Leaving a known write behind that claim is precisely the kind of drift this codebase's own conventions forbid (see the status-set fork warnings, `daemon/constants.py:730-756`). Making the claim true is cheaper than qualifying it.
- **Residual to disclose:** after the fix the read path issues **SELECTs only** against `instances`, `task`, `job_queue_items`, `message_queue`, `dead_letter_items`, `instance_ops_audit` and the checkpoint store; it performs **no** MCP schema discovery; and it does **not** warm the instance cache. The last item is the only behavioural change for callers and is stated in the `_full_doc_`.

### 6.4 The docstring duty

Every one of the four `_full_doc_` blocks currently ends with a "Side effects:" line (`:594-595`, `:843-844`, `:934-935`, `:982-983`). After the fix:

- `instance_list` / `instance_health` / `fleet_capacity_summary`: "Side effects: NONE. Read-only; no audit row." — **becomes true** (the census/health/capacity paths issue SELECTs only; note `fleet_capacity_summary` also touches `pool.get_stats()` which is in-memory, `:1362-1378`).
- `instance_get`: the line stays, and gains one sentence: *"A read never triggers MCP schema discovery and never restores the instance into memory — a cold instance stays cold."*
- The category doc (`:84-109`) says "These tools READ. They never change anything, never write an audit row, and never trigger a repair." — keep, and it is now accurate.

---

## 7. F5 — MEASURE SEMANTICS (recommend: document + one derived canonical field)

### 7.1 The two measures, precisely

| Field | Source | Table | Write path | Lifetime |
|---|---|---|---|---|
| `dlq_depth` | `get_dead_letter_service().count_dlq()` (`:1380-1387` → `daemon/services/dead_letter_service.py:575-592` → `daemon/repositories/job_queue/dead_letter_repository.py:183-205`) | `dead_letter_items` | **only** `move_to_dlq` / `move_to_dlq_standalone` (`daemon/services/job_queue_service.py:3235`, `:5151`; `daemon/services/job_retry_engine.py:437`) | pruned by `cleanup_by_age` (`dead_letter_repository.py:152-181`) |
| per-queue `dead` | grouped `SUM(CASE WHEN admission_state='dead' …)` over `job_queue_items` (`:1328-1342`) | `job_queue_items` | the job row's **canonical** terminal admission state | the job row persists (soft-delete only) |

A dead job **without** a DLQ snapshot is reachable: `atomic_transition(..., to_status="dead_letter")` maps the legacy status to `AdmissionState.DEAD` (`daemon/repositories/job_queue/repository.py:36`) and writes no `dead_letter_items` row — the drift-recovery path calls exactly that and calls it a "structurally silent terminal write" (`daemon/services/job_recovery_service.py:4076-4086`). So `dlq_depth = 0` with `dead = 5` is **two true facts about two different tables**, not a contradiction.

### 7.2 Recommendation: DOCUMENT (not unify), with one derived field

**Do not unify** (i.e. do not redefine `dlq_depth` to be the admission-state count). Reasons: (a) `dlq_depth` is a **shipped payload field** with a documented meaning in the `_full_doc_` (`:970-974`) and in the prior feature's planning set — silently redefining it changes a public contract under the same name; (b) the DLQ snapshot table is the **only** place carrying the failure context (`reason`, `error_message`, the message snapshot, `daemon/services/dead_letter_service.py:283-300`) — its count answers "how many dead jobs still have their failure record on file", which is **not** redundant with "how many jobs are dead"; (c) unifying would duplicate a number the payload already exposes (the per-queue `dead` rows).

**Do this instead** (the patch's F5 scope):

1. **Keep** `dlq_depth` with its current value and its current key.
2. **Add** one derived field, computed from data already in hand (zero extra queries):
   ```python
   "dead_jobs_total": sum(int(r["dead"]) for r in queue_depth),   # canonical measure
   ```
3. **Rewrite the docstring wording** for both fields in `fleet_capacity_summary._full_doc_` (`:960-987`) and in `CATEGORY_DOC` (`:84-109`) so the difference is explicit:

   > `dead_jobs_total` — the canonical count of jobs in the terminal `dead`
   > admission state (summed from the per-queue `dead` column). `dlq_depth` is
   > a **different** measure: the row count of the legacy `dead_letter_items`
   > snapshot table, written only when a job is moved to the dead-letter queue
   > through the DLQ service and pruned by the retention sweep. A dead job
   > can exist with **no** snapshot row (the drift-recovery path finalizes to
   > `dead` directly), so `dlq_depth ≤ dead_jobs_total` is expected and
   > `dlq_depth = 0` with `dead_jobs_total > 0` is **not** an error.

4. **Disclose the risk** in the payload itself (a one-line `note` next to the two fields) so an operator reading raw JSON does not have to consult the docstring:

   ```python
   "dlq_note": ("dlq_depth counts dead_letter_items snapshot rows "
                "(DLQ-service writes only, retention-pruned); "
                "dead_jobs_total counts job_queue_items in the 'dead' "
                "admission state (canonical). They measure different tables."),
   ```

**Cost:** zero extra queries (the per-queue rows are already computed at `:1321-1351`). **Shape:** two new keys (`dead_jobs_total`, `dlq_note`) — additive, no removals, no renames.

---

## 8. F1 — ALIAS DESIGN (`instance_fleet_summary` → alias of the live `fleet_capacity_summary`)

### 8.1 Mechanics

Inside `create_instance_read_tools` (the same factory, `daemon/tools/instance_read.py:397-420`), add a **second `@tool` closure** that delegates to the same builder:

```python
    # ── instance_fleet_summary (ALIAS of fleet_capacity_summary) ───────────
    # The ratified rename (architecture-recommendation.md:59) shipped as
    # ``fleet_capacity_summary``; the pre-rename planning rows still say
    # ``instance_fleet_summary``. Both names resolve. The LIVE name is
    # untouched — this is an ADDITIVE alias, never a rename.

    class InstanceFleetSummaryInput(BaseModel):
        """Input schema for instance_fleet_summary (no arguments)."""

    @register_tool_category("instance_read")
    @tool(args_schema=InstanceFleetSummaryInput)
    async def instance_fleet_summary() -> str:
        """Alias of fleet_capacity_summary — same payload. Use tool_help("instance_fleet_summary")."""
        if not _is_instance_read_authorized(caller_agent_id):
            return _error("instance_read operation not permitted for this agent")
        try:
            payload = await asyncio.to_thread(_build_capacity_summary, manager)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_fleet_summary failed")
            return _error(f"instance_fleet_summary failed: {type(exc).__name__}")
        return json.dumps(payload, default=str)

    instance_fleet_summary._full_doc_ = """Alias of `fleet_capacity_summary` …
```

and return **five** tools from the factory (`:989-994`) instead of four.

| Requirement | Where it is satisfied |
|---|---|
| **Name** | the Python function name `instance_fleet_summary` (the `@tool` decorator derives the tool name from it — the registry's name is the function name, see `_tool_registry.py:818-873`) |
| **Category** | the same `@register_tool_category("instance_read")` decorator — which sets `_tool_category` **and** the first-party provenance marker `_tool_category_first_party` (`:529-554`). The marker matters: `scan_tools_for_full_docs` honors a category override on rescan only for first-party tools (`:846-852`), so a non-first-party entry could be re-categorized by a spoofing tool list. Using the decorator is mandatory, not cosmetic. |
| **Auth gate** | the same `_is_instance_read_authorized(caller_agent_id)` body check — the category alone cannot spell the audience (a literal tool name in an allow list bypasses the category gate; see the module docstring `:50-59`). |
| **Delegation** | `_build_capacity_summary(manager)` — the **same** builder the live tool uses, so the two payloads cannot drift. No second implementation. |
| **Doc** | its own `instance_fleet_summary._full_doc_` — `scan_tools_for_full_docs` keys the full doc by the tool **name** (`:818-873`, key at `:828`), so without its own `_full_doc_` the alias would show up in `tool_help` with an empty body. |
| **Registry lists** | **both** `DYNAMIC_TOOL_NAMES` (`daemon/tools/_tool_registry.py:100-109`) and `KNOWN_TOOL_NAMES` (`:1167-1176`) — the drift pin `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift` (`:223-242`) compares `KNOWN_TOOL_NAMES` **bidirectionally** against the source-discovered universe; a new `@tool` name that is not in the static set fails the pin. |
| **Construction** | no change needed at `daemon/tools/instance.py:6058` — the factory returns both tools and the existing `tools.extend(create_instance_read_tools(...))` picks both up. (The extend **must** stay before `create_help_tool` / `scan_tools_for_full_docs` / `_apply_tool_filter` — the placement comment at `daemon/tools/instance.py:6030-6057` — but the extend itself is unchanged.) |
| **Agent config** | none needed: `agents/maintenancer/meta.json:11-21` allows the **category** `instance_read`, not tool names, so the alias is available to the same audience as the live tool, with no agent-file edit. |
| **Live name** | `fleet_capacity_summary` is **untouched** — same function, same decorator, same payload, same doc. Nothing about the live tool changes. |

### 8.2 The doc correction

- `architecture-recommendation.md:37` and `:57` are **stale pre-rename rows** (they name `instance_fleet_summary` as if it were the shipped surface). The fix is an **annotation, not a rewrite** — those rows are the historical Q1 record, and the ratified rename is already recorded at `:59` and `:268`, `decisions.md:121`, `plan-overview.md:401`/`:564`. Annotate rows `:37` and `:57` with a pointer to the rename verdict (`:59`) so a reader of the historical table is not misled.
- `docs/` contains **zero** references to either name (verified by grep over `docs/`), so there is no operational doc to fix.
- The live `_full_doc_` for `fleet_capacity_summary` (`:960-987`) should mention the alias ("`instance_fleet_summary` is an alias of this tool; both return the same payload"), and the alias's `_full_doc_` should say it is an alias of the live name. This is the "mention of the alias" the commission asks for.

### 8.3 What the alias does **not** do

It does not introduce a generic alias mechanism (none exists under `daemon/tools/`), it does not rename anything, and it does not create a second code path — the payload comes from one builder. If a future commission wants a general alias framework, that is a separate design.

---

## 9. TEST / E2E STRATEGY INPUT (for the plan author)

### 9.1 Environment rules (binding)

- **Real PostgreSQL only.** A fresh-SQLite boot is blocked by the pre-existing migration prefix defect `20260714_000001` (the runner is SQLite-only and the PG path no-ops; the runner skips it on SQLite and the PG path never hits it — the defect is the SQLite-specific ALTER, which fails a fresh SQLite boot). The runner is a no-op on PG, so PG boots are unaffected.
- **Throwaway role + database**, `DROP`ped at cleanup. **Never** touch `ensemble_prod`, and never touch the live daemon on `:9797`.
- **Scrub `POSTGRES_*` per-variable.** The `env -u POSTGRES_*` idiom is a silent NO-OP (the shell does not glob env names) — an ambient `POSTGRES_DB=ensemble_prod` would survive it and a "fenced" run would hit prod. Use the explicit per-variable form: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>`.
- Assert the import resolves inside the worktree before trusting any output (`PYTHONPATH=<worktree> python -c "import daemon; print(daemon.__file__)"`).

### 9.2 The husk fixtures — real spawn order, then one clock backdate

The fixture rows must be created through the **real spawn path**, not synthesised:

1. Spawn through the production API path so the row is born exactly as production births it: `spawn_instance_with_mcp` (`daemon/manager.py:9361-9430`) → `spawn_instance` (`daemon/services/instance_lifecycle.py:1802+`) → `_spawn_instance_db_sync` (`:2444-2459`). The row lands with `status='idle'`, `version=1`, **`last_activity_at` NULL** (the `Instance(...)` construction at `daemon/repositories/instance/repository.py:431-442` never passes it; the model default is `None`, `models.py:72`).
2. Pass the **exact production ids** so the pin is anchored to the real identifiers:
   `5ba21b2d-43b7-4378-be53-cca781f9c124` and `7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f`. Both are valid UUIDs, so the spawn path accepts them as explicit ids (`_UUID_PATTERN` check, `daemon/services/instance_lifecycle.py:1967-1973`).
3. **Do not dispatch** — no message, no task, no job. This is the point of the fixture.
4. **One** synthetic write: backdate `created_at` (and `updated_at`) by 12 and 16 days respectively, with a single direct `UPDATE` in the fixture transaction. This is the *only* synthetic touch and it is confined to the clock axis — the row's *shape* is 100 % production-real. The alternative (sleeping 12 days) is not available.
5. **Dispatch nothing afterwards** — the fixture must not accidentally give the row evidence.

### 9.3 Per-defect pin shapes and their mutation-verification

| Defect | Pin | Mutation that must turn it RED |
|---|---|---|
| **F3** | The health report's `never-dispatched-husk` detector contains **both** target ids in `instances[]`, with `idle_seconds` = age-since-`created_at` and `detail` naming the null-activity mechanism. | Delete the detector's age leg → the fresh-spawn fixture (below) fires → RED. Or flip the evidence conjunction (e.g. drop the JobItem leg) → the "husk-with-a-job" fixture fires → RED. Or neuter `last_activity_at IS NULL` → the detector misses the targets → RED. |
| **F3 (negative)** | A **fresh** spawn (same shape, `created_at = now`) is **not** flagged. | Raise the age threshold check to `>= 0` → the fresh spawn fires → RED. |
| **F3 (evidence)** | A row that has a `task` row / a `job_queue_items` row / a `message_queue` row / a child / a question pack / a pause marker is **not** flagged (six negative fixtures, one per evidence leg). | Drop one evidence leg → exactly that fixture fires → RED. One behaviour neutered per run (surgical mutations, not whole-file reverts). |
| **F3 (pin direction)** | Every husk-flagged row satisfies the canonical ghost evidence signature (`filter_never_dispatched_ids` / `_ghost_child_filter`). | Change the husk predicate to bypass the canonical filter → RED. |
| **F3 × F4 coupling** | The health report over a fleet **larger than the scan window** still flags the two targets. | Restore the single 1000-row window → the targets fall out of the window → RED. This is the pin that protects the coupling. |
| **F2** | `sum(counts_by_status.values()) == totals.total_rows == totals.counted_rows` and `totals.reconciled is True`; `counts_by_status` contains the `idle` key; `total_rows` equals `instance_list.total_matching` for the same (unfiltered) call. | Revert to the windowed loop → the sum drops to 1000 → RED. Or drop the `exclude_kb` predicate from the new method → the totals diverge from `instance_list` → RED. |
| **F2 (KB parity)** | With a KB-agent row present, the census total and `instance_list.total_matching` both exclude it, and the two agree. | Remove the KB exclusion from one side → divergence → RED. |
| **F4 (flags)** | `instance_list` with `count < total_matching` reports `truncated: true`; with `count == total_matching` reports `truncated: false`; an empty page reports `truncated: true` ONLY when `total > offset` (the exhausted case `len(rows)==0 ∧ total ≤ offset` reads `false` — iteration-003 remedy 4). | Restore the repository's constant `False` (or copy it verbatim again) → RED. |
| **F4 (scan)** | Over a fleet > window size, `instance_health.scan_truncated` is `false` and the old rows are covered. | Restore the single-window scan → `scan_truncated` flips or the old rows vanish → RED. |
| **F4 (cap)** | When the fleet exceeds the window cap, `scan_truncated` is `true` (**not** silently false). | Remove the cap → the tool never truncates but also never terminates on a huge fleet → the pin must assert the flag flips at the cap → RED. |
| **F8** | `instance_get` on a **cold** instance issues **no** MCP discovery (the MCP service's `get_schemas_for_server` is never called — assert with a spy) and performs **no** write (assert the instances row is byte-identical before/after, including `agent_tag`, `agent_dir` and `metadata`). | Restore `manager.get_instance` in the resolver → the spy fires and/or the row changes (if the fixture instance carries `original_agent_tag`, the re-elevation write lands) → RED. |
| **F8 (behaviour)** | `instance_get` still returns the full payload for a cold instance (row fields, children, ancestors, current_task, last_messages, spawned_by) — purity must not cost functionality. | Point the resolver at a stub returning a bare id → payload fields go missing → RED. |
| **F8 (refusal)** | An unknown id still refuses with `reason=instance-not-found`; an ambiguous short id still lists the candidates. | Make the resolver always raise → RED. |
| **F1** | **Both** `fleet_capacity_summary` and `instance_fleet_summary` resolve, return **byte-identical** payloads, and are listed in `tool_help` with a non-empty body. | Remove the alias closure → the alias resolution RED; remove one registry-list entry → the frozen-name drift pin RED; drop the alias `_full_doc_` → the `tool_help` body-empty assertion RED. |
| **F5** | `dlq_depth` and `dead_jobs_total` are both present; `dead_jobs_total == sum(per-queue dead)`; the docstring/`dlq_note` state the difference. | Redefine `dlq_depth` to the admission-state count → the "different tables" doc assertion and the dlq_depth value assertion RED. |
| **Doc** | The `_full_doc_` side-effect lines are accurate: a text assertion that `instance_get`'s doc mentions "never triggers MCP schema discovery" and that `fleet_capacity_summary`'s doc distinguishes `dlq_depth` from `dead_jobs_total`. | Revert the docstrings → RED. |

### 9.4 Mutation discipline

One behaviour neutered per run (surgical mutations), restoring green between runs, and the per-behaviour red sets should partition the full-revert red set. Report the **measured** red set, never a statically derived one. If a pin is anchor-neutral by construction (e.g. a fixture that passes before and after the fix), say so explicitly rather than counting it as coverage.

### 9.5 Regression guard

The whole-tree suite must show **net-new reds = 0** against the base (`b257842c9`), with any pre-existing reds enumerated and attributed. In particular, watch the packs that pin the areas this patch touches: `tests/unit/tools/test_frozen_tool_name_discovery.py` (the F1 registry pin), `tests/unit/test_pause_never_dispatched_ghosts.py` (the ghost-signature lockstep pin — the husk detector must not disturb it), and any pack that asserts the `instance_read` payload shape.

---

## 10. ADJACENT FINDINGS (out of scope — recorded for later routing)

These are **findings, not fixes**. They were discovered while verifying the read-side defects; the commission's scope for this patch is detectors, census, copy, flags, side-effect purity, pinned regressions and doc updates named in the defect list.

| # | Finding | Evidence | Impact |
|---|---|---|---|
| **A1** | **`waiting` is in NEITHER the alive nor the terminal set.** `ALIVE_INSTANCE_STATUSES = {idle, running, paused, queued, waiting_children}` (`daemon/constants.py:760-766`); `TERMINAL_INSTANCE_STATUSES = {completed, terminated, error, failed}` (`:702-707`); but `InstanceStatus.WAITING` exists (`daemon/repositories/instance/models.py:22`). Every "is the parent dead?" test therefore treats a `waiting` parent as DEAD — the `waiting-on-a-dead-parent` detector (`:1117-1119`) and the `orphans` detector (`:1248-1250`) both misclassify it. **False-positive class.** | `daemon/constants.py:695-766`, `daemon/tools/instance_read.py:1117-1119`, `:1248-1250` | A parent that is merely `waiting` (active, no in-flight work) is reported as dead → its children are reported as orphans. Fixing it changes two existing detectors' semantics → its own commission. |
| **A2** | **N+1 child lookup on every fleet scan.** `manager.list_instances` attaches `children` with one query per row: `info["children"] = instance_repository.list_child_ids_permanent(inst.instance_id)` (`daemon/services/instance_lifecycle.py:5103-5106`). At 1 730 rows that is 1 730 extra queries per census/health/capacity call; the F4 full-cover change raises it from 1 000 to the full fleet. | `daemon/services/instance_lifecycle.py:5101-5106` | Latency and DB load of the read surface. **Note the interaction:** the F4 full cover multiplies this. Mitigation inside the patch: none (out of scope) — but the plan should record the cost and consider batching the children lookup in the same window loop. |
| **A3** | **Forked task-terminal constants.** `instance_read.py:1051` and `:713` hard-code `{"completed", "failed", "cancelled"}` while the canonical set is `daemon/constants.py:769-794` (`NON_TERMINAL_TASK_STATUSES` / the task-status vocabulary). | `daemon/tools/instance_read.py:713`, `:1051`; `daemon/constants.py:769-794` | A new task status could be silently treated as terminal by one path and live by another — the exact fork hazard `daemon/constants.py:730-756` warns about. The husk detector should import the canonical set rather than copy it. |
| **A4** | **Duplicate terminal-status tuple.** `daemon/repositories/instance/repository.py:41-46` defines `TERMINAL_INSTANCE_STATUSES` as a *tuple* while `daemon/constants.py:702-707` defines the same membership as a *frozenset*. | both sites | Two homes for one membership — the codebase's own stated anti-pattern. |
| **A5** | **`mission_ref` is permanently null with no instance→work projection.** The probe was deleted (M1) rather than wired (`daemon/tools/instance_read.py:772-774`); the payload key is a constant `None` (`:810-812`); the only mission linkage is work-side (`daemon/services/mission_resolver.py:1086-1128`). | as cited | The instance surface cannot answer "is this instance part of a mission?" — which is why the husk predicate cannot carry a mission leg (§3.4). |

---

## 11. CLASS-LEVEL FINDINGS (findings, not fixes)

### 11.1 The husk class is a NEW shape the existing healers cannot match BY CONSTRUCTION

The six detectors are all evidence-keyed, and the four write-side/repair lanes are all evidence-keyed too. The husk has **no** evidence, so no existing lane can even *see* it:

- The `orphans` detector requires a parent (`parent_id` set, `:1245-1247`) — a **root** husk has none.
- The `waiting-on-a-dead-parent` detector requires `status == 'waiting_children'` (`:1112-1113`) — a husk is `idle`.
- The `zombie-pending` detector requires an `admission_state='active'` job row (`:1190-1196`) — a husk has no job.
- The `question-paused` detector requires `status == 'paused'` and a non-`None` idle (`:1147-1152`) — a husk is `idle` with a `None` idle.
- The strand healer's orphan leg requires a **TERMINAL direct parent** and an `updated_at` older than `ORPHAN_MIN_AGE_SECONDS` (`daemon/services/mission_strand_healer.py:30-40`) — a husk under a **live** parent, or a root husk, does not qualify, and the healer explicitly refuses to touch it: "the healer NEVER terminalizes a never-dispatched row under a LIVE direct parent (the legitimate spawn-then-message ritual)" (`:54-56`).
- The mission-live guard's strand exclusion has the same shape: a never-dispatched row is excluded from liveness-holding **only** when its direct parent is terminal (`daemon/services/mission_live_guard.py:793-816`).

**The spawn path is why this shape exists.** `spawn_instance` creates the row immediately and does nothing else: the row is written by `_spawn_instance_db_sync` (`daemon/services/instance_lifecycle.py:2444-2459`) with `status='idle'` (`daemon/repositories/instance/models.py:62`), `version=1`, `last_activity_at` **NULL** (never passed at `daemon/repositories/instance/repository.py:431-442`), and only metadata (`project_id`, `instance_name`, `invoked_as_tool`, `model_override`, `mcp_tool_names`, `source_type` — `daemon/services/instance_lifecycle.py:2330-2387`). The instance is then registered in memory and an SSE `instance_created` event fires (`:2472-2500`) — but **no message is enqueued**. Dispatch requires a message, and "the automatic dispatcher of a spawned child is its parent" (`daemon/services/mission_live_guard.py:805-807`). So a spawned-but-never-dispatched instance **is** this shape — idle, version 1, null activity, zero evidence — and it is the most likely way the two known targets formed (a parent that died or was abandoned between the spawn and the first message).

### 11.2 Spawn-side attribution is unrecoverable once log rotation passes it

The `instances` row carries `parent_id` (the lineage pointer), the child's own `agent_id`/`agent_dir`/`agent_name`/`agent_tag`, and the metadata listed above — and **no** durable "who requested this spawn" field. A repo-wide grep finds no `spawned_by` / `spawn_source` / `requested_by` column anywhere in `daemon/` (the only `requested_by` is an unrelated resource-router field, `daemon/routers/resources.py:1247`).

What exists today instead:

1. **The parent's row** — `instance_get` derives `spawned_by.agent_id` by walking `parent_id` up (`daemon/tools/instance_read.py:667-701`), and states honestly when it cannot (`"no-parent"`, `"parent-agent-unresolved"`). This is **indirect** (it names the parent's agent, not the requesting caller) and it evaporates when the parent row is deleted.
2. **The spawn log line** — `logger.info("Spawning instance {instance_id} (agent=…, parent=…, name=…, model=…, source=…)")` (`daemon/services/instance_lifecycle.py:2419-2424`). This is the only record naming the spawn context, and it **dies with log rotation**.
3. **The transient SSE `instance_created` event** (`:2490-2500`) — not durable.

So: after log rotation, a husk's spawner is **unrecoverable** from the database. The patch does not add an attribution record (out of scope) — but any future husk-remediation act (terminate / reclaim) will be acting on a row whose origin it cannot prove, and the plan should say so out loud rather than let the act tools imply provenance they do not have.

### 11.3 A spawn-without-dispatch cleanup exists — but only for a STRICT SUBSET, and this patch does NOT build one

**What exists.** The strand healer's orphan leg terminalizes never-dispatched rows, but only rows whose **direct parent is TERMINAL** and whose `updated_at` is older than the race guard (`daemon/services/mission_strand_healer.py:30-40`, `:680-700`; age predicate `strand_past_race_guard`, `daemon/services/mission_live_guard.py:294-319`). It drives the held root through the production completion chain. The mission-live guard's read-side twin excludes the same subset from liveness-holding (`:793-816`).

**What does NOT exist.** Any cleanup for: (a) a never-dispatched row under a **LIVE** parent (explicitly protected — "the legitimate spawn-then-message ritual", `daemon/services/mission_strand_healer.py:54-56`, `daemon/services/mission_live_guard.py:815-817`); (b) a **root** husk (no parent at all); (c) a husk carrying a pause marker or a question pack. These are precisely the rows the new detector reports — and after this patch they will be **reported but not cleaned**.

**Decision (anti-scope-creep, explicit):** the patch **does not** build a spawn-without-dispatch cleanup. Patch scope = the six defect fixes above + pinned regressions + doc corrections. The detector's value is that a human/operator (or a later, separately-commissioned act lane) can finally *see* the class. Any cleanup must make its own safety argument (the ritual race, the live-parent protection, the blast radius of a terminalizing write) and belongs in its own commission.

---

## 12. OPEN QUESTIONS

None blocking. Three items are recorded for the plan author as decisions already taken here but worth a conscious ratification, plus one genuinely open item:

1. **Husk threshold = 24 h** (not 2 h / 6 h). Rationale in §3.2; ratify or override — the choice is a single constant.
2. **Census on a grouped SQL count** (§4.1 Option B) rather than full-fleet paging — this adds one read-only repository method to the shipped surface. Ratify or override; the alternative is tool-only.
3. **Census row shape untouched** (§4.4 non-goal): the husk class is surfaced by `instance_health`, not by the census. If the commission wants the census to carry the class too, that is a scope addition (a `never_active` field) and needs its own pin.
4. **Genuinely open, non-blocking:** the plan author must decide whether the F1 alias should be **two tools** (as designed here: `fleet_capacity_summary` + `instance_fleet_summary`) or **one tool with two names** in the registry — the codebase has no alias mechanism (`daemon/tools/` has none), so the two-tool form is the only one implementable without new registry machinery. Recommendation: two tools, one payload builder.

---

## References

- `daemon/tools/instance_read.py` — the read surface (constants `:115-152`; helpers `:155-295`; resolver `:334-348`; census row `:351-394`; factory `:397-420`; `instance_list` `:462-601`; `instance_get` `:617-848`; `instance_health` `:866-940`; `fleet_capacity_summary` `:946-987`; `_flagged_row` `:1010-1024`; `_has_live_task` `:1043-1052`; `_build_health_report` `:1055-1300`; `_build_capacity_summary` `:1303-1415`).
- `daemon/repositories/instance/repository.py` — `create` `:403-455`; `list_child_ids_permanent` `:262-282`; `list` `:1008-1177` (count `:1133-1153`, KB exclusion `:1141-1143`, ORDER BY `:1158-1174`, constant `truncated=False` `:1176-1177`); `filter_never_dispatched_ids` `:735-790`; `update` `:1422-1479`; `transition_status_if` `:1485-1517`; `KB_AGENT_IDS` `:33`.
- `daemon/repositories/instance/models.py` — `InstanceStatus` `:20-31`; `status` default `:62`; `last_activity_at` `:72`; `created_at` `:74`; `to_dict` `:143`.
- `daemon/services/child_reports.py` — `_ghost_child_filter` `:2864-2901`; consumers `:3336`, `:3913`.
- `daemon/services/instance_lifecycle.py` — `get_instance` `:4478-4513`; `_restore_instance` `:4600+`; re-elevation write `:4640-4695`; `spawn_instance` `:1802+`; metadata assembly `:2330-2387`; spawn log `:2419-2424`; `_spawn_instance_db_sync` call `:2444-2459`; `list_instances` `:5021-5106` (children N+1 `:5103-5106`).
- `daemon/manager.py` — `ensure_mcp_preloaded` `:9288-9359`; `spawn_instance_with_mcp` `:9361-9430`; revival write `:9832`; `list_instances` `:13520-13588`; `find_near_instance` `:13817-13836`.
- `daemon/services/mcp_service.py` — `get_schemas_for_server` `:236-319` (global server-name cache `:258-262`; empty-result-never-cached `:286-293`; 30 s throttle `:53`, `:294-305`); `preload_mcp_tools` `:630-826`.
- `daemon/services/question_pause_set.py` — deliberate-park tombstone `:612-641`; marker read `:660-681`.
- `daemon/services/mission_live_guard.py` — `ORPHAN_MIN_AGE_SECONDS` `:294`; `strand_past_race_guard` `:297-319`; never-dispatched ghost discussion `:793-816`.
- `daemon/services/mission_strand_healer.py` — heal invariant `:30-40`; safety bound `:54-56`; `DEFAULT_RUNNING_ZOMBIE_AGE_BOUND_SECONDS` `:197`.
- `daemon/services/stale_pause_sweep.py` — `DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS` `:126`.
- `daemon/services/job_recovery_service.py` — the "structurally silent terminal write" that finalizes a job to DEAD without a DLQ snapshot `:4076-4086`.
- `daemon/repositories/job_queue/repository.py` — `_LEGACY_TO_ADMISSION` `:36`; `get_by_instance` `:543-563`; `get_active_by_instance` `:565+`; `find_jobs_by_instance` `:1569-1594`.
- `daemon/repositories/job_queue/dead_letter_repository.py` — `cleanup_by_age` `:152-181`; `count` `:183-205`.
- `daemon/services/dead_letter_service.py` — `move_to_dlq` `:75+`; `move_to_dlq_standalone` `:221+`; `count_dlq` `:575-592`.
- `daemon/tools/_tool_registry.py` — `register_tool_category` `:529-554`; `DYNAMIC_TOOL_NAMES` `:100-109`; `KNOWN_TOOL_NAMES` `:1167-1176`; `scan_tools_for_full_docs` `:818-873`.
- `daemon/tools/instance.py` — `create_instance_tools` extend `:6058`; `_resolve_instance_id` `:1451-1477`.
- `daemon/constants.py` — `TERMINAL_INSTANCE_STATUSES` `:702-707`; `ALIVE_INSTANCE_STATUSES` `:760-766`; task-status vocabulary `:769-794`.
- `daemon/config.py` — `usage_limit_window_seconds` `:1776-1783`.
- `tests/unit/tools/test_frozen_tool_name_discovery.py` — the bidirectional drift pin `:223-242`.
- `tests/unit/test_pause_never_dispatched_ghosts.py` — the ghost-signature lockstep pin.
- `.agents/shared/planning/nest-ops/` — `architecture-recommendation.md:37`/`:57` (stale pre-rename rows), `:59`/`:268` (the rename verdict), `decisions.md:121`, `plan-overview.md:401`/`:564`.
