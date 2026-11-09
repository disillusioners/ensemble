"""Memoir Store v2 — the REFLECTION ENGINE (the SQLite lane).

Phase 2 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase2-plan.md`` AC1/AC2;
pack ``test/packs/memoir_v2_reflection_service_test.sh``).

What this suite pins:

* **THE DERIVED TRIGGER** (AC1) — the sum comes from the DATABASE, never
  from an in-memory counter: one episode with importance 10 does NOT
  trigger, episodes totalling 26 DO, and marking ``reflected_at`` drops
  the derived sum back to 0;
* **THE DEBOUNCE DOUBLING** (AC1) — the window starts at 60 s and
  doubles on every burst, capped at the 960 s ceiling;
* **THE FORCE-FIRE** (AC1) — a continuous stream still fires at 3600 s;
* **THE BUDGETS** (AC2) — 5 syntheses per pass, the 1000-character
  REJECT (never a truncation), the 4-call cap counted AT DISPATCH, and
  the tick-budget deferral;
* **THE DEDUP KEY** (AC2 / V2-17) — the same belief over the same
  sources is the same belief;
* **THE CLAIM + STALE TAKEOVER** (AC3 / V2-35) — a claim older than 15
  minutes is re-takeable after a crash;
* **THE CONSTELLATION PASS ID** (the phase-1 review note) — the
  reflection engine ALWAYS passes an explicit ``pass_id``, and a
  ``pass_id=None`` NEVER increments ``mention_count``;
* the ``[MemoirReflection]`` pass log line.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.reflection_repository import (
    MEMOIR_REFLECTION_CLAIM_STALE_SECONDS,
    MemoirReflectionRepository,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.repositories.memoir.syntheses_repository import (
    MemoirSynthesesRepository,
    synthesis_dedup_key,
)
from daemon.services.memoir_reflection_service import (
    MemoirReflectionService,
    ReflectionSettings,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = "ari"


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def reflection_repo(engine):
    return MemoirReflectionRepository(engine)


@pytest.fixture()
def syntheses_repo(engine):
    return MemoirSynthesesRepository(engine)


@pytest.fixture()
def constellation_repo(engine):
    return MemoirConstellationRepository(engine)


def _service(
    reflection_repo,
    syntheses_repo,
    constellation_repo=None,
    llm_caller=None,
    **settings_kwargs,
) -> MemoirReflectionService:
    defaults = {"agent_id": AGENT}
    defaults.update(settings_kwargs)
    return MemoirReflectionService(
        reflection_repository=reflection_repo,
        syntheses_repository=syntheses_repo,
        constellation_repository=constellation_repo,
        settings=ReflectionSettings(**defaults),
        # The DEFAULT caller returns a VALID EMPTY envelope — a considered
        # batch with zero syntheses.
        llm_caller=llm_caller
        or (lambda prompt: '{"episodes": [], "syntheses": []}'),
    )


def _envelope(claims, episode_ids):
    """A well-formed synthesis envelope."""
    return json.dumps(
        {
            "episodes": list(episode_ids),
            "syntheses": list(claims),
        }
    )


def _seed(repo, count=3, importance=9, created_at=None):
    """Seed ``count`` episodes (importance is bounded to 1..10).

    The trigger threshold is 25, so a trigger-ready set needs SEVERAL
    episodes — the sum is the point, not one big row.
    """
    return [
        repo.insert_episode(
            agent_id=AGENT,
            body=f"memory {index}",
            importance=importance,
            created_at=created_at,
        )
        for index in range(count)
    ]


# ── AC1: the derived trigger ─────────────────────────────────────────────


def test_the_trigger_is_derived_from_the_database(repo, reflection_repo, syntheses_repo):
    """One episode with importance 10 does NOT trigger (sum < 25)."""
    repo.insert_episode(agent_id=AGENT, body="a small memory", importance=10)
    service = _service(reflection_repo, syntheses_repo)
    total, _newest = service.derive_trigger()
    assert total == 10
    should_run, reason, _total = service.evaluate_in_day()
    assert should_run is False
    assert reason == "below_threshold"


def test_the_trigger_sum_is_the_database_sum_not_a_counter(
    repo, reflection_repo, syntheses_repo
):
    """The sum is re-derived per tick — a second episode changes it."""
    repo.insert_episode(agent_id=AGENT, body="one", importance=10)
    repo.insert_episode(agent_id=AGENT, body="two", importance=9)
    service = _service(reflection_repo, syntheses_repo)
    total, newest = service.derive_trigger()
    assert total == 19
    assert newest is not None


def test_marking_reflected_at_drops_the_derived_sum_to_zero(
    repo, reflection_repo, syntheses_repo
):
    """The trigger is SELF-CLEARING (V2-D 2.2)."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=10)
    service = _service(reflection_repo, syntheses_repo)
    assert service.derive_trigger()[0] == 10
    reflection_repo.mark_reflected([episode_id])
    assert service.derive_trigger()[0] == 0


