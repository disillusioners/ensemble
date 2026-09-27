"""Tests for the WP11 ``[resume]`` convention (parse helper + contract).

P3-WP11 acceptance coverage:

- **parse round-trip** — ``format_resume_message`` →
  ``parse_resume_message`` preserves the four-field shape.
- **re-run capability_check on resume** —
  :func:`resume_capability_check` re-runs the tri-state pre-flight with
  injected lookups; the carried status is never trusted.
- **resume_from honored** — the parsed shape CARRIES ``resume_from``
  (the consumer contract's jump-to-step label).
- **malformed resume → escalation ``kind=capability_missing``** — via
  :func:`escalation_for_malformed_resume`, riding the full
  ``Result:`` envelope lane round-trip.

In-memory only — NO DB, NO daemon. Parser lives in
``daemon.services.capability_resolver`` next to the envelope parser.
"""

from __future__ import annotations

import json

import pytest

from daemon.services.capability_resolver import (
    EscalationEnvelope,
    RESUME_TAG,
    ResumeMessage,
    ResumeParseError,
    CapabilityCheckResult,
    McpLookupResult,
    escalation_for_malformed_resume,
    format_envelope_message,
    parse_envelope_from_message,
    format_resume_message,
    parse_resume_message,
    resume_capability_check,
    resume_status_agrees,
)


def _resume_message(**overrides) -> str:
    payload = {
        "capability_id": "opendesign",
        "status": "installed_but_unconfigured",
        "tools_now_available": ["kms_request", "kms_attach"],
        "resume_from": "step_after_kms_bind",
    }
    payload.update(overrides)
    return f"{RESUME_TAG} {json.dumps(payload)}"


class TestParseRoundTrip:
    def test_format_then_parse_round_trip(self):
        resume = ResumeMessage(
            capability_id="opendesign",
            status="unconfigured",
            tools_now_available=["kms_request"],
            resume_from="step_after_kms_bind",
        )
        parsed = parse_resume_message(format_resume_message(resume))
        assert parsed.capability_id == resume.capability_id
        assert parsed.status == resume.status
        assert parsed.tools_now_available == resume.tools_now_available
        assert parsed.resume_from == resume.resume_from
        assert parsed.to_dict() == resume.to_dict()

    def test_parse_same_line_json(self):
        parsed = parse_resume_message(_resume_message())
        assert parsed.capability_id == "opendesign"
        assert parsed.status == "installed_but_unconfigured"
        assert parsed.tools_now_available == ["kms_request", "kms_attach"]

    def test_parse_tag_alone_json_on_next_line(self):
        message = (
            "please resume the OD install\n"
            f"{RESUME_TAG}\n"
            "{\n  \"capability_id\": \"opendesign\",\n"
            "  \"status\": \"unconfigured\",\n"
            "  \"tools_now_available\": [],\n"
            "  \"resume_from\": \"step_after_preflight\"\n}\n"
            "thanks"
        )
        parsed = parse_resume_message(message)
        assert parsed.capability_id == "opendesign"
        assert parsed.resume_from == "step_after_preflight"

    def test_parse_ignores_surrounding_prose(self):
        message = "context: install resumed.\n" + _resume_message() + "\n(end)"
        assert parse_resume_message(message).capability_id == "opendesign"

    def test_resume_from_is_carried(self):
        parsed = parse_resume_message(_resume_message(resume_from="step_after_preflight"))
        assert parsed.resume_from == "step_after_preflight"

    def test_raw_preserved(self):
        parsed = parse_resume_message(_resume_message(extra_hint="mint-first"))
        assert parsed.raw["extra_hint"] == "mint-first"

    def test_empty_tools_list_is_valid(self):
        parsed = parse_resume_message(_resume_message(tools_now_available=[]))
        assert parsed.tools_now_available == []


