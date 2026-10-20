"""``board_items`` / ``board_notes`` / ``board_item_tags`` — the global
work board (ari-tools Phase 1 — "The Workbook").

Three tables, ONE global namespace (D1): there is deliberately **no**
``context_key`` / ``tree_root_id`` / ``source`` / project-scoped column
anywhere in this schema. Every Ari instance (one per chat surface, one
per wake-up) and the leader read and write the SAME rows. Project and
channel are *attributes of an item* — a nullable ``project_id`` FK and
the channel/source as a plain tag string (e.g. ``telegram``) — never
separate boards. This is the deliberate opposite of ``shared_meta_kv``,
which partitions by ``context_key = tree-root instance id``.

Storage posture (the ``app_settings`` / ``resource_monitor`` precedent):

* **SQLite** — the companion migration
  ``daemon/migrations/versions/20261020_060000_create_board_tables.sql``
  is the canonical persistent record of the schema change. Every
  ``CREATE`` in that file carries ``IF NOT EXISTS`` because
  ``SQLModel.metadata.create_all`` runs FIRST on the boot path, so the
  objects already exist by the time the runner applies the file.
* **PostgreSQL** — the schema comes from ``SQLModel.metadata.create_all``
  with THESE models as the canonical source. Registration into the
  metadata happens via the one-line late-import block in
  ``daemon/manager.py`` (the snapshot / maintenance_runs / drift_events
  precedent), placed BEFORE the ``create_all`` call, so brand-new
  tables arrive on existing PG databases on the next boot with zero
  operator action. No ``_ensure_postgres_columns`` mirror is needed —
  these are brand-new tables, not column ADDs.
* 3-site index lockstep (D7): the SQLModel ``__table_args__`` index
  names **and column lists** are byte-identical to the ``CREATE INDEX``
  statements in the ``.sql`` migration (and to the N7 parity test).

Schema rulings (phase1-plan §4):

* Every string column is declared ``Text`` (never a bare ``str``,
  which would emit ``VARCHAR`` on PG and make the PG and SQLite
  schemas differ type-for-type). Note that SQLModel ignores
  Field-level column options when ``sa_column`` is supplied — the
  ``Column`` itself carries ``primary_key`` / ``nullable``.
* ``project_id`` is a REAL foreign key to ``projects.project_id`` with
  ``ON DELETE SET NULL`` — the ``ondelete=`` clause must live inside
  the ``Column``, because ``Field(foreign_key=...)`` emits none and
  would leave D14 unimplementable.
* ``linked_job_ids`` is an embedded dialect-aware JSON list
  (``JSONBType`` → JSONB on PG / JSON on SQLite). There is no job
  filter in v1, so a junction table would buy nothing; job rows are
  soft-deleted, so a hard FK would either block job deletion or
  cascade-delete a board item.
* The two CHILD foreign keys (``board_notes.item_id`` and
  ``board_item_tags.item_id``) carry **NO** ``ondelete=`` clause at
  all — i.e. ``NO ACTION``. A hard delete of the parent item is
  therefore refused **by the database**, which is what makes the
  soft-delete-only / append-only position structural rather than a
  convention. (``project_tags`` is the exact declaration precedent.)
* ``board_notes`` is APPEND-ONLY: the table has no ``updated_at``, no
  ``deleted_at`` and no ``revision`` column, so the schema itself
  cannot express "this note was edited".
* All timestamps are fixed-width UTC ISO-8601 with microseconds ALWAYS
  present (D7). The ``due_before`` filter compares TEXT
  lexicographically, which is only chronologically correct under a
  fixed-width format. The house ``_now_iso()``
  (``daemon/repositories/project/models.py``) drops microseconds when
  they are zero, so the board uses its own helper; the house helper is
  left untouched (no blast radius).
* ONE ``_to_utc_iso()`` normalizer serves BOTH the ``due_at`` WRITE
  path and the ``due_before`` FILTER path (R11). A raw agent string
  must never reach SQL: a ``+07:00`` offset compared as TEXT against
  a UTC-stored row **silently inverts** the filter
  (``'2026-10-20T08:00:00+07:00' < '2026-10-20T01:55:40.123456+00:00'``
  is False although 01:00Z < 01:55Z), so the filter would return the
  LATER items and drop the EARLIER ones — no error, no visible
  symptom. Naive (tz-less) input is rejected with
  ``BoardValidationError``.

The error hierarchy is DEFINED here so that ``_to_utc_iso`` can raise
``BoardValidationError`` directly (one normalizer, no per-call-site
fix-up, no circular import). It is RE-EXPORTED by
``daemon.services.board_service`` — import it from either module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, ForeignKey, Index, Integer, Text
from sqlmodel import Field, SQLModel

from ..repositories.infra.types import JSONBType


# ── Status enum (service-validated; no DB CHECK) ───────────────────────────
#
# D9: transitions are UNRESTRICTED (any -> any, including reopening a
# ``done`` item). The board is a human-facing work surface, not a
# workflow engine; the notes timeline is the audit trail, not the
# status field. The only validation is membership in this set, checked
# at the SERVICE boundary (the "forward-compatible enum tag validated at
# the service boundary, NOT at the model" precedent).

BOARD_STATUS_TODO = "todo"
BOARD_STATUS_IN_PROGRESS = "in_progress"
BOARD_STATUS_BLOCKED = "blocked"
BOARD_STATUS_DONE = "done"

BOARD_STATUSES: frozenset[str] = frozenset({
    BOARD_STATUS_TODO,
    BOARD_STATUS_IN_PROGRESS,
    BOARD_STATUS_BLOCKED,
    BOARD_STATUS_DONE,
})

#: The "open" convention — ``board_list(status="open")`` expands to
#: exactly this set. ``done`` is the only terminal status.
BOARD_OPEN_STATUSES: tuple[str, ...] = (
    BOARD_STATUS_TODO,
    BOARD_STATUS_IN_PROGRESS,
    BOARD_STATUS_BLOCKED,
)


# ── Error hierarchy (re-exported by daemon.services.board_service) ─────────


class BoardError(RuntimeError):
    """Base class for every board-domain error."""


class BoardItemNotFound(BoardError):
    """Raised when a board item id does not resolve to a row."""


class BoardValidationError(BoardError):
    """Raised for an invalid board input (naive timestamp, bad status,
    out-of-range priority, over-long title, ...).

    The message is an AUTHORED, safe string — it is echoed verbatim in
    the tool error envelope, so it must never carry a raw exception
    representation, a path, or a credential.
    """


# ── Timestamp helpers (D7 / R11) ───────────────────────────────────────────


def _now_iso() -> str:
    """Fixed-width UTC ISO-8601 with microseconds ALWAYS present.

    ``2026-10-20T01:55:40.123456+00:00``

    The board deliberately does NOT use the house ``_now_iso()``
    (``daemon/repositories/project/models.py``) — that helper omits the
    microseconds when they are zero, which yields a VARIABLE-width
    string and breaks the lexicographic (TEXT) comparison the
    ``due_before`` filter depends on.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _to_utc_iso(value: str, *, field: str = "timestamp") -> str:
    """Normalize a tz-aware ISO-8601 string to the fixed-width UTC form.

    THE single normalizer for the deadline path: it serves BOTH the
    ``due_at`` WRITE path and the ``due_before`` FILTER path, so the
    two can never disagree (R11). A raw agent string must never reach
    SQL — a non-UTC offset compared as TEXT against a UTC-stored row
    silently inverts the filter.

    Args:
        value: A timezone-aware ISO-8601 string (``Z`` suffix accepted).
        field: The field name used in the error message, so the caller
            gets an authored, field-specific message (e.g. ``due_at``).

    Returns:
        The fixed-width UTC ISO-8601 form —
        ``2026-10-20T01:55:40.123456+00:00`` — which sorts
        lexicographically in chronological order.

    Raises:
        BoardValidationError: the value is not a parseable, timezone-AWARE
            ISO-8601 string. A naive (tz-less) input is rejected — the
            service boundary never guesses an offset.
    """
    if not isinstance(value, str) or not value.strip():
        raise BoardValidationError(
            f"{field} must be a timezone-aware ISO-8601 string"
        )
    raw = value.strip()
    candidate = raw
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise BoardValidationError(
            f"{field} must be a timezone-aware ISO-8601 string"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BoardValidationError(
            f"{field} must be a timezone-aware ISO-8601 string"
        )
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


# ── board_items ────────────────────────────────────────────────────────────


class BoardItem(SQLModel, table=True):
    """One durable work item on the global board.

    A work item is a human-facing task with a stable id
    (``wi-<uuid8>``), a markdown body, a 4-value status, tags, an
    optional project FK, an optional tz-aware deadline, a list of
    linked job ids, and author / created / updated provenance.
    """

    __tablename__ = "board_items"

    __table_args__ = (
        # 3-site index lockstep — names AND column lists MUST match
        # ``daemon/migrations/versions/20261020_060000_create_board_tables.sql``
        # (and the N7 parity test). The status filter and the ordering
        # when status is pinned; a single-column status index would be
        # subsumed by this composite.
        Index("idx_board_items_status_updated", "status", "updated_at"),
        # The default ``ORDER BY updated_at DESC`` + LIMIT.
        Index("idx_board_items_updated_at", "updated_at"),
        # The project filter.
        Index("idx_board_items_project_id", "project_id"),
        # The ``due_before`` range (NULLs excluded by the predicate).
        Index("idx_board_items_due_at", "due_at"),
    )

    id: str = Field(
        sa_column=Column("id", Text, primary_key=True),
        description="Stable item id — 'wi-' + uuid4().hex[:8].",
    )
    title: str = Field(
        sa_column=Column("title", Text, nullable=False),
        description="Short human title (1-512 chars).",
    )
    body_md: str = Field(
        default="",
        sa_column=Column("body_md", Text, nullable=False, default=""),
        description="Markdown body.",
    )
    status: str = Field(
        default=BOARD_STATUS_TODO,
        sa_column=Column(
            "status", Text, nullable=False, default=BOARD_STATUS_TODO
        ),
        description="todo | in_progress | blocked | done.",
    )
    project_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            "project_id",
            Text,
            ForeignKey("projects.project_id", ondelete="SET NULL"),
            nullable=True,
        ),
        description="Optional project FK — ON DELETE SET NULL.",
    )
    due_at: Optional[str] = Field(
        default=None,
        sa_column=Column("due_at", Text, nullable=True),
        description=(
            "Deadline — fixed-width UTC ISO-8601 with microseconds. "
            "NULL when unset."
        ),
    )
    priority: Optional[int] = Field(
        default=None,
        sa_column=Column("priority", Integer, nullable=True),
        description="1 (lowest) .. 5 (highest). NULL = unset.",
    )
    linked_job_ids: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            "linked_job_ids", JSONBType, nullable=False, default=list
        ),
        description="Opaque job ids linked to this item. Only ever grows.",
    )
    created_by_instance: str = Field(
        sa_column=Column("created_by_instance", Text, nullable=False),
        description="Authoring instance id — stamped by the service.",
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="Fixed-width UTC ISO-8601.",
    )
    updated_at: str = Field(
        sa_column=Column("updated_at", Text, nullable=False),
        description="Fixed-width UTC ISO-8601 — bumped on any mutation.",
    )


