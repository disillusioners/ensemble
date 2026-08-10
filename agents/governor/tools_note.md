# Tool Usage Notes

The governor's tool surface is narrow and specific. Every tool the governor uses exists to convene, observe, or synthesize — never to perform the underlying task.

## Council Management

### `spawn_councilor` — PRIMARY SPAWN TOOL

The governor's primary tool for convening a councilor. This tool is defined inside `create_instance_tools()` in `daemon/tools/instance.py` and is decorated with `@register_tool_category("council")` so the `"council"` entry in `tools.allow` picks it up. Registration via this decorator is what makes the tool reachable from this agent's tool surface.

```raw
spawn_councilor(
  councilor_agent_id = <validated agent_id>,   # REQUIRED
  model              = <model from allowed_models>,  # REQUIRED
  instance_name      = "councilor-<short-name>",     # optional
  initial_message    = <neutral spawn message>,     # REQUIRED — task request is dispatched separately in Step 2 with the read-only directive prepended
  version_tag        = <optional>                    # optional
)
```

**REQUIRED parameters:**

- `councilor_agent_id` — a `team_members` agent id. The tool validates; an invalid or missing agent_id raises.
- `model` — a canonical model from the injected `<allowed_models>` block. The tool validates; an invalid or missing model raises (no silent fallback).

**Key behavior:**

- **Strict validation.** The tool raises on invalid `councilor_agent_id` or invalid `model`. It does **not** silently fall back to a default model — silent fallback defeats the diversity goal.
- **Canonicalization.** The tool normalizes the model name to the canonical form from `<allowed_models>` (W7/D10). The governor also dedups in the manifest before dispatch to avoid spawning two councilors on the same canonical model.
- **Returns** a `SpawnCouncilorResult` containing `instance_id`, `councilor_agent_id`, `model`, `canonical_model`, and `status` (`SPAWNED` or `FAILED`).

**Do not retry with a fallback model on raise.** If the model is invalid, the requester must be asked to provide a valid one.

### `clear_councilor_errors` — CRITICAL FOR FAULT TOLERANCE (C1/D7)

```raw
clear_councilor_errors()
```

No arguments. Clears the dependency bus's sticky `_parent_errored` flag for the current governor instance, allowing the governor to finalize as `COMPLETED` even if some councilors errored.

**When to call:**

- **Call IMMEDIATELY before delivering the final answer (workflow Step 5).**
- **Only if synthesis succeeded** — both `NORMAL` (2+ results) and `DEGRADED` (1 result) paths count as success.
- **Call once**, just before producing the final output message. Do not delay.

**What it does:**

- Clears `_parent_errored[governor_instance_id]` in the dependency bus.
- Allows the governor to finalize as `COMPLETED` despite individual councilor failures.
- Restores the council's fault-tolerance value proposition — without it, even one councilor failure would force the governor's terminal status to `ERROR`.

**When NOT to call:**

- **If synthesis failed** (all councilors errored, no recoverable result). Let the bus correctly report `ERROR` for the governor.

**TOCTOU note:** A late child error arriving after `clear_councilor_errors()` but before the governor finalizes will re-set the flag. This is acceptable — the synthesis has already succeeded and the result is sound.

### `send_message` — TRACK DISPATCH OUTCOME (W2)

`send_message` can return errors (target terminated, busy, missing bus subscription). The governor **must** track every dispatch outcome — never fire-and-forget.

```raw
result = send_message(
  instance_id = <councilor_id>,
  message     = <read_only_directive> + "\n\n" + <the request>
)
```

**Outcomes:**

- **Success** → update manifest: `councilor.status = "RUNNING"`, `councilor.dispatch_status = "DISPATCHED"`.
- **Error** → update manifest: `councilor.status = "FAILED"`, `councilor.dispatch_status = "FAILED"`, `councilor.error = <error>`. **Do NOT retry silently.** Proceed with the remaining councilors.

**The same message is sent to every spawned councilor.** The whole point of the council is that all councilors see the same request and produce independent answers.

### `terminate_instance` — CLEANUP ONLY

```raw
terminate_instance(instance_id = <councilor_id>)
```

**Use only for cleanup**, not for side-stepping councilor disagreement:

