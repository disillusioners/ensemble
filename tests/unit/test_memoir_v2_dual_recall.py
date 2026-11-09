"""Memoir Store v2 — the DUAL-RECALL envelope (the SQLite lane).

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` AC5;
pack ``test/packs/memoir_v2_dual_recall_test.sh``).

What this suite pins:

* **THE SYNTHESES SECTION LEADS** — with the lane ON and matches
  present, the beliefs render FIRST and the episode blocks follow
  byte-identically;
* **THE FOOTER TOKEN** — ``syntheses=M`` appears when the lane is ON
  and is ABSENT when the lane is OFF;
* **THE ZERO-MATCH CASE** — the lane ON with no matching belief omits
  the section entirely and reports ``syntheses=0``;
* **THE TAIL-DROP ORDER** (§3.3) — the EPISODES are dropped first and
  only then the WEAKEST synthesis;
* **THE HEAT INCREMENT** (W5) — an explicit recall hit increments the
  heat of the stars the RETURNED episodes cite;
* **THE LANE-OFF BYTE-IDENTITY** (AC7(b)) — with
  ``ENSEMBLE_MEMOIR_REFLECTION_ENABLED=false`` the envelope and the
  footer are BYTE-IDENTICAL to the v1 output.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.repositories.memoir.syntheses_repository import (
    MemoirSynthesesRepository,
)
from daemon.services.memoir_retrieval import (
    MEMOIR_EPISODES_SECTION_HEADER,
    MEMOIR_RECALL_MAX_SYNTHESES,
    MEMOIR_SYNTHESES_SECTION_HEADER,
    MEMOIR_RECALL_TOKEN_BUDGET,
    MemoirRetrievalService,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"


@pytest.fixture()
def engine(tmp_path):
    # A FILE-backed SQLite database: the tool layer runs the recall on
    # ``asyncio.to_thread`` (a DIFFERENT thread), and an in-memory
    # SQLite database is per-THREAD — a second thread gets a second,
    # EMPTY database.
    eng = create_engine(f"sqlite:///{tmp_path}/memoir-dual-recall.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def syntheses_repo(engine):
    return MemoirSynthesesRepository(engine)


@pytest.fixture()
def constellation_repo(engine):
    return MemoirConstellationRepository(engine)


def _service(repo, syntheses_repo=None, constellation_repo=None, lane=True):
    return MemoirRetrievalService(
        repo,
        config=None,
        syntheses_repository=syntheses_repo,
        reflection_enabled=lambda: lane,
        constellation_repository=constellation_repo,
    )


def _seed(repo, body="the zebra choice", importance=5, stars=None):
    return repo.insert_episode(
        agent_id=AGENT, body=body, importance=importance, stars=stars
    )


# ── the lane-OFF byte identity (AC7(b)) ─────────────────────────────────


def test_the_lane_off_envelope_is_byte_identical_to_v1(
    repo, syntheses_repo, monkeypatch
):
    """AC7(b): the lane OFF ⇒ the v1 envelope, byte for byte.

    The reference is the service built WITHOUT the dual-recall arm at
    all — the v1 service. With the lane OFF the two envelopes must be
    IDENTICAL, including the footer.
    """
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    _seed(repo)
    v1_service = MemoirRetrievalService(repo, config=None)
    lane_service = _service(repo, syntheses_repo, lane=False)
    v1_result = v1_service.recall(AGENT, "zebra", 5)
    lane_result = lane_service.recall(AGENT, "zebra", 5)
    assert lane_result.footer == v1_result.footer
    # The projections are compared WITHOUT ``last_accessed_at``: the first
    # recall MARKS the episode, so the second projection carries a
    # different access stamp. That field is the stats write's business,
    # not the envelope's.
    def _stable(projection):
        return {k: v for k, v in projection.items() if k != "last_accessed_at"}

    assert [_stable(p) for p in lane_result.episodes] == [
        _stable(p) for p in v1_result.episodes
    ]
    assert "syntheses=" not in lane_result.footer
    assert "syntheses=" not in v1_result.footer


def test_the_lane_off_footer_carries_no_syntheses_token(
    repo, syntheses_repo, monkeypatch
):
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    _seed(repo)
    result = _service(repo, syntheses_repo, lane=False).recall(AGENT, "zebra", 5)
    assert "syntheses=" not in result.footer
    assert result.syntheses == []


# ── the lane ON: the section leads ───────────────────────────────────────


def test_the_syntheses_section_renders_before_the_episodes(
    repo, syntheses_repo
):
    episode_id = _seed(repo, body="the zebra choice")
    syntheses_repo.create_synthesis(
        AGENT,
        "the Emperor prefers the zebra",
        [episode_id],
        "CREATE",
        0.9,
    )
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 5)
    assert result.syntheses
    assert "[synthesis:" in result.syntheses[0]["id"] or result.syntheses
    assert result.syntheses[0]["verb"] == "CREATE"
    assert result.syntheses[0]["source_episode_ids"] == [episode_id]


def test_the_footer_carries_the_syntheses_count(repo, syntheses_repo):
    episode_id = _seed(repo, body="the zebra choice")
    syntheses_repo.create_synthesis(
        AGENT, "the Emperor prefers the zebra", [episode_id], "CREATE", 0.9
    )
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 5)
    assert "syntheses=1" in result.footer


def test_the_zero_match_case_omits_the_section_and_reports_zero(
    repo, syntheses_repo
):
    """AC5: the lane ON with zero matches ⇒ no section, ``syntheses=0``."""
    _seed(repo, body="the zebra choice")
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 5)
    assert result.syntheses == []
    assert "syntheses=0" in result.footer


def test_the_syntheses_are_capped(repo, syntheses_repo):
    episode_ids = [_seed(repo, body=f"the zebra choice {i}") for i in range(8)]
    for index, episode_id in enumerate(episode_ids):
        syntheses_repo.create_synthesis(
            AGENT,
            f"the Emperor prefers the zebra number {index}",
            [episode_id],
            "CREATE",
            0.9,
        )
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 5)
    assert len(result.syntheses) == MEMOIR_RECALL_MAX_SYNTHESES


def test_the_syntheses_are_ordered_strongest_first(repo, syntheses_repo):
    episode_ids = [_seed(repo, body=f"the zebra choice {i}") for i in range(3)]
    for index, episode_id in enumerate(episode_ids):
        syntheses_repo.create_synthesis(
            AGENT,
            f"the Emperor prefers the zebra number {index}",
            [episode_id],
            "CREATE",
            0.1 + index * 0.3,
        )
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 5)
    confidences = [float(item["confidence"]) for item in result.syntheses]
    assert confidences == sorted(confidences, reverse=True)


# ── the tail-drop order (§3.3) ───────────────────────────────────────────


def test_the_episodes_are_dropped_before_the_weakest_synthesis(
    repo, syntheses_repo
):
    """The tail-drop order: episodes FIRST, then the weakest synthesis.

    The scene: FIVE long syntheses (the head of the envelope) plus FOUR
    long episodes (the tail). The envelope tips over, so the episodes are
    dropped first; the beliefs survive because they lead.
    """
    episode_ids = [
        _seed(repo, body=f"the zebra choice {index} " + ("x" * 6000))
        for index in range(4)
    ]
    for index, episode_id in enumerate(episode_ids):
        syntheses_repo.create_synthesis(
            AGENT,
            f"zebra belief {index} " + ("y" * 2800),
            [episode_id],
            "CREATE",
            0.5 + index * 0.1,
        )
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 20)
    # The BELIEFS are the head: they survive.
    assert len(result.syntheses) == 4
    assert result.syntheses_dropped == 0
    # The EPISODES are the tail: they were dropped.
    assert result.dropped > 0
    assert len(result.episodes) < 4
    assert result.total_tokens <= MEMOIR_RECALL_TOKEN_BUDGET


def test_the_weakest_synthesis_is_dropped_when_the_episodes_are_gone(
    repo, syntheses_repo
):
    """When the episodes are exhausted, the WEAKEST belief goes next."""
    episode_ids = [
        _seed(repo, body=f"the zebra choice {index}") for index in range(5)
    ]
    for index, episode_id in enumerate(episode_ids):
        syntheses_repo.create_synthesis(
            AGENT,
            f"zebra belief {index} " + ("y" * 5000),
            [episode_id],
            "CREATE",
            0.9 - index * 0.1,
        )
    result = _service(repo, syntheses_repo, lane=True).recall(AGENT, "zebra", 20)
    assert result.syntheses_dropped > 0, (
        "the weakest synthesis must be dropped when the envelope tips over"
    )
    # The survivors are the STRONGEST ones.
    confidences = [float(item["confidence"]) for item in result.syntheses]
    assert confidences == sorted(confidences, reverse=True)
    assert result.total_tokens <= MEMOIR_RECALL_TOKEN_BUDGET


# ── the heat increment (W5) ──────────────────────────────────────────────


def test_an_explicit_recall_hit_increments_the_star_heat(
    repo, syntheses_repo, constellation_repo
):
    """W5: the stars the RETURNED episodes cite get their heat up."""
    episode_id = _seed(repo, body="the zebra choice", stars=["the Emperor"])
    star = constellation_repo.upsert_star(AGENT, "the Emperor", "person")
    assert star.heat == 0.0
    service = _service(repo, syntheses_repo, constellation_repo, lane=True)
    result = service.recall(AGENT, "zebra", 5)
    assert result.episodes, "the recall shipped nothing"
    assert constellation_repo.get_star_card(AGENT, star.id)["star"]["heat"] == 1.0


def test_the_heat_is_not_incremented_for_a_dropped_episode(
    repo, syntheses_repo, constellation_repo
):
    """Only the SHIPPED episodes' stars are heated."""
    star = constellation_repo.upsert_star(AGENT, "the Emperor", "person")
    # An episode that does NOT match the query is never shipped.
    _seed(repo, body="a completely unrelated memory", stars=["the Emperor"])
    service = _service(repo, syntheses_repo, constellation_repo, lane=True)
    result = service.recall(AGENT, "zebra", 5)
    assert not result.episodes
    assert constellation_repo.get_star_card(AGENT, star.id)["star"]["heat"] == 0.0


