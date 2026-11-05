"""The memoir pin admission + the write bounds.

Phase 2 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase2-plan.md`` AC5 (+ the
AC1 mutation legs); pack ``test/packs/memoir_admission_unit_test.sh``).

What this suite pins:

* **AC5** — twelve small pins are accepted; the THIRTEENTH is refused by
  the COUNT cap; an OVERSIZED pin is refused by the TOKEN zone; an unpin
  frees the budget so a re-pin works; and the keepsake-at-WRITE path
  reuses the SAME admission (and a refused keepsake write leaves NO row
  behind — the admission reads PRECEDE the single INSERT);
* the refusal NAMES the binding constraint and the current pins;
* nothing is ever evicted (DD-3 — reject-don't-evict);
* **D2.13** — the write bounds are hygiene, not normalization: they
  refuse, and they change no string's content.
"""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import (
    KEEPSAKE_ADMISSION_PCLASS,
    MemoirEpisode,
    render_keepsake_line,
)
from daemon.repositories.memoir.repository import (
    MEMOIR_BODY_CHAR_CAP,
    MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET,
    MEMOIR_PIN_COUNT_CAP,
    MemoirAdmissionError,
    MemoirRepository,
    MemoirWriteError,
)


@pytest.fixture()
def engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


def _episode(repo, body: str = "a small moment worth keeping", **kwargs) -> str:
    return repo.insert_episode(agent_id="ari", body=body, **kwargs)


# ── AC5 — the count cap ───────────────────────────────────────────────────


def test_twelve_small_pins_are_accepted(repo):
    """The count cap is 12: twelve small pins fit."""
    ids = [_episode(repo) for _ in range(MEMOIR_PIN_COUNT_CAP)]
    for episode_id in ids:
        assert repo.set_pin(episode_id, actor="empress", reason="worth keeping")
    assert repo.count_pins("ari") == MEMOIR_PIN_COUNT_CAP
    assert MEMOIR_PIN_COUNT_CAP == 12


def test_the_thirteenth_pin_is_refused_by_the_count(repo):
    """The refusal names the binding constraint and the current pins."""
    ids = [_episode(repo) for _ in range(MEMOIR_PIN_COUNT_CAP)]
    for episode_id in ids:
        repo.set_pin(episode_id, actor="empress", reason="worth keeping")
    thirteenth = _episode(repo)

    with pytest.raises(MemoirAdmissionError) as excinfo:
        repo.set_pin(thirteenth, actor="emperor", reason="one too many")

    error = excinfo.value
    assert error.constraint == "pin_count"
    assert "12" in error.message
    assert len(error.pins) == MEMOIR_PIN_COUNT_CAP
    for pin in error.pins:
        assert set(pin) == {"id", "title", "pinned_by", "tokens"}
        assert pin["pinned_by"] == "empress"
        assert isinstance(pin["tokens"], int)


def test_a_refused_pin_does_not_evict_an_older_keepsake(repo):
    """DD-3: a keepsake that can be silently evicted is not a keepsake."""
    ids = [_episode(repo) for _ in range(MEMOIR_PIN_COUNT_CAP)]
    for episode_id in ids:
        repo.set_pin(episode_id, actor="empress", reason="worth keeping")
    thirteenth = _episode(repo)

    with pytest.raises(MemoirAdmissionError):
        repo.set_pin(thirteenth, actor="emperor", reason="one too many")

    assert repo.count_pins("ari") == MEMOIR_PIN_COUNT_CAP
    with Session(repo.engine) as session:
        still = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == ids[0])
        ).first()
        assert still.is_keepsake is True
        assert still.pinned_by == "empress"


def test_unpin_frees_the_budget_so_a_re_pin_works(repo):
    ids = [_episode(repo) for _ in range(MEMOIR_PIN_COUNT_CAP)]
    for episode_id in ids:
        repo.set_pin(episode_id, actor="empress", reason="worth keeping")
    thirteenth = _episode(repo)
    with pytest.raises(MemoirAdmissionError):
        repo.set_pin(thirteenth, actor="emperor", reason="one too many")

    assert repo.clear_pin(ids[0]) is True
    assert repo.count_pins("ari") == MEMOIR_PIN_COUNT_CAP - 1
    assert repo.set_pin(thirteenth, actor="emperor", reason="now there is room")
    assert repo.count_pins("ari") == MEMOIR_PIN_COUNT_CAP


