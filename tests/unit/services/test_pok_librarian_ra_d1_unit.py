"""The RA-D1 leg — the group's J target is computed ONCE with ``family=None``.

The functional-gate fix round's one non-skippable item (the leader's
ruling, 2026-11-24): the I3 wiring computed the duplicate group's
merge target PER ENTRY with ``family=entry.family``, so a FULL-TIE +
DIFFERENT-FAMILY group made each row its own J target — both rows
skipped, ZERO proposals, the group never surfaced: a SILENT signal
replacing the contradictory one. The fix computes the group's target
ONCE with ``family=None`` (the J-partition selector picks the group's
survivor over the WHOLE group); the R-DET re-slug path
(``detect_r_ret_candidates``) keeps its own family-scoped selection,
unchanged.

The leg asserts the GOOD SHAPE (the proposal set targets exactly the
J-selected row — not zero proposals, not a mutual pair), and the two
mutation directions are proven at code level in the fix round's
mutation legs (reverting to the per-entry-family computation → the
zero-proposal shape; reverting to the arbitrary ``survivors[0]`` → the
mutual pair).

Fence discipline: pure-function probes on in-memory dataclasses — no
engine, no store, no network.
"""

from __future__ import annotations

from daemon.services.pok_librarian import (
    EntryMetrics,
    StoreMetrics,
    propose_consolidations,
)


def _tie_row(slug: str, family: str, sha: str, rid: str) -> EntryMetrics:
    """A FULL-TIE row: every trusted signal equal, families differ."""
    return EntryMetrics(
        id=rid,
        slug=slug,
        family=family,
        content_sha256=sha,
        verify_count=1,
    )


class TestGroupTargetRA1:
    def test_a_full_tie_different_families_surfaces_the_group_target(
        self,
    ) -> None:
        """RA-D1 (the 2-row defect shape): the group is surfaced — ONE
        proposal targeting the group's J-SELECTED row, NOT zero
        proposals (the silent signal), NOT a mutual pair (the
        contradiction)."""
        store = StoreMetrics(
            project_id="p",
            active_count=2,
            entries=(
                _tie_row("fam-01-row", "fam", "sha-x", "i1"),
                _tie_row("other-01-row", "other", "sha-x", "i2"),
            ),
        )
        proposals = propose_consolidations(store)
        # NOT zero (the RA-D1 silent signal)...
        assert proposals, "the full-tie group was never surfaced (RA-D1)"
        # ...NOT a mutual pair...
        assert len(proposals) == 1
        # ...and the proposal set targets exactly the group's J-selected
        # row: family=None over the full tie ⇒ the selector's slug
        # tiebreak picks "other-01-row" (the lexical max).
        assert proposals[0].slug == "fam-01-row"
        assert proposals[0].target_slug == "other-01-row"
        assert proposals[0].basis == "covered-by-surviving-row"

    def test_a_three_row_full_tie_targets_the_group_j_row_only(self) -> None:
        """RA-D1 (the 3-row shape): every non-J row proposes into the
        ONE group target; the J-selected row itself is NOT proposed
        away under the covered basis."""
        store = StoreMetrics(
            project_id="p",
            active_count=3,
            entries=(
                _tie_row("aaa-01-row", "aaa", "sha-y", "i1"),
                _tie_row("bbb-01-row", "bbb", "sha-y", "i2"),
                _tie_row("ccc-01-row", "ccc", "sha-y", "i3"),
            ),
        )
        proposals = propose_consolidations(store)
        # The J pick over the whole group (family=None, slug tiebreak)
        # is "ccc-01-row": exactly TWO proposals, both targeting it.
        assert len(proposals) == 2
        assert {p.slug for p in proposals} == {"aaa-01-row", "bbb-01-row"}
        assert all(p.target_slug == "ccc-01-row" for p in proposals)
        assert all(p.basis == "covered-by-surviving-row" for p in proposals)
        # The J-selected row itself is never proposed away under the
        # covered basis (the mutual-pair guard).
        assert "ccc-01-row" not in {p.slug for p in proposals}

    def test_the_r_ret_re_slug_path_is_untouched(self) -> None:
        """The R-RET path is UNTOUCHED by RA-D1 (the leader's ruling):
        ``detect_r_ret_candidates`` keeps its OWN selection — it still
        calls ``select_merge_target`` over the well-slugged members of
        each family group, exactly as before the fix. The consolidation
        fix must not narrow that surface."""
        import inspect

        from daemon.services import pok_librarian

        source = inspect.getsource(pok_librarian.detect_r_ret_candidates)
        assert "select_merge_target(" in source, (
            "the R-DET re-slug path lost its selector call — RA-D1 must "
            "not touch it"
        )
