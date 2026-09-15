"""Critical-Notes Phase 2 — R25 stable id (project:{instance_id}).

Verifies the contract adopted in
``.agents/shared/planning/critical-notes-retrieval/decisions.md``:

* The injected project-context HumanMessage carries the stable
  construction-time id ``project:{instance_id}`` (option (a)).
* Implementer plumbing requirement: ``instance_id`` plumbed into
  :func:`build_project_context_message` via the ``instance_id=``
  kwarg so the builder mints the id at construction time.
* Id-stability: same instance id → same message id (so a
  second call during the same session returns a HumanMessage
  with the SAME id — LangGraph ``add_messages`` SUPERSEDES on
  matching id, so a revive / restore / repeat-build does NOT
  append a duplicate).
* Revive no-append: a fresh builder call for the same instance
  produces a fresh HumanMessage object but with the SAME id —
  the message-tap metadata path stays stable on restore.

Tests are pure (no DB / engine / mock manager required) — the
builder accepts already-fetched data and the id-derivation is a
one-line helper lookup.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from daemon.services.context_messages import (
    build_project_context_message,
    _stable_id_for,
)


_PROJECT = {"name": "test-project"}
_CRITICAL_NOTES = [
    {
        "id": "n-1",
        "priority": "high",
        "category": "risk",
        "summary": "Test note",
        "reference": None,
        "pinned": False,
        "superseded_by_id": None,
        "created_at": "2026-09-15T13:00:00+00:00",
    },
]
_HISTORY: list[dict] = []


class TestStableId:
    def test_builder_with_instance_id_mints_project_id(self):
        instance_id = "instance-abc-123"
        msg = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
            instance_id=instance_id,
        )
        assert isinstance(msg, HumanMessage)
        assert msg.id == f"project:{instance_id}"

    def test_id_matches_stable_id_for_helper(self):
        """The builder's id must equal :func:`_stable_id_for('project', instance_id)`."""
        instance_id = "instance-xyz-789"
        msg = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
            instance_id=instance_id,
        )
        assert msg.id == _stable_id_for("project", instance_id=instance_id)

    def test_no_instance_id_falls_back_to_uuid4(self):
        """When ``instance_id`` is omitted (legacy callers / tests), the
        builder mints a fresh uuid4. The pre-R25 behavior is preserved.
        """
        msg1 = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
        )
        msg2 = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
        )
        assert isinstance(msg1, HumanMessage)
        assert isinstance(msg2, HumanMessage)
        # Distinct objects / ids — fresh uuid4 each call.
        assert msg1.id != msg2.id
        assert len(msg1.id) == 36  # uuid4 string length

    def test_same_instance_id_same_id_across_calls(self):
        """Revive no-append contract: same instance → same id → no
        duplicate block after the messaging path runs the slot a
        second time.
        """
        instance_id = "instance-stable-test"
        msg_a = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
            instance_id=instance_id,
        )
        msg_b = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
            instance_id=instance_id,
        )
        # Different HumanMessage objects, but the SAME id — so
        # LangGraph's ``add_messages`` reducer will SUPERSEDE
        # rather than append.
        assert msg_a is not msg_b
        assert msg_a.id == msg_b.id
        assert msg_a.id == f"project:{instance_id}"

    def test_different_instance_ids_yield_different_ids(self):
        """Per-instance determinism must NOT collide across instances."""
        msg_a = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
            instance_id="instance-a",
        )
        msg_b = build_project_context_message(
            project=_PROJECT,
            critical_notes=_CRITICAL_NOTES,
            history_entries=_HISTORY,
            instance_id="instance-b",
        )
        assert msg_a.id != msg_b.id
        assert msg_a.id == "project:instance-a"
        assert msg_b.id == "project:instance-b"

    def test_synthetic_via_helper_for_coverage(self):
        """Direct helper exercise — keeps the docstring/id-format
        table honest (S19 single-source-of-truth rule).
        """
        assert _stable_id_for("project", instance_id="i-1") == "project:i-1"
        try:
            _stable_id_for("project", instance_id=None)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "_stable_id_for('project') must require instance_id"
            )
        try:
            _stable_id_for("unknown_kind")
        except ValueError:
            pass
        else:
            raise AssertionError(
                "_stable_id_for must reject unknown kinds (S19 invariant)"
            )
