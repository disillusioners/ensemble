"""Tests for ``ask_questions`` input-format validation and normalization.

Covers the tool-boundary hardening in ``daemon.tools.question_tools``:

  1. **MAIN format** (existing contract) — ``options`` as plain strings
     still works end-to-end: pack persisted, pause flag set.
  2. **SECONDARY format** (new) — ``options`` as ``{"label", "description"}``
     objects are normalized to their label strings BEFORE the pack is
     stored or emitted; descriptions are carried through as additive
     ``option_descriptions`` metadata (frontend-facing ``options``
     contract stays ``list[str]``).
  3. **MIXED lists** — each element is validated/normalized
     independently (ratified choice).
  4. **Failure semantics** — every validation failure returns a
     deterministic ``ERROR:`` hint naming the exact field path of the
     FIRST problem and has ZERO side effects: no pack persisted, no SSE
     emitted, instance NOT paused (pause flag untouched). Per the C2
     deferred-pause design, "paused" in unit terms = the tool flipped
     ``set_question_pause_requested`` (the actual
     ``pause_instance_cascade`` only runs from the post-graph completion
     path when the flag is set — see
     ``tests/unit/test_question_deferred_pause_callback.py``).
  5. **Pure helper** — ``validate_and_normalize_questions`` is
     unit-tested directly for exact field-path assertions.

Mirrors the fixture pattern of ``tests/test_question_tools.py``:
``MagicMock`` manager with a real ``QuestionManager`` at
``_question_manager`` and dict-backed pause-flag stubs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from daemon.services.question_manager import QuestionManager, pack_to_dict
from daemon.tools.question_tools import validate_and_normalize_questions

INSTANCE_ID = "test-instance-id"

# VERBATIM secondary-format payload shape from the incident report.
SECONDARY_PACK = [
    {
        "id": "headline",
        "text": "Approve the headline recommendation?",
        "options": [
            {"label": "Approve — start M1", "description": "Proceed with the phased plan M0–M4"},
            {"label": "Approve with changes", "description": "I want to adjust part of the approach before starting"},
            {"label": "Not yet", "description": "Discussed more before deciding"},
        ],
    }
]


# VERBATIM observed failing payload from the dispatch (Zai/GLM wraps each
# question's ``options`` as the canonical single-key ``{"item": [...]}``).
# Pre-fix this returned ``ERROR: Invalid ask_questions payload —
# questions[0].options: must be a list of strings or {label, description}
# objects.`` from the body validator; the fix unwraps it and normalizes
# the inner list with descriptions intact.
OBSERVED_FAILING_PAYLOAD = [
    {
        "id": "zombie",
        "text": "Authorize the zombie-ACTIVE finalize (b639aaf8) to release slot 1?",
        "options": {
            "item": [
                {
                    "description": (
                        "One guarded pass: finalize b639aaf8 (done/completed) then lock "
                        "releases; 4/5 free. Also read-only census of the two earlier "
                        "suspected wedges (6588/94cd3abd 06:44, 6600/14eb052d 06:54) to "
                        "confirm whether they self-resolved or are silently accumulating."
                    ),
                    "label": "Finalize zombie + census (recommended)",
                },
                {
                    "description": "Only the finalize. Skip the census check.",
                    "label": "Finalize zombie only",
                },
                {
                    "description": (
                        "Leave b639aaf8 active and slot 1 held. No urgency (3/5 free, "
                        "wedge class being fixed by code). Revisit during a dedicated "
                        "f2-backstop investigation commission later."
                    ),
                    "label": "Defer to f2 investigation",
                },
            ]
        },
    }
]


# =============================================================================
# Helpers (mirrors tests/test_question_tools.py)
# =============================================================================


def _make_manager() -> MagicMock:
    """Mock InstanceManager with a real QuestionManager + pause-flag dict stubs."""
    manager = MagicMock()
    manager._question_manager = QuestionManager()

    flag_state: dict[str, bool] = {}

    def _set(instance_id: str) -> None:
        flag_state[instance_id] = True

    def _is_set(instance_id: str) -> bool:
        return flag_state.get(instance_id, False)

    def _clear(instance_id: str) -> None:
        flag_state.pop(instance_id, None)

    manager.set_question_pause_requested.side_effect = _set
    manager.is_question_pause_requested.side_effect = _is_set
    manager.clear_question_pause_requested.side_effect = _clear

    return manager


def _build_tool(manager: MagicMock | None = None, live_event_hub=None):
    from daemon.tools.question_tools import create_question_tools

    if manager is None:
        manager = _make_manager()
    if live_event_hub is None:
        live_event_hub = MagicMock()
        live_event_hub.stream_question_pack = AsyncMock(return_value=MagicMock())

    return (
        create_question_tools(
            manager=manager,
            current_instance_id=INSTANCE_ID,
            live_event_hub=live_event_hub,
        )[0],
        manager,
        live_event_hub,
    )


def _assert_zero_side_effects(manager: MagicMock, live_event_hub, result: str, path: str) -> None:
    """Assert the failure contract: deterministic hint + ZERO side effects."""
    assert isinstance(result, str)
    assert result.startswith("ERROR:")
    # Exact field path of the FIRST problem.
    assert path in result, f"expected path {path!r} in hint: {result!r}"
    # Actionable hint: MAIN-format spec + minimal correct example.
    assert "Expected MAIN format" in result
    assert "Minimal correct example" in result
    # No pack persisted / registered pending.
    assert manager._question_manager.get_question_pack(INSTANCE_ID) is None
    # Instance NOT paused (flag never flipped → no cascade post-graph).
    manager.set_question_pause_requested.assert_not_called()
    assert manager.is_question_pause_requested(INSTANCE_ID) is False
    # No SSE event emitted.
    live_event_hub.stream_question_pack.assert_not_awaited()


# =============================================================================
# (a) Valid MAIN format
# =============================================================================


class TestValidMainFormat:
    """Plain-string options: unchanged contract, pack persisted, pause flag set."""

    async def test_main_format_pack_persisted_and_pause_requested(self):
        tool, manager, hub = _build_tool()

        result = await tool.coroutine(
            questions=[{"id": "approach", "text": "Approach A or B?", "options": ["A", "B"]}],
        )

        assert isinstance(result, str)
        assert "Approach A or B?" in result  # echo contract

        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored is not None
        assert stored.status == "pending"
        assert stored.questions[0].options == ["A", "B"]
        assert stored.questions[0].option_descriptions == {}

        manager.set_question_pause_requested.assert_called_once_with(INSTANCE_ID)
        assert manager.is_question_pause_requested(INSTANCE_ID) is True
        hub.stream_question_pack.assert_awaited_once()

    async def test_main_format_sse_payload_has_string_options(self):
        tool, manager, hub = _build_tool()

        await tool.coroutine(
            questions=[{"text": "Pick one", "options": ["X", "Y"]}],
        )

        payload = hub.stream_question_pack.await_args.args[1]
        assert payload["questions"][0]["options"] == ["X", "Y"]


# =============================================================================
# (b) Valid SECONDARY format (label-objects)
# =============================================================================


class TestValidSecondaryFormat:
    """Label-object options normalize to strings before store/SSE; descriptions carried as metadata."""

    async def test_secondary_pack_normalized_persisted_and_pauses(self):
        tool, manager, hub = _build_tool()

        result = await tool.coroutine(questions=SECONDARY_PACK)

        assert isinstance(result, str)
        assert "Approve the headline recommendation?" in result

        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored is not None
        assert stored.status == "pending"
        q = stored.questions[0]
        # Normalization: labels become the option strings — NO objects leak.
        assert q.options == [
            "Approve — start M1",
            "Approve with changes",
            "Not yet",
        ]
        assert all(isinstance(o, str) for o in q.options)
        # Descriptions carried through as metadata, keyed by label.
        assert q.option_descriptions == {
            "Approve — start M1": "Proceed with the phased plan M0–M4",
            "Approve with changes": "I want to adjust part of the approach before starting",
            "Not yet": "Discussed more before deciding",
        }

        manager.set_question_pause_requested.assert_called_once_with(INSTANCE_ID)
        assert manager.is_question_pause_requested(INSTANCE_ID) is True

    async def test_secondary_pack_sse_payload_strings_plus_metadata(self):
        tool, manager, hub = _build_tool()

        await tool.coroutine(questions=SECONDARY_PACK)

        payload = hub.stream_question_pack.await_args.args[1]
        q = payload["questions"][0]
        # FE contract stays strings.
        assert q["options"] == ["Approve — start M1", "Approve with changes", "Not yet"]
        # Descriptions ride along as additive metadata.
        assert q["option_descriptions"]["Not yet"] == "Discussed more before deciding"
        # pack_to_dict serializer agrees with the SSE payload.
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert pack_to_dict(stored)["questions"][0]["options"] == q["options"]

    async def test_label_object_without_description_omits_metadata(self):
        tool, manager, _hub = _build_tool()

        result = await tool.coroutine(
            questions=[{"text": "t", "options": [{"label": "Only label"}]}],
        )

        assert isinstance(result, str)
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored.questions[0].options == ["Only label"]
        assert stored.questions[0].option_descriptions == {}


# =============================================================================
# (c) MIXED options list
# =============================================================================


class TestMixedOptionsList:
    """Ratified: each element of a mixed list is normalized independently."""

    async def test_mixed_list_per_element_normalization(self):
        tool, manager, _hub = _build_tool()

        result = await tool.coroutine(
            questions=[
                {
                    "text": "How to proceed?",
                    "options": [
                        "Plain string option",
                        {"label": "Label with desc", "description": "extra detail"},
                        {"label": "Label no desc"},
                    ],
                }
            ],
        )

        assert isinstance(result, str)
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        q = stored.questions[0]
        assert q.options == [
            "Plain string option",
            "Label with desc",
            "Label no desc",
        ]
        assert q.option_descriptions == {"Label with desc": "extra detail"}
        assert "How to proceed?" in result

    async def test_mixed_list_persisted_options_are_all_strings(self):
        tool, manager, hub = _build_tool()

        await tool.coroutine(
            questions=[{"text": "t", "options": ["a", {"label": "b", "description": "d"}]}],
        )

        payload = hub.stream_question_pack.await_args.args[1]
        assert payload["questions"][0]["options"] == ["a", "b"]
        assert all(isinstance(o, str) for o in manager._question_manager.get_question_pack(INSTANCE_ID).questions[0].options)


# =============================================================================
# (d) Failure variants — deterministic path + ZERO side effects
# =============================================================================


class TestValidationFailures:
    """Every invalid payload: exact first-problem path, no store, no SSE, no pause."""

    async def test_questions_none(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=None)
        _assert_zero_side_effects(manager, hub, result, "questions:")

    async def test_questions_empty_list(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[])
        _assert_zero_side_effects(manager, hub, result, "questions:")

    async def test_questions_not_a_list(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions="not a list")
        _assert_zero_side_effects(manager, hub, result, "questions:")

    async def test_question_element_not_a_dict(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "ok"}, "oops"])
        _assert_zero_side_effects(manager, hub, result, "questions[1]: must be a dict")

    async def test_text_missing(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"id": "x"}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].text: missing or empty")

    async def test_text_empty_string(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": ""}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].text: missing or empty")

    async def test_text_not_a_string(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": 42}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].text: must be a string")

    async def test_options_not_a_list(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "options": "Yes"}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].options: must be a list")

    async def test_option_element_bad_type(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "options": ["ok", 7]}])
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].options[1]:"
        )

    async def test_label_object_missing_label(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": [{"description": "d"}]}],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].options[0].label: missing or empty"
        )

    async def test_label_object_empty_label(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": [{"label": ""}]}],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].options[0].label: missing or empty"
        )

    async def test_label_object_non_string_label(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": [{"label": 42}]}],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].options[0].label: must be a string"
        )

    async def test_label_object_non_string_description(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": [{"label": "L", "description": 9}]}],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].options[0].description: must be a string"
        )

    async def test_allow_custom_not_a_bool(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "allow_custom": "yes"}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].allow_custom: must be a boolean")

    async def test_allow_custom_none_rejected(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "allow_custom": None}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].allow_custom: must be a boolean")

    async def test_required_not_a_bool(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "required": 1}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].required: must be a boolean")

    async def test_required_none_rejected(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "required": None}])
        _assert_zero_side_effects(manager, hub, result, "questions[0].required: must be a boolean")

    async def test_id_non_string(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "id": 42}])
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].id: must be a non-empty string"
        )

    async def test_id_empty_string(self):
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"text": "t", "id": ""}])
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].id: must be a non-empty string"
        )

    async def test_first_problem_is_deterministic_across_questions(self):
        """Q1 has options[1] bad AND Q2 has empty text → Q1's option path wins."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {"text": "fine"},
                {"text": "t", "options": ["a", {"label": ""}]},
                {"text": ""},
            ],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[1].options[1].label: missing or empty"
        )
        assert "questions[2].text" not in result

    async def test_first_problem_is_deterministic_within_a_question(self):
        """Field order is id → text → options → allow_custom → required."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(questions=[{"id": "", "text": ""}])
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].id: must be a non-empty string"
        )
        assert "questions[0].text" not in result

    async def test_second_question_path_reported(self):
        """Sanity on multi-question path indexing (requirement example shape)."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {"text": "ok", "options": ["a", "b", "c"]},
                {"text": "ok", "options": ["a", "b", {"description": "no label"}]},
            ],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[1].options[2].label: missing or empty"
        )

    # -- W3: duplicate question ids -----------------------------------------

    async def test_duplicate_explicit_ids_rejected(self):
        """Two questions sharing the same explicit id → deterministic path.

        Error format: ``questions[{i}].id: duplicate of questions[{j}].id``
        where i = current (later) index, j = earlier index. Validation
        runs BEFORE any side effect.
        """
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {"id": "approach", "text": "First"},
                {"id": "approach", "text": "Second"},
            ],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[1].id: duplicate of questions[0].id"
        )

    async def test_duplicate_ids_first_match_path_index(self):
        """Duplicate with more than two collisions: the FIRST earlier index wins.

        Q0 and Q2 both have id="dup"; Q2's error must reference Q0 (the
        earliest match), not Q1.
        """
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {"id": "dup", "text": "Q0"},
                {"id": "other", "text": "Q1"},
                {"id": "dup", "text": "Q2"},
            ],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[2].id: duplicate of questions[0].id"
        )

    async def test_two_idless_questions_succeed(self):
        """Two id-less questions auto-gen UUIDs — no collision possible.

        RATIFIED CHOICE: only explicit ids participate in the
        duplicate check. Auto-generated UUIDs are random and cannot
        collide within a single pack.
        """
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {"text": "First"},
                {"text": "Second"},
            ],
        )
        # Success path — pack persisted, pause flag set.
        assert isinstance(result, str)
        assert "Q1: First" in result
        assert "Q2: Second" in result
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored is not None
        assert len(stored.questions) == 2
        # Both ids are UUIDs, not equal.
        ids = {q.id for q in stored.questions}
        assert len(ids) == 2
        manager.set_question_pause_requested.assert_called_once_with(INSTANCE_ID)
        hub.stream_question_pack.assert_awaited_once()

    async def test_duplicate_ids_pure_helper_path(self):
        """Duplicate check is enforced by ``validate_and_normalize_questions`` directly."""
        normalized, problem = validate_and_normalize_questions(
            [
                {"id": "x", "text": "T1"},
                {"id": "x", "text": "T2"},
            ]
        )
        assert normalized is None
        assert problem == "questions[1].id: duplicate of questions[0].id"

    # -- W4: whitespace-only gates ------------------------------------------

    async def test_whitespace_only_id_rejected(self):
        """Whitespace-only id fails the id gate — same wording as empty id."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"id": "   ", "text": "t"}],
        )
        _assert_zero_side_effects(
            manager, hub, result, "questions[0].id: must be a non-empty string"
        )

    async def test_whitespace_only_label_rejected(self):
        """Whitespace-only label-object label fails the label gate."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {"text": "t", "options": [{"label": "   ", "description": "d"}]},
            ],
        )
        _assert_zero_side_effects(
            manager, hub,
            result,
            "questions[0].options[0].label: missing or empty",
        )

    async def test_whitespace_only_label_pure_helper_path(self):
        """Whitespace-only label rejected at the pure-helper layer too."""
        normalized, problem = validate_and_normalize_questions(
            [{"text": "t", "options": [{"label": "   "}]}]
        )
        assert normalized is None
        assert problem == "questions[0].options[0].label: missing or empty"


