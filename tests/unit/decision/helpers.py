"""Shared fakes for the decision-module unit tests.

Nothing here touches the network, the database, or the live daemon: the
HTTP layer is an ``httpx.MockTransport``, the kill store is the
in-memory implementation, and the shadow writer's sink captures the
rendered rows.
"""

from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path
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


# ═══════════════════════════════════════════════════════════════════════
# the in-memory mutant loader (the counter-mutation workhorse)
# ═══════════════════════════════════════════════════════════════════════


#: The repo root — the mutant loader resolves module paths against it.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def load_mutant_module(
    rel_path: str,
    mutations: list[tuple[str, str]],
    module_name: str,
) -> types.ModuleType:
    """Compile a DOCTORED copy of a module IN MEMORY.

    The mutant is the SHIPPED source with the named replacements applied
    — the real defect, not a re-implementation. Nothing is written to
    the tree; the module's absolute imports still resolve to the real
    package, so the mutant exercises the real collaborators.

    Args:
        rel_path: the module's path, relative to the repo root.
        mutations: ``(old, new)`` pairs; each ``old`` must occur
            EXACTLY once (a moved anchor is a loud failure, not a
            silent no-op).
        module_name: the module object's ``__name__``.

    Returns:
        The mutant module object.
    """
    path = _REPO_ROOT / rel_path
    source = path.read_text()
    for old, new in mutations:
        count = source.count(old)
        assert count == 1, (
            f"the mutation anchor must occur EXACTLY once in {rel_path} "
            f"(found {count}): {old[:70]!r}"
        )
        source = source.replace(old, new)
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


# ═══════════════════════════════════════════════════════════════════════
# D-2 — the PURE exception-leak walker
# ═══════════════════════════════════════════════════════════════════════


#: The calls that may receive an exception object: the shared
#: TYPE-only helper, and ``type()`` (the sanctioned type extraction).
SANCTIONED_CALLS = frozenset({"log_exception", "type"})


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_sanctioned(node: ast.Call) -> bool:
    return _call_name(node) in SANCTIONED_CALLS


def _contains_unsafe(node: ast.AST, tainted: set[str]) -> bool:
    """True when the expression can carry an exception's MESSAGE.

    The exception OBJECT is unsafe: it renders as its message. The
    TYPE extraction (``type(exc).__name__``) is safe — that is the
    sanctioned form, and the walker must not flag it.
    """
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name == "type":
            # type(exc).__name__ — the sanctioned extraction.
            return False
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Attribute):
        return _contains_unsafe(node.value, tainted)
    for child in ast.iter_child_nodes(node):
        if _contains_unsafe(child, tainted):
            return True
    return False


def _tainted_names(tree: ast.AST) -> set[str]:
    """The exception names + every name that DERIVES from one.

    Foreign except-names are included (``except X as e`` binds ``e``),
    and the derivation is transitive: ``error = f"{exc}"`` taints
    ``error``; ``payload = {"error": error}`` taints ``payload``;
    ``sink(payload)`` then leaks.
    """
    tainted = {
        handler.name
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler) and handler.name
    }
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value = None
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if value is None or not isinstance(value, ast.expr):
                continue
            if not _contains_unsafe(value, tainted):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in tainted:
                    tainted.add(target.id)
                    changed = True
    return tainted


