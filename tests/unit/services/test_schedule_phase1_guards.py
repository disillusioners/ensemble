"""PIN — the Phase-1 fix-round GUARDS on the schedule surfaces
(agents-ensemble, branch feature/schedule-listing-truth-phase1).

Three behaviours from this round's commits (38cb02c2c / a70b3980d /
b6157743a), each traced to the v4 ruling of record
(.agents/shared/planning/weekly-random-windowed-scheduling/architecture-recommendation-v4.md
§3.3 item 3 and §4.1–§4.4):

* **M-A (armed gate)** — ARMED = ``enabled`` AND ``status`` not in
  {cancelled, stopped} (``svc.is_armed``).  A DISARMED row renders NO
  next-fire on ANY surface (``svc.row_next_run`` returns
  ``(None, None, warning)``); the surfaces expose ``enabled`` + ``armed``
  so the operator can SEE the disarmed state.  Pinned on the tool list,
  the tool get, and the tool-update echo (``ScheduleUpdateResponse``).

* **M-D (ONE next-fire value)** — every surface derives the next-fire
  from the STORED config via the ONE shared helper
  ``svc.row_next_run``.  BOTH adapter-fallback spans in
  ``daemon/routers/schedules.py`` were deleted: there is no
  ``_get_next_trigger_time`` call left in the router, so the adapter's
  ``"__PAST_LATENESS_CAP__"`` STRING sentinel can never reach a
  response.  Pinned behaviourally (the tool list ignores a live
  adapter's claimed value for a row whose stored config yields no
  next-fire) AND statically (the router source contains no
  ``_get_next_trigger_time`` CALL — comments and doc strings are
  excluded via tokenization, so the pin is not tripped by prose).

* **M-E (fail-closed namespace)** — ``svc._rows_named`` raises
  ``LabelNamespaceUnavailable`` instead of returning partial rows on a
  scan error; ``live_label_conflict`` and ``resolve_label_row``
  propagate it (the historical first-match fallback is guarded too).
  NOTHING answers "no conflict" when the scan is unavailable.

MUTATION-VERIFIED (skill ``mutation-verified-regression-pins``): each
pin was proven to bite in the lane's IN-LANE MUTATION WINDOW — plant
the defect ⇒ RED, restore ⇒ GREEN, per transition.

SAFETY FENCE: pure unit tests — in-memory doubles only.  No DB, no
daemon boot, no live contact.
"""

from __future__ import annotations

import inspect
import io
import tokenize
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services import scheduling_service as svc


# ---------------------------------------------------------------------------
# Doubles (the style of tests/unit/services/test_schedule_listing_truth.py)
# ---------------------------------------------------------------------------


def _row(
    source_id: str,
    *,
    name: str | None = None,
    config: dict | None = None,
    status: str = "running",
    enabled: bool = True,
    source_type: str = "scheduler",
) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        name=name if name is not None else source_id,
        config=config or {},
        credentials=None,
        enabled=enabled,
        autostart=True,
        status=status,
        error_message=None,
        created_at="2026-11-01T00:00:00+00:00",
        updated_at="2026-11-01T00:00:00+00:00",
    )


