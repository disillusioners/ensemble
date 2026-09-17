"""Skill evolution tools for the skill-keeper agent.

Internal tools that wrap :class:`SkillEvolutionService` methods. The
service is implemented in ``daemon/services/skill_evolution_service.py``;
when it is attached to the manager as ``_skill_evolution_service`` the
tools dispatch straight through. If the service is absent the tools
return a "not yet connected" stub message — the skill-keeper agent
sees a tool response (not a stack trace) and can keep planning. These
tools are reserved for the skill-keeper agent; NOT for regular agents.

Architecture
------------

* **Closure injection** — ``create_skill_evolution_tools(manager,
  current_instance_id)`` mirrors the closure pattern used by
  :mod:`daemon.tools.todo_tools` and :mod:`daemon.tools.chart_tools`.
  Both arguments are captured in the tool closures; the tools never
  touch instance state directly.
* **Async wrappers** — the 5 tool functions are declared
  ``async def`` and ``await`` :func:`_invoke_service`. The
  helper handles both sync and async service methods uniformly
  via ``hasattr(result, "__await__")`` detection, so a single
  set of tools covers any future service shape.
* **Soft-fail** — when the manager has no
  ``_skill_evolution_service`` attribute (e.g. during early
  Phase 2 init or in tests that disable the service) the tools
  return a "⏳ not yet connected" stub message rather than
  raising. Once the service lands the tools dispatch transparently.

Tools produced
--------------

* ``skill_analyze`` — Tier 2 static analysis wrapper
  (``SkillEvolutionService.analyze_skill``).
* ``skill_evolve`` — Tier 3 evolution wrapper
  (``SkillEvolutionService.evolve_skill``).
* ``skill_resolve_ab`` — A/B test resolution wrapper
  (``SkillEvolutionService.check_ab_test_resolution``).
* ``skill_get_metrics`` — metrics retrieval wrapper
  (``SkillEvolutionService.get_skill_metrics``).
* ``skill_execute_capture`` — CAPTURED flow wrapper
  (``SkillEvolutionService.capture_skill``).
"""

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from daemon.services.skill_job_dispatcher import (
    SKILL_CAPTURE_KILL_SWITCH_ENV,
    _resolve_capture_enabled,
)

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Skill Evolution"
CATEGORY_DOC = """\
Internal tools for the skill-keeper agent to analyze, evolve, and
manage skill A/B tests. These tools are NOT for regular agents.

- skill_analyze — Tier 2 static analysis wrapper.
- skill_evolve — Tier 3 evolution wrapper.
- skill_resolve_ab — A/B test resolution wrapper.
- skill_get_metrics — Metrics retrieval wrapper.
- skill_execute_capture — CAPTURED flow wrapper.
"""


