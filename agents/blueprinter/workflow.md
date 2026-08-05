# Blueprinter Workflow

I run exactly **one workflow per trigger**, then exit. The trigger metadata selects the workflow: `rebuild` or `incremental`. Any other trigger is a contained no-op.

I contain failures, respect the rate limiter, and report the outcome. I never run both workflows on a single trigger, and I never re-enqueue myself — daily cadence is managed daemon-side, not by me.

## Phase 0 — Determine Mode

1. Read the trigger metadata from the message that initiated my run.
2. If `metadata.trigger == "rebuild"` → run the Rebuild workflow below.
3. If `metadata.trigger == "incremental"` → run the Incremental workflow below.
4. If `metadata.trigger == "single"` → run the Single Blueprint Workflow below.
   - Read `metadata.blueprint_id`. If missing → no-op report "trigger single requires blueprint_id".
   - Call `blueprint_get(blueprint_id)`. If the blueprint is missing, inactive, or doesn't belong to this project → no-op report "blueprint not found: <id>". Do NOT fall back to a full rebuild — that silently expands scope.
5. Otherwise → emit a contained no-op report ("invalid trigger: <value>"), end the run.

---

## Rebuild Workflow

### Phase 0a — Clear stale pending queue

A full rebuild scans the project from scratch — any accumulated pending records are subsumed. Clear them so they don't linger as stale data.

1. **Call `blueprint_get_pending_count(project_id)`**.
2. If count > 0:
   - **Call `blueprint_claim_pending(batch_size=10000)`** to claim all pending records.
   - **Call `blueprint_acknowledge_pending(run_token)`** immediately (using the run_token from the claim response).
   - These records are subsumed by the full rebuild scan — no need to process them.
3. If count == 0, skip this step.

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
4. **Doc-maintainer mixed batch (when enabled).** When the project has `doc_maintenance_enabled=true` AND drift candidates exist:
   - Count blueprint actions (N).
   - If N < 4, allocate `M = 4 - N` doc-maintainer slots. Each slot gets `load_skill="maintain-docs"` and dispatches against one Decision-Set area touching `docs/` or docstring-bearing source.
   - If N >= 4, defer doc maintenance this run; note "doc maintenance deferred — all slots used by blueprint craft" in the report.
   - If `doc_maintenance_enabled=false`, skip doc-maintenance dispatch entirely.
5. **Spawn the MIXED batch (N + M workers, ≤4 total).** END MY TURN once for the batch.

### Phase 2 — Fan-in (route by skill type)

After the mixed batch reports land:

1. **Route each worker report by skill assignment** (recorded at dispatch):
   - `build-blueprint` reports → existing Worker Report parser (unchanged)
   - `maintain-docs` reports → Doc Maintenance Report parser (extract `### Files Updated`)
2. **Blueprint updates proceed to SAVE** (unchanged logic).
3. **Doc results are aggregated into a final `### Doc Maintenance` section** of the report. Contain any doc errors — never block SAVE or the Report.

### Phase 2a — Build Gate + Commit (atomic, best-effort)

After fan-in, before SAVE:

1. **Collect doc changes** from all `### Files Updated` sections across `maintain-docs` reports. Deduplicate paths.
2. **If no doc changes** → skip this phase entirely.
3. **If `doc_maintenance_commit_enabled=false`** → skip the commit step. Doc changes remain in the working tree for manual review. Continue to SAVE.
4. **If `doc_maintenance_commit_enabled=true`** → call `commit_docs_validated(changed_paths, message)`:
   - `message` format: `docs(blueprinter): auto-update <mode> <area> [skip ci]`
   - The tool runs the atomic build-validation + git-commit sequence server-side.
   - **Build FAIL or TIMEOUT** → hard stop; changes remain in the working tree. Record the outcome in the report under `### Doc Commit`. Cardinal #1 extends to this step — never blocks SAVE.
5. **Continue to Phase 2 — SAVE** regardless of commit outcome (best-effort).

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

7. Move to the Report phase.

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

Goal: analyze the pending records, check blueprint coverage, and report which blueprints need updates or creation.

1. **For each pending record group, check coverage first.** Before spawning workers, use `blueprint_search` with keywords from the pending record to determine whether any existing blueprint covers this topic.
   - If an existing blueprint matches → the worker should assess it for UPDATE (existing behavior).
   - If NO existing blueprint matches a significant architectural area → the worker should explore that area for a potential CREATE.
2. **Split the pending records into ≤4 groups by topic or module similarity.** Group records that describe the same architectural area together. A group may contain both "update existing" and "explore new" records.
3. **For each group: spawn a worker with `load_skill="explore-for-incremental"`**. The dispatch message includes:
   - The pending records' full text (for the group)
   - The current blueprint content for matching areas (if any)
   - Whether this group is an UPDATE assessment or a NEW-AREA exploration (or both)
   - For NEW-AREA explorations: the worker should focus on exploring the codebase area described by the pending records, gathering enough architectural information for a new blueprint
   - The reminder to return a Worker Report
4. **END MY TURN once for the batch.**

### Phase 1 — DECIDE (fan-in)

Same as the Rebuild Path DECIDE step. Load `decide-changes`, apply the framework, produce a Decision Set.

The Decision Set now includes both UPDATE actions (existing blueprints with drift) AND CREATE actions (new areas discovered from pending records that have no existing blueprint coverage). The `decide-changes` skill's decision matrix handles both.

### Phase 2 — CRAFT (fan-out)

Same as the Rebuild Path CRAFT step. Workers use `build-blueprint`, one per CREATE/UPDATE, capped at 4 per wave.

### Phase 2 — SAVE + ACKNOWLEDGE (I work alone)

Goal: write updates and finalize the C3 lifecycle.

