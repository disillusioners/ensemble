"""Repository-layer tests for the project_future wishlist (SQLModelProjectRepository).

House idiom of tests/test_project_history.py: in-memory SQLite + create_all (the
migration runner is NEVER executed here — create_all sidesteps the fresh-SQLite
breaker). AC25/AC26 interleaving tests use TEMPFILE-backed SQLite with TWO
separate engines (RW8): an ``:memory:`` pair is TWO DISJOINT databases and the
second-session delete/insert would be invisible. FK behavioral tests need the
``PRAGMA foreign_keys=ON`` connect listener (SQLite default OFF makes the
cascade assertion vacuous).
"""

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.repositories.project.models import Project, ProjectFutureEntry
from daemon.repositories.project.repository import ConcurrentWishlistUpdate


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store(engine):
    return ProjectStore(engine)


@pytest.fixture
def project(store):
    return store.create(name="Test Project")


def seed_via_repo(store, project_id, count, start=1):
    """Add ``count`` wishlist rows through the repo (seqs assigned by the repo)."""
    added = []
    for i in range(start, start + count):
        added.append(store.add_future_entry(project_id, f"idea-{i}", max_entries=32))
    return added


def fill_direct(session, project_id, count, same_ts=None, seq_start=1):
    """Insert rows DIRECTLY (drift-seeding / tie-pinning helper)."""
    rows = []
    for i in range(seq_start, seq_start + count):
        row = ProjectFutureEntry(project_id=project_id, summary=f"direct-{i}", seq=i)
        if same_ts is not None:
            row.created_at = same_ts
        rows.append(row)
        session.add(row)
    session.commit()
    return rows


def fk_engine():
    """In-memory engine with PRAGMA foreign_keys=ON on every connection."""
    eng = create_engine("sqlite:///:memory:")
    event.listens_for(eng, "connect")(_pragma_on)
    SQLModel.metadata.create_all(eng)
    return eng


