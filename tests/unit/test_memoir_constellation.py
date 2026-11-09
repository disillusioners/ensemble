"""Memoir Store v2 — the CONSTELLATION SERVICE (phase 3, T3.6).

Plan: ``.agents/shared/planning/ari-memoir-store/v2-phase3-plan.md``
T3.6 / AC1 / AC2 / AC7; packs
``test/packs/memoir_v2_constellation_service_test.sh`` (AC1 + AC7) and
``test/packs/memoir_v2_star_description_test.sh`` (AC2).

What this suite pins:

* **AC1 — cold-path extraction & alias normalization**: an episode with
  ``stars=["The Emperor", "agents-ensemble"]`` creates/updates the
  canonical stars and records ``memoir_line`` edges; the alias
  resolution maps "the Emperor" onto the ONE canonical "The Emperor"
  star; the ``last_pass_id`` guard leaves ``mention_count`` untouched on
  a same-pass re-run and increments on a distinct pass;
* **AC1/DD-12 — background extraction NEVER increments heat**: the
  stored accumulator moves ONLY on explicit recall (the AC6 mutation
  leg (c) flips this test RED);
* **AC2 — the two-tier description thresholds (V2-D 3.2)**: fragments
  are ``COUNT(*)`` of distinct ``memoir_line`` rows, NEVER
  ``SUM(mention_count)`` — one episode mentioned ten times
  (``COUNT(*)=1``, ``SUM=10``) stays Tier 1 while the SUM alone would
  have crossed the threshold (the discriminator the AC6 mutation leg
  (a) flips); 5 DISTINCT episodes totalling >1200 chars trigger the ONE
  Tier-2 summarization call and stamp ``description_as_of``; a second
  run inside the 24h cooldown re-uses the cached summary; Tier 2 is
  capped at ONE summarization call per pass;
* **AC7 — the bounded historical seed (C2 / V2-27)**: the nightly pass
  over the pre-cutover corpus walks the backlog OLDEST-FIRST under the
  ``MEMOIR_REFLECTION_NIGHTLY_CAP`` episode cap, seeds the Constellation
  from the historical ``stars[]``, and the seeded star is queryable
  through ``memoir_about`` with its historical episode line.
* **the PER-PASS LLM-call budget (V2-D 3.9)**: the constellation cap is
  a PASS cap, never a per-chunk cap — a multi-chunk pass spends at most
  TWO constellation LLM calls (extraction + Tier-2) in total, and the
  heuristic ``stars[]`` seed is NEVER blocked by a spent budget;
* **the heat gate (V2-D 3.3)**: a FAILED connected-episode fetch renders
  the fail-soft ``(none)`` dossier but does NOT increment the stored
  heat — a lookup that could not read its episodes is not a hit.

Every DB touch runs over an in-memory SQLite engine built from the
SQLModel metadata (the phase-1/phase-2 suite precedent). The engine is
thread-shareable (``StaticPool`` + ``check_same_thread=False``) because
the AC7 leg closes the loop through the real ``memoir_about`` tool,
whose DB touch runs under ``asyncio.to_thread``. No LLM is reachable —
the heuristic ``stars[]`` seed is the primary mechanism and every
caller in this file is a fake.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlmodel import Session, SQLModel, StaticPool, create_engine, func, select

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.models import MemoirEpisode, MemoirLine, MemoirStar
from daemon.repositories.memoir.reflection_repository import (
    MemoirReflectionRepository,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.repositories.memoir.syntheses_repository import (
    MemoirSynthesesRepository,
)
from daemon.services.memoir_constellation_service import (
    MEMOIR_STAR_SUMMARY_MIN_CHARS,
    MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS,
    MemoirConstellationService,
)
from daemon.services.memoir_reflection_service import (
    MEMOIR_REFLECTION_NIGHTLY_CAP,
    ConstellationCallBudget,
    MemoirReflectionService,
    ReflectionSettings,
)
from daemon.tools.memoir import MEMOIR_AUTHORIZED_AGENT, create_memoir_tools

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

AGENT = MEMOIR_AUTHORIZED_AGENT

#: The AC7 pre-cutover anchor (the plan's cutover instant, V2-27).
CUTOVER = datetime(2026, 11, 9, 0, 0, 0, tzinfo=timezone.utc)

#: A fixed "now" for the two-tier clock so the 24h cooldown is exact.
NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


class CountingCaller:
    """A fake LLM caller that RECORDS every prompt it is handed."""

    def __init__(self, summary: str = "a coherent ~300 word card") -> None:
        self.summary = summary
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.summary

    @property
    def summarize_calls(self) -> int:
        """The Tier-2 summarization calls (that prompt names the entity card)."""
        return sum(
            1
            for p in self.prompts
            if "Summarize the following memory excerpts" in p
        )


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def constellation_repo(engine):
    return MemoirConstellationRepository(engine)


def _reflection_service(
    engine,
    constellation_repo,
    llm_caller=None,
    **settings_kwargs,
) -> MemoirReflectionService:
    """The full reflection engine over one engine (the phase-2 donor shape).

    The default caller returns a VALID EMPTY synthesis envelope, so the
    pass's synthesis half is inert and the constellation half is the only
    thing under observation.
    """
    defaults = {"agent_id": AGENT}
    defaults.update(settings_kwargs)
    return MemoirReflectionService(
        reflection_repository=MemoirReflectionRepository(engine),
        syntheses_repository=MemoirSynthesesRepository(engine),
        constellation_repository=constellation_repo,
        settings=ReflectionSettings(**defaults),
        llm_caller=llm_caller or (lambda prompt: '{"episodes": [], "syntheses": []}'),
    )


def _svc(
    constellation_repo, llm_caller=None
) -> MemoirConstellationService:
    """The bare constellation service (no reflection engine)."""
    return MemoirConstellationService(
        constellation_repo,
        llm_caller=llm_caller,
        clock=lambda: NOW,
        agent_id=AGENT,
    )


def _episode(
    repo,
    episode_id: str,
    *,
    body: str = "a moment worth keeping",
    stars: list[str] | None = None,
    created_at: datetime | None = None,
    title: str | None = None,
) -> str:
    return repo.insert_episode(
        agent_id=AGENT,
        body=body,
        title=title or episode_id,
        stars=stars,
        created_at=created_at,
        event_at=created_at,
    )


def _stand_in(episode_id: str, *, stars: list[str], body: str, event_at) -> SimpleNamespace:
    """A duck-typed episode stand-in for the service's chunk protocol."""
    return SimpleNamespace(
        id=episode_id, stars=list(stars), body=body, event_at=event_at
    )


