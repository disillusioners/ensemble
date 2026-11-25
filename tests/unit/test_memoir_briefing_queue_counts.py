"""The briefing's TWO approval-queue lines (m1) — counts, zones, budgets.

WHAT THIS SUITE PINS

* the repository's CHEAP count — ``MemoirPersonaRepository.count_pending``
  is a ``SELECT COUNT(*)`` with the SAME predicate ``list_pending``
  selects with (asserted on the EMITTED SQL, not on a docstring);
* ALWAYS-PRESENT-BUT-SILENT-WHEN-ZERO: at zero counts the rendered body
  is BYTE-IDENTICAL to the body rendered with no count parameters at
  all — the existing pins (24k/48k boundaries, zone layout, trim
  ladder, proposals-zone admission) keep measuring the same bytes;
* the ZONE each line belongs to — the persona line inside ``## Persona``,
  the proposal line inside the proposals zone — and the SURFACE each
  line names. The two surfaces differ: the proposal queue is the
  ``memoir_review`` tool (asserted against the registry); the persona
  queue is named with the ``memoir_persona_ratify`` verb as a STRING —
  that verb's EXISTENCE is the sibling verb module's pin, so this suite
  asserts the line's wording, never the verb's registration;
* the proposals-zone MEASUREMENT: the count line is zone OVERHEAD,
  measured FIRST inside the ≤1,000-char bound, and it still renders when
  NO proposal line fits;
* the pinned LADDER is untouched: fit dynamic → trim dynamic → trim
  proposals → withhold, and the 24,000-char ceiling still binds with both
  count lines present;
* the build path: ONE guarded COUNT per queue, a failed count degrades to
  "no line" (never an aborted build), and ``list_pending`` is never
  called (the count must not materialize the lineage).

FENCE: throwaway in-memory SQLite via ``StaticPool`` (the build's reads
run in ``asyncio.to_thread`` workers); the "manager" is a
``SimpleNamespace``, never an ``InstanceManager``; no ``POSTGRES_*``
variable is read.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.memoir.models import (
    MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET,
    MEMOIR_PROPOSALS_ZONE_MAX,
    PIN_ACTORS,
    PROPOSALS_ZONE_HEADER,
)
from daemon.repositories.memoir.repository import (
    MemoirPersonaRepository,
    MemoirRepository,
)
from daemon.services import memoir_briefing
from daemon.services.memoir_briefing import (
    MEMOIR_AGENT_ID,
    MEMOIR_AFFORDANCE_LINE,
    MEMOIR_PERSONA_MISSING_PREFIX,
    MEMOIR_PERSONA_PENDING_LINE,
    MEMOIR_PROPOSAL_PENDING_LINE,
    MEMOIR_SECTION_CHAR_CEILING,
    admit_proposal_lines,
    render_briefing_body,
    render_queue_count_line,
)
from daemon.services.memoir_keepsake_proposal_service import (
    MemoirKeepsakeProposalService,
)
from daemon.tools.memoir import MEMOIR_TOOL_NAMES

FIXED_NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

#: The rendered persona-queue line for count 2 — the LITERAL text asserted
#: by the legs below (the agent-side surface, named as a STRING: the
#: verb's existence is the sibling verb module's pin, never this one).
PERSONA_LINE_2 = (
    "- 2 persona versions pending approval — act with "
    "memoir_persona_ratify(version_id)."
)

#: The rendered proposal-queue line for count 3 (the tool surface).
PROPOSAL_LINE_3 = (
    "- 3 keepsake proposals pending review — act with "
    "memoir_review(title, decision, actor)."
)

#: A body long enough to clear the proposal service's substance floor.
LONG_BODY = (
    "We argued about the font for three hours and settled on the serif; "
    "the decision became canon for every later page we shipped together."
)


# ── harness ──────────────────────────────────────────────────────────────


@pytest.fixture()
def engine():
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
def persona_repo(engine):
    return MemoirPersonaRepository(engine)


@pytest.fixture()
def fake_manager(engine):
    """The consumer shape WITHOUT an InstanceManager — never construct one."""
    return SimpleNamespace(_engine=engine, config=None)


def _pending_persona(persona_repo, *, agent_id: str = MEMOIR_AGENT_ID) -> str:
    """Create + submit ONE persona version ⇒ status ``pending_approval``."""
    draft = persona_repo.create_draft(agent_id, "who we are, draft")
    persona_repo.submit(draft.id)
    return draft.id


def _stage_one_proposal(repo, engine, title: str = "The font argument") -> None:
    """Stage ONE proposal through the REAL qualification pass (no notify)."""
    repo.insert_episode(
        agent_id=MEMOIR_AGENT_ID,
        body=LONG_BODY,
        title=title,
        importance=9,
        event_at=FIXED_NOW,
        created_at=FIXED_NOW,
    )
    MemoirKeepsakeProposalService(engine, clock=lambda: FIXED_NOW).process_pass(
        notify=False
    )


def _proposal_lines(count: int, *, reason_chars: int = 0) -> list[str]:
    """Deterministic proposal-zone line bodies (strongest-first)."""
    return [
        f"- [proposal] Proposal {index} (poignancy=10) reason "
        + "r" * reason_chars
        for index in range(count)
    ]


def _zone_chars(lines) -> int:
    """The SHIPPED proposals-zone chars (header wrapper + every line)."""
    return len(PROPOSALS_ZONE_HEADER) + 1 + sum(len(line) + 1 for line in lines)


# ── the repository count (cheap, same predicate) ─────────────────────────


class TestPersonaCountPending:
    def test_counts_only_pending_approval_rows_for_the_agent(
        self, persona_repo
    ):
        _pending_persona(persona_repo)
        _pending_persona(persona_repo)
        # a DRAFT (not submitted) never counts
        persona_repo.create_draft(MEMOIR_AGENT_ID, "still a draft")
        # ... and neither does another agent's pending row
        _pending_persona(persona_repo, agent_id="someone-else")

        assert persona_repo.count_pending(MEMOIR_AGENT_ID) == 2
        assert persona_repo.count_pending("someone-else") == 1
        # ``None`` counts across every agent — the ``list_pending`` parity
        assert persona_repo.count_pending() == 3

    def test_the_count_agrees_with_list_pending(self, persona_repo):
        _pending_persona(persona_repo)
        _pending_persona(persona_repo, agent_id="someone-else")
        assert persona_repo.count_pending(MEMOIR_AGENT_ID) == len(
            persona_repo.list_pending(MEMOIR_AGENT_ID)
        )

    def test_the_module_function_delegates_to_the_same_count(
        self, repo, persona_repo
    ):
        """ONE predicate home: the module function delegates, never re-derives."""
        from daemon.repositories.memoir.repository import count_pending_persona

        _pending_persona(persona_repo)
        assert count_pending_persona(repo.engine, MEMOIR_AGENT_ID) == 1
        assert count_pending_persona(repo.engine, MEMOIR_AGENT_ID) == (
            persona_repo.count_pending(MEMOIR_AGENT_ID)
        )
        assert count_pending_persona(repo.engine) == 1

    def test_zero_when_the_queue_is_empty(self, persona_repo):
        assert persona_repo.count_pending(MEMOIR_AGENT_ID) == 0

    def test_it_is_a_count_query_not_a_row_materialization(self, engine):
        """THE CHEAPNESS PROOF: the emitted SQL is a COUNT, not a row read."""
        _pending_persona(MemoirPersonaRepository(engine))
        statements: list[str] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()).lower())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            total = MemoirPersonaRepository(engine).count_pending(MEMOIR_AGENT_ID)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        assert total == 1
        count_sql = [s for s in statements if "count(" in s]
        assert count_sql, statements
        assert all("memoir_persona_versions" in s for s in count_sql)
        assert not any("select memoir_persona_versions." in s for s in statements)


# ── the line renderer ────────────────────────────────────────────────────


class TestQueueCountLine:
    def test_zero_and_negative_and_junk_render_nothing(self):
        for value in (0, -3, None, "junk"):
            assert render_queue_count_line(MEMOIR_PERSONA_PENDING_LINE, value) == ""
            assert render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, value) == ""

    def test_singular_and_plural(self):
        assert render_queue_count_line(MEMOIR_PERSONA_PENDING_LINE, 1) == (
            "- 1 persona version pending approval — act with "
            "memoir_persona_ratify(version_id)."
        )
        assert render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, 1) == (
            "- 1 keepsake proposal pending review — act with "
            "memoir_review(title, decision, actor)."
        )
        assert render_queue_count_line(MEMOIR_PERSONA_PENDING_LINE, 2) == (
            PERSONA_LINE_2
        )
        assert render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, 3) == (
            PROPOSAL_LINE_3
        )

    def test_each_line_is_one_line(self):
        for template in (MEMOIR_PERSONA_PENDING_LINE, MEMOIR_PROPOSAL_PENDING_LINE):
            assert "\n" not in render_queue_count_line(template, 4)


# ── the zero tax + the zone placement ────────────────────────────────────


class TestRenderZones:
    def test_zero_counts_are_byte_identical_to_no_parameters(self):
        """THE ZERO-TAX PIN: an empty queue changes NOTHING."""
        kwargs = dict(
            persona_body="I am the persona body.",
            keepsakes=[],
            episodes=[],
            provenance_class="direct",
            proposal_lines=_proposal_lines(2),
        )
        bare = render_briefing_body(**kwargs)
        explicit = render_briefing_body(
            **kwargs, persona_pending_count=0, proposal_pending_count=0
        )
        assert bare is not None and explicit is not None
        assert bare == explicit
        assert "pending approval" not in bare
        assert "pending review" not in bare
        # the zone itself is unchanged at zero (the count line is extra)
        assert PROPOSALS_ZONE_HEADER in bare
        assert bare.count("[proposal]") == 2

    def test_the_persona_line_rides_the_persona_zone(self):
        body = render_briefing_body(
            persona_body="I am the persona body.",
            keepsakes=[],
            episodes=[],
            persona_pending_count=2,
        )
        assert body is not None
        assert PERSONA_LINE_2 in body
        persona_zone = body.split("## Persona", 1)[1]
        assert persona_zone.startswith("\nI am the persona body.\n")
        assert PERSONA_LINE_2 in persona_zone
        # the line sits BEFORE the closing affordance / any later zone
        assert body.index(PERSONA_LINE_2) < body.index(MEMOIR_AFFORDANCE_LINE)

    def test_the_persona_line_renders_under_the_missing_persona_prefix(self):
        body = render_briefing_body(
            persona_body=None,
            keepsakes=[],
            episodes=[],
            persona_pending_count=1,
        )
        assert body is not None
        assert body.startswith(MEMOIR_PERSONA_MISSING_PREFIX + "\n")
        assert "1 persona version pending approval" in body

    def test_the_proposal_line_rides_the_proposals_zone(self):
        body = render_briefing_body(
            persona_body="I am the persona body.",
            keepsakes=[],
            episodes=[],
            proposal_lines=_proposal_lines(3),
            proposal_pending_count=3,
        )
        assert body is not None
        assert PROPOSAL_LINE_3 in body
        zone = body.split(PROPOSALS_ZONE_HEADER, 1)[1]
        # the count line is the FIRST line of the zone (it measures first)
        assert zone.splitlines()[1] == PROPOSAL_LINE_3
        # and it is NOT in the persona zone
        assert PROPOSAL_LINE_3 not in body.split(PROPOSALS_ZONE_HEADER)[0]

    def test_the_proposal_line_renders_with_no_renderable_proposal_line(self):
        """A count with nothing to render is still the actionable fact."""
        body = render_briefing_body(
            persona_body="I am the persona body.",
            keepsakes=[],
            episodes=[],
            proposal_lines=[],
            proposal_pending_count=1,
        )
        assert body is not None
        assert PROPOSALS_ZONE_HEADER in body
        assert "1 keepsake proposal pending review" in body
        assert "[proposal]" not in body
        assert body.rstrip().endswith(MEMOIR_AFFORDANCE_LINE)


# ── the proposals-zone budget (measured, not appended) ───────────────────


class TestProposalsZoneBudget:
    def test_the_count_line_is_measured_inside_the_zone_budget(self):
        lines = _proposal_lines(3, reason_chars=260)
        prefix = render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, 3)
        kept = admit_proposal_lines(lines, prefix=prefix)
        assert prefix in kept
        assert len(kept) - 1 <= MEMOIR_PROPOSALS_ZONE_MAX
        assert len(kept) < 4, "the prefix must push proposal lines out"
        assert _zone_chars(kept) <= MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET

    def test_the_three_line_cap_bounds_proposal_lines_only(self):
        lines = _proposal_lines(6)
        prefix = render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, 6)
        kept = admit_proposal_lines(lines, prefix=prefix)
        assert kept[0] == prefix
        assert len(kept) - 1 == MEMOIR_PROPOSALS_ZONE_MAX
        assert _zone_chars(kept) <= MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET

    def test_a_prefix_that_cannot_fit_refuses_the_whole_zone(self):
        prefix = "- " + "x" * MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET
        assert admit_proposal_lines(_proposal_lines(2), prefix=prefix) == []

    def test_no_prefix_keeps_the_existing_admission_bytes(self):
        """The pre-existing admission (no prefix) is unchanged."""
        lines = _proposal_lines(6)
        assert admit_proposal_lines(lines) == lines[:MEMOIR_PROPOSALS_ZONE_MAX]

    def test_the_rendered_zone_never_exceeds_its_budget_with_the_count_line(self):
        body = render_briefing_body(
            persona_body="I am the persona body.",
            keepsakes=[],
            episodes=[],
            proposal_lines=_proposal_lines(3, reason_chars=260),
            proposal_pending_count=3,
        )
        assert body is not None
        zone = body.split(PROPOSALS_ZONE_HEADER, 1)[1]
        shipped = len(PROPOSALS_ZONE_HEADER) + len(zone.split(MEMOIR_AFFORDANCE_LINE)[0])
        assert shipped <= MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET
        assert "3 keepsake proposals pending review" in zone


# ── the backstop arithmetic + the pinned trim order ──────────────────────


class TestBackstopsAndLadder:
    def test_the_persona_line_counts_into_the_stable_zone_measurement(self):
        """The line ships INSIDE the stable zone, so the 24k rung MEASURES it.

        The persona body alone sits just under the ceiling; adding the
        count line pushes the SHIPPED stable zone over it ⇒ the overage
        disclosure fires and the dynamic zone is dropped. The disclosure's
        ``chars`` equals the escaped stable zone INCLUDING the line.
        """
        # sized off the LINE's own length so the leg holds for any wording
        persona = "p" * (MEMOIR_SECTION_CHAR_CEILING - len(PERSONA_LINE_2) - 5)
        without = render_briefing_body(
            persona_body=persona, keepsakes=[], episodes=[], provenance_class=""
        )
        assert without is not None
        assert "pin admission exceeded its budget" not in without

        episodes = [
            SimpleNamespace(
                title="recent", body="dynamic body", event_at=FIXED_NOW,
                is_keepsake=False,
            )
        ]
        with_line = render_briefing_body(
            persona_body=persona,
            keepsakes=[],
            episodes=episodes,
            provenance_class="",
            persona_pending_count=2,
        )
        assert with_line is not None
        assert PERSONA_LINE_2 in with_line
        assert "pin admission exceeded its budget" in with_line
        assert "dynamic body" not in with_line

    def test_the_24k_ceiling_still_binds_with_both_counts(self):
        body = render_briefing_body(
            persona_body="p" * 23000,
            keepsakes=[],
            episodes=[
                SimpleNamespace(
                    title=f"Episode {index}",
                    body="a recent memory body",
                    event_at=FIXED_NOW,
                    is_keepsake=False,
                )
                for index in range(5)
            ],
            proposal_lines=_proposal_lines(3),
            persona_pending_count=2,
            proposal_pending_count=3,
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING

    def test_the_dynamic_zone_is_still_the_first_loser(self):
        """The ladder's order: dynamic lines go before any proposal line."""
        episodes = [
            SimpleNamespace(
                title=f"Episode {index}",
                body="a recent memory body",
                event_at=FIXED_NOW,
                is_keepsake=False,
            )
            for index in range(5)
        ]
        proposals = _proposal_lines(3)
        prefix = render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, 3)
        # persona sized so the body overflows by less than one dynamic line
        probe = render_briefing_body(
            persona_body="p" * 100,
            keepsakes=[],
            episodes=episodes,
            proposal_lines=proposals,
            proposal_pending_count=3,
        )
        assert probe is not None
        extras = len(probe) - (len("## Persona") + 1 + 100)
        dynamic_cost = len(episodes[0].body) + 20
        persona = "p" * (
            MEMOIR_SECTION_CHAR_CEILING + dynamic_cost - 5 - extras - len("## Persona") - 1
        )
        body = render_briefing_body(
            persona_body=persona,
            keepsakes=[],
            episodes=episodes,
            proposal_lines=proposals,
            proposal_pending_count=3,
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING
        assert body.count("[proposal]") == len(proposals)
        assert prefix in body
        assert body.count("[2026-11-18] Episode") == len(episodes) - 1

    def test_the_count_prefix_is_the_last_proposal_rung_to_fall(self):
        """Under total pressure the proposal lines go BEFORE the count line."""
        proposals = _proposal_lines(3)
        prefix = render_queue_count_line(MEMOIR_PROPOSAL_PENDING_LINE, 3)
        header = "## Persona"
        # Measure the non-stable extras with a TINY persona (the ladder
        # tests' own technique): separators + zone + affordance.
        probe = render_briefing_body(
            persona_body="abc",
            keepsakes=[],
            episodes=[],
            proposal_lines=proposals,
            proposal_pending_count=3,
        )
        assert probe is not None
        extras = len(probe) - (len(header) + 1 + 3)
        # Overflow of LESS than the weakest proposal line: ONE proposal
        # line must be dropped and the count prefix must stay.
        overflow = len(proposals[-1]) + 1 - 5
        persona = "p" * (
            MEMOIR_SECTION_CHAR_CEILING + overflow - extras - (len(header) + 1)
        )
        body = render_briefing_body(
            persona_body=persona,
            keepsakes=[],
            episodes=[],
            proposal_lines=proposals,
            proposal_pending_count=3,
        )
        assert body is not None
        assert len(body) <= MEMOIR_SECTION_CHAR_CEILING
        assert prefix in body
        assert body.count("[proposal]") == len(proposals) - 1


# ── the build path: guarded COUNTs, degrade, no row materialization ──────


class TestBuildPath:
    def test_the_build_renders_both_queue_lines(
        self, repo, persona_repo, fake_manager
    ):
        repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
        _pending_persona(persona_repo)
        _pending_persona(persona_repo)
        _stage_one_proposal(repo, fake_manager._engine)

        message = asyncio.run(
            memoir_briefing._build_memoir_briefing_message(
                MEMOIR_AGENT_ID, "inst-1", fake_manager
            )
        )
        assert message is not None
        body = message.content
        assert PERSONA_LINE_2 in body
        assert "1 keepsake proposal pending review" in body
        assert "memoir_review(title, decision, actor)" in body
        assert MEMOIR_AFFORDANCE_LINE in body

    def test_the_build_is_silent_when_both_queues_are_empty(
        self, repo, fake_manager
    ):
        repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
        message = asyncio.run(
            memoir_briefing._build_memoir_briefing_message(
                MEMOIR_AGENT_ID, "inst-1", fake_manager
            )
        )
        assert message is not None
        assert "pending approval" not in message.content
        assert "pending review" not in message.content

    def test_a_failed_persona_count_degrades_to_no_line(
        self, engine, repo, persona_repo, fake_manager, monkeypatch
    ):
        repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
        _pending_persona(persona_repo)
        _stage_one_proposal(repo, engine)

        def _boom(self, agent_id=None):
            raise RuntimeError("count read failed")

        monkeypatch.setattr(MemoirPersonaRepository, "count_pending", _boom)
        message = asyncio.run(
            memoir_briefing._build_memoir_briefing_message(
                MEMOIR_AGENT_ID, "inst-1", fake_manager
            )
        )
        assert message is not None
        # the failed count costs ITS line only — the other queue + the
        # persona/keepsake/dynamic zones still ship
        assert "pending approval" not in message.content
        assert "1 keepsake proposal pending review" in message.content
        assert "## Persona" in message.content

    def test_a_failed_proposal_count_degrades_to_no_line(
        self, engine, repo, persona_repo, fake_manager, monkeypatch
    ):
        repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
        _pending_persona(persona_repo)
        _stage_one_proposal(repo, engine)

        def _boom(self, *, agent_id=None):
            raise RuntimeError("count read failed")

        monkeypatch.setattr(
            MemoirKeepsakeProposalService, "count_pending_reviews", _boom
        )
        message = asyncio.run(
            memoir_briefing._build_memoir_briefing_message(
                MEMOIR_AGENT_ID, "inst-1", fake_manager
            )
        )
        assert message is not None
        assert "pending review" not in message.content
        assert "1 persona version pending approval" in message.content
        # the proposal LINE still renders (its own read is a separate domain)
        assert "[proposal]" in message.content

    def test_the_build_never_materializes_the_persona_lineage(
        self, engine, repo, persona_repo, fake_manager, monkeypatch
    ):
        repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
        _pending_persona(persona_repo)

        def _forbidden(self, agent_id=None):
            raise AssertionError("list_pending must not be called by the briefing")

        monkeypatch.setattr(MemoirPersonaRepository, "list_pending", _forbidden)
        message = asyncio.run(
            memoir_briefing._build_memoir_briefing_message(
                MEMOIR_AGENT_ID, "inst-1", fake_manager
            )
        )
        assert message is not None
        assert "1 persona version pending approval" in message.content

    def test_the_proposal_count_comes_from_the_service_count(
        self, repo, persona_repo, fake_manager, monkeypatch
    ):
        """The line reports the SERVICE's count, not a zone-length guess."""
        repo.seed_persona_if_absent(MEMOIR_AGENT_ID)

        def _seven(self, *, agent_id=None):
            return 7

        monkeypatch.setattr(
            MemoirKeepsakeProposalService, "count_pending_reviews", _seven
        )
        message = asyncio.run(
            memoir_briefing._build_memoir_briefing_message(
                MEMOIR_AGENT_ID, "inst-1", fake_manager
            )
        )
        assert message is not None
        assert "7 keepsake proposals pending review" in message.content


# ── the surfaces the lines NAME are real ─────────────────────────────────


class TestNamedSurfaces:
    def test_the_proposal_line_names_the_registered_tool(self):
        assert "memoir_review(title, decision, actor)" in PROPOSAL_LINE_3
        assert "memoir_review" in MEMOIR_TOOL_NAMES

    def test_the_persona_line_names_the_ratify_verb_as_a_string(self):
        """The persona queue's AGENT-side surface is the ratify verb.

        The verb NAME is carried as a string in the LINE; its existence is
        the sibling verb module's pin, so this suite asserts the wording
        and NEVER the verb's registration (a registry pin here would go
        stale the moment that lane lands). The line also claims nothing
        about whether the call succeeds — the refusal itself carries the
        human-lane detail — so no session/operator wording is allowed.
        """
        assert "memoir_persona_ratify(version_id)" in PERSONA_LINE_2
        assert "pending approval" in PERSONA_LINE_2
        for stale_claim in ("operator session", "session lane",
                            "POST /memoir", "403"):
            assert stale_claim not in PERSONA_LINE_2

    def test_neither_line_carries_an_actor_token(self):
        """The queue lines carry NO lore actor literal — derived at runtime.

        The actor vocabulary is read from the model (ONE home); the lines
        must not spell any of it, and neither must this suite.
        """
        for line in (PERSONA_LINE_2, PROPOSAL_LINE_3):
            for actor in PIN_ACTORS:
                assert actor not in line
