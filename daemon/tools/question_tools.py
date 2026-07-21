"""Question tools for per-instance user-pause-and-answer flows.

Mirrors the closure-injection pattern of ``daemon.tools.todo_tools``:
``create_question_tools(manager, current_instance_id, live_event_hub)``
is invoked from ``create_instance_tools`` to assemble the per-instance
tool list. The single ``ask_questions`` tool delegates to
``manager._question_manager`` for state and emits a ``question_pack``
SSE event before setting the pause flag.

Lifecycle:
    1. Agent calls ``ask_questions(questions=[...])``.
    2. Tool stores the pack via ``QuestionManager.set_question_pack``
       (rejects duplicate pending packs — F8/F11).
    3. Tool emits ``question_pack`` SSE event with ``status="pending"``.
       Emission is best-effort and runs synchronously BEFORE the pause
       flag is set, because the subsequent pause cascade cancels the
       graph task mid-execution and skips any post-commit SSE code
       (F3 / SSE-timing note).
    4. Tool sets the pause flag via
       ``manager.set_question_pause_requested(current_instance_id)``.
    5. Tool returns a string that ECHOES the question text (F7
       compaction safety — after context compaction, the AIMessage that
       issued the tool call may be lost; echoing Q text lets the LLM
       correlate Q↔A from later context alone).
    6. After the agent turn completes, the conditional post-tools edge
        in ``daemon.graph.build_instance_graph`` routes to
        ``question_pause_node`` which sets the deferred-pause marker
        (C2 fix — the actual ``pause_instance_cascade`` runs from the
        post-graph completion path, not from inside the graph task)
        and clears the flag in its ``finally`` block.

Answers flow back through the Phase 2 answer API
(``POST /api/instances/{id}/answer``) — see ``daemon.routers.instances``.
"""

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.services.question_manager import pack_to_dict

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.services.live_event_hub import LiveEventHub

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Question"
CATEGORY_DOC = """\
Question tools for asking the user a batch of structured questions and
pausing the instance until they answer.

- ask_questions(questions): store a QuestionPack, emit ``question_pack`` SSE
  (status=pending), set the pause flag, and return a compaction-safe
  echo of the question text. The graph's conditional post-tools edge
  routes to ``question_pause_node`` which sets the deferred-pause
  marker (C2 fix — ``pause_instance_cascade`` runs from the post-graph
  completion path) and clears the flag in ``finally``.

The user submits answers through the Phase 2 answer API
(``POST /api/instances/{id}/answer``), which resumes the instance and
delivers the answers as a HumanMessage. Only one pack may be pending
per instance — a second ``ask_questions`` call while a pack is still
pending returns an error string.
"""


async def _emit_pending_pack(
    live_event_hub: "LiveEventHub | None",
    current_instance_id: str,
    pack,
) -> None:
    """Best-effort SSE emission of the pending pack — never raises.

    Wraps ``live_event_hub.stream_question_pack`` in a try/except so a
    transport hiccup never blocks the question flow. The SSE payload
    uses the **frozen pack_to_dict schema** consumed by the frontend;
    any schema change here must be coordinated across Phase 1 (pending
    event) and Phase 2 (answered event).

    Args:
        live_event_hub: The ``LiveEventHub`` reference, or ``None``
            when the hub is not wired (tests, partial bootstrap).
        current_instance_id: Owning instance identifier.
        pack: The newly stored :class:`QuestionPack` to serialize.
    """
    if live_event_hub is None:
        return
    try:
        await live_event_hub.stream_question_pack(
            current_instance_id, pack_to_dict(pack)
        )
    except Exception as e:
        logger.warning(
            f"question SSE emission failed for instance "
            f"{current_instance_id} ({len(pack.questions)} questions): {e}"
        )


