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


# ══════════════════════════════════════════════════════════════════════
# FIX WAVE 1 — m1 (fixture hygiene) and m5 (the experiment-equivalence
# pin that §18 row 35 asked for and that only existed as PACK EVIDENCE)
# ══════════════════════════════════════════════════════════════════════
#
# NAMED MUTATION LEGS (each must turn this file RED):
#
# * **M-m1** — put the STALE artefacts back into the fixture (the
#   ``NullPool`` import, the "the pool is ``NullPool``" comment, the
#   30 s connect timeout) → the fixture-hygiene pin goes RED.
# * **M-m5** — revert the fixture to the pre-G shared-connection shape
#   (``StaticPool`` + ``:memory:``) → the experiment-equivalence pin
#   goes RED (the two arms no longer produce the SAME outcome — the
#   base-evidence decisive experiment: 11P/1F normal vs 12P/0F inline).

import asyncio  # noqa: E402  (added by the fix wave 1 — see M-m5 below)
import json  # noqa: E402
from unittest.mock import patch  # noqa: E402

from tests.services.test_skill_evolution_service import (  # noqa: E402
    _make_skill,
    evolution_service,
    fake_embedding_service,
    fake_metrics_service,
    llm_config,
)


def test_g_pin_services_fixture_ships_the_documented_shape(engine):
    """M-m1 — the fixture must match its own documentation.

    Three stale artefacts were carried by the shipped fixture: an
    unused ``NullPool`` import, a comment that still said "the pool is
    ``NullPool``" while the code used ``QueuePool``, and a 30 s connect
    timeout that the 5 s ``busy_timeout`` PRAGMA silently overrode (a
    DEAD value — the busy timeout is the one that matters for the
    flake, because it is what a blocked writer waits for).

    Named mutations: restore the ``NullPool`` import, restore the
    stale comment, or restore the 30 s timeout → this pin goes RED.
    """
    import tests.services.conftest as services_conftest
    from sqlalchemy.pool import NullPool, QueuePool

    assert isinstance(engine.pool, QueuePool), (
        "the services fixture must use QueuePool (one connection per "
        "session) — the shared-connection shape is the flake's cause"
    )
    assert not isinstance(engine.pool, NullPool)

    # The two timeout values must be ONE value: the PRAGMA and the
    # driver connect timeout.
    assert (
        services_conftest.SQLITE_BUSY_TIMEOUT_SECONDS
        == services_conftest.SQLITE_BUSY_TIMEOUT_MS / 1000.0
    )
    assert services_conftest.SQLITE_BUSY_TIMEOUT_SECONDS == 5.0, (
        "the busy timeout is the value that matters for the flake — it "
        "must be the single 5 s value (the old 30 s connect timeout was "
        "dead: the PRAGMA overrode it)"
    )

    source = inspect.getsource(services_conftest)
    assert "SQLITE_BUSY_TIMEOUT_SECONDS" in source, (
        "the connect timeout must be derived from the busy-timeout "
        "constant — the two must never disagree"
    )
    assert "30.0" not in source, (
        "the dead 30 s connect timeout is back"
    )

    doc = services_conftest.__doc__ or ""
    assert "the pool is ``NullPool``" not in doc, (
        "the module docstring must describe the SHIPPED pool "
        "(QueuePool), not the pre-fix NullPool claim"
    )


# ── M-m5 — the decisive experiment, AS A TEST ──────────────────────────

#: How many times each arm runs. The normal arm is repeated because
#: the pre-G flake is PROBABILISTIC (15% isolated / 38% in the
#: full-file-set context) — a single run can pass by luck. The pin
#: compares the OUTCOME SETS of the two arms, so one diverging normal
#: run is enough to turn the pin RED under the pre-G fixture, while
#: the shipped fixture is deterministic (every run resolves).
_EQUIV_RUNS = 4


async def _inline_to_thread(func, /, *args, **kwargs):
    """The NO-THREAD-HOP twin of ``asyncio.to_thread``."""
    return func(*args, **kwargs)


def _canonical(outcome: dict) -> str:
    """A comparable form of one scenario outcome."""
    return json.dumps(outcome, sort_keys=True, default=str)


