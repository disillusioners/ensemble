# Skill Evolution System

> Replaces the external OpenSpace MCP with a native, deeply-integrated skill engine. Skills are living entities: dynamically selected per task, self-improving over time, and versioned through lineage.

## Overview

The Skill Evolution System gives agents the ability to **search a skill library**, **have relevant skills auto-injected into their prompts**, and **improve skills automatically** based on task outcomes.

Key concepts:

- **Skills** are markdown instruction blocks that agents load at runtime.
- **Dynamic selection** means the right skill is injected automatically based on the task at hand — no manual retrieval required.
- **Self-improvement** means skills that fail or underperform are repaired, specialized, or replaced via an on-demand `skill-keeper` agent.
- **A/B testing** ensures that evolved skills genuinely outperform their predecessors.
- **Lineage tracking** preserves version history so no change is ever destructive.

---

## Architecture

### Database Schema (6 tables)

All tables live in `daemon/repositories/skill/models.py`.

| Table | Purpose |
|-------|---------|
| `skills` | The skill document itself — name, description, content (markdown body), category, generation, lineage origin, A/B test group, and six denormalized counters (`total_selections`, `total_applied`, `total_completions`, `total_fallbacks`, `consecutive_failures`, `last_used_at`). Skills are soft-deleted (status = `inactive`), never hard-deleted. |
| `skill_lineage` | Append-only parent/child graph recording every evolution step. Composite PK `(skill_id, parent_skill_id)` with ON DELETE CASCADE. Each edge carries a one-line `change_summary` and a full `content_diff`. Forms a DAG where roots are `lineage_origin='imported'` / `generation=0`. |
| `skill_usage_records` | One row per skill application to a task. Captures `selected` (was the skill injected?), `applied` (did the agent consume it?), `task_succeeded`, `iterations`, `duration_seconds`, `fallback`, and two nullable feedback fields (`feedback_applied`, `feedback_note`). All fields are stamped by the Phase 4 `SkillMetricsService` on task completion. |
| `skill_triggers` | Declarative condition → action rules. `condition_json` is type-specific (`keyword`, `regex`, `embedding_match`). `project_id IS NULL` means global; otherwise project-scoped. Supports soft-disable via `is_enabled`. |
| `skill_embeddings` | Cached embedding vectors for high-frequency trigger queries. One or more per skill. The `embedding` column is a plain JSON array of floats (not BYTEA) so the schema works on both SQLite and PostgreSQL. |
| `skill_ab_tests` | A/B test bucket grouping old + new variants under a shared `ab_test_group` UUID. Tracks `comparisons` (side-by-side feedback events), `extension_count` (how many times the test was extended), `resolved_at`, and `winner_skill_id`. |

### Tiered Cost Model

Most skill-system operations cost **zero additional LLM calls**:

| Tier | Cost | Operations |
|------|------|-----------|
| **Tier 0** | Free | Skill metrics recording, counter bumps, A/B stat reads |
| **Tier 1** | Free | BM25 keyword search, embedding re-rank, deterministic variant selection |
| **Tier 2** | Cheap LLM | Skill analysis (deciding *what* to evolve) — uses `analysis_model` |
| **Tier 3** | Evolution model | Actual skill content mutation — uses `evolution_model` |

### Three-Stage Search Pipeline

`SkillSearchService.search()` in `daemon/services/skill_search_service.py` runs three filters:

1. **BM25 keyword prefilter** — pure-Python BM25 (k1=1.5, b=0.75) over `name + description + content`. Returns top `bm25_top_k` (default 10) candidates. Cheap, deterministic, typo-tolerant.
2. **Embedding re-rank** — user message embedded via `SkillEmbeddingService.embed_user_message()`, scored against cached per-skill trigger embeddings using cosine similarity (pure Python, no numpy). Score is the **MAX** across all cached triggers for each skill. Returns top `llm_select_top_k` (default 5).
3. **LLM final selection** — asks the LLM to pick up to `max_inject_skills` (default 2) from the re-ranked candidates, returning JSON with `selected` and `low_match` arrays.

Graceful degradation: Stage 2 failure falls back to BM25-only with 0.0 similarity scores. Stage 3 failure falls back to the top `max_results` from stage 2.

### Skill Injection Flow