def _mention_sum(engine, star_id: str) -> int:
    """``SUM(mention_count)`` — the number the fragments metric must NOT be."""
    with Session(engine) as session:
        total = session.exec(
            select(func.sum(MemoirLine.mention_count)).where(
                MemoirLine.star_id == star_id
            )
        ).one()
    return int(total or 0)


# ── AC1: cold-path extraction & alias normalization ──────────────────────


class TestExtractionAndAlias:
    def test_stars_array_creates_canonical_stars_and_lines(
        self, repo, constellation_repo
    ):
        """One episode with two star tags ⇒ two canonical stars, two lines."""
        ep_id = _episode(
            repo,
            "ep-1",
            stars=["The Emperor", "agents-ensemble"],
            created_at=NOW - timedelta(days=1),
        )
        svc = _svc(constellation_repo)
        written = svc.extract(
            [
                _stand_in(
                    ep_id,
                    stars=["The Emperor", "agents-ensemble"],
                    body="a moment",
                    event_at=NOW - timedelta(days=1),
                )
            ],
            pass_id="pass-1",
            now=NOW,
        )
        assert written == 2

        emperor = constellation_repo.resolve_star(AGENT, "The Emperor")
        project = constellation_repo.resolve_star(AGENT, "agents-ensemble")
        assert emperor is not None
        assert project is not None
        assert emperor.name == "The Emperor"
        card = constellation_repo.get_star_card(AGENT, emperor.id)
        assert [line["episode_id"] for line in card["lines"]] == [ep_id]

    def test_alias_resolution_maps_lowercase_surface_to_the_canonical_star(
        self, repo, constellation_repo
    ):
        """"the Emperor" resolves onto the ONE canonical "The Emperor" star."""
        ep1 = _episode(
            repo, "ep-1", stars=["The Emperor"], created_at=NOW - timedelta(days=2)
        )
        ep2 = _episode(
            repo, "ep-2", stars=["the Emperor"], created_at=NOW - timedelta(days=1)
        )
        svc = _svc(constellation_repo)
        svc.extract(
            [
                _stand_in(ep1, stars=["The Emperor"], body="b", event_at=NOW - timedelta(days=2)),
                _stand_in(ep2, stars=["the Emperor"], body="b", event_at=NOW - timedelta(days=1)),
            ],
            pass_id="pass-1",
            now=NOW,
        )

        canonical = constellation_repo.resolve_star(AGENT, "The Emperor")
        assert canonical is not None
        # The lowercase surface does NOT mint a second star — it resolves.
        resolved = constellation_repo.resolve_star(AGENT, "the Emperor")
        assert resolved is not None
        assert resolved.id == canonical.id
        with Session(constellation_repo.engine) as session:
            star_rows = session.exec(
                select(MemoirStar).where(MemoirStar.agent_id == AGENT)
            ).all()
            assert len(star_rows) == 1  # ONE canonical node, not two
        aliases = constellation_repo.get_star_card(AGENT, canonical.id)["aliases"]
        assert "the Emperor" in [a["alias"] for a in aliases]

    def test_the_last_pass_id_guard_prevents_double_counting(
        self, repo, constellation_repo
    ):
        """Re-running the SAME pass leaves ``mention_count`` unchanged (SF2)."""
        ep_id = _episode(
            repo, "ep-1", stars=["The Emperor"], created_at=NOW - timedelta(days=1)
        )
        svc = _svc(constellation_repo)
        chunk = [
            _stand_in(
                ep_id, stars=["The Emperor"], body="b", event_at=NOW - timedelta(days=1)
            )
        ]
        svc.extract(chunk, pass_id="pass-7", now=NOW)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        card = constellation_repo.get_star_card(AGENT, star.id)
        assert card["lines"][0]["mention_count"] == 1

        # The crash-recovery replay: SAME pass id, same chunk.
        svc.extract(chunk, pass_id="pass-7", now=NOW)
        card = constellation_repo.get_star_card(AGENT, star.id)
        assert card["lines"][0]["mention_count"] == 1

    def test_a_distinct_pass_id_increments_the_mention_count(
        self, repo, constellation_repo
    ):
        ep_id = _episode(
            repo, "ep-1", stars=["The Emperor"], created_at=NOW - timedelta(days=1)
        )
        svc = _svc(constellation_repo)
        chunk = [
            _stand_in(
                ep_id, stars=["The Emperor"], body="b", event_at=NOW - timedelta(days=1)
            )
        ]
        svc.extract(chunk, pass_id="pass-1", now=NOW)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        svc.extract(chunk, pass_id="pass-2", now=NOW)
        card = constellation_repo.get_star_card(AGENT, star.id)
        assert card["lines"][0]["mention_count"] == 2
        assert _mention_sum(constellation_repo.engine, star.id) == 2

    def test_background_extraction_never_increments_heat(
        self, repo, constellation_repo
    ):
        """DD-12: extraction is NOT access — the stored heat stays 0.0.

        (AC6 mutation leg (c) increments heat inside the extraction loop
        and flips this test RED.)
        """
        ep_id = _episode(
            repo, "ep-1", stars=["The Emperor"], created_at=NOW - timedelta(days=1)
        )
        svc = _svc(constellation_repo)
        svc.extract(
            [
                _stand_in(
                    ep_id,
                    stars=["The Emperor"],
                    body="b",
                    event_at=NOW - timedelta(days=1),
                )
            ],
            pass_id="pass-1",
            now=NOW,
        )
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        with Session(constellation_repo.engine) as session:
            stored = session.get(MemoirStar, star.id)
            assert float(stored.heat) == 0.0


