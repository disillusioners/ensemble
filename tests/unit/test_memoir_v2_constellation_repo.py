"""Memoir Store v2 — the Constellation repository (the SQLite lane).

Phase 1 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md`` AC4;
pack ``test/packs/memoir_v2_constellation_repo_test.sh``).

What this suite pins:

* the canonical star is an IDENTITY: ``(agent_id, name)`` is unique, and
  an upsert returns the SAME row;
* an alias resolves to its canonical star, and a canonical name resolves
  to itself;
* an ALIAS COLLISION raises the unique-constraint refusal — the alias
  table is an identity map, not a suggestion list;
* the ``last_pass_id`` CRASH-RECOVERY GUARD: re-recording a line with
  the SAME pass id leaves ``mention_count`` UNCHANGED, and a DISTINCT
  pass increments it;
* ``update_star_description`` stamps ``description_as_of`` (the 24h
  cooldown watermark) — and ``upsert_star`` never rewrites a description;
* the 1-hop neighbourhood query returns the connected stars, and it
  answers within the 50 ms budget the plan names;
* ``increment_heat`` is ONE statement and never leaves a negative heat
  (the model's ``heat >= 0.0`` CHECK).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, Session, create_engine

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.models import MemoirEpisode
from daemon.services.timestamps import coerce_to_aware_utc

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirConstellationRepository(engine)


def _episode(engine, episode_id: str, agent_id: str = "ari") -> str:
    """Write ONE episode row — the parent of a mention line."""
    with Session(engine) as session:
        session.add(
            MemoirEpisode(
                id=episode_id,
                agent_id=agent_id,
                title="a moment",
                body="the body of the moment",
                created_at=datetime.now(timezone.utc),
                event_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return episode_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── the canonical node ────────────────────────────────────────────────────


def test_an_upsert_creates_the_star(repo):
    star = repo.upsert_star("ari", "the Emperor", "person")
    assert star.name == "the Emperor"
    assert star.star_type == "person"
    assert star.heat == 0.0
    assert star.description == ""
    assert star.first_seen_at is not None
    assert star.last_touched_at is not None


def test_an_upsert_of_the_same_name_returns_the_same_row(repo):
    first = repo.upsert_star("ari", "the Emperor", "person")
    second = repo.upsert_star("ari", "the Emperor", "person")
    assert second.id == first.id


def test_two_agents_may_each_own_a_star_of_the_same_name(repo):
    first = repo.upsert_star("ari", "the Emperor", "person")
    second = repo.upsert_star("zoe", "the Emperor", "person")
    assert first.id != second.id


def test_the_last_touched_at_only_moves_forward(repo):
    old = _now() - timedelta(days=3)
    star = repo.upsert_star("ari", "the Emperor", "person", event_at=old)
    # A LATE-written mention carries an OLDER event time — the timeline
    # must not rewind.
    repo.upsert_star("ari", "the Emperor", "person", event_at=old - timedelta(days=1))
    with Session(repo.engine) as session:
        row = session.get(type(star), star.id)
        assert coerce_to_aware_utc(row.last_touched_at) == coerce_to_aware_utc(
            star.last_touched_at
        )
    # A NEWER event time moves it forward.
    newer = _now()
    repo.upsert_star("ari", "the Emperor", "person", event_at=newer)
    with Session(repo.engine) as session:
        row = session.get(type(star), star.id)
        assert coerce_to_aware_utc(row.last_touched_at) >= coerce_to_aware_utc(newer)


def test_the_upsert_never_rewrites_an_existing_description(repo):
    """``update_star_description`` is the SOLE writer of the description
    family — the upsert must not create a second writer for it."""
    star = repo.upsert_star("ari", "the Emperor", "person", description="a warm figure")
    assert star.description == "a warm figure"
    repo.upsert_star("ari", "the Emperor", "person", description="a different figure")
    with Session(repo.engine) as session:
        row = session.get(type(star), star.id)
        assert row.description == "a warm figure"


# ── the alias ────────────────────────────────────────────────────────────


def test_an_alias_resolves_to_its_canonical_star(repo):
    star = repo.upsert_star("ari", "the Emperor", "person")
    repo.add_alias("ari", star.id, "my dear")
    resolved = repo.resolve_star("ari", "my dear")
    assert resolved is not None
    assert resolved.id == star.id


def test_the_canonical_name_resolves_to_itself(repo):
    star = repo.upsert_star("ari", "the Emperor", "person")
    resolved = repo.resolve_star("ari", "the Emperor")
    assert resolved is not None and resolved.id == star.id


def test_an_unknown_surface_form_resolves_to_nothing(repo):
    assert repo.resolve_star("ari", "nobody at all") is None


def test_an_alias_collision_raises_the_unique_constraint_refusal(repo):
    """The alias table is an IDENTITY map: one alias, one star."""
    first = repo.upsert_star("ari", "the Emperor", "person")
    second = repo.upsert_star("ari", "the Empress", "person")
    repo.add_alias("ari", first.id, "my dear")
    with pytest.raises(IntegrityError):
        repo.add_alias("ari", second.id, "my dear")


def test_an_alias_of_one_agent_does_not_leak_into_another(repo):
    star = repo.upsert_star("ari", "the Emperor", "person")
    repo.add_alias("ari", star.id, "my dear")
    assert repo.resolve_star("zoe", "my dear") is None


# ── the edge and the crash-recovery guard ────────────────────────────────


def test_a_line_is_recorded_with_a_mention_count_of_one(repo, engine):
    star = repo.upsert_star("ari", "the Emperor", "person")
    _episode(engine, "ep1")
    line = repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    assert line.mention_count == 1
    assert line.last_pass_id == "pass-1"
    assert line.star_id == star.id
    assert line.episode_id == "ep1"


def test_re_recording_a_line_with_the_same_pass_leaves_the_count_unchanged(repo, engine):
    """THE CRASH-RECOVERY GUARD — a re-run of the SAME pass must not
    double-count the mention."""
    star = repo.upsert_star("ari", "the Emperor", "person")
    _episode(engine, "ep1")
    first = repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    second = repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    third = repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    assert first.mention_count == 1
    assert second.mention_count == 1
    assert third.mention_count == 1
    assert second.id == first.id


def test_a_distinct_pass_increments_the_mention_count(repo, engine):
    star = repo.upsert_star("ari", "the Emperor", "person")
    _episode(engine, "ep1")
    repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    second = repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-2")
    assert second.mention_count == 2
    assert second.last_pass_id == "pass-2"


def test_a_new_line_is_a_new_row(repo, engine):
    star = repo.upsert_star("ari", "the Emperor", "person")
    _episode(engine, "ep1")
    _episode(engine, "ep2")
    first = repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    second = repo.record_line("ari", star.id, "ep2", _now(), pass_id="pass-1")
    assert first.id != second.id
    assert second.mention_count == 1


# ── the description writer ───────────────────────────────────────────────


def test_updating_the_description_stamps_the_cooldown_watermark(repo):
    star = repo.upsert_star("ari", "the Emperor", "person")
    stamp = _now()
    assert repo.update_star_description("ari", star.id, "a patient figure", stamp) is True
    with Session(repo.engine) as session:
        row = session.get(type(star), star.id)
        assert row.description == "a patient figure"
        assert row.description_as_of is not None
        assert row.description_as_of.replace(tzinfo=timezone.utc) == stamp


def test_updating_the_description_of_an_unknown_star_is_false(repo):
    assert repo.update_star_description("ari", "no-such-star", "x", _now()) is False


# ── the heat accumulator ─────────────────────────────────────────────────


def test_the_heat_accumulator_increments(repo):
    star = repo.upsert_star("ari", "the Emperor", "person")
    assert repo.increment_heat("ari", star.id) == 1.0
    assert repo.increment_heat("ari", star.id, 2.5) == 3.5
    with Session(repo.engine) as session:
        row = session.get(type(star), star.id)
        assert row.heat == 3.5


def test_the_heat_accumulator_never_goes_negative(repo):
    """The model's ``heat >= 0.0`` CHECK must never abort a write."""
    star = repo.upsert_star("ari", "the Emperor", "person")
    repo.increment_heat("ari", star.id, 1.0)
    assert repo.increment_heat("ari", star.id, -5.0) == 0.0


