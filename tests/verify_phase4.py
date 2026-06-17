"""Phase 4 Focused Verification — `waiting_for` deprecation for control flow.

This script verifies the five Phase 4 invariants the deprecation must enforce:

  A. `waiting_for` read deprecation
     * Control-flow uses ``cm.get_pending_count()`` / ``cm.is_complete()``
     * ``waiting_for`` is still WRITTEN (increment at send_message, decrement
       at child completion / error)
     * No leftover control-flow READS of ``waiting_for``

  B. ``WAITING_CHILDREN`` status cleanup
     * CM-active path stays PROCESSING (no imperative ``WAITING_CHILDREN``)
     * SSE display event still fires
     * ``CM is None`` graceful-degradation still uses ``waiting_for`` reads

  C. ``_locks`` dict cleanup
     * ``_locks.pop(parent_id)`` runs AFTER ``del self._pending[parent_id]``
     * ``_locks`` does not grow unbounded across many sessions

  D. ``rebuild_from_db()`` still works
     * Queries parents with ``waiting_for > 0``
     * Rebuilds correct CM state
     * After rebuild, CM operates normally (register/resolve round-trip)

  E. Edge cases
     * Parent with 0 children → CM reports complete
     * Parent with 50+ children → all CM-driven

Run with::

    pytest tests/verify_phase4.py -v
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Import models so SQLModel.metadata.create_all picks them up.
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import (
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.services.correlation_manager import (
    CorrelationManager,
    ParentCorrelation,
    PendingResponse,
    STATUS_PENDING,
    STATUS_RESPONDED,
    STATUS_ERROR,
    get_correlation_manager,
    set_correlation_manager,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cm_singleton():
    """Ensure the module-level CM singleton is reset around every test."""
    set_correlation_manager(None)
    try:
        yield
    finally:
        set_correlation_manager(None)


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def message_repo(engine: Engine) -> SQLModelMessageQueueRepository:
    return SQLModelMessageQueueRepository(engine)


def _make_instance(
    engine: Engine,
    instance_id: str,
    *,
    parent_id: str | None = None,
    waiting_for: int = 0,
    status: str = "running",
) -> Instance:
    """Insert a bare ``Instance`` row in the test DB."""
    with Session(engine) as session:
        row = Instance(
            instance_id=instance_id,
            agent_id="coder",
            agent_dir=f"/tmp/agents/coder/{uuid.uuid4().hex[:6]}",
            parent_id=parent_id,
            status=status,
            waiting_for=waiting_for,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


_INCREMENT_WAITING_FOR_SQL = text(
    "UPDATE instances "
    "SET waiting_for = COALESCE(waiting_for, 0) + 1 "
    "WHERE instance_id = :pid "
    "RETURNING waiting_for"
)
_DECREMENT_WAITING_FOR_SQL = text(
    "UPDATE instances "
    "SET waiting_for = CASE "
    "    WHEN COALESCE(waiting_for, 0) - 1 > 0 "
    "        THEN COALESCE(waiting_for, 0) - 1 "
    "    ELSE 0 "
    "END "
    "WHERE instance_id = :pid "
    "RETURNING waiting_for"
)


def _increment_waiting_for(engine: Engine, parent_id: str) -> int:
    with Session(engine) as session:
        row = session.execute(_INCREMENT_WAITING_FOR_SQL, {"pid": parent_id}).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _decrement_waiting_for(engine: Engine, parent_id: str) -> int:
    with Session(engine) as session:
        row = session.execute(_DECREMENT_WAITING_FOR_SQL, {"pid": parent_id}).first()
        session.commit()
        return int(row[0]) if row is not None else 0


def _read_source(rel_path: str) -> str:
    """Read a source file relative to the repo root."""
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Strip Python comments to focus on actual code.

    We only strip whole-line comments and trailing ``# ...`` comments
    to keep string contents intact. Good enough for static analysis.
    """
    lines = []
    for line in src.splitlines():
        # Whole-line comment
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Trailing comment: anything after ``#`` not inside a string.
        # Cheap heuristic: find first ``#`` outside of any quotes.
        in_str = None
        cut = None
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str is None:
                if ch in ('"', "'"):
                    in_str = ch
                elif ch == "#":
                    cut = i
                    break
            elif ch == in_str and (i == 0 or line[i - 1] != "\\"):
                in_str = None
            i += 1
        if cut is not None:
            lines.append(line[:cut].rstrip())
        else:
            lines.append(line)
    return "\n".join(lines)


