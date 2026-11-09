"""Council round 4 — the tester's four blockers + the fold-now items.

B-1  the exception MESSAGE never reaches the daemon log (the log is
     AGENT-READABLE — the ``system-log`` category is granted to
     ``worker`` and ``maintenancer``, so an interpolated exception is a
     content leak by another name);
B-2  the decision config is OPTIONAL — absent or invalid ⇒ the module is
     NOT INSTALLED (inert), the manager never raises;
fold 2  the outward port reports the BILLED cost, not the provider-
     reported cost;
fold 3  the ledger's concurrency pin must genuinely exercise concurrency;
fold 4  ``sanitize_token("")`` keeps an empty value EMPTY.

Every discrimination proof here is IN-MEMORY (a monkeypatched or
subclassed copy) — this round writes to the shared tree with NOTHING
but the source under test, and never reverts with git.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from daemon.services.decision import metrics
from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import GLOBAL_ENABLED_KEY
from daemon.services.decision.kill import (
    InMemoryKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.logging_safe import log_exception
from daemon.services.decision.overlay import DecisionOverlay
from daemon.services.decision.registry import SeamRegistry
from daemon.services.decision.shadow import ShadowWriter, sanitize_token
from daemon.services.decision.types import FailureClass
from tests.unit.decision.helpers import (
    RowCapture,
    choice_request,
    json_response,
    make_config,
    seam_record,
    smart_handler,
)

#: A secret-shaped marker. The daemon log is agent-readable: if this
#: string EVER appears in a log line, an agent can read a secret.
SECRET = "sk-live-SECRETMARKER-do-not-log-9f3a2b"


def _package_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "daemon" / "services" / "decision"


# ═══════════════════════════════════════════════════════════════════════
# B-1 — the exception TYPE only
# ═══════════════════════════════════════════════════════════════════════


def test_the_shared_helper_never_logs_the_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B-1 — the SINGLE surface: the helper logs the TYPE and nothing else.

    Discriminating: a helper that interpolated the exception (or its
    message) would put the secret in the log.
    """
    logger = logging.getLogger("daemon.services.decision.test")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        log_exception(
            logger,
            logging.WARNING,
            "a fault happened for %s",
            ValueError(f"the secret is {SECRET} and the prompt text is here"),
            "snap-1",
        )
    assert SECRET not in caplog.text, "the exception MESSAGE must never be logged"
    assert "ValueError" in caplog.text, "the failure CLASS must stay visible"
    assert "snap-1" in caplog.text, "the caller's own context (an id) stays"


def test_no_decision_log_site_can_receive_an_exception_object() -> None:
    """B-1 — the STATIC half: no ``logger.*`` call in the package may
    receive an exception object (a bare name, an f-string interpolation,
    or ``str(exc)``).

    This is what keeps the helper SINGLE-surface: a future site that
    bypasses :func:`log_exception` REDs here, before it can leak.
    """
    offenders: list[str] = []
    for path in sorted(_package_dir().glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "logger"
            ):
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Name) and arg.id in {
                    "exc",
                    "err",
                    "error",
                    "e",
                    "exception",
                }:
                    offenders.append(f"{path.name}:{arg.lineno} — bare {arg.id}")
                if isinstance(arg, ast.JoinedStr):
                    for part in arg.values:
                        if isinstance(part, ast.FormattedValue) and isinstance(
                            part.value, ast.Name
                        ) and part.value.id in {
                            "exc",
                            "err",
                            "error",
                            "e",
                            "exception",
                        }:
                            offenders.append(
                                f"{path.name}:{part.lineno} — f-string {{{part.value.id}}}"
                            )
                if (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr in {"format", "join"}
                ):
                    offenders.append(f"{path.name}:{arg.lineno} — exception formatting")
    assert offenders == [], (
        "a decision log site may not receive an exception object — the "
        f"daemon log is agent-readable. Offenders: {offenders}"
    )


