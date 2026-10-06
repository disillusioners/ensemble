"""Unit tests for the slice-⑤ Port registry (comp 8), capability seam
gate (comp 14), and the first real Port declarations.

Coverage:
- Port dataclass shape + named tuple semantics.
- validate_port: positive + every documented refusal code.
- validate_port_serializability: CON §3 negative tests (raw object,
  callable, datetime-without-format, binary-without-contentEncoding,
  unknown string format, bare object schema, bare array schema, type
  list nullable syntax).
- PortRegistry: lookup-by-id, lookup-by-consumer, lookup-by-capability-tag,
  duplicate-port-id refusal.
- Capability seam gate: three-role check + Definition+Provider+Consumer
  failures + the provider-without-consumer explicit refusal.
- opendesign-instance: the 4 declared Ports pass the full gate
  (validation + seam gate).
- Negative CI: a non-serializable value in a Port schema fails the
  build at validate time (the load-bearing guarantee).

Slice ⑤ tier-2 components tested:
- daemon/plugin_subsystem/port_registry.py
- daemon/plugin_subsystem/capability_seam_gate.py
- daemon/plugin_subsystem/opendesign/ports.py
"""

from __future__ import annotations

import pytest

from daemon.plugin_subsystem import (
    ANONYMOUS_CONSUMER,
    PORT_ID_PATTERN,
    Port,
    PortRefusal,
    PortRegistry,
    build_default_port_registry,
    declared_opendesign_ports,
    is_three_role_complete,
    seam_gate_check,
    seam_gate_check_batch,
    validate_port,
    validate_port_serializability,
    validate_ports,
    validate_ports_report,
)


# ---------------------------------------------------------------------------
# Port dataclass + lookup
# ---------------------------------------------------------------------------


class TestPortDataclass:
    def test_port_construction_minimal(self):
        """A Port can be constructed with the minimum required fields."""
        p = Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
        )
        assert p.port_id == "od.test"
        assert p.version == 1
        assert p.capability_tags == frozenset()
        assert p.consumers == ()

    def test_expected_error_envelope_keys(self):
        """The CON §3 envelope must carry ok + code + message (no details)."""
        p = Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={},
        )
        assert p.expected_error_envelope_keys() == frozenset({"ok", "code", "message"})

    def test_has_consumer(self):
        """Consumer membership is a string set lookup."""
        p = Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={},
            consumers=("designer.test", "job.test"),
        )
        assert p.has_consumer("designer.test")
        assert p.has_consumer("job.test")
        assert not p.has_consumer("designer.other")


class TestPortIdPattern:
    @pytest.mark.parametrize(
        "port_id",
        ["od.generate", "od.compose_brief", "opendesign.list_systems", "a.b"],
    )
    def test_valid_port_ids(self, port_id):
        import re

        assert re.match(PORT_ID_PATTERN, port_id), f"{port_id!r} should match the CON §3 dotted-kebab pattern"

    @pytest.mark.parametrize(
        "port_id",
        [
            "generate",        # no dot (needs at least one segment)
            "od",              # single segment
            "Od.generate",     # uppercase
            "od.Generate",     # uppercase second segment
            "od..generate",    # empty segment
            "od.generate.",    # trailing dot
            ".od.generate",    # leading dot
            "od-gen",          # hyphen in segment, no dot
        ],
    )
    def test_invalid_port_ids(self, port_id):
        import re

        assert not re.match(PORT_ID_PATTERN, port_id), f"{port_id!r} should be refused"


# ---------------------------------------------------------------------------
# validate_port_serializability — CON §3 negative tests
# ---------------------------------------------------------------------------