# =============================================================================
# Pure helper — direct unit tests (exact normalization contract)
# =============================================================================


class TestValidateAndNormalizeHelper:
    """Direct tests for validate_and_normalize_questions (no tool, no manager)."""

    def test_main_format_passthrough(self):
        normalized, problem = validate_and_normalize_questions(
            [{"id": "a", "text": "T?", "options": ["x", "y"], "allow_custom": False}]
        )
        assert problem is None
        assert normalized == [
            {"text": "T?", "options": ["x", "y"], "id": "a", "allow_custom": False}
        ]

    def test_secondary_format_normalizes_to_labels_with_metadata(self):
        normalized, problem = validate_and_normalize_questions(SECONDARY_PACK)
        assert problem is None
        q = normalized[0]
        assert q["options"] == ["Approve — start M1", "Approve with changes", "Not yet"]
        assert set(q["option_descriptions"]) == {
            "Approve — start M1",
            "Approve with changes",
            "Not yet",
        }
        assert q["id"] == "headline"
        # allow_custom/required omitted → manager applies its True defaults.
        assert "allow_custom" not in q
        assert "required" not in q

    def test_mixed_list_normalized_per_element(self):
        normalized, problem = validate_and_normalize_questions(
            [{"text": "t", "options": ["a", {"label": "b", "description": "d"}, {"label": "c"}]}]
        )
        assert problem is None
        assert normalized[0]["options"] == ["a", "b", "c"]
        assert normalized[0]["option_descriptions"] == {"b": "d"}

    def test_unknown_keys_dropped(self):
        normalized, problem = validate_and_normalize_questions(
            [
                {
                    "text": "t",
                    "options": [{"label": "L", "description": "d", "icon": "🔥", "priority": 1}],
                    "surprise": {"nested": True},
                }
            ]
        )
        assert problem is None
        assert normalized[0] == {
            "text": "t",
            "options": ["L"],
            "option_descriptions": {"L": "d"},
        }
        assert "surprise" not in normalized[0]

    def test_missing_id_omitted_manager_auto_generates(self):
        """No id → key omitted; QuestionManager's existing UUID4 default applies."""
        normalized, problem = validate_and_normalize_questions([{"text": "t"}])
        assert problem is None
        assert "id" not in normalized[0]

    def test_explicit_none_id_treated_as_omitted(self):
        normalized, problem = validate_and_normalize_questions([{"text": "t", "id": None}])
        assert problem is None
        assert "id" not in normalized[0]

    def test_options_absent_normalized_to_empty_list(self):
        normalized, problem = validate_and_normalize_questions([{"text": "t"}])
        assert problem is None
        assert normalized[0]["options"] == []

    def test_explicit_bool_false_preserved(self):
        normalized, problem = validate_and_normalize_questions(
            [{"text": "t", "allow_custom": False, "required": False}]
        )
        assert problem is None
        assert normalized[0]["allow_custom"] is False
        assert normalized[0]["required"] is False

    def test_error_returns_none_with_exact_path(self):
        cases = [
            (None, "questions:"),
            ([], "questions:"),
            ("nope", "questions:"),
            (["not-a-dict"], "questions[0]: must be a dict"),
            ([{"text": ""}], "questions[0].text: missing or empty"),
            ([{"text": 1.5}], "questions[0].text: must be a string"),
            ([{"text": "t", "options": {}}], "questions[0].options: must be a list"),
            ([{"text": "t", "options": [None]}], "questions[0].options[0]:"),
            ([{"text": "t", "options": [{"label": None}]}], "questions[0].options[0].label:"),
            ([{"text": "t", "required": "yes"}], "questions[0].required: must be a boolean"),
        ]
        for payload, expected_path in cases:
            normalized, problem = validate_and_normalize_questions(payload)
            assert normalized is None, f"payload {payload!r} should fail"
            assert problem is not None and expected_path in problem

    def test_whitespace_only_text_passes(self):
        """Documented leniency: whitespace-only text passes (matches manager's falsy check)."""
        normalized, problem = validate_and_normalize_questions([{"text": "   "}])
        assert problem is None
        assert normalized[0]["text"] == "   "

    def test_never_raises_on_hostile_payloads(self):
        """Deeply weird shapes must return (None, path) — never raise."""
        hostile = [
            [{"text": "t", "options": [["nested"], {"label": ["also-nested"]}]}],
            [{"text": "t", "options": [3.14]}],
            [[[[["deep"]]]]],
            [{}],
        ]
        for payload in hostile:
            normalized, problem = validate_and_normalize_questions(payload)
            assert normalized is None
            assert isinstance(problem, str) and problem