# ── AC2: the two-tier description thresholds ─────────────────────────────


class TestTwoTier:
    LONG_BODY = "the emperor reviewed the ranks of the realm " * 40  # >1200 chars

    def _five_episodes_over_1200_chars(self, repo, tag: str) -> list[str]:
        """Five distinct episodes whose bodies total >1200 chars."""
        ids = []
        for i in range(MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS):
            ids.append(
                _episode(
                    repo,
                    f"ep-{tag}-{i}",
                    body=("the sovereign ruled on many matters " * 10) + str(i),
                    stars=[tag],
                    created_at=NOW - timedelta(days=6 - i),
                )
            )
        return ids

    def test_one_heavily_mentioned_episode_stays_tier_1(
        self, repo, constellation_repo
    ):
        """THE DISCRIMINATOR (V2-D 3.2): fragments are COUNT(*), never SUM.

        ONE episode mentioned 10 times carries COUNT(*)=1 and SUM=10 with
        a body long enough that the SUM alone would have crossed the
        fragment threshold — the star must stay Tier 1 anyway. (AC6
        mutation leg (a) flips this test RED by measuring the SUM.)
        """
        assert len(self.LONG_BODY) > MEMOIR_STAR_SUMMARY_MIN_CHARS
        ep_id = _episode(
            repo,
            "ep-1",
            body=self.LONG_BODY,
            stars=["The Emperor"],
            created_at=NOW - timedelta(days=1),
        )
        svc = _svc(constellation_repo)
        star = constellation_repo.upsert_star(
            AGENT, "The Emperor", "person", event_at=NOW - timedelta(days=1)
        )
        for pass_n in range(10):
            constellation_repo.record_line(
                AGENT, star.id, ep_id, event_at=NOW, pass_id=f"pass-{pass_n}"
            )

        counted = svc.fragments_count(star.id, AGENT)
        summed = _mention_sum(constellation_repo.engine, star.id)
        assert counted == 1
        assert summed == 10
        # The discriminator, stated the way the plan states it: COUNT
        # says Tier 1 even though the SUM is over the threshold.
        assert counted < MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS
        assert summed >= MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS
        assert svc.needs_tier2(star, now=NOW) is False

        # And a pass that COULD summarize (caller present) does not.
        caller = CountingCaller()
        svc.extract(
            [
                _stand_in(
                    ep_id,
                    stars=["The Emperor"],
                    body=self.LONG_BODY,
                    event_at=NOW,
                )
            ],
            pass_id="pass-later",
            llm_caller=caller,
            now=NOW,
            budget=ConstellationCallBudget(2),
        )
        assert caller.summarize_calls == 0

    def test_five_distinct_episodes_over_1200_chars_trigger_tier_2(
        self, repo, constellation_repo
    ):
        ep_ids = self._five_episodes_over_1200_chars(repo, "Hot Star")
        assert len(ep_ids) == MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS
        caller = CountingCaller()
        svc = _svc(constellation_repo, llm_caller=caller)
        chunk = [
            _stand_in(
                eid, stars=["Hot Star"], body="x" * 300, event_at=NOW - timedelta(days=1)
            )
            for eid in ep_ids
        ]
        svc.extract(
            chunk, pass_id="pass-1", now=NOW, budget=ConstellationCallBudget(2)
        )
        star = constellation_repo.resolve_star(AGENT, "Hot Star")
        assert caller.summarize_calls == 1
        with Session(constellation_repo.engine) as session:
            row = session.get(MemoirStar, star.id)
            assert row.description == "a coherent ~300 word card"
            assert row.description_as_of is not None

    def test_a_second_run_within_24h_uses_the_cached_summary(
        self, repo, constellation_repo
    ):
        ep_ids = self._five_episodes_over_1200_chars(repo, "Hot Star")
        caller = CountingCaller(summary="first summary")
        svc = _svc(constellation_repo, llm_caller=caller)
        chunk = [
            _stand_in(
                eid, stars=["Hot Star"], body="x" * 300, event_at=NOW - timedelta(days=1)
            )
            for eid in ep_ids
        ]
        svc.extract(
            chunk, pass_id="pass-1", now=NOW, budget=ConstellationCallBudget(2)
        )
        star = constellation_repo.resolve_star(AGENT, "Hot Star")
        assert caller.summarize_calls == 1

        # The cooldown re-run: 1h later, still inside the 24h window.
        svc.extract(
            chunk,
            pass_id="pass-2",
            llm_caller=caller,
            now=NOW + timedelta(hours=1),
            budget=ConstellationCallBudget(2),
        )
        assert caller.summarize_calls == 1  # NO second LLM call
        with Session(constellation_repo.engine) as session:
            row = session.get(MemoirStar, star.id)
            assert row.description == "first summary"  # the cached summary

    def test_tier2_is_capped_at_one_summarization_per_extraction_invocation(
        self, repo, constellation_repo
    ):
        """Two qualifying stars ⇒ still exactly ONE summarization per
        EXTRACTION INVOCATION.

        SCOPE (the rename is the fix): this leg drives ONE ``extract()``
        call — it proves the per-INVOCATION cap ("at most one star is
        summarized per chunk invocation").  The PER-PASS cap (the
        budget that spans the chunks of a whole reflection pass) is
        ``TestThePerPassCallBudget`` below.
        """
        hot_ids = self._five_episodes_over_1200_chars(repo, "Hot Star")
        cold_ids = self._five_episodes_over_1200_chars(repo, "Cold Star")
        caller = CountingCaller()
        svc = _svc(constellation_repo, llm_caller=caller)
        chunk = [
            _stand_in(
                eid,
                stars=[tag],
                body="x" * 300,
                event_at=NOW - timedelta(days=1),
            )
            for tag, ids in (("Hot Star", hot_ids), ("Cold Star", cold_ids))
            for eid in ids
        ]
        svc.extract(
            chunk, pass_id="pass-1", now=NOW, budget=ConstellationCallBudget(2)
        )
        assert caller.summarize_calls == 1


