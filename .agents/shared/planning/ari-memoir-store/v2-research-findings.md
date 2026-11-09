# Memoir Store v2 "The Reflecting Mind" — Research Findings & Technical Digest

**Lane:** `feature/memoir-v2-reflecting-mind` @ worktree `/home/nea/ensemble-src-wt-memoir-v2`  
**Base commit:** `f49006b922492f72ca032c56e26d20468f7a3afd` (branched from `origin/latest`)  
**Status:** **AUTHORITATIVE RESEARCH RECORD (Additive).** Prepared 2026-11-09.  
**Doctrine & Safety Notice (P-7):** All file:line anchors cited below are DATED evidence verified on 2026-11-09. Because the codebase actively evolves across parallel lanes, line numbers are leads — **always re-grep at use time**.

---

## 1. Schema Reality & Database Substrate

### 1.1 The Pre-Staged `memoir_syntheses` Table
In v1, decision **DD-2** pre-staged the Layer 4 syntheses table empty so that v2 would spend its engineering budget on *behavior*, not schema churn.
- **Migration anchor:** `daemon/migrations/versions/20261105_102821_create_memoir_tables.sql` (lines 134–149).
- **ORM Model anchor:** `daemon/repositories/memoir/models.py` (lines 380–405, class `MemoirSynthesis`).
- **Existing Schema:**
  ```sql
  CREATE TABLE IF NOT EXISTS memoir_syntheses (
      id TEXT NOT NULL PRIMARY KEY,
      agent_id TEXT NOT NULL,
      claim TEXT NOT NULL,
      source_episode_ids TEXT,             -- JSONB on PG, JSON on SQLite (D1.14 / DR-6)
      valid_from TEXT,                     -- ISO-8601 string
      valid_to TEXT,                       -- ISO-8601 string
      verb TEXT NOT NULL CHECK (verb IN ('CREATE', 'CORROBORATE', 'REFINE', 'CORRECT')),
      supersedes TEXT,                     -- Foreign key-like pointer to prior synthesis id
      superseded_by TEXT,                  -- Replacement synthesis id
      confidence REAL,                     -- Float score 0.0 - 1.0
      created_at DATETIME NOT NULL         -- Timezone-aware timestamp
  );
  CREATE INDEX IF NOT EXISTS idx_memoir_syntheses_agent ON memoir_syntheses (agent_id, created_at);
  ```
- **Finding:** The syntheses schema is already 100% schema-complete for the blessed v2 scope! Zero `ALTER TABLE memoir_syntheses` statements are required unless new operational metadata is added.
- **Citations Mandate:** Every synthesis row written in v2 MUST name its source episodes in `source_episode_ids` (JSON list of UUID strings). A synthesis without source citations is a defect.

### 1.2 Seeded `memoir_episodes.stars`
- **ORM Model anchor:** `daemon/repositories/memoir/models.py` (line 244).
- In v1, decision **DD-19** stored self-declared stars flat (`stars: list[str] | None = Field(sa_column=Column(StarsListType, nullable=True))`).
- In v2, this column serves as the primary seed for Constellation entity extraction and graph line formation.

### 1.3 New Constellation Tables (Genuinely New)
v2 introduces the cold-path graph substrate:
1. `memoir_star`: Canonical entity records (`id`, `agent_id`, `name`, `star_type` ∈ `{person, project, place, concept, domain}`, `description`, `first_seen_at`, `last_touched_at`, `heat`).
2. `memoir_star_alias`: Mapping alternative surface forms to canonical stars (`id`, `agent_id`, `star_id` REFERENCES `memoir_star(id)`, `alias`, `created_at`).
3. `memoir_line`: Weighted episodic connection graph (`id`, `agent_id`, `star_id` REFERENCES `memoir_star(id)`, `episode_id` REFERENCES `memoir_episodes(id)`, `mention_count`, `first_mentioned_at`, `last_mentioned_at`, `created_at`).