def test_the_heat_accumulator_of_an_unknown_star_raises(repo):
    with pytest.raises(KeyError):
        repo.increment_heat("ari", "no-such-star")


# ── the star card ────────────────────────────────────────────────────────


def test_the_star_card_carries_the_star_its_aliases_and_its_lines(repo, engine):
    star = repo.upsert_star("ari", "the Emperor", "person")
    repo.add_alias("ari", star.id, "my dear")
    _episode(engine, "ep1")
    repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    card = repo.get_star_card("ari", star.id)
    assert card["star"]["id"] == star.id
    assert card["star"]["name"] == "the Emperor"
    assert [alias["alias"] for alias in card["aliases"]] == ["my dear"]
    assert len(card["lines"]) == 1
    assert card["lines"][0]["episode_id"] == "ep1"


def test_the_star_card_of_an_unknown_star_is_empty(repo):
    card = repo.get_star_card("ari", "no-such-star")
    assert card["star"] is None
    assert card["aliases"] == []
    assert card["lines"] == []
    assert card["neighbors"] == []


def test_the_one_hop_query_returns_the_connected_stars(repo, engine):
    star = repo.upsert_star("ari", "the Emperor", "person")
    friend = repo.upsert_star("ari", "the Empress", "person")
    stranger = repo.upsert_star("ari", "the Garden", "place")
    _episode(engine, "ep1")
    repo.record_line("ari", star.id, "ep1", _now(), pass_id="pass-1")
    repo.record_line("ari", friend.id, "ep1", _now(), pass_id="pass-1")
    repo.record_line("ari", stranger.id, "ep2", _now(), pass_id="pass-1")
    _episode(engine, "ep2")
    repo.record_line("ari", stranger.id, "ep2", _now(), pass_id="pass-1")
    card = repo.get_star_card("ari", star.id)
    neighbor_ids = {neighbor["id"] for neighbor in card["neighbors"]}
    assert friend.id in neighbor_ids
    assert stranger.id not in neighbor_ids
    assert star.id not in neighbor_ids


def test_the_one_hop_query_answers_within_the_fifty_millisecond_budget(repo, engine):
    """AC4 — the 1-hop query answers within 50 ms on a populated
    constellation."""
    stars = [repo.upsert_star("ari", f"entity-{i}", "concept") for i in range(60)]
    for index in range(20):
        _episode(engine, f"ep{index}")
    for index, star in enumerate(stars):
        for episode in range(3):
            repo.record_line(
                "ari", star.id, f"ep{episode}", _now(), pass_id=f"pass-{index}-{episode}"
            )
    started = time.perf_counter()
    card = repo.get_star_card("ari", stars[0].id)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 50.0, f"the 1-hop query took {elapsed_ms:.1f} ms"
    assert card["neighbors"], "the 1-hop query returned nothing on a populated constellation"
