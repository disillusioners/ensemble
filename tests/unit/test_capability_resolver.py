"""Unit tests for ``daemon.services.capability_resolver`` (Phase 3 WP1–WP4).

Covers all four work packages:

* **WP1** — :func:`parse_requires_block` + :class:`CapabilityRequirement`.
  * Valid ``requires:`` parse (mcp + tools + env).
  * Unknown keys under ``requires:`` lint-warn but load.
  * Malformed ``requires:`` hard-fails (non-dict → ValueError).
  * Missing ``requires:`` → default empty requirement (back-compat).
  * Round-trip JSON serialization.

* **WP2** — :func:`capability_check` tri-state + mandatory-first-instruction rule.
  * ``present`` across all three categories.
  * ``missing`` across all three categories.
  * ``unconfigured`` for an installed-but-misconfigured MCP.
  * Mandatory-first-instruction rule: passes when body begins with
    ``capability_check(...)``; fails when body starts with prose / heading.
  * Aggregate auto-dispatch (no ``capability_kind`` arg).

* **WP3** — :class:`EscalationEnvelope` + Result: prefix conventions.
  * Round-trip parse.
  * All three kinds accepted (incl. ``policy_denied`` schema-only).
  * Missing-field rejection.
  * Result: prefix parse (with and without trailing space).
  * Bare JSON parse (defensive).
  * Day-1 lint: ``assert_no_policy_denied_in_day1_paths`` catches a
    synthetic offender; passes on day-1 source roots.

* **WP4** — :func:`load_capabilities_registry`.
  * Valid registry path (uses the seeded ``capabilities.yaml``).
  * Missing-field rejection.
  * ``requires_secret=True`` with empty ``kms_service_id``.
  * Dangling ``installer_skill`` → lazy ``_pending=True`` flag.
  * Strict ``installer_skill`` cross-reference when names supplied.
  * Dangling ``builtin_mcp_class`` (no resolver hit).

Each WP has at least one happy-path test plus at least one
failure-mode test per acceptance bullet in
``.agents/shared/planning/designer-agent/implementation-plan/phase3-bootstrap-kms-lite.md``.

Test surface is in-memory; NO DB / network. The registry fixture
relies on the project-root ``agents/_prompt_system/innate-skills/
dynamic-skill/capabilities.yaml`` file co-located with the
``dynamic-skill`` innate skill.
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path
from typing import Iterable

import pytest
import yaml

from daemon.services.capability_resolver import (
    CapabilityCheckResult,
    CapabilityRegistryEntry,
    CapabilityRegistryError,
    CapabilityRequirement,
    DEFAULT_CAPABILITIES_REGISTRY_PATH,
    EnvelopeParseError,
    EscalationEnvelope,
    MandatoryFirstInstructionError,
    McpLookupResult,
    RESULT_PREFIX,
    assert_no_policy_denied_in_day1_paths,
    capability_check,
    check_env_capability,
    check_mcp_capability,
    check_tool_capability,
    enforce_mandatory_first_instruction,
    extend_skill_entry_with_requires,
    format_envelope_message,
    list_known_skill_names_from_skill_set_files,
    load_capabilities_registry,
    parse_envelope_from_message,
    parse_requires_block,
)


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


def _mcp_row(name: str = "opendesign", **kwargs) -> McpLookupResult:
    defaults = dict(
        name=name,
        is_active=True,
        config_env={"OPEN_DESIGN_API_KEY": "__KMS_REF__opendesign__"},
        requires_secret=True,
        bound_handle="handle-123",
    )
    defaults.update(kwargs)
    return McpLookupResult(**defaults)


def _make_tools_allow(*names: str):
    def lookup() -> list[str]:
        return list(names)
    return lookup


def _make_env_lookup(**kwargs):
    def lookup() -> dict[str, str]:
        return dict(kwargs)
    return lookup


# ═════════════════════════════════════════════════════════════════════
# WP1 — parse_requires_block + CapabilityRequirement
# ═════════════════════════════════════════════════════════════════════


class TestParseRequiresBlockValid:
    def test_parse_valid_full(self, caplog):
        raw = {
            "mcp": ["opendesign"],
            "tools": ["bash", "instance"],
            "env": ["OPEN_DESIGN_LICENSE"],
        }
        with caplog.at_level(logging.WARNING, logger="daemon.services.capability_resolver"):
            req = parse_requires_block(raw, source="<test>")
        assert req.mcp == ["opendesign"]
        assert req.tools == ["bash", "instance"]
        assert req.env == ["OPEN_DESIGN_LICENSE"]
        assert req.unknown_keys == {}
        # Empty known + no unknowns → no warnings.
        assert not any("unknown keys" in r.message for r in caplog.records)

    def test_parse_partial_only_mcp(self):
        req = parse_requires_block({"mcp": ["x"]}, source="<t>")
        assert req.mcp == ["x"]
        assert req.tools == []
        assert req.env == []

    def test_parse_empty_mapping(self):
        req = parse_requires_block({}, source="<t>")
        assert req.is_empty()
        assert req.mcp == [] and req.tools == [] and req.env == []
        assert req.unknown_keys == {}


class TestParseRequiresBlockUnknown:
    def test_unknown_key_warning_lint(self, caplog):
        raw = {
            "mcp": ["opendesign"],
            "docker": {"image": "node:20"},
            "network": True,
        }
        with caplog.at_level(logging.WARNING, logger="daemon.services.capability_resolver"):
            req = parse_requires_block(raw, source="<test>")
        # Recognized keys still parse.
        assert req.mcp == ["opendesign"]
        # Unknown keys captured for transparency, NOT hard-fail.
        assert "docker" in req.unknown_keys
        assert req.unknown_keys["docker"] == {"image": "node:20"}
        assert req.unknown_keys["network"] is True
        # Lint warning emitted.
        assert any(
            "unknown keys" in r.message
            for r in caplog.records
        )

    def test_unknown_does_not_raise(self):
        # The plan: unknown keys lint WARNING, not hard-fail.
        req = parse_requires_block(
            {"docker": "anything"}, source="<t>"
        )
        assert "docker" in req.unknown_keys


class TestParseRequiresBlockMalformed:
    def test_non_dict_raises_with_source(self):
        with pytest.raises(ValueError) as exc_info:
            parse_requires_block(
                ["opendesign"], source="<test-skill-set.yaml>"
            )
        assert "must be a mapping" in str(exc_info.value)
        assert "<test-skill-set.yaml>" in str(exc_info.value)

    def test_non_list_value_logs_warning(self, caplog):
        raw = {"mcp": 5, "tools": "bash", "env": None}
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.capability_resolver"
        ):
            req = parse_requires_block(raw, source="<t>")
        assert req.mcp == []
        assert req.tools == []
        assert req.env == []
        # Warnings emitted per bad field.
        warnings = [r.message for r in caplog.records]
        assert any("requires.mcp" in w for w in warnings)
        assert any("requires.tools" in w for w in warnings)

    def test_string_in_list_dropped_with_warning(self, caplog):
        raw = {"mcp": ["opendesign", 5, "", "valid"]}
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.capability_resolver"
        ):
            req = parse_requires_block(raw, source="<t>")
        # Only valid strings retained.
        assert req.mcp == ["opendesign", "valid"]


class TestParseRequiresBlockBackCompat:
    def test_none_means_no_requirement(self):
        req = parse_requires_block(None, source="<t>")
        assert req.is_empty()

    def test_extend_skill_entry_with_requires_absent_returns_none(self):
        assert extend_skill_entry_with_requires(
            {"name": "foo"}, source="<t>"
        ) is None

    def test_extend_skill_entry_with_requires_present(self):
        req = extend_skill_entry_with_requires(
            {"requires": {"mcp": ["opendesign"]}}, source="<t>"
        )
        assert req is not None
        assert req.mcp == ["opendesign"]


class TestCapabilityRequirementRoundTrip:
    def test_dict_round_trip(self):
        req = CapabilityRequirement(
            mcp=["opendesign"],
            tools=["bash"],
            env=["OPEN_DESIGN_LICENSE"],
        )
        out = req.to_dict()
        # Round-trips through from_dict.
        again = CapabilityRequirement.from_dict(out)
        assert again.mcp == req.mcp
        assert again.tools == req.tools
        assert again.env == req.env

    def test_json_round_trip(self):
        req = CapabilityRequirement(
            mcp=["opendesign"], tools=["bash"], env=["X"]
        )
        s = req.to_json()
        # Loadable JSON, keys in canonical order via sort_keys.
        data = json.loads(s)
        again = CapabilityRequirement.from_dict(data)
        assert again.mcp == req.mcp
        assert again.tools == req.tools
        assert again.env == req.env

    def test_round_trip_preserves_unknown_keys(self):
        req = CapabilityRequirement(
            mcp=["x"], unknown_keys={"docker": {"image": "node:20"}}
        )
        again = CapabilityRequirement.from_dict(req.to_dict())
        assert again.unknown_keys == {"docker": {"image": "node:20"}}
        assert again.mcp == ["x"]


# ═════════════════════════════════════════════════════════════════════
# WP2 — capability_check + mandatory-first-instruction rule
# ═════════════════════════════════════════════════════════════════════


class TestCapabilityCheckPresent:
    def test_mcp_present(self):
        result = check_mcp_capability(
            "opendesign", mcp_lookup=lambda name: _mcp_row(name) if name == "opendesign" else None
        )
        assert result.state == "present"
        assert result.capability_id == "opendesign"
        assert result.capability_kind == "mcp"

    def test_tools_present(self):
        result = check_tool_capability(
            "bash", tools_allow=_make_tools_allow("bash", "read")
        )
        assert result.state == "present"

    def test_env_present(self):
        result = check_env_capability(
            "OPENAI_API_KEY", env_lookup=_make_env_lookup(OPENAI_API_KEY="sk-x")
        )
        assert result.state == "present"

    def test_aggregate_dispatch_first_present(self):
        result = capability_check(
            "opendesign",
            mcp_lookup=lambda n: _mcp_row(n),
            tools_allow=_make_tools_allow("bash"),
            env_lookup=_make_env_lookup(),
        )
        # MCP is the first category; row present → present from MCP.
        assert result.state == "present"
        assert result.capability_kind == "mcp"


class TestCapabilityCheckMissing:
    def test_mcp_missing(self):
        result = check_mcp_capability(
            "opendesign", mcp_lookup=lambda n: None
        )
        assert result.state == "missing"
        assert "-> None" in result.detection_evidence

    def test_tools_missing(self):
        result = check_tool_capability(
            "bash", tools_allow=_make_tools_allow("read")
        )
        assert result.state == "missing"
        assert "not in" in result.detection_evidence

    def test_tools_empty_allowlist_is_present(self):
        """F1 fix (2026-10-02): empty tools.allow = inherit/default universe.

        Pinned regression for the day-1 load_skill pre-flight defect
        (capability_missing: bash on workers). Before the fix, an empty
        allowlist returned state="missing" (deny-all), which blocked
        load_skill against any agent whose pre-flight saw the allow list
        as empty. Now per the codebase convention
        (resolve_tool_filter empty-allow+empty-deny branch at
        instance.py:322-327 → "all tools allowed"), an empty allowlist
        returns state="present" with explicit "empty allowlist
        (inherit/default universe)" evidence so an operator can
        distinguish the two branches.
        """
        result = check_tool_capability(
            "bash", tools_allow=_make_tools_allow()
        )
        assert result.state == "present"
        assert "empty allowlist" in result.detection_evidence
        assert "inherit/default universe" in result.detection_evidence

    def test_env_missing(self):
        result = check_env_capability(
            "OPENAI_API_KEY", env_lookup=_make_env_lookup()
        )
        assert result.state == "missing"
        assert "unset" in result.detection_evidence

    def test_aggregate_no_present_returns_mcp_miss(self):
        # F1 fix (2026-10-02): empty tools.allow now means "inherit/default
        # universe" — matches resolve_tool_filter empty-allow+empty-deny
        # semantics (instance.py:322-327). The aggregate therefore sees the
        # tool axis as "present" when mcp+env miss, and returns tools (not
        # the mcp miss). Pin the new behavior here:
        result = capability_check(
            "opendesign",
            mcp_lookup=lambda n: None,
            tools_allow=_make_tools_allow(),
            env_lookup=_make_env_lookup(),
        )
        assert result.state == "present"
        # Tool axis (empty allowlist → inherit/default universe) wins.
        assert result.capability_kind == "tools"

    def test_aggregate_nonempty_tools_deny_returns_mcp_miss(self):
        # F1 fix corollary: when tools.allow is non-empty AND the skill's
        # tool is not in it, the aggregate STILL falls through to mcp/env.
        # This is the pre-F1 behavior for the non-empty deny case (preserve).
        result = capability_check(
            "opendesign",
            mcp_lookup=lambda n: None,
            tools_allow=_make_tools_allow("read"),
            env_lookup=_make_env_lookup(),
        )
        assert result.state == "missing"
        # Aggregate prefers MCP evidence.
        assert result.capability_kind == "mcp"

    def test_aggregate_tools_present_when_mcp_misses(self):
        result = capability_check(
            "bash",
            mcp_lookup=lambda n: None,
            tools_allow=_make_tools_allow("bash"),
            env_lookup=_make_env_lookup(),
        )
        assert result.state == "present"
        assert result.capability_kind == "tools"


class TestCapabilityCheckUnconfigured:
    def test_mcp_row_present_not_active(self):
        result = check_mcp_capability(
            "opendesign",
            mcp_lookup=lambda n: _mcp_row(n, is_active=False),
        )
        assert result.state == "unconfigured"
        assert "is_active=False" in result.detection_evidence

    def test_mcp_requires_secret_no_handle(self):
        result = check_mcp_capability(
            "opendesign",
            mcp_lookup=lambda n: _mcp_row(n, bound_handle=None),
        )
        assert result.state == "unconfigured"
        assert "bound handle" in result.detection_evidence

    def test_mcp_present_but_env_empty(self):
        result = check_mcp_capability(
            "opendesign",
            mcp_lookup=lambda n: _mcp_row(n, config_env={}, requires_secret=False),
        )
        assert result.state == "unconfigured"
        assert "config.env is empty" in result.detection_evidence


class TestCapabilityCheckTargeted:
    def test_capability_kind_mcp_dispatches_to_mcp(self):
        result = capability_check(
            "opendesign",
            mcp_lookup=lambda n: _mcp_row(n),
            tools_allow=_make_tools_allow(),  # doesn't matter
            env_lookup=_make_env_lookup(),    # doesn't matter
            capability_kind="mcp",
        )
        assert result.state == "present"
        assert result.capability_kind == "mcp"

    def test_capability_kind_env_dispatches_to_env(self):
        result = capability_check(
            "TOKEN",
            env_lookup=_make_env_lookup(TOKEN="abc"),
            capability_kind="env",
        )
        assert result.state == "present"
        assert result.capability_kind == "env"

    def test_no_lookup_injected_returns_missing_with_evidence(self):
        result = capability_check("opendesign", capability_kind="mcp")
        assert result.state == "missing"
        assert "<no mcp_lookup injected>" in result.detection_evidence


class TestMandatoryFirstInstructionRule:
    def test_passes_when_first_line_is_capability_check(self):
        # No heading before the call — front-matter only, then the call.
        body = textwrap.dedent(
            """
            ---
            version: 1.0.0
            ---

            capability_check("opendesign")

            # Heading comes AFTER the call

            Then do the work.
            """
        )
        req = CapabilityRequirement(mcp=["opendesign"])
        # No raise.
        enforce_mandatory_first_instruction(
            skill_name="install-opendesign",
            skill_body=body,
            requirement=req,
        )

    def test_passes_with_inline_call(self):
        body = "capability_check(\"opendesign\")\nrest of skill\n"
        req = CapabilityRequirement(mcp=["opendesign"])
        enforce_mandatory_first_instruction(
            skill_name="x", skill_body=body, requirement=req
        )

    def test_fails_on_heading(self):
        body = textwrap.dedent(
            """
            # Install OpenDesign

            This skill installs OpenDesign...
            """
        )
        req = CapabilityRequirement(mcp=["opendesign"])
        with pytest.raises(MandatoryFirstInstructionError) as exc_info:
            enforce_mandatory_first_instruction(
                skill_name="install-opendesign",
                skill_body=body,
                requirement=req,
            )
        err = exc_info.value
        assert err.skill_name == "install-opendesign"
        assert err.section == "requires"
        assert "capability_check(...)" in str(err)

    def test_fails_on_heading_before_fence(self):
        # Heading BEFORE a fenced code block that contains the call
        # — the heading is the first substantive line, so violation.
        body = textwrap.dedent(
            """
            # Some heading

            ```bash
            capability_check("opendesign")
            ```
            """
        )
        req = CapabilityRequirement(mcp=["opendesign"])
        with pytest.raises(MandatoryFirstInstructionError):
            enforce_mandatory_first_instruction(
                skill_name="x", skill_body=body, requirement=req
            )

    def test_passes_with_leading_fence(self):
        # A code fence as the very first line — substantive content
        # is inside the fence (no heading before).
        body = textwrap.dedent(
            """\
            ---
            version: 1.0.0
            ---

            ```python
            capability_check("opendesign")
            result = check.run()
            ```

            Then do the work.
            """
        )
        req = CapabilityRequirement(mcp=["opendesign"])
        # No raise — the first substantive line inside the fence is
        # the call.
        enforce_mandatory_first_instruction(
            skill_name="x", skill_body=body, requirement=req
        )

    def test_skipped_when_requirement_is_none(self):
        # Empty requirement → rule does not apply.
        body = "# Just a heading\nDo the work\n"
        # No raise when requirement is None.
        enforce_mandatory_first_instruction(
            skill_name="x", skill_body=body, requirement=None
        )

    def test_skipped_when_requirement_empty(self):
        # All-known-empty + no-unknown-key requirement → rule does not apply.
        body = "# Just a heading\nDo the work\n"
        enforce_mandatory_first_instruction(
            skill_name="x",
            skill_body=body,
            requirement=CapabilityRequirement(),
        )


# ═════════════════════════════════════════════════════════════════════
# WP3 — Escalation envelope + Result: prefix + day-1 lint
# ═════════════════════════════════════════════════════════════════════


def _envelope_kwargs(**overrides):
    base = dict(
        kind="capability_missing",
        capability="opendesign",
        installer_skill="install-opendesign",
        detection_evidence="pre_flight: mcp_servers.query -> None",
        blocker_scope="this_turn",
        resume_hint="step_after_X",
        policy_denied_reason=None,
        ts="2026-09-26T20:41:31+00:00",
    )
    base.update(overrides)
    return base


class TestEscalationEnvelopeSchema:
    def test_round_trip(self):
        env = EscalationEnvelope(**_envelope_kwargs())
        # JSON-serializable.
        s = env.to_json()
        data = json.loads(s)
        # All seven §7.2 keys present.
        for key in (
            "kind",
            "capability",
            "installer_skill",
            "detection_evidence",
            "blocker_scope",
            "resume_hint",
            "policy_denied_reason",
            "ts",
        ):
            assert key in data
        # Round-trip back.
        env2 = EscalationEnvelope.model_validate_json(s)
        assert env2 == env

    def test_all_three_kinds_accepted_by_schema(self):
        for kind in ("capability_missing", "installed_but_unconfigured", "policy_denied"):
            kwargs = _envelope_kwargs(
                kind=kind,
                policy_denied_reason=(
                    "no budget" if kind == "policy_denied" else None
                ),
            )
            env = EscalationEnvelope(**kwargs)
            assert env.kind == kind

    def test_unknown_kind_rejected(self):
        with pytest.raises(Exception):
            EscalationEnvelope(**_envelope_kwargs(kind="not_a_kind"))

    def test_invalid_ts_rejected(self):
        with pytest.raises(Exception):
            EscalationEnvelope(**_envelope_kwargs(ts="not-a-timestamp"))

    def test_invalid_blocker_scope_rejected(self):
        with pytest.raises(Exception):
            EscalationEnvelope(**_envelope_kwargs(blocker_scope="everywhere"))

    def test_missing_field_rejected(self):
        partial = _envelope_kwargs()
        partial.pop("capability")
        with pytest.raises(Exception):
            EscalationEnvelope(**partial)

    def test_policy_denied_reason_must_be_null_for_non_policy_denied(self):
        with pytest.raises(Exception) as exc_info:
            EscalationEnvelope(
                **_envelope_kwargs(
                    kind="capability_missing",
                    policy_denied_reason="forbidden",
                )
            )
        assert "policy_denied_reason" in str(exc_info.value)

    def test_policy_denied_reason_required_when_kind_policy_denied(self):
        with pytest.raises(Exception) as exc_info:
            EscalationEnvelope(
                **_envelope_kwargs(
                    kind="policy_denied",
                    policy_denied_reason=None,
                )
            )
        assert "policy_denied_reason" in str(exc_info.value)

    def test_extra_field_rejected(self):
        kwargs = _envelope_kwargs()
        kwargs["unknown"] = "x"
        with pytest.raises(Exception):
            EscalationEnvelope(**kwargs)


class TestEnvelopeFromCheck:
    def test_missing_lifts_to_capability_missing(self):
        check = CapabilityCheckResult(
            state="missing",
            capability_id="opendesign",
            capability_kind="mcp",
            detection_evidence="pre_flight: mcp_servers.query -> None",
        )
        env = EscalationEnvelope.from_check(
            check, installer_skill="install-opendesign"
        )
        assert env.kind == "capability_missing"
        assert env.capability == "opendesign"
        assert env.installer_skill == "install-opendesign"
        # Now-stamped — non-empty ISO-8601.
        assert env.ts and "T" in env.ts
        # policy_denied_reason null for non-policy_denied kind.
        assert env.policy_denied_reason is None
        # Default resume_hint applied.
        assert env.resume_hint == "step_after_preflight"

    def test_unconfigured_lifts_to_installed_but_unconfigured(self):
        check = CapabilityCheckResult(
            state="unconfigured",
            capability_id="opendesign",
            capability_kind="mcp",
            detection_evidence="pre_flight: bound_handle missing",
        )
        env = EscalationEnvelope.from_check(
            check, installer_skill="install-opendesign"
        )
        assert env.kind == "installed_but_unconfigured"

    def test_present_raises(self):
        check = CapabilityCheckResult(
            state="present", capability_id="opendesign"
        )
        with pytest.raises(ValueError):
            EscalationEnvelope.from_check(
                check, installer_skill="install-opendesign"
            )


class TestResultPrefixFormat:
    def test_format_prefix(self):
        env = EscalationEnvelope(**_envelope_kwargs())
        out = format_envelope_message(env)
        # Begins with the canonical prefix.
        assert out.startswith("Result: ")
        # Body is valid JSON.
        body_json = out[len(RESULT_PREFIX):].rstrip()
        data = json.loads(body_json)
        assert data["kind"] == env.kind
        assert data["capability"] == env.capability

    def test_format_no_newline_strips(self):
        env = EscalationEnvelope(**_envelope_kwargs())
        out = format_envelope_message(env).rstrip("\n")
        # Without the trailing newline the canonical prefix still holds.
        assert out.startswith("Result: ")

    def test_format_rejects_empty_prefix(self):
        env = EscalationEnvelope(**_envelope_kwargs())
        with pytest.raises(ValueError):
            format_envelope_message(env, prefix="")

    def test_format_rejects_non_string_prefix(self):
        env = EscalationEnvelope(**_envelope_kwargs())
        with pytest.raises(ValueError):
            format_envelope_message(env, prefix=None)


class TestResultPrefixParse:
    def _make_envelope(self, **overrides):
        return EscalationEnvelope(**_envelope_kwargs(**overrides))

    def test_parse_with_prefix(self):
        env = self._make_envelope()
        body = format_envelope_message(env)
        parsed = parse_envelope_from_message(body)
        assert parsed == env

    def test_parse_with_prefix_no_trailing_space(self):
        # Older test fixtures stripped the trailing space; tolerate both.
        env = self._make_envelope()
        body = format_envelope_message(env)
        body_alt = "Result:" + body[len(RESULT_PREFIX):]
        parsed = parse_envelope_from_message(body_alt)
        assert parsed == env

    def test_parse_with_leading_whitespace(self):
        env = self._make_envelope()
        body = format_envelope_message(env)
        # Leading whitespace tolerated.
        parsed = parse_envelope_from_message("   \n" + body)
        assert parsed == env

    def test_parse_bare_json(self):
        env = self._make_envelope()
        bare = env.to_json()
        parsed = parse_envelope_from_message(bare)
        assert parsed == env

    def test_parse_invalid_json_raises(self):
        with pytest.raises(EnvelopeParseError):
            parse_envelope_from_message("Result: not json")

    def test_parse_non_object_raises(self):
        with pytest.raises(EnvelopeParseError):
            parse_envelope_from_message("Result: \"string only\"")

    def test_parse_empty_raises(self):
        with pytest.raises(EnvelopeParseError):
            parse_envelope_from_message("   \n")

    def test_parse_missing_field_raises(self):
        partial = json.loads(self._make_envelope().to_json())
        partial.pop("capability")
        body = "Result: " + json.dumps(partial)
        with pytest.raises(EnvelopeParseError):
            parse_envelope_from_message(body)

    def test_parse_unknown_kind_raises(self):
        partial = json.loads(self._make_envelope().to_json())
        partial["kind"] = "not_a_real_kind"
        body = "Result: " + json.dumps(partial)
        with pytest.raises(EnvelopeParseError):
            parse_envelope_from_message(body)


class TestPolicyDeniedDay1Lint:
    def test_passes_on_day1_source_roots(self):
        # The default set (this module + skill_seed_service) must NOT
        # contain the forbidden literal call.
        assert_no_policy_denied_in_day1_paths()

    def test_rejects_synthetic_offender_string(self):
        # Use proper Python syntax (assign to a name + kwarg).
        bad = (
            'def make():\n'
            '    return EscalationEnvelope(kind="policy_denied", '
            'capability="x", installer_skill="y", detection_evidence="z", '
            'blocker_scope="this_turn", resume_hint="r", '
            'policy_denied_reason=None, ts="2026-01-01T00:00:00+00:00")\n'
        )
        with pytest.raises(AssertionError) as exc_info:
            assert_no_policy_denied_in_day1_paths(scan_string=bad)
        assert "policy_denied" in str(exc_info.value)

    def test_rejects_now_envelope_form(self):
        bad = (
            'def make():\n'
            '    return EscalationEnvelope.now_envelope('
            'kind="policy_denied", capability="x", installer_skill="y", '
            'detection_evidence="z", blocker_scope="this_turn", '
            'resume_hint="r", policy_denied_reason=None)\n'
        )
        with pytest.raises(AssertionError) as exc_info:
            assert_no_policy_denied_in_day1_paths(scan_string=bad)
        assert "policy_denied" in str(exc_info.value)

    def test_rejects_from_check_form(self):
        # from_check is a defensive vector — from_check can only emit
        # missing / unconfigured, but the lint flags it anyway.
        bad = (
            'def make():\n'
            '    return EscalationEnvelope.from_check('
            'check_result=result, installer_skill="x", '
            'kind="policy_denied")\n'
        )
        with pytest.raises(AssertionError):
            assert_no_policy_denied_in_day1_paths(scan_string=bad)

    def test_passes_when_no_violation_in_string(self):
        good = (
            'def make():\n'
            '    return EscalationEnvelope.now_envelope('
            'kind="capability_missing", capability="x", '
            'installer_skill="y", detection_evidence="z")\n'
        )
        assert_no_policy_denied_in_day1_paths(scan_string=good)

    def test_passes_when_string_has_only_schema_references(self):
        # The schema code legitimately carries the policy_denied
        # enum member in the Literal + validators + error paths; the
        # AST walk ignores those (no Call node constructs with it).
        schema_like = (
            'EnvelopeKind = Literal[\n'
            '    "capability_missing", "installed_but_unconfigured", '
            '"policy_denied"\n'
            ']\n'
            '\n'
            'class EscalationEnvelope:\n'
            '    policy_denied_reason = None\n'
            '    kind = "policy_denied"\n'  # assignment, NOT a call
        )
        assert_no_policy_denied_in_day1_paths(scan_string=schema_like)


# ═════════════════════════════════════════════════════════════════════
# WP4 — load_capabilities_registry
# ═════════════════════════════════════════════════════════════════════


REGISTRY_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent
    / "agents"
    / "_prompt_system"
    / "innate-skills"
    / "dynamic-skill"
    / "capabilities.yaml"
)


@pytest.fixture
def tmp_registry(tmp_path: Path) -> Path:
    """Write a per-test registry file; return its path."""

    def make(content: str) -> Path:
        p = tmp_path / "capabilities.yaml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    return make  # type: ignore[return-value]


@pytest.fixture
def real_registry_path() -> Path:
    """The co-located registry file shipped with the project."""
    if not REGISTRY_FIXTURE_PATH.exists():
        pytest.skip(
            f"real capabilities.yaml not found at {REGISTRY_FIXTURE_PATH}"
        )
    return REGISTRY_FIXTURE_PATH


class TestRegistryValid:
    def test_load_real_registry_default_path(self, real_registry_path):
        entries = load_capabilities_registry(
            real_registry_path, lazy_installer_validation=True
        )
        assert len(entries) == 1
        e = entries[0]
        assert e.capability_id == "opendesign"
        assert e.installer_skill == "install-opendesign"
        assert e.builtin_mcp_class == "OpenDesignMCP"
        assert e.schema_version == "0.16.1"
        assert e.requires_secret is True
        assert e.kms_service_id == "opendesign"
        # The install-opendesign skill LANDED (P3-WP5) — the lazy
        # default scan of the real agents/ tree now resolves the
        # installer, so the entry loads non-pending even in lazy mode.
        # (Pre-WP5 this asserted pending=True; the assertion tracks the
        # landed reality, same as the strict-load test in
        # test_worker_skill_seed.py.)
        assert e.pending is False

    def test_load_real_registry_default_cwd_resolution(self, real_registry_path):
        # Resolve via the project root (this test runs from the
        # worktree's repo root).
        entries = load_capabilities_registry(
            DEFAULT_CAPABILITIES_REGISTRY_PATH,
            lazy_installer_validation=True,
        )
        assert len(entries) >= 1
        assert entries[0].capability_id == "opendesign"

    def test_registry_entry_to_dict_round_trip(self):
        e = CapabilityRegistryEntry(
            capability_id="opendesign",
            installer_skill="install-opendesign",
            builtin_mcp_class="OpenDesignMCP",
            schema_version="0.16.1",
            requires_secret=True,
            kms_service_id="opendesign",
            pending=True,
        )
        again_dict = e.to_dict()
        assert again_dict["capability_id"] == "opendesign"
        assert again_dict["_pending"] is True


class TestRegistryMissingField:
    def test_missing_field_raises(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: opendesign
              installer_skill: install-opendesign
              # builtin_mcp_class MISSING
              schema_version: '0.16.1'
              requires_secret: true
              kms_service_id: opendesign
            """
        )
        with pytest.raises(CapabilityRegistryError) as exc_info:
            load_capabilities_registry(path)
        msg = str(exc_info.value)
        assert "missing required fields" in msg
        assert "builtin_mcp_class" in msg

    def test_missing_capability_id_raises(self, tmp_registry):
        path = tmp_registry(
            """
            - installer_skill: install-opendesign
              builtin_mcp_class: OpenDesignMCP
              schema_version: '0.16.1'
              requires_secret: true
              kms_service_id: opendesign
            """
        )
        with pytest.raises(CapabilityRegistryError):
            load_capabilities_registry(path)


