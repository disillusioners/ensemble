# Skill-Per-Worker Architecture — Next Milestone

> Status: DESIGN APPROVED — Ready for implementation planning
> Date: 2026-07-15
> Type: **Major milestone** for skill evolution system, not bug fix
> Decisions: Q1=A (tester keeps test-strategy only), Q2=A (worker skill-free, receives via send_message)

---

## 1. Vision

Transform the skill execution model from **"many skills in one instance"** to **"one skill per worker instance"**. This achieves:

- **Clean per-skill attribution** — each skill has its own isolated execution context
- **Reliable metrics** — skill_feedback enforced, tool calls attributable
- **Parallel skill execution** — multiple skills can run in parallel as separate workers
- **Foundation for advanced evolution** — A/B testing, composite scoring, and new metrics all become meaningful

This is the **second major milestone** of the skill evolution system after the Tester Skill Evolution POC.

---

## 2. Design Principles

1. **One worker = one skill = one metric record** — clean 1:1 attribution
2. **Tester is planner + dispatcher** — decides which skill is needed, spawns worker with skill
3. **Worker is skill-agnostic by default** — receives skill dynamically via `send_message`
4. **Metrics scope = skill load to skill_feedback** — the "skill run scope"
5. **LLM is smart today** — skip edge cases like "what if skill_feedback not called" for simplicity

---

## 3. Architecture

### 3.1 Role Distribution

| Agent | Role | Skills | Auto_Load |
|-------|------|--------|-----------|
| **Tester** | Planner + dispatcher | `test-strategy` only | ✅ Yes (1 skill for blast radius + planning) |
| **Worker** | Skill executor | None by default — receives via `send_message(skill=...)` | ❌ No auto_load |

### 3.2 Skill Distribution

| Skill | Lives On | How Loaded |
|-------|----------|------------|
| test-strategy | Tester (auto_load) | Always in tester's prompt for planning |
| unit-test | Worker (dynamic) | `send_message("task...", skill="unit-test")` |
| mock-test | Worker (dynamic) | `send_message("task...", skill="mock-test")` |
| test-pack-execution | Worker (dynamic) | `send_message("task...", skill="test-pack-execution")` |
| integration-test | Worker (dynamic) | `send_message("task...", skill="integration-test")` |
| e2e-test | Worker (dynamic) | `send_message("task...", skill="e2e-test")` |
| ensure-validation | Worker (dynamic) | `send_message("task...", skill="ensure-validation")` |
| flaky-test-management | Worker (dynamic) | `send_message("task...", skill="flaky-test-management")` |
| quick-fix | Worker (dynamic) | `send_message("task...", skill="quick-fix")` |

### 3.3 Data Flow

```
1. User sends testing task to Tester
2. Tester loads test-strategy (auto_load) → plans blast radius, decides what to test
3. Tester spawns Worker with send_message(skill="unit-test", message="run unit tests on auth module")
4. Worker receives skill → skill system loads "unit-test" skill from project (clone-on-miss if needed)
5. ← METRICS SCOPE START: last_injected_skill_ids = ["unit-test"]
6. Worker executes task using the skill
7. Worker reports back: skill_feedback(skill_id, applied=True, note="...") + task result
8. ← METRICS SCOPE END: usage record created with clean 1:1 attribution
9. Tester receives report, aggregates, continues planning
```

### 3.4 Sequence Diagram

```
Tester                          Worker                    Metrics System
  │                               │                           │
  │── send_message(               │                           │
  │    skill="unit-test",         │                           │
  │    "run unit tests on auth"   │                           │
  │  ) ──────────────────────────>│                           │
  │                               │                           │
  │                               │── load skill "unit-test"  │
  │                               │   (clone-on-miss if needed)│
  │                               │                           │
  │                               │── METRICS START ─────────>│
  │                               │   last_injected_skill_ids │
  │                               │   = ["unit-test"]         │
  │                               │                           │
  │                               │── execute task ───────────│
  │                               │   (iterations tracked)    │
  │                               │   (duration tracked)      │
  │                               │   (tool calls tracked)    │
  │                               │                           │
  │                               │── skill_feedback(          │
  │                               │    skill_id,               │
  │                               │    applied=True,           │
  │                               │    note="..."              │
  │                               │  ) ──────────────────────>│
  │                               │                           │
  │                               │   METRICS END              │
  │                               │   usage record created    │
  │                               │   clean 1:1 attribution   │
  │                               │                           │
  │<── report: "12 pass, 2 fail" ─│                           │
  │                               │                           │
```

---

## 4. What This Unlocks

### 4.1 Metrics That Become Reliable

| Metric | Before (Multi-Skill) | After (Skill-Per-Worker) |
|--------|---------------------|--------------------------|
| `task_succeeded` | Shared across all injected skills | Per-skill (1 worker = 1 skill) |
| `applied` (feedback) | 0% — agents never call `skill_feedback` | ~90%+ — worker has dense reinforcement |
| `iterations` | Counts agent messages across all skills | Worker's iterations = skill's iterations |
| `duration_seconds` | Shared across all skills | Worker duration = skill duration |
| `feedback_note` | Rarely provided | Part of worker's natural workflow |

### 4.2 Problems That Self-Heal

