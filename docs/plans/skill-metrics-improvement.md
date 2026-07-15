# Skill Metrics Improvement Plan

> Status: DRAFT — Under discussion
> Date: 2026-07-15
> Author: Leader (ensemble multi-agent system)
> Related: Tester Skill Evolution System (merged to latest 2026-07-15)

---

## 1. Problem Statement

The skill evolution system's A/B testing mechanism — its most consequential decision point — determines whether to keep or replace a skill variant using **a single metric (completion_rate)**, despite collecting 9+ metrics per usage event.

### 1.1 Core Problem: Single-Metric A/B Winner Selection

**Current behavior:** `_pick_winner()` in `skill_evolution_service.py:700-710` compares only `completions / total_records`:

```python
def _pick_winner():
    rate_a = float(stats.get("completion_rate_a", 0.0) or 0.0)
    rate_b = float(stats.get("completion_rate_b", 0.0) or 0.0)
    if rate_a >= rate_b:
        return skill_id_a  # Old variant wins ties
    return skill_id_b
```

**Impact:** The system cannot distinguish between variants when both succeed:

| Scenario | Skill A (old) | Skill B (new) | System Says | Should Say |
|----------|--------------|---------------|-------------|------------|
| Both succeed, B is faster | 8 iterations, 120s | 3 iterations, 45s | "Equal" | **B is better** |
| Both succeed, agents prefer B | feedback="confusing" | feedback="clear, helpful" | "Equal" | **B is better** |
| Both succeed, B more adopted | applied 20% | applied 80% | "Equal" | **B is better** |
| Both succeed, B fewer fallbacks | fallback 40% | fallback 5% | "Equal" | **B is better** |

### 1.2 Secondary Problem: Polluted A/B Baselines

**Current behavior:** `get_ab_comparison_stats()` calls `_completion_rate_for(skill_id)` which computes completion rate from **ALL** usage records for that skill, including records accumulated before the A/B test started.

**Impact:** If skill A accumulated 100 usage records before the A/B test (at 80% completion rate), those pre-test records pollute the comparison. The test isn't comparing "A during test period" vs "B during test period."

### 1.3 Tertiary Problem: Python-Side Aggregation Performance

**Current behavior:** `get_stats()` in the usage record repository loads ALL records into Python memory and counts with `sum(1 for r in records if r.task_succeeded)`.

**Impact:** O(n) per comparison check. Degrades as skills accumulate usage records over time.

### 1.4 Additional Findings

- **`consecutive_failures` skips LLM analysis** — Only trigger that goes directly to variant creation (`action="evolve_fix"`) without Tier 2 cheap-LLM sanity check. Three unlucky failures → automatic variant creation.
- **Tie-breaking favors incumbent** — `rate_a >= rate_b` means old variant wins ties. New variant must be *strictly* better.
- **Dual metric sources** — Trigger engine uses denormalized counters (`total_completions / total_selections`), while A/B winner uses raw records (`completions / total_records`). Could theoretically diverge under counter drift.
- **`fallback` heuristic is weak** — Only counts as fallback if `consecutive_failures > 0 AND not task_succeeded`. A skill failing on first use isn't flagged.
- **Small sample size** — 10 comparisons is statistically weak for a 15% difference threshold.

---

## 2. Current Metrics Inventory

### 2.1 Per-Event Data (`skill_usage_records` table — 14 columns)

| Column | Type | What It Measures | Used in A/B Winner? |
|--------|------|-----------------|---------------------|
| `selected` | bool | Skill was injected | ❌ |
| `applied` | bool | Agent explicitly applied skill | ❌ |
| `task_succeeded` | bool | Task completed successfully | ✅ (only this) |
| `iterations` | int | LLM iterations consumed | ❌ |
| `duration_seconds` | int | Wall-clock seconds | ❌ |
| `fallback` | bool | Skill execution fell back | ❌ |
| `feedback_applied` | bool? | Agent's explicit feedback | ❌ |
| `feedback_note` | str | Agent's free-text feedback | ❌ |
| `task_message` | str | Triggering message snapshot | ❌ |
| `agent_id` | str | Which agent used it | ❌ |
| `instance_id` | str | Which instance used it | ❌ |
| `project_id` | str | Project scope | ❌ |
| `skill_id` | str | Which skill | ❌ |
| `created_at` | str | Timestamp | ❌ |

