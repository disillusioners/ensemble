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
# THE SEAM IS IMPORTABLE. The lifespan builds that dict through
# ``daemon.api._reflection_llm_config`` — a MODULE-LEVEL helper — so
# every pin below drives the REAL dict instead of a hand-built
# look-alike.
#
# WHY THE SEAM MATTERS (the MUT-5 lesson): the FIRST version of this
# pin was AST-only and read the dict's KEY NAMES. A typo'd ATTRIBUTE
# (``base_url_backu``) left the VALUE permanently ``None`` — the key
# SPELLING was still right, so every leg stayed GREEN while the
# failover path could never swap. A key-spelling pin cannot see a dead
# value. The behavioural legs below read the VALUE, and the facade's
# own ``is_configured`` gate is asserted against the real dict.

_LIFESPAN_PRIMARY_URL = "https://primary.test/v1"
_LIFESPAN_BACKUP_URL = "https://backup.test/v1"


def _real_reflection_llm_config() -> dict:
    """The REAL dict the lifespan hands the reflection service."""
    from types import SimpleNamespace

    from daemon.api import _reflection_llm_config

    config = SimpleNamespace(
        llm=SimpleNamespace(
            base_url=_LIFESPAN_PRIMARY_URL,
            base_url_backup=_LIFESPAN_BACKUP_URL,
            api_key="test-key",
            model="gpt-test",
        )
    )
    return _reflection_llm_config(config)


def _lifespan_llm_config_keyword_node():
    """The AST node of the lifespan's ``llm_config`` keyword value."""
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
            if keyword.arg == "llm_config":
                return keyword.value
    raise AssertionError(
        "the lifespan builds no llm_config for the reflection service"
    )


def test_the_reflection_llm_config_helper_is_module_level_and_importable():
    """The helper is MODULE-LEVEL in ``daemon.api`` — and importable."""
    import ast
    import pathlib

    api_path = pathlib.Path(__file__).resolve().parents[2] / "daemon" / "api.py"
    tree = ast.parse(api_path.read_text())
    top_level = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_reflection_llm_config"
    ]
    assert top_level, (
        "_reflection_llm_config must be a MODULE-LEVEL function in "
        "daemon/api.py — a closure inside lifespan() is not importable "
        "and cannot be pinned behaviourally"
    )
    from daemon.api import _reflection_llm_config

    assert callable(_reflection_llm_config)


def test_the_lifespan_uses_the_importable_reflection_llm_config_helper():
    """The lifespan call site USES the helper — no second hand-built dict."""
    import ast

    node = _lifespan_llm_config_keyword_node()
    assert isinstance(node, ast.Call), (
        "the lifespan's llm_config MUST come from "
        "_reflection_llm_config(config) — a second hand-built dict is a "
        "second place to drop base_url_backup"
    )
    func = node.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    assert name == "_reflection_llm_config"


def test_the_real_reflection_llm_config_carries_the_failover_backup():
    """F2: the REAL dict carries the backup VALUE (not just the key)."""
    llm_config = _real_reflection_llm_config()
    assert llm_config["base_url"] == _LIFESPAN_PRIMARY_URL
    assert llm_config["base_url_backup"] == _LIFESPAN_BACKUP_URL, (
        "the reflection llm_config MUST carry the backup VALUE — a "
        "typo'd attribute leaves it None and the failover path can never "
        "swap (daemon/services/llm_failover.py)"
    )
    assert llm_config["api_key"] == "test-key"
    assert llm_config["model"] == "gpt-test"


def test_the_real_reflection_llm_config_configures_the_failover_shim():
    """The REAL dict actually CONFIGURES the failover swap path."""
    from daemon.services.llm_failover import _RawFailoverShim

    llm_config = _real_reflection_llm_config()
    shim = _RawFailoverShim(
        llm_config.get("base_url"), llm_config.get("base_url_backup")
    )
    assert shim.is_configured is True, (
        "the facade's own gate must be OPEN for the real dict — "
        "is_configured is False when the backup value never arrived"
    )


def test_the_failover_path_can_swap_on_the_real_lifespan_dict():
    """F2: the REAL dict lets the facade SWAP.

    The primary answers 500 (a retryable status); the facade exhausts
    the primary slice, swaps to the backup and the call succeeds there.
    The backup is reached ONLY because the REAL dict carries the value.
    """
    import httpx
    import openai

    from daemon.services.llm_failover import (
        current_failover_url,
        invoke_raw_with_failover,
    )

    primary = _LIFESPAN_PRIMARY_URL
    backup = _LIFESPAN_BACKUP_URL
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
    # THE REAL dict — not a hand-built look-alike.
    llm_config = _real_reflection_llm_config()

    def _call():
        url = current_failover_url() or primary
        client = openai.OpenAI(
            api_key=llm_config["api_key"],
            base_url=url or None,
            http_client=http_client,
            max_retries=0,
        )
        return client.chat.completions.create(
            model=llm_config["model"],
            messages=[{"role": "user", "content": "hi"}],
        )

    result = invoke_raw_with_failover(_call, llm_config)
    assert result.choices[0].message.content == "ok-backup"
    assert "primary.test" in captured, "the primary must be tried first"
    assert "backup.test" in captured, (
        "the failover path must be able to SWAP to the backup — the REAL "
        "dict carries base_url_backup"
    )