def _pragma_on(dbapi_conn, record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest.fixture
def two_engine_env(tmp_path):
    """TEMPFILE-backed file DB with TWO separate engines (RW8).

    An ``:memory:`` pair would be two disjoint databases — the forced
    interleaving below requires the second engine's write to be visible to
    (and committable against) the first engine's connection.
    """
    db_path = tmp_path / "project_future_guard.db"
    url = f"sqlite:///{db_path}"
    e1 = create_engine(url)
    e2 = create_engine(url)
    SQLModel.metadata.create_all(e1)
    yield e1, e2, ProjectStore(e1)
    e1.dispose()
    e2.dispose()


def install_before_hook(engine, matcher, action):
    """Fire ``action`` (sync) right before engine executes a matching statement."""

    def before_execute(conn, cursor, statement, parameters, context, executemany):
        if matcher(statement):
            action()

    event.listen(engine, "before_cursor_execute", before_execute)


# ============================================================================
# AC10 — 32-cap pin
# ============================================================================


class TestCap:
    def test_33rd_insert_evicts_first_and_discloses(self, store, project):
        """AC10: #33 → evicted == [item#1 dict incl. id/summary/seq=1]; count == 32."""
        pid = project.project_id
        added = seed_via_repo(store, pid, 32)
        first = added[0]
        r33 = store.add_future_entry(pid, "idea-33", max_entries=32)
        assert len(r33["evicted"]) == 1
        ev = r33["evicted"][0]
        assert ev["id"] == first["id"]
        assert ev["summary"] == first["summary"]
        assert ev["seq"] == 1
        assert "created_at" in ev
        assert store.list_future_entries(pid, limit=100)["total"] == 32

    def test_add_returns_flat_dict_with_evicted_key(self, store, project):
        pid = project.project_id
        r = store.add_future_entry(pid, "idea", max_entries=32)
        assert r["evicted"] == []
        assert set(r) >= {"id", "project_id", "summary", "details",
                          "source_agent", "source_instance_id", "created_at", "seq",
                          "evicted"}


# ============================================================================
# AC11 — FIFO order over multiple evictions
# ============================================================================


class TestFifoOrder:
    def test_35_inserts_evict_oldest_three_in_order(self, store, project):
        """AC11: evictions are #1,#2,#3 in order; survivors #4..#35; min(seq)==4."""
        pid = project.project_id
        added = seed_via_repo(store, pid, 32)
        evicted_all = []
        for i in range(33, 36):
            r = store.add_future_entry(pid, f"idea-{i}", max_entries=32)
            assert len(r["evicted"]) == 1
            evicted_all.append(r["evicted"][0])
        assert [e["seq"] for e in evicted_all] == [1, 2, 3]
        assert [e["id"] for e in evicted_all] == [added[j]["id"] for j in range(3)]
        entries = store.list_future_entries(pid, limit=100)["entries"]
        assert sorted(e["seq"] for e in entries) == list(range(4, 36))
        assert min(e["seq"] for e in entries) == 4


# ============================================================================
# AC12 — same-timestamp tie: seq decides, timestamps don't
# ============================================================================


class TestSameTimestampTie:
    def test_identical_created_at_evicts_by_seq_asc(self, store, project):
        """AC12: 32 rows with IDENTICAL created_at → eviction strictly by seq ASC."""
        pid = project.project_id
        same = datetime(2026, 11, 12, 12, 0, 0)
        with Session(store.engine) as session:
            rows = fill_direct(session, pid, 32, same_ts=same)
            first_id = rows[0].id  # capture before the session closes
        r = store.add_future_entry(pid, "tie-breaker", max_entries=32)
        assert len(r["evicted"]) == 1
        assert r["evicted"][0]["seq"] == 1
        assert r["evicted"][0]["id"] == first_id

    def test_inverted_timestamps_still_evict_lowest_seq(self, store, project):
        """AC12: seq=1 row carries a LATER timestamp → still evicted (seq decides)."""
        pid = project.project_id
        base = datetime(2026, 11, 12, 12, 0, 0)
        with Session(store.engine) as session:
            rows = fill_direct(session, pid, 32, same_ts=base)
            rows[0].created_at = base + timedelta(days=1)  # seq1 "newest" by ts
            session.add(rows[0])
            session.commit()
        r = store.add_future_entry(pid, "ts-ignored", max_entries=32)
        assert r["evicted"][0]["seq"] == 1


# ============================================================================
# AC13 — list newest-first
# ============================================================================


class TestList:
    def test_newest_first_and_envelope(self, store, project):
        """AC13: entries[0].seq == max(seq); seq DESC; {entries,total,limit,offset}."""
        pid = project.project_id
        seed_via_repo(store, pid, 5)
        result = store.list_future_entries(pid)
        assert set(result) == {"entries", "total", "limit", "offset"}
        assert result["total"] == 5
        assert result["limit"] == 20 and result["offset"] == 0
        seqs = [e["seq"] for e in result["entries"]]
        assert seqs == sorted(seqs, reverse=True)
        assert result["entries"][0]["seq"] == max(seqs)

    def test_limit_passes_through(self, store, project):
        """AC13: repo passes limit/offset straight through (clamp lives in the tool)."""
        pid = project.project_id
        seed_via_repo(store, pid, 5)
        result = store.list_future_entries(pid, limit=2, offset=1)
        assert result["limit"] == 2 and result["offset"] == 1
        assert len(result["entries"]) == 2
        assert [e["seq"] for e in result["entries"]] == [4, 3]


# ============================================================================
# AC14 — per-project independence
# ============================================================================


class TestPerProjectIndependence:
    def test_projects_evict_independently(self, store):
        """AC14: A and B each reach 32; A#33 evicts A#1 only; B untouched; B seq 1."""
        a = store.create(name="A")
        b = store.create(name="B")
        seed_via_repo(store, a.project_id, 32)
        seed_via_repo(store, b.project_id, 32)
        a_first = store.list_future_entries(a.project_id, limit=100)["entries"][-1]
        r = store.add_future_entry(a.project_id, "A-idea-33", max_entries=32)
        assert [e["id"] for e in r["evicted"]] == [a_first["id"]]
        b_list = store.list_future_entries(b.project_id, limit=100)
        assert b_list["total"] == 32
        assert all(e["project_id"] == b.project_id for e in b_list["entries"])
        # B's seq numbering is independent — a fresh project's first add gets seq 1.
        c = store.create(name="C")
        c_first = store.add_future_entry(c.project_id, "C-idea-1", max_entries=32)
        assert c_first["seq"] == 1


# ============================================================================
# AC15 — remove semantics
# ============================================================================


class TestRemove:
    def test_remove_existing_decrements_count(self, store, project):
        """AC15: existing → removed dict + count decremented."""
        pid = project.project_id
        added = seed_via_repo(store, pid, 3)
        target = added[1]
        removed = store.remove_future_entry(target["id"], project_id=pid)
        assert removed is not None
        assert removed["id"] == target["id"]
        assert removed["seq"] == target["seq"]
        assert store.list_future_entries(pid)["total"] == 2

    def test_remove_unknown_id_returns_none(self, store, project):
        seed_via_repo(store, project.project_id, 1)
        assert store.remove_future_entry("missing-id") is None

    def test_remove_wrong_project_scoped(self, store, project):
        """AC15: wrong project_id → None (A17 :1355 scoping preserved)."""
        other = store.create(name="Other")
        added = seed_via_repo(store, project.project_id, 1)
        assert store.remove_future_entry(added[0]["id"], project_id=other.project_id) is None
        assert store.list_future_entries(project.project_id)["total"] == 1


# ============================================================================
# AC16 — FK cascade (behavioral + introspection)
# ============================================================================


class TestFkCascade:
    def test_delete_project_cascades_future_rows(self):
        """AC16 behavioral leg: PRAGMA FK ON; delete project row → future rows gone."""
        eng = fk_engine()
        try:
            store = ProjectStore(eng)
            proj = store.create(name="Cascading")
            seed_via_repo(store, proj.project_id, 3)
            with Session(eng) as session:
                row = session.get(Project, proj.project_id)
                session.delete(row)
                session.commit()
            with Session(eng) as session:
                from sqlmodel import select

                remaining = list(
                    session.exec(
                        select(ProjectFutureEntry).where(
                            ProjectFutureEntry.project_id == proj.project_id
                        )
                    )
                )
            assert remaining == []
        finally:
            eng.dispose()

    def test_introspection_pin_ondelete_cascade(self):
        """AC16 introspection leg: fk.ondelete == 'CASCADE' (pattern :1039)."""
        fk_col = next(
            c for c in ProjectFutureEntry.__table__.columns if c.name == "project_id"
        )
        fk = next(iter(fk_col.foreign_keys))
        assert fk.ondelete == "CASCADE"
        assert fk.column.table.name == "projects"


# ============================================================================
# AC17 — repo-layer truncation
# ============================================================================


class TestRepoTruncation:
    def test_repo_truncates_summary_and_details(self, store, project):
        """AC17: repository duplicates the tool layer's bounds (300/5000)."""
        pid = project.project_id
        r = store.add_future_entry(
            pid, "s" * 400, "d" * 6000, max_entries=32
        )
        assert len(r["summary"]) == 300
        assert r["details"] is not None and len(r["details"]) == 5000

    def test_repo_none_details_stays_none(self, store, project):
        r = store.add_future_entry(project.project_id, "idea", None, max_entries=32)
        assert r["details"] is None


# ============================================================================
# AC18 — drift self-heal
# ============================================================================


class TestDriftSelfHeal:
    def test_34_rows_next_add_evicts_three(self, store, project):
        """AC18: inflate to 34 via direct inserts → next add evicts 3 → count 32."""
        pid = project.project_id
        seed_via_repo(store, pid, 32)
        with Session(store.engine) as session:
            session.add_all(
                [
                    ProjectFutureEntry(project_id=pid, summary=f"drift-{i}", seq=100 + i)
                    for i in range(2)
                ]
            )
            session.commit()
        assert store.list_future_entries(pid, limit=100)["total"] == 34
        r = store.add_future_entry(pid, "healer", max_entries=32)
        assert len(r["evicted"]) == 3
        assert [e["seq"] for e in r["evicted"]] == [1, 2, 3]
        assert store.list_future_entries(pid, limit=100)["total"] == 32


# ============================================================================
# AC19 — UNIQUE(project_id, seq) enforced
# ============================================================================


class TestUniqueConstraint:
    def test_duplicate_live_seq_raises_integrity_error(self, store, project):
        """AC19: duplicate-seq insert (duplicate of a LIVE seq) → IntegrityError."""
        pid = project.project_id
        seed_via_repo(store, pid, 3)
        with Session(store.engine) as session:
            session.add(ProjectFutureEntry(project_id=pid, summary="dupe", seq=2))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


# ============================================================================
# AC27 — keyword-only max_entries (O11)
# ============================================================================


class TestKeywordOnlyCap:
    def test_positional_max_entries_raises_type_error(self, store, project):
        """AC27: positional past the * marker → TypeError; keyword call works."""
        pid = project.project_id
        with pytest.raises(TypeError):
            store.add_future_entry(pid, "s", None, "a", "i", 32)
        ok = store.add_future_entry(pid, "s", None, "a", "i", max_entries=32)
        assert ok["evicted"] == []


# ============================================================================
# AC28 — seq reuse (D2/R6)
# ============================================================================


class TestSeqReuse:
    def test_removing_newest_row_reuses_its_seq(self, store, project):
        """AC28: seed 5; remove seq=5; next add returns seq==5; list newest-first."""
        pid = project.project_id
        added = seed_via_repo(store, pid, 5)
        newest = added[-1]
        assert newest["seq"] == 5
        removed = store.remove_future_entry(newest["id"], project_id=pid)
        assert removed["seq"] == 5
        nxt = store.add_future_entry(pid, "reuse-top-slot", max_entries=32)
        assert nxt["seq"] == 5
        result = store.list_future_entries(pid)
        assert result["entries"][0]["id"] == nxt["id"]
        assert result["entries"][0]["seq"] == 5


# ============================================================================
# AC25 — stale/concurrent victim delete aborts the transaction (R1 guard)
# ============================================================================


class TestStaleVictimGuard:
    def _seed_32(self, store, project):
        return seed_via_repo(store, project.project_id, 32)

    def test_naive_delete_then_add_does_not_trip_guard(self, two_engine_env, tmp_path):
        """AC25 negative leg: a committed delete BEFORE the add counts 31 → clean."""
        e1, e2, store = two_engine_env
        proj = store.create(name="Guard")
        pid = proj.project_id
        self._seed_32(store, proj)
        with Session(e2) as session:
            victim = session.exec(
                select(ProjectFutureEntry)
                .where(ProjectFutureEntry.project_id == pid)
                .order_by(ProjectFutureEntry.seq.asc())
                .limit(1)
            ).first()
            session.delete(victim)
            session.commit()
        r = store.add_future_entry(pid, "after-naive-delete", max_entries=32)
        assert "evicted" in r and r["evicted"] == []
        assert store.list_future_entries(pid, limit=100)["total"] == 32

    def test_forced_victim_delete_between_select_and_delete_aborts(self, two_engine_env):
        """AC25: hook deletes the selected victim from engine2 between the add's
        victim-SELECT and victim-DELETE → ConcurrentWishlistUpdate, WHOLE
        transaction rolled back, count <= 32, no phantom row, no false evicted."""
        e1, e2, store = two_engine_env
        proj = store.create(name="Guard")
        pid = proj.project_id
        added = self._seed_32(store, proj)

        def steal_victim_from_second_engine():
            # engine1 holds NO write lock here: every prior statement was an
            # autocommit SELECT, and the victims rows are already fetched —
            # engine2's write+commit goes through cleanly.
            with Session(e2) as session:
                victim = session.exec(
                    select(ProjectFutureEntry)
                    .where(ProjectFutureEntry.project_id == pid)
                    .order_by(ProjectFutureEntry.seq.asc())
                    .limit(1)
                ).first()
                session.delete(victim)
                session.commit()

        install_before_hook(
            e1,
            lambda stmt: stmt.startswith("DELETE FROM project_future WHERE project_future.id IN"),
            steal_victim_from_second_engine,
        )
        with pytest.raises(ConcurrentWishlistUpdate) as excinfo:
            store.add_future_entry(pid, "during-interleave", max_entries=32)
        assert pid in str(excinfo.value)
        # (ii) no phantom row from the rolled-back add; count <= 32
        entries = store.list_future_entries(pid, limit=100)["entries"]
        assert len(entries) == 31  # the externally deleted victim stays gone
        assert all(e["summary"] != "during-interleave" for e in entries)
        # (iii) no false disclosure — the guard raised before any return
        assert "during-interleave" not in [e["summary"] for e in entries]


# ============================================================================
# AC26 — UNIQUE(project_id, seq) race forced THROUGH add_future_entry
# ============================================================================


class TestUniqueRaceThroughAdd:
    def test_colliding_seq_from_second_engine_translates(self, two_engine_env):
        """AC26: hook INSERTs the colliding seq from a SECOND engine between the
        add's MAX(seq) read and its INSERT → ConcurrentWishlistUpdate + whole
        rollback + no partial rows.

        The table is deliberately NOT full here: with 32 seeded rows the add's
        staged victim-DELETE takes SQLite's RESERVED write lock BEFORE the hook
        window, so the second engine's colliding insert cannot commit (the lock
        serializes them and the R1 rowcount guard path fires instead — pinned
        by TestStaleVictimGuard). The not-full add's first write IS the INSERT,
        so the hook window is real and the D16/D18 IntegrityError path is
        exercised directly.
        """
        e1, e2, store = two_engine_env
        proj = store.create(name="Race")
        pid = proj.project_id
        seed_via_repo(store, pid, 2)  # NOT full: first write is the INSERT

        def collide_from_second_engine():
            with Session(e2) as session:
                max_seq = session.exec(
                    select(func.max(ProjectFutureEntry.seq)).where(
                        ProjectFutureEntry.project_id == pid
                    )
                ).one()
                session.add(
                    ProjectFutureEntry(project_id=pid, summary="external", seq=(max_seq or 0) + 1)
                )
                session.commit()

        install_before_hook(
            e1,
            lambda stmt: stmt.startswith("INSERT INTO project_future"),
            collide_from_second_engine,
        )
        with pytest.raises(ConcurrentWishlistUpdate):
            store.add_future_entry(pid, "racer", max_entries=32)
        # WHOLE rollback: no partial rows — the racer row is gone; the external
        # row (committed by engine2) is the only seq=3 row.
        entries = store.list_future_entries(pid, limit=100)["entries"]
        assert len(entries) == 3
        assert [e["summary"] for e in entries] == ["external", "idea-2", "idea-1"]
        assert sum(1 for e in entries if e["summary"] == "racer") == 0

    def test_non_unique_integrity_error_propagates_untranslated(self):
        """D18 narrowing pin: a NON-unique IntegrityError (FK violation via
        PRAGMA ON + unknown project) re-raises RAW — no ConcurrentWishlistUpdate."""
        eng = fk_engine()
        try:
            store = ProjectStore(eng)
            with pytest.raises(IntegrityError) as excinfo:
                store.add_future_entry("no-such-project", "orphan", max_entries=32)
            assert not isinstance(excinfo.value, ConcurrentWishlistUpdate)
            assert "UNIQUE" not in str(excinfo.value)
        finally:
            eng.dispose()


# --- imports kept at the bottom on purpose: the fixtures above read naturally
# top-down; sqlalchemy funcs + datetime helpers are shared across classes.
from datetime import datetime, timedelta  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlmodel import select  # noqa: E402
