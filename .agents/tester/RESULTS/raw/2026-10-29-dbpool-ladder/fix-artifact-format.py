#!/usr/bin/env python
"""Sanctioned artifact format fix (dispatcher ruling, Option A):
strip markdown code spans from the count cells of expected-collected.md
(rows 1-35 leading numeric portion only; trailing prose byte-identical)
+ the one sanctioned footnote clause fix. ZERO digit changes; assert 35
cells + 1 clause; assert every changed cell's digit run is unchanged."""
import re
import pathlib
import sys

P = pathlib.Path("/home/nea/ensemble-src-wt-tdbp-gate/.agents/shared/planning/db-pool-idle-txn/expected-collected.md")
src = P.read_text()
orig_lines = src.split("\n")
out = []
changed_cells = 0
digits_log = []  # (rownum, before_cell, after_cell)

for ln in orig_lines:
    m = re.match(r"^\| (\d+) \|", ln)
    if m:
        rownum = int(m.group(1))
        if 1 <= rownum <= 35:
            parts = ln.split("|")
            if len(parts) >= 6:
                cell = parts[5]
                cm = re.match(r"^(\s*)`([0-9]+)`(.*)$", cell, re.DOTALL)
                if cm:
                    newcell = cm.group(1) + cm.group(2) + cm.group(3)
                    assert re.sub(r"[^0-9]", "", cell) == re.sub(r"[^0-9]", "", newcell), f"digit change in row {rownum}!"
                    assert cell.replace("`", "", 1).replace("`", "", 1) if False else True
                    parts[5] = newcell
                    newln = "|".join(parts)
                    digits_log.append((rownum, cell.strip(), newcell.strip()))
                    ln = newln
                    changed_cells += 1
    out.append(ln)

new = "\n".join(out)

OLD_CLAUSE = "it still reads `TBD (R-1)`,"
NEW_CLAUSE = "it read `TBD (R-1)` until R-1 landed 2026-10-29 (now filled),"
n_clause = new.count(OLD_CLAUSE)
assert n_clause == 1, f"footnote clause found {n_clause} times (expected 1)"
new = new.replace(OLD_CLAUSE, NEW_CLAUSE)

assert changed_cells == 35, f"expected 35 cells stripped, got {changed_cells}"
assert len(new.split("\n")) == len(orig_lines), "line count changed!"

# Whole-file backtick-only guarantee: every removed char must be a backtick.
removed = []
added = []
import difflib
for d in difflib.unified_diff(orig_lines, new.split("\n"), lineterm="", n=0):
    if d.startswith("-") and not d.startswith("---"):
        removed.append(d[1:])
    elif d.startswith("+") and not d.startswith("+++"):
        added.append(d[1:])
assert len(removed) == len(added) == 36, f"diff lines: -{len(removed)} +{len(added)} (expected 36/36)"
for r, a in zip(removed, added):
    # either backtick-only removal from a table cell, or the footnote clause
    if r.startswith("|"):
        assert a == r.replace("`", "") or re.sub(r"`(\d+)`", r"\1", r) == a, f"non-backtick table change:\n  -{r}\n  +{a}"
    else:
        # the footnote LINE: old must contain the clause, new must be the exact clause substitution
        assert OLD_CLAUSE in r, f"non-table removed line does not contain the sanctioned clause:\n  -{r}"
        assert a == r.replace(OLD_CLAUSE, NEW_CLAUSE), f"footnote line is not the pure clause substitution:\n  -{r}\n  +{a}"

P.write_text(new)
print(f"OK: {changed_cells} cells stripped; footnote clause replaced once; 36 diff lines; zero digit changes")
for rownum, before, after in digits_log:
    print(f"  row {rownum:>2}: {before!r} -> {after!r}")