@pytest.mark.asyncio
async def test_the_decision_log_sites_never_leak_a_secret_bearing_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """B-1 — the BEHAVIOURAL half: force a fault whose MESSAGE carries a
    secret at each representative site and prove the secret appears in
    NO captured log line."""
    config = make_config()
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response(
            {
                "model": "decision",
                "answers": [
                    {
                        "type": "choice",
                        "name": "s1_candidate_selection",
                        "choice": "id-a",
                        "probabilities": [
                            {"value": v, "probability": 0.5}
                            for v in ("id-a", "id-b", "id-c")
                        ],
                        "confidence": 0.9,
                    }
                ],
            }
        )

    def _boom(message: str) -> Exception:
        return RuntimeError(f"{message}: {SECRET}")

    # ── the client: the internal fault (the site the tester named) ──
    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(_handler)
    )
    with caplog.at_level(logging.DEBUG):
        original_post = client._post

        async def _faulting_post(*args: Any, **kwargs: Any) -> Any:
            raise _boom("the client internal fault")

        client._post = _faulting_post  # type: ignore[method-assign]
        outcome = await client.consult(choice_request())
        client._post = original_post  # type: ignore[method-assign]
    assert outcome.failure_class == FailureClass.SEAM_FAULT.value
    assert SECRET not in caplog.text, "the client's internal-fault site leaked"

    # ── the client: the pre-flight fault ──
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        original_preflight = client._preflight

        def _faulting_preflight(*args: Any, **kwargs: Any) -> Any:
            raise _boom("the preflight fault")

        client._preflight = _faulting_preflight  # type: ignore[method-assign]
        outcome = await client.consult(choice_request())
        client._preflight = original_preflight  # type: ignore[method-assign]
    assert outcome.failure_class == FailureClass.SEAM_FAULT.value
    assert SECRET not in caplog.text, "the client's pre-flight site leaked"

    # ── the registry: build_input, consult, apply, comparator ──
    caplog.clear()
    registry = SeamRegistry(client=client, config=config)

    def _boom_build(context: Any) -> Any:
        raise _boom("build_input")

    record = seam_record(build_input=_boom_build)
    registry.register(record)
    with caplog.at_level(logging.WARNING):
        result = await registry.execute(
            "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
        )
    assert result is not None
    assert SECRET not in caplog.text, "the registry's build_input site leaked"

    caplog.clear()
    registry2 = SeamRegistry(client=client, config=config)
    registry2.register(seam_record(apply=lambda h, o: (_ for _ in ()).throw(_boom("apply"))))
    with caplog.at_level(logging.WARNING):
        await registry2.execute(
            "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
        )
    assert SECRET not in caplog.text, "the registry's apply site leaked"

    # ── the overlay: the gate switch, the notify sink, the shadow row ──
    caplog.clear()
    gate_record = seam_record(gate_switch=lambda: (_ for _ in ()).throw(_boom("gate")))
    registry3 = SeamRegistry(client=client, config=config)
    registry3.register(gate_record)
    overlay = DecisionOverlay(
        registry=registry3,
        kill=KillChannelResolver(
            InMemoryKillStore(
                {
                    GLOBAL_ENABLED_KEY: "1",
                    seam_enabled_key("snapshot.l3.selection"): "1",
                    seam_stage_key("snapshot.l3.selection"): "shadow",
                }
            )
        ),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
        notify_sink=lambda payload: (_ for _ in ()).throw(_boom("notify sink")),
    )
    with caplog.at_level(logging.WARNING):
        await overlay.consult(
            "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
        )
    assert SECRET not in caplog.text, "the overlay's gate-switch site leaked"

    # ── the shadow writer: a broken sink ──
    caplog.clear()
    writer = ShadowWriter(sink=lambda row: (_ for _ in ()).throw(_boom("shadow sink")))
    with caplog.at_level(logging.DEBUG):
        writer.write(
            __import__(
                "daemon.services.decision.shadow", fromlist=["ShadowRow"]
            ).ShadowRow(
                seam_id="snapshot.l3.selection",
                question_id="q1",
                mode="shadow",
                overlay_outcome="",
                heuristic_outcome="",
                verdict="no_overlay",
                latency_ms=0,
                failure_class="",
                model_id="decision",
                reason="",
                switch_state="on",
            )
        )
    assert SECRET not in caplog.text, "the shadow-writer site leaked"


# ═══════════════════════════════════════════════════════════════════════
# B-2 — the decision config is OPTIONAL
# ═══════════════════════════════════════════════════════════════════════


def test_b2_an_absent_decision_config_leaves_the_module_inert() -> None:
    """B-2 — a config object WITHOUT the decision section ⇒ the module
    is NOT INSTALLED (``None``), never a raise.

    This is the MEASURED red: a ``MagicMock(spec=Config)`` fixture has
    no ``decision`` attribute and raises ``AttributeError`` on access.
    """
    from daemon.services.decision.factory import build_decision_overlay_defensively

    class _NoDecisionConfig:
        """A config object whose ``decision`` access RAISES (the spec'd
        mock's behaviour)."""

        @property
        def decision(self) -> Any:
            raise AttributeError("decision")

    assert build_decision_overlay_defensively(
        engine=None, config=_NoDecisionConfig()
    ) is None

    class _PlainConfig:
        pass

    assert build_decision_overlay_defensively(
        engine=None, config=_PlainConfig()
    ) is None

    # A spec'd mock (the measured fixture shape) — the attribute access
    # raises, the helper returns None.
    spec_mock = MagicMock(spec=["persistence"])
    assert build_decision_overlay_defensively(
        engine=None, config=spec_mock
    ) is None


