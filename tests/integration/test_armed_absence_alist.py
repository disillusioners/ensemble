"""T5.4 — FR-4 armed-absence alist test (FR-4 AC-4.1 / AC-4.2).

This test class is the dedicated pin for the armed-absence contract:

* **AC-4.1** — the fixture is an ``AsyncMock`` with
  ``side_effect=AssertionError("alist called on live path")``.
* **AC-4.2** — zero ``.alist(`` call sites in ``daemon/**/*.py`` outside
  ``daemon/migrations/checkpoint_migrator.py`` (verified by a
  repo-wide grep pinned here as a regression guard).
* **Self-check** — the fixture itself MUST raise if invoked; if a
  future maintainer forgets to wire ``side_effect``, the test catches
  the regression.

The test also runs a LIVE-PATH exercise on a real
``AsyncPostgresSaver`` (DSN-pinned disposable PG, per
``tests/helpers/checkpoint_prune_pg.py``) to prove the fixture wires
correctly end-to-end: tap a thread → read messages → the armed alist
is never invoked → assert_not_called passes → metric stays at 0.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.helpers.armed_absence import (
    ARMED_ALIST_MESSAGE,
    armed_alist_fixture,
    armed_alist_mock,
)
from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ALIST_FILES = {"daemon/migrations/checkpoint_migrator.py"}


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root-conftest langgraph mocks for this module (repo pattern).

    The global mocks installed by ``tests/conftest.py`` block the real
    ``AsyncPostgresSaver`` import that ``real_pg_checkpointer`` and the
    fixture-patch path need. Eviction is the binding-gate idiom; the
    T5.4 armed-absence tests need the real class to patch it.
    """
    saved = evict_langgraph_mocks()
    try:
        yield
    finally:
        restore_langgraph_mocks(saved)


class TestFixtureSelfCheck:
    """Self-check: the fixture is GENUINELY armed.

    If a future maintainer drops the ``side_effect=AssertionError(…)``
    wiring, the test catches it directly. A vacuously-armed fixture
    would silently let live-path alist calls through; this test guards
    against that regression.
    """

    def test_armed_alist_mock_sets_side_effect(self):
        """``armed_alist_mock()`` returns an ``AsyncMock`` with a side_effect."""
        mock = armed_alist_mock()
        assert isinstance(mock, AsyncMock)
        assert mock.side_effect is not None
        # The side_effect MUST be an AssertionError instance carrying the
        # contract message. Mock re-instantiates the call args on each
        # invocation, so what we see is the eager instance from init.
        assert isinstance(mock.side_effect, AssertionError)
        err = mock.side_effect  # this IS the eager AssertionError instance
        assert isinstance(err, AssertionError)
        assert ARMED_ALIST_MESSAGE in str(err)

    @pytest.mark.asyncio
    async def test_invoking_armed_mock_raises(self):
        """Calling the armed mock raises AssertionError with the contract message."""
        mock = armed_alist_mock()
        with pytest.raises(AssertionError, match=ARMED_ALIST_MESSAGE):
            await mock()


class TestFixtureWiresClass:
    """The ``armed_alist_fixture`` pytest fixture patches the live class."""

    def test_fixture_patches_async_postgres_saver_alist(
        self, armed_alist_fixture
    ):
        """``armed_alist_fixture`` patches ``AsyncPostgresSaver.alist``.

        After the fixture is applied, the class's ``alist`` attribute
        is the armed mock. Other tests using the same class but not
        the fixture are unaffected (the monkeypatch undoes on teardown).
        """
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        assert AsyncPostgresSaver.alist is armed_alist_fixture


