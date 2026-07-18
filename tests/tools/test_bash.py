"""Tests for bash cancellation cleanup and runtime registration fallback."""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

bash_module = importlib.import_module("daemon.tools.bash")


class _FakeProcess:
    pid = 4242
    returncode = 0

    def wait(self):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        loop.call_soon(future.set_result, 0)
        return future


class TestCancelledErrorLeakFix:
    async def test_cancelled_error_at_wait_kills_process(self, monkeypatch):
        proc = _FakeProcess()
        registry = MagicMock()
        registry.register = AsyncMock()
        registry.unregister = AsyncMock()
        kill_process = AsyncMock()
        monkeypatch.setattr(
            bash_module.asyncio,
            "create_subprocess_shell",
            AsyncMock(return_value=proc),
        )
        monkeypatch.setattr(
            bash_module.asyncio,
            "wait_for",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        monkeypatch.setattr(bash_module, "_kill_process", kill_process)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: registry
        )
        monkeypatch.setattr(bash_module.os, "getpgid", lambda _pid: proc.pid)

        with pytest.raises(asyncio.CancelledError):
            await bash_module.bash.coroutine(
                command="echo hi", instance_id="test-inst"
            )

        kill_process.assert_awaited_once_with(proc)
        registry.unregister.assert_awaited_once_with("test-inst", proc.pid)

    async def test_cancelled_error_at_spawn_kills_process_if_proc_assigned(
        self, monkeypatch
    ):
        proc = _FakeProcess()
        registry = MagicMock()
        registry.register = AsyncMock()
        registry.unregister = AsyncMock()
        kill_process = AsyncMock()

        async def spawn_then_cancel(*args, **kwargs):
            task = asyncio.current_task()
            assert task is not None
            asyncio.get_running_loop().call_soon(task.cancel)
            return proc

        monkeypatch.setattr(
            bash_module.asyncio,
            "create_subprocess_shell",
            spawn_then_cancel,
        )
        monkeypatch.setattr(bash_module, "_kill_process", kill_process)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: registry
        )
        monkeypatch.setattr(bash_module.os, "getpgid", lambda _pid: proc.pid)

        with pytest.raises(asyncio.CancelledError):
            await bash_module.bash.coroutine(
                command="echo hi", instance_id="test-inst"
            )

        kill_process.assert_awaited_once_with(proc)
        registry.unregister.assert_awaited_once_with("test-inst", proc.pid)

    async def test_cancelled_error_at_spawn_skips_kill_if_proc_none(
        self, monkeypatch
    ):
        kill_process = AsyncMock()
        monkeypatch.setattr(
            bash_module.asyncio,
            "create_subprocess_shell",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        monkeypatch.setattr(bash_module, "_kill_process", kill_process)

        with pytest.raises(asyncio.CancelledError):
            await bash_module.bash.coroutine(
                command="echo hi", instance_id="test-inst"
            )

        kill_process.assert_not_awaited()

    async def test_uncancel_called_for_python_311_plus(self, monkeypatch):
        proc = _FakeProcess()
        registry = MagicMock()
        registry.register = AsyncMock()
        registry.unregister = AsyncMock()
        fake_task = MagicMock()
        fake_task.uncancel = MagicMock()
        monkeypatch.setattr(
            bash_module.asyncio,
            "create_subprocess_shell",
            AsyncMock(return_value=proc),
        )
        monkeypatch.setattr(
            bash_module.asyncio,
            "wait_for",
            AsyncMock(side_effect=asyncio.CancelledError),
        )
        monkeypatch.setattr(bash_module.asyncio, "current_task", lambda: fake_task)
        monkeypatch.setattr(bash_module, "_kill_process", AsyncMock())
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: registry
        )
        monkeypatch.setattr(bash_module.os, "getpgid", lambda _pid: proc.pid)

        with pytest.raises(asyncio.CancelledError):
            await bash_module.bash.coroutine(
                command="echo hi", instance_id="test-inst"
            )

        fake_task.uncancel.assert_called_once_with()

    async def test_instance_id_none_skips_registration_with_warning(
        self, monkeypatch, caplog
    ):
        get_registry = MagicMock()
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", get_registry
        )

        with caplog.at_level("WARNING"):
            result = await bash_module.bash.coroutine(
                command="echo hi", instance_id=None
            )

        assert "hi" in result
        get_registry.assert_not_called()
        assert any(
            "instance_id is None; skipping process registration"
            in record.getMessage()
            for record in caplog.records
        )