def test_the_trigger_uses_effective_importance_coalesce(
    repo, reflection_repo, syntheses_repo
):
    """``COALESCE(poignancy, importance)`` — the importance ruling."""
    from sqlmodel import Session

    from daemon.repositories.memoir.models import MemoirEpisode

    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=2)
    with Session(reflection_repo.engine) as session:
        episode = session.get(MemoirEpisode, episode_id)
        episode.poignancy = 9
        session.add(episode)
        session.commit()
    service = _service(reflection_repo, syntheses_repo)
    assert service.derive_trigger()[0] == 9


# ── AC1: the debounce doubling + the force-fire ──────────────────────────


def test_the_debounce_window_starts_at_the_base(repo, reflection_repo, syntheses_repo):
    _seed(repo, count=3, importance=9)
    service = _service(reflection_repo, syntheses_repo)
    assert service.debounce_s == 60.0
    should_run, reason, _total = service.evaluate_in_day()
    assert should_run is False
    assert reason == "debouncing"


def test_the_debounce_doubles_when_an_episode_arrives(
    repo, reflection_repo, syntheses_repo
):
    """A burst DOUBLES the window: 60 -> 120 (AC1)."""
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base)
    service = _service(reflection_repo, syntheses_repo)
    service.evaluate_in_day(base + timedelta(seconds=1))
    assert service.debounce_s == 60.0

    # A NEW episode arrives during the debounce.
    repo.insert_episode(
        agent_id=AGENT,
        body="two",
        importance=9,
        created_at=base + timedelta(seconds=10),
    )
    service.evaluate_in_day(base + timedelta(seconds=11))
    assert service.debounce_s == 120.0


def test_the_debounce_is_capped_at_the_ceiling(
    repo, reflection_repo, syntheses_repo
):
    """The doubling stops at the 960 s ceiling (AC1)."""
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base)
    service = _service(reflection_repo, syntheses_repo)
    service.evaluate_in_day(base + timedelta(seconds=1))
    for index in range(2, 12):
        repo.insert_episode(
            agent_id=AGENT,
            body=f"episode {index}",
            importance=9,
            created_at=base + timedelta(seconds=index),
        )
        service.evaluate_in_day(base + timedelta(seconds=index))
    assert service.debounce_s == 960.0


def test_the_debounce_fires_after_the_idle_window(
    repo, reflection_repo, syntheses_repo
):
    """sum >= 25 AND idle >= 60 s ⇒ the in-day pass is due (AC1)."""
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base)
    service = _service(reflection_repo, syntheses_repo)
    should_run, reason, total = service.evaluate_in_day(base + timedelta(seconds=61))
    assert total == 27
    assert should_run is True
    assert reason == "idle"


