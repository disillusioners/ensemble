"""Agent Snapshot capture machinery (PR4).

Sibling of :mod:`daemon.services.compact_executor` in shape. Two
classes:

* :class:`SnapshotExecutor` — the single-instance capture pipeline:
  per-instance dormant read → R6a exclusion → 40k input clamp → R11
  digest LLM call → digest assembly (provenance block) → terminal row
  write. ONE try/except around read+LLM+write (design §2.4 — the
  concrete exception to tolerate is ``KeyError`` from hard-deleted
  instance rows, ``instance_lifecycle.py:3887-3888``).
* :class:`SnapshotService` — the D3 row-ledger lane:
  :meth:`capture_async` inserts the ``running`` row (the durability
  ledger), runs the capture as an asyncio background task, and writes
  the terminal state (``active`` | ``failed``); plus the boot sweep
  call-through (:meth:`sweep_orphaned_running` — ``running`` rows
  orphaned by a daemon crash → ``interrupted``, same shape as
  ``JobRecoveryService.recover_on_startup`` scoped to ONE table) and
  the staleness consumer seam.

Hazard mitigations (design §9 — ALL mandatory here):

* **Revive hazard** — this module NEVER calls ``send_message`` on its
  target. Capture reads are ``manager.get_instance`` +
  ``graph.aget_state`` only (the revive hazard lives at
  ``instance_messaging.py:1897-1931``).
* **Memory pinning** — the cold-load read inserts a
  ``manager.instances`` entry permanently; the executor runs a
  post-read eviction pass popping ONLY the entry the read inserted
  (the target was absent before).
* **Watchover-recovery asterisk** — ``get_instance`` on an instance
  carrying a stale ``watchover_pending_termination`` marker triggers
  a REAL terminate cascade
  (``instance_lifecycle.py:4369-4370``). "Capture never mutates" is
  true modulo that asterisk; the single try/except absorbs the
  fallout and the failure note names it.
* **Hard-deleted rows** — ``KeyError`` tolerated → row ``failed``
  with the reason recorded (never raised into the tool surface).

Committed caps (design §8 — fail loud, not advisory):

* 40k chars per-call input clamp (mirrors
  ``TRUNCATION_GLOBAL_INPUT_CAP_CHARS`` — imported, not duplicated).
* 600s per-snapshot wall clock — breach records a ``failed`` row.
* Cheap-tier model chain ``SNAPSHOT_MODEL > COMPACTION_MODEL >
  session`` with the effective model stamped on the row.

R16 groundwork: one structured capture log line per capture (status,
approx token usage, wall clock, effective model) — counters/metrics
SURFACE is Wave 3; this module only emits the line.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from daemon._content_hardening import (  # noqa: F401  (R6a — direct import, NOT compaction aliases)
    extract_text_from_content,
    has_context_kind,
    is_hoisted_injected,
    is_injected_message,
    partition_injected_for_compaction,
)
from daemon.repositories.snapshot.models import (
    SNAPSHOT_STATUS_ACTIVE,
    SNAPSHOT_STATUS_FAILED,
    SNAPSHOT_STATUS_RUNNING,
    Snapshot,
    SnapshotEmbedding,
)
from daemon.repositories.snapshot.repository import SnapshotRepository
from daemon.services.snapshot_prompts import (
    DIGEST_KEYS,
    SNAPSHOT_PROMPT_VERSION,
    SNAPSHOT_SUMMARIZER_PERSONA,
)

logger = logging.getLogger(__name__)

# ── Committed caps (design §8) ───────────────────────────────────────────
#: Per-call input clamp — MIRRORS the compaction precedent constant
#: (imported, single source of truth).
from daemon.compaction import TRUNCATION_GLOBAL_INPUT_CAP_CHARS  # noqa: E402

#: Per-snapshot wall clock (seconds) — breach fails the capture (loud).
SNAPSHOT_WALL_CLOCK_S = 600

# ── R8 judgment tags ─────────────────────────────────────────────────────
#: Fixed ``kind:`` enum — exactly 8 values (design §6.3 R8).
KIND_ENUM: frozenset[str] = frozenset(
    {
        "investigation",
        "defect-verification",
        "implementation",
        "review",
        "refactor",
        "release-gate",
        "design-exploration",
        "environment-setup",
    }
)
#: Free-form judgment dims (lowercase-kebab values).
JUDGMENT_DIMS: frozenset[str] = frozenset({"kind", "subsystem", "feature", "topic"})
#: Judgment-tag count window: 2-4 recommended, hard cap 8 (fail loud
#: outside the window).
JUDGMENT_TAG_MIN = 2
JUDGMENT_TAG_HARD_CAP = 8

# ── Staleness thresholds (design §5.2) ───────────────────────────────────
#: Age (days) at/below which a snapshot is ``fresh``.
SNAPSHOT_FRESH_MAX_AGE_DAYS = 7
#: Age (days) at/above which a snapshot is ``expired`` (between the
#: two = ``stale`` — warm with drift warning, R14).
SNAPSHOT_EXPIRED_AGE_DAYS = 30

#: Terminal instance statuses (post-capture-advance check compares the
#: target's CURRENT status against these).
TERMINAL_INSTANCE_STATUSES: frozenset[str] = frozenset(
    {"completed", "error", "terminated", "failed"}
)

#: Lineage up-walk depth cap (mirrors the repository's
#: ``_MAX_TRAVERSAL_DEPTH``).
_LINEAGE_MAX_DEPTH = 256


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# R8 tags — derivation + judgment normalization (fail loud)
# ============================================================================


def _kebab(value: str) -> str:
    """Normalize a free-form tag value to lowercase-kebab."""
    lowered = value.strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def derive_auto_tags(
    *,
    project_id: str,
    agent_id: str,
    lineage_root_id: str | None,
    git_branch: str | None,
    spawned_from_snapshot_id: str | None,
    runtime_version: str,
) -> list[str]:
    """Derive the R8 auto-tag set (zero prompt cost, computed at capture).

    Emits (in order): ``project:``, ``agent:``, ``role:`` (alias —
    BOTH emitted; drop-vs-deprecate deferred), ``lineage:`` (from the
    permanent ``parent_id`` chain root), ``branch:`` (iff stamped),
    ``from-snapshot:`` (iff the target carries
    ``spawned_from_snapshot_id`` — R6b read-side), ``runtime:``.
    """
    tags = [
        f"project:{project_id}",
        f"agent:{agent_id}",
        f"role:{agent_id}",
    ]
    if lineage_root_id:
        tags.append(f"lineage:{lineage_root_id}")
    if git_branch:
        tags.append(f"branch:{git_branch}")
    if spawned_from_snapshot_id:
        tags.append(f"from-snapshot:{spawned_from_snapshot_id}")
    tags.append(f"runtime:{runtime_version}")
    return tags


def normalize_judgment_tags(tags: list[str]) -> list[str]:
    """Normalize + enforce the R8 judgment-tag contract (fail loud).

    Contract:

    * every tag is ``dim:value`` with ``dim`` in
      :data:`JUDGMENT_DIMS`;
    * ``kind:`` appears EXACTLY once, from the fixed
      :data:`KIND_ENUM`;
    * free-form values are normalized to lowercase-kebab;
    * count within ``[JUDGMENT_TAG_MIN, JUDGMENT_TAG_HARD_CAP]``
      (2-4 recommended, 8 hard cap);
    * duplicates (post-normalization) are rejected;
    * any violation raises ``ValueError`` — the capture lane refuses
      to run with malformed tags (fail loud, never silently drop).

    Returns:
        The normalized judgment tags (order preserved).
    """
    normalized: list[str] = []
    seen: set[str] = set()
    kind_values: list[str] = []
    for raw in tags:
        if ":" not in raw:
            raise ValueError(
                f"judgment tag {raw!r} is not dim:value "
                f"(dims: {sorted(JUDGMENT_DIMS)})"
            )
        dim, _, value = raw.partition(":")
        dim = dim.strip().lower()
        if dim not in JUDGMENT_DIMS:
            raise ValueError(
                f"judgment tag dim {dim!r} unknown; "
                f"expected one of {sorted(JUDGMENT_DIMS)}"
            )
        value = _kebab(value)
        if not value:
            raise ValueError(f"judgment tag {raw!r} has an empty value")
        tag = f"{dim}:{value}"
        if tag in seen:
            raise ValueError(f"duplicate judgment tag {tag!r}")
        seen.add(tag)
        if dim == "kind":
            kind_values.append(value)
        normalized.append(tag)

    if len(kind_values) != 1:
        raise ValueError(
            f"exactly one kind: tag is required, got {len(kind_values)} "
            f"({kind_values!r}); enum: {sorted(KIND_ENUM)}"
        )
    if kind_values[0] not in KIND_ENUM:
        raise ValueError(
            f"kind {kind_values[0]!r} not in the fixed enum: {sorted(KIND_ENUM)}"
        )
    if not (JUDGMENT_TAG_MIN <= len(normalized) <= JUDGMENT_TAG_HARD_CAP):
        raise ValueError(
            f"judgment tag count {len(normalized)} outside "
            f"[{JUDGMENT_TAG_MIN}, {JUDGMENT_TAG_HARD_CAP}] "
            f"(2-4 recommended, {JUDGMENT_TAG_HARD_CAP} hard cap)"
        )
    return normalized


# ============================================================================
# Staleness compute (design §5.2 — pure helper, consumed by Wave 2)
# ============================================================================


def compute_staleness_report(
    snapshot: Snapshot,
    *,
    target_instance_status: str | None = None,
    current_runtime_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute the §5.2 ``staleness_report`` (sync, no LLM, no subprocess).

    Metadata-compare default: ``age_days`` + ``runtime_version`` drift
    + the post-capture-advance check (one cheap DB read performed by
    the CALLER — this helper takes the resulting status as an arg).
    ``repo_state`` stays ``None`` — the ``verify=git`` anchor is Wave
    2's tool-side opt-in; the helper is extensible (an optional
    ``git_state`` kwarg can populate it later without a shape change).

    Returns:
        ``{"snapshot_age_days": float, "freshness": "fresh"|"stale"|
        "expired", "warnings": [...], "repo_state": None}`` — the
        shape from design §5.2. ``fresh | stale | expired`` are
        COMPUTED here and never stored.
    """
    now = now or datetime.now(timezone.utc)
    captured = datetime.fromisoformat(snapshot.created_at)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - captured).total_seconds() / 86400.0)

    current_version = current_runtime_version or _runtime_version()
    warnings: list[str] = []
    if snapshot.runtime_version and snapshot.runtime_version != current_version:
        warnings.append(
            f"runtime version drift: {snapshot.runtime_version} → {current_version}"
        )

    # Post-capture-advance check (single-instance, design §5.2): the
    # capture recorded whether the target was LIVE at read time; if it
    # has since gone terminal, the work moved on after the digest froze.
    provenance = (snapshot.digest or {}).get("provenance", {}) or {}
    if provenance.get("captured_live") and target_instance_status in (
        TERMINAL_INSTANCE_STATUSES
    ):
        warnings.append("tree advanced post-capture (live-tree snapshot)")

    if age_days >= SNAPSHOT_EXPIRED_AGE_DAYS:
        freshness = "expired"
    elif age_days >= SNAPSHOT_FRESH_MAX_AGE_DAYS:
        freshness = "stale"
    else:
        freshness = "fresh"

    return {
        "snapshot_age_days": round(age_days, 4),
        "freshness": freshness,
        "warnings": warnings,
        "repo_state": None,  # verify=git is Wave 2's tool-side opt-in
    }


