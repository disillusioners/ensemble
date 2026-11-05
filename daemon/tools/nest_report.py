"""The ``nest-report`` write category — ONE tool: ``report_anomaly``.

The nest-anomaly lane (board ``wi-2d691ed2``) opens ONE write surface:
an agent that notices something wrong says so, and the daemon normalizes,
scrubs, harvests the bounded evidence and persists it.  Clustering, rate
limits, the board sink and retention are the SERVICE's job (phase 2) —
this module is the tool surface plus the refusal ladder.

WHAT THIS TOOL IS NOT
---------------------
* It is NOT a diagnostic surface — it does not read the metrics registry
  (that is the ``metrics-read`` category) and it does not read other
  instances (that is ``instance_read``).  It WRITES ONE ROW.
* It is NOT a privileged category.  ``nest-report`` is deliberately OUT of
  ``PRIVILEGED_TOOL_CATEGORIES`` (R4): the tool cannot read secrets, the
  environment or another instance's data, and its own evidence harvest is
  self-scoped and scrubbed.  Agents with an EMPTY allow list therefore
  INHERIT it through the default universe; an agent with an EXPLICIT
  allow list receives it in the phase-5 grant ceremony.
* It NEVER voices.  A critical report is surfaced on the BOARD
  server-side (phase 2) — ``notify_user`` is never called from this path.

THE CLOSED VOCABULARY (R1)
--------------------------
``category`` is free text at the tool boundary and is folded AT WRITE TIME
into a CLOSED canonical vocabulary: lowercase → strip → exact canonical
match → synonym → the reserved ``other``.  The canonical values are

    tool-failure, performance, resource-exhaustion, loop-stall,
    wedge-suspected, data-corruption, auth-permission,
    external-dependency, mcp-failure, llm-provider, queue-scheduling,
    environment-config

plus the reserved fold target ``other``.  The raw input is preserved
verbatim (capped at 64 chars) as ``category_raw`` on the row, so a
vocabulary gap stays visible instead of being silently lost.  A fold is
reported in the envelope's ``category_fold_notice`` line.

THE SEVERITY TIERS (R3)
-----------------------
``minor`` (an annoyance; work continues) · ``major`` (work materially
impeded or quality at risk) · ``critical`` (nest-level: multiple
trees/agents impacted or integrity at risk; needs Ari/the user at the next
review).  The tier is normalized (lowercase/strip) and an OUT-OF-SET value
is REFUSED with ``invalid-input`` — it is NEVER silently folded, because
folding ``critical`` down to ``major`` would hide a nest-level event.

THE REFUSAL LADDER (checked in this order)
------------------------------------------
1. ``tool-disabled``  — the kill-switch
   (``ENSEMBLE_NEST_REPORT_ENABLED=false``), an ABSENT config, this
   writer's agent type on the AM-16 deny list
   (``ENSEMBLE_NEST_REPORT_DISABLED_AGENTS``), or the anomaly substrate
   not being mounted on the manager.  A degraded boot leaves the tool
   PRESENT and refusing LOUDLY (the metrics-read RULING-1 shape): for a
   reporting surface, silent absence is the worst failure mode.
2. ``invalid-input``  — empty/whitespace description, an out-of-set
   severity, a non-string argument.  NOTHING is persisted.
3. ``rate-limited``   — the per-agent-TYPE budget (phase 2 enforces it).
4. ``capacity-exhausted`` — the nest-wide capacity ceilings (phase 2
   enforces them).
5. ``persist-failed`` — the INSERT failed.

The house envelope is ``Error: {label} REFUSED — reason={token}: {message}``.

THE SPLIT POSTURE (AM-11) — READ THIS BEFORE EDITING
----------------------------------------------------
* The **excerpt** column is FAIL-CLOSED on a scrubber-absent boot: it
  persists NULL and a WARNING is logged.  The excerpt is RAW TOOL STDOUT
  (the bash tool returns STDOUT verbatim) — the highest-risk field.
* **description** / **suspected_cause** stay FAIL-OPEN on a
  scrubber-absent boot: the pattern masker and the shape masker still
  apply, and a WARNING is logged.  Refusing the report would blind the
  nest exactly when things are broken.

THE SOLE-DEFENCE STATEMENT (C1c)
---------------------------------
For a NON-STORED secret the PATTERN MASKER IS THE ONLY DEFENCE — the
exact-match scrubber knows only STORED secret values, so scrubber
PRESENCE is irrelevant to that class.  The excerpt's fail-closed posture
must NOT be read as covering it.

THE BUDGET IS PER AGENT **TYPE** (AM-10)
----------------------------------------
The budget key is the agent TYPE (e.g. ``worker``), NEVER the instance
id.  Consequences, stated because this doc is the agent-visible surface:

* SPAWNING DOES NOT MULTIPLY THE BUDGET — an orchestrator that spawns
  fifty workers still spends ONE ``worker`` budget.
* A TYPE'S INSTANCES SHARE ONE BUDGET (the noisy-neighbour trade-off:
  one agent's burst can consume its siblings' quota).
* THE KEY SPACE IS NOT AGENT-EXPANDABLE — the distinct budget keys are
  fixed by the in-repo agent catalogue.

THE BOUNDS ARE A HIERARCHY (N2), NOT ONE KNOB
----------------------------------------------
* The PER-TREE ROW CAP (2,000 rows) SHEDS (oldest-first trim) and NEVER
  refuses — so one tree can never go dark; the durable critical record is
  the board item, not the report row.
* The GLOBAL TOTAL-ROW CEILING (50,000 rows) is the HARD nest-wide wall.
* The 1,500/hour knob is the EARLY GLOBAL BRAKE — an OPERATOR BRAKE, not
  a security fence.
* The PER-TYPE CAPS (5 per 10 min for minor+major, 20 per hour for
  critical) are the INNER brake.

THE CONCURRENCY CAVEAT (N3) — READ IT AS A CAVEAT
--------------------------------------------------
"5 per 10 min" is BEST-EFFORT under concurrency: the check is
read-then-insert, so two concurrent reports from one agent type can BOTH
pass.  It is NOT a hard invariant.  The GLOBAL TOTAL-ROW CEILING and
the 1,500/hour GLOBAL BRAKE are read-then-insert BEST-EFFORT in
exactly the same way.

THE OFFLOAD-GATE PRESSURE (AM-14)
---------------------------------
One report costs roughly SEVEN off-loop database round trips (the
ancestor walk, the task/JobItem/MissionCommission chain and the rest)
against the 25-slot strict-admission executor.  A burst therefore
DEGRADES the evidence fields to NULL — the report is still ACCEPTED.
A report with NULL evidence is a weaker report, never a lost one.

THE DEFAULT-ON POSTURE IS CONDITIONAL (AM-16)
----------------------------------------------
``ENSEMBLE_NEST_REPORT_ENABLED`` defaults to True IFF the four security
amendments (AM-9 capacity fences, AM-11 scrub fence, AM-12 board
ceilings, AM-13 board-item ceilings) have landed AND are verified by the
lane's gate; if any of the four is missing or unverified at gate time
the lane ships default-OFF plus a mandatory morning-report disclosure.
The env kill-switch stays the operator escape hatch in either state, and
the per-writer deny list (``ENSEMBLE_NEST_REPORT_DISABLED_AGENTS``) is
the surgical lever the whole-surface kill-switch lacks: a listed writer
is refused while every other writer in the same boot is unaffected.

THE SUBSTRATE SEAM
------------------
The tool body dereferences the manager handles PER CALL: the config
(``manager.config.nest_report``) and the anomaly service
(``manager._anomaly_service``).  A degraded boot therefore leaves the
tool PRESENT and refusing ``tool-disabled`` — never silently vanished.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from daemon._redact import redact_exc_str

from ._tool_registry import register_tool_category
from daemon.repositories.anomaly.models import OUTCOME_UNCLUSTERED
from daemon.services.anomaly_service import (
    ANOMALY_SEVERITIES as _SERVICE_ANOMALY_SEVERITIES,
    CATEGORY_RAW_MAX_CHARS as _SERVICE_CATEGORY_RAW_MAX_CHARS,
)

logger = logging.getLogger(__name__)

#: The category key — KEBAB-case, byte-identical in every place it
#: appears (the module, the registry, the ``meta.json`` allow entries,
#: the ``tools_note.md`` sections and the pin packs).
NEST_REPORT_CATEGORY_KEY = "nest-report"

#: The display name for the rendered tool documentation.
CATEGORY_NAME = "Nest Report"

#: The tool names this category owns.
TOOL_NAMES: frozenset[str] = frozenset({"report_anomaly"})

# ── the refusal tokens (the ladder is ordered; the order IS the contract) ──

TOKEN_TOOL_DISABLED = "tool-disabled"
TOKEN_INVALID_INPUT = "invalid-input"
TOKEN_RATE_LIMITED = "rate-limited"
TOKEN_CAPACITY_EXHAUSTED = "capacity-exhausted"
TOKEN_PERSIST_FAILED = "persist-failed"

#: The tokens the SERVICE may raise back at the tool (phase 2 enforces
#: the rate limits and the capacity ceilings).  The tool maps them onto
#: the same house envelope; any other failure is ``persist-failed``.
SERVICE_TOKENS: frozenset[str] = frozenset(
    {TOKEN_RATE_LIMITED, TOKEN_CAPACITY_EXHAUSTED}
)

#: The closed severity tiers (R3) — IMPORTED from the service vocabulary
#: (the single source of truth: a duplicated literal would drift exactly
#: like the category cap below). ``SEVERITIES`` stays the tool-side name.
SEVERITIES: tuple[str, ...] = _SERVICE_ANOMALY_SEVERITIES

#: The default severity — the tier an unreported severity lands on.
DEFAULT_SEVERITY = "major"

#: The raw-category cap (R1) — the reporter's verbatim input, capped.
#: The value is IMPORTED from the service (the single source of truth —
#: a duplicated literal would drift).  The service does NOT import this
#: tool, so the import is acyclic (verified in both import orders).
#: ``tests/unit/test_anomaly_report_tool_registration.py`` pins the
#: tool's cap EQUALS the service's cap, so the two can never drift.
CATEGORY_RAW_MAX_CHARS = _SERVICE_CATEGORY_RAW_MAX_CHARS

#: The envelope's cluster tail for the IMPOSSIBLE
#: ``accepted``-without-cluster shape — the greppable marker for an
#: AM-5b contract violation the service cannot produce (phase 2 deleted
#: the phase-1 degenerate tail).
PENDING_CLUSTER_ID = "pending-phase-2"

#: The envelope's cluster tail for a report whose ASSIGNMENT FAILED —
#: the AM-5b ``unclustered`` terminal state.  The value IS the outcome
#: token (one vocabulary, no drift): phase 2 deleted the phase-1
#: "clustering not yet available" tail — a report with no cluster was
#: TERMINALIZED, never deferred.
UNCLUSTERED_CLUSTER_ID = OUTCOME_UNCLUSTERED

CATEGORY_DOC = __doc__ or ""


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope.

    ``Error: {label} REFUSED — reason={token}: {message}`` — the same
    shape the other categories use, so one parser reads them all.
    """
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _nest_config(manager: Any) -> Any:
    """The ``NestReportConfig`` for this manager (or ``None``).

    Resolved PER CALL: the config is the operator's live knob set, and a
    manager whose config never grew the section is a degraded boot, not
    a crash.
    """
    config = getattr(manager, "config", None)
    if config is None:
        return None
    return getattr(config, "nest_report", None)


