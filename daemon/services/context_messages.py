"""ContextMessageBuilder — pure builder functions for context HumanMessages.

Phase 1 of the Context Injection Restructure plan. This module is a
standalone foundation that produces ``[SYSTEM CONTEXT: ...]`` tagged
``HumanMessage`` instances for all ``CONTEXT_KIND_*`` kinds
(``project``, ``shared_context``, ``auto_load_skills``, ``skills``,
``task_context``, ``blueprint``, ``project_scope_guide``).

The builders are intentionally pure and unit-testable in isolation —
they accept already-fetched data and return either a ``HumanMessage``
or ``None`` when there is no content to emit. Side-effecting concerns
(DB queries, RAG matching, skill search) are isolated inside the
async orchestrator :func:`assemble_context_messages`, which calls
into the existing services (``get_shared_context``,
``SkillInjectionService.inject_skills``, the project / metadata
repositories) and threads the results through the pure builders.

Design follows the plan:

* **ADR-4** — message format
  ``[SYSTEM CONTEXT: <title>]\\n\\n<content>``.
* **ADR-5** — ``additional_kwargs`` carries ``injected_message=True``
  and ``context_kind`` so downstream code (compaction re-append,
  ``GET /messages`` API display) can identify the message.
* **ADR-7** — drop XML fences for the data body, but keep character
  escaping (``&`` / ``<`` / ``>`` → unicode escapes) for embedded
  untrusted content so a malicious KV or note value cannot break the
  context block. The system-prompt-level prompt-injection defense
  instruction lives on the persona side (added in Phase 2).
* **ADR-10** — preserve the ``[System Inject]`` → ``[SYSTEM CONTEXT:
  Skills]`` switch — the old preamble is stripped before the new
  prefix is applied, so the rebuilt message reads cleanly.
* **ADR-11** — KV metadata merges into the same ``[SYSTEM CONTEXT:
  Related Project]`` message instead of a separate appender.
* **ADR-13** — opencode path is OUT OF SCOPE. These builders are
  consumed only by the ensemble ``agent_node`` path (Phase 3).

Opencode single-message merging lives behind
``external_opencode_send_message`` and is intentionally untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import os
from typing import Any

from langchain_core.messages import HumanMessage

from daemon import constants as _constants
from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY, SYSTEM_DEFAULT_PROJECT_NAME
from daemon.config import _resolve_kv_ambient_system_default_enabled
from daemon.loader import estimate_tokens
from .skill_metrics_service import REPLACED_SKILLS_METADATA_KEY

# Phase-2 selector lazy import keeps the module-level surface
# small; the orchestrator itself owns imports of the heavier
# service dependencies it needs.
from .critical_notes_selection_orchestrator import (
    _maybe_tiered_critical_notes,
    install_critical_notes_selection_config,
)

logger = logging.getLogger(__name__)


# ─── Module constants ─────────────────────────────────────────────────────────


# Standard prefix used by every SYSTEM CONTEXT message. The title sits
# between these so downstream consumers (compaction re-append, API
# display) can recognize the boundary line.
CONTEXT_PREFIX = "[SYSTEM CONTEXT: "
CONTEXT_SUFFIX = "]\n\n"


# Context kind enum values — see ADR-5. Stored as plain string
# constants (rather than ``enum.StrEnum``) so ``additional_kwargs``
# values remain JSON-serializable in checkpoint snapshots / API
# responses without an explicit encoder.
CONTEXT_KIND_PROJECT = "project"
CONTEXT_KIND_SHARED_CONTEXT = "shared_context"
CONTEXT_KIND_AUTO_LOAD_SKILLS = "auto_load_skills"
CONTEXT_KIND_SKILLS = "skills"
CONTEXT_KIND_TASK_CONTEXT = "task_context"
CONTEXT_KIND_BLUEPRINT = "blueprint"
CONTEXT_KIND_PROJECT_SCOPE_GUIDE = "project_scope_guide"
# Standalone ambient shared-meta-KV host (kv-ambient-awareness-fix
# C2 / decisions.md D7 — RATIFIED). The system-default project path
# (which substitutes the scope guide for the project JSON dump) has
# no KV renderer of its own; this kind is the durable key downstream
# consumers (FE styling, compaction re-append, ``GET /messages``
# filters) key on — phase3-plan.md Risk 4: consumers filter by
# ``context_kind``, never by index or title.
CONTEXT_KIND_SHARED_META_KV = "shared_meta_kv"
# Hallucination-recovery ladder phase 1 (A-4/T-11): the durable loop
# repair doc ("what was attempted" summary emitted by
# ``daemon/services/symptom_repair_engine.py``). Stamping the doc with
# this kind puts it in the permanently non-selectable / hoisted bucket
# of the compaction three-bucket partition — the doc survives every
# later compaction verbatim (mirrors how compaction docs and system
# context blocks are treated) instead of being summarizable history.
CONTEXT_KIND_SYMPTOM_REPAIR = "symptom_repair"
# Child-terminal contradiction detection CONTEXT_KIND retained (2026-09-18
# user decision, D-CTD-7): the ``child_report_check`` note MESSAGE is
# removed at the mint site, but the context_kind string stays as a
# stable enum value because
# ``daemon/services/attestation_resolver_activation.py::_is_child_report_check_note``
# uses it to detect note-shaped messages that older agents may have
# preserved through compaction (the resolver's A-signal surface is
# untouched — only the producer side is gone). The mint site's
# ``_stable_id_for('child_report_check', ...)`` call is the only
# production caller of the corresponding stable-id branch and is
# deleted with the note, so the branch is removed too (no remaining
# callers; tests for the branch are deleted in
# ``tests/unit/test_child_terminal_contradiction.py``).
CONTEXT_KIND_CHILD_REPORT_CHECK = "child_report_check"
# Agent Snapshot v1 (PR4 — design-exploration §4.3 / R2): the
# warm-start digest block. Stamping the digest with this kind places
# it in the permanently non-selectable / hoisted bucket of the
# compaction three-bucket partition (the hoist is truthy-keyed on any
# ``context_kind`` string — zero compaction changes), so the digest
# survives every later compaction verbatim. Stable message id
# ``snapshot_digest:{instance_id}`` — ``add_messages`` supersedes in
# place (one-digest-per-instance). Written to
# ``instance_metadata["snapshot_digest"]`` by the spawn tool's atomic
# ``set_metadata_many`` (Wave 2b); THIS module only reads/consumes.
CONTEXT_KIND_SNAPSHOT_DIGEST = "snapshot_digest"
_AMBIENT_KV_FRESH: bool | None = None
_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED = False


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _make_context_message(
    kind: str,
    title: str,
    content: str,
    id_: str | None = None,
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

    Returns:
        A fresh ``HumanMessage`` with the canonical
        ``[SYSTEM CONTEXT: <title>]\\n\\n<content>`` body and the
        identifying ``additional_kwargs``.
    """
    return HumanMessage(
        content=f"{CONTEXT_PREFIX}{title}{CONTEXT_SUFFIX}{content}",
        id=id_ if id_ is not None else str(uuid.uuid4()),
        additional_kwargs={"injected_message": True, "context_kind": kind},
    )


# ── Agent Snapshot digest (warm-start) — PR4 read/consume seam ────────────
# The metadata WRITE at spawn is Wave 2b's tool path (atomic
# ``set_metadata_many`` of ``instance_metadata["snapshot_digest"]`` +
# ``spawned_from_snapshot_id``, R6b). THIS seam only reads/consumes:
# on TURN 1, ``assemble_context_messages`` renders the stored digest
# into a ``[SYSTEM CONTEXT: Agent Snapshot Digest]`` block.
#
# Escape-then-cap ordering (developer-verified discipline, design
# §4.3): ``_make_context_message`` does NOT escape — run
# :func:`escape_for_context_block` FIRST (escaping can expand content
# up to ~6×), THEN enforce the D6-ratified hard ceiling of ~25k
# tokens, strictly counted (tiktoken cl100k via
# :func:`daemon.loader.estimate_tokens`), TAIL-truncating with a
# ``snapshot_search``-for-full-body hint. Truncate, never skip — a
# digest that silently doesn't land defeats warm-start. The cap
# bounds INJECTION, not knowledge — the full digest (with
# refs/artifacts) persists in the DB.
SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS = 25_000
# Wrapper that :func:`_make_context_message` prepends to the digest body
# to form the final ``HumanMessage.content``. The cap function subtracts
# this overhead from the body budget so the FINAL assembled message
# (wrapper + body) sits AT OR UNDER the ceiling — Wave 2a pre-step fix
# (the bare-body cap could let the final message exceed the ceiling by
# the wrapper's token cost; observed ~25008 against a 25k ceiling).
_SNAPSHOT_DIGEST_WRAPPER = (
    f"{CONTEXT_PREFIX}Agent Snapshot Digest{CONTEXT_SUFFIX}"
)
_SNAPSHOT_DIGEST_WRAPPER_TOKENS = estimate_tokens(_SNAPSHOT_DIGEST_WRAPPER)
_SNAPSHOT_DIGEST_TRUNCATION_HINT = (
    "\n\n… (digest truncated at the "
    f"{SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS}-token injection "
    "ceiling — use snapshot_search for the full body)"
)


def render_snapshot_digest_body(value: Any) -> str | None:
    """Render the stored ``snapshot_digest`` metadata value to block text.

    Accepts the two shapes the write side may produce:

    * ``dict`` — the digest JSONB (R11 8-tuple + provenance): the
      MANDATORY provenance block renders ATOP the digest (§9
      mitigation d), then the task summary, then the 8 sections.
    * ``str`` — a pre-rendered body (back-compat shape), passed
      through as-is.

    Returns ``None`` for empty/blank values (no block emitted).
    """
    if value is None:
        return None
    if isinstance(value, str):
        body = value.strip()
        return body or None
    if not isinstance(value, dict):
        return None

    lines: list[str] = []
    provenance = value.get("provenance") or {}
    if isinstance(provenance, dict) and provenance:
        lines.append("**Provenance** (a lead, not ground truth — hold "
                     "warm-started conclusions to the same evidence rule "
                     "as cold ones)")
        for key in (
            "source_instance_id",
            "effective_model",
            "prompt_version",
            "captured_at",
        ):
            if provenance.get(key):
                lines.append(f"- {key}: {provenance[key]}")
        for banner in provenance.get("banner") or []:
            lines.append(f"- ⚠ {banner}")
    if value.get("task_summary_text"):
        lines.append("")
        lines.append(str(value["task_summary_text"]))
    for key, header in (
        ("decisions", "Decisions"),
        ("gotchas", "Gotchas"),
        ("conventions", "Conventions"),
        ("open_threads", "Open threads"),
        ("artifact_refs", "Artifact refs"),
        ("worked_vs_wasted", "Worked vs wasted"),
        ("workflow_refinements", "Workflow refinements"),
        ("judgment_calls", "Judgment calls"),
    ):
        entries = value.get(key) or []
        if not entries:
            continue
        lines.append("")
        lines.append(f"## {header}")
        for entry in entries:
            lines.append(f"- {entry}")
    body = "\n".join(lines).strip()
    return body or None


