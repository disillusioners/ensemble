"""Tests for ``daemon.tools.compare_tools.create_compare_tools`` and
``compare_images``.

Coverage lanes:

  1. **Factory** — ``create_compare_tools(manager, current_instance_id)``
     returns a list with exactly one tool, ``compare_images``.
  2. **Registration** — the returned tool is registered under the
     ``"design"`` category (via the ``_tool_category`` attribute set by
     ``@register_tool_category``), NOT the ``"instance"`` category.
  3. **Authorization** — the ``"design"`` row in
     ``TOOL_REQUIRED_AGENTS`` byte-matches the
     ``@register_tool_category`` string (AC-1 grep-provable).
  4. **Vision fail-loud init** — facade refuses to init when
     ``"vision"`` is absent from ``config.llm.allowed_models`` AND the
     list is non-empty (P2-WP3 AC-5, mirrors arch §8 🔴).
  5. **Never-raise contract** — distinct error envelope kinds for
     ``timeout`` / ``missing-agent`` / ``vision-failure`` /
     ``input-not-found`` / ``schema-invalid`` (AC-3);
     no exception escapes the facade.
  6. **Reuse-by-discovery** — second call finds the caller's most
     recent invoked-as-tool-stamped ``image-comparator`` child
     (AC-4, mirrors ``tests/test_chart_tools.py::TestGenerateChartReuse``).
  7. **Discovery error degrades** — repository error during discovery
     falls back to fresh spawn, never raises.
  8. **Monitoring triggers** — the T1–T3 trigger payload reaches
     ``shared_meta_kv_repo`` with the ``design.comparator.monitor``
     key (AC-5). Docstring is canonical — KV write is best-effort.
  9. **Single-call shape** — ``invoke_agent_and_wait`` receives
     ``images=[uri_a, uri_b]`` in ONE dispatch (AC-3).
 10. **Findings schema validation** — agent return matches the
     findings schema; a malformed return surfaces the
     ``schema-invalid`` envelope (AC-1).
 11. **Output type check** — facade never returns image bytes /
     data-URI fields in its structured return (AC-7).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import daemon.tools.compare_tools as compare_tools_module


# Two 32-hex substrate ids for tests that exercise the input
# resolution path; the input-not-found path uses a third non-hex id
# so the resolver routes it through the workdir branch (which then
# 404s because the test has no workdir wired — the same expected
# input-not-found surface).
_SUBSTRATE_ID_A = "0123456789abcdef0123456789abcdef"
_SUBSTRATE_ID_B = "fedcba9876543210fedcba9876543210"


def _valid_findings_json() -> str:
    """A minimal findings JSON object that passes the WP3 schema validator."""
    return json.dumps(
        {
            "verdict": "pass",
            "per_criterion": [
                {
                    "criterion": "structural_layout",
                    "result": "pass",
                    "severity": "nit",
                    "evidence": ["stub"],
                }
            ],
            "summary": "stub",
            "pinned_spec_sha": None,
        }
    )


def _make_manager(
    *,
    get_children_return: list | None = None,
    get_children_side_effect: BaseException | None = None,
    shared_meta_kv_repo: MagicMock | None = None,
    instance_get_return=None,
    allowed_models: list[str] | None = None,
    config_llm_side_effect: BaseException | None = None,
    no_config: bool = False,
    tmp_image_store: MagicMock | None = None,
    enqueue_message: AsyncMock | None = None,
) -> MagicMock:
    """Build a mock manager wired for ``compare_images`` invocation.

    Defaults:
      * ``_instance_repository.get`` → ``None`` (no project context; keeps
        project_id auto-injection deterministic, same shape as the
        chart_tools test factory).
      * ``_instance_repository.get_children`` → ``[]`` (discovery miss,
        so the fresh-spawn path is exercised).
      * ``shared_meta_kv_repo`` → ``MagicMock`` with ``set_many``
        attr-recorded so monitoring KV writes can be asserted on.
      * ``enqueue_message`` → ``AsyncMock`` (P2-WP2 reuse rail — the
        reuse path calls ``manager.enqueue_message`` directly; a
        bare ``MagicMock`` would not be awaitable). Tests that want
        to observe the reuse dispatch pass ``enqueue_message=`` or
        rely on the default; tests that exercise the fresh-spawn
        path assert it was NOT called.
      * ``config.llm.allowed_models`` → ``["vision"]`` so the WP3
        fail-loud init gate passes (P2-WP3 AC-5). Tests that exercise
        the gate override this with ``allowed_models=[...]`` or
        ``no_config=True``.
      * ``tmp_image_store`` → a ``MagicMock`` whose
        ``open_full`` returns a default TmpImageRecord and whose
        ``dir`` is path-like; tests that exercise the bridge
        override it.
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=instance_get_return)
    if get_children_side_effect is not None:
        manager._instance_repository.get_children = MagicMock(
            side_effect=get_children_side_effect,
        )
    else:
        manager._instance_repository.get_children = MagicMock(
            return_value=get_children_return if get_children_return is not None else [],
        )
    manager._instance_repository.get_tree_root_id = MagicMock(return_value=None)
    manager.shared_meta_kv_repo = shared_meta_kv_repo or MagicMock()
    # P2-WP2 reuse rail — ``_reuse_comparator`` awaits
    # ``manager.enqueue_message`` on the reuse path. A bare
    # ``MagicMock`` attribute is sync and would explode with
    # ``TypeError: object MagicMock can't be used in 'await'
    # expression`` when awaited. Tests that want to observe the
    # dispatch can pass ``enqueue_message=AsyncMock(...)`` and
    # override the default.
    manager.enqueue_message = (
        enqueue_message
        if enqueue_message is not None
        else AsyncMock(return_value=MagicMock())
    )
    # WP3 — wire a default tmp_image_store whose ``open_full`` returns
    # a successful TmpImageRecord for any 32-hex id (matches the
    # substrate shape the bridge expects). Tests that exercise the
    # bridge surface override ``tmp_image_store``.
    if tmp_image_store is None:
        from tests.test_tmp_image_bridge import _FakeStore, _tmp_image_record

        # Default store: 404 on every id (the resolver then surfaces
        # input-not-found). Tests that want success override the
        # store and provide records.
        default_records = {
            _SUBSTRATE_ID_A: _tmp_image_record(image_id=_SUBSTRATE_ID_A),
            _SUBSTRATE_ID_B: _tmp_image_record(image_id=_SUBSTRATE_ID_B),
        }
        manager.tmp_image_store = _FakeStore(
            records=default_records,
            blobs={
                _SUBSTRATE_ID_A: b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
                _SUBSTRATE_ID_B: b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            },
        )
    else:
        manager.tmp_image_store = tmp_image_store
    # Wire the WP3 vision-model gate (P2-WP3 AC-5). Default is a
    # list containing ``"vision"`` — mirrors the production
    # ``OPENAI_SELECTABLE_MODELS=agentic,coding,coding2,vision`` set
    # so the gate passes on the canonical test fixture. Tests
    # exercising the gate override ``allowed_models`` /
    # ``no_config``.
    if no_config:
        # Strip ``config`` to test the no-config pass-through branch.
        # ``MagicMock.__getattr__`` auto-creates attrs on AttributeError
        # so a simple ``del manager.config`` is not enough — the next
        # access re-creates a MagicMock. Use a simple namespace
        # object instead that does NOT support ``config`` at all.
        manager = SimpleNamespace(
            _instance_repository=manager._instance_repository,
            shared_meta_kv_repo=manager.shared_meta_kv_repo,
            tmp_image_store=manager.tmp_image_store,
            enqueue_message=manager.enqueue_message,
        )
    else:
        cfg = MagicMock()
        cfg.llm = MagicMock()
        if config_llm_side_effect is not None:
            # Caller wants the gate to error — wire ``llm`` to raise
            # when ``allowed_models`` is accessed.
            type(cfg.llm).allowed_models = property(
                lambda self: (_ for _ in ()).throw(config_llm_side_effect)
            )
        else:
            cfg.llm.allowed_models = (
                allowed_models if allowed_models is not None else ["vision"]
            )
        manager.config = cfg
    return manager