class TestMalformedResume:
    def test_no_tag_raises(self):
        with pytest.raises(ResumeParseError, match="no .resume. block"):
            parse_resume_message("just some prose, no tag")

    def test_no_json_after_tag_raises(self):
        with pytest.raises(ResumeParseError, match="no JSON object"):
            parse_resume_message(f"{RESUME_TAG} resume please")

    def test_invalid_json_raises(self):
        with pytest.raises(ResumeParseError, match="not valid JSON"):
            parse_resume_message(f"{RESUME_TAG} {{broken")

    def test_non_object_json_raises(self):
        with pytest.raises(ResumeParseError, match="JSON object"):
            parse_resume_message(f'{RESUME_TAG} ["a", "b"]')

    def test_missing_field_raises(self):
        message = (
            f'{RESUME_TAG} {{"capability_id": "opendesign", "status": "missing"}}'
        )
        with pytest.raises(ResumeParseError, match="missing required fields"):
            parse_resume_message(message)

    @pytest.mark.parametrize("field", ["capability_id", "status", "resume_from"])
    def test_empty_or_nonstring_scalar_raises(self, field):
        payload = {
            "capability_id": "opendesign",
            "status": "missing",
            "tools_now_available": [],
            "resume_from": "step_after_preflight",
        }
        payload[field] = "" if field != "status" else 42
        with pytest.raises(ResumeParseError):
            parse_resume_message(f"{RESUME_TAG} {json.dumps(payload)}")

    def test_nonstring_tools_list_raises(self):
        message = _resume_message(tools_now_available=["ok", 7])
        with pytest.raises(ResumeParseError, match="tools_now_available"):
            parse_resume_message(message)

    def test_first_tag_wins(self):
        message = (
            _resume_message(capability_id="first")
            + "\n"
            + _resume_message(capability_id="second")
        )
        assert parse_resume_message(message).capability_id == "first"


class TestResumeCapabilityCheck:
    """Consumer step 2: re-run the pre-flight; never trust the status."""

    @staticmethod
    def _lookup(state: str):
        def _mcp_lookup(capability_id: str) -> McpLookupResult | None:
            if state == "absent":
                return None
            return McpLookupResult(
                name=capability_id,
                is_active=True,
                config_env={"OD_API_TOKEN": "v"} if state == "present" else {},
                requires_secret=(state == "present"),
                bound_handle="KMS_HANDLE_x" if state == "present" else None,
            )

        return _mcp_lookup

    def test_rerun_returns_present_after_install_lands(self):
        resume = parse_resume_message(_resume_message())
        result = resume_capability_check(
            resume, mcp_lookup=self._lookup("present")
        )
        assert result.state == "present"

    def test_rerun_returns_unconfigured_when_still_unbound(self):
        resume = parse_resume_message(_resume_message())
        result = resume_capability_check(
            resume, mcp_lookup=self._lookup("unconfigured-ish")
        )
        # has_required_secret False + active row → unconfigured
        assert result.state == "unconfigured"

    def test_no_lookup_falls_back_to_missing(self):
        resume = parse_resume_message(_resume_message())
        result = resume_capability_check(resume, mcp_lookup=lambda cid: None)
        assert result.state == "missing"

    def test_status_agreement_matrix(self):
        resume = parse_resume_message(_resume_message())  # carried: unconfigured
        assert resume_status_agrees(
            resume, CapabilityCheckResult(state="unconfigured", capability_id="opendesign", detection_evidence="e")
        )
        # Resolved during the resume window → agrees (present wins).
        assert resume_status_agrees(
            resume, CapabilityCheckResult(state="present", capability_id="opendesign", detection_evidence="e")
        )
        # Fresh missing vs carried unconfigured → disagreement.
        assert not resume_status_agrees(
            resume, CapabilityCheckResult(state="missing", capability_id="opendesign", detection_evidence="e")
        )
        # Unknown carried status agrees vacuously (informational field).
        weird = parse_resume_message(_resume_message(status="banana"))
        assert resume_status_agrees(
            weird, CapabilityCheckResult(state="missing", capability_id="opendesign", detection_evidence="e")
        )


class TestMalformedResumeEscalation:
    def test_escalation_is_capability_missing(self):
        envelope = escalation_for_malformed_resume(
            capability="opendesign",
            installer_skill="install-opendesign",
            reason="no [resume] block found in message",
        )
        assert envelope.kind == "capability_missing"
        assert envelope.capability == "opendesign"
        assert envelope.installer_skill == "install-opendesign"
        assert envelope.detection_evidence.startswith("malformed [resume] block:")
        assert envelope.policy_denied_reason is None
        assert envelope.blocker_scope == "this_task"

    def test_escalation_rides_the_result_lane(self):
        """Full round-trip through the child-report wire format."""
        envelope = escalation_for_malformed_resume(
            capability="opendesign",
            installer_skill="install-opendesign",
            reason="invalid JSON",
        )
        wire = format_envelope_message(envelope)
        assert wire.startswith("Result: ")
        parsed = parse_envelope_from_message(wire)
        assert parsed.kind == "capability_missing"
        assert isinstance(parsed, EscalationEnvelope)

    def test_escalation_supports_this_turn_scope(self):
        envelope = escalation_for_malformed_resume(
            capability="opendesign",
            installer_skill="install-opendesign",
            reason="missing fields",
            blocker_scope="this_turn",
        )
        assert envelope.blocker_scope == "this_turn"
