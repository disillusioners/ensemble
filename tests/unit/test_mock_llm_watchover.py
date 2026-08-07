"""Unit tests for the watchover-specific behavior of the mock LLM server.

Covers the watchover extension to ``tests/mock_llm_server.py``:

* :class:`WatchoverTestState` — scenario activation and counter resets.
* :func:`_detect_call_type` — request classification into watcher / builder / agent.
* :func:`_has_watchover_markers` — gate for routing to the watchover handler.
* :func:`_handle_watchover_request` — scenario-driven responses.
* :func:`build_watchover_response` — OpenAI-compatible response format.
* ``POST /scenario`` and ``GET /stats`` endpoints.

These tests run in-process via FastAPI's :class:`TestClient` — no live server,
no subprocess, no network. State is reset before and after every test through
an autouse fixture so tests do not leak counters into each other.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import tests.mock_llm_server as mock_server
from tests.mock_llm_server import (
    BUILDER_GUARDRAILS,
    ChatCompletionRequest,
    Message,
    WatchoverTestState,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """TestClient bound to the mock LLM server's FastAPI app."""
    return TestClient(mock_server.app)


@pytest.fixture(autouse=True)
def reset_watchover_state_fixture():
    """Reset watchover and mock-LLM state before AND after each test.

    ``reset_watchover_state`` only touches the watchover counters; we also
    zero ``state.request_count`` so total-request assertions are stable when
    a test deliberately sends N requests.
    """
    mock_server.reset_watchover_state()
    mock_server.state.request_count = 0
    mock_server.state.response_index = 0
    yield
    mock_server.reset_watchover_state()
    mock_server.state.request_count = 0
    mock_server.state.response_index = 0


def _make_request(messages: list[dict]) -> ChatCompletionRequest:
    """Helper to build a ChatCompletionRequest from plain dicts."""
    return ChatCompletionRequest(
        model="mock-test-model",
        messages=[Message(**m) for m in messages],
    )


def _watcher_user_message() -> dict:
    return {"role": "user", "content": "[WATCHOVER CHECK]\nEvaluate this tool call: kubectl delete"}


def _watcher_context_user_message() -> dict:
    return {"role": "user", "content": "[WATCHOVER CONTEXT]\nUse this context to evaluate"}


def _builder_system_message() -> dict:
    return {"role": "system", "content": "You are a security-profile compiler for Kubernetes."}


def _plain_user_message() -> dict:
    return {"role": "user", "content": "List the running pods in the cluster."}


def _snapshot_user_message() -> dict:
    """Layer-3 [CONVERSATION SNAPSHOT] marker payload (no summarize cue)."""
    return {
        "role": "user",
        "content": (
            "[CONVERSATION SNAPSHOT]\n"
            "[tool_call] bash kubectl get nodes\n"
            "[tool_call] bash kubectl get pods"
        ),
    }


def _summarize_system_message() -> dict:
    """Layer-2 system cue that pairs with [CONVERSATION SNAPSHOT] to mark a snapshot call."""
    return {
        "role": "system",
        "content": "You are a conversation summarizer. Summarize the following concisely.",
    }


def _layer4_messages() -> list[dict]:
    """Layer-4 separator pair wrapping individual messages.

    In the 5-layer architecture, layer 4 holds the actual recent messages
    individually (not packed), bracketed by [start of recent messages] and
    [end of recent messages] markers.
    """
    return [
        {"role": "user", "content": "[start of recent messages]"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "kubectl get nodes"}'},
                }
            ],
        },
        {"role": "user", "content": "[end of recent messages]"},
    ]


# ---------------------------------------------------------------------------
# Group 1: Detection Logic — `_detect_call_type`
# ---------------------------------------------------------------------------

def test_detect_watcher_via_watchover_check():
    """A `[WATCHOVER CHECK]` marker anywhere in the messages routes to watcher."""
    req = _make_request([_watcher_user_message()])
    assert mock_server._detect_call_type(req) == "watcher"


