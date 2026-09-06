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


# =============================================================================
# Input validation + normalization (tool-boundary hardening)
# =============================================================================


def validate_and_normalize_questions(
    questions: Any,
) -> tuple[list[dict] | None, str | None]:
    """Validate an ``ask_questions`` payload and normalize it to the MAIN format.

    Pure function — no I/O, no manager access, never raises. Runs BEFORE
    any store / SSE / pause side effect so a validation failure provably
    has zero side effects.

    Returns:
        ``(normalized_questions, None)`` on success, or ``(None, problem)``
        where ``problem`` is the exact field path of the FIRST validation
        failure in deterministic order:

        * questions are iterated in list order;
        * within a question, fields are checked in the fixed order
          ``id → text → options → allow_custom → required``;
        * within ``options``, elements are checked in list order; within
          a label-object, ``label`` before ``description``.

    Validation rules:
        * ``questions``: required non-empty list of dicts.
        * ``text``: required non-empty string. (Whitespace-only strings
          pass — deliberately consistent with QuestionManager's
          ``not text`` falsy check.)
        * ``id``: optional. Absent or explicit ``None`` → omitted from
          the normalized dict (QuestionManager auto-generates a UUID4 —
          its existing default). Present and non-None: must be a
          non-empty string (whitespace-only also rejected). Within a
          single pack, two questions MAY NOT share the same explicit
          id — collision is a validation failure pointing at the
          later index.
        * ``options``: optional list; absent → normalized to ``[]``.
          MIXED lists (some plain strings, some label-objects) are
          accepted — RATIFIED CHOICE: each element is validated and
          normalized independently. Per element:
            - non-empty ``str`` → kept as-is;
            - ``dict`` → requires non-empty string ``label`` (becomes
              the option string; whitespace-only label is rejected —
              "missing or empty" semantic); optional string
              ``description`` is
              carried through as ``option_descriptions[label]``
              metadata; empty descriptions are dropped; duplicate
              labels (including two entries normalizing to the same
              string) with differing descriptions → LAST WRITER WINS
              (only one ``option_descriptions`` entry survives);
            - anything else (int, list, ...) → validation failure.
        * ``allow_custom`` / ``required``: optional; must be ``bool``
          when present (explicit ``None`` included — a silent
          ``bool(None)`` coercion would flip the True default).
        * Unknown extra keys on question dicts and label-objects are
          DROPPED (ratified choice — the normalized dict only carries
          the known keys, so unknown keys cannot leak into storage).
    """
    if not isinstance(questions, list) or len(questions) == 0:
        return None, "questions: must be a non-empty list of question dicts"

    normalized_questions: list[dict] = []
    # Track explicit ids so we can reject collisions within a single pack
    # (W3). Auto-generated UUIDs (omitted / None ids) cannot collide, so
    # only present-and-non-None ids are tracked.
    seen_ids: dict[str, int] = {}
    for q_index, q in enumerate(questions):
        if not isinstance(q, dict):
            return None, f"questions[{q_index}]: must be a dict"

        # -- id ----------------------------------------------------------
        raw_id = q.get("id")
        if raw_id is not None:
            if not isinstance(raw_id, str) or not raw_id.strip():
                return None, (
                    f"questions[{q_index}].id: must be a non-empty "
                    f"string when present"
                )
            if raw_id in seen_ids:
                return None, (
                    f"questions[{q_index}].id: duplicate of "
                    f"questions[{seen_ids[raw_id]}].id"
                )
            seen_ids[raw_id] = q_index

        # -- text --------------------------------------------------------
        raw_text = q.get("text")
        if raw_text is None or raw_text == "":
            return None, f"questions[{q_index}].text: missing or empty"
        if not isinstance(raw_text, str):
            return None, f"questions[{q_index}].text: must be a string"

        # -- options -----------------------------------------------------
        raw_options = q.get("options")
        option_strings: list[str] = []
        option_descriptions: dict[str, str] = {}
        if raw_options is not None:
            if not isinstance(raw_options, list):
                return None, (
                    f"questions[{q_index}].options: must be a list of "
                    f"strings or {{label, description}} objects"
                )
            for opt_index, opt in enumerate(raw_options):
                if isinstance(opt, str):
                    if not opt:
                        return None, (
                            f"questions[{q_index}].options[{opt_index}]: "
                            f"must be a non-empty string"
                        )
                    option_strings.append(opt)
                elif isinstance(opt, dict):
                    label = opt.get("label")
                    if label is None or (
                        isinstance(label, str) and not label.strip()
                    ):
                        return None, (
                            f"questions[{q_index}].options[{opt_index}]"
                            f".label: missing or empty"
                        )
                    if not isinstance(label, str):
                        return None, (
                            f"questions[{q_index}].options[{opt_index}]"
                            f".label: must be a string"
                        )
                    description = opt.get("description")
                    if description is not None and not isinstance(description, str):
                        return None, (
                            f"questions[{q_index}].options[{opt_index}]"
                            f".description: must be a string"
                        )
                    option_strings.append(label)
                    if description:
                        option_descriptions[label] = description
                else:
                    return None, (
                        f"questions[{q_index}].options[{opt_index}]: must "
                        f'be a non-empty string or a {{"label", '
                        f'"description"}} object, got {type(opt).__name__}'
                    )

        # -- allow_custom / required (must be bool when present) ---------
        for flag in ("allow_custom", "required"):
            if flag in q and not isinstance(q[flag], bool):
                return None, f"questions[{q_index}].{flag}: must be a boolean"

        # -- normalized entry (unknown keys dropped by construction) -----
        entry: dict = {"text": raw_text, "options": option_strings}
        if raw_id is not None:
            entry["id"] = raw_id
        if "allow_custom" in q:
            entry["allow_custom"] = q["allow_custom"]
        if "required" in q:
            entry["required"] = q["required"]
        if option_descriptions:
            entry["option_descriptions"] = option_descriptions
        normalized_questions.append(entry)

    return normalized_questions, None


