"""The thin client — the exhaustive 30-row failure table (AC-1, AC-2).

``thin-client.md`` §6: every row names the HEURISTIC result the caller
receives; there is no row whose outcome is an error. The client NEVER
raises and NEVER returns an error envelope — it returns a
:class:`ClientOutcome` carrying either a failure label (rows 6–30) or a
suppression reason (rows 1–5).

The two label vocabularies are DISJOINT and the test proves it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import pytest

from daemon.services.decision.client import (
    MAX_RESPONSE_BYTES,
    DecisionClient,
    _carries_misroute_code,
    _carries_proxy_error_envelope,
)
from daemon.services.decision.config import DecisionConfig
from daemon.services.decision.types import (
    FailureClass,
    QuestionKind,
    SuppressionReason,
)
from tests.unit.decision.helpers import (
    choice_answer_payload,
    choice_question,
    choice_request,
    json_response,
    make_client,
    make_config,
    predicate_question,
    text_response,
)


# ── the table ────────────────────────────────────────────────────────────


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return json_response(choice_answer_payload())


def _row_not_configured_model() -> DecisionClient:
    return make_client(_ok_handler, config=make_config(model=""))


def _row_not_configured_base() -> DecisionClient:
    return make_client(_ok_handler, config=make_config(base_url=""))


def _row_not_configured_key() -> DecisionClient:
    return make_client(_ok_handler, config=make_config(api_key=""))


def _row_invalid_request_input() -> DecisionClient:
    return make_client(_ok_handler)


def _row_candidate_set_too_small() -> DecisionClient:
    return make_client(_ok_handler)


def _row_connect_error() -> DecisionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure", request=request)

    return make_client(handler)


def _row_timeout() -> DecisionClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return json_response(choice_answer_payload())

    return make_client(handler, timeout_s=0.05)


def _row_http_400_shape() -> DecisionClient:
    return make_client(
        lambda request: json_response(
            {"error": {"type": "server_error", "message": "at least one question is required"}},
            400,
        )
    )


def _row_http_401() -> DecisionClient:
    return make_client(
        lambda request: json_response(
            {"error": {"type": "authentication_error", "message": "bad key"}}, 401
        )
    )


def _row_http_403() -> DecisionClient:
    return make_client(
        lambda request: json_response({"error": {"type": "access_denied"}}, 403)
    )


def _row_http_404_proxy() -> DecisionClient:
    return make_client(
        lambda request: json_response(
            {"error": {"type": "server_error", "message": "unknown model \"decision\""}},
            404,
        )
    )


def _row_http_404_transport() -> DecisionClient:
    return make_client(lambda request: text_response("404 Not Found", 404))


def _row_http_405() -> DecisionClient:
    return make_client(lambda request: text_response("405 method not allowed", 405))


def _row_http_413() -> DecisionClient:
    return make_client(lambda request: text_response("too large", 413))


def _row_http_500() -> DecisionClient:
    return make_client(lambda request: text_response("boom", 500))


def _row_http_502_foreign_envelope() -> DecisionClient:
    return make_client(
        lambda request: json_response(
            {
                "type": "invalid_request_error",
                "code": "missing_required_parameter",
                "message": "Missing required parameter: 'questions[0].instructions'",
            },
            502,
        )
    )


def _row_http_504() -> DecisionClient:
    return make_client(lambda request: text_response("gateway timeout", 504))


def _row_http_other() -> DecisionClient:
    return make_client(lambda request: text_response("teapot", 418))


def _row_malformed_body_not_json() -> DecisionClient:
    return make_client(lambda request: text_response("not json at all", 200))


def _row_malformed_body_sse() -> DecisionClient:
    return make_client(
        lambda request: httpx.Response(
            200,
            content=b"data: {\"model\":\"decision\"}\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )


def _row_malformed_body_missing_answers() -> DecisionClient:
    return make_client(lambda request: json_response({"model": "decision"}))


def _row_malformed_body_empty_answers() -> DecisionClient:
    return make_client(lambda request: json_response({"model": "decision", "answers": []}))


def _row_unmatched_answer_missing_name() -> DecisionClient:
    payload = choice_answer_payload()
    payload["answers"][0].pop("name")
    return make_client(lambda request: json_response(payload))


def _row_unmatched_answer_wrong_name() -> DecisionClient:
    payload = choice_answer_payload(name="some_other_question")
    return make_client(lambda request: json_response(payload))


def _row_malformed_answers_wrong_type() -> DecisionClient:
    payload = choice_answer_payload()
    payload["answers"][0]["type"] = "predicate"
    return make_client(lambda request: json_response(payload))


def _row_malformed_answers_out_of_range() -> DecisionClient:
    payload = choice_answer_payload()
    payload["answers"][0]["choice"] = "not-a-candidate"
    return make_client(lambda request: json_response(payload))


def _row_malformed_answers_probability_out_of_range() -> DecisionClient:
    payload = {
        "model": "decision",
        "answers": [
            {
                "type": "predicate",
                "name": "q_complete",
                "probability": 1.4,
            }
        ],
    }
    return make_client(lambda request: json_response(payload))


def _row_refusal() -> DecisionClient:
    payload = {
        "model": "decision",
        "answers": [
            {
                "type": "refusal",
                "name": "s1_candidate_selection",
                "refusal": "I cannot answer that.",
            }
        ],
    }
    return make_client(lambda request: json_response(payload))


def _row_too_large() -> DecisionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        blob = "x" * (MAX_RESPONSE_BYTES + 1024)
        return httpx.Response(
            200,
            content=blob.encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    return make_client(handler)


def _row_below_floor() -> DecisionClient:
    return make_client(
        lambda request: json_response(choice_answer_payload(confidence=0.2))
    )


def _row_seam_fault_internal() -> DecisionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("something exotic broke")

    return make_client(handler)


#: ``(row, label_kind, label, builder, request_factory)`` — the whole
#: table. ``label_kind`` is ``"failure"`` or ``"suppression"``.
FAILURE_TABLE: list[
    tuple[int, str, str, Callable[[], DecisionClient], Callable[[], Any], float]
] = [
    (1, "suppression", SuppressionReason.NOT_CONFIGURED.value, _row_not_configured_model, choice_request, 0.0),
    (2, "suppression", SuppressionReason.NOT_CONFIGURED.value, _row_not_configured_base, choice_request, 0.0),
    (3, "suppression", SuppressionReason.NOT_CONFIGURED.value, _row_not_configured_key, choice_request, 0.0),
    (4, "suppression", SuppressionReason.INVALID_REQUEST.value, _row_invalid_request_input, lambda: choice_request(text=""), 0.0),
    (5, "suppression", SuppressionReason.CANDIDATE_SET_TOO_SMALL.value, _row_candidate_set_too_small, lambda: choice_request(choice_question(options=("id-a",))), 0.0),
    (6, "failure", FailureClass.CONNECT_ERROR.value, _row_connect_error, choice_request, 0.0),
    (7, "failure", FailureClass.CONNECT_ERROR.value, _row_connect_error, choice_request, 0.0),
    (8, "failure", FailureClass.CONNECT_ERROR.value, _row_connect_error, choice_request, 0.0),
    (9, "failure", FailureClass.TIMEOUT.value, _row_timeout, choice_request, 0.0),
    (10, "suppression", SuppressionReason.INVALID_REQUEST.value, _row_http_400_shape, choice_request, 0.0),
    (11, "failure", FailureClass.HTTP_401.value, _row_http_401, choice_request, 0.0),
    (12, "failure", FailureClass.HTTP_403.value, _row_http_403, choice_request, 0.0),
    (13, "failure", FailureClass.HTTP_404.value, _row_http_404_proxy, choice_request, 0.0),
    (13, "failure", FailureClass.HTTP_404.value, _row_http_404_transport, choice_request, 0.0),
    (14, "failure", FailureClass.HTTP_405.value, _row_http_405, choice_request, 0.0),
    (15, "failure", FailureClass.HTTP_413.value, _row_http_413, choice_request, 0.0),
    (16, "failure", FailureClass.HTTP_500.value, _row_http_500, choice_request, 0.0),
    (17, "failure", FailureClass.HTTP_502.value, _row_http_502_foreign_envelope, choice_request, 0.0),
    (18, "failure", FailureClass.HTTP_504.value, _row_http_504, choice_request, 0.0),
    (19, "failure", FailureClass.HTTP_OTHER.value, _row_http_other, choice_request, 0.0),
    (20, "failure", FailureClass.MALFORMED_BODY.value, _row_malformed_body_not_json, choice_request, 0.0),
    (21, "failure", FailureClass.MALFORMED_BODY.value, _row_malformed_body_sse, choice_request, 0.0),
    (22, "failure", FailureClass.MALFORMED_BODY.value, _row_malformed_body_missing_answers, choice_request, 0.0),
    (23, "failure", FailureClass.MALFORMED_BODY.value, _row_malformed_body_empty_answers, choice_request, 0.0),
    (24, "failure", FailureClass.UNMATCHED_ANSWER.value, _row_unmatched_answer_missing_name, choice_request, 0.0),
    (24, "failure", FailureClass.UNMATCHED_ANSWER.value, _row_unmatched_answer_wrong_name, choice_request, 0.0),
    (25, "failure", FailureClass.MALFORMED_ANSWERS.value, _row_malformed_answers_wrong_type, choice_request, 0.0),
    (26, "failure", FailureClass.MALFORMED_ANSWERS.value, _row_malformed_answers_out_of_range, choice_request, 0.0),
    (26, "failure", FailureClass.MALFORMED_ANSWERS.value, _row_malformed_answers_probability_out_of_range, lambda: choice_request(predicate_question()), 0.0),
    (27, "failure", FailureClass.REFUSAL.value, _row_refusal, choice_request, 0.0),
    (28, "failure", FailureClass.TOO_LARGE.value, _row_too_large, choice_request, 0.0),
    (29, "failure", FailureClass.BELOW_FLOOR.value, _row_below_floor, choice_request, 0.5),
    (30, "failure", FailureClass.SEAM_FAULT.value, _row_seam_fault_internal, choice_request, 0.0),
]


@pytest.mark.parametrize(
    "row,kind,label,builder,request_factory,floor",
    FAILURE_TABLE,
    ids=[f"row{row}-{label}" for row, kind, label, _b, _r, _f in FAILURE_TABLE],
)
async def test_every_failure_row_yields_the_heuristic(
    row: int, kind: str, label: str, builder, request_factory, floor: float
) -> None:
    """Every row of §6 fails open (or suppresses) — never an error.

    The client NEVER raises: the outcome carries the label and NO
    answer, so the caller's heuristic result stands (D2).
    """
    client = builder()
    outcome = await client.consult(
        request_factory(), floor=floor or None
    )
    assert outcome.answer is None, f"row {row} must not yield an answer"
    if kind == "failure":
        assert outcome.failure_class is not None, f"row {row}: expected a failure"
        assert outcome.failure_class.value == label, f"row {row}: wrong label"
        assert outcome.suppression is None, f"row {row}: a failure is not a suppression"
    else:
        assert outcome.suppression is not None, f"row {row}: expected a suppression"
        assert outcome.suppression.value == label, f"row {row}: wrong label"
        assert outcome.failure_class is None, f"row {row}: a suppression is not a failure"


async def test_the_two_label_vocabularies_are_disjoint() -> None:
    """AC-2 — the suppression and failure vocabularies never overlap."""
    failures = {fc.value for fc in FailureClass}
    suppressions = {sr.value for sr in SuppressionReason}
    assert failures & suppressions == set()
    assert len(failures) == 19, f"the failure vocabulary must carry 19 labels: {failures}"
    # L8 adds ``pricing_unconfigured`` — the closed vocabulary is now 10.
    assert len(suppressions) == 10, (
        f"the suppression vocabulary must carry 10 labels: {suppressions}"
    )
    assert SuppressionReason.PRICING_UNCONFIGURED.value in suppressions
    assert FailureClass.SEAM_FAULT.value in failures


async def test_the_client_never_raises_on_a_broken_transport() -> None:
    """AC-1 — the client never raises, whatever the transport does."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("exotic")

    client = make_client(handler)
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is FailureClass.SEAM_FAULT
    assert outcome.answer is None


