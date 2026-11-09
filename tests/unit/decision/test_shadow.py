"""The shadow writer — content-free by construction (AC-13, T-7).

The shadow log is the A/B record (D3). It carries ids, hashes, scores,
counts, latencies and enum labels — and NEVER credentials, request
payloads, raw response bodies or raw user content.

T-7 proves this on the ACTUAL WRITTEN ROW: the content the stage-3
prompt carries (snapshot titles, tags, summaries) must never reach it.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx
import pytest

from daemon.services.decision import metrics
from daemon.services.decision.client import DecisionClient
from daemon.services.decision.kill import (
    GLOBAL_ENABLED_KEY,
    InMemoryKillStore,
    KillChannelResolver,
    seam_enabled_key,
    seam_stage_key,
)
from daemon.services.decision.overlay import DecisionOverlay
from daemon.services.decision.registry import SeamRegistry
from daemon.services.decision.shadow import ShadowRow, ShadowWriter, sanitize_token
from daemon.services.decision.types import (
    DecisionChoice,
    DecisionQuestion,
    DecisionRequest,
    QuestionKind,
)
from tests.unit.decision.helpers import (
    RowCapture,
    json_response,
    make_config,
    smart_handler,
)


#: The content the S1 stage-3 prompt carries today
#: (``snapshot_search_service.py:640-642``): id, title, tags, summary.
SNAPSHOT_TITLE = "Refactored the decision module"
SNAPSHOT_TAGS = "architecture,decision-module"
SNAPSHOT_SUMMARY = "A long prose summary of what the snapshot contains."
SNAPSHOT_ID = "snap-0f3a9c21"


def _seam_with_content():
    """A seam whose question carries the snapshot titles/tags/summary."""

    def _build(context: Mapping[str, Any]) -> DecisionRequest:
        question = DecisionQuestion(
            kind=QuestionKind.CHOICE,
            name="s1_candidate_selection",
            instructions=(
                "Which candidate is most relevant to the query? "
                f"title={SNAPSHOT_TITLE} tags={SNAPSHOT_TAGS} "
                f"summary={SNAPSHOT_SUMMARY}"
            ),
            choices=(
                DecisionChoice(
                    value=SNAPSHOT_ID,
                    description=(
                        f"title={SNAPSHOT_TITLE} tags={SNAPSHOT_TAGS} "
                        f"summary={SNAPSHOT_SUMMARY}"
                    ),
                ),
                DecisionChoice(value="snap-other", description="another candidate"),
            ),
        )
        return DecisionRequest(
            input=f"query about {SNAPSHOT_TITLE}", questions=(question,)
        )

    from daemon.services.decision.types import Monotonicity
    from daemon.services.decision.registry import SeamRecord

    return SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=_build,
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
        question_id="s1_candidate_selection",
    )


async def test_the_written_row_carries_no_prompt_content() -> None:
    """T-7 — the content the stage-3 prompt carries NEVER reaches the row.

    The row is captured as it is written, and asserted against every
    content fragment: the title, the tags, the summary, the query, the
    request payload and the raw response.
    """
    capture = RowCapture()
    writer = ShadowWriter(sink=capture)
    config = make_config()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return smart_handler(request)

    client = DecisionClient(config, transport_factory=lambda: httpx.MockTransport(handler))
    registry = SeamRegistry(client=client, config=config)
    registry.register(_seam_with_content())

    store = InMemoryKillStore(
        {
            GLOBAL_ENABLED_KEY: "1",
            seam_enabled_key("snapshot.l3.selection"): "1",
            seam_stage_key("snapshot.l3.selection"): "shadow",
        }
    )
    kill = KillChannelResolver(store, env={})
    overlay = DecisionOverlay(
        registry=registry, kill=kill, shadow_writer=writer, config=config
    )

    result = await overlay.consult(
        "snapshot.l3.selection",
        heuristic_outcome=[SNAPSHOT_ID],
        context={"input": f"query about {SNAPSHOT_TITLE}"},
    )
    assert result.outcome == [SNAPSHOT_ID]

    assert capture.rows, "the shadow row must have been written"
    row = capture.rows[-1]

    # The content fragments must be ABSENT.
    for fragment in (
        SNAPSHOT_TITLE,
        SNAPSHOT_TAGS,
        SNAPSHOT_SUMMARY,
        "Which candidate is most relevant",
        "title=",
        "summary=",
    ):
        assert fragment not in row, f"the row leaked {fragment!r}: {row}"

    # The raw request payload / response must be ABSENT.
    assert "questions" not in row
    assert "instructions" not in row
    assert "probabilities" not in row
    assert "authorization" not in row.lower()
    assert "Bearer" not in row
    assert config.api_key not in row

    # The allowed content IS there: the seam id, the ids, the mode, the
    # verdict, the latency and the model id.
    assert "event=decision_overlay_shadow" in row
    assert "seam_id=snapshot.l3.selection" in row
    assert SNAPSHOT_ID in row
    assert "mode=shadow" in row
    assert "verdict=" in row
    assert "latency_ms=" in row
    assert "model_id=decision" in row


async def test_a_prose_value_is_hashed_not_written() -> None:
    """A string that is not an opaque token is hashed."""
    assert sanitize_token("snap-0f3a9c21") == "snap-0f3a9c21"
    hashed = sanitize_token(SNAPSHOT_TITLE)
    assert hashed.startswith("sha256:")
    assert SNAPSHOT_TITLE not in hashed


async def test_the_writer_never_fails_a_caller() -> None:
    """AC-13 — a writer failure is counted and swallowed."""
    before = metrics.shadow_write_failures_total.get()

    def _boom(row: str) -> None:
        raise RuntimeError("disk full")

    writer = ShadowWriter(sink=_boom)
    written = writer.write(
        ShadowRow(
            seam_id="snapshot.l3.selection",
            question_id="q1",
            mode="shadow",
        )
    )
    assert written is False
    assert writer.write_failures == 1
    assert metrics.shadow_write_failures_total.get() == before + 1


async def test_the_row_cap_drops_and_counts() -> None:
    """AC-13 — the writer's own row-count cap drops and counts."""
    before = metrics.shadow_rows_dropped_total.get()
    capture = RowCapture()
    writer = ShadowWriter(sink=capture, max_rows_per_seam_per_window=2)
    for _ in range(4):
        writer.write(
            ShadowRow(
                seam_id="snapshot.l3.selection",
                question_id="q1",
                mode="shadow",
            )
        )
    assert len(capture.rows) == 2
    assert writer.rows_dropped == 2
    assert metrics.shadow_rows_dropped_total.get() == before + 2


async def test_the_writer_survives_an_unserialisable_outcome() -> None:
    """The writer degrades — a weird outcome never fails the caller."""
    capture = RowCapture()
    writer = ShadowWriter(sink=capture)
    row = ShadowRow(
        seam_id="snapshot.l3.selection",
        question_id="q1",
        mode="shadow",
        overlay_outcome={"kind": "choice", "ids": ("a", "b")},
        heuristic_outcome={"kind": "list", "count": 2, "items": []},
        verdict="agree",
        latency_ms=12,
    )
    assert writer.write(row) is True
    assert "event=decision_overlay_shadow" in capture.last
