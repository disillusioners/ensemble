"""RED PINS — auxiliary pin file for commission ``scheduler-deferred-alarm-delivery``.

PLAN HOME
=========
``.agents/shared/planning/scheduler-deferred-alarm-delivery/plan-overview.md``

* §(d) traceability rows **D4** (visibility fields) and **D5** (no migration /
  additive only);
* §(e) "Extra pins (same house shape)" — the two ``TestDeferredVisibility``
  pins;
* §(e) "PIN INVENTORY" → the **Auxiliary (3)** line, which named three
  auxiliary pins.  This file now holds the first two:

    1. ``TestDeferredVisibility::test_schedule_detail_exposes_deferred_count_and_last_execution_status`` (D4)
    2. ``TestDeferredVisibility::test_no_migration_file_and_deferred_status_is_text`` (D5)

ADJ-1 (leader ruling) — the third auxiliary pin,
``TestPausedCaller::test_paused_caller_is_not_blocking_and_receives_the_alarm``
(D6), was DELETED from this file: the DELIVERY file's pin 13
(``tests/unit/test_scheduler_deferred_delivery_pin.py``) is CANONICAL for
it (mandate + plan §(d) home), and its substance is already pinned there —
the busy-set exact-set sweep by delivery pin 8 (``TestBusyPredicate``) and
the pause-claim substance by delivery pin 13.  The plan's "Auxiliary (3)"
label is reconciled by the leader at the next legitimate plan touch; the
plan documents are FROZEN and were not edited here.

The file holds EXACTLY those two test functions — the plan's inventory
counts pins, not assertions, and a third function here would duplicate
the canonical D6 pin.

SUBSTRATE / LINEAGE
====================
Hermetic **file-backed SQLite** under ``tmp_path`` (real engine, real rows,
``NullPool``) — the shape of the commission's mechanism probe
``tests/unit/test_scheduler_deferred_mechanism_probe.py`` (1501L), whose
fixture / fence / teardown patterns are COPIED here (engine creation, the
seeding helpers, the source-row shape).  The probe's **PostgreSQL branch is
REMOVED**: this file never opens a PostgreSQL connection, so the probe's
``ensemble_dev`` fence is never touched and no live database is reachable
from here.  No daemon boot, no port binding, no network.

FENCES
======
* no ``ensemble_prod``, no live install, no port 9797 (nothing but a
  ``tmp_path`` SQLite file is opened);
* no ``agents/`` writes, no ``daemon/migrations/`` writes — D5 *asserts*
  that no migration is needed, and this file must not create one either;
* the frozen plan documents are READ-ONLY (an approver is reading them);
* this file is the only artefact this pin-authoring pass produces.

RED STATE
=========
RED is the EXPECTED state for BOTH pins in this file (the base code has no
``deferred_count`` / ``last_execution_status`` surface and no
``ExecutionStatus.DEFERRED`` member).  Each pin says so in its own
docstring — including the D5 split, whose legs 3-6 are absence assertions
(trivially GREEN at base by construction) while its legs 1-2 carry the
RED-at-base content.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401  (register tables)
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.source.models import (
    ExecutionStatus,
    ScheduleExecution,
    SourceConfig as SourceConfigRow,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Namespace for every row this file creates.
PIN_TAG = "pin_self_alarm_deferred_visibility"

#: The commission's base commit (the tree the pins are written against).
#: Used ONLY to compare the migration file SET (D5) — never to mutate git.
BASE_COMMIT = "8e6df58e445cd56b5e775dacebe0ef53f0add0b4"

#: The two visibility fields D4 requires on every schedule-detail surface.
REQUIRED_VISIBILITY_KEYS = ("deferred_count", "last_execution_status")

#: ``CREATE TYPE <name>`` — a native database enum type (D5 forbids one
#: for the execution status).
_CREATE_TYPE_RE = re.compile(r"create\s+type\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)

#: An ALTER of the ``schedule_executions`` TABLE that touches its
#: ``status`` column.  The word boundary after ``schedule_executions``
#: keeps a rename of ``schedule_executions_new`` (the pre-existing
#: 2026-04-02 session→instance migration) out of the match.
_ALTER_STATUS_RE = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?schedule_executions\b[^;]*\bstatus\b[^;]*;",
    re.IGNORECASE,
)


def _repo_root() -> Path:
    """Repository root of the WORKTREE under test."""
    return Path(__file__).resolve().parents[2]


def _migrations_dir() -> Path:
    return _repo_root() / "daemon" / "migrations" / "versions"


# ---------------------------------------------------------------------------
# Fixtures — hermetic SQLite (probe lineage, PG branch removed)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_engine(tmp_path) -> Engine:
    """A REAL file-backed SQLite database under ``tmp_path``.

    Copied from the probe's SQLite fallback branch
    (``tests/unit/test_scheduler_deferred_mechanism_probe.py``) — file DB +
    ``NullPool`` (never ``StaticPool``/``:memory:``), real schema, real rows.
    The probe's PostgreSQL branch is deliberately NOT copied: this file
    never opens a PostgreSQL connection.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path / 'self_alarm_deferred_visibility_pin.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def source_repo(sqlite_engine: Engine):
    """The REAL source repository (same class the daemon uses)."""
    from daemon.repositories.source.repository import SQLModelSourceRepository

    return SQLModelSourceRepository(sqlite_engine)