# ── the transport contract ───────────────────────────────────────────────


async def test_exactly_one_attempt_no_retry_no_idempotency_key() -> None:
    """AC-1 — ONE attempt; a retry would double-bill (PX-6)."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return text_response("boom", 500)

    client = make_client(handler)
    await client.consult(choice_request())
    assert len(calls) == 1, "the client must make exactly ONE attempt"


async def test_the_url_never_appends_v1_again() -> None:
    """The base already carries ``/v1`` — never append it twice."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return json_response(choice_answer_payload())

    client = make_client(handler)
    await client.consult(choice_request())
    assert seen == ["https://proxy.test/v1/decisions"]


async def test_the_headers_match_the_contract() -> None:
    """Bearer + ``x-proxy-app`` + JSON content negotiation."""
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return json_response(choice_answer_payload())

    client = make_client(handler)
    await client.consult(choice_request())
    headers = seen[0]
    assert headers["authorization"] == "Bearer sk-test-key"
    assert headers["x-proxy-app"] == "ensemble"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "application/json"


async def test_the_client_sends_the_request_model_verbatim() -> None:
    """The client is faithful; the REGISTRY owns the model id.

    The registry overwrites the model on every consult (the seam never
    chooses it) — pinned in ``test_registry.py``.
    """
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return json_response(choice_answer_payload())

    client = make_client(handler)
    await client.consult(choice_request(model="decision"))
    assert bodies[0]["model"] == "decision"


