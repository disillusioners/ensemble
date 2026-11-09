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
from sqlmodel import Session, SQLModel, StaticPool, create_engine, func, select

from daemon.repositories.memoir.constellation_repository import (
    MemoirConstellationRepository,
)
from daemon.repositories.memoir.models import MemoirLine, MemoirStar
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
        svc.extract(chunk, pass_id="pass-1", now=NOW)
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
        svc.extract(chunk, pass_id="pass-1", now=NOW)
        star = constellation_repo.resolve_star(AGENT, "Hot Star")
        assert caller.summarize_calls == 1

        # The cooldown re-run: 1h later, still inside the 24h window.
        svc.extract(
            chunk,
            pass_id="pass-2",
            llm_caller=caller,
            now=NOW + timedelta(hours=1),
        )
        assert caller.summarize_calls == 1  # NO second LLM call
        with Session(constellation_repo.engine) as session:
            row = session.get(MemoirStar, star.id)
            assert row.description == "first summary"  # the cached summary

    def test_tier2_is_capped_at_one_summarization_per_pass(
        self, repo, constellation_repo
    ):
        """Two qualifying stars ⇒ still exactly ONE summarization per pass."""
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
        svc.extract(chunk, pass_id="pass-1", now=NOW)
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
