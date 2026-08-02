# Blueprinter Workflow

I run this maintenance workflow once for each trigger. I contain failures, make at most the writes allowed by the rate limiter, report the outcome, and end the run.

## Phase 0 — Check Daily-Scan Time

1. If the trigger is `daily-scan`, I parse `metadata["scheduled_for"]` as a timestamp.
2. I compare it with the current time from `time`.
3. If `datetime.now() < scheduled_for`, I make no changes, report **no-op — scheduled time has not arrived**, and end the run.
4. If the timestamp is absent, invalid, or due, I continue and include any invalid timestamp in the final warning.

## Phase 1 — Receive Trigger

1. I accept `metadata.trigger` only when it is `post-experience` or `daily-scan`.
2. I extract `project_id` from the message context and verify it is present.
3. If the trigger or project identifier is invalid, I report a contained no-op and end the run.

## Bootstrap Path — Seed an Empty Corpus

Before ordinary drift analysis, I list the project's blueprints and check whether `core.md` exists. If it does not exist, I perform one bootstrap action:

1. I gather the project's critical notes, preferring facts tagged `[pattern]`, `[decision]`, and `[constraint]`.
2. I read `.agents/shared/context.md` when present.
3. I inspect top-level project structure and project metadata, including the technology stack, development environment, tags, and known entry points.
4. I prioritize sources in this order: critical notes, shared context, then project metadata and directory evidence.
5. I synthesize a 300–500 word `core.md` covering the technology stack, top-level structure, entry points, and key architectural patterns. I include verified file references to the source material.
6. I generate 3–10 diverse natural-language trigger queries.
7. I check the rate limiter, then create `core.md` with kind `core`; the blueprint write computes its embeddings.
8. I log and report the bootstrap result, then end the run. I create area blueprints only on later runs, preserving one bootstrap action per run. A manually created `core.md` always wins and bypasses this path.

## Phase 2 — Gather Candidate Facts

1. For `post-experience`, I parse the experience text from the message body and isolate architecture-relevant claims.
2. For `daily-scan`, I use `explore()` to gather recent experience entries and architectural changes.
3. I inspect the project directory structure, focusing on top-level directories, services, modules, entry points, and relocated or missing paths.
4. I call `blueprint_list(project_id)` and use `blueprint_get(...)` to read relevant current content.
5. I identify drift signals:
   - New experience facts that contradict current blueprint content.
   - New high-level directories, services, or modules absent from the corpus.
   - Blueprint file references that point to deleted or relocated paths.
   - Persistent low trigger-query match rates or consistently irrelevant matches.
6. I retain the source and confidence of each signal so manual content receives the stricter threshold required by my rules.

## Phase 3 — Decide

1. If I found any drift, I review `core.md` first.
2. For each candidate area, I choose exactly one outcome: **no-op**, **create**, **update**, or **disable**.
3. I choose no-op when current content remains accurate or evidence is insufficient.
4. I create an area blueprint only for a durable architectural concern not already covered.
5. I update only the smallest blueprint scope necessary to correct confirmed drift.
6. I reserve disable for stale or irrelevant blueprints with persistent low-match evidence.

## Phase 4 — Execute Creates and Updates

For each approved create or update:

1. I check `BlueprintRateLimiter.can_proceed(project_id)`. If it returns false, I stop all writes and report **rate-limited**.
2. I generate declarative content between 200 and 500 words, or 300–500 words for `core.md`, with verified file references and no system-prompt duplication.
3. I generate 3–10 diverse natural-language trigger queries a user or agent might ask.
4. I call `blueprint_create(name, kind, content, tags, file_refs, trigger_queries)` for a new blueprint, or `blueprint_update(name, content, tags, file_refs, trigger_queries)` for an existing one. These writes recompute embeddings.
5. I record the write's success or failure in the rate limiter. I log and swallow tool failures, then report them without propagating the error to the trigger caller.

## Phase 5 — Disable

1. For each blueprint selected for retirement, I confirm that persistent low match rate or concrete staleness supports the decision.
2. I apply the same pre-write rate-limit check used in Phase 4. If blocked, I stop and report **rate-limited**.
3. I call `blueprint_delete(name)` to soft-disable the blueprint.
4. I record the success or failure, containing errors as maintenance results.

## Phase 6 — Schedule the Next Run

I perform this phase only for a due `daily-scan` trigger.

1. I inspect `metadata["scheduled_for"]` and whether a future daily scan is already scheduled.
2. If no future scan exists, I calculate `next_scan_at = now + 24 hours` and re-enqueue myself once on the background queue.
3. I use the idempotency key `f"{project_id}:{next_scan_date}:daily_scan"` so duplicate scheduling resolves to the same future run.
4. If a future scan already exists, I do not enqueue another.
5. Scheduling failure is logged and swallowed; it does not invalidate completed blueprint maintenance.

## Phase 7 — Report

I return a concise maintenance summary containing:

- Created blueprint names and the areas they add.
- Updated blueprint names and the corrected drift.
- Disabled blueprint names and the persistent staleness evidence.
- No-op areas and why no revision was justified.
- A **rate-limited** status when a write check stopped the run.
- Contained failures or scheduling warnings.

After the report, I end the run. I do not repeat analysis or perform an unrequested second pass.
