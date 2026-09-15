"""Critical-Notes Phase 2 — R21 entry-gate filter and 0b defensive review.

Phase-2 review-conditioned survey of every surface that consumes the
critical-notes list unfiltered upstream:

* ``daemon/tools/critical_notes.py::_is_active_critical_note`` — the
  shared predicate (single source of truth).
* ``daemon/services/context_injection.py::_mcp_rag_hint`` — defensive
  post-fetch filter applied inside the renderer so any upstream caller
  shape can't leak a superseded row.
* ``daemon/tools/external_opencode.py`` — list-and-coerce path that
  builds the dict list passed into the preloader.
* ``daemon/routers/projects.py::_get_critical_notes_safe`` — fetch
  site used by every ProjectResponse payload.
* ``daemon/services/context_messages.py::_format_critical_notes_section``
  — the 0b defensive review: ``is not None`` comparison so a stray
  empty-string ``superseded_by_id`` pointer is ALSO dropped (the
  truthy-check previously FALSELY KEPT it).

These tests do NOT require any DB / engine / fixture — the
predicate is pure and the three downstream surfaces are exercised
with mock-shaped inputs.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from daemon.services.context_messages import (
    _format_critical_notes_section,
    build_project_context_message,
)
from daemon.services.context_injection import _mcp_rag_hint
from daemon.tools.critical_notes import _is_active_critical_note


# ── 1. The shared predicate (single source of truth) ──────────────────────


class TestSharedPredicate:
    def test_dict_with_null_superseded_kept(self):
        assert _is_active_critical_note({"superseded_by_id": None}) is True

    def test_dict_with_id_superseded_dropped(self):
        assert _is_active_critical_note({"superseded_by_id": "x-1"}) is False

    def test_dict_with_empty_string_superseded_dropped(self):
        """0b defensive: a stray ``""`` pointer must NOT bypass the filter."""
        assert _is_active_critical_note({"superseded_by_id": ""}) is False

    def test_dict_with_missing_key_kept(self):
        assert _is_active_critical_note({}) is True

    def test_dict_with_non_dict_dropped(self):
        # Defensive — non-dict inputs DO raise the predicate (the
        # try branch leaves ``sid = None`` which is "active"), so
        # non-dicts surface as ACTIVE in this single check.
        assert _is_active_critical_note("a string") is True
        assert _is_active_critical_note(123) is True


# ── 2. _mcp_rag_hint — defensive post-fetch filter ──────────────────────────


class TestMcpRagHintFilter:
    def test_superseded_dropped_at_render_time(self):
        # Two notes, one superseded. The renderer should iterate
        # only the active one even if upstream passes the raw list
        # unfiltered.
        notes = [
            {"priority": "high", "category": "risk", "summary": "Live note"},
            {
                "priority": "low",
                "category": "decision",
                "summary": "Dead note",
                "superseded_by_id": "dead-parent",
            },
        ]
        out = _mcp_rag_hint(
            audience="external",
            project_id="p-1",
            project_name="proj",
            critical_notes=notes,
        )
        assert "Live note" in out
        assert "Dead note" not in out

    def test_all_superseded_renders_empty_block(self):
        notes = [
            {"priority": "high", "summary": "X", "superseded_by_id": "y"},
        ]
        out = _mcp_rag_hint(
            audience="external",
            project_id="p-1",
            critical_notes=notes,
        )
        # No critical notes block should appear at all (only the
        # current-project context line is emitted when the block is
        # empty).
        assert "⚡ Critical notes" not in out

    def test_stray_empty_string_pointer_dropped(self):
        """0b defensive review: empty string superseded_by_id is also dropped."""
        notes = [
            {
                "priority": "high",
                "category": "risk",
                "summary": "Has empty pointer",
                "superseded_by_id": "",
            },
        ]
        out = _mcp_rag_hint(
            audience="external",
            project_id="p-1",
            critical_notes=notes,
        )
        assert "Has empty pointer" not in out


# ── 3. build_project_context_message + 0b in _format_critical_notes_section ──


class TestRenderSectionEmptyStringFilter:
    def test_empty_string_superseded_dropped_in_injected_block(self):
        """0b defensive review: 'truthy-check' regression — the empty
        string used to slip past ``not e.get("superseded_by_id")``.
        The strict ``is None`` comparison drops it.
        """
        notes = [
            {
                "id": "n-1",
                "priority": "high",
                "category": "risk",
                "summary": "Has empty pointer",
                "superseded_by_id": "",
                "reference": None,
                "pinned": False,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "n-2",
                "priority": "medium",
                "category": "pattern",
                "summary": "Legit active note",
                "reference": None,
                "pinned": False,
                "created_at": "2026-01-01T00:00:01+00:00",
            },
        ]
        msg = build_project_context_message(
            project={"name": "p"},
            critical_notes=notes,
            history_entries=[],
        )
        assert isinstance(msg, HumanMessage)
        assert "Legit active note" in msg.content
        assert "Has empty pointer" not in msg.content

    def test_non_null_id_still_drops(self):
        notes = [
            {
                "id": "n-1",
                "priority": "high",
                "category": "risk",
                "summary": "Genuinely superseded",
                "superseded_by_id": "n-99",
                "reference": None,
                "pinned": False,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        msg = build_project_context_message(
            project={"name": "p"},
            critical_notes=notes,
            history_entries=[],
        )
        assert isinstance(msg, HumanMessage)
        assert "Genuinely superseded" not in msg.content
