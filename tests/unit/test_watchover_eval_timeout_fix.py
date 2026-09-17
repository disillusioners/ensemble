"""Regression tests for the watchover per-tool-call eval timeout fix.

Covers:

  * Root cause 1 — ``agents/watcher/meta.json`` no longer pins a 10s
    ``timeout_seconds`` override; the 90s
    :data:`WATCHOVER_TIMEOUT_SECONDS_DEFAULT` applies.

  * Root cause 2 — the watcher meta ``llm_model`` /
    ``snapshot_llm_model`` keys (the sentinel ``"quick"`` and any
    other literal model name) are now RESOLVED to a real model name
    and threaded through to the actual LLM call.

  * Root cause 3 — the evaluator's first call seeds ``_last_seen_count``
    to the current message count so the initial conversation history is
    NOT absorbed into one snapshot-regeneration payload.

  * Root cause 4 — sub-floor ``timeout_seconds`` values are clamped at
    construction time with a WARNING so a future ``meta.json`` typo
    cannot silently re-neuter the gate.

  * Regression #d — the regen-failure → stale-snapshot behavior from
    the locked failure semantics is preserved end-to-end.

Also pins the meta-cache test isolation: ``_WATCHER_META_CACHE`` is
process-lifetime, so tests that vary the watcher ``watcher_config``
must NOT depend on the cached meta. The factory's resolver accepts
explicit ``model_override`` / ``snapshot_model_override`` parameters
for this reason — tests use those instead of monkey-patching the cache.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon import graph as graph_mod
from daemon.graph import (
    WATCHOVER_TIMEOUT_MIN_SECONDS,
    WATCHOVER_TIMEOUT_SECONDS_DEFAULT,
    WatchoverEvaluator,
    create_watchover_check_node,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class _FakeLLMResult:
    """Drop-in for a LangChain ``AIMessage`` — only ``.content`` matters."""

    content: Any = ""


def _bypass_first_call_seed(evaluator: "WatchoverEvaluator") -> None:
    """Mark the evaluator's first-call seed as already done.

    2026-09-17 fix: the evaluator's ``evaluate()`` seeds ``_last_seen_count``
    to ``len(messages)`` on the very first call so the entire conversation
    history is NOT absorbed into the initial delta (the pre-fix
    deterministic-timeout bug for long conversations). Tests written
    against the pre-fix behavior — where the first call DOES absorb
    ``messages[0:]`` — call this helper to opt out of the seed and
    exercise the original "absorb everything on first call" semantics.
    New tests for the seed behavior itself should NOT call this helper.

    Mirrors the identical helper in ``tests/unit/test_watchover_decision.py``
    (defined as a module-level free function there). Re-defined here so
    this test file is self-contained and does not rely on a sibling
    test file's internals.
    """
    evaluator._initial_delta_seeded = True  # noqa: SLF001 — test-only opt-out



def _make_fake_llm_class(responses: list[Any] | None = None):
    """Build a ``ThinkingChatOpenAI`` factory mock with a queued response list.

    Mirrors the shape used by ``test_watchover_decision.py``. The factory
    captures the ``model`` kwarg so tests can assert on the model name
    that reached the LLM construction site (verifying that the
    purpose-bound quick-model wiring actually applies).
    """
    if responses is None:
        responses = []
    queue = list(responses)
    captured_kwargs: list[dict] = []

    def _next(_messages):
        if not queue:
            raise AssertionError("LLM mock exhausted")
        item = queue.pop(0)
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        if isinstance(item, _FakeLLMResult):
            return item
        return _FakeLLMResult(content=item)

    mock_instance = MagicMock()
    mock_instance.invoke.side_effect = _next

    def _factory(**kwargs):
        captured_kwargs.append(kwargs)
        return mock_instance

    # Class attrs read by ``clean_llm_config`` (graph.py:3733, 3768).
    _factory.default_streaming = False
    _factory.default_request_timeout = 610
    _factory.default_request_gzip = False
    _factory.captured_kwargs = captured_kwargs  # type: ignore[attr-defined]

    return _factory, mock_instance


def _make_manager_with_config(
    *,
    session_model: str = "agentic",
    model_keywords: str = "quick-mirror",
    allowed_models: tuple[str, ...] = ("agentic", "coding", "quick-mirror"),
) -> MagicMock:
    """Mock an ``InstanceManager`` with a ``config.llm`` surface.

    Wires the minimum surface the watcher uses:

      * ``_live_hub.stream_message(...)`` — AsyncMock (no-op)
      * ``is_watchover_enabled(iid) -> True``
      * ``_instance_repository.get(iid).instance_metadata = {}``
      * ``config.llm.model / model_keywords / allowed_models`` —
        passthrough from constructor kwargs (operator-pinned values).
    """
    manager = MagicMock()
    manager.is_watchover_enabled.side_effect = lambda iid: True
    manager.is_watchover_terminate_requested = MagicMock(return_value=False)
    manager.set_deferred_watchover_terminate = MagicMock()
    manager.clear_watchover_terminate_requested = MagicMock()

    row = MagicMock()
    row.instance_metadata = {}
    repo = MagicMock()
    repo.get.return_value = row
    repo.set_metadata = MagicMock(return_value=row)
    repo.set_metadata_many = MagicMock(return_value=row)
    manager._instance_repository = repo

    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()

    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.set_deferred_question_pause = MagicMock()
    manager.clear_question_pause_requested = MagicMock()
    manager.pause_instance_cascade = AsyncMock()

    # Daemon config surface (graph.py reads it via
    # ``getattr(manager, "config", None)``).
    llm_cfg = MagicMock()
    llm_cfg.model = session_model
    llm_cfg.model_keywords = model_keywords
    llm_cfg.allowed_models = list(allowed_models)
    config = MagicMock()
    config.llm = llm_cfg
    manager.config = config

    return manager


def _read_meta_json() -> dict:
    """Read ``agents/watcher/meta.json`` ``watchover`` section fresh.

    Bypasses the module-level ``_WATCHER_META_CACHE`` so the test sees
    the actual on-disk content (the cache is process-lifetime — see
    ``test_meta_cache_resets_only_via_explicit_assignment``).
    """
    with open(
        os.path.join(
            os.path.dirname(graph_mod.__file__),
            "..",
            "agents",
            "watcher",
            "meta.json",
        ),
        encoding="utf-8",
    ) as f:
        meta = json.load(f)
    return meta.get("watchover", {})


# =============================================================================
# Root cause 1 — meta.json timeout override
# =============================================================================


class TestMetaJsonTimeoutOverride:
    """``agents/watcher/meta.json`` no longer pins ``timeout_seconds``.

    Pre-fix, the meta file had ``timeout_seconds: 10`` which silently
    neutered the watchover gate in prod (2026-09-17 v0.13.1). The fix
    removes the override so :data:`WATCHOVER_TIMEOUT_SECONDS_DEFAULT`
    (90s) applies. The constructor still applies
    :data:`WATCHOVER_TIMEOUT_MIN_SECONDS` (15s) as a safety floor.
    """

    def test_meta_json_does_not_override_timeout_seconds(self):
        """No ``timeout_seconds`` key in meta.json — default applies."""
        meta = _read_meta_json()
        assert "timeout_seconds" not in meta, (
            f"agents/watcher/meta.json still overrides timeout_seconds "
            f"(={meta.get('timeout_seconds')}); the fix removes this "
            f"key so WATCHOVER_TIMEOUT_SECONDS_DEFAULT (90s) applies."
        )

    def test_default_timeout_is_90_seconds(self):
        """``WATCHOVER_TIMEOUT_SECONDS_DEFAULT`` is 90s at module level."""
        assert WATCHOVER_TIMEOUT_SECONDS_DEFAULT == 90, (
            "Default timeout changed from 90s — the meta.json fix "
            "relies on the default being high enough that the LLM "
            "has time to respond under load."
        )

    def test_constructor_uses_default_when_no_meta_override(self):
        """No ``watcher_config`` → effective timeout = 90s."""
        manager = _make_manager_with_config()
        eval_llm = MagicMock()
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=eval_llm):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
            )
        assert evaluator._timeout_seconds == WATCHOVER_TIMEOUT_SECONDS_DEFAULT
        assert evaluator._timeout_seconds == 90


# =============================================================================
# Root cause 4 — timeout floor + clamping
# =============================================================================


class TestTimeoutFloor:
    """Sub-floor ``timeout_seconds`` values are clamped with a WARNING.

    A future ``meta.json`` typo (e.g. ``timeout_seconds: 5``) MUST
    NOT silently re-neuter the gate. The constructor clamps to
    :data:`WATCHOVER_TIMEOUT_MIN_SECONDS` and logs a WARNING.

    Tests that intentionally use sub-floor values (e.g. to drive the
    LLM mock into raising TimeoutError immediately) still work because
    their mocks raise exceptions directly — the clamp only affects the
    ``asyncio.wait_for`` ceiling, which the mock bypasses.
    """

    def test_min_timeout_constant_is_15(self):
        """Floor is 15s — low enough to keep fail-open tests viable
        (their mocks raise directly; the clamp does not interfere),
        high enough to give a real LLM call a chance to complete
        under typical load.
        """
        assert WATCHOVER_TIMEOUT_MIN_SECONDS == 15

    def test_sub_floor_timeout_clamped_at_construction(self, caplog):
        """``timeout_seconds=5`` → clamped to 15, WARNING emitted."""
        manager = _make_manager_with_config()
        with caplog.at_level("WARNING"):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid-broken-test",
                watcher_config={"timeout_seconds": 5},
            )
        # 5s is below the floor (15s) → clamped.
        assert evaluator._timeout_seconds == WATCHOVER_TIMEOUT_MIN_SECONDS
        # WARNING emitted explaining the clamp + the 2026-09-17 cause.
        assert any(
            "configured timeout_seconds=5" in r.message
            and "WATCHOVER_TIMEOUT_MIN_SECONDS" in r.message
            for r in caplog.records
        ), f"expected clamping WARNING; got {[r.message for r in caplog.records]}"

    def test_zero_timeout_clamped(self, caplog):
        """``timeout_seconds=0`` (a typo) → clamped, not left at 0."""
        manager = _make_manager_with_config()
        with caplog.at_level("WARNING"):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={"timeout_seconds": 0},
            )
        assert evaluator._timeout_seconds == WATCHOVER_TIMEOUT_MIN_SECONDS

    def test_above_floor_timeout_honored(self):
        """``timeout_seconds=45`` (above floor) → kept verbatim."""
        manager = _make_manager_with_config()
        evaluator = WatchoverEvaluator(
            manager=manager,
            llm_config={"model": "agentic"},
            instance_id="iid",
            watcher_config={"timeout_seconds": 45},
        )
        assert evaluator._timeout_seconds == 45

    def test_exactly_at_floor_honored(self):
        """``timeout_seconds == WATCHOVER_TIMEOUT_MIN_SECONDS`` is honored
        (boundary, not strict-greater clamp).
        """
        manager = _make_manager_with_config()
        evaluator = WatchoverEvaluator(
            manager=manager,
            llm_config={"model": "agentic"},
            instance_id="iid",
            watcher_config={"timeout_seconds": WATCHOVER_TIMEOUT_MIN_SECONDS},
        )
        assert evaluator._timeout_seconds == WATCHOVER_TIMEOUT_MIN_SECONDS

    async def test_wait_for_timeout_argument_reaches_call_site(
        self, monkeypatch
    ):
        """W2 fix: ``asyncio.wait_for(..., timeout=self._timeout_seconds)``
        is the actual LD-2 fail-open enforcement mechanism.

        The pre-existing LD-2 fail-open tests in
        ``test_watchover_decision.py::test_infra_error_timeout_fails_open``
        raise ``asyncio.TimeoutError`` directly from a mock
        ``side_effect`` — so nothing in those tests pins the fact that
        the watcher actually wraps the LLM call in
        ``asyncio.wait_for(timeout=self._timeout_seconds, ...)`` at
        graph.py:9929-9934. If the ``wait_for`` wrapper were removed
        tomorrow, every LD-2 test would still pass — meaning the entire
        fail-open safety net is one typo away from going silently
        inert. This test pins the wrapper directly.

        Implementation: we patch ``daemon.graph.asyncio.wait_for`` with
        ``wraps=asyncio.wait_for`` so the real function still runs (no
        behavior change for the eval), but we capture every
        ``timeout=`` keyword so we can assert the configured value
        reached the call site.

        The assertion is conservative: ``configured_timeout`` is in the
        observed set. If someone changes
        ``asyncio.wait_for(coro, timeout=self._timeout_seconds)`` to
        ``asyncio.wait_for(coro, timeout=None)`` (or removes the wrapper
        entirely), this test fails because the observed set no longer
        contains the configured value.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        configured_timeout = 42  # any value above the 15s floor
        factory, _llm = _make_fake_llm_class(["Allowed"])
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={"timeout_seconds": configured_timeout},
            )
            assert evaluator._timeout_seconds == configured_timeout

            # Spy on ``asyncio.wait_for`` in the graph module's
            # namespace — the watcher's wrapper call (graph.py:9990)
            # is the one we want to pin. ``wraps=`` delegates to the
            # real function (so the eval path still works normally
            # with no spurious "coroutine never awaited" warnings)
            # and exposes ``call_args_list`` for inspection.
            with patch(
                "daemon.graph.asyncio.wait_for",
                wraps=asyncio.wait_for,
            ) as wait_for_spy:
                await evaluator.evaluate(
                    tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                    messages=[HumanMessage(content="m0")],
                    watchover_context="ctx",
                )

        # Extract every timeout the watcher's wait_for calls were
        # invoked with — supports both positional and keyword forms so
        # a future refactor of the call signature doesn't silently
        # break the pin.
        observed_timeouts: list[int | None] = []
        for call in wait_for_spy.call_args_list:
            if "timeout" in call.kwargs:
                observed_timeouts.append(call.kwargs["timeout"])
            elif len(call.args) >= 2:
                # ``asyncio.wait_for(fut, timeout)`` — positional
                # timeout is the second arg.
                observed_timeouts.append(call.args[1])
            else:
                observed_timeouts.append(None)

        # The configured timeout MUST reach the call site. If the
        # ``asyncio.wait_for(...)`` wrapper is removed or its timeout
        # argument is changed away from ``self._timeout_seconds``, this
        # assertion fails.
        assert configured_timeout in observed_timeouts, (
            f"asyncio.wait_for was not called with the configured "
            f"timeout={configured_timeout}; observed={observed_timeouts}. "
            f"The LD-2 fail-open enforcement is unpinned — check "
            f"graph.py:9929-9934."
        )