class _FakeRepo:
    """In-memory source repository double with PAGE-AWARE listing."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def add(self, row: SimpleNamespace) -> SimpleNamespace:
        self.rows[row.source_id] = row
        return row

    # -- reads --------------------------------------------------------------
    def get_source_config(self, source_id):
        return self.rows.get(source_id)

    def get_source_config_by_name(self, name):
        for row in self.rows.values():
            if row.name == name:
                return row
        return None

    def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
        rows = list(self.rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if enabled is not None:
            rows = [r for r in rows if r.enabled == enabled]
        return rows[offset : offset + limit]

    def get_latest_execution(self, schedule_id):
        return None

    # -- writes -------------------------------------------------------------
    def update_source_config(self, source_id, source_type=None, name=None,
                             config=None, credentials=None, enabled=True,
                             autostart=True):
        row = self.rows.get(source_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if config is not None:
            row.config = config
        if credentials is not None:
            row.credentials = credentials
        row.enabled = enabled
        row.autostart = autostart
        return row

    def update_source_status(self, source_id, status, error_message=None):
        row = self.rows.get(source_id)
        if row is None:
            return None
        row.status = status
        row.error_message = error_message
        return row


def _cron_config(cron: str = "30 3 * * 0", tz: str = "Asia/Ho_Chi_Minh") -> dict:
    """A canonical recurring config (no chain block, no run_at)."""
    return {
        "agent": "maintenancer",
        "message": "weekly run",
        "timezone": tz,
        "recurrence": "weekly",
        "local_time": "03:30",
        "schedule": cron,
        "instance_mode": "new_instance",
        "project_id": "agents-ensemble",
    }


def _once_config(run_at: datetime, tz: str = "UTC") -> dict:
    return {
        "agent": "ari",
        "message": "hi",
        "timezone": tz,
        "recurrence": "once",
        "local_time": run_at.astimezone().strftime("%H:%M"),
        "run_at": run_at.isoformat(),
        "instance_mode": "new_instance",
        "project_id": "agents-ensemble",
    }


class _UnavailableRepo(_FakeRepo):
    """A repository whose PAGE SCAN is broken (the M-E defect shape)."""

    def __init__(self, *, scan_error: Exception, name_lookup_error: Exception | None = None):
        super().__init__()
        self._scan_error = scan_error
        self._name_lookup_error = name_lookup_error

    def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
        raise self._scan_error

    def get_source_config_by_name(self, name):
        if self._name_lookup_error is not None:
            raise self._name_lookup_error
        return super().get_source_config_by_name(name)


@pytest.fixture
def guards_env(monkeypatch):
    """A configured service with ONE armed recurring row."""
    repo = _FakeRepo()
    repo.add(
        _row("armed-1", name="armed-cron", config=_cron_config(),
             status="running", enabled=True)
    )
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    svc.configure(source_repo=repo, source_registry=registry)
    monkeypatch.setattr(svc, "_source_locks", {})
    yield repo
    svc._deps.source_repo = None
    svc._deps.source_registry = None
    svc._deps.project_repository = None
    svc._source_locks.clear()


# ---------------------------------------------------------------------------
# M-A — the armed gate
# ---------------------------------------------------------------------------


class TestArmedGateMA:
    def test_is_armed_truth_table(self):
        """M-A — ARMED = enabled AND status not in {cancelled, stopped}."""
        assert svc.is_armed(True, "running") is True
        assert svc.is_armed(True, "stopped") is False
        assert svc.is_armed(True, "cancelled") is False
        assert svc.is_armed(False, "running") is False
        assert svc.is_armed(False, "stopped") is False
        assert svc.is_armed(False, "cancelled") is False

    def test_row_next_run_gates_the_fire_instants_on_armed(self):
        """M-A — a disarmed row renders NO next-fire from the ONE helper,
        even though its stored config HAS a next occurrence; an armed row
        renders the stored config's instant."""
        run_at = datetime.now(timezone.utc) + timedelta(days=3)
        config = _once_config(run_at)
        armed = svc.row_next_run(config, enabled=True, status="running")
        paused = svc.row_next_run(config, enabled=False, status="stopped")
        cancelled = svc.row_next_run(config, enabled=True, status="cancelled")
        # ARMED → the stored config's instant.
        assert armed[0] is not None
        assert armed[1] is not None
        assert armed[1].startswith(run_at.astimezone(timezone.utc).isoformat()[:16])
        # DISARMED → the fire instants are NULL on both.
        assert paused[0] is None
        assert paused[1] is None
        assert cancelled[0] is None
        assert cancelled[1] is None

    @pytest.mark.asyncio
    async def test_a_paused_row_renders_no_next_fire_on_the_tool_list(
        self, guards_env
    ):
        """M-A — a paused row (enabled=False + status=stopped) still LISTS
        (it is live namespace-wise) but renders NO next-fire, with
        ``enabled``/``armed`` visible so the operator can SEE why."""
        repo = guards_env
        repo.add(
            _row("paused-1", name="paused-cron", config=_cron_config(),
                 status="stopped", enabled=False)
        )
        items = {i.source_id: i for i in await svc.list_schedules()}
        assert "paused-1" in items
        paused = items["paused-1"]
        assert paused.next_run_at_utc is None
        assert paused.next_run_at_local is None
        assert paused.enabled is False
        assert paused.armed is False
        # The ARMED neighbour keeps its next-fire (asymmetry control).
        assert items["armed-1"].next_run_at_utc is not None
        assert items["armed-1"].armed is True

    @pytest.mark.asyncio
    async def test_get_schedule_disarmed_row_renders_no_next_fire(
        self, guards_env
    ):
        """M-A — the tool GET surface renders the same gate."""
        repo = guards_env
        repo.add(
            _row("paused-2", name="paused-weekly", config=_cron_config(),
                 status="stopped", enabled=False)
        )
        detail = await svc.get_schedule("paused-2")
        assert detail is not None
        assert detail.next_run_at_utc is None
        assert detail.next_run_at_local is None
        assert detail.enabled is False
        assert detail.armed is False

    @pytest.mark.asyncio
    async def test_update_schedule_pause_echo_carries_enabled_and_armed(
        self, guards_env, monkeypatch
    ):
        """M-A — the TOOL-UPDATE echo (``ScheduleUpdateResponse``) carries
        ``enabled``/``armed`` and a paused update renders NO next-fire."""
        import daemon.registry as registry_module

        repo = guards_env
        agent_registry = MagicMock()
        agent_registry.exists = MagicMock(return_value=True)
        monkeypatch.setattr(registry_module, "get_registry", lambda: agent_registry)

        response = await svc.update_schedule("armed-1", {"paused": True})
        assert response.enabled is False
        assert response.armed is False
        assert response.next_run_at_utc is None
        assert response.next_run_at_local is None
        assert response.status == "stopped"


