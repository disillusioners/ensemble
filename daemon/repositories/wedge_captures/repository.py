"""The ``wedge_captures`` repository (D10 / D12 / D23 / A3 / T11 / T12).

WHAT IT DOES
------------
* **INSERT** one capture's metadata row.
* **LIST** the recent captures (the read-back surface's LIST).
* **FETCH** one capture by id (the read-back surface's FETCH).
* **RECONCILE** the pending sidecars — the rows whose write failed
  (T11/T12/A3).
* **PRUNE** the rows whose capture directory is gone.

THE ROW IS AN INDEX, NOT THE EVIDENCE (D10 / D12)
--------------------------------------------------
The payload lives on disk. The row is a path plus a small metrics
snapshot. A capture whose row is missing is still discoverable through
the filesystem fallback — the LIST endpoint scans the capture
directory when the table is unreachable.

THE RECONCILER (A3)
-------------------
At watchdog start and before each capture (W6 / AC19), the reconciler
scans for ``.pending_row.json`` sidecars and re-inserts each over the
DAEMON'S POOLED ENGINE (``Session(self._engine)``) — NOT the capture
path's own one-shot connection. T12's "one-shot" wording was a
plan-time assumption; the pooled-engine shape is the ACCEPTED
deviation (leader-picked, W5: boot-time and mid-run engine use is the
lower-risk shape, and the honesty fix is this paragraph — not a
reroute of a healthy boot-time path through a second connect).
Duplicate ``capture_dir`` rows are absorbed by the pre-insert
existence check plus the table's UNIQUE constraints; a reconciled
sidecar is deleted on success. The sidecar is a RECONCILE artifact —
it is never part of the capture's evidence payload and never a
capture of its own.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from .models import WedgeCapture

logger = logging.getLogger(__name__)


class WedgeCaptureRepository:
    """The ``wedge_captures`` repository.

    Args:
        engine: The daemon's engine (the app DB).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        """The engine this repository writes through."""
        return self._engine

    # ── write ──────────────────────────────────────────────────────────
    def insert(self, row: dict[str, Any]) -> WedgeCapture | None:
        """Insert one capture row.

        A duplicate ``capture_dir`` / ``capture_id`` is absorbed (the
        row already exists) — the reconciler and the writer can both
        attempt the same row without a hard failure.
        """
        payload = dict(row)
        payload.setdefault("created_at", WedgeCapture.__table__.columns["created_at"].default.arg)
        try:
            with Session(self._engine) as session:
                existing = session.exec(
                    select(WedgeCapture).where(
                        WedgeCapture.capture_dir == payload.get("capture_dir")
                    )
                ).first()
                if existing is not None:
                    return existing
                model = WedgeCapture(**payload)
                session.add(model)
                session.commit()
                session.refresh(model)
                return model
        except Exception as exc:  # noqa: BLE001 — a metadata row never wedges
            logger.warning("Wedge-capture row insert failed: %s", exc)
            return None

    # ── read ───────────────────────────────────────────────────────────
    def list_recent(self, limit: int = 50) -> list[WedgeCapture]:
        """The most recent captures, newest first."""
        try:
            with Session(self._engine) as session:
                statement = select(WedgeCapture).order_by(
                    WedgeCapture.captured_at.desc()
                ).limit(max(1, int(limit)))
                return list(session.exec(statement).all())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture list failed: %s", exc)
            return []

    def get(self, capture_id: str) -> WedgeCapture | None:
        """One capture by its id, or ``None``."""
        try:
            with Session(self._engine) as session:
                return session.exec(
                    select(WedgeCapture).where(WedgeCapture.capture_id == capture_id)
                ).first()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture fetch failed: %s", exc)
            return None

    def get_by_dir(self, capture_dir: str) -> WedgeCapture | None:
        """One capture by its directory path, or ``None``."""
        try:
            with Session(self._engine) as session:
                return session.exec(
                    select(WedgeCapture).where(
                        WedgeCapture.capture_dir == capture_dir
                    )
                ).first()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture fetch-by-dir failed: %s", exc)
            return None

    # ── prune ──────────────────────────────────────────────────────────
    def delete_by_dirs(self, capture_dirs: list[str]) -> int:
        """Delete the rows whose capture directory was pruned.

        The capture prune is DIRECTORY-AUTHORITATIVE (D23 / A3); the
        rows go afterwards, BY ``capture_dir``.
        """
        if not capture_dirs:
            return 0
        deleted = 0
        try:
            with Session(self._engine) as session:
                for capture_dir in capture_dirs:
                    row = session.exec(
                        select(WedgeCapture).where(
                            WedgeCapture.capture_dir == capture_dir
                        )
                    ).first()
                    if row is None:
                        continue
                    session.delete(row)
                    deleted += 1
                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wedge-capture row prune failed: %s", exc)
        return deleted

    # ── reconcile ──────────────────────────────────────────────────────
    def reconcile_pending(self, pending_rows: list[tuple[Path, dict[str, Any]]]) -> int:
        """Re-insert the pending sidecars' rows (T12 / A3).

        Args:
            pending_rows: ``(sidecar_path, row_payload)`` pairs, as
                returned by
                :func:`daemon.services.wedge_capture.writer.read_pending_row_sidecars`.

        Returns:
            How many sidecars were successfully reconciled (and their
            sidecar deleted).
        """
        reconciled = 0
        for sidecar, payload in pending_rows:
            try:
                with Session(self._engine) as session:
                    existing = session.exec(
                        select(WedgeCapture).where(
                            WedgeCapture.capture_dir == payload.get("capture_dir")
                        )
                    ).first()
                    if existing is None:
                        row = WedgeCapture(**payload)
                        session.add(row)
                        session.commit()
                    try:
                        sidecar.unlink()
                    except OSError:
                        pass
                    reconciled += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Wedge-capture sidecar reconcile failed for %s: %s",
                    sidecar, exc,
                )
        return reconciled

    # ── helpers ────────────────────────────────────────────────────────
    @staticmethod
    def row_to_dict(row: WedgeCapture | None) -> dict[str, Any] | None:
        """The LIST shape of one row (the canonical matrix §9 names)."""
        if row is None:
            return None
        return {
            "capture_id": row.capture_id,
            "captured_at": row.captured_at,
            "trigger_reason": row.trigger_reason,
            "trigger_source": row.trigger_source,
            "boot_epoch": row.boot_epoch,
            "host": row.host,
            "summary": row.summary,
            "auto_continue_mode": row.auto_continue_mode,
            "file_count": row.file_count,
            "total_bytes": row.total_bytes,
            "capture_dir": row.capture_dir,
        }


__all__ = ["WedgeCaptureRepository"]
