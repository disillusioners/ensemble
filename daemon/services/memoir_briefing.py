"""The wake briefing — the turn-1 ``[SYSTEM CONTEXT: Memoir]`` block.

Memoir Store v1 — PHASE 5 (plan:
``.agents/shared/planning/ari-memoir-store/phase5-plan.md``, D5.1–D5.15;
packs ``test/packs/memoir_briefing_unit_test.sh`` +
``test/packs/memoir_briefing_seam_test.sh``).

WHAT THIS MODULE OWNS: the two-zone briefing block — the STABLE zone
(the approved persona + ALL keepsakes, rendered in full, NEVER
truncated) and the DYNAMIC zone (the latest episodes, oldest dropped
first under the token budget) — the derived 24,000-character absolute
ceiling with its withhold-without-truncating backstop (AM-43/DR-1),
the deadline-bounded reads (AM-38), and the byte-pinned surfaces
(keepsake line, affordance line, overage disclosure, withhold
WARNING, persona-missing prefix).

WHAT THIS MODULE MUST NEVER DO (AM-22/D5.4 — the worst failure mode
in the lane): WRITE the once-per-instance flag. ``assemble_context_messages``
BUILDS ONLY; the flag is written by the CONSUMER
(``daemon/services/instance_messaging.py``) after the returned
persistent half is CONFIRMED to contain the memoir block. The graph
path (``daemon/graph.py``) discards the persistent half, so a
builder-written flag would consume the flag on a thrown-away block and
permanently, silently lose the briefing for that instance.

Design contract refs: ``design.md`` §8 (a CONTEXT BLOCK, never a
loader/system-prompt injection — DD-10) and §8.2 (the two zones);
``decisions.md`` DD-11 (once per instance), DD-13 (identity from the
factory-captured agent id — never a caller-supplied key), DR-1 (the
token-vs-char reconciliation: tokens plan, 24,000 chars is the absolute
ceiling, "~12k" survives as the NOMINAL size); amendments AM-22 (the
flag write site is the consumer), AM-24 (the provenance class on the
keepsake line), AM-37 (the flag read is off-loop), AM-38 (every read is
deadline-bounded; the whole build degrades rather than delaying the
turn), AM-40 (there is NO compaction-hoist membership set — the hoist
is truthy-keyed on any ``context_kind``), AM-43 (the render backstop).

FENCE (the wave-1 production incident): this module NEVER builds an
engine and NEVER reads an ambient ``POSTGRES_*`` variable. The
repository is a thin wrapper around the CALLER'S engine
(``manager._engine``) — the same discipline
``daemon/repositories/memoir/repository.py`` documents. Tests use a
throwaway in-memory SQLite engine; no ``InstanceManager`` is ever
constructed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from daemon.repositories.memoir.models import (
    KEEPSAKE_LINE_TEMPLATE,
    MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET,
    MEMOIR_PROPOSALS_ZONE_MAX,
    MEMOIR_UNTITLED_TITLE,
    PROPOSALS_ZONE_HEADER,
)
from daemon.repositories.memoir.models import (
    render_keepsake_line as render_keepsake_line_shared,
)

logger = logging.getLogger(__name__)

# ── identity + flag (D5.12 / T5.4) ────────────────────────────────────────

#: The briefing builds for THIS agent and nobody else (D5.12). The
#: gate reads the id from the off-loop instance-row read (D5.5) — the
#: same read that fetches the once-flag — NEVER from a caller-supplied
#: key (DD-13/L3).
MEMOIR_AGENT_ID = "ari"

#: The once-per-instance metadata key. Written by the CONSUMER
#: (``instance_messaging.py``, D5.4/AM-22), read fresh by the seam
#: every assembly (revive-safe, D5.11). ``assemble_context_messages``
#: reads it but NEVER writes it.
MEMOIR_BRIEFING_FLAG = "memoir_briefing_injected"

# ── byte-pinned surfaces (D5.7 / D5.8 / D5.9 / D5.10 / D5.15) ────────────

#: The block title — ``_make_context_message`` renders the canonical
#: ``[SYSTEM CONTEXT: <title>]`` prefix around it (ADR-4/ADR-5).
MEMOIR_BLOCK_TITLE = "Memoir"

#: The ONE keepsake-line template (D5.7, AM-24). SINGLE-SOURCED in the
#: repository package (``daemon/repositories/memoir/models.py``) so
#: D2.12's admission arithmetic measures the same bytes this module
#: ships (W-1) — the constant above is IMPORTED from there and re-
#: exported for the pack's byte pin. The template is the pack's pinned
#: bytes — change it THERE and this pack goes RED.
# (``KEEPSAKE_LINE_TEMPLATE`` is imported at the top of this module.)

#: The closing affordance line (D5.10). The block ENDS with it — the
#: one-liner that answers "why doesn't she remember an hour ago" at
#: the affordance level (DD-11 stands; the v2 alarm-wake refresh is a
#: different, unbuilt lane).
MEMOIR_AFFORDANCE_LINE = (
    "Memory remains queryable mid-session via memoir_recall(query) / "
    "memoir_timeline(since?, until?, q?)."
)

#: The overage disclosure (D5.8, first backstop rung). Appended when
#: the stable zone ALONE exceeds the 24,000-char ceiling: the stable
#: zone still ships IN FULL, the dynamic zone is dropped entirely.
MEMOIR_STABLE_OVERAGE_DISCLOSURE = (
    "[…stable zone is {chars} chars — {over} over the 24,000-char "
    "ceiling; pin admission exceeded its budget]"
)

#: The withhold WARNING body (D5.8, second backstop rung). Logged at
#: WARNING level when the stable zone alone exceeds the 48,000-char
#: absolute bound; the block is withheld (render returns ``None``) and
#: the once-flag is therefore never written (the consumer only writes
#: when the block is present).
MEMOIR_WITHHOLD_WARNING = (
    "[Memoir] briefing withheld — stable zone {chars} chars exceeds "
    "the 48,000-char absolute bound"
)

#: The persona-missing prefix (D5.9/AM-39). The seed failed at boot or
#: the DB predates the seed: the stable zone renders KEEPSAKES-ONLY
#: under this prefix, and the flag IS still written (the block was
#: delivered; the persona is a separate gap).
MEMOIR_PERSONA_MISSING_PREFIX = (
    "[persona unavailable — no approved persona version]"
)

#: The TWO approval-queue lines (m1). Each renders inside its OWN zone —
#: the persona line in ``## Persona``, the proposal line in the
#: proposals zone — and ONLY when its queue is NON-EMPTY: at zero the
#: rendering is the empty string, so every existing briefing pin stays
#: byte-identical (zero token tax on an empty queue).
#:
#: EACH LINE NAMES THE SURFACE THE AGENT ACTS WITH, and the two surfaces
#: differ:
#:
#: * the PERSONA queue is acted on with the ``memoir_persona_ratify``
#:   verb (the 8th memoir verb — its existence is THAT verb module's
#:   pin, not this line's; the name is carried as a STRING here). The
#:   verb is the agent-side surface and it RELAYS the human-lane
#:   refusal verbatim when the gate refuses the caller — so this line
#:   deliberately does NOT claim whether the call will succeed ("an
#:   operator session approves …" would go stale the moment a persona
#:   self-ratify exemption lands); the refusal itself carries that
#:   detail.
#: * the PROPOSAL queue is the ``memoir_review`` tool (the 7th memoir
#:   verb) — the SAME name the registry ships.
MEMOIR_PERSONA_PENDING_LINE = (
    "- {count} persona version{s} pending approval — act with "
    "memoir_persona_ratify(version_id)."
)

MEMOIR_PROPOSAL_PENDING_LINE = (
    "- {count} keepsake proposal{s} pending review — act with "
    "memoir_review(title, decision, actor)."
)

# ── budgets (D5.7 — flag #3 / DR-1's model) ──────────────────────────────

#: Stable-zone admission bounds. These are WRITE-TIME promises (the
#: persona lane and the pin admission, D2.12); the renderer never
#: truncates against them — the render-time backstop below is what
#: makes "never truncated" true even when a write-time promise is
#: exceeded.
MEMOIR_PERSONA_TOKEN_BUDGET = 2000
MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET = 4000

#: The dynamic zone: the latest episodes, oldest dropped first under
#: this token budget (design §8.2).
MEMOIR_DYNAMIC_TOKEN_BUDGET = 3000

#: "latest ~5 episodes".
MEMOIR_DYNAMIC_EPISODES = 5

#: The hard absolute section ceiling (DR-1 — supersedes design §8.2's
#: "~12k chars" SURGICALLY; "~12k" survives as the NOMINAL size).
MEMOIR_SECTION_CHAR_CEILING = 24000

#: The absolute bound at which the block is WITHHELD entirely
#: (AM-43 — the backstop's second rung).
MEMOIR_SECTION_CHAR_ABSOLUTE = 48000

# ── deadlines (AM-38 — the turn is the product, the briefing is a wake) ──

#: Per-read deadline. The house class is ``BOOT_DB_TIMEOUT_S = 10``
#: (``daemon/constants.py:70``) — the same magnitude, carried here so
#: the briefing owns its number and tests can patch it.
MEMOIR_READ_TIMEOUT_S = 10

#: The WHOLE-build budget: knob read + flag read + content reads +
#: render. A breach degrades (no block, no flag write) — never delays
#: the turn.
MEMOIR_BUILD_BUDGET_S = 5.0

# ── the section headers (stable, deterministic block layout) ─────────────

_PERSONA_HEADER = "## Persona"
_KEEPSAKES_HEADER = "## Keepsakes"
_RECENT_HEADER = "## Recent episodes"

#: Single-sourced with the repository renderer (W-1) — the same
#: placeholder the keepsake-line admission arithmetic measures with.
_UNTITLED = MEMOIR_UNTITLED_TITLE


def _estimate_tokens(text: str) -> int:
    """The house token estimator (``daemon/loader.estimate_tokens``).

    Lazy import: the loader is a heavy module; this service must stay
    importable without it. tiktoken cl100k — the planning unit of the
    whole lane (R4: no new tokenizer dependency).
    """
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)


def _one_line(value: str | None) -> str:
    """Collapse a single field to ONE line (newlines are structure loss)."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _fmt_event_at(value: Any) -> str:
    """Render ``event_at`` as a bare date for the dynamic line."""
    if value is None:
        return ""
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)[:10]


