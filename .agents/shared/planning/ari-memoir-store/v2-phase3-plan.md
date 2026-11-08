# Phase 3 — The Constellation Layer & `memoir_about` Tool

**File:** `v2-phase3-plan.md`  
**Depends on:** Phase 1 (`v2-phase1-plan.md`), Phase 2 (`v2-phase2-plan.md`).  
**Flags / Decisions consumed:** V2-3, V2-4, V2-8, V2-13 (`v4-federated-sketch.md` §5), V2-22, V2-25.

---

## 1. Objective

Deliver the cold-path Constellation graph layer and the entity-centric retrieval tool:
1. Implement the cold-path entity extraction pipeline in `MemoirConstellationService`: extracts entities from `memoir_episodes.stars[]` and narrative bodies during reflection passes, maps them to canonical stars via deterministic canonicalization rules, and records weighted edges in `memoir_line`.
2. Implement the LightRAG M4 two-tier description synthesizer:
   - *Tier 1 (Small):* Plain concatenation/join of episode excerpts when fragments < 5 or total text < 1200 chars.
   - *Tier 2 (Grown):* LLM summarization into a coherent entity card (~300 words) when thresholds are exceeded and cooldown has elapsed.
   - **Fragments definition:** `COUNT(*)` of distinct `memoir_line` rows (distinct citing episodes), never `SUM(mention_count)` (§2-Q5 / Register #18).
   - **Cooldown carrier:** `memoir_star.description_as_of DATETIME NULL` carries the 24h cooldown and v4 derivation watermark (§2-Q5 / Register #9).
3. Implement organic star heat scoring: `heat = (access_count * 2.0) + recency_factor`, strictly kingdom-local per R1/v4 constraints.
4. Implement the `memoir_about(star: str)` tool:
   - Registers in the existing privileged category `memoir`.
   - Adheres to the two-layer gate (ari-only factory identity + runtime kill-switch).
   - Returns a structured dossier: canonical name, star type, description, recent linked episodes, and 1-hop neighbor stars.
   - Maps all refusal conditions onto the frozen 19-code refusal vocabulary.
   - **Zero-episode edge rule:** A resolved star with ZERO linked episodes returns an **empty-dossier SUCCESS render** (sections say "none"), NOT `memoir_not_found` (§2-Q7).
5. Execute the complete tool registration ceremony and re-stamp `agents/ari/tools_note.md`.

---

## 2. Decisions

| ID | Decision | Rationale (verified anchors) | Rejected Alternatives |
|----|----------|------------------------------|-----------------------|
| **V2-D 3.1** | Cold-path extraction during reflection: Constellation nodes and edges are updated ONLY during background reflection passes, never on the hot `memoir_write` path. | DD-20 invariant: hot append must never block conversation turns. Graph consolidation cost scales with change volume rather than total corpus size. | Synchronous graph extraction during `memoir_write`. |
| **V2-D 3.2** | LightRAG M4 two-tier description thresholds: `MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS = 5`, `MEMOIR_STAR_SUMMARY_MIN_CHARS = 1200`, `MEMOIR_STAR_SUMMARY_COOLDOWN_HOURS = 24`. **Fragments = `COUNT(*)` of distinct `memoir_line` rows, never `SUM(mention_count)`** (§2-Q5 / Register #18). Cooldown carried by `memoir_star.description_as_of` (§2-Q5 / Register #9). | One heavily re-mentioned episode must not alone graduate a star. Accumulate across observations, synthesize over coverage. `last_touched_at` updates on every touch and cannot carry a summarization cooldown clock. | `SUM(mention_count)` (allows 1 repetitive episode to trigger summarization); OR using `last_touched_at` for cooldown. |
| **V2-D 3.3** | Kingdom-local heat scoring (`v4-federated-sketch.md` §5): Heat tracks local attention (`access_count` * 2 + recency prior). Foreign kingdoms have their own attention patterns; heat must never cross kingdom boundaries. | Syncing heat across kingdoms; OR counting background extraction as access. |
| **V2-D 3.4** | `memoir_about` joins the existing `memoir` category: category count stays unchanged; ari's `tools.allow: ["memoir"]` automatically covers it. | Creating a new `constellation` tool category (churns privileged category pins and agent meta configs). |
| **V2-D 3.5** | Frozen 19-code refusal mapping: Map `memoir_about` error conditions onto existing `MEMOIR_CODES`: star not found -> `memoir_not_found`; empty star param -> `memoir_query_required`; star name too long -> `memoir_stars_out_of_bounds`. | Adding a 20th error code (`memoir_star_not_found`). Preserves `assert len(MEMOIR_CODES) == 19` and avoids touching the V-1 truthfulness bar. |
| **V2-D 3.6** | Zero-episode star returns empty-dossier SUCCESS (§2-Q7): If a star exists in `memoir_star` but currently has zero linked episodes, render a valid empty dossier (sections report "none"), NOT `memoir_not_found`. | Returning `memoir_not_found` for zero-episode stars. | V-1 truthfulness bar: returning `memoir_not_found` when the entity exists mislabels real data as missing. |
| **V2-D 3.7** | Category ordinal dynamically grep-derived: All test fixtures and ceremony notes grep-derive the category ordinal at ceremony time; zero hardcoded ordinal numbers in prose. | Hardcoding category #18 or #19 (breaks when sibling lanes merge). |

---

## 3. Domain Detail & Tool Specification

### 3.1 `memoir_about` Tool Signature & Gate Order
```python
def memoir_about(
    star: str,
) -> dict[str, Any] | str:
    """Retrieve an entity dossier from the Constellation graph.
    
    Returns the star's canonical identity, type, consolidated description,
    recent connected episodes ordered by recency/importance, and 1-hop
    neighbor stars.
    """
```
- **Gate Sequence (Auth-First, `_gate` in `daemon/tools/memoir.py:319`):**
  1. Factory-captured `agent_id == "ari"` -> if not, `Error: memoir_about REFUSED — reason=memoir_not_authorized`.
  2. Runtime knob `resolve_memoir_knob(config)` -> if OFF, `Error: memoir_about REFUSED — reason=memoir_disabled`.
  3. Validate `star`: must be non-empty string, ≤100 chars -> if empty `memoir_query_required`, if too long `memoir_stars_out_of_bounds`.
  4. Lookup via `constellation_repo.resolve_star(agent_id, star)` -> if None, `Error: memoir_about REFUSED — reason=memoir_not_found: star not found: <star>`.
  5. If star exists but has 0 linked episodes: assemble and return empty-dossier SUCCESS (description rendered, connected episodes reported as "none", neighbor stars reported as "none").
  6. If star has linked episodes: assemble dossier and return formatted string.

### 3.2 Output Dossier Format
```text
=== STAR: The Emperor (type: person, heat: 42.5) ===
Aliases: my dear, Emperor, He
First Seen: 2026-10-01 | Last Touched: 2026-11-09 | Summary As Of: 2026-11-09 03:15:00 UTC

Description:
The sovereign of the kingdom and Ari's primary conversational counterpart.
Directs system architecture, reviews critical milestones, and ratifies promotions.

Connected Episodes (recent):
- [2026-11-08] Joint design session on Memoir Store v2 architecture
- [2026-11-04] R1-R4 constitutional rulings ratified

Neighbor Stars (1-hop):
- Empress (person) — 14 shared episodes
- agents-ensemble (project) — 38 shared episodes
```

---

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks carried | AC |
|----|------|--------------------|-------------|------------|---------------|----|
| **T3.1** | Implement `MemoirConstellationService` in `daemon/services/memoir_constellation_service.py` to handle cold-path star extraction from episodes and deterministic alias linking. | Service parses `episodes.stars[]` and narrative text; creates/updates `memoir_star` and `memoir_line`. | `daemon/services/memoir_constellation_service.py` | P1 | R9 | AC1 |
| **T3.2** | Implement LightRAG M4 two-tier description synthesizer measuring fragments as `COUNT(*)` of lines and updating `description_as_of`. | Entities with <5 distinct citing episodes join raw snippets; entities with ≥5 distinct episodes trigger LLM summarization with 24h cooldown. | `daemon/services/memoir_constellation_service.py` | T3.1 | R3 | AC2 |
| **T3.3** | Implement `memoir_about` tool in `daemon/tools/memoir.py` with two-layer gate, frozen 19-code refusal mapping, and zero-episode empty-dossier success render. | Calling tool with valid star returns formatted card; zero-episode star returns empty-dossier success; missing/invalid star returns mapped refusal codes without raising exceptions. | `daemon/tools/memoir.py` | P1, T3.2 | — | AC3 |
| **T3.4** | Update tool registration in `daemon/tools/_tool_registry.py` and `MEMOIR_TOOL_NAMES` frozenset in `daemon/tools/memoir.py`. | `MEMOIR_TOOL_NAMES` holds 6 tools; tool factory instantiates `memoir_about` for Ari; denied for other agents. | `daemon/tools/memoir.py`, `daemon/tools/_tool_registry.py` | T3.3 | R7 | AC4 |
| **T3.5** | Regenerate checkpoint metrics and re-stamp `agents/ari/tools_note.md` with updated memoir tool documentation. | `checkpoint_metrics.py` generates updated JSON; `tools_note.md` accurately documents `memoir_about`. | `agents/ari/tools_note.md`, `.agents/shared/metrics/tool-categories.json` | T3.4 | — | AC5 |
| **T3.6** | Implement test packs for constellation service, fragment counting, description thresholds, zero-episode success, refusal mapping, and tool registration. | Unit and integration tests pass with 100% assertion coverage; mutation legs verified. | `tests/unit/test_memoir_constellation.py`, `tests/unit/test_memoir_about_tool.py` | T3.3, T3.4 | — | AC6 |

---

## 5. Acceptance Criteria

| ID | Criterion |
|---|---|
| **AC1** | Cold-path extraction: Processing an episode with `stars=["The Emperor", "agents-ensemble"]` creates/updates canonical stars and records `memoir_line` edges; alias resolution maps "the Emperor" to "The Emperor". |
| **AC2** | Two-tier description threshold: Entity with 1 episode mentioned 10 times (`COUNT(*)=1`, `SUM=10`) stays Tier 1 (plain join); entity with 5 distinct episodes (`COUNT(*)=5`) and >1200 chars triggers Tier 2 LLM summarization and sets `description_as_of`; subsequent runs within 24h use cached summary. |
| **AC3** | `memoir_about` tool safety & empty dossier: Unauthorized agent calling `memoir_about` returns `memoir_not_authorized`; knob OFF returns `memoir_disabled`; missing star returns `memoir_query_required`; non-existent star returns `memoir_not_found`; star with 0 linked episodes returns empty-dossier SUCCESS. No exceptions raised. |
| **AC4** | Registration integrity: `len(MEMOIR_TOOL_NAMES) == 6`; `discover_source_only_tool_names` includes `memoir_about`; tool resolves cleanly when Ari instance spawns. |
| **AC5** | Category ordinal safety: Category ordinal is verified by dynamic grep derivation in test assertions; zero test files contain hardcoded category numbers. |
| **AC6 (Mutation)** | Measuring fragments via `SUM(mention_count)` flips AC2 RED; returning `memoir_not_found` for zero-episode stars flips AC3 RED; bypassing alias resolution flips AC1 RED. |

---

## 6. Test Packs

- `test/packs/memoir_v2_constellation_service_test.sh`: Tests entity extraction, alias normalization, line edge recording, and heat calculations.
- `test/packs/memoir_v2_star_description_test.sh`: Tests `COUNT(*)` fragment metric, Tier-1 vs Tier-2 description generation, `description_as_of` timestamp, and 24h cooldown.
- `test/packs/memoir_v2_about_tool_test.sh`: Tests `memoir_about` execution, zero-episode success render, card formatting, two-layer security gate, and frozen 19-code refusal mapping.
- `test/packs/memoir_v2_tool_registration_test.sh`: Tests tool discovery, registry factories, metrics regeneration, and grep-derived ordinal assertions.

---

## 7. Flag Consumption

- **V2-3:** Cold-path graph drawing during reflection.
- **V2-4:** LightRAG M4 two-tier star description summarization.
- **V2-8:** `memoir_about` refusal mapping onto existing 19 codes.
- **V2-13:** Kingdom-local heat stats and deterministic star canonicalization per `v4-federated-sketch.md` §5.
- **V2-22 / §2-Q5:** `COUNT(*)` fragment metric and `description_as_of` cooldown carrier.
- **V2-25 / §2-Q7:** Zero-episode empty-dossier success render.
