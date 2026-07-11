"""Phase 3 cross-agent integration tests for Ari + Worker.

Verifies the cross-cutting contracts between the Ari (jober-hybrid) and
Worker (OpenSpace orchestrator) agents added in Phases 1 and 2. These
tests cover concerns that span both agents and the broader system:

- Coexistence: both agents discoverable through AgentRegistry simultaneously
  with no ID conflicts and neither silently dropped by SKIP_DIRS.
- No-spawn-authorization: both agents have empty team_members AND no
  'instance' category in tools.allow. This is the dual-layer defense that
  prevents Ari/Worker from ever spawning their own subordinates.
- Dispatch graph acyclicity: Ari has 'job' (delegates via job queue to
  Leader/Worker); Worker has no 'job' (it is a leaf executor); and Leader
  does NOT list 'ari' or 'worker' in team_members — preventing Leader
  from dispatching back to either, which would create a circular path.
- Prompt composition: both agents' innate_skills load into the composed
  system prompt through the load_agent_skills → compose_system_prompt
  pipeline.
- Autonomy model: Ari's prompt documents TrueAuto; Worker's documents
  SemiAuto. This is the safety contract — Worker is the one who stops
  for breaking changes, Ari is the one who grants TrueAuto overrides
  via job_continue.

Mirrors the gold-standard pattern from test_devops_agent.py and the
sister-agent patterns from test_worker_agent.py / test_ari_agent.py
(class-per-concern structure, fixture style, assertion patterns,
imports). All tests are spec-driven and run in the unit environment
with langgraph/MCP mocks from conftest.py — no live OpenSpace or job
queue required.
"""

import json
from pathlib import Path

import pytest


# Path constants
AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"
ARI_AGENT_DIR = AGENTS_DIR / "ari"
WORKER_AGENT_DIR = AGENTS_DIR / "worker"
LEADER_AGENT_DIR = AGENTS_DIR / "leader"


# =============================================================================
# 1. Agent Coexistence
# =============================================================================


class TestAgentCoexistence:
    """Tests that Ari and Worker coexist in the AgentRegistry without conflict.

    Both agents were added in Phases 1 and 2. They must be discovered
    together when AgentRegistry scans the agents directory, neither must
    collide with another agent's ID, and neither must be filtered out by
    SKIP_DIRS (the underscore-prefixed template directories).
    """

    def test_both_ari_and_worker_discoverable_simultaneously(self) -> None:
        """A single AgentRegistry.discover() call must surface both Ari and Worker.

        The registry scans the agents directory once and populates its
        internal dict. Verifying both IDs exist after one discover() call
        proves that adding ari/ does not shadow worker/ (or vice versa)
        in the discovery loop.
        """
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(AGENTS_DIR)
        registry.discover()

        assert registry.exists("ari"), (
            "ari should be discovered alongside worker — missing after single discover()"
        )
        assert registry.exists("worker"), (
            "worker should be discovered alongside ari — missing after single discover()"
        )

    def test_no_agent_id_conflicts_across_all_agents(self) -> None:
        """All discovered agent IDs must be unique — no duplicates after discover().

        AgentRegistry uses agent_id as the key in its internal dict. If
        two directories produced the same id, one would silently shadow
        the other. This test guards against that regression by asserting
        list_all() produces a list with the same length as its id set.
        """
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(AGENTS_DIR)
        registry.discover()

        agents = registry.list_all()
        agent_ids = [a.id for a in agents]

        assert len(agent_ids) == len(set(agent_ids)), (
            f"Duplicate agent IDs detected: "
            f"{[aid for aid in agent_ids if agent_ids.count(aid) > 1]}"
        )
        # Both target agents must be present in the unique-id set
        assert "ari" in set(agent_ids), "ari should appear in the unique agent id set"
        assert "worker" in set(agent_ids), "worker should appear in the unique agent id set"

    def test_neither_ari_nor_worker_in_skip_dirs(self) -> None:
        """'ari' and 'worker' must NOT be in SKIP_DIRS — they are real agents.

        SKIP_DIRS contains only framework scaffolding (_trash, _baby_template,
        _prompt_system, _inner_soul). Adding 'ari' or 'worker' there would
        silently disable them at startup with no error to the operator.
        """
        from daemon.registry import SKIP_DIRS

        assert "ari" not in SKIP_DIRS, (
            "ari should NOT be in SKIP_DIRS — it is a real agent, not a template"
        )
        assert "worker" not in SKIP_DIRS, (
            "worker should NOT be in SKIP_DIRS — it is a real agent, not a template"
        )