`SkillInjectionService` in `daemon/services/skill_injection_service.py` bridges search results to the LangGraph prompt:

1. Calls `SkillSearchService.search()` with `max_results` from config.
2. For each skill with `ab_test_group` set and `status='ab_testing'`, fetches all variants and picks **deterministically** via `hash(instance_id + message_id + ab_test_group) % len(variants)`. Hash-based allocation is stable across retries and re-emissions (unlike `random.choice()`).
3. Bumps the A/B comparison counter so the Phase 4 metrics pipeline tracks side-by-side events.
4. Renders results into a `[System Inject]` block with full markdown for injected skills and a one-liner for low-match candidates.
5. The formatted block is injected as a `HumanMessage` **before** the user's message in the LangGraph state.

Gating: `_process_message_with_tracking()` in `instance_messaging.py` checks `agent_meta.skill_injection` before calling the injection service. An agent must have `skill_injection: true` in `meta.json` to receive injected skills.

### Skill-Keeper Agent

`agents/skill-keeper/` is a system agent spawned on-demand via the job queue. It **never** participates in normal agent workflows.

**Two operating modes:**

- **Tier 2 — Analysis** (cheap model): inspects skill stats and decides *what* to do — `FIX`, `DERIVED`, or `CAPTURED` — and writes a concrete `direction` paragraph for the evolution pass.
- **Tier 3 — Evolution** (evolution model): produces the actual new content.

**Evolution types:**

| Type | Trigger | Action | Lineage |
|------|---------|--------|---------|
| **FIX** | Low completion rate, high fallback count, consecutive failures | Repair in place; same name, new generation | New version row; parent = previous; origin = `fixed` |
| **DERIVED** | Skill useful but overloaded / needs narrower scope | Create a variant with new name | New row; parent = original; origin = `derived` |
| **CAPTURED** | Successful complex task completed without any skill applied | Extract pattern as brand-new skill | New row; no parent; origin = `captured` |

**A/B testing rules (FIX only):**

- Both old and new versions stay active during the test.
- Deterministic selection via hash (same instance + same message → same variant on retry).
- After `ab_sample_size` comparisons (default 10) with `ab_min_difference` (default 15%), the loser is deactivated.
- If difference is below threshold, the test is extended (up to `max_extensions` times).
- Skills with **zero** successful uses can be deactivated immediately, bypassing A/B.
- Every evolution re-generates trigger query embeddings so the search index reflects the new content.

---

## Configuration

`SkillEvolutionConfig` in `daemon/config.py` (env prefix: `SKILL_EVOLUTION_`):