### 2.2 Aggregated Counters (`skills` table — 5 columns)

| Counter | Used in A/B Winner? | Used in Triggers? |
|---------|---------------------|-------------------|
| `total_selections` | ❌ | ✅ |
| `total_applied` | ❌ | ❌ |
| `total_completions` | ❌ | ✅ (via computed rate) |
| `total_fallbacks` | ❌ | ✅ (via computed rate) |
| `consecutive_failures` | ❌ | ✅ |

### 2.3 Data Flow

```
Task Completes
  → record_task_completion()
    → Read last_injected_skill_ids from instance metadata
    → For each injected skill:
      → INSERT skill_usage_record (14 data points)
      → Atomic UPDATE counters on skills table
    → Clear metadata key
    → Check CAPTURED eligibility (5 gates)

Agent calls skill_feedback(skill_id, applied, note)
  → record_feedback()
    → UPDATE latest skill_usage_record (feedback_applied, feedback_note)
    → If applied=True: increment total_applied counter

Trigger Engine (Tier 1, free, rule-based)
  → Reads counters from skills table
  → 5 condition types:
    - low_completion_rate (< 0.3 AND selections >= 5)
    - high_fallback_rate (> 0.5 AND selections >= 5)
    - consecutive_failures (>= 3)
    - task_count_scan (selections >= 20)
    - periodic_scan (last_used > 7 days)
  → Flagged skills → Tier 2 analysis

A/B Testing
  → New variant created (via FIX evolution)
  → Deterministic hash: md5(instance:message:group) → pick variant
  → comparisons += 1
  → After 10 comparisons + 15% difference → resolve
  → Winner = higher completion_rate (ONLY metric)
  → Winner promoted, loser deactivated
```

---

## 3. Proposed Solutions

### Solution A: Multi-Metric Composite Score (Primary Fix)

Replace single-metric `_pick_winner()` with a weighted composite score.

#### Proposed Metrics & Weights

| Metric | Weight | Formula | Rationale |
|--------|--------|---------|-----------|
| **completion_rate** | 35% | completions / total | Primary: task success is most important |
| **applied_rate** | 20% | applied_count / total | Agent adoption: if agents ignore it, it's not useful |
| **efficiency_score** | 20% | normalized inverse iterations | Fewer iterations = more actionable skill |
| **low_fallback_rate** | 15% | 1 - (fallbacks / total) | Fewer fallbacks = more reliable |
| **speed_score** | 10% | normalized inverse duration | Faster completion is better (secondary) |

#### Composite Score Formula

```
score = completion_rate * 0.35
      + applied_rate * 0.20
      + efficiency_score * 0.20
      + low_fallback_rate * 0.15
      + speed_score * 0.10
```

#### Normalization for Efficiency and Speed

Efficiency and speed need normalization since raw iteration counts and durations vary by task type:

```
efficiency_score = baseline_avg_iterations / actual_avg_iterations
  (capped at 0.0-1.0, baseline = global average across all skills)

speed_score = baseline_avg_duration / actual_avg_duration
  (capped at 0.0-1.0, baseline = global average across all skills)
```

If the skill's average iterations is lower than baseline → score > 0.5 (better than average).
If higher → score < 0.5 (worse than average).

#### Configuration

All weights should be configurable via `SkillEvolutionConfig` with env prefix `SKILL_EVOLUTION_`:

```python
ab_weight_completion: float = 0.35
ab_weight_applied: float = 0.20
ab_weight_efficiency: float = 0.20
ab_weight_fallback: float = 0.15
ab_weight_speed: float = 0.10
```

#### Tie-Breaking

Current: `rate_a >= rate_b` → incumbent wins ties.

Proposed: Tie goes to the **new variant** (challenger), not the incumbent. Rationale: If a new skill is *equal* in performance, prefer the evolved version — it may have accumulated improvements that don't show up yet in metrics but will over time.

### Solution B: Test-Period Isolation (Fix Polluted Baselines)

#### Schema Change

Add `ab_test_group` column to `skill_usage_records`:

```
ab_test_group TEXT NULL  -- Set to the A/B test group UUID during testing
```

#### Logic Change

During A/B testing, when the injection pipeline picks a variant and records usage:

```python
# When recording task completion during A/B test:
usage_record.ab_test_group = active_ab_test_group  # Tag with test period
```

When computing A/B comparison stats:

```python
# Instead of ALL records:
_completion_rate_for(skill_id)

# Use only test-period records:
_completion_rate_for(skill_id, ab_test_group=group_id)
```

This ensures A/B comparisons only use records from the test period, giving a clean comparison.

### Solution C: SQL Aggregation (Fix Performance)

Move rate computation from Python-side to SQL-side:

```sql
SELECT 
  COUNT(*) as total,
  SUM(CASE WHEN task_succeeded THEN 1 ELSE 0 END) as completions,
  SUM(CASE WHEN applied THEN 1 ELSE 0 END) as applied_count,
  SUM(CASE WHEN fallback THEN 1 ELSE 0 END) as fallback_count,
  AVG(CASE WHEN iterations IS NOT NULL THEN iterations END) as avg_iterations,
  AVG(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds END) as avg_duration
FROM skill_usage_records 
WHERE skill_id = ? AND ab_test_group = ?
GROUP BY skill_id
```

This returns a single aggregated row instead of loading all records into Python.

### Solution D: Enhance Tier 2 Analysis Prompt (Bonus)

The Tier 2 analysis prompt currently sends 4 metrics + 10 recent records to the LLM. Enhance to include:

```
Skill Performance Summary:
- completion_rate: {rate}
- applied_rate: {rate}
- fallback_rate: {rate}
- avg_iterations: {avg} (baseline: {global_avg})
- avg_duration: {avg}s (baseline: {global_avg}s)
- consecutive_failures: {count}
- feedback_notes (last 10): {notes}

Recent usage (last 10 records):
- succeeded={ok} iterations={n} duration={s}s feedback={note}
```

This gives the LLM full context when deciding how to evolve the skill.

### Solution E: Fix `consecutive_failures` Trigger (Bonus)

Currently the only trigger that skips Tier 2 analysis and goes directly to variant creation.

Proposed: Route through Tier 2 like all other triggers (`action="analyze"` instead of `action="evolve_fix"`). The cheap LLM gets a chance to say "this skill is fine, just unlucky" before creating a variant.

---

## 4. Decisions Pending

| ID | Decision | Options | Recommendation |
|----|----------|---------|----------------|
| D1 | Metric weights | 35/20/20/15/10 or custom | 35/20/20/15/10 (configurable) |
| D2 | Statistical approach | (A) Simple threshold, (B) Bayesian, (C) LLM-assisted | (A) — keep simple, upgrade later |
| D3 | Sample size | 10, 20, 30, 50 | 20 — balance reliability and speed |
| D4 | Scope of this milestone | (A) Scoring only, (B) Scoring + isolation, (C) Full overhaul | (B) — highest impact/effort ratio |
| D5 | Fix Tier 2 prompt | Yes/No | Yes — low effort, high value |

---

## 5. Impact Summary

| Solution | Impact | Effort | Priority |
|----------|--------|--------|----------|
| A: Multi-metric scoring | 🔴 Critical — fixes the core blind spot | Medium | P0 |
| B: Test-period isolation | 🔴 Critical — fixes polluted baselines | Low | P0 |
| C: SQL aggregation | 🟡 Medium — performance at scale | Low | P1 |
| D: Tier 2 prompt enhancement | 🟡 Medium — better evolution decisions | Low | P1 |
| E: Fix consecutive_failures trigger | 🟢 Low — reduces false evolution | Trivial | P2 |

---

## 6. Multi-Skill Attribution Problem

### 6.1 The Attribution Gap

The metrics system assumes one skill per task. In reality, the tester agent can have multiple skills active simultaneously:

- **4 auto_load skills** baked into the system prompt (test-strategy, test-pack-execution, mock-test, unit-test)
- **Up to 2 on-demand skills** injected via the search pipeline per message
- **Total: up to 6 skills active** at the same time

When a task completes, the system records the SAME outcome for ALL injected skills:

```
5 skills active → Task succeeds in 5 iterations
→ ALL skills get: task_succeeded=True, iterations=5, duration=120s
→ No way to know which skill actually contributed
```

### 6.2 Auto-Load Skills Are Invisible to Metrics

The `append_auto_load_skills()` function bakes skills into the system prompt but does NOT write to `last_injected_skill_ids`. This means:

- The 4 most-used tester skills get **zero usage records**
- Zero counters (`total_selections`, `total_completions`, etc.)
- Zero trigger evaluations
- Zero A/B test data
- **They cannot be evolved at all**