def test_detect_watcher_via_watchover_context():
    """A `[WATCHOVER CONTEXT]` marker alone (no CHECK) also routes to watcher."""
    req = _make_request([_watcher_context_user_message()])
    assert mock_server._detect_call_type(req) == "watcher"


def test_detect_builder_via_security_profile_compiler():
    """A system message naming the 'security-profile compiler' routes to builder."""
    req = _make_request([_builder_system_message(), _plain_user_message()])
    assert mock_server._detect_call_type(req) == "builder"


def test_detect_builder_via_watcher_context_builder():
    """A system message naming 'Watcher Context Builder' also routes to builder."""
    req = _make_request(
        [
            {
                "role": "system",
                "content": "I am the Watcher Context Builder — assemble the guardrails.",
            },
            _plain_user_message(),
        ]
    )
    assert mock_server._detect_call_type(req) == "builder"


def test_detect_agent_no_markers():
    """Plain messages with no watchover markers classify as agent."""
    req = _make_request([_plain_user_message()])
    assert mock_server._detect_call_type(req) == "agent"


def test_detect_priority_check_before_context():
    """Both markers present — still watcher (priority test)."""
    req = _make_request(
        [
            {
                "role": "user",
                "content": "[WATCHOVER CHECK] and [WATCHOVER CONTEXT] both present",
            }
        ]
    )
    assert mock_server._detect_call_type(req) == "watcher"


def test_detect_snapshot_via_conversation_snapshot_marker():
    """[CONVERSATION SNAPSHOT] + summarize in system → 'snapshot' call type."""
    req = _make_request([_summarize_system_message(), _snapshot_user_message()])
    assert mock_server._detect_call_type(req) == "snapshot"


def test_detect_snapshot_requires_summarize_cue():
    """[CONVERSATION SNAPSHOT] without 'summarize' in system → NOT snapshot.

    Snapshot detection requires BOTH the layer-3 marker AND a 'summarize'
    cue in a system message. Without the system cue, the request falls
    through to the default 'agent' classification.
    """
    req = _make_request([_snapshot_user_message()])
    assert mock_server._detect_call_type(req) == "agent"


def test_detect_snapshot_priority_over_watcher():
    """Snapshot marker wins over [WATCHOVER CHECK] when both are present.

    Snapshot calls may legitimately contain watchover-related text (they
    summarize prior tool calls including watcher decisions), so the
    snapshot-specific check must run FIRST.
    """
    req = _make_request(
        [
            _summarize_system_message(),
            {
                "role": "user",
                "content": (
                    "[CONVERSATION SNAPSHOT]\n"
                    "[WATCHOVER CHECK] previous tool: kubectl get nodes"
                ),
            },
        ]
    )
    assert mock_server._detect_call_type(req) == "snapshot"


def test_detect_5layer_individual_messages():
    """Layer 4 messages are individual, not packed into one.

    [start of recent messages] / [end of recent messages] are layer-4
    separators that bracket INDIVIDUAL messages (each its own entry in the
    request.messages list). The markers themselves carry no classification
    signal — they must not trigger snapshot, watcher, or builder detection
    on their own.
    """
    # Case 1: layer-4 markers alone (no [CONVERSATION SNAPSHOT], no summarize)
    # → must classify as 'agent', not 'snapshot'.
    req = _make_request([_plain_user_message()] + _layer4_messages())
    assert mock_server._detect_call_type(req) == "agent"

    # Case 2: layer-4 markers alongside [CONVERSATION SNAPSHOT] but NO
    # 'summarize' in any system message → still 'agent' (snapshot needs both).
    req_no_summarize = _make_request(
        [
            {
                "role": "user",
                "content": (
                    "[CONVERSATION SNAPSHOT]\n"
                    "[start of recent messages]\n"
                    "msg1\n"
                    "[end of recent messages]"
                ),
            }
        ]
    )
    assert mock_server._detect_call_type(req_no_summarize) == "agent"

    # Case 3: layer-4 markers alongside [CONVERSATION SNAPSHOT] WITH
    # 'summarize' in system → 'snapshot' (the two signals together).
    req_full = _make_request(
        [
            _summarize_system_message(),
            {
                "role": "user",
                "content": (
                    "[CONVERSATION SNAPSHOT]\n"
                    "[start of recent messages]\n"
                    "msg1\n"
                    "[end of recent messages]"
                ),
            },
        ]
    )
    assert mock_server._detect_call_type(req_full) == "snapshot"


