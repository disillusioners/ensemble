"""Tests for the skill-evolution lane gate + skill-keeper confinement.

Incident 2026-10-21 (§5 L1/L2) enforcement pair — 2026-10-22:

* **L1 allowlist over-grant** — ``agents/skill-keeper/meta.json`` held
  ``bash`` + ``filesystem`` (write-capable) on a DB-only agent; the
  only write guard covered ``*.md`` prompt artifacts and bash
  bypassed everything. Fix: allowlist stripped to a READ-ONLY
  filesystem subset (``read_file`` / ``list_directory`` /
  ``glob_files`` / ``grep_files``), plus an explicit ``tools.deny``
  for the mutation-capable categories/tools (``bash``, ``proc``,
  ``service``, ``write_file``, ``edit_file``). ``service`` is denied
  too: ``service_start`` executes arbitrary argv — a bash-equivalent
  escape hatch (``git``/``python3``/``bash -c`` all work through it).
* **L2 ungated evolution lane** — ``SkillJobDispatcher.enqueue_evolution``
  was the ONE skill-job admission lane with no switch, no dedup, no
  cooldown (10 skill_fix calls → 10 Tier-3 jobs; same skill re-FIXed
  3× in 38 min). Fix: master kill-switch
  (``ENSEMBLE_SKILL_EVOLUTION_ENABLED`` env, winning over the
  ``SkillEvolutionConfig.evolution_lane_enabled`` configured default,
  itself False), per-(project, skill) FIX cooldown (default 1800s),
  and an ``idempotency_key`` non-terminal dedup on the DB row.
* **Docstring truthfulness** — the ``skill_fix`` tool claimed "only
  records the request" while immediately dispatching a Tier-3
  mutation job. The tool now reports the admission outcome honestly
  (dispatched + job id, or NOT dispatched + reason).
* **Forced A/B resolve guard** — during the incident a keeper run
  force-resolved a LIVE experiment at comparisons=2/10 via the admin
  endpoint, killing the incumbent arm. ``check_ab_test_resolution``
  now refuses a forced winner while ``comparisons < ab_sample_size``.

Out of scope (NOT gated, deliberate): ``enqueue_analysis`` (Tier-2
read-only analysis jobs), ``enqueue_metric_scan`` (the scan itself),
the CAPTURED lane (its own ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` switch,
default OFF), and the skill-keeper's DIRECT execution tools
(``skill_evolve`` / ``skill_analyze`` run inline on an
already-running keeper — the job-level gate stops ADMISSION and
queued-EXECUTION (terminally), but cannot reach an inline tool call
on a keeper instance that is already running).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
KEEPER_META_PATH = REPO_ROOT / "agents" / "skill-keeper" / "meta.json"
KEEPER_SOUL_PATH = REPO_ROOT / "agents" / "skill-keeper" / "soul.md"

# The READ-ONLY filesystem surface granted to skill-keeper (the whole
# read half of the ``filesystem`` category — write_file / edit_file
# are the only mutating tools in it).
READ_ONLY_FS_TOOLS = ("read_file", "list_directory", "glob_files", "grep_files")
WRITE_CAPABLE_TOOLS = ("write_file", "edit_file", "inner_soul")
# The categories the skill-keeper allow/deny lists touch — resolved
# against the PRODUCTION registry (see ``_production_category_map``).
_REGISTRY_CATEGORIES = (
    "bash",
    "proc",
    "service",
    "filesystem",
    "self",
    "dynamic-skill",
    "skill-evolution",
    "knowledge",
    "shared_meta_kv",
)


def _production_category_map() -> dict[str, list[str]]:
    """Build the category→tool-names map from the PRODUCTION registry.

    NO hand-invented names (2026-10-22 review M-3): the map is derived
    from ``daemon.tools._tool_registry.CATEGORY_MODULES`` (the
    registry's own category→module ownership map) plus the real tool
    objects those modules expose — module-level ``@tool`` functions and
    the tools the production factories create (factory-internal tools
    never register at import time).

    The inner_soul factory runs an archival sweep over the agent's
    memories directory at CREATION time; it is fenced to a no-op here
    so this helper can never mutate the repo tree.
    """
    import importlib
    from unittest.mock import MagicMock

    import daemon.tools.inner_soul as inner_soul_module
    from daemon.tools._tool_registry import CATEGORY_MODULES

    categories: dict[str, list[str]] = {}

    def _add(category_key: str, obj: Any) -> None:
        if getattr(obj, "_tool_category", None) != category_key:
            return
        name = getattr(obj, "name", None) or getattr(obj, "__name__", None)
        if not name:
            return
        bucket = categories.setdefault(category_key, [])
        if name not in bucket:
            bucket.append(name)

    # 1) Module-level tools (filesystem, bash, ...).
    for category_key in _REGISTRY_CATEGORIES:
        module_paths = CATEGORY_MODULES.get(category_key)
        if not module_paths:
            continue
        paths = (
            module_paths if isinstance(module_paths, list) else [module_paths]
        )
        for path in paths:
            try:
                module = importlib.import_module(path)
            except Exception:  # pragma: no cover — import guard
                continue
            for attr in dir(module):
                _add(category_key, getattr(module, attr, None))

    # 2) Factory-created tools (self, dynamic-skill, skill-evolution,
    #    knowledge, proc, service, shared_meta_kv).
    manager = MagicMock()
    from daemon.tools.access_memory import create_access_memory_tool
    from daemon.tools.help import create_help_tool
    from daemon.tools.inner_soul import create_inner_soul_tool
    from daemon.tools.knowledge_tools import create_knowledge_tools
    from daemon.tools.proc_tools import create_proc_tools
    from daemon.tools.service_tools import create_service_tools
    from daemon.tools.shared_meta_kv_tools import create_shared_meta_kv_tools
    from daemon.tools.skill_evolution_tools import create_skill_evolution_tools
    from daemon.tools.skill_tools import create_skill_tools

    # Never mutate the repo tree from a test: the inner_soul factory
    # archives old memory files at creation time.
    inner_soul_module._archive_old_memories = lambda *a, **k: 0

    for tool in create_skill_tools(manager, "inst-1"):
        _add("dynamic-skill", tool)
    for tool in create_skill_evolution_tools(manager, "inst-1"):
        _add("skill-evolution", tool)
    for tool in create_knowledge_tools(manager, "inst-1", "skill-keeper"):
        _add("knowledge", tool)
    for tool in create_proc_tools("inst-1"):
        _add("proc", tool)
    for tool in create_service_tools(manager, "inst-1"):
        _add("service", tool)
    for tool in create_shared_meta_kv_tools(manager, "inst-1"):
        _add("shared_meta_kv", tool)
    for tool in [create_access_memory_tool("skill-keeper")]:
        _add("self", tool)
    for tool in [create_inner_soul_tool(manager, "skill-keeper", "inst-1")]:
        _add("self", tool)

    return categories


# =============================================================================
# Fixtures
# =============================================================================


class FakeJob:
    def __init__(self, job_id: str):
        self.job_id = job_id


def _make_job_service(job_id: str = "job-1") -> Any:
    svc = MagicMock()
    svc.enqueue = AsyncMock(return_value=FakeJob(job_id))
    return svc


def _make_queue_repo() -> Any:
    repo = MagicMock()
    q = SimpleNamespace(queue_id="q-parallel-1")
    repo.get_by_name = MagicMock(return_value=q)
    return repo


def _make_dispatcher(
    job_service: Any | None = None,
    evolution_lane_enabled: bool = False,
    fix_cooldown_seconds: int = 1800,
) -> Any:
    from daemon.services.skill_job_dispatcher import SkillJobDispatcher

    return SkillJobDispatcher(
        job_service=job_service if job_service is not None else _make_job_service(),
        queue_repo=_make_queue_repo(),
        evolution_lane_enabled=evolution_lane_enabled,
        fix_cooldown_seconds=fix_cooldown_seconds,
    )


@pytest.fixture
def gate_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the evolution-lane kill-switch ON for the calling test."""
    monkeypatch.setenv("ENSEMBLE_SKILL_EVOLUTION_ENABLED", "1")


@pytest.fixture
def gate_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the evolution-lane kill-switch explicitly OFF."""
    monkeypatch.setenv("ENSEMBLE_SKILL_EVOLUTION_ENABLED", "0")


# =============================================================================
# 1. Resolver semantics
# =============================================================================


class TestEvolutionGateResolver:
    def test_env_name_is_the_documented_kill_switch(self):
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
        )

        assert SKILL_EVOLUTION_KILL_SWITCH_ENV == "ENSEMBLE_SKILL_EVOLUTION_ENABLED"

    def test_unset_defers_to_configured_default_false(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
            _resolve_evolution_enabled,
        )

        monkeypatch.delenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, raising=False)
        assert _resolve_evolution_enabled(False) is False
        assert _resolve_evolution_enabled(True) is True

    def test_empty_string_defers_to_configured_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
            _resolve_evolution_enabled,
        )

        monkeypatch.setenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, "   ")
        assert _resolve_evolution_enabled(False) is False
        assert _resolve_evolution_enabled(True) is True

    @pytest.mark.parametrize("raw", ["1", "true", "YES", "On"])
    def test_on_values_win_over_configured_off(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ):
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
            _resolve_evolution_enabled,
        )

        monkeypatch.setenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, raw)
        assert _resolve_evolution_enabled(False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "No", "OFF"])
    def test_off_values_win_over_configured_on(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ):
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
            _resolve_evolution_enabled,
        )

        monkeypatch.setenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, raw)
        assert _resolve_evolution_enabled(True) is False

    def test_invalid_value_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
            _resolve_evolution_enabled,
        )

        monkeypatch.setenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, "banana")
        with pytest.raises(ValueError, match="ENSEMBLE_SKILL_EVOLUTION_ENABLED"):
            _resolve_evolution_enabled(False)

    def test_config_field_defaults_off(self):
        """``SkillEvolutionConfig.evolution_lane_enabled`` defaults False."""
        from daemon.config import SkillEvolutionConfig

        cfg = SkillEvolutionConfig()
        assert cfg.evolution_lane_enabled is False
        # Cooldown default: 30 minutes (the 2026-10-21 burst re-FIXed
        # one skill 3× in 38 minutes).
        assert cfg.fix_cooldown_seconds == 1800


# =============================================================================
# 2. Gate behavior at the dispatcher chokepoint
# =============================================================================


class TestEnqueueEvolutionGate:
    async def test_gate_off_nops_every_evolution_type(
        self, gate_off
    ):
        """Gate OFF → ``None`` for FIX/DERIVED/CAPTURED, zero enqueue calls."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service)
        for evo_type in ("FIX", "DERIVED", "CAPTURED"):
            result = await d.enqueue_evolution(
                project_id="p1", skill_id="s1",
                evolution_type=evo_type, direction="d",
            )
            assert result is None, f"{evo_type} must be refused when gate OFF"
        assert job_service.enqueue.await_count == 0

    async def test_feedback_filed_fix_request_refused_when_gate_off(
        self, gate_off
    ):
        """The post-execution feedback producer is covered by the gate.

        Incident 2026-10-21 (addendum evidence): worker feedback
        (``skill_feedback`` improvement_note) seeded a fix request that
        reached the lane within seconds. Every feedback-filed fix
        funnels through ``dispatch_fix`` (skill_fix tool / HTTP
        endpoint) → ``enqueue_evolution`` — pin that the WHOLE funnel
        is a no-op when the gate is OFF.
        """
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service)
        result = await d.dispatch_fix(
            project_id="p1",
            skill_id="s1",
            issue_description="improvement note from skill_feedback",
            suggested_fix="",
        )
        assert result is None
        assert job_service.enqueue.await_count == 0

    async def test_env_unset_uses_configured_enabled_true(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Constructed ``evolution_lane_enabled=True`` + env unset → ON."""
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
        )

        monkeypatch.delenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, raising=False)
        job_service = _make_job_service()
        d = _make_dispatcher(
            job_service=job_service, evolution_lane_enabled=True
        )
        result = await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="DERIVED", direction="d",
        )
        assert result == "job-1"
        assert job_service.enqueue.await_count == 1

    async def test_env_off_overrides_configured_enabled_true(
        self, gate_off
    ):
        """The env kill-switch WINS over a config-enabled lane (emergency off)."""
        job_service = _make_job_service()
        d = _make_dispatcher(
            job_service=job_service, evolution_lane_enabled=True
        )
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d",
        ) is None
        assert job_service.enqueue.await_count == 0

    async def test_env_on_overrides_configured_disabled_false(
        self, gate_on
    ):
        """The env opt-in WINS over the default-OFF config."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service)  # config default False
        result = await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d",
        )
        assert result == "job-1"


