"""Resource traceback — the read-side JOIN layer (resource-cost-control P3).

Answers "why was RAM high at <T>?" for a historical window by joining
the P1/P2 resource signal (``resource_daemon_bucket`` /
``resource_instance_sample`` / ``resource_pid_map``) with the EXISTING
lineage spines AT QUERY TIME. Pure reads — this layer persists nothing
and returns ids / counters / timestamps only (D14: never message
bodies, prompts, tool args, or secret plaintext).

Closed legal-spine set (architecture-recommendation §2.6 — the unit
pack pins both this list and the exclusions below):

=================  ==========================================  ==========================
Spine              Role                                         Anchor index
=================  ==========================================  ==========================
task               in-flight-at-T, owning work/agent,           idx_task_status_created
                   wallclock-bridge source
message_metadata   wallclock bridge — composite-PK point        PK (thread_id, message_id)
                   lookups ONLY; its TEXT-ISO ``created_at``
                   is payload and NEVER a filter column
event              alive-at-T history                           idx_event_instance_created
                   (``kind='instance_lifecycle'``; read via
                   RAW SQL so offsets survive SQLite TEXT)
instances          CURRENT registry snapshot — candidate        PK instance_id
                   set + created-after-T guard + status
                   fallback (status is CURRENT-only: it is
                   sampled at query time and labelled as
                   such, never treated as an at-T truth)
service_tracking   LIVE pid→instance fallback                   idx_service_tracking_pid
resource_*         the resource signal (P1/P2 tables)           (instance_id, bucket_ts) /
                                                                (kind, spawned_at)
=================  ==========================================  ==========================

EXCLUDED BY DESIGN (never a join spine — the unit pack's AST pin
asserts this module never imports or attribute-references them):

* ``message_queue`` — BOOT-WIPED on startup; historical queue state
  does not exist. Only the LIVE depth is meaningful, and that live
  counter is injected by the ROUTER as a provider callable — never
  read here.
* the checkpoint store — ZERO time columns + 168 h TTL
  (``daemon/constants.py`` ``CHECKPOINT_TTL_HOURS``); any
  checkpoint-derived join ages out. Only a LIVE count is meaningful
  and it too arrives as an injected provider callable.

🔴 TZ normalization contract (phase3-plan §TZ — the load-bearing rule):

1. The ONLY normalization primitives are
   ``daemon.services.timestamps.coerce_to_aware_utc`` (assume-UTC
   policy for naive values) and ``to_utc_iso`` (wire), imported
   below. The naive-digit view (``_naive_digits``) is DERIVED from
   ``coerce_to_aware_utc`` — there is no local re-implementation.
2. The request window is normalized ONCE at the entry of
   :func:`build_window_view` into ONE canonical aware-UTC pair. The
   naive-digit bind strings (``task.*``, ``bucket_ts``,
   ``spawned_at`` / ``exited_at`` predicates) and the aware bind
   strings (``event.created_at`` predicates) are both derived from
   that one pair.
3. Every datetime READ from any spine funnels through
   :func:`_read_dt` — which applies ``coerce_to_aware_utc`` — before
   any Python-side comparison or arithmetic, regardless of what the
   driver returns. ``event`` rows are read via RAW SQL precisely so
   SQLite TEXT storage keeps the original ``+07:00``-style offset
   (the ORM DATETIME bind processor would strip it).
4. Every timestamp on the WIRE passes ``timestamps.to_utc_iso``.

Storage-format note (bind boundary): naive spine columns carry
naive-UTC digits — ``task.*`` stores microsecond digits (SQLModel
DATETIME storage), while ``bucket_ts`` / ``spawned_at`` /
``exited_at`` store second-resolution digits (the P1/P2 writers'
``strftime``). Both compare correctly as TEXT on SQLite and as
timestamp literals on PostgreSQL when bound in the
``"%Y-%m-%d %H:%M:%S.%f"`` digit form (a second-resolution stored
value is a strict prefix of the microsecond bind, so ``<=`` / ``>``
predicates keep their exact instant semantics).

Payload-surface note: the WIRE response is the FE-frozen shape
(``frontend/src/app/models/resources.models.ts``
``TracebackPayload``): ``{window, instances_alive, in_flight_tasks,
spawned_children, structural, findings}`` — ``findings`` is
key-reserved for P4 and always ``[]`` here. The deeper per-question
answers (J3 agent hop, J4 bridge, J5 resource rows, J6
``chart_render`` aggregate, source labels) are answered by the
per-question functions below and consumed by P4 through this
module's API; the router's ``response_model`` strips the internal
``_degraded`` key from the wire.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import bindparam, text

from daemon.services.timestamps import coerce_to_aware_utc, to_utc_iso

logger = logging.getLogger(__name__)

# ── Contract constants ──────────────────────────────────────────────────────

#: Max queryable span — mirrors the checkpoint TTL horizon (168 h).
MAX_WINDOW_SECONDS = 168 * 3600

#: Per-list row cap (default / hard cap) — A6 bounded response.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: J5 nearest-sample acceptance window ("tolerance = 1 tick"; the raw
#: tick is 30 s by default). A nearest bucket older than this is an
#: HONEST GAP (``resource: null``), never interpolated.
DEFAULT_SAMPLE_TOLERANCE_S = 30.0

_LIFECYCLE_KIND = "instance_lifecycle"
_NAIVE_FMT = "%Y-%m-%d %H:%M:%S.%f"
#: FALLBACK fetch bound — used ONLY when no candidate carries a
#: parseable registry ``created_at`` (the normal lower bound is the
#: oldest candidate's creation, which reaches a long-lived instance's
#: FULL history; this fixed lookback does not). Residual envelope,
#: stated honestly: for such candidates a latest pre-T event older
#: than this is not fetched and the CURRENT-status fallback decides —
#: and the fallback is CURRENT information, not at-T truth: it can
#: silently EXCLUDE a now-terminal long-lived instance that was alive
#: at T (false negative), or report ALIVE an instance that was already
#: terminal at T and only revived afterwards (false positive). 14 d
#: covers the 168 h max window plus a week of pre-history.
_EVENT_LOOKBACK = timedelta(days=14)
#: Max real-world UTC offset is ±14 h — the aware-string prefilter on
#: ``event.created_at`` is widened by this pad on BOTH sides so
#: mixed-offset rows are always fetched; the exact instant decision is
#: then made in Python on ``coerce_to_aware_utc`` values (rule 3).
#: The pad STRICTLY EXCEEDS the max offset on purpose: an event at
#: exactly the bind instant carried at the extreme ``+14:00`` offset
#: has wall-clock digits EQUAL to a 14 h-padded bind but a
#: lexicographically greater suffix — with a 14 h pad the strict-``<``
#: hi comparison would drop it from the prefilter. 15 h + strict-``<``
#: makes that impossible (any representation of an instant ≤ T has
#: digits ≤ T+14h < T+15h). Unreachable from today's production writer
#: (``datetime.now(timezone.utc)`` is always ``+00:00``); kept as a
#: documented invariant for any future writer.
_TZ_PAD = timedelta(hours=15)
#: Terminal instance statuses — a latest-lifecycle-event or CURRENT
#: snapshot carrying one of these means the instance was NOT alive at T.
_INSTANCE_TERMINAL_STATUSES = frozenset(
    {"completed", "error", "terminated", "failed"}
)
#: Task status partition used to keep BOTH in-flight arms on
#: ``idx_task_status_created`` (the index leads with ``status``; the
#: bare ``created_at <= T AND (completed_at IS NULL OR > T)``
#: predicate alone would fall to a TABLE SCAN). The two arms union to
#: EXACTLY the plan predicate: every known status falls in one arm.
_TASK_LIVE_STATUSES = ("pending", "running", "paused")
_TASK_TERMINAL_STATUSES = ("completed", "failed", "cancelled")
#: The T3 kind vocabulary observed at HEAD — used to fan the no-filter
#: children query into per-kind index seeks. Unknown kinds stored by
#: future writers are still discovered via the DISTINCT pass.
_KNOWN_CHILD_KINDS = ("bash", "chart_render", "mcp_child", "service", "proc")
_STRUCTURAL_KEYS = ("queue_depth", "workers", "checkpoints", "gc_events")
_CHUNK = 400  # IN-clause chunk for the batched J1 event fetch


# ── TZ seam (contract rules 1-4) ────────────────────────────────────────────


def parse_window_param(name: str, value: Any) -> datetime:
    """Parse one ISO-8601 window param into aware-UTC.

    Naive input ⇒ assume-UTC; aware input (``Z`` or offset) ⇒
    ``astimezone(utc)`` — both via :func:`coerce_to_aware_utc` (rule 1;
    Python 3.14 ``fromisoformat`` handles the trailing ``Z`` the FE
    sends). Raises ``ValueError`` — the router maps that to 422.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required (ISO-8601)")
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"{name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    return coerce_to_aware_utc(dt)


