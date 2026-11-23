"""Regression pins for the SPAWN-STAMP BLIND SPOT of the husk detector.

THE DEFECT (measured on the live ``ensemble_prod`` fleet, 2026-11-23)
---------------------------------------------------------------------
The ``never-dispatched-husk`` detector (#7 of ``instance_health``) could
not see an instance that was SPAWNED but never dispatched, because the
spawn/enqueue transaction stamps ``instances.last_activity_at`` ITSELF
(``daemon/services/instance_messaging.py:2711``) and the detector's
activity conjunct demanded ``last_activity_at IS NULL``.

Live proof instance (still ``running`` four hours after its spawn):

    716908e7-b740-431f-9152-0e90ab64d9b0   (agent ``leader``)
      created_at        2026-11-23T14:51:31.148105+00:00
      last_activity_at  2026-11-23 14:51:31.198498   (+50.393 ms)
      status            running          version 2
      ZERO rows in task / message_queue / job_queue_items /
                checkpoints / checkpoint_blobs / report_injections /
                dependency_watchers, ZERO children

It was spawned inside the 06:17-08:21Z daemon outage and its first
turn never dispatched. The detector flagged FOUR never-dispatched husks
that day and did NOT include it — purely because its
``last_activity_at`` is non-null.

THE FIX
-------
``daemon/tools/instance_read.py``:

* ``SPAWN_STAMP_WINDOW_SECONDS = 60`` — the spawn-stamp window.
* ``_activity_is_spawn_stamp_only(info)`` — the replacement conjunct:
  ``last_activity_at IS NULL`` **OR** the stamp lies inside
  ``[created_at, created_at + SPAWN_STAMP_WINDOW_SECONDS]``.
* ``_husk_row_local_candidate`` uses it; the disagreement pin and the
  flagged-row ``detail`` were rewritten so they describe what the
  detector ACTUALLY does.

The load-bearing evidence is UNCHANGED: the never-dispatched signature
(zero ``message_queue`` rows AND zero ``task`` rows, via the shared
``never_dispatched_evidence_expr``) plus the zero ``job_queue_items``
probe, plus non-terminal, plus the 24h age bound. Only the ACTIVITY
PROXY was widened.

THE PLANNED MUTATIONS (one behaviour per run)
---------------------------------------------
* restore ``last_activity_at IS NULL`` (the pre-fix conjunct) — the
  spawn-stamped fixture stops firing → RED;
* neuter the window (accept ANY stamp) — the "dispatched later"
  fixture fires → RED;
* drop the window's lower bound (accept a stamp that PREDATES the
  creation) — the clock-skew fixture fires → RED;
* drop the evidence legs for the spawn-stamped branch — the
  "spawn-stamped WITH a task row" fixture fires → RED.

SAFETY FENCE: every repository pin runs on a ``tempfile`` SQLite
database created per test; ``HOME`` is redirected at a throwaway
directory by the mechanical ``install_fake_home`` fence. Nothing here
touches ``ensemble_prod`` or the live daemon.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.tools import instance_read as ir
from tests.helpers.nestops_pg_fixtures import by_name, call, install_fake_home


# ────────────────────────────────────────────────────────────────────────
# harness
# ────────────────────────────────────────────────────────────────────────

#: The age the husk threshold demands (24h) plus a generous margin, so
#: the age leg is never the reason a fixture passes or fails.
HUSK_AGE = timedelta(seconds=ir.DEFAULT_HUSK_MIN_AGE_SECONDS + 3600)

#: The LIVE spawn-stamp distance of the proof instance: +50.393 ms.
LIVE_SPAWN_STAMP = timedelta(milliseconds=50)


@pytest.fixture(autouse=True)
def _fake_home_fence(monkeypatch, tmp_path):
    """The mechanical HOME fence (see ``install_fake_home``)."""
    install_fake_home(monkeypatch, tmp_path)


@pytest.fixture()
def fleet_db():
    """A scratch SQLite engine with the instance + evidence tables."""
    tmpdir = tempfile.mkdtemp(prefix="husk-spawnstamp-fleet-")
    engine = create_engine(f"sqlite:///{tmpdir}/fleet.db")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class _FakeTaskRepo:
    def __init__(self, tasks: dict[str, list] | None = None):
        self._tasks = tasks or {}

    def get_by_instance(self, instance_id):
        return self._tasks.get(instance_id, [])


class _FakeQuestionManager:
    def __init__(self, packs: dict[str, object] | None = None):
        self._packs = packs or {}

    def get_question_pack(self, instance_id):
        return self._packs.get(instance_id)


class _RepoBackedManager:
    """The manager surface the read tools touch, over a REAL repository."""

    def __init__(self, engine, *, tasks=None, packs=None):
        self.engine = engine
        self._instance_repository = SQLModelInstanceRepository(engine)
        self._task_repo = _FakeTaskRepo(tasks)
        self._question_manager = _FakeQuestionManager(packs)

    async def get_instance(self, instance_id):
        return object()

    async def ensure_mcp_preloaded(self, instance_id):
        return []

    def find_near_instance(self, instance_id, max_distance=7):
        return []

    def list_instances(self, limit=10, offset=0, status=None, **kw):
        instances, total, truncated = self._instance_repository.list(
            limit=limit,
            offset=offset,
            status=status,
            exclude_kb=kw.get("exclude_kb", True),
        )
        result = []
        for inst in instances:
            info = inst.to_dict()
            info["children"] = self._instance_repository.list_child_ids_permanent(
                inst.instance_id
            )
            result.append(info)
        return result, total, truncated


def _read_tools(manager, *, agent: str = "maintenancer"):
    return ir.create_instance_read_tools(
        manager, current_instance_id="caller-iid", agent_id=agent
    )


def _instance(
    instance_id: str,
    *,
    agent_id: str = "worker",
    status: str = "idle",
    version: int = 1,
    created_at: datetime | None = None,
    last_activity_at: datetime | None = None,
    metadata: dict | None = None,
    parent_id: str | None = None,
) -> Instance:
    created = created_at or datetime.now(timezone.utc)
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/tmp/agents/{agent_id}",
        status=status,
        version=version,
        created_at=created.isoformat(),
        updated_at=created.isoformat(),
        last_activity_at=last_activity_at,
        instance_metadata=metadata or {},
        parent_id=parent_id,
    )


def _aged(**kw) -> datetime:
    return datetime.now(timezone.utc) - HUSK_AGE


def _insert(engine, rows: list[Instance]) -> list[str]:
    ids = [row.instance_id for row in rows]
    with Session(engine) as session:
        for row in rows:
            session.add(row)
        session.commit()
    return ids


class TestSpawnStampBlindSpot:
    """THE DEFECT — the spawned-but-never-dispatched class."""

    async def _health(self, manager):
        return await call(_read_tools(manager), "instance_health")

    def _husk(self, payload):
        for detector in payload["detectors"]:
            if detector["detector"] == "never-dispatched-husk":
                return detector
        raise AssertionError("never-dispatched-husk detector is missing")

    def _flagged_ids(self, payload) -> set[str]:
        return {row["instance_id"] for row in self._husk(payload)["instances"]}

    async def test_spawn_stamped_never_dispatched_row_is_flagged(self, fleet_db):
        """THE CORE PIN: the spawn/enqueue stamp must NOT hide the row.

        The instance was created and enqueued (hence the stamp) but its
        message/task/job rows never materialised — the shape the live
        proof instance has.
        """
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "spawn-stamped-1",
                    status="running",
                    version=2,
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "spawn-stamped-1" in self._flagged_ids(payload)
        row = self._husk(payload)["instances"][0]
        assert "never dispatched" in row["detail"]
        assert "last_activity_at" in row["detail"]

    async def test_the_live_proof_instance_shape_is_flagged(self, fleet_db):
        """The EXACT live shape of ``716908e7-…`` — id, agent, status,
        version, parent, and the +50.393 ms spawn-stamp distance.

        The live row's ``created_at`` is BACKDATED by a day here: the
        live instance is only ~4.4h old, so what currently keeps it
        out of the report is the 24h AGE leg (separately pinned), not
        the activity conjunct. Backdating isolates the conjunct under
        test — it is the conjunct that was blind.
        """
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "716908e7-b740-431f-9152-0e90ab64d9b0",
                    agent_id="leader",
                    status="running",
                    version=2,
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                    parent_id="1b60e186-ddba-4973-a3b4-d04c73db7aa5",
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "716908e7-b740-431f-9152-0e90ab64d9b0" in self._flagged_ids(payload)

    async def test_never_enqueued_row_is_still_flagged(self, fleet_db):
        """The PRE-FIX shape (``last_activity_at`` NULL) is unchanged."""
        _insert(fleet_db, [_instance("never-enqueued-1", created_at=_aged())])
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "never-enqueued-1" in self._flagged_ids(payload)


class TestSpawnStampDoesNotWidenTheEvidence:
    """The widened conjunct must NOT trade evidence for coverage."""

    async def _health(self, manager):
        return await call(_read_tools(manager), "instance_health")

    def _flagged_ids(self, payload) -> set[str]:
        for detector in payload["detectors"]:
            if detector["detector"] == "never-dispatched-husk":
                return {row["instance_id"] for row in detector["instances"]}
        raise AssertionError("never-dispatched-husk detector is missing")

    async def test_spawn_stamped_row_with_a_task_row_is_not_flagged(self, fleet_db):
        """E1 still clears a spawn-stamped row."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "spawn-stamped-tasked",
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        with Session(fleet_db) as session:
            session.add(
                Task(instance_id="spawn-stamped-tasked", task_type="PROCESS_MESSAGE")
            )
            session.commit()
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "spawn-stamped-tasked" not in self._flagged_ids(payload)

    async def test_spawn_stamped_row_with_a_message_row_is_not_flagged(
        self, fleet_db
    ):
        """E2 still clears a spawn-stamped row."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "spawn-stamped-messaged",
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        with Session(fleet_db) as session:
            session.add(
                MessageQueue(
                    message_id="m-spawn-stamped",
                    instance_id="spawn-stamped-messaged",
                    content="hello",
                    role="user",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.commit()
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "spawn-stamped-messaged" not in self._flagged_ids(payload)

    async def test_spawn_stamped_row_with_a_job_item_is_not_flagged(self, fleet_db):
        """E3 still clears a spawn-stamped row (any admission state)."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "spawn-stamped-jobbed",
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        with Session(fleet_db) as session:
            session.add(
                JobItem(
                    agent_id="worker",
                    agent_dir="/tmp/agents/worker",
                    message="dispatch",
                    instance_id="spawn-stamped-jobbed",
                    job_type="task",
                    admission_state="dead",
                    deleted_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.commit()
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "spawn-stamped-jobbed" not in self._flagged_ids(payload)

    async def test_idle_but_previously_dispatched_row_is_not_flagged(
        self, fleet_db
    ):
        """THE MERELY-IDLE CASE (evidence still on file).

        An idle instance that WAS dispatched carries its evidence rows
        — a task row here — so the evidence legs clear it.
        """
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "idle-but-dispatched",
                    status="idle",
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        with Session(fleet_db) as session:
            session.add(
                Task(
                    instance_id="idle-but-dispatched",
                    task_type="PROCESS_MESSAGE",
                )
            )
            session.commit()
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "idle-but-dispatched" not in self._flagged_ids(payload)

    async def test_idle_but_previously_dispatched_row_with_a_later_stamp_is_not_flagged(
        self, fleet_db
    ):
        """THE MERELY-IDLE CASE (evidence aged out) — THE TIGHTNESS PIN.

        An idle instance that was dispatched LATER than the spawn window
        keeps a LATER activity stamp. Even with every evidence row gone
        (they age out: ``message_queue.cleanup_old`` deletes completed
        messages after 24h), the widened conjunct must NOT admit it:
        the window is measured from ``created_at``, which never moves,
        so the class is ONE-SHOT.
        """
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "idle-dispatched-later",
                    status="idle",
                    created_at=created,
                    last_activity_at=created + timedelta(hours=3),
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "idle-dispatched-later" not in self._flagged_ids(payload)

    async def test_stamp_just_outside_the_window_is_not_flagged(self, fleet_db):
        """One second past the window — the window is a hard edge."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "just-outside",
                    created_at=created,
                    last_activity_at=created
                    + timedelta(seconds=ir.SPAWN_STAMP_WINDOW_SECONDS + 1),
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "just-outside" not in self._flagged_ids(payload)

    async def test_stamp_exactly_at_the_window_edge_is_flagged(self, fleet_db):
        """The window edge is INCLUSIVE."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "at-the-edge",
                    created_at=created,
                    last_activity_at=created
                    + timedelta(seconds=ir.SPAWN_STAMP_WINDOW_SECONDS),
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "at-the-edge" in self._flagged_ids(payload)

    async def test_stamp_that_predates_the_creation_is_not_flagged(self, fleet_db):
        """A stamp BEFORE the creation is a clock-skew artefact, not a
        spawn stamp — the conjunct must not widen on it."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "skewed",
                    created_at=created,
                    last_activity_at=created - timedelta(hours=1),
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "skewed" not in self._flagged_ids(payload)

    async def test_fresh_spawn_stamped_row_is_not_flagged(self, fleet_db):
        """The age leg still governs a spawn-stamped row."""
        now = datetime.now(timezone.utc)
        _insert(
            fleet_db,
            [
                _instance(
                    "fresh-spawn-stamped",
                    created_at=now,
                    last_activity_at=now + LIVE_SPAWN_STAMP,
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "fresh-spawn-stamped" not in self._flagged_ids(payload)

    async def test_terminal_spawn_stamped_row_is_not_flagged(self, fleet_db):
        """The non-terminal leg still governs a spawn-stamped row."""
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "terminal-spawn-stamped",
                    status="completed",
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "terminal-spawn-stamped" not in self._flagged_ids(payload)


class TestSpawnStampConjunctUnit:
    """The conjunct itself — the exact shape predicate."""

    def _info(self, *, created, activity):
        return {"created_at": created, "last_activity_at": activity}

    def test_null_activity_is_a_spawn_stamp(self):
        assert ir._activity_is_spawn_stamp_only(
            self._info(
                created=datetime.now(timezone.utc).isoformat(), activity=None
            )
        )

    def test_the_live_proof_distance_is_a_spawn_stamp(self):
        created = datetime(2026, 11, 23, 14, 51, 31, 148105, tzinfo=timezone.utc)
        assert ir._activity_is_spawn_stamp_only(
            self._info(
                created=created.isoformat(),
                activity=(created + LIVE_SPAWN_STAMP).isoformat(),
            )
        )

    def test_a_later_stamp_is_not_a_spawn_stamp(self):
        created = datetime.now(timezone.utc)
        assert not ir._activity_is_spawn_stamp_only(
            self._info(
                created=created.isoformat(),
                activity=(created + timedelta(hours=1)).isoformat(),
            )
        )

    def test_a_pre_creation_stamp_is_not_a_spawn_stamp(self):
        created = datetime.now(timezone.utc)
        assert not ir._activity_is_spawn_stamp_only(
            self._info(
                created=created.isoformat(),
                activity=(created - timedelta(seconds=1)).isoformat(),
            )
        )

    def test_an_unreadable_activity_is_not_a_spawn_stamp(self):
        """Fail-closed: a value the scanner could not parse never
        widens the conjunct."""
        created = datetime.now(timezone.utc)
        assert not ir._activity_is_spawn_stamp_only(
            self._info(created=created.isoformat(), activity="not-a-timestamp")
        )

    def test_an_unreadable_creation_is_not_a_spawn_stamp(self):
        assert not ir._activity_is_spawn_stamp_only(
            self._info(
                created="not-a-timestamp",
                activity=datetime.now(timezone.utc).isoformat(),
            )
        )

    def test_a_naive_stamp_is_read_as_utc(self):
        """The column is tz-naive UTC; a naive ISO string must be read
        in the SAME frame as the offset-carrying ``created_at``."""
        created = datetime(2026, 11, 23, 14, 51, 31, 148105, tzinfo=timezone.utc)
        naive = (created + LIVE_SPAWN_STAMP).replace(tzinfo=None)
        assert ir._activity_is_spawn_stamp_only(
            self._info(created=created.isoformat(), activity=naive.isoformat())
        )


class TestDisagreementPinTruthfulness:
    """The payload must describe what the detector ACTUALLY does."""

    async def test_the_pin_states_the_spawn_stamp_conjunct(self, fleet_db):
        created = _aged()
        _insert(
            fleet_db,
            [
                _instance(
                    "pin-1",
                    created_at=created,
                    last_activity_at=created + LIVE_SPAWN_STAMP,
                )
            ],
        )
        manager = _RepoBackedManager(fleet_db)

        payload = await call(_read_tools(manager), "instance_health")

        pin = next(
            d["disagreement_pin"]
            for d in payload["detectors"]
            if d["detector"] == "never-dispatched-husk"
        )
        # The canonical twins + the evidence-only containment.
        assert "filter_never_dispatched_ids" in pin
        assert "_ghost_child_filter" in pin
        assert "never_dispatched_evidence_expr" in pin
        assert "EVIDENCE-CONJUNCT CONTAINMENT ONLY" in pin
        assert "NOT a subset" in pin
        # The ACTIVITY conjunct must be named truthfully — the pre-fix
        # text ("last_activity_at IS NULL" as the ONLY shape) would be
        # a lie once the spawn-stamp branch exists.
        assert "spawn-stamp-only activity" in pin
        assert "SPAWN_STAMP_WINDOW_SECONDS" in pin
