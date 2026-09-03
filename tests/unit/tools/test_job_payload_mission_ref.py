"""Tests for the M2 ``mission_ref`` + ``outcome: null`` payload guardrails.

Mission-class Milestone M2 (2026-09-02, ``feature/mission-class``) —
the anti-trap guardrails on every transport (job) payload (contract
draft §3). These tests pin the shape that an agent sees on the wire
so a future regression that drops ``outcome`` or ``mission_ref``
silently widens the wrong-predicate trap.

The two keys are:

* ``outcome`` — ALWAYS ``None`` on transport payloads (the asymmetric
  outcome token: ``outcome: null`` on transport = "NOT done" by
  construction; draft §3.2). On mission payloads the SAME field
  carries the outcome value when terminal.
* ``mission_ref`` — the cross-reference payload ``{mission_id,
  agent_id, liveness}`` that ties a job row to its linked mission
  (mandatory on terminal payloads per draft §3.3).

Both keys surface on every transport surface that an agent reads:

* ``JobResponse`` (the HTTP GET /api/jobs/{job_id} + /api/jobs
  list-shape response).
* ``WorkRecord.to_dict()`` (the GET /api/work + MCP tool surface).
* ``_ResolvedWork.to_payload()`` / ``to_completed_payload()`` (the
  SSE payload for the streaming surface).

The four Fix-C read surfaces (§8.2) must agree on the keys — this
test file pins the agreement with three surface-specific tests plus
a schema-level cross-cut.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.routers.jobs_crud import _derive_m2_mission_ref, _job_to_response
from daemon.routers.jobs_streaming import _ResolvedWork
from daemon.routers.schemas import JobResponse
from daemon.services.mission_resolver import mission_ref_to_dict
from daemon.services.work_resolver import _derive_m2_mission_ref_on_work


# ─── Schema-level: JobResponse carries the two new keys ──────────────────


class TestJobResponseSchema:
    """``JobResponse`` exposes ``outcome`` and ``mission_ref``."""

    def test_schema_declares_outcome_field(self) -> None:
        """``outcome`` is on the schema with ``None`` default.

        Pins the contract draft §3.2: transport payloads carry
        ``"outcome": null`` ALWAYS. The schema's ``default=None``
        makes the value unconditionally present in the serialized
        payload (a literal key the agent can branch on).
        """
        assert "outcome" in JobResponse.model_fields
        assert JobResponse.model_fields["outcome"].default is None

    def test_schema_declares_mission_ref_field(self) -> None:
        """``mission_ref`` is on the schema with ``None`` default.

        The cross-reference payload — ``{mission_id, agent_id,
        liveness}`` — is mandatory on terminal payloads (draft §3.3).
        ``None`` when the resolver degraded.
        """
        assert "mission_ref" in JobResponse.model_fields
        assert JobResponse.model_fields["mission_ref"].default is None

    def test_serializer_emits_outcome_and_mission_ref(self) -> None:
        """``_serialize`` includes the two new keys (the literal-key
        asymmetric-outcome contract).

        Even when ``outcome`` and ``mission_ref`` are ``None`` the
        keys MUST appear in the serialized dict — an absent key
        would break the agent's ``payload["outcome"] is None``
        branch. This is the "literal key the model can branch on"
        contract from §3.2.
        """
        record = JobResponse(
            job_id="job-x",
            status="processing",
            priority=5,
            agent_id="developer",
            agent_dir="agents/developer",
            project_id="p",
            queue_id=None,
            instance_id=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            started_at=None,
            completed_at=None,
            result_summary=None,
            error_message=None,
            source="api",
            message="ok",
        )
        serialized = record.model_dump()
        assert "outcome" in serialized
        assert serialized["outcome"] is None
        assert "mission_ref" in serialized
        # mission_ref may be None when the resolver degraded (no
        # work_record supplied) — the literal-key contract applies
        # to ``outcome``; ``mission_ref`` is allowed to be missing
        # in the legacy no-work-record branch (the serializer
        # always emits the key, so the consumer can defensively
        # check ``"mission_ref" in payload``).


# ─── ``_derive_m2_mission_ref`` — JobResponse payload builder ────────────


def _mock_work_record(
    *,
    mission_id: str | None = "inst-1",
    agent_id: str | None = "developer",
    mission_liveness: str | None = "processing",
    status: str | None = "processing",
    job_type: str | None = "message",
) -> Any:
    """Build a WorkRecord-shaped mock for ``_derive_m2_mission_ref``.

    Uses ``types.SimpleNamespace`` so every field has a real value
    (string fields validated by ``JobResponse`` are not
    ``MagicMock`` instances that fail Pydantic strict-mode
    validation).
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        mission_id=mission_id,
        agent_id=agent_id,
        mission_liveness=mission_liveness,
        status=status,
        job_type=job_type,
        # Fields populated by ``_job_to_response`` from the work
        # record (used to source timing, error, result_summary):
        work_id="job-1",
        kind="job",
        instance_id=mission_id or "inst-1",
        project_id="test-project",
        result_summary=None,
        error=None,
        created_at=datetime.now(timezone.utc),
        message_id=None,
        started_at=None,
        completed_at=None,
        mission_epoch=1,
        mission_terminal_reason=None,
    )


