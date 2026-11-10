"""Memoir Store v2 — PHASE 6 keepsake proposals (qualification, once-ever,
the measured briefing zone, and the enqueue bridge).

Plan: ``.agents/shared/planning/ari-memoir-store/v2-phase6-plan.md``
(§3.1, T6.1/T6.2/T6.3, AC1/AC2/AC3/AC6). Pack:
``test/packs/memoir_v2_keepsake_proposals_test.sh`` +
``test/packs/memoir_v2_proposal_notification_test.sh``.

What this suite pins:

* **AC1 — qualification**: ``COALESCE(poignancy, importance) >= 8`` AND
  not-a-keepsake AND not-superseded AND the enduring-significance
  heuristic; below-threshold episodes are NEVER proposed;
* **AC1 — ONCE-EVER-PER-EPISODE (V2-36)**: a second proposal attempt for
  the same episode is a SKIP — no error, no second row — for ANY prior
  outcome (pending / enqueued / sent / failed), and the durable
  ``uq_memoir_proposal_agent_episode`` constraint is proven to be the
  racing guard;
* **AC2 — the [PROPOSALS] zone**: the plan's exact header and line shape,
  the EXPLICIT JOIN onto ``memoir_episodes`` (the ledger alone has no
  title/event_at/poignancy), the ``pending``/``enqueued`` status filter,
  the plan's ordering, the ≤ 1,000-char measured bound (header wrapper
  included) and the 3-line cap;
* **AC2 — the §A6 admission ladder** (verbatim rung order
  ``fit dynamic → trim dynamic → trim proposals (drop weakest first) →
  rung-2 withhold``) and the V2-D 6.5 reachability analysis;
* **AC3 — the durable bridge**: ``source="system:memoir-proposal"``, the
  slot consumed ONLY on an accepted enqueue, every failure path leaving
  the row ``pending``, the 1/day gate counted on the CONSUMPTION day
  (``notified_at``) with a ``pending``-only notify target — both parts
  needed across a date rollover — and restart survival (a brand-new
  service over the same engine stays silent);
* **V2-D 6.3 / R2 — no pin authority**: the module holds no pin writer
  and never sets ``is_keepsake``.

FENCE: throwaway in-memory SQLite via ``StaticPool`` (the briefing's
reads run in ``asyncio.to_thread`` workers); the "manager" is a
``SimpleNamespace``/fake, NEVER an ``InstanceManager``; no
``POSTGRES_*`` variable is read.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.repositories.memoir.models import (
    MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET,
    MEMOIR_PROPOSALS_ZONE_MAX,
    PROPOSALS_ZONE_HEADER,
    PROPOSAL_LINE_TEMPLATE,
    MemoirEpisode,
    MemoirProposalLedger,
    render_proposal_line,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.memoir_briefing import (
    MEMOIR_AFFORDANCE_LINE,
    MEMOIR_SECTION_CHAR_ABSOLUTE,
    MEMOIR_SECTION_CHAR_CEILING,
    admit_proposal_lines,
    render_briefing_body,
)
from daemon.services.memoir_keepsake_proposal_service import (
    MEMOIR_KEEPSAKE_POIGNANCY_THRESHOLD,
    MEMOIR_PROPOSAL_MIN_BODY_CHARS,
    MEMOIR_PROPOSAL_SOURCE,
    MemoirKeepsakeProposalService,
    PROPOSAL_STATUS_ENQUEUED,
    PROPOSAL_STATUS_FAILED,
    PROPOSAL_STATUS_PENDING,
    PROPOSAL_STATUS_SENT,
)

AGENT = "ari"
FIXED_NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The proposal service (the AST-census legs read it).
SERVICE_PATH = _REPO_ROOT / "daemon" / "services" / "memoir_keepsake_proposal_service.py"

#: The briefing module — the SHIPPED home of the §A6 quote (soft-wrapped).
BRIEFING_PATH = _REPO_ROOT / "daemon" / "services" / "memoir_briefing.py"

#: The lane's own notes — the single-line copy of the SAME quote.
NOTES_PATH = _REPO_ROOT / "docs" / "memoir-v2" / "implementation-notes.md"

#: V2-D 6.5's canonical ladder sentence (§A6). ONE spelling, THREE homes
#: (the briefing docstring, these notes, and this constant) — the
#: whitespace-normalized presence leg below is what keeps them together.
_CANONICAL_LADDER_QUOTE = (
    "fit dynamic → trim dynamic → trim proposals (drop weakest first) → "
    "rung-2 withhold"
)


def _normalize_quote(text: str) -> str:
    """Whitespace-normalized, blockquote-marker-free view of ``text``.

    The shipped copy of the quote is soft-wrapped inside a ``>``
    blockquote, so a line-anchored search would need to know the wrap
    column; normalizing collapses the wrap AND the ``>`` prefixes, which
    is exactly the comparison that survives a re-wrap while still going
    RED on a deletion or a reword.
    """
    stripped = " ".join(
        line.lstrip().lstrip(">").strip() for line in text.splitlines()
    )
    return " ".join(stripped.split())

#: A body long enough to clear the enduring-significance substance floor.
LONG_BODY = (
    "We argued about the font for three hours and settled on the serif; "
    "the decision became canon for every later page we shipped together."
)


@pytest.fixture()
def engine():
    """Throwaway in-memory SQLite, ONE connection shared across threads."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def service(engine):
    return MemoirKeepsakeProposalService(engine, clock=lambda: FIXED_NOW)


def _insert(repo, *, body=LONG_BODY, importance=5, title="A memory", **kwargs):
    return repo.insert_episode(
        agent_id=AGENT, body=body, title=title, importance=importance, **kwargs
    )


def _set_poignancy(engine, episode_id, score):
    with Session(engine) as session:
        row = session.get(MemoirEpisode, episode_id)
        row.poignancy = score
        session.add(row)
        session.commit()


