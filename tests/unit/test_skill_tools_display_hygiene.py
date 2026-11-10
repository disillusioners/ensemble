"""E3 — skill-tools DISPLAY HYGIENE (board `wi-d340f16c`, plan §9 E3 / §18 rows 19-20).

The board item contains **EXACTLY THREE** bullets, all DISPLAY-ONLY
(*"skill_view consumption is NOT affected"*). This module pins one node
per bullet and nothing else:

* **E3.1** — ``skill_fix`` echoed a bare 8-hex token (``skill_id[:8]``).
  The 8-hex form is NOT ``skill_view``-consumable, so an agent that
  copied it back into ``skill_view`` failed (the wi-76d89e11
  verbatim-id contract). The echo now carries the **full id**.
* **E3.2** — ``skill_feedback`` echoed the same bare 8-hex token. Same
  treatment.
* **E3.3** — ``_render_pf_view`` printed the internal ``pf-<sha16>``
  pseudo-id on the agent-facing ``- id:`` line. It now prints the
  CONSUMABLE **address** ``pf:<ns>:<name>``, and the pseudo-id stays
  available ADDITIVELY under ``- pseudo_id:`` (the internal key
  continuity the injection path and the embedding rows depend on).

WHAT IS **NOT** PINNED HERE (deliberately): the ``:1213`` refusal-gap
extension, the ``to_summary()`` dead surface and the P7
shared-refusal-helper re-derive. Those three are **leader-judgment
cleanups, NOT-from-board** — out of this lane's scope.

DISPLAY-ONLY means the consumable surfaces must be UNAFFECTED: the
existing ``skill_id_verbatim_contract`` pack (135/135) is the
regression net and must stay green.
"""

from __future__ import annotations

import os
import re

import pytest
from sqlmodel import SQLModel, create_engine
from unittest.mock import AsyncMock, MagicMock

import daemon.repositories.skill.models  # noqa: F401 — legacy models registered
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillFileRepository,
    ProjectSkillUsageRecordRepository,
)
from daemon.services import skill_capture_observability as observability
from daemon.services.project_skill_source.service import (
    ProjectSkillSourceService,
)
from daemon.services.skill_job_dispatcher import SkillJobDispatcher
from daemon.tools.skill_tools import create_skill_tools
from tests.helpers.project_skill_source_fixtures import (
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    write_skill,
)

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
INSTANCE_ID = "inst-display-hygiene"

#: A realistic DB skill id — a full uuid4 (the only form ``skill_view``
#: resolves exactly, per the wi-76d89e11 contract).
FULL_SKILL_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"

#: The 8-hex prefix the pre-fix code echoed. NOTE it is a SUBSTRING of
#: the full id, so every assertion below matches on the SURROUNDING
#: shape (a quoted id, or ``skill <id>...``), never on mere presence.
BARE_TOKEN = FULL_SKILL_ID[:8]

#: An 8-hex token that is NOT part of the full id — used as a positive
#: control so the "bare token absent" assertions cannot pass vacuously.
UNRELATED_BARE_TOKEN = "deadbeef"


def _bare_token_shape(token: str) -> str:
    """The exact quoted shape the pre-fix echo produced."""
    return f"'{token}'"


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/display.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _observability_sandbox(tmp_path):
    """Point the observability sink at a SCRATCH dir + reset the counters.

    L7 (the E1 burst-guard lesson): without this fixture the
    ``skill_fix`` dispatch-fault path writes its observability rows
    into the CHECKOUT's real ``data/`` tree — the tree is gitignored,
    so the leak is invisible to ``git status`` and survives every run.
    """
    observability.configure_sink(tmp_path)
    observability.reset_counters()
    observability.set_anomaly_reporter(None)
    try:
        yield tmp_path
    finally:
        observability.configure_sink(None)
        observability.reset_counters()
        observability.set_anomaly_reporter(None)


@pytest.fixture
def canon(tmp_path):
    """A minimal project-file canon with ONE skill."""
    root = str(tmp_path / "proj-root")
    os.makedirs(root, exist_ok=True)
    write_skill(
        root, "tester", "beta-skill",
        body_text="Beta body.\n",
        manifest={
            "schema_version": 1, "name": "beta-skill",
            "body": "beta-skill.md", "version": "2.0.0",
            "description": "Beta skill.", "category": "workflow",
            "tiers": [1], "loads": [], "assets": [],
        },
    )
    return root