def _runtime_version() -> str:
    """Lazy ``daemon.__version__`` read (import-time cost avoidance)."""
    import daemon

    return daemon.__version__


# ============================================================================
# R6a exclusion predicate
# ============================================================================


def is_snapshot_digest_block(msg: Any) -> bool:
    """R6a predicate: is this message a ``snapshot_digest`` context block?

    Uses the Wave 1a hardening predicates directly (imported from
    :mod:`daemon._content_hardening` — NOT the compaction aliases):
    the stamp contract is ``injected_message=True`` + ``context_kind``
    via ``_make_context_message``, stable id
    ``snapshot_digest:{iid}``.
    """
    if not is_injected_message(msg) or not has_context_kind(msg):
        return False
    kwargs = getattr(msg, "additional_kwargs", None) or {}
    return kwargs.get("context_kind") == "snapshot_digest"


# ============================================================================
# Digest markdown parsing (R11 8-tuple)
# ============================================================================

_HEADER_ALIASES: dict[str, str] = {}


def _header_to_key(title: str) -> str | None:
    """Map a ``## `` section title back to its canonical digest key."""
    global _HEADER_ALIASES
    if not _HEADER_ALIASES:
        from daemon.services.snapshot_prompts import DIGEST_EXTRACTION_FIELDS

        for key, name, _inst in DIGEST_EXTRACTION_FIELDS:
            _HEADER_ALIASES[name.lower()] = key
            _HEADER_ALIASES[key.lower()] = key
            _HEADER_ALIASES[_kebab(name)] = key
    return _HEADER_ALIASES.get(title.strip().lower())


