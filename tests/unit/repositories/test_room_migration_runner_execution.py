"""Migration-runner EXECUTION pins — group-chat P3 carry-overs O-1 + O-5.

The P1 review's raw-text pins (test_room_models_and_migration.py)
string-matched the .sql files. This module is the re-basing: the real
files are executed through the REAL runner — anchored on
``MigrationRunner.apply_migration`` (there is NO ``_executable_sql`` to
anchor on; O-1's refinement) — and the assertions run against the LIVE
schema the execution produced.

O-1 specifics:
  * The known fresh-SQLite boot breaker
    ``20260714_000001_widen_job_queue_type_constraint`` (PG-only
    ``DROP CONSTRAINT`` syntax) is ledger-PRE-MARKED via the house
    helper ``tests.fakes.f1_premark_helper.premark_f1`` so
    ``run_pending_migrations`` exercises the room migrations without
    tripping the pre-existing abort (the F1 trap; DO NOT FIX THE
    MIGRATION — separate commission).
  * The DRIVER-DEPENDENT constraint fingerprint is pinned BOTH ways:
    the SQLite dotted-pair form empirically (a real IntegrityError off
    the executed schema) and the PG named-constraint form via the
    documented psycopg3 message shape (live-verified on the fenced
    ensemble_dev — A-P3-10). The pins MUST NOT assume one form is
    universal.

Covers, on the EXECUTED schema:
  * the runner applies both room migrations and records ledger rows;
  * the pre-marked F1 migration is not pending and not executed;
  * the three room tables exist with the pinned columns;
  * the FK shape is NO ACTION on both child tables AND is enforced
    (a parent delete with children present is refused);
  * the executed index set matches the model-declared index/constraint
    column-sets (execution-level lockstep, replacing the raw-text
    regex lockstep);
  * DOWN (rollback_migration) drops exactly what UP created;
  * the proposal-dedup UNIQUE index admits one proposal per
    (source_message_id, trigger, member) and never collides on
    all-NULL ordinary rows.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.event.models  # noqa: F401 — register event
import daemon.repositories.instance.models  # noqa: F401 — register instances
from daemon.repositories.room.models import (  # noqa: F401 — register room
    ChatRoom,
    ChatRoomMember,
    RoomMessage,
)
from daemon.repositories.room.repository import (
    MemberNameCollisionError,
    RoomError,
    is_room_member_integrity,
    raise_typed_member_integrity,
)
from daemon.migrations.runner import MigrationRunner
from tests.fakes.f1_premark_helper import F1_VERSION, premark_f1
from tests.helpers.migration_execution import (
    CHATROOM_MIGRATION_FILENAME,
    MIGRATION_ENGINE_BUSY_TIMEOUT_MS,
    MIGRATION_ENGINE_FOREIGN_KEYS,
    MIGRATIONS_DIR,
    PROPOSAL_DEDUP_MIGRATION_FILENAME,
    RESOLUTION_WARNINGS_MIGRATION_FILENAME,
    SQLITE_DEFAULT_BUSY_TIMEOUT_MS,
    apply_migration_file,
    make_migration_engine,
    parse_migration,
    sqlite_columns,
    sqlite_foreign_keys,
    sqlite_indexes,
    sqlite_pragma,
    table_exists,
)

# The pinned column manifest per table (execution-level; mirrors the
# raw-text manifest it replaces — O-5).
PINNED_COLUMNS: dict[str, list[str]] = {
    "chatrooms": [
        "room_id", "name", "tree_root_id", "owner_instance_id",
        "state", "closed_at", "last_post_at", "message_count",
        "created_at", "updated_at",
    ],
    "chatroom_members": [
        "room_id", "instance_id", "member_name", "agent_id", "role",
        "joined_at", "last_read_seq", "last_delivered_seq", "state",
    ],
    "room_messages": [
        "id", "room_id", "seq", "author_instance_id", "author_name",
        "text", "tagged_members", "addressed_members", "kind",
        "created_at",
        # The P3 proposal-dedup columns (migration 20261115_120000).
        "source_message_id", "trigger", "member",
        # The P5 resolution-warnings column (migration 20261116_083000).
        "resolution_warnings",
    ],
}

# The model-declared secondary indexes (name → columns) — the
# execution-level lockstep compares THESE against the executed schema.
MODEL_INDEXES: dict[str, dict[str, list[str]]] = {
    "chatrooms": {
        "idx_chatrooms_tree_root_id": ["tree_root_id"],
        "idx_chatrooms_state_closed_at": ["state", "closed_at"],
        "idx_chatrooms_state_last_post_at": ["state", "last_post_at"],
    },
    "chatroom_members": {
        "idx_chatroom_members_instance_id": ["instance_id"],
    },
}

# The unique-backstop column pairs (constraint NAMES differ by origin —
# SQLite's inline UNIQUE is auto-named — so the lockstep is on the
# COLUMN SETS, not names).
MODEL_UNIQUE_COLUMN_SETS: dict[str, set[frozenset[str]]] = {
    "chatroom_members": {
        frozenset({"room_id", "member_name"}),
        frozenset({"room_id", "instance_id"}),  # the composite PK
    },
    "room_messages": {
        frozenset({"room_id", "seq"}),
    },
}


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Fresh file-backed SQLite with the FK pragma ON — NO create_all:
    everything these tests see is what the RUNNER's execution built.

    M-ro4: the builder moved to
    ``tests/helpers/migration_execution.py::make_migration_engine``
    (ONE builder for both migration-test modules). The PRAGMA
    equivalence proof below pins that the consolidated engine's PRAGMA
    state is EXACTLY the state this module's original per-file engine
    had.
    """
    eng = make_migration_engine(tmp_path, "room_migration_execution.db")
    yield eng
    eng.dispose()