async def _invoke_service(manager, service_name, method_name, *args, **kwargs):
    """Call a method on a manager-attached service.

    Looks up ``manager.<service_name>``; if absent, returns a
    "not yet connected" stub message. If the service exists but
    lacks the requested method, returns a "method not found"
    message. On any exception during the call, returns an
    ``ERROR: ...`` string. Never raises — the skill-keeper agent
    loop must always see a tool response.

    Args:
        manager: The :class:`InstanceManager` instance. May expose
            ``_skill_evolution_service`` (Phase 5) — if absent the
            helper returns the "not yet connected" stub message.
        service_name: Name of the manager attribute holding the
            service, e.g. ``"_skill_evolution_service"``.
        method_name: Name of the service method to invoke, e.g.
            ``"analyze_skill"`` or ``"evolve_skill"``.
        *args: Positional arguments forwarded to the service method.
        **kwargs: Keyword arguments forwarded to the service method.

    Returns:
        - The JSON-serialized result string on success.
        - A "⏳ <service_name> not yet initialized…" stub message
          when the service attribute is missing or ``None``.
        - A "⚠️ <service_name>.<method_name> not found." message
          when the service exists but lacks the method.
        - An ``ERROR: ...`` string when the service method raises.
    """
    service = getattr(manager, service_name, None)
    if service is None:
        # Echo a short hint back to the caller (truncated first
        # positional arg) so the agent loop can correlate the stub
        # with the operation that triggered it.
        hint = ""
        if args:
            try:
                hint = str(args[0])[:10]
            except Exception:
                hint = ""
        if hint:
            return (
                f"\u23f3 {service_name} not yet initialized. "
                f"(hint: {hint})"
            )
        return (
            f"\u23f3 {service_name} not yet initialized."
        )
    method = getattr(service, method_name, None)
    if method is None:
        return f"\u26a0\ufe0f {service_name}.{method_name} not found."
    try:
        result = method(*args, **kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return json.dumps(result, default=str, indent=2)
    except Exception as e:
        return f"ERROR: {service_name}.{method_name} failed: {e}"


def create_skill_evolution_tools(
    manager: "InstanceManager", current_instance_id: str
) -> list:
    """Create skill evolution tools for the skill-keeper agent.

    These are internal tools that wrap :class:`SkillEvolutionService`
    methods. The service is wired to the manager as
    ``_skill_evolution_service``; when absent the tools return a
    "not yet connected" stub message.

    Args:
        manager: The :class:`InstanceManager` instance. May expose
            ``_skill_evolution_service`` (Phase 5) — if absent the
            tools return a "not yet connected" message.
        current_instance_id: The ID of the calling instance. Captured
            in the closure for context logging; the tools themselves
            do not mutate state.

    Returns:
        List of 5 tool functions in this exact order:
        ``[skill_analyze, skill_evolve, skill_resolve_ab,
        skill_get_metrics, skill_execute_capture]``.
    """

    @register_tool_category("skill-evolution")
    @tool
    async def skill_analyze(skill_id: str) -> str:
        """Run Tier 2 static analysis on a skill.

        Delegates to ``SkillEvolutionService.analyze_skill(skill_id,
        reason="", stats=None)``. Tier 2 analysis inspects a skill's
        static shape (frontmatter schema, required vs optional fields,
        example block structure, references section, etc.) and the
        runtime stats (completion_rate, consecutive_failures, recent
        feedback) to decide whether the skill should evolve, and if so
        in what direction.
        """
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "analyze_skill",
            skill_id,
            reason="",
            stats=None,
        )

    skill_analyze._full_doc_ = """\
Run Tier 2 static analysis on a skill.

Delegates to ``SkillEvolutionService.analyze_skill(skill_id,
reason="", stats=None)``. Tier 2 analysis inspects a skill's
static shape (frontmatter schema, required vs optional fields,
example block structure, references section, etc.) and the
runtime stats (completion_rate, consecutive_failures, recent
feedback) and asks the cheap LLM to classify the skill as
``FIX`` / ``DERIVED`` / ``CAPTURED`` / ``NONE`` with a one-line
``direction``.

Args:
    skill_id: Stable skill identifier (e.g. ``"skill-keeper"`` or
        the frontmatter ``id`` of an ``agents/<id>/skill.md`` file).

Returns:
    JSON document with ``should_evolve`` (bool), ``evolution_type``
    (``FIX`` | ``DERIVED`` | ``CAPTURED`` | ``NONE``), ``direction``
    (str), ``analysis_summary`` (str).

While the underlying :class:`SkillEvolutionService` is not yet
wired up, this tool returns the
``"⏳ Skill evolution service not yet initialized…"``
stub message instead of raising.
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_evolve(
        skill_id: str, evolution_type: str, direction: str
    ) -> str:
        """Run a Tier 3 evolution pass on a skill.

        Delegates to ``SkillEvolutionService.evolve_skill(skill_id,
        evolution_type, direction)``. Dispatches on ``evolution_type``:
        ``FIX`` creates a tweaked copy and starts an A/B test vs the
        original; ``DERIVED`` creates a specialized sibling; ``CAPTURED``
        wraps ``direction`` + the skill into a task-details dict and
        runs the capture flow.
        """
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "evolve_skill",
            skill_id,
            evolution_type,
            direction,
        )

    skill_evolve._full_doc_ = """\
Run a Tier 3 evolution pass on a skill.

Delegates to ``SkillEvolutionService.evolve_skill(skill_id,
evolution_type, direction)``. Dispatches on ``evolution_type``:

* ``FIX`` — create a tweaked copy and start an A/B test vs the
  original (guarded against nested A/B tests).
* ``DERIVED`` — create a specialized sibling (new name,
  generation 0, lineage to the original).
* ``CAPTURED`` — wrap ``direction`` and the skill into a
  task-details dict and run the capture flow.

Args:
    skill_id: Stable skill identifier targeted for evolution.
    evolution_type: One of ``"FIX"`` / ``"DERIVED"`` /
        ``"CAPTURED"``.
    direction: Free-form human-readable direction the evolution
        should respect — e.g. ``"add explicit error-handling
        guidance"`` or ``"favor concise bullet lists over prose"``.

Returns:
    JSON document describing the proposed change. ``FIX`` returns
    ``new_skill_id`` / ``old_skill_id`` / ``ab_test_group`` /
    ``skipped``; ``DERIVED`` returns ``new_skill_id`` /
    ``parent_ids`` / ``skipped``; ``CAPTURED`` returns
    ``new_skill_id`` / ``skipped``.

