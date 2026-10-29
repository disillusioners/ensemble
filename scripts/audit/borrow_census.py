#!/home/nea/ensemble-src/.venv/bin/python
"""Borrow-site census over daemon/ — AST-based, re-runnable.

Usage: borrow_census.py <repo-root>

Prints one line per family, the grand total, the docstring N/A note count,
and the per-site detail for family C (the only manual-borrow family).

Family definitions (audit-method.md §3.1 — the canonical scanner, with
the B/F split the audit's own reconciliation table requires):

* **A** — ``with Session(...)`` / ``with WriteGuardSession(Session(...))``
  (the plain-SQLModel/WriteGuard session borrows);
* **B** — ``with SQLModelSession(...)`` (SPLIT OUT of A: audit-method
  §5.2 records the AST scanner's ``A`` as "A+B combined" because the
  published matcher counted ``SQLModelSession`` in A — the split makes
  the executed output reproduce the audit's family table directly);
* **C** — non-``with`` session assignment (``= Session(...)``);
* **D** — ``with engine.connect()`` / ``with engine.begin()``;
* **E** — ``async with pool.acquire()``;
* **F** — raw psycopg borrows (``psycopg.connect`` /
  ``raw_connection()`` / ``getconn()`` / ``putconn()``).

Exit contract (precise):

* **0** — a census completed (per-file PARSE-ERRORs are reported on
  **stderr** and counted under PARSE-ERROR; they do NOT abort the
  census, and the caller diffs the output);
* **2** — the root has no ``daemon/`` directory: a bogus root must
  NEVER yield a complete-looking zero census (fail closed);
* **1** — an unexpected internal error.

(Before this revision the docstring claimed "exit code 0 always" and
"a parse error is reported on stderr" while the code printed
PARSE-ERROR to stdout and could abort with a traceback on a
non-UTF-8 / unreadable file — both claims were false.)
"""
from __future__ import annotations

import argparse
import ast
import os
import sys
from collections import Counter

FAMILIES = (
    ("A", "A  with Session(...) / WriteGuardSession(Session(...))"),
    ("B", "B  with SQLModelSession(...)"),
    ("C", "C  non-with session assignment (= Session(...))"),
    ("D", "D  with engine.connect() / engine.begin()"),
    ("E", "E  async with pool.acquire()"),
    ("F", "F  raw psycopg / raw_connection / getconn / putconn"),
)


def _is_plain_session_call(node):
    """True for Session(...) / WriteGuardSession(...) — NOT SQLModelSession."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("Session", "WriteGuardSession")
    if isinstance(f, ast.Attribute):
        return f.attr in ("Session", "WriteGuardSession")
    return False


def _is_sqlmodel_session_call(node):
    """True for SQLModelSession(...) — family B (split out of A)."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id == "SQLModelSession"
    if isinstance(f, ast.Attribute):
        return f.attr == "SQLModelSession"
    return False


def _is_connect_call(node):
    """True for <engine>.connect() / <engine>.begin() — engine-level borrows."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr in ("connect", "begin")
    if isinstance(f, ast.Name):
        return f.id in ("connect", "begin")
    return False


def _is_acquire_call(node):
    """True for pool.acquire() — asyncpg / psycopg_pool checkouts."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "acquire")


def _is_raw_psycopg_call(node):
    """True for a raw psycopg borrow (family F).

    Covers ``psycopg.connect(...)`` (the attribute chain rooted at the
    ``psycopg`` module) and the pool-raw forms
    ``raw_connection()`` / ``getconn()`` / ``putconn()``.
    """
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Attribute):
        if f.attr in ("raw_connection", "getconn", "putconn"):
            return True
        if f.attr == "connect":
            # psycopg.connect(...) — the receiver chain must be rooted
            # at the psycopg module (``psycopg.connect``,
            # ``psycopg.AsyncConnection.connect``).
            parts = []
            cur = f.value
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return "psycopg" in parts
    return False


def scan(root):
    """Scan ``<root>/daemon`` and return (counts, family_c, docstring_na, parse_errors)."""
    counts = Counter()
    family_c = []
    docstring_na = 0
    parse_errors = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "daemon")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
            except (UnicodeDecodeError, OSError) as exc:
                # A non-UTF-8 or unreadable file must not abort the
                # census — it is a PARSE-ERROR like a syntax error.
                parse_errors.append((os.path.relpath(path, root), str(exc)))
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError as exc:
                parse_errors.append((os.path.relpath(path, root), str(exc)))
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.With):
                    for item in node.items:
                        if _is_sqlmodel_session_call(item.context_expr):
                            counts["B"] += 1
                        elif _is_plain_session_call(item.context_expr):
                            counts["A"] += 1
                        elif _is_connect_call(item.context_expr):
                            counts["D"] += 1
                elif isinstance(node, ast.AsyncWith):
                    for item in node.items:
                        if _is_acquire_call(item.context_expr):
                            counts["E"] += 1
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, (ast.Name, ast.Attribute)):
                            if (_is_plain_session_call(node.value)
                                    or _is_sqlmodel_session_call(node.value)):
                                counts["C"] += 1
                                family_c.append((os.path.relpath(path, root),
                                                 node.lineno,
                                                 ast.unparse(node)[:90]))
            # Family F is counted as a CALL SITE anywhere in the file
            # (not only as a with-item): the audited-clean site is a
            # bare `return psycopg.connect(...)`, so a with-scoped
            # detector would keep the family unmeasurable (the
            # false-clean T-H2 names).
            for node in ast.walk(tree):
                if _is_raw_psycopg_call(node):
                    counts["F"] += 1
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc and ("Session(" in doc or "sessionmaker" in doc):
                        docstring_na += 1
    return counts, family_c, docstring_na, parse_errors


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="AST borrow-site census over <root>/daemon (audit-method.md §3.1)",
    )
    parser.add_argument("root", help="the repo root (its daemon/ is scanned)")
    args = parser.parse_args(argv)
    root = args.root

    # T-M5: a bogus root must never produce a complete-looking zero
    # census — fail closed.
    daemon_dir = os.path.join(root, "daemon")
    if not os.path.isdir(daemon_dir):
        print(
            f"FATAL: no daemon/ directory under {root!r} — refusing to "
            "report a zero census (a typo'd root is a false-clean)",
            file=sys.stderr,
        )
        return 2

    counts, family_c, doc_na, errs = scan(root)
    for key, label in FAMILIES:
        print(f"{label}: {counts.get(key, 0)}")
    print(
        "TOTAL borrow sites (A+B+C+D+E+F as summed above): "
        f"{sum(counts.values())}"
    )
    print(f"docstring N/A notes: {doc_na}")
    # T-M4: PARSE-ERRORs go to STDERR (the docstring said so; the code
    # printed them to stdout) — the count line above stays clean for
    # the caller's diff.
    for rel, line in errs:
        print(f"PARSE-ERROR {rel}: {line}", file=sys.stderr)
    print("--- family C detail (the only manual-borrow family) ---")
    for rel, line, text in family_c:
        print(f"  {rel}:{line}: {text}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — the exit contract says 1
        print(f"FATAL: census failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