def _anomaly_service(manager: Any) -> Any:
    """The anomaly service mounted on this manager (or ``None``).

    The house convention is a private manager attribute
    (``manager._anomaly_service``); the public alias is accepted as a
    second door so a mount under either name resolves.  Resolved PER CALL
    so a service mounted after the tool list was built is still seen.

    TWIN: ``daemon/tools/nest_report_read.py::_anomaly_service`` (and the
    router's request-scoped variant) — an attribute-order change must
    land in all three.
    """
    for attribute in ("_anomaly_service", "anomaly_service"):
        service = getattr(manager, attribute, None)
        if service is not None:
            return service
    return None


def _disabled_for(config: Any, agent_id: str) -> bool:
    """Is this writer's agent TYPE on the AM-16 deny list?

    The list holds agent TYPE ids (comma-separated).  The check is
    PRE-rate-gate and affects THAT WRITER ONLY — every other writer in
    the same boot is unaffected.
    """
    raw = getattr(config, "disabled_agents", "") or ""
    if not isinstance(raw, str):
        return False
    listed = {part.strip() for part in raw.split(",") if part.strip()}
    if not listed:
        return False
    return agent_id in listed


def _service_token(exc: BaseException) -> str | None:
    """The refusal token an exception carries (or ``None``).

    The service signals a refusal by raising an exception that carries a
    ``token`` attribute (phase 2's rate-limit / capacity refusals).  An
    exception WITHOUT a recognised token is a persistence failure.
    """
    token = getattr(exc, "token", None)
    if isinstance(token, str) and token in SERVICE_TOKENS:
        return token
    return None


