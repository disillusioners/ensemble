"""Unit tests for the slice-⑤ plugin tool factory (REC §1.2 component 12).

Coverage:
- ADAPTER_CLASS_TABLE builds (no dynamic loading; CON §7 preserved).
- ``register_adapter`` adds a binding at runtime.
- ``build_tools_for_port`` binds a single Port to a single LangChain
  tool with the Port's args_schema.
- ``build_plugin_tools`` aggregates across the registry.
- A seam-gate failure surfaces as a refused_ports tuple (the tool
  is NOT built).
- An unknown ``adapter_id`` is refused cleanly.
- The tool invocation routes to the adapter's ``execute_dict`` /
  ``compose_dict`` / ``save_dict`` / ``lint`` entrypoint.
- Project uses ``inspect`` module is NOT shadowed by ``inspect`` attribute
  on the factory (CON §7 sentinel-style guard).
"""

from __future__ import annotations

from typing import Any

import pytest

from daemon.plugin_subsystem import (
    build_plugin_tools,
    build_tools_for_port,
    declared_opendesign_ports,
    register_adapter,
    validate_ports,
)
from daemon.plugin_subsystem.opendesign.compose_brief import OdComposeBrief
from daemon.plugin_subsystem.opendesign.generate import OdGenerate, GenerateInput
from daemon.plugin_subsystem.opendesign.lint import OdLint
from daemon.plugin_subsystem.opendesign.save import OdSave
from daemon.plugin_subsystem.plugin_tool_factory import (
    ADAPTER_CLASS_TABLE,
    ADAPTER_REGISTRY,
)
from daemon.plugin_subsystem.port_registry import Port


class TestAdapterClassTable:
    def test_default_table_has_all_four_opendesign_adapters(self):
        assert "opendesign.generate.v1" in ADAPTER_CLASS_TABLE
        assert "opendesign.compose_brief.v1" in ADAPTER_CLASS_TABLE
        assert "opendesign.save.v1" in ADAPTER_CLASS_TABLE
        assert "opendesign.lint.v1" in ADAPTER_CLASS_TABLE

    def test_table_values_are_classes(self):
        for adapter_id, cls in ADAPTER_CLASS_TABLE.items():
            assert isinstance(cls, type), f"{adapter_id} should map to a class"

    def test_registry_is_mirror_of_table(self):
        """The legacy ``ADAPTER_REGISTRY`` alias matches ``ADAPTER_CLASS_TABLE``."""
        assert ADAPTER_REGISTRY is ADAPTER_CLASS_TABLE


class TestRegisterAdapter:
    def test_register_new_adapter(self):
        class _DummyAdapter:
            @staticmethod
            def execute_dict(raw):
                return {"ok": True}

        register_adapter("dummy.test.v1", _DummyAdapter)
        assert ADAPTER_CLASS_TABLE["dummy.test.v1"] is _DummyAdapter

    def test_register_rejects_non_class(self):
        with pytest.raises(TypeError, match="expected a class"):
            register_adapter("dummy.bad", "not-a-class")


class TestBuildToolsForPort:
    def test_builds_tool_for_each_declared_port(self):
        """Every declared opendesign Port produces exactly one tool."""
        ports, _ = validate_ports(declared_opendesign_ports())
        for port in ports:
            tools = build_tools_for_port(port)
            assert len(tools) == 1
            assert tools[0].name == port.port_id

    def test_tool_args_schema_matches_port_inputs(self):
        """The tool's args_schema mirrors the Port's inputs_schema
        (langchain_core refines the JSON Schema into a Pydantic-derived
        shape; we check the names + types are present rather than
        structural equality)."""
        ports, _ = validate_ports(declared_opendesign_ports())
        for port in ports:
            tools = build_tools_for_port(port)
            tool = tools[0]
            # The tool has args (a dict; langchain strips some JSON-Schema
            # noise like additionalProperties). Check the named properties
            # are present.
            for prop_name in port.inputs_schema.get("properties", {}):
                assert prop_name in tool.args, (
                    f"{port.port_id} tool missing arg {prop_name}"
                )
            # Check the required fields are propagated.
            for required in port.inputs_schema.get("required", []):
                assert required in tool.args, (
                    f"{port.port_id} tool missing required arg {required}"
                )

    def test_tool_description_includes_capability_tags(self):
        ports, _ = validate_ports(declared_opendesign_ports())
        for port in ports:
            tools = build_tools_for_port(port)
            tool = tools[0]
            # The description surfaces capability_tags + consumer names so
            # the agent's tool-help output is informative.
            for tag in port.capability_tags:
                assert tag in tool.description, f"{port.port_id} tool description missing tag {tag}"
            for consumer in port.consumers:
                assert consumer in tool.description, f"{port.port_id} tool description missing consumer {consumer}"