def _set_superseded(engine, episode_id, pointer="other-episode"):
    with Session(engine) as session:
        row = session.get(MemoirEpisode, episode_id)
        row.superseded_by = pointer
        session.add(row)
        session.commit()


def _set_keepsake(engine, episode_id, value=True):
    with Session(engine) as session:
        row = session.get(MemoirEpisode, episode_id)
        row.is_keepsake = bool(value)
        session.add(row)
        session.commit()


def _only_episode(engine):
    with Session(engine) as session:
        return list(session.exec(MemoirEpisode.__table__.select()).all())[0].id


def _ledger_rows(engine):
    with Session(engine) as session:
        return list(session.exec(MemoirProposalLedger.__table__.select()).all())


def _set_status(engine, ledger_id, status):
    with Session(engine) as session:
        row = session.get(MemoirProposalLedger, ledger_id)
        row.notification_status = status
        session.add(row)
        session.commit()


class _MutableClock:
    """A clock the cross-day legs can ADVANCE (day rollover is the point)."""

    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment

    def advance(self, *, days=0, seconds=0):
        self.moment = self.moment + timedelta(days=days, seconds=seconds)
        return self.moment


class FakeManager:
    """The enqueue bridge's destination: a manager-shaped double.

    Records every ``enqueue_message`` call and every ``_loop`` read; the
    loop is a REAL running loop so ``run_coroutine_threadsafe`` works
    when a test drives the production bridge from another thread.
    """

    def __init__(self, loop=None, *, raise_on_enqueue: Exception | None = None):
        self._loop = loop
        self.calls: list[tuple[str, str, str]] = []
        self.raise_on_enqueue = raise_on_enqueue

    async def enqueue_message(self, instance_id, message, source="api", **kwargs):
        if self.raise_on_enqueue is not None:
            raise self.raise_on_enqueue
        self.calls.append((instance_id, message, source))
        return SimpleNamespace(message_id="msg-1", job_id="job-1")


def _running_ari_instance(engine, *, instance_id="ari-inst-1", updated="2026-11-09T00:00:00"):
    from daemon.repositories.instance.models import Instance

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                project_id=None,
                agent_id="ari",
                agent_dir="agents/ari",
                status="running",
                updated_at=updated,
            )
        )
        session.commit()
    return instance_id


# ─────────────────────────────────────────────────────────────────────────
# AC1 — qualification
# ─────────────────────────────────────────────────────────────────────────


class TestQualification:
    def test_an_episode_below_the_poignancy_threshold_is_never_proposed(
        self, engine, repo, service
    ):
        below = MEMOIR_KEEPSAKE_POIGNANCY_THRESHOLD - 1
        _insert(repo, importance=below, title="Ordinary day")
        outcome = service.process_pass(notify=False)
        assert outcome.staged == 0
        assert _ledger_rows(engine) == []

    def test_the_threshold_is_inclusive_and_reads_the_effective_poignancy(
        self, engine, repo, service
    ):
        # importance below the threshold, poignancy ABOVE it — the COALESCE
        # must win (the importance ruling, §3-A2).
        episode_id = _insert(repo, importance=3, title="Refined memory")
        _set_poignancy(engine, episode_id, MEMOIR_KEEPSAKE_POIGNANCY_THRESHOLD)
        outcome = service.process_pass(notify=False)
        assert outcome.staged == 1
        rows = _ledger_rows(engine)
        assert len(rows) == 1
        assert rows[0].episode_id == episode_id
        assert rows[0].notification_status == PROPOSAL_STATUS_PENDING
        assert rows[0].notified_at is None

    def test_an_existing_keepsake_is_never_proposed(self, engine, repo, service):
        repo.insert_episode(
            agent_id=AGENT,
            body=LONG_BODY,
            title="Already sacred",
            importance=10,
            is_keepsake=True,
            pinned_by="empress",
            pinned_reason="the font argument",
        )
        assert service.process_pass(notify=False).staged == 0
        assert _ledger_rows(engine) == []

    def test_a_superseded_episode_is_never_proposed(self, engine, repo, service):
        episode_id = _insert(repo, importance=10, title="Retracted belief")
        _set_superseded(engine, episode_id)
        assert service.process_pass(notify=False).staged == 0
        assert _ledger_rows(engine) == []

    def test_a_terse_episode_fails_the_enduring_heuristic(self, engine, repo, service):
        """A high score on a substance-free row is intensity, not endurance."""
        _insert(repo, importance=10, title="ok", body="ok")
        assert len("ok") < MEMOIR_PROPOSAL_MIN_BODY_CHARS
        outcome = service.process_pass(notify=False)
        assert outcome.staged == 0
        assert outcome.skipped_ineligible >= 1
        assert _ledger_rows(engine) == []

    def test_staging_writes_the_composed_reason_and_the_clock_date(
        self, engine, repo, service
    ):
        episode_id = _insert(repo, importance=9, title="Deploy night")
        service.process_pass(notify=False)
        row = _ledger_rows(engine)[0]
        assert row.episode_id == episode_id
        assert row.proposal_date == FIXED_NOW.date().isoformat()
        assert row.reason.startswith("enduring significance:")
        assert "9/10" in row.reason


# ─────────────────────────────────────────────────────────────────────────
# AC1 — ONCE-EVER-PER-EPISODE (V2-36 / W2)
# ─────────────────────────────────────────────────────────────────────────


