"""Repository-layer tests for the ``order`` kwarg on
``SQLModelInstanceRepository.list`` (root-based pagination path).

Locks the two root orderings:

* ``order="pinned"`` (DEFAULT) — byte-compatible with the historical
  pinned-first behavior: pinned tier (``pinned_at`` DESC), unpinned tier,
  ``created_at`` DESC, ``instance_id`` ASC. The default no-kwarg call and the
  explicit ``order="pinned"`` call MUST produce identical sequences.
* ``order="activity"`` — live (non-terminal) roots first, then
  ``updated_at`` DESC, ``created_at`` DESC, ``instance_id`` ASC. Pins can
  never push a live conversation off the page.

Terminal set contract: ``{"completed", "error", "terminated", "failed"}``
(mirrors ``TERMINAL_INSTANCE_STATUSES`` and the FE
``isTerminalInstanceStatus`` complement). Timestamps are NOT NULL in the
ORM schema (verified against ``CreateTable`` DDL), so the explicit
``NULLS LAST`` on the activity tiebreaks is defense-in-depth against
backend default divergence, not a reachable-data path.

Fixture wiring mirrors ``tests/unit/test_instance_tree_loading.py``
(real ``SQLModelInstanceRepository`` over in-memory SQLite with the full
``SQLModel.metadata`` schema).

Run standalone:

    timeout 300 .venv/bin/pytest tests/unit/test_instance_list_order_repository.py --tb=short -q
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import (
    TERMINAL_INSTANCE_STATUSES,
    SQLModelInstanceRepository,
)
from daemon.repositories.instance_ui_prefs.repository import (
    InstanceUiPrefsRepository,
)


# =============================================================================
# FIXTURES
# =============================================================================

# Fixed ISO-8601 UTC timestamps (lexicographic == chronological). Distinct
# values so every ordering expectation below is unambiguous.
T0 = "2026-09-01T10:00:00+00:00"  # oldest
T1 = "2026-09-02T10:00:00+00:00"
T2 = "2026-09-03T10:00:00+00:00"
T3 = "2026-09-04T10:00:00+00:00"
T4 = "2026-09-05T10:00:00+00:00"
T5 = "2026-09-06T10:00:00+00:00"  # newest


@pytest.fixture
def repo():
    """In-memory SQLite repository with real schema (mirrors
    ``test_instance_tree_loading.py::repo``)."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def ui_prefs(repo):
    """UI-prefs repository over the same engine — drives the pinned tier."""
    return InstanceUiPrefsRepository(repo.engine)


def _root(
    instance_id: str,
    *,
    status: str = "idle",
    created_at: str = T0,
    updated_at: str | None = None,
    agent_id: str = "developer",
) -> Instance:
    """Build a ROOT ``Instance`` row (not committed) with explicit,
    deterministic timestamps."""
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"./agents/{agent_id}",
        parent_id=None,
        status=status,
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
        version=1,
        instance_metadata={},
    )


def _seed(repo: SQLModelInstanceRepository, *rows: Instance) -> None:
    with Session(repo.engine) as s:
        for row in rows:
            s.add(row)
        s.commit()


def _root_ids(instances: list[Instance]) -> list[str]:
    """Root-page ids in returned order (descendants excluded from view)."""
    return [i.instance_id for i in instances if i.parent_id is None] or [
        i.instance_id for i in instances
    ]


# =============================================================================
# Byte-compat pin — default (no kwarg) == explicit "pinned", historical order
# =============================================================================


