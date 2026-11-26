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

MUTATION EVIDENCE (skill ``mutation-verified-regression-pins``): the pins
are PER-PIN mutation-proven — each pin's own docstring names — or states
in prose — the single edit that turns it RED, and that RED was
demonstrated in the round that added the pin (mutate ⇒ RED ⇒ revert ⇒
GREEN).  There is NO blanket "each pin" claim here: where a pin can only
bite a COMBINED mutation (the two halves of one rule), its docstring
says so explicitly.

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


def _snapshot(row: SimpleNamespace) -> SimpleNamespace:
    """A DETACHED copy of a row — the repository hands out fresh objects.

    The real repository returns a NEW object per read/write (a
    session-scoped ``session.get`` / ``session.refresh``): a value read
    BEFORE a later write does NOT observe that write.  The double must
    behave the same way — when it ALIASES the stored row, a pre-write
    read and a post-write read are the SAME object and every pin that
    asserts "the echo reflects the POST-write row" is masked (MA-4).
    """
    return SimpleNamespace(
        source_id=row.source_id,
        source_type=row.source_type,
        name=row.name,
        config=dict(row.config or {}),
        credentials=row.credentials,
        enabled=row.enabled,
        autostart=row.autostart,
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class _FakeRepo:
    """In-memory source repository double with PAGE-AWARE listing.

    Every read AND every write hands back a DETACHED snapshot (see
    ``_snapshot``) — the double mirrors the real repository's
    session-per-call object identity.
    """

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        #: How many times the PAGE SCAN ran — the namespace scan's
        #: observable side effect (the own-name short-circuit must NOT
        #: reach the scan).
        self.scan_calls = 0

    def add(self, row: SimpleNamespace) -> SimpleNamespace:
        self.rows[row.source_id] = row
        return row

    # -- reads --------------------------------------------------------------
    def get_source_config(self, source_id):
        row = self.rows.get(source_id)
        return _snapshot(row) if row is not None else None

    def get_source_config_by_name(self, name):
        for row in self.rows.values():
            if row.name == name:
                return _snapshot(row)
        return None

    def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
        self.scan_calls += 1
        rows = list(self.rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if enabled is not None:
            rows = [r for r in rows if r.enabled == enabled]
        return [_snapshot(r) for r in rows[offset : offset + limit]]

    def get_latest_execution(self, schedule_id):
        return None

    # -- writes -------------------------------------------------------------
    def create_source_config(self, source_type, name, config, credentials=None,
                             enabled=True, autostart=True, source_id=None):
        source_id = source_id or f"created-{len(self.rows) + 1}"
        row = SimpleNamespace(
            source_id=source_id,
            source_type=source_type,
            name=name,
            config=dict(config or {}),
            credentials=credentials,
            enabled=enabled,
            autostart=autostart,
            # The real repository creates a row STOPPED — a fresh row is
            # not armed until the adapter starts it.  The double mirrors
            # that, so the create echo's armed gate is exercised.
            status="stopped",
            error_message=None,
            created_at="2026-11-01T00:00:00+00:00",
            updated_at="2026-11-01T00:00:00+00:00",
        )
        self.rows[source_id] = row
        return _snapshot(row)

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
        return _snapshot(row)

    def update_source_status(self, source_id, status, error_message=None):
        row = self.rows.get(source_id)
        if row is None:
            return None
        row.status = status
        row.error_message = error_message
        return _snapshot(row)


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


def _registry_double(repo, *, starts: bool):
    """A source-registry double whose adapter start DOES or DOES NOT flip
    the row to ``running`` (the real registry writes RUNNING when the
    adapter starts).  ``starts=False`` models an adapter that failed to
    start — the row stays ``stopped`` in the DB."""
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())

    async def _start(source_id):
        if starts:
            row = repo.rows.get(source_id)
            if row is not None:
                row.status = "running"
        return starts

    registry.start_adapter = AsyncMock(side_effect=_start)
    return registry


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
# MA-2 — the echo surfaces are ARMED-GATED (the pre-write snapshot bug)
# ---------------------------------------------------------------------------


class TestArmedGatedEchoMA2:
    """MA-2 — the CREATE and UPDATE echoes render the POST-WRITE row.

    The defect: both echoes read the PRE-WRITE snapshot.
    ``update_source_config`` hands back an object from its OWN session and
    the status write that follows opened ANOTHER session, so the
    snapshot's ``status`` stayed STALE (``running``) while the DB held
    ``stopped`` — the echo then reported ``armed`` + a next-fire for a
    DISARMED row.  The self-alarm resume detector
    (``daemon/tools/self_alarm.py:1403-1425``) reads exactly those three
    fields (``paused`` / ``status`` / ``next_run_at_utc``), so the false
    "armed" signal was read as a successful resume.
    """

    @pytest.mark.asyncio
    async def test_the_update_echo_reports_a_stopped_row_as_disarmed(
        self, guards_env, monkeypatch
    ):
        """MA-2 (UPDATE) — a row the DB holds ``stopped`` is echoed as
        DISARMED: ``armed is False`` and NO next-fire on either instant.

        COUNTER-MUTATION: revert the post-write re-read in
        ``scheduling_service.update_schedule`` (read ``updated.status``
        again) ⇒ the STALE pre-write status (``running``) is echoed ⇒
        ``armed`` is True and a next-fire is rendered ⇒ RED."""
        import daemon.registry as registry_module

        repo = guards_env
        agent_registry = MagicMock()
        agent_registry.exists = MagicMock(return_value=True)
        monkeypatch.setattr(registry_module, "get_registry", lambda: agent_registry)
        # The adapter does NOT start ⇒ the row stays ``stopped`` in the DB.
        svc.configure(
            source_repo=repo, source_registry=_registry_double(repo, starts=False)
        )

        response = await svc.update_schedule("armed-1", {"paused": False})

        # The DB holds ``stopped`` — the echo must reflect the POST-WRITE row.
        assert repo.rows["armed-1"].status == "stopped"
        assert response.status == "stopped"
        assert response.armed is False
        assert response.next_run_at_utc is None
        assert response.next_run_at_local is None

    @pytest.mark.asyncio
    async def test_the_update_echo_reports_a_started_row_as_armed(
        self, guards_env, monkeypatch
    ):
        """MA-2 (UPDATE) CONTROL leg — the adapter STARTED ⇒ the row is
        ``running`` in the DB ⇒ the echo reports ``armed`` and renders the
        next-fire (the asymmetry control for the pin above)."""
        import daemon.registry as registry_module

        repo = guards_env
        agent_registry = MagicMock()
        agent_registry.exists = MagicMock(return_value=True)
        monkeypatch.setattr(registry_module, "get_registry", lambda: agent_registry)
        svc.configure(
            source_repo=repo, source_registry=_registry_double(repo, starts=True)
        )

        response = await svc.update_schedule("armed-1", {"paused": False})

        assert repo.rows["armed-1"].status == "running"
        assert response.status == "running"
        assert response.armed is True
        assert response.next_run_at_utc is not None

    @pytest.mark.asyncio
    async def test_the_create_echo_of_a_row_the_adapter_did_not_start_renders_no_next_fire(
        self, guards_env, monkeypatch
    ):
        """MA-2 (CREATE) — the repository creates the row ``stopped``
        (``daemon/repositories/source/repository.py``); until the adapter
        ACTUALLY starts it the create echo must render NO next-fire.

        COUNTER-MUTATION: revert the create echo to the ungated
        ``_compute_next_run(config_json)`` ⇒ a next-fire is echoed for a
        DISARMED row ⇒ RED."""
        import daemon.registry as registry_module

        repo = guards_env
        agent_registry = MagicMock()
        agent_registry.exists = MagicMock(return_value=True)
        monkeypatch.setattr(registry_module, "get_registry", lambda: agent_registry)
        svc.configure(
            source_repo=repo, source_registry=_registry_double(repo, starts=False)
        )

        payload = svc.ScheduleCreatePayload(
            label="fresh-cron",
            agent="maintenancer",
            message="weekly run",
            when="03:30",
            timezone="UTC",
            recurrence="weekly",
            weekday=0,
            instance_mode="new_instance",
            project_id="agents-ensemble",
        )
        response = await svc.create_schedule(
            payload, caller_instance_id="", caller_agent_id=""
        )

        # The repository creates the row STOPPED — the echo must agree.
        assert repo.rows[response.source_id].status == "stopped"
        assert response.status == "stopped"
        assert response.next_run_at_utc is None
        assert response.next_run_at_local is None

    @pytest.mark.asyncio
    async def test_the_create_echo_of_a_started_row_renders_its_next_fire(
        self, guards_env, monkeypatch
    ):
        """MA-2 (CREATE) CONTROL leg — the adapter STARTED ⇒ the row is
        ``running`` ⇒ the create echo renders the stored config's
        next-fire (the asymmetry control for the pin above)."""
        import daemon.registry as registry_module

        repo = guards_env
        agent_registry = MagicMock()
        agent_registry.exists = MagicMock(return_value=True)
        monkeypatch.setattr(registry_module, "get_registry", lambda: agent_registry)
        svc.configure(
            source_repo=repo, source_registry=_registry_double(repo, starts=True)
        )

        payload = svc.ScheduleCreatePayload(
            label="fresh-cron-2",
            agent="maintenancer",
            message="weekly run",
            when="03:30",
            timezone="UTC",
            recurrence="weekly",
            weekday=0,
            instance_mode="new_instance",
            project_id="agents-ensemble",
        )
        response = await svc.create_schedule(
            payload, caller_instance_id="", caller_agent_id=""
        )

        assert repo.rows[response.source_id].status == "running"
        assert response.status == "running"
        assert response.next_run_at_utc is not None


# ---------------------------------------------------------------------------
# MA-5 — the armed gate compares the status BY VALUE
# ---------------------------------------------------------------------------


class TestEnumStatusMA5:
    """MA-5 — ``is_armed`` compares the status BY VALUE.

    ``str(SourceStatus.cancelled)`` is ``"SourceStatus.cancelled"`` (the
    enum's ``__str__``), NOT ``"cancelled"`` — so an enum-valued status
    read ARMED.  That is a fail-open: a cancelled or stopped row handed
    over as an enum would report a next-fire.

    COUNTER-MUTATION: revert ``is_armed`` to
    ``str(status) not in {"cancelled", "stopped"}`` ⇒ every enum leg below
    goes RED (``armed`` is True for a cancelled/stopped enum).
    """

    def test_an_enum_valued_status_is_never_armed(self):
        """MA-5 — the REST enum's non-armed members read DISARMED; the
        armed member stays armed; the str-typed case is unchanged."""
        from daemon.models.source import SourceStatus

        # The enum-valued non-armed members.
        assert svc.is_armed(True, SourceStatus.cancelled) is False
        assert svc.is_armed(True, SourceStatus.stopped) is False
        # The enum-valued armed member.
        assert svc.is_armed(True, SourceStatus.running) is True
        # The str-typed case stays green.
        assert svc.is_armed(True, "cancelled") is False
        assert svc.is_armed(True, "stopped") is False
        assert svc.is_armed(True, "running") is True
        # The repository's own enum (upper-case members, same values).
        from daemon.repositories.source.models import SourceStatus as RepoStatus

        assert svc.is_armed(True, RepoStatus.CANCELLED) is False
        assert svc.is_armed(True, RepoStatus.STOPPED) is False
        assert svc.is_armed(True, RepoStatus.RUNNING) is True

    def test_row_next_run_gates_on_an_enum_valued_status(self):
        """MA-5 — the ONE next-fire helper gates on an enum-valued status
        too (a cancelled row renders no fire instant)."""
        from daemon.models.source import SourceStatus

        run_at = datetime.now(timezone.utc) + timedelta(days=3)
        config = _once_config(run_at)
        assert svc.row_next_run(config, enabled=True, status=SourceStatus.running)[1]
        assert (
            svc.row_next_run(config, enabled=True, status=SourceStatus.cancelled)[1]
            is None
        )
        assert (
            svc.row_next_run(config, enabled=True, status=SourceStatus.stopped)[1]
            is None
        )


# ---------------------------------------------------------------------------
# M-6 — the TOOL-path rename rule (the service layer)
# ---------------------------------------------------------------------------


class TestToolPathRenameM6:
    """M-6 — the TOOL-path rename rule, which every existing rename pin
    left UNPINNED (they were all REST-level).

    * the own-name short-circuit: renaming a row to its OWN label is a
      no-op — the namespace scan is never reached;
    * a live/live collision: renaming onto another LIVE row's label is
      refused at the SERVICE layer (``ValueError`` → the tool surface's
      usual ERROR string).
    """

    @pytest.mark.asyncio
    async def test_the_tool_path_rename_to_the_own_name_is_a_noop(
        self, guards_env
    ):
        """M-6 — a rename to the row's OWN label is allowed (no error) and
        the namespace scan is NEVER reached (the own-name short-circuit).

        COUNTER-MUTATION: drop the ``new_label != row.name`` guard in
        ``scheduling_service.update_schedule`` ⇒ the scan runs ⇒
        ``scan_calls`` grows ⇒ RED."""
        repo = guards_env
        svc.configure(
            source_repo=repo, source_registry=_registry_double(repo, starts=False)
        )
        scans_before = repo.scan_calls

        response = await svc.update_schedule("armed-1", {"label": "armed-cron"})

        assert response.label == "armed-cron"
        assert repo.rows["armed-1"].name == "armed-cron"
        # The own-name short-circuit skipped the namespace scan entirely.
        assert repo.scan_calls == scans_before

    @pytest.mark.asyncio
    async def test_the_tool_path_rename_onto_a_live_label_is_refused(
        self, guards_env
    ):
        """M-6 — renaming onto another LIVE row's label raises at the
        SERVICE layer, and the rename never reaches the repository.

        COUNTER-MUTATION: drop the ``live_label_conflict`` check in
        ``scheduling_service.update_schedule`` ⇒ no ValueError ⇒ RED."""
        repo = guards_env
        repo.add(
            _row("other-1", name="taken-label", config=_cron_config(),
                 status="running", enabled=True)
        )
        svc.configure(
            source_repo=repo, source_registry=_registry_double(repo, starts=False)
        )

        with pytest.raises(ValueError) as excinfo:
            await svc.update_schedule("armed-1", {"label": "taken-label"})

        assert "already exists" in str(excinfo.value)
        # The rename never reached the repository.
        assert repo.rows["armed-1"].name == "armed-cron"


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