class TestOnceEver:
    def test_a_second_pass_never_re_proposes_the_same_episode(
        self, engine, repo, service
    ):
        """The LEDGER PRE-CHECK performs the skip — not the DB constraint.

        The spy on ``stage_candidate`` proves the second pass never even
        ATTEMPTS a staging write: the once-ever skip is a read-side
        decision (V2-36), and the unique constraint is only the racing
        fallback. Without the spy the leg would stay green with the
        pre-check deleted (the INSERT would raise and be swallowed as the
        same skip) — a vacuous pin.
        """
        _insert(repo, importance=9, title="A memory")
        first = service.process_pass(notify=False)
        assert first.staged == 1

        attempts: list[str] = []
        original = service.stage_candidate

        def _spy(episode, **kwargs):
            attempts.append(str(getattr(episode, "id", "")))
            return original(episode, **kwargs)

        service.stage_candidate = _spy
        second = service.process_pass(notify=False)
        assert second.staged == 0
        assert second.skipped_existing == 0
        assert attempts == [], (
            "the ONCE-EVER skip must happen BEFORE any staging attempt — "
            f"stage_candidate was called for {attempts!r}"
        )
        assert service.has_proposal(_only_episode(engine)) is True
        assert len(_ledger_rows(engine)) == 1

    @pytest.mark.parametrize(
        "status",
        [
            PROPOSAL_STATUS_PENDING,
            PROPOSAL_STATUS_ENQUEUED,
            PROPOSAL_STATUS_SENT,
            PROPOSAL_STATUS_FAILED,
        ],
    )
    def test_any_prior_outcome_skips_permanently(self, engine, repo, service, status):
        """ONCE-EVER is outcome-blind: even a FAILED row never re-nags."""
        episode_id = _insert(repo, importance=9, title="A memory")
        service.process_pass(notify=False)
        ledger_id = _ledger_rows(engine)[0].id
        _set_status(engine, ledger_id, status)
        assert service.has_proposal(episode_id) is True
        outcome = service.process_pass(notify=False)
        assert outcome.staged == 0
        assert outcome.skipped_existing == 0
        assert len(_ledger_rows(engine)) == 1

    def test_the_unique_constraint_is_the_racing_guard_and_is_swallowed(
        self, engine, repo, service
    ):
        """A pre-existing row ⇒ the INSERT raises IntegrityError ⇒ SKIP.

        The constraint is ``uq_memoir_proposal_agent_episode`` — the
        durable half of the once-ever rule. The race must degrade to a
        skip, never leak an exception into the reflection pass.
        """
        episode_id = _insert(repo, importance=9, title="A memory")
        with Session(engine) as session:
            session.add(
                MemoirProposalLedger(
                    id="pre-existing",
                    agent_id=AGENT,
                    episode_id=episode_id,
                    proposal_date=FIXED_NOW.date().isoformat(),
                    proposed_at=FIXED_NOW - timedelta(days=30),
                    notification_status=PROPOSAL_STATUS_FAILED,
                    notified_at=None,
                    reason="from a month ago",
                    created_at=FIXED_NOW - timedelta(days=30),
                )
            )
            session.commit()
        # stage_candidate() must return None (skip) — not raise.
        with Session(engine) as session:
            episode = session.get(MemoirEpisode, episode_id)
        assert service.stage_candidate(episode) is None
        assert len(_ledger_rows(engine)) == 1
        outcome = service.process_pass(notify=False)
        assert outcome.staged == 0
        assert outcome.skipped_existing == 0


# ─────────────────────────────────────────────────────────────────────────
# AC1 — the staging WINDOW (W-1): ledgered/superseded rows must not eat
# the window's slots and starve eligible rows below the LIMIT.
# ─────────────────────────────────────────────────────────────────────────


class TestCandidateWindow:
    def test_ledgered_head_of_line_does_not_starve_eligible_rows(
        self, engine, repo, service
    ):
        """W-1 (CRITICAL): 12 ledgered poignancy-9 rows + 2 eligible ⇒
        the 2 stage.

        Before the fix, the ledger exclusion ran READ-SIDE after the
        LIMIT, so the window's top-10 slots were all occupied by
        ledgered rows and the eligible rows below never entered the
        window (pass 2 staged 0). With the exclusion IN THE QUERY,
        the window only ever contains eligible candidates, so the 2
        eligible rows enter and stage.
        """
        # 12 poignancy-9 episodes, ALL ledgered by a single pass.
        for index in range(12):
            _insert(repo, importance=9, title=f"Ledgered {index}")
        first = service.process_pass(notify=False, limit=12)
        assert first.staged == 12, first

        # 2 further eligible episodes arrive AFTER the ledgered block.
        eligible_ids = [
            _insert(repo, importance=8, title=f"Eligible {index}")
            for index in range(2)
        ]

        outcome = service.process_pass(notify=False)
        assert outcome.staged == 2, outcome
        ledger_episode_ids = {row.episode_id for row in _ledger_rows(engine)}
        for episode_id in eligible_ids:
            assert episode_id in ledger_episode_ids

    def test_superseded_head_of_line_does_not_starve_eligible_rows(
        self, engine, repo, service
    ):
        """W-1 same-class completion: superseded rows excluded IN THE
        QUERY (no read-side-after-LIMIT starvation)."""
        for index in range(10):
            episode_id = _insert(repo, importance=10, title=f"Superseded {index}")
            _set_superseded(engine, episode_id)
        eligible_ids = [
            _insert(repo, importance=9, title=f"Eligible {index}")
            for index in range(2)
        ]

        outcome = service.process_pass(notify=False)
        assert outcome.staged == 2, outcome
        ledger_episode_ids = {row.episode_id for row in _ledger_rows(engine)}
        for episode_id in eligible_ids:
            assert episode_id in ledger_episode_ids


# ─────────────────────────────────────────────────────────────────────────
# AC2 — the [PROPOSALS] zone (selection, shape, admission)
# ─────────────────────────────────────────────────────────────────────────


