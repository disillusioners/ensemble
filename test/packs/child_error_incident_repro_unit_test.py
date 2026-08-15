#!/usr/bin/env python3
"""Functional incident-chain repro: bare-str LLM response → guard → retry → exhaust → error report.

Incident (2026-08-15, instance f10b7694): a provider under stress returned a
bare JSON string body instead of a ChatCompletion object. The OpenAI SDK's
``construct_type()`` passthrough returned the ``str`` as-is, and LangChain's
``BaseChatOpenAI._create_chat_result`` called ``.model_dump()`` on it —
surfacing as ``AttributeError: 'str' object has no attribute 'model_dump'``
from deep inside LangChain. The classifier marked that AttributeError
NON-retryable, tenacity never retried, the instance died, and the parent
closed as COMPLETED — silently losing the child work.

This script drives the REAL production chain end-to-end (no re-asserting of
unit-test mocks of the guard itself):

  Phase A — incident chain (most integrated path):
    A1. Simulate the provider: patch the SDK client so
        ``client.with_raw_response.create(...).parse()`` returns a bare ``str``
        (this is the exact seam where the SDK's construct_type() passthrough
        let the str through in the incident — see
        langchain_openai/chat_models/base.py::_generate: ``raw_response =
        self.client.with_raw_response.create(**payload); response =
        raw_response.parse()`` → ``_create_chat_result(response)``).
    A2. Drive the REAL ``ThinkingChatOpenAI.invoke()`` (not _create_chat_result
        directly) → assert the guard in daemon/graph.py:~1826 fires and raises
        ``MalformedLLMResponseError`` naming the offending type ("str").
    A3. Drive the REAL classifier (``classify_llm_errors``) wrapping the same
        poisoned LLM → assert it re-raises the SAME exception instance
        (retryable handler, no wrapping).
    A4. Drive the REAL tenacity retry loop (``make_llm_retry_strategy`` +
        ``tenacity.Retrying``, exactly how with_retry drives it) → assert the
        provider client is actually re-hit (transient_max-1 retries), then the
        retry budget EXHAUSTS and the final exception is still a
        ``MalformedLLMResponseError`` (the exhausted error surfaces unchanged).
    A5. Assert ``MalformedLLMResponseError in TRANSIENT_EXCEPTIONS``.

  Phase B — regression net (the retry net was NOT widened):
    B1. Assert ``AttributeError not in TRANSIENT_EXCEPTIONS``.
    B2. Assert the exact incident signature —
        ``AttributeError("'str' object has no attribute 'model_dump'")`` — is
        classified NON-retryable by the real retry predicate (returns False).

  Phase C — exhausted-error report reaches the parent with recovery guidance:
    Drive the REAL ``ErrorReportingService._send_error_report`` (async path:
    dedup → metadata → DB-sync half → bus hook → CompletionRegistry →
    enqueue) with every external dependency stubbed (same recipe as
    tests/test_cascade_integration.py Site 2 / tests/unit/test_error_report_recovery_hint.py),
    passing the exhausted error string from Phase A4. Assert:
      C1. exactly one message is enqueued to the PARENT instance;
      C2. the enqueued content contains the original error details;
      C3. the content contains ``[RECOVERY GUIDANCE]`` / RECOVERY_GUIDANCE_HINT
          as the appended tail;
      C4. metadata marks type=error_report with the child linkage.

Self-contained: stdlib + project modules + tenacity/langchain (already
production dependencies). No network, no DB — the client is a mock, the DB
session/bus/registry are stubs.

Output: final line ``RESULT: PASS|FAIL|TIMEOUT``; exit 0 (PASS) / 1 (FAIL) /
124 (TIMEOUT). Internal guard: ``signal.alarm(120)`` (Layer 2 inner timer,
matching the 120s unit-pack limit; the bash wrapper additionally wraps this
process with ``timeout 120``).
"""

from __future__ import annotations