# ── TRAP 1 — instructions are structurally required ──────────────────────


async def test_a_predicate_without_instructions_is_suppressed_not_502() -> None:
    """TRAP 1 — a missing ``instructions`` 502s upstream (silently dead).

    The client pre-flights it: the consult is SUPPRESSED (no call), so
    the failure is visible instead of an invisible 502 that fails open
    forever.
    """
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response({})

    client = make_client(handler)
    outcome = await client.consult(choice_request(predicate_question(instructions="")))
    assert outcome.suppression is SuppressionReason.INVALID_REQUEST
    assert calls == [], "a request without instructions must never reach the socket"


async def test_the_built_request_always_carries_instructions_for_predicate_and_score() -> None:
    """TRAP 1 — the BUILT REQUEST always carries non-empty instructions."""
    from daemon.services.decision.types import DecisionLevel

    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {"type": "predicate", "name": "q_complete", "probability": 0.8}
                ],
            }
        )

    client = make_client(handler)
    request = choice_request(predicate_question(instructions="Is this complete?"))
    payload = request.build_payload()
    assert payload["questions"][0]["instructions"] == "Is this complete?"
    await client.consult(request)
    assert bodies[0]["questions"][0]["instructions"] == "Is this complete?"

    score_request = choice_request(
        predicate_question(instructions="Rate it")
    )
    score_request = score_request.__class__(
        input="rate it",
        questions=(
            __import__(
                "daemon.services.decision.types", fromlist=["DecisionQuestion"]
            ).DecisionQuestion(
                kind=QuestionKind.SCORE,
                name="q_score",
                instructions="Rate the work",
                levels=(DecisionLevel(label="low"), DecisionLevel(label="high")),
            ),
        ),
    )
    payload = score_request.build_payload()
    assert payload["questions"][0]["instructions"] == "Rate the work"


