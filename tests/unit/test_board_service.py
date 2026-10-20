"""Board service behaviour tests (ari-tools Phase 1 — the Workbook).

Covers the phase1-plan §8 N5 matrix:

* create / get / update / list
* status transitions — UNRESTRICTED, including REOPENING a ``done`` item
* notes are APPEND-ONLY — the count grows monotonically, no update /
  delete code path exists, and the **SQL-capture guard** shows the ONLY
  statements touching ``board_notes`` are INSERT and SELECT
* tag filter, project filter, the ``status="open"`` helper
* the ``due_before`` filter INCLUDING the mixed-offset inversion probe
  (a ``+07:00`` offset must not invert the comparison) and the
  ``_to_utc_iso`` round-trip
* ``ORDER BY updated_at DESC`` (a contract, not an implementation detail)
* the id-collision retry-once contract (the service retries once; the
  AGENT never supplies the id)
* the child FKs are NO ACTION — a hard parent delete is refused by the
  DATABASE (the unit-test engine enables ``PRAGMA foreign_keys=ON``
  explicitly; a bare ``create_engine("sqlite://")`` has it OFF)
* batched hydration — ``list_items(with_notes=True)`` is at most THREE
  queries (items + one batched tags + one batched notes)
* an update of a missing id raises ``BoardItemNotFound``
* validation errors — naive ``due_at`` / ``due_before``, an
  out-of-range priority, an unknown status
* restart survival — the tables (and their six indexes) are
  re-created by the boot path and the rows survive a restart
* ``create_all`` idempotency
* write-lock concurrency — two threads appending notes produce BOTH rows

No HOME-derived or live-shaped path is resolved anywhere in this file:
every fixture path comes from pytest's ``tmp_path``.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Importing the models registers the board tables (and the ``projects``
# table the board's project_id FK points at) on ``SQLModel.metadata``.
# Without the ``projects`` registration the FK cannot be emitted.
from daemon.models.board import (  # noqa: F401
    BOARD_OPEN_STATUSES,
    BOARD_STATUSES,
    BoardItem,
    BoardItemNotFound,
    BoardItemTag,
    BoardNote,
    BoardValidationError,
    _now_iso,
    _to_utc_iso,
)
from daemon.repositories.project.models import Project  # noqa: F401
from daemon.services.board_service import BoardService

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_SOURCE = REPO_ROOT / "daemon" / "services" / "board_service.py"

#: The six indexes of phase1-plan §4.1 — names AND column lists.
EXPECTED_INDEXES: dict[str, list[str]] = {
    "idx_board_items_status_updated": ["status", "updated_at"],
    "idx_board_items_updated_at": ["updated_at"],
    "idx_board_items_project_id": ["project_id"],
    "idx_board_items_due_at": ["due_at"],
    "idx_board_item_tags_tag_item": ["tag", "item_id"],
    "idx_board_notes_item_id_ts": ["item_id", "ts"],
}

#: The canonical item shape (phase1-plan §7.2).
ITEM_FIELDS = {
    "id",
    "title",
    "body_md",
    "status",
    "tags",
    "project_id",
    "due_at",
    "priority",
    "linked_job_ids",
    "created_by_instance",
    "created_at",
    "updated_at",
}


def _make_engine(url: str = "sqlite://", *, fk_on: bool = True):
    """Build a SQLite engine with the daemon's PRAGMA posture.

    ``PRAGMA foreign_keys=ON`` is MANDATORY for any FK assertion — the
    bare ``create_engine("sqlite://")`` default has it OFF, so a
    NO-ACTION FK assertion against it would be vacuous.
    """
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    if fk_on:

        @event.listens_for(engine, "connect")
        def _set_pragma(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@pytest.fixture
def engine():
    eng = _make_engine()
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def service(engine):
    return BoardService(engine=engine)


# ── create / get / list ───────────────────────────────────────────────────


class TestCreateAndRead:
    def test_create_returns_the_canonical_item_shape(self, service):
        item = service.create_item(
            title="Ship the board",
            body_md="body text",
            created_by_instance="inst-a",
        )
        assert set(item) == ITEM_FIELDS
        assert item["id"].startswith("wi-")
        assert len(item["id"]) == len("wi-") + 8
        assert item["title"] == "Ship the board"
        assert item["body_md"] == "body text"
        assert item["status"] == "todo"
        assert item["tags"] == []
        assert item["project_id"] is None
        assert item["due_at"] is None
        assert item["priority"] is None
        assert item["linked_job_ids"] == []
        assert item["created_by_instance"] == "inst-a"
        # Timestamps are the fixed-width UTC form (microseconds always
        # present) — the lexicographic comparison depends on it.
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00",
            item["created_at"],
        )
        assert item["updated_at"] == item["created_at"]

    def test_agent_never_supplies_the_id(self, service):
        """The id is generated by the SERVICE — an agent cannot choose it."""
        import inspect

        params = inspect.signature(BoardService.create_item).parameters
        assert "id" not in params
        first = service.create_item(title="a", created_by_instance="i")
        second = service.create_item(title="b", created_by_instance="i")
        assert first["id"] != second["id"]

    def test_get_item_returns_the_item_with_its_notes(self, service):
        created = service.create_item(title="a", created_by_instance="i")
        service.update_item(
            item_id=created["id"], note="first", author_instance="i2"
        )
        service.update_item(
            item_id=created["id"], note="second", author_instance="i3"
        )
        fetched = service.get_item(item_id=created["id"])
        assert set(fetched) == ITEM_FIELDS | {"notes"}
        # Oldest-first.
        assert [n["text"] for n in fetched["notes"]] == ["first", "second"]
        assert fetched["notes"][0]["author_instance"] == "i2"

    def test_get_missing_item_raises_not_found(self, service):
        with pytest.raises(BoardItemNotFound):
            service.get_item(item_id="wi-00000000")

    def test_update_missing_item_raises_not_found(self, service):
        with pytest.raises(BoardItemNotFound):
            service.update_item(item_id="wi-00000000", author_instance="i")

    def test_list_returns_items_and_count(self, service):
        for i in range(3):
            service.create_item(title=f"t{i}", created_by_instance="i")
        result = service.list_items()
        assert result["count"] == 3
        assert len(result["items"]) == 3
        # The tool item shape carries NO notes key.
        assert all("notes" not in item for item in result["items"])


# ── status transitions ────────────────────────────────────────────────────


class TestStatusTransitions:
    def test_status_enum_is_the_four_value_set(self):
        assert BOARD_STATUSES == {
            "todo",
            "in_progress",
            "blocked",
            "done",
        }

    def test_transitions_are_unrestricted_and_done_is_reopenable(self, service):
        item = service.create_item(title="a", created_by_instance="i")
        for status in ("in_progress", "blocked", "done", "todo", "done"):
            updated = service.update_item(
                item_id=item["id"], status=status, author_instance="i"
            )
            assert updated["status"] == status

    def test_unknown_status_is_rejected(self, service):
        item = service.create_item(title="a", created_by_instance="i")
        with pytest.raises(BoardValidationError):
            service.update_item(
                item_id=item["id"], status="finished", author_instance="i"
            )

    def test_open_helper_expands_to_the_three_non_terminal_statuses(self, service):
        for status in ("todo", "in_progress", "blocked", "done"):
            service.create_item(
                title=status,
                tags=["marker"],
                created_by_instance="i",
            )
            fetched = service.get_item(item_id=service.list_items()["items"][0]["id"])
            service.update_item(
                item_id=fetched["id"], status=status, author_instance="i"
            )
        open_items = service.list_items(status="open")["items"]
        assert {i["status"] for i in open_items} == set(BOARD_OPEN_STATUSES)
        assert len(open_items) == 3
        # ``done`` is the only terminal status — it is excluded.
        assert all(i["status"] != "done" for i in open_items)


# ── notes: append-only ───────────────────────────────────────────────────


class TestNotesAreAppendOnly:
    def test_note_count_grows_monotonically(self, service):
        item = service.create_item(title="a", created_by_instance="i")
        counts = []
        for n in range(4):
            service.update_item(
                item_id=item["id"], note=f"note {n}", author_instance="i"
            )
            counts.append(len(service.get_item(item_id=item["id"])["notes"]))
        assert counts == [1, 2, 3, 4]

    def test_sql_capture_guard_only_sees_insert_and_select(self, engine, service):
        """The PRIMARY proof: the only statements touching ``board_notes``
        are INSERT and SELECT — never UPDATE, never DELETE."""
        captured: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def _capture(_conn, _cursor, statement, _params, _ctx, _many):
            captured.append(statement)

        item = service.create_item(title="a", created_by_instance="i")
        service.update_item(
            item_id=item["id"], note="a note", author_instance="i"
        )
        service.get_item(item_id=item["id"])
        service.list_items(with_notes=True)

        note_statements = [
            stmt for stmt in captured if "board_notes" in stmt.lower()
        ]
        assert note_statements, "no board_notes statement was captured"
        for stmt in note_statements:
            head = stmt.lstrip().upper()
            assert head.startswith(("INSERT", "SELECT")), (
                f"a non-append statement touched board_notes: {stmt}"
            )
            assert not head.startswith("UPDATE")
            assert not head.startswith("DELETE")

    def test_static_source_scan_has_no_note_mutation(self):
        """Cheap SECONDARY check: the service source contains no UPDATE
        or DELETE against ``board_notes`` (and no ``session.delete``)."""
        source = SERVICE_SOURCE.read_text(encoding="utf-8")
        lowered = source.lower()
        assert "update board_notes" not in lowered
        assert "delete from board_notes" not in lowered
        assert "session.delete(" not in lowered

    def test_update_item_does_not_expose_a_note_mutation_api(self, service):
        import inspect

        for name in ("update_note", "delete_note", "edit_note", "remove_note"):
            assert not hasattr(service, name), (
                f"a note-mutation API ({name}) exists — notes must be "
                "append-only"
            )
        # The only notes mutation is the private INSERT helper.
        assert hasattr(service, "_append_note")


# ── filters ───────────────────────────────────────────────────────────────


class TestFilters:
    def test_tag_filter(self, service):
        service.create_item(title="tagged", tags=["deploy"], created_by_instance="i")
        service.create_item(title="untagged", created_by_instance="i")
        result = service.list_items(tag="deploy")
        assert [i["title"] for i in result["items"]] == ["tagged"]

    def test_project_filter(self, engine, service):
        # The project FK is REAL — the referenced project must exist.
        with Session(engine) as session:
            session.add(Project(project_id="proj-1", name="P1"))
            session.add(Project(project_id="proj-2", name="P2"))
            session.commit()
        service.create_item(
            title="p1", project_id="proj-1", created_by_instance="i"
        )
        service.create_item(
            title="p2", project_id="proj-2", created_by_instance="i"
        )
        result = service.list_items(project_id="proj-2")
        assert [i["title"] for i in result["items"]] == ["p2"]

    def test_unknown_project_id_is_a_validation_error(self, service):
        """A dangling project FK is an authored validation error, not a
        server fault — the agent gets a usable message."""
        with pytest.raises(BoardValidationError) as excinfo:
            service.create_item(
                title="p", project_id="no-such-project", created_by_instance="i"
            )
        assert "project_id" in str(excinfo.value)

    def test_due_before_excludes_items_at_or_after_the_deadline(self, service):
        service.create_item(
            title="early",
            due_at="2026-10-20T00:30:00+00:00",
            created_by_instance="i",
        )
        service.create_item(
            title="late",
            due_at="2026-10-20T09:00:00+00:00",
            created_by_instance="i",
        )
        result = service.list_items(due_before="2026-10-20T01:00:00+00:00")
        assert [i["title"] for i in result["items"]] == ["early"]

    def test_due_before_mixed_offset_does_not_invert(self, service):
        """R11 — the inversion probe.

        ``2026-10-20T08:00:00+07:00`` is 01:00Z. A raw TEXT comparison
        would rank the stored ``01:xx`` row as GREATER than the raw
        ``08:00`` string and the filter would return the LATER items
        and drop the EARLIER ones. Normalised, the filter must return
        only the row that is genuinely before 01:00Z.
        """
        service.create_item(
            title="before",
            due_at="2026-10-20T00:30:00+00:00",
            created_by_instance="i",
        )
        service.create_item(
            title="at",
            due_at="2026-10-20T01:00:00+00:00",
            created_by_instance="i",
        )
        service.create_item(
            title="after",
            due_at="2026-10-20T02:00:00+00:00",
            created_by_instance="i",
        )
        result = service.list_items(due_before="2026-10-20T08:00:00+07:00")
        assert [i["title"] for i in result["items"]] == ["before"]

    def test_due_at_is_stored_as_fixed_width_utc(self, service):
        item = service.create_item(
            title="a",
            due_at="2026-10-20T08:00:00+07:00",
            created_by_instance="i",
        )
        assert item["due_at"] == "2026-10-20T01:00:00.000000+00:00"

    def test_to_utc_iso_round_trip_is_byte_identical(self):
        for raw in (
            "2026-10-20T08:00:00+07:00",
            "2026-10-20T01:55:40.123456+00:00",
            "2026-10-20T01:55:40Z",
        ):
            once = _to_utc_iso(raw)
            assert _to_utc_iso(once) == once

    def test_naive_due_at_is_rejected(self, service):
        with pytest.raises(BoardValidationError):
            service.create_item(
                title="a", due_at="2026-10-20T08:00:00", created_by_instance="i"
            )

    def test_naive_due_before_is_rejected(self, service):
        with pytest.raises(BoardValidationError):
            service.list_items(due_before="2026-10-20T08:00:00")

    def test_ordering_is_updated_at_desc(self, engine, service):
        """``ORDER BY updated_at DESC`` is a CONTRACT."""
        for title in ("a", "b", "c"):
            service.create_item(title=title, created_by_instance="i")
        # Force a deterministic ordering (the clock alone could tie).
        with engine.begin() as conn:
            for title, stamp in (
                ("a", "2026-01-01T00:00:00.000000+00:00"),
                ("b", "2026-01-02T00:00:00.000000+00:00"),
                ("c", "2026-01-03T00:00:00.000000+00:00"),
            ):
                conn.execute(
                    text(
                        "UPDATE board_items SET updated_at = :stamp "
                        "WHERE title = :title"
                    ),
                    {"stamp": stamp, "title": title},
                )
        titles = [i["title"] for i in service.list_items()["items"]]
        assert titles == ["c", "b", "a"]


# ── validation ───────────────────────────────────────────────────────────


class TestValidation:
    def test_empty_title_is_rejected(self, service):
        with pytest.raises(BoardValidationError):
            service.create_item(title="   ", created_by_instance="i")

    def test_over_long_title_is_rejected(self, service):
        with pytest.raises(BoardValidationError):
            service.create_item(title="x" * 513, created_by_instance="i")

    def test_priority_out_of_range_is_rejected(self, service):
        for bad in (0, 6, -1):
            with pytest.raises(BoardValidationError):
                service.create_item(
                    title="a", priority=bad, created_by_instance="i"
                )

    def test_priority_in_range_is_accepted(self, service):
        for good in (1, 3, 5):
            item = service.create_item(
                title="a", priority=good, created_by_instance="i"
            )
            assert item["priority"] == good

    def test_due_at_is_replaceable_but_not_clearable(self, service):
        item = service.create_item(
            title="a",
            due_at="2026-10-20T08:00:00+07:00",
            created_by_instance="i",
        )
        replaced = service.update_item(
            item_id=item["id"],
            due_at="2026-10-21T09:00:00+07:00",
            author_instance="i",
        )
        assert replaced["due_at"] == "2026-10-21T02:00:00.000000+00:00"
        # ``due_at=None`` means "no change" — there is no clear path.
        untouched = service.update_item(
            item_id=item["id"], due_at=None, author_instance="i"
        )
        assert untouched["due_at"] == "2026-10-21T02:00:00.000000+00:00"

    def test_tags_are_deduplicated_in_the_service(self, service):
        """The junction PK dedupes ROWS; the SERVICE dedupes the INPUT."""
        item = service.create_item(
            title="a", tags=["x", "y", "x", "x"], created_by_instance="i"
        )
        # Order-preserving, first occurrence wins.
        assert item["tags"] == ["x", "y"]
        assert service.get_item(item_id=item["id"])["tags"] == ["x", "y"]

    def test_linked_job_ids_only_grow(self, service):
        item = service.create_item(title="a", created_by_instance="i")
        first = service.update_item(
            item_id=item["id"], add_job_id="job-1", author_instance="i"
        )
        assert first["linked_job_ids"] == ["job-1"]
        again = service.update_item(
            item_id=item["id"], add_job_id="job-1", author_instance="i"
        )
        assert again["linked_job_ids"] == ["job-1"]
        second = service.update_item(
            item_id=item["id"], add_job_id="job-2", author_instance="i"
        )
        assert second["linked_job_ids"] == ["job-1", "job-2"]

    def test_no_op_update_does_not_bump_updated_at(self, service):
        item = service.create_item(title="a", created_by_instance="i")
        result = service.update_item(item_id=item["id"], author_instance="i")
        assert result["updated_at"] == item["updated_at"]
        assert result["note_appended"] is False

    def test_update_bumps_updated_at(self, service):
        item = service.create_item(title="a", created_by_instance="i")
        result = service.update_item(
            item_id=item["id"], status="done", author_instance="i"
        )
        assert result["updated_at"] > item["updated_at"]


# ── id collision: the SERVICE retries once, the agent never sees it ──────


class TestIdCollisionRetryOnce:
    def test_service_retries_once_with_a_fresh_id(self, service, monkeypatch):
        existing = service.create_item(title="existing", created_by_instance="i")
        minted = [existing["id"], "wi-freshid1"]

        def _fake_new_item_id():
            return minted.pop(0)

        monkeypatch.setattr(
            BoardService, "_new_item_id", staticmethod(_fake_new_item_id)
        )
        created = service.create_item(title="second", created_by_instance="i")
        # The retry produced a FRESH id — the agent never sees a collision.
        assert created["id"] == "wi-freshid1"
        assert created["title"] == "second"
        # The pre-existing row is untouched.
        assert service.get_item(item_id=existing["id"])["title"] == "existing"

    def test_second_collision_raises_a_generic_error(self, service, monkeypatch):
        from daemon.services.board_service import BoardError

        existing = service.create_item(title="existing", created_by_instance="i")
        monkeypatch.setattr(
            BoardService,
            "_new_item_id",
            staticmethod(lambda: existing["id"]),
        )
        with pytest.raises(BoardError) as excinfo:
            service.create_item(title="second", created_by_instance="i")
        assert "board operation failed" in str(excinfo.value)


# ── the child FKs are NO ACTION ──────────────────────────────────────────


class TestChildForeignKeysAreNoAction:
    def test_hard_delete_of_the_parent_is_refused(self, engine, service):
        item = service.create_item(
            title="a", tags=["t"], created_by_instance="i"
        )
        service.update_item(
            item_id=item["id"], note="a note", author_instance="i"
        )
        with engine.begin() as conn:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text("DELETE FROM board_items WHERE id = :id"),
                    {"id": item["id"]},
                )
        assert "FOREIGN KEY" in str(excinfo.value).upper()

    def test_the_child_fks_declare_no_ondelete_clause(self):
        """The model must carry NO ``ondelete=`` on the two child FKs."""
        for table, column in (
            (BoardNote.__table__, "item_id"),
            (BoardItemTag.__table__, "item_id"),
        ):
            fk = next(
                f for f in table.foreign_keys if f.parent.name == column
            )
            assert fk.ondelete is None, (
                f"{table.name}.{column} declares ondelete={fk.ondelete!r} — "
                "the child FK must be NO ACTION"
            )

    def test_the_project_fk_declares_on_delete_set_null(self):
        fk = next(
            f
            for f in BoardItem.__table__.foreign_keys
            if f.parent.name == "project_id"
        )
        assert fk.ondelete == "SET NULL"


# ── batched hydration ────────────────────────────────────────────────────


class TestBatchedHydration:
    def test_list_with_notes_is_at_most_three_queries(self, engine, service):
        for i in range(5):
            item = service.create_item(
                title=f"t{i}", tags=["bulk"], created_by_instance="i"
            )
            service.update_item(
                item_id=item["id"], note=f"n{i}", author_instance="i"
            )

        captured: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def _capture(_conn, _cursor, statement, _params, _ctx, _many):
            captured.append(statement)

        result = service.list_items(with_notes=True)
        assert result["count"] == 5
        # At most 3 queries: items + ONE batched tags + ONE batched notes.
        assert len(captured) <= 3, (
            f"expected <= 3 queries, got {len(captured)}: {captured}"
        )
        # The notes and tags are each loaded with ONE IN (...) query.
        tag_queries = [s for s in captured if "board_item_tags" in s]
        note_queries = [s for s in captured if "board_notes" in s]
        assert len(tag_queries) == 1
        assert len(note_queries) == 1
        assert " IN (" in tag_queries[0]
        assert " IN (" in note_queries[0]

    def test_list_without_notes_is_two_queries(self, engine, service):
        service.create_item(title="a", tags=["t"], created_by_instance="i")
        captured: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def _capture(_conn, _cursor, statement, _params, _ctx, _many):
            captured.append(statement)

        service.list_items()
        assert len(captured) == 2


# ── restart survival + create_all idempotency ────────────────────────────


class TestRestartSurvival:
    def test_rows_survive_a_restart_and_the_indexes_exist(self, tmp_path):
        db_path = tmp_path / "board_restart.db"
        url = f"sqlite:///{db_path}"

        eng_a = _make_engine(url)
        SQLModel.metadata.create_all(eng_a)
        svc_a = BoardService(engine=eng_a)
        item = svc_a.create_item(
            title="survivor", tags=["keep"], created_by_instance="inst-a"
        )
        svc_a.update_item(
            item_id=item["id"], note="a note", author_instance="inst-a"
        )
        eng_a.dispose()

        # ── restart: a fresh engine + a fresh service over the SAME db.
        eng_b = _make_engine(url)
        svc_b = BoardService(engine=eng_b)
        fetched = svc_b.get_item(item_id=item["id"])
        assert fetched["title"] == "survivor"
        assert fetched["tags"] == ["keep"]
        assert [n["text"] for n in fetched["notes"]] == ["a note"]
        eng_b.dispose()

    def test_create_all_recreates_the_tables_and_their_six_indexes(self, engine):
        inspector = inspect(engine)
        for name, columns in EXPECTED_INDEXES.items():
            table = _table_for_index(name)
            indexes = {ix["name"]: ix for ix in inspector.get_indexes(table)}
            assert name in indexes, f"missing index {name} on {table}"
            assert indexes[name]["column_names"] == columns

    def test_create_all_is_idempotent(self, engine):
        SQLModel.metadata.create_all(engine)
        SQLModel.metadata.create_all(engine)
        inspector = inspect(engine)
        for table in ("board_items", "board_notes", "board_item_tags"):
            assert table in inspector.get_table_names()

    def test_dropped_tables_are_recreated_by_the_boot_path(self, engine):
        inspector = inspect(engine)
        assert "board_items" in inspector.get_table_names()
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE board_item_tags"))
            conn.execute(text("DROP TABLE board_notes"))
            conn.execute(text("DROP TABLE board_items"))
        assert "board_items" not in inspect(engine).get_table_names()
        # The boot path: create_all FIRST, then the migration runner.
        SQLModel.metadata.create_all(engine)
        inspector = inspect(engine)
        for table in ("board_items", "board_notes", "board_item_tags"):
            assert table in inspector.get_table_names()
        for name, columns in EXPECTED_INDEXES.items():
            table = _table_for_index(name)
            indexes = {ix["name"]: ix for ix in inspector.get_indexes(table)}
            assert indexes[name]["column_names"] == columns


def _table_for_index(index_name: str) -> str:
    """Map an index name to the table it lives on."""
    for table, indexes in EXPECTED_INDEXES_BY_TABLE.items():
        if index_name in indexes:
            return table
    raise AssertionError(f"unknown index {index_name}")


EXPECTED_INDEXES_BY_TABLE: dict[str, dict[str, list[str]]] = {
    "board_items": {
        "idx_board_items_status_updated": ["status", "updated_at"],
        "idx_board_items_updated_at": ["updated_at"],
        "idx_board_items_project_id": ["project_id"],
        "idx_board_items_due_at": ["due_at"],
    },
    "board_notes": {"idx_board_notes_item_id_ts": ["item_id", "ts"]},
    "board_item_tags": {"idx_board_item_tags_tag_item": ["tag", "item_id"]},
}


# ── write-lock concurrency ───────────────────────────────────────────────


class TestWriteLockConcurrency:
    def test_two_threads_appending_notes_both_land(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'board_concurrent.db'}"
        eng = _make_engine(url)
        SQLModel.metadata.create_all(eng)
        svc = BoardService(engine=eng)
        item = svc.create_item(title="a", created_by_instance="i")

        errors: list[BaseException] = []

        def _append(text: str) -> None:
            try:
                svc.update_item(
                    item_id=item["id"], note=text, author_instance="i"
                )
            except BaseException as exc:  # noqa: BLE001 — test capture
                errors.append(exc)

        threads = [
            threading.Thread(target=_append, args=(f"note {n}",))
            for n in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        notes = svc.get_item(item_id=item["id"])["notes"]
        assert len(notes) == 4
        assert sorted(n["text"] for n in notes) == [
            f"note {n}" for n in range(4)
        ]
        eng.dispose()


# ── the global namespace ─────────────────────────────────────────────────


class TestGlobalNamespace:
    def test_two_services_over_two_instances_share_one_board(self, engine):
        """The board is ONE global namespace — no per-instance partition."""
        ari = BoardService(engine=engine)
        leader = BoardService(engine=engine)
        created = ari.create_item(title="shared", created_by_instance="inst-ari")
        seen = leader.list_items()
        assert [i["id"] for i in seen["items"]] == [created["id"]]
        assert seen["items"][0]["created_by_instance"] == "inst-ari"