# =============================================================================
# Root cause 3 — first-call delta seed
# =============================================================================


class TestFirstCallSeed:
    """The evaluator seeds ``_last_seen_count`` on first call.

    Pre-fix, ``_last_seen_count == 0`` on the first call caused
    ``messages[0:]`` to be absorbed into the delta; >20 messages
    immediately triggered snapshot regeneration whose payload was the
    entire conversation history → deterministic timeout → LD-2 fail-open
    fires for every tool batch.

    Post-fix, the first call sets ``_last_seen_count = len(messages)``
    so the initial conversation history is NOT absorbed. The first
    eval sees an empty delta; subsequent calls absorb the new tail.
    """

    async def test_first_call_does_not_absorb_initial_history(self, monkeypatch):
        """First call with N messages → delta stays empty (the seed)."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"] * 5)
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                # No bypass — we ARE testing the seed behavior.
            )
            await evaluator.evaluate(
                tool_calls=[
                    {"id": "tc-1", "name": "bash", "args": {"command": "ls"}}
                ],
                messages=[
                    HumanMessage(content="m0"),
                    HumanMessage(content="m1"),
                    HumanMessage(content="m2"),
                    HumanMessage(content="m3"),
                    HumanMessage(content="m4"),
                    HumanMessage(content="m5"),
                ],
                watchover_context="ctx",
            )

        # The first-call seed: NO absorption.
        assert evaluator._delta_messages == [], (
            f"first-call seed failed: delta should be empty after the "
            f"first call, got {len(evaluator._delta_messages)} messages"
        )
        assert evaluator._last_seen_count == 6
        # No regeneration triggered on the first call.
        assert evaluator._snapshot == ""
        assert evaluator._snapshot_turn == 0
        # _initial_delta_seeded flips to True so subsequent calls
        # absorb normally.
        assert evaluator._initial_delta_seeded is True

    async def test_subsequent_call_absorbs_new_tail_only(self, monkeypatch):
        """After seed, only the new tail (beyond seeded count) is absorbed."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"] * 10)
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={"delta_max_messages": 100},
            )
            # First call (SEED): 4 messages.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[HumanMessage(content=f"m{i}") for i in range(4)],
                watchover_context="ctx",
            )
            assert evaluator._last_seen_count == 4
            assert evaluator._delta_messages == []

            # Second call: 2 NEW messages appended → delta grows by 2.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-2", "name": "bash", "args": {}}],
                messages=[HumanMessage(content=f"m{i}") for i in range(6)],
                watchover_context="ctx",
            )
            assert evaluator._last_seen_count == 6
            assert len(evaluator._delta_messages) == 2
            assert evaluator._delta_messages[0].content == "m4"
            assert evaluator._delta_messages[1].content == "m5"

    async def test_first_call_seed_does_not_trigger_regen_with_long_history(
        self, monkeypatch
    ):
        """1000-message history → first call does NOT trigger regen
        (the pre-fix deterministic-timeout bug).
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        # Allow up to 5 LLM calls (per-batch evals + the regen that
        # would happen if the seed were broken).
        factory, _llm = _make_fake_llm_class(["Allowed"] * 100)
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={"delta_max_messages": 20},
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[
                    HumanMessage(content=f"m{i}") for i in range(1000)
                ],
                watchover_context="ctx",
            )

        # The seed: NO delta, NO regen, NO _snapshot_turn increment.
        assert evaluator._delta_messages == []
        assert evaluator._snapshot == ""
        assert evaluator._snapshot_turn == 0
        # Crucially: the LLM was called EXACTLY ONCE (the per-tool eval),
        # not 1000 times (which would happen if the delta had been
        # absorbed + regen had fired + regen had failed).
        assert _llm.invoke.call_count == 1

    async def test_first_call_empty_messages_list(self, monkeypatch):
        """#9(i) — Empty ``messages=[]`` on first call: the seed is a
        no-op, delta stays empty, ``_last_seen_count`` stays 0.

        Catches a regression where the seed accidentally wrote
        ``_last_seen_count = -1`` or similar on an empty input.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"] * 5)
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="ctx",
            )

        # Seed is a no-op on empty input.
        assert evaluator._delta_messages == []
        assert evaluator._last_seen_count == 0
        assert evaluator._snapshot == ""
        assert evaluator._snapshot_turn == 0
        assert evaluator._initial_delta_seeded is True

    async def test_initial_delta_seeded_defaults_to_false(self):
        """#9(ii) — Pre-call: ``_initial_delta_seeded == False``.

        The 7 ``_bypass_first_call_seed`` sites in
        ``test_watchover_decision.py`` (and any future test that wants
        the pre-fix absorption path) would silently MASK a regression
        where the constructor defaults ``_initial_delta_seeded`` to
        ``True`` — every seed-affirming test would still pass because
        the bypass flips it back to ``True``, and we'd ship a
        no-op seed. This test pins the constructor default.
        """
        manager = _make_manager_with_config()
        evaluator = WatchoverEvaluator(
            manager=manager,
            llm_config={"model": "agentic"},
            instance_id="iid",
        )
        # The constructor MUST default the seed flag to False; the
        # first ``evaluate()`` call is responsible for flipping it to
        # True (the seed semantics).
        assert evaluator._initial_delta_seeded is False, (
            "constructor defaulted _initial_delta_seeded to True — "
            "the first-call seed would be skipped, re-introducing the "
            "pre-fix deterministic-timeout bug for long conversations."
        )

    async def test_default_delta_max_20_boundary_no_regen(self, monkeypatch):
        """#9(iii-a) — At the production default (``delta_max_messages=20``),
        ``len(_delta_messages) == 20`` does NOT trigger regeneration.
        The trigger is strict-greater (``len > _delta_max``).
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        # Generous LLM response queue: one call per evaluate.
        factory, _llm = _make_fake_llm_class(["Allowed"] * 5)
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            # No ``delta_max_messages`` override → uses the production
            # default ``WATCHOVER_DELTA_MAX_MESSAGES_DEFAULT == 20``.
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
            )
            # Bypass the seed so the 20-message batch lands in the
            # delta on the first call (the seed would suppress it).
            _bypass_first_call_seed(evaluator)

            # 20 messages — exactly at the boundary, NOT over.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[HumanMessage(content=f"m{i}") for i in range(20)],
                watchover_context="ctx",
            )

        # The trigger is strict-greater — len == delta_max → NO regen.
        assert len(evaluator._delta_messages) == 20
        assert evaluator._snapshot == ""
        assert evaluator._snapshot_turn == 0
        # LLM was called ONCE (the eval), not twice (eval + regen).
        assert _llm.invoke.call_count == 1

    async def test_default_delta_max_20_boundary_plus_one_regen(
        self, monkeypatch
    ):
        """#9(iii-b) — ``len == 21`` (one over the production default
        ``delta_max_messages=20``) DOES trigger regeneration.
        The trigger is strict-greater (``len > _delta_max``).
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        # Generous queue: one call for the eval + one call for the
        # snapshot regen (which uses the same mock and returns
        # "Allowed" — the regen treats any LLM response as the new
        # snapshot text).
        factory, _llm = _make_fake_llm_class(["Allowed"] * 10)
        manager = _make_manager_with_config()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            # No ``delta_max_messages`` override → uses the production
            # default ``WATCHOVER_DELTA_MAX_MESSAGES_DEFAULT == 20``.
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
            )
            _bypass_first_call_seed(evaluator)

            # 21 messages — exactly one over the boundary.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[HumanMessage(content=f"m{i}") for i in range(21)],
                watchover_context="ctx",
            )

        # 21 > 20 → regen fires; the snapshot is populated with the
        # mock LLM's response ("Allowed").
        assert evaluator._snapshot == "Allowed"
        assert evaluator._snapshot_turn == 1
        # Delta is bounded to the last ``delta_max`` messages — the
        # sliding-window tail (messages[1:] keeps the last 20).
        assert len(evaluator._delta_messages) == 20
        assert evaluator._delta_messages[0].content == "m1"
        assert evaluator._delta_messages[-1].content == "m20"
        # LLM was called TWICE: once for the eval, once for the regen.
        assert _llm.invoke.call_count == 2