import signal
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

INTERNAL_TIMEOUT_SECONDS = 120

CHILD_ID = "child-incident-0001"
PARENT_ID = "parent-incident-0001"

# The bare JSON string body a stressed provider returned in the incident —
# NOT a dict, NOT a ChatCompletion object.
BARE_STR_BODY = (
    '{"id": "chatcmpl-x", "object": "chat.completion", '
    '"created": 1, "model": "glm-5", "choices": []}'
)


def _on_alarm(signum: int, frame) -> None:
    print("RESULT: TIMEOUT", flush=True)
    sys.exit(124)


def _fail(step: str, exc: BaseException | None = None, detail: str = "") -> None:
    print(f"[FAIL] {step}" + (f" — {detail}" if detail else ""), flush=True)
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    print("RESULT: FAIL", flush=True)
    sys.exit(1)


def _ok(step: str, detail: str = "") -> None:
    print(f"[ok] {step}" + (f" — {detail}" if detail else ""), flush=True)


def make_incident_llm():
    """Build a real ThinkingChatOpenAI wired to a provider that returns a bare str.

    The mock lands on the exact seam the incident went through:
    ``self.client.with_raw_response.create(**payload)`` returns a raw
    response whose ``.parse()`` yields the bare ``str`` body (the OpenAI
    SDK's ``construct_type()`` passthrough). Everything upstream — message
    conversion, payload build, the guard, super()._create_chat_result — is
    the real production code.
    """
    from daemon.graph import ThinkingChatOpenAI

    llm = ThinkingChatOpenAI(model="glm-5", api_key="test-key", max_retries=0)
    raw_response = MagicMock(name="raw_response")
    raw_response.parse.return_value = BARE_STR_BODY  # SDK passthrough of str
    client = MagicMock(name="openai_client")
    client.with_raw_response.create.return_value = raw_response
    llm.client = client
    return llm


# ---------------------------------------------------------------------------
# Phase A — the incident chain, end to end
# ---------------------------------------------------------------------------

def phase_a() -> tuple[str, type]:
    """Run the incident chain; return (exhausted_error_string, exception_type)."""
    from langchain_core.messages import HumanMessage

    from daemon.graph import ThinkingChatOpenAI
    from daemon.llm_error_classifier import (
        TRANSIENT_EXCEPTIONS,
        MalformedLLMResponseError,
        classify_llm_errors,
        make_llm_retry_strategy,
    )

    import tenacity

    # A1+A2 — real invoke() against the poisoned provider: guard must fire
    # before super()._create_chat_result can touch .model_dump().
    llm = make_incident_llm()
    try:
        llm.invoke([HumanMessage(content="hello")])
        _fail("A2: invoke() did not raise on bare-str response")
    except MalformedLLMResponseError as e:
        assert "str" in str(e), f"offending type name missing from message: {e}"
        assert e.response is BARE_STR_BODY, "guard did not carry the raw body"
        _ok("A2: guard fired on bare-str response via real invoke()", str(e))
    except Exception as e:  # noqa: BLE001
        _fail(
            "A2: expected MalformedLLMResponseError from guard",
            e,
            f"got {type(e).__name__}",
        )

    # A5 — retryability is declared at the source of truth.
    assert MalformedLLMResponseError in TRANSIENT_EXCEPTIONS, (
        "MalformedLLMResponseError must be a member of TRANSIENT_EXCEPTIONS"
    )
    _ok("A5: MalformedLLMResponseError in TRANSIENT_EXCEPTIONS (retryable)")

    # A3+A4 — real classifier + real tenacity loop (with_retry's shape).
    llm2 = make_incident_llm()
    classified = classify_llm_errors(llm2)
    strategy = make_llm_retry_strategy(transient_max=3, timeout_max=2)
    provider_hits = {"count": 0}
    final_exc: BaseException | None = None

    try:
        for attempt in tenacity.Retrying(
            retry=strategy,
            wait=tenacity.wait_none(),  # no sleeps — pack stays fast
            stop=tenacity.stop_after_attempt(50),
            reraise=True,
        ):
            with attempt:
                provider_hits["count"] += 1
                classified.invoke([HumanMessage(content="hello")])
    except Exception as e:  # noqa: BLE001
        final_exc = e

    assert final_exc is not None, "retry loop must exhaust and re-raise"
    assert isinstance(final_exc, MalformedLLMResponseError), (
        f"exhausted error must remain MalformedLLMResponseError, "
        f"got {type(final_exc).__name__}"
    )
    # transient_max=3 → predicate True while count<3 → 3 attempts total.
    assert provider_hits["count"] == 3, (
        f"expected exactly 3 provider attempts (1 + 2 retries), "
        f"got {provider_hits['count']}"
    )
    assert llm2.client.with_raw_response.create.call_count == 3, (
        "provider client must actually be re-hit by the retries"
    )
    _ok(
        "A3+A4: classifier re-raised + tenacity retried 3x then exhausted",
        f"provider hits={provider_hits['count']}, "
        f"final={type(final_exc).__name__}",
    )

    # The exhausted error string exactly as the upstream error pipeline
    # would stringify it — this is what _send_error_report receives.
    exhausted_error = f"{type(final_exc).__name__}: {final_exc}"
    return exhausted_error, type(final_exc)