def _cluster_tail(result: Any) -> str:
    """The envelope's cluster tail — HONEST about a missing assignment.

    Phase 2 deleted the phase-1 ``pending-phase-2`` tail: there is no
    "clustering not yet available" state.  A report that carries no
    cluster was TERMINALIZED ``unclustered`` (AM-5b — ``accepted``
    MEANS clustered), so the envelope renders the outcome's own token
    and never a stale "pending" marker.

    The ``pending-phase-2`` fallback survives ONLY for a result that
    carries no cluster AND claims ``accepted`` — an AM-5b contract
    violation the service cannot produce (it writes the ``unclustered``
    outcome instead).  It is kept as the greppable marker for that
    impossible shape.
    """
    cluster_id = getattr(result, "cluster_id", None)
    if cluster_id:
        return str(cluster_id)
    if getattr(result, "outcome", None) == OUTCOME_UNCLUSTERED:
        return UNCLUSTERED_CLUSTER_ID
    return PENDING_CLUSTER_ID


def _success_envelope(result: Any, *, truncated: bool) -> str:
    """The success envelope — a single string, one field per line.

    The field set is FIXED:

    ``OK: anomaly accepted``
    ``report_id=<uuid>``
    ``cluster_id=<uuid | unclustered>``
    ``cluster_report_count=<n>``
    ``cluster_first_report_at=<ISO-8601>``
    ``category_fold_notice=<notice>``  (ONLY when a fold happened)
    ``truncated=true``                 (ONLY when a field was capped)
    """
    cluster_id = _cluster_tail(result)
    lines = [
        "OK: anomaly accepted",
        f"report_id={getattr(result, 'report_id', '')}",
        f"cluster_id={cluster_id}",
        f"cluster_report_count={getattr(result, 'cluster_report_count', 1)}",
        f"cluster_first_report_at={getattr(result, 'cluster_first_report_at', '')}",
    ]
    fold_notice = getattr(result, "fold_notice", None)
    if fold_notice:
        lines.append(f"category_fold_notice={fold_notice}")
    if truncated:
        lines.append("truncated=true")
    return "\n".join(lines)


