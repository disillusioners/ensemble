#!/usr/bin/env python3
"""Mock OpenAI-compatible LLM endpoint for LCA Stage-2 boot smoke.

Serves /v1/chat/completions on 127.0.0.1:15778.

Supports BOTH:
- non-streaming: returns single JSON chat.completion
- streaming (stream=true): returns SSE-formatted chat.completion.chunk events

State machine (role detected by system prompt content):
- judge: contains "fused evidence bundle" → verdict payloads
         #1 not_complete, #2 complete
- leader: contains "strategic leader" → scripted turns
         #1 spawn_instance (agent=developer)
         #2 send_message(child_id, task) OR final_report if no child_id
         #3 send_message(child_id, status check) OR final_report if no child_id
         #4 final_report (gate fires → judge #1 → deny+nudge)
         #5 final_report (gate fires → judge #2 → allow+END)
- developer: contains "Developer Agent" / "Coder Agent" → completion
- other: simple ack

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

LISTEN_PORT = int(os.environ.get("MOCK_LLM_PORT", "15778"))
LOG_PATH = os.environ.get("MOCK_LLM_LOG", "/tmp/lca2-smoke/mock_llm.log")
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
    if "fused evidence bundle" in system_content:
        return "judge"
    if "strategic leader" in system_content or "coordinates specialized agents" in system_content:
        return "leader"
    if "working-lead coding agent" in system_content or "Coder Agent" in system_content \
            or "Developer Agent" in system_content or "Developer (Working-Lead" in system_content:
        return "coder"
    return "other"


def _msg_assistant(content: str, tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _judge_verdict(verdict: str) -> str:
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
            "leader claimed completion but the child report was not present",
            "no concrete evidence of deliverable IDs in the transcript",
        ],
        "advisory": (
            "The leader's report does not enumerate the concrete deliverable "
            "IDs the child was supposed to produce. Cite the child report "
            "explicitly before attesting completion."
        ),
    })


def _final_report_text(attempt: int) -> str:
    return (
        "Final report — mission complete. The delegated developer child has produced "
        "the smoke-test deliverable I requested (output: smoke-test-output.txt). "
        "All promised work is delivered; no follow-ups are outstanding. The "
        "mission is genuinely complete. "
        * (3 + attempt)
    )


def _tool_call_spawn() -> list:
    return [{
        "id": "call_spawn_1",
        "type": "function",
        "function": {
            "name": "spawn_instance",
            "arguments": json.dumps({"agent_id": "developer", "instance_name": "smoke-child"}),
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


# ── Decision ────────────────────────────────────────────────────────────
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
        content = (
            f"Task complete (developer turn {cn}). "
            "Deliverable: smoke-test-output.txt — content: 'smoke ok'. "
            "Per-child attestation: the task is finished and verified."
        )
        resp_msg = _msg_assistant(content)
        label = f"DEVELOPER#{cn}→complete"
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
        # Debug: dump message count + roles
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


# ── Response builders ───────────────────────────────────────────────────
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
    """SSE-formatted streaming response.
    The OpenAI streaming protocol emits:
    - initial role chunk
    - content chunks (one or more)
    - tool_calls chunks (one per tool call)
    - finish_reason chunk
    - data: [DONE]
    """
    chat_id = f"chatcml-mock-{n}-{int(time.time() * 1000000)}"
    created = int(time.time())
    model = "mock-llm"

    lines = []
    # 1. Role chunk
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

    # 2. Tool call chunks (if any) — emit per tool call
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

    # 3. Content chunks (split into words for OpenAI streaming semantics)
    content = resp_msg.get("content") or ""
    if content:
        # Emit as a single chunk to keep things simple
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

    # 4. Final chunk (finish_reason)
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

    # 5. DONE
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


# ── HTTP ────────────────────────────────────────────────────────────────
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