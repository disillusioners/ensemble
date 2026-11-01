"""wi-1fbee2ec STEP 2 — BASE-direction emission repro (BASE-API ONLY).

Drives the 10:35 shape against the BASE tree (f7039c3ff) at the
emission checkpoint (child_reports._root_completion_gate) plus the
in-turn decide() 4-evaluation arc and the G2 base walk. cwd MUST be
/home/nea/ensemble-src-wt-obligation-base.

EXPECTED OLD results (the blind spot):
  PART 1  gate PROCEEDS (True, None, False) with the obligation
          outstanding; verdict identical to the obligation-free tree.
  PART 2  4th evaluation trips TERMINAL_AFTER_BOUND (OLD closure).
  PART 3  minted-IDLE child reads (busy=0, live=0) on the base walk;
          in-turn evaluate returns TERMINAL_AFTER_BOUND (verify-clean);
          the emission gate DOES block the same tree via tree-liveness
          (asymmetry documented).
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.message_queue.models import MessageStatus
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.attestation_gate import Decision, GateSettings, decide, evaluate
from daemon.services.child_reports import ChildReportsService
from daemon.write_pause_guard import WritePauseGuard
from langchain_core.messages import AIMessage, HumanMessage

ROOT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
DB = Path("/tmp/wi1f-g1g2-base-emission.db")
if DB.exists():
    DB.unlink()

engine = create_engine(
    f"sqlite:///{DB}",
    connect_args={"check_same_thread": False, "timeout": 30},
)
SQLModel.metadata.create_all(engine)
repo = ReportInjectionRepository(engine=engine)
inst_repo = SQLModelInstanceRepository(engine)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_instance(instance_id, status, parent_id=None):
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="worker",
                agent_dir="agents/worker",
                status=status,
                parent_id=parent_id,
                version=2,
                created_at=now(),
                updated_at=now(),
            )
        )
        s.commit()


def insert_injection(parent_id, child_id, state, reason=None):
    with Session(engine) as s:
        s.add(
            ReportInjection(
                parent_instance_id=parent_id,
                child_instance_id=child_id,
                child_message_id="child-msg-1",
                report_message_id="report-msg-1",
                content="x",
                state=state,
                deferred_reason=reason,
            )
        )
        s.commit()


def make_gate_service():
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager._instance_repository = inst_repo
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    service = ChildReportsService(manager=manager)
    service._bus_count_pending_for_target_sync = lambda iid: 0
    service._count_outstanding_mission_commissions = lambda iid: 0
    service._gate_wedge_resolver = AsyncMock(return_value=(True, None, False))
    return service


def banner(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ─────────────────────────────────────────────────────────────────────
# PART 1 — G1 at the EMISSION checkpoint: the 10:35 shape
# ─────────────────────────────────────────────────────────────────────
banner("PART 1 — 10:35 shape at BASE _root_completion_gate")

insert_instance(ROOT, InstanceStatus.COMPLETED.value)
insert_instance(CHILD, InstanceStatus.COMPLETED.value, parent_id=ROOT)
# the undelivered report obligation owed to the gated root
# (deferred delivery — the incident's durable marker)
insert_injection(
    ROOT, CHILD,
    state=ReportInjectionState.DEFERRED.value,
    reason="RESUME_ROUTER",
)

print("[1a] base repo primitives SEE the obligation (data shape sane):")
deferred = repo.find_deferred_for_parent(ROOT)
print(f"     find_deferred_for_parent(ROOT)              -> {len(deferred)} row(s)")
print(f"     count_pending_for_parent(ROOT)              -> {repo.count_pending_for_parent(ROOT)}")
with Session(engine) as s:
    rows = repo.count_pending_for_parent_with_terminal_child(s, ROOT)
print(f"     count_pending_for_parent_with_terminal_child -> {len(rows)} row(s) {rows}")

print("[1b] drive BASE _root_completion_gate(None, ROOT):")
verdict = asyncio.run(make_gate_service()._root_completion_gate(None, ROOT))
print(f"     BASE VERDICT (with obligation outstanding): {verdict}")

print("[1c] counterfactual — obligation row terminal (delivered):")
with Session(engine) as s:
    row = s.exec(select(ReportInjection)).one()
    row.state = ReportInjectionState.TASK_DELIVERED.value
    s.add(row)
    s.commit()
verdict_cf = asyncio.run(make_gate_service()._root_completion_gate(None, ROOT))
print(f"     BASE VERDICT (obligation delivered):        {verdict_cf}")
same = verdict == verdict_cf
print(f"     IDENTICAL VERDICTS -> base gate CANNOT see the obligation: {same}")

# ─────────────────────────────────────────────────────────────────────
# PART 2 — G1 at the IN-TURN checkpoint: base decide() 4-eval arc
# ─────────────────────────────────────────────────────────────────────
banner("PART 2 — base decide() 4-evaluation arc (quiet tree, the 10:35 in-turn shape)")


def real(content="ship the feature"):
    return HumanMessage(content=content)


def delegating_ai():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"instance_id": "child-1", "message": "do it"},
                "id": "call-1",
            }
        ],
    )


ENFORCE = GateSettings("enforce", 3, 3)
msgs = [real(), delegating_ai(), AIMessage(content="All done.")]

for dc in (0, 1, 2):
    d = decide(
        attested=False,
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=0,
        denied_count=dc,
        bound=3,
        attestation_required=True,
    )
    print(f"     eval {dc + 1}: denied_count={dc} -> {d.decision.name} (next={d.next_denied_count})")

d4 = decide(
    attested=False,
    pending_children=0,
    queued_or_expected_wakeups=0,
    live_descendants=0,
    denied_count=3,
    bound=3,
    attestation_required=True,
)
print(f"     eval 4: denied_count=3 == bound -> {d4.decision.name} (next={d4.next_denied_count})")
print(f"     OLD BOUND CLOSURE at 4th evaluation: {d4.decision is Decision.TERMINAL_AFTER_BOUND}")

print("[2b] base evaluate() end-to-end on the same quiet shape (the")
print("     manager carries an obligations facade the base gate never reads):")
mm = MagicMock()
mm.count_pending_children = MagicMock(return_value=0)
mm.get_queued_or_expected_wakeups = MagicMock(return_value=0)
mm.count_live_descendants = MagicMock(return_value=0)
mm.count_outstanding_commissions = MagicMock(return_value=0)
mm.count_outstanding_report_obligations = MagicMock(return_value=1)  # INVISIBLE to base
res = evaluate("inst-repro", 3, msgs, ENFORCE, mm)
print(f"     BASE evaluate() -> {res.decision.name} (next={res.next_denied_count})")
print(f"     obligations=1 changed nothing (blind): {res.decision is Decision.TERMINAL_AFTER_BOUND}")

# ─────────────────────────────────────────────────────────────────────
# PART 3 — G2 BASE direction: minted-but-not-RUNNING child
# ─────────────────────────────────────────────────────────────────────
banner("PART 3 — G2 base direction: minted IDLE child, quiet tree")

G2ROOT = "55555555-5555-4555-8555-555555555501"
G2CHILD = "55555555-5555-4555-8555-555555555502"
insert_instance(G2ROOT, InstanceStatus.RUNNING.value)
insert_instance(G2CHILD, InstanceStatus.IDLE.value, parent_id=G2ROOT)

shell = MagicMock()
shell._instance_repository = inst_repo
shell.LIVE_DESCENDANTS_BFS_CAP = InstanceManager.LIVE_DESCENDANTS_BFS_CAP
# The BASE dormant resolution consults these two lanes for IDLE/QUEUED
# descendants; a MagicMock shell would auto-create Mock repos and
# poison the probe. Suppress BOTH to their unwired None (the
# "dormant with no evidence rows" production shape — b08f40fe doc):
shell._queue_repository = None
shell._job_queue_service = None
walk = MethodType(InstanceManager._count_descendants_busy_and_live, shell)
busy, live = walk(G2ROOT)
print(f"     BASE walk _count_descendants_busy_and_live({G2ROOT[:8]}…)")
print(f"       -> busy={busy} live={live}   (minted IDLE child, NO evidence rows)")
print(f"       OLD all-zero liveness read: {live == 0}")

d = decide(
    attested=False,
    pending_children=0,
    queued_or_expected_wakeups=0,
    live_descendants=live,
    denied_count=3,
    bound=3,
    attestation_required=True,
)
print(f"     decide(live_descendants={live}, denied=3==bound) -> {d.decision.name}")
print(f"     G2 BASE verify-clean closure: {d.decision is Decision.TERMINAL_AFTER_BOUND}")

mm2 = MagicMock()
mm2.count_pending_children = MagicMock(return_value=0)
mm2.get_queued_or_expected_wakeups = MagicMock(return_value=0)
mm2.count_live_descendants = MagicMock(return_value=live)  # 0 — the base walk result
mm2.count_outstanding_commissions = MagicMock(return_value=0)
res2 = evaluate("inst-g2", 3, msgs, ENFORCE, mm2)
print(f"     BASE evaluate() end-to-end -> {res2.decision.name} (next={res2.next_denied_count})")

print("[3b] asymmetry note — the EMISSION gate at base DOES block the")
print("     same minted-IDLE tree via its tree-liveness leg:")
E_ROOT = "55555555-5555-4555-8555-555555555503"
E_CHILD = "55555555-5555-4555-8555-555555555504"
insert_instance(E_ROOT, InstanceStatus.COMPLETED.value)
insert_instance(E_CHILD, InstanceStatus.IDLE.value, parent_id=E_ROOT)
verdict_e = asyncio.run(make_gate_service()._root_completion_gate(None, E_ROOT))
print(f"     BASE emission gate on minted-IDLE tree: {verdict_e}")
print(f"       blocked={verdict_e[0] is False} (emission-side leg already counts non-terminal)")

engine.dispose()
print()
print("STEP-2 BASE REPRO COMPLETE")
