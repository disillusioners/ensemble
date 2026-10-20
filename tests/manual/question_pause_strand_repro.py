#!/usr/bin/env python
"""STANDALONE symptom repro — question pause/resume strand (RED/GREEN).

Feature: ``fix/question-pause-resume-asymmetry``.

This script is the REPEATABLE SYMPTOM REPRO the fix must flip. It is
deliberately written so that it runs against BOTH the pre-fix and the
post-fix source trees:

* it imports NOTHING from the new modules
  (``daemon.services.question_pause_set`` /
  ``daemon.services.stale_pause_sweep``) — the harness it borrows from
  the test module imports them only INSIDE test bodies, so the import
  graph stays clean on the pre-fix tree (no trivial ImportError "red");
* it drives the REAL production paths: the real ``QuestionManager``,
  the real ``InstanceLifecycleService`` pause cascade and the real
  shared answer helper (``daemon/routers/answer_helper.py``).

WHAT IT SHOWS
-------------
The live shape (2026-10-19: f86c4015 / 756d37ba — both needed a
manual resume):

  1. an asker calls ``ask_questions`` → the deferred question pause
     parks the asker's WHOLE tree (the sibling ``side-1`` is parked as
     a SIDE EFFECT and owns no suspension handle);
  2. the user answers;
  3. SYMPTOM: the side-effect target's interrupted turn is never
     re-armed — the tree-scoped resume flips its STATUS only, and a
     task-less instance has no handle to resume. The child can never
     report terminal, so the parent can never leave
     ``waiting_children``.

EXIT CODES
----------
  0 — the symptom is ABSENT (the fix works — GREEN)
  1 — the symptom is PRESENT (the strand — RED)
  2 — the harness could not run (an environment problem)

Usage::

    python tests/manual/question_pause_strand_repro.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
# The repo root is two levels up from tests/manual/.
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlmodel import Session  # noqa: E402

from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: E402
from daemon.services.question_manager import QuestionManager  # noqa: E402
from tests.unit.test_question_pause_resume_asymmetry import (  # noqa: E402
    _FakeManager,
    _ask,
    _answer,
    _paused_at,
    _seed_tree,
    _status,
    _woken_ids,
)


def _report(engine, manager) -> bool:
    """Print the evidence; return True when the STRAND is present."""
    woken = _woken_ids(manager)
    side_status = _status(engine, "side-1")
    root_status = _status(engine, "root-1")

    print("=" * 72)
    print("QUESTION PAUSE STRAND — symptom repro")
    print("=" * 72)
    print(f"side-1 (side-effect target) status : {side_status}")
    print(f"side-1 paused_at                   : {_paused_at(engine, 'side-1')}")
    print(f"root-1 (parent) status             : {root_status}")
    print(f"re-armed (woken) instances         : {woken}")
    print("-" * 72)

    stranded = "side-1" not in woken
    if stranded:
        print(
            "RESULT: STRAND PRESENT — the side-effect target's "
            "interrupted turn was never re-armed; it can never report "
            "terminal, so the parent stays in waiting_children forever."
        )
    else:
        print(
            "RESULT: NO STRAND — the side-effect target was re-armed; "
            "its turn can complete and the parent can leave "
            "waiting_children."
        )
    print("=" * 72)
    return stranded


async def _main() -> int:
    with tempfile.TemporaryDirectory(prefix="qpause-repro-") as tmpdir:
        engine, manager = _build(tmpdir)
        _seed_tree(engine)
        _ask(manager)

        # The deferred question pause (the production call site).
        pause_result = await manager.pause_instance_cascade(
            "asker-1", suspension_reason="awaiting_answer"
        )
        print(
            "pause cascade parked: "
            f"{sorted(pause_result.get('paused_ids', []))}"
        )
        print(f"side-1 parked at   : {_status(engine, 'side-1')}")
        print("-" * 72)

        # The user answers.
        try:
            answer_result = await _answer(manager)
        except Exception as exc:  # noqa: BLE001
            print(f"answer raised      : {type(exc).__name__}: {exc}")
        else:
            print(f"answer status      : {answer_result.get('status')}")
        print("-" * 72)

        stranded = _report(engine, manager)
        with tempfile.TemporaryDirectory(prefix="qpause-repro-b-") as tmpdir_b:
            late_parked = await _late_pause_shape(tmpdir_b)
        return 1 if (stranded or late_parked) else 0


async def _late_pause_shape(tmpdir: str) -> bool:
    """Shape B — the LATE-PAUSE race.

    The deferred question pause runs OUTSIDE the graph task, so it can
    land AFTER the answer was consumed. Pre-fix that parks the whole
    tree with no question to answer: the child REMAINS paused and the
    parent is parked with it — a strand with no answering counterpart.

    Returns True when the tree was parked by the late pause.
    """
    print()
    print("=" * 72)
    print("SHAPE B — late-pause race (the pause lands AFTER the answer)")
    print("=" * 72)
    engine, manager = _build(tmpdir)
    _seed_tree(engine)
    _ask(manager)

    # The answer lands FIRST.
    try:
        answer_result = await _answer(manager)
        print(f"answer status      : {answer_result.get('status')}")
    except Exception as exc:  # noqa: BLE001
        print(f"answer raised      : {type(exc).__name__}: {exc}")

    # The deferred pause fires afterwards.
    pause_result = await manager.pause_instance_cascade(
        "asker-1", suspension_reason="awaiting_answer"
    )
    refused = bool(pause_result.get("question_pause_refused"))
    print(f"late pause refused : {refused}")
    print(f"side-1 status      : {_status(engine, 'side-1')}")
    print(f"root-1 status      : {_status(engine, 'root-1')}")
    print("-" * 72)
    if refused:
        print(
            "RESULT: NO STRAND — the late pause was refused; the tree "
            "was not parked with no answering counterpart."
        )
        return False
    print(
        "RESULT: STRAND PRESENT — the late pause parked the tree after "
        "the question was consumed; the child REMAINS paused with no "
        "answering counterpart."
    )
    return True


def _build(tmpdir: str):
    """Build the engine + the harness manager (SQLite, in a temp dir)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    engine = create_engine(
        f"sqlite:///{tmpdir}/strand_repro.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine, _FakeManager(engine)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(_main()))
    except Exception as exc:  # noqa: BLE001
        print(f"HARNESS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
