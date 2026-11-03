# Technical Analysis: Agent-facing Metrics Tool Surface (`metrics-agent-tools`, item #2)

Date: 2026-11-03 (UTC)
Author: planner[v2] via technical-analysis worker (DESIGN worker, lane `metrics-agent-tools`)
Analysis depth: deep-dive (the artifact the plan set is built from)
Status: Draft — Ready for Review
Worktree (read-only basis): `/home/nea/ensemble-src-wt-metrics-tools` (branch `feature/metrics-agent-tools`, base `origin/latest @ 03a2297c3180e79c6b5c26a92b2caca7c0452725`)

Primary inputs (written by research explorers in this worktree, read in full):
- `.agents/shared/planning/metrics-agent-tools/research-metrics-surface.md` (715 L — contract clauses, provider seam, auth at the door, D6 verbatim, inventories, freshness)
- `.agents/shared/planning/metrics-agent-tools/research-tool-conventions.md` (992 L — house privileged-category anatomy, pin ceremony, allow-list mechanics, alert-surface census)
- `.agents/shared/planning/metrics-agent-tools/research-folds.md` (the additive constraints folded in below)

**The frozen contract is frozen.** The tool layer adapts to `docs/architecture/metrics-query-contract.md`; it never changes it. The only named, in-scope change to the frozen surface is the `metrics:read` scope split (`central-metrics-v1/decisions.md` §D6, `:641-658`) — and even that is a *named commitment with a 4-line retrofit map*, executed by this lane per ruling R2.

---

## Question

What is the concrete, decision-ready **agent-facing surface** over the frozen central-metrics query door?

Sub-questions this artifact answers:

1. What are the exact tools (names, `args_schema`, provider calls, return shapes, default posture)?
2. What does the output look like so an agent can **decide**, not just read a dump — freshness, staleness, availability, cardinality, degradation?
3. How is the `metrics:read` scope split re-derived for an **in-process** tool (the HTTP retrofit map does not reach it)?
4. What is the watch/alert **outline** for a future lane (design-only, v1 is pull-only)?
5. How does the tool reach the provider (no factory exists; the tool layer gets only `manager`)?
6. What are the house-mechanics a new privileged read-only category must satisfy?

---

## Context Summary

Lane #1 (`central-metrics-v1`, shipped v0.28.6) froze a **one-door / three-route / one-provider** metrics query surface. The substrate is `daemon/services/metrics_query/`: a `Protocol` (`protocol.py:22-52`), a concrete `RegistryMetricsQueryProvider` (`provider.py:58`), provider-agnostic envelope builders (`envelope.py:85/:109/:154`), a label-aware selector parser (`selectors.py:32/:64`), a closed availability-reason vocabulary with a REQUIRED reason order (`availability.py:34/:76-92`), and 16 registered families across 26 catalogued (`wiring/adjudication.py:84-101`). The door serves live registry-sourced data in **both** kill-switch states ("availability, not emptiness"), is auth-gated `maintenance:read`, and fail-closes with `503 AUTH_NOT_CONFIGURED` when auth is OFF (`routers/metrics.py:121-137`).

**This lane is the provider's FIRST consumer.** `decisions.md:375-377` (D4 swappability guarantee) says so explicitly: *"Future agent tools (deferred item #2) call the SAME provider functions directly — the provider is importable, side-effect-free, and the single read substrate for both the HTTP door and the tool door."* The research census also records the gap this lane closes: `git grep 'readiness|loop_lag|pool_state|leak_signal|grace_active|resume_hold' daemon/tools/` → **ZERO hits** (`research-metrics-surface.md:563`) — the daemon's self-observability flows only to HTTP/loopback surfaces that **no agent tool wraps**; devops is *"the most metrics-hungry agent with the thinnest sanctioned read surface"*.

**The central design tension** is that the seam is **unscoped**: the provider imports nothing from `daemon.auth` (`provider.py:26-43`), the Protocol carries no `Request`/FastAPI type (`protocol.py:3-8`), and the live instance sits at `app.state.metrics_query` (`api.py:2533`) with **no auth wrapper** and **no factory**. An in-process tool therefore bypasses 401/403/503 entirely. That is the whole of Section B.

**House-mechanics context.** A new privileged read-only category is not one edit — it is a three-step registration seam (decorator → registry entries → construction) plus a SAME-PR pin fan-out (16 assertion sites across 10 files) plus per-agent allow entries plus a mandatory in-body `agent_id` gate. The template is `wedge-capture` (read-only, three holders); the second template is `instance_read` (read-only, *"a read has no side effect and writes NO audit row"* — `instance_read.py:62-64`).

---

## A. OUTPUT-SHAPE UX DESIGN

### A.0 — The tool set: one category, three flat tools

**Ruling R1 confirmed.** Flat tools (not a mega-tool), ONE category, three tools mirroring the provider's three operations.

| | |
|---|---|
| Category key | **`metrics-read`** (kebab-case — the house pattern, `_tool_registry.py:1060-1065`: *"The category key is KEBAB-case (`wedge-capture`) while the tool names are snake_case (`wedge_capture_*`) — the house pattern (`self-alarm` → `self_alarm`, `board-link` → `board_link`)"*) |
| Category id (module constant) | `METRICS_CATEGORY_KEY = "metrics-read"` |
| Tool names | **`metrics_families`**, **`metrics_snapshot`**, **`metrics_history`** (snake_case, `metrics_` prefix) |
| Human title | `CATEGORY_NAME = "Metrics"` |
| Module | `daemon/tools/metrics_read.py` (new) |
| Holders | `maintenancer`, `devops`, `wanderer` — ONE category, ONE allow-set (R4) |
| In-body gate | `METRICS_AUTHORIZED_AGENTS = frozenset({"maintenancer", "devops", "wanderer"})` |

**Why flat over mega-tool — the evidence (R1 upheld, not merely inherited).**
The house's own split doctrine (`_tool_registry.py:544-560`) separates a read category from an act category *"so a future read-only grant to another agent is a one-line allow change on a category that structurally CANNOT carry acts."* A mega-tool would collapse three distinct **views** (catalog / current value / windowed trend) behind one `args_schema`, forcing every caller to know the difference between a `view=` enum and the per-view parameters, and forcing the `args_schema` to carry every union of parameters. Flat tools keep each `args_schema` minimal (the pydantic schema IS the tool's documentation surface — `loader.py:684-707` renders the tools list into the system prompt), keep each tool's provider call singular and auditable, and let the `tool_help` surface carry a per-tool `_full_doc_`. The wedge-capture precedent (two tools, one category) and the `instance_read` precedent (four tools, one category) are the same shape.

**Name-collision verification (against `KNOWN_TOOL_NAMES` / `DYNAMIC_TOOL_NAMES` / `PRIVILEGED_TOOL_CATEGORIES`).**

```
$ grep -n 'metrics' daemon/tools/_tool_registry.py
1534:    "skill_get_metrics",
```

- `skill_get_metrics` (`_tool_registry.py:1534`) is the **only** `metrics`-containing name in the registry. It lives in the `dynamic-skill` category and is a *skill-evolution metrics* tool. **No collision** with `metrics_families` / `metrics_snapshot` / `metrics_history` — the strings differ, and the registry's name set is exact-match.
- `PRIVILEGED_TOOL_CATEGORIES` (16 entries today) contains **no** `metrics-read` and no `metrics*` key — verified by parsing the literal. `metrics-read` is free.
- `DYNAMIC_TOOL_NAMES` (101 names) contains **no** `metrics*` entry; the wedge/board factory-built names ARE in it (`wedge_capture_list`, `wedge_capture_get`, `board_*`) — so the three new names MUST be added there too (they are factory-built, not import-time registered).
- `KNOWN_TOOL_NAMES` contains no `metrics*` entry either — the three names must be added.
- **Semantic proximity warning (not a collision):** `snapshot_create` / `snapshot_search` (`_tool_registry.py:1539-1540`) exist in the **`snapshot` category** and mean *Agent Snapshot* (a task-scoped working-state capture) — a completely different concept from a metrics *current-values* snapshot. `metrics_snapshot`'s docstring and `CATEGORY_DOC` MUST carry the disambiguation sentence: *"metrics_snapshot returns CURRENT published metric values. It is NOT the Agent Snapshot artifact — that is `snapshot_create` / `snapshot_search` in the `snapshot` category."*

**The `/series` ↔ `history()` naming mapping (resolved and documented).**

| Layer | Name for the third view |
|---|---|
| HTTP route | `GET /api/metrics/series` (`routers/metrics.py:673`) |
| Provider method | `history(...)` (`provider.py:318`) |
| Envelope builder | `build_series_envelope(...)` (`envelope.py:154`) |
| **Agent tool** | **`metrics_history`** |

**Resolution: the tool is named after the PROVIDER METHOD (`history()`), not the wire route (`/series`).** Rationale: the tool is the provider's *in-process* door — it calls `provider.history()` directly, and the house's tool-naming rule is that the name says what the tool does (`wedge_capture_list` → `list`, `instance_get` → `get`). The three tool names then read as a complete 1:1 mirror of the three Protocol methods (`families()` / `snapshot()` / `history()`), which is exactly the seam this lane consumes. The `/series` word is not lost: the tool's **`series` argument** (the label-aware selector list) uses it, and the mapping is stated in three places (the module `CATEGORY_DOC`, each tool's one-line docstring, and the `tool_help` `_full_doc_`). The rejected alternative — naming it `metrics_series` — creates a **second vocabulary** that matches the wire but not the seam, and would make an agent that read `docs/architecture/metrics-query-api.md` hunt for a name that does not exist.

---

### A.1 — How the tool reaches the provider (the additive seam)

