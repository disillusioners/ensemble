"""DF-4 pin (iii) — the sweep-forged wake is REFUSED, end-to-end, on REAL PostgreSQL.

Commission DF-4 (AGENT-ORIGIN provenance gate closure), phase P3.

THE MINIMAL-REAL SEAM (the plan's §"MINIMAL-REAL SEAM spec", F4)
----------------------------------------------------------------
Both house precedents for this area are FAKE harnesses — a ``MagicMock``
manager (``tests/job_queue/test_post_restart_arm_notify_routing.py``) and a
``FakeManager`` + hand-written window dicts
(``tests/unit/test_instance_ops_behaviour.py``). With a fake, the inherit +
gate legs pass VACUOUSLY — the exact failure mode this pin exists to
prevent. Therefore:

* NO ``MagicMock``, NO ``FakeManager``, NO hand-authored window dict.
* The manager is a REAL ``daemon.manager.InstanceManager`` over the REAL
  PostgreSQL engine (``tests/postgres/conftest.py::pg_engine``).
* The window stores read by the assertions are the REAL dicts the
  production code writes to (``manager._user_origin_windows`` /
  ``manager._inherited_provenance_windows``).
* The audit ledger is the REAL ``instance_ops_audit`` table on PostgreSQL.

THE CHAIN (in the REAL order — the defect lives in the ORDER, not in a
dict shape)
-----------------------------------------------------------------------
1. A REAL ``PendingWake`` armed at ``source=""`` (the no-user-origin
   sentinel) in a ``tmp_path`` journal.
2. The REAL ``UpgradeJournalSweepService.sweep_wake_records`` delivers it —
   the fallback arm is exercised by the production code, never hand-built.
3. The delivered source is read back from the REAL ``message_queue`` row.
4. The REAL dispatch funnel — ``InstanceManager._process_message_with_tracking``
   (``daemon/manager.py``) — runs. Its FIRST action is the stamp site
   (``manager.stamp_user_origin_window``), followed by the inherit site
   (``manager._inherit_provenance_window``); both must be observed through
   the REAL manager's own state.
5. A delegated child (``internal_agent:``) is dispatched through the same
   REAL funnel.
6. The REAL ``instance_terminate`` act runs against a REAL protected
   target row and the REAL audit ledger.

BODY ↔ HEADER STEP MAP: the body's numbered comments run 1, 2, 3, 4,
5a, 5b, 6, 7, 8 — body steps 5a/5b/6/7/8 correspond to header steps
4/4/5/6 (the body is finer-grained; the numbering is NOT renumbered).

ENV FENCING
-----------
The database fence runs at COLLECTION time — at MODULE IMPORT, which
PRECEDES the session-scoped ``pg_engine`` fixture's
``drop_all``/``create_all`` (a mis-pointed ``PG_TEST_DB`` would
otherwise have its tables dropped before any in-test assert could fire).
The effective ``PG_TEST_DB`` must be neither a live/dev/prod database
nor a non-scratch-shaped name. The RESOLVED engine database is asserted
AGAIN inside the test: the manager's engine is built by
``create_postgres_engine``, which reads ``POSTGRES_*`` FIRST, so the
resolved name gets the same forbidden + scratch-shape check and must
equal the scratch database the ``pg_engine`` fixture owns.
``POSTGRES_*`` is set explicitly to the SAME scratch database the
``pg_engine`` fixture owns — never inherited from the ambient
environment.

MANDATORY INVOCATION (a deselected/skipped pin is a FAILED gate)::

    pytest tests/postgres/ --override-ini="addopts=" -m postgres
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Sequence

import pytest
from sqlmodel import Session, select

import daemon.tools.instance_ops as io
from daemon.config import Config
from daemon.ensemble_config import EnsembleConfig, PostgresConfig
from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance_ops.models import InstanceOpsAudit
from daemon.repositories.message_queue.models import MessageQueue
from daemon.services.upgrade_journal_sweep import UpgradeJournalSweepService
from daemon.tools.upgrade_journal import (
    SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
    classify_user_origin,
)
from tests.helpers.df4_wake_fixtures import (
    append_journal_history,
    make_install,
    make_pending_wake,
)

# EVERY SQLModel model module is imported HERE, at COLLECTION time, so the
# ``pg_engine`` fixture's ``create_all`` (session setup) creates the FULL
# schema. A model registered only later (e.g. by a lazily-imported module)
# would leave its table missing while the conftest's per-test TRUNCATE
# already knows about it — the next test in the session would then fail on
# an undefined relation.
import daemon.models.agent  # noqa: F401
import daemon.models.app_settings  # noqa: F401
import daemon.models.board  # noqa: F401
import daemon.models.instance  # noqa: F401
import daemon.models.mapping  # noqa: F401
import daemon.models.mcp_server  # noqa: F401
import daemon.models.message  # noqa: F401
import daemon.models.resource_monitor  # noqa: F401
import daemon.models.schedule  # noqa: F401
import daemon.models.source  # noqa: F401
import daemon.models.tmp_image  # noqa: F401
import daemon.repositories.auth_session.models  # noqa: F401
import daemon.repositories.blueprint.models  # noqa: F401
import daemon.repositories.custom_agent_configs.models  # noqa: F401
import daemon.repositories.db_connection.models  # noqa: F401
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.ens_db.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.infra.models  # noqa: F401
import daemon.repositories.instance_ui_prefs.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.maintenance_runs.models  # noqa: F401
import daemon.repositories.mcp_kb_keys.models  # noqa: F401
import daemon.repositories.mcp_server.models  # noqa: F401
import daemon.repositories.message_metadata.models  # noqa: F401
import daemon.repositories.prompt_version.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.secrets.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401
import daemon.repositories.shared_meta_kv.models  # noqa: F401
import daemon.repositories.skill.models  # noqa: F401
import daemon.repositories.snapshot.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.view_passcodes.models  # noqa: F401

pytestmark = pytest.mark.postgres


# ── the fence ──────────────────────────────────────────────────────────────

#: Databases this pin must NEVER touch. ``ensemble_test`` (the house
#: scratch DB, owned by the ``pg_engine`` fixture) is the only acceptable
#: target.
FORBIDDEN_DATABASES = frozenset({
    "ensemble",
    "ensemble_dev",
    "ensemble_demo",
    "ensemble_dev_prod",
    "ensemble_prod",
    "ensemble_sandbox",
    "postgres",
    "template0",
    "template1",
})

#: The house POSITIVE scratch-shape rule (adopted from
#: ``tests/postgres/test_resource_monitor_estimated_bool_pg.py``): a
#: scratch database NAME must be visibly scratch — "test" or "scratch"
#: in the name. The denylist above is necessary but NOT sufficient — a
#: live database that is not on the list would slip through it.
SCRATCH_NAME_MARKERS = ("test", "scratch")


def _assert_scratch_db(db_name: str, *, where: str) -> None:
    """Refuse a live/dev database OR a name that is not scratch-shaped.

    Two rails, both required: the FORBIDDEN name list and the POSITIVE
    scratch-shape rule.
    """
    assert db_name not in FORBIDDEN_DATABASES, (
        f"refusing to run against a live/dev database ({where}): {db_name!r}"
    )
    lowered = db_name.lower()
    assert any(marker in lowered for marker in SCRATCH_NAME_MARKERS), (
        f"refusing to run against a non scratch-shaped database ({where}): "
        f"{db_name!r} — the name must contain 'test' or 'scratch'"
    )


def _refuse_non_scratch(db_name: str) -> None:
    """The COLLECTION-TIME fence — RAISES (never asserts).

    The session-scoped ``pg_engine`` fixture drops every table at
    session setup; a mis-pointed ``PG_TEST_DB`` must be refused BEFORE
    that happens, so this runs at module import (collection time), not
    inside a test body.
    """
    try:
        _assert_scratch_db(db_name, where="PG_TEST_DB (collection-time fence)")
    except AssertionError as exc:
        raise RuntimeError(
            f"refused: {exc} — the DF-4 postgres pin is scratch-only "
            "(point PG_TEST_DB at a scratch-shaped database)"
        ) from exc


_refuse_non_scratch(os.environ.get("PG_TEST_DB", "ensemble_test"))

ARMING_IID = "i-df4-arming"
CHILD_IID = "i-df4-child"
TARGET_IID = "i-df4-target"


def _pg_settings() -> dict[str, Any]:
    """The house ``PG_TEST_*`` settings (the same ones the ``pg_engine``
    fixture reads — its URL is built at conftest import time)."""
    return {
        "host": os.environ.get("PG_TEST_HOST", "localhost"),
        "port": int(os.environ.get("PG_TEST_PORT", "5432")),
        "db": os.environ.get("PG_TEST_DB", "ensemble_test"),
        "user": os.environ.get("PG_TEST_USER", "ensemble"),
        "password": os.environ.get("PG_TEST_PASSWORD", "ensemble_dev"),
    }


@pytest.fixture
def real_manager(
    pg_engine, tmp_path, monkeypatch
) -> Generator[InstanceManager, None, None]:
    """A REAL ``InstanceManager`` over the REAL PostgreSQL engine.

    ``pg_engine`` is requested so the fixture owns the schema lifecycle
    (drop_all + create_all at session setup, TRUNCATE per test).
    """
    settings = _pg_settings()
    _assert_scratch_db(settings["db"], where="PG_TEST_DB (fixture)")
    # ``create_postgres_engine`` reads POSTGRES_* FIRST — pin them to the
    # SAME scratch database the fixture owns, so the manager's engine and
    # the fixture's engine are the same database (never the ambient one).
    monkeypatch.setenv("POSTGRES_HOST", settings["host"])
    monkeypatch.setenv("POSTGRES_PORT", str(settings["port"]))
    monkeypatch.setenv("POSTGRES_DB", settings["db"])
    monkeypatch.setenv("POSTGRES_USER", settings["user"])
    monkeypatch.setenv("POSTGRES_PASSWORD", settings["password"])
    monkeypatch.delenv("POSTGRES_URL", raising=False)

    config = Config()
    # The checkpoint/SQLite paths are tmp_path-scoped — NEVER a live or
    # HOME-derived install path.
    config.persistence.db_path = str(tmp_path / "instances.db")
    config.mcp_pool.enabled = False
    ensemble_config = EnsembleConfig(
        database="postgres",
        postgres=PostgresConfig(
            host=settings["host"],
            port=settings["port"],
            db=settings["db"],
            user=settings["user"],
            password=settings["password"],
        ),
    )
    manager = InstanceManager(config, ensemble_config=ensemble_config)
    # The fence is asserted INSIDE the test, against the RESOLVED engine.
    resolved = manager.engine.url.database
    _assert_scratch_db(resolved, where="the RESOLVED manager engine database")
    assert resolved == settings["db"], (
        "the manager's engine must resolve to the same scratch database "
        f"the pg_engine fixture owns ({settings['db']!r} != {resolved!r})"
    )
    try:
        yield manager
    finally:
        # The manager's own PG engine/pool is never disposed otherwise
        # (``cleanup()`` disposes exactly that engine).
        manager.cleanup()


# ── helpers ────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A tmp install dir with a real journal — never a live-shaped path."""
    return make_install(tmp_path)


