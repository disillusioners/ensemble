"""Run-id shape pin for the maintenance-reports read door (BR-17).

The run id is a FILENAME COMPONENT, never a path — every id is matched
against ``RUN_ID_PATTERN`` before it is used to build a filename under
the fixed artifact root. The anchor is ``\\Z`` (NOT ``$``): Python's ``$``
also matches BEFORE a trailing newline, so a run id carrying one would
pass the shape check and reach the path builder.

THIS FILE PINS
--------------
1. the canonical id still matches (non-vacuity — a pattern that matches
   NOTHING would satisfy every "must be rejected" assertion below),
2. the REGRESSION property: an id carrying a TRAILING NEWLINE is
   REJECTED. A ``$``-anchored pattern would ACCEPT it (Python's ``$``
   matches before a final newline) — the danger shape is asserted
   explicitly so this pin cannot silently go vacuous,
3. the negative-shape neighbours (wrong hex length, wrong separator,
   missing prefix, uppercase hex, wrong digit counts, non-hex char),
4. the ROUTER-LEVEL behaviour: the application site
   (``daemon.routers.maintenance_reports._collect_runs``) ignores an
   artifact whose run id carries a trailing newline — the
   implementation-agnostic leg of the same property.

Application method — the router applies the pattern with
``RUN_ID_PATTERN.match(run_id)`` (``daemon/routers/maintenance_reports.py``,
inside ``_collect_runs``). Every assertion below uses that SAME method.
``re.fullmatch`` would be equally strict and equally acceptable; the
pin is on the OBSERVED BEHAVIOUR (a trailing newline is rejected), not
on the pattern's source text.
"""

from __future__ import annotations

import re

import pytest

from daemon.routers.maintenance_reports import RUN_ID_PATTERN, _collect_runs

#: The canonical run id — the shape the door accepts.
CANONICAL_RUN_ID = "ckpt-20260101_000000000000-a1b2c3d4"

#: The regression shape — the canonical id with a TRAILING NEWLINE.
#: ``$`` accepts this; ``\Z`` (and ``fullmatch``) reject it.
TRAILING_NEWLINE_RUN_ID = "ckpt-20260101_000000000000-a1b2c3d4\n"

#: The DANGER pattern — what a ``$``-anchored regression would look
#: like. Asserted so the pin below is demonstrably load-bearing
#: (this one DOES accept the trailing-newline id).
DOLLAR_ANCHORED = re.compile(r"^ckpt-\d{8}_\d{12}-[0-9a-f]{8}$")


def _write_artifact(root, filename: str, run_id: str) -> None:
    """Write one maintenance artifact with a parseable header block."""
    (root / filename).write_text(
        "run_id: {run_id}\n"
        "trigger_type: scheduled\n"
        "started_at: 2026-01-01T00:00:00Z\n"
        "ended_at: 2026-01-01T00:01:00Z\n"
        "overall_status: ok\n"
        "\n"
        "lanes:\n"
        "  - lane: demo\n"
        "    status: ok\n".format(run_id=run_id),
        encoding="utf-8",
    )


# ── 1. positive / non-vacuity ───────────────────────────────────────────


def test_canonical_run_id_matches_the_pattern():
    """The canonical id matches — the non-vacuity anchor for this file."""
    assert RUN_ID_PATTERN.match(CANONICAL_RUN_ID) is not None


def test_canonical_run_id_matches_under_the_same_application_method():
    """The positive form matches under the router's OWN application call.

    Same method as the router's application site (``RUN_ID_PATTERN.match``)
    — so the negatives below can never pass by testing a different,
    stricter call than production uses.
    """
    assert RUN_ID_PATTERN.match(CANONICAL_RUN_ID) is not None
    assert RUN_ID_PATTERN.match(TRAILING_NEWLINE_RUN_ID) is None


# ── 2. THE REGRESSION PIN — trailing newline ────────────────────────────


def test_trailing_newline_run_id_is_rejected():
    """A run id carrying a TRAILING NEWLINE must be REJECTED.

    Python's ``$`` matches BEFORE a trailing newline, so a ``$``-anchored
    pattern would let this id through to the path builder. ``\\Z`` (and
    ``re.fullmatch``) reject it.
    """
    assert RUN_ID_PATTERN.match(TRAILING_NEWLINE_RUN_ID) is None


def test_the_trailing_newline_pin_is_load_bearing():
    """Prove the pin bites: a ``$``-anchored pattern ACCEPTS the id.

    If the production pattern were ever regressed to a ``$`` anchor, this
    assertion is what fails first — a ``$``-anchored pattern accepts the
    trailing-newline id, the production one must not.
    """
    assert DOLLAR_ANCHORED.match(TRAILING_NEWLINE_RUN_ID) is not None
    assert RUN_ID_PATTERN.match(TRAILING_NEWLINE_RUN_ID) is None


# ── 3. negative-shape neighbours ─────────────────────────────────────────


@pytest.mark.parametrize(
    "run_id",
    [
        pytest.param("ckpt-20260101_000000000000-a1b2c3d", id="hex-too-short"),
        pytest.param("ckpt-20260101_000000000000-a1b2c3d4f", id="hex-too-long"),
        pytest.param(
            "ckpt-20260101-000000000000-a1b2c3d4", id="wrong-separator"
        ),
        pytest.param("20260101_000000000000-a1b2c3d4", id="missing-prefix"),
        pytest.param("CKPT-20260101_000000000000-a1b2c3d4", id="upper-prefix"),
        pytest.param(
            "ckpt-20260101_000000000000-A1B2C3D4", id="upper-hex"
        ),
        pytest.param("ckpt-202601_000000000000-a1b2c3d4", id="date-too-short"),
        pytest.param(
            "ckpt-20260101_00000000000-a1b2c3d4", id="time-too-short"
        ),
        pytest.param(
            "ckpt-20260101_000000000000-a1b2c3g4", id="non-hex-char"
        ),
        pytest.param(
            "ckpt-20260101_000000000000-a1b2c3d4 ", id="trailing-space"
        ),
    ],
)
def test_negative_shape_run_ids_are_rejected(run_id: str):
    """Neighbouring shapes are rejected — under the router's own method."""
    assert RUN_ID_PATTERN.match(run_id) is None


# ── 4. ROUTER-LEVEL behaviour (implementation-agnostic) ──────────────────


def test_collect_runs_ignores_an_artifact_whose_run_id_carries_a_newline(
    tmp_path,
):
    """The application site ignores the trailing-newline artifact.

    This is the implementation-agnostic leg: whatever call the router
    uses (``match``/``fullmatch``), a run id carrying a trailing newline
    must never become a listed run.
    """
    root = tmp_path / "RESULTS"
    root.mkdir()
    _write_artifact(
        root, f"run-{CANONICAL_RUN_ID}.md", CANONICAL_RUN_ID
    )
    _write_artifact(
        root,
        f"run-{TRAILING_NEWLINE_RUN_ID}.md",
        TRAILING_NEWLINE_RUN_ID,
    )

    rows = _collect_runs(root)

    # Non-vacuity: the CANONICAL artifact IS listed.
    assert [row["run_id"] for row in rows] == [CANONICAL_RUN_ID]
    assert rows[0]["overall_status"] == "ok"
    assert rows[0]["trigger_type"] == "scheduled"