def render_keepsake_line(
    *,
    title: str | None,
    pinned_by: str | None,
    pclass: str,
    pinned_reason: str | None,
) -> str:
    """Render ONE keepsake line from the byte-pinned template (D5.7).

    DELEGATES to the repository-package renderer (W-1) — the SAME
    function the pin admission measures with, so the briefing ships and
    the admission measures identical bytes by construction.

    ``pclass`` is the house provenance class (AM-24 — ``direct`` /
    ``inherited`` / ``""``), captured ONCE per briefing build and
    rendered identically on every keepsake line so a wrong ``pinned_by``
    claim stays detectable at read time as well as write time.
    """
    return render_keepsake_line_shared(
        title=title,
        pinned_by=pinned_by,
        pclass=pclass,
        pinned_reason=pinned_reason,
    )


def render_dynamic_line(episode: Any) -> str:
    """Render ONE dynamic-zone line — ``- [date] title: body``."""
    title = _one_line(getattr(episode, "title", None)) or _UNTITLED
    body = _one_line(getattr(episode, "body", None))
    return f"- [{_fmt_event_at(getattr(episode, 'event_at', None))}] {title}: {body}"


def render_queue_count_line(template: str, count: Any) -> str:
    """Render ONE queue-count line from its template, or ``""`` at zero.

    ALWAYS-PRESENT-BUT-SILENT-WHEN-ZERO: the queues are visible in the
    briefing whenever they carry work, and cost nothing when they do not
    (the block must not grow a "0 pending" line an agent would have to
    read on every turn). A non-integer / negative count is treated as
    zero — a count is an annotation, never a reason to fail the build.
    """
    try:
        value = int(count)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return template.format(
        count=value,
        s="" if value == 1 else "s",
    )


