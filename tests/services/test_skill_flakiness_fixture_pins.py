"""G pins — flakiness stabilization (phase1-plan §11; decisions.md DS-29/30/37).

Registered pack: ``skill_evolution_unit_test`` (EXTENDED) — see
``.agents/tester/PACKS.md``.

The flake this pins is the A/B-resolution flake
(``tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::
test_ab_resolution_threshold_met`` + its sibling
``test_ab_resolution_force_resolve``), whose mechanism is PROVEN in the
base-evidence pack (``.agents/tester/RESULTS/2026-11-09-skill-revival-base-evidence/``):

    StaticPool (ONE shared DBAPI connection) + the concurrent
    ``asyncio.gather(asyncio.to_thread(...))`` resolve fan-out in
    ``daemon/services/skill_evolution_service.py`` → one session's
    COMMIT/ROLLBACK interleaves with another's transaction on that one
    connection → ``session.get(Skill, id)`` returns ``None`` →
    ``daemon/repositories/skill/repository.py:333`` logs "Skill not
    found for update" → the loser is never deactivated.

Measured: 15% isolated / 38% in the full-file-set context. Decisive A/B
(same tree, same interpreter, only the thread hop differs): normal
``asyncio.to_thread`` = 11P/1F, inline monkey-patch = 12P/0F.

The FIX is fixture-level (DS-29): a file-backed database with a
non-shared pool, so every concurrent operation gets its OWN connection
— the production fan-out is NOT changed and NOT serialized.

NAMED MUTATION LEGS (each must turn this file RED):

* **M-G1** — revert the fixture to ``StaticPool`` + ``:memory:`` →
  :func:`test_g_pin_concurrent_sessions_do_not_share_one_connection`
  goes RED (the two sessions share one connection again).
* **M-G2** — revert to a shared ``:memory:`` with ``NullPool`` (every
  connection gets its OWN database) →
  :func:`test_g_pin_database_is_shared_across_connections` goes RED.
* **M-G3** — serialize the production fan-out with a lock (the masking
  anti-pattern) → :func:`test_g_pin_production_fanout_is_not_serialized`
  goes RED.
* **M-G4** — delete the ">= 3 consecutive FULL-FILE-SET greens"
  acceptance helper → :func:`test_g_pin_acceptance_helper_requires_three_full_file_set_greens`
  goes RED (import error).
* **M-G5** — narrow the acceptance helper to a single isolated run →
  :func:`test_g_pin_acceptance_helper_requires_three_full_file_set_greens`
  goes RED.
"""

from __future__ import annotations

import inspect
import re

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as SQLModelSession


def test_g_pin_concurrent_sessions_do_not_share_one_connection(engine):
    """M-G1 — two live sessions must hold DIFFERENT DBAPI connections.

    The flake's mechanism is a SHARED connection: with ``StaticPool``
    every session checks out the SAME sqlite3 connection, so one
    session's transaction interleaves with another's. Reverting the
    fixture to ``StaticPool`` + ``:memory:`` makes this pin RED.
    """
    with SQLModelSession(engine) as first:
        with SQLModelSession(engine) as second:
            raw_first = first.connection().connection.dbapi_connection
            raw_second = second.connection().connection.dbapi_connection
            assert raw_first is not raw_second, (
                "concurrent sessions share ONE DBAPI connection — the "
                "StaticPool shape that caused the A/B-resolution flake "
                "(phase1-plan §11) is back"
            )

    assert not isinstance(engine.pool, StaticPool), (
        "the services engine must not use StaticPool (one shared "
        "connection for every thread)"
    )


