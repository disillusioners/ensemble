# Phase 1: Message Meta Tag Skill Loading

## Objective
Enable explicit skill loading via a `<meta>` tag embedded in the message body. Extend the injection pipeline to trigger on ANY incoming message (not just the first), enabling worker reuse with different skills. This is the core mechanism for the skill-per-worker pattern.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `instance_messaging.py` (Phase 6 tests touch this); `skill_metrics_service.py` (C2 finalize-on-replace calls into metrics service)
- **Shared APIs/interfaces**: New `parse_meta_tag()` function + `inject_explicit_skill()` method + `finalize_superseded_skills()` method
- **Why this coupling**: Phase 1 is the foundation. Phases 2 (auto_load ordering), 5 (tester workflow), and 6 (tests) depend on this mechanism.

## Context
- The injection pipeline currently runs inside `if not is_retry:` at line 1680 of `instance_messaging.py`
- The gate `is_retry=False` means injection runs on the FIRST message only
- There is NO existing meta/json/tag parsing on user message content — the message string flows through as-is
- The dedup-merge logic at line 2003 (`list(dict.fromkeys(existing + list(_ids)))`) is already idempotent
- Clone-on-miss: `SkillCloneService.clone_on_miss_sync(name, agent_id, project_id)` at `skill_clone_service.py:143-196`
- **Usage records are created at task completion** (not injection time). When worker reuse REPLACES `last_injected_skill_ids`, skill A's contribution from turn N is silently discarded (see C2 — finalize-on-replace).

## Tasks

### Task 1.0: Schema Migration Prerequisite (ab_test_group + superseded columns)

> **⚠️ BLOCKING PREREQUISITE for Tasks 1.5+1.6.** The `finalize_superseded_skills()` method writes `superseded=True` to `SkillUsageRecord`. This column MUST exist before that method can run. This task was moved from Phase 3 to Phase 1 to resolve the schema contradiction under parallel execution.

**File**: `daemon/repositories/skill/models.py`

Add TWO columns to the `SkillUsageRecord` model (after `created_at`, ~line 391):

```python
    # A/B test-period isolation (originally Phase 3, moved to Phase 1
    # as schema prerequisite for all phases that write usage records).
    # NULL = not under test. Non-NULL = tagged with active A/B test group UUID.
    ab_test_group: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        max_length=64,
    )
    # C2: SUPERSEDED flag for finalize-on-replace records.
    # When a worker is reused with a different skill, the old skill's
    # usage record is marked superseded=True — a neutral outcome
    # EXCLUDED from completion_rate calculations.
    superseded: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
    )
```

Add indexes to `__table_args__`:
```python
    __table_args__ = (
        Index("ix_skill_usage_records_skill_id", "skill_id"),
        Index("ix_skill_usage_records_instance_id", "skill_id"),
        Index("ix_skill_usage_records_instance_feedback", "instance_id", "feedback_applied"),
        Index("ix_skill_usage_records_ab_group", "ab_test_group"),  # NEW
        # Issue 3: Composite index for aggregation queries used by
        # get_stats_filtered() — supports both A/B-scoped and general
        # stats without full-table scans as usage records grow.
        Index("ix_skill_usage_records_skill_created", "skill_id", "created_at"),  # NEW
    )
```

**File**: `daemon/manager.py` — Add to `_ensure_postgres_columns()` (after the skill_bank columns block, ~line 3130):

```python
            # ── SkillUsageRecord ab_test_group + superseded (2026-07-15) ──
            # Milestone 2: A/B test-period isolation + C2 finalize-on-replace.
            # Moved to Phase 1 as schema prerequisite — Phase 1.5 writes
            # superseded=True, Phase 3 writes ab_test_group, Phase 4 reads
            # avg_iterations/avg_duration via aggregation queries.
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS ab_test_group TEXT",
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS superseded BOOLEAN NOT NULL DEFAULT false",
            "CREATE INDEX IF NOT EXISTS ix_skill_usage_records_ab_group ON skill_usage_records(ab_test_group)",
            "CREATE INDEX IF NOT EXISTS ix_skill_usage_records_skill_created ON skill_usage_records(skill_id, created_at)",
```

