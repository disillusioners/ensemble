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

from daemon.constants import MANAGER_POOL_SIZE_DEFAULT
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
EXPECTED_COLLECTED = 16

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READINESS_PATH = _REPO_ROOT / "daemon" / "services" / "readiness.py"
_FACTORY_PATH = _REPO_ROOT / "daemon" / "repositories" / "factory.py"
_CONSTANTS_PATH = _REPO_ROOT / "daemon" / "constants.py"

#: The manager engine's CONFIGURED overflow at the construction site
#: (``daemon/repositories/factory.py``). A literal there — the knob
#: matrix pins it — so the pin can read it directly.
EXPECTED_MAX_OVERFLOW = 10


# ── helpers ──────────────────────────────────────────────────────────────


def _function_source(path: Path, name: str) -> str:
    """Return the source text of the named top-level function."""
    source = path.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None, "AST yielded no segment — reader stale"
            return segment
    raise AssertionError(f"function {name} not found in {path}")


def _manager_engine_construction_kwargs() -> dict[str, ast.AST]:
    """The manager engine's ``create_engine`` kwargs (as AST nodes).

    The MANAGER engine's construction site — ``create_postgres_engine``
    (the SQLite + config-driven engines are other call sites and are
    NOT this lane's subject). The values are returned UNRESOLVED: the
    config-driven ``pool_size`` is a Name, so the caller resolves the
    chain itself.
    """
    tree = ast.parse(_FACTORY_PATH.read_text())
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
                return {
                    kw.arg: kw.value for kw in inner.keywords if kw.arg is not None
                }
    raise AssertionError(
        "F4-a: the create_engine call site was not found in "
        "create_postgres_engine (daemon/repositories/factory.py)"
    )


def _manager_pool_size_default_tier() -> int:
    """The construction's ``pool_size`` DEFAULT TIER, resolved.

    ``create_postgres_engine(..., manager_pool_size=MANAGER_POOL_SIZE_
    DEFAULT)`` → the constant's value in ``daemon/constants.py`` (15).
    """
    tree = ast.parse(_FACTORY_PATH.read_text())
    for node in tree.body:
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == "create_postgres_engine"
        ):
            continue
        positional = list(node.args.posonlyargs) + list(node.args.args)
        defaults = list(node.args.defaults)
        offset = len(positional) - len(defaults)
        for index, arg in enumerate(positional):
            if arg.arg != "manager_pool_size":
                continue
            default = defaults[index - offset]
            assert (
                isinstance(default, ast.Name)
                and default.id == "MANAGER_POOL_SIZE_DEFAULT"
            ), (
                "F4-a: the factory's manager_pool_size default is no "
                f"longer MANAGER_POOL_SIZE_DEFAULT (observed: "
                f"{ast.dump(default)})"
            )
            return _constants_default_pool_size()
    raise AssertionError(
        "F4-a: create_postgres_engine has no manager_pool_size parameter"
    )


def _constants_default_pool_size() -> int:
    """``MANAGER_POOL_SIZE_DEFAULT``'s value, read from the constants
    module source (the canonical home of the default tier)."""
    tree = ast.parse(_CONSTANTS_PATH.read_text())
    for node in tree.body:
        # Both plain (``X = 15``) and annotated (``X: int = 15``)
        # assignments carry the constant.
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value_node = node.value
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
            value_node = node.value
        else:
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "MANAGER_POOL_SIZE_DEFAULT"
            for target in targets
        ):
            continue
        assert value_node is not None, (
            "F4-a: MANAGER_POOL_SIZE_DEFAULT has no value"
        )
        value = ast.literal_eval(value_node)
        assert isinstance(value, int), (
            "F4-a: MANAGER_POOL_SIZE_DEFAULT is not an integer literal"
        )
        return value
    raise AssertionError(
        "F4-a: MANAGER_POOL_SIZE_DEFAULT was not found in "
        "daemon/constants.py"
    )