# =============================================================================
# (e) Executor-layer schema rejection (W1b) — args schema is a defense layer
# =============================================================================


class TestExecutorLayerSchemaRejects:
    """Payloads rejected by the pydantic args-schema layer BEFORE the body
    runs surface as a deterministic, side-effect-free error ToolMessage
    from the tool executor (LangGraph ``ToolNode`` with
    ``handle_tool_errors=True``).

    This is the executor-layer pin: the args schema is a real defense
    boundary, NOT just a hint to LLM planners. Payloads that don't even
    satisfy the schema (e.g. ``questions="not-a-list"``) MUST produce
    ZERO side effects — no pack stored, no SSE event, no pause flag.
    Mirrors the ToolNode pattern in ``tests/unit/test_mcp_tool_timeout.py``
    (the only other repo site that invokes ``ToolNode`` directly).
    """

    async def test_args_schema_rejects_before_body_runs(self):
        """Bad payload: schema raises ValidationError pre-body; ToolNode
        surfaces a ToolMessage error with ZERO side effects.
        """
        import sys

        from langchain_core.messages import AIMessage, ToolMessage

        # Unmock langgraph so ToolNode + the internal runtime config
        # helpers are the real classes. ``tests/conftest.py`` mocks
        # langgraph by default; without this, the integration test would
        # hit ``MagicMock`` placeholders and never exercise the real
        # ToolNode path. Mirrors ``tests/unit/test_mcp_tool_timeout.py``
        # (the only other repo site that invokes ToolNode directly).
        _langgraph_mocks = {}
        for _key in list(sys.modules):
            if _key.startswith("langgraph"):
                _langgraph_mocks[_key] = sys.modules.pop(_key)
        try:
            from langgraph._internal._runnable import CONF, CONFIG_KEY_RUNTIME
            from langgraph.prebuilt import ToolNode
            from langgraph.runtime import Runtime

            tool, manager, hub = _build_tool()

            tool_node = ToolNode([tool], handle_tool_errors=True)

            # ``questions="not-a-list"`` violates the pydantic args_schema
            # (questions must be ``list[dict] | None``), so the schema
            # rejects BEFORE the body runs.
            ai_msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_questions",
                        "args": {"questions": "not-a-list"},
                        "id": "call_executor_reject",
                        "type": "tool_call",
                    }
                ],
            )

            runtime = Runtime()
            result = await tool_node.ainvoke(
                {"messages": [ai_msg]},
                config={CONF: {CONFIG_KEY_RUNTIME: runtime}},
            )

            # 1. Error result returned as a ToolMessage.
            out_messages = result["messages"]
            assert len(out_messages) == 1, (
                f"expected exactly one ToolMessage, got {len(out_messages)}"
            )
            msg = out_messages[0]
            assert isinstance(msg, ToolMessage)
            assert msg.status == "error"
            # The executor wraps the ValidationError — must mention the tool
            # name and the field name (schema-level rejection marker).
            assert "ask_questions" in msg.content
            assert "questions" in msg.content
            # Must NOT carry the in-body field-path hint ("ERROR: Invalid
            # ask_questions payload") — that comes only from the body.
            assert "ERROR: Invalid ask_questions payload" not in msg.content
        finally:
            # Restore the conftest mocks so the rest of the suite keeps
            # using the (fast) langgraph MagicMock placeholders.
            for _key, _mod in _langgraph_mocks.items():
                sys.modules[_key] = _mod

        # 2. ZERO side effects — pack not stored, pause never set,
        #    SSE never awaited. Reuse the same zero-side-effect contract
        #    the in-body tests verify. (Asserted after the mocks are
        #    restored — manager/hub references are unaffected.)
        assert manager._question_manager.get_question_pack(INSTANCE_ID) is None
        manager.set_question_pause_requested.assert_not_called()
        assert manager.is_question_pause_requested(INSTANCE_ID) is False
        hub.stream_question_pack.assert_not_awaited()