class TestThePragmaEquivalenceProof:
    """M-ro4 — the PRAGMA equivalence proof, FIRST.

    The consolidation moved ONE builder into the shared helper; the
    two per-file engines it replaces had DIFFERENT PRAGMA shapes (the
    runner-execution engine set ``busy_timeout=10000``; the
    models-and-migration engine set the FK pragma ONLY). The proof
    pins that the consolidated builder reproduces BOTH shapes exactly
    — so the consolidation is a pure de-duplication, never a behavior
    change.
    """

    def test_the_consolidated_engine_pragmas_match_the_per_file_engines(
        self, tmp_path, engine
    ):
        """The runner-execution module's ORIGINAL shape: FK ON +
        a 10 s busy timeout."""
        assert sqlite_pragma(engine, "foreign_keys") == (
            MIGRATION_ENGINE_FOREIGN_KEYS
        )
        assert sqlite_pragma(engine, "busy_timeout") == (
            MIGRATION_ENGINE_BUSY_TIMEOUT_MS
        )

    def test_the_fk_only_shape_is_reproducible(self, tmp_path):
        """The models-and-migration module's ORIGINAL shape: the FK
        pragma ONLY (SQLite's own default busy timeout)."""
        eng = make_migration_engine(
            tmp_path, "room_migration_fk_only.db", busy_timeout_ms=None
        )
        try:
            assert sqlite_pragma(eng, "foreign_keys") == (
                MIGRATION_ENGINE_FOREIGN_KEYS
            )
            assert sqlite_pragma(eng, "busy_timeout") == (
                SQLITE_DEFAULT_BUSY_TIMEOUT_MS
            )
        finally:
            eng.dispose()


# ---------------------------------------------------------------------------
# O-1 — the runner-execution pin with the F1 pre-mark
# ---------------------------------------------------------------------------


