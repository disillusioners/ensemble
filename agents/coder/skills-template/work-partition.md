---
version: 1.0.0
category: planning
auto_load: true
---

# Work Partition

Decide what **I keep** vs what **I offload to a `worker`**. The default is: I keep it. Offloading is an exception for clean bulk only.

**I am the Coder (working-lead).** I implement directly by hand. I offload a partition to a `worker` leaf only when, mid-work, I discover a chunk that is bulk + low-coupling + no-judgment + disjoint-files. I never spawn `coder` (recursion guard: worker-only, workers never spawn). I never re-dispatch a failed partition — I take it back by hand. Planning is an internal hint, never an emitted artifact.

---

## The Offload Gate (run before every spawn)

Before calling `spawn_instance`, verify ALL four conditions. If any fails → do the work myself.

| Condition | Holds when | Fails when |
|-----------|-----------|------------|
| **Bulk** | 5+ files, same kind of edit (rename, pattern swap, import add, boilerplate) | 1–4 files, or a single complex change |
| **Low-coupling** | Each file editable independently; no shared signature, no cross-file data flow needing judgment | Edits share a signature, or one file's change forces re-thinking another's |
| **No-judgment** | The exact edit is fully determined — a worker can apply it from a precise instruction | Requires per-file architectural decisions, taste, or trade-offs |
| **Disjoint** | The partition's file set does not overlap with my own edits or another worker's | Same file edited by me and a worker, or by two workers |

**Default: keep.** When in doubt, do it myself. Offloading is an optimization for clean bulk, never a way to dodge the hard parts.

---

## What I Always Keep (Bucket A — never offload)

- Architectural / central / coupled edits
- Signature changes touching shared interfaces
- Per-file judgment work (naming decisions, error-handling trade-offs)
- Any file in the shared/core area other partitions touch
- The integration step (running tests on the whole tree after aggregation)

## What I Offload (Bucket B — clean bulk)

Examples where offload makes sense:
- `grep` finds 60 files calling `old_name()` → rename to `new_name()` across all (code-refactor)
- 30 test files need the same import line added (code-implementation)
- 40 modules need a deprecated decorator stripped and replaced (code-refactor)
- A bulk fix: 20 files have the same off-by-one pattern (code-fix)

If the bulk chunk is actually a *feature* needing design per file → it is NOT clean bulk; do it myself or hand back to the dispatcher.

---

## Partition Rules

1. **Disjoint file sets** — each worker owns files no other worker (and not I, in the same cycle) edits. Parallel edits on overlapping files conflict.
2. **Max 2–3 concurrent workers** per fan-out cycle. Beyond that, partition iteratively across cycles.
3. **One skill per worker** — pick from the table below based on the partition's shape. Omit `load_skill` when none fits (detailed instructions in the message instead).

### Skill-per-partition selection

| Partition shape | `load_skill` |
|---|---|
| Bulk rename / structural simplify / dedup | `code-refactor` |
| Same fix applied across many files | `code-fix` |
| Same feature/addition across many files | `code-implementation` |
| The commit step after all edits land | `git-commit` |
| No skill matches the shape | (omit — detailed instruction in message) |

Exactly ONE skill per worker. Never bundle multiple skills into one dispatch.

---

## Dispatch Mechanics

1. **Before dispatching 2+ workers**, create a `todo_graph` to track fan-in:
   ```python
   todo_graph_create(
       nodes=[
           {"id": "coder-worker-rename", "text": "Rename old_name→new_name across module A"},
           {"id": "coder-worker-imports", "text": "Add import line across module B tests"},
       ],
   )
   ```

2. **Spawn + dispatch** each partition:
   ```python
   worker_id = spawn_instance(agent="worker")
   send_message(
       instance_id=worker_id,
       message=(
           "Apply this exact change across these disjoint files: <file list>. "
           "Edit per file: <precise, no-judgment instruction>. "
           "Constraints: match existing style; minimal diffs; do not touch other files. "
           "Report: files changed, lines per file, any deviation. "
           "Before ending any turn: begin work with a tool call, deliver your "
           "report, or ask — a turn that ends on future-intent text with zero "
           "tool calls is treated as a junk report. I adjudicate your report "
           "on evidence: zero tool-call evidence and no concrete artifact is "
           "treated as interim, not completion, and I will verify before "
           "acting on it. "
           "Call skill_feedback(skill_id, applied=True, usefulness=<1-10>, "
           "note=<short>, improvement_note=<actionable>) as a TOOL CALL ONLY "
           "first, then deliver your full report as your FINAL message and "
           "end your turn."
       ),
       load_skill="code-refactor",  # or code-fix / code-implementation / git-commit; omit if none fits
   )
   ```

3. **END MY TURN** after every `send_message`. The runtime resumes me when the worker reports back. Never poll `get_instance_info`/`list_instances`, never `sleep`/`bash`-wait — holding the turn blocks report delivery and deadlocks.

---

## Aggregate + Verify

1. As each worker report arrives → `todo_graph_update(node_id, status="done")`.
2. `git diff`-check each worker's output before trusting it.
3. Aggregate only when `todo_view()` shows ALL nodes done.
4. Run tests on the **whole tree** — the integration is mine to verify, not the workers'.
5. Run linters/formatters if the project uses them.

---

## Failure Policy — One Shot, Then I Do It By Hand

If a worker reports failure, partial output, or bad/stray edits:
- **Do NOT re-dispatch** to a fresh worker (avoids thrash and token spend).
- **Take that partition back and do it by hand** immediately.
- If a worker edited outside its disjoint set, revert just those stray edits and redrive that part myself.
- Note the takeover in the final report (which partition, why).

---

## Pre-Dispatch Self-Check

Before every `send_message`:

- [ ] **Offload gate passed** — bulk + low-coupling + no-judgment + disjoint (all four)
- [ ] **Disjoint files** — no overlap with my edits or another worker's partition
- [ ] **Skill selected** — exactly one `load_skill` per worker (or omitted with detailed instructions)
- [ ] **Precise instruction** — the edit is fully determined; no judgment required from the worker
- [ ] **`todo_graph` created** — fan-in tracking ready (2+ workers)
- [ ] **Will END TURN** after send_message — no polling, no holding the turn

---

## Mandatory Output Format (final report excerpt)

Surface the offload map briefly in the final report — no separate plan artifact:

```
## Coder Report: [Task]

### Offload Map (if any workers were spawned)
| Worker | Skill | Target | Outcome |
|--------|-------|--------|---------|
| coder-worker-rename | code-refactor | module A (12 files) | done |
| coder-worker-imports | code-implementation | module B tests (30 files) | failed → taken back by hand |

### What I Did By Hand
- <core/coupled edits>

### Verification
- Whole-tree tests: N passed / M failed
- Linters: <result>

### Notes
- <takeovers, follow-ups, risks>
```
