"""Decision module — DB-backed durable cost accumulator and wiring adapters.

Implements the ``CostAccumulator`` protocol over the ``app_settings`` table,
ensuring that month-to-date token and USD totals survive daemon restarts.
Also provides ``SettingsServiceKillStore`` (binding the kill resolver's DB-tier
read to the settings service) and ``build_s1_seam_record`` (the S1 seam definition).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from daemon.models.app_settings import AppSetting
from daemon.services.db_offload import offload
from daemon.services.decision.logging_safe import log_exception
from daemon.services.decision.kill import KillStore
from daemon.services.decision.overlay import CostAccumulator, CostTotals
from daemon.services.decision.port import extract_snapshot_ids
from daemon.services.decision.registry import SeamRecord
from daemon.services.decision.types import (
    DecisionChoice,
    DecisionQuestion,
    DecisionRequest,
    Monotonicity,
    QuestionKind,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AppSettingCostAccumulator",
    "SettingsServiceKillStore",
    "build_s1_seam_record",
]


def _current_utc_month() -> str:
    """The current UTC month string 'YYYY-MM'."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


class AppSettingCostAccumulator(CostAccumulator):
    """A durable month-to-date cost ledger persisted to ``app_settings``.

    Deliberately NOT registered in ``MANAGED_KEYS`` so the settings router
    404s it — operator-invisible and un-editable.
    Keyed by month ('YYYY-MM') so each calendar month naturally starts fresh.
    All DB operations run off the event loop via ``daemon.services.db_offload``.

    **KNOWN LIMITATION — THE LOCK'S GRANULARITY (council round 4,
    fold-now item 5; a documented limitation, NOT a defect).** The
    read-modify-write is serialized by ONE process-level lock, and the
    lock's granularity is the LEDGER ROW (one ``decision_cost_ledger_{period}``
    row), while the LOCK ITSELF is process-wide: records for DIFFERENT
    periods, and for different seams inside one period, serialize on the
    SAME lock. The correctness argument is unaffected — the daemon is
    single-process (``uvicorn`` ``workers=1``), so one lock is the full
    argument, and the lock SPANS the whole read-modify-write (the
    council's (e)/(a) pins and the mutation matrix establish the
    behaviour). What the granularity costs is THROUGHPUT: the ceiling
    binds on a month-to-date total that a burst of consults updates
    serially. The limit is accepted deliberately: a per-row lock (or a
    SQL-side ``UPDATE ... SET total = total + $1``) would trade a
    correctness-critical invariant for a throughput gain the P1 traffic
    profile does not need. Revisit only if the consult rate makes the
    serialization measurable.
    """

    def __init__(
        self,
        engine: Any,
        *,
        period_getter: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._period_getter = period_getter or _current_utc_month
        # COST-LEDGER ATOMICITY (council fold-now advisory): the
        # increment is a read-modify-write, so it is SERIALIZED through
        # ONE process-level lock. The daemon is single-process
        # (``uvicorn`` default ``workers=1`` — the same invariant the
        # settings service relies on), so one lock is the full
        # correctness argument: without it, concurrent consults lose
        # deltas and the month-to-date spend UNDERCOUNTS — and the
        # ceiling binds on exactly that quantity.
        self._lock = threading.Lock()

    async def record(
        self, seam_id: str, *, usd: float, tokens: int
    ) -> CostTotals:
        """Record metered spend for one consult and return the new month-to-date totals."""
        period = self._period_getter()
        return await offload(
            self._record_sync,
            period,
            seam_id,
            float(usd),
            int(tokens),
        )

    async def current_totals(self, seam_id: str) -> CostTotals:
        """Query current month totals without recording a new spend."""
        period = self._period_getter()
        return await offload(self._read_sync, period, seam_id)

    # ── synchronous offload bodies ──────────────────────────────────────

    def _record_sync(
        self, period: str, seam_id: str, usd: float, tokens: int
    ) -> CostTotals:
        """Read-modify-write the month ledger — SERIALIZED (atomicity).

        Held under ONE process-level lock: the daemon is single-process
        (``uvicorn`` ``workers=1``), so the lock is the full correctness
        argument for concurrent consults. Without it, two concurrent
        read-modify-writes both read the OLD total and one delta is
        LOST — the month-to-date spend undercounts, and the ceiling
        binds on that undercounted quantity.
        """
        key = f"decision_cost_ledger_{period}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock:
            return self._record_locked(
                key, period, seam_id, usd, tokens, now_iso
            )

    def _record_locked(
        self,
        key: str,
        period: str,
        seam_id: str,
        usd: float,
        tokens: int,
        now_iso: str,
    ) -> CostTotals:
        """The lock-held body of :meth:`_record_sync`."""
        from sqlmodel import Session, select

        with Session(self._engine) as session:
            row = session.exec(
                select(AppSetting).where(AppSetting.key == key)
            ).first()

            if row is not None:
                try:
                    data = json.loads(row.value_json)
                    if not isinstance(data, dict):
                        data = {}
                except (ValueError, TypeError):
                    data = {}
            else:
                data = {}

            month_usd = float(data.get("usd", 0.0)) + usd
            month_tokens = int(data.get("tokens", 0)) + tokens
            per_seam_usd: dict[str, float] = {
                str(k): float(v) for k, v in data.get("per_seam_usd", {}).items()
            }
            per_seam_tokens: dict[str, int] = {
                str(k): int(v) for k, v in data.get("per_seam_tokens", {}).items()
            }

            seam_usd = per_seam_usd.get(seam_id, 0.0) + usd
            seam_tokens = per_seam_tokens.get(seam_id, 0) + tokens
            per_seam_usd[seam_id] = seam_usd
            per_seam_tokens[seam_id] = seam_tokens

            new_data = {
                "period": period,
                "usd": month_usd,
                "tokens": month_tokens,
                "per_seam_usd": per_seam_usd,
                "per_seam_tokens": per_seam_tokens,
            }
            payload_json = json.dumps(new_data)

            if row is None:
                row = AppSetting(
                    key=key,
                    value_json=payload_json,
                    value_type="str",
                    updated_by=None,
                    updated_at=now_iso,
                )
                session.add(row)
            else:
                row.value_json = payload_json
                row.updated_at = now_iso
                session.add(row)

            session.commit()

            return CostTotals(
                month_to_date_usd=month_usd,
                seam_month_to_date_usd=seam_usd,
                month_to_date_tokens=month_tokens,
                seam_month_to_date_tokens=seam_tokens,
            )

    def _read_sync(self, period: str, seam_id: str) -> CostTotals:
        from sqlmodel import Session, select

        key = f"decision_cost_ledger_{period}"
        with Session(self._engine) as session:
            row = session.exec(
                select(AppSetting).where(AppSetting.key == key)
            ).first()
            if row is None:
                return CostTotals()
            try:
                data = json.loads(row.value_json)
                if not isinstance(data, dict):
                    return CostTotals()
            except (ValueError, TypeError):
                return CostTotals()

            month_usd = float(data.get("usd", 0.0))
            month_tokens = int(data.get("tokens", 0))
            per_seam_usd = data.get("per_seam_usd", {})
            per_seam_tokens = data.get("per_seam_tokens", {})
            return CostTotals(
                month_to_date_usd=month_usd,
                seam_month_to_date_usd=float(per_seam_usd.get(seam_id, 0.0)),
                month_to_date_tokens=month_tokens,
                seam_month_to_date_tokens=int(per_seam_tokens.get(seam_id, 0)),
            )


class SettingsServiceKillStore(KillStore):
    """A KillStore adapter that reads through ``SettingsService.get_overrides()``.

    Binds the kill resolver's DB-tier read to the settings service so the ladder
    resolves in ONE place. Accepts a callable returning the settings service
    (tolerating late/lazy wiring during lifespan boot) and an optional
    pre-wiring fallback store (e.g. ``AppSettingKillStore``).
    """

    def __init__(
        self,
        service_or_getter: Any,
        *,
        fallback_store: KillStore | None = None,
    ) -> None:
        self._getter = (
            service_or_getter
            if callable(service_or_getter)
            else (lambda: service_or_getter)
        )
        self._fallback = fallback_store

    async def get(self, key: str) -> str | None:
        """Read one raw value — RAISING when BOTH tiers fail (D2).

        The settings tier is tried first; on failure the fallback store
        is tried. If the fallback ALSO fails (or is absent), the error
        is RE-RAISED so the resolver's fail-closed ``switch_unreadable``
        branch is reachable in production. Only "no row in either
        tier" returns ``None``.
        """
        service = self._getter()
        settings_error: Exception | None = None
        if service is not None and hasattr(service, "get_overrides"):
            try:
                overrides = await offload(service.get_overrides)
                if key in overrides:
                    val = overrides[key]
                    return str(val) if val is not None else None
            except Exception as exc:
                settings_error = exc
                log_exception(
                    logger,
                    logging.WARNING,
                    "[SettingsServiceKillStore] get_overrides failed for %s",
                    exc,
                    key,
                )
        if self._fallback is not None:
            # The fallback store re-raises its own read errors (D2).
            return await self._fallback.get(key)
        if settings_error is not None:
            raise settings_error
        return None

    async def set(self, key: str, value: str) -> None:
        if self._fallback is not None:
            await self._fallback.set(key, value)


def build_s1_seam_record(
    question_id: str = "s1_candidate_selection",
) -> SeamRecord:
    """Build the canonical SeamRecord for S1 (snapshot L3 candidate selection).

    Monotonicity is WITHIN_SET: it selects within candidate_ids and cannot empty the set.
    """

    def _build_input(context: Mapping[str, Any]) -> DecisionRequest:
        query = str(context.get("query", ""))
        candidates_raw = context.get("candidate_ids", ())
        if not isinstance(candidates_raw, (list, tuple)):
            # duck-typed: a snapshot-row stand-in, not a Sequence[Any]
            candidates_raw = extract_snapshot_ids(candidates_raw)  # type: ignore[arg-type]
        choices = tuple(
            DecisionChoice(value=cid)
            for cid in candidates_raw
            if isinstance(cid, str) and cid.strip()
        )
        question = DecisionQuestion(
            kind=QuestionKind.CHOICE,
            name=question_id,
            instructions="Select the most relevant snapshot for the user query.",
            choices=choices,
        )
        return DecisionRequest(
            input=f"User query: {query}",
            questions=(question,),
        )

    def _apply(heuristic: Any, overlay: Any) -> Any:
        # heuristic is a sequence of snapshot IDs
        # overlay is a DecisionAnswer (from proxy)
        if overlay is None:
            return heuristic
        ranked = overlay.ranked_values()
        if not ranked:
            return heuristic
        return ranked

    return SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=_build_input,
        apply=_apply,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
        question_id=question_id,
    )
