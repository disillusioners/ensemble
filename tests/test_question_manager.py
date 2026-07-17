"""Unit tests for ``daemon.services.question_manager.QuestionManager``.

Mirrors the structure of ``tests/test_todo_manager.py``:

  1. **Create** — ``set_question_pack`` happy path, auto-id generation,
     provided-id preservation, duplicate-pending rejection, replace after
     answer / clear.
  2. **Read** — ``get_question_pack`` returns the pack when present,
     ``None`` when absent.
  3. **Answer** — ``set_answers`` flips status and stores answers.
  4. **Clear** — ``clear_question_pack`` removes the pack, safe no-op.
  5. **Serialize** — ``pack_to_dict`` emits the documented frozen schema.

The manager is synchronous behind a ``threading.Lock`` (matches the
``TodoManager`` convention), so all tests are plain sync ``def`` — no
``pytest.mark.asyncio`` and no asyncio primitives are needed.
"""

from __future__ import annotations

import re

import pytest

from daemon.services.question_manager import (
    Question,
    QuestionManager,
    QuestionPack,
    pack_to_dict,
)


# =============================================================================
# Set question pack
# =============================================================================


class TestSetQuestionPack:
    """``QuestionManager.set_question_pack(instance_id, questions)`` — happy path & rejections."""

    def test_set_question_pack_creates_pending_pack(self):
        """First call for an instance returns a pack with status='pending'."""
        mgr = QuestionManager()
        pack = mgr.set_question_pack(
            "inst-1",
            [{"text": "What is your name?"}],
        )

        assert pack is not None
        assert isinstance(pack, QuestionPack)
        assert pack.instance_id == "inst-1"
        assert pack.status == "pending"
        assert len(pack.questions) == 1
        assert pack.questions[0].text == "What is your name?"

    def test_set_question_pack_auto_generates_uuids(self):
        """Questions without an ``id`` field get a UUID4 hex assigned.

        The generated id is stored on the ``Question`` so callers can
        reference it later when correlating answers.
        """
        mgr = QuestionManager()
        pack = mgr.set_question_pack(
            "inst-1",
            [
                {"text": "Q1"},
                {"text": "Q2"},
            ],
        )

        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        for q in pack.questions:
            assert uuid_re.match(q.id), f"id {q.id!r} does not look like UUID4 hex"

    def test_set_question_pack_preserves_provided_ids(self):
        """Caller-supplied ``id`` values must be preserved verbatim."""
        mgr = QuestionManager()
        pack = mgr.set_question_pack(
            "inst-1",
            [
                {"id": "approach", "text": "Approach?"},
                {"id": "deadline", "text": "Deadline?"},
            ],
        )

        assert pack.questions[0].id == "approach"
        assert pack.questions[1].id == "deadline"

    def test_set_question_pack_rejects_duplicate_pending(self):
        """Second call while the first pack is still ``pending`` returns ``None``."""
        mgr = QuestionManager()
        first = mgr.set_question_pack(
            "inst-1",
            [{"text": "First question"}],
        )
        assert first is not None

        second = mgr.set_question_pack(
            "inst-1",
            [{"text": "Second question"}],
        )

        assert second is None

        # The first pack must still be the one stored — no overwrite.
        stored = mgr.get_question_pack("inst-1")
        assert stored is first
        assert len(stored.questions) == 1
        assert stored.questions[0].text == "First question"

    def test_set_question_pack_allows_new_pack_after_answered(self):
        """Once the pack transitions to ``answered``, a new pack may replace it."""
        mgr = QuestionManager()
        first = mgr.set_question_pack(
            "inst-1",
            [{"text": "First question"}],
        )
        answered = mgr.set_answers("inst-1", {"first-id": "answer-1"})
        assert answered.status == "answered"

        replacement = mgr.set_question_pack(
            "inst-1",
            [{"text": "Second question"}],
        )

        assert replacement is not None
        assert replacement is not first
        assert replacement.status == "pending"
        assert replacement.questions[0].text == "Second question"

    def test_set_question_pack_allows_new_pack_after_cleared(self):
        """After ``clear_question_pack`` the next ``set_question_pack`` succeeds."""
        mgr = QuestionManager()
        mgr.set_question_pack(
            "inst-1",
            [{"text": "First question"}],
        )
        mgr.clear_question_pack("inst-1")

        replacement = mgr.set_question_pack(
            "inst-1",
            [{"text": "Second question"}],
        )

        assert replacement is not None
        assert replacement.status == "pending"
        assert replacement.questions[0].text == "Second question"


