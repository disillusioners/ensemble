"""The ``metrics-read`` privileged tool category (3 read-only tools).

Three tools
-----------
``metrics_families`` — the family catalog (what metrics exist, their shape).
``metrics_snapshot``  — the CURRENT published values.
``metrics_history``   — the windowed trend, with a decision-ready delta.

ACCESS POSTURE — TWO LAYERS, BOTH REQUIRED
-------------------------------------------
1. The ``metrics-read`` category is PRIVILEGED, so it never joins the
   empty-allow default-grant universe.
2. A privileged category alone is NOT sufficient — a **literal tool name**
   in an allow list bypasses the category gate entirely.  So every tool
   body ALSO calls the one shared module-level helper
   :func:`_is_metrics_authorized`.

Both gates are required; neither substitutes for the other.  The
allow-list is ``{maintenancer, devops, wanderer}``.

THE TOOLS STAY PRESENT ON A DEGRADED BOOT (RULING-1)
-----------------------------------------------------
The factory gates on MODULE + manager-handle existence only; the tool
BODY reads ``manager.metrics_query_provider`` PER CALL and returns the
typed ``metrics-unavailable`` refusal when the mount is ``None``.  A
degraded boot therefore leaves the tools PRESENT and loudly refusing
(the ``notify_user`` kill-switch shape) rather than making the surface
VANISH SILENTLY — for a diagnosis surface, silent absence is the worst
failure mode (the diagnosing agent does not know what it is missing).

TOOLS NEVER RAISE
-----------------
Every failure returns a typed refusal string (the house envelope).  The
four-token ladder mirrors the door's status semantics IN-PROCESS:
``unknown-agent`` · ``not-authorized`` · ``metrics-unavailable`` ·
``invalid_label_filter``.  Authorization fires FIRST — an unauthorized
caller must never learn whether the substrate is mounted.

THE READS HAVE NO SIDE EFFECT
-----------------------------
A read writes NO audit row, deletes NOTHING, and triggers NOTHING.  There
is no act half in this category: it is structurally read-only.

ONE PROVIDER, ONE RING, ONE TRUTH
---------------------------------
The tools read ``manager.metrics_query_provider`` — the SAME provider
instance (and the SAME in-RAM trend ring) the HTTP query door serves
from.  The tool NEVER constructs a provider, never scrapes the REST door
and never reads the database directly.

THE FROZEN CONTRACT IS FROZEN
------------------------------
The tool layer ADAPTS to the frozen wire contract, it never changes it:
no wire shape, route, error-code, availability-reason or invariant is
edited by this lane.  ``schema_version`` stays 1 and ``vocabulary_version``
is untouched — the tools ECHO both and never gate on them.

THE AVAILABILITY BLOCK IS CARRIED VERBATIM
------------------------------------------
The provider's ``availability`` block is carried UNCHANGED — never
re-derived, never re-ordered, never substituted.  The tool only ADDS
non-substitutive annotations (``data_present`` / ``dark_with_live_series``)
so the agent never reads "dark" as "no data".

A BOUND THAT DROPS SOMETHING IS NEVER SILENT
---------------------------------------------
Every result carries the SAME six-member bound block
(``matched_count`` / ``returned_count`` / ``dropped_count`` /
``more_available`` / ``truncated`` / ``narrow_hint``), so one agent
reading rule covers all three tools.  When a bound bites, the result says
so: ``truncated`` is true, ``dropped_count`` is reported and a
``narrow_hint`` names a CONCRETE next filter.

NO CURSOR, EVER (v1)
---------------------
No tool accepts a cursor and no tool returns one.  Every call
re-anchors itself (``now=instant``), the comparable-window leg is an
internal re-anchor (``now=instant - window_s``), and the continuation
mechanism is "narrow the selector", not "resume a page".

THE COST OF A CALL (BINDING)
-----------------------------
A default ``metrics_history`` call returns ~1 320 points (~33 KB, ~9k
tokens) of agent context, and the compare leg doubles the PROVIDER work
(it returns only scalars, so it is provider work, not context tokens).
A 7-call patrol is therefore materially budget-relevant: prefer
``metrics_families`` (one call) plus ONE targeted
``metrics_snapshot`` (a multi-family snapshot drops a patrol from 7
round trips to 3).

NON-GOALS (BINDING)
--------------------
There is NO anomaly-first ordering and NO cross-family top-N: the only
ordering is deterministic (ascending by family, name, label key) and the
anomaly signal is ``metrics_history``'s ``delta_change``.

READ-BROAD / ALERT-MAY-BE-NARROW
--------------------------------
The READ surface is granted to three non-ari agents; the future ALERT
surface may end up ari-only.  A later lane that narrows the ALERT side
must not read this breadth as an accident.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any, Sequence

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from daemon.services.metrics_query.constants import (
    MAX_FAMILIES,
    MAX_NAMES,
    MAX_TOTAL_POINTS,
    RESPONSE_BYTE_CEILING,
    STALE_INTERVAL_MULTIPLIER,
    WINDOW_SECONDS,
)
from daemon.services.metrics_query.envelope import (
    activation_block,
    effective_interval_s,
    read_numeric_fact,
)
from daemon.services.metrics_query.selectors import (
    InvalidLabelFilter,
    parse_series_selector,
)

from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

#: The category key — KEBAB-case, byte-identical in every place it
#: appears (the module, the registry, the three ``meta.json`` allow
#: entries, the three ``tools_note.md`` sections and the pin packs).
METRICS_CATEGORY_KEY = "metrics-read"

CATEGORY_NAME = "Metrics"

CATEGORY_DOC = """\
Read-only views over the daemon's own metric registry — the SAME
substrate the HTTP query door serves from (one provider, one ring, one
truth).