While the underlying service is not yet wired up, this tool
returns the stub message containing ``"⏳"``. Once connected,
the returned payload is a JSON document describing the
proposed diff.
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_resolve_ab(ab_test_group: str) -> str:
        """Resolve an in-flight skill A/B test group.

        Delegates to ``SkillEvolutionService.check_ab_test_resolution(
        ab_test_group)``. Inspects the persisted test row + per-variant
        stats and decides whether to resolve, extend, or wait for more
        data.
        """
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "check_ab_test_resolution",
            ab_test_group,
        )

    skill_resolve_ab._full_doc_ = """\
Resolve an in-flight skill A/B test group.

Delegates to ``SkillEvolutionService.check_ab_test_resolution(
ab_test_group)``. Closes out an A/B test, computes a winner (or
declares a tie), persists the decision, and returns a structured
summary. Decision tree:

1. **Not enough data** (``comparisons < ab_sample_size``) →
   keep collecting. ``reason='needs_more_data'``.
2. **Threshold met** (``difference >= ab_min_difference``) →
   resolve by raw completion rate. ``reason='threshold_met'``.
3. **Threshold missed + ``extension_count >= max_extensions``** →
   force-resolve by raw completion rate.
   ``reason='force_resolved_max_extensions'``.
4. **Threshold missed + extensions remaining** → bump
   ``extension_count`` via the repo. ``reason='extended'``.

Args:
    ab_test_group: Stable identifier of the A/B test group to
        resolve — typically a slug like ``"prompt-style-2026-07"``
        or a UUID assigned when the test was created.

Returns:
    JSON document with ``resolved`` (bool), ``winner_id`` /
    ``loser_id`` (str | None), ``reason`` (str),
    ``extension_count`` (int).

While the underlying service is not yet wired up, this tool
returns the stub message containing ``"⏳"``. Once connected,
the returned payload is the resolution record (winner,
metrics snapshot, decision timestamp).
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_get_metrics(skill_id: str) -> str:
        """Fetch runtime metrics for a skill.

        Delegates to ``SkillEvolutionService.get_skill_metrics(
        skill_id)``. Returns the skill row, usage stats, recent usage
        count, and any open A/B test cohort — a single convenience
        round-trip for the metrics / admin UI.
        """
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "get_skill_metrics",
            skill_id,
        )

    skill_get_metrics._full_doc_ = """\
Fetch runtime metrics for a skill.

Delegates to ``SkillEvolutionService.get_skill_metrics(skill_id)``.
Returns the aggregated runtime metrics for ``skill_id`` —
invocation counts, success / failure rates, latency percentiles,
and any open A/B test cohorts — bundled into a single document
(skill row via ``Skill.to_dict()``, stats from
``SkillMetricsService.get_skill_stats``, usage record count, and
the persisted A/B test row when present).

Args:
    skill_id: Stable skill identifier whose metrics should be
        fetched.

Returns:
    JSON document with ``skill_id``, ``found`` (bool), and —
    when found — ``skill`` (via ``Skill.to_dict()``), ``stats``,
    ``usage_recent_count``, and ``ab_test``.

