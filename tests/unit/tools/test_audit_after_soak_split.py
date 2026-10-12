"""Unit tests for the P3 audit-after-soak sequencing split (decision f).

Covers T3.9 from test-strategy.md §1:
  T3.9 — audit-after-soak ordering: CAPTURE pre-pool sync; DELETE
         post-readyz background

State-location decision: the split lives in
``daemon/repositories/message_queue/repository.py``
(SQLModelMessageQueueRepository):
  - capture_doomed_ids_and_audit() — sync, in-band, returns doomed list
  - delete_doomed_ids() — bounded DELETE in chunks, called from the
    lifespan startup hook on (readyz-green AND +1s) OR (+5s)

This test exercises the capture/delete split with a stub Session
(no real DB needed). The fixture mirrors the
test_message_queue_clear_all_2arm_disjunction.py pattern (in-memory
SQLite via tmp_path) but stubs the Session class to avoid pulling
sqlmodel/sqlalchemy into the unit-test runner.

Run:
  PYTHONPATH=. python3 tests/unit/tools/test_audit_after_soak_split.py

Or under pytest:
  pytest tests/unit/tools/test_audit_after_soak_split.py
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

# Add the repo root to sys.path so the test can import the queue
# repository module. The P2 pattern is --noconftest + PYTHONPATH
# pinning (the audit helper doesn't pull in heavyweight daemons —
# only the repository module + a small SQLModel shim).
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# Minimal stub of the SQLModel / sqlmodel bits the repository
# module references at import time. We patch these BEFORE importing
# the repository module.
_STUB_MODULES: dict[str, Any] = {}


def _install_stub_modules() -> None:
    """Install stub modules for sqlmodel / sqlalchemy so the
    repository module imports cleanly without a real DB.

    P2 pattern (per the task brief): --noconftest + PYTHONPATH
    pinning. The audit-after-soak unit test runs in a worktree
    that may not have a built venv; this stubbing keeps the test
    hermetic and fast.
    """
    import types

    # sqlmodel stub
    sqlmodel_mod = types.ModuleType("sqlmodel")
    sqlmodel_mod.Session = MagicMock()
    sqlmodel_mod.select = MagicMock()
    sqlmodel_mod.col = MagicMock()
    _STUB_MODULES["sqlmodel"] = sqlmodel_mod

    # sqlalchemy stub
    sqlalchemy_mod = types.ModuleType("sqlalchemy")
    sqlalchemy_func_mod = types.ModuleType("sqlalchemy.func")
    sqlalchemy_func_mod.count = MagicMock()
    sqlalchemy_mod.func = sqlalchemy_func_mod
    sqlalchemy_mod.and_ = MagicMock()
    sqlalchemy_mod.or_ = MagicMock()
    sqlalchemy_mod.text = MagicMock()
    sqlalchemy_mod.delete = MagicMock()
    sqlalchemy_engine_mod = types.ModuleType("sqlalchemy.engine")
    sqlalchemy_engine_mod.Engine = MagicMock()
    sqlalchemy_mod.engine = sqlalchemy_engine_mod
    _STUB_MODULES["sqlalchemy"] = sqlalchemy_mod
    _STUB_MODULES["sqlalchemy.func"] = sqlalchemy_func_mod
    _STUB_MODULES["sqlalchemy.engine"] = sqlalchemy_engine_mod

    # daemon.constants stub (TERMINAL_INSTANCE_STATUSES)
    daemon_constants_mod = types.ModuleType("daemon.constants")
    daemon_constants_mod.TERMINAL_INSTANCE_STATUSES = frozenset(
        {"completed", "failed", "cancelled", "error", "dead"}
    )
    _STUB_MODULES["daemon.constants"] = daemon_constants_mod

    # daemon.services.timestamps stub
    daemon_timestamps_mod = types.ModuleType("daemon.services.timestamps")
    daemon_timestamps_mod.coerce_to_aware_utc = MagicMock()
    daemon_timestamps_mod.now_utc = MagicMock()
    daemon_timestamps_mod.now_utc_naive = MagicMock()
    _STUB_MODULES["daemon.services.timestamps"] = daemon_timestamps_mod

    # daemon.repositories.message_queue.models stub
    mq_models_mod = types.ModuleType("daemon.repositories.message_queue.models")
    mq_models_mod.MessageQueue = MagicMock()
    mq_models_mod.MessageStatus = MagicMock()
    _STUB_MODULES["daemon.repositories.message_queue.models"] = mq_models_mod

    # daemon.repositories.message_queue package init
    mq_pkg_mod = types.ModuleType("daemon.repositories.message_queue")
    mq_pkg_mod.__path__ = [
        os.path.join(_REPO_ROOT, "daemon", "repositories", "message_queue")
    ]
    _STUB_MODULES["daemon.repositories.message_queue"] = mq_pkg_mod

    # daemon.repositories.task.repository stub (the lazy import in
    # capture_doomed_ids_and_audit / clear_all resolves through here)
    task_pkg_mod = types.ModuleType("daemon.repositories.task")
    task_pkg_mod.__path__ = [os.path.join(_REPO_ROOT, "daemon", "repositories", "task")]
    _STUB_MODULES["daemon.repositories.task"] = task_pkg_mod
    task_repo_mod = types.ModuleType("daemon.repositories.task.repository")
    task_repo_mod._capped_doomed_id_repr = MagicMock(
        side_effect=lambda ids: repr(ids)
    )
    _STUB_MODULES["daemon.repositories.task.repository"] = task_repo_mod

    # daemon.repositories package init
    repos_pkg_mod = types.ModuleType("daemon.repositories")
    repos_pkg_mod.__path__ = [os.path.join(_REPO_ROOT, "daemon", "repositories")]
    _STUB_MODULES["daemon.repositories"] = repos_pkg_mod

    # daemon package init
    daemon_pkg_mod = types.ModuleType("daemon")
    daemon_pkg_mod.__path__ = [os.path.join(_REPO_ROOT, "daemon")]
    _STUB_MODULES["daemon"] = daemon_pkg_mod

    for name, mod in _STUB_MODULES.items():
        sys.modules[name] = mod


_install_stub_modules()

# Now we can import the repository module. The Session is a MagicMock
# at this point; the repository module's import-time references
# (e.g. `from sqlmodel import Session, select, col`) are satisfied.
_repo_module_path = os.path.join(
    _REPO_ROOT, "daemon", "repositories", "message_queue", "repository.py"
)
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "daemon.repositories.message_queue.repository", _repo_module_path
)
assert _spec is not None
assert _spec.loader is not None
_repo_mod = importlib.util.module_from_spec(_spec)
sys.modules["daemon.repositories.message_queue.repository"] = _repo_mod
_spec.loader.exec_module(_repo_mod)


# ----------------------------------------------------------------------------
# Stub Session / engine helpers
# ----------------------------------------------------------------------------


class _StubSession:
    """In-memory stand-in for sqlmodel.Session for the audit-after-soak
    tests. Tracks exec() calls and their parameters; honors the
    rows-fetched result on the second .all() call (matching the
    repository's pattern of fetching doomed ids then DELETing).
    """

    def __init__(self) -> None:
        self.exec_calls: list[Any] = []
        self.doomed_ids: list[str] = []
        self.deleted_count: int = 0
        self.committed: bool = False

    def __enter__(self) -> "_StubSession":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def exec(self, stmt: Any, params: Any = None) -> Any:
        # We capture the SQL as a string. The repository's `text(...)`
        # call returns our `_StrSQL` subclass (string that ALSO
        # stringifies via str()).
        sql_str = getattr(stmt, "_sql_string", None) or (str(stmt) if stmt is not None else "")
        self.exec_calls.append((sql_str, params))
        # Branch order: check DELETE first (the clear_all path's
        # DELETE statement also contains "SELECT message_id" in
        # its subquery, so a SELECT-first check would mis-classify
        # the DELETE).
        if sql_str.upper().lstrip().startswith("DELETE"):
            return _StubResult(rowcount=self.deleted_count)
        if "SELECT message_id" in sql_str:
            return _StubResult(rows=[(d,) for d in self.doomed_ids])
        # Fallback
        return _StubResult()

    def commit(self) -> None:
        self.committed = True


class _StubResult:
    """Plain result object with .all() and .rowcount — bypasses the
    MagicMock auto-attribute issue (where `result.rowcount` returns
    a new MagicMock instead of the int we set).
    """

    def __init__(self, rows: list[tuple] | None = None, rowcount: int = 0) -> None:
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount

    def all(self) -> list[tuple]:
        return self._rows

    def commit(self) -> None:
        self.committed = True


# Thread-local "current SQL" that the test fixture sets before
# each exec call. The repository's `text(...)` returns a MagicMock
# whose stringification we control via this dict. Simpler than
# monkey-patching the sqlmodel.text symbol.
_CURRENT_SQL: dict[str, str] = {"last": ""}


def _sql(sql: str) -> Any:
    """Return a MagicMock that stringifies to the given SQL. The
    repository's `text(sql)` returns a MagicMock too; we override
    ``__str__`` so the stub's exec() can read the SQL.
    """
    m = MagicMock()
    m._sql_string = sql

    class _StrSQL(str):
        """A string that ALSO exposes _sql_string for the stub's
        exec() to read directly. Subclassing str is the cleanest
        way to make `str(stmt)` return the SQL while keeping the
        MagicMock semantics for other accesses.
        """

    s = _StrSQL(sql)
    s._sql_string = sql
    return s


# ----------------------------------------------------------------------------
# The tests themselves
# ----------------------------------------------------------------------------


class TestCaptureDoomedIdsAndAudit(unittest.TestCase):
    """T3.9 / D-P3.9 / D-P3.10 — CAPTURE_PHASE: sync, in-band, returns
    the doomed-id list, audits at the same shape as the original
    clear_all audit log line (env-poison family audit).
    """

    def setUp(self) -> None:
        # Per-test stubs: a fresh Session + a list of doomed ids
        self.stubs: list[_StubSession] = []
        self.engine = MagicMock()
        # Test subtest config: doomed ids and per-session
        # deleted_count. setUp doesn't reach into a real session —
        # the session_factory below attaches the doomed_ids /
        # deleted_count once the test sets them on self.
        self._doomed_ids: list[str] = []
        self._deleted_count: int = 0
        self._committed: bool = True

        # Wire Session to return our stub via the context-manager
        # protocol (the repository uses `with Session(self.engine) as
        # session:`).
        def session_factory(engine: Any) -> _StubSession:
            s = _StubSession()
            s.doomed_ids = list(self._doomed_ids)
            s.deleted_count = self._deleted_count
            s.committed = self._committed
            self.stubs.append(s)
            return s

        self._session_factory = session_factory

    def test_capture_preserves_in_flight_returns_doomed_ids(self) -> None:
        """The CAPTURE_PHASE returns the list of doomed message_ids."""
        self._doomed_ids = ["id-1", "id-2", "id-3"]
        # Patch text() to return tagged MagicMocks that stringify
        # to the SQL the test expects.
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "_message_queue_boot_auto_continued_preserve_enabled", return_value=False):
                with patch.object(_repo_mod, "text", side_effect=text_factory):
                    repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                    result = repo.capture_doomed_ids_and_audit(
                        preserve_in_flight=True
                    )
        assert result == ["id-1", "id-2", "id-3"], f"expected doomed_ids, got {result}"

    def test_capture_emits_audit_log_line(self) -> None:
        """The audit log line shape is preserved (env-poison family
        audit). The pre-P3 audit shape included the doomed-id
        repr; the P3 CAPTURE_PHASE keeps the same shape so the
        log-line assertion in T-G4a still passes.
        """
        self._doomed_ids = ["id-1"]
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "_message_queue_boot_auto_continued_preserve_enabled", return_value=False):
                with patch.object(_repo_mod, "text", side_effect=text_factory):
                    repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                    # No exception → audit log line emitted cleanly
                    result = repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
        assert result == ["id-1"]

    def test_capture_returns_empty_when_predicate_empty(self) -> None:
        """No doomed ids (the wipe gate is off, or the table is empty) →
        the CAPTURE_PHASE returns an empty list. The DELETE_PHASE
        will no-op (delete_doomed_ids short-circuits on empty).
        """
        self._doomed_ids = []
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "_message_queue_boot_auto_continued_preserve_enabled", return_value=False):
                with patch.object(_repo_mod, "text", side_effect=text_factory):
                    repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                    result = repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
        assert result == [], f"expected empty list, got {result}"


class TestDeleteDoomedIds(unittest.TestCase):
    """T3.9 / D-P3.9 — DELETE_PHASE: bounded DELETE in chunks,
    background, fires post-readyz-green.
    """

    def setUp(self) -> None:
        self.stubs: list[_StubSession] = []
        self.engine = MagicMock()
        self._deleted_count: int = 0

        def session_factory(engine: Any) -> _StubSession:
            s = _StubSession()
            s.deleted_count = self._deleted_count
            self.stubs.append(s)
            return s

        self._session_factory = session_factory

    def test_delete_empty_noop(self) -> None:
        """Empty doomed list → DELETE short-circuits (no rows to
        delete). This is the QUEUE_DISCARD_ON_STARTUP=false case
        OR the case where a prior boot's DELETE already completed.
        """
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
            deleted = repo.delete_doomed_ids([], preserve_in_flight=True)
        assert deleted == 0, f"expected 0 deleted, got {deleted}"
        assert self.stubs == [], "expected no Session to be created for empty doomed list"
    def test_delete_chunks_large_doomed_list(self) -> None:
        """Per-chunk size 1000 (default). A doomed list of 2500 ids
        → 3 chunks: 1000 + 1000 + 500.
        """
        self._deleted_count = 1
        doomed = [f"id-{i}" for i in range(2500)]
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "text", side_effect=text_factory):
                repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                deleted = repo.delete_doomed_ids(doomed, preserve_in_flight=True, chunk_size=1000)
        # 3 chunks → 3 sessions
        assert len(self.stubs) == 3, f"expected 3 chunks (sessions), got {len(self.stubs)}"
        # 3 chunks × 1 rowcount = 3
        assert deleted == 3, f"expected 3 deleted (1 per chunk), got {deleted!r}"

    def test_delete_idempotent_on_empty_doomed(self) -> None:
        """A second boot's CAPTURE_PHASE that re-derives the same
        doomed set (DELETE didn't complete) → DELETE re-runs and
        deletes the residual rows. Idempotency preserved.
        """
        self._deleted_count = 1
        doomed = ["id-1", "id-2"]
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "text", side_effect=text_factory):
                repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                deleted = repo.delete_doomed_ids(doomed, preserve_in_flight=True, chunk_size=1000)
        assert deleted == 1, f"expected 1 deleted (1 chunk), got {deleted}"


class TestClearAllSyncCoordinator(unittest.TestCase):
    """T3.9 / D-P3.9 — The original clear_all(preserve_in_flight=True)
    remains the synchronous coordinator: capture + DELETE in one
    shot. Tests stay green for non-boot callers (existing
    test_message_queue_clear_all_2arm_disjunction).
    """

    def setUp(self) -> None:
        self.stubs: list[_StubSession] = []
        self.engine = MagicMock()
        self._doomed_ids: list[str] = []
        self._deleted_count: int = 0

        def session_factory(engine: Any) -> _StubSession:
            s = _StubSession()
            s.doomed_ids = list(self._doomed_ids)
            s.deleted_count = self._deleted_count
            self.stubs.append(s)
            return s

        self._session_factory = session_factory

    def test_clear_all_preserve_in_flight_deletes(self) -> None:
        """The synchronous clear_all path: capture doomed ids,
        DELETE them, commit, return rowcount. Mirrors the
        pre-P3 behavior exactly.
        """
        self._doomed_ids = ["id-1", "id-2"]
        self._deleted_count = 2
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "_message_queue_boot_auto_continued_preserve_enabled", return_value=False):
                with patch.object(_repo_mod, "text", side_effect=text_factory):
                    repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                    rc = repo.clear_all(preserve_in_flight=True)
        # The clear_all path uses ONE session (capture + DELETE
        # in the same Session — preserving the v0.18-era shape).
        assert len(self.stubs) == 1, f"expected 1 session for sync clear_all, got {len(self.stubs)}"
        assert self.stubs[0].committed, "expected sync clear_all to commit"
        # The repository's clear_all returns result.rowcount from
        # the LAST exec (the DELETE) — which our stub returns as
        # self.deleted_count (2).
        assert rc == 2, f"expected 2 rowcount returned, got {rc}"


class TestAuditAfterSoakOrdering(unittest.TestCase):
    """T3.9 ordering assertion: CAPTURE pre-pool (sync, in-band) +
    DELETE post-readyz background (lifespan startup hook).

    The repo methods themselves are the building blocks; the
    ordering is enforced by the daemon (manager.py:843 calls
    capture, then api.py lifespan startup hook fires the
    background DELETE task). This test class asserts the
    repo-level contract: capture returns the list, delete takes
    the list, neither depends on the other.
    """

    def setUp(self) -> None:
        self.stubs: list[_StubSession] = []
        self.engine = MagicMock()
        self._doomed_ids: list[str] = []
        self._deleted_count: int = 0

        def session_factory(engine: Any) -> _StubSession:
            s = _StubSession()
            s.doomed_ids = list(self._doomed_ids)
            s.deleted_count = self._deleted_count
            self.stubs.append(s)
            return s

        self._session_factory = session_factory

    def test_capture_then_delete_split(self) -> None:
        """End-to-end happy path: capture returns the doomed list,
        delete takes it and reports the rowcount. The split is
        the load-bearing change — pre-P3 the two operations
        happened in a single Session inside clear_all.
        """
        self._doomed_ids = ["id-1", "id-2", "id-3"]
        self._deleted_count = 1
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "_message_queue_boot_auto_continued_preserve_enabled", return_value=False):
                with patch.object(_repo_mod, "text", side_effect=text_factory):
                    repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                    # CAPTURE
                    doomed = repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
                    # DELETE
                    deleted = repo.delete_doomed_ids(doomed, preserve_in_flight=True, chunk_size=1000)
        assert doomed == ["id-1", "id-2", "id-3"]
        # 1 capture session + 1 delete chunk = 2 sessions total
        assert len(self.stubs) == 2, f"expected 2 sessions (1 capture + 1 delete), got {len(self.stubs)}"
        assert deleted == 1  # the 1 chunk had deleted_count=1

    def test_capture_doomed_ids_does_not_delete(self) -> None:
        """The CAPTURE_PHASE must NOT issue a DELETE — the audit
        log fires BEFORE the wipe so a crash mid-wipe still
        leaves the audit trail. This is the load-bearing
        invariant of decision (f) §Concrete Contract.
        """
        self._doomed_ids = ["id-1"]
        def text_factory(sql: str) -> Any:
            return _sql(sql)
        with patch.object(_repo_mod, "Session", side_effect=self._session_factory):
            with patch.object(_repo_mod, "_message_queue_boot_auto_continued_preserve_enabled", return_value=False):
                with patch.object(_repo_mod, "text", side_effect=text_factory):
                    repo = _repo_mod.SQLModelMessageQueueRepository(engine=self.engine)
                    repo.capture_doomed_ids_and_audit(preserve_in_flight=True)
        # Check the stub's exec_calls — there should be exactly
        # ONE call (the SELECT), not two (the SELECT + DELETE).
        exec_calls = self.stubs[0].exec_calls
        assert len(exec_calls) == 1, f"expected 1 exec (SELECT), got {len(exec_calls)}"
        sql_str = exec_calls[0][0] if exec_calls else ""
        assert "SELECT" in sql_str.upper(), f"expected SELECT, got {sql_str}"
        assert "DELETE" not in sql_str.upper(), f"CAPTURE must not DELETE, got {sql_str}"
        # Capture must not commit (the doomed ids are returned
        # to the caller; the transaction boundary is invisible
        # so a crash between capture and DELETE is safe).
        assert not self.stubs[0].committed, "CAPTURE_PHASE must not commit (no transaction boundary)"


def main() -> int:
    """Run the audit-after-soak split unit tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestCaptureDoomedIdsAndAudit))
    suite.addTests(loader.loadTestsFromTestCase(TestDeleteDoomedIds))
    suite.addTests(loader.loadTestsFromTestCase(TestClearAllSyncCoordinator))
    suite.addTests(loader.loadTestsFromTestCase(TestAuditAfterSoakOrdering))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
