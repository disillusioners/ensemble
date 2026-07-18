"""Unit tests for the bash subprocess registry."""

from __future__ import annotations

import os
import signal
import sys
from unittest.mock import MagicMock

import pytest

from daemon.tools.bash import BashProcessRegistry


@pytest.fixture
def unique_instance_id() -> str:
    """Return a unique instance ID for registry isolation."""
    return f"test-bash-{os.urandom(4).hex()}"


class TestRegistryBasics:
    async def test_register_adds_entry(self, unique_instance_id):
        registry = BashProcessRegistry()

        await registry.register(unique_instance_id, pid=1, pgid=1)

        assert len(registry._entries[unique_instance_id]) == 1
        entry = registry._entries[unique_instance_id][0]
        assert entry.pid == 1
        assert entry.pgid == 1

    async def test_register_appends_multiple_entries(self, unique_instance_id):
        registry = BashProcessRegistry()

        for pid in (1, 2, 3):
            await registry.register(unique_instance_id, pid=pid, pgid=pid + 10)

        assert len(registry._entries[unique_instance_id]) == 3

    async def test_unregister_removes_specific_pid(self, unique_instance_id):
        registry = BashProcessRegistry()
        await registry.register(unique_instance_id, pid=1, pgid=11)
        await registry.register(unique_instance_id, pid=2, pgid=12)

        await registry.unregister(unique_instance_id, pid=1)

        entries = registry._entries[unique_instance_id]
        assert len(entries) == 1
        assert entries[0].pid == 2

    async def test_unregister_clears_empty_bucket(self, unique_instance_id):
        registry = BashProcessRegistry()
        await registry.register(unique_instance_id, pid=1, pgid=11)

        await registry.unregister(unique_instance_id, pid=1)

        assert unique_instance_id not in registry._entries

    async def test_unregister_no_op_for_unknown_instance_id(self):
        registry = BashProcessRegistry()

        await registry.unregister("missing-iid", pid=1)

        assert registry._entries == {}

    async def test_unregister_no_op_for_unknown_pid(self, unique_instance_id):
        registry = BashProcessRegistry()
        await registry.register(unique_instance_id, pid=1, pgid=11)

        await registry.unregister(unique_instance_id, pid=999)

        assert len(registry._entries[unique_instance_id]) == 1
        assert registry._entries[unique_instance_id][0].pid == 1


class TestCleanupInstance:
    async def test_cleanup_instance_kills_all_entries(
        self, unique_instance_id, monkeypatch
    ):
        registry = BashProcessRegistry()
        for pid, pgid in ((1, 11), (2, 12), (3, 13)):
            await registry.register(unique_instance_id, pid=pid, pgid=pgid)
        kill_group = MagicMock()
        monkeypatch.setattr(registry, "_kill_group", kill_group)

        killed = await registry.cleanup_instance(unique_instance_id)

        assert killed == 3
        assert [call.args[0] for call in kill_group.call_args_list] == [11, 12, 13]
        assert unique_instance_id not in registry._entries

    async def test_cleanup_instance_idempotent_on_empty(self):
        registry = BashProcessRegistry()

        assert await registry.cleanup_instance("missing-iid") == 0
        assert await registry.cleanup_instance("missing-iid") == 0

    async def test_cleanup_instance_isolates_per_entry_failure(
        self, unique_instance_id, monkeypatch
    ):
        registry = BashProcessRegistry()
        for pid, pgid in ((1, 11), (2, 12), (3, 13)):
            await registry.register(unique_instance_id, pid=pid, pgid=pgid)
        attempted: list[int] = []

        def kill_group(pgid: int) -> None:
            attempted.append(pgid)
            if pgid == 12:
                raise OSError("synthetic kill failure")

        monkeypatch.setattr(registry, "_kill_group", kill_group)

        killed = await registry.cleanup_instance(unique_instance_id)

        assert killed == 2
        assert attempted == [11, 12, 13]
        assert unique_instance_id not in registry._entries

    async def test_cleanup_instance_uses_killpg_on_unix(
        self, unique_instance_id, monkeypatch
    ):
        registry = BashProcessRegistry()
        await registry.register(unique_instance_id, pid=7, pgid=42)
        killpg = MagicMock()
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(os, "killpg", killpg)

        killed = await registry.cleanup_instance(unique_instance_id)

        assert killed == 1
        killpg.assert_called_once_with(42, signal.SIGKILL)


class TestCleanupAll:
    async def test_cleanup_all_empties_all_buckets(self, monkeypatch):
        registry = BashProcessRegistry()
        kill_group = MagicMock()
        monkeypatch.setattr(registry, "_kill_group", kill_group)
        for index in range(3):
            await registry.register(f"iid-{index}", pid=index + 1, pgid=index + 11)

        killed = await registry.cleanup_all()

        assert killed == 3
        assert registry._entries == {}

    async def test_cleanup_all_is_idempotent(self, monkeypatch):
        registry = BashProcessRegistry()
        monkeypatch.setattr(registry, "_kill_group", MagicMock())

        assert await registry.cleanup_all() == 0
        await registry.register("iid", pid=1, pgid=11)
        assert await registry.cleanup_all() == 1
        assert await registry.cleanup_all() == 0

    async def test_cleanup_all_isolates_per_instance_failures(self, monkeypatch):
        registry = BashProcessRegistry()
        for index in range(3):
            await registry.register(f"iid-{index}", pid=index + 1, pgid=index + 11)
        original_cleanup = registry.cleanup_instance
        attempted: list[str] = []

        async def cleanup_instance(instance_id: str) -> int:
            attempted.append(instance_id)
            if instance_id == "iid-1":
                async with registry._lock:
                    registry._entries.pop(instance_id, [])
                raise RuntimeError("synthetic instance failure")
            return await original_cleanup(instance_id)

        monkeypatch.setattr(registry, "cleanup_instance", cleanup_instance)
        monkeypatch.setattr(registry, "_kill_group", MagicMock())

        killed = await registry.cleanup_all()

        assert killed == 2
        assert attempted == ["iid-0", "iid-1", "iid-2"]
        assert registry._entries == {}


class TestKillGroup:
    def test_kill_group_uses_killpg_on_unix(self, monkeypatch):
        killpg = MagicMock()
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(os, "killpg", killpg)

        BashProcessRegistry._kill_group(42)

        killpg.assert_called_once_with(42, signal.SIGKILL)

    def test_kill_group_windows_fallback(self, monkeypatch):
        import subprocess

        run = MagicMock()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "run", run)

        BashProcessRegistry._kill_group(42)

        run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "42"],
            capture_output=True,
        )
