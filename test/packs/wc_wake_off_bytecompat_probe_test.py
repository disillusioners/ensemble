#!/usr/bin/env python3
"""Byte-Compat Probe — wc-wake phase-1 gate, OFF-state kill-switch revert proof.

Drives the three wc-wake routing sites (HTTP POST /messages, agent-tool
``send_message``, ``job_inject``) on a WAITING_CHILDREN target under
the kill-switch default-OFF state (``ENSEMBLE_WC_WAKE_ENQUEUE`` unset or
explicitly ``0``) and prints a JSON record of every captured byte.

The probe is parameterized by the ``DAEMON_BYTECOMPAT_ROOT`` env var —
the root directory whose ``daemon/`` should be imported. The wrapper
shell script runs the probe twice, once against the HEAD repo's daemon
and once against a git worktree pinned at base ``1f8f8ed4``, then
diff-compares the two outputs. Any byte diff is a byte-compat
REGRESSION (C1-Q2 + decisions.md D2.5-FLIP / C2-D2.5-FLIP — OFF is the
instant-revert path, so OFF must be byte-faithful to base).

Mirrors the origin_contract_e2e_probe pattern
(``test/packs/origin_contract_e2e_probe_test.py``):

  * Real router/tool code — no MagicMock of the routing decision.
  * Stubbed manager + enqueue — captured return values drive the
    expected byte shape.
  * ASGITransport for HTTP (in-process, no ports).
  * Inline ``create_instance_tools`` invocation for the agent-tool and
    job_inject sites — captures the closure's ``.coroutine`` directly.

Cross-tree import feasibility (per ``base-evidence attribution via
worktree A/B pytest comparison`` skill):
  * The venv's editable .pth points at the main repo's daemon.
  * PYTHONPATH alone is NOT sufficient — the import-finder cache
    favors the main-repo path even when /tmp/wcbase-test precedes it
    on ``sys.path``. We force the worktree path to position 0 via
    ``sys.path.insert(0, root)`` AFTER clearing any cached daemon
    modules — proven to load the worktree's ``daemon`` package.
  * PROOF is printed inline (``daemon.__file__``) for each run so the
    report carries the resolution proof.

Output: a single JSON document on stdout containing per-site captures.
The shell wrapper writes this to ``head.json`` and ``base.json`` and
diff-compares them.

Exit codes:
  0   PASS (every site byte-compat AND daemon resolution prints the
        expected root)
  1   FAIL (any assertion failed; the JSON record's ``ok`` field
        tells the story)
  124 internal timeout (signal.alarm)

Per the test-pack skill, this probe is paired with a 300s command-level
wrapper and 280s self-guard.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ── Layer 2 inner guard (signal.alarm) ─────────────────────────────────────

INTERNAL_TIMEOUT_S = 280


class TimeoutError_(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError_(f"internal timeout after {INTERNAL_TIMEOUT_S}s")


# ── Cross-tree import resolution ────────────────────────────────────────────

if "DAEMON_BYTECOMPAT_ROOT" not in os.environ:
    print("ERROR: DAEMON_BYTECOMPAT_ROOT not set", file=sys.stderr)
    sys.exit(1)

DAEMON_ROOT = os.environ["DAEMON_BYTECOMPAT_ROOT"]

# Force the worktree (or any caller-chosen root) ahead of the venv's
# .pth-installed main-repo path. Clear any cached daemon modules first
# so we don't bind to the wrong identity.
for k in list(sys.modules.keys()):
    if k == "daemon" or k.startswith("daemon."):
        del sys.modules[k]
sys.path.insert(0, DAEMON_ROOT)

# Import AFTER the path manipulation so the resolution proof holds.
import daemon  # noqa: E402

DAEMON_FILE = daemon.__file__ or "<unknown>"
# Resolution proof: daemon.__file__ must be rooted at DAEMON_ROOT.
# If not, fail loud — never let a cross-tree regression hide behind
# a green probe.
if not DAEMON_FILE.startswith(DAEMON_ROOT):
    print(
        f"ERROR: daemon.__file__={DAEMON_FILE!r} does not start with "
        f"DAEMON_BYTECOMPAT_ROOT={DAEMON_ROOT!r}; cross-tree import failed",
        file=sys.stderr,
    )
    sys.exit(1)


# ── Record schema ───────────────────────────────────────────────────────────
#
# {
#   "daemon_root": "<DAEMON_ROOT>",
#   "daemon_file": "<daemon.__file__>",
#   "flag": "<unset|0>",
#   "sites": {
#     "http":    {"status": <int>, "body": <dict>},
#     "agent_tool": {"text": <str>},
#     "job_inject_wc":   <dict>,    # injection shape (status="injected")
#     "job_inject_idle": <dict>     # legacy eligibility error string
#   },
#   "ok": True
# }

record: dict = {
    "daemon_root": DAEMON_ROOT,
    "daemon_file": DAEMON_FILE,
    "flag": os.environ.get("ENSEMBLE_WC_WAKE_ENQUEUE", "<unset>"),
    "sites": {},
    "ok": True,
}

# ── Manager stub (one instance, shared across the three sites) ────────────
#
# Status=waiting_children so all three sites take the legacy flag-OFF
# injection branch (HTTP returns 202, agent-tool returns the legacy
# text, job_inject returns the injection shape).

STUB_STATUS = "waiting_children"
STUB_INSTANCE_ID = "wc-bytecompat-stub"


def _make_manager_stub() -> MagicMock:
    """Build the minimal manager stub the three sites consume.

    Mirrors ``tests/helpers/send_message_fixtures.make_send_message_manager``
    but trimmed to what the byte-compat probe actually exercises. The
    stub records every call so the captured bytes are deterministic.

    Every method the production code ``await``s MUST be an AsyncMock;
    methods called without ``await`` stay plain MagicMock. This matches
    the make_send_message_manager fixture's convention exactly.
    """
    manager = MagicMock()
    manager.is_write_paused = False
    manager.get_instance = AsyncMock(
        return_value=MagicMock(instance_id=STUB_INSTANCE_ID)
    )
    manager.find_near_instance = MagicMock(return_value=[])
    manager.get_instance_info = MagicMock(
        return_value={"status": STUB_STATUS, "agent_id": "developer"}
    )
    manager.set_injection = MagicMock(
        return_value={"content": "WAKE", "timestamp": "2026-08-30T00:00:00Z"}
    )
    manager.get_injection_count = MagicMock(return_value=1)
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(status=STUB_STATUS, instance_id=STUB_INSTANCE_ID)
    )
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-bytecompat", queued=False)
    )
    manager.enqueue_message_job = MagicMock(
        return_value=MagicMock(message_id="msg-bytecompat-job", queued=False)
    )
    manager.get_agent_tool_revive_count = MagicMock(return_value=0)
    manager.note_agent_tool_revive = MagicMock(return_value=1)
    # job_inject's job_service needs a get_work coroutine.
    caller_instance = SimpleNamespace(
        instance_id="caller-iid",
        project_id="probe-project",
        agent_id="developer",
    )
    job_service = MagicMock()
    job_service.get_work = AsyncMock(
        return_value=SimpleNamespace(
            job_id="job-bytecompat",
            instance_id=STUB_INSTANCE_ID,
            project_id="probe-project",
            agent_id="developer",
        )
    )
    job_service.get_message_status = AsyncMock(return_value=None)
    manager._job_queue_service = job_service
    manager._task_repo = None  # job_inject busy pre-check uses this; None skips.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(
            status=STUB_STATUS,
            instance_id=STUB_INSTANCE_ID,
            project_id="probe-project",
            agent_id="developer",
        )
    )
    manager._instance_repository.get_tree_ids = MagicMock(return_value=[])
    manager.get_instance = AsyncMock(return_value=caller_instance)
    manager.resume_instance_cascade = AsyncMock(
        return_value={"target_id": STUB_INSTANCE_ID}
    )
    manager.resume_processing_job = MagicMock(return_value=None)
    return manager


# ── HTTP site: POST /instances/{id}/messages ────────────────────────────────

def drive_http(manager: MagicMock) -> dict:
    """Drive the real ``daemon.routers.messages.send_message`` endpoint.

    Mirrors origin_contract_e2e_probe_test.build_app — minimal FastAPI
    app that mounts ONLY the messages router with a stubbed manager
    (real router code, real Pydantic validation, stubbed downstream).
    """
    from fastapi import FastAPI
    from daemon.routers.messages import (
        MessageCreate,
        send_message as http_send_message,
    )
    from starlette.requests import Request
    from starlette.responses import Response

    app = FastAPI()
    app.state.manager = manager
    app.state.live_hub = None

    # Construct a minimal Request/Response pair (the endpoint reads
    # manager off ``request.app.state.manager`` and sets status_code).
    request = Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": f"/instances/{STUB_INSTANCE_ID}/messages",
            "headers": [],
            "query_string": b"",
        }
    )
    response = Response()

    captured: dict = {}

    async def _drive():
        body = await http_send_message(
            instance_id=STUB_INSTANCE_ID,
            message=MessageCreate(content="WAKE"),
            request=request,
            response=response,
        )
        captured["status"] = response.status_code
        captured["body"] = body
        return captured

    result = asyncio.run(_drive())
    return result


# ── Agent-tool site: tools.instance.send_message ────────────────────────────

def drive_agent_tool(manager: MagicMock) -> dict:
    """Drive the real ``send_message`` tool closure on a WC target.

    Uses the production ``create_instance_tools`` factory with the
    factory-helper stack patched out (mirrors the test pattern in
    ``tests/helpers/send_message_fixtures.patch_heavy_helpers``).
    Captures the closure's ``.coroutine`` and invokes it directly.
    """
    from daemon.tools.instance import create_instance_tools

    patches = [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch(
            "daemon.tools.instance._apply_tool_filter",
            side_effect=lambda tools, *a, **kw: tools,
        ),
    ]
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "caller-iid", "developer")
    finally:
        for p in reversed(patches):
            p.stop()

    send_tool = next(
        (t for t in tools if getattr(t, "name", None) == "send_message"),
        None,
    )
    if send_tool is None:
        return {"text": None, "error": "send_message tool not in create_instance_tools output"}

    with patch(
        "daemon.tools.instance._check_team_membership", return_value=None
    ):
        result = asyncio.run(send_tool.coroutine(STUB_INSTANCE_ID, "WAKE"))

    return {"text": result}


# ── job_inject site: tools.job_queue ────────────────────────────────────────

def drive_job_inject(manager: MagicMock, *, target_status: str) -> dict:
    """Drive the real ``job_inject`` tool closure on the given target status.

    ``target_status`` controls which branch the tool takes:

      * ``"waiting_children"`` — under flag OFF, hits the legacy
        ``set_injection`` path (returns ``{status: "injected", ...}``).
      * anything else (e.g. ``"idle"``) — hits the eligibility-error
        path. Under flag OFF, returns the LEGACY error string with
        the wording "only works on RUNNING or WAITING_CHILDREN
        instances. Use job_continue for IDLE/terminal instances."
        This is the "byte-restored error string" deliverable — the
        pre-T7 byte-faithful legacy text the kill-switch revert
        path must preserve.

    Unlike ``daemon.tools.instance.create_instance_tools``,
    ``create_job_tools`` has no RAG/knowledge/MCP factory-helper stack
    to patch out — the module is self-contained. We just build the
    closure with our stubbed manager + job_service.
    """
    from daemon.tools.job_queue import create_job_tools

    # Re-stub the target status on the manager + instance_repository
    # so the eligibility check sees the requested status. Note: the
    # _check_job_access helper queries ``_instance_repository.get`` with
    # ``current_instance_id`` (the CALLER, not the target) — so we
    # branch on the instance id to return a complete caller shape
    # (with project_id) for the caller, and the target-shape (without
    # project_id; job_inject does not consult it) for the target.
    def _instance_lookup(instance_id):
        if instance_id == "caller-iid":
            return SimpleNamespace(
                status="running",
                instance_id="caller-iid",
                project_id="probe-project",
                agent_id="developer",
            )
        return SimpleNamespace(
            status=target_status,
            instance_id=STUB_INSTANCE_ID,
            project_id="probe-project",
            agent_id="developer",
        )

    manager.get_instance_info = MagicMock(
        return_value={"status": target_status, "agent_id": "developer"}
    )
    manager._instance_repository.get = MagicMock(side_effect=_instance_lookup)

    tools = create_job_tools(
        manager._job_queue_service,
        queue_mgmt_service=None,
        dead_letter_service=None,
        current_instance_id="caller-iid",
        agent_id="developer",
        manager=manager,
    )

    job_inject = next(
        (t for t in tools if getattr(t, "name", None) == "job_inject"),
        None,
    )
    if job_inject is None:
        return {"error": "job_inject tool not in create_job_tools output"}

    result = asyncio.run(
        job_inject.coroutine(job_id="job-bytecompat", message="WAKE")
    )
    return dict(result) if isinstance(result, dict) else {"result": str(result)}


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(INTERNAL_TIMEOUT_S)

    manager = _make_manager_stub()

    try:
        # (a) HTTP
        record["sites"]["http"] = drive_http(manager)
        # (b) agent-tool
        record["sites"]["agent_tool"] = drive_agent_tool(manager)
        # (c-1) job_inject on a WC target — captures the legacy
        #       ``set_injection`` shape (status="injected" + pending_count).
        record["sites"]["job_inject_wc"] = drive_job_inject(
            manager, target_status="waiting_children"
        )
        # (c-2) job_inject on an IDLE target — captures the legacy
        #       eligibility error STRING (the byte-restored error
        #       deliverable). Quotes the exact string at both trees
        #       so the report can prove byte-compat.
        record["sites"]["job_inject_idle"] = drive_job_inject(
            manager, target_status="idle"
        )
    except TimeoutError_ as exc:
        record["ok"] = False
        record["error"] = f"TIMEOUT: {exc}"
        print(json.dumps(record, indent=2, default=str))
        return 124
    except Exception as exc:
        record["ok"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(record, indent=2, default=str))
        return 1

    print(json.dumps(record, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