# =============================================================================
# (f) Zai/GLM ``{"item": [...]}`` array-wrap convenience — acceptance + rejection
# =============================================================================


class TestItemWrappedPayloads:
    """Some LLM providers (Zai/GLM function-calling) emit array-typed
    parameters wrapped as the canonical single-key ``{"item": [...]}`` dict
    instead of a bare JSON array. The body validator unwraps BOTH the
    ``questions`` parameter and each question's ``options`` BEFORE per-element
    rules run; non-canonical shapes fall through to the existing rules (reject
    with the field-path hint at the body layer, or fail-closed at the
    pydantic args-schema layer if it reaches the executor first).
    """

    # -- Acceptance: wrapped options -----------------------------------------

    async def test_wrapped_options_accepted_and_stored_identically_to_bare_list(
        self,
    ):
        """Wrapped ``options`` must produce stored state IDENTICAL to the
        bare-list equivalent (requirement: ``options`` + ``option_descriptions``
        equality). SSE payload equality too."""
        tool_wrapped, manager_wrapped, hub_wrapped = _build_tool()
        wrapped_result = await tool_wrapped.coroutine(
            questions=[
                {
                    "id": "approach",
                    "text": "Approach?",
                    "options": {"item": ["A", "B", "C"]},
                }
            ],
        )
        assert isinstance(wrapped_result, str)
        stored_wrapped = manager_wrapped._question_manager.get_question_pack(INSTANCE_ID)
        payload_wrapped = hub_wrapped.stream_question_pack.await_args.args[1]

        tool_bare, manager_bare, hub_bare = _build_tool()
        bare_result = await tool_bare.coroutine(
            questions=[
                {
                    "id": "approach",
                    "text": "Approach?",
                    "options": ["A", "B", "C"],
                }
            ],
        )
        stored_bare = manager_bare._question_manager.get_question_pack(INSTANCE_ID)
        payload_bare = hub_bare.stream_question_pack.await_args.args[1]

        # Stored options + option_descriptions MUST be equal.
        assert stored_wrapped.questions[0].options == stored_bare.questions[0].options == [
            "A",
            "B",
            "C",
        ]
        assert (
            stored_wrapped.questions[0].option_descriptions
            == stored_bare.questions[0].option_descriptions
            == {}
        )
        # SSE payload equality — the user-facing contract is unchanged.
        assert (
            payload_wrapped["questions"][0]["options"]
            == payload_bare["questions"][0]["options"]
        )
        # Both pause + emit side effects equally.
        manager_wrapped.set_question_pause_requested.assert_called_once_with(INSTANCE_ID)
        hub_wrapped.stream_question_pack.assert_awaited_once()

    async def test_wrapped_options_with_mixed_elements_validates_and_stores(self):
        """Mixed strings + label-objects inside an UNWRAPPED list (after the
        canonical wrap is removed) still validate and store with descriptions
        carried through as ``option_descriptions`` metadata."""
        tool, manager, _hub = _build_tool()
        result = await tool.coroutine(
            questions=[
                {
                    "text": "How to proceed?",
                    "options": {
                        "item": [
                            "Plain string option",
                            {"label": "Label with desc", "description": "extra detail"},
                            {"label": "Label no desc"},
                        ]
                    },
                }
            ],
        )

        assert isinstance(result, str)
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored.questions[0].options == [
            "Plain string option",
            "Label with desc",
            "Label no desc",
        ]
        assert stored.questions[0].option_descriptions == {"Label with desc": "extra detail"}

    async def test_wrapped_options_empty_list_accepted(self):
        """Empty wrapped list ``{"item": []}`` is accepted — empty options
        are valid (MAIN-format empty list is also accepted per requirement 3)."""
        tool, manager, _hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"id": "open", "text": "t", "options": {"item": []}}],
        )
        assert isinstance(result, str)
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored.questions[0].options == []
        assert stored.questions[0].option_descriptions == {}

    # -- Acceptance: wrapped questions (top-level) ----------------------------

    async def test_wrapped_questions_accepted_original_failing_payload(self):
        """VERBATIM observed failing payload: the dispatcher's report fixture.
        Pre-fix this raised ``ERROR: Invalid ask_questions payload — questions[0]
        .options: must be a list of strings or {label, description} objects.``
        Post-fix the pack is stored with all three labels + descriptions."""
        tool, manager, hub = _build_tool()

        result = await tool.coroutine(questions=OBSERVED_FAILING_PAYLOAD)

        assert isinstance(result, str)
        assert "Authorize the zombie-ACTIVE finalize (b639aaf8)" in result

        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored is not None
        assert stored.status == "pending"
        q = stored.questions[0]
        assert q.id == "zombie"
        assert q.options == [
            "Finalize zombie + census (recommended)",
            "Finalize zombie only",
            "Defer to f2 investigation",
        ]
        assert set(q.option_descriptions.keys()) == set(q.options)
        # Descriptions round-trip (lengths match the original).
        assert q.option_descriptions["Finalize zombie + census (recommended)"].startswith(
            "One guarded pass"
        )
        assert q.option_descriptions["Finalize zombie only"] == (
            "Only the finalize. Skip the census check."
        )

        manager.set_question_pause_requested.assert_called_once_with(INSTANCE_ID)
        assert manager.is_question_pause_requested(INSTANCE_ID) is True
        hub.stream_question_pack.assert_awaited_once()

    def test_wrapped_questions_pure_helper(self):
        """Direct call to ``validate_and_normalize_questions`` with a wrapped
        top-level questions dict — unwrap + per-question rules."""
        normalized, problem = validate_and_normalize_questions(
            {"item": [{"text": "T1"}, {"id": "x", "text": "T2", "options": ["a"]}]}
        )
        assert problem is None
        assert normalized == [
            {"text": "T1", "options": []},
            {"text": "T2", "options": ["a"], "id": "x"},
        ]

    # -- Rejection: options wrap — non-canonical shapes (body layer) ---------

    async def test_options_rejects_item_not_a_list(self):
        """``{"item": "not-a-list"}`` (item value is not a list) → reject with
        existing field-path hint, ZERO side effects."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": {"item": "not-a-list"}}]
        )
        _assert_zero_side_effects(
            manager,
            hub,
            result,
            "questions[0].options: must be a list of strings or {label, description} objects",
        )

    async def test_options_rejects_item_with_extra_key(self):
        """``{"item": [...], "extra": 1}`` (extra key breaks canonical
        single-key shape) → reject with existing field-path hint."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": {"item": ["a"], "extra": 1}}]
        )
        _assert_zero_side_effects(
            manager,
            hub,
            result,
            "questions[0].options: must be a list of strings or {label, description} objects",
        )

    async def test_options_rejects_item_key_mismatch(self):
        """``{"item_key": [...]}`` (wrong key name) → reject with existing
        field-path hint."""
        tool, manager, hub = _build_tool()
        result = await tool.coroutine(
            questions=[{"text": "t", "options": {"item_key": ["a"]}}]
        )
        _assert_zero_side_effects(
            manager,
            hub,
            result,
            "questions[0].options: must be a list of strings or {label, description} objects",
        )

    # -- Rejection: questions wrap — non-canonical shapes (pure helper) -----

    def test_questions_rejects_item_not_a_list_pure_helper(self):
        normalized, problem = validate_and_normalize_questions({"item": "x"})
        assert normalized is None
        assert problem == "questions: must be a non-empty list of question dicts"

    def test_questions_rejects_item_with_extra_key_pure_helper(self):
        normalized, problem = validate_and_normalize_questions(
            {"item": [{"text": "t"}], "extra": 1}
        )
        assert normalized is None
        assert problem == "questions: must be a non-empty list of question dicts"

    def test_questions_rejects_item_key_mismatch_pure_helper(self):
        normalized, problem = validate_and_normalize_questions({"item_key": [{"text": "t"}]})
        assert normalized is None
        assert problem == "questions: must be a non-empty list of question dicts"

    # -- Pydantic args-schema layer (executor pin) --------------------------

    async def test_args_schema_accepts_canonical_wrapped_questions_end_to_end(self):
        """Canonical wrapped questions flow through the pydantic args-schema
        ``mode="before"`` unwrap, the body runs, the pack is stored, the
        pause flag is set — mirror of the existing executor pin but on the
        acceptance path."""
        import sys

        _saved = {}
        for _key in list(sys.modules):
            if _key.startswith("langgraph"):
                _saved[_key] = sys.modules.pop(_key)
        try:
            from langgraph._internal._runnable import CONF, CONFIG_KEY_RUNTIME
            from langgraph.prebuilt import ToolNode
            from langgraph.runtime import Runtime

            tool, manager, hub = _build_tool()
            tool_node = ToolNode([tool], handle_tool_errors=True)

            from langchain_core.messages import AIMessage

            ai_msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_questions",
                        "args": {"questions": {"item": [{"text": "t"}]}},
                        "id": "call_wrapped_questions",
                        "type": "tool_call",
                    }
                ],
            )
            runtime = Runtime()
            result = await tool_node.ainvoke(
                {"messages": [ai_msg]},
                config={CONF: {CONFIG_KEY_RUNTIME: runtime}},
            )
            out_messages = result["messages"]
            assert len(out_messages) == 1
            msg = out_messages[0]
            from langchain_core.messages import ToolMessage

            assert isinstance(msg, ToolMessage)
            # Acceptance pin: NO error — the canonical wrap was unwrapped by
            # the pydantic before-validator and the body stored the pack.
            assert msg.status != "error"
        finally:
            for _key, _mod in _saved.items():
                sys.modules[_key] = _mod

        # Side effects DID happen — pack stored, pause flag set, SSE emitted.
        stored = manager._question_manager.get_question_pack(INSTANCE_ID)
        assert stored is not None
        assert stored.status == "pending"
        assert stored.questions[0].text == "t"
        manager.set_question_pause_requested.assert_called_once_with(INSTANCE_ID)
        hub.stream_question_pack.assert_awaited_once()

    async def test_args_schema_rejects_non_canonical_wrapped_questions(self):
        """Non-canonical wrap (``{"item": "x"}``) at the questions parameter
        fails the pydantic ValidationError path — executor wrapper, ZERO side
        effects, fail-closed (the body validator is never reached)."""
        import sys

        _saved = {}
        for _key in list(sys.modules):
            if _key.startswith("langgraph"):
                _saved[_key] = sys.modules.pop(_key)
        try:
            from langgraph._internal._runnable import CONF, CONFIG_KEY_RUNTIME
            from langgraph.prebuilt import ToolNode
            from langgraph.runtime import Runtime
            from langchain_core.messages import AIMessage, ToolMessage

            tool, manager, hub = _build_tool()
            tool_node = ToolNode([tool], handle_tool_errors=True)

            ai_msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_questions",
                        "args": {"questions": {"item": "not-a-list"}},
                        "id": "call_bad_wrapped_questions",
                        "type": "tool_call",
                    }
                ],
            )
            runtime = Runtime()
            result = await tool_node.ainvoke(
                {"messages": [ai_msg]},
                config={CONF: {CONFIG_KEY_RUNTIME: runtime}},
            )
            msg = result["messages"][0]
            assert isinstance(msg, ToolMessage)
            assert msg.status == "error"
            # Executor wrapper text (NOT the body's field-path hint).
            assert "ask_questions" in msg.content
            assert "questions" in msg.content
            assert "ERROR: Invalid ask_questions payload" not in msg.content
        finally:
            for _key, _mod in _saved.items():
                sys.modules[_key] = _mod

        # ZERO side effects — pack not stored, pause never set, SSE never awaited.
        assert manager._question_manager.get_question_pack(INSTANCE_ID) is None
        manager.set_question_pause_requested.assert_not_called()
        assert manager.is_question_pause_requested(INSTANCE_ID) is False
        hub.stream_question_pack.assert_not_awaited()