Tools in this category:
- ``metrics_families`` — the family catalog (what metrics exist, their shape)
- ``metrics_snapshot``  — the CURRENT published values of the matching series
- ``metrics_history``   — the windowed trend of the matching series, with a
  comparable-window delta

These tools READ. They never write, never delete, and never trigger a
metric collection.

DARK IS NOT EMPTY
-----------------
``availability.state='dark'`` is a hint about the SOURCE, not an absence
of data: a family can report dark AND publish live series.  When that
happens the result carries a caveat saying so — the series below are
real.  A FAILED registry read is likewise not "no data": the
``read_status`` block reports ``registry_read_ok=false`` and the empty
payload must be read as UNKNOWN, not empty.

The caveat is GENERIC on purpose: it asserts only the fact the tool can
verify at runtime (dark AND live series) and never names a cause — a
shipped string that names one goes stale the moment the owning lane
lands.

``metrics_snapshot`` returns CURRENT published metric values. It is NOT
the Agent Snapshot artifact — that is ``snapshot_create`` /
``snapshot_search`` in the ``snapshot`` category.

NAMING MAP
----------
The HTTP door's third route is ``GET /api/metrics/series``; the provider
method it calls is ``history()``.  The tool is named after the PROVIDER
METHOD (``metrics_history``); its ``series`` argument is the
label-aware selector list.

THE COST OF A CALL
------------------
A default ``metrics_history`` call returns ~1 320 points (~33 KB, ~9k
tokens) of agent context; the compare leg doubles the PROVIDER work (it
returns only scalars, so it is provider work, not context tokens).  A
7-call patrol is materially budget-relevant: prefer ``metrics_families``
(one call) plus ONE targeted ``metrics_snapshot`` — a multi-family
snapshot drops a patrol from 7 round trips to 3.

NON-GOALS
---------
There is NO anomaly-first ordering and NO cross-family top-N: the only
ordering is deterministic (ascending by family, name, label key) and the
anomaly signal is ``metrics_history``'s ``delta_change``.

READ-BROAD / ALERT-MAY-BE-NARROW
-------------------------------
The READ surface is granted to three non-ari agents; the future ALERT
surface may end up ari-only.  A later lane that narrows the ALERT side
must not read this breadth as an accident.
"""

#: The agents allowed to read the metrics registry.  The privileged
#: category gates VISIBILITY; this set gates AUTHORITY.  Both are
#: required.
METRICS_AUTHORIZED_AGENTS: frozenset[str] = frozenset(
    {"maintenancer", "devops", "wanderer"}
)

#: The three tool names this module constructs — the canonical list the
#: registry's ``KNOWN_TOOL_NAMES`` / ``DYNAMIC_TOOL_NAMES`` and the
#: registration pin pack agree with.
TOOL_NAMES: frozenset[str] = frozenset(
    {
        "metrics_families",
        "metrics_snapshot",
        "metrics_history",
    }
)

# ── the four refusal tokens (the in-process mirror of the door's status
#    semantics: 401 / 403 / 503 / 400) ────────────────────────────────────
TOKEN_UNKNOWN_AGENT = "unknown-agent"
TOKEN_NOT_AUTHORIZED = "not-authorized"
TOKEN_UNAVAILABLE = "metrics-unavailable"
TOKEN_INVALID_LABEL_FILTER = "invalid_label_filter"

# ── the tool's own caps (the SEAM constants are IMPORTED, never
#    duplicated — a duplicated literal silently diverges from the seam) ──
DEFAULT_TOP_N = 20
MAX_TOP_N = 50
DEFAULT_WINDOW_S = 900
MIN_WINDOW_S = 60
MAX_WINDOW_S = WINDOW_SECONDS
DEFAULT_MAX_POINTS = 120
MIN_MAX_POINTS = 2
MAX_MAX_POINTS = 240

#: The read-status doctrine (AmC-4) — the racy-bool semantics, stated.
READ_STATUS_DOCTRINE = (
    "registry_read_ok is ONE mutable flag flipped by every reader; it is "
    "this call's observed value at read time, not a per-reader guarantee."
)


def _is_metrics_authorized(agent_id: str) -> bool:
    """Is this agent allowed to read the metrics registry?

    ONE shared helper, called by ALL THREE tool bodies — the set lives in
    exactly one place and the bodies share it (the ``wedge_capture`` /
    ``instance_read`` precedent).

    Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER authorized.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in METRICS_AUTHORIZED_AGENTS


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


# ── the shared projection helpers ─────────────────────────────────────────


def _encoded_size(payload: Any) -> int:
    """The encoded byte size of a payload (the byte-ceiling probe)."""
    try:
        return len(
            json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        )
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return 0


def _trim_to_byte_ceiling(
    payload: dict[str, Any], *, key: str, items: list[Any]
) -> tuple[list[Any], bool]:
    """Trim ``items`` until the encoded body fits the byte ceiling.

    The door's pattern (``routers/metrics.py``) — the trim is a BOUND, and
    the caller MUST surface it (never a silent truncation).
    """
    low, high, best = 0, len(items), 0
    while low <= high:
        mid = (low + high) // 2
        if _encoded_size({**payload, key: items[:mid]}) <= RESPONSE_BYTE_CEILING:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return items[:best], best < len(items)


