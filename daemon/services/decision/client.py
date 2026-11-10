"""Decision module — the thin client (``thin-client.md`` §1–§6).

**One HTTP transport + one typed mapper + one fail-open wrapper.** It
ASKS; it never DECIDES.

The delta from the transport template (``daemon/tools/image_gen.py``,
IP-3) is the whole point: image_gen fails CLOSED, this client fails
OPEN. Every one of the 30 rows of ``thin-client.md`` §6 yields the
HEURISTIC result — the client NEVER raises and NEVER returns an error
envelope (``DecisionClient.consult`` always returns a
:class:`ClientOutcome`).

Transport contract (§4):

* **exactly ONE attempt** — no retry, no failover, no idempotency key
  (the proxy issues no idempotency keys, so a retry DOUBLE-BILLS, PX-6);
* ``POST {base}/decisions`` — the base already carries ``/v1``;
* the wall clock is ``asyncio.wait_for`` with the per-lane budget;
* the response read is capped at **1 MiB**;
* the surface is a **blocking single JSON** — an SSE body is a content
  failure, not a stream to read.

Error classification branches on the **HTTP STATUS ONLY** (PX-8, §2.1):
never on body text, never on a ``message`` field, never on vendor copy.
The ONE exception is the structural ``decision_misroute`` marker on a
400 — a probe-PROVABLE provisioning condition, not a prose match.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import httpx

from daemon.services.decision.logging_safe import log_exception
from daemon.services.decision.config import DEFAULT_TIMEOUT_S, DecisionConfig
from daemon.services.decision.types import (
    AnswerProbability,
    DecisionAnswer,
    DecisionRequest,
    FailureClass,
    ProvisioningState,
    QuestionKind,
    SuppressionReason,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ClientOutcome",
    "DecisionClient",
    "KNOWN_ANSWER_TYPES",
    "MAX_RESPONSE_BYTES",
    "MISROUTE_MARKER",
]


#: The client-side response cap (``thin-client.md`` §4.3) — a typed
#: decision answer is tiny; an oversized body is a content failure.
MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MiB

#: The structural provisioning marker the proxy emits on a misrouted
#: 400 (``pkg/decision/handler.go:273``). Matched STRUCTURALLY — a key
#: of this name, or a string value EXACTLY equal to it — never as prose.
MISROUTE_MARKER = "decision_misroute"

#: The proxy's choice-question option bound (PX-2).
MAX_CHOICES = 255
#: The proxy's score-question level bound (PX-2).
MAX_LEVELS = 10

#: Transport seam (tests): the factory returns an httpx transport, which
#: is injected into every client this module builds — the house pattern
#: (``daemon/tools/image_gen.py``).
_TRANSPORT_FACTORY: Callable[[], Any] = lambda: None  # noqa: E731


@dataclass(frozen=True)
class ClientOutcome:
    """The client's verdict on ONE consult — never an error envelope.

    Exactly one of ``failure_class`` / ``suppression`` is set, or
    neither (success). ``answer`` is populated only on success.

    Attributes:
        answer: The typed answer (success only).
        failure_class: The failure label (rows 6–30) or ``None``.
        suppression: The suppression reason (rows 1–5) or ``None``.
        latency_s: The wall clock spent on the attempt (0 for pre-flight).
        cost_usd: The PROVIDER-REPORTED cost of this attempt in USD
            (0.0 when the provider reported none — L8: the cost is
            NEVER invented).
        total_tokens: The metered tokens from the ``usage`` block
            (``None`` when the block is absent).
        unanswered_questions: The question names with NO matching
            answer — an explicit UNANSWERED outcome (FIX A), never
            dropped and never substituted.
        usage: The proxy's ``usage`` block, when present.
        provisioning_state: The provisioning verdict to apply, or
            ``None`` for "UNCHANGED" (a 502 never moves it).
        http_status: The observed HTTP status (None pre-flight).
        reached_socket: True when an attempt was actually made — the
            cost counter counts ONLY these (PX-6).
    """

    answer: DecisionAnswer | None = None
    failure_class: FailureClass | None = None
    suppression: SuppressionReason | None = None
    latency_s: float = 0.0
    cost_usd: float = 0.0
    total_tokens: int | None = None
    unanswered_questions: tuple[str, ...] = ()
    usage: Mapping[str, Any] | None = None
    provisioning_state: ProvisioningState | None = None
    http_status: int | None = None
    reached_socket: bool = False


class DecisionClient:
    """The single-attempt, fail-open client for ``POST /v1/decisions``.

    The client owns the transport, the timeout, the response cap and the
    typed mapper. It never raises: every failure path returns a
    :class:`ClientOutcome` carrying a failure label, and the caller
    (the registry) turns that into the heuristic result (D2).
    """

    def __init__(
        self,
        config: DecisionConfig,
        *,
        transport_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        configured: bool | None = None,
    ) -> None:
        """Build the client.

        Args:
            config: The resolved configuration (``thin-client.md`` §3).
            transport_factory: Test seam — returns the httpx transport
                injected into every client this instance builds.
            clock: Monotonic clock seam (latency measurement).
            configured: An EXPLICIT decision signal (Q2). ``None`` ⇒
                resolved duck-typed from ``config`` (``is_configured()`` /
                ``explicitly_configured``, then the package-level signal
                check over the environment).
        """
        self._config = config
        self._transport_factory = transport_factory or _TRANSPORT_FACTORY
        self._clock = clock
        self._configured_override = configured

    # ── public surface ──────────────────────────────────────────────────

    async def consult(
        self,
        request: DecisionRequest,
        *,
        floor: float | None = None,
        timeout_s: float | None = None,
    ) -> ClientOutcome:
        """Ask the proxy ONCE. Never raises; never returns an error.

        Args:
            request: The wire request (the registry owns the model id).
            floor: The seam's confidence floor (``thin-client.md`` §8.2.1).
                ``None`` means "no floor in force" — the shadow stage
                applies no floor, so a missing ``confidence`` is NOT a
                failure there.
            timeout_s: The per-lane budget override; defaults to the
                configured timeout.

        Returns:
            A :class:`ClientOutcome` — success, a failure label, or a
            suppression reason. Never raises.
        """
        started = self._clock()

        # (g1) — the pre-flight sits INSIDE a never-raises guard: the
        # thin client must NEVER raise into the caller, and a malformed
        # request shape must yield the failure outcome (the caller's
        # heuristic stands), not an exception. A pre-flight fault NEVER
        # opened a socket, so it is not D3-floor-eligible.
        try:
            preflight = self._preflight(request)
            if preflight is not None:
                return preflight
        except Exception as exc:  # noqa: BLE001 — never raises
            log_exception(
                logger,
                logging.DEBUG,
                "decision preflight fault (non-fatal)",
                exc,
            )
            return self._failure(
                FailureClass.SEAM_FAULT,
                latency_s=self._clock() - started,
                reached_socket=False,
            )

        budget = float(timeout_s if timeout_s is not None else self._config.timeout_s)
        # (g2) — the budget is CLAMPED, twice: ``not (b > 0)`` also
        # rejects NaN (every comparison with NaN is False), and the
        # config tier falls back to the §4.2 default S1 budget so a
        # garbage config can never produce an unbounded/NaN wait_for.
        if not (budget > 0):
            budget = float(self._config.timeout_s)
        if not (budget > 0):
            budget = DEFAULT_TIMEOUT_S

        try:
            status, raw_body, oversize, sse = await asyncio.wait_for(
                self._post(request, budget), timeout=budget
            )
        except asyncio.TimeoutError:
            return self._failure(
                FailureClass.TIMEOUT,
                latency_s=self._clock() - started,
                reached_socket=True,
            )
        except httpx.ConnectTimeout:
            # (e) — the CONNECT phase timed out: the TCP connection was
            # never established, so no socket was opened and the attempt
            # is NOT D3-floor-eligible (no server-side spend can have
            # accrued). Listed BEFORE the broad TimeoutException.
            return self._failure(
                FailureClass.TIMEOUT,
                latency_s=self._clock() - started,
                reached_socket=False,
            )
        except httpx.TimeoutException:
            # A mid-flight timeout (read/write/pool): the socket WAS
            # open — the attempt bills (§3.5.3 + the D3 floor).
            return self._failure(
                FailureClass.TIMEOUT,
                latency_s=self._clock() - started,
                reached_socket=True,
            )
        except httpx.ConnectError:
            # (e) — connection refused / DNS failure / unreachable host:
            # the connection was never established, so no socket was
            # opened and the attempt is NOT D3-floor-eligible. Listed
            # BEFORE the broad HTTPError.
            return self._failure(
                FailureClass.CONNECT_ERROR,
                latency_s=self._clock() - started,
                reached_socket=False,
            )
        except httpx.HTTPError:
            # DNS failure, connection refused, TLS failure — one label
            # for every transport fault (§6 rows 6–8). Anything reaching
            # THIS handler (not ConnectError above) faulted MID-FLIGHT —
            # the socket was open — so the conservative
            # ``reached_socket=True`` stands.
            return self._failure(
                FailureClass.CONNECT_ERROR,
                latency_s=self._clock() - started,
                reached_socket=True,
            )
        except Exception as exc:  # noqa: BLE001 — the client never raises
            log_exception(
                logger,
                logging.DEBUG,
                "decision client internal fault (non-fatal)",
                exc,
            )
            return self._failure(
                FailureClass.SEAM_FAULT,
                latency_s=self._clock() - started,
                reached_socket=True,
            )

        latency = self._clock() - started

        if oversize:
            return self._failure(
                FailureClass.TOO_LARGE,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )

        if status != 200:
            return self._status_failure(status, raw_body, latency)

        if sse:
            # A streaming body on this route is a CONTENT failure
            # (§6 row 21) — the surface is a blocking single JSON.
            return self._failure(
                FailureClass.MALFORMED_BODY,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )

        return self._map_success(request, raw_body, latency, floor)

    # ── pre-flight (rows 1–5 — NO socket is opened) ─────────────────────

    def _preflight(self, request: DecisionRequest) -> ClientOutcome | None:
        """The five pre-flight rows. ``None`` means "proceed to the call".

        Rows 1–5 are SUPPRESSIONS, never failures: the client
        short-circuits BEFORE any socket is opened, so no cost is
        incurred (``thin-client.md`` §6).
        """
        # Q2 — the CONFIGURED predicate is an EXPLICIT decision signal
        # (DECISION_BASE_URL / DECISION_API_KEY in the env, or a
        # ``decision:`` yaml block). Merely INHERITING the OPENAI_*
        # values is not enough: the decision row routes to a PAID
        # upstream, so an operator who never opted in must never be
        # consulted — and never billed.
        if not self._is_configured():
            return self._suppressed(SuppressionReason.NOT_CONFIGURED)
        # Rows 1-3 of §6 — the emptiness checks are SEPARATE from the
        # configured predicate: a configured module with a missing base
        # URL / key / model is still not_configured.
        if not self._config.base_url or not self._config.api_key or not self._config.model:
            return self._suppressed(SuppressionReason.NOT_CONFIGURED)

        raw_input = request.input
        if isinstance(raw_input, str):
            if not raw_input.strip():
                return self._suppressed(SuppressionReason.INVALID_REQUEST)
        elif not raw_input:
            return self._suppressed(SuppressionReason.INVALID_REQUEST)

        if not request.questions:
            return self._suppressed(SuppressionReason.INVALID_REQUEST)

        # The structural request validation: an empty question name, a
        # missing ``instructions`` on a predicate/score question — both
        # 502 upstream (an INVISIBLE 502 that fails open forever). They
        # are SUPPRESSED here, before any socket is opened.
        try:
            request.validate()
        except ValueError:
            return self._suppressed(SuppressionReason.INVALID_REQUEST)

        for question in request.questions:
            if question.kind is QuestionKind.CHOICE:
                if len(question.choices) < 2:
                    # The n >= 2 precondition (§6 row 5).
                    return self._suppressed(SuppressionReason.CANDIDATE_SET_TOO_SMALL)
                if len(question.choices) > MAX_CHOICES:
                    return self._suppressed(SuppressionReason.INVALID_REQUEST)
            elif question.kind is QuestionKind.SCORE:
                if len(question.levels) < 2:
                    return self._suppressed(SuppressionReason.CANDIDATE_SET_TOO_SMALL)
                if len(question.levels) > MAX_LEVELS:
                    return self._suppressed(SuppressionReason.INVALID_REQUEST)
        return None

    def _is_configured(self) -> bool:
        """The Q2 CONFIGURED predicate (duck-typed for the house config).

        The house ``DecisionConfig`` (``daemon/config.py``) carries no
        signal field — it is the wiring round's territory, not mine —
        so the predicate is resolved duck-typed: an explicit override
        (the wiring test's hermetic signal), then ``is_configured()`` /
        ``explicitly_configured`` if the config carries them, then the
        package-level signal check over the environment.
        """
        if self._configured_override is not None:
            return bool(self._configured_override)
        cfg = self._config
        if hasattr(cfg, "is_configured") and callable(cfg.is_configured):
            try:
                return bool(cfg.is_configured())
            except TypeError:
                pass
        if hasattr(cfg, "explicitly_configured"):
            return bool(cfg.explicitly_configured)
        return DecisionConfig.has_explicit_decision_signal()

    # ── transport ───────────────────────────────────────────────────────

    async def _post(
        self, request: DecisionRequest, budget: float
    ) -> tuple[int, bytes, bool, bool]:
        """POST once and read the body under the 1 MiB cap.

        Returns ``(status, body, oversize, sse)``.
        """
        payload = request.build_payload()
        headers = self._config.headers()
        url = self._config.decisions_url
        timeout = httpx.Timeout(budget, connect=min(budget, 10.0))
        async with httpx.AsyncClient(
            timeout=timeout, transport=self._transport_factory()
        ) as client:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                status = int(response.status_code)
                content_type = str(response.headers.get("content-type", "")).lower()
                sse = "text/event-stream" in content_type
                chunks: list[bytes] = []
                total = 0
                oversize = False
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        oversize = True
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
        if not sse and body[:16].lstrip().startswith((b"data:", b"event:")):
            sse = True
        return status, body, oversize, sse

    # ── status branching (PX-8 — HTTP STATUS ONLY) ──────────────────────

    def _status_failure(
        self, status: int, body: bytes, latency: float
    ) -> ClientOutcome:
        """Map a non-200 status onto its failure label.

        The branch is on the STATUS. The body is inspected ONLY for the
        two probe-PROVABLE provisioning conditions, and only
        STRUCTURALLY (an envelope shape / a ``code`` field) — never by
        prose-matching a message.

        Live-confirmed taxonomy:

        * 404 with the proxy's error envelope ⇒ the model row is ABSENT
          ⇒ probe-provable provisioning verdict. A transport-level 404
          for a missing path is a plain ``404 Not Found`` ⇒ NOT a
          provisioning verdict.
        * 400 WITH ``code: "decision_misroute"`` ⇒ the second
          probe-provable provisioning verdict.
        * 400 WITHOUT that code ⇒ proxy request-shape validation, which
          fires BEFORE model resolution ⇒ NOT a provisioning verdict ⇒
          the SUPPRESSED ``invalid_request`` class.
        * 502 ⇒ the upstream envelope relayed VERBATIM (with the
          upstream's own ``type``). The body is OPAQUE: eight distinct
          causes all land on 502, so a 502 proves NOTHING about
          provisioning and must NEVER park a seam NOT_ENABLED.
        """
        if status == 400:
            if _carries_misroute_code(body):
                # The probe-provable provisioning verdict.
                return self._failure(
                    FailureClass.HTTP_400,
                    latency_s=latency,
                    reached_socket=True,
                    http_status=status,
                    provisioning_state=ProvisioningState.PROVISIONING_MISSING,
                )
            # A request-shape rejection: the proxy refused the request
            # before model resolution. Not a provisioning verdict — a
            # caller-side error, reported as a suppression.
            return self._post_call_suppression(
                SuppressionReason.INVALID_REQUEST,
                latency_s=latency,
                http_status=status,
            )
        if status == 401:
            return self._failure(
                FailureClass.HTTP_401,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        if status == 403:
            return self._failure(
                FailureClass.HTTP_403,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
                # The per-token model allow-list rejection (PX-8 / §S.3
                # row 44) — a distinct reason, never a provisioning verdict.
                provisioning_state=ProvisioningState.ALLOW_LIST_EXCLUDED,
            )
        if status == 404:
            if _carries_proxy_error_envelope(body):
                # The proxy's own error envelope ⇒ the model row is
                # absent ⇒ probe-provable provisioning verdict.
                return self._failure(
                    FailureClass.HTTP_404,
                    latency_s=latency,
                    reached_socket=True,
                    http_status=status,
                    provisioning_state=ProvisioningState.PROVISIONING_MISSING,
                )
            # A transport-level 404 (a missing path on another server) —
            # plain http_404, NO provisioning verdict.
            return self._failure(
                FailureClass.HTTP_404,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        if status == 405:
            return self._failure(
                FailureClass.HTTP_405,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        if status == 413:
            return self._failure(
                FailureClass.HTTP_413,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        if status == 500:
            return self._failure(
                FailureClass.HTTP_500,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        if status == 502:
            # EVERY 502 cause folds into ONE label (§6 row 17) — the
            # relayed envelope's text is NEVER inspected.
            return self._failure(
                FailureClass.HTTP_502,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        if status == 504:
            return self._failure(
                FailureClass.HTTP_504,
                latency_s=latency,
                reached_socket=True,
                http_status=status,
            )
        return self._failure(
            FailureClass.HTTP_OTHER,
            latency_s=latency,
            reached_socket=True,
            http_status=status,
        )

    # ── typed mapping (rows 20–29) ──────────────────────────────────────

    def _map_success(
        self,
        request: DecisionRequest,
        body: bytes,
        latency: float,
        floor: float | None,
    ) -> ClientOutcome:
        """Map a 200 body onto a typed answer — or a failure label."""
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._failure(
                FailureClass.MALFORMED_BODY,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
            )
        if not isinstance(parsed, dict):
            return self._failure(
                FailureClass.MALFORMED_BODY,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
            )

        usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
        cost, tokens = self._usage_facts(usage)

        raw_answers = parsed.get("answers")
        if not isinstance(raw_answers, list) or not raw_answers:
            # Missing ``answers`` or an empty list (§6 rows 22–23).
            return self._failure(
                FailureClass.MALFORMED_BODY,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
                usage=usage,
                cost_usd=cost,
                total_tokens=tokens,
            )

        by_name: dict[str, tuple[Any, dict[str, Any]]] = {}
        for raw in raw_answers:
            if not isinstance(raw, dict):
                return self._failure(
                    FailureClass.MALFORMED_BODY,
                    latency_s=latency,
                    reached_socket=True,
                    http_status=200,
                    usage=usage,
                    cost_usd=cost,
                )
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                # The join key is missing (§6 row 24).
                return self._failure(
                    FailureClass.UNMATCHED_ANSWER,
                    latency_s=latency,
                    reached_socket=True,
                    http_status=200,
                    usage=usage,
                    cost_usd=cost,
                )
            by_name[name] = raw

        questions = {q.name: q for q in request.questions}
        for name in by_name:
            if name not in questions:
                # The answer does not match any question (§6 row 24).
                return self._failure(
                    FailureClass.UNMATCHED_ANSWER,
                    latency_s=latency,
                    reached_socket=True,
                    http_status=200,
                    usage=usage,
                    cost_usd=cost,
                )

        # COMPLETENESS (FIX A) — the join is BY NAME, never by index, and
        # a question with NO matching answers[].name is recorded as an
        # explicit UNANSWERED outcome: never dropped, never substituted
        # with another question's answer. The OPENAI adapter is a
        # byte-faithful pass-through, so completeness is NOT guaranteed
        # there (the System One adapter synthesizes a refusal for a
        # missing answer — pkg/decision/typesafe.go:424-427 — so this
        # branch is defensive but real).
        unanswered = tuple(
            question.name
            for question in request.questions
            if question.name not in by_name
        )
        if unanswered:
            return self._failure(
                FailureClass.UNMATCHED_ANSWER,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
                usage=usage,
                cost_usd=cost,
                unanswered_questions=unanswered,
            )

        # The join is BY NAME — the answer whose name matches the
        # question we asked, never a positional pick.
        first = request.questions[0].name
        raw_answer = by_name.get(first)
        if raw_answer is None:
            # The question we asked has no answer — the join produced
            # nothing usable ⇒ fail open.
            return self._failure(
                FailureClass.UNMATCHED_ANSWER,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
                usage=usage,
                cost_usd=cost,
                total_tokens=tokens,
            )
        raw = raw_answer
        question = questions[first]

        if raw.get("refusal") is not None or raw.get("type") == "refusal":
            return self._failure(
                FailureClass.REFUSAL,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
                usage=usage,
                cost_usd=cost,
                total_tokens=tokens,
            )

        answer = _parse_answer(raw)
        if answer is None:
            return self._failure(
                FailureClass.MALFORMED_ANSWERS,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
                usage=usage,
                cost_usd=cost,
                total_tokens=tokens,
            )

        invalid = _validate_answer(answer, question)
        if invalid is not None:
            return self._failure(
                FailureClass.MALFORMED_ANSWERS,
                latency_s=latency,
                reached_socket=True,
                http_status=200,
                usage=usage,
                cost_usd=cost,
                total_tokens=tokens,
            )

        if floor is not None:
            observed = _floor_value(answer, question)
            # An ABSENT field is never read as "zero" and never as
            # "above the floor" (§8.2.1).
            if observed is None or observed < floor:
                return self._failure(
                    FailureClass.BELOW_FLOOR,
                    latency_s=latency,
                    reached_socket=True,
                    http_status=200,
                    usage=usage,
                    cost_usd=cost,
                )

        return ClientOutcome(
            answer=answer,
            failure_class=None,
            suppression=None,
            latency_s=latency,
            cost_usd=cost,
            total_tokens=tokens,
            usage=usage,
            provisioning_state=None,
            http_status=200,
            reached_socket=True,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _usage_facts(self, usage: Mapping[str, Any] | None) -> tuple[float, int]:
        """The metered facts of one attempt — ``(reported_usd, tokens)``.

        **L8 — the cost is NEVER invented.** The proxy meters TOKENS
        and the decision lane's ``usage`` block carries no price; the
        provider's own reported cost is used when it responds with one
        (``cost`` / ``cost_usd`` / ``total_cost_usd``). The TOKEN count
        is returned separately so the caller can convert it with the
        OPERATOR-configured per-model rate, or fall back to the token
        ceiling when no rate exists.
        """
        usd = 0.0
        tokens = 0
        if usage:
            for key in ("cost_usd", "total_cost_usd", "cost"):
                raw = usage.get(key)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    if raw >= 0:
                        usd = float(raw)
                        break
            raw_tokens = usage.get("total_tokens")
            if isinstance(raw_tokens, (int, float)) and not isinstance(raw_tokens, bool):
                tokens = max(0, int(raw_tokens))
            else:
                # D-9 — a payload that carries usage tokens but NO price
                # must still ACCRUE to the token meter. ``total_tokens``
                # alone was the only shape read, so the standard
                # ``prompt_tokens``/``completion_tokens`` (and the
                # Responses-API ``input_tokens``/``output_tokens``)
                # shapes metered ZERO — the token ceiling could never
                # bind, and the USD ceiling was bypassable by a
                # no-price payload (L8: the token meter is the
                # fail-safe). Summing the two halves is NOT inventing a
                # cost: it is the same token count the provider reports
                # as ``total_tokens`` when it reports one.
                for left, right in (
                    ("prompt_tokens", "completion_tokens"),
                    ("input_tokens", "output_tokens"),
                ):
                    raw_left = usage.get(left)
                    raw_right = usage.get(right)
                    if not isinstance(raw_left, (int, float)) or isinstance(
                        raw_left, bool
                    ):
                        continue
                    if not isinstance(raw_right, (int, float)) or isinstance(
                        raw_right, bool
                    ):
                        continue
                    tokens = max(0, int(raw_left) + int(raw_right))
                    break
        return usd, tokens

    def _failure(
        self,
        failure_class: FailureClass,
        *,
        latency_s: float,
        reached_socket: bool,
        http_status: int | None = None,
        usage: Mapping[str, Any] | None = None,
        cost_usd: float = 0.0,
        total_tokens: int | None = None,
        unanswered_questions: tuple[str, ...] = (),
        provisioning_state: ProvisioningState | None = None,
    ) -> ClientOutcome:
        """Build a failure outcome (never raises)."""
        return ClientOutcome(
            answer=None,
            failure_class=failure_class,
            suppression=None,
            latency_s=latency_s,
            cost_usd=cost_usd,
            total_tokens=total_tokens,
            unanswered_questions=unanswered_questions,
            usage=usage,
            provisioning_state=provisioning_state,
            http_status=http_status,
            reached_socket=reached_socket,
        )

    def _suppressed(self, reason: SuppressionReason) -> ClientOutcome:
        """Build a PRE-FLIGHT suppression outcome (no socket opened)."""
        return ClientOutcome(
            answer=None,
            failure_class=None,
            suppression=reason,
            latency_s=0.0,
            cost_usd=0.0,
            usage=None,
            provisioning_state=None,
            http_status=None,
            reached_socket=False,
        )

    def _post_call_suppression(
        self,
        reason: SuppressionReason,
        *,
        latency_s: float,
        http_status: int,
    ) -> ClientOutcome:
        """Build a POST-CALL suppression outcome.

        The socket WAS reached (the proxy refused the request), so the
        attempt is COUNTED. The cost fields stay at zero here — the
        overlay applies the operator-configured per-attempt floor (D3)
        when an attempt reaches the socket but carries no usage.
        """
        return ClientOutcome(
            answer=None,
            failure_class=None,
            suppression=reason,
            latency_s=latency_s,
            cost_usd=0.0,
            total_tokens=0,
            usage=None,
            provisioning_state=None,
            http_status=http_status,
            reached_socket=True,
        )


# ── module-level helpers ─────────────────────────────────────────────────


#: The answer types the wire may carry. An answer whose ``type`` is
#: OUTSIDE this set (the System One answer synthesizer has been
#: observed relaying ``"noul"`` from upstreams outside the OpenAI
#: answer union) is a CONTENT failure that fails OPEN — never an error
#: to the caller.
KNOWN_ANSWER_TYPES: frozenset[str] = frozenset(
    {"predicate", "choice", "score", "refusal"}
)


def _parse_answer(raw: Mapping[str, Any]) -> DecisionAnswer | None:
    """Map one wire answer onto the typed shape (PX-3).

    ``None`` means "the shape is not mappable" — the caller turns that
    into ``malformed_answers`` and fails open. The mapper DEGRADES; it
    never raises.
    """
    kind = raw.get("type")
    if not isinstance(kind, str) or not kind:
        return None
    if kind not in KNOWN_ANSWER_TYPES:
        # An unexpected answer type (``"noul"`` and friends) — a
        # content failure, not a crash.
        return None

    probabilities: list[AnswerProbability] = []
    raw_probs = raw.get("probabilities")
    if isinstance(raw_probs, list):
        for entry in raw_probs:
            if not isinstance(entry, dict):
                return None
            value = entry.get("value")
            probability = entry.get("probability")
            if not isinstance(value, str) or not isinstance(probability, (int, float)):
                return None
            if isinstance(probability, bool):
                return None
            label = entry.get("label")
            probabilities.append(
                AnswerProbability(
                    value=value,
                    probability=float(probability),
                    label=label if isinstance(label, str) else None,
                )
            )
    elif raw_probs is not None:
        # ``probabilities`` arrived as a MAP (not the documented array)
        # — degrade to a content failure instead of raising.
        return None

    def _opt_float(key: str) -> float | None:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def _opt_str(key: str) -> str | None:
        value = raw.get(key)
        return value if isinstance(value, str) else None

    name = _opt_str("name")
    refusal = _opt_str("refusal")
    return DecisionAnswer(
        type=kind,
        name=name,
        probability=_opt_float("probability"),
        choice=_opt_str("choice"),
        probabilities=tuple(probabilities),
        confidence=_opt_float("confidence"),
        score=_opt_float("score"),
        refusal=refusal,
    )


def _validate_answer(answer: DecisionAnswer, question: Any) -> str | None:
    """Validate the answer against its question's kind and options.

    Returns ``None`` when the answer is valid, else a short reason (the
    caller maps it to ``malformed_answers``).
    """
    if question.kind is QuestionKind.PREDICATE:
        if answer.type != "predicate":
            return "type mismatch"
        if answer.probability is None:
            return "missing probability"
        if not 0.0 <= answer.probability <= 1.0:
            return "probability out of range"
        return None

    if question.kind is QuestionKind.CHOICE:
        if answer.type != "choice":
            return "type mismatch"
        offered = set(question.option_values)
        if answer.choice is None:
            return "missing choice"
        if answer.choice not in offered:
            return "option not offered"
        for entry in answer.probabilities:
            if entry.value not in offered:
                return "option not offered"
            if not 0.0 <= entry.probability <= 1.0:
                return "probability out of range"
        return None

    # score
    if answer.type != "score":
        return "type mismatch"
    offered = set(question.level_labels)
    if answer.score is None:
        return "missing score"
    if answer.score not in offered:
        return "level not offered"
    for entry in answer.probabilities:
        if entry.value not in offered:
            return "level not offered"
        if not 0.0 <= entry.probability <= 1.0:
            return "probability out of range"
    return None


def _floor_value(answer: DecisionAnswer, question: Any) -> float | None:
    """The field the seam's floor reads (``thin-client.md`` §8.2.1).

    ``choice``/``score`` read ``confidence``; ``predicate`` reads
    ``probability`` (``confidence`` NEVER exists on a predicate).
    """
    if question.kind is QuestionKind.PREDICATE:
        return answer.probability
    return answer.confidence


def _carries_misroute_code(body: bytes) -> bool:
    """Structural detection of the ``decision_misroute`` marker.

    The discriminator is the structural ``code`` field: a ``code``
    whose value is EXACTLY ``decision_misroute`` — at the top level or
    inside an ``error`` object. Prose that merely CONTAINS the token
    does NOT match: this is never a substring match on vendor copy.
    """
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    if _code_is_misroute(parsed):
        return True
    error = parsed.get("error")
    if isinstance(error, dict):
        return _code_is_misroute(error)
    return False


def _code_is_misroute(node: Mapping[str, Any]) -> bool:
    """True when the node's ``code`` field is the misroute marker."""
    code = node.get("code")
    return isinstance(code, str) and code.strip().lower() == MISROUTE_MARKER


def _carries_proxy_error_envelope(body: bytes) -> bool:
    """True when the body carries the PROXY's own error envelope.

    The envelope is structural: a JSON object with an ``error`` object
    that carries a ``type`` field. A transport-level 404 (a missing
    path served by a different server) is plain text and never matches.
    """
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    error = parsed.get("error")
    if not isinstance(error, dict):
        return False
    return isinstance(error.get("type"), str) and bool(error.get("type").strip())
