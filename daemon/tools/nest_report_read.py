"""The ``nest-report-read`` privileged tool category (2 read-only tools).

Two tools
-----------
``anomaly_clusters``     — the decision-ready GROUPS (AM-6c): one entry
                           per ``(tree_root_instance_id, category)``.
``anomaly_report_detail`` — one cluster's member reports, freshest-first.

ACCESS POSTURE — TWO LAYERS, BOTH REQUIRED (the ``metrics-read`` shape)
------------------------------------------------------------------------
1. The ``nest-report-read`` category is PRIVILEGED (entry #18), so it
   never joins the empty-allow default-grant universe.
2. A privileged category alone is NOT sufficient — a **literal tool
   name** in an allow list bypasses the category gate entirely.  So
   every tool body ALSO calls the one shared module-level helper
   :func:`_is_nest_report_authorized`.

Both gates are required; neither substitutes for the other.  The
allow-list is ``{maintenancer, devops, wanderer}`` (the R8 ops trio).

ONE PROJECTOR, TWO TRANSPORTS (AM-1)
-------------------------------------
The tools read through the ANOMALY SERVICE
(:class:`daemon.services.anomaly_service.AnomalyService`) — never a raw
engine handle, never direct repository access from a tool body, never
REST-scraping a door (R8).  ``GET /api/nest-reports/*`` calls the SAME
``service.list_clusters`` / ``service.list_cluster_reports`` projector,
so the grouped projection cannot drift between the two transports.

THE TOOLS STAY PRESENT ON A DEGRADED BOOT (RULING-1)
-----------------------------------------------------
The factory gates on MODULE existence only; the tool BODY resolves the
anomaly service on the manager PER CALL and returns the typed
``anomaly-unavailable`` refusal — PLUS a degraded block that states the
read's scope and when it happened — when the mount is ``None``.  A
degraded boot therefore leaves the tools PRESENT and loudly refusing
rather than making the surface VANISH SILENTLY — for a diagnosis
surface, silent absence is the worst failure mode.

TOOLS NEVER RAISE
-----------------
Every failure returns a typed refusal string.  The four-token ladder
mirrors the door's status semantics IN-PROCESS:
``unknown-agent`` · ``not-authorized`` · ``anomaly-unavailable`` ·
``invalid-input``.  Authorization fires FIRST — an unauthorized caller
must never learn whether the substrate is mounted.

THE READS HAVE NO SIDE EFFECT
-----------------------------
A read writes NO row, deletes NOTHING, and triggers NOTHING.

AM-12 — THE ``excerpt`` FIELD IS OPT-IN AND LIST-EXCLUDED
---------------------------------------------------------
The LIST payload (``anomaly_clusters``) projects NO ``excerpt`` key at
all — not empty, not truncated: the key is ABSENT.  The detail payload
carries a member ``excerpt`` ONLY behind an explicit
``include_excerpt=true`` — the default detail payload carries no
excerpt anywhere.  The excerpt is raw tool stdout harvested from a
transcript; it is the heaviest field and belongs only to a deliberate
drill-down.

AM-7 — PEAK SEVERITY LABELLING
------------------------------
Every severity the surfaces render is a PEAK (``critical (peak)``) —
``max_severity`` is a max-rank rollup over the cluster's window, never
a "current" severity.

A BOUND THAT DROPS SOMETHING IS NEVER SILENT
---------------------------------------------
Both tools carry the SIX-member bound block (from the service's
projector) and the tool's own byte-ceiling belt: when either bites, the
result says so and a ``narrow_hint`` names a CONCRETE next filter.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from daemon._redact import redact_exc_str
from daemon.services.metrics_query.constants import (
    RESPONSE_BYTE_CEILING,
    WINDOW_SECONDS,
)

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

#: The category key — KEBAB-case, byte-identical in every place it
#: appears (the module, the registry, the three ``meta.json`` allow
#: entries and the pin packs).  The OPERATIONAL surface takes the
#: ``nest-`` prefix (R9) while the TOOL NAMES keep the DATA vocabulary
#: (``anomaly_*``) — the house pattern (``metrics-read`` →
#: ``metrics_*``).
NEST_REPORT_READ_CATEGORY_KEY = "nest-report-read"

CATEGORY_NAME = "Nest Report Read"

CATEGORY_DOC = """\
Read-only views over the nest's anomaly store (the anomaly_reports /
anomaly_clusters tables) — the SAME decision-ready projection the
/api/nest-reports REST route serves from (one projector, two
transports).

