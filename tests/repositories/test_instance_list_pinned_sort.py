"""Unit tests for pinned-first sort order in ``SQLModelInstanceRepository.list()``.

Validates the LEFT JOIN to ``instance_ui_prefs`` added in
``feature/pinned-instance-sort`` (commit 8fae7b8d). The repository now sorts by
``pinned DESC NULLS LAST, pinned_at DESC NULLS LAST, created_at DESC`` on the
flat pagination path.

These tests run against an in-memory SQLite database using the same pattern as
``tests/unit/test_instance_tree_loading.py``. Importing :class:`InstanceUiPrefs`
at module level registers its table on ``SQLModel.metadata`` so
``create_all`` picks it up (mirroring ``test_instance_ui_prefs.py``).

The four scenarios required:

1. **Pinned-first ordering** — an older *pinned* instance sorts before a newer
   *unpinned* instance; all pinned instances precede all unpinned ones.
2. **Pagination correctness** — with multiple pages, pinned instances are
   concentrated on page 1, not scattered across pages.
3. **No prefs row (NULL handling)** — instances WITHOUT an
   ``instance_ui_prefs`` row sort as unpinned (NULLS LAST behaviour) and land
   below any pinned instance.
4. **Multiple pinned instances** — among pinned instances, the most recently
   pinned (``pinned_at`` DESC) comes first.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance_ui_prefs import InstanceUiPrefs

# Importing InstanceUiPrefs at module level registers the table on
# SQLModel.metadata so the ``engine`` fixture's ``create_all`` picks it up —
# exactly the same pattern used by ``test_instance_ui_prefs.py``.
_ = InstanceUiPrefs


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def repo():
    """In-memory SQLite repository with the full schema registered.

    Mirrors the fixture in ``tests/unit/test_instance_tree_loading.py``.
    ``SQLModel.metadata.create_all`` builds every table registered via the
    import chain — including ``instance_ui_prefs`` (imported above).
    """
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def engine_factory():
    """Return a factory that builds a fresh in-memory engine + repo pair.

    Used by scenarios that need direct DB access to insert prefs rows.
    """
    created = {}

    def _make():
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        r = SQLModelInstanceRepository(engine)
        created["engine"] = engine
        created["repo"] = r
        return r, engine

    yield _make


# =============================================================================
# Helpers
# =============================================================================


def _iso(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> str:
    """Build a deterministic ISO-8601 UTC timestamp for controlled ordering.

    Lexicographic ordering of these strings matches chronological ordering
    (all are zero-padded, fixed-width ISO-8601 with the same timezone suffix).
    """
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).isoformat()


def _make_instance(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    created_at: str,
    parent_id: str | None = None,
    agent_id: str = "developer",
    project_id: str = "proj-1",
) -> None:
    """Create an instance and then overwrite its ``created_at``.

    ``repo.create()`` stamps ``created_at`` internally; we override it
    afterwards via ``repo.update()`` (which accepts arbitrary model fields
    except ``status`` / ``instance_metadata``) so each row has a distinct,
    controlled timestamp. Distinctness is essential because ``created_at`` is
    the final sort tiebreaker and the default stamps all rows with "now".
    """
    repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"./agents/{agent_id}",
        parent_id=parent_id,
        status="idle",
        project_id=project_id,
    )
    repo.update(instance_id, created_at=created_at)


def _set_pref(
    engine,
    instance_id: str,
    pinned: bool,
    pinned_at: str | None = None,
) -> None:
    """Insert an ``instance_ui_prefs`` row with exact ``pinned``/``pinned_at``.

    We insert directly via a session (rather than
    ``InstanceUiPrefsRepository.upsert``) so the test fully controls the
    ``pinned_at`` value. ``upsert`` auto-stamps ``pinned_at`` to "now", which
    would make scenario 4's DESC-ordering assertion non-deterministic. Direct
    insertion keeps the sort-order test deterministic.
    """
    row = InstanceUiPrefs(
        instance_id=instance_id,
        pinned=pinned,
        pinned_at=pinned_at,
        created_at=_iso(2026, 1, 1),
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()


# =============================================================================
# Scenario 1 — Pinned-first ordering
# =============================================================================


class TestPinnedFirstOrdering:
    """Pinned instances appear first; an older pinned instance precedes a
    newer unpinned one."""

    def test_older_pinned_before_newer_unpinned(self, repo, engine_factory):
        """The canonical assertion: an OLD pinned instance must sort BEFORE a
        NEW unpinned instance. This is the whole point of the feature."""
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # Three instances with increasing created_at (newest last).
        _make_instance(repo, "old-unpinned", created_at=_iso(2026, 1, 1))
        _make_instance(repo, "older-pinned", created_at=_iso(2026, 1, 2))
        _make_instance(repo, "new-unpinned", created_at=_iso(2026, 1, 10))

        # Pin the OLDEST-but-one instance.
        _set_pref(engine, "older-pinned", pinned=True, pinned_at=_iso(2026, 7, 1))

        instances, total = repo.list(exclude_kb=False)

        ids = [i.instance_id for i in instances]
        assert total == 3, f"expected total=3, got {total}; ids={ids}"

        # The pinned instance must be FIRST despite being older than new-unpinned.
        assert ids == ["older-pinned", "new-unpinned", "old-unpinned"], (
            f"Pinned-first ordering broken: got {ids}. "
            f"Expected pinned 'older-pinned' first, then unpinned by created_at DESC."
        )

    def test_all_pinned_precede_all_unpinned(self, repo, engine_factory):
        """With 2 pinned + 2 unpinned, every pinned instance precedes every
        unpinned instance, regardless of created_at."""
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # Unpinned instances (varied created_at).
        _make_instance(repo, "u-old", created_at=_iso(2026, 1, 1))
        _make_instance(repo, "u-new", created_at=_iso(2026, 6, 1))

        # Pinned instances (varied created_at — note u-new is NEWER than the
        # pinned ones; pinned must still win).
        _make_instance(repo, "p-old", created_at=_iso(2026, 2, 1))
        _make_instance(repo, "p-new", created_at=_iso(2026, 3, 1))
        _set_pref(engine, "p-old", pinned=True, pinned_at=_iso(2026, 7, 2))
        _set_pref(engine, "p-new", pinned=True, pinned_at=_iso(2026, 7, 1))

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 4
        pinned_block = {i for i in ids[:2]}
        unpinned_block = {i for i in ids[2:]}
        assert pinned_block == {"p-old", "p-new"}, (
            f"Expected pinned instances in first 2 positions, got {ids}"
        )
        assert unpinned_block == {"u-old", "u-new"}, (
            f"Expected unpinned instances in last 2 positions, got {ids}"
        )


# =============================================================================
# Scenario 2 — Pagination correctness
# =============================================================================


class TestPaginationCorrectness:
    """Pinned instances are concentrated on page 1, not scattered."""

    def test_pinned_concentrated_on_page1(self, repo, engine_factory):
        """6 instances, 2 pinned. With limit=2, page 1 must contain BOTH
        pinned instances (they sort to the top), and pages 2-3 contain only
        unpinned instances."""
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # Six unpinned-by-default instances with distinct created_at.
        # Created oldest→newest so created_at DESC gives newest first.
        for i in range(6):
            _make_instance(
                repo, f"inst-{i}", created_at=_iso(2026, 1, 1, i)
            )

        # Pin the two OLDEST instances (inst-0, inst-1). Despite being oldest,
        # they must float to page 1.
        _set_pref(engine, "inst-0", pinned=True, pinned_at=_iso(2026, 7, 1))
        _set_pref(engine, "inst-1", pinned=True, pinned_at=_iso(2026, 6, 30))

        # Page 1: limit=2, offset=0 → the two pinned instances.
        page1, total = repo.list(limit=2, offset=0, exclude_kb=False)
        ids_p1 = [i.instance_id for i in page1]
        assert total == 6, f"expected total=6, got {total}"
        assert set(ids_p1) == {"inst-0", "inst-1"}, (
            f"Page 1 must contain both pinned instances; got {ids_p1}. "
            f"Pinned instances must be concentrated on page 1, not scattered."
        )

        # Page 2: limit=2, offset=2 → unpinned, newest first (inst-5, inst-4).
        page2, _ = repo.list(limit=2, offset=2, exclude_kb=False)
        ids_p2 = [i.instance_id for i in page2]
        assert "inst-0" not in ids_p2 and "inst-1" not in ids_p2, (
            f"Pinned instances leaked onto page 2: {ids_p2}"
        )

        # Page 3: limit=2, offset=4 → remaining unpinned.
        page3, _ = repo.list(limit=2, offset=4, exclude_kb=False)
        ids_p3 = [i.instance_id for i in page3]
        assert "inst-0" not in ids_p3 and "inst-1" not in ids_p3, (
            f"Pinned instances leaked onto page 3: {ids_p3}"
        )

        # All 6 instances covered exactly once across the 3 pages.
        all_ids = ids_p1 + ids_p2 + ids_p3
        assert sorted(all_ids) == [f"inst-{i}" for i in range(6)], (
            f"Pagination did not cover all instances exactly once: {all_ids}"
        )

    def test_pagination_10plus_false_and_null_unpinned_on_page1(
        self, repo, engine_factory
    ):
        """10+ instances across multiple pages. Pinned concentrated on page 1;
        page 1 ALSO contains newer unpinned of BOTH kinds (explicit FALSE and
        NULL) after the pinned block — they tiebreak purely on created_at DESC.

        This closes the gap left by ``test_pinned_concentrated_on_page1``
        (6 instances, page-1 = pinned-only, no FALSE/NULL mix on page 1).
        """
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # 12 instances, created oldest→newest so created_at DESC = newest first.
        for i in range(12):
            _make_instance(repo, f"inst-{i:02d}", created_at=_iso(2026, 1, 1, i))

        # Pin the two OLDEST (inst-00, inst-01) — they must float to page 1.
        _set_pref(engine, "inst-00", pinned=True, pinned_at=_iso(2026, 7, 2))
        _set_pref(engine, "inst-01", pinned=True, pinned_at=_iso(2026, 7, 1))

        # Mark inst-05..inst-09 as explicit pinned=False; the rest stay NULL.
        for i in range(5, 10):
            _set_pref(engine, f"inst-{i:02d}", pinned=False, pinned_at=None)

        page1, total = repo.list(limit=5, offset=0, exclude_kb=False)
        ids_p1 = [i.instance_id for i in page1]
        assert total == 12, f"expected total=12, got {total}"
        # [pinned inst-00, inst-01] then newest unpinned by created_at DESC:
        # inst-11 (NULL), inst-10 (NULL), inst-09 (explicit FALSE).
        assert ids_p1 == ["inst-00", "inst-01", "inst-11", "inst-10", "inst-09"], (
            f"Page 1 must start with pinned, then newest unpinned incl. an "
            f"explicit FALSE (inst-09) alongside NULL (inst-11/inst-10); got "
            f"{ids_p1}."
        )

        # Pinned must NOT leak onto later pages.
        page2, _ = repo.list(limit=5, offset=5, exclude_kb=False)
        ids_p2 = [i.instance_id for i in page2]
        assert "inst-00" not in ids_p2 and "inst-01" not in ids_p2, (
            f"Pinned instances leaked onto page 2: {ids_p2}"
        )
        page3, _ = repo.list(limit=5, offset=10, exclude_kb=False)
        ids_p3 = [i.instance_id for i in page3]
        assert "inst-00" not in ids_p3 and "inst-01" not in ids_p3, (
            f"Pinned instances leaked onto page 3: {ids_p3}"
        )

        # All 12 instances covered exactly once across the 3 pages.
        all_ids = ids_p1 + ids_p2 + ids_p3
        assert sorted(all_ids) == [f"inst-{i:02d}" for i in range(12)], (
            f"Pagination did not cover all 12 instances exactly once: {all_ids}"
        )


# =============================================================================
# Scenario 3 — No prefs row (NULL handling)
# =============================================================================


class TestNoPrefsRowNullHandling:
    """Instances WITHOUT an ``instance_ui_prefs`` row (the LEFT JOIN yields
    NULL pinned/pinned_at) must sort as unpinned — i.e. AFTER any pinned
    instance. This is the NULLS LAST behaviour and is crucial."""

    def test_no_prefs_row_sorts_after_pinned(self, repo, engine_factory):
        """A pinned instance with a prefs row must precede an instance that
        has NO prefs row at all, even if the no-prefs instance is newer."""
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # Instance WITH a prefs row, pinned.
        _make_instance(repo, "pinned-one", created_at=_iso(2026, 1, 1))
        _set_pref(engine, "pinned-one", pinned=True, pinned_at=_iso(2026, 7, 1))

        # Instance WITHOUT any prefs row (no _set_pref call) — newer than pinned.
        _make_instance(repo, "no-prefs-new", created_at=_iso(2026, 6, 1))

        # Another no-prefs instance — oldest of all.
        _make_instance(repo, "no-prefs-old", created_at=_iso(2025, 12, 1))

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 3
        # Pinned instance first, then the two no-prefs instances by created_at DESC.
        assert ids == ["pinned-one", "no-prefs-new", "no-prefs-old"], (
            f"NULLS LAST handling broken: got {ids}. "
            f"Expected pinned instance first, then no-prefs instances by "
            f"created_at DESC. Instances without a prefs row must NOT sort "
            f"above a pinned instance."
        )

    def test_explicit_false_treated_same_as_null_prefs(self, repo, engine_factory):
        """An instance with an explicit ``pinned=False`` prefs row is treated
        EQUIVALENTLY to an instance with no prefs row (``pinned=NULL``) — both
        are "unpinned" and tiebreak purely on ``created_at DESC``.

        The new ``CASE`` expression maps both ``pinned=False`` and ``pinned=NULL``
        to tier 0; only ``pinned=True`` floats to tier 1. So a NEWER
        never-touched instance now wins over an OLDER explicitly-unpinned one
        — the legacy DESC-NULLS-LAST behaviour that pushed unpinned-but-touched
        rows above never-touched rows was a bug confirmed against production.
        """
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # Explicit unpinned prefs row, OLDER created_at.
        _make_instance(repo, "explicit-unpinned", created_at=_iso(2026, 1, 1))
        _set_pref(engine, "explicit-unpinned", pinned=False, pinned_at=None)

        # No prefs row at all, NEWER created_at.
        _make_instance(repo, "no-prefs", created_at=_iso(2026, 2, 1))

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 2
        # Both are tier 0 (unpinned); order comes purely from created_at DESC,
        # so the NEWER no-prefs instance wins over the older explicit-unpinned one.
        assert ids == ["no-prefs", "explicit-unpinned"], (
            f"Expected explicit pinned=False and no-prefs (NULL) to be treated "
            f"equivalently (both unpinned) and tiebreak on created_at DESC; "
            f"got {ids}. The newer no-prefs instance must come before the "
            f"older explicit-unpinned one."
        )


# =============================================================================
# Scenario 4 — Multiple pinned instances (pinned_at DESC)
# =============================================================================


class TestMultiplePinnedOrdering:
    """Among pinned instances, the most recently pinned (pinned_at DESC)
    comes first."""

    def test_most_recently_pinned_first(self, repo, engine_factory):
        """Three pinned instances with distinct pinned_at values. The one with
        the LATEST pinned_at must come first among the pinned block."""
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # All created at the same time so created_at is NOT the tiebreaker —
        # pinned_at must decide.
        base = _iso(2026, 3, 1)
        _make_instance(repo, "pin-early", created_at=base)
        _make_instance(repo, "pin-mid", created_at=base)
        _make_instance(repo, "pin-late", created_at=base)

        # Pin at different times. The earliest pin should sort LAST among pinned.
        _set_pref(engine, "pin-early", pinned=True, pinned_at=_iso(2026, 6, 1))
        _set_pref(engine, "pin-mid", pinned=True, pinned_at=_iso(2026, 6, 15))
        _set_pref(engine, "pin-late", pinned=True, pinned_at=_iso(2026, 7, 1))

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 3
        # pinned_at DESC: pin-late (Jul 1) > pin-mid (Jun 15) > pin-early (Jun 1).
        assert ids == ["pin-late", "pin-mid", "pin-early"], (
            f"Among pinned instances, most-recently-pinned must come first; "
            f"got {ids}. Expected pin-late (Jul 1) > pin-mid (Jun 15) > "
            f"pin-early (Jun 1) by pinned_at DESC."
        )

    def test_pinned_at_desc_beats_created_at_desc(self, repo, engine_factory):
        """pinned_at is a STRONGER sort key than created_at among pinned
        instances. A pinned instance with an EARLIER created_at but a LATER
        pinned_at must come first."""
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        _make_instance(repo, "a-created-early", created_at=_iso(2026, 1, 1))
        _make_instance(repo, "b-created-late", created_at=_iso(2026, 5, 1))

        # Pin the early-created one LATER → it should win.
        _set_pref(engine, "a-created-early", pinned=True, pinned_at=_iso(2026, 7, 5))
        _set_pref(engine, "b-created-late", pinned=True, pinned_at=_iso(2026, 7, 1))

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 2
        # a-created-early has later pinned_at → comes first despite earlier created_at.
        assert ids == ["a-created-early", "b-created-late"], (
            f"pinned_at DESC must dominate created_at DESC among pinned instances; "
            f"got {ids}. Expected a-created-early (later pinned_at) first."
        )


# =============================================================================
# Scenario 5 — Mixed TRUE / FALSE / NULL (the exact bug scenario)
# =============================================================================


class TestMixedTrueFalseNullBugFix:
    """The confirmed production bug: explicit ``pinned=False`` rows sorted
    above ``pinned=NULL`` rows under DESC NULLS LAST, pushing newer never-
    pinned instances to page 2.

    The ``CASE`` fix maps only ``pinned=True`` to tier 1; both
    ``pinned=False`` and ``pinned=NULL`` are tier 0 and tiebreak on
    ``created_at DESC``."""

    def test_mixed_true_false_null_true_floats_then_created_at(self, repo, engine_factory):
        """Three instances:
        1. pinned=True   (OLDEST created_at)
        2. pinned=False  (MIDDLE created_at)
        3. pinned=NULL   (NEWEST created_at — no prefs row)

        Expected order: [TRUE, NULL-newest, FALSE-middle]

        The TRUE instance floats to tier 1 regardless of created_at.
        Among tier-0 (unpinned), order comes purely from created_at DESC,
        so the newest (NULL) comes before the middle (FALSE).
        """
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        # Instance 1: pinned=True, OLDEST created_at.
        _make_instance(repo, "is-pinned", created_at=_iso(2026, 1, 1))
        _set_pref(engine, "is-pinned", pinned=True, pinned_at=_iso(2026, 7, 1))

        # Instance 2: pinned=False, MIDDLE created_at.
        _make_instance(repo, "is-false", created_at=_iso(2026, 2, 1))
        _set_pref(engine, "is-false", pinned=False, pinned_at=None)

        # Instance 3: no prefs row (pinned=NULL), NEWEST created_at.
        _make_instance(repo, "is-null", created_at=_iso(2026, 3, 1))

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 3
        # TRUE floats to top (tier 1); the two tier-0 unpinned instances
        # order by created_at DESC: is-null (newest) before is-false (middle).
        assert ids == ["is-pinned", "is-null", "is-false"], (
            f"Expected [TRUE, NULL-newest, FALSE-middle]; got {ids}. "
            f"pinned=True must float to top; unpinned instances (FALSE and NULL) "
            f"must tiebreak by created_at DESC, not by prefs-row existence."
        )


# =============================================================================
# Scenario 6 — Stable tiebreaker: FALSE + NULL with identical created_at
# =============================================================================


class TestFalseNullTiebreakerSameCreatedAt:
    """When an explicit ``pinned=False`` and a never-pinned (``pinned=NULL``)
    instance share the SAME ``created_at``, both are tier 0 and the order is
    decided by the final stable tiebreaker ``instance_id ASC``.

    This is the one gap in the existing FALSE-vs-NULL coverage: the prior test
    (``test_explicit_false_treated_same_as_null_prefs``) used DISTINCT
    created_at values, so it exercised created_at DESC but never the
    instance_id ASC final tiebreak between the two unpinned kinds."""

    def test_false_and_null_same_created_at_tiebreak_instance_id_asc(
        self, repo, engine_factory
    ):
        """Two instances, identical created_at:
        - ``id-before``: explicit pinned=False
        - ``id-after``:  no prefs row (pinned=NULL)

        With identical tier (0) and identical created_at, ``instance_id ASC``
        is the only remaining sort key, so ``id-before`` precedes
        ``id-after`` regardless of pinned-state (FALSE vs NULL).
        """
        _, engine = engine_factory()
        repo = SQLModelInstanceRepository(engine)

        same_created = _iso(2026, 3, 1)
        _make_instance(repo, "id-before", created_at=same_created)
        _set_pref(engine, "id-before", pinned=False, pinned_at=None)

        _make_instance(repo, "id-after", created_at=same_created)

        instances, total = repo.list(exclude_kb=False)
        ids = [i.instance_id for i in instances]

        assert total == 2
        # instance_id ASC: "id-after" < "id-before" lexicographically → id-after first.
        assert ids == ["id-after", "id-before"], (
            f"With identical created_at, FALSE vs NULL must tiebreak by "
            f"instance_id ASC; got {ids}. Expected id-after < id-before."
        )
