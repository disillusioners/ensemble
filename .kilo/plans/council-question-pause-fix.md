# Plan: Fix Governor `ask_questions` Pause + Councilor-Validation Gap

## Context / Root Cause (from production investigation)

Reviewer instance `79a17072` called `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="code-review", instance_name="review-council-pinned-cleanup", ...)`. This spawned governor `925d3ee2`. The governor tried to spawn a `worker` councilor, but **`worker` was not in the governor's `team_members`** (`governor/meta.json` → `["developer","coder","wanderer","explorer","doc-writer","reviewer"]`). The spawn tool (`spawn_councilor`, `daemon/tools/instance.py:978`) rejected it via `_check_team_membership("governor", resolved_agent_id)`.

Instead of self-correcting, the governor's LLM (glm-5.2) called the **`ask_questions`** tool (the question tool). `ask_questions` (`daemon/tools/question_tools.py:188`) set the pause flag → the graph's conditional post-tools edge (`daemon/graph.py:3077`) routed to `question_pause_node` → **the governor paused mid-council** waiting for a human answer. This blocked the entire review chain until a human intervened via the answer UI.

Confirmed from checkpoint blobs of `925d3ee2` (AIMessage `2026-07-31T17:24:05`):

> Q1: "The councilor_agent_id "worker" is not a valid team member and cannot be spawned. Which agent should I use for this deep code review council?"
> options: `[reviewer, developer, coder, explorer]`, `allow_custom=true`, `required=true`, `id="agent_id"`

This is a **human-in-the-loop pause fired inside an automated sub-tree** (reviewer → governor) — an automated orchestrator paused waiting on a human it has no direct user channel to.

### How it recovered (the eventual successful path)
The governor `925d3ee2` was eventually completed with a report; reviewer resumed on the completion report, then **re-invoked** `convene_council_with_skill(councilor_agent_id="developer")` → spawned governor `1e6dbccc` → spawned developer councilor `aa885e36` which delivered the full Finding Report. A second developer councilor (`2566fa6f`) was still running at investigation time. So `developer` is the known-good councilor that completed the actual review.

---

## Problems Found

### P1. The governor has the `question` tool enabled
`governor/meta.json` lists `"question"` in `tools.allow`. A governor is an automated council orchestrator whose parent is another agent (reviewer/developer/etc.), so a human-in-the-loop pause mid-council is never desirable — there is no human at the governor level to answer. This is why the LLM's fallback to `ask_questions` deadlocked the chain.

### P2. `convene_council` / `convene_council_with_skill` do NOT validate `councilor_agent_id` against the governor's team
In `daemon/tools/instance.py:1108` and `:1198`, the only team-membership check is:
```python
membership_error = _check_team_membership(caller_agent_id, "governor", caller_version_tag)
```
This verifies the **caller (reviewer) has `governor` in its team_members** ("can the reviewer convene councils?"). It does NOT verify that `councilor_agent_id` is a valid councilor for the governor. Meanwhile `spawn_councilor` (`:978`) checks `_check_team_membership("governor", resolved_agent_id)` — the governor's team. So `councilor_agent_id="worker"` passed the convene layer (reviewer→governor OK) but failed the spawn layer (governor→worker NOT OK at the time). This mismatch surfaced only at runtime inside the governor, where the LLM chose `ask_questions` instead of failing cleanly.

> Note (post-decision): the **primary** fix for the `worker` case is S2 below (add `worker` to governor team_members), which makes `convene_council_with_skill(councilor_agent_id="worker")` genuinely valid. The validation hardening (S3) then becomes defense-in-depth for *other* invalid ids (typos, non-existent agents) rather than the fix for the reported incident.

### P3. The reviewer workflow docs push `councilor_agent_id="worker"` as the default deep-review councilor
`agents/reviewer[v2]/workflow.md` Deep-Review examples use `councilor_agent_id="worker"`. Once S2 lands, these become valid (no rewrite needed). This was the direct seed of the incident: the docs asked for a councilor the governor's team rejected.