# =============================================================================
# 2. No Team Members (No Spawn Authorization)
# =============================================================================


class TestNoTeamMembers:
    """Tests that neither Ari nor Worker has spawn-instance authorization.

    Both agents are designed to be dispatched-to (Ari by users, Worker by
    Ari's job_create). Neither is allowed to dispatch further. The
    team_members field in meta.json is the gate that the spawn_instance
    tool checks — empty/missing means deny-by-default. This is one half
    of the dual-layer no-spawn contract (the other half is the 'instance'
    category absence in tools.allow, verified in TestNoInstanceTools).
    """

    def test_ari_has_no_team_members_in_meta(self) -> None:
        """Ari meta.json must have NO team_members field, OR an empty list.

        Ari dispatches via job_create (job queue), not spawn_instance.
        Listing teammates in team_members would be moot (no instance tools),
        but the spec requires both layers to agree — the meta.json
        declaration must match the deny-by-default contract.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        team_members = meta.get("team_members", [])

        assert team_members == [] or team_members is None, (
            f"Ari should have NO team_members (front door dispatches via job_*), "
            f"got: {team_members}"
        )

    def test_worker_has_no_team_members_in_meta(self) -> None:
        """Worker meta.json must have NO team_members field, OR an empty list.

        Worker is a leaf executor — it receives jobs from Ari via the job
        queue and executes them via OpenSpace MCP tools. It must NOT be
        able to spawn any other agents. Empty/missing team_members is
        the deny-by-default contract.
        """
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        team_members = meta.get("team_members", [])

        assert team_members == [] or team_members is None, (
            f"Worker should have NO team_members (leaf executor), "
            f"got: {team_members}"
        )


# =============================================================================
# 3. No Instance Tools (No Spawn Authorization at Tool Layer)
# =============================================================================


class TestNoInstanceTools:
    """Tests that neither Ari nor Worker grants 'instance' tool category.

    The 'instance' category unlocks spawn_instance / send_message /
    terminate_instance. If present, the agent could bypass the job queue
    and create unmanaged dispatch paths. This is the tool-layer half of
    the no-spawn contract; the team_members half is verified in
    TestNoTeamMembers.
    """

    def test_ari_tools_allow_has_no_instance_category(self) -> None:
        """Ari's tools.allow must NOT contain 'instance'.

        Ari dispatches via job_create (the 'job' category). Listing
        'instance' would unlock spawn_instance and let Ari bypass the
        job-queue lifecycle and watch semantics that job_create provides.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        allow = meta.get("tools", {}).get("allow", [])

        assert "instance" not in allow, (
            f"Ari tools.allow must NOT include 'instance' (jober dispatches via job_*), "
            f"got: {allow}"
        )

    def test_worker_tools_allow_has_no_instance_category(self) -> None:
        """Worker's tools.allow must NOT contain 'instance'.

        Worker is a leaf executor. Even though Worker could technically
        be granted spawn_instance (e.g., to dispatch back to a
        sub-specialist), the spec deliberately keeps it a leaf — it
        returns results to its dispatcher (Ari) via job output, never
        by spawning further agents.
        """
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        allow = meta.get("tools", {}).get("allow", [])

        assert "instance" not in allow, (
            f"Worker tools.allow must NOT include 'instance' (leaf executor), "
            f"got: {allow}"
        )


# =============================================================================
# 4. Dispatch Graph Acyclicity
# =============================================================================