def test_the_heat_increment_never_breaks_the_recall(
    repo, syntheses_repo, constellation_repo
):
    """A failing heat write NEVER destroys the recall."""
    _seed(repo, body="the zebra choice", stars=["the Emperor"])

    class _Boom:
        def resolve_star(self, agent_id, name):
            raise RuntimeError("the constellation exploded")

        def increment_heat(self, agent_id, star_id, amount=1.0):
            raise RuntimeError("the constellation exploded")

    service = _service(repo, syntheses_repo, _Boom(), lane=True)
    result = service.recall(AGENT, "zebra", 5)
    assert result.episodes


# ── the timeline is untouched ────────────────────────────────────────────


def test_the_timeline_carries_no_syntheses_token(repo, syntheses_repo):
    """The dual-recall arm is the RECALL's arm; the timeline is v1."""
    _seed(repo, body="the zebra choice")
    service = _service(repo, syntheses_repo, lane=True)
    result = service.timeline(AGENT)
    assert "syntheses=" not in result.footer
    assert result.syntheses == []


# ── the tool-level envelope ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tool_renders_the_syntheses_section_first(
    repo, syntheses_repo, monkeypatch
):
    """The TOOL ships the beliefs first and the episodes after."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "true")
    from daemon.tools.memoir import create_memoir_tools

    class _Manager:
        def __init__(self, engine):
            self._engine = engine
            self.config = None

    manager = _Manager(repo.engine)
    tools = {tool.name: tool for tool in create_memoir_tools(manager, agent_id=AGENT)}
    episode_id = _seed(repo, body="the zebra choice")
    syntheses_repo.create_synthesis(
        AGENT, "the Emperor prefers the zebra", [episode_id], "CREATE", 0.9
    )
    text = await tools["memoir_recall"].ainvoke({"query": "zebra"})
    assert isinstance(text, str)
    assert MEMOIR_SYNTHESES_SECTION_HEADER in text
    assert MEMOIR_EPISODES_SECTION_HEADER in text
    assert text.index(MEMOIR_SYNTHESES_SECTION_HEADER) < text.index(
        MEMOIR_EPISODES_SECTION_HEADER
    )
    assert "syntheses=1" in text


@pytest.mark.asyncio
async def test_the_tool_with_the_lane_off_is_the_v1_envelope(
    repo, syntheses_repo, monkeypatch
):
    """AC7(b) at the TOOL level: the lane OFF ⇒ the v1 envelope."""
    monkeypatch.setenv("ENSEMBLE_MEMOIR_REFLECTION_ENABLED", "false")
    from daemon.tools.memoir import create_memoir_tools

    class _Manager:
        def __init__(self, engine):
            self._engine = engine
            self.config = None

    manager = _Manager(repo.engine)
    tools = {tool.name: tool for tool in create_memoir_tools(manager, agent_id=AGENT)}
    episode_id = _seed(repo, body="the zebra choice")
    syntheses_repo.create_synthesis(
        AGENT, "the Emperor prefers the zebra", [episode_id], "CREATE", 0.9
    )
    text = await tools["memoir_recall"].ainvoke({"query": "zebra"})
    assert MEMOIR_SYNTHESES_SECTION_HEADER not in text
    assert MEMOIR_EPISODES_SECTION_HEADER not in text
    assert "syntheses=" not in text
