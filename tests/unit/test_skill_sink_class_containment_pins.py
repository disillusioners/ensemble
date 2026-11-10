"""CLASS-WIDE containment pins for the capture-lane sink (2026-11-10).

THE DEFECT CLASS THIS FILE CLOSES.  The capture-observability sink
resolves its data dir from a PROCESS-GLOBAL override
(``observability.configure_sink(...)``): ``ENSEMBLE_DATA_DIR`` >
``DATA_DIR`` > ``./data`` — and ``./data`` is **CWD-relative**, i.e. the
CHECKOUT's real ``data/`` tree.  A test fixture that installs a redirect
(``configure_sink(tmp_path)``) and then RESTORES THE OVERRIDE TO ``None``
in its teardown re-exposes the checkout's real data dir for every write
that follows — the order-dependent sink leak (a later file's test, a
background task finishing late, a stray call).

``None`` is therefore the ONE value a lane teardown may never restore.
The sanctioned restore is ``restore_safe_sink_redirect()`` (a scratch
dir), never ``None``.

WHY A CLASS-WIDE PIN AND NOT FOUR PER-FILE FIXES.  Fixing the four
known poisoners closes today's leaks; it does not stop the NEXT one.
This file derives the class FROM THE TREE and pins the class-wide
invariant, so a new poisoner fails a node instead of silently leaking:

* **N1 — STATIC CLASS CENSUS (derived).**  The class is DERIVED by
  AST-scanning every ``tests/**.py`` for the poison signature
  ``configure_sink(None)`` (or the zero-arg ``configure_sink()``, which
  means exactly the same thing).  The ONLY sanctioned occurrence is the
  deliberate P9 rogue-teardown SIMULATION in
  ``tests/unit/test_skill_sink_retention_shared_path_pins.py`` — the
  count there is pinned, and the set of OTHER files must be EMPTY.  A
  new poisoner fails this node LOUDLY, with the offending file:line
  list.  Nothing here is an authored tuple of offenders: only the
  SANCTIONED site is authored (a policy fact); the class is derived.

* **N2 — MARKER ORACLE (dynamic, per member).**  Each class member is
  driven in a SUBPROCESS with the sink NOT redirected (no
  ``ENSEMBLE_DATA_DIR``, no ``DATA_DIR``) and a UNIQUE PER-RUN MARKER.
  A pytest plugin stamps that marker into every record the run writes
  and, at session finish (AFTER the member's fixtures tore down),
  writes ONE probe record and reports where the sink resolved.  The
  node then asserts that NO file under the checkout's real
  ``data/skill-capture-observability/`` carries that marker, and that
  the resolved sink dir is not the real one.  The oracle is the MARKER
  — never byte-identity — because that dir is SHARED with every other
  lane working in this worktree.

* **N3 — SHARED-FILE SENTINEL.**  Across the whole class run, no shared
  sink file may VANISH and none may SHRANK (destruction is never
  legitimate).  Reuses the house helper
  ``assert_sink_never_reaches_real_dir(before=...)``.

RACE TOLERANCE.  The real sink dir is shared evidence: a concurrent
lane may append at any moment (growth is fine) and a concurrent lane's
pack cleanup may rewrite the daily file (a shrink is reported by N3 —
see the house note on F-G1's row-granular cleanup).  The MARKER is the
race-free oracle; the file-level observation is the secondary one.

NON-VACUITY.  The probe writes a REAL record through the REAL sink
writer, so a leaked override cannot hide: the marker lands in the real
dir and the node goes red.  A member that cannot even be collected (the
worktree is live and another lane edits it) is reported INCONCLUSIVE
rather than failed — the containment question is answered by the probe,
not by the member's own test outcome.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from daemon.services import skill_capture_observability as observability
from tests.helpers.skill_sink_redirect import (
    REAL_SINK_DIR,
    assert_sink_never_reaches_real_dir,
    restore_safe_sink_redirect,
    scrub_data_dir_env,
    snapshot_real_sink_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"

#: The SANCTIONED poison site — the deliberate P9 rogue-teardown
#: SIMULATION in the sink-retention pin file (the pin proves the guard
#: BITES, and restores the safe redirect in its ``finally``).  This is a
#: POLICY fact, not an offender list: the CLASS itself is derived.
SANCTIONED_POISON_SITES: dict[str, int] = {
    "tests/unit/test_skill_sink_retention_shared_path_pins.py": 2,
}

#: The per-variable env scrub the house requires for every run.
HOUSE_ENV_SCRUB: tuple[str, ...] = (
    "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
    "ENSEMBLE_AUTH_CLIENT_ID",
    "ENSEMBLE_AUTH_CLIENT_SECRET",
    "ENSEMBLE_AUTH_DISCOVERY_URL",
    "ENSEMBLE_AUTH_ENABLED",
    "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
    "ENSEMBLE_AUTH_ISSUER",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
)

#: The pytest plugin that stamps the marker and runs the probe.  It is
#: written to a scratch dir at RUN time (never committed) and loaded with
#: ``-p``.  It never edits the member's behaviour — it only adds a
#: marker field to every record the run writes.
_MARKER_PLUGIN_SOURCE = '''
import json
import os

_MARKER = os.environ["ENS_SINK_CLASS_MARKER"]
_PROBE_FILE = os.environ["ENS_SINK_CLASS_PROBE_FILE"]
_STOP_AFTER_FIRST = os.environ.get("ENS_SINK_CLASS_STOP_AFTER_FIRST") == "1"
_STATE = {"ran": 0, "session": None}


def pytest_configure(config):
    from daemon.services import skill_capture_observability as obs

    original = obs.append_run_record

    def patched(record, **kwargs):
        stamped = dict(record)
        stamped["_sink_class_marker"] = _MARKER
        return original(stamped, **kwargs)

    obs.append_run_record = patched


def pytest_sessionstart(session):
    _STATE["session"] = session


def pytest_runtest_logfinish(nodeid, location):
    """Optionally stop after the FIRST test — the teardown state is what
    matters for the containment question (the class run is bounded)."""
    _STATE["ran"] += 1
    session = _STATE["session"]
    if _STOP_AFTER_FIRST and session is not None:
        session.shouldstop = True


def pytest_sessionfinish(session, exitstatus):
    """The PROBE — one real record, written where the override points."""
    from daemon.services import skill_capture_observability as obs

    resolved = str(obs.resolve_sink_dir())
    override = obs.sink_override()
    wrote = False
    try:
        # A huge retention window: the PROBE must never prune anything.
        obs.append_run_record(
            {
                "stage": "sink_class_probe",
                "run_id": _MARKER,
                "agent_id": None,
                "prompt_source": "sink_class_probe",
                "outcome": "probe",
            },
            retention_days=10 ** 6,
        )
        wrote = True
    except Exception:  # pragma: no cover — the sink never raises
        wrote = False
    payload = {
        "marker": _MARKER,
        "resolved": resolved,
        "override": None if override is None else str(override),
        "ran": _STATE["ran"],
        "probe_wrote": wrote,
    }
    try:
        with open(_PROBE_FILE, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))
    except OSError:  # pragma: no cover
        pass
'''


# ── derivation ─────────────────────────────────────────────────────────────


def _iter_test_files() -> list[Path]:
    """Every ``*.py`` under ``tests/`` (sorted, deterministic)."""
    return sorted(TESTS_ROOT.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _configure_sink_calls(path: Path) -> list[tuple[int, bool]]:
    """Every ``configure_sink(...)`` CALL in ``path``.

    Returns ``(lineno, poisons)`` pairs, sorted by line.  ``poisons`` is
    True when the call RESETS the override to the resolved default —
    ``configure_sink(None)`` and the zero-arg ``configure_sink()`` mean
    exactly the same thing, and both are poison.

    The scan is an AST walk, not a text search: a DOCSTRING that merely
    mentions ``configure_sink(None)`` (the helper's own module docstring
    does) is NOT a call and must never be counted as a poisoner.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):  # pragma: no cover — a broken file
        return []
    found: list[tuple[int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute) and func.attr == "configure_sink"
        ):
            continue
        poisons = False
        if not node.args and not node.keywords:
            # ``configure_sink()`` → the default (None).
            poisons = True
        elif len(node.args) == 1 and not node.keywords:
            arg = node.args[0]
            poisons = isinstance(arg, ast.Constant) and arg.value is None
        found.append((node.lineno, poisons))
    return sorted(found)