class TestRegistryRequiresSecret:
    def test_requires_secret_true_empty_kms_service_id_raises(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: opendesign
              installer_skill: install-opendesign
              builtin_mcp_class: OpenDesignMCP
              schema_version: '0.16.1'
              requires_secret: true
              kms_service_id: ''
            """
        )
        with pytest.raises(CapabilityRegistryError) as exc_info:
            load_capabilities_registry(path)
        assert "requires_secret=True" in str(exc_info.value)
        assert "kms_service_id" in str(exc_info.value)

    def test_requires_secret_false_empty_kms_service_id_ok(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: foo
              installer_skill: install-foo
              builtin_mcp_class: Foo
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        entries = load_capabilities_registry(path)
        assert len(entries) == 1
        assert entries[0].kms_service_id == ""


class TestRegistryCrossReference:
    def test_dangling_installer_skill_lazy_marks_pending(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: future-mcp
              installer_skill: install-future-mcp
              builtin_mcp_class: FutureMCP
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        # Realistic scenario: skill not yet shipped; lazy validation
        # should mark pending=True and not raise.
        entries = load_capabilities_registry(
            path,
            installer_skill_names={"existing-skill"},
            lazy_installer_validation=True,
        )
        assert entries[0].pending is True
        assert entries[0].installer_skill == "install-future-mcp"

    def test_dangling_installer_skill_strict_raises(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: future-mcp
              installer_skill: install-future-mcp
              builtin_mcp_class: FutureMCP
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        # Strict validation — the skill is NOT in the manifest.
        with pytest.raises(CapabilityRegistryError) as exc_info:
            load_capabilities_registry(
                path,
                installer_skill_names={"some-other-skill"},
                lazy_installer_validation=False,
            )
        assert "install-future-mcp" in str(exc_info.value)
        assert "not present" in str(exc_info.value)

    def test_matching_installer_skill_passes(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: foo
              installer_skill: install-foo
              builtin_mcp_class: Foo
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        entries = load_capabilities_registry(
            path,
            installer_skill_names={"install-foo"},
            lazy_installer_validation=False,
        )
        assert entries[0].pending is False

    def test_dangling_builtin_mcp_class_raises(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: foo
              installer_skill: install-foo
              builtin_mcp_class: NotInRegistry
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        with pytest.raises(CapabilityRegistryError) as exc_info:
            load_capabilities_registry(
                path, builtin_mcp_resolver=lambda n: None
            )
        assert "NotInRegistry" in str(exc_info.value)
        assert "does not resolve" in str(exc_info.value)

    def test_present_builtin_mcp_class_passes(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: x
              installer_skill: install-x
              builtin_mcp_class: SomeServer
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        sentinel = object()
        entries = load_capabilities_registry(
            path, builtin_mcp_resolver=lambda n: sentinel
        )
        assert entries[0].builtin_mcp_class == "SomeServer"


class TestRegistryStructural:
    def test_empty_registry_returns_empty(self, tmp_registry):
        path = tmp_registry("")
        entries = load_capabilities_registry(path)
        assert entries == []

    def test_top_level_must_be_list(self, tmp_registry):
        path = tmp_path_str = tmp_registry(  # noqa: F841
            """
            capability_id: opendesign
            installer_skill: install-opendesign
            """
        )
        with pytest.raises(CapabilityRegistryError):
            load_capabilities_registry(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_capabilities_registry(tmp_path / "nope.yaml")

    def test_empty_capability_id_raises(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: ''
              installer_skill: install-x
              builtin_mcp_class: X
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            """
        )
        with pytest.raises(CapabilityRegistryError):
            load_capabilities_registry(path)

    def test_multiple_entries_all_loaded(self, tmp_registry):
        path = tmp_registry(
            """
            - capability_id: foo
              installer_skill: install-foo
              builtin_mcp_class: Foo
              schema_version: '1'
              requires_secret: false
              kms_service_id: ''
            - capability_id: bar
              installer_skill: install-bar
              builtin_mcp_class: Bar
              schema_version: '1'
              requires_secret: true
              kms_service_id: bar-svc
            """
        )
        entries = load_capabilities_registry(path)
        assert len(entries) == 2
        assert {e.capability_id for e in entries} == {"foo", "bar"}