def _render_stable_text(
    *,
    persona_body: str | None,
    keepsakes: Sequence[Any],
    provenance_class: str,
    persona_pending_count: int = 0,
) -> str:
    """Render the STABLE zone — persona + ALL keepsakes, never truncated.

    ``persona_body is None`` is the persona-missing path (D5.9): the
    zone renders keepsakes-only under the pinned prefix. A missing
    persona must not suppress the keepsakes that ARE available.

    The persona-queue line (m1) rides INSIDE this zone so the stable
    chars — and therefore the 24,000-char disclosure and the 48,000-char
    withhold measurements — account for the bytes it ships. It renders
    under the missing-persona prefix too: pending persona versions are
    exactly the actionable fact on that path.
    """
    parts: list[str] = []
    pending_line = render_queue_count_line(
        MEMOIR_PERSONA_PENDING_LINE, persona_pending_count
    )
    if persona_body is None:
        parts.append(MEMOIR_PERSONA_MISSING_PREFIX)
        if pending_line:
            parts.append(pending_line)
    else:
        parts.extend([_PERSONA_HEADER, str(persona_body)])
        if pending_line:
            parts.append(pending_line)
    if keepsakes:
        parts.append("")
        parts.append(_KEEPSAKES_HEADER)
        for keepsake in keepsakes:
            parts.append(
                render_keepsake_line(
                    title=getattr(keepsake, "title", None),
                    pinned_by=getattr(keepsake, "pinned_by", None),
                    pclass=provenance_class,
                    pinned_reason=getattr(keepsake, "pinned_reason", None),
                )
            )
    return "\n".join(parts)