class TestRunnerExecutesRoomMigrations:
    def test_run_pending_applies_room_migrations_with_f1_premark(
        self, tmp_path, engine
    ):
        """THE O-1 pin: with the known F1 breaker ledger-pre-marked,
        ``run_pending_migrations`` executes the two room migrations
        end-to-end (real statement splitter, real ledger rows) and
        NEVER touches F1."""
        # tmp versions dir with exactly F1 + the THREE room migrations.
        versions = tmp_path / "versions"
        versions.mkdir()
        for name in (
            "20260714_000001_widen_job_queue_type_constraint.sql",
            CHATROOM_MIGRATION_FILENAME,
            PROPOSAL_DEDUP_MIGRATION_FILENAME,
            RESOLUTION_WARNINGS_MIGRATION_FILENAME,
        ):
            shutil.copy(MIGRATIONS_DIR / name, versions / name)

        # The tmp-ledger PRE-MARK (the F1 trap workaround — its own
        # documented skip_reason; the migration SQL never executes).
        db_path = str(engine.url).replace("sqlite:///", "")
        premark_f1(db_path)

        runner = MigrationRunner(engine, migrations_dir=versions)
        runner.ensure_migrations_table()
        pending_versions = [m.version for m in runner.get_pending_migrations()]
        assert F1_VERSION not in pending_versions, (
            "the pre-marked F1 breaker must not be pending"
        )
        assert pending_versions == [
            "20261115_080000", "20261115_120000", "20261116_083000"
        ], pending_versions

        applied = runner.run_pending_migrations()
        assert applied == [
            "20261115_080000", "20261115_120000", "20261116_083000"
        ], applied

        # Ledger rows exist for the two room migrations (and F1 keeps
        # its pre-mark row).
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT version, skip_reason FROM schema_migrations "
                    "ORDER BY version"
                )
            ).all()
        by_version = {r[0]: r[1] for r in rows}
        assert set(by_version) == {
            F1_VERSION, "20261115_080000", "20261115_120000",
            "20261116_083000",
        }, by_version
        assert by_version[F1_VERSION] is not None, "F1 keeps its skip_reason"
        assert by_version["20261115_080000"] is None
        assert by_version["20261115_120000"] is None
        assert by_version["20261116_083000"] is None

        # The execution built the real schema — all three tables exist
        # with the pinned columns.
        for table, cols in PINNED_COLUMNS.items():
            assert table_exists(engine, table), table
            got = [c["name"] for c in sqlite_columns(engine, table)]
            assert got == cols, (table, got)

    def test_apply_migration_anchor_direct(self, engine):
        """The O-1 anchor, directly: ``apply_migration`` executes the
        chatroom migration on a bare engine (no create_all) and returns
        a non-negative execution time; the ledger row lands."""
        runner = MigrationRunner(engine, migrations_dir=MIGRATIONS_DIR)
        runner.ensure_migrations_table()
        ms = runner.apply_migration(parse_migration(CHATROOM_MIGRATION_FILENAME))
        assert isinstance(ms, (int, float)) and ms >= 0
        assert table_exists(engine, "chatrooms")
        assert table_exists(engine, "chatroom_members")
        assert table_exists(engine, "room_messages")
        assert "20261115_080000" in runner.get_applied_versions()


# ---------------------------------------------------------------------------
# O-5 — the executed-schema shape pins (the re-based raw-text manifest)
# ---------------------------------------------------------------------------


@pytest.fixture
def executed_engine(engine) -> Engine:
    """Fresh schema built ONLY by executed migrations (both room .sql
    files — the full set boot applies). NO create_all anywhere in this
    module's executed paths: everything asserted is what the RUNNER's
    execution produced."""
    apply_migration_file(engine, CHATROOM_MIGRATION_FILENAME)
    apply_migration_file(engine, PROPOSAL_DEDUP_MIGRATION_FILENAME)
    apply_migration_file(engine, RESOLUTION_WARNINGS_MIGRATION_FILENAME)
    return engine