# ── AC7: the bounded historical seed (C2 / V2-27) ─────────────────────────


class TestSeed:
    async def test_the_nightly_walk_seeds_the_constellation_oldest_first_capped(
        self,
        repo,
        constellation_repo,
        monkeypatch,
    ):
        """35 pre-cutover episodes ⇒ the OLDEST nightly-cap slice is absorbed.

        The walk is oldest-first (the absorbed ids are exactly the
        ``MEMOIR_REFLECTION_NIGHTLY_CAP`` oldest by ``created_at``), the
        cap holds (the 5 newest stay unreflected and un-extracted), and
        the seeded "The Emperor" star is then queryable through the real
        ``memoir_about`` tool with its historical episode line.
        """
        monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
        total = MEMOIR_REFLECTION_NIGHTLY_CAP + 5
        episode_ids: list[str] = []
        for i in range(total):
            created = CUTOVER - timedelta(days=total - i)  # i=0 is the OLDEST
            episode_ids.append(
                repo.insert_episode(
                    agent_id=AGENT,
                    title=f"historical ruling {i}",
                    body=f"the emperor ratified ruling {i} of the memoir lane",
                    stars=["The Emperor"],
                    created_at=created,
                    event_at=created,
                )
            )
        oldest_first = list(episode_ids)  # insert order == oldest-first

        service = _reflection_service(
            repo.engine, constellation_repo
        )
        outcome = service.run_nightly_pass(now=CUTOVER + timedelta(hours=6))
        assert outcome.ran is True

        # OLDEST-FIRST + CAPPED: exactly the nightly-cap OLDEST episodes
        # were considered/marked; the 5 newest are untouched.
        assert outcome.considered == MEMOIR_REFLECTION_NIGHTLY_CAP
        marked_ids = set(outcome.considered_ids)
        assert marked_ids == set(oldest_first[:MEMOIR_REFLECTION_NIGHTLY_CAP])
        unreflected_ids = set(oldest_first) - marked_ids
        assert unreflected_ids == set(oldest_first[MEMOIR_REFLECTION_NIGHTLY_CAP:])

        # The Constellation absorbed the historical mentions — and ONLY
        # the capped slice of them.
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        assert star is not None
        fragments = _svc(constellation_repo).fragments_count(star.id, AGENT)
        assert fragments == MEMOIR_REFLECTION_NIGHTLY_CAP

        # The seeded star is queryable from day 1 — through the real
        # memoir_about surface, carrying its historical episode line.
        manager = SimpleNamespace(_engine=constellation_repo.engine, config=None)
        tools = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                manager,
                current_instance_id="inst-seed",
                agent_id=AGENT,
            )
        }
        result = await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        assert isinstance(result, str)
        assert "REFUSED" not in result
        assert "=== STAR: The Emperor" in result
        # The card is POPULATED with its historical episode lines — the
        # card renders the most recent 10 of the absorbed slice, so the
        # NEWEST absorbed ruling leads.
        assert "historical ruling" in result
        assert "historical ruling 29" in result
        assert "(none)\n" not in result.split("Connected Episodes")[1].split(
            "Neighbor Stars"
        )[0]


# ── the PER-PASS LLM-call budget (V2-D 3.9) ───────────────────────────────