class TestProposalsZoneSelection:
    def test_the_selection_joins_memoir_episodes_for_the_display_columns(
        self, engine, repo, service
    ):
        """The ledger alone has NO title/event_at/poignancy — the JOIN is
        REQUIRED (plan §3.1 / N2)."""
        episode_id = _insert(repo, importance=7, title="Joined title")
        _set_poignancy(engine, episode_id, 9)
        service.process_pass(notify=False)
        rows = service.select_zone_rows()
        assert len(rows) == 1
        assert rows[0].title == "Joined title"
        assert rows[0].event_at is not None
        assert rows[0].effective_poignancy == 9

    def test_only_pending_and_enqueued_rows_render(self, engine, repo, service):
        ids = []
        for index, status in enumerate(
            [
                PROPOSAL_STATUS_PENDING,
                PROPOSAL_STATUS_ENQUEUED,
                PROPOSAL_STATUS_SENT,
                PROPOSAL_STATUS_FAILED,
            ]
        ):
            episode_id = _insert(repo, importance=9, title=f"Episode {index}")
            ids.append(episode_id)
        service.process_pass(notify=False)
        ledger = {row.episode_id: row.id for row in _ledger_rows(engine)}
        _set_status(engine, ledger[ids[0]], PROPOSAL_STATUS_PENDING)
        _set_status(engine, ledger[ids[1]], PROPOSAL_STATUS_ENQUEUED)
        _set_status(engine, ledger[ids[2]], PROPOSAL_STATUS_SENT)
        _set_status(engine, ledger[ids[3]], PROPOSAL_STATUS_FAILED)
        rendered = service.select_zone_rows()
        assert {row.episode_id for row in rendered} == {ids[0], ids[1]}

    def test_ordering_is_strongest_first_then_newest_proposal(
        self, engine, repo, service
    ):
        weak = _insert(repo, importance=8, title="Weak")
        strong = _insert(repo, importance=10, title="Strong")
        service.process_pass(notify=False)
        assert [row.episode_id for row in service.select_zone_rows()] == [
            strong,
            weak,
        ]

    def test_the_zone_caps_at_three_in_the_service_read(self, engine, repo, service):
        for index in range(5):
            _insert(repo, importance=9, title=f"Episode {index}")
        service.process_pass(notify=False)
        assert len(service.select_zone_rows(limit=3)) == 3

    def test_a_pinned_episode_is_not_rendered_in_the_zone(self, engine, repo, service):
        """W-3: a pending proposal whose episode is later pinned is NOT
        rendered; an unpinned pending proposal still is."""
        pinned_episode = _insert(repo, importance=9, title="Pinned memory")
        unpinned_episode = _insert(repo, importance=9, title="Still proposable")
        service.process_pass(notify=False)

        # Pin the first episode AFTER staging — the proposal row stays
        # ``pending``, but the episode is no longer proposable.
        _set_keepsake(engine, pinned_episode, True)

        rendered = {row.episode_id for row in service.select_zone_rows()}
        assert unpinned_episode in rendered
        assert pinned_episode not in rendered


class TestProposalsZoneRender:
    def test_the_line_shape_is_the_plans_shape_verbatim(self):
        line = render_proposal_line(
            title="The lantern walk",
            event_at=datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc),
            poignancy=9,
            reason="enduring significance: effective poignancy 9/10",
        )
        assert line == (
            '- [proposal] "The lantern walk" (event_at=2026-11-09, '
            "poignancy=9): enduring significance: effective poignancy 9/10"
        )
        assert line == PROPOSAL_LINE_TEMPLATE.format(
            title="The lantern walk",
            event_at="2026-11-09",
            poignancy=9,
            reason="enduring significance: effective poignancy 9/10",
        )

    def test_the_header_is_the_plans_header(self):
        assert PROPOSALS_ZONE_HEADER == "### Proposals for Keepsake Review"

    def test_the_zone_renders_under_its_own_header_in_the_briefing(self):
        body = render_briefing_body(
            persona_body="I am Ari.",
            keepsakes=[],
            episodes=[],
            proposal_lines=[
                '- [proposal] "The lantern walk" (event_at=2026-11-09, '
                "poignancy=9): enduring significance"
            ],
        )
        assert body is not None
        assert PROPOSALS_ZONE_HEADER in body
        assert body.rstrip().endswith(MEMOIR_AFFORDANCE_LINE)
        # The zone sits BEFORE the closing affordance and AFTER the stable
        # text (it is its own section, never bolted into the stable zone).
        assert body.index(PROPOSALS_ZONE_HEADER) < body.index(MEMOIR_AFFORDANCE_LINE)

    def test_the_zone_is_measured_inclusively_of_the_header_wrapper(self):
        long_reason = "r" * 1100
        line = render_proposal_line(
            title="T", event_at=None, poignancy=9, reason=long_reason
        )
        kept = admit_proposal_lines([line])
        assert kept == []
        shipped = len(PROPOSALS_ZONE_HEADER) + 1 + len(line) + 1
        assert shipped > MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET

    def test_the_zone_never_exceeds_its_char_budget_measured_on_shipped_bytes(self):
        lines = [
            render_proposal_line(
                title=f"Title {index}", event_at=None, poignancy=9,
                reason="r" * 200,
            )
            for index in range(3)
        ]
        kept = admit_proposal_lines(lines)
        shipped = (
            len(PROPOSALS_ZONE_HEADER) + 1
            + sum(len(line) + 1 for line in kept)
        )
        assert shipped <= MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET
        assert len(kept) <= MEMOIR_PROPOSALS_ZONE_MAX

    def test_the_structural_cap_is_three_lines(self):
        lines = [
            render_proposal_line(title="T", event_at=None, poignancy=9, reason="r")
            for _ in range(6)
        ]
        assert len(admit_proposal_lines(lines)) == MEMOIR_PROPOSALS_ZONE_MAX == 3


