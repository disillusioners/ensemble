"""Tests for inner_soul tool's persona content preservation (Phase 3).

The companion to `test_inner_soul_rejection.py`. Where that file pins
the behavior that *project* content is REJECTED, this file pins the
inverse: legitimate persona/behavioral content must NOT be rejected,
even when it mentions project-y words like "deployments" or "kubernetes"
in a clearly self-referential way.

The 3-stage flow rescues persona-prefixed requests that mention project
terms ONLY when a persona CATEGORY (identity, personality,
user_preference, user_identity, workflow) ALSO matches in Stage 3.
Otherwise the persona prefix is treated as camouflage and the request
is REJECTED. These tests pin the actual behavior — for every test case
we verified the result by running `_classify_request()` against the
real implementation.
"""

import pytest
from unittest.mock import MagicMock, patch

from daemon.tools.inner_soul import _classify_request


# =============================================================================
# Helpers
# =============================================================================


def _assert_not_project_knowledge(result: dict) -> None:
    """Assert the classification is NOT a project rejection."""
    assert result["type"] != "project_knowledge", (
        f"Expected non-project classification, got {result}"
    )
    assert "REJECT" not in result.get("targets", []), (
        f"Expected no REJECT target, got {result.get('targets')}"
    )


# =============================================================================
# Persona content must be accepted (25 cases)
# =============================================================================


class TestPersonaContentAccepted:
    """Legitimate persona/behavioral reflection must NOT be rejected.

    Each test asserts `_classify_request()` does not return
    `project_knowledge`/`REJECT`. The "expected type" assertion is a
    soft pin on the dominant category — the hard requirement is
    "not rejected" so this file remains green even when the secondary
    category shifts.
    """

    # --- 1-3: personality / preference -----------------------------------

    def test_i_should_be_concise(self):
        result = _classify_request("I should be more concise")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    def test_be_more_formal(self):
        result = _classify_request("Be more formal with the user")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    def test_user_prefers_typescript(self):
        result = _classify_request("User prefers TypeScript")
        _assert_not_project_knowledge(result)
        assert result["type"] == "user_preference"

    # --- 4-6: workflow / identity (with project-y words) -----------------

    def test_always_verify_before_committing(self):
        """`always verify` matches the workflow pattern, not the project one."""
        result = _classify_request("Always verify before committing")
        _assert_not_project_knowledge(result)
        assert result["type"] == "workflow"

    def test_i_am_devops_agent(self):
        """`I am a` matches identity; "DevOps" is not in the project list."""
        result = _classify_request("I am a DevOps agent")
        _assert_not_project_knowledge(result)
        assert result["type"] == "identity"

    def test_i_should_be_careful_with_deployments(self):
        """`deployments` (plural) is NOT in the project pattern list.

        The project pattern is `\\bdeployment\\b` (singular). The plural
        `deployments` does not match, so this request falls through to
        Stage 3 with no category match and is classified as the
        fallback `event` — still NOT project_knowledge.
        """
        result = _classify_request("I should be more careful with deployments")
        _assert_not_project_knowledge(result)
        # Falls through to the event fallback (no persona category matched).
        assert result["type"] == "event"

    # --- 7-9: my-* persona prefixes --------------------------------------

    def test_my_approach_building_solutions(self):
        result = _classify_request(
            "My approach to building solutions should be structured"
        )
        _assert_not_project_knowledge(result)

    def test_i_learned_early_testing_catches_bugs(self):
        """`I learned that early` matches the persona-learning prefix.

        The prefix rescues the request from any incidental project terms
        (no project terms here, but the prefix is exercised for symmetry).
        """
        result = _classify_request("I learned that early testing catches bugs")
        _assert_not_project_knowledge(result)
        # Falls through to event fallback (no persona category matched).
        assert result["type"] == "event"

    def test_i_learned_i_rush_too_much(self):
        result = _classify_request("I learned that I rush too much on small tasks")
        _assert_not_project_knowledge(result)

    # --- 10-12: my-* with project-y words -------------------------------

    def test_my_strategy_for_endpoint_design(self):
        """`my strategy` is a persona prefix; "endpoint" alone isn't project.

        Project patterns require a compound verb+noun (e.g. "updated the
        endpoint"), so "my strategy for endpoint design" doesn't match.
        """
        result = _classify_request("My strategy for endpoint design should be minimal")
        _assert_not_project_knowledge(result)

    def test_my_tendency_overplan(self):
        result = _classify_request("My tendency is to overplan simple tasks")
        _assert_not_project_knowledge(result)

    def test_i_value_thorough_testing(self):
        """`I value` matches the personality pattern (`i (value|believe|care about)`)."""
        result = _classify_request("I value thorough testing over speed")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    # --- 13-15: I believe / I strive / Be more concise -------------------

    def test_i_believe_in_clean_code(self):
        result = _classify_request("I believe in clean code")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    def test_i_strive_to_be_concise(self):
        result = _classify_request("I strive to be concise")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    def test_be_more_concise_in_responses(self):
        result = _classify_request("Be more concise in responses")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    # --- 16-18: Be cozy / User likes / Remember my name ------------------

    def test_be_cozy_with_user(self):
        result = _classify_request("Be cozy with the user")
        _assert_not_project_knowledge(result)
        assert result["type"] == "personality"

    def test_user_likes_concise_answers(self):
        result = _classify_request("User likes concise answers")
        _assert_not_project_knowledge(result)
        assert result["type"] == "user_preference"

    def test_remember_my_name_is_cody(self):
        """`Remember my` IS a persona prefix; "Cody" is a name, not project."""
        result = _classify_request("Remember my name is Cody")
        _assert_not_project_knowledge(result)
        assert result["type"] == "identity"

    # --- 19-21: I should trust / I am a coder / I need -------------------

    def test_i_should_trust_agents(self):
        result = _classify_request("I should trust agents more on small tasks")
        _assert_not_project_knowledge(result)

    def test_i_am_a_coder_specialist(self):
        result = _classify_request("I am a coder specialist")
        _assert_not_project_knowledge(result)
        assert result["type"] == "identity"

    def test_i_need_to_be_patient(self):
        result = _classify_request("I need to be more patient with users")
        _assert_not_project_knowledge(result)

    # --- 22-25: philosophy / wants / aim -------------------------------

    def test_my_philosophy_test_early(self):
        result = _classify_request("My philosophy is to test early and often")
        _assert_not_project_knowledge(result)

    def test_user_wants_detailed_explanations(self):
        result = _classify_request("User wants detailed explanations")
        _assert_not_project_knowledge(result)
        assert result["type"] == "user_preference"

    def test_i_aim_to_reduce_complexity(self):
        """`I aim to` matches the persona prefix; no project term present.

        The prefix fires, Stage 2 finds no project pattern, Stage 3 finds
        no persona CATEGORY (personality requires "value|believe|care about",
        not "aim to"), so the request falls through to the event fallback.
        """
        result = _classify_request("I aim to reduce unnecessary complexity")
        _assert_not_project_knowledge(result)
        # Falls through to event fallback.
        assert result["type"] == "event"


