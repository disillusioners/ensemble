"""The F0.3 GATE VERDICT — the WS-A ancestry check (AM-24).

The catalog's F0.3 exit gate BLOCKS unless the resource-monitor flusher
fix (``feature/resource-monitor-flusher-fix@53547b915``) is in the
lane's ancestry.  The check is by **ANCESTRY**
(``git merge-base --is-ancestor``), never by memory.

THE RC SEMANTICS (verbatim from the plan)
------------------------------------------
* ``rc == 0`` ⇒ **PASS** ⇒ F0.3 ships and its family pack runs;
* ``rc == 1`` ⇒ **DEFER** ⇒ F0.3 leaves the cluster, the lane's series
  delta drops by 8, and the deferral is DISCLOSED;
* any other ``rc`` ⇒ **STOP** ⇒ the lane halts and reports — an
  unexpected rc is an UNKNOWN state, not a verdict.

``rc == 1`` MUST NOT be treated as a PASS (the P4-M13 mutation).
"""

from __future__ import annotations

import logging
import subprocess
from typing import Callable

logger = logging.getLogger(__name__)

#: The WS-A commit the F0.3 gate names.
WS_A_COMMIT = "53547b915"

#: The verdict constants.
VERDICT_PASS = "PASS"
VERDICT_DEFER = "DEFER"
VERDICT_STOP = "STOP"


def _default_runner(argv: list[str]) -> int:
    """Run the ancestry probe and return its exit code."""
    try:
        completed = subprocess.run(  # noqa: S603 — a fixed argv, no shell
            argv,
            capture_output=True,
            check=False,
        )
    except OSError:  # pragma: no cover — a missing git ⇒ an unknown state
        logger.warning("metrics wiring: the git ancestry probe failed to run")
        return 128
    return int(completed.returncode)


def ws_a_ancestry_verdict(
    *,
    repo: str = ".",
    commit: str = WS_A_COMMIT,
    runner: Callable[[list[str]], int] | None = None,
) -> tuple[str, int]:
    """The F0.3 gate verdict — ``(verdict, rc)``.

    ``runner`` is the injection seam (the pack drives it with a fake rc
    to prove the rc semantics; production uses ``git``).
    """
    probe = runner or _default_runner
    rc = probe(["git", "merge-base", "--is-ancestor", commit, repo])
    if rc == 0:
        return VERDICT_PASS, rc
    if rc == 1:
        return VERDICT_DEFER, rc
    return VERDICT_STOP, rc


__all__ = [
    "VERDICT_DEFER",
    "VERDICT_PASS",
    "VERDICT_STOP",
    "WS_A_COMMIT",
    "ws_a_ancestry_verdict",
]
