"""Mission tools — `get_mission` / `await_mission` / `list_missions`.

M2 (mission-class, 2026-09-02, ``feature/mission-class``) — the agent
tool surface for the mission read-model projection. Builds on the M1
additive mission response fields (always-on since WS3) and the M4(i)-HTTP
``GET /missions`` pull-forward: the same ``MissionResolver`` leaf service
serves all three surfaces — no new writers, no JobItem creation, no
admission-state mutation. Census stays frozen at 23
(``daemon/job_state/constitution.py``).

The tools are the **agent-facing** path for the two-layer model
(``docs/job-task-system.md`` §6.7):

  * Jobs = transport (was my submission handled?)
  * Missions = work (is the work done?)

The three guardrails that make the wrong-predicate trap structurally
hard (per ``.agents/shared/planning/mission-class/agent-contract-draft.md``
§3):

  1. **Naming asymmetry** — ``await_mission`` (outcome) vs ``watch_job``
     (transport). No shared verb; tool-selection-time disambiguation.
  2. **``outcome`` token asymmetry** — transport payloads carry
     ``"outcome": null`` ALWAYS (the new ``outcome`` field on
     :class:`JobResponse`); mission payloads carry the outcome value
     ALWAYS when terminal, ``None`` when live (the ``outcome`` field
     on :func:`_mission_snapshot_dict`).
  3. **``mission_ref`` cross-reference** — every terminal job payload
     carries ``mission_ref: {mission_id, agent_id, liveness}`` next to
     ``outcome: null`` so an agent cannot read a terminal job state
     without seeing the linked mission's liveness in the same payload.

Architecture
------------

The factory :func:`create_mission_tools` mirrors the
``create_job_tools`` pattern: a ``MissionResolver`` instance is injected
at construction time (wired in ``daemon/manager.py`` next to
``set_missions_resolver``); the resolver is a **leaf READ service** —
``SQLModelInstanceRepository`` + ``JobRepository``, both read-only. The
tools themselves never touch the DB; every read goes through the
resolver. ``await_mission`` blocks via an asyncio poll loop with a
configurable ``timeout`` and ``poll_interval`` — same blocking-poll
semantic the contract draft sketches against the ``watch_job`` watch
primitive (``daemon/tools/job_queue.py:1320-1360``).

W4 hazard
---------

DEAD-job missions surface ``terminal_reason: "dead_letter"`` regardless
of a since-revived instance (the W4-hazard rule from
``agent-contract-draft.md`` §2 and ``docs/job-task-system.md`` §8.2).
The resolver owns this branch
(:meth:`daemon.services.mission_resolver.MissionResolver._project`) —
the tools faithfully project it through without ever re-deriving the
terminal cause from ``Instance.status`` alone.

Identities
----------

* ``mission_id == instance_id`` (one mission per instance, the
  leader's lean, adjudicated under pressure-test — spec §3).
* ``parent_mission_id == instance.parent_id`` (permanent across
  terminate→revive; ``instances.parent_id`` is permanent by design).
* A mirror receipt (``job_type='message'``) shares its instance's
  mission — the link lives on ``JobItem.instance_id``.

Tools are READERS — STOP conditions
-----------------------------------

The contract is explicit (deliverable #5 in WS1): these tools perform
NO state writes. No job-state mutation, no admission-state writes, no
mirror-row updates, no DB writes of any kind. The census/writer count
stays frozen at 23 through M1–M3; ``MissionResolver`` is a leaf service
that touches none of the ``KNOWN_ADMISSION_STATE_WRITERS`` sites. The
only synchronous I/O in this module is read-only ``SELECT`` against
``instances`` / ``job_queue_items`` — via the resolver — and an
asyncio sleep loop for the await poll. If a future task needs ANY
write here, the answer is "STOP and report the blocker" — do not add a
write path.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.services.mission_resolver import (
        MissionRecord,
        MissionResolver,
    )

logger = logging.getLogger(__name__)


CATEGORY_NAME = "Missions"
CATEGORY_DOC = """\
Mission read-model — `get_mission`, `await_mission`, `list_missions`.

