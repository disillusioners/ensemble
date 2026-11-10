"""The lane's SAFE sink redirect + the shared-path SENTINEL.

THE DEFECT THIS MODULE EXISTS FOR (the order-dependent sink leak)
-----------------------------------------------------------------
The capture-lane sink resolves its data dir from the PROCESS-GLOBAL
override installed by ``observability.configure_sink(...)``.  A lane test
file's fixture used to install its own per-test redirect and then
RESTORE THE OVERRIDE TO ``None`` in the teardown — and ``None`` means
"the resolved default", i.e. the CHECKOUT's real ``data/`` tree.

That left a WINDOW in which the override pointed at the REAL data dir:
between two tests, and between two test FILES.  Any write in that window
— a background task finishing late, a later module's test, a stray call
— landed in the checkout's shared daily sink file.  The leak is
ORDER-DEPENDENT (it depends on which file's teardown ran last), which is
why a per-file check could not see it.

THE RULE
--------
**A lane test may never leave the sink override pointing at the real
data dir.**  The restore must land on a SAFE value — a scratch dir —
never on ``None``.  This module owns that safe value: ONE process-wide
session scratch dir, shared by every lane file, so the override is
NEVER ``None`` between tests and NEVER points at the checkout.

THE SENTINEL
------------
:func:`assert_sink_never_reaches_real_dir` is the class-proof.  It
combines the DECISIVE, race-free invariant (the override in force is
never ``None`` and the resolved sink dir is never the checkout's real
data dir) with the OBSERVATION of the real sink dir (no file vanished,
none shrank, and no lane row landed there).

RACE TOLERANCE (why the observation is marker-based, not byte-identity)
-----------------------------------------------------------------------
The checkout's ``data/`` tree is SHARED with every other lane working in
this worktree, so a global "the real dir is byte-identical across the
run" assertion is racy against a concurrent lane's test run — the house
recorded that lesson in
``.agents/tester/LESSONS/2026-11-10-pack-cleanup-row-granularity.md``
and the sibling lane's leak pin is built on the same principle ("look
for YOUR OWN marker, not for global equality").  The sentinel therefore
asserts:

* the DECISIVE invariant (override never ``None``; the resolved sink dir
  is never the real data dir) — race-free, and it is what catches a
  rogue teardown;
* no shared file VANISHED or SHRANK (destruction is never legitimate);
* no file APPEARED whose every row is ours;
* no row carrying a LANE MARKER is present in the real dir.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from daemon.services import skill_capture_observability as observability

#: The repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The checkout's REAL sink dir — the shared path under audit.
REAL_SINK_DIR = REPO_ROOT / "data" / observability.SINK_DIR_NAME

#: The observability canary of ``tests/services/test_skill_capture_observability.py``
#: — its sha256 PREFIX is what a leaked row would carry (the raw text
#: must never reach the sink at all — DS-18).
_OBSERVABILITY_CANARY = "CANARY-RAW-PROMPT-TEXT-4f2b9c-do-not-store"

#: The markers a row of THIS lane carries.  A row carrying one of these
#: in the real sink dir is a LEAK.
LANE_ROW_MARKERS: tuple[str, ...] = (
    # the burst-guard file's skill ids
    "skill-aaaa1111",
    "skill-bbbb2222",
    # the L7 pin's per-run skill ids
    "skill-l7-",
    # the sink-retention pins' per-run markers
    "skill-sink-pin-",
    # the wave-2 tools seam's prompt source
    "skill_execute_capture",
    # the observability canary's sha256 prefix
    hashlib.sha256(_OBSERVABILITY_CANARY.encode("utf-8")).hexdigest()[:16],
)

#: The process-wide session scratch dir (created lazily, ONCE).
_SESSION_DIR: Path | None = None

#: The state the previous lane test left behind:
#: ``(nodeid, override_was_safe)`` or ``None`` (no lane test yet).
_LAST_LANE_STATE: tuple[str, bool] | None = None


def session_sink_dir() -> Path:
    """Return the PROCESS-WIDE session scratch dir for the sink.

    One dir per process, shared by every lane file, so the sink
    override is never ``None`` between tests.  It lives OUTSIDE the
    checkout (``/tmp``) — it is never a shared path.
    """
    global _SESSION_DIR
    if _SESSION_DIR is None:
        _SESSION_DIR = Path(
            tempfile.mkdtemp(prefix="skill-capture-sink-session-")
        )
    return _SESSION_DIR


def restore_safe_sink_redirect() -> Path:
    """Point the sink at the session scratch dir — NEVER at the real dir.

    THE FIX for the order-dependent leak: a fixture teardown must land
    on a SAFE value.  ``configure_sink(None)`` means "the resolved
    default" = the checkout's real ``data/`` tree, so it is the one
    value a lane teardown may never restore.
    """
    target = session_sink_dir()
    observability.configure_sink(target)
    return target


def lane_test_start(nodeid: str) -> None:
    """The START boundary of a lane test — BEFORE the file's redirect.

    Asserts that the PREVIOUS lane test left the override in force.  A
    teardown that restores ``None`` therefore makes the NEXT lane test
    FAIL HERE — a deterministic, FAILED-level detector for the
    order-dependent leak (the "a later file's test writes with no
    redirect in force" window).

    A foreign lane's teardown is tolerated: the assertion only fires
    when the PREVIOUS test was one of THIS lane's tests.
    """
    global _LAST_LANE_STATE
    previous = _LAST_LANE_STATE
    _LAST_LANE_STATE = None
    if previous is None:
        return
    previous_id, previous_safe = previous
    assert previous_safe, (
        f"the previous lane test ({previous_id}) left the sink override "
        "unset — the CHECKOUT's real data dir was re-exposed for every "
        "write that followed. A lane fixture teardown must restore a "
        "SAFE value (restore_safe_sink_redirect()), never None."
    )


def lane_test_end(nodeid: str, before: dict[str, bytes] | None = None) -> None:
    """The END boundary of a lane test — AFTER the file's restore.

    Records the state the test left behind (for :func:`lane_test_start`)
    and runs the sentinel's observation half.
    """
    global _LAST_LANE_STATE
    safe = observability.sink_override() is not None
    _LAST_LANE_STATE = (nodeid, safe)
    assert_sink_never_reaches_real_dir(before)


#: NO import-time install.  The redirect is installed by the lane's
#: FIXTURES (setup) and restored by them (teardown, to the session
#: scratch dir — never to ``None``).  An import-time install would set
#: the process-global override for EVERY test in the session and would
#: therefore SHADOW the ``ENSEMBLE_DATA_DIR`` knob (the override wins
#: over the env var), breaking any test that redirects the sink through
#: the environment.  The lane's fixtures are the right owner: they
#: cover the whole affected surface and nobody else's.


def snapshot_real_sink_dir() -> dict[str, bytes]:
    """Snapshot the real sink dir's files (name -> bytes)."""
    if not REAL_SINK_DIR.is_dir():
        return {}
    out: dict[str, bytes] = {}
    for path in sorted(REAL_SINK_DIR.glob("*.jsonl")):
        try:
            out[path.name] = path.read_bytes()
        except OSError:  # pragma: no cover — a racing prune
            continue
    return out


def _is_under(path: Path, parent: Path) -> bool:
    """True when ``path`` is ``parent`` or lives under it."""
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def assert_sink_never_reaches_real_dir(
    before: dict[str, bytes] | None = None,
) -> None:
    """THE SENTINEL — the sink must never point at (or reach) the real dir.

    Args:
        before: The real sink dir's snapshot taken BEFORE the test (the
            observation baseline).  ``None`` → take it now (the
            observation half is then skipped).

    Raises:
        AssertionError: when the override was reset to the real dir, or
            when the real sink dir changed in a way only a leak or a
            destruction could explain.
    """
    # (1) THE DECISIVE INVARIANT — race-free, and it is what catches a
    # rogue teardown that restores the override to ``None``.
    override = observability.sink_override()
    assert override is not None, (
        "the sink override was reset to None — that re-exposes the "
        "CHECKOUT's real data dir for every later write (the "
        "order-dependent sink leak). A lane fixture teardown must "
        "restore a SAFE value (restore_safe_sink_redirect()), never None."
    )
    resolved = observability.resolve_sink_dir()
    assert not _is_under(resolved, REAL_SINK_DIR), (
        f"the sink resolved into the checkout's real sink dir: {resolved}"
    )

    if before is None:
        return

    # (2) THE OBSERVATION — the real sink dir is shared EVIDENCE.
    after = snapshot_real_sink_dir()
    vanished = sorted(set(before) - set(after))
    assert not vanished, (
        f"a shared sink file VANISHED: {vanished} — the checkout's real "
        f"sink dir ({REAL_SINK_DIR}) must never lose a file"
    )
    shrunk = sorted(
        name for name in before
        if name in after and len(after[name]) < len(before[name])
    )
    assert not shrunk, (
        f"a shared sink file SHRANK: {shrunk} — a shrink is an incident "
        "(rows were destroyed), not a cleanup"
    )
    appeared = sorted(set(after) - set(before))
    for name in appeared:
        rows = [
            line for line in after[name].decode("utf-8", "ignore").splitlines()
            if line.strip()
        ]
        if rows and all(
            any(marker in row for marker in LANE_ROW_MARKERS) for row in rows
        ):
            raise AssertionError(
                f"a file appeared in the checkout's real sink dir whose "
                f"every row is ours: {name} — the lane's sink write "
                "escaped its scratch dir"
            )
    for name, blob in after.items():
        text = blob.decode("utf-8", "ignore")
        hit = [marker for marker in LANE_ROW_MARKERS if marker in text]
        assert not hit, (
            f"a lane row landed in the checkout's real sink dir: {name} "
            f"(markers {hit}) — the sink must be redirected to a scratch dir"
        )


def scrub_data_dir_env() -> dict[str, str]:
    """An env copy with the data-dir knobs REMOVED (per-variable).

    The sink resolves ``ENSEMBLE_DATA_DIR`` > ``DATA_DIR`` > ``./data``.
    A subprocess that must reproduce the DEVELOPER's direct-pytest
    condition has to lose BOTH knobs — otherwise the pack-level sandbox
    would mask the leak the probe is looking for.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("ENSEMBLE_DATA_DIR", "DATA_DIR")
    }
    return env