### 1.4 Migration & Ensure Invariants
- **Migration file rule:** Every new SQLModel table requires its own dedicated `.sql` file in `daemon/migrations/versions/` (ordered prefix, e.g. `20261109_..._create_memoir_constellation_tables.sql`).
- **Postgres table creation:** In Postgres, tables are created by `SQLModel.metadata.create_all(self._engine)` at boot time (`daemon/manager.py:975-997`). Brand-new tables registered before `create_all` need **NO** `_ensure_postgres_columns` entry (the POK precedent).
- **Ensure census pin:** Existing tests (`tests/postgres/test_memoir_retrieval_pg.py:98-104` and `test_memoir_pg_corpus.py:115-123`) assert EXACTLY 2 memoir ensure entries (the `tsv` column ALTER and the GIN index). Any added ensure statement changes those test assertions.
- **Ensure single-transaction blast radius:** `_ensure_postgres_columns` in `daemon/manager.py` (lines 8055–10164) executes all statements in ONE single transaction (`with self._engine.begin() as conn: for stmt in statements: conn.execute(text(stmt))`). Any statement failure rolls back the entire ensure and aborts daemon boot. Order is NOT containment!

---

## 2. Frozen v1 Contracts & Boundaries

v1 contracts are frozen by constitutional doctrine. v2 is strictly additive.
- **File anchor:** `daemon/tools/memoir.py` (1,217 lines).

### 2.1 The 5 Frozen Tool Signatures
1. `memoir_write(text, title=None, importance=5, event_at=None, stars=None, keepsake=False, reason=None, actor=None)` -> returns dict with `semantic_status: "pending"`.
2. `memoir_pin(id, reason, actor)` -> `reason` and `actor` are mandatory; returns confirmation dict.
3. `memoir_unpin(id)` -> `id` only (blessed no-actor contract; idempotent no-op on non-keepsake returns success). v2 must NOT alter this signature.
4. `memoir_recall(query, k=5)` -> `k` clamped 1..20; returns byte-pinned string block with header, episode entries, and footer.
5. `memoir_timeline(since=None, until=None, q=None)` -> time-travel query on `event_at`; `stats_updated=false` always.

### 2.2 Two-Layer Gate & Execution Order
All memoir tools enforce a strict two-layer security gate (`_gate` in `daemon/tools/memoir.py:319-368`):
1. **Layer 1 (Caller identity):** Factory-captured `agent_id == "ari"` (enforced before any configuration check). Unauthorized callers learn nothing about whether the feature is enabled.
2. **Layer 2 (Runtime knob):** Call-time resolution of `resolve_memoir_knob(config)`. If OFF, returns typed refusal `MEMOIR_CODE_DISABLED`.
3. **Layer 3 (Validation & DB):** Parameter bounds checking, then DB transaction.

### 2.3 Refusal Vocabulary
- `MEMOIR_CODES` is asserted at module load: `assert len(MEMOIR_CODES) == 19` (`memoir.py:235-238`).
- Envelope format: `Error: {tool} REFUSED — reason={code}: {message}`.
- Tools NEVER raise exceptions (ADR-005 soft-fail contract).
- Any write-family tool post-commit failure reproduces M16 post-commit honesty ("MAY HAVE BEEN APPLIED… verify before retry").
- Decision for `memoir_about`: Map all errors onto existing codes (`memoir_not_found`, `memoir_query_required`, `memoir_stars_out_of_bounds`, etc.) to preserve the 19-code frozen vocabulary.

---

## 3. Wake Briefing Architecture & Budgets

- **Builder anchor:** `daemon/services/memoir_briefing.py` (452 lines).
- **Seam anchor:** `daemon/services/context_messages.py` (`_assemble_memoir_briefing` line 432, `_maybe_memoir_briefing_message` line 483).

### 3.1 Budget Model (DR-1 / AM-38)
- Persona budget: ~2,000 tokens nominal (authority `memoir_briefing.py:127-135`).
- Keepsake zone: ~4,000 tokens rendered FULL, never truncated.
- Dynamic zone: ~3,000 tokens over latest 5 episodes.
- Hard briefing ceiling: **24,000 characters** (DR-1).
- Absolute render-time backstop: **48,000 characters** (breach withholds the block rather than emitting partial corrupted text).
- Timeouts: Read timeout 10.0s (`MEMOIR_READ_TIMEOUT_S`), total briefing build budget 5.0s (`MEMOIR_BUILD_BUDGET_S`).

