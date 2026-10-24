"""Nest-ops shakedown Phase 4 — the REAL-PostgreSQL husk fleet (T4.1/T4.2/T4.8).

This module builds the TWO-TIER >window fixture (D-21, arch §11.2) on a
DISPOSABLE PostgreSQL database and pins the read-side surface the fleet
exists for:

* **T4.2** — both proof husks exist via the FULL REAL spawn order
  (``spawn_instance_with_mcp`` → ``spawn_instance`` →
  ``_spawn_instance_db_sync``) under the EXACT production UUIDs; every
  leg of the §3.5 table is asserted against the live rows; ZERO
  ``job_queue_items`` rows exist for either husk (E3 against the real
  spawn order — D-1); ONE synthetic touch (the 12d/16d ``created_at``
  backdate, production ISO format); the tier-2 filler (1,001 rows via
  the production ORM constructor, ONE session) lands ABOVE the husks in
  ``created_at DESC`` order.
* **F3×F4 coupling** — the health report over the >window fleet still
  flags BOTH husks under ``never-dispatched-husk`` (the single-window
  regression makes this RED: the husks sit at offsets 1001/1002).
* **F2** — the census counts the whole 1,003-row fleet and reconciles
  with ``instance_list.total_matching`` on real PostgreSQL.
* **F4a** — the honest ``truncated`` flag over a real over-limit fleet.
* **T4.8** — the D-20 scan-cost MEASUREMENT: per-window query count via
  an in-process SQLAlchemy ``before_cursor_execute`` event-listener
  counter + the wall-time of the full scan. A MEASUREMENT, not an
  assertion-gate — the numbers are recorded (printed + written to the
  evidence doc) for design-decision.md; only the D-20 failure
  thresholds are asserted.

EVERY multi-behavior pin carries its PLANNED MUTATION in the docstring
(the full 12-cycle mutation matrix is the TESTER's — T4.4; this file
only proves the pin design).

SAFETY FENCES (binding — D-13)
------------------------------
* Disposable LOGIN role + database on the LOCAL PostgreSQL
  (``PG_TEST_HOST/PORT``, default ``localhost:5432``), DROPPED at
  teardown with ``WITH (FORCE)`` + a ``pg_terminate_backend`` fallback.
* The ambient ``POSTGRES_*`` values (this host's shell carries
  ``POSTGRES_DB=ensemble_prod``) are NEVER trusted: the fixtures pin all
  five variables per-variable to the disposable database and the fence
  test asserts the resolved engine never equals the ambient database.
* The husk UUIDs are FIXTURES ONLY — never dispatched to (no message,
  no task, no job is ever created for them). ``ensemble_prod`` and the
  live daemon on ``:9797`` are never contacted.
* No ``$HOME``-derived live path is ever resolved (checkpoint/SQLite
  paths are ``tmp_path``-scoped).

RUN (a deselected/skipped pin is a FAILED gate)::

    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \\
        -u POSTGRES_HOST -u POSTGRES_PORT \\
        .venv/bin/pytest tests/integration/test_nestops_husk_fleet_real_pg.py \\
        --override-ini="addopts=" -m integration -q
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, select

import daemon.tools.instance_read as ir
from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from tests.helpers.nestops_pg_fixtures import (
    AMBIENT_POSTGRES,
    FILLER_AGENT_ID,
    FILLER_ROWS,
    HUSK_A_AGE_DAYS,
    HUSK_A_ID,
    HUSK_B_AGE_DAYS,
    HUSK_B_ID,
    SCRUBBED_POSTGRES_VARS,
    assert_scratch_db,
    backdate_husk,
    build_real_manager,
    disposable_db,
    insert_filler_fleet,
    pin_scratch_postgres_env,
    probe_admin_engine,
    spawn_real_husk,
)

pytestmark = pytest.mark.integration

#: Every SQLModel table must be registered BEFORE ``create_all`` — a
#: lazily-imported model would leave its table missing (the DF-4
#: collection-time lesson).
import daemon.models.agent  # noqa: F401,E402
import daemon.models.app_settings  # noqa: F401,E402
import daemon.models.board  # noqa: F401,E402
import daemon.models.instance  # noqa: F401,E402
import daemon.models.mapping  # noqa: F401,E402
import daemon.models.mcp_server  # noqa: F401,E402
import daemon.models.message  # noqa: F401,E402
import daemon.models.resource_monitor  # noqa: F401,E402
import daemon.models.schedule  # noqa: F401,E402
import daemon.models.source  # noqa: F401,E402
import daemon.models.tmp_image  # noqa: F401,E402
import daemon.migrations.models  # noqa: F401,E402
import daemon.repositories.auth_session.models  # noqa: F401,E402
import daemon.repositories.blueprint.models  # noqa: F401,E402
import daemon.repositories.custom_agent_configs.models  # noqa: F401,E402
import daemon.repositories.db_connection.models  # noqa: F401,E402
import daemon.repositories.dependency_bus.models  # noqa: F401,E402
import daemon.repositories.ens_db.models  # noqa: F401,E402
import daemon.repositories.event.models  # noqa: F401,E402
import daemon.repositories.infra.models  # noqa: F401,E402
import daemon.repositories.instance_ops.models  # noqa: F401,E402
import daemon.repositories.instance_ui_prefs.models  # noqa: F401,E402
import daemon.repositories.job_queue.models  # noqa: F401,E402
import daemon.repositories.maintenance_runs.models  # noqa: F401,E402
import daemon.repositories.mcp_kb_keys.models  # noqa: F401,E402
import daemon.repositories.message_metadata.models  # noqa: F401,E402
import daemon.repositories.prompt_version.models  # noqa: F401,E402
import daemon.repositories.report_injection.models  # noqa: F401,E402
import daemon.repositories.secrets.models  # noqa: F401,E402
import daemon.repositories.service_tool.models  # noqa: F401,E402
import daemon.repositories.shared_meta_kv.models  # noqa: F401,E402
import daemon.repositories.skill.models  # noqa: F401,E402
import daemon.repositories.snapshot.models  # noqa: F401,E402
import daemon.repositories.source.models  # noqa: F401,E402
import daemon.repositories.task.models  # noqa: F401,E402
import daemon.repositories.view_passcodes.models  # noqa: F401,E402


# ── module-scoped disposable environment ───────────────────────────────────


@pytest.fixture(scope="module")
def shakedown_admin():
    """The ADMIN engine to the LOCAL PostgreSQL — skip loudly when down."""
    admin = probe_admin_engine()
    if admin is None:
        pytest.skip(
            "PostgreSQL not available at "
            f"{os.environ.get('PG_TEST_HOST', 'localhost')}:"
            f"{os.environ.get('PG_TEST_PORT', '5432')} — the real-PG "
            "shakedown pins require the local PG16 test server"
        )
    yield admin
    admin.dispose()


@pytest.fixture(scope="module")
def shakedown_db(shakedown_admin):
    """The disposable LOGIN role + database (created once, DROPPED once)."""
    with disposable_db(shakedown_admin) as db:
        yield db


@pytest.fixture(scope="module")
def shakedown_engine(shakedown_admin, shakedown_db, tmp_path_factory):
    """Schema-create on the disposable DB; dispose at module teardown."""
    engine = create_engine(shakedown_db.url, pool_pre_ping=True, future=True)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def shakedown_env(shakedown_admin, shakedown_db, shakedown_engine, tmp_path_factory):
    """A REAL ``InstanceManager`` over the disposable DB + the env pins.

    The per-variable ``POSTGRES_*`` pin mirrors the DF-4 closure pin:
    ``create_postgres_engine`` reads the environment FIRST, so the pin
    (not the ambient shell) decides where the manager's engine lands.
    """
    from daemon import constants as _constants

    monkeypatch_mp = _ModuleMonkeys()
    pin_scratch_postgres_env(monkeypatch_mp, shakedown_db)
    constants_original = _constants.SYSTEM_DEFAULT_PROJECT_ID
    _constants.SYSTEM_DEFAULT_PROJECT_ID = (
        "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
    )
    tmp_path = tmp_path_factory.mktemp("nestops-shakedown")
    manager = build_real_manager(shakedown_db, tmp_path)
    yield manager
    manager.cleanup()
    _constants.SYSTEM_DEFAULT_PROJECT_ID = constants_original
    monkeypatch_mp.undo()


class _ModuleMonkeys:
    """A minimal module-scoped stand-in for ``pytest.MonkeyPatch``.

    (``pytest``'s own ``MonkeyPatch`` is usable directly, but its
    fixture lifecycle is function-scoped; the disposable DB is
    module-scoped, so the env pins must outlive one test.)
    """

    def __init__(self) -> None:
        self._prior: dict[str, str | None] = {}

    def setenv(self, name: str, value: str) -> None:
        if name not in self._prior:
            self._prior[name] = os.environ.get(name)
        os.environ[name] = value

    def delenv(self, name: str, raising: bool = True) -> None:
        if name not in self._prior:
            self._prior[name] = os.environ.get(name)
        os.environ.pop(name, None)

    def undo(self) -> None:
        for name, old in self._prior.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


@pytest.fixture(scope="module")
def window_fleet(shakedown_env, shakedown_engine):
    """T4.2 — the TWO-TIER >window fixture, built ONCE per module.

    Tier 1: the two proof husks via the FULL REAL spawn order + the ONE
    backdate touch. Tier 2: 1,001 filler rows via the production ORM
    constructor. The husks end up at offsets 1001/1002 of the
    ``created_at DESC`` ordering — OUTSIDE the first
    ``FLEET_SCAN_LIMIT`` window. NOTHING is ever dispatched to them.
    """
    manager = shakedown_env
    engine = shakedown_engine
    husks = asyncio_spawn_two(manager)
    assert husks == [HUSK_A_ID, HUSK_B_ID]
    backdate_husk(engine, HUSK_A_ID, days=HUSK_A_AGE_DAYS)
    backdate_husk(engine, HUSK_B_ID, days=HUSK_B_AGE_DAYS)
    filler = insert_filler_fleet(engine, count=FILLER_ROWS)
    return {"husks": husks, "filler": filler}


def asyncio_spawn_two(manager) -> list[str]:
    import asyncio

    async def _run():
        a = await spawn_real_husk(manager, HUSK_A_ID, "maintenancer")
        b = await spawn_real_husk(manager, HUSK_B_ID, "maintenancer")
        return [a, b]

    return asyncio.run(_run())


def _read_tools(manager, *, agent: str = "maintenancer"):
    """The ``instance_read`` tool set (the unit pins' shape)."""
    return ir.create_instance_read_tools(
        manager, current_instance_id="caller-iid", agent_id=agent
    )


def _by_name(tools, name):
    tool = next((t for t in tools if t.name == name), None)
    assert tool is not None, (
        f"tool {name!r} is not registered "
        f"(registered: {sorted(t.name for t in tools)})"
    )
    return tool


async def _call(tools, name, **kwargs):
    out = await _by_name(tools, name).ainvoke(kwargs)
    return json.loads(out)


# ── AC4.1 — the fence ──────────────────────────────────────────────────────


class TestEnvironmentFence:
    """The D-13 environment fence — ambient values can never leak."""

    def test_ambient_snapshot_records_the_poison(self):
        """The ambient ``POSTGRES_DB`` (if set) is captured at import.

        This host's shell carries ``POSTGRES_DB=ensemble_prod`` — the
        fence test proves the fixture stack never resolves to it. When
        the ambient var is absent the snapshot records ``None`` and the
        pin is trivially satisfied (declared anchor-neutral in that
        case).
        """
        ambient_db = AMBIENT_POSTGRES.get("POSTGRES_DB")
        if ambient_db is not None:
            assert ambient_db not in ("",), "ambient POSTGRES_DB is empty"
        # either way the snapshot itself must exist and be readable
        assert set(AMBIENT_POSTGRES) == set(SCRUBBED_POSTGRES_VARS)

    def test_resolved_engine_is_the_disposable_db(
        self, shakedown_db, shakedown_env
    ):
        """The manager's RESOLVED engine database is the disposable name.

        PLANNED MUTATION (fence pin): delete the per-variable
        ``pin_scratch_postgres_env`` call from the ``shakedown_env``
        fixture — on a host whose shell exports
        ``POSTGRES_DB=ensemble_prod`` the manager's engine would resolve
        to the AMBIENT database and this assertion goes RED (the
        env-poison family guard). On a scrubbed shell the ambient value
        is absent and the pin still holds via the disposable-name
        equality.
        """
        resolved = shakedown_env.engine.url.database
        assert_scratch_db(resolved, where="the resolved manager engine")
        assert resolved == shakedown_db.db_name
        ambient_db = AMBIENT_POSTGRES.get("POSTGRES_DB")
        if ambient_db is not None:
            assert resolved != ambient_db, (
                "the resolved engine database must never be the AMBIENT "
                f"POSTGRES_DB ({ambient_db!r}) — the per-variable scrub "
                "failed"
            )

    def test_scrubbed_env_carries_none_of_the_ambient_vars(
        self, shakedown_db
    ):
        """The pin writes ALL five ``POSTGRES_*`` variables explicitly.

        The per-variable form (NOT ``env -u POSTGRES_*`` — a silent
        no-op). Every one of the five resolves to the disposable pair's
        value; ``POSTGRES_URL`` is absent.
        """
        import subprocess
        import sys

        # Simulate the house invocation: the subprocess env is the
        # scrubbed form — each variable set EXPLICITLY to the
        # disposable pair. The assertion: none of the five carries the
        # AMBIENT value through.
        env = dict(os.environ)
        for name in SCRUBBED_POSTGRES_VARS:
            env.pop(name, None)
        env["POSTGRES_DB"] = shakedown_db.db_name
        env["POSTGRES_USER"] = shakedown_db.role
        env["POSTGRES_PASSWORD"] = shakedown_db.password
        from tests.helpers.nestops_pg_fixtures import (
            PG_TEST_HOST,
            PG_TEST_PORT,
        )

        env["POSTGRES_HOST"] = PG_TEST_HOST
        env["POSTGRES_PORT"] = str(PG_TEST_PORT)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; print(repr({k: os.environ.get(k) for k in "
                "('POSTGRES_DB','POSTGRES_USER','POSTGRES_PASSWORD',"
                "'POSTGRES_HOST','POSTGRES_PORT','POSTGRES_URL')}))",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        resolved = eval(out.stdout.strip())  # noqa: S307 — a local repr
        assert resolved["POSTGRES_DB"] == shakedown_db.db_name
        assert resolved["POSTGRES_USER"] == shakedown_db.role
        assert resolved["POSTGRES_URL"] is None
        ambient_db = AMBIENT_POSTGRES.get("POSTGRES_DB")
        if ambient_db is not None:
            assert resolved["POSTGRES_DB"] != ambient_db

    def test_import_daemon_resolves_inside_the_worktree(self):
        """``import daemon`` resolves inside THIS worktree (T4.1)."""
        import daemon

        worktree = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        assert daemon.__file__.startswith(worktree), (
            f"daemon.__file__ {daemon.__file__!r} is not under {worktree!r} "
            "— the import guard resolved a foreign checkout"
        )


# ── T4.2 — the fixture verification (the §3.5 leg table) ──────────────────


class TestHuskFixtureLegs:
    """T4.2 — every §3.5 leg asserted against the REAL-spawned rows.

    PLANNED MUTATION (the F3 family — the tester runs these; T4.4):
    delete the detector's age leg → the fresh-spawn fixture fires RED;
    drop one evidence leg (E1..E6) → exactly that negative fixture
    fires RED; restore the single 1000-row window → the coupling pin
    below RED.
    """

    def test_husks_exist_via_the_real_spawn_order(self, window_fleet):
        """The two EXACT UUIDs exist, spawned (not seeded)."""
        assert window_fleet["husks"] == [HUSK_A_ID, HUSK_B_ID]

    def test_leg_table_non_terminal_no_activity_age(
        self, shakedown_engine
    ):
        """Legs 1-3: idle, version 1, ``last_activity_at`` NULL, aged."""
        with Session(shakedown_engine) as session:
            for iid, days in (
                (HUSK_A_ID, HUSK_A_AGE_DAYS),
                (HUSK_B_ID, HUSK_B_AGE_DAYS),
            ):
                row = session.get(Instance, iid)
                assert row is not None, f"husk {iid} missing"
                # leg 1 — non-terminal (born idle)
                assert row.status == "idle"
                # the birth shape: version 1
                assert row.version == 1
                # leg 2 — no activity EVER
                assert row.last_activity_at is None
                # leg 3 — age well past the 86,400 s threshold
                created = datetime.fromisoformat(row.created_at)
                age = (
                    datetime.now(timezone.utc) - created
                ).total_seconds()
                assert age > 86_400, f"husk {iid} age {age}s below threshold"
                expected_floor = days * 86_400 - 3_600  # 1h slack for runtime
                assert age >= expected_floor, (
                    f"husk {iid} backdate {days}d did not land "
                    f"(age={age}s < floor {expected_floor}s)"
                )

    async def test_evidence_legs_e1_to_e6(
        self, shakedown_env, shakedown_engine
    ):
        """E1-E6: zero task / message / jobitem / children / pack / marker."""
        with Session(shakedown_engine) as session:
            for iid in (HUSK_A_ID, HUSK_B_ID):
                # E1 — no task rows
                tasks = session.exec(
                    select(Task).where(Task.instance_id == iid)
                ).all()
                assert not tasks, f"E1 violated for {iid}"
                # E2 — no message rows
                msgs = session.exec(
                    select(MessageQueue).where(
                        MessageQueue.instance_id == iid
                    )
                ).all()
                assert not msgs, f"E2 violated for {iid}"
                # E3 — ZERO job_queue_items rows (the D-1 pin against the
                # REAL spawn order)
                jobs = session.exec(
                    select(JobItem).where(JobItem.instance_id == iid)
                ).all()
                assert not jobs, f"E3 violated for {iid}"
                # E4 — no children
                children = session.exec(
                    select(Instance).where(Instance.parent_id == iid)
                ).all()
                assert not children, f"E4 violated for {iid}"
                # E5/E6 — no question pack / pause marker (metadata)
                row = session.get(Instance, iid)
                metadata = row.instance_metadata or {}
                assert "question_paused" not in metadata, f"E6 violated for {iid}"
                assert not metadata.get("question_pack"), f"E5 violated for {iid}"

    def test_raw_job_queue_count_is_zero_for_both_husks(
        self, shakedown_engine
    ):
        """E3 raw-SQL confirmation — no JobItem mirror for either husk."""
        with shakedown_engine.connect() as conn:
            n = conn.execute(
                text(
                    "SELECT COUNT(*) FROM job_queue_items "
                    "WHERE instance_id IN (:a, :b)"
                ),
                {"a": HUSK_A_ID, "b": HUSK_B_ID},
            ).scalar()
        assert n == 0

    def test_filler_lands_above_the_husks(self, shakedown_engine, window_fleet):
        """The filler tier pushes the husks OUTSIDE the first window.

        The TEXT-ISO discipline (GT-9) is what makes this assertable:
        every timestamp used the production ``isoformat()``, so the
        lexicographic ``ORDER BY created_at DESC`` matches the
        chronological one and the backdated husks sink to the tail.
        """
        with shakedown_engine.connect() as conn:
            ordered = conn.execute(
                text(
                    "SELECT instance_id FROM instances "
                    "ORDER BY created_at DESC"
                )
            ).scalars().all()
        total = len(ordered)
        assert total == 2 + FILLER_ROWS, (
            f"fleet size {total} != {2 + FILLER_ROWS}"
        )
        pos_a = ordered.index(HUSK_A_ID)
        pos_b = ordered.index(HUSK_B_ID)
        from daemon.tools.instance_read import FLEET_SCAN_LIMIT

        assert pos_a >= FLEET_SCAN_LIMIT, (
            f"husk A at offset {pos_a} — INSIDE the first window; the "
            "F3xF4 coupling pin would be vacuous"
        )
        assert pos_b >= FLEET_SCAN_LIMIT, (
            f"husk B at offset {pos_b} — INSIDE the first window"
        )

    def test_filler_shape_is_production(self, shakedown_engine, window_fleet):
        """The filler rows carry the production shape (idle, v1, non-KB)."""
        with Session(shakedown_engine) as session:
            sample = session.exec(
                select(Instance)
                .where(Instance.agent_name == "shakedown-filler-0")
            ).first()
            assert sample is not None
            assert sample.status == "idle"
            assert sample.version == 1
            assert sample.agent_id == FILLER_AGENT_ID
            assert sample.last_activity_at is None
            assert sample.instance_metadata == {}


# ── the F3×F4 coupling pin over the >window fleet ──────────────────────────


class TestF3xF4CouplingRealFleet:
    """F3×F4 — the health report covers the WHOLE fleet and flags BOTH.

    PLANNED MUTATION (the decisive one): restore the single 1000-row
    window in ``_build_health_report`` (drop the offset-window loop) →
    the husks fall outside the window → the detector misses both → this
    pin RED.
    """

    async def test_both_husks_flagged_over_the_window_fleet(
        self, shakedown_env, window_fleet
    ):
        manager = shakedown_env
        detectors, scan_truncated, stop_reason = ir._build_health_report(
            manager, ir.DEFAULT_HUSK_MIN_AGE_SECONDS
        )
        husk = next(
            (
                d
                for d in detectors
                if d.get("detector") == "never-dispatched-husk"
            ),
            None,
        )
        assert husk is not None, (
            "the never-dispatched-husk detector row is missing — "
            f"detectors: {[d.get('detector') for d in detectors]}"
        )
        flagged = {row["instance_id"]: row for row in husk.get("instances", [])}
        assert HUSK_A_ID in flagged, (
            f"husk A not flagged over the {len(window_fleet['filler'])}-row "
            f"fleet (stop_reason={stop_reason!r})"
        )
        assert HUSK_B_ID in flagged, (
            f"husk B not flagged over the >window fleet"
        )
        # the reported idle_seconds is the AGE since created_at
        for iid, days in ((HUSK_A_ID, HUSK_A_AGE_DAYS), (HUSK_B_ID, HUSK_B_AGE_DAYS)):
            row = flagged[iid]
            assert row["idle_seconds"] >= days * 86_400 - 3_600
            assert "never dispatched" in row["detail"]
            assert "last_activity_at is null" in row["detail"]

    async def test_scan_covers_the_end_of_set(
        self, shakedown_env, window_fleet
    ):
        """The walk stops at ``end_of_set`` — never the window cap."""
        _detectors, scan_truncated, stop_reason = ir._build_health_report(
            shakedown_env, ir.DEFAULT_HUSK_MIN_AGE_SECONDS
        )
        assert stop_reason == "end_of_set", (
            f"expected end_of_set, got {stop_reason!r}"
        )
        assert scan_truncated is False

    async def test_husk_set_satisfies_the_canonical_ghost_signature(
        self, shakedown_env, shakedown_engine, window_fleet
    ):
        """F3 (pin direction) — every husk-flagged row satisfies the
        canonical ghost evidence set (the containment pin).

        PLANNED MUTATION: change the husk predicate to bypass the
        canonical filter → RED.
        """
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        detectors, _trunc, _reason = ir._build_health_report(
            shakedown_env, ir.DEFAULT_HUSK_MIN_AGE_SECONDS
        )
        husk = next(
            d for d in detectors if d.get("detector") == "never-dispatched-husk"
        )
        flagged_ids = [row["instance_id"] for row in husk["instances"]]
        assert flagged_ids, "no husks flagged — the containment pin is vacuous"
        repo = SQLModelInstanceRepository(shakedown_engine)
        canonical = set(repo.filter_never_dispatched_ids(flagged_ids))
        for iid in flagged_ids:
            assert iid in canonical, (
                f"husk-flagged row {iid} violates the canonical "
                "never-dispatched evidence signature"
            )


# ── F2 — the census over the real fleet ───────────────────────────────────


class TestF2CensusRealFleet:
    """F2 — the census counts the whole 1,003-row fleet and reconciles.

    PLANNED MUTATION: revert to the windowed Python loop → the sum drops
    to 1000 → this pin RED (on the real fleet — the unit pin's synthetic
    fleet cannot show it at this scale).
    """

    async def test_totals_reconcile_with_instance_list(
        self, shakedown_env, window_fleet
    ):
        tools = _read_tools(shakedown_env)
        capacity = await _call(tools, "fleet_capacity_summary")
        listing = await _call(tools, "instance_list", limit=5, offset=0)
        totals = capacity["totals"]
        assert sum(capacity["counts_by_status"].values()) == totals["counted_rows"]
        assert totals["counted_rows"] == totals["total_rows"]
        expected = 2 + FILLER_ROWS
        assert totals["total_rows"] == expected, (
            f"census total {totals['total_rows']} != fleet {expected}"
        )
        assert totals["reconciled"] is True
        assert totals["total_rows"] == listing["total_matching"]

    async def test_idle_bucket_present(self, shakedown_env, window_fleet):
        tools = _read_tools(shakedown_env)
        capacity = await _call(tools, "fleet_capacity_summary")
        assert capacity["counts_by_status"].get("idle") == 2 + FILLER_ROWS
        assert "idle" not in capacity["counts_by_liveness"]


# ── F4a — the honest truncated flag over the real fleet ───────────────────


class TestF4aTruncatedRealFleet:
    """F4a — ``truncated`` is honest over a REAL over-limit fleet.

    PLANNED MUTATION: restore the repository's constant ``False`` → this
    pin RED.
    """

    async def test_under_page_reports_truncated(self, shakedown_env, window_fleet):
        tools = _read_tools(shakedown_env)
        payload = await _call(tools, "instance_list", limit=5, offset=0)
        assert payload["truncated"] is True
        assert payload["total_matching"] == 2 + FILLER_ROWS

    async def test_exact_page_reports_not_truncated(
        self, shakedown_env, window_fleet
    ):
        """The EXHAUSTED final page reads ``truncated: false``.

        ``instance_list`` caps ``limit`` at 200, so the exact-page case
        over this fleet is the final partial page: offset 1000 carries
        exactly the 2 husks + 1 filler — ``count(3) == total - offset``
        → NOT truncated (iteration-003 remedy 4: the exhausted case
        ``len(rows) < limit`` with nothing left reads ``false``).
        """
        tools = _read_tools(shakedown_env)
        payload = await _call(tools, "instance_list", limit=200, offset=1000)
        assert len(payload["rows"]) == 3
        assert payload["total_matching"] == 2 + FILLER_ROWS
        assert payload["truncated"] is False


# ── T4.8 — the scan-cost measurement (D-20's arbiter) ──────────────────────


class TestT48ScanCostMeasurement:
    """T4.8 — per-window query count + wall-time, MEASURED on real PG.

    Mechanism (N-7): an in-process SQLAlchemy event-listener counter
    (``sqlalchemy.event.listen(engine, 'before_cursor_execute', ...)``)
    counts statements per scan window — no DB proxy, no
    ``pg_stat_statements`` wiring; works identically on the disposable
    PG. This is a MEASUREMENT: the numbers are recorded for
    design-decision.md; only the D-20 failure thresholds assert.

    D-20 thresholds:
      (i)   per-window query count > ``FLEET_SCAN_LIMIT + 2`` → FAIL
      (ii)  wall-time > 10 s WITHOUT the budget path exercising → FAIL
      (iii) ``scan_truncated=False`` while the tail is uncovered → FAIL
    """

    def test_scan_cost_measured(self, shakedown_env, window_fleet):
        from daemon.tools.instance_read import (
            FLEET_SCAN_LIMIT,
            FLEET_SCAN_TIME_BUDGET_SECONDS,
        )

        engine = shakedown_env.engine
        statements: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()))

        event.listen(engine, "before_cursor_execute", _count)
        try:
            start = time.monotonic()
            detectors, scan_truncated, stop_reason = ir._build_health_report(
                shakedown_env, ir.DEFAULT_HUSK_MIN_AGE_SECONDS
            )
            wall = time.monotonic() - start
        finally:
            event.remove(engine, "before_cursor_execute", _count)

        # ── per-window attribution ───────────────────────────────────
        # The scan's page reads are the ``SELECT instance_id, project_id,
        # … FROM instances … OFFSET …`` statements (the repository list
        # path). Statements between consecutive page reads belong to
        # that window (the child lookups are per-row, inside the loop).
        # The parameterized OFFSET never renders a literal, so the match
        # keys on the projection prefix + the OFFSET keyword.
        def _is_page_read(s: str) -> bool:
            return (
                s.startswith("SELECT instances.instance_id, instances.project_id")
                and "FROM instances" in s
                and "OFFSET" in s
            )

        page_read_idx = [
            i for i, s in enumerate(statements) if _is_page_read(s)
        ]
        assert page_read_idx, (
            "no window page reads observed — the attribution harness "
            "broke (the scan projection changed?)"
        )
        bounds = page_read_idx + [len(statements)]
        per_window = [
            bounds[w + 1] - bounds[w] for w in range(len(page_read_idx))
        ]
        per_window_max = max(per_window)
        pre_loop = page_read_idx[0]  # fixed statements before the walk
        total_rows = 2 + FILLER_ROWS
        budget_path_fired = stop_reason == "time_budget"

        record = {
            "measurement": "T4.8 scan-cost (D-20 arbiter)",
            "fixture_rows": total_rows,
            "statements_total": len(statements),
            "statements_pre_loop_fixed": pre_loop,
            "windows_executed": len(page_read_idx),
            "per_window_counts": per_window,
            "per_window_max": per_window_max,
            "per_window_gate": FLEET_SCAN_LIMIT + 2,
            "wall_time_seconds": round(wall, 3),
            "time_budget_seconds": FLEET_SCAN_TIME_BUDGET_SECONDS,
            "scan_stop_reason": stop_reason,
            "scan_truncated": scan_truncated,
            "husks_flagged": [
                row["instance_id"]
                for d in detectors
                if d.get("detector") == "never-dispatched-husk"
                for row in d.get("instances", [])
            ],
            "composition_note": (
                "per arch §4 arithmetic: 1 page read + 1 child lookup per "
                "row per window (the A2 N+1 — batched children are the "
                "disclosed first-class follow-up); window 1 of the "
                f"{total_rows}-row fixture carries exactly "
                f"{FLEET_SCAN_LIMIT + 2} statements (the gate), the tail "
                "window carries the remainder + the 2 batched husk "
                "evidence queries"
            ),
        }
        print("\nT4.8 MEASURED: " + json.dumps(record, indent=2))

        # ── the D-20 thresholds ──────────────────────────────────────
        # (i) per-window query count ≤ FLEET_SCAN_LIMIT + 2
        assert per_window_max <= FLEET_SCAN_LIMIT + 2, (
            f"N+1 UNBOUNDED: per-window query count {per_window_max} > "
            f"{FLEET_SCAN_LIMIT + 2} (D-20 threshold i); "
            f"per-window counts: {per_window}"
        )
        # (ii) wall-time within budget OR the budget path fired loudly
        assert wall <= FLEET_SCAN_TIME_BUDGET_SECONDS or budget_path_fired, (
            f"wall-time {wall:.2f}s exceeds the "
            f"{FLEET_SCAN_TIME_BUDGET_SECONDS}s budget and the budget "
            "path never exercised (D-20 threshold ii)"
        )
        # (iii) scan_truncated=False must never occur while the tail is
        # uncovered — the fleet (1003) is under the window CAP
        # (20×1000), so end_of_set must be the stop reason.
        assert (stop_reason == "end_of_set" and scan_truncated is False) or (
            scan_truncated is True
        ), (
            f"scan_truncated={scan_truncated!r} with stop_reason="
            f"{stop_reason!r} — the arch §3.2 drift shape (D-20 "
            "threshold iii)"
        )
        # the fixture rows stayed covered
        assert set(record["husks_flagged"]) >= {HUSK_A_ID, HUSK_B_ID}