# ── TRAP 2 — the mapper degrades, it never raises ───────────────────────


async def test_an_unexpected_answer_type_fails_open() -> None:
    """TRAP 2 — ``type:"noul"`` is a CONTENT failure, not a crash."""
    payload = {
        "model": "decision",
        "answers": [
            {
                "type": "noul",
                "name": "s1_candidate_selection",
                "legend": {"a": "b"},
            }
        ],
    }
    client = make_client(lambda request: json_response(payload))
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is FailureClass.MALFORMED_ANSWERS
    assert outcome.answer is None


async def test_a_map_shaped_probabilities_degrades_to_fail_open() -> None:
    """TRAP 2 — ``probabilities`` as a MAP degrades; it never raises."""
    payload = {
        "model": "decision",
        "answers": [
            {
                "type": "choice",
                "name": "s1_candidate_selection",
                "choice": "id-a",
                "probabilities": {"id-a": 0.6, "id-b": 0.4},
                "confidence": 0.8,
            }
        ],
    }
    client = make_client(lambda request: json_response(payload))
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is FailureClass.MALFORMED_ANSWERS
    assert outcome.answer is None


# ── the live-confirmed error taxonomy ────────────────────────────────────


def test_the_misroute_discriminator_is_the_structural_code_field() -> None:
    """A 400 is a provisioning verdict ONLY with ``code: decision_misroute``."""
    assert _carries_misroute_code(
        b'{"error":{"type":"server_error","code":"decision_misroute","message":"x"}}'
    )
    assert _carries_misroute_code(b'{"code":"decision_misroute"}')
    assert not _carries_misroute_code(
        b'{"error":{"type":"server_error","message":"decision_misroute: no model"}}'
    )
    assert not _carries_misroute_code(b"not json")


def test_the_proxy_error_envelope_is_structural() -> None:
    """A transport-level 404 (plain text) is NOT the proxy's envelope."""
    assert _carries_proxy_error_envelope(
        b'{"error":{"type":"server_error","message":"unknown model \\"decision\\""}}'
    )
    assert not _carries_proxy_error_envelope(b"404 Not Found")
    assert not _carries_proxy_error_envelope(b'{"message":"not found"}')


async def test_a_404_with_the_proxy_envelope_is_a_provisioning_verdict() -> None:
    """T-5 — a probe-provable 404 ⇒ ``provisioning_missing``."""
    client = _row_http_404_proxy()
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is FailureClass.HTTP_404
    assert outcome.provisioning_state is not None
    assert outcome.provisioning_state.value == "provisioning_missing"


async def test_a_transport_404_is_not_a_provisioning_verdict() -> None:
    """A plain 404 never parks a seam as provisioning_missing."""
    client = _row_http_404_transport()
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is FailureClass.HTTP_404
    assert outcome.provisioning_state is None


