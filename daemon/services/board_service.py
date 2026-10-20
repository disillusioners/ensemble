"""The board service — the DB-backed work board (ari-tools Phase 1).

One global namespace, no per-chat / per-project / per-tree
partitioning (D1): every Ari instance and the leader read and write the
SAME rows. Project and channel are attributes of an ITEM (a nullable
``project_id`` FK, the channel as a plain tag string) — never separate
boards.

The service is a CLASS (D5) — it has exactly one dependency (the
``Engine``), it owns a write lock, and it is engine-bound like
:class:`daemon.services.secret_service.SecretService`. The manager
constructs one instance and exposes it through a read-only property.

Storage
-------
Three tables (see :mod:`daemon.models.board`): ``board_items``,
``board_notes`` (APPEND-ONLY), ``board_item_tags`` (junction).

FROZEN INTERFACE (binding for Phase 3 — names AND keyword names)
-----------------------------------------------------------------
``list_items`` and ``get_item`` are the ONLY tight seam in this
commission. They are frozen at the end of Phase 1 and Phase 3 consumes
exactly this signature. Changing either later is a breaking change to
a frozen interface, not a refactor. The two signatures, verbatim::

    list_items(*, status: str | None = None, project_id: str | None = None,
               tag: str | None = None, due_before: str | None = None,
               limit: int = 50, with_notes: bool = False) -> dict

    get_item(*, item_id: str) -> dict

``list_items`` returns ``{"items": [...], "count": N}`` PLUS two
ADDITIVE pagination keys — ``has_more`` (True iff more items match
the filters beyond this page; probed by fetching ``limit + 1`` rows
in the SAME query, never a ``COUNT(*)``) and ``applied_limit`` (the
post-clamp page size, so a clamp is visible to the caller). Each
item carries the canonical item shape (see ``_row_to_item``) and NO
``notes`` key. ``get_item`` returns the same item shape PLUS a
``notes`` list, oldest-first. The SIGNATURE restated above is the
frozen contract; the additive result keys extend the payload without
touching it.

The item shape (canonical, all four tools)::

    {id, title, body_md, status, tags[], project_id, due_at, priority,
     linked_job_ids[], created_by_instance, created_at, updated_at}

The create-only field set is ``{title, project_id, tags, priority}``:
``tags`` is IMMUTABLE post-create (the junction rows are written only
by ``create_item``) and ``board_update`` carries no tag, title,
project_id or priority parameter at all.

The deadline path
-----------------
``due_at`` (write) and ``due_before`` (filter) are normalised by the
SINGLE board-local helper ``_to_utc_iso`` (in :mod:`daemon.models.board`)
— a raw agent string never reaches SQL. A tz-aware input carrying a
non-UTC offset compared as TEXT against a UTC-stored row SILENTLY
INVERTS the filter (R11), so the filter returns the later items and
drops the earlier ones. A naive (tz-less) input is rejected with
``BoardValidationError``.

Concurrency
-----------
A process-level ``threading.RLock`` is held across every session
block. Under a SQLite ``StaticPool`` concurrent writers corrupt the
prepared-statement cache, and the lock also makes the
``linked_job_ids`` read-modify-write atomic. The daemon is
single-process today, so a process-level lock is sufficient.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..models.board import (
    BOARD_OPEN_STATUSES,
    BOARD_STATUSES,
    BoardError,
    BoardItem,
    BoardItemNotFound,
    BoardItemTag,
    BoardNote,
    BoardValidationError,
    _now_iso,
    _to_utc_iso,
)

logger = logging.getLogger(__name__)

#: Maximum title length (the schema bound).
MAX_TITLE_LENGTH = 512

#: Inclusive priority range (1 lowest .. 5 highest).
MIN_PRIORITY = 1
MAX_PRIORITY = 5

#: ``board_list`` page-size bounds.
MIN_LIMIT = 1
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class BoardService:
    """The in-process board service (Phase 1 scope).

    Public methods are the tool surface behind the four ``board_*``
    tools (see :mod:`daemon.tools.board_tools`). The service
    implements NO per-call access check — the access boundary is the
    per-agent ``tools.allow`` category gate (the ``board`` privileged
    category + the day-one allow-list on the ari / leader meta.json)
    AND the tool body's own ``agent_id`` gate.
    """

    def __init__(self, engine: Engine) -> None:
        """Initialize the service.

        Args:
            engine: SQLAlchemy engine bound to the daemon's database
                (PostgreSQL in production, SQLite in dev). The same
                engine the manager uses for every other repository.
        """
        self._engine = engine
        # Re-entrant DB-bound serialisation — see the module docstring.
        self._write_lock = threading.RLock()

    # ── public API ────────────────────────────────────────────────────────

    def create_item(
        self,
        *,
        title: str,
        body_md: str = "",
        project_id: str | None = None,
        tags: list[str] | None = None,
        due_at: str | None = None,
        priority: int | None = None,
        created_by_instance: str,
    ) -> dict:
        """Create one board item (and its tag rows) in a single transaction.

        Args:
            title: Short human title (1-512 chars). Required.
            body_md: Markdown body. Defaults to empty.
            project_id: Optional project id (nullable FK).
            tags: Optional tag list. De-duplicated IN THE SERVICE —
                order-preserving, first occurrence wins (the junction
                PK dedupes the stored ROWS, not the incoming list).
            due_at: Optional tz-aware ISO-8601 deadline. Normalised to
                fixed-width UTC by ``_to_utc_iso``.
            priority: Optional 1-5. NULL means unset.
            created_by_instance: The authoring instance id. Stamped by
                the caller (the tool closure) — an agent can never
                supply this value.

        Returns:
            The created item in the canonical item shape.

        Raises:
            BoardValidationError: an invalid title, priority, or
                deadline.
            BoardError: an unexpected persistence failure.
        """
        clean_title = self._validate_title(title)
        clean_body = body_md if isinstance(body_md, str) else ""
        clean_project = project_id.strip() if isinstance(project_id, str) and project_id.strip() else None
        clean_priority = self._validate_priority(priority)
        clean_due = self._normalize_due(due_at, "due_at")
        clean_tags = self._normalize_tags(tags)

        with self._write_lock:
            for attempt in (1, 2):
                item_id = self._new_item_id()
                try:
                    with Session(self._engine) as session:
                        with session.begin():
                            now = _now_iso()
                            row = BoardItem(
                                id=item_id,
                                title=clean_title,
                                body_md=clean_body,
                                status="todo",
                                project_id=clean_project,
                                due_at=clean_due,
                                priority=clean_priority,
                                linked_job_ids=[],
                                created_by_instance=created_by_instance,
                                created_at=now,
                                updated_at=now,
                            )
                            session.add(row)
                            for tag in clean_tags:
                                session.add(BoardItemTag(item_id=item_id, tag=tag))
                            session.flush()
                            projection = self._row_to_item(row, clean_tags)
                    return projection
                except IntegrityError as exc:
                    if attempt == 1 and self._is_id_pk_collision(exc):
                        # The id is generated HERE, never by the agent.
                        # A birthday collision on the 32-bit id space is
                        # the ONLY expected source of this error: retry
                        # ONCE with a fresh id and never surface the
                        # collision to the agent.
                        logger.warning(
                            "board: id collision on %s — regenerating (attempt 2)",
                            item_id,
                        )
                        continue
                    raise self._translate_integrity_error(exc) from exc
            raise BoardError("board operation failed")

    def update_item(
        self,
        *,
        item_id: str,
        status: str | None = None,
        note: str | None = None,
        add_job_id: str | None = None,
        body_md: str | None = None,
        due_at: str | None = None,
        author_instance: str,
    ) -> dict:
        """Update one board item and optionally append a note.

        The note INSERT is stamped inside the SAME transaction as the
        item UPDATE (the audit-event contract). A pure read never
        bumps ``updated_at``; an update with no mutable field is a
        no-op.

        Args:
            item_id: The item to update.
            status: New status — ``todo | in_progress | blocked | done``.
                Transitions are UNRESTRICTED (any to any, including
                reopening a ``done`` item).
            note: Optional note text. Appended to the timeline —
                notes are APPEND-ONLY, never edited, never deleted.
            add_job_id: Optional job id to link. The list only ever
                grows; there is no unlink in v1.
            body_md: Optional replacement markdown body.
            due_at: Optional replacement deadline (tz-aware ISO-8601).
                REPLACEABLE but not clearable — ``due_at=None`` means
                "no change".
            author_instance: The authoring instance id, stamped by the
                caller (the tool closure).

        Returns:
            The updated item in the canonical item shape, plus
            ``note_appended`` (bool).

        Raises:
            BoardItemNotFound: no row carries that id.
            BoardValidationError: an invalid status or deadline.
        """
        if status is not None and status not in BOARD_STATUSES:
            raise BoardValidationError(
                "status must be one of: todo, in_progress, blocked, done"
            )
        clean_due = self._normalize_due(due_at, "due_at") if due_at is not None else None
        note_text = note.strip() if isinstance(note, str) else ""
        job_id = add_job_id.strip() if isinstance(add_job_id, str) else ""

        with self._write_lock:
            with Session(self._engine) as session:
                with session.begin():
                    row = session.exec(
                        select(BoardItem).where(BoardItem.id == item_id)
                    ).first()
                    if row is None:
                        raise BoardItemNotFound(f"board item not found: {item_id}")

                    changed = False
                    if status is not None and status != row.status:
                        row.status = status
                        changed = True
                    if body_md is not None and body_md != row.body_md:
                        row.body_md = body_md
                        changed = True
                    if clean_due is not None and clean_due != row.due_at:
                        row.due_at = clean_due
                        changed = True
                    if job_id:
                        linked = list(row.linked_job_ids or [])
                        if job_id not in linked:
                            linked.append(job_id)
                            row.linked_job_ids = linked
                            changed = True

                    note_appended = False
                    if note_text:
                        self._append_note(
                            session,
                            item_id=item_id,
                            author_instance=author_instance,
                            text=note_text,
                        )
                        note_appended = True
                        changed = True

                    if changed:
                        row.updated_at = _now_iso()
                    session.flush()
                    tags = self._load_tags_bulk(session, [item_id]).get(item_id, [])
                    projection = self._row_to_item(row, tags)
            projection["note_appended"] = note_appended
            return projection

    def list_items(
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        tag: str | None = None,
        due_before: str | None = None,
        limit: int = DEFAULT_LIMIT,
        with_notes: bool = False,
    ) -> dict:
        """List board items, newest-updated first.

        The ordering is ``ORDER BY updated_at DESC`` — a CONTRACT, not
        an implementation detail (the ``idx_board_items_updated_at``
        index depends on it).

        Hydration is BATCHED: the items are one query, the tags are
        ONE ``WHERE item_id IN (...)`` query for the whole page, and
        ``with_notes=True`` adds ONE more for the notes — at most three
        queries per render instead of 1+N.

        Args:
            status: ``todo | in_progress | blocked | done``, or
                ``"open"`` (the convention: ``todo|in_progress|blocked``
                — ``done`` is the only terminal status). ``None`` means
                no filter.
            project_id: Optional project filter.
            tag: Optional single-tag filter.
            due_before: Optional tz-aware ISO-8601 instant. Items due
                STRICTLY before it are returned. Normalised to UTC
                before the comparison — a raw string never reaches the
                query.
            limit: Max items to return (clamped to 1..200); the
                applied value is reported back as ``applied_limit``.
            with_notes: When True, each item ALSO carries a ``notes``
                list (oldest-first).

        Returns:
            ``{"items": [...], "count": N, "has_more": bool,
            "applied_limit": int}`` — the two keys beyond ``count``
            are ADDITIVE pagination metadata. ``has_more`` is True iff
            at least one MORE item matches the filters beyond this
            page, probed by fetching ``bounded_limit + 1`` rows in the
            SAME query (zero extra queries, no ``COUNT(*)``);
            ``applied_limit`` is the page size actually applied after
            the 1..200 clamp, so a caller that asked for more can SEE
            the clamp instead of silently receiving a smaller page
            (review F1).

        Raises:
            BoardValidationError: an unknown status value or a naive
                ``due_before``.
        """
        bounded_limit = self._clamp_limit(limit)

        statement = select(BoardItem)
        if status is not None:
            if status == "open":
                statement = statement.where(
                    BoardItem.status.in_(BOARD_OPEN_STATUSES)
                )
            elif status in BOARD_STATUSES:
                statement = statement.where(BoardItem.status == status)
            else:
                raise BoardValidationError(
                    "status must be one of: todo, in_progress, blocked, done, open"
                )
        if project_id:
            statement = statement.where(BoardItem.project_id == project_id)
        if tag:
            statement = statement.where(
                BoardItem.id.in_(
                    select(BoardItemTag.item_id).where(BoardItemTag.tag == tag)
                )
            )
        if due_before is not None:
            statement = statement.where(BoardItem.due_at.is_not(None))
            statement = statement.where(
                BoardItem.due_at < self._normalize_due(due_before, "due_before")
            )
        # The +1 probe (review F1): fetch ONE row beyond the page in
        # the SAME query. ``has_more`` is therefore exact for the
        # FILTERED set and costs zero extra queries — no ``COUNT(*)``
        # — so the page budget stays at three queries (items + batched
        # tags + batched notes).
        statement = statement.order_by(BoardItem.updated_at.desc()).limit(
            bounded_limit + 1
        )

        with self._write_lock:
            with Session(self._engine) as session:
                rows = list(session.exec(statement).all())
                has_more = len(rows) > bounded_limit
                rows = rows[:bounded_limit]
                item_ids = [row.id for row in rows]
                tags_by_item = self._load_tags_bulk(session, item_ids)
                notes_by_item = (
                    self._load_notes_bulk(session, item_ids) if with_notes else {}
                )
                items = [
                    self._row_to_item(
                        row,
                        tags_by_item.get(row.id, []),
                        notes=notes_by_item.get(row.id),
                    )
                    for row in rows
                ]
        return {
            "items": items,
            "count": len(items),
            "has_more": has_more,
            "applied_limit": bounded_limit,
        }

    def get_item(self, *, item_id: str) -> dict:
        """Fetch one board item with its append-only note timeline.

        Args:
            item_id: The item id.

        Returns:
            The item in the canonical item shape PLUS ``notes``
            (oldest-first).

        Raises:
            BoardItemNotFound: no row carries that id.
        """
        with self._write_lock:
            with Session(self._engine) as session:
                row = session.exec(
                    select(BoardItem).where(BoardItem.id == item_id)
                ).first()
                if row is None:
                    raise BoardItemNotFound(f"board item not found: {item_id}")
                tags = self._load_tags_bulk(session, [item_id]).get(item_id, [])
                notes = self._load_notes_bulk(session, [item_id]).get(item_id, [])
                item = self._row_to_item(row, tags, notes=notes)
        return item

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _new_item_id() -> str:
        """Mint a fresh item id — ``wi-`` + the first 8 hex of a uuid4.

        The id is generated by the SERVICE, never supplied by the
        agent, so "the agent retries with a fresh id" is not a
        reachable behaviour.
        """
        return f"wi-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _validate_title(title: str) -> str:
        """Validate and normalise the title."""
        if not isinstance(title, str) or not title.strip():
            raise BoardValidationError(
                "title must be a non-empty string of at most "
                f"{MAX_TITLE_LENGTH} characters"
            )
        clean = title.strip()
        if len(clean) > MAX_TITLE_LENGTH:
            raise BoardValidationError(
                "title must be a non-empty string of at most "
                f"{MAX_TITLE_LENGTH} characters"
            )
        return clean

    @staticmethod
    def _validate_priority(priority: int | None) -> int | None:
        """Validate the priority (1-5, or None for unset)."""
        if priority is None:
            return None
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise BoardValidationError(
                f"priority must be an integer between {MIN_PRIORITY} and "
                f"{MAX_PRIORITY}, or omitted"
            )
        if priority < MIN_PRIORITY or priority > MAX_PRIORITY:
            raise BoardValidationError(
                f"priority must be an integer between {MIN_PRIORITY} and "
                f"{MAX_PRIORITY}, or omitted"
            )
        return priority

    @staticmethod
    def _normalize_due(value: str | None, field: str) -> str | None:
        """Normalise a deadline to the fixed-width UTC form.

        ONE helper for the whole deadline path — the ``due_at`` write
        path AND the ``due_before`` filter path (R11). A naive input is
        rejected; a non-UTC offset is converted, never compared as a
        raw string.

        An EMPTY-STRING ``due_at`` is rejected fail-closed
        INTENTIONALLY (plan Q2): ``due_at`` is replaceable-not-
        clearable in v1 — ``None`` means "no change" and no clearing
        input exists, so ``""`` would be ambiguous; it is a validation
        error, never a silent clear.
        """
        if value is None:
            return None
        return _to_utc_iso(value, field=field)

    @staticmethod
    def _normalize_tags(tags: Sequence[str] | None) -> list[str]:
        """De-duplicate the incoming tag list — order-preserving, first wins.

        The junction table's composite PK dedupes the stored ROWS; it
        does NOT dedupe the incoming LIST. ``tags=["a", "a"]`` would
        otherwise attempt the same ``(item_id, tag)`` twice and raise
        an IntegrityError on the second insert — a create that looks
        like an id collision but is not one. The dedupe happens on the
        INPUT, not on the read path.
        """
        if not tags:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for raw in tags:
            if not isinstance(raw, str):
                continue
            clean = raw.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
        return out

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        """Clamp the page size into the 1..200 window."""
        try:
            value = int(limit)
        except (TypeError, ValueError):
            return DEFAULT_LIMIT
        if value < MIN_LIMIT:
            return MIN_LIMIT
        if value > MAX_LIMIT:
            return MAX_LIMIT
        return value

    @staticmethod
    def _is_id_pk_collision(exc: IntegrityError) -> bool:
        """Was this IntegrityError an id PRIMARY KEY collision?

        The check is deliberately narrow: it must recognise the id PK
        violation (SQLite: "UNIQUE constraint failed: board_items.id";
        PostgreSQL: "duplicate key value violates unique constraint
        \\"board_items_pkey\\"") and must NOT swallow a FOREIGN KEY or
        a junction-PK violation.
        """
        orig = getattr(exc, "orig", None)
        text = str(orig if orig is not None else exc).lower()
        if "board_items" not in text:
            return False
        if "foreign key" in text:
            return False
        return (
            "pkey" in text
            or "board_items.id" in text
            or "unique constraint" in text
        )

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> Exception:
        """Turn a non-collision IntegrityError into an authored error.

        The overwhelmingly likely cause is a ``project_id`` that does
        not reference an existing project. That is a validation
        problem, not a server fault, so it gets a safe authored
        message instead of the generic failure envelope.
        """
        orig = getattr(exc, "orig", None)
        text = str(orig if orig is not None else exc).lower()
        if "foreign key" in text:
            return BoardValidationError(
                "project_id does not reference an existing project"
            )
        return BoardError("board operation failed")

    def _append_note(
        self,
        session: Session,
        *,
        item_id: str,
        author_instance: str,
        text: str,
    ) -> dict:
        """Append one note to an item's timeline — the ONLY notes mutation.

        There is no ``update_note`` / ``delete_note`` / ``edit_note``
        anywhere in the codebase: the update path is structurally
        incapable of editing or deleting history because no such code
        path exists. The note row is stamped inside the caller's
        transaction (the audit-event contract).

        The ORM instance is DETACHED (``expunge``) right after the
        flush so a live ``BoardNote`` can never survive a commit
        boundary — otherwise SQLAlchemy's dirty-tracking could emit an
        UPDATE against a row that must never change.
        """
        note = BoardNote(
            item_id=item_id,
            ts=_now_iso(),
            author_instance=author_instance,
            text=text,
        )
        session.add(note)
        session.flush()
        payload = {
            "ts": note.ts,
            "author_instance": note.author_instance,
            "text": note.text,
        }
        session.expunge(note)
        return payload

    def _load_notes_bulk(
        self, session: Session, item_ids: list[str]
    ) -> dict[str, list[dict]]:
        """Load every note for a page of items in ONE query.

        The notes are read into PLAIN DICTS — no live ``BoardNote``
        instance escapes this method.
        """
        if not item_ids:
            return {}
        rows = session.exec(
            select(BoardNote)
            .where(BoardNote.item_id.in_(item_ids))
            .order_by(BoardNote.ts.asc(), BoardNote.id.asc())
        ).all()
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row.item_id, []).append(
                {
                    "ts": row.ts,
                    "author_instance": row.author_instance,
                    "text": row.text,
                }
            )
        return grouped

    def _load_tags_bulk(
        self, session: Session, item_ids: list[str]
    ) -> dict[str, list[str]]:
        """Load every tag for a page of items in ONE query.

        READ-ONLY — the junction rows are written only by
        ``create_item``. This is a hydration path, never a write path.
        """
        if not item_ids:
            return {}
        rows = session.exec(
            select(BoardItemTag)
            .where(BoardItemTag.item_id.in_(item_ids))
            .order_by(BoardItemTag.item_id.asc(), BoardItemTag.tag.asc())
        ).all()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row.item_id, []).append(row.tag)
        return grouped

    @staticmethod
    def _row_to_item(
        row: BoardItem,
        tags: Sequence[str],
        notes: Optional[Sequence[dict]] = None,
    ) -> dict:
        """Project one ORM row into the canonical item shape.

        The shape is projected ONCE, here — every tool returns exactly
        this.
        """
        item: dict[str, Any] = {
            "id": row.id,
            "title": row.title,
            "body_md": row.body_md,
            "status": row.status,
            "tags": list(tags),
            "project_id": row.project_id,
            "due_at": row.due_at,
            "priority": row.priority,
            "linked_job_ids": list(row.linked_job_ids or []),
            "created_by_instance": row.created_by_instance,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        if notes is not None:
            item["notes"] = list(notes)
        return item


__all__ = [
    "BoardError",
    "BoardItemNotFound",
    "BoardService",
    "BoardValidationError",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_PRIORITY",
    "MAX_TITLE_LENGTH",
    "MIN_LIMIT",
    "MIN_PRIORITY",
]