def _comparator_row(
    instance_id: str = "comp-1",
    status: str = "completed",
    last_activity_at=None,
    created_at: str = "2026-09-26T00:00:00+00:00",
    agent_id: str = "image-comparator",
    invoked_as_tool: bool = True,
) -> SimpleNamespace:
    """Fabricate an ``instances`` row stand-in for discovery/reuse tests.

    Field names mirror ``daemon/repositories/instance/models.py`` —
    note the primary key is ``instance_id`` (NOT ``id``) and
    ``created_at`` is an ISO string while ``last_activity_at`` is a
    nullable datetime.
    """
    return SimpleNamespace(
        instance_id=instance_id,
        agent_id=agent_id,
        status=status,
        instance_metadata={"invoked_as_tool": True} if invoked_as_tool else {},
        last_activity_at=last_activity_at,
        created_at=created_at,
        project_id=None,
    )


# ── Factory shape ────────────────────────────────────────────────────────────


class TestCreateCompareToolsFactory:
    """Factory tests for ``create_compare_tools``."""

    def test_factory_returns_exactly_one_tool(self):
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        tools = create_compare_tools(manager, "test-instance-id")

        assert isinstance(tools, list)
        assert len(tools) == 1

    def test_factory_returns_compare_images_tool(self):
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        tools = create_compare_tools(manager, "test-instance-id")

        assert tools[0].name == "compare_images"

    def test_factory_creates_independent_tools_per_call(self):
        """Each factory call produces a fresh closure (no shared state).

        Two ``compare_images`` tools must be distinct objects so a
        per-instance tool list does not leak state between instances.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        tools_a = create_compare_tools(manager, "instance-a")
        tools_b = create_compare_tools(manager, "instance-b")

        assert tools_a[0] is not tools_b[0]
        assert tools_a[0].name == tools_b[0].name


# ── Category registration ────────────────────────────────────────────────────


class TestCompareToolRegistration:
    """Registration tests for the design tool category."""

    def test_compare_images_registered_under_design_category(self):
        """Tool is tagged with ``_tool_category == "design"`` (AC-1).

        Set by ``@register_tool_category("design")`` in ``compare_tools.py``.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        tools = create_compare_tools(manager, "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) == "design"

    def test_compare_images_not_registered_under_instance_category(self):
        """SECURITY: the compare tool must NOT be tagged as ``"instance"``.

        Companion to the ``INNATE_SKILL_TOOL_CATEGORIES`` security
        test. A design-enabled agent must NOT silently inherit the
        full instance-management suite.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        tools = create_compare_tools(manager, "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) != "instance"

    def test_design_registry_row_byte_matches_category(self):
        """AC-1 grep-provable — ``TOOL_REQUIRED_AGENTS["design"]`` exists.

        The category key string MUST byte-match
        ``@register_tool_category(...)`` (the rule documented in
        ``daemon/tools/_auth.py``).
        """
        from daemon.tools._auth import TOOL_REQUIRED_AGENTS
        from daemon.tools.compare_tools import create_compare_tools

        assert "design" in TOOL_REQUIRED_AGENTS
        assert TOOL_REQUIRED_AGENTS["design"] == ["image-comparator"]

        # And the registry row matches the factory's category tag.
        manager = _make_manager()
        tools = create_compare_tools(manager, "test-instance-id")
        assert (
            tools[0]._tool_category
            in TOOL_REQUIRED_AGENTS
        )


# ── Never-raise contract (AC-3) ─────────────────────────────────────────────


class TestCompareImagesNeverRaise:
    """Never-raise contract: distinct envelope ``kind`` for each failure mode.

    Each test patches ``invoke_agent_and_wait`` to return the canonical
    raw error string for one failure class and asserts the facade
    returns a JSON envelope with the matching ``kind`` — never
    raises.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        """Clear in-flight reuse state before/after each test."""
        compare_tools_module._inflight_reuse.clear()
        yield
        compare_tools_module._inflight_reuse.clear()

    async def test_timeout_returns_timeout_envelope(self):
        """``invoke_agent_and_wait`` timeout string → kind=timeout envelope."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(
            return_value=(
                "Error: Agent timed out after 600s. "
                "Instance abc12345... may still be running.",
                "abc12345-...",
            )
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert isinstance(result, str)
        envelope = json.loads(result)
        assert envelope["kind"] == "timeout"
        assert "timed out" in envelope["error"].lower()

    async def test_missing_agent_returns_missing_agent_envelope(self):
        """``agent not found`` string → kind=missing-agent envelope."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(
            return_value=(
                "Error: Agent 'image-comparator' does not exist.",
                "def67890-...",
            )
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert isinstance(result, str)
        envelope = json.loads(result)
        assert envelope["kind"] == "missing-agent"

    async def test_vision_failure_returns_vision_failure_envelope(self):
        """Vision-model 400-class error → kind=vision-failure envelope."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(
            return_value=(
                "Error: Agent failed. Vision call returned 400 — "
                "model 'gpt-4o' does not support this image shape.",
                "ghi13579-...",
            )
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert isinstance(result, str)
        envelope = json.loads(result)
        assert envelope["kind"] == "vision-failure"

    async def test_unclassified_error_falls_back_to_vision_failure(self):
        """An error string the classifier cannot route lands on
        ``vision-failure`` — the safest catch-all because every
        non-success comparator path involves the vision model either
        directly or indirectly.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(
            return_value=(
                "Error: Something unexpected happened.",
                "jkl24680-...",
            )
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        envelope = json.loads(result)
        assert envelope["kind"] == "vision-failure"

    async def test_none_result_returns_timeout_envelope(self):
        """``(None, instance_id)`` from invoke_agent_and_wait → timeout envelope."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=(None, "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        envelope = json.loads(result)
        assert envelope["kind"] == "timeout"

    async def test_invoke_exception_does_not_escape(self):
        """An exception from ``invoke_agent_and_wait`` is caught and
        surfaced as a vision-failure envelope (never raises).
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()

        async def _raise(*_args, **_kwargs):
            raise RuntimeError("transport exploded")

        with patch(
            "daemon.tools.compare_tools.invoke_agent_and_wait",
            side_effect=_raise,
        ):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        envelope = json.loads(result)
        assert envelope["kind"] == "vision-failure"
        assert "transport exploded" in envelope["error"]

    async def test_schema_invalid_envelope_when_agent_return_malformed(self):
        """Agent return that doesn't match the findings schema →
        ``kind: schema-invalid`` envelope (P2-WP3 AC-1).
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(
            return_value=(
                "this is not a findings JSON object",
                "comp-1",
            )
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        envelope = json.loads(result)
        assert envelope["kind"] == "schema-invalid"


# ── Happy path: findings schema passthrough ─────────────────────────────────


class TestCompareImagesHappyPath:
    """When the comparator agent returns valid content, the facade
    passes it through verbatim — no envelope wrapping.
    """

    async def test_agent_findings_string_returned_verbatim(self):
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        findings_json = json.dumps(
            {
                "verdict": "pass",
                "per_criterion": [
                    {
                        "criterion": "structural_layout",
                        "result": "pass",
                        "severity": "nit",
                        "evidence": ["header aligned top-left as expected"],
                    }
                ],
                "summary": "All criteria pass; one nit on whitespace.",
                "pinned_spec_sha": None,
            }
        )
        mock_invoke = AsyncMock(return_value=(findings_json, "comp-1"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Findings returned verbatim — facade does NOT wrap successes
        # in an envelope (the agent already produced the structured
        # artifact; the facade is a thin shell).
        assert result == findings_json

    async def test_message_includes_pinned_spec_sha_when_supplied(self):
        """When the caller passes ``pinned_spec_sha``, it rides the message.

        This is the D6 hard-rule wiring — the comparator's prompt
        carries the SHA so the agent can cite it in findings.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "comp-1"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
                pinned_spec_sha="abc123",
            )

        message = mock_invoke.call_args.kwargs["message"]
        assert "abc123" in message
        assert "pinned_spec_sha" in message

    async def test_message_includes_criteria_override_when_supplied(self):
        """When the caller passes ``criteria``, the override rides the message."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "comp-1"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
                criteria=["layout_matches", "color_conforms"],
            )

        message = mock_invoke.call_args.kwargs["message"]
        # JSON-encoded list, present in the prompt.
        assert "layout_matches" in message
        assert "color_conforms" in message
        assert "criteria_override" in message


# ── Findings wire-schema extraction (P2-WP8 schema-fix) ─────────────────────


class TestFindingsWireSchemaExtraction:
    """P2-WP8 schema-fix regression pins.

    Root cause (evidence: verdicts/p2-wp8-e2e-findings.md, §E re-fire
    2026-09-27T03:57Z; raw child return recovered from checkpoint blob
    of instance 44c63fc3): the comparator emitted a COMPLETE,
    untruncated findings JSON (finish_reason=stop, no inline
    ``<think>`` — reasoning rode ``additional_kwargs.reasoning_content``)
    but wrapped it in a `` ```json `` fence AND drifted from the wire
    contract (`criteria` vs `per_criterion`, rows keyed `id`, string
    `evidence`, `severity: "pass"`). No surface told the model the wire
    schema. Fix: soul.md now pins the wire schema; the facade extracts
    shape-level noise (fences / ``<think>`` / prose) BEFORE the
    UNCHANGED schema validation.
    """

    # The confirmed root-cause shape, trimmed from the recovered raw
    # return (long evidence strings elided; keys and structure exact).
    # NOTE: still schema-INVALID even after fence stripping — the
    # extractor is shape-level tolerance only, never value substitution.
    REAL_MODEL_DRIFTED_SHAPE = (
        "```json\n"
        "{\n"
        '  "artifact": "compare_images.findings",\n'
        '  "schema_version": "1.0",\n'
        '  "pinned_spec_sha": "81113ea0f2ffe0d2e7994e963e34f04f10989887a659a33774e2966af2c85b24",\n'
        '  "verdict": "conditional_pass",\n'
        '  "criteria": [\n'
        "    {\n"
        '      "id": "structural_layout",\n'
        '      "severity": "pass",\n'
        '      "evidence": "Image A: header block above a white card.",\n'
        '      "expected": "clean hierarchy",\n'
        '      "observed": "clean hierarchy"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "One major defect on image B H1 mojibake."\n'
        "}\n"
        "```"
    )

    def test_bare_valid_findings_parse_unchanged(self):
        """Regression guard: bare contract-valid JSON validates exactly
        as before the fix."""
        from daemon.tools.compare_tools import _validate_findings

        findings = json.loads(_valid_findings_json())
        assert _validate_findings(_valid_findings_json()) == findings

    def test_code_fence_wrapped_valid_findings_parse(self):
        """A `` ```json ``-fenced CONTRACT-VALID payload now parses —
        the observed presentation-noise class is absorbed shape-level."""
        from daemon.tools.compare_tools import _validate_findings

        fenced = "```json\n" + _valid_findings_json() + "\n```"
        findings = json.loads(_valid_findings_json())
        assert _validate_findings(fenced) == findings

    def test_think_wrapped_valid_findings_parse(self):
        """A leading ``<think>…</think>`` reasoning block (the known
        backing-model inline-reasoning behavior) is stripped before
        validation."""
        from daemon.tools.compare_tools import _validate_findings

        wrapped = (
            "<think>\nThe image A header sits above a card.\n</think>\n"
            + _valid_findings_json()
        )
        findings = json.loads(_valid_findings_json())
        assert _validate_findings(wrapped) == findings

    def test_prose_wrapped_valid_findings_parse(self):
        """Prose around the JSON object does not defeat validation —
        the outermost balanced object is extracted."""
        from daemon.tools.compare_tools import _validate_findings

        wrapped = (
            "Here are my findings:\n\n"
            + _valid_findings_json()
            + "\n\nLet me know if you need more detail."
        )
        findings = json.loads(_valid_findings_json())
        assert _validate_findings(wrapped) == findings

    def test_real_model_drifted_shape_still_rejected(self):
        """THE confirmed root-cause pin: the shape the real comparator
        returned (fence + invented keys) is still schema-INVALID.
        Schema drift is fixed at the SOURCE (soul wire schema), not by
        validator leniency."""
        from daemon.tools.compare_tools import _validate_findings

        assert _validate_findings(self.REAL_MODEL_DRIFTED_SHAPE) is None

    def test_schema_still_enforced_after_stripping(self):
        """Fence-stripping never weakens schema enforcement: an
        invalid verdict enum inside a fence is still rejected."""
        from daemon.tools.compare_tools import _validate_findings

        findings = json.loads(_valid_findings_json())
        findings["verdict"] = "excellent"
        fenced = "```json\n" + json.dumps(findings) + "\n```"
        assert _validate_findings(fenced) is None

    def test_no_json_returns_none(self):
        """Prose-only returns (and the Error-string lane) yield None."""
        from daemon.tools.compare_tools import _validate_findings

        assert _validate_findings("this is not a findings JSON object") is None
        assert _validate_findings("") is None

    def test_unterminated_object_returns_none(self):
        """Truncated JSON (brace never closes) yields None."""
        from daemon.tools.compare_tools import _validate_findings

        assert _validate_findings('{"verdict": "pass", "per_criterion": [') is None

    def test_braces_inside_evidence_strings_do_not_desync(self):
        """The balanced-object scan is string-aware: ``{``/``}`` inside
        evidence prose cannot truncate the extraction."""
        from daemon.tools.compare_tools import _validate_findings

        findings = json.loads(_valid_findings_json())
        findings["per_criterion"][0]["evidence"] = [
            "palette {off-palette #ABCDEF} observed near hero"
        ]
        wrapped = "note: findings follow\n" + json.dumps(findings)
        assert _validate_findings(wrapped) == findings

    async def test_facade_returns_canonical_stripped_json(self):
        """Facade-level pin: a fenced-but-valid return surfaces as the
        canonical (stripped) findings JSON in the tool result — noise
        never leaks to the caller (both dispatch paths use this seam)."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        fenced = "```json\n" + _valid_findings_json() + "\n```"
        mock_invoke = AsyncMock(return_value=(fenced, "comp-1"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert result == _valid_findings_json()


# ── Reuse-by-discovery (AC-4) ────────────────────────────────────────────────


class TestCompareImagesReuse:
    """Reuse-by-discovery unit tests (mirrors ``TestGenerateChartReuse``).

    The comparator reuse logic mirrors the charter precedent — pure
    query-discovery over ``get_children``, filtered to
    ``image-comparator`` children flagged ``invoked_as_tool``. The
    reuse dispatch goes through ``manager.enqueue_message`` (the
    service-side revive-on-send flips terminal→RUNNING and the
    existing checkpoint reloads) — ``invoke_agent_and_wait`` is the
    FRESH-spawn helper and MUST NOT be awaited on the reuse path.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        compare_tools_module._inflight_reuse.clear()
        compare_tools_module._reuse_revive_attempts.clear()
        yield
        compare_tools_module._inflight_reuse.clear()
        compare_tools_module._reuse_revive_attempts.clear()

    async def test_first_call_spawns_fresh_when_no_prior_child(self):
        """Discovery empty → fresh spawn via ``invoke_agent_and_wait``."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager(get_children_return=[])
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Fresh path: invoke_agent_and_wait invoked once with the
        # comparator agent_id; enqueue_message NOT awaited (no
        # discovered child to reuse).
        mock_invoke.assert_awaited_once()
        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["agent_id"] == "image-comparator"
        assert kwargs["return_instance_id"] is True
        assert kwargs["timeout"] == 600.0
        assert kwargs["parent_id"] == "test-instance-id"
        manager.enqueue_message.assert_not_awaited()

    async def test_second_call_reuses_completed_comparator(self):
        """Terminal COMPLETED comparator → reuse via ``enqueue_message``.

        The discovered comparator id receives the dispatch (service-side
        revive-on-send flips terminal→RUNNING and the existing
        checkpoint reloads). ``invoke_agent_and_wait`` is NEVER
        awaited on the reuse path — that's the load-bearing seam
        the prior P2-WP2 review flagged as MAJOR-1 vacuous.
        """
        from daemon.tools.compare_tools import create_compare_tools

        completed = _comparator_row(
            instance_id="comp-1",
            status="completed",
            last_activity_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        )
        manager = _make_manager(get_children_return=[completed])
        # ``_reuse_comparator`` re-reads the comparator row for its
        # authoritative prior_status — wire ``get`` to return the
        # same completed row.
        manager._instance_repository.get = MagicMock(return_value=completed)
        # Mock invoke is the tripwire — must NEVER be awaited on the
        # reuse path (P2-WP2 review MAJOR-1 was that the prior test
        # asserted only the agent_id, not that the discovered id was
        # actually used).
        mock_invoke = AsyncMock(
            side_effect=AssertionError(
                "invoke_agent_and_wait MUST NOT be called on the reuse path"
            )
        )
        expected = _valid_findings_json()
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content=expected, is_error=False)
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            with _patched_registry(mock_registry):
                tools = create_compare_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )

        # The discovered comparator id is the target of the enqueue.
        # This is the load-bearing pin: the prior P2-WP2 review
        # flagged the old test as vacuous because it only asserted
        # ``invoke_agent_and_wait`` was called (which always mints a
        # fresh instance) — the test below proves the DISCOVERED id
        # was actually used.
        assert result == expected
        manager.enqueue_message.assert_awaited_once()
        enqueue_kwargs = manager.enqueue_message.call_args.kwargs
        assert enqueue_kwargs["instance_id"] == "comp-1"
        assert (
            enqueue_kwargs["source"]
            == "internal_comparator_reuse:test-instance-id"
        )
        assert enqueue_kwargs["metadata"] == {"comparator_reuse": True}
        assert _SUBSTRATE_ID_A in enqueue_kwargs["message"]
        # Tripwire — the spawn helper MUST NOT be used on reuse.
        mock_invoke.assert_not_awaited()
        # Registry lifecycle — register (drain + register; the
        # post-enqueue re-register only fires when
        # ``is_registered`` returns False at step 5; the mock
        # fixture returns True so step 5 is a no-op). The key
        # invariant is the discovered id was the registration
        # target and the drain fired (step-3 W1 fix).
        register_calls = [
            call.args[0]
            for call in mock_registry.register.call_args_list
        ]
        assert register_calls == ["comp-1"]
        # Unregister fired at least twice: step-3 W1 stale-buffer
        # drain + step-7 finally cleanup (mirrors chart T8.2 W1
        # pin). The mock's unregister records every call; we only
        # pin the target id (no exact count — the second pass
        # depends on whether ``is_registered`` flipped).
        unregister_calls = [
            call.args[0]
            for call in mock_registry.unregister.call_args_list
        ]
        assert all(c == "comp-1" for c in unregister_calls)
        assert len(unregister_calls) >= 1
        # finally-cleanup ran — no leaked inflight slot.
        assert "comp-1" not in compare_tools_module._inflight_reuse

    async def test_only_image_comparator_children_qualify(self):
        """A child with a different ``agent_id`` is NOT reusable for compare.

        Discovery walks ALL the caller's children, but only
        ``image-comparator`` rows qualify (the filter mirrors
        charter's charter-only filter).
        """
        from daemon.tools.compare_tools import create_compare_tools

        # A charter child — same shape, different agent_id.
        charter_child = _comparator_row(
            instance_id="chart-1",
            agent_id="charter",
            status="completed",
        )
        manager = _make_manager(get_children_return=[charter_child])
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Discovery filtered out the charter child → fresh spawn.
        mock_invoke.assert_awaited_once()
        # Caller identity is the parent_id on the FRESH spawn (not
        # the reused charter id).
        assert mock_invoke.call_args.kwargs["parent_id"] == "test-instance-id"
        # The filtered-out charter id was never enqueued onto.
        manager.enqueue_message.assert_not_awaited()

    async def test_only_invoked_as_tool_children_qualify(self):
        """A comparator child WITHOUT the ``invoked_as_tool`` stamp is skipped.

        Mirrors chart_tools.py:99-102 — the stamp is the only way to
        distinguish a tool-spawned comparator from an
        orchestrated-as-a-team-member comparator (the latter is not
        safe to reuse for a different task).
        """
        from daemon.tools.compare_tools import create_compare_tools

        # No ``invoked_as_tool`` flag → filtered out.
        not_invoked = _comparator_row(
            instance_id="comp-1",
            invoked_as_tool=False,
        )
        manager = _make_manager(get_children_return=[not_invoked])
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Discovery empty after filter → fresh spawn.
        mock_invoke.assert_awaited_once()
        manager.enqueue_message.assert_not_awaited()

    async def test_discovery_repository_error_degrades_to_fresh(self):
        """A repository error during discovery falls back to fresh spawn.

        The error never escapes the facade; the caller observes a
        normal fresh-path result.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager(
            get_children_side_effect=RuntimeError("database unavailable"),
        )
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert result == _valid_findings_json()
        mock_invoke.assert_awaited_once()
        manager.enqueue_message.assert_not_awaited()

    async def test_busy_in_flight_reuse_rejected(self):
        """A second concurrent call targeting the same comparator is
        busy-rejected (in-flight reuse guard).

        Mirrors the chart precedent: two waiters on one
        ``instance_id`` would share an ``asyncio.Event`` and the
        second would wake on the FIRST caller's completion. The
        busy-reject now fires inside ``_reuse_comparator`` (the
        dispatch site no longer pre-checks busy — see
        ``_reuse_comparator`` step 2), so the test pre-seeds
        ``_inflight_reuse`` to simulate the comparator being
        mid-turn and asserts no enqueue / no invoke.
        """
        from daemon.tools.compare_tools import create_compare_tools

        completed = _comparator_row(
            instance_id="comp-1",
            status="completed",
        )
        manager = _make_manager(get_children_return=[completed])
        manager._instance_repository.get = MagicMock(return_value=completed)
        # Simulate the comparator being mid-turn by pre-seeding the
        # in-flight set.
        compare_tools_module._inflight_reuse.add("comp-1")

        with patch(
            "daemon.tools.compare_tools.invoke_agent_and_wait",
            AsyncMock(),
        ) as mock_invoke:
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Busy-rejected — no second invoke dispatched, no enqueue,
        # no fresh spawn.
        mock_invoke.assert_not_awaited()
        manager.enqueue_message.assert_not_awaited()
        envelope = json.loads(result)
        assert envelope["kind"] == "vision-failure"
        assert "Comparator busy" in envelope["error"]
        # The pre-seeded slot is still held — busy-reject does NOT
        # consume the in-flight token (the holding caller still
        # owns it).
        assert "comp-1" in compare_tools_module._inflight_reuse

    async def test_paused_comparator_returns_paused_envelope(self):
        """PAUSED comparator → busy-rejected WITHOUT enqueue (W6 pin).

        The reuse helper rejects PAUSED children at the
        authoritative re-read (status pre-check, step 1 of
        ``_reuse_comparator``) — enqueueing onto a paused
        comparator would sit PENDING until an operator resumes
        the child while the tool wait burns.
        """
        from daemon.tools.compare_tools import create_compare_tools

        paused = _comparator_row(
            instance_id="comp-1",
            status="paused",
        )
        manager = _make_manager(get_children_return=[paused])
        manager._instance_repository.get = MagicMock(return_value=paused)
        mock_invoke = AsyncMock()

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        mock_invoke.assert_not_awaited()
        manager.enqueue_message.assert_not_awaited()
        envelope = json.loads(result)
        assert envelope["kind"] == "vision-failure"
        assert "paused" in envelope["error"].lower()
        assert "fresh=True" in envelope["error"]

    async def test_error_comparator_revives_once_then_respawns(self):
        """ERROR comparator: call 1 revives (counter 1), call 2 respawns.

        The T6 one-shot budget: first ERROR hit consumes a revive
        counter, the next ERROR hit on the same comparator id
        respawns fresh. Mirrors chart test T8.4 exactly. The
        counter is in-memory (daemon-restart-bounded) and only
        ERROR/FAILED prior statuses consume it.
        """
        from daemon.tools.compare_tools import create_compare_tools

        error_comparator = _comparator_row(
            instance_id="comp-err",
            status="error",
            last_activity_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        )
        manager = _make_manager(get_children_return=[error_comparator])
        manager._instance_repository.get = MagicMock(return_value=error_comparator)
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(
                content=_valid_findings_json(), is_error=False
            )
        )
        mock_invoke = AsyncMock(
            return_value=(_valid_findings_json(), "fresh-child-id")
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            with _patched_registry(mock_registry):
                tools = create_compare_tools(manager, "test-instance-id")

                # Call 1: ERROR + counter 0 → revive (consume).
                result1 = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )
                assert result1 == _valid_findings_json()
                assert (
                    compare_tools_module._reuse_revive_attempts["comp-err"]
                    == 1
                )
                manager.enqueue_message.assert_awaited_once()
                assert (
                    manager.enqueue_message.call_args.kwargs["instance_id"]
                    == "comp-err"
                )

                # Call 2: counter ≥ 1 → treat as MISS → fresh spawn.
                result2 = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )
                assert result2 == _valid_findings_json()
                # Counter NOT incremented again by the respawn path.
                assert (
                    compare_tools_module._reuse_revive_attempts["comp-err"]
                    == 1
                )
                # Still only ONE enqueue (call 1); call 2 spawned fresh.
                assert manager.enqueue_message.await_count == 1
                mock_invoke.assert_awaited_once()
                # The fresh spawn carries the caller id as parent_id,
                # NOT the discovered (and now-skipped) comp-err id.
                assert (
                    mock_invoke.call_args.kwargs["parent_id"]
                    == "test-instance-id"
                )

    async def test_failed_comparator_revives_once_then_respawns(self):
        """FAILED comparator mirrors the ERROR/FAILED symmetric budget.

        T6 treats ERROR and FAILED symmetrically — both consume
        the one-shot budget; COMPLETED/TERMINATED never touch the
        counter (free revives).
        """
        from daemon.tools.compare_tools import create_compare_tools

        failed = _comparator_row(
            instance_id="comp-fail",
            status="failed",
            last_activity_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        )
        manager = _make_manager(get_children_return=[failed])
        manager._instance_repository.get = MagicMock(return_value=failed)
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(
                content=_valid_findings_json(), is_error=False
            )
        )
        mock_invoke = AsyncMock(
            return_value=(_valid_findings_json(), "fresh-child-id")
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            with _patched_registry(mock_registry):
                tools = create_compare_tools(manager, "test-instance-id")

                # Call 1: FAILED + counter 0 → revive.
                result1 = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )
                assert result1 == _valid_findings_json()
                assert (
                    compare_tools_module._reuse_revive_attempts["comp-fail"]
                    == 1
                )
                manager.enqueue_message.assert_awaited_once()

                # Call 2: FAILED + counter 1 → respawn fresh.
                result2 = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )
                assert result2 == _valid_findings_json()
                assert manager.enqueue_message.await_count == 1
                mock_invoke.assert_awaited_once()

    async def test_terminated_comparator_respawns_fresh(self):
        """TERMINATED comparator → fresh spawn, no reuse, no revive bump.

        Comparator-vs-charter divergence (MINOR-1): charter reuses
        TERMINATED freely (chart T8.13); the comparator does NOT
        — TERMINATED children fall through to a fresh spawn because
        the comparator has no durable cross-call state worth a
        TERMINATED→revive round-trip.
        """
        from daemon.tools.compare_tools import create_compare_tools

        terminated = _comparator_row(
            instance_id="comp-term",
            status="terminated",
        )
        manager = _make_manager(get_children_return=[terminated])
        manager._instance_repository.get = MagicMock(return_value=terminated)
        mock_invoke = AsyncMock(
            return_value=(_valid_findings_json(), "fresh-child-id")
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Fresh spawn invoked once; enqueue NEVER called (TERMINATED
        # child is filtered at the dispatch site, not at the reuse
        # helper).
        mock_invoke.assert_awaited_once()
        manager.enqueue_message.assert_not_awaited()
        assert result == _valid_findings_json()
        # TERMINATED never touches the revive counter (the counter
        # only ERROR/FAILED consume — the dispatch site skips
        # TERMINATED before reaching the helper).
        assert (
            compare_tools_module._reuse_revive_attempts.get("comp-term") is None
        )

    async def test_completed_comparator_revives_free(self):
        """COMPLETED comparator: free revive, counter never created.

        T6 mirror — COMPLETED never touches the local revive
        counter (free revives). Mirrors chart T8.11 (the
        comparator path's free-revive invariant).
        """
        from daemon.tools.compare_tools import create_compare_tools

        completed = _comparator_row(
            instance_id="comp-1",
            status="completed",
        )
        manager = _make_manager(get_children_return=[completed])
        manager._instance_repository.get = MagicMock(return_value=completed)
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(
                content=_valid_findings_json(), is_error=False
            )
        )

        with _patched_registry(mock_registry):
            tools = create_compare_tools(manager, "test-instance-id")
            for i in range(3):
                result = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )
                assert result == _valid_findings_json()

        # Three reuse dispatches, three enqueues, NO fresh spawn.
        assert manager.enqueue_message.await_count == 3
        # Counter never created for COMPLETED children.
        assert compare_tools_module._reuse_revive_attempts.get("comp-1") is None

    async def test_reuse_timeout_returns_timeout_envelope(self):
        """Registry wait_for → None → timeout envelope; no fresh spawn.

        Mirrors chart T8.7 — timeout on the reuse path returns an
        error string, NEVER spawns fresh (M8 mirror — the
        comparator is durable across calls so we do not terminate
        it on timeout).
        """
        from daemon.tools.compare_tools import create_compare_tools

        completed = _comparator_row(
            instance_id="comp-1",
            status="completed",
        )
        manager = _make_manager(get_children_return=[completed])
        manager._instance_repository.get = MagicMock(return_value=completed)
        mock_invoke = AsyncMock(
            side_effect=AssertionError("must not be called on reuse")
        )
        mock_registry = _make_registry(wait_result=None)  # timeout

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            with _patched_registry(mock_registry):
                tools = create_compare_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(
                    image_a=_SUBSTRATE_ID_A,
                    image_b=_SUBSTRATE_ID_B,
                )

        envelope = json.loads(result)
        assert envelope["kind"] == "timeout"
        # No fresh spawn on timeout (M8 mirror).
        mock_invoke.assert_not_awaited()
        # Inflight slot cleaned up.
        assert "comp-1" not in compare_tools_module._inflight_reuse