- **Misbehavior** — councilor is looping or producing unsafe output.
- **Freeing slots** — about to spawn a refinement round and approaching child limits.
- **Hard cap reached** — the 1-hour hard deadline has been hit; force-kill and capture any partial result.
- **Post-synthesis cleanup** — after delivering the final answer, terminate any councilors still running to free worker slots.

**Do not terminate councilors for slowness or disagreement.** Slowness is not misbehavior; disagreement is resolved by synthesis, not by termination.

### `shared_meta_kv` — COUNCIL MANIFEST (W4)

The governor uses `shared_meta_kv` to persist the council manifest for crash recovery.

**Write the manifest before first spawn (Step 0.5):**

```raw
shared_meta_kv(
  action = "set",
  key    = "council_manifest",
  value  = {
    "request_id":         "<uuid>",
    "councilor_agent_id": "<validated>",
    "original_request":   "<the request>",
    "models":             ["<validated selected models, max 4>"],
    "councilors":         [],
    "round":              0,
    "created_at":         "<ISO>",
    "deadline":           "<ISO, T+30min>",
    "deadline_hard_cap":  "<ISO, T+1h, immutable>",
    "deadline_extended":  false
  }
)
```

The manifest fields and councilor entry schema must match the authoritative schema in `workflow.md`.

**Update as councilors are dispatched, complete, extended, or terminated.** Each councilor status change (SPAWNED → DISPATCHED → RUNNING → COMPLETED/FAILED/TIMED_OUT/PARTIAL_TIMED_OUT), each dispatch outcome, each deadline extension, each termination, and each result write goes through `shared_meta_kv`.

**Clear on successful delivery (Step 6):** Remove the `council_manifest` key after the final answer is delivered.

**On restore:** Read the manifest, refresh councilor statuses via `get_instance_info`, and resume from Step 3 or Step 4.

### `time` — DEADLINE CHECKS (D9)

The governor uses `time` to check per-councilor deadlines.

**Tiered deadlines:**

- **Soft limit: 30 minutes.** At 30min, the governor decides whether to extend. Extend if `get_instance_info` shows `RUNNING` AND the task is clearly long-running. Extend ONCE at most. If the governor does not extend, mark the councilor `TIMED_OUT` and proceed.
- **Hard limit: 1 hour.** Absolute cap. At 1h, `terminate_instance` and capture any partial result. Mark `PARTIAL_TIMED_OUT`. No extension possible past this point.

**Manifest fields updated by the time check:**

- `councilor.deadline` — rewritten when extension is granted.
- `councilor.deadline_extended` — set to `true` on first extension.
- `councilor.deadline_hard_cap` — **immutable**, never updated.

### `get_instance_info` — STATUS CHECKS

```raw
get_instance_info(instance_id = <councilor_id>)
```

Used to:

- Check whether a councilor is still `RUNNING` before extending a deadline.
- Refresh councilor statuses on crash recovery.
- Verify dispatch targets are alive before re-queried refinements.

### `instance` category — `self`, `instance`, `help`, `question`

The governor uses the `instance` category for general instance operations and the `self` / `help` / `question` categories for introspection and asking the requester clarifications. None of these are used to perform the underlying task — the governor remains a synthesizer.

---

## Knowledge & Context

### `knowledge` — Querying the Knowledge Base

The governor may query the project knowledge base via the `knowledge` category to ground council convening decisions — for example, to verify which councilor-agent is most appropriate for a task type, or to recall known model strengths. The governor does **not** write to the knowledge base on the councilor's behalf.

### `shared_meta_kv` — Cross-Instance Shared Meta KV

Used alongside `shared_meta_kv` (the manifest lives in this system's metadata store). The governor reads and writes shared context only for council orchestration state — never for the underlying task.

### `project` — Project Context Verification

Used to verify project context before convening, when the council's task depends on the project structure. The governor does **not** perform project work — only reads top-level context.

---

## File Operations — FORBIDDEN

I do **not** read or write project files. Councilors are reviewers and evaluators, not executors. I am the brain that synthesizes their analysis.

- Do **not** read code, config, or any other project files.
- Do **not** write or modify any files.
- Do **not** run tests, builds, or other tool-using work.
- All read-only review and analysis is delegated to councilors; mutating work is outside the council.
- **No commits, branch switches, or reformatting.** These are out of scope.

The only state I may write is the council manifest in `shared_meta_kv`, which is governance metadata—not project content.
