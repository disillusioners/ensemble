#!/usr/bin/env python3
"""mock_be.py — stdlib-only mock backend for the job-queue indicator smoke.

Serves the exact wire shapes the FE services parse (verified against
frontend/src/app/services/{instance,mission,job}.service.ts and
frontend/src/app/models/{index,mission.model,job.model,defer-blocked.model}.ts):

  GET /api/instances?limit&offset&exclude_kb&include_descendants&order
      -> InstanceListResponse {instances, total, limit, offset, has_more}
  GET /api/jobs?status=queued,active            -> {jobs: [...]}
  GET /api/jobs?status=completed,...&limit=10   -> {jobs: [...]}
  GET /api/missions?liveness=...&limit=20
      -> MissionListResponse {missions, total, limit, offset, has_more, degraded}
  GET /api/queues/defer-blocked -> DeferBlockedStatus {defer_blocked, pending_count, holders}

Env:
  MOCK_PORT  (default 10080; binds 127.0.0.1)
  DATASET    "mixed" (default) | "allidle"

Unknown paths -> 404 {"detail": "not mocked"}. Query strings are ignored for
matching (path-only). Every request is logged to stderr with the dataset tag.
Self-timeout: exits after 600s via threading.Timer (inner guard; the outer
guard is the caller's `timeout 300` on the browser-drive phase).
"""
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

PORT = int(os.environ.get("MOCK_PORT", "10080"))
DATASET = os.environ.get("DATASET", "mixed").strip().lower()
SELF_TIMEOUT_S = int(os.environ.get("MOCK_SELF_TIMEOUT_S", "600"))

_NOW = datetime.now(timezone.utc)


