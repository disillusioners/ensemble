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
    reset_critical_notes_render_config,
    _format_critical_notes_section,
    _order_critical_notes_for_injection,
    _resolve_critical_notes_reference_max,
    _resolve_critical_notes_section_char_cap,
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


class TestPreOrderedRenderMode:
    """Item 10 (spec §4.2/R19 render-order contract): the tiered path's
    tail arrives in FUSION-score order — the renderer must preserve it
    verbatim (``pre_ordered=True``) instead of applying the legacy R19
    re-sort. The legacy mode (default) keeps the re-sort byte-identical.
    """

    @staticmethod
    def _fusion_ordered_notes():
        """Pinned row + tail whose fusion order DIFFERS from the
        legacy priority/recency order: fusion-1st is priority=medium
        (would sort LAST under the legacy re-sort), fusion-2nd is
        high, fusion-3rd critical.
        """
        return [
            _note(id="p-1", pinned=True, priority="critical",
                  summary="PINNED-ROW"),
            _note(id="t-1", priority="medium", summary="FUSION-FIRST"),
            _note(id="t-2", priority="high", summary="FUSION-SECOND"),
            _note(id="t-3", priority="critical", summary="FUSION-THIRD"),
        ]

    def test_pre_ordered_mode_preserves_fusion_order(self):
        notes = self._fusion_ordered_notes()
        section = _format_critical_notes_section(notes, pre_ordered=True)
        idx_pinned = section.index("PINNED-ROW")
        idx_first = section.index("FUSION-FIRST")
        idx_second = section.index("FUSION-SECOND")
        idx_third = section.index("FUSION-THIRD")
        # Tail renders in EXACTLY the input (fusion) order — the
        # medium-priority fusion winner stays AHEAD of the critical
        # row, which the legacy re-sort would have moved to the front.
        assert idx_pinned < idx_first < idx_second < idx_third

    def test_pre_ordered_mode_still_filters_superseded_and_emits_hint(self):
        notes = self._fusion_ordered_notes()
        notes[-1]["__hint_drop_count"] = 2
        notes.append(_note(id="dead", summary="DEAD-ROW",
                           superseded_by_id="p-1"))
        section = _format_critical_notes_section(notes, pre_ordered=True)
        # R21 filter + sentinel consumption still active in
        # pre-ordered mode.
        assert "DEAD-ROW" not in section
        assert "2 additional notes not shown" in section

    def test_default_mode_keeps_legacy_r19_resort(self):
        notes = self._fusion_ordered_notes()
        section = _format_critical_notes_section(notes)
        # Legacy mode re-sorts by priority: critical tail row first,
        # the medium-priority fusion winner last.
        idx_third = section.index("FUSION-THIRD")
        idx_second = section.index("FUSION-SECOND")
        idx_first = section.index("FUSION-FIRST")
        assert idx_third < idx_second < idx_first

    def test_block_level_tail_order_matches_fusion_order(self):
        """Builder-level pin: with ``notes_pre_ordered=True`` the tail
        order INSIDE the injected block == fusion order."""
        from unittest.mock import MagicMock

        project = MagicMock()
        project.to_dict.return_value = {
            "project_id": "p1", "critical_notes": [],
        }
        notes = self._fusion_ordered_notes()
        msg = build_project_context_message(
            project, notes, [], instance_id="i-order",
            notes_pre_ordered=True,
        )
        assert msg is not None
        content = msg.content
        idx_pinned = content.index("PINNED-ROW")
        idx_first = content.index("FUSION-FIRST")
        idx_second = content.index("FUSION-SECOND")
        idx_third = content.index("FUSION-THIRD")
        assert idx_pinned < idx_first < idx_second < idx_third


