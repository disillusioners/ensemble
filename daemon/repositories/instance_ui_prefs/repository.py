"""SQLModel-based InstanceUiPrefs repository.

Persistence layer for the ``instance_ui_prefs`` table. Exposes the
four primitives the API router needs:

* :meth:`get` — single-row lookup by ``instance_id``. Returns
  ``None`` when the instance has never been pinned or tagged (the
  default state — most instances have no row at all).
* :meth:`get_all` — batch lookup for the list endpoint. Returns a
  dict keyed by ``instance_id`` so the caller can merge in O(1).
  An empty ``instance_ids`` input short-circuits to ``{}`` without
  hitting the DB.
* :meth:`upsert` — partial-update semantics: only the fields the
  caller explicitly passes are changed. Creates the row lazily on
  first call. Manages the ``pinned_at`` side-effect automatically
  (set on pin, cleared on unpin, left alone on omission).
* :meth:`delete` — removes a prefs row. Idempotent at the HTTP
  layer: ``DELETE /api/instances/{id}/ui-prefs`` returns
  ``{"deleted": false}`` when nothing matched.

The repository is intentionally sync; callers bridge to async via
direct invocation in FastAPI handlers. The methods are fast indexed
lookups on a tiny table so wrapping in ``asyncio.to_thread`` adds
overhead without measurable benefit (matches the pattern used by the
adjacent ``self._report_injection_repo`` field on
:class:`InstanceManager`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import InstanceUiPrefs

logger = logging.getLogger(__name__)


class InstanceUiPrefsRepository:
    """SQLModel-based repository for the ``instance_ui_prefs`` table.

    All methods are synchronous; the FastAPI handlers invoke them
    directly. They are fast indexed lookups on a tiny table so the
    usual ``asyncio.to_thread`` wrap is unnecessary overhead.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy ``Engine`` bound to a SQLite or
                PostgreSQL database. Should be the same shared engine
                used by the other repositories to avoid lock
                contention.
        """
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _now_iso(self) -> str:
        """Return current UTC time as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, instance_id: str) -> InstanceUiPrefs | None:
        """Return the UI-prefs row for ``instance_id``, or ``None``.

        Used by the per-instance GET path (single-row fetch). The
        list endpoint uses :meth:`get_all` instead.

        Args:
            instance_id: The instance whose prefs to load.

        Returns:
            The :class:`InstanceUiPrefs` row, or ``None`` when no
            row exists for this instance (the common case — most
            instances have never been pinned or tagged, so the row
            is created lazily on the first :meth:`upsert` call).
        """
        with Session(self.engine) as session:
            return session.exec(
                select(InstanceUiPrefs).where(
                    InstanceUiPrefs.instance_id == instance_id
                )
            ).first()

    def get_all(
        self, instance_ids: list[str]
    ) -> dict[str, InstanceUiPrefs]:
        """Batch-fetch prefs for a list of instances in one query.

        Returns a dict keyed by ``instance_id`` so the router's merge
        step can look up each row in O(1). Instances that have no
        prefs row are simply absent from the dict — the merge step
        uses ``prefs_map.get(...)`` and treats ``None`` as "use the
        API default" (``pinned=None``, ``color_tag=None``,
        ``pinned_at=None``).

        An empty ``instance_ids`` input short-circuits to ``{}``
        without touching the DB — the common case when the page
        filters out everything or the request itself is degenerate.

        Args:
            instance_ids: List of instance IDs to load. May be empty
                (returns ``{}``).

        Returns:
            Dict mapping each ``instance_id`` that has a prefs row
            to its :class:`InstanceUiPrefs`. Instances without a
            row are omitted from the dict (not stored as ``None``).
        """
        if not instance_ids:
            return {}

        with Session(self.engine) as session:
            rows = session.exec(
                select(InstanceUiPrefs).where(
                    col(InstanceUiPrefs.instance_id).in_(instance_ids)
                )
            ).all()
        return {row.instance_id: row for row in rows}

    # --------------------------------------------------------
    # WRITE
    # --------------------------------------------------------

    def upsert(
        self,
        instance_id: str,
        pinned: bool | None = None,
        color_tag: str | None = None,
        clear_color_tag: bool = False,
    ) -> InstanceUiPrefs:
        """Partial-update the prefs row for ``instance_id``.

        Creates the row lazily on first call (with the defaults
        ``pinned=False``, ``pinned_at=None``, ``color_tag=None``,
        ``created_at=now``). On subsequent calls, only the fields
        explicitly changed by the caller are mutated.

        ``pinned_at`` side-effect:

        * ``pinned=True`` was passed → ``pinned_at`` is set to the
          current UTC ISO-8601 timestamp (acts as the pin start
          marker).
        * ``pinned=False`` was passed → ``pinned_at`` is cleared
          (``None``) — explicit unpin.
        * ``pinned is None`` (omitted) → ``pinned_at`` is left
          unchanged on the row. This lets a caller pass
          ``{"color_tag": "red"}`` without accidentally bumping
          ``pinned_at``.

        ``color_tag`` follow-up:

        * ``color_tag="blue"`` (or another string) was passed →
          ``color_tag`` is set to that value, including the empty
          string if a caller wants to normalize an existing tag.
        * ``color_tag=None`` without ``clear_color_tag=True`` was
          passed → ``color_tag`` is left unchanged.
        * ``color_tag=None`` with ``clear_color_tag=True`` was passed
          → ``color_tag`` is cleared to ``None``.
        * ``color_tag`` omitted → ``color_tag`` is left unchanged.
        * Both ``color_tag="x"`` and ``clear_color_tag=True`` passed →
          ``clear_color_tag=True`` wins (value is cleared to ``None``).

        Args:
            instance_id: The instance whose prefs to upsert.
            pinned: Optional new pin state. ``True`` pins and sets
                ``pinned_at``, ``False`` unpins and clears
                ``pinned_at``, and ``None`` leaves both fields
                unchanged.
            color_tag: Optional new color tag. A string sets it;
                ``None`` leaves it unchanged unless
                ``clear_color_tag`` is ``True``.
            clear_color_tag: When True, force ``color_tag`` to ``None`` even if
                ``color_tag`` is None. Used by the API router to translate an
                explicit JSON ``"color_tag": null`` into a clear operation,
                disambiguating it from "field omitted" (no-op). Defaults to
                False (preserve the partial-update "leave unchanged" semantics
                when ``color_tag`` is None).

        Returns:
            The persisted :class:`InstanceUiPrefs` row (refreshed
            with any DB-side defaults).
        """
        now_iso = self._now_iso()

        with Session(self.engine) as session:
            existing = session.exec(
                select(InstanceUiPrefs).where(
                    InstanceUiPrefs.instance_id == instance_id
                )
            ).first()

            if existing is None:
                # Lazy first-time create. Default all UI fields to
                # their not-pinned / no-tag state. Field-preservation
                # semantics only apply when a row already exists;
                # the "create new with this state" semantics are
                # driven by the arguments passed.
                row = InstanceUiPrefs(
                    instance_id=instance_id,
                    pinned=False,
                    pinned_at=None,
                    color_tag=None,
                    created_at=now_iso,
                )
                # Reflect the explicit new-state on the freshly-created
                # row. ``pinned_at`` is computed here too because
                # ``pinned=True`` on first-touch should stamp it.
                if pinned is not None:
                    row.pinned = pinned
                    row.pinned_at = now_iso if pinned else None
                if clear_color_tag:
                    row.color_tag = None
                elif color_tag is not None:
                    row.color_tag = color_tag
                # else: leave unchanged
                row.updated_at = now_iso
                session.add(row)
                session.commit()
                session.refresh(row)
                return row

            # Partial update path — only mutate fields the caller
            # explicitly passed. ``pinned_at`` is managed together
            # with ``pinned`` per the side-effect rules documented
            # on the method.
            if pinned is not None:
                existing.pinned = pinned
                existing.pinned_at = now_iso if pinned else None
            if clear_color_tag:
                existing.color_tag = None
            elif color_tag is not None:
                existing.color_tag = color_tag
            # else: leave unchanged
            existing.updated_at = now_iso

            session.add(existing)
            session.commit()
            session.refresh(existing)
            logger.info(
                f"[InstanceUiPrefs] Upserted prefs for instance "
                f"{instance_id[:8]}... (pinned={existing.pinned}, "
                f"color_tag={existing.color_tag!r})"
            )
            return existing

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, instance_id: str) -> bool:
        """Remove the prefs row for ``instance_id``.

        Idempotent: returns ``False`` when no row matched (the
        caller maps that to a 200 with ``{"deleted": false}`` to
        preserve idempotency at the HTTP layer). The instance
        itself is NOT affected — only the UI prefs row.

        Args:
            instance_id: The instance whose prefs to delete.

        Returns:
            ``True`` if a row was removed, ``False`` when no row
            existed for the given instance.
        """
        with Session(self.engine) as session:
            row = session.exec(
                select(InstanceUiPrefs).where(
                    InstanceUiPrefs.instance_id == instance_id
                )
            ).first()
            if row is None:
                return False
            session.delete(row)
            session.commit()
            logger.info(
                f"[InstanceUiPrefs] Deleted prefs row for instance "
                f"{instance_id[:8]}..."
            )
            return True
