"""JSONB concurrent key updates via atomic jsonb_set — no lost updates under concurrent writers.

Scenario 6 of Phase 3 PostgreSQL concurrency tests.

This file validates that PostgreSQL's ``jsonb_set`` performs an atomic,
read-modify-write on a single ``UPDATE`` statement, so that when N
concurrent writers each set a *different* key on the same JSONB column,
no writer's key is lost. The key safety property: every key/value set by
any of the writers must still be present in the final column value.

Why this is interesting
-----------------------

The naive Python implementation would be::

    row = session.get(Instance, id)
    row.metadata[key] = value
    session.commit()

Two concurrent transactions doing that on the same row under READ
COMMITTED would both read the initial metadata, both write back their
own dict, and the second ``COMMIT`` would silently clobber the first
writer's key — a *lost update*.

The production fix used in the daemon's repositories (see
``daemon/repositories/source/repository.py``'s ``increment_scheduler_run_counter``
for the gold-standard pattern) is to push the read-modify-write into a
single SQL statement that holds the row lock for its entire duration::

    UPDATE instances
       SET metadata = jsonb_set(metadata, '{key}', to_jsonb('value'::text), true)
     WHERE instance_id = :id

``jsonb_set`` reads the current committed ``metadata``, sets the
requested key, and writes the result back — all under the row's
exclusive lock acquired by the ``UPDATE``. Subsequent UPDATEs against
the same row see the freshly-committed value (thanks to READ COMMITTED
+ EvalPlanQual recheck on the row's ``xmin``) and accumulate their keys
on top of it. The end state must contain all N keys.

Test mechanics
--------------

* N = 4 concurrent writers, each setting a distinct top-level key
  (``a``/``b``/``c``/``d``).
* Each writer runs in its own thread with its own raw-SQLAlchemy
  connection (N > 2, so we cannot use the ``pg_two_connections`` fixture
  and must create the additional connections from ``pg_engine`` directly).
* ``threading.Barrier(N)`` releases all writers simultaneously so they
  race against the row lock rather than executing in some implicit
  ordering determined by thread start-up time.
* After all threads join, we read the row's ``metadata`` once and
  assert every key is present with the expected value.

Note on serialization
---------------------

Even with the barrier, PostgreSQL's row lock will serialize the writers
physically: the first writer to acquire the lock holds it for the
duration of its ``UPDATE``, the others queue. That serialization is
*fine and expected* — the property under test is that no key is lost
in the final state, not that the writes overlap in wall-clock time.

Flakiness detection
-------------------

Each test runs ``@pytest.mark.parametrize("run", range(5))`` so a single
bug only visible under a specific scheduler interleaving will be caught
with high probability without requiring thousands of iterations.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text

# Importing the Instance model registers it in ``SQLModel.metadata`` so
# that ``SQLModel.metadata.create_all(engine)`` (run by the
# ``pg_engine`` session fixture in ``tests/postgres/conftest.py``)
# actually creates the ``instances`` table. The ORM is not used by this
# test — every statement is raw SQL — but without the import the table
# would not exist at all and the seed INSERT below would error with
# ``UndefinedTable``.
from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: F401


# Number of concurrent writers. Each sets a distinct key, so the final
# metadata dict must contain exactly this many keys (no lost updates).
N_WRITERS = 4

# Keys and matching values written by writers 0..N_WRITERS-1. The
# values are intentionally distinguishable so a clobber (a writer's key
# being overwritten by another writer's value) would be detectable as a
# mismatched value, not just a missing key.
_KEYS = ["a", "b", "c", "d"]
_VALUES = [f"value_{i}" for i in range(N_WRITERS)]


def _insert_empty_metadata_instance(conn, instance_id: str) -> None:
    """Insert a fresh ``instances`` row whose ``metadata`` starts as ``'{}'``.

    The Instance model declares Python defaults for several columns
    (``status``, ``children``, ``waiting_for``, ``version``, ``created_at``,
    ``updated_at``) but SQLModel does not always synthesize a matching
    ``server_default`` for those columns on PostgreSQL, so an INSERT
    that omits them trips ``NotNullViolation``. We pass values
    explicitly to avoid relying on the Python-side default_factory.

    ``metadata`` starts as ``'{}'::jsonb`` so that ``jsonb_set`` has a
    JSONB object to operate on (calling ``jsonb_set`` on a SQL NULL
    would raise ``ERROR:  jsonb_set called on NULL``).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        text(
            """
            INSERT INTO instances (
                instance_id, agent_id, agent_dir,
                status, children, waiting_for, version,
                created_at, updated_at,
                metadata
            ) VALUES (
                :instance_id, :agent_id, :agent_dir,
                :status, :children, :waiting_for, :version,
                :created_at, :updated_at,
                '{}'::jsonb
            )
            """
        ),
        {
            "instance_id": instance_id,
            "agent_id": "concurrency-jsonb-test",
            "agent_dir": "/tmp/concurrency-jsonb-test",
            "status": InstanceStatus.IDLE.value,
            "children": "[]",
            "waiting_for": 0,
            "version": 1,
            "created_at": now_iso,
            "updated_at": now_iso,
        },
    )
    conn.commit()


def _read_metadata(conn, instance_id: str) -> dict[str, Any]:
    """Return the row's ``metadata`` decoded as a Python dict.

    Uses ``jsonb_each_text`` to flatten the JSONB into ``(key, value)``
    rows so we don't have to rely on JSON-deserialization in psycopg —
    any deserialization discrepancy is a possible source of false
    negatives, and this path is pure text.
    """
    rows = conn.execute(
        text(
            """
            SELECT key, value
              FROM jsonb_each_text(
                  (SELECT metadata FROM instances WHERE instance_id = :instance_id)
              )
            """
        ),
        {"instance_id": instance_id},
    ).fetchall()
    return {k: v for (k, v) in rows}