**File**: SQLite migration — Create `daemon/migrations/versions/20260715_000001_skill_usage_new_columns.sql`:
```sql
ALTER TABLE skill_usage_records ADD COLUMN ab_test_group TEXT;
ALTER TABLE skill_usage_records ADD COLUMN superseded BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_skill_usage_records_ab_group ON skill_usage_records(ab_test_group);
CREATE INDEX IF NOT EXISTS ix_skill_usage_records_skill_created ON skill_usage_records(skill_id, created_at);
```
(Remember: .sql migration is NO-OP on PostgreSQL — handled by `_ensure_postgres_columns()`)

**W6 — `ab_test_group` NULL semantics**: NULL means "not under test". Records with `ab_test_group IS NULL` are EXCLUDED from A/B-scoped queries. Only non-NULL values participate in A/B comparison stats.

### Task 1.1: Create Meta Tag Parser Utility

**File**: `daemon/services/skill_meta_parser.py` (NEW)

**Purpose**: Parse `<meta>` tags from message content. Extract `load_skill` key. Security-hardened.

```python
# daemon/services/skill_meta_parser.py

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── C1 FIX: Use (.*?) to capture everything between tags, let json.loads ──
# handle brace matching. The previous \{.*?\} regex broke on nested JSON
# like {"load_skill":"x","opts":{"nested":true}} — the non-greedy .*?
# stopped at the first }, truncating the payload.
_META_TAG_RE = re.compile(r"<meta>(.*?)</meta>", re.DOTALL | re.IGNORECASE)

# Schema allow-list: only these keys are recognized. Unknown keys
# are logged as warnings and silently ignored (defense-in-depth).
_ALLOWED_META_KEYS: frozenset[str] = frozenset({"load_skill"})


def parse_meta_tag(message: str) -> tuple[str, dict | None]:
    """Extract and remove ALL <meta> tags from message content.

    Security-hardened parser (C1 fix):
    1. Finds ALL <meta>...</meta> blocks via finditer
    2. Last-wins policy: if multiple tags, the LAST valid one wins
    3. isinstance(data, dict) guard against non-dict JSON (arrays, strings)
    4. Schema allow-list: unknown keys logged + ignored
    5. ALL meta tags stripped from message (agent never sees control data)
    6. Audit log: every processed tag logged at INFO level

    Args:
        message: The raw incoming message content.

    Returns:
        Tuple of (cleaned_message_with_all_meta_tags_stripped, parsed_dict_or_None).
        Returns (original_message, None) when no valid meta tag is found.
    """
    matches = list(_META_TAG_RE.finditer(message))
    if not matches:
        return message, None

    last_valid: dict | None = None

    for i, match in enumerate(matches):
        raw_content = match.group(1).strip()
        tag_label = "last" if i == len(matches) - 1 else f"#{i + 1}"

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.warning(
                f"<meta> tag {tag_label} JSON parse failed: {e}. Ignoring this tag."
            )
            continue

        if not isinstance(data, dict):
            logger.warning(
                f"<meta> tag {tag_label} is not a JSON object "
                f"(type={type(data).__name__}), ignoring."
            )
            continue

        # Schema allow-list: warn on unknown keys but don't reject
        unknown_keys = set(data.keys()) - _ALLOWED_META_KEYS
        if unknown_keys:
            logger.warning(
                f"<meta> tag {tag_label} contains unknown keys "
                f"{unknown_keys} — ignoring them."
            )

        # Last-wins: each valid tag overwrites the previous
        last_valid = data

    # Strip ALL meta tags from message (even malformed ones)
    cleaned = _META_TAG_RE.sub("", message).rstrip()

    if last_valid is not None:
        logger.info(
            f"[MetaTag] Parsed <meta> tag: {last_valid} "
            f"(from {len(matches)} tag(s), last-wins)"
        )

    return cleaned, last_valid


def extract_load_skill(meta: dict | None) -> str | None:
    """Extract the load_skill value from a parsed meta dict.

    Args:
        meta: Parsed meta dict, or None.

    Returns:
        The skill name (stripped string) or None if absent/invalid.
    """
    if meta is None:
        return None
    skill_name = meta.get("load_skill")
    if isinstance(skill_name, str) and skill_name.strip():
        return skill_name.strip()
    return None
```

