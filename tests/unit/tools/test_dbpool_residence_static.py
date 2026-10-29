"""F7-a-static pin — the residence twin (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §5.2 and §4.1(b) (Δ3); decisions
D-24 (static-twin discipline) / D-48 / DR-35.

Default-collected static twin of the PG pins F7-a/F7-b
(``tests/integration/test_db_pool_idle_in_transaction_guard.py``) so
deleting or un-marking the PG guard leaves the DEFAULT run RED.

What this pin asserts:

1. The residence budget constant EXISTS in ``daemon/constants.py``
   (``DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S``).
2. The guard's assertion REFERENCES it (the guard source imports the
   constant and compares against it — never a literal, DR-20).
3. The guard's metric is ``xact_age_s`` — the residence-comparison
   helper computes from the census's ``xact_age_s`` column and its
   body contains NO ``state_change``-derived metric (D-48: the frozen
   metric can never fire on the leak).
4. **Δ3 reachability** — every call-site → budget-constant row in the
   F7 pack header's declaration table resolves to an existing
   constant in ``daemon/constants.py``.

Plantable mutations: delete the budget constant from
``daemon/constants.py``; revert the guard's metric to
``state_change − xact_start``; or add a declaration-table row whose
constant does not exist ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_residence_static.py --tb=short -q
"""
from __future__ import annotations

import re
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUARD_PATH = (
    _REPO_ROOT / "tests" / "integration" / "test_db_pool_idle_in_transaction_guard.py"
)

#: Δ3(iv) — the declaration table's opening marker in the F7 pack
#: header (ONE table, at the pinned location).
_DECL_TABLE_MARKER = "CALL-SITE → BUDGET-CONSTANT DECLARATION TABLE"
#: A declaration-table row: ``| <site> | ``CONSTANT_NAME`` | ... |``.
#: The constant name is captured from the SECOND column.
_DECL_ROW = re.compile(
    r"^\|\s*[^|]+\|\s*``([A-Z][A-Z0-9_]+)``\s*\|", re.M
)


def _daemon_constants() -> str:
    return (_REPO_ROOT / "daemon" / "constants.py").read_text()


def _guard_source() -> str:
    return _GUARD_PATH.read_text()


def _guard_function_source(name: str) -> str:
    """The source of one top-level function in the guard pack, with
    its docstring STRIPPED (the docstring may legitimately NAME the
    banned frozen metric to document why it is banned — only the
    executable body is the pinned subject)."""
    import ast

    source = _guard_source()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(source, node) or ""
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                lines = seg.splitlines(keepends=True)
                doc_end = node.body[0].end_lineno - node.lineno
                seg = "".join(lines[doc_end:])
            return seg.strip()
    return ""


class TestF7aStaticResidenceTwin:
    """F7-a-static — the guard's residence invariant, statically."""

    def test_the_residence_budget_constant_exists(self):
        constants = _daemon_constants()
        assert re.search(
            r"^DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S\s*:\s*float\s*=\s*"
            r"[0-9.]+\s*$",
            constants,
            re.M,
        ), (
            "F7-a-static: DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S is "
            "missing from daemon/constants.py (or is not a plain "
            "typed float literal)"
        )
        assert re.search(
            r"^MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S\s*[:=]",
            constants,
            re.M,
        ), (
            "F7-a-static: MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S is "
            "missing from daemon/constants.py — the Δ3 declaration "
            "table references it"
        )

    def test_the_guard_references_the_constant_not_a_literal(self):
        source = _guard_source()
        assert (
            "from daemon.constants import" in source
            and "DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S" in source
        ), (
            "F7-a-static: the guard does not import/reference "
            "DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S — the residence "
            "assertion must compare against the CONSTANT (DR-20)"
        )
        assert source.count("DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S") >= 3, (
            "F7-a-static: the guard references the budget constant "
            "fewer than 3 times — the comparison, the healthy-cycle "
            "check and the deliberate-hold leg must all use it"
        )

    def test_the_guard_metric_is_xact_age_s_never_the_frozen_form(self):
        helper = _guard_function_source("_residence_violations")
        assert helper, (
            "F7-a-static: the guard's _residence_violations helper "
            "was not found — the residence comparison helper is the "
            "pinned subject"
        )
        assert "xact_age_s" in helper, (
            "F7-a-static: the residence helper does not use "
            "xact_age_s — the D-48 metric is now() − xact_start"
        )
        assert "state_change" not in helper, (
            "F7-a-static: the residence helper uses a state_change-"
            "derived metric — FROZEN at the last statement and "
            "vacuous against the leak (D-48); revert to xact_age_s"
        )
        assert "2.0" not in helper and "2.0s" not in helper, (
            "F7-a-static: a 2.0 literal inside the residence helper — "
            "the WITHDRAWN placeholder must not be resurrected"
        )

    def test_declaration_table_rows_resolve_to_real_constants(self):
        # Δ3(iv) reachability: every call-site → budget-constant row
        # in the F7 pack header resolves to an existing constant in
        # daemon/constants.py.
        source = _guard_source()
        marker_at = source.find(_DECL_TABLE_MARKER)
        assert marker_at != -1, (
            "F7-a-static: the Δ3 declaration table marker was not "
            "found in the F7 pack header (Δ3(iv) pins its location)"
        )
        table_region = source[marker_at: marker_at + 4000]
        rows = _DECL_ROW.findall(table_region)
        assert rows, (
            "F7-a-static: the Δ3 declaration table carries no "
            "constant-bearing rows — the reachability check is vacuous"
        )
        constants = _daemon_constants()
        unresolved = [
            name
            for name in rows
            if not re.search(rf"^{name}\s*[:=]", constants, re.M)
        ]
        assert not unresolved, (
            "F7-a-static: declaration-table constants that do NOT "
            f"exist in daemon/constants.py: {unresolved} — a row whose "
            "constant does not exist is a broken reachability pin"
        )
