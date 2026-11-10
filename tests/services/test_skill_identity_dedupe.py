"""Identity dedupe at the injection boundary (workstream F, Ruling F).

**The invariant under test.**  At most ONE variant per ``(name,
project_id)`` identity may reach a given agent in one injection, and
WHICH variant survives is decided by a stated, reproducible rule —
highest LINEAGE GENERATION, ties broken by the skill id — never by
row order and never by wall-clock.

**The counter-arm (the reason these pins are not vacuous).**  Every
end-to-end pin here runs the SAME fixture twice: once through the
deduped injection path (one variant) and once through the raw injector
(both variants).  A fixture that cannot tell the two apart would prove
nothing, so the counter-arm is asserted, not assumed.

**The counterfactual is also a source-level mutation leg (M-F6).**  The
pack neuters :func:`daemon.services.skill_identity_dedupe.dedupe_search_results`
and the same pins turn RED.

Nothing here touches a database, the network, or the environment: the
search service is stubbed and the corpus is seeded in-process.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.skill_identity_dedupe import (
    dedupe_injected_entries,
    dedupe_search_results,
    install_identity_dedupe,
    inject_skills_deduped,
    select_canonical_variant,
    skill_identity_key,
)
from daemon.services.skill_injection_service import SkillInjectionService

# ─────────────────────────────────────────────────────────────
# Fixtures — live-shaped skill rows
# ─────────────────────────────────────────────────────────────

PROJECT = "83da04de-a410-4fb5-9e92-251a99d28a52"


def make_skill(
    *,
    skill_id: str,
    name: str,
    project_id: str | None = PROJECT,
    generation: int = 0,
    content: str = "# Skill\n\nBody.",
    description: str = "A skill.",
    status: str = "active",
    is_active: bool = True,
    ab_test_group: str | None = None,
    updated_at: str | None = None,
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
        updated_at=updated_at,
    )


def make_service(search_result: dict) -> SkillInjectionService:
    """A ``SkillInjectionService`` whose search returns ``search_result``."""
    search = MagicMock()
    search.search = AsyncMock(return_value=search_result)
    config = MagicMock(spec=["max_inject_skills"])
    config.max_inject_skills = 2
    ab_repo = MagicMock()
    ab_repo.increment_comparison = MagicMock(return_value=None)
    skill_repo = MagicMock()
    skill_repo.get_ab_variants = MagicMock(return_value=[])
    return SkillInjectionService(
        search_service=search,
        config=config,
        ab_test_repo=ab_repo,
        skill_repo=skill_repo,
    )


# Two variants of ONE identity (same name, same project) — the shape the
# 27 stranded A/B pairs produce.
LOSER = make_skill(
    skill_id="aaaaaaaa-0000-4000-8000-000000000001",
    name="code-fix",
    generation=1,
    content="# Code Fix (gen 1)\n\nOld body: apply the patch, then commit.",
    description="Fix the failing test and commit the change.",
    status="ab_testing",
    ab_test_group="grp-code-fix",
    updated_at="2026-11-09T10:00:00+00:00",
)
WINNER = make_skill(
    skill_id="bbbbbbbb-0000-4000-8000-000000000002",
    name="code-fix",
    generation=2,
    content="# Code Fix (gen 2)\n\nNew body: reproduce, fix, run the suite.",
    description="Fix the failing test and commit the change.",
    status="ab_testing",
    ab_test_group="grp-code-fix",
    updated_at="2026-10-04T09:00:00+00:00",
)


# ─────────────────────────────────────────────────────────────
# The pick rule
# ─────────────────────────────────────────────────────────────


def test_identity_key_is_the_pair_not_the_name():
    """Same name + different project ⇒ DIFFERENT identities."""
    a = make_skill(skill_id="a", name="code-fix", project_id="p1")
    b = make_skill(skill_id="b", name="code-fix", project_id="p2")
    assert skill_identity_key(a) == ("code-fix", "p1")
    assert skill_identity_key(a) != skill_identity_key(b)


def test_pick_rule_prefers_the_highest_generation_not_the_timestamp():
    """The stale-``updated_at`` case: a naive timestamp rule picks WRONG.

    The winner carries an OLDER ``updated_at`` than the loser (two
    families are known to be in that state) but a HIGHER generation —
    the rule must pick the generation.
    """
    assert WINNER.updated_at < LOSER.updated_at
    chosen = select_canonical_variant([LOSER, WINNER])
    assert chosen is WINNER
    # Order-independent — the rule does not depend on the input order.
    assert select_canonical_variant([WINNER, LOSER]) is WINNER


def test_pick_rule_tie_break_is_the_skill_id():
    """Equal generation ⇒ the lexicographically smallest id wins."""
    low = make_skill(skill_id="11111111-0000-4000-8000-00000000000a", name="x", generation=3)
    high = make_skill(skill_id="99999999-0000-4000-8000-00000000000b", name="x", generation=3)
    assert select_canonical_variant([high, low]) is low
    assert select_canonical_variant([low, high]) is low


def test_pick_rule_degrades_to_generation_zero_on_a_broken_value():
    """A non-numeric generation degrades to 0 (never to an arbitrary pick)."""
    broken = make_skill(skill_id="c", name="x", generation=0)
    broken.generation = "not-a-number"
    good = make_skill(skill_id="d", name="x", generation=1)
    assert select_canonical_variant([broken, good]) is good


# ─────────────────────────────────────────────────────────────
# The pool filter
# ─────────────────────────────────────────────────────────────


def test_dedupe_keeps_exactly_one_entry_per_identity():
    entries = [
        {"skill": LOSER, "score": 0.91},
        {"skill": WINNER, "score": 0.88},
    ]
    deduped = dedupe_injected_entries(entries)
    assert len(deduped) == 1
    assert deduped[0]["skill"] is WINNER
    # The surviving ENTRY is the canonical variant's own entry — the
    # score belongs to the row that was kept. The POSITION is the slot
    # of the group's first occurrence, so the ranking is preserved.
    assert deduped[0]["score"] == 0.88


def test_dedupe_keeps_two_different_identities():
    """Same name, DIFFERENT project ⇒ both survive (the key is the PAIR)."""
    p1 = make_skill(skill_id="a", name="code-fix", project_id="p1", generation=1)
    p2 = make_skill(skill_id="b", name="code-fix", project_id="p2", generation=0)
    deduped = dedupe_injected_entries(
        [{"skill": p1, "score": 0.9}, {"skill": p2, "score": 0.8}]
    )
    assert len(deduped) == 2
    assert {e["skill"].id for e in deduped} == {"a", "b"}


def test_dedupe_leaves_the_low_match_list_alone():
    """``low_match`` carries no skill row ⇒ no identity key ⇒ untouched.

    It is also rendered as name + description only (never a body), so it
    cannot deliver two DIVERGENT variants of one skill.
    """
    results = {
        "injected": [{"skill": LOSER, "score": 0.9}, {"skill": WINNER, "score": 0.8}],
        "low_match": [
            {"name": "code-fix", "score": 0.2, "description": "same name, other project"},
            {"name": "code-fix", "score": 0.1, "description": "same name, yet another"},
        ],
    }
    out = dedupe_search_results(results)
    assert len(out["injected"]) == 1
    assert len(out["low_match"]) == 2


def test_dedupe_passes_through_entries_without_a_skill():
    entries = [{"skill": None, "score": 0.0}, {"skill": LOSER, "score": 0.9}]
    deduped = dedupe_injected_entries(entries)
    assert deduped[0]["skill"] is None
    assert len(deduped) == 2


# ─────────────────────────────────────────────────────────────
# The end-to-end injection boundary
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_injection_serves_one_variant_and_names_the_winner():
    """The end-to-end pin: exactly ONE variant is injected — the winner."""
    service = make_service(
        {"injected": [{"skill": LOSER, "score": 0.91}, {"skill": WINNER, "score": 0.88}], "low_match": []}
    )
    text, ids = await inject_skills_deduped(
        service,
        "fix the failing test",
        project_id=PROJECT,
        instance_id="inst-1",
        message_id="msg-1",
        agent_id="worker",
    )
    assert text is not None
    assert ids == [WINNER.id]
    assert WINNER.id in text
    assert LOSER.id not in text
    assert "Code Fix (gen 2)" in text
    assert "Code Fix (gen 1)" not in text


@pytest.mark.asyncio
async def test_counter_arm_without_the_dedupe_both_variants_reach_the_agent():
    """The counter-arm: the SAME fixture through the RAW injector.

    Without the dedupe the agent receives BOTH divergent variants — the
    exact defect the pin exists to close.  If this arm ever stopped
    producing two variants, the fixture would have stopped
    discriminating and the pin would be vacuous.
    """
    service = make_service(
        {"injected": [{"skill": LOSER, "score": 0.91}, {"skill": WINNER, "score": 0.88}], "low_match": []}
    )
    text, ids = await service.inject_skills(
        "fix the failing test",
        project_id=PROJECT,
        instance_id="inst-1",
        message_id="msg-1",
        agent_id="worker",
    )
    assert len(ids) == 2
    assert LOSER.id in text
    assert WINNER.id in text


@pytest.mark.asyncio
async def test_dedupe_survives_a_retry_of_the_same_message():
    """Determinism: the same message resolves to the same variant."""
    service = make_service(
        {"injected": [{"skill": LOSER, "score": 0.91}, {"skill": WINNER, "score": 0.88}], "low_match": []}
    )
    first = await inject_skills_deduped(
        service, "fix", project_id=PROJECT, instance_id="i", message_id="m"
    )
    second = await inject_skills_deduped(
        service, "fix", project_id=PROJECT, instance_id="i", message_id="m"
    )
    assert first[1] == second[1] == [WINNER.id]


@pytest.mark.asyncio
async def test_dedupe_install_is_idempotent():
    """Installing twice must not stack two proxies (and must not fail)."""
    service = make_service({"injected": [], "low_match": []})
    assert install_identity_dedupe(service) is True
    assert install_identity_dedupe(service) is False
    await inject_skills_deduped(service, "x", project_id=PROJECT, instance_id="i", message_id="m")
    # The search reference is still a single proxy layer.
    from daemon.services.skill_identity_dedupe import _IdentityDedupingSearchProxy

    proxy = service._search_service
    assert isinstance(proxy, _IdentityDedupingSearchProxy)
    assert not isinstance(proxy._inner, _IdentityDedupingSearchProxy)


@pytest.mark.asyncio
async def test_dedupe_does_not_change_a_pool_without_duplicates():
    """A pool with no duplicate identity is served unchanged."""
    other = make_skill(skill_id="cccc", name="git-commit", generation=1)
    service = make_service(
        {"injected": [{"skill": LOSER, "score": 0.9}, {"skill": other, "score": 0.8}], "low_match": []}
    )
    text, ids = await inject_skills_deduped(
        service, "fix", project_id=PROJECT, instance_id="i", message_id="m"
    )
    assert len(ids) == 2
    assert LOSER.id in text
    assert other.id in text


@pytest.mark.asyncio
async def test_dedupe_collapses_only_the_duplicated_identity():
    """One duplicated identity collapses; its neighbours survive."""
    other = make_skill(skill_id="cccc", name="git-commit", generation=1)
    service = make_service(
        {
            "injected": [
                {"skill": LOSER, "score": 0.9},
                {"skill": WINNER, "score": 0.88},
                {"skill": other, "score": 0.8},
            ],
            "low_match": [],
        }
    )
    text, ids = await inject_skills_deduped(
        service, "fix", project_id=PROJECT, instance_id="i", message_id="m"
    )
    assert ids == [WINNER.id, other.id]
    assert LOSER.id not in text
    assert WINNER.id in text
    assert other.id in text