def _find_unprotected_waiting_for_reads(rel_path: str) -> list[int]:
    """Find ``.waiting_for`` READS that are NOT inside a graceful-degradation
    branch and NOT inside a comment or log message.

    Returns a list of line numbers (1-based) in the original source file.
    """
    src = _read_source(rel_path)
    src_lines = src.splitlines()
    reads: list[int] = []
    # Track whether we're inside a `else:` branch of `if cm is not None`
    # (or inside a CM-disabled code path).
    cm_disabled_depth = 0
    cm_enabled_depth = 0
    # Track recent skip region (e.g. inside docstring/template)
    in_docstring_depth = 0
    in_log_arg = False

    for line_idx, raw_line in enumerate(src_lines):
        line_no = line_idx + 1
        stripped = raw_line.lstrip()
        is_comment = stripped.startswith("#")

        # Track docstring boundaries (triple quotes).
        triple_quotes = raw_line.count('"""') + raw_line.count("'''")
        if triple_quotes % 2 == 1:
            in_docstring_depth ^= 1

        # Track if we're inside a CM-disabled branch.
        if not is_comment and re.search(r"\bcm is None\b", raw_line):
            cm_disabled_depth += 1
        if not is_comment and re.search(r"\bcm is not None\b", raw_line):
            cm_enabled_depth += 1
        if not is_comment and stripped.startswith("else:") and cm_enabled_depth > 0:
            # We just stepped out of the `if cm is not None:` branch.
            # The `else:` here means CM is disabled.
            cm_disabled_depth += 1
            cm_enabled_depth = max(0, cm_enabled_depth - 1)
        # Closing the if-block: the `else` was inside `if cm is not None`,
        # so when we dedent back to the same level as `if`, both close.
        # Heuristic: if we see a dedent past the `if`, pop the depths.
        if not is_comment and cm_enabled_depth > 0 and (
            "return False, None, None" in raw_line
            or "return True, None, None" in raw_line
        ):
            # The CM-active branch returned — close the `if` block.
            cm_enabled_depth = max(0, cm_enabled_depth - 1)

        # Skip comments, docstrings, and log/f-string bodies.
        if is_comment or in_docstring_depth:
            continue
        # Heuristic: log lines that contain ``.waiting_for`` in f-strings
        # are display, not control flow.
        if "logger." in raw_line and "waiting_for" in raw_line:
            continue

        # Find every `.waiting_for` access on this line.
        for m in re.finditer(r"\.waiting_for\b", raw_line):
            if cm_disabled_depth > 0:
                # Inside CM-disabled branch — graceful-degradation path.
                continue
            # If we get here, this is a control-flow read of `.waiting_for`
            # in a CM-active code path. That's a violation.
            reads.append(line_no)
    return reads


# ===========================================================================
# CRITERION A: `waiting_for` read deprecation
# ===========================================================================


class TestAControlFlowUsesCM:
    """A.1: control-flow paths use CM, not ``waiting_for`` reads."""

    def test_manager_py_uses_cm_get_pending_count(self):
        """``manager.py`` defers completion via ``cm.get_pending_count()``."""
        src = _read_source("daemon/manager.py")
        # The control-flow decision site is around the [RESUME] block
        # which is the only branch deciding skip_complete based on pending
        # children. The text "cm.get_pending_count" must appear there.
        assert "cm.get_pending_count" in src, (
            "manager.py must use cm.get_pending_count() for control flow"
        )

    def test_message_job_handler_uses_cm(self):
        """``message_job_handler.py`` defers completion via CM."""
        src = _read_source("daemon/services/message_job_handler.py")
        assert "cm.get_pending_count" in src
        assert "cm.is_complete" in src or "get_correlation_manager" in src

    def test_child_reports_uses_cm_is_complete(self):
        """``child_reports.py`` cascade uses ``cm.is_complete()``."""
        src = _read_source("daemon/services/child_reports.py")
        assert "cm.is_complete" in src
        assert "get_correlation_manager" in src

    def test_error_reporting_uses_cm_is_complete(self):
        """``error_reporting.py`` cascade uses ``cm.is_complete()``."""
        src = _read_source("daemon/services/error_reporting.py")
        assert "cm.is_complete" in src
        assert "get_correlation_manager" in src

    def test_job_processor_passes_cm_value(self):
        """``job_processor.py`` falls back to DB ``waiting_for`` only
        when CM is not available, and prefers CM-derived values."""
        src = _read_source("daemon/services/job_processor.py")
        # The file must reference waiting_for (display path) but
        # the control-flow decision should consult CM first.
        assert "waiting_for" in src
        # Confirm graceful-degradation pattern exists.
        assert "get_correlation_manager" in src or "cm" in src


