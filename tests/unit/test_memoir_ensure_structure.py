"""W8.3 structural pins — the memoir ensure rides ONE fail-loud transaction.

Phase 1 AC3 / phase 7 D7.6 / architecture-recommendation.md §12.4 (W8.3,
W8.5). The memoir ``tsv`` + GIN statements are DDL inside the house's
single, all-or-nothing ``_ensure_postgres_columns`` statement list. The
adopted posture (Q6, ``decisions.md`` §4 row 17) is NO lane-owned
containment: a failure in ANY entry aborts the boot and rolls back the
shared transaction. These pins keep that property from silently
regressing:

1. the shared statement block has EXACTLY ONE ``with self._engine.begin()``
   wrapper (one transaction — a second ``begin()`` would carve the list
   into separate commit scopes);
2. the per-statement execute loop has NO per-statement ``try/except``
   (a swallowed failure would let the daemon boot with a half-applied
   ensure — the W8.2-1 RED condition);
3. the memoir ``tsv`` ALTER and its GIN index are present in that SAME
   shared list (not a lane-owned side transaction).

Mutation legs (phase 1 AC3): (i) wrapping ``conn.execute(text(stmt))`` in
``try/except`` flips pin 2 RED; (ii) a second ``begin()`` flips pin 1
RED; (iii) removing the memoir statements flips pin 3 RED. Pins 1/3 are
asserted at source-structure level; pin 2's behavioral half (the boot no
longer aborts) is exercised by ``test/packs/memoir_w8_boot_abort_test.sh``.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_MANAGER_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "daemon" / "manager.py"
)

_BEGIN_RE = re.compile(r"^\s*with\s+self\._engine\.begin\(\)\s+as\s+conn:\s*$")
_LOOP_RE = re.compile(r"^\s*for\s+stmt\s+in\s+statements:\s*$")
_TRY_RE = re.compile(r"^\s*try\s*:")
_EXCEPT_RE = re.compile(r"^\s*except\b")


def _extract_shared_ensure_block() -> dict:
    """Parse ``daemon/manager.py`` and return the shared ensure block.

    Returns the statement list's string constants plus the line span from
    the ``statements = [...]`` assignment through the
    ``conn.execute(text(stmt))`` call.
    """
    src = _MANAGER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_ensure_postgres_columns":
            fn = node
            break
    assert fn is not None, "_ensure_postgres_columns not found in daemon/manager.py"

    assign = None
    for stmt in fn.body:
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "statements"
        ):
            assign = stmt
            break
    assert assign is not None, "the shared `statements = [...]` list is missing"

    lines = src.splitlines()

    loop_idx = None
    for i in range(assign.end_lineno, len(lines)):
        if _LOOP_RE.match(lines[i]):
            loop_idx = i
            break
    assert loop_idx is not None, (
        "`for stmt in statements:` loop missing after the shared list"
    )

    exec_idx = None
    for i in range(loop_idx + 1, len(lines)):
        if "conn.execute(text(stmt))" in lines[i]:
            exec_idx = i
            break
    assert exec_idx is not None, (
        "`conn.execute(text(stmt))` missing inside the shared loop"
    )

    strings = [
        elt.value
        for elt in assign.value.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    ]
    assert strings, "the shared statements list carries no string entries"

    return {
        "strings": strings,
        "block_lines": lines[assign.lineno - 1 : exec_idx + 1],
        "loop_span": lines[loop_idx : exec_idx + 1],
    }


def test_the_shared_ensure_block_uses_exactly_one_transaction() -> None:
    """W8.3-1: exactly ONE ``begin()`` wraps the per-statement loop.

    A second ``begin()`` would split the list into independent commit
    scopes and silently destroy the all-or-nothing blast radius that
    ``decisions.md`` §4 row 17 records and W8 proves.
    """
    block = _extract_shared_ensure_block()
    begin_lines = [
        line for line in block["block_lines"] if _BEGIN_RE.match(line)
    ]
    assert len(begin_lines) == 1, (
        "the shared ensure must run ONE transaction: found "
        f"{len(begin_lines)} `with self._engine.begin() as conn:` wrappers"
    )


def test_the_statement_loop_has_no_per_statement_try_except() -> None:
    """W8.3-2: no per-statement ``try/except`` between loop and execute.

    House fail-loud semantics: a failed ensure must propagate and abort
    the boot. A swallowed per-statement error is the W8.2-1 RED shape
    (the daemon boots with a half-applied ensure).
    """
    block = _extract_shared_ensure_block()
    offenders = [
        line
        for line in block["loop_span"]
        if _TRY_RE.match(line) or _EXCEPT_RE.match(line)
    ]
    assert offenders == [], (
        "per-statement try/except found in the shared ensure loop — the "
        "fail-loud all-or-nothing posture is broken: " + repr(offenders)
    )


def test_the_memoir_tsv_and_gin_statements_ride_the_shared_list() -> None:
    """W8.3-3: memoir ``tsv`` ALTER + GIN index are in the SAME list.

    They must ride the shared ensure (no lane-owned side transaction) and
    the inherited statements must still be there — the collateral-rollback
    property is only meaningful while both share one list.
    """
    block = _extract_shared_ensure_block()
    strings = block["strings"]

    tsv = [s for s in strings if "ADD COLUMN IF NOT EXISTS tsv" in s]
    assert tsv, "the memoir tsv ALTER is missing from the shared ensure list"
    assert any("memoir_episodes" in s for s in tsv), (
        "the tsv ALTER does not target memoir_episodes"
    )

    gin = [s for s in strings if "idx_memoir_episodes_tsv" in s]
    assert gin, "the memoir tsv GIN index is missing from the shared ensure list"
    assert any("USING GIN" in s for s in gin), (
        "the memoir tsv index is not a GIN index"
    )

    inherited = [s for s in strings if "instance_execution_leases" in s]
    assert inherited, (
        "the shared list carries no inherited statement — cannot prove the "
        "memoir DDL shares the transaction with another lane's ensure"
    )