def exception_leak_offenders(source: str) -> list[str]:
    """Every place in ``source`` where an exception's MESSAGE could go.

    A PURE function over source text — the pin feeds it the SHIPPED
    module sources AND the doctored :981 shape, so the walker's teeth
    are proven against the real defect.

    The classes it covers (the round-4 blind spots):

    1. ``str(exc)`` / ``repr(exc)`` — the message in text form;
    2. a FOREIGN except-name (``except X as e``) — not just exc/err/e;
    3. an INTERMEDIATE variable — a name assigned from an exception and
       then interpolated, passed to a sink, or placed in a payload;
    4. a BoolOp flow — ``error or "-"`` carries the tainted value into
       a logger call just as an f-string would;
    5. a payload/dict value — the notify-payload shape.
    """
    tree = ast.parse(source)
    exception_names = {
        handler.name
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler) and handler.name
    }
    tainted = _tainted_names(tree)
    # A name that is only RE-RAISED never renders the message.
    raised = {
        node.exc.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Name)
    }
    tainted -= raised

    offenders: list[str] = []

    def _flag(node: ast.AST, text: str) -> None:
        offenders.append(f"line {getattr(node, 'lineno', '?')}: {text}")

    for node in ast.walk(tree):
        # 1. an assignment that CAPTURES the exception content
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
            node.value, ast.expr
        ):
            targets = (
                list(node.targets)
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names and not any(n in raised for n in names):
                if _contains_unsafe(node.value, exception_names):
                    _flag(node, "an assignment captures the exception content")

        # 2. str()/repr() of an exception
        if isinstance(node, ast.Call) and _call_name(node) in {"str", "repr"}:
            if _contains_unsafe(node, exception_names):
                _flag(node, f"{_call_name(node)}() of an exception")

        # 3. a CALL (a sink: logger, notify sink, record) receiving tainted content
        if isinstance(node, ast.Call) and not _is_sanctioned(node):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if _contains_unsafe(arg, tainted):
                    _flag(node, "a call receives the exception content")
                    break

        # 4. a payload/dict value carrying tainted content
        if isinstance(node, ast.Dict):
            for value in node.values:
                if value is not None and _contains_unsafe(value, tainted):
                    _flag(node, "a payload/dict value carries the exception content")
                    break

        # 5. an f-string interpolating tainted content
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue) and _contains_unsafe(
                    part.value, tainted
                ):
                    _flag(node, "an f-string interpolates the exception content")
                    break

    return offenders


def test_the_exception_leak_walker_finds_nothing_in_the_shipped_package() -> None:
    """D-2 — the widened walker over the SHIPPED package: no offender.

    The walker covers the classes the round-4 pin missed: ``str(exc)``,
    a foreign except-name, an intermediate variable, a BoolOp flow and
    a payload value.
    """
    package_dir = _REPO_ROOT / "daemon" / "services" / "decision"
    offenders: list[str] = []
    for path in sorted(package_dir.glob("*.py")):
        for offender in exception_leak_offenders(path.read_text()):
            offenders.append(f"{path.name}: {offender}")
    assert offenders == [], (
        "the daemon log and the board body are agent-readable — no "
        f"exception MESSAGE may reach them. Offenders: {offenders}"
    )


def test_the_exception_leak_walker_flags_the_real_cost_trip_shape() -> None:
    """D-2 — the walker's TEETH, proven on the REAL :981 shape.

    The doctored source is the SHIPPED overlay with the D-1 fix reverted
    (the exact pre-round-5 text) — fed to the walker IN MEMORY. The
    walker MUST flag it; if it does not, the pin is green on the defect
    it exists to catch (the round-4 pattern).
    """
    source = (_REPO_ROOT / "daemon/services/decision/overlay.py").read_text()
    doctored = source.replace(
        '            error = type(exc).__name__\n',
        '            error = f"{type(exc).__name__}: {exc}"\n',
        1,
    )
    assert doctored != source, "the anchor moved — the pin must be updated"
    offenders = exception_leak_offenders(doctored)
    assert offenders, (
        "the walker MUST flag the real :981 shape (an assignment that "
        "captures the exception content)"
    )


def test_the_exception_leak_walker_flags_the_blind_spot_classes() -> None:
    """D-2 — each class the round-4 pin missed, proven individually."""
    cases = {
        "str(exc)": "try:\n    pass\nexcept Exception as exc:\n    logger.warning('%s', str(exc))\n",
        "repr(exc)": "try:\n    pass\nexcept Exception as exc:\n    logger.warning('%s', repr(exc))\n",
        "foreign except-name": "try:\n    pass\nexcept Exception as boom:\n    logger.warning('%s', boom)\n",
        "intermediate variable": (
            "try:\n    pass\nexcept Exception as exc:\n"
            "    detail = f'boom {exc}'\n"
            "    logger.warning('%s', detail)\n"
        ),
        "BoolOp flow": (
            "try:\n    pass\nexcept Exception as exc:\n"
            "    detail = f'boom {exc}'\n"
            "    logger.warning('%s', detail or '-')\n"
        ),
        "payload value": (
            "try:\n    pass\nexcept Exception as exc:\n"
            "    detail = f'boom {exc}'\n"
            "    payload = {'error': detail}\n"
            "    sink(payload)\n"
        ),
    }
    for label, source in cases.items():
        offenders = exception_leak_offenders(source)
        assert offenders, f"the walker missed the class: {label}"