class TestBuildPluginTools:
    def test_aggregate_builds_four_tools(self):
        tools, refused = build_plugin_tools()
        assert len(tools) == 4
        assert refused == []
        names = sorted(t.name for t in tools)
        assert names == sorted(["od.generate", "od.compose_brief", "od.save", "od.lint"])

    def test_seam_gate_failure_surfaces_as_refused(self):
        """A Port with no consumers fails the seam gate → tool not built."""
        bad_port = Port(
            port_id="od.broken",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="opendesign.generate.v1",
            provider_path="B",
            consumers=(),  # no consumers
        )
        from daemon.plugin_subsystem.port_registry import PortRegistry

        reg = PortRegistry([bad_port])
        tools, refused = build_plugin_tools(registry=reg)
        assert tools == []
        assert len(refused) == 1
        assert refused[0][0] == "od.broken"


class TestToolInvocationRouting:
    """Each tool routes to the correct adapter callable."""

    def test_compose_brief_invokes_compose_dict(self):
        from daemon.plugin_subsystem.opendesign.compose_brief import (
            OdComposeBrief,
        )

        ports, _ = validate_ports(declared_opendesign_ports())
        cb_port = next(p for p in ports if p.port_id == "od.compose_brief")
        tools = build_tools_for_port(cb_port)
        tool = tools[0]
        result = tool.invoke({"page_prompt": "test prompt"})
        # OdComposeBrief.compose_dict returns {"prompt": "..."} per its outputs schema.
        assert "prompt" in result
        assert "[page brief]" in result["prompt"]
        assert "test prompt" in result["prompt"]

    def test_lint_invokes_lint_dict(self):
        """F1-round-3 fix: the lint tool routes through ``OdLint.lint_dict``
        (dict entrypoint) and returns REAL verdicts — the prior mapping
        handed the kwargs DICT to ``OdLint.lint(html: str)`` and every
        invocation collapsed to the fail-1 empty-HTML verdict. The
        round-1 version of this test asserted only key-presence and
        passed while the tool was broken (vacuous — banned for this
        surface)."""
        ports, _ = validate_ports(declared_opendesign_ports())
        ln_port = next(p for p in ports if p.port_id == "od.lint")
        tools = build_tools_for_port(ln_port)
        tool = tools[0]
        good_html = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>Probe</title>\n<style>body{margin:0}</style>\n</head>\n"
            "<body>\n<main><p>Hello</p></main>\n</body>\n</html>"
        )
        result = tool.invoke({"html": good_html})
        # REAL verdict: a structurally complete, rule-clean document PASSES.
        assert result == {"verdict": "pass", "fail_count": 0, "failures": []}

        # REAL verdict: a rule-violating document fails with the right
        # rule ids (missing doctype/html/head/title/body + a TODO marker).
        bad_html = "<p>no structure here TODO</p>"
        result = tool.invoke({"html": bad_html})
        assert result["verdict"] == "fail-3"  # 7+ structural failures
        rule_ids = {f["rule_id"] for f in result["failures"]}
        assert {"R1", "R2", "R3", "R4", "R5", "R6", "R16"} <= rule_ids
        assert result["fail_count"] == len(result["failures"])

        # REAL verdict: the empty-HTML guard keeps its documented shape.
        result = tool.invoke({"html": ""})
        assert result["verdict"] == "fail-1"
        assert result["failures"][0]["rule_id"] == "EOF"
        assert result["failures"][0]["message"] == "empty HTML passed to lint"

    def test_lint_tool_truncation_marker_is_fail_4_halt(self):
        """R14 (truncation marker) through the REAL factory lane → fail-4
        (the halt verdict that must never ride into a brief)."""
        ports, _ = validate_ports(declared_opendesign_ports())
        ln_port = next(p for p in ports if p.port_id == "od.lint")
        tool = build_tools_for_port(ln_port)[0]
        truncated = (
            "<!doctype html><html><head>"
            "<meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>t</title>"
            "<style>body{margin:0}</style></head><body>"
            "<!-- Generation timed out before completion -->"
            "</body></html>"
        )
        result = tool.invoke({"html": truncated})
        assert result["verdict"] == "fail-4"
        assert [f["rule_id"] for f in result["failures"]] == ["R14"]

    def test_lint_dict_rejects_non_object_input(self):
        """``OdLint.lint_dict`` keeps the documented verdict shape for
        non-object input (never raises through the factory lane)."""
        result = OdLint.lint_dict(["not", "a", "dict"])
        assert result["verdict"] == "fail-1"
        assert result["failures"][0]["message"] == "input must be a JSON object"

    def test_generate_invokes_execute_dict_with_env_forwarded(self):
        ports, _ = validate_ports(declared_opendesign_ports())
        gen_port = next(p for p in ports if p.port_id == "od.generate")
        tools = build_tools_for_port(gen_port)
        tool = tools[0]

        # Stub the LLM seam so the test is offline.
        class _Usage:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            class completion_tokens_details:
                pass

        class _Resp:
            choices = []
            usage = _Usage()

        class _Comps:
            def create(self, **kwargs):
                return _Resp()

        class _Chat:
            completions = _Comps()

        class _Client:
            chat = _Chat()

        saved_factory = OdGenerate._LLM_INVOKER

        def _factory(**_kwargs):
            return _Client().chat.completions.create()

        OdGenerate._LLM_INVOKER = staticmethod(_factory)
        try:
            # Pass an empty prompt — the adapter refuses BEFORE the LLM call.
            result = tool.invoke({"prompt": ""})
            assert result["error"]["code"] == "prompt_composition_failed"
        finally:
            OdGenerate._LLM_INVOKER = saved_factory

    def test_generate_full_success_through_factory_lane(self):
        """Sweep (F1-round-3): a COMPLETE generate response through the
        REAL factory tool returns the success ``outputs_schema`` shape —
        the routing carries compose → call → gates → output end-to-end."""
        ports, _ = validate_ports(declared_opendesign_ports())
        gen_port = next(p for p in ports if p.port_id == "od.generate")
        tool = build_tools_for_port(gen_port)[0]
        complete_html = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>Probe</title>\n<style>body{margin:0}</style>\n</head>\n"
            "<body>\n<main><p>Hello</p></main>\n</body>\n</html>"
        )

        class _Msg:
            content = complete_html

        class _Choice:
            finish_reason = "stop"
            message = _Msg()

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 20
            total_tokens = 30

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        class _Comps:
            def create(self, **kwargs):
                # The composed system prompt must be the FULL vendored
                # runtime strings (F1: no truncation riding into the call).
                assert "## Direction library — infer and bind by default" in kwargs["messages"][0]["content"]
                return _Resp()

        class _Chat:
            completions = _Comps()

        class _Client:
            chat = _Chat()

        saved_factory = OdGenerate._LLM_INVOKER

        def _invoker(**kwargs):
            return _Client().chat.completions.create(
                messages=[
                    {"role": "system", "content": kwargs["system_prompt"]},
                    {"role": "user", "content": kwargs["user_prompt"]},
                ],
                model=kwargs.get("model", "vision"),
                max_tokens=kwargs.get("max_tokens", 64000),
                temperature=kwargs.get("temperature", 0.7),
                timeout=kwargs.get("timeout", 120.0),
            )

        OdGenerate._LLM_INVOKER = staticmethod(_invoker)
        try:
            result = tool.invoke({"prompt": "landing page", "kind": "prototype"})
        finally:
            OdGenerate._LLM_INVOKER = saved_factory
        assert result["html"] == complete_html
        assert result["finish_reason"] == "stop"
        assert result["truncated"] is False
        assert result["usage"]["total_tokens"] == 30

    def test_designer_step3_lint_gate_exercisable_via_factory_lane(self):
        """The designer's Step-3 quality gate (workflow.md step 3: run
        ``od.lint`` on every generated page; a ``fail`` verdict never
        rides into the developer's brief) is EXERCISABLE through the
        real factory-built tools: generate-shaped output → lint tool →
        gate decision from the ACTUAL verdict payload.

        The A1 live-failure shape (mid-CSS truncation: no closing tags)
        must FAIL the gate; the complete document must PASS it.
        """
        ports, _ = validate_ports(declared_opendesign_ports())
        ln_port = next(p for p in ports if p.port_id == "od.lint")
        lint_tool = build_tools_for_port(ln_port)[0]

        complete_html = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>Probe</title>\n<style>body{margin:0}</style>\n</head>\n"
            "<body>\n<main><p>Hello</p></main>\n</body>\n</html>"
        )
        a1_truncated = (
            "<!doctype html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"utf-8\">\n<title>Probe</title>\n<style>\n"
            "body { margin: 0; display: flex;"  # cut mid-CSS, no closes
        )

        def gate(html: str) -> tuple[str, bool]:
            verdict = lint_tool.invoke({"html": html})
            # The gate decision is the VERDICT STRING, exactly as the
            # designer workflow consumes it.
            ok = verdict["verdict"] == "pass"
            return verdict["verdict"], ok

        assert gate(complete_html) == ("pass", True)
        verdict, ok = gate(a1_truncated)
        assert ok is False
        assert verdict.startswith("fail-")
        # The truncation signature is the EOF gate (unbalanced html/head/body).
        rule_ids = {f["rule_id"] for f in lint_tool.invoke({"html": a1_truncated})["failures"]}
        assert "EOF" in rule_ids

    def test_save_invokes_save_dict_with_project_root_forwarded(self):
        """``od.save`` requires ``project_root``; the factory forwards it
        into the closure and the save REALLY writes the canonical file
        (real-verdict sweep: the prior version asserted only that the
        payload had a 'path' or 'error' key)."""
        import tempfile
        from pathlib import Path

        ports, _ = validate_ports(declared_opendesign_ports())
        sv_port = next(p for p in ports if p.port_id == "od.save")
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            tools = build_tools_for_port(sv_port, project_root=project_root)
            tool = tools[0]
            html = (
                "<!doctype html><html><head><title>t</title></head>"
                "<body><p>OK</p></body></html>"
            )
            result = tool.invoke({
                "html": html,
                "feature_slug": "demo-tool-factory",
                "page_slug": "test",
            })
            # REAL verdict: the canonical file exists on disk with the
            # exact bytes handed to the tool.
            assert "path" in result, f"save returned error payload: {result}"
            written = project_root / result["path"] if not Path(result["path"]).is_absolute() else Path(result["path"])
            assert written.is_file()
            assert written.read_text(encoding="utf-8") == html


class TestNoInspectShadowing:
    """CON §7-style guard: the factory's name 'inspect' is NOT a builtin shadow."""

    def test_no_inspect_attribute(self):
        """The factory module does NOT export an ``inspect`` attribute
        (the legacy name would shadow the Python stdlib ``inspect``
        module — a small but real concern when reviewer tools walk the
        factory)."""
        from daemon.plugin_subsystem import plugin_tool_factory

        assert not hasattr(plugin_tool_factory, "inspect"), (
            "plugin_tool_factory must not export an 'inspect' attribute "
            "(would shadow the stdlib inspect module used by CON §7 "
            "sentinels and reviewer tooling)"
        )