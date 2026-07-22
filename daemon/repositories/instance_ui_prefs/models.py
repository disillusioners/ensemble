"""SQLModel table definition for instance UI preferences.

Single table backing the UI-only "pin" + "color tag" + "icon tag"
preferences attached to each instance (see package docstring).

* :class:`InstanceUiPrefs` — one row per instance, keyed by
  ``instance_id``. Created lazily on the first ``upsert`` call from
  ``PUT /api/instances/{id}/ui-prefs``, read on every
  ``GET /api/instances`` page-load so the frontend can render the
  pin / color / icon overlay without a per-instance round trip, and
  deleted by ``DELETE /api/instances/{id}/ui-prefs`` (or indirectly
  via the hard-delete cascade in
  :meth:`SQLModelInstanceRepository.hard_delete_tree`, which wipes
  ``instance_ui_prefs`` rows as step 9b alongside the other dependent
  tables).

The table is created on every backend by
``SQLModel.metadata.create_all()`` at startup (the model is imported
from ``daemon/repositories/__init__.py`` so it is registered with
``SQLModel.metadata`` before ``create_all`` runs). Additive columns
on this table for existing databases are handled by
:meth:`InstanceManager._ensure_postgres_columns` on PostgreSQL (the
``.sql`` migration runner is SQLite-only); see the ``icon_tag``
statement there for the nullable ``VARCHAR`` add.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


class InstanceUiPrefs(SQLModel, table=True):
    """UI-only preferences (``pinned`` + ``color_tag``) for one instance.

    One row per instance. The row is created lazily on the first
    ``upsert`` from the API — instances that have never been pinned
    or tagged simply have no row (the API returns ``pinned=None`` /
    ``color_tag=None`` / ``pinned_at=None`` in the merge step). The
    primary key is the ``instance_id`` itself, so the row count
    always matches the number of instances the user has touched via
    the UI.

    The table is intentionally separate from ``instances`` so the
    agent-tool's :class:`Instance` model stays insulated from
    UI-only fields — ``Instance.to_dict()`` is NOT modified. Merge
    happens at the API router layer (see
    :mod:`daemon.routers.instances`).

    Attributes:
        instance_id: Primary key. The instance this row belongs to.
            Logical FK to ``instances.instance_id`` — no DB-level FK
            is declared because the ``instance_ui_prefs`` model lives
            in a separate repository package and ``instances`` is the
            "core" table that should not depend on UI extras. The
            hard-delete cascade keeps the table clean even without a
            formal FK.
        pinned: Whether the user has pinned this instance in the
            UI. Defaults to ``False``. Managed as a native ``bool``
            column (SQLite + PostgreSQL both support it natively, so
            ``Field(default=False)`` is sufficient — no
            ``sa_column=Column(Boolean, ...)`` workaround required).
        pinned_at: ISO-8601 UTC timestamp set when ``pinned`` last
            transitioned to ``True``; ``None`` when not pinned. Managed
            by :meth:`InstanceUiPrefsRepository.upsert` (the HTTP
            body shape does not expose it directly).
        color_tag: Free-form color tag string (``"red"``, ``"blue"``,
            ``"#ff0000"``, etc.) chosen by the user. ``None`` when
            no tag is set. Capped at 32 characters via
            ``max_length=32``; null values are allowed
            (``nullable=True``).
        created_at: ISO-8601 timestamp, set once on row creation.
        updated_at: ISO-8601 timestamp refreshed on every successful
            ``upsert``. ``None`` until the first update (matches the
            ISO-8601 ``TEXT`` convention used by sibling repos such
            as :mod:`daemon.repositories.report_injection`).
    """

    __tablename__ = "instance_ui_prefs"

    instance_id: str = Field(
        sa_column=Column(String, primary_key=True, nullable=False),
        max_length=64,
    )
    pinned: bool = Field(default=False)
    pinned_at: str | None = Field(default=None)
    color_tag: str | None = Field(
        sa_column=Column(String, nullable=True),
        max_length=32,
        default=None,
    )
    icon_tag: str | None = Field(
        sa_column=Column(String, nullable=True),
        max_length=64,
        default=None,
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str | None = Field(default=None)