@pytest.fixture
def instance_repo(sqlite_engine: Engine):
    """The REAL instance repository (same class the daemon uses)."""
    from daemon.repositories.instance.repository import SQLModelInstanceRepository

    return SQLModelInstanceRepository(sqlite_engine)


@pytest.fixture
def mounted_service(source_repo):
    """Mount the real repo into the REAL scheduling service; restore after.

    ``scheduling_service._deps`` is the daemon's own dependency holder
    (wired by ``InstanceManager.scheduling_service``); the house pack
    ``tests/unit/tools/test_self_alarm_tools.py`` saves/restores the very
    same attributes — same pattern, same reason (a test must not leak a
    repo into the next test in the session).
    """
    from daemon.services import scheduling_service

    previous_repo = scheduling_service._deps.source_repo
    previous_registry = scheduling_service._deps.source_registry
    scheduling_service.configure(source_repo=source_repo)
    try:
        yield scheduling_service
    finally:
        scheduling_service._deps.source_repo = previous_repo
        scheduling_service._deps.source_registry = previous_registry


class _FakeAdapter:
    """A stand-in for the scheduler adapter (registry double payload)."""

    source_id = "src-pin"

    async def stop(self) -> None:
        return None


class _FakeRegistry:
    """A registry double (house shape — ``tests/unit/tools/test_self_alarm_tools.py``).

    The real registry starts the adapter and writes ``running`` onto the
    row; the double only has to satisfy ``_rebuild_register_start``'s
    three calls (``_create_adapter_from_config`` / ``register`` /
    ``start_adapter``).
    """

    def __init__(self, repo=None) -> None:
        self.repo = repo
        self.registered: list = []

    def get(self, source_id):
        return None

    def unregister(self, source_id) -> None:
        return None

    async def _create_adapter_from_config(self, row):
        return _FakeAdapter()

    def register(self, adapter) -> None:
        self.registered.append(adapter)

    async def start_adapter(self, source_id):
        return True


@pytest.fixture
def mounted_registry(source_repo):
    """Mount the registry double into the real scheduling service."""
    from daemon.services import scheduling_service

    previous = scheduling_service._deps.source_registry
    scheduling_service.configure(source_registry=_FakeRegistry(source_repo))
    try:
        yield scheduling_service._deps.source_registry
    finally:
        scheduling_service._deps.source_registry = previous


# ---------------------------------------------------------------------------
# Seeding helpers (probe lineage)
# ---------------------------------------------------------------------------