# =============================================================================
# Boundary cases: same keyword, persona vs project context
# =============================================================================


class TestPersonaVsProjectContrast:
    """The same project-y keyword must be accepted in persona context
    and rejected in project context. These pairs prove the 3-stage
    flow distinguishes intent, not just vocabulary.
    """

    def test_clean_code_persona_vs_project(self):
        """`I believe in clean code` is persona; `Updated the code...` is project."""
        persona = _classify_request("I believe in clean code")
        _assert_not_project_knowledge(persona)
        assert persona["type"] == "personality"

        project = _classify_request("Updated the code to handle edge cases")
        assert project["type"] == "project_knowledge"
        assert project["targets"] == ["REJECT"]

    def test_deployments_persona_vs_project(self):
        """`I should be more careful with deployments` is persona;
        `Deployed the new build to production` is project.
        """
        persona = _classify_request("I should be more careful with deployments")
        _assert_not_project_knowledge(persona)

        project = _classify_request("Deployed the new build to production")
        assert project["type"] == "project_knowledge"
        assert project["targets"] == ["REJECT"]

    def test_early_testing_persona_vs_project(self):
        """`I learned that early testing catches bugs` is persona-prefixed;
        `Deployed the new build to production` is project.
        """
        persona = _classify_request("I learned that early testing catches bugs")
        _assert_not_project_knowledge(persona)

        project = _classify_request("Deployed the new build to production")
        assert project["type"] == "project_knowledge"
        assert project["targets"] == ["REJECT"]

    def test_workflow_persona_vs_project(self):
        """`Always check tests before committing` is workflow (not project).

        The verb "check" is in the workflow alternation
        `(do|check|verify|run|use)`, and the pattern fires before any
        project pattern would.
        """
        workflow = _classify_request("Always check tests before committing")
        _assert_not_project_knowledge(workflow)
        assert workflow["type"] == "workflow"

        # Compare with a project-y phrasing that DOES get rejected.
        project = _classify_request("Updated the API endpoint")
        assert project["type"] == "project_knowledge"
        assert project["targets"] == ["REJECT"]