class TestPinnedDefaultByteCompat:
    """Default ordering MUST be byte-compatible with pre-change behavior."""

    @pytest.fixture
    def seeded(self, repo, ui_prefs):
        """Mixed pinned/unpinned/old/new root set.

        Layout (all roots):
            id         pinned   pinned_at   created_at
            ---------  -------  ---------   ----------
            pin-new    True     later       T1 (old)
            pin-old    True     earlier     T0 (oldest)
            free-new   False    —           T5 (newest)
            free-mid   False    —           T3
            free-old   False    —           T2

        Upsert order sets ``pinned_at`` (later upsert ⇒ later pinned_at).
        ``pin-new`` also carries the NEWER created_at of the two pinned
        roots, so the expected order is identical whether or not the two
        ``pinned_at`` values land in the same microsecond (tiebreak
        ``created_at`` DESC agrees with ``pinned_at`` DESC here).
        """
        _seed(
            repo,
            _root("pin-old", status="completed", created_at=T0),
            _root("pin-new", status="completed", created_at=T1),
            _root("free-new", status="idle", created_at=T5),
            _root("free-mid", status="idle", created_at=T3),
            _root("free-old", status="idle", created_at=T2),
        )
        ui_prefs.upsert("pin-old", pinned=True)
        ui_prefs.upsert("pin-new", pinned=True)
        return repo

    def test_default_no_kwarg_exact_order(self, seeded):
        """BYTE-COMPAT PIN (repo layer): no kwarg → pinned tier first
        (pinned_at DESC), then unpinned by created_at DESC."""
        instances, total = seeded.list(include_descendants=True)
        assert total == 5
        assert _root_ids(instances) == [
            "pin-new", "pin-old",              # pinned tier: pinned_at DESC
            "free-new", "free-mid", "free-old",  # unpinned: created_at DESC
        ]

    def test_explicit_pinned_identical_to_default(self, seeded):
        """``order="pinned"`` MUST equal the no-kwarg default exactly."""
        default_instances, default_total = seeded.list(include_descendants=True)
        pinned_instances, pinned_total = seeded.list(
            include_descendants=True, order="pinned"
        )
        assert pinned_total == default_total
        assert _root_ids(pinned_instances) == _root_ids(default_instances)

    def test_pinned_tier_beats_newest_unpinned(self, seeded):
        """The historical contract: a pinned root outranks ANY unpinned root
        regardless of recency — 'free-new' (newest created_at) stays below
        the pinned tier."""
        instances, _ = seeded.list(include_descendants=True)
        ids = _root_ids(instances)
        assert ids.index("pin-old") < ids.index("free-new")

    def test_flat_path_ignores_order_kwarg(self, seeded):
        """The flat path (``include_descendants=False``) keeps the historical
        pinned-first ordering regardless of ``order`` — internal callers
        (cache cleanup, fuzzy match, tools) are untouched by this change."""
        flat_default, total_default = seeded.list(include_descendants=False)
        flat_activity, total_activity = seeded.list(
            include_descendants=False, order="activity"
        )
        assert total_default == total_activity
        assert [i.instance_id for i in flat_activity] == [
            i.instance_id for i in flat_default
        ]
        # And it is still the pinned-first order, NOT activity order.
        assert [i.instance_id for i in flat_activity][0] == "pin-new"


# =============================================================================
# Activity ordering
# =============================================================================


class TestActivityOrdering:
    """``order="activity"``: live roots first, then updated_at DESC."""

    @pytest.fixture
    def seeded(self, repo, ui_prefs):
        """Pinned TERMINAL root + unpinned fresh LIVE root + older unpinned
        live roots — the production failure shape (pinned roots owning the
        page while a fresh live conversation is pushed off)."""
        _seed(
            repo,
            # Pinned but terminal: under pinned ordering it owns the page top.
            _root("pin-stale", status="completed", created_at=T0, updated_at=T1),
            # Fresh unpinned live root (the leader that never appeared).
            _root("fresh-live", status="running", created_at=T5, updated_at=T5),
            # Older unpinned live root.
            _root("older-live", status="idle", created_at=T2, updated_at=T2),
        )
        ui_prefs.upsert("pin-stale", pinned=True)
        return repo

    def test_unpinned_fresh_live_above_older_pinned_terminal(self, seeded):
        """The motivating case: the unpinned fresh live root must rank ABOVE
        the pinned older terminal root in activity ordering."""
        instances, total = seeded.list(include_descendants=True, order="activity")
        assert total == 3
        assert _root_ids(instances) == ["fresh-live", "older-live", "pin-stale"]

    def test_default_pinned_order_unchanged_for_same_fixture(self, seeded):
        """Contrast pin on the SAME fixture: default ordering still puts the
        pinned root first (proves the fixture really distinguishes the two
        orderings — guards against a vacuous activity test)."""
        instances, _ = seeded.list(include_descendants=True)
        assert _root_ids(instances)[0] == "pin-stale"