def test_the_force_fire_rescues_a_continuous_stream(
    repo, reflection_repo, syntheses_repo
):
    """A busy stream never goes idle — the 3600 s force-fire does (AC1)."""
    base = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    _seed(repo, count=3, importance=9, created_at=base)
    service = _service(reflection_repo, syntheses_repo)
    # The trigger becomes ready at t=0.
    service.evaluate_in_day(base)
    assert service.debounce_s == 60.0
    # 3601 s later the stream is still busy (a new episode every minute).
    moment = base + timedelta(seconds=3601)
    for index in range(60):
        repo.insert_episode(
            agent_id=AGENT,
            body=f"burst {index}",
            importance=9,
            created_at=base + timedelta(seconds=3600 - index),
        )
    should_run, reason, _total = service.evaluate_in_day(moment)
    assert should_run is True
    assert reason == "force_fire"


# ── AC2: the budgets ─────────────────────────────────────────────────────


def test_the_pass_stops_after_five_syntheses(
    repo, reflection_repo, syntheses_repo
):
    """The synthesis cap is 5 — the sixth candidate is NOT accepted."""
    episode_ids = [
        repo.insert_episode(agent_id=AGENT, body=f"memory {index}", importance=9)
        for index in range(6)
    ]

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": f"belief number {index}",
                    "source_episode_ids": [episode_ids[index]],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
                for index in range(6)
            ],
            episode_ids,
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 5
    assert any("synthesis_cap_hit" in note for note in outcome.notes)
    assert len(syntheses_repo.list_active_syntheses(AGENT, limit=50)) == 5


def test_a_claim_over_the_character_bound_is_rejected_not_truncated(
    repo, reflection_repo, syntheses_repo
):
    """1001 chars ⇒ REJECTED, and the stored claim is never shorter."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)
    long_claim = "x" * 1001

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": long_claim,
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 0
    assert outcome.syntheses_rejected == 1
    assert syntheses_repo.list_active_syntheses(AGENT) == []


def test_a_claim_at_the_character_bound_is_accepted(
    repo, reflection_repo, syntheses_repo
):
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)
    claim = "y" * 1000

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": claim,
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 1
    rows = syntheses_repo.list_active_syntheses(AGENT)
    assert len(rows[0].claim) == 1000


def test_the_llm_call_cap_is_counted_at_dispatch(
    repo, reflection_repo, syntheses_repo
):
    """The 4-call cap stops the pass; the calls are counted AT DISPATCH."""
    episode_ids = [
        repo.insert_episode(agent_id=AGENT, body=f"memory {index}", importance=9)
        for index in range(12)
    ]
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        return _envelope([], episode_ids)

    # One episode per chunk — 12 chunks, 4 calls.
    service = _service(
        reflection_repo,
        syntheses_repo,
        llm_caller=fake_llm,
        max_prompt_tokens=1,
    )
    outcome = service.run_in_day_pass()
    assert len(calls) == 4
    assert outcome.llm_calls == 4
    assert any("call_cap_hit" in note for note in outcome.notes)
    assert outcome.deferred > 0


def test_a_duplicate_belief_returns_the_existing_row(
    repo, reflection_repo, syntheses_repo
):
    """The dedup key makes a retry a NO-OP (AC2 / V2-17)."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "the Emperor is patient",
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    first = service.run_in_day_pass()
    assert first.syntheses_accepted == 1

    # The SAME claim over the SAME sources — the dedup key hits.
    duplicate = syntheses_repo.find_duplicate(
        AGENT, "the Emperor is patient", [episode_id]
    )
    assert duplicate is not None
    assert (
        synthesis_dedup_key("the Emperor is patient", [episode_id])
        == synthesis_dedup_key(
            duplicate.claim, list(duplicate.source_episode_ids or [])
        )
    )


def test_an_uncited_belief_is_refused(repo, reflection_repo, syntheses_repo):
    """V2-6: a belief with no source is refused."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [{"claim": "a belief from nowhere", "source_episode_ids": [], "verb": "CREATE"}],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 0
    assert syntheses_repo.list_active_syntheses(AGENT) == []


def test_a_belief_citing_an_unknown_episode_is_refused(
    repo, reflection_repo, syntheses_repo
):
    """V2-D 2.3: the citations must name REAL episodes."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "a belief about nothing",
                    "source_episode_ids": ["not-an-episode"],
                    "verb": "CREATE",
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 0