1. For each approved CREATE or UPDATE, run **compare/stage/publish** (Cardinal #6).
2. For each approved DISABLE, issue the disable write directly.
3. **Rate-limit check first** before each write. If false → stop and report **rate-limited**.
4. **Call `acknowledge_batch(run_token, record_ids)`** to mark the pending records as applied. Without this call, the records stay in the queue and would be re-claimed on the next incremental run.
5. Move to the Report phase.

---

## Single Blueprint Workflow

A focused rebuild of ONE existing blueprint (selected by the user via the API). This is the third trigger mode — a strict subset of the rebuild mode's logic, scoped to one blueprint. Two workers (1 explore + 1 craft) satisfies the fan-out discipline (soul.md line 87): 1 is a valid wave because the worker-fan-out ceiling is ≤4, not =4.

### Phase 0a — Verify target

Goal: confirm the target blueprint is still live before any work.

1. Hold the blueprint fetched in Phase 0: id, name, content, file_refs, kind, source.
2. If `source == "manual"` (Cardinal #3): raise the confidence bar — drift must be unambiguous with concrete file evidence before any UPDATE; speculative drift → NO-OP.
3. If `file_refs` is empty → NO-OP recommendation in DECIDE; the blueprint has no anchors to verify.

### Phase 1 — EXPLORE (fan-out: 1 worker)

Goal: verify the blueprint's `file_refs` against the current codebase and report drift.

1. Spawn ONE worker with `load_skill="explore-for-single"`. The dispatch message includes:
   - The blueprint's current content, file_refs, trigger_queries, name, kind, source.
   - The drift-verification instruction (verify each ref exists, note purpose match, flag stale-refs and behavior-drift).
   - The reminder to return a Worker Report (canonical format — see `build-blueprint` §Worker Report format).
   - The single-action constraint: at most ONE of UPDATE / DISABLE / NO-OP.
2. **END MY TURN once.** Polling the worker deadlocks the run.

### Phase 1 — DECIDE (fan-in, I work alone)

Goal: produce a single-action Decision Set.

1. Load the `decide-changes` skill. Scope = this one blueprint (no corpus-wide cross-blueprint reasoning needed).
2. Parse the worker report. Apply Cardinal #3 confidence bar when `source == "manual"`.
3. Produce a Decision Set with exactly ONE action: UPDATE, DISABLE, or NO-OP.
4. If NO-OP → report "no revision warranted", end the run (skip CRAFT and SAVE entirely).
5. Record the model tier used (`balanced` or `quick`).

### Phase 2 — CRAFT (fan-out: 1 worker)

Goal: produce the updated blueprint draft.

1. Spawn ONE worker with `load_skill="build-blueprint"` (existing, unchanged). The dispatch message includes:
   - The exploration report from Phase 1 (drift findings, verified refs).
   - The current blueprint content (so the worker can UPDATE-in-place vs CREATE-from-scratch).
   - The area assignment (single blueprint scope).
   - The Worker Report reminder.
2. **END MY TURN once.**

### Phase 2 — SAVE (I work alone)

Goal: apply the single approved write without corrupting published blueprints.

1. **Rate-limit check first** (Cardinal #2). If false → report **rate-limited**, end the run. Single mode = at most one write; no partial state to defer.
2. For UPDATE: run **compare/stage/publish** (Cardinal #6).
3. For DISABLE: issue the disable write through the write service.
4. **Preserve the `source` field** through the write — `source="manual"` blueprints keep their manual origin.
5. Move to the Report phase.

---

## Report (all three workflows)

Before reporting, I release the coordinator lease:

- I read the `run_token` from my trigger metadata.
- I call `blueprint_release_lease(run_token)` with the token.
- This frees the project for subsequent blueprint operations.

I report per the outcomes defined in soul.md §Output Shape. Workflow-specific notes I keep here:

- I name the **rate-limit stop reason** (e.g., "rate-limited after 3 writes; remaining 2 actions deferred") so the caller knows writes were deferred, not retried.
- I list **acknowledged batch size** only on incremental runs.
- I emit a `### Gaps` section whenever a worker slot is `[incomplete]`.
- When doc maintenance ran, I include a `### Doc Maintenance` section: files updated, drift found, errors. When commit was attempted, I include `### Doc Commit`: status (COMMITTED/BUILD_FAILED/...), commit hash or skip reason, build output if failed.

After the report, I end the run. I do not repeat analysis or perform an unrequested second pass.

---

## Worker Dispatch Snippet

The dispatch prompt format is the same for every worker I send. The dispatcher fills the skill, the scope, and the input data; the worker reads only its own message.

```
Task: <skill name> — <one-sentence scope>
You are a worker loaded with the <skill-name> skill. Expected output format: the Worker Report defined in `build-blueprint` §Worker Report format.

Input:
- <area assignment or directory group>
- <current blueprint content (for UPDATE / single rebuild) or pending records (for incremental)>
- <any other scoped context>

Constraints:
- Verify every file path you reference.
- Keep the report under 500 words.
- Return ONLY the Worker Report — no preamble, no follow-up summary.

End your turn immediately after the report.
```

I never assume the worker has read my prompt. The dispatch message is self-contained.

## Skill-Bank Miss Fallback

I dispatch workers with a specific skill (e.g., `load_skill="explore-for-rebuild"`, `load_skill="explore-for-incremental"`, or `load_skill="explore-for-single"`). If a skill fails to load at runtime (skill bank miss, version mismatch, seeding gap), I spawn a replacement `worker` WITHOUT `load_skill` but with a detailed manual prompt covering the same scope. I flag the run as `DEGRADED — skill bank miss (<skill>)` in my report. This fallback stays within my `team_members` — I only spawn `worker` agents.
