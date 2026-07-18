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

    # ────────────────────────────────────────────────────────────────────
    # m7 (MINOR) — defense-in-depth: LLM cannot override ``instance_id``.
    # The wrapper uses ``if kwargs.get('instance_id') is None`` to decide
    # whether to inject the closure value. A non-None value WOULD override
    # the closure — so we rely on the @tool args_schema (BashInputSchema)
    # to keep ``instance_id`` out of the LLM-visible surface. Two layers:
    #   (a) BashInputSchema uses default Pydantic ``extra='ignore'`` → the
    #       extra ``instance_id`` is silently stripped BEFORE it reaches
    #       the wrapped function.
    #   (b) ``StructuredTool.args`` does not advertise ``instance_id`` —
    #       already covered by ``test_args_schema_does_not_expose_instance_id``.
    # ────────────────────────────────────────────────────────────────────

    def test_bash_input_schema_strips_extra_instance_id(self):
        """Pydantic's default ``extra='ignore'`` silently drops unknown keys.

        Verified empirically: ``BashInputSchema(command='echo hi',
        instance_id='attacker-id')`` does NOT raise — it accepts and drops
        ``instance_id``. This is the first defense layer.
        """
        from daemon.tools.bash import BashInputSchema

        schema = BashInputSchema(command="echo hi", instance_id="attacker-id")
        # ``instance_id`` must NOT be on the constructed model: Pydantic
        # default strips it.
        dumped = schema.model_dump()
        assert dumped == {
            "command": "echo hi",
            "timeout": 1800,
            "workdir": None,
            "input": None,
        }
        assert "instance_id" not in dumped

        # model_validate must behave the same way (this is the path that
        # StructuredTool.ainvoke uses when binding LLM-supplied kwargs).
        validated = BashInputSchema.model_validate(
            {"command": "echo hi", "instance_id": "attacker-id"}
        )
        assert "instance_id" not in validated.model_dump()

    async def test_wrapped_ainvoke_strips_llm_injected_instance_id(
        self, monkeypatch
    ):
        """Even if a future bug or alternate path lets ``instance_id`` through,
        the runtime injects the closure value whenever the model is None.
        We confirm the production path: LLM-supplied ``instance_id`` is
        stripped by the schema, so the closure value wins.
        """
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

        # Attempt to inject ``instance_id`` via LLM-supplied input. The
        # args_schema should strip it, so the closure value reaches the
        # underlying coroutine.
        await wrapped.ainvoke(
            {"command": "echo hi", "instance_id": "attacker-id"}
        )
        assert received == ["closure-iid"]

    async def test_wrapped_direct_call_preserves_explicit_instance_id(
        self, monkeypatch
    ):
        """Explicit ``instance_id`` from internal callers is preserved — the
        intended escape hatch for non-LLM paths (registered tool checks,
        shutdown helpers, etc.). This is NOT an LLM attack vector because
        the args_schema blocks the key from the LLM surface.
        """
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

        # Direct .coroutine bypasses args_schema validation — this is the
        # escape hatch. Internal callers can override the closure value.
        await wrapped.coroutine(command="echo hi", instance_id="shutdown-helper")
        assert received == ["shutdown-helper"]


# ──────────────────────────────────────────────────────────────────────
# m7 helper — top-level so any of the new TestInstanceIdAware tests can
# also be referenced individually without needing the test class handle.
# ──────────────────────────────────────────────────────────────────────


def test_bash_input_schema_does_not_advertise_instance_id():
    """Sanity: BashInputSchema.model_fields has no ``instance_id`` key.

    Combines with ``test_args_schema_does_not_expose_instance_id`` to
    document that ``instance_id`` is hidden from BOTH the LLM-facing args
    (StructuredTool.args) and the Pydantic schema used to validate LLM
    input.
    """
    from daemon.tools.bash import BashInputSchema

    assert "instance_id" not in BashInputSchema.model_fields
    # And nothing else surprises us:
    assert set(BashInputSchema.model_fields.keys()) == {
        "command",
        "timeout",
        "workdir",
        "input",
    }
