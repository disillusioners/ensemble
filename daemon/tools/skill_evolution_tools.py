"""Skill evolution tools for the skill-keeper agent.

Internal tools that wrap :class:`SkillEvolutionService` methods. The
service itself is implemented in Phase 5 — these stubs attempt to
call ``manager._skill_evolution_service`` if available and otherwise
return a "not yet connected" message. Reserved for the skill-keeper
agent; NOT for regular agents.

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
  set of stubs covers both Phase 5 service shapes.
* **Soft-fail stubs** — until the real service lands, calling
  these tools returns a stub message ("⏳ Skill evolution service
  not yet initialized…") rather than raising. The skill-keeper
  agent therefore sees a tool response (not a stack trace) and can
  continue planning during the Phase 2 / Phase 5 interregnum.

Tools produced
--------------

* ``skill_analyze`` — Tier 2 static analysis wrapper stub.
* ``skill_evolve`` — Tier 3 evolution wrapper stub.
* ``skill_resolve_ab`` — A/B test resolution wrapper stub.
* ``skill_get_metrics`` — metrics retrieval wrapper stub.
* ``skill_execute_capture`` — CAPTURED flow wrapper stub.
"""

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Skill Evolution"
CATEGORY_DOC = """\
Internal tools for the skill-keeper agent to analyze, evolve, and
manage skill A/B tests. These tools are NOT for regular agents.

- skill_analyze — Tier 2 static analysis wrapper stub.
- skill_evolve — Tier 3 evolution wrapper stub.
- skill_resolve_ab — A/B test resolution wrapper stub.
- skill_get_metrics — Metrics retrieval wrapper stub.
- skill_execute_capture — CAPTURED flow wrapper stub.
"""


async def _invoke_service(manager, service_name, method_name, *args):
    """Try to call a method on a manager-attached service.

    This helper is the Phase 2 stub behavior. In Phase 5 the real
    :class:`SkillEvolutionService` will be attached to the manager as
    ``_skill_evolution_service`` and these stubs will dispatch
    straight through.

    Args:
        manager: The :class:`InstanceManager` instance. May expose
            ``_skill_evolution_service`` (Phase 5) — if absent the
            helper returns the "not yet connected" stub message.
        service_name: Name of the manager attribute holding the
            service, e.g. ``"_skill_evolution_service"``.
        method_name: Name of the service method to invoke, e.g.
            ``"analyze"`` or ``"evolve"``.
        *args: Positional arguments forwarded to the service method.

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
                f"Queued for Phase 5. (hint: {hint})"
            )
        return (
            f"\u23f3 {service_name} not yet initialized. "
            "Queued for Phase 5."
        )
    method = getattr(service, method_name, None)
    if method is None:
        return f"\u26a0\ufe0f {service_name}.{method_name} not found."
    try:
        result = method(*args)
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
    methods. The service itself is implemented in Phase 5 — these
    stubs will be connected to the actual service in Phase 5.

    Args:
        manager: The :class:`InstanceManager` instance. May expose
            ``_skill_evolution_service`` (Phase 5) — if absent the
            stubs return a "not yet connected" message.
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
        """[Phase 5 stub] Run Tier 2 static analysis on a skill."""
        return await _invoke_service(
            manager, "_skill_evolution_service", "analyze", skill_id
        )

    skill_analyze._full_doc_ = """\
[Phase 5 stub] Run Tier 2 static analysis on a skill.

Tier 2 analysis inspects a skill's static shape (frontmatter schema,
required vs optional fields, example block structure, references
section, etc.) and returns a structured report of compliance,
warnings, and improvement hints. In Phase 5 this delegates to
``SkillEvolutionService.analyze(skill_id)``.

Args:
    skill_id: Stable skill identifier (e.g. ``"skill-keeper"`` or
        the frontmatter ``id`` of an ``agents/<id>/skill.md`` file).

