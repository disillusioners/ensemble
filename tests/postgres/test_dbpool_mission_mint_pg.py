"""F6-b / F6-c pins — the mission mint on real PostgreSQL (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F6.5/F6.7 and §5.2. Marker:
``postgres`` (auto-applied by ``tests/postgres/conftest.py``).

What these pins assert:

* **F6-b** — idempotency holds on a real scratch PG **UNDER
  CONCURRENCY**: a **concurrent double-mint for the same anchor** must
  not raise. (The MEASURED trigger: an 8-way concurrent mint with no
  barrier produced **7 real ``UniqueViolation``s** at base —
  ``BASELINE.md`` T5c / raw ``t5_fix_sites.out:2040-2053``; the forced
  overlap produced 5.) The sequential path was already idempotent
  (c2: ``ok: true``, ``rung: 1``) ⇒ the leg that must go GREEN is the
  CONCURRENT one.
* **F6-c** — the target DB **has** ``ux_mission_records_anchor_open``
  with a **byte-identical** predicate to
  ``OPEN_EPOCH_STATUS_SQL``; and the negative leg — with the index
  absent the upsert raises **42P10** (undefined table/index) ⇒ the
  failure is LOUD (never a silent double-mint).

Plantable mutations:

* remove the idempotency (revert to a bare INSERT) ⇒ the concurrent leg
  raises ``UniqueViolation`` ⇒ RED (F6-b);
* drop the partial unique index from the target DB ⇒ the upsert raises
  ``42P10`` ⇒ RED (F6-c).

Run (house form — scratch PG, per-variable env scrub, marker override)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5433 PG_TEST_DB=<scratch db> \
    PG_TEST_USER=<scratch role> PG_TEST_PASSWORD=<scratch pw> \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/postgres/test_dbpool_mission_mint_pg.py \
        --override-ini="addopts=" -m postgres -q --timeout=300
"""
from __future__ import annotations

import re
import threading
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlmodel import Session

# Register the mission-record tables BEFORE ``create_all`` runs.
import daemon.repositories.mission_record.models  # noqa: F401

from daemon.repositories.mission_record.models import (
    OPEN_EPOCH_STATUS_SQL,
    MissionRecord,
)
from daemon.repositories.mission_record.repository import MissionRecordRepository

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3

#: The concurrent fan-out (the measured trigger shape — 8-way, NO
#: barrier: the threads race on the anchor).
CONCURRENCY = 8


def _repo(pg_engine) -> MissionRecordRepository:
    return MissionRecordRepository(pg_engine)


def _normalize_predicate(predicate: str) -> str:
    """Normalize a PG-rendered index predicate for comparison.

    PostgreSQL renders ``status = 'open'`` as
    ``((status)::text = 'open'::text)`` — the casts and the redundant
    parentheses are PG's rendering, not a different predicate.
    """
    text_value = predicate.strip().rstrip(";").strip()
    text_value = text_value.replace("::text", "")
    # PG renders the column as ``(status)`` — drop the redundant
    # parentheses around a bare identifier.
    text_value = re.sub(r"\(([A-Za-z_][A-Za-z0-9_]*)\)", r"\1", text_value)
    while text_value.startswith("(") and text_value.endswith(")"):
        inner = text_value[1:-1].strip()
        if inner.count("(") != inner.count(")"):
            break
        text_value = inner
    return " ".join(text_value.split())


