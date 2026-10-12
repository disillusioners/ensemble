"""Cluster 2.6 — the ``daemon/constants.py`` VACUUM policy comment
reflects the scheduled/interactive lane split (decisions.md §D-6).

R6 mitigation (be-vacuum-full.md risk 2.r6): the comment block is
documentation, but a mistaken "VACUUM FULL is now OK in the scheduled
lane" reading would be dangerous — these 2 tests pin that the block
(a) names BOTH lanes and (b) cites the structural guard, so a future
edit that silently drops the lane split (or the guard citation) trips
loudly.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSTANTS = REPO_ROOT / "daemon" / "constants.py"


def _vacuum_comment_block() -> str:
    """The VACUUM policy comment block — the text between the
    ``# ── PG-only VACUUM`` section header and the ``VACUUM_ENABLED``
    constant line (the block §D-6 supersedes)."""
    src = CONSTANTS.read_text(encoding="utf-8")
    start = src.find("# ── PG-only VACUUM")
    assert start != -1, (
        "the PG-only VACUUM policy section header is gone from "
        "daemon/constants.py — the lane-split documentation block was "
        "removed (decisions.md §D-6)"
    )
    end = src.find("VACUUM_ENABLED", start)
    assert end != -1, (
        "VACUUM_ENABLED not found after the VACUUM policy header — "
        "daemon/constants.py structure drifted; update this test's "
        "block extraction"
    )
    return src[start:end]


class TestPolicyCommentLaneSplit:
    def test_policy_comment_mentions_both_lanes(self):
        """The block names BOTH lanes — the scheduled lane keeps its
        plain-VACUUM-forever contract; the interactive lane documents
        the operator-opted escalation."""
        block = _vacuum_comment_block()
        assert "Scheduled lane" in block, (
            "the VACUUM policy comment no longer names the Scheduled "
            "lane — the lane split documentation was dropped "
            "(decisions.md §D-6)"
        )
        assert "Interactive lane" in block, (
            "the VACUUM policy comment no longer names the Interactive "
            "lane — the lane split documentation was dropped "
            "(decisions.md §D-6)"
        )

    def test_policy_comment_mentions_structural_guard(self):
        """The block cites the structural guard (AST pins) — the
        scheduled lane's plain-VACUUM-forever contract is enforced by
        the pins, not by the comment; the comment must SAY so."""
        block = _vacuum_comment_block()
        assert ("structural guard" in block) or ("AST pin" in block), (
            "the VACUUM policy comment no longer cites the structural "
            "guard / AST pins — the scheduled-lane lock documentation "
            "was dropped (decisions.md §D-6)"
        )