async def _run_ab_scenario(
    skill_repo,
    ab_test_repo,
    project_id,
    evolution_service,
    fake_metrics_service,
    *,
    tag: str,
    inline: bool,
) -> dict:
    """Run ONE A/B-resolution scenario and return its OUTCOME.

    The scenario is the threshold-met path — the one that fans three
    WRITE transactions out concurrently via
    ``asyncio.gather(asyncio.to_thread(...))``
    (``daemon/services/skill_evolution_service.py`` — the Path-2
    ``threshold_met`` branch). That fan-out is the flake's blast
    radius.

    Args:
        inline: When True, ``asyncio.to_thread`` is monkey-patched to
            run the callable INLINE (no thread hop) — the base
            evidence pack's decisive experiment's second arm.
    """
    old = _make_skill(skill_repo, project_id, f"old-{tag}")
    new = _make_skill(skill_repo, project_id, f"new-{tag}")
    group = f"g-equiv-{tag}"
    ab_test_repo.create_ab_test(group, old.id, new.id)
    for _ in range(10):
        ab_test_repo.increment_comparison(group)

    fake_metrics_service.get_ab_comparison_stats.return_value = {
        "skill_id_a": old.id,
        "skill_id_b": new.id,
        "completion_rate_a": 0.6,
        "completion_rate_b": 0.85,
        "difference": 0.25,
        "comparisons": 12,
        "extension_count": 0,
        "ready_to_resolve": True,
        "needs_more_data": False,
    }

    if inline:
        with patch.object(asyncio, "to_thread", _inline_to_thread):
            result = await evolution_service.check_ab_test_resolution(group)
    else:
        result = await evolution_service.check_ab_test_resolution(group)

    loser = skill_repo.get(old.id)
    winner = skill_repo.get(new.id)
    test = ab_test_repo.get_by_group(group)
    return {
        "resolved": result.get("resolved"),
        "reason": result.get("reason"),
        # The ids are SYMBOLIC: every scenario creates fresh skills, so
        # the raw uuid4s would differ between the arms and the
        # comparison would be vacuous. The ROLES are what matters.
        "winner_is_new": result.get("winner_id") == new.id,
        "loser_is_old": result.get("loser_id") == old.id,
        "winner_id_present": bool(result.get("winner_id")),
        "loser_id_present": bool(result.get("loser_id")),
        "loser_active": bool(loser.is_active) if loser else None,
        "winner_active": bool(winner.is_active) if winner else None,
        "winner_ab_test_group": (
            winner.ab_test_group if winner else None
        ),
        "test_winner_is_new": (
            test.winner_skill_id == new.id if test else None
        ),
        "test_resolved": bool(test.resolved_at) if test else None,
    }


async def test_g_pin_experiment_equivalence_normal_vs_inline(
    skill_repo,
    ab_test_repo,
    project_id,
    evolution_service,
    fake_metrics_service,
):
    """M-m5 — the decisive experiment, AS A TEST (not as pack evidence).

    The base evidence pack proved the flake's mechanism with an A/B:
    normal ``asyncio.to_thread`` execution = 11P/1F, the inline
    (no-thread-hop) monkey-patch = 12P/0F. §18 row 35 asks for that
    experiment as a PIN — a pack artifact is evidence, not a gate: it
    cannot turn RED when the fixture regresses.

    This pin runs BOTH arms and asserts they produce the SAME outcome
    SET. Under the shipped fixture (file-backed DB + ``QueuePool``) the
    two arms are identical — the thread hop is then irrelevant, which
    is exactly the point: the production fan-out stays CONCURRENT and
    UNSERIALIZED (no lock) without the shared-connection defect.

    Named mutation: revert the fixture to the pre-G shared-connection
    shape (``StaticPool`` + ``:memory:``) → the normal arm's writes
    interleave on ONE connection, the loser's deactivation is lost,
    and the two arms no longer agree → this pin goes RED.
    """
    normal = []
    inline = []
    for index in range(_EQUIV_RUNS):
        normal.append(
            await _run_ab_scenario(
                skill_repo, ab_test_repo, project_id, evolution_service,
                fake_metrics_service, tag=f"normal-{index}", inline=False,
            )
        )
    for index in range(_EQUIV_RUNS):
        inline.append(
            await _run_ab_scenario(
                skill_repo, ab_test_repo, project_id, evolution_service,
                fake_metrics_service, tag=f"inline-{index}", inline=True,
            )
        )

    normal_set = {_canonical(outcome) for outcome in normal}
    inline_set = {_canonical(outcome) for outcome in inline}

    assert normal_set == inline_set, (
        "the two arms of the decisive experiment DISAGREE — the normal "
        "asyncio.to_thread execution and the inline (no-thread-hop) "
        "execution must produce the SAME outcome.\n"
        f"normal: {sorted(normal_set)}\ninline: {sorted(inline_set)}"
    )
    assert len(normal_set) == 1, (
        "the normal arm produced MORE THAN ONE outcome across "
        f"{_EQUIV_RUNS} runs: {sorted(normal_set)} — the fixture is "
        "not deterministic (the shared-connection flake is back)"
    )
    outcome = json.loads(next(iter(normal_set)))
    assert outcome["resolved"] is True, (
        "the A/B resolution must actually RESOLVE under the shipped "
        f"fixture — got {outcome!r}"
    )
    assert outcome["loser_active"] is False, (
        "the loser must be deactivated — the fan-out's write was lost "
        f"(got {outcome!r})"
    )
    assert outcome["test_resolved"] is True
