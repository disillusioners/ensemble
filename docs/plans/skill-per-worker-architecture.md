# Skill-Per-Worker Architecture

> Status: DRAFT — Design approved by user
> Date: 2026-07-15
> Related: skill-metrics-improvement.md, Tester Skill Evolution System
> Decisions: Q1=A (tester keeps test-strategy only), Q2=A (worker skill-free by default, receives skill via send_message)

---

## 1. Design Principles

1. **One worker = one skill = one metric record** — clean 1:1 attribution
2. **Tester is planner + dispatcher** — decides which skill is needed, spawns worker with skill
3. **Worker is skill-agnostic by default** — receives skill dynamically via `send_message`
4. **Metrics scope = skill load to skill_feedback** — the "skill run scope"
5. **LLM is smart today** — skip edge cases like "what if skill_feedback not called" for simplicity

---

## 2. Architecture

### 2.1 Role Distribution

| Agent | Role | Skills | Auto_Load |
|-------|------|--------|-----------|
| **Tester** | Planner + dispatcher | `test-strategy` only | ✅ Yes (1 skill, for blast radius + planning) |
| **Worker** | Skill executor | None by default — receives via `send_message(skill=...)` | ❌ No auto_load |

### 2.2 Skill Distribution

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

### 2.3 Data Flow

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

### 2.4 Sequence Diagram

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

## 3. Component Changes

### 3.1 Tester Agent Changes

#### meta.json
```json
{
  "innate_skills": ["opencode", "test-pack", "todo", "dynamic-skill"],
  "skill_injection": true
}
```
No change needed here — already configured from tester-skill-evolution.

#### auto_load: Reduce to 1 skill
Tester's auto_load skills change from 4 → 1:
- KEEP: `test-strategy` (for planning decisions)
- REMOVE from auto_load: `test-pack-execution`, `mock-test`, `unit-test` (these move to workers)

Update `agents/tester/skill-set.md`: change `auto_load: false` for the 3 removed skills.

### 3.2 Worker Agent Changes

#### meta.json (CURRENT)
```json
{
  "innate_skills": ["dynamic-skill", "todo"],
  "skill_injection": true
}
```
Already has `dynamic-skill` and `skill_injection: true`. No change needed.

#### Prompt Reinforcement (ALREADY EXISTS)
Worker already has strong `skill_feedback` enforcement:
- `rule.md:107`: "After applying an injected or searched skill, I **always** call `skill_feedback`"
- `rule.md:186`: "❌ Never Skip `skill_feedback`"
- `workflow.md:281`: Anti-pattern showing wrong way
- Report template includes `Skill Feedback:` line

### 3.3 send_message Enhancement

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
1. System resolves the skill name to a skill ID (from project skills, clone-on-miss from bank)
2. Skill is injected into the worker's context via the existing injection pipeline
3. `last_injected_skill_ids` is set to `[skill_id]` — establishing the metrics scope

When `skill` is NOT provided (or None):
- Worker runs normally without any skill (existing behavior, no change)

### 3.4 Metrics System — How It Works Now

The existing metrics pipeline works WITHOUT modification for the worker-per-skill pattern:

1. **Skill load** → `last_injected_skill_ids = [skill_id]` (existing mechanism)
2. **Task executes** → iterations, duration tracked (existing mechanism)
3. **skill_feedback called** → `feedback_applied=True`, `feedback_note` recorded (existing mechanism)
4. **Task completes** → `record_task_completion()` creates usage record (existing mechanism)

The KEY DIFFERENCE: Only ONE skill in `last_injected_skill_ids` → clean attribution.

### 3.5 What Fixes Itself

| Problem | Why It's Fixed |
|---------|----------------|
| Multi-skill attribution | Only 1 skill per worker → 1:1 attribution |
| `applied` always 0 | Worker already has dense `skill_feedback` reinforcement |
| Auto_load invisible to metrics | Skills aren't auto_load on worker — they're loaded via send_message and tracked |
| Co-injection dilution | No co-injection — one skill per worker |
| A/B testing pollution | Clean variant per worker instance |

---

## 4. Changes to Existing System

### 4.1 skill-set.md Update (Tester)

Change 3 skills from auto_load to on-demand:

```yaml
# BEFORE (current):
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

### 4.2 Tester Workflow Update

Tester's workflow.md needs updated delegation patterns:

```
Instead of: "Load unit-test skill and execute"
Now: "Spawn worker with unit-test skill and delegate execution"

Instead of: "Run mock tests"
Now: "Spawn worker with mock-test skill, delegate mock test execution"
```

The tester's test-strategy skill (auto_load) helps decide WHICH skill the worker needs.

### 4.3 No Change to Clone-on-Miss

The clone-on-miss mechanism works as-is:
- When worker receives `skill="unit-test"`, the system checks project skills
- If not found → clones from skill bank (existing mechanism)
- Worker gets the cloned project skill

### 4.4 No Change to Evolution System

The skill-keeper agent and evolution pipeline work as-is:
- Usage records from workers feed into the existing metrics pipeline
- Triggers evaluate metrics and evolve skills
- A/B testing serves variants to different workers
- Clean attribution makes A/B results meaningful

---

## 5. New Metrics Available

With worker-per-skill, these metrics become accurate for the first time:

| Metric | Source | Accuracy |
|--------|--------|----------|
| `task_succeeded` | Job finalization | ✅ Per-skill (1 worker = 1 skill) |
| `iterations` | Agent message count | ✅ Per-skill (worker's iterations = skill's iterations) |
| `duration_seconds` | Job timing | ✅ Per-skill (worker's duration = skill's duration) |
| `applied` (via feedback) | Worker's `skill_feedback` call | ✅ Reliable (worker has dense reinforcement) |
| `feedback_note` | Worker's `skill_feedback` note | ✅ Qualitative signal per skill |

### Future Metrics (Not in Scope Yet)

| Metric | How to Collect |
|--------|---------------|
| Tool call count | Count tool calls during worker execution |
| Token usage | Count input/output tokens per worker |
| Skill section usage | Worker reports which skill sections it used |

---

## 6. Implementation Phases (Preview)

| Phase | Deliverable | Effort |
|-------|-------------|--------|
| 1 | `send_message` skill parameter | Medium — new optional param, skill resolution, injection |
| 2 | Update tester skill-set.md (3 skills → on-demand) | Trivial |
| 3 | Update tester workflow.md delegation patterns | Low — prompt changes |
| 4 | Verify metrics flow works for worker-per-skill | Medium — integration testing |
| 5 | Validate A/B testing produces clean results | Medium — end-to-end test |

---

## 7. Open Items (Deferred)

1. **Tool call counting** — New metric, needs graph-level tracking
2. **Token usage tracking** — New metric, needs LLM API integration
3. **Multi-skill tasks** — What if a task genuinely needs 2 skills? (For now: spawn 2 workers)
4. **Worker pool** — Should workers be reused or spawned fresh each time?
5. **A/B composite scoring** — Still needed (see skill-metrics-improvement.md) but now with clean data
