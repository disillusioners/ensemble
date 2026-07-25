# Workflow

## Overview

One workflow: **Validate → Persist Manifest → Spawn Councilors → Dispatch → Collect → Synthesize → Clear Errors → Deliver**.

The governor is a synthesizer. Every step below assumes that all real work is delegated to councilors; the governor's job is to coordinate, track, and synthesize.

---

## Step 0: Validate Inputs (ALWAYS FIRST)

Before doing anything else, validate the inputs.

```raw
1. councilor_agent_id:
   - Must be present and non-empty
   - Must appear in the agent's team_members list
   - If invalid → STOP and ask the requester to specify a valid agent_id

2. models:
   - Must be present as a non-empty list
   - Each model must be in the injected <allowed_models> block
   - If invalid or empty → STOP and ask
   - D4 revision: Max 4 councilors. If >4 models are available, pick the 4 most diverse.
   - W7 / D10: canonical-model dedup — collapse case-insensitive duplicates
     (e.g., "gpt-4o" and "GPT-4O" both normalize to the same canonical model)
     before finalizing the model list

3. request:
   - Must be clear and unambiguous
   - If unclear → ask the requester to clarify before proceeding
   - The same request will be forwarded to every councilor
```

If any validation fails, STOP. Do not proceed to Step 0.5. Do not persist a manifest.

**Minimum council size warning:** If fewer than 2 distinct canonical models are available, warn the requester before proceeding. The requester may explicitly choose to proceed; the resulting output will be degraded.

---

## Step 0.5: Persist Council Manifest (W4/D8) — BEFORE FIRST SPAWN

Before spawning any councilor, write the council manifest to `shared_context_metadata` under the key `council_manifest`. This is the **crash-recovery anchor**.

```raw
1. Call shared_context_metadata to set:
   "council_manifest": {
     "request_id": "<uuid>",
     "councilor_agent_id": <validated agent_id>,
     "original_request": <the request>,
     "spawned_at": <current time, ISO timestamp>,
     "deadline": <current time + 30 min per councilor (soft limit)>,
     "deadline_hard_cap": <current time + 1 hour (absolute cap, immutable)>,
     "deadline_extended": false,
     "councilors": []
   }
2. Proceed to Step 1
```

**Manifest schema (authoritative):**

```json
{
  "request_id": "string (uuid)",
  "councilor_agent_id": "string",
  "original_request": "string",
  "spawned_at": "ISO timestamp",
  "deadline": "ISO timestamp (30min soft)",
  "deadline_hard_cap": "ISO timestamp (1h hard)",
  "deadline_extended": false,
  "councilors": [
    {
      "instance_id": "string",
      "model": "string",
      "canonical_model": "string",
      "status": "SPAWNED|RUNNING|COMPLETED|TIMED_OUT|PARTIAL_TIMED_OUT|FAILED",
      "result": "string|null",
      "dispatch_status": "DISPATCHED|FAILED"
    }
  ]
}
```

**On restore (crash recovery):**

```raw
1. Read council_manifest from shared_context_metadata
2. If manifest exists with councilors:
   a. For each councilor, call get_instance_info(instance_id)
   b. Update status: COMPLETED / FAILED / RUNNING / TIMED_OUT / PARTIAL_TIMED_OUT
   c. Collect available results
   d. If at least 1 result is available (COMPLETED or PARTIAL_TIMED_OUT)
      → proceed to Step 4 (Synthesize)
   e. If 0 results → wait for remaining, checking the deadline
      (30min soft / 1h hard)
3. If no manifest exists → fresh start at Step 1
```

---

## Step 1: Convene the Council (REVISED — max 4, structured)

Spawn one councilor per canonical model. Cap at **4** (D4 / WorkerPool alignment).

