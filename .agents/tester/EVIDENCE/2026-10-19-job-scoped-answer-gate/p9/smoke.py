#!/usr/bin/env python
"""Live HTTP route smoke for the job-scoped answer fix.

Branch fix/job-scoped-answer-resolution @ 829feb94d (worktree run).
In-process real app (create_app + lifespan) against ensemble_dev; real
HTTP route via fastapi.testclient (ASGI, binds no port). Only fixture
rows are synthetic; every created row id is recorded and deleted in
cleanup with exact-id predicates.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

START = time.monotonic()
WT = "/home/nea/ensemble-src-wt-job-answer-asker-fix"
EVIDENCE = "/tmp/jaaf-p9"
RUN_TAG = f"jaaf-p9-{datetime.now(timezone.utc).strftime('%H%M%S')}"
PROJECT_ID = RUN_TAG  # unique per-run project id for my rows

ev_lines: list[str] = []


def ev(msg: str) -> None:
    line = f"[{time.monotonic() - START:7.1f}s] {msg}"
    print(line, flush=True)
    ev_lines.append(line)


class SmokeTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise SmokeTimeout()


signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(280)  # script-internal deadline (outer guard: timeout 300)

# ── Fence asserts (env) ────────────────────────────────────────────────
assert os.environ.get("POSTGRES_DB") == "ensemble_dev", os.environ.get("POSTGRES_DB")
assert os.environ.get("DAEMON_PORT") == "8079", os.environ.get("DAEMON_PORT")
assert os.environ.get("ENSEMBLE_SELF_ENV") == "dev"
os.chdir(WT)
sys.path.insert(0, WT)

# ── Fence: keep the source-adapter subsystem idle in THIS process. ─────
# ensemble_dev.source_configs is empty, so this is belt-and-suspenders —
# but it deterministically prevents any chat-source adapter from ever
# connecting from the smoke process.
from daemon.manager import InstanceManager  # noqa: E402


async def _noop_start_sources(self):  # noqa: ANN001
    ev("FENCE: InstanceManager.start_sources() patched to no-op "
       "(smoke process never connects chat sources)")
    return


InstanceManager.start_sources = _noop_start_sources

# ── Boot the REAL app with lifespan ───────────────────────────────────
ev("booting real app via create_app() + TestClient lifespan ...")
t0 = time.monotonic()
from fastapi.testclient import TestClient  # noqa: E402

from daemon.api import create_app  # noqa: E402

app = create_app()
client = TestClient(app)
client.__enter__()
ev(f"app booted with lifespan in {time.monotonic() - t0:.1f}s")

manager = app.state.manager
assert manager is not None, "app.state.manager missing"
assert not manager.is_write_paused, "manager.is_write_paused is True — everything would 503"
import daemon as _daemon  # noqa: E402

ev(f"manager wired; is_write_paused=False; daemon.__version__={_daemon.__version__}")

# ── Route discovery: the REAL mounted path for the job answer route ────
answer_path = None
for r in app.routes:
    p = getattr(r, "path", "")
    if p.endswith("/answer") and "{work_id}" in p:
        answer_path = p
        break
assert answer_path, "job answer route not found in app.routes"
ev(f"REAL route path resolved: POST {answer_path}")

# ── DB access for seeding / verification / cleanup ────────────────────
from sqlalchemy import text  # noqa: E402
from sqlmodel import Session  # noqa: E402

from daemon.repositories.instance.models import Instance, InstanceStatus  # noqa: E402
from daemon.repositories.task.models import Task, TaskStatus, TaskType, SuspensionReason  # noqa: E402
from daemon.services.question_manager import pack_to_dict  # noqa: E402

url = (
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)
from sqlalchemy import create_engine  # noqa: E402

engine = create_engine(url)
qm = manager._question_manager
inst_repo = manager._instance_repository
task_repo = manager._task_repo

created_instance_ids: list[str] = []
created_task_work_ids: list[str] = []
created_packs: list[str] = []


def seed_instance(status: str, parent_id: str | None, agent_id: str) -> str:
    iid = f"inst-{uuid.uuid4()}"
    with Session(engine) as session:
        session.add(Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir="/tmp",
            project_id=PROJECT_ID,
            status=status,
            version=1,
            instance_metadata={},
            parent_id=parent_id,
        ))
        session.commit()
    created_instance_ids.append(iid)
    return iid


def seed_task(instance_id: str, status: str) -> str:
    wid = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(Task(
            work_id=wid,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance_id,
            status=status,
            suspension_reason=(
                SuspensionReason.AWAITING_ANSWER.value
                if status == TaskStatus.PAUSED.value
                else None
            ),
            resume_target_turn_id=wid if status == TaskStatus.PAUSED.value else None,
            created_at=datetime.now(timezone.utc),
        ))
        session.commit()
    created_task_work_ids.append(wid)
    return wid


def seed_asker_child(parent_id: str, q_text: str):
    """Child + awaiting_answer handle + RAM pack + durable shadow — the
    exact state the question tool leaves behind (mirror of
    _seed_asking_child in tests/job_queue/test_job_answer_target_resolution.py)."""
    child = seed_instance(InstanceStatus.PAUSED.value, parent_id, "devops")
    handle_wid = seed_task(child, TaskStatus.PAUSED.value)  # authoritative handle
    pack = qm.set_question_pack(child, [{"id": "q1", "text": q_text}])
    inst_repo.set_metadata(child, "question_pack_id", pack.id)
    inst_repo.set_metadata(child, "question_pack_payload", pack_to_dict(pack))
    created_packs.append(pack.id)
    return child, pack, handle_wid


def instance_status(iid: str) -> str | None:
    row = inst_repo.get(iid)
    return getattr(row, "status", None) if row else None


def task_row(wid: str):
    return task_repo.get_by_work_id(wid)


def dump_state(tag: str, iid: str, handle_wid: str | None, work_wid: str | None) -> None:
    ev(f"  DB[{tag}] instances.status = {instance_status(iid)!r}  (instance_id={iid})")
    for label, wid in (("handle", handle_wid), ("work", work_wid)):
        if not wid:
            continue
        t = task_row(wid)
        if t is None:
            ev(f"  DB[{tag}] task[{label}] work_id={wid} -> MISSING")
        else:
            ev(f"  DB[{tag}] task[{label}] work_id={wid} status={t.status!r} "
               f"suspension_reason={t.suspension_reason!r} "
               f"resume_target_turn_id={t.resume_target_turn_id!r}")


def run_tool_on_app_loop(coro_fn, tool_input: dict, timeout: float = 120.0):
    """Run an async callable on the app's own event loop (TestClient
    portal) so manager internals that captured that loop stay valid.
    The callable receives ``tool_input`` as its single positional arg —
    the same shape the branch tests use (``await tool.ainvoke({...})``)."""
    portal = getattr(client, "portal", None)
    if portal is not None:
        return portal.call(coro_fn, tool_input)
    import asyncio
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn(tool_input))).result(timeout=timeout)


def cleanup() -> bool:
    ev("── cleanup begin (exact-id predicates only) ──")
    ids_i = list(created_instance_ids)
    ids_w = list(created_task_work_ids)

    def safe_delete(conn, table: str, col: str, ids: list[str]) -> None:
        if not ids:
            return
        exists = conn.execute(
            text("SELECT count(*) FROM information_schema.tables "
                 "WHERE table_schema='public' AND table_name=:t"),
            {"t": table},
        ).scalar()
        if not exists:
            ev(f"cleanup: table {table} absent — skipped")
            return
        res = conn.execute(
            text(f"DELETE FROM {table} WHERE {col} = ANY(:ids)"), {"ids": ids}
        )
        ev(f"cleanup: DELETE FROM {table} WHERE {col} = ANY({len(ids)} recorded ids) -> {res.rowcount} rows")

    with engine.begin() as conn:
        # side-effect rows that reference my instance/work ids first
        safe_delete(conn, "event", "instance_id", ids_i)
        safe_delete(conn, "message_queue", "instance_id", ids_i)
        safe_delete(conn, "checkpoints", "thread_id", ids_i)
        safe_delete(conn, "checkpoint_blobs", "thread_id", ids_i)
        safe_delete(conn, "checkpoint_writes", "thread_id", ids_i)
        safe_delete(conn, "instance_hierarchy", "parent_id", ids_i)
        safe_delete(conn, "instance_hierarchy", "child_id", ids_i)
        safe_delete(conn, "job_watcher", "work_id", ids_w)
        # my fixture rows
        safe_delete(conn, "task", "work_id", ids_w)
        res = conn.execute(
            text("DELETE FROM instances WHERE instance_id = ANY(:ids)"), {"ids": ids_i}
        )
        ev(f"cleanup: DELETE FROM instances WHERE instance_id = ANY({len(ids_i)} recorded ids) "
           f"-> {res.rowcount} rows")

    ev("residue verification (every count must be 0):")
    zero_ok = True
    with engine.begin() as conn:
        for table, col, ids in (
            ("instances", "instance_id", ids_i),
            ("task", "work_id", ids_w),
            ("event", "instance_id", ids_i),
            ("message_queue", "instance_id", ids_i),
            ("checkpoints", "thread_id", ids_i),
            ("checkpoint_blobs", "thread_id", ids_i),
            ("checkpoint_writes", "thread_id", ids_i),
            ("job_watcher", "work_id", ids_w),
        ):
            if not ids:
                continue
            exists = conn.execute(
                text("SELECT count(*) FROM information_schema.tables "
                     "WHERE table_schema='public' AND table_name=:t"),
                {"t": table},
            ).scalar()
            if not exists:
                ev(f"  residue {table}: table absent — n/a")
                continue
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {col} = ANY(:ids)"), {"ids": ids}
            ).scalar()
            ev(f"  SELECT count(*) FROM {table} WHERE {col} = ANY(ids) -> {n}")
            if n:
                zero_ok = False

    # in-process RAM packs for my instances — drop so the store holds nothing of mine
    for iid in ids_i:
        try:
            qm.clear_question_pack(iid)
        except Exception:
            pass

    ev(f"row ids created (instances): {ids_i}")
    ev(f"row ids created (task work_ids): {ids_w}")
    ev(f"row ids created (pack ids): {created_packs}")
    ev(f"cleanup residue-zero: {zero_ok}")
    return zero_ok


def finish(result: str, detail: str = "") -> int:
    runtime = time.monotonic() - START
    ev(f"runtime: {runtime:.1f}s")
    ev(f"RESULT: {result}{(' — ' + detail) if detail else ''}")
    with open(f"{EVIDENCE}/evidence.md", "w") as f:
        f.write("# jaaf-p9 live HTTP route smoke evidence\n\n```\n"
                + "\n".join(ev_lines) + "\n```\n")
    return {"PASS": 0, "TIMEOUT": 124}.get(result, 1)


result, detail = "FAIL", "unreached"
try:
    # ── Seed mission M1 (root R1 + child C1 with pending pack P1) ─────
    ev("seeding mission M1 (root R1, child C1 with pending pack P1, work W1 on R1)")
    root1 = seed_instance(InstanceStatus.PAUSED.value, None, "leader")
    w1 = seed_task(root1, TaskStatus.RUNNING.value)  # mission work unit on the ROOT
    child1, pack1, h1 = seed_asker_child(root1, "P1: Approach A or B?")
    ev(f"M1 ids: R1={root1} C1={child1} W1={w1} C1-handle={h1} P1={pack1.id}")
    ev("BEFORE states (M1):")
    dump_state("M1-before-C1", child1, h1, w1)
    dump_state("M1-before-R1", root1, None, w1)
    before_c1 = instance_status(child1)
    before_r1 = instance_status(root1)
    before_t1 = task_row(w1)

    # ── Seed mission M2 (root R2 + two asker children) — for S3/S4 ────
    ev("seeding mission M2 (root R2, children CA/CB with distinct pending packs PA/PB, work W2 on R2)")
    root2 = seed_instance(InstanceStatus.PAUSED.value, None, "leader")
    w2 = seed_task(root2, TaskStatus.RUNNING.value)
    childA, packA, hA = seed_asker_child(root2, "PA: Choose X or Y?")
    childB, packB, hB = seed_asker_child(root2, "PB: Choose P or Q?")
    ev(f"M2 ids: R2={root2} CA={childA} CB={childB} W2={w2} PA={packA.id} PB={packB.id}")

    # ══ S1: job-scoped answer WITH pack id → child asker ══════════════
    ev("═══ S1: POST job-scoped answer WITH pack id ═══")
    s1_path = answer_path.replace("{work_id}", w1)
    s1_req = {"answers": {"q1": "Approach A (p9-smoke answer)"}, "question_pack_id": pack1.id}
    ev(f"S1 request: POST {s1_path} body={s1_req}")
    r1 = client.post(s1_path, json=s1_req)
    ev(f"S1 response: HTTP {r1.status_code}")
    ev(f"S1 body verbatim: {r1.text}")
    assert r1.status_code == 200, f"S1 expected HTTP 200, got {r1.status_code}: {r1.text}"
    s1_body = r1.json()
    ev(f"S1 parsed: status={s1_body.get('status')!r} instance_id={s1_body.get('instance_id')!r} "
       f"answer_target={s1_body.get('answer_target')!r} work_id={s1_body.get('work_id')!r}")
    assert s1_body.get("instance_id") == child1, (
        f"S1 delivery target mismatch: expected child {child1}, got {s1_body.get('instance_id')}"
    )
    time.sleep(3.0)  # let the scheduled background resume attempt play out (observation)
    ev("AFTER states (M1):")
    dump_state("M1-after-C1", child1, h1, w1)
    dump_state("M1-after-R1", root1, None, w1)
    after_c1 = instance_status(child1)
    after_r1 = instance_status(root1)
    after_t1 = task_row(w1)
    ev(f"S1 DB transition child C1: {before_c1!r} -> {after_c1!r}")
    ev(f"S1 DB transition root  R1: {before_r1!r} -> {after_r1!r} (noted, not gated)")
    ev(f"S1 DB transition C1 handle task {h1}: {task_row(h1).status!r} "
       f"(suspension_reason={task_row(h1).suspension_reason!r})")
    ev(f"S1 DB transition work  task {w1}: {before_t1.status!r} -> {after_t1.status!r}")
    p1_after = qm.get_question_pack(child1)
    ev(f"S1 RAM pack on C1 after: id={getattr(p1_after, 'id', None)} "
       f"status={getattr(p1_after, 'status', None)}")
    ev(f"S1 durable shadow on C1 after consumption: "
       f"{inst_repo.get_metadata_value(child1, 'question_pack_id')!r} (expect None)")

    # ══ S2: idempotent retry — same answer again ══════════════════════
    ev("═══ S2: POST the SAME answer again (idempotency) ═══")
    r2 = client.post(s1_path, json=s1_req)
    ev(f"S2 response: HTTP {r2.status_code}")
    ev(f"S2 body verbatim: {r2.text}")
    assert r2.status_code == 200, f"S2 expected HTTP 200, got {r2.status_code}: {r2.text}"
    s2_body = r2.json()
    assert s2_body.get("status") == "already_delivered", (
        f"S2 expected already_delivered, got: {r2.text}"
    )
    ev(f"S2 parsed: status={s2_body.get('status')!r} instance_id={s2_body.get('instance_id')!r} "
       f"resume_route={s2_body.get('resume_route')!r}")

    # ══ S3: ambiguity — two pending packs, no pack id ═════════════════
    ev("═══ S3: POST WITHOUT pack id into a two-packer mission (ambiguity) ═══")
    s3_path = answer_path.replace("{work_id}", w2)
    s3_req = {"answers": {"q1": "no pack id supplied (p9-smoke)"}}
    ev(f"S3 request: POST {s3_path} body={s3_req}")
    r3 = client.post(s3_path, json=s3_req)
    ev(f"S3 response: HTTP {r3.status_code}")
    ev(f"S3 body verbatim: {r3.text}")
    assert r3.status_code == 400, f"S3 expected HTTP 400, got {r3.status_code}: {r3.text}"
    s3_body = r3.json()
    s3_detail = s3_body.get("detail", {})
    s3_details = s3_detail.get("details", {}) if isinstance(s3_detail, dict) else {}
    cand = s3_details.get("candidate_ids")
    assert s3_detail.get("code") == "INVALID_REQUEST", (
        f"S3 expected code INVALID_REQUEST, got: {s3_detail.get('code')!r}"
    )
    assert isinstance(cand, list) and set(cand) == {childA, childB}, (
        f"S3 candidates wrong: {cand} (expected [{childA}, {childB}])"
    )
    ev(f"S3 parsed: code={s3_detail.get('code')!r} candidate_ids={cand} (both children named)")

    # ══ S4: the REAL job_answer tool lane ═════════════════════════════
    ev("═══ S4: job_answer tool via the REAL production factory ═══")
    from daemon.tools.instance import create_job_tools_if_available

    tools = create_job_tools_if_available(manager, current_instance_id="", agent_id="worker")
    assert tools, "create_job_tools_if_available returned no tools — job services not wired"
    job_answer = next(t for t in tools if t.name == "job_answer")
    ev(f"S4 factory produced {len(tools)} tools; selected by name: job_answer")
    s4_result = run_tool_on_app_loop(
        job_answer.ainvoke,
        {"work_id": w2,
         "answers": {"q1": "Choose X (p9-smoke tool answer)"},
         "question_pack_id": packA.id},
    )
    ev(f"S4 tool output verbatim: {s4_result}")
    assert s4_result.get("status") == "answered", f"S4 expected answered, got: {s4_result}"
    assert s4_result.get("instance_id") == childA, (
        f"S4 delivery target mismatch: expected {childA}, got {s4_result.get('instance_id')}"
    )
    ev(f"S4 DB after: CA status={instance_status(childA)!r} CB status={instance_status(childB)!r} "
       f"R2 status={instance_status(root2)!r}")
    pA_after = qm.get_question_pack(childA)
    ev(f"S4 RAM pack on CA after: status={getattr(pA_after, 'status', None)}")
    ev("═══ all four scenarios behaved as specified ═══")
    result, detail = "PASS", ""
except SmokeTimeout:
    result, detail = "TIMEOUT", ""
except AssertionError as e:
    result, detail = "FAIL", str(e)
except Exception as e:  # noqa: BLE001
    import traceback

    result, detail = "FAIL", f"{type(e).__name__}: {e}"
    ev("unexpected exception:\n" + traceback.format_exc())
finally:
    cleanup_ok = False
    try:
        cleanup_ok = cleanup()
    except Exception as e:  # noqa: BLE001
        ev(f"CLEANUP ERROR: {type(e).__name__}: {e}")
    try:
        client.__exit__(None, None, None)
        ev("app shutdown complete (lifespan exit)")
    except Exception as e:  # noqa: BLE001
        ev(f"app shutdown note: {type(e).__name__}: {e}")
    if result == "PASS" and not cleanup_ok:
        result, detail = "FAIL", "residue non-zero after cleanup"

sys.exit(finish(result, detail))