Tools in this category:
- ``anomaly_clusters`` — the decision-ready GROUPS: one entry per
  (tree root, category), with the peak severity, the occurrence badge
  (how many clusters), the summed report count, the member cluster
  ids and the derived open flag
- ``anomaly_report_detail`` — one cluster's member reports, freshest
  first, with the excerpt only behind an explicit include_excerpt=true

These tools READ. They never write, never delete and never trigger a
collection.

SEVERITY IS A PEAK
------------------
Every severity on this surface is a PEAK ("critical (peak)") — the
cluster's max_severity is a max-rank rollup over its window and never
de-escalates; it is not a "current" severity.

OPEN IS DERIVED (R5)
--------------------
A cluster is open iff its last report is within the resolve window
(default 1h; ``NestReportConfig.resolve_window_seconds``). The flag is
computed SERVER-side at query time; there is no stored status and
nothing to drift.

THE EXCERPT IS OPT-IN (AM-12)
------------------------------
The list projection carries NO excerpt key at all, and the detail
projection includes a member excerpt only with include_excerpt=true.

THE COST OF A CALL
------------------
A default ``anomaly_clusters`` call returns up to 20 groups (~a few
KB). The excerpt is the heaviest field and is opt-in precisely so a
morning scan stays cheap.

NON-GOALS
---------
There is no write surface here (that is ``report_anomaly`` in the
non-privileged ``nest-report`` category) and no metric-shaped series
read (that is the ``metrics-read`` category — F23 appears there
automatically).
"""

#: The agents allowed to read the anomaly store.  The privileged
#: category gates VISIBILITY; this set gates AUTHORITY.  Both are
#: required (the R8 ops trio).
NEST_REPORT_READ_AUTHORIZED_AGENTS: frozenset[str] = frozenset(
    {"maintenancer", "devops", "wanderer"}
)

#: The two tool names this module constructs — the canonical list the
#: registry's ``KNOWN_TOOL_NAMES`` / ``DYNAMIC_TOOL_NAMES`` and the
#: registration pin pack agree with.  The TOOL names keep the DATA
#: vocabulary (R9's two-layer split).
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "anomaly_clusters",
        "anomaly_report_detail",
    }
)

# ── the four refusal tokens (the in-process mirror of the route's
#    status semantics: 401 / 403 / 503 / 400) ─────────────────────────────
TOKEN_UNKNOWN_AGENT = "unknown-agent"
TOKEN_NOT_AUTHORIZED = "not-authorized"
TOKEN_UNAVAILABLE = "anomaly-unavailable"
TOKEN_INVALID_INPUT = "invalid-input"

# ── the tool's own caps (the seam constants are IMPORTED, never
#    duplicated — a duplicated literal silently diverges from the seam) ──
MAX_TOP_N = 50
MAX_WINDOW_S = WINDOW_SECONDS

#: The bytes RESERVED for the byte-ceiling caveat + the narrow_hint.
_CAVEAT_HEADROOM_BYTES = 2048

#: The narrowing filters EACH body actually accepts (5b-③: a disclosure
#: that names a filter the CALLER cannot pass is a disclosure the agent
#: cannot act on).
_BODY_NARROW_LEVERS: dict[str, str] = {
    "anomaly_clusters": (
        "a smaller `window_s=`, a specific `severity=` or `category=`, or "
        "a smaller `limit`"
    ),
    "anomaly_report_detail": (
        "a smaller `limit` (the cluster id pins the rest of the query)"
    ),
}


def _byte_ceiling_caveat(body: str) -> str:
    levers = _BODY_NARROW_LEVERS.get(body, "a narrower query")
    return (
        "The response was trimmed to fit the daemon's byte ceiling — the "
        "items below are a SUBSET of what matched. Narrow the query ("
        + levers
        + ") to read the rest."
    )


def _byte_ceiling_hint(body: str) -> str:
    levers = _BODY_NARROW_LEVERS.get(body, "a narrower query")
    return (
        "the response hit the daemon's byte ceiling — narrow the query ("
        + levers
        + ") and call again."
    )


def _is_nest_report_authorized(agent_id: str) -> bool:
    """Is this agent allowed to read the anomaly store?

    ONE shared helper, called by BOTH tool bodies — the set lives in
    exactly one place.  Fail-closed: a blank or non-``str`` ``agent_id``
    is NEVER authorized.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in NEST_REPORT_READ_AUTHORIZED_AGENTS


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope.

    ``Error: {label} REFUSED — reason={token}: {message}`` — the same
    shape the other privileged categories use, so one parser reads them
    all.
    """
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def _anomaly_service(manager: Any) -> Any:
    """The anomaly service mounted on this manager (or ``None``).

    The house convention is a private manager attribute
    (``manager._anomaly_service``); the public alias is accepted as a
    second door.  Resolved PER CALL (RULING-1) so a degraded boot leaves
    the tools present and loudly refusing.
    """
    for attribute in ("_anomaly_service", "anomaly_service"):
        service = getattr(manager, attribute, None)
        if service is not None:
            return service
    return None


def _degraded_block(scope: str) -> dict[str, Any]:
    """The degraded-read disclosure (a missing substrate is LOUD).

    States WHAT would have been read and WHEN the attempt happened —
    the reader can tell "nothing matched" from "the store is down".
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "degraded": True,
        "scope": scope,
        "observed_at_utc": now,
        "note": (
            "the anomaly substrate is unavailable on this boot — this is a "
            "REFUSAL, not an empty result; the data below is UNKNOWN, not "
            "'no anomalies'"
        ),
    }


