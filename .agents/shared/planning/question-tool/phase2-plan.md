# Phase 2: Backend API + SSE — Answer Endpoint & Resume Flow

## Objective
Add the `POST /api/instances/{id}/answer` endpoint that receives user answers, stores them in QuestionManager, emits an "answered" SSE event, resumes the instance (mirroring the PAUSED-branch fan-out from `messages.py`), and delivers answers to the agent as a HumanMessage.

## Coupling
- **Depends on**: Phase 1 (QuestionManager, pause flag, InstanceManager, LiveEventHub.stream_question_pack)
- **Coupling type**: tight — uses Phase 1's QuestionManager + InstanceManager methods directly
- **Shared files with other phases**: `daemon/routers/instances.py` (Phase 2 adds endpoint here), `daemon/services/question_manager.py` (Phase 1 defines, Phase 2 calls `set_answers`)
- **Shared APIs/interfaces**: `QuestionManager.set_answers()`, `InstanceManager.clear_pause_requested()`, `resume_instance_cascade()`, `resume_processing_job()`
- **Why this coupling**: The answer endpoint is the "resume half" of the question lifecycle. It must call the exact same QuestionManager and pause/resume infrastructure Phase 1 created.

## Context
- Phase 1 created the "ask + pause" half. Phase 2 creates the "answer + resume" half.
- Reference pattern: `POST /api/instances/{id}/messages` PAUSED branch at `messages.py:198-249` — the answer endpoint **mirrors this fan-out exactly** (F10).
- The answers must reach the agent as a message so the LLM sees them in context after resume.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add Answer API endpoint | `POST /api/instances/{instance_id}/answer`. Accept `{ "answers": { ... } }` (any JSON). Validate instance exists + pack pending. Store answers, resume with PAUSED-branch fan-out. | `daemon/routers/instances.py` |
| 2 | Implement answer→resume with PAUSED-branch fan-out (F10) | Mirror `messages.py:198-249`: call `resume_instance_cascade()` then `resume_processing_job()` per resumed instance. Target instance gets `message=answer_msg`; children get `silent=True`. | `daemon/routers/instances.py` |
| 3 | Format answers as HumanMessage | Create a HumanMessage containing the answers, formatted for the agent. Include question text + answer pairs so the LLM can correlate (F7 — tool placeholder already echoes Q text, but the answer message should also include it for clarity). | `daemon/routers/instances.py` or a helper |
| 4 | Add request/response models (Pydantic) | `AnswerRequest(BaseModel)` with `answers: dict` (flexible). `AnswerResponse` with the updated QuestionPack. Follow existing Pydantic model patterns in the router. | `daemon/routers/instances.py` or `daemon/schemas/` |

## Detailed Design Notes

### Task 1+2: Answer API endpoint with PAUSED-branch fan-out (F10)

```python
# daemon/routers/instances.py

@router.post("/instances/{instance_id}/answer")
async def answer_questions(
    instance_id: str,
    request: AnswerRequest,
    manager: InstanceManager = Depends(get_instance_manager),
):
    # 1. Validate instance exists
    instance = manager.get_instance(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # 2. Store answers in QuestionManager
    pack = manager._question_manager.set_answers(instance_id, request.answers)
    if pack is None:
        raise HTTPException(status_code=404, detail="No pending question pack for this instance")

    # 3. Emit SSE: question_pack with status='answered'
    try:
        await manager._live_hub.stream_question_pack(instance_id, pack_to_dict(pack))
    except Exception:
        pass  # best-effort SSE

    # 4. Format answers as a message for the agent
    answer_message = format_answers_as_message(pack)

    # 5. Resume the instance — MIRROR PAUSED-branch fan-out from messages.py:198-249 (F10)
    #    pause_instance_cascade() cascades to children, so resume must too.
    #    Target instance gets the answer message; children resume silently.
    manager.resume_instance_cascade(instance_id)  # resumes target + all paused children

    #    Per resumed instance: resume_processing_job() with appropriate message
    #    Target instance: message=answer_message
    #    Children: silent=True (they were paused as a side effect of the parent's question)
    #
    #    See messages.py:198-249 for the exact pattern — replicate it here.
    #    The key calls:
    #    - resume_instance_cascade(instance_id) — DB state transition
    #    - For target: resume_processing_job(instance_id, message=answer_message)
    #    - For children: resume_processing_job(child_id, silent=True)

    return {"status": "answered", "question_pack": pack_to_dict(pack)}
```