def _jsonb_set_key(conn, instance_id: str, key: str, value: str) -> None:
    """Run one writer's UPDATE: atomic ``jsonb_set`` of a single key.

    The ``create_if_missing`` flag (last ``true``) lets the call work
    even on an empty object — though in this test the row already has
    ``'{}'::jsonb``, so ``create_if_missing`` is mostly defensive.

    The value parameter is bound as text (psycopg maps Python ``str``
    to PostgreSQL ``text``). We use ``CAST(:value AS text)`` rather
    than the shorthand ``:value::text`` because SQLAlchemy's
    ``named`` paramstyle treats every ``:foo`` token as a parameter
    binding — so ``:value::text`` would parse as the two parameters
    ``:value`` and ``:text``. The explicit ``CAST`` form sidesteps that
    while still giving ``to_jsonb`` a concrete ``text`` input (it's a
    polymorphic function and would otherwise fail with
    ``could not determine polymorphic type because input has type
    unknown``).

    Each caller is responsible for committing the transaction; we do
    not commit here so the barrier release stays tight: all threads
    reach the UPDATE at the same moment, then each blocks on the row
    lock, then commits as soon as the lock is released.
    """
    conn.execute(
        text(
            """
            UPDATE instances
               SET metadata = jsonb_set(
                   metadata,
                   :key_path,
                   to_jsonb(CAST(:value AS text)),
                   true
               )
             WHERE instance_id = :instance_id
            """
        ),
        {
            "key_path": f"{{{key}}}",
            "value": value,
            "instance_id": instance_id,
        },
    )
    conn.commit()


@pytest.mark.parametrize("run", range(5))
def test_concurrent_jsonb_key_writes_no_lost_updates(pg_engine, run):
    """N concurrent ``jsonb_set`` writers must all leave their key in the JSONB column.

    Runs ``N_WRITERS = 4`` threads, each with its own raw-SQLAlchemy
    connection, all released at the same instant by a
    ``threading.Barrier``. Each thread performs a single ``UPDATE``
    using ``jsonb_set`` to set a *distinct* top-level key on the same
    row.

    Assertions:

    * Every writer's key is present in the final ``metadata``.
    * Every writer's value matches the expected string.

    If ``jsonb_set`` were *not* atomic (e.g. if the daemon's code were
    to fall back to a Python read-modify-write under the hood), this
    test would fail with one or more missing/overwritten keys.

    The test is parametrized over ``run in range(5)`` to give the GIL
    and PG scheduler enough chances to surface a non-atomic
    implementation's race; a single run would still catch the bug most
    of the time, but 5 runs is the floor for "high confidence".
    """
    instance_id = f"jsonb-conc-{uuid.uuid4()}"
    barrier = threading.Barrier(N_WRITERS)
    errors: list[BaseException] = []

    def writer(idx: int) -> None:
        # One connection per thread. Creating it inside the worker (not
        # passing it in) means each thread owns its own transaction
        # lifecycle and is responsible for its own close.
        conn = pg_engine.connect()
        try:
            # Synchronize: every thread reaches the barrier, then all
            # proceed to the UPDATE at the same instant. The PG row
            # lock serializes them physically; what we care about is
            # that each UPDATE sees the latest committed metadata, so
            # no key gets clobbered.
            barrier.wait(timeout=10)
            _jsonb_set_key(conn, instance_id, _KEYS[idx], _VALUES[idx])
        except BaseException as exc:  # noqa: BLE001 - surface from thread
            errors.append(exc)
        finally:
            conn.close()

    # Seed the row with empty metadata. Done on a fresh connection so
    # the writer threads see a clean committed starting point.
    seed_conn = pg_engine.connect()
    try:
        _insert_empty_metadata_instance(seed_conn, instance_id)
    finally:
        seed_conn.close()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(N_WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Writer thread(s) raised: {errors!r}"
    for t in threads:
        assert not t.is_alive(), "A writer thread did not finish in time"

    # Read final state on a fresh connection so we get a clean
    # snapshot independent of the writers' (now-closed) transactions.
    verify_conn = pg_engine.connect()
    try:
        final_metadata = _read_metadata(verify_conn, instance_id)
    finally:
        verify_conn.close()

    # No lost updates: every key must be present.
    missing = [k for k in _KEYS if k not in final_metadata]
    assert not missing, (
        f"Lost-update bug: jsonb_set dropped key(s) {missing!r}; "
        f"final metadata={final_metadata!r}"
    )

    # No value corruption: every value must match what its writer
    # installed. A clobber-by-Python-RMW would show up here as a key
    # mapping to a value it was never written with.
    expected = dict(zip(_KEYS, _VALUES))
    mismatched = {
        k: {"expected": expected[k], "got": final_metadata[k]}
        for k in _KEYS
        if final_metadata[k] != expected[k]
    }
    assert not mismatched, (
        f"jsonb_set wrote wrong values for some key(s): {mismatched!r}; "
        f"final metadata={final_metadata!r}"
    )

    # Belt-and-braces: the row's metadata dict must have exactly the
    # keys we wrote (no extra junk, no duplicates from a non-atomic
    # implementation that might merge arrays or append).
    assert set(final_metadata.keys()) == set(_KEYS), (
        f"Unexpected keys in final metadata: {final_metadata!r}"
    )