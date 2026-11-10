"""The injection budget, proved against REAL skill content (workstream F).

**What is proven.**  ``max_inject_skills`` defaults to **2**
(``daemon/config.py`` — ``SkillEvolutionConfig.max_inject_skills``) and
the search returns ``{"injected": [...], "low_match": [...]}``.  This
file proves, against a **live-shaped corpus** (real skill names,
real descriptions, real multi-paragraph bodies — NOT synthetic
one-word skills):

1. the injected count never exceeds the cap, and the cap **binds**
   (the corpus is large enough that an unbounded injector would
   over-deliver);
2. the ``low_match`` list is present and **bounded**;
3. the rendered ``[System Inject]`` block is **honest** — a skill that
   was NOT injected is never rendered as injected;
4. with E4.3's filter active, no stale row appears in ``injected`` or
   ``low_match`` (conditional on that filter being present in the tree
   — it is E4's file, not F's).

**The pipeline under test is the REAL one.**  The only stub is the DB
prefilter (``_bm25_prefilter``) — the corpus is seeded in-process, but
the selection, the cap, the A/B routing and the rendering are the
production code paths.

**The mutations.**  M-F1 (raise the cap to 3) and M-F2 (drop the
low_match bound) are planted on production source by the pack; both
turn the pins here RED.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.config import SkillEvolutionConfig
from daemon.services.skill_identity_dedupe import inject_skills_deduped
from daemon.services.skill_injection_service import SkillInjectionService
from daemon.services.skill_search_service import SkillSearchService

PROJECT = "83da04de-a410-4fb5-9e92-251a99d28a52"

# The PINNED cap — asserted as a literal, NOT read from the config, so
# a mutation that raises the configured default is detected.
PINNED_MAX_INJECT_SKILLS = 2

# The PINNED low_match bound — the search offers the next three.
PINNED_LOW_MATCH_BOUND = 3


def make_skill(
    *,
    skill_id: str,
    name: str,
    description: str,
    content: str,
    project_id: str | None = PROJECT,
    generation: int = 0,
    status: str = "active",
    is_active: bool = True,
    ab_test_group: str | None = None,
) -> SimpleNamespace:
    """Build a skill row stand-in with the real model's field names."""
    return SimpleNamespace(
        id=skill_id,
        name=name,
        description=description,
        content=content,
        project_id=project_id,
        is_active=is_active,
        status=status,
        ab_test_group=ab_test_group,
        generation=generation,
        lineage_origin="evolved" if generation else "imported",
        auto_load=False,
        source_skill_bank_id=None,
    )


# ─────────────────────────────────────────────────────────────
# The live-shaped corpus
# ─────────────────────────────────────────────────────────────

CODE_FIX_GEN2 = make_skill(
    skill_id="c0de0001-0000-4000-8000-00000000f001",
    name="code-fix",
    description=(
        "Fix a failing test or a reported defect end to end: reproduce it, "
        "patch the root cause, run the suite, and report what changed."
    ),
    content=(
        "# Code Fix\n\n"
        "Reproduce the failure first — never patch blind.\n\n"
        "## Procedure\n\n"
        "1. Re-run the failing test and capture the exact assertion.\n"
        "2. Locate the root cause in the production path, not in the test.\n"
        "3. Patch the cause; keep the diff minimal and focused.\n"
        "4. Re-run the focused test, then the touched suite.\n"
        "5. Report the change, the evidence, and the residual risk.\n\n"
        "Never weaken an assertion to make a test pass.\n"
    ),
    generation=2,
    status="ab_testing",
    ab_test_group="grp-code-fix",
)

DEV_STRATEGY_GEN2 = make_skill(
    skill_id="d3e00001-0000-4000-8000-00000000f002",
    name="dev-strategy",
    description=(
        "Plan and execute a code change: read the surrounding code first, "
        "state the approach, implement it, and verify the behaviour."
    ),
    content=(
        "# Development Strategy\n\n"
        "Read before you write.\n\n"
        "## Procedure\n\n"
        "1. Read the code the change touches, and its callers.\n"
        "2. State the approach in one paragraph before editing.\n"
        "3. Implement the smallest change that satisfies the requirement.\n"
        "4. Verify against the requirement, not against the diff.\n"
        "5. Leave the tree in a state the next reader can follow.\n"
    ),
    generation=2,
    status="ab_testing",
    ab_test_group="grp-dev-strategy",
)

DEV_STRATEGY_GEN1 = make_skill(
    skill_id="d3e00001-0000-4000-8000-00000000f003",
    name="dev-strategy",
    description=(
        "Plan and execute a code change: read the surrounding code first, "
        "state the approach, implement it, and verify the behaviour."
    ),
    content=(
        "# Development Strategy (older generation)\n\n"
        "Read before you write.\n"
        "Implement, then run the tests.\n"
    ),
    generation=1,
    status="ab_testing",
    ab_test_group="grp-dev-strategy",
)