**Parsing logic detail:**
- **Regex**: `<meta>(.*?)</meta>` with `DOTALL` (newlines in JSON) and `IGNORECASE` (`<META>` or `<Meta>` also match). Captures everything between tags; `json.loads` handles brace matching correctly.
- **C1 FIX**: The previous regex `\{.*?\}` stopped at the first `}`, truncating nested JSON. Now captures full content between `<meta>` and `</meta>`.
- **Multiple tags**: `finditer()` finds ALL tags. Last valid one wins (overwrites previous). ALL tags stripped from message regardless of validity.
- **Schema allow-list**: Only `load_skill` is recognized. Unknown keys logged + ignored.
- **isinstance guard**: Non-dict JSON (arrays, strings, numbers) rejected with warning.
- **Audit log**: Every successfully parsed tag logged at INFO level with the payload.

### Task 1.2: Create Explicit Skill Injection Method

**File**: `daemon/services/skill_injection_service.py`

**Purpose**: A new method that bypasses the search pipeline and directly injects a named skill.

Add to `SkillInjectionService` class (after `inject_skills()`, ~line 263):

```python
async def inject_explicit_skill(
    self,
    skill_name: str,
    project_id: str | None,
    instance_id: str,
    message_id: str,
    agent_id: str,
) -> tuple[str | None, list[str]]:
    """Inject a specific named skill, bypassing the search pipeline.

    Used by the meta-tag loading path. Resolves the skill name to a
    project-scoped Skill (clone-on-miss from bank if needed), applies
    A/B variant selection if the skill is under test, and returns
    the formatted injection text + skill IDs.

    Unlike inject_skills() which runs BM25→embedding→LLM search,
    this method:
    1. Clones the skill from bank if missing (clone-on-miss)
    2. Resolves name → Skill object
    3. Applies A/B variant selection (same as inject_skills)
    4. Formats the injection text (same formatter)

    Args:
        skill_name: The skill name to load (e.g. "unit-test").
        project_id: Project scope for skill lookup.
        instance_id: For A/B variant hashing.
        message_id: For A/B variant hashing.
        agent_id: For bank template lookup (clone-on-miss).

    Returns:
        Tuple of (injection_text_or_None, skill_ids_list).
        Returns (None, []) on failure (soft-fail).
    """
    if not project_id or not skill_name:
        return None, []

    # ── Clone-on-miss: ensure skill exists in project scope ──
    clone_service = getattr(self, "_clone_service", None)
    if clone_service is not None:
        try:
            skill = await asyncio.to_thread(
                clone_service.clone_on_miss_sync,
                skill_name,
                agent_id,
                project_id,
            )
        except Exception as e:
            logger.warning(
                f"[SkillInjection] clone-on-miss failed for "
                f"'{skill_name}' (agent={agent_id}): {e}"
            )
            return None, []
    else:
        # Fallback: direct repo lookup (no clone-on-miss)
        try:
            skill = await asyncio.to_thread(
                self._skill_repo.get_by_name,
                project_id,
                skill_name,
                0,  # generation=0
            )
        except Exception as e:
            logger.warning(
                f"[SkillInjection] skill lookup failed for "
                f"'{skill_name}': {e}"
            )
            return None, []

    if skill is None:
        logger.warning(
            f"[SkillInjection] Skill '{skill_name}' not found "
            f"in project {project_id[:8]}... or skill bank"
        )
        return None, []

    # ── A/B variant selection (same path as inject_skills) ──
    selected = await self._select_ab_variant(skill, instance_id, message_id)

    # ── Format injection text ──
    injection_text = self._format_injection(
        {"injected": [{"skill": selected, "score": 1.0}], "low_match": []}
    )

    return injection_text, [str(selected.id)]
```

**W1 FIX — Setter pattern instead of constructor injection:**

The `SkillInjectionService` is constructed in `manager.py` BEFORE `_skill_clone_service` is available (chicken-and-egg: the clone service needs the skill repo, which may be wired after the injection service). Use a setter instead of constructor injection.

