"""Phase 3 — set_instance_tunable tool test matrix (a)-(h).

The tool is acquired through the real ``create_instance_tools`` factory
with the heavy factory helpers patched out (the
``tests.helpers.send_message_fixtures`` pattern), then invoked via
``tool.coroutine(...)``. As of 2026-09-13 (H2 + M6), the tool body
lives in ``daemon.tools.tunables.create_set_instance_tunable_tool``
and the write routes through ``manager.set_metadata_many`` (the
InstanceManager facade) — no more ``manager._instance_repository``
reach-in.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from daemon.services.long_tool_nudge import (
    HARD_MAX_THRESHOLD_SECONDS,
    MIN_THRESHOLD_SECONDS,
)
from daemon.tools.tunables import (
    LONG_TOOL_CALL_ALLOWED_TUNABLES,
    LONG_TOOL_CALL_THRESHOLD_KEY,
)
from tests.helpers.send_message_fixtures import patch_heavy_helpers

TOOL_NAME = "set_instance_tunable"
KEY = "long_tool_call_threshold_seconds"


def _manager(*, enabled: bool = True, found: bool = True, prior=None):
    """Build a MagicMock manager wired for the post-M6 facade surface.

    The tool no longer reaches into ``manager._instance_repository``;
    it uses ``manager.get_instance_info`` for the prior-value read
    and ``manager.set_metadata_many`` for the write.
    """
    manager = MagicMock()
    manager.config.long_tool_nudge.enabled = enabled
    if found:
        manager.get_instance_info = MagicMock(
            return_value={
                "instance_metadata": {
                    LONG_TOOL_CALL_THRESHOLD_KEY: prior,
                }
                if prior is not None
                else {}
            }
        )
        manager.set_metadata_many = MagicMock(return_value=MagicMock())
    else:
        manager.get_instance_info = MagicMock(
            side_effect=KeyError("Instance not found")
        )
        manager.set_metadata_many = MagicMock(return_value=None)
    return manager


def _build_tools(manager):
    """Build the real instance-tool surface with heavy helpers patched."""
    patches = patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        from daemon.tools.instance import create_instance_tools

        tools = create_instance_tools(
            manager, "parent-instance", "developer"
        )
    finally:
        for p in patches:
            p.stop()
    return {getattr(t, "name", None): t for t in tools}


@pytest.fixture
def built():
    manager = _manager()
    mapping = _build_tools(manager)
    yield manager, mapping[TOOL_NAME]


class TestToolPresence:
    def test_tool_registered_in_factory_surface(self, built):
        _, tool = built
        assert tool is not None
        assert getattr(tool, "_tool_category", None) == "instance"

    def test_known_tool_names_contains_entry(self):
        from daemon.tools._tool_registry import KNOWN_TOOL_NAMES

        assert TOOL_NAME in KNOWN_TOOL_NAMES
        # alphabetic slot: send_message < set_instance_tunable < shared_meta_kv
        names = sorted(KNOWN_TOOL_NAMES)
        i = names.index(TOOL_NAME)
        assert names[i - 1] < TOOL_NAME < names[i + 1]


class TestAKillSwitchGate:
    @pytest.mark.asyncio
    async def test_disabled_returns_feature_disabled_and_writes_nothing(self):
        manager = _manager(enabled=False)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        assert result["error_code"] == "FEATURE_DISABLED"
        assert "no metadata written" in result["error"]
        manager.set_metadata_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_default_writes(self, built):
        manager, tool = built
        await tool.coroutine("child-1", KEY, 1200)
        manager.set_metadata_many.assert_called_once()


class TestBTypeCheck:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["1200", 12.5, None, True, False])
    async def test_non_int_and_bool_raise(self, built, bad):
        manager, tool = built
        with pytest.raises(ValueError, match=r"must be an integer number of seconds"):
            await tool.coroutine("child-1", KEY, bad)
        manager.set_metadata_many.assert_not_called()


class TestCAllowlist:
    @pytest.mark.asyncio
    async def test_unknown_key_returns_unknown_key_dict(self, built):
        manager, tool = built
        result = await tool.coroutine("child-1", "ltct", 900)
        assert result["error_code"] == "UNKNOWN_KEY"
        assert "long_tool_call_threshold_seconds" in result["error"]
        manager.set_metadata_many.assert_not_called()


class TestDRangeCheck:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [59, 0, -5, 1801, 18000])
    async def test_out_of_range_raises_loud(self, built, bad):
        manager, tool = built
        with pytest.raises(ValueError, match=r"must be in \[60, 1800\]"):
            await tool.coroutine("child-1", KEY, bad)
        manager.set_metadata_many.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("good", [60, 900, 1800])
    async def test_boundary_values_accepted(self, built, good):
        _, tool = built
        result = await tool.coroutine("child-1", KEY, good)
        assert result["effective_value"] == good


class TestEPriorValueRead:
    @pytest.mark.asyncio
    async def test_prior_value_echoed(self):
        """Prior value is read via ``manager.get_instance_info`` (M6 facade)."""
        manager = _manager(prior=900)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        manager.get_instance_info.assert_called_once_with("child-1")
        assert result["prior_value"] == 900

    @pytest.mark.asyncio
    async def test_prior_value_none_when_no_override(self):
        manager = _manager(prior=None)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        assert result["prior_value"] is None


class TestFWritePath:
    @pytest.mark.asyncio
    async def test_write_goes_through_set_metadata_many_only(self, built):
        """M6 — write routes through the ``InstanceManager.set_metadata_many``
        facade (NOT ``manager._instance_repository.set_metadata``)."""
        manager, tool = built
        await tool.coroutine("child-1", KEY, 1200)
        manager.set_metadata_many.assert_called_once_with(
            "child-1", {LONG_TOOL_CALL_THRESHOLD_KEY: 1200}
        )
        # The pre-M6 reach-in path is gone — the tool no longer touches
        # the repository directly.
        assert not getattr(manager, "_instance_repository", None) or not (
            manager._instance_repository.set_metadata.called
            if hasattr(manager._instance_repository, "set_metadata")
            else False
        )


class TestGReturnShape:
    @pytest.mark.asyncio
    async def test_success_shape(self, built):
        manager, tool = built
        result = await tool.coroutine("child-1", KEY, 1200)
        assert result["instance_id"] == "child-1"
        assert result["key"] == KEY
        assert result["prior_value"] is None
        # M7 — the upstream range check rejects >HARD_MAX; the
        # effective_value is just value (no silent clamp).
        assert result["effective_value"] == 1200
        assert "applied_at" in result

    @pytest.mark.asyncio
    async def test_not_found_when_set_metadata_many_returns_none(self):
        manager = _manager(found=False)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        # M5 — every error branch carries ``error`` + ``error_code``.
        assert result["error_code"] == "NOT_FOUND"
        assert "not found" in result["error"].lower()


class TestHConstants:
    def test_tunables_module_constants(self):
        from daemon.tools import tunables

        assert tunables.LONG_TOOL_CALL_THRESHOLD_KEY == KEY
        assert tunables.LONG_TOOL_CALL_ALLOWED_TUNABLES == frozenset({KEY})

    def test_floor_ceiling_identity_imported_from_canonical_home(self):
        """AD-30: the tool imports the constants FROM the canonical home
        (no duplication, no aliasing, no config-side re-export).

        Pinned by SOURCE (import statement) + VALUE: object-identity
        via ``is`` is ordering-sensitive under the suite's module
        pop/re-import fixtures (values equal, object forked — a
        test-only artifact; production has one import chain)."""
        import inspect

        import daemon.tools.tunables as tunables
        from daemon.services import long_tool_nudge as canonical

        source = inspect.getsource(tunables)
        assert (
            "from daemon.services.long_tool_nudge import" in source
        ), "tunables.py must import the canonical constants directly"
        assert tunables.HARD_MAX_THRESHOLD_SECONDS == canonical.HARD_MAX_THRESHOLD_SECONDS
        assert tunables.MIN_THRESHOLD_SECONDS == canonical.MIN_THRESHOLD_SECONDS
        assert tunables.HARD_MAX_THRESHOLD_SECONDS == 1800
        assert tunables.MIN_THRESHOLD_SECONDS == 60
        assert (
            tunables.LONG_TOOL_CALL_THRESHOLD_KEY
            == canonical.LONG_TOOL_NUDGE_THRESHOLD_KEY
        )


class TestExposure:
    @pytest.mark.parametrize(
        "agent", ["leader", "planner", "developer", "tester", "governor"]
    )
    def test_active_meta_json_allows_instance_category(self, agent):
        import json

        meta = json.load(open(f"agents/{agent}/meta.json"))
        assert "instance" in meta["tools"]["allow"], (
            f"{agent} must allow the 'instance' category to receive "
            f"set_instance_tunable"
        )
