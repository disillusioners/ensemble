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
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from daemon.services import skill_capture_observability as observability

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

#: The lane's PACK scripts (the static house-guard scans these too).
LANE_PACK_SCRIPTS = (
    "test/packs/skill_capture_observability_unit_test.sh",
    "test/packs/skill_evolution_unit_test.sh",
    "test/packs/skill_stale_deactivation_unit_test.sh",
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
def _sink_sandbox(tmp_path):
    """Point the sink at a SCRATCH dir for every test in this file."""
    observability.configure_sink(tmp_path)
    try:
        yield tmp_path
    finally:
        observability.configure_sink(None)


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

    for relative in LANE_TEST_FILES:
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


def test_p7_a_lane_write_leaves_the_real_sink_dir_byte_identical(tmp_path):
    """P7 — the sentinel: the shared sink dir is untouched by a lane write.

    The house rule (F-G1 lesson): the shared sink is EVIDENCE — snapshot
    the bytes before the run, diff after, and treat any shrink as an
    incident.  A clean END state can be the residue of destruction, not
    cleanliness.

    The pin asserts all three of: no file vanished, no file shrank, and
    no row carrying this run's marker landed in the shared dir.
    """
    marker = marker_for_run()
    before = sink_snapshot(REAL_SINK_DIR)

    # The lane's own write — through the PUBLIC path.
    written = observability.append_run_record(
        {"stage": "pin", "marker": marker},
    )

    resolved = observability.resolve_sink_dir()
    assert str(resolved).startswith(str(tmp_path)), (
        f"the sink resolved to {resolved} — the lane's write must go to the "
        "test's scratch dir, never to a shared path"
    )
    assert written is not None and written.exists()

    after = sink_snapshot(REAL_SINK_DIR)

    vanished = sorted(set(before) - set(after))
    assert not vanished, (
        f"a lane write DELETED a shared sink file: {vanished} — the real "
        "sink dir must be byte-identical after a lane write"
    )
    shrunk = sorted(
        name for name in before
        if name in after and len(after[name]) < len(before[name])
    )
    assert not shrunk, (
        f"a shared sink file SHRANK across a lane write: {shrunk} — a shrink "
        "is an incident (rows were destroyed), not a cleanup"
    )
    leaked = sorted(
        name for name, blob in after.items()
        if marker.encode("utf-8") in blob
    )
    assert not leaked, (
        f"a lane write landed a row in the REAL sink dir: {leaked} — the "
        "sink must be redirected to the test's scratch dir"
    )


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
