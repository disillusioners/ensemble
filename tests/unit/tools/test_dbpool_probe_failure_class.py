"""F4-c pin — the readiness probe's failure classification (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F4.7 and §5.2.

What this pin asserts:

* the probe's returned ``(verdict, failure_class)`` pair stays
  ``probe_timeout`` — never ``hard_component_red``;
* **F4-A's synchronous ``TimeoutError`` lands on the SAME class**
  (``FAILURE_CLASS_PROBE_TIMEOUT`` via the existing
  ``except TimeoutError`` branch in ``_guarded``) ⇒ grace-eligibility
  is preserved and the verdict contract does not change.

Plantable mutation: change the probe's returned pair, or reclassify a
timeout as ``hard_component_red`` — **and** the F4-A refusal path must
land on ``FAILURE_CLASS_PROBE_TIMEOUT`` (if the refusal were routed to
the hard-red class, the grace window would stop covering it and the
readiness verdict contract would change).

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_probe_failure_class.py --tb=short -q
"""
from __future__ import annotations

import asyncio

import pytest

from daemon.services.readiness import (
    FAILURE_CLASS_HARD_COMPONENT_RED,
    FAILURE_CLASS_PROBE_TIMEOUT,
    GRACE_ELIGIBLE_FAILURE_CLASSES,
    MANAGER_POOL_CONFIGURED_CAPACITY,
    make_db_probe,
    refresh_readiness_composite,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3


class _SaturatedPool:
    def __init__(self) -> None:
        self._n = MANAGER_POOL_CONFIGURED_CAPACITY

    def checkedout(self) -> int:
        return self._n

    def size(self) -> int:
        return 5


class _SaturatedEngine:
    """An engine whose pool is at capacity — the F4-A refusal path."""

    def __init__(self) -> None:
        self.pool = _SaturatedPool()
        self.connect_calls = 0

    def connect(self):  # pragma: no cover — must never be reached
        self.connect_calls += 1
        raise AssertionError("connect() must not be reached at capacity")


class _RaisingProbe:
    """A probe that raises the exception under test."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self):
        raise self._exc


def _composite_for_db_probe(probe) -> object:
    """Run one readiness refresh with the given DB probe."""
    return asyncio.run(
        refresh_readiness_composite(
            db_probe=probe,
            queue_probe=None,
            services_ok=True,
            queue_freshness_threshold_seconds=60.0,
        )
    )


class TestF4cTimeoutStaysProbeTimeout:
    """F4-c — a timeout is ``probe_timeout`` (grace-eligible), never
    hard-component-red."""

    def test_f4a_refusal_lands_on_probe_timeout(self):
        engine = _SaturatedEngine()
        probe = make_db_probe(engine)  # type: ignore[arg-type]
        composite = _composite_for_db_probe(probe)
        classes = getattr(composite, "component_classes", {})
        assert classes.get("database") == FAILURE_CLASS_PROBE_TIMEOUT, (
            "F4-c: the F4-A admission refusal must land on "
            f"{FAILURE_CLASS_PROBE_TIMEOUT!r} — got "
            f"{classes.get('database')!r}; a hard-component-red class "
            "would drop the grace-window eligibility"
        )
        assert classes.get("database") != FAILURE_CLASS_HARD_COMPONENT_RED
        assert FAILURE_CLASS_PROBE_TIMEOUT in GRACE_ELIGIBLE_FAILURE_CLASSES, (
            "F4-c: probe_timeout must remain grace-eligible"
        )

    def test_client_side_timeout_keeps_the_same_class(self):
        """The client-side budget timeout (asyncio.wait_for) must land on
        the SAME class as the F4-A refusal — the two shapes must not
        diverge."""
        composite = _composite_for_db_probe(
            _RaisingProbe(TimeoutError("client budget elapsed"))
        )
        classes = getattr(composite, "component_classes", {})
        assert classes.get("database") == FAILURE_CLASS_PROBE_TIMEOUT

    def test_a_non_timeout_exception_is_still_hard_component_red(self):
        """The classification contract is UNCHANGED for exceptions: a
        non-timeout exception is row 2 (never grace-eligible)."""
        composite = _composite_for_db_probe(
            _RaisingProbe(RuntimeError("boom"))
        )
        classes = getattr(composite, "component_classes", {})
        assert classes.get("database") == FAILURE_CLASS_HARD_COMPONENT_RED