def iso(seconds_ago: float) -> str:
    return (_NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ─────────────────────────────────────────────────────────────────────────────
# Instance rows — fields mirror InstanceInfo (frontend/src/app/models/index.ts).
# Tree nesting is driven by parent_id (models/instance-node.model.ts
# buildInstanceNodes); `children` is the pre-KB-strip wire list.
# ─────────────────────────────────────────────────────────────────────────────
def make_mixed_instances():
    idle_root_id = "mock-idle-root-0001"
    run_root_id = "mock-run-root-0002"
    idle_child_id = "mock-idle-child-0003"
    orphan_child_id = "mock-orphan-child-0004"
    done_root_id = "mock-done-root-0005"
    err_root_id = "mock-err-root-0006"
    return [
        {
            "instance_id": idle_root_id,
            "agent_id": "architect",
            "project_id": None,
            "status": "idle",                      # (a) must NOT appear
            "parent_id": None,
            "children": [orphan_child_id],
            "title": "MOCK IDLE-ROOT QW7",
            "instance_name": "MOCK IDLE-ROOT QW7",
            "created_at": iso(3600),
            "updated_at": iso(3000),
            "pinned": False,
        },
        {
            "instance_id": run_root_id,
            "agent_id": "coder",
            "project_id": None,
            "status": "running",                   # (b) must appear
            "parent_id": None,
            "children": [idle_child_id],
            "title": "MOCK RUN-ROOT RW2",
            "instance_name": "MOCK RUN-ROOT RW2",
            "created_at": iso(3500),
            "updated_at": iso(10),
            "pinned": False,
        },
        {
            "instance_id": idle_child_id,
            "agent_id": "tester",
            "project_id": None,
            "status": "idle",                      # (c) must NOT appear
            "parent_id": run_root_id,
            "children": [],
            "title": "MOCK IDLE-CHILD QC3",
            "instance_name": "MOCK IDLE-CHILD QC3",
            "created_at": iso(3400),
            "updated_at": iso(2900),
            "pinned": False,
        },
        {
            "instance_id": orphan_child_id,
            "agent_id": "worker",
            "project_id": None,
            "status": "waiting_children",          # (d) live child of filtered
            "parent_id": idle_root_id,             #     idle parent -> promotes
            "children": [],                        #     to a visible root
            "title": "MOCK ORPHAN-CHILD OP9",
            "instance_name": "MOCK ORPHAN-CHILD OP9",
            "created_at": iso(3300),
            "updated_at": iso(20),
            "pinned": False,
        },
        {
            "instance_id": done_root_id,
            "agent_id": "writer",
            "project_id": None,
            "status": "completed",                 # (e) terminal stays visible
            "parent_id": None,
            "children": [],
            "title": "MOCK DONE-ROOT DN5",
            "instance_name": "MOCK DONE-ROOT DN5",
            "created_at": iso(7200),
            "updated_at": iso(1800),
            "pinned": False,
        },
        {
            "instance_id": err_root_id,
            "agent_id": "fixer",
            "project_id": None,
            "status": "error",                     # (f) terminal stays visible
            "parent_id": None,
            "children": [],
            "title": "MOCK ERR-ROOT ER8",
            "instance_name": "MOCK ERR-ROOT ER8",
            "created_at": iso(7000),
            "updated_at": iso(1700),
            "pinned": False,
        },
    ]


def make_allidle_instances():
    return [
        {
            "instance_id": "mock-allidle-root-a",
            "agent_id": "architect",
            "project_id": None,
            "status": "idle",
            "parent_id": None,
            "children": [],
            "title": "MOCK ALLIDLE-ROOT A1",
            "instance_name": "MOCK ALLIDLE-ROOT A1",
            "created_at": iso(3600),
            "updated_at": iso(3000),
            "pinned": False,
        },
        {
            "instance_id": "mock-allidle-root-b",
            "agent_id": "writer",
            "project_id": None,
            "status": "idle",
            "parent_id": None,
            "children": [],
            "title": "MOCK ALLIDLE-ROOT B2",
            "instance_name": "MOCK ALLIDLE-ROOT B2",
            "created_at": iso(3500),
            "updated_at": iso(2900),
            "pinned": False,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Job receipts — fields mirror Job (frontend/src/app/models/job.model.ts).
# Grouping key on the FE: `mission_id ?? instance_id` at any tree depth.
# BE list wire ships mission_id: null for child-bound rows -> the orphan-child
# receipt below reproduces that real shape (FE falls back to instance_id).
# ─────────────────────────────────────────────────────────────────────────────
def make_mixed_jobs():
    active = [
        {
            "job_id": "mock-job-run-root-active",
            "agent_id": "coder",
            "message": "MOCK-RECEIPT active on RUN-ROOT",
            "project_id": None,
            "priority": 5,
            "status": "processing",
            "created_at": iso(120),
            "started_at": iso(110),
            "completed_at": None,
            "instance_id": "mock-run-root-0002",
            "error_message": None,
            "result_summary": None,
            "cancelled_at": None,
            "mission_id": "mock-run-root-0002",
            "mission_ref": None,
            "job_type": "message",
            "kind": "job",
            "job_metadata": {"instance_name": "MOCK-RCPT-RUNROOT J1"},
        },
        {
            "job_id": "mock-job-orphan-child-active",
            "agent_id": "worker",
            "message": "MOCK-RECEIPT active on ORPHAN-CHILD",
            "project_id": None,
            "priority": 5,
            "status": "pending",
            "created_at": iso(60),
            "started_at": None,
            "completed_at": None,
            "instance_id": "mock-orphan-child-0004",
            "error_message": None,
            "result_summary": None,
            "cancelled_at": None,
            "mission_id": None,          # real BE child-bound list-wire shape
            "mission_ref": None,
            "job_type": "message",
            "kind": "job",
            "job_metadata": {"instance_name": "MOCK-RCPT-ORPHAN K2"},
        },
    ]
    recent = [
        {
            "job_id": "mock-job-idle-root-receipt",
            "agent_id": "architect",
            "message": "MOCK-RECEIPT bound to hidden IDLE-ROOT (must still surface)",
            "project_id": None,
            "priority": 5,
            "status": "completed",
            "created_at": iso(2400),
            "started_at": iso(2350),
            "completed_at": iso(2300),
            "instance_id": "mock-idle-root-0001",   # hidden instance
            "error_message": None,
            "result_summary": "done",
            "cancelled_at": None,
            "mission_id": "mock-idle-root-0001",    # grouping key -> filtered
            "mission_ref": None,                    # node -> recentFlat
            "job_type": "message",
            "kind": "job",
            "job_metadata": {"instance_name": "MOCK-RCPT-IDLEPARENT Z4"},
        },
    ]
    return active, recent


def make_mixed_missions():
    return [
        {
            "mission_id": "mock-run-root-0002",
            "agent_id": "coder",
            "parent_mission_id": None,
            "liveness": "processing",
            "terminal_reason": None,
            "epoch": 1,
            "linked_jobs": ["mock-job-run-root-active"],
            "started_at": iso(120),
            "last_activity_at": iso(10),
            "title": "MOCK RUN-ROOT RW2 mission",
            "initiative_preview": None,
        },
        {
            "mission_id": "mock-orphan-child-0004",
            "agent_id": "worker",
            "parent_mission_id": "mock-idle-root-0001",
            "liveness": "pending",
            "terminal_reason": None,
            "epoch": 1,
            "linked_jobs": ["mock-job-orphan-child-active"],
            "started_at": iso(60),
            "last_activity_at": iso(20),
            "title": "MOCK ORPHAN-CHILD OP9 mission",
            "initiative_preview": None,
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────

def dataset_payloads():
    if DATASET == "allidle":
        return {
            "instances": make_allidle_instances(),
            "active_jobs": [],
            "recent_jobs": [],
            "missions": [],
        }
    active, recent = make_mixed_jobs()
    return {
        "instances": make_mixed_instances(),
        "active_jobs": active,
        "recent_jobs": recent,
        "missions": make_mixed_missions(),
    }


PAYLOADS = dataset_payloads()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence default per-line stderr
        pass

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        qs = parse_qs(split.query)
        print(f"[mock dataset={DATASET}] GET {path} qs={split.query}", file=sys.stderr, flush=True)
        try:
            if path == "/api/instances":
                self._send(200, {
                    "instances": PAYLOADS["instances"],
                    "total": len(PAYLOADS["instances"]),
                    "limit": int(qs.get("limit", ["10"])[0]),
                    "offset": int(qs.get("offset", ["0"])[0]),
                    "has_more": False,
                })
            elif path == "/api/jobs":
                status = qs.get("status", ["queued,active"])[0]
                if "queued" in status or "active" in status:
                    self._send(200, {"jobs": PAYLOADS["active_jobs"]})
                else:
                    self._send(200, {"jobs": PAYLOADS["recent_jobs"]})
            elif path == "/api/missions":
                self._send(200, {
                    "missions": PAYLOADS["missions"],
                    "total": len(PAYLOADS["missions"]),
                    "limit": int(qs.get("limit", ["20"])[0]),
                    "offset": 0,
                    "has_more": False,
                    "degraded": False,
                })
            elif path == "/api/queues/defer-blocked":
                self._send(200, {"defer_blocked": False, "pending_count": 0, "holders": []})
            elif path == "/api/notifications/stream" or path.startswith("/ws"):
                # EventSource / websocket endpoints — deliberately unmocked.
                self._send(404, {"detail": "not mocked"})
            else:
                self._send(404, {"detail": "not mocked"})
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    timer = threading.Timer(SELF_TIMEOUT_S, server.shutdown)
    timer.daemon = True
    timer.start()
    print(f"[mock] dataset={DATASET} listening on http://127.0.0.1:{PORT} "
          f"(self-timeout {SELF_TIMEOUT_S}s)", file=sys.stderr, flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        timer.cancel()
        server.server_close()
        print("[mock] shutdown", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