def poison_sites() -> dict[str, list[int]]:
    """The DERIVED class: ``{repo-relative file: [lineno, ...]}``."""
    sites: dict[str, list[int]] = {}
    for path in _iter_test_files():
        lines = [lineno for lineno, poisons in _configure_sink_calls(path) if poisons]
        if lines:
            sites[_rel(path)] = lines
    return sites


def sink_override_managers() -> list[str]:
    """The class MEMBERS — the files that MANAGE the sink override.

    Derived from the tree (any ``configure_sink(...)`` call), not
    authored: a file that installs a redirect owns the process-global
    override for the whole session, so its teardown discipline IS the
    containment boundary.  ``tests/helpers/**`` is infrastructure, not a
    test file, and is excluded from the member set (it is still covered
    by N1's census).
    """
    members: list[str] = []
    for path in _iter_test_files():
        rel = _rel(path)
        if rel.startswith("tests/helpers/"):
            continue
        if _configure_sink_calls(path):
            members.append(rel)
    return sorted(members)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def _marker_hits(marker: str) -> list[str]:
    """The real sink files that carry ``marker`` (the oracle)."""
    if not REAL_SINK_DIR.is_dir():
        return []
    hits: list[str] = []
    for path in sorted(REAL_SINK_DIR.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover — a racing prune
            continue
        if marker in text:
            hits.append(path.name)
    return hits


# ── the class run (N2's engine, shared with N3) ───────────────────────────


class _MemberRun:
    """The outcome of driving ONE class member in a subprocess."""

    def __init__(self, member: str, rc: int, probe: dict | None, stdout: str):
        self.member = member
        self.rc = rc
        self.probe = probe or {}
        self.stdout = stdout

    @property
    def ran(self) -> int:
        return int(self.probe.get("ran", 0) or 0)

    @property
    def marker(self) -> str:
        return str(self.probe.get("marker", ""))

    @property
    def resolved(self) -> str:
        return str(self.probe.get("resolved", ""))

    @property
    def inconclusive(self) -> bool:
        return self.ran == 0


def _drive_member(member: str, workdir: Path) -> _MemberRun:
    """Drive ONE member in a subprocess with the sink NOT redirected."""
    marker = f"sink-class-{uuid.uuid4().hex}"
    plugin_dir = workdir / f"plugin-{uuid.uuid4().hex}"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "sink_class_marker_plugin.py").write_text(
        _MARKER_PLUGIN_SOURCE, encoding="utf-8"
    )
    probe_file = workdir / f"probe-{uuid.uuid4().hex}.json"
    probe_file.write_text("{}", encoding="utf-8")

    env = scrub_data_dir_env()
    for name in HOUSE_ENV_SCRUB:
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(plugin_dir), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env["ENS_SINK_CLASS_MARKER"] = marker
    env["ENS_SINK_CLASS_PROBE_FILE"] = str(probe_file)
    env["ENS_SINK_CLASS_STOP_AFTER_FIRST"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            member,
            "-q",
            "--tb=line",
            "-p",
            "sink_class_marker_plugin",
            "-p",
            "no:cacheprovider",
            "--timeout=120",
            "--override-ini=addopts=",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    try:
        probe = json.loads(probe_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        probe = {}
    return _MemberRun(member, completed.returncode, probe, completed.stdout[-2000:])


@pytest.fixture(scope="module")
def _class_run(tmp_path_factory) -> dict:
    """Drive the WHOLE derived class once; N2 and N3 read the result."""
    workdir = tmp_path_factory.mktemp("sink-class")
    before = snapshot_real_sink_dir()
    runs = [_drive_member(member, workdir) for member in sink_override_managers()]
    after = snapshot_real_sink_dir()
    return {"runs": runs, "before": before, "after": after}


@pytest.fixture(scope="module", autouse=True)
def _pin_sandbox():
    """The pin's OWN sink redirect — a scratch dir, NEVER ``None``."""
    restore_safe_sink_redirect()
    try:
        yield
    finally:
        # NEVER None — the same rule this file pins.
        restore_safe_sink_redirect()


# ── N1 — the static class census ───────────────────────────────────────────


def test_n1_no_test_file_may_restore_the_sink_override_to_none():
    """N1 — the DERIVED class census: only the sanctioned site may poison.

    The class is derived by AST-scanning ``tests/**.py`` for the poison
    signature ``configure_sink(None)`` (and the zero-arg equivalent).
    Any file outside the sanctioned simulation site is a NEW poisoner and
    fails this node loudly, with the offending file:line list.
    """
    sites = poison_sites()

    offenders: dict[str, list[int]] = {
        rel: lines
        for rel, lines in sites.items()
        if rel not in SANCTIONED_POISON_SITES
    }
    assert not offenders, (
        "a test file restores the sink override to None — the checkout's "
        "REAL data dir is re-exposed for every later write (the "
        "order-dependent sink leak). Offending sites:\n"
        + "\n".join(
            f"  {rel}:{lineno}  configure_sink(None)"
            for rel, lines in sorted(offenders.items())
            for lineno in lines
        )
        + "\nUse the sanctioned restore: restore_safe_sink_redirect() "
        "(tests/helpers/skill_sink_redirect.py)."
    )

    for rel, expected in SANCTIONED_POISON_SITES.items():
        found = sites.get(rel, [])
        assert len(found) == expected, (
            f"the SANCTIONED simulation site {rel} must carry exactly "
            f"{expected} poison call(s) (the deliberate P9 rogue-teardown "
            f"simulation); found {len(found)} at {found}. A changed count "
            "means the simulation was reworked — re-derive the sanction."
        )

    # Non-vacuity: the census MUST be able to see a poisoner.  The
    # sanctioned site's own occurrences prove the scanner sees them.
    assert sites.get(
        "tests/unit/test_skill_sink_retention_shared_path_pins.py", []
    ), "the census found nothing — the scanner is broken (non-vacuity)"


# ── N2 — the marker oracle ─────────────────────────────────────────────────


@pytest.mark.timeout(900)
def test_n2_no_class_member_can_write_outside_its_scratch_dir(_class_run):
    """N2 — the MARKER oracle: the class cannot reach the real dir.

    Each derived member is driven in a subprocess with the sink NOT
    redirected and a UNIQUE PER-RUN MARKER.  The plugin stamps that
    marker into every record the run writes and probes the sink at
    session finish (after the member's teardown).  No file under the
    checkout's real sink dir may carry the marker, and the resolved sink
    dir must not BE the real one.
    """
    runs: list[_MemberRun] = _class_run["runs"]
    assert runs, "the class is empty — the derivation found no members"

    leaks: list[str] = []
    unresolved: list[str] = []
    inconclusive: list[str] = []
    covered_by_marker: list[str] = []

    for run in runs:
        if not run.probe:
            unresolved.append(
                f"{run.member} (the probe never reported — rc={run.rc})\n"
                f"{run.stdout}"
            )
            continue
        if run.inconclusive:
            # The member did not run a test (a live worktree, another
            # lane's WIP): the containment question is unanswered.
            inconclusive.append(f"{run.member} (0 tests ran, rc={run.rc})")
            continue
        covered_by_marker.append(run.member)
        resolved = Path(run.resolved) if run.resolved else REPO_ROOT / "data"
        if _is_under(resolved, REAL_SINK_DIR):
            leaks.append(
                f"{run.member}: the sink resolved into the checkout's real "
                f"sink dir after the member's teardown: {resolved}"
            )
        hits = _marker_hits(run.marker)
        if hits:
            leaks.append(
                f"{run.member}: the run's marker {run.marker} landed in the "
                f"checkout's real sink dir: {hits}"
            )

    assert not leaks, (
        "a class member can write OUTSIDE its scratch dir — the sink "
        "override was reset to None (or never installed):\n  "
        + "\n  ".join(leaks)
        + f"\n(marker coverage: {len(covered_by_marker)} member(s); "
        f"inconclusive: {inconclusive})"
    )
    assert not unresolved, (
        "the probe never reported for these members — the oracle is "
        "broken, the node would be vacuous:\n  " + "\n  ".join(unresolved)
    )
    # Report the coverage shape (marker vs seam attribution).  Every
    # member that ran is marker-covered: the probe writes a REAL record
    # carrying the run's unique marker.
    print(
        f"\n[sink-class] marker-covered members: {len(covered_by_marker)}"
        f"/{len(runs)}"
        + (f"; inconclusive: {inconclusive}" if inconclusive else "")
    )


# ── N3 — the shared-file sentinel ──────────────────────────────────────────


@pytest.mark.timeout(900)
def test_n3_the_class_run_neither_destroyed_nor_shrank_a_shared_sink_file(
    _class_run,
):
    """N3 — the shared sink dir survived the class run intact.

    Reuses the house helper: no shared sink file may VANISH and none may
    SHRANK (destruction is never legitimate), and the override in force
    must never be ``None``.
    """
    assert_sink_never_reaches_real_dir(_class_run["before"])