def _seed_rows(manager: InstanceManager) -> None:
    """REAL instance rows: the arming instance, its delegated child, and
    a PROTECTED target (agent_id == "leader") that is old enough to pass
    the min-age rail."""
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with Session(manager.engine) as session:
        for iid in (ARMING_IID, CHILD_IID, TARGET_IID):
            existing = session.exec(
                select(Instance).where(Instance.instance_id == iid)
            ).first()
            if existing is not None:
                session.delete(existing)
        session.add(
            Instance(
                instance_id=ARMING_IID,
                agent_id="worker",
                agent_dir="agents/worker",
                status=InstanceStatus.IDLE.value,
                instance_metadata={},
                created_at=old,
                updated_at=old,
            )
        )
        session.add(
            Instance(
                instance_id=CHILD_IID,
                agent_id="worker",
                agent_dir="agents/worker",
                parent_id=ARMING_IID,
                status=InstanceStatus.IDLE.value,
                instance_metadata={},
                created_at=old,
                updated_at=old,
            )
        )
        session.add(
            Instance(
                instance_id=TARGET_IID,
                agent_id="leader",
                agent_dir="agents/leader",
                status=InstanceStatus.IDLE.value,
                instance_metadata={},
                created_at=old,
                updated_at=old,
            )
        )
        session.commit()