# =============================================================================
# Get question pack
# =============================================================================


class TestGetQuestionPack:
    """``QuestionManager.get_question_pack(instance_id)`` — snapshot read."""

    def test_get_question_pack_returns_the_stored_pack(self):
        """After ``set_question_pack`` the same pack is returned (identity preserved)."""
        mgr = QuestionManager()
        created = mgr.set_question_pack(
            "inst-1",
            [{"text": "What is your favorite color?"}],
        )

        fetched = mgr.get_question_pack("inst-1")

        assert fetched is created

    def test_get_question_pack_returns_none_when_absent(self):
        """``get_question_pack`` returns ``None`` for an unknown instance_id."""
        mgr = QuestionManager()

        assert mgr.get_question_pack("nope") is None


# =============================================================================
# Set answers
# =============================================================================


class TestSetAnswers:
    """``QuestionManager.set_answers(instance_id, answers)`` — status transition."""

    def test_set_answers_flips_status_and_stores_answers(self):
        """After ``set_answers`` the pack is ``answered`` and answers are stored as-is."""
        mgr = QuestionManager()
        mgr.set_question_pack(
            "inst-1",
            [{"id": "color", "text": "Color?"}],
        )

        updated = mgr.set_answers(
            "inst-1",
            {"color": "blue"},
        )

        assert updated is not None
        assert updated.status == "answered"
        assert updated.answers == {"color": "blue"}

    def test_set_answers_returns_none_when_no_pack(self):
        """``set_answers`` returns ``None`` for an unknown instance_id (no pack stored)."""
        mgr = QuestionManager()

        assert mgr.set_answers("nope", {"x": 1}) is None


# =============================================================================
# Clear question pack
# =============================================================================


class TestClearQuestionPack:
    """``QuestionManager.clear_question_pack(instance_id)`` — drop the pack."""

    def test_clear_question_pack_removes_the_pack(self):
        """After clear, ``get_question_pack`` returns ``None``."""
        mgr = QuestionManager()
        mgr.set_question_pack(
            "inst-1",
            [{"text": "Q"}],
        )

        mgr.clear_question_pack("inst-1")

        assert mgr.get_question_pack("inst-1") is None

    def test_clear_question_pack_safe_when_no_pack(self):
        """Calling clear on an unknown instance_id must not raise."""
        mgr = QuestionManager()

        # No exception expected.
        mgr.clear_question_pack("never-existed")


# =============================================================================
# pack_to_dict
# =============================================================================


class TestPackToDict:
    """``pack_to_dict`` — frozen SSE payload schema (Phase 1 + Phase 2)."""

    def test_pack_to_dict_serializes_all_fields(self):
        """Output dict has the documented top-level keys and per-question fields."""
        mgr = QuestionManager()
        pack = mgr.set_question_pack(
            "inst-1",
            [
                {
                    "id": "approach",
                    "text": "Approach?",
                    "options": ["A", "B"],
                    "allow_custom": False,
                    "required": True,
                },
                {"text": "Deadline?"},
            ],
        )
        assert pack is not None

        d = pack_to_dict(pack)

        assert isinstance(d, dict)
        assert d["instance_id"] == "inst-1"
        assert d["status"] == "pending"
        assert isinstance(d["created_at"], str) and d["created_at"]
        assert d["answers"] == {}

        # Questions list — list of dicts with the documented fields.
        assert isinstance(d["questions"], list)
        assert len(d["questions"]) == 2

        first = d["questions"][0]
        assert first["id"] == "approach"
        assert first["text"] == "Approach?"
        assert first["options"] == ["A", "B"]
        assert first["allow_custom"] is False
        assert first["required"] is True
        assert first["answer"] is None

        # Second question auto-got a UUID; defaults applied.
        second = d["questions"][1]
        assert second["text"] == "Deadline?"
        assert second["options"] == []
        assert second["allow_custom"] is True
        assert second["required"] is True
        assert second["answer"] is None