Only on-demand skills (injected via `SkillInjectionService`) are tracked.

### 6.3 Co-Injection Correlation Dilution

When 2 on-demand skills are co-injected (the default `max_inject_skills=2`), both share the same outcome label. If one skill is genuinely helpful and the other is noise, the noise skill's `completion_rate` is inflated by the helpful skill's successes. Over many tasks, the noise skill appears equally effective.

### 6.4 The `applied` Flag Is Always Zero

The `total_applied` counter depends entirely on the agent voluntarily calling `skill_feedback(applied=True)`. In practice:

- The `dynamic-skill` innate skill mentions the tool exists
- But tester's `soul.md` and `workflow.md` have ZERO mentions of `skill_feedback`
- No system reminder after task completion
- No enforcement mechanism
- Estimated 5-15% probability an LLM agent calls it without explicit prompting

**Result:** `applied_rate = 0.0` for ALL skills → metric is meaningless.

---

## 7. Proposed Direction: Skill-Per-Worker Pattern

### 7.1 The Core Idea

Instead of loading multiple skills into a single instance (broken attribution), the tester delegates skill-specific execution to dedicated worker instances:

**CURRENT (broken attribution):**

```
┌──────────────────────────────────────────────────────────────────────┐
│ TESTER INSTANCE                                                      │
│                                                                      │
│ System Prompt (4 auto_load baked in — INVISIBLE to metrics):       │
│ ┌──────────────────┐ ┌──────────────────┐                          │
│ │ test-strategy    │ │ test-pack-       │                          │
│ │ (auto_load)      │ │ execution        │                          │
│ │                  │ │ (auto_load)      │ ...                      │
│ └──────────────────┘ └──────────────────┘                          │
│ ┌──────────────────┐ ┌──────────────────┐                          │
│ │ mock-test        │ │ unit-test        │                          │
│ │ (auto_load)      │ │ (auto_load)      │                          │
│ └──────────────────┘ └──────────────────┘                          │
│                                                                      │
│ + Injected on-demand: [code-analyzer]                               │
│                                                                      │
│ ────────────────────────────────────────────────────────────────    │
│ Task completes: ✅ in 5 iterations, 120s                            │
│ ────────────────────────────────────────────────────────────────    │
│                                                                      │
│ Metrics recorded for ALL skills (same outcome):                     │
│   test-strategy     → succeeded  (iter=5, dur=120s)                 │
│   test-pack-exec    → succeeded  (iter=5, dur=120s)                 │
│   mock-test         → succeeded  (iter=5, dur=120s)                 │
│   unit-test         → succeeded  (iter=5, dur=120s)                 │
│   code-analyzer     → succeeded  (iter=5, dur=120s)                 │
│                                                                      │
│ ❓ Who actually helped? Nobody knows.                                │
└──────────────────────────────────────────────────────────────────────┘
```

**PROPOSED (clean attribution):**

```
┌─────────────────────────────────┐       spawn       ┌─────────────────────────────────┐
│ TESTER INSTANCE                 │       with        │ WORKER INSTANCE                 │
│                                 │       one         │                                 │
│ Role: PLANNER + DISPATCHER      │       skill        │ Role: EXECUTOR                 │
│                                 │   ────────────▶   │                                 │
│ Has skill:                      │                    │ Has skill:                     │
│   • test-strategy               │                    │   • unit-test (ONE skill)      │
│     (used for planning)         │                    │                                 │
│                                 │                    │ Decides nothing — just runs    │
│ 1. Analyze incoming task        │                    │ the task with ONE skill loaded │
│ 2. Pick best skill for task     │                    │                                 │
│    (e.g. "unit-test" needed)    │                    │ ────────────────────────────── │
│ 3. Spawn worker with that skill │                    │ Task runs:                     │
│ 4. Receive report               │                    │   ✅ in 3 iterations           │
│                                 │                    │   🔧 8 tool calls              │
│                                 │                    │   ⏱  45 seconds                │
│                                 │                    │ ────────────────────────────── │
│                                 │                    │                                 │
│                                 │                    │ Worker reports back:           │
│                                 │                    │   {                            │
│                                 │                    │     result:    "success",      │
│                                 │                    │     metrics: {                 │
│                                 │                    │       iterations: 3,           │
│                                 │                    │       tool_calls: 8,           │
│                                 │                    │       duration_seconds: 45     │
│                                 │                    │     },                         │
│                                 │                    │     feedback: {                │
│                                 │                    │       helpful: true,           │
│                                 │                    │       reason:                  │
│                                 │                    │         "clear steps"          │
│                                 │                    │     }                          │
│                                 │                    │   }                            │
│                                 │                    │                                 │
│                                 │                    │ ↓                              │
│                                 │                    │ Clean 1:1 attribution ✓        │
└─────────────────────────────────┘                    └─────────────────────────────────┘
```