Stub behavior:
    While the underlying :class:`SkillEvolutionService` is not yet
    wired up (Phase 5), this tool returns the
    ``"⏳ Skill evolution service not yet initialized…"``
    stub message instead of raising. The actual service connection
    happens in Phase 5; no migration is required on the agent side.
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_evolve(skill_id: str, evolution_type: str, direction: str) -> str:
        """[Phase 5 stub] Run a Tier 3 evolution pass on a skill."""
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "evolve",
            skill_id,
            evolution_type,
            direction,
        )

    skill_evolve._full_doc_ = """\
[Phase 5 stub] Run a Tier 3 evolution pass on a skill.

Tier 3 evolution drafts a concrete change to a skill's frontmatter,
prompt body, or examples based on accumulated Tier 2 analyses and
runtime metrics. In Phase 5 this delegates to
``SkillEvolutionService.evolve(skill_id, evolution_type, direction)``.

Args:
    skill_id: Stable skill identifier targeted for evolution.
    evolution_type: Kind of evolution to perform, e.g.
        ``"prompt_rewrite"``, ``"example_add"``,
        ``"schema_tighten"``, ``"split_skill"``.
    direction: Free-form human-readable direction the evolution
        should respect — e.g. ``"add explicit error-handling
        guidance"`` or ``"favor concise bullet lists over prose"``.

Stub behavior:
    While the underlying service is not yet wired up (Phase 5), this
    tool returns the stub message containing ``"⏳"`` and
    ``"Phase 5"``. The actual service connection happens in Phase 5;
    once connected, the returned payload is a JSON document
    describing the proposed diff.
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_resolve_ab(ab_test_group: str) -> str:
        """[Phase 5 stub] Resolve an in-flight skill A/B test group."""
        return await _invoke_service(
            manager, "_skill_evolution_service", "resolve_ab", ab_test_group
        )

    skill_resolve_ab._full_doc_ = """\
[Phase 5 stub] Resolve an in-flight skill A/B test group.

Closes out an A/B test, computes a winner (or declares a tie),
persists the decision, and returns a structured summary. In Phase 5
this delegates to ``SkillEvolutionService.resolve_ab(ab_test_group)``.

Args:
    ab_test_group: Stable identifier of the A/B test group to
        resolve — typically a slug like ``"prompt-style-2026-07"``
        or a UUID assigned when the test was created.

Stub behavior:
    While the underlying service is not yet wired up (Phase 5), this
    tool returns the stub message containing ``"⏳"`` and
    ``"Phase 5"``. The actual service connection happens in Phase 5;
    once connected, the returned payload is the resolution record
    (winner, metrics snapshot, decision timestamp).
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_get_metrics(skill_id: str) -> str:
        """[Phase 5 stub] Fetch runtime metrics for a skill."""
        return await _invoke_service(
            manager, "_skill_evolution_service", "get_metrics", skill_id
        )

    skill_get_metrics._full_doc_ = """\
[Phase 5 stub] Fetch runtime metrics for a skill.

Returns the aggregated runtime metrics for ``skill_id`` —
invocation counts, success / failure rates, latency percentiles,
and any open A/B test cohorts. In Phase 5 this delegates to
``SkillEvolutionService.get_metrics(skill_id)``.

Args:
    skill_id: Stable skill identifier whose metrics should be
        fetched.

Stub behavior:
    While the underlying service is not yet wired up (Phase 5), this
    tool returns the stub message containing ``"⏳"`` and
    ``"Phase 5"``. The actual service connection happens in Phase 5;
    once connected, the returned payload is a JSON metrics document.
"""

    @register_tool_category("skill-evolution")
    @tool
    async def skill_execute_capture(instance_id: str, task_details: str) -> str:
        """[Phase 5 stub] Execute the CAPTURED flow for a task."""
        return await _invoke_service(
            manager,
            "_skill_evolution_service",
            "execute_capture",
            instance_id,
            task_details,
        )

    skill_execute_capture._full_doc_ = """\
[Phase 5 stub] Execute the CAPTURED flow for a task.

The CAPTURED flow wraps an arbitrary ``task_details`` payload into a
runnable skill-cap unit: it identifies the responsible skill,
captures the runtime trace, and persists the record for later
analysis. In Phase 5 this delegates to
``SkillEvolutionService.execute_capture(instance_id, task_details)``.

Args:
    instance_id: The originating instance ID (typically the current
        instance captured in the closure).
    task_details: Free-form description of the task to capture —
        natural-language intent plus any structured context the
        service needs.

Stub behavior:
    While the underlying service is not yet wired up (Phase 5), this
    tool returns the stub message containing ``"⏳"`` and
    ``"Phase 5"``. The actual service connection happens in Phase 5;
    once connected, the returned payload is the captured record id
    and a brief summary.
"""

    return [
        skill_analyze,
        skill_evolve,
        skill_resolve_ab,
        skill_get_metrics,
        skill_execute_capture,
    ]