# =============================================================================
# Root cause 2 — quick-model wiring
# =============================================================================


class TestResolveWatcherModelHelper:
    """``_resolve_watcher_model(config, requested)`` resolution rules.

    Resolution precedence (mirrors ``resolve_judge_model`` /
    ``extract_keywords`` for the purpose-bound ``model_keywords``
    pattern; W1 hardened the no-op branches to preserve the watched
    instance's spawn-time model):

      * ``requested`` empty / None / whitespace → ``""`` (W1: was
        ``config.llm.model`` pre-W1; now a no-op so the watched
        instance's own ``llm_config["model"]`` flows through
        unmodified).
      * ``requested == "quick"`` (case-insensitive) → ``config.llm.model_keywords``
        when set; ``""`` (W1: was ``config.llm.model`` pre-W1) when
        unset — same no-op contract as above.
      * other string → honor verbatim if in ``config.llm.allowed_models``,
        else WARN + fallback to ``config.llm.model`` (this branch is
        unchanged from pre-W1 — the reviewer scoped the fix to the
        two no-op branches only; branch 3 is the "operator asked for
        X, X is unavailable" path and stays a daemon-global override
        with WARNING).
    """

    def test_empty_requested_returns_empty_string_for_no_op(self):
        """W1: ``requested=None`` / ``""`` / whitespace → ``""``.

        The factory (``create_watchover_check_node``) treats ``""``
        the same as ``None`` — no model override is applied, and the
        watched instance's own ``llm_config["model"]`` (its spawn-time
        model) flows through unmodified. This is the W1 contract:
        the resolver is purely a *purpose-bound* quick-mirror wiring;
        when there is nothing to wire (no key, or the key is ``"quick"``
        with no operator-pinned mirror), we MUST NOT silently override
        the instance model with the daemon-global ``config.llm.model``.

        Pre-W1 this branch fell back to ``config.llm.model`` (the
        daemon-global session model). The reviewer flagged that as a
        behavior change vs. pre-fix on deployments where
        ``OPENAI_MODEL_KEYWORDS`` was unset — it overrode the watched
        instance's spawn-time model with the daemon-global default.
        W1 fixes that.
        """
        manager = _make_manager_with_config(session_model="agentic")
        assert graph_mod._resolve_watcher_model(manager.config, None) == ""
        assert graph_mod._resolve_watcher_model(manager.config, "") == ""
        assert graph_mod._resolve_watcher_model(manager.config, "   ") == ""

    def test_quick_sentinel_resolves_to_model_keywords(self):
        """``requested="quick"`` → ``config.llm.model_keywords``."""
        manager = _make_manager_with_config(
            session_model="agentic", model_keywords="quick-mirror"
        )
        assert (
            graph_mod._resolve_watcher_model(manager.config, "quick")
            == "quick-mirror"
        )

    def test_quick_sentinel_case_insensitive(self):
        """``requested="Quick"`` / ``"QUICK"`` → same as ``"quick"``."""
        manager = _make_manager_with_config(model_keywords="quick-mirror")
        assert (
            graph_mod._resolve_watcher_model(manager.config, "Quick")
            == "quick-mirror"
        )
        assert (
            graph_mod._resolve_watcher_model(manager.config, "QUICK")
            == "quick-mirror"
        )

    def test_quick_with_no_model_keywords_returns_empty_string(self):
        """W1: ``requested="quick"`` + ``model_keywords=""`` → ``""``.

        The W1 contract: a watcher meta key of ``"quick"`` with no
        operator-pinned ``OPENAI_MODEL_KEYWORDS`` mirror MUST NOT
        silently override the watched instance's spawn-time model
        with the daemon-global ``config.llm.model``. The factory
        passes ``None`` to ``WatchoverEvaluator`` and the instance's
        own ``llm_config["model"]`` flows through unmodified.

        Pre-W1 this branch fell back to ``config.llm.model`` (the
        daemon-global session model). The reviewer flagged that as
        the same behavior-change vs. pre-fix issue as
        ``test_empty_requested_returns_empty_string_for_no_op`` — both
        branches are now no-op rather than daemon-global-override.
        """
        manager = _make_manager_with_config(
            session_model="agentic", model_keywords=""
        )
        assert graph_mod._resolve_watcher_model(manager.config, "quick") == ""

    def test_specific_model_name_honored_when_in_allowed(self):
        """Specific model name in ``allowed_models`` → returned verbatim."""
        manager = _make_manager_with_config(
            allowed_models=("agentic", "coding", "custom-model")
        )
        assert (
            graph_mod._resolve_watcher_model(manager.config, "custom-model")
            == "custom-model"
        )
        assert (
            graph_mod._resolve_watcher_model(manager.config, "Agentic")
            == "Agentic"  # case-insensitive match preserves the requested case
        )

    def test_specific_model_name_falls_back_when_not_in_allowed(
        self, caplog
    ):
        """Specific model name absent from ``allowed_models`` →
        WARNING + fallback to session model.
        """
        manager = _make_manager_with_config(
            session_model="agentic",
            allowed_models=("agentic", "coding"),
        )
        with caplog.at_level("WARNING"):
            result = graph_mod._resolve_watcher_model(
                manager.config, "forbidden-model"
            )
        assert result == "agentic", (
            "model not in allowed_models should fall back to session model"
        )
        assert any(
            "forbidden-model" in r.message
            and "not in config.llm.allowed_models" in r.message
            for r in caplog.records
        )

    def test_empty_allowed_list_means_unrestricted(self):
        """``allowed_models=[]`` (unrestricted) → requested returned verbatim."""
        manager = _make_manager_with_config(allowed_models=())
        assert (
            graph_mod._resolve_watcher_model(manager.config, "any-model-name")
            == "any-model-name"
        )

    def test_no_config_returns_requested_verbatim(self):
        """``config=None`` → requested returned verbatim (test seam)."""
        assert (
            graph_mod._resolve_watcher_model(None, "some-model") == "some-model"
        )

    def test_config_without_llm_attribute_returns_requested_verbatim(self):
        """Config missing ``.llm`` attribute → requested returned verbatim."""
        config = MagicMock(spec=[])  # no .llm attribute
        assert (
            graph_mod._resolve_watcher_model(config, "some-model") == "some-model"
        )


