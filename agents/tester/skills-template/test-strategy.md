---
version: 1.0.0
category: testing-strategy
auto_load: true
---

# Test Strategy

Decide WHAT to test and HOW to scope it. The default is the smallest scope that covers the change.

## Blast Radius Control (Run First, Always)

Before listing packs, derive the change set. **Even on an explicit "full test suite" request, assess real scope first** — never blindly run everything.

**Derive the change set from any available signal (no explicit phase context required):**

1. Request wording / user message
2. `.agents/shared/planning/`, conventions, recent commits
3. Spawn opencode to inspect `git diff` / changed files / affected modules (you cannot run git directly)
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

## Phase Context (When Provided)

If the leader provides phase context (changed files/modules):

- Use it as the primary signal to derive the change set
- Match changed file paths to pack names via naming convention (e.g., `src/auth/` → `auth_unit_test.sh`)
- Run only the affected packs; report skipped packs:
  > "Running: [packs]. Skipped: [packs]. Reason: [no changed files in X modules]."

Scope is always driven by the actual change set — never auto-expand to all packs based on a pack-count ratio. Broad cross-module change → full suite is warranted; otherwise stay scoped.