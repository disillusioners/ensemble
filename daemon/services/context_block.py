"""The SHARED ``[SYSTEM CONTEXT: …]`` block primitives.

P6 H2 (2026-11-16) — the structural extraction that took
``daemon/services/context_messages.py`` back under 3000 lines (the
P5 tidier's H2 recommendation). This module owns the THREE primitives
every context builder shares:

* :data:`CONTEXT_PREFIX` / :data:`CONTEXT_SUFFIX` — the block envelope
  (``[SYSTEM CONTEXT: <title>]\n\n<content>``, ADR-4);
* :func:`_make_context_message` — the message factory (ADR-5's
  ``injected_message`` / ``context_kind`` stamping);
* :func:`_stable_id_for` — the deterministic stable-id composer (the
  id-format table of record — D3);
* :func:`escape_for_context_block` — the narrow ``&``/``<``/``>``
  escape for untrusted content (ADR-7's belt-and-braces).

IMPORT COMPATIBILITY: every name is re-exported from
``daemon.services.context_messages`` (the historical import surface),
so ``from daemon.services.context_messages import escape_for_context_block``
(and friends) keeps working — this module is a MOVE, not a fork.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import HumanMessage

# Standard prefix used by every SYSTEM CONTEXT message. The title sits
# between these so downstream consumers (compaction re-append, API
# display) can recognize the boundary line.
CONTEXT_PREFIX = "[SYSTEM CONTEXT: "
CONTEXT_SUFFIX = "]\n\n"


def _make_context_message(
    kind: str,
    title: str,
    content: str,
    id_: str | None = None,
    source: str | None = None,
) -> HumanMessage:
    """Factory for any ``[SYSTEM CONTEXT: …]`` tagged HumanMessage.

    Forces prefix and ``additional_kwargs`` consistency across all builders
    so downstream consumers can rely on them. Per
    ADR-5, every injected context message carries
    ``injected_message=True`` and the ``context_kind`` enum value.

    Args:
        kind: One of the ``CONTEXT_KIND_*`` enum strings.
        title: Human-readable section title (e.g. ``"Related
            Project"``).
        content: Already-formatted body text. ``_make_context_message``
            does NOT escape or trim — callers must run
            :func:`escape_for_context_block` on any untrusted content
            before it lands here.
        id_: Optional stable message id. When ``None`` (the default)
            a fresh ``uuid4`` is minted — identical to the pre-``C0``
            behavior, so every existing caller is unchanged. Callers
            that re-emit a refreshable block pass an explicit stable
            id (via :func:`_stable_id_for`) so LangGraph's
            ``add_messages`` reducer SUPERSEDES the prior checkpoint
            entry in place instead of appending a duplicate.
        source: Optional ``source`` stamp. ``None`` (the default) adds
            NOTHING to ``additional_kwargs`` — the pre-phase-6 shape,
            byte-identical for every existing caller. The memoir
            WAKE-REFRESH block passes ``"memoir_refresh"`` so the
            message's provenance is legible to the attestation scanner's
            exclusion ladder and to graph-side diagnostics (the
            plan §3.2 emission shape).

    Returns:
        A fresh ``HumanMessage`` with the canonical
        ``[SYSTEM CONTEXT: <title>]\\n\\n<content>`` body and the
        identifying ``additional_kwargs``.
    """
    additional_kwargs: dict[str, Any] = {
        "injected_message": True,
        "context_kind": kind,
    }
    if source is not None:
        additional_kwargs["source"] = source
    return HumanMessage(
        content=f"{CONTEXT_PREFIX}{title}{CONTEXT_SUFFIX}{content}",
        id=id_ if id_ is not None else str(uuid.uuid4()),
        additional_kwargs=additional_kwargs,
    )


def _stable_id_for(
    kind: str,
    *,
    instance_id: str | None = None,
    context_key: str | None = None,
    agent_id: str | None = None,
    project_id: str | None = None,
) -> str:
    """Compose the deterministic stable id for a refreshable block.

    Canonical id-format table (decisions.md D3 — kv-ambient-awareness-fix;
    single source of truth — all callers route through this helper so
    the mint site stays grep-able and the formats stay append-only):

    =====================================  ===================================================  =============
    ``kind``                               id format                                            required parts
    ``project``                            ``project:{instance_id}``                            ``instance_id``
    ``shared_meta_kv``                     ``kv:{context_key}``                                 ``context_key``
    ``attestation_nudge``                  ``attestation_nudge:{instance_id}``                  ``instance_id``
    ``attestation_final_report_reminder``  ``attestation_final_report_reminder:{instance_id}``  ``instance_id``
    ``memoir``                             ``memoir:{agent_id}``                                ``agent_id``
    ``memoir_refresh``                     ``memoir_refresh:{instance_id}``                     ``instance_id``
    ``joined_room``                        ``joined_room:{instance_id}``                        ``instance_id``
    ``pok``                                ``pok:{project_id}``                                 ``project_id``
    =====================================  ===================================================  =============

    ``context_key`` is the FULL resolved tree-root partition key — the
    id suffix IS the partition the block content was read from, so
    supersede granularity matches data granularity exactly. Splitting
    the key (e.g. ``context_key.split(':')[-1]``) is a WRONG-ID hazard
    and must never be reintroduced (S19/D3 erratum).

    ``attestation_nudge`` (2026-09-16, incident 6a0d60c9 fix cycle
    FIX-3) mints a stable id per ``instance_id`` for the
    attestation-gate deny nudge so CONSECUTIVE denies on the same
    instance supersede the prior nudge block in place via LangGraph's
    ``add_messages`` reducer. Both deny producers — the plain
    ``decide()`` deny and the marker-path (a)/(d) allow-to-deny
    conversions — funnel through the single nudge construction site in
    ``daemon/graph.py``, so all three mint the SAME id and supersede
    each other.

    ``child_report_check`` kind REMOVED 2026-09-18 (D-CTD-7): the
    producer mint site in ``daemon/services/child_reports.py`` was
    deleted, so this branch has no remaining callers in production
    (tests deleted in ``tests/unit/test_child_terminal_contradiction.py``).
    The ``CONTEXT_KIND_CHILD_REPORT_CHECK`` enum constant is KEPT so
    the resolver-side ``_is_child_report_check_note`` detector can
    still recognize note-shaped messages that older agents may have
    preserved through compaction; that surface is the
    A-signal path the user pinned as untouched.

    The completion-check-note kind REMOVED 2026-09-23 (D-entry
    2026-09-23, incident b2f4dae9): the retired completion-check
    note producer helper in ``daemon/graph.py`` was deleted
    end-to-end; the (b)/(d)-with-pending route resolves to allow
    on the resolver row but emits NO message. The stable-id table
    row was removed too — no per-instance stable id for that kind
    survives anywhere. This docstring deliberately carries NONE of
    the retired identifier literals: the lcancheck whole-tree
    census pins grep live ``daemon/`` code for those literals and
    a descriptive reference stays clean without any witness
    carve-out. Enforcement lives in
    ``tests/unit/test_attestation_lca_note_removed.py`` (the
    negative census pins) plus the lcancheck family pins; at
    runtime a stale caller's kind is named by the
    ``_stable_id_for`` unknown-kind ValueError via its ``repr``.

    Args:
        kind: The block kind (see table above).
        instance_id: Owning instance id (``project`` kind).
        context_key: Full resolved tree-root partition key
            (``shared_meta_kv`` kind).
        agent_id: Agent id. Accepted for signature stability across the
            canonical table; unused by the C0 kinds.

    Returns:
        The deterministic stable id string.

    Raises:
        ValueError: unknown ``kind``, or a required part for the kind
            is missing/empty.
    """
    if kind == "project":
        if not instance_id:
            raise ValueError(
                "_stable_id_for('project') requires instance_id"
            )
        return f"project:{instance_id}"
    if kind == "shared_meta_kv":
        if not context_key:
            raise ValueError(
                "_stable_id_for('shared_meta_kv') requires the FULL "
                "context_key (resolved tree-root partition key)"
            )
        return f"kv:{context_key}"
    if kind == "attestation_nudge":
        if not instance_id:
            raise ValueError(
                "_stable_id_for('attestation_nudge') requires "
                "instance_id"
            )
        return f"attestation_nudge:{instance_id}"
    if kind == "attestation_final_report_reminder":
        # 2026-09-19 (attest-first contract, c5d9a38a remediation):
        # the HOLD-state Final Report Reminder carries the SAME
        # stable-id supersede contract as the existing
        # ``attestation_nudge`` kind. Consecutive HOLD events on the
        # SAME instance collapse to ONE reminder block in the
        # resulting state via LangGraph's ``add_messages`` reducer
        # upsert — the unbounded ``context_kind=task_context`` tail
        # under three-bucket compaction is closed. The cap
        # (``daemon.graph.ATTESTATION_REMINDER_CAP = 2``) prevents
        # the supersede chain from running forever in the
        # degenerate case.
        if not instance_id:
            raise ValueError(
                "_stable_id_for('attestation_final_report_reminder') "
                "requires instance_id"
            )
        return f"attestation_final_report_reminder:{instance_id}"
    if kind == "memoir":
        # Memoir Store v1 (memoir P5, D5.2): the wake briefing block is
        # keyed per AGENT — one memoir block per agent, superseded in
        # place by ``add_messages``. The suffix IS the agent the memory
        # belongs to, so supersede granularity matches data granularity
        # (the same discipline as the ``kv:{context_key}`` suffix).
        if not agent_id:
            raise ValueError(
                "_stable_id_for('memoir') requires agent_id"
            )
        return f"memoir:{agent_id}"
    if kind == "memoir_refresh":
        # Memoir Store v2 (phase 6, T6.4 / V2-D 6.4 / N3): the
        # WAKE-REFRESH block is keyed per INSTANCE. There is NO
        # once-flag — the block re-fires on every qualifying scheduler
        # wake, and successive wakes SUPERSEDE in place to exactly one
        # current row (``add_messages`` stable-id supersede). The suffix
        # is the instance, so two different sessions never collapse into
        # one refresh.
        if not instance_id:
            raise ValueError(
                "_stable_id_for('memoir_refresh') requires instance_id"
            )
        return f"memoir_refresh:{instance_id}"
    if kind == "joined_room":
        # Agent group chat P5 (2026-11-16): the joined-room block is
        # keyed per INSTANCE — one block per member, superseded in place
        # by ``add_messages`` on every turn (the digest is always
        # FRESH; the block never accumulates). The suffix is the
        # instance, so two members of the SAME room get SEPARATE blocks
        # (each carries its own name + roster view + unread count).
        if not instance_id:
            raise ValueError(
                "_stable_id_for('joined_room') requires instance_id"
            )
        return f"joined_room:{instance_id}"
    if kind == "pok":
        # POK Phase 1 (2026-11-07): the operating-knowledge block is
        # keyed per PROJECT — the audience dimension varies the
        # ADMISSION per view, but the block identity is the project
        # (stable id ``pok:{project_id}``; §4.5.1 the ari seam shares
        # this id within its own instance-context namespace). The
        # suffix IS the project the knowledge belongs to, so supersede
        # granularity matches data granularity.
        if not project_id:
            raise ValueError(
                "_stable_id_for('pok') requires project_id"
            )
        return f"pok:{project_id}"
    raise ValueError(
        f"_stable_id_for: unknown kind {kind!r} — C0 mints ids only "
        "for 'project', 'shared_meta_kv', 'attestation_nudge', "
        "'attestation_final_report_reminder', 'memoir', 'memoir_refresh', "
        "and 'pok' blocks"
    )


def escape_for_context_block(content: str) -> str:
    """Escape characters that could close an inner data fence.

    Moved out of :func:`daemon.services.instance_lifecycle._format_shared_context_kv_block`
    (now removed) so the prompt-injection defense survives the move
    to ``[SYSTEM CONTEXT: ...]`` messages. The replacement is
    intentionally narrow:

    * ``&`` → ``\\u0026``
    * ``<`` → ``\\u003c``
    * ``>`` → ``\\u003e``

    These three characters are the only ones that can close an
    XML-style data fence or otherwise inject a redirect into a
    machine boundary. Plain Unicode characters (emoji, non-ASCII
    letters) round-trip untouched so we don't mangle valid content.

    Note: in the new HumanMessages mode there are no XML fences to
    escape (ADR-7 dropped them), but the same character escaping is
    retained as defense-in-depth. The system-prompt-level instruction
    is a separate layer; this helper protects the data body from
    being mis-interpreted downstream.

    Args:
        content: Untrusted text (project JSON, KV metadata, a file
            snippet, etc.).

    Returns:
        The same string with ``&``/``<``/``>`` replaced by their
        ``\\uXXXX`` escape sequences. The original string is
        unchanged (no fences to break, the escape is purely
        belt-and-braces).
    """
    # Replacement is order-independent — the escape sequences
    # ``\u0026`` / ``\u003c`` / ``\u003e`` contain no ``&``, ``<``,
    # # or ``>`` glyphs, so reordering would not change the output.
    # Keeping ``&`` first matches the natural defensive style and
    # the original helper's convention.
    return (
        content
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


__all__ = [
    "CONTEXT_PREFIX",
    "CONTEXT_SUFFIX",
    "escape_for_context_block",
]