def _make_registry(wait_result=None) -> MagicMock:
    """Mock CompletionRegistry with a configured ``wait_for`` result.

    Patched at the ``daemon.services.completion_registry`` MODULE
    attribute — ``_reuse_comparator`` imports
    ``get_completion_registry`` lazily inside the function body, so
    the patched name must be visible at the module attribute (the
    local import re-binds on every call), mirroring
    ``tests/test_chart_tools.py::_make_registry``.
    """
    registry = MagicMock(name="CompletionRegistry")
    registry.wait_for = AsyncMock(return_value=wait_result)
    registry.register = MagicMock()
    registry.unregister = MagicMock()
    registry.is_registered = MagicMock(return_value=True)
    return registry


@contextmanager
def _patched_registry(mock_registry):
    """Patch ``get_completion_registry`` at the module attribute."""
    with patch(
        "daemon.services.completion_registry.get_completion_registry",
        return_value=mock_registry,
    ):
        yield mock_registry


# ── Monitoring triggers (AC-5) ──────────────────────────────────────────────


class TestCompareImagesMonitoring:
    """T1–T3 monitoring triggers land in shared_meta_kv under the
    ``design.comparator.monitor`` key (AC-5).
    """

    async def test_monitor_triggers_written_to_shared_meta_kv(self):
        """First call writes the T1–T3 payload to ``shared_meta_kv_repo``.

        The write is idempotent (best-effort upsert). Docstring is
        canonical — KV failure never silences a real trigger.
        """
        from daemon.tools.compare_tools import create_compare_tools

        kv_repo = MagicMock()
        manager = _make_manager(shared_meta_kv_repo=kv_repo)
        mock_invoke = AsyncMock(return_value=(_valid_findings_json(), "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a="img_a",
                image_b="img_b",
            )

        # ``set_many`` was called at least once with the monitor key.
        assert kv_repo.set_many.called
        # Pull out the call args and find the monitor key.
        monitor_payload = None
        for call in kv_repo.set_many.call_args_list:
            _, payload = call.args
            if isinstance(payload, dict) and "design.comparator.monitor" in payload:
                monitor_payload = payload["design.comparator.monitor"]
                break
        assert monitor_payload is not None

        triggers = monitor_payload["triggers"]
        assert "T1" in triggers
        assert "T2" in triggers
        assert "T3" in triggers
        # Each trigger carries a firing condition and the commission it opens.
        for tag, body in triggers.items():
            assert "condition" in body, f"{tag} missing condition"
            assert "commission" in body, f"{tag} missing commission"
        # Source-of-truth provenance for the audit trail.
        assert monitor_payload["recorded_at_wpid"] == "P2-WP2"

    async def test_monitor_write_failure_does_not_break_tool(self):
        """If the KV write fails, the tool still returns the agent's result.

        Docstring is canonical — KV failure is logged + best-effort,
        never blocks the compare.
        """
        from daemon.tools.compare_tools import create_compare_tools

        kv_repo = MagicMock()
        kv_repo.set_many.side_effect = RuntimeError("kv offline")
        manager = _make_manager(shared_meta_kv_repo=kv_repo)
        findings = _valid_findings_json()
        mock_invoke = AsyncMock(return_value=(findings, "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            # Tool invocation completes — KV failure is logged at
            # debug, never raised.
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert result == findings

    async def test_monitor_write_falls_back_to_module_sentinel_on_repo_miss(self):
        """If the manager exposes no ``shared_meta_kv_repo``, the write
        is a no-op — but never raises.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        # Make the manager raise when the KV repo is requested.
        del manager.shared_meta_kv_repo
        type(manager).shared_meta_kv_repo = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no kv"))
        )
        findings = _valid_findings_json()
        mock_invoke = AsyncMock(return_value=(findings, "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert result == findings


# ── Error envelope shape (helper) ───────────────────────────────────────────


class TestClassifyError:
    """Unit tests for the ``_classify_error`` helper.

    The classifier routes raw ``invoke_agent_and_wait`` return strings
    to the three WP2 envelope kinds. It runs at zero cost per call
    (sub-string scan); safety over precision.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Error: Agent timed out after 600s.", "timeout"),
            ("Error: image-comparator does not exist.", "missing-agent"),
            ("Error: Agent 'image-comparator' not found.", "missing-agent"),
            (
                "Error: Agent failed. Vision call returned 400 — "
                "model 'gpt-4o' rejected this image.",
                "vision-failure",
            ),
            ("Error: invalid model 'foo'", "vision-failure"),
            ("Error: Something unexpected happened.", "vision-failure"),
            ("", "vision-failure"),
            (None, "vision-failure"),
        ],
    )
    def test_classify_error_routes_correctly(self, raw, expected):
        assert compare_tools_module._classify_error(raw) == expected

    def test_envelope_is_sorted_json(self):
        """The envelope shape is JSON, ``sort_keys=True``, ``kind`` + ``error``."""
        env = compare_tools_module._envelope(
            "vision-failure",
            message="boom",
            extra_field=42,
        )
        parsed = json.loads(env)
        assert parsed["kind"] == "vision-failure"
        assert parsed["error"] == "boom"
        assert parsed["extra_field"] == 42
        # ``sort_keys=True`` ⇒ the JSON string is byte-stable, which
        # makes it test-pin friendly.
        assert json.dumps(parsed, sort_keys=True) == env


# ── P2-WP3 — Vision fail-loud init gate ─────────────────────────────────────


class TestVisionFailLoudInit:
    """P2-WP3 AC-5 / arch §8 🔴 — silent default resolution is FORBIDDEN.

    The comparator's ``vision`` model alias must be in
    ``config.llm.allowed_models``; if not AND the list is non-empty,
    the factory raises ``VisionModelNotAllowedError`` at init time
    (never at first spawn).
    """

    def test_init_passes_when_vision_is_in_allowed_models(self):
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager(allowed_models=["vision", "agentic"])
        # Should not raise.
        tools = create_compare_tools(manager, "test-instance-id")
        assert tools[0].name == "compare_images"

    def test_init_passes_when_allowed_models_is_empty(self):
        """Empty ``allowed_models`` = "all models allowed" (canonical default)."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager(allowed_models=[])
        tools = create_compare_tools(manager, "test-instance-id")
        assert tools[0].name == "compare_images"

    def test_init_fails_loud_when_vision_missing_from_allowed_models(self):
        from daemon.tools.compare_tools import (
            VisionModelNotAllowedError,
            create_compare_tools,
        )

        manager = _make_manager(allowed_models=["agentic", "coding"])
        with pytest.raises(VisionModelNotAllowedError) as exc_info:
            create_compare_tools(manager, "test-instance-id")

        # The error message surfaces the missing alias + how to fix it.
        assert "vision" in str(exc_info.value)
        assert "OPENAI_SELECTABLE_MODELS" in str(exc_info.value)

    def test_init_is_case_insensitive(self):
        """The match is case-insensitive (mirrors spawn-time check)."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager(allowed_models=["VISION"])
        tools = create_compare_tools(manager, "test-instance-id")
        assert tools[0].name == "compare_images"

    def test_init_fails_loud_when_config_access_errors(self):
        """A config-access failure closes the gate (no silent pass)."""
        from daemon.tools.compare_tools import (
            VisionModelNotAllowedError,
            create_compare_tools,
        )

        manager = _make_manager(config_llm_side_effect=RuntimeError("db gone"))
        with pytest.raises(VisionModelNotAllowedError):
            create_compare_tools(manager, "test-instance-id")

    def test_init_passes_when_no_config_wired(self):
        """Partial-init manager (no ``config``) — gate is not enforceable.

        The actual spawn will surface the failure downstream; the
        facade does not block on init.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager(no_config=True)
        tools = create_compare_tools(manager, "test-instance-id")
        assert tools[0].name == "compare_images"

    def test_no_config_init_actually_strips_config(self):
        """Sanity: the no_config fixture removes ``config`` so the
        gate falls through to the no-config pass-through branch."""
        from daemon.tools.compare_tools import _verify_vision_allowed

        manager = _make_manager(no_config=True)
        # ``config`` getter raises AttributeError — the gate must
        # not raise; it returns ``None`` (no enforcement).
        result = _verify_vision_allowed(manager)
        assert result is None


# ── P2-WP3 — Input resolution (substrate id → bridge) ─────────────────────


class TestInputResolutionSubstrate:
    """P2-WP3 AC-2 — facade resolves substrate ids via the bridge."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        compare_tools_module._inflight_reuse.clear()
        yield
        compare_tools_module._inflight_reuse.clear()

    async def test_substrate_ids_route_through_bridge(self):
        """Two valid 32-hex ids → bridge resolves both → single dispatch."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        findings = _valid_findings_json()
        mock_invoke = AsyncMock(return_value=(findings, "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert result == findings
        # ``invoke_agent_and_wait`` received ``images=[uri_a, uri_b]`` —
        # AC-3 single-call shape.
        kwargs = mock_invoke.call_args.kwargs
        assert "images" in kwargs
        assert isinstance(kwargs["images"], list)
        assert len(kwargs["images"]) == 2
        # Both entries are ``data:`` URIs.
        assert all(uri.startswith("data:") for uri in kwargs["images"])

    async def test_missing_substrate_id_surfaces_input_not_found(self):
        """A 32-hex id not in the store → ``kind: input-not-found`` envelope.

        The agent is NEVER spawned with a missing image — the facade
        rejects the input BEFORE the dispatch.
        """
        from daemon.services.tmp_image_store import TmpImageNotFound
        from daemon.tools.compare_tools import create_compare_tools
        from tests.test_tmp_image_bridge import _FakeStore

        # A store that 404s on every id — input resolution surfaces
        # the failure before the dispatch.
        store = _FakeStore(
            records={},
            blobs={},
            open_full_error=TmpImageNotFound("tmp image not found"),
        )
        manager = _make_manager(tmp_image_store=store)
        mock_invoke = AsyncMock()
        with patch(
            "daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke
        ):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        envelope = json.loads(result)
        assert envelope["kind"] == "input-not-found"
        # The agent was never invoked.
        mock_invoke.assert_not_awaited()


# ── P2-WP2 review MINOR-5 — Distinct workdir failure reasons ─────────────────


class TestInputResolutionWorkdirReasons:
    """P2-WP2 review MINOR-5 — distinct ``reason`` per workdir failure class.

    The facade distinguishes three workdir-input failure modes
    (each surfaces a distinct ``reason`` string so the caller / a
    future operator can branch / triage without parsing prose):

      * ``workdir_invalid`` — ``_load_image_from_path`` raised
        ``ValueError`` (confinement / magic-byte / size /
        non-regular-file rejection; the validator refused the
        path before the open call).
      * ``workdir_unreadable`` — ``_load_image_from_path`` raised
        ``OSError`` (filesystem read failure: permissions, I/O,
        post-validator unlink race).
      * ``workdir_unknown`` — any other ``Exception`` (defensive
        catch-all for unforeseen failures: encoding errors, OOM).

    Each test patches ``_load_image_from_path`` to raise the
    canonical exception class and asserts the envelope's
    ``reason`` field is the matching string.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        compare_tools_module._inflight_reuse.clear()
        yield
        compare_tools_module._inflight_reuse.clear()

    async def test_workdir_value_error_returns_workdir_invalid(self):
        """``ValueError`` from ``_load_image_from_path`` → ``workdir_invalid``.

        Confinement / magic-byte / size rejection falls here.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock()

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            # ``_load_image_from_path`` is imported lazily inside
            # ``_resolve_workdir_input`` from
            # ``daemon.tools.image_tools`` — patch the import source
            # so the facade's lazy import binds to the mock.
            with patch(
                "daemon.tools.image_tools._load_image_from_path",
                side_effect=ValueError(
                    "Path 'img.png' is outside the project workdir boundary."
                ),
            ):
                tools = create_compare_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(
                    image_a="img.png",  # not a 32-hex → workdir branch
                    image_b=_SUBSTRATE_ID_B,
                )

        envelope = json.loads(result)
        assert envelope["kind"] == "input-not-found"
        assert envelope["reason"] == "workdir_invalid"
        assert "outside the project workdir" in envelope["error"]
        # No spawn — the agent is never invoked on a bad image.
        mock_invoke.assert_not_awaited()

    async def test_workdir_oserror_returns_workdir_unreadable(self):
        """``OSError`` from ``_load_image_from_path`` → ``workdir_unreadable``.

        Filesystem read failure falls here (validator passed; the
        read itself failed — typically permissions or I/O).
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock()

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            with patch(
                "daemon.tools.image_tools._load_image_from_path",
                side_effect=PermissionError("Permission denied: 'img.png'"),
            ):
                tools = create_compare_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(
                    image_a="img.png",
                    image_b=_SUBSTRATE_ID_B,
                )

        envelope = json.loads(result)
        assert envelope["kind"] == "input-not-found"
        assert envelope["reason"] == "workdir_unreadable"
        assert "Permission denied" in envelope["error"]
        mock_invoke.assert_not_awaited()

    async def test_workdir_unexpected_error_returns_workdir_unknown(self):
        """Any other ``Exception`` → ``workdir_unknown``.

        Defensive catch-all so a future unforeseen failure mode
        (encoding error, OOM, etc.) surfaces with a distinct
        reason the operator can triage without parsing the prose
        message.
        """
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock()

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            with patch(
                "daemon.tools.image_tools._load_image_from_path",
                side_effect=RuntimeError("unexpected codec failure"),
            ):
                tools = create_compare_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(
                    image_a="img.png",
                    image_b=_SUBSTRATE_ID_B,
                )

        envelope = json.loads(result)
        assert envelope["kind"] == "input-not-found"
        assert envelope["reason"] == "workdir_unknown"
        assert "RuntimeError" in envelope["error"]
        mock_invoke.assert_not_awaited()


# ── P2-WP3 — Single-call shape ──────────────────────────────────────────────


class TestSingleCallShape:
    """AC-3 — comparator runs ONE vision call per compare.

    Mechanism: ``invoke_agent_and_wait`` receives ``images=[uri_a,
    uri_b]`` in one dispatch; ``instance_messaging.py:113-128``
    builds the multimodal content with two ``image_url`` blocks in
    ONE message (the mechanism the existing
    ``test_multiple_images_in_one_message`` test pins at
    ``tests/unit/test_vision_routing.py:275``).
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        compare_tools_module._inflight_reuse.clear()
        yield
        compare_tools_module._inflight_reuse.clear()

    async def test_invoke_called_once_with_both_images(self):
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        findings = _valid_findings_json()
        mock_invoke = AsyncMock(return_value=(findings, "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        # Exactly one dispatch — no per-image fan-out.
        mock_invoke.assert_awaited_once()
        images = mock_invoke.call_args.kwargs["images"]
        assert len(images) == 2


# ── P2-WP3 — Findings schema validation ────────────────────────────────────


class TestFindingsSchemaValidation:
    """AC-1 — facade validates the agent's return against the findings schema.

    On schema violation the facade returns ``kind: schema-invalid``.
    The validator's enums / shapes are pinned here so a future
    schema evolution surfaces as a test failure.
    """

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        compare_tools_module._inflight_reuse.clear()
        yield
        compare_tools_module._inflight_reuse.clear()

    @pytest.mark.parametrize(
        "raw,should_pass",
        [
            # Valid: pass verdict + one per_criterion row.
            (
                json.dumps(
                    {
                        "verdict": "pass",
                        "per_criterion": [
                            {
                                "criterion": "structural_layout",
                                "result": "pass",
                                "severity": "major",
                                "evidence": ["header"],
                            }
                        ],
                        "summary": "ok",
                        "pinned_spec_sha": None,
                    }
                ),
                True,
            ),
            # Valid: fail verdict with critical severity.
            (
                json.dumps(
                    {
                        "verdict": "fail",
                        "per_criterion": [
                            {
                                "criterion": "states_a11y",
                                "result": "fail",
                                "severity": "critical",
                                "evidence": [
                                    "no focus ring visible on tab key"
                                ],
                            }
                        ],
                        "summary": "missing focus ring",
                        "pinned_spec_sha": "abc123",
                    }
                ),
                True,
            ),
            # Invalid: bad verdict.
            ('{"verdict": "ok", "per_criterion": [], "summary": "x"}', False),
            # Invalid: empty per_criterion.
            (
                json.dumps(
                    {
                        "verdict": "pass",
                        "per_criterion": [],
                        "summary": "x",
                        "pinned_spec_sha": None,
                    }
                ),
                False,
            ),
            # Invalid: bad severity.
            (
                json.dumps(
                    {
                        "verdict": "pass",
                        "per_criterion": [
                            {
                                "criterion": "x",
                                "result": "pass",
                                "severity": "high",
                                "evidence": [],
                            }
                        ],
                        "summary": "x",
                        "pinned_spec_sha": None,
                    }
                ),
                False,
            ),
            # Invalid: missing summary.
            (
                json.dumps(
                    {
                        "verdict": "pass",
                        "per_criterion": [
                            {
                                "criterion": "x",
                                "result": "pass",
                                "severity": "nit",
                                "evidence": [],
                            }
                        ],
                        "pinned_spec_sha": None,
                    }
                ),
                False,
            ),
            # Invalid: evidence is not a list of strings.
            (
                json.dumps(
                    {
                        "verdict": "pass",
                        "per_criterion": [
                            {
                                "criterion": "x",
                                "result": "pass",
                                "severity": "nit",
                                "evidence": [42, 43],
                            }
                        ],
                        "summary": "x",
                        "pinned_spec_sha": None,
                    }
                ),
                False,
            ),
        ],
    )
    def test_validator_accepts_or_rejects(self, raw, should_pass):
        result = compare_tools_module._validate_findings(raw)
        if should_pass:
            assert result is not None
            assert result["verdict"] in {
                "pass",
                "fail",
                "conditional_pass",
            }
        else:
            assert result is None

    async def test_schema_invalid_envelope_returned_to_caller(self):
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        # Missing summary → schema-invalid.
        bad_findings = json.dumps(
            {
                "verdict": "pass",
                "per_criterion": [
                    {
                        "criterion": "x",
                        "result": "pass",
                        "severity": "nit",
                        "evidence": [],
                    }
                ],
                "pinned_spec_sha": None,
            }
        )
        mock_invoke = AsyncMock(return_value=(bad_findings, "comp-1"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        envelope = json.loads(result)
        assert envelope["kind"] == "schema-invalid"

    def test_pinned_spec_sha_null_is_valid(self):
        """``pinned_spec_sha: null`` is the advisory-mode default."""
        findings = compare_tools_module._validate_findings(
            json.dumps(
                {
                    "verdict": "pass",
                    "per_criterion": [
                        {
                            "criterion": "x",
                            "result": "pass",
                            "severity": "nit",
                            "evidence": [],
                        }
                    ],
                    "summary": "x",
                    "pinned_spec_sha": None,
                }
            )
        )
        assert findings is not None
        assert findings["pinned_spec_sha"] is None

    def test_pinned_spec_sha_string_is_valid(self):
        """``pinned_spec_sha: <sha>`` is the spec-conformance shape (D6)."""
        findings = compare_tools_module._validate_findings(
            json.dumps(
                {
                    "verdict": "pass",
                    "per_criterion": [
                        {
                            "criterion": "x",
                            "result": "pass",
                            "severity": "nit",
                            "evidence": [],
                        }
                    ],
                    "summary": "x",
                    "pinned_spec_sha": "deadbeef",
                }
            )
        )
        assert findings is not None
        assert findings["pinned_spec_sha"] == "deadbeef"


# ── P2-WP3 — Output type check (AC-7) ──────────────────────────────────────


class TestOutputType:
    """AC-7 — the facade's return is NEVER an image.

    Schema-validated success returns a JSON string carrying only
    strings / enums / arrays. The envelope shape carries the same
    restriction. No image bytes / data-URI fields survive the
    facade's output.
    """

    def test_findings_schema_has_no_image_field(self):
        """The findings schema itself carries zero image fields."""
        # Walk the JSON-schema shape — verdict / per_criterion /
        # summary / pinned_spec_sha. No field name carries an image.
        findings_obj = json.loads(_valid_findings_json())
        top_level_keys = set(findings_obj.keys())
        assert "verdict" in top_level_keys
        assert "per_criterion" in top_level_keys
        assert "summary" in top_level_keys
        assert "pinned_spec_sha" in top_level_keys
        # No image keys.
        for forbidden in ("image", "image_bytes", "data_uri", "image_url"):
            assert forbidden not in top_level_keys
        # per_criterion row keys also carry no image fields.
        row_keys = set(findings_obj["per_criterion"][0].keys())
        for forbidden in ("image", "image_bytes", "data_uri", "image_url"):
            assert forbidden not in row_keys

    async def test_facade_returns_string_only(self):
        """The facade's return is always a JSON-encoded string."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        findings = _valid_findings_json()
        mock_invoke = AsyncMock(return_value=(findings, "child-id"))

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert isinstance(result, str)
        # Always parseable as JSON — the contract is a JSON-encoded
        # string of either the findings schema or an envelope.
        parsed = json.loads(result)
        assert "verdict" in parsed  # success shape

    async def test_envelope_returns_string_with_no_image_field(self):
        """Even the structured-error envelope carries no image data."""
        from daemon.tools.compare_tools import create_compare_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(
            return_value=("Error: vision exploded", "comp-1")
        )

        with patch("daemon.tools.compare_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_compare_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(
                image_a=_SUBSTRATE_ID_A,
                image_b=_SUBSTRATE_ID_B,
            )

        assert isinstance(result, str)
        parsed = json.loads(result)
        # Envelope keys never include image payloads.
        for forbidden in ("image", "image_bytes", "data_uri", "image_url"):
            assert forbidden not in str(parsed).lower()