# =============================================================================
# 3. FIX abuse controls (cooldown + idempotency)
# =============================================================================


class TestFixAbuseControls:
    async def test_fix_carries_idempotency_key(self, gate_on):
        """FIX admissions carry ``skill_fix:<project>:<skill>`` dedup key."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service)
        await d.enqueue_evolution(
            project_id="proj-1", skill_id="skill-abc",
            evolution_type="FIX", direction="d",
        )
        kwargs = job_service.enqueue.await_args.kwargs
        assert kwargs["idempotency_key"] == "skill_fix:proj-1:skill-abc"

    async def test_non_fix_carries_no_idempotency_key(self, gate_on):
        """Tier-2-style lanes (analysis) and non-FIX evolutions don't dedup."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service)
        await d.enqueue_analysis(project_id="p1", skill_id="s1")
        await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="DERIVED", direction="d",
        )
        for call in job_service.enqueue.await_args_list:
            assert call.kwargs["idempotency_key"] is None

    async def test_repeat_fix_inside_cooldown_refused(self, gate_on):
        """The 2026-10-21 amplifier class: same skill re-FIXed minutes apart."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service, fix_cooldown_seconds=1800)
        first = await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="first",
        )
        assert first == "job-1"
        second = await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="sweep re-file",
        )
        assert second is None, "re-FIX inside the cooldown window must refuse"
        assert job_service.enqueue.await_count == 1

    async def test_repeat_fix_via_dispatch_fix_refused_inside_cooldown(
        self, gate_on
    ):
        """The sweep re-filed through skill_fix → dispatch_fix; same refusal."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service, fix_cooldown_seconds=1800)
        assert await d.dispatch_fix("p1", "s1", "first issue") == "job-1"
        assert await d.dispatch_fix("p1", "s1", "re-filed same class") is None
        assert job_service.enqueue.await_count == 1

    async def test_different_skill_not_blocked_by_cooldown(self, gate_on):
        """Cooldown is per (project, skill) — distinct skills flow."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service, fix_cooldown_seconds=1800)
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d",
        ) == "job-1"
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s2",
            evolution_type="FIX", direction="d",
        ) == "job-1"
        assert job_service.enqueue.await_count == 2

    async def test_derived_not_blocked_by_fix_cooldown(self, gate_on):
        """The cooldown keys on FIX admissions; DERIVED stays open."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service, fix_cooldown_seconds=1800)
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="d",
        ) == "job-1"
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="DERIVED", direction="d",
        ) == "job-1"
        assert job_service.enqueue.await_count == 2

    async def test_zero_cooldown_disables_the_window(self, gate_on):
        """``fix_cooldown_seconds=0`` → no cooldown (idempotency still set)."""
        job_service = _make_job_service()
        d = _make_dispatcher(job_service=job_service, fix_cooldown_seconds=0)
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="first",
        ) == "job-1"
        assert await d.enqueue_evolution(
            project_id="p1", skill_id="s1",
            evolution_type="FIX", direction="second",
        ) == "job-1"
        assert job_service.enqueue.await_count == 2


# =============================================================================
# 4. skill_fix tool truthfulness (docstring + behavior)
# =============================================================================


class TestSkillFixTruthfulness:
    def _tools(self, manager: Any) -> dict[str, Any]:
        from daemon.tools.skill_tools import create_skill_tools

        return {t.name: t for t in create_skill_tools(manager, "inst-1")}

    def test_docstring_no_longer_claims_record_only(self):
        """The old docstring LIED ("only records the request"). Dead. Buried."""
        import daemon.tools.skill_tools as st

        full_doc = st.create_skill_tools.__doc__ or ""
        # The tool function itself is created inside the factory; read
        # the source instead of introspecting a closure.
        source = Path(st.__file__).read_text(encoding="utf-8")
        assert "It only\n        records the request" not in source
        assert "only records the request" not in source
        assert "NEVER performs the fix inline. It only" not in source
        # The truthful dispatch language is present.
        assert "IMMEDIATELY" in source or "immediately" in source
        assert "ENSEMBLE_SKILL_EVOLUTION_ENABLED" in source
        assert full_doc  # factory docstring sanity

    async def test_tool_reports_not_dispatched_when_gate_off(self, gate_off):
        manager = MagicMock()
        dispatcher = MagicMock()
        dispatcher.dispatch_fix = AsyncMock(return_value=None)
        manager._skill_search_service = None
        manager._skill_store_service = None
        manager._skill_job_dispatcher = dispatcher
        manager._instance_repository = None

        result = await self._tools(manager)["skill_fix"].ainvoke({
            "skill_id": "sk-x",
            "issue_description": "broken",
        })
        assert "NOT dispatched" in result
        assert "NO skill-keeper job was created" in result
        # The old optimistic lie must be gone.
        assert "will analyze this when available" not in result

    async def test_tool_reports_job_id_when_dispatched(self, gate_on):
        manager = MagicMock()
        dispatcher = MagicMock()
        dispatcher.dispatch_fix = AsyncMock(return_value="job-42")
        manager._skill_search_service = None
        manager._skill_store_service = None
        manager._skill_job_dispatcher = dispatcher
        manager._instance_repository = None

        result = await self._tools(manager)["skill_fix"].ainvoke({
            "skill_id": "sk-x",
            "issue_description": "broken",
        })
        assert "Dispatched" in result
        assert "job-42" in result
        assert "NOT dispatched" not in result

    async def test_tool_dispatcher_missing_still_reports_record_only(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The Phase-2 interregnum path (no dispatcher) keeps its wording."""
        from daemon.services.skill_job_dispatcher import (
            SKILL_EVOLUTION_KILL_SWITCH_ENV,
        )

        monkeypatch.delenv(SKILL_EVOLUTION_KILL_SWITCH_ENV, raising=False)
        manager = MagicMock()
        manager._skill_search_service = None
        manager._skill_store_service = None
        manager._skill_job_dispatcher = None
        manager._instance_repository = None

        result = await self._tools(manager)["skill_fix"].ainvoke({
            "skill_id": "sk-x",
            "issue_description": "broken",
        })
        assert "Skill fix request recorded" in result
        assert "dispatcher not yet available" in result


# =============================================================================
# 5. Allowlist confinement (meta.json + resolved filter)
# =============================================================================


class TestSkillKeeperAllowlistConfinement:
    def _meta(self) -> dict[str, Any]:
        return json.loads(KEEPER_META_PATH.read_text(encoding="utf-8"))

    def test_no_repo_mutation_categories_in_allow(self):
        allow = self._meta()["tools"]["allow"]
        for mutation in ("bash", "filesystem", "service", "proc"):
            assert mutation not in allow, (
                f"{mutation!r} must NOT be in skill-keeper's allow list "
                f"(DB-only agent): {allow}"
            )

    def test_no_write_tools_in_allow(self):
        allow = self._meta()["tools"]["allow"]
        for tool_name in WRITE_CAPABLE_TOOLS:
            assert tool_name not in allow

    def test_read_only_surface_present(self):
        allow = self._meta()["tools"]["allow"]
        for tool_name in READ_ONLY_FS_TOOLS:
            assert tool_name in allow, (
                f"audit duties need the read-only tool {tool_name!r}"
            )
        # The DB/coordination surface survives (its legitimate lane).
        for kept in (
            "self", "help", "knowledge", "dynamic-skill",
            "skill-evolution", "shared_meta_kv",
        ):
            assert kept in allow

    def test_deny_defends_against_allow_regression(self):
        """Deny wins over allow in resolve_tool_filter — pin the fence."""
        deny = self._meta()["tools"].get("deny", [])
        for mutation in (
            "bash", "proc", "service", "write_file", "edit_file", "inner_soul",
        ):
            assert mutation in deny

    def test_resolved_filter_grants_no_write_capable_tool(self):
        """End-to-end: resolve skill-keeper's allow/deny through the
        production filter AND the PRODUCTION registry map — the
        resolved set must contain ZERO write-capable tools and the
        full read-only surface."""
        from daemon.tools.instance import resolve_tool_filter

        category_map = _production_category_map()
        meta = self._meta()["tools"]
        resolved = resolve_tool_filter(
            allow=meta["allow"],
            deny=meta.get("deny"),
            tool_categories=category_map,
        )
        assert resolved is not None
        for tool_name in WRITE_CAPABLE_TOOLS:
            assert tool_name not in resolved
        assert "bash" not in resolved
        assert not (resolved & set(category_map["proc"]))
        assert not (resolved & set(category_map["service"]))
        for tool_name in READ_ONLY_FS_TOOLS:
            assert tool_name in resolved
        assert "skill_view" in resolved  # audit-duty read path survives

    def test_memory_read_preserved_after_inner_soul_denial(self):
        """M-1: denying ``inner_soul`` must NOT take ``access_memory``
        with it — the keeper keeps READ access to its own memories.

        ``inner_soul`` is denied by exact tool name; ``access_memory``
        is a DIFFERENT tool in the same ``self`` category, so the
        deny-list (deny wins, name-scoped) leaves it in the resolved
        set.
        """
        from daemon.tools.instance import resolve_tool_filter

        category_map = _production_category_map()
        # The production registry really does expose both names.
        assert "inner_soul" in category_map["self"]
        assert "access_memory" in category_map["self"]

        meta = self._meta()["tools"]
        resolved = resolve_tool_filter(
            allow=meta["allow"],
            deny=meta.get("deny"),
            tool_categories=category_map,
        )
        assert resolved is not None
        assert "inner_soul" not in resolved, (
            "inner_soul writes agents/<agent>/memories/** — on live "
            "installs that is releases/<ver>/agents/** (the recurring "
            "agent-self-mod incident class)"
        )
        assert "access_memory" in resolved, (
            "memory READ must survive the inner_soul denial"
        )

    def test_meta_version_bumped(self):
        """The confinement is a behavior change — meta version must move."""
        assert self._meta()["version"] != "0.1.0"


# =============================================================================
# 6. Charter fencing (soul.md consistency with the strip)
# =============================================================================


class TestSkillKeeperCharterFencing:
    def test_old_bash_filesystem_section_is_gone(self):
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        assert "### `bash`, `filesystem`" not in soul

    def test_charter_names_the_structural_strip(self):
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        # The section must name the actual read-only tools...
        for tool_name in READ_ONLY_FS_TOOLS:
            assert tool_name in soul
        # ...and state that writes/bash/inner_soul are denied at the
        # tool layer (M-1 added inner_soul to the deny list).
        assert "write_file" in soul
        assert "edit_file" in soul
        assert "inner_soul" in soul
        assert "tools.deny" in soul
        assert "no tool i hold can write" in soul.lower()

    def test_must_not_section_carries_the_repo_mutation_prohibition(self):
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        assert "❌ Mutate the repository" in soul
        assert "NO bash" in soul
        assert "NO inner_soul" in soul
        assert "read-only evidence" in soul

    def test_charter_does_not_claim_shell_access(self):
        """No surviving line may tell the keeper it has bash."""
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        assert "### `bash`" not in soul
        # The old phrasing granted shell "when an audit demands" — gone.
        assert "run migrations when an audit demands" not in soul

    def test_charter_uses_real_tool_names_only(self):
        """HIGH-2: the charter must name tools that EXIST.

        The phantom names (``skill_update``, ``analyze_skill``,
        ``evolve_skill``, ``resolve_ab_test``, ``get_skill_metrics``,
        ``list_captured_opportunities``) are gone — the real registry
        names are in their place.
        """
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        for phantom in (
            "skill_update",
            "analyze_skill",
            "evolve_skill",
            "resolve_ab_test",
            "get_skill_metrics",
            "list_captured_opportunities",
        ):
            assert phantom not in soul, f"phantom tool name {phantom!r}"
        for real in (
            "skill_analyze",
            "skill_evolve",
            "skill_resolve_ab",
            "skill_get_metrics",
            "skill_execute_capture",
        ):
            assert real in soul, f"real tool name {real!r} must be listed"

    def test_charter_states_read_reach_is_not_workdir_fenced(self):
        """M-5: the charter must be accurate about READ reach —
        absolute paths are honored, so reads are not confined to the
        project. The over-claim ("read-only = confined") is corrected
        in prose; the scope feature itself is NOT built here.
        """
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        assert "ABSOLUTE paths" in soul
        assert "not fenced to the repo" in soul
        assert "filesystem_scope" in soul


# =============================================================================
# 7. Forced A/B resolve guard (service Path 0)
# =============================================================================


def _make_evolution_service(
    ab_test_row: Any,
    stats: dict[str, Any],
    sample_size: int = 10,
) -> Any:
    """A MagicMock-harness SkillEvolutionService for check_ab_test_resolution.

    Only the Path-0 forced-resolve surface is wired: config knob, the
    A/B row (source of truth for ``comparisons``), the comparison
    stats, and MagicMock side-effect repos (deactivate / resolve /
    update) so refusals can assert ZERO side effects.
    """
    from daemon.services.skill_evolution_service import SkillEvolutionService

    svc = SkillEvolutionService.__new__(SkillEvolutionService)
    svc._config = SimpleNamespace(
        ab_sample_size=sample_size,
        ab_min_difference=0.15,
        max_extensions=3,
    )
    svc._ab_test_repo = MagicMock()
    svc._ab_test_repo.get_by_group = MagicMock(return_value=ab_test_row)
    svc._ab_test_repo.resolve = MagicMock()
    svc._skill_repo = MagicMock()
    svc._skill_repo.deactivate = MagicMock()
    svc._skill_repo.update = MagicMock()
    metrics = MagicMock()
    metrics.get_ab_comparison_stats = AsyncMock(return_value=stats)
    svc._metrics_service = metrics
    return svc


class TestForcedABResolveGuard:
    A_GROUP = "g-forced-guard"

    def _row(self, comparisons: int) -> Any:
        return SimpleNamespace(
            ab_test_group=self.A_GROUP,
            comparisons=comparisons,
            extension_count=0,
        )

    def _stats(self) -> dict[str, Any]:
        return {
            "skill_id_a": "skill-old",
            "skill_id_b": "skill-new",
            "completion_rate_a": 0.5,
            "completion_rate_b": 0.9,
            "difference": 0.4,
            "comparisons": 2,
        }

    async def test_forced_resolve_below_sample_size_refused(self):
        """comparisons=2/10 + winner_id → ValueError, ZERO side effects.

        This is the incident shape: a keeper force-resolved a live
        experiment at 2/10 comparisons and killed the incumbent arm.
        """
        svc = _make_evolution_service(self._row(2), self._stats())
        with pytest.raises(ValueError, match="Forced A/B resolve refused"):
            await svc.check_ab_test_resolution(self.A_GROUP, winner_id="skill-new")
        svc._skill_repo.deactivate.assert_not_called()
        svc._ab_test_repo.resolve.assert_not_called()
        svc._skill_repo.update.assert_not_called()

    async def test_forced_resolve_at_sample_size_admitted(self):
        """The operator-authority contract survives at/above sample size."""
        svc = _make_evolution_service(self._row(10), self._stats())
        result = await svc.check_ab_test_resolution(self.A_GROUP, winner_id="skill-new")
        assert result["resolved"] is True
        assert result["reason"] == "forced_winner"
        assert result["winner_id"] == "skill-new"
        assert result["loser_id"] == "skill-old"
        svc._skill_repo.deactivate.assert_called_once()
        svc._ab_test_repo.resolve.assert_called_once()

    async def test_unforced_resolve_below_sample_size_still_waits(self):
        """The normal decision tree is untouched (needs_more_data)."""
        svc = _make_evolution_service(self._row(2), self._stats())
        result = await svc.check_ab_test_resolution(self.A_GROUP)
        assert result["resolved"] is False
        assert result["reason"] == "needs_more_data"
        svc._skill_repo.deactivate.assert_not_called()

    async def test_unknown_winner_variant_still_400s_first(self):
        """Variant validation precedes the threshold guard (same 400 class)."""
        svc = _make_evolution_service(self._row(2), self._stats())
        with pytest.raises(ValueError, match="not a variant"):
            await svc.check_ab_test_resolution(self.A_GROUP, winner_id="skill-nope")

# =============================================================================
# 8. Manager wiring (review iteration-1 pins)
# =============================================================================


class TestManagerDispatcherWiring:
    """``InstanceManager.set_job_queue_service`` must pass the config
    knobs into the dispatcher faithfully.

    Review finding 2: the former ``or 1800`` truthiness coercion
    silently turned a configured ``fix_cooldown_seconds=0`` back into
    1800, violating the config contract ("0 disables the cooldown").
    Only absent/None falls back to 1800.
    """

    @staticmethod
    def _wire(
        fix_cooldown_seconds: int | None,
        evolution_lane_enabled: bool = False,
    ) -> Any:
        from daemon.config import SkillEvolutionConfig
        from daemon.manager import InstanceManager

        mgr = SimpleNamespace()
        mgr.config = SimpleNamespace(
            skill_evolution=SkillEvolutionConfig(
                evolution_lane_enabled=evolution_lane_enabled,
                **(
                    {}
                    if fix_cooldown_seconds is None
                    else {"fix_cooldown_seconds": fix_cooldown_seconds}
                ),
            )
        )
        svc = MagicMock()
        svc._queue_repo = MagicMock()
        InstanceManager.set_job_queue_service(mgr, svc)
        return mgr._skill_job_dispatcher

    def test_explicit_zero_cooldown_survives_wiring(self):
        """``fix_cooldown_seconds=0`` must reach the dispatcher as 0
        (0 disables the cooldown) — NOT be coerced back to 1800."""
        d = self._wire(0)
        assert d._fix_cooldown_seconds == 0

    def test_absent_cooldown_falls_back_to_1800(self):
        """Absent (None) still falls back to the 1800 default."""
        d = self._wire(None)
        assert d._fix_cooldown_seconds == 1800

    def test_configured_lane_flag_reaches_dispatcher(self):
        """The config-declared lane default rides the same wiring."""
        d = self._wire(600, evolution_lane_enabled=True)
        assert d._evolution_lane_enabled is True
        assert d._fix_cooldown_seconds == 600


# =============================================================================
# 9. Execution-time gate (M-2) — job processor + retry carry-through
# =============================================================================


class _ExecJob:
    """Minimal JobItem stand-in for the job-processor execution gate."""

    def __init__(self, job_type: str = "skill_evolution") -> None:
        self.job_id = "job-exec-1"
        self.job_type = job_type
        self.agent_id = "skill-keeper"
        self.project_id = "proj-1"
        self.queue_id = "q-1"
        self.message = "Evolve skill sk-1. Type: FIX. Direction: d"
        self.source = "skill_evolution"
        self.instance_id = None
        self.status = "queued"
        self.admission_state = "queued"


class _ExecQueue:
    def __init__(self) -> None:
        self.queue_id = "q-1"
        self.project_id = "proj-1"
        self.queue_name = "system_parallel_queue"
        self.is_paused = False
        self.concurrency_limit = 5
        self.queue_type = "parallel"


def _make_exec_processor(lane_enabled: bool, job: _ExecJob) -> Any:
    from daemon.services.job_processor import JobProcessor

    queue_service = MagicMock()
    queue_service.complete_job = AsyncMock()
    queue_service.start_job = AsyncMock()
    queue_service._repository.list_pending_by_queue = MagicMock(
        return_value=[job]
    )
    queue_service._repository.list_by_queue = MagicMock(
        return_value=([], 0)
    )

    instance_manager = MagicMock()
    instance_manager.is_skill_evolution_lane_enabled = MagicMock(
        return_value=lane_enabled
    )
    instance_manager.spawn_instance_with_mcp = AsyncMock(
        return_value="instance-123"
    )
    instance_manager.enqueue_message = AsyncMock()
    instance_manager.get_instance = AsyncMock(
        return_value=MagicMock()
    )

    project_repo = MagicMock()
    project_repo.get = MagicMock(
        return_value=SimpleNamespace(job_queue_paused=False)
    )

    queue_repo = MagicMock()
    queue_repo.list_queues_with_admittable_work = MagicMock(
        return_value=[_ExecQueue()]
    )

    return JobProcessor(
        queue_service=queue_service,
        instance_manager=instance_manager,
        project_repo=project_repo,
        queue_repo=queue_repo,
        poll_interval=0.1,
    )


class TestJobProcessorExecutionGate:
    """F1/F4 (review iteration 3): the gate pin runs against the REAL
    ``JobQueueService`` + in-memory SQLite and asserts POST-STATE —
    the refused row is TERMINALLY written (admission_state='done',
    terminal_reason='lane_disabled', no ``failed_at`` so the retry
    engine cannot loop it) and the queue head ADVANCES (a second job
    completes). The first cut of this pin mocked ``complete_job`` and
    asserted only ``await_args`` — blind to the queued-row no-op write
    that was the F1 defect.
    """

    @staticmethod
    def _build_real_service():
        """Real JobQueueService + repos over in-memory SQLite (the
        ``tests/services/test_skill_metric_scan.py`` pattern)."""
        from sqlalchemy import create_engine, event
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        import daemon.repositories.job_queue.models  # noqa: F401

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(eng, "connect")
        def _enable_fk(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        SQLModel.metadata.create_all(eng)

        from daemon.repositories.job_queue.lock_repository import (
            LockRepository,
        )
        from daemon.repositories.job_queue.queue_repository import (
            JobQueueRepository,
        )
        from daemon.repositories.job_queue.repository import JobRepository
        from daemon.services.job_lock_manager import JobLockManager
        from daemon.services.job_queue_service import JobQueueService

        job_repo = JobRepository(eng)
        queue_repo = JobQueueRepository(eng)
        lock_manager = JobLockManager(lock_repo=LockRepository(eng))

        manager = MagicMock()
        manager.is_skill_evolution_lane_enabled = MagicMock(
            return_value=False
        )
        manager.get_instance = AsyncMock(side_effect=KeyError("not in memory"))
        manager.spawn_instance_with_mcp = AsyncMock(return_value="inst-1")
        manager.enqueue_message = AsyncMock(return_value=None)

        service = JobQueueService(
            repository=job_repo,
            lock_manager=lock_manager,
            queue_repo=queue_repo,
            instance_manager=manager,
        )
        return eng, service, manager, job_repo, queue_repo

    @staticmethod
    def _seed_queue(engine, project_id: str) -> str:
        from sqlmodel import Session

        from daemon.repositories.job_queue.models import (
            JobQueue,
            QueueType,
        )

        queue_id = "q-exec-gate"
        with Session(engine) as sess:
            sess.add(
                JobQueue(
                    queue_id=queue_id,
                    project_id=project_id,
                    queue_name="system_parallel_queue",
                    queue_name_lower="system_parallel_queue",
                    queue_type=QueueType.PARALLEL.value,
                    concurrency_limit=5,
                    is_system=True,
                )
            )
            sess.commit()
        return queue_id

    @staticmethod
    def _seed_job(
        engine,
        *,
        job_id: str,
        job_type: str,
        project_id: str,
        queue_id: str,
        created_offset_seconds: int,
    ) -> None:
        from datetime import datetime, timedelta, timezone
        from sqlmodel import Session

        from daemon.repositories.job_queue.models import JobItem

        with Session(engine) as sess:
            sess.add(
                JobItem(
                    job_id=job_id,
                    agent_id="skill-keeper",
                    agent_dir="agents/skill-keeper",
                    message=(
                        f"Evolve skill sk-1. Type: FIX. Direction: d ({job_id})"
                        if job_type == "skill_evolution"
                        else f"plain task ({job_id})"
                    ),
                    source="skill_evolution"
                    if job_type == "skill_evolution"
                    else "api",
                    project_id=project_id,
                    priority=5,
                    job_metadata={"skill_id": "sk-1"}
                    if job_type == "skill_evolution"
                    else {},
                    queue_id=queue_id,
                    job_type=job_type,
                    status="pending",
                    admission_state="queued",
                    created_at=(
                        datetime.now(timezone.utc)
                        + timedelta(seconds=created_offset_seconds)
                    ).isoformat(),
                )
            )
            sess.commit()

    @staticmethod
    def _get_job(engine, job_id: str):
        from sqlmodel import Session

        from daemon.repositories.job_queue.models import JobItem

        with Session(engine) as sess:
            job = sess.get(JobItem, job_id)
            assert job is not None
            sess.refresh(job)
            return job

    async def test_refused_row_is_terminally_written_and_head_advances(self):
        """THE F1 post-state proof: tick 1 terminally writes the gated
        head row; tick 2 admits the SECOND job (no permanent
        head-of-line blocker)."""
        from daemon.services.job_processor import JobProcessor

        eng, service, manager, job_repo, queue_repo = (
            self._build_real_service()
        )
        project_id = "proj-exec-gate"
        queue_id = self._seed_queue(eng, project_id)
        self._seed_job(
            eng,
            job_id="job-tier3-head",
            job_type="skill_evolution",
            project_id=project_id,
            queue_id=queue_id,
            created_offset_seconds=0,
        )
        self._seed_job(
            eng,
            job_id="job-task-second",
            job_type="task",
            project_id=project_id,
            queue_id=queue_id,
            created_offset_seconds=1,
        )

        processor = JobProcessor(
            queue_service=service,
            instance_manager=manager,
            project_repo=MagicMock(
                get=MagicMock(
                    return_value=SimpleNamespace(job_queue_paused=False)
                )
            ),
            queue_repo=queue_repo,
            poll_interval=0.1,
        )

        await processor._process_next_job()  # tick 1: the gated head

        head = self._get_job(eng, "job-tier3-head")
        assert head.admission_state == "done", (
            f"the refused row must be TERMINALLY written, got "
            f"admission_state={head.admission_state!r} (F1: a no-op "
            f"write leaves it queued forever)"
        )
        assert head.terminal_reason == "lane_disabled"
        assert head.failed_at is None, (
            "no failed_at marker => should_retry can never loop the row"
        )
        # The instance must NOT have been spawned for the refused row.
        manager.spawn_instance_with_mcp.assert_not_awaited()

        await processor._process_next_job()  # tick 2: the second job

        second = self._get_job(eng, "job-task-second")
        assert second.admission_state == "active", (
            f"the queue head must ADVANCE — second job admitted, got "
            f"admission_state={second.admission_state!r}"
        )

    async def test_active_orphan_refused_terminally_when_lane_off(self):
        """F2: the ACTIVE-orphan recovery path is covered by the hoisted
        predicate — the row is terminally written and NO instance is
        re-spawned (the direction text never reaches a keeper)."""
        from daemon.services.job_processor import JobProcessor

        eng, service, manager, job_repo, queue_repo = (
            self._build_real_service()
        )
        manager.is_skill_evolution_lane_enabled = MagicMock(
            return_value=False
        )
        project_id = "proj-exec-gate-2"
        queue_id = self._seed_queue(eng, project_id)
        self._seed_job(
            eng,
            job_id="job-tier3-orphan",
            job_type="skill_evolution",
            project_id=project_id,
            queue_id=queue_id,
            created_offset_seconds=0,
        )
        # Force the row into the ACTIVE admission state (orphan shape).
        from sqlmodel import Session

        from daemon.repositories.job_queue.models import JobItem

        with Session(eng) as sess:
            job = sess.get(JobItem, "job-tier3-orphan")
            job.admission_state = "active"
            job.instance_id = "inst-orphan-1"
            sess.add(job)
            sess.commit()

        processor = JobProcessor(
            queue_service=service,
            instance_manager=manager,
            project_repo=MagicMock(
                get=MagicMock(
                    return_value=SimpleNamespace(job_queue_paused=False)
                )
            ),
            queue_repo=queue_repo,
            poll_interval=0.1,
        )

        await processor._process_next_job()

        orphan = self._get_job(eng, "job-tier3-orphan")
        assert orphan.admission_state == "done"
        # NOTE: the ACTIVE path goes through complete_job(FAILED), whose
        # fail semantics stamp ``failed_at`` (unlike the queued-gate
        # path, which deliberately omits it). In production a retry
        # engine could bounce such a row back to queued ONCE — where the
        # queued gate terminally refuses it again with NO failed_at, so
        # the cycle ends; it can never loop indefinitely.
        assert orphan.terminal_reason in ("lane_disabled", None) or (
            orphan.failed_at is not None
        )
        manager.spawn_instance_with_mcp.assert_not_awaited()
        manager.enqueue_message.assert_not_awaited()

    async def test_absent_lane_accessor_fails_closed(self):
        """F3: an instance manager WITHOUT the lane accessor must
        REFUSE (fail-closed), never silently admit."""
        from daemon.services.job_processor import JobProcessor

        eng, service, manager, job_repo, queue_repo = (
            self._build_real_service()
        )
        del manager.is_skill_evolution_lane_enabled  # absent accessor
        project_id = "proj-exec-gate-3"
        queue_id = self._seed_queue(eng, project_id)
        self._seed_job(
            eng,
            job_id="job-tier3-noprobe",
            job_type="skill_evolution",
            project_id=project_id,
            queue_id=queue_id,
            created_offset_seconds=0,
        )

        processor = JobProcessor(
            queue_service=service,
            instance_manager=manager,
            project_repo=MagicMock(
                get=MagicMock(
                    return_value=SimpleNamespace(job_queue_paused=False)
                )
            ),
            queue_repo=queue_repo,
            poll_interval=0.1,
        )

        await processor._process_next_job()

        job = self._get_job(eng, "job-tier3-noprobe")
        assert job.admission_state == "done"
        assert job.terminal_reason == "lane_disabled"

    async def test_lane_probe_error_fails_closed(self):
        """A raising probe must REFUSE (and terminally write), never
        silently execute."""
        from daemon.services.job_processor import JobProcessor

        eng, service, manager, job_repo, queue_repo = (
            self._build_real_service()
        )
        manager.is_skill_evolution_lane_enabled = MagicMock(
            side_effect=RuntimeError("probe down")
        )
        project_id = "proj-exec-gate-4"
        queue_id = self._seed_queue(eng, project_id)
        self._seed_job(
            eng,
            job_id="job-tier3-errprobe",
            job_type="skill_evolution",
            project_id=project_id,
            queue_id=queue_id,
            created_offset_seconds=0,
        )

        processor = JobProcessor(
            queue_service=service,
            instance_manager=manager,
            project_repo=MagicMock(
                get=MagicMock(
                    return_value=SimpleNamespace(job_queue_paused=False)
                )
            ),
            queue_repo=queue_repo,
            poll_interval=0.1,
        )

        await processor._process_next_job()

        job = self._get_job(eng, "job-tier3-errprobe")
        assert job.admission_state == "done"
        assert job.terminal_reason == "lane_disabled"
        manager.spawn_instance_with_mcp.assert_not_awaited()

    async def test_non_skill_job_untouched_when_lane_off(self):
        """Scope: skill_evolution rows ONLY — a plain task job proceeds
        through start_job even with the lane off."""
        from daemon.services.job_processor import JobProcessor

        eng, service, manager, job_repo, queue_repo = (
            self._build_real_service()
        )
        project_id = "proj-exec-gate-5"
        queue_id = self._seed_queue(eng, project_id)
        self._seed_job(
            eng,
            job_id="job-task-plain",
            job_type="task",
            project_id=project_id,
            queue_id=queue_id,
            created_offset_seconds=0,
        )

        processor = JobProcessor(
            queue_service=service,
            instance_manager=manager,
            project_repo=MagicMock(
                get=MagicMock(
                    return_value=SimpleNamespace(job_queue_paused=False)
                )
            ),
            queue_repo=queue_repo,
            poll_interval=0.1,
        )

        await processor._process_next_job()

        job = self._get_job(eng, "job-task-plain")
        assert job.admission_state == "active"


class TestRetryJobCarryThrough:
    """M-2: ``retry_job`` must carry ``job_type`` + ``idempotency_key``
    (a retried skill_evolution job must not silently become a generic
    task job that bypasses the execution-time gate)."""

    async def test_retry_carries_job_type_and_idempotency_key(self):
        from daemon.repositories.job_queue.models import AdmissionState
        from daemon.services.job_queue_service import JobQueueService

        failed = MagicMock()
        failed.job_id = "job-1"
        failed.admission_state = AdmissionState.DONE.value
        failed.failed_at = "2026-10-22T00:00:00+00:00"
        failed.agent_id = "skill-keeper"
        failed.message = "Evolve skill sk-1"
        failed.source = "skill_evolution"
        failed.project_id = "proj-1"
        failed.queue_id = "q-1"
        failed.priority = 4
        failed.job_metadata = {"skill_id": "sk-1"}
        failed.agent_tag = None
        failed.job_type = "skill_evolution"
        failed.idempotency_key = "skill_fix:proj-1:sk-1"

        svc = MagicMock()
        svc._repository.get = MagicMock(return_value=failed)
        svc.enqueue = AsyncMock(return_value=MagicMock(job_id="job-2"))

        result = await JobQueueService.retry_job(svc, "job-1")

        assert result is not None
        kwargs = svc.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "skill_evolution"
        assert kwargs["idempotency_key"] == "skill_fix:proj-1:sk-1"

    async def test_retry_carries_task_type_for_plain_jobs(self):
        from daemon.repositories.job_queue.models import AdmissionState
        from daemon.services.job_queue_service import JobQueueService

        failed = MagicMock()
        failed.admission_state = AdmissionState.DONE.value
        failed.failed_at = "2026-10-22T00:00:00+00:00"
        failed.job_type = "task"
        failed.idempotency_key = None
        svc = MagicMock()
        svc._repository.get = MagicMock(return_value=failed)
        svc.enqueue = AsyncMock(return_value=MagicMock(job_id="job-3"))

        await JobQueueService.retry_job(svc, "job-1")

        kwargs = svc.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "task"
        assert kwargs["idempotency_key"] is None


# =============================================================================
# 10. Switch-token alignment + load-time loud check + type constants
#    (review iteration-2 minors c/d/b)
# =============================================================================


class TestSwitchTokenAlignment:
    def test_capture_and_evolution_share_the_exact_token_sets(self):
        """Minor (c): one parser, one token set — no drift."""
        from daemon.services import skill_job_dispatcher as d

        assert d._SWITCH_ON_TOKENS == frozenset({"1", "true", "yes", "on"})
        assert d._SWITCH_OFF_TOKENS == frozenset({"0", "false", "no", "off"})
        # Both resolvers delegate to the SAME shared parser.
        import inspect

        assert "_resolve_switch" in inspect.getsource(
            d._resolve_capture_enabled
        )
        assert "_resolve_switch" in inspect.getsource(
            d._resolve_evolution_enabled
        )

    def test_both_resolvers_agree_across_the_token_matrix(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from daemon.services import skill_job_dispatcher as d

        matrix = ["1", "true", "yes", "on", "0", "false", "no", "off"]
        for token in matrix:
            monkeypatch.setenv(d.SKILL_CAPTURE_KILL_SWITCH_ENV, token)
            monkeypatch.setenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, token)
            capture = d._resolve_capture_enabled()
            evolution = d._resolve_evolution_enabled(configured_default=False)
            assert capture == evolution, (
                f"token {token!r}: capture={capture} evolution={evolution}"
            )
        # And both fail closed on the same invalid value.
        monkeypatch.setenv(d.SKILL_CAPTURE_KILL_SWITCH_ENV, "banana")
        monkeypatch.setenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, "banana")
        with pytest.raises(ValueError):
            d._resolve_capture_enabled()
        with pytest.raises(ValueError):
            d._resolve_evolution_enabled(False)

    def test_empty_and_whitespace_match_capture_exactly(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Capture-parity ruling (iteration 3, (a)): the capture switch
        does NOT raise on empty/whitespace — it defers to its (hardcoded
        False) default. The evolution switch matches EXACTLY: no raise,
        defer to the configured default. Only unrecognized NON-empty
        values raise — in both switches."""
        from daemon.services import skill_job_dispatcher as d

        for token in ("", "   "):
            monkeypatch.setenv(d.SKILL_CAPTURE_KILL_SWITCH_ENV, token)
            assert d._resolve_capture_enabled() is False
            monkeypatch.setenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, token)
            assert d._resolve_evolution_enabled(False) is False
            assert d._resolve_evolution_enabled(True) is True
        # "banana" raises in BOTH (unchanged).
        monkeypatch.setenv(d.SKILL_CAPTURE_KILL_SWITCH_ENV, "banana")
        with pytest.raises(ValueError):
            d._resolve_capture_enabled()

    def test_alias_surface_is_disclosed_not_eliminated(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Reviewer finding (iteration 3): pydantic's env_prefix mints a
        SECOND switch surface (``SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED``).
        Elimination was probed and is NOT mechanical (validation_alias
        breaks the by-name yaml/test surface; populate_by_name
        re-enables the field name for env lookup), so the ruling's
        fallback applies: BOTH surfaces exist and are pinned here —
        the alias sets the CONSTRUCTED DEFAULT; the canonical var wins
        at every resolution (the resolver reads it per-call first)."""
        from daemon.config import SkillEvolutionConfig
        from daemon.services import skill_job_dispatcher as d

        monkeypatch.delenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, raising=False)
        monkeypatch.delenv(
            "SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED", raising=False
        )
        # The alias var alone sets the constructed default.
        monkeypatch.setenv("SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED", "true")
        cfg = SkillEvolutionConfig()
        assert cfg.evolution_lane_enabled is True
        # ...but the canonical var (unset) still decides at resolution
        # time: the resolver returns the configured default only when
        # the canonical var is unset — here the alias-configured default
        # flows through and the lane IS enabled (the disclosed residual).
        assert d._resolve_evolution_enabled(
            cfg.evolution_lane_enabled
        ) is True
        # The canonical var, once set, wins over BOTH.
        monkeypatch.setenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, "0")
        assert d._resolve_evolution_enabled(
            cfg.evolution_lane_enabled
        ) is False

    def test_load_time_check_logs_error_for_invalid_values(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """Minor (c): an invalid switch value is LOUD at load time."""
        import logging

        from daemon.services import skill_job_dispatcher as d

        monkeypatch.setenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, "banana")
        with caplog.at_level(logging.ERROR, logger=d.logger.name):
            d._log_invalid_switch_env_at_load()
        assert any(
            "Invalid" in rec.message and d.SKILL_EVOLUTION_KILL_SWITCH_ENV in rec.message
            for rec in caplog.records
        )

    def test_load_time_check_silent_for_valid_values(
        self, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        import logging

        from daemon.services import skill_job_dispatcher as d

        monkeypatch.setenv(d.SKILL_EVOLUTION_KILL_SWITCH_ENV, "1")
        monkeypatch.setenv(d.SKILL_CAPTURE_KILL_SWITCH_ENV, "off")
        with caplog.at_level(logging.ERROR, logger=d.logger.name):
            d._log_invalid_switch_env_at_load()
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    def test_evolution_type_constants(self):
        """Minor (b): literal-match fragility replaced by constants."""
        from daemon.services import skill_job_dispatcher as d

        assert d.EVOLUTION_TYPE_FIX == "FIX"
        assert d.EVOLUTION_TYPE_DERIVED == "DERIVED"
        assert d.EVOLUTION_TYPE_CAPTURED == "CAPTURED"