def _make_manager(engine, canon, *, dispatcher_missing: bool):
    """The pf-surface manager shape (real file source, mocked DB services)."""
    file_repo = ProjectSkillFileRepository(engine)
    usage_repo = ProjectSkillUsageRecordRepository(engine)
    fs = ProjectSkillSourceService(
        instance_repository=FakeInstanceRepository(
            {INSTANCE_ID: FakeInstance(INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID)}
        ),
        project_repository=FakeProjectRepository(
            [FakeProject(PROJECT_ID, main_directory=canon)]
        ),
        file_repository=file_repo,
    )
    manager = MagicMock()
    manager._skill_search_service = MagicMock()
    manager._skill_store_service = MagicMock()
    manager._skill_metrics_service = MagicMock()
    manager._skill_metrics_service.record_feedback = AsyncMock(return_value=True)
    manager._skill_job_dispatcher = None if dispatcher_missing else MagicMock()
    manager._project_skill_source = fs
    manager._project_skill_file_repository = file_repo
    manager._project_skill_usage_repository = usage_repo
    manager._instance_repository = FakeInstanceRepository(
        {INSTANCE_ID: FakeInstance(INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID)}
    )
    return manager, fs


def _tools(manager):
    return {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}


#: The EXACT sentence the blanket catch used to render — and which is
#: UNTRUE for a designed refusal (no job exists, nothing will pick it
#: up). Asserted as a FULL PHRASE, never as a fragment: the envelope
#: echoes the caller's text, so a short needle would match for the wrong
#: reason.
FALSE_DURABILITY_SENTENCE = (
    "request is still recorded above for the next skill-keeper run"
)


async def _drive_skill_fix(raiser) -> str:
    """Drive ``skill_fix`` with a dispatcher whose ``dispatch_fix`` raises.

    The dispatcher is INJECTED (``manager._skill_job_dispatcher``) — never
    an env switch: the lane is simulated at the object boundary, exactly
    as the B2 pins do.
    """
    manager = MagicMock()
    manager._skill_search_service = MagicMock()
    manager._skill_store_service = MagicMock()
    manager._skill_metrics_service = MagicMock()
    manager._project_skill_source = None
    manager._project_skill_file_repository = None
    manager._project_skill_usage_repository = None
    dispatcher = MagicMock()
    dispatcher.dispatch_fix = MagicMock(side_effect=raiser)
    manager._skill_job_dispatcher = dispatcher
    tools = _tools(manager)
    return await tools["skill_fix"].ainvoke(
        {
            "skill_id": FULL_SKILL_ID,
            "issue_description": "stale regex",
        }
    )


# =============================================================================
# E3.1 — skill_fix echoes the FULL id
# =============================================================================