class TestExecutedSchemaShape:
    def test_pinned_columns_present_on_executed_schema(self, executed_engine):
        for table, cols in PINNED_COLUMNS.items():
            got = [c["name"] for c in sqlite_columns(executed_engine, table)]
            assert got == cols, (table, got)

    def test_executed_fk_shape_is_no_action(self, executed_engine):
        """The FK pins, EXECUTED: both child tables reference
        chatrooms(room_id) with on_delete == 'NO ACTION' (the raw-text
        pin asserted the ABSENCE of an ON DELETE clause; here the live
        FK metadata is the witness)."""
        for table in ("chatroom_members", "room_messages"):
            fks = sqlite_foreign_keys(executed_engine, table)
            room_fks = [f for f in fks if f["table"] == "chatrooms"]
            assert room_fks, (table, fks)
            assert any(f["from"] == "room_id" for f in room_fks), (table, fks)
            for f in room_fks:
                assert (f["on_delete"] or "NO ACTION") == "NO ACTION", (
                    table, f
                )

    def test_executed_fk_is_enforced(self, executed_engine):
        """The NO ACTION refusal is REAL, not textual: deleting the
        parent room with children present is refused by the database
        (PRAGMA foreign_keys=ON); child-first deletion succeeds."""
        _insert_room(executed_engine, "r1")
        _insert_member(executed_engine, "r1", "c1", "w")

        # Parent-first delete is REFUSED (the NO ACTION FK).
        with pytest.raises(IntegrityError):
            with executed_engine.connect() as conn:
                conn.execute(text("DELETE FROM chatrooms WHERE room_id='r1'"))
                conn.commit()

        # Child-first delete is ALLOWED.
        with executed_engine.connect() as conn:
            conn.execute(text("DELETE FROM chatroom_members WHERE room_id='r1'"))
            conn.execute(text("DELETE FROM chatrooms WHERE room_id='r1'"))
            conn.commit()

    def test_executed_index_lockstep_matches_models(self, tmp_path, executed_engine):
        """The raw-text index-lockstep pin, re-based on EXECUTION: the
        executed migration schema and the model-created schema carry
        the SAME index column-sets (+ uniqueness) per table. Names may
        differ — the models declare NAMED UniqueConstraints while the
        migration's inline UNIQUEs are SQLite-auto-named — so the
        comparison is on (column-set → unique) pairs, the driver-honest
        invariant the regex pin approximated."""
        # Model-created schema (what PG gets via create_all).
        model_engine = create_engine(
            f"sqlite:///{tmp_path}/model_schema.db",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        try:
            SQLModel.metadata.create_all(model_engine)
            for table in ("chatrooms", "chatroom_members", "room_messages"):
                model_sets = {
                    frozenset(idx["columns"]): idx["unique"]
                    for idx in sqlite_indexes(model_engine, table).values()
                }
                executed_sets = {
                    frozenset(idx["columns"]): idx["unique"]
                    for idx in sqlite_indexes(executed_engine, table).values()
                }
                assert model_sets == executed_sets, (
                    table,
                    sorted(map(sorted, model_sets)),
                    sorted(map(sorted, executed_sets)),
                )
        finally:
            model_engine.dispose()

    def test_executed_unique_backstops_present(self, executed_engine):
        """The unique-backstop column sets exist on the executed schema:
        UNIQUE(room_id, member_name) + the composite PK on members,
        UNIQUE(room_id, seq) on messages."""
        members = sqlite_indexes(executed_engine, "chatroom_members")
        member_unique_sets = {
            frozenset(v["columns"]) for v in members.values() if v["unique"]
        }
        assert frozenset({"room_id", "member_name"}) in member_unique_sets
        assert frozenset({"room_id", "instance_id"}) in member_unique_sets

        messages = sqlite_indexes(executed_engine, "room_messages")
        message_unique_sets = {
            frozenset(v["columns"]) for v in messages.values() if v["unique"]
        }
        assert frozenset({"room_id", "seq"}) in message_unique_sets

    def test_named_secondary_indexes_present(self, executed_engine):
        """The four named secondary indexes were created by the executed
        SQL with the pinned column lists."""
        rooms = sqlite_indexes(executed_engine, "chatrooms")
        members = sqlite_indexes(executed_engine, "chatroom_members")
        for name, cols in MODEL_INDEXES["chatrooms"].items():
            assert name in rooms, (name, sorted(rooms))
            assert rooms[name]["columns"] == cols
        for name, cols in MODEL_INDEXES["chatroom_members"].items():
            assert name in members, (name, sorted(members))
            assert members[name]["columns"] == cols

    def test_down_drops_exactly_what_up_created(self, executed_engine):
        """The DOWN pin, executed: ``rollback_migration`` drops the
        indexes + the three tables (and the ledger row) — nothing else
        is touched (schema_migrations survives)."""
        runner = MigrationRunner(
            executed_engine, migrations_dir=MIGRATIONS_DIR
        )
        runner.rollback_migration("20261115_080000")
        for table in ("chatrooms", "chatroom_members", "room_messages"):
            assert not table_exists(executed_engine, table), table
        with executed_engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars().all()
        assert "schema_migrations" in remaining
        assert not (
            {"chatrooms", "chatroom_members", "room_messages"} & set(remaining)
        )

    def test_the_p5_resolution_warnings_column_executes(self, executed_engine):
        """The P5 (A-P3-16) migration, executed: the durable
        resolution-warnings column lands on the live schema and its
        DOWN drops exactly that column."""
        columns = [c["name"] for c in sqlite_columns(executed_engine, "room_messages")]
        assert "resolution_warnings" in columns, columns

        runner = MigrationRunner(
            executed_engine, migrations_dir=MIGRATIONS_DIR
        )
        runner.rollback_migration("20261116_083000")
        after = [c["name"] for c in sqlite_columns(executed_engine, "room_messages")]
        assert "resolution_warnings" not in after, after
        # The rollback touched NOTHING else — the proposal-dedup
        # columns (the previous migration) survive.
        assert "source_message_id" in after, after

    def test_proposal_dedup_unique_executes(self, executed_engine):
        """The P3 proposal-dedup migration, executed: one proposal row
        per (source_message_id, trigger, member); ordinary all-NULL
        rows never collide (NULLs distinct on SQLite)."""
        _insert_room(executed_engine, "r1")

        with executed_engine.connect() as conn:
            # The dedup index exists with the pinned columns.
            messages = sqlite_indexes(executed_engine, "room_messages")
            dedup = [
                v for v in messages.values()
                if v["columns"] == ["source_message_id", "trigger", "member"]
            ]
            assert dedup and dedup[0]["unique"], messages

            base = (
                "INSERT INTO room_messages (room_id, seq, "
                "author_instance_id, author_name, text, tagged_members, "
                "addressed_members, kind, created_at{extra}) "
                "VALUES ('r1', :seq, 'a', 'w', 'body', '[]', '[]', "
                ":kind, 't0'{vals})"
            )
            # Ordinary rows: all three dedup columns NULL — never collide.
            for seq in (1, 2):
                conn.execute(
                    text(base.format(extra="", vals="")),
                    {"seq": seq, "kind": "message"},
                )
            # Proposal row #1: accepted.
            conn.execute(
                text(base.format(
                    extra=", source_message_id, trigger, member",
                    vals=", :smid, :trig, :mem",
                )),
                {"seq": 3, "kind": "task_proposal", "smid": 1,
                 "trig": "tag_terminal", "mem": "capture"},
            )
            conn.commit()
            # The SAME (source_message_id, trigger, member) is REFUSED.
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(base.format(
                        extra=", source_message_id, trigger, member",
                        vals=", :smid, :trig, :mem",
                    )),
                    {"seq": 4, "kind": "task_proposal", "smid": 1,
                     "trig": "tag_terminal", "mem": "capture"},
                )
                conn.commit()
            # A DIFFERENT member on the same source+trigger is accepted.
            conn.execute(
                text(base.format(
                    extra=", source_message_id, trigger, member",
                    vals=", :smid, :trig, :mem",
                )),
                {"seq": 5, "kind": "task_proposal", "smid": 1,
                 "trig": "tag_terminal", "mem": "editor"},
            )
            conn.commit()