# ─────────────────────────────────────────────────────────────────────────
# AC2 — the §A6 admission ladder (verbatim rung order) + reachability
# ─────────────────────────────────────────────────────────────────────────


class TestAdmissionLadder:
    """The ladder is V2-D 6.5's verbatim quote:

    > fit dynamic → trim dynamic → trim proposals (drop weakest first) →
    > rung-2 withhold

    Each leg below is one step of that ladder (the CANONICAL labels, no
    invented numbering); the AC6 mutation leg (b) swaps the two trims and
    turns these RED.
    """

    @staticmethod
    def _dynamic(count=5):
        return [
            SimpleNamespace(
                title=f"Episode {index}",
                body="a recent memory body",
                event_at=FIXED_NOW,
                is_keepsake=False,
            )
            for index in range(count)
        ]

    @staticmethod
    def _proposals(count=3):
        return [
            render_proposal_line(
                title=f"Proposal {index}",
                event_at=None,
                poignancy=10 - index,
                reason="enduring significance: effective poignancy",
            )
            for index in range(count)
        ]

    def test_fit_dynamic_keeps_every_proposal(self):
        body = render_briefing_body(
            persona_body="I am Ari.",
            keepsakes=[],
            episodes=self._dynamic(),
            proposal_lines=self._proposals(),
        )
        assert body is not None
        assert "## Recent episodes" in body
        assert PROPOSALS_ZONE_HEADER in body
        assert body.count("[proposal]") == 3

    _PERSONA_HEADER = "## Persona"

    @classmethod
    def _extras_len(cls, episodes, proposals):
        """Bytes the non-stable sections add, measured with a TINY persona."""
        tiny = "abc"
        body = render_briefing_body(
            persona_body=tiny, keepsakes=[], episodes=episodes,
            proposal_lines=proposals,
        )
        assert body is not None
        return len(body) - (len(cls._PERSONA_HEADER) + 1 + len(tiny))

    @classmethod
    def _persona_for_overflow(cls, episodes, proposals, overflow):
        """The persona length that makes the UNTRIMMED body overflow by N."""
        extras = cls._extras_len(episodes, proposals)
        target = MEMOIR_SECTION_CHAR_CEILING + overflow
        length = target - extras - (len(cls._PERSONA_HEADER) + 1)
        assert length > 0
        return "p" * length

    @classmethod
    def _line_cost(cls, line):
        return len(line) + 1

    def test_trim_dynamic_before_any_proposal_is_dropped(self):
        """Under total pressure the DYNAMIC lines go FIRST — the proposals
        zone is the second loser, never the first (the ladder's order)."""
        episodes = self._dynamic(5)
        proposals = self._proposals(3)
        from daemon.services.memoir_briefing import render_dynamic_line

        costs = [
            self._line_cost(render_dynamic_line(episode)) for episode in episodes
        ]
        # Overflow smaller than ONE dynamic line ⇒ dropping a single (oldest)
        # dynamic line restores the ceiling; no proposal may be touched.
        persona = self._persona_for_overflow(episodes, proposals, costs[-1] - 5)
        body = render_briefing_body(
            persona_body=persona,
            keepsakes=[],
            episodes=episodes,
            proposal_lines=proposals,
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING
        # Exactly ONE dynamic line was trimmed (the OLDEST — the list tail,
        # i.e. the LAST fixture entry: the fixture is newest-first) ...
        assert body.count("[2026-11-09] Episode") == len(episodes) - 1
        assert f"Episode {len(episodes) - 1}:" not in body
        # ... and EVERY proposal still ships (the proposals trim never fired).
        assert body.count("[proposal]") == len(proposals)

    def test_trim_proposals_drops_the_weakest_first(self):
        """When the dynamic zone is already empty, the WEAKEST proposal
        (the tail of the strongest-first list) is the next to go."""
        proposals = self._proposals(3)
        costs = [self._line_cost(line) for line in proposals]
        # No episodes: the only movable step left is the proposals trim. Overflow
        # exceeds the cost of the two WEAKEST lines but not all three, so
        # exactly the strongest one survives.
        overflow = costs[-1] + costs[-2] - 10
        persona = self._persona_for_overflow([], proposals, overflow)
        body = render_briefing_body(
            persona_body=persona,
            keepsakes=[],
            episodes=[],
            proposal_lines=proposals,
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING
        shipped = [line for line in proposals if line in body]
        assert len(shipped) == 1
        # The survivor is the STRONGEST (head), never the weakest (tail).
        assert shipped == proposals[:1]

    def test_withhold_is_reachable_only_by_stable_zone_overflow(self):
        """> 48,000 chars of STABLE text ⇒ the whole block is withheld; the
        SAME total reached via stable + dynamic + proposals does NOT
        withhold (it trims instead)."""
        huge_persona = "p" * (MEMOIR_SECTION_CHAR_ABSOLUTE + 10)
        assert (
            render_briefing_body(
                persona_body=huge_persona, keepsakes=[], episodes=[],
                proposal_lines=self._proposals(),
            )
            is None
        )
        # The same magnitude spread across zones: never withheld.
        body = render_briefing_body(
            persona_body="p" * (MEMOIR_SECTION_CHAR_CEILING - 5000),
            keepsakes=[],
            episodes=self._dynamic(60),
            proposal_lines=self._proposals(),
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING

    def test_the_total_based_trim_is_reachable_within_1000_chars_of_the_ceiling(self):
        """V2-D 6.5's reachability analysis, PINNED.

        Because the proposals zone is admitted at ≤ 1,000 chars, the
        total-based trim can fire only when ``stable + dynamic`` lands
        within the zone's bound of 24,000. This leg constructs exactly
        that window and proves the trim DOES fire there — and (the
        companion leg above) that a smaller stable zone leaves the whole
        zone intact.
        """
        proposals = self._proposals(3)
        zone_chars = len(PROPOSALS_ZONE_HEADER) + 1 + sum(
            len(line) + 1 for line in proposals
        )
        assert zone_chars <= MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET
        # stable chosen so stable + zone + separators overflows by a little.
        persona = "p" * (MEMOIR_SECTION_CHAR_CEILING - zone_chars + 40)
        body = render_briefing_body(
            persona_body=persona,
            keepsakes=[],
            episodes=[],
            proposal_lines=proposals,
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING
        assert body.count("[proposal]") < len(proposals)
        # ... and 200 chars of headroom keeps the full zone.
        comfortable = "p" * (MEMOIR_SECTION_CHAR_CEILING - zone_chars - 200)
        body2 = render_briefing_body(
            persona_body=comfortable,
            keepsakes=[],
            episodes=[],
            proposal_lines=proposals,
        )
        assert body2 is not None
        assert body2.count("[proposal]") == len(proposals)

    def test_the_canonical_quote_is_present_verbatim_in_the_shipped_source(self):
        """V2-D 6.5's quote must SURVIVE a refactor (the documentation pin).

        The BEHAVIOR of the ladder is pinned by the legs above; this leg
        pins the DOCUMENTATION CLAIM itself. The source copy soft-wraps
        across two blockquote lines, so the comparison is
        whitespace-normalized with the blockquote markers stripped — a
        line-anchored pin would pass on a deleted quote's neighbours and
        fail on the real one.
        """
        assert _CANONICAL_LADDER_QUOTE in _normalize_quote(BRIEFING_PATH.read_text())
        assert _CANONICAL_LADDER_QUOTE in _normalize_quote(NOTES_PATH.read_text())


# ─────────────────────────────────────────────────────────────────────────
# AC3 — the durable bridge (source, consumption, failures, 1/day, restart)
# ─────────────────────────────────────────────────────────────────────────


class TestEnqueueBridge:
    def test_an_accepted_enqueue_consumes_the_slot(self, engine, repo):
        _insert(repo, importance=9, title="A memory")
        _running_ari_instance(engine, instance_id="ari-inst-1")
        manager = FakeManager()
        service = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW, manager=manager,
            enqueue_bridge=lambda iid, msg, src: manager.calls.append((iid, msg, src)),
        )
        outcome = service.process_pass()
        assert outcome.enqueued is True
        assert outcome.enqueue_failure is None
        assert manager.calls and manager.calls[0][0] == "ari-inst-1"
        # THE SOURCE STRING IS THE PLAN'S (V2-D 6.2 verbatim).
        assert manager.calls[0][2] == MEMOIR_PROPOSAL_SOURCE == "system:memoir-proposal"
        assert "has reflected and proposed 1 new keepsake" in manager.calls[0][1]
        row = _ledger_rows(engine)[0]
        assert row.notification_status == PROPOSAL_STATUS_ENQUEUED
        assert row.notified_at is not None

    # ── the DATE ROLLOVER (F1: the gate is keyed on the CONSUMPTION day) ──

    @staticmethod
    def _rollover_setup(engine, repo, sent):
        """Stage TWO proposals on D with NO running ari instance.

        This is the ORDINARY cross-day path (not a contrived one): the
        reflection pass stages the row, the bridge finds no instance, and
        the row stays ``pending`` for a later day. Staging stamps
        ``proposal_date = D`` and the notification lands on D+1.
        """
        strong = _insert(repo, importance=10, title="The serif decision")
        weak = _insert(repo, importance=9, title="The lantern walk")
        clock = _MutableClock(FIXED_NOW)
        service = MemoirKeepsakeProposalService(
            engine, clock=clock,
            enqueue_bridge=lambda iid, msg, src: sent.append(msg),
        )
        staged = service.process_pass()
        assert staged.staged == 2
        assert staged.enqueued is False
        assert staged.enqueue_failure == "no_active_instance"
        rows = {row.episode_id: row for row in _ledger_rows(engine)}
        # BOTH rows are stamped with the STAGING day, and neither consumed
        # a slot (V2-23 → V2-40: consumption requires an accepted enqueue).
        assert rows[strong].proposal_date == FIXED_NOW.date().isoformat()
        assert rows[weak].proposal_date == FIXED_NOW.date().isoformat()
        assert rows[strong].notified_at is None
        return strong, weak, clock, service, rows

    def test_a_cross_day_enqueue_announces_the_episode_exactly_once(
        self, engine, repo
    ):
        """(i) Staged D, notified D+1 ⇒ ONE announcement, silence on D+2+.

        The gate must count the CONSUMPTION day: the row's
        ``proposal_date`` is D while its notification happens on D+1, so a
        staging-date gate would see zero on every following day and
        re-announce the same episode forever (the measured 6-day loop).
        """
        sent: list[str] = []
        strong, weak, clock, service, _ = self._rollover_setup(engine, repo, sent)
        _running_ari_instance(engine)

        clock.advance(days=1)  # D+1
        first = service.process_pass()
        assert first.enqueued is True, first.enqueue_failure
        assert len(sent) == 1 and "The serif decision" in sent[0]
        rows = {row.episode_id: row for row in _ledger_rows(engine)}
        assert rows[strong].notification_status == PROPOSAL_STATUS_ENQUEUED
        assert rows[weak].notification_status == PROPOSAL_STATUS_PENDING
        # The CONSUMPTION day is D+1 — that is the day whose slot is spent.
        assert service.count_notified_today(
            day=clock.moment.date().isoformat()
        ) == 1
        assert service.count_notified_today(
            day=FIXED_NOW.date().isoformat()
        ) == 0

        for _ in range(3):  # D+2, D+3, D+4
            clock.advance(days=1)
            service.process_pass()
        assert sum("The serif decision" in message for message in sent) == 1, (
            "the SAME episode must never be re-announced after a date "
            f"rollover (messages: {sent!r})"
        )

    def test_the_still_pending_proposal_eventually_lands(self, engine, repo):
        """(ii) The weaker row is NOT starved by the enqueued one.

        ``select_zone_rows(limit=1)`` orders by poignancy, so an
        ``enqueued`` row inside the NOTIFY target outranks every later
        proposal FOREVER — the enqueued row must be excluded from the
        TARGET (it stays in the briefing ZONE) and the next day's slot
        must go to the strongest ``pending`` row.
        """
        sent: list[str] = []
        strong, weak, clock, service, _ = self._rollover_setup(engine, repo, sent)
        _running_ari_instance(engine)

        clock.advance(days=1)   # D+1 — the strong row is announced
        service.process_pass()
        clock.advance(days=1)   # D+2 — the WEAK row must be the candidate
        second = service.process_pass()
        assert second.enqueued is True, second.enqueue_failure
        assert len(sent) == 2
        assert "The lantern walk" in sent[1]
        rows = {row.episode_id: row for row in _ledger_rows(engine)}
        assert rows[weak].notification_status == PROPOSAL_STATUS_ENQUEUED
        # ... and the briefing ZONE still surfaces BOTH (the target
        # narrowing is the bridge's only difference).
        assert {row.episode_id for row in service.select_zone_rows()} == {
            strong,
            weak,
        }
        clock.advance(days=1)   # D+3 — nothing pending left
        third = service.process_pass()
        assert third.enqueued is False
        assert third.enqueue_failure == "no_proposal"

    def test_no_calendar_day_receives_two_notifications(self, engine, repo):
        """(iii) The gate counts the day the slot was SPENT, so a
        cross-day row consumes D+1's slot and a SECOND pass on D+1 is
        silent — even though the row's ``proposal_date`` is D."""
        sent: list[str] = []
        _strong, weak, clock, service, _ = self._rollover_setup(engine, repo, sent)
        _running_ari_instance(engine)

        clock.advance(days=1)  # D+1
        assert service.process_pass().enqueued is True
        assert len(sent) == 1
        # A second tick the SAME day: the slot is spent.
        again = service.process_pass()
        assert again.enqueued is False
        assert again.enqueue_failure == "daily_slot_consumed"
        assert len(sent) == 1, f"two notifications on one day: {sent!r}"
        rows = {row.episode_id: row for row in _ledger_rows(engine)}
        assert rows[weak].notification_status == PROPOSAL_STATUS_PENDING
        assert service.count_notified_today(
            day=clock.moment.date().isoformat()
        ) == 1

    def test_a_failed_enqueue_leaves_the_row_pending(self, engine, repo):
        _insert(repo, importance=9, title="A memory")
        _running_ari_instance(engine)
        service = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW,
            enqueue_bridge=lambda *a: (_ for _ in ()).throw(RuntimeError("loop down")),
        )
        outcome = service.process_pass()
        assert outcome.enqueued is False
        assert outcome.enqueue_failure == "enqueue_failed:RuntimeError"
        row = _ledger_rows(engine)[0]
        # THE SLOT IS NOT CONSUMED (V2-23 → V2-40's surviving invariant).
        assert row.notification_status == PROPOSAL_STATUS_PENDING
        assert row.notified_at is None
        assert service.count_notified_today() == 0

    def test_no_active_instance_keeps_the_row_pending(self, engine, repo):
        _insert(repo, importance=9, title="A memory")
        manager = FakeManager()
        service = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW,
            enqueue_bridge=lambda iid, msg, src: manager.calls.append((iid, msg, src)),
        )
        outcome = service.process_pass()
        assert outcome.enqueued is False
        assert outcome.enqueue_failure == "no_active_instance"
        assert manager.calls == []
        assert _ledger_rows(engine)[0].notification_status == PROPOSAL_STATUS_PENDING

    def test_the_daily_gate_counts_the_consumption_not_the_pending_row(
        self, engine, repo
    ):
        """A ``pending`` row consumes NOTHING; an accepted enqueue does."""
        first = _insert(repo, importance=9, title="First memory")
        _running_ari_instance(engine)
        sent: list[str] = []
        service = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW,
            enqueue_bridge=lambda iid, msg, src: sent.append(msg),
        )
        assert service.process_pass().enqueued is True
        assert service.count_notified_today() == 1
        # A SECOND pass the same day: staged rows keep accumulating in the
        # briefing, but the DAY's notification slot is spent.
        _insert(repo, importance=10, title="Second memory")
        second = service.process_pass()
        assert second.staged == 1
        assert second.enqueued is False
        assert second.enqueue_failure == "daily_slot_consumed"
        assert len(sent) == 1
        assert service.count_notified_today() == 1

    def test_the_daily_gate_survives_a_restart(self, engine, repo):
        """A BRAND-NEW service over the same engine is still silent."""
        _insert(repo, importance=9, title="A memory")
        _running_ari_instance(engine)
        first_service = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW, enqueue_bridge=lambda *a: None,
        )
        assert first_service.process_pass().enqueued is True

        _insert(repo, importance=10, title="Later memory")
        restarted = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW, enqueue_bridge=lambda *a: None,
        )
        outcome = restarted.process_pass()
        assert outcome.enqueued is False
        assert outcome.enqueue_failure == "daily_slot_consumed"
        assert len(_ledger_rows(engine)) == 2  # staged, but not notified

    def test_the_bridge_reaches_the_manager_loop_from_a_worker_thread(self, engine, repo):
        """The PRODUCTION bridge shape, exercised for real.

        The service runs on the reflection worker's THREAD, so
        ``_enqueue`` must marshal onto the manager's loop via
        ``asyncio.run_coroutine_threadsafe`` — the RDRS lane-6 precedent.
        """
        _insert(repo, importance=9, title="A memory")
        _running_ari_instance(engine, instance_id="ari-inst-loop")

        async def _run():
            manager = FakeManager(loop=asyncio.get_running_loop())
            service = MemoirKeepsakeProposalService(
                engine, clock=lambda: FIXED_NOW, manager=manager,
            )
            return await asyncio.to_thread(service.process_pass)

        outcome = asyncio.run(_run())
        assert outcome.enqueued is True, outcome.enqueue_failure
        assert _ledger_rows(engine)[0].notification_status == PROPOSAL_STATUS_ENQUEUED


    def test_a_pinned_episode_is_not_announced_by_the_bridge(self, engine, repo):
        """W-3: a pending proposal whose episode is later pinned is NOT
        announced; an unpinned pending proposal still is."""
        pinned_episode = _insert(repo, importance=9, title="Pinned memory")
        unpinned_episode = _insert(repo, importance=9, title="Still proposable")
        # Stage with no running instance so both rows stay ``pending``.
        service = MemoirKeepsakeProposalService(engine, clock=lambda: FIXED_NOW)
        outcome = service.process_pass(notify=False)
        assert outcome.staged == 2

        # Pin one episode AFTER staging.
        _set_keepsake(engine, pinned_episode, True)

        # With a running ari instance, the bridge announces the strongest
        # PENDING proposal that is still proposable — the unpinned one.
        sent: list[str] = []
        _running_ari_instance(engine)
        bridge_service = MemoirKeepsakeProposalService(
            engine, clock=lambda: FIXED_NOW,
            enqueue_bridge=lambda iid, msg, src: sent.append(msg),
        )
        enqueued, failure = bridge_service.notify_once()
        assert enqueued is True, failure
        assert len(sent) == 1
        assert "Still proposable" in sent[0]
        assert "Pinned memory" not in sent[0]

        # The pinned episode's row remains pending (never announced).
        rows = {row.episode_id: row for row in _ledger_rows(engine)}
        assert rows[pinned_episode].notification_status == PROPOSAL_STATUS_PENDING
        assert rows[unpinned_episode].notification_status == PROPOSAL_STATUS_ENQUEUED