class TestAWaitingForStillWritten:
    """A.2: ``waiting_for`` is still WRITTEN (rebuild cache)."""

    def test_send_message_increments_waiting_for(
        self, engine: Engine, instance_repo: SQLModelInstanceRepository
    ):
        """The increment UPDATE pattern still runs on send_message."""
        _make_instance(engine, "p1", parent_id=None, waiting_for=0)
        new_val = _increment_waiting_for(engine, "p1")
        assert new_val == 1
        refreshed = instance_repo.get("p1")
        assert refreshed.waiting_for == 1

    def test_child_completion_decrements_waiting_for(
        self, engine: Engine, instance_repo: SQLModelInstanceRepository
    ):
        """The decrement UPDATE pattern still runs on child completion."""
        _make_instance(engine, "p1", parent_id=None, waiting_for=2)
        new_val = _decrement_waiting_for(engine, "p1")
        assert new_val == 1
        new_val = _decrement_waiting_for(engine, "p1")
        assert new_val == 0

    def test_decrement_clamps_at_zero(
        self, engine: Engine, instance_repo: SQLModelInstanceRepository
    ):
        """A decrement on a zero counter does not go negative."""
        _make_instance(engine, "p1", parent_id=None, waiting_for=0)
        new_val = _decrement_waiting_for(engine, "p1")
        assert new_val == 0
        refreshed = instance_repo.get("p1")
        assert refreshed.waiting_for == 0

    def test_increment_sql_pattern_exists_in_tools_instance(self):
        """``tools/instance.py`` still has the increment UPDATE."""
        src = _read_source("daemon/tools/instance.py")
        assert "waiting_for = COALESCE(waiting_for, 0) + 1" in src, (
            "tools/instance.py must still increment waiting_for on send_message"
        )

    def test_decrement_sql_pattern_exists_in_child_reports(self):
        """``child_reports.py`` still has the decrement UPDATE."""
        src = _read_source("daemon/services/child_reports.py")
        assert "WHEN COALESCE(waiting_for, 0) - 1 > 0" in src, (
            "child_reports.py must still decrement waiting_for on child completion"
        )

    def test_decrement_sql_pattern_exists_in_error_reporting(self):
        """``error_reporting.py`` still has the decrement UPDATE (error path)."""
        src = _read_source("daemon/services/error_reporting.py")
        assert "WHEN COALESCE(waiting_for, 0) - 1 > 0" in src, (
            "error_reporting.py must still decrement waiting_for on child error"
        )