### 3.2 Context Seam Integration Sites
The briefing is injected into agent turns at TWO distinct sites in `daemon/services/context_messages.py`:
1. **Refresh branch:** Lines 2564–2570 (re-waking into existing instances).
2. **Main turn-1 path:** Lines 2928–2934 (fresh instance spawns).
- **Once-flag rule (D5.4 / AM-22):** The once-flag `memoir_briefing_injected` is written into `instance_metadata` by the **consumer** only (`daemon/services/instance_messaging.py`), NEVER by the builder.
- **Context Kind:** `CONTEXT_KIND_MEMOIR = "memoir"` (stable id `memoir:{agent_id}`, compaction-hoist truthy-keyed).
- **Single-Sourced Keepsake Line:** `KEEPSAKE_LINE_TEMPLATE` is single-sourced in `daemon/repositories/memoir/models.py:417` (`render_keepsake_line`). All keepsake proposal lines in v2 must mirror this single-sourcing pattern.

---

## 4. Wake-Refresh Lane & Self-Alarm Audit Landmines

### 4.1 The Landmine (L4 / Product Ruling)
- Self-alarms are rows in `scheduler_sources` enqueued via `daemon/sources/adapters/scheduler.py:2109`.
- The alarm message is composed at ARM TIME (`compose_alarm_message` in `daemon/services/self_alarm_service.py:603-653`).
- **Product Ruling:** `scheduler` is DELIBERATELY ABSENT from `INTERNAL_STAMPED_SOURCE_PREFIXES` in `daemon/constants.py:1376-1399`. Unstamped scheduler wakes are treated as REAL USER messages (anchored with `MessageType.HUMAN` to preserve attestation source U).
- `tests/unit/test_source_stamp_never_stamped_guards.py` pins `scheduler` OUT of the stamped set. **v2 must NOT stamp scheduler wakes.**

### 4.2 Candidate Wake-Refresh Lanes
- **Candidate Lane (a) — Alarm Template Compose:**
  Modify `compose_alarm_message` in `self_alarm_service.py` to append refreshed memory text.
  *Flaws:* Freezes briefing content at alarm ARM time (which could be days earlier); burns prompt tokens on every alarm turn regardless of need; mixes system memory into human message body.
- **Candidate Lane (b) — Parallel Stamped Refresh Block (RECOMMENDED):**
  Create `_maybe_memoir_refresh_message` in `context_messages.py` beside `_maybe_memoir_briefing_message`, evaluated at both assembly sites (:2564-2570 and :2928-2934).
  *Precedent:* Follows the POK refresh seam (`_maybe_pok_context_message` in lines 2572–2588).
  *Advantages:* Evaluates fresh state at execution time; emits a clean stamped context block (`CONTEXT_KIND_MEMOIR_REFRESH`); compaction-hoistable; never touches the human message stamp.
  *Status:* Carried as open question PENDING-ARCHITECT with recommendation (b).

---

## 5. Background Workers: The Embedding Worker Exemplar

- **Exemplar anchor:** `daemon/services/memoir_embedding_worker.py` (932 lines).
- The poignancy worker and reflection worker in v2 MUST clone this architectural pattern:
  1. **Lifespan registration:** Created in `daemon/api.py` lifespan context (`app.state.memoir_embedding_worker`).
  2. **Dedicated Executor:** Uses `ThreadPoolExecutor(max_workers=2, thread_name_prefix="memoir-embed")`. `asyncio.to_thread` is STRICTLY FORBIDDEN (D3.5, test-pinned) to avoid starving the shared event-loop pool.
  3. **Tick loop exception isolation:** The tick loop catches and logs per-tick exceptions; `asyncio.CancelledError` propagates cleanly for graceful shutdown.
  4. **Call-time knob re-reading:** Re-reads configuration every tick. If knob is OFF, logs one park message, sleeps, and resumes automatically when turned ON.
  5. **Attempt tracking & terminal markers:** Uses attempt counters (`attempts >= 5`) and terminal failure markers (e.g. `terminal:<reason>`) to prevent poisoned rows from blocking the queue window.
  6. **Pinnable tick lines:** Emits structured, test-assertable log lines: `[MemoirEmbed] pending=N embedded=K ...`.

---

## 6. LLM Failover Lane & Execution Bounds

- **Failover service anchor:** `daemon/services/llm_failover.py` (lines 700–807).
- Function: `invoke_raw_with_failover(factory, llm_config, *, transient_max=3, timeout_max=2, wall_clock_cap_s=45.0)`.
- **Requirements:**
  - `factory` must be a zero-arg callable that re-reads `current_failover_url()` on every invocation (closing over a static URL breaks dynamic failover).
  - Nested calls are not supported.
  - LLM extraction must be tolerant (using `_extract_json_object` from `daemon/services/skill_search_service.py:374`). Parse failure must fall back gracefully, never raising unhandled exceptions.
  - Token estimation: Uses `daemon.loader.estimate_tokens` (`cl100k_base` tiktoken encoding) for measuring pass budgets.