def test_a_verb_outside_the_vocabulary_is_refused(
    repo, reflection_repo, syntheses_repo
):
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "a belief",
                    "source_episode_ids": [episode_id],
                    "verb": "BELIEVE",
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 0


def test_the_confidence_is_clamped_not_rejected(
    repo, reflection_repo, syntheses_repo
):
    """§3-A4: clamping a scalar is not truncating a claim."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "a belief",
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 7.5,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 1
    row = syntheses_repo.list_active_syntheses(AGENT)[0]
    assert row.confidence == 1.0


def test_a_garbage_envelope_leaves_the_batch_unmarked(
    repo, reflection_repo, syntheses_repo
):
    """A batch-level parse failure does NOT mark the episodes."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return "this is not JSON at all"

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.marked == 0
    assert service.derive_trigger()[0] == 9


def test_a_model_refusal_counts_as_a_considered_empty(
    repo, reflection_repo, syntheses_repo
):
    """§3-A4: a refusal is a valid-empty — the batch IS marked."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return "I cannot answer this one."

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.syntheses_accepted == 0
    assert outcome.marked == 1
    assert service.derive_trigger()[0] == 0


def test_a_truncated_envelope_is_refused(repo, reflection_repo, syntheses_repo):
    """§3-A4: a truncated envelope must not be accepted."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        # The last-"}" fallback of the house extractor would return a
        # TRUNCATED object — the completeness check must catch it.
        return '{"episodes": ["%s"], "syntheses": [{"claim": "a be' % episode_id

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()
    assert outcome.marked == 0
    assert outcome.syntheses_accepted == 0


# ── the claim + the stale takeover (AC3 / V2-35) ─────────────────────────


def test_the_claim_is_taken_and_released(repo, reflection_repo, syntheses_repo):
    service = _service(reflection_repo, syntheses_repo)
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    assert reflection_repo.claim(now=now) is True
    assert reflection_repo.claim(now=now) is False
    assert reflection_repo.release_claim() is True
    assert reflection_repo.claim(now=now) is True


def test_a_stale_claim_is_taken_over(repo, reflection_repo, syntheses_repo):
    """V2-35 / SF5: a claim older than 15 minutes is re-takeable."""
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    assert reflection_repo.claim(now=now) is True
    fresh = now + timedelta(seconds=60)
    assert reflection_repo.claim(now=fresh) is False
    stale = now + timedelta(seconds=MEMOIR_REFLECTION_CLAIM_STALE_SECONDS + 60)
    assert reflection_repo.claim(now=stale) is True


def test_the_nightly_pass_stamps_the_nightly_date(
    repo, reflection_repo, syntheses_repo
):
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    repo.insert_episode(agent_id=AGENT, body="one", importance=9, created_at=now)
    service = _service(reflection_repo, syntheses_repo)
    service.run_nightly_pass(now)
    assert reflection_repo.last_nightly_date() == "2026-11-09"


def test_the_nightly_gate_is_closed_after_the_pass(
    repo, reflection_repo, syntheses_repo
):
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    repo.insert_episode(agent_id=AGENT, body="one", importance=9, created_at=now)
    service = _service(reflection_repo, syntheses_repo)
    assert service.nightly_gate_open(now) is True
    service.run_nightly_pass(now)
    assert service.nightly_gate_open(now) is False


def test_the_nightly_gate_is_outside_the_quiet_hours(
    repo, reflection_repo, syntheses_repo
):
    service = _service(reflection_repo, syntheses_repo)
    assert service.nightly_gate_open(
        datetime(2026, 11, 9, 1, 59, tzinfo=timezone.utc)
    ) is False
    assert service.nightly_gate_open(
        datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
    ) is False
    assert service.nightly_gate_open(
        datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)
    ) is True