class TestDispatchGraphAcyclic:
    """Tests that the Ari ↔ Worker ↔ Leader dispatch graph is acyclic.

    The dispatch graph must be a DAG (Directed Acyclic Graph) — no agent
    can dispatch back to itself or any of its dispatchers. The acyclic
    invariant is enforced by:

    1. Tool topology: Ari has 'job' (dispatches via job queue) but not
       'instance' (no direct spawn); Worker has no 'job' and no
       'instance' (leaf executor).
    2. Routing topology: Leader's team_members must NOT list 'ari' or
       'worker'. Leader dispatches to its own knowledge team
       (developer, planner, etc.), never back to Ari (the user-facing
       front door) or Worker (the OpenSpace executor).

    A cycle would create an infinite loop: Ari → Leader → ... → Ari.
    """

    def test_ari_has_job_tools_worker_does_not(self) -> None:
        """Ari must have 'job' in tools.allow; Worker must NOT.

        This is the tool-topology half of the dispatch-graph acyclicity
        rule. Ari is a jober-hybrid: it has the 'job' category so it can
        dispatch to Leader (Mode 2) and Worker (Mode 3) via the job
        queue. Worker is a leaf executor — it does NOT manage the job
        queue, it only receives jobs. If Worker had 'job', it could
        create sub-jobs and the graph would no longer terminate.
        """
        ari_meta_path = ARI_AGENT_DIR / "meta.json"
        worker_meta_path = WORKER_AGENT_DIR / "meta.json"

        with open(ari_meta_path, "r", encoding="utf-8") as f:
            ari_meta = json.load(f)
        with open(worker_meta_path, "r", encoding="utf-8") as f:
            worker_meta = json.load(f)

        ari_allow = ari_meta.get("tools", {}).get("allow", [])
        worker_allow = worker_meta.get("tools", {}).get("allow", [])

        # Ari has job — she is the jober dispatch point
        assert "job" in ari_allow, (
            f"Ari tools.allow must include 'job' (jober dispatches via job_create), "
            f"got: {ari_allow}"
        )

        # Worker does NOT have job — it is a leaf executor
        assert "job" not in worker_allow, (
            f"Worker tools.allow must NOT include 'job' (Worker is a leaf executor, "
            f"receives jobs but does not dispatch them), got: {worker_allow}"
        )

    def test_leader_does_not_list_ari_or_worker_in_team_members(self) -> None:
        """Leader's team_members must NOT include 'ari' or 'worker'.

        Leader dispatches to its own knowledge team (planner, developer,
        reviewer, etc.). Listing 'ari' or 'worker' would let Leader
        spawn an Ari instance — but Ari is the user-facing front door,
        not a worker that Leader should dispatch to. Listing 'worker'
        would let Leader spawn the OpenSpace executor directly,
        bypassing Ari's triage logic. Both paths would create cycles.
        """
        meta_path = LEADER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            leader_meta = json.load(f)

        team_members = leader_meta.get("team_members", [])

        assert "ari" not in team_members, (
            f"Leader should NOT have 'ari' in team_members (Ari is the front door, "
            f"not a Leader dispatch target), got: {team_members}"
        )
        assert "worker" not in team_members, (
            f"Leader should NOT have 'worker' in team_members (Worker is dispatched "
            f"via Ari's job queue, not via Leader), got: {team_members}"
        )


# =============================================================================
# 5. Prompt Composition (Both Agents)
# =============================================================================


class TestPromptCompositionBoth:
    """Tests that both Ari's and Worker's innate skills load into the composed prompt.

    Exercises the full load_agent_prompts → load_agent_skills →
    compose_system_prompt pipeline for each agent. Verifies that the
    innate_skills declared in meta.json actually produce content in the
    composed system prompt — i.e., the loader accepts the agent's
    structure and the skill files exist at the centralized
    _prompt_system/innate-skills/ location.
    """

    def test_ari_composes_prompt_with_innate_skills(self) -> None:
        """Ari's innate_skills (job-orchestration, openspace, chart, todo)
        must load and appear in the composed system prompt.

        Mirrors the gold-standard pattern from test_openspace_skill_loading.py:
        read the actual meta.json, pass it to load_agent_skills(), then
        compose_system_prompt() and verify the skill content is present.
        """
        from daemon.loader import (
            compose_system_prompt,
            load_agent_prompts,
            load_agent_skills,
        )

        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # Ari's documented innate_skills
        expected_skills = {"job-orchestration", "openspace", "chart", "todo"}
        assert set(meta.get("innate_skills", [])) == expected_skills, (
            f"Precondition failed: Ari innate_skills should be {expected_skills}, "
            f"got {set(meta.get('innate_skills', []))}"
        )

        # Load and compose
        skills = load_agent_skills(ARI_AGENT_DIR, meta)
        for skill_name in expected_skills:
            assert skill_name in skills, (
                f"{skill_name} skill should be loaded for Ari, "
                f"got: {list(skills.keys())}"
            )
            assert isinstance(skills[skill_name], str)
            assert len(skills[skill_name]) > 0, (
                f"{skill_name} skill content should be non-empty"
            )

        prompts = load_agent_prompts(ARI_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts, skills)

        # The composed prompt must include the skill content — sanity-check
        # that the skill section actually made it into the final output.
        assert isinstance(system_prompt, str)
        assert len(system_prompt) > 0, "Composed system prompt should not be empty"
        # OpenSpace is in both agents' skills; verify the heading marker
        assert "OpenSpace-Skill" in system_prompt, (
            "Composed Ari prompt should contain the OpenSpace-Skill heading "
            "(from the openspace innate skill)"
        )

    def test_worker_composes_prompt_with_innate_skills(self) -> None:
        """Worker's innate_skills (dynamic-skill, todo) must load and appear in the
        composed system prompt.

        Mirrors the test_openspace_skill_loading.py end-to-end pipeline.
        Worker is a smaller agent than Ari (2 innate_skills vs 4), so
        this is a focused check that the loader handles a minimal
        innate_skills list correctly.
        """
        from daemon.loader import (
            compose_system_prompt,
            load_agent_prompts,
            load_agent_skills,
        )

        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # Worker's documented innate_skills (migrated from openspace to dynamic-skill)
        expected_skills = {"dynamic-skill", "todo"}
        assert set(meta.get("innate_skills", [])) == expected_skills, (
            f"Precondition failed: Worker innate_skills should be {expected_skills}, "
            f"got {set(meta.get('innate_skills', []))}"
        )

        # Load and compose
        skills = load_agent_skills(WORKER_AGENT_DIR, meta)
        for skill_name in expected_skills:
            assert skill_name in skills, (
                f"{skill_name} skill should be loaded for Worker, "
                f"got: {list(skills.keys())}"
            )
            assert isinstance(skills[skill_name], str)
            assert len(skills[skill_name]) > 0, (
                f"{skill_name} skill content should be non-empty"
            )

        prompts = load_agent_prompts(WORKER_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts, skills)

        assert isinstance(system_prompt, str)
        assert len(system_prompt) > 0, "Composed system prompt should not be empty"
        # The dynamic-skill skill teaches Worker about the 6 skill tools
        assert "Dynamic Skill System" in system_prompt, (
            "Composed Worker prompt should contain the Dynamic Skill System heading "
            "(from the dynamic-skill innate skill)"
        )


