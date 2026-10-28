"""F4-a / F4-e pins — the readiness probe bounds (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F4.1–F4.6 and §5.2.

What these pins assert:

* **F4-a** — BOTH halves of the (rewritten, demoted) F4:
  **(i) F4-A** — each probe body performs the **acquisition admission
  pre-check** before ``engine.connect()`` (the synchronous
  ``TimeoutError``-on-saturation path), and the **saver probe is
  excluded** from both mechanisms; the **pinned capacity constant
  EQUALS the engine's configured capacity** at construction
  (``pool_size + max_overflow`` as passed to ``create_engine``) — a
  knob drift REDs (D-57); **(ii) F4-B** — ``make_db_probe`` issues
  ``SET LOCAL statement_timeout = 2 × DB_PROBE_TIMEOUT_S`` as the FIRST
  statement, dialect-guarded (PG only), and ``make_queue_probe`` issues
  ``2 × QUEUE_PROBE_TIMEOUT_S`` — the value is **derived**, never a
  literal.
* **F4-e** — the docstring at the ``DB_PROBE_TIMEOUT_S`` block no
  longer carries the false premise (*"the driver's own connect/execute
  timeout releases it"*) and states the **MEASURED** semantics (backend
  ``active``, statement cancelled ``57014``, row persists; under
  starvation the probe holds no connection).

Plantable mutations:

* remove the admission pre-check from ``make_db_probe`` or
  ``make_queue_probe`` ⇒ RED;
* remove the ``SET LOCAL statement_timeout`` from ``make_db_probe`` ⇒
  RED;
* change the derived value so it is no longer ``2 ×`` the constant ⇒
  RED;
* add ``SET LOCAL`` to the **saver** probe ⇒ RED (the exclusion
  assertion);
* change ``pool_size`` / ``max_overflow`` in ``daemon/repositories/factory.py``
  without mirroring the pinned constant ⇒ RED (the capacity-equality
  assertion);
* restore the false docstring premise ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_probe_bound.py --tb=short -q
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from daemon.services import readiness as readiness_mod
from daemon.services.readiness import (
    DB_PROBE_TIMEOUT_S,
    MANAGER_POOL_CONFIGURED_CAPACITY,
    QUEUE_PROBE_TIMEOUT_S,
    make_db_probe,
    make_queue_probe,
    _probe_statement_timeout_ms,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 13

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READINESS_PATH = _REPO_ROOT / "daemon" / "services" / "readiness.py"
_FACTORY_PATH = _REPO_ROOT / "daemon" / "repositories" / "factory.py"


# ── helpers ──────────────────────────────────────────────────────────────


def _function_source(path: Path, name: str) -> str:
    """Return the source text of the named top-level function."""
    source = path.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function {name} not found in {path}")


def _probe_body_source(name: str) -> str:
    """Return the source of the probe closure (the inner ``_probe``)."""
    source = _READINESS_PATH.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.FunctionDef) and inner.name == "_probe":
                    return ast.get_source_segment(source, inner) or ""
    raise AssertionError(f"probe closure _probe not found in {name}")


class _FakePool:
    """A pool stub exposing the public occupancy accessors."""

    def __init__(self, checkedout: int, size: int = 5):
        self._checkedout = checkedout
        self._size = size

    def checkedout(self) -> int:
        return self._checkedout

    def size(self) -> int:
        return self._size


class _FakeEngine:
    """A minimal engine stub — the pre-check must run BEFORE connect()."""

    def __init__(self, checkedout: int):
        self.pool = _FakePool(checkedout)
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        raise AssertionError("engine.connect() must not be reached at capacity")


# ── F4-a (i): the admission pre-check ───────────────────────────────────


class TestF4aAdmissionPreCheck:
    """F4-A — the probe refuses entry into a saturated pool."""

    def test_db_probe_body_calls_the_precheck_before_connect(self):
        body = _probe_body_source("make_db_probe")
        precheck_at = body.find("_refuse_pool_admission_at_capacity")
        connect_at = body.find("engine.connect()")
        assert precheck_at != -1, (
            "F4-a: make_db_probe has no acquisition admission pre-check"
        )
        assert connect_at != -1, "F4-a: make_db_probe no longer connects"
        assert precheck_at < connect_at, (
            "F4-a: the admission pre-check must run BEFORE engine.connect()"
        )

    def test_queue_probe_body_calls_the_precheck_before_connect(self):
        body = _probe_body_source("make_queue_probe")
        precheck_at = body.find("_refuse_pool_admission_at_capacity")
        connect_at = body.find("engine.connect()")
        assert precheck_at != -1, (
            "F4-a: make_queue_probe has no acquisition admission pre-check"
        )
        assert connect_at != -1, "F4-a: make_queue_probe no longer connects"
        assert precheck_at < connect_at, (
            "F4-a: the admission pre-check must run BEFORE engine.connect()"
        )

    def test_saver_probe_is_excluded_from_both_mechanisms(self):
        saver = _function_source(_READINESS_PATH, "make_checkpoint_saver_probe")
        assert "_refuse_pool_admission_at_capacity" not in saver, (
            "F4-a: the saver probe must NOT carry the admission pre-check "
            "(F4.6 — it does not use engine.connect(); it reads through "
            "the saver path)"
        )
        assert "SET LOCAL" not in saver, (
            "F4-a: the saver probe must NOT carry SET LOCAL (F4.6 — the P2 "
            "psycopg pool is autocommit=True, so SET LOCAL is a no-op with "
            "a PG warning)"
        )

    def test_at_capacity_the_probe_refuses_synchronously(self):
        engine = _FakeEngine(checkedout=MANAGER_POOL_CONFIGURED_CAPACITY)
        probe = make_db_probe(engine)  # type: ignore[arg-type]
        with pytest.raises(TimeoutError):
            probe()
        assert engine.connect_calls == 0, (
            "F4-a: the probe entered the blocking checkout at capacity — "
            "the refusal must happen BEFORE engine.connect()"
        )

    def test_below_capacity_the_precheck_does_not_refuse(self):
        engine = _FakeEngine(checkedout=MANAGER_POOL_CONFIGURED_CAPACITY - 1)
        # The pre-check must NOT raise on its own (the probe itself will
        # fail later on the fake engine — that is out of scope here).
        readiness_mod._refuse_pool_admission_at_capacity(engine)  # type: ignore[arg-type]


class TestF4aCapacityEquality:
    """F4-a — the pinned capacity constant EQUALS the engine's configured
    capacity at construction (D-57: a knob drift REDs)."""

    def test_pinned_constant_equals_configured_pool_capacity(self):
        source = _FACTORY_PATH.read_text()
        tree = ast.parse(source)
        kwargs: dict[str, Any] = {}
        # The MANAGER engine's construction site — create_postgres_engine
        # (the SQLite + config-driven engines are other call sites and
        # are NOT this lane's subject).
        for node in tree.body:
            if not (
                isinstance(node, ast.FunctionDef)
                and node.name == "create_postgres_engine"
            ):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "create_engine"
                ):
                    for kw in inner.keywords:
                        if kw.arg in ("pool_size", "max_overflow"):
                            kwargs[kw.arg] = ast.literal_eval(kw.value)
        assert "pool_size" in kwargs and "max_overflow" in kwargs, (
            "F4-a: could not read pool_size/max_overflow from the "
            "manager engine construction site in factory.py "
            "(create_postgres_engine)"
        )
        configured = kwargs["pool_size"] + kwargs["max_overflow"]
        assert MANAGER_POOL_CONFIGURED_CAPACITY == configured, (
            "F4-a: the pinned capacity constant "
            f"({MANAGER_POOL_CONFIGURED_CAPACITY}) does not equal the "
            f"engine's configured capacity ({configured}) — the probe's "
            "admission threshold has drifted from the engine"
        )


# ── F4-a (ii): the probe-scoped statement bound ──────────────────────────


class TestF4aStatementBound:
    """F4-B — the probe-scoped ``SET LOCAL statement_timeout``."""

    def test_db_probe_issues_set_local_as_first_statement(self):
        body = _probe_body_source("make_db_probe")
        assert "SET LOCAL statement_timeout" in body, (
            "F4-a: make_db_probe no longer issues the probe-scoped "
            "statement bound"
        )
        set_local_at = body.find("SET LOCAL statement_timeout")
        select_at = body.find("SELECT 1")
        assert set_local_at < select_at, (
            "F4-a: SET LOCAL must be the FIRST statement (it scopes the "
            "autobegun transaction)"
        )
        assert "DB_PROBE_TIMEOUT_S" in body, (
            "F4-a: the bound value must be DERIVED from DB_PROBE_TIMEOUT_S"
        )

    def test_queue_probe_issues_set_local_as_first_statement(self):
        body = _probe_body_source("make_queue_probe")
        assert "SET LOCAL statement_timeout" in body, (
            "F4-a: make_queue_probe no longer issues the probe-scoped "
            "statement bound"
        )
        assert "QUEUE_PROBE_TIMEOUT_S" in body, (
            "F4-a: the bound value must be DERIVED from "
            "QUEUE_PROBE_TIMEOUT_S"
        )

    def test_derived_values_are_twice_the_client_budget(self):
        assert _probe_statement_timeout_ms(DB_PROBE_TIMEOUT_S) == int(
            2 * DB_PROBE_TIMEOUT_S * 1000
        )
        assert _probe_statement_timeout_ms(QUEUE_PROBE_TIMEOUT_S) == int(
            2 * QUEUE_PROBE_TIMEOUT_S * 1000
        )

    def test_statement_bound_is_pg_only(self):
        for name in ("make_db_probe", "make_queue_probe"):
            body = _probe_body_source(name)
            assert "postgres" in body, (
                f"F4-a: {name} must dialect-guard SET LOCAL (PG only — "
                "SQLite raises OperationalError on SET LOCAL)"
            )

    def test_explicit_end_of_transaction_before_return(self):
        for name in ("make_db_probe", "make_queue_probe"):
            body = _probe_body_source(name)
            assert "conn.rollback()" in body, (
                f"F4-a: {name} must issue the explicit end-of-transaction "
                "(D-07) before the connection returns to the pool"
            )


# ── F4-e: the docstring correction ───────────────────────────────────────


class TestF4eDocstringCorrection:
    """F4-e — the false premise is gone; the measured semantics are stated."""

    def test_false_premise_is_removed(self):
        source = _READINESS_PATH.read_text()
        assert "the driver's own connect/execute timeout releases it" not in source, (
            "F4-e: the false premise ('the driver's own connect/execute "
            "timeout releases it') is still in the module — the manager "
            "engine sets NO engine-wide execute timeout"
        )

    def test_measured_semantics_are_stated(self):
        source = _READINESS_PATH.read_text()
        assert "57014" in source, (
            "F4-e: the docstring must state the MEASURED semantics — the "
            "statement is cancelled with 57014 / QueryCanceled"
        )
        assert "PERSIST" in source or "persist" in source, (
            "F4-e: the docstring must state that the backend row is "
            "EXPECTED TO PERSIST (the row-gone signature belongs to the "
            "engine-level idle-in-txn backstop, which this lane does NOT "
            "install)"
        )
        assert "WAITER" in source or "waiter" in source, (
            "F4-e: the docstring must state the starvation shape — under "
            "pool starvation the probe holds NO connection (it is a pool "
            "waiter)"
        )