GIT_COMMIT = make_skill(
    skill_id="917c0001-0000-4000-8000-00000000f004",
    name="git-commit",
    description=(
        "Stage and commit a change with a conventional-commit subject and "
        "an explicit path list — never a blanket `git add -A`."
    ),
    content=(
        "# Git Commit\n\n"
        "One logical change per commit.\n\n"
        "## Procedure\n\n"
        "1. Review the diff before staging anything.\n"
        "2. Stage with EXPLICIT pathspecs — `git add -A` is banned.\n"
        "3. Commit as `<type>(<scope>): <description>`.\n"
        "4. Re-read the committed diff to confirm nothing foreign rode along.\n"
    ),
)

PLAN_CREATION = make_skill(
    skill_id="91a00001-0000-4000-8000-00000000f005",
    name="plan-creation",
    description=(
        "Write a plan a stranger can execute: objective, phases, explicit "
        "dependencies, and the acceptance criterion of every phase."
    ),
    content=(
        "# Plan Creation\n\n"
        "A plan is an executable artifact, not a narrative.\n\n"
        "## Structure\n\n"
        "1. Objective — one sentence, testable.\n"
        "2. Phases — each with its own acceptance criterion.\n"
        "3. Dependencies — the hard edges between phases.\n"
        "4. Risks — what would make the plan wrong.\n"
    ),
    generation=1,
)

TEST_PACK_EXECUTION = make_skill(
    skill_id="7e500001-0000-4000-8000-00000000f006",
    name="test-pack-execution",
    description=(
        "Run a registered test pack and read its verdict honestly: the exit "
        "code, the failed list, and the red-set diff against the base."
    ),
    content=(
        "# Test Pack Execution\n\n"
        "Run the pack; read the verdict; report the red set as a SET.\n\n"
        "## Procedure\n\n"
        "1. Run the registered pack with the worktree-local interpreter.\n"
        "2. Classify the failures with the house classifier (`grep '^FAILED'`).\n"
        "3. Diff the red set against the base set — sets, not counts.\n"
        "4. Report net-new reds; disclose base-proven debt.\n"
    ),
)

REVIEW_STRATEGY = make_skill(
    skill_id="9e000001-0000-4000-8000-00000000f007",
    name="review-strategy",
    description=(
        "Review a change for correctness, blast radius and honesty: what "
        "breaks, what is claimed but unproven, and what the reviewer cannot "
        "verify from the diff alone."
    ),
    content=(
        "# Review Strategy\n\n"
        "Three passes: correctness, blast radius, honesty of the claims.\n\n"
        "## Severity\n\n"
        "- [CRITICAL] blocks the change.\n"
        "- [ADVISORY] rides along with the change.\n"
        "- [BRIEF-LEVEL] is an input to the brief, not a defect.\n"
    ),
)

PR_REVIEW = make_skill(
    skill_id="9e000001-0000-4000-8000-00000000f008",
    name="pr-review",
    description=(
        "Review a pull request end to end: the diff, the commit trail, the "
        "tests that were added, and the claims the description makes."
    ),
    content=(
        "# PR Review\n\n"
        "Read the description, then the diff, then the tests.\n\n"
        "## Checklist\n\n"
        "1. Does the diff do what the description claims?\n"
        "2. Is there a test that would fail without the change?\n"
        "3. Does the commit trail tell one story?\n"
    ),
)

CORPUS = [
    CODE_FIX_GEN2,
    DEV_STRATEGY_GEN2,
    DEV_STRATEGY_GEN1,
    GIT_COMMIT,
    PLAN_CREATION,
    TEST_PACK_EXECUTION,
    REVIEW_STRATEGY,
    PR_REVIEW,
]


# ─────────────────────────────────────────────────────────────
# The pipeline (real search + real injector, seeded corpus)
# ─────────────────────────────────────────────────────────────


def build_pipeline(corpus: list[SimpleNamespace]):
    """Build the REAL search + injection pipeline over a seeded corpus."""
    skill_repo = MagicMock()
    skill_repo.get_ab_variants = MagicMock(return_value=[])
    skill_repo.get_active_variant = MagicMock(return_value=None)
    ab_repo = MagicMock()
    ab_repo.increment_comparison = MagicMock(return_value=None)

    search = SkillSearchService(
        skill_repo=skill_repo,
        embedding_repo=MagicMock(),
        embedding_service=None,
        llm_config={},
        config=None,  # the file-only / degraded construction: no LLM, no network
    )
    # The ONLY stub: the DB prefilter. The corpus is seeded in-process;
    # every stage above it is production code.
    search._bm25_prefilter = AsyncMock(return_value=list(corpus))

    config = SkillEvolutionConfig()
    service = SkillInjectionService(
        search_service=search,
        config=config,
        ab_test_repo=ab_repo,
        skill_repo=skill_repo,
    )
    return search, service


