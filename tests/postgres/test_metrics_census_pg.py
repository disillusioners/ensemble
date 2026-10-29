"""P0 T7 — the PostgreSQL census against REAL PostgreSQL.

What this leg proves that the fake-connection unit leg cannot:

* the census opens a **DIRECT, NON-POOLED** connection to a real
  server (the connection object is a raw psycopg connection — never a
  pool);
* the census's connection really carries
  ``application_name='ensemble-metrics-census'`` and
  ``statement_timeout=5 s``;
* the census really reads the live ``pg_stat_activity``;
* the **self-exclusion** really excludes ONLY the census's own
  ``application_name`` (an impostor connection is invisible to the
  census; every other backend is counted);
* the **fragment→site map** really classifies an idle-in-transaction
  backend (the sum-ties invariant holds:
  ``sum(sites) == backends{state="idle in transaction"}``).

THE SCRATCH-DB FENCE (a dependency edge, BEFORE any fixture touches the
DB)
------------------------------------------------------------------------

This module OVERRIDES the shared ``pg_engine`` fixture with a
module-local one whose FIRST action is the fence check: the target
database name must be an EXACT member of :data:`ALLOWED_SCRATCH_DBS` —
never a substring / prefix heuristic. A disallowed name is an ERROR
(and it is an error BEFORE any connect attempt — the ordering is
proven by the two child-run legs below: a disallowed name against an
UNREACHABLE port still reports the FENCE, while an allowed name
against the same unreachable port reports a SKIP).

The census touches NO application table, so the shared TRUNCATE
fixture is neutralized for this module and no schema is created.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from daemon.services import metrics_census as mc

#: The EXACT set of database names this module may touch. Membership is
#: EXACT — ``ensemble_prod_test`` is NOT scratch.
ALLOWED_SCRATCH_DBS = frozenset(
    {"ensemble_dev_test_only", "ensemble_test_only"}
)

#: The fence's message fragment — asserted in the child-run legs.
FENCE_MARKER = "PG_TEST_DB is not in the hard scratch allowlist"

#: The child-run marker (the fence-proof children must not recurse).
_CHILD_ENV = "ENSEMBLE_METRICS_CENSUS_FENCE_CHILD"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _pg_url() -> str:
    host = os.environ.get("PG_TEST_HOST", "localhost")
    port = os.environ.get("PG_TEST_PORT", "5432")
    db = os.environ.get("PG_TEST_DB", "")
    user = os.environ.get("PG_TEST_USER", "ensemble")
    password = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
    return (
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"
    )


def _probe(url: str) -> Engine | None:
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except (OperationalError, DBAPIError):
        return None
    except Exception:  # pragma: no cover — environment-dependent
        return None


@pytest.fixture(scope="module")
def pg_engine():
    """The module-local ``pg_engine`` — the FENCE runs FIRST.

    Order (this ordering IS the fence):

    1. the EXACT-name membership check (a disallowed name ⇒ ERROR, no
       connect attempt);
    2. the reachability probe (an allowed name + an unreachable server
       ⇒ SKIP);
    3. the engine.
    """
    db = os.environ.get("PG_TEST_DB", "")
    if db not in ALLOWED_SCRATCH_DBS:
        pytest.fail(
            f"FENCE: {FENCE_MARKER} — PG_TEST_DB={db!r} is not one of "
            f"{sorted(ALLOWED_SCRATCH_DBS)} (EXACT-name membership; no "
            f"substring heuristic). Refused BEFORE any connect attempt."
        )
    engine = _probe(_pg_url())
    if engine is None:
        pytest.skip(f"PostgreSQL not available at {_pg_url()}")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _pg_truncate_tables():
    """Neutralize the shared TRUNCATE fixture.

    The census reads ``pg_stat_activity`` only — it never touches an
    application table, so the shared autouse fixture (which truncates
    every SQLModel table) must not run for this module.
    """
    yield


def _dsn(engine: Engine) -> str:
    dsn = mc.resolve_dsn(engine)
    assert dsn, "the census DSN could not be resolved"
    return dsn


# ─── the non-pooled connection ────────────────────────────────────────────


def test_the_census_connection_is_direct_and_not_a_pool(pg_engine):
    """A raw psycopg connection — NEVER a pool (the census must not
    consume a pool slot)."""
    import psycopg

    conn = mc.connect_census(_dsn(pg_engine))
    try:
        module = type(conn).__module__
        assert module.startswith("psycopg")
        assert "pool" not in module
        assert not hasattr(conn, "get_stats")  # a pool API
    finally:
        conn.close()


def test_the_census_connection_carries_its_identity_and_timeout(pg_engine):
    conn = mc.connect_census(_dsn(pg_engine))
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW application_name")
            assert cur.fetchone()[0] == "ensemble-metrics-census"
            cur.execute("SHOW statement_timeout")
            assert cur.fetchone()[0] == "5s"
    finally:
        conn.close()


def test_the_connect_timeout_is_three_seconds():
    assert mc.CENSUS_CONNECT_TIMEOUT_S == 3


# ─── the live census ──────────────────────────────────────────────────────


def test_the_census_reads_the_live_cluster(pg_engine):
    snapshot = mc.CensusSampler(dsn=_dsn(pg_engine)).collect()
    assert snapshot is not None
    assert snapshot.last_success_epoch is not None
    assert snapshot.state_counts  # the live pg_stat_activity was read


def test_the_census_excludes_only_its_own_application_name(pg_engine):
    """An impostor connection wearing the census's ``application_name``
    is INVISIBLE to the census; every other backend is counted."""
    import psycopg

    dsn = _dsn(pg_engine)
    impostor = psycopg.connect(dsn, application_name="ensemble-metrics-census")
    other = psycopg.connect(dsn, application_name="metrics-census-probe")
    try:
        snapshot = mc.CensusSampler(dsn=dsn).collect()
        assert snapshot is not None
        with psycopg.connect(dsn) as probe:
            with probe.cursor() as cur:
                # The impostor IS visible to the server…
                cur.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name = 'ensemble-metrics-census' "
                    "AND pid <> pg_backend_pid()"
                )
                assert cur.fetchone()[0] >= 1
                # …but the census counts exactly the OTHER backends
                # (the probe's OWN backend is excluded from the
                # expectation — it did not exist when the census ran).
                cur.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND COALESCE(application_name, '') <> "
                    "'ensemble-metrics-census' "
                    "AND pid <> pg_backend_pid()"
                )
                expected = cur.fetchone()[0]
        assert sum(snapshot.state_counts.values()) == expected
    finally:
        impostor.close()
        other.close()


def test_the_site_map_classifies_a_real_idle_in_transaction_backend(
    pg_engine,
):
    """The fragment→site map, evaluated SERVER-SIDE, really classifies
    a live idle-in-transaction backend."""
    import psycopg

    dsn = _dsn(pg_engine)
    with psycopg.connect(dsn) as setup:
        with setup.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS instances (id text primary key)"
            )
    probe = psycopg.connect(dsn, application_name="metrics-census-probe")
    probe.autocommit = False
    try:
        with probe.cursor() as cur:
            # A LITERAL query — no bound parameters — so the statement
            # text in pg_stat_activity carries the fragment verbatim.
            cur.execute(
                "SELECT id FROM instances WHERE instances.id = 'no-such-id'"
            )
            cur.fetchall()
            snapshot = mc.CensusSampler(dsn=dsn).collect()
            assert snapshot is not None
            assert snapshot.idle_in_txn_by_site["instance_parent_lookup"] >= 1
            # The sum-ties invariant: the sites always add up to the
            # idle-in-transaction backend count.
            assert snapshot.idle_in_txn_total == snapshot.state_counts.get(
                "idle in transaction", 0
            )
    finally:
        probe.close()


def test_the_census_never_raises_against_real_postgres(pg_engine):
    """A failure never raises into the daemon — the collector returns
    ``None`` and the last-success stamp stays stale."""
    sampler = mc.CensusSampler(dsn="postgresql://nobody@127.0.0.1:1/none")
    assert sampler.collect() is None
    assert sampler.snapshot is None


# ─── the fence ordering proof (child runs) ────────────────────────────────


def test_the_fence_probe_uses_the_module_local_fixture(pg_engine):
    """The probe the child runs request: it touches the module-local
    fixture (and only that one)."""
    assert pg_engine is not None


def _run_child(db_name: str, port: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PG_TEST_DB"] = db_name
    env["PG_TEST_PORT"] = port
    env[_CHILD_ENV] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__).resolve()),
            "-k",
            "test_the_fence_probe_uses_the_module_local_fixture",
            "--override-ini=addopts=",
            "-m",
            "postgres",
            "-q",
            "--no-header",
            "-p",
            "no:randomly",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=300,
    )


@pytest.mark.skipif(
    os.environ.get(_CHILD_ENV) == "1",
    reason="fence-proof child run (no recursion)",
)
def test_the_fence_rejects_a_disallowed_db_name_before_connecting():
    """A disallowed name + an UNREACHABLE port ⇒ the FENCE error (not a
    skip, not a connection error) — the fence is a dependency edge that
    runs BEFORE any fixture touches the DB."""
    result = _run_child("ensemble_prod", "1")
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert FENCE_MARKER in output, output
    # The refusal is the FENCE's, not a connection failure.
    assert "connection refused" not in output.lower()


@pytest.mark.skipif(
    os.environ.get(_CHILD_ENV) == "1",
    reason="fence-proof child run (no recursion)",
)
def test_an_allowed_db_name_reaches_the_probe_and_skips_when_unreachable():
    """An allowed name + the SAME unreachable port ⇒ SKIP — the fence
    let the request through to the probe."""
    result = _run_child("ensemble_dev_test_only", "1")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "skipped" in output.lower(), output
    assert FENCE_MARKER not in output, output
