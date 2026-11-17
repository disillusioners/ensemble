"""Manager primitive: refresh one instance's prompt (prompt-reload lane).

``refresh_instance_prompt(manager, instance_id, *, force=False)`` performs
the whole validate → compose → compare → pop cycle for ONE instance (the
caller's own). Consumers: the ``prompt_reload`` tool (Phase 2) and,
future/optional, the compaction path (R8 — callable shape only, NOT
wired). The ``InstanceManager.refresh_instance_prompt`` delegate is the
AM-12 pinned skeleton; all logic lives here.

Mechanism (leader ruling R1, probe-validated):

* resolution — registry ``metadata.path`` (spawn parity, F5), NEVER the
  ``current`` symlink; divergence is REPORTED (tri-state), never a
  refusal (AM-5/D2).
* validation — self-env-aware containment (live/sandbox-shaped → under
  ``<install_root>/releases/<v>/agents/``; dev → repo ``agents/``).
* manifest verification — the LOADER'S READ SET for the calling agent
  (AM-1), verified via :mod:`daemon.services.release_manifest` (AM-2)
  against the staged release ``manifest.json``; sandbox is treated
  LIVE-SHAPED (a frozen-binary sandbox runs out of a staged release —
  AM-3).
* composition — ONLY via ``load_and_cache_prompt`` imported through
  ``daemon.manager`` (R2 single source of truth; the house test patch
  seam), with ``mcp_tool_names`` resolved EXACTLY as the restore path
  resolves it (AM-14 parity, private reach W3-authorized below).
* decision — tool-side per-file sha256 + the prompt-versions DB
  fingerprint as a TUPLE against the process-local baseline map
  (AM-8/D5; Rev-3 B1/B4): no baseline → ``unproven`` (force-like);
  files+db equal → ``no_change`` (never pops); db-only change →
  ``reloaded_db`` (F4 honored AT the reload); hashes-differ →
  ``reloaded``. The loader-cache bust is a CORRECTNESS mechanism, not
  only a report flag (N1/D27): whenever the shared cache entry's bytes
  are unprovable — a COLD process (no baseline) holding a live entry,
  or the warm hash/mtime-divergence shape — ``PromptCache.invalidate``
  fires BEFORE composing, because the loader's mtime-only cache-hit
  would otherwise serve the old persona on the NEXT RESTORE too
  (``force=true`` cannot cure it).
* eviction — the probe-validated BARE POP ``manager.instances.pop(id,
  None)`` (P3). NO seam guard (AM-7/D22: the instances dict is unlocked
  across a 12-site mutation census with awaitless critical spans; a
  guard would add a stuck-flag failure mode to a non-defect). NEVER
  the P4 zombie-maker cached-instance release path, NEVER a tuple
  swap (P5), NEVER closure mutation.
* diff — tier-b exact diff over the LOADER-CACHED persona only (never
  the post-cache-appended persona — runtime content is re-appended
  fresh on each restore), fired ONLY under the stored-mtimes claim
  guard (AM-13/D23); otherwise tier-c ``persona_diff=unavailable``
  (AM-10 — the v1 contract; no structural claim).
* concurrency — NO lock (D23); the one subtlety (report truthfulness)
  is the claim guard. Runbook precondition: the primitive must be
  AWAITED on the daemon's main loop (the awaitless-atomicity argument
  collapses off-loop).

Persistent effects (all pinned): ``instances.pop``, ``cache.set`` (on
miss, inside the loader), ``cache.invalidate`` (the AM-8 bust), and the
process-local baseline map write (D5 — bounded at
``_BASELINE_MAX_ENTRIES`` with insertion-order eviction; the runbook
carries the same ledger). Nothing else mutates state.

Refusal vocabulary (AM-6/D6 — FINAL, 9 REFUSED + 1 FAILED): see
:data:`REFUSAL_CODES` / :data:`FAILURE_CODES`. ``not_ari`` is
deliberately ABSENT — the tool's layer-3 non-ari check returns the
notify-exact generic ``ERROR:`` envelope, not a REFUSED code.

Size note: this module is deliberately ONE cohesive unit — the whole
validate → compose → compare → pop primitive plus its pinned
outcome/refusal vocabulary, the S10 disclosure literals, provenance
assembly, the tier-b/tier-c diff, and the frozen INFO line all live
here so the AM-12 manager delegate stays a skeleton and every
pin-anchored surface stays reviewable in a single file. The ~1k-line
size is the floor of that cohesion, not sprawl; splitting would
scatter the pinned contract across modules.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..loader import (
    PROJECT_EXPERIENCE_FILE,
    KNOWLEDGE_FILE,
    KNOWLEDGE_NO_FORCE_EXPLORE_FILE,
    _compute_db_versions_fingerprint,
    _get_prompt_version_repo,
    _resolve_innate_skill_paths,
)
# stdlib-only sibling (zero daemon imports of its own — no cycle): the
# ONE file-sha256 helper and the manifest reader come from here.
from .release_manifest import (
    ManifestError,
    _file_sha256,
    load_release_manifest,
    verify_paths,
)

logger = logging.getLogger(__name__)

#: The synthetic mtime-map key the loader folds the DB fingerprint
#: under (loader.py:1455) — excluded from the file-mtime comparisons.
FP_KEY = "__prompt_versions_fingerprint__"

# ── Outcomes ────────────────────────────────────────────────────────────────
# D25 (leader ruling 2026-11-06, PINNED): the MARKER success set is the
# FIVE tokens below. The INFO-line outcome enum (D24/T3.7) is the SIX-
# value SUPERSET (these five + ``refused``) — DIFFERENT SETS BY DESIGN;
# never reconcile them by shrinking either.
OUTCOME_NO_CHANGE = "no_change"
OUTCOME_RELOADED = "reloaded"
OUTCOME_RELOADED_DB = "reloaded_db"
OUTCOME_FORCED_RELOAD = "forced_reload"
OUTCOME_UNPROVEN = "unproven"
OUTCOME_REFUSED = "refused"  # INFO-line only — never a marker token.

#: The five-token success set (marker vocabulary, D25).
SUCCESS_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_NO_CHANGE,
        OUTCOME_RELOADED,
        OUTCOME_RELOADED_DB,
        OUTCOME_FORCED_RELOAD,
        OUTCOME_UNPROVEN,
    }
)

#: The six-value INFO-line outcome enum (D24) — the superset.
INFO_OUTCOMES: tuple[str, ...] = (
    OUTCOME_NO_CHANGE,
    OUTCOME_RELOADED_DB,
    OUTCOME_RELOADED,
    OUTCOME_FORCED_RELOAD,
    OUTCOME_UNPROVEN,
    OUTCOME_REFUSED,
)

# ── Refusal vocabulary (AM-6/D6 — 14 REFUSED + 1 FAILED, literally
# enumerated, Advisory 1) ────────────────────────────────────────────────────────
ENVELOPE_REFUSED = "REFUSED"
ENVELOPE_FAILED = "FAILED"

CODE_DISABLED = "prompt_reload_disabled"
CODE_AGENT_DIR_UNRESOLVED = "prompt_reload_agent_dir_unresolved"
CODE_DIR_INVALID = "prompt_reload_dir_invalid"
CODE_MANIFEST_MISSING_LIVE = "prompt_reload_manifest_missing_live"
CODE_MANIFEST_UNREADABLE = "prompt_reload_manifest_unreadable"
CODE_MANIFEST_MISMATCH = "prompt_reload_manifest_mismatch"
CODE_AGENT_META_INVALID = "prompt_reload_agent_meta_invalid"
CODE_COMPOSE_FAILED = "prompt_reload_compose_failed"
CODE_DB_UNAVAILABLE = "prompt_reload_db_unavailable"
CODE_INTERNAL_ERROR = "prompt_reload_internal_error"

# ── Compact-note lane codes (the prompt_reload(compact, compact_note) surface)
# ─────────────────────────────────────────────────────────────────────────────────
# Architect §G "Cap + refusal vocabulary" — FIVE new REFUSED codes. The
# ABSENT and WHITESPACE-ONLY note cases share ONE code (forking them buys
# nothing). ``prompt_reload_compact_note_already_armed`` is RETIRED: a
# second note while one is armed REPLACES the first WITH DISCLOSURE (never
# a refusal) — a refusal would strand a mistyped note with no un-arm path.
CODE_NOTE_REQUIRED = "prompt_reload_note_required"
CODE_NOTE_OVER_CAP = "prompt_reload_note_over_cap"
CODE_NOTE_REQUIRES_COMPACT = "prompt_reload_note_requires_compact"
CODE_COMPACT_DISABLED = "prompt_reload_compact_disabled"
CODE_COMPACT_RATE_LIMITED = "prompt_reload_compact_rate_limited"

#: codes 1-14 — the REFUSED envelope (notify_user.py:314-316 shape).
#: 1-9 are the original prompt-refresh ladder; 10-14 are the compact-note
#: lane's five (architect §G). The count is RE-DERIVED by grep, never
#: copied — the census pin in ``tests/unit/tools/test_prompt_reload_refusals.py``
#: asserts 14.
REFUSAL_CODES: frozenset[str] = frozenset(
    {
        CODE_DISABLED,
        CODE_AGENT_DIR_UNRESOLVED,
        CODE_DIR_INVALID,
        CODE_MANIFEST_MISSING_LIVE,
        CODE_MANIFEST_UNREADABLE,
        CODE_MANIFEST_MISMATCH,
        CODE_AGENT_META_INVALID,
        CODE_COMPOSE_FAILED,
        CODE_DB_UNAVAILABLE,
        CODE_NOTE_REQUIRED,
        CODE_NOTE_OVER_CAP,
        CODE_NOTE_REQUIRES_COMPACT,
        CODE_COMPACT_DISABLED,
        CODE_COMPACT_RATE_LIMITED,
    }
)

#: code 10 — the FAILED envelope (notify_user.py:319-321 shape).
FAILURE_CODES: frozenset[str] = frozenset({CODE_INTERNAL_ERROR})

#: Envelope kind per code — the runbook's envelope column.
CODE_ENVELOPES: dict[str, str] = {
    **{code: ENVELOPE_REFUSED for code in REFUSAL_CODES},
    **{code: ENVELOPE_FAILED for code in FAILURE_CODES},
}


# ── Compact-note lane — the ARMED vocabulary ───────────────────────────────────
#
# The compact-note surface (``prompt_reload(compact=True, compact_note=…)``)
# carries its OWN outcome namespace so the prior lane's frozen marker set
# (``no_change``/``reloaded``/``reloaded_db``/``forced_reload``/``unproven``)
# and its six-value INFO enum are NOT extended — the prior lane's pins do
# not move (D25 discipline: the two sets are DIFFERENT BY DESIGN and must
# never be reconciled).

#: The ARMED outcome token (v1.5 §2g). Re-shaped: the note is now a
#: REQUEST the agent's own model executes; the receipt reports what
#: actually happened. There is NO "armed pending seam" (the seam is
#: gone) and NO "three gates" (the floor predicate + threshold bypass
#: are removed).
OUTCOME_ARMED = "armed"

#: The seam statement, in plain words — v1.5: the note is a REQUEST
#: the agent's own model executes on its next invoke (mid-turn append);
#: there is NO quiescent pre-dispatch gate and NO three gates.
ARMED_SEAM_STATEMENT = (
    "your next invoke — the daemon appends the request mid-turn and "
    "your own model writes the compaction"
)

#: The expected-seam clause (architect v1.5 — the seam is gone).
#:
#: Under v1.5 there is no seam and no gates: the append is mid-turn and
#: the same-model invariant is structural. The prior "three gates"
#: (minimum-compaction floor, 60s anti-refire window, deliberate-compact
#: rate limit) are removed; only the 900s deliberate-compact rate limit
#: survives, enforced at ARM time as a typed refusal.
ARMED_EXPECTED_SEAM_CLAUSE = (
    "it is executed by your own model on this turn's invoke — there is "
    "no seam and no gate list; the 900s deliberate-compact rate limit "
    "is enforced at arm time"
)

#: The explicit "not yet" line — MANDATORY in the armed receipt.
ARMED_NOT_YET_COMPACTED_LINE = (
    "CONTEXT NOT YET COMPACTED — the compaction happens at your next "
    "turn boundary; until then this note remains armed."
)

#: The THREE deferred receipt strings (VERBATIM — architect §G / AC10.5).
#: A deferred compaction is NEVER a success receipt for work not done.
ARMED_DEFERRED_ANTI_REFIRE = (
    "armed — deferred (anti-refire window), not compacted"
)
ARMED_DEFERRED_BELOW_FLOOR = (
    "armed — deferred (below minimum-compaction floor), not compacted"
)
ARMED_STILL_ARMED_NO_DOC = (
    "armed — still armed (last compaction produced no doc), not compacted"
)
#: The plain armed status line (nothing deferred yet).
ARMED_PENDING_SEAM = "armed — pending seam, not compacted"

#: The persisted deferred-state discriminators (CONTENT-FREE — a label,
#: never note text). The arm-status surface maps them to the verbatim
#: receipt strings above.
DEFERRED_STATE_NONE = ""
DEFERRED_STATE_ANTI_REFIRE = "anti_refire"
DEFERRED_STATE_BELOW_FLOOR = "below_floor"
DEFERRED_STATE_NO_DOC = "no_doc"

#: Discriminator → verbatim receipt string.
DEFERRED_STATE_TEXT: dict[str, str] = {
    DEFERRED_STATE_ANTI_REFIRE: ARMED_DEFERRED_ANTI_REFIRE,
    DEFERRED_STATE_BELOW_FLOOR: ARMED_DEFERRED_BELOW_FLOOR,
    DEFERRED_STATE_NO_DOC: ARMED_STILL_ARMED_NO_DOC,
}


# ── Compact-note lane — the ARM CARRIER ────────────────────────────────────────
#
# Carrier: ``instances.metadata`` JSONB (architect §D / ES-3). NO migration,
# NO DDL, NO schema change — the column already exists and the generic
# repository ``update`` REJECTS the ``instance_metadata`` kwarg by design,
# so the writes go through the atomic ``set_metadata`` / ``delete_metadata``
# helpers (the ``daemon/services/_escalation_metadata.py`` precedent).
#
# The note is the agent's OWN working memory and can carry intimate
# content: it rides VERBATIM in the RECORD (the checkpoint messages
# channel) and the CONTEXT, and NOWHERE else. Every log line on this
# surface carries the note's LENGTH and sha256 PREFIX only.

#: The verbatim armed note.
COMPACT_NOTE_ARM_KEY = "compact_note_arm"
#: ISO timestamp of the arm.
COMPACT_NOTE_ARMED_AT_KEY = "compact_note_armed_at"
#: sha256 prefix (12 hex chars) of the armed note.
COMPACT_NOTE_SHA256_12_KEY = "compact_note_sha256_12"
#: ISO timestamp of the last CONSUMED (doc-producing) note-guided compaction
#: — the deliberate-compact rate-limit clock.
COMPACT_NOTE_LAST_APPLIED_AT_KEY = "compact_note_last_applied_at"
#: sha256 prefix of the last consumed note.
COMPACT_NOTE_LAST_SHA256_12_KEY = "compact_note_last_sha256_12"
#: ADDITIVE (beyond the architect's five keys): the deferred-state
#: discriminator of the last quiescent seam. The agent must be told
#: "deferred, not compacted" — a deferred state that nobody remembers
#: cannot be disclosed. CONTENT-FREE (a label, never note text).
COMPACT_NOTE_LAST_DEFERRED_KEY = "compact_note_last_deferred"


def compact_note_sha256_12(note: str) -> str:
    """The content-free digest of a note — sha256, first 12 hex chars."""
    return hashlib.sha256(note.encode("utf-8")).hexdigest()[:12]


@dataclass
class CompactNoteState:
    """The carrier's compact-note state, as read for one instance.

    CONTENT-FREE apart from ``note`` itself (the verbatim armed note) —
    which is read from the carrier, never logged.
    """

    armed: bool = False
    note: str = ""
    armed_at: str = ""
    sha256_12: str = ""
    deferred: str = DEFERRED_STATE_NONE
    last_applied_at: str = ""
    last_sha256_12: str = ""


def _read_instance_metadata_dict(instance: Any) -> dict | None:
    """Read the JSONB ``instance_metadata`` column off an ``Instance`` row.

    Mirrors ``daemon/services/_escalation_metadata.py`` — the canonical
    read is ``instance_metadata`` (the SQLAlchemy-mapped column); the
    legacy ``metadata`` fallback exists for test fixtures that carry a
    dict-typed ``metadata`` attribute (on a real row that attribute is the
    SQLAlchemy ``MetaData()`` registry object, NOT a dict).
    """
    md = getattr(instance, "instance_metadata", None)
    if isinstance(md, dict):
        return md
    legacy = getattr(instance, "metadata", None)
    if isinstance(legacy, dict):
        return legacy
    return None


def read_compact_note_state(repository: Any, instance_id: str) -> CompactNoteState:
    """Read the armed-note state for one instance. NEVER raises.

    A missing repository, a missing row, or a DB hiccup yields the EMPTY
    state (never armed) — the arm predicate short-circuits false and the
    no-note path stays byte-identical.
    """
    state = CompactNoteState()
    if repository is None:
        return state
    try:
        row = repository.get(instance_id)
    except Exception:  # pragma: no cover — defensive; never raises
        logger.debug(
            "prompt_reload compact-note: arm read failed for instance=%s",
            str(instance_id)[:8],
        )
        return state
    if row is None:
        return state
    md = _read_instance_metadata_dict(row)
    if not md:
        return state
    note = md.get(COMPACT_NOTE_ARM_KEY)
    if isinstance(note, str) and note.strip():
        state.armed = True
        state.note = note
        state.armed_at = str(md.get(COMPACT_NOTE_ARMED_AT_KEY) or "")
        state.sha256_12 = str(md.get(COMPACT_NOTE_SHA256_12_KEY) or "")
    deferred = md.get(COMPACT_NOTE_LAST_DEFERRED_KEY)
    if isinstance(deferred, str):
        state.deferred = deferred
    state.last_applied_at = str(md.get(COMPACT_NOTE_LAST_APPLIED_AT_KEY) or "")
    state.last_sha256_12 = str(md.get(COMPACT_NOTE_LAST_SHA256_12_KEY) or "")
    return state


def _write_instance_metadata(repository: Any, instance_id: str, updates: dict[str, Any]) -> bool:
    """Write a bundle of metadata keys atomically. NEVER raises.

    Prefers the repository's atomic multi-key helper (``set_metadata_many``
    — ONE dialect-aware UPDATE); falls back to per-key ``set_metadata``
    (also a dialect-aware single-statement UPDATE, so concurrent writers
    targeting different keys compose correctly). The generic ``update``
    method REJECTS ``instance_metadata`` by design and is never used.
    """
    if repository is None:
        return False
    try:
        if hasattr(repository, "set_metadata_many"):
            repository.set_metadata_many(instance_id, updates)
            return True
        if hasattr(repository, "set_metadata"):
            for key, value in updates.items():
                repository.set_metadata(instance_id, key, value)
            return True
    except Exception:  # pragma: no cover — defensive; never raises
        logger.debug(
            "prompt_reload compact-note: metadata write failed for "
            "instance=%s",
            str(instance_id)[:8],
        )
    return False


def arm_compact_note(
    repository: Any,
    instance_id: str,
    note: str,
    *,
    armed_at: str | None = None,
) -> bool:
    """ARM the note on the carrier. Returns ``True`` on a successful write.

    Writes the verbatim note + its digest + the arm timestamp. The note
    body is NEVER logged (R11 — the memoir-lane content-free doctrine and
    the decision-family shadow-lane content-free doctrine are the
    precedent this follows).
    """
    stamp = armed_at or datetime.now(timezone.utc).isoformat()
    ok = _write_instance_metadata(
        repository,
        instance_id,
        {
            COMPACT_NOTE_ARM_KEY: note,
            COMPACT_NOTE_ARMED_AT_KEY: stamp,
            COMPACT_NOTE_SHA256_12_KEY: compact_note_sha256_12(note),
            # A NEW arm starts with NO deferral history — the previous
            # arm's deferred state was already disclosed in the receipt
            # that replaced it.
            COMPACT_NOTE_LAST_DEFERRED_KEY: DEFERRED_STATE_NONE,
        },
    )
    return ok


def clear_compact_note_arm(
    repository: Any,
    instance_id: str,
    *,
    sha256_12: str,
    applied_at: str | None = None,
) -> bool:
    """CONSUME the armed note — clear the arm, stamp the consumption.

    Called AFTER the compaction record has been persisted (two stores, a
    persist-then-delete crash window: at-most-once in steady state,
    at-least-once re-application inside the window — architect §D).

    Also writes the deliberate-compact rate-limit clock
    (``compact_note_last_applied_at``) — the clock counts DELIBERATE
    (note-guided) compactions ONLY.
    """
    stamp = applied_at or datetime.now(timezone.utc).isoformat()
    ok = _write_instance_metadata(
        repository,
        instance_id,
        {
            COMPACT_NOTE_LAST_APPLIED_AT_KEY: stamp,
            COMPACT_NOTE_LAST_SHA256_12_KEY: sha256_12,
            COMPACT_NOTE_LAST_DEFERRED_KEY: DEFERRED_STATE_NONE,
        },
    )
    if not ok:
        return False
    try:
        if hasattr(repository, "delete_metadata"):
            repository.delete_metadata(instance_id, COMPACT_NOTE_ARM_KEY)
        elif hasattr(repository, "delete_metadata_if_present"):
            repository.delete_metadata_if_present(instance_id, COMPACT_NOTE_ARM_KEY)
    except Exception:  # pragma: no cover — defensive; never raises
        logger.debug(
            "prompt_reload_compact_note: arm clear failed for instance=%s",
            str(instance_id)[:8],
        )
        return False
    return True


def mark_compact_note_deferred(
    repository: Any,
    instance_id: str,
    state: str,
) -> bool:
    """Record a DEFERRED seam outcome — the arm PERSISTS.

    The deferred discriminator is content-free (a label). The next armed
    receipt carries the matching VERBATIM deferred string, so a deferral
    is never silently swallowed.
    """
    if state not in DEFERRED_STATE_TEXT:
        return False
    return _write_instance_metadata(
        repository,
        instance_id,
        {COMPACT_NOTE_LAST_DEFERRED_KEY: state},
    )



def settle_compact_note_arm(
    repository: Any,
    instance_id: str,
    *,
    note_sha12: str,
    outcome: str,
    persisted: bool = True,
) -> str:
    """Settle an ARMED note after the APPEND outcome.

    Re-purposed for the append path (architect v1.5 §2d/§2g): the arm is
    consumed (cleared) ONLY when the swap landed — i.e. ``outcome ==
    "compacted"`` AND ``persisted`` is True. Otherwise the arm PERSISTS
    with the matching deferred discriminator recorded, so a crash before
    the settle re-runs rather than half-applies (the receipt must never
    say ``compacted`` before the swap lands).

    ``outcome`` is one of the append path's outcome labels:
    ``"compacted"`` / ``"deferred"`` / ``"append_failed"`` /
    ``"empty_reply"`` (and the refusal labels at arm time).
    """
    if outcome == "compacted" and persisted:
        clear_compact_note_arm(repository, instance_id, sha256_12=note_sha12)
        return "landed"
    mark_compact_note_deferred(repository, instance_id, DEFERRED_STATE_NO_DOC)
    return DEFERRED_STATE_NO_DOC

# ── Canonical disclosure literals (plan-overview §10, W5 — ONE literal
# per bullet id; referenced BY ID everywhere else, never paraphrased) ────────
S10_A_TOOLS_NOTE_DEAD = (
    "tools_note: DB override NOT applied — known defect F7i (the loader "
    "composes the file content under key 'tools' while the override hook "
    "writes 'tools_note'); routed, not fixed here."
)
S10_B_USER_GROWTH_NOT_IN_PROMPT = (
    "user/growth: not part of the composed prompt — F7ii (these artifacts "
    "never enter the loader's prompt map); DB rows are write-only today."
)
S10_C_DISPLAY_DIVERGENCE = (
    "The FE-visible synthetic system message may lag or diverge from the "
    "live persona until the next display rebuild — no display-parity "
    "promise (F8)."
)
S10_D_NEXT_TURN = (
    "The reload takes effect on your NEXT turn; this tool return (and its "
    "marker) is the only in-turn signal."
)
S10_E_PROVIDER_CACHE = (
    "Reloading the persona invalidates provider prompt caches — accepted "
    "consequence (R9)."
)
S10_F_INFLIGHT_SURVIVES = (
    "An in-flight turn survives on its own graph reference; no re-cache "
    "happens at turn end (P1/P3)."
)
S10_G_NODE_SET_HAZARD = (
    "Divergent-node-set handover is only a hazard under code drift; this "
    "path evicts, never swaps mid-turn (P1)."
)
S10_H_FILE_TOCTOU = (
    "File-side TOCTOU on the loader cache is bounded one-call staleness "
    "that self-heals on the next call; DB-side TOCTOU is closed by the "
    "loader's single-SELECT."
)
S10_I_PROBE_TO_COMPOSE = (
    "A prompt-versions repo failure between the tool-side probe and "
    "composition still fail-opens inside the loader (sub-second window; "
    "routed, not fixed here)."
)
S10_J_NO_CHANGE_WORDING = (
    "no_change claims FILE-LEVEL freshness, not closure currency — another "
    "instance sharing this agent's cache may have refreshed it; force=true "
    "is the escape."
)
S10_K_MARKER_ERASURE = (
    "The [PROMPT-RELOADED …] marker survives only in-turn; compaction may "
    "erase it — acceptable, the persona is already live."
)
S10_L_UNVERIFIED_ROUTED = (
    "Runtime proof that no path rewrites a live instance's cached tuple "
    "first element is routed to the tester gate (L1)."
)
S10_M_OBSERVABILITY_ROUTED = (
    "Observability is one frozen INFO line per call; no journal, no "
    "ledger, no metrics family in this lane (prompt_reload_total{outcome} "
    "routed to a future metrics lane)."
)
S10_N_COLD_START_DELIBERATE = (
    "Cold-start behavior is DELIBERATE: with no prior baseline record "
    "(first call after ANY restart) the outcome is unproven and the "
    "primitive behaves force-like (recompose + pop). It NEVER returns "
    "no_change without a baseline — by design, not a defect."
)

#: The two DISTINCT fixed divergence disclosure lines (AM-5 — one for
#: ``false``, one for ``unknown``; pinned separately, never collapsed).
DIVERGENCE_LINE_FALSE = (
    "current symlink does not match the running release — the daemon "
    "resolves prompts from the RUNNING release (registry, spawn parity); "
    "operator action (re-point or restart) resolves this, the reload "
    "proceeds."
)
DIVERGENCE_LINE_UNKNOWN = (
    "current-vs-running release could not be determined (no install dir, "
    "no symlink, or a dangling link in this environment) — the reload "
    "proceeds; prompt resolution follows the registry (spawn parity)."
)

#: The in-turn breadcrumb marker (AM-4/D10) — rendered by the TOOL on
#: success outcomes only; token == the structured outcome (D25).
PROMPT_RELOADED_MARKER = "[PROMPT-RELOADED outcome={outcome} next_turn=true]"

# ── The loader's own artifact list (frozen constant; pinned equal to the
# loader's literal by the phase-4 pack — a hand-written DIVERGENT glob is
# the AM-1 test-failure class) ───────────────────────────────────────────────
_OWN_PROMPT_FILES: tuple[str, ...] = (
    "soul.md",
    "skill.md",
    "workflow.md",
    "rule.md",
    "memory.md",
)

#: The three SHARED ``_prompt_system/`` basenames the loader reads. The
#: loader keys them by BASENAME in its mtime map (``loader.py:1374``,
#: ``:1391``); the release manifest lists them under ``_prompt_system/``.
#: Derived from the loader's own path constants so a constant change can
#: never silently drift this mapping (C1: keying them as ``<agent>/<name>``
#: made them unmanifested → SKIP → the two shared persona files were
#: NEVER verified).
_SHARED_PROMPT_BASENAMES: frozenset[str] = frozenset(
    {
        PROJECT_EXPERIENCE_FILE.name,
        KNOWLEDGE_FILE.name,
        KNOWLEDGE_NO_FORCE_EXPLORE_FILE.name,
    }
)

# ── The process-local baseline map (D5) ─────────────────────────────────────
# Key: (agent_id, version_tag, tuple(sorted(mcp_tool_names))).
# Value: (read-set file sha256 map, db fingerprint operand).
# Written ONLY by this primitive (Advisory 2: a FORCED pop also writes it —
# every successful compose/fill seeds it, else post-force calls would forever
# return unproven). A2 (leader ruling 2026-11-06): this is the FOURTH
# persistent effect (documented in the runbook + docstring); it is BOUNDED —
# insertion-order eviction past _BASELINE_MAX_ENTRIES (max ~256 small
# entries) so the process map can never grow without limit.
_BASELINE_MAP: dict[tuple[str | None, str | None, tuple[str, ...]], tuple[dict[str, str], str]] = {}
_BASELINE_MAX_ENTRIES = 256


def _record_baseline(
    key: tuple[str | None, str | None, tuple[str, ...]],
    hashes: dict[str, str],
    db_fingerprint: str,
) -> None:
    """Write one baseline record, evicting the OLDEST past the size bound.

    FIFO, not LRU (N4): re-recording an existing key does NOT promote it;
    eviction is graceful because a lost record only yields ``unproven`` on
    the next call (which recomposes + pops).

    INVARIANT (N1.4): ``hashes`` MUST be the same pre-compose snapshot
    that fed the decision tuple — the record must always describe the
    inputs of the compose that produced the persona actually served.
    Never pass a hashes map sourced from a separate/later disk read.
    """
    _BASELINE_MAP[key] = (hashes, db_fingerprint)
    while len(_BASELINE_MAP) > _BASELINE_MAX_ENTRIES:
        _BASELINE_MAP.pop(next(iter(_BASELINE_MAP)), None)


@dataclass
class PromptRefreshResult:
    """The structured result — success report or typed refusal.

    ``outcome`` is the SIX-value enum (INFO-line vocabulary): the five
    success tokens (D25 marker set) or ``refused`` when ``code`` is set.
    The marker is rendered ONLY for the five success tokens (code None).
    """

    outcome: str
    code: str | None = None
    message: str = ""
    #: tier-b: persona_diff="exact" + counts + heading sets; tier-c:
    #: persona_diff="unavailable" and nothing else claimed.
    diff: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code is None


def _refused(code: str, message: str, provenance: dict[str, Any]) -> PromptRefreshResult:
    return PromptRefreshResult(
        outcome=OUTCOME_REFUSED, code=code, message=message, provenance=provenance
    )


# ── Self-env / release provenance (T1.2, AM-5) ──────────────────────────────

_RELEASE_NAME_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _release_provenance() -> dict[str, Any]:
    """Tri-state current-vs-running provenance (NEVER refuses — AM-5).

    W3-authorized private reach (leader ruling): the lazy import of
    ``daemon.tools.upgrade_tools`` privates lives ONLY in this module,
    copied from the ``promote_txn_hold.py:145-182`` pattern. Do NOT
    reimplement these resolvers and do NOT hoist the import to module
    level (upgrade_tools is 4k+ lines of upgrade-lane scope; in-function
    import dodges the import cycle).
    """
    from ..tools.upgrade_tools import (
        _current_symlink,
        _resolve_install_dir,
        _self_env_marker,
    )

    self_env = _self_env_marker()
    install_root = _resolve_install_dir(self_env) if self_env else None
    current_target, current_note = _current_symlink(install_root)

    running_release: str | None = None
    if getattr(sys, "frozen", False):
        exe_parent = Path(sys.executable).parent.name
        if _RELEASE_NAME_RE.match(exe_parent):
            running_release = exe_parent

    if current_note or current_target is None or running_release is None:
        matches = "unknown"
        divergence_line = DIVERGENCE_LINE_UNKNOWN
    else:
        target_name = current_target.rsplit("/", 1)[-1]
        matches = "true" if target_name == running_release else "false"
        divergence_line = DIVERGENCE_LINE_FALSE if matches == "false" else ""

    try:
        from .. import __version__ as daemon_version
    except Exception:  # pragma: no cover — defensive; daemon always versions
        daemon_version = "?"

    return {
        "self_env": self_env,
        "install_root": str(install_root) if install_root else None,
        "running_release": running_release,
        "current_target": current_target,  # null-not-empty when unresolved
        "current_matches_running": matches,
        "divergence_line": divergence_line,
        "daemon_version": daemon_version,
    }


# ── The loader's read set (AM-1 — derived by IMPORTING the loader's own
# resolvers; a hand-written glob is a test failure) ──────────────────────────


def _loader_read_set(agent_dir: Path, meta: dict[str, Any] | None) -> dict[str, Path]:
    """Every file the loader reads for this agent, keyed by the loader's
    own mtime-map key convention (so hashes and mtime comparisons line
    up with ``load_and_cache_prompt`` exactly).

    Mirrors the loader's derivation: own artifacts (:1369), meta.json,
    shared ``_prompt_system`` files (constants :449-451, selected via
    ``no_force_explore``), innate-skill files
    (``_resolve_innate_skill_paths`` :880-886), and ``memories/*.md``
    (:1423-1425). Deliberately NOT added: any file the loader does not
    read (Advisory 3 — e.g. ``_prompt_system/critical-notes.md``).
    """
    read_set: dict[str, Path] = {}
    for filename in _OWN_PROMPT_FILES:
        path = agent_dir / filename
        if path.exists():
            read_set[filename] = path
    tools_note = agent_dir / "tools_note.md"
    tools_fallback = agent_dir / "tools.md"
    if tools_note.exists():
        read_set["tools_note.md"] = tools_note
    elif tools_fallback.exists():
        read_set["tools.md"] = tools_fallback
    meta_path = agent_dir / "meta.json"
    if meta_path.exists():
        read_set["meta.json"] = meta_path
    if PROJECT_EXPERIENCE_FILE.exists():
        read_set[PROJECT_EXPERIENCE_FILE.name] = PROJECT_EXPERIENCE_FILE
    no_force_explore = bool(meta and meta.get("no_force_explore"))
    knowledge_file = KNOWLEDGE_NO_FORCE_EXPLORE_FILE if no_force_explore else KNOWLEDGE_FILE
    if knowledge_file.exists():
        read_set[knowledge_file.name] = knowledge_file
    if meta and meta.get("innate_skills"):
        for _skill_name, skill_file in _resolve_innate_skill_paths(agent_dir, meta):
            if skill_file.exists():
                read_set[f"_prompt_system/innate-skills/{_skill_name}/skill.md"] = skill_file
    else:
        skills_dir = agent_dir / "skills"
        if skills_dir.exists() and skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir(), key=lambda p: p.name):
                skill_file = skill_dir / "skill.md"
                if skill_dir.is_dir() and skill_file.exists():
                    read_set[f"skills/{skill_dir.name}/skill.md"] = skill_file
    memories_dir = agent_dir / "memories"
    if memories_dir.exists() and memories_dir.is_dir():
        for memory_file in memories_dir.iterdir():
            if memory_file.is_file() and memory_file.suffix == ".md":
                read_set[f"memories/{memory_file.name}"] = memory_file
    return read_set


def _manifest_relpath(agent_dir_name: str, key: str) -> str:
    """Map a loader mtime key to its ``agents/``-relative manifest path.

    C1 fix (leader ruling 2026-11-06): the three SHARED persona files are
    keyed by BASENAME in the loader's mtime map but live under
    ``_prompt_system/`` in the manifest — map them explicitly. The
    ``read_set`` keys stay UNTOUCHED (renaming them would break the
    loader-mtime parity in the stale-cache bust). Shared
    ``_prompt_system/`` keys (innate skills) are already
    agents/-relative; per-agent keys (artifacts, tools_note, memories)
    are prefixed with the agent dir name.
    """
    if key in _SHARED_PROMPT_BASENAMES:
        return f"_prompt_system/{key}"
    if key.startswith("_prompt_system/"):
        return key
    return f"{agent_dir_name}/{key}"


# ── AM-14 mcp_tool_names parity (private reach W3-authorized) ───────────────


def _resolve_mcp_tool_names(
    manager: Any, instance_id: str, stored_mcp: list[str] | None
) -> tuple[list[str], str]:
    """Resolve ``mcp_tool_names`` EXACTLY as the restore path does.

    W3 ruling: the private reach (``manager._lifecycle_service`` +
    stored ``instance_metadata`` + ``_get_mcp_tool_names`` at
    instance_lifecycle.py:1487) is ACCEPTABLE but ONLY inside this
    module, with this comment. Do NOT reimplement it and do NOT widen
    the reach. Provenance reports which branch won:
    ``cache`` (manager's MCP cache) | ``metadata`` (stored fallback) |
    ``empty`` (neither).
    """
    lifecycle = getattr(manager, "_lifecycle_service", None)
    resolver = getattr(lifecycle, "_get_mcp_tool_names", None)
    if resolver is not None:
        resolved = list(resolver(instance_id, stored_mcp) or [])
    else:
        resolved = list(stored_mcp or [])
    if resolved:
        source = "cache"
        if stored_mcp and resolved == list(stored_mcp):
            mcp_service = getattr(manager, "_mcp_service", None)
            cache_lookup = getattr(mcp_service, "get_mcp_tools", None) if mcp_service else None
            if cache_lookup is None or not cache_lookup(instance_id):
                source = "metadata"
    elif stored_mcp:
        source = "metadata"
    else:
        source = "empty"
    return resolved, source


# ── Tier-b diff (T3.1 — over the LOADER-CACHED persona only) ────────────────

_HEADING_RE = re.compile(r"^#{1,2} .*$", re.MULTILINE)
_HEADING_CAP = 10


def _persona_diff(old_persona: str, new_persona: str) -> dict[str, Any]:
    old_lines = old_persona.splitlines()
    new_lines = new_persona.splitlines()
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    old_headings = set(_HEADING_RE.findall(old_persona))
    new_headings = set(_HEADING_RE.findall(new_persona))
    headings_added = sorted(new_headings - old_headings)
    headings_removed = sorted(old_headings - new_headings)

    def _capped(items: list[str]) -> list[str]:
        if len(items) <= _HEADING_CAP:
            return items
        return items[:_HEADING_CAP] + [f"…and {len(items) - _HEADING_CAP} more"]

    return {
        "persona_diff": "exact",
        "lines_added": added,
        "lines_removed": removed,
        "bytes_before": len(old_persona.encode("utf-8")),
        "bytes_after": len(new_persona.encode("utf-8")),
        "headings_added": _capped(headings_added),
        "headings_removed": _capped(headings_removed),
    }


def _tier_c_diff() -> dict[str, Any]:
    """Tier-c honest fallback (AM-10 — the v1 contract): NO claim."""
    return {"persona_diff": "unavailable"}


# ── The INFO line (AM-15/D24 — frozen field order; the ONE telemetry) ───────


def format_info_line(
    instance_id: str,
    agent_id: str,
    outcome: str,
    code: str | None,
    provenance: dict[str, Any],
    *,
    fingerprint_changed: bool,
    hash_divergence: bool,
    cache_busted: bool,
    persona_diff_kind: str,
    was_cached: bool,
    manager_instances: str,
) -> str:
    """The frozen one-line-per-call observability (counts/flags ONLY —
    never persona or diff content). Field order is PINNED (D24); the
    outcome enum is the six-value superset (INFO_OUTCOMES)."""
    release = provenance.get("running_release") or "?"
    daemon_version = provenance.get("daemon_version") or "?"
    # W6 (leader ruling): primitive-origin refusals carry no counts yet —
    # render the explicit unknown marker, never an empty field.
    manager_instances = provenance.get("manager_instances") or "?->?"
    return (
        f"prompt_reload: instance={instance_id} agent={agent_id} "
        f"outcome={outcome} code={code or '-'} release={release} "
        f"daemon_version={daemon_version} "
        f"fingerprint_changed={str(fingerprint_changed).lower()} "
        f"hash_divergence={str(hash_divergence).lower()} "
        f"cache_busted={str(cache_busted).lower()} "
        f"current_matches_running={provenance.get('current_matches_running', 'unknown')} "
        f"persona_diff={persona_diff_kind} was_cached={str(was_cached).lower()} "
        f"manager_instances={manager_instances}"
    )


#: The compact-note surface's INFO-line prefix. DISTINCT from the prior
#: lane's ``prompt_reload:`` prefix: the prior lane's INFO format and its
#: six-value outcome enum are PINNED (D24/D25) and are NOT extended by this
#: lane — the compact-note surface carries its own namespace.
COMPACT_NOTE_INFO_PREFIX = "prompt_reload_compact_note:"


def format_compact_note_info_line(
    instance_id: str,
    agent_id: str,
    outcome: str,
    code: str | None,
    *,
    note_chars: int,
    note_sha256_12: str,
    superseded_previous: bool = False,
    deferred: str = DEFERRED_STATE_NONE,
) -> str:
    """The compact-note surface's ONE content-free INFO line.

    Content-free BY CONSTRUCTION (the "logs content-free v1" doctrine —
    the memoir-lane content-free doctrine and the decision-family
    shadow-lane content-free doctrine are the precedents): the note's
    LENGTH and sha256 PREFIX only, NEVER the note body. Field order is
    FROZEN (AC11.3).
    """
    return (
        f"{COMPACT_NOTE_INFO_PREFIX} instance={instance_id} "
        f"agent={agent_id} outcome={outcome} code={code or '-'} "
        f"note_chars={note_chars} note_sha256={note_sha256_12} "
        f"superseded_previous={str(bool(superseded_previous)).lower()} "
        f"deferred={deferred or '-'}"
    )


# ── The primitive (T1.5/T1.6/T1.7) ──────────────────────────────────────────


async def refresh_instance_prompt(
    manager: Any, instance_id: str, *, force: bool = False
) -> PromptRefreshResult:
    """Validate → compose → compare → (maybe) pop, for ONE instance.

    Raises nothing — every failure is a typed :class:`PromptRefreshResult`
    (W7: the env-provenance build is INSIDE the try, so even it cannot
    raise to the caller — the future compaction caller inherits the same
    contract). Must be AWAITED on the daemon's main loop (D23 runbook
    precondition).
    """
    provenance: dict[str, Any] = {
        "instance_id": instance_id,
        "force": bool(force),
        "version_tag": None,  # parity: None on both paths for ari v1 (approver note 4)
        "manager_instances": "",
        "was_cached": instance_id in getattr(manager, "instances", {}),
    }

    try:
        provenance.update(_release_provenance())
        return await _refresh_impl(manager, instance_id, force, provenance)
    except Exception:  # noqa: BLE001 — typed FAILED envelope, never raise
        logger.exception("prompt_reload internal error for %s", instance_id)
        return _refused(
            CODE_INTERNAL_ERROR,
            "an unexpected error occurred (logged server-side); nothing was "
            "popped and no cache was mutated by this call",
            provenance,
        )


async def _refresh_impl(
    manager: Any, instance_id: str, force: bool, provenance: dict[str, Any]
) -> PromptRefreshResult:
    # ── Resolve the instance + its agent (registry, spawn parity) ──────────
    instance_repo = getattr(manager, "_instance_repository", None)
    instance_meta = instance_repo.get(instance_id) if instance_repo is not None else None
    if instance_meta is None:
        return _refused(
            CODE_INTERNAL_ERROR,
            f"instance row not found for {instance_id} — cannot resolve its agent",
            provenance,
        )
    agent_id = getattr(instance_meta, "agent_id", "") or ""
    provenance["agent_id"] = agent_id

    from ..registry import get_registry

    registry = get_registry()
    agent_meta = registry.get_resolved(agent_id) if agent_id else None
    if agent_meta is None:
        return _refused(
            CODE_AGENT_DIR_UNRESOLVED,
            f"registry has no agent metadata for {agent_id!r} (metadata.path "
            "unresolvable — the agent dir the spawn path used is gone)",
            provenance,
        )
    resolved_dir = Path(agent_meta.path)
    provenance["resolved_dir"] = str(resolved_dir)
    version_tag = getattr(agent_meta, "version_tag", None)
    provenance["version_tag"] = version_tag  # parity with the restore call shape

    # ── Self-env-aware validation (D2/AM-5) ─────────────────────────────────
    self_env = provenance.get("self_env")
    if not self_env:
        return _refused(
            CODE_DIR_INVALID,
            "self-env marker unresolvable (ENSEMBLE_SELF_ENV unset and "
            "auto-derivation failed) — cannot validate the agent dir",
            provenance,
        )
    if self_env == "dev":
        agents_root = getattr(registry, "_agents_dir", None)
        if agents_root is None or not _is_under(resolved_dir, Path(agents_root)):
            return _refused(
                CODE_DIR_INVALID,
                f"dev env: resolved dir {resolved_dir} is outside the repo "
                f"agents root {agents_root}",
                provenance,
            )
        manifest_mode = "skip-dev"
    else:
        install_root = Path(provenance["install_root"]) if provenance.get("install_root") else None
        if install_root is None:
            return _refused(
                CODE_DIR_INVALID,
                f"{self_env} env: no install dir resolvable — cannot validate "
                f"resolved dir {resolved_dir}",
                provenance,
            )
        try:
            rel = resolved_dir.resolve().relative_to((install_root / "releases").resolve())
        except ValueError:
            rel = None
        if (
            rel is None
            or len(rel.parts) != 3
            or rel.parts[1] != "agents"
            or not _RELEASE_NAME_RE.match(rel.parts[0])
        ):
            return _refused(
                CODE_DIR_INVALID,
                f"{self_env} env: resolved dir {resolved_dir} is not under "
                f"{install_root / 'releases'} / <vX.Y.Z> / agents",
                provenance,
            )
        manifest_mode = "verify" if self_env in ("live", "sandbox") else "verify-soft"

    # ── meta.json structural validation (D6 code 7 — the loader papers
    # over corrupt meta with a silently skill-less persona; we refuse) ──────
    meta_path = resolved_dir / "meta.json"
    agent_file_meta: dict[str, Any] | None = None
    try:
        agent_file_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(agent_file_meta, dict):
            raise ValueError("meta.json root is not an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _refused(
            CODE_AGENT_META_INVALID,
            f"agent meta.json at {meta_path} is unparseable/structurally "
            f"invalid ({exc}) — the loader would silently compose a "
            "skill-less persona over it; refusing instead",
            provenance,
        )

    # ── Read-set derivation + THE compose-input snapshot (AM-1/AM-8/N1) ─────
    # Hashes + mtimes are captured ONCE, immediately before the decision
    # and compose below, and this SAME snapshot feeds BOTH the decision
    # tuple and the baseline record. INVARIANT (N1.4): a baseline must
    # always describe the inputs of the compose that produced the persona
    # actually served — never a separate later disk read. The N1 defect
    # was exactly that: `_record_baseline` recorded DISK hashes while the
    # served persona came from a stale CACHE HIT, poisoning the map so
    # every later call reported `no_change` while serving stale bytes.
    read_set = _loader_read_set(resolved_dir, agent_file_meta)
    compose_input_hashes = {key: _file_sha256(path) for key, path in read_set.items()}
    compose_input_mtimes = {key: path.stat().st_mtime for key, path in read_set.items()}

    # ── Manifest verification (D3/AM-2/AM-3) ────────────────────────────────
    if manifest_mode == "skip-dev":
        provenance["manifest"] = "absent(dev)"
    else:
        release_root = resolved_dir.parent.parent
        try:
            manifest = load_release_manifest(release_root)
        except ManifestError as exc:
            return _refused(
                CODE_MANIFEST_UNREADABLE,
                f"release manifest at {release_root} is torn/invalid: {exc}",
                provenance,
            )
        if manifest is None:
            if manifest_mode == "verify":
                return _refused(
                    CODE_MANIFEST_MISSING_LIVE,
                    f"{self_env}-shaped release {release_root} has no "
                    "manifest.json — cannot verify the loader's read set",
                    provenance,
                )
            provenance["manifest"] = f"absent({self_env})"
        else:
            relpaths = [
                _manifest_relpath(resolved_dir.name, key)
                for key in sorted(read_set)
            ]
            verdict = verify_paths(release_root, manifest, relpaths)
            if not verdict.ok:
                problems = list(verdict.mismatches) + list(verdict.unexpected_skips)
                return _refused(
                    CODE_MANIFEST_MISMATCH,
                    "loader read-set files fail manifest verification: "
                    + "; ".join(problems),
                    provenance,
                )
            provenance["manifest"] = (
                f"ok({verdict.checked} checked, {verdict.skipped_unmanifested} "
                "unmanifested-skipped)"
            )

    # ── Tool-side prompt-versions repo probe (AM-9 — BEFORE composition;
    # the loader's own hook FAIL-OPENS and cannot be fail-closed truth) ─────
    repo = _get_prompt_version_repo()
    db_rows: list[Any] | None = None
    if repo is not None:
        try:
            db_rows = repo.get_effective_rows(agent_id, version_tag or "")
        except Exception as exc:  # noqa: BLE001 — fail-closed probe (R6)
            return _refused(
                CODE_DB_UNAVAILABLE,
                f"the prompt-versions repository probe raised ({exc.__class__.__name__}) "
                "— composition skipped (the loader would fail OPEN and "
                "silently downgrade the persona)",
                provenance,
            )
    # N4-unified operand: repo absent and zero-rows both yield "" (loader
    # :210 convention) so benign boot-moment states never false-diff.
    db_fingerprint = _compute_db_versions_fingerprint(db_rows)
    provenance["db_fingerprint"] = "absent" if repo is None else (db_fingerprint or "")
    provenance["db_rows"] = _db_row_states(db_rows)

    # ── mcp_tool_names parity (AM-14) ───────────────────────────────────────
    instance_metadata = getattr(instance_meta, "instance_metadata", None) or {}
    stored_mcp = instance_metadata.get("mcp_tool_names") if isinstance(instance_metadata, dict) else None
    mcp_names, mcp_source = _resolve_mcp_tool_names(manager, instance_id, stored_mcp)
    provenance["mcp_tool_names_source"] = mcp_source
    provenance["mcp_tool_names"] = sorted(mcp_names)

    prompt_cache = getattr(manager, "prompt_cache", None)
    if prompt_cache is None:
        return _refused(
            CODE_INTERNAL_ERROR,
            "manager has no prompt_cache — cannot compose",
            provenance,
        )
    my_cache_key = prompt_cache._make_key(agent_id, mcp_names, version_tag)
    tag_suffix = f"[{version_tag}]" if version_tag else ""
    key_prefix = f"{agent_id}{tag_suffix}::"
    other_keys = [
        key
        for key in prompt_cache._cache
        if key.startswith(key_prefix) and key != my_cache_key
    ]
    # AM-14 downgrade vector: a differently-keyed cache entry means the
    # persona this closure consumed lives under ANOTHER key — diffing
    # against MY key's entry would fabricate a delta (or hide one).
    parity_ok = not other_keys

    # ── Claim-guard snapshot (AM-13/D23): capture the tuple + its STORED
    # MTIMES ONCE, before composing. The stored-mtimes conjunct is
    # evaluated at decision time (below) — W5, leader ruling 2026-11-06:
    # the weaker content-only guard was rejected because it leaves a
    # permanently unverifiable leg. ──────────────────────────────────────
    snapshot_entry = prompt_cache._cache.get(my_cache_key)
    snapshot_mtimes = snapshot_entry[2] if snapshot_entry is not None else None

    # ── Baseline-map consult + tuple decision (D5 — hash-based, AM-8) ───────
    baseline_key = (agent_id, version_tag, tuple(sorted(mcp_names)))
    baseline = _BASELINE_MAP.get(baseline_key)
    files_changed = baseline is None or baseline[0] != compose_input_hashes
    # fingerprint_changed (Advisory 8): the DB operand differs from the
    # baseline's stored value; false on unproven (no baseline to compare).
    fingerprint_changed = (
        bool(baseline is not None and baseline[1] != db_fingerprint)
        if baseline is not None
        else False
    )
    hash_divergence = bool(files_changed and baseline is not None)
    hash_mtime_divergence = False
    cache_busted = False

    # Stale-cache bust (AM-8 / N1 — leader-ratified Candidate A). The
    # loader's cache-hit (loader.py:1466) serves the cached persona
    # whenever ``stored_mtimes == current_mtimes`` — NO content hash, NO
    # re-read — so an mtime-preserving edit is invisible to it and the
    # bust is the ONLY cure. ``cache_busted`` therefore means "the shared
    # cache entry was invalidated": a REPORTING observable AND a
    # CORRECTNESS mechanism (gating this on the baseline removed the
    # cure — N1). Two branches:
    #   (b) COLD (no baseline) + a live entry ⇒ we cannot prove the
    #       entry's bytes match disk ⇒ MUST bust so the compose reads
    #       disk truth (this is the already-documented "recompose + pop"
    #       path). ``hash_mtime_divergence`` stays False — no fabricated
    #       divergence claim (W1's over-report stays closed).
    #   (a) warm + files changed + the loader's mtime/fingerprint
    #       equality would still HIT ⇒ provably stale ⇒ bust and REPORT
    #       ``hash_mtime_divergence=True``.
    if snapshot_entry is not None:
        if baseline is None:
            prompt_cache.invalidate(agent_id, mcp_names, version_tag)
            cache_busted = True
            provenance["cache_busted_reason"] = (
                "cold_process_cannot_prove_entry_bytes"
            )
        elif files_changed:
            file_keys_match = {
                key: value for key, value in snapshot_mtimes.items() if key != FP_KEY
            } == compose_input_mtimes
            fp_match = snapshot_mtimes.get(FP_KEY) == db_fingerprint
            if file_keys_match and fp_match:
                hash_mtime_divergence = True
                prompt_cache.invalidate(agent_id, mcp_names, version_tag)
                cache_busted = True
                provenance["cache_busted_reason"] = "hash_mtime_divergence"

    # ── Compose (R2 — through daemon.manager, the test patch seam) ──────────
    from ..manager import load_and_cache_prompt

    try:
        new_persona, _token_count = load_and_cache_prompt(
            agent_id,
            resolved_dir,
            prompt_cache,
            mcp_names,
            version_tag=version_tag,
            is_custom_agent=bool(getattr(agent_meta, "is_custom", False)),
        )
    except Exception as exc:  # noqa: BLE001 — typed refusal, never raise
        return _refused(
            CODE_COMPOSE_FAILED,
            f"load_and_cache_prompt raised {exc.__class__.__name__}: {exc}",
            provenance,
        )

    # ── Outcome decision (D5, Rev-3 B1/B4) ──────────────────────────────────
    if baseline is None:
        # B1/Q2: no baseline record (cold process / first call after ANY
        # restart) → unproven, force-like: recompose + POP + tier-c report.
        # NEVER no_change without a baseline (S10-n — deliberate).
        outcome = OUTCOME_UNPROVEN
    elif not files_changed and not fingerprint_changed:
        outcome = OUTCOME_FORCED_RELOAD if force else OUTCOME_NO_CHANGE
    elif not files_changed and fingerprint_changed:
        outcome = OUTCOME_RELOADED_DB  # B4/Q1 — F4 honored AT the reload
    else:
        outcome = OUTCOME_RELOADED

    # ── Tier-b vs tier-c diff (claim-guard AM-13/D23 + AM-14 parity) ────────
    # W5 — the AM-13 stored-mtimes conjunct, evaluated at DECISION time:
    # the entry must be EITHER untouched (cache hit — the mtimes object
    # is still the snapshot's) OR exactly the entry our own compose just
    # wrote. Any other stored-mtimes state ⇒ a third party refilled the
    # entry mid-call ⇒ skew ⇒ tier-c. W1 — a cold process (no baseline)
    # NEVER claims exactness ("unproven" must not print a delta).
    post_entry = prompt_cache._cache.get(my_cache_key)
    compose_wrote = post_entry is not None and post_entry[0] == new_persona
    stored_mtimes_unchanged = snapshot_mtimes is not None and (
        (post_entry is snapshot_entry and post_entry[2] is snapshot_mtimes)
        or compose_wrote
    )
    claim_ok = (
        snapshot_entry is not None
        and baseline is not None
        and parity_ok
        and stored_mtimes_unchanged
    )
    if outcome in (OUTCOME_NO_CHANGE, OUTCOME_FORCED_RELOAD) and not files_changed:
        # Unchanged tree: the old and new personas are the same fill —
        # the tier-b block still reports (zero-delta) when provable.
        diff = (
            _persona_diff(snapshot_entry[0], new_persona)
            if claim_ok
            else _tier_c_diff()
        )
    elif claim_ok:
        diff = _persona_diff(snapshot_entry[0], new_persona)
    else:
        diff = _tier_c_diff()

    file_rows = []
    for key in sorted(compose_input_hashes):
        before = baseline[0].get(key) if baseline else None
        after = compose_input_hashes[key]
        file_rows.append(
            {
                "path": key,
                "sha_before": (before[:12] + "…") if before else "-",
                "sha_after": after[:12] + "…",
                "changed": bool(before is None or before != after),
            }
        )
    diff["files"] = file_rows

    # ── Pop (T1.7 — iff changed, forced, or unproven; NEVER on no_change) ───
    instances = getattr(manager, "instances", {})
    before_count = len(instances)
    if outcome != OUTCOME_NO_CHANGE:
        instances.pop(instance_id, None)
    after_count = len(instances)
    provenance["manager_instances"] = f"{before_count}->{after_count}"
    provenance["hash_mtime_divergence"] = hash_mtime_divergence
    provenance["cache_busted"] = cache_busted
    provenance["fingerprint_changed"] = fingerprint_changed
    provenance["hash_divergence"] = hash_divergence
    provenance["baseline_present"] = baseline is not None
    provenance["parity_ok"] = parity_ok
    provenance["claim_guard"] = "held" if claim_ok else "skew-or-absent"

    # ── Baseline write (Advisory 2 — every successful fill seeds it) ────────
    # N1.4 INVARIANT: the record MUST derive from the SAME snapshot that
    # fed the decision/compose above — never a later, separate disk read.
    # The bust branches (above) guarantee the compose served DISK truth in
    # every case where the entry's bytes were unprovable, so this snapshot
    # describes exactly the inputs of the persona actually served.
    _record_baseline(baseline_key, dict(compose_input_hashes), db_fingerprint)

    message = _outcome_message(outcome, files_changed, fingerprint_changed, cache_busted)
    return PromptRefreshResult(
        outcome=outcome,
        code=None,
        message=message,
        diff=diff,
        provenance=provenance,
    )


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _db_row_states(db_rows: list[Any] | None) -> list[dict[str, str]]:
    """Per-artifact effective-row state for the provenance payload,
    carrying the F7 honesty marks (D15 — the canonical S10-a/S10-b
    literals, referenced by content here; the report renderer emits
    them verbatim)."""
    states: list[dict[str, str]] = []
    for row in db_rows or []:
        artifact = getattr(row, "artifact", "") or ""
        state = {
            "artifact": artifact,
            "class": getattr(row, "artifact_class", "CORE") or "CORE",
            "selected": getattr(row, "selected_version_id", None) or "-",
        }
        if artifact == "tools_note":
            state["honesty"] = S10_A_TOOLS_NOTE_DEAD
        elif artifact in ("user", "growth"):
            state["honesty"] = S10_B_USER_GROWTH_NOT_IN_PROMPT
        states.append(state)
    return states


def _outcome_message(
    outcome: str, files_changed: bool, fingerprint_changed: bool, cache_busted: bool
) -> str:
    if outcome == OUTCOME_NO_CHANGE:
        return (
            "No changes to the prompt files since the last cache fill "
            "(file-level freshness — not a claim that the running closure "
            "persona is current)."
        )
    if outcome == OUTCOME_FORCED_RELOAD:
        return (
            "Forced reload: the prompt files are unchanged, but force=true "
            "popped the cached graph anyway — the next dispatch recomposes "
            "from the same inputs."
        )
    if outcome == OUTCOME_RELOADED_DB:
        return (
            "Prompt-versions DB content changed (files unchanged) — "
            "recomposed through the loader and popped; the DB override is "
            "honored on the reload itself, not deferred."
        )
    if outcome == OUTCOME_UNPROVEN:
        return (
            "No prior baseline record for this agent in this process "
            "(first call after a restart) — recomposed and popped as a "
            "precaution; the diff below cannot be proven, so no persona "
            "delta is claimed."
        )
    detail = "prompt files changed"
    if cache_busted:
        detail += "; the loader cache was provably stale (hash/mtime divergence) and was BUSTED before composing"
    return f"Reloaded: {detail} — recomposed and the cached graph was popped."
