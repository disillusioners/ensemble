"""MissionRecordService — the single writer for the mission-record anchor.

Completion-subsystem Proposal B — PR1 of 3 (dry mints). One entry point
per semantic act, exactly as proposal-b §2.2 prescribes:

* :meth:`MissionRecordService.resolve_or_mint` — the 3-rung RESOLUTION
  LADDER behind every commissioning act (commissioning lanes):
  (1) the actor has an OPEN epoch as anchor → that mission;
  (2) the actor has an OUTSTANDING commission row → that commission's
  mission ("work commissioned of you belongs to your commissioner's
  mission");
  (3) else MINT a new epoch anchored on the actor.
  Every commissioning act resolves to exactly one mission — that
  invariant is the semantic core.

* Delivery/resume lanes (boot-sweep, cascade-revive) use the
  mint-if-no-open-epoch shape instead (``_mint_if_no_open_epoch``):
  resume is DELIVERY, not founding — an open epoch continues; only a
  stranded instance (no open epoch) gets an honest boundary row. Rung 2
  is deliberately NOT consulted there (a resumed worker's epoch
  boundary is its own, not its commissioner's).

* :meth:`MissionRecordService.mint_continuation_epoch` — job_continue's
  force-NEW-epoch mint (follow-on job ⇒ new epoch, parent chain, the
  prior OPEN epoch → ``superseded`` RECORD-SIDE ONLY).

* :meth:`MissionRecordService.mint_revive_epoch` — the revive boundary
  (terminal target + p1-HUMAN message; nudge-on-terminal), parent
  chain.

* commission / attestation / settle / amend seams — guarded, guarded,
  guarded (every transition is ``UPDATE ... WHERE state/status=<pre>``,
  the ``dependency_watchers`` exactly-once shape).

SIBLING (name collision — NOT an alias): the legacy mission READ-MODEL
is ``daemon/services/mission_resolver.py:241`` (``MissionRecord`` — a
dataclass whose ``mission_id == instance_id``). It is a DIFFERENT
symbol from the ``mission_records`` TABLE this module owns; PR1 does
not touch it, and no import alias is needed (no module imports both).

MODE LADDER (restart-read, ship default ``dry``)
------------------------------------------------
``ENSEMBLE_MISSION_RECORDS = off | dry | enforce``:

* ``off``     — no mints, no reads. The tables stay untouched.
* ``dry``     — mint + stamp + log everything; ALL read sites (gate,
                census, anchor, routing, backstop) stay on legacy
                behavior. **This is the ship default** — records
                accumulate, zero behavior change (the attestation C2
                soak precedent).
* ``enforce`` — the PR2 read flip: gate/census/routing consult records
                with legacy fallback. NOT IMPLEMENTED IN PR1 (PR1 only
                reserves the vocabulary; minting behaves identically to
                ``dry``).

The mode is resolved ONCE from the environment and CACHED (the house
switch pattern — ``daemon/config.py``'s kill-switch family: the typed
config is for intervals, not switch flips; the operator flips the env
and RESTARTS, and a mid-run env mutation can no longer change a
resolved knob). An UNRECOGNIZED token raises a LOUD ``ValueError``
naming ``ENSEMBLE_MISSION_RECORDS`` — the daemon's boot resolution
site (the manager's mission-record service construction) turns that
into a failed start, exactly like the other kill-switches do. The
lane-facing ``*_fail_open`` wrappers never propagate that error: a
records-side problem must NEVER break a commissioning lane, so they
resolve through the never-raising :func:`_mode_is_off` helper and
proceed record-blind.

ADOPTION DELTA (a) — TIE-BREAK PRECEDENCE (routing authority)
-------------------------------------------------------------
Proposal A's live D1 resolver (:mod:`daemon.services.report_recipient`)
tie-breaks multi-nudge delivery by LATEST-COMMITTED-NUDGE; Proposal C
would use OLDEST-OPEN-FIRST with ``routed_elsewhere`` recording;
Proposal B's commission table nominally assumes a SINGLE outstanding
row (multi-commissioner undefined). PR1 decision: when a worker is
commissioned twice, the second commission MINTS A SECOND OUTSTANDING
ROW — the guarded-state lifecycle (``outstanding`` →
``satisfied``/``abandoned``/``superseded``) is the discriminator, and
the rung-2 lookup takes the OLDEST outstanding row (deterministic).
ROUTING AUTHORITY STAYED WITH THE A-RESOLVER until the PR2/PR3 flip;
**PR3 IS THE FLIP** (ruling ④): report routing now keys on the OLDEST
OUTSTANDING commission row (advisory backfill rows excluded FOREVER)
ahead of the A-resolver, which survives as the DECLARED LEGACY
FALLBACK leg — see ``report_recipient.resolve_report_recipient`` (the
flip lives INSIDE the resolver, so every call site inherits it) and
the report_recipient module docstring ("ADOPTION DELTA (a) — … AMENDED
PR3").

ADOPTION DELTA (b) — HISTORICAL COVERAGE (advisory backfill)
------------------------------------------------------------
:meth:`MissionRecordService.backfill_advisory_nudge_commissions`
covers the existing ``instance_ops_audit`` rows (the tiny historical
surface: 6 rows / 1 committed nudge all-time): committed non-dry
nudges whose target is STILL NON-TERMINAL mint ADVISORY nudge
commission rows with ``provenance='backfill'`` +
``advisory_backfill=True`` + deterministic UUID5 ids — idempotent
(re-run = PK conflict = no-op), dry-run-loggable. Unrecoverable targets
(nudge already completed — the audit row records the actor, not the
outcome linkage) are an ACCEPTED ONE-TIME GAP. **Advisory backfill rows
are EXCLUDED from routing authority FOREVER** — if dropped, the ledger
would be worse than no ledger (the comparison.md §4c rule).

ZERO-BEHAVIOR-CHANGE CONTRACT (the PR1 hard gate)
-------------------------------------------------
* Mints write ONLY the two mission-record tables (+ the
  ``message_metadata.mission_id`` delivery stamp through the existing
  non-load-bearing tap). No instance-status writes, no admission-state
  writes, no attestation-column clears, no ``denial_epochs`` writes —
  the §2.4 epoch-boundary LEDGER-CLEAR POLICY is PR2+; the revive
  lane's EXISTING fresh-episode clear stays exactly as it is (the mint
  RECORDS the boundary; the legacy clear keeps its current behavior).
* The terminal-write census (``tests/job_queue/test_terminal_write_census.py``)
  and the admission-writer census (``daemon/job_state/constitution.py``)
  are UNTOUCHED by PR1 — PR1 adds no writer to either census's surface.
  They are NOT both green on this branch: the terminal-write census
  currently carries the SAME 2 pre-existing base-red failures that were
  A/B-proven at the base commit ``7af1d2632`` (i.e. they are NOT caused
  by PR1 and are not PR1's to fix).
* All gate/census/anchor/routing reads stay legacy.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..repositories.instance.models import Instance, InstanceStatus
from ..repositories.mission_record.models import (
    CommissionLane,
    CommissionState,
    MissionAttestMethod,
    MissionCommission,
    MissionFoundingSource,
    MissionProvenance,
    MissionRecord,
    MissionStatus,
)
from ..repositories.mission_record.repository import (
    MissionRecordError,
    MissionRecordRepository,
)
from ..constants import (
    DEFAULT_MISSION_COMMISSION_TTL_SECONDS,
    MISSION_COMMISSION_TTL_ENV,
)
from .timestamps import now_utc_iso

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)


def _new_uuid4() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Mode ladder
# ---------------------------------------------------------------------------

#: Operator mode env (restart-read; ship default ``dry``).
MISSION_RECORDS_MODE_ENV = "ENSEMBLE_MISSION_RECORDS"

#: The RESERVED ``message_metadata`` key carrying the mission carrier on
#: the persisted MessageQueue row (the F11 ``__ens_dispatch_chain__``
#: precedent): the producer (``_prepare_enqueued_message``) stamps it
#: once; the claim path reads it into ``ProcessingContext.mission_id``.
#: Daemon-internal only — never caller-supplied.
MISSION_ID_KEY = "__ens_mission_id__"

#: The inert extension-point gate for the SCHEDULER lane (lane 9).
#: STANDING PRODUCT RULING: the scheduler lane stays BARE (it must not
#: anchor like a human, and it must not mint like one either — yet).
#: The mint SITE is WIRED — ``SchedulerAdapter._route_via_job_queue``
#: (the scheduler's own durable-enqueue point) calls the fail-open
#: resolve-or-mint behind this gate. The gate is what keeps that site
#: inert until the standing ruling flips; it defaults OFF and there is
#: no plan to flip it in PR1-PR3.
SCHEDULER_MINT_EXTENSION_ENV = "ENSEMBLE_MISSION_RECORDS_SCHEDULER_MINT"


class MissionRecordMode(str):
    """Vocabulary for :data:`MISSION_RECORDS_MODE_ENV` (see module docstring)."""

    OFF = "off"
    DRY = "dry"
    ENFORCE = "enforce"


_VALID_MODES = (MissionRecordMode.OFF, MissionRecordMode.DRY, MissionRecordMode.ENFORCE)


#: The CACHED resolved mode ladder value (restart-read). ``None`` = not
#: resolved yet. The cache is what makes the knob restart-read: a
#: mid-run env mutation can no longer flip a live lane (the bug the
#: independent review flagged). Cleared only by the test-reset hook.
_MISSION_RECORDS_MODE: str | None = None


def resolve_mission_records_mode() -> str:
    """Resolve the mode ladder from the environment ONCE and CACHE it.

    House switch pattern (``daemon/config.py``'s kill-switch family,
    e.g. ``get_symptom_repair_ladder_enabled``): the value is resolved
    once (restart-read) and served from the cache afterwards — the
    operator flips the env and restarts; a mid-run env mutation cannot
    change a resolved knob.

    Default ``dry`` (mints write; reads stay legacy). An UNRECOGNIZED
    token raises a LOUD :class:`ValueError` naming the env var — the
    operator-typo convention (a misconfigured knob must fail the boot
    loudly, not silently degrade). The lane-facing ``*_fail_open``
    wrappers never propagate it (they use :func:`_mode_is_off`), so a
    lane can never break on a records-side problem.
    """
    global _MISSION_RECORDS_MODE
    if _MISSION_RECORDS_MODE is None:
        raw = (os.environ.get(MISSION_RECORDS_MODE_ENV) or "").strip().lower()
        if not raw:
            _MISSION_RECORDS_MODE = MissionRecordMode.DRY
        elif raw in _VALID_MODES:
            _MISSION_RECORDS_MODE = raw
        else:
            raise ValueError(
                f"Invalid {MISSION_RECORDS_MODE_ENV} value {raw!r} — "
                f"expected one of {'|'.join(_VALID_MODES)}"
            )
    return _MISSION_RECORDS_MODE


def mission_records_enforce_enabled() -> bool:
    """``True`` when the mode ladder is ``enforce`` — NEVER raises.

    The single gate every read site (and the epoch-boundary ledger
    clear) consults before it looks at the record. An unrecognized
    ``ENSEMBLE_MISSION_RECORDS`` token degrades to "not enforcing" (the
    resolver itself raises loudly at the boot site; this helper must not
    raise on the gate hot path).

    Lives HERE (the mode ladder's owner) so the reader module and the
    writer module share ONE definition — no import cycle, no drift.
    """
    try:
        return resolve_mission_records_mode() == MissionRecordMode.ENFORCE
    except Exception:  # noqa: BLE001 — never break a gate on a knob fault
        return False


def _reset_mission_records_mode_for_tests() -> None:
    """Clear the cached mode ladder value (unit-test isolation only)."""
    global _MISSION_RECORDS_MODE
    _MISSION_RECORDS_MODE = None


def _mode_is_off() -> bool:
    """``True`` when the mode ladder is ``off`` — NEVER raises.

    The lane-facing ``*_fail_open`` wrappers use this instead of
    calling :func:`resolve_mission_records_mode` directly: the
    resolver raises LOUDLY on an unrecognized token (house switch
    pattern) and a commissioning lane must NEVER break on a
    records-side problem (the fail-open producer posture). The loud
    failure lives at the BOOT resolution site (the manager's
    mission-record service construction), where an operator typo fails
    the daemon start rather than a lane mid-flight.
    """
    try:
        return resolve_mission_records_mode() == MissionRecordMode.OFF
    except ValueError as exc:
        logger.warning(
            "mission_records: mode ladder unresolved (%s) — this lane "
            "proceeds record-blind",
            exc,
        )
        return True


# ---------------------------------------------------------------------------
# Commission TTL horizon (the reconciliation sweep's time-bounded
# fail-MISS backstop — Proposal B PR3)
# ---------------------------------------------------------------------------

#: The CACHED resolved TTL horizon (restart-read, house-switch pattern —
#: the same shape as :func:`resolve_mission_records_mode`). ``None`` =
#: not resolved yet.
_MISSION_COMMISSION_TTL_SECONDS: int | None = None


def resolve_mission_commission_ttl_seconds(
    default: int = DEFAULT_MISSION_COMMISSION_TTL_SECONDS,
) -> int:
    """Resolve the commission TTL horizon from the environment.

    ``ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS`` — the operator knob
    for the reconciliation sweep's time-bounded fail-MISS backstop
    (the design's R2/P3). Default ``21600`` (6h) — the same horizon
    family as ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` (the
    one-horizon principle).

    House-switch pattern: the value is resolved ONCE and cached
    (restart-read) so a mid-run env mutation cannot change a resolved
    knob. An UNRECOGNIZED / non-positive value degrades to the
    ``default`` with a WARNING (never raises): a misconfigured knob must
    not break the sweep thread (the fail-MISS producer posture — a
    raised knob fault would strand every commission forever, which is
    the exact failure the sweep exists to prevent).
    """
    global _MISSION_COMMISSION_TTL_SECONDS
    raw = (os.environ.get(MISSION_COMMISSION_TTL_ENV) or "").strip()
    if not raw:
        # No env override → the caller's configured default applies
        # (per call — the cache holds the ENV value only, so two
        # constructions with different configured defaults do not
        # poison each other).
        return int(default)
    if _MISSION_COMMISSION_TTL_SECONDS is None:
        try:
            candidate = int(raw)
        except ValueError:
            candidate = 0
        if candidate > 0:
            _MISSION_COMMISSION_TTL_SECONDS = candidate
        else:
            logger.warning(
                "mission_records: invalid %s value %r — using the "
                "default %ss",
                MISSION_COMMISSION_TTL_ENV,
                raw,
                default,
            )
            _MISSION_COMMISSION_TTL_SECONDS = int(default)
    return _MISSION_COMMISSION_TTL_SECONDS


def _reset_mission_commission_ttl_for_tests() -> None:
    """Clear the cached TTL horizon (unit-test isolation only)."""
    global _MISSION_COMMISSION_TTL_SECONDS
    _MISSION_COMMISSION_TTL_SECONDS = None


def scheduler_mint_extension_enabled() -> bool:
    """The INERT scheduler extension-point gate (default OFF).

    Standing product ruling — lane 9 stays bare. Even when the mode
    ladder is ``dry``, the scheduler lane mints NOTHING unless this
    gate is explicitly set to ``1``/``true``. The gate exists only so
    the eventual flip is a one-line env change with the wiring already
    in place.

    DELIBERATE DIVERGENCE from the mode ladder above: this gate
    re-reads ``os.environ`` PER CALL (it is NOT cached), so the
    standing ruling can be flipped on a LIVE daemon without a restart.
    The named per-call precedents are
    ``auto_continue_boot_pass.py:176``,
    ``job_recovery_service.py:144``/``:283``,
    ``report_integrity_guard.py:449`` and ``plane_sync_service.py:157``.
    Do NOT cache it — caching would remove the only way to arm the
    gate on a live daemon (the mode ladder's own cache is the house
    switch pattern, but this gate is deliberately NOT one of them).
    """
    raw = (os.environ.get(SCHEDULER_MINT_EXTENSION_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------


@dataclass
class MissionResolution:
    """The exactly-one-mission outcome of the resolution ladder.

    ``rung``: 1 = open-epoch-as-anchor hit; 2 = outstanding-commission
    hit; 3 = minted new epoch. ``created`` is True only on rung 3 (the
    caller can log mint vs reuse — the dry-mode observability channel).
    """

    mission_id: str
    rung: int
    created: bool


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class MissionRecordService:
    """Single writer for ``mission_records`` + ``mission_commissions``.

    Every public write method takes an optional ``session``: when
    provided, the CALLER's transaction is used and NOT committed (the
    revive mint rides ``_prepare_enqueued_message``'s
    ``WriteGuardSession`` so the epoch boundary is atomic with the
    revive); when ``None``, the repository opens + commits its own
    session.

    The lane-facing ``*_fail_open`` wrappers enforce the lane posture:
    a mission-record failure must NEVER fail the commissioning lane
    (the D2(b) watcher-mint posture verbatim). Contract violations
    RAISE out of the non-wrapper methods (D4(ii): a commission whose
    worker row is missing at mint time raises — the mint is the
    semantic contract) and the wrapper catches + logs.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._repo = MissionRecordRepository(engine)

    def commission_repository(self) -> MissionRecordRepository:
        """Public access to the underlying commission/record repository.

        Proposal B PR3 — the RECONCILIATION SWEEP (RDRS lane 7) is a
        READER + the single GUARDED RESOLVER of commission rows; it
        needs the repository's guarded transition primitives
        (``find_outstanding_commissions`` / ``resolve_commission``)
        without reaching into a private attribute.

        The accessors this replaces: ``service._repo`` reach-throughs
        (the reviewer follow-up on the ``_instance_manager._instance_repository``
        family). The repository is the SAME object the service writes
        through — no second writer, no drift.
        """
        return self._repo

    # ── internal validation ──

    def _require_instance_row(
        self,
        instance_id: str,
        *,
        role: str,
        session: Session | None,
    ) -> None:
        """The mint-path existence contract (D4(ii) scope).

        An epoch anchor or a commission worker that does not exist must
        not be recorded — a mission/commission that can never advance
        or be satisfied must not be written as if real. Raises
        :class:`MissionRecordError`.
        """
        if not instance_id:
            raise MissionRecordError(
                f"mission_records: {role} instance id is empty — refusing to mint"
            )
        stmt = select(Instance.instance_id).where(Instance.instance_id == instance_id)
        if session is not None:
            row = session.exec(stmt).first()
        else:
            with Session(self._engine) as own:
                row = own.exec(stmt).first()
        if row is None:
            raise MissionRecordError(
                f"mission_records: {role} instance '{instance_id}' has no "
                f"instance row — refusing to mint (D4(ii) mint-path validation)"
            )

    def _mint_epoch_locked(
        self,
        *,
        actor_instance_id: str,
        founding_source: str,
        session: Session | None,
        founding_message_id: str | None = None,
        commissioner_instance_id: str | None = None,
        work_id: str | None = None,
        parent_mission_id: str | None = None,
        provenance: str = MissionProvenance.LIVE.value,
    ) -> MissionRecord:
        """The actual epoch INSERT (caller resolved already).

        Idempotency: when ``founding_message_id`` is set, the
        ``(anchor, founding_message_id)`` probe + partial unique index
        make the mint idempotent — a concurrent duplicate insert
        surfaces as ``IntegrityError`` and is re-resolved to the winning
        row.
        """
        self._require_instance_row(
            actor_instance_id, role="anchor", session=session
        )
        if founding_message_id:
            existing = self._repo.find_epoch_by_anchor_and_founding_message(
                actor_instance_id, founding_message_id, session=session
            )
            if existing is not None:
                return existing
        epoch_seq = 1 + self._repo.count_epochs_for_anchor(
            actor_instance_id, session=session
        )
        record = MissionRecord(
            anchor_instance_id=actor_instance_id,
            epoch_seq=epoch_seq,
            founding_message_id=founding_message_id,
            founding_source=founding_source,
            commissioner_instance_id=commissioner_instance_id,
            work_id=work_id,
            parent_mission_id=parent_mission_id,
            status=MissionStatus.OPEN.value,
            provenance=provenance,
        )
        try:
            return self._repo.insert_mission(record, session=session)
        except IntegrityError:
            # Lost the partial-unique race — the concurrent mint won.
            # Re-resolve (never mint twice for the same founding act).
            if session is not None:
                # The caller's transaction is poisoned on PG after a
                # statement-level IntegrityError — fail the mint and
                # let the caller's fail-open wrapper skip it. (The
                # revive path re-runs on the next boundary; the window
                # is a same-tick double boundary, which is not a real
                # shape.)
                raise
            # The re-resolve probe is keyed on ``founding_message_id``;
            # a NULL-founding epoch (the boot / backfill / job_continue
            # shape) can NEVER be found by that probe, so the sentinel
            # ``or ""`` it used to carry could only ever miss. Guard the
            # probe on the value being present instead of substituting
            # an empty-string key.
            if founding_message_id is not None:
                existing = self._repo.find_epoch_by_anchor_and_founding_message(
                    actor_instance_id, founding_message_id, session=None
                )
                if existing is not None:
                    return existing
            raise

    # ── the resolution ladder (commissioning lanes) ──

    def resolve_or_mint(
        self,
        actor_instance_id: str,
        lane: str,
        *,
        founding_source: str | None = None,
        founding_message_id: str | None = None,
        commissioner_instance_id: str | None = None,
        work_id: str | None = None,
        session: Session | None = None,
        provenance: str = MissionProvenance.LIVE.value,
    ) -> MissionResolution:
        """The 3-rung resolution ladder — every commissioning act resolves
        to exactly one mission (the semantic core).

        Rung 1: the actor has an OPEN epoch as anchor → that mission.
        Rung 2: the actor has an OUTSTANDING commission row → that
        commission's mission (oldest outstanding row wins — the
        deterministic tie-break under adoption-delta (a)'s
        multiple-outstanding-rows rule).
        Rung 3: mint a new epoch anchored on the actor.

        Args:
            actor_instance_id: The instance the act lands on (the
                enqueue target / the nudge target / the continued
                instance).
            lane: Free-form lane label for the log line (the
                ``founding_source`` vocabulary is carried separately).
            founding_source: The epoch's ``founding_source`` when rung 3
                mints. Defaults to the lane label when None.
            founding_message_id: The founding message id when known at
                resolve time. The user_api/chat lanes mint BEFORE the
                enqueue (the id is minted inside it) and attach the id
                afterwards via :meth:`attach_founding_message` — the
                honest mint-then-attach order.
        """
        # Rung 1 — open epoch as anchor.
        open_epoch = self._repo.find_open_epoch_by_anchor(
            actor_instance_id, session=session
        )
        if open_epoch is not None:
            return MissionResolution(open_epoch.mission_id, 1, False)

        # Rung 2 — outstanding commission on the actor.
        commission = self._repo.find_outstanding_commission_for_worker(
            actor_instance_id, session=session
        )
        if commission is not None:
            return MissionResolution(commission.mission_id, 2, False)

        # Rung 3 — mint a new epoch anchored on the actor.
        record = self._mint_epoch_locked(
            actor_instance_id=actor_instance_id,
            founding_source=founding_source or lane,
            session=session,
            founding_message_id=founding_message_id,
            commissioner_instance_id=commissioner_instance_id,
            work_id=work_id,
            provenance=provenance,
        )
        # ── Proposal B PR2 — the EPOCH-BOUNDARY ledger clear ──
        # A NEW epoch is a REAL boundary: the deny ledger, the escalation
        # flag and the O4 dedup array are cleared (proposal-b §2.4).
        # The clear rides the mint's transaction when the mint rode the
        # caller's (the delegation lane's WriteGuardSession).
        self.clear_epoch_ledger(actor_instance_id, session=session)
        return MissionResolution(record.mission_id, 3, True)

    # ── delivery/resume lanes (mint-if-no-open-epoch) ──

    def _mint_if_no_open_epoch(
        self,
        actor_instance_id: str,
        *,
        founding_source: str,
        commissioner_instance_id: str | None = None,
        founding_message_id: str | None = None,
        session: Session | None = None,
        provenance: str = MissionProvenance.LIVE.value,
    ) -> MissionResolution:
        """The delivery/resume shape: an OPEN epoch continues (resume is
        delivery, not founding); a stranded instance (no open epoch)
        gets an honest boundary row. Rung 2 is deliberately not
        consulted — see the module docstring.
        """
        open_epoch = self._repo.find_open_epoch_by_anchor(
            actor_instance_id, session=session
        )
        if open_epoch is not None:
            return MissionResolution(open_epoch.mission_id, 1, False)
        parent = self._repo.find_latest_epoch_by_anchor(
            actor_instance_id, session=session
        )
        record = self._mint_epoch_locked(
            actor_instance_id=actor_instance_id,
            founding_source=founding_source,
            session=session,
            founding_message_id=founding_message_id,
            commissioner_instance_id=commissioner_instance_id,
            parent_mission_id=parent.mission_id if parent else None,
            provenance=provenance,
        )
        # ── Proposal B PR2 — the EPOCH-BOUNDARY ledger clear (see the
        # resolve_or_mint site). A stranded instance's honest boundary
        # row IS a boundary: the ledger resets with it.
        self.clear_epoch_ledger(actor_instance_id, session=session)
        return MissionResolution(record.mission_id, 3, True)

    def mint_boot_epoch_if_none(
        self,
        actor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> MissionResolution:
        """Boot-sweep boundary (lane 14): ``founding_source='boot'``,
        ``founding_message_id=NULL`` — the honest null (no message exists;
        closes the "no message, no clear at all" gap)."""
        return self._mint_if_no_open_epoch(
            actor_instance_id,
            founding_source=MissionFoundingSource.BOOT.value,
            session=session,
        )

    def mint_cascade_resume_epoch_if_stranded(
        self,
        actor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> MissionResolution:
        """Cascade-resume boundary (lane 10): no mint unless the instance
        is stranded (no open epoch) → ``founding_source='revive'``."""
        return self._mint_if_no_open_epoch(
            actor_instance_id,
            founding_source=MissionFoundingSource.REVIVE.value,
            session=session,
        )

    def mint_revive_epoch(
        self,
        actor_instance_id: str,
        *,
        commissioner_instance_id: str | None = None,
        founding_message_id: str | None = None,
        session: Session | None = None,
    ) -> MissionResolution:
        """The revive boundary (lanes 4-terminal / 13): ``founding_source=
        'revive'`` with a parent chain to the anchor's latest epoch.

        If the anchor somehow still has an OPEN epoch, it is returned
        (never two open epochs per anchor — the revive is a continuation
        of it). The nudge-triggered variant records the nudger as the
        epoch's commissioner (the nudge IS what commissioned the revived
        work); the pure-message-shape revive passes ``None``.
        ``founding_message_id`` attaches the triggering message when the
        caller holds it (the enqueue-lane revive: the message id is
        known inside ``_prepare_enqueued_message``).
        """
        return self._mint_if_no_open_epoch(
            actor_instance_id,
            founding_source=MissionFoundingSource.REVIVE.value,
            commissioner_instance_id=commissioner_instance_id,
            founding_message_id=founding_message_id,
            session=session,
        )

    def mint_continuation_epoch(
        self,
        actor_instance_id: str,
        *,
        work_id: str | None = None,
        founding_message_id: str | None = None,
        session: Session | None = None,
    ) -> MissionResolution:
        """job_continue's force-NEW-epoch mint (lane 3).

        A follow-on job is a NEW mission epoch on the same instance —
        NOT rung-1 reuse. The prior OPEN epoch transitions
        ``open → superseded`` RECORD-SIDE ONLY (no instance status, no
        gate channel, no ledger — the §2.4 ledger-clear policy is PR2+;
        PR1 writes records only). ``parent_mission_id`` chains to the
        superseded epoch.

        F1 (Proposal B PR2 carry) — ORDER: the prior open epoch is
        superseded BEFORE the new one is inserted. The open-epoch
        partial unique index (``ux_mission_records_anchor_open``) makes
        "two open epochs for one anchor" a CONSTRAINT violation, so the
        old insert-then-supersede order would raise. When the caller
        supplies no session, both statements run in ONE transaction
        (this method opens it) so the anchor is never observed with
        zero open epochs.
        """
        if session is not None:
            return self._mint_continuation_epoch_locked(
                actor_instance_id,
                work_id=work_id,
                founding_message_id=founding_message_id,
                session=session,
            )
        with Session(self._engine) as own:
            resolution = self._mint_continuation_epoch_locked(
                actor_instance_id,
                work_id=work_id,
                founding_message_id=founding_message_id,
                session=own,
            )
            own.commit()
            return resolution

    def _mint_continuation_epoch_locked(
        self,
        actor_instance_id: str,
        *,
        work_id: str | None,
        founding_message_id: str | None,
        session: Session,
    ) -> MissionResolution:
        """The continuation mint body — assumes the caller's transaction.

        The prior open epoch is superseded FIRST (F1's ordering
        constraint), then the new epoch is inserted, then the parent's
        mission id is attached.
        """
        prior_open = self._repo.find_open_epoch_by_anchor(
            actor_instance_id, session=session
        )
        if prior_open is not None:
            self._repo.supersede_open_epochs_for_anchor(
                actor_instance_id,
                except_mission_id=None,
                session=session,
            )
        record = self._mint_epoch_locked(
            actor_instance_id=actor_instance_id,
            founding_source=MissionFoundingSource.JOB_CONTINUE.value,
            session=session,
            founding_message_id=founding_message_id,
            work_id=work_id,
            parent_mission_id=prior_open.mission_id if prior_open else None,
        )
        # ── Proposal B PR2 — the EPOCH-BOUNDARY ledger clear (see the
        # resolve_or_mint site). A job_continue follow-on is a NEW
        # epoch: the boundary resets the ledger.
        self.clear_epoch_ledger(actor_instance_id, session=session)
        return MissionResolution(record.mission_id, 3, True)

    # ── record amendments (guarded, records only) ──

    def clear_epoch_ledger(
        self,
        anchor_instance_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """The EPOCH-BOUNDARY ledger clear (proposal-b §2.4).

        "Ledger reset at every boundary, deliberately: epoch mint clears
        ``attestation_denied_count`` + ``completion_gate_escalated``
        (generalizing the fresh-episode revive clear from 'terminal
        revive only' to 'every recorded boundary'), and
        ``denial_epochs`` becomes epoch-tagged: entries carry the owning
        ``mission_id``; the deny bound counts only current-epoch
        entries; entries from closed epochs are pruned at boundary."

        WHAT DELIBERATELY DOES NOT RESET: the withhold budget (per
        dispatch) and the run-deny cap (per run) — already scoped
        tighter than the epoch.

        MODE GATE: the clear is armed ONLY in ``enforce`` mode. The
        mode ladder's ``dry`` contract is "mint + stamp + log
        everything" with the gate on legacy behavior — and the clear IS
        a gate-visible behavior change (it resets the deny bound). The
        operator promotes it with the same env flip that promotes the
        reads (``ENSEMBLE_MISSION_RECORDS=enforce`` + restart).

        ATOMICITY: when ``session`` is supplied, the clear rides the
        CALLER's transaction — the delegation lane's
        ``WriteGuardSession``. A second connection's write would
        contend with the lane's open write transaction (the SQLite
        single-writer lock), so the clear MUST ride the mint's own
        transaction.

        Fail-open: the caller (the mint path) wraps this; a ledger
        fault must never break a mint.
        """
        if not mission_records_enforce_enabled():
            return False
        if not anchor_instance_id:
            return False
        try:
            from ..repositories.instance.repository import (
                SQLModelInstanceRepository,
            )

            repo = SQLModelInstanceRepository(self._engine)
            if session is not None:
                return repo.reset_attestation_denied_count_on_session(
                    session, anchor_instance_id
                )
            with Session(self._engine) as own:
                cleared = repo.reset_attestation_denied_count_on_session(
                    own, anchor_instance_id
                )
                own.commit()
                return cleared
        except Exception as exc:  # noqa: BLE001 — fail-open (never break a mint)
            logger.warning(
                "mission_records: epoch-boundary ledger clear failed "
                "(anchor=%s…): %s: %s — the mint continues",
                anchor_instance_id[:8],
                type(exc).__name__,
                exc,
            )
            return False

    def attach_founding_message(
        self,
        mission_id: str,
        founding_message_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Attach the enqueue-minted message id to its epoch (the
        mint-then-attach order). First attach wins (guarded on NULL)."""
        return self._repo.attach_founding_message(
            mission_id, founding_message_id, session=session
        )

    def amend_anchor(
        self,
        mission_id: str,
        founding_message_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Re-founding amendment seam (mid-mission user correction) —
        RECORDS ONLY in PR1. The delivery-time firing (tap site) and the
        gate read flip are PR2; the record shape ships complete."""
        return self._repo.amend_founding_message(
            mission_id, founding_message_id, session=session
        )

    def attach_work_id(
        self,
        mission_id: str,
        work_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """Attach the post-enqueue ``work_id`` (job_continue linkage)."""
        return self._repo.attach_work_id(mission_id, work_id, session=session)

    # ── commissions ──

    def mint_commission(
        self,
        mission_id: str,
        *,
        commissioner_instance_id: str,
        worker_instance_id: str,
        lane: str,
        work_id: str | None = None,
        session: Session | None = None,
        advisory_backfill: bool = False,
        commission_id: str | None = None,
        provenance: str = MissionProvenance.LIVE.value,
    ) -> MissionCommission:
        """Record one commissioned worker / work item.

        D4(ii) mint-path validation: a commission whose WORKER row is
        missing at mint time RAISES
        :class:`MissionRecordError` — "a commission that can never be
        satisfied must not be recorded as outstanding" (proposal-b §3).
        Lane-side fail-open wrappers catch + log.

        ADOPTION DELTA (a): a second commission of the same worker
        mints a SECOND OUTSTANDING row — the guarded-state lifecycle is
        the discriminator (see the module docstring for the precedence
        decision). Routing authority STAYED with the A-resolver through
        PR1/PR2; **PR3 flipped it** — the OLDEST outstanding row now
        routes (report_recipient.resolve_report_recipient, enforce
        -gated; the A-resolver is the declared legacy fallback leg).

        ``commission_id`` may be pinned (deterministic UUID5) by the
        BACKFILL so its idempotency PRE-CHECK (``get_commission``) keys
        on the PK; there is NO ``ON CONFLICT`` clause in this layer — a
        re-run is a no-op because the pre-check finds the row, not
        because the insert swallows a conflict. Live mints leave it None
        (random UUID4).
        """
        self._require_instance_row(
            worker_instance_id, role="commission worker", session=session
        )
        commission = MissionCommission(
            commission_id=commission_id if commission_id is not None else _new_uuid4(),
            mission_id=mission_id,
            commissioner_instance_id=commissioner_instance_id,
            worker_instance_id=worker_instance_id,
            work_id=work_id,
            lane=lane,
            state=CommissionState.OUTSTANDING.value,
            advisory_backfill=advisory_backfill,
        )
        if provenance == MissionProvenance.BACKFILL.value and not advisory_backfill:
            commission.advisory_backfill = True
        return self._repo.insert_commission(commission, session=session)

    def satisfy_commission(
        self,
        commission_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """``outstanding → satisfied`` (guarded).

        **PR3 lands the production caller**: the natural completion
        write path satisfies the ROUTING commission when the child's
        report is actually written + addressed to its commissioner
        (``child_reports._process_child_completion_db_sync``, the
        ReportInjection writer region — in-transaction, savepoint
        -guarded, fail-open: a satisfy failure never breaks delivery).
        Rowcount 0 = another actor won the guarded transition; the
        loser counts it and never retries in place.

        NOT covered here (deliberate): the reconciliation sweep's
        delivery-evidence probes and the ``abandoned``/
        worker-terminal/TTL writes — that is RDRS lane 7, a separate
        cycle. Until it lands, a commission whose report is never
        delivered still holds the mission (the eternal-mission class,
        MAJOR-1 ruling (c) keeps it deliberately).
        """
        return self._repo.transition_commission_state(
            commission_id, CommissionState.SATISFIED.value, session=session
        )

    def abandon_commission(
        self,
        commission_id: str,
        *,
        reason: str,
        session: Session | None = None,
    ) -> bool:
        """``outstanding → abandoned`` (guarded).

        The reconciliation sweep's outcome write (the eternal-mission
        escape hatch, proposal-b §8). **STILL no production caller in
        PR3** — the sweep is RDRS lane 7, a separate cycle (the PR3
        terminator covers ONLY the delivery-side ``satisfied`` write).
        The seam ships record-side complete.
        """
        return self._repo.transition_commission_state(
            commission_id,
            CommissionState.ABANDONED.value,
            abandon_reason=reason,
            session=session,
        )

    def supersede_commission(
        self,
        commission_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """``outstanding → superseded`` (guarded)."""
        return self._repo.transition_commission_state(
            commission_id, CommissionState.SUPERSEDED.value, session=session
        )

    # ── attestation + settle (record-only in PR1) ──

    def record_attestation(
        self,
        mission_id: str,
        *,
        attest_method: str = MissionAttestMethod.LEADER_ATTEST.value,
        attestation_bypassed: bool = False,
        session: Session | None = None,
    ) -> bool:
        """Write the attestation block on the OPEN epoch.

        PR1 lands the FIELDS + this seam (record-only). The Term-1
        BLOCKING policy stays default-OFF per the ruling — the blocking
        read is PR2 policy, gated separately. ``attest_completion``'s
        one-row write is expected to call this in PR2's wiring; PR1
        keeps the seam tested and unused by production callers.
        """
        return self._repo.record_attestation(
            mission_id,
            attest_method=attest_method,
            attestation_bypassed=attestation_bypassed,
            session=session,
        )

    def settle(
        self,
        mission_id: str,
        *,
        session: Session | None = None,
    ) -> bool:
        """``open → settled`` (guarded, records only).

        PR1 has NO production caller: settle is written by the EXISTING
        settle machinery as a post-condition in PR2/PR3 (the L2 root-lane
        stamp site / emission dispatch). No new terminal instance-status
        writer exists — this writes ONLY ``mission_records.status``.
        """
        return self._repo.transition_epoch_status(
            mission_id,
            MissionStatus.SETTLED.value,
            settled_at=now_utc_iso(),
            session=session,
        )

    # ── adoption delta (b): advisory nudge-commission backfill ──

    def backfill_advisory_nudge_commissions(
        self,
        *,
        dry_run: bool = True,
        session: Session | None = None,
    ) -> dict[str, int]:
        """Best-effort historical coverage of the existing nudge audit rows.

        Rules (proposal-b §5.2 + comparison.md §4c, verbatim intent):

        * Source: ``instance_ops_audit`` rows with
          ``action='instance_nudge'`` AND ``outcome='committed'`` AND
          ``dry_run=false`` (the audit's default row is a PREVIEW —
          committed non-dry rows are the REAL acts; erratum 7).
        * Target still NON-TERMINAL → mint an ADVISORY nudge commission
          row (``lane='instance_nudge'``, ``provenance='backfill'``,
          ``advisory_backfill=True``, deterministic UUID5 id).
        * Target TERMINAL (or actor missing, or the mission epoch-0
          row cannot be minted) → SKIPPED — the unrecoverable
          one-time gap, counted in the result.
        * IDEMPOTENT: the deterministic commission id + the epoch-0
          resolve-if-none probe make re-runs no-ops.
        * ``dry_run=True`` (the default) runs the scan + rules and logs
          the plan WITHOUT writing — the dry-run-loggable contract.

        **Advisory backfill rows are EXCLUDED from routing authority
        FOREVER** (comparison.md §4c: "if dropped, the ledger is worse
        than no ledger"). Nothing in PR1 reads them for decisions; the
        PR3 flip must keep the exclusion.

        Returns counters: ``{'scanned', 'minted_epochs', 'minted_commissions',
        'skipped_terminal', 'skipped_no_actor', 'skipped_missing_instance_row'}``.
        """
        counters = {
            "scanned": 0,
            "minted_epochs": 0,
            "minted_commissions": 0,
            "skipped_terminal": 0,
            "skipped_no_actor": 0,
            "skipped_missing_instance_row": 0,
        }
        terminal = {
            InstanceStatus.COMPLETED.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        }

        audits = self._repo.list_committed_nudge_audits(
            action="instance_nudge",
            outcome="committed",
            dry_run=False,
            session=session,
        )
        counters["scanned"] = len(audits)
        logger.info(
            "mission_records backfill: scanning %d committed non-dry "
            "instance_nudge audit rows (dry_run=%s)",
            len(audits),
            dry_run,
        )

        for audit_id, actor_instance_id, target_instance_id, _created_at in audits:
            # The audit row records the ACTOR (nudger); a committed
            # non-dry nudge without an actor id cannot be attributed —
            # the accepted one-time gap.
            if not actor_instance_id:
                counters["skipped_no_actor"] += 1
                continue
            with Session(self._engine) as own:
                target_row = own.get(Instance, target_instance_id)
                actor_row = own.get(Instance, actor_instance_id)
            if target_row is None or actor_row is None:
                counters["skipped_missing_instance_row"] += 1
                continue
            if target_row.status in terminal:
                # The nudge's outcome is unrecoverable from the audit
                # row (it records the act, not the completion linkage).
                counters["skipped_terminal"] += 1
                continue

            # Epoch coverage: the epoch-0 synthetic row for the ACTOR
            # (the mission the nudge belonged to), honest-NULL founding.
            existing_epoch0 = self._repo.find_backfill_row_for_anchor(
                actor_instance_id, session=session
            )
            mission_id: str | None = None
            if existing_epoch0 is not None:
                mission_id = existing_epoch0.mission_id
            elif not dry_run:
                # F1 (Proposal B PR2 carry): the open-epoch partial
                # unique index makes "two open epochs for one anchor" a
                # CONSTRAINT violation. A live anchor that already owns
                # an OPEN epoch therefore REUSES it (the mission the
                # nudge belonged to) instead of minting a synthetic
                # epoch-0 row that would violate the index. The
                # synthetic epoch-0 row remains for anchors with no
                # live epoch (the historical-coverage case).
                live_epoch = self._repo.find_open_epoch_by_anchor(
                    actor_instance_id, session=session
                )
                if live_epoch is not None:
                    mission_id = live_epoch.mission_id
                else:
                    record = self._mint_epoch_locked(
                        actor_instance_id=actor_instance_id,
                        founding_source=MissionFoundingSource.BACKFILL.value,
                        session=session,
                        provenance=MissionProvenance.BACKFILL.value,
                    )
                    mission_id = record.mission_id
                    counters["minted_epochs"] += 1
            else:
                counters["minted_epochs"] += 1  # dry-run projection
                mission_id = "dry-run-projected"  # plan-only marker

            if mission_id is None:
                continue

            deterministic_id = self._repo.deterministic_commission_id(
                mission_id, target_instance_id, CommissionLane.INSTANCE_NUDGE.value, audit_id
            )
            if not dry_run:
                existing = self._repo.get_commission(
                    deterministic_id, session=session
                )
                if existing is not None:
                    continue  # idempotent re-run — conflict = no-op
                self.mint_commission(
                    mission_id,
                    commissioner_instance_id=actor_instance_id,
                    worker_instance_id=target_instance_id,
                    lane=CommissionLane.INSTANCE_NUDGE.value,
                    session=session,
                    advisory_backfill=True,
                    commission_id=deterministic_id,
                    provenance=MissionProvenance.BACKFILL.value,
                )
            counters["minted_commissions"] += 1
            logger.info(
                "mission_records backfill: advisory nudge commission "
                "actor=%s… target=%s… mission=%s… (dry_run=%s)",
                actor_instance_id[:8],
                target_instance_id[:8],
                mission_id[:8],
                dry_run,
            )

        logger.info(
            "mission_records backfill: done scanned=%(scanned)d "
            "minted_epochs=%(minted_epochs)d minted_commissions=%(minted_commissions)d "
            "skipped_terminal=%(skipped_terminal)d skipped_no_actor=%(skipped_no_actor)d "
            "skipped_missing_instance_row=%(skipped_missing_instance_row)d (dry_run=%(dryrun)s)",
            {**counters, "dryrun": dry_run},
        )
        return counters


# ---------------------------------------------------------------------------
# Lane-facing fail-open wrappers
# ---------------------------------------------------------------------------


def _log_mint(
    rung: int,
    created: bool,
    actor: str,
    lane: str,
    mission_id: str,
    *,
    deferred: bool = False,
) -> None:
    """Log the resolve-or-mint outcome.

    ``deferred=True`` marks a mint that rides a CALLER-owned transaction
    (the ``session=`` lanes): the row is STAGED, not durable, when this
    fires — the caller owns the COMMIT and announces the durable success
    afterwards. A staged mint is therefore logged at DEBUG, so the INFO
    "MINT" line always means "durable" (a success log that precedes a
    rollback is a false success record).
    """
    if created:
        if deferred:
            logger.debug(
                "mission_records MINT (staged — caller owns the commit) "
                "lane=%s actor=%s… mission=%s… (rung %d)",
                lane,
                actor[:8],
                mission_id[:8],
                rung,
            )
        else:
            logger.info(
                "mission_records MINT lane=%s actor=%s… mission=%s… (rung %d)",
                lane,
                actor[:8],
                mission_id[:8],
                rung,
            )
    else:
        logger.debug(
            "mission_records resolve lane=%s actor=%s… mission=%s… (rung %d)",
            lane,
            actor[:8],
            mission_id[:8],
            rung,
        )


def resolve_or_mint_fail_open(
    service: MissionRecordService | None,
    actor_instance_id: str,
    lane: str,
    *,
    founding_source: str | None = None,
    founding_message_id: str | None = None,
    commissioner_instance_id: str | None = None,
    work_id: str | None = None,
    session: Session | None = None,
) -> str | None:
    """Commissioning-lane wrapper: resolve-or-mint, never raise.

    Mode gate: ``off`` → ``None`` (no mints, no reads — the lane
    proceeds record-blind, byte-identical to pre-PR1). Any failure →
    logged + ``None`` (the D2(b) watcher-mint fail-open posture: the
    record must never break the lane). When ``session`` is provided the
    mint rides the CALLER's transaction inside a SAVEPOINT
    (``begin_nested``) — the send_message delegation lane's
    same-transaction-as-the-watcher contract — and a mint failure rolls
    back the savepoint only, never the caller's own writes. In that mode
    the mint is logged at DEBUG (STAGED, not durable): the CALLER owns
    the COMMIT and announces the durable success after it, so a success
    log never precedes a rollback.

    ``founding_message_id`` is forwarded verbatim into
    :meth:`MissionRecordService.resolve_or_mint` so lanes that know the
    founding id BEFORE the mint (the chat inject sub-lane, whose echo
    id is minted ahead of the call) mint a COMPLETE record at mint
    time — the signature must accept it even though every caller is
    keyword-only (H1, 2026-10-25: the chat lane's kwarg failed to bind
    at call time, BEFORE the fail-open try, dropping the message).
    """
    if service is None:
        return None
    if _mode_is_off():
        return None
    try:
        if session is not None:
            with session.begin_nested():
                resolution = service.resolve_or_mint(
                    actor_instance_id,
                    lane,
                    founding_source=founding_source,
                    founding_message_id=founding_message_id,
                    commissioner_instance_id=commissioner_instance_id,
                    work_id=work_id,
                    session=session,
                )
        else:
            resolution = service.resolve_or_mint(
                actor_instance_id,
                lane,
                founding_source=founding_source,
                founding_message_id=founding_message_id,
                commissioner_instance_id=commissioner_instance_id,
                work_id=work_id,
            )
        _log_mint(
            resolution.rung,
            resolution.created,
            actor_instance_id,
            lane,
            resolution.mission_id,
            deferred=session is not None,
        )
        return resolution.mission_id
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: resolve_or_mint failed (lane=%s actor=%s…): "
            "%s: %s — lane proceeds without a record",
            lane,
            actor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def mint_continuation_epoch_fail_open(
    service: MissionRecordService | None,
    actor_instance_id: str,
    *,
    work_id: str | None = None,
) -> str | None:
    """job_continue wrapper (force-new epoch; never raise)."""
    if service is None:
        return None
    if _mode_is_off():
        return None
    try:
        resolution = service.mint_continuation_epoch(
            actor_instance_id, work_id=work_id
        )
        _log_mint(
            resolution.rung,
            resolution.created,
            actor_instance_id,
            "job_continue",
            resolution.mission_id,
        )
        return resolution.mission_id
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: mint_continuation_epoch failed (actor=%s…): "
            "%s: %s — lane proceeds without a record",
            actor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def mint_revive_epoch_fail_open(
    service: MissionRecordService | None,
    actor_instance_id: str,
    *,
    commissioner_instance_id: str | None = None,
    founding_message_id: str | None = None,
    session: Session | None = None,
    context_label: str = "revive",
) -> str | None:
    """Revive-boundary wrapper (nudge-on-terminal + the
    ``_prepare_enqueued_message`` fresh-episode revive). When
    ``session`` is provided the mint rides the CALLER's transaction
    (atomic with the revive) inside a SAVEPOINT (``begin_nested``) so a
    mint failure can never poison the caller's transaction — the
    savepoint rolls back, the wrapper degrades to no-record, and the
    caller's own writes commit unchanged."""
    if service is None:
        return None
    if _mode_is_off():
        return None
    try:
        if session is not None:
            with session.begin_nested():
                resolution = service.mint_revive_epoch(
                    actor_instance_id,
                    commissioner_instance_id=commissioner_instance_id,
                    founding_message_id=founding_message_id,
                    session=session,
                )
        else:
            resolution = service.mint_revive_epoch(
                actor_instance_id,
                commissioner_instance_id=commissioner_instance_id,
                founding_message_id=founding_message_id,
            )
        _log_mint(
            resolution.rung,
            resolution.created,
            actor_instance_id,
            context_label,
            resolution.mission_id,
        )
        return resolution.mission_id
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: mint_revive_epoch failed (actor=%s… "
            "context=%s): %s: %s — lane proceeds without a record",
            actor_instance_id[:8],
            context_label,
            type(exc).__name__,
            exc,
        )
        return None


def mint_boot_epoch_if_none_fail_open(
    service: MissionRecordService | None,
    actor_instance_id: str,
) -> str | None:
    """Boot-sweep wrapper (never raise; boot must never wedge)."""
    if service is None:
        return None
    if _mode_is_off():
        return None
    try:
        resolution = service.mint_boot_epoch_if_none(actor_instance_id)
        _log_mint(
            resolution.rung,
            resolution.created,
            actor_instance_id,
            "boot",
            resolution.mission_id,
        )
        return resolution.mission_id
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: mint_boot_epoch_if_none failed (actor=%s…): "
            "%s: %s — boot proceeds without a record",
            actor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def mint_cascade_resume_epoch_if_stranded_fail_open(
    service: MissionRecordService | None,
    actor_instance_id: str,
) -> str | None:
    """Cascade-resume wrapper (never raise; the resume must never wedge)."""
    if service is None:
        return None
    if _mode_is_off():
        return None
    try:
        resolution = service.mint_cascade_resume_epoch_if_stranded(
            actor_instance_id
        )
        _log_mint(
            resolution.rung,
            resolution.created,
            actor_instance_id,
            "cascade_resume",
            resolution.mission_id,
        )
        return resolution.mission_id
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: cascade_resume mint failed (actor=%s…): "
            "%s: %s — resume proceeds without a record",
            actor_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def mint_commission_fail_open(
    service: MissionRecordService | None,
    mission_id: str | None,
    *,
    commissioner_instance_id: str,
    worker_instance_id: str,
    lane: str,
    work_id: str | None = None,
    session: Session | None = None,
) -> str | None:
    """Commission-mint wrapper (never raise).

    D4(ii) note: the SERVICE raises ``MissionRecordError`` on a missing
    worker row (the mint is the semantic contract); this WRAPPER is the
    lane posture — the commissioning act itself still succeeds and the
    validation failure is LOUD in the logs, never a silent mis-record.
    Returns the commission id, or None (mode off / no mission / failure).

    When ``session`` is provided the mint rides the CALLER's transaction
    (SAVEPOINT) — the commission is STAGED, not durable, when this
    returns, so it is logged at DEBUG and the CALLER announces the
    durable success after its COMMIT (a success log that precedes a
    rollback is a false success record). With ``session=None`` the
    repository owns the transaction, the row is already durable and the
    INFO line is a true success record.
    """
    if service is None or not mission_id:
        return None
    if _mode_is_off():
        return None
    try:
        if session is not None:
            with session.begin_nested():
                commission = service.mint_commission(
                    mission_id,
                    commissioner_instance_id=commissioner_instance_id,
                    worker_instance_id=worker_instance_id,
                    lane=lane,
                    work_id=work_id,
                    session=session,
                )
        else:
            commission = service.mint_commission(
                mission_id,
                commissioner_instance_id=commissioner_instance_id,
                worker_instance_id=worker_instance_id,
                lane=lane,
                work_id=work_id,
            )
        if session is None:
            # Repo-owned transaction: the service committed before this
            # line, so the row IS durable — the INFO line is a true
            # success record.
            logger.info(
                "mission_records COMMISSION lane=%s mission=%s… "
                "commissioner=%s… worker=%s… commission=%s…",
                lane,
                mission_id[:8],
                commissioner_instance_id[:8],
                worker_instance_id[:8],
                commission.commission_id[:8],
            )
        else:
            # Caller-owned transaction: the row is STAGED, not durable —
            # the caller commits and announces the durable success.
            # A success log that precedes a rollback is a false success
            # record.
            logger.debug(
                "mission_records COMMISSION (staged — caller owns the "
                "commit) lane=%s mission=%s… commissioner=%s… worker=%s… "
                "commission=%s…",
                lane,
                mission_id[:8],
                commissioner_instance_id[:8],
                worker_instance_id[:8],
                commission.commission_id[:8],
            )
        return commission.commission_id
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: mint_commission failed (lane=%s "
            "commissioner=%s… worker=%s…): %s: %s — act proceeds "
            "without a commission record",
            lane,
            commissioner_instance_id[:8],
            worker_instance_id[:8],
            type(exc).__name__,
            exc,
        )
        return None


def satisfy_commission_fail_open(
    service: MissionRecordService | None,
    commission_id: str | None,
    *,
    session: Session | None = None,
) -> bool:
    """Commission-terminator wrapper (never raise) — the PR3 delivery
    terminator.

    The DELIVERY-side ``outstanding → satisfied`` write: the natural
    completion write path calls this when a LEG-1 (mission-commission)
    -routed report is actually written + addressed to its commissioner.
    The posture is FAIL-OPEN — a satisfy failure (or a records-side
    error, or mode ``off``) must NEVER break delivery; the caller logs
    and delivery proceeds with the commission still outstanding (the
    reconciliation sweep's probes are the net, RDRS lane 7).

    Guarded transition ONLY: ``UPDATE ... WHERE state='outstanding'``
    (rowcount 0 = another actor won — counted, never retried in
    place). When ``session`` is provided the write rides the CALLER's
    transaction via a SAVEPOINT — atomic with the report write (a
    rolled-back delivery can never leave a satisfied commission
    behind); with ``session=None`` the repository owns the transaction.

    Returns:
        ``True``  — the guarded transition moved exactly one row (this
                     actor won) — STAGED when ``session`` is given.
        ``False`` — mode ``off``, no commission id, a lost race
                     (rowcount 0), or ANY error (fail-open).
    """
    if service is None or not commission_id:
        return False
    if _mode_is_off():
        return False
    try:
        if session is not None:
            moved = False
            with session.begin_nested():
                moved = service.satisfy_commission(
                    commission_id, session=session
                )
            if moved:
                # Caller-owned transaction: STAGED until the caller
                # commits — the caller announces the durable success
                # post-commit (a success log that precedes a rollback
                # is a false success record).
                logger.debug(
                    "mission_records COMMISSION SATISFIED (staged — "
                    "caller owns the commit) commission=%s…",
                    commission_id[:8],
                )
            else:
                # Guarded-transition lost race: another actor moved the
                # row first. Count it, never retry in place.
                logger.info(
                    "mission_records: satisfy lost the guarded race "
                    "(commission=%s… already moved) — counted, not "
                    "retried",
                    commission_id[:8],
                )
            return moved
        moved = service.satisfy_commission(commission_id)
        if moved:
            logger.info(
                "mission_records COMMISSION SATISFIED commission=%s… "
                "(repo-owned transaction — durable)",
                commission_id[:8],
            )
        else:
            logger.info(
                "mission_records: satisfy lost the guarded race "
                "(commission=%s… already moved) — counted, not retried",
                commission_id[:8],
            )
        return moved
    except Exception as exc:  # noqa: BLE001 — fail-open terminator
        logger.warning(
            "mission_records: satisfy_commission failed "
            "(commission=%s…): %s: %s — fail-OPEN, delivery proceeds "
            "with the commission outstanding",
            commission_id[:8] if commission_id else "?",
            type(exc).__name__,
            exc,
        )
        return False


def attach_work_id_fail_open(
    service: MissionRecordService | None,
    mission_id: str | None,
    work_id: str | None,
) -> bool:
    """Post-enqueue job-linkage attach wrapper (never raise; miss = logged)."""
    if service is None or not mission_id or not work_id:
        return False
    if _mode_is_off():
        return False
    try:
        attached = service.attach_work_id(mission_id, work_id)
        if not attached:
            logger.debug(
                "mission_records: work_id attach skipped (mission=%s… "
                "already set or row missing)",
                mission_id[:8],
            )
        return attached
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: attach_work_id failed (mission=%s…): %s: %s",
            mission_id[:8],
            type(exc).__name__,
            exc,
        )
        return False


def attach_founding_message_fail_open(
    service: MissionRecordService | None,
    mission_id: str | None,
    founding_message_id: str | None,
) -> bool:
    """Post-enqueue attach wrapper (never raise; miss = logged)."""
    if service is None or not mission_id or not founding_message_id:
        return False
    if _mode_is_off():
        return False
    try:
        attached = service.attach_founding_message(mission_id, founding_message_id)
        if not attached:
            logger.debug(
                "mission_records: founding message attach skipped "
                "(mission=%s… already set or row missing)",
                mission_id[:8],
            )
        return attached
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: attach_founding_message failed (mission=%s…): "
            "%s: %s",
            mission_id[:8],
            type(exc).__name__,
            exc,
        )
        return False


def amend_anchor_fail_open(
    service: MissionRecordService | None,
    mission_id: str | None,
    founding_message_id: str | None,
) -> bool:
    """Re-founding amendment wrapper (never raise; miss = logged).

    Proposal B PR2 — the DELIVERY-TIME amendment seam (ruling ③). The
    guarded write touches only an OPEN epoch (F2), so a closed epoch is
    never rewritten and a stale mission id is a safe no-op.

    Mode gate: ``off`` → ``False`` (no records, no amendment). Any
    failure → logged + ``False``: the amendment is a RECORD write and
    must never break the delivery lane it rides.
    """
    if service is None or not mission_id or not founding_message_id:
        return False
    if _mode_is_off():
        return False
    try:
        amended = service.amend_anchor(mission_id, founding_message_id)
        if not amended:
            logger.debug(
                "mission_records: anchor amendment skipped (mission=%s… "
                "not open or row missing)",
                mission_id[:8],
            )
        return amended
    except Exception as exc:  # noqa: BLE001 — fail-open producer
        logger.warning(
            "mission_records: amend_anchor failed (mission=%s…): %s: %s",
            mission_id[:8],
            type(exc).__name__,
            exc,
        )
        return False


__all__ = [
    "MissionRecordService",
    "MissionRecordError",
    "MissionResolution",
    "MissionRecordMode",
    "MISSION_RECORDS_MODE_ENV",
    "MISSION_COMMISSION_TTL_ENV",
    "resolve_mission_commission_ttl_seconds",
    "MISSION_ID_KEY",
    "SCHEDULER_MINT_EXTENSION_ENV",
    "resolve_mission_records_mode",
    "mission_records_enforce_enabled",
    "scheduler_mint_extension_enabled",
    "resolve_or_mint_fail_open",
    "mint_continuation_epoch_fail_open",
    "mint_revive_epoch_fail_open",
    "mint_boot_epoch_if_none_fail_open",
    "mint_cascade_resume_epoch_if_stranded_fail_open",
    "mint_commission_fail_open",
    "attach_founding_message_fail_open",
    "amend_anchor_fail_open",
    "attach_work_id_fail_open",
]