def test_snapshot_response_returns_summary_text(client: TestClient):
    """Snapshot calls through the HTTP path return the deterministic summary
    with finish_reason='stop'."""
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-test-model",
            "messages": [_summarize_system_message(), _snapshot_user_message()],
        },
    )
    assert r.status_code == 200
    body = r.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "stop"
    content = choice["message"]["content"]
    assert "kubectl" in content
    assert "get nodes" in content
    assert "get pods" in content


# ---------------------------------------------------------------------------
# Group 2: Scenario Cycling (TestClient HTTP integration)
# ---------------------------------------------------------------------------

def test_scenario_allow_watcher_always_allows(client: TestClient):
    """Under 'allow' scenario, every watcher call returns 'Allowed'."""
    client.post("/scenario", json={"scenario": "allow"})

    r1 = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_watcher_user_message()]},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["choices"][0]["message"]["content"] == "Allowed"
    assert body1["choices"][0]["finish_reason"] == "stop"

    r2 = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_watcher_user_message()]},
    )
    assert r2.status_code == 200
    assert r2.json()["choices"][0]["message"]["content"] == "Allowed"


def test_scenario_three_strikes_always_denies(client: TestClient):
    """Under 'three_strikes' scenario, every watcher call is denied."""
    client.post("/scenario", json={"scenario": "three_strikes"})

    for _ in range(3):
        r = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [_watcher_user_message()]},
        )
        assert r.status_code == 200
        content = r.json()["choices"][0]["message"]["content"]
        assert content.startswith("Deny:")


def test_scenario_deny_then_correct(client: TestClient):
    """First watcher call denies, subsequent calls allow."""
    client.post("/scenario", json={"scenario": "deny_then_correct"})

    r1 = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_watcher_user_message()]},
    )
    assert r1.status_code == 200
    content1 = r1.json()["choices"][0]["message"]["content"]
    assert content1.startswith("Deny:")
    assert "kubectl delete is a mutating operation" in content1

    r2 = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_watcher_user_message()]},
    )
    assert r2.status_code == 200
    assert r2.json()["choices"][0]["message"]["content"] == "Allowed"


def test_scenario_infra_error_first_call_500(client: TestClient):
    """'infra_error' returns HTTP 500 on first watcher call, then 'Allowed'."""
    client.post("/scenario", json={"scenario": "infra_error"})

    r1 = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_watcher_user_message()]},
    )
    assert r1.status_code == 500

    r2 = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_watcher_user_message()]},
    )
    assert r2.status_code == 200
    assert r2.json()["choices"][0]["message"]["content"] == "Allowed"


def test_scenario_builder_returns_guardrails(client: TestClient):
    """Builder calls return the BUILDER_GUARDRAILS markdown content."""
    client.post("/scenario", json={"scenario": "builder_quality"})

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock",
            "messages": [_builder_system_message(), _plain_user_message()],
        },
    )
    assert r.status_code == 200
    content = r.json()["choices"][0]["message"]["content"]
    assert "## Allowed" in content
    assert "## Forbidden" in content
    # And it must match the canonical guardrails constant.
    assert content == BUILDER_GUARDRAILS


# ---------------------------------------------------------------------------
# Group 3: Response Format Correctness
# ---------------------------------------------------------------------------

def test_watcher_response_format(client: TestClient):
    """Watcher response shape conforms to OpenAI chat.completion schema."""
    client.post("/scenario", json={"scenario": "allow"})

    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock-test-model", "messages": [_watcher_user_message()]},
    )
    assert r.status_code == 200
    body = r.json()

    # OpenAI top-level keys
    assert isinstance(body["id"], str) and body["id"].startswith("mockchat-")
    assert body["object"] == "chat.completion"
    assert isinstance(body["created"], int)
    assert body["model"] == "mock-test-model"

    # choices[0]
    choices = body["choices"]
    assert isinstance(choices, list) and len(choices) == 1
    msg = choices[0]["message"]
    assert msg["role"] == "assistant"
    assert choices[0]["finish_reason"] == "stop"


