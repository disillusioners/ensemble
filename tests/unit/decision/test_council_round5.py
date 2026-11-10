"""Council round 5 — the tester's re-gate: the counter-mutation round.

The lane shipped evidence that was GREEN ON THE EXACT DEFECT it existed
to catch, twice. This round closes that pattern: every pin below is
**counter-mutation-proven** — the defect is planted (IN MEMORY) and the
pin is watched to go RED, then the shipped code is watched to go GREEN.

* **D-1** — the cost-trip path leaked the exception MESSAGE into the log
  AND the notify payload (two surfaces, one defect).
* **D-2** — the AST pin had blind spots: it did not see
  ``str(exc)``, a FOREIGN except-name, an INTERMEDIATE variable, or a
  BoolOp flow — so it was green on the real ``:981`` shape.
* **D-3** — the behavioural pin never entered the cost-trip path, so a
  full-leak mutant passed the whole suite.
* **D-9** — the USD ceiling was bypassable by a tokens-without-price
  payload: the token meter read only ``usage["total_tokens"]``, so the
  standard ``prompt_tokens``/``completion_tokens`` shape metered ZERO
  and the TOKEN CEILING could never bind (L8: the token meter is the
  fail-safe).
* **D-10** — the ``(e)`` floor-discriminator pin was VACUOUS: it
  asserted a METRIC delta after a bare client consult, which never
  accounts at all.

**THE DISCIPLINE (permanent for this lane).** Every counter-mutation
below runs IN MEMORY: a doctored source string compiled into a module
object, or a pure function fed a doctored source. No file is written to
the tree, and no ``git checkout`` / ``stash`` / ``reset`` / ``clean`` is
used anywhere.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from daemon.services.decision import metrics
from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import GLOBAL_ENABLED_KEY
from daemon.services.decision.cost_store import AppSettingCostAccumulator
from daemon.services.decision.kill import (
    InMemoryKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.overlay import DecisionOverlay
from daemon.services.decision.registry import SeamRegistry
from daemon.services.decision.shadow import ShadowWriter
from tests.unit.decision.helpers import (
    RowCapture,
    exception_leak_offenders,
    load_mutant_module,
    make_config,
    seam_record,
)

#: A secret-shaped marker: the daemon log AND the board body are
#: agent-readable surfaces, so this string must never reach either.
SECRET = "sk-live-SECRETMARKER-do-not-log-9f3a2b"

_REPO_ROOT = Path(__file__).resolve().parents[3]


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


# ═══════════════════════════════════════════════════════════════════════
# D-3 — the cost-trip path: no leak in the log, none in the payload
# ═══════════════════════════════════════════════════════════════════════


class _TripExplodingKill:
    """A kill resolver whose ``trip_shadow_off`` raises with a SECRET."""

    async def trip_shadow_off(self, seam_ids, global_off: bool) -> Any:
        raise RuntimeError(f"the write failed and the secret is {SECRET}")


async def _drive_trip(overlay: DecisionOverlay, payloads: list[dict]) -> Any:
    """Drive the overlay's cost-trip path with an exploding kill."""
    return await overlay._trip(
        reason="global_ceiling",
        seam_ids=("snapshot.l3.selection",),
        global_off=True,
        month_to_date_usd=99.0,
        ceiling_usd=50.0,
    )