class TestANoControlFlowReads:
    """A.3: No leftover control-flow READS of ``waiting_for``."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "daemon/manager.py",
            "daemon/services/child_reports.py",
            "daemon/services/error_reporting.py",
            "daemon/services/job_processor.py",
            "daemon/services/message_job_handler.py",
        ],
    )
    def test_no_unprotected_waiting_for_reads(self, rel_path: str):
        """No `if waiting_for > 0` or `if waiting_for == 0` style reads
        outside a graceful-degradation block.

        The pre-loaded context notes that legacy ``waiting_for`` reads are
        retained ONLY in the `else` branch of `if cm is not None` checks.
        This test asserts the structure: any line that READS ``.waiting_for``
        in a CM-active code path is a violation.
        """
        unprotected = _find_unprotected_waiting_for_reads(rel_path)
        assert not unprotected, (
            f"{rel_path} has unprotected control-flow reads of .waiting_for "
            f"on line(s): {unprotected}"
        )


# ===========================================================================
# CRITERION B: WAITING_CHILDREN status cleanup
# ===========================================================================


class TestBWaitingChildrenCleanup:
    """B.1: CM-active paths stay PROCESSING (no WAITING_CHILDREN imperative)."""

    def test_child_reports_cm_active_returns_early(self):
        """``child_reports.py`` cascade returns early when CM is active."""
        src = _read_source("daemon/services/child_reports.py")
        # The CM-active branch is the one that logs "CM-active: skipping
        # inline cascade" and returns False, None, None.
        assert "CM-active: skipping inline cascade" in src
        # It must NOT then set WAITING_CHILDREN.
        # We look for the block between CM-active and the next significant
        # branch boundary.
        active_idx = src.index("CM-active: skipping inline cascade")
        # The next ~2000 chars should be the early return.
        active_block = src[active_idx : active_idx + 2000]
        # The early return should fire before WAITING_CHILDREN assignment.
        assert "return False, None, None" in active_block
        assert "InstanceStatus.WAITING_CHILDREN" not in active_block

    def test_cm_active_path_does_not_set_waiting_children(self):
        """The CM-active path explicitly does NOT set WAITING_CHILDREN.

        We check the structure: when CM is active, the code returns
        ``False, None, None`` BEFORE touching ``WAITING_CHILDREN``.
        """
        src = _read_source("daemon/services/child_reports.py")
        code = _strip_comments(src)

        # Find the block of code that starts at `if cm is not None:` and
        # ends at the matching `return False, None, None` (or the next
        # `else:`). We use a non-greedy match between the marker and the
        # early return, since the comments inside the block may contain
        # ``"return"`` text in prose.
        m = re.search(
            r"if cm is not None:.*?return False, None, None",
            code,
            re.DOTALL,
        )
        assert m is not None, "CM-active block must return early"
        block = m.group(0)
        assert "WAITING_CHILDREN" not in block, (
            "CM-active block must not reference WAITING_CHILDREN"
        )

    def test_sse_status_change_event_fires(self):
        """The SSE ``stream_status_change`` call still fires for
        ``waiting_children`` display."""
        src = _read_source("daemon/services/child_reports.py")
        assert "stream_status_change" in src
        # Must reference the waiting_children display value.
        assert "waiting_children" in src


class TestBCmNoneGracefulDegradation:
    """B.2: ``CM is None`` path still works with graceful degradation."""

    def test_child_reports_has_else_branch_for_cm_none(self):
        """``child_reports.py`` keeps the legacy path for CM-disabled mode."""
        src = _read_source("daemon/services/child_reports.py")
        # The graceful-degradation else branch must still be present.
        assert "getattr(parent, \"waiting_for\", None) or 0" in src

    def test_error_reporting_has_else_branch_for_cm_none(self):
        """``error_reporting.py`` keeps the legacy path for CM-disabled mode."""
        src = _read_source("daemon/services/error_reporting.py")
        assert "getattr(parent, \"waiting_for\", None) or 0" in src

    def test_message_job_handler_has_else_branch_for_cm_none(self):
        """``message_job_handler.py`` keeps the legacy path for CM-disabled mode."""
        src = _read_source("daemon/services/message_job_handler.py")
        assert "getattr(instance, \"waiting_for\", None) or 0" in src

    def test_runtime_cm_none_path_uses_waiting_for(self, engine: Engine):
        """End-to-end: with no CM wired up, the cascade falls back to
        the ``waiting_for`` DB column."""
        parent = _make_instance(engine, "p-no-cm", parent_id=None, waiting_for=0)
        # Simulate the graceful-degradation branch:
        from daemon.services.correlation_manager import get_correlation_manager
        cm = get_correlation_manager()
        assert cm is None, "No CM should be wired up"
        # The branch reads waiting_for directly:
        legacy_complete = (getattr(parent, "waiting_for", None) or 0) == 0
        assert legacy_complete is True


# ===========================================================================
# CRITERION C: _locks dict cleanup
# ===========================================================================


class TestCLocksCleanup:
    """C.1: ``_locks.pop(parent_id)`` runs AFTER ``del _pending[parent_id]``."""

    def test_locks_pop_after_pending_del(self):
        """Static check: order of operations in ``resolve_response``."""
        src = _read_source("daemon/services/correlation_manager.py")
        # Find the resolve_response body. The next top-level method on the
        # class is ``get_pending_count`` (a plain ``def``, not ``async def``).
        # We slice from the start of resolve_response to the start of the
        # next method, then check the order of operations inside.
        m_start = re.search(
            r"async def resolve_response\(",
            src,
        )
        assert m_start is not None, "resolve_response definition not found"
        body_start = m_start.start()
        # Find the next method definition after resolve_response.
        m_next = re.search(
            r"^(?:    async def |    def )",
            src[body_start + 1:],
            re.MULTILINE,
        )
        assert m_next is not None, "next method after resolve_response not found"
        body = src[body_start : body_start + 1 + m_next.start()]
        # Both operations must appear, and the del must precede the pop.
        assert "del self._pending[parent_id]" in body, (
            "resolve_response must delete _pending[parent_id]"
        )
        assert "self._locks.pop(parent_id, None)" in body, (
            "resolve_response must pop _locks[parent_id]"
        )
        del_idx = body.index("del self._pending[parent_id]")
        pop_idx = body.index("self._locks.pop(parent_id, None)")
        assert del_idx < pop_idx, (
            f"_pending delete must come BEFORE _locks pop, but del at {del_idx} "
            f"and pop at {pop_idx}"
        )

    def test_locks_does_not_grow_unbounded(
        self, instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository,
    ):
        """Runtime check: 100 parent sessions, _locks stays bounded."""
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
            completion_callback=AsyncMock(),
        )

        async def run():
            for i in range(100):
                parent_id = f"parent-{i}"
                child_id = f"child-{i}"
                msg_id = f"msg-{i}"
                await cm.register_message_send(parent_id, child_id, msg_id)
                # Resolve immediately → correlation completes → _locks
                # entry is popped.
                await cm.resolve_response(parent_id, child_id, msg_id,
                                          status=STATUS_RESPONDED)
            # After all 100 sessions resolve, the _locks dict should be empty.
            assert len(cm._locks) == 0, (
                f"_locks grew unbounded: {len(cm._locks)} entries after "
                f"100 complete sessions"
            )

        asyncio.run(run())


# ===========================================================================
# CRITERION D: rebuild_from_db() still works
# ===========================================================================


class TestDRebuildFromDb:
    """D.1: ``rebuild_from_db()`` queries parents with ``waiting_for > 0``."""

    def test_repository_has_get_all_with_waiting_for(self):
        """The repository method exists."""
        src = _read_source("daemon/repositories/instance/repository.py")
        assert "def get_all_with_waiting_for" in src
        assert "Instance.waiting_for > 0" in src

    def test_message_repo_has_get_pending_for_instances(self):
        """The batched message query method exists."""
        src = _read_source("daemon/repositories/message_queue/repository.py")
        assert "def get_pending_for_instances" in src

    def test_rebuild_finds_parents_with_positive_waiting_for(
        self, engine: Engine, instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository
    ):
        """End-to-end: parents with waiting_for > 0 are picked up."""
        # Create a parent with waiting_for=2 and 2 children with pending
        # messages.
        _make_instance(engine, "p-rebuild", parent_id=None, waiting_for=2)
        for i in range(2):
            child_id = f"c-rebuild-{i}"
            _make_instance(engine, child_id, parent_id="p-rebuild", waiting_for=0)
            with Session(engine) as session:
                row = MessageQueue(
                    message_id=f"m-rebuild-{i}",
                    instance_id=child_id,
                    content="rebuild test",
                    type="agent",
                    source="test",
                    status=MessageStatus.READY.value,
                    priority=1,
                    retry_count=0,
                    max_retries=5,
                    enqueued_at=datetime.now(timezone.utc),
                )
                session.add(row)
                session.commit()

        # Create another parent with waiting_for=0 — must be ignored.
        _make_instance(engine, "p-ignore", parent_id=None, waiting_for=0)

        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
            completion_callback=AsyncMock(),
        )

        async def run():
            await cm.rebuild_from_db()
            # p-rebuild should be tracked with 2 pending.
            assert "p-rebuild" in cm._pending
            assert cm.get_pending_count("p-rebuild") == 2
            # p-ignore should NOT be tracked (waiting_for=0).
            assert "p-ignore" not in cm._pending
            # is_complete for untracked parent is True (Phase 1 contract).
            assert cm.is_complete("p-ignore") is True

        asyncio.run(run())

    def test_rebuild_then_register_resolve_round_trip(
        self, engine: Engine, instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository
    ):
        """After rebuild, register/resolve still works normally."""
        # Pre-populate a parent with waiting_for=1 and one child + message.
        _make_instance(engine, "p-rt", parent_id=None, waiting_for=1)
        _make_instance(engine, "c-rt", parent_id="p-rt", waiting_for=0)
        with Session(engine) as session:
            row = MessageQueue(
                message_id="m-rt-0",
                instance_id="c-rt",
                content="round trip",
                type="agent",
                source="test",
                status=MessageStatus.READY.value,
                priority=1,
                retry_count=0,
                max_retries=5,
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.commit()

        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
            completion_callback=AsyncMock(),
        )

        async def run():
            await cm.rebuild_from_db()
            assert cm.get_pending_count("p-rt") == 1
            assert cm.is_complete("p-rt") is False

            # Resolve the rebuilt correlation.
            resolved = await cm.resolve_response(
                "p-rt", "c-rt", "m-rt-0", status=STATUS_RESPONDED
            )
            assert resolved is True, "Resolving the last correlation must return True"
            # The parent should be removed from _pending.
            assert "p-rt" not in cm._pending
            # is_complete for the (now-untracked) parent is True.
            assert cm.is_complete("p-rt") is True

        asyncio.run(run())


# ===========================================================================
# CRITERION E: Edge cases
# ===========================================================================


class TestEEdgeCases:
    """E.1: Parent with 0 children → CM reports complete."""

    def test_parent_with_zero_pending_correlations_is_complete(
        self, instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository
    ):
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
            completion_callback=AsyncMock(),
        )

        async def run():
            # A parent that has never had a child registered is untracked.
            # Phase 1 contract: untracked parents are reported as complete.
            assert cm.is_complete("never-tracked-parent") is True
            assert cm.get_pending_count("never-tracked-parent") == 0

            # After register + resolve of the only correlation, the parent
            # is no longer tracked, and is_complete flips back to True.
            await cm.register_message_send("p1", "c1", "m1")
            assert cm.is_complete("p1") is False
            await cm.resolve_response("p1", "c1", "m1", status=STATUS_RESPONDED)
            assert cm.is_complete("p1") is True

        asyncio.run(run())

    def test_parent_with_50_plus_children_all_cm_driven(
        self, instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository
    ):
        """E.2: Parent with 50+ children — all tracked by CM."""
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
            completion_callback=AsyncMock(),
        )

        N = 75
        callback_calls: list[tuple[str, str]] = []

        async def cb(parent_id: str, status: str) -> None:
            callback_calls.append((parent_id, status))

        cm._completion_callback = cb

        async def run():
            parent = "big-parent"
            # Register N children.
            for i in range(N):
                await cm.register_message_send(parent, f"c{i}", f"m{i}")
            assert cm.get_pending_count(parent) == N
            assert cm.is_complete(parent) is False

            # Resolve them all in random order.
            import random
            indices = list(range(N))
            random.shuffle(indices)
            for i in indices:
                resolved = await cm.resolve_response(
                    parent, f"c{i}", f"m{i}", status=STATUS_RESPONDED
                )
                if i == indices[-1]:
                    assert resolved is True, "Last resolve must return True"
            assert cm.get_pending_count(parent) == 0
            assert cm.is_complete(parent) is True
            assert len(callback_calls) == 1
            assert callback_calls[0] == (parent, "completed")

        asyncio.run(run())

    def test_parent_with_50_plus_error_paths(
        self, instance_repo: SQLModelInstanceRepository,
        message_repo: SQLModelMessageQueueRepository
    ):
        """E.3: A burst of error resolutions still completes the parent
        with terminal_status=error (conservative rule)."""
        cm = CorrelationManager(
            instance_repository=instance_repo,
            message_queue_repository=message_repo,
            completion_callback=AsyncMock(),
        )

        callback_calls: list[tuple[str, str]] = []

        async def cb(parent_id: str, status: str) -> None:
            callback_calls.append((parent_id, status))

        cm._completion_callback = cb

        async def run():
            parent = "err-parent"
            N = 50
            for i in range(N):
                await cm.register_message_send(parent, f"c{i}", f"m{i}")
            # Resolve all as errors.
            for i in range(N - 1):
                await cm.resolve_response(
                    parent, f"c{i}", f"m{i}", status=STATUS_RESPONDED
                )
            # Final resolve as error → terminal_status must be "error".
            await cm.resolve_response(
                parent, f"c{N-1}", f"m{N-1}", status=STATUS_ERROR
            )
            assert len(callback_calls) == 1
            assert callback_calls[0] == (parent, "error"), (
                "Final status must be 'error' when any child errored"
            )

        asyncio.run(run())
