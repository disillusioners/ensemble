#!/usr/bin/env python3
"""Mock OpenAI-compatible LLM endpoint for LCA advisory-note-removal boot smoke.

Serves /v1/chat/completions on 127.0.0.1:<MOCK_LLM_PORT> (default 15820).

Designed for the LCA advisory-note-removal merge gate
(branch feature/lca-remove-advisory-note @ 0a4fccb1, base 858b1038):

The DELETED producer (daemon/services/child_reports.py note-mint block,
removed in commit 6a695b8f) used to attach a "Child Report Check" note to
the leader's window whenever a child's terminal report contained
"promise-while-stopping" markers. Post-removal, the A-band fires
DIFFERENTLY — the resolver scans the leader's in-context
``internal_report:<child_iid>:<msg_id>`` HumanMessages for the 17-pattern
catalog at gate-evaluation time (commit 1ad924d7). NO separate advisory
note is minted anywhere.

This pack's role is to PROVE the new arc end-to-end through the real
daemon: the child LIES on its terminal report (its outgoing text contains
catalog markers), the lie is stamped into the leader's window as
``internal_report:`` (the live producer), and the gate fires A-band via
the transcript scan — NOT via a minted note.

Role detection (same key phrases as the lcau/lca2 helpers — confirmed
intact at 0a4fccb1):

- judge:  system prompt contains "fused evidence bundle"
          verdict scripting: #1 not_complete, #2+ complete
- leader: system prompt contains "strategic leader" or
          "coordinates specialized agents"
          scripted turns:
            #1 spawn_instance(agent=developer)
            #2 send_message(child_id, task)              OR final_report
            #3 send_message(child_id, status check)      OR final_report
            #4 final_report (gate fires → judge#1 → deny+nudge)
            #5 final_report (gate fires → judge#2 → ALLOW + END)
- developer: system prompt contains "Developer Agent" / "Coder Agent" /
             "working-lead coding agent" / "Developer (Working-Lead"
             LIAR turns — outgoing text contains catalog markers so the
             A-band trigger fires via the evaluation-time transcript
             scan (NO mint of a separate note):
               #1 "I've started the task. Will write the deliverable.
                   Ending turn."
               #2 "Still pending. Will write the deliverable in the next
                   turn. Ending turn."
               #3+ (only if leader pings again) "In progress — will write
                   the RESULTS file shortly. Ending turn."

The lie is the WHOLE POINT — the child tells the parent "I'm still
working, I'll deliver it soon" while the marker catalog catches the lie.
A-band fires from the transcript scan, the judge says "not complete", the
leader is nudged. On the leader's second attempt (turn 5) the child is
already terminal so the same markers remain in the window — but the
judge decides "complete" on the second call and the leader is allowed
through. The deny→allow arc IS the expected behavior; the lie is what
drives the deny on attempt 1.

Hard kill: 280s.
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_PORT = int(os.environ.get("MOCK_LLM_PORT", "15820"))
LOG_PATH = os.environ.get("MOCK_LLM_LOG", "/tmp/lcan-boot-smoke/mock_llm.log")
MAX_LIFETIME_S = int(os.environ.get("MOCK_LLM_MAX_LIFETIME_S", "280"))

_state_lock = threading.Lock()
_state = {
    "call_count": 0,
    "leader_turn_count": 0,
    "child_turn_count": 0,
    "judge_call_count": 0,
    "fired": [],
    "first_child_id": None,
    "child_id_recovery_log": [],
}

LOG = open(LOG_PATH, "a", buffering=1)


def _log(msg: str) -> None:
    LOG.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


CHILD_ID_PATTERNS = [
    re.compile(r'instance_id="([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"'),
    re.compile(r'"instance_id"\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"'),
    re.compile(r'\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b'),
]


def _recover_child_id(messages: list) -> str | None:
    """Recover the spawned child instance id from any prior tool result.

    The leader spawns via tool_call(spawn_instance) on turn 1; the
    instance_id comes back in a tool result message on turn 2. We
    pattern-match the 8-4-4-4-12 uuid from the most recent tool result
    text — exactly the lcau/lca2 helper approach (READ-ONLY inheritance).
    """
    attempts = []
    for m in messages:
        if m.get("role") == "tool":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(b.get("text", b)) if isinstance(b, dict) else str(b)
                    for b in content
                )
            text = str(content)
            for pat in CHILD_ID_PATTERNS:
                m2 = pat.search(text)
                if m2:
                    attempts.append((text[:120], m2.group(1)))
                    return m2.group(1)
            attempts.append((text[:120], None))
    with _state_lock:
        _state["child_id_recovery_log"].extend(attempts)
    return None


def _detect_role(system_content: str) -> str:
    """Detect the role from the system prompt content (production keys intact).

    Order matters — judge first (the "fused evidence bundle" phrase is the
    most specific; if it appears the call IS a fused judge invocation),
    then leader (the "strategic leader" phrase from agents/leader/soul.md:3),
    then the developer family (the post-rename developer agent identity is
    "I am a code orchestrator" from agents/developer/soul.md:3 — the legacy
    "coder" agent's "working-lead coding agent" identity is preserved as a
    fallback so the helper still detects both spellings). The lcau/lca2
    helpers' keys ("Developer Agent" / "Coder Agent") are KEPT as belt-and-
    suspenders — older prompt revisions referenced those phrases; the
    production developer soul uses "code orchestrator" + "opencode" (the
    opencode-skill is the developer's only tool, agents/developer/soul.md).
    """
    if "fused evidence bundle" in system_content:
        return "judge"
    if "strategic leader" in system_content or "coordinates specialized agents" in system_content:
        return "leader"
    if (
        "code orchestrator" in system_content
        or "working-lead coding agent" in system_content
        or "Developer Agent" in system_content
        or "Coder Agent" in system_content
        or "Developer (Working-Lead" in system_content
    ):
        return "coder"
    return "other"


def _msg_assistant(content: str, tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _judge_verdict(verdict: str) -> str:
    """Return the JSON envelope the FUSED JUDGE prompt expects.

    Mirrors the lcau/lca2 helper shape exactly — the field set is
    pinned by ``FUSED_JUDGE_SYSTEM_PROMPT`` (daemon/services/attestation_report_judge.py)
    and ``_parse_fused_judge_response``. The lie-detection rationale is
    phrased for the bundled A-band evidence (the catalog markers in the
    child's report).
    """
    if verdict == "complete":
        return json.dumps({
            "verdict": "complete",
            "evidence": [
                "leader enumerated per-child deliverables with concrete identifiers",
                "no promised-but-undelivered work in the transcript",
            ],
            "advisory": "",
        })
    return json.dumps({
        "verdict": "not_complete",
        "evidence": [
            "leader's transcript still contains promise-while-stopping markers in the "
            "child-report source-A section (catalog hits observed: 'will write', "
            "'ending turn', 'in progress', 'still pending' — the child reported more "
            "work but is going terminal)",
            "the child never confirmed the deliverable was actually written",
        ],
        "advisory": (
            "The leader is closing on a child report that still contains 'will write' / "
            "'ending turn' / 'in progress' markers — the child claimed work but did "
            "not confirm delivery. Wait for a follow-up report that explicitly states "
            "the deliverable is written and verified, then attest."
        ),
    })


def _final_report_text(attempt: int) -> str:
    """Leader's completion text — crafted to NOT carry catalog markers.

    The A-band trigger fires from the CHILD'S internal_report: messages
    in the leader window (NOT from the leader's own text). The leader's
    final_report prose is deliberately marker-free so the B-band
    (leader-side marker_hit / length_trigger) does NOT fire — only the
    A-band fires, the witness row shows terms_fired includes a_suspicion,
    and the deny is purely a-band-driven (the architectural under-test
    point: the A-band fires from the transcript scan alone, not from a
    separately minted note).
    """
    if attempt == 1:
        return (
            "Final report — the delegated developer child has reported back with its "
            "status. Mission assessment: the work products are documented in the "
            "project files per the developer's confirmation. All delegated tasks are "
            "accounted for, and the team has finalized the deliverables. The mission "
            "is genuinely complete and verified at the leader level."
        )
    return (
        "Final report (second attempt, post-nudge). The delegated developer child "
        "has confirmed all requested artifacts are present in the project files. "
        "Mission assessment: deliverables are documented and verified. All delegated "
        "tasks are accounted for, the team has finalized the deliverables, and no "
        "follow-ups are outstanding. The mission is genuinely complete and verified."
    )


def _child_lie_text(turn: int) -> str:
    """Developer/child terminal-report LIE.

    The text contains 4 catalog markers per turn (well over the marker
    catalog's per-entry threshold — phrases chosen to MATCH the live
    CHILD_TERMINAL_PROMISE_MARKERS catalog in
    daemon/services/attestation_marker_scanner.py:258-294 verbatim, not
    paraphrased). The markers are present in the OUTGOING text, which
    becomes an internal_report: HumanMessage in the leader's window.
    The evaluation-time transcript scan (post-removal, commit 1ad924d7)
    finds the markers and fires A-band via a_suspicion=True.

    The leader's final_report text (above) is deliberately marker-free
    so B-band does NOT fire — the A-band fires alone from the lie.
    """
    if turn == 1:
        # First lie — strongest signal set so the catalog-scan finds it
        # immediately on the first child terminal report.
        return (
            "I've started the implementation. Will write the deliverable in the "
            "project files shortly. The task is in progress. Ending turn now and "
            "will continue the next iteration."
        )
    if turn == 2:
        # Second lie — same marker families, slightly different phrasing
        # so the catalog still hits.
        return (
            "Still pending — the deliverable has not been finalized yet. Will write "
            "the RESULTS file in the next turn. Awaiting confirmation before "
            "closing. Ending my turn here."
        )
    # turn >= 3 — third+ lie (defensive; leader shouldn't ping this many times)
    return (
        "In progress — will write the deliverable now. The task is interim and "
        "standby until the next pass. To be continued."
    )


def _tool_call_spawn() -> list:
    return [{
        "id": "call_spawn_1",
        "type": "function",
        "function": {
            "name": "spawn_instance",
            "arguments": json.dumps({"agent_id": "developer", "instance_name": "lcan-child"}),
        },
    }]


def _tool_call_send(instance_id: str, msg: str, call_id: str) -> list:
    return [{
        "id": call_id,
        "type": "function",
        "function": {
            "name": "send_message",
            "arguments": json.dumps({"instance_id": instance_id, "message": msg}),
        },
    }]


def _decide(request_body: dict) -> tuple[dict, str]:
    with _state_lock:
        _state["call_count"] += 1
        n = _state["call_count"]

    messages = request_body.get("messages", []) or []
    system_content = " ".join(
        str(m.get("content", "")) for m in messages if m.get("role") == "system"
    )
    role = _detect_role(system_content)
    child_id = _recover_child_id(messages)

    if role == "judge":
        with _state_lock:
            _state["judge_call_count"] += 1
            jn = _state["judge_call_count"]
        if jn == 1:
            content = _judge_verdict("not_complete")
            label = f"JUDGE#{jn}→not_complete"
        else:
            content = _judge_verdict("complete")
            label = f"JUDGE#{jn}→complete"
        resp_msg = _msg_assistant(content)
    elif role == "leader":
        # Derive leader's "turn within this conversation" from message history
        # — robust to ANY prior leader sharing the mock state (turns restart
        # at 1 for each NEW leader session because each session's first turn
        # has 0 AIMessages in its request).
        ai_msg_count = sum(1 for m in messages if m.get("role") == "assistant")
        ln = ai_msg_count + 1  # 1-indexed
        with _state_lock:
            _state["leader_turn_count"] = ln
            if child_id and not _state["first_child_id"]:
                _state["first_child_id"] = child_id
        cid = child_id or _state["first_child_id"]

        if ln == 1:
            content = "I'll delegate this task to a developer child instance."
            tcs = _tool_call_spawn()
            resp_msg = _msg_assistant(content, tcs)
            label = f"LEADER#{ln}→spawn_child"
        elif ln == 2:
            if not cid:
                content = _final_report_text(1)
                resp_msg = _msg_assistant(content)
                label = f"LEADER#{ln}→final_report_attempt_1"
            else:
                content = "Sending the task to the child."
                tcs = _tool_call_send(cid, "Please complete the delegated task and report back when done.", "call_send_1")
                resp_msg = _msg_assistant(content, tcs)
                label = f"LEADER#{ln}→send_to_child({cid[:8]})"
        elif ln == 3:
            if not cid:
                content = _final_report_text(2)
                resp_msg = _msg_assistant(content)
                label = f"LEADER#{ln}→final_report_attempt_2"
            else:
                content = "Checking status."
                tcs = _tool_call_send(cid, "Status check — please confirm completion.", "call_status_1")
                resp_msg = _msg_assistant(content, tcs)
                label = f"LEADER#{ln}→status_check({cid[:8]})"
        elif ln == 4:
            content = _final_report_text(1)
            resp_msg = _msg_assistant(content)
            label = f"LEADER#{ln}→final_report_attempt_1"
        elif ln == 5:
            content = _final_report_text(2)
            resp_msg = _msg_assistant(content)
            label = f"LEADER#{ln}→final_report_attempt_2"
        else:
            content = "Acknowledged. Mission complete."
            resp_msg = _msg_assistant(content)
            label = f"LEADER#{ln}→ack"
    elif role == "coder":
        with _state_lock:
            _state["child_turn_count"] += 1
            cn = _state["child_turn_count"]
        content = _child_lie_text(cn)
        resp_msg = _msg_assistant(content)
        label = f"DEVELOPER#{cn}→LIE(catalog_markers_in_text)"
    else:
        resp_msg = _msg_assistant("OK.")
        label = "OTHER→ack"

    with _state_lock:
        _state["fired"].append({
            "n": n, "role": role, "label": label, "ts": time.strftime("%H:%M:%S"),
            "msgs": len(messages), "child_id_recovered": child_id,
            "stream": bool(request_body.get("stream")),
        })
    _log(f"call#{n} {label} role={role} msgs={len(messages)} stream={request_body.get('stream')} child_id={(child_id or 'None')[:12]}")
    if role == "leader":
        roles = [m.get("role") for m in messages]
        ai_count = sum(1 for m in messages if m.get("role") == "assistant")
        tool_count = sum(1 for m in messages if m.get("role") == "tool")
        _log(f"  DEBUG leader-roles={roles} ai_count={ai_count} tool_count={tool_count}")
        for i, m in enumerate(messages):
            tc = m.get("tool_calls")
            tcid = m.get("tool_call_id")
            cont = str(m.get("content", ""))[:80]
            _log(f"    msg#{i} role={m.get('role')} tool_call_id={tcid} has_tool_calls={bool(tc)} content={cont!r}")

    return resp_msg, label, n


# ── Response builders (identical to lcau/lca2 — OpenAI streaming + non-streaming)
def _build_json_response(resp_msg: dict, n: int, model: str = "mock-llm") -> dict:
    return {
        "id": f"chatcmpl-mock-{n}-{int(time.time() * 1000000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": resp_msg,
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _build_streaming_response(resp_msg: dict, n: int) -> str:
    """SSE-formatted streaming response."""
    chat_id = f"chatcml-mock-{n}-{int(time.time() * 1000000)}"
    created = int(time.time())
    model = "mock-llm"

    lines = []
    # Role chunk
    chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": ""},
            "finish_reason": None,
        }],
    }
    lines.append(f"data: {json.dumps(chunk)}")
    # Tool-call chunks
    tool_calls = resp_msg.get("tool_calls") or []
    for tc in tool_calls:
        tc_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }],
                },
                "finish_reason": None,
            }],
        }
        lines.append(f"data: {json.dumps(tc_chunk)}")
    # Content chunk
    content = resp_msg.get("content") or ""
    if content:
        c_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
            }],
        }
        lines.append(f"data: {json.dumps(c_chunk)}")
    # Final chunk
    end_chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }
    lines.append(f"data: {json.dumps(end_chunk)}")
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


# ── HTTP ─────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body_raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(body_raw.decode("utf-8"))
        except Exception as e:
            _log(f"PARSE_ERROR {e!r}")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "bad request"}')
            return

        resp_msg, label, n = _decide(body)
        is_stream = bool(body.get("stream"))

        if is_stream:
            payload = _build_streaming_response(resp_msg, n)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Content-Length", str(len(payload.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))
        else:
            payload = json.dumps(_build_json_response(resp_msg, n)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path == "/state":
            with _state_lock:
                body = json.dumps(_state, indent=2, default=str)
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


def _timeout_handler(_signum, _frame):
    _log(f"HARD_TIMEOUT after {MAX_LIFETIME_S}s — exiting")
    sys.exit(0)


def main():
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(MAX_LIFETIME_S)
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    _log(f"START mock-llm listening on 127.0.0.1:{LISTEN_PORT} pid={os.getpid()}")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _log("STOP mock-llm")


if __name__ == "__main__":
    main()