def test_b2_an_invalid_decision_config_leaves_the_module_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-2 — a decision section the factory cannot use ⇒ ``None``, not a
    raise.

    The failure is injected IN MEMORY (the factory's builder is
    replaced by one that raises) — no tree write, no git. The ambient
    OPENAI_* environment is scrubbed first so no live credential is
    ever materialised into a config object (and never printed).
    """
    from daemon.services.decision import factory as decision_factory

    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "DECISION_API_KEY", "DECISION_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    def _raising_builder(**kwargs: Any) -> Any:
        raise RuntimeError("the decision section is broken")

    monkeypatch.setattr(
        decision_factory, "build_decision_overlay", _raising_builder
    )

    class _Config:
        decision = object()

    assert decision_factory.build_decision_overlay_defensively(
        engine=None, config=_Config()
    ) is None, (
        "an invalid decision section must leave the module INERT, not raise"
    )


# ═══════════════════════════════════════════════════════════════════════
# fold 2 — the port reports the BILLED cost
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fold2_the_port_reports_the_billed_cost_not_the_reported_cost() -> None:
    """fold 2 — the outward port's ``cost_usd`` is the BILLED cost.

    A timeout reports 0.0 upstream but bills the D3 floor; the port's
    contract says "the billed cost", so it must carry the floor — not
    the provider-reported 0.0 (the mismatch).
    """
    floor = 0.25
    config = make_config(cost_floor_per_attempt_usd=floor)

    def _read_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out mid-read")

    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(_read_timeout)
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    overlay = DecisionOverlay(
        registry=registry,
        kill=KillChannelResolver(
            InMemoryKillStore(
                {
                    GLOBAL_ENABLED_KEY: "1",
                    seam_enabled_key("snapshot.l3.selection"): "1",
                    seam_stage_key("snapshot.l3.selection"): "shadow",
                }
            )
        ),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
    )
    result = await overlay.consult(
        "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
    )
    assert result.failure_class == FailureClass.TIMEOUT.value
    assert result.cost_usd == pytest.approx(floor), (
        "the port must report the BILLED cost (the D3 floor), not the "
        "provider-reported 0.0"
    )


# ═══════════════════════════════════════════════════════════════════════
# fold 3 — the concurrency pin must genuinely exercise concurrency
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fold3_the_ledger_pin_exercises_real_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fold 3 — the ledger's concurrency pin must NOT be vacuous.

    The earlier pin asserted only the total after ``asyncio.gather`` —
    which a SERIAL execution satisfies too (a ``max_workers=1``
    executor would pass it). This pin measures the REAL overlap: the
    in-flight count at the offload boundary must exceed 1, so the pin
    can only pass when the writes genuinely overlap.
    """
    from daemon.services.decision.cost_store import AppSettingCostAccumulator
    from tests.unit.decision.test_council_blockers import _sqlite_engine

    engine = _sqlite_engine()
    accumulator = AppSettingCostAccumulator(engine, period_getter=lambda: "2099-02")
    n = 20

    in_flight = 0
    peak = 0
    probe_lock = threading.Lock()
    original = accumulator._record_sync

    def _probed(*args: Any, **kwargs: Any) -> Any:
        nonlocal in_flight, peak
        with probe_lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            # Widen the overlap window so the offload pool really runs
            # the records in parallel.
            time.sleep(0.005)
            return original(*args, **kwargs)
        finally:
            with probe_lock:
                in_flight -= 1

    monkeypatch.setattr(accumulator, "_record_sync", _probed)
    await asyncio.gather(
        *[
            accumulator.record("snapshot.l3.selection", usd=0.01, tokens=10)
            for _ in range(n)
        ]
    )
    totals = await accumulator.current_totals("snapshot.l3.selection")

    assert peak > 1, (
        "the pin did NOT exercise concurrency — the records never "
        f"overlapped (peak in-flight={peak}); a serial executor would "
        "satisfy the old total-only assertion"
    )
    assert totals.month_to_date_usd == pytest.approx(n * 0.01), (
        f"the ledger lost deltas under concurrency: {totals.month_to_date_usd}"
    )
    assert totals.month_to_date_tokens == n * 10


# ═══════════════════════════════════════════════════════════════════════
# fold 4 — an empty value stays empty
# ═══════════════════════════════════════════════════════════════════════


def test_fold4_an_empty_value_stays_empty() -> None:
    """fold 4 — ``sanitize_token("")`` must NOT hash the empty string.

    The hash of "" is the CONSTANT ``sha256:e3b0c442...`` — in a row it
    reads as a real token and hides that the field was ABSENT.
    """
    assert sanitize_token("") == ""
    assert sanitize_token("   ") == ""
    assert not sanitize_token("").startswith("sha256:")

    # The non-empty shapes are unchanged.
    assert sanitize_token("snap-1") == "snap-1"
    assert sanitize_token("a title with spaces").startswith("sha256:")
    assert sanitize_token("x" * 65).startswith("sha256:")

    # A row rendered with an empty field shows an EMPTY field.
    from daemon.services.decision.shadow import ShadowRow

    row = ShadowRow(
        seam_id="snapshot.l3.selection",
        question_id="",
        mode="shadow",
        overlay_outcome="",
        heuristic_outcome="",
        verdict="no_overlay",
        latency_ms=0,
        failure_class="",
        model_id="decision",
        reason="",
        switch_state="on",
    )
    rendered = ShadowWriter(sink=RowCapture())._render(row)
    assert "sha256:e3b0c442" not in rendered, (
        "an empty field must not carry the hash of the empty string"
    )
