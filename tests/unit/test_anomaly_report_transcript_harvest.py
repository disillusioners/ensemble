"""The nest-anomaly TRANSCRIPT HARVEST — the bounded read + the selection.

Covers the AM-8a / C5 / D1 / E2 contract:

* the harvest read is BOUNDED AT THE READ SEAM — the scan window IS the
  read cap (a post-read trim would be a RED: the O(N^2) walk family the
  v0.27.15 incident names);
* the excerpt SELECTION predicate is the house ASSIGNMENT form — an
  assignment followed by a SEPARATE comparison:
  ``msg_type = getattr(msg, "type", None) or getattr(msg, "role", None)``
  THEN ``msg_type == "tool"``. The one-expression rendering parses as
  ``A or (B == "tool")``, so the ``.type`` STRING short-circuits the
  ``or`` and the agent's own prose is laundered into the excerpt;
* the ERROR-BEARING half of the predicate — the content must carry a
  house typed-error shape (``ERROR:`` / the ``REFUSED —`` envelope);
* the C5 numeric bounds — ≤5 excerpts, ≤500 chars each, ≤2000 total;
* ``minor`` severity SKIPS the transcript read entirely (no harvest, no
  column, no cost).

Mutation legs (phase6 §(b)):

* **M-1g** — remove the read cap → RED.
* **M-1h** — POSITIVE SELECTION: plant an error-bearing tool message ⇒
  it MUST land; mutation: invert the predicate ⇒ RED.
* **M-1i** — NEGATIVE SELECTION: a HumanMessage AND an AIMessage
  carrying ``ERROR:`` in prose MUST NOT land; mutation: drop the
  tool-message test ⇒ RED (and M-1h stays GREEN under that mutation).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.config import NestReportConfig
from daemon.repositories.anomaly.models import AnomalyCluster, AnomalyReport
from daemon.services import anomaly_service
from daemon.services.anomaly_service import (
    MAX_TRANSCRIPT_WINDOW,
    AnomalyService,
    select_error_excerpt,
)

ERROR_TOOL_TEXT = "ERROR: the write failed — Error: nest_report REFUSED — reason=x: y"


class _RecordingList(list):
    """A message list that RECORDS every slice the harvest takes.

    The read-cap pin needs to see the slice itself — a post-read trim
    would slice too, but with a different bound.
    """

    def __init__(self, items) -> None:
        super().__init__(items)
        self.slices: list[object] = []

    def __getitem__(self, key):
        self.slices.append(key)
        return super().__getitem__(key)


class _FakeRawSaver:
    def __init__(self, messages) -> None:
        self._messages = messages
        self.calls: list[dict] = []

    async def aget(self, config: dict) -> dict:
        self.calls.append(config)
        return {"channel_values": {"messages": self._messages}}


class _FakeCheckpointer:
    def __init__(self, messages) -> None:
        self.raw_saver = _FakeRawSaver(messages)


class _FakeManager:
    def __init__(self, messages) -> None:
        self.checkpointer = _FakeCheckpointer(messages)
        self.metrics_query_provider = None
        self._instance_repository = None
        self._task_repo = None
        self._job_queue_service = None
        self._mission_record_service = None
        self._wedge_capture_repository = None


async def _passthrough(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def _service(messages) -> AnomalyService:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(
        engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
    )
    return AnomalyService(
        engine,
        config=NestReportConfig(),
        manager=_FakeManager(messages),
        offload=_passthrough,
    )


def _tool_message(content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="call-1")


def _selection_source() -> str:
    return Path(anomaly_service.__file__).read_text(encoding="utf-8")


def _selection_function() -> ast.FunctionDef:
    tree = ast.parse(_selection_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "select_error_excerpt":
            return node
    raise AssertionError("select_error_excerpt was not found in the service")


@pytest.fixture(autouse=True)
def _scrubber_present(monkeypatch):
    """A value scrubber is present.

    Without it the AM-11 split posture withholds the excerpt (the
    fail-CLOSED leg) — which would make the SELECTION pins VACUOUS:
    an excerpt that is never persisted cannot prove that the wrong
    message was selected.
    """
    monkeypatch.setattr(
        anomaly_service, "get_process_scrubber", lambda: object(), raising=True
    )


class TestTheReadCap:
    """M-1g — the read cap lives AT THE READ SEAM."""

    async def test_the_transcript_is_sliced_at_the_read_seam(self) -> None:
        messages = _RecordingList(
            [HumanMessage(content="hello") for _ in range(40)]
        )
        service = _service(messages)
        await service.harvest(instance_id="inst-1", severity="major")
        bounded = [
            key
            for key in messages.slices
            if isinstance(key, slice) and key.start is not None and key.start < 0
        ]
        assert bounded, (
            "the harvest must bound the transcript with a negative-start "
            f"slice at the READ seam; recorded slices: {messages.slices!r}"
        )
        for key in bounded:
            assert -MAX_TRANSCRIPT_WINDOW <= key.start, key

    async def test_error_bearing_messages_outside_the_window_are_not_selected(
        self,
    ) -> None:
        # 50 messages: the FIRST 30 are error-bearing tool messages, the
        # LAST 20 are benign. The scan window is the last 20 — so the
        # excerpt must be EMPTY. Without the read cap the harvest would
        # walk the whole transcript and find the older errors.
        messages = [
            _tool_message(ERROR_TOOL_TEXT) for _ in range(30)
        ] + [HumanMessage(content=f"chatter {i}") for i in range(20)]
        service = _service(messages)
        harvest = await service.harvest(instance_id="inst-1", severity="major")
        assert harvest.excerpt is None
        assert harvest.excerpt_sha256 is None

    async def test_minor_severity_skips_the_transcript_read_entirely(self) -> None:
        messages = [_tool_message(ERROR_TOOL_TEXT)]
        service = _service(messages)
        harvest = await service.harvest(instance_id="inst-1", severity="minor")
        assert harvest.excerpt is None
        assert service._manager.checkpointer.raw_saver.calls == []


class TestTheSelectionPredicate:
    """E2 — the house ASSIGNMENT form, as TWO statements."""

    def test_the_predicate_assigns_msg_type_first(self) -> None:
        function = _selection_function()
        assignments = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "msg_type"
                for target in node.targets
            )
        ]
        assert assignments, (
            "the house form ASSIGNS msg_type (getattr type or getattr role) "
            "before it compares — the one-expression form is forbidden"
        )
        value = assignments[0].value
        assert isinstance(value, ast.BoolOp), value
        assert isinstance(value.op, ast.Or), value
        assert len(value.values) == 2, value
        for operand in value.values:
            assert isinstance(operand, ast.Call), operand
            assert isinstance(operand.func, ast.Name), operand
            assert operand.func.id == "getattr", operand
            assert isinstance(operand.args[0], ast.Name), operand
            assert operand.args[0].id == "message", operand
            assert operand.args[1].value in {"type", "role"}, operand
            assert isinstance(operand.args[2], ast.Constant), operand
            assert operand.args[2].value is None, operand

    def test_the_predicate_compares_msg_type_in_a_separate_statement(self) -> None:
        function = _selection_function()
        comparisons = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "msg_type"
        ]
        assert comparisons, "the house form COMPARES msg_type separately"
        assert all(len(node.comparators) == 1 for node in comparisons)
        # F-2: the pin must discriminate on the OPERATOR too — the
        # M-1h `!=` inversion kept this pin GREEN because it only
        # asserted the comparator Constant, never ast.Eq.
        assert all(isinstance(node.ops[0], ast.Eq) for node in comparisons)
        assert all(
            isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == "tool"
            for node in comparisons
        )
        # The comparison must NOT be nested inside the assignment's
        # value — that is the one-expression form in disguise.
        assignments = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "msg_type"
                for target in node.targets
            )
        ]
        for comparison in comparisons:
            for nested in ast.walk(assignments[0].value):
                assert nested is not comparison

    def test_the_forbidden_one_expression_form_is_absent(self) -> None:
        function = _selection_function()
        for node in ast.walk(function):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    assert not (
                        isinstance(comparator, ast.BoolOp)
                        and isinstance(comparator.op, ast.Or)
                    ), (
                        "the one-expression form "
                        "(getattr type or getattr role == 'tool') is FORBIDDEN"
                    )


class TestTheSelectionBehaviour:
    """M-1h (positive) + M-1i (negative)."""

    def test_an_error_bearing_tool_message_is_selected(self) -> None:
        parts = select_error_excerpt(
            [_tool_message(ERROR_TOOL_TEXT)],
            max_messages=5,
            max_chars_per_message=500,
        )
        assert len(parts) == 1
        assert ERROR_TOOL_TEXT in parts[0]

    def test_a_human_message_carrying_error_in_prose_is_not_selected(self) -> None:
        parts = select_error_excerpt(
            [HumanMessage(content="ERROR: please do the thing")],
            max_messages=5,
            max_chars_per_message=500,
        )
        assert parts == []

    def test_an_ai_message_carrying_error_in_prose_is_not_selected(self) -> None:
        parts = select_error_excerpt(
            [AIMessage(content="ERROR: I could not do the thing")],
            max_messages=5,
            max_chars_per_message=500,
        )
        assert parts == []

    def test_a_tool_message_without_the_error_shape_is_not_selected(self) -> None:
        parts = select_error_excerpt(
            [_tool_message("the file was written")],
            max_messages=5,
            max_chars_per_message=500,
        )
        assert parts == []

    def test_only_the_last_five_error_bearing_messages_are_kept(self) -> None:
        messages = [_tool_message(f"ERROR: failure {i}") for i in range(8)]
        parts = select_error_excerpt(
            messages,
            max_messages=5,
            max_chars_per_message=500,
        )
        assert len(parts) == 5
        assert parts[0].endswith("failure 3")
        assert parts[-1].endswith("failure 7")

    def test_the_per_message_and_total_caps_are_applied(self) -> None:
        long_text = "ERROR: " + ("x" * 2000)
        parts = select_error_excerpt(
            [_tool_message(long_text)],
            max_messages=5,
            max_chars_per_message=500,
        )
        assert len(parts) == 1
        assert len(parts[0]) == 500

    def test_the_selection_returns_the_parts_oldest_first(self) -> None:
        messages = [
            _tool_message("ERROR: first"),
            _tool_message("ERROR: second"),
        ]
        parts = select_error_excerpt(
            messages,
            max_messages=5,
            max_chars_per_message=500,
        )
        assert parts == ["ERROR: first", "ERROR: second"]


class TestThePersistedExcerpt:
    async def test_the_selected_message_reaches_the_persisted_excerpt(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            anomaly_service, "get_process_scrubber", lambda: object(), raising=True
        )
        engine = create_engine(
            f"sqlite:///{tmp_path / 'anomaly_harvest.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(
            engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
        )
        service = AnomalyService(
            engine,
            config=NestReportConfig(),
            manager=_FakeManager([_tool_message(ERROR_TOOL_TEXT)]),
            offload=_passthrough,
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        from sqlmodel import Session

        with Session(engine) as session:
            row = session.get(AnomalyReport, result.report_id)
            assert row is not None
            assert row.excerpt is not None
            assert ERROR_TOOL_TEXT in row.excerpt
            assert row.excerpt_length == len(row.excerpt)

    async def test_a_human_message_in_the_transcript_never_reaches_the_column(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            anomaly_service, "get_process_scrubber", lambda: object(), raising=True
        )
        engine = create_engine(
            f"sqlite:///{tmp_path / 'anomaly_harvest_negative.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(
            engine, tables=[AnomalyReport.__table__, AnomalyCluster.__table__]
        )
        service = AnomalyService(
            engine,
            config=NestReportConfig(),
            manager=_FakeManager(
                [
                    HumanMessage(content="ERROR: agent prose that must not be filed"),
                    AIMessage(content="ERROR: model prose that must not be filed"),
                ]
            ),
            offload=_passthrough,
        )
        result = await service.accept_report(
            agent_id="worker",
            instance_id="inst-1",
            category="tool-failure",
            description="boom",
            severity="major",
        )
        from sqlmodel import Session

        with Session(engine) as session:
            row = session.get(AnomalyReport, result.report_id)
            assert row is not None
            assert row.excerpt is None
            assert row.excerpt_sha256 is None


class TestTheBootEpoch:
    """AC7 — the boot epoch is NAIVE-UTC, and the wedge subsystem's TWO
    shapes canonicalize to the same value before any compare."""

    async def test_the_boot_epoch_is_stored_naive_utc(self, monkeypatch) -> None:
        from datetime import datetime

        from daemon.services import boot_epoch as boot_epoch_module

        monkeypatch.setattr(
            boot_epoch_module,
            "get_boot_epoch",
            lambda: datetime(2026, 11, 5, 2, 0, 0, 123456),
            raising=True,
        )
        service = _service([])
        harvested = await service.harvest(instance_id="inst-1", severity="major")
        assert harvested.boot_epoch == "2026-11-05T02:00:00.123456"
        assert "+" not in harvested.boot_epoch
        assert not harvested.boot_epoch.endswith("Z")

    def test_the_wedge_boot_epoch_canonicalization_handles_both_shapes(self) -> None:
        from daemon.services.anomaly_service import canonical_boot_epoch

        watchdog = "2026-11-05T02:00:00.123456"  # the naive writer
        boot_record = "2026-11-05T02:00:00.123456Z"  # the ``+ "Z"`` writer
        assert canonical_boot_epoch(watchdog) == canonical_boot_epoch(boot_record)
        assert canonical_boot_epoch(watchdog) == "2026-11-05T02:00:00.123456"
        assert canonical_boot_epoch(None) is None
        assert canonical_boot_epoch("not-a-timestamp") is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
