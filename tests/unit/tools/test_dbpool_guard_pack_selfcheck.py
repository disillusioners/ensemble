"""F7-c / F7-d pins — the guard-pack selfcheck (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §5.2 (F7-c/F7-d rows);
test-strategy.md §3.7; decisions D-25/A-9.

Default-collected selfcheck over the F7 PG packs:

* **F7-c (self-pin)** — the guard's assertion set is re-read from
  SOURCE. Deleting a guard assertion **and** decrementing the guard
  pack's own ``EXPECTED_COLLECTED`` constant leaves THIS pack RED:
  the expected counts live HERE (not in the guard), and the key
  assertion markers (the ``xact_age_s`` residence comparison, the
  occupancy-return-to-baseline assertion) are re-checked against the
  guard's source text.
* **F7-d (placement twin)** — the guard exists at exactly the
  ``tests/integration/`` placement and carries
  ``pytestmark = pytest.mark.integration`` (tests/integration does
  NOT auto-mark — the pack itself must carry the marker). Delete /
  un-mark / move the guard file ⇒ RED here, in the DEFAULT run.

Plantable mutations: delete the guard's residence assertion and
decrement its count constant (F7-c still RED); delete / un-mark /
move the guard file out of the integration placement (F7-d RED).

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_guard_pack_selfcheck.py --tb=short -q
"""
from __future__ import annotations

import re
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUARD_REL = "tests/integration/test_db_pool_idle_in_transaction_guard.py"
_CHURN_REL = "tests/integration/test_dbpool_churn_census.py"
_GUARD_PATH = _REPO_ROOT / _GUARD_REL
_CHURN_PATH = _REPO_ROOT / _CHURN_REL

#: F7-c — the expected test counts of the PG packs, declared HERE so
#: decrementing the guard pack's own EXPECTED_COLLECTED cannot hide a
#: deleted assertion (A-9: the self-pin re-reads the source).
GUARD_EXPECTED_TESTS = 7
CHURN_EXPECTED_TESTS = 3

#: F7-c — the load-bearing assertion markers the guard source must
#: carry (deleting the assertion deletes the marker ⇒ RED).
_GUARD_MARKERS = (
    # the residence comparison (D-48 metric vs the Δ3 constant)
    "DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S",
    "xact_age_s",
    # the occupancy invariant (the pin that would have caught the
    # incident)
    "checkedout()",
)


def _count_test_functions(path: Path) -> int:
    return len(re.findall(r"^\s*def test_", path.read_text(), re.M))


class TestF7cSelfPin:
    """F7-c — the guard's assertion set, re-read from source."""

    def test_guard_carries_the_expected_assertion_set(self):
        assert _GUARD_PATH.exists(), (
            "F7-c: the guard pack is missing — the F7 PG pins are gone"
        )
        source = _GUARD_PATH.read_text()
        declared = re.search(
            r"^EXPECTED_COLLECTED\s*[:=]\s*(\d+)", source, re.M
        )
        assert declared is not None, (
            "F7-c: the guard pack no longer declares EXPECTED_COLLECTED"
        )
        assert int(declared.group(1)) == GUARD_EXPECTED_TESTS, (
            f"F7-c: the guard declares {declared.group(1)} tests — "
            f"this selfcheck re-derives {GUARD_EXPECTED_TESTS} from "
            "the source; deleting an assertion AND decrementing the "
            "guard's own constant cannot hide the deletion"
        )
        assert _count_test_functions(_GUARD_PATH) == GUARD_EXPECTED_TESTS, (
            "F7-c: the guard's test-function count drifted from "
            f"{GUARD_EXPECTED_TESTS} — an assertion was deleted or "
            "added without reconciling the selfcheck"
        )
        missing = [m for m in _GUARD_MARKERS if m not in source]
        assert not missing, (
            f"F7-c: the guard source lost load-bearing assertion "
            f"markers: {missing}"
        )

    def test_churn_pack_carries_the_expected_assertion_set(self):
        assert _CHURN_PATH.exists(), (
            "F7-c: the churn census pack is missing"
        )
        source = _CHURN_PATH.read_text()
        declared = re.search(
            r"^EXPECTED_COLLECTED\s*[:=]\s*(\d+)", source, re.M
        )
        assert declared is not None, (
            "F7-c: the churn pack no longer declares EXPECTED_COLLECTED"
        )
        assert int(declared.group(1)) == CHURN_EXPECTED_TESTS, (
            f"F7-c: the churn pack declares {declared.group(1)} tests "
            f"— this selfcheck re-derives {CHURN_EXPECTED_TESTS}"
        )
        assert (
            _count_test_functions(_CHURN_PATH) == CHURN_EXPECTED_TESTS
        ), "F7-c: the churn pack's test-function count drifted"
        assert "checkedout()" in source, (
            "F7-c: the churn pack lost the occupancy assertion marker"
        )


class TestF7dPlacementTwin:
    """F7-d — delete / un-mark / move the guard ⇒ RED here."""

    def test_guard_is_marked_integration_at_the_pinned_placement(self):
        assert _GUARD_REL.startswith("tests/integration/"), (
            "F7-d: the guard must live under tests/integration/ "
            "(D-23)"
        )
        assert _GUARD_PATH.exists(), (
            f"F7-d: the guard file {_GUARD_REL} is missing — deleted "
            "or moved out of the integration placement"
        )
        source = _GUARD_PATH.read_text()
        assert "pytest.mark.integration" in source, (
            "F7-d: the guard lost its pytestmark = "
            "pytest.mark.integration — tests/integration does NOT "
            "auto-mark; an un-marked guard is silently deselected by "
            "the Class-3 runner form"
        )
        assert re.search(
            r"^pytestmark\s*=\s*\[pytest\.mark\.integration\]", source, re.M
        ), (
            "F7-d: the module-level pytestmark binding was weakened — "
            "it must be the module-level list form so EVERY test in "
            "the pack carries the marker"
        )
        # The churn pack rides the same discipline (its own placement
        # + marker — the F7-e PG leg's reachability).
        assert _CHURN_PATH.exists(), (
            "F7-d: the churn census pack is missing from "
            "tests/integration/"
        )
        assert "pytest.mark.integration" in _CHURN_PATH.read_text(), (
            "F7-d: the churn census pack lost its integration marker"
        )