class TestNegativeSerializability:
    def test_bare_object_schema_refused(self):
        """Bare ``{}`` / ``{"type": "object"}`` without properties + without
        additionalProperties is refused (CON §3 — no contract surface)."""
        for schema in (
            {},
            {"type": "object"},
        ):
            refusal = validate_port_serializability(schema, location="test")
            assert refusal is not None
            assert refusal.code == "non_serializable_input"
            assert "must declare 'properties'" in refusal.message or "'type'" in refusal.message

    def test_bare_array_schema_refused(self):
        refusal = validate_port_serializability({"type": "array"}, location="test")
        assert refusal is not None
        assert refusal.code == "non_serializable_input"
        assert "'items'" in refusal.message

    def test_object_with_properties_passes(self):
        """When properties are declared, the schema is fine."""
        refusal = validate_port_serializability(
            {
                "type": "object",
                "properties": {
                    "x": {"type": "string"},
                },
            },
            location="test",
        )
        assert refusal is None

    def test_object_with_additional_properties_false_passes(self):
        """An object schema with ``additionalProperties: false`` is accepted."""
        refusal = validate_port_serializability(
            {"type": "object", "additionalProperties": False},
            location="test",
        )
        assert refusal is None

    def test_binary_without_content_encoding_refused(self):
        """``contentMediaType`` without ``contentEncoding`` is refused
        (CON §3 binary-without-contentEncoding)."""
        refusal = validate_port_serializability(
            {"type": "string", "contentMediaType": "image/png"},
            location="test",
        )
        assert refusal is not None
        assert refusal.code == "non_serializable_input"
        assert "contentEncoding" in refusal.message

    def test_binary_with_content_encoding_passes(self):
        refusal = validate_port_serializability(
            {
                "type": "string",
                "contentMediaType": "image/png",
                "contentEncoding": "base64",
            },
            location="test",
        )
        assert refusal is None

    def test_unknown_string_format_refused(self):
        refusal = validate_port_serializability(
            {"type": "string", "format": "made-up-format"},
            location="test",
        )
        assert refusal is not None
        assert refusal.code == "non_serializable_input"
        assert "unknown string format" in refusal.message

    def test_known_string_formats_pass(self):
        for fmt in ("date-time", "date", "time", "email", "uuid", "uri", "ipv4", "ipv6", "hostname", "regex"):
            refusal = validate_port_serializability(
                {"type": "string", "format": fmt},
                location=f"test[{fmt}]",
            )
            assert refusal is None, f"format {fmt!r} should be accepted"

    def test_primitive_types_pass(self):
        for schema in (
            {"type": "string"},
            {"type": "integer", "minimum": 0},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
        ):
            assert validate_port_serializability(schema, location="test") is None

    def test_nullable_type_list_passes(self):
        """JSON Schema 2020-12 nullable: ``type: [\"string\", \"null\"]``."""
        refusal = validate_port_serializability(
            {"type": ["string", "null"]},
            location="test",
        )
        assert refusal is None

    def test_unsupported_type_refused(self):
        refusal = validate_port_serializability(
            {"type": "function"},
            location="test",
        )
        assert refusal is not None
        assert refusal.code == "non_serializable_input"

    def test_nested_walk_finds_inner_violation(self):
        """The validator walks into properties and reports the inner violation's path."""
        refusal = validate_port_serializability(
            {
                "type": "object",
                "properties": {
                    "ok_field": {"type": "string"},
                    "bad_field": {"type": "function"},
                },
            },
            location="test",
        )
        assert refusal is not None
        assert "bad_field" in (refusal.location or "")


# ---------------------------------------------------------------------------
# validate_port — full refusal-code matrix
# ---------------------------------------------------------------------------


