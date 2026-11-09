"""E4 — the stale/shadow DB skill DEACTIVATION mechanism (rule-based, reversible).

WHY THIS EXISTS (skill-system-revival Phase 1, workstream E4, plan §9)

The lever is **TWO COLUMNS THAT CAN DESYNC**: ``skills.is_active`` (bool) and
``skills.status`` (varchar). The Phase-0 census proved the desync:
``(True,'active')=538 · (True,'ab_testing')=52 · (False,'inactive')=39 ·
(False,'ab_testing')=1`` — the last group is a half-dead row. ANY deactivation
MUST set BOTH columns; setting one leaves a half-dead row.

The house writer is :meth:`SkillRepository.deactivate`
(``daemon/repositories/skill/repository.py``) — it sets both columns in one
call. This module never writes one column without the other: every write
goes through :func:`write_skill_pair` (ONE repository call, BOTH columns) or
through the sanctioned ``deactivate()`` wrapper.

THE RULES (DS-11 — RULE-BASED, NEVER AN ID LIST)

============= ========================================================= ==========
Rule         Predicate (a QUERY, never an id list)                    Action
============= ========================================================= ==========
``R1``       two or more ACTIVE rows sharing ``(project_id, name)``    **REFUSE**
             with a shared ``ab_test_group`` AND a ``skill_ab_tests``   (A/B
             row whose ``resolved_at IS NULL``                        guard)
``R2``       ``lineage_origin='captured'`` AND ``is_active=false``    re-affirm
             AND ``last_used_at IS NULL``                             the pair
``R3``       the legacy DB gate-runner copies, matched by a            deactivate
             NAME + CONTENT signature, ACTIVE, not A/B-guarded and
             superseded by a newer active same-name sibling
``R5``       ``is_active=false`` AND ``status <> 'inactive'``          normalize
             (the census' DESYNC PROOF — a half-dead row)             the pair
============= ========================================================= ==========

``R5`` is an **addition** to the plan's R1/R2/R3 (reported): the plan's R2
covers only captured-origin rows, so without R5 the census' single
``(False,'ab_testing')`` row would survive every boot. R5 NEVER flips an
active row, never re-activates and never deletes — it normalises the STATUS
string of a row that is already inactive.

THE A/B GUARD (R3.6)

A row whose A/B test is unresolved (``resolved_at IS NULL``) is REFUSED: deactivating
a variant of a live A/B test silently picks a winner. The refusal is
recorded in the receipt with the **blocking test id** so the reason is
visible and enumerable.

NO WINNERS ARE DECLARED

The 27-pair A/B backlog is the Emperor's call (plan Ruling E / open item
E-6). :meth:`SkillStaleDeactivator.resolve_pair` REFUSES to act without an
explicit ``winner_skill_id`` — the shipped code never supplies one.

THE RECEIPT (R3.3 / E4.4)

``<data_dir>/skill_stale_deactivation_receipt.json`` — a FILE, never a DB
table (a table would need a ``.sql`` migration, and the migration runner is
a documented no-op on PostgreSQL — DS-10). The receipt carries, per touched
row, the **PRIOR** ``(is_active, status)`` pair, so the operation is exactly
reversible (``--reactivate <receipt>`` restores the PAIR, not just
``is_active``). Atomic temp+rename on write; rotated copies
``<receipt>.<run_id>.json`` keep the last 5.

THE SUPPRESSION MECHANISM (R3.5 — "process-once suppression")

The receipt IS the suppression store: a ``(skill_id, rule_id)`` key that is
already present in the receipt is SUPPRESSED — the boot reconciliation never
re-applies it. The key is **file-backed**, so it survives a process restart
(it is not RAM-only). ``--reactivate`` restores the prior pair AND records
``reactivated_at`` while KEEPING the suppression, so an operator's reversal
is not undone by the next boot (a genuinely re-stale row is a new incident
requiring a new receipt entry).

Env note: this module is import-time inert (no I/O at import, no registry
side effects). The data-dir resolution reuses the house chain
``ENSEMBLE_DATA_DIR > DATA_DIR > ./data`` via the skill-lane helper
``daemon.services.skill_capture_observability.resolve_data_dir``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from daemon.repositories.skill.models import Skill, SkillABTest
from daemon.services.skill_capture_observability import resolve_data_dir
from daemon.services.skill_deactivation_manifest import (
    DECLARED_DISPOSITIONS,
    RULE_DECLARED,
    match_declared_disposition,
)

logger = logging.getLogger(__name__)


# ============================================================
# Constants
# ============================================================

#: R1 — same-name active shadowing inside an unresolved A/B group.
RULE_R1 = "R1_SAME_NAME_ACTIVE_SHADOW"
#: R2 — captured-origin, never used (already inactive; re-affirm only).
RULE_R2 = "R2_CAPTURED_NEVER_USED"
#: R3 — the legacy DB gate-runner copies (NAME + CONTENT signature).
RULE_R3 = "R3_LEGACY_GATE_RUNNER_COPY"
#: R5 — a desynced inactive pair (the census' desync proof).
RULE_R5 = "R5_DESYNCED_INACTIVE_PAIR"

#: Every rule this module can apply, in evaluation order.
ALL_RULES: tuple[str, ...] = (RULE_R1, RULE_R2, RULE_R3, RULE_R5)

#: The refusal reason recorded for an A/B-guarded row (R3.6).
REFUSAL_OPEN_AB_TEST = "skipped_open_ab_test"
#: The refusal reason recorded when the pair-resolver has no winner.
REFUSAL_NO_WINNER_DECISION = "skipped_no_winner_decision"

#: The receipt file name inside the data dir.
RECEIPT_FILENAME = "skill_stale_deactivation_receipt.json"
#: The candidate-set manifest file name inside the data dir.
CANDIDATE_MANIFEST_FILENAME = "skill_stale_candidate_manifest.json"
#: How many rotated receipt copies are kept next to the live receipt.
RECEIPT_ROTATION_KEEP = 5

#: The canonical inactive pair — the ONLY pair a deactivation writes.
CANONICAL_INACTIVE = (False, "inactive")
#: The canonical active pair.
CANONICAL_ACTIVE = (True, "active")

#: R3's NAME signature — the canon gate-runner skill names the Phase-0
#: census named. This is a NAME signature (a QUERY INPUT), never a skill-id
#: list (DS-11): the plan says R3 is "identified by a content/name signature
#: query, never by the commission's ids".
R3_NAME_SIGNATURE: tuple[str, ...] = (
    "whole-tree-base-proven-diff",
    "regression-ab-partition-run",
    "failed-digest-equality-gate",
)

#: R3's CONTENT signature — a legacy DB gate-runner copy carries the
#: gate-runner marker in its body. A row that matches the NAME signature but
#: not the CONTENT marker is observed, never deactivated.
R3_CONTENT_SIGNATURE: tuple[str, ...] = (
    "gate-runner",
    "gate runner",
)


def _now_iso() -> str:
    """The house ISO-8601 UTC timestamp shape."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ============================================================
