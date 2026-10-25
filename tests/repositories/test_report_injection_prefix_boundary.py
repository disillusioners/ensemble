"""D3 fusion pin — the report-source prefix is an EXACT boundary.

The incident that motivated the D3 prefix lane produced a PHANTOM
instance id. The hypothesis was that the ``LIKE`` guards in
``daemon/repositories/report_injection/repository.py`` alias a sibling
child whose id PREFIXES another child's id. The hypothesis is INVERTED:
the guard is ``source LIKE 'internal_report:' || <child_id> || ':%'`` —
the ``:`` terminator anchors the boundary, so a sibling source
(``internal_report:{child_id}2:msg``) does NOT match
``internal_report:{child_id}:%``. Two well-formed 36-char uuids cannot
alias (equal length + colon anchor) — the repository documents this at
``daemon/repositories/report_injection/repository.py:2098-2108``.

This file PINS that behaviour so a future edit to the prefix shape
cannot silently re-open the aliasing hole:

* sibling-prefix source → NOT delivery evidence for the child;
* the child's OWN source → IS delivery evidence for the child.

Engine: the in-memory SQLite ``engine`` fixture from
``tests/repositories/conftest.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)

_PARENT_ID = "parent-1"
# The child whose report has NOT been delivered …
_CHILD_ID = "child-aaa"
# … and a SIBLING child whose id EXTENDS the first child's id. A
# delivery-evidence LIKE that matched on the bare id prefix would treat
# the sibling's report row as the first child's evidence.
_SIBLING_CHILD_ID = "child-aaa2"


@pytest.fixture
def repo(engine) -> ReportInjectionRepository:
    return ReportInjectionRepository(engine=engine)


def _seed_instance(engine, *, instance_id: str, parent_id: str | None, status: str) -> str:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent-worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_parent_report_row(engine, *, parent_id: str, source: str) -> str:
    """A PARENT-side completion-report row with an EXPLICIT source."""
    message_id = f"rmsg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=parent_id,
                content="report body",
                source=source,
                type=MessageType.COMPLETION_REPORT.value,
                status=MessageStatus.COMPLETED.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return message_id


def _seed_tree(engine) -> None:
    _seed_instance(
        engine, instance_id=_PARENT_ID, parent_id=None, status=InstanceStatus.RUNNING.value
    )
    _seed_instance(
        engine,
        instance_id=_CHILD_ID,
        parent_id=_PARENT_ID,
        status=InstanceStatus.COMPLETED.value,
    )
    _seed_instance(
        engine,
        instance_id=_SIBLING_CHILD_ID,
        parent_id=_PARENT_ID,
        status=InstanceStatus.COMPLETED.value,
    )


class TestReportSourcePrefixBoundary:
    def test_sibling_prefix_source_is_not_delivery_evidence(self, engine, repo):
        """The child's OWN report is missing, and the sibling's report
        row (whose source extends the child's id) must NOT be counted
        as the child's delivery evidence."""
        _seed_tree(engine)
        _seed_parent_report_row(
            engine,
            parent_id=_PARENT_ID,
            source=f"internal_report:{_SIBLING_CHILD_ID}:anchor-1",
        )

        rows = repo.find_completed_children_without_delivery(parent_not_terminal=True)
        child_ids = {row["child_id"] for row in rows}

        # The child is STILL an outstanding obligation — the sibling's
        # row did not satisfy it.
        assert _CHILD_ID in child_ids
        # The sibling itself HAS delivery evidence → it is not a
        # candidate.
        assert _SIBLING_CHILD_ID not in child_ids

    def test_own_source_is_delivery_evidence(self, engine, repo):
        """Control: the child's own report row DOES satisfy the child's
        delivery evidence (the pin above is not vacuous)."""
        _seed_tree(engine)
        _seed_parent_report_row(
            engine,
            parent_id=_PARENT_ID,
            source=f"internal_report:{_CHILD_ID}:anchor-1",
        )

        rows = repo.find_completed_children_without_delivery(parent_not_terminal=True)
        child_ids = {row["child_id"] for row in rows}

        assert _CHILD_ID not in child_ids