def _delivered_sources(manager: InstanceManager) -> list[str]:
    """The sources the sweep actually delivered — read back from the REAL
    ``message_queue`` rows."""
    with Session(manager.engine) as session:
        rows = session.exec(select(MessageQueue)).all()
        return [r.source for r in rows if r.source]


async def _drive_funnel(
    manager: InstanceManager,
    instance_id: str,
    message_source: str,
    message_id: str,
) -> str:
    """Drive the REAL dispatch funnel.

    The funnel entry point is ``InstanceManager._process_message_with_tracking``
    (``daemon/manager.py``). Its FIRST two actions are the provenance
    sites: ``manager.stamp_user_origin_window(...)`` and
    ``manager._inherit_provenance_window(...)``. The turn itself then
    continues into the messaging service — this pin asserts the PROVENANCE
    STATE, so a downstream failure (no graph / no checkpointer in a
    headless harness) is expected and swallowed.

    Returns the DIAGNOSTIC of that swallowed failure (a short
    ``TypeName: message`` string, or ``"no exception"``) so the follow-on
    asserts can report WHY the funnel failed instead of failing
    opaquely. The swallow itself is deliberate: the follow-on asserts
    catch a funnel failure BEFORE the stamp site, so the pin cannot pass
    vacuously.
    """
    try:
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="df4 provenance closure probe",
            message_id=message_id,
            message_source=message_source,
        )
    except Exception as exc:  # noqa: BLE001 — the turn may fail; the stamp ran
        return f"{type(exc).__name__}: {exc}"
    return "no exception"