@pytest.mark.asyncio
async def test_the_cost_trip_path_leaks_no_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-3 — the behavioural pin: a secret-bearing exception in the
    cost-trip path reaches NEITHER the log NOR the notify payload.

    The two surfaces are one defect: the daemon log is agent-readable
    (``system-log`` is granted to ``worker``/``maintenainer``) and the
    board body is an agent-readable surface too.
    """
    config = make_config()
    client = DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(lambda r: httpx.Response(200))
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    payloads: list[dict] = []
    overlay = DecisionOverlay(
        registry=registry,
        kill=_TripExplodingKill(),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
        notify_sink=payloads.append,
    )
    with caplog.at_level(logging.DEBUG):
        await _drive_trip(overlay, payloads)

    assert payloads, "the notify sink must have been reached (the pin's premise)"
    assert SECRET not in caplog.text, "the exception MESSAGE reached the log"
    assert SECRET not in json.dumps(payloads, default=str), (
        "the exception MESSAGE reached the notify payload (the board body)"
    )
    # The TYPE is still visible to the operator (the log carries it).
    assert "RuntimeError" in caplog.text, "the failure CLASS must stay visible"


@pytest.mark.asyncio
async def test_the_cost_trip_leak_pin_is_discriminating(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """D-3 — the counter-mutation proof: the FULL-LEAK mutant (the
    pre-D-1 code) is planted IN MEMORY and the secret DOES leak — so
    the pin above is watching a live defect, not a dead one."""
    mutant = load_mutant_module(
        "daemon/services/decision/overlay.py",
        [
            (
                "            error = type(exc).__name__\n",
                '            error = f"{type(exc).__name__}: {exc}"\n',
            ),
            (
                '            "ceiling_usd": ceiling_usd,\n        }',
                '            "ceiling_usd": ceiling_usd,\n'
                '            "error": error,\n        }',
            ),
        ],
        "daemon.services.decision._mutant_overlay_round5",
    )
    config = make_config()
    client = mutant.__dict__ and DecisionClient(
        config, transport_factory=lambda: httpx.MockTransport(lambda r: httpx.Response(200))
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    payloads: list[dict] = []
    overlay = mutant.DecisionOverlay(
        registry=registry,
        kill=_TripExplodingKill(),
        shadow_writer=ShadowWriter(sink=RowCapture()),
        config=config,
        configured=True,
        notify_sink=payloads.append,
    )
    with caplog.at_level(logging.DEBUG):
        await overlay._trip(
            reason="global_ceiling",
            seam_ids=("snapshot.l3.selection",),
            global_off=True,
            month_to_date_usd=99.0,
            ceiling_usd=50.0,
        )
    assert SECRET in caplog.text, (
        "the mutant must LEAK into the log — otherwise the pin is green "
        "on the defect it exists to catch"
    )
    assert SECRET in json.dumps(payloads, default=str), (
        "the mutant must LEAK into the notify payload"
    )


# ═══════════════════════════════════════════════════════════════════════
# D-9 — the token ceiling binds on a tokens-without-price payload
# ═══════════════════════════════════════════════════════════════════════


def _usage_handler(usage: dict[str, Any]):
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        question = body["questions"][0]
        choices = question.get("choices") or []
        payload = {
            "model": "decision",
            "usage": usage,
            "answers": [
                {
                    "type": question["type"],
                    "name": question["name"],
                    "choice": choices[0].get("value", "") if choices else "",
                    "probabilities": [
                        {"value": c.get("value"), "probability": 0.5} for c in choices
                    ],
                    "confidence": 0.9,
                }
            ],
        }
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    return _handler


def _overlay_over(
    client: DecisionClient,
    accumulator: Any,
    token_ceiling: int,
    cost_floor_per_attempt_usd: float = 0.0005,
):
    config = make_config(
        token_ceiling=token_ceiling,
        cost_ceiling_usd=50.0,
        pricing_usd_per_million_tokens={},
        cost_floor_per_attempt_usd=cost_floor_per_attempt_usd,
    )
    registry = SeamRegistry(client=client, config=config)
    registry.register(seam_record())
    return DecisionOverlay(
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
        accumulator=accumulator,
        configured=True,
    )


@pytest.mark.asyncio
async def test_a_tokens_without_price_payload_accrues_and_trips_the_token_ceiling() -> None:
    """D-9 — L8: usage tokens with NO price must accrue to the TOKEN
    meter, and the TOKEN CEILING must trip at its configured bound.

    The payload carries the STANDARD usage shape
    (``prompt_tokens``/``completion_tokens``) and no price — the shape
    the old meter ignored (it read only ``total_tokens``), so the
    tokens metered ZERO, the token ceiling could never bind, and the
    USD ceiling was bypassable.
    """
    from tests.unit.decision.test_council_blockers import _sqlite_engine

    engine = _sqlite_engine()
    accumulator = AppSettingCostAccumulator(engine, period_getter=lambda: "2099-05")
    client = DecisionClient(
        make_config(token_ceiling=1000, cost_ceiling_usd=50.0),
        transport_factory=lambda: httpx.MockTransport(
            _usage_handler({"prompt_tokens": 900, "completion_tokens": 100})
        ),
    )
    overlay = _overlay_over(client, accumulator, token_ceiling=1000)

    for _ in range(2):
        await overlay.consult(
            "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
        )

    totals = await accumulator.current_totals("snapshot.l3.selection")
    assert totals.month_to_date_tokens == 1000, (
        "the usage tokens must accrue to the TOKEN meter "
        f"(got {totals.month_to_date_tokens})"
    )
    assert totals.month_to_date_usd == 0.0, (
        "no price ⇒ no USD accrued (the cost is NEVER invented)"
    )
    assert overlay.trips, (
        "the TOKEN CEILING must trip at its configured bound — a "
        "tokens-without-price payload may not bypass the control"
    )
    assert overlay.trips[-1].reason == "global_ceiling"


@pytest.mark.asyncio
async def test_the_token_ceiling_pin_is_discriminating() -> None:
    """D-9 — the counter-mutation proof: the mutant that drops the
    prompt/completion fallback (the pre-round-5 meter) is planted IN
    MEMORY and the tokens meter ZERO with NO trip — so the pin above is
    watching a live defect."""
    from tests.unit.decision.test_council_blockers import _sqlite_engine

    mutant = load_mutant_module(
        "daemon/services/decision/client.py",
        [
            (
                "                for left, right in (\n"
                '                    ("prompt_tokens", "completion_tokens"),\n'
                '                    ("input_tokens", "output_tokens"),\n'
                "                ):",
                "                for left, right in ():",
            )
        ],
        "daemon.services.decision._mutant_client_round5",
    )
    engine = _sqlite_engine()
    accumulator = AppSettingCostAccumulator(engine, period_getter=lambda: "2099-06")
    client = mutant.DecisionClient(
        make_config(token_ceiling=1000, cost_ceiling_usd=50.0),
        transport_factory=lambda: httpx.MockTransport(
            _usage_handler({"prompt_tokens": 900, "completion_tokens": 100})
        ),
    )
    overlay = _overlay_over(client, accumulator, token_ceiling=1000)
    for _ in range(2):
        await overlay.consult(
            "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
        )
    totals = await accumulator.current_totals("snapshot.l3.selection")
    assert totals.month_to_date_tokens == 0, (
        "the mutant must meter ZERO tokens — that is the bypass"
    )
    assert not overlay.trips, (
        "the mutant must NOT trip — the token ceiling cannot bind"
    )


# ═══════════════════════════════════════════════════════════════════════
# D-10 — the (e) floor discriminator, at the STORE level
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_connect_phase_fault_bills_nothing_at_the_ledger() -> None:
    """D-10 — a connect-phase fault (never opened a socket) must NOT
    bill: asserted at the STORE level (the durable ledger's
    month-to-date USD delta), not at a metric that never moves.

    The round-4 pin asserted a metric delta after a bare CLIENT
    consult — the overlay never accounted at all, so the assertion was
    vacuous (it passed identically with and without the floor).
    """
    from tests.unit.decision.test_council_blockers import _sqlite_engine

    engine = _sqlite_engine()
    accumulator = AppSettingCostAccumulator(engine, period_getter=lambda: "2099-07")

    def _connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = DecisionClient(
        make_config(cost_floor_per_attempt_usd=0.5),
        transport_factory=lambda: httpx.MockTransport(_connect_error),
    )
    overlay = _overlay_over(
        client, accumulator, token_ceiling=10_000_000, cost_floor_per_attempt_usd=0.5
    )
    before = await accumulator.current_totals("snapshot.l3.selection")

    result = await overlay.consult(
        "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
    )
    after = await accumulator.current_totals("snapshot.l3.selection")

    assert result.failure_class == "connect_error"
    assert after.month_to_date_usd - before.month_to_date_usd == 0.0, (
        "a connect-phase fault never opened a socket — it must NOT bill "
        "the D3 floor (the ledger delta must be exactly 0.0)"
    )


@pytest.mark.asyncio
async def test_the_connect_phase_floor_pin_is_discriminating() -> None:
    """D-10 — the counter-mutation proof: the mutant that marks a
    connect-phase fault as socket-reached (the pre-round-5 shape) is
    planted IN MEMORY and the LEDGER DOES bill the floor — so the pin
    above is watching a live defect."""
    from tests.unit.decision.test_council_blockers import _sqlite_engine

    mutant = load_mutant_module(
        "daemon/services/decision/client.py",
        [
            (
                "        except httpx.ConnectError:\n"
                "            # (e) — connection refused / DNS failure / unreachable host:\n"
                "            # the connection was never established, so no socket was\n"
                "            # opened and the attempt is NOT D3-floor-eligible. Listed\n"
                "            # BEFORE the broad HTTPError.\n"
                "            return self._failure(\n"
                "                FailureClass.CONNECT_ERROR,\n"
                "                latency_s=self._clock() - started,\n"
                "                reached_socket=False,\n"
                "            )",
                "        except httpx.ConnectError:\n"
                "            return self._failure(\n"
                "                FailureClass.CONNECT_ERROR,\n"
                "                latency_s=self._clock() - started,\n"
                "                reached_socket=True,\n"
                "            )",
            )
        ],
        "daemon.services.decision._mutant_client_round5_e",
    )
    engine = _sqlite_engine()
    accumulator = AppSettingCostAccumulator(engine, period_getter=lambda: "2099-08")

    def _connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = mutant.DecisionClient(
        make_config(cost_floor_per_attempt_usd=0.5),
        transport_factory=lambda: httpx.MockTransport(_connect_error),
    )
    overlay = _overlay_over(
        client, accumulator, token_ceiling=10_000_000, cost_floor_per_attempt_usd=0.5
    )
    before = await accumulator.current_totals("snapshot.l3.selection")
    await overlay.consult(
        "snapshot.l3.selection", heuristic_outcome=["id-a"], context={"input": "x"}
    )
    after = await accumulator.current_totals("snapshot.l3.selection")
    assert after.month_to_date_usd - before.month_to_date_usd == 0.5, (
        "the mutant must BILL the floor — that is what the pin forbids"
    )


# ═══════════════════════════════════════════════════════════════════════
# D-4/D-5 — the mutation matrix must be RE-DERIVABLE (and must stay so)
# ═══════════════════════════════════════════════════════════════════════


def _parse_matrix_legs(text: str) -> list[dict[str, str]]:
    """Parse the published legs out of MUTATION_MATRIX.md."""
    legs: list[dict[str, str]] = []
    blocks = text.split("### ")[1:]
    for block in blocks:
        header = block.splitlines()[0]
        if "—" not in header:
            continue
        lid = header.split("—")[0].strip()
        if not lid or not lid[0].isalpha():
            continue
        fences = [
            part.strip("\n")
            for part in block.split("```")[1::2]
        ]
        if len(fences) < 2:
            continue
        digest = ""
        for line in block.splitlines():
            if "**digest**" in line:
                digest = line.split("`")[1]
                break
        path = header.split("`")[1] if "`" in header else ""
        legs.append(
            {
                "id": lid,
                "path": path,
                "shipped": fences[0],
                "mutated": fences[1],
                "digest": digest,
            }
        )
    return legs


def _matrix_problems(text: str, repo_root: Path) -> list[str]:
    """Every way the matrix fails to re-derive (the shared checker)."""
    import hashlib

    legs = _parse_matrix_legs(text)
    problems: list[str] = []
    if len(legs) < 24:
        problems.append(f"the matrix must publish its legs (found {len(legs)})")
    for leg in legs:
        if not leg["path"]:
            problems.append(f"{leg['id']}: no path published")
            continue
        digest = hashlib.sha256(
            f"{leg['path']}\n{leg['shipped']}\n{leg['mutated']}".encode("utf-8")
        ).hexdigest()[:16]
        if digest != leg["digest"]:
            problems.append(
                f"{leg['id']}: the digest does not re-derive "
                f"(published {leg['digest']}, recomputed {digest})"
            )
        source_path = repo_root / leg["path"]
        if not source_path.exists():
            problems.append(f"{leg['id']}: the published path does not exist")
            continue
        if leg["shipped"] not in source_path.read_text():
            problems.append(
                f"{leg['id']}: the published operand is NOT byte-exact in "
                f"{leg['path']}"
            )
    return problems


def test_the_mutation_matrix_digests_are_re_derivable() -> None:
    """D-4/D-5 — every published digest is RECOMPUTED from the published
    operands, and every published operand is byte-exact in the tree.

    The previous revision's digests were 0/15 re-derivable (~1.8M
    reconstructions attempted) — an unverifiable provenance claim, the
    same class as a pin that cannot fail. This pin makes the artefact
    SELF-VERIFYING: it cannot drift again without going RED.
    """
    matrix_path = _REPO_ROOT / "daemon/services/decision/MUTATION_MATRIX.md"
    problems = _matrix_problems(matrix_path.read_text(), _REPO_ROOT)
    assert problems == [], (
        "the mutation matrix must be re-derivable — an unverifiable "
        f"provenance claim is the same class as a pin that cannot fail: {problems}"
    )


def test_the_mutation_matrix_pin_is_discriminating() -> None:
    """D-4/D-5 — the counter-mutation proof: a doctored matrix (a digest
    that does not match its operands, and an operand that is not
    byte-exact in the tree) is rejected IN MEMORY — so the artefact
    cannot drift again without going RED."""
    matrix_path = _REPO_ROOT / "daemon/services/decision/MUTATION_MATRIX.md"
    text = matrix_path.read_text()

    # Doctor the FIRST published digest — the operands stay, the digest
    # moves. The checker must notice.
    legs = _parse_matrix_legs(text)
    assert legs, "the matrix must publish its legs"
    first = legs[0]
    doctored = text.replace(f"* **digest** `{first['digest']}`",
                            "* **digest** `0000000000000000`", 1)
    assert doctored != text, "the digest anchor moved — the pin must be updated"
    problems = _matrix_problems(doctored, _REPO_ROOT)
    assert problems, "the checker must reject a digest that does not re-derive"
    assert "does not re-derive" in problems[0]

    # Doctor an OPERAND — a shipped text that is not in the tree.
    doctored_2 = text.replace(first["shipped"], "NOT-IN-THE-TREE", 1)
    problems_2 = _matrix_problems(doctored_2, _REPO_ROOT)
    assert problems_2, "the checker must reject an operand that is not byte-exact"
