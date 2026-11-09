"""Decision module — shared types (thin client + seam registry).

Design of record: ``.agents/shared/planning/decision-module/``
(``scope-and-doctrine.md`` is the NORMATIVE ANCHOR; ``thin-client.md``
carries the wire contract and the 30-row failure table;
``seam-registry.md`` carries the registry contract).

This module holds ONLY the vocabulary the rest of the package speaks:
the question/answer wire shapes, the closed label vocabularies (the
two DISJOINT ones — ``FailureClass`` and ``SuppressionReason``), and the
direction/stage enums.

**Two label vocabularies, never overlapping** (``thin-client.md`` §6 /
``controls-and-phasing.md`` §2.2):

* rows 1–5 are PRE-FLIGHT — the client short-circuits BEFORE any socket
  is opened, so they are SUPPRESSIONS (``SuppressionReason``), never
  failures;
* rows 6–30 are the failure values, carrying **19 DISTINCT**
  ``failure_class`` labels (``FailureClass``) — ``seam_fault`` IS one of
  them.

The two enums are asserted disjoint by the package's own test
(``tests/unit/decision/``) so the split cannot drift.

Nothing in this module imports the metrics registry, the transport, or
the settings store: it is the dependency-free base of the package (the
narrow outward port in ``port.py`` may import it freely).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    "AnswerProbability",
    "DecisionAnswer",
    "DecisionChoice",
    "DecisionInputMessage",
    "DecisionInputPart",
    "DecisionLevel",
    "DecisionQuestion",
    "DecisionRequest",
    "DecisionStage",
    "FailureClass",
    "Monotonicity",
    "OverlayOutcome",
    "ProvisioningState",
    "QuestionKind",
    "SuppressionReason",
    "VERDICT_AGREE",
    "VERDICT_DISAGREE",
    "VERDICT_NO_OVERLAY",
    "VERDICT_UNANSWERED",
]


# ── Wire vocabulary (thin-client.md §2, PX-1…PX-3) ────────────────────────


class QuestionKind(str, Enum):
    """The proxy's typed question kinds (PX-1).

    ``predicate`` | ``choice`` | ``score``. Refusal is ANSWER-side only
    — it is never a request question type.
    """

    PREDICATE = "predicate"
    CHOICE = "choice"
    SCORE = "score"


class Monotonicity(str, Enum):
    """The declared direction an overlay may move the outcome.

    (``seam-registry.md`` §2.2 — D4 translated.)

    * ``TOWARD_COMPLETION`` — may ADD completions above a floor; may
      never remove one and may never deny (S4).
    * ``TOWARD_TIGHTENING`` — may only ADD a positive signal; may never
      remove one and may never loosen the gate (S3).
    * ``WITHIN_SET`` — may select/reorder WITHIN an existing candidate
      set; may not extend it and may not empty it (S1).
    * ``ADVISORY_ONLY`` — may only annotate; the heuristic decision
      stands regardless (S2).
    * ``NONE`` — the seam is EXCLUDED from overlay consultation
      (EX-1…EX-5). A registration declaring it is REFUSED.
    """

    TOWARD_COMPLETION = "TOWARD_COMPLETION"
    TOWARD_TIGHTENING = "TOWARD_TIGHTENING"
    WITHIN_SET = "WITHIN_SET"
    ADVISORY_ONLY = "ADVISORY_ONLY"
    NONE = "NONE"


class DecisionStage(str, Enum):
    """The per-seam stage key's two values (``seam-registry.md`` §3.4).

    ``shadow`` logs the A/B row and never acts (the safe default, the
    only one compatible with D3). ``deciding`` is the Emperor-ratified
    stage — unreachable in P1 (the registry refuses the transition).
    """

    SHADOW = "shadow"
    DECIDING = "deciding"


class FailureClass(str, Enum):
    """The ``failure_class`` label vocabulary — **19 DISTINCT** values.

    Rows 6–30 of ``thin-client.md`` §6 carry 25 failure VALUES over
    these 19 labels (the table's own count; R-A binds to 19). The
    vocabularies are DISJOINT from :class:`SuppressionReason`.

    ``SEAM_FAULT`` IS a failure class: it is a failure of the overlay
    machinery whether or not a socket was opened (cost accounting is
    unchanged — only socket-reached attempts bill).
    """

    CONNECT_ERROR = "connect_error"
    TIMEOUT = "timeout"
    HTTP_400 = "http_400"
    HTTP_401 = "http_401"
    HTTP_403 = "http_403"
    HTTP_404 = "http_404"
    HTTP_405 = "http_405"
    HTTP_413 = "http_413"
    HTTP_500 = "http_500"
    HTTP_502 = "http_502"
    HTTP_504 = "http_504"
    HTTP_OTHER = "http_other"
    MALFORMED_BODY = "malformed_body"
    UNMATCHED_ANSWER = "unmatched_answer"
    MALFORMED_ANSWERS = "malformed_answers"
    REFUSAL = "refusal"
    TOO_LARGE = "too_large"
    BELOW_FLOOR = "below_floor"
    SEAM_FAULT = "seam_fault"


class SuppressionReason(str, Enum):
    """The ``reason`` label vocabulary for suppressed consults — closed, 10.

    Rows 1–5 of ``thin-client.md`` §6 (pre-flight, no socket opened)
    plus the CONTROL decisions that make no call at all: the switch
    states, the breaker, and the rate/concurrency budgets
    (``controls-and-phasing.md`` §2.2, §3.5).

    ``PRICING_UNCONFIGURED`` (L8) joins the vocabulary: the model has no
    operator-configured USD rate, so the consult is metered in TOKENS
    and the token ceiling is the fail-safe.

    Disjoint from :class:`FailureClass` by construction.
    """

    NOT_CONFIGURED = "not_configured"
    INVALID_REQUEST = "invalid_request"
    CANDIDATE_SET_TOO_SMALL = "candidate_set_too_small"
    SWITCH_OFF = "switch_off"
    SWITCH_UNREADABLE = "switch_unreadable"
    EXCLUDED_SEAM = "excluded_seam"
    BREAKER_OPEN = "breaker_open"
    RATE_LIMITED = "rate_limited"
    CONCURRENCY_LIMITED = "concurrency_limited"
    #: L8 — the model has no USD rate configured: the consult is metered
    #: in TOKENS and the token ceiling is the fail-safe. The control
    #: degrades VISIBLY rather than silently vanishing.
    PRICING_UNCONFIGURED = "pricing_unconfigured"


class ProvisioningState(str, Enum):
    """The per-seam provisioning state (``controls-and-phasing.md`` §2.9).

    ``ok`` | ``provisioning_missing`` | ``allow_list_excluded``.

    **A 502 is NEVER a provisioning verdict** — credential-unresolved,
    adapter-not-registered and upstream failures are indistinguishable
    to the client, so a transient 502 must never park a seam as
    ``provisioning_missing``. Only the probe-provable conditions (404,
    400 ``decision_misroute``) may set that state.
    """

    OK = "ok"
    PROVISIONING_MISSING = "provisioning_missing"
    ALLOW_LIST_EXCLUDED = "allow_list_excluded"


class OverlayOutcome(str, Enum):
    """The terminal state of ONE consult — what the caller observes.

    * ``APPLIED`` — the overlay's answer moved the outcome (deciding
      stage, monotonicity satisfied).
    * ``LOGGED`` — the answer was recorded next to the heuristic's and
      the heuristic stood (shadow stage — D3).
    * ``SUPPRESSED`` — NO call was made (gate/breaker/budget/pre-flight).
    * ``FAILED`` — the call (or the consult) failed; the heuristic
      stands (D2).
    * ``REFUSED`` — the overlay's answer violated the seam's declared
      monotonicity; the heuristic stands (fail open).
    """

    APPLIED = "applied"
    LOGGED = "logged"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    REFUSED = "refused"


#: The shadow row's verdict vocabulary (``controls-and-phasing.md`` §2.6).
#: The METRIC ``verdict`` label keeps its three values
#: {agree, disagree, no_overlay} — ``VERDICT_UNANSWERED`` lives in the
#: SHADOW LOG's verdict field ONLY (FIX A: no new metric label value,
#: so the zero-headroom band stays 411); the unanswered consult is
#: COUNTED under the existing ``unmatched_answer`` failure class.
VERDICT_AGREE = "agree"
VERDICT_DISAGREE = "disagree"
VERDICT_NO_OVERLAY = "no_overlay"
VERDICT_UNANSWERED = "unanswered"


# ── Request shapes (thin-client.md §2, PX-2) ──────────────────────────────


@dataclass(frozen=True)
class DecisionChoice:
    """One ``choices[]`` entry — ``{value, description?}`` (PX-2)."""

    value: str
    description: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"value": self.value}
        if self.description is not None:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True)
class DecisionLevel:
    """One ``levels[]`` entry — ``{label, description?}`` (PX-2).

    ``label`` is required by wire-shape / System One semantics.
    """

    label: str
    description: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"label": self.label}
        if self.description is not None:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True)
class DecisionQuestion:
    """One typed question — ``{type, name, instructions?, choices?, levels?}``.

    ``name`` MUST be non-empty: the System One adapter rejects an empty
    question name, which surfaces as a **502** and makes every consult
    silently inert (``thin-client.md`` §2). The client pre-flights it.

    ``instructions`` MUST be non-empty for ``predicate`` and ``score``:
    the upstream requires it there, and omitting it produced a 502
    (``Missing required parameter: 'questions[0].instructions'``) on a
    fully working path — an INVISIBLE 502 that fails open forever and
    never parks the seam, i.e. a silently dead overlay. The client
    pre-flights it (:attr:`requires_instructions`).
    """

    kind: QuestionKind
    name: str
    instructions: str | None = None
    choices: tuple[DecisionChoice, ...] = ()
    levels: tuple[DecisionLevel, ...] = ()

    @property
    def requires_instructions(self) -> bool:
        """True when the kind must carry non-empty ``instructions``.

        ``predicate`` and ``score`` REQUIRE them upstream; ``choice``
        does not.
        """
        return self.kind in (QuestionKind.PREDICATE, QuestionKind.SCORE)

    def to_payload(self) -> dict[str, Any]:
        """The CLIENT-FACING wire shape — ``choices[]`` / ``levels[]``.

        This is the shape the proxy's request validators accept
        (``pkg/decision/validators.go``: a choice question needs ≥2
        ``choices`` and a score question needs 2–10 ``levels``).

        **THE CLIENT NEVER EMITS A ``criteria`` KEY.** The proxy has two
        adapters: the OPENAI adapter is a BYTE-FAITHFUL PASS-THROUGH
        (only the literal ``model`` token is rewritten), so on the path
        P1 uses the request body IS the upstream body; the SYSTEM ONE
        adapter is the TRANSLATING one and it BUILDS the canonical
        R3/R4 ``criteria`` from these very ``choices[]``/``levels[]``
        entries (choice → a label→description map, score → an ordered
        label list). The canonicalization is the PROXY's business —
        supplying labels and descriptions in the OpenAI-decisions shape
        is the client's whole job.

        Proof the client contract did not move: ``pkg/decision/types.go``
        is ABSENT from the proxy's v185→v187 diff (10 files, +792/-118)
        — only the upstream wire did.
        """
        payload: dict[str, Any] = {"type": self.kind.value, "name": self.name}
        if self.instructions is not None:
            payload["instructions"] = self.instructions
        if self.choices:
            payload["choices"] = [c.to_payload() for c in self.choices]
        if self.levels:
            payload["levels"] = [level.to_payload() for level in self.levels]
        return payload

    @property
    def option_values(self) -> tuple[str, ...]:
        """The offered choice values (the candidate set for ``choice``)."""
        return tuple(choice.value for choice in self.choices)

    @property
    def level_labels(self) -> tuple[str, ...]:
        """The offered level labels."""
        return tuple(level.label for level in self.levels)


@dataclass(frozen=True)
class DecisionInputPart:
    """One part of an input message — ``{type, text}`` (PX-2)."""

    type: str
    text: str

    def to_payload(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True)
class DecisionInputMessage:
    """One input message — ``{role, parts[{type, text}]}`` (PX-2)."""

    role: str
    parts: tuple[DecisionInputPart, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {"role": self.role, "parts": [p.to_payload() for p in self.parts]}


@dataclass(frozen=True)
class DecisionRequest:
    """The wire request — ``{model, input, questions[], safety_identifier?}``.

    ``model`` is owned by the registry (the configured purpose-id); a
    seam never chooses it. The registry overwrites it on every consult.
    """

    input: str | tuple[DecisionInputMessage, ...]
    questions: tuple[DecisionQuestion, ...]
    model: str = ""
    safety_identifier: str | None = None

    def validate(self) -> None:
        """Raise ``ValueError`` when the request cannot be built.

        The client calls this in its pre-flight, so a request that would
        be rejected upstream (an empty question name, a missing
        ``instructions`` on a predicate/score question) is SUPPRESSED
        before any socket is opened — never turned into an invisible
        502.
        """
        if not self.questions:
            raise ValueError("at least one question is required")
        for question in self.questions:
            if not question.name.strip():
                raise ValueError(
                    "a question name is required (an empty name 502s upstream)"
                )
            if question.requires_instructions and not (
                question.instructions or ""
            ).strip():
                raise ValueError(
                    f"a {question.kind.value} question requires non-empty "
                    "instructions (omitting them 502s upstream)"
                )

    def build_payload(self) -> dict[str, Any]:
        """Validate, then render the wire payload (the client shape).

        The payload is ALWAYS the OpenAI-decisions shape — there is no
        ``criteria`` key anywhere in the client request path (the
        proxy derives the canonical R3/R4 criteria itself).

        Raises:
            ValueError: when :meth:`validate` fails.
        """
        self.validate()
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self.input
            if isinstance(self.input, str)
            else [m.to_payload() for m in self.input],
            "questions": [q.to_payload() for q in self.questions],
        }
        if self.safety_identifier is not None:
            payload["safety_identifier"] = self.safety_identifier
        return payload

    def to_payload(self) -> dict[str, Any]:
        """The client-facing wire payload (no validation)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self.input
            if isinstance(self.input, str)
            else [m.to_payload() for m in self.input],
            "questions": [q.to_payload() for q in self.questions],
        }
        if self.safety_identifier is not None:
            payload["safety_identifier"] = self.safety_identifier
        return payload

    def with_model(self, model: str) -> "DecisionRequest":
        """Return a copy carrying the registry-owned model id."""
        return replace(self, model=model)


