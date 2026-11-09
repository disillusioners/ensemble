"""Shared fakes for the decision-module unit tests.

Nothing here touches the network, the database, or the live daemon: the
HTTP layer is an ``httpx.MockTransport``, the kill store is the
in-memory implementation, and the shadow writer's sink captures the
rendered rows.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

import httpx

from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import DecisionConfig
from daemon.services.decision.kill import InMemoryKillStore, KillChannelResolver
from daemon.services.decision.registry import SeamRecord
from daemon.services.decision.shadow import ShadowWriter
from daemon.services.decision.types import (
    DecisionChoice,
    DecisionQuestion,
    DecisionRequest,
    Monotonicity,
    QuestionKind,
)

#: A base URL that already carries ``/v1`` — the shape the proxy's base
#: URL has (the client appends only ``/decisions``).
TEST_BASE_URL = "https://proxy.test/v1"

TEST_API_KEY = "sk-test-key"


def make_config(**overrides: Any) -> DecisionConfig:
    """A configured :class:`DecisionConfig` for the tests."""
    base = {
        "base_url": TEST_BASE_URL,
        "api_key": TEST_API_KEY,
        "model": "decision",
        "timeout_s": 5.0,
        "cost_ceiling_usd": 50.0,
        "token_ceiling": 10_000_000,
        "pricing_usd_per_million_tokens": {},
        # The tests' EXPLICIT decision signal (Q2): a config built here
        # stands for a module the operator opted into. The Q2 pins
        # build theirs through ``resolve_values(env=...)`` instead, so
        # the reconciliation is exercised end-to-end.
        "explicitly_configured": True,
    }
    base.update(overrides)
    return DecisionConfig(**base)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    config: DecisionConfig | None = None,
    timeout_s: float = 5.0,
) -> DecisionClient:
    """A client wired to a mock transport."""
    transport = httpx.MockTransport(handler)
    return DecisionClient(
        config or make_config(timeout_s=timeout_s),
        transport_factory=lambda: transport,
    )


def json_response(
    payload: Any, status_code: int = 200, *, content_type: str = "application/json"
) -> httpx.Response:
    """A JSON response."""
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": content_type},
    )


def text_response(text: str, status_code: int = 200) -> httpx.Response:
    """A plain-text response."""
    return httpx.Response(
        status_code,
        content=text.encode("utf-8"),
        headers={"content-type": "text/plain; charset=utf-8"},
    )


def choice_question(
    name: str = "s1_candidate_selection",
    *,
    options: tuple[str, ...] = ("id-a", "id-b", "id-c"),
    instructions: str | None = "Which candidate is most relevant?",
) -> DecisionQuestion:
    """A ``choice`` question over the given option values."""
    return DecisionQuestion(
        kind=QuestionKind.CHOICE,
        name=name,
        instructions=instructions,
        choices=tuple(DecisionChoice(value=value) for value in options),
    )


def predicate_question(
    name: str = "q_complete",
    *,
    instructions: str | None = "Is this work complete?",
) -> DecisionQuestion:
    """A ``predicate`` question."""
    return DecisionQuestion(
        kind=QuestionKind.PREDICATE,
        name=name,
        instructions=instructions,
    )


def choice_request(
    question: DecisionQuestion | None = None,
    *,
    model: str = "decision",
    text: str = "find the relevant snapshot",
) -> DecisionRequest:
    """A request carrying one choice question."""
    return DecisionRequest(
        input=text,
        questions=(question or choice_question(),),
        model=model,
    )


def choice_answer_payload(
    *,
    name: str = "s1_candidate_selection",
    picked: str = "id-a",
    values: tuple[str, ...] = ("id-a", "id-b", "id-c"),
    confidence: float | None = 0.9,
    usage: Mapping[str, Any] | None = None,
    answer_type: str = "choice",
) -> dict[str, Any]:
    """A well-formed choice answer payload."""
    answer: dict[str, Any] = {
        "type": answer_type,
        "name": name,
        "choice": picked,
        "probabilities": [
            {"value": value, "probability": 0.5 if value == picked else 0.25}
            for value in values
        ],
    }
    if confidence is not None:
        answer["confidence"] = confidence
    payload: dict[str, Any] = {"model": "decision", "answers": [answer]}
    if usage is not None:
        payload["usage"] = usage
    return payload


def seam_record(
    seam_id: str = "snapshot.l3.selection",
    *,
    role: str = "snapshot_selection",
    monotonicity: Monotonicity = Monotonicity.WITHIN_SET,
    outcome_kind: str = "ordered_set",
    apply: Callable[[Any, Any], Any] | None = None,
    build_input: Callable[[Mapping[str, Any]], DecisionRequest] | None = None,
    question: DecisionQuestion | None = None,
    floor: float | None = None,
    gate_switch: Callable[[], bool] | None = None,
    question_id: str = "q1",
) -> SeamRecord:
    """A registrable seam record."""
    the_question = question or choice_question()

    def _build(context: Mapping[str, Any]) -> DecisionRequest:
        if build_input is not None:
            return build_input(context)
        return DecisionRequest(
            input=str(context.get("input", "find the relevant snapshot")),
            questions=(the_question,),
        )

    def _apply(heuristic: Any, overlay: Any) -> Any:
        if apply is not None:
            return apply(heuristic, overlay)
        # The default WITHIN_SET apply: rank the candidates by the
        # overlay's distribution, keeping only candidate ids.
        allowed = set(the_question.option_values)
        ranked = [
            value
            for value in (overlay.ranked_values() if overlay is not None else ())
            if value in allowed
        ]
        if not ranked:
            return heuristic
        return ranked

    return SeamRecord(
        seam_id=seam_id,
        role=role,
        question_kind=the_question.kind,
        build_input=_build,
        apply=_apply,
        monotonicity=monotonicity,
        outcome_kind=outcome_kind,
        confidence_floor=floor,
        gate_switch=gate_switch,
        question_id=question_id,
    )


class RowCapture:
    """A shadow-writer sink that captures the rendered rows."""

    def __init__(self) -> None:
        self.rows: list[str] = []

    def __call__(self, row: str) -> None:
        self.rows.append(row)

    @property
    def last(self) -> str:
        return self.rows[-1] if self.rows else ""


def make_shadow_writer(capture: RowCapture | None = None) -> ShadowWriter:
    """A shadow writer whose rows land in ``capture`` (or a fresh one)."""
    sink = capture if capture is not None else RowCapture()
    writer = ShadowWriter(sink=sink)
    return writer


def make_kill_resolver(
    values: Mapping[str, str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[KillChannelResolver, InMemoryKillStore]:
    """A kill resolver over an in-memory store."""
    store = InMemoryKillStore(values)
    return KillChannelResolver(store, env=env or {}), store


def answer_for_request(request: httpx.Request) -> dict[str, Any]:
    """Build a well-formed answer for whatever the request asked.

    The answer's ``name`` MATCHES the request's question name — the
    join key — so the happy path exercises the real mapper rather than
    an unmatched answer.
    """
    body = json.loads(request.content.decode("utf-8"))
    question = body["questions"][0]
    name = question["name"]
    kind = question["type"]
    model = body.get("model", "decision")
    if kind == "predicate":
        return {
            "model": model,
            "answers": [{"type": "predicate", "name": name, "probability": 0.8}],
        }
    if kind == "score":
        labels = [level["label"] for level in question.get("levels") or []]
        return {
            "model": model,
            "answers": [
                {
                    "type": "score",
                    "name": name,
                    "score": labels[0] if labels else "low",
                    "probabilities": [
                        {"value": label, "probability": 0.5} for label in labels
                    ],
                    "confidence": 0.8,
                }
            ],
        }
    options = [choice["value"] for choice in question.get("choices") or []]
    return {
        "model": model,
        "answers": [
            {
                "type": "choice",
                "name": name,
                "choice": options[0] if options else "",
                "probabilities": [
                    {"value": value, "probability": 0.5} for value in options
                ],
                "confidence": 0.9,
            }
        ],
    }


def smart_handler(request: httpx.Request) -> httpx.Response:
    """A handler that answers whatever was asked."""
    return json_response(answer_for_request(request))
