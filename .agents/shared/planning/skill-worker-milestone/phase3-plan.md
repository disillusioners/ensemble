# Phase 3: Multi-Metric A/B Scoring

## Objective
Replace the single-metric `_pick_winner()` (completion_rate only) with a 5-metric composite score. Add `ab_test_group` column to `skill_usage_records` for test-period isolation. Move aggregation from Python-side to SQL-side. Update sample size to 20 and tie-breaking to favor the challenger.

## Coupling
- **Depends on**: Phase 1 + Phase 2 (for producing clean attribution data — but code is independent)
- **Coupling type**: loose (different files for logic, but depends on Phase 1 Task 1.0 for schema columns)
- **Shared files with other phases**: `daemon/config.py` (Phase 4 reads `SkillEvolutionConfig`)
- **Shared APIs/interfaces**: `get_ab_comparison_stats()` return shape changes (additive)
- **Why this coupling**: Phase 3 changes the A/B resolution math, which is independent of how skills get injected. The data quality improvement from Phases 1-2 makes the composite score *meaningful*, but the code compiles and tests independently.

## Context
- `_pick_winner()` is a nested function inside `check_ab_test_resolution()` at `skill_evolution_service.py:697-710`
- `get_ab_comparison_stats()` is at `skill_metrics_service.py:903-1001`
- `_completion_rate_for()` is at `skill_metrics_service.py:1001-1021` — wraps `SkillUsageRepository.get_stats()`
- `get_stats()` is at `repository.py:995-1051` — loads ALL records then counts in Python (O(n))
- `SkillUsageRecord` model: `daemon/repositories/skill/models.py:304-411` — 14 columns, NO `ab_test_group`
- `_ensure_postgres_columns()` pattern: `daemon/manager.py:2500+` — uses `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`

## Tasks

### Task 3.1: Verify Schema Columns (Migrated in Phase 1 Task 1.0)

**File**: `daemon/repositories/skill/models.py`

Add column to the `SkillUsageRecord` model (after `created_at`, ~line 391):

```python
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

**W6 — `ab_test_group` NULL semantics**: NULL means "not under test". Records with `ab_test_group IS NULL` are EXCLUDED from A/B-scoped queries (they belong to the pre-test or no-test period). Only non-NULL values participate in A/B comparison stats. The `get_stats_filtered()` method below enforces this.

Add index:
```python
    __table_args__ = (
        Index("ix_skill_usage_records_skill_id", "skill_id"),
        Index("ix_skill_usage_records_instance_id", "skill_id"),
        Index("ix_skill_usage_records_instance_feedback", "instance_id", "feedback_applied"),
        Index("ix_skill_usage_records_ab_group", "ab_test_group"),  # NEW
    )
```

**File**: `daemon/manager.py` — Add to `_ensure_postgres_columns()` (after the skill_bank columns block, ~line 3130):

```python
            # ── SkillUsageRecord ab_test_group (2026-07-15) ──
            # Milestone 2 Phase 3: A/B test-period isolation.
            # Tags usage records with the active A/B test group UUID
            # so get_ab_comparison_stats() can filter to only test-period
            # records instead of ALL historical records.
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS ab_test_group TEXT",
            "ALTER TABLE skill_usage_records ADD COLUMN IF NOT EXISTS superseded BOOLEAN NOT NULL DEFAULT false",
            "CREATE INDEX IF NOT EXISTS ix_skill_usage_records_ab_group ON skill_usage_records(ab_test_group)",
```

**File**: SQLite migration — Create `daemon/migrations/versions/20260715_000001_skill_usage_ab_test_group.sql`:
```sql
ALTER TABLE skill_usage_records ADD COLUMN ab_test_group TEXT;
ALTER TABLE skill_usage_records ADD COLUMN superseded BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_skill_usage_records_ab_group ON skill_usage_records(ab_test_group);
```
(Remember: .sql migration is NO-OP on PostgreSQL — handled by `_ensure_postgres_columns()`)

### Task 3.2: Set `ab_test_group` When Recording Usage Records

**File**: `daemon/services/skill_metrics_service.py`

In `record_task_completion()` → `_record_one()` helper (lines 662-673), set `ab_test_group` from the skill's active test:

```python
# Before creating the usage record, look up the skill's ab_test_group
_ab_group = getattr(skill, "ab_test_group", None) if skill else None