def test_the_nightly_pass_walks_the_backlog_without_the_window(
    repo, reflection_repo, syntheses_repo
):
    """C2 / V2-27: the catch-up pass ignores the 2-day window."""
    old = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    episode_id = repo.insert_episode(
        agent_id=AGENT, body="an old memory", importance=9, created_at=old
    )
    service = _service(reflection_repo, syntheses_repo)
    assert service.derive_trigger()[0] == 0  # outside the window
    outcome = service.run_nightly_pass(
        datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    )
    assert outcome.considered == 1
    assert outcome.marked == 1


def test_the_nightly_pass_is_capped_at_the_episode_cap(
    repo, reflection_repo, syntheses_repo
):
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    for index in range(35):
        repo.insert_episode(
            agent_id=AGENT,
            body=f"memory {index}",
            importance=9,
            created_at=now + timedelta(seconds=index),
        )
    service = _service(reflection_repo, syntheses_repo)
    outcome = service.run_nightly_pass(now)
    assert outcome.considered <= 30


def test_the_nightly_scan_is_oldest_first(repo, reflection_repo, syntheses_repo):
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    ids = []
    for index in range(3):
        ids.append(
            repo.insert_episode(
                agent_id=AGENT,
                body=f"memory {index}",
                importance=9,
                created_at=now + timedelta(seconds=index),
            )
        )
    rows = reflection_repo.scan_unreflected(AGENT, limit=30, window_start=None)
    assert [row.id for row in rows] == ids


# ── the pass log line ────────────────────────────────────────────────────


def test_the_pass_emits_the_structured_log_line(
    repo, reflection_repo, syntheses_repo, caplog
):
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "a belief",
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    with caplog.at_level(logging.INFO, logger="daemon.services.memoir_reflection_service"):
        service.run_in_day_pass()
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[MemoirReflection]" in text
    assert "syntheses=1" in text


# ── the constellation pass id (the phase-1 review note) ──────────────────