**The problem, stated precisely.** The live provider exists in exactly one place: `app.state.metrics_query = _metrics_query_provider` (`daemon/api.py:2533`, inside `create_app`'s `lifespan`, which starts at `daemon/api.py:480`). The construction is at `api.py:2527-2532` and the degradation path sets `app.state.metrics_query = None` and logs ERROR (`api.py:2540-2547`). The router reads it via `request.app.state.metrics_query` (`routers/metrics.py:219-233`). The **agent-tool factory receives `manager`, never the `FastAPI` app** (`create_instance_tools(manager, …)`, `daemon/tools/instance.py:3014`), and the manager has no reference to the app or to `app.state`. There is **no factory function** in the seam — `daemon/services/metrics_query/__init__.py:83-127` re-exports classes and functions only, and the package is **side-effect-free** (`__init__.py:16-19`: importing it starts NO thread, opens NO connection, registers NO metric).

**Why the tool must NOT construct its own provider.** The trend view lives in the `TrendRingStore` — an in-RAM array ring (`ring.py`, 16 B/point, 2 h window, `WINDOW_SECONDS = 7200`). A second provider instance would have a second (empty) ring and the `history()` tool would return `[]` forever (`provider.py:340-341`: *"no ring ⇒ no trend, not an error"*). The registry itself IS a module-level singleton (`daemon/checkpoint_metrics.py`), so `families()`/`snapshot()` would work from any provider — but `history()` would not. **One provider, one ring, one truth.**

**The chosen mechanism — an ADDITIVE manager-held mount (manager → provider).**

Three candidate mechanisms were considered:

| Option | Mechanism | Verdict |
|---|---|---|
| (a) **Manager-held reference** | `InstanceManager.metrics_query_provider` property, set by the lifespan | **CHOSEN** — zero new plumbing at the tool call site (the factory already receives `manager`); matches the house's `manager.board_service` / `manager.scheduling_service` precedent (`manager.py:15863` / `:15840`); no module-level mutable global. |
| (b) Module-level holder in the seam | `daemon/services/metrics_query/holder.py::set/get_query_provider()` | Rejected — a mutable module global; the house's `job_queue_service.set_instance_manager(manager)` precedent exists but is a legacy shape, not the target for new work. |
| (c) Factory in the seam | `metrics_query.build_provider(...)` called by both `api.py` and the tool | Rejected — the tool would build a SECOND provider/ring (breaks the single-substrate invariant) unless the factory became a singleton holder, which is (b) with a worse name. |

**Concrete additive change (3 edits, zero contract change):**

1. `daemon/manager.py` — a class-level default + a typed property:
```python
if TYPE_CHECKING:  # typing-only import — zero runtime import cost, zero cycle risk
    from daemon.services.metrics_query.protocol import MetricsQueryProvider

class InstanceManager:
    #: The live metrics query provider (the ONE read substrate shared by the
    #: HTTP door and the agent-tool door). Mounted by the lifespan right
    #: next to ``app.state.metrics_query``; ``None`` when the metrics stack
    #: is boot-degraded (api.py:2540-2547) or on a test double that never
    #: mounted it. A class-level default means ``getattr`` always succeeds.
    metrics_query_provider: "MetricsQueryProvider | None" = None

    @property
    def metrics_query(self) -> "MetricsQueryProvider | None":
        """The metrics query provider (or ``None`` when not mounted)."""
        return self.metrics_query_provider
```
2. `daemon/api.py:2533` — one additive line beside the existing assignment:
```python
app.state.metrics_query = _metrics_query_provider
manager.metrics_query_provider = _metrics_query_provider   # ← NEW (additive)
```
and the degradation branch (`api.py:2540-2547`) leaves the class default `None` (or sets it explicitly to `None`).
3. `daemon/tools/instance.py` — the factory wrapper (see A.11).

**Is this additive or a contract change?** **ADDITIVE — allowed.** The contract governs the HTTP/wire semantics; nothing in `docs/architecture/metrics-query-contract.md` mentions `app.state`, the manager, or the tool layer. The provider's own contract (`provider.py:3-12`) is unchanged. The only "shared state" invariant being introduced is *"one provider instance per process"*, which is already true in production today (`app.state.metrics_query` is the single instance) — the change only makes that instance reachable from the tool layer.

---

### A.2 — Protocol-completeness discipline (additive Protocol amendment)

**The house lesson** (`.agents/tidier/notes.md`, central-metrics-v1 craftsmanship pass): *"a Protocol that no consumer references guarantees nothing — extend the Protocol BEFORE typing the consumer, or the consumer resorts to `Any` + getattr and the Protocol's incompleteness stays invisible."*

**`MetricsQueryProvider` has NO consumer today — this lane is its first.** The tool MUST be typed against the Protocol with **no `Any` / `getattr` escape hatch on the provider**.

**What the three tools actually need, member by member (audited against `protocol.py:22-52` and `provider.py:87-160`, `:397-422`):**

| Fact the tools need | Provider has it? | Protocol declares it? | Gap |
|---|---|---|---|
| `families() -> list[dict]` | ✅ `provider.py:211` | ✅ `protocol.py:29` | — |
| `snapshot(*, family, name_prefix)` | ✅ `provider.py:281` | ✅ `protocol.py:32` | — |
| `history(*, names, series, window_s, max_points)` | ✅ `provider.py:318` | ✅ `protocol.py:37` | — |
| `history(..., now=…)` (the previous-window anchor) | ✅ `provider.py:325` | ❌ | **GAP-1** |
| `history(..., max_series=…)` (the work bound) | ✅ `provider.py:326` | ❌ | **GAP-2** |
| `registry_read_ok` (the degraded-read flag) | ✅ `provider.py:98-107` | ❌ | **GAP-3** |
| `listener_enabled` | ✅ `provider.py:87-96` | ❌ | **GAP-4** |
| `census_enabled` | ✅ `provider.py:109-122` | ❌ | **GAP-5** |
| `effective_interval_s` | ✅ `provider.py:124-131` | ❌ | see note |
| `last_sample_age_s` | ✅ `provider.py:397` | ❌ | see note |
| `last_sample_timestamp()` | ✅ `provider.py:406` | ❌ | optional |
| `oldest_sample_timestamp()` | ✅ `provider.py:412` | ❌ | not needed (no cursor) |

**The proposed ADDITIVE amendment** to `daemon/services/metrics_query/protocol.py` (same file, same Protocol — **not** a new protocol, **not** a contract change: the contract governs the HTTP/wire semantics, the Protocol governs the in-process seam):

```python
@runtime_checkable
class MetricsQueryProvider(Protocol):
    # ── the three views (UNCHANGED — the frozen seam) ──
    def families(self) -> list[dict[str, Any]]: ...
    def snapshot(self, *, family: str | None = None,
                       name_prefix: str | None = None) -> list[dict[str, Any]]: ...
    def history(self, *, names=None, series=None, window_s=None, max_points=None,
                     now: int | None = None,          # ← ADDITIVE (GAP-1)
                     max_series: int | None = None     # ← ADDITIVE (GAP-2)
                ) -> list[dict[str, Any]]: ...

    # ── ADDITIVE: the degradation + activation facts (GAP-3..5) ──
    @property
    def registry_read_ok(self) -> bool: ...
    @property
    def listener_enabled(self) -> bool: ...
    @property
    def census_enabled(self) -> bool: ...
```

**Why GAP-1/GAP-2 are non-negotiable additions.** GAP-2 (`max_series`) is the tool's **work bound** (A.4): the provider's own docstring says *"``max_series`` caps the WORK: the provider stops collecting once it holds that many entries (W-2)"* (`provider.py:330-337`). Without it the tool cannot bound its own provider call. GAP-1 (`now`) is what makes the **comparable-window delta** sound (A.9): the previous window must be anchored at `instant − window_s`, and `now=None` anchors at `time.time()` — two un-anchored calls milliseconds apart would return the SAME window.

**Why the two NUMERIC facts stay OUT of the Protocol (a deliberate non-amendment).** `effective_interval_s` is a **property** on the provider (`provider.py:124`) but a plain **attribute** on the shared test fake (`tests/helpers/metrics_query_fixtures.py:67`); `last_sample_age_s` is a **method** on the provider (`provider.py:397`) but a plain **attribute** on the same fake (`:66`). The seam already has a **sanctioned duck-type bridge** for exactly this: `envelope._provider_float(provider, attribute, default)` (`envelope.py:135-146`) reads *"property OR method"* — and `envelope.effective_interval_s(provider)` (`:149-151`) is the public wrapper the door itself uses. Forcing one shape into the Protocol would be **seam-side churn with zero consumer value**, and would break the sanctioned fake. **Ruling:** the two numeric facts are read through the seam's own public reader, and the reader is **promoted to a public name** so the tool never touches a private symbol:

> **Add (1 line, additive):** `daemon/services/metrics_query/envelope.py` — publish `read_numeric_fact = _provider_float` (keep `_provider_float` as the internal name so the envelope's own call sites are untouched). The tool imports `read_numeric_fact` + `effective_interval_s` from the package and calls `read_numeric_fact(provider, "last_sample_age_s", 0.0)` / `effective_interval_s(provider)`.

This is the same discipline the lesson demands — **the consumer is typed against the seam's public contract** (the Protocol for the three views + three facts, the package's public reader for the two numerics) — **not** against `Any`/`getattr`. *(import-path wording superseded by planner ruling PL-4 — see decisions.md Part 5: `read_numeric_fact` is imported DIRECT from the `envelope` module; `__init__.py` does not export it and is not widened.)*

**The amendment's cost, enumerated (the plan must carry it):** `runtime_checkable` `isinstance()` checks member presence, so any fake missing a newly-declared member stops satisfying the Protocol. Exactly **two** provider fakes exist (`grep -rn 'def history' tests/ --include=*.py`):
- `tests/helpers/metrics_query_fixtures.py:47` `RecordingProvider` — already has `listener_enabled` / `census_enabled` as attributes (✅ satisfies GAP-4/GAP-5); **must grow `registry_read_ok = True`** (GAP-3).
- `tests/unit/metrics/test_metrics_query_provider.py:557-572` `_Fake` — has **none** of the three; **must grow all three** (defaults `True`/`True`/`True`). Its `history(self, **kwargs)` already absorbs `now=` / `max_series=` ✅.

Both fakes' `history()` signatures already accept the two new kwargs — **no fake breaks on GAP-1/GAP-2.** The amendment is therefore additive in production code with a two-line mechanical test-side cost. The plan must also add a pin asserting the amendment (the Protocol declares every member the tool calls) — the house lesson's whole point.

---

### A.3 — The three tools — full signatures

**Common result envelope (all three tools).** Every tool returns a JSON string (house pattern — `json.dumps(..., indent=2, default=str)`, `wedge_capture.py:203-206`). The shape is a **projection of the frozen envelope**, not the envelope itself:

```jsonc
{
  "tool": "metrics_history",                       // which tool produced this
  "read_status": { … },                            // the DEGRADED-READ block (A.9)
  "schema_version": 1,                             // echoed from the frozen envelope header
  "vocabulary_version": 2,                         // echoed (never hard-coded as a gate)
  "generated_at_utc": "2026-11-03T01:12:44.902Z", // echoed (aware UTC)
  "restart_epoch": "9f2c…",                        // echoed (boot id — a restart is visible)
  "activation": { … },                             // echoed VERBATIM (4 members, incl. registry_read_ok)
  "query": { … },                                  // the EFFECTIVE query (post-defaulting)
  "matched_count": 2,                              // rows/series/families matched before the tool's bound
  "returned_count": 2,                             // rows/series/families returned
  "dropped_count": 0,                              // matched − returned (null when not exactly knowable)
  "truncated": false,                              // TRUE whenever anything was dropped
  "narrow_hint": null,                             // a CONCRETE instruction when truncated
  "caveats": [],                                   // standing advisories (never silent)
  // …tool-specific payload…
}
```

The `matched_count` / `returned_count` / `dropped_count` / `truncated` / `narrow_hint` quintet is the **uniform truncation-surfacing block** (A.6) — identical in all three tools, so one agent reading rule covers all three. *(renamed by architect-fold AmB-14 — see decisions.md Part 5: the block is SIX members (`more_available` is the sixth); "quintet" is superseded everywhere by "the six-member bound block".)*

---

#### Tool 1 — `metrics_families`

**Purpose:** the family catalog — what metrics exist, what shape they have, and whether they are live.

```python
class MetricsFamiliesInput(BaseModel):
    family: Annotated[
        str | None,
        Field(default=None, description=(
            "Exact family id to read (e.g. 'F9' for readiness, 'F1' for "
            "event-loop lag, 'F10' for the job queue, 'F6' for the PG "
            "census). Omit for the whole catalog (16 families)."
        )),
    ]
    details: Annotated[
        bool,
        Field(default=False, description=(
            "When true, each metric name also carries its declared label "
            "space, buckets, monotonicity, chart_role and help text. "
            "Default false = names + kind + cardinality_class only "
            "(the catalog stays small)."
        )),
    ]
    only_with_data: Annotated[
        bool,
        Field(default=False, description=(
            "When true, keep only families that currently publish at least "
            "one live series (live_series_count > 0). Note this is NOT the "
            "same as availability.state == 'live' — a family can report "
            "dark and still be publishing (see dark_with_live_series)."
        )),
    ]
```

**Provider call:** `provider.families()` — then the tool filters `family` (exact id match) and `only_with_data` (`live_series_count > 0`) in the tool layer.

**Returns (payload):** `families: list[dict]` — each entry is the **frozen family entry** (`provider.py:268-278`) PLUS three tool-layer derived members:

| Member | Source | Meaning |
|---|---|---|
| `id`, `role`, `source_idiom`, `durability`, `live_series_count`, `cardinality_contribution`, `availability` | **verbatim** from the provider | the frozen shape |
| `availability` | **verbatim** (`{"state": "live"\|"dark", "reason"?: …}`) | never re-derived, never re-ordered (A.8) |
| `name_count` | derived | the family's **DECLARED** name count — the TRUE count, even when the `names` projection is bounded (200/family, 2000/response): `name_count` is the DECLARED count, `names` is the BOUNDED projection, and the difference between the two IS the visible signal that names were dropped |
| `data_present` | derived | `live_series_count > 0` |
| `dark_with_live_series` | derived | `availability.state == "dark" AND live_series_count > 0` |

With `details=false`, each name renders as `{"name", "kind", "cardinality_class"}`; with `details=true` it additionally renders `labels` (the declared value space or `null`), `monotonic`, `chart_role`, `buckets`, `help`.

**Default posture:** `family=None, details=False, only_with_data=False` → the whole 16-family catalog, one line per family. **Never** switch-gated (the door is never switch-gated — D1/AM-17); **never** an error on an empty catalog — the `read_status` block carries the degraded signal instead (A.9).

**Failure modes:** unknown family id → the tool returns a **catalog listing with a hint**, not an error: `{"families": [], "matched_count": 0, "narrow_hint": "no family with id 'F17x'; known ids: F0.3, F1, F3, …"}`. (The HTTP door answers `404 unknown_family` for this — `routers/metrics.py:662-665`; the tool has no HTTP status, so it answers with the closest useful thing: the list of valid ids. The tool MAY also raise the seam's `InvalidLabelFilter` → typed refusal, A.6.)

---

#### Tool 2 — `metrics_snapshot`

**Purpose:** the CURRENT published values of the matching series.

```python
class MetricsSnapshotInput(BaseModel):
    family: Annotated[
        str | None,
        Field(default=None, description=(
            "Exact family id (e.g. 'F10'). Omit for all families."
        )),
    ]
    name_prefix: Annotated[
        str | None,
        Field(default=None, description=(
            "Metric-name prefix filter (e.g. 'ensemble_loop_lag_' → every "
            "loop-lag metric). An EXACT metric name is a valid prefix (a "
            "name is a prefix of itself), so name_prefix='ensemble_"
            "loop_lag_seconds' returns exactly that metric's series."
        )),
    ]
    top_n: Annotated[
        int,
        Field(default=20, description=(
            "Max series rows to return (1-50, default 20). Order is "
            "ascending by (family, name, label key) — deterministic, so "
            "the same query always returns the same rows."
        )),
    ]
```

*(B-6's ascending-order promise above is superseded-in-part by architect-fold AmB-6 — see decisions.md Part 5: the provider returns registration order; the TOOL sorts before the `top_n` cut so the promise is tool-enforced.)*

**Provider call:** `provider.snapshot(family=family, name_prefix=name_prefix)` — **exactly** the provider's two keyword arguments, no more. *(superseded-in-part by architect-fold AmB-10/AmB-6 — see decisions.md Part 5: `families: list[str] | None` is added tool-side over ONE provider call (PL-1), and the tool sorts BEFORE the `top_n` cut so the ascending promise above is tool-enforced, not provider-given — the provider returns registration order.)* The `top_n` cut is applied by the tool AFTER the provider returns (the provider returns the full filtered list; the snapshot view carries no points, so the work is trivial).

**Returns (payload):** `series: list[dict]` — each row is the **frozen snapshot row** (`provider.py:296-312`: `name`, `labels`, `kind`, `monotonic`, `last_publish_age_s`, `value` — plus `histogram` for histogram kinds) PLUS two tool-layer derived members:

| Member | Source | Meaning |
|---|---|---|
| `name`, `labels`, `kind`, `monotonic`, `value`/`histogram`, `last_publish_age_s` | **verbatim** | the frozen row |
| `stale` | derived | `last_publish_age_s > 3 × effective_interval_s` — the contract's own 3× rule (`constants.py:92` `STALE_INTERVAL_MULTIPLIER = 3.0`) applied to the row's publish age |
| `cardinality_class` | derived (from the catalog) | so the agent can tell a `closed` name's declared space from an `open` name's observed labels |

Plus the top-level `effective_interval_s` (the observed spacing) so the agent can recompute `stale` itself.

**Default posture:** `family=None, name_prefix=None, top_n=20`. Unfiltered, this returns every live series (112 measured in the fenced-dev observation — `metrics-promote-disclosure.md:53-57`); with `top_n=20` the tool returns 20 and reports `dropped_count: 92` plus a `narrow_hint` naming the family to narrow to. **The agent should always pass a `family` or `name_prefix`** — the `narrow_hint` says so.

**Failure modes:** unknown family → same shape as tool 1 (empty + `narrow_hint` naming the known ids). No live series for the filter → `series: []`, `matched_count: 0`, and — critically — the `read_status` block decides whether that means "no data" or "the read failed" (A.9).

---

#### Tool 3 — `metrics_history`

**Purpose:** the windowed TREND of the selected series, with a decision-ready delta.

```python
class MetricsHistoryInput(BaseModel):
    family: Annotated[
        str | None,
        Field(default=None, description=(
            "Exact family id; the tool resolves it to that family's metric "
            "names from the catalog (a convenience over the provider's "
            "name-level selector)."
        )),
    ]
    series: Annotated[
        list[str] | None,
        Field(default=None, description=(
            "Label-aware series selectors, the contract's canonical form: "
            "'<name>{k=v,k2=v2}' — e.g. "
            "'ensemble_loop_lag_seconds{stat=p95}'. Comma-separated k=v "
            "pairs. A bare '<name>' matches every label variant. Max 50."
        )),
    ]
    names: Annotated[
        list[str] | None,
        Field(default=None, description=(
            "Name-level selectors (every label variant of the named "
            "metrics). Max 50. Prefer 'series' when you know the label "
            "values."
        )),
    ]
    window_s: Annotated[
        int,
        Field(default=900, description=(
            "Window length in seconds (60-7200, default 900 = 15 min). "
            "The ring holds 2 h; longer windows are clamped."
        )),
    ]
    max_points: Annotated[
        int,
        Field(default=120, description=(
            "Max points per series (2-240, default 120). Server-side "
            "bucket-max downsample — peaks are preserved, never "
            "every-kth."
        )),
    ]
    top_n: Annotated[
        int,
        Field(default=10, description=(
            "Max series to return (1-50, default 10). The provider is "
            "asked for top_n+1 internally so the tool can detect that "
            "more exist."
        )),
    ]
    compare: Annotated[
        bool,
        Field(default=True, description=(
            "When true (default), also read the immediately preceding "
            "equal-length window and report delta vs that comparable "
            "window. Set false to halve the work."
        )),
    ]
```

**Provider calls (two, when `compare=true`):**

```python
instant = int(time.time())
current  = provider.history(names=names, series=series, window_s=window_s,
                            max_points=max_points, now=instant,
                            max_series=top_n + 1)          # ← the WORK bound
previous = provider.history(names=names, series=series, window_s=window_s,
                            max_points=max_points,
                            now=instant - window_s,          # ← the comparable-window anchor
                            max_series=top_n + 1)            #   (GAP-1)
```

The `top_n + 1` is the **continuation probe** — the identical trick the frozen envelope builder uses (`envelope.py:203-210`: *"the provider collects only what this page needs plus ONE probe entry, which tells us whether more series remain"*). The `+1` entry is detected and discarded.

**Returns (payload):**

```jsonc
"window": {                                   // echoed from the frozen envelope
  "from_utc": "…", "to_utc": "…",
  "interval_s": 15.0, "interval_s_source": "observed"
},
"last_sample_age_s": 9.4,                    // frozen field
"stale": false,                              // frozen field (3× interval rule)
"effective_interval_s": 15.0,                // derived (observed)
"compare_window": { "from_utc": "…", "to_utc": "…" },   // only when compare=true
"series": [
  {
    "name": "ensemble_loop_lag_seconds",      // frozen
    "labels": { "stat": "p95" },              // frozen (the COMPLETE discriminator)
    "kind": "gauge",                          // frozen
    "monotonic": false,                       // frozen
    "points": [ [1762132447, 0.0041], … ],    // frozen ([epoch_s, value] pairs)
    "delta": 0.0113,                          // derived: last − first of THIS window
    "prev_delta": 0.0009,                     // derived: last − first of the PREVIOUS window
    "delta_change": 0.0104,                   // derived: delta − prev_delta
    "delta_reason": null                      // "insufficient_points" when <2 points
  }
]
```

**Default posture:** `family=None, series=None, names=None, window_s=900, max_points=120, top_n=10, compare=True`. With NO selector at all, the provider would match **every published series** — so the tool applies the **hard floor**: if neither `series` nor `names` nor `family` is given, the tool returns a typed refusal naming the 16 family ids and the selector syntax (it never runs an unbounded match). If `family` IS given, the tool resolves it to that family's metric names from the catalog and passes them as `names=` (the resolution is the tool's convenience; the provider still receives only its own two selector kwargs).

**Failure modes:** a malformed selector (`InvalidLabelFilter`, `selectors.py:28-29`) → typed refusal `invalid_label_filter` (never a 500, never a silent empty). More than 50 selectors → the tool refuses **before** calling the provider (the provider would raise the same class at `provider.py:346-349`; the tool pre-empts it so the agent gets a clean message).

---

### A.4 — Sane defaults: the mapping onto the contract's two-axis bounds

**The rule (R6 + the "agents must never need to understand cursors or cardinality" requirement):** every bound the contract exposes is a CEILING the tool stays far below, and the tool's defaults are chosen so a *default* call can never approach a ceiling.

| Contract bound | Value | Source | Tool's ceiling | Tool's DEFAULT | Head-room at default |
|---|---|---|---|---|---|
| `MAX_NAMES` (requested selectors) | 50 | `constants.py:46` | 50 (`series`/`names` list length; the tool refuses above it) | ≤ 1 family resolved to ≤ 5 names | — |
| `MAX_SERIES` (emitted series) | 500 | `constants.py:69` | 50 (`top_n` max) | 10 | 50× |
| `MAX_TOTAL_POINTS` (emitted points) | 12 000 | `constants.py:78` | 12 240 worst case (51 × 240) | 1 320 (11 × 120) | 9.1× |
| `MAX_POINTS_CEILING` (points per series) | 240 | `constants.py:55` | 240 (`max_points` max) | 120 | 2× |
| `WINDOW_SECONDS` (ring window) | 7 200 | `constants.py:33` | 7 200 (`window_s` max) | 900 | 8× |
| `MAX_SNAPSHOT_SERIES` | 500 | `constants.py:83` | 50 (`top_n` max) | 20 | 25× |
| `MAX_FAMILIES` | 500 | `constants.py:59` | 50 | 16 (all) | 31× |
| `RESPONSE_BYTE_CEILING` | 1 MiB | `constants.py:87` | — | — | — |

**The work bound is provable.** The provider's `history()` stops collecting at `max_series` entries (`provider.py:336-337`), so one call's work ≤ `(top_n + 1) × max_points` points. At the tool's DEFAULTS: **11 × 120 = 1 320 points = 11 % of `MAX_TOTAL_POINTS`**. At the tool's CEILINGS: 51 × 240 = 12 240 points ≈ the contract's own ceiling. *(superseded-in-part by architect-fold AmM-1 — see decisions.md Part 5: 12 240 points EXCEEDS `MAX_TOTAL_POINTS` = 12 000 by 240 (not "≈ the ceiling"), and `compare=true` by default makes provider work 2 640 pts = 22 %, not 11 %; M-1's returned-leg clamp, tool-local byte ceiling, separate compare-leg accounting and CATEGORY_DOC cost statement are BINDING.)* The tool therefore never issues a call the contract would consider over-budget, and never issues a call the provider cannot bound.

**The window default (`window_s=900`).** The contract's `DEFAULT_MAX_POINTS = 240` exists because *"60 min × 4/min at 15 s spacing"* is the acceptance floor (`metrics-query-contract.md:201-203`) — i.e. 240 points ≈ 60 min at the observed 15 s spacing. A 60-minute default window is right for a *chart* and wrong for an *agent*: at 15 s spacing it is 240 points × N series — a large LLM payload for a decision. The tool's default is **15 min (900 s)**, which at 15 s spacing is 60 raw points (60 → 120 max_points is a no-op at the observed spacing), and which covers the two relevant operational horizons: the wedge-capture sustained-loop-lag trigger window (`wedge_capture/triggers.py:54-55`) and the 30-minute self-alarm floor *(truth-in-docs correction, approver i2-N8 — see decisions.md Part 5: the wedge-capture lag trigger is `DEFAULT_LAG_S = 5.0` sustained `DEFAULT_LAG_SUSTAIN_S = 15.0` (`daemon/services/wedge_capture/config.py:100-101`), NOT an 1800 s window; `1800 s` is the self-alarm `MIN_HORIZON_SECONDS` (`daemon/services/self_alarm_service.py:77`) — the same sentence names both horizons, which is where the blemish came from. The 900 s default window still covers the ~15 s sustain ~60×; the PARAMETER CHOICE is unaffected.)* The agent can raise it to 7 200 when it needs the full ring.

**The `top_n` default.** 10 series ≈ the F1 band (9), the F18 band (5), the F6 band (15). A default call returns "the interesting families whole" for most families and truncates the biggest (F12: 117, F9: 53) — with the exact dropped count reported.

**Cardinality is the agent's problem to NOT have.** The tool never asks the agent to reason about label cardinality:
- **Family-wide queries** (`family="F9"`) are resolved by the tool to that family's metric names, and the tool's `top_n` bounds the result — the agent never enumerates 53 label variants by hand.
- **Label-aware selectors** (`series="ensemble_loop_lag_seconds{stat=p95}"`) exist for the case where the agent wants exactly one combination — and the tool accepts them verbatim from the contract's canonical syntax (`selectors.py:32-61`), which is the SAME serialization the ring keys use (`checkpoint_metrics.py:485`), so a selector can never disagree with the data.

---

### A.5 — Cursor handling (R6: cursors are never exposed to agents in v1)

**The contract's cursor policy** (`metrics-query-contract.md:291-294`): `next_cursor: string | null`, opaque, pins the evaluation instant + position; an invalid/expired cursor (older than the ring's oldest point) ⇒ `400 invalid_cursor` — never a 500, never a silent gap. The route mints it (`routers/metrics.py:454-473`); the envelope builders do **not** (`envelope.py:344-346`).