def parse_digest_markdown(text: str) -> dict[str, Any]:
    """Parse the LLM's markdown digest into the R11 8-tuple structure.

    Tolerant parse: ``## <title>`` headers map (case/kebab
    insensitively) to the canonical :data:`DIGEST_KEYS`; ``- `` bullet
    lines become the section's entries; the prose before the first
    header becomes ``task_summary_text``. Unparseable output
    (no recognized headers) falls back to ``raw_markdown`` so the
    knowledge is never silently dropped.
    """
    sections: dict[str, list[str]] = {key: [] for key in DIGEST_KEYS}
    task_summary_text = ""
    raw_lines: list[str] = []
    current_key: str | None = None
    recognized_any = False

    for line in text.splitlines():
        header_match = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if header_match:
            key = _header_to_key(header_match.group(1))
            if key:
                recognized_any = True
                current_key = key
                continue
        if line.strip() and current_key is None and not recognized_any:
            # Prose before the first recognized header.
            task_summary_text = (task_summary_text + " " + line.strip()).strip()
        if current_key is not None:
            bullet = line.strip()
            if bullet.startswith("- "):
                sections[current_key].append(bullet[2:].strip())
            elif bullet:
                # Continuation / plain prose inside a section.
                sections[current_key].append(bullet)
        raw_lines.append(line)

    digest: dict[str, Any] = {
        "task_summary_text": task_summary_text,
    }
    digest.update({key: sections[key] for key in DIGEST_KEYS})
    if not recognized_any and text.strip():
        digest["raw_markdown"] = text.strip()
    return digest


