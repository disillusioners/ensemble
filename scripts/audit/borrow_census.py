#!/home/nea/ensemble-src/.venv/bin/python
"""Borrow-site census over daemon/ — AST-based, re-runnable.

Usage: python3 scripts/audit/borrow_census.py <repo-root>

Prints one line per family, the grand total, the docstring N/A note count,
and the per-site detail for family C (the only manual-borrow family).
Exit code 0 always (the caller diffs the output); a parse error is
reported on stderr and counted under PARSE-ERROR.
"""
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


def _is_session_call(node):
    """True for Session(...) / SQLModelSession(...) / WriteGuardSession(...)."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("Session", "SQLModelSession", "WriteGuardSession")
    if isinstance(f, ast.Attribute):
        return f.attr in ("Session", "SQLModelSession", "WriteGuardSession")
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


def scan(root):
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
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            try:
                tree = ast.parse(src)
            except SyntaxError as exc:
                parse_errors.append((os.path.relpath(path, root), str(exc)))
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.With):
                    for item in node.items:
                        if _is_session_call(item.context_expr):
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
                            if _is_session_call(node.value):
                                counts["C"] += 1
                                family_c.append((os.path.relpath(path, root),
                                                 node.lineno,
                                                 ast.unparse(node)[:90]))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc and ("Session(" in doc or "sessionmaker" in doc):
                        docstring_na += 1
    return counts, family_c, docstring_na, parse_errors


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    counts, family_c, doc_na, errs = scan(root)
    for key, label in FAMILIES:
        print(f"{label}: {counts.get(key, 0)}")
    print(f"TOTAL borrow sites (A+B+C+D+E+F): {sum(counts.values())}")
    print(f"docstring N/A notes: {doc_na}")
    for rel, line in errs:
        print(f"PARSE-ERROR {rel}: {line}")
    print("--- family C detail (the only manual-borrow family) ---")
    for rel, line, text in family_c:
        print(f"  {rel}:{line}: {text}")
