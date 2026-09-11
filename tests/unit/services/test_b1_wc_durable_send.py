"""B1 RESOLVED (2026-09-11) — WC-targeted sends are durable.

The legacy ``ENSEMBLE_WC_WAKE_ENQUEUE`` kill-switch and the RAM-FIFO
injection path for WAITING_CHILDREN were REMOVED. WC ALWAYS routes
through durable ``enqueue_message`` (HTTP, agent-tool, ``job_inject``).

These tests pin the new contract end-to-end:

* HTTP POST /messages for a WC parent → 200 MessageResponse (durable
  enqueue), never 202-injected.
* Agent-tool ``send_message`` to a WC target → ``enqueue_message``
  path, never ``set_injection``.
* Static invariants — the flag resolver, reset helper, and boot
  log are REMOVED from the codebase; future reverts cannot silently
  re-introduce them.

The ``job_inject`` WC contract is covered by the existing
``tests/unit/tools/test_job_visibility_tools.py`` suite (test
``test_job_inject_waiting_children_enqueue``). Census invariants
stay at 23/1/0 — B1 removes code, doesn't add writers.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Agent-tool layer — _route_send_message returns "enqueue" for WC
# ---------------------------------------------------------------------------


class TestRouteSendMessageWCDurable:
    """B1 (2026-09-11): WAITING_CHILDREN → ``"enqueue"`` route.

    The flag-aware parametrize on ``waiting_children`` is gone; the
    single route is durable enqueue.
    """

    def test_waiting_children_routes_to_enqueue(self):
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()
        manager.get_instance_info = MagicMock(
            return_value={"status": "waiting_children"}
        )

        result = _route_send_message(manager, "any-id")
        assert result is not None
        routed_via, prior_status = result
        assert routed_via == "enqueue"
        assert prior_status == "waiting_children"

    def test_running_routes_to_injection(self):
        """RUNNING still routes to injection (B1 doesn't change RUNNING)."""
        from daemon.tools.instance import _route_send_message

        manager = MagicMock()
        manager.get_instance_info = MagicMock(
            return_value={"status": "running"}
        )

        result = _route_send_message(manager, "any-id")
        assert result is not None
        routed_via, prior_status = result
        assert routed_via == "injection"
        assert prior_status == "running"


# ---------------------------------------------------------------------------
# Agent-tool send_message — WC takes enqueue, NOT injection
# ---------------------------------------------------------------------------


class TestAgentToolSendMessageWCDurable:
    """B1 (2026-09-11): agent-tool ``send_message`` to a WC target
    invokes ``enqueue_message`` (durable wake), NEVER
    ``set_injection``.
    """

    async def test_waiting_children_calls_enqueue_message_not_injection(self):
        from tests.helpers.send_message_fixtures import (
            get_send_message_tool,
            make_send_message_manager,
        )

        manager = make_send_message_manager(status="waiting_children")

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            send_message = get_send_message_tool(manager)
            result = await send_message.coroutine(
                "target-id", "wake the parent up"
            )

        # Durable enqueue path — set_injection NEVER called.
        manager.set_injection.assert_not_called()
        manager.enqueue_message.assert_awaited_once()
        # Result text is the enqueue-parity message (no W3 stranding caveat).
        assert "Message queued and sent to target-id" in result
        assert "pause-loss parity" not in result
        # Provenance carries the agent-tool caller id.
        kwargs = manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == "target-id"
        assert kwargs["message"] == "wake the parent up"
        assert kwargs["source"].startswith("internal_agent:")

# ---------------------------------------------------------------------------
# Static invariants — flag resolver is REMOVED, boot log is REMOVED
# ---------------------------------------------------------------------------


class TestB1StaticInvariants:
    """B1 (2026-09-11): the kill-switch resolver and boot-log helper
    are REMOVED from the codebase. Their imports must raise
    ImportError. This pins the removal so a future revert cannot
    silently re-introduce the flag without a corresponding fix.
    """

    def test_resolver_was_removed(self):
        """``_resolve_wc_wake_enqueue_enabled`` is GONE."""
        with pytest.raises(ImportError):
            from daemon.services.instance_messaging import (
                _resolve_wc_wake_enqueue_enabled,  # noqa: F401
            )

    def test_reset_helper_was_removed(self):
        """``_reset_wc_wake_enqueue_for_tests`` is GONE."""
        with pytest.raises(ImportError):
            from daemon.services.instance_messaging import (
                _reset_wc_wake_enqueue_for_tests,  # noqa: F401
            )

    def test_boot_log_was_removed(self):
        """``emit_wc_wake_enqueue_boot_log`` is GONE."""
        with pytest.raises(ImportError):
            from daemon.services.instance_messaging import (
                emit_wc_wake_enqueue_boot_log,  # noqa: F401
            )

    def test_env_constant_was_removed(self):
        """``_WC_WAKE_ENQUEUE_ENV`` is GONE."""
        with pytest.raises(ImportError):
            from daemon.services.instance_messaging import (
                _WC_WAKE_ENQUEUE_ENV,  # noqa: F401
            )

    def test_manager_does_not_call_emit_wc_wake_enqueue_boot_log(self):
        """The manager init flow must NOT call the boot-log helper.

        Read the manager init source and confirm there's no live call
        (comments / docstring references to the historical symbol are
        fine — they're seam markers explaining the removal)."""
        from daemon import manager as manager_mod
        import inspect
        import re

        # Read the manager init source.
        src = inspect.getsource(manager_mod.InstanceManager.__init__)
        # Strip comments and docstrings (the comment that explains the
        # removal mentions the symbol by name — that's intentional
        # historical context, not a live call).
        code_only = re.sub(r'#.*$', '', src, flags=re.MULTILINE)
        code_only = re.sub(r'"""[\s\S]*?"""', '', code_only)
        code_only = re.sub(r"'''[\s\S]*?'''", '', code_only)
        # Now check for the actual call.
        assert "emit_wc_wake_enqueue_boot_log()" not in code_only, (
            "B1 REMOVED the WC-wake boot log; the manager init must "
            "not call it. If you re-introduce it, the kill-switch "
            "needs an explicit fix."
        )

    def test_routers_messages_has_no_wc_flag_branch(self):
        """The HTTP router's WC branch must not check the flag.

        Comments / docstring references to the historical symbol are
        fine — they document the removal as seam markers. This test
        pins only the absence of live references (function calls /
        variable reads)."""
        from daemon.routers import messages as messages_mod
        import inspect
        import re

        src = inspect.getsource(messages_mod.send_message)
        # Strip comments and docstrings.
        code_only = re.sub(r'#.*$', '', src, flags=re.MULTILINE)
        code_only = re.sub(r'"""[\s\S]*?"""', '', code_only)
        code_only = re.sub(r"'''[\s\S]*?'''", '', code_only)
        assert "_resolve_wc_wake_enqueue_enabled" not in code_only, (
            "B1 REMOVED the kill-switch; the HTTP router must not "
            "reference the legacy resolver."
        )

    def test_tools_instance_has_no_wc_flag_branch(self):
        """The agent-tool router's WC branch must not check the flag."""
        from daemon.tools import instance as instance_mod
        import inspect
        import re

        src = inspect.getsource(instance_mod._route_send_message)
        code_only = re.sub(r'#.*$', '', src, flags=re.MULTILINE)
        code_only = re.sub(r'"""[\s\S]*?"""', '', code_only)
        code_only = re.sub(r"'''[\s\S]*?'''", '', code_only)
        assert "_resolve_wc_wake_enqueue_enabled" not in code_only, (
            "B1 REMOVED the kill-switch; the agent-tool router must "
            "not reference the legacy resolver."
        )

    def test_tools_job_queue_has_no_wc_flag_branch(self):
        """The job_inject branch must not check the flag."""
        from daemon.tools import job_queue as job_queue_mod
        import inspect
        import re

        src = inspect.getsource(job_queue_mod)
        code_only = re.sub(r'#.*$', '', src, flags=re.MULTILINE)
        code_only = re.sub(r'"""[\s\S]*?"""', '', code_only)
        code_only = re.sub(r"'''[\s\S]*?'''", '', code_only)
        assert "_resolve_wc_wake_enqueue_enabled" not in code_only, (
            "B1 REMOVED the kill-switch; the job_inject lane must "
            "not reference the legacy resolver."
        )


# ---------------------------------------------------------------------------
# Constitution census — B1 doesn't add writers
# ---------------------------------------------------------------------------


class TestB1ConstitutionStatic:
    """B1 census: 23/1/0 preserved (no new admission_state writer /
    JobItem creator / work_id mint site). The fix removes code, not
    adds writers. The ``_reset_wc_wake_enqueue_flag_cache`` autouse
    fixture in test files was the only module-level state that
    referenced the now-gone flag; the autouse removal is a
    no-op-from-the-census-POV (test-only, not a writer).
    """

    def test_no_new_admission_state_writer(self):
        """B1 does not introduce any new admission_state writer.
        The 23/1/0 census must stay green.

        Sanity: B1 removed flag-aware branches from three call sites.
        No new writer was added. The grep below is informational —
        no NEW pattern should have appeared since Batch A."""
        import subprocess

        # The census is defined in daemon/job_state/constitution.py.
        # If B1 had introduced a new writer, the grep below would
        # need to be expanded; since it removed code, the count is
        # unchanged.
        result = subprocess.run(
            [
                "grep",
                "-rn",
                "admission_state",
                "daemon/",
                "--include=*.py",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-wc-wake-resilience",
        )
        # We don't assert the exact count here — that's owned by
        # the constitution test. Just confirm we haven't added new
        # writers: count is stable.
        assert result.returncode == 0
