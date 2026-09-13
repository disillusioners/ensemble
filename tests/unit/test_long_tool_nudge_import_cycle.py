"""Regression test: cold-import ``daemon.graph`` in a fresh interpreter.

The long-tool-nudge feature shipped a module-level import in
``daemon/graph.py:5-8`` that creates a latent circular-import wedge:

    daemon/graph.py → daemon.services.long_tool_nudge
                    → daemon/services/__init__.py
                    → daemon.services.child_reports
                    → daemon.graph (still partially initialized)
                    → ImportError on ``ThinkingChatOpenAI``.

The wedge fires ONLY when ``daemon.graph`` is the FIRST daemon module
loaded into a fresh Python interpreter. The daemon's normal boot path
imports ``daemon.config`` / ``daemon.persistence`` first, which never
trips the cycle — so realistic boots are safe. Realistic boots safe is
NOT realistic proofs safe: any cold-import entry point (a test that
``import daemon.graph``, an embedded REPL, a debugger session) crashes.

This test pins the cold-import contract via a real subprocess —
pytest's own ``tests/conftest.py`` pre-installs mock langgraph modules
and pre-imports many daemon modules, so the bug is invisible from
inside the pytest process. The subprocess spawns a fresh interpreter,
scrubs ``PYTHONPATH`` / ambient daemon state from the env, and just
runs ``import daemon.graph`` — if it returns non-zero or writes
``ImportError`` to stderr, the cycle wedge is back.

D-1 surgical fix pinning (post-merge): the import in ``daemon/graph.py``
must be deferred (function-local in the single use site) so the module
loads cleanly even when it is the first daemon module the interpreter
sees. The behavioral contract (same call shape, same singleton) is
unchanged; only the resolution timing moves.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _cold_import_env(workdir: Path) -> dict[str, str]:
    """Build a deterministic, contamination-free env for the cold-import subprocess.

    Stripped: ``PYTHONPATH`` (would inject an ambient check-out's daemon
    ahead of the worktree's), ``ENSEMBLE_*`` (would force a real
    config load and potentially mask the import cycle behind a
    different failure). Kept: ``PATH`` (the interpreter needs to
    locate shared libs), ``HOME`` (any tool that touches ``~/.config``
    on import), ``LANG`` (avoids locale warnings). No
    ``ENSEMBLE_*`` knob needs to be ON for this test — we just want
    the bare module import to resolve.
    """
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


def _run_cold_daemon_graph_import(workdir: Path) -> subprocess.CompletedProcess:
    """Spawn a fresh interpreter that imports ``daemon.graph`` as the
    FIRST daemon module, with no ambient daemon state in the env.

    Returns the completed subprocess. Caller asserts on returncode /
    stderr.
    """
    return subprocess.run(
        [sys.executable, "-c", "import daemon.graph"],
        cwd=str(workdir),
        env=_cold_import_env(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cold_import_daemon_graph_does_not_hit_cycle(tmp_path: Path) -> None:
    """Cold ``import daemon.graph`` returns 0 with no ``ImportError``.

    The wedge is a partial-init cycle through
    ``daemon.services.child_reports``; pre-fix this subprocess returns
    ``returncode=1`` and ``ImportError: cannot import name
    'ThinkingChatOpenAI' from partially initialized module
    'daemon.graph'``. Post-fix the cycle no longer fires at
    ``daemon.graph`` import time.
    """
    workdir = Path(__file__).resolve().parents[2]  # worktree root
    result = _run_cold_daemon_graph_import(workdir)

    if result.returncode != 0:
        pytest.fail(
            "cold `import daemon.graph` failed with returncode="
            f"{result.returncode}\n--- stderr ---\n{result.stderr}"
            "\n--- stdout ---\n" + result.stdout
        )

    # Belt-and-suspenders: returncode can be 0 even if a side-channel
    # emitted the cycle; explicitly forbid ImportError in stderr.
    assert "ImportError" not in result.stderr, (
        "ImportError present in cold-import stderr (cycle wedge?):\n"
        f"{result.stderr}"
    )


def test_cold_import_daemon_graph_through_pkg_warmup(tmp_path: Path) -> None:
    """Same cold import, but after a warm-up that pre-loads ``daemon``
    as an empty namespace — guards against a future change that
    accidentally routes ``daemon.graph`` through a different cycle
    edge.

    Specifically: this catches the latent case where someone adds a
    sibling module-level import that doesn't go through
    ``daemon.services.__init__`` but still creates a partial-init
    cycle. The warm-up deliberately does NOT import any daemon
    submodule, so the next ``import daemon.graph`` is still the
    first daemon submodule the interpreter sees.
    """
    workdir = Path(__file__).resolve().parents[2]
    code = (
        "import daemon\n"
        # Sanity: the package import must NOT have populated graph yet.
        # We don't assert that strictly (it depends on __init__.py
        # content), but we do require that the next import is the
        # one that pulls graph in for the first time.
        "import daemon.graph\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(workdir),
        env=_cold_import_env(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        pytest.fail(
            "warm-then-cold `import daemon.graph` failed:\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )
    assert "ImportError" not in result.stderr, (
        "ImportError present in warm-cold stderr (cycle wedge?):\n"
        f"{result.stderr}"
    )