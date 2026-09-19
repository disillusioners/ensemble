"""Phase 3 / chat-source-worker-lane — Task #7 — boot-line observability.

Pins SC#8: the chat pool boot emits
``"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"``
via ``logger.info`` (``daemon/manager.py:6800-6803``).

In-process log capture only: ``caplog`` substring pin (filesystem
log parsing FORBIDDEN per Phase 2/3 Exit Criterion #1).
Precedent: ``tests/unit/job_queue/test_joblock_sweep_lifecycle.py``
uses the same in-process capture pattern for boot-line assertions.

Substring match is acceptable (e.g., assert
``"ChatSourceWorkerPool started"`` is in any captured record's
formatted message); the exact workers=2 / prefixes= fields are
rendered from the live ``CHAT_WORKER_POOL_SIZE`` and
``CHAT_SOURCE_PREFIXES`` constants — pinning those substrings
guards against constant drift.
"""

from __future__ import annotations

import logging

import pytest

from daemon.constants import CHAT_SOURCE_PREFIXES, CHAT_WORKER_POOL_SIZE
from tests.integration.chat_source_harness import (
    build_chat_source_engine,
    build_live_pool_manager,
    chat_lane_flag_reset_fixture,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Lane-flag isolation
# ---------------------------------------------------------------------------


# Shared autouse lane-flag reset — the @pytest.fixture(autouse=True)
# decoration travels with the harness factory's returned object, so
# this single module-level assignment wires it for every test here.
chat_lane_flag_reset = chat_lane_flag_reset_fixture()


@pytest.fixture
def engine(tmp_path):
    eng = build_chat_source_engine(str(tmp_path / "chat_boot_line.db"))
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestChatSourceBootLine:
    """SC#8 — boot log substring pin via ``caplog`` in-process capture."""

    def test_chat_pool_started_substring_emitted(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """``"ChatSourceWorkerPool started"`` is logged via ``logger.info``
        when the chat pool constructs during ``setup_worker_pool``."""
        with caplog.at_level(logging.INFO):
            with build_live_pool_manager(engine) as manager:
                # Sanity: chat pool is alive (boot-line was emitted
                # AS A RESULT of the chat pool construction).
                assert manager._chat_worker_pool is not None
                assert (
                    len(manager._chat_worker_pool._workers)
                    == CHAT_WORKER_POOL_SIZE
                )

        # The literal boot-line substring.
        assert "ChatSourceWorkerPool started" in caplog.text, (
            f"expected 'ChatSourceWorkerPool started' substring in "
            f"boot log; got: {caplog.text!r}"
        )

    def test_boot_line_includes_production_worker_count(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """The boot line includes ``workers=2`` — the
        ``CHAT_WORKER_POOL_SIZE`` constant rendered into the
        f-string at ``daemon/manager.py:6800-6803``.

        This guards against constant drift: if a future change
        widens ``CHAT_WORKER_POOL_SIZE`` without updating the
        boot-line template, this assertion fails.
        """
        with caplog.at_level(logging.INFO):
            with build_live_pool_manager(engine) as manager:
                pass  # context exit triggers shutdown

        assert f"workers={CHAT_WORKER_POOL_SIZE}" in caplog.text, (
            f"expected 'workers={CHAT_WORKER_POOL_SIZE}' substring in "
            f"boot log; got: {caplog.text!r}"
        )

    def test_boot_line_includes_all_three_chat_prefixes(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """The boot line includes ``prefixes=telegram:,slack:,discord:``
        — the ``CHAT_SOURCE_PREFIXES`` tuple rendered into the
        f-string at ``daemon/manager.py:6800-6803``. The order
        matches the constant tuple's order (P10).

        Pin against constant drift: a future change that adds a
        prefix or reorders the tuple must also update the boot-line
        template (or this test fails).
        """
        with caplog.at_level(logging.INFO):
            with build_live_pool_manager(engine) as manager:
                pass

        expected_prefixes = ",".join(CHAT_SOURCE_PREFIXES)
        assert f"prefixes={expected_prefixes}" in caplog.text, (
            f"expected 'prefixes={expected_prefixes}' substring in "
            f"boot log; got: {caplog.text!r}"
        )

    def test_boot_line_full_substring(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """The full literal substring
        ``"ChatSourceWorkerPool started: workers=2, "
        "prefixes=telegram:,slack:,discord:"`` appears in the
        captured log.

        This is the SC#8 literal — the test is a single substring
        check against the assembled boot line.
        """
        with caplog.at_level(logging.INFO):
            with build_live_pool_manager(engine) as manager:
                pass

        # Compose the full SC#8 literal from the live constants —
        # guards against both constant drift AND template drift.
        expected_full = (
            f"ChatSourceWorkerPool started: "
            f"workers={CHAT_WORKER_POOL_SIZE}, "
            f"prefixes={','.join(CHAT_SOURCE_PREFIXES)}"
        )
        assert expected_full in caplog.text, (
            f"expected full boot line {expected_full!r} in log; "
            f"got: {caplog.text!r}"
        )