### Embedding

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `embedding_model` | `SKILL_EVOLUTION_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model identifier |
| `embedding_dimensions` | `SKILL_EVOLUTION_EMBEDDING_DIMENSIONS` | `1536` | Vector dimensions (must match model) |
| `embedding_base_url` | `SKILL_EVOLUTION_EMBEDDING_BASE_URL` | `None` | Custom embedding endpoint (falls back to LLMConfig.base_url) |
| `embedding_api_key` | `SKILL_EVOLUTION_EMBEDDING_API_KEY` | `None` | Embedding API key (falls back to LLMConfig.api_key) |

### Evolution Models

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `evolution_model` | `SKILL_EVOLUTION_EVOLUTION_MODEL` | `None` | Model for Tier 3 evolution (falls back to main model) |
| `analysis_model` | `SKILL_EVOLUTION_ANALYSIS_MODEL` | `None` | Cheap model for Tier 2 analysis |

### Injection

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `max_inject_skills` | `SKILL_EVOLUTION_MAX_INJECT_SKILLS` | `2` | Maximum skills injected per message |
| `min_score_full_inject` | `SKILL_EVOLUTION_MIN_SCORE_FULL_INJECT` | `0.7` | Score threshold for full injection |
| `min_score_low_match` | `SKILL_EVOLUTION_MIN_SCORE_LOW_MATCH` | `0.3` | Score threshold for low-match listing |
| `bm25_top_k` | `SKILL_EVOLUTION_BM25_TOP_K` | `10` | BM25 prefilter candidate count |
| `llm_select_top_k` | `SKILL_EVOLUTION_LLM_SELECT_TOP_K` | `5` | Re-ranked candidates sent to LLM selection |

### Triggers

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `default_task_count_threshold` | `SKILL_EVOLUTION_DEFAULT_TASK_COUNT_THRESHOLD` | `20` | Minimum selections before trigger checks a skill |
| `default_daily_scan_hour` | `SKILL_EVOLUTION_DEFAULT_DAILY_SCAN_HOUR` | `3` | Hour (UTC) for daily trigger scan |
| `metric_scan_interval_hours` | `SKILL_EVOLUTION_METRIC_SCAN_INTERVAL_HOURS` | `24.0` | How often the metric scan job runs (hours) |

### A/B Testing

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `ab_sample_size` | `SKILL_EVOLUTION_AB_SAMPLE_SIZE` | `10` | Comparisons needed before resolution check |
| `ab_min_difference` | `SKILL_EVOLUTION_AB_MIN_DIFFERENCE` | `0.15` | Minimum completion-rate difference to pick a winner (15%) |
| `max_extensions` | `SKILL_EVOLUTION_MAX_EXTENSIONS` | `3` | Maximum test extensions before force-resolution |

### Capture

| Field | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `capture_min_iterations` | `SKILL_EVOLUTION_CAPTURE_MIN_ITERATIONS` | `5` | Minimum LLM iterations to qualify for CAPTURED |
| `capture_min_duration_seconds` | `SKILL_EVOLUTION_CAPTURE_MIN_DURATION_SECONDS` | `60` | Minimum wall-clock seconds to qualify for CAPTURED |

---

## Agent Setup

### Enabling Skill Injection

Add `"skill_injection": true` to the agent's `meta.json`:

```json
{
  "id": "developer",
  "skill_injection": true,
  ...
}
```

Without this flag, the injection service skips skill injection even if other conditions are met.

### Granting Skill Tools

To give an agent the 6 `dynamic-skill` tools, add `"dynamic-skill"` to both `innate_skills` and `tools.allow` in `meta.json`:

```json
{
  "id": "developer",
  "innate_skills": ["opencode", "dynamic-skill"],
  "tools": {
    "allow": ["bash", "filesystem", "self", "help", "knowledge", "dynamic-skill", "skill-evolution"]
  }
}
```

`"dynamic-skill"` in `innate_skills` loads the skill prompt (the how/when documentation) into the system prompt. `"dynamic-skill"` in `tools.allow` grants actual tool access. Both are required.

### The 6 Dynamic-Skill Tools

All defined in `daemon/tools/skill_tools.py`, registered via `create_skill_tools()` factory:

| Tool | Signature | Description |
|------|-----------|-------------|
| `skill_search` | `(query: str, limit: int = 10) -> str` | Run the three-stage search pipeline (BM25 → embedding → LLM). Returns JSON with `injected` and `low_match` buckets. |
| `skill_list` | `(category: str \| None = None, active_only: bool = True) -> str` | List skills in project + global scope. Human-readable bullet list. |
| `skill_view` | `(skill_id: str) -> str` | Render one skill's full body + lineage as Markdown. Content truncated at 8000 chars. |
| `skill_create` | `(name: str, description: str, content: str, category: str = "workflow") -> str` | Create a new skill row. Auto-assigns project scope from instance context. |
| `skill_fix` | `(skill_id: str, issue_description: str, suggested_fix: str \| None = None) -> str` | Record a fix request (dispatched to skill-keeper, never performed inline). |
| `skill_feedback` | `(skill_id: str, applied: bool \| None = None, note: str = "") -> str` | Stamp feedback onto the most recent usage record via `SkillMetricsService`. |

All tools follow a **soft-fail** pattern: if the underlying service is unavailable, they return a clear `"... not yet available"` message rather than raising.

---

## Skill Lifecycle

### Creation

- **Manual**: `skill_create` tool or `POST /api/skills` — write name, description, content, category.
- **Auto-captured**: `SkillMetricsService` detects successful complex tasks (above `capture_min_iterations` and `capture_min_duration_seconds`) where no skill was applied. Enqueues a CAPTURED job to the skill-keeper agent.

### Selection

Every incoming user message runs through `SkillInjectionService`:

1. BM25 prefilter over all active skills (project-scoped + global overlay).
2. Embedding re-rank against cached trigger query vectors.
3. LLM final selection of top `max_inject_skills` candidates.
4. A/B variant selection (hash-based, deterministic) for skills in active tests.
5. Skills injected as a `HumanMessage` block before the user's message.

### Metrics Recording

On task completion, `SkillMetricsService.record_task_completion()` stamps usage records:

- `selected=True` for every injected skill.
- `applied` set later via `skill_feedback`.
- Denormalized counters bumped atomically on the `skills` row.
- `consecutive_failures` reset on success, incremented on failure.
- `fallback = (consecutive_failures > 0) and (not task_succeeded)`.

### Evolution Triggers

`SkillTriggerEngine` (in `daemon/services/skill_trigger_engine.py`) runs periodic scans:

- Flags skills where `total_selections >= default_task_count_threshold` and `completion_rate < threshold` or `consecutive_failures > 0`.
- Enqueues a `skill_analysis` job for the skill-keeper agent.
- The skill-keeper decides `should_evolve` and the evolution type.

### Evolution

The skill-keeper agent performs one of three mutations:

- **FIX**: Creates a new `generation` of the same skill, inherits `name`, writes lineage edge with `parent = old_version.id` and `origin = fixed`. Opens an A/B test between old and new.
- **DERIVED**: Creates a new skill with a distinct name, lineage edge with `parent = original.id` and `origin = derived`. Original stays active.
- **CAPTURED**: Creates a brand-new skill with no parent and `origin = captured`. Result of a successful task with no applied skill.

### A/B Testing

When a FIX produces a new generation:

1. Both old and new are kept `active` with a shared `ab_test_group`.
2. For every message using either variant, `SkillABTestRepository.increment_comparison()` bumps the counter.
3. After `ab_sample_size` comparisons, `SkillEvolutionService.check_ab_test_resolution()` checks the difference.
4. If `|rate_a - rate_b| >= ab_min_difference`: loser is deactivated, winner remains active.
5. If below threshold and `extension_count < max_extensions`: test is extended.
6. After `max_extensions` extensions with no clear winner: force-resolve by raw completion rate.

### Lineage

Every evolution writes a `SkillLineage` row:

- `skill_id` = new version's ID
- `parent_skill_id` = previous version's ID
- `change_summary` = one-line description of the change
- `content_diff` = unified diff string
- `created_at` = ISO-8601 timestamp

Lineage is **never deleted**. Generations are deactivated, not destroyed.

---

## REST API Endpoints

All in `daemon/routers/skills.py`, mounted under `/api/skills`.

### Skill CRUD

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills` | List skills with `project_id`, `category`, `active_only` filters |
| `POST` | `/api/skills` | Create a new skill |
| `GET` | `/api/skills/{skill_id}` | Get skill detail (body + lineage + metrics in one response) |
| `PUT` | `/api/skills/{skill_id}` | Partial update (refreshes embeddings when content changes) |
| `DELETE` | `/api/skills/{skill_id}` | Soft-delete (deactivate) |
| `POST` | `/api/skills/{skill_id}/deactivate` | Soft-delete alias (returns refreshed row) |
| `POST` | `/api/skills/{skill_id}/share` | Promote project-scoped skill to global library |