### 7.2 Why This Solves the Core Problems

| Problem | Current Model | Worker-Per-Skill |
|---------|---------------|------------------|
| Attribution | All skills share same metrics | Each worker has ONE skill — perfect 1:1 attribution |
| Feedback reliability | Agent must voluntarily call `skill_feedback` | Worker reports as part of natural workflow |
| Iteration/duration accuracy | Counts all iterations across all skills | Worker's iterations = skill's iterations |
| Tool call metrics | Can't attribute tool calls to skills | Worker's tool calls are all for that one skill |
| A/B testing | Multiple skills pollute each other's test | Clean variant comparison per worker |
| Auto_load invisibility | Skills in prompt but invisible to metrics | No auto_load needed — skills on workers |

### 7.3 New Metrics This Enables

| Metric | Current Model | Worker Model |
|--------|---------------|--------------|
| Tool calls per skill | Can't attribute | Worker's tool calls = skill's tool calls |
| Time per skill | Shared across all skills | Worker duration = skill duration |
| Skill self-rating | Requires voluntary feedback | Natural part of worker's report |
| Skill usage pattern | All-or-nothing | Worker reports which sections of skill it used |

### 7.4 Worker Agent Already Has Capabilities

The existing worker agent already has:
- `skill_injection: true`
- `dynamic-skill` innate skill (knows about skill_search, skill_feedback, etc.)
- `opencode` innate skill (for delegated code execution)
- Full tool set (bash, filesystem, etc.)

### 7.5 What's Missing (To Be Designed)

1. **Skill-aware spawning** — Mechanism to pass a specific skill to a worker at spawn time
2. **Metric reporting** — Worker reports metrics as part of its completion report (not voluntary)
3. **Tool call counting** — Track which tools were called during skill execution (new metric)
4. **Structured feedback** — Worker self-reports "did skill X help?" as part of natural workflow

### 7.6 Open Design Questions

These require user input before implementation:

**Q1: Worker spawn granularity**
- (A) Per-task: One worker per task, loaded with most relevant skill
- (B) Per-phase: Multiple workers per task, each with one skill
- (C) Dynamic: Worker decides which skill to use based on context

**Q2: How does the skill get to the worker?**
- (A) System parameter: `spawn_worker(skills=["unit-test"])`
- (B) Worker auto-loads from skill set
- (C) Skill injected as system message to worker

**Q3: Does this change auto_load for the tester?**
- (A) Keep all 4 auto_load — tester uses them for PLANNING
- (B) Reduce to just test-strategy — for deciding what to test
- (C) Remove all auto_load — tester is pure dispatcher

**Q4: Adapt existing worker or create new agent?**
- (A) Enhance existing worker — add metric reporting, tool call tracking
- (B) New dedicated "skill-runner" agent — purpose-built
- (C) Both — worker for general work, skill-runner for skill-specific

**Q5: What happens to existing injection pipeline?**
- (A) Keep as-is — injection is separate from worker spawning
- (B) Replace — all skill usage goes through workers
- (C) Hybrid — injection for discovery, worker for execution

---

## 8. A/B Testing: Additional Issues

### 8.1 Comparison Counter vs Per-Variant Records Mismatch

The A/B `comparisons` counter counts injection events, not per-variant usage records. Since `_select_ab_variant` picks ONE variant per injection via hash, each variant only accumulates ~half the records of the comparison counter. So when `comparisons >= 10` (the gate), each variant may only have ~5 usage records — below statistical significance.

### 8.2 No Statistical Significance

The 15% difference threshold with n=10 (effectively n≈5 per variant) is not statistically significant. The system treats noise as signal. A simple Fisher's exact test or chi-square would be more appropriate.

### 8.3 Historical Data Contamination

`_completion_rate_for(skill_id)` computes completion rate from ALL usage records for a skill, including records accumulated before the A/B test started. Pre-test records pollute the comparison.