# ═════════════════════════════════════════════════════════════════════
# Auxiliary — list_known_skill_names_from_skill_set_files
# ═════════════════════════════════════════════════════════════════════


class TestSkillDiscovery:
    def test_collects_across_yaml_files(self, tmp_path):
        agents_dir = tmp_path / "agents"
        worker = agents_dir / "worker"
        worker.mkdir(parents=True)
        (worker / "skill-set.yaml").write_text(
            textwrap.dedent(
                """
                agent_id: worker
                skills:
                  - name: install-opendesign
                    version: "1.0.0"
                    auto_load: false
                    category: bootstrap
                    description: First-user installer skill
                """
            ),
            encoding="utf-8",
        )
        coder = agents_dir / "coder"
        coder.mkdir()
        (coder / "skill-set.yaml").write_text(
            textwrap.dedent(
                """
                agent_id: coder
                skills:
                  - name: work-partition
                    version: "1.0.0"
                    auto_load: true
                    category: planning
                    description: Partition helper
                  - name: code-fix
                    version: "1.2.0"
                    auto_load: false
                    category: execution
                    description: Bug fix
                """
            ),
            encoding="utf-8",
        )
        names = list_known_skill_names_from_skill_set_files(agents_dir)
        assert "install-opendesign" in names
        assert "work-partition" in names
        assert "code-fix" in names