def test_clear_pin_nulls_the_whole_pin_column_set(repo):
    """N-1: no stale attribution survives an unpin."""
    episode = _episode(repo)
    repo.set_pin(episode, actor="empress", reason="worth keeping")
    assert repo.clear_pin(episode) is True
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.is_keepsake is False
        assert row.pinned_by is None
        assert row.pinned_reason is None
        assert row.pinned_at is None


# ── AC5 — the token zone ──────────────────────────────────────────────────


def test_an_oversized_pin_is_refused_by_the_zone_with_names(repo):
    """W-1 (council): the zone measures the RENDERED keepsake lines, so
    an over-long ``pinned_reason`` — uncapped by ``_validate_pin_claim``
    — blows the 4,000-token zone and is REJECTED at admission
    (reject-don't-evict: the cap working, not a clamp). The refusal
    names the binding constraint and the held pins with their rendered
    sizes. This test is the W-1 regression pin: under the old
    content-only arithmetic a long reason measured as NOTHING and the
    zone never refused."""
    held_reason = "word " * 700
    first = _episode(repo)
    repo.set_pin(first, actor="empress", reason=held_reason)
    second = _episode(repo)

    with pytest.raises(MemoirAdmissionError) as excinfo:
        repo.set_pin(second, actor="emperor", reason="word " * 4000)

    error = excinfo.value
    assert error.constraint == "keepsake_zone_tokens"
    assert MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET == 4000
    assert len(error.pins) == 1
    assert error.pins[0]["id"] == first
    # W-1 arithmetic: the held pin is measured as its RENDERED line — a
    # 3,500-character reason alone renders to hundreds of tokens.
    assert error.pins[0]["tokens"] > 600


def test_the_zone_counts_the_existing_keepsakes(repo):
    """``keepsake_zone_tokens`` is the sum of the admitted keepsakes."""
    assert repo.keepsake_zone_tokens("ari") == 0
    episode = _episode(repo, body="word " * 100)
    repo.set_pin(episode, actor="empress", reason="worth keeping")
    assert repo.keepsake_zone_tokens("ari") > 0


def test_a_re_pin_of_an_existing_keepsake_does_not_double_count(repo):
    """Re-pinning replaces; it does not add a second pin. W-1: the zone
    is the RENDERED line of the ONE surviving pin — never the sum of
    two pins' lines."""
    episode = _episode(repo, body="word " * 100)
    repo.set_pin(episode, actor="empress", reason="worth keeping")
    before = repo.keepsake_zone_tokens("ari")
    assert before > 0
    assert repo.set_pin(episode, actor="emperor", reason="re-pinned")
    assert repo.count_pins("ari") == 1
    from daemon.loader import estimate_tokens

    expected_after = estimate_tokens(
        render_keepsake_line(
            title=None,
            pinned_by="emperor",
            pclass=KEEPSAKE_ADMISSION_PCLASS,
            pinned_reason="re-pinned",
        )
    )
    assert repo.keepsake_zone_tokens("ari") == expected_after
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.pinned_by == "emperor"


# ── AC5 — the keepsake-at-WRITE path reuses the SAME admission ───────────


def test_a_keepsake_at_write_reuses_the_same_admission(repo):
    """T2.5: the write path calls the SAME admission as the pin path."""
    for _ in range(MEMOIR_PIN_COUNT_CAP):
        repo.insert_episode(
            agent_id="ari",
            body="a small moment worth keeping",
            is_keepsake=True,
            pinned_by="empress",
            pinned_reason="worth keeping",
        )
    assert repo.count_pins("ari") == MEMOIR_PIN_COUNT_CAP

    with pytest.raises(MemoirAdmissionError) as excinfo:
        repo.insert_episode(
            agent_id="ari",
            body="one too many",
            is_keepsake=True,
            pinned_by="emperor",
            pinned_reason="one too many",
        )
    assert excinfo.value.constraint == "pin_count"


