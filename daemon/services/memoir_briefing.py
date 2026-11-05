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
    MEMOIR_UNTITLED_TITLE,
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


def _render_stable_text(
    *,
    persona_body: str | None,
    keepsakes: Sequence[Any],
    provenance_class: str,
) -> str:
    """Render the STABLE zone — persona + ALL keepsakes, never truncated.

    ``persona_body is None`` is the persona-missing path (D5.9): the
    zone renders keepsakes-only under the pinned prefix. A missing
    persona must not suppress the keepsakes that ARE available.
    """
    parts: list[str] = []
    if persona_body is None:
        parts.append(MEMOIR_PERSONA_MISSING_PREFIX)
    else:
        parts.extend([_PERSONA_HEADER, str(persona_body)])
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


def _escape_for_context_block(content: str) -> str:
    """The module's ADR-7 escaper (``context_messages.escape_for_context_block``).

    Lazy import keeps this module importable in isolation and avoids an
    import cycle (``context_messages`` lazily imports THIS module at the
    seam).
    """
    from .context_messages import escape_for_context_block

    return escape_for_context_block(content)


def render_briefing_body(
    *,
    persona_body: str | None,
    keepsakes: Sequence[Any],
    episodes: Sequence[Any],
    provenance_class: str = "",
) -> str | None:
    """Render the briefing body — the two zones + the backstop (D5.7/D5.8).

    Returns the full escaped body, or ``None`` when the block is
    WITHHELD (the stable zone alone exceeds the 48,000-char absolute
    bound — the WARNING is logged here, and the caller's ``None``
    propagates into "no block ⇒ no flag write").

    Measurement discipline (the metrics-read M-1 doctrine): the
    ceilings are measured on the SHIPPED bytes — each zone is escaped
    first (ADR-7), then measured, so the number in the disclosure is
    the number the agent actually receives.
    """
    stable_raw = _render_stable_text(
        persona_body=persona_body,
        keepsakes=keepsakes,
        provenance_class=provenance_class,
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

    parts: list[str] = [stable]

    if stable_chars > MEMOIR_SECTION_CHAR_CEILING:
        # First backstop rung: stable in full, dynamic dropped, the
        # overage disclosed (AM-43/DR-1). The stable zone is NEVER
        # truncated — "never truncated" and "hard ceiling" reconcile
        # by the OUTCOME.
        parts.append(
            MEMOIR_STABLE_OVERAGE_DISCLOSURE.format(
                chars=stable_chars,
                over=stable_chars - MEMOIR_SECTION_CHAR_CEILING,
            )
        )
    else:
        dynamic_lines = _fit_dynamic_lines(
            _render_dynamic_lines(episodes),
            MEMOIR_DYNAMIC_TOKEN_BUDGET,
        )
        if dynamic_lines:
            parts.append(
                "\n".join([_RECENT_HEADER, *dynamic_lines])
            )

    parts.append(MEMOIR_AFFORDANCE_LINE)
    return "\n\n".join(parts)


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


async def _build_memoir_briefing_message(
    agent_id: str,
    instance_id: str,
    manager: Any,
):
    """Build the turn-1 memoir block, or ``None`` (D5.1's builder).

    Off-loop, deadline-bounded reads (AM-37/AM-38): every repository
    read runs in a worker thread under ``asyncio.wait_for``. A failed
    read must NOT abort context assembly — swallow and skip (D5.14).

    The repository wraps the CALLER'S engine (``manager._engine``) —
    this module never builds an engine and never reads an ambient
    ``POSTGRES_*`` variable (the fence).
    """
    engine = getattr(manager, "_engine", None)
    if engine is None or not agent_id:
        return None
    from daemon.repositories.memoir.repository import MemoirRepository

    repo = MemoirRepository(engine)

    persona = await asyncio.wait_for(
        asyncio.to_thread(repo.get_approved_persona, agent_id),
        timeout=MEMOIR_READ_TIMEOUT_S,
    )
    keepsakes = await asyncio.wait_for(
        asyncio.to_thread(repo.fetch_keepsakes, agent_id),
        timeout=MEMOIR_READ_TIMEOUT_S,
    )
    episodes = await asyncio.wait_for(
        asyncio.to_thread(repo.fetch_latest, agent_id, MEMOIR_DYNAMIC_EPISODES),
        timeout=MEMOIR_READ_TIMEOUT_S,
    )

    provenance_class = _capture_provenance_class(manager, instance_id)

    body = render_briefing_body(
        persona_body=(persona.body if persona is not None else None),
        keepsakes=keepsakes or [],
        episodes=episodes or [],
        provenance_class=provenance_class,
    )
    if body is None:
        # Withheld (AM-43): the WARNING is already logged; ``None``
        # propagates so the consumer finds NO block and writes NO flag.
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
    "render_briefing_body",
    "memoir_block_present",
    "knob_allows",
    "_build_memoir_briefing_message",
]