def _read_status(provider: Any) -> dict[str, Any]:
    """The ``read_status`` block — the DEGRADED-READ facts of THIS call.

    The activation facts are read PER CALL from the shipped
    ``activation_block`` builder (never re-derived, never cached): the
    provider's ``registry_read_ok`` is one mutable flag that every reader
    flips, so this block reports THIS call's observed value (AmC-4).
    """
    activation = activation_block(provider)
    ok = bool(activation.get("registry_read_ok", True))
    ring_empty = bool(read_numeric_fact(provider, "last_sample_age_s", 0.0) <= 0.0)
    return {
        "registry_read_ok": ok,
        "mode": "ok" if ok else "degraded",
        "note": (
            None
            if ok
            else (
                "The metrics registry read FAILED — the payload below is "
                "INCOMPLETE or UNKNOWN, not 'no data'. Treat every count in "
                "this result as unreliable."
            )
        ),
        "trend_empty_since_restart": ring_empty,
        "doctrine": READ_STATUS_DOCTRINE,
    }


def _header(provider: Any) -> dict[str, Any]:
    """The echoed envelope header + the activation block (VERBATIM)."""
    from daemon.services.metrics_query.envelope import envelope_header

    header = envelope_header(provider)
    return {
        "schema_version": header["schema_version"],
        "vocabulary_version": header["vocabulary_version"],
        "generated_at_utc": header["generated_at_utc"],
        "restart_epoch": header["restart_epoch"],
        "activation": header["activation"],
    }


def _bound_block(
    *,
    matched_count: int | None,
    returned_count: int,
    dropped_count: int | None,
    more_available: bool,
    narrow_hint: str | None,
) -> dict[str, Any]:
    """The SIX-member bound block — identical in all three tools."""
    truncated = bool(more_available) or (
        dropped_count is not None and dropped_count > 0
    )
    return {
        "matched_count": matched_count,
        "returned_count": returned_count,
        "dropped_count": dropped_count,
        "more_available": bool(more_available),
        "truncated": truncated,
        "narrow_hint": narrow_hint,
    }


def _dark_with_live_caveat(families: list[dict[str, Any]]) -> str | None:
    """The canonical dark-with-live caveat — GENERIC by design.

    It asserts only the runtime-verifiable fact (dark AND live series) and
    NEVER attributes a cause (a shipped string naming a defect would go
    stale the moment the owning lane lands).
    """
    dark = [f for f in families if f.get("dark_with_live_series")]
    if not dark:
        return None
    reasons = sorted({str((f.get("availability") or {}).get("reason") or "unknown") for f in dark})
    total = sum(int(f.get("live_series_count") or 0) for f in dark)
    noun = "family" if len(dark) == 1 else "families"
    verb = "reports" if len(dark) == 1 else "report"
    return (
        f"{len(dark)} {noun} {verb} availability.state='dark' "
        f"(reason={', '.join(reasons)}) while publishing live series "
        f"(live_series_count={total}). 'dark' is a hint about the SOURCE, "
        "not an absence of data — the series below are real."
    )


def _degraded_caveat() -> str:
    """The degraded-read caveat (a failed read is NOT an empty result)."""
    return (
        "The metrics registry read FAILED — this payload is INCOMPLETE or "
        "UNKNOWN, not 'no data'. Treat every count in this result as "
        "unreliable."
    )


def _known_family_ids(catalog: list[dict[str, Any]]) -> list[str]:
    """The catalog's family ids, sorted (the narrow-hint vocabulary)."""
    return sorted(str(entry.get("id")) for entry in catalog if entry.get("id"))


