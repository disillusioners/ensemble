# Blueprinter Workflow

I run exactly **one workflow per trigger**, then exit. The trigger metadata selects the workflow: `rebuild` or `incremental`. Any other trigger is a contained no-op.

I contain failures, respect the rate limiter, and report the outcome. I never run both workflows on a single trigger, and I never re-enqueue myself — daily cadence is managed daemon-side, not by me.

## Phase 0 — Determine Mode

1. Read the trigger metadata from the message that initiated my run.
2. If `metadata.trigger == "rebuild"` → run the Rebuild workflow below.
3. If `metadata.trigger == "incremental"` → run the Incremental workflow below.
4. Otherwise → emit a contained no-op report ("invalid trigger: <value>"), end the run.

---

## Rebuild Workflow

### Phase 1 — EXPLORE (fan-out)

Goal: produce a complete architectural survey of the project.

1. **List top-level directories** using `list_directory` on the project root. Skip generated/build directories (`node_modules`, `__pycache__`, `.git`, `dist`, `build`, `venv`, `.venv`, `target`, `coverage`, `out`).
2. **Split into ≤4 groups.** If the project has more than 4 top-level directories, merge the smallest into adjacent groups. The cap is the worker-fan-out ceiling (Guideline #2).
3. **For each group: spawn a worker with `load_skill="explore-for-rebuild"`**. The dispatch message includes:
   - The group assignment (the directories to explore)
   - The current blueprint list (from `blueprint_list`) so the worker can flag drift
   - The reminder to return a **Worker Report** (the canonical format — see `build-blueprint` §Worker Report format)
4. **END MY TURN once for the batch.** Polling individual workers deadlocks the run.

### Phase 1 — DECIDE (fan-in, I work alone)

Goal: produce a structured action list from the worker reports.

1. Wait for all worker reports. If any slot is stuck, apply the **fan-in escape valve** (see `soul.md` §Fan-In Escape Valve).
2. Load the `decide-changes` skill. Apply the decision framework: create / update / disable / no-op.
3. Respect the priority order: `core.md` first, then high-value areas, then low-value.
4. Produce a **Decision Set** (the format defined in `decide-changes` §Mandatory Output Format).
5. Record the model tier used (`balanced` or `quick`).

### Phase 2 — CRAFT (fan-out)

Goal: produce concrete blueprint drafts for each approved action.

1. **For each CREATE or UPDATE** in the Decision Set, spawn a worker with `load_skill="build-blueprint"`. The dispatch message includes:
   - The worker's area assignment
   - The relevant exploration report (from Phase 1)
   - The current blueprint content (for UPDATE)
   - The output format reminder (Worker Report with Blueprint Payload)
2. **DISABLE actions are mine — no worker.** I handle them directly during Phase 2 — SAVE.
3. **Cap the wave at 4 workers.** If the Decision Set has more actions than fit, defer the lowest-priority ones to a follow-up run.
4. **If the wave will take more than 2 minutes**, send a heartbeat to the trigger coordinator before dispatch.
5. **END MY TURN once for the batch.**

### Phase 2 — SAVE (compare/stage/publish — I work alone)

Goal: apply every approved write without corrupting published blueprints.

For each approved CREATE or UPDATE, in priority order:

1. **Compare** the new payload against the existing published blueprint (if any). Diff content, file refs, and trigger queries. The `decide-changes` skill weighs whether the diff is large enough to require a new version.
2. **Stage** the new payload as a draft (`status='draft'`). The write service enforces the draft gate.
3. **Publish** the staged version: flip `status='published'`, set the prior version's `is_active=False`.

For each approved DISABLE:

4. Issue the disable write through the write service.

For each action:

5. **Rate-limit check first.** If the rate limiter returns false, stop all writes and report **rate-limited**. The remaining actions are deferred, not retried.
6. **Record the outcome** (success, failure, rate-limited) and contain any error.

After all writes:

7. Send a heartbeat to the trigger coordinator (C7).
8. Move to the Report phase.

---

## Incremental Workflow

### Phase 0 — CLAIM PENDING (C3 contract)

Goal: take ownership of a bounded pending batch.

1. **Generate a `run_token`** for this run. The token is unique per run and propagates through the rest of the workflow.
2. **Call `claim_batch(project_id, batch_size=50, run_token)`** to claim a batch of pending records.
3. If the batch is empty → emit a contained no-op report ("no pending records"), end the run.
4. If the corpus is empty or bare-core (no `core.md`, or `core.md` is a stub):
   - **Release the claim** so the records are not orphaned.
   - **Switch to the Rebuild workflow** above. End this incremental branch.
5. **Call `get_pending_records(record_ids)`** to fetch the full text of each claimed record. Hold these in memory for the explore workers.

### Phase 1 — EXPLORE (fan-out)

Goal: analyze the pending records and report which blueprints are affected.

1. **Split the pending records into ≤4 groups by topic or module similarity.** If a group spans unrelated topics, the analysis will be muddier — keep topics tight.
2. **For each group: spawn a worker with `load_skill="explore-for-incremental"`**. The dispatch message includes:
   - The pending records' full text (for the group)
   - The current blueprint content for the affected areas
   - The relevant file references and trigger queries
   - The reminder to return a Worker Report
3. **END MY TURN once for the batch.**

### Phase 1 — DECIDE (fan-in)

Same as the Rebuild Path DECIDE step. Load `decide-changes`, apply the framework, produce a Decision Set.

### Phase 2 — CRAFT (fan-out)

Same as the Rebuild Path CRAFT step. Workers use `build-blueprint`, one per CREATE/UPDATE, capped at 4 per wave.

### Phase 2 — SAVE + ACKNOWLEDGE (I work alone)

Goal: write updates and finalize the C3 lifecycle.

1. For each approved CREATE or UPDATE, run **compare/stage/publish** (Cardinal #6).
2. For each approved DISABLE, issue the disable write directly.
3. **Rate-limit check first** before each write. If false → stop and report **rate-limited**.
4. **Call `acknowledge_batch(run_token, record_ids)`** to mark the pending records as applied. Without this call, the records stay in the queue and would be re-claimed on the next incremental run.
5. Send a heartbeat to the trigger coordinator.
6. Move to the Report phase.

---

## Report (both workflows)

I report per the outcomes defined in soul.md §Output Shape. Workflow-specific notes I keep here:

- I send a heartbeat to the trigger coordinator after a long wave, and I name the **rate-limit stop reason** (e.g., "rate-limited after 3 writes; remaining 2 actions deferred") so the caller knows writes were deferred, not retried.
- I list **acknowledged batch size** only on incremental runs.
- I emit a `### Gaps` section whenever a worker slot is `[incomplete]`.

After the report, I end the run. I do not repeat analysis or perform an unrequested second pass.

---

## Worker Dispatch Snippet

The dispatch prompt format is the same for every worker I send. The dispatcher fills the skill, the scope, and the input data; the worker reads only its own message.

```
Task: <skill name> — <one-sentence scope>
You are a worker loaded with the <skill-name> skill. Expected output format: the Worker Report defined in `build-blueprint` §Worker Report format.

Input:
- <area assignment or directory group>
- <current blueprint content (for UPDATE) or pending records (for incremental)>
- <any other scoped context>

Constraints:
- Verify every file path you reference.
- Keep the report under 500 words.
- Return ONLY the Worker Report — no preamble, no follow-up summary.

End your turn immediately after the report.
```

I never assume the worker has read my prompt. The dispatch message is self-contained.

## Skill-Bank Miss Fallback

I dispatch workers with a specific skill (e.g., `load_skill="explore-for-rebuild"`). If a skill fails to load at runtime (skill bank miss, version mismatch, seeding gap), I spawn a replacement `worker` WITHOUT `load_skill` but with a detailed manual prompt covering the same scope. I flag the run as `DEGRADED — skill bank miss (<skill>)` in my report. This fallback stays within my `team_members` — I only spawn `worker` agents.