class ConstellationCountingCaller:
    """A fake LLM caller that counts the CONSTELLATION calls only.

    The reflection engine uses ONE caller for the synthesis AND for the
    constellation, so the counter classifies the prompt: the extraction
    prompt, the Tier-2 prompt and the synthesis prompt are three
    different texts.
    """

    def __init__(self) -> None:
        self.extraction_calls = 0
        self.summarize_calls = 0
        self.synthesis_calls = 0

    @property
    def constellation_calls(self) -> int:
        """Every LLM call the CONSTELLATION made (extraction + Tier-2)."""
        return self.extraction_calls + self.summarize_calls

    def __call__(self, prompt: str) -> str:
        if "You extract ENTITY MENTIONS" in prompt:
            self.extraction_calls += 1
            return '{"episodes": [], "mentions": []}'
        if "Summarize the following memory excerpts" in prompt:
            self.summarize_calls += 1
            return "a coherent ~300 word card"
        self.synthesis_calls += 1
        return '{"episodes": [], "syntheses": []}'


def _seed_a_tier2_star(repo, constellation_repo, tag: str, *, created_at):
    """Seed ONE Tier-2-QUALIFIED star (5 fragments, >1200 chars).

    The seed episodes sit OUTSIDE the in-day scan window (they are
    created long ago), so the reflection pass never CONSIDERS them —
    only their constellation lines matter (the Tier-2 candidate scan
    reads the LINES, not the pass).
    """
    ids = []
    for index in range(MEMOIR_STAR_SUMMARY_MIN_FRAGMENTS):
        ids.append(
            _episode(
                repo,
                f"ep-{tag}-{index}",
                body=("the sovereign ruled on many matters " * 10) + str(index),
                stars=[tag],
                created_at=created_at + timedelta(minutes=index),
            )
        )
    _svc(constellation_repo).extract(
        [
            _stand_in(
                eid,
                stars=[tag],
                body="x" * 300,
                event_at=created_at,
            )
            for eid in ids
        ],
        pass_id=f"seed-{tag}",
        now=NOW,
    )
    return ids


class TestThePerPassCallBudget:
    """THE CONSTELLATION BUDGET IS PER PASS (V2-D 3.9), NOT PER CHUNK.

    One pass dispatches MANY chunks, and ONE chunk invocation can make
    TWO LLM calls (the fallback extraction + the Tier-2 summarization).
    A per-chunk counter therefore let a two-chunk pass reach FOUR calls
    against a cap of TWO.  The budget is now ONE object per pass, and
    every chunk threads it.

    THE HEURISTIC IS FREE: the ``stars[]`` seed never consumes the
    budget, so a spent budget never blocks the primary mechanism.
    """

    def test_a_multi_chunk_pass_spends_at_most_two_constellation_llm_calls(
        self, repo, constellation_repo
    ):
        # TWO Tier-2-qualified stars: with a per-chunk counter each of
        # the two chunks could spend TWO calls (extraction + Tier-2),
        # so the pass reached FOUR against a cap of TWO.
        _seed_a_tier2_star(
            repo,
            constellation_repo,
            "Hot Star",
            created_at=NOW - timedelta(days=30),
        )
        _seed_a_tier2_star(
            repo,
            constellation_repo,
            "Cold Star",
            created_at=NOW - timedelta(days=30),
        )
        # TWO untagged episodes (the LLM fallback needs them) and ONE
        # tagged episode (the heuristic seed).
        for index in range(3):
            _episode(
                repo,
                f"ep-plain-{index}",
                body="an untagged memory that only the extractor can resolve",
                stars=None if index < 2 else ["Heuristic Star"],
                created_at=NOW - timedelta(hours=3 - index),
            )

        caller = ConstellationCountingCaller()
        service = _reflection_service(
            repo.engine,
            constellation_repo,
            llm_caller=caller,
            # One episode per chunk ⇒ THREE chunks in ONE pass.
            max_prompt_tokens=1,
        )
        outcome = service.run_in_day_pass(now=NOW)

        # THE CAP: at most TWO constellation LLM calls for the WHOLE pass.
        assert caller.constellation_calls <= 2
        # The budget was SPENT on real work, not skipped: the first chunk
        # took the extraction and the Tier-2 summarization.
        assert caller.extraction_calls == 1
        assert caller.summarize_calls == 1
        # The metered truth agrees with the caller.
        assert outcome.constellation_calls == 2
        # THE HEURISTIC IS FREE: the tagged episode was extracted even
        # though the budget was spent by the time its chunk ran.
        heuristic_star = constellation_repo.resolve_star(AGENT, "Heuristic Star")
        assert heuristic_star is not None
        assert (
            _svc(constellation_repo).fragments_count(heuristic_star.id, AGENT) == 1
        )


# ── the heat gate (V2-D 3.3): a FAILED fetch is NOT a hit ─────────────────


def _stored_heat(engine, star_id: str) -> float:
    with Session(engine) as session:
        row = session.get(MemoirStar, star_id)
        return float(row.heat)


def _seed_a_populated_star(repo, constellation_repo, tag: str = "The Emperor"):
    """One star with TWO connected episodes (the populated-card shape)."""
    ids = []
    for index in range(2):
        ids.append(
            _episode(
                repo,
                f"ep-{tag}-{index}",
                body="a moment worth keeping",
                stars=[tag],
                created_at=NOW - timedelta(days=2 - index),
                title=f"ruling {index}",
            )
        )
    _svc(constellation_repo).extract(
        [
            _stand_in(
                eid,
                stars=[tag],
                body="a moment worth keeping",
                event_at=NOW - timedelta(days=2),
            )
            for eid in ids
        ],
        pass_id="seed-populated",
        now=NOW,
    )
    return ids