# ── Response shape (thin-client.md §2, PX-3) ─────────────────────────────


@dataclass(frozen=True)
class AnswerProbability:
    """One entry of an answer's ``probabilities[]`` distribution.

    ``choice`` answers carry ``{value, probability}``; ``score`` answers
    carry ``{value, label, probability}`` (level-indexed).
    """

    value: str
    probability: float
    label: str | None = None


@dataclass(frozen=True)
class DecisionAnswer:
    """One typed answer — ``{type, name?, probability?, choice?, ...}``.

    ``type`` is ALWAYS emitted on the wire. ``name`` is the JOIN KEY to
    ``questions[].name``; an answer whose name is missing or unmatched
    fails open (``unmatched_answer``).
    """

    type: str
    name: str | None = None
    probability: float | None = None
    choice: str | None = None
    probabilities: tuple[AnswerProbability, ...] = ()
    confidence: float | None = None
    score: float | None = None
    refusal: str | None = None

    @property
    def is_refusal(self) -> bool:
        """True when the answer is a refusal (answer-side only, PX-1)."""
        return self.type == "refusal" or self.refusal is not None

    def distribution(self) -> tuple[AnswerProbability, ...]:
        """The full per-option distribution, ranked by probability desc.

        A ``choice`` answer carries the FULL distribution — that is what
        makes the multi-select contract expressible in ONE call
        (``thin-client.md`` §8.2).
        """
        return tuple(
            sorted(self.probabilities, key=lambda p: (-p.probability, p.value))
        )

    def ranked_values(self) -> tuple[str, ...]:
        """The distribution's option values, ranked (ties broken by value)."""
        return tuple(entry.value for entry in self.distribution())