@pytest.fixture(scope="module")
def pipeline():
    return build_pipeline(CORPUS)


# ─────────────────────────────────────────────────────────────
# The budget proof
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_holds_against_real_content(pipeline):
    """The cap holds, the cap binds, and the block is honest."""
    _search, service = pipeline
    text, ids = await inject_skills_deduped(
        service,
        "fix the failing test and commit the change",
        project_id=PROJECT,
        instance_id="inst-budget",
        message_id="msg-budget",
        agent_id="worker",
    )
    assert text is not None

    # 1 — the cap holds (PINNED literal, not read from the config).
    assert len(ids) <= PINNED_MAX_INJECT_SKILLS
    # 2 — the cap BINDS: the corpus is far larger than the cap.
    assert len(ids) == PINNED_MAX_INJECT_SKILLS
    assert len(CORPUS) > PINNED_MAX_INJECT_SKILLS

    # 3 — the block is honest: every injected id is rendered, and no
    # row that was NOT injected is rendered as injected.
    for skill_id in ids:
        assert skill_id in text
    for skill in CORPUS:
        if skill.id not in ids:
            assert skill.id not in text


@pytest.mark.asyncio
async def test_low_match_is_present_and_bounded(pipeline):
    """``low_match`` exists and stays inside its bound."""
    search, service = pipeline
    results = await search.search(
        "fix the failing test and commit the change",
        project_id=PROJECT,
        max_results=PINNED_MAX_INJECT_SKILLS,
    )
    assert "low_match" in results
    assert len(results["low_match"]) <= PINNED_LOW_MATCH_BOUND
    # Non-vacuous: the corpus is large enough for the bound to bite.
    assert len(results["low_match"]) == PINNED_LOW_MATCH_BOUND
    assert len(results["injected"]) == PINNED_MAX_INJECT_SKILLS


@pytest.mark.asyncio
async def test_the_effective_cap_is_the_pinned_default():
    """The effective cap is the pinned default of 2.

    This is the assertion the M-F1 mutation (raise the configured
    default to 3) turns RED.
    """
    config = SkillEvolutionConfig()
    assert config.max_inject_skills == PINNED_MAX_INJECT_SKILLS


@pytest.mark.asyncio
async def test_the_corpus_is_live_shaped():
    """Non-vacuity: real names, real descriptions, real multi-line bodies."""
    assert len(CORPUS) >= 6
    for skill in CORPUS:
        assert len(skill.name) > 3 and " " not in skill.name
        assert len(skill.description) > 40
        assert skill.content.count("\n") >= 3
        assert len(skill.content) > 80


@pytest.mark.asyncio
async def test_one_variant_per_identity_is_served_from_the_real_pipeline(pipeline):
    """The dedupe holds on the REAL pipeline, not only on the unit fixture."""
    _search, service = pipeline
    _text, ids = await inject_skills_deduped(
        service,
        "plan the development strategy",
        project_id=PROJECT,
        instance_id="inst-dedupe",
        message_id="msg-dedupe",
        agent_id="worker",
    )
    identities = [
        (s.name, s.project_id) for s in CORPUS if s.id in ids
    ]
    assert len(identities) == len(set(identities))


@pytest.mark.asyncio
async def test_no_stale_row_is_served():
    """M-F3 (consumer side): an inactive row is never served.

    Conditional on E4.3's filter being present in the tree — that
    filter is E4's file, not F's.  The predicate used here (an
    inactive row) is a pure attribute read, so the assertion is
    deterministic and needs no database.
    """
    _search, service = build_pipeline(list(CORPUS))
    if not hasattr(service, "_filter_stale_candidates"):
        pytest.skip("E4.3's injection filter is not present in this tree yet")

    stale = make_skill(
        skill_id="5ta10001-0000-4000-8000-00000000f009",
        name="code-review",
        description="An inactive row that must never be served to an agent.",
        content="# Code Review\n\nThis row is inactive and must never be served.\n",
        generation=0,
        is_active=False,
    )
    corpus = [CODE_FIX_GEN2, stale, DEV_STRATEGY_GEN2]
    _search2, service2 = build_pipeline(corpus)
    if not hasattr(service2, "_filter_stale_candidates"):
        pytest.skip("E4.3's injection filter is not present in this tree yet")

    text, ids = await inject_skills_deduped(
        service2,
        "fix the failing test",
        project_id=PROJECT,
        instance_id="inst-stale",
        message_id="msg-stale",
        agent_id="worker",
    )
    assert stale.id not in ids
    assert stale.id not in (text or "")