---

## 7. Notify User Tool & Keepsake Proposals

- **Tool anchor:** `daemon/tools/notify_user.py` (190 lines).
- **Constraints:**
  - Accepts a single string `message` (1..2,000 chars); no structured JSON payload.
  - Destination resolved server-side from session `original_source` binding. If unbound (e.g. background scheduler session), returns typed refusal `notify_no_bound_chat_source`.
  - Ari-only 3-layer authorization gate.
  - Configuration (`NotifyUserConfig` in `daemon/config.py:3166-3224`): max 6 notifications per hour, minimum 60s interval.
- **Keepsake Proposal Discipline:**
  - The "at most ONE gentle proposal notification per day" gate is LANE-OWNED state (stored in a date-keyed memoir metadata row or audit table).
  - The notification message is human-friendly and concise.
  - Full details appear in the wake briefing proposals zone.

---

## 8. Configuration & Kill-Switch Pattern (DR-3)

- **Configuration anchor:** `daemon/config.py` (lines 3480–3571, class `MemoirConfig`).
- **Authority module:** `daemon/tools/memoir_knob.py`.
- **The DR-3 Truth Table:**
  - Absent / empty string (`""` / `None`) -> Defaults to **ON** (`True`) + emits ONE per-boot WARNING.
  - Explicit truthy (`"true"`, `"1"`, `"yes"`, `"on"`) -> **ON** (`True`) + INFO log.
  - Explicit falsy (`"false"`, `"0"`, `"no"`, `"off"`) -> **OFF** (`False`) + INFO log.
  - Present but unparseable garbage (`"banana"`, `"maybe"`) -> **INDETERMINATE** -> fails **CLOSED / OFF** (`False`) + per-boot WARNING naming the invalid value.
- **Model-layer validator:** Uses Pydantic `mode="before"` validator that NEVER raises exceptions and never aborts daemon boot.
- **v2 application:** All new v2 knobs (`ENSEMBLE_MEMOIR_REFLECTION_ENABLED`, `ENSEMBLE_MEMOIR_POIGNANCY_ENABLED`, `ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED`) MUST replicate this exact truth table.

---

## 9. Tool Registration & Census Pin Checklist

When registering `memoir_about`:
1. `daemon/tools/memoir.py`: Add `memoir_about` to `MEMOIR_TOOL_NAMES` frozenset (line 160) and update `CATEGORY_DOC`.
2. `daemon/tools/_tool_registry.py`: Add to `_TOOL_FACTORIES` under category `"memoir"` (lines 303-314) and update `KNOWN_SOURCE_ONLY_TOOLS` (line 1851).
3. `agents/ari/meta.json`: `tools.allow` already holds `"memoir"`, which is category-scoped.
4. Regenerate metrics: Run `python -m daemon.checkpoint_metrics` to update `.agents/shared/metrics/tool-categories.json` (NEVER hand-edit).
5. Update unit test resolution sets: `tests/unit/test_memoir_registration.py`, `tests/integration/test_lcancheck_family_separation.py`, `tests/unit/test_service_tool_flag_off_byte_identical.py`.
6. **Category ordinal rule:** NEVER cite the category ordinal as a fixed number in code or documentation. Always derive it via grep at ceremony time!

---

## 10. Dormant Hooks & Ready Foundations

1. **Persona State Machine:**
   `MemoirPersonaRepository` (`daemon/repositories/memoir/repository.py:163-231`) already has `create_draft`, `submit`, and `approve`.
   The human-lane guard `_require_human_lane` is fully active and tested. v2 JEV hooks directly into `create_draft(source="ari")` and `submit()`.
2. **Superseded Pointer:**
   `mark_superseded` in `MemoirRepository` (`repository.py:896`) is defined and tested. Reflection synthesis verbs (`REFINE`, `CORRECT`) will exercise this pointer via the service layer (preserving tool purity).
3. **Release Staging:**
   `scripts/upgrade/stage.sh` handles building `releases/<VERSION>/`, generating the 4-way SHA manifest, and cutting the git tag. Coordinate the version slot (v0.29.8+) via Shared Meta KV census at stage time. Never promote in this lane.
