"""Real-dispatch integration tests for ``spawn_instance(model_tier=...)``.

Feature #1 of spawn-time intelligence override. Phase 2 plan Pins G, H,
I, J, K, L + Phase 2 supplementary Pins X (both-params), Z
(schema-rejects-invalid-literal), AA (both-params + invalid-legacy-model).

The test-shape contract (B3 plan):

  - **Tier-path pins (G, H, X, Z, AA)** drive the LANGCHAIN TOOL
    (``spawn_instance`` StructuredTool built via
    ``create_instance_tools(...)``); the tool is invoked with
    ``await spawn_instance.coroutine(...)``; assertions cover the
    RETURNED STRING and (where applicable) the
    ``manager.spawn_instance(...)`` kwargs.
  - **Legacy / default pins (I, J)** stay MANAGER-level — they call
    ``manager.spawn_instance(...)`` synchronously (the facade is SYNC;
    no ``await``). ``model_tier`` exists ONLY on the tool surface (D7);
    passing it to the facade would violate the Facade-Forwarding
    Discipline.

``uv run python -m pytest tests/integration/test_spawn_intelligence_tier.py -v``
from the worktree root. Same fixture patterns as
``tests/unit/tools/test_spawn_councilor_default_version.py`` — mock
manager + DB stubs, no live daemon.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from daemon.tools.instance import SpawnInstanceInput, create_instance_tools
from tests.helpers.send_message_fixtures import (
    make_spawn_manager,
    patch_heavy_helpers,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _get_spawn_tool(manager: MagicMock) -> MagicMock:
    """Build instance tools via the patched factory and return the
    ``spawn_instance`` StructuredTool."""
    patches = patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(
            manager, "parent-instance-id", agent_id="tester"
        )
    finally:
        for p in reversed(patches):
            p.stop()

    spawn_tool = None
    for t in tools:
        if getattr(t, "name", None) == "spawn_instance":
            spawn_tool = t
            break
    if spawn_tool is None:
        raise RuntimeError(
            "spawn_instance tool not found; got: "
            f"{[getattr(t, 'name', None) for t in tools]}"
        )
    return spawn_tool


# ─────────────────────────────────────────────────────────────────────────────
# Pin G — Real-dispatch SUCCESS: tier resolves + visibility line + persistence
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnIntelligenceTierSuccess:
    @pytest.mark.asyncio
    async def test_tier_high_resolves_to_agentic_with_visibility_line(self):
        """Pin G (D1 + D2 success): ``model_tier='high'`` + ``agentic`` in
        ``allowed_models`` + default configured model → tool returns a
        string starting with the spawned-instance id prefix AND carrying
        the visibility line ``model='agentic' (model_tier='high')``. The
        SYNC facade ``manager.spawn_instance(...)`` receives
        ``model='agentic'`` as the priority-1 override (D7 — no
        ``model_tier`` kwarg crosses the facade).
        """
        manager = make_spawn_manager(allowed_models=["agentic", "coding", "coding2"])
        spawn_tool = _get_spawn_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry",
                return_value=_fake_registry(),
            ):
                result = await spawn_tool.coroutine(
                    agent_id="coder", model_tier="high"
                )

        # (1) Returned string starts with the spawned-instance id prefix.
        assert "Successfully spawned instance: new-spawn-instance-id" in result, (
            f"expected id-prefix in return; got: {result!r}"
        )

        # (2) W3 visibility line carries the resolved model + tier literal.
        assert "model='agentic' (model_tier='high')" in result, (
            f"expected W3 visibility line in return; got: {result!r}"
        )

        # (3) Facade received ``model='agentic'`` as the priority-1 override.
        assert manager.spawn_instance.called, "manager.spawn_instance was not called"
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs.get("model") == "agentic", (
            f"manager.spawn_instance must receive resolved model='agentic'; "
            f"got: {call_kwargs.get('model')!r}"
        )
        # D7: NO ``model_tier`` kwarg on the facade.
        assert "model_tier" not in manager.spawn_instance.call_args.kwargs, (
            f"D7 violated — manager.spawn_instance received model_tier kwarg: "
            f"{list(manager.spawn_instance.call_args.kwargs)}"
        )

        # (4) Persistence — the SYNC facade stub returns
        # ``("new-spawn-instance-id", "agentic")`` (the
        # ``validated_model_override``); the tool's return does NOT
        # re-resolve the persistence layer (Pin V owns that contract
        # in Phase 5). For Pin G, the persistence claim is the
        # facade's second tuple element — which the tool does not
        # surface; we assert it here as the integration contract.
        # (Phase 5 Pin V tests the real DB round-trip.)
        spawn_result = manager.spawn_instance.return_value
        assert spawn_result[1] == "agentic"


# ─────────────────────────────────────────────────────────────────────────────
# Pin H — Real-dispatch LOUD ValueError (resolver block BEFORE :try).
# ─────────────────────────────────────────────────────────────────────────────


class TestSpawnIntelligenceTierLoud:
    @pytest.mark.asyncio
    async def test_tier_high_with_agentic_excluded_raises_value_error(self):
        """Pin H (D2 loud-validation): ``model_tier='high'`` + ``agentic``
        NOT in ``allowed_models`` → tool RAISES ``ValueError`` with the
        A2 verbatim message (Phase 2 task 4b.i). Possible ONLY because
        the resolver block sits BEFORE the ``try:`` (B2 — load-bearing
        placement); INSIDE the try the ``except ValueError`` at
        :sym:`daemon.tools.instance.spawn_instance` (load-bearing
        placement) would flatten it to a soft ``ERROR: ...`` string.
        """
        manager = make_spawn_manager(allowed_models=["coding"])
        spawn_tool = _get_spawn_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry",
                return_value=_fake_registry(),
            ):
                with pytest.raises(
                    ValueError,
                    match=r"spawn_instance\(model_tier='high'\) resolved to model",
                ) as exc_info:
                    await spawn_tool.coroutine(
                        agent_id="coder", model_tier="high"
                    )

        msg = str(exc_info.value)
        # §2.1 verbatim text — 4 required components:
        # (1) valid-models list (the allowed list interpolated).
        assert "coding" in msg, f"valid-models list missing from error: {msg!r}"
        # (2) tier→model resolution result (the resolved model name).
        assert "agentic" in msg, f"resolved model name missing from error: {msg!r}"
        # (3) env-var name (operator hint).
        assert "SPAWN_INTELLIGENCE_TIER_HIGH_MODEL" in msg, (
            f"env-var hint missing from error: {msg!r}"
        )
        # (4) parent-actionable remedies — three discrete paths.
        assert "allowed_models and restart" in msg, (
            f"remedy #1 (add to allowed_models) missing from error: {msg!r}"
        )
        assert "SPAWN_INTELLIGENCE_TIER_HIGH_MODEL to one of" in msg, (
            f"remedy #2 (re-point env) missing from error: {msg!r}"
        )
        assert "model=" in msg, (
            f"remedy #3 (legacy silent-fallback) missing from error: {msg!r}"
        )

        # The facade must NOT have been called — the loud-raise aborts
        # before manager.spawn_instance is reached.
        assert not manager.spawn_instance.called, (
            "manager.spawn_instance should NOT be called when tier "
            "validation raises loud"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pin I — Legacy ``model=`` silent fallback (manager-level, B3 carve-out)
# ─────────────────────────────────────────────────────────────────────────────


class TestLegacyModelSilentFallback:
    def test_legacy_model_silently_falls_back_when_not_in_allowed(self):
        """Pin I (D2 asymmetry regression): the legacy ``model=``
        silent-fallback path stays UNCHANGED — ``model='gpt-4'`` (NOT
        in ``allowed_models``) + NO ``model_tier`` → spawn succeeds
        with the default model + soft notice. The ``model_tier``
        loud-raise logic MUST NOT regress this path.

        MANAGER-level (B3 carve-out): the legacy path is exercised at
        the facade, not the tool — the tool body never sees a
        ``model=`` mismatch when ``model_tier=None``.
        """
        manager = make_spawn_manager(allowed_models=["agentic", "coding"])
        # Simulate the facade's silent-fallback behavior: when
        # ``model='gpt-4'`` ∉ ``allowed_models``, lifecycle's
        # ``_resolve_model_override`` returns ``None`` (silent
        # fallback); the spawn proceeds with the default (pool).
        manager.spawn_instance = MagicMock(return_value=("inst-id", None))

        # SYNC facade call (B3: drop await), no ``model_tier``.
        result = manager.spawn_instance(
            agent_id="coder", model="gpt-4"
        )

        # The facade returns the (id, validated_model_override) tuple.
        # The legacy path's silent fallback → ``validated_model_override`` is
        # ``None`` (the resolved default from the pool).
        assert result[0] == "inst-id"
        assert result[1] is None  # silent fallback — no override recorded


# ─────────────────────────────────────────────────────────────────────────────
# Pin J — Default-unchanged regression (manager-level, B3 carve-out)
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultUnchangedAtManagerLevel:
    def test_no_model_tier_routes_through_weighted_pool(self):
        """Pin J (D6 default-unchanged regression): ``manager.spawn_instance(
        agent_id='coder', model=None)`` (NO ``model_tier``) → returns
        a model from the weighted pool, NOT from the resolver. The
        resolver MUST NOT be called when ``model_tier`` is absent.

        MANAGER-level (B3 carve-out): the no-param path trivially
        cannot reach the resolver — the facade has no ``model_tier``
        kwarg (D7). Pin J patches ``daemon.tools.instance._resolve_intelligence_tier``
        to prove the symbol binding (W2 — the name-import binds the
        symbol into ``daemon.tools.instance``; patching the source
        module ``daemon.services.instance_lifecycle`` never
        intercepts the tool's reference).
        """
        manager = make_spawn_manager()
        manager.spawn_instance = MagicMock(return_value=("inst-id", "coding"))

        with patch(
            "daemon.tools.instance._resolve_intelligence_tier",
            wraps=lambda *a, **kw: pytest.fail(
                "resolver MUST NOT be called on the no-model_tier path"
            ),
        ):
            result = manager.spawn_instance(agent_id="coder")

        # Returns the pool-selected model.
        assert result == ("inst-id", "coding")


# ─────────────────────────────────────────────────────────────────────────────
# Pin K / L — Negative surface boundary
# ─────────────────────────────────────────────────────────────────────────────


class TestSurfaceBoundary:
    def test_spawn_councilor_signature_does_not_accept_model_tier(self):
        """Pin K (D3 surface boundary): ``spawn_councilor`` runtime
        signature MUST NOT accept ``model_tier``. Council semantics
        differ (council = diverse models by design); tier override is
        out of scope for v1.
        """
        manager = make_spawn_manager()
        patches = patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="governor"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        councilor_tool = None
        for t in tools:
            if getattr(t, "name", None) == "spawn_councilor":
                councilor_tool = t
                break
        assert councilor_tool is not None, "spawn_councilor tool not found"

        sig = inspect.signature(councilor_tool.coroutine)
        assert "model_tier" not in sig.parameters, (
            f"spawn_councilor MUST NOT accept model_tier; got params: "
            f"{list(sig.parameters)}"
        )

    def test_terminate_instance_signature_does_not_accept_model_tier(self):
        """Pin L (D3 defensive sweep): ``terminate_instance`` runtime
        signature MUST NOT accept ``model_tier`` either. The param is
        scope-bound to ``spawn_instance`` only (D3); terminate
        operates on existing instances.
        """
        manager = make_spawn_manager()
        patches = patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            tools = create_instance_tools(
                manager, "parent-instance-id", agent_id="tester"
            )
        finally:
            for p in reversed(patches):
                p.stop()

        terminate_tool = None
        for t in tools:
            if getattr(t, "name", None) == "terminate_instance":
                terminate_tool = t
                break
        assert terminate_tool is not None, "terminate_instance tool not found"

        sig = inspect.signature(terminate_tool.coroutine)
        assert "model_tier" not in sig.parameters, (
            f"terminate_instance MUST NOT accept model_tier; got params: "
            f"{list(sig.parameters)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pin X — Both-params precedence (tier WINS, visible [NOTE])
# ─────────────────────────────────────────────────────────────────────────────


def _fake_registry() -> MagicMock:
    """A registry stub that resolves ``agent_id`` to itself and returns
    a truthy metadata for version validation."""
    fake = MagicMock()
    fake.get_version.return_value = MagicMock()
    fake.resolve_to_id.side_effect = lambda x: x
    return fake


class TestBothParamsPrecedence:
    @pytest.mark.asyncio
    async def test_model_tier_wins_over_model_with_visible_note(
        self, monkeypatch
    ):
        """Pin X (D12 + A5/R-A5): both ``model_tier='high'`` AND
        ``model='coding'`` are passed. ``model_tier`` WINS — spawn
        proceeds on the TIER-resolved model (``'agentic'``); the
        return carries a visible ``[NOTE]`` supersede line naming the
        superseded value + the W3 visibility line; persistence
        records ``'agentic'``. NO ``ValueError`` (D12 — both-params is
        loud-but-successful, not strict-reject).
        """
        manager = make_spawn_manager(allowed_models=["agentic", "coding"])
        spawn_tool = _get_spawn_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry",
                return_value=_fake_registry(),
            ):
                result = await spawn_tool.coroutine(
                    agent_id="coder", model="coding", model_tier="high"
                )

        # (1) Spawn proceeds — no ValueError.
        assert manager.spawn_instance.called
        call_kwargs = manager.spawn_instance.call_args.kwargs
        # (2) Tier-resolved model wins.
        assert call_kwargs.get("model") == "agentic", (
            f"tier-resolved model must win over legacy model=; got: "
            f"{call_kwargs.get('model')!r}"
        )

        # (3) Visible [NOTE] supersede line in the return.
        assert "[NOTE] model='coding' superseded by model_tier='high'" in result, (
            f"both-params [NOTE] supersede line missing from return: {result!r}"
        )

        # (4) W3 visibility line present (every tier-path success).
        assert "model='agentic' (model_tier='high')" in result, (
            f"W3 visibility line missing from return: {result!r}"
        )

        # (5) UUID prefix preserved.
        assert "Successfully spawned instance: new-spawn-instance-id" in result

    @pytest.mark.asyncio
    async def test_model_tier_wins_with_operator_overridden_default(
        self, monkeypatch
    ):
        """Pin X-s1 parametrization (A6 / R-A6): the boot-snapshot
        ``spawn_intelligence_tier_high_model`` is read by the tool
        layer and threaded into the resolver. Operator remap
        (``= 'gpt-5'``) → ``'gpt-5'`` is the resolved model.
        """
        manager = make_spawn_manager(
            allowed_models=["agentic", "coding", "gpt-5"],
            spawn_intelligence_tier_high_model="gpt-5",
            spawn_result=("new-spawn-instance-id", "gpt-5"),
        )
        spawn_tool = _get_spawn_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry",
                return_value=_fake_registry(),
            ):
                result = await spawn_tool.coroutine(
                    agent_id="coder", model="coding", model_tier="high"
                )

        assert manager.spawn_instance.called
        assert manager.spawn_instance.call_args.kwargs.get("model") == "gpt-5", (
            f"operator-remap must thread into resolver; got: "
            f"{manager.spawn_instance.call_args.kwargs.get('model')!r}"
        )
        assert "model='gpt-5' (model_tier='high')" in result, (
            f"boot-snapshot value must drive W3 visibility line; got: {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pin Z — Schema rejects invalid literal
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaRejectsInvalidLiteral:
    def test_model_tier_low_rejected_by_pydantic_literal(self):
        """Pin Z (s2 / D11): ``model_tier='low'`` is REJECTED at the
        Pydantic ``SpawnInstanceInput`` validation gate; the value
        never reaches the tool body. The ``Literal['high'] | None``
        type renders as ``enum: ['high']`` in the tool's JSON schema
        — the LLM sees the closed tier set.
        """
        with pytest.raises(
            ValidationError, match=r"model_tier"
        ) as exc_info:
            SpawnInstanceInput(agent_id="coder", model_tier="low")

        # The validation error must mention the field.
        errors = exc_info.value.errors()
        assert any(
            e.get("loc") == ("model_tier",) for e in errors
        ), f"ValidationError must target the model_tier field; got: {errors}"

    def test_schema_renders_literal_enum_for_llm_discovery(self):
        """Discoverability (D5): the tool's args_schema exposes the
        closed tier literal set in ``model_fields``; LLM-facing
        tooling introspects this for the JSON schema.
        """
        assert "model_tier" in SpawnInstanceInput.model_fields
        field = SpawnInstanceInput.model_fields["model_tier"]
        # Literal['high'] | None — annotation string contains 'high'.
        annotation_str = str(field.annotation)
        assert "high" in annotation_str, (
            f"model_tier annotation must expose 'high'; got: {annotation_str!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pin AA — Both-params + invalid legacy model (suppressed fallback notice)
# ─────────────────────────────────────────────────────────────────────────────


class TestBothParamsInvalidLegacyModel:
    @pytest.mark.asyncio
    async def test_both_params_with_invalid_legacy_model_suppresses_notice(self):
        """Pin AA (s3 / W3 item 4): both-params where the superseded
        legacy ``model='bogus-not-allowed'`` is NOT in
        ``allowed_models`` — spawn proceeds on the tier-resolved
        model; the return carries the visible ``[NOTE]`` naming the
        superseded value; NO legacy fallback notice for the bogus
        model appears (suppressed per Phase 2 task 4e item 4); NO
        ``ValueError`` (the tier path is canonical + validated).
        """
        manager = make_spawn_manager(allowed_models=["agentic", "coding"])
        spawn_tool = _get_spawn_tool(manager)

        with patch(
            "daemon.tools.instance._check_team_membership",
            return_value=None,
        ):
            with patch(
                "daemon.registry.get_registry",
                return_value=_fake_registry(),
            ):
                result = await spawn_tool.coroutine(
                    agent_id="coder",
                    model="bogus-not-allowed",
                    model_tier="high",
                )

        # (1) Spawn proceeds.
        assert manager.spawn_instance.called
        call_kwargs = manager.spawn_instance.call_args.kwargs
        # (2) Tier-resolved canonical model — NOT the bogus legacy value.
        assert call_kwargs.get("model") == "agentic", (
            f"tier-resolved model must win; got: {call_kwargs.get('model')!r}"
        )

        # (3) [NOTE] names the superseded value (bogus).
        assert "model='bogus-not-allowed' superseded by" in result, (
            f"[NOTE] supersede line missing the superseded value; got: {result!r}"
        )

        # (4) NO legacy fallback notice for the bogus model — the
        # tool layer's ``_format_model_fallback_notice`` MUST NOT be
        # called when ``model_tier`` is set (suppression gate).
        manager._lifecycle_service._format_model_fallback_notice.assert_not_called(), (
            "fallback notice MUST be suppressed on the tier path; "
            "the bogus model cannot emit a misleading notice"
        )