# ============================================================================
# SnapshotExecutor — the single-instance capture pipeline
# ============================================================================


class SnapshotExecutor:
    """One capture: read → R6a → clamp → LLM → digest → terminal write."""

    def __init__(self, manager: Any, snapshot_repository: SnapshotRepository) -> None:
        """Initialize the executor.

        Args:
            manager: :class:`InstanceManager` — used for the dormant
                ``get_instance`` + ``aget_state`` read, the LLM config
                (via the manager's compactor), and the instance
                repository reads. NEVER for messaging the target.
            snapshot_repository: Storage lane.
        """
        self._manager = manager
        self._snapshots = snapshot_repository

    # ── lineage (tag computation ONLY — never a capture walk) ─────────

    def _resolve_lineage_root(self, target_instance_id: str) -> str | None:
        """Walk the permanent ``parent_id`` chain UP to the tree root.

        This is the ONLY lineage machinery capture uses —
        ``get_tree_ids_permanent`` (the down-tree BFS) exists as a
        utility but capture never enumerates the tree (design P1
        per-instance pivot). Read-only, depth-capped at 256.
        """
        repo = getattr(self._manager, "_instance_repository", None)
        if repo is None:
            return None
        current = target_instance_id
        for _ in range(_LINEAGE_MAX_DEPTH):
            row = repo.get(current)
            if row is None or not row.parent_id:
                return current if row is not None else None
            current = row.parent_id
        logger.warning(
            f"[Snapshot] lineage up-walk hit the {_LINEAGE_MAX_DEPTH} "
            f"depth cap for {target_instance_id[:8]}…"
        )
        return current

    # ── the capture pipeline ──────────────────────────────────────────

    async def capture(self, row: Snapshot) -> Snapshot:
        """Run the full capture pipeline for one ``running`` row.

        Single try/except around read+LLM+write (design §2.4). The
        row's terminal state is written on EVERY path: ``active`` on
        success, ``failed`` otherwise (with the error in the digest).
        The concrete tolerated exception is ``KeyError`` (hard-deleted
        instance rows).

        Returns:
            The updated row (re-fetched post-write).
        """
        started_monotonic = time.monotonic()
        target = row.target_instance_id
        prompt_version = SNAPSHOT_PROMPT_VERSION
        # Pre-clamp transcript size for the R16 log line (None until
        # the transcript exists — earlier failures log the fallback).
        input_chars: int | None = None
        try:
            # ── 0. Target liveness read (LIVE banner + R6b stamp) ─────
            instance_repo = getattr(self._manager, "_instance_repository", None)
            target_row = (
                await asyncio.to_thread(instance_repo.get, target)
                if instance_repo is not None
                else None
            )
            if target_row is None:
                raise KeyError(
                    f"target instance row vanished before capture: {target}"
                )
            captured_live = target_row.status not in TERMINAL_INSTANCE_STATUSES
            spawned_from = (target_row.instance_metadata or {}).get(
                "spawned_from_snapshot_id"
            )

            # ── 1. Per-instance dormant read (P1 — TARGET ONLY) ───────
            # NO tree walk. NO send_message (revive hazard).
            was_pinned = target in self._manager.instances
            graph_obj = await self._manager.get_instance(target)
            checkpoint_state = await graph_obj.aget_state(
                {"configurable": {"thread_id": target}}
            )
            messages = list((checkpoint_state.values or {}).get("messages") or [])

            # ── 2. Post-read eviction pass (§9 memory-pin mitigation) ─
            # Pop ONLY the entry the cold-load read inserted (the
            # target was absent from manager.instances before us).
            if not was_pinned:
                self._manager.instances.pop(target, None)

            if not messages:
                raise ValueError(
                    "empty checkpoint (spawned-but-never-dispatched "
                    "instance has no conversation to distill)"
                )

            # ── 3. R6a exclusion + flatten ────────────────────────────
            selectable, preserved, _absorbed = partition_injected_for_compaction(
                messages
            )
            # Belt-and-braces: snapshot_digest blocks are
            # context_kind-stamped, so the truthy-keyed hoist lands
            # them in `preserved` (excluded from the selectable pool
            # by construction). The explicit drop below pins the R6a
            # contract against hoist-semantics drift; a leak is
            # WARN-logged via the hoist-predicate cross-check.
            for msg in selectable:
                if is_snapshot_digest_block(msg):
                    logger.warning(
                        "[Snapshot] R6a: snapshot_digest block leaked into "
                        "the selectable pool — dropped (hoist semantics "
                        "drift?)"
                    )
            r6a_input = [
                m
                for m in selectable
                if not is_snapshot_digest_block(m)
            ]

            transcript_parts: list[str] = []
            for msg in r6a_input:
                text = extract_text_from_content(msg.content)
                if not text.strip():
                    continue
                role = type(msg).__name__.replace("Message", "").lower()
                transcript_parts.append(f"[{role}] {text.strip()}")
            transcript = "\n\n".join(transcript_parts)
            if not transcript.strip():
                raise ValueError(
                    "post-R6a transcript is empty — nothing to distill"
                )
            # R16 honest input figure: capture the PRE-clamp size so
            # the capture log line reflects actual input size, not the
            # committed-cap ceiling.
            input_chars = len(transcript)

            input_clamped = False
            if len(transcript) > TRUNCATION_GLOBAL_INPUT_CAP_CHARS:
                # Committed cap (§8): head-keep clamp (early
                # exploration is the reusable experience; tail results
                # live in artifacts by pointer — D5). Loud, not
                # advisory: the clamp is stamped in the digest.
                transcript = (
                    transcript[:TRUNCATION_GLOBAL_INPUT_CAP_CHARS]
                    + "\n\n[…input clamped at "
                    f"{TRUNCATION_GLOBAL_INPUT_CAP_CHARS} chars…]"
                )
                input_clamped = True
                logger.warning(
                    f"[Snapshot] input clamp engaged for target "
                    f"{target[:8]}… ({TRUNCATION_GLOBAL_INPUT_CAP_CHARS} "
                    "chars — committed cap §8)"
                )

            # ── 4. Idempotency key + concurrent-capture dedup ─────────
            key_material: str = target + "\0" + hashlib.sha256(
                transcript.encode("utf-8")
            ).hexdigest()
            idempotency_key = hashlib.sha256(
                key_material.encode("utf-8")
            ).hexdigest()
            duplicate = self._find_concurrent_duplicate(row, idempotency_key)
            if duplicate is not None:
                return await self._fail_row(
                    row,
                    error=(
                        f"duplicate capture already in flight "
                        f"(snapshot {duplicate.id}, same "
                        f"target_instance_id+hash idempotency key)"
                    ),
                    digest_extra={
                        "idempotency_key": idempotency_key,
                        "duplicate_of": duplicate.id,
                    },
                    started_monotonic=started_monotonic,
                    prompt_version=prompt_version,
                    effective_model=row.effective_model,
                    input_chars=input_chars,
                )

            # ── 5. Digest LLM call (R11 persona; SNAPSHOT_MODEL chain) ─
            from daemon.compaction import (
                CompactionContext,
                ContextCompactor,
                call_summarization_llm_for_snapshot,
                resolve_snapshot_model,
            )
            from daemon.config import get_snapshot_model_env_resolved

            base_compactor = getattr(self._manager, "_compactor", None)
            base_config = getattr(base_compactor, "config", None) or getattr(
                getattr(self._manager, "config", None), "compaction", None
            )
            base_llm_cfg = dict(
                getattr(base_compactor, "llm_config_with_headers", None) or {}
            )
            base_llm_cfg.pop("default_headers", None)  # constructor re-adds
            if base_config is None or not base_llm_cfg:
                raise ValueError(
                    "manager carries no compactor/LLM config — cannot run "
                    "the digest call"
                )

            # Effective model: SNAPSHOT_MODEL > COMPACTION_MODEL > session.
            override = resolve_snapshot_model(
                base_config,
                snapshot_env_value=get_snapshot_model_env_resolved(),
            )
            effective_model = override or base_llm_cfg.get("model", "")
            # Synthetic config: only `.config` is read on this path —
            # pre-set `model` to the snapshot-chain override so the
            # call's internal resolve lands on the snapshot model (the
            # timeout math inherits the base config unchanged).
            call_config = (
                base_config.model_copy(update={"model": override})
                if override
                else base_config
            )
            compactor = ContextCompactor(
                config=call_config, llm_config=base_llm_cfg
            )
            context = CompactionContext(
                messages=r6a_input,
                system_prompt_tokens=0,
                model_name=effective_model,
                config=call_config,
                llm_config=dict(base_llm_cfg),
                instance_id=target,
            )
            prompt = (
                "Capture the digest for the agent-instance transcript "
                "below.\n\n=== INSTANCE TRANSCRIPT ===\n"
                f"{transcript}\n=== END TRANSCRIPT ==="
            )
            digest_text = await call_summarization_llm_for_snapshot(
                compactor,
                prompt,
                context,
                # R11 steering persona — REQUIRED on the snapshot path.
                system_message=SNAPSHOT_SUMMARIZER_PERSONA,
            )
            if not digest_text or not digest_text.strip():
                raise ValueError("digest LLM call returned an empty response")

            # ── 6. Digest assembly (R11 parse + provenance block) ─────
            parsed = parse_digest_markdown(digest_text)
            provenance = self._build_provenance(
                row=row,
                effective_model=effective_model,
                prompt_version=prompt_version,
                captured_live=captured_live,
                spawned_from=spawned_from,
                input_clamped=input_clamped,
            )
            digest: dict[str, Any] = {
                "provenance": provenance,
                "idempotency_key": idempotency_key,
                **parsed,
            }

            return await self._finish_row(
                row,
                status=SNAPSHOT_STATUS_ACTIVE,
                digest=digest,
                effective_model=effective_model,
                started_monotonic=started_monotonic,
                input_chars=input_chars,
            )

        except KeyError as exc:
            # Hard-deleted instance rows raise KeyError
            # (instance_lifecycle.py:3887-3888). Tolerate → failed row.
            return await self._fail_row(
                row,
                error=f"target instance row not found (hard-deleted?): {exc}",
                digest_extra=None,
                started_monotonic=started_monotonic,
                prompt_version=prompt_version,
                effective_model=row.effective_model,
                input_chars=input_chars,
            )
        except asyncio.CancelledError:
            # Service shutdown mid-capture — re-raise after recording
            # so the boot sweep classifies the row correctly.
            raise
        except Exception as exc:
            # Single try/except around read+LLM+write (design §2.4).
            # NOTE (§9 asterisk): a failure here MAY be the watchover
            # recovery cascade having terminated a corrupted-watchover
            # target during get_instance — the fallout surfaces as a
            # normal exception and lands in this handler by design;
            # the note names it so operators can tell the cases apart.
            return await self._fail_row(
                row,
                error=f"{type(exc).__name__}: {exc}",
                digest_extra={
                    "watchover_note": (
                        "capture may have triggered the watchover "
                        "pending-termination cascade on the target "
                        "(instance_lifecycle.py:4369-4370) — §9 asterisk"
                    )
                },
                started_monotonic=started_monotonic,
                prompt_version=prompt_version,
                effective_model=row.effective_model,
                input_chars=input_chars,
            )

    # ── helpers ───────────────────────────────────────────────────────

    def _find_concurrent_duplicate(
        self, row: Snapshot, idempotency_key: str
    ) -> Snapshot | None:
        """Another RUNNING row for the same target with the same key?"""
        for other in self._snapshots.find_by_status(SNAPSHOT_STATUS_RUNNING):
            if other.id == row.id or other.target_instance_id != row.target_instance_id:
                continue
            other_key = (other.digest or {}).get("idempotency_key")
            if other_key == idempotency_key:
                return other
        return None

    def _build_provenance(
        self,
        *,
        row: Snapshot,
        effective_model: str,
        prompt_version: str,
        captured_live: bool,
        spawned_from: str | None,
        input_clamped: bool,
    ) -> dict[str, Any]:
        """§9 mitigation (d) — the MANDATORY digest provenance block."""
        provenance: dict[str, Any] = {
            "source_instance_id": row.target_instance_id,
            "effective_model": effective_model,
            "prompt_version": prompt_version,
            "captured_at": _now_iso(),
            "captured_live": captured_live,
            "spawned_from_snapshot_id": spawned_from,
            "input_clamped": input_clamped,
        }
        banners: list[str] = []
        if captured_live:
            # LIVE banner — mandatory for live-instance captures (§9d).
            banners.append(
                "LIVE-CAPTURE: target was live at read time "
                "(last-committed-boundary read; verify staleness "
                "before trusting)"
            )
        if spawned_from:
            # R6b capture-time banner (R6c delta-only context).
            banners.append(
                f"SNAPSHOT-BORN: spawned from snapshot {spawned_from} — "
                "this capture should be DELTA-ONLY over that inherited "
                "digest"
            )
        provenance["banner"] = banners
        return provenance

    async def _finish_row(
        self,
        row: Snapshot,
        *,
        status: str,
        digest: dict[str, Any],
        effective_model: str | None,
        started_monotonic: float,
        input_chars: int | None = None,
    ) -> Snapshot:
        """Terminal write + the R16 structured capture log line."""
        wall_clock = time.monotonic() - started_monotonic
        updated = await asyncio.to_thread(
            self._snapshots.update_capture_result,
            row.id,
            status=status,
            digest=digest,
            effective_model=effective_model,
        )
        # R16 groundwork — ONE structured line per capture (counters /
        # metrics surface is Wave 3; token usage is an honest
        # tiktoken approximation of prompt-in / digest-out).
        logger.info(
            "[SnapshotCapture] "
            + json.dumps(
                {
                    "status": status,
                    "snapshot_id": row.id,
                    "target_instance_id": row.target_instance_id,
                    "effective_model": effective_model,
                    "wall_clock_s": round(wall_clock, 3),
                    "approx_tokens_in": _log_input_tokens(digest, input_chars),
                    "approx_tokens_out": _log_output_tokens(digest),
                    "created_by_agent_id": row.created_by_agent_id,
                    "project_id": row.project_id,
                }
            )
        )
        return updated

    async def _fail_row(
        self,
        row: Snapshot,
        *,
        error: str,
        digest_extra: dict[str, Any] | None,
        started_monotonic: float,
        prompt_version: str,
        effective_model: str | None,
        input_chars: int | None = None,
    ) -> Snapshot:
        """Record a ``failed`` terminal row (never raises)."""
        digest = {
            "provenance": {
                "source_instance_id": row.target_instance_id,
                "effective_model": effective_model,
                "prompt_version": prompt_version,
                "captured_at": _now_iso(),
                "captured_live": None,
            },
            "error": error,
            **(digest_extra or {}),
        }
        return await self._finish_row(
            row,
            status=SNAPSHOT_STATUS_FAILED,
            digest=digest,
            effective_model=effective_model,
            started_monotonic=started_monotonic,
            input_chars=input_chars,
        )