def _audit_rows(manager: InstanceManager) -> list[InstanceOpsAudit]:
    with Session(manager.engine) as session:
        return list(session.exec(select(InstanceOpsAudit)).all())


def _by_name(tools: Sequence[Any], name: str) -> Any:
    """The registered tool named ``name`` — a MISS is a legible failure."""
    tool = next((t for t in tools if t.name == name), None)
    assert tool is not None, (
        f"tool {name!r} is not registered "
        f"(registered: {sorted(t.name for t in tools)})"
    )
    return tool


# ── pin (iii) ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_forged_wake_is_refused_real_chain(
    real_manager, install
) -> None:
    """The sweep-forged chain is DEAD: no window is minted, the delegated
    child inherits nothing, and the protected act REFUSES.

    This is the closure proof's REFUSAL leg, driven through the REAL
    chain — the sweep, the dispatch funnel, the inherit mint and the gate.
    """
    manager = real_manager
    _seed_rows(manager)

    # 1. a REAL wake armed with NO user origin.
    make_pending_wake(
        install,
        source="",
        arming_instance_id=ARMING_IID,
        message_id="m-arm-1",
        message_metadata={},
    )
    append_journal_history(install, "commit", detail="df4")

    # 2. the REAL sweep — the fallback arm is exercised by production code.
    service = UpgradeJournalSweepService(install, manager=manager)
    result = await service.sweep_wake_records()
    assert result.delivered == 1, "the sweep must have delivered the wake"

    # 3. the delivered source, read back from the REAL queue row.
    delivered_sources = _delivered_sources(manager)
    assert delivered_sources, "the sweep must have written a queue row"
    for delivered in delivered_sources:
        assert delivered == SWEEP_NO_USER_ORIGIN_WAKE_SOURCE
        # the constant's VALUE must never be the user-origin literal
        assert delivered != "api"

    # 4. the classifier fails the delivered source CLOSED.
    ok, detail = classify_user_origin(delivered_sources[0], None)
    assert ok is False
    assert detail == "reserved-internal"

    # 5a. the SWEEP RE-STAMP did NOT fire. The sweep's re-stamp is
    #     ``if w.source:``-gated (upgrade_journal_sweep.py) and the armed
    #     source is empty, so the REAL manager's observation store must
    #     still be EMPTY — this asserts the CALL did not happen, not
    #     merely that no window exists.
    assert manager._user_origin_last_stamp.get(ARMING_IID) is None, (
        "the sweep's re-stamp must not fire for a no-user-origin wake"
        " (observed stamp state: "
        f"{manager._user_origin_last_stamp.get(ARMING_IID)!r})"
    )

    # 5b. the REAL dispatch funnel runs; the stamp takes the POP branch
    #     (manager.stamp_user_origin_window's else-arm pops the window).
    funnel_diag = await _drive_funnel(
        manager, ARMING_IID, delivered_sources[0], "m-df4-funnel-1"
    )
    assert ARMING_IID not in manager._user_origin_windows, (
        "no DIRECT window may be minted on the woken instance"
        f" (funnel diagnostic: {funnel_diag})"
    )
    stamp = manager._user_origin_last_stamp[ARMING_IID]
    assert stamp["stamped"] is False, f"funnel diagnostic: {funnel_diag}"
    assert stamp["detail"] == "reserved-internal", (
        f"funnel diagnostic: {funnel_diag}"
    )

    # 6. the delegated child inherits NOTHING (the parent has no live
    #    window to copy).
    child_diag = await _drive_funnel(
        manager, CHILD_IID, f"internal_agent:{ARMING_IID}", "m-df4-funnel-2"
    )
    assert CHILD_IID not in manager._inherited_provenance_windows, (
        "no INHERITED window may be minted from a forged root"
        f" (funnel diagnostic: {child_diag})"
    )

    # 7. the protected act on the child REFUSES.
    #
    # F6 / D-8 (nest-ops shakedown Phase 3): the protected gate SPLITS
    # at the dry_run fork. The CONFIRMATION conditions run on both
    # paths; ONLY the user-provenance condition is apply-only. So:
    #
    #   * the DEFAULT call (a preview) REPORTS the apply-path
    #     requirement as data — it does not refuse, and it does not
    #     write a refusal row;
    #   * the DESTRUCTIVE act (dry_run=False) still REFUSES with
    #     ``no-user-provenance`` and still writes the refusal row.
    #
    # The refusal leg — the point of this pin — is UNCHANGED: the forged
    # wake arms nothing, so the act cannot pass the gate. Only the
    # rendering of the preview moved.
    tools = io.create_instance_ops_tools(
        manager, current_instance_id=CHILD_IID, agent_id="maintenancer"
    )
    preview = await _by_name(tools, "instance_terminate").ainvoke(
        {
            "id": TARGET_IID,
            "reason": "df4 closure probe",
            "confirmed_target_id": TARGET_IID,
        }
    )
    assert "NO mutation happened (dry-run)" in preview, preview
    preview_payload = json.loads(preview.split("\nNO mutation happened")[0])
    assert preview_payload["act_gate_would_pass"] == {
        "ok": False,
        "reason": "no-user-provenance",
    }, preview
    out = await _by_name(tools, "instance_terminate").ainvoke(
        {
            "id": TARGET_IID,
            "reason": "df4 closure probe",
            "confirmed_target_id": TARGET_IID,
            "dry_run": False,
        }
    )
    assert "reason=no-user-provenance" in out, out

    # 8. the REAL audit refusal row — the countability surface survives.
    rows = _audit_rows(manager)
    assert rows, "the refusal must be recorded in the audit ledger"
    row = rows[-1]
    assert row.outcome == "refused"
    assert row.action == "instance_terminate"
    assert row.root_source is None
    assert row.provenance_depth is None