async def test_a_400_with_the_misroute_code_is_a_provisioning_verdict() -> None:
    """T-5 — a 400 ``decision_misroute`` ⇒ ``provisioning_missing``."""
    client = make_client(
        lambda request: json_response(
            {"error": {"type": "server_error", "code": "decision_misroute"}}, 400
        )
    )
    outcome = await client.consult(choice_request())
    assert outcome.provisioning_state is not None
    assert outcome.provisioning_state.value == "provisioning_missing"


async def test_a_shape_400_is_a_suppression_not_a_provisioning_verdict() -> None:
    """A bare 400 never becomes ``provisioning_missing``."""
    client = _row_http_400_shape()
    outcome = await client.consult(choice_request())
    assert outcome.provisioning_state is None
    assert outcome.suppression is SuppressionReason.INVALID_REQUEST
    assert outcome.failure_class is None


async def test_a_502_with_a_foreign_envelope_is_still_http_502() -> None:
    """The 502 body is OPAQUE — a foreign envelope changes nothing."""
    client = _row_http_502_foreign_envelope()
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is FailureClass.HTTP_502
    assert outcome.provisioning_state is None


# ── the typed mapping (AC-4) ─────────────────────────────────────────────


async def test_a_missing_usage_block_is_not_a_failure() -> None:
    """A 200 without ``usage`` is valid — the cost degrades to the TOKENS.

    L8: there is no pricing table anywhere, so a missing ``usage``
    block yields NO invented USD — the caller meters the tokens and
    the token ceiling is the fail-safe.
    """
    client = make_client(
        lambda request: json_response(choice_answer_payload(usage=None))
    )
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is None
    assert outcome.answer is not None
    assert outcome.cost_usd == 0.0, "the cost is never invented"
    assert outcome.total_tokens == 0, "no usage block ⇒ no metered tokens"


async def test_a_missing_confidence_is_not_a_failure_without_a_floor() -> None:
    """No floor in force ⇒ a missing ``confidence`` is not a failure."""
    client = make_client(
        lambda request: json_response(choice_answer_payload(confidence=None))
    )
    outcome = await client.consult(choice_request())
    assert outcome.failure_class is None
    assert outcome.answer is not None


async def test_an_absent_floor_field_fails_open() -> None:
    """With a floor in force, an ABSENT field fails open (§8.2.1)."""
    client = make_client(
        lambda request: json_response(choice_answer_payload(confidence=None))
    )
    outcome = await client.consult(choice_request(), floor=0.5)
    assert outcome.failure_class is FailureClass.BELOW_FLOOR
    assert outcome.answer is None


async def test_the_cost_is_never_invented() -> None:
    """L8 — the client NEVER invents a cost.

    There is no pricing table in the ensemble or in the proxy: the
    cost is the provider's reported figure or NOTHING. The USD
    conversion happens in the overlay, against the operator's rate.
    """
    client = make_client(
        lambda request: json_response(
            choice_answer_payload(usage={"total_tokens": 1234})
        )
    )
    outcome = await client.consult(choice_request())
    assert outcome.cost_usd == 0.0
    assert outcome.total_tokens == 1234


async def test_the_reported_cost_is_carried_through() -> None:
    """The provider's reported cost is carried through verbatim."""
    client = make_client(
        lambda request: json_response(
            choice_answer_payload(usage={"cost_usd": 0.25, "total_tokens": 100})
        )
    )
    outcome = await client.consult(choice_request())
    assert outcome.cost_usd == pytest.approx(0.25)
    assert outcome.total_tokens == 100


async def test_a_choice_answer_carries_the_full_distribution() -> None:
    """AC-4 — the full per-option distribution rides the answer."""
    client = make_client(lambda request: json_response(choice_answer_payload()))
    outcome = await client.consult(choice_request())
    assert outcome.answer is not None
    assert outcome.answer.ranked_values() == ("id-a", "id-b", "id-c")
    assert outcome.answer.choice == "id-a"
    assert outcome.answer.confidence == pytest.approx(0.9)


async def test_a_predicate_answer_reads_its_probability() -> None:
    """AC-4 — a predicate answer carries ``probability``."""
    client = make_client(
        lambda request: json_response(
            {
                "model": "decision",
                "answers": [
                    {"type": "predicate", "name": "q_complete", "probability": 0.77}
                ],
            }
        )
    )
    outcome = await client.consult(choice_request(predicate_question()))
    assert outcome.failure_class is None
    assert outcome.answer is not None
    assert outcome.answer.probability == pytest.approx(0.77)