class TestE31SkillFixEchoesTheFullId:
    """Board bullet 1 — the fix-request confirmation line."""

    @pytest.mark.asyncio
    async def test_the_echo_carries_the_full_uuid4(self, engine, canon):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_fix"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "issue_description": "stale regex"}
        )

        assert f"skill '{FULL_SKILL_ID}'" in result, (
            "the fix echo must carry the FULL id (skill_view-consumable "
            f"verbatim): {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_echo_never_carries_the_bare_8_hex_token(self, engine, canon):
        """The 8-hex shape is NOT ``skill_view``-consumable."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_fix"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "issue_description": "stale regex"}
        )

        assert _bare_token_shape(BARE_TOKEN) not in result, (
            "the fix echo must not truncate the id to 8 hex chars: "
            f"{result!r}"
        )

    def test_the_assertion_can_see_a_bare_token(self):
        """Vacuity control for the two pins above.

        The 'bare token absent' assertion matches the QUOTED shape; prove
        that shape is detectable at all, so the pin is not silently
        always-true.
        """
        hypothetical_pre_fix = (
            f"\U0001f4dd Skill fix request recorded for skill '{BARE_TOKEN}'."
        )
        assert _bare_token_shape(BARE_TOKEN) in hypothetical_pre_fix
        assert _bare_token_shape(UNRELATED_BARE_TOKEN) not in hypothetical_pre_fix


# =============================================================================
# E3.2 — skill_feedback echoes the FULL id
# =============================================================================


class TestE32SkillFeedbackEchoesTheFullId:
    """Board bullet 2 — the feedback confirmation lines."""

    @pytest.mark.asyncio
    async def test_the_recorded_line_carries_the_full_uuid4(self, engine, canon):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_feedback"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "applied": True, "note": "helped"}
        )

        assert f"Feedback recorded for skill {FULL_SKILL_ID}..." in result, (
            f"the feedback echo must carry the FULL id: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_recorded_line_never_carries_the_bare_8_hex_token(
        self, engine, canon
    ):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        result = await tools["skill_feedback"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "applied": True, "note": "helped"}
        )

        assert f"skill {BARE_TOKEN}..." not in result, (
            f"the feedback echo must not truncate the id: {result!r}"
        )


# =============================================================================
# E3.3 — _render_pf_view echoes the ADDRESS, pseudo_id additive
# =============================================================================


class TestE33PfViewEchoesTheAddress:
    """Board bullet 3 — the file-skill view header."""

    @pytest.mark.asyncio
    async def test_the_id_line_carries_the_address(self, engine, canon):
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        rendered = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )

        assert "- id: pf:tester:beta-skill" in rendered, (
            f"the agent-facing `- id:` line must carry the ADDRESS: {rendered!r}"
        )

    @pytest.mark.asyncio
    async def test_the_pseudo_id_stays_available_additively(self, engine, canon):
        """The internal key stays reachable — deletion is NOT the fix."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        rendered = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )

        assert re.search(r"- pseudo_id: pf-[0-9a-f]{16}\b", rendered), (
            "the internal pseudo-id must stay available under the "
            f"ADDITIVE `- pseudo_id:` line: {rendered!r}"
        )

    @pytest.mark.asyncio
    async def test_the_id_line_never_carries_the_pseudo_id(self, engine, canon):
        """The pseudo-id must not LEAK on the agent-facing `- id:` line."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        rendered = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )

        id_lines = [ln for ln in rendered.splitlines() if ln.startswith("- id: ")]
        assert id_lines, f"the header must carry an `- id:` line: {rendered!r}"
        for line in id_lines:
            assert not re.search(r"pf-[0-9a-f]{16}", line), (
                f"the `- id:` line must not leak the pseudo-id: {line!r}"
            )

    @pytest.mark.asyncio
    async def test_the_emitted_id_is_skill_view_consumable_verbatim(
        self, engine, canon
    ):
        """DISPLAY-ONLY means the emitted id must still ROUND-TRIP.

        The address printed on the ``- id:`` line is fed straight back
        into ``skill_view`` — it must resolve to the same skill (the
        wi-76d89e11 verbatim contract, which is what a truncated or
        internal-only id breaks).
        """
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)

        first = await tools["skill_view"].ainvoke({"skill_id": "pf:tester:beta-skill"})
        emitted = next(
            ln[len("- id: "):].strip()
            for ln in first.splitlines()
            if ln.startswith("- id: ")
        )
        again = await tools["skill_view"].ainvoke({"skill_id": emitted})
        assert again.splitlines()[0] == "# beta-skill", (
            f"the emitted id {emitted!r} must be verbatim-consumable: {again!r}"
        )


# =============================================================================
# H2 — the designed burst-cap refusal must NOT be reported as a fault
# =============================================================================
#
# The tidier's HIGH finding (H2): ``skill_fix``'s blanket ``except
# Exception`` swallowed ``SkillFixBurstCapRefused`` (a DESIGNED refusal:
# the evolution lane is ON and the durable re-fix burst CAP refused the
# admission) and rendered the generic note — including the UNTRUE
# "request is still recorded above for the next skill-keeper run": the
# cap REFUSED the dispatch, so NO job exists and nothing will pick it
# up. An agent told "your request is recorded" believes a fix is
# pending when nothing was recorded.
#
# The REST route already handles the same exception structurally (HTTP
# 429, M2-pinned) and reads the stable token off the refusal class
# (``e.reason``) instead of hard-coding a literal. The agent-facing
# envelope mirrors that vocabulary.


class TestH2BurstCapRefusalIsTruthful:
    @pytest.mark.asyncio
    async def test_the_false_durability_sentence_is_absent(self):
        """Pin 1 — the UNTRUE sentence must never reach the agent.

        F-G2 removed that sentence from the GENERIC branch as well (the
        generic branch now renders a DERIVED outcome). So the
        "sentence absent" assertion alone can NO LONGER discriminate the
        H2 plant — the pin must also assert that the DESIGNED refusal
        still renders its OWN first-class shape and is NOT degraded into
        the generic fault note.
        """
        from daemon.services.skill_job_dispatcher import (
            SkillFixBurstCapRefused,
        )

        result = await _drive_skill_fix(
            SkillFixBurstCapRefused("refused: burst cap — 3 FIX admissions")
        )

        assert FALSE_DURABILITY_SENTENCE not in result, (
            "the burst-cap refusal must not claim the request is still "
            f"recorded for a later run: {result!r}"
        )
        assert "burst cap" in result, (
            "the designed refusal must keep its own shape — the cap "
            f"refusal must NOT be degraded into the generic fault note: {result!r}"
        )
        assert "dispatcher raised during dispatch" not in result, (
            "the designed refusal must NOT be rendered as a generic "
            f"dispatcher fault: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_truthful_markers_reach_the_agent(self):
        """Pin 2 — cap-tripped + no job + nothing picked up + the token."""
        from daemon.services.skill_job_dispatcher import (
            SkillFixBurstCapRefused,
        )

        result = await _drive_skill_fix(
            SkillFixBurstCapRefused("refused: burst cap — 3 FIX admissions")
        )

        assert "NOT dispatched" in result, (
            f"the refusal must use the lane's first-class refusal shape: {result!r}"
        )
        assert "burst cap" in result, (
            f"the refusal must name the cap that tripped: {result!r}"
        )
        assert "NO skill-keeper job was created" in result, (
            f"the refusal must state that no job exists: {result!r}"
        )
        assert "NOTHING will be picked up" in result, (
            f"the refusal must state that nothing will pick it up: {result!r}"
        )
        # The stable reason token — the SAME one the REST route returns
        # (read off the refusal class, never hard-coded).
        assert SkillFixBurstCapRefused.reason in result, (
            f"the refusal must carry the reason token: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_refusal_claims_no_success(self):
        """Pin 3 — the lane's success-blocklist discipline."""
        from daemon.services.skill_job_dispatcher import (
            SkillFixBurstCapRefused,
        )

        result = await _drive_skill_fix(
            SkillFixBurstCapRefused("refused: burst cap — 3 FIX admissions")
        )

        for forbidden in (
            "✅",
            "Dispatched:",
            "was admitted",
            "Evolution complete",
            "successfully",
            "has been evolved",
        ):
            assert forbidden not in result, (
                f"the refusal must not claim success: {forbidden!r}"
            )

    @pytest.mark.asyncio
    async def test_a_generic_dispatcher_error_still_renders_the_generic_note(self):
        """Pin 4 — the non-vacuity twin.

        The structural catch must NOT swallow every dispatcher error: a
        plain fault still renders the generic envelope, which NAMES the
        fault. The wording is now DERIVED from the persistence outcome
        (F-G2) — the constant "still recorded" sentence is GONE, because
        it was empirically FALSE (0 rows persist when the dispatch
        raises before the insert), so this twin asserts the DERIVED
        wording instead.
        """
        result = await _drive_skill_fix(RuntimeError("dispatcher exploded"))

        assert "dispatcher raised during dispatch" in result, (
            f"a generic dispatcher fault must keep the generic note: {result!r}"
        )
        assert "dispatcher exploded" in result, (
            f"the generic note must carry the fault text: {result!r}"
        )
        assert FALSE_DURABILITY_SENTENCE not in result, (
            "the generic path must NOT claim the request is recorded — "
            f"the outcome is derived, not asserted: {result!r}"
        )
        assert "NOT recorded" in result, (
            f"the derived wording must say the request was not recorded: {result!r}"
        )
        assert "NOTHING will be picked up" in result, (
            f"the derived wording must say nothing will pick it up: {result!r}"
        )


# =============================================================================
# F-G2 — the generic-fault envelope is DERIVED from the real outcome
# =============================================================================
#
# The tester gate (2026-11-10, F-G2) proved the GENERIC-fault branch of
# ``skill_fix`` renders "request is still recorded above for the next
# skill-keeper run" while EMPIRICALLY 0 rows persist when the dispatch
# raises BEFORE the insert (no job row, no counter, no anomaly). The
# agent was told its work was recorded; nothing was — the Oct-22-class
# failure this workstream exists to kill.
#
# F-G2.1 — the truthfulness comes from the REAL persistence outcome:
#   * no job persisted ⇒ "NOT recorded … NOTHING will be picked up";
#   * a job persisted ⇒ "was recorded" AND the job id is NAMED.
# F-G2.2 — a DROPPED request is VISIBLE: an anomaly row + a refusal
#   counter under its OWN reason token + a WARNING log + a sink record.


class _FaultyJobService:
    """A job service double that FAULTS at the persistence seam.

    The REAL ``SkillJobDispatcher`` sits on top of it, so the
    idempotency key, the metadata shape and the direction text are the
    PRODUCTION ones — only the persistence boundary is doubled, to
    inject the fault at the exact seam the tester gate exercised.

    The key is persisted VERBATIM (no ``#<uuid8>`` suffix): that is
    the PRODUCTION shape of a FRESH admission — the suffix is only
    added when a TERMINAL job already holds the key
    (``daemon/services/job_queue_service.py:1596-1608``).
    """

    def __init__(self, repo: JobRepository, mode: str) -> None:
        self._repo = repo
        self._mode = mode
        self.enqueued: list = []

    @property
    def repository(self) -> JobRepository:
        """The PUBLIC repository door the read-back resolves through."""
        return self._repo

    async def enqueue(self, **kwargs):
        if self._mode == "pre":
            # The fault BEFORE the insert: nothing is persisted.
            raise RuntimeError("job store unavailable before the insert")
        metadata = kwargs.get("metadata") or {}
        job = self._repo.create(
            agent_id=kwargs.get("agent_id", "skill-keeper"),
            agent_dir=kwargs.get("agent_dir", "agents/skill-keeper"),
            message=kwargs.get("message", ""),
            source=kwargs.get("source", "skill_evolution"),
            project_id=kwargs.get("project_id"),
            priority=kwargs.get("priority", 5),
            job_metadata=metadata,
            queue_id=kwargs.get("queue_id"),
            idempotency_key=kwargs.get("idempotency_key"),
            job_type=kwargs.get("job_type", "task"),
            instance_id=kwargs.get("instance_id"),
        )
        self.enqueued.append(job)
        if self._mode == "post":
            # The fault AFTER the insert: the row IS persisted.
            raise RuntimeError("dispatch bus unavailable after the insert")
        return job


class _StubQueueRepo:
    """A queue repo whose parallel-queue lookup finds nothing (FIFO)."""

    def get_by_name(self, project_id, queue_name):
        return None


class _RecordingReporter:
    """Records the daemon-side anomaly calls (the human-visible signal)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"accepted": True}


class TestDG1HeaderIsDurabilityNeutral:
    """D-G1 — the header carries NO durability claim on ANY branch.

    The F-G2 derivation introduced an internal CONTRADICTION: the
    header still said "📝 Skill fix request recorded for skill ...",
    while the drop branch's body said "the request was NOT recorded".
    An agent that reads (or truncates to) the first line believed a
    record existed; the body said the opposite. The header is now
    durability-neutral — EVERY branch's verdict lives in its own body
    line — and this pin keeps the contradiction class
    un-reintroducible across all three outcomes (drop, absent
    dispatcher, success).
    """

    #: The exact old header phrase — asserted ABSENT from the whole
    #: output wherever the branch verdict says otherwise.
    OLD_HEADER_PHRASE = "Skill fix request recorded"

    #: The neutral header prefix — the SAME line on every branch.
    NEUTRAL_HEADER_PREFIX = "📝 Skill fix request for skill"

    @pytest.mark.asyncio
    async def test_the_drop_case_never_carries_a_recorded_header(
        self, engine, canon
    ):
        """The drop: the header must NOT claim a record anywhere."""
        result, enqueued = await _drive_faulty_skill_fix(
            engine, canon, mode="pre"
        )

        assert enqueued == [], "the drop must have persisted no row"
        assert self.OLD_HEADER_PHRASE not in result, (
            "the drop must not carry the old 'recorded' header — the body "
            f"says the opposite: {result!r}"
        )
        assert result.splitlines()[0].startswith(self.NEUTRAL_HEADER_PREFIX), (
            f"the header must be the neutral line: {result.splitlines()[0]!r}"
        )
        # the verdict lives in the BODY
        assert "was NOT recorded" in result

    @pytest.mark.asyncio
    async def test_the_absent_dispatcher_case_never_carries_a_recorded_header(
        self, engine, canon
    ):
        """No dispatcher: the same neutral header, no durability claim."""
        manager, _fs = _make_manager(engine, canon, dispatcher_missing=True)
        tools = _tools(manager)
        result = await tools["skill_fix"].ainvoke(
            {"skill_id": FULL_SKILL_ID, "issue_description": "stale regex"}
        )

        assert self.OLD_HEADER_PHRASE not in result, (
            "the absent-dispatcher case must not carry the old 'recorded' "
            f"header — nothing was dispatched: {result!r}"
        )
        assert result.splitlines()[0].startswith(self.NEUTRAL_HEADER_PREFIX), (
            f"the header must be the neutral line: {result.splitlines()[0]!r}"
        )
        assert (
            "NOTHING was dispatched and no skill-keeper job was created"
            in result
        ), f"the verdict must live in the body: {result!r}"

    @pytest.mark.asyncio
    async def test_the_success_case_header_is_the_same_neutral_line(
        self, engine, canon
    ):
        """Success: the SAME neutral header; the verdict is in the body."""
        result, enqueued = await _drive_faulty_skill_fix(
            engine, canon, mode="ok"
        )

        assert len(enqueued) == 1, (
            f"the success case must have admitted the job: {enqueued!r}"
        )
        assert self.OLD_HEADER_PHRASE not in result, (
            f"the success header must be the SAME neutral line: {result!r}"
        )
        assert result.splitlines()[0].startswith(self.NEUTRAL_HEADER_PREFIX), (
            f"the header must be the neutral line: {result.splitlines()[0]!r}"
        )
        assert "was admitted" in result, (
            f"the verdict must live in the body: {result!r}"
        )


class _FaultCarryingThePersistedJobId(RuntimeError):
    """A dispatcher fault that CARRIES the persisted job id.

    The carrier contract the dispatcher would satisfy with ONE line
    (attach the committed job id to the exception it raises). The tool
    prefers it over a DB read.
    """

    persisted_job_id: str = ""


def _make_fault_manager(engine, canon, *, mode):
    """A manager double wired with the REAL dispatcher over a faulty store."""
    repo = JobRepository(engine)
    job_service = _FaultyJobService(repo, mode)
    dispatcher = SkillJobDispatcher(
        job_service=job_service,
        queue_repo=_StubQueueRepo(),
        evolution_lane_enabled=True,
        # The RAM cooldown and the durable cap are DISABLED here on
        # purpose: these pins exercise the FAULT path alone.
        fix_cooldown_seconds=0,
        fix_burst_cap=0,
    )
    manager, _fs = _make_manager(engine, canon, dispatcher_missing=False)
    manager._skill_job_dispatcher = dispatcher
    # The PUBLIC manager accessor the read-back resolves through.
    manager.job_queue_service = job_service
    return manager, job_service, repo


async def _drive_faulty_skill_fix(engine, canon, mode) -> tuple[str, list]:
    """Drive ``skill_fix`` through the REAL dispatcher with a fault."""
    manager, job_service, _repo = _make_fault_manager(
        engine, canon, mode=mode
    )
    tools = _tools(manager)
    result = await tools["skill_fix"].ainvoke(
        {
            "skill_id": FULL_SKILL_ID,
            "issue_description": "stale regex",
        }
    )
    return result, job_service.enqueued


class TestFG2GenericFaultIsDerivedFromTheOutcome:
    """F-G2.1 — the generic envelope must not lie about the record."""

    @pytest.mark.asyncio
    async def test_the_pre_insert_fault_never_claims_a_record(self, engine, canon):
        """Pin 1 — the dispatch raised BEFORE the insert: NOTHING exists."""
        result, enqueued = await _drive_faulty_skill_fix(
            engine, canon, mode="pre"
        )

        assert enqueued == [], (
            "the pre-insert fault must persist NO row — the pin would be "
            f"vacuous otherwise: {enqueued!r}"
        )
        assert FALSE_DURABILITY_SENTENCE not in result, (
            "the generic fault must not claim the request is recorded for "
            f"a later run: {result!r}"
        )
        assert "NOT recorded" in result, (
            f"the derived wording must say the request was NOT recorded: {result!r}"
        )
        assert "NOTHING will be picked up" in result, (
            f"the derived wording must say nothing will pick it up: {result!r}"
        )
        for forbidden in (
            "Dispatched:",
            "was admitted",
            "was persisted for this request",
            "the skill-keeper agent will pick it up",
        ):
            assert forbidden not in result, (
                f"the drop must not claim a job exists: {forbidden!r} in {result!r}"
            )

    @pytest.mark.asyncio
    async def test_the_post_insert_fault_names_the_persisted_job(self, engine, canon):
        """Pin 2 — the dispatch raised AFTER the insert: the job EXISTS."""
        result, enqueued = await _drive_faulty_skill_fix(
            engine, canon, mode="post"
        )

        assert len(enqueued) == 1, (
            f"the post-insert fault must have persisted exactly one row: {enqueued!r}"
        )
        job_id = enqueued[0].job_id
        assert job_id in result, (
            "the envelope must NAME the persisted job id so the agent can "
            f"look it up: {job_id!r} not in {result!r}"
        )
        assert "was persisted for this request" in result, (
            f"the envelope must say the request WAS recorded: {result!r}"
        )
        assert FALSE_DURABILITY_SENTENCE not in result, (
            f"the old constant must not reach the agent: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_the_drop_is_countable_and_announced(self, engine, canon):
        """Pin 3 — a dropped request must be VISIBLE (F-G2.2).

        The house refusal pattern: an anomaly row + a counter + a
        WARNING log + a sink record. The counter and the reason token
        are what make the drop COUNTABLE, and the token is DISTINCT
        from the cap refusal and from a successful admission.
        """
        reporter = _RecordingReporter()
        observability.set_anomaly_reporter(reporter)

        result, enqueued = await _drive_faulty_skill_fix(
            engine, canon, mode="pre"
        )

        assert enqueued == [], "the drop must have persisted no row"

        # (a) the refusal counter — its OWN reason token.
        refusals = observability.counters_snapshot()["admission_refusals"]
        assert refusals.get("fix_dispatch_fault") == 1, (
            f"the drop must be countable under its own token: {refusals!r}"
        )
        assert "fix_burst_cap" not in refusals, (
            f"the drop must not be merged with the cap refusal: {refusals!r}"
        )

        # (b) the anomaly row — the SAME public seam the cap refusal uses.
        assert len(reporter.calls) == 1, (
            f"the drop must write exactly ONE anomaly row: {reporter.calls!r}"
        )
        assert reporter.calls[0]["category"] == observability.CATEGORY_LANE_DEVIATION
        assert "fix_dispatch_fault" in reporter.calls[0]["description"], (
            "the anomaly must carry the stable reason token: "
            f"{reporter.calls[0]!r}"
        )

        # (c) the sink record.
        sink_files = sorted(
            observability.resolve_sink_dir().glob("skill-capture-*.jsonl")
        )
        assert sink_files, "the drop must leave a sink record"
        rows = [
            line
            for line in sink_files[-1].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert rows, "the sink file must carry the record"
        assert any("fix_dispatch_fault" in line for line in rows), (
            f"the sink record must carry the stable reason token: {rows!r}"
        )

    @pytest.mark.asyncio
    async def test_the_carrier_id_wins_over_the_read_back(self, engine, canon):
        """Pin 4 — the dispatcher's OWN carrier is preferred (option 1).

        An exception that CARRIES the persisted job id is the
        dispatcher's statement that a row was committed for THIS
        admission. It is preferred over a DB read — and it is the only
        source that can reach a row whose idempotency key was suffixed
        (a fresh-admission key is suffixed when a TERMINAL predecessor
        already held it, and a key-based read then misses the row).
        """
        fault = _FaultCarryingThePersistedJobId("dispatcher exploded")
        fault.persisted_job_id = "job-carried-by-the-dispatcher"

        result = await _drive_skill_fix(fault)

        assert "job-carried-by-the-dispatcher" in result, (
            "the carrier id must be named: " f"{result!r}"
        )
        assert "was persisted for this request" in result, (
            f"the carrier means the request WAS recorded: {result!r}"
        )
        assert FALSE_DURABILITY_SENTENCE not in result, (
            f"the old constant must not reach the agent: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_a_recorded_fault_is_not_counted_as_a_refusal(self, engine, canon):
        """The recorded fault is announced but NOT counted as a refusal.

        A row WAS minted, so counting it as an admission refusal would
        merge two different signals (DS-19) and make the drop count
        wrong.
        """
        reporter = _RecordingReporter()
        observability.set_anomaly_reporter(reporter)

        result, enqueued = await _drive_faulty_skill_fix(
            engine, canon, mode="post"
        )

        refusals = observability.counters_snapshot()["admission_refusals"]
        assert "fix_dispatch_fault" not in refusals, (
            f"a persisted request is NOT a refusal: {refusals!r}"
        )
        assert len(reporter.calls) == 1, (
            "the fault must still be announced: " f"{reporter.calls!r}"
        )