### P4. With the question tool removed, the governor's "ask the requester" path must become a clean completion (report → reply → revive), and the reviewer must know to reply rather than re-convene
The capability for an automatic reviewer↔governor continue loop already exists end-to-end (verified in code), but it is not wired in the prompts:
- `convene_council_with_skill` is non-blocking; reviewer "ENDs TURN" (reviewer workflow).
- Governor completing injects a completion report into the reviewer queue and reactivates it — already working (incident data shows reviewer `79a17072` received governor `925d3ee2`'s report).
- The reviewer has `send_message` (in `"instance"` category, allowed) to reply to the governor by instance_id.
- A COMPLETED governor revives on a new message — `instance_messaging.py:1311-1346` ("terminal revival") reactivates COMPLETED→RUNNING; the `send_message` guard (`instance.py:1345`) only blocks TERMINATED/ERROR, not COMPLETED. The checkpoint + message history + LangGraph thread all persist and reload on the next `graph.astream`.

So nothing new needs building — but the governor/reviewer prompts must encode the report→reply→revive contract, and the governor must be constrained to ask **only at Step 0** (before manifest + councilor spawns) so revival has no orphaned children.

---

## Solutions

### S1. Remove `question` from governor `tools.allow`
**File:** `agents/governor/meta.json`
- Remove `"question"` from `tools.allow`.
- With the tool gone, the governor cannot pause; its only way to surface a clarifying question is to complete its turn with the question text. The system wraps that into a completion report delivered to the requester (S4). This is the direct fix for P1.

### S2. Add `worker` to the governor's `team_members`  ⭐ (primary fix for the reported incident)
**File:** `agents/governor/meta.json`
- Add `"worker"` to `team_members`:
  ```json
  "team_members": ["developer", "coder", "wanderer", "explorer", "doc-writer", "reviewer", "worker"]
  ```
- This makes `convene_council_with_skill(councilor_agent_id="worker")` genuinely valid end-to-end: it passes `spawn_councilor`'s `_check_team_membership("governor", "worker")` check, so the governor can spawn worker councilors and the review proceeds — no validation failure, no `ask_questions` fallback.
- Honors the existing reviewer workflow docs, which treat `worker` as the default deep-review councilor — no doc rewrite required (P3 satisfied by construction).
- **Write-permission decision (confirmed):** leave worker's tools unchanged (no `deny` list). Worker councilors rely on the `⛔ READ-ONLY MODE` directive already injected by `convene` for read-only intent — same envelope as the standard reviewer→worker review path. This keeps worker identical whether it runs as a standalone review worker or as a governor councilor, and avoids diverging worker-as-worker vs worker-as-councilor behavior.

### S3. Add councilor team-membership validation to `convene_council` / `convene_council_with_skill` (defense-in-depth)
**File:** `daemon/tools/instance.py` (around lines 1104-1113 and 1194-1201)
- After `registry.resolve_to_id(councilor_agent_id)` succeeds, **add** a check that the resolved councilor id is in the **governor's** roster team_members — symmetric to `spawn_councilor:978`. Implement by calling the existing `_check_team_membership("governor", councilor_id, governor_version_tag)` helper.
- On failure, raise `ValueError` with a clear message naming the valid governor-team ids, e.g.:
  ```
  ValueError: councilor_agent_id="farmer" is not a valid councilor for the governor. Governor's team_members: developer, coder, wanderer, explorer, doc-writer, reviewer, worker.
  ```
- This fails fast at the **caller** (reviewer) before any governor is spawned — catching typos / unknown agent_ids cleanly instead of surfacing as a runtime error inside the governor where the LLM may mis-handle it.
- Resolve the governor's `version_tag` for the check using the same `_resolve_default_version_tag` already used later (`:1123`/`:1254`); move it above the check so both paths share it.
- This now guards *other* invalid ids; `worker` specifically is covered by S2. Keep it because LLM-supplied councilor ids are otherwise unchecked until deep inside the governor.

### S4. Encode the report→reply→revive contract in prompts (so the loop is automatic post-S1)
**File:** `agents/governor/workflow.md` (Step 0 "Validate Inputs")
- Make explicit that when validation fails (or the governor needs a clarification from the requester), it must **END its turn / complete** with a self-contained, explicit question in its final message — it must NOT (and now, post-S1, cannot) call `ask_questions`.
- Add the self-contained-echo requirement (mirrors the `ask_questions` F7 compaction-safety rationale): the final message must name the failing field, the constraint violated, and the list of valid options, so that after the reviewer replies and the governor revives, it can self-justify re-validation (its prior turn's AIMessage may be compacted).
- Constrain "ask the requester" to **Step 0 only** — before Step 0.5 (manifest) and before any councilor spawn. This guarantees a revived governor has no orphaned councilors to reconcile and no in-flight completion reports to race against.
- On revival (new HumanMessage from the reviewer containing the answer), re-run Step 0 with the new answer; if valid proceed to Step 0.5; if still invalid, complete again with a refined question. (The LangGraph thread/checkpoint persists across the completion-revival, so context is retained.)

**File:** `agents/reviewer[v2]/workflow.md` (report-handling / Deep-Review section)
- Add guidance to distinguish a **clarifying-question report** from a **final report**. When a governor child's completion report is a clarifying question addressed to the reviewer, the reviewer must **reply via `send_message(instance_id=<governor id from the report>, message="<answer>")`** rather than re-convening a fresh governor.
- Note the governor instance_id is carried in the completion report (`internal_report:<gov_id>:...` source, and the report body names the governor). The reviewer extracts it and replies, reviving the same governor (terminal-revival path) with its context intact — cheaper and context-preserving versus re-convening.
- Keep the existing re-convene behavior as the fallback when the governor is genuinely done (final report) or when the question cannot be answered from context and a fresh council with corrected params is preferable. (This incident recovered via re-convening with `developer`; with S2 the original `worker` council would have proceeded directly, so re-convening should become the exception.)

---

## Definition of Done
- [ ] S1: `question` removed from `governor/meta.json` `tools.allow` (grep returns nothing).
- [ ] S2: `"worker"` present in `governor/meta.json` `team_members`.
- [ ] S3: `convene_council` and `convene_council_with_skill` reject an invalid `councilor_agent_id` (e.g. a typo) at the caller with a clear `ValueError` naming valid ids; no governor spawned on rejection. Reuses `_check_team_membership("governor", …)`, symmetric to `spawn_councilor:978`.
- [ ] S4: governor workflow Step 0 documents complete-to-ask (Step-0 only, self-contained echo); reviewer workflow documents reply-via-`send_message` on a clarifying-question report vs re-convene on a final report.
- [ ] (Optional, if feasible without running the daemon) A unit test asserting `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="code-review", request=...)` spawns a governor without a team-membership error; and that an unknown id raises before spawning.

## Out of Scope / Follow-ups
- No code changes to the question-tool machinery itself (`question_tools.py`, `question_pause_node`) — only its removal from the governor's tool list.
- No changes to the completion-report / terminal-revival infrastructure in `instance_messaging.py` / `dependency_bus.py` — it already works (verified in production data).
- No `deny` list added to `worker` (confirmed: rely on the READ-ONLY prompt directive, matching existing reviewer→worker reviews).
- Consider (separately) documenting `developer` as an alternative/primary councilor for reviews where write-blocked-at-tool-layer safety is desired, since `developer[v2]` carries `"deny": ["edit_file","write_file"]` while worker does not.