class TestValidatePort:
    def _valid_raw(self) -> dict:
        return {
            "port_id": "od.test",
            "version": 1,
            "definition": {
                "inputs_schema": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                },
                "outputs_schema": {
                    "type": "object",
                    "properties": {"y": {"type": "string"}},
                },
                "errors": {
                    "envelope": {
                        "ok": {"const": False},
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
            "provider": {"adapter_id": "test.adapter.v1", "path": "B"},
            "consumer": ["designer.test"],
        }

    def test_valid_port(self):
        port, refusal = validate_port(self._valid_raw())
        assert refusal is None
        assert port is not None
        assert port.port_id == "od.test"
        assert port.adapter_id == "test.adapter.v1"
        assert port.provider_path == "B"
        assert port.consumers == ("designer.test",)

    def test_port_id_missing(self):
        raw = self._valid_raw()
        del raw["port_id"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "port_id_invalid"

    def test_port_id_invalid_shape(self):
        raw = self._valid_raw()
        raw["port_id"] = "single-segment"
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "port_id_invalid"

    def test_version_invalid(self):
        raw = self._valid_raw()
        raw["version"] = 0
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "version_invalid"

    def test_version_missing(self):
        raw = self._valid_raw()
        del raw["version"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "version_invalid"

    def test_definition_missing(self):
        raw = self._valid_raw()
        del raw["definition"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "definition_missing"

    def test_inputs_schema_missing(self):
        raw = self._valid_raw()
        del raw["definition"]["inputs_schema"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "inputs_schema_missing"

    def test_outputs_schema_missing(self):
        raw = self._valid_raw()
        del raw["definition"]["outputs_schema"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "outputs_schema_missing"

    def test_errors_envelope_missing(self):
        raw = self._valid_raw()
        del raw["definition"]["errors"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "errors_envelope_missing"

    def test_errors_envelope_ok_not_const_false(self):
        """``ok`` must be ``{\"const\": false}``; ``enum`` / ``type: boolean`` is refused."""
        raw = self._valid_raw()
        raw["definition"]["errors"]["envelope"]["ok"] = {"type": "boolean"}
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "errors_envelope_missing"

    def test_errors_envelope_missing_code(self):
        raw = self._valid_raw()
        del raw["definition"]["errors"]["envelope"]["code"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "errors_envelope_missing"

    def test_capability_tags_must_be_list_of_strings(self):
        raw = self._valid_raw()
        raw["definition"]["capability_tags"] = ["valid", 42, ""]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "capability_tags_invalid"

    def test_provider_missing(self):
        raw = self._valid_raw()
        del raw["provider"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "provider_missing"

    def test_adapter_id_invalid(self):
        raw = self._valid_raw()
        raw["provider"]["adapter_id"] = ""
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "adapter_id_invalid"

    def test_provider_path_invalid(self):
        raw = self._valid_raw()
        raw["provider"]["path"] = "Z"  # not B/C/A
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "provider_path_invalid"

    def test_consumer_missing(self):
        raw = self._valid_raw()
        del raw["consumer"]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "consumer_missing"

    def test_consumer_anonymous_refused(self):
        raw = self._valid_raw()
        raw["consumer"] = [ANONYMOUS_CONSUMER]
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "consumer_anonymous"

    def test_consumer_empty_list_refused(self):
        raw = self._valid_raw()
        raw["consumer"] = []
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "consumer_missing"

    def test_non_serializable_input_refused(self):
        """Negative-CI coverage — non-JSON-serializable type in inputs_schema."""
        raw = self._valid_raw()
        raw["definition"]["inputs_schema"] = {
            "type": "object",
            "properties": {"bad": {"type": "function"}},
        }
        port, refusal = validate_port(raw)
        assert port is None
        assert refusal is not None
        assert refusal.code == "non_serializable_input"


class TestValidatePortsBatch:
    def test_batch_returns_valid_and_refused_lists(self):
        valid = {
            "port_id": "od.test1",
            "version": 1,
            "definition": {
                "inputs_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                "outputs_schema": {"type": "object", "properties": {"y": {"type": "string"}}},
                "errors": {"envelope": {"ok": {"const": False}, "code": "x", "message": "x"}},
            },
            "provider": {"adapter_id": "t1", "path": "B"},
            "consumer": ["designer.test1"],
        }
        invalid = {"port_id": "single-segment", "version": 1}
        ports, refusals = validate_ports([valid, invalid])
        assert len(ports) == 1
        assert ports[0].port_id == "od.test1"
        assert len(refusals) == 1
        assert refusals[0].code == "port_id_invalid"


# ---------------------------------------------------------------------------
# PortRegistry — lookup + duplicate-id refusal
# ---------------------------------------------------------------------------


class TestPortRegistry:
    def _sample_port(self, port_id: str = "od.test") -> Port:
        return Port(
            port_id=port_id,
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="test.adapter.v1",
            provider_path="B",
            consumers=("designer.test",),
            capability_tags=frozenset({"foo", "bar"}),
        )

    def test_register_and_lookup(self):
        p = self._sample_port()
        reg = PortRegistry([p])
        assert len(reg) == 1
        assert reg.get("od.test") is p
        assert "od.test" in reg

    def test_by_consumer(self):
        p = self._sample_port()
        reg = PortRegistry([p])
        assert reg.by_consumer("designer.test") == (p,)
        assert reg.by_consumer("designer.other") == ()

    def test_by_capability_tag(self):
        p = self._sample_port()
        reg = PortRegistry([p])
        assert reg.by_capability_tag("foo") == (p,)
        assert reg.by_capability_tag("missing") == ()

    def test_all(self):
        p1 = self._sample_port("od.a")
        p2 = self._sample_port("od.b")
        reg = PortRegistry([p1, p2])
        assert set(reg.all()) == {p1, p2}

    def test_duplicate_port_id_refused(self):
        p1 = self._sample_port("od.dup")
        p2 = self._sample_port("od.dup")
        with pytest.raises(ValueError, match="duplicate port_id"):
            PortRegistry([p1, p2])

    def test_get_missing_returns_none(self):
        reg = PortRegistry([])
        assert reg.get("od.missing") is None


# ---------------------------------------------------------------------------
# Capability seam gate — three-role + provider-without-consumer refusal
# ---------------------------------------------------------------------------


class TestSeamGate:
    def _complete_port(self) -> Port:
        return Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="test.adapter.v1",
            provider_path="B",
            consumers=("designer.test",),
        )

    def test_complete_port_passes(self):
        port = self._complete_port()
        verdict = seam_gate_check(port)
        assert verdict.ok
        assert verdict.failures == ()
        assert is_three_role_complete(port)

    def test_definition_incomplete_refused(self):
        port = Port(
            port_id="od.test",
            version=1,
            inputs_schema={},  # empty
            outputs_schema={},  # empty
            errors_envelope={},  # empty
            adapter_id="test",
            provider_path="B",
            consumers=("designer.test",),
        )
        verdict = seam_gate_check(port)
        assert not verdict.ok
        assert any(f.code == "definition_incomplete" for f in verdict.failures)

    def test_provider_incomplete_refused(self):
        port = Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="",  # empty
            provider_path="B",
            consumers=("designer.test",),
        )
        verdict = seam_gate_check(port)
        assert not verdict.ok
        assert any(f.code == "provider_incomplete" for f in verdict.failures)

    def test_consumer_missing_refused(self):
        port = Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="test",
            provider_path="B",
            consumers=(),
        )
        verdict = seam_gate_check(port)
        assert not verdict.ok
        assert any(f.code == "consumer_missing" for f in verdict.failures)

    def test_consumer_anonymous_refused_explicit(self):
        """CON §3: provider-without-consumer-style rejection (anonymous refused)."""
        port = Port(
            port_id="od.test",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="test",
            provider_path="B",
            consumers=(ANONYMOUS_CONSUMER,),
        )
        verdict = seam_gate_check(port)
        assert not verdict.ok
        assert any(f.code == "consumer_anonymous" for f in verdict.failures)

    def test_seam_gate_batch(self):
        good = self._complete_port()
        bad = Port(
            port_id="od.bad",
            version=1,
            inputs_schema={"type": "object"},
            outputs_schema={"type": "object"},
            errors_envelope={"ok": {"const": False}, "code": "x", "message": "x"},
            adapter_id="bad",
            provider_path="B",
            consumers=(),
        )
        verdicts = seam_gate_check_batch([good, bad])
        assert verdicts[0].ok
        assert not verdicts[1].ok


# ---------------------------------------------------------------------------
# opendesign-instance declared Ports — full gate
# ---------------------------------------------------------------------------


class TestDeclaredOpendesignPorts:
    def test_four_ports_declared(self):
        ports = declared_opendesign_ports()
        assert len(ports) == 4

    def test_declared_port_ids_match_conventions(self):
        ports = declared_opendesign_ports()
        ids = {p["port_id"] for p in ports}
        assert ids == {"od.generate", "od.compose_brief", "od.save", "od.lint"}

    def test_all_declared_ports_pass_validation(self):
        ports, refusals = validate_ports(declared_opendesign_ports())
        assert refusals == []
        assert len(ports) == 4

    def test_all_declared_ports_pass_seam_gate(self):
        ports, refusals = validate_ports(declared_opendesign_ports())
        assert refusals == []
        for p in ports:
            verdict = seam_gate_check(p)
            assert verdict.ok, f"{p.port_id} failed seam gate: {[f.code for f in verdict.failures]}"

    def test_build_default_registry_succeeds(self):
        reg = build_default_port_registry()
        assert len(reg) == 4
        assert reg.get("od.generate") is not None
        assert reg.get("od.compose_brief") is not None
        assert reg.get("od.save") is not None
        assert reg.get("od.lint") is not None

    def test_validate_ports_report_ok(self):
        report = validate_ports_report()
        assert report["ok"]
        assert report["checked"] == 4
        assert report["passed"] == 4
        assert report["refused_validation"] == []
        assert report["refused_seam_gate"] == []