def _catalog_index(
    catalog: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    """The catalog's ``name -> family`` and ``name -> cardinality_class``."""
    family_of: dict[str, str] = {}
    cardinality_of: dict[str, str] = {}
    for entry in catalog:
        family = str(entry.get("id") or "")
        for name_entry in entry.get("names") or []:
            name = str(name_entry.get("name") or "")
            if not name:
                continue
            family_of[name] = family
            cardinality_of[name] = str(name_entry.get("cardinality_class") or "none")
    return family_of, cardinality_of


def _sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """The deterministic ascending order: (family, name, label key)."""
    labels = row.get("labels") or {}
    label_key = ";".join(f"{k}={labels[k]}" for k in sorted(labels))
    return (str(row.get("__family__") or ""), str(row.get("name") or ""), label_key)


def _strip_internal(row: dict[str, Any]) -> dict[str, Any]:
    """The tool-internal ``__family__`` marker is never emitted."""
    return {k: v for k, v in row.items() if not k.startswith("__")}


# ── the three body builders (run OFF the event loop) ─────────────────────


def _build_families(
    provider: Any,
    *,
    family: str | None,
    details: bool,
    only_with_data: bool,
) -> dict[str, Any]:
    """The ``metrics_families`` body — built OFF the event loop."""
    catalog = list(provider.families())
    known = _known_family_ids(catalog)

    selected: list[dict[str, Any]] = []
    unknown = False
    if family:
        selected = [e for e in catalog if str(e.get("id")) == family]
        unknown = not selected
    else:
        selected = list(catalog)
    if only_with_data:
        selected = [e for e in selected if int(e.get("live_series_count") or 0) > 0]

    entries: list[dict[str, Any]] = []
    dark_candidates: list[dict[str, Any]] = []
    for entry in selected:
        names_out: list[dict[str, Any]] = []
        for name_entry in entry.get("names") or []:
            projected = {
                "name": name_entry.get("name"),
                "kind": name_entry.get("kind"),
                "cardinality_class": name_entry.get("cardinality_class"),
            }
            if details:
                projected.update(
                    {
                        "labels": name_entry.get("labels"),
                        "monotonic": name_entry.get("monotonic"),
                        "chart_role": name_entry.get("chart_role"),
                        "buckets": name_entry.get("buckets"),
                        "help": name_entry.get("help"),
                    }
                )
            names_out.append(projected)
        live = int(entry.get("live_series_count") or 0)
        availability = entry.get("availability") or {}
        derived = {
            "id": entry.get("id"),
            "role": entry.get("role"),
            "source_idiom": entry.get("source_idiom"),
            "durability": entry.get("durability"),
            "live_series_count": live,
            "cardinality_contribution": entry.get("cardinality_contribution"),
            "availability": availability,
            "names": names_out,
            "name_count": len(names_out),
            "data_present": live > 0,
            "dark_with_live_series": (
                str(availability.get("state") or "") == "dark" and live > 0
            ),
        }
        entries.append(derived)
        dark_candidates.append(derived)

    matched = len(entries)
    kept = entries[:MAX_FAMILIES]
    dropped = matched - len(kept)
    narrow_hint: str | None = None
    if unknown:
        narrow_hint = (
            f"no family with id {family!r}; known ids: {', '.join(known)}"
        )
    elif dropped:
        narrow_hint = (
            f"matched_count={matched} families; {len(kept)} returned. "
            "Narrow with `family=…` — known ids: " + ", ".join(known)
        )

    payload: dict[str, Any] = {
        "tool": "metrics_families",
        "read_status": _read_status(provider),
        **_header(provider),
        "query": {
            "family": family,
            "details": bool(details),
            "only_with_data": bool(only_with_data),
        },
        **_bound_block(
            matched_count=matched,
            returned_count=len(kept),
            dropped_count=dropped,
            more_available=False,
            narrow_hint=narrow_hint,
        ),
        "caveats": [],
        "families": kept,
    }
    caveat = _dark_with_live_caveat(dark_candidates)
    if caveat:
        payload["caveats"].append(caveat)
    if not payload["read_status"]["registry_read_ok"]:
        payload["caveats"].append(_degraded_caveat())
    return payload


def _build_snapshot(
    provider: Any,
    *,
    family: str | None,
    families: Sequence[str] | None,
    name_prefix: str | None,
    top_n: int,
) -> dict[str, Any]:
    """The ``metrics_snapshot`` body — built OFF the event loop."""
    catalog = list(provider.families())
    known = _known_family_ids(catalog)
    family_of, cardinality_of = _catalog_index(catalog)

    requested_families = [str(f) for f in (families or []) if str(f)]
    unknown = False
    if family and str(family) not in known:
        unknown = True
    unknown_family = family if unknown else None
    if requested_families:
        unknown_requested = [f for f in requested_families if f not in known]
        if unknown_requested:
            unknown = True
            unknown_family = unknown_requested[0]

    # ONE snapshot call — the multi-family list is filtered TOOL-SIDE.
    rows = list(provider.snapshot(family=family, name_prefix=name_prefix))

    projected: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "")
        row_family = family_of.get(name, "")
        if requested_families and row_family not in requested_families:
            continue
        out = dict(row)
        out["__family__"] = row_family
        out["cardinality_class"] = cardinality_of.get(name, "none")
        projected.append(out)

    interval = effective_interval_s(provider)
    for out in projected:
        age = float(out.get("last_publish_age_s") or 0.0)
        out["stale"] = age > STALE_INTERVAL_MULTIPLIER * interval

    # SORT BEFORE CUT — the provider returns registration order, so the
    # ascending promise is enforced TOOL-side and ``dropped_count`` is
    # meaningful.
    projected.sort(key=_sort_key)

    matched = len(projected)
    kept = projected[: max(1, int(top_n))]
    dropped = matched - len(kept)
    narrow_hint: str | None = None
    if unknown:
        narrow_hint = (
            f"no family with id {unknown_family!r}; known ids: {', '.join(known)}"
        )
    elif dropped:
        narrow_hint = (
            f"matched_count={matched} live series; {len(kept)} returned. "
            "Narrow with `family=…` (known ids: "
            + ", ".join(known)
            + ") or `name_prefix=…`, or raise `top_n` (max "
            f"{MAX_TOP_N})."
        )

    payload: dict[str, Any] = {
        "tool": "metrics_snapshot",
        "read_status": _read_status(provider),
        **_header(provider),
        "query": {
            "family": family,
            "families": requested_families or None,
            "name_prefix": name_prefix,
            "top_n": int(top_n),
        },
        "effective_interval_s": interval,
        **_bound_block(
            matched_count=matched,
            returned_count=len(kept),
            dropped_count=dropped,
            more_available=False,
            narrow_hint=narrow_hint,
        ),
        "caveats": [],
        "series": [_strip_internal(row) for row in kept],
    }
    if not payload["read_status"]["registry_read_ok"]:
        payload["caveats"].append(_degraded_caveat())
    return payload


def _series_delta(points: list[Any]) -> tuple[float | None, str | None]:
    """``(delta, delta_reason)`` — never a fabricated zero.

    A window with fewer than two points yields ``None`` +
    ``"insufficient_points"``: a zero delta would assert "nothing
    changed", which is a different claim from "we cannot tell".
    """
    if len(points) < 2:
        return None, "insufficient_points"
    try:
        first = float(points[0][1])
        last = float(points[-1][1])
    except (IndexError, TypeError, ValueError):  # pragma: no cover — defensive
        return None, "insufficient_points"
    return last - first, None


def _build_history(
    provider: Any,
    *,
    family: str | None,
    series: Sequence[str] | None,
    names: Sequence[str] | None,
    end_offset_s: int,
    window_s: int,
    max_points: int,
    top_n: int,
    compare: bool,
) -> dict[str, Any]:
    """The ``metrics_history`` body — built OFF the event loop.

    Raises :class:`InvalidLabelFilter` for the malformed/over-ceiling
    selector conditions — the caller maps them to the typed refusal.
    """
    requested_names = [str(n) for n in (names or []) if str(n)]
    requested_series = [str(s) for s in (series or []) if str(s)]

    # THE SELECTORS ARE PARSED BY THE TOOL — the malformed-selector
    # refusal is DETERMINISTIC and fires before ANY provider read (the
    # door parses them itself for the same reason).
    for selector in requested_series:
        parse_series_selector(selector)

    # THE HARD FLOOR — no selector at all is NEVER an unbounded match.
    if not family and not requested_names and not requested_series:
        known = _known_family_ids(list(provider.families()))
        raise InvalidLabelFilter(
            "metrics_history needs a selector: pass `series=` "
            "(label-aware, '<name>{k=v}'), `names=` (name-level) or "
            "`family=`. Known family ids: " + ", ".join(known)
        )

    catalog = list(provider.families())
    known = _known_family_ids(catalog)
    family_of, cardinality_of = _catalog_index(catalog)

    unknown_family = None
    if family and str(family) not in known:
        unknown_family = str(family)

    resolved_names = list(requested_names)
    if family and not resolved_names and not requested_series:
        resolved_names = sorted(
            name for name, fam in family_of.items() if fam == str(family)
        )
        if len(resolved_names) > MAX_NAMES:
            raise InvalidLabelFilter(
                f"family {family!r} declares {len(resolved_names)} metric "
                f"names — more than the {MAX_NAMES}-selector ceiling. Pass "
                "`series=` label-aware selectors (e.g. "
                "'<name>{k=v}') for the exact series you want."
            )

    # THE FLOOR, RE-CHECKED AFTER THE FAMILY RESOLUTION: an UNKNOWN
    # family resolves to no names, and an unbounded match is never
    # allowed.
    if not resolved_names and not requested_series:
        raise InvalidLabelFilter(
            "metrics_history needs a selector: pass `series=` "
            "(label-aware, '<name>{k=v}'), `names=` (name-level) or a known "
            "`family=`. Known family ids: " + ", ".join(known)
        )

    # THE COMBINED SELECTOR CAP (the router's formula, mirrored exactly):
    # the provider caps `names` only, and the tool bypasses the router.
    if len(set(resolved_names)) + len(requested_series) > MAX_NAMES:
        raise InvalidLabelFilter(
            f"at most {MAX_NAMES} selectors may be requested; got "
            f"{len(set(resolved_names)) + len(requested_series)}"
        )

    top_n = max(1, min(int(top_n), MAX_TOP_N))
    window = max(MIN_WINDOW_S, min(int(window_s), MAX_WINDOW_S))
    # The END of the window may be shifted back; the anchor stays RELATIVE
    # (the no-cursor doctrine is preserved).
    offset = max(0, min(int(end_offset_s), max(0, WINDOW_SECONDS - window)))
    # THE BUDGET CLAMP (AmM-1) — the returned leg can never exceed the
    # seam's own total-point ceiling.
    effective_max_points = min(
        max(MIN_MAX_POINTS, min(int(max_points), MAX_MAX_POINTS)),
        max(MIN_MAX_POINTS, MAX_TOTAL_POINTS // (top_n + 1)),
    )
    clamped = effective_max_points != int(max_points)

    instant = int(time.time()) - offset
    current = list(
        provider.history(
            names=resolved_names or None,
            series=requested_series or None,
            window_s=window,
            max_points=effective_max_points,
            now=instant,
            max_series=top_n + 1,
        )
    )
    more_available = len(current) > top_n
    kept = current[:top_n]

    previous: list[dict[str, Any]] = []
    if compare:
        previous = list(
            provider.history(
                names=resolved_names or None,
                series=requested_series or None,
                window_s=window,
                max_points=effective_max_points,
                now=instant - window,
                max_series=top_n + 1,
            )
        )
    previous_by_key = {
        (str(row.get("name") or ""), _label_key(row.get("labels") or {})): row
        for row in previous
    }

    interval = effective_interval_s(provider)
    ring_age = float(read_numeric_fact(provider, "last_sample_age_s", 0.0))

    series_out: list[dict[str, Any]] = []
    for row in kept:
        name = str(row.get("name") or "")
        points = list(row.get("points") or [])
        delta, reason = _series_delta(points)
        prev_row = previous_by_key.get((name, _label_key(row.get("labels") or {})))
        prev_points = list((prev_row or {}).get("points") or [])
        prev_delta, prev_reason = _series_delta(prev_points)
        delta_change: float | None = None
        if delta is not None and prev_delta is not None:
            delta_change = delta - prev_delta
        out = dict(row)
        out["cardinality_class"] = cardinality_of.get(name, "none")
        out["delta"] = delta
        out["prev_delta"] = prev_delta
        out["delta_change"] = delta_change
        out["delta_reason"] = reason
        out["prev_delta_reason"] = prev_reason if compare else None
        out["downsampled"] = len(points) > 0 and len(points) < max(
            1, int(window // max(interval, 1.0))
        )
        out["last_sample_age_s"] = _series_age(points, instant)
        out["points_span_s"] = _points_span(points)
        series_out.append(out)

    narrow_hint: str | None = None
    if unknown_family:
        narrow_hint = (
            f"no family with id {unknown_family!r}; known ids: "
            + ", ".join(known)
        )
    elif more_available:
        narrow_hint = (
            f"returned_count={len(kept)} of more than {len(kept)} series "
            f"matched (top_n={top_n}). Narrow with `family=…` or a label "
            "filter such as `series='<name>{k=v}'`, or raise `top_n` (max "
            f"{MAX_TOP_N}). For the exact matched count call "
            "`metrics_snapshot` with the same filter."
        )

    payload: dict[str, Any] = {
        "tool": "metrics_history",
        "read_status": _read_status(provider),
        **_header(provider),
        "query": {
            "family": family,
            "series": requested_series or None,
            "names": resolved_names or None,
            "window_s": window,
            "max_points": effective_max_points,
            "max_points_requested": int(max_points),
            "top_n": top_n,
            "compare": bool(compare),
            "end_offset_s": offset,
        },
        "window": {
            "from_utc": _iso(instant - window),
            "to_utc": _iso(instant),
            "interval_s": interval,
            "interval_s_source": "observed",
        },
        "last_sample_age_s": ring_age,
        "stale": ring_age > STALE_INTERVAL_MULTIPLIER * interval,
        "effective_interval_s": interval,
        **_bound_block(
            matched_count=None,
            returned_count=len(kept),
            dropped_count=None,
            more_available=more_available,
            narrow_hint=narrow_hint,
        ),
        "caveats": [],
        "series": series_out,
    }
    if compare:
        payload["compare_window"] = {
            "from_utc": _iso(instant - 2 * window),
            "to_utc": _iso(instant - window),
        }
    if clamped:
        # NON-SILENCE — a clamp that reduces the caller's requested bound
        # is ALWAYS surfaced (the effective bound is echoed above).
        payload["caveats"].append(
            "max_points was clamped from "
            f"{int(max_points)} to {effective_max_points} so the returned "
            "leg stays within the daemon's total-point budget."
        )
    if not payload["read_status"]["registry_read_ok"]:
        payload["caveats"].append(_degraded_caveat())
    return payload


def _label_key(labels: dict[str, Any]) -> str:
    """The canonical label-key serialization (the discriminator)."""
    return ";".join(f"{k}={labels[k]}" for k in sorted(labels))


def _series_age(points: list[Any], instant: int) -> float:
    """The age of THIS series' newest point against the anchor."""
    if not points:
        return 0.0
    try:
        newest = float(points[-1][0])
    except (IndexError, TypeError, ValueError):  # pragma: no cover — defensive
        return 0.0
    return max(0.0, float(instant) - newest)


def _points_span(points: list[Any]) -> float:
    """The span this series' points cover (0.0 when there are none)."""
    if len(points) < 2:
        return 0.0
    try:
        return max(0.0, float(points[-1][0]) - float(points[0][0]))
    except (IndexError, TypeError, ValueError):  # pragma: no cover — defensive
        return 0.0


def _iso(epoch_s: int) -> str:
    """An aware-UTC ISO-8601 stamp (the seam's own producer shape)."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(epoch_s), tz=timezone.utc).isoformat()


def _provider_or_refuse(manager: Any, label: str) -> tuple[Any, str | None]:
    """The PER-CALL provider resolution + the substrate refusal.

    The mount is read at CALL time (RULING-1): a degraded boot leaves the
    tools present and refusing loudly, never absent.
    """
    provider = getattr(manager, "metrics_query_provider", None)
    if provider is None:
        return None, _refusal(
            label,
            TOKEN_UNAVAILABLE,
            "the metrics query provider is not mounted on the manager "
            "(the metrics stack is boot-degraded)",
        )
    return provider, None


async def _offload(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """The off-loop body build (the ``/api/work`` precedent).

    The body build is the expensive part (the provider read + the
    projection), so it runs on the dedicated ``db-offload`` executor and
    never on the event loop (D-UX-16 / AmRULING-4).
    """
    from daemon.services.db_offload import offload as _offload

    return await _offload(fn, *args, **kwargs)


def create_metrics_read_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the three ``metrics-read`` tools for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — the tool reads
            ``manager.metrics_query_provider`` (the ONE mounted provider,
            the same one the HTTP query door serves from).
        current_instance_id: The caller's instance id (unused by the
            reads; kept for factory-signature parity).
        agent_id: The caller's agent id. Captured at factory time and
            checked by :func:`_is_metrics_authorized` in every tool body.
        agent_tag: The caller's version tag (unused; signature parity).

    Returns:
        A list of the three ``@tool``-decorated callables.
    """
    caller_agent_id = agent_id or ""

    class MetricsFamiliesInput(BaseModel):
        """Input schema for metrics_families."""

        family: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Exact family id to read (e.g. 'F9' for readiness, 'F1' "
                    "for event-loop lag, 'F10' for the job queue, 'F6' for "
                    "the PG census). Omit for the whole catalog."
                ),
            ),
        ]
        details: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "When true, each metric name also carries its declared "
                    "label space, buckets, monotonicity, chart_role and "
                    "help text. Default false = names + kind + "
                    "cardinality_class only (the catalog stays small)."
                ),
            ),
        ]
        only_with_data: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "When true, keep only families that currently publish at "
                    "least one live series (live_series_count > 0). Note "
                    "this is NOT the same as availability.state == 'live' — "
                    "a family can report dark and still be publishing (see "
                    "dark_with_live_series)."
                ),
            ),
        ]

    @register_tool_category(METRICS_CATEGORY_KEY)
    @tool(args_schema=MetricsFamiliesInput)
    async def metrics_families(
        family: str | None = None,
        details: bool = False,
        only_with_data: bool = False,
    ) -> str:
        """The metrics family catalog — what metrics exist and their shape. Use tool_help("metrics_families") for details."""
        if not _is_metrics_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "metrics_families",
                    TOKEN_UNKNOWN_AGENT,
                    "the metrics registry is readable by maintenancer, "
                    "devops and wanderer only",
                )
            return _refusal(
                "metrics_families",
                TOKEN_NOT_AUTHORIZED,
                "the metrics registry is readable by maintenancer, devops "
                "and wanderer only",
            )
        provider, refusal = _provider_or_refuse(manager, "metrics_families")
        if refusal is not None:
            return refusal
        try:
            payload = await _offload(
                _build_families,
                provider,
                family=family,
                details=bool(details),
                only_with_data=bool(only_with_data),
            )
            payload["query"] = {
                **payload["query"],
                "agent_id": caller_agent_id,
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning("metrics_families failed: %s", exc)
            return _error(f"metrics_families failed: {exc}")

    class MetricsSnapshotInput(BaseModel):
        """Input schema for metrics_snapshot."""

        family: Annotated[
            str | None,
            Field(
                default=None,
                description=("Exact family id (e.g. 'F10'). Omit for all families."),
            ),
        ]
        families: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Several family ids at once (e.g. ['F10','F13']). The "
                    "rows are filtered tool-side over ONE provider read, so "
                    "a multi-family snapshot costs one round trip instead of "
                    "one per family."
                ),
            ),
        ]
        name_prefix: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Metric-name prefix filter (e.g. 'ensemble_loop_lag_' → "
                    "every loop-lag metric). An EXACT metric name is a valid "
                    "prefix (a name is a prefix of itself)."
                ),
            ),
        ]
        top_n: Annotated[
            int,
            Field(
                default=DEFAULT_TOP_N,
                description=(
                    f"Max series rows to return (1-{MAX_TOP_N}, default "
                    f"{DEFAULT_TOP_N}). Order is ascending by (family, name, "
                    "label key) — deterministic, so the same query always "
                    "returns the same rows."
                ),
            ),
        ]

    @register_tool_category(METRICS_CATEGORY_KEY)
    @tool(args_schema=MetricsSnapshotInput)
    async def metrics_snapshot(
        family: str | None = None,
        families: list[str] | None = None,
        name_prefix: str | None = None,
        top_n: int = DEFAULT_TOP_N,
    ) -> str:
        """The CURRENT published metric values. Use tool_help("metrics_snapshot") for details."""
        if not _is_metrics_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "metrics_snapshot",
                    TOKEN_UNKNOWN_AGENT,
                    "the metrics registry is readable by maintenancer, "
                    "devops and wanderer only",
                )
            return _refusal(
                "metrics_snapshot",
                TOKEN_NOT_AUTHORIZED,
                "the metrics registry is readable by maintenancer, devops "
                "and wanderer only",
            )
        provider, refusal = _provider_or_refuse(manager, "metrics_snapshot")
        if refusal is not None:
            return refusal
        try:
            payload = await _offload(
                _build_snapshot,
                provider,
                family=family,
                families=families,
                name_prefix=name_prefix,
                top_n=max(1, min(int(top_n), MAX_TOP_N)),
            )
            payload["query"] = {
                **payload["query"],
                "agent_id": caller_agent_id,
            }
            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning("metrics_snapshot failed: %s", exc)
            return _error(f"metrics_snapshot failed: {exc}")

    class MetricsHistoryInput(BaseModel):
        """Input schema for metrics_history."""

        family: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Exact family id; the tool resolves it to that family's "
                    "metric names from the catalog (a convenience over the "
                    "provider's name-level selector)."
                ),
            ),
        ]
        series: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Label-aware series selectors, the contract's canonical "
                    "form: '<name>{k=v,k2=v2}' — e.g. "
                    "'ensemble_loop_lag_seconds{stat=p95}'. Comma-separated "
                    "k=v pairs. A bare '<name>' matches every label variant. "
                    f"Max {MAX_NAMES} selectors in total."
                ),
            ),
        ]
        names: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Name-level selectors (every label variant of the named "
                    f"metrics). Max {MAX_NAMES} selectors in total. Prefer "
                    "'series' when you know the label values."
                ),
            ),
        ]
        end_offset_s: Annotated[
            int,
            Field(
                default=0,
                description=(
                    "Shift the window END back from now (seconds). The "
                    "anchor stays RELATIVE (no cursor): the window is always "
                    "'the last N seconds as of now minus this offset'."
                ),
            ),
        ]
        window_s: Annotated[
            int,
            Field(
                default=DEFAULT_WINDOW_S,
                description=(
                    f"Window length in seconds ({MIN_WINDOW_S}-{MAX_WINDOW_S}, "
                    f"default {DEFAULT_WINDOW_S} = 15 min). The ring holds 2 h; "
                    "longer windows are clamped."
                ),
            ),
        ]
        max_points: Annotated[
            int,
            Field(
                default=DEFAULT_MAX_POINTS,
                description=(
                    f"Max points per series ({MIN_MAX_POINTS}-{MAX_MAX_POINTS}, "
                    f"default {DEFAULT_MAX_POINTS}). Server-side bucket-max "
                    "downsample — peaks are preserved, never every-kth. The "
                    "daemon clamps this further so the returned leg stays "
                    "within its own total-point budget; the effective value "
                    "is echoed back."
                ),
            ),
        ]
        top_n: Annotated[
            int,
            Field(
                default=10,
                description=(
                    f"Max series to return (1-{MAX_TOP_N}, default 10). The "
                    "provider is asked for top_n+1 internally so the tool can "
                    "detect that more exist."
                ),
            ),
        ]
        compare: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    "When true (default), also read the immediately preceding "
                    "equal-length window and report delta vs that comparable "
                    "window. Set false to halve the work."
                ),
            ),
        ]

    @register_tool_category(METRICS_CATEGORY_KEY)
    @tool(args_schema=MetricsHistoryInput)
    async def metrics_history(
        family: str | None = None,
        series: list[str] | None = None,
        names: list[str] | None = None,
        end_offset_s: int = 0,
        window_s: int = DEFAULT_WINDOW_S,
        max_points: int = DEFAULT_MAX_POINTS,
        top_n: int = 10,
        compare: bool = True,
    ) -> str:
        """The windowed metric TREND, with a comparable-window delta. Use tool_help("metrics_history") for details."""
        if not _is_metrics_authorized(caller_agent_id):
            if not isinstance(caller_agent_id, str) or not caller_agent_id:
                return _refusal(
                    "metrics_history",
                    TOKEN_UNKNOWN_AGENT,
                    "the metrics registry is readable by maintenancer, "
                    "devops and wanderer only",
                )
            return _refusal(
                "metrics_history",
                TOKEN_NOT_AUTHORIZED,
                "the metrics registry is readable by maintenancer, devops "
                "and wanderer only",
            )
        provider, refusal = _provider_or_refuse(manager, "metrics_history")
        if refusal is not None:
            return refusal
        try:
            payload = await _offload(
                _build_history,
                provider,
                family=family,
                series=series,
                names=names,
                end_offset_s=int(end_offset_s),
                window_s=int(window_s),
                max_points=int(max_points),
                top_n=int(top_n),
                compare=bool(compare),
            )
            payload["query"] = {
                **payload["query"],
                "agent_id": caller_agent_id,
            }
            return json.dumps(payload, indent=2, default=str)
        except InvalidLabelFilter as exc:
            return _refusal("metrics_history", TOKEN_INVALID_LABEL_FILTER, str(exc))
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.warning("metrics_history failed: %s", exc)
            return _error(f"metrics_history failed: {exc}")

    # ── the tool_help surface (the ``_full_doc_`` contract) ──────────────
    # The full docs are keyed by the tool name, so the attribute must be
    # set on the tool objects themselves (the house pattern — the
    # ``instance_read`` / ``instance_ops`` precedent).
    metrics_families._full_doc_ = """The metrics family catalog — what metrics exist and their shape.

    Answers: "which metric families does the daemon publish, what is in
    each, and is it live?"

    Args:
      family: Exact family id (e.g. 'F1' event-loop lag, 'F10' job queue).
        Omit for the whole catalog.
      details: When true, each metric name also carries its DECLARED
        label space, buckets, monotonicity, chart_role and help text.
        Default false = names + kind + cardinality_class only.
      only_with_data: Keep only families that publish at least one live
        series (live_series_count > 0). NOT the same as
        availability.state == 'live' — a family can report dark AND
        publish (see dark_with_live_series).

    Reads: the family catalog only — no points, no series values.

    Dark is NOT empty: a family can report availability.state='dark' and
    still publish live series. When that happens the result carries a
    caveat saying so — the series below are real. A FAILED registry read
    is likewise not "no data": the read_status block reports
    registry_read_ok=false and the payload must be read as UNKNOWN.

    An unknown family id is NOT an error: the result is empty and
    narrow_hint names the known ids.
    """

    metrics_snapshot._full_doc_ = """The CURRENT published metric values.

    Answers: "what does the metric read RIGHT NOW?"

    Args:
      family: Exact family id (e.g. 'F10'). Omit for all families.
      families: Several family ids at once (e.g. ['F10','F13']) — the
        rows are filtered tool-side over ONE provider read, so a
        multi-family snapshot costs one round trip instead of one per
        family.
      name_prefix: Metric-name prefix filter (e.g. 'ensemble_loop_lag_'
        matches every loop-lag metric; an exact name is a valid prefix).
      top_n: Max series rows (1-50, default 20). The order is ascending
        by (family, name, label key) — deterministic, so the same query
        always returns the same rows.

    NOT the Agent Snapshot artifact — that is snapshot_create /
    snapshot_search in the 'snapshot' category.

    Each row carries the frozen fields (name, labels, kind, monotonic,
    value/histogram, last_publish_age_s) plus the derived `stale` (the
    3x-interval rule) and `cardinality_class` (so a closed name's
    declared space is distinguishable from an open name's observed
    labels).

    A bound that drops rows is never silent: dropped_count is reported
    and narrow_hint names a concrete next filter.
    """

    metrics_history._full_doc_ = """The windowed metric TREND, with a comparable-window delta.

    Answers: "is it getting worse, and by how much?"

    Args:
      family: Exact family id; resolved to that family's metric names
        from the catalog (a convenience over the name-level selector).
      series: Label-aware selectors, the contract's canonical form:
        '<name>{k=v,k2=v2}' — e.g.
        'ensemble_loop_lag_seconds{stat=p95}'. A bare '<name>' matches
        every label variant.
      names: Name-level selectors (every label variant of the named
        metrics). Prefer 'series' when you know the label values.
      end_offset_s: Shift the window END back from now (seconds). The
        anchor stays RELATIVE (no cursor): the window is always "the
        last N seconds as of now minus this offset".
      window_s: Window length (60-7200, default 900 = 15 min). The ring
        holds 2 h; longer windows are clamped.
      max_points: Max points per series (2-240, default 120). The
        daemon clamps this further so the returned leg stays within its
        own total-point budget; the effective value is echoed back and
        the clamp is named in a caveat.
      top_n: Max series (1-50, default 10). The provider is asked for
        top_n+1 internally so the tool can detect that more exist.
      compare: When true (default), also read the immediately preceding
        equal-length window and report the delta vs that comparable
        window. Set false to halve the work.

    Per series: delta (last - first of THIS window), prev_delta (the
    same over the preceding window), delta_change (delta - prev_delta)
    and downsampled. A window with fewer than two points yields null +
    delta_reason 'insufficient_points' — NEVER 0 (a zero delta would
    assert "nothing changed", which is a different claim).

    NO CURSOR: every call re-anchors itself, and the continuation
    mechanism is "narrow the selector" (narrow_hint), not "resume a
    page".
    """

    return [metrics_families, metrics_snapshot, metrics_history]