Missions are the work layer (was the underlying work actually done?);
jobs are the transport layer (was my submission handled?). The two
questions are different; these tools answer the mission question.

For the transport question, use the job tools (`job_get`, `job_list`,
`watch_job`, etc.). For the outcome question, use these mission tools.
The cross-reference `mission_ref` on every terminal job payload ties
the two layers together in a single read.

These tools NEVER write — they are pure read-model projections of
`Instance.status` (+ `JobItem.terminal_reason` for the W4 DEAD
hazard). The mission layer's storage is a M4-ii gated concern; today,
every mission field is best-effort derived from the instance/job
read-model.
"""


# Default values for ``await_mission`` — chosen to match the contract
# draft (§2 ``await_mission`` entry) and the spirit of the
# ``watch_job`` blocking-poll primitive
# (``daemon/tools/job_queue.py:1320-1360``).
AWAIT_MISSION_DEFAULT_TIMEOUT: float = 600.0
AWAIT_MISSION_DEFAULT_POLL_INTERVAL: float = 2.0

# ``list_missions`` pagination bounds — aligned with the HTTP surface's
# ``DEFAULT_PAGE_LIMIT`` / ``MAX_PAGE_LIMIT`` (``daemon/constants.py``)
# so the two surfaces share a single page-shape contract. The contract
# draft pins the upper bound at 200 (one mission per instance; large
# fan-outs are common in this system).
LIST_MISSIONS_DEFAULT_LIMIT: int = 50
LIST_MISSIONS_MAX_LIMIT: int = 200


# Liveness values accepted by the ``list_missions`` ``liveness``
# filter — derived from the canonical mission vocabulary (spec §6.7).
# Inlined rather than imported so a tooling-only consumer does not
# need to follow the ``daemon.services`` import chain. Keep aligned
# with ``_STATUS_CANONICAL_MAP`` minus ``"dead_letter"`` (which is a
# ``terminal_reason``, never a liveness — spec §8.2).
_LIST_MISSIONS_LIVENESS_VALUES: frozenset[str] = frozenset(
    {
        "pending",
        "processing",
        "paused",
        "completed",
        "failed",
        "cancelled",
    }
)


# ── Tool input schemas (Pydantic — required for @tool(args_schema=…)) ──


class ListMissionsInput(BaseModel):
    """Input schema for ``list_missions``.

    All filters are optional. ``limit`` is clamped to
    ``[1, LIST_MISSIONS_MAX_LIMIT]`` (the contract draft's 200-page
    ceiling). Unknown ``liveness`` values are accepted at the wire
    surface but produce an honestly-empty page (no InstanceStatus
    member projects onto them today — the §8.2 "source-less filter"
    case, mirrored by the HTTP list).
    """

    agent_id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional exact-match filter on Instance.agent_id. "
                "Use the project's agent shortname (e.g. 'developer', "
                "'leader')."
            ),
        ),
    ] = None
    liveness: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional canonical mission-liveness filter: pending | "
                "processing | paused | completed | failed | cancelled. "
                "Single value only at the tool surface (the HTTP list "
                "accepts comma-separated multi — the tool keeps the "
                "shape simple per the draft §2 'single value' "
                "sketch). Unknown values yield an honestly-empty page."
            ),
        ),
    ] = None
    parent_mission_id: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional exact-match filter on parent_mission_id "
                "(= instances.parent_id). Use to scope a subtree."
            ),
        ),
    ] = None
    since: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Optional ISO-8601 lower bound on last_activity_at — "
                "rows with last_activity_at < since are excluded. "
                "Stale-tolerant: a value the resolver cannot parse "
                "silently degrades to 'no since filter' (logged at "
                "DEBUG, not warning — same shape as the HTTP list's "
                "unknown-liveness degrade)."
            ),
        ),
    ] = None
    limit: Annotated[
        int,
        Field(
            default=LIST_MISSIONS_DEFAULT_LIMIT,
            description=(
                "Maximum number of mission summaries to return. "
                "Clamped to [1, 200]. Default 50."
            ),
        ),
    ] = LIST_MISSIONS_DEFAULT_LIMIT


# ── Helpers (pure — no DB, no I/O) ───────────────────────────────────────


def _mission_snapshot_dict(record: "MissionRecord") -> dict[str, Any]:
    """Compose the ``get_mission`` snapshot payload (contract draft §2).

    Field order and shape mirror the spec — every key the draft lists
    appears in the same shape. ``epochs`` is a best-effort single-element
    array summarising the current/last interval (per F7 reconciliation
    note: read-surface ``mission_epoch`` stays constant-1 until M4(ii);
    the tool's ``epochs`` is the best-effort derived view; current epoch
    is always ``1`` today, so a one-element array is the honest answer
    rather than a fabricated multi-element history).

    Args:
        record: The resolved :class:`MissionRecord`. Guaranteed
            non-null by the caller (``resolve()`` returns ``None``
            for unknown instances; the tool handles that before
            reaching this helper).

    Returns:
        A JSON-friendly dict matching the contract draft §2 schema.
    """
    # Identity.
    mission_id = record.mission_id

    # Liveness / terminal cause — ``outcome`` is the asymmetric
    # counterpart to transport payloads' ``"outcome": null``. ALWAYS set
    # when terminal (mission layer carries the value); ``None`` when
    # live. The four terminal-cause tokens are the canonical mission
    # vocabulary (spec §6.7); ``dead_letter`` is a terminal_reason
    # value that surfaces through this field per the W4 rule.
    outcome = record.terminal_reason

    # W4-hazard — propagated verbatim by the resolver; we do not
    # re-derive. The renderer must not let liveness override DEAD: a
    # dead-linked JobItem stays ``dead_letter`` even after a since-
    # revived instance.
    terminal_reason = record.terminal_reason

    # Epoch fields — best-effort derived. ``epoch_count`` and
    # ``last_epoch_at`` are derivable from the read-model (epoch is
    # constant 1 today); ``epochs`` is a single-element best-effort
    # summary that does not require ANY storage write (per F7 note:
    # "If best-effort epoch derivation would require ANY storage/state
    # write, degrade gracefully").
    epoch = record.epoch  # constant 1 from the resolver (non-degraded)
    epoch_count = epoch if epoch is not None else 0
    last_epoch_at = record.last_activity_at

    # Best-effort single-element epoch summary — the tool's view of
    # history. The DB does not store per-epoch timestamps (spec §3
    # known-limitation), so the only honest entry is the current
    # interval. ``ended_at`` is populated when the mission is
    # terminal AND ``last_activity_at`` is available; ``None`` while
    # the mission is live.
    epochs: list[dict[str, Any]] = []
    if mission_id is not None and record.started_at is not None:
        epoch_entry: dict[str, Any] = {
            "seq": epoch if epoch is not None else 1,
            "started_at": record.started_at,
            "ended_at": (
                record.last_activity_at
                if terminal_reason is not None
                else None
            ),
            "kind": "initial",
            "terminal_reason": terminal_reason,
        }
        epochs.append(epoch_entry)

    return {
        "mission_id": mission_id,
        "agent_id": record.agent_id,
        "parent_mission_id": record.parent_mission_id,
        "liveness": record.liveness,
        "terminal_reason": terminal_reason,
        "epoch": epoch,
        "epochs": epochs,
        "epoch_count": epoch_count,
        "last_epoch_at": last_epoch_at,
        "linked_jobs": list(record.linked_jobs) if record.linked_jobs else [],
        "started_at": record.started_at,
        "last_activity_at": record.last_activity_at,
        # Asymmetric outcome token — null when live, value when
        # terminal. NEVER omitted (a literal key the agent can branch
        # on; null = "NOT done" by construction).
        "outcome": outcome,
    }


def _mission_summary_dict(record: "MissionRecord") -> dict[str, Any]:
    """Compose the ``list_missions`` summary payload (contract draft §2).

    The summary carries identity + liveness + ``epoch_count`` +
    ``last_epoch_at`` only — the full ``epochs`` array is omitted
    (the snapshot path returns it; the list path stays bounded).

    Args:
        record: The resolved :class:`MissionRecord`.

    Returns:
        A JSON-friendly dict matching the contract draft §2 summary
        shape.
    """
    epoch = record.epoch
    return {
        "mission_id": record.mission_id,
        "agent_id": record.agent_id,
        "parent_mission_id": record.parent_mission_id,
        "liveness": record.liveness,
        "terminal_reason": record.terminal_reason,
        "epoch": epoch,
        "epoch_count": epoch if epoch is not None else 0,
        "last_epoch_at": record.last_activity_at,
        "linked_jobs": (
            list(record.linked_jobs) if record.linked_jobs else []
        ),
        "started_at": record.started_at,
        "last_activity_at": record.last_activity_at,
        # Asymmetric outcome token — same rule as the snapshot.
        "outcome": record.terminal_reason,
    }


def _parse_since(value: str | None) -> datetime | None:
    """Parse an ISO-8601 ``since`` value into a tz-aware datetime.

    Mirrors the message_metadata / work_status handling for ISO-8601
    timestamps: accept the ``+00:00`` offset; assume UTC for a naive
    timestamp (the dominant case in this system). Returns ``None``
    for empty / whitespace / unparseable input — the caller's degrade
    contract is "no since filter", not an error.

    Args:
        value: The raw ``since`` query value.

    Returns:
        A tz-aware :class:`datetime`, or ``None`` when the input is
        missing or unparseable.
    """
    if not value or not value.strip():
        return None
    try:
        # ``fromisoformat`` accepts both ``...Z`` (Python 3.11+) and
        # ``...+00:00`` for ISO-8601 timestamps. Normalise ``Z`` to
        # ``+00:00`` for the older 3.10 fallback path.
        normalised = value.strip()
        if normalised.endswith("Z"):
            normalised = normalised[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalised)
    except (TypeError, ValueError):
        logger.debug(
            "list_missions: since=%r unparseable — degrading to no filter",
            value,
        )
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _resolve_paged(
    resolver: "MissionResolver",
    *,
    agent_id: str | None,
    liveness: str | None,
    parent_mission_id: str | None,
    since: str | None,
    limit: int,
) -> tuple[list["MissionRecord"], bool]:
    """Compose the paged mission list — minimal, additive over ``resolve_page``.

    The resolver's :meth:`MissionResolver.resolve_page` is the production
    engine for the HTTP list (M4(i)) and supports ``liveness`` +
    ``agent_id`` filters in SQL. This helper adds the ``parent_mission_id``
    and ``since`` filters in a bounded second pass — a single Python-side
    filter on the page rows (the contract draft's ``limit`` cap is 200,
    so the second pass is bounded; the resolver's batched 3-SELECT page
    is preserved).

    Args:
        resolver: The wired-in ``MissionResolver``.
        agent_id: Optional agent filter (forwarded to ``resolve_page``).
        liveness: Optional single-value liveness filter (forwarded).
        parent_mission_id: Optional parent filter (Python-side pass).
        since: Optional ISO-8601 lower bound on ``last_activity_at``
            (Python-side pass).
        limit: Page size, clamped to ``[1, LIST_MISSIONS_MAX_LIMIT]``.

    Returns:
        A 2-tuple of ``(missions, truncated)``. ``truncated`` is
        ``True`` when the second-pass filter removed rows from the
        page (so the caller can hint at "more results available
        beyond this filtered subset").
    """
    if limit < 1:
        limit = 1
    if limit > LIST_MISSIONS_MAX_LIMIT:
        limit = LIST_MISSIONS_MAX_LIMIT

    liveness_list: list[str] | None = None
    if liveness is not None and liveness.strip():
        # Validate against the canonical vocabulary; an unknown value
        # degrades to the source-less-filter case (honestly-empty
        # page) — same shape as the HTTP surface.
        if liveness.strip().lower() in _LIST_MISSIONS_LIVENESS_VALUES:
            liveness_list = [liveness.strip().lower()]
        else:
            logger.debug(
                "list_missions: unknown liveness=%r — returning empty page",
                liveness,
            )
            return [], False

    page = resolver.resolve_page(
        limit=limit,
        offset=0,
        liveness=liveness_list,
        agent_id=agent_id,
    )

    since_dt = _parse_since(since)

    rows = page.missions
    truncated = False
    if parent_mission_id is not None:
        rows = [r for r in rows if r.parent_mission_id == parent_mission_id]
        truncated = len(rows) < len(page.missions)
    if since_dt is not None:
        # NULL-exclusion contract (M3 fix round, 2026-09-03,
        # ``feature/mission-class``): a row whose
        # ``last_activity_at`` is NULL OR whose ``last_activity_at``
        # is unparseable is excluded from the page. This matches
        # the §8.4 "honest filter" precedent — a NULL activity
        # timestamp means "no recorded activity yet" and must NOT
        # leak into a since-filtered page (an operator querying
        # for activity since T gets rows that ACTUALLY had
        # activity since T, not rows with missing timestamps).
        # Cross-reference: the input ``since`` description above
        # documents the same shape — the two sites MUST stay in
        # sync; this comment is the single source of truth.
        rows = [
            r
            for r in rows
            if r.last_activity_at is not None
            and _parse_iso_for_compare(r.last_activity_at) is not None
            and _parse_iso_for_compare(r.last_activity_at) >= since_dt
        ]
        truncated = truncated or len(rows) < len(page.missions)

    return rows, truncated


def _parse_iso_for_compare(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string into a tz-aware datetime for comparison.

    Companion to :func:`_parse_since` for already-formatted
    ``last_activity_at`` values (which the resolver emits as
    ``datetime.isoformat()`` strings — tz-aware on PG, naive on SQLite,
    so we normalise both to UTC for safe comparison).

    Args:
        value: An ISO-8601 string, or ``None``.

    Returns:
        A tz-aware :class:`datetime` suitable for ``>=`` comparison,
        or ``None`` when unparseable.
    """
    if not value:
        return None
    try:
        normalised = value
        if normalised.endswith("Z"):
            normalised = normalised[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalised)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_mission_terminal(record: "MissionRecord") -> bool:
    """True iff the mission is in a terminal state with a terminal cause.

    Terminal = ``liveness in {completed, failed, cancelled}`` AND
    ``terminal_reason`` is non-null. The compound check guards against
    the "instance is in a degraded state" case where the resolver
    degrades ``liveness=None`` AND ``terminal_reason=None`` —
    that shape is NOT terminal for ``await_mission`` purposes (the
    mission has not reached an outcome; it has lost read access).

    Args:
        record: The resolved :class:`MissionRecord`.

    Returns:
        ``True`` when the mission has reached an outcome; ``False``
        otherwise (live OR degraded).
    """
    if record.liveness is None or record.terminal_reason is None:
        return False
    return record.liveness in {"completed", "failed", "cancelled"}


# ── Factory + tools ───────────────────────────────────────────────────────


def create_mission_tools(
    mission_resolver: "MissionResolver",
) -> list[Any]:
    """Create the three mission tools, wired against a ``MissionResolver``.

    Mirrors the ``create_job_tools`` factory pattern: one injectable
    dependency (the resolver), no instance / manager / project-scoping
    plumbing. The mission surface is intentionally lightweight — the
    resolver is already project-scoped at the call-site
    (it reads from the daemon-wide instances / jobs tables); per-agent
    project gating would belong on top of this surface, not inside
    these tools, and is out of M3 scope.

    Args:
        mission_resolver: The wired-in :class:`MissionResolver`
            instance. The factory holds a reference for the lifetime
            of the tools.

    Returns:
        A list of the three tool callables (``get_mission``,
        ``await_mission``, ``list_missions``) — appended to the
        daemon-wide tool list by ``daemon/manager.py``.
    """

    @register_tool_category("mission")
    @tool
    async def get_mission(
        mission_id: Annotated[
            str,
            Field(
                description=(
                    "The mission id (= instance_id, per M1 identity "
                    "verdict). Resolved through MissionResolver; "
                    "unknown ids return `{\"error\": "
                    "\"mission_not_found\", ...}` rather than raising."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Get a mission snapshot — never blocks, returns immediately.

        Use this when the agent needs to ask "is the work done?" —
        the mission layer's snapshot. For the transport question
        ("was my submission handled?"), use the job tools
        (`job_get`, `job_list`). The two questions are different;
        the wrong-predicate trap is the live pain point the
        mission tools retire.

        The returned `outcome` key is the asymmetric outcome
        token: non-null ONLY when the mission is terminal;
        null when the mission is live. `mission_ref` cross-refs
        on every terminal job payload carry the same liveness.

        Use tool_help("get_mission") for details.
        """
        try:
            record = mission_resolver.resolve(mission_id)
        except Exception as exc:  # noqa: BLE001 — surface every read failure
            logger.warning(
                "get_mission: resolver raised for mission_id=%r: %s",
                mission_id,
                exc,
            )
            return {
                "error": "mission_resolver_error",
                "mission_id": mission_id,
            }

        if record is None:
            return {
                "error": "mission_not_found",
                "mission_id": mission_id,
            }

        return _mission_snapshot_dict(record)
    get_mission._full_doc_ = (
        "Get a mission snapshot (READ-ONLY, never blocks).\n\n"
        "Returns a JSON object with identity, liveness, terminal "
        "cause, epoch summary, linked jobs, timestamps, and the "
        "asymmetric `outcome` token. Use when the agent needs to "
        "ask 'is the work done?' — the mission-layer question. For "
        "the transport-layer question ('was my submission "
        "handled?'), use the job tools.\n\n"
        "Args:\n"
        "    mission_id: The mission id (= instance_id).\n\n"
        "Returns:\n"
        "    dict: A mission snapshot, or "
        "{`error`: `mission_not_found`, `mission_id`: ...} for "
        "unknown ids."
    )

    @register_tool_category("mission")
    @tool
    async def await_mission(
        mission_id: Annotated[
            str,
            Field(
                description=(
                    "The mission id (= instance_id) to await."
                )
            ),
        ],
        timeout: Annotated[
            float,
            Field(
                default=AWAIT_MISSION_DEFAULT_TIMEOUT,
                description=(
                    "Maximum seconds to block. Default 600 "
                    "(10 minutes). On timeout the tool returns the "
                    "current snapshot — NOT an error. Per the "
                    "contract draft §2: timeout = 'return current "
                    "snapshot (no error)'."
                ),
            ),
        ] = AWAIT_MISSION_DEFAULT_TIMEOUT,
        poll_interval: Annotated[
            float,
            Field(
                default=AWAIT_MISSION_DEFAULT_POLL_INTERVAL,
                description=(
                    "Seconds between resolution polls. Default 2. "
                    "Smaller = faster terminal detect at higher "
                    "DB-read cost; larger = cheaper but laggier."
                ),
            ),
        ] = AWAIT_MISSION_DEFAULT_POLL_INTERVAL,
    ) -> dict[str, Any]:
        """Block until the mission reaches a terminal state, or timeout.

        Polls the mission resolver on a fixed interval and returns
        the moment the mission's liveness and terminal_reason both
        indicate completion (F7 semantics: terminal is revivable;
        this await resolves on epoch-terminal — if the mission
        revives later, a fresh await sees the new epoch).

        Use this when the agent is ready to wait for the underlying
        work to finish. For the transport question (was the message
        receipt handled?), `watch_job` is the right primitive — the
        naming asymmetry is intentional.

        Use tool_help("await_mission") for details.
        """
        # Defensive clamp — a zero/negative poll interval would busy-
        # loop and saturate the DB; a zero/negative timeout is
        # equivalent to "give me the snapshot now". Clamp in-tool so
        # an over-eager caller can't degrade the daemon.
        if poll_interval <= 0:
            poll_interval = AWAIT_MISSION_DEFAULT_POLL_INTERVAL
        if timeout < 0:
            timeout = 0

        deadline = asyncio.get_event_loop().time() + timeout

        # First check: not-found is a hard miss (no point polling). The
        # same shape as the contract draft's `not-found → error`
        # contract.
        try:
            first = mission_resolver.resolve(mission_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "await_mission: resolver raised for mission_id=%r: %s",
                mission_id,
                exc,
            )
            return {
                "error": "mission_resolver_error",
                "mission_id": mission_id,
            }
        if first is None:
            return {
                "error": "mission_not_found",
                "mission_id": mission_id,
            }
        if _is_mission_terminal(first):
            return _mission_snapshot_dict(first)

        # Poll loop. We re-resolve on every iteration so a revival
        # bumps the epoch transparently (F7: a revived mission
        # re-enters non-terminal liveness, and the next poll sees
        # `liveness` flip; the previous terminal ``terminal_reason``
        # is overwritten by the resolver to ``None`` for a since-
        # revived mission that is back in a non-terminal state).
        while True:
            now = asyncio.get_event_loop().time()
            remaining = deadline - now
            if remaining <= 0:
                # Timeout — return the current snapshot (NOT an error).
                try:
                    current = mission_resolver.resolve(mission_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "await_mission: timeout-snapshot resolve "
                        "raised for mission_id=%r: %s",
                        mission_id,
                        exc,
                    )
                    return {
                        "error": "mission_resolver_error",
                        "mission_id": mission_id,
                    }
                if current is None:
                    return {
                        "error": "mission_not_found",
                        "mission_id": mission_id,
                    }
                return _mission_snapshot_dict(current)

            sleep_for = min(poll_interval, remaining)
            await asyncio.sleep(sleep_for)

            try:
                current = mission_resolver.resolve(mission_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "await_mission: poll resolve raised for "
                    "mission_id=%r: %s",
                    mission_id,
                    exc,
                )
                # Transient DB error — treat as "still waiting" and
                # retry on the next iteration. The timeout cap is the
                # hard ceiling.
                continue

            if current is None:
                # Mission disappeared between polls (unlikely: only a
                # cascade-delete could remove an Instance row, and the
                # system does not do that today). Surface as not-found.
                return {
                    "error": "mission_not_found",
                    "mission_id": mission_id,
                }
            if _is_mission_terminal(current):
                return _mission_snapshot_dict(current)
    await_mission._full_doc_ = (
        "Block (asyncio poll) until the mission reaches a terminal "
        "state, or timeout.\n\n"
        "Polls MissionResolver on a fixed interval (default 2s) up "
        "to the timeout (default 600s = 10 minutes). On timeout the "
        "tool returns the current snapshot — NOT an error (contract "
        "draft §2). On terminal-resolve the tool returns the "
        "terminal snapshot (the F7 reconciled shape: terminal is "
        "revivable; this await resolves on the current epoch).\n\n"
        "Args:\n"
        "    mission_id: The mission id (= instance_id).\n"
        "    timeout: Maximum seconds to block. Default 600.\n"
        "    poll_interval: Seconds between polls. Default 2.\n\n"
        "Returns:\n"
        "    dict: A mission snapshot (same shape as get_mission) "
        "on terminal or timeout; "
        "{`error`: `mission_not_found`, `mission_id`: ...} for "
        "unknown ids."
    )

    @register_tool_category("mission")
    @tool(args_schema=ListMissionsInput)
    async def list_missions(
        agent_id: str | None = None,
        liveness: str | None = None,
        parent_mission_id: str | None = None,
        since: str | None = None,
        limit: int = LIST_MISSIONS_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List mission summaries, optionally filtered.

        Returns paged mission summaries — `epoch_count` +
        `last_epoch_at` instead of the full `epochs` array (use
        `get_mission` for the full snapshot). Filters compose with
        AND semantics. Use to scope a subtree (parent_mission_id),
        restrict to a single agent (agent_id), focus on a liveness
        cohort (liveness), or only see activity since a timestamp
        (since).

        Use tool_help("list_missions") for details.
        """
        try:
            rows, truncated = _resolve_paged(
                mission_resolver,
                agent_id=agent_id,
                liveness=liveness,
                parent_mission_id=parent_mission_id,
                since=since,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "list_missions: resolver raised for filters=%r: %s",
                {
                    "agent_id": agent_id,
                    "liveness": liveness,
                    "parent_mission_id": parent_mission_id,
                    "since": since,
                    "limit": limit,
                },
                exc,
            )
            return {"error": "mission_resolver_error", "missions": []}

        # Bound the response — clamp the page size the same way the
        # HTTP surface does (so the two surfaces share a single page-
        # shape contract).
        bounded_limit = limit
        if bounded_limit < 1:
            bounded_limit = 1
        if bounded_limit > LIST_MISSIONS_MAX_LIMIT:
            bounded_limit = LIST_MISSIONS_MAX_LIMIT

        return {
            "missions": [_mission_summary_dict(r) for r in rows],
            "limit": bounded_limit,
            # ``truncated`` is the honest "filters may have dropped
            # rows from the page" hint — when ``parent_mission_id`` or
            # ``since`` shrinks the page below the requested limit, a
            # caller can re-issue with a larger limit to confirm there
            # are no further matching rows.
            "truncated": truncated,
        }
    list_missions._full_doc_ = (
        "List mission summaries with optional filters.\n\n"
        "Filters (all optional, AND-composed):\n"
        "    agent_id: Exact match on Instance.agent_id.\n"
        "    liveness: Canonical mission vocabulary (pending / "
        "processing / paused / completed / failed / cancelled). "
        "Unknown values yield an honestly-empty page.\n"
        "    parent_mission_id: Exact match on instances.parent_id "
        "(subtree scoping).\n"
        "    since: ISO-8601 lower bound on last_activity_at. "
        "Unparseable values degrade to no filter.\n"
        "    limit: Page size, clamped to [1, 200]. Default 50.\n\n"
        "Returns:\n"
        "    dict: {`missions`: [...summaries], `limit`: int, "
        "`truncated`: bool}. Each summary carries identity, "
        "liveness, terminal_reason, epoch, epoch_count, "
        "last_epoch_at, linked_jobs, started_at, last_activity_at, "
        "and the asymmetric outcome token — the full epochs array "
        "is omitted (use `get_mission` for that)."
    )

    return [get_mission, await_mission, list_missions]


__all__ = [
    # Public category surface (consumed by ``daemon/tools/_tool_registry``).
    "CATEGORY_NAME",
    "CATEGORY_DOC",
    # Public factory (consumed by ``daemon/manager.py`` tool wiring).
    "create_mission_tools",
    # Public defaults (consumed by tests + adjacent surfaces for
    # boundary-value assertions; documenting them in ``__all__`` makes
    # the public/tuning surface explicit).
    "AWAIT_MISSION_DEFAULT_TIMEOUT",
    "AWAIT_MISSION_DEFAULT_POLL_INTERVAL",
    "LIST_MISSIONS_DEFAULT_LIMIT",
    "LIST_MISSIONS_MAX_LIMIT",
    # Public Pydantic input schema (consumed by the ``list_missions``
    # tool wrapper via ``args_schema``).
    "ListMissionsInput",
    # Module-private helpers (intentionally re-exported for
    # unit-test access; the leading underscore is the
    # "internal-but-importable" convention, NOT a hard-private
    # marker — tests use ``from daemon.tools.missions import
    # _mission_snapshot_dict`` etc.).
    "_mission_snapshot_dict",
    "_mission_summary_dict",
    "_parse_since",
    "_parse_iso_for_compare",
    "_resolve_paged",
    "_is_mission_terminal",
]