class TestF6bConcurrentDoubleMint:
    """F6-b — the concurrent double-mint must not raise."""

    def test_concurrent_mint_for_one_anchor_returns_one_mission_id(self, pg_engine):
        repo = _repo(pg_engine)
        anchor = "anchor-concurrent-f6"
        results: list[str] = []
        errors: list[str] = []
        lock = threading.Lock()

        def mint() -> None:
            try:
                record = MissionRecord(
                    anchor_instance_id=anchor,
                    founding_source="boot",
                    epoch_seq=1,
                )
                out = repo.insert_mission(record)
                with lock:
                    results.append(out.mission_id)
            except Exception as exc:  # noqa: BLE001 — the assertion is below
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=mint) for _ in range(CONCURRENCY)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, (
            "F6-b: the concurrent double-mint RAISED — the boot mint is "
            f"not idempotent under the measured trigger: {errors}"
        )
        assert len(results) == CONCURRENCY, (
            f"F6-b: {len(results)}/{CONCURRENCY} minters returned a row"
        )
        assert len(set(results)) == 1, (
            "F6-b: the concurrent minters returned DIFFERENT mission_ids "
            f"({len(set(results))} distinct) — the anchor has more than "
            "one open epoch"
        )

        with Session(pg_engine) as session:
            count = session.execute(
                text(
                    "select count(*) from mission_records "
                    "where anchor_instance_id = :a"
                ),
                {"a": anchor},
            ).scalar()
        assert count == 1, (
            f"F6-b: {count} open-epoch rows exist for one anchor"
        )


class TestF6cIndexPresence:
    """F6-c — the target DB has the partial unique index, byte-identical."""

    def test_index_exists_with_a_byte_identical_predicate(self, pg_engine):
        with pg_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select i.indexdef from pg_indexes i "
                    "join pg_class c on c.relname = i.indexname "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where i.indexname = 'ux_mission_records_anchor_open' "
                    "and n.nspname = current_schema()"
                )
            ).all()
        assert rows, (
            "F6-c: the target DB has NO ux_mission_records_anchor_open — "
            "the boot mint would raise 42P10 instead of being repaired"
        )
        indexdef = rows[0][0]
        assert "UNIQUE INDEX" in indexdef.upper()
        assert "anchor_instance_id" in indexdef
        # The predicate must be the SAME predicate as the shared
        # constant. PostgreSQL renders an index predicate in its own
        # normalized form (``((status)::text = 'open'::text)``) — the
        # comparison is therefore made on the NORMALIZED form, which is
        # the byte-identity the source contract pins (the model's
        # constant vs the partial index declaration).
        predicate = indexdef.split("WHERE", 1)[1].strip().rstrip(";").strip()
        normalized = _normalize_predicate(predicate)
        assert normalized == _normalize_predicate(OPEN_EPOCH_STATUS_SQL), (
            "F6-c: the index predicate on the target DB "
            f"({predicate!r}) is not the same predicate as "
            f"OPEN_EPOCH_STATUS_SQL ({OPEN_EPOCH_STATUS_SQL!r}) — the "
            "upsert's conflict target would not match the index"
        )

    def test_without_the_index_the_upsert_fails_loudly(self, pg_engine):
        """The negative leg: with the index absent the upsert must fail
        LOUDLY (42P10) — never a silent second open epoch."""
        with pg_engine.begin() as conn:
            conn.execute(
                text("drop index if exists ux_mission_records_anchor_open")
            )
        try:
            repo = _repo(pg_engine)
            with pytest.raises((ProgrammingError, IntegrityError)) as excinfo:
                repo.insert_mission(
                    MissionRecord(
                        anchor_instance_id="anchor-no-index",
                        founding_source="boot",
                        epoch_seq=1,
                    )
                )
            sqlstate = getattr(
                getattr(excinfo.value, "orig", None), "sqlstate", None
            )
            assert sqlstate == "42P10", (
                "F6-c: the missing-index failure was not the loud 42P10 "
                f"(invalid_column_reference) shape: sqlstate={sqlstate!r} "
                f"error={excinfo.value!r}"
            )
        finally:
            # Restore the index (the DDL is inside a transaction that is
            # rolled back below — this is the belt-and-braces path).
            with pg_engine.begin() as conn:
                conn.execute(
                    text(
                        "create unique index if not exists "
                        "ux_mission_records_anchor_open "
                        "on mission_records(anchor_instance_id) "
                        "where status = 'open'"
                    )
                )