**The tool's rule: NO tool accepts a cursor and NO tool returns a cursor in v1.** The tool is a PULL surface for an agent making a forward-moving decision, not a pager. Concretely:

1. **Every call re-anchors.** The tool computes `instant = int(time.time())` itself and passes `now=instant` (`GAP-1`). The window is always *"the last N seconds as of now"*. There is no way for an agent to pin a past instant — and no need: the ring is 2 h and in-RAM; the interesting question is always "what does it look like NOW".
2. **The comparable-window leg is an INTERNAL re-anchor, not a cursor.** `compare=true` reads the preceding window with `now = instant − window_s` — the same anchor discipline, applied one window back, entirely inside the tool. The agent never sees a cursor; it sees `prev_delta`.
3. **The continuation mechanism is "narrow the selector", not "resume a page".** When the tool's own bound drops something, it emits `narrow_hint` naming the exact next filter to apply (A.6). The agent's next call is a fresh, re-anchored query — which is *semantically better* than a cursor, because a cursor pins a stale instant that the agent then has to reason about.
4. **The v2 shape, stated for the record (not planned here):** if a future lane ever needs paging, the tool would accept an opaque `cursor` string, map a `400 invalid_cursor` / `InvalidCursor` (`envelope.py:39`) to a typed refusal carrying an **explicit re-anchor instruction** (*"the window moved on — call again without a cursor"*), and mint the cursor itself rather than pass one through. No contract change is needed for that (the cursor vocabulary is already frozen); the tool simply would not expose it in v1.

**Consequence for the tool's `truncated` handling:** the contract's clause *"a body that dropped something ALWAYS carries the continuation cursor"* (`metrics-query-contract.md:283-286`) governs the **HTTP page body**. The tool is not an HTTP page body — it is a **projection** (D-UX-3). The tool honours the clause's INTENT (never silent) by emitting `truncated: true` + `dropped_count` + `narrow_hint` on every dropped result. That is strictly more information than a cursor would give the agent.

---

### A.6 — The truncation rule: "a bound that drops something is never silent"

**The uniform block (all three tools):**

| Field | Type | Meaning |
|---|---|---|
| `matched_count` | int \| null | how many rows/series/families matched **before** the tool's own bound. `null` when not exactly knowable (the history view's work bound prevents counting the tail) |
| `returned_count` | int | how many were returned |
| `dropped_count` | int \| null | `matched_count − returned_count`; `null` when the exact count is not knowable |
| `more_available` | bool | true when the provider's probe entry (`top_n + 1`) saw one more |
| `truncated` | bool | **true whenever anything was dropped** — the single boolean the agent must check |
| `narrow_hint` | string \| null | a CONCRETE next action |

**Where each count is exact:**
- `metrics_families` — **exact.** The catalog is 16 entries; the tool materializes it fully and cuts.
- `metrics_snapshot` — **exact.** The provider returns the full filtered list; the tool cuts.
- `metrics_history` — **`returned_count` exact; `matched_count`/`dropped_count` null; `more_available` boolean.** The provider's work bound (`max_series=top_n+1`) means the tail was never materialized. The agent's route to the exact count is `metrics_snapshot` with the same filter (it returns every live label variant with no points). The `narrow_hint` says so explicitly.

**The `narrow_hint` is not boilerplate — it names the actual next filter.** Examples the tool must emit verbatim:
- *"`returned_count=10` of more than 10 series matched (top_n=10). Narrow with `family='F9'` (readiness), or a label filter such as `series='ensemble_loop_lag_seconds{stat=p95}'`, or raise `top_n` (max 50). For the exact matched count call `metrics_snapshot` with the same filter."*
- *"`matched_count=112` of live series; `top_n=20` returned 20. Narrow with `family=…` — known ids: F0.3, F1, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12, F13, F17, F18, F21."*

**Standing caveats (`caveats[]`)** — never silent, always advisory:
- The **dark-but-live** caveat (A.9): emitted when any returned family has `dark_with_live_series: true`.
- The **degraded-read** caveat (A.9): emitted when `registry_read_ok` is false.

---

### A.7 — Freshness, staleness, and the restart signal

The contract's doctrine: *"staleness as explicit metadata, never a silently frozen line"* (`metrics-query-contract.md:198-199`) and *"the staleness of a value is EXPLICIT metadata (`last_publish_age_s`), never a silently stale number"* (`provider.py:3-12`).

| Signal | Where it appears | Source | Meaning |
|---|---|---|---|
| `generated_at_utc` | top level, all three tools | frozen header (`envelope.py:79`) | aware-UTC ISO-8601 of the read |
| `restart_epoch` | top level, all three tools | frozen header (`envelope.py:80`) | the boot id (`bash_spawn_reaper.CURRENT_BOOT_ID`) — **a daemon restart is visible, never a silent gap**. The agent compares it across calls; a changed value means the trend history was lost (the ring is in-RAM, `contract:400-402`) |
| `last_sample_age_s` | `metrics_history` top level | frozen field (`envelope.py:224`) | age of the newest ring sample |
| `stale` | `metrics_history` top level | frozen field (`envelope.py:225`) | `age > 3 × effective_interval_s` — the contract's rule, verbatim |
| `effective_interval_s` | `metrics_history` + `metrics_snapshot` top level | derived via the seam's public reader | the **OBSERVED** spacing (`max(sampler, publisher)` = 15 s at defaults), never the configured cadence |
| `last_publish_age_s` | each `metrics_snapshot` row | frozen field (`provider.py:304`) | seconds since that metric's last publish |
| `stale` (derived) | each `metrics_snapshot` row | derived | `last_publish_age_s > 3 × effective_interval_s` |
| `window.interval_s` + `interval_s_source:"observed"` | `metrics_history` | frozen | the agent can reason about resolution |

**Deltas — never fabricated.** `delta` = `last − first` of the requested window; `prev_delta` = `last − first` of the preceding equal-length window; `delta_change` = `delta − prev_delta`. If a window holds fewer than 2 points, the fields are **`null`** with `delta_reason: "insufficient_points"` — **never** `0`. A zero delta would assert "nothing changed", which is a different claim from "we cannot tell".

---

### A.8 — Availability: the closed vocabulary and its REQUIRED order

The contract freezes the reason set and the reason ORDER (`metrics-query-contract.md:350-367`; code `availability.derive_availability`, `availability.py:76-92`):

```
census_gated | census_not_running | capture_off | series_absent | listener_disabled
```
and the ORDER is load-bearing:
```
postgres_census → census_not_running FIRST, then census_gated
wedge_capture   → capture_off
live_series_count <= 0 → series_absent
otherwise → live
```
Rationale in code (`availability.py:77-88`): *"the census-not-running fact is the fact that EXPLAINS the darkness"*. The provider also **asserts** the emitted reason is in the closed set (`provider.py:256-266`, A-9) — the backend refuses to widen it.

**The tool's rule: the availability block is carried VERBATIM, and the tool NEVER re-derives, re-orders, "improves", or substitutes a reason.** The tool does not read the census switch, does not inspect threads, does not probe the DB — the seam's own rule is *"the facts are INJECTED — the query package never goes looking for them itself"* (`availability.py:49-62`).

What the tool DOES add is a **derived, non-substitutive** annotation:
- `data_present: bool` — `live_series_count > 0`.
- `dark_with_live_series: bool` — `state == "dark" AND live_series_count > 0`.

These do not change the reason; they stop the agent from reading "dark" as "no data" (A.9).

