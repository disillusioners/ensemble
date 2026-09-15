"""Phase-1 render contract for the injected critical-notes block.

Pins the ``_format_critical_notes_section`` Phase-1 changes
(critical-notes-retrieval, R19) plus the builder-level guarantees:

- Superseded rows are NEVER injected.
- ALL non-superseded rows are injected (Phase 1 has no tail selection).
- R19 ordering is scoped to the INJECTED BLOCK ONLY: pinned tier first
  (critical→high→medium, recency within tier), then the remainder
  (priority→recency).
- ``reference`` is render-bounded with the truncation suffix — the
  defensive backstop ONLY (write-side reject is authoritative).
  Truncation must never fire for rows already within the bound.
- ``detail_ref`` is NEVER injected.
- No strike-through / stale marks in the injected block (list surfaces
  only).
- Render bound is installable (config-layer tuning, D4 — no env var).
- Compaction three-bucket contract untouched: the block is still ONE
  ``context_kind=project`` message.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.context_messages import (
    build_project_context_message,
    install_critical_notes_render_config,
    _format_critical_notes_section,
    _order_critical_notes_for_injection,
    _resolve_critical_notes_reference_max,
)
from tests.helpers.critical_notes_fixtures import reset_module_state


def _note(**overrides):
    base = {
        "id": "note-1",
        "priority": "high",
        "category": "risk",
        "summary": "A risk note",
        "reference": None,
        "pinned": False,
        "superseded_by_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "detail_ref": "SECRET-DETAIL-TEXT",
    }
    base.update(overrides)
    return base


class TestSupersessionFiltering:
    def test_superseded_rows_never_injected(self):
        notes = [_note(summary="Live note"), _note(summary="Dead note", superseded_by_id="note-9")]
        section = _format_critical_notes_section(notes)
        assert "Live note" in section
        assert "Dead note" not in section
        assert "superseded by" not in section

    def test_all_superseded_renders_empty(self):
        notes = [_note(superseded_by_id="note-9")]
        assert _format_critical_notes_section(notes) == ""

    def test_all_non_superseded_rendered_phase1(self):
        notes = [_note(summary=f"note {i}", id=f"n{i}") for i in range(10)]
        section = _format_critical_notes_section(notes)
        for i in range(10):
            assert f"note {i}" in section


class TestInjectedBlockOrdering:
    def test_pinned_tier_first_then_remainder(self):
        notes = [
            _note(id="unpinned-critical", priority="critical", summary="unpinned critical"),
            _note(id="pinned-medium", priority="medium", pinned=True, summary="pinned medium"),
            _note(id="pinned-critical", priority="critical", pinned=True, summary="pinned critical"),
        ]
        ordered = _order_critical_notes_for_injection(notes)
        ids = [n["id"] for n in ordered]
        assert ids == ["pinned-critical", "pinned-medium", "unpinned-critical"]

    def test_recency_within_tier_newest_first(self):
        old = _note(id="old", pinned=True, created_at="2026-01-01T00:00:00+00:00")
        new = _note(id="new", pinned=True, created_at="2026-09-01T00:00:00+00:00")
        ordered = _order_critical_notes_for_injection([old, new])
        assert [n["id"] for n in ordered] == ["new", "old"]

    def test_unknown_priority_sorts_last_within_group(self):
        notes = [
            _note(id="weird", priority="cosmic"),
            _note(id="medium", priority="medium"),
        ]
        ordered = _order_critical_notes_for_injection(notes)
        assert [n["id"] for n in ordered] == ["medium", "weird"]


class TestReferenceBounding:
    def test_over_bound_truncated_with_suffix(self):
        notes = [_note(reference="x" * 600)]
        section = _format_critical_notes_section(notes)
        assert "… (truncated — project_cn_list for full text)" in section
        # The raw 600-char body must not survive whole.
        assert "x" * 600 not in section

    def test_within_bound_untouched(self):
        notes = [_note(reference="x" * 500)]
        section = _format_critical_notes_section(notes)
        assert "… (truncated" not in section
        assert "x" * 500 in section

    def test_bound_is_installable_no_env(self):
        assert _resolve_critical_notes_reference_max() == 500
        install_critical_notes_render_config(reference_max=100)
        assert _resolve_critical_notes_reference_max() == 100
        notes = [_note(reference="x" * 150)]
        section = _format_critical_notes_section(notes)
        assert "… (truncated" in section


class TestDetailRefNeverInjected:
    def test_detail_ref_never_in_injected_block(self):
        notes = [_note(detail_ref="SECRET-DETAIL-TEXT")]
        section = _format_critical_notes_section(notes)
        assert "SECRET-DETAIL-TEXT" not in section

    def test_detail_ref_not_in_built_message(self):
        msg = build_project_context_message(
            {"name": "p"}, [_note(detail_ref="SECRET-DETAIL-TEXT")], []
        )
        assert isinstance(msg, HumanMessage)
        assert "SECRET-DETAIL-TEXT" not in msg.content


class TestBuilderLevelGuarantees:
    def test_block_still_single_project_context_kind(self):
        msg = build_project_context_message({"name": "p"}, [_note()], [])
        assert msg.additional_kwargs["context_kind"] == "project"
        assert msg.additional_kwargs.get("injected_message") is True

    def test_injected_block_has_no_maintenance_marks(self):
        notes = [
            _note(summary="Stale but active", created_at="2026-01-01T00:00:00+00:00"),
            _note(summary="Retired note", superseded_by_id="n-9"),
        ]
        section = _format_critical_notes_section(notes)
        assert "⚠️" not in section
        assert "~~" not in section
        assert "days ago" not in section