def cap_snapshot_digest_for_injection(
    escaped_body: str,
    *,
    wrapper_overhead_tokens: int | None = None,
) -> str:
    """Enforce the D6 ~25k-token ceiling on the ESCAPED digest body.

    STRICTLY counted (:func:`daemon.loader.estimate_tokens`,
    tiktoken cl100k). TAIL-truncates (the provenance block and the
    steering-ordered decisions live at the head; dropped content is
    the tail) and appends the ``snapshot_search``-for-full-body hint
    — truncate, never skip.

    **Wave 2a pre-step fix (wrapper budget):** the FINAL injected
    message is ``wrapper_prefix + body``. The bare-body cap left the
    final message up to ~``wrapper_tokens`` over the ceiling (the
    observed case hit ~25008 against a 25k ceiling). The fix subtracts
    the wrapper's token cost from the body budget so the FINAL
    assembled message sits AT OR UNDER
    :data:`SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS`.

    The ``wrapper_overhead_tokens`` kwarg defaults to the
    snapshot-digest wrapper constant (:data:`_SNAPSHOT_DIGEST_WRAPPER_TOKENS`)
    and is exposed for unit-test scenarios that need a different
    overhead (or to assert against ``0`` for the legacy bare-body
    contract).

    The FINAL content (wrapper + truncated head + hint) is asserted
    to sit under the ceiling: the injection hook fails loud rather
    than ever landing an over-cap block.
    """
    if wrapper_overhead_tokens is None:
        wrapper_overhead_tokens = _SNAPSHOT_DIGEST_WRAPPER_TOKENS

    if estimate_tokens(escaped_body) <= SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS:
        return escaped_body

    hint = _SNAPSHOT_DIGEST_TRUNCATION_HINT
    hint_tokens = estimate_tokens(hint)
    # Body budget reserves BOTH the wrapper's tokens AND the hint's
    # tokens so wrapper + (head + hint) stays at or under the ceiling.
    body_budget = (
        SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS
        - wrapper_overhead_tokens
        - hint_tokens
    )
    if body_budget < 0:
        # Pathological: wrapper alone exceeds the ceiling. Fail loud
        # rather than silently produce an over-cap block.
        raise AssertionError(
            "snapshot digest wrapper overhead "
            f"({wrapper_overhead_tokens}t) + hint "
            f"({hint_tokens}t) exceeds the "
            f"{SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS}-token ceiling"
        )
    # Binary search the largest head (in chars) whose token count
    # fits the post-hint budget — deterministic, strictly counted.
    lo, hi = 0, len(escaped_body)
    best = escaped_body
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = escaped_body[:mid].rstrip()
        if estimate_tokens(candidate) <= body_budget:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    capped = best + hint
    # FINAL-message ceiling check: wrapper + body ≤ ceiling.
    # Replaces the prior bare-body assert (the Wave 2a fix); the
    # assert was converted to a raise so the guard survives
    # `python -O` (tidier pass).
    final_tokens = wrapper_overhead_tokens + estimate_tokens(capped)
    if final_tokens > SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS:
        raise RuntimeError(
            "snapshot digest injection exceeded the "
            f"{SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS}-token ceiling "
            f"(wrapper={wrapper_overhead_tokens}t + body={estimate_tokens(capped)}t "
            f"= {final_tokens}t)"
        )
    return capped