def outcome_summary(
    outcome: Any,
    *,
    max_items: int = 32,
) -> dict[str, Any]:
    """Build a CONTENT-FREE summary of a seam outcome.

    Allowed: ids, hashes, scores, probabilities, counts, enum labels
    (``controls-and-phasing.md`` §2.6). A string that does not look like
    an opaque token (short, no whitespace) is replaced by its sha256
    prefix so a title/summary can never reach a shadow row.

    The function is deliberately total: any unexpected shape degrades to
    ``{"kind": "<type>"}`` rather than raising.
    """
    if outcome is None:
        return {"kind": "none"}
    if isinstance(outcome, bool):
        return {"kind": "bool", "value": outcome}
    if isinstance(outcome, (int, float)):
        return {"kind": "number", "value": outcome}
    if isinstance(outcome, str):
        return {"kind": "token", "value": _safe_token(outcome)}
    if isinstance(outcome, Mapping):
        safe: dict[str, Any] = {"kind": "map"}
        for key, value in list(outcome.items())[:max_items]:
            safe[str(key)] = outcome_summary(value, max_items=max_items)
        return safe
    if isinstance(outcome, (list, tuple, set, frozenset)):
        items = list(outcome)[:max_items]
        return {
            "kind": "list",
            "count": len(outcome),
            "items": [outcome_summary(item, max_items=max_items) for item in items],
        }
    return {"kind": type(outcome).__name__}


def _safe_token(value: str) -> str:
    """Return an opaque token, or a sha256 prefix when the value is prose."""
    text = value.strip()
    if text and len(text) <= 64 and not any(ch.isspace() for ch in text):
        return text
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