def _naive_digits(dt: datetime) -> str:
    """Naive-digit bind view — DERIVED from ``coerce_to_aware_utc``.

    The comparison form for naive spine columns (``task.*``,
    ``bucket_ts``, ``spawned_at`` / ``exited_at``): UTC wall-clock
    digits, microsecond precision.
    """
    aware = coerce_to_aware_utc(dt)
    return aware.replace(tzinfo=None).strftime(_NAIVE_FMT)


def _aware_bind(dt: datetime) -> str:
    """Aware bind view — for ``event.created_at`` (timestamptz on PG).

    Space-separated digits + explicit ``+00:00`` so the literal is
    unambiguous on both dialects.
    """
    aware = coerce_to_aware_utc(dt)
    return (
        aware.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
        + "+00:00"
    )


def _read_dt(value: Any) -> Optional[datetime]:
    """Rule-3 seam — every datetime READ from a spine funnels here.

    ``str`` (SQLite TEXT, offset preserved by raw reads) and
    ``datetime`` (PG driver decode) both land on
    ``coerce_to_aware_utc``; unparseable values degrade to ``None``
    rather than raising mid-join.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return coerce_to_aware_utc(value)
    if isinstance(value, str):
        try:
            return coerce_to_aware_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


# ── J1 — alive-at-T ─────────────────────────────────────────────────────────

_REGISTRY_SQL = text(
    "SELECT instance_id, agent_id, agent_name, status, created_at "
    "FROM instances"
)

# Per-candidate lifecycle fetch — an index seek per instance
# (``idx_event_instance_created`` leads with instance_id). The padded
# aware-string range only NARROWS the candidate set; the exact instant
# decision happens in Python on coerced values (rule 3) because mixed
# TEXT offsets do not compare lexicographically.
_LIFECYCLE_SQL = text(
    "SELECT instance_id, data, created_at FROM event "
    "WHERE instance_id IN :ids AND kind = :kind "
    "AND created_at < :hi AND created_at >= :lo "
    "ORDER BY instance_id, created_at"
).bindparams(bindparam("ids", expanding=True))


def _status_from_event_data(raw: Any) -> Optional[str]:
    """Extract the lifecycle status from an event ``data`` JSON blob.

    Publisher shape (``daemon/services/event_publisher.py``):
    ``{"instance_id", "status", "error", "parent_id"}``. Anything else
    degrades to ``None`` (the caller then falls back to the CURRENT
    snapshot rather than guessing).
    """
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, str) and status:
            return status
    return None


def instances_alive_at(
    engine: Any,
    at: datetime,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """J1 — instances alive at instant ``at`` (aware-UTC).

    Candidates come from the CURRENT ``instances`` registry (rows
    created after ``at`` are excluded via the created-after-T guard),
    so the query set is bounded by the registry, not by event history.
    For each candidate the LATEST ``instance_lifecycle`` event at or
    before ``at`` decides — fetched over the candidate's FULL history
    (the fetch's lower bound is the oldest candidate's own creation, so
    a long-lived instance's at-T evidence is never aged out of the
    fetch range). With no event coverage the CURRENT status snapshot
    decides; that fallback is CURRENT information, not at-T truth (see
    :data:`_EVENT_LOOKBACK` for its honestly-stated failure envelope)
    and is reached only for genuinely event-less candidates — or, when
    the candidate's ``created_at`` is unparseable, for events older
    than the fallback lookback. Both sources are labelled in the
    returned rows (``source``: ``"event"`` / ``"status-snapshot"``) —
    the label is internal (the FE-frozen wire carries instance_id/agent/status
    only) and is what P4's contributor ranking consumes.

    Semantics note: an instance that was alive at ``at`` but
    terminated AFTER ``at`` stays correctly alive here — its terminal
    event is newer than ``at`` and never fetched.
    """
    at = coerce_to_aware_utc(at)
    with engine.connect() as conn:
        reg_rows = conn.execute(_REGISTRY_SQL).fetchall()

    candidates: list[dict] = []
    created_by_iid: dict[str, Optional[datetime]] = {}
    for iid, agent_id, agent_name, status, created_at in reg_rows:
        created = _read_dt(created_at)
        if created is not None and created > at:
            continue  # did not exist at T
        created_by_iid[iid] = created
        candidates.append(
            {
                "instance_id": iid,
                "agent": agent_name or agent_id,
                "status": status,
            }
        )
    if not candidates:
        return []

    # Latest lifecycle event ≤ at, per instance (padded range prefilter
    # + exact Python filter — see _LIFECYCLE_SQL / rule 3). The lower
    # bound is anchored at the OLDEST candidate's own creation — an
    # instance's lifecycle history cannot predate its row — so a
    # long-lived instance's full at-T evidence is always inside the
    # fetch range. (A fixed lookback here silently aged that evidence
    # out: a now-terminal long-lived instance then fell to the
    # CURRENT-status fallback and was wrongly excluded despite being
    # alive at T.) Lifecycle rows are transition-only, so the widened
    # fetch stays small per instance and the (instance_id, created_at)
    # seek is unchanged.
    known_created = [c for c in created_by_iid.values() if c is not None]
    if known_created:
        lo = _aware_bind(min(known_created) - _TZ_PAD)
    else:
        # No parseable creation on ANY candidate — keep the fetch
        # bounded by the fallback lookback (see _EVENT_LOOKBACK for the
        # honestly-stated residual envelope).
        lo = _aware_bind(at - _EVENT_LOOKBACK - _TZ_PAD)
    hi = _aware_bind(at + _TZ_PAD)
    latest: dict[str, tuple[Optional[datetime], Any]] = {}
    ids = [c["instance_id"] for c in candidates]
    with engine.connect() as conn:
        for i in range(0, len(ids), _CHUNK):
            rows = conn.execute(
                _LIFECYCLE_SQL,
                {
                    "ids": ids[i : i + _CHUNK],
                    "kind": _LIFECYCLE_KIND,
                    "hi": hi,
                    "lo": lo,
                },
            ).fetchall()
            for iid, data, created_at in rows:
                ts = _read_dt(created_at)
                if ts is None or ts > at:
                    # Unparseable, or AFTER the instant of interest —
                    # the padded prefilter (±15h for mixed-offset TEXT
                    # safety) deliberately over-fetches; the exact
                    # at-T decision happens here (rule 3).
                    continue
                cur = latest.get(iid)
                if cur is None or (cur[0] is None or ts >= cur[0]):
                    latest[iid] = (ts, data)

    alive: list[dict] = []
    for c in candidates:
        iid = c["instance_id"]
        event = latest.get(iid)
        if event is not None:
            status = _status_from_event_data(event[1])
            if status is None:
                # Lifecycle event without a parseable status — the
                # event's mere existence ≤ T proves the instance
                # existed; keep the CURRENT snapshot's status but stay
                # event-labelled for the source of ALIVENESS.
                status = c["status"]
            if status in _INSTANCE_TERMINAL_STATUSES:
                continue  # already terminal at T → not alive
            alive.append({**c, "status": status, "source": "event"})
        else:
            if c["status"] in _INSTANCE_TERMINAL_STATUSES:
                continue
            alive.append({**c, "source": "status-snapshot"})
    alive.sort(key=lambda r: r["instance_id"])
    return alive[: max(1, limit)]


# ── J2 + J3 + J4 — in-flight-at-T, work/agent enrichment, bridge ────────────

# Two status-partitioned arms of the SAME plan predicate
# (``created_at <= T AND (completed_at IS NULL OR completed_at > T)``)
# so each arm seeks ``idx_task_status_created`` (status equality prefix)
# instead of table-scanning. Merged they are exactly equivalent to the
# single unpartitioned predicate.
_INFLIGHT_SQL = text(
    "SELECT id, work_id, instance_id, message_id, status, created_at "
    "FROM task "
    "WHERE status IN :statuses AND created_at <= :t "
    "AND (completed_at IS NULL OR completed_at > :t) "
    "ORDER BY created_at DESC LIMIT :cap"
).bindparams(bindparam("statuses", expanding=True))

_BRIDGE_SQL = text(
    "SELECT created_at FROM message_metadata "
    "WHERE thread_id = :thread_id AND message_id = :message_id"
)


def in_flight_tasks_at(
    engine: Any,
    at: datetime,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """J2 (+J3/J4 enrichment) — tasks in flight at instant ``at``.

    A task is in flight at ``at`` iff ``created_at <= at`` AND it had
    not completed by ``at`` (``completed_at IS NULL OR > at``) — the
    plan predicate verbatim, evaluated against the CURRENT row (so a
    task that finished after ``at`` still appears: it WAS in flight).
    ``status`` on the row is the task's CURRENT status (the at-T
    status is not recoverable; the current one is the honest label).

    Internal enrichment (stripped from the FE-frozen wire, consumed by
    P4): ``agent_id`` — the J3 one-hop to the P2 sample snapshot
    (``resource_instance_sample.agent_id``, known §2.6 gap #5 hop);
    ``bridge`` — the J4 wallclock bridge
    (``task.message_id`` → ``message_metadata(thread_id=instance_id,
    message_id)`` composite-PK point lookup; a missing metadata row
    degrades to ``bridge: None``, never raises).
    """
    at = coerce_to_aware_utc(at)
    t_bind = _naive_digits(at)
    cap = max(1, limit)
    rows: list[Any] = []
    with engine.connect() as conn:
        for statuses in (_TASK_LIVE_STATUSES, _TASK_TERMINAL_STATUSES):
            rows.extend(
                conn.execute(
                    _INFLIGHT_SQL,
                    {"statuses": list(statuses), "t": t_bind, "cap": cap},
                ).fetchall()
            )

    def _sort_key(r: Any) -> datetime:
        ts = _read_dt(r.created_at)
        return ts or datetime.min.replace(tzinfo=at.tzinfo)

    rows.sort(key=_sort_key, reverse=True)
    rows = rows[:cap]

    # J3 agent hop — one nearest-sample lookup per DISTINCT instance.
    agent_by_instance: dict[str, Optional[str]] = {}
    for r in rows:
        iid = r.instance_id
        if iid in agent_by_instance:
            continue
        try:
            sample = nearest_sample_at(
                engine, iid, at, tolerance_s=None
            )
        except Exception:
            sample = None
        agent_by_instance[iid] = (sample or {}).get("agent_id")

    out: list[dict] = []
    with engine.connect() as conn:
        for r in rows:
            bridge: Optional[str] = None
            if r.message_id and r.instance_id:
                try:
                    brow = conn.execute(
                        _BRIDGE_SQL,
                        {
                            "thread_id": r.instance_id,
                            "message_id": r.message_id,
                        },
                    ).first()
                    # created_at is TEXT-ISO payload — never a filter
                    # column (R4); pass it through verbatim.
                    bridge = brow[0] if brow else None
                except Exception:
                    bridge = None  # missing row / table → honest None
            out.append(
                {
                    "task_id": str(r.id) if r.id is not None else None,
                    "work_id": r.work_id,
                    "instance_id": r.instance_id,
                    "status": r.status,
                    "created_at": _read_dt(r.created_at),
                    "agent_id": agent_by_instance.get(r.instance_id),
                    "bridge": bridge,
                }
            )
    return out


# ── J5 — resource-at-T (nearest sample) ─────────────────────────────────────

_NEAREST_SAMPLE_SQL = text(
    "SELECT bucket_ts, agent_id, status, scan_kind, charged_bytes, "
    "deep_bytes, child_rss_kb, cpu_share_usec_est, in_flight_tasks, "
    "estimated "
    "FROM resource_instance_sample "
    "WHERE instance_id = :iid AND bucket_ts <= :t "
    "ORDER BY bucket_ts DESC LIMIT 1"
)


def nearest_sample_at(
    engine: Any,
    instance_id: str,
    at: datetime,
    tolerance_s: Optional[float] = DEFAULT_SAMPLE_TOLERANCE_S,
) -> Optional[dict]:
    """J5 — the nearest ``resource_instance_sample`` row at/before ``at``.

    ``bucket_ts`` is second-resolution naive-UTC (P1 writer). A nearest
    bucket older than ``tolerance_s`` (the "30 s blur" — one raw tick)
    is an HONEST GAP: returns ``None``, never interpolates. Pass
    ``tolerance_s=None`` to disable the freshness check (the J3 agent
    hop uses that form — it wants the nearest KNOWN agent, however old).
    """
    at = coerce_to_aware_utc(at)
    with engine.connect() as conn:
        row = conn.execute(
            _NEAREST_SAMPLE_SQL,
            {"iid": instance_id, "t": _naive_digits(at)},
        ).first()
    if row is None:
        return None
    sampled_at = _read_dt(row.bucket_ts)
    if sampled_at is None:
        return None
    if tolerance_s is not None:
        if (at - sampled_at) > timedelta(seconds=float(tolerance_s)):
            return None
    return {
        "instance_id": instance_id,
        "sampled_at": sampled_at,
        "agent_id": row.agent_id,
        "status": row.status,
        "scan_kind": row.scan_kind,
        "charged_bytes": row.charged_bytes,
        "deep_bytes": row.deep_bytes,
        "child_rss_kb": row.child_rss_kb,
        "cpu_share_usec_est": row.cpu_share_usec_est,
        "in_flight_tasks": row.in_flight_tasks,
        "estimated": bool(row.estimated),
    }


# ── J6 — children-at-T (+ the chart_render aggregate) ───────────────────────

_CHILDREN_SQL = text(
    "SELECT pid, kind, command, instance_id, spawned_at, exited_at, "
    "peak_rss_kb "
    "FROM resource_pid_map "
    "WHERE kind = :kind AND spawned_at <= :t "
    "AND (exited_at IS NULL OR exited_at > :t) "
    "ORDER BY spawned_at DESC LIMIT :limit"
)

_DISTINCT_KINDS_SQL = text(
    "SELECT DISTINCT kind FROM resource_pid_map WHERE spawned_at <= :t"
)

# The 8.3 GB-class aggregate — ``kind='chart_render'`` children alive
# at T, counted and peak-RSS-summed. Served by the (kind, spawned_at)
# index; surfaced to P4 via :func:`chart_render_aggregate`.
_CHART_AGG_SQL = text(
    "SELECT COUNT(*), SUM(peak_rss_kb) FROM resource_pid_map "
    "WHERE kind = 'chart_render' AND spawned_at <= :t "
    "AND (exited_at IS NULL OR exited_at > :t)"
)


def _child_name(command: Any) -> Optional[str]:
    """Derive the display name from a pid-map ``command``.

    First whitespace token's basename — the command text is the
    W-2-sanitized (redacted) form stored by the P2 stamp lane, so the
    derived name leaks no argv. Empty/unparseable ⇒ ``None``.
    """
    if not command or not isinstance(command, str):
        return None
    first = command.strip().split(" ", 1)[0].strip()
    if not first:
        return None
    return os.path.basename(first) or None


def _child_wire_row(row: Any) -> dict:
    exited_at = _read_dt(row.exited_at)
    return {
        "pid": row.pid,
        "kind": row.kind or "unknown",
        "name": _child_name(row.command),
        "spawned_at": _read_dt(row.spawned_at),
        "exited_at": exited_at,  # None stays None (live child)
        "instance_id": row.instance_id,
    }


def children_at_t(
    engine: Any,
    at: datetime,
    kind: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """J6 — children alive at instant ``at`` (``spawned_at ≤ at`` and
    not exited by ``at``), newest spawns first.

    ``kind`` filters to one kind — ``kind='chart_render'`` is THE
    8.3 GB-class query (render children). Without a filter the query
    fans into per-kind index seeks (the ``(kind, spawned_at)`` index
    leads with ``kind``; a bare ``spawned_at`` range would table-scan)
    over the DISTINCT kinds present at ``at``.
    """
    at = coerce_to_aware_utc(at)
    t_bind = _naive_digits(at)
    cap = max(1, limit)
    rows: list[Any] = []
    with engine.connect() as conn:
        if kind:
            rows = conn.execute(
                _CHILDREN_SQL,
                {"kind": kind, "t": t_bind, "limit": cap},
            ).fetchall()
        else:
            kinds = [
                r[0]
                for r in conn.execute(_DISTINCT_KINDS_SQL, {"t": t_bind})
                .fetchall()
                if r[0]
            ]
            for k in kinds:
                rows.extend(
                    conn.execute(
                        _CHILDREN_SQL,
                        {"kind": k, "t": t_bind, "limit": cap},
                    ).fetchall()
                )
            rows.sort(
                key=lambda r: _read_dt(r.spawned_at)
                or datetime.min.replace(tzinfo=at.tzinfo),
                reverse=True,
            )
            rows = rows[:cap]
    return [_child_wire_row(r) for r in rows]


def chart_render_aggregate(engine: Any, at: datetime) -> dict:
    """J6 aggregate — count + total ``peak_rss_kb`` of chart_render
    children alive at ``at`` (the 8.3 GB-class signal P4 ranks on)."""
    at = coerce_to_aware_utc(at)
    t_bind = _naive_digits(at)
    with engine.connect() as conn:
        count, total = conn.execute(_CHART_AGG_SQL, {"t": t_bind}).first()
    return {
        "kind": "chart_render",
        "count": int(count or 0),
        "total_peak_rss_kb": int(total) if total is not None else 0,
    }


# ── J7 — historical pid→instance ────────────────────────────────────────────

#: ``LIMIT 50`` — a heavily-recycled pid's 50 NEWEST eras suffice:
#: the start_time match (or the newest-row preference when no era
#: matches) never needs deeper history, and the bound keeps a
#: pathological pid's row count off the read path.
_PID_MAP_SQL = text(
    "SELECT pid, start_time, instance_id, agent_id, spawned_at, exited_at "
    "FROM resource_pid_map WHERE pid = :pid ORDER BY spawned_at DESC LIMIT 50"
)

_SERVICE_TRACKING_SQL = text(
    "SELECT started_by_instance_id, start_time, name FROM service_tracking "
    "WHERE pid = :pid AND (:st IS NULL OR start_time = :st) "
    "ORDER BY id DESC LIMIT 1"
)


def resolve_pid_instance(
    engine: Any,
    pid: int,
    start_time: Optional[int] = None,
) -> Optional[dict]:
    """J7 — resolve a pid to its owning instance.

    Historical first: ``resource_pid_map`` rows for the pid, preferring
    the row whose kernel ``start_time`` (field-22 jiffies) matches the
    target — the pid-recycle guard (two rows, same pid, different
    start_time ⇒ the correct-era row wins). Without a map row, the LIVE
    ``service_tracking`` fallback (``idx_service_tracking_pid``) — with
    the SAME ``start_time`` guard applied: a recycled pid must not be
    answered by a tracking row from a different era, so a caller-
    supplied ``start_time`` matching no tracking row is an honest
    ``None`` rather than a wrong-era guess. ``source`` labels which
    spine answered; ``None`` when neither does.
    """
    with engine.connect() as conn:
        rows = conn.execute(_PID_MAP_SQL, {"pid": pid}).fetchall()
        picked = None
        for r in rows:
            if start_time is not None and r.start_time == start_time:
                picked = r
                break
        if picked is None and rows:
            picked = rows[0]
        if picked is not None:
            return {
                "pid": picked.pid,
                "instance_id": picked.instance_id,
                "agent_id": picked.agent_id,
                "start_time": picked.start_time,
                "spawned_at": _read_dt(picked.spawned_at),
                "exited_at": _read_dt(picked.exited_at),
                "source": "resource_pid_map",
            }
        srow = conn.execute(
            _SERVICE_TRACKING_SQL, {"pid": pid, "st": start_time}
        ).first()
        if srow is not None:
            return {
                "pid": pid,
                "instance_id": srow.started_by_instance_id,
                "agent_id": None,
                "start_time": srow.start_time,
                "spawned_at": None,
                "exited_at": None,
                "source": "service_tracking",
            }
    return None


# ── Structural block (LIVE at query time — injected providers) ──────────────


def collect_structural(
    providers: Optional[Mapping[str, Callable[[], Any]]] = None,
) -> dict:
    """Live structural counters — best-effort, NEVER raises.

    The providers are injected by the router (they read LIVE daemon
    state: the boot-wiped queue's current depth, the worker pools'
    started-worker counts, a checkpoint-row count, the gc counters).
    This module deliberately does NOT import any of those stores — the
    excluded-spine pin holds. Any provider failure (or absence)
    degrades that counter to ``None``.
    """
    out: dict[str, Optional[int]] = {k: None for k in _STRUCTURAL_KEYS}
    if not providers:
        return out
    for key in _STRUCTURAL_KEYS:
        fn = providers.get(key)
        if fn is None:
            continue
        try:
            value = fn()
        except Exception as exc:
            logger.warning(
                "[resource_traceback] structural %s unavailable: %s", key, exc
            )
            continue
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return out


# ── Window view assembly ────────────────────────────────────────────────────


def build_window_view(
    engine: Any,
    t0: Any,
    t1: Any,
    *,
    kind: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    sample_tolerance_s: float = DEFAULT_SAMPLE_TOLERANCE_S,
    structural_providers: Optional[Mapping[str, Callable[[], Any]]] = None,
) -> dict:
    """Assemble the §2.8 traceback window view for ``[t0, t1]``.

    Normalizes the window ONCE (rule 2) and evaluates every question
    at the window END (``t1`` — the drill-down instant). Section
    failures degrade to empty lists + an internal ``_degraded`` reason
    list (the router surfaces it as a response header + WARN log) —
    this function raises ONLY for caller input errors (unparseable
    params, ``t1 < t0``, span > 168 h), which the router maps to 422.
    The wire dict carries exactly the FE-frozen keys plus the
    underscore-prefixed internal ``_degraded`` (stripped by the
    router's ``response_model``).
    """
    t0_aware = parse_window_param("t0", t0)
    t1_aware = parse_window_param("t1", t1)
    if t1_aware < t0_aware:
        raise ValueError("t1 must not precede t0")
    if (t1_aware - t0_aware) > timedelta(seconds=MAX_WINDOW_SECONDS):
        raise ValueError(
            f"window span exceeds {MAX_WINDOW_SECONDS // 3600}h"
        )
    try:
        limit = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    degraded: list[str] = []
    alive: list[dict] = []
    in_flight: list[dict] = []
    children: list[dict] = []

    if engine is None:
        degraded.append("engine-unavailable")
    else:
        for label, fn, target in (
            ("instances_alive", instances_alive_at, alive),
            ("in_flight_tasks", in_flight_tasks_at, in_flight),
            ("spawned_children", None, children),
        ):
            try:
                if label == "spawned_children":
                    target.extend(
                        children_at_t(engine, t1_aware, kind=kind, limit=limit)
                    )
                else:
                    target.extend(fn(engine, t1_aware, limit))
            except Exception as exc:
                degraded.append(f"{label}: {exc}")
                logger.warning(
                    "[resource_traceback] %s degraded: %s", label, exc
                )

    structural = collect_structural(structural_providers)

    return {
        "window": {
            "t0": to_utc_iso(t0_aware),
            "t1": to_utc_iso(t1_aware),
        },
        "instances_alive": [
            {
                "instance_id": r["instance_id"],
                "agent": r.get("agent"),
                "status": r.get("status"),
            }
            for r in alive
        ],
        "in_flight_tasks": [
            {
                "task_id": r["task_id"],
                "work_id": r["work_id"],
                "instance_id": r["instance_id"],
                "status": r["status"],
                "created_at": to_utc_iso(r["created_at"]),
            }
            for r in in_flight
        ],
        "spawned_children": [
            {
                "pid": r["pid"],
                "kind": r["kind"],
                "name": r["name"],
                "spawned_at": to_utc_iso(r["spawned_at"]),
                "exited_at": to_utc_iso(r["exited_at"]),
                "instance_id": r["instance_id"],
            }
            for r in children
        ],
        "structural": structural,
        "findings": [],  # key reserved for P4 — contract stability
        "_degraded": degraded,
    }