# The dual-column value objects
# ============================================================


@dataclass(frozen=True)
class SkillPair:
    """The two-column active-state pair of one ``skills`` row.

    ``is_active`` (bool) and ``status`` (varchar) are the lever the census
    proved can desync; this type exists so a pair is always carried, read
    and written as ONE value — never one column without the other.
    """

    is_active: bool
    status: str

    def to_json(self) -> dict[str, Any]:
        return {"is_active": self.is_active, "status": self.status}

    @classmethod
    def from_row(cls, row: Any) -> "SkillPair":
        return cls(
            is_active=bool(getattr(row, "is_active", False)),
            status=str(getattr(row, "status", "") or ""),
        )


@dataclass
class DeactivationEntry:
    """One TOUCHED row — the reversible unit of the receipt."""

    skill_id: str
    name: str
    rule_id: str
    before: SkillPair
    after: SkillPair
    touched_at_iso: str
    run_id: str
    project_id: Optional[str] = None
    reason: str = "rule_match"
    reactivated_at: Optional[str] = None

    def to_json(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "rule_id": self.rule_id,
            "before": self.before.to_json(),
            "after": self.after.to_json(),
            "touched_at_iso": self.touched_at_iso,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "reason": self.reason,
            "reactivated_at": self.reactivated_at,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "DeactivationEntry":
        return cls(
            skill_id=str(payload.get("skill_id") or ""),
            name=str(payload.get("name") or ""),
            rule_id=str(payload.get("rule_id") or ""),
            before=SkillPair(
                is_active=bool((payload.get("before") or {}).get("is_active")),
                status=str((payload.get("before") or {}).get("status") or ""),
            ),
            after=SkillPair(
                is_active=bool((payload.get("after") or {}).get("is_active")),
                status=str((payload.get("after") or {}).get("status") or ""),
            ),
            touched_at_iso=str(payload.get("touched_at_iso") or ""),
            run_id=str(payload.get("run_id") or ""),
            project_id=payload.get("project_id"),
            reason=str(payload.get("reason") or "rule_match"),
            reactivated_at=payload.get("reactivated_at"),
        )


@dataclass
class RefusalEntry:
    """One REFUSED row — visible, enumerated, and reversible."""

    skill_id: str
    name: str
    rule_id: str
    reason: str
    observed_at_iso: str
    run_id: str
    project_id: Optional[str] = None
    blocking_test_id: Optional[str] = None
    blocking_test_group: Optional[str] = None
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "observed_at_iso": self.observed_at_iso,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "blocking_test_id": self.blocking_test_id,
            "blocking_test_group": self.blocking_test_group,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RefusalEntry":
        return cls(
            skill_id=str(payload.get("skill_id") or ""),
            name=str(payload.get("name") or ""),
            rule_id=str(payload.get("rule_id") or ""),
            reason=str(payload.get("reason") or ""),
            observed_at_iso=str(payload.get("observed_at_iso") or ""),
            run_id=str(payload.get("run_id") or ""),
            project_id=payload.get("project_id"),
            blocking_test_id=payload.get("blocking_test_id"),
            blocking_test_group=payload.get("blocking_test_group"),
            detail=str(payload.get("detail") or ""),
        )


@dataclass
class ObservationEntry:
    """One IDENTIFIED-BUT-NOT-TOUCHED row (the audit trail of a no-op)."""

    skill_id: str
    name: str
    rule_id: str
    outcome: str
    observed_at_iso: str
    run_id: str
    project_id: Optional[str] = None
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "rule_id": self.rule_id,
            "outcome": self.outcome,
            "observed_at_iso": self.observed_at_iso,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ObservationEntry":
        return cls(
            skill_id=str(payload.get("skill_id") or ""),
            name=str(payload.get("name") or ""),
            rule_id=str(payload.get("rule_id") or ""),
            outcome=str(payload.get("outcome") or ""),
            observed_at_iso=str(payload.get("observed_at_iso") or ""),
            run_id=str(payload.get("run_id") or ""),
            project_id=payload.get("project_id"),
            detail=str(payload.get("detail") or ""),
        )


@dataclass
class DeactivationReceipt:
    """The on-disk receipt — the reversible record of every touched row."""

    version: int = 1
    run_id: str = ""
    updated_at_iso: str = ""
    entries: list[DeactivationEntry] = field(default_factory=list)
    refusals: list[RefusalEntry] = field(default_factory=list)
    observations: list[ObservationEntry] = field(default_factory=list)

    # ── suppression ──────────────────────────────────────────
    def suppression_keys(self) -> set[tuple[str, str]]:
        """The persisted ``(skill_id, rule_id)`` suppression set.

        File-backed (the receipt lives in the data dir), so it survives a
        process restart — this is the "process-once suppression" of R3.5.
        """
        return {
            (entry.skill_id, entry.rule_id)
            for entry in self.entries
        }

    def is_suppressed(self, skill_id: str, rule_id: str) -> bool:
        return (skill_id, rule_id) in self.suppression_keys()

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "updated_at_iso": self.updated_at_iso,
            "entries": [entry.to_json() for entry in self.entries],
            "refusals": [refusal.to_json() for refusal in self.refusals],
            "observations": [
                observation.to_json() for observation in self.observations
            ],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "DeactivationReceipt":
        return cls(
            version=int(payload.get("version") or 1),
            run_id=str(payload.get("run_id") or ""),
            updated_at_iso=str(payload.get("updated_at_iso") or ""),
            entries=[
                DeactivationEntry.from_json(item)
                for item in (payload.get("entries") or [])
            ],
            refusals=[
                RefusalEntry.from_json(item)
                for item in (payload.get("refusals") or [])
            ],
            observations=[
                ObservationEntry.from_json(item)
                for item in (payload.get("observations") or [])
            ],
        )


@dataclass
class ReconciliationReport:
    """The result of one reconciliation pass."""

    run_id: str
    dry_run: bool
    entries: list[DeactivationEntry] = field(default_factory=list)
    refusals: list[RefusalEntry] = field(default_factory=list)
    observations: list[ObservationEntry] = field(default_factory=list)
    suppressed: list[str] = field(default_factory=list)
    receipt_path: Optional[str] = None
    candidate_manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def touched_count(self) -> int:
        return len(self.entries)

    @property
    def refused_count(self) -> int:
        return len(self.refusals)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "touched": [entry.to_json() for entry in self.entries],
            "refusals": [refusal.to_json() for refusal in self.refusals],
            "observations": [
                observation.to_json() for observation in self.observations
            ],
            "suppressed": list(self.suppressed),
            "receipt_path": self.receipt_path,
            "candidate_manifest": self.candidate_manifest,
        }


