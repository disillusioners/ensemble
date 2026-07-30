---
version: 1.1.0
category: testing-strategy
auto_load: true
---

# Test Strategy

Decide WHAT to test and HOW to scope it. The default is the smallest scope that covers the change.

**I am the Test Leader + Dispatcher.** Planning answers WHAT to test. Dispatching answers WHO runs each piece — I never execute tests myself. Each worker instance receives exactly ONE skill via the `load_skill` parameter (e.g. `send_message(..., load_skill="<skill_name>")`) so attribution stays clean and per-skill guidance is loaded for the actual execution. My own `test-strategy` skill is for my planning only; never embed it in a worker dispatch.

## Blast Radius Control (Run First, Always)

Before listing packs, derive the change set. **Even on an explicit "full test suite" request, assess real scope first** — never blindly run everything.

**Derive the change set from any available signal (no explicit phase context required):**

1. Request wording / user message
2. `.agents/shared/planning/`, conventions, recent commits
3. Spawn worker (without `load_skill`) to inspect `git diff` / changed files / affected modules (you cannot run git directly)
4. PACKS.md pack-to-module mapping (match file paths to pack names via naming convention)

**Decision matrix:**

| Change shape | Action |
|---|---|
| Small / isolated (few files, single module, no architecture impact) | **Reduce scope** to relevant packs only — even if "full" was requested. Report the reduction. |
| Big / critical (cross-module, architecture refactor, release gate) | Full suite is justified → proceed to Split & Parallel. |
| Ambiguous / unknown | Default to scoped run of directly-affected packs; offer to expand. Don't default to "run everything". |
| User insists on full after being told change is small | Honor it, but surface the cost first. |

**Default:** the smallest scope that covers the change. When in doubt, scope down and offer to expand.

**Report template (when reducing):**
> "Full requested; change touches [X files / N modules] → running [packs], skipping [packs]. Full suite [warranted / not warranted]. Reason: [why]."

## Planning Checklist

1. **Identify all work** — list test packs to run; note dependencies; identify ensure.md validations needed
2. **Assess parallelism** — independent? → parallel; dependent? → sequential; parallelizable? → 2+ independent groups
3. **Determine execution strategy:**

   | Scenario | Strategy |
   |---|---|
   | 1 independent pack | 1 session |
   | 2-3 small packs (same module) | 1 session (grouped) |
   | 3+ independent packs (different modules) | Multiple sessions in parallel |
   | Mixed dependencies | Parallel + sequential |

4. **Group packs into sessions** — by module / test type / execution environment; keep unrelated packs separate; consider quick-fix context (reuse same module)
5. **Set execution order** — order dependent packs; launch independent groups simultaneously; note which validations run after tests pass
6. **Materialize the plan as a todo graph** — `todo_graph_create(nodes=<packs>, edges=<dependencies>)`, one node per pack. Prefer `todo_graph_*` over `todo_list_*` (DAG expresses fan-out/fan-in). Independent packs → sibling nodes (no edge); dependent packs → edge from prerequisite to dependent. Add a final aggregation/ensure.md node with edges from every pack. Keep current with `todo_graph_update(node_id, status)` (`in_progress` → `done`).

## Planning Rules

- **Never skip planning** — analyze before spawning
- **Parallel when safe** — independent packs benefit from parallelism
- **Group related packs** — same module = same session (better context)
- **When in doubt, split** — separate sessions are safer than mis-grouped ones
- **Plan for aggregations** — know how you'll combine results

## Worker Skill Selection (Dispatcher Contract)

Planning determines WHAT to test. Dispatching determines WHICH skill each worker receives. **The tester never executes tests directly** — every worker instance is spawned with exactly ONE skill embedded in the message via the `load_skill` parameter of `send_message(...)`. This keeps attribution 1:1 (one skill, one worker, one responsibility).

### Skill Selection by Task Type

| Task | Worker skill (`load_skill`) | Why this skill |
|------|------------------------------|----------------|
| Run a single test pack (unit/integration/e2e/mock) | `test-pack-execution` | Pack lifecycle, Pre-Send Self-Check, strict single-pack template, TTQA, dual-layer timeout |
| Mock test design + implementation + run | `mock-test` | Spec template, port allocation (>10000), what-to-mock, 5-phase workflow |
| Cross-component / API boundary / DB integration validation | `integration-test` | Cross-component testing, API boundaries, integration concerns |
| End-to-end / browser / full flow validation | `e2e-test` | Full-flow validation; may dispatch agent-browser skill for UI |
| ensure.md requirement validation (per pack) | `ensure-validation` | Quality-gate parsing, pack-mapping, contradiction detection |
| Test-code quick fix / repair (<20 lines) | `quick-fix` | Eligibility assessment, commit-required execution |
| Flaky test detection / quarantine lifecycle | `flaky-test-management` | 3× retry budget, QUARANTINE.md, un-quarantine |
| Unit test discovery / coverage analysis (read-only investigation) | `unit-test` | Discovery + coverage documentation (no execution) |

### Dispatch Rules

- **Exactly one skill per worker** — never bundle multiple skills into one dispatch. One skill = one responsibility = one clear attribution in `RESULTS/`.
- **Never send `test-strategy` to workers** — `test-strategy` is the tester's own auto-loaded planning skill. Workers receive execution skills only.
- **Skill must match task type** — running a pack requires `test-pack-execution`, not `unit-test`. If a worker would need multiple skills, split the work into multiple workers (one skill each).
- **The "session" in the execution strategy table is a WORKER instance**, not the tester. The tester spawns + sends_message; the worker runs the pack.

### Dispatch Pattern

When spawning a worker for a planned task:

```
<my planning context, constraints, expected output>

<tasks and edges from todo_graph_create>

<full message body — see test-pack-execution or relevant skill template>

→ spawn_instance(agent="worker")
→ send_message(
    instance_id=worker_id,
    message="<full message body — see test-pack-execution or relevant skill template>",
    load_skill="<selected skill from table above>"
  )
```

The `load_skill` parameter is parsed by the worker runtime so the worker loads only the skill needed for its task. The tester's own skill stack is untouched.

### Pre-Dispatch Self-Check (dispatcher-level)

Before every `send_message` to a worker, in addition to the skill's own Pre-Send Self-Check:

- [ ] **Worker skill selected** from the table above (matches task type)
- [ ] **Exactly one** `load_skill="..."` parameter on the `send_message(...)` call
- [ ] **`test-strategy` NOT embedded** in the worker message (tester-only planning skill)
- [ ] **Skill ↔ task match verified** (e.g., running a pack → `test-pack-execution`; not `unit-test` or `e2e-test`)
- [ ] **todo_graph node updated** to `in_progress` before the dispatch lands

## Phase Context (When Provided)

If the leader provides phase context (changed files/modules):

- Use it as the primary signal to derive the change set
- Match changed file paths to pack names via naming convention (e.g., `src/auth/` → `auth_unit_test.sh`)
- Run only the affected packs; report skipped packs:
  > "Running: [packs]. Skipped: [packs]. Reason: [no changed files in X modules]."

Scope is always driven by the actual change set — never auto-expand to all packs based on a pack-count ratio. Broad cross-module change → full suite is warranted; otherwise stay scoped.