def create_question_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    live_event_hub: "LiveEventHub | None" = None,
) -> list:
    """Create question tools with injected manager and SSE hub.

    Args:
        manager: The :class:`InstanceManager` instance to use for
            QuestionManager access and pause-flag toggling.
        current_instance_id: The ID of the owning instance.
        live_event_hub: Optional :class:`LiveEventHub` for SSE
            emission. When ``None``, mutations still succeed but no
            event is emitted (matches the ``_emit_update`` pattern in
            ``daemon.tools.todo_tools``).

    Returns:
        A single-element list containing the ``ask_questions`` tool.
    """

    @register_tool_category("question")
    @tool
    async def ask_questions(questions: list[dict] | None = None) -> str:
        """Ask the user one or more questions; pause the instance until they answer.

        Each entry in ``questions`` is a dict with:
          - ``text`` (str, required) — the question body shown to the user.
          - ``options`` (list[str], optional) — pre-canned answer options
            for the frontend to render as buttons / a dropdown.
          - ``allow_custom`` (bool, default ``True``) — whether the user
            may supply a free-text answer in addition to the options.
          - ``required`` (bool, default ``True``) — whether the question
            must be answered.
          - ``id`` (str, optional) — caller-supplied identifier; when
            missing the manager auto-generates a UUID4.

        The instance is paused after this call. The user submits answers
        through ``POST /api/instances/{id}/answer`` (the Phase 2 answer
        endpoint). Only one pending pack is allowed per instance — a
        second call while a pack is still pending returns an error.

        Args:
            questions: List of question spec dicts.
        """
        # 1. Validate input — never raise exceptions; return an error
        #    string instead (per codebase convention for tools).
        if questions is None or not isinstance(questions, list) or len(questions) == 0:
            return (
                "ERROR: Provide `questions` (a non-empty list of question "
                "spec dicts, each with at minimum a `text` field)."
            )

        # 2. Store pack via QuestionManager. Returns ``None`` when an
        #    existing pack is still pending (F8/F11) — surface a clear
        #    error string so the agent can self-correct instead of
        #    waiting on a never-delivered pack.
        try:
            pack = manager._question_manager.set_question_pack(
                current_instance_id, questions
            )
        except Exception as e:
            return f"ERROR: Failed to store question pack: {e}"

        if pack is None:
            return (
                "Already have a pending question pack for this instance. "
                "Wait for answers before asking more."
            )

        # 3. Emit SSE best-effort BEFORE setting the pause flag. The
        #    conditional post-tools edge routes the graph to
        #    ``question_pause_node``, which sets the deferred-pause
        #    marker; the actual ``pause_instance_cascade`` runs from the
        #    post-graph completion path AFTER ``_graph_tasks`` is popped.
        #    The graph task is no longer running the question tool by the
        #    time the cascade fires, so any post-tool SSE code on the
        #    tool-call side is moot — this synchronous emit is still the
        #    most reliable place (F3) so the frontend sees the
        #    ``question_pack`` event before the user's interaction
        #    surface changes (status_change → PAUSED).
        await _emit_pending_pack(live_event_hub, current_instance_id, pack)

        # 4. Set pause flag — read by ``create_post_tools_router`` in
        #    ``daemon.graph`` to route to ``question_pause_node`` after
        #    the tools node finishes.
        try:
            manager.set_question_pause_requested(current_instance_id)
        except Exception as e:
            # Flag-setting failure is non-fatal — the question is
            # stored, the SSE event fired, and the user can still
            # answer; we just won't auto-pause. Surface as a warning
            # rather than crashing the tool.
            logger.warning(
                f"set_question_pause_requested failed for instance "
                f"{current_instance_id}: {e}"
            )

        # 5. Return a string that ECHOES question text (F7 compaction
        #    safety). After compaction, the AIMessage that issued the
        #    tool call may be lost; echoing Q text in the tool result
        #    lets the LLM correlate Q↔A from later context alone. The
        #    Phase 2 answer message ALSO includes Q text (defense in
        #    depth).
        q_summary = " | ".join(
            f"Q{i + 1}: {q.text}" for i, q in enumerate(pack.questions)
        )
        return (
            f"Asked the user: {q_summary}. "
            "The instance will pause until the user answers."
        )

    ask_questions._full_doc_ = """\
Ask the user one or more structured questions; pause the instance
until they answer.

Lifecycle:
  1. The tool stores a :class:`QuestionPack` via
     ``manager._question_manager.set_question_pack``.
  2. The tool emits a ``question_pack`` SSE event with
     ``status="pending"`` so the frontend renders the question UI.
  3. The tool sets the pause flag — the conditional post-tools edge in
     ``daemon.graph`` then routes the graph to
     ``question_pause_node``, which sets the deferred-pause marker
     (C2 fix — ``pause_instance_cascade`` runs from the post-graph
     completion path) and clears the flag in its ``finally`` block.
  4. The user submits answers via
     ``POST /api/instances/{id}/answer`` (the Phase 2 answer API). The
     endpoint stores the answers, emits a ``question_pack`` SSE event
     with ``status="answered"``, and resumes the instance with a
     HumanMessage containing the Q↔A pairs.

Input shape::

    ask_questions(questions=[
        {
            "id": "approach",          # optional — auto-generated when missing
            "text": "Approach A or B?",
            "options": ["Approach A", "Approach B"],   # optional
            "allow_custom": True,      # default True
            "required": True,          # default True
        },
        {"text": "What's the deadline?"},   # id/text only — open-ended
    ])

Args:
    questions: Non-empty list of question spec dicts. Each spec accepts
        ``id`` (str, optional), ``text`` (str, required), ``options``
        (list[str], optional), ``allow_custom`` (bool, default
        ``True``), and ``required`` (bool, default ``True``).

Returns:
    A confirmation string that echoes the question text (for compaction
    safety) and notes the instance is paused, or an ``ERROR:`` string
    on validation failure or duplicate-pending rejection. The tool
    never raises exceptions.

Edge cases:
  * Empty / missing ``questions`` → ``ERROR: ...`` returned.
  * Second ``ask_questions`` call while a pack is still ``pending`` →
    ``"Already have a pending question pack for this instance. Wait
    for answers before asking more."`` — at most one pending pack per
    instance.
  * Once answered (Phase 2), the next ``ask_questions`` call replaces the
    answered pack with a fresh pending pack.
"""

    return [ask_questions]
