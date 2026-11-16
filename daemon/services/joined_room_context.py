"""The JOINED-ROOM context block — the agent group chat P5 system message.

P6 H2 (2026-11-16) — the structural extraction the P5 tidier
recommended: the joined-room builder + its constants MOVE here from
``daemon/services/context_messages.py`` (which is back under 3000 lines
after the move). The module is a MOVE, not a fork: every name is
re-exported from ``context_messages``, so the historical import surface
(``from daemon.services.context_messages import ROOM_GUIDELINES_TEXT,
build_joined_room_context_message``) keeps working.

THE RULING this block implements (architecture §5): *"system message =
guidelines + short digest ONLY; full history behind the read tool"*.
The block renders FOUR things per room, in this order:

1. the member's OWN NAME FIRST (Ruling B — self-recognition: the
   member must be able to spot a message aimed at it);
2. the guidelines (the STATIC CONSTANT below — milestone-level
   messaging, the warm-join steer, the addressing convention);
3. the roster, INCLUDING the owner;
4. the digest — last-N messages + the unread count (NEVER the full
   history).

Every DB touch is OFF-LOOP (``asyncio.to_thread`` — the v0.27.14
event-loop discipline); the builder never raises (a room-store failure
degrades to NO block, never to a broken turn).
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from langchain_core.messages import HumanMessage

from .context_block import (
    CONTEXT_PREFIX,
    CONTEXT_SUFFIX,
    _make_context_message,
    _stable_id_for,
    escape_for_context_block,
)

logger = logging.getLogger(__name__)


# Agent group chat P5 (2026-11-16): the JOINED-ROOM block kind. The
# "system message = guidelines + short digest ONLY; full history behind
# the read tool" ruling (architecture §5) — this block IS that system
# message: the working guidelines, the member's OWN NAME (self-
# recognition for @tag), the roster (incl. the owner) and a SHORT
# digest (last-N + unread count). NEVER the full history.
#
# Stable message id ``joined_room:{instance_id}`` — one block per
# instance; ``add_messages`` supersedes it in place on every turn, so
# the digest is always FRESH and never accumulates. Stamping
# ``context_kind`` lands the block in the truthy-keyed compaction hoist
# bucket (``daemon/_content_hardening.py`` → ``is_hoisted_injected``),
# so it survives every later compaction VERBATIM with zero new
# compaction plumbing.
#
# The guidelines TEXT is a STATIC CONSTANT in this module — NEVER an
# agents/*.md file. The prompt-artifact baseline (agents/*.md is
# DB-versioned and prompt-sealed) must not move for this feature
# (RK-8), and a static constant keeps the block's content versioned
# with the CODE that renders it.
CONTEXT_KIND_JOINED_ROOM = "joined_room"

#: The room digest's cap — a HARD truncation at 2000 chars: the
#: builder slices the digest and appends an ellipsis once it passes
#: this length, so nothing beyond 2000 survives (the architecture §5
#: "soft" wording does not describe the implemented behavior).
ROOM_DIGEST_MAX_CHARS = 2000
#: The whole joined-room block's hard cap — the DELIVERED-body budget
#: (the design's "4000 hard"). The budget is spent SECTION-AWARE
#: (A-P5-17): every section's mandatory content is reserved FIRST and
#: the elastic content (the digest body, then the room label) takes
#: what is left. A section is never DROPPED.
ROOM_BLOCK_MAX_CHARS = 4000
#: The roster section's cap (architecture §5 — "roster incl. owner
#: ≤600").
ROOM_ROSTER_MAX_CHARS = 600
#: The room LABEL's cap inside the block — the ESCAPED worst case of
#: the creation bound (A-P5-17: 64 raw chars × the 6× expansion of
#: the ``&`` escape). A LEGACY room name (created before the bound) is
#: cut HERE, with the block's visible marker, so it can never crowd
#: out a section (the B-1 defect: an unbounded name voided the
#: ROSTER, then the guidelines).
ROOM_LABEL_MAX_CHARS = 384
#: The room LABEL's floor — the label shrinks before the guidelines /
#: roster / own-name, but never below this (the member must still be
#: able to tell WHICH room the block is about).
ROOM_LABEL_MIN_CHARS = 16
#: The joined-room block's ENVELOPE overhead (the ``[SYSTEM CONTEXT: …]``
#: wrapper + the title). The body budget is reduced by it, so the
#: DELIVERED message (wrapper + body) stays inside
#: ``ROOM_BLOCK_MAX_CHARS`` — the cap is on what the agent actually
#: receives.
_ROOM_BLOCK_WRAPPER_CHARS = (
    len(CONTEXT_PREFIX) + len("Team Room") + len(CONTEXT_SUFFIX)
)
#: How many recent messages the digest carries (architecture §5 — "digest
#: last-3").
ROOM_DIGEST_LAST_N = 3
#: How many characters of a single message body the digest keeps.
ROOM_DIGEST_LINE_MAX_CHARS = 160

#: THE WORKING GUIDELINES — a STATIC CONSTANT, never an agents/*.md file.
#:
#: The text is the COST DISCIPLINE: the team works at milestone
#: granularity, not as a stream of consciousness (RK-3 — push-bloat is
#: the top risk; the digest + the read tool are the pull-first
#: surface). It also carries the warm-join steer (a member joining
#: mid-flight gets oriented by the roster + digest, not by a wall of
#: history) and the addressing convention (``@`` interrupts, ``to:``
#: routes) that §4.4 makes load-bearing.
ROOM_GUIDELINES_TEXT = (
    "How this team works: report MILESTONES, not a stream of "
    "consciousness — one post per milestone, with what changed, what "
    "you need, and what you are doing next. @name INTERRUPTS a "
    "teammate (use it only when the work genuinely needs them now); "
    "to: name ROUTES to them (they see it in their next digest, it "
    "never interrupts). Address teammates by their room name — the "
    "roster below carries your own name so you can spot a message "
    "aimed at you. The full history stays behind room_read; read it "
    "when you need detail, do not paste it back into the room."
)


# ── JOINED-ROOM block (agent group chat P5) ──────────────────────────────
#
# The "system message = guidelines + short digest ONLY; full history
# behind the read tool" ruling (architecture §5). The builder renders
# FOUR things, in this order:
#
#   1. the member's OWN NAME FIRST (Ruling B — self-recognition: the
#      member must be able to spot a message aimed at it);
#   2. the guidelines (the STATIC CONSTANT above — milestone-level
#      messaging, the warm-join steer, the addressing convention);
#   3. the roster, INCLUDING the owner (≤600 chars);
#   4. the digest — last-N messages + the unread count (≤2000 chars,
#      NEVER the full history).
#
# The whole block is capped at 4000 chars (the hard cap). Every DB
# touch is OFF-LOOP (``asyncio.to_thread`` — the v0.27.14 event-loop
# discipline); the builder never raises (a room-store failure degrades
# to NO block, never to a broken turn).


class _JoinedRoomParts(NamedTuple):
    """The FOUR sections of one room's block, BEFORE the budget fit.

    Split out of the renderer so the JOINED builder can size the
    per-room budget across N rooms BEFORE any section is rendered (the
    section-aware fit needs the room count).
    """

    label: str
    own: str
    guidelines: str
    roster_line: str
    digest_header: str
    digest_body: str


def _joined_room_parts(
    *,
    room: dict[str, Any],
    own_name: str,
    roster: list[dict[str, Any]],
    digest_lines: list[str],
    unread: int,
) -> _JoinedRoomParts:
    """Collect the FOUR sections of one room's block (pure).

    The factory contract (``_make_context_message``): untrusted
    content is ESCAPED before it lands in a ``[SYSTEM CONTEXT: …]``
    block — and room/member names ARE agent-supplied free text. The
    same treatment the digest text gets below: embedded newlines are
    flattened first (the module's own digest-text idiom — a name
    must never forge block line structure), then the escape helper
    runs. The helper rewrites the ``&``/``<``/``>`` class; every
    other character round-trips by its documented design (A-P5-15).
    """
    room_label = escape_for_context_block(
        str(room.get("name") or room.get("room_id") or "").replace("\n", " ")
    )
    own_label = escape_for_context_block(
        str(own_name or "").replace("\n", " ")
    )
    roster_bits: list[str] = []
    for member in roster:
        name = member.get("member_name")
        role = member.get("role")
        marker = " (owner)" if role == "owner" else ""
        safe_name = escape_for_context_block(
            str(name or "").replace("\n", " ")
        )
        roster_bits.append(f"{safe_name}{marker}")
    roster_line = (
        "ROSTER: " + ", ".join(roster_bits)
        if roster_bits
        else "ROSTER: (you are alone)"
    )
    if len(roster_line) > ROOM_ROSTER_MAX_CHARS:
        roster_line = roster_line[: ROOM_ROSTER_MAX_CHARS - 3] + "..."
    digest_header = f"RECENT (last {len(digest_lines)} · unread {unread}):"
    digest_body = (
        "\n".join(digest_lines) if digest_lines else "(nothing posted yet)"
    )
    return _JoinedRoomParts(
        label=room_label,
        own=own_label,
        guidelines=ROOM_GUIDELINES_TEXT,
        roster_line=roster_line,
        digest_header=digest_header,
        digest_body=digest_body,
    )


def _render_joined_room_block(parts: _JoinedRoomParts, budget: int) -> str:
    """Render one room's block WITHIN ``budget`` chars.

    The SECTION-AWARE fit (A-P5-17 — the B-1 fix). The four sections
    (the own-name banner / the guidelines / the roster / the digest)
    are NEVER dropped: an oversized input degrades the CONTENT, never
    the STRUCTURE. The elastic content shrinks in a FIXED order — the
    digest body FIRST (the design's "digest ≤2000 soft"), then the
    room label — while the guidelines, the roster and the own name
    NEVER shrink. (The B-1 defect: a blind whole-body slice dropped
    the ROSTER, then the guidelines, leaving the member with a banner
    and nothing else.)
    """

    def _cut(text: str, limit: int) -> str:
        """The block's VISIBLE truncation idiom (``...`` marker).

        A non-positive limit yields the empty string — the caller keeps
        the section's MARKER line, so the section stays readable.
        """
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3] + "..."

    # The room LABEL's own cap first (a legacy name is cut HERE — see
    # ROOM_LABEL_MAX_CHARS), then the budget fit.
    label = _cut(parts.label, ROOM_LABEL_MAX_CHARS)
    # The digest's OWN cap (header + body ≤ ROOM_DIGEST_MAX_CHARS) —
    # the header is the MARKER, so the BODY is what shrinks.
    digest_body = _cut(
        parts.digest_body,
        ROOM_DIGEST_MAX_CHARS - len(parts.digest_header) - 1,
    )
    # The MANDATORY content: the banner's fixed parts + the own name +
    # the guidelines + the roster + the digest's marker line. The
    # elastic content (the label + the digest body) takes what is left.
    fixed = (
        len('[TEAM ROOM] "')
        + len('" — ')
        + len("your name in this room: ")
        + len(parts.own)
        + len(parts.guidelines)
        + len(parts.roster_line)
        + len(parts.digest_header)
        + 4  # the four newlines of the rendered block
    )
    elastic = budget - fixed
    # The room LABEL keeps its FLOOR (the member must still be able to
    # tell WHICH room the block is about) — the digest body yields the
    # space that floor needs (the digest is the elastic section: it
    # shrinks FIRST).
    label_floor = min(len(label), ROOM_LABEL_MIN_CHARS)
    digest_budget = max(elastic - label_floor, 0)
    if len(digest_body) > digest_budget:
        digest_body = _cut(digest_body, digest_budget)
    remaining = elastic - len(digest_body)
    if len(label) > max(remaining, label_floor):
        label = _cut(label, max(remaining, label_floor))
    return (
        f'[TEAM ROOM] "{label}" — '
        f"your name in this room: {parts.own}\n"
        f"{parts.guidelines}\n"
        f"{parts.roster_line}\n"
        f"{parts.digest_header}\n"
        f"{digest_body}"
    )


async def build_joined_room_context_message(
    instance_id: str,
    manager: Any,
) -> HumanMessage | None:
    """Build the JOINED-ROOM block for one instance (or ``None``).

    ``None`` when the instance is not an ACTIVE member of any room (the
    common case — most instances never join a room) or when the room
    substrate is not wired on this daemon.

    The stable id ``joined_room:{instance_id}`` makes ``add_messages``
    SUPERSEDE the previous block in place: exactly ONE joined-room
    block per instance, refreshed every turn (the digest is never
    stale, and the block never accumulates).
    """
    room_service = getattr(manager, "_room_chat_service", None)
    if room_service is None:
        return None
    try:
        memberships = await room_service.list_member_rooms(
            instance_id, active_only=True
        )
    except Exception as exc:  # noqa: BLE001 — never-raise
        logger.warning(
            "[ContextMessages] joined-room membership read failed for "
            f"{instance_id[:8]}...: {exc} — no block"
        )
        return None
    if not memberships:
        return None

    sections: list[_JoinedRoomParts] = []
    for membership in memberships:
        room_id = membership.get("room_id")
        own_name = str(membership.get("member_name") or "")
        try:
            room = await room_service.get_room(room_id)
            if room is None:
                # Purged rooms are NONEXISTENT (R2) — skip silently.
                continue
            roster = await room_service.list_members(
                room_id, caller_instance_id=instance_id, active_only=True
            )
            unread = await room_service.unread_count(
                room_id, caller_instance_id=instance_id
            )
            # The digest is the LAST N rows — read them by the room's
            # denormalized ``message_count`` (bumped in the SAME
            # transaction as every append, so it IS the latest seq:
            # one append = one row = one seq). The counter is what the
            # digest reads instead of a COUNT(*) over the log (the
            # denormalized-counter discipline).
            message_count = int(room.get("message_count") or 0)
            tail_from = max(message_count - ROOM_DIGEST_LAST_N, 0)
            messages = await room_service.read_messages(
                room_id,
                caller_instance_id=instance_id,
                after_seq=tail_from,
                limit=ROOM_DIGEST_LAST_N,
            )
        except Exception as exc:  # noqa: BLE001 — never-raise, per-room isolation
            logger.warning(
                "[ContextMessages] joined-room read failed for room "
                f"{room_id}: {exc} — room skipped"
            )
            continue
        recent = list(messages)[-ROOM_DIGEST_LAST_N:]
        digest_lines: list[str] = []
        for message in recent:
            author = message.get("author_name")
            seq = message.get("seq")
            text = str(message.get("text") or "").replace("\n", " ").strip()
            if len(text) > ROOM_DIGEST_LINE_MAX_CHARS:
                text = text[: ROOM_DIGEST_LINE_MAX_CHARS - 3] + "..."
            # The AUTHOR name is untrusted too (same contract as the
            # text and the roster/banner names above).
            safe_author = escape_for_context_block(
                str(author or "").replace("\n", " ")
            )
            digest_lines.append(
                f"- {safe_author} (seq {seq}): {escape_for_context_block(text)}"
            )
        sections.append(
            _joined_room_parts(
                room=room,
                own_name=own_name,
                roster=roster,
                digest_lines=digest_lines,
                unread=int(unread or 0),
            )
        )

    if not sections:
        return None
    # The SECTION-AWARE budget across the rooms (A-P5-17): the joined
    # body's 4000 chars are split into a per-room share BEFORE any
    # section is rendered, so no room's block is ever cut mid-way (the
    # B-1 defect: the blind whole-body slice dropped the last room's
    # roster, its guidelines and its digest ENTIRELY).
    separator = "\n\n"
    room_count = len(sections)
    per_room_budget = max(
        (
            ROOM_BLOCK_MAX_CHARS
            - _ROOM_BLOCK_WRAPPER_CHARS
            - len(separator) * (room_count - 1)
        )
        // room_count,
        0,
    )
    body = separator.join(
        _render_joined_room_block(section, per_room_budget)
        for section in sections
    )
    if len(body) > ROOM_BLOCK_MAX_CHARS:
        # The MANDATORY content of every room (the guidelines + the
        # roster + the own name) can exceed the hard cap when an
        # instance belongs to MANY rooms (architecture §5: "Multi-room:
        # v1 one room/instance; builder multi-room-capable"). The block
        # then OVERFLOWS rather than dropping a room's orientation
        # content — and the overflow is LOGGED, never silent.
        logger.warning(
            "[ContextMessages] joined-room block exceeded the hard cap "
            f"({len(body)} > {ROOM_BLOCK_MAX_CHARS}) for {room_count} "
            "room(s) — the mandatory per-room content is never dropped"
        )
    return _make_context_message(
        CONTEXT_KIND_JOINED_ROOM,
        "Team Room",
        body,
        id_=_stable_id_for("joined_room", instance_id=instance_id),
        source="joined_room",
    )


__all__ = [
    "CONTEXT_KIND_JOINED_ROOM",
    "ROOM_GUIDELINES_TEXT",
    "ROOM_DIGEST_MAX_CHARS",
    "ROOM_BLOCK_MAX_CHARS",
    "ROOM_ROSTER_MAX_CHARS",
    "ROOM_LABEL_MAX_CHARS",
    "ROOM_LABEL_MIN_CHARS",
    "ROOM_DIGEST_LAST_N",
    "ROOM_DIGEST_LINE_MAX_CHARS",
    "build_joined_room_context_message",
]