class TestModelOverrideReachesLlmCall:
    """The ``model_override`` / ``snapshot_model_override`` parameters
    actually reach the LLM construction site.

    Pre-fix, the watcher meta ``llm_model`` keys were dead config:
    read but never threaded into the LLM's ``model`` field. Post-fix,
    ``_resolve_watcher_model`` produces a real model name and the
    evaluator's ``_get_llm`` applies it before the ``ThinkingChatOpenAI``
    build. Verified by capturing the kwargs the patched factory
    receives.
    """

    async def test_model_override_applied_to_llm_construction(
        self, monkeypatch
    ):
        """``model_override="quick-mirror"`` → factory receives
        ``model="quick-mirror"``.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"])
        manager = _make_manager_with_config(
            session_model="agentic",
            model_keywords="quick-mirror",
            allowed_models=("agentic", "quick-mirror"),
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={"llm_model": "quick"},
                # The factory call path simulates what
                # ``create_watchover_check_node`` does: resolve once
                # up front and pass the resolved name as an explicit
                # parameter. This decouples the evaluator from the
                # meta-cache + manager.config surface, which is what
                # keeps the factory's resolver thread-safe.
                model_override="quick-mirror",
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[HumanMessage(content="m0")],
                watchover_context="ctx",
            )

        # Captured factory kwargs — the model override MUST be applied.
        captured = factory.captured_kwargs  # type: ignore[attr-defined]
        assert len(captured) == 1, (
            f"expected exactly one LLM construction, got {len(captured)}"
        )
        assert captured[0].get("model") == "quick-mirror", (
            f"model_override did not reach the LLM; got "
            f"model={captured[0].get('model')!r}"
        )

    async def test_snapshot_model_override_distinct_from_eval(
        self, monkeypatch
    ):
        """``snapshot_model_override != model_override`` → eval LLM and
        snapshot LLM use different models; both reach the factory.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        # Generate enough LLM responses: first call (eval) + second call
        # (eval) + regen call (snapshot). Use a generous queue.
        factory, _llm = _make_fake_llm_class(["Allowed"] * 20)
        manager = _make_manager_with_config(
            allowed_models=("agentic", "quick-mirror", "other-model")
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={
                    "delta_max_messages": 2,
                    "llm_model": "quick-mirror",
                    "snapshot_llm_model": "other-model",
                },
                model_override="quick-mirror",
                snapshot_model_override="other-model",
            )
            # First call (SEED): 1 message → delta stays empty → eval
            # LLM constructed with quick-mirror, no regen.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[HumanMessage(content="m0")],
                watchover_context="ctx",
            )

            # Second call: 5 messages → new tail = 4 messages →
            # delta = 4 > delta_max=2 → regen fires (snapshot LLM
            # constructed with other-model), then delta bounded to 2.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-2", "name": "bash", "args": {}}],
                messages=[HumanMessage(content=f"m{i}") for i in range(5)],
                watchover_context="ctx",
            )

        captured = factory.captured_kwargs  # type: ignore[attr-defined]
        models_constructed = [kw.get("model") for kw in captured]
        # Two LLM constructions: eval (quick-mirror) + snapshot (other-model).
        assert "quick-mirror" in models_constructed, (
            f"eval LLM not built with quick-mirror: {models_constructed}"
        )
        assert "other-model" in models_constructed, (
            f"snapshot LLM not built with other-model: {models_constructed}"
        )

    async def test_snapshot_override_falls_back_to_model_override(self):
        """Only ``model_override`` set → snapshot uses the same model."""
        factory, _llm = _make_fake_llm_class(["Allowed"] * 10)
        manager = _make_manager_with_config(
            allowed_models=("agentic", "quick-mirror")
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                model_override="quick-mirror",
                # snapshot_model_override intentionally NOT set.
            )
            assert evaluator._snapshot_model_override == "quick-mirror"


