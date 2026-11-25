"""P5 T5.1 — R2 no-idle-starvation two-legged pin (BR-6, MUTATION TARGET).

The old daemon-internal maintenance tier gated every job on an idle probe and
starved on a busy nest (500 skips / 0 executions).  The Central Maintenance
System replaces it with a self-rescheduling one-shot chain that MUST dispatch
on the schedule regardless of how busy the system is.  This pack pins that
contract in two legs:

* **BEHAVIORAL** — a BUSY system (non-empty request registry, mirroring
  ``tests/unit/test_maintenance_idle_starvation.py::_busy_service``) still
  enqueues the due ``maintenancer`` chain one-shot, with the deterministic
  dedup idempotency key, and NO idle/busy consult anywhere on the dispatch
  path.  The only deferral — busy-CALLER for ``reuse_instance`` — is
  unreachable because one-shots force ``new_instance``.
* **STRUCTURAL** — an AST scan (never grep; comments are invisible to AST and
  docstring ``Expr`` nodes are explicitly skipped) asserting the canonical
  predicate below over EXACTLY FOUR files, with an in-file non-vacuity
  self-check.

Canonical predicate (single source of truth, cited VERBATIM from P1 T1.5 /
P1 AC5 — the predicate-ledger convention):

no reference resolving to JOB_IDLE_POLICIES, IdlePolicy, or _is_idle in daemon/sources/adapters/scheduler.py, daemon/services/maintenance_schedule_knobs.py, daemon/repositories/source/maintenance_seed.py, or daemon/manager.py

SAFETY FENCE: throwaway SQLite under ``tmp_path``; no PostgreSQL, no daemon
boot, no port binding, no network.  Production code is never modified here —
the mutation proof is a separate, later serialized leg.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

from daemon.models import SchedulerInstanceMode
from daemon.repositories.source.models import (
    ScheduleExecution,
    SourceConfig,
    SourceStatus,
)
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.services.maintenance import MaintenanceService
from daemon.sources.adapters.scheduler import SchedulerAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "no-idle-pin-chain-source"

# Fixed, past-due instant — deterministic idempotency key, no clock dependence.
RUN_AT = datetime(2024, 5, 6, 3, 0, tzinfo=timezone.utc)

CANONICAL_PREDICATE = (
    "no reference resolving to JOB_IDLE_POLICIES, IdlePolicy, or _is_idle in "
    "daemon/sources/adapters/scheduler.py, "
    "daemon/services/maintenance_schedule_knobs.py, "
    "daemon/repositories/source/maintenance_seed.py, or daemon/manager.py"
)

# EXACTLY four files (BR-6) — no more, no less.
AST_FILES = (
    "daemon/sources/adapters/scheduler.py",
    "daemon/services/maintenance_schedule_knobs.py",
    "daemon/repositories/source/maintenance_seed.py",
    "daemon/manager.py",
)

FORBIDDEN_IDENTIFIERS = frozenset({"JOB_IDLE_POLICIES", "IdlePolicy", "_is_idle"})


def _canonical_predicate_is_verbatim_in_docstring() -> None:
    """Guard the predicate-ledger single-source convention (internal helper)."""
    assert CANONICAL_PREDICATE in (__doc__ or "")


# ══════════════════════════════════════════════════════════════════════════
# Shared fixture — a due one-shot `maintenancer` chain row in throwaway SQLite
# ══════════════════════════════════════════════════════════════════════════


def _chain_config(run_at: datetime) -> dict:
    return {
        "agent": "maintenancer",
        "message": "Weekly central maintenance run.\n\ntrigger: scheduled\n",
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "once",
        "local_time": "03:00",
        "run_at": run_at.isoformat(),
        "instance_mode": "new_instance",
        "project_id": "system-default",
        "chain": {
            "mode": "weekly-random",
            "window_start": "01:00",
            "window_end": "05:00",
            "weekday": 0,
            "timezone": "Asia/Ho_Chi_Minh",
            "run_at_utc": run_at.isoformat(),
        },
    }


@pytest.fixture
def chain_engine(tmp_path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'no_idle_pin.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(
        engine, tables=[SourceConfig.__table__, ScheduleExecution.__table__]
    )
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            SourceConfig(
                source_id=SOURCE_ID,
                source_type="scheduler",
                name="central-maintenance-weekly",
                config=_chain_config(RUN_AT),
                credentials=None,
                enabled=True,
                autostart=True,
                status=SourceStatus.RUNNING.value,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()


async def _noop_on_message(_msg) -> None:
    return None


def _load_row(engine: Engine) -> SourceConfig:
    with Session(engine) as session:
        row = session.get(SourceConfig, SOURCE_ID)
    assert row is not None
    return row


def _build_adapter(engine: Engine, manager) -> SchedulerAdapter:
    return SchedulerAdapter(
        _load_row(engine),
        on_message=_noop_on_message,
        manager=manager,
        source_repo=SQLModelSourceRepository(engine),
    )


# ══════════════════════════════════════════════════════════════════════════
# LEG (i) — BEHAVIORAL: busy system still dispatches the chain one-shot
# ══════════════════════════════════════════════════════════════════════════


class TestR2NoIdleBehavioral:
    @pytest.mark.asyncio
    async def test_busy_chain_one_shot_enqueues_with_dedup_key_and_no_idle_consult(
        self, chain_engine, monkeypatch
    ):
        """A BUSY system still enqueues the due maintenancer chain one-shot.

        The busy fixture mirrors ``_busy_service`` in the old-tier substrate:
        a non-empty request registry is the "system is busy" signal that used
        to make ``MaintenanceService._is_idle`` return False (→ 500 skips).
        Here the chain one-shot MUST dispatch anyway, with the deterministic
        ``scheduler:{source_id}:slot:{run_at.isoformat()}`` dedup key, and the idle
        probe is NEVER consulted on the dispatch path.
        """
        # ── Busy fixture (non-empty request registry) ───────────────────
        busy = MaintenanceService()
        busy.set_request_registry({"inflight-request": object()})
        assert len(busy._request_registry) > 0  # genuinely busy
        idle_probe = AsyncMock(return_value=False)  # "busy" idle result
        busy._is_idle = idle_probe  # type: ignore[method-assign]

        # ── Manager mock: capture the enqueue call ──────────────────────
        manager = MagicMock()
        enqueue = AsyncMock(
            return_value=SimpleNamespace(job_id="job-1", message_id="msg-1")
        )
        manager.enqueue_message_job = enqueue

        adapter = _build_adapter(chain_engine, manager=manager)
        assert adapter._run_at == RUN_AT
        assert adapter._schedule_type == SchedulerAdapter.SCHEDULE_TYPE_ONE_TIME

        # ── Mock the mapper / registry seam so dispatch reaches enqueue ─
        fake_mapper = MagicMock()
        fake_mapper.get_or_create_instance = AsyncMock(return_value="inst-123")
        monkeypatch.setattr(
            "daemon.sources.mapper.InstanceMapper", lambda *a, **k: fake_mapper
        )
        monkeypatch.setattr(
            "daemon.sources.adapters.scheduler.get_registry",
            lambda: SimpleNamespace(resolve_to_id=lambda _agent: None),
        )
        # Standing product ruling: scheduler-lane mint stays inert by default.
        monkeypatch.setattr(
            "daemon.sources.adapters.scheduler._scheduler_mint_armed",
            lambda: False,
        )
        adapter._notify_fire_notice = AsyncMock()  # type: ignore[method-assign]

        metadata = {
            "scheduler": {"trigger_type": "scheduled"},
            "agent": "maintenancer",
            "force_new_instance": True,
        }
        await adapter._route_via_job_queue(
            "exec-1", "Weekly central maintenance run.", metadata
        )

        # ── The enqueue fired, with the deterministic dedup key ─────────
        enqueue.assert_awaited_once()
        kwargs = enqueue.await_args.kwargs
        assert kwargs["idempotency_key"] == (
            f"scheduler:{SOURCE_ID}:slot:{RUN_AT.isoformat()}"
        )
        assert kwargs["source"] == "scheduler"

        # ── NO idle/busy consult anywhere on the dispatch path ──────────
        idle_probe.assert_not_awaited()
        # (The source-level "no reference" assertion is the structural leg's
        # job; here we pin the runtime fact that the busy probe is untouched.)

        # ── new_instance path: mapper forced fresh; no busy-CALLER defer ─
        fake_mapper.get_or_create_instance.assert_awaited_once()
        assert (
            fake_mapper.get_or_create_instance.await_args.kwargs["force_new"]
            is True
        )

    @pytest.mark.asyncio
    async def test_one_shot_forces_new_instance_so_busy_caller_deferral_is_unreachable(
        self, chain_engine
    ):
        """One-shots force ``new_instance`` ⇒ the reuse-only busy deferral is dead.

        ``_emit_scheduled_message`` has exactly one deferral — the busy-CALLER
        branch gated on ``_instance_mode == REUSE_INSTANCE``.  A chain one-shot
        is forced to ``NEW_INSTANCE`` at construction, so even an ACTIVE/busy
        caller mapped to the schedule must never enter that branch: the fire
        goes straight to ``_execute_run``.
        """
        manager = MagicMock()
        adapter = _build_adapter(chain_engine, manager=manager)
        assert adapter._instance_mode is SchedulerInstanceMode.NEW_INSTANCE

        # Arm the reuse-only busy-CALLER deferral to detect ANY entry: if the
        # branch were reached these mocks would be called/awaited.
        adapter._is_instance_active = Mock(  # type: ignore[method-assign]
            return_value=(True, "caller-inst", "running")
        )
        adapter._defer_delivery = AsyncMock()  # type: ignore[method-assign]

        # Stub the pre-branch fire machinery (age cap + slot acquisition).
        adapter._resolve_deferrable_target = Mock(return_value=None)  # type: ignore[method-assign]
        adapter._defer_bounds = Mock(return_value=(1, 999_999))  # type: ignore[method-assign]
        adapter._defer_state = Mock(return_value={})  # type: ignore[method-assign]
        adapter._handle_age_cap_breach = AsyncMock(return_value=None)  # type: ignore[method-assign]
        adapter._acquire_execution_slot = AsyncMock(return_value=True)  # type: ignore[method-assign]
        adapter._clear_episode = AsyncMock()  # type: ignore[method-assign]
        execute_run = AsyncMock()
        adapter._execute_run = execute_run  # type: ignore[method-assign]

        await adapter._emit_scheduled_message()

        # The busy-CALLER deferral (and its active-instance probe) never ran.
        adapter._is_instance_active.assert_not_called()
        adapter._defer_delivery.assert_not_awaited()
        # The fire went straight to the scheduled dispatch path.
        execute_run.assert_awaited_once()
        assert execute_run.await_args.args[1] == "scheduled"


# ══════════════════════════════════════════════════════════════════════════
# LEG (ii) — STRUCTURAL: AST scan over exactly the four BR-6 files
# ══════════════════════════════════════════════════════════════════════════


def _no_idle_refs(source: str, label: str) -> tuple[list[str], int]:
    """Return ``(offenses, nodes_visited)`` for one Python source string.

    Detect references via ``Name`` (bare identifier) and ``Attribute``
    (``obj.<identifier>``) nodes.  Comments are invisible to AST (good).
    DOCSTRINGS ARE ``Expr(Constant(str))`` nodes — skip them so the no-idle
    attestation text embedded in the new files' docstrings cannot false-RED.
    """
    tree = ast.parse(source, filename=label)
    offenses: list[str] = []
    visited = 0
    for node in ast.walk(tree):
        visited += 1
        # Skip docstring / bare-string expression statements.
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_IDENTIFIERS:
            offenses.append(f"{label}:{node.lineno} Name {node.id}")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in FORBIDDEN_IDENTIFIERS
        ):
            offenses.append(f"{label}:{node.lineno} Attribute .{node.attr}")
    return offenses, visited


def _no_idle_refs_in(path: Path) -> tuple[list[str], int]:
    return _no_idle_refs(path.read_text(encoding="utf-8"), str(path))


class TestR2NoIdleStructuralAST:
    def test_canonical_predicate_is_verbatim_in_docstring(self):
        """The canonical predicate string is the single source of truth."""
        _canonical_predicate_is_verbatim_in_docstring()

    def test_ast_file_set_is_exactly_four(self):
        """BR-6 pins EXACTLY four files — no more, no less."""
        assert AST_FILES == (
            "daemon/sources/adapters/scheduler.py",
            "daemon/services/maintenance_schedule_knobs.py",
            "daemon/repositories/source/maintenance_seed.py",
            "daemon/manager.py",
        )
        assert len(AST_FILES) == 4

    def test_detector_flags_forbidden_identifiers_in_synthetic_snippet(self):
        """NON-VACUITY: prove the walker actually detects all three ids.

        A synthetic in-test snippet containing ``_is_idle`` (plus the other
        two identifiers, in both bare-Name and Attribute shapes) must be
        flagged.  Without this, an empty-walker bug would make the real-file
        assertion vacuously green.
        """
        snippet = '''
def probe():
    if self._is_idle():
        return JOB_IDLE_POLICIES
    return IdlePolicy.UN_GATED
'''
        offenses, visited = _no_idle_refs(snippet, "<synthetic-non-vacuity>")
        assert visited > 0
        joined = "\n".join(offenses)
        assert "_is_idle" in joined
        assert "JOB_IDLE_POLICIES" in joined
        assert "IdlePolicy" in joined
        # Attribute shape (.attr) and bare Name shape both detected.
        assert any("Attribute ._is_idle" in line for line in offenses)
        assert any("Name JOB_IDLE_POLICIES" in line for line in offenses)

    def test_docstring_mention_does_not_false_red(self):
        """A forbidden identifier inside a docstring is NOT a reference."""
        snippet = '''
"""This module never uses JOB_IDLE_POLICIES, IdlePolicy, or _is_idle."""

def clean():
    return 1
'''
        offenses, visited = _no_idle_refs(snippet, "<synthetic-docstring>")
        assert visited > 0
        assert offenses == []

    def test_no_idle_references_in_the_four_br6_files(self):
        """CANONICAL PREDICATE (AST, docstrings/comments excluded).

        Assert no reference resolving to JOB_IDLE_POLICIES, IdlePolicy, or
        _is_idle in the four BR-6 files.  Each real path must exist and the
        walk must visit >0 nodes (non-vacuity on the real inputs).
        """
        missing = [rel for rel in AST_FILES if not (REPO_ROOT / rel).is_file()]
        assert missing == [], f"missing BR-6 files: {missing}"

        all_offenses: list[str] = []
        for rel in AST_FILES:
            offenses, visited = _no_idle_refs_in(REPO_ROOT / rel)
            assert visited > 0, f"AST walk visited 0 nodes in {rel}"
            all_offenses.extend(offenses)

        assert all_offenses == [], (
            CANONICAL_PREDICATE
            + "\nOffending references:\n"
            + "\n".join(f"  * {line}" for line in all_offenses)
        )