# ─────────────────────────────────────────────────────────────────────────
# The reflection-pass integration (T6.1: the service is a PASS consumer)
# ─────────────────────────────────────────────────────────────────────────


class TestReflectionPassIntegration:
    def _reflection_service(self, engine, proposals):
        from daemon.repositories.memoir.reflection_repository import (
            MemoirReflectionRepository,
        )
        from daemon.repositories.memoir.syntheses_repository import (
            MemoirSynthesesRepository,
        )
        from daemon.services.memoir_reflection_service import (
            MemoirReflectionService,
            ReflectionSettings,
        )

        return MemoirReflectionService(
            reflection_repository=MemoirReflectionRepository(engine),
            syntheses_repository=MemoirSynthesesRepository(engine),
            keepsake_proposal_service=proposals,
            settings=ReflectionSettings(agent_id=AGENT),
            llm_caller=lambda prompt: '{"episodes": [], "syntheses": []}',
        )

    def test_an_in_day_pass_stages_proposals_from_the_pass_episodes(
        self, engine, repo, service
    ):
        episode_id = _insert(repo, importance=9, title="A memory")
        reflection = self._reflection_service(engine, service)
        outcome = reflection.run_in_day_pass(FIXED_NOW)
        assert outcome.ran is True
        rows = _ledger_rows(engine)
        assert len(rows) == 1
        assert rows[0].episode_id == episode_id

    def test_a_failing_proposal_service_never_fails_the_pass(self, engine, repo):
        class Boom:
            def process_pass(self, **kwargs):
                raise RuntimeError("proposal lane exploded")

        _insert(repo, importance=9, title="A memory")
        reflection = self._reflection_service(engine, Boom())
        outcome = reflection.run_in_day_pass(FIXED_NOW)
        assert outcome.ran is True  # the pass completed
        assert _ledger_rows(engine) == []

    def test_a_service_without_the_proposal_lane_is_unchanged(self, engine, repo):
        """The default (None) keeps every existing construction byte-safe."""
        _insert(repo, importance=9, title="A memory")
        reflection = self._reflection_service(engine, None)
        outcome = reflection.run_in_day_pass(FIXED_NOW)
        assert outcome.ran is True
        assert _ledger_rows(engine) == []


