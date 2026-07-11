# Skill Evolution Configuration

Reference for `SkillEvolutionConfig` (`daemon/config.py:473`). All settings can be
overridden via environment variables prefixed with `SKILL_EVOLUTION_` (see
[Environment Variable Override](#environment-variable-override)).

## Overview

The skill evolution system lets agents discover, inject, analyze, and mutate
skills over time. Configuration lives in `SkillEvolutionConfig` (a
`pydantic-settings` `BaseSettings` subclass), mounted on the root `Config` as
`config.skill_evolution`. Services read it from the manager via
`self._config.skill_evolution` — do **not** import `daemon.config` directly from
factory-injected tools.

Two settings follow the LLM config as a fallback when not set:
`embedding_base_url` and `embedding_api_key` fall back to `LLMConfig.base_url`
and `LLMConfig.api_key` respectively. `evolution_model` and `analysis_model`
fall back to the main LLM model.

---

## 1. Embedding Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `embedding_model` | `text-embedding-3-small` | OpenAI-compatible embedding model |
| `embedding_dimensions` | `1536` | Embedding vector dimensions |
| `embedding_base_url` | `None` | Embedding API endpoint (falls back to `LLMConfig.base_url`) |
| `embedding_api_key` | `None` | Embedding API key (falls back to `LLMConfig.api_key`) |

---

## 2. Evolution Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `evolution_model` | `None` | Model used for Tier 3 evolution. Falls back to main LLM model. |
| `analysis_model` | `None` | Cheap model for Tier 2 analysis. Falls back to main LLM model. |

---

## 3. Injection Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `max_inject_skills` | `2` | Max skills fully injected per message |
| `min_score_full_inject` | `0.7` | Min score for full injection |
| `min_score_low_match` | `0.3` | Min score for the low-match list |
| `bm25_top_k` | `10` | BM25 pre-filter candidate count |
| `llm_select_top_k` | `5` | LLM selection candidate count |

---

## 4. Trigger Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `default_task_count_threshold` | `20` | Tasks before periodic analysis |
| `default_daily_scan_hour` | `3` | Hour (24h) for daily trigger scan (3 AM) |
| `metric_scan_interval_hours` | `24.0` | Maintenance scan interval in hours |

---

## 5. A/B Testing

| Setting | Default | Description |
|---------|---------|-------------|
| `ab_sample_size` | `10` | Comparisons collected before resolving an A/B test |
| `ab_min_difference` | `0.15` | Min `completion_rate` difference (15%) required to resolve. If the difference is below the threshold after `ab_sample_size` comparisons, the test is extended by another `ab_sample_size` comparisons. |
| `max_extensions` | `3` | After this many extensions, the test is force-resolved by raw `completion_rate` even if the difference is below `ab_min_difference`. |

### Resolution Rules

1. **Threshold met** → resolve with winner = higher `completion_rate`.
2. **Threshold missed + `extension_count < max_extensions`** → extend by another
   `ab_sample_size` comparisons.
3. **Threshold missed + `extension_count >= max_extensions`** → force-resolve by
   raw `completion_rate`, recorded with `reason='force_resolved_max_extensions'`.

With defaults this means a maximum of `(max_extensions + 1) * ab_sample_size`
= `4 * 10` = **40 comparisons** per A/B test.

---

## 6. Capture Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `capture_min_iterations` | `5` | Min iterations for capture |
| `capture_min_duration_seconds` | `60` | Min duration (seconds) for capture |

---

## 7. Enabling Skill Injection per Agent

Skill injection is **opt-in** per agent. Add `"skill_injection": true` to the
agent's `meta.json`:

```json
{
  "id": "my-agent",
  "skill_injection": true
}
```

`AgentRegistry.discover()` reads this field (`daemon/registry.py:214`) and
`AgentMetadata.skill_injection` is consumed by `SkillInjectionService` and the
metrics service to gate injection. Agents without this field (or with
`"skill_injection": false`) skip injection entirely.

---

## 8. Environment Variable Override

All settings can be overridden via environment variables with the
`SKILL_EVOLUTION_` prefix. Pydantic-settings reads them at startup; set them in
`.env` or your process environment.

```bash
# Increase A/B sample size
SKILL_EVOLUTION_AB_SAMPLE_SIZE=20

# Allow more injected skills per message
SKILL_EVOLUTION_MAX_INJECT_SKILLS=3

# Use a larger embedding model
SKILL_EVOLUTION_EMBEDDING_MODEL=text-embedding-3-large

# Pin a cheap model for Tier 2 analysis
SKILL_EVOLUTION_ANALYSIS_MODEL=gpt-4o-mini

# Pin a strong model for Tier 3 evolution
SKILL_EVOLUTION_EVOLUTION_MODEL=gpt-4o
```

> **Caveat:** pydantic-settings does **not** coerce empty-string env vars to
> `None` for `str | None` fields. Setting
> `SKILL_EVOLUTION_EMBEDDING_BASE_URL=""` stores `""`, not `None` — callers
> must handle fallback semantics at the call site.

---

## 9. Cost Tiers

The skill evolution pipeline is split into four cost tiers. Each tier
short-circuits if its predecessor decides no action is needed.

| Tier | Cost | Service | Triggered By | Config |
|------|------|---------|--------------|--------|
| **Tier 0** | Free | `SkillMetricsService` | Job completion (passive recording of usage / completion metrics) | — |
| **Tier 1** | Free | `SkillTriggerEngine` | Periodic scan (`default_task_count_threshold`, `default_daily_scan_hour`) — pure rule check, no LLM | — |
| **Tier 2** | Cheap LLM | `SkillEvolutionService.analyze_skill` | Tier 1 emits an `"analyze"` verdict — runs static analysis on a flagged skill | `analysis_model` |
| **Tier 3** | Main LLM | `SkillEvolutionService.evolve_skill` / `evolve_fix` / `evolve_derived` | Tier 2 verdict decides evolution is needed — actually mutates skill content | `evolution_model` |

Tier 2 and Tier 3 are enqueued as `skill_analysis` and `skill_evolution` jobs
through `SkillJobDispatcher` (`daemon/services/skill_job_dispatcher.py`).

A/B testing sits **above** Tier 3: every Tier 3 `evolve_fix` creates a tweaked
copy and starts an A/B test; resolution follows the rules in
[A/B Testing](#5-ab-testing).