self.usage_repo.create(
    skill_id=skill_id,
    project_id=project_id,
    instance_id=instance_id,
    agent_id=agent_id,
    selected=True,
    applied=False,
    task_succeeded=task_succeeded,
    iterations=iterations,
    duration_seconds=duration_seconds,
    fallback=fallback,
    ab_test_group=_ab_group,  # NEW — tag with test period
)
```

**Note**: Need to fetch the skill object to read `ab_test_group`. If the skill isn't loaded in the loop, add a `skill_repo.get(skill_id)` lookup (best-effort, soft-fail):

```python
# In _record_one(), before the create():
try:
    skill = self.skill_repo.get(skill_id)
    _ab_group = getattr(skill, "ab_test_group", None) if skill else None
except Exception:
    _ab_group = None
```

### Task 3.3: SQL Aggregation with A/B Group Filtering

**File**: `daemon/repositories/skill/repository.py`

Add a new method `get_stats_filtered()` to `SkillUsageRepository` (after `get_stats()`, ~line 1051):

```python
def get_stats_filtered(
    self,
    skill_id: str,
    ab_test_group: str | None = None,
) -> dict[str, Any]:
    """Compute aggregated stats via SQL (not Python-side).

    When ab_test_group is provided, filters to ONLY records tagged
    with that group — enabling clean A/B test-period isolation.

    C2: ALWAYS excludes superseded records (WHERE superseded = FALSE)
    from completion_rate calculations. SUPERSEDED records are neutral
    outcomes from worker reuse — they should not count as success or
    failure.

    W6: When ab_test_group is None, includes ALL non-superseded records
    (general stats). When ab_test_group is a specific value, includes only
    records tagged with that group (test-period isolation). NULL
    ab_test_group records are excluded from A/B-scoped queries.

    Returns a dict with: total, selected, applied, completions,
    fallbacks, avg_iterations, avg_duration, completion_rate,
    applied_rate, fallback_rate.
    """
    with Session(self.engine) as session:
        base_filter = SkillUsageRecord.skill_id == skill_id
        if ab_test_group is not None:
            stmt = select(
                func.count().label("total"),
                func.sum(case((SkillUsageRecord.selected == True, 1), else_=0)).label("selected"),
                func.sum(case((SkillUsageRecord.applied == True, 1), else_=0)).label("applied"),
                func.sum(case((SkillUsageRecord.task_succeeded == True, 1), else_=0)).label("completions"),
                func.sum(case((SkillUsageRecord.fallback == True, 1), else_=0)).label("fallbacks"),
                func.avg(SkillUsageRecord.iterations).label("avg_iterations"),
                func.avg(SkillUsageRecord.duration_seconds).label("avg_duration"),
            ).where(
                base_filter,
                SkillUsageRecord.ab_test_group == ab_test_group,
                SkillUsageRecord.superseded == False,  # C2: exclude SUPERSEDED
            )
        else:
            stmt = select(
                func.count().label("total"),
                func.sum(case((SkillUsageRecord.selected == True, 1), else_=0)).label("selected"),
                func.sum(case((SkillUsageRecord.applied == True, 1), else_=0)).label("applied"),
                func.sum(case((SkillUsageRecord.task_succeeded == True, 1), else_=0)).label("completions"),
                func.sum(case((SkillUsageRecord.fallback == True, 1), else_=0)).label("fallbacks"),
                func.avg(SkillUsageRecord.iterations).label("avg_iterations"),
                func.avg(SkillUsageRecord.duration_seconds).label("avg_duration"),
            ).where(
                base_filter,
                SkillUsageRecord.superseded == False,  # C2: exclude SUPERSEDED
            )

        row = session.exec(stmt).first()
        if row is None or (row.total or 0) == 0:
            return {
                "total": 0, "selected": 0, "applied": 0,
                "completions": 0, "fallbacks": 0,
                "avg_iterations": 0.0, "avg_duration": 0.0,
                "completion_rate": 0.0, "applied_rate": 0.0,
                "fallback_rate": 0.0,
            }

        total = int(row.total or 0)
        return {
            "total": total,
            "selected": int(row.selected or 0),
            "applied": int(row.applied or 0),
            "completions": int(row.completions or 0),
            "fallbacks": int(row.fallbacks or 0),
            "avg_iterations": float(row.avg_iterations or 0.0),
            "avg_duration": float(row.avg_duration or 0.0),
            "completion_rate": (int(row.completions or 0) / total) if total else 0.0,
            "applied_rate": (int(row.applied or 0) / total) if total else 0.0,
            "fallback_rate": (int(row.fallbacks or 0) / total) if total else 0.0,
        }