def test_the_reflection_engine_always_passes_an_explicit_pass_id(
    repo, reflection_repo, syntheses_repo, constellation_repo
):
    """The engine's ``record_line`` call ALWAYS carries a pass id."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)
    captured = []
    original = constellation_repo.record_line

    def spy(*args, **kwargs):
        captured.append(kwargs.get("pass_id"))
        return original(*args, **kwargs)

    constellation_repo.record_line = spy  # type: ignore[method-assign]

    def fake_llm(prompt):
        if "extract entity mentions" in prompt.lower():
            return json.dumps(
                {
                    "episodes": [episode_id],
                    "mentions": [
                        {
                            "episode_id": episode_id,
                            "name": "the Emperor",
                            "star_type": "person",
                        }
                    ],
                }
            )
        return _envelope([], [episode_id])

    service = _service(
        reflection_repo,
        syntheses_repo,
        constellation_repo=constellation_repo,
        llm_caller=fake_llm,
    )
    outcome = service.run_in_day_pass()
    assert captured, "the extraction call must have recorded a line"
    assert all(pass_id for pass_id in captured)
    assert all(isinstance(pass_id, str) for pass_id in captured)
    assert outcome.lines_written == 1


def test_a_null_pass_id_never_increments_the_mention_count(
    constellation_repo,
):
    """The phase-1 CRASH-RECOVERY GUARD, pinned.

    ``record_line`` increments ``mention_count`` only when the incoming
    ``pass_id`` differs from the stored one, and the comparison is
    ``IS DISTINCT FROM`` — so a ``None`` pass id is NOT distinct from a
    ``None`` pass id, and the count NEVER moves.
    """
    from sqlmodel import Session

    from daemon.repositories.memoir.models import MemoirEpisode

    with Session(constellation_repo.engine) as session:
        episode_id = "episode-1"
        session.add(
            MemoirEpisode(
                id=episode_id,
                agent_id=AGENT,
                body="one",
                created_at=datetime.now(timezone.utc),
                event_at=datetime.now(timezone.utc),
                importance=5,
            )
        )
        session.commit()

    star = constellation_repo.upsert_star(AGENT, "the Emperor", "person")
    first = constellation_repo.record_line(AGENT, star.id, episode_id, pass_id=None)
    assert first.mention_count == 1
    second = constellation_repo.record_line(AGENT, star.id, episode_id, pass_id=None)
    assert second.mention_count == 1, (
        "a NULL pass id must NEVER increment the mention count"
    )
    third = constellation_repo.record_line(
        AGENT, star.id, episode_id, pass_id="reflect-abc"
    )
    assert third.mention_count == 2
    fourth = constellation_repo.record_line(
        AGENT, star.id, episode_id, pass_id="reflect-abc"
    )
    assert fourth.mention_count == 2, (
        "the SAME pass id must not increment the count either"
    )


# ── the lifespan llm_config carries the failover backup ──────────────────
#
# The reflection lane's LLM calls go through the HA facade
# (``invoke_raw_with_failover``), which reads the backup URL from the
# RAW dict. Without ``base_url_backup`` in that dict the facade is
# built UNCONFIGURED: every attempt uses the primary and NO swap can
# ever occur (see ``daemon/services/llm_failover.py``).
#
# The lifespan builds that dict INLINE (there is no importable
# helper), so the wiring pin reads the source — the house AST pin.


def _lifespan_reflection_llm_config_keys() -> set[str]:
    """The KEYS of the lifespan-built reflection ``llm_config`` dict."""
    import ast
    import pathlib

    api_path = pathlib.Path(__file__).resolve().parents[2] / "daemon" / "api.py"
    tree = ast.parse(api_path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "MemoirReflectionService":
            continue
        for keyword in node.keywords:
            if keyword.arg != "llm_config":
                continue
            if not isinstance(keyword.value, ast.Dict):
                continue
            return {
                key.value
                for key in keyword.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise AssertionError(
        "the lifespan builds no llm_config dict for the reflection service"
    )


def test_the_lifespan_llm_config_carries_the_failover_backup():
    """F2: the lifespan-built dict carries ``base_url_backup``.

    The blueprint clause is absolute — ``base_url_backup`` is NEVER
    stripped. Without it the failover path can NEVER swap.
    """
    keys = _lifespan_reflection_llm_config_keys()
    assert "base_url" in keys
    assert "api_key" in keys
    assert "model" in keys
    assert "base_url_backup" in keys, (
        "the lifespan's llm_config MUST carry base_url_backup — without it "
        "the failover path can never swap (daemon/services/llm_failover.py)"
    )


def test_the_failover_path_can_swap_on_the_lifespan_backup():
    """F2: a dict shaped like the lifespan's lets the facade SWAP.

    The primary answers 500 (a retryable status); the facade exhausts
    the primary slice, swaps to the backup and the call succeeds there.
    The backup is reached ONLY because the dict carries the key.
    """
    import httpx
    import openai

    from daemon.services.llm_failover import (
        current_failover_url,
        invoke_raw_with_failover,
    )

    primary = "https://primary.test/v1"
    backup = "https://backup.test/v1"
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.host)
        if request.url.host == "backup.test":
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": "gpt-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok-backup"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        return httpx.Response(
            500, json={"error": {"message": "down", "type": "server_error"}}
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    # the dict shape the lifespan builds — the backup key included.
    llm_config = {
        "base_url": primary,
        "base_url_backup": backup,
        "api_key": "test",
        "model": "gpt-test",
    }

    def _call():
        url = current_failover_url() or primary
        client = openai.OpenAI(
            api_key="test",
            base_url=url or None,
            http_client=http_client,
            max_retries=0,
        )
        return client.chat.completions.create(
            model="gpt-test", messages=[{"role": "user", "content": "hi"}]
        )

    result = invoke_raw_with_failover(_call, llm_config)
    assert result.choices[0].message.content == "ok-backup"
    assert "primary.test" in captured, "the primary must be tried first"
    assert "backup.test" in captured, (
        "the failover path must be able to SWAP to the backup — the dict "
        "carries base_url_backup"
    )