async def _build_snapshot_digest_message(
    instance_id: str,
    manager: Any,
) -> HumanMessage | None:
    """Build the turn-1 warm-start digest block, or ``None``.

    Reads ``instance_metadata["snapshot_digest"]`` (single DB read;
    a failed read must NOT abort context assembly — swallow and
    skip). Escape FIRST, then cap (see the module-seam comment
    above), then emit via :func:`_make_context_message` with the
    stable id ``snapshot_digest:{instance_id}``.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return None
    try:
        row = await asyncio.to_thread(repo.get, instance_id)
        value = (row.instance_metadata or {}).get("snapshot_digest") if row else None
    except Exception as exc:
        logger.warning(
            f"[ContextMessages] snapshot_digest read failed for "
            f"{instance_id[:8]}...: {exc}"
        )
        return None
    body = render_snapshot_digest_body(value)
    if body is None:
        return None
    escaped = escape_for_context_block(body)
    capped = cap_snapshot_digest_for_injection(escaped)
    return _make_context_message(
        CONTEXT_KIND_SNAPSHOT_DIGEST,
        "Agent Snapshot Digest",
        capped,
        id_=f"snapshot_digest:{instance_id}",
    )


def _stable_id_for(
    kind: str,
    *,
    instance_id: str | None = None,
    context_key: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Compose the deterministic stable id for a refreshable block.

    Canonical id-format table (decisions.md D3 — kv-ambient-awareness-fix;
    single source of truth — all callers route through this helper so
    the mint site stays grep-able and the formats stay append-only):

    ========================  =============================================  =========================
    ``kind``                  id format                                       required parts
    ========================  =============================================  =========================
    ``project``               ``project:{instance_id}``                       ``instance_id``
    ``shared_meta_kv``        ``kv:{context_key}``                            ``context_key``
    ``attestation_nudge``     ``attestation_nudge:{instance_id}``             ``instance_id``
    ========================  =============================================  =========================

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

    ``completion_check_note`` kind REMOVED 2026-09-23 (D-entry
    2026-09-23, incident b2f4dae9): the producer mint site in
    ``daemon/graph.py::_make_completion_check_note_message`` was
    deleted end-to-end; the (b)/(d)-with-pending route resolves to
    allow on the resolver row but emits NO message. The stable-id
    table row was removed too — there is no surviving
    ``completion_check_note:{instance_id}`` consumer. The
    ``_stable_id_for`` kind-list value-error message and this
    docstring entry are the only remaining witnesses; the kind
    literal is now a sentinel for the negative census pin in
    ``tests/unit/test_attestation_lca_note_removed.py``.

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
    raise ValueError(
        f"_stable_id_for: unknown kind {kind!r} — C0 mints ids only "
        "for 'project', 'shared_meta_kv', 'attestation_nudge', and "
        "'attestation_final_report_reminder' blocks"
    )


def _resolve_ambient_kv_fresh() -> bool:
    """Resolve per-turn ambient KV freshness (cached for process lifetime).

    W4 fail-loud contract (council-recommended alignment with Shape A
    ``config._parse_proactive_str``): an unrecognized NON-EMPTY env
    value raises :class:`ValueError` naming the flag and the valid
    vocabulary — an operator typo during an incident must not
    silently keep per-turn refresh ON. The error surfaces at boot via
    the manager-wired :func:`emit_ambient_kv_fresh_boot_log` call
    (``daemon/manager.py``), mirroring Shape A's boot-fail semantics.

    Accepted vocabulary (case-insensitive, whitespace-stripped —
    identical to Shape A's ``_PROACTIVE_FALSE_BOOLS`` /
    ``_PROACTIVE_TRUE_BOOLS``):

    * ``0`` / ``false`` / ``no`` / ``off`` → ``False`` (kill-switch)
    * ``1`` / ``true`` / ``yes`` / ``on`` → ``True``
    * unset / empty / whitespace-only → ``True`` (documented default;
      empty-string-safe so a bare ``KEY=`` line does NOT crash boot)
    """
    global _AMBIENT_KV_FRESH
    if _AMBIENT_KV_FRESH is not None:
        return _AMBIENT_KV_FRESH
    raw = os.environ.get(_constants.ENSEMBLE_AMBIENT_KV_FRESH)
    if raw is None or not raw.strip():
        _AMBIENT_KV_FRESH = True
    else:
        value = raw.strip().lower()
        if value in {"0", "false", "no", "off"}:
            _AMBIENT_KV_FRESH = False
        elif value in {"1", "true", "yes", "on"}:
            _AMBIENT_KV_FRESH = True
        else:
            raise ValueError(
                f"Invalid {_constants.ENSEMBLE_AMBIENT_KV_FRESH} value "
                f"{raw!r} — expected one of 0/false/no/off (disable) "
                f"or 1/true/yes/on (enable); unset/empty defaults to "
                f"enable"
            )
    return _AMBIENT_KV_FRESH


def _reset_ambient_kv_fresh_for_tests() -> None:
    global _AMBIENT_KV_FRESH
    _AMBIENT_KV_FRESH = None


def emit_ambient_kv_fresh_boot_log() -> None:
    global _AMBIENT_KV_FRESH_BOOT_LOG_EMITTED
    if _AMBIENT_KV_FRESH_BOOT_LOG_EMITTED:
        return
    _AMBIENT_KV_FRESH_BOOT_LOG_EMITTED = True
    return logger.info("Ambient KV freshness %s", "ENABLED (per-turn fresh)" if _resolve_ambient_kv_fresh() else "DISABLED (cadence-only legacy: turn-1 snapshot, no refresh)")


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


# ─── Project context builder ──────────────────────────────────────────────────


def _format_relative_time_standalone(created_at: Any) -> str:
    """Lightweight ``get_relative_time``-style helper.

    Mirrors ``_format_relative_time`` in :mod:`daemon.manager` but
    lives here so the builder is self-contained. Returns a short
    human string (``"5 minutes ago"``, ``"2 days ago"``, …) or
    ``"unknown"`` when the timestamp is missing / unparseable.

    Args:
        created_at: Timestamp-like value (``datetime``, ISO ``str``,
            ``None``).

    Returns:
        Compact human-readable relative-time string, or
        ``"unknown"`` when the input is unusable.
    """
    from datetime import datetime, timezone

    if created_at is None:
        return "unknown"

    if isinstance(created_at, str):
        try:
            # Accept ISO-8601 strings (with or without trailing ``Z``).
            raw = created_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return "unknown"
    elif isinstance(created_at, datetime):
        dt = created_at
    else:
        return "unknown"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if seconds < 604800:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    if seconds < 2_592_000:
        weeks = seconds // 604800
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if seconds < 31_536_000:
        months = seconds // 2_592_000
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = seconds // 31_536_000
    return f"{years} year{'s' if years != 1 else ''} ago"


# ── Phase-1 critical-notes render knobs (config-installed; D4) ────────────────
#
# Render-side reference bound (§4.4 #11, defensive backstop only — the
# tool-layer write REJECT is authoritative). Tuned by
# ``config.yaml → critical_notes.reference_max`` via
# :func:`install_critical_notes_render_config` from ``load_config``. NO
# ``ENSEMBLE_*`` env var exists for this (D4: config-layer tuning only).
#
# N5 — Phase-1 budget derivation: with summaries tool-capped at 200
# chars and references render-bounded at 500, the injected notes block
# worst case ≈ store cap 50 × (summary 200 + reference 500 + per-row
# markdown overhead ≈ 30) ≈ **35k chars** (~9k tokens) — down from the
# unbounded ≈100k+ legacy worst case. Phase 2 adds the 12k
# ``section_char_cap`` on top; this phase's bound alone already cuts
# the ceiling roughly 3×.
_CRITICAL_NOTES_DEFAULT_REFERENCE_MAX = 500
_critical_notes_reference_max = _CRITICAL_NOTES_DEFAULT_REFERENCE_MAX

# R19 injected-block ordering: pinned tier first (priority
# critical→high→medium, recency within tier), then the remainder
# (priority→recency). Phase 2 replaces the remainder ordering with
# fusion-score order; the pinned tier ordering stays.
_CRITICAL_NOTES_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2}


def install_critical_notes_render_config(*, reference_max: int | None = None) -> None:
    """Install boot-resolved render knobs (called once from ``load_config``).

    ``None`` leaves the current value untouched (partial yaml block →
    documented default). Mirrors the ``_install_vscode_webview_csp_fix``
    module-cache pattern.
    """
    global _critical_notes_reference_max
    if reference_max is not None:
        _critical_notes_reference_max = int(reference_max)


def reset_critical_notes_render_config() -> None:
    """Restore documented defaults (test isolation helper)."""
    global _critical_notes_reference_max
    _critical_notes_reference_max = _CRITICAL_NOTES_DEFAULT_REFERENCE_MAX


def _resolve_critical_notes_reference_max() -> int:
    """Return the effective render-side reference bound."""
    return _critical_notes_reference_max


def _order_critical_notes_for_injection(notes: list[dict]) -> list[dict]:
    """Order the injected notes block (R19 — injected block ONLY).

    Pinned rows first, then the remainder; within each group priority
    ascending (critical→high→medium) and recency descending (newest
    first) within a priority tier. Two-pass stable sort: recency desc
    first, then a stable priority sort preserves recency inside tiers.
    Unparseable/missing ``created_at`` sorts last within its pass
    (empty string vs ISO strings under ``reverse=True``).
    """
    pinned = [n for n in notes if n.get("pinned")]
    rest = [n for n in notes if not n.get("pinned")]

    def _recency_desc(seq: list[dict]) -> list[dict]:
        return sorted(seq, key=lambda n: (n.get("created_at") or ""), reverse=True)

    def _priority_then_recency(seq: list[dict]) -> list[dict]:
        return sorted(
            _recency_desc(seq),
            key=lambda n: _CRITICAL_NOTES_PRIORITY_RANK.get(n.get("priority", ""), 3),
        )

    return _priority_then_recency(pinned) + _priority_then_recency(rest)


def _format_critical_notes_section(
    critical_notes: list[dict], *, pre_ordered: bool = False
) -> str:
    """Render the critical-notes subsection used by the project builder.

    Phase-1+2 render contract (critical-notes-retrieval, R19 + Phase 2
    tiered selection):

    - SUPERSEDED rows are never injected (``superseded_by_id``
      set OR non-``None`` per the 0b defensive review — empty
      string is also dropped).
    - Ordering is scoped to the INJECTED BLOCK ONLY, and has TWO
      modes (item 10 / spec §4.2 R19 render-order contract):
      ``pre_ordered=True`` (tiered path) renders the input order
      VERBATIM — the orchestrator already emitted pinned first
      (priority sort) then the tail in FUSION-score order, and
      re-sorting here would destroy that fusion order;
      ``pre_ordered=False`` (default, legacy / render-all path)
      applies the R19 re-sort: pinned rows first (priority
      critical→high→medium, recency within tier), then the rest.
    - ``project_cn_list`` output order stays ``created_at`` DESC
      — the tool surface is unchanged.
    - ``reference`` is bounded at the render bound below with a
      truncation suffix. This is the DEFENSIVE backstop only — the
      write-side tool REJECT is authoritative (reject-first
      precedence); truncation exists solely for rows that predate
      or bypass the bound. ``detail_ref`` is NEVER injected
      (list/router reads only).
    - No strike-through / staleness marks here — those render in
      the list/housekeeping surfaces (R19); the injected block
      stays clean.
    - **Phase-2 hint line**: when the selection orchestrator
      appended the ``__hint_drop_count`` sentinel to the last
      surviving row (architecture-recommendation §4.2 — "ALWAYS
      when notes are dropped"), the canonical hint line
      ``(N additional notes not shown — use project_cn_list for
      the full view)`` is emitted at the END of the block. The
      sentinel is consumed by this renderer and stripped from the
      row dict before serialization.

    Args:
        critical_notes: List of dicts with ``priority``, ``category``,
            ``summary``, optional ``reference`` / ``pinned`` /
            ``superseded_by_id`` / ``created_at`` /
            ``__hint_drop_count``. Non-dict entries are silently
            skipped (matches the legacy defensive contract).
        pre_ordered: ``True`` renders the input order verbatim
            (tiered fusion order preserved); ``False`` (default)
            applies the legacy R19 re-sort.

    Returns:
        Markdown subsection text including the leading ``\\n### ⚡
        Critical Notes`` header when there is at least one valid
        entry. Empty string when there is nothing to render.
    """
    if not critical_notes:
        return ""

    priority_icon = {
        "critical": "🔴",
        "high": "🟡",
        "medium": "🟢",
    }

    # Phase-1+2 injected rows: ALL active notes. Tiered tail selection
    # + the 12k section cap are Phase 2 — this phase only bounds
    # references and orders the block.
    #
    # 0b (Phase-2 review conditioning, 2026-09-15): ``is not None``
    # comparison. The previous ``not e.get("superseded_by_id")``
    # truthy-check FALSELY KEPT a row whose ``superseded_by_id`` was
    # an empty string ``""`` (a stray empty pointer bypassed the
    # filter). The strict comparison drops both ``NULL`` and ``""``.
    injected = [
        e for e in critical_notes
        if isinstance(e, dict) and e.get("superseded_by_id") is None
    ]
    if not injected:
        return ""

    rendered: list[str] = ["\n### ⚡ Critical Notes"]
    hint_drop_count: int | None = None
    ordered = (
        injected if pre_ordered
        else _order_critical_notes_for_injection(injected)
    )
    for entry in ordered:
        # Phase-2 sentinel: the orchestrator attaches
        # ``__hint_drop_count`` to the last surviving row when the
        # selection dropped ≥1 row. Consume it here AND strip
        # from the row so the sentinel never renders literally.
        if isinstance(entry, dict):
            sentinel = entry.pop("__hint_drop_count", None)
            if sentinel is not None:
                hint_drop_count = int(sentinel)
        icon = priority_icon.get(entry.get("priority", ""), "⚪")
        category = entry.get("category", "")
        summary = entry.get("summary", "")
        reference = entry.get("reference")
        if isinstance(reference, str) and len(reference) > _resolve_critical_notes_reference_max():
            # Defensive backstop ONLY (§4.4 #11): legacy pre-bound rows
            # or non-tool write paths. The tool layer REJECTS >bound on
            # write — reject-first, truncation is never the alternative.
            reference = (
                reference[:_resolve_critical_notes_reference_max()]
                + "… (truncated — project_cn_list for full text)"
            )
        ref_str = f" *(ref: {reference})*" if reference else ""
        rendered.append(f"- {icon} **[{category}]** {summary}{ref_str}")

    if hint_drop_count and hint_drop_count > 0:
        # Phase-2 hint line (architect §4.2 — "ALWAYS when notes
        # are dropped"). Canonical copy; operators grep for it on
        # clean-window reviews.
        rendered.append(
            f"- …({hint_drop_count} additional notes not shown — use "
            "project_cn_list for the full view)"
        )

    return "\n".join(rendered) + "\n"


def _format_history_section(history_entries: list[dict]) -> str:
    """Render the recent-history subsection used by the project builder.

    Layout: entry-type icon + bracketed type + summary +
    relative-time suffix.

    Args:
        history_entries: List of history dicts with ``entry_type``,
            ``summary``, ``created_at``. Empty list → empty string.

    Returns:
        Markdown subsection text including the leading ``\\n### 📜
        Recent History`` header when there is at least one entry.
        Empty string when there is nothing to render.
    """
    if not history_entries:
        return ""

    entry_type_icons = {
        "milestone": "🏆",
        "commit": "📦",
        "phase": "🔀",
        "bugfix": "🐛",
        "deployment": "🚀",
        "note": "📝",
        "config_change": "⚙️",
        "feature": "✨",
        "other": "❓",
    }

    rendered: list[str] = ["\n### 📜 Recent History"]
    for entry in history_entries:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("entry_type", "other")
        emoji = entry_type_icons.get(entry_type, "❓")
        summary = entry.get("summary", "")
        created_at = entry.get("created_at")
        relative = _format_relative_time_standalone(created_at)
        rendered.append(f"- {emoji} **[{entry_type}]** {summary} — _{relative}_")

    return "\n".join(rendered) + "\n"


def _format_project_json_section(project: Any, critical_notes: list[dict]) -> str:
    """Render the ``## Related Project`` JSON block for the builder.

    Pretty-prints the project dict inside a ``json`` fence with
    the ``critical_notes`` key removed (it's rendered as its own
    formatted subsection below).

    Args:
        project: Object exposing ``to_dict()`` (e.g. a
            :class:`ProjectData` model) or already a plain dict.
        critical_notes: List of critical note dicts already passed to
            the builder — used only to confirm removal of the
            ``critical_notes`` key from the JSON dump.

    Returns:
        Markdown subsection text starting with the ``## Related
        Project`` header followed by a fenced JSON dump, or an empty
        string when ``project`` is ``None``.
    """
    if project is None:
        return ""

    project_dict: Any
    if hasattr(project, "to_dict"):
        project_dict = project.to_dict()
    elif isinstance(project, dict):
        project_dict = project
    else:
        # Fall back to ``vars()`` so an unknown project-like object
        # still renders something useful rather than crashing.
        project_dict = vars(project)

    # ``critical_notes`` is emitted as its own formatted subsection
    # below — drop it from the JSON dump to avoid duplication.
    if isinstance(project_dict, dict) and "critical_notes" in project_dict:
        project_dict = {k: v for k, v in project_dict.items() if k != "critical_notes"}

    try:
        payload = json.dumps(project_dict, indent=2, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        logger.warning(f"[ContextMessages] Failed to serialize project dict: {exc}")
        return ""

    # Escape the JSON body so a project field containing ``<`` / ``>``
    # / ``&`` (e.g. legacy description text with HTML) cannot later
    # be re-interpreted as instructions by a downstream parser.
    payload = escape_for_context_block(payload)

    return "## Related Project\n\n```json\n" + payload + "\n```\n"


def build_project_context_message(
    project: Any,
    critical_notes: list[dict] | None,
    history_entries: list[dict] | None,
    *,
    instance_id: str | None = None,
    notes_pre_ordered: bool = False,
) -> HumanMessage | None:
    """Build the merged ``[SYSTEM CONTEXT: Related Project]`` message.

    One HumanMessage wrapping four pieces of project / project-
    related data (per ADR-11):

    1. Project JSON dump (``to_dict()`` minus ``critical_notes``).
    2. Critical notes (formatted as a markdown list).
    3. Recent project history (formatted as a markdown list).

    The builder is pure: it accepts already-fetched data and returns
    a single ``HumanMessage`` or ``None`` when there is no content
    to emit (no project, no notes, no history). KV metadata is
    rendered separately by :func:`build_shared_meta_kv_message` per
    decisions.md D4 / D7 — this function intentionally no longer
    takes a ``kv_metadata`` parameter.

    R25 (2026-09-15, ADOPTED architect pick): when ``instance_id`` is
    provided, the HumanMessage is minted with the stable
    construction-time id ``project:{instance_id}``. This honors the
    foundational Message-id invariant — id-less persisted HumanMessages
    break FE merge ordering via the moving-checkpoint-ts fallback
    (``persistence.py:527-528``) and drop ``MessageTapSlot``
    metadata. Per-instance determinism is revive-safe (same instance
    → same id → stable merge) and collision-free under first-turn-
    frozen semantics. Callers WITHOUT ``instance_id`` still get a
    uuid4 (legacy callers, internal tests) — the id is not optional
    in production paths.

    Args:
        project: Project model / dict exposing ``to_dict()``. ``None``
            → return ``None`` (nothing to render).
        critical_notes: List of critical-note dicts (each must have
            ``priority`` / ``category`` / ``summary``; ``reference``
            optional). ``None`` is treated as an empty list.
        history_entries: List of recent-history dicts, or ``None`` /
            empty list.
        instance_id: Owning instance id (only consumed for the R25
            stable-id path). Optional because legacy tests / callers
            pass dicts directly without an instance id; production
            callers (``assemble_context_messages``) always provide it.
        notes_pre_ordered: ``True`` when ``critical_notes`` arrives
            pre-ordered from the Phase-2 tiered orchestrator (pinned
            first, then tail in fusion-score order) — the renderer
            then preserves that order verbatim (spec §4.2/R19
            render-order contract). Default ``False`` keeps the
            legacy behavior: the renderer applies its own R19
            priority/recency re-sort (render-all fallback shape).

    Returns:
        Tagged :class:`HumanMessage`` carrying the merged body, or
        ``None`` when every input is empty / ``None``.
    """
    # Fast-path: nothing to render at all.
    has_project = project is not None
    has_notes = bool(critical_notes)
    has_history = bool(history_entries)

    if not (has_project or has_notes or has_history):
        return None

    # Render each subsection. Empty inputs return empty strings; the
    # trailing ``+`` concatenation below skips them cleanly.
    project_section = _format_project_json_section(
        project, critical_notes or []
    )
    notes_section = _format_critical_notes_section(
        critical_notes or [], pre_ordered=notes_pre_ordered,
    )
    history_section = _format_history_section(history_entries or [])

    body = project_section + notes_section + history_section

    if not body.strip():
        # Defensive — every section returned empty text. Mirror the
        # ``None`` return on the fast-path above.
        return None

    # R25 stable id (option (a), ADOPTED): mint ``project:{instance_id}``
    # at construction time so the persisted HumanMessage survives
    # revive-restore without a duplicate append (LangGraph
    # ``add_messages`` reducer SUPERSEDES on matching id). When the
    # caller did not pass ``instance_id`` (legacy internal callers /
    # tests), fall back to the uuid4 default — the construction-time
    # id stays "some stable" but is not per-instance.
    if instance_id:
        message_id = _stable_id_for("project", instance_id=instance_id)
    else:
        message_id = None

    return _make_context_message(
        kind=CONTEXT_KIND_PROJECT,
        title="Related Project",
        content=body,
        id_=message_id,
    )


# ─── Project scope guide (non-scoped mode) ────────────────────────────────────


# Static guide text injected when the instance operates under the system
# default project. ``_make_context_message`` wraps the text with the
# ``[SYSTEM CONTEXT: Project Scope Guide]`` prefix without escaping —
# this is trusted static text, NOT user content, so the nested code
# fences are intentional and safe.
_PROJECT_SCOPE_GUIDE_CONTENT = """\
## Non-Scoped Mode — Project Selection Required

You are currently operating **without a specific project scope**. Many ensemble tools (jobs, queues, project tools, knowledge tools) require a `project_id` to function correctly. Using the wrong project leads to misplaced jobs, lost context, and broken workflows.

### What You Should Do

1. **Identify the correct project** for your task. Use these tools:
   - `project_search(query="...")` — Search projects by name or description
   - `project_list()` — List all available projects

2. **Pass the correct `project_id`** when calling tools that need it (jobs, queues, etc.)

3. **When spawning child instances**, pass the correct `project_id` so they inherit the right scope.

### How to Identify the Right Project

- Match by **name** — the project name usually reflects the codebase or feature area
- Match by **description** — read what the project covers
- Match by **tags** and **shortnames** — useful aliases and categorization
- Match by **main_directory** — the filesystem path tells you which codebase it covers

### Example

If asked to "fix a bug in the ensemble scheduler":
```
project_search(query="scheduler")  → find the matching project
# Use the returned project_id for all subsequent tool calls
```

Do NOT guess or use a default project_id. Always verify the project matches your task scope before proceeding."""


def build_project_scope_guide_message() -> HumanMessage:
    """Build the ``[SYSTEM CONTEXT: Project Scope Guide]`` message.

    Injected when an instance operates under the system default project
    (``__system_default__``) instead of the unhelpful default-project
    JSON dump. The guide teaches the agent how to find and select the
    correct project for its task.

    The content is static guidance text — no arguments needed.

    Returns:
        Tagged :class:`HumanMessage` carrying the scope-guide body.
    """
    return _make_context_message(
        kind=CONTEXT_KIND_PROJECT_SCOPE_GUIDE,
        title="Project Scope Guide",
        content=_PROJECT_SCOPE_GUIDE_CONTENT,
    )


# ─── Shared Meta KV (standalone ambient host, kv-ambient C2) ─────────────────


def build_shared_meta_kv_message(
    kv_metadata: dict[str, Any] | None,
    *,
    stable_id: str | None = None,
) -> HumanMessage | None:
    """Build the ``[SYSTEM CONTEXT: Shared Meta KV]`` message.

    The standalone ambient KV host (kv-ambient-awareness-fix C2;
    decisions.md D7 RATIFIED, extended to all projects by D4). Mirrors
    the KV render path that :func:`build_project_context_message` uses
    for non-default projects, but as a standalone block so the
    system-default project path (which substitutes the scope guide
    instead of the project JSON dump) also surfaces ambient KV.

    Serialization is ``json.dumps(kv_metadata, sort_keys=True,
    indent=2)`` — byte-stable between turns when the data is
    unchanged, so a stable-id re-emit is a zero-cost no-op from the
    reducer's perspective (D7 rationale).

    Degradation contract (all failure modes return ``None`` — the same
    observable outcome as an empty partition; never a truncated or
    malformed block):

    * empty / ``None`` partition → ``None`` (no empty-host noise).
    * non-JSON-serializable value → ``None`` + WARNING (Risk 9;
      mirrors the ``_fetch_kv_metadata`` swallow-and-log posture).
    * serialized payload over the 32k cap → ``None`` + WARNING (W10
      skip-on-overflow — a runaway KV set must never balloon the
      prompt).

    Args:
        kv_metadata: ``{meta_key: meta_value}`` dict for the resolved
            tree-root partition, or ``None`` / empty when the repo
            returned nothing.
        stable_id: Optional deterministic message id (C0
            :func:`_stable_id_for` contract — ``kv:{context_key}``).
            When ``None`` a fresh ``uuid4`` is minted (pre-C0
            behavior). Callers that re-emit a refreshable block MUST
            pass the stable id so ``add_messages`` supersedes in place
            instead of appending (Message-id invariant).

    Returns:
        Tagged :class:`HumanMessage` carrying the escaped JSON body,
        or ``None`` when there is nothing to render.
    """
    if not kv_metadata:
        return None

    try:
        payload = json.dumps(kv_metadata, sort_keys=True, indent=2)
    except (TypeError, ValueError) as exc:
        logger.warning(
            f"[ContextMessages] Failed to serialize shared meta KV "
            f"({len(kv_metadata)} entries): {exc}"
        )
        return None

    # W10 cap (2026-09-08 revision, W2 post-escape fix): bound the
    # ESCAPED body. The cap used to sit on the RAW payload BEFORE
    # ``escape_for_context_block`` — but the escape expands ``&``/``<``/``>``
    # up to 6× (1 char → ``\\uXXXX``), so a 32k-raw payload dense in
    # escapable glyphs could render at ~197k chars. The cap now sits
    # AFTER the escape: on overflow log a WARNING and SKIP the block
    # this turn (same skip-on-overflow outcome, never a truncated
    # output).
    body = escape_for_context_block(payload)
    if len(body) > 32 * 1024:
        logger.warning(
            "[ContextMessages] shared_meta_kv escaped body exceeds 32k "
            "(%d bytes); skipping ambient KV block this turn",
            len(body),
        )
        return None

    return _make_context_message(
        kind=CONTEXT_KIND_SHARED_META_KV,
        title="Shared Meta KV",
        content=body,
        id_=stable_id,
    )


# ─── Shared context (RAG) builder ────────────────────────────────────────────


# Strings returned by ``get_shared_context`` that mean "no usable
# context here" so the builder can short-circuit cleanly.
_NO_CONTEXT_SENTINELS = (
    "There is no context yet.",
    # Fallback for empty / whitespace-only payloads — see
    # ``get_shared_context._empty()`` in ``context_injection.py``.
)


def build_shared_context_message(
    rag_text: str | None,
) -> HumanMessage | None:
    """Build the ``[SYSTEM CONTEXT: Shared Context]`` message.

    Wraps the text returned by
    :func:`daemon.services.context_injection.get_shared_context` — the
    matched-file block plus the Available Context Files index. Per
    ADR-7 the ``<injected_project_context>`` XML fence is dropped on
    this path (the data-instruction boundary is now provided by the
    ``[SYSTEM CONTEXT: ...]`` prefix itself plus a system-level
    prompt-injection defense instruction added in Phase 2).

    Args:
        rag_text: Output of ``get_shared_context(...)`` — formatted
            markdown, or ``None`` when the lookup failed. Empty string
            and the legacy ``"There is no context yet."`` sentinel
            are also treated as "nothing to render".

    Returns:
        Tagged :class:`HumanMessage` carrying the RAG output, or
        ``None`` when the input is empty / unusable.
    """
    if not rag_text:
        return None

    # Defense: ``get_shared_context`` may return a payload that is
    # technically non-empty but contains only whitespace (e.g. a
    # ``_format_injection`` early-exit where the body is just
    # ``"\n"`` separators). Treat those as "no content" so the
    # caller doesn't get a context message whose body is blank.
    if not rag_text.strip():
        return None

    if any(sentinel in rag_text for sentinel in _NO_CONTEXT_SENTINELS):
        # ``get_shared_context`` returns the "There is no context yet."
        # payload when the context dir is missing or the match set
        # is empty. Suppress it in the HumanMessages mode so the
        # agent sees a clean break between context kinds instead of
        # a "no context yet" notice mid-flow.
        return None

    # The RAG output already starts with ``# Shared Context`` /
    # ``context_key: ...`` headers (see ``_format_injection`` in
    # ``context_injection.py``) — leave the body intact so the
    # existing file-index / pre-loaded structure survives the move
    # to HumanMessages.
    body = rag_text.rstrip() + "\n"

    return _make_context_message(
        kind=CONTEXT_KIND_SHARED_CONTEXT,
        title="Shared Context",
        content=body,
    )


# ─── Auto-load skills builder ─────────────────────────────────────────────────


def build_auto_load_skills_message(
    body: str,
    skill_ids: list[str] | None = None,
    instance_id: str | None = None,
    agent_id: str | None = None,
) -> HumanMessage | None:
    """Build the ``[SYSTEM CONTEXT: Auto-Load Skills]`` message.

    Wraps the always-on ``auto_load=True`` skills section as a
    persistent ``[SYSTEM CONTEXT: ...]`` HumanMessage. Unlike
    :func:`build_skills_message` (the BM25-search result), the
    auto-load block is **not** driven by message relevance — it is
    the foundational skill set the agent must see on every task.

    Ordered BEFORE the BM25 skills block so the canonical layout is
    ``project → shared_context → auto_load_skills → skills``. The
    foundational block lands first because an agent (e.g.
    ``developer`` / ``dev-strategy``) reads its always-on planning
    guidance before any relevance-matched skill.

    Stable identity (once-per-instance contract): when ``instance_id``
    and ``agent_id`` are both provided the message id is derived as
    ``"auto_load:{instance_id}:{agent_id}"`` so LangGraph's
    ``add_messages`` reducer REPLACES the slot on every rebuild
    instead of appending a duplicate. A fresh ``uuid4`` is used as a
    fallback for callers that don't pass both ids (keeps backward-
    compatible shape with the other builders at the cost of re-
    accumulation in that path — the orchestrator always passes them).

    Args:
        body: The concatenated skill markdown (each skill's
            ``content`` joined by the caller). Empty ``""`` →
            ``None`` (no message to emit).
        skill_ids: The auto-load skill IDs materialized for this
            instance + project. Stored on
            ``additional_kwargs["auto_load_skill_ids"]`` so the
            messaging path can dedup-merge them into the instance's
            ``last_injected_skill_ids`` metadata at checkpoint time
            (keeping the orchestrator itself free of DB writes — see
            :func:`assemble_context_messages`). The same stable id
            also lets a ``<meta>`` REPLACE sweep drop the stale block
            via :class:`RemoveMessage`.
        instance_id: Instance id for the stable message id.
        agent_id: Agent id for the stable message id.

    Returns:
        Tagged :class:`HumanMessage` carrying the auto-load block,
        or ``None`` when ``body`` is empty / ``None``.
    """
    body = (body or "").strip()
    if not body:
        return None

    if instance_id and agent_id:
        msg_id = f"auto_load:{instance_id}:{agent_id}"
    else:
        msg_id = str(uuid.uuid4())

    kwargs: dict[str, Any] = {
        "injected_message": True,
        "context_kind": CONTEXT_KIND_AUTO_LOAD_SKILLS,
    }
    if skill_ids:
        kwargs["auto_load_skill_ids"] = list(skill_ids)

    return HumanMessage(
        content=f"{CONTEXT_PREFIX}Auto-Load Skills{CONTEXT_SUFFIX}{body}\n",
        id=msg_id,
        additional_kwargs=kwargs,
    )


def auto_load_skills_message_id(instance_id: str, agent_id: str) -> str:
    """Return the stable message id for an instance+agent auto-load block.

    Centralizes the id derivation so the builder (:func:`build_auto_load_skills_message`)
    and the ``<meta>`` REPLACE sweep (:class:`RemoveMessage` emission in
    :mod:`daemon.services.instance_messaging`) reference exactly the same
    slot — the sweep can only drop the block it built.
    """
    return f"auto_load:{instance_id}:{agent_id}"


async def _fetch_auto_load_skills(
    agent_id: str,
    project_id: str | None,
    instance_id: str,
    manager: Any,
    instance_repository: Any,
) -> tuple[list[Any], list[str]]:
    """Fetch ``auto_load=True`` skills agent-scoped for the agent + project.

    Returns ONLY the skills belonging to ``agent_id`` (not the
    project-wide union), so a child agent (e.g. ``coder``) never
    inherits a parent's (e.g. ``developer``) foundational skill —
    preserving the one-skill-per-worker / per-agent auto_load contract.

    Live implementation: the per-turn orchestrator (HumanMessages
    mode) is responsible for auto-load delivery; this helper only
    fetches the agent-scoped auto_load skills, it does not inject
    them into the system prompt:

    1. Clone-on-miss via ``SkillCloneService.ensure_auto_load_skills_sync``,
       which returns THIS agent's materialized skills (cloned from
       ``skill_bank.get_auto_load_by_agent(agent_id)``). The return
       value is used directly — no second ``get_auto_load_skills``
       query, which also closes the cross-agent union gap (the project
       table has no ``agent_id`` column).
    2. Filter out skills explicitly REPLACED via ``<meta>`` tag
       (``explicitly_replaced_ids`` in instance metadata) so REPLACE
       semantics survive the move to HumanMessages (C3 invariant).

    All side-effecting calls (clone, metadata read) are wrapped
    in ``asyncio.to_thread`` (ADR-12) and guarded by ``try/except``
    so a missing skill-evolution stack or transient DB error degrades
    to ``(skills=[], trackable_ids=[])`` — the prompt is assembled
    without an auto-load block rather than crashing a message turn.

    Args:
        agent_id: The resolved base agent id (e.g. ``"developer"``).
        project_id: Project scope. ``None`` / empty → ``([], [])``
            (auto-load is project-scoped).
        instance_id: Instance id for the metadata read.
        instance_repository: Repository exposing ``get(instance_id)``
            with ``instance_metadata`` dict (duck-typed). ``None``
            skips the REPLACE filter.

    Returns:
        ``(skills, trackable_ids)``:

        * ``skills`` — this agent's :class:`Skill` rows to render
          (REPLACE'd ones already excluded).
        * ``trackable_ids`` — stringified skill IDs of ``skills`` for
          the dedup-merge metadata write on the messaging path.
    """
    if not project_id:
        return ([], [])

    clone_service = getattr(manager, "_skill_clone_service", None)
    if clone_service is None:
        # No skill-evolution stack → cannot materialize per-agent
        # auto_load skills. Degrade to "no block".
        return ([], [])

    # Clone-on-miss + return: this agent's cloned skills only.
    try:
        skills_list = await asyncio.to_thread(
            clone_service.ensure_auto_load_skills_sync,
            agent_id=agent_id,
            project_id=project_id,
        )
    except Exception as e:
        logger.warning(
            f"[ContextMessages] Clone-on-miss for auto_load skills "
            f"failed (agent={agent_id}, project={project_id[:8]}...): {e}"
        )
        return ([], [])

    if not skills_list:
        return ([], [])

    # Issue 2 / C3: skip skills explicitly REPLACED via ``<meta>`` tag.
    replaced_ids: set[str] = set()
    if instance_repository is not None:
        try:
            inst = await asyncio.to_thread(
                instance_repository.get, instance_id
            )
        except Exception as exc:
            logger.debug(
                f"[ContextMessages] instance_repository.get for REPLACE "
                f"filter failed ({instance_id[:8]}...): {exc}"
            )
            inst = None
        if inst is not None:
            meta = getattr(inst, "instance_metadata", None) or {}
            raw_replaced = meta.get(REPLACED_SKILLS_METADATA_KEY) or []
            if isinstance(raw_replaced, list):
                replaced_ids = {str(x) for x in raw_replaced if x}

    filtered: list[Any] = []
    trackable: list[str] = []
    for skill in skills_list:
        sid = getattr(skill, "id", None)
        if sid is not None and str(sid) in replaced_ids:
            continue
        filtered.append(skill)
        if sid is not None:
            trackable.append(str(sid))

    return (filtered, trackable)


async def _build_auto_load_block(
    agent_meta: Any,
    instance_id: str,
    project_id: str | None,
    manager: Any,
    instance_repository: Any,
) -> HumanMessage | None:
    """Fetch + render the auto-load skills block for this instance + agent.

    Shared by the first-turn path (``not project_already_injected``) and
    the REPLACE-invalidation path (``auto_load_invalidated``) so the
    build instruction is defined exactly once. Returns the stable-id
    ``[SYSTEM CONTEXT: Auto-Load Skills]`` HumanMessage (filtered by
    ``explicitly_replaced_ids`` inside :func:`_fetch_auto_load_skills`),
    or ``None`` when the agent has no auto-load skills / skill stack /
    non-empty content.

    Args:
        agent_meta: Agent metadata (``id`` drives the agent-scoped fetch).
        instance_id: Instance id (stable block id component).
        project_id: Project scope (``None``/empty → no block).
        manager: :class:`InstanceManager` exposing
            ``_skill_clone_service``.
        instance_repository: Repository for the REPLACE-filter read.

    Returns:
        The auto-load HumanMessage, or ``None``.
    """
    al_agent_id = getattr(agent_meta, "id", None)
    if not al_agent_id:
        return None
    al_skills, al_trackable_ids = await _fetch_auto_load_skills(
        agent_id=al_agent_id,
        project_id=project_id,
        instance_id=instance_id,
        manager=manager,
        instance_repository=instance_repository,
    )
    al_sections: list[str] = []
    for _skill in al_skills:
        _content = (getattr(_skill, "content", "") or "").strip()
        if _content:
            al_sections.append(_content)
    if not al_sections:
        return None
    al_body = "\n\n---\n\n".join(al_sections)
    msg = build_auto_load_skills_message(
        body=al_body,
        skill_ids=al_trackable_ids,
        instance_id=instance_id,
        agent_id=al_agent_id,
    )
    if msg is not None:
        logger.info(
            f"[ContextMessages] Built auto-load skills block "
            f"({len(al_skills)} skill(s)) for "
            f"{instance_id[:8]}... (agent={al_agent_id})"
        )
    return msg


# ─── Skills builder ──────────────────────────────────────────────────────────


# Old prefix emitted by ``SkillInjectionService._format_injection``
# — the builder strips this preamble before applying
# ``[SYSTEM CONTEXT: Skills]`` so the rebuilt message reads cleanly.
_LEGACY_SKILL_PREFIX = "[System Inject] Relevant skills loaded:\n\n"


def build_skills_message(
    injection_text: str | None,
) -> HumanMessage | None:
    """Build the ``[SYSTEM CONTEXT: Skills]`` message.

    Wraps the output of :meth:`SkillInjectionService.inject_skills`
    or :meth:`SkillInjectionService.inject_explicit_skill` after
    replacing the legacy ``[System Inject]`` prefix with
    ``[SYSTEM CONTEXT: Skills]`` so every context kind reads under
    the same prefix family. Body content (skill IDs, scores, full
    markdown, low-match list, ``skill_search`` hint) is preserved
    verbatim — the legacy prefix is the only thing that changes.

    Per ADR-10, the ``<meta skill="…">`` tag carries REPLACE
    semantics; those callers should pass the rendered output through
    this wrapper unchanged.

    Args:
        injection_text: Output of ``SkillInjectionService.inject_*``
            — formatted markdown text. ``None`` or empty string →
            ``None`` (no message to emit).

    Returns:
        Tagged :class:`HumanMessage` carrying the skill block, or
        ``None`` when ``injection_text`` is empty / ``None``.
    """
    if not injection_text:
        return None

    # Strip the legacy ``[System Inject] Relevant skills loaded:\n\n``
    # preamble so the new ``[SYSTEM CONTEXT: Skills]`` prefix is the
    # sole header. ``startswith`` guard prevents accidental mangling
    # if the upstream formatter ever changes the preamble wording.
    body = injection_text
    if body.startswith(_LEGACY_SKILL_PREFIX):
        body = body[len(_LEGACY_SKILL_PREFIX):]

    return _make_context_message(
        kind=CONTEXT_KIND_SKILLS,
        title="Skills",
        content=body.rstrip() + "\n",
    )


# ─── Async orchestrator ──────────────────────────────────────────────────────


def _resolve_tree_root_id(
    instance_id: str,
    parent_id: str | None,
    instance_repository: Any,
) -> str:
    """Resolve the tree-root ``context_key`` for a context lookup.

    Logic:

    * Root instance (``parent_id is None``) → context key is its
      own ``instance_id``.
    * Child instance → ask the instance repository for the tree
      root via ``get_tree_root_id(parent_id)``.
    * Fallback to ``parent_id`` when the repo returns ``None`` so a
      transient repo error never blocks the rebuild.

    Wrapped in a defensive ``try``/``except`` so an unexpected repo
    crash returns the caller's own id instead of bubbling up.

    Args:
        instance_id: The current instance.
        parent_id: The parent instance id, or ``None`` for a root.
        instance_repository: Repository exposing
            ``get_tree_root_id(parent_id)`` (duck-typed).

    Returns:
        The context key string used to look up shared-context files
        and KV metadata.
    """
    if parent_id is None:
        return instance_id

    try:
        root_id = instance_repository.get_tree_root_id(parent_id)
    except Exception as exc:
        logger.warning(
            f"[ContextMessages] get_tree_root_id({parent_id}) "
            f"failed, falling back to parent_id: {exc}"
        )
        return parent_id

    return root_id if root_id is not None else parent_id


def _fetch_kv_metadata(
    context_key: str,
    manager: Any,
) -> dict[str, Any] | None:
    """Read shared-context KV metadata for ``context_key``.

    Pulls ``self._shared_meta_kv_repo`` off the manager
    (duck-typed; matches ``InstanceManager`` /
    ``InstanceLifecycleService``). The repo returns an empty dict
    when nothing is stored, which is the normal happy path.

    Args:
        context_key: The tree-root instance id.
        manager: The :class:`InstanceManager` or compatible object.

    Returns:
        Dict of ``{meta_key: meta_value}`` or ``None`` if no repo
        attached. Any exception is logged + swallowed so a missing
        repo or transient DB error never blocks context rebuild.
    """
    repo = getattr(manager, "_shared_meta_kv_repo", None)
    if repo is None:
        return None

    try:
        kvs = repo.get_all_as_dict(context_key)
    except Exception as exc:
        logger.warning(
            f"[ContextMessages] Failed to read shared context KV "
            f"for {context_key}: {exc}"
        )
        return None

    return kvs or None


def _fetch_project_payload(
    project_id: str | None,
    manager: Any,
) -> tuple[Any, list[dict], list[dict]]:
    """Fetch project + critical notes + recent history in one go.

    Each fetch is best-effort and degrades to an empty / ``None``
    fallback so a single broken repo does not break the whole
    ``[SYSTEM CONTEXT: Related Project]`` rebuild. The
    :func:`build_project_context_message` builder decides what to
    emit based on what came back.

    Args:
        project_id: The active project UUID, or ``None`` to skip
            project lookups entirely.
        manager: The :class:`InstanceManager` exposing
            ``self._project_repository`` (duck-typed).

    Returns:
        Tuple ``(project, critical_notes, history_entries)``. Any
        element may be ``None`` / ``[]`` when the lookup failed or
        the project id was missing.
    """
    if not project_id:
        logger.debug(
            "[ContextMessages] Skipping project context — "
            "project_id is None"
        )
        return (None, [], [])

    project_repo = getattr(manager, "_project_repository", None)
    if project_repo is None:
        return (None, [], [])

    project: Any = None
    critical_notes: list[dict] = []
    history_entries: list[dict] = []

    try:
        project = project_repo.get(project_id)
    except Exception as exc:
        logger.warning(f"[ContextMessages] Failed to load project {project_id}: {exc}")

    if project is not None:
        try:
            notes = project_repo.list_critical_notes(project_id)
            critical_notes = [n.to_dict() for n in notes if hasattr(n, "to_dict")]
        except Exception as exc:
            logger.warning(
                f"[ContextMessages] Failed to list critical notes "
                f"for {project_id}: {exc}"
            )

        try:
            history_entries = project_repo.get_recent_history(project_id, limit=10)
        except Exception as exc:
            logger.warning(
                f"[ContextMessages] Failed to load recent history "
                f"for {project_id}: {exc}"
            )

    return (project, critical_notes, history_entries)


async def _run_skill_search(
    user_query: str,
    project_id: str | None,
    instance_id: str,
    manager: Any,
    message_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """Run ``SkillInjectionService.inject_skills`` with graceful fallback.

    The skill search is async (BM25 → embedding → LLM); the manager's
    service is awaited directly. If the service is missing (e.g.
    older manager init order) or the call raises, we log + return
    ``(None, [])`` so the orchestrator can skip the skills message
    entirely.

    Args:
        user_query: The user message text.
        project_id: Project scope, or ``None`` for global-only.
        instance_id: Receiving instance id (used for A/B routing).
        manager: :class:`InstanceManager` exposing
            ``self._skill_injection_service``.
        message_id: Identifier of the user message the search
            attaches to. ``None`` → fall back to ``instance_id`` so
            legacy call sites keep working and search-result
            caching remains scoped to the instance.

    Returns:
        Tuple ``(injection_text, skill_ids)`` matching the
        underlying ``SkillInjectionService.inject_skills`` contract.
    """
    service = getattr(manager, "_skill_injection_service", None)
    if service is None:
        return (None, [])

    effective_message_id = message_id if message_id is not None else instance_id
    try:
        result = await service.inject_skills(
            user_query,
            project_id=project_id,
            instance_id=instance_id,
            message_id=effective_message_id,
        )
        if not isinstance(result, tuple) or len(result) != 2:
            logger.warning(
                f"[ContextMessages] Skill injector returned malformed "
                f"payload: {type(result).__name__}"
            )
            return (None, [])
        return result
    except Exception as exc:
        logger.warning(f"[ContextMessages] Skill injection failed: {exc}")
        return (None, [])


def _build_blueprint_block_text(matched: list) -> str:
    """Format matched blueprints into injection message text."""
    lines = ["Matched Project Blueprints:"]
    for bp in matched:
        source_tag = "core" if bp.kind == "core" else "matched"
        lines.append(f"✓ {bp.name} (score: {bp.score:.2f}, source: {source_tag})")
    lines.append("")  # blank line
    for bp in matched:
        lines.append(f"--- {bp.name} ---")
        lines.append(bp.content)
        if bp.file_refs:
            lines.append(f"For more detail read: {', '.join(bp.file_refs)}")
        lines.append("")
    return "\n".join(lines)


async def assemble_context_messages(
    instance_id: str,
    user_query: str,
    project_id: str | None,
    agent_meta: Any,
    manager: Any,
    instance_repository: Any,
    parent_id: str | None = None,
    skill_injection_result: tuple[str | None, list[str]] | None = None,
    message_id: str | None = None,
    project_already_injected: bool = False,
    auto_load_invalidated: bool = False,
) -> tuple[list[HumanMessage], list[HumanMessage]]:
    """Async orchestrator returning ``(persistent_msgs, ephemeral_msgs)``.

    Hybrid Context Injection (2026-07-29): project context + shared
    context (heuristic ``.md`` matches) **and** skills are now
    **persistent** — built once on the first user turn and prepended
    to ``graph_input`` so LangGraph's ``add_messages`` reducer
    checkpoints them with the user message. Subsequent turns read
    them straight from ``state['messages']`` for free, preserving
    the LLM prefix-cache and making the skill block visible in the
    message history for debugging.

    The split is still returned as a ``(persistent, ephemeral)``
    tuple so callers can route each part to its own delivery
    surface — but the **ephemeral** half is a documented
    no-op (currently always ``[]``). The pre-refactor
    per-turn ephemeral architecture is kept in place for future use
    (e.g. when explicit per-turn skill lifecycles are introduced).

    Partition rule (ADR-15 — Hybrid split, refactored 2026-07-29):

    * Persistent: ``"project"`` + ``"shared_context"`` + ``"skills"``
      — injected via ``graph_input`` once, then read from checkpoint.
    * Ephemeral: always ``[]`` (architectural code kept but disabled).
      Future versions may re-enable ephemeral injection with explicit
      skill lifecycles (e.g. per-turn debug, ephemeral scratch pads).

    When ``project_already_injected=True`` the orchestrator skips the
    entire project + shared_context build section (no project_repo /
    shared-context / RAG I/O) and only emits skills — preserving the
    per-turn freshness contract for skills while avoiding wasted DB
    work on every turn after the first.

    Skill injection takes two paths (B3 fix, risk register):

    * ``skill_injection_result`` is provided (messaging path
      pre-computed the result, stored it on the manager): reuse
      directly without re-running the search.
    * ``skill_injection_result`` is ``None`` (retry path, no prior
      search ran): fall back to
      :func:`_run_skill_search` so a retry never loses its
      skills just because the first attempt skipped the
      injection step.

    Per-turn freshness guarantee (ADR-2) for the ephemeral part:

    Although the skill ``HumanMessage`` itself is now checkpointed,
    the **search** is still re-run on every turn — a skill
    added/changed mid-session will be picked up by the next call to
    :func:`assemble_context_messages`. The orchestrator is **not** a
    one-shot snapshot captured at graph compile time; every
    invocation performs a live BM25 / embedding search via
    :class:`SkillInjectionService` (unless the caller passed a
    pre-computed ``skill_injection_result``).

    The persistent part is intentionally rebuilt ONCE for
    project + shared-context — its freshness guarantee is replaced
    by the once-per-instance contract enforced via the
    ``project_injected`` flag in instance metadata. Skills, in
    contrast, are searched every turn but the resulting
    HumanMessage becomes part of the persisted state, so a NEW
    skill result on turn 2 is APPENDED to the existing skill
    message in the checkpoint (LangGraph ``add_messages`` reducer
    semantics).

    Args:
        instance_id: The current instance id.
        user_query: The user message text — used for both the RAG
            query and the skill search query (same input drives
            both pipelines today).
        project_id: The active project id, or ``None`` when no
            project is attached.
        agent_meta: :class:`AgentMetadata` providing the
            ``context_injection`` / ``skill_injection`` feature
            flags. Duck-typed; ``getattr`` with ``False``
            default.
        manager: :class:`InstanceManager` exposing
            ``_project_repository``,
            ``_shared_meta_kv_repo``, and
            ``_skill_injection_service``.
        instance_repository: Repository exposing
            ``get_tree_root_id(parent_id)`` for tree-root
            resolution.
        parent_id: Parent instance id, or ``None`` when this is a
            tree-root instance. Mirrors ``append_context_key``.
        skill_injection_result: Optional pre-computed
            ``(injection_text, skill_ids)`` tuple from the
            messaging path. ``None`` → run the search inside
            this orchestrator.
        message_id: Identifier of the user message the skill
            search attaches to. Forwarded to
            :func:`_run_skill_search` so callers in the messaging
            path can attach the search result to the correct
            message rather than the instance. ``None`` → use
            ``instance_id`` as a stable fallback.
        project_already_injected: When ``True`` the project +
            shared_context build is skipped (no DB / RAG work) and
            only skills are emitted. Used by ``ContextSlot`` on
            every turn after the first to honour the once-per-
            instance ``project_injected`` flag. Default ``False``
            for backward-compatible first-turn callers.

    Returns:
        ``(persistent_msgs, ephemeral_msgs)`` tuple.
        ``persistent_msgs`` carries zero-to-three tagged
        :class:`HumanMessage` instances (``[project?, shared_context?,
        skills?]``). ``ephemeral_msgs`` is always an empty list
        (architectural code retained for future use).
    """
    # Lazy imports to keep DB-touching imports out of unit-test
    # import paths where the test mocks the repos directly.
    from .context_injection import get_shared_context

    # Hybrid split — when persistent context was already injected on
    # a previous turn, skip the project + shared_context builders
    # entirely (no DB / RAG I/O) and only emit skills. This is the
    # steady-state hot path: every turn after the first for a given
    # instance pays only the skills-search cost.
    #
    # Skills have been moved into the persistent half (2026-07-29):
    # even on subsequent turns a freshly-found skill is appended to
    # the checkpoint via LangGraph's ``add_messages`` reducer, so the
    # skill block keeps growing turn-over-turn and is visible in
    # message history for debugging.
    if project_already_injected:
        # On turn 2+ the project + shared-context + auto-load blocks
        # are already checkpointed — only the per-turn BM25 skill search
        # rebuilds. Exception: a ``<meta>`` REPLACE recorded this turn
        # (``auto_load_invalidated``) may have changed the auto-load set
        # (``explicitly_replaced_ids``), so re-materialize the FILTERED
        # block under its stable id so ``add_messages`` supersedes the
        # stale one instead of leaving the replaced skill in context
        # (or, with the messaging path's RemoveMessage backstop,
        # dropping all auto-load skills for the session).
        persistent_after_inject: list[HumanMessage] = []

        # Refresh ambient KV on non-retry turns when enabled
        # (D4 composition: non-default trees always refresh;
        # default-project trees refresh only when D3 flag is ON).
        if _resolve_ambient_kv_fresh():
            context_key = _resolve_tree_root_id(instance_id, parent_id, instance_repository)
            kv_enabled = _resolve_kv_ambient_system_default_enabled()
            project = await asyncio.to_thread(_fetch_project_payload, project_id, manager)
            is_default = project[0] is not None and getattr(project[0], "name", None) == SYSTEM_DEFAULT_PROJECT_NAME
            if not is_default or kv_enabled:
                kv_msg = build_shared_meta_kv_message(
                    await asyncio.to_thread(_fetch_kv_metadata, context_key, manager),
                    stable_id=_stable_id_for("shared_meta_kv", context_key=context_key),
                )
                if kv_msg is not None:
                    persistent_after_inject.append(kv_msg)

        if auto_load_invalidated:
            al_msg = await _build_auto_load_block(
                agent_meta=agent_meta,
                instance_id=instance_id,
                project_id=project_id,
                manager=manager,
                instance_repository=instance_repository,
            )
            if al_msg is not None:
                persistent_after_inject.append(al_msg)
        skills_enabled_only = bool(getattr(agent_meta, "skill_injection", False))
        if not skills_enabled_only:
            return (persistent_after_inject, [])
        if skill_injection_result is not None:
            injection_text, _skill_ids = skill_injection_result
        else:
            injection_text, _skill_ids = await _run_skill_search(
                user_query=user_query,
                project_id=project_id,
                instance_id=instance_id,
                manager=manager,
                message_id=message_id,
            )
        skills_msg = build_skills_message(injection_text)
        if skills_msg is None:
            return (persistent_after_inject, [])
        # Skills are now PERSISTENT (checkpointed). The pre-refactor
        # ephemeral path returned ``([], [skills_msg])`` — kept as a
        # comment here for traceability:
        #   return ([], [skills_msg])
        # Future versions may re-enable ephemeral injection with
        # explicit skill lifecycles.
        persistent_after_inject.append(skills_msg)
        return (persistent_after_inject, [])

    persistent_msgs: list[HumanMessage] = []
    ephemeral_msgs: list[HumanMessage] = []

    context_key = _resolve_tree_root_id(instance_id, parent_id, instance_repository)

    # ── 1. Project context message (includes KV metadata) — PERSISTENT ──
    project, critical_notes, history_entries = await asyncio.to_thread(
        _fetch_project_payload, project_id, manager
    )

    # Detect the system default project. ``SYSTEM_DEFAULT_PROJECT_ID``
    # is set at startup (it is ``None`` at import time), so we read it
    # at call time via ``_constants.SYSTEM_DEFAULT_PROJECT_ID``. The
    # name-based check is a robust fallback that also works in unit
    # tests where the ID constant was never set.
    is_system_default = (
        project_id == _constants.SYSTEM_DEFAULT_PROJECT_ID
        or (
            project is not None
            and getattr(project, "name", None) == SYSTEM_DEFAULT_PROJECT_NAME
        )
    )

    # ── Ambient shared-meta-KV gate (kv-ambient-awareness-fix C2) ──
    # Pre-C2 the fetch was skipped unconditionally for system-default
    # instances ("KV metadata is only consumed by
    # build_project_context_message") — which silently dropped the
    # entire ambient KV signal for every default-project tree. The
    # gate now honors the ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
    # kill-switch (Shape A, default ON; =0 restores the legacy
    # skip-the-DB-read behavior byte-for-byte — restart-to-flip).
    kv_ambient_enabled = _resolve_kv_ambient_system_default_enabled()

    kv_metadata: dict[str, Any] | None = None
    if not is_system_default or kv_ambient_enabled:
        kv_metadata = await asyncio.to_thread(
            _fetch_kv_metadata, context_key, manager
        )

    if is_system_default:
        # Inject the scope guide instead of the unhelpful default-project
        # JSON dump.
        project_msg = build_project_scope_guide_message()
        persistent_msgs.append(project_msg)
        # Ambient KV block (flag-gated, default ON) — the STANDALONE
        # host (decisions.md D7), rendered only when the tree-root
        # partition is non-empty (builder returns ``None`` otherwise —
        # no empty-host noise). The scope guide above is untouched:
        # scope-guide content edits are out of scope (phase3-plan
        # Scope). Stable id ``kv:{context_key}`` = the FULL resolved
        # tree-root partition key (D3 canonical table; split-
        # extraction is a WRONG-ID hazard, S19) so a C3 refresh
        # supersedes this entry in place instead of appending.
        if kv_ambient_enabled:
            kv_msg = build_shared_meta_kv_message(
                kv_metadata,
                stable_id=_stable_id_for(
                    "shared_meta_kv", context_key=context_key
                ),
            )
            if kv_msg is not None:
                persistent_msgs.append(kv_msg)
    else:
        # Phase-2 tiered selection (critical-notes-retrieval §4.2):
        # the orchestrator pre-computes the selection here, on the
        # FIRST TURN only — the messaging path's ``project_already_injected``
        # gate (instance_messaging.py:2829-2929) ensures this block
        # never re-runs. The builder receives a pre-shaped
        # ``critical_notes`` list (pinned first, then tail, then a
        # sentinel hint drop count) so the existing
        # ``_format_critical_notes_section`` renderer emits the
        # ``(N additional notes not shown — use project_cn_list
        # for the full view)`` line when notes were dropped.
        # ``notes_pre_ordered`` (item 10 / R19 render-order contract):
        # the tiered path returns the tail in FUSION-score order —
        # the renderer must preserve it, not re-sort. The render-all
        # fallback (``pre_ordered=False``) keeps the legacy R19
        # priority/recency re-sort byte-identically.
        selected_critical_notes, notes_pre_ordered = (
            await _maybe_tiered_critical_notes(
                project_id=project_id,
                active_notes=critical_notes,
                user_query=user_query,
                instance_id=instance_id,
                manager=manager,
            )
        )
        project_msg = build_project_context_message(
            project, selected_critical_notes, history_entries,
            instance_id=instance_id,
            notes_pre_ordered=notes_pre_ordered,
        )
        if project_msg is not None:
            persistent_msgs.append(project_msg)
        if kv_metadata:
            kv_msg = build_shared_meta_kv_message(kv_metadata, stable_id=_stable_id_for("shared_meta_kv", context_key=context_key))
            if kv_msg is not None:
                persistent_msgs.append(kv_msg)

    # ── 2. Shared context (RAG) message — PERSISTENT ──
    # Gate the entire RAG path on ``context_injection.heuristic_match_shared_md_files``
    # so the filesystem read + message build only runs when the agent
    # explicitly opts in. Project + metadata messages above are
    # always built.
    ci = getattr(agent_meta, "context_injection", None)
    heuristic_enabled = bool(
        ci and getattr(ci, "heuristic_match_shared_md_files", False)
    )
    rag_text: str | None = None
    if heuristic_enabled:
        # The RAG call is sync (filesystem + slug token overlap) —
        # wrap in ``asyncio.to_thread`` per ADR-12 so the agent-
        # node async loop doesn't block on disk I/O.
        try:
            rag_text = await asyncio.to_thread(
                get_shared_context,
                context_key,
                user_query,
                "internal",
                project_id=project_id,
            )
        except Exception as exc:
            logger.warning(
                f"[ContextMessages] get_shared_context failed for "
                f"{context_key}: {exc}"
            )
            rag_text = None

        shared_msg = build_shared_context_message(rag_text)
        if shared_msg is not None:
            persistent_msgs.append(shared_msg)

    # ── 3. Auto-load skills message — PERSISTENT (once-per-instance + REPLACE-rebuild) ─
    # Always-on ``auto_load=True`` skills (e.g. ``developer`` /
    # ``dev-strategy``). Unlike the BM25 skills block below, this is NOT
    # driven by message relevance — it is the foundational skill set the
    # agent must see on every task. Independent of the ``skill_injection``
    # boolean so an agent without per-turn search still gets its
    # always-on planning/strategy skill.
    #
    # Build + checkpoint contract: built on the first turn (when
    # ``project_already_injected`` is False) AND rebuilt — filtered by
    # ``explicitly_replaced_ids`` — on a ``<meta>``-REPLACE turn where
    # the messaging path sets ``auto_load_invalidated`` (see the
    # ``project_already_injected`` early-return above). The resulting
    # HumanMessage is prepended to ``graph_input`` by the messaging path
    # and lives in ``state['messages']`` from then on via LangGraph's
    # ``add_messages`` reducer — subsequent turns read it from the
    # checkpoint for free. The stable id (``auto_load:{iid}:{aid}``)
    # means a filtered rebuild SUPERSEDES the stale block instead of
    # appending (and lets the messaging path safely ``RemoveMessage``
    # the old one when the filtered result is empty).
    #
    # The orchestrator itself performs NO metadata writes (the
    # ``last_injected_skill_ids`` dedup-merge is deferred to the
    # messaging path via the ``auto_load_skill_ids`` additional_kwargs),
    # so the ``GET /messages`` read path that also calls this function
    # is structurally read-only.
    if not project_already_injected:
        # First turn: build + checkpoint the auto-load block. (On turn 2+
        # the gate above short-circuits; a REPLACE-invalidation rebuild is
        # handled in the ``project_already_injected`` branch via
        # ``auto_load_invalidated``.)
        al_msg = await _build_auto_load_block(
            agent_meta=agent_meta,
            instance_id=instance_id,
            project_id=project_id,
            manager=manager,
            instance_repository=instance_repository,
        )
        if al_msg is not None:
            persistent_msgs.append(al_msg)

        # ── 3.5. Blueprint message — PERSISTENT (once-per-instance, opt-out) ──
        # Project Blueprint: matched architectural knowledge injected once
        # on the first user turn. Gated by:
        #   (a) project_already_injected must be False (once-per-instance)
        #   (b) project must have opted in (default: false = no injection).
        #       The per-project opt-in lives in ``project_metadata_records``
        #       under ``BLUEPRINT_ACTIVE_METADATA_KEY``; absent = inactive.
        #   (c) blueprint_inactive must be False (opt-out via meta.json)
        #   (d) manager._blueprint_matcher must exist (graceful skip if absent)
        # matcher.match() is async — await DIRECTLY (assemble_context_messages
        # is already async). Do NOT wrap in asyncio.to_thread(asyncio.run(...)).
        #
        # ``get_metadata`` is sync SQLAlchemy — wrap in ``asyncio.to_thread``
        # per ADR-12 so we don't block the agent-node loop on a disk read.
        # A metadata lookup failure must NOT abort the whole context
        # assembly, so we swallow the exception and treat the project as
        # inactive (the safer default).
        project_blueprint_active = False
        project_repo_for_meta = getattr(manager, "_project_repository", None)
        if project_repo_for_meta is not None and project_id:
            try:
                val = await asyncio.to_thread(
                    project_repo_for_meta.get_metadata,
                    project_id,
                    BLUEPRINT_ACTIVE_METADATA_KEY,
                )
                project_blueprint_active = bool(val)
            except Exception:
                project_blueprint_active = False
        blueprint_inactive = bool(getattr(agent_meta, "blueprint_inactive", False))
        if project_blueprint_active and not blueprint_inactive:
            try:
                matcher = getattr(manager, "_blueprint_matcher", None)
                if matcher is None:
                    matched = []
                else:
                    matched = await matcher.match(
                        project_id=project_id,
                        query=user_query,
                    )
            except Exception as exc:
                logger.warning(
                    f"[ContextMessages] Blueprint matching failed for "
                    f"project {project_id}: {exc}"
                )
                matched = []

            if matched:
                blueprint_text = _build_blueprint_block_text(matched)
                persistent_msgs.append(
                    _make_context_message(
                        CONTEXT_KIND_BLUEPRINT,
                        "Project Blueprint",
                        blueprint_text,
                    )
                )

        # ── 3.7. Snapshot digest (warm-start) — PERSISTENT (once-per-instance) ──
        # Agent Snapshot v1 (PR4 read seam): on TURN 1, if the spawn
        # tool stamped ``instance_metadata["snapshot_digest"]``
        # (Wave 2b write path — atomic ``set_metadata_many``), render
        # it into a ``[SYSTEM CONTEXT: Agent Snapshot Digest]``
        # block. Escape FIRST, then the D6 ~25k-token hard ceiling
        # (strictly counted, tail-truncate with a snapshot_search
        # hint — truncate, never skip). Stable id
        # ``snapshot_digest:{instance_id}`` so a re-spawn supersedes
        # in place. The hoisted bucket (``context_kind``-keyed) makes
        # the block survive every later compaction verbatim — zero
        # compaction changes.
        snap_digest_msg = await _build_snapshot_digest_message(
            instance_id=instance_id, manager=manager
        )
        if snap_digest_msg is not None:
            persistent_msgs.append(snap_digest_msg)

    # ── 4. Skills message — PERSISTENT (2026-07-29 refactor) ─────────────
    # Ephemeral skill injection is currently disabled. Skills are
    # persistent (checkpointed) for debugging and improvement. The
    # skill ``HumanMessage`` produced here is prepended to
    # ``graph_input`` by the messaging path so LangGraph's
    # ``add_messages`` reducer appends it to ``state['messages']``
    # alongside the user message — every subsequent turn then reads
    # the skill from the checkpoint via ``list(messages)``, no
    # per-turn rebuild required.
    #
    # Skill injection remains opt-in via the ``skill_injection``
    # boolean — there is no mode gate, so this flag is the sole
    # switch controlling per-turn skill injection.
    #
    # Per-turn freshness is preserved by re-running the BM25 / embedding
    # search on every orchestrator call (not by re-injecting into the
    # LLM-bound ``full_messages``): a new skill result is appended to
    # the checkpoint as a fresh ``HumanMessage``, leaving earlier
    # entries untouched.
    #
    # Future versions may re-enable ephemeral injection with explicit
    # skill lifecycles — see the partition rule in the module docstring
    # and the docstring of :func:`assemble_context_messages`.
    skills_enabled = bool(getattr(agent_meta, "skill_injection", False))
    if skills_enabled:
        if skill_injection_result is not None:
            injection_text, _skill_ids = skill_injection_result
        else:
            injection_text, _skill_ids = await _run_skill_search(
                user_query=user_query,
                project_id=project_id,
                instance_id=instance_id,
                manager=manager,
                message_id=message_id,
            )

        skills_msg = build_skills_message(injection_text)
        if skills_msg is not None:
            # Skills are now PERSISTENT (checkpointed) — prepended to
            # ``graph_input`` by the messaging path, not re-injected
            # into the local ``full_messages`` by ``agent_node``.
            # The pre-refactor ephemeral append is preserved as a
            # comment for traceability:
            #   ephemeral_msgs.append(skills_msg)
            persistent_msgs.append(skills_msg)

    return (persistent_msgs, ephemeral_msgs)


__all__ = [
    # Module constants
    "CONTEXT_PREFIX",
    "CONTEXT_SUFFIX",
    "CONTEXT_KIND_PROJECT",
    "CONTEXT_KIND_SHARED_CONTEXT",
    "CONTEXT_KIND_AUTO_LOAD_SKILLS",
    "CONTEXT_KIND_SKILLS",
    "CONTEXT_KIND_TASK_CONTEXT",
    "CONTEXT_KIND_BLUEPRINT",
    "CONTEXT_KIND_PROJECT_SCOPE_GUIDE",
    "CONTEXT_KIND_SHARED_META_KV",
    "CONTEXT_KIND_SNAPSHOT_DIGEST",
    "SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS",
    # Pure builder functions
    "build_project_context_message",
    "build_project_scope_guide_message",
    "build_shared_meta_kv_message",
    "build_shared_context_message",
    "build_auto_load_skills_message",
    "auto_load_skills_message_id",
    "build_skills_message",
    # Snapshot digest seam (PR4 — read/consume side)
    "render_snapshot_digest_body",
    "cap_snapshot_digest_for_injection",
    # Shared helpers
    "escape_for_context_block",
    # Async orchestrator
    "assemble_context_messages",
]