def _format_validation_error(problem: str) -> str:
    """Build the deterministic, actionable error hint for a bad payload.

    States the exact field path of the FIRST problem, then the
    MAIN-format spec and ONE minimal correct example so the calling
    agent can immediately re-ask correctly.
    """
    return (
        f"ERROR: Invalid ask_questions payload — {problem}.\n\n"
        "Expected MAIN format (options as plain strings):\n"
        "  questions: non-empty list of dicts; each dict accepts\n"
        "    - text (string, REQUIRED, non-empty)\n"
        "    - id (string, optional, non-empty when present; auto-generated when omitted)\n"
        "    - options (list, optional, defaults to [] when omitted; each element is\n"
        '      EITHER a non-empty string OR an object {"label": string (required,\n'
        "      non-empty), \"description\": string (optional)})\n"
        "    - allow_custom (boolean, optional, default true)\n"
        "    - required (boolean, optional, default true)\n"
        "  Mixed lists (some strings, some {label, description} objects) are\n"
        "  accepted; label-objects are normalized to their label string before\n"
        "  storage. Unknown extra keys are dropped.\n\n"
        "Minimal correct example:\n"
        '  ask_questions(questions=[{"text": "Proceed?", "options": ["Yes", "No"]}])'
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
          - ``options`` (list, optional) — pre-canned answer options for
            the frontend to render as buttons / a dropdown. TWO element
            formats are accepted (MAY be mixed within one list):
              * MAIN format — a plain non-empty string, or
              * a ``{"label": str (required, non-empty),
                "description": str (optional)}`` object, which is
                normalized to its label string before storage; the
                description is carried through as optional
                ``option_descriptions`` display metadata.
          - ``allow_custom`` (bool, default ``True``) — whether the user
            may supply a free-text answer in addition to the options.
          - ``required`` (bool, default ``True``) — whether the question
            must be answered.
          - ``id`` (str, optional) — caller-supplied identifier; when
            missing the manager auto-generates a UUID4.

        Validation is strict at the tool body: on any invalid field that
        reaches the tool body, the tool returns a deterministic ``ERROR:``
        hint naming the exact field path of the first problem (questions
        in order; per question ``id → text → options → allow_custom →
        required``) plus the expected format, and has ZERO side effects —
        nothing is stored, no SSE event is emitted, and the instance is
        NOT paused. Unknown extra keys on question dicts and option
        objects are dropped.

        Note: payloads rejected by the tool's pydantic args-schema layer
        BEFORE the body runs (e.g. ``questions="not-a-list"``) surface
        as a deterministic, side-effect-free error ToolMessage from the
        tool executor (no store, no SSE, no pause) — but that error
        carries the executor's wrapper text, not the field-path hint
        below. The two layers are defense-in-depth.

        The instance is paused after this call. The user submits answers
        through ``POST /api/instances/{id}/answer`` (the Phase 2 answer
        endpoint). Only one pending pack is allowed per instance — a
        second call while a pack is still pending returns an error.

        Args:
            questions: List of question spec dicts.
        """
        # 1. Validate + normalize the payload BEFORE any side effect
        #    (store / SSE / pause). On any validation failure the tool
        #    returns a deterministic actionable error hint and has ZERO
        #    side effects: no pack is persisted, no SSE event is emitted,
        #    and the instance is NOT paused.
        normalized_questions, problem = validate_and_normalize_questions(questions)
        if normalized_questions is None:
            return _format_validation_error(problem or "unknown validation failure")

        # 2. Store pack via QuestionManager. Returns ``None`` when an
        #    existing pack is still pending (F8/F11) — surface a clear
        #    error string so the agent can self-correct instead of
        #    waiting on a never-delivered pack.
        try:
            pack = manager._question_manager.set_question_pack(
                current_instance_id, normalized_questions
            )
        except Exception as e:
            # Surface as an error string AND log at WARNING so transport
            # / storage failures leave an audit trail (was silent).
            logger.warning(
                f"set_question_pack failed for instance "
                f"{current_instance_id} ({len(normalized_questions)} "
                f"questions): {e}"
            )
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

Input shape — TWO option formats are accepted and MAY be mixed within
one options list::

    ask_questions(questions=[
        {
            "id": "approach",          # optional — auto-generated when missing
            "text": "Approach A or B?",
            "options": ["Approach A", "Approach B"],   # optional — MAIN format
            "allow_custom": True,      # default True
            "required": True,          # default True
        },
        {
            "id": "headline",
            "text": "Approve the headline recommendation?",
            "options": [               # SECONDARY format — label-objects
                {"label": "Approve — start M1", "description": "Proceed with the phased plan M0–M4"},
                {"label": "Not yet"},
            ],
        },
        {"text": "What's the deadline?"},   # id/text only — open-ended
    ])

Validation rules (enforced at the tool boundary, before any side
effect):
  * ``questions`` must be a non-empty list of dicts; ``text`` must be a
    non-empty string; ``id``, when present (and not ``None``), must be a
    non-empty string; ``allow_custom`` / ``required`` must be booleans
    when present; ``options``, when present, must be a list whose every
    element is a non-empty string OR a valid label-object (``label``
    required non-empty string; ``description`` optional string).
  * MIXED lists (some strings, some label-objects) are accepted —
    ratified choice: each element is validated and normalized
    independently.
  * Label-objects are normalized to their label string before the pack
    is stored or emitted; the description is carried through as
    optional ``option_descriptions`` display metadata on the pack
    payload (additive — the frontend-facing ``options`` contract stays
    ``list[str]``). Duplicate option labels (including two entries
    normalizing to the same string) with differing descriptions → last
    writer wins: only one ``option_descriptions`` entry survives;
    duplicates are accepted, NOT rejected.
  * Unknown extra keys on question dicts and option objects are
    dropped.

Failure semantics: for any payload that REACHES THE TOOL BODY and fails
validation, the tool returns a deterministic ``ERROR:`` hint naming the
exact field path of the FIRST problem (questions in list order; within
a question the fixed field order ``id → text → options → allow_custom →
required``; within ``options`` the element order, ``label`` before
``description``), plus the MAIN-format spec and one minimal correct
example. The failure has ZERO side effects: no pack is persisted or
registered pending, no SSE event is emitted, and the instance is NOT
paused.

Payloads rejected by the tool's pydantic args-schema layer BEFORE the
body runs (e.g. ``questions="not-a-list"``) surface as a deterministic,
side-effect-free error ToolMessage from the tool executor — they do NOT
carry the field-path hint above, since validation never reached the
body. The args-schema layer is a defense boundary; the body's
field-path hint is the canonical message for in-body failures.

Args:
    questions: Non-empty list of question spec dicts. Each spec accepts
        ``id`` (str, optional), ``text`` (str, required), ``options``
        (list of non-empty strings and/or {label, description} objects,
        optional; mixed lists accepted), ``allow_custom`` (bool, default
        ``True``), and ``required`` (bool, default ``True``).

Returns:
    A confirmation string that echoes the question text (for compaction
    safety) and notes the instance is paused, an ``ERROR:`` string on
    in-body validation failure or duplicate-pending rejection, or — for
    payloads rejected by the args-schema layer BEFORE the body runs — a
    deterministic, side-effect-free error ToolMessage emitted by the
    tool executor. The tool itself never raises exceptions.

Edge cases:
  * Empty / missing / malformed ``questions`` → ``ERROR: ...`` hint
    with the first failing field path; zero side effects.
  * Second ``ask_questions`` call while a pack is still ``pending`` →
    ``"Already have a pending question pack for this instance. Wait
    for answers before asking more."`` — at most one pending pack per
    instance.
  * Once answered (Phase 2), the next ``ask_questions`` call replaces the
    answered pack with a fresh pending pack.
"""

    return [ask_questions]