# =============================================================================
# 6. Autonomy Model in Prompts
# =============================================================================


class TestAutonomyModelInPrompts:
    """Tests that each agent's autonomy model is documented in its prompt.

    Ari operates in **TrueAuto** (autonomous decision-making by default,
    stops only for breaking/critical things). Worker operates in
    **SemiAuto** (stops for breaking changes, requests permission from
    its dispatcher). This is the safety contract that makes the
    Ari → Worker job_create chain safe: Worker never silently
    overwrites/deletes, and Ari grants TrueAuto overrides via
    job_continue when appropriate.

    These tests verify the autonomy labels appear in the COMPOSED system
    prompt (not just the raw soul.md file), proving they survive the
    full prompt-composition pipeline.
    """

    def test_ari_prompt_mentions_trueauto(self) -> None:
        """Ari's composed system prompt must mention TrueAuto.

        Ari's soul.md declares "# My Autonomy: TrueAuto (DEFAULT)" and
        rule.md/soul.md/workflow.md/user.md all reference TrueAuto
        throughout. The composed prompt must include this autonomy
        contract — otherwise the agent would have no documented
        decision-making posture.
        """
        from daemon.loader import compose_system_prompt, load_agent_prompts, load_agent_skills

        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        prompts = load_agent_prompts(ARI_AGENT_DIR)
        skills = load_agent_skills(ARI_AGENT_DIR, meta)
        system_prompt = compose_system_prompt(prompts, skills)

        assert "TrueAuto" in system_prompt, (
            "Ari's composed system prompt must contain 'TrueAuto' — "
            "her default autonomy mode. If missing, the autonomy contract "
            "is not reaching the agent."
        )

    def test_worker_prompt_mentions_semiauto(self) -> None:
        """Worker's composed system prompt must mention SemiAuto.

        Worker's soul.md declares "## My Autonomy: SemiAuto (DEFAULT)"
        and rule.md/workflow.md repeatedly reference the SemiAuto
        safety gate. The composed prompt must include this — Worker is
        the safety net for the entire ensemble, and SemiAuto is what
        makes it a safety net rather than an autonomous operator.
        """
        from daemon.loader import compose_system_prompt, load_agent_prompts, load_agent_skills

        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        prompts = load_agent_prompts(WORKER_AGENT_DIR)
        skills = load_agent_skills(WORKER_AGENT_DIR, meta)
        system_prompt = compose_system_prompt(prompts, skills)

        assert "SemiAuto" in system_prompt, (
            "Worker's composed system prompt must contain 'SemiAuto' — "
            "his default safety-gated autonomy mode. If missing, the "
            "breaking-change permission gate is not documented in the prompt."
        )