**Do NOT modify `__init__`.** Instead, add a setter method to `SkillInjectionService`:

```python
def set_clone_service(self, clone_service: Any) -> None:
    """Inject the SkillCloneService after construction (W1 fix).

    Called by EnsembleManager after _skill_clone_service is created.
    Avoids constructor chicken-and-egg: SkillInjectionService is
    constructed before SkillCloneService in manager.py init order.
    """
    self._clone_service = clone_service
```

Initialize `_clone_service` to `None` in `__init__` (add near other instance vars, ~line 170):
```python
self._clone_service: Any = None  # Set via set_clone_service() after construction
```

**Wire-up change** in `daemon/manager.py` (AFTER `_skill_clone_service` is created, search for where it's assigned):
```python
# After: self._skill_clone_service = SkillCloneService(...)
# Add:
if hasattr(self, '_skill_injection_service') and self._skill_injection_service:
    self._skill_injection_service.set_clone_service(self._skill_clone_service)
```

### Task 1.3: Extend Injection Pipeline — Meta-Tag Injection with Finalize-on-Replace

**File**: `daemon/services/instance_messaging.py`

**Purpose**: The core change. Parse the meta tag early in message processing, and if `load_skill` is present, run the explicit skill injection regardless of `is_retry`.

**Change 1**: Parse meta tag at the TOP of `_process_message_with_tracking()` (after line ~1579, before the existing logic):

```python
# At the top of _process_message_with_tracking, after signature:
from .skill_meta_parser import parse_meta_tag, extract_load_skill

# Parse meta tag from message content (line ~1579)
_meta_skill: str | None = None
if message and isinstance(message, str):
    message, _meta = parse_meta_tag(message)
    _meta_skill = extract_load_skill(_meta)
```

**Change 2**: Add meta-tag-driven injection as a SEPARATE block AFTER the `if not is_retry:` block (after line ~2021). This runs on ANY message where `_meta_skill` is set.

**C2 FIX — Finalize-on-Replace**: Before REPLACING `last_injected_skill_ids`, finalize any dropped skills as `SUPERSEDED` so they don't become orphaned pending records.

```python
# ── Meta-tag explicit skill loading (runs on ANY message) ──
# This is separate from the first-message injection pipeline above.
# It enables worker reuse: a worker can receive different skills
# via <meta>{"load_skill": "X"}</meta> on subsequent messages.
#
# C3 INVARIANT: Explicit <meta> injection runs FIRST (REPLACE
# semantics). Auto_load DEDUP-MERGE runs SECOND (additive onto
# the explicit set). This block is the explicit path.
if _meta_skill:
    try:
        # Get instance metadata for project_id + agent_id resolution
        _meta_instance = await asyncio.to_thread(
            self._manager._instance_repository.get, instance_id
        )
        if _meta_instance is not None:
            _meta_project_id = (
                (_meta_instance.instance_metadata or {}).get("project_id")
                if _meta_instance.instance_metadata
                else None
            )
            _meta_agent_id = _meta_instance.agent_id

            injection_service = getattr(
                self._manager, "_skill_injection_service", None
            )
            if injection_service is not None and _meta_project_id:
                (
                    _meta_injection_text,
                    _meta_skill_ids,
                ) = await injection_service.inject_explicit_skill(
                    skill_name=_meta_skill,
                    project_id=_meta_project_id,
                    instance_id=instance_id,
                    message_id=message_id,
                    agent_id=_meta_agent_id,
                )

                if _meta_injection_text:
                    _skill_injection_msg = HumanMessage(
                        content=_meta_injection_text,
                        id=str(uuid.uuid4()),
                    )

                # ── C2 FIX: Finalize-on-Replace ──
                # Before replacing the skill scope, finalize any skills
                # that are being dropped. Without this, skill A's
                # contribution from turn N is silently discarded when
                # turn N+1 brings skill B — leaving orphaned pending
                # records that corrupt ALL Phase 3 metrics.
                if _meta_skill_ids:
                    try:
                        def _replace_with_finalize(
                            _iid: str = instance_id,
                            _new_ids: list[str] = _meta_skill_ids,
                            _msg_id: str = message_id,
                            _agent_id: str = _meta_agent_id,
                            _project_id: str = _meta_project_id or "",
                        ) -> None:
                            inst_repo = self._manager._instance_repository
                            inst = inst_repo.get(_iid)
                            existing: list[str] = []
                            if inst is not None and inst.instance_metadata:
                                raw = inst.instance_metadata.get(
                                    INJECTED_SKILLS_METADATA_KEY
                                ) or []
                                if isinstance(raw, list):
                                    existing = [str(x) for x in raw if x]

                            # Compute dropped skills (in existing but
                            # NOT in new_ids)
                            new_set = set(_new_ids)
                            dropped = [s for s in existing if s not in new_set]

                            # C2: Finalize dropped skills as SUPERSEDED
                            if dropped:
                                metrics_service = getattr(
                                    self._manager,
                                    "_skill_metrics_service",
                                    None,
                                )
                                if metrics_service is not None:
                                    metrics_service.finalize_superseded_skills(
                                        instance_id=_iid,
                                        agent_id=_agent_id,
                                        project_id=_project_id,
                                        dropped_skill_ids=dropped,
                                    )

                            # REPLACE (not merge) — meta-tag loading
                            # establishes a new skill scope.
                            inst_repo.set_metadata(
                                _iid,
                                INJECTED_SKILLS_METADATA_KEY,
                                list(_new_ids),
                            )

                            # Issue 2 FIX: Persist explicitly_replaced_ids
                            # across checkpoints. When the instance is
                            # restored after a crash, _apply_post_cache_appends()
                            # re-runs append_auto_load_skills() with DEDUP-MERGE.
                            # Without this set, the restore would silently
                            # re-introduce skills that were explicitly REPLACED
                            # via <meta> tag — corrupting the REPLACE semantics.
                            #
                            # The dropped skill IDs are recorded here so that
                            # Phase 2's auto_load DEDUP-MERGE can skip them.
                            inst_repo.set_metadata(
                                _iid,
                                "explicitly_replaced_ids",
                                list(dropped),
                            )

                        await asyncio.to_thread(_replace_with_finalize)
                    except Exception as e:
                        logger.warning(
                            f"Failed to persist meta-tag skill scope "
                            f"for {instance_id[:8]}...: {e}"
                        )

                # Track for in-memory feedback attribution
                injection_service.track_injection(
                    instance_id, message_id, _meta_skill_ids,
                )
    except Exception as e:
        logger.warning(
            f"Meta-tag skill loading failed for "
            f"{instance_id[:8]}...: {e}"
        )
```

**Key design decisions:**
1. **REPLACE, not merge**: When a meta-tag skill is loaded, it REPLACES `last_injected_skill_ids` (not dedup-merge). This is because meta-tag loading establishes a NEW skill scope — the old skill is no longer the active one.
2. **C2 Finalize-on-Replace**: Before replacing, dropped skills are finalized as `SUPERSEDED` (neutral outcome — excluded from completion_rate denominator). See Task 1.5 for implementation.
3. **C3 Canonical ordering**: This explicit injection block runs FIRST. Auto_load dedup-merge runs SECOND (additive). Explicit skills are authoritative; auto_load is additive.
4. **Runs on ANY message**: This block is OUTSIDE `if not is_retry:`, so it triggers on subsequent messages too.
5. **Soft-fail**: All failures are caught and logged — never blocks message processing.
6. **Issue 2 FIX — Checkpoint restore safety**: The dropped skill IDs are persisted as `explicitly_replaced_ids` in instance metadata. When the instance is restored after a crash, auto_load's DEDUP-MERGE (Phase 2) reads this set and skips any IDs in it — preventing silent re-introduction of explicitly replaced skills. This set is cleared when a new explicit REPLACE occurs (overwritten with the new dropped set).

### Task 1.4: Update `_build_graph_input` (if needed)

**File**: `daemon/services/instance_messaging.py`

The existing `_build_graph_input()` (lines 82-118) already accepts `skill_injection_msg` and prepends it. Since Task 1.3 sets `_skill_injection_msg`, the existing graph_input construction sites (lines 2035-2054) will automatically include the meta-tag skill injection. **No change needed** — the variable is shared.

### Task 1.5: Implement `finalize_superseded_skills()` — C2 Finalize-on-Replace

**File**: `daemon/services/skill_metrics_service.py`

**Purpose**: When a worker is reused with a different skill, the old skill's contribution must be finalized as `SUPERSEDED` — a neutral outcome that does NOT count as success or failure in completion_rate. This prevents orphaned pending records that corrupt metrics.

**New `SUPERSEDED` outcome** in the usage record lifecycle:

| Outcome | What it means | Effect on metrics |
|---------|--------------|-------------------|
| Normal (task completed) | Task ran to completion with skill active | Counted in completion_rate denominator |
| SUPERSEDED | Skill was replaced before task completed | **Excluded** from completion_rate denominator |

**Implementation:**

Add to `SkillMetricsService`:

```python
def finalize_superseded_skills(
    self,
    instance_id: str,
    agent_id: str,
    project_id: str,
    dropped_skill_ids: list[str],
) -> int:
    """Finalize dropped skills as SUPERSEDED (C2 fix).

    When a worker is reused with a different skill via <meta> tag,
    the old skill's scope is REPLACED. Before that replacement,
    this method creates usage records for the dropped skills with
    a SUPERSEDED outcome.

    SUPERSEDED records are EXCLUDED from completion_rate calculations:
    - They increment total_selections (the skill WAS selected)
    - They do NOT increment total_completions or total_fallbacks
    - They are tagged so get_stats_filtered() can exclude them

    This prevents the "orphaned pending record" problem where
    skill A's contribution from turn N is silently discarded
    when turn N+1 brings skill B.

    Args:
        instance_id: The worker instance.
        agent_id: The agent using the skills.
        project_id: Project scope.
        dropped_skill_ids: Skills being replaced (in old set, not in new).

    Returns:
        Number of SUPERSEDED records created.
    """
    if not dropped_skill_ids:
        return 0

    inserted = 0
    for skill_id in dropped_skill_ids:
        try:
            self.usage_repo.create(
                skill_id=skill_id,
                project_id=project_id or "",
                instance_id=instance_id,
                agent_id=agent_id,
                selected=True,
                applied=False,
                task_succeeded=False,
                iterations=0,
                duration_seconds=0,
                fallback=False,
                superseded=True,  # Column from Task 1.0 schema migration
            )
            # Bump total_selections only (NOT completions/fallbacks)
            self.skill_repo.increment_counter(skill_id, "total_selections", 1)
            inserted += 1
        except Exception as e:
            logger.warning(
                f"[SkillMetrics] Failed to finalize SUPERSEDED record "
                f"for skill {skill_id[:8]}...: {e}"
            )

    if inserted > 0:
        logger.info(
            f"[SkillMetrics] Finalized {inserted} SUPERSEDED record(s) "
            f"for instance {instance_id[:8]}..."
        )
    return inserted
```

**Schema requirement**: The `superseded` and `ab_test_group` columns are created in Task 1.0 (this phase's schema prerequisite). No cross-phase dependency — the column exists before `finalize_superseded_skills()` runs. The `get_stats_filtered()` method in Phase 3 Task 3.3 MUST include `WHERE superseded = FALSE` in its rate queries.

### Task 1.6: Add Orphan-Sweep Maintenance Job (Belt-and-Suspenders)

**File**: `daemon/services/skill_metrics_service.py` + `daemon/services/maintenance_service.py`

**Purpose**: Periodic sweep for stale pending usage records that somehow escaped finalization (crash between injection and completion, edge cases in finalize-on-replace).

```python
async def sweep_orphaned_skill_records(self, max_age_hours: int = 24) -> int:
    """Finalize stale usage records as SUPERSEDED (belt-and-suspenders).

    Scans for usage records that have been pending (no completion
    signal) for longer than max_age_hours. These are orphans from:
    - Crashes between injection and completion
    - Edge cases in finalize-on-replace
    - Instance termination without proper cleanup

    Such records are marked superseded=True to exclude them from
    completion_rate calculations.

    Returns:
        Number of records swept.
    """
    # Implementation: query for records WHERE created_at < threshold
    #   AND task_succeeded=False AND applied=False AND iterations=0
    #   (the "pending" signature)
    # Mark them superseded=True
    ...
```

Register with `MaintenanceService` alongside the existing `_run_skill_metric_scan` (interval configurable, default 24h).

## Key Files

| File | Change Type | Purpose |
|------|------------|---------|
| `daemon/services/skill_meta_parser.py` | NEW | Meta tag parsing utility (C1 security-hardened) |
| `daemon/services/skill_injection_service.py` | MODIFY | Add `inject_explicit_skill()`, add `set_clone_service()` (W1) |
| `daemon/services/instance_messaging.py` | MODIFY | Parse meta tag at top, explicit injection block with finalize-on-replace (C2, C3) |
| `daemon/repositories/skill/models.py` | MODIFY | Add `ab_test_group` + `superseded` columns + indexes (Task 1.0) |
| `daemon/manager.py` | MODIFY | `_ensure_postgres_columns()` for new columns (Task 1.0) + `set_clone_service()` call (W1) |
| `daemon/migrations/versions/20260715_000001_*.sql` | NEW | SQLite migration for new columns (Task 1.0) |
| `daemon/services/skill_metrics_service.py` | MODIFY | Add `finalize_superseded_skills()` (C2), `sweep_orphaned_skill_records()` |

## Constraints
- PostgreSQL is PRIMARY dev/test DB — no SQLite-only syntax
- All changes additive (rollback-safe) — meta tag parsing is a no-op when no `<meta>` tag present
- Soft-fail everywhere — meta tag failures never block message processing
- The meta tag is STRIPPED from the message before the agent sees it (agents should not see control data)
- When meta-tag skill loading occurs, it REPLACES (not merges) the skill scope
- **C2**: Dropped skills are finalized as SUPERSEDED before replacement — never orphaned
- **C3**: Explicit injection runs FIRST (REPLACE), auto_load dedup-merge runs SECOND (additive)
- **W1**: Clone service injected via setter, not constructor

## Deliverables
- [ ] Task 1.0: `ab_test_group` + `superseded` columns added (dual SQLite + PostgreSQL + indexes)
- [ ] `skill_meta_parser.py` with `parse_meta_tag()` (C1 hardened) and `extract_load_skill()`
- [ ] `inject_explicit_skill()` method in `SkillInjectionService`
- [ ] `set_clone_service()` setter (W1), wired in `manager.py`
- [ ] Meta tag parsing + explicit injection block with finalize-on-replace (C2, C3)
- [ ] `finalize_superseded_skills()` method in `SkillMetricsService` (C2)
- [ ] `sweep_orphaned_skill_records()` maintenance job (C2 belt-and-suspenders)
- [ ] Unit test: parse meta tag — basic, multiline, nested JSON, multiple tags (last-wins), malformed
- [ ] Unit test: schema allow-list rejects unknown keys
- [ ] Unit test: inject_explicit_skill resolves name → ID with clone-on-miss
- [ ] Unit test: worker reuse finalizes old skill as SUPERSEDED before replace
- [ ] Unit test: orphan sweep finds stale pending records
- [ ] **Issue 2**: `explicitly_replaced_ids` persisted on REPLACE
- [ ] **Issue 2**: Unit test: crash after explicit REPLACE → restore → assert auto_load skips replaced skill

## Test Strategy

### Unit Tests
```python
# test_skill_meta_parser.py — C1 security hardening

def test_parse_meta_tag_basic():
    msg = 'run unit tests\n<meta>{"load_skill": "unit-test"}</meta>'
    cleaned, meta = parse_meta_tag(msg)
    assert cleaned == "run unit tests"
    assert meta == {"load_skill": "unit-test"}

def test_parse_meta_tag_absent():
    msg = "just a regular message"
    cleaned, meta = parse_meta_tag(msg)
    assert cleaned == msg
    assert meta is None

def test_parse_meta_tag_nested_json():
    """C1 FIX: nested JSON braces must not truncate."""
    msg = '<meta>{"load_skill": "unit-test", "opts": {"verbose": true}}</meta>'
    cleaned, meta = parse_meta_tag(msg)
    assert meta == {"load_skill": "unit-test", "opts": {"verbose": True}}
    assert extract_load_skill(meta) == "unit-test"

def test_parse_meta_tag_malformed_json():
    msg = '<meta>{not json}</meta>'
    cleaned, meta = parse_meta_tag(msg)
    assert cleaned == ""  # tag still stripped
    assert meta is None

def test_parse_meta_tag_non_dict_json():
    """isinstance guard: arrays/strings rejected."""
    msg = '<meta>["load_skill", "unit-test"]</meta>'
    cleaned, meta = parse_meta_tag(msg)
    assert meta is None

def test_parse_meta_tag_multiple_last_wins():
    """Multiple <meta> tags: last valid wins, all stripped."""
    msg = ('<meta>{"load_skill": "first"}</meta>\n'
           'task\n'
           '<meta>{"load_skill": "second"}</meta>')
    cleaned, meta = parse_meta_tag(msg)
    assert extract_load_skill(meta) == "second"
    assert "<meta>" not in cleaned

def test_parse_meta_tag_multiline():
    msg = 'task\n<meta>\n  {"load_skill": "mock-test"}\n</meta>\nmore text'
    cleaned, meta = parse_meta_tag(msg)
    assert extract_load_skill(meta) == "mock-test"
    assert "mock-test" not in cleaned

def test_parse_meta_tag_all_tags_stripped_even_malformed():
    """Malformed tags are still stripped from message."""
    msg = '<meta>{bad}</meta>\ntask\n<meta>{"load_skill": "ok"}</meta>'
    cleaned, meta = parse_meta_tag(msg)
    assert "<meta>" not in cleaned
    assert extract_load_skill(meta) == "ok"

def test_extract_load_skill():
    assert extract_load_skill({"load_skill": "unit-test"}) == "unit-test"
    assert extract_load_skill({"other": "value"}) is None
    assert extract_load_skill(None) is None
```

### C2 Finalize-on-Replace Tests
```python
def test_worker_reuse_finalizes_superseded():
    """C2: Old skill finalized as SUPERSEDED before replacement."""
    # 1. Worker has last_injected_skill_ids = [skill_a]
    # 2. Send message with <meta>{"load_skill": "skill_b"}</meta>
    # 3. Assert: finalize_superseded_skills called with [skill_a]
    # 4. Assert: usage record created for skill_a with superseded=True
    # 5. Assert: last_injected_skill_ids = [skill_b]

def test_same_skill_no_finalize():
    """Re-injecting same skill does NOT finalize (no drop)."""
    # 1. last_injected_skill_ids = [skill_a]
    # 2. Send with <meta>{"load_skill": "skill_a"}</meta>
    # 3. Assert: finalize NOT called (dropped set is empty)

def test_orphan_sweep_finds_stale():
    """Orphan sweep finds records older than threshold."""
    # 1. Create records with pending signature (no completion)
    # 2. Set created_at to > max_age_hours ago
    # 3. Run sweep
    # 4. Assert: records marked superseded=True
```

### Integration Test
```python
# test_meta_tag_skill_loading.py
async def test_worker_receives_skill_via_meta_tag():
    """Send message with meta tag → worker has skill loaded + tracked."""
    # 1. Spawn a worker instance
    # 2. Send: "run unit tests\n<meta>{"load_skill": "unit-test"}</meta>"
    # 3. Assert: instance_metadata["last_injected_skill_ids"] == [unit_test_skill_id]
    # 4. Assert: the skill content appears in the injected HumanMessage

async def test_worker_reuse_different_skill():
    """Worker reuse with different skill finalizes old + sets new."""
    # 1. Spawn worker, send with load_skill=unit-test
    # 2. Assert last_injected_skill_ids == [unit_test_id]
    # 3. Send second message with load_skill=mock-test
    # 4. Assert: SUPERSEDED record created for unit-test
    # 5. Assert last_injected_skill_ids == [mock_test_id] (REPLACED)
```