# ---------------------------------------------------------------------------
# O-1 — the DRIVER-DEPENDENT constraint fingerprint (both shapes pinned)
# ---------------------------------------------------------------------------


def _insert_room(engine: Engine, room_id: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO chatrooms (room_id, name, tree_root_id, "
            "owner_instance_id, state, message_count, created_at, "
            "updated_at) VALUES (:r, 'alpha', 'root', 'own', 'active', "
            "0, 't0', 't0')"
        ), {"r": room_id})
        conn.commit()


def _insert_member(engine: Engine, room_id: str, iid: str, name: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO chatroom_members (room_id, instance_id, "
            "member_name, role, joined_at, last_read_seq, "
            "last_delivered_seq, state) VALUES (:r, :i, :n, 'child', "
            "'t0', 0, 0, 'active')"
        ), {"r": room_id, "i": iid, "n": name})
        conn.commit()


class TestConstraintFingerprintDriverDependence:
    def test_sqlite_fires_the_dotted_pair_form(self, executed_engine):
        """EMPIRICAL (SQLite): a real duplicate (room_id, member_name)
        off the executed schema fires the DOTTED-PAIR message — the
        named-constraint string is NOT in it. The classifier must
        accept this shape (never assume the named form is universal)."""
        _insert_room(executed_engine, "r1")
        _insert_member(executed_engine, "r1", "c1", "capture")
        with pytest.raises(IntegrityError) as excinfo:
            _insert_member(executed_engine, "r1", "c2", "capture")
        msg = str(excinfo.value)
        assert "chatroom_members.room_id, chatroom_members.member_name" in msg, msg
        assert "uq_chatroom_members_room_member_name" not in msg, (
            "SQLite does not report constraint NAMES for inline UNIQUE — "
            "if this ever changes, the fingerprint documentation (A-P3-10) "
            "must be revisited"
        )
        assert is_room_member_integrity(excinfo.value) is True

    def test_named_constraint_form_classifies_to_typed_collision(self):
        """The PG/psycopg3 NAMED-CONSTRAINT form (live-verified on the
        fenced ensemble_dev — A-P3-10: psycopg3 reports the declared
        constraint name ``uq_chatroom_members_room_member_name`` and
        NOT the dotted-pair columns) also matches the fingerprint and
        re-types to MemberNameCollisionError. Synthetic IntegrityError
        carrying the documented PG message shape."""
        pg_shaped = IntegrityError(
            "stmt", {},
            Exception(
                'duplicate key value violates unique constraint '
                '"uq_chatroom_members_room_member_name"'
            ),
        )
        assert is_room_member_integrity(pg_shaped) is True
        with pytest.raises(MemberNameCollisionError):
            raise_typed_member_integrity(pg_shaped, "r1", "capture")

    def test_composite_pk_duplicate_never_mistypes(self, executed_engine):
        """The negative leg: a composite-PK duplicate (same room+member
        name DIFFERENT, same room+instance_id) fires a DIFFERENT
        dotted-pair message and must fall through to the generic
        RoomError — never the name-collision type."""
        _insert_room(executed_engine, "r1")
        _insert_member(executed_engine, "r1", "c1", "capture")
        with pytest.raises(IntegrityError) as excinfo:
            _insert_member(executed_engine, "r1", "c1", "other-name")
        msg = str(excinfo.value)
        assert "chatroom_members.room_id, chatroom_members.instance_id" in msg
        assert is_room_member_integrity(excinfo.value) is False
        with pytest.raises(RoomError) as room_err:
            raise_typed_member_integrity(excinfo.value, "r1", "capture")
        assert not isinstance(room_err.value, MemberNameCollisionError)