**The one thing the tool must NOT do:** synthesize a reason the provider did not emit. If `availability.reason` is absent (a live family), the tool leaves it absent. If a future reason appears, the tool carries it through verbatim — the tool does not validate the reason against the closed set (that is the BACKEND's assertion, `provider.py:256-266`); the tool's contract is *"carry it, never reinterpret it"*.

---

### A.9 — The two "not-empty" distinctions (dark-vs-empty, and failed-read-vs-empty)

These are ONE step apart and both must be visible, or the agent will be misled.

#### (i) `dark` is NOT "no data" — the production defect F1/F3/F6

`docs/architecture/metrics-promote-disclosure.md:82-98` records: **THREE of the SIXTEEN families report `dark` WHILE PUBLISHING LIVE SERIES — AND THAT IS A PRODUCTION DEFECT, NOT A DEV ARTEFACT.** F1 (9 live series), F3, and F6 (15 live series) all report `dark`; the causes are the two out-of-lane defects R-i / R-ii (`capture_active` never wired at `api.py:2528-2533`; the census `gated` property INVERTED at `services/metrics_census.py:323-335`).

**⇒ A tool MUST NOT treat `availability.state == "dark"` as "no data"** (`research-metrics-surface.md:551`).

**The tool's presentation:**
- Carry `availability` **verbatim**.
- Add the derived `data_present` and `dark_with_live_series`.
- When any returned family has `dark_with_live_series: true`, append a **generic** caveat to `caveats[]`:
  > *"1 family reports availability.state='dark' (reason='capture_off') while publishing live series (live_series_count=9). 'dark' is a hint about the SOURCE, not an absence of data — the series below are real. A known door defect (R-ii) makes some families report dark unconditionally; its cause is owned by another lane."*

  The caveat is **generic on purpose**: the tool asserts a *fact it can verify at runtime* (dark + live series), and does NOT assert a *cause it cannot* (which defect is responsible). The specific defect attribution lives in the docs and in the open R-i/R-ii lanes — a shipped tool string that hard-codes "R-ii" would go stale the moment those lanes land.

#### (ii) A FAILED REGISTRY READ is not "no data" either — `registry_read_ok`

`provider.registry_read_ok` (`provider.py:98-107`) is the H5 degraded flag: **`False` only when the registry read RAISED** (`provider.py:152-160` — `_metrics()` catches, sets the flag false, logs, returns `[]`). A successful read of an EMPTY registry leaves it `True`. The door publishes it as the **4th member of the `activation` block** (`envelope.py:56-71`, member at `:70`). The FE's mirror is landing in this lane.

**The tool's presentation (the flag name and its exact placement, in all three tools):**

| Where | What | Source |
|---|---|---|
| `activation.registry_read_ok` (top level, all three tools) | the **frozen** activation member, echoed VERBATIM | `envelope.py:70` |
| **`read_status.registry_read_ok`** (top level, all three tools — the tool-layer mirror) | the **same name, the same value** | derived |
| `read_status.mode` | `"ok"` \| `"degraded"` | derived: `"degraded"` iff `registry_read_ok` is false |
| `read_status.note` | `null` when ok; a short string when degraded | derived |

```jsonc
"read_status": {
  "registry_read_ok": false,
  "mode": "degraded",
  "note": "The metrics registry read FAILED — the payload below is INCOMPLETE or UNKNOWN, not 'no data'. Treat every count in this result as unreliable."
}
```

**The behavioural rule (the part that matters):** an EMPTY payload means different things depending on `registry_read_ok`:

| `registry_read_ok` | payload | The tool says |
|---|---|---|
| `true` | empty | *"no matching series (the registry read succeeded)"* — a real absence |
| `false` | empty | *"the registry read FAILED — the result is unknown, NOT empty"* — via `read_status` + a `caveats[]` entry |

**The FE/tool alignment (same concept, same vocabulary).** The FE currently treats a 200-empty body as the empty state (`frontend/src/app/pages/metrics/metrics.component.ts:118-129`: *"A failed catalog load is NOT an empty state: the door being unreachable is an error"*) — but a **200** body whose registry read raised is **indistinguishable from an empty catalog** unless `activation.registry_read_ok` is consulted. The FE mirror fix is therefore: set the FE's `catalogError` when `activation.registry_read_ok === false`, so the empty panel (`showEmptyPanel`) does NOT fire. **The vocabulary is shared:** the FE's flag name and the tool's flag name are both `registry_read_ok`, and the tool's derived `mode`/`note` mirror the FE's error-banner/empty-panel distinction one-for-one. The tool does not invent a second word for the same concept.

---

### A.10 — Cardinality: the `open` class renders `labels: null` and is never value-enumerated

**The frozen rule** (`metrics-query-contract.md:150-153`, `:310-311`): `cardinality_class ∈ {closed, open, none}`; **`open`** declares label keys WITHOUT a frozen value set → renders `labels: null` and is **never value-enumerated**.

The provider enforces it: `_declared_labels` returns `None` unless the class is closed (`provider.py:193-202`), and `_cardinality_class` derives the class (`provider.py:182-191`).

**The tool's presentation:**

| Class | `metrics_families` name entry | `metrics_snapshot` row | `metrics_history` row |
|---|---|---|---|
| `closed` | `labels: {"stat": ["max","p50","p95"]}` (the declared value space) | `labels: {"stat": "p95"}` (one observed combination) | `labels: {"stat": "p95"}` |
| `open` | `labels: null` + `cardinality_class: "open"` — **the tool NEVER expands the value space** | `labels: {…}` (the OBSERVED combination) + `cardinality_class: "open"` | `labels: {…}` + `cardinality_class: "open"` |
| `none` | `labels: null` + `cardinality_class: "none"` | `labels: {}` | `labels: {}` |

**The distinction the tool must not blur:** for an `open` metric, the *catalog* renders `labels: null` (the declared space is unknown and is NOT enumerated), while a *live row* carries its observed label combination — because series identity IS `(name, labels)` (AM-1, `metrics-query-contract.md:113-120`) and the ring key uses the registry's canonical `label_key` serialization (`checkpoint_metrics.py:485`). To keep that distinction visible to the agent, the tool:
1. **NEVER enumerates** an `open` metric's label values — it does not call `snapshot()` with an open name and present the resulting value set as if it were the declared space;
2. carries `cardinality_class` on every snapshot/history row (derived from the catalog) so the agent can tell "this label value is one OBSERVED instance" from "this is the DECLARED space";
3. keeps the frozen field name `labels` (no renaming to `observed_labels`) and adds the class as a sibling member.

**The AM-2 guard counters** (`ensemble_metrics_registry_measurements`, `ensemble_metrics_cap_rejected_total`, `ensemble_metrics_invalid_label_total`) are the door's own backstops; the tool inherits them by reading published state and never re-derives label spaces.

---

### A.11 — House mechanics this design must satisfy (accounting, not a plan)

| # | Site | What this lane must do |
|---|---|---|
| 1 | NEW `daemon/tools/metrics_read.py` | module docstring with the **two-layer access posture** doctrine + "tools NEVER raise" (`wedge_capture.py:1-40` / `instance_read.py:1-65`) |
| 2 | `METRICS_CATEGORY_KEY = "metrics-read"` | module constant, byte-identical in all 5 places it appears |
| 3 | `CATEGORY_NAME` + `CATEGORY_DOC` | `wedge_capture.py:64-65` pattern; `CATEGORY_DOC` lists all three tools + the dark-vs-empty doctrine + the `metrics_snapshot`≠Agent-Snapshot disambiguation |
| 4 | `METRICS_AUTHORIZED_AGENTS` + `_is_metrics_authorized(agent_id)` | ONE shared helper, fail-closed on blank / non-`str` (`wedge_capture.py:103-112`) |
| 5 | `_refusal(label, token, message)` + `_error(msg)` | house envelopes (`instance_read.py:215-226`) |
| 6 | `create_metrics_read_tools(manager, *, current_instance_id="", agent_id="", agent_tag=None)` | factory signature parity (`wedge_capture.py:149-155`) |
| 7 | per-tool `BaseModel` `args_schema` | `Annotated[..., Field(description=…)]` (A.3) |
| 8 | `@register_tool_category("metrics-read")` **ABOVE** `@tool(args_schema=…)` | order is load-bearing — reversed, the privileged strip FAILS OPEN (`_tool_registry.py:621-646`; pinned `test_notify_user_registration.py:90-115`) |
| 9 | one-line tool docstring + `tool._full_doc_` | `wedge_capture.py` / `notify_user.py:538` pattern |
| 10 | **body gate is the FIRST statement** of every tool body | `wedge_capture.py:192-199`; the notify pack pins gate-ordering (`:226-239`) |
| 11 | `PRIVILEGED_TOOL_CATEGORIES` — append `"metrics-read"` **at the END** of the literal + a doctrine comment block | `_tool_registry.py:357-618`; append after `:617` |
| 12 | `CATEGORY_MODULES["metrics-read"] = "daemon.tools.metrics_read"` | `_tool_registry.py:969-1260`; the value MUST be a `daemon.tools.*` path |
| 13 | `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` — add the 3 names | `_tool_registry.py:23+` / `:1262+` |
| 14 | import the factory in `daemon/tools/instance.py` | imports block `:284-298` |
| 15 | `create_metrics_read_tools_if_available(manager, …)` wrapper | `create_board_tools_if_available` pattern (`instance.py:2667-2699`) — returns `[]` when the manager has no provider |
| 16 | **`tools.extend(...)` BEFORE `create_help_tool` (`:7133`), `scan_tools_for_full_docs` (`:7138`), `_apply_tool_filter` (`:7141`)** | a later extend is invisible to the help tool or **bypasses the privileged strip** |
| 17 | `agents/{maintenancer,devops,wanderer}/meta.json` — append `"metrics-read"` to `tools.allow` **at the END** | the allow entry is the **CATEGORY ID only** — a literal tool name bypasses the category gate (`_tool_registry.py:559-560`) |
| 18 | `agents/{maintenancer,devops,wanderer}/tools_note.md` | add the category section (wanderer's is 22 lines, prose only) |
| 19 | **NO audit ledger** | R5; `instance_read.py:62-64` — *"a read has no side effect and writes NO audit row"* |
| 20 | **NO `INNATE_SKILL_TOOL_CATEGORIES`** entry | the expander refuses privileged categories (`instance.py:228-229`) |
| 21 | **NO `TOOL_REQUIRED_AGENTS`** row | the category spawns no agent (`_auth.py:35-51`) |
| 22 | **NO `DYNAMIC_TOOL_PREFIXES`** entry | that namespace is for runtime-discovered toolsets (`mcp_`, `plane_`) |
| 23 | **NO kill-switch** | D-UX-15 — a read has no side effect; the off lever is removing the allow entry (D-UX-15) |
| 24 | **SAME-PR pin fan-out**: 10 files / 16 assertion sites (5 `EXPECTED_PRIVILEGED_SET` + 5 inline `frozenset` + 5 count pins 16→17 + 1 ari trailing-entry pin) | `_tool_registry.py:314-356`; the count pins are 16→**17**; the ari trailing pin is **unaffected** (the metrics category is not granted to ari) |
| 25 | NEW `tests/unit/test_metrics_read_registration.py` | model on `test_wedge_capture_registration.py` (303 L) — 10 legs + the A13 attribute/default-deny/fail-closed legs |
| 26 | `tests/unit/tools/test_frozen_tool_name_discovery.py` | the bidirectional drift pin — the 3 names and the source closure land in the SAME commit (`_tool_registry.py:1285-1288`) |
| 27 | shell packs + `.agents/tester/PACKS.md` rows | optional but house-expected (the notify pack's 8 legs over 4 files is the model) |

**Re-derive the pin set by grepping, never from prose** (`_tool_registry.py:347-351`):
```
grep -rn "PRIVILEGED_TOOL_CATEGORIES == frozenset" tests/ --include=*.py
grep -rn "len(PRIVILEGED_TOOL_CATEGORIES)" tests/ --include=*.py
```

---

### A.12 — TWO PERSONA EXAMPLES (real JSON the tool returns)

The values below are grounded in the **real** family ids and metric names (`daemon/services/metrics_query/wiring/{w1,w2,w3}.py`) and the real frozen vocabularies (`wiring/vocab.py`). The **counts** are observation-dependent (`metrics-promote-disclosure.md:53-57`: *"The live count is observation-dependent and NOT statically derivably"*) and are drawn from the fenced-dev observation; the **shapes, field names, and vocabulary** are exact.

---

#### PERSONA (a) — the **maintenancer** diagnosing a slow instance

*Situation:* the maintainer is told an instance is "slow". It has `instance_read` (fleet census) and `ens-db` (pool status) but **no native view of the event-loop lag** — the research records that `readyz` components, `grace_active`, `leak_signal`, `pool_state`, `resume_hold` and loop lag live *"exclusively on HTTP/loopback/log surfaces that no agent tool wraps"* (`research-metrics-surface.md:563`).

**Call 1 — `metrics_families(family="F1", details=true)`**

```json
{
  "tool": "metrics_families",
  "read_status": {
    "registry_read_ok": true,
    "mode": "ok",
    "note": null
  },
  "schema_version": 1,
  "vocabulary_version": 2,
  "generated_at_utc": "2026-11-03T01:12:44.902113+00:00",
  "restart_epoch": "9f2c7c1e0a3b4d5e6f708192a3b4c5d6",
  "activation": {
    "listener_enabled": true,
    "census_enabled": false,
    "query_api": "auth-gated-always-on",
    "registry_read_ok": true
  },
  "query": {
    "family": "F1",
    "details": true,
    "only_with_data": false
  },
  "matched_count": 1,
  "returned_count": 1,
  "dropped_count": 0,
  "more_available": false,
  "truncated": false,
  "narrow_hint": null,
  "caveats": [
    "F1 reports availability.state='dark' (reason='capture_off') while publishing live series (live_series_count=9). 'dark' is a hint about the SOURCE, not an absence of data — the series below are real. A known door defect (R-ii) makes some families report dark unconditionally; its cause is owned by another lane."
  ],
  "families": [
    {
      "id": "F1",
      "role": "chart",
      "source_idiom": "wedge_capture",
      "durability": "process",
      "live_series_count": 9,
      "cardinality_contribution": 9,
      "availability": {
        "state": "dark",
        "reason": "capture_off"
      },
      "data_present": true,
      "dark_with_live_series": true,
      "name_count": 4,
      "names": [
        {
          "name": "ensemble_loop_lag_seconds",
          "kind": "gauge",
          "cardinality_class": "closed",
          "labels": {
            "stat": ["max", "p50", "p95"]
          },
          "monotonic": false,
          "chart_role": "line",
          "buckets": null,
          "help": "Event-loop lag quantiles in seconds, computed by the probe's 300-sample rolling window (nearest-rank). NOT a histogram: the quantiles are computed, not bucketed."
        },
        {
          "name": "ensemble_loop_lag_samples",
          "kind": "gauge",
          "cardinality_class": "none",
          "labels": null,
          "monotonic": false,
          "chart_role": "line",
          "buckets": null,
          "help": "The number of lag samples the probe's rolling window currently holds."
        },
        {
          "name": "ensemble_loop_lag_overflow_total",
          "kind": "counter",
          "cardinality_class": "none",
          "labels": null,
          "monotonic": true,
          "chart_role": "step",
          "buckets": null,
          "help": "The number of lag samples the probe clamped into its top bucket."
        },
        {
          "name": "ensemble_loop_lag_probe_state",
          "kind": "gauge",
          "cardinality_class": "closed",
          "labels": {
            "state": ["initializing", "ok", "stopped", "unknown"]
          },
          "monotonic": false,
          "chart_role": "line",
          "buckets": null,
          "help": "The lag probe's state vector (1 for the current state, 0 for the others). The probe is dark when the wedge capture is off."
        }
      ]
    }
  ]
}
```

**How the maintainer should read it.** The family is `dark` with reason `capture_off` — but `dark_with_live_series: true` and `live_series_count: 9` say the loop-lag probe **is publishing**. If the maintainer stopped at `availability.state == "dark"` they would wrongly conclude "no loop-lag data" and fall back to `bash`-curling `/readyz` (the uncontracted workaround the census names). The actionable content is the **declared label space** of `ensemble_loop_lag_seconds`: `stat ∈ {p50, p95, max}` — that is exactly the selector vocabulary for the next call. `ensemble_loop_lag_probe_state{state=…}` (one-hot) tells the maintainer whether the probe itself is `ok` or `stopped`, and `ensemble_loop_lag_overflow_total` is a **counter** (renders STEP — a linear interpolation would assert values never observed).

**Call 2 — `metrics_history(series=["ensemble_loop_lag_seconds{stat=p95}", "ensemble_loop_lag_seconds{max}"], window_s=120, max_points=120, top_n=5, compare=true)`**

```json
{
  "tool": "metrics_history",
  "read_status": {
    "registry_read_ok": true,
    "mode": "ok",
    "note": null
  },
  "schema_version": 1,
  "vocabulary_version": 2,
  "generated_at_utc": "2026-11-03T01:12:44.902113+00:00",
  "restart_epoch": "9f2c7c1e0a3b4d5e6f708192a3b4c5d6",
  "activation": {
    "listener_enabled": true,
    "census_enabled": false,
    "query_api": "auth-gated-always-on",
    "registry_read_ok": true
  },
  "query": {
    "family": null,
    "series": [
      "ensemble_loop_lag_seconds{stat=p95}",
      "ensemble_loop_lag_seconds{max}"
    ],
    "names": null,
    "window_s": 120,
    "max_points": 120,
    "top_n": 5,
    "compare": true
  },
  "window": {
    "from_utc": "2026-11-03T01:10:44.902113+00:00",
    "to_utc": "2026-11-03T01:12:44.902113+00:00",
    "interval_s": 15.0,
    "interval_s_source": "observed"
  },
  "compare_window": {
    "from_utc": "2026-11-03T01:08:44.902113+00:00",
    "to_utc": "2026-11-03T01:10:44.902113+00:00"
  },
  "last_sample_age_s": 9.4,
  "stale": false,
  "effective_interval_s": 15.0,
  "matched_count": null,
  "returned_count": 2,
  "dropped_count": null,
  "more_available": false,
  "truncated": false,
  "narrow_hint": null,
  "caveats": [],
  "series": [
    {
      "name": "ensemble_loop_lag_seconds",
      "labels": {
        "stat": "max"
      },
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "closed",
      "downsampled": false,
      "points": [
        [1793668244, 0.0041],
        [1793668259, 0.0044],
        [1793668274, 0.0089],
        [1793668289, 0.0187],
        [1793668304, 0.0241],
        [1793668319, 0.0226],
        [1793668334, 0.0198],
        [1793668349, 0.0154]
      ],
      "delta": 0.0113,
      "prev_delta": 0.0009,
      "delta_change": 0.0104,
      "delta_reason": null
    },
    {
      "name": "ensemble_loop_lag_seconds",
      "labels": {
        "stat": "p95"
      },
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "closed",
      "downsampled": false,
      "points": [
        [1793668244, 0.0022],
        [1793668259, 0.0025],
        [1793668274, 0.0047],
        [1793668289, 0.0093],
        [1793668304, 0.0128],
        [1793668319, 0.0119],
        [1793668334, 0.0101],
        [1793668349, 0.0079]
      ],
      "delta": 0.0057,
      "prev_delta": 0.0004,
      "delta_change": 0.0053,
      "delta_reason": null
    }
  ]
}
```

**How the maintainer should read it.** `stale: false` and `last_sample_age_s: 9.4` (≈ the 15 s observed spacing) say the trend is live, not frozen; `restart_epoch` is the boot id, so if it changed between calls the ring was lost and the "trend" is a fresh window. The **shape** is the story: p95 lag climbed from ~2 ms to ~13 ms and is already decaying — but `delta_change` (+5.3 ms vs the previous comparable window) says the excursion is **new**, not a steady state. `downsampled: false` confirms the points are raw 15 s observations (the default `max_points=120` was above the 8 raw points), so `delta` is an observation-level change. The lag peaked at ~24 ms max — below the wedge-capture `sustained_loop_lag` trigger (5.0 s) by two orders of magnitude, so this is a slowdown, not a wedge. Next move: widen `window_s` to 3600 and check `ensemble_readiness_failure_streak` (F9) for a matching readiness streak.

---

#### PERSONA (b) — the **wanderer** on a patrol sweep

*Situation:* the wanderer is the "read-only investigation and research specialist" (`agents/wanderer/meta.json:4`) doing a routine sweep. The census records its gap: *"Can describe one instance deeply; **cannot natively answer 'how sick is the fleet'**"* — no fleet census (`instance_read` is maintenancer-gated), no daemon logs, no pool status. It does have `bash` (which can curl `/livez` and `/readyz` — "powerful but uncontracted").

**Call 1 — `metrics_families()`** (the whole catalog, `details=false`)

```json
{
  "tool": "metrics_families",
  "read_status": {
    "registry_read_ok": true,
    "mode": "ok",
    "note": null
  },
  "schema_version": 1,
  "vocabulary_version": 2,
  "generated_at_utc": "2026-11-03T02:41:07.318774+00:00",
  "restart_epoch": "9f2c7c1e0a3b4d5e6f708192a3b4c5d6",
  "activation": {
    "listener_enabled": true,
    "census_enabled": false,
    "query_api": "auth-gated-always-on",
    "registry_read_ok": true
  },
  "query": {
    "family": null,
    "details": false,
    "only_with_data": false
  },
  "matched_count": 16,
  "returned_count": 16,
  "dropped_count": 0,
  "more_available": false,
  "truncated": false,
  "narrow_hint": null,
  "caveats": [
    "3 families report availability.state='dark' while publishing live series (F1=9, F3=6, F6=15 series). 'dark' is a hint about the SOURCE, not an absence of data. A known door defect (R-ii) makes some families report dark unconditionally; its cause is owned by another lane."
  ],
  "families": [
    {
      "id": "F0.3",
      "role": "stat",
      "source_idiom": "registry",
      "durability": "durable",
      "live_series_count": 8,
      "cardinality_contribution": 8,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_resource_spine_tables_accepting_rows", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_resource_spine_rows_written_total", "kind": "counter", "cardinality_class": "none" }
      ]
    },
    {
      "id": "F1",
      "role": "chart",
      "source_idiom": "wedge_capture",
      "durability": "process",
      "live_series_count": 9,
      "cardinality_contribution": 9,
      "availability": { "state": "dark", "reason": "capture_off" },
      "data_present": true,
      "dark_with_live_series": true,
      "name_count": 4,
      "names": [
        { "name": "ensemble_loop_lag_seconds", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_loop_lag_samples", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_loop_lag_overflow_total", "kind": "counter", "cardinality_class": "none" },
        { "name": "ensemble_loop_lag_probe_state", "kind": "gauge", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F3",
      "role": "stat",
      "source_idiom": "wedge_capture",
      "durability": "durable",
      "live_series_count": 6,
      "cardinality_contribution": 6,
      "availability": { "state": "dark", "reason": "capture_off" },
      "data_present": true,
      "dark_with_live_series": true,
      "name_count": 2,
      "names": [
        { "name": "ensemble_wedge_capture_total", "kind": "counter", "cardinality_class": "closed" },
        { "name": "ensemble_wedge_capture_write_total", "kind": "counter", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F4",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 10,
      "cardinality_contribution": 10,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_db_pool_native", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_db_pool_requests_errors_total", "kind": "counter", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F5",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 4,
      "cardinality_contribution": 4,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_db_pool_checked_out", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_db_pool_waiting_clients", "kind": "gauge", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F6",
      "role": "chart",
      "source_idiom": "postgres_census",
      "durability": "process",
      "live_series_count": 15,
      "cardinality_contribution": 15,
      "availability": { "state": "dark", "reason": "census_gated" },
      "data_present": true,
      "dark_with_live_series": true,
      "name_count": 4,
      "names": [
        { "name": "ensemble_pg_backends", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_pg_idle_in_txn_oldest_seconds", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_pg_leaked_txn", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_pg_census_last_success_timestamp_seconds", "kind": "gauge", "cardinality_class": "none" }
      ]
    },
    {
      "id": "F7",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 0,
      "cardinality_contribution": 0,
      "availability": { "state": "dark", "reason": "series_absent" },
      "data_present": false,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_checkpoint_op_bytes", "kind": "histogram", "cardinality_class": "closed" },
        { "name": "ensemble_checkpoint_slow_ops_total", "kind": "counter", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F8",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 3,
      "cardinality_contribution": 3,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_checkpoint_blobs_table_bytes", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_checkpoint_table_bytes", "kind": "gauge", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F9",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 14,
      "cardinality_contribution": 14,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 4,
      "names": [
        { "name": "ensemble_readiness_probe_total", "kind": "counter", "cardinality_class": "closed" },
        { "name": "ensemble_readiness_grace_active", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_readiness_failure_streak", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_readiness_component_state", "kind": "gauge", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F10",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "durable",
      "live_series_count": 6,
      "cardinality_contribution": 6,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 3,
      "names": [
        { "name": "ensemble_job_queue_depth", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_dead_letter_items", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_job_queue_items_created_total", "kind": "counter", "cardinality_class": "none" }
      ]
    },
    {
      "id": "F11",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 8,
      "cardinality_contribution": 8,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 4,
      "names": [
        { "name": "ensemble_workers_total", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_workers_busy", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_worker_pool_wakeup_efficiency", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_worker_pool_events_total", "kind": "counter", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F12",
      "role": "stat",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 17,
      "cardinality_contribution": 17,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_sweep_lane_outcome_total", "kind": "counter", "cardinality_class": "closed" },
        { "name": "ensemble_sweep_lane_duration_seconds", "kind": "gauge", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F13",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 19,
      "cardinality_contribution": 19,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 4,
      "names": [
        { "name": "ensemble_instance_status_count", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_instance_liveness_count", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_waiting_children_count", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_waiting_children_max_age_seconds", "kind": "gauge", "cardinality_class": "none" }
      ]
    },
    {
      "id": "F17",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 10,
      "cardinality_contribution": 10,
      "availability": { "state": "live" },
      "data_present": true,
      "dark_with_live_series": false,
      "name_count": 2,
      "names": [
        { "name": "ensemble_mcp_server_state", "kind": "gauge", "cardinality_class": "closed" },
        { "name": "ensemble_mcp_failures_total", "kind": "counter", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F18",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 0,
      "cardinality_contribution": 0,
      "availability": { "state": "dark", "reason": "series_absent" },
      "data_present": false,
      "dark_with_live_series": false,
      "name_count": 3,
      "names": [
        { "name": "ensemble_mcps_gateway_sessions", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_mcps_gateway_tombstones", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_mcps_gateway_audit_total", "kind": "counter", "cardinality_class": "closed" }
      ]
    },
    {
      "id": "F21",
      "role": "chart",
      "source_idiom": "registry",
      "durability": "process",
      "live_series_count": 0,
      "cardinality_contribution": 0,
      "availability": { "state": "dark", "reason": "series_absent" },
      "data_present": false,
      "dark_with_live_series": false,
      "name_count": 3,
      "names": [
        { "name": "ensemble_journal_file_bytes", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_journal_history_entries", "kind": "gauge", "cardinality_class": "none" },
        { "name": "ensemble_upgrade_release_current", "kind": "gauge", "cardinality_class": "none" }
      ]
    }
  ]
}
```

**How the wanderer should read it.** This one call answers *"how sick is the fleet"* — the question the census says it currently cannot answer. The **shape** of the sweep is: 10 families live, 3 dark-but-publishing (F1/F3/F6 — the known door defect, and the caveat says so), 3 genuinely absent (F7 `series_absent`, F18 `series_absent`, F21 `series_absent` — the last because the journal family needs an install dir). The **decision-relevant** rows for a sweep are F13 (instance status/liveness counts), F10 (queue depth), F9 (readiness), F6 (PG backends / idle-in-txn) and F11 (worker pool). `source_idiom` and `durability` tell the wanderer which numbers survive a restart (`durable` = DB-backed) and which are process-local (`process` = in-RAM, reset on restart) — F1's lag is `process`, so a `restart_epoch` change wipes it.

**Call 2 — `metrics_snapshot(family="F10")`** (the queue-depth read)

```json
{
  "tool": "metrics_snapshot",
  "read_status": {
    "registry_read_ok": true,
    "mode": "ok",
    "note": null
  },
  "schema_version": 1,
  "vocabulary_version": 2,
  "generated_at_utc": "2026-11-03T02:41:09.551002+00:00",
  "restart_epoch": "9f2c7c1e0a3b4d5e6f708192a3b4c5d6",
  "activation": {
    "listener_enabled": true,
    "census_enabled": false,
    "query_api": "auth-gated-always-on",
    "registry_read_ok": true
  },
  "query": {
    "family": "F10",
    "name_prefix": null,
    "top_n": 20
  },
  "effective_interval_s": 15.0,
  "matched_count": 6,
  "returned_count": 6,
  "dropped_count": 0,
  "more_available": false,
  "truncated": false,
  "narrow_hint": null,
  "caveats": [],
  "series": [
    {
      "name": "ensemble_job_queue_depth",
      "labels": { "admission_state": "QUEUED" },
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "closed",
      "value": 14,
      "last_publish_age_s": 4.1,
      "stale": false
    },
    {
      "name": "ensemble_job_queue_depth",
      "labels": { "admission_state": "ACTIVE" },
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "closed",
      "value": 6,
      "last_publish_age_s": 4.1,
      "stale": false
    },
    {
      "name": "ensemble_job_queue_depth",
      "labels": { "admission_state": "DONE" },
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "closed",
      "value": 231,
      "last_publish_age_s": 4.1,
      "stale": false
    },
    {
      "name": "ensemble_job_queue_depth",
      "labels": { "admission_state": "DEAD" },
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "closed",
      "value": 2,
      "last_publish_age_s": 4.1,
      "stale": false
    },
    {
      "name": "ensemble_dead_letter_items",
      "labels": {},
      "kind": "gauge",
      "monotonic": false,
      "cardinality_class": "none",
      "value": 2,
      "last_publish_age_s": 4.1,
      "stale": false
    },
    {
      "name": "ensemble_job_queue_items_created_total",
      "labels": {},
      "kind": "counter",
      "monotonic": true,
      "cardinality_class": "none",
      "value": 18422,
      "last_publish_age_s": 4.1,
      "stale": false
    }
  ]
}
```

**How the wanderer should read it.** `last_publish_age_s: 4.1` (≈ a third of the 15 s observed interval) with `stale: false` says these are fresh, not leftovers. The `admission_state` label is the **four-value** `AdmissionState` (`QUEUED` / `ACTIVE` / `DONE` / `DEAD`) — the wanderer now knows the fleet has 14 queued, 6 active, 2 dead. Two `DEAD` items agreeing with `ensemble_dead_letter_items: 2` is a **cross-family consistency check** the agent can do in one glance — the same value from two independently-produced families. `ensemble_job_queue_items_created_total` is a **counter** (`monotonic: true`): it only moves forward, and it is meaningless as a "current" number — the agent must difference it over time (that is exactly what `metrics_history`'s `delta` does). Next step for the sweep: `metrics_history(family="F13")` to see which instances are `WAITING_CHILDREN` and how old they are (`ensemble_waiting_children_max_age_seconds`).

---

## B. `metrics:read` SCOPE-SPLIT DESIGN

### B.1 — Re-deriving D6 for an IN-PROCESS tool

**D6 verbatim** (`central-metrics-v1/decisions.md:641-658`):

> **D6 — Deferred scope split (`metrics:read`) — NAMED COMMITMENT (leader LR-1)**
> **Status: NAMED COMMITMENT, not built now.** v1 keeps `maintenance:read` (zero amendment; the scope-manifest cage is trivially green). This section is **binding guidance for the deferred lanes** (deferred item #2 agent tools, deferred item #3 Grafana) — it is not a "revisit later".
> **Retrofit map (4-line + manifest, cheap — AM-20):**
> 1. **1 taxonomy line** — add `metrics:read` to the scope taxonomy (`daemon/auth/scopes.py:60-101`).
> 2. **1 manifest line** — family scope becomes `"/api/metrics": "maintenance:read|metrics:read"`.
> 3. **4 decorator lines** — the three new query routes declare `metrics:read`; the shipped text route keeps `maintenance:read`.
> 4. **Cage note** — the scope cage is a **subset check**, so the three routes stay inside the pinned set; `SESSION_OPERATOR_SCOPES` auto-grants, so **no operator re-grant is needed**.
> **Trigger for the retrofit (the recorded condition):** a non-operator machine lane reads the door in this lane's lifetime (e.g. a Grafana / deferred-#3 token) ⇒ mint `metrics:read` at that point.
> **Until then:** v1 stays on `maintenance:read` and the query-API docs disclose the over-grant (checkpoint-cleanup-execute also rides `maintenance:read`).

The **public restatement** in the shipped API doc (`docs/architecture/metrics-query-api.md:286-292`) is the decisive one:

> *"**The trigger for the retrofit (the recorded condition):** a non-operator machine lane reads the door in this lane's lifetime (a Grafana token, or **the deferred agent-tool door**) ⇒ mint `metrics:read` at that point. … **This section is binding guidance for the deferred lanes** (the deferred agent-tool door and the deferred Grafana layer). It is not a 'revisit later'."*

**The gap the retrofit map does not cover.** Every one of D6's four steps is **HTTP-shaped**: a taxonomy constant, a manifest string, a route decorator, a cage note. An **in-process** tool has **no route, no decorator, no AuthContext, no request**. And the seam it reads is **unscoped by construction**:

- `provider.py:26-43` imports **nothing** from `daemon.auth` — only `daemon.checkpoint_metrics`, the `metrics_query` submodules and stdlib;
- the Protocol is deliberately web-framework-free (`protocol.py:3-8`: *"no `Request`, no FastAPI type, no web-framework import appears in any signature"*);
- the scope check lives **exclusively in the router** (`routers/metrics.py:143`, `:560`, `:604`, `:673`) and the fail-closed 503 lives **exclusively in the router** (`routers/metrics.py:121-137`);
- the provider is stored on `app.state.metrics_query` with **no auth wrapper** (`api.py:2533`).

⇒ **Executing D6 literally changes NOTHING about the tool path.** The tool would remain gated by nothing at all on the scope axis. **The tool layer must supply its own gate.** The house already has that gate — the privileged-category + in-body-`agent_id` two-layer pattern (`wedge_capture.py:1-22`, `instance_read.py:51-64`) — and it is the pattern every privileged read category uses.

**And the house already has the precedent that agents bypass HTTP scope.** `daemon/auth/scopes.py:78-84`, on the `prompts:submit` scope: *"the agent's internal lane bypasses HTTP scope entirely — see D10"*. The tool path IS an internal lane.

---

### B.2 — The scope model

**What `metrics:read` means:** *"read the daemon's metric catalog, current values and windowed trend — and nothing else."* It is a **read-only, metrics-only** scope. It is deliberately **narrower** than `maintenance:read`, which the contract itself discloses: *"`maintenance:read` ALSO confers checkpoint-cleanup-EXECUTE capability (`daemon/routers/maintenance.py`)"* (`metrics-query-contract.md:404-405`) — a caller who can read the metrics can also trigger a checkpoint cleanup. Minting `metrics:read` gives machine lanes a scope that **cannot** clean checkpoints.

**Where it is declared:** `daemon/auth/scopes.py` → `SCOPE_TAXONOMY` (the block at `:60-101`). One line:
```python
"metrics:read",
```
**Consequences that are automatic:** `SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY` (`scopes.py:111`) ⇒ the **browser/operator lane auto-carries it** — no operator re-grant, no session-row migration. The bearer (machine) lane carries **exactly** the scopes minted onto its token row (`scopes.py:44-48`) ⇒ a Grafana-style machine token can now be minted with `metrics:read` **alone** and read the metrics door **without** checkpoint-cleanup power.

**How the HTTP door's `maintenance:read` coexists:** it is **re-pointed to an OR-set, not replaced**. The route decorator is `scope_required(...)` / `require_scope(*required)`, and the enforcement is **any-of** (`daemon/auth/decorators.py:94-105`: `any(scope in ctx.scopes for scope in required)` — *"NO lane satisfies scopes by identity"*). So:

```python
# daemon/routers/metrics.py
_SCOPE_READ = "maintenance:read"
_SCOPE_READ_METRICS = "metrics:read"
# the three query routes:
dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)]
# the shipped text-exposition route KEEPS maintenance:read only:
dependencies=[scope_required(_SCOPE_READ)]
```
**`maintenance:read` is retained** (the operator lane still has it; removing it would be a breaking change to the door's grant set), and `metrics:read` is **added as an alternative** for the three query routes only. The text-exposition route (`/api/metrics`, `routers/metrics.py:143`) keeps `maintenance:read` alone — per D6 step 3 — because Prometheus text exposition is the *scraping* surface, not the query door.

---

### B.3 — How the TOOL path is gated (a gate that CANNOT be bypassed)

**The bypass hazard is explicit and house-documented.** `daemon/tools/_tool_registry.py:559-560` (D-13):

> *"Per-tool allow is REJECTED (D-13): a literal tool name in an allow list bypasses the category gate outright."*

Mechanically: `resolve_tool_filter` treats each allow entry as *"if the entry is a known category → expand to its tool names; else → treat it as a literal tool name"* (`daemon/tools/instance.py:561-568`), and the privileged strip only applies on the **empty-allow** path (`instance.py:7146-7181`). So if `agents/maintenancer/meta.json` listed `"metrics_families"` instead of `"metrics-read"`, the tool would be visible to that agent **regardless of the category gate**, and — worse — would be visible to an agent whose allow list happens to contain that literal name.

**⇒ The tool path uses the house TWO-LAYER gate, and the meta.json entries are CATEGORY IDS ONLY.**

**Layer 1 — the category gate (visibility).**
- `"metrics-read"` is appended to `PRIVILEGED_TOOL_CATEGORIES` (`_tool_registry.py:357-618`) ⇒ it is **default-denied**: the empty-allow inherit universe (e.g. `agents/watcher/meta.json` `"allow": []`) never receives it.
- `"metrics-read"` is added to `CATEGORY_MODULES` (`:969-1260`) ⇒ it resolves as a known category at boot (`daemon/registry.py:1355-1368`) and the allow entry expands to its three tool names.
- Each agent's `meta.json` carries **`"metrics-read"`** — the category id, **never** a tool name.

**Layer 2 — the in-body `agent_id` gate (authority).** Every tool body calls ONE shared helper, as the FIRST statement:

```python
METRICS_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"maintenancer", "devops", "wanderer"})

def _is_metrics_authorized(agent_id: str) -> bool:
    """Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER authorized."""
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in METRICS_AUTHORIZED_AGENTS
```
(`wedge_capture.py:84`, `:103-112`; `instance_read.py:128`, `:199-212`.)

**Why layer 2 is the unbypassable one.** The category gate governs **visibility** — which tools an agent can even see in its tool list. It does not govern **authority**, because a literal tool name in an allow list escapes it entirely. The in-body gate reads the **agent id captured at factory time** (`create_instance_tools(manager, …, agent_id=agent_id, …)` → `create_metrics_read_tools(manager, agent_id=agent_id, …)` → `caller_agent_id = agent_id or ""`) and is evaluated **inside** the tool body on **every** call. It cannot be escaped by any allow-list spelling, because the allow list never reaches it.

**The full gate chain for one `metrics_history` call:**
1. The category is **privileged** ⇒ the empty-allow universe never gets it.
2. The agent's `tools.allow` carries the **category id** ⇒ the three tools are visible only to `maintenancer` / `devops` / `wanderer`.
3. **The body gate runs first** ⇒ an unauthorized agent id (blank, `None`, a non-`str`, or a known-but-not-listed agent) is **refused before any provider read** — no data is touched, not even a count.
4. **The provider is reached only through the manager mount** (A.1), which is `None` on a test double ⇒ a typed refusal, never an exception.

**The three-layer refusal ladder** mirrors the door's 401/403/503 semantics so one parser reads them all (house envelope `_refusal(label, token, message)` → `Error: {label} REFUSED — reason={token}: {message}`):

| Condition | Token | Door analogue |
|---|---|---|
| `agent_id` blank / `None` / non-`str` | `unknown-agent` | **401** — the caller has no identity |
| `agent_id` known but not in the allow-set | `not-authorized` | **403** — authenticated but not permitted |
| `manager.metrics_query_provider is None` | `metrics-unavailable` | **503 registry_unavailable** — the substrate is not mounted |
| a malformed selector / > 50 selectors | `invalid_label_filter` | **400 invalid_label_filter** |

---

### B.4 — The auth-fail-closed posture for the tool path

**The router's fail-closed seam** (`routers/metrics.py:121-137`): `create_app` sets `app.state.auth_config` **only** when auth is enabled; its absence is the authoritative OFF signal, and `_require_auth_on(request)` runs **inside the handler body** (not as a route dependency) so it fires **after** the middleware's authentication decision and **before any registry read** (`metrics.py:72-74`). Rationale: *"the deployed default is auth-OFF on a `0.0.0.0` bind with permissive CORS, so an open route here would publish the daemon's internals to anyone who can reach the port."*

**The tool path's equivalent — and why it is DIFFERENT (not weaker).** The tool is **not a network surface**. There is no anonymous lane in-process: every tool call arrives from an agent session that already has an identity (`agent_id`), and the daemon has already decided which tools that identity may hold (the allow list, resolved at instance construction). The exposure the router's 503 protects against — *"anyone who can reach the port"* — **does not exist for the tool path**: the tool is only reachable from inside the daemon, through a tool list that was already filtered.

**⇒ The tool's fail-closed posture is the SAME PRINCIPLE, EXPRESSED ON THE SEAM THAT ACTUALLY CARRIES THE RISK:**

| Door (HTTP) | Tool (in-process) |
|---|---|
| 503 `AUTH_NOT_CONFIGURED` when auth is OFF | **not applicable** — there is no anonymous caller; the tool's "auth" is the agent-id allow-set, which is *always* evaluated |
| 401 when unauthenticated | **`unknown-agent`** refusal — blank / `None` / non-`str` `agent_id` ⇒ refused |
| 403 on a scope miss | **`not-authorized`** refusal — a known agent id outside the allow-set ⇒ refused |
| 503 `registry_unavailable` when the provider is absent | **`metrics-unavailable`** refusal — `manager.metrics_query_provider is None` |
| the gate runs **before any registry read** | **the body gate is the FIRST statement of every tool body** — before the provider is touched |

**The one invariant the door has that the tool must mirror exactly:** *"the fail-closed gate fires BEFORE the registry is read"* (`metrics.py:171-220`). The tool's body gate runs before `provider.families()` / `.snapshot()` / `.history()` is called — so an unauthorized call **never** reads the registry, never materializes a series, and never even learns whether the substrate is mounted (the `metrics-unavailable` check is *after* the authorization check). This ordering is **pinnable** (the notify pack pins gate-ordering at `tests/unit/test_notify_user_registration.py:226-239`).

**Should the tool consult the auth configuration at all?** **No.** If the tool required auth to be ON, then in an auth-OFF deployment (the deployed default on this host) the metrics tools would be dead — while the daemon's own maintainer/devops/wanderer agents would lose their only sanctioned view of the daemon's health. The agent lane is an **internal** lane, and the house's own precedent (`scopes.py:78-84`, D10) is that the agent's internal lane bypasses HTTP scope entirely. The tool's authority is the agent-id allow-set; the daemon's own boundary is the process boundary.

**A note for the record (the disclosed over-grant that D6 was minted to fix):** the tool grants an agent read access to the daemon's internal telemetry — loop lag, PG backend states, worker-pool internals, queue admission states. The house accepted the equivalent exposure for `wedge-capture` (all-thread stack dumps and per-thread CPU maps — a strictly richer artifact) with the same two-layer gate, the same three-holder set shape (`ari`/`leader`/`maintenancer` — three holders, one category), and **no audit ledger** (a read writes no row, `wedge_capture.py:30-32`). The metrics surface is strictly less sensitive than the wedge-capture surface it sits beside.

---

### B.5 — Ruling on the D6 trigger: **MET**

**The recorded condition:** *"a non-operator machine lane reads the door in this lane's lifetime (e.g. a Grafana / deferred-#3 token) ⇒ mint `metrics:read` at that point."* The shipped API doc restates it and **names this lane explicitly**: *"a Grafana token, or **the deferred agent-tool door**"*.

**Ruling: the trigger is MET.** This lane makes the door readable by a **non-operator** consumer — three agents that are not operators, reading the door's substrate **in-process**, with no operator session and no operator scope. Per ruling R2 the scope split is a **binding deliverable of this lane**, not a deferral to item #3.

**Two consequences of the ruling:**

1. **The taxonomy line lands in this lane** (`daemon/auth/scopes.py:60-101`) — one line, additive, and `SESSION_OPERATOR_SCOPES` auto-grants it to the operator lane with no re-grant and no migration.
2. **The door is re-pointed to the OR-set** (`maintenance:read` OR `metrics:read`) so that a future machine lane (item #3 Grafana) can be granted a metrics-read scope that **cannot** clean checkpoints. This is the over-grant D6 was written to close.

**And the third consequence — the one the retrofit map does not say:** because the tool path bypasses the scope system entirely, **the scope split is a statement about the HTTP door's grant model, NOT the tool's gate.** The tool's gate is the two-layer house pattern (B.3). Both must exist, and they are different mechanisms for different doors. Saying otherwise — i.e. claiming the tool is "scope-gated" — would be a false claim in the shipped docs.

---

### B.6 — Blast radius on the scope-manifest pins

**The three pins this lane touches, and their exact current values (verified at this tip, not from the research prose):**

| Pin | File:line | Current value | Effect of this lane |
|---|---|---|---|
| The **family scope** | `tests/unit/test_router_scope_manifest.py:169` | `"/api/metrics": "maintenance:read"` | becomes `"/api/metrics": "maintenance:read|metrics:read"` (D6 step 2) |
| The **scope-cage** family scope | `tests/unit/metrics/test_metrics_query_scope_cage.py:147` | `assert PINNED_SCOPE_MANIFEST["/api/metrics"] == FAMILY_SCOPE` (with `FAMILY_SCOPE = "maintenance:read"` at `:43`) | `FAMILY_SCOPE` becomes `"maintenance:read\|metrics:read"` (the manifest form) — and the per-route assert at `:150-157` (`scopes == frozenset({FAMILY_SCOPE})`) must become `frozenset({"maintenance:read", "metrics:read"})` |
| The **declared-route count** | `tests/unit/test_router_scope_manifest.py:409` (`assert len(declared) == 111`) and `tests/unit/metrics/test_metrics_query_scope_cage.py:37` (`DECLARED_ROUTE_CENSUS = 111`) | **111** | **UNCHANGED — 111.** This lane adds **ZERO routes** (the tool is not a route). The mount census stays 37 and the class-manifest family count stays 33. |

**A correction to the research file, disclosed:** `research-metrics-surface.md:429` states the declared-route count is *"108 at the research base"* and that *"adding three scope-decorated routes takes it 108→111"*. The **111 already happened** — it was central-metrics-v1's own P3 change (the docstring at `test_router_scope_manifest.py:398-401` records the 108 → 111 transition). The count is **111 today** and this lane does not move it. **Any plan step that says "bump the route count" is wrong** — this lane's route-count blast radius is **zero**.

**Also unaffected:** `tests/integration/test_router_class_manifest.py:147` (`"/api/metrics": frozenset({"pd"})` — the class/pd tag) — no new route, no new class. And `tests/unit/test_router_scope_manifest.py` declares the manifest as **a set of strings declared in the test**, not a runtime registry; the taxonomy constant is `daemon/auth/scopes.py`. The manifest string form is pipe-delimited (the same convention as `"/api/mcps-gateway": "mcp:read|*"` at `:160`).

**The cage is a SUBSET check** — D6 step 4: *"the scope cage is a subset check, so the three routes stay inside the pinned set"*. Re-pointing the family scope to the OR-set and declaring both scopes on the routes keeps the routes **inside** the pinned family set (the pinned set grows with the routes' declared set). The text-exposition route keeps `maintenance:read` alone — and `maintenance:read` is inside the new family OR-set, so it also stays inside.

**The contract-doc amendment this implies (a cited, description-only amendment — NOT a machine-pinned change).** The frozen contract's **prose** names `maintenance:read` as THE scope in four places: the §1.13 activation-surface matrix rows 2–3 (`:31-32`), the §6 normative truth table (`:318-322`), the §7 door disclosure (`:379-380`, `:387`) and the AM-20 over-grant disclosure (`:404-405`). *(enumeration superseded by E-1 amendment-2 — see decisions.md Part 4: FIVE canonical line-ranges, `:387` explicitly IN.)* **None of those is inside a machine-pinned fenced block** — the pinned blocks are `activation-surfaces` (`:41-48`, six surface NAMES), `query-routes` (`:225-229`, the three route paths), `error-vocabulary` (`:235-242`, the error codes), `truth-table-invariants` (`:328-334`, the five invariant names), `availability-reasons` (`:352-358`) and `gate-pack-ids` (`:423-433`). **No pinned block carries a scope string**, so the `central_metrics_contract_doc` pack stays green.

The contract doc **has an in-place amendment precedent**: `:248` (*"it is amended here to the shipped form"*) and `:396` (*"a **description-only** amendment because this lane consumes it"*). The scope retrofit is exactly a **description-only** amendment: **no wire shape changes** (`schema_version` stays 1, `vocabulary_version` stays 2 — the two axes are untouched), **no route changes**, **no behaviour change for any existing holder** (`maintenance:read` still works on every route). The amendment records: *"the three query routes accept `maintenance:read` OR `metrics:read`; `metrics:read` is read-only and does not confer checkpoint-cleanup-execute."* See **E-1** — this is the one item this lane raises to the caller, because it touches a document the brief declares frozen. *(enumeration superseded by E-1 amendment-2 — see decisions.md Part 4: the canonical amendment set is FIVE line-ranges, `:387` is IN; guardrail (b) restated as green-WITH-the-amendment via the verbatim-preservation list.)*

---

### B.7 — Recommendation: **mint a NEW scope** (`metrics:read`)

**Pick: mint `metrics:read` as a new taxonomy entry, and re-point the three query routes to accept `maintenance:read` OR `metrics:read`.**

**Reasoning.** (1) The trigger D6 recorded is **met** and the shipped API doc names this lane as the trigger — so reuse-with-a-role-distinction would leave a recorded commitment unexecuted for the second consecutive lane. (2) The over-grant is **real and disclosed**: `maintenance:read` also confers checkpoint-cleanup-EXECUTE (`daemon/routers/maintenance.py:255`, `:279-283`; the contract's own AM-20 disclosure at `:404-405`) — a machine lane that only wants to read metrics should not be able to VACUUM the checkpoint store, and a scope split is the only mechanism in this codebase that can express that difference. (3) The cost is **four lines plus two test strings**, and `SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY` (`scopes.py:111`) means **no operator re-grant and no migration**. (4) A "role distinction" is **not expressible** in the current scope model: the grant model is a flat set of strings with **no role axis** (`scopes.py:42-52`: *"NO lane satisfies scopes by identity. Every lane — session (cookie operator) and bearer (machine) alike — satisfies a requirement only when its GRANTED scope set contains one of the required scopes"*). Trying to express "maintenance:read but read-only for agents" would either need a new scope anyway, or need a role concept the auth model does not have.

**Rejected alternative 1 — reuse `maintenance:read` with a role distinction.** Rejected on three grounds: it **cannot** be expressed (no role axis); it leaves the checkpoint-cleanup over-grant in place for every machine lane; and it contradicts the shipped public restatement of D6, which names the agent-tool door as a mint trigger. The only argument in its favour — *"no scope churn"* — is worth ~4 lines and does not survive the over-grant argument.

**Rejected alternative 2 — the tool consumes the HTTP door (loopback + auth) instead of the provider.** Rejected: it would put the tool on the network path for an in-process read, require the daemon to know its own bearer token (a secret-shaped dependency the agent lane must not hold), re-impose 401/403/503 handling on every call, and contradict D4's swappability guarantee (`decisions.md:375-377`) that the agent tools call the **SAME provider functions directly**. The tool MUST consume the provider; therefore the tool MUST carry its own gate — which is exactly why B.3 exists.

**Rejected alternative 3 — the tool is scope-gated by minting a scope onto agent identities.** Rejected: agents hold **no** AuthContext, and the house already has the precedent that the agent's internal lane bypasses HTTP scope (`scopes.py:78-84`, D10). Manufacturing scope-bearing identities for agents would be a large auth-model change for no security gain — the agent-id allow-set is the enforcement the house actually uses for privileged agent tools.

**What the scope split does NOT change:** the tool's own gate (B.3), the tool's read-only posture, the door's kill-switch truth table (the door is still never switch-gated), or any frozen wire shape. **The scope split is a GRANT-MODEL change to the HTTP door; the tool's gate is an AUTHORITY change on the in-process seam.** Both ship; they are orthogonal; the docs must say which is which.

---

## C. WATCH/ALERT DESIGN-DOC OUTLINE (design-only)

**Status: v1 is PULL-ONLY (ruling R3).** This lane ships three read tools and **no** alert machinery. This section is the **outline for a FUTURE lane's design doc** — the deliverable that lane authors as a close-out artifact. It is deliberately a set of *questions with a recommended shape*, not a plan: the plan worker for this lane must **not** plan any of it.

### C.1 — What the future lane would decide (the outline's spine)

**1. Trigger semantics.** Three candidate trigger classes, each with a different failure mode:

| Trigger class | Example | Semantics | Difficulty |
|---|---|---|---|
| **Threshold** | `ensemble_loop_lag_seconds{stat=p95} > 1.0` for ≥ 60 s | the value crosses a line and stays across it | simplest; but the frozen contract has **no threshold vocabulary** — the tool would evaluate it against published state |
| **Delta** | `delta(ensemble_job_queue_depth) > +50` in a 5-minute window | the value CHANGED materially | needs the ring's points; a delta over a downsampled window is approximate |
| **Absence / staleness** | `last_sample_age_s > 3 × effective_interval_s`, or `availability.state == "dark"` for ≥ 3 windows | the signal STOPPED arriving | the most operationally valuable class (a wedged sampler is the incident class) — and the cheapest to evaluate (the contract already exposes `stale` and `availability`) |

*Recommended shape for the future lane:* **start with absence/staleness**, because (a) the contract already freezes the exact inputs (`last_sample_age_s`, `stale`, `availability.state/reason`, `restart_epoch`), (b) it needs no new vocabulary, and (c) it covers the incident family this project keeps hitting (a wedged sampler that leaves a frozen line). Threshold and delta come later.

**2. Fit with the existing trigger/scheduling machinery.** The house's reusable pieces: the scheduler source adapter (cron-style schedules with a durable execution ledger), the self-alarm arm (`daemon/services/self_alarm_service.py`, `MIN_HORIZON_SECONDS = 1800`), and the mid-flight QA fan-out. The future lane's design doc must answer: *does the metrics watch arm a SCHEDULE (poll the metrics substrate on a cadence) or does it ride the existing event/notification machinery (react to a published-composite change)?* The first is simpler and matches the scheduler's existing durability; the second is cheaper but needs a new publish-triggered seam. **Recommendation: a schedule-driven poller** — it reuses the durable execution ledger, the deferred/coalesced delivery path (C.3), and the at-most-once invariant for free.

**3. Alert delivery surfaces.** See C.3 — exactly one primitive is reusable without widening.

**4. Idempotence / de-duplication.** A metrics watch must not re-fire on every poll while the condition holds. The scheduler's `deferred`/`coalesced` machinery already provides an at-most-once *delivery* invariant, but the future lane must also decide the **firing** invariant (fire on transition only? on a rising edge? with a cooldown?). Note the house's existing bounds vocabulary for chat: N=1/turn (≥ 60 s), M=6/rolling hour (`notify_user.py:74-97`, `:240-266`).

**5. Evaluation substrate.** A metrics watch MUST evaluate against **published registry state** (the same read-path-purity rule the door obeys: no DB read, no metric lock, no switch read — `provider.py:3-12`). It must NOT query the database for metrics — that is the door's binding clause (`metrics-query-contract.md:215-223`).

**6. Scope estimate (phases + rough size) — an estimate for the future lane, not this lane:**

| Phase | Content | Rough size |
|---|---|---|
| P0 | Evidence + contract freeze for the trigger semantics (vocabulary, firing invariant, de-dup) | 1 plan round, ~1–2 days |
| P1 | The watch model + the armed-watch substrate (a schedule row + a trigger spec) | ~3–5 days |
| P2 | The evaluator (published-state only) + the firing invariant | ~3–5 days |
| P3 | Delivery wiring (the ONE reusable primitive; see C.3) | ~2–4 days |
| P4 | The agent-facing arm/disarm tools (a SECOND privileged category, or a sibling of `metrics-read`) | ~3–5 days + a full pin ceremony |
| P5 | Gates (plan council → code council → functional → staged) | ~1 week |

**Total ≈ 3–4 engineer-weeks** — i.e. a lane comparable in size to this one, NOT a follow-on. This is the concrete reason R3 (pull-only) is the right posture for v1.

### C.2 — The three trigger classes, mapped onto what the frozen contract already gives

| Trigger class | Frozen input it would read | Gap |
|---|---|---|
| Threshold | `snapshot()` values + the family's `labels` | a **threshold vocabulary** does not exist (no `daemon/services/metrics_query/` concept of a threshold); the future lane must define one, and it must live OUTSIDE the frozen contract (the contract is frozen) — e.g. in a new service module that reads published state |
| Delta | `history()` points | the delta is computed by the consumer (the tool does it in the tool layer, D-UX-11); the future lane reuses the same computation but must decide whether a **downsampled** window's delta is acceptable (the tool surfaces a `downsampled` flag for exactly this reason) |
| Absence / staleness | `last_sample_age_s`, `stale`, `availability.state/reason`, `restart_epoch` | **none** — every input is already frozen and published |

### C.3 — The delivery primitives (researched census)

| Surface | Category | Owner | Mutating? | Reusable for a metrics watch? |
|---|---|---|---|---|
| **scheduler deferred / coalesced** | (no category — a transport) | daemon | writes Task/MQ/JobItem rows | **YES — FULLY GENERIC.** It is exactly *"the target is busy → deliver later → coalesce the repeats"*, it is agent-agnostic (the pinned caller is a schedule row, not an agent), and it is durable + at-most-once. **The ONLY immediately reusable generic delivery primitive.** `daemon/sources/adapters/scheduler.py:1111-1163` (the deferred-durable delivery + the coalesce outcome vocabulary `_COALESCE_COALESCED` / `_COALESCE_ROLLOVER` / `_COALESCE_SKIPPED` / `_COALESCE_WRITE_UNKNOWN` at `:126-136`), with the visibility surface in `daemon/services/scheduling_service.py:202-213` and `:629-698`. Bounds come from `SchedulingConfig.defer_max_count` / `defer_max_age_seconds` (defaults **16 / 86 400**) — **pydantic-settings knobs, NOT DDL** (`scheduler.py:1127-1145`) ⇒ the migration delta stays EMPTY. |
| `self_alarm` | `self-alarm` (privileged #9) | **ari** | arms a schedule row | **Mechanism generic; target HARDCODED to ari.** `SELF_ALARM_TARGET_AGENT` — a metrics watch for `maintenancer` / `devops` / `wanderer` **cannot** reuse it as-is. **Scope-widening cost:** re-gating the category + the body gate away from `ari` is a *user-visible authority change* (it would let a non-ari agent arm a schedule that fires a message) — it needs its own lane, its own pin ceremony, and a deliberate decision about whether `ari` keeps exclusivity. |
| `notify_user` | `notify-user` (privileged #16) | **ari** | writes to the user's chat | **Mechanism generic; category + body gate ari-EXACTLY** (`notify_user.py:180`, the `agent_id == "ari"` check must be the FIRST statement, pinned `test_notify_user_registration.py:226-239`). **Same scope-widening cost** as `self_alarm` — plus it writes to the *user's* channel, which is the single most sensitive surface in the daemon. |
| `board_*` / `board_link` | `board` (#8), `board-link` (#11) | **ari** | writes board rows / mints a passcode | **Pattern generic** (a durable, global, shared evidence surface — *"the board is the ALWAYS surface; notify is the MAY voice"*, `notify_user.py:32-39`) but the namespace is **ari-owned and global**; opening it to other agents is an explicit user decision, not a default. |
| `watchover` | (no category — a service) | `watcher` | activates a watcher on an instance | **NO — IT IS NOT A DELIVERY CHANNEL.** `WatchoverService` is an LLM evaluator that **intercepts every tool call** of a watched instance *before execution* and returns Allow/Deny (`daemon/services/watchover_service.py:236`, `:12-18`); the `watcher` agent is **tools-less** (`allow: []`). It has no queue, no delivery, no recipient. Its only generic value is the **pause-first-then-quiesce choreography** — which the metrics lane may need IF a watch ever has to mutate daemon state while an instance is running. |

**The design implication, verbatim from the research:** *"the only **immediately** reusable delivery primitive is the **scheduler deferred/coalesced delivery** path, because it is agent-agnostic and already durable + at-most-once. The ari-owned surfaces (`self_alarm`, `notify_user`, `board_*`) would each need a per-agent re-gating lane."*

### C.4 — The future lane's open questions (NOT this lane's plan items)

1. **May metrics-triggered alerts reach non-ari agents?** This is the **explicit open question** and it directly determines the scope-split: if alerts may only ever reach `ari`, the future lane can reuse the ari-owned surfaces (with an ari-only recipient) and the scope model stays simple; if alerts may reach `maintenancer` / `devops` / `wanderer`, the future lane must build a **non-ari delivery path** (the scheduler's deferred/coalesced path into a target agent's session — the `source_type="scheduler"` + bound-caller machinery in `daemon/sources/mapper.py:422-460`) and **must** re-gate `self_alarm` / `notify_user` or build a sibling surface. **This question also bears back on THIS lane's scope decision** (B.5) — the tool layer's category is ari-free, so the *read* surface is broad while the *alert* surface may end up narrow. That asymmetry is fine and should be stated explicitly in the shipped docs.
2. **What is the notification budget?** The chat surfaces already carry N=1/turn (≥ 60 s) and M=6/rolling-hour per destination (`notify_user.py:74-97`). A metrics watch needs its own equivalent, or it inherits the chat surface's (which is shared with `ari`'s other notifications).
3. **Who may ARM a watch?** An agent-readable trigger is a durable write; arming is not a read. If arming is agent-facing it needs an ACT category (a `metrics-watch` sibling of `instance_ops`) with its own audit ledger — a **write** surface, unlike this lane's read-only category (R5).
4. **Does a watch survive a restart?** The ring is in-RAM and lost on restart (`contract:400-402`); `restart_epoch` makes the loss visible. A watch whose evaluation window is entirely in-RAM has a cold-start blind spot — the future lane must decide whether the arming state is durable (DB) and whether the first evaluation after a restart is suppressed.

---

## D. DECISIONS

> The plan worker folds these **verbatim** into `.agents/shared/planning/metrics-agent-tools/decisions.md`. Each carries a rationale and the rejected alternatives, so the decision survives without this document.

### D.UX — output shape & UX

**D-UX-1 — Flat tools, ONE category, three tools.**
*Decision:* one privileged category `metrics-read`; three flat tools `metrics_families` / `metrics_snapshot` / `metrics_history`, mirroring the provider's three methods.
*Rationale:* each `args_schema` stays minimal and is itself the agent's documentation surface (`loader.py:684-707`); each tool makes ONE provider call and is independently auditable; the house precedent (wedge-capture: 2 tools/1 category; instance_read: 4 tools/1 category) matches.
*Rejected:* a mega-tool with a `view=` enum (forces one schema to carry the union of all parameters and makes the tools-list in the system prompt uninformative); a single "query" tool with a raw filter string (defeats the contract's label-aware selector and re-implements query parsing in the tool layer).

**D-UX-2 — The tool is named `metrics_history` (provider method), not `metrics_series` (wire route).**
*Decision:* `metrics_history`.
*Rationale:* the tool calls `provider.history()` in-process; the three names then read as a 1:1 mirror of the Protocol's three methods, matching D4's "one provider, three views". The `/series` wire name survives in the tool's `series` ARGUMENT (the contract's canonical label-aware selector) and the mapping is stated in `CATEGORY_DOC`, each docstring and `_full_doc_`.
*Rejected:* `metrics_series` (creates a second vocabulary that matches the wire but not the seam; an agent that read the API doc would hunt for a name that does not exist).

**D-UX-3 — The tool result is a PROJECTION of the frozen envelope, not the envelope.**
*Decision:* the tool echoes the frozen header members (`schema_version`, `vocabulary_version`, `generated_at_utc`, `restart_epoch`, `activation`) verbatim, reuses the frozen field names for what it surfaces, and ADDS tool-layer members (`read_status`, `query`, `matched_count`, `returned_count`, `dropped_count`, `more_available`, `truncated`, `narrow_hint`, `caveats`, plus the derived per-row/per-family members).
*Rationale:* the frozen contract governs the **HTTP wire**; the agent result is a new surface and must not claim to BE the envelope. The AM-7 two-axis policy (`schema_version` = envelope shape, `vocabulary_version` = label vocabulary) is untouched — the tool echoes both and never gates on them.
*Rejected:* returning the raw envelope verbatim (it carries the HTTP page semantics — `offset`/`limit`/`truncated`-as-page — which do not apply to a tool call, and it lacks the decision-ready derived members).

**D-UX-4 — The sane-default ladder, mapped onto the contract's two-axis bounds.**
*Decision:* `window_s` default 900 (max 7 200 = `WINDOW_SECONDS`); `max_points` default 120 (max 240 = `MAX_POINTS_CEILING`); `top_n` default 10 for history / 20 for snapshot (max 50 = `MAX_NAMES`); selectors ≤ 50 (the tool refuses above it, pre-empting the provider's `InvalidLabelFilter` at `provider.py:346-349`); the provider work bound is `max_series = top_n + 1`. *(citation corrected by architect-fold AmB-2 — see decisions.md Part 5: `provider.py:346-349` caps `names` ONLY; the label-aware `series` list is uncapped provider-side and the combined cap is the ROUTER's (`routers/metrics.py:719`) — the tool bypasses the router, so it must enforce `len(set(names)) + len(series) > MAX_NAMES` itself; the returned-leg budget is additionally clamped by AmM-1.)*
*Rationale:* the agent must never reason about cursors or cardinality; the defaults keep the provider's work at 11 × 120 = 1 320 points (11 % of `MAX_TOTAL_POINTS = 12 000`) and the tool's CEILINGS at the contract's own ceiling (51 × 240 = 12 240). A 15-minute default window covers the two operational horizons the daemon cares about (the wedge-capture sustained-lag trigger — `DEFAULT_LAG_S = 5.0` sustained `DEFAULT_LAG_SUSTAIN_S = 15.0`; the 30-minute self-alarm floor) without the LLM cost of a 60-minute chart window *(truth-in-docs correction, approver i2-N8 — the "1800 s wedge trigger" phrasing was wrong: `1800 s` is the self-alarm `MIN_HORIZON_SECONDS` (`daemon/services/self_alarm_service.py:77`), while the wedge-capture lag trigger is `DEFAULT_LAG_S = 5.0` sustained `DEFAULT_LAG_SUSTAIN_S = 15.0` (`daemon/services/wedge_capture/config.py:100-101`); the parameter choice is unaffected.)*
*Rejected:* the contract's own `DEFAULT_MAX_POINTS = 240` as the tool default (it is sized for a 60-minute CHART; for an agent it is a 4× larger payload for no extra decision value); passing `max_series=None` (unbounded provider work — the tail could be 2000 series × 240 points).

**D-UX-5 — No cursor in v1; every call re-anchors; the continuation mechanism is "narrow the selector".**
*Decision:* no tool accepts or returns a cursor. The tool computes `now = int(time.time())` itself and passes `now=instant`; `compare=true` reads the preceding window at `now = instant − window_s` (an internal re-anchor). Truncation is continued by narrowing the filter, guided by `narrow_hint`.
*Rationale:* a cursor pins an evaluation instant — correct for a paginating HTTP client, wrong for an agent making a forward-moving decision. Re-anchoring every call means "the last N seconds as of now", which is the question an agent actually has. The comparable-window leg still needs the anchor, which is why `now` is a required Protocol amendment (GAP-1).
*Rejected:* exposing the cursor (agents would carry a stale instant across turns and reason about it; the tool has no page model to resume); splitting ONE 2×-window read into two halves to avoid the second call (unsound: bucket-max downsampling makes a half-window's first/last point a bucket maximum, not an observation — the delta would be wrong).

**D-UX-6 — A bound that drops something is NEVER silent — the uniform quintet.**
*Decision:* every tool emits `matched_count`, `returned_count`, `dropped_count`, `more_available`, `truncated`, `narrow_hint` with identical semantics in all three tools. `truncated` is true whenever anything was dropped. `narrow_hint` names a CONCRETE next filter.
*Rationale:* the contract's clause *"a bound that drops something is never silent"* (`metrics-query-contract.md:283-286`) governs the HTTP page body; the tool honours its INTENT with strictly MORE information than a cursor (an exact count where it is knowable, plus an instruction). `metrics_families` and `metrics_snapshot` have EXACT counts; `metrics_history` has an exact `returned_count` + `more_available` (the provider's work bound prevents counting the tail) and the hint routes the agent to `metrics_snapshot` for the exact live-variant count.
*Rejected:* omitting `matched_count`/`dropped_count` and reporting only a boolean (the agent cannot tell "2 dropped" from "2 000 dropped" — a materially different decision).

**D-UX-7 — `dark` is NOT "no data": the derived `data_present` / `dark_with_live_series` + a GENERIC caveat.**
*Decision:* the tool carries `availability` **verbatim** and adds `data_present` (`live_series_count > 0`) and `dark_with_live_series`. When any returned family is dark-with-live, a `caveats[]` entry says the fact **without attributing a cause**.
*Rationale:* F1 (9 series), F3 and F6 (15 series) report dark while publishing — a PRODUCTION defect owned by other lanes (R-i/R-ii). A tool that read "dark" as "no data" would send the agent back to `bash`-curling `/readyz` — the uncontracted workaround the census names.
*Rejected:* treating `dark` as empty (wrong for 3 of 16 families today); hard-coding the defect attribution ("R-ii") into a shipped tool string (it would go stale the moment those lanes land — the tool asserts only what it can verify at runtime).

**D-UX-8 — `registry_read_ok` is the degraded-read signal; the uniform `read_status` block; the FE shares the vocabulary.**
*Decision:* all three tools carry `read_status: {registry_read_ok, mode, note}` at the top level, where `registry_read_ok` EQUALS the frozen `activation.registry_read_ok` (`envelope.py:70`; `provider.py:98-107`, set false at `:152-160` when the registry read RAISED), `mode` is `"ok"|"degraded"`, and `note` is null when ok. An EMPTY payload with `registry_read_ok=false` is reported as *"the read FAILED — unknown, not empty"*, never as "no data".
*Rationale:* a raised registry read and an empty registry produce byte-identical bodies (`provider.py:100-104`); without the flag a client cannot tell them apart. The FE has the same blind spot (its `catalogError` only fires on a transport failure — `metrics.component.ts:118-129`), so the FE mirror must set `catalogError` when `activation.registry_read_ok === false`, keeping the FE's error/empty distinction one-for-one with the tool's `mode`.
*Rejected:* using an empty payload as the absence signal (indistinguishable from failure — the exact defect the flag exists to prevent); inventing a second word for the same concept (the FE and the tool must share one vocabulary, or the alignment breaks).

**D-UX-9 — The availability reason is carried VERBATIM; the tool never re-derives or re-orders it.**
*Decision:* `availability` is copied from the provider's family entry unchanged. The tool does not read the census switch, inspect threads, probe the DB, validate the reason against the closed set, or substitute a reason.
*Rationale:* the closed set and its REQUIRED ORDER (`census_not_running` BEFORE `census_gated`) are frozen (`metrics-query-contract.md:350-367`; `availability.py:76-92`), the backend already ASSERTS the emitted reason is in the set (`provider.py:256-266`), and the facts are INJECTED — *"the query package never goes looking for them itself"* (`availability.py:49-62`). The tool is a consumer, not a second source of truth.
*Rejected:* re-deriving availability in the tool layer (it would duplicate the reason order in a second place and drift); "improving" a reason the provider emitted (the tool cannot see the truth the provider saw).

**D-UX-10 — The `open` cardinality class renders `labels: null` and is NEVER value-enumerated.**
*Decision:* the catalog entry for an `open` name renders `labels: null` + `cardinality_class: "open"`; live rows carry their OBSERVED label combination plus the same `cardinality_class`; the tool never expands an open metric's label value space.
*Rationale:* the rule is frozen (`metrics-query-contract.md:150-153`, `:310-311`) and enforced by the provider (`_declared_labels` returns `None` unless closed, `provider.py:193-202`). Series identity IS `(name, labels)` (AM-1) so live rows must carry their labels; the class member is what stops the agent from reading an observed combination as a declared space.
*Rejected:* renaming the row field to `observed_labels` (breaks the frozen field-name reuse and the "reuse frozen names" rule); enumerating an open metric's values for the agent's convenience (a direct contract violation — the open class exists precisely because the value space is unbounded).

**D-UX-11 — Deltas are computed and compared, never fabricated.**
*Decision:* `delta` = last − first of the RETURNED window; `prev_delta` = the same over the preceding equal-length window; `delta_change` = `delta − prev_delta`; a window with fewer than 2 points yields `null` + `delta_reason: "insufficient_points"`; the series carries `downsampled: bool`.
*Rationale:* "deltas vs a comparable window" is what makes a trend decision-ready, and the comparable window is exactly the second anchored read (D-UX-5). A fabricated `0` asserts "nothing changed" — a different claim from "we cannot tell". `downsampled` tells the agent whether the delta is observation-level (the default: `max_points` ≥ `window_s ÷ effective_interval_s`) or bucket-level.
*Rejected:* emitting `0` for an insufficient window (a false claim); omitting the comparable-window leg (a raw trend is not a decision input); splitting one 2× read into halves (unsound under bucket-max — D-UX-5).

**D-UX-12 — The provider is reached through an ADDITIVE manager-held mount.**
*Decision:* `InstanceManager.metrics_query_provider` (class-level default `None` + a typed property), set by the lifespan at `api.py:2533` beside the existing `app.state.metrics_query` assignment; the tool factory resolves it via `create_metrics_read_tools_if_available` (the `create_board_tools_if_available` pattern) and returns `[]` when it is absent.
*Rationale:* the factory already receives `manager` (zero new plumbing at the call site); it matches the house's `manager.board_service` / `manager.scheduling_service` precedent; it avoids a module-level mutable global; and it guarantees the tool reads the SAME provider and the SAME in-RAM ring as the HTTP door (a second provider would have an empty ring — `provider.py:340-341`).
*Rejected:* a module-level holder in the seam (a mutable global); a factory called by both the app and the tool (the tool would build a SECOND ring — it would silently return no trend forever); the tool calling the HTTP door over loopback (a network hop for an in-process read, plus a bearer-secret dependency).

**D-UX-13 — The Protocol is extended ADDITIVELY; the tool is typed against it with NO `Any`/`getattr` escape.**
*Decision:* amend `daemon/services/metrics_query/protocol.py` — add `now` and `max_series` to `history()`, and add the properties `registry_read_ok`, `listener_enabled`, `census_enabled`. The two NUMERIC facts (`effective_interval_s`, `last_sample_age_s`) are read through the seam's own public reader (`envelope.effective_interval_s` + a promoted `envelope.read_numeric_fact`), NOT through `getattr`.
*Rationale:* the house lesson (*"a Protocol that no consumer references guarantees nothing — extend the Protocol BEFORE typing the consumer"*) applies with full force: this lane IS the first consumer. The amendment is additive in the SEAM (the contract governs the HTTP/wire semantics; the Protocol governs the in-process seam) and the implementation already supports it (`provider.py:325-326`, `:87-122`). The two numeric facts stay out of the Protocol because their duck-typed shape already varies between the provider and the sanctioned fake, and the seam already ships the sanctioned bridge for exactly that (`envelope._provider_float`, `envelope.py:135-146`).
*Rejected:* typing the provider as `Any` and using `getattr` everywhere (the exact failure the lesson names — the Protocol's incompleteness stays invisible); declaring the numeric facts in the Protocol (it would force one shape on a provider and a fake that deliberately differ, for no consumer value); a SECOND protocol for the freshness facts (splits the one substrate into two contracts).

**D-UX-14 — No audit ledger (R5).**
*Decision:* the metrics category writes NO audit row, NO migration, NO table.
*Rationale:* the house precedent is explicit and applies twice — `instance_read.py:62-64` (*"The audit ledger belongs to the ACT category only: a read has no side effect and writes NO audit row"*) and `wedge_capture.py:30-32` (*"THE READS HAVE NO SIDE EFFECT — A read writes NO audit row, deletes NOTHING, and triggers NOTHING"*). Nothing in the house pattern obliges a read to write.
*Rejected:* adding a "who queried what" ledger (it would be a NEW table + a migration + the two-tier coupling — a write surface in disguise, and it would make the migration delta non-empty for a read-only lane). If query auditing is wanted later, it is its own decision with its own lane.

**D-UX-15 — No kill-switch for this category.**
*Decision:* no dedicated kill-switch.
*Rationale:* a kill-switch is proportionate to a surface with a **side effect and a blast radius beyond the daemon** — `notify_user` has one because it writes to the user's chat channel (`notify_user.py:106`), and `instance_ops` has one because it can terminate a cascade tree. A read has neither. The effective off lever is **removing `"metrics-read"` from the three `meta.json` allow arrays** (three one-line edits, no restart of the daemon needed for the next instance construction) plus the privileged category's default-deny (an empty-allow agent never had it).
*Rejected:* adding `ENSEMBLE_METRICS_READ_ENABLED` (a knob that guards nothing observable — the switch would be read by the tool layer, adding a second check site; and the door itself is never switch-gated, so a switch-gated tool would be INCONSISTENT with the door it mirrors — the contract's invariant `no_direct_switch_read_in_query_package` and `check_site_census_equals_two` would both be violated in spirit).

### D.SCOPE — the `metrics:read` split

**D-SCOPE-1 — MINT a new scope `metrics:read`.**
*Decision:* add `"metrics:read"` to `SCOPE_TAXONOMY` (`daemon/auth/scopes.py:60-101`); re-point the three query routes to `scope_required("maintenance:read", "metrics:read")`; the text-exposition route keeps `maintenance:read`; the manifest string becomes `"/api/metrics": "maintenance:read|metrics:read"`.
*Rationale:* the D6 trigger is MET (the shipped API doc names the agent-tool door as a trigger — `metrics-query-api.md:286-292`); `maintenance:read` also confers checkpoint-cleanup-EXECUTE (the contract's own AM-20 disclosure at `:404-405`), which a metrics-only machine lane must not carry; the cost is 4 lines + 2 test strings, and `SESSION_OPERATOR_SCOPES = SCOPE_TAXONOMY` means no operator re-grant and no migration.
*Rejected:* reuse `maintenance:read` with a role distinction (the grant model has NO role axis — `scopes.py:42-52`; and it leaves the over-grant in place); a scope-gated agent identity (agents hold no AuthContext; the house precedent is that the agent's internal lane bypasses HTTP scope — `scopes.py:78-84`, D10).

**D-SCOPE-2 — The tool path is gated by the house TWO-LAYER pattern, not by the HTTP scope.**
*Decision:* the privileged category (visibility) + the in-body `agent_id` gate (authority). The tool's authority model is the agent-id allow-set `{maintenancer, devops, wanderer}`.
*Rationale:* the seam is unscoped by construction (`provider.py:26-43`; `protocol.py:3-8`) and D6's retrofit map is entirely HTTP-shaped — executing it literally changes NOTHING about the tool path. The in-body gate is the unbypassable one: a literal tool name in an allow list escapes the CATEGORY gate (`_tool_registry.py:559-560`), but the allow list never reaches the body gate.
*Rejected:* scope-gating the tool (there is no AuthContext to check); relying on the category gate alone (a literal tool-name allow entry bypasses it — D-13); relying on the allow list alone (the same hazard).

**D-SCOPE-3 — The meta.json entries are CATEGORY IDS ONLY.**
*Decision:* each of the three holders' `tools.allow` carries `"metrics-read"` — never a tool name.
*Rationale:* D-13 — a literal tool name bypasses the category gate outright (`_tool_registry.py:559-560`; `instance.py:561-568`). The category id is also what makes the entry resolve as a known category at boot (`registry.py:1355-1368`).
*Rejected:* listing `metrics_families` / `metrics_snapshot` / `metrics_history` (they would be visible to any agent whose allow list contains the literal — and would escape the default-deny strip entirely).

**D-SCOPE-4 — The tool's fail-closed ladder mirrors the door's 401/403/503/400 semantics.**
*Decision:* four distinct refusal tokens — `unknown-agent` (blank/`None`/non-`str` agent id ≡ 401), `not-authorized` (known agent id outside the allow-set ≡ 403), `metrics-unavailable` (provider not mounted ≡ 503 registry_unavailable), `invalid_label_filter` (malformed / over-ceiling selector ≡ 400). The authorization check runs **FIRST** — before the provider is touched at all.
*Rationale:* the door's fail-closed principle is *"the gate fires BEFORE the registry is read"* (`routers/metrics.py:171-220`); the tool must mirror it exactly, so an unauthorized call never reads the registry, never materializes a series, and never even learns whether the substrate is mounted.
*Rejected:* one generic "denied" token (the agent cannot distinguish "I am not allowed" from "the metrics stack is down", and those have different next actions); checking the substrate before the authorization (it leaks the substrate's state to an unauthorized caller).

**D-SCOPE-5 — The tool does NOT require auth to be configured.**
*Decision:* the tool path never consults `app.state.auth_config`.
*Rationale:* the tool is not a network surface — there is no anonymous caller in-process, and the exposure the router's 503 guards against (*"anyone who can reach the port"*) does not exist. The deployed default on this host is auth-OFF; gating the tools on it would kill the only sanctioned health view for the daemon's own maintainer/devops/wanderer. The house precedent is that the agent's internal lane bypasses HTTP scope (`scopes.py:78-84`).
*Rejected:* mirroring the 503 literally (it would disable the tools in the default deployment); checking the auth config as a soft warning (a warning nobody reads is not a control).

**D-SCOPE-6 — Blast radius on the scope pins: two string pins change, the route-count pin does NOT move.**
*Decision:* update `tests/unit/test_router_scope_manifest.py:169` (family scope) and `tests/unit/metrics/test_metrics_query_scope_cage.py:43`/`:147`/`:150-157` (the `FAMILY_SCOPE` constant and the per-route assert). The declared-route count stays **111** — this lane adds ZERO routes. The mount census stays 37 and the class-manifest count stays 33. The `ari` trailing-entry allow pin is unaffected (the metrics category is not granted to ari).
*Rationale:* D6's step 4 (the cage is a subset check) means the routes stay inside the pinned family set; and a tool is not a route.
*Rejected:* bumping the route count (it would be wrong — the count already moved 108 → 111 in central-metrics-v1, and this lane adds no routes).

**D-SCOPE-7 — The contract doc amendment is a cited, DESCRIPTION-ONLY amendment.**
*Decision:* amend the frozen contract's PROSE (the §1.13 matrix rows 2–3, the §6 truth table, the §7 disclosure, the AM-20 disclosure) to record that the three query routes accept `maintenance:read` OR `metrics:read`. **No machine-pinned fenced block is touched** (the pinned blocks are `activation-surfaces`, `query-routes`, `error-vocabulary`, `truth-table-invariants`, `availability-reasons`, `gate-pack-ids` — none carries a scope string), so `central_metrics_contract_doc` stays green. `schema_version` stays 1 and `vocabulary_version` stays 2 (no wire shape changes). The amendment cites D6 as its authority, following the doc's own in-place amendment precedent (`:248`, `:396`).
*Rationale:* the contract names `maintenance:read` as THE scope in four prose places; the retrofit changes the door's grant set, so the prose must follow. The amendment changes NO behaviour for any existing holder.
*Rejected:* leaving the prose stale (the contract would name a scope set that no longer matches the code — a documentation defect in a frozen document is worse than a cited amendment); editing a machine-pinned block (it would break the pack for no reason).

### D.WATCH — the future lane (design-only, carried for reference)

**D-WATCH-1 — v1 is pull-only; the watch/alert surface is a SEPARATE future lane.**
*Decision:* this lane ships three read tools and no alert machinery. The watch/alert design doc is authored DURING the future lane's implementation as a close-out artifact, not planned here.
*Rationale:* ruling R3; the delivery problem is genuinely hard (§C.3 — only ONE primitive is reusable without widening an ari-owned authority surface), and the estimate is 3–4 engineer-weeks — a lane, not a follow-on.
*Rejected:* adding an "arm a watch" tool to this lane (arming is a WRITE with a durable schedule row and a firing invariant — it would need an ACT category, an audit ledger, and its own kill-switch; it would also make this lane's migration delta non-empty and its "read-only" posture false).

**D-WATCH-2 — The ONLY immediately reusable delivery primitive is the scheduler's deferred/coalesced path.**
*Decision:* if the future lane wants a delivery path that works for ANY agent, the scheduler's deferred-durable + coalescing delivery is the base (`daemon/sources/adapters/scheduler.py:1111-1163`). Its bounds are pydantic-settings knobs, not DDL ⇒ the migration delta stays EMPTY.
*Rationale:* it is agent-agnostic (the pinned caller is a schedule row, not an agent), durable, and at-most-once by construction (`deferred`/`coalesced`; `_COALESCE_*` vocabulary at `:126-136`).
*Rejected:* `self_alarm` (target hardcoded to ari — widening it is a user-visible authority change); `notify_user` (same, plus it writes to the user's chat); `board_*` (ari-owned global namespace); `watchover` (**not a delivery channel at all** — it is a tool-call interceptor with no queue and no recipient).

**D-WATCH-3 — Watchover is NOT a delivery channel.**
*Decision:* the future lane may reuse watchover's **pause-first-then-quiesce choreography** and nothing else.
*Rationale:* `WatchoverService` intercepts a watched instance's tool calls with an LLM evaluator and returns Allow/Deny (`watchover_service.py:236`, `:12-18`); the `watcher` agent is tools-less. There is no queue, no recipient, no delivery.
*Rejected:* treating it as an alert transport (it would fire on tool calls, not on metrics — a different trigger class entirely).

**D-WATCH-4 — The "may alerts reach non-ari agents?" question is OPEN and belongs to the future lane — but it is flagged here because it bears on this lane's scope decision.**
*Decision:* this lane's READ surface is granted to three non-ari agents (R4); the future lane's ALERT surface may end up ari-only. The asymmetry is acceptable and must be stated in the shipped docs.
*Rationale:* the read surface is strictly less sensitive than the wedge-capture surface the three agents already share (all-thread stack dumps + per-thread CPU maps, `wedge_capture.py:30-32`), whereas the alert surface touches a durable schedule row and possibly the user's chat.
*Rejected:* pre-deciding the alert audience in this lane (it is a user decision, and it depends on the future lane's delivery design).

---

## E. ESCALATIONS

**E-1 — The scope split touches the FROZEN contract's prose (caller-level ruling requested).**

*The issue.* The brief says: *"The frozen contract is frozen. The tool layer adapts to it, never changes it. If a contract change seems unavoidable, do NOT plan it — raise it in an ESCALATIONS section of your doc and in your final report."* D6's retrofit map re-points the door's scope from `maintenance:read` to `maintenance:read|metrics:read`. The frozen contract's **prose** names `maintenance:read` as THE scope in four places — the §1.13 activation-surface matrix (rows 2–3, `metrics-query-contract.md:31-32`), the §6 normative truth table (`:318-322`), the §7 door disclosure (`:379-380`, `:387`) and the AM-20 over-grant disclosure (`:404-405`). Executing D6 therefore **does** require a doc change, even though it changes **no wire shape, no route, and no existing holder's behaviour**.

*What is NOT at stake (established by evidence, so the ruling can be narrow):*
- **No machine-pinned fenced block carries a scope string.** The pinned blocks are `activation-surfaces` (`:41-48` — six surface NAMES), `query-routes` (`:225-229` — three route PATHS), `error-vocabulary` (`:235-242`), `truth-table-invariants` (`:328-334` — five invariant NAMES), `availability-reasons` (`:352-358`) and `gate-pack-ids` (`:423-433`). The `central_metrics_contract_doc` pack stays green.
- **`schema_version` (1) and `vocabulary_version` (2) are untouched** — the two axes the contract defines (`metrics-query-contract.md:83-90`) govern the envelope shape and the label vocabulary; a grant-model change is neither.
- **The contract doc has an in-place amendment precedent** — `:248` (*"it is amended here to the shipped form"*) and `:396` (*"a **description-only** amendment because this lane consumes it"*).
- **D6 is itself the authority.** `decisions.md:641-658` is a NAMED COMMITMENT addressed to exactly this lane, and the shipped public restatement (`docs/architecture/metrics-query-api.md:286-292`) names *"the deferred agent-tool door"* as the trigger.

*The ruling requested:* **confirm that this lane may execute D6's retrofit map, including a cited, description-only amendment to the frozen contract's prose, under D6's own authority** — i.e. that the amendment is a *deferred-lane execution of a named commitment*, not a *change to the frozen contract*.

*My recommendation:* **PROCEED** under D6, with the amendment recorded in the contract's own style (cited, description-only, naming D6 as the authority) and pinned by the existing `central_metrics_contract_doc` pack (which continues to guard the fenced blocks). The rejected alternative — leaving the contract's prose naming a scope set the code no longer has — trades a cited, zero-behaviour doc amendment for an unamended documentation defect in a frozen document, which is the worse trade.

*If the caller rules "no contract prose edit at all":* the fallback is to execute **D-SCOPE-1's taxonomy line + the manifest/decorator re-point only**, and record the contract-prose divergence as a known documentation debt for the contract owner. That fallback leaves the shipped contract naming a scope set the door no longer implements exactly — the caller should weigh that before choosing it.

**No other escalation.** The tool surface (names, params, defaults, output shapes), the provider reach mechanism (D-UX-12), the Protocol amendment (D-UX-13), the no-audit-ledger posture (D-UX-14), the no-kill-switch ruling (D-UX-15), the holders (R4) and the pull-only posture (R3) are all **within this lane's mandate** and are decided above with their rationale and rejected alternatives.

---

## References

**Frozen contract & published docs**
- `docs/architecture/metrics-query-contract.md` (818 L) — the FROZEN contract: §1.13 activation-surface matrix `:31-35` · §4 envelope header `:92-107` · §5 the three views `:128-230` · §5 error vocabulary `:233-258` · §6 the normative truth table `:313-334` · §7 disclosures `:379-405`
- `docs/architecture/metrics-query-api.md` (380 L) — §5 scope disclosure `:268-292` (the D6 trigger, incl. "the deferred agent-tool door"); §6 the OUT-list `:296-330`
- `docs/architecture/metrics-promote-disclosure.md` (216 L) — the live/dark measurement `:53-57`; the six dark families `:69-80`; the `dark`-while-live production defect `:82-98`
- `central-metrics-v1/decisions.md` — D1 `:28-125` · D4 (swappability guarantee) `:289-444`, esp. `:375-377` · **D6 `:641-658`**

**Provider seam**
- `daemon/services/metrics_query/protocol.py:22-52` (the Protocol — the first consumer arrives with this lane) · `provider.py:58` (the concrete provider), `:87-160` (the facts), `:211-279` (`families()`), `:281-316` (`snapshot()`), `:318-393` (`history()`), `:397-422` (freshness) · `envelope.py:56-71` (the activation block, `registry_read_ok` at `:70`), `:74-82` (header), `:85/:109/:154` (the three builders), `:135-151` (the tolerant reader), `:263-290` (cursor helpers) · `selectors.py:32-77` · `availability.py:34-92` · `constants.py:25-98` · `roster.py:35-251` · `ring.py` · `sampler.py` · `downsample.py`
- `daemon/services/metrics_query/wiring/registry.py:74-180` (the registration helper) · `w1.py` / `w2.py` / `w3.py` (the real metric names) · `vocab.py:40-180` (the frozen label vocabularies) · `adjudication.py:51-209` (the family verdicts + series bands)

**Registry, auth, wiring**
- `daemon/checkpoint_metrics.py` — `VOCABULARY_VERSION:208` · `SOURCE_IDIOMS:225` · `DURABILITIES:232` · `CHART_ROLES:237` · `FAMILY_ROLE_*:240-241` · `label_key():485` · `Metric.__init__:563` · `last_publish_monotonic():628` · `effective_chart_role():640` · `is_chart_primary():652`
- `daemon/api.py:480` (the lifespan) · `:2495-2547` (the provider/sampler wiring + degradation) · `:2533` (`app.state.metrics_query`)
- `daemon/routers/metrics.py` — `:96-106` (`_SCOPE_READ`, `_ERR_AUTH_NOT_CONFIGURED`) · `:121-137` (`_require_auth_on`) · `:143` (the text route) · `:208` (`_CURSOR_VERSION`) · `:211-233` (`_UnknownFamily`, `_ExpiredCursor`, `_query_provider`) · `:441-473` (the off-loop build + `_bound_page`) · `:560`/`:604`/`:673` (the three query routes)
- `daemon/auth/scopes.py:42-52` (grant model, no role axis) · `:60-101` (`SCOPE_TAXONOMY`) · `:104-116` (`SCOPE_WILDCARD`, `SESSION_OPERATOR_SCOPES`, `BOOTSTRAP_DEFAULT_SCOPES`) · `:78-84` (the D10 agent-lane precedent)
- `daemon/auth/decorators.py:41-50` · `:75-78` (`SCOPE_INSUFFICIENT`) · `:94-114` (the any-of grant check)
- `daemon/manager.py:618` (`class InstanceManager`) · `:621-700` (the constructor) · `:15840-15861` (`scheduling_service`) · `:15863-15876` (`board_service`)

**Tool-layer house conventions**
- `daemon/tools/_tool_registry.py` — `:23+` (`DYNAMIC_TOOL_NAMES`) · `:279` (`DYNAMIC_TOOL_PREFIXES`) · `:314-356` (the SAME-PR pin doctrine) · `:357-618` (`PRIVILEGED_TOOL_CATEGORIES`) · `:559-560` (D-13: per-tool allow rejected) · `:621-646` (`register_tool_category`) · `:934-936` (the name-prefix fallback — the fail-open risk) · `:969-1260` (`CATEGORY_MODULES`) · `:1060-1065`/`:1072-1078` (the kebab/snake convention) · `:1262+` (`KNOWN_TOOL_NAMES`) · `:1285-1288` (the frozen drift pin) · `:1534` (`skill_get_metrics` — the collision check) · `:1636-1661` (`get_category_doc`)
- `daemon/tools/instance.py` — `:181-187` (`INNATE_SKILL_TOOL_CATEGORIES`) · `:190-235` (the innate-skill expander, refuses privileged) · `:284-298` (the factory imports) · `:424-580` (`resolve_tool_filter`) · `:561-568` (the category-vs-literal expansion) · `:2667-2699` (the `*_if_available` wrapper) · `:3014` (`create_instance_tools`) · `:6542-6621` (the extends) · `:7133-7141` (help / scan / filter — the placement constraint) · `:7146-7181` (the privileged strip)
- `daemon/tools/wedge_capture.py:1-40` (the doctrine), `:64-65` (`CATEGORY_NAME`/`CATEGORY_DOC`), `:84` (the authorized set), `:103-112` (the fail-closed helper), `:136-146` (`_resolve_root` — the manager-resource precedent), `:149-201` (the factory + the tool) · `daemon/tools/instance_read.py:1-65`, `:87-128`, `:199-226`, `:524-591` · `daemon/tools/_auth.py:35-51`
- `daemon/tools/notify_user.py:19-30`, `:74-97`, `:106`, `:149-180`, `:538` · `daemon/tools/board_tools.py:19-29`, `:57+` · `daemon/tools/self_alarm.py:1-53`, `:143`, `:618-625` · `daemon/loader.py:655-709` · `daemon/tools/help.py:67-121`, `:228`

**Tests / pins**
- `tests/unit/test_wedge_capture_registration.py` (303 L — the registration-pack template, incl. the attribute pin `:185-201`, the default-deny leg `:203-220`, the fail-closed leg `:226-246`, the holder census `:252-275`) · `tests/unit/test_notify_user_registration.py:54/90-115/117-164/168-175/181-222/226-239` · `tests/unit/test_instance_ops_registration.py:88-246` · `tests/unit/test_board_registration.py:70/201/215/249-287` · `tests/unit/test_self_alarm_registration.py:76/209/212` · `tests/unit/tools/test_upgrade_registration.py:149` · `tests/unit/tools/test_attestation_registration.py:192` · `tests/unit/tools/test_secrets_registration.py:226` · `tests/integration/test_maintenancer_spawn_resolves_tools.py:369` · `tests/integration/test_service_tool_flag_off_byte_identical.py:832` · `tests/unit/tools/test_frozen_tool_name_discovery.py`
- `tests/unit/test_router_scope_manifest.py:169` (the family scope) / `:333-409` (the declared-route count = **111**) · `tests/unit/metrics/test_metrics_query_scope_cage.py:37-43/135-160` · `tests/integration/test_router_class_manifest.py:147` · `tests/helpers/metrics_query_fixtures.py:47-120` (`RecordingProvider`) · `tests/unit/metrics/test_metrics_query_provider.py:557-588` (the Protocol conformance)

**Delivery / alerting (design-only)**
- `daemon/sources/adapters/scheduler.py:111-136` (the coalesce vocabulary), `:238-254` (the fire outcomes), `:1111-1163` (the deferred-durable delivery), `:1127-1145` (the bounds knobs), `:1228-1230` (the MQ write target) · `daemon/sources/mapper.py:422-460` (the bound-caller refusal) · `daemon/services/scheduling_service.py:202-213`, `:629-698` (the visibility surface) · `daemon/services/self_alarm_service.py:67/77/280-281/550-590/694-708` · `daemon/tools/self_alarm.py:143` · `daemon/tools/notify_user.py:74-97/180/240-266` · `daemon/services/watchover_service.py:12-18/236/554-568`

**Planning inputs (this lane)**
- `.agents/shared/planning/metrics-agent-tools/research-metrics-surface.md` (715 L) · `research-tool-conventions.md` (992 L) · `research-folds.md` · `central-metrics-v1/{decisions,requirements,plan-overview,fold-ledger,research-findings}.md` · `metrics-collection/metric-catalog.md` (2010 L) · `central-metrics-v1/inventory-vendor/metrics-inventory-surfaces-fe-deps.md` (§S3.3 the per-agent asymmetry) · `central-metrics-v1/inventory-vendor/metrics-inventory-backend-producers.md` (§S3.4 the producer facts) · `frontend/src/app/pages/metrics/metrics.component.ts:30-140` (the FE catalog/empty/error projection — the FE mirror this lane aligns with)