def test_the_service_preserves_the_backup_value_to_the_failover_facade(
    monkeypatch,
):
    """THE SERVICE→FACADE HOP: the value survives the service seam.

    The helper hands the service a dict; the service stores it and
    hands it to ``invoke_raw_with_failover``. A strip introduced at
    EITHER seam (the service's ``__init__`` or ``_house_llm_call``)
    must turn this RED — the value is dropped between the helper and
    the facade.
    """
    from daemon.services import llm_failover as failover_module
    from daemon.services.memoir_reflection_service import (
        MemoirReflectionService,
    )

    captured: dict = {}

    def _capture(factory, llm_config, **kwargs):
        captured["llm_config"] = llm_config
        return "ok"

    monkeypatch.setattr(failover_module, "invoke_raw_with_failover", _capture)

    service = MemoirReflectionService(
        reflection_repository=object(),
        syntheses_repository=object(),
        llm_config=_real_reflection_llm_config(),
    )
    service._house_llm_call("a prompt")

    assert "llm_config" in captured, (
        "the house lane MUST route through the failover facade"
    )
    assert captured["llm_config"].get("base_url_backup") == _LIFESPAN_BACKUP_URL, (
        "the backup VALUE must survive the SERVICE→FACADE hop — a strip "
        "at the service seam silently disables the failover swap"
    )


def test_the_house_llm_client_disables_the_sdk_retry(monkeypatch):
    """F: the SDK's OWN retry is OFF — the facade owns the discipline.

    The openai SDK's default ``max_retries=2`` issues up to THREE HTTP
    requests per facade attempt. The facade's attempt ladder is 6 (with
    a configured backup), so the SDK's ladder would silently turn one
    LLM call into up to 18 HTTP requests. The house lane sets 0 (see
    ``daemon/plugin_subsystem/opendesign/generate.py``).
    """
    import openai

    from daemon.services.memoir_reflection_service import (
        MemoirReflectionService,
    )

    captured: dict = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            message = type("Message", (), {"content": "ok"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = type("Chat", (), {"completions": _FakeCompletions()})()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)

    service = MemoirReflectionService(
        reflection_repository=object(),
        syntheses_repository=object(),
        llm_config={
            "base_url": "https://primary.test/v1",
            "base_url_backup": "https://backup.test/v1",
            "api_key": "test-key",
            "model": "gpt-test",
        },
    )
    service._house_llm_call("a prompt")

    assert captured.get("max_retries") == 0, (
        "the SDK's own retry MUST be OFF (max_retries=0) — the default 2 "
        "turns the facade's 6-attempt ladder into up to 18 HTTP requests "
        "per LLM call"
    )


# ── G: the mark / unmark distinction ────────────────────────────────────
#
# A marked episode is never re-considered (``reflected_at`` is the
# watermark). So marking an episode whose belief never landed LOSES the
# belief forever. The distinction:
#
# * INVARIANT REJECTION (too long, uncited, an unknown episode, a verb
#   outside the vocabulary) ⇒ the candidate is PERMANENTLY invalid ⇒
#   MARK (it will never become valid);
# * WRITE FAILURE / CAP-DEFERAL ⇒ the belief is missing but VALID ⇒
#   UNMARK (the next pass retries it). THE DEDUP KEY (V2-17) is what
#   makes that retry idempotent: the beliefs that DID land come back as
#   "duplicate" and are skipped.


def test_a_write_failure_leaves_the_episode_unmarked_for_the_next_pass(
    repo, reflection_repo, syntheses_repo, monkeypatch
):
    """G: a WRITE FAILURE must not mark the episode — the belief is lost
    forever if the episode is marked."""
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def _boom(*args, **kwargs):
        raise RuntimeError("the store is unavailable")

    monkeypatch.setattr(syntheses_repo, "create_synthesis", _boom)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "the Emperor prefers the zebra",
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()

    assert outcome.syntheses_rejected == 1
    assert outcome.marked == 0, (
        "a failed write MUST NOT mark the episode — the belief is lost "
        "forever if the episode is marked"
    )
    assert service.derive_trigger()[0] == 9, (
        "the episode must stay UNREFLECTED so the next pass retries it"
    )


def test_an_invariant_rejection_still_marks_the_episode(
    repo, reflection_repo, syntheses_repo
):
    """G: an INVARIANT rejection still MARKS — it is permanent.

    A claim over the character bound will NEVER become valid: the
    episode was considered and stays marked. An unmarked episode would
    re-derive the same invalid belief forever.
    """
    episode_id = repo.insert_episode(agent_id=AGENT, body="one", importance=9)

    def fake_llm(prompt):
        return _envelope(
            [
                {
                    "claim": "x" * 1001,
                    "source_episode_ids": [episode_id],
                    "verb": "CREATE",
                    "confidence": 0.8,
                }
            ],
            [episode_id],
        )

    service = _service(reflection_repo, syntheses_repo, llm_caller=fake_llm)
    outcome = service.run_in_day_pass()

    assert outcome.syntheses_rejected == 1
    assert outcome.marked == 1, (
        "an invariant rejection is PERMANENT — the episode stays marked"
    )


def test_the_cap_deferral_leaves_the_dropped_belief_unmarked(
    repo, reflection_repo, syntheses_repo
):
    """G: a CAP-DEFERAL must not mark the dropped candidate's episode.

    The belief did not land (the synthesis cap was spent) — the episode
    stays UNREFLECTED so the next pass re-derives it.
    """
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
    assert len(outcome.considered_ids) == 5, (
        "only the five beliefs that LANDED may be marked — the dropped "
        "candidate's episode must stay unreflected so the next pass "
        "re-derives it"
    )
    assert episode_ids[5] not in outcome.considered_ids, (
        "the DROPPED candidate's episode must NOT be marked"
    )
    assert set(outcome.considered_ids) == set(episode_ids[:5])
    assert outcome.marked == 5