# ---------------------------------------------------------------------------
# M-D — ONE next-fire value, no adapter path
# ---------------------------------------------------------------------------


class TestOneNextFireValueMD:
    def test_row_next_run_is_derived_from_the_stored_config(self):
        """M-D — the helper derives from the STORED config: two rows with
        different stored run_at values get different instants."""
        early = datetime.now(timezone.utc) + timedelta(days=2)
        late = datetime.now(timezone.utc) + timedelta(days=9)
        a = svc.row_next_run(_once_config(early), enabled=True, status="running")
        b = svc.row_next_run(_once_config(late), enabled=True, status="running")
        assert a[1] != b[1]
        assert a[1].startswith(early.astimezone(timezone.utc).isoformat()[:16])
        assert b[1].startswith(late.astimezone(timezone.utc).isoformat()[:16])

    @pytest.mark.asyncio
    async def test_the_tool_list_ignores_a_live_adapters_value(
        self, guards_env
    ):
        """M-D — a row whose stored config yields NO next-fire (a past-due
        one-shot) + a live adapter CLAIMING a different instant: the tool
        list renders the helper's value (null), NEVER the adapter's."""
        repo = guards_env
        run_at_past = datetime.now(timezone.utc) - timedelta(days=1)
        repo.add(
            _row("past-1", name="past-once", config=_once_config(run_at_past),
                 status="running", enabled=True)
        )
        adapter = MagicMock()
        adapter._get_next_trigger_time = MagicMock(
            return_value=datetime(1999, 1, 1, 0, 0, tzinfo=timezone.utc)
        )
        registry = MagicMock()
        registry.get = MagicMock(return_value=adapter)
        svc.configure(source_repo=repo, source_registry=registry)
        items = {i.source_id: i for i in await svc.list_schedules()}
        item = items["past-1"]
        # The adapter claims 1999; the STORED config (past ⇒ no next
        # occurrence) is the authority ⇒ the render is null.
        assert item.next_run_at_utc is None
        assert item.next_run_at_local is None

    def test_router_source_contains_no_adapter_next_trigger_call(self):
        """M-D STATIC leg — the REST router source contains NO
        ``_get_next_trigger_time`` CALL.  Tokenization excludes comments
        and string literals, so prose (the explanatory comments this
        round landed) cannot trip the pin, and only a real call — the
        defect shape — turns it RED."""
        import daemon.routers.schedules as schedules_router

        path = inspect.getfile(schedules_router)
        with open(path, "rb") as fh:
            tokens = list(tokenize.tokenize(fh.readline))
        name_tokens = [
            tok.string
            for tok in tokens
            if tok.type == tokenize.NAME
        ]
        assert "_get_next_trigger_time" not in name_tokens, (
            "the REST router must not call the adapter's "
            "_get_next_trigger_time (M-D: ONE stored-config next-fire "
            "value on every surface)"
        )


