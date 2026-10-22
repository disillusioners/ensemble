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
(``skill_evolve`` / ``analyze_skill`` run inline on an
already-running keeper — the gate stops job ADMISSION, not
execution).
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
WRITE_FS_TOOLS = ("write_file", "edit_file")
# Representative category maps for resolve_tool_filter (the registry
# populates at tool-module import; the pins pass explicit maps, same
# pattern as tests/tools/test_infra_tools.py Group 12).
CATEGORY_MAP = {
    "bash": ["bash"],
    "proc": ["proc_run", "proc_logs", "proc_status", "proc_stop", "proc_list"],
    "service": [
        "service_start",
        "service_stop",
        "service_status",
        "service_list",
        "service_logs",
    ],
    "filesystem": [
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "glob_files",
        "grep_files",
    ],
    "self": ["inner_soul", "access_memory"],
    "help": ["tool_help"],
    "knowledge": ["explore", "experience"],
    "dynamic-skill": [
        "skill_search",
        "skill_list",
        "skill_view",
        "skill_create",
        "skill_fix",
        "skill_feedback",
    ],
    "skill-evolution": [
        "skill_analyze",
        "skill_evolve",
        "skill_resolve_ab",
        "skill_metrics",
        "skill_capture_opportunities",
    ],
    "shared_meta_kv": ["shared_meta_kv"],
}


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
        for tool_name in WRITE_FS_TOOLS:
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
        for mutation in ("bash", "proc", "service", "write_file", "edit_file"):
            assert mutation in deny

    def test_resolved_filter_grants_no_write_capable_tool(self):
        """End-to-end: resolve skill-keeper's allow/deny through the
        production filter — the resolved set must contain ZERO
        write-capable tools and the full read-only surface."""
        from daemon.tools.instance import resolve_tool_filter

        meta = self._meta()["tools"]
        resolved = resolve_tool_filter(
            allow=meta["allow"],
            deny=meta.get("deny"),
            tool_categories=CATEGORY_MAP,
        )
        assert resolved is not None
        for tool_name in WRITE_FS_TOOLS:
            assert tool_name not in resolved
        assert "bash" not in resolved
        assert not (resolved & set(CATEGORY_MAP["proc"]))
        assert not (resolved & set(CATEGORY_MAP["service"]))
        for tool_name in READ_ONLY_FS_TOOLS:
            assert tool_name in resolved
        assert "skill_view" in resolved  # audit-duty read path survives

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
        # ...and state that writes/bash are denied at the tool layer.
        assert "write_file" in soul
        assert "edit_file" in soul
        assert "tools.deny" in soul
        assert "not granted" in soul

    def test_must_not_section_carries_the_repo_mutation_prohibition(self):
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        assert "❌ Mutate the repository" in soul
        assert "NO bash" in soul
        assert "read-only evidence" in soul

    def test_charter_does_not_claim_shell_access(self):
        """No surviving line may tell the keeper it has bash."""
        soul = KEEPER_SOUL_PATH.read_text(encoding="utf-8")
        assert "### `bash`" not in soul
        # The old phrasing granted shell "when an audit demands" — gone.
        assert "run migrations when an audit demands" not in soul


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