def test_a_refused_keepsake_write_leaves_NO_row_behind(repo):
    """D2.15 / W3: the admission reads PRECEDE the single INSERT.

    If the admission ran AFTER the insert, the refused episode would
    EXIST — the zone would be blown and the agent would be told the write
    failed when it did not.
    """
    for _ in range(MEMOIR_PIN_COUNT_CAP):
        repo.insert_episode(
            agent_id="ari",
            body="a small moment worth keeping",
            is_keepsake=True,
            pinned_by="empress",
            pinned_reason="worth keeping",
        )
    with pytest.raises(MemoirAdmissionError):
        repo.insert_episode(
            agent_id="ari",
            body="one too many",
            is_keepsake=True,
            pinned_by="emperor",
            pinned_reason="one too many",
        )
    with Session(repo.engine) as session:
        rows = session.exec(select(MemoirEpisode)).all()
        assert len(rows) == MEMOIR_PIN_COUNT_CAP
        assert all(row.is_keepsake for row in rows)


def test_a_keepsake_at_write_sets_the_pin_columns_in_the_same_row(repo):
    """D2.15: the pin columns are part of the SAME row — no second write."""
    episode = repo.insert_episode(
        agent_id="ari",
        body="the first day is preserved",
        is_keepsake=True,
        pinned_by="empress",
        pinned_reason="the first day",
    )
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.is_keepsake is True
        assert row.pinned_by == "empress"
        assert row.pinned_reason == "the first day"
        assert row.pinned_at is not None
    assert repo.count_pins("ari") == 1


# ── D2.13 — the write bounds ──────────────────────────────────────────────


def test_an_oversized_body_is_refused(repo):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.insert_episode(agent_id="ari", body="x" * (MEMOIR_BODY_CHAR_CAP + 1))
    assert excinfo.value.code == "body_too_long"


def test_an_oversized_title_is_refused(repo):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.insert_episode(agent_id="ari", body="fine", title="t" * 201)
    assert excinfo.value.code == "title_too_long"


def test_too_many_stars_are_refused(repo):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.insert_episode(agent_id="ari", body="fine", stars=["s"] * 17)
    assert excinfo.value.code == "too_many_stars"


def test_an_oversized_star_is_refused(repo):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.insert_episode(agent_id="ari", body="fine", stars=["s" * 121])
    assert excinfo.value.code == "star_too_long"


def test_the_star_bounds_are_hygiene_not_normalization(repo):
    """The bound is measured POST-TRIM; the stored value is VERBATIM."""
    episode = repo.insert_episode(
        agent_id="ari", body="fine", stars=["  the Emperor  "]
    )
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.stars == ["  the Emperor  "]


@pytest.mark.parametrize("bad_importance", [0, 11, -3, 42])
def test_an_out_of_range_importance_is_refused(repo, bad_importance):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.insert_episode(
            agent_id="ari", body="fine", importance=bad_importance
        )
    assert excinfo.value.code == "importance_out_of_range"


def test_an_unparseable_event_at_is_refused(repo):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.insert_episode(agent_id="ari", body="fine", event_at="not-a-date")
    assert excinfo.value.code == "event_at_invalid"


def test_an_iso_event_at_is_accepted(repo):
    episode = repo.insert_episode(
        agent_id="ari", body="fine", event_at="2026-03-01T10:00:00+00:00"
    )
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.event_at.year == 2026
        assert row.event_at.month == 3


def test_an_invalid_pin_actor_is_refused(repo):
    episode = _episode(repo)
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.set_pin(episode, actor="somebody", reason="worth keeping")
    assert excinfo.value.code == "invalid_actor"


def test_a_pin_without_a_reason_is_refused(repo):
    episode = _episode(repo)
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.set_pin(episode, actor="empress", reason="   ")
    assert excinfo.value.code == "reason_required"


def test_pinning_a_missing_episode_is_refused(repo):
    with pytest.raises(MemoirWriteError) as excinfo:
        repo.set_pin("no-such-episode", actor="empress", reason="worth keeping")
    assert excinfo.value.code == "episode_not_found"
