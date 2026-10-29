"""G-A pin — the census families match the AST baseline (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §5.2 (G-A row); the scanner and the
diff gate are ``audit-method.md`` §3.1/§7.2 (the materialised
scanner lives at ``scripts/audit/borrow_census.py`` — the §3.1
materialisation note assigns that path to the phase-3 implementer).

What this pin asserts: the borrow-site census over ``daemon/``
reproduces the **executed instrument's** family counts exactly —
**A=438**, **B=175**, **C=4** at the four named manual-borrow sites,
**D=148**, **E=14**, **F=1** — and the total **780**.

**Reconciliation with the audit artifact** (T-H2; the instrument, this
pin and ``audit-findings.md`` are one triangle):

* The artifact's **A=613** is the AST scanner's **A+B COMBINED**
  (audit-method §5.2 says so verbatim: *"the AST scanner's A also
  counts SQLModelSession"*). The fixed instrument SPLITS the two
  families, so **438 + 175 = 613** — the artifact's A is reproduced
  exactly, now as two families.
* The artifact's **F=0** was a **SCANNER GAP, not a tree fact**: the
  published §3.1 source had no F branch at all (F was structurally
  unmeasurable — the false-clean the tidier found). The implemented F
  detector (psycopg.connect / raw_connection / getconn / putconn)
  finds **F=1**: the ONE-SHOT ``psycopg.connect`` in
  ``daemon/services/wedge_capture/db_capture.py:195`` — which the
  contract already records as CLEAN (F5.3: *"a one-shot
  psycopg.connect with close() in finally ⇒ no pool, so it cannot
  leak"*). The pin names that site explicitly.
* Consequently the total is **780**, not the artifact's 779: the
  difference is exactly the one F site the old instrument could not
  see. Nothing else moved.

**Base vs HEAD (audit-method §7.2 diff gate):** the SAME fixed
instrument run at base ``19fc11c8b`` yields the IDENTICAL numbers
(A=438 / B=175 / C=4 / D=148 / E=14 / F=1 / total 780) ⇒ the lane
added **ZERO** borrow sites — the delta is empty, so no §7.3
classification table is required. Any family count delta from here on
is a FAILURE until classified per §7.3 — the diff IS the failing step.

The **§3.2 pattern cross-check** (grep) keeps its own vocabulary —
its F pattern (``raw_connection(|getconn(|putconn(``) does not name
plain ``psycopg.connect``, so the pattern F stays 0 while the AST F
is 1. That divergence is the same PATTERN-DEPENDENT class the
audit records for D and E (§5.2); the AST is canonical.

The retired **≈689** aggregate stays NON-CANONICAL (DR-6 — never
cited as the census figure).

Plantable mutation: any new borrow site in ``daemon/`` (a new manual
session assignment, a raw pool checkout, a ``sessionmaker``) ⇒ the
family counts drift ⇒ RED; deleting the materialised scanner ⇒ the
import fails ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_census_families.py --tb=short -q
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCANNER_PATH = _REPO_ROOT / "scripts" / "audit" / "borrow_census.py"

#: The EXECUTED instrument's family counts (T-H2: B and F are measured,
#: not structurally zero). Measured with the fixed instrument at base
#: ``19fc11c8b`` AND at HEAD — the two are IDENTICAL (the lane added
#: zero borrow sites). The audit artifact's A=613 is this instrument's
#: A+B COMBINED (438 + 175) and its F=0 was a SCANNER GAP (no F
#: detector); see the module docstring.
AST_BASELINE = {"A": 438, "B": 175, "C": 4, "D": 148, "E": 14, "F": 1}
AST_BASELINE_TOTAL = 780

#: The ONE family-F site — the audited-clean one-shot psycopg.connect
#: (contract F5.3: it closes in a finally, holds no pool, cannot leak).
#: Named explicitly so a SECOND raw-psycopg borrow is a loud failure.
FAMILY_F_SITES = ("daemon/services/wedge_capture/db_capture.py:195",)

#: The §7.2 / §3.2 pattern cross-check constants.
PATTERN_BASELINE = {"A": 439, "B": 175, "C": 4, "F": 0}

#: The four named manual-borrow sites (audit-method §6.3) — the
#: scanner-independent primary assertion.
FAMILY_C_SITES = (
    "daemon/manager.py:2782",
    "daemon/repositories/auth_bootstrap/repository.py:93",
    "daemon/repositories/auth_bootstrap/repository.py:161",
    "daemon/services/child_reports.py:2594",
)

_GNU_GREP_TRAP_NOTE = (
    "no alternation in the patterns (the GNU grep ERE trap — under "
    "-E a backslash-pipe is a literal pipe)"
)


def _load_scanner():
    """Import the materialised scanner (audit-method §3.1 assigns
    ``scripts/audit/borrow_census.py``; a missing scanner REDs)."""
    assert _SCANNER_PATH.exists(), (
        "G-A: the materialised scanner scripts/audit/borrow_census.py "
        "is missing (audit-method.md §3.1 materialisation note)"
    )
    spec = importlib.util.spec_from_file_location(
        "borrow_census", _SCANNER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _run_scan():
    module = _load_scanner()
    counts, family_c, _doc_na, parse_errors = module.scan(str(_REPO_ROOT))
    return counts, family_c, parse_errors


def _pattern_count(pattern: str) -> int:
    """The §3.2 pattern cross-check (grep -rnE over daemon/)."""
    result = subprocess.run(
        ["grep", "-rnE", pattern, "daemon/", "--include=*.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # grep exits 1 on zero matches — that is a legitimate 0, not an
    # error (the F row expects it).
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"G-A: the pattern cross-check failed for {pattern!r}: "
            f"{result.stderr.strip()}"
        )
    return len(result.stdout.splitlines())


class TestGAstCensusFamilies:
    """G-A — the census families match the AST baseline."""

    def test_ast_scanner_reproduces_the_baseline_families(self):
        counts, _family_c, parse_errors = _run_scan()
        assert not parse_errors, (
            f"G-A: parse errors mean the tree was scanned "
            f"incompletely — the census is INVALID: {parse_errors}"
        )
        for family, expected in AST_BASELINE.items():
            observed = counts.get(family, 0)
            assert observed == expected, (
                f"G-A: family {family} drifted — baseline {expected}, "
                f"observed {observed}. Any delta is a FAILURE until "
                "classified per audit-method.md §7.3 (enumerate the "
                "delta sites, classify each, re-baseline only with an "
                "explicit note)"
            )
        total = sum(counts.values())
        assert total == AST_BASELINE_TOTAL, (
            f"G-A: the scanner-independent total drifted — baseline "
            f"{AST_BASELINE_TOTAL}, observed {total}"
        )

    def test_family_c_is_exactly_the_four_named_sites(self):
        _counts, family_c, _errs = _run_scan()
        observed_sites = sorted(f"{rel}:{line}" for rel, line, _text in family_c)
        expected_sites = sorted(FAMILY_C_SITES)
        assert observed_sites == expected_sites, (
            "G-A (§7.2 PRIMARY, scanner-independent): the manual-borrow "
            "family C must be EXACTLY the four named file:line sites. "
            f"expected {expected_sites}, observed {observed_sites} — a "
            "5th manual borrow must be audited individually before the "
            "lane may proceed"
        )

    def test_pattern_cross_check_reproduces_the_baseline(self):
        for family, pattern in (
            ("A", r"with (SQLModel\.)?Session\(|WriteGuardSession\(Session\("),
            ("B", r"with SQLModelSession\("),
            ("C", r"=\s*(SQLModel)?Session\("),
            ("F", r"raw_connection\(|getconn\(|putconn\("),
        ):
            observed = _pattern_count(pattern)
            expected = PATTERN_BASELINE[family]
            assert observed == expected, (
                f"G-A: the pattern cross-check drifted for family "
                f"{family} — baseline {expected}, observed {observed} "
                f"({_GNU_GREP_TRAP_NOTE})"
            )

    def test_family_f_is_the_audited_site_and_the_aggregate_stays_non_canonical(self):
        """Family F is MEASURED now (T-H2) — the ONE audited-clean
        one-shot psycopg.connect; and DR-6: the ≈689 aggregate stays
        NON-CANONICAL (it is the figure a reader would otherwise cite)."""
        counts, _family_c, parse_errors = _run_scan()
        assert not parse_errors
        # T-H2: family F is MEASURED now (the detector exists) — it is
        # the ONE audited-clean one-shot psycopg.connect. A SECOND raw
        # psycopg borrow is a manual pool checkout requiring individual
        # audit (the artifact's F=0 was a scanner gap, not a tree fact).
        assert counts.get("F", 0) == len(FAMILY_F_SITES), (
            "G-A: family F drifted — expected exactly "
            f"{len(FAMILY_F_SITES)} (the audited one-shot psycopg.connect), "
            f"observed {counts.get('F', 0)}; a new raw pool checkout "
            "requires individual audit"
        )
        with open(_REPO_ROOT / "daemon/services/wedge_capture/db_capture.py") as fh:
            db_capture = fh.read()
        assert "psycopg.connect(" in db_capture, (
            "G-A: the named family-F site is gone — re-audit the raw "
            "psycopg borrow before re-baselining"
        )
        # §3.3/§7.2: sessionmaker MUST remain 0 — a new sessionmaker
        # changes the session-construction contract tree-wide.
        sessionmaker_hits = _pattern_count(r"sessionmaker|async_sessionmaker")
        assert sessionmaker_hits == 0, (
            "G-A: a sessionmaker/async_sessionmaker appeared in "
            "daemon/ — the session-construction contract changed; the "
            "census must be RE-AUDITED, not just re-counted"
        )
        # DR-6: the ≈689 aggregate stays NON-CANONICAL — the audit
        # record must keep marking it (it is the figure a reader
        # would otherwise cite).
        audit = (
            _REPO_ROOT
            / ".agents"
            / "shared"
            / "planning"
            / "db-pool-idle-txn"
            / "audit-findings.md"
        ).read_text()
        assert "689" in audit and re.search(
            r"NON-CANONICAL|non-canonical|RETIRED", audit
        ), (
            "G-A: the audit record must keep the ≈689 aggregate "
            "explicitly NON-CANONICAL (DR-6)"
        )