@pytest.mark.asyncio
async def test_genuine_wake_source_mints_the_window_and_admits(
    real_manager, install
) -> None:
    """NEGATIVE CONTROL — the pin above is not passing vacuously.

    The SAME real path with a GENUINE user-origin source mints the window
    and the protected act ADMITS.
    """
    manager = real_manager
    _seed_rows(manager)

    # "api" is the genuine-by-design web-UI chat ingress literal
    # (daemon/routers/messages.py) — the exact user-origin arm of
    # classify_user_origin. It is ALSO the literal the sweep's retired
    # fallback used to fabricate, so this control isolates the one
    # difference that matters: a GENUINE user-origin source vs a
    # no-user-origin wake.
    make_pending_wake(
        install,
        source="api",
        run_id="r-df4-genuine",
        arming_instance_id=ARMING_IID,
        message_id="m-arm-1",
        message_metadata={},
    )
    append_journal_history(install, "commit", detail="df4")

    service = UpgradeJournalSweepService(install, manager=manager)
    result = await service.sweep_wake_records()
    assert result.delivered == 1

    delivered_sources = _delivered_sources(manager)
    assert delivered_sources
    assert delivered_sources[0] == "api"
    ok, _detail = classify_user_origin(delivered_sources[0], None)
    assert ok is True

    # the REAL funnel mints the window (the POP branch does NOT run).
    funnel_diag = await _drive_funnel(
        manager, ARMING_IID, delivered_sources[0], "m-df4-genuine-1"
    )
    assert ARMING_IID in manager._user_origin_windows, (
        "a genuine user-origin source MUST mint the window"
        f" (funnel diagnostic: {funnel_diag})"
    )
    assert manager._user_origin_last_stamp[ARMING_IID]["stamped"] is True, (
        f"funnel diagnostic: {funnel_diag}"
    )

    # the delegated child inherits the genuine root.
    child_diag = await _drive_funnel(
        manager, CHILD_IID, f"internal_agent:{ARMING_IID}", "m-df4-genuine-2"
    )
    assert CHILD_IID in manager._inherited_provenance_windows, (
        "a genuine delegation MUST mint the inherited window"
        f" (funnel diagnostic: {child_diag})"
    )

    # …and the protected act on the child ADMITS.
    tools = io.create_instance_ops_tools(
        manager, current_instance_id=CHILD_IID, agent_id="maintenancer"
    )
    out = await _by_name(tools, "instance_terminate").ainvoke(
        {
            "id": TARGET_IID,
            "reason": "df4 genuine control",
            "confirmed_target_id": TARGET_IID,
            "dry_run": False,
        }
    )
    assert "reason=no-user-provenance" not in out, out
    rows = _audit_rows(manager)
    committed = [r for r in rows if r.outcome == "committed"]
    assert committed, "the admitted act must carry a committed audit row"
    assert committed[-1].provenance_class == "inherited"
    assert committed[-1].provenance_depth == 1
    assert committed[-1].root_source == "api"