```

**Import additions** at top of `repository.py`:
```python
from sqlalchemy import func, case
```

### Task 3.4: Add Composite Score Configuration

**File**: `daemon/config.py`

Add weight fields to `SkillEvolutionConfig` (after `max_extensions`, ~line 512):

```python
    # ── Multi-metric composite scoring (Milestone 2 Phase 3) ──
    # Weights for the 5-metric composite A/B winner score.
    # All weights should sum to 1.0 (not enforced — the composite
    # normalizes by dividing by the sum of active weights).
    ab_weight_completion: float = Field(default=0.35)
    ab_weight_applied: float = Field(default=0.20)
    ab_weight_efficiency: float = Field(default=0.20)
    ab_weight_fallback: float = Field(default=0.15)
    ab_weight_speed: float = Field(default=0.10)
```

**Also update**: `ab_sample_size` default from 10 to 20:
```python
    ab_sample_size: int = Field(default=20)  # Changed from 10
```

**W5 — Migration policy for existing OPEN A/B tests**:

Changing the default from 10→20 affects A/B tests that are already in progress. Three policy options:

| Option | Description | Impact |
|--------|-------------|--------|
| A: Grandfather | Existing tests use old threshold (10); new tests use 20 | Requires storing per-test sample_size on SkillABTest row |
| B: Force-resolve | Immediately resolve all open tests with comparisons >= 10 using old completion_rate | Clean break; may resolve tests prematurely |
| C: Silent upgrade | All tests immediately use new threshold (20) | Tests that were near 10 now need 10 more comparisons |

**Decision: Option C (Silent upgrade)**. Rationale:
- Simplest implementation — no per-test threshold storage needed
- Existing tests with <20 comparisons simply collect more data (they were already going to collect more)
- Tests with >=10 comparisons but <20 get 10 more comparisons — acceptable delay
- No force-resolve needed (no premature decisions)
- The threshold is read from config at resolution-check time, not stored per-test

If a test has already reached 10 comparisons and the difference is significant, it can be manually resolved via the `winner_id` parameter on `check_ab_test_resolution()`.

### Task 3.5: Implement Composite Score Calculation

**File**: `daemon/services/skill_metrics_service.py`

Add a private method `_composite_score()` (after `_completion_rate_for()`, ~line 1021):

```python
def _composite_score(
    self,
    stats: dict[str, Any],
    global_baselines: dict[str, float],
) -> float:
    """Compute the weighted composite score for a skill variant.

    Combines 5 metrics into a single [0.0, 1.0] score:
    - completion_rate (35%): task success rate
    - applied_rate (20%): agent adoption rate
    - efficiency_score (20%): normalized inverse iterations
    - low_fallback_rate (15%): 1 - fallback_rate
    - speed_score (10%): normalized inverse duration

    Normalization for efficiency and speed uses global baselines:
        efficiency = baseline_avg_iterations / actual_avg_iterations
        speed = baseline_avg_duration / actual_avg_duration
    Both capped to [0.0, 1.0]. When baseline is 0 or missing,
    defaults to 0.5 (neutral).

    Args:
        stats: Output of get_stats_filtered() for this variant.
        global_baselines: Dict with 'avg_iterations' and 'avg_duration'
            computed across ALL skills (the normalization reference).

    Returns:
        Composite score in [0.0, 1.0].
    """
    cfg = self.config

    completion_rate = float(stats.get("completion_rate", 0.0) or 0.0)
    applied_rate = float(stats.get("applied_rate", 0.0) or 0.0)
    fallback_rate = float(stats.get("fallback_rate", 0.0) or 0.0)
    low_fallback_rate = 1.0 - fallback_rate

    # Efficiency normalization
    actual_iter = float(stats.get("avg_iterations", 0.0) or 0.0)
    baseline_iter = float(global_baselines.get("avg_iterations", 0.0) or 0.0)
    if baseline_iter > 0 and actual_iter > 0:
        efficiency = min(1.0, baseline_iter / actual_iter)
    else:
        efficiency = 0.5  # Neutral when no data

    # Speed normalization
    actual_dur = float(stats.get("avg_duration", 0.0) or 0.0)
    baseline_dur = float(global_baselines.get("avg_duration", 0.0) or 0.0)
    if baseline_dur > 0 and actual_dur > 0:
        speed = min(1.0, baseline_dur / actual_dur)
    else:
        speed = 0.5  # Neutral when no data

    w_completion = float(getattr(cfg, "ab_weight_completion", 0.35))
    w_applied = float(getattr(cfg, "ab_weight_applied", 0.20))
    w_efficiency = float(getattr(cfg, "ab_weight_efficiency", 0.20))
    w_fallback = float(getattr(cfg, "ab_weight_fallback", 0.15))
    w_speed = float(getattr(cfg, "ab_weight_speed", 0.10))

    score = (
        completion_rate * w_completion
        + applied_rate * w_applied
        + efficiency * w_efficiency
        + low_fallback_rate * w_fallback
        + speed * w_speed
    )
    return score
```

Add a helper for global baselines:

```python
def _get_global_baselines(self) -> dict[str, float]:
    """Compute global average iterations + duration across all skills.

    Used as normalization baseline for efficiency/speed scores.
    """
    try:
        return self.usage_repo.get_global_averages()
    except Exception:
        return {"avg_iterations": 0.0, "avg_duration": 0.0}
```

**Add to `SkillUsageRepository`** (`repository.py`):
```python
def get_global_averages(self) -> dict[str, float]:
    """Compute global avg iterations + duration across all records."""
    with Session(self.engine) as session:
        stmt = select(
            func.avg(SkillUsageRecord.iterations).label("avg_iterations"),
            func.avg(SkillUsageRecord.duration_seconds).label("avg_duration"),
        )
        row = session.exec(stmt).first()
        return {
            "avg_iterations": float(row.avg_iterations or 0.0) if row else 0.0,
            "avg_duration": float(row.avg_duration or 0.0) if row else 0.0,
        }
```

### Task 3.6: Update `get_ab_comparison_stats()` to Use Composite Score

**File**: `daemon/services/skill_metrics_service.py`

Rewrite `get_ab_comparison_stats()` (lines 903-1001) to:
1. Use `get_stats_filtered()` with `ab_test_group` instead of `_completion_rate_for()` (which loads ALL records)
2. Compute composite scores for both variants
3. Return composite scores + difference in the stats dict

```python
async def get_ab_comparison_stats(
    self,
    ab_test_group: str,
) -> dict[str, Any]:
    # ... existing setup ...
    def _compute() -> dict[str, Any]:
        test = self.ab_test_repo.get_by_group(ab_test_group)
        if test is None:
            return {  # ... same zeros dict ... }

        # Use SQL aggregation filtered by test group
        stats_a = self.usage_repo.get_stats_filtered(
            test.skill_id_old, ab_test_group=ab_test_group
        )
        stats_b = self.usage_repo.get_stats_filtered(
            test.skill_id_new, ab_test_group=ab_test_group
        )

        # Compute composite scores
        baselines = self._get_global_baselines()
        score_a = self._composite_score(stats_a, baselines)
        score_b = self._composite_score(stats_b, baselines)
        difference = abs(score_a - score_b)

        # ... existing comparisons/extension_count logic ...
        return {
            "skill_id_a": test.skill_id_old,
            "skill_id_b": test.skill_id_new,
            "completion_rate_a": stats_a["completion_rate"],
            "completion_rate_b": stats_b["completion_rate"],
            "composite_score_a": score_a,   # NEW
            "composite_score_b": score_b,   # NEW
            "difference": difference,        # Now based on composite
            "comparisons": comparisons,
            "extension_count": extension_count,
            "ready_to_resolve": ready,
            "needs_more_data": needs_more,
        }
    return await asyncio.to_thread(_compute)
```

### Task 3.7: Update `_pick_winner()` to Use Composite Score + Challenger Tie-Break

**File**: `daemon/services/skill_evolution_service.py`

Replace the nested `_pick_winner()` inside `check_ab_test_resolution()` (lines 697-710):

```python
    # Helper: pick winner by composite score.
    # Tie-breaking: challenger (variant B / new) wins ties.
    def _pick_winner() -> tuple[Optional[str], Optional[str]]:
        score_a = float(stats.get("composite_score_a", 0.0) or 0.0)
        score_b = float(stats.get("composite_score_b", 0.0) or 0.0)
        # Challenger (B) wins ties — changed from incumbent-wins-ties
        if score_b >= score_a:
            return (
                stats.get("skill_id_b"),  # winner = new
                stats.get("skill_id_a"),  # loser = old
            )
        return (
            stats.get("skill_id_a"),  # winner = old
            stats.get("skill_id_b"),  # loser = new
        )
```

**Key changes from original:**
1. Uses `composite_score_a/b` instead of `completion_rate_a/b`
2. Tie-breaking inverted: `score_b >= score_a` (challenger wins ties) instead of `rate_a >= rate_b` (incumbent wins ties)
3. Returns `(skill_id_b, skill_id_a)` first when B wins — note the order swap

## Key Files

| File | Change Type | Purpose |
|------|------------|---------|
| `daemon/repositories/skill/repository.py` | MODIFY | Add `get_stats_filtered()`, `get_global_averages()` |
| `daemon/config.py` | MODIFY | Add weight fields, change `ab_sample_size` default to 20 |
| `daemon/services/skill_metrics_service.py` | MODIFY | Add `_composite_score()`, `_get_global_baselines()`, rewrite `get_ab_comparison_stats()` |
| `daemon/services/skill_evolution_service.py` | MODIFY | Rewrite `_pick_winner()` to use composite score + challenger tie-break |

## Constraints
- PostgreSQL is PRIMARY — use `_ensure_postgres_columns()` for `ab_test_group` (🔴 critical constraint)
- `get_stats_filtered()` must use SQL `func.sum(case(...))` — compatible with both SQLite and PostgreSQL
- Weights default to 35/20/20/15/10 but are configurable via `SKILL_EVOLUTION_AB_WEIGHT_*` env vars
- Composite score is additive to the stats dict — old keys preserved for backward compat
- Global baselines default to 0.5 (neutral) when no data exists
- **W5**: Existing open A/B tests silently upgrade to new sample size (20). No per-test threshold storage; config is read at resolution-check time.
- **W6**: `ab_test_group IS NULL` means "not under test" — excluded from A/B-scoped queries. Only non-NULL values participate in A/B comparison stats.
- **C2**: `superseded=True` records are excluded from ALL rate calculations (completion_rate, applied_rate, fallback_rate) via `WHERE superseded = FALSE`

## Deliverables
- [ ] `ab_test_group` column added to `SkillUsageRecord` (dual SQLite + PostgreSQL)
- [ ] Usage records tagged with `ab_test_group` during recording
- [ ] `get_stats_filtered()` with SQL aggregation + group filtering
- [ ] `get_global_averages()` for normalization baselines
- [ ] 5 composite weight fields in `SkillEvolutionConfig`
- [ ] `_composite_score()` implementation
- [ ] `get_ab_comparison_stats()` uses filtered stats + composite scores
- [ ] `_pick_winner()` uses composite score with challenger tie-break
- [ ] `ab_sample_size` default changed to 20
- [ ] W5: Migration policy documented (silent upgrade, no per-test threshold)
- [ ] `superseded` boolean column added (C2 — for finalize-on-replace records)
- [ ] `get_stats_filtered()` excludes superseded records from rate calculations
- [ ] W6: NULL ab_test_group semantics documented (excluded from A/B-scoped queries)
- [ ] Unit test: composite score calculation with known inputs
- [ ] Unit test: tie-breaking favors challenger
- [ ] Unit test: A/B stats only use test-period records

## Test Strategy

### Unit Tests
```python
def test_composite_score_basic():
    """Composite score weights all 5 metrics."""
    stats = {
        "completion_rate": 0.8, "applied_rate": 0.5,
        "fallback_rate": 0.1, "avg_iterations": 3.0, "avg_duration": 60.0,
    }
    baselines = {"avg_iterations": 5.0, "avg_duration": 100.0}
    score = metrics_service._composite_score(stats, baselines)
    # efficiency = 5/3 = 1.0 (capped), speed = 100/60 = 1.0 (capped)
    # low_fallback = 0.9
    # score = 0.8*0.35 + 0.5*0.20 + 1.0*0.20 + 0.9*0.15 + 1.0*0.10
    #       = 0.28 + 0.10 + 0.20 + 0.135 + 0.10 = 0.815
    assert abs(score - 0.815) < 0.01

def test_tie_break_challenger_wins():
    """Equal composite scores → challenger (B) wins."""
    stats = {
        "composite_score_a": 0.5, "composite_score_b": 0.5,
        "skill_id_a": "old", "skill_id_b": "new",
    }
    winner, loser = _pick_winner_with_stats(stats)
    assert winner == "new"
    assert loser == "old"

def test_ab_stats_filtered_by_group():
    """get_stats_filtered only returns test-period records."""
    # 1. Insert records with ab_test_group="group-1" and without
    # 2. Call get_stats_filtered(skill_id, ab_test_group="group-1")
    # 3. Assert only group-1 records counted
```
