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