# ── board_notes (APPEND-ONLY child table) ──────────────────────────────────


class BoardNote(SQLModel, table=True):
    """One append-only note on a board item's timeline.

    There is NO update / delete path for a note anywhere in the
    codebase, and the table has no column that could express one. The
    child FK to ``board_items.id`` is **NO ACTION** (no ``ondelete=``
    clause at all), so a hard delete of the parent item is refused by
    the database.
    """

    __tablename__ = "board_notes"

    __table_args__ = (
        # 3-site index lockstep — the per-item timeline read path.
        Index("idx_board_notes_item_id_ts", "item_id", "ts"),
    )

    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        sa_column_kwargs={"autoincrement": True},
        description=(
            "SERIAL PK on PG (INTEGER PRIMARY KEY AUTOINCREMENT on "
            "SQLite). The dialect incantation is handled by SQLAlchemy."
        ),
    )
    item_id: str = Field(
        sa_column=Column(
            "item_id", Text, ForeignKey("board_items.id"), nullable=False
        ),
        description="Parent board item id — FK is NO ACTION.",
    )
    ts: str = Field(
        sa_column=Column("ts", Text, nullable=False),
        description="Fixed-width UTC ISO-8601 stamp.",
    )
    author_instance: str = Field(
        sa_column=Column("author_instance", Text, nullable=False),
        description="Authoring instance id — stamped by the service.",
    )
    text: str = Field(
        sa_column=Column("text", Text, nullable=False),
        description="Note body. Never edited, never deleted.",
    )