# ---------------------------------------------------------------------------
# Phase B — the regression net (AttributeError stays non-retryable)
# ---------------------------------------------------------------------------

def phase_b() -> None:
    from daemon.llm_error_classifier import (
        TRANSIENT_EXCEPTIONS,
        make_llm_retry_strategy,
    )

    assert AttributeError not in TRANSIENT_EXCEPTIONS, (
        "generic AttributeError must NOT be in the retry set — the net was widened"
    )
    _ok("B1: AttributeError not in TRANSIENT_EXCEPTIONS")

    # The exact incident signature, evaluated by the real predicate.
    incident_attr_error = AttributeError(
        "'str' object has no attribute 'model_dump'"
    )
    strategy = make_llm_retry_strategy(transient_max=8, timeout_max=3)
    state = MagicMock()
    state.attempt_number = 1
    state.outcome.exception.return_value = incident_attr_error
    decision = strategy(state)
    assert decision is False, (
        f"incident-signature AttributeError must NOT be retried, got {decision}"
    )
    _ok("B2: incident-signature AttributeError classified non-retryable")


# ---------------------------------------------------------------------------
# Phase C — exhausted error → report to parent with recovery guidance
# ---------------------------------------------------------------------------

def phase_c(exhausted_error: str) -> None:
    import asyncio

    from daemon.repositories.instance.models import InstanceStatus
    from daemon.services.error_reporting import (
        RECOVERY_GUIDANCE_HINT,
        ErrorReportingService,
    )

    # Mock manager exposing everything the real _send_error_report touches.
    manager = MagicMock(name="InstanceManager")

    child_meta = MagicMock(name="child_meta")
    child_meta.parent_id = PARENT_ID
    child_meta.agent_name = "tester"
    child_meta.agent_dir = "/tmp/agents/tester"
    manager._instance_repository.get = MagicMock(return_value=child_meta)
    manager._queue_repository.list = MagicMock(return_value=[])

    async def _capture_enqueue(**kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(message_id="report-msg-0001")

    captured: dict = {}
    manager.enqueue_message = AsyncMock(side_effect=_capture_enqueue)
    manager._live_hub = None  # skip SSE side-effects

    # Stub session yielded by the patched WriteGuardSession (DB-sync half).
    child = MagicMock(name="child_instance")
    child.instance_id = CHILD_ID
    child.agent_id = "tester"
    child.parent_id = PARENT_ID
    child.status = InstanceStatus.RUNNING.value
    child.instance_metadata = {}
    parent = MagicMock(name="parent_instance")
    parent.instance_id = PARENT_ID
    parent.agent_id = "leader"
    parent.parent_id = None
    parent.status = InstanceStatus.RUNNING.value
    parent.version = 1

    session = MagicMock(name="session")
    session.get = MagicMock(
        side_effect=lambda cls, iid: {
            CHILD_ID: child,
            PARENT_ID: parent,
        }.get(iid)
    )
    session.execute = MagicMock(return_value=MagicMock(name="exec_result"))
    session.expire = MagicMock()
    session.commit = MagicMock()
    session.add = MagicMock()

    wgs = MagicMock(name="WriteGuardSession")
    wgs.__enter__ = MagicMock(return_value=session)
    wgs.__exit__ = MagicMock(return_value=False)

    bus_stub = MagicMock(name="DependencyBus")
    bus_stub.count_pending_for_target_sync = MagicMock(return_value=0)

    service = ErrorReportingService(manager=manager, events_service=None)

    with patch(
        "daemon.services.dependency_bus.get_dependency_bus",
        return_value=bus_stub,
    ), patch(
        "daemon.services.error_reporting.WriteGuardSession",
        return_value=wgs,
    ), patch(
        "daemon.services.error_reporting.Session",
        return_value=MagicMock(name="raw_session"),
    ), patch(
        "daemon.services.completion_registry.get_completion_registry",
        return_value=MagicMock(name="CompletionRegistry"),
    ):
        asyncio.run(
            service._send_error_report(
                instance_id=CHILD_ID,
                error=exhausted_error,
                error_type="execution_error",
                message_id=None,
            )
        )

    # C1 — exactly one report, delivered to the PARENT.
    assert manager.enqueue_message.await_count == 1, (
        f"expected exactly 1 enqueue, got {manager.enqueue_message.await_count}"
    )
    kwargs = captured["kwargs"]
    assert kwargs["instance_id"] == PARENT_ID, (
        f"report must be enqueued to the parent, got {kwargs['instance_id']}"
    )
    _ok("C1: exactly one error report enqueued to the PARENT")

    message = kwargs["message"]

    # C2 — original exhausted-error details preserved.
    assert exhausted_error in message, (
        "original exhausted error string missing from the report"
    )
    assert message.startswith("⚠️ tester encountered an error:"), (
        "report must keep the original leading content"
    )
    _ok("C2: original exhausted-error details preserved in the report")

    # C3 — recovery guidance appended as the tail.
    assert "[RECOVERY GUIDANCE]" in message, "hint block missing"
    assert RECOVERY_GUIDANCE_HINT in message, "full hint constant missing"
    assert message.endswith(RECOVERY_GUIDANCE_HINT), (
        "hint must be the appended tail of the report"
    )
    _ok("C3: [RECOVERY GUIDANCE] hint appended to the report tail")

    # C4 — message metadata carries the error_report linkage.
    meta = kwargs["metadata"]
    assert meta["type"] == "error_report", f"metadata type wrong: {meta.get('type')}"
    assert meta["child_instance_id"] == CHILD_ID, "child linkage missing"
    _ok("C4: error_report metadata with child linkage")


def main() -> int:
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(INTERNAL_TIMEOUT_SECONDS)

    print("=== Test Pack: child_error_incident_repro_unit_test ===", flush=True)

    try:
        exhausted_error, _ = phase_a()
        phase_b()
        phase_c(exhausted_error)
    except SystemExit:
        raise
    except AssertionError as e:
        _fail("assertion", e, str(e))
    except Exception as e:  # noqa: BLE001
        _fail("unexpected exception", e, f"{type(e).__name__}: {e}")

    signal.alarm(0)  # cancel the internal timer — we are done
    print("Incident chain verified: bare-str response → guard → retry → "
          "exhaust → parent report with recovery guidance.", flush=True)
    print("RESULT: PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
