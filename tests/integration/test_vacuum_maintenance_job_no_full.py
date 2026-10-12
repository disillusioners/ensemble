"""Structural-impossibility pin — the scheduled ``VacuumMaintenanceJob``
NEVER issues ``VACUUM FULL``.

The dedicated scheduled vacuum job at
``daemon/services/vacuum_maintenance.py`` runs plain ``VACUUM (ANALYZE)``
only — its module docstring explicitly warns against ``VACUUM FULL``
(per decisions.md §D-4 + Plan Review R-7). This file pins that
contract at the AST + source-text level so a future commission that
adds a ``vacuum_full`` knob to the scheduled job (or accidentally
threads ``VACUUM FULL`` into the SQL template) trips the pin loudly.

Per decisions.md §D-4 R-7 + Plan Review: the scheduled lane is
STRUCTURALLY LOCKED to plain ``VACUUM (ANALYZE)`` regardless of
payload, config, or env. The guard is the docstring + the SQL
template's literal absence of the ``FULL`` keyword; this pin
asserts both.

Cluster 4 (test-plan.md §2.2) — 3 tests:

1. ``test_vacuum_maintenance_job_sql_template_never_contains_full_keyword``
   — AST pin: parse the file's ``sql = f"VACUUM ..."`` template; the
   literal must NOT contain ``FULL`` (case-insensitive).

2. ``test_vacuum_maintenance_job_does_not_read_vacuum_full_env_vars``
   — AST pin: no ``os.environ.get("VACUUM_FULL_*")`` read site.

3. ``test_vacuum_maintenance_job_docstring_warns_against_full``
   — text pin: the module docstring (top of file) retains the
   "NEVER VACUUM FULL" warning.

NOTE: these pins pin EXISTING correct behavior — they are expected
to pass GREEN immediately (regression pins). They become load-bearing
if a future commission adds a ``vacuum_full`` knob to the scheduled
job.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VACUUM_MAINTENANCE = REPO_ROOT / "daemon" / "services" / "vacuum_maintenance.py"


def _find_sql_templates(tree: ast.Module) -> list[ast.Constant]:
    """Find every SQL template literal in the module — the f-string
    assignment ``sql = f"VACUUM ..."`` (and any sibling templates
    the future might add). The pin asserts the FULL keyword is
    absent from every template; today there is exactly one (the
    ``VACUUM (ANALYZE) <table>`` template at
    ``daemon/services/vacuum_maintenance.py:509``)."""
    templates: list[ast.Constant] = []
    for node in ast.walk(tree):
        # Form A — ``sql = f"VACUUM ..."`` (JoinedStr at the RHS).
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id in {"sql", "vacuum_sql", "vacuum_sql_template"}
                ):
                    value = node.value
                    if isinstance(value, ast.JoinedStr):
                        for sub in ast.walk(value):
                            if isinstance(sub, ast.Constant) and isinstance(
                                sub.value, str
                            ):
                                templates.append(sub.value)
                    elif isinstance(value, ast.Constant) and isinstance(
                        value.value, str
                    ):
                        templates.append(value.value)
        # Form B — direct ``conn.execute(text(sql))`` call where
        # ``sql`` is a string literal inline. Catch any literal
        # containing the substring VACUUM (avoid false positives
        # in unrelated strings).
        if isinstance(node, ast.Call):
            func = node.func
            # conn.execute(text("...")) shape — the inner string
            # literal IS the SQL template.
            if isinstance(func, ast.Attribute) and func.attr == "execute":
                for arg in node.args:
                    if isinstance(arg, ast.Call):
                        inner = arg.func
                        if (
                            isinstance(inner, ast.Name)
                            and inner.id == "text"
                            and arg.args
                            and isinstance(arg.args[0], ast.Constant)
                            and isinstance(arg.args[0].value, str)
                            and "VACUUM" in arg.args[0].value.upper()
                        ):
                            templates.append(arg.args[0].value)
    return templates


def _find_environ_reads(tree: ast.Module) -> list[ast.Call]:
    """Find every ``os.environ`` read call site (get / __getitem__)
    whose key starts with ``VACUUM_FULL_``."""
    sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Form A — os.environ.get("VACUUM_FULL_*")
        if isinstance(func, ast.Attribute) and func.attr == "get":
            recv = func.value
            if (
                isinstance(recv, ast.Attribute)
                and recv.attr == "environ"
                and isinstance(recv.value, ast.Name)
                and recv.value.id == "os"
            ):
                if node.args and isinstance(node.args[0], ast.Constant):
                    key = node.args[0].value
                    if isinstance(key, str) and key.startswith("VACUUM_FULL_"):
                        sites.append(node)
        # Form B — os.environ["VACUUM_FULL_*"] (Subscript form).
        if isinstance(func, ast.Attribute) and func.attr == "__getitem__":
            recv = func.value
            if (
                isinstance(recv, ast.Attribute)
                and recv.attr == "environ"
                and isinstance(recv.value, ast.Name)
                and recv.value.id == "os"
            ):
                sites.append(node)
    return sites


class TestVacuumMaintenanceJobNoFull:
    """Cluster 4 — the scheduled ``VacuumMaintenanceJob`` is structurally
    locked to plain ``VACUUM (ANALYZE)`` — never ``VACUUM FULL``."""

    def test_vacuum_maintenance_job_sql_template_never_contains_full_keyword(self):
        """AST pin — the SQL template(s) passed to ``conn.execute(text(sql))``
        must NOT contain the ``FULL`` keyword (case-insensitive)."""
        tree = ast.parse(VACUUM_MAINTENANCE.read_text(encoding="utf-8"))
        templates = _find_sql_templates(tree)
        assert templates, (
            "no VACUUM SQL template found in "
            "daemon/services/vacuum_maintenance.py — the pin lost its "
            "target (the SQL builder was refactored into a different "
            "shape; update the pin)"
        )
        offenders: list[str] = []
        for tmpl in templates:
            if "full" in tmpl.lower():
                offenders.append(tmpl)
        assert not offenders, (
            "VacuumMaintenanceJob SQL template(s) MUST NOT contain "
            "the FULL keyword (decisions.md §D-4 R-7 — the scheduled "
            "lane is structurally locked to plain VACUUM (ANALYZE)); "
            f"found offender(s): {offenders}"
        )

    def test_vacuum_maintenance_job_does_not_read_vacuum_full_env_vars(self):
        """AST pin — the file must NOT read any ``os.environ`` key
        starting with ``VACUUM_FULL_``. A future commission that
        threads an env gate through the scheduled job trips this
        pin loudly."""
        tree = ast.parse(VACUUM_MAINTENANCE.read_text(encoding="utf-8"))
        offenders = _find_environ_reads(tree)
        assert not offenders, (
            "VacuumMaintenanceJob MUST NOT read os.environ['VACUUM_FULL_*'] "
            "(decisions.md §D-4 R-7 — the scheduled lane is structurally "
            "locked, no env gate for FULL); "
            f"found {len(offenders)} read site(s) at lines "
            f"{[n.lineno for n in offenders]}"
        )

    def test_vacuum_maintenance_job_docstring_warns_against_full(self):
        """Text pin — the module docstring at the top of
        ``daemon/services/vacuum_maintenance.py`` retains the
        "NEVER VACUUM FULL" warning. This is the documentation
        backstop for the AST + structural-impossibility layers —
        if a future refactor moves the SQL builder out of this
        file, the docstring warning is the trip-wire that points
        the reviewer at the new home."""
        src = VACUUM_MAINTENANCE.read_text(encoding="utf-8")
        # The module docstring lives at the top of the file — the
        # first string literal after the optional shebang/encoding
        # declaration. Walk the AST and grab the first Expr whose
        # value is a Constant string.
        tree = ast.parse(src)
        module_docstring: str | None = None
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            module_docstring = tree.body[0].value.value
        assert module_docstring is not None, (
            "no module docstring found at top of "
            "daemon/services/vacuum_maintenance.py — the NEVER "
            "VACUUM FULL warning has been removed; the structural "
            "guard is no longer documented"
        )
        assert "NEVER VACUUM FULL" in module_docstring, (
            "VacuumMaintenanceJob module docstring MUST retain the "
            "'NEVER VACUUM FULL' warning (decisions.md §D-4 R-7) — "
            "the docstring is the documentation backstop for the "
            "structural-impossibility layer"
        )