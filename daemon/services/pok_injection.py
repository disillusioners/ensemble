"""POK per-view injection admission (Phase 1 T6 — ER-1 / §4.5.1).

The AUDIENCE-DIMENSIONED admission pipeline behind
``build_pok_context_message``. Everything here operates PER VIEW —
an agent's view is selected from ITS visible entry set, and the
8-entry / 2,900-char budget never sums across audiences (the
audience filter PRECEDES admission, so the project-wide worst case
bounds every view without re-derivation — §4.5 bounded arithmetic).

Pipeline (order is load-bearing):

1. **AUDIENCE FILTER FIRST** (ER-1.2): visible set = ACTIVE rows
   whose effective audience includes the viewer's factory-captured
   ``agent_id``. Defaults (design-time constants in code, plan §8):
   operation → {ari, leader, devops}; implementation →
   {leader, developer}; both → every agent on the project. A
   curator-set ``audience_agents`` REPLACES the default set when
   present.
2. **Exclusions within the visible set** (F3 extended): disputed
   rows are excluded-for-view (suppress injection, stay readable);
   an audience-INVISIBLE member is an excluded row FOR THAT VIEW.
3. **Pinned tier first** (≤2 pins, PER PROJECT): a pinned member
   carries its whole visible family adjacent in slug order at the
   pinned position; a doubly-pinned family is counted ONCE on rows
   AND chars; a pinned-member exclusion CANCELS the drag.
4. **Ranked actives**: the remaining visible rows in selector-fusion
   rank (B5 reuse — zero POK-invented weights; the turn-1 user
   query drives the fusion, the ari seam passes the empty query and
   the selector's floor supplies the deterministic baseline), with
   the 30-day decay rank-down (``[stale Nd]`` — ranks down, never
   drops).
5. **F5 rider**: stop where rendered chars would exceed
   ``POK_BLOCK_MAX_CHARS`` (2,900, FULL block wrapper-inclusive) or
   entries would exceed ``POK_BLOCK_MAX_ENTRIES`` (8); the tail
   counts the remainder. Families atomic throughout — a family is
   admitted whole (visible members contiguous in slug order) or not
   at all; zero visible survivors → the family does not render in
   that view.

NEVER-RAISE contract (§4.9): every public entry returns a result
object; all store failures degrade to "no block" + WARNING (the
``:5083`` swallow family) — the injection path must never break the
turn. The once-per-instance flag is NEVER WRITTEN here —
``assemble_context_messages`` BUILDS ONLY; the consumer
(``instance_messaging.py``) writes it after the persistent half is
CONFIRMED to carry the block (AM-22 discipline, memoir precedent).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from daemon.repositories.pok.models import (
    POK_FAMILY_SLUG_PATTERN,
    ProjectOperatingKnowledge,
)

logger = logging.getLogger(__name__)

# ── §4.7 sizing constants (imported from the service module's single
#    source; re-exported here so the render arithmetic measures the
#    same bytes the tools enforce) ──────────────────────────────────────
from daemon.services.pok_service import (  # noqa: E402
    POK_BLOCK_MAX_CHARS,
    POK_BLOCK_MAX_ENTRIES,
    POK_ENTRY_RENDER_MAX_CHARS,
    POK_FAMILY_MAX,
    POK_PIN_CAP_PER_PROJECT,
    POK_VERIFY_WINDOW_DAYS,
)

#: The once-per-instance metadata key. Written by the CONSUMER
#: (``instance_messaging.py``, the AM-22 discipline), read fresh by
#: the seam every assembly (revive-safe). The builder NEVER writes it.
POK_INJECTION_FLAG = "pok_injected"

#: The block title — ``_make_context_message`` renders the canonical
#: ``[SYSTEM CONTEXT: <title>]`` wrapper around it (47 chars:
#: CONTEXT_PREFIX 17 + title 27 + CONTEXT_SUFFIX 3 — §4.5 wrapper).
POK_BLOCK_TITLE = "Project Operating Knowledge"

#: The fixed list-item template (the content fence): the ONLY variable
#: slots are slug/domain/audience/stale-mark/escaped-body — the body
#: is escaped + newline-flattened before insertion, so no structural
#: token survives (A-29 render side). Overhead per entry (template
#: without body) = 2 + 44 + 3 + 11 + 1 + 14 + 10 + 2 ≤ 87 ≤ 92
#: (the §4.5 per-entry overhead bound: 332 ceiling = 240 escaped body
#: + 92 overhead).
POK_ENTRY_TEMPLATE = "- {slug} [{domain}|{audience}{stale}]: {body}"

#: The tail line (counts the remainder — F5 rider).
POK_TAIL_TEMPLATE = (
    "({n} more entr{'y' if n == 1 else 'ies'} not shown — use pok_query "
    "for the full view)"
)

#: The header line rendered inside the block content.
POK_HEADER_LINE = f"## {POK_BLOCK_TITLE}"

#: Slug-family regex (single-sourced from the models module).
POK_FAMILY_RE = re.compile(POK_FAMILY_SLUG_PATTERN)

#: Default audiences for ACTIVE injection (§4.5.1 — design-time
#: constants in code; ER-1 ruling: leader INCLUDED in operation —
#: it runs the ops lanes).
DEFAULT_AUDIENCE_SETS: dict[str, frozenset[str]] = {
    "operation": frozenset({"ari", "leader", "devops"}),
    "implementation": frozenset({"leader", "developer"}),
    # "both" is universal — modeled as None (no set membership test).
}


def _slug_family(slug: str) -> str | None:
    m = POK_FAMILY_RE.match(slug)
    return m.group(1) if m else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def stale_days(row: ProjectOperatingKnowledge, *, now: datetime | None = None) -> int | None:
    """Days since ``last_verified`` past the 30-day window (AC5), or
    ``None`` when the row is fresh / never verified (an
    unverified-but-new row is NOT stale — only an old verification
    stamp decays)."""
    verified = _parse_iso(row.last_verified)
    if verified is None:
        return None
    now_dt = now or datetime.now(timezone.utc)
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    days = (now_dt - verified).days
    if days > POK_VERIFY_WINDOW_DAYS:
        return days
    return None


def effective_audience_agents(row: ProjectOperatingKnowledge) -> set[str] | None:
    """The effective audience set for ACTIVE injection.

    Returns ``None`` for the universal case (``both`` without a
    curator override — every agent on the project sees the entry);
    otherwise the set of agent ids (a curator ``audience_agents``
    override REPLACES the default set when present).
    """
    if row.audience_agents:
        try:
            parsed = json.loads(row.audience_agents)
            if isinstance(parsed, list):
                return {str(a) for a in parsed if str(a).strip()}
        except (ValueError, TypeError):
            logger.warning(
                "[POK:Degraded] malformed audience_agents on %s — "
                "falling back to the default set",
                row.slug,
            )
    if row.audience == "both":
        return None
    return set(DEFAULT_AUDIENCE_SETS.get(row.audience, set()))


def row_is_visible(row: ProjectOperatingKnowledge, agent_id: str | None) -> bool:
    """The audience filter for ONE row (ER-1.2). ``agent_id`` is the
    FACTORY-CAPTURED id — mapping by agent_id is the proven in-body
    pattern."""
    effective = effective_audience_agents(row)
    if effective is None:
        return True
    return (agent_id or "") in effective


# ── result shapes ─────────────────────────────────────────────────────────


@dataclass
class StaleMark:
    """AC5: the ``[stale Nd]`` decoration — ranks down, never drops."""

    days: int

    def render(self) -> str:
        return f" [stale {self.days}d]"


@dataclass
class ViewEntry:
    """One admitted row plus its per-view decorations."""

    row: ProjectOperatingKnowledge
    stale: StaleMark | None = None
    tier: str = "ranked"  # "pinned" | "ranked"

    def render(self) -> str:
        # Content fence (A-29 render side): flatten newlines FIRST,
        # then escape_for_context_block (lazy import — cycle-neutral:
        # context_messages imports THIS module for the builder). The
        # fixed template carries no slot a structural token survives.
        from daemon.services.context_messages import (  # noqa: PLC0415
            escape_for_context_block,
        )

        body = (self.row.body or "").replace("\r", " ").replace("\n", " ").strip()
        body = escape_for_context_block(body)
        mark = self.stale.render() if self.stale is not None else ""
        return POK_ENTRY_TEMPLATE.format(
            slug=self.row.slug,
            domain=self.row.domain,
            audience=self.row.audience,
            stale=mark,
            body=body,
        )


@dataclass
class AdmissionResult:
    """The per-view admission outcome (render-ready, ordered).

    ``entries`` is the FINAL ordered list (pinned tier first, then
    ranked actives — families contiguous in slug order within each
    tier). ``omitted`` counts the tail remainder. ``agent_id`` is the
    view key (stable id ``pok:{project_id}`` is minted by the
    builder, per project — the audience dimension is per-view state,
    not per-block identity).
    """

    entries: list[ViewEntry] = field(default_factory=list)
    omitted: int = 0
    visible_total: int = 0
    agent_id: str | None = None
    degraded: bool = False


def admit_view(
    rows: list[ProjectOperatingKnowledge],
    *,
    agent_id: str | None,
    query: str = "",
    query_embedding: list[float] | None = None,
    rank_fn: Any = None,
    now: datetime | None = None,
    project_id: str | None = None,
    embeddings_map: dict[str, list[float]] | None = None,
) -> AdmissionResult:
    """THE per-view admission (audience filter FIRST — ER-1.2).

    ``rank_fn`` is the B5 selector hook: ``callable(rows, query,
    query_embedding, embeddings_map) -> ordered rows``. The caller
    (the context seam / the ari seam) supplies the
    ``POKService._rank_with_selector``-shaped function; ``None``
    degrades to slug order (the deterministic fallback — the pin
    tier and F5 arithmetic are unaffected).

    NEVER RAISES (§4.9): any failure degrades to an empty result
    with ``degraded=True`` (no block this turn; the once-flag stays
    unwritten and the next turn retries).
    """
    result = AdmissionResult(agent_id=agent_id)
    try:
        # 0. ACTIVE only (F3 status exclusions — the invariant floor).
        active = [r for r in rows if r.status == "active"]
        # 1. AUDIENCE FILTER FIRST (ER-1.2).
        visible = [r for r in active if row_is_visible(r, agent_id)]
        result.visible_total = len(visible)
        # 2. Disputed excluded-for-view (suppress injection; readable
        #    via pok_query — ER-1.4 untouched). The visible candidate
        #    set drives EVERY tier below — excluded rows never render.
        candidates = [r for r in visible if not r.disputed]

        # Family partition over the candidates (visible survivors only;
        # slug order within each family — the atomic, contiguous block).
        families: dict[str, list[ProjectOperatingKnowledge]] = {}
        for row in candidates:
            fam = _slug_family(row.slug)
            if fam is not None:
                families.setdefault(fam, []).append(row)
        for fam_rows in families.values():
            fam_rows.sort(key=lambda r: r.slug)

        def _mark(row: ProjectOperatingKnowledge) -> StaleMark | None:
            d = stale_days(row, now=now)
            return StaleMark(days=d) if d is not None else None

        def _unit_cost(members: list[ProjectOperatingKnowledge]) -> tuple[int, int]:
            """(rows, chars) for one unit — entry renders + newlines."""
            chars = 0
            for m in members:
                ve = ViewEntry(row=m, stale=_mark(m))
                chars += len(ve.render()) + 1
            return len(members), chars

        # Reserve the header + the eventual tail line inside the
        # wrapper-inclusive budget (the builder measures the FULL
        # block; this reservation is the admission-side mirror).
        used_rows = 0
        used_chars = len(POK_HEADER_LINE) + 1 + len(
            "(999 more entries not shown — use pok_query for the full view)"
        )

        def _fits(n_rows: int, n_chars: int) -> bool:
            return (
                used_rows + n_rows <= POK_BLOCK_MAX_ENTRIES
                and used_chars + n_chars <= POK_BLOCK_MAX_CHARS
            )

        def _admit(members: list[ProjectOperatingKnowledge], tier: str) -> None:
            nonlocal used_rows, used_chars
            for m in members:
                admitted.append(
                    ViewEntry(row=m, stale=_mark(m), tier=tier)
                )
                used_rows += 1
            _, c = _unit_cost(members)
            used_chars += c

        admitted: list[ViewEntry] = []
        consumed_ids: set[str] = set()
        consumed_fams: set[str] = set()

        # 3. PINNED TIER FIRST (≤2 pins per project — enforced at
        #    write/curate; capped defensively here). Pinned order:
        #    slug. A pinned member DRAGS its whole visible family as a
        #    contiguous block at the pinned position; a doubly-pinned
        #    family is consumed ONCE; a pinned-member exclusion never
        #    reaches `candidates`, so the drag carries visible
        #    survivors only — and if the pinned member itself is
        #    excluded, no drag happens (exclusion cancels the drag).
        pinned_rows = sorted(
            (r for r in candidates if r.pinned), key=lambda r: r.slug
        )[:POK_PIN_CAP_PER_PROJECT]
        pinned_family_ids: set[str] = set()
        for prow in pinned_rows:
            fam = _slug_family(prow.slug)
            if fam is not None:
                if fam in pinned_family_ids:
                    continue  # doubly-pinned family — counted once
                block = families.get(fam, [prow])
                pinned_family_ids.add(fam)
            else:
                block = [prow]
            n, c = _unit_cost(block)
            if not _fits(n, c):
                continue
            _admit(block, tier="pinned")
            consumed_ids.update(m.id for m in block)
            if fam is not None:
                consumed_fams.add(fam)

        # 4. RANKED ACTIVES: non-pinned candidates in selector-fusion
        #    rank; the decay ranks down WITHIN the ranked list (stable
        #    partition, order preserved within each side — AC5).
        ranked_pool = [r for r in candidates if r.id not in consumed_ids]
        if rank_fn is not None:
            try:
                ranked = list(
                    rank_fn(ranked_pool, query, query_embedding, embeddings_map or {})
                )
            except Exception as exc:  # noqa: BLE001 — §4.9 never-raise
                logger.warning(
                    "[POK:Degraded] rank_fn failed (%s) — falling back "
                    "to slug order",
                    exc,
                )
                ranked = sorted(ranked_pool, key=lambda r: r.slug)
        else:
            ranked = sorted(ranked_pool, key=lambda r: r.slug)
        fresh: list[ProjectOperatingKnowledge] = []
        stale_pool: list[ProjectOperatingKnowledge] = []
        for row in ranked:
            (stale_pool if stale_days(row, now=now) is not None else fresh).append(row)
        ranked = fresh + stale_pool

        # 5. F5 ADMISSION over UNITS: each ranked row expands to its
        #    unit (a family's FIRST-ranked member carries the whole
        #    family; later members of an already-consumed family are
        #    no-ops). Units are admitted whole (families atomic) or
        #    skipped; on the first non-fitting unit the scan stops and
        #    the remainder is counted (F5 rider — the tail counts).
        emitted_fams: set[str] = set()
        stop = False
        for idx, row in enumerate(ranked):
            fam = _slug_family(row.slug)
            if fam is not None:
                if fam in consumed_fams or fam in emitted_fams:
                    continue
                members = families.get(fam, [row])
                emitted_fams.add(fam)
            else:
                if row.id in consumed_ids:
                    continue
                members = [row]
            n, c = _unit_cost(members)
            if stop or not _fits(n, c):
                # F5 rider: this unit (and everything after it in the
                # ranked scan) is the omitted tail.
                stop = True
                result.omitted += len(members)
                continue
            _admit(members, tier="ranked")
            consumed_ids.update(m.id for m in members)
            if fam is not None:
                consumed_fams.add(fam)

        # family remainder: visible members of families that never
        # rendered (unreached by the ranked scan — e.g. ranked pool
        # exhausted early or a pinned-only family partially admitted).
        admitted_ids = {v.row.id for v in admitted}
        counted_fams = consumed_fams | emitted_fams
        for fam, members in families.items():
            if fam in counted_fams:
                continue
            result.omitted += sum(1 for m in members if m.id not in admitted_ids)

        result.entries = admitted
        return result
    except Exception as exc:  # noqa: BLE001 — §4.9 never-raise (the :5083 swallow family)
        logger.warning(
            "[POK:Degraded] admission failed (%s) — no POK block this "
            "turn; the once-flag stays unwritten and the next turn "
            "retries",
            exc,
        )
        return AdmissionResult(
            entries=[], omitted=0, visible_total=0, agent_id=agent_id, degraded=True
        )


def render_pok_block(result: AdmissionResult) -> str | None:
    """Render the block CONTENT (the builder wraps it via
    ``_make_context_message``). ``None`` = nothing to render (no
    admitted entries — no empty-host noise, the shared_meta_kv
    precedent). The caller owns the final escape check: bodies are
    newline-flattened here and ESCAPED by the builder
    (``escape_for_context_block``) — the fixed template carries no
    slot a structural token can survive (A-29 render side)."""
    if not result.entries:
        return None
    lines = [POK_HEADER_LINE]
    for entry in result.entries:
        lines.append(entry.render())
    if result.omitted > 0:
        n = result.omitted
        lines.append(
            f"({n} more entr{'y' if n == 1 else 'ies'} not shown — use "
            "pok_query for the full view)"
        )
    return "\n".join(lines)


def pok_block_present(messages: list[Any]) -> bool:
    """True when the persistent half carries the POK block (stable-id
    ``pok:{project_id}`` or ``context_kind == 'pok'`` match). The
    landing site calls this BEFORE writing the once-flag (the AM-22
    discipline — the flag write site is the CONSUMER)."""
    for msg in messages or []:
        kwargs = getattr(msg, "additional_kwargs", None) or {}
        if kwargs.get("context_kind") == "pok":
            return True
        msg_id = getattr(msg, "id", None) or ""
        if isinstance(msg_id, str) and msg_id.startswith("pok:"):
            return True
    return False