class TestTheHeatHonesty:
    """THE HEAT GATE (V2-D 3.3): a FAILED fetch is NOT a hit.

    The stored accumulator moves ONLY on an explicit recall that really
    READ the episodes.  A fetch that fails renders the fail-soft
    ``(none)`` text and MUST NOT inflate the heat.
    """

    async def test_a_failed_episode_fetch_renders_but_leaves_the_heat_alone(
        self, repo, constellation_repo, engine, monkeypatch
    ):
        monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
        _seed_a_populated_star(repo, constellation_repo)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        assert _stored_heat(engine, star.id) == 0.0

        # THE FAULT: the episode table is gone ⇒ the connected-episode
        # fetch fails while the CARD read still works.
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE memoir_episodes"))

        manager = SimpleNamespace(_engine=engine, config=None)
        tools = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                manager,
                current_instance_id="inst-heat",
                agent_id=AGENT,
            )
        }
        result = await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        # The dossier STILL renders (fail-soft) — the tool never raises.
        assert "REFUSED" not in result
        assert "=== STAR: The Emperor" in result
        assert "(none)" in result.split("Connected Episodes (recent):")[1]
        # THE HONESTY GUARD: the stored heat did NOT move.
        assert _stored_heat(engine, star.id) == 0.0

    async def test_a_deleted_episode_does_not_accrue_heat(
        self, repo, constellation_repo, engine, monkeypatch
    ):
        """A DELETED EPISODE IS NOT A HIT (R3).

        The connected-episode fetch SUCCEEDS (the table is there) but
        the episode ROWS are gone — the lines are dangling edges.  The
        dossier must render the fail-soft ``(none)`` text and the stored
        heat MUST NOT move: a deleted dossier is not a hit.
        """
        monkeypatch.delenv("ENSEMBLE_MEMOIR_ENABLED", raising=False)
        _seed_a_populated_star(repo, constellation_repo)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        assert _stored_heat(engine, star.id) == 0.0

        # THE DELETION: the episode rows are gone, the edges stay.
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM memoir_episodes"))

        manager = SimpleNamespace(_engine=engine, config=None)
        tools = {
            tool_obj.name: tool_obj
            for tool_obj in create_memoir_tools(
                manager,
                current_instance_id="inst-heat-deleted",
                agent_id=AGENT,
            )
        }
        result = await tools["memoir_about"].ainvoke({"star": "The Emperor"})
        assert "REFUSED" not in result
        assert "=== STAR: The Emperor" in result
        assert "(none)" in result.split("Connected Episodes (recent):")[1]
        # THE HONESTY GUARD: the stored heat did NOT move.
        assert _stored_heat(engine, star.id) == 0.0


# ── F-2: the PER-PURPOSE split of the constellation budget (V2-D 3.9) ────


class TestThePerPurposeBudget:
    """THE SHARED POOL IS SPLIT BY PURPOSE (V2-D 3.9 / F-2).

    V2-D 3.9 caps the constellation at "max 1 extraction, max 1 tier-2
    card summarization per PASS".  The code used ONE shared pool of TWO
    with NO per-purpose cap, so a Tier-2-heavy pass could spend BOTH
    calls on Tier-2 and leave the extraction with ZERO — the council's
    own probe produced "2 tier-2 calls, 0 extraction", which defeats
    the T3.1b seed.

    THE ORDERING: the pass runs every chunk's EXTRACTION first and the
    Tier-2 phase LAST, so a Tier-2-qualified star can never starve the
    extraction of its budget unit.
    """

    def test_the_extraction_is_taken_before_the_tier2(
        self, repo, constellation_repo
    ):
        """A Tier-2-qualified star COMPETES with an extraction-needing
        episode ⇒ the extraction happens and the Tier-2 yields.

        The two Tier-2-qualified stars sit in the EARLIER chunks and the
        untagged episode (the one that needs the LLM fallback) in the
        LAST one — the shape that produced "0 extraction" before the
        fix.
        """
        _seed_a_tier2_star(
            repo, constellation_repo, "Hot Star", created_at=NOW - timedelta(days=30)
        )
        _seed_a_tier2_star(
            repo, constellation_repo, "Cold Star", created_at=NOW - timedelta(days=30)
        )
        # TWO tagged episodes (the heuristic path) and ONE untagged
        # episode (the LLM fallback path), OLDEST FIRST.
        _episode(
            repo,
            "ep-tagged-0",
            body="a tagged memory that the heuristic seed resolves",
            stars=["Hot Star"],
            created_at=NOW - timedelta(hours=5),
        )
        _episode(
            repo,
            "ep-tagged-1",
            body="another tagged memory that the heuristic seed resolves",
            stars=["Cold Star"],
            created_at=NOW - timedelta(hours=4),
        )
        _episode(
            repo,
            "ep-plain-0",
            body="an untagged memory that only the extractor can resolve",
            stars=None,
            created_at=NOW - timedelta(hours=3),
        )

        caller = ConstellationCountingCaller()
        service = _reflection_service(
            repo.engine,
            constellation_repo,
            llm_caller=caller,
            max_prompt_tokens=1,
        )
        outcome = service.run_in_day_pass(now=NOW)

        # THE EXTRACTION WAS NOT STARVED: the untagged episode's chunk
        # took the extraction unit.
        assert caller.extraction_calls == 1
        # THE TIER-2 YIELDED: it took its OWN unit (at most one) and
        # never the extraction's.
        assert caller.summarize_calls == 1
        # THE WHOLE PASS STAYED INSIDE THE CAP.
        assert caller.constellation_calls == 2
        assert outcome.constellation_calls == 2

    def test_a_second_extraction_in_the_same_pass_is_refused(
        self, repo, constellation_repo
    ):
        """TWO untagged episodes ⇒ ONE extraction call (the per-purpose cap)."""
        for index in range(2):
            _episode(
                repo,
                f"ep-plain-{index}",
                body="an untagged memory that only the extractor can resolve",
                stars=None,
                created_at=NOW - timedelta(hours=3 - index),
            )

        caller = ConstellationCountingCaller()
        service = _reflection_service(
            repo.engine,
            constellation_repo,
            llm_caller=caller,
            max_prompt_tokens=1,
        )
        outcome = service.run_in_day_pass(now=NOW)

        # THE PER-PURPOSE CAP: the extraction purpose is spent after ONE
        # call — the second chunk's extraction is REFUSED.
        assert caller.extraction_calls == 1
        # ... and the shared pool still had room for the Tier-2 phase.
        assert caller.summarize_calls == 0
        assert outcome.constellation_calls == 1

    def test_a_second_tier2_in_the_same_pass_is_refused(
        self, repo, constellation_repo
    ):
        """TWO Tier-2-qualified stars across TWO chunks ⇒ ONE call.

        The Tier-2 phase runs ONCE, at the END of the pass — so the
        second qualifying star never gets a second LLM call.
        """
        _seed_a_tier2_star(
            repo, constellation_repo, "Hot Star", created_at=NOW - timedelta(days=30)
        )
        _seed_a_tier2_star(
            repo, constellation_repo, "Cold Star", created_at=NOW - timedelta(days=30)
        )
        for index in range(2):
            _episode(
                repo,
                f"ep-tagged-{index}",
                body="a tagged memory that the heuristic seed resolves",
                stars=["Hot Star" if index == 0 else "Cold Star"],
                created_at=NOW - timedelta(hours=4 - index),
            )

        caller = ConstellationCountingCaller()
        service = _reflection_service(
            repo.engine,
            constellation_repo,
            llm_caller=caller,
            max_prompt_tokens=1,
        )
        outcome = service.run_in_day_pass(now=NOW)

        assert caller.summarize_calls == 1
        assert outcome.constellation_calls == 1