### Skill View

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills/{skill_id}/view` | Full skill document + lineage graph bundle |
| `GET` | `/api/skills/{skill_id}/lineage` | Skinny lineage view (parents, children, generation, origin) |

### Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/skills/search` | Run three-stage search pipeline. Body: `{query, project_id?, max_results?}` |

### Metrics & Usage

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills/{skill_id}/metrics` | Per-skill counter bundle + derived rates |
| `GET` | `/api/skills/{skill_id}/usage` | Aggregate usage stats (skill_id + stats bundle) |
| `POST` | `/api/skills/{skill_id}/feedback` | Stamp feedback onto latest usage record |

### Evolution

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/skills/{skill_id}/fix` | Dispatch FIX evolution job to skill-keeper. Returns `202 Accepted` with `job_id` |

### A/B Testing

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills/{skill_id}/ab-test` | Get A/B test status for a skill |
| `POST` | `/api/skills/{skill_id}/ab-test/resolve` | Check/resolve A/B test. Optional `winner_id` query param for forced resolution |

### Triggers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills/triggers` | List trigger rules |
| `POST` | `/api/skills/triggers` | Create a new trigger rule |
| `PUT` | `/api/skills/triggers/{trigger_id}` | Partial update to a trigger |
| `DELETE` | `/api/skills/triggers/{trigger_id}` | Hard-delete a trigger |