# ── board_item_tags (junction table) ───────────────────────────────────────


class BoardItemTag(SQLModel, table=True):
    """Junction table for the item <-> tag many-to-many relationship.

    Uniqueness on ``(item_id, tag)`` is enforced by the composite
    primary key, so a repeated tag can never produce two stored rows.
    The junction rows are written **only** by ``create_item`` — there
    is no tag-insert and no tag-delete anywhere in the write path, so
    ``tags`` are IMMUTABLE post-create.

    The reverse tag filter (``WHERE tag = ?``) cannot be served by the
    composite PK, hence the dedicated ``(tag, item_id)`` index — the
    ``project_tags`` precedent full-scans; do not copy that.
    """

    __tablename__ = "board_item_tags"

    __table_args__ = (
        # 3-site index lockstep — the reverse tag filter.
        Index("idx_board_item_tags_tag_item", "tag", "item_id"),
    )

    item_id: str = Field(
        sa_column=Column(
            "item_id",
            Text,
            ForeignKey("board_items.id"),
            primary_key=True,
        ),
        description="Parent board item id — FK is NO ACTION.",
    )
    tag: str = Field(
        sa_column=Column("tag", Text, primary_key=True),
        description="Tag string (plain TEXT; no case folding in v1).",
    )


__all__ = [
    "BOARD_OPEN_STATUSES",
    "BOARD_STATUS_BLOCKED",
    "BOARD_STATUS_DONE",
    "BOARD_STATUS_IN_PROGRESS",
    "BOARD_STATUS_TODO",
    "BOARD_STATUSES",
    "BoardError",
    "BoardItem",
    "BoardItemNotFound",
    "BoardItemTag",
    "BoardNote",
    "BoardValidationError",
    "_now_iso",
    "_to_utc_iso",
]