# ── F-3: the DURABLE pass identity + the pass unit (V2-29 / A2) ──────────


def _line_row(engine, star_id: str) -> MemoirLine | None:
    with Session(engine) as session:
        return session.exec(
            select(MemoirLine).where(MemoirLine.star_id == star_id)
        ).first()


def _reflected_at(engine, episode_id: str):
    with Session(engine) as session:
        row = session.get(MemoirEpisode, episode_id)
        return None if row is None else row.reflected_at


class TestTheDurablePassIdentity:
    """THE PASS IDENTITY IS DURABLE AND THE UNIT IS ATOMIC (F-3).

    THE OLD DEFECT: the pass id was minted PER CHUNK
    (``reflect-<uuid>``), so a crash-retry re-processed the same work
    under a NEW stamp and the ``last_pass_id`` guard — which only binds
    when the stamp is STABLE — inflated ``mention_count``.  The unit leg
    passed only because it HAND-SUPPLIED a stable id (the
    guard-blindness class).

    THE FIX: the id is minted ONCE, at the PRODUCTION site, and it is
    DERIVED from durable state (the pass's anchor episode), so a
    crash-retry of the same pass mints the SAME id.  The edges and the
    per-episode watermark land in ONE transaction (the pass unit).
    """

    def _seed_one_tagged_episode(self, repo):
        """ONE tagged episode INSIDE the in-day window (its real id)."""
        return _episode(
            repo,
            "ep-durable",
            body="a tagged memory the heuristic seed resolves",
            stars=["The Emperor"],
            created_at=NOW - timedelta(hours=2),
        )

    def test_the_retry_of_the_same_pass_reuses_the_identity(
        self, repo, constellation_repo
    ):
        """The id is minted by the PRODUCTION site and it is STABLE.

        The test NEVER hands the pass an id — the pass mints its own —
        so the leg proves the production mint, not a hand-supplied one.
        """
        episode_id = self._seed_one_tagged_episode(repo)
        caller = ConstellationCountingCaller()
        service = _reflection_service(
            repo.engine, constellation_repo, llm_caller=caller
        )

        service.run_in_day_pass(now=NOW)
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        line = _line_row(repo.engine, star.id)
        assert line is not None
        first_pass_id = line.last_pass_id

        # THE CRASH SIMULATION: the watermark is lost while the edges
        # stay (the state the old per-chunk mint could not survive).
        with repo.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE memoir_episodes SET reflected_at = NULL "
                    "WHERE id = :episode_id"
                ),
                {"episode_id": episode_id},
            )

        # THE RETRY: the same work, re-run.
        service.run_in_day_pass(now=NOW)
        line = _line_row(repo.engine, star.id)
        # THE SAME IDENTITY — the durable id, reproducible across the
        # retry of the same pass.
        assert line.last_pass_id == first_pass_id
        # THE GUARD BOUND: the mention count did NOT inflate.
        assert line.mention_count == 1
        # THE MARKS LAND.
        assert _reflected_at(repo.engine, episode_id) is not None
        # THE PRODUCTION MINT: a ``reflect-<digest>`` identity, never a
        # hand-supplied stamp.
        assert first_pass_id.startswith("reflect-")
        assert len(first_pass_id) == len("reflect-") + 16

    def test_the_edges_roll_back_with_the_marks(
        self, repo, constellation_repo, monkeypatch
    ):
        """THE PASS UNIT: the edges and the marks are ONE transaction.

        The fault is injected INSIDE the unit (the watermark writer
        fails), so the whole unit rolls back — there is never an edge
        without its mark (the window V2-29 / A2 exist to close).
        """
        episode_id = self._seed_one_tagged_episode(repo)
        service = _reflection_service(
            repo.engine, constellation_repo, llm_caller=ConstellationCountingCaller()
        )

        boom = {"armed": True}

        def _failing_mark(self, episode_ids, *, reflected_at=None, session=None):
            if boom["armed"]:
                boom["armed"] = False
                raise RuntimeError("the injected watermark fault")
            return _real_mark(self, episode_ids, reflected_at=reflected_at, session=session)

        _real_mark = MemoirReflectionRepository.mark_reflected
        monkeypatch.setattr(MemoirReflectionRepository, "mark_reflected", _failing_mark)

        service.run_in_day_pass(now=NOW)

        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        # THE ATOMICITY: no edge survived the failed unit ...
        assert _line_row(repo.engine, star.id) is None
        # ... and no mark either.
        assert _reflected_at(repo.engine, episode_id) is None

        # THE RETRY: the pass runs again — the edges land ONCE (not
        # twice) and the marks land.
        service.run_in_day_pass(now=NOW)
        line = _line_row(repo.engine, star.id)
        assert line is not None
        assert line.mention_count == 1
        assert _reflected_at(repo.engine, episode_id) is not None