### Response Conventions

- Skill endpoints (`POST`, `GET`, `PUT`, `DELETE`, `/deactivate`, `/share`) return the skill record **directly** — no `{"skill": ...}` envelope.
- `GET /api/skills/{id}` returns skill + inline `lineage` (parents/children) and `metrics`.
- `GET /api/skills/{id}/lineage` returns the flat `SkillLineage` shape directly.
- `GET /api/skills/{id}/metrics` returns the `SkillMetrics` shape directly.
- `POST /api/skills/{id}/feedback` returns `{"recorded": bool}`.
- `POST /api/skills/{id}/fix` returns `202 Accepted` with `{"job_id": str}`.
- `POST /api/skills/{id}/ab-test/resolve` returns the resolution dict directly.

---

## Frontend

The Angular 21 frontend provides a "Skills" section next to "Jobs" in the navigation. Source in `frontend/src/app/`:

| Path | Purpose |
|------|---------|
| `pages/skills/skills.component.*` | Skills list page (card-based, with filters) |
| `pages/skills/skill-detail/skill-detail.component.*` | Skill detail page (content, metrics, lineage, A/B status) |
| `components/skill-card/skill-card.component.*` | Reusable skill card for list views |
| `services/skill.service.ts` | HTTP service covering full CRUD + search + metrics + feedback + A/B + share |
| `models/skill.model.ts` | TypeScript interfaces mirroring all backend shapes |

### Skill Card Features

- **Success rate chip** — `completion_rate` rendered as color-coded percentage (green ≥60%, amber ≥30%, red <30%).
- **A/B test badge** — shown when `status = 'ab_testing'`.
- **Deactivate / Share actions** — buttons on each card.
- **Category filter** — chip-based category selector (workflow, coding, debugging, analysis, communication, review, research, other).

### Skill Detail Features

- **Metrics panel** — counters (`total_selections`, `total_applied`, `total_completions`, `total_fallbacks`) and derived rates.
- **Lineage panel** — parent/child version graph with generation numbers and change summaries.
- **A/B test banner** — shown when enrolled; displays variant IDs, comparison count, and resolution status.
- **Feedback form** — thumbs up/down + optional note.
- **Fix request form** — issue description + optional suggested fix. Dispatches a job and returns the `job_id`.

### Skill Status Lifecycle

| Status | Meaning |
|--------|---------|
| `active` | Normal use, eligible for selection |
| `ab_testing` | Participating in A/B test; A/B banner shown |
| `deactivated` | Present but not selectable; can be re-activated |
| `archived` | Read-only, not selectable, not in default lists |

---

## Key Design Decisions

### Why not hard-delete?

Skills are soft-deleted (status = `inactive`) so usage history remains queryable and lineage stays intact. A generation is deactivated, never deleted.

### Why hash-based A/B allocation?

`random.choice()` produced unstable results across retries (same instance + same message landed on different variants on resume), making A/B comparison statistics noisy. Hashing by `(instance_id, message_id, ab_test_group)` is deterministic across retries and re-emissions.

### Why Tier 2 / Tier 3 split?

Tier 2 (cheap model) decides *what* to evolve. Tier 3 (evolution model) actually rewrites the content. The split avoids paying the cost of a full LLM rewrite when the analysis determines no evolution is needed.

### Why inject as `HumanMessage`?

LangChain's `HumanMessage` signals that the content came from the system orchestrator, not the user. This lets the agent distinguish skill context from user intent and prevents the skill content from being echoed back as a user message.

### Why not use numpy for BM25 / cosine similarity?

Per `ensemble.spec`, the build excludes `numpy`. Both BM25 and cosine similarity are pure Python. BM25 uses `k1=1.5, b=0.75` (literature-standard values). Cosine similarity loops over the vector directly.

### Why are embeddings stored as JSON arrays?

Using `JSONBType` (a JSON array of floats) instead of `BYTEA` / `numpy` / `pickle` means the same schema works on both SQLite and PostgreSQL without any binary serialization layer.

---

## Related Documentation

- [`docs/AGENTS.md`](AGENTS.md) — agent system, `meta.json` schema, innate skills
- [`docs/mcp-integration.md`](mcp-integration.md) — MCP client/server architecture