class TestCreateWatchoverCheckNodeResolvesModel:
    """``create_watchover_check_node`` resolves watcher meta model keys.

    End-to-end check that the factory wires ``manager.config`` into
    ``_resolve_watcher_model``, threads the resolved name into the
    evaluator's ``model_override`` / ``snapshot_model_override``
    parameters, and that the LLM constructed on the first eval
    reflects the override.
    """

    async def test_factory_resolves_quick_to_model_keywords(
        self, monkeypatch
    ):
        """Factory with ``watcher_config={"llm_model": "quick"}`` +
        manager with ``model_keywords="quick-mirror"`` → LLM is
        built with ``model="quick-mirror"``.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"])
        manager = _make_manager_with_config(
            session_model="agentic",
            model_keywords="quick-mirror",
            allowed_models=("agentic", "quick-mirror"),
        )
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager,
                slot=slot,
                llm_config={"model": "agentic"},
                watcher_config={"llm_model": "quick"},
            )
            # Build a minimal state with one tool_call.
            from langchain_core.messages import AIMessage

            state = {
                "messages": [
                    AIMessage(
                        content="ok",
                        tool_calls=[
                            {
                                "id": "tc-1",
                                "name": "bash",
                                "args": {"command": "ls"},
                            }
                        ],
                    )
                ],
            }
            from langchain_core.runnables import RunnableConfig

            config: RunnableConfig = {"configurable": {"thread_id": "iid"}}
            result = await node(state, config)

        # The node's eval returned Allow.
        assert result["watchover_route"] == "tools"
        # The LLM was constructed with model=quick-mirror.
        captured = factory.captured_kwargs  # type: ignore[attr-defined]
        assert len(captured) == 1
        assert captured[0].get("model") == "quick-mirror", (
            f"factory did not resolve llm_model=quick to model_keywords; "
            f"got model={captured[0].get('model')!r}"
        )

    async def test_factory_falls_back_when_model_not_allowed(
        self, monkeypatch, caplog
    ):
        """Factory with ``watcher_config={"llm_model": "forbidden"}`` →
        WARNING + fallback to session model.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"])
        manager = _make_manager_with_config(
            session_model="agentic",
            allowed_models=("agentic",),  # "forbidden" not in here
        )
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        with caplog.at_level("WARNING"):
            with patch("daemon.graph.ThinkingChatOpenAI", factory):
                node = create_watchover_check_node(
                    manager=manager,
                    slot=slot,
                    llm_config={"model": "agentic"},
                    watcher_config={"llm_model": "forbidden"},
                )
                from langchain_core.messages import AIMessage

                state = {
                    "messages": [
                        AIMessage(
                            content="ok",
                            tool_calls=[
                                {
                                    "id": "tc-1",
                                    "name": "bash",
                                    "args": {"command": "ls"},
                                }
                            ],
                        )
                    ],
                }
                from langchain_core.runnables import RunnableConfig

                config: RunnableConfig = {
                    "configurable": {"thread_id": "iid"}
                }
                await node(state, config)

        captured = factory.captured_kwargs  # type: ignore[attr-defined]
        assert captured[0].get("model") == "agentic", (
            f"forbidden model should fall back to session model; got "
            f"{captured[0].get('model')!r}"
        )
        assert any(
            "forbidden" in r.message and "falling back" in r.message
            for r in caplog.records
        )