```raw
For each canonical model in the validated model list (up to 4):
  1. spawn_councilor(
       councilor_agent_id = <validated agent_id>,
       model              = <this model>,
       initial_message    = <the request>,
       instance_name      = "councilor-<model-short-name>"
     )
   2. Record in manifest:
      {
        "instance_id":      <returned>,
        "model":            <model>,
        "canonical_model":  <canonical form>,
        "status":           "SPAWNED",
        "result":           null
      }
   3. Update shared_context_metadata with the new councilor entry

   `dispatch_status` is intentionally absent until Step 2 completes the
   dispatch. It must then be set to exactly `DISPATCHED` or `FAILED`; the
   manifest has no intermediate dispatch state.
```

**⚠️ W7 (model canonicalization):** Do not spawn two councilors with the same canonical model name. The `spawn_councilor` tool normalizes to canonical; also dedup in the manifest before spawning.

**Do NOT send messages yet — spawn all first, then dispatch (W1).** This is **validate-all-then-dispatch**, not a sequential spawn-send loop.

If a spawn fails for one councilor, record it as `FAILED` in the manifest and proceed with the remaining councilors. Do not abort the whole council because of one failed spawn.

---

## Step 2: Dispatch Request (REVISED — structured tracking W1/W2)

Send the **same** request to every spawned councilor. Track every dispatch outcome.

```raw
For each councilor in manifest (status = SPAWNED):
  1. result = send_message(instance_id = <councilor_id>, message = <the request>)

  2. If result indicates success:
     → Update manifest:
       councilor.status          = "RUNNING"
       councilor.dispatch_status = "DISPATCHED"

  3. If result indicates error (W2):
     → Update manifest:
       councilor.status          = "FAILED"
       councilor.dispatch_status = "FAILED"
       councilor.error           = <record the error>
     → Do NOT retry silently
     → Proceed with remaining councilors

  4. Update shared_context_metadata
```

**W1 correction:** This is **validate-all-then-dispatch**, NOT a sequential spawn-send loop. All spawns complete first (Step 1), then all dispatches (Step 2). This allows per-state compensation if any step fails.

---

## Step 3: Collect Results (REVISED — D9 tiered deadline + degraded quorum)

Wait for councilor results, respecting the tiered deadline and degraded quorum.

```raw
1. Results arrive as completion reports (fire-and-forget pattern).
2. As each report arrives:
   a. Update manifest:
      councilor.status = "COMPLETED" or "FAILED"
   b. Store the result for analysis
   c. Update shared_context_metadata

3. Periodically check time (D9 tiered deadlines):
   a. For each RUNNING councilor, check if the deadline has been exceeded.

   b. Soft limit (30min) hit:
      - Call get_instance_info(instance_id) to check status.
      - If RUNNING AND task is clearly long-running (multi-file, complex
        analysis) → EXTEND:
          * Update councilor.deadline to a later value
          * Set councilor.deadline_extended = true
          * Update manifest. Do NOT extend past deadline_hard_cap.
      - If ERROR / COMPLETE → mark appropriately, proceed.
      - If unsure → DEFAULT: extend ONCE (up to 1h hard cap).
        Do not extend repeatedly.

   c. Hard limit (1h) hit:
      - Call terminate_instance(instance_id) — force kill.
      - Capture any partial result.
      - Mark councilor.status = "PARTIAL_TIMED_OUT".
      - Update manifest with the partial result and the PARTIAL_TIMED_OUT
        status.

4. When ≥1 result is available (COMPLETED or PARTIAL_TIMED_OUT) OR all
   councilors are resolved:
   → Proceed to Step 4

5. If 0 results (all FAILED, all PARTIAL_TIMED_OUT with empty output):
   → Report failure. Do NOT clear errors (Step 5).
```

**Degraded synthesis (1 result):** If only 1 result is available (whether `COMPLETED` or `PARTIAL_TIMED_OUT`), the synthesis in Step 4 will prepend a **degraded-confidence notice** to the final output. See rule.md for the notice format.

**Note on partial results:** A single partial result from a `PARTIAL_TIMED_OUT` councilor counts as 1 degraded result. If the partial result is empty or unusable, it counts as 0 results → report failure.

---

## Step 4: Analyze + Synthesize (REVISED — D9 degraded synthesis)

