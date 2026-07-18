"""Tests for instance-scoped bash tool wrapping."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from daemon.tools.bash import bash
from daemon.tools.instance import (
    _make_instance_id_aware,
    _make_workdir_aware,
)


class TestInstanceIdAware:
    async def test_wrapper_injects_instance_id_when_none(self, monkeypatch):
        received: list[str | None] = []

        async def fake_bash(
            command,
            timeout=1800,
            workdir=None,
            input=None,
            instance_id=None,
        ):
            received.append(instance_id)
            return "ok"

        monkeypatch.setattr(bash, "coroutine", fake_bash)
        wrapped = _make_instance_id_aware(bash, lambda: "closure-iid")

        result = await wrapped.ainvoke({"command": "echo hi"})

        assert result == "ok"
        assert received == ["closure-iid"]

    async def test_wrapper_does_not_override_explicit_instance_id(
        self, monkeypatch
    ):
        received: list[str | None] = []

        async def fake_bash(
            command,
            timeout=1800,
            workdir=None,
            input=None,
            instance_id=None,
        ):
            received.append(instance_id)
            return "ok"

        monkeypatch.setattr(bash, "coroutine", fake_bash)
        wrapped = _make_instance_id_aware(bash, lambda: "closure-iid")

        result = await wrapped.coroutine(
            command="echo hi", instance_id="explicit"
        )

        assert result == "ok"
        assert received == ["explicit"]

    def test_args_schema_does_not_expose_instance_id(self):
        wrapped = _make_instance_id_aware(
            _make_workdir_aware(bash, lambda: None),
            lambda: "closure-iid",
        )

        assert list(wrapped.args.keys()) == [
            "command",
            "timeout",
            "workdir",
            "input",
        ]
        assert "instance_id" not in wrapped.args_schema.model_fields

    async def test_runtime_fallback_warns_and_skips_when_instance_id_is_none(
        self, monkeypatch, caplog
    ):
        bash_module = importlib.import_module("daemon.tools.bash")

        get_registry = MagicMock()
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", get_registry
        )

        with caplog.at_level("WARNING"):
            result = await bash.coroutine(command="echo hi", instance_id=None)

        assert "hi" in result
        get_registry.assert_not_called()
        assert any(
            "instance_id is None; skipping process registration"
            in record.getMessage()
            for record in caplog.records
        )
