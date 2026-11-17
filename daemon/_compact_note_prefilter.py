"""The compact-note PRE-FILTER — a DETERMINISTIC, ZERO-LLM marker matcher.

**DORMANT BY DEFAULT.** The stage ships disabled (``CompactionConfig.
compact_note_prefilter_enabled`` — code-default OFF, env-overridable,
deliberately absent from ``config.yaml``).

Substitution disclosure (architect §B/§C/§F; the Emperor's directive
authorized *jev* filtering, the ratified doctrine says the overlay never
decides): the v1 occupant is **NOT** the decision engine. It is a
purpose-bound, non-family, in-process matcher with:

* **ZERO LLM calls** — no model, no budget, no cost;
* **no decision-family coupling** — it is NEVER registered on the
  decision registry, NEVER called through ``DecisionOverlayPort``, NEVER
  billed to the decision ledger (the decision family is UNTOUCHED:
  S1 shadow-only detached, EX-2 intact, D3 gate intact);
* its own kill lever (the config bool above);
* **identity on EVERY failure** — and identity is the SAME CODE PATH as
  "off", not two paths that happen to agree.

THE SEAM-SAFETY INVARIANT (architect §E — the operative rule):

    A pre-filter may drop a candidate group only when the note's
    structured marker block mechanically accounts for that group (exact
    message-id or group-index-span match, decidable WITHOUT judgment at
    filter time); every other group falls through to today's path
    unchanged, and an absent or unparseable marker block yields an empty
    drop-set — free-text notes make the filter VACUOUS BY CONSTRUCTION.

Two hard walls ride with it:

1. the **never-empty wall** — a drop-set that would empty a NON-EMPTY
   candidate set is discarded ⇒ identity;
2. **whole-group atomicity** — drops operate on whole ``MessageGroup``
   objects only (atomic tool sequences never split; the span math
   downstream is group-position-derived).

The gate is ``enabled AND note_present``: with no note the function
short-circuits BEFORE any list copy, any parse, or any arm-state read.

Marker block format (v1) — an optional FINAL fenced block of the note::

    <<<COMPACT-ACCOUNT
    accounted_for: msg:<message_id>, group:<start_idx>-<end_idx>, ...
    >>>

The matcher intersects the declared set with the ACTUAL candidate groups
(ids / spans re-derived at filter time); anything unparseable ⇒ an empty
drop-set.

Dropped groups are itemized in the envelope's existing dropped-spans
clause — never silently vanished (the caller receives the spans in the
ORIGINAL compactable coordinates so the doc's index space stays single).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

#: The marker block's opening fence.
MARKER_BLOCK_START = "<<<COMPACT-ACCOUNT"

#: The marker block's closing fence.
MARKER_BLOCK_END = ">>>"

#: The ``accounted_for:`` line's prefix.
ACCOUNTED_FOR_PREFIX = "accounted_for:"

#: The marker block — the LAST such block in the note wins (it is the
#: note's FINAL authored statement of what it accounts for).
_MARKER_BLOCK_RE = re.compile(
    re.escape(MARKER_BLOCK_START) + r"\s*\n(?P<body>.*?)\n?\s*"
    + re.escape(MARKER_BLOCK_END),
    re.DOTALL,
)

#: A ``msg:<message_id>`` reference.
_MSG_REF_RE = re.compile(r"^msg:(?P<id>\S+)$")

#: A ``group:<start_idx>-<end_idx>`` reference.
_GROUP_REF_RE = re.compile(r"^group:(?P<start>\d+)-(?P<end>\d+)$")

#: The outcome reasons (CONTENT-FREE — a label, never note text).
REASON_OFF = "off"
REASON_NO_NOTE = "no-note"
REASON_NO_MARKER_BLOCK = "no-marker-block"
REASON_UNPARSEABLE = "unparseable-marker-block"
REASON_NO_MATCH = "no-match"
REASON_NEVER_EMPTY_WALL = "never-empty-wall"
REASON_MARKER_MATCH = "marker-match"


@dataclass
class PrefilterResult:
    """The pre-filter's outcome.

    ``kept`` is the candidate list to summarize; ``dropped`` the groups
    the note mechanically accounted for. ``dropped_spans`` carries the
    dropped groups' ``(start, end)`` message positions in the ORIGINAL
    candidate-list coordinates (1-based, inclusive) so the doc's index
    space stays single and honest.
    """

    kept: list[Any]
    dropped: list[Any] = field(default_factory=list)
    enabled: bool = False
    matched: bool = False
    reason: str = REASON_OFF
    dropped_spans: list[tuple[int, int]] = field(default_factory=list)
    #: The ORIGINAL ``(start, end)`` position of each KEPT group — the
    #: caller's span arithmetic uses it to keep the doc's coordinates
    #: in the ORIGINAL space (the pre-filter's holes stay visible).
    kept_positions: list[tuple[int, int]] = field(default_factory=list)


def parse_marker_accounts(note: str) -> tuple[set[str], set[tuple[int, int]]]:
    """Parse the note's structured marker block.

    Returns ``(message_ids, group_spans)``.

    An ABSENT or UNPARSEABLE marker block yields TWO EMPTY SETS — the
    filter is then VACUOUS by construction (free-text notes drop
    nothing). Parsing is STRICT: a single malformed entry discards the
    WHOLE block (fail-closed towards identity).

    An out-of-set reference (an id that is not among the candidates) is
    NOT a parse failure — it simply matches nothing.
    """
    if not note:
        return set(), set()
    matches = list(_MARKER_BLOCK_RE.finditer(note))
    if not matches:
        return set(), set()
    body = matches[-1].group("body")  # the LAST block wins
    ids: set[str] = set()
    spans: set[tuple[int, int]] = set()
    seen_accounted_line = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(ACCOUNTED_FOR_PREFIX):
            # A line that is not an ``accounted_for:`` statement — the
            # block is unparseable (fail-closed towards identity).
            return set(), set()
        seen_accounted_line = True
        entries = line[len(ACCOUNTED_FOR_PREFIX) :].strip()
        if not entries:
            continue
        for entry in entries.split(","):
            entry = entry.strip()
            if not entry:
                continue
            msg_match = _MSG_REF_RE.match(entry)
            if msg_match:
                ids.add(msg_match.group("id"))
                continue
            group_match = _GROUP_REF_RE.match(entry)
            if group_match:
                spans.add(
                    (int(group_match.group("start")), int(group_match.group("end")))
                )
                continue
            # UNPARSEABLE entry — discard the WHOLE block.
            return set(), set()
    if not seen_accounted_line:
        return set(), set()
    return ids, spans


def _group_is_accounted(
    group: Any,
    ids: set[str],
    spans: set[tuple[int, int]],
) -> bool:
    """Whether the note's marker block mechanically accounts for a group.

    An EXACT message-id match (any message of the group) or an EXACT
    group-index-span match (the group's own ``start_idx``/``end_idx``).
    No judgment, no similarity, no ordering.
    """
    if ids:
        for message in getattr(group, "messages", []) or []:
            message_id = getattr(message, "id", None)
            if message_id is not None and str(message_id) in ids:
                return True
    if spans:
        group_span = (
            getattr(group, "start_idx", None),
            getattr(group, "end_idx", None),
        )
        if group_span in spans:
            return True
    return False


def _positions(groups: Sequence[Any]) -> list[tuple[int, int]]:
    """The 1-based ``(start, end)`` message positions of each group."""
    out: list[tuple[int, int]] = []
    cursor = 1
    for group in groups:
        count = len(getattr(group, "messages", []) or [])
        out.append((cursor, cursor + count - 1))
        cursor += count
    return out


def filter_candidate_groups(
    groups: Sequence[Any],
    note: str,
    *,
    enabled: bool,
) -> PrefilterResult:
    """Apply the compact-note pre-filter to the candidate groups.

    The gate is ``enabled AND note_present`` — with no note (or with the
    filter OFF) the function short-circuits BEFORE any list copy and
    returns the INPUT list unchanged: identity is the SAME CODE PATH as
    "off".

    Args:
        groups: The candidate groups (the compactable partition output
            of ``select_compactable_groups``). NEVER mutated.
        note: The authored note (the marker block is parsed from it).
        enabled: The kill-switch state.

    Returns:
        A :class:`PrefilterResult`. ``result.kept`` is the list to
        summarize; ``result.dropped_spans`` itemizes the dropped groups
        in the ORIGINAL candidate-list coordinates.
    """
    if not enabled:
        return PrefilterResult(
            kept=list(groups), enabled=False, reason=REASON_OFF
        )
    if not note:
        # Short-circuit BEFORE any list copy / parse / arm-state read.
        return PrefilterResult(
            kept=list(groups), enabled=True, reason=REASON_NO_NOTE
        )

    try:
        ids, spans = parse_marker_accounts(note)
    except Exception:  # pragma: no cover — defensive; every failure ⇒ identity
        logger.warning(
            "[compact-note prefilter] marker parse failed — skipping the "
            "filter (identity)"
        )
        return PrefilterResult(
            kept=list(groups), enabled=True, reason=REASON_UNPARSEABLE
        )

    if not ids and not spans:
        # No marker block (or an unparseable one) ⇒ the drop-set is
        # EMPTY — free-text notes make the filter vacuous by
        # construction.
        return PrefilterResult(
            kept=list(groups), enabled=True, reason=REASON_NO_MARKER_BLOCK
        )

    try:
        original_positions = _positions(groups)
        dropped_indexes: list[int] = []
        for index, group in enumerate(groups):
            if _group_is_accounted(group, ids, spans):
                dropped_indexes.append(index)
    except Exception:  # pragma: no cover — defensive; every failure ⇒ identity
        logger.warning(
            "[compact-note prefilter] match failed — skipping the filter "
            "(identity)"
        )
        return PrefilterResult(
            kept=list(groups), enabled=True, reason=REASON_UNPARSEABLE
        )

    if not dropped_indexes:
        return PrefilterResult(
            kept=list(groups), enabled=True, reason=REASON_NO_MATCH
        )

    if len(dropped_indexes) == len(groups):
        # NEVER-EMPTY WALL — a drop-set that would empty a non-empty
        # candidate set is discarded ⇒ identity.
        logger.info(
            "[compact-note prefilter] drop-set would empty the candidate "
            "set — discarded (identity)"
        )
        return PrefilterResult(
            kept=list(groups), enabled=True, reason=REASON_NEVER_EMPTY_WALL
        )

    dropped_set = set(dropped_indexes)
    kept = [group for index, group in enumerate(groups) if index not in dropped_set]
    dropped = [group for index, group in enumerate(groups) if index in dropped_set]
    dropped_spans = [
        original_positions[index] for index in dropped_indexes
    ]
    kept_positions = [
        original_positions[index] for index in range(len(groups)) if index not in dropped_set
    ]
    logger.info(
        "[compact-note prefilter] dropped %d of %d candidate groups "
        "(reason=%s)",
        len(dropped),
        len(groups),
        REASON_MARKER_MATCH,
    )
    return PrefilterResult(
        kept=kept,
        dropped=dropped,
        enabled=True,
        matched=True,
        reason=REASON_MARKER_MATCH,
        dropped_spans=dropped_spans,
        kept_positions=kept_positions,
    )


__all__ = [
    "ACCOUNTED_FOR_PREFIX",
    "MARKER_BLOCK_END",
    "MARKER_BLOCK_START",
    "PrefilterResult",
    "filter_candidate_groups",
    "parse_marker_accounts",
]