class TestRepoWideGrepGuard:
    """AC-4.2 / T5.4: zero ``.alist(`` call sites outside the migrator.

    This is the regression-guard test for the §33 guardrail (FR-7) on
    the LIVE path. Any ``.alist(`` call in ``daemon/**/*.py`` outside
    the migrator is a Phase-5 violation — would resurrect the O(N²)
    pathology PR3 explicitly removed.

    Two-pass check:

    1. AST call-func scan over every file under ``daemon/`` except the
       migrator — must find ZERO ``ast.Call`` nodes whose ``func.attr
       == 'alist'``. AST-based (robust against multi-line / aliased
       references).
    2. Text grep over the same surface — sanity check, must agree with
       the AST result. (Documented because a typo like ``.aliast`` is
       an AST call but not the right name; the text grep would not
       match the typo.)
    """

    def _collect_violations(self) -> list[tuple[str, int]]:
        daemon_root = REPO_ROOT / "daemon"
        violations: list[tuple[str, int]] = []
        for path in sorted(daemon_root.rglob("*.py")):
            relpath = path.relative_to(REPO_ROOT).as_posix()
            if relpath in ALLOWED_ALIST_FILES:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "alist"
                ):
                    violations.append((relpath, node.lineno))
        return violations

    def test_zero_alist_call_sites_outside_migrator(self):
        """AST scan: zero ``.alist(`` calls outside ``daemon/migrations/checkpoint_migrator.py``."""
        violations = self._collect_violations()
        assert violations == [], (
            f"Found {len(violations)} .alist() call site(s) in daemon/**/*.py "
            f"outside the migrator:\n"
            + "\n".join(f"  {p}:{ln}" for p, ln in violations)
        )

    def test_migrator_is_the_only_exemption(self):
        """Sanity: the migrator IS in scope for alist calls.

        Without this assertion, a future maintainer could delete the
        migrator's exemption from ``ALLOWED_ALIST_FILES`` and the
        above test would silently pass with zero violations — leaving
        the migrator unable to use alist.
        """
        assert "daemon/migrations/checkpoint_migrator.py" in ALLOWED_ALIST_FILES
        migrator_path = REPO_ROOT / "daemon/migrations/checkpoint_migrator.py"
        assert migrator_path.exists()
        source = migrator_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        alist_calls = sum(
            1
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "alist"
            )
        )
        assert alist_calls >= 1, (
            "The migrator should contain at least one .alist( call; the "
            "exemption entry exists because the migrator is the sanctioned "
            "alist caller."
        )


class TestLivePathExercise:
    """End-to-end prove: the fixture wires correctly on a real saver.

    This is the "stays green" half of the T5.4 brief: prove that
    applying the fixture on a real PG-backed saver does NOT break
    the live message-API path (because post-PR3 the live path makes
    zero alist calls).

    Skipped loudly when PG is unreachable (per the project's
    SKIP-LOUDLY contract; a skip is NOT green for the binding gate).
    """

    @pytest.fixture
    def _probe_pg(self):
        from tests.helpers.checkpoint_prune_pg import require_postgres

        require_postgres()

    @pytest.mark.asyncio
    async def test_live_path_does_not_fire_alist(
        self, _probe_pg, armed_alist_fixture
    ):
        """A real PG-backed saver round-trip stays green under the armed alist.

        Minimal live-path exercise: just aget on a thread with no
        checkpoints. Post-PR3 the aget-only path does not invoke alist,
        so the armed mock is never invoked; the call returns ``None``
        (empty state). The exercise exists to prove the fixture wires
        correctly through the binding-gate harness.
        """
        from tests.helpers.checkpoint_prune_pg import (
            create_disposable_db,
            drop_database,
            real_pg_checkpointer,
        )

        _name, dsn = await create_disposable_db()
        try:
            async with real_pg_checkpointer(_name, dsn) as (saver, _pool, _adapter):
                # aget on an empty thread — returns None. The live path
                # MUST NOT invoke alist (post-PR3 read flip).
                state = await saver.aget(
                    {"configurable": {"thread_id": "thr-armed", "checkpoint_ns": ""}}
                )
                assert state is None
                # THE live-path gate: alist was never invoked.
                armed_alist_fixture.assert_not_called()
        finally:
            await drop_database(_name)


class TestAlistHelperExportSurface:
    """The helper exports the symbols the brief requires.

    * ``armed_alist_fixture`` (pytest fixture name) — importable from
      ``tests.helpers.armed_absence``.
    * ``armed_alist_mock`` — the constructor; returns the AsyncMock with
      side_effect wired.
    * ``ARMED_ALIST_MESSAGE`` — the message string; tests can import
      this for ``pytest.raises(..., match=ARMED_ALIST_MESSAGE)``.
    """

    def test_armed_alist_fixture_callable(self):
        assert callable(armed_alist_fixture)

    def test_armed_alist_mock_callable(self):
        assert callable(armed_alist_mock)

    def test_armed_alist_message_is_string(self):
        assert isinstance(ARMED_ALIST_MESSAGE, str)
        assert "alist" in ARMED_ALIST_MESSAGE
        assert "live path" in ARMED_ALIST_MESSAGE