def _log_input_tokens(
    digest: dict[str, Any],
    input_chars: int | None = None,
) -> int:
    """Approximate input tokens recorded for the capture log line.

    The executor clamps input at 40k chars ≈ 10k tokens (§8); the
    honest figure is the PRE-clamp transcript size, which ``capture``
    records before clamping and threads through ``_finish_row`` /
    ``_fail_row``. Only when the true length is unavailable (failure
    before the transcript was built) does this fall back to the
    ceiling-bound estimate; the clamp stamp
    (``provenance.input_clamped``) disambiguates the clamped case
    (R16 groundwork fidelity for v1).
    """
    if input_chars is not None:
        return input_chars // 4
    return TRUNCATION_GLOBAL_INPUT_CAP_CHARS // 4


def _log_output_tokens(digest: dict[str, Any]) -> int:
    """Approximate digest-out tokens for the capture log line."""
    from daemon.loader import estimate_tokens

    return estimate_tokens(json.dumps(digest, default=str))


# ============================================================================
# SnapshotService — the D3 row-ledger lane
# ============================================================================


class SnapshotService:
    """Row-ledger lane: ``running`` row + asyncio background capture.

    The ``snapshots`` row IS the durability ledger (design §2.4 /
    decision D3): ``capture_async`` inserts the row with
    ``status='running'``, spawns the capture as an asyncio background
    task, and the task writes the terminal state. Agent-visible
    failures — the agent re-invokes on ``failed``; the boot sweep
    classifies crash-orphaned ``running`` rows ``interrupted``.
    """

    def __init__(
        self,
        manager: Any,
        snapshot_repository: SnapshotRepository,
        *,
        wall_clock_s: int = SNAPSHOT_WALL_CLOCK_S,
    ) -> None:
        """Initialize the service.

        Args:
            manager: :class:`InstanceManager`.
            snapshot_repository: Storage lane.
            wall_clock_s: Per-snapshot wall clock (committed cap §8).
        """
        self._manager = manager
        self._snapshots = snapshot_repository
        self._executor = SnapshotExecutor(manager, snapshot_repository)
        self._wall_clock_s = wall_clock_s
        self._tasks: set[asyncio.Task] = set()

    # ── capture lane ──────────────────────────────────────────────────

    async def capture_async(
        self,
        *,
        target_instance_id: str,
        project_id: str,
        created_by_agent_id: str,
        title: str,
        judgment_tags: list[str],
        task_summary: str = "",
        supersedes_snapshot_id: str | None = None,
        repo_path: str | None = None,
        vcs_type: str | None = None,
        git_sha: str | None = None,
        git_branch: str | None = None,
        git_dirty: bool = False,
    ) -> dict[str, Any]:
        """Insert the ``running`` ledger row and start the background
        capture.

        Returns (never raises into the tool surface):
            ``{"snapshot_id": str, "status": "running", "error": None}``
        """
        # R8 judgment tags — fail loud BEFORE any write (a malformed
        # tag list never enters the ledger).
        normalized_tags = normalize_judgment_tags(list(judgment_tags))
        auto_tags = derive_auto_tags(
            project_id=project_id,
            agent_id=self._resolve_target_agent_id(target_instance_id) or "unknown",
            lineage_root_id=self._executor._resolve_lineage_root(target_instance_id),
            git_branch=git_branch,
            spawned_from_snapshot_id=self._resolve_spawned_from(target_instance_id),
            runtime_version=_runtime_version(),
        )
        domain_tags = auto_tags + normalized_tags

        row = Snapshot(
            project_id=project_id,
            created_by_agent_id=created_by_agent_id,
            target_instance_id=target_instance_id,
            title=title,
            task_summary=task_summary,
            domain_tags=domain_tags,
            status=SNAPSHOT_STATUS_RUNNING,
            supersedes_snapshot_id=None,  # stamped by R12 at successor mint
            repo_path=repo_path,
            vcs_type=vcs_type,
            git_sha=git_sha,
            git_branch=git_branch,
            git_dirty=git_dirty,
            runtime_version=_runtime_version(),
            effective_model=None,  # stamped by the executor pre-call
            digest={"supersedes_snapshot_id": supersedes_snapshot_id}
            if supersedes_snapshot_id
            else {},
        )
        created = await asyncio.to_thread(
            self._snapshots.create_with_embeddings, row
        )

        task = asyncio.create_task(self._run_capture(created))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"snapshot_id": created.id, "status": SNAPSHOT_STATUS_RUNNING, "error": None}

    async def _run_capture(self, row: Snapshot) -> None:
        """Background task: capture under the committed wall clock."""
        try:
            await asyncio.wait_for(
                self._executor.capture(row), timeout=self._wall_clock_s
            )
        except asyncio.TimeoutError:
            # Committed cap (§8): 600s wall clock — fail loud.
            await asyncio.to_thread(
                self._snapshots.update_capture_result,
                row.id,
                status=SNAPSHOT_STATUS_FAILED,
                digest={
                    "error": (
                        f"per-snapshot wall clock exceeded "
                        f"({self._wall_clock_s}s) — committed cap §8"
                    )
                },
                effective_model=row.effective_model,
            )
        except asyncio.CancelledError:
            # Daemon shutdown — leave the row 'running'; the boot
            # sweep classifies it 'interrupted' on next start.
            raise
        except Exception as exc:  # pragma: no cover - defensive belt
            logger.error(
                f"[Snapshot] capture task crashed for row {row.id}: {exc}"
            )
            await asyncio.to_thread(
                self._snapshots.update_capture_result,
                row.id,
                status=SNAPSHOT_STATUS_FAILED,
                digest={"error": f"capture task crash: {type(exc).__name__}: {exc}"},
                effective_model=row.effective_model,
            )

    # ── boot sweep (D3) ───────────────────────────────────────────────

    async def sweep_orphaned_running(self) -> int:
        """Mark orphaned ``running`` rows ``interrupted`` (boot sweep).

        One idempotent startup query — wired where
        ``JobRecoveryService.recover_on_startup`` runs (``daemon/api.py``),
        scoped to the ONE snapshots table (zero job-system coupling —
        D1 hard requirement).
        """
        return await asyncio.to_thread(
            self._snapshots.mark_orphaned_running_interrupted
        )

    # ── staleness consumer seam (Wave 2 calls this) ───────────────────

    async def staleness_report(
        self, snapshot_id: str
    ) -> dict[str, Any] | None:
        """Compute the §5.2 staleness report for one snapshot.

        Performs the post-capture-advance single DB read (target's
        CURRENT status) and delegates to
        :func:`compute_staleness_report`.
        """
        snapshot = await asyncio.to_thread(self._snapshots.get, snapshot_id)
        if snapshot is None:
            return None
        instance_repo = getattr(self._manager, "_instance_repository", None)
        target_row = (
            await asyncio.to_thread(instance_repo.get, snapshot.target_instance_id)
            if instance_repo is not None
            else None
        )
        return compute_staleness_report(
            snapshot,
            target_instance_status=target_row.status if target_row else None,
            current_runtime_version=_runtime_version(),
        )

    # ── metadata reads for tags ───────────────────────────────────────

    def _resolve_target_agent_id(self, target_instance_id: str) -> str | None:
        repo = getattr(self._manager, "_instance_repository", None)
        row = repo.get(target_instance_id) if repo is not None else None
        return row.agent_id if row else None

    def _resolve_spawned_from(self, target_instance_id: str) -> str | None:
        repo = getattr(self._manager, "_instance_repository", None)
        row = repo.get(target_instance_id) if repo is not None else None
        return (row.instance_metadata or {}).get("spawned_from_snapshot_id") if row else None
