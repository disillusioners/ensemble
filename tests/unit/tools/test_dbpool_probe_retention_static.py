"""F7-b-static pin — the probe-retention twin (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §5.2 (F7-b-static row); test-strategy
§3.7. Default-collected static twin of the PG pin F4-b
(``tests/integration/test_dbpool_probe_bound_pg.py``) — deleting or
un-marking that PG pack leaves the DEFAULT run RED through this twin.

What this pin asserts:

1. ``make_queue_probe``'s source contains ``SET LOCAL
   statement_timeout`` (the F4-B probe-scoped statement bound).
2. The derived value equals ``2 * QUEUE_PROBE_TIMEOUT_S`` — asserted
   STRUCTURALLY by calling the one derivation site
   (``_probe_statement_timeout_ms``) with the imported constant
   (DR-20 — never a literal).
3. The SAVER probe does NOT contain ``SET LOCAL`` (F4.6 — the P2
   psycopg pool is ``autocommit=True``; ``SET LOCAL`` there would be
   a no-op with a PG warning).
4. The ``daemon/services/readiness.py`` probe-budget docstring no
   longer carries the false premise ("the driver's own
   connect/execute timeout releases it") and STATES the measured
   semantics (backend ``active``, statement cancelled ``57014``, the
   backend row persists).

Plantable mutations: remove ``SET LOCAL statement_timeout`` from
``make_queue_probe``; add it to the saver probe; restore the false
docstring premise ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_probe_retention_static.py --tb=short -q
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4

_REPO_ROOT = Path(__file__).resolve().parents[3]
_READINESS_PATH = _REPO_ROOT / "daemon" / "services" / "readiness.py"

#: The FALSE premise F4-e/F7-b-static banishes (F4.5/DR-25).
_FALSE_PREMISE = "connect/execute timeout releases"


def _function_source(path: Path, name: str) -> str:
    """The source of one module-level function (incl. closures).

    T-M15: RAISES on miss — the previous silent ``""`` return disarmed
    the ABSENCE assertions (a renamed/moved target made
    ``"..." not in source`` go GREEN vacuously)."""
    src = path.read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            segment = ast.get_source_segment(src, node)
            assert segment is not None, (
                f"AST failed to yield a source segment for {name!r} "
                f"in {path} — the reader is stale"
            )
            return segment
    raise AssertionError(f"function {name} not found in {path}")


class TestF7bStaticProbeRetentionTwin:
    """F7-b-static — the F4-B probe bound's static facts."""

    def test_queue_probe_carries_the_statement_bound(self):
        source = _function_source(_READINESS_PATH, "make_queue_probe")
        assert source, "F7-b-static: make_queue_probe was not found"
        assert "SET LOCAL statement_timeout" in source, (
            "F7-b-static: make_queue_probe no longer issues SET LOCAL "
            "statement_timeout — the F4-B statement bound is gone "
            "(under a held lock the statement would block unbounded)"
        )
        db_source = _function_source(_READINESS_PATH, "make_db_probe")
        assert db_source, "F7-b-static: make_db_probe was not found"
        assert "SET LOCAL statement_timeout" in db_source, (
            "F7-b-static: make_db_probe no longer issues SET LOCAL "
            "statement_timeout"
        )

    def test_derived_value_is_two_times_the_client_budget(self):
        from daemon.services.readiness import (
            QUEUE_PROBE_TIMEOUT_S,
            DB_PROBE_TIMEOUT_S,
            _probe_statement_timeout_ms,
        )

        assert _probe_statement_timeout_ms(QUEUE_PROBE_TIMEOUT_S) == int(
            2 * QUEUE_PROBE_TIMEOUT_S * 1000
        ), (
            "F7-b-static: the queue probe's server bound is not "
            "derived as 2 × QUEUE_PROBE_TIMEOUT_S (the ONE derivation "
            "site — D-05/F4.3)"
        )
        assert _probe_statement_timeout_ms(DB_PROBE_TIMEOUT_S) == int(
            2 * DB_PROBE_TIMEOUT_S * 1000
        ), (
            "F7-b-static: the db probe's server bound is not derived "
            "as 2 × DB_PROBE_TIMEOUT_S"
        )

    def test_the_saver_probe_is_excluded(self):
        source = _function_source(
            _READINESS_PATH, "make_checkpoint_saver_probe"
        )
        assert source, (
            "F7-b-static: make_checkpoint_saver_probe was not found"
        )
        assert "SET LOCAL" not in source, (
            "F7-b-static: the SAVER probe carries SET LOCAL — F4.6 "
            "EXCLUDES it (the P2 psycopg pool is autocommit=True; the "
            "clause would be a no-op with a PG warning)"
        )

    def test_the_probe_docstring_states_the_measured_semantics(self):
        src = _READINESS_PATH.read_text()
        # The premise may be QUOTED only to be FALSIFIED: every
        # occurrence must sit inside a falsifying sentence ("…
        # premise is FALSE at base"). A bare re-assertion REDs.
        for match in re.finditer(re.escape(_FALSE_PREMISE), src):
            tail = src[match.end(): match.end() + 80]
            assert re.search(r"premise\s+is\s+FALSE", tail), (
                "F7-b-static: the false premise ('the driver's own "
                "connect/execute timeout releases it') appears in "
                "readiness.py WITHOUT the falsification — F4.5/DR-25 "
                "requires the MEASURED semantics, not the premise"
            )
        # The measured semantics (F4-B): backend active, the statement
        # cancelled with 57014/QueryCanceled, the backend row
        # PERSISTS (the row-gone signature belongs to the
        # engine-level idle_in_transaction_session_timeout, which
        # this lane does not enable).
        budget_region = src.split("DB_PROBE_TIMEOUT_S", 1)[0]
        assert "57014" in budget_region or "QueryCanceled" in budget_region, (
            "F7-b-static: the corrected docstring must state the "
            "measured cancellation semantics (57014 / QueryCanceled)"
        )
        assert "PERSIST" in budget_region.upper(), (
            "F7-b-static: the corrected docstring must state the "
            "backend row is EXPECTED TO PERSIST after the statement "
            "is cancelled"
        )