Analyze all councilor results and synthesize the final answer.

```raw
1. Count available results (COMPLETED or PARTIAL_TIMED_OUT):
   - 0 results → report failure (do NOT clear errors in Step 5)
   - 1 result  → DEGRADED path (see below)
   - 2+ results → NORMAL path

2. NORMAL path (2+ results):
   a. Read all results.
   b. Identify agreement zones (high confidence) and disagreement zones
      (needs resolution).
   c. Extract the strongest elements from each councilor output.
   d. Compose a unified answer (no degraded notice).
   e. Quality gate: if the synthesized answer is weaker than the best
      single councilor output, FALL BACK to that best output.
   f. Surface any unresolved disagreements transparently.

3. DEGRADED path (1 result):
   a. Use the single available result as the basis.
   b. If the single result is PARTIAL_TIMED_OUT (1h hard kill), note the
      partial nature in the synthesis.
   c. Compose the answer from the single source.
   d. Prepend the **degraded-confidence notice** to the output (see rule.md).
   e. Quality gate: there is no "best single" to compare to — use the
      only available result.
```

**Degraded-confidence notice format (from rule.md):**

```raw
⚠️ Confidence Notice: This answer was synthesized from a single councilor source
(model: <model>, status: <COMPLETED|PARTIAL_TIMED_OUT>). Multi-model consensus
was not achieved — confidence is reduced. Consider re-running for higher confidence.
---
<actual synthesized answer>
```

**⚠️ Reminder:** Degraded synthesis STILL calls `clear_councilor_errors()` in Step 5 (synthesis succeeded). The degraded notice is the user's signal of reduced confidence — it is **not** a failure.

---

## Step 5: Clear Errors (NEW — C1/D7 — CRITICAL)

Before delivering, clear the sticky parent-error flag **if and only if** synthesis succeeded.

```raw
If synthesis produced a valid answer (NORMAL or DEGRADED path):
  1. Call clear_councilor_errors()
     → This clears _parent_errored[governor_instance_id] in the dependency bus
     → Allows the governor to finalize as COMPLETED despite individual
       councilor failures
  2. Proceed to Step 6

If synthesis FAILED (all councilors errored, no answer):
  → Do NOT call clear_councilor_errors()
  → Governor will finalize as ERROR (correct behavior)
```

**⚠️ Why this is critical:** Without this step, ANY councilor failure forces the governor's terminal status to ERROR via the dependency bus's sticky `_parent_errored` flag — even if 3 of 4 councilors succeeded and the governor synthesized a perfect answer. This step restores fault-tolerance.

**Timing:** Call `clear_councilor_errors()` IMMEDIATELY before producing the final output. Do not delay. A late child error arriving after the clear is acceptable (TOCTOU window) — synthesis has already succeeded and the result is sound.

---

## Step 6: Deliver

Present the synthesized answer to the requester.

```raw
1. Present the synthesized answer (NORMAL or DEGRADED path).
2. If disagreements were unresolved, surface them — quote the councilor
   positions, explain my reasoning, recommend the preferred position.
3. Terminate any councilors that are still running (free worker slots).
4. Clear the council manifest from shared_context_metadata on successful
   delivery.
5. Report completion to the caller.
```

**Cleanup is part of delivery.** Leaving orphan councilors running wastes worker slots. Leaving the manifest behind pollutes shared_context_metadata.

---

## Optional: Refinement Rounds (D5)

Refinement rounds are **optional** and **capped**.

```raw
1. Round 0 (mandatory): steps 0–6 above.
2. If the synthesized answer has unresolved factual disagreement:

   Round 1 (optional):
   - Select ≤2 councilors to re-query (the ones whose views I disagree with,
     or whose views I most want to test).
   - Send a targeted clarification.
   - Collect, update manifest, re-synthesize.
   - Up to 2 councilors re-queried per round.

3. If still unresolved:

   Round 2 (optional, final):
   - One more re-query to ≤2 councilors.
   - Collect, update manifest, re-synthesize.

4. STOP. Deliver the final answer. No Round 3.
```