| Problem | Why It's Fixed |
|---------|----------------|
| Multi-skill attribution | Only 1 skill per worker → 1:1 attribution |
| `applied` always 0 | Worker already has dense `skill_feedback` reinforcement |
| Auto_load invisible to metrics | Skills aren't auto_load on worker — loaded via send_message and tracked |
| Co-injection dilution | No co-injection — one skill per worker |
| A/B testing pollution | Clean variant per worker instance |
| CAPTURED flow bias | Workers actually call feedback → `has_applied_for_instance` works correctly |

### 4.3 New Capabilities

| Capability | How |
|------------|-----|
| Parallel skill execution | Spawn 5 workers, each with a different skill, run in parallel |
| Per-skill A/B testing | Worker A gets variant v1, Worker B gets variant v2 — clean comparison |
| Tool call metrics per skill | Count tools called during worker's execution |
| Skill usage patterns | Worker reports which sections of the skill it used |
| Confidence intervals | With 1:1 attribution, statistical significance becomes meaningful |

---

## 5. Component Changes

### 5.1 Tester Agent

#### auto_load: Reduce from 4 → 1

Update `agents/tester/skill-set.md`:

```yaml
# BEFORE:
skills:
  - name: test-strategy
    auto_load: true
  - name: test-pack-execution
    auto_load: true
  - name: mock-test
    auto_load: true
  - name: unit-test
    auto_load: true

# AFTER:
skills:
  - name: test-strategy
    auto_load: true          # KEEP — tester uses for planning
  - name: test-pack-execution
    auto_load: false         # MOVE — worker loads dynamically
  - name: mock-test
    auto_load: false         # MOVE — worker loads dynamically
  - name: unit-test
    auto_load: false         # MOVE — worker loads dynamically
```

#### workflow.md: Update delegation patterns

```
Instead of: "Load unit-test skill and execute"
Now: "Spawn worker with unit-test skill and delegate execution"

Instead of: "Run mock tests"
Now: "Spawn worker with mock-test skill, delegate mock test execution"
```

### 5.2 Worker Agent

No meta.json change — worker already has:
- `dynamic-skill` innate skill
- `skill_injection: true`
- Dense `skill_feedback` enforcement in rule.md, workflow.md

### 5.3 send_message Enhancement

#### New Optional Parameter

`send_message` gets an optional `skill` parameter:

```python
send_message(
    instance_id="worker-xxx",
    message="run unit tests on auth module",
    skill="unit-test"  # NEW: optional skill name
)
```

When `skill` is provided:
1. System resolves skill name to skill ID (from project skills, clone-on-miss from bank)
2. Skill is injected into worker's context via existing injection pipeline
3. `last_injected_skill_ids` is set to `[skill_id]` — establishing metrics scope

When `skill` is NOT provided:
- Worker runs normally without any skill (existing behavior, no change)

### 5.4 Clone-on-Miss (No Change)

Existing mechanism works as-is:
- Worker receives `skill="unit-test"` → system checks project skills
- If not found → clones from skill bank (existing mechanism)
- Worker gets cloned project skill

### 5.5 Evolution Pipeline (No Change)

Skill-keeper agent and evolution pipeline work as-is:
- Usage records from workers feed into existing metrics pipeline
- Triggers evaluate metrics and evolve skills
- A/B testing serves variants to different workers
- Clean attribution makes A/B results meaningful

---

## 6. Future Metrics Enabled (Not in This Milestone)

| Metric | How to Collect |
|--------|---------------|
| Tool call count | Count tool calls during worker execution (graph-level) |
| Token usage | Count input/output tokens per worker (LLM API integration) |
| Skill section usage | Worker reports which skill sections it used (worker's self-report) |
| Time-to-first-use | How long after skill load before first tool call |
| Iteration efficiency | Iterations per task type per skill |

These become meaningful only AFTER skill-per-worker is implemented, since they require clean per-skill context.

---

## 7. Implementation Phases (Preview)

| Phase | Deliverable | Effort | Dependencies |
|-------|-------------|--------|--------------|
| 1 | `send_message` skill parameter | Medium | None |
| 2 | Update tester skill-set.md (3 → on-demand) | Trivial | None |
| 3 | Update tester workflow.md delegation patterns | Low | None |
| 4 | Verify metrics flow for worker-per-skill | Medium | Phase 1 |
| 5 | Validate A/B testing produces clean results | Medium | Phases 1-4 |
| 6 | Add tool call counting (future metrics) | Medium | Phase 4 |

---

## 8. Relationship to Other Documents

| Document | Relationship |
|----------|-------------|
| `skill-metrics-improvement.md` | Provides multi-metric scoring, test-period isolation. Becomes **meaningful** with this architecture. |
| `tester-skill-evolution` (merged) | First milestone — introduced auto_load, clone-on-miss, skill bank. This builds on top. |
| `agents/tester/skill-set.md` | Updated to move 3 skills from auto_load to on-demand. |
| Worker agent prompt | No change needed — already instrumented for metrics. |

---

## 9. Success Criteria

After implementation:

1. ✅ `send_message(skill="X")` reliably delivers skill X to worker
2. ✅ Worker's `last_injected_skill_ids` contains exactly 1 skill ID after send_message
3. ✅ Worker calls `skill_feedback` after task completion (reinforced in prompt)
4. ✅ `skill_usage_records` table shows 1 row per worker task with clean skill attribution
5. ✅ `applied` column is non-zero for worker-attributed tasks
6. ✅ A/B test results distinguish variants cleanly
7. ✅ Composite metrics from skill-metrics-improvement.md can be computed reliably