def test_agent_tool_call_response_format(client: TestClient):
    """Agent responses emit finish_reason='tool_calls' with a tool_calls list."""
    client.post("/scenario", json={"scenario": "allow"})

    # Agent call: no markers, but scenario is active → routed to watchover handler.
    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_plain_user_message()]},
    )
    assert r.status_code == 200
    choice = r.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"

    tool_calls = choice["message"]["tool_calls"]
    assert isinstance(tool_calls, list) and len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "bash"
    assert "kubectl" in tool_calls[0]["function"]["arguments"]


def test_agent_returns_safe_command_in_allow_scenario(client: TestClient):
    """Under 'allow' scenario, agent emits the safe 'kubectl top nodes' command."""
    client.post("/scenario", json={"scenario": "allow"})

    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_plain_user_message()]},
    )
    args = r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert "kubectl top nodes" in args


def test_agent_returns_dangerous_in_three_strikes(client: TestClient):
    """Under 'three_strikes' scenario, agent emits a dangerous 'kubectl delete'."""
    client.post("/scenario", json={"scenario": "three_strikes"})

    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_plain_user_message()]},
    )
    args = r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert "kubectl delete" in args


# ---------------------------------------------------------------------------
# Group 4: Stats Tracking
# ---------------------------------------------------------------------------

def test_stats_track_counts(client: TestClient):
    """Watcher and agent counters reflect the actual call mix."""
    client.post("/scenario", json={"scenario": "allow"})

    # 2 watcher calls
    for _ in range(2):
        client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [_watcher_user_message()]},
        )
    # 1 agent call
    client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_plain_user_message()]},
    )

    stats = client.get("/stats").json()
    assert stats["watcher_call_count"] == 2
    assert stats["agent_call_count"] == 1
    assert stats["current_scenario"] == "allow"


def test_scenario_reset_clears_counters(client: TestClient):
    """Changing scenario via POST /scenario resets internal counters."""
    client.post("/scenario", json={"scenario": "allow"})
    for _ in range(3):
        client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": [_watcher_user_message()]},
        )

    # Sanity: counters are non-zero before the reset.
    pre = client.get("/stats").json()
    assert pre["watcher_call_count"] == 3

    # Switching scenario must reset counters.
    client.post("/scenario", json={"scenario": "three_strikes"})
    post = client.get("/stats").json()
    assert post["watcher_call_count"] == 0
    assert post["agent_call_count"] == 0
    assert post["current_scenario"] == "three_strikes"


def test_stats_track_builder_count(client: TestClient):
    """Builder counter increments when a builder-type request is processed."""
    client.post("/scenario", json={"scenario": "builder_quality"})

    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock",
            "messages": [_builder_system_message(), _plain_user_message()],
        },
    )
    assert r.status_code == 200

    stats = client.get("/stats").json()
    assert stats["builder_call_count"] == 1


# ---------------------------------------------------------------------------
# Group 5: Backward Compatibility
# ---------------------------------------------------------------------------

def test_generic_path_still_works_without_scenario(client: TestClient):
    """Without a scenario, plain agent requests fall through to the generic mock."""
    # NOTE: do NOT call POST /scenario — scenario stays inactive.
    assert mock_server.watchover_state.active is False

    r = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [_plain_user_message()]},
    )
    assert r.status_code == 200
    body = r.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "stop"
    content = choice["message"]["content"]
    assert content is not None
    assert content in mock_server.MOCK_CONTENT_RESPONSES


def test_null_content_does_not_422(client: TestClient):
    """An assistant message with content=null and tool_calls must not 422."""
    # No scenario active — falls through to generic path.
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "mock",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                }
            ],
        },
    )
    assert r.status_code == 200