def _probe_body_source(name: str) -> str:
    """Return the source of the probe closure (the inner ``_probe``)."""
    source = _READINESS_PATH.read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            for inner in ast.walk(node):
                if isinstance(inner, ast.FunctionDef) and inner.name == "_probe":
                    segment = ast.get_source_segment(source, inner)
                    assert segment is not None, "AST yielded no segment — reader stale"
                    return segment
    raise AssertionError(f"probe closure _probe not found in {name}")


class _FakePool:
    """A QueuePool-SHAPED stub exposing the occupancy accessors.

    Models the REAL surface the admission pre-check reads:
    ``checkedout()`` (current occupancy), ``size()`` (the CONFIGURED
    pool size) and ``_max_overflow`` (the configured overflow — the
    private attribute the real ``QueuePool`` carries). A stub WITHOUT
    the latter two models an UNRESOLVABLE capacity ⇒ the pre-check
    must NOT refuse (fail-open on refusal) — that is a distinct,
    separately pinned shape (``_UnresolvablePool`` below).
    """

    def __init__(
        self,
        checkedout: int,
        size: int = MANAGER_POOL_SIZE_DEFAULT,
        max_overflow: int = EXPECTED_MAX_OVERFLOW,
    ):
        self._checkedout = checkedout
        self._size = size
        self._max_overflow = max_overflow

    def checkedout(self) -> int:
        return self._checkedout

    def size(self) -> int:
        return self._size


class _UnresolvablePool:
    """A pool whose EFFECTIVE capacity cannot be resolved.

    No ``size()`` accessor (the StaticPool/NullPool shape) — the
    pre-check must SKIP it, never refuse on a guess.
    """

    def __init__(self, checkedout: int):
        self._checkedout = checkedout

    def checkedout(self) -> int:
        return self._checkedout


class _FakeEngine:
    """A minimal engine stub — the pre-check must run BEFORE connect()."""

    def __init__(self, checkedout: int, pool=None):
        self.pool = pool if pool is not None else _FakePool(checkedout)
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

    def test_a_raised_pool_size_keeps_the_overflow_slots_servable(self):
        """The 15-vs-25 safety argument, as a behavior.

        ``limits.manager_pool_size`` is operator-raisable (bounds
        [15, 30]). At a RAISED tier the engine's effective capacity is
        ``size() + _max_overflow`` — so a pool at 25 checked-out
        connections with a 22-connection tier still has 10 overflow
        slots SERVABLE. A STATIC threshold of 15 would refuse here —
        a self-inflicted outage. The pre-check must therefore compare
        against the ENGINE's effective capacity, never a fixed number.
        """
        pool = _FakePool(checkedout=25, size=22, max_overflow=10)
        engine = _FakeEngine(checkedout=25, pool=pool)
        # 25 < 22 + 10 = 32 ⇒ servable ⇒ NO refusal.
        readiness_mod._refuse_pool_admission_at_capacity(engine)  # type: ignore[arg-type]
        # ...and at the engine's real capacity it DOES refuse.
        full = _FakePool(checkedout=32, size=22, max_overflow=10)
        with pytest.raises(TimeoutError):
            readiness_mod._refuse_pool_admission_at_capacity(  # type: ignore[arg-type]
                _FakeEngine(checkedout=32, pool=full)
            )

    def test_an_unresolvable_capacity_never_refuses(self):
        """Fail-open ON REFUSAL: an unresolvable capacity is not proof
        of exhaustion. A pool without ``size()`` (StaticPool /
        NullPool / a test double) must be SKIPPED, never refused —
        refusing on a guess would strand a probe that still had
        servable slots."""
        engine = _FakeEngine(
            checkedout=999, pool=_UnresolvablePool(checkedout=999)
        )
        readiness_mod._refuse_pool_admission_at_capacity(engine)  # type: ignore[arg-type]


class TestF4aCapacityEquality:
    """F4-a — the pinned capacity constant EQUALS the engine's
    DEFAULT-TIER configured capacity at construction (D-57: a knob
    drift REDs).

    RE-DERIVED (2026-10-29 merge-semantics fix): the manager engine's
    ``pool_size`` is CONFIG-DRIVEN since the manager-pool-size knob
    hotfix (``pool_size=manager_pool_size`` — a Name, not a literal),
    so ``ast.literal_eval`` can no longer read the construction site.
    The pin now follows the chain:

    ``pool_size=manager_pool_size`` → the factory's default parameter
    ``manager_pool_size=MANAGER_POOL_SIZE_DEFAULT`` → the constant's
    value in ``daemon/constants.py`` (the default tier, 15)

    and reads ``max_overflow`` (10) at the construction site; the
    DEFAULT-TIER total (15 + 10 = 25) must equal the declared
    ``MANAGER_POOL_CONFIGURED_CAPACITY``. The declaration is the
    DEFAULT-TIER declaration — the EFFECTIVE refusal threshold is
    DERIVED from the pool per call (the knob is operator-raisable),
    which the sibling structural pin below enforces.
    """

    def test_pinned_constant_equals_configured_pool_capacity(self):
        kwargs = _manager_engine_construction_kwargs()
        assert "pool_size" in kwargs and "max_overflow" in kwargs, (
            "F4-a: could not read pool_size/max_overflow from the "
            "manager engine construction site in factory.py "
            "(create_postgres_engine)"
        )
        pool_size_node = kwargs["pool_size"]
        assert (
            isinstance(pool_size_node, ast.Name)
            and pool_size_node.id == "manager_pool_size"
        ), (
            "F4-a: the construction site's pool_size is no longer the "
            "ladder-resolved `manager_pool_size` — the knob wiring was "
            f"severed (observed: {ast.dump(pool_size_node)})"
        )
        default_tier = _manager_pool_size_default_tier()
        overflow = ast.literal_eval(kwargs["max_overflow"])
        configured = default_tier + overflow
        assert MANAGER_POOL_CONFIGURED_CAPACITY == configured, (
            "F4-a: the pinned capacity constant "
            f"({MANAGER_POOL_CONFIGURED_CAPACITY}) does not equal the "
            f"engine's DEFAULT-TIER configured capacity ({configured} = "
            f"{default_tier} pool_size + {overflow} max_overflow) — the "
            "declaration has drifted from the construction site"
        )

    def test_the_refusal_threshold_is_derived_from_the_pool(self):
        """The refusal threshold must be DERIVED from the engine's own
        pool — never a static literal comparison.

        ``limits.manager_pool_size`` is operator-raisable and the
        engine's overflow slots are servable, so a hard-coded threshold
        would refuse admission while slots are free — a self-inflicted
        outage. This pin makes re-hard-coding it RED.
        """
        source = _function_source(
            _READINESS_PATH, "_refuse_pool_admission_at_capacity"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for comparator in node.comparators:
                assert not (
                    isinstance(comparator, ast.Name)
                    and comparator.id == "MANAGER_POOL_CONFIGURED_CAPACITY"
                ), (
                    "F4-a: the admission pre-check compares the occupancy "
                    "against the STATIC MANAGER_POOL_CONFIGURED_CAPACITY — "
                    "the threshold must be DERIVED from the pool's own "
                    "effective capacity (the manager pool size is "
                    "config-driven and operator-raisable)"
                )
        assert "_manager_pool_effective_capacity" in source, (
            "F4-a: the admission pre-check no longer derives the "
            "effective capacity from the pool"
        )
        helper = _function_source(
            _READINESS_PATH, "_manager_pool_effective_capacity"
        )
        assert "size" in helper and "_max_overflow" in helper, (
            "F4-a: the capacity derivation must read the pool's "
            "configured size AND its configured overflow"
        )
        # The derivation must be fail-open on an unresolvable capacity.
        assert "return None" in helper, (
            "F4-a: the capacity derivation must return None for an "
            "unresolvable capacity (the pre-check then does NOT refuse)"
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