def _render_dynamic_lines(episodes: Sequence[Any]) -> list[str]:
    """Render the DYNAMIC zone's lines — newest first, keepsakes excluded.

    Keepsakes are already rendered in the stable zone (a keepsake is a
    STATUS on an episode row, so ``fetch_latest`` returns them too);
    rendering them again in the dynamic zone would pay their tokens
    twice and blur which zone carries the pin.
    """
    lines: list[str] = []
    for episode in episodes:
        if bool(getattr(episode, "is_keepsake", False)):
            continue
        lines.append(render_dynamic_line(episode))
    return lines


def _fit_dynamic_lines(lines: Sequence[str], budget_tokens: int) -> list[str]:
    """Apply the dynamic token budget — oldest dropped first (D5.7).

    ``lines`` is NEWEST-first (``fetch_latest`` order). The OLDEST line
    (the list tail) is dropped repeatedly until the rendered total fits
    the budget; the NEWEST line is always kept even when it alone
    exceeds the budget (dropping everything would empty the zone — the
    newest memory is the least dispensable).
    """
    if not lines:
        return []
    escaped = [_escape_for_context_block(line) for line in lines]
    kept = list(escaped)
    total = sum(_estimate_tokens(line) for line in kept)
    while len(kept) > 1 and total > budget_tokens:
        total -= _estimate_tokens(kept.pop())  # the tail is the oldest
    return kept


def admit_proposal_lines(
    lines: Sequence[str] | None,
    *,
    char_budget: int = MEMOIR_PROPOSALS_ZONE_CHAR_BUDGET,
    max_lines: int = MEMOIR_PROPOSALS_ZONE_MAX,
    prefix: str | None = None,
) -> list[str]:
    """The ``[PROPOSALS]`` zone admission — its OWN measured section.

    WHY A SEPARATE ZONE (the arithmetic trap §3-A6 closed): persona +
    keepsake admission alone (~6,000 tokens ≈ 24,000 chars) can fill the
    whole 24,000-char ceiling, so bolting proposals into the STABLE text
    would trip the stable-overage rung and silently kill EVERY dynamic
    episode whenever keepsake admission is full. Proposals therefore get
    their own measured section with their own admission bound, applied at
    extraction time.

    Measurement discipline (W-1): the bound is measured on the SHIPPED
    bytes — each line is escaped first (ADR-7), and the header wrapper
    (``PROPOSALS_ZONE_HEADER`` + its newline) is INCLUDED in the
    measurement, because those bytes ship too.

    ``prefix`` (m1) is the OPTIONAL count line — ZONE OVERHEAD, not a
    proposal. It is measured FIRST, so a long prefix can push proposal
    lines out of the bound; and it is returned as element 0 of the
    admitted list, so the zone renders it even when NO proposal line
    fits. ``max_lines`` bounds the PROPOSAL lines only; a prefix that
    cannot fit beside the header wrapper at all is refused together with
    the zone (the bound is the bound — nothing is truncated).

    Weakest-first dropping is the CALLER's rung (``render_briefing_body``
    applies the total-based trim per the V2-D 6.5 ladder); this function
    only enforces the STRUCTURAL admission: at most ``max_lines`` lines
    and at most ``char_budget`` characters of shipped zone text.
    """
    # The header wrapper is overhead in every arm — the prefix joins it
    # BEFORE any proposal line is considered (measured first).
    overhead = len(PROPOSALS_ZONE_HEADER) + 1  # the header line + its newline
    kept: list[str] = []
    if prefix:
        escaped_prefix = _escape_for_context_block(prefix)
        with_prefix = overhead + len(escaped_prefix) + 1
        if with_prefix > char_budget:
            return []
        kept.append(escaped_prefix)
        overhead = with_prefix

    if not lines:
        return kept
    escaped = [_escape_for_context_block(line) for line in lines]
    total = overhead
    for line in escaped[: max(0, int(max_lines))]:
        candidate = total + len(line) + 1  # + the line's own newline
        if candidate > char_budget:
            # THE BOUND IS THE BOUND — and the lines arrive STRONGEST-FIRST,
            # so the first line that does not fit ends the admission: every
            # line after it is WEAKER than the one refused. (Admitting a
            # later weaker line while dropping a stronger one would invert
            # the drop-weakest-first order the trim rung owns.) A line is
            # never truncated — a truncated claim reads as a different claim.
            break
        kept.append(line)
        total = candidate
    return kept