class TestActivityLiveAboveAllTerminal:
    """Every live status outranks every terminal status, pins notwithstanding."""

    LIVE_STATUSES = ("idle", "running", "waiting", "paused", "queued", "waiting_children")
    TERMINAL_STATUSES = ("completed", "error", "terminated", "failed")

    def test_terminal_set_matches_contract(self):
        """The shipped terminal set is exactly the FE
        ``isTerminalInstanceStatus`` cluster."""
        assert TERMINAL_INSTANCE_STATUSES == self.TERMINAL_STATUSES
        live = {"idle", "running", "waiting", "paused", "queued", "waiting_children"}
        assert live.isdisjoint(set(TERMINAL_INSTANCE_STATUSES))
        # Total split: live ∪ terminal == full enum value set.
        from daemon.repositories.instance.models import InstanceStatus

        assert live | set(TERMINAL_INSTANCE_STATUSES) == {s.value for s in InstanceStatus}

    def test_all_live_above_all_terminal_ordered_by_updated_at(self, repo):
        """6 live roots (one per live status, pins on the OLDEST terminal
        rows) + 4 terminal roots → live tier first ordered by updated_at
        DESC, then terminal tier ordered by updated_at DESC."""
        rows = [
            # Live tier — updated_at staggered NEWEST→OLDEST by construction.
            _root("live-running", status="running", created_at=T0, updated_at=T5),
            _root("live-idle", status="idle", created_at=T0, updated_at=T4),
            _root("live-waiting", status="waiting", created_at=T0, updated_at=T3),
            _root("live-paused", status="paused", created_at=T0, updated_at=T2),
            _root("live-queued", status="queued", created_at=T0, updated_at=T1),
            _root("live-waiting-children", status="waiting_children", created_at=T0, updated_at=T0),
            # Terminal tier — updated_at DESC within the tier (T3→T0 offsets).
            _root("term-completed", status="completed", created_at=T0, updated_at="2026-09-04T09:00:00+00:00"),
            _root("term-error", status="error", created_at=T0, updated_at="2026-09-04T08:00:00+00:00"),
            _root("term-terminated", status="terminated", created_at=T0, updated_at="2026-09-04T07:00:00+00:00"),
            _root("term-failed", status="failed", created_at=T0, updated_at="2026-09-04T06:00:00+00:00"),
        ]
        _seed(repo, *rows)
        # Pin the two OLDEST live rows AND one terminal row — pins must NOT
        # reorder the activity tiers.
        ui = InstanceUiPrefsRepository(repo.engine)
        ui.upsert("live-queued", pinned=True)
        ui.upsert("live-waiting-children", pinned=True)
        ui.upsert("term-completed", pinned=True)

        instances, total = repo.list(include_descendants=True, order="activity")
        assert total == 10
        assert _root_ids(instances) == [
            # Live tier: updated_at DESC — pins on live-queued/-waiting-children
            # do NOT float them above newer live roots.
            "live-running",
            "live-idle",
            "live-waiting",
            "live-paused",
            "live-queued",
            "live-waiting-children",
            # Terminal tier: updated_at DESC — pin on term-completed does NOT
            # lift it above the tier boundary.
            "term-completed",
            "term-error",
            "term-terminated",
            "term-failed",
        ]

    def test_updated_at_dominates_created_at_within_tier(self, repo):
        """A root with an OLDER created_at but NEWER updated_at outranks a
        root with newer created_at / older updated_at (same tier)."""
        _seed(
            repo,
            _root("old-born-recently-active", status="idle", created_at=T0, updated_at=T5),
            _root("new-born-stale", status="idle", created_at=T4, updated_at=T1),
        )
        instances, _ = repo.list(include_descendants=True, order="activity")
        assert _root_ids(instances) == ["old-born-recently-active", "new-born-stale"]

    def test_equal_timestamps_tiebreak_instance_id_asc(self, repo):
        """Identical timestamps → deterministic ``instance_id`` ASC."""
        _seed(
            repo,
            _root("zzz", status="idle", created_at=T3, updated_at=T3),
            _root("aaa", status="idle", created_at=T3, updated_at=T3),
            _root("mmm", status="idle", created_at=T3, updated_at=T3),
        )
        instances, _ = repo.list(include_descendants=True, order="activity")
        assert _root_ids(instances) == ["aaa", "mmm", "zzz"]
