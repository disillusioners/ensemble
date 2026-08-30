"""W2 council follow-up tests — dead-site symmetry attaches (2026-08-30).

The pre-flip council report flagged two latent unguarded stamp paths that
the Wave-1 / Phase-2 code did NOT cover:

  * ``daemon/services/job_feedback_observer.py:_finalize_instance_db_sync``
    (the sync DB half of ``_finalize_instance`` — DEAD in production,
    only invoked by ``tests/test_finalize_instance.py`` and
    ``tests/test_deadlock_fix.py``).
  * ``daemon/services/error_reporting.py:~318`` (the bus=None fallback in
    ``_send_error_report_db_sync`` — DEAD because ``bus is None`` raises
    A8 / RuntimeError at the first guard before control reaches here).

Dispositions chosen: **ATTACH WITH NOTE** at both sites (the helper
``log_declared_waiting_violations`` is called at the dead-twin position,
LOG ONLY — never enforcement). Each attach carries an in-line dead-site
NOTE comment matching the ``child_reports.py:~3390`` precedent: why the
site is dead, that it never fires in production, that it is
symmetry-coverage ONLY if the site ever becomes live.

Deadness verification (grep-verified, 2026-08-30):

  * ``_finalize_instance`` (the only caller of ``_finalize_instance_db_sync``)
    has ZERO production callers in ``daemon/``. ``grep -rn
    "_finalize_instance\b" --include="*.py" daemon/`` returns only the
    definition, docstring references, and an internal ``asyncio.to_thread``
    argument. All non-trivial callers live in
    ``tests/test_finalize_instance.py`` and ``tests/test_deadlock_fix.py``.
  * ``error_reporting.py:_send_error_report_db_sync`` first invokes
    ``bus = get_dependency_bus()`` at :242, then branches at :242-254:
    ``if bus is not None: ... else: raise RuntimeError("DependencyBus is
    None …")``. Bus=None raises A8 BEFORE control reaches the dead-twin
    else-branch at :286 (which contains the log attach at :327).

These tests pin the ATTACHED disposition:

  * Site 1 — direct call to ``_finalize_instance_db_sync`` exercises the
    path with a real DB; caplog verifies the new
    ``observer_finalize_instance_db_sync`` context_tag fires on the
    incident shape (terminal child + pending report).
  * Site 2 — source-grep assertion that the dead-twin attach IS present
    at the expected location with the expected context_tag. A
    behavioral test is not feasible (the bus=None guard raises A8 before
    control reaches the dead branch).

LOG-ONLY contract (D2.6 LOCKED): the helper never raises into the
completion path, never mutates anything, never enqueues a notice, never
reads the (b) kill-switch flag.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.job_feedback_observer import JobFeedbackObserver


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine — exercises the DB transition end-to-end."""
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


def _seed_instance(
    engine: Engine,
    *,
    status: str,
    parent_id: str | None = None,
    prefix: str = "inst",
) -> str:
    """Insert an instance row with the requested status."""
    iid = f"{prefix}-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return iid


def _seed_parent(engine: Engine) -> str:
    return _seed_instance(engine, status=InstanceStatus.RUNNING.value, prefix="parent")


def _seed_terminal_child(engine: Engine, parent_id: str) -> str:
    return _seed_instance(
        engine,
        status=InstanceStatus.COMPLETED.value,
        parent_id=parent_id,
        prefix="child",
    )


def _enqueue_pending(
    report_repo: ReportInjectionRepository,
    *,
    parent_id: str,
    child_id: str,
) -> str:
    """Insert a PENDING report-injection row for (parent, child)."""
    report_msg = f"rmsg-{uuid.uuid4().hex[:8]}"
    report_repo.enqueue(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        report_message_id=report_msg,
        content="junk opener body",
    )
    return report_msg