def test_g_pin_database_is_shared_across_connections(engine):
    """M-G2 — the database must be SHARED across connections.

    ``:memory:`` + a non-shared pool hands every connection its OWN
    database, so a row written through one connection is invisible to
    the next — the data would silently vanish between operations.
    The database must therefore be file-backed (or shared-cache).
    """
    with engine.begin() as writer:
        writer.execute(
            text("CREATE TABLE g_pin_probe (id INTEGER PRIMARY KEY, val TEXT)")
        )
        writer.execute(text("INSERT INTO g_pin_probe (val) VALUES ('shared')"))

    with engine.connect() as reader:
        rows = reader.execute(text("SELECT val FROM g_pin_probe")).fetchall()

    assert rows == [("shared",)], (
        "the database is not shared across connections — a row written "
        "through one connection was invisible to the next"
    )


def test_g_pin_production_fanout_is_not_serialized():
    """M-G3 — the production resolve fan-out must stay CONCURRENT.

    A lock around the fan-out would MASK the race instead of fixing it
    (DS-21/DS-29: "a lock would mask the defect, not fix it"). The
    fan-out must keep fanning out via ``asyncio.to_thread`` and must
    not be wrapped in a lock/semaphore.
    """
    from daemon.services.skill_evolution_service import SkillEvolutionService

    source = inspect.getsource(SkillEvolutionService.check_ab_test_resolution)

    assert "asyncio.gather" in source, (
        "the A/B resolve fan-out no longer uses asyncio.gather"
    )
    assert source.count("asyncio.to_thread") >= 3, (
        "the A/B resolve fan-out must fan out at least three operations "
        "concurrently (deactivate + resolve + update)"
    )
    assert not re.search(r"\b(?:threading|asyncio)\.(?:RLock|Lock|Semaphore)\b", source), (
        "the A/B resolve fan-out is wrapped in a lock/semaphore — that "
        "MASKS the shared-connection race instead of fixing it"
    )
    assert not re.search(r"\b(?:Lock|Semaphore)\(", source), (
        "the A/B resolve fan-out constructs a lock/semaphore — that "
        "MASKS the shared-connection race instead of fixing it"
    )


def test_g_pin_acceptance_helper_requires_three_full_file_set_greens():
    """M-G4 / M-G5 — the acceptance helper must demand the FULL context.

    DS-37: the flake fires at 15% isolated and 38% in the full-file-set
    context, so an ISOLATED green is a 1-in-7 false green. The helper
    must refuse fewer than three runs, refuse an isolated context, and
    refuse a red run. Deleting the helper (M-G4) or narrowing it to a
    single isolated run (M-G5) turns this pin RED.
    """
    from tests.services.test_skill_evolution_service import (
        G_ACCEPTANCE_CONTEXT,
        G_ACCEPTANCE_MIN_GREEN_RUNS,
        GAcceptanceRun,
        assert_full_file_set_acceptance,
    )

    def green() -> GAcceptanceRun:
        return GAcceptanceRun(G_ACCEPTANCE_CONTEXT)

    # Happy path — three consecutive green full-file-set runs.
    assert_full_file_set_acceptance(
        [green() for _ in range(G_ACCEPTANCE_MIN_GREEN_RUNS)]
    )

    # Too few runs — an isolated (or too-short) proof is NOT acceptable.
    with pytest.raises(AssertionError):
        assert_full_file_set_acceptance(
            [green() for _ in range(G_ACCEPTANCE_MIN_GREEN_RUNS - 1)]
        )

    # Isolated context — never an acceptable proof.
    with pytest.raises(AssertionError):
        assert_full_file_set_acceptance(
            [GAcceptanceRun("isolated") for _ in range(G_ACCEPTANCE_MIN_GREEN_RUNS)]
        )

    # An isolated run smuggled into an otherwise-green set — refused.
    with pytest.raises(AssertionError):
        assert_full_file_set_acceptance(
            [green(), green(), GAcceptanceRun("isolated")]
        )

    # A red run — refused.
    with pytest.raises(AssertionError):
        assert_full_file_set_acceptance(
            [
                green(),
                green(),
                GAcceptanceRun(
                    G_ACCEPTANCE_CONTEXT,
                    ("tests/services/test_skill_evolution_service.py::x",),
                ),
            ]
        )