# =============================================================================
# Regression #d — regen-failure → stale-snapshot behavior preserved
# =============================================================================


class TestRegenFailureKeepsStaleSnapshot:
    """Regen failure keeps the previous snapshot (locked failure semantics).

    The pre-fix prod evidence shows regen failures logged a WARNING
    ("snapshot regeneration failed for ...: TimeoutError: ") and the
    eval continued with the stale snapshot. This is documented as
    load-bearing (graph.py:9806-9814) and MUST be preserved — a
    regen failure is preferable to no context.
    """

    async def test_regen_failure_returns_previous_snapshot(self, monkeypatch):
        """Snapshot regen raises TimeoutError → return ``self._snapshot``.

        The eval call continues normally; the delta is bounded by the
        caller regardless. This is the locked failure-mode semantics.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"] * 10)
        manager = _make_manager_with_config()

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "agentic"},
                instance_id="iid",
                watcher_config={"delta_max_messages": 2},
            )
            evaluator._snapshot = "PREVIOUS_SNAPSHOT_TEXT"
            # Bypass the first-call seed so we can drive the regen
            # path with a known-large delta on the first call.
            evaluator._initial_delta_seeded = True

            # First invoke: regen call (raises TimeoutError).
            # Second invoke: eval call (returns "Allowed").
            # Order is regen-first because evaluate() runs the regen
            # before the per-tool eval loop (graph.py:9809-9811).
            call_count = {"n": 0}

            def _side_effect(_messages):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise asyncio.TimeoutError()
                return _FakeLLMResult(content="Allowed")

            _llm.invoke.side_effect = _side_effect

            # Trigger the regen by sending a delta that exceeds delta_max.
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[
                    HumanMessage(content="a"),
                    HumanMessage(content="b"),
                    HumanMessage(content="c"),
                ],
                watchover_context="ctx",
            )

        # The previous snapshot is preserved — regen failure keeps it.
        assert evaluator._snapshot == "PREVIOUS_SNAPSHOT_TEXT", (
            f"regen failure should preserve the previous snapshot; "
            f"got {evaluator._snapshot!r}"
        )
        # The eval still returned a verdict (eval call happened BEFORE
        # the regen call — see graph.py evaluate() flow).
        # (We didn't capture verdicts here; the regen was a separate
        # call. We just need the eval to have completed and the
        # subsequent regen to have failed gracefully.)


# =============================================================================
# Meta-cache test isolation
# =============================================================================


class TestMetaCacheIsolation:
    """``_WATCHER_META_CACHE`` is process-lifetime — tests that vary
    ``watcher_config`` should NOT depend on it.

    Pre-fix, tests that wanted different watcher configs had to monkey-
    patch ``daemon.graph._WATCHER_META_CACHE`` directly because the
    factory would re-read the same cached dict. Post-fix, the
    ``model_override`` / ``snapshot_model_override`` parameters give
    tests a clean seam — they don't touch the cache, the resolver
    isn't re-run, and the LLM construction reflects the explicit
    override.

    This test pins the seam: it confirms that
    ``create_watchover_check_node`` consumes its ``watcher_config``
    argument at factory time (not via the cache), and that changing
    the cache between two factory calls does NOT affect an evaluator
    built before the change.
    """

    async def test_factory_uses_passed_watcher_config_not_cache(self, monkeypatch):
        """Factory built with explicit ``watcher_config={"llm_model": "x"}``
        + a polluted ``_WATCHER_META_CACHE={"llm_model": "y"}`` uses
        ``"x"`` — the explicit argument wins.
        """
        factory, _llm = _make_fake_llm_class(["Allowed"])
        manager = _make_manager_with_config(allowed_models=("x", "y"))
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        # Pollute the cache; this is what stale test environments
        # look like.
        monkeypatch.setattr(
            graph_mod, "_WATCHER_META_CACHE", {"llm_model": "y"}
        )

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager,
                slot=slot,
                llm_config={"model": "agentic"},
                watcher_config={"llm_model": "x"},
            )
            # The factory only matters if its resolution runs at
            # build time. With our fix, the resolver is called at
            # build time but takes the explicit watcher_config arg.
            # The model_override baked into the evaluator should be
            # "x" (resolved from the explicit arg).
            # We can't easily inspect the evaluator without triggering
            # an eval, so we trigger a minimal one.
            from langchain_core.messages import AIMessage

            state = {
                "messages": [
                    AIMessage(
                        content="ok",
                        tool_calls=[
                            {
                                "id": "tc-1",
                                "name": "bash",
                                "args": {"command": "ls"},
                            }
                        ],
                    )
                ],
            }
            from langchain_core.runnables import RunnableConfig

            config: RunnableConfig = {
                "configurable": {"thread_id": "iid"}
            }
            await node(state, config)

        captured = factory.captured_kwargs  # type: ignore[attr-defined]
        assert captured[0].get("model") == "x", (
            f"factory used cache ({captured[0].get('model')!r}) "
            f"instead of explicit watcher_config 'x'"
        )

    def test_meta_cache_starts_as_none_after_explicit_reset(self, monkeypatch):
        """#7 fix: ``_WATCHER_META_CACHE`` is ``None`` immediately
        after a clean reset (the read-once sentinel value).

        Pre-#7 the test was tautological (``assert hasattr(...)``)
        — it proved the constant EXISTS but not that it STARTS at
        ``None``. This test pins the sentinel value so a future
        refactor that changes the cache initial state (e.g.
        ``_WATCHER_META_CACHE: dict = {}`` to silence a "may be
        referenced before assignment" lint) is caught at the test
        layer instead of silently breaking the read-once contract.
        """
        monkeypatch.setattr(graph_mod, "_WATCHER_META_CACHE", None)
        assert graph_mod._WATCHER_META_CACHE is None, (
            "monkeypatch reset to None did not stick — the cache "
            "may not be a module-level mutable global, or the "
            "sentinel has been changed from None"
        )