# ── the output budget (the metrics-read trim, ported whole) ───────────────


def _encoded_size(payload: Any) -> int:
    """The encoded byte size of a payload — in the tool's OWN output form.

    The probe measures the form the tool actually RETURNS
    (``json.dumps(..., indent=2)``) — the C1 regression lesson: a probe
    measured against a different object than the body ships is not a
    bound.
    """
    try:
        return len(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return 0


def _trim_to_byte_ceiling(
    payload: dict[str, Any],
    *,
    key: str,
    items: list[Any],
    target: int | None = None,
) -> list[Any]:
    """Binary-search the largest prefix of ``items`` that fits ``target``.

    The TRIM measures the SHIPPED projection (the payload with the
    candidate items), never a raw-row fiction.
    """
    if target is None:
        target = RESPONSE_BYTE_CEILING - _CAVEAT_HEADROOM_BYTES
    low, high, best = 0, len(items), 0
    while low <= high:
        mid = (low + high) // 2
        if _encoded_size({**payload, key: items[:mid]}) <= target:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return items[:best]


async def _offload(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """The off-loop body build (the ``/api/work`` precedent).

    The projection build runs on the dedicated ``db-offload`` executor
    and never on the event loop (D-UX-16 / AmRULING-4).  The SERVICE's
    projector also offloads its persistence reads; this seam offloads
    the JSON-size probe and any residual sync work.
    """
    from daemon.services.db_offload import offload as _off

    return await _off(fn, *args, **kwargs)


def create_nest_report_read_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the two ``nest-report-read`` tools for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — the tools read
            ``manager._anomaly_service`` (the ONE mounted service, the
            same one the REST route reads through).
        current_instance_id: The caller's instance id (unused by the
            reads; kept for factory-signature parity).
        agent_id: The caller's agent id. Captured at factory time and
            checked by :func:`_is_nest_report_authorized` in every tool
            body — the layer-2 gate a literal tool name in an allow
            list cannot bypass.
        agent_tag: The caller's version tag (unused; signature parity).

    Returns:
        A list of the two ``@tool``-decorated callables.
    """
    caller_agent_id = agent_id or ""

    class AnomalyClustersInput(BaseModel):
        """Input schema for anomaly_clusters."""

        window_s: Annotated[
            int | None,
            Field(
                default=None,
                description=(
                    "Lookback window in seconds: only clusters whose last "
                    "report is within the window. Default 3600; clamped to "
                    f"at most {MAX_WINDOW_S}."
                ),
            ),
        ]
        severity: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Filter by the cluster's PEAK severity: minor | major | "
                    "critical."
                ),
            ),
        ]
        category: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Filter by canonical category (one of the closed "
                    "vocabulary, e.g. 'tool-failure', 'llm-provider')."
                ),
            ),
        ]
        limit: Annotated[
            int | None,
            Field(
                default=None,
                description=(
                    f"Maximum groups returned (default 20, at most {MAX_TOP_N})."
                ),
            ),
        ]

    @register_tool_category(NEST_REPORT_READ_CATEGORY_KEY)
    @tool(args_schema=AnomalyClustersInput)
    async def anomaly_clusters(
        window_s: int | None = None,
        severity: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> str:
        """The decision-ready anomaly GROUPS — one entry per (tree root, category) with the peak severity, occurrence badge and derived open flag. Use tool_help("anomaly_clusters") for details."""
        if not _is_nest_report_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "anomaly_clusters",
                    TOKEN_UNKNOWN_AGENT,
                    "the anomaly store is readable by maintenancer, devops "
                    "and wanderer only",
                )
            return _refusal(
                "anomaly_clusters",
                TOKEN_NOT_AUTHORIZED,
                "the anomaly store is readable by maintenancer, devops and "
                "wanderer only",
            )
        service = _anomaly_service(manager)
        if service is None:
            payload = {
                "refusal": _refusal(
                    "anomaly_clusters",
                    TOKEN_UNAVAILABLE,
                    "the anomaly service is not mounted on the manager "
                    "(the anomaly substrate is boot-degraded)",
                ),
                "degraded_read": _degraded_block(
                    "anomaly_clusters: the grouped (tree root, category) "
                    "incident view over the anomaly store"
                ),
            }
            return json.dumps(payload, indent=2, default=str)
        try:
            payload = await service.list_clusters(
                window_s=window_s,
                severity=severity,
                category=category,
                limit=limit,
            )
            payload["query"] = {**payload.get("query", {}), "agent_id": caller_agent_id}
            items = list(payload["groups"])
            if _encoded_size(payload) > RESPONSE_BYTE_CEILING - _CAVEAT_HEADROOM_BYTES:
                kept = await _offload(
                    _trim_to_byte_ceiling, payload, key="groups", items=items
                )
                payload["groups"] = kept
                payload["bound"] = {
                    **payload["bound"],
                    "returned_count": len(kept),
                    "truncated": True,
                    "narrow_hint": _byte_ceiling_hint("anomaly_clusters"),
                }
                payload["byte_ceiling_caveat"] = _byte_ceiling_caveat(
                    "anomaly_clusters"
                )
            return json.dumps(payload, indent=2, default=str)
        except ValueError as exc:
            # AnomalyInputError subclasses ValueError — the typed
            # invalid-input refusal (an out-of-set filter), without
            # importing the service module at tool-import time.
            return _refusal(
                "anomaly_clusters",
                TOKEN_INVALID_INPUT,
                str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning(
                "anomaly_clusters failed: %s", redact_exc_str(exc, limit=200)
            )
            return _error(f"anomaly_clusters failed: {exc}")

    class AnomalyReportDetailInput(BaseModel):
        """Input schema for anomaly_report_detail."""

        cluster_id: Annotated[
            str,
            Field(
                description=(
                    "The cluster identity (anomaly_clusters.id — the uuid "
                    "the grouped list's cluster_ids carry)."
                ),
            ),
        ]
        limit: Annotated[
            int | None,
            Field(
                default=None,
                description=(
                    f"Maximum member reports returned (default 20, at most {MAX_TOP_N})."
                ),
            ),
        ]
        include_excerpt: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Include each member's stored, bounded, scrubbed excerpt. "
                    "Default false — the excerpt is the heaviest field and "
                    "is opt-in (AM-12b)."
                ),
            ),
        ]

    @register_tool_category(NEST_REPORT_READ_CATEGORY_KEY)
    @tool(args_schema=AnomalyReportDetailInput)
    async def anomaly_report_detail(
        cluster_id: str,
        limit: int | None = None,
        include_excerpt: bool = False,
    ) -> str:
        """One anomaly cluster's member reports, freshest first (excerpt only with include_excerpt=true). Use tool_help("anomaly_report_detail") for details."""
        if not _is_nest_report_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "anomaly_report_detail",
                    TOKEN_UNKNOWN_AGENT,
                    "the anomaly store is readable by maintenancer, devops "
                    "and wanderer only",
                )
            return _refusal(
                "anomaly_report_detail",
                TOKEN_NOT_AUTHORIZED,
                "the anomaly store is readable by maintenancer, devops and "
                "wanderer only",
            )
        service = _anomaly_service(manager)
        if service is None:
            payload = {
                "refusal": _refusal(
                    "anomaly_report_detail",
                    TOKEN_UNAVAILABLE,
                    "the anomaly service is not mounted on the manager "
                    "(the anomaly substrate is boot-degraded)",
                ),
                "degraded_read": _degraded_block(
                    "anomaly_report_detail: the member reports of one "
                    "anomaly cluster"
                ),
            }
            return json.dumps(payload, indent=2, default=str)
        try:
            payload = await service.list_cluster_reports(
                cluster_id=cluster_id,
                limit=limit,
                include_excerpt=bool(include_excerpt),
            )
            if payload is None:
                return _refusal(
                    "anomaly_report_detail",
                    TOKEN_INVALID_INPUT,
                    f"no anomaly cluster with id {cluster_id!r}",
                )
            payload["query"] = {**payload.get("query", {}), "agent_id": caller_agent_id}
            items = list(payload["reports"])
            if _encoded_size(payload) > RESPONSE_BYTE_CEILING - _CAVEAT_HEADROOM_BYTES:
                kept = await _offload(
                    _trim_to_byte_ceiling, payload, key="reports", items=items
                )
                payload["reports"] = kept
                payload["bound"] = {
                    **payload["bound"],
                    "returned_count": len(kept),
                    "truncated": True,
                    "narrow_hint": _byte_ceiling_hint("anomaly_report_detail"),
                }
                payload["byte_ceiling_caveat"] = _byte_ceiling_caveat(
                    "anomaly_report_detail"
                )
            return json.dumps(payload, indent=2, default=str)
        except ValueError as exc:
            # AnomalyInputError subclasses ValueError — the typed
            # invalid-input refusal (an empty/invalid cluster id).
            return _refusal(
                "anomaly_report_detail",
                TOKEN_INVALID_INPUT,
                str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning(
                "anomaly_report_detail failed: %s", redact_exc_str(exc, limit=200)
            )
            return _error(f"anomaly_report_detail failed: {exc}")

    return [anomaly_clusters, anomaly_report_detail]


__all__ = [
    "CATEGORY_DOC",
    "CATEGORY_NAME",
    "NEST_REPORT_READ_AUTHORIZED_AGENTS",
    "NEST_REPORT_READ_CATEGORY_KEY",
    "TOOL_NAMES",
    "create_nest_report_read_tools",
]