# ─────────────────────────────────────────────────────────────────────────
# V2-D 6.3 / R2 — dual governance: the service holds NO pin authority
# ─────────────────────────────────────────────────────────────────────────


class TestNoPinAuthority:
    def test_the_module_holds_no_pin_writer_reference(self):
        """V2-D 6.3 / R2 — pinning is the Emperor/Empress path only.

        The AST census (docstrings legitimately NAME the forbidden
        symbols; the pin is about REFERENCES) asserts the service never
        imports or calls the pin writer.
        """
        tree = ast.parse(SERVICE_PATH.read_text())
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    referenced.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    referenced.add(alias.name)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)
        assert not (referenced & {"set_pin", "clear_pin", "memoir_pin"})

    def test_processing_never_marks_an_episode_as_a_keepsake(self, engine, repo, service):
        _insert(repo, importance=10, title="A memory")
        service.process_pass()
        with Session(engine) as session:
            rows = list(session.exec(MemoirEpisode.__table__.select()).all())
        assert all(row.is_keepsake is False for row in rows)
        assert all(row.pinned_by is None for row in rows)

    def test_the_service_makes_no_llm_calls(self):
        """Phase 6 is heuristic + render only (plan §1/§4)."""
        tree = ast.parse(SERVICE_PATH.read_text())
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    referenced.add(alias.name)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)
        assert not (
            referenced
            & {"llm_caller", "llm_config", "invoke_raw_with_failover",
               "_house_llm_call", "httpx"}
        )