def _mock_job(
    *,
    instance_id: str | None = "inst-1",
    agent_id: str | None = "developer",
    admission_state: str = "queued",
    terminal_reason: str | None = None,
) -> Any:
    """Build a JobItem-shaped mock for ``_job_to_response``.

    Pydantic ``JobResponse`` validation enforces strict types on
    many fields (``source``, ``job_metadata``, ``idempotency_key``,
    ``deleted_at``, etc.). The mock is constructed with a
    permissive spec so attribute access succeeds for every field
    the ``_job_to_response`` path reads; only the access-relevant
    fields are seeded with real values, the rest default to
    ``MagicMock`` instances that Pydantic will reject during model
    construction. To bypass that rejection we instead build a
    plain ``types.SimpleNamespace``-backed stand-in — see
    ``_mock_job_minimal`` below for the cases that go through the
    Pydantic validator.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        instance_id=instance_id,
        agent_id=agent_id,
        admission_state=admission_state,
        terminal_reason=terminal_reason,
        # Fields consumed by ``_job_to_response`` outside the
        # ``work_record`` branch (the legacy fallback path) —
        # populated with safe defaults so Pydantic validation passes.
        job_id="job-1",
        priority=5,
        agent_dir="agents/developer",
        project_id="test-project",
        queue_id=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
        result_summary=None,
        error_message=None,
        position=None,
        message="(test)",
        source="api",
        job_metadata=None,
        cancelled_at=None,
        idempotency_key=None,
        dlq_reason=None,
        retry_count=None,
        moved_to_dlq_at=None,
        deleted_at=None,
        job_type="task",
    )


class TestDeriveM2MissionRef:
    """The cross-reference derivation under each row kind."""

    def test_mirror_row_carries_mission_liveness(
        self
    ) -> None:
        """``job_type='message'`` ⇒ ``liveness`` from ``mission_liveness``.

        The cross-reference MUST show the linked instance's canonical
        mission liveness — NOT the row's own transport status.
        Mirror rows are receipts; the underlying mission may still
        be running even after the receipt settled (the 28c6421b
        class).
        """
        record = _mock_work_record(
            mission_liveness="processing",
            status="completed",
            job_type="message",
        )
        ref = _derive_m2_mission_ref(record, _mock_job())
        assert ref == {
            "mission_id": "inst-1",
            "agent_id": "developer",
            "liveness": "processing",  # not "completed"
        }

    def test_task_row_carries_status_as_liveness(self) -> None:
        """``job_type='task'`` ⇒ ``liveness`` from ``status`` (canonical).

        For task rows ``mission_liveness`` is ``None`` by Fix C
        split-semantics design — the row IS its own mission. So
        ``liveness`` falls back to ``status`` (the canonical work
        vocabulary).
        """
        record = _mock_work_record(
            mission_liveness=None,  # task rows have mission_liveness=None
            status="completed",
            job_type="task",
        )
        ref = _derive_m2_mission_ref(record, _mock_job())
        assert ref["liveness"] == "completed"

    def test_terminal_payload_has_outcome_null(self) -> None:
        """The JobResponse carries ``outcome: None`` even on terminal rows.

        Contract draft §3.2: transport payloads ALWAYS carry
        ``"outcome": null`` — the value is reserved for mission
        payloads. The ``_job_to_response`` constructor passes
        ``outcome=None``; the JobResponse schema's
        ``_serialize`` emits the key.
        """
        record = _mock_work_record(
            status="completed",
            job_type="task",
        )
        job = _mock_job(
            admission_state="done",
            terminal_reason="completed",
        )
        # Build the JobResponse via ``_job_to_response`` — the
        # production serializer path.
        response = _job_to_response(job, work_record=record)
        # Serialize through the model (the public-facing shape)
        serialized = response.model_dump()
        assert "outcome" in serialized
        assert serialized["outcome"] is None  # ALWAYS null on transport
        # mission_ref is present (terminal payload — cross-reference mandatory)
        assert "mission_ref" in serialized
        assert serialized["mission_ref"] is not None
        assert serialized["mission_ref"]["mission_id"] == "inst-1"

    def test_terminal_payload_carries_mission_ref(self) -> None:
        """A terminal JobResponse payload carries the full
        ``mission_ref`` cross-reference.

        Draft §3.3: ``mission_ref`` is MANDATORY on terminal
        payloads. ``{mission_id, agent_id, liveness}`` — three
        keys, all present.
        """
        record = _mock_work_record(
            mission_id="inst-terminal",
            agent_id="leader",
            mission_liveness="completed",
            status="completed",
            job_type="message",
        )
        job = _mock_job(
            instance_id="inst-terminal",
            agent_id="leader",
            admission_state="done",
            terminal_reason="completed",
        )
        response = _job_to_response(job, work_record=record)
        serialized = response.model_dump()
        mission_ref = serialized["mission_ref"]
        assert mission_ref is not None
        assert mission_ref["mission_id"] == "inst-terminal"
        assert mission_ref["agent_id"] == "leader"
        assert mission_ref["liveness"] == "completed"

    def test_degraded_work_record_yields_none_mission_ref(self) -> None:
        """When the resolver degraded across all three fields, the
        ``mission_ref`` is ``None`` (the §8.2 split-semantics
        unavailable contract)."""
        record = _mock_work_record(
            mission_id=None,
            agent_id=None,
            mission_liveness=None,
            status=None,
            job_type=None,
        )
        ref = _derive_m2_mission_ref(record, _mock_job())
        assert ref is None


# ─── WorkRecord surface (the work router / MCP tool side) ────────────────


class TestWorkRecordToDict:
    """``WorkRecord.to_dict()`` carries the two new keys."""

    def test_to_dict_includes_outcome_and_mission_ref(self) -> None:
        """Both new keys appear in the serialized work record."""
        from daemon.services.work_resolver import WorkRecord

        now = datetime.now(timezone.utc)
        record = WorkRecord(
            work_id="job-1",
            kind="job",
            status="processing",
            instance_id="inst-1",
            project_id=None,
            agent_id="developer",
            result_summary=None,
            error=None,
            created_at=now,
            message_id=None,
            started_at=None,
            completed_at=None,
            job_type="message",
            mission_liveness="processing",
            mission_id="inst-1",
            mission_epoch=1,
            mission_terminal_reason=None,
            outcome=None,
            mission_ref={
                "mission_id": "inst-1",
                "agent_id": "developer",
                "liveness": "processing",
            },
        )
        d = record.to_dict()
        # Literal-key contract (same as JobResponse): ``outcome`` is
        # ALWAYS present; ``mission_ref`` is present too.
        assert "outcome" in d
        assert d["outcome"] is None
        assert "mission_ref" in d
        assert d["mission_ref"] == {
            "mission_id": "inst-1",
            "agent_id": "developer",
            "liveness": "processing",
        }


# ─── SSE payload (the streaming surface) ─────────────────────────────────


class TestResolvedWorkPayload:
    """``_ResolvedWork.to_payload()`` and ``to_completed_payload()``
    carry the two new keys."""

    def test_connected_payload_emits_outcome_and_mission_ref(self) -> None:
        """The connected/status_update payload includes both keys."""
        record = _ResolvedWork.from_work_record(MagicMock(
            work_id="job-x",
            status="processing",
            instance_id="inst-x",
            result_summary=None,
            error=None,
            job_type="message",
            mission_liveness="processing",
            mission_id="inst-x",
            mission_epoch=1,
            mission_terminal_reason=None,
            outcome=None,
            mission_ref={
                "mission_id": "inst-x",
                "agent_id": "developer",
                "liveness": "processing",
            },
        ))
        payload = record.to_payload(work_id="job-x")
        assert "outcome" in payload
        assert payload["outcome"] is None
        assert "mission_ref" in payload

    def test_completed_payload_emits_outcome_and_mission_ref(self) -> None:
        """The completed-event payload includes both keys (terminal
        surface — the most likely place the renderer reports
        completion)."""
        record = _ResolvedWork.from_work_record(MagicMock(
            work_id="job-y",
            status="completed",
            instance_id="inst-y",
            result_summary="done",
            error=None,
            job_type="task",
            mission_liveness=None,  # task row: mission_liveness is None
            mission_id="inst-y",
            mission_epoch=1,
            mission_terminal_reason="completed",
            outcome=None,
            mission_ref={
                "mission_id": "inst-y",
                "agent_id": "developer",
                "liveness": "completed",
            },
        ))
        payload = record.to_completed_payload(work_id="job-y")
        # ``outcome`` is ALWAYS ``None`` on transport — even on a
        # completed surface (the value is reserved for mission
        # payloads).
        assert "outcome" in payload
        assert payload["outcome"] is None
        # ``mission_ref`` is present with the terminal ``liveness``.
        assert payload["mission_ref"] == {
            "mission_id": "inst-y",
            "agent_id": "developer",
            "liveness": "completed",
        }


# ─── Cross-cut: the four Fix-C surfaces agree on the keys ───────────────


class TestFourSurfacesAgree:
    """Surface uniformity — the §8.2 contract."""

    def test_mission_ref_to_dict_shape_matches_draft(self) -> None:
        """``mission_ref_to_dict`` returns exactly the three keys
        the contract draft §3.3 names.

        ``{"mission_id": ..., "agent_id": ..., "liveness": ...}`` —
        no extras, no missing keys.
        """
        ref = mission_ref_to_dict(
            mission_id="m", agent_id="a", liveness="processing"
        )
        assert set(ref.keys()) == {"mission_id", "agent_id", "liveness"}

    def test_work_record_helper_derives_terminal_task_liveness(self) -> None:
        """The WorkRecord-side helper produces the same shape on task
        rows (where ``mission_liveness`` is None and ``status`` is
        the canonical mission vocabulary)."""
        ref = _derive_m2_mission_ref_on_work(
            work_record_status="completed",
            job_type="task",
            mission_liveness=None,
            mission_id="inst-1",
            agent_id="developer",
        )
        assert ref == {
            "mission_id": "inst-1",
            "agent_id": "developer",
            "liveness": "completed",
        }

    def test_work_record_helper_derives_mirror_liveness(self) -> None:
        """The WorkRecord-side helper prefers ``mission_liveness``
        over ``status`` for mirror rows (the canonical mission
        vocabulary lives there)."""
        ref = _derive_m2_mission_ref_on_work(
            work_record_status="completed",  # receipt settled
            job_type="message",
            mission_liveness="processing",  # mission still running
            mission_id="inst-1",
            agent_id="developer",
        )
        assert ref["liveness"] == "processing"