class TestRenderAllSectionCap:
    """W8 (cap-fix review, 2026-10-31): the render-ALL path (zero pins,
    ``pre_ordered=False``) has NO selector-side budget — the renderer now
    applies the SAME ``section_char_cap`` (default 12000), dropping whole
    rows and ALWAYS closing with the canonical truncation line. The
    tiered path is NEVER re-capped here (its last surviving row may carry
    the ``__hint_drop_count`` sentinel; re-capping could cut it).
    """

    CANONICAL_TRUNCATION = (
        "additional notes not shown — use project_cn_list for the full view"
    )

    @staticmethod
    def _large_set(n: int) -> list[dict]:
        return [
            _note(id=f"n{i}", summary=f"note summary number {i} " + "x" * 120)
            for i in range(n)
        ]

    def test_render_all_large_set_bounded_with_exactly_one_truncation_line(self):
        notes = self._large_set(200)
        section = _format_critical_notes_section(notes)
        # Exactly ONE truncation line, canonical wording.
        assert section.count(self.CANONICAL_TRUNCATION) == 1
        # The line names the hidden count (whichever rows were dropped).
        import re

        match = re.search(r"- …\((\d+) additional notes not shown", section)
        assert match, section[-400:]
        hidden = int(match.group(1))
        # Bounded: total section length stays within cap + the trailing
        # line (the ONLY overage the contract allows).
        cap = _resolve_critical_notes_section_char_cap()
        line_len = len(match.group(0)) + 1
        assert len(section) <= cap + line_len + 1  # +1 trailing newline
        # Every rendered row is intact — whole-row drops, no partial cut.
        assert hidden >= 1
        # R19 legacy re-sort is recency-DESC within a tier: the NEWEST
        # row (199) renders first; a row always renders.
        assert "note summary number 199" in section

    def test_render_all_small_set_renders_in_full(self):
        notes = self._large_set(5)
        section = _format_critical_notes_section(notes)
        assert self.CANONICAL_TRUNCATION not in section
        for i in range(5):
            assert f"note summary number {i}" in section

    def test_cap_cannot_cut_the_trailing_line(self):
        """Even when NO row fits the cap (greedy selector semantics allow
        the zero-row degenerate), the section closes with the canonical
        truncation line naming every hidden row — the cap can never cut
        the line itself (appended after the budget check)."""
        install_critical_notes_render_config(section_char_cap=10)
        try:
            notes = self._large_set(3)
            section = _format_critical_notes_section(notes)
            assert section.count(self.CANONICAL_TRUNCATION) == 1
            assert "3 additional notes not shown" in section
        finally:
            reset_critical_notes_render_config()

    def test_cap_is_installable_no_env(self):
        assert _resolve_critical_notes_section_char_cap() == 12000
        install_critical_notes_render_config(section_char_cap=500)
        try:
            assert _resolve_critical_notes_section_char_cap() == 500
            notes = self._large_set(30)
            section = _format_critical_notes_section(notes)
            assert self.CANONICAL_TRUNCATION in section
        finally:
            reset_critical_notes_render_config()

    def test_tiered_path_is_never_re_capped_sentinel_survives(self):
        """pre_ordered=True (tiered): the renderer must NOT apply the cap —
        a large tiered set whose final row carries the sentinel keeps the
        hint line and renders every selected row, byte-identical to the
        pre-W8 contract."""
        notes = [
            _note(id="p-1", pinned=True, priority="critical", summary="PINNED-ROW")
        ]
        notes += [
            _note(id=f"t-{i}", priority="high", summary=f"TIERED-ROW-{i} " + "y" * 300)
            for i in range(40)
        ]
        notes[-1]["__hint_drop_count"] = 3
        section = _format_critical_notes_section(notes, pre_ordered=True)
        # No render-all cap applied on the tiered path.
        assert len(section) > 12000
        assert "3 additional notes not shown" in section  # sentinel survives
        assert "TIERED-ROW-0" in section
        assert f"TIERED-ROW-39" in section  # last selected row renders in full

    def test_build_message_render_all_path_bounded(self):
        """Builder-level pin: the render-all builder path inherits the cap
        (zero-pin projects cannot inject an unbounded block)."""
        from unittest.mock import MagicMock

        project = MagicMock()
        project.to_dict.return_value = {"project_id": "p1"}
        notes = self._large_set(200)
        msg = build_project_context_message(project, notes, [], instance_id="i-cap")
        assert msg is not None
        assert msg.content.count(self.CANONICAL_TRUNCATION) == 1