def _compose_briefing_body(*, stable: str, dynamic_lines: Sequence[str],
                           proposal_lines: Sequence[str]) -> str:
    """Join the shipped sections exactly as the block ships them."""
    parts: list[str] = [stable]
    if dynamic_lines:
        parts.append("\n".join([_RECENT_HEADER, *dynamic_lines]))
    if proposal_lines:
        parts.append("\n".join([PROPOSALS_ZONE_HEADER, *proposal_lines]))
    parts.append(MEMOIR_AFFORDANCE_LINE)
    return "\n\n".join(parts)


def escape_for_context_block(content: str) -> str:
    """Alias of :func:`_escape_for_context_block` (an exported spelling).

    Tests measure the SAME escape the renderer applies; exposing it here
    keeps the two from drifting without importing the heavier
    ``context_messages`` module at import time.
    """
    return _escape_for_context_block(content)


def _escape_for_context_block(content: str) -> str:
    """The module's ADR-7 escaper (``context_messages.escape_for_context_block``).

    Lazy import keeps this module importable in isolation and avoids an
    import cycle (``context_messages`` lazily imports THIS module at the
    seam).
    """
    from .context_messages import escape_for_context_block as _escape

    return _escape(content)


def render_briefing_body(
    *,
    persona_body: str | None,
    keepsakes: Sequence[Any],
    episodes: Sequence[Any],
    provenance_class: str = "",
    proposal_lines: Sequence[str] | None = None,
    persona_pending_count: int = 0,
    proposal_pending_count: int = 0,
) -> str | None:
    """Render the briefing body — the zones + the backstop (D5.7/D5.8/T6.2).

    Returns the full escaped body, or ``None`` when the block is
    WITHHELD (the stable zone alone exceeds the 48,000-char absolute
    bound — the WARNING is logged here, and the caller's ``None``
    propagates into "no block ⇒ no flag write").

    Measurement discipline (the metrics-read M-1 doctrine): the
    ceilings are measured on the SHIPPED bytes — each zone is escaped
    first (ADR-7), then measured, so the number in the disclosure is
    the number the agent actually receives.

    THE ADMISSION LADDER (V2-D 6.5 — CANONICAL SEMANTICS LIVE
    EXCLUSIVELY IN THE VERBATIM §A6 QUOTE, cross-referenced here and
    NEVER paraphrased; see
    ``.agents/shared/planning/ari-memoir-store/v2-architecture-recommendation.md``
    §A6 and ``v2-phase6-plan.md`` V2-D 6.5):

    > with explicit rung order **fit dynamic → trim dynamic → trim
    > proposals (drop weakest first) → rung-2 withhold**

    This function implements exactly those rungs in that order. The
    measurement basis is ``stable_chars`` (the escaped stable zone
    ALONE): the withhold (> 48,000) is reachable ONLY by
    stable-zone overflow, and the total-based proposals trim is
    reachable only when ``stable_chars + dynamic_chars`` lands within
    the proposals zone's own bound (≤ 1,000 chars) of the 24,000-char
    ceiling — the reachability analysis recorded in V2-D 6.5, pinned by
    ``tests/unit/test_memoir_keepsake_proposals.py``.

    THE TWO QUEUE-COUNT LINES (m1) are OPTIONAL parameters that default
    to 0, so every existing call path keeps rendering byte-identical
    bytes at an empty queue. The persona line renders inside the stable
    zone (it is measured by the same backstop arithmetic as the rest of
    that zone); the proposal line rides the proposals zone as its
    measured PREFIX (inside the zone's own bound, not appended outside
    it) and is the LAST thing the proposals trim rung drops — after
    every proposal line, because a count with nothing to review is still
    the actionable fact.
    """
    stable_raw = _render_stable_text(
        persona_body=persona_body,
        keepsakes=keepsakes,
        provenance_class=provenance_class,
        persona_pending_count=persona_pending_count,
    )
    stable = _escape_for_context_block(stable_raw)
    stable_chars = len(stable)

    if stable_chars > MEMOIR_SECTION_CHAR_ABSOLUTE:
        logger.warning(
            MEMOIR_WITHHOLD_WARNING.format(
                chars=stable_chars,
            )
        )
        return None

    if stable_chars > MEMOIR_SECTION_CHAR_CEILING:
        # First backstop rung: stable in full, dynamic and proposals
        # dropped, the overage disclosed (AM-43/DR-1). The stable zone is
        # NEVER truncated — "never truncated" and "hard ceiling"
        # reconcile by the OUTCOME.
        parts = [
            stable,
            MEMOIR_STABLE_OVERAGE_DISCLOSURE.format(
                chars=stable_chars,
                over=stable_chars - MEMOIR_SECTION_CHAR_CEILING,
            ),
            MEMOIR_AFFORDANCE_LINE,
        ]
        return "\n\n".join(parts)

    # ── FIT DYNAMIC ──────────────────────────────────────────────────
    dynamic_lines = _fit_dynamic_lines(
        _render_dynamic_lines(episodes),
        MEMOIR_DYNAMIC_TOKEN_BUDGET,
    )
    # The proposals zone's OWN structural admission (≤ 1,000 chars
    # including the header wrapper, at most 3 lines) — measured on the
    # shipped bytes, W-1. The count line (m1) is measured FIRST as zone
    # overhead, so it can never be appended outside the budget.
    proposal_prefix = render_queue_count_line(
        MEMOIR_PROPOSAL_PENDING_LINE, proposal_pending_count
    )
    admitted_proposals = admit_proposal_lines(
        proposal_lines, prefix=proposal_prefix or None
    )
    # Was the prefix admitted (element 0)? Compare on the SAME escaped
    # bytes the admission measured.
    prefix_shipped = bool(proposal_prefix) and bool(admitted_proposals) and (
        admitted_proposals[0] == _escape_for_context_block(proposal_prefix)
    )

    body = _compose_briefing_body(
        stable=stable,
        dynamic_lines=dynamic_lines,
        proposal_lines=admitted_proposals,
    )
    # ── TRIM DYNAMIC (oldest first) ──────────────────────────────────
    while len(body) > MEMOIR_SECTION_CHAR_CEILING and dynamic_lines:
        dynamic_lines = dynamic_lines[:-1]  # the tail is the oldest
        body = _compose_briefing_body(
            stable=stable,
            dynamic_lines=dynamic_lines,
            proposal_lines=admitted_proposals,
        )
    # ── TRIM PROPOSALS (drop weakest first) ──────────────────────────
    # The selection is strongest-first, so the tail IS the weakest. The
    # count prefix (element 0, when admitted) is ZONE OVERHEAD, not a
    # proposal: it holds the floor until every proposal line is gone, and
    # is dropped as the absolute last resort so the 24,000-char ceiling
    # still binds.
    floor = 1 if prefix_shipped else 0
    while len(body) > MEMOIR_SECTION_CHAR_CEILING and len(admitted_proposals) > floor:
        admitted_proposals = admitted_proposals[:-1]
        body = _compose_briefing_body(
            stable=stable,
            dynamic_lines=dynamic_lines,
            proposal_lines=admitted_proposals,
        )
    if prefix_shipped and len(body) > MEMOIR_SECTION_CHAR_CEILING:
        admitted_proposals = admitted_proposals[1:]
        body = _compose_briefing_body(
            stable=stable,
            dynamic_lines=dynamic_lines,
            proposal_lines=admitted_proposals,
        )
    return body