# ---------------------------------------------------------------------------
# M-E — the label namespace scan FAILS CLOSED
# ---------------------------------------------------------------------------


class TestFailClosedNamespaceME:
    def _unavailable_repo(self) -> _UnavailableRepo:
        return _UnavailableRepo(
            scan_error=RuntimeError("repository unavailable"),
            name_lookup_error=RuntimeError("repository unavailable"),
        )

    def test_rows_named_raises_when_the_scan_fails(self):
        """M-E — ``_rows_named`` itself raises on a broken scan (never a
        partial-row answer)."""
        repo = self._unavailable_repo()
        with pytest.raises(svc.LabelNamespaceUnavailable) as excinfo:
            svc._rows_named(repo, "some-label")
        assert "some-label" in str(excinfo.value)

    def test_live_label_conflict_raises_label_namespace_unavailable(self):
        """M-E — ``live_label_conflict`` PROPAGATES the unavailable scan;
        it never answers False (= 'no conflict') on a broken scan."""
        repo = self._unavailable_repo()
        with pytest.raises(svc.LabelNamespaceUnavailable):
            svc.live_label_conflict(repo, "some-label")

    def test_resolve_label_row_raises_label_namespace_unavailable(self):
        """M-E — ``resolve_label_row`` PROPAGATES the unavailable scan."""
        repo = self._unavailable_repo()
        with pytest.raises(svc.LabelNamespaceUnavailable):
            svc.resolve_label_row(repo, "some-label")

    def test_the_first_match_fallback_is_guarded_too(self):
        """M-E — the HISTORICAL first-match fallback (taken only when the
        scan succeeded and no live row carries the label) is guarded: a
        repository error there raises, it is never a silent wrong
        answer."""
        repo = _FakeRepo()  # scan succeeds (short page), name lookup fails
        def _boom(name):
            raise RuntimeError("name lookup unavailable")
        repo.get_source_config_by_name = _boom
        with pytest.raises(svc.LabelNamespaceUnavailable):
            svc.resolve_label_row(repo, "some-label")

    def test_nothing_answers_no_conflict_when_the_scan_is_unavailable(self):
        """M-E — the fail-open regression shape ('the scan error degrades
        to no rows seen') is EXACTLY what this pin forbids: the caller
        must see the exception, not a False/None."""
        repo = self._unavailable_repo()
        observed: Any = None
        try:
            observed = svc.live_label_conflict(repo, "some-label")
        except svc.LabelNamespaceUnavailable:
            observed = "RAISED"
        assert observed == "RAISED", (
            "a broken namespace scan must NOT be answered as 'no "
            f"conflict' (got {observed!r})"
        )