def create_nest_report_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the ``report_anomaly`` tool for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — the tool reads
            ``manager.config.nest_report`` (the knob set) and the
            anomaly service (``manager._anomaly_service``) PER CALL.
        current_instance_id: The caller's instance id (the ROW's
            ``instance_id``; NEVER the budget key).
        agent_id: The caller's agent TYPE id (AM-10 — the budget key).
            Captured at factory time and threaded to the service; it is
            the TYPE, not the instance id, so a type's instances share
            one budget.
        agent_tag: The caller's version tag (unused; signature parity).

    Returns:
        A list of the one ``@tool``-decorated callable.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    @register_tool_category(NEST_REPORT_CATEGORY_KEY)
    @tool
    async def report_anomaly(
        category: str,
        description: str,
        severity: str = DEFAULT_SEVERITY,
        suspected_cause: str | None = None,
    ) -> str:
        """Report something that looks wrong in this nest (one row, no voice).

        Args:
            category: What kind of problem this is. Free text is accepted
                and folded at write time into a closed vocabulary
                (tool-failure, performance, resource-exhaustion,
                loop-stall, wedge-suspected, data-corruption,
                auth-permission, external-dependency, mcp-failure,
                llm-provider, queue-scheduling, environment-config, or
                the reserved ``other``). The raw text is preserved.
            description: What is wrong, in your own words. Required and
                non-empty.
            severity: One of ``minor``, ``major`` (default) or
                ``critical``. An out-of-set value is refused, never
                folded.
            suspected_cause: Your hypothesis about the cause (optional).

        Returns:
            The acceptance envelope (field per line) or the typed
            refusal envelope.
        """
        # ── rung 1: tool-disabled ─────────────────────────────────────
        # The kill-switch, an absent config, the AM-16 per-writer deny
        # list and an unmounted substrate are all "this writer cannot
        # report right now" — the tool stays PRESENT and refuses LOUDLY.
        config = _nest_config(manager)
        if config is None:
            return _refusal(
                "report_anomaly",
                TOKEN_TOOL_DISABLED,
                "the nest-report configuration is not mounted on the "
                "manager (the anomaly stack is boot-degraded)",
            )
        if not getattr(config, "enabled", False):
            return _refusal(
                "report_anomaly",
                TOKEN_TOOL_DISABLED,
                "the report_anomaly tool is disabled by the operator "
                "(ENSEMBLE_NEST_REPORT_ENABLED=false)",
            )
        if _disabled_for(config, caller_agent_id):
            return _refusal(
                "report_anomaly",
                TOKEN_TOOL_DISABLED,
                "the report_anomaly tool is disabled for this agent type "
                "(ENSEMBLE_NEST_REPORT_DISABLED_AGENTS)",
            )
        service = _anomaly_service(manager)
        if service is None:
            return _refusal(
                "report_anomaly",
                TOKEN_TOOL_DISABLED,
                "the anomaly service is not mounted on the manager "
                "(the anomaly stack is boot-degraded)",
            )

        # ── rung 2: invalid-input (nothing is persisted) ──────────────
        # The reporter's IDENTITY is validated here too: the service
        # refuses an empty agent type / instance id, and that is an
        # input refusal, not a persistence failure.
        if not caller_agent_id:
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                "the reporting agent type is unknown",
            )
        if not caller_instance_id:
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                "the reporting instance id is unknown",
            )
        if not isinstance(category, str) or not category.strip():
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                "category must be a non-empty string",
            )
        if not isinstance(description, str) or not description.strip():
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                "description must be a non-empty string",
            )
        if suspected_cause is not None and not isinstance(suspected_cause, str):
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                "suspected_cause must be a string or omitted",
            )
        severity_value = severity
        if not isinstance(severity_value, str):
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                "severity must be a string",
            )
        severity_value = severity_value.strip().lower()
        if severity_value not in SEVERITIES:
            # R3 — NEVER silently fold: folding critical down to major
            # would HIDE a nest-level event.
            return _refusal(
                "report_anomaly",
                TOKEN_INVALID_INPUT,
                f"severity must be one of {'|'.join(SEVERITIES)}",
            )

        # ── rungs 3-5: the service's refusals + the accept path ───────
        try:
            result = await service.accept_report(
                agent_id=caller_agent_id,
                instance_id=caller_instance_id,
                category=category,
                description=description,
                severity=severity_value,
                suspected_cause=suspected_cause,
            )
        except Exception as exc:  # noqa: BLE001 — the tool NEVER raises
            token = _service_token(exc)
            if token == TOKEN_RATE_LIMITED:
                return _refusal(
                    "report_anomaly",
                    TOKEN_RATE_LIMITED,
                    "this agent type's report budget for the current "
                    "window is exhausted",
                )
            if token == TOKEN_CAPACITY_EXHAUSTED:
                return _refusal(
                    "report_anomaly",
                    TOKEN_CAPACITY_EXHAUSTED,
                    "the nest-wide anomaly capacity ceiling is reached",
                )
            logger.warning(
                "report_anomaly persist failed for agent type %r: %s",
                caller_agent_id,
                redact_exc_str(exc, limit=200),
            )
            return _refusal(
                "report_anomaly",
                TOKEN_PERSIST_FAILED,
                "the anomaly report could not be persisted",
            )

        # The category's raw form is capped at 64 chars (R1) — the
        # service stores the capped raw verbatim; the envelope says so.
        truncated = bool(getattr(result, "truncated", False)) or (
            len(category) > CATEGORY_RAW_MAX_CHARS
        )
        return _success_envelope(result, truncated=truncated)

    # The ``tool_help`` surface — the per-tool full documentation. The
    # category-level doctrine lives in ``CATEGORY_DOC``; this is the
    # per-call contract (arguments, envelope, refusals).
    report_anomaly._full_doc_ = """The report_anomaly tool — report something \
that looks wrong in this nest (ONE row, no voice).

ARGUMENTS
  category (str, REQUIRED) — what kind of problem this is. Free text is
    accepted and folded AT WRITE TIME into a closed vocabulary:
    tool-failure, performance, resource-exhaustion, loop-stall,
    wedge-suspected, data-corruption, auth-permission,
    external-dependency, mcp-failure, llm-provider, queue-scheduling,
    environment-config, or the reserved ``other``. The raw text is
    preserved verbatim (capped at 64 chars) so a vocabulary gap stays
    visible.
  description (str, REQUIRED) — what is wrong, in your own words.
    Non-empty; capped at 1000 chars (truncated, never refused).
  severity (str, default "major") — one of minor | major | critical. An
    out-of-set value is REFUSED, never folded (folding critical down to
    major would hide a nest-level event).
  suspected_cause (str | None, optional) — your hypothesis about the
    cause; capped at 500 chars.

THE SUCCESS ENVELOPE (one field per line)
  OK: anomaly accepted
  report_id=<uuid>
  cluster_id=<uuid | unclustered>   (unclustered = the cluster assignment
                                    failed; the row was terminalized)
  cluster_report_count=<n>
  cluster_first_report_at=<ISO-8601>
  category_fold_notice=<notice>   (ONLY when the category was folded)
  truncated=true                  (ONLY when a field was capped)

THE REFUSAL LADDER (checked in this order)
  tool-disabled      — the kill-switch (ENSEMBLE_NEST_REPORT_ENABLED=false),
                       an absent config, this agent type on the deny list
                       (ENSEMBLE_NEST_REPORT_DISABLED_AGENTS), or the
                       anomaly substrate not being mounted.
  invalid-input      — an empty description, an out-of-set severity, a
                       non-string argument. NOTHING is persisted.
  rate-limited       — this agent TYPE's budget for the window is spent.
  capacity-exhausted — the nest-wide capacity ceiling is reached.
  persist-failed     — the row could not be written.

THE BUDGET IS PER AGENT TYPE (AM-10) — the budget key is the agent
TYPE, never the instance id: spawning does NOT multiply the budget, a
type's instances SHARE one budget, and the key space is not
agent-expandable.

BOUNDS ARE A HIERARCHY: the per-tree row cap SHEDS (never refuses, so
one tree cannot go dark); the global total-row ceiling is the hard
nest-wide wall; the 1,500/hour knob is an operator brake, not a security
fence; the per-type caps are the inner brake. "5 per 10 min" is
BEST-EFFORT under concurrency (read-then-insert), not a hard invariant.
"""

    return [report_anomaly]


__all__ = [
    "CATEGORY_DOC",
    "CATEGORY_NAME",
    "DEFAULT_SEVERITY",
    "NEST_REPORT_CATEGORY_KEY",
    "PENDING_CLUSTER_ID",
    "SEVERITIES",
    "SERVICE_TOKENS",
    "TOOL_NAMES",
    "TOKEN_CAPACITY_EXHAUSTED",
    "TOKEN_INVALID_INPUT",
    "TOKEN_PERSIST_FAILED",
    "TOKEN_RATE_LIMITED",
    "TOKEN_TOOL_DISABLED",
    "UNCLUSTERED_CLUSTER_ID",
    "create_nest_report_tools",
]