While the underlying service is not yet wired up, this tool
returns the stub message containing ``"⏳"``. Once connected,
the returned payload is a JSON metrics document.
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_execute_capture(
        instance_id: str,
        task_message: str,
        iterations: int,
        duration_seconds: int,
    ) -> str:
        """Execute the CAPTURED flow for a task.

        Constructs a ``task_details`` dict from the tool arguments and
        delegates to ``SkillEvolutionService.capture_skill(
        current_instance_id, task_details)``. The capture flow
        validates that the task succeeded with sufficient complexity,
        then asks the LLM to distill it into a reusable skill body
        with ``lineage_origin='captured'``.

        Kill-switch contract: when
        ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` is unset / empty / falsy
        (default OFF), the tool returns a JSON envelope with
        ``{"skipped": true, "reason": "...", "new_skill_id": null,
        "instance_id": ..., "kill_switch_env":
        "ENSEMBLE_SKILL_CAPTURE_ENABLED"}`` and does NOT invoke
        ``_skill_evolution_service.capture_skill``. This closes the
        known bypass path around ``check_and_capture`` — the tool
        has historically been able to trigger capture even when the
        post-execution eligibility gate would have refused it (the
        metrics-service path enforces more gates, but the tool path
        does not). When the kill-switch is ON (``=1``/``true``/etc.)
        the tool behavior is byte-identical to the pre-flag codebase.

        Quality gates (2026-09-17, skill 7ae063c4 class): even with
        the kill-switch ON, the service seam now rejects captures
        with a blank/whitespace ``task_message``, ``iterations <= 0``,
        a blank ``agent_id``, or a missing ``project_id`` — the
        originating instance's ``agent_id`` / ``project_id`` are
        resolved from the instance row and stamped into
        ``task_details`` (captures are project-scoped by default;
        global requires an explicit share action). A post-LLM content
        gate additionally rejects self-referential meta-skills,
        truncated descriptions, and un-parsed bodies. Refusals return
        the standard envelope with ``skipped: true`` and a
        machine-readable ``skip_reason``; nothing is inserted.
        """
        # ── Kill-switch gate ─────────────────────────────────────
        # Closes the bypass path around ``check_and_capture``: the
        # tool path historically skipped the metrics-service gates
        # and could trigger capture even when the post-execution
        # eligibility check would have refused it. We short-circuit
        # BEFORE constructing task_details / dispatching to the
        # service — the service records nothing, no job is enqueued,
        # no LLM is invoked.
        if not _resolve_capture_enabled():
            envelope = {
                "skipped": True,
                "reason": (
                    f"skill capture disabled (env "
                    f"{SKILL_CAPTURE_KILL_SWITCH_ENV}=disabled); "
                    f"no skill_capture job will be enqueued and no "
                    f"record will be written"
                ),
                "new_skill_id": None,
                "instance_id": instance_id,
                "kill_switch_env": SKILL_CAPTURE_KILL_SWITCH_ENV,
            }
            return json.dumps(envelope, default=str, indent=2)

        # ``current_instance_id`` is captured in the closure; the
        # caller-supplied ``instance_id`` is forwarded into the
        # task_details dict for lineage/audit purposes.
        task_details = {
            "instance_id": instance_id,
            "task_message": task_message,
            "iterations": iterations,
            "duration_seconds": duration_seconds,
        }

        # Stamp ownership scope from the ORIGINATING instance row.
        # Historically this tool dropped ``agent_id`` / ``project_id``
        # from task_details, so every tool-driven capture landed
        # GLOBAL (project_id NULL) with BOTH dedup layers disarmed —
        # violating the project-scope-default decision (observed
        # 2026-09-17, skill 7ae063c4). The service-side P0 gate now
        # refuses scope-less captures, so a failed lookup here fails
        # CLOSED (skipped capture) rather than inserting a global
        # row. Same instance-row metadata read pattern as
        # ``daemon/tools/instance.py::_get_instance_project_id``.
        try:
            origin_row = manager._instance_repository.get(instance_id)
        except Exception as exc:
            logger.warning(
                "[SkillEvolution] skill_execute_capture could not read "
                f"instance row for {instance_id!r} ({exc!s}); leaving "
                "agent_id/project_id unset — the service P0 gate will "
                "refuse a scope-less capture."
            )
            origin_row = None
        if origin_row is not None:
            origin_agent_id = getattr(origin_row, "agent_id", None)
            origin_project_id = getattr(origin_row, "project_id", None)
            if origin_agent_id:
                task_details["agent_id"] = origin_agent_id
            if origin_project_id:
                task_details["project_id"] = origin_project_id
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "capture_skill",
            current_instance_id,
            task_details,
        )

    skill_execute_capture._full_doc_ = """\
Execute the CAPTURED flow for a task.

Constructs a ``task_details`` dict from the tool arguments (plus
``agent_id`` / ``project_id`` resolved from the originating instance
row — captures are project-scoped by default) and delegates to
``SkillEvolutionService.capture_skill(
current_instance_id, task_details)``. The capture flow distills the
task into a reusable skill body with ``lineage_origin='captured'``.

Input gates (no LLM spend): blank ``task_message``, ``iterations <=
0``, blank ``agent_id``, or missing ``project_id`` →
``{"skipped": true, "skip_reason": ...}``, nothing inserted. A
post-LLM content gate likewise rejects self-referential meta-skills
(bodies describing the capture procedure itself), truncated
descriptions, and bodies containing literal escape sequences.

Args:
    instance_id: The originating instance ID (the agent that ran
        the task). Forwarded into the task_details dict for
        lineage/audit purposes; the closure-supplied
        ``current_instance_id`` is what is passed as the first
        argument to ``capture_skill``.
    task_message: Free-form description of the task to capture —
        natural-language intent that the LLM will distill into a
        reusable skill body. Blank/whitespace-only is rejected
        before any LLM call.
    iterations: Loop iterations the agent took on this task.
        ``<= 0`` is rejected before any LLM call.
    duration_seconds: Wall-clock runtime in seconds.

Returns:
    JSON document with ``new_skill_id`` and ``skipped`` (plus
    ``skip_reason`` on refusals).
"""

    return [
        skill_analyze,
        skill_evolve,
        skill_resolve_ab,
        skill_get_metrics,
        skill_execute_capture,
    ]