def memoir_block_present(messages: Sequence[Any] | None, agent_id: str | None = None) -> bool:
    """The consumer-side presence check (D5.4) — is the memoir block HERE?

    The once-flag may be written ONLY after the returned persistent
    half is confirmed to contain the memoir block: a stable-id match
    (``memoir:{agent_id}``) or a ``context_kind == "memoir"`` match.
    The stable id needs the agent id; the kind match does not — so a
    failed instance-row read cannot strand the flag unset when the
    block is visibly present.

    The id is composed through the DECLARED single source
    (``context_messages._stable_id_for`` — tidier A3/H5): the format's
    one home. Lazy import (the module-level one would cycle: the seam
    lazily imports THIS module).
    """
    stable_id = None
    if agent_id:
        from .context_messages import _stable_id_for

        stable_id = _stable_id_for("memoir", agent_id=agent_id)
    for message in messages or []:
        kwargs = getattr(message, "additional_kwargs", None) or {}
        if kwargs.get("context_kind") == "memoir":
            return True
        if stable_id is not None and getattr(message, "id", None) == stable_id:
            return True
    return False


def knob_allows(config: Any = None) -> bool:
    """Re-read the kill-switch NOW (D5.13 — call-time, never cached).

    OFF ⇒ the briefing is not built and NO flag is written, so a later
    enable delivers the briefing on the next turn (intended). The
    memory is unreadable meanwhile — no other read path exists in v1.
    """
    from daemon.tools.memoir_knob import resolve_memoir_knob

    try:
        return bool(resolve_memoir_knob(config).enabled)
    except Exception:  # pragma: no cover - defensive; the knob never raises
        return False


def _capture_provenance_class(manager: Any, instance_id: str) -> str:
    """The house provenance class, captured ONCE per build (AM-24).

    ONE home for the derivation (tidier A11/M9): delegates to
    ``daemon/tools/provenance.py``'s ``capture_provenance_class_name``
    (the same helper the memoir tools use — it logs before any empty
    return). The manager read is pure in-memory state.
    """
    from daemon.tools.provenance import capture_provenance_class_name

    return capture_provenance_class_name(manager, instance_id)


async def _guarded_read(
    label: str,
    read: Any,
    *args: Any,
    default: Any,
) -> Any:
    """ONE deadline-bounded briefing read that CANNOT abort the build.

    AM-38 (every read is deadline-bounded) + tidier M17 (each read is
    guarded INDIVIDUALLY): an exception OR a timeout degrades only this
    read to ``default`` and logs a reason token, so a missing persona
    still ships the keepsakes (D5.9's explicit promise).
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(read, *args),
            timeout=MEMOIR_READ_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — a read must never kill the block
        logger.warning(
            "[Memoir] briefing read failed read=%s reason=%s — degrading "
            "this zone only",
            label,
            type(exc).__name__,
        )
        return default


def _read_proposal_lines(engine: Any, agent_id: str) -> list[str]:
    """The proposals zone's line bodies — one sync read (off-loop by caller).

    The service is constructed against the CALLER'S engine (the fence:
    this module never builds an engine). A ledger failure raises out of
    here and is degraded by the ``_guarded_read`` wrapper at the build
    site — the zone disappears, the rest of the briefing ships.
    """
    from daemon.services.memoir_keepsake_proposal_service import (
        MemoirKeepsakeProposalService,
    )

    service = MemoirKeepsakeProposalService(engine, agent_id=agent_id)
    return service.zone_lines(limit=MEMOIR_PROPOSALS_ZONE_MAX, agent_id=agent_id)


def _read_persona_pending_count(engine: Any, agent_id: str) -> int:
    """The persona queue's OPEN count — one sync COUNT (off-loop by caller).

    CHEAP BY CONSTRUCTION: the read is a ``SELECT COUNT(*)`` — the
    briefing must never materialize the lineage to answer "is anything
    awaiting approval?". It goes through the repository module's
    ``count_pending_persona`` (the count's ONE home is the persona
    repository), so the briefing widens neither the episode surface's
    closed D2.1 set nor the persona repository's pinned caller census.
    """
    from daemon.repositories.memoir.repository import count_pending_persona

    return count_pending_persona(engine, agent_id)


def _read_proposal_pending_count(engine: Any, agent_id: str) -> int:
    """The proposal queue's OPEN count — one sync COUNT (off-loop).

    Delegates to the proposal service's ``count_pending_reviews``, whose
    predicates are the SAME ones the zone renders with (undecided + open
    delivery status), so the count and the zone can never disagree about
    what "pending" means.
    """
    from daemon.services.memoir_keepsake_proposal_service import (
        MemoirKeepsakeProposalService,
    )

    service = MemoirKeepsakeProposalService(engine, agent_id=agent_id)
    return service.count_pending_reviews(agent_id=agent_id)


async def _render_briefing_body_for(
    agent_id: str,
    instance_id: str,
    manager: Any,
) -> str | None:
    """Render the briefing BODY (persona + keepsakes + proposals + dynamic).

    The ONE render path shared by the TURN-1 briefing block (kind
    ``memoir``) and the WAKE-REFRESH block (kind ``memoir_refresh``,
    phase 6 / SF6): both carry the same fresh payload, and the refresh
    lane must not grow a second renderer that could drift from the
    briefing's (the single-source discipline).

    Off-loop, deadline-bounded reads (AM-37/AM-38): every repository
    read runs in a worker thread under ``asyncio.wait_for``. A failed
    read must NOT abort context assembly — swallow and skip (D5.14).

    The repository wraps the CALLER'S engine (``manager._engine``) —
    this module never builds an engine and never reads an ambient
    ``POSTGRES_*`` variable (the fence).

    Returns the escaped body, or ``None`` when the block is withheld (the
    stable zone alone exceeds the 48,000-char absolute bound) or when no
    engine/agent is available.
    """
    engine = getattr(manager, "_engine", None)
    if engine is None or not agent_id:
        return None
    from daemon.repositories.memoir.repository import MemoirRepository

    repo = MemoirRepository(engine)

    # ── Tidier M17: PER-READ guards (D5.9 conformance). A failure in ONE
    # read must degrade ONLY its own zone — before this, a persona read
    # error aborted the whole build and the keepsakes (which HAD loaded)
    # never shipped. Each failure logs its reason token; the block still
    # assembles from whatever is available.
    persona = await _guarded_read(
        "persona", repo.get_approved_persona, agent_id, default=None
    )
    keepsakes = await _guarded_read(
        "keepsakes", repo.fetch_keepsakes, agent_id, default=[]
    )
    episodes = await _guarded_read(
        "episodes",
        repo.fetch_latest,
        agent_id,
        MEMOIR_DYNAMIC_EPISODES,
        default=[],
    )

    provenance_class = _capture_provenance_class(manager, instance_id)

    # ── The [PROPOSALS] zone (phase 6, T6.2 / V2-D 6.5 / N2) ──────────
    # ONE extra guarded read: the zone's strongest-first proposal lines.
    # The read is its OWN failure domain (a ledger problem degrades the
    # zone to "no proposals", never the persona/keepsakes/dynamic zones).
    proposal_lines = await _guarded_read(
        "proposals",
        _read_proposal_lines,
        engine,
        agent_id,
        default=[],
    )

    # ── The TWO approval-queue counts (m1) ────────────────────────────
    # ONE guarded COUNT per queue, each its OWN failure domain: a failed
    # count degrades to "no line", never aborts the build (and never
    # blanks the zone it annotates). COUNT only — never a row read.
    persona_pending = await _guarded_read(
        "persona_pending",
        _read_persona_pending_count,
        engine,
        agent_id,
        default=0,
    )
    proposal_pending = await _guarded_read(
        "proposal_pending",
        _read_proposal_pending_count,
        engine,
        agent_id,
        default=0,
    )

    return render_briefing_body(
        persona_body=(persona.body if persona is not None else None),
        keepsakes=keepsakes or [],
        episodes=episodes or [],
        provenance_class=provenance_class,
        proposal_lines=proposal_lines or [],
        persona_pending_count=persona_pending or 0,
        proposal_pending_count=proposal_pending or 0,
    )


async def _build_memoir_briefing_message(
    agent_id: str,
    instance_id: str,
    manager: Any,
):
    """Build the turn-1 memoir block, or ``None`` (D5.1's builder).

    Wraps :func:`_render_briefing_body_for` into the ``memoir`` kind +
    ``memoir:{agent_id}`` stable id. The rendering (and every budget /
    backstop rule) lives in the ONE shared render path.
    """
    body = await _render_briefing_body_for(agent_id, instance_id, manager)
    if body is None:
        # Withheld (AM-43) or no engine: ``None`` propagates so the
        # consumer finds NO block and writes NO flag.
        return None

    from .context_messages import (
        CONTEXT_KIND_MEMOIR,
        _make_context_message,
        _stable_id_for,
    )

    return _make_context_message(
        CONTEXT_KIND_MEMOIR,
        MEMOIR_BLOCK_TITLE,
        body,
        id_=_stable_id_for("memoir", agent_id=agent_id),
    )


__all__ = [
    "MEMOIR_AGENT_ID",
    "MEMOIR_BRIEFING_FLAG",
    "MEMOIR_BLOCK_TITLE",
    "KEEPSAKE_LINE_TEMPLATE",
    "MEMOIR_AFFORDANCE_LINE",
    "MEMOIR_STABLE_OVERAGE_DISCLOSURE",
    "MEMOIR_WITHHOLD_WARNING",
    "MEMOIR_PERSONA_MISSING_PREFIX",
    "MEMOIR_PERSONA_PENDING_LINE",
    "MEMOIR_PROPOSAL_PENDING_LINE",
    "MEMOIR_PERSONA_TOKEN_BUDGET",
    "MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET",
    "MEMOIR_DYNAMIC_TOKEN_BUDGET",
    "MEMOIR_DYNAMIC_EPISODES",
    "MEMOIR_SECTION_CHAR_CEILING",
    "MEMOIR_SECTION_CHAR_ABSOLUTE",
    "MEMOIR_READ_TIMEOUT_S",
    "MEMOIR_BUILD_BUDGET_S",
    "render_keepsake_line",
    "render_dynamic_line",
    "render_queue_count_line",
    "render_briefing_body",
    "admit_proposal_lines",
    "escape_for_context_block",
    "memoir_block_present",
    "knob_allows",
    "_read_proposal_lines",
    "_read_persona_pending_count",
    "_read_proposal_pending_count",
    "_render_briefing_body_for",
    "_build_memoir_briefing_message",
]
