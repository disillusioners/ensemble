"""G-A pin — the census families match the AST baseline (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §5.2 (G-A row); the scanner and the
diff gate are ``audit-method.md`` §3.1/§7.2 (the materialised
scanner lives at ``scripts/audit/borrow_census.py`` — the §3.1
materialisation note assigns that path to the phase-3 implementer).

What this pin asserts: the borrow-site census over ``daemon/``
reproduces the **executed instrument's** family counts exactly —
**A=438**, **B=175**, **C=4** at the four named manual-borrow sites,
**D=150**, **E=15**, **F=2** — and the total **784**.

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
  originally found **F=1**: the ONE-SHOT ``psycopg.connect`` in
  ``daemon/services/wedge_capture/db_capture.py`` (the
  ``return psycopg.connect(`` call — line numbers drift with upstream
  merges, so the pin names the FILE and the STATEMENT) — which the
  contract already records as CLEAN (F5.3: *"a one-shot
  psycopg.connect with close() in finally ⇒ no pool, so it cannot
  leak"*). The pin names that site explicitly. **F is 2 since the
  2026-10-29 §7.3 re-baseline** (see the re-baseline record below) —
  the second site is the metrics census's own one-shot connect, same
  pattern, audited with all four criteria code-cited.
* Consequently the total is **784**, not the artifact's 779: the
  difference is exactly the two F sites the old instrument could not
  see (F 0 → 2) plus the checkpoint lane's three audited borrow sites
  (D +2, E +1) — both re-baselines are recorded below. Nothing else
  moved.

**Base vs HEAD (audit-method §7.2 diff gate):** the SAME fixed
instrument run at base ``19fc11c8b`` yields the IDENTICAL numbers
(A=438 / B=175 / C=4 / D=148 / E=14 / F=1 / total 780) ⇒ the lane
added **ZERO** borrow sites — the delta is empty, so no §7.3
classification table is required. Any family count delta from here on
is a FAILURE until classified per §7.3 — the diff IS the failing step.

**§7.3 RE-BASELINE (2026-10-29 — the checkpoint lane's THREE borrow
sites, individually audited; family D 148 → 150, family E 14 → 15,
total 781 → 784):** the checkpoint-read-path lane (merged into
``latest`` at ``4c8d2f105``) added THREE borrow sites to ``daemon/``.
**CODE DRIFT** (not scanner drift — the materialised instrument
``scripts/audit/borrow_census.py`` is UNCHANGED, byte-for-byte, by
this re-baseline; §5.3's "the scanner definition is the contract"
therefore still holds and the baseline, not the scanner, is what
moved). Classification table of three:

* ``daemon/services/checkpoint_prune.py:493`` — family **D** —
  **CLEAN** — ``with engine.connect() as conn:`` inside
  ``_analyze_one_table`` (:483-494), the post-prune ``ANALYZE`` sweep.
  (i) **NEVER POOLED** — the engine is the DEDICATED per-cycle
  autocommit engine built by ``create_vacuum_engine``
  (``daemon/services/vacuum_engine.py:54-88``), a **NullPool** engine
  whose ``connect_args`` carry the bounded ``statement_timeout`` /
  ``lock_timeout`` / ``idle_in_transaction_session_timeout`` triple
  (``checkpoint_prune.py:574-580``); (ii) **BOUNDED** — server-side
  ``statement_timeout`` = ``VACUUM_STATEMENT_TIMEOUT_MS`` (10 min,
  ``daemon/constants.py:570``) and ``lock_timeout`` =
  ``VACUUM_LOCK_TIMEOUT_MS`` (45 s, ``constants.py:571``);
  (iii) **GUARANTEED CLOSED on every path** — a ``with`` form, so it
  closes by construction (and the engine is ``dispose()``-d in the
  caller's ``finally``, :608-617); (iv) **NEVER HANDED OUT** — the
  connection is function-local and only the ``ANALYZE`` result leaves
  it. It is deliberately NOT the hot psycopg saver pool and NOT the
  adapter's shared asyncpg pool (:434-439).
* ``daemon/services/checkpoint_prune.py:693`` — family **D** —
  **CLEAN** — ``with engine.connect() as conn:`` inside
  ``_collect_blob_bytes_sync`` (:691-695), the per-channel blob byte
  census. Same dedicated NullPool per-cycle engine and the same
  bounded timeout triple (:749-755), and the engine is ``dispose()``-d
  in the caller's ``finally`` (:784-791); a ``with`` form ⇒ closes by
  construction; the connection is function-local and only the fetched
  rows leave it.
* ``daemon/checkpoint_adapter.py:1458`` — family **E** —
  **CLEAN** — ``async with self._pool.acquire() as conn:`` inside
  ``has_checkpoint`` (:1427-1464), the ``EXISTS`` probe that moved the
  retry-path existence check OFF the saver's single ``asyncio.Lock``
  (checkpoint-read-path plan Track-1 item 2, commit ``d9ece2fee``).
  (i) **NEVER A MANUAL CHECKOUT** — an ``async with`` form ⇒ it
  returns to the pool by construction, exactly like the 12 sibling
  ``self._pool.acquire()`` sites already in this file; (ii)
  **BOUNDED** — the asyncpg pool's own checkout budget plus the
  server-side bounds the pool is configured with; (iii)
  **GUARANTEED CLOSED on every path** — the ``async with`` exits on
  both the success and the exception path; (iv) **NEVER HANDED OUT**
  — the connection is function-local and only the fetched row leaves
  it. NOTE: this probe opens **NO explicit transaction** (unlike the
  sibling ``_delete_blobs_anti_join_chunk`` site, which wraps its work
  in ``conn.transaction()``) ⇒ it contributes **no** idle-in-transaction
  hold — the adapter pool's transaction-shape idle-in-txn item is the
  db-pool lane's routed R-9 (decisions.md D-58), not this site.

All three sites are ``with`` / ``async with`` forms ⇒ **0 LEAK, 0
unclosed**; the audit conclusion is unchanged. The re-baseline names
EXACTLY these three sites (see :data:`REBASELINED_DELTA_SITES`) and
nothing broader — the **drift rule STANDS**: any FOURTH borrow site in
``daemon/`` still REDs the family counts until individually audited.

The audited family-D baseline becomes **D=150** and family-E becomes
**E=15**; the total becomes **784** (438 + 175 + 4 + 150 + 15 + 2).
Families **A**, **B**, **C** and **F** are UNCHANGED — the checkpoint
lane added no session borrow, no manual borrow, and no raw-psycopg
borrow.

**§7.3 RE-BASELINE (2026-10-29 — one delta site, audited BENIGN on
all four criteria; family F 1 → 2, total 780 → 781):** the lane's
absorption of the WS-B metrics-collection merge (origin/latest
``bf673fdbe``; lane merge ``04de2d10c``) carried in
``daemon/services/metrics_census.py`` — a SECOND raw-psycopg site
(``return psycopg.connect(`` in ``connect_census``, :234). CODE DRIFT
(not scanner drift — the scanner is unchanged). Classification table
of one:

* ``daemon/services/metrics_census.py:234`` — family F — **CLEAN** —
  the census's own dedicated, NON-POOLED one-shot probe connection,
  the same pattern as the baseline site. All four benign criteria
  hold, code-cited: **(i) NEVER POOLED** — a direct
  ``psycopg.connect`` at :234-239 (``connect_census``, :226); the
  production wiring passes a DSN STRING only (``daemon/api.py:2381``
  ``resolve_dsn(manager.engine)``, which reads only
  ``engine.url.render_as_string()`` at ``metrics_census.py:189-205``;
  the sampler is constructed ``dsn=``-only at ``api.py:2385-2390``) —
  no pool object anywhere in the path. **(ii) BOUNDED** —
  ``connect_timeout=3`` s at :236 (constant ``:77``) and
  ``statement_timeout=5000`` ms set at the site via ``options`` at
  :238 (constant ``:74``). **(iii) GUARANTEED CLOSED on every path** —
  the sole production caller ``_query`` (:418-430) holds the
  connection inside ``try/finally`` with ``conn.close()`` in the
  ``finally`` (:425-429); a raising ``psycopg.connect`` leaves no
  connection to close — the same discipline as the baseline site's
  caller (``wedge_capture/db_capture.py:288-293``). **(iv) NEVER
  HANDED OUT** — ``conn`` is function-local (:420), closed in the same
  function, and only ``rows`` leave it (:430); the sampler stores the
  FACTORY callable, never a connection (:296).

The audited family-F baseline becomes **F=2**
{``wedge_capture/db_capture.py:195``,
``daemon/services/metrics_census.py:234``}. **The drift rule STANDS:**
any THIRD family-F site still REDs until individually audited — this
re-baseline names exactly one new site, nothing broader.

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

import ast
import importlib.util
import os
import re
import subprocess
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
#: 4 -> 5 (2026-10-29 merge-semantics fix): the R3 re-point added the
#: path-existence leg — see FOLD-MAP NOTE-6.
EXPECTED_COLLECTED = 5

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCANNER_PATH = _REPO_ROOT / "scripts" / "audit" / "borrow_census.py"

#: The EXECUTED instrument's family counts (T-H2: B and F are measured,
#: not structurally zero). Measured with the fixed instrument at base
#: ``19fc11c8b`` AND at HEAD — the two were IDENTICAL (the lane added
#: zero borrow sites) until the 2026-10-29 §7.3 re-baseline moved F 1→2
#: (the WS-B metrics-census site, audited benign — module docstring).
#: The audit artifact's A=613 is this instrument's A+B COMBINED
#: (438 + 175) and its F=0 was a SCANNER GAP (no F detector); see the
#: module docstring.
AST_BASELINE = {"A": 438, "B": 175, "C": 4, "D": 150, "E": 15, "F": 2}
AST_BASELINE_TOTAL = 784

#: The THREE borrow sites the checkpoint lane added — the §7.3
#: re-baseline's named classification table (module docstring above).
#: Each row is (family, path, the ENCLOSING QUALIFIED FUNCTION, the
#: audited count of sites inside that function).
#:
#: Keyed on PATH + QUALIFIED FUNCTION, not on a line number: the lines
#: drift with every upstream merge, and one file can carry MANY borrows
#: of the same statement shape (the adapter holds 13
#: ``self._pool.acquire()`` sites — 12 pre-existing plus this one). The
#: qualified function is the auditable unit — it is what the §7.3
#: classification table names.
#:
#: The counts here are the §7.3 DELTA legs, not the family totals — the
#: totals stay gated by AST_BASELINE above. A FOURTH borrow site in
#: ``daemon/`` still REDs the family counts until individually audited.
REBASELINED_DELTA_SITES = (
    ("D", "daemon/services/checkpoint_prune.py", "_analyze_one_table", 1),
    (
        "D",
        "daemon/services/checkpoint_prune.py",
        "_collect_blob_bytes_sync",
        1,
    ),
    (
        "E",
        "daemon/checkpoint_adapter.py",
        "PostgresCheckpointerAdapter.has_checkpoint",
        1,
    ),
)

#: The TWO family-F sites — audited-clean one-shot psycopg.connect
#: calls (contract F5.3: closes in a finally, holds no pool, cannot
#: leak). Named explicitly so a THIRD raw-psycopg borrow is a loud
#: failure. Path-only (no line number): the line drifts with every
#: upstream merge, and the pin's MEANING is "this file holds an
#: audited raw-psycopg borrow" (the statement text is asserted
#: separately). Site 2 (metrics_census.py) added by the 2026-10-29
#: §7.3 re-baseline (WS-B, bf673fdbe) — benign on all four criteria;
#: see the module docstring's re-baseline record.
FAMILY_F_SITES = (
    "daemon/services/wedge_capture/db_capture.py",
    "daemon/services/metrics_census.py",
)

#: The §7.2 / §3.2 pattern cross-check constants.
PATTERN_BASELINE = {"A": 439, "B": 175, "C": 4, "F": 0}

#: The four named manual-borrow sites (audit-method §6.3) — the
#: scanner-independent primary assertion.
#:
#: RE-POINTED (2026-10-29 merge-semantics fix): the pin was keyed on
#: bare ``file:line`` literals, so the upstream merge's +12-line shift
#: in ``daemon/manager.py`` (2782 -> 2794) turned it RED. The pin is
#: now keyed on (PATH, STATEMENT) — the borrow itself — which is
#: drift-robust AND stricter: the STATEMENT must match, so a borrow
#: that was rewritten in place cannot hide behind a re-pointed line
#: number. The audit record keeps the line numbers for the reader.
FAMILY_C_SITES = (
    ("daemon/manager.py", "session = Session(self._engine)"),
    (
        "daemon/repositories/auth_bootstrap/repository.py",
        "session = Session(engine)",
    ),
    (
        "daemon/repositories/auth_bootstrap/repository.py",
        "session = Session(self._engine)",
    ),
    (
        "daemon/services/child_reports.py",
        "session = Session(self._manager._engine)",
    ),
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


def _per_site_counts(family: str) -> dict[tuple[str, str], int]:
    """Enumerate the per-(path, QUALIFIED FUNCTION) borrow sites of
    family D or E.

    Uses the MATERIALISED INSTRUMENT'S OWN predicates
    (``scripts/audit/borrow_census.py`` — ``_is_connect_call`` /
    ``_is_acquire_call``), so this list can never drift from the
    definition that produces the counted totals (audit-method §3.1:
    "the scanner definition is the contract, not the aggregate
    number"). The instrument exposes per-site detail for family C
    only; families D and E need the same naming for the §7.3
    re-baseline record, so this helper re-derives it THROUGH the
    instrument's own predicates — not through a second, competing
    definition.

    Sites are keyed on the ENCLOSING QUALIFIED FUNCTION
    (``Class.method`` for a method, ``func`` for a module-level
    function), NOT on the statement text and NOT on a line number: one
    file can carry MANY borrows of the same statement shape (the
    adapter holds 13 ``self._pool.acquire()`` sites, 12 of them
    pre-existing), and lines drift with every upstream merge. The
    qualified function is the auditable unit — it is what the §7.3
    classification table names.

    Returns a map ``(relpath, qualified_function) -> count``.
    """
    module = _load_scanner()
    root = str(_REPO_ROOT)
    is_target = (
        module._is_connect_call if family == "D" else module._is_acquire_call
    )
    node_kind = ast.With if family == "D" else ast.AsyncWith
    counts: dict[tuple[str, str], int] = {}

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = stack + [child.name]
                for sub in ast.walk(child):
                    if isinstance(sub, node_kind):
                        for item in sub.items:
                            if is_target(item.context_expr):
                                key = (os.path.relpath(path, root), ".".join(qual))
                                counts[key] = counts.get(key, 0) + 1
                visit(child, qual)
            elif isinstance(child, ast.ClassDef):
                visit(child, stack + [child.name])
            else:
                visit(child, stack)

    for dirpath, dirnames, filenames in os.walk(os.path.join(root, "daemon")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            visit(tree, [])
    return counts


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
        # §7.3: the re-baseline NAMES its delta sites — the three
        # borrow sites the checkpoint lane added (module docstring's
        # classification table). The family counts above gate the
        # totals; this leg names WHERE the audited delta sites are, so
        # a future delta is attributable to a file and the audit
        # record stays truthful. Line numbers are deliberately NOT
        # pinned (they drift with every upstream merge — the same
        # contract the family-C pin uses). Enumeration rides the
        # INSTRUMENT'S OWN predicates (_per_site_counts), never a
        # second competing definition of the family.
        for family, rel, statement, expected_sites in REBASELINED_DELTA_SITES:
            observed_sites = _per_site_counts(family).get((rel, statement), 0)
            assert observed_sites == expected_sites, (
                f"G-A: the §7.3 re-baselined family-{family} site "
                f"{rel} ({statement}) drifted — expected "
                f"{expected_sites} audited site(s), observed "
                f"{observed_sites}. Re-audit the delta per "
                "audit-method.md §7.3 (classify every site, then "
                "re-baseline only with an explicit note) before "
                "touching REBASELINED_DELTA_SITES."
            )

    def test_family_c_is_exactly_the_four_named_sites(self):
        _counts, family_c, _errs = _run_scan()
        # Keyed on (PATH, STATEMENT) — not on a bare line number (an
        # upstream merge shifts lines; the statement is the audited
        # site). The scanner truncates the statement at 90 chars, which
        # none of the four reaches.
        observed_sites = sorted((rel, text) for rel, _line, text in family_c)
        expected_sites = sorted(FAMILY_C_SITES)
        assert observed_sites == expected_sites, (
            "G-A (§7.2 PRIMARY, scanner-independent): the manual-borrow "
            "family C must be EXACTLY the four named (path, statement) "
            f"sites. expected {expected_sites}, observed "
            f"{observed_sites} — a 5th manual borrow must be audited "
            "individually before the lane may proceed"
        )

    def test_the_named_family_c_sites_still_exist_at_their_paths(self):
        """The (path, statement) pin above is only as good as the paths:
        this leg asserts each named path still EXISTS and still carries
        its statement — so a moved/renamed file cannot silently drop
        out of the comparison."""
        for rel, statement in FAMILY_C_SITES:
            path = _REPO_ROOT / rel
            assert path.exists(), (
                f"G-A: the named family-C site path {rel} no longer exists"
            )
            body = path.read_text()
            assert statement in body, (
                f"G-A: the named family-C site {rel} no longer carries "
                f"{statement!r}"
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
        """Family F is MEASURED now (T-H2) — the TWO audited-clean
        one-shot psycopg.connect sites (F=2 since the 2026-10-29 §7.3
        re-baseline — the WS-B metrics-census site); and DR-6: the ≈689
        aggregate stays NON-CANONICAL (it is the figure a reader would
        otherwise cite)."""
        counts, _family_c, parse_errors = _run_scan()
        assert not parse_errors
        # T-H2: family F is MEASURED now (the detector exists) — it is
        # EXACTLY the audited-clean one-shot psycopg.connect sites
        # (db_capture + metrics_census, the §7.3 re-baseline record in
        # the module docstring). A THIRD raw psycopg borrow is a manual
        # pool checkout requiring individual audit (the artifact's F=0
        # was a scanner gap, not a tree fact).
        assert counts.get("F", 0) == len(FAMILY_F_SITES), (
            "G-A: family F drifted — expected exactly "
            f"{len(FAMILY_F_SITES)} (the audited one-shot "
            "psycopg.connect sites), "
            f"observed {counts.get('F', 0)}; a new raw pool checkout "
            "requires individual audit"
        )
        for rel in FAMILY_F_SITES:
            site_body = (_REPO_ROOT / rel).read_text()
            assert "psycopg.connect(" in site_body, (
                f"G-A: the named family-F site {rel} is gone — re-audit "
                "the raw psycopg borrow before re-baselining"
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