# ============================================================
# The WRITER — ONE flag, BOTH columns
# ============================================================


def write_skill_pair(
    skill_repo: Any,
    skill_id: str,
    *,
    is_active: bool,
    status: str,
) -> Any:
    """Write BOTH columns of a skill row in ONE repository call.

    This is the ONLY writer in the E4 mechanism. It never writes one column
    without the other (DS-39 / R3.2: "Two ad-hoc UPDATEs are forbidden").

    Args:
        skill_repo: The ``SkillRepository``.
        skill_id: The row to write.
        is_active: The new ``is_active`` value.
        status: The new ``status`` value.

    Returns:
        The updated row, or ``None`` when no row matched.
    """
    updated = skill_repo.update(
        skill_id,
        is_active=bool(is_active),
        status=str(status),
    )
    return updated


def deactivate_skill(skill_repo: Any, skill_id: str) -> Any:
    """The single-flag deactivation — delegates to the SANCTIONED writer.

    ``SkillRepository.deactivate`` sets ``is_active=False`` AND
    ``status='inactive'`` in one call. This wrapper exists so the E4
    mechanism has exactly ONE deactivation path (no ad-hoc second UPDATE).

    Args:
        skill_repo: The ``SkillRepository``.
        skill_id: The row to deactivate.

    Returns:
        The updated row, or ``None`` when no row matched.
    """
    return skill_repo.deactivate(skill_id)


def restore_skill_pair(
    skill_repo: Any,
    skill_id: str,
    pair: SkillPair,
) -> Any:
    """Restore a PRIOR pair (the reversal) in ONE repository call.

    Args:
        skill_repo: The ``SkillRepository``.
        skill_id: The row to restore.
        pair: The prior ``(is_active, status)`` pair.

    Returns:
        The updated row, or ``None`` when no row matched.
    """
    return write_skill_pair(
        skill_repo,
        skill_id,
        is_active=pair.is_active,
        status=pair.status,
    )


