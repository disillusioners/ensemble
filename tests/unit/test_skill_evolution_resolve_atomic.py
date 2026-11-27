"""Unit pack — the ATOMIC + CAS A/B resolve path (wi-4d1fa093, part CAS).

Pre-fix, the sanctioned resolver applied its three terminal mutations
as three independent ``asyncio.gather`` transactions — no lock, no
compare-and-set. Two failure shapes followed:

* **Partial resolve** — one transaction committed while another
  failed, leaving the pair half resolved (pair row written, winner
  still bound to the group, or loser deactivated with the winner never
  re-activated).
* **Double resolve** — two concurrent resolvers interleaved their
  mutations on the same pair.

The fix claims the pair row with a CAS UPDATE (``resolved_at IS NULL``,
plus ``comparisons = 0`` on the stalled basis) and applies ALL THREE
mutations inside ONE transaction.

Covered here:

* ``SkillABTestRepository.resolve_atomic`` — CAS win / CAS loss /
  zero-traffic guard / mid-transaction failure (zero partial state).
* ``SkillEvolutionService.resolve_ab_test_sanctioned`` — the service
  path that calls it, including the concurrent duplicate-resolve case
  (exactly-once).

The tests run against a FILE-backed SQLite database (one connection
per thread) — see the ``engine`` fixture below. No live DB contact,
no production writes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.skill.models import (
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import (
    SkillABTestRepository,
    SkillRepository,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures — file-backed SQLite (one connection per thread)

@pytest.fixture(autouse=True)
def _fence_data_dir(monkeypatch, tmp_path):
    """Hermeticity fence: keep the capture-observability sink under tmp_path.

    resolve_data_dir is env-first with a CWD-relative "./data" fallback;
    without this, the pack appends records to data/skill-capture-observability/.
    Non-behavioral: no assertion in this pack touches the sink and sink
    append failures are swallowed.
    """
    monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
# ─────────────────────────────────────────────────────────────────────


def _enable_sqlite_foreign_keys(engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine(tmp_path):
    """A FILE-backed SQLite engine (one connection per thread).

    The in-memory ``StaticPool`` pattern used elsewhere in the repo
    hands EVERY thread the SAME DBAPI connection — two concurrent
    transactions then share one transaction, and the loser's rollback
    silently discards the winner's committed work (observed). A
    file-backed DB with the default pool gives each thread its own
    connection, so the CAS behaves the way it does on PostgreSQL: the
    loser blocks on the write lock, wakes after the winner commits,
    re-evaluates its predicate and matches ZERO rows.
    """
    db_path = tmp_path / "skill_ab_atomic.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    _enable_sqlite_foreign_keys(eng)
    _ = (Skill, SkillLineage, SkillUsageRecord, SkillTrigger,
         SkillEmbedding, SkillABTest)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def skill_repo(engine):
    return SkillRepository(engine)


@pytest.fixture
def ab_test_repo(engine):
    return SkillABTestRepository(engine)


def _make_pair(skill_repo, ab_test_repo, *, group="ab-atomic", comparisons=0,
               old_generation=0, new_generation=1, age_days=30):
    """Create two variant skills + an unresolved (aged) pair row."""
    old = skill_repo.create(
        name="variant-old", description="old", content="old",
        project_id="p-atomic", generation=old_generation,
        ab_test_group=group,
    )
    new = skill_repo.create(
        name="variant-new", description="new", content="new",
        project_id="p-atomic", generation=new_generation,
        ab_test_group=group,
    )
    ab_test_repo.create_ab_test(
        ab_test_group=group,
        skill_id_old=old.id,
        skill_id_new=new.id,
    )
    if age_days:
        # The stalled basis is age-gated — backdate the pair so the
        # stall predicate is satisfied (the age is NEVER used for
        # winner selection — E-6 decides on generation number).
        with Session(ab_test_repo.engine) as session:
            row = session.exec(
                select(SkillABTest).where(
                    SkillABTest.ab_test_group == group
                )
            ).first()
            row.created_at = (
                datetime.now(timezone.utc) - timedelta(days=age_days)
            ).isoformat()
            session.add(row)
            session.commit()
    row = ab_test_repo.get_by_group(group)
    if comparisons:
        for _ in range(comparisons):
            ab_test_repo.increment_comparison(group)
        row = ab_test_repo.get_by_group(group)
    return old, new, row


# ─────────────────────────────────────────────────────────────────────
# F2 — winner activeness + write-once counting helpers
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def _skill_write_spy(engine, request):
    """Count per-row writes to the ``skills`` table across a window.

    Two independent counters:

    * ``dirty`` — ORM dirty objects (of type :class:`Skill`) observed
      at each ``before_flush`` — proves each ROW OBJECT is mutated
      exactly once per resolve.
    * ``updates`` — compiled ``UPDATE skills ...`` statements captured
      via ``before_cursor_execute`` — proves the row writes actually
      reach the database exactly once (the pair-row UPDATE targets
      ``skillabtest`` and is filtered out by the table-name prefix).

    The spy exposes ``on_flush`` / ``on_exec`` (the listener functions)
    and a ``cleanup()`` method for deterministic teardown, plus
    ``dirty`` / ``updates`` views of the same counters.

    The listeners are REMOVED in teardown (both of them) so they do
    not leak into the rest of the pytest session or into any later
    test that touches a SQLModel session.
    """
    import daemon.repositories.skill.repository as repo_mod

    # Plain counters dict — the listeners close over THIS, not over
    # the spy object, so the spy stays collectable.
    counters = {"dirty": {}, "updates": {}}

    def _on_flush(session, flush_context, instances):
        for obj in session.dirty:
            if isinstance(obj, Skill):
                counters["dirty"][obj.id] = (
                    counters["dirty"].get(obj.id, 0) + 1
                )

    def _on_exec(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE SKILLS"):
            # SQLModel binds the WHERE primary key LAST in the
            # parameter tuple for its single-row UPDATE shape.
            row_id = None
            if isinstance(parameters, (tuple, list)) and parameters:
                row_id = parameters[-1]
            elif isinstance(parameters, dict):
                row_id = parameters.get("id")
            if row_id is not None:
                counters["updates"][row_id] = (
                    counters["updates"].get(row_id, 0) + 1
                )

    def _cleanup():
        try:
            event.remove(repo_mod.Session, "before_flush", _on_flush)
        except Exception:  # noqa: BLE001
            pass
        try:
            event.remove(engine, "before_cursor_execute", _on_exec)
        except Exception:  # noqa: BLE001
            pass

    class _Spy:
        __slots__ = (
            "dirty", "updates", "on_flush", "on_exec", "cleanup",
            "__weakref__",
        )

    spy = _Spy()
    spy.dirty = counters["dirty"]
    spy.updates = counters["updates"]
    spy.on_flush = _on_flush
    spy.on_exec = _on_exec
    spy.cleanup = _cleanup

    event.listens_for(repo_mod.Session, "before_flush")(_on_flush)
    event.listens_for(engine, "before_cursor_execute")(_on_exec)

    request.addfinalizer(_cleanup)

    return spy


# ─────────────────────────────────────────────────────────────────────
# Repository level — the CAS + single-transaction contract
# ─────────────────────────────────────────────────────────────────────


class TestResolveAtomicCAS:
    def test_first_resolve_wins_and_applies_all_three_mutations(
        self, skill_repo, ab_test_repo
    ):
        """CAS win ⇒ pair row resolved AND both skill rows updated."""
        old, new, _row = _make_pair(skill_repo, ab_test_repo)

        won = ab_test_repo.resolve_atomic(
            "ab-atomic", new.id, old.id,
        )

        assert won is True

        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.resolved_at is not None
        assert pair.winner_skill_id == new.id

        loser_row = skill_repo.get(old.id)
        assert loser_row.is_active is False
        assert loser_row.status == "inactive"
        # The loser RETAINS its ab_test_group (pinned contract).
        assert loser_row.ab_test_group == "ab-atomic"

        winner_row = skill_repo.get(new.id)
        assert winner_row.status == "active"
        # F2: the winner write also restores servability — the loser
        # side sets ``is_active=False`` so the winner side MUST set
        # ``is_active=True`` (same transaction, same commit).
        assert winner_row.is_active is True
        assert winner_row.ab_test_group is None

    def test_winner_and_loser_are_each_written_exactly_once(
        self, skill_repo, ab_test_repo, engine, _skill_write_spy
    ):
        """F2: a resolution writes the WINNER and the LOSER exactly
        once each — one transaction, one flush, no double-write of
        either row (and the pair-row UPDATE is not a skills write)."""
        old, new, _row = _make_pair(skill_repo, ab_test_repo)

        spy = _skill_write_spy

        won = ab_test_repo.resolve_atomic("ab-atomic", new.id, old.id)
        assert won is True

        # Terminal activeness: winner servable, loser not.
        winner_row = skill_repo.get(new.id)
        loser_row = skill_repo.get(old.id)
        assert winner_row.is_active is True
        assert winner_row.status == "active"
        assert loser_row.is_active is False
        assert loser_row.status == "inactive"

        # Exactly-once per row: one dirty mutation and one UPDATE
        # statement per skill row — nothing more.
        assert spy.dirty == {old.id: 1, new.id: 1}
        assert spy.updates == {old.id: 1, new.id: 1}

    def test_duplicate_resolve_is_a_noop(self, skill_repo, ab_test_repo):
        """The second resolver LOSES the CAS — exactly-once."""
        old, new, _row = _make_pair(skill_repo, ab_test_repo)

        assert ab_test_repo.resolve_atomic("ab-atomic", new.id, old.id) is True
        assert ab_test_repo.resolve_atomic("ab-atomic", new.id, old.id) is False

    def test_duplicate_resolve_does_not_overwrite_the_winner(
        self, skill_repo, ab_test_repo
    ):
        """A CAS loser cannot steal the pair from the first winner."""
        old, new, _row = _make_pair(skill_repo, ab_test_repo)

        ab_test_repo.resolve_atomic("ab-atomic", new.id, old.id)
        # A second resolver naming the OTHER variant as winner.
        ab_test_repo.resolve_atomic("ab-atomic", old.id, new.id)

        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.winner_skill_id == new.id
        winner_row = skill_repo.get(new.id)
        assert winner_row.status == "active"
        assert winner_row.ab_test_group is None
        loser_row = skill_repo.get(old.id)
        assert loser_row.status == "inactive"

    def test_zero_comparisons_guard_rejects_a_traffic_bearing_pair(
        self, skill_repo, ab_test_repo
    ):
        """``comparisons = 0`` in the CAS predicate ⇒ traffic-bearing
        pairs are NEVER resolved by the zero-traffic path."""
        old, new, _row = _make_pair(
            skill_repo, ab_test_repo, comparisons=3,
        )

        won = ab_test_repo.resolve_atomic(
            "ab-atomic", new.id, old.id, require_zero_comparisons=True,
        )

        assert won is False
        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.resolved_at is None
        assert pair.winner_skill_id is None
        # The skill rows are untouched too.
        assert skill_repo.get(old.id).is_active is True
        assert skill_repo.get(new.id).ab_test_group == "ab-atomic"

    def test_mid_transaction_failure_leaves_zero_partial_mutations(
        self, skill_repo, ab_test_repo, monkeypatch
    ):
        """A failure between the CAS claim and the COMMIT rolls the whole
        transaction back — zero partial mutations."""
        import daemon.repositories.skill.repository as repo_mod

        old, new, _row = _make_pair(skill_repo, ab_test_repo)

        class _BoomSession(repo_mod.Session):
            def commit(self):
                raise RuntimeError("mid-transaction failure")

        monkeypatch.setattr(repo_mod, "Session", _BoomSession)

        with pytest.raises(RuntimeError, match="mid-transaction failure"):
            ab_test_repo.resolve_atomic("ab-atomic", new.id, old.id)

        monkeypatch.undo()

        # NOTHING was committed — not the pair row, not the skill rows.
        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.resolved_at is None
        assert pair.winner_skill_id is None
        assert skill_repo.get(old.id).is_active is True
        assert skill_repo.get(old.id).status == "active"
        assert skill_repo.get(new.id).ab_test_group == "ab-atomic"
        assert skill_repo.get(new.id).status == "active"


# ─────────────────────────────────────────────────────────────────────
# Service level — the sanctioned resolver rides the atomic path
# ─────────────────────────────────────────────────────────────────────


def _make_service(skill_repo, ab_test_repo, *, stall_timeout_days=7.0):
    from daemon.services.skill_evolution_service import SkillEvolutionService

    return SkillEvolutionService(
        skill_repo=skill_repo,
        lineage_repo=MagicMock(),
        usage_repo=MagicMock(),
        embedding_service=MagicMock(),
        metrics_service=MagicMock(),
        ab_test_repo=ab_test_repo,
        config=SimpleNamespace(ab_stall_timeout_days=stall_timeout_days),
        llm_config={},
    )


class TestSanctionedResolverUsesAtomicCAS:
    async def test_sanctioned_resolve_commits_the_full_terminal_state(
        self, skill_repo, ab_test_repo
    ):
        """The sanctioned resolver now applies all three mutations."""
        old, new, _row = _make_pair(skill_repo, ab_test_repo)
        svc = _make_service(skill_repo, ab_test_repo)

        outcome = await svc.resolve_ab_test_sanctioned(
            "ab-atomic",
            basis="stalled",
            reason="unit test — stalled pair",
            actor="unit-test",
        )

        assert outcome["resolved"] is True
        # E-6: the NEWER GENERATION wins.
        assert outcome["winner_id"] == new.id
        assert outcome["loser_id"] == old.id

        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.resolved_at is not None
        assert pair.winner_skill_id == new.id
        assert skill_repo.get(old.id).status == "inactive"
        assert skill_repo.get(old.id).is_active is False
        assert skill_repo.get(old.id).ab_test_group == "ab-atomic"
        assert skill_repo.get(new.id).status == "active"
        # F2: the sanctioned path crowns a SERVABLE winner.
        assert skill_repo.get(new.id).is_active is True
        assert skill_repo.get(new.id).ab_test_group is None

    async def test_concurrent_duplicate_resolve_resolves_exactly_once(
        self, skill_repo, ab_test_repo
    ):
        """Two concurrent sanctioned resolvers ⇒ exactly one wins."""
        _old, new, _row = _make_pair(skill_repo, ab_test_repo)
        svc = _make_service(skill_repo, ab_test_repo)

        outcomes = await asyncio.gather(
            svc.resolve_ab_test_sanctioned(
                "ab-atomic", basis="stalled", reason="race A",
                actor="unit-test",
            ),
            svc.resolve_ab_test_sanctioned(
                "ab-atomic", basis="stalled", reason="race B",
                actor="unit-test",
            ),
        )

        resolved_flags = [o.get("resolved") for o in outcomes]
        assert resolved_flags.count(True) == 1
        assert resolved_flags.count(False) == 1
        loser = [o for o in outcomes if not o.get("resolved")][0]
        assert loser["reason"] == "cas_lost"

        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.resolved_at is not None
        assert pair.winner_skill_id == new.id

    async def test_second_sequential_resolve_is_refused_by_the_existing_guard(
        self, skill_repo, ab_test_repo
    ):
        """A second sanctioned resolve of the SAME group is refused.

        The pre-existing ``already resolved`` guard (which reads the
        pair BEFORE the CAS) fires first — it is unchanged by this
        patch. The CAS is the SECOND line of defence: it covers the
        true race, where the guard's read is stale (see the concurrent
        test above and the repository-level duplicate test).
        """
        _old, new, _row = _make_pair(skill_repo, ab_test_repo)
        svc = _make_service(skill_repo, ab_test_repo)

        first = await svc.resolve_ab_test_sanctioned(
            "ab-atomic", basis="stalled", reason="first", actor="unit-test",
        )
        assert first["resolved"] is True

        with pytest.raises(ValueError, match="already resolved"):
            await svc.resolve_ab_test_sanctioned(
                "ab-atomic", basis="stalled", reason="second",
                actor="unit-test",
            )

        # The first resolution's terminal state is intact.
        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.winner_skill_id == new.id
        assert skill_repo.get(new.id).status == "active"

    async def test_repo_failure_is_reported_without_partial_state(
        self, skill_repo, ab_test_repo, monkeypatch
    ):
        """A mid-transaction failure surfaces as ``resolve_failed`` and
        leaves the pair untouched."""
        import daemon.repositories.skill.repository as repo_mod

        old, new, _row = _make_pair(skill_repo, ab_test_repo)
        svc = _make_service(skill_repo, ab_test_repo)

        class _BoomSession(repo_mod.Session):
            def commit(self):
                raise RuntimeError("mid-transaction failure")

        monkeypatch.setattr(repo_mod, "Session", _BoomSession)

        outcome = await svc.resolve_ab_test_sanctioned(
            "ab-atomic", basis="stalled", reason="boom", actor="unit-test",
        )

        monkeypatch.undo()

        assert outcome["resolved"] is False
        assert outcome["reason"] == "resolve_failed"
        pair = ab_test_repo.get_by_group("ab-atomic")
        assert pair.resolved_at is None
        assert skill_repo.get(old.id).is_active is True
        assert skill_repo.get(new.id).ab_test_group == "ab-atomic"


# ── FIX 8 regression pin — the session-global listener must NOT leak ──────


class TestSpyTeardownRegressionPin:
    def test_teardown_removes_session_global_before_flush_listener(
        self, engine, _skill_write_spy
    ):
        import daemon.repositories.skill.repository as repo_mod
        from sqlalchemy import event

        spy = _skill_write_spy
        # Registered while alive.
        assert event.contains(
            repo_mod.Session, "before_flush", spy.on_flush
        ) is True
        # Deterministic teardown (idempotent with the fixture's
        # addfinalizer).
        spy.cleanup()
        assert event.contains(
            repo_mod.Session, "before_flush", spy.on_flush
        ) is False
        assert event.contains(
            engine, "before_cursor_execute", spy.on_exec
        ) is False