def _seed_project(eng: Engine, *, project_id: str) -> None:
    """Seed the project row the instance references."""
    from daemon.repositories.project.models import Project, ProjectStatus

    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as session:
        session.add(
            Project(
                project_id=project_id,
                name=PIN_TAG,
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="self_alarm deferred visibility pin",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_instance(
    eng: Engine,
    *,
    instance_id: str,
    project_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> Instance:
    """Seed an ``instances`` row (probe lineage)."""
    inst = Instance(
        instance_id=instance_id,
        agent_id="ari",
        agent_dir="/agents/ari",
        project_id=project_id,
        status=status,
        version=1,
        instance_metadata={},
    )
    with Session(eng) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _seed_scheduler_row(
    eng: Engine,
    *,
    source_id: str,
    name: str,
    config: dict,
    status: str = "running",
) -> SourceConfigRow:
    """Seed a ``source_configs`` scheduler row (probe lineage).

    Row shape mirrors a real self-alarm row: ``source_type='scheduler'``
    and a ``self-alarm:<label>`` name.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=source_id,
                source_type="scheduler",
                name=name,
                config=config,
                credentials=None,
                enabled=True,
                autostart=True,
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()
    return SourceConfigRow(
        source_id=source_id,
        source_type="scheduler",
        name=name,
        config=config,
        status=status,
        enabled=True,
    )


def _seed_execution(
    eng: Engine,
    *,
    schedule_id: str,
    status: str,
    triggered_at: datetime,
    execution_id: str | None = None,
) -> ScheduleExecution:
    """Seed a ``schedule_executions`` row (the audit trail the D4 field reads)."""
    row = ScheduleExecution(
        execution_id=execution_id or str(uuid.uuid4()),
        schedule_id=schedule_id,
        triggered_at=triggered_at.isoformat(),
        instance_id=None,
        status=status,
        error_message=None,
        completed_at=triggered_at.isoformat(),
    )
    with Session(eng) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


# ===========================================================================
# D4 + D5 — the visibility surface
# ===========================================================================


class TestDeferredVisibility:
    """D4 (visibility fields) + D5 (additive only, no migration)."""

    async def test_schedule_detail_exposes_deferred_count_and_last_execution_status(
        self,
        sqlite_engine: Engine,
        source_repo,
        instance_repo,
        mounted_service,
        mounted_registry,
    ) -> None:
        """D4 — the schedule-detail surface exposes the deferral visibility.

        DEFECT (the pin's named defect)
        --------------------------------
        A busy scheduled fire is **dropped** today with a zero-trace
        residue: the drop is indistinguishable from a successful delivery
        to the operator.  ``ScheduleDetail`` carries no execution-status
        field (``last_run_at`` reads ``triggered_at``) and no deferral
        count, so the fix would be invisible to the very operator it
        serves.  The fix adds TWO visibility fields — ``deferred_count``
        and ``last_execution_status`` — to the schedule-detail surface
        (``daemon/services/scheduling_service.py``) and to the self-alarm
        tool's arm response (``daemon/tools/self_alarm.py``).

        WHAT THIS PIN ASSERTS (observable output, EXISTING entry points)
        ----------------------------------------------------------------
        1. **The service detail path** — ``scheduling_service.get_schedule()``
           (the same entry point the REST router and the agent tools use)
           returns a ``ScheduleDetail`` whose serialized surface carries
           BOTH fields, with the plan's values:
           * ``deferred_count`` == the schedule row's ``config['_defer_count']``
             (plan §(c) C4: "``deferred_count`` from ``config['_defer_count']``");
           * ``last_execution_status`` == the LATEST ``schedule_executions``
             row's status (plan §(c) C4 / DEC-12: "``get_latest_execution(
             schedule_id).status``").  TWO execution rows are seeded — an
             older ``completed`` and a newer ``deferred`` — so an
             implementation that reads the wrong row fails here.
        2. **The self-alarm arm response** — the agent-facing
           ``self_alarm`` tool's response dict carries the same two keys
           (plan §(c) C4: "``_create_arm`` … / ``_reuse_alarm`` … response
           dicts → ``+ 'deferred_count'``, ``+ 'last_execution_status'``").

        MUTATION SPEC (verbatim from plan §(e), "Extra pins (same house
        shape)": ``(mutate: drop the two fields from `_row_to_detail` → RED)``).

        RED at base: ``ScheduleDetail`` has neither field, so the
        serialized detail surface has no ``deferred_count`` and no
        ``last_execution_status``, and the arm response dict has neither
        key.  The assertion below reports EVERY missing piece in ONE
        clean failure (no AttributeError, no fixture crash).
        """
        source_id = str(uuid.uuid4())
        label = f"self-alarm:{PIN_TAG}"
        defer_count = 3

        _seed_scheduler_row(
            sqlite_engine,
            source_id=source_id,
            name=label,
            config={
                "recurrence": "cron",
                "schedule": "0 9 * * *",
                "timezone": "UTC",
                "instance_mode": "reuse_instance",
                "agent": "ari",
                "message": "pin alarm payload",
                # The plan's storage for the counter: a JSONB key on the
                # schedule row (DEC-12: `_defer_count`, `_defer_gen`,
                # `_defer_message_id`, `_defer_since`).
                "_defer_count": defer_count,
            },
        )

        now = datetime.now(timezone.utc)
        _seed_execution(
            sqlite_engine,
            schedule_id=source_id,
            status=ExecutionStatus.COMPLETED.value,
            triggered_at=now - timedelta(minutes=10),
            execution_id=str(uuid.uuid4()),
        )
        _seed_execution(
            sqlite_engine,
            schedule_id=source_id,
            status="deferred",
            triggered_at=now,
            execution_id=str(uuid.uuid4()),
        )

        problems: list[str] = []

        # ---- (1) the service detail path --------------------------------
        detail = await mounted_service.get_schedule(source_id)
        if detail is None:
            problems.append(
                "scheduling_service.get_schedule() returned None for the seeded "
                "scheduler row — the detail path is not answering"
            )
        else:
            payload = detail.model_dump()
            if "deferred_count" not in payload:
                problems.append(
                    "the schedule-detail surface exposes no 'deferred_count' "
                    f"(visible keys: {sorted(payload)})"
                )
            elif payload["deferred_count"] != defer_count:
                problems.append(
                    "the schedule-detail surface reports deferred_count="
                    f"{payload['deferred_count']!r}, expected {defer_count!r} "
                    "(the schedule row's config['_defer_count'])"
                )
            if "last_execution_status" not in payload:
                problems.append(
                    "the schedule-detail surface exposes no 'last_execution_status' "
                    f"(visible keys: {sorted(payload)})"
                )
            elif payload["last_execution_status"] != "deferred":
                problems.append(
                    "the schedule-detail surface reports last_execution_status="
                    f"{payload['last_execution_status']!r}, expected 'deferred' "
                    "(the LATEST schedule_executions row)"
                )

        # ---- (2) the self-alarm arm response ----------------------------
        project_id = f"proj-{PIN_TAG}"
        _seed_project(sqlite_engine, project_id=project_id)
        caller_instance_id = str(uuid.uuid4())
        _seed_instance(
            sqlite_engine,
            instance_id=caller_instance_id,
            project_id=project_id,
            status=InstanceStatus.RUNNING.value,
        )

        try:
            from daemon.tools.self_alarm import create_self_alarm_tools

            tools = create_self_alarm_tools(
                mounted_service,
                source_repo=source_repo,
                instance_repository=instance_repo,
                project_repository=None,
                current_instance_id=caller_instance_id,
                agent_id="ari",
            )
            by_name = {t.name: t for t in tools}
            arm_result = await by_name["self_alarm"].coroutine(
                label="deferred-visibility-pin",
                when_or_every="in 45m",
                message_to_self="Do the thing.",
            )
        except Exception as exc:  # noqa: BLE001 — a crash is a finding, not a crash
            arm_result = None
            problems.append(
                f"the self_alarm arm did not complete: {type(exc).__name__}: {exc}"
            )

        if isinstance(arm_result, dict):
            for key in REQUIRED_VISIBILITY_KEYS:
                if key not in arm_result:
                    problems.append(
                        f"the self_alarm arm response exposes no {key!r} "
                        f"(visible keys: {sorted(arm_result)})"
                    )
        elif arm_result is not None:
            problems.append(
                f"the self_alarm arm returned {type(arm_result).__name__}, "
                "not the response dict"
            )

        assert not problems, (
            "D4 — the deferral visibility surface is ABSENT: "
            + " | ".join(problems)
        )

    async def test_no_migration_file_and_deferred_status_is_text(
        self,
        sqlite_engine: Engine,
    ) -> None:
        """D5 — the fix is ADDITIVE ONLY: no migration, the status is TEXT.

        DEFECT (the pin's named defect)
        --------------------------------
        A scheduler-deferral fix that needs a schema change is a fix that
        breaks the rollback story: this commission's rollback is a SINGLE
        revert commit (plan §(f): "No migration → the rollback is a single
        revert commit"), because the new status is a pure-Python enum
        member on an existing TEXT column and the new bounds are
        pydantic-settings fields, not DDL.

        WHAT THIS PIN ASSERTS
        ----------------------
        RED legs (the additive-only surface must EXIST):
        1. ``ExecutionStatus`` carries a member whose value is
           ``"deferred"`` — a Python enum member over a TEXT column, NOT a
           database enum type (plan §(c) C4: "**NEW** ``ExecutionStatus.
           DEFERRED = 'deferred'`` — ``daemon/repositories/source/models.py``
           (TEXT column, NOT NULL only → **no migration**)"; DEC-12: "the
           enum member is pure Python, NO migration").
        2. ``SchedulingConfig`` carries the two bound knobs as
           pydantic-settings FIELDS — ``defer_max_count`` (default 16)
           and ``defer_max_age_seconds`` (default 86400) — i.e. env
           knobs, not DDL (plan §(c) C0; DEC-19: "Two **new** fields on
           ``SchedulingConfig`` … env prefix ``ENSEMBLE_SCHEDULING_``").

        Guard legs (the additive-only PROPERTY must hold):
        3. ``schedule_executions.status`` is still a string column — the
           ORM column type is a ``String``/``Text`` type, never a native
           database enum.
        4. The value ``"deferred"`` is storable in that column as-is
           (a TEXT value, no DDL needed) — proven by writing and reading
           back a row through the real ORM.
        5. NO migration file introduces or alters an execution-status type:
           no file under ``daemon/migrations/versions/`` may create a DB
           enum type for the execution status, nor ALTER the
           ``schedule_executions.status`` column.
        6. The migration file SET is byte-for-byte the commission's base
           set — no file was added, removed or renamed.

        MUTATION SPEC (plan §(e), "Extra pins (same house shape)" —
        "``test_no_migration_file_and_deferred_status_is_text`` (asserts
        ``daemon/migrations/versions/`` gains **no** file and
        ``schedule_executions.status`` stays TEXT)"; §(d) D5 row:
        "``source/models.py::ExecutionStatus`` ``+DEFERRED``;
        ``config.py::SchedulingConfig`` 2 fields; **no** file under
        ``daemon/migrations/versions/``").  The RED mutation this pin is
        built to catch is the one the plan forbids: **implementing D5 as
        DDL** — adding a ``daemon/migrations/versions/*.sql`` that creates
        a database enum type for the execution status (or ALTERs
        ``schedule_executions.status``) → leg 5/6 fires; and **dropping
        the additive-only surface** — removing ``ExecutionStatus.DEFERRED``
        or either ``SchedulingConfig`` knob → legs 1/2 fire.

        HONEST RED/green split (the D5 caution): legs 5 and 6 are
        ABSENCE assertions — they are GREEN at base BY CONSTRUCTION
        (the fix has not been written yet, so of course no migration
        exists) and they only ever fire when someone implements D5 as
        DDL.  Legs 1 and 2 are the RED-at-base content of this pin.
        """
        problems: list[str] = []

        # ---- (1) the new status is a pure-Python enum member -------------
        status_values = {member.value for member in ExecutionStatus}
        if "deferred" not in status_values:
            problems.append(
                "ExecutionStatus carries no 'deferred' member "
                f"(values: {sorted(status_values)}) — the new status must be "
                "a Python enum member over the existing TEXT column"
            )
        elif not ExecutionStatus.is_valid("deferred"):
            problems.append(
                "ExecutionStatus.is_valid('deferred') is False — the new "
                "status must be accepted by the write path's validity check"
            )

        # ---- (2) the two bounds are settings, not DDL --------------------
        from daemon.config import SchedulingConfig

        config_fields = SchedulingConfig.model_fields
        for field_name, expected_default in (
            ("defer_max_count", 16),
            ("defer_max_age_seconds", 86400),
        ):
            if field_name not in config_fields:
                problems.append(
                    f"SchedulingConfig has no {field_name!r} field — the two "
                    "bounds must be env-tunable settings, not DDL"
                )
            elif config_fields[field_name].default != expected_default:
                problems.append(
                    f"SchedulingConfig.{field_name} defaults to "
                    f"{config_fields[field_name].default!r}, expected "
                    f"{expected_default!r}"
                )
        env_prefix = SchedulingConfig.model_config.get("env_prefix")
        if env_prefix != "ENSEMBLE_SCHEDULING_":
            problems.append(
                "SchedulingConfig's env_prefix is "
                f"{env_prefix!r}, expected 'ENSEMBLE_SCHEDULING_'"
            )

        # ---- (3) the status column is still TEXT -------------------------
        status_column = ScheduleExecution.__table__.columns["status"]
        # SQLModel renders a plain ``str`` field as ``AutoString`` — a
        # TypeDecorator wrapping ``String``.  Unwrap one level so the
        # check sees the real column type.
        column_type = status_column.type
        column_impl = getattr(column_type, "impl", None)
        if column_impl is not None:
            column_type = column_impl
        if isinstance(column_type, sa.Enum):
            problems.append(
                "schedule_executions.status is a native database ENUM type "
                f"({column_type!r}) — the deferred status must stay a "
                "TEXT value (no DDL)"
            )
        elif not isinstance(column_type, (sa.String, sa.Text)):
            problems.append(
                "schedule_executions.status is not a string column "
                f"({column_type!r}) — it must stay TEXT"
            )

        # ---- (4) 'deferred' is storable as a TEXT value ------------------
        source_id = str(uuid.uuid4())
        _seed_scheduler_row(
            sqlite_engine,
            source_id=source_id,
            name=f"self-alarm:{PIN_TAG}-d5",
            config={"recurrence": "cron", "schedule": "0 9 * * *"},
        )
        try:
            stored = _seed_execution(
                sqlite_engine,
                schedule_id=source_id,
                status="deferred",
                triggered_at=datetime.now(timezone.utc),
            )
            if stored.status != "deferred":
                problems.append(
                    "a 'deferred' execution row did not round-trip as TEXT "
                    f"(read back {stored.status!r})"
                )
        except Exception as exc:  # noqa: BLE001 — a crash is a finding
            problems.append(
                f"writing a 'deferred' execution row failed: "
                f"{type(exc).__name__}: {exc}"
            )

        # ---- (5) no migration touches the execution-status type ----------
        migrations_dir = _migrations_dir()
        offenders: list[str] = []
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            text = sql_file.read_text(encoding="utf-8", errors="replace")
            # (a) a database enum type for the execution status — the
            #     "status is a TEXT value" claim, at the DDL level.
            for type_name in _CREATE_TYPE_RE.findall(text):
                lowered_name = type_name.lower()
                if "execution" in lowered_name or "status" in lowered_name:
                    offenders.append(f"{sql_file.name} (CREATE TYPE {type_name})")
            # (b) an ALTER of the schedule_executions TABLE that touches
            #     its status column.  The regex is deliberately anchored on
            #     ``schedule_executions`` followed by a word boundary, so a
            #     pre-existing rename of ``schedule_executions_new`` (the
            #     2026-04-02 session→instance migration) is NOT matched.
            if _ALTER_STATUS_RE.search(text):
                offenders.append(f"{sql_file.name} (ALTER schedule_executions ... status)")
        if offenders:
            problems.append(
                "migration file(s) create/alter an execution-status type: "
                f"{offenders} — D5 is additive-only (no migration)"
            )

        # ---- (6) the migration file SET is unchanged ---------------------
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(_repo_root()),
                    "ls-tree",
                    "-r",
                    "--name-only",
                    BASE_COMMIT,
                    "--",
                    "daemon/migrations/versions/",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            base_files = {
                line.split("/")[-1]
                for line in completed.stdout.splitlines()
                if line.strip().endswith(".sql")
            }
            now_files = {p.name for p in migrations_dir.glob("*.sql")}
            added = sorted(now_files - base_files)
            removed = sorted(base_files - now_files)
            if added or removed:
                problems.append(
                    "the migration file SET changed against the commission base "
                    f"commit — added={added} removed={removed} (D5: the "
                    "migration delta must stay EMPTY)"
                )
        except Exception as exc:  # noqa: BLE001 — a broken env is a finding
            problems.append(
                "could not compare the migration file set against the "
                f"commission base commit {BASE_COMMIT[:8]}: "
                f"{type(exc).__name__}: {exc}"
            )

        assert not problems, (
            "D5 — the additive-only contract is broken: " + " | ".join(problems)
        )

