"""SINK-RETENTION shared-path pins — the capture-lane sink must never
delete a SHARED file (skill-system-revival Phase 1, workstream D).

Registered pack: ``skill_capture_observability_unit_test`` (EXTENDED) —
see ``.agents/tester/PACKS.md``.

THE DEFECT CLASS (a sibling lane already paid for it)
-----------------------------------------------------
The sink dir (``<data_dir>/skill-capture-observability/``) holds ONE
daily file per day (``skill-capture-<YYYYMMDD>.jsonl``) and EVERY lane
appends into the SAME file of the day.  A cleanup that deletes such a
file therefore destroys every other lane's rows — the sibling lane's
gate pack did exactly that (116 rows, gitignored, unrecoverable; the
house lesson is ``.agents/tester/LESSONS/2026-11-10-pack-cleanup-row-granularity.md``).

TWO HALVES OF THE CLASS, BOTH PINNED HERE
------------------------------------------
* **PRODUCTION** — the retention prune
  (``daemon/services/skill_capture_observability.py::_prune_expired_sink_files``)
  used to unlink a file when its **MTIME** fell out of the window.  The
  mtime is mutable metadata (a restore from a backup, a copy, an
  ``os.utime``, a fixture with an old timestamp) while the DATE IN THE
  NAME is the file's identity — the day whose runs it holds.  An
  mtime-keyed prune could therefore unlink a file named for TODAY, and
  it excluded nothing that a writer held open.  The prune now keys on
  the NAME-DATE and never unlinks the file the writer holds open.
* **TEST** — the lane's own test cleanup (the L7 pin in
  ``tests/unit/test_skill_fix_burst_guard.py``) deleted whole files in
  the checkout's REAL sink dir whenever a stray appeared.  The cleanup
  is now ROW-GRANULAR (read → filter the marked rows → atomic rewrite),
  the file is NEVER unlinked, and the marker is unique per run.

NAMED MUTATION LEGS (each must turn this file RED)
--------------------------------------------------
* **M-SR1** — revert the prune to mtime-keying → P1 REDs.
* **M-SR2** — drop the prune's current-file guard → P3 REDs.
* **M-SR3** — widen the prune to any file in the dir → P4 REDs.
* **M-SR4** — make the prune a no-op → P2 REDs.
* **M-SR5** — reintroduce a shared-path delete in a lane test file →
  P6 REDs.
* **M-SR6** — make the row-granular cleanup delete the whole file →
  P8 REDs.
* **M-SR7** — remove a lane test's sink redirect → P7 REDs (and the
  burst-guard file's own L7 pin REDs).

THE HOUSE GUARD (the class-proof)
---------------------------------
P6 (static) and P7 (sentinel) assert the CLASS, not one instance:
no test file of this lane may carry a destructive filesystem call, and
no lane write may reach a shared path — the checkout's real sink dir
must be byte-identical after a lane write.

THE WHITE-BOX PINS (P1–P4) call the module's private prune on purpose:
the retention decision is an internal policy, and the discriminating
shapes (a stale mtime, an out-of-window name-date, a name that encodes
no day) cannot be produced through the public append path.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from daemon.services import skill_capture_observability as observability

# The lane's SAFE sink redirect + the shared-path sentinel (the
# order-dependent leak fix: a teardown may never restore None).
from tests.helpers.skill_sink_redirect import (
    LANE_ROW_MARKERS,
    assert_sink_never_reaches_real_dir,
    lane_test_end,
    lane_test_start,
    restore_safe_sink_redirect,
    scrub_data_dir_env,
    snapshot_real_sink_dir,
)

#: The repository root — the lane's files are addressed from here.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The CHECKOUT's real sink dir — the shared path under audit.  The
#: lane's tests must never write into it and never delete from it.
REAL_SINK_DIR = REPO_ROOT / "data" / observability.SINK_DIR_NAME

#: The lane's TEST files (the static house-guard scans exactly these).
LANE_TEST_FILES = (
    "tests/services/test_skill_capture_observability.py",
    "tests/unit/test_skill_revival_fix_wave1_pins.py",
    "tests/unit/test_skill_revival_wave2_pins.py",
    "tests/unit/test_skill_stale_deactivation.py",
    "tests/unit/test_skill_fix_burst_guard.py",
    "tests/unit/test_skill_sink_retention_shared_path_pins.py",
)

#: The lane's SUPPORT modules (the static house-guard scans these too —
#: a support module is just as able to carry a destructive call).
LANE_SUPPORT_FILES = (
    "tests/helpers/skill_sink_redirect.py",
)

#: The lane's PACK scripts (the static house-guard scans these too).
LANE_PACK_SCRIPTS = (
    "test/packs/skill_capture_observability_unit_test.sh",
    "test/packs/skill_evolution_unit_test.sh",
    "test/packs/skill_stale_deactivation_unit_test.sh",
    # The pack that CAUSED the F-G1 incident (the file-level `rm -f` of
    # the shared daily sink).  The tester's gate flagged the omission of
    # this very pack from the static class-proof — the scan is READ-ONLY,
    # so covering another lane's pack costs nothing.
    "test/packs/metrics_capture_lane_off_seam_unit_test.sh",
)

#: The destructive filesystem calls a lane test must never make.  The
#: rule is deliberately STRICT (fail-closed): a lane test that needs to
#: delete a file it created must say so here — the default is "no
#: deletes at all", because the ONLY safe delete is one that can be
#: proven to be scoped to the test's own scratch dir.
_DESTRUCTIVE_CALLS = frozenset(
    {"unlink", "remove", "rmtree", "rmdir", "removedirs"}
)

#: A shell delete command in a lane pack script.
_SHELL_DELETE = re.compile(r"\brm\s+-[a-zA-Z]*[rf]|\brmtree\b|\bunlink\b")

#: A variable assigned from ``mktemp`` — the ONLY paths a pack script
#: may delete (a scratch dir it created itself).
_MKTEMP_ASSIGN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)=.*\bmktemp\b"
)

#: A ``$VAR`` / ``${VAR}`` reference in a shell line.
_VAR_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


# ── The shared row-granular cleanup (the F-G1 house shape) ───────────────


def json_line(payload: dict) -> str:
    """Render ONE JSONL row (the sink's own row shape)."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"


def remove_rows_carrying(path: Path, marker: str) -> int:
    """Remove ONLY the rows carrying ``marker`` — NEVER the file.

    ROW-GRANULAR BY CONSTRUCTION (the F-G1 house lesson): the sink
    appends EVERY lane into ONE daily file, so a file-level delete
    destroys every other lane's rows in that file.  The file is
    rewritten atomically (temp + ``os.replace``) and is NEVER unlinked
    — the sink appends by path, so an emptied file is deliberately left
    in place (empty).

    The temp file is created next to the target with a ``.row-clean``
    suffix, so it can never be mistaken for a sink file (the prune's
    glob is ``skill-capture-*.jsonl`` and a leading dot never matches
    it).

    Returns:
        The number of removed rows (0 ⇒ the file was not touched at
        all — the common case).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    lines = text.splitlines()
    keep = [line for line in lines if marker not in line]
    removed = len(lines) - len(keep)
    if removed == 0:
        return 0
    payload = "".join(line + "\n" for line in keep)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".row-clean"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    # Preserve the sink file's own mode (the rewrite must not change it).
    try:
        os.chmod(tmp_name, os.stat(path).st_mode & 0o7777)
    except OSError:
        pass
    os.replace(tmp_name, path)
    return removed


def marker_for_run() -> str:
    """Mint a marker unique to THIS invocation (per-run, per-lane)."""
    return f"skill-sink-pin-{os.getpid()}-{uuid.uuid4().hex[:12]}"


def sink_snapshot(directory: Path) -> dict[str, bytes]:
    """Snapshot the sink files' BYTES (the house's "shrink = incident")."""
    if not directory.exists():
        return {}
    return {
        path.name: path.read_bytes()
        for path in sorted(directory.glob("*.jsonl"))
    }


# ── Harness ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _sink_sandbox(tmp_path, request):
    """Point the sink at a SCRATCH dir for every test in this file.

    The teardown NEVER restores the override to ``None``: that
    re-exposed the checkout's REAL data dir for every later write (the
    order-dependent sink leak).  It restores the SAFE session redirect
    and runs the shared-path sentinel.
    """
    lane_test_start(request.node.nodeid)
    before = snapshot_real_sink_dir()
    observability.configure_sink(tmp_path)
    try:
        yield tmp_path
    finally:
        # NEVER restore to None: that re-exposes the checkout's REAL
        # data dir for every later write (the order-dependent sink
        # leak).  Restore the SAFE session redirect instead.
        restore_safe_sink_redirect()
        lane_test_end(request.node.nodeid, before)


def _sink_dir(tmp_path) -> Path:
    return tmp_path / observability.SINK_DIR_NAME


def _write_sink_file(sink_dir: Path, name: str, payload: str = "{}\n") -> Path:
    sink_dir.mkdir(parents=True, exist_ok=True)
    path = sink_dir / name
    path.write_text(payload, encoding="utf-8")
    return path


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


# ── P1 — the retention decision follows the NAME, never the mtime ────────


def test_p1_a_file_named_for_today_is_never_pruned_for_a_stale_mtime(tmp_path):
    """P1 — the mtime is NOT the retention key.

    A sink file named for TODAY whose mtime is 120 days old (a restore
    from a backup, a copy, an ``os.utime``, a fixture with an old
    timestamp) is TODAY's live file and MUST survive the prune.

    Named mutation: revert the prune to mtime-keying → this pin REDs.
    """
    sink_dir = _sink_dir(tmp_path)
    live = _write_sink_file(sink_dir, f"skill-capture-{_today()}.jsonl")

    stale = (datetime.now(timezone.utc) - timedelta(days=120)).timestamp()
    os.utime(live, (stale, stale))

    observability._prune_expired_sink_files(sink_dir, retention_days=90)

    assert live.exists(), (
        "a sink file named for TODAY was pruned because its MTIME was old "
        f"({live.name}) — the retention decision must follow the DATE IN "
        "THE NAME (the file's identity), never the mutable mtime"
    )


# ── P2 — the retention still works (the prune is not a no-op) ─────────────


def test_p2_a_file_whose_name_date_is_out_of_window_is_pruned(tmp_path):
    """P2 — the window is enforced against the name-date.

    Named mutation: make the prune a no-op → this pin REDs.
    """
    sink_dir = _sink_dir(tmp_path)
    old_day = (
        datetime.now(timezone.utc) - timedelta(days=200)
    ).strftime("%Y%m%d")
    stale = _write_sink_file(sink_dir, f"skill-capture-{old_day}.jsonl")

    observability._prune_expired_sink_files(sink_dir, retention_days=90)

    assert not stale.exists(), (
        f"{stale.name} is 200 days old (far outside the 90-day window) and "
        "must be pruned — the retention sweep must still delete"
    )


# ── P3 — the file the writer holds open is NEVER unlinked ────────────────


def test_p3_the_file_currently_held_open_is_never_unlinked(tmp_path):
    """P3 — the CURRENT file is excluded BY PATH.

    The shape: the writer's clock rolled back, so the file it just
    wrote carries a name-date that is already outside the window.  The
    prune must not unlink the file that writer holds open — unlinking
    it would send the writer's next rows into an unlinked inode.

    Named mutation: drop the prune's ``current_path`` guard → this pin
    REDs.
    """
    sink_dir = _sink_dir(tmp_path)
    current = _write_sink_file(
        sink_dir, "skill-capture-20200101.jsonl", '{"current":"row"}\n'
    )

    observability._prune_expired_sink_files(
        sink_dir,
        retention_days=90,
        current_path=current,
    )

    assert current.exists(), (
        "the file the writer holds open was unlinked by the prune — the "
        "current file must be excluded BY PATH (a clock rollback names "
        "the file with a past day)"
    )


# ── P4 — a file that is not ours is never touched ────────────────────────


def test_p4_a_neighbouring_file_in_the_sink_dir_is_never_touched(tmp_path):
    """P4 — the prune only ever touches OUR daily files.

    Two shapes of "not ours": a name that does not match the
    convention at all, and a name that matches the glob but does not
    encode a day (``skill-capture-notes.jsonl``).

    Named mutation: widen the prune to any file in the sink dir → this
    pin REDs.
    """
    sink_dir = _sink_dir(tmp_path)
    foreign = _write_sink_file(sink_dir, "someone-elses-file.jsonl")
    unparseable = _write_sink_file(sink_dir, "skill-capture-notes.jsonl")

    observability._prune_expired_sink_files(sink_dir, retention_days=90)

    assert foreign.exists(), (
        f"{foreign.name} is not one of our daily sink files and must never "
        "be deleted by the prune"
    )
    assert unparseable.exists(), (
        f"{unparseable.name} matches the glob but encodes no day — the prune "
        "must skip it (only `skill-capture-<YYYYMMDD>.jsonl` is ours)"
    )


# ── P5 — the PUBLIC write path keeps its own file and the neighbours ─────


def test_p5_the_public_append_keeps_its_own_file_and_the_neighbours(tmp_path):
    """P5 — end-to-end through :func:`append_run_record`.

    The append-triggered prune must (a) leave the file it just wrote
    alive and carrying the row, (b) leave a neighbour's file alone,
    and (c) still prune an out-of-window file of ours.
    """
    sink_dir = _sink_dir(tmp_path)
    marker = marker_for_run()
    foreign = _write_sink_file(sink_dir, "someone-elses-file.jsonl")
    old_day = (
        datetime.now(timezone.utc) - timedelta(days=200)
    ).strftime("%Y%m%d")
    stale = _write_sink_file(sink_dir, f"skill-capture-{old_day}.jsonl")

    written = observability.append_run_record(
        {"stage": "pin", "marker": marker},
    )

    assert written is not None and written.exists(), (
        "the file the append wrote must survive the prune it triggered"
    )
    assert marker in written.read_text(encoding="utf-8"), (
        "the appended row must still be in the file the append returned"
    )
    assert foreign.exists(), (
        "the append-triggered prune deleted a neighbour's file"
    )
    assert not stale.exists(), (
        "the append-triggered prune did not prune an out-of-window file"
    )


# ── P6 (house guard, static) — no lane test may DELETE ──────────────────


def test_p6_no_lane_test_file_carries_a_destructive_filesystem_call():
    """P6 — the class-proof: a lane test can never delete a shared file.

    The rule is deliberately STRICT: the lane's test files carry NO
    destructive filesystem call at all (``unlink`` / ``remove`` /
    ``rmtree`` / ``rmdir``).  A delete that IS provably scoped to the
    test's own scratch dir is harmless — but a static check cannot prove
    scoping, so the house rule is "no deletes", and an exception has to
    be argued explicitly.

    The lane's PACK scripts are held to the same rule, with ONE
    allowance: a delete of a path held by a variable assigned from
    ``mktemp`` (the pack's own scratch dir) is allowed; every other
    delete is refused.

    Named mutation: reintroduce a shared-path delete in a lane test
    file (or in the lane's pack) → this pin REDs.
    """
    offenders: list[str] = []

    for relative in LANE_TEST_FILES + LANE_SUPPORT_FILES:
        path = REPO_ROOT / relative
        assert path.exists(), f"lane test file missing: {relative}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                continue
            if name in _DESTRUCTIVE_CALLS:
                offenders.append(
                    f"{relative}:{node.lineno}: {name}() — a lane test may "
                    "not delete a file (the shared-path class)"
                )

    for relative in LANE_PACK_SCRIPTS:
        path = REPO_ROOT / relative
        assert path.exists(), f"lane pack script missing: {relative}"
        lines = path.read_text(encoding="utf-8").splitlines()
        scratch_vars = {
            match.group(1)
            for match in (_MKTEMP_ASSIGN.match(line) for line in lines)
            if match is not None
        }
        for lineno, raw in enumerate(lines, start=1):
            code = raw.split("#", 1)[0]
            if not _SHELL_DELETE.search(code):
                continue
            refs = set(_VAR_REF.findall(code))
            unproven = sorted(refs - scratch_vars)
            if refs and not unproven:
                continue  # the delete is scoped to a mktemp scratch dir
            offenders.append(
                f"{relative}:{lineno}: a lane pack may not run a delete of a "
                f"path it did not create from mktemp: {raw.strip()!r}"
            )

    assert not offenders, (
        "a lane test/pack carries a destructive filesystem call — the lane "
        "must never be able to delete a shared file:\n  "
        + "\n  ".join(offenders)
    )


# ── P7 (house guard, sentinel) — a lane write touches NO shared path ────


@pytest.mark.timeout(600)
def test_p7_the_lane_files_run_together_never_reach_the_real_sink_dir(tmp_path):
    """P7 — the CLASS-level sentinel: the lane's files, TOGETHER.

    THE REPRODUCTION THAT PROVED THE DEFECT: each of the lane's files
    on its OWN wrote 0 bytes into the checkout's real sink dir, but the
    SAME FILES RUN TOGETHER wrote thousands of bytes into it.  The leak
    was ORDER-DEPENDENT: a file's fixture teardown restored the sink
    override to ``None`` (= the checkout's real data dir), and a later
    file's test then wrote with no redirect in force.

    So this pin runs the lane's three affected files TOGETHER, in a
    SUBPROCESS, under the DEVELOPER's direct-pytest condition (the
    data-dir env knobs scrubbed — otherwise the pack-level sandbox would
    mask the very leak the pin looks for), and asserts that the
    checkout's real sink dir is untouched.

    The pin is not vacuous: the inner run is the leak's own reproduction,
    and the sentinel in the lane's fixtures (``lane_test_start`` /
    ``lane_test_end``) turns a rogue teardown into a FAILED test.

    Named mutation: restore the override to ``None`` in one of the lane
    files' teardowns → this pin (and the lane's own sentinel) REDs.
    """
    inner_sandbox = tmp_path / "inner-sandbox"
    inner_sandbox.mkdir()
    env = scrub_data_dir_env()
    # pytest's tmp_path base follows TMPDIR ⇒ the inner run's scratch
    # files stay observable under the sandbox.
    env["TMPDIR"] = str(inner_sandbox)

    before = sink_snapshot(REAL_SINK_DIR)
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/unit/test_skill_sink_retention_shared_path_pins.py",
            "tests/unit/test_skill_fix_burst_guard.py",
            "tests/services/test_skill_capture_observability.py",
            # no recursion: this pin does not run inside the inner run
            "--deselect",
            "tests/unit/test_skill_sink_retention_shared_path_pins.py::"
            "test_p7_the_lane_files_run_together_never_reach_the_real_sink_dir",
            "-q", "--tb=line",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=540,
    )
    assert completed.returncode == 0, (
        "the lane's files must pass when run TOGETHER — the combination "
        "is the leak's own reproduction, so a failure here is the "
        "order-dependent defect:\n"
        f"{completed.stdout[-4000:]}\n{completed.stderr[-2000:]}"
    )

    after = sink_snapshot(REAL_SINK_DIR)
    vanished = sorted(set(before) - set(after))
    assert not vanished, (
        f"running the lane's files together DELETED a shared sink file: "
        f"{vanished} — the checkout's real sink dir must be untouched"
    )
    shrunk = sorted(
        name for name in before
        if name in after and len(after[name]) < len(before[name])
    )
    assert not shrunk, (
        f"running the lane's files together SHRANK a shared sink file: "
        f"{shrunk} — a shrink is an incident (rows were destroyed)"
    )
    appeared = sorted(set(after) - set(before))
    for name in appeared:
        rows = [
            line for line in after[name].decode("utf-8", "ignore").splitlines()
            if line.strip()
        ]
        assert not (rows and all(
            any(marker in row for marker in LANE_ROW_MARKERS) for row in rows
        )), (
            f"running the lane's files together created {name} in the "
            "checkout's real sink dir, and every row in it is ours — the "
            "lane's sink write escaped its scratch dir (the order-dependent "
            "leak)"
        )
    for name, blob in after.items():
        text = blob.decode("utf-8", "ignore")
        hit = [marker for marker in LANE_ROW_MARKERS if marker in text]
        assert not hit, (
            f"a lane row landed in the checkout's real sink dir: {name} "
            f"(markers {hit}) — the lane's files, run together, must never "
            "reach the shared data dir"
        )


def test_p9_the_sentinel_catches_a_teardown_that_restores_none():
    """P9 — the guard BITES: a rogue teardown is caught, not tolerated.

    Two halves, both deterministic:

    * the observation helper refuses to pass while the override is
      unset (``None`` = the checkout's real data dir);
    * the NEXT lane test's start boundary fails when the previous lane
      test left the override unset — that is the FAILED-level detector
      for the order-dependent leak.
    """
    observability.configure_sink(None)
    try:
        with pytest.raises(AssertionError) as excinfo:
            assert_sink_never_reaches_real_dir(None)
        assert "override" in str(excinfo.value), (
            f"the guard must name the override: {excinfo.value}"
        )
    finally:
        restore_safe_sink_redirect()

    # The rogue state is RECORDED by the end boundary (the state the
    # next lane test will look at) …
    observability.configure_sink(None)
    try:
        with pytest.raises(AssertionError):
            lane_test_end(
                "tests/unit/test_skill_sink_retention_shared_path_pins.py::"
                "p9_simulated_rogue_teardown",
                None,
            )
    finally:
        restore_safe_sink_redirect()

    # … and the NEXT lane test's start boundary fails on it.
    with pytest.raises(AssertionError) as excinfo:
        lane_test_start(
            "tests/unit/test_skill_sink_retention_shared_path_pins.py::"
            "p9_the_next_lane_test"
        )
    assert "real data dir" in str(excinfo.value) or "override" in str(
        excinfo.value
    ), f"the guard must explain itself: {excinfo.value}"


# ── P8 (house guard) — the cleanup is ROW-GRANULAR, never file-level ────


def test_p8_the_row_granular_cleanup_keeps_the_file_and_the_foreign_rows(tmp_path):
    """P8 — the cleanup removes the MARKED ROWS and nothing else.

    The house shape (F-G1): read → filter out the rows carrying this
    run's marker → atomic rewrite.  The FILE is never unlinked, and a
    foreign row (another lane's evidence) is never dropped.

    Named mutation: make the cleanup delete the whole file (the pre-fix
    shape) → this pin REDs.
    """
    marker = marker_for_run()
    sink_dir = _sink_dir(tmp_path)
    shared = _write_sink_file(
        sink_dir,
        f"skill-capture-{_today()}.jsonl",
        json_line({"lane": "other", "row": "keep-me"})
        + json_line({"lane": "pin", "marker": marker})
        + json_line({"lane": "other", "row": "keep-me-too"}),
    )

    removed = remove_rows_carrying(shared, marker)

    assert removed == 1, (
        f"the cleanup removed {removed} rows — it must remove EXACTLY the "
        "row carrying this run's marker"
    )
    assert shared.exists(), (
        "the cleanup DELETED the shared sink file — the daily file is "
        "shared by every lane and must survive (the F-G1 lesson)"
    )
    text = shared.read_text(encoding="utf-8")
    assert marker not in text, "the marked row survived the cleanup"
    assert "keep-me" in text and "keep-me-too" in text, (
        "the cleanup dropped a FOREIGN row — another lane's evidence must "
        "never be destroyed"
    )
