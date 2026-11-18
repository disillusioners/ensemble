"""Compact-note lane — the ENGINE surface (``daemon/compaction.py``).

This file's surviving content is the **ceiling rule** (``_apply_ceiling_rule``),
which SURVIVES the pivot: it serves the AUTO path unchanged. The note-specific
hunks (the note-first arm, the merge-skip entry guard, the armed-aware
floor predicate, the threshold bypass, the note render) were REMOVED in
Phase A (commit 02685ac54) per the architect v1.5 per-piece audit — the
append path makes no model call, so those pieces have no caller.

The append path's doc wrapper + tail splice + receipt pins live in the
tool-level pack (``tests/unit/tools/test_prompt_reload_compact_note.py``)
and, once the graph wiring lands, in integration tests.
"""

from __future__ import annotations


class TestCeilingRule:
    def test_the_ceiling_rule_condenses_oldest_sections_first(self) -> None:
        """The ceiling rule itself SURVIVES (the auto path uses it
        unchanged)."""
        from daemon.compaction import _apply_ceiling_rule

        sections = [
            {"start_idx": 1, "end_idx": 1, "body": "s" * 4000, "start_id": None, "end_id": None}
        ]
        condensed, archived_line, archived = _apply_ceiling_rule(
            sections, "GLOBAL", 1000
        )
        assert archived >= 1


# ── F10 — the honest-truncation marker text is PINNED ──────────────────────


class TestHonestTruncationMarker:
    def _doc(self, *, honest_truncation: bool):
        from daemon.compaction import build_compaction_doc

        # An over-cap GLOBAL forces the truncation marker.
        huge = "x " * 4000
        return build_compaction_doc(
            instance_id="iid-1",
            seq=1,
            mode="summary",
            compacted_at="2026-11-17T00:00:00+00:00",
            global_overview=huge,
            sections=[],
            total_sections=0,
            summarized_start=1,
            summarized_end=10,
            preserved_count=3,
            dropped_spans=[],
            msg_timestamps=None,
            context_window=None,
            previous_overview="",
            honest_truncation=honest_truncation,
        )

    def test_the_honest_marker_never_claims_section_detail(self) -> None:
        body = self._doc(honest_truncation=True).content
        assert "no SECTION DETAIL follows on this path" in body
        assert "SECTION DETAIL below remains complete" not in body

    def test_the_default_marker_keeps_the_legacy_clause(self) -> None:
        """Every existing (auto-path) caller is byte-identical: the
        legacy clause survives when honest_truncation is False."""
        body = self._doc(honest_truncation=False).content
        assert "SECTION DETAIL below remains complete" in body