# ============================================================
# The receipt store
# ============================================================


def resolve_receipt_path(data_dir: Any = None) -> Path:
    """Resolve the receipt path inside the data dir.

    Precedence: an explicit ``data_dir`` → the house chain
    ``ENSEMBLE_DATA_DIR > DATA_DIR > ./data`` (via the skill-lane helper
    ``daemon.services.skill_capture_observability.resolve_data_dir``).

    The receipt NEVER lands in the repo / planning dir — a per-boot write
    into a tracked tree was RULED OUT.

    Args:
        data_dir: An explicit data dir override (``None`` → the env chain).

    Returns:
        The absolute-ish path of the receipt file.
    """
    base = resolve_data_dir(data_dir)
    return Path(base) / RECEIPT_FILENAME


def resolve_candidate_manifest_path(data_dir: Any = None) -> Path:
    """Resolve the candidate-set manifest path inside the data dir."""
    base = resolve_data_dir(data_dir)
    return Path(base) / CANDIDATE_MANIFEST_FILENAME


def load_receipt(path: Any) -> DeactivationReceipt:
    """Load a receipt from disk. A missing/corrupt file ⇒ an empty receipt."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DeactivationReceipt()
    if not isinstance(payload, dict):
        return DeactivationReceipt()
    return DeactivationReceipt.from_json(payload)


def _atomic_write_json(path: Any, payload: dict[str, Any]) -> Path:
    """Write JSON atomically (temp file + ``os.replace``) in the same dir."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def rotate_receipt(path: Any, run_id: str, keep: int = RECEIPT_ROTATION_KEEP) -> Path:
    """Copy the live receipt to ``<receipt>.<run_id>.json`` and prune old copies."""
    target = Path(path)
    if not target.exists():
        return target
    copy_path = target.with_name(f"{target.name}.{run_id}.json")
    try:
        copy_path.write_text(
            target.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError:  # pragma: no cover — a failed rotation is not fatal
        logger.warning(
            "[SkillDeactivation] receipt rotation copy failed: %s", copy_path
        )
        return target
    copies = sorted(
        target.parent.glob(f"{target.name}.*.json"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
        reverse=True,
    )
    for stale in copies[max(int(keep), 0):]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover
            pass
    return copy_path


def save_receipt(path: Any, receipt: DeactivationReceipt) -> Path:
    """Persist a receipt atomically (and rotate the previous copy)."""
    target = Path(path)
    rotate_receipt(target, receipt.run_id or "unknown")
    return _atomic_write_json(target, receipt.to_json())


# ============================================================
# Rule evaluation — the queries
# ============================================================


@dataclass
class ShadowedSkill:
    """An R1-shaped row: active, same-name, unresolved A/B group."""

    skill: Any
    blocking_test: Optional[SkillABTest] = None
    sibling_ids: tuple[str, ...] = ()

    @property
    def skill_id(self) -> str:
        return str(getattr(self.skill, "id", "") or "")

    @property
    def name(self) -> str:
        return str(getattr(self.skill, "name", "") or "")

    @property
    def project_id(self) -> Optional[str]:
        return getattr(self.skill, "project_id", None)


def _all_skills(skill_repo: Any) -> list[Any]:
    """Every ``skills`` row (dialect-safe ORM read — no raw SQL)."""
    with Session(skill_repo.engine) as session:
        return list(session.exec(select(Skill)))


def _open_ab_tests(ab_test_repo: Any) -> dict[str, SkillABTest]:
    """The UNRESOLVED A/B tests, keyed by group."""
    out: dict[str, SkillABTest] = {}
    for test in ab_test_repo.get_active_tests() or []:
        group = str(getattr(test, "ab_test_group", "") or "")
        if group:
            out[group] = test
    return out


def find_open_ab_shadowed_skills(
    skill_repo: Any,
    ab_test_repo: Any,
    skills: Optional[Iterable[Any]] = None,
) -> list[ShadowedSkill]:
    """Enumerate the R1-shaped rows.

    R1 = two or more rows sharing ``(project_id, name)`` with
    ``is_active=true``, a shared ``ab_test_group``, and a live UNRESOLVED
    ``skill_ab_tests`` row (``resolved_at IS NULL``).

    Args:
        skill_repo: The ``SkillRepository``.
        ab_test_repo: The ``SkillABTestRepository``.
        skills: An optional pre-fetched row list (avoids a second read).

    Returns:
        The R1-shaped rows, each carrying the blocking test.
    """
    rows = list(skills) if skills is not None else _all_skills(skill_repo)
    open_tests = _open_ab_tests(ab_test_repo)

    by_name: dict[tuple[Any, str], list[Any]] = {}
    for row in rows:
        if not bool(getattr(row, "is_active", False)):
            continue
        group = getattr(row, "ab_test_group", None)
        if not group:
            continue
        if str(group) not in open_tests:
            continue
        key = (getattr(row, "project_id", None), str(getattr(row, "name", "")))
        by_name.setdefault(key, []).append(row)

    shadowed: list[ShadowedSkill] = []
    for key, members in by_name.items():
        if len(members) < 2:
            continue
        for member in members:
            group = str(getattr(member, "ab_test_group", "") or "")
            shadowed.append(
                ShadowedSkill(
                    skill=member,
                    blocking_test=open_tests.get(group),
                    sibling_ids=tuple(
                        str(getattr(other, "id", "") or "")
                        for other in members
                        if other is not member
                    ),
                )
            )
    return shadowed


def is_open_ab_shadowed(
    skill: Any,
    skill_repo: Any,
    ab_test_repo: Any,
) -> tuple[bool, Optional[str]]:
    """Is *skill* an R1-shaped row? (used by the E4.3 injection filter)

    Args:
        skill: The candidate row.
        skill_repo: The ``SkillRepository``.
        ab_test_repo: The ``SkillABTestRepository``.

    Returns:
        ``(shadowed, blocking_test_id)``.
    """
    group = getattr(skill, "ab_test_group", None)
    if not group:
        return (False, None)
    if not bool(getattr(skill, "is_active", False)):
        return (False, None)
    test = ab_test_repo.get_by_group(str(group))
    if test is None or getattr(test, "resolved_at", None) is not None:
        return (False, None)
    variants = skill_repo.get_ab_variants(str(group)) or []
    same_name_active = [
        variant
        for variant in variants
        if bool(getattr(variant, "is_active", False))
        and str(getattr(variant, "name", "")) == str(getattr(skill, "name", ""))
        and getattr(variant, "project_id", None)
        == getattr(skill, "project_id", None)
    ]
    if len(same_name_active) >= 2:
        return (True, str(getattr(test, "id", "") or ""))
    return (False, None)


def _r3_signature_match(row: Any) -> bool:
    """True iff *row* matches R3's NAME + CONTENT signature."""
    name = str(getattr(row, "name", "") or "")
    if name not in R3_NAME_SIGNATURE:
        return False
    content = str(getattr(row, "content", "") or "").lower()
    return any(marker in content for marker in R3_CONTENT_SIGNATURE)


# ============================================================
# The engine
# ============================================================


class PairResolutionRefused(Exception):
    """Raised when a pair resolution is attempted without a winner decision."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class SkillStaleDeactivator:
    """The E4 mechanism — plan, apply, reverse, and resolve pairs.

    Args:
        skill_repo: The ``SkillRepository`` (the sanctioned writer).
        ab_test_repo: The ``SkillABTestRepository``.
        data_dir: An explicit data dir override (``None`` → the env chain).
        receipt_path: An explicit receipt path override.
        allow_declared_artifact: Opt-in override that permits the operator
            path to touch a row carrying a declared disposition. Defaults to
            ``False`` — the shipped reconciliation NEVER touches a declared
            artifact.
    """

    def __init__(
        self,
        skill_repo: Any,
        ab_test_repo: Any,
        *,
        data_dir: Any = None,
        receipt_path: Any = None,
        allow_declared_artifact: bool = False,
    ) -> None:
        self._skill_repo = skill_repo
        self._ab_test_repo = ab_test_repo
        self._data_dir = data_dir
        self._allow_declared_artifact = allow_declared_artifact
        self._receipt_path = Path(
            receipt_path
            if receipt_path is not None
            else resolve_receipt_path(data_dir)
        )

    # ── paths ───────────────────────────────────────────────
    @property
    def receipt_path(self) -> Path:
        """The receipt path this instance writes."""
        return self._receipt_path

    @property
    def candidate_manifest_path(self) -> Path:
        """The candidate-set manifest path this instance writes."""
        if self._data_dir is not None:
            return resolve_candidate_manifest_path(self._data_dir)
        return self._receipt_path.with_name(CANDIDATE_MANIFEST_FILENAME)

    # ── the pass ────────────────────────────────────────────
    def reconcile(self, *, dry_run: bool = True) -> ReconciliationReport:
        """Run ONE reconciliation pass.

        A dry run (the default) NEVER writes: no row is touched and no
        receipt is written. An apply pass writes the matched rows, appends
        their receipt entries and persists the receipt atomically.

        Args:
            dry_run: ``True`` (default) ⇒ plan only.

        Returns:
            The :class:`ReconciliationReport`.
        """
        run_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        receipt = load_receipt(self._receipt_path)
        suppression = receipt.suppression_keys()

        rows = _all_skills(self._skill_repo)
        open_tests = _open_ab_tests(self._ab_test_repo)

        entries: list[DeactivationEntry] = []
        refusals: list[RefusalEntry] = []
        observations: list[ObservationEntry] = []
        suppressed: list[str] = []
        candidates: list[dict[str, Any]] = []

        shadowed = find_open_ab_shadowed_skills(
            self._skill_repo, self._ab_test_repo, rows
        )
        shadowed_ids = {item.skill_id for item in shadowed}

        # ── R1 — same-name active shadowing in an unresolved A/B group ──
        for item in shadowed:
            skill = item.skill
            before = SkillPair.from_row(skill)
            if (item.skill_id, RULE_R1) in suppression:
                suppressed.append(item.skill_id)
                candidates.append(
                    _candidate_row(
                        skill,
                        RULE_R1,
                        "suppressed_by_receipt",
                        blocking_test=item.blocking_test,
                    )
                )
                continue
            test = item.blocking_test
            refusals.append(
                RefusalEntry(
                    skill_id=item.skill_id,
                    name=item.name,
                    rule_id=RULE_R1,
                    reason=REFUSAL_OPEN_AB_TEST,
                    observed_at_iso=now,
                    run_id=run_id,
                    project_id=item.project_id,
                    blocking_test_id=(
                        str(getattr(test, "id", "") or "") if test else None
                    ),
                    blocking_test_group=(
                        str(getattr(test, "ab_test_group", "") or "")
                        if test
                        else None
                    ),
                    detail=(
                        "active same-name shadowing inside an unresolved A/B "
                        "group - refusing to deactivate (a deactivation would "
                        "silently pick a winner)"
                    ),
                )
            )
            candidates.append(
                _candidate_row(
                    skill,
                    RULE_R1,
                    "refused_open_ab_test",
                    blocking_test=item.blocking_test,
                    before=before,
                )
            )

        # ── R2 — captured-origin, never used (re-affirm only) ──
        for row in rows:
            if str(getattr(row, "lineage_origin", "") or "") != "captured":
                continue
            if bool(getattr(row, "is_active", False)):
                continue
            if getattr(row, "last_used_at", None) is not None:
                continue
            before = SkillPair.from_row(row)
            skill_id = str(getattr(row, "id", "") or "")
            if (skill_id, RULE_R2) in suppression:
                suppressed.append(skill_id)
                continue
            if before == SkillPair(*CANONICAL_INACTIVE):
                observations.append(
                    ObservationEntry(
                        skill_id=skill_id,
                        name=str(getattr(row, "name", "") or ""),
                        rule_id=RULE_R2,
                        outcome="already_inactive",
                        observed_at_iso=now,
                        run_id=run_id,
                        project_id=getattr(row, "project_id", None),
                        detail="captured-origin, never used - pair already canonical",
                    )
                )
                candidates.append(
                    _candidate_row(row, RULE_R2, "already_inactive", before=before)
                )
                continue
            entries.append(
                DeactivationEntry(
                    skill_id=skill_id,
                    name=str(getattr(row, "name", "") or ""),
                    rule_id=RULE_R2,
                    before=before,
                    after=SkillPair(*CANONICAL_INACTIVE),
                    touched_at_iso=now,
                    run_id=run_id,
                    project_id=getattr(row, "project_id", None),
                    reason="captured_origin_pair_normalised",
                )
            )
            candidates.append(
                _candidate_row(row, RULE_R2, "pair_normalised", before=before)
            )

        # ── R3 — the legacy DB gate-runner copies (NAME + CONTENT signature) ──
        for row in rows:
            if not _r3_signature_match(row):
                continue
            if not bool(getattr(row, "is_active", False)):
                continue
            skill_id = str(getattr(row, "id", "") or "")
            name = str(getattr(row, "name", "") or "")
            if skill_id in shadowed_ids:
                # Already accounted for by R1 (refused) - never double-handled.
                continue
            if (skill_id, RULE_R3) in suppression:
                suppressed.append(skill_id)
                continue
            group = str(getattr(row, "ab_test_group", "") or "")
            blocking = open_tests.get(group) if group else None
            if blocking is not None:
                refusals.append(
                    RefusalEntry(
                        skill_id=skill_id,
                        name=name,
                        rule_id=RULE_R3,
                        reason=REFUSAL_OPEN_AB_TEST,
                        observed_at_iso=now,
                        run_id=run_id,
                        project_id=getattr(row, "project_id", None),
                        blocking_test_id=str(getattr(blocking, "id", "") or ""),
                        blocking_test_group=group,
                        detail="R3 signature match inside an unresolved A/B group",
                    )
                )
                candidates.append(
                    _candidate_row(
                        row, RULE_R3, "refused_open_ab_test",
                        blocking_test=blocking,
                    )
                )
                continue
            newer = _has_newer_active_same_name_sibling(row, rows)
            if not newer:
                observations.append(
                    ObservationEntry(
                        skill_id=skill_id,
                        name=name,
                        rule_id=RULE_R3,
                        outcome="not_stale",
                        observed_at_iso=now,
                        run_id=run_id,
                        project_id=getattr(row, "project_id", None),
                        detail=(
                            "legacy gate-runner signature match, but no newer "
                            "active same-name sibling - NOT stale, no flip"
                        ),
                    )
                )
                candidates.append(
                    _candidate_row(row, RULE_R3, "not_stale")
                )
                continue
            before = SkillPair.from_row(row)
            entries.append(
                DeactivationEntry(
                    skill_id=skill_id,
                    name=name,
                    rule_id=RULE_R3,
                    before=before,
                    after=SkillPair(*CANONICAL_INACTIVE),
                    touched_at_iso=now,
                    run_id=run_id,
                    project_id=getattr(row, "project_id", None),
                    reason="legacy_gate_runner_copy_superseded",
                )
            )
            candidates.append(
                _candidate_row(row, RULE_R3, "deactivate", before=before)
            )

        # ── R5 — a desynced INACTIVE pair (the census' desync proof) ──
        for row in rows:
            if bool(getattr(row, "is_active", False)):
                continue
            before = SkillPair.from_row(row)
            if before == SkillPair(*CANONICAL_INACTIVE):
                continue
            skill_id = str(getattr(row, "id", "") or "")
            if (skill_id, RULE_R5) in suppression:
                suppressed.append(skill_id)
                continue
            declared = match_declared_disposition(row)
            if declared is not None and not self._allow_declared_artifact:
                refusals.append(
                    RefusalEntry(
                        skill_id=skill_id,
                        name=str(getattr(row, "name", "") or ""),
                        rule_id=RULE_R5,
                        reason="skipped_declared_disposition",
                        observed_at_iso=now,
                        run_id=run_id,
                        project_id=getattr(row, "project_id", None),
                        detail=declared.disposition,
                    )
                )
                candidates.append(
                    _candidate_row(row, RULE_R5, "refused_declared_disposition")
                )
                continue
            entries.append(
                DeactivationEntry(
                    skill_id=skill_id,
                    name=str(getattr(row, "name", "") or ""),
                    rule_id=RULE_R5,
                    before=before,
                    after=SkillPair(*CANONICAL_INACTIVE),
                    touched_at_iso=now,
                    run_id=run_id,
                    project_id=getattr(row, "project_id", None),
                    reason="desynced_inactive_pair_normalised",
                )
            )
            candidates.append(
                _candidate_row(row, RULE_R5, "pair_normalised", before=before)
            )

        # ── the DECLARED dispositions (never rule-derived) ──
        for declared in DECLARED_DISPOSITIONS:
            for row in rows:
                if str(getattr(row, "name", "") or "") != declared.name:
                    continue
                candidates.append(
                    {
                        "skill_id": str(getattr(row, "id", "") or ""),
                        "name": declared.name,
                        "project_id": getattr(row, "project_id", None),
                        "rule_id": RULE_DECLARED,
                        "disposition": declared.disposition,
                        "outcome": "declared_tooling_artifact",
                        "injection": declared.injection,
                        "deactivation": declared.deactivation,
                    }
                )

        manifest = {
            "version": 1,
            "run_id": run_id,
            "generated_at_iso": now,
            "dry_run": bool(dry_run),
            "rules": list(ALL_RULES),
            "candidates": candidates,
        }

        if dry_run:
            return ReconciliationReport(
                run_id=run_id,
                dry_run=True,
                entries=entries,
                refusals=refusals,
                observations=observations,
                suppressed=suppressed,
                receipt_path=str(self._receipt_path),
                candidate_manifest=manifest,
            )

        # ── APPLY ─────────────────────────────────────────────
        touched: list[DeactivationEntry] = []
        for entry in entries:
            declared = match_declared_disposition(
                _skill_by_id(self._skill_repo, entry.skill_id)
            )
            if declared is not None and not self._allow_declared_artifact:
                refusals.append(
                    RefusalEntry(
                        skill_id=entry.skill_id,
                        name=entry.name,
                        rule_id=entry.rule_id,
                        reason="skipped_declared_disposition",
                        observed_at_iso=now,
                        run_id=run_id,
                        project_id=entry.project_id,
                        detail=declared.disposition,
                    )
                )
                continue
            if entry.rule_id == RULE_R1:
                # Defensive: R1 is a REFUSAL rule - never written.
                continue
            if entry.after == SkillPair(*CANONICAL_INACTIVE) and (
                entry.before.is_active
            ):
                deactivate_skill(self._skill_repo, entry.skill_id)
            else:
                write_skill_pair(
                    self._skill_repo,
                    entry.skill_id,
                    is_active=entry.after.is_active,
                    status=entry.after.status,
                )
            touched.append(entry)

        receipt.entries.extend(touched)
        receipt.refusals.extend(refusals)
        receipt.observations.extend(observations)
        receipt.run_id = run_id
        receipt.updated_at_iso = now
        save_receipt(self._receipt_path, receipt)
        try:
            _atomic_write_json(self.candidate_manifest_path, manifest)
        except OSError:  # pragma: no cover — the manifest is an audit extra
            logger.warning(
                "[SkillDeactivation] candidate manifest write failed: %s",
                self.candidate_manifest_path,
            )

        return ReconciliationReport(
            run_id=run_id,
            dry_run=False,
            entries=touched,
            refusals=refusals,
            observations=observations,
            suppressed=suppressed,
            receipt_path=str(self._receipt_path),
            candidate_manifest=manifest,
        )

    # ── the reversal ────────────────────────────────────────
    def reactivate(
        self,
        receipt_path: Any = None,
        *,
        apply: bool = True,
    ) -> list[dict[str, Any]]:
        """Reverse every entry of a receipt — restoring the PRIOR PAIR.

        The reversal restores BOTH columns of the prior pair (not just
        ``is_active``) and records ``reactivated_at`` on the entry while
        KEEPING the suppression, so the next boot does not undo it.

        Args:
            receipt_path: The receipt to reverse (defaults to this
                instance's receipt path).
            apply: ``False`` ⇒ plan only (no writes).

        Returns:
            One dict per reversed row.
        """
        target = Path(receipt_path) if receipt_path is not None else self._receipt_path
        receipt = load_receipt(target)
        results: list[dict[str, Any]] = []
        now = _now_iso()
        for entry in receipt.entries:
            if entry.reactivated_at:
                results.append(
                    {
                        "skill_id": entry.skill_id,
                        "name": entry.name,
                        "rule_id": entry.rule_id,
                        "outcome": "already_reactivated",
                        "reactivated_at": entry.reactivated_at,
                    }
                )
                continue
            if apply:
                restore_skill_pair(self._skill_repo, entry.skill_id, entry.before)
                entry.reactivated_at = now
            results.append(
                {
                    "skill_id": entry.skill_id,
                    "name": entry.name,
                    "rule_id": entry.rule_id,
                    "before": entry.before.to_json(),
                    "outcome": "reactivated" if apply else "would_reactivate",
                }
            )
        if apply:
            receipt.updated_at_iso = now
            save_receipt(target, receipt)
        return results

    # ── the pair resolver (NO WINNER IS EVER DECLARED HERE) ──
    def resolve_pair(
        self,
        *,
        ab_test_group: str,
        winner_skill_id: Optional[str] = None,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Resolve one A/B pair as ONE reversible unit.

        The winner decision is the Emperor's (plan Ruling E / open item
        E-6). This method REFUSES to act without an explicit
        ``winner_skill_id`` — the shipped code never supplies one.

        Args:
            ab_test_group: The shared group UUID.
            winner_skill_id: The explicit winner decision. ``None`` ⇒ the
                call is REFUSED (reason ``skipped_no_winner_decision``).
            apply: ``False`` (default) ⇒ plan only.

        Returns:
            The resolution plan (or the applied result).

        Raises:
            PairResolutionRefused: When no winner decision is supplied, or
                the winner is not a variant of the group.
        """
        test = self._ab_test_repo.get_by_group(ab_test_group)
        if test is None:
            raise PairResolutionRefused(
                "skipped_no_such_ab_test",
                f"no skill_ab_tests row for group {ab_test_group}",
            )
        if not winner_skill_id:
            raise PairResolutionRefused(
                REFUSAL_NO_WINNER_DECISION,
                (
                    "the winner decision is the orchestrator's - the E4 "
                    "mechanism refuses to declare one"
                ),
            )
        variants = {
            str(getattr(test, "skill_id_old", "") or ""),
            str(getattr(test, "skill_id_new", "") or ""),
        }
        if winner_skill_id not in variants:
            raise PairResolutionRefused(
                "skipped_winner_not_a_variant",
                f"{winner_skill_id} is not a variant of {ab_test_group}",
            )
        loser_ids = [vid for vid in variants if vid and vid != winner_skill_id]
        plan: list[dict[str, Any]] = []
        for loser_id in loser_ids:
            row = _skill_by_id(self._skill_repo, loser_id)
            if row is None:
                continue
            before = SkillPair.from_row(row)
            plan.append(
                {
                    "skill_id": loser_id,
                    "name": str(getattr(row, "name", "") or ""),
                    "before": before.to_json(),
                    "after": SkillPair(*CANONICAL_INACTIVE).to_json(),
                }
            )
        result = {
            "ab_test_group": ab_test_group,
            "test_id": str(getattr(test, "id", "") or ""),
            "winner_skill_id": winner_skill_id,
            "loser_plan": plan,
            "applied": False,
        }
        if not apply:
            return result
        # APPLY — the test row and the loser rows move as ONE unit.
        before_test = {
            "resolved_at": getattr(test, "resolved_at", None),
            "winner_skill_id": getattr(test, "winner_skill_id", None),
        }
        self._ab_test_repo.resolve(ab_test_group, winner_skill_id)
        for item in plan:
            deactivate_skill(self._skill_repo, item["skill_id"])
        result["applied"] = True
        result["before_test"] = before_test
        return result


# ============================================================
# Helpers
# ============================================================


def _skill_by_id(skill_repo: Any, skill_id: str) -> Any:
    with Session(skill_repo.engine) as session:
        return session.get(Skill, skill_id)


def _has_newer_active_same_name_sibling(row: Any, rows: list[Any]) -> bool:
    """True iff *row* has a NEWER active same-name sibling in its project."""
    row_created = str(getattr(row, "created_at", "") or "")
    for other in rows:
        if other is row:
            continue
        if not bool(getattr(other, "is_active", False)):
            continue
        if str(getattr(other, "name", "") or "") != str(
            getattr(row, "name", "") or ""
        ):
            continue
        if getattr(other, "project_id", None) != getattr(row, "project_id", None):
            continue
        other_created = str(getattr(other, "created_at", "") or "")
        if other_created > row_created:
            return True
    return False


def _candidate_row(
    row: Any,
    rule_id: str,
    outcome: str,
    *,
    before: Optional[SkillPair] = None,
    blocking_test: Optional[Any] = None,
) -> dict[str, Any]:
    """Build one candidate-set manifest row."""
    payload: dict[str, Any] = {
        "skill_id": str(getattr(row, "id", "") or ""),
        "name": str(getattr(row, "name", "") or ""),
        "project_id": getattr(row, "project_id", None),
        "rule_id": rule_id,
        "outcome": outcome,
    }
    if before is not None:
        payload["before"] = before.to_json()
    if blocking_test is not None:
        payload["blocking_test_id"] = str(getattr(blocking_test, "id", "") or "")
        payload["blocking_test_group"] = str(
            getattr(blocking_test, "ab_test_group", "") or ""
        )
    return payload