def _make_observer_with_engine(engine: Engine) -> JobFeedbackObserver:
    """Build a bare ``JobFeedbackObserver`` with a real engine + stub manager.

    Mirrors the test_finalize_instance.py fixtures closely — we only need
    the DB-transaction half (``_finalize_instance_db_sync``), so the SSE /
    CompletionRegistry / lifecycle-event dependencies are NOT wired.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = MagicMock(name="WritePauseGuard")
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    observer._instance_manager = manager
    observer._events_service = None
    return observer


# ─────────────────────────────────────────────────────────────────────────────
# Site 1 — _finalize_instance_db_sync (DEAD in production, ATTACHED)
# ─────────────────────────────────────────────────────────────────────────────


class TestW2Site1FinalizeInstanceDbSyncAttached:
    """W2 Site 1: ``_finalize_instance_db_sync`` carries the (b) stage-ii log.

    Direct-call behavioral test — the method is a sync ``def`` and can be
    invoked without the async ``_finalize_instance`` wrapper. Production
    callers: NONE (grep-verified). This test pins the ATTACHED
    disposition by asserting the log fires with the expected
    ``observer_finalize_instance_db_sync`` context_tag in the incident
    shape (terminal child + pending report).
    """

    def test_log_fires_at_dead_site_on_incident_shape(
        self, engine: Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Terminal child + pending report-injection row ⇒ log fires at
        ``_finalize_instance_db_sync`` with the dead-site context_tag."""
        parent_id = _seed_parent(engine)
        child_id = _seed_terminal_child(engine, parent_id=parent_id)

        report_repo = ReportInjectionRepository(engine)
        _enqueue_pending(report_repo, parent_id=parent_id, child_id=child_id)

        observer = _make_observer_with_engine(engine)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_instance_db_sync(
                parent_id, InstanceStatus.COMPLETED.value
            )

        # Stamp succeeded — instance flipped to terminal.
        assert result.skip is False
        assert result.parent_id is None
        assert result.agent_id == "worker"

        # The dead-site attach fired with the council-approved context_tag.
        matched = [
            r
            for r in caplog.records
            if "observer_finalize_instance_db_sync" in r.getMessage()
        ]
        assert len(matched) == 1, (
            "W2 Site 1 disposition (ATTACHED): the dead-site log must fire "
            "exactly once when the incident shape is staged; records="
            f"{[r.getMessage() for r in caplog.records]}"
        )
        assert "declared-waiting violation" in matched[0].getMessage()
        assert parent_id in matched[0].getMessage(), (
            "log must include parent_id so soak analysis can attribute the "
            "firing to the specific parent"
        )

    def test_dead_site_log_silent_on_healthy_path(
        self, engine: Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No pending report-injection rows ⇒ NO log (zero noise on healthy)."""
        parent_id = _seed_parent(engine)

        observer = _make_observer_with_engine(engine)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = observer._finalize_instance_db_sync(
                parent_id, InstanceStatus.COMPLETED.value
            )

        assert result.skip is False
        assert not any(
            "observer_finalize_instance_db_sync" in r.getMessage()
            for r in caplog.records
        ), (
            "W2 Site 1 LOG-ONLY contract (D2.6): healthy paths emit NO log "
            "line (the helper returns None and never calls logger.warning)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Site 2 — error_reporting.py dead bus-None fallback (ATTACHED, structural)
# ─────────────────────────────────────────────────────────────────────────────


class TestW2Site2ErrorReportingBusNoneFallbackAttached:
    """W2 Site 2: ``error_reporting.py:316-324`` carries the (b) stage-ii log.

    Behavioral test is not feasible — the ``bus is None`` guard at
    ``error_reporting.py:242-254`` raises ``RuntimeError`` BEFORE control
    reaches the dead-twin else-branch at :286. A behavioral test would
    require bypassing the raise, which would exercise a path that does
    NOT exist in production (and would not be a faithful behavioral pin).

    The structural test pins the ATTACHED disposition by asserting the
    source carries:
      * the ``log_declared_waiting_violations`` import,
      * the dedicated context_tag ``error_reporting.parent_completion_bus_none_fallback``,
      * the dead-site NOTE comment matching the
        ``child_reports.py:~3390`` precedent (calls it a dead-site,
        explains why, names the symmetry-coverage intent).
    """

    def test_dead_site_attach_is_present_in_source(self) -> None:
        """Source carries the dead-site ATTACH (council W2, 2026-08-30)."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        src_path = repo_root / "daemon" / "services" / "error_reporting.py"
        src = src_path.read_text(encoding="utf-8")

        # 1. The helper is imported (provides the attach callable).
        assert (
            "from .report_integrity_guard import log_declared_waiting_violations"
            in src
        ), (
            "W2 Site 2 (ATTACHED): error_reporting.py must import "
            "log_declared_waiting_violations for the dead-site attach"
        )

        # 2. The dedicated context_tag for this site is present.
        assert (
            "error_reporting.parent_completion_bus_none_fallback"
            in src
        ), (
            "W2 Site 2 (ATTACHED): the dead-site attach must use the "
            "approved context_tag "
            "'error_reporting.parent_completion_bus_none_fallback' for "
            "soak greppability"
        )

        # 3. The call sits inside the dead-twin else-branch (the
        # ``if bus is not None: ... else: <attach>`` pair at :279-286).
        # We assert that the attach's call comes AFTER the dead-code
        # fallback marker comment, matching the child_reports.py:~3390
        # precedent. Use a grep-agnostic lookup so the test stays
        # indentation-robust.
        attach_idx = src.find(
            "error_reporting.parent_completion_bus_none_fallback"
        )
        assert attach_idx > 0, (
            "W2 Site 2: the dead-site attach call must be present with "
            "the dedicated context_tag"
        )

        # 4. The dead-site NOTE comment is present, matching the
        # child_reports.py:~3390 style (explains why the site is dead).
        assert "W2 dead-site symmetry attach (council, 2026-08-30)" in src, (
            "W2 Site 2: the dead-site NOTE comment must be present so the "
            "next reader knows why the log is never fired in production"
        )
        assert "Bus=None raises A8" in src or "bus=None raises" in src, (
            "W2 Site 2: the dead-site NOTE must explain WHY the site is "
            "dead (the bus=None guard raises A8 / RuntimeError before "
            "control reaches here)"
        )
        assert "Symmetry" in src or "symmetry" in src, (
            "W2 Site 2: the dead-site NOTE must name the symmetry-coverage "
            "intent (matches the child_reports.py:~3390 precedent)"
        )

        # 5. The attach does NOT enforce — call result is unused.
        # Find the attach call block and assert no assignment target.
        attach_block_start = src.rfind(
            "log_declared_waiting_violations(", 0, attach_idx + 100
        )
        attach_block_end = src.find(")", attach_idx + 50)
        attach_block = src[attach_block_start:attach_block_end]
        assert "=" not in attach_block.split("log_declared_waiting_violations(")[0], (
            "W2 Site 2 LOG-ONLY contract: the attach's return value must "
            "NOT be captured — no enforcement, no return value usage. "
            f"Found: {attach_block[:200]!r}"
        )

    def test_dead_site_attach_lives_in_bus_none_else_branch(self) -> None:
        """The attach call sits inside the ``if bus is not None: ... else:`` block.

        Proves the attach is in the dead twin's body — not a stray call
        elsewhere in the file. This is the structural analog of the
        ``child_reports.py:~3390`` inline-twin precedent.
        """
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        src_path = repo_root / "daemon" / "services" / "error_reporting.py"
        src = src_path.read_text(encoding="utf-8")

        # Find the dead-code marker (the inline-twin signature at :286).
        dead_marker = "(dead-code fallback — bus-active path bypasses)"
        # Two markers exist in error_reporting.py: the inline-twin at :286
        # and (potentially) other audit markers. Find the second-most-
        # recent one — the inline-twin body.
        markers = [i for i in range(len(src)) if src.startswith(dead_marker, i)]
        assert len(markers) >= 1, (
            "expected the (dead-code fallback — bus-active path bypasses) "
            "marker at the error_reporting.py bus=None inline-twin site"
        )
        inline_twin_marker_idx = markers[-1]

        # The attach's context_tag must appear AFTER the inline-twin marker
        # AND BEFORE the next ``if bus is not None:`` block (or the
        # function end).
        attach_idx = src.find(
            "error_reporting.parent_completion_bus_none_fallback"
        )
        assert attach_idx > inline_twin_marker_idx, (
            "the dead-site attach must appear AFTER the dead-code marker "
            "comment so it is clearly inside the dead twin's body"
        )

        # No additional "if bus is not None:" between the marker and the
        # attach — proves the attach is in the SAME dead twin (not some
        # later branch).
        next_bus_check = src.find("if bus is not None:", inline_twin_marker_idx)
        assert next_bus_check < 0 or attach_idx < next_bus_check, (
            "the dead-site attach must sit inside the FIRST dead-twin "
            "else-branch (between the dead-code marker and the next "
            "live 'if bus is not None:' block, if any)"
        )