**⚠️ Critical: Mirror the PAUSED branch exactly (F10)**

`pause_instance_cascade()` in Phase 1 cascades to ALL children in the tree. The answer endpoint must mirror this fan-out — it cannot just resume the target instance. Study `messages.py:198-249`:

1. `resume_instance_cascade(instance_id)` — atomically transitions the entire tree from PAUSED → RUNNING
2. For each resumed instance, call `resume_processing_job()`:
   - **Target instance** (the one that asked the question): `resume_processing_job(instance_id, message=answer_message)` — the agent receives the answers
   - **Child instances** (paused as a side effect): `resume_processing_job(child_id, silent=True)` — they resume without a new message, continuing from their checkpoint

This is the same pattern used when a user sends a message to a PAUSED instance with children.

### Task 3: Format answers as HumanMessage

The agent needs to see the answers in its conversation. The answer message should include both the question text and the answer for each Q&A pair (complements F7 — the tool placeholder echoes Q text, and the answer message re-states them):

```python
def format_answers_as_message(pack: QuestionPack) -> str:
    lines = ["Here are the user's answers to your questions:", ""]
    for i, q in enumerate(pack.questions):
        answer = pack.answers.get(q.id) or pack.answers.get(q.text) or "(no answer)"
        lines.append(f"**Q{i+1}:** {q.text}")
        lines.append(f"**A{i+1}:** {answer}")
        lines.append("")
    return "\n".join(lines)
```

This produces:
```
Here are the user's answers to your questions:

**Q1:** Should we use approach A or B?
**A1:** Approach A — it's simpler.

**Q2:** What's the target deadline?
**A2:** End of next sprint.
```

The message is injected as a HumanMessage via the existing resume path. The agent processes it naturally and continues its reasoning with the answers visible.

**Why include question text in the answer message (F7)**: The tool placeholder already echoes Q text for compaction safety. The answer message ALSO includes Q text so that even if the tool result was compacted away, the agent can correlate Q↔A from the answer message alone. Defense in depth.

### Resume path selection

The answer endpoint replicates the exact resume mechanism used by `POST /api/instances/{id}/messages` when the instance is PAUSED (`messages.py:198-249`):
- Calls `resume_instance_cascade()` then `resume_processing_job()` per instance
- The message is processed via `_process_message_with_tracking(is_retry=True)`

**⚠️ Key**: The instance was paused by the graph pause hook (Phase 1's `question_pause_node`). The resume must work identically to an externally-paused instance — because after `pause_instance_cascade()`, the instance state is PAUSED regardless of who triggered it. The resume path doesn't care *why* the instance was paused.

## Key Files
- `daemon/routers/instances.py` — new endpoint
- `daemon/services/question_manager.py` — (Phase 1, read-only here) `set_answers()` method
- `daemon/manager.py` — `resume_instance_cascade()`, `resume_processing_job()`, `_live_hub`
- `daemon/routers/messages.py` — (read-only reference) PAUSED branch pattern at lines 198-249

## Constraints
- Answers JSON is NOT format-enforced — accept any dict. For external API compatibility.
- The endpoint must be flexible: if there's no pending question pack, return 404 (not error).
- SSE emission is best-effort (try/except).
- Resume must use the EXISTING resume infrastructure — do not create a new resume path.
- **Resume fan-out must mirror `pause_instance_cascade`** — resume all children too, not just the target (F10).
- The answer message must be a HumanMessage (or equivalent) so the agent processes it naturally.
- Include question text in the answer message format for compaction safety (F7).

## Deliverables
- [ ] `POST /api/instances/{id}/answer` endpoint implemented
- [ ] Answer→resume flow: store answers → SSE → resume cascade with fan-out (F10)
- [ ] Target instance gets `message=answer_msg`; children get `silent=True`
- [ ] Answers formatted as HumanMessage with Q&A pairs (F7)
- [ ] Pydantic request/response models
- [ ] Unit test: answer endpoint stores answers + resumes instance (target + children)
- [ ] Unit test: answer endpoint returns 404 if no pending pack
