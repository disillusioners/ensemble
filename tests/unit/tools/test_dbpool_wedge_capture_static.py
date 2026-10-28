"""F5-c / F5-d pins — the wedge-capture table name (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F5.1–F5.3 and §5.2 (F5 raised into
Phase 1 by DR-34 — the wrong table name is the ROOT of the incident's
same-connection aborted-transaction cascade).

What these pins assert:

* **F5-c** — the module reads the REAL table (``task``): no SQL site
  references a ``tasks`` table any more.
* **F5-d** — the **store census** query (over ``pg_class`` /
  ``pg_namespace``) is untouched and still structurally intact — the
  wrong-table fix must not have broken it.

Plantable mutations:

* re-introduce ``FROM tasks`` / ``tasks t`` ⇒ RED (F5-c);
* break the census SQL (e.g. change the ``pg_class`` /
  ``pg_namespace`` join) ⇒ RED (F5-d).

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_wedge_capture_static.py --tb=short -q
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 6

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / "daemon" / "services" / "wedge_capture" / "db_capture.py"

#: SQL-level references to a ``tasks`` table (the wrong name). The
#: singular ``task`` is the real table; a plural reference in a SQL
#: statement (FROM / JOIN / INTO / UPDATE) is the defect.
_TASKS_SQL_PATTERNS = (
    re.compile(r"\bFROM\s+tasks\b", re.IGNORECASE),
    re.compile(r"\bJOIN\s+tasks\b", re.IGNORECASE),
    re.compile(r"\bINTO\s+tasks\b", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+tasks\b", re.IGNORECASE),
    re.compile(r"\btasks\s+t\b", re.IGNORECASE),
)


def _sql_string_literals() -> list[str]:
    """Every string literal in the module that looks like SQL."""
    tree = ast.parse(_MODULE_PATH.read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if any(
                kw in text.upper() for kw in (" SELECT ", " FROM ", " JOIN ", "WHERE")
            ):
                out.append(text)
    return out


class TestF5cNoTasksTable:
    """F5-c — the capture reads ``task``, never ``tasks``."""

    def test_no_sql_literal_references_a_tasks_table(self):
        for literal in _sql_string_literals():
            for pattern in _TASKS_SQL_PATTERNS:
                assert not pattern.search(literal), (
                    "F5-c: a SQL literal still references the non-existent "
                    f"'tasks' table: {literal!r} — the real table is 'task'"
                )

    def test_the_two_read_sites_target_the_task_table(self):
        source = _MODULE_PATH.read_text()
        assert "FROM task t" in source, (
            "F5-c: the executing-turns read must target 'task t'"
        )
        assert "FROM task\n" in source, (
            "F5-c: the resume-history read must target 'task'"
        )

    def test_module_docstring_names_the_task_table(self):
        source = _MODULE_PATH.read_text()
        assert "``task`` rows with ``status='running'``" in source, (
            "F5-c: the module docstring must name the REAL table ('task')"
        )


class TestF5dStoreCensusUntouched:
    """F5-d — the store-census query still works (it is a SEPARATE query
    from the two fixed ones)."""

    def test_census_query_keeps_its_pg_catalog_join(self):
        source = _MODULE_PATH.read_text()
        assert "FROM pg_class c" in source, (
            "F5-d: the store census no longer reads pg_class"
        )
        assert "JOIN pg_namespace n ON n.oid = c.relnamespace" in source, (
            "F5-d: the store census lost its pg_namespace join"
        )
        assert "n.nspname = current_schema()" in source, (
            "F5-d: the store census lost its schema filter"
        )
        assert "pg_total_relation_size(c.oid)" in source, (
            "F5-d: the store census lost its relation-size projection"
        )

    def test_census_query_is_in_its_own_function(self):
        tree = ast.parse(_MODULE_PATH.read_text())
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        assert "_read_store_census" in functions, (
            "F5-d: the store-census reader function is gone"
        )
        source = ast.get_source_segment(
            _MODULE_PATH.read_text(), functions["_read_store_census"]
        ) or ""
        assert "pg_class" in source and "pg_namespace" in source, (
            "F5-d: the store-census reader no longer queries the catalog"
        )

    def test_census_and_fixed_queries_are_distinct_statements(self):
        """The census is a SEPARATE query from the two fixed ones — a
        regression that merged them would break the census."""
        source = _MODULE_PATH.read_text()
        assert source.count("FROM pg_class c") == 1
        assert source.count("FROM task t") == 1