# ── R1: the LLM paths REFUSE an unbudgeted call (V2-31) ──────────────────


class TestTheBudgetRefusal:
    """AN UNBUDGETED LLM CALL IS A COST-CONTROL BYPASS (V2-31 / R1).

    The extraction and the Tier-2 summarization are MANDATED to run
    under the pass's budget.  A caller that reaches an LLM path with
    ``budget=None`` used to get the call for FREE — the bypass that
    made the cost control advisory.
    """

    def test_the_llm_extraction_is_refused_without_a_budget(
        self, repo, constellation_repo
    ):
        _episode(
            repo,
            "ep-unbudgeted",
            body="an untagged memory that only the extractor can resolve",
            stars=None,
            created_at=NOW - timedelta(hours=1),
        )
        caller = CountingCaller()
        svc = _svc(constellation_repo, llm_caller=caller)
        with pytest.raises(Exception) as excinfo:
            svc.extract(
                [
                    _stand_in(
                        "ep-unbudgeted",
                        stars=[],
                        body="an untagged memory that only the extractor can resolve",
                        event_at=NOW,
                    )
                ],
                pass_id="pass-unbudgeted",
                llm_caller=caller,
                now=NOW,
            )
        assert "budget" in str(excinfo.value).lower()
        # NO LLM CALL WAS TAKEN — the refusal is not a warning, it is a
        # gate.
        assert caller.prompts == []

    def test_the_tier2_summarization_is_refused_without_a_budget(
        self, repo, constellation_repo
    ):
        _seed_a_tier2_star(
            repo, constellation_repo, "Hot Star", created_at=NOW - timedelta(days=30)
        )
        caller = CountingCaller()
        svc = _svc(constellation_repo, llm_caller=caller)
        with pytest.raises(Exception) as excinfo:
            svc.extract(
                [
                    _stand_in(
                        "ep-tier2",
                        stars=["Hot Star"],
                        body="x" * 300,
                        event_at=NOW,
                    )
                ],
                pass_id="pass-unbudgeted",
                llm_caller=caller,
                now=NOW,
            )
        assert "budget" in str(excinfo.value).lower()
        assert caller.summarize_calls == 0


# ── R2: a failed synthesis batch must not starve the seed ────────────────


class TestTheSeedSurvivesAParseFailure:
    """THE CONSTELLATION SEED IS THE PRIMARY MECHANISM (R2).

    A chunk whose SYNTHESIS batch fails to parse used to ``continue``
    BEFORE its constellation extraction, so a permanently-failing
    synthesis starved the seed forever: the stars were never written.
    """

    def test_a_failed_synthesis_batch_still_seeds_the_constellation(
        self, repo, constellation_repo
    ):
        _episode(
            repo,
            "ep-parse-failed",
            body="a tagged memory the heuristic seed resolves",
            stars=["The Emperor"],
            created_at=NOW - timedelta(hours=1),
        )

        class _GarbageSynthesisCaller:
            """Garbage for the SYNTHESIS prompt, a valid envelope for the rest."""

            def __init__(self) -> None:
                self.prompts: list[str] = []

            def __call__(self, prompt: str) -> str:
                self.prompts.append(prompt)
                if "You extract ENTITY MENTIONS" in prompt:
                    return '{"episodes": [], "mentions": []}'
                return "this is not JSON at all"

        caller = _GarbageSynthesisCaller()
        service = _reflection_service(
            repo.engine, constellation_repo, llm_caller=caller
        )
        outcome = service.run_in_day_pass(now=NOW)

        # THE BATCH REALLY FAILED.
        assert "batch_parse_failed" in outcome.notes
        # THE SEED STILL HAPPENED: the star and its edge are there.
        star = constellation_repo.resolve_star(AGENT, "The Emperor")
        assert star is not None
        assert _line_row(repo.engine, star.id) is not None
