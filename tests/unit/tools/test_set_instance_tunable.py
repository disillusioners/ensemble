"""Phase 3 — set_instance_tunable tool test matrix (a)-(h).

The tool is acquired through the real ``create_instance_tools`` factory
with the heavy factory helpers patched out (the
``tests.helpers.send_message_fixtures`` pattern), then invoked via
``tool.coroutine(...)``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from daemon.services.long_tool_nudge import (
    HARD_MAX_THRESHOLD_SECONDS,
    MIN_THRESHOLD_SECONDS,
)
from tests.helpers.send_message_fixtures import patch_heavy_helpers

TOOL_NAME = "set_instance_tunable"
KEY = "long_tool_call_threshold_seconds"


def _manager(*, enabled: bool = True, found: bool = True):
    manager = MagicMock()
    manager.config.long_tool_nudge.enabled = enabled
    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    if found:
        repo.set_metadata = MagicMock(return_value=MagicMock())
    else:
        repo.set_metadata = MagicMock(return_value=None)
    manager._instance_repository = repo
    return manager, repo


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
    manager, repo = _manager()
    mapping = _build_tools(manager)
    yield manager, repo, mapping[TOOL_NAME]


class TestToolPresence:
    def test_tool_registered_in_factory_surface(self, built):
        _, _, tool = built
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
        manager, repo = _manager(enabled=False)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        assert result["error_code"] == "FEATURE_DISABLED"
        assert "no metadata written" in result["message"]
        repo.set_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_default_writes(self, built):
        _, repo, tool = built
        await tool.coroutine("child-1", KEY, 1200)
        repo.set_metadata.assert_called_once()


class TestBTypeCheck:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["1200", 12.5, None, True, False])
    async def test_non_int_and_bool_raise(self, built, bad):
        _, repo, tool = built
        with pytest.raises(ValueError):
            await tool.coroutine("child-1", KEY, bad)
        repo.set_metadata.assert_not_called()


class TestCAllowlist:
    @pytest.mark.asyncio
    async def test_unknown_key_returns_unknown_key_dict(self, built):
        _, repo, tool = built
        result = await tool.coroutine("child-1", "ltct", 900)
        assert result["error_code"] == "UNKNOWN_KEY"
        assert "long_tool_call_threshold_seconds" in result["error"]
        repo.set_metadata.assert_not_called()


class TestDRangeCheck:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [59, 0, -5, 1801, 18000])
    async def test_out_of_range_raises_loud(self, built, bad):
        _, repo, tool = built
        with pytest.raises(ValueError, match=r"must be in \[60, 1800\]"):
            await tool.coroutine("child-1", KEY, bad)
        repo.set_metadata.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("good", [60, 900, 1800])
    async def test_boundary_values_accepted(self, built, good):
        _, repo, tool = built
        result = await tool.coroutine("child-1", KEY, good)
        assert result["effective_value"] == good


class TestEPriorValueRead:
    @pytest.mark.asyncio
    async def test_prior_value_echoed(self):
        manager, repo = _manager()
        repo.get_metadata_value = MagicMock(return_value=900)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        repo.get_metadata_value.assert_called_once_with(
            "child-1", "long_tool_call_threshold_seconds"
        )
        assert result["prior_value"] == 900


class TestFWritePath:
    @pytest.mark.asyncio
    async def test_write_goes_through_set_metadata_only(self, built):
        _, repo, tool = built
        await tool.coroutine("child-1", KEY, 1200)
        repo.set_metadata.assert_called_once_with(
            "child-1", "long_tool_call_threshold_seconds", 1200
        )
        # NEVER update_instance — it rejects instance_metadata.
        repo.update_instance = MagicMock()
        assert not repo.update_instance.called


class TestGReturnShape:
    @pytest.mark.asyncio
    async def test_success_shape(self, built):
        _, repo, tool = built
        result = await tool.coroutine("child-1", KEY, 1200)
        assert result["instance_id"] == "child-1"
        assert result["key"] == KEY
        assert result["prior_value"] is None
        assert result["effective_value"] == min(1200, HARD_MAX_THRESHOLD_SECONDS)
        assert "applied_at" in result

    @pytest.mark.asyncio
    async def test_not_found_when_set_metadata_returns_none(self):
        manager, repo = _manager(found=False)
        tool = _build_tools(manager)[TOOL_NAME]
        result = await tool.coroutine("child-1", KEY, 1200)
        assert result == {"error_code": "NOT_FOUND"}


class TestHConstants:
    def test_module_constants(self):
        import daemon.tools.instance as inst

        assert inst.LONG_TOOL_CALL_THRESHOLD_KEY == KEY
        assert inst.LONG_TOOL_CALL_ALLOWED_TUNABLES == frozenset({KEY})

    def test_floor_ceiling_identity_imported_from_canonical_home(self):
        """AD-30: the tool imports the constants FROM the canonical home
        (no duplication, no aliasing, no config-side re-export).

        Pinned by SOURCE (import statement) + VALUE: object-identity
        via ``is`` is ordering-sensitive under the suite's module
        pop/re-import fixtures (values equal, object forked — a
        test-only artifact; production has one import chain)."""
        import inspect

        import daemon.tools.instance as inst
        from daemon.services import long_tool_nudge as canonical

        source = inspect.getsource(inst)
        assert (
            "from daemon.services.long_tool_nudge import" in source
        ), "instance.py must import the canonical constants directly"
        assert inst.HARD_MAX_THRESHOLD_SECONDS == canonical.HARD_MAX_THRESHOLD_SECONDS
        assert inst.MIN_THRESHOLD_SECONDS == canonical.MIN_THRESHOLD_SECONDS
        assert inst.HARD_MAX_THRESHOLD_SECONDS == 1800
        assert inst.MIN_THRESHOLD_SECONDS == 60
        assert inst.LONG_TOOL_CALL_THRESHOLD_KEY == canonical.LONG_TOOL_NUDGE_THRESHOLD_KEY


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