**Hard limits:** 2 councilors per refinement round, 2 refinement rounds total. After Round 2, the workflow MUST stop and deliver the final answer — regardless of remaining uncertainty.

---

## Crash Recovery (W4)

If the governor instance is restored after a crash or restart:

```raw
1. Read shared_context_metadata["council_manifest"].
2. If manifest exists:
   a. For each councilor entry:
      - Call get_instance_info(instance_id)
      - Update status to COMPLETED / FAILED / RUNNING / TIMED_OUT
        / PARTIAL_TIMED_OUT based on current state
   b. If ≥1 result available (COMPLETED or PARTIAL_TIMED_OUT):
      → Jump to Step 4 (Synthesize) with whatever is available
   c. If 0 results so far but councilors are RUNNING:
      → Resume Step 3 (Collect), respecting the tiered deadline
   d. If all councilors resolved and 0 results → report failure
3. If no manifest exists → fresh start at Step 1
```

The manifest is the single source of truth across crashes. Every councilor read, every status update, every result write goes through it.

---

## Error Handling

| Situation | Action |
|-----------|--------|
| Invalid `councilor_agent_id` | STOP at Step 0 |
| Invalid `model` (not in `<allowed_models>`) | STOP at Step 0 |
| `spawn_councilor` raises (invalid model) | Report, do NOT retry with fallback |
| Councilor errors during execution | Proceed with available results; clear errors in Step 5 |
| **`send_message` returns an error (W2)** | Mark councilor `FAILED` with `dispatch_status=FAILED`, record error, do NOT retry silently, proceed with remaining councilors |
| **All councilors fail** | Do NOT clear errors; report failure (bus marks ERROR) |
| Councilor times out (30min soft limit) | Extend if RUNNING + long-running task; otherwise mark `TIMED_OUT` and proceed |
| Councilor hits 1h hard limit | `terminate_instance`, capture partial result, mark `PARTIAL_TIMED_OUT`, counts as 1 degraded result |
| All councilors fail / 0 results | Report failure. Do NOT clear errors. |
| 1 result only (degraded) | Synthesize with degraded-confidence notice prepended to output |
| Late child error after `clear_councilor_errors()` | Acceptable (TOCTOU window); synthesis already succeeded |
| Manifest missing on restart | Fresh start at Step 1 |
| Crash mid-council | On restore, read manifest, refresh statuses, resume from Step 3 or Step 4 |

---

## Manifest Example (for reference)

```json
{
  "request_id": "a1b2c3d4-...",
  "councilor_agent_id": "developer",
  "original_request": "Implement rate limiting on the login endpoint with max 5 attempts per 15 min.",
  "spawned_at": "2026-07-25T10:00:00Z",
  "deadline": "2026-07-25T10:30:00Z",
  "deadline_hard_cap": "2026-07-25T11:00:00Z",
  "deadline_extended": false,
  "councilors": [
    {
      "instance_id": "inst-abc123",
      "model": "gpt-4o",
      "canonical_model": "gpt-4o",
      "status": "COMPLETED",
      "result": "Use existing middleware in src/middleware/...",
      "dispatch_status": "DISPATCHED"
    },
    {
      "instance_id": "inst-def456",
      "model": "claude-3-5-sonnet",
      "canonical_model": "claude-3-5-sonnet",
      "status": "RUNNING",
      "result": null,
      "dispatch_status": "DISPATCHED"
    },
    {
      "instance_id": "inst-ghi789",
      "model": "GPT-4O",
      "canonical_model": "gpt-4o",
      "status": "FAILED",
      "result": null,
      "dispatch_status": "FAILED"
    }
  ]
}
```

The third entry illustrates W7 dedup: caller wrote `GPT-4O`, normalized to canonical `gpt-4o`, deduped against the first entry (already spawning `gpt-4o`), and the spawn was skipped → `FAILED`. In practice, dedup happens at validation (Step 0 / Step 1) so this duplicate would never be spawned; the example is shown for completeness.
