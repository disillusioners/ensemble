"""The persona lane — the versioned durable self and its human gate.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` T1.5, D1.11,
D1.12; amendment AM-17).

Layer 3 of the memoir store (design §4.3) is ONE small evolving markdown
document — "who we are". This module is its state machine:

* the transition vocabulary is MIRRORED from the prompt-version lane
  (:data:`daemon.repositories.memoir.models.PERSONA_VALID_TRANSITIONS`),
  so DD-9's gate is enforced by the DATA and not by a convention:
  ``draft -> pending_approval -> approved | rejected | draft`` and
  ``draft -> archived``. ``(draft, approved)`` is NOT a member — the
  human lane is not optional, and a v2 submitter cannot reach
  ``approved`` without passing through ``pending_approval``.
* the approve/reject seam ships the HUMAN-LANE GUARD (D1.12 / AM-17):
  it raises the house ``HumanLaneRequiredError`` unless
  ``actor.auth_method == "session"``. The house discriminator is
  ``daemon/services/prompt_version_service.py`` ``_require_human_lane``
  — the lane, not the scope claim, is the source of authority: even a
  bearer token holding the right scope is refused, because a machine
  cannot satisfy the human-only approval rule.
* ``submit()`` has NO CALLER in v1 (the v1 persona is read-only; the
  five-tool surface of design §5.4 has no persona-edit tool). The seam
  exists so that the v2 lane inherits the guard explicitly instead of
  re-deriving it.

The EPISODE surface (the insert / recall / pin / unpin writers and the
mutability partition) arrives in phase 2 — this module carries the
PERSONA lane only.

A NOTE ON THE ERROR CLASSES: the transition error is a LOCAL class
(:class:`MemoirTransitionError`) because the house
``InvalidTransitionError`` lives in the prompt-version repository and a
cross-lane import would couple the two artifact families. The
human-lane error IS the house class — it is imported lazily inside the
guard so the repository layer keeps no module-load dependency on the
service layer, and so the HTTP layer's existing ``403 HUMAN_LANE_REQUIRED``
mapping keeps working unchanged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence

from sqlalchemy import Boolean, DateTime, Integer, Text, bindparam, text
from sqlmodel import Session, select

from daemon.repositories.infra.types import JSONBType
from daemon.repositories.memoir.models import (
    KEEPSAKE_ADMISSION_PCLASS,
    PERSONA_SOURCES,
    PERSONA_STATUSES,
    PERSONA_VALID_TRANSITIONS,
    PIN_ACTORS,
    MemoirEpisode,
    MemoirPersona,
    MemoirPersonaVersion,
    StarsListType,
    render_keepsake_line,
)
from daemon.repositories.memoir.seed import (
    MEMOIR_SEED_AGENT_ID,
    seed_persona_if_absent as _seed_persona_if_absent,
)
from daemon.services.timestamps import now_utc, now_utc_iso, to_utc_iso

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Size rationale (~1,451 lines, refreshed 2026-11-05): this is the
# memoir DATA layer — the persona state machine, the episode writers,
# the keepsake admission arithmetic, the lexical rankers (PG
# ``ts_rank_cd`` + the SQLite BM25 dev fallback), the recall-stats
# writers and the windowed timeline read. It is one module because
# every one of those operations shares the SAME transaction discipline
# and the SAME single-sourced predicates (terminal-row predicate,
# keepsake-line renderer, admission token arithmetic); splitting by verb
# would duplicate the predicates and re-open the W-1 drift (admission
# measuring different bytes than the briefing ships). Mirrors the
# ``manager.py`` precedent: a deferred-decomposition ledger, not an
# aspiration — extract a cohesive seam (e.g. the lexical rankers) behind
# this module's public methods when one outgrows its section; never split
# a shared predicate from its consumer.


class MemoirTransitionError(ValueError):
    """Raised when a persona version transition is not permitted.

    The mirror of the prompt-version lane's ``InvalidTransitionError``:
    a ``ValueError`` subclass, so a caller can catch ``ValueError`` and
    still get the specific type.
    """

    def __init__(self, version_id: str, from_status: str, to_status: str) -> None:
        self.version_id = version_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"persona version {version_id}: transition {from_status!r} -> "
            f"{to_status!r} is not permitted"
        )


class MemoirPersonaRepository:
    """Sync repository over the persona layer.

    Sync class operating through ``Session`` blocks (the
    ``daemon/repositories/bash_spawn/repository.py`` precedent).
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── reads ─────────────────────────────────────────────────────────────

    def list_versions(self, agent_id: str) -> list[MemoirPersonaVersion]:
        """Every persona version for the agent, oldest version first."""
        with Session(self.engine) as session:
            stmt = (
                select(MemoirPersonaVersion)
                .where(MemoirPersonaVersion.agent_id == agent_id)
                .order_by(MemoirPersonaVersion.version)
            )
            return list(session.exec(stmt).all())

    def get_version(self, version_id: str) -> MemoirPersonaVersion | None:
        """One persona version by id, or ``None``."""
        with Session(self.engine) as session:
            return session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.id == version_id
                )
            ).first()

    def get_current(self, agent_id: str) -> MemoirPersonaVersion | None:
        """The version currently in force for the agent, or ``None``.

        The pointer row (``memoir_persona.current_version_id``) decides;
        a pointer that names a missing row resolves to ``None`` (the
        briefing then renders its "persona unavailable" prefix — AM-39).
        """
        with Session(self.engine) as session:
            pointer = session.exec(
                select(MemoirPersona).where(MemoirPersona.agent_id == agent_id)
            ).first()
            if pointer is None or not pointer.current_version_id:
                return None
            return session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.id == pointer.current_version_id
                )
            ).first()

    # ── writes ────────────────────────────────────────────────────────────

    def create_draft(
        self, agent_id: str, body: str, source: str = "ari"
    ) -> MemoirPersonaVersion:
        """Create the NEXT draft version for the agent.

        The version number is ``max(version) + 1`` — the rejected row
        stays in the lineage and a re-submission is a NEW row (the house
        prompt-version rule), so the forensic identity of every version
        is preserved.
        """
        if source not in PERSONA_SOURCES:
            raise ValueError(
                f"persona source must be one of {sorted(PERSONA_SOURCES)}; "
                f"got {source!r}"
            )
        with Session(self.engine) as session:
            versions = session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.agent_id == agent_id
                )
            ).all()
            next_version = max((v.version for v in versions), default=0) + 1
            draft = MemoirPersonaVersion(
                id=uuid.uuid4().hex,
                agent_id=agent_id,
                version=next_version,
                body=body,
                status="draft",
                source=source,
            )
            session.add(draft)
            session.commit()
            session.refresh(draft)
            return draft

    def submit(self, version_id: str, actor: Any = None) -> MemoirPersonaVersion:
        """Move a draft to ``pending_approval``.

        NO CALLER IN v1 (the v1 persona is read-only). The seam exists so
        the v2 lane inherits the guard and the transition vocabulary
        explicitly. ``actor`` is accepted for signature parity with
        :meth:`approve`; submitting is NOT a human-lane action (a
        machine may propose) — only the APPROVAL is.
        """
        return self._transition(version_id, "pending_approval", actor=actor)

    def approve(self, version_id: str, actor: Any) -> MemoirPersonaVersion:
        """Approve a pending persona version — HUMAN LANE ONLY.

        Raises:
            HumanLaneRequiredError: when ``actor.auth_method`` is not
                ``"session"``. The house discriminator is reused
                verbatim (AM-17), so the seam is safe by construction
                rather than safe by a grep pin.
            MemoirTransitionError: when the current status does not
                permit ``pending_approval -> approved``.
        """
        self._require_human_lane(version_id, actor)
        return self._transition(version_id, "approved", actor=actor)

    def reject(self, version_id: str, actor: Any) -> MemoirPersonaVersion:
        """Reject a pending persona version — HUMAN LANE ONLY."""
        self._require_human_lane(version_id, actor)
        return self._transition(version_id, "rejected", actor=actor)

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _require_human_lane(version_id: str, actor: Any) -> None:
        """Raise the house ``HumanLaneRequiredError`` unless human.

        The check is a single equality test on the lane discriminator
        (``actor.auth_method``) — NOT on the scope claim: a bearer
        token holding the right scope is still refused, because a
        machine cannot satisfy the human-only approval rule.
        """
        # Imported lazily: the repository layer keeps no module-load
        # dependency on the service layer, and the exception object is
        # the SAME house class the HTTP layer maps to
        # ``403 HUMAN_LANE_REQUIRED``.
        from daemon.services.prompt_version_service import HumanLaneRequiredError

        auth_method = getattr(actor, "auth_method", None)
        if auth_method != "session":
            raise HumanLaneRequiredError(
                version_id=version_id,
                actor_auth_method=auth_method or "<missing>",
            )

    def _transition(
        self, version_id: str, to_status: str, actor: Any = None
    ) -> MemoirPersonaVersion:
        """Apply one validated transition, updating the pointer in the
        SAME transaction.

        The transition is validated against the MIRRORED vocabulary
        (:data:`PERSONA_VALID_TRANSITIONS`) — the read and the write
        share one session, so a concurrent transition cannot slip
        between them.
        """
        if to_status not in PERSONA_STATUSES:
            raise ValueError(
                f"unknown persona status {to_status!r}; expected one of "
                f"{sorted(PERSONA_STATUSES)}"
            )
        with Session(self.engine) as session:
            version = session.exec(
                select(MemoirPersonaVersion).where(
                    MemoirPersonaVersion.id == version_id
                )
            ).first()
            if version is None:
                raise ValueError(f"persona version {version_id} not found")
            from_status = version.status
            if (from_status, to_status) not in PERSONA_VALID_TRANSITIONS:
                raise MemoirTransitionError(version_id, from_status, to_status)

            version.status = to_status
            if to_status == "pending_approval":
                version.submitted_at = now_utc_iso()
            elif to_status in ("approved", "rejected"):
                version.approved_at = now_utc_iso()

            if to_status == "approved":
                pointer = session.exec(
                    select(MemoirPersona).where(
                        MemoirPersona.agent_id == version.agent_id
                    )
                ).first()
                now = now_utc()
                if pointer is None:
                    session.add(
                        MemoirPersona(
                            agent_id=version.agent_id,
                            current_version_id=version.id,
                            updated_at=now,
                        )
                    )
                else:
                    pointer.current_version_id = version.id
                    pointer.updated_at = now

            session.commit()
            session.refresh(version)
            return version


# ── the episode surface (phase 2) ──────────────────────────────────────────
#
# D2.1: the public surface is a CLOSED set — one named writer per mutable
# family and NO general-purpose update anywhere. The mutability partition
# (``decisions.md`` §4 row 5) is enforced STRUCTURALLY:
#
#   * the RECORD columns (``id``, ``agent_id``, ``title``, ``body``,
#     ``created_at``, ``event_at``, ``importance``, ``stars``) are written
#     ONCE — by the single INSERT;
#   * each OPERATIONAL family has exactly ONE named writer: ``mark_recalled``
#     (the access stats), ``set_pin`` / ``clear_pin`` (the pin SET),
#     ``store_embedding`` / ``record_embedding_attempt`` (the semantic
#     half), ``mark_superseded`` (the replacement pointer — defined,
#     uncalled in v1);
#   * ``tsv`` is DB-derived and appears in NO statement here except the PG
#     lexical ranker itself (D2.17).
#
# EVERY UPDATE below is COLUMN-TARGETED: it names the columns it writes and
# touches no record column. The purity pack pins that property by parsing
# this module's SQL constants (the mutation leg: an UPDATE that touches
# ``body`` ⇒ RED).

#: The candidate pool PER RANKER (AM-2). 100 rows per ranker; the two pools
#: are INDEPENDENT and their UNION is the candidate set. A rank-1 hit
#: contributes ``1/61`` and a rank-100 hit ``1/160``, so 100 candidates
#: capture >=96 % of the achievable RRF mass while keeping the PG scan
#: bounded. This is a MODULE CONSTANT, not a config knob.
MEMOIR_RANK_CANDIDATES = 100

#: The keepsake COUNT cap (D2.12 / adjudication row 1). 12 pins.
MEMOIR_PIN_COUNT_CAP = 12

#: The keepsake TOKEN zone (D2.12 / adjudication row 1) — the stable zone
#: is pre-bounded by ADMISSION and is never truncated, so the cap on the
#: count alone is not enough (12 x a legal ~2,800-token body would blow it).
MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET = 4000

#: The write bounds (D2.13) — HYGIENE, NOT normalization. They change no
#: string's content (adjudication row 4): the stars are stored VERBATIM.
MEMOIR_BODY_CHAR_CAP = 16000
MEMOIR_BODY_WORD_CAP = 2000
MEMOIR_TITLE_CHAR_CAP = 200
MEMOIR_STARS_MAX_ENTRIES = 16
MEMOIR_STAR_CHAR_CAP = 120
MEMOIR_IMPORTANCE_MIN = 1
MEMOIR_IMPORTANCE_MAX = 10

#: The ONE INSERT's EXACT column set (D2.15 / AM-13). ``tsv`` is DB-generated
#: and is NOT here — it is in no statement but the PG lexical ranker.
MEMOIR_INSERT_COLUMNS: tuple[str, ...] = (
    "id",
    "agent_id",
    "title",
    "body",
    "created_at",
    "event_at",
    "importance",
    "stars",
    "is_keepsake",
    "pinned_by",
    "pinned_reason",
    "pinned_at",
    "embedding",
    "embedding_model",
    "embedding_dims",
    "embedded_at",
    "embedding_attempts",
    "embedding_error",
    "last_accessed_at",
    "access_count",
    "superseded_by",
)

#: The ONE INSERT (D2.15). The DEFAULTS are literal in the VALUES clause so
#: the shipped statement is byte-pinned: the semantic half starts NULL, the
#: attempt counter at 0, the stats at NULL/0.
MEMOIR_INSERT_SQL = (
    "INSERT INTO memoir_episodes (\n"
    "    id, agent_id, title, body, created_at, event_at, importance, stars,\n"
    "    is_keepsake, pinned_by, pinned_reason, pinned_at,\n"
    "    embedding, embedding_model, embedding_dims, embedded_at,\n"
    "    embedding_attempts, embedding_error,\n"
    "    last_accessed_at, access_count, superseded_by\n"
    ") VALUES (\n"
    "    :id, :agent_id, :title, :body, :created_at, :event_at, :importance,"
    " :stars,\n"
    "    :is_keepsake, :pinned_by, :pinned_reason, :pinned_at,\n"
    "    NULL, NULL, NULL, NULL,\n"
    "    0, NULL,\n"
    "    NULL, 0, NULL\n"
    ")"
)

_MEMOIR_INSERT_STATEMENT = text(MEMOIR_INSERT_SQL).bindparams(
    bindparam("id", type_=Text()),
    bindparam("agent_id", type_=Text()),
    bindparam("title", type_=Text()),
    bindparam("body", type_=Text()),
    bindparam("created_at", type_=DateTime(timezone=True)),
    bindparam("event_at", type_=DateTime(timezone=True)),
    bindparam("importance", type_=Integer()),
    bindparam("stars", type_=StarsListType()),
    bindparam("is_keepsake", type_=Boolean()),
    bindparam("pinned_by", type_=Text()),
    bindparam("pinned_reason", type_=Text()),
    bindparam("pinned_at", type_=DateTime(timezone=True)),
)

#: The access stats (D2.8) — ONE column-targeted UPDATE, called ONLY by the
#: recall service, marking EXACTLY the ids in the shipped projection.
_MEMOIR_MARK_RECALLED_SQL = (
    "UPDATE memoir_episodes "
    "SET last_accessed_at = :now, access_count = access_count + 1 "
    "WHERE id IN :ids"
)
_MEMOIR_MARK_RECALLED_STATEMENT = text(_MEMOIR_MARK_RECALLED_SQL).bindparams(
    bindparam("now", type_=DateTime(timezone=True)),
    bindparam("ids", expanding=True, type_=Text()),
)

#: The pin SET (D2.12 / N-1): the pin columns are written TOGETHER.
_MEMOIR_SET_PIN_SQL = (
    "UPDATE memoir_episodes "
    "SET is_keepsake = :is_keepsake, pinned_by = :pinned_by, "
    "pinned_reason = :pinned_reason, pinned_at = :pinned_at "
    "WHERE id = :id"
)
_MEMOIR_SET_PIN_STATEMENT = text(_MEMOIR_SET_PIN_SQL).bindparams(
    bindparam("is_keepsake", type_=Boolean()),
    bindparam("pinned_by", type_=Text()),
    bindparam("pinned_reason", type_=Text()),
    bindparam("pinned_at", type_=DateTime(timezone=True)),
    bindparam("id", type_=Text()),
)

#: N-1 (D2.1): ``clear_pin`` NULLs the WHOLE pin column set — no stale
#: attribution survives an unpin, and ``pinned_by``'s CHECK is satisfied by
#: NULL.
_MEMOIR_CLEAR_PIN_SQL = (
    "UPDATE memoir_episodes "
    "SET is_keepsake = :is_keepsake, pinned_by = NULL, pinned_reason = NULL, "
    "pinned_at = NULL "
    "WHERE id = :id"
)
_MEMOIR_CLEAR_PIN_STATEMENT = text(_MEMOIR_CLEAR_PIN_SQL).bindparams(
    bindparam("is_keepsake", type_=Boolean()),
    bindparam("id", type_=Text()),
)

#: The embedding write-back (D2.16 / AM-15) — ONE atomic column-targeted
#: UPDATE setting the four embedding columns TOGETHER and clearing the
#: error. A partial write would leave ``embedding IS NULL AND embedded_at IS
#: NULL`` true ⇒ the row is re-embedded FOREVER.
_MEMOIR_STORE_EMBEDDING_SQL = (
    "UPDATE memoir_episodes "
    "SET embedding = :embedding, embedding_model = :embedding_model, "
    "embedding_dims = :embedding_dims, embedded_at = :embedded_at, "
    "embedding_error = NULL "
    "WHERE id = :id"
)
_MEMOIR_STORE_EMBEDDING_STATEMENT = text(_MEMOIR_STORE_EMBEDDING_SQL).bindparams(
    bindparam("embedding", type_=JSONBType()),
    bindparam("embedding_model", type_=Text()),
    bindparam("embedding_dims", type_=Integer()),
    bindparam("embedded_at", type_=Text()),
    bindparam("id", type_=Text()),
)

#: A failed embed (or a failed write-back) COUNTS AS AN ATTEMPT (AM-30).
_MEMOIR_RECORD_ATTEMPT_SQL = (
    "UPDATE memoir_episodes "
    "SET embedding_attempts = embedding_attempts + 1, embedding_error = :error "
    "WHERE id = :id"
)
_MEMOIR_RECORD_ATTEMPT_STATEMENT = text(_MEMOIR_RECORD_ATTEMPT_SQL).bindparams(
    bindparam("error", type_=Text()),
    bindparam("id", type_=Text()),
)

#: The replacement pointer (DD-8) — defined, uncalled in v1.
_MEMOIR_MARK_SUPERSEDED_SQL = (
    "UPDATE memoir_episodes SET superseded_by = :superseded_by WHERE id = :id"
)
_MEMOIR_MARK_SUPERSEDED_STATEMENT = text(_MEMOIR_MARK_SUPERSEDED_SQL).bindparams(
    bindparam("superseded_by", type_=Text()),
    bindparam("id", type_=Text()),
)

#: The PG lexical ranker (D2.2 / AM-1). ``tsvector`` is the FIRST argument:
#: PostgreSQL has NO ``ts_rank_cd(tsquery, tsvector)`` overload, and the
#: reversed form raises ``UndefinedFunction`` — a defect the SQLite
#: fallback would keep GREEN (so it would surface only in production).
#: The ``agent_id`` equality predicate is the DD-13 identity discipline:
#: the recall service is agent-scoped and the agent id is captured at the
#: factory, never supplied by a caller.
_MEMOIR_PG_LEXICAL_SQL = (
    "SELECT id, ts_rank_cd(tsv, plainto_tsquery('english', :q)) AS score "
    "FROM memoir_episodes "
    "WHERE agent_id = :agent_id "
    "AND tsv @@ plainto_tsquery('english', :q)"
)

#: The far bounds used when only one side of the timeline window is given.
_MEMOIR_WINDOW_FLOOR = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MEMOIR_WINDOW_CEILING = datetime(9999, 12, 31, tzinfo=timezone.utc)


class MemoirWriteError(ValueError):
    """A typed refusal from the memoir write path.

    The repository refuses with a CODE so the tool layer can map it onto
    the frozen ``memoir_*`` refusal vocabulary (AM-21) without re-deriving
    the reason. A ``ValueError`` subclass, so a caller that catches
    ``ValueError`` still gets the specific type.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{code}: {message}")


class MemoirAdmissionError(MemoirWriteError):
    """A typed refusal from the pin admission (D2.12).

    The error NAMES THE BINDING CONSTRAINT (the count cap or the token
    zone) and carries the CURRENT PINS (id / title / pinned_by / tokens),
    so the caller can tell the agent exactly what is in the way. It NEVER
    evicts: a keepsake that can be silently evicted is not a keepsake
    (DD-3).
    """

    def __init__(
        self,
        constraint: str,
        message: str,
        pins: Sequence[dict[str, Any]] = (),
    ) -> None:
        super().__init__(constraint, message, constraint=constraint, pins=list(pins))
        self.constraint = constraint
        self.pins = list(pins)


def as_aware_utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime to an aware UTC datetime.

    A NAIVE value is assumed to carry NAIVE-UTC DIGITS (the house
    convention — ``daemon/services/timestamps.py``): the naive-column
    boundary is the reason the house has a dedicated producer for them.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def render_episode_content(title: str | None, body: str | None) -> str:
    """The CONTENT of an episode — what the keepsake zone carries.

    The zone budget is measured on the episode's own words (title plus
    body), not on a rendered tool envelope: the zone holds the memory, and
    the envelope around it is the tool layer's business.
    """
    clean_title = (title or "").strip()
    clean_body = body or ""
    if clean_title:
        return f"{clean_title}\n{clean_body}"
    return clean_body


def _estimate_tokens(text: str) -> int:
    """The house token estimator (``daemon/loader.py``).

    Imported lazily: the loader is a heavy module and the repository must
    stay importable without it. The estimator is the house one (D2.10) —
    a bespoke counter would make the budget arithmetic disagree with the
    briefing's.
    """
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)


# ── THE EMBEDDING TERMINAL PREDICATE (W-2, single-sourced) ───────────────
#: The error-string marker that makes an embedding row terminal on the
#: next scan (D3.7/AM-30 — the worker's own vocabulary). It LIVES HERE
#: so the SQL scan window and the worker's Python belt predicate cannot
#: drift: the worker imports BOTH this marker and the predicate below.
MEMOIR_EMBEDDING_TERMINAL_MARKER = "terminal:"

#: The per-row embedding attempt budget (D3.7 — attempts at/over the cap
#: are terminal). Single-sourced here; the worker re-exports it.
MEMOIR_EMBEDDING_ATTEMPT_CAP = 5


def memoir_embedding_terminal_predicate(attempt_cap: int = MEMOIR_EMBEDDING_ATTEMPT_CAP):
    """THE terminal predicate, SQL form (W-2).

    Terminal = still missing its embedding AND (attempts at/over the cap
    OR the ``terminal:`` marker present). ``get_pending_embeddings``
    EXCLUDES this predicate (so the oldest-N window advances past
    terminal rows — W-2: terminal rows matched the pending query
    forever, their embedding never arrives, and the worker's Python
    filter emptied the window every tick); ``_count_terminal`` in the
    worker SELECTS it (D3.11's operator slice).

    BOTH legs ``coalesce`` their nullable input so the predicate is
    TOTAL — it never evaluates to SQL NULL. This matters for the
    EXCLUSION: ``NOT (NULL OR FALSE)`` is NULL (not TRUE) in SQL's
    three-valued logic, so a naive negation would silently drop every
    fresh row whose ``embedding_error`` is still NULL — the exact
    opposite of the fix. The Python form (:func:`memoir_row_is_terminal`)
    mirrors the same coalescing (``or 0`` / ``or ""``).
    """
    from sqlalchemy import func, or_

    return or_(
        func.coalesce(MemoirEpisode.embedding_attempts, 0) >= int(attempt_cap),
        func.coalesce(MemoirEpisode.embedding_error, "").like(
            f"{MEMOIR_EMBEDDING_TERMINAL_MARKER}%"
        ),
    )


def memoir_row_is_terminal(row: Any, attempt_cap: int = MEMOIR_EMBEDDING_ATTEMPT_CAP) -> bool:
    """THE terminal predicate, Python form (W-2) — the same rule the SQL
    predicate encodes, stated ONCE so the two cannot disagree. The
    worker keeps a thin alias (``_is_terminal_row``) as the belt."""
    attempts = int(getattr(row, "embedding_attempts", 0) or 0)
    if attempts >= int(attempt_cap):
        return True
    error = getattr(row, "embedding_error", None) or ""
    return error.startswith(MEMOIR_EMBEDDING_TERMINAL_MARKER)


class MemoirRepository:
    """The episode surface — the CLOSED public surface (D2.1).

    Sync repository over the engine, in the
    ``daemon/repositories/bash_spawn/repository.py`` shape (the same
    ``Session``-block pattern the persona lane uses).

    THE ENGINE IS THE CALLER'S. This module never builds an engine and
    never reads an ambient ``POSTGRES_*`` variable: the
    ``daemon/repositories/factory.py`` engine factory lets the ambient
    environment override the passed config, which is how a probe can boot
    against PRODUCTION by accident. The caller passes the engine; this
    class uses it and nothing else.
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── writes ───────────────────────────────────────────────────────────

    def insert_episode(
        self,
        *,
        agent_id: str,
        body: str,
        title: str | None = None,
        importance: int = 5,
        stars: Sequence[str] | None = None,
        event_at: datetime | str | None = None,
        created_at: datetime | None = None,
        is_keepsake: bool = False,
        pinned_by: str | None = None,
        pinned_reason: str | None = None,
        episode_id: str | None = None,
    ) -> str:
        """Write ONE episode — the single INSERT of the write path.

        The pin columns are part of the SAME row (D2.15): a keepsake needs
        no second statement. When ``is_keepsake`` is true the pin-admission
        READS (``count_pins`` / ``keepsake_zone_tokens``) run FIRST, in
        the same transaction — W3: an inversion would write the 13th
        keepsake and THEN refuse it, blowing the 4,000-token zone and
        telling the agent the write failed when it exists.

        Returns:
            The new episode id.

        Raises:
            MemoirWriteError: on a write-bound violation (D2.13).
            MemoirAdmissionError: when the pin admission refuses (D2.12).
        """
        clean_body = self._validate_body(body)
        clean_title = self._validate_title(title)
        clean_stars = self._validate_stars(stars)
        clean_importance = self._validate_importance(importance)
        event = self._parse_event_at(event_at)
        if is_keepsake:
            self._validate_pin_claim(pinned_by, pinned_reason)

        written_at = as_aware_utc(created_at) or now_utc()
        occurred_at = event or written_at
        new_id = episode_id or uuid.uuid4().hex

        with Session(self.engine) as session:
            if is_keepsake:
                # THE ADMISSION READS — PINNED-PRECEDE the single INSERT.
                # W-1: the candidate is measured as the RENDERED keepsake
                # line (the wrapper + pinned_by + the provenance class +
                # the uncapped reason) — the SAME bytes the briefing
                # ships — never the content alone.
                candidate_tokens = _estimate_tokens(
                    render_keepsake_line(
                        title=clean_title,
                        pinned_by=pinned_by,
                        pclass=KEEPSAKE_ADMISSION_PCLASS,
                        pinned_reason=pinned_reason,
                    )
                )
                self._admit_pin(
                    session,
                    agent_id,
                    candidate_id=new_id,
                    candidate_tokens=candidate_tokens,
                )
            session.execute(
                _MEMOIR_INSERT_STATEMENT,
                {
                    "id": new_id,
                    "agent_id": agent_id,
                    "title": clean_title,
                    "body": clean_body,
                    "created_at": written_at,
                    "event_at": occurred_at,
                    "importance": clean_importance,
                    "stars": list(clean_stars) if clean_stars is not None else None,
                    "is_keepsake": bool(is_keepsake),
                    "pinned_by": pinned_by if is_keepsake else None,
                    "pinned_reason": pinned_reason if is_keepsake else None,
                    "pinned_at": written_at if is_keepsake else None,
                },
            )
            session.commit()
        return new_id

    def mark_recalled(self, ids: Sequence[str], now: datetime | None = None) -> int:
        """Record the access stats for the SHIPPED ids (D2.8).

        ONE column-targeted UPDATE. Called ONLY by the recall service, and
        only with the ids that were actually shipped: an episode dropped
        for budget is NOT returned and must NOT be marked (a briefing is
        not an act of attention — DD-12).
        """
        id_list = [str(item) for item in ids]
        if not id_list:
            return 0
        stamp = as_aware_utc(now) or now_utc()
        with Session(self.engine) as session:
            result = session.execute(
                _MEMOIR_MARK_RECALLED_STATEMENT,
                {"ids": id_list, "now": stamp},
            )
            session.commit()
            return int(result.rowcount or 0)

    def set_pin(
        self,
        episode_id: str,
        *,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        """Pin an episode — the keepsake SET, written together (D2.12).

        The admission runs BEFORE the write: the count cap and the token
        zone must both hold, and the refusal NAMES the binding constraint
        and the current pins. Nothing is ever evicted (DD-3).
        """
        self._validate_pin_claim(actor, reason)
        stamp = as_aware_utc(now) or now_utc()
        with Session(self.engine) as session:
            episode = session.exec(
                select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
            ).first()
            if episode is None:
                raise MemoirWriteError(
                    "episode_not_found", f"no episode with id {episode_id!r}"
                )
            # W-1: the candidate is measured as the RENDERED keepsake
            # line the briefing will ship for this row — never the
            # content alone (the wrapper + pinned_by + the provenance
            # class + the uncapped reason are part of the zone).
            candidate_tokens = _estimate_tokens(
                render_keepsake_line(
                    title=episode.title,
                    pinned_by=actor,
                    pclass=KEEPSAKE_ADMISSION_PCLASS,
                    pinned_reason=reason,
                )
            )
            self._admit_pin(
                session,
                episode.agent_id,
                candidate_id=episode_id,
                candidate_tokens=candidate_tokens,
            )
            session.execute(
                _MEMOIR_SET_PIN_STATEMENT,
                {
                    "is_keepsake": True,
                    "pinned_by": actor,
                    "pinned_reason": reason,
                    "pinned_at": stamp,
                    "id": episode_id,
                },
            )
            session.commit()
        return True

    def clear_pin(self, episode_id: str) -> bool:
        """Unpin an episode — the WHOLE pin column set is cleared (N-1).

        ``is_keepsake`` goes false AND ``pinned_by`` / ``pinned_reason`` /
        ``pinned_at`` go NULL, so no stale attribution survives an unpin
        and ``pinned_by``'s CHECK is satisfied by NULL. Idempotent: an
        episode that is not a keepsake is a no-op, not an error.
        """
        with Session(self.engine) as session:
            result = session.execute(
                _MEMOIR_CLEAR_PIN_STATEMENT,
                {"is_keepsake": False, "id": episode_id},
            )
            session.commit()
            return bool(result.rowcount)

    def store_embedding(
        self,
        episode_id: str,
        vector: Sequence[float],
        model: str,
        dims: int,
        now: datetime | None = None,
    ) -> bool:
        """Write the semantic half — ONE atomic column-targeted UPDATE.

        The four embedding columns are set TOGETHER and the error is
        cleared (D2.16 / AM-15). A partial write would leave the pending
        predicate true and re-embed the row forever.
        """
        stamp = as_aware_utc(now) or now_utc()
        with Session(self.engine) as session:
            result = session.execute(
                _MEMOIR_STORE_EMBEDDING_STATEMENT,
                {
                    "embedding": [float(value) for value in vector],
                    "embedding_model": model,
                    "embedding_dims": int(dims),
                    "embedded_at": to_utc_iso(stamp),
                    "id": episode_id,
                },
            )
            session.commit()
            return bool(result.rowcount)

    def record_embedding_attempt(
        self, episode_id: str, error: str
    ) -> int:
        """Count a failed embed (OR a failed write-back) as an attempt.

        AM-30: a successful embed whose write-back failed would otherwise
        leave ``embedding IS NULL`` with ``attempts=0`` ⇒ an infinite
        re-embed that is never terminal and never reported.

        Returns:
            The row's attempt count AFTER the increment.
        """
        with Session(self.engine) as session:
            session.execute(
                _MEMOIR_RECORD_ATTEMPT_STATEMENT,
                {"error": str(error)[:500], "id": episode_id},
            )
            session.commit()
            episode = session.exec(
                select(MemoirEpisode).where(MemoirEpisode.id == episode_id)
            ).first()
            if episode is None:
                return 0
            return int(episode.embedding_attempts or 0)

    def mark_superseded(self, episode_id: str, superseded_by: str) -> bool:
        """Point an episode at its replacement (DD-8).

        Defined and UNCALLED in v1: the replacement pointer exists so a
        correction never destroys the record.
        """
        with Session(self.engine) as session:
            result = session.execute(
                _MEMOIR_MARK_SUPERSEDED_STATEMENT,
                {"superseded_by": superseded_by, "id": episode_id},
            )
            session.commit()
            return bool(result.rowcount)

    # ── reads ────────────────────────────────────────────────────────────

    def get_pending_embeddings(
        self,
        agent_id: str | None = None,
        limit: int = 100,
        *,
        attempt_cap: int = MEMOIR_EMBEDDING_ATTEMPT_CAP,
        exclude_terminal: bool = True,
    ) -> list[MemoirEpisode]:
        """The worker's pending queue — ``embedding IS NULL`` IS the queue.

        No side table: the column's emptiness is the queue (AM-30).

        W-2 (terminal-aware window): the query EXCLUDES the rows the
        worker would classify terminal (the single-sourced
        :func:`memoir_embedding_terminal_predicate`), so the oldest-N
        window ADVANCES past them. Without the exclusion, terminal rows
        — whose embedding never arrives — sat at the head of the window
        forever: ``pending > 0`` yet the worker's Python filter emptied
        ``candidates`` every tick, silently and permanently. The
        exclusion is the fix; the worker's Python-side filter stays as
        the belt. ``exclude_terminal=False`` restores the raw
        emptiness-is-the-queue read (the diagnostic shape).
        """
        with Session(self.engine) as session:
            stmt = (
                select(MemoirEpisode)
                .where(MemoirEpisode.embedding.is_(None))
                .where(MemoirEpisode.embedded_at.is_(None))
            )
            if agent_id:
                stmt = stmt.where(MemoirEpisode.agent_id == agent_id)
            if exclude_terminal:
                stmt = stmt.where(
                    ~memoir_embedding_terminal_predicate(attempt_cap)
                )
            stmt = stmt.order_by(
                MemoirEpisode.created_at.asc(), MemoirEpisode.id.asc()
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def fetch_keepsakes(self, agent_id: str) -> list[MemoirEpisode]:
        """The agent's keepsakes — newest first."""
        with Session(self.engine) as session:
            stmt = (
                select(MemoirEpisode)
                .where(MemoirEpisode.agent_id == agent_id)
                .where(MemoirEpisode.is_keepsake.is_(True))
                .order_by(MemoirEpisode.created_at.desc(), MemoirEpisode.id.asc())
            )
            return list(session.exec(stmt).all())

    def fetch_latest(
        self,
        agent_id: str,
        limit: int | None = None,
        *,
        only_embedded: bool = False,
    ) -> list[MemoirEpisode]:
        """The agent's episodes, newest first.

        ``only_embedded`` narrows the read to the rows that carry a
        semantic vector (the identity audit's input). ``limit=None`` means
        "the whole set" — the retrieval service needs the WHOLE set for
        the embedding-identity audit, because a bounded scan would make the
        exclusion count silently partial, which is exactly the loss
        D2.11 exists to disclose.
        """
        with Session(self.engine) as session:
            stmt = select(MemoirEpisode).where(MemoirEpisode.agent_id == agent_id)
            if only_embedded:
                stmt = stmt.where(MemoirEpisode.embedding.is_not(None))
            stmt = stmt.order_by(
                MemoirEpisode.created_at.desc(), MemoirEpisode.id.asc()
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def get_approved_persona(self, agent_id: str) -> MemoirPersonaVersion | None:
        """The persona version IN FORCE — the approved one, or ``None``.

        The pointer row decides; a pointer that names a version which is
        not ``approved`` falls back to the newest approved version, so a
        half-finished edit can never put an unapproved self in force
        (DD-9).
        """
        with Session(self.engine) as session:
            pointer = session.exec(
                select(MemoirPersona).where(MemoirPersona.agent_id == agent_id)
            ).first()
            if pointer is not None and pointer.current_version_id:
                version = session.exec(
                    select(MemoirPersonaVersion).where(
                        MemoirPersonaVersion.id == pointer.current_version_id
                    )
                ).first()
                if version is not None and version.status == "approved":
                    return version
            return session.exec(
                select(MemoirPersonaVersion)
                .where(MemoirPersonaVersion.agent_id == agent_id)
                .where(MemoirPersonaVersion.status == "approved")
                .order_by(MemoirPersonaVersion.version.desc())
            ).first()

    def seed_persona_if_absent(
        self, agent_id: str = MEMOIR_SEED_AGENT_ID
    ) -> MemoirPersonaVersion | None:
        """Idempotently install the day-one persona (the seed's seam).

        Delegates to :func:`daemon.repositories.memoir.seed.seed_persona_if_absent`
        — ONE implementation, so the boot-time call and this method can
        never drift.
        """
        return _seed_persona_if_absent(self.engine, agent_id)

    def lexical_rank(
        self,
        agent_id: str,
        query: str,
        limit: int = MEMOIR_RANK_CANDIDATES,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[tuple[str, float]]:
        """The LEXICAL ranker — driver-aware behind ONE interface (D2.2).

        PostgreSQL: ``ts_rank_cd(tsv, plainto_tsquery('english', :q))`` —
        ``tsvector`` FIRST, because PostgreSQL has no
        ``ts_rank_cd(tsquery, tsvector)`` overload (AM-1).

        SQLite / dev: the house Python BM25 helper
        (``skill_search_service._bm25_score``) over the fetched candidates.

        The two legs return the SAME SHAPE — an ordinal-ranked candidate
        list of ``(id, score)`` pairs, sorted by ``(score DESC, id ASC)`` —
        so the fusion cannot tell which driver produced them.
        """
        clean_query = (query or "").strip()
        if not clean_query:
            return []
        if self.engine.dialect.name == "postgresql":
            return self._lexical_rank_pg(agent_id, clean_query, limit, since, until)
        return self._lexical_rank_sqlite(agent_id, clean_query, limit, since, until)

    def timeline_window(
        self,
        agent_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        query: str | None = None,
        limit: int = MEMOIR_RANK_CANDIDATES,
    ) -> list[MemoirEpisode]:
        """The TIMELINE read — filter-then-rank (D2.9).

        ``since`` / ``until`` are INCLUSIVE bounds on ``event_at`` (the
        DR-4 rename: ``from`` / ``to`` are Python keywords). With a
        ``query`` the LEXICAL-ONLY ranker narrows the window; without one
        the order is ``event_at DESC``. There is NO cosine, NO RRF, NO
        recency prior and NO keepsake priority here — the prior would
        actively distort a historical window.
        """
        with Session(self.engine) as session:
            rows = self._window_rows(session, agent_id, since, until)
        if query and query.strip():
            ranked = self.lexical_rank(
                agent_id, query, limit, since=since, until=until
            )
            by_id = {row.id: row for row in rows}
            return [by_id[rid] for rid, _score in ranked if rid in by_id]
        return list(rows)

    def count_pins(self, agent_id: str, *, exclude_id: str | None = None) -> int:
        """How many keepsakes the agent holds (the count cap's input)."""
        with Session(self.engine) as session:
            return self._count_pins(session, agent_id, exclude_id=exclude_id)

    def keepsake_zone_tokens(
        self, agent_id: str, *, exclude_id: str | None = None
    ) -> int:
        """The token size of the agent's keepsake zone (the zone cap's input)."""
        with Session(self.engine) as session:
            return self._zone_tokens(session, agent_id, exclude_id=exclude_id)

    # ── internals ────────────────────────────────────────────────────────

    # -- the admission (D2.12) --

    def _admit_pin(
        self,
        session: Session,
        agent_id: str,
        *,
        candidate_id: str | None,
        candidate_tokens: int,
    ) -> None:
        """Apply the pin admission — the reads PRECEDE any write.

        Two bounds, both of which must hold:

        * the COUNT cap (12 pins);
        * the TOKEN zone (4,000 tokens for the admitted keepsakes).

        The refusal names the BINDING constraint and the current pins.
        Nothing is evicted (DD-3).
        """
        pins = self._pin_details(session, agent_id, exclude_id=candidate_id)
        if len(pins) >= MEMOIR_PIN_COUNT_CAP:
            raise MemoirAdmissionError(
                "pin_count",
                (
                    f"the keepsake count cap is reached: {len(pins)} of "
                    f"{MEMOIR_PIN_COUNT_CAP} pins are already held, so a new "
                    f"pin is refused (nothing is evicted)"
                ),
                pins=pins,
            )
        zone = sum(int(pin["tokens"]) for pin in pins)
        if zone + int(candidate_tokens) > MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET:
            raise MemoirAdmissionError(
                "keepsake_zone_tokens",
                (
                    f"the keepsake zone budget would be exceeded: the existing "
                    f"zone is {zone} tokens and the candidate renders to "
                    f"{candidate_tokens}, over the "
                    f"{MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET}-token zone"
                ),
                pins=pins,
            )

    def _pin_details(
        self, session: Session, agent_id: str, *, exclude_id: str | None = None
    ) -> list[dict[str, Any]]:
        """The current pins with their sizes — what a refusal names."""
        stmt = (
            select(MemoirEpisode)
            .where(MemoirEpisode.agent_id == agent_id)
            .where(MemoirEpisode.is_keepsake.is_(True))
            .order_by(MemoirEpisode.created_at.desc(), MemoirEpisode.id.asc())
        )
        if exclude_id:
            stmt = stmt.where(MemoirEpisode.id != exclude_id)
        pins: list[dict[str, Any]] = []
        for episode in session.exec(stmt).all():
            pins.append(
                {
                    "id": episode.id,
                    "title": episode.title,
                    "pinned_by": episode.pinned_by,
                    # W-1: each held pin counts as the RENDERED keepsake
                    # line the briefing ships for it — the same
                    # arithmetic the candidate is measured with, so the
                    # zone sum and the candidate render cannot disagree.
                    "tokens": _estimate_tokens(
                        render_keepsake_line(
                            title=episode.title,
                            pinned_by=episode.pinned_by,
                            pclass=KEEPSAKE_ADMISSION_PCLASS,
                            pinned_reason=episode.pinned_reason,
                        )
                    ),
                }
            )
        return pins

    def _count_pins(
        self, session: Session, agent_id: str, *, exclude_id: str | None = None
    ) -> int:
        """The pin COUNT — derived from the SAME list the zone sums.

        Tidier M8: the counter used to run its own query, so it could
        drift from ``_pin_details`` (the thing admission actually
        measures). ``len`` of the one list keeps the denominator and the
        numerators single-sourced.
        """
        return len(self._pin_details(session, agent_id, exclude_id=exclude_id))

    def _zone_tokens(
        self, session: Session, agent_id: str, *, exclude_id: str | None = None
    ) -> int:
        return sum(
            int(pin["tokens"])
            for pin in self._pin_details(session, agent_id, exclude_id=exclude_id)
        )

    # -- the lexical rankers --

    def _lexical_rank_pg(
        self,
        agent_id: str,
        query: str,
        limit: int,
        since: datetime | None,
        until: datetime | None,
    ) -> list[tuple[str, float]]:
        """The PostgreSQL lexical ranker (D2.2 / AM-1)."""
        sql = _MEMOIR_PG_LEXICAL_SQL
        binds: list[Any] = [
            bindparam("q", type_=Text()),
            bindparam("agent_id", type_=Text()),
            bindparam("cand", type_=Integer()),
        ]
        params: dict[str, Any] = {
            "q": query,
            "agent_id": agent_id,
            "cand": int(limit),
        }
        if since is not None or until is not None:
            sql += " AND event_at >= :since AND event_at <= :until"
            binds.extend(
                [
                    bindparam("since", type_=DateTime(timezone=True)),
                    bindparam("until", type_=DateTime(timezone=True)),
                ]
            )
            params["since"] = as_aware_utc(since) or _MEMOIR_WINDOW_FLOOR
            params["until"] = as_aware_utc(until) or _MEMOIR_WINDOW_CEILING
        sql += " ORDER BY score DESC, id ASC LIMIT :cand"
        statement = text(sql).bindparams(*binds)
        with Session(self.engine) as session:
            rows = session.execute(statement, params).fetchall()
        return [(str(row[0]), float(row[1])) for row in rows]

    def _lexical_rank_sqlite(
        self,
        agent_id: str,
        query: str,
        limit: int,
        since: datetime | None,
        until: datetime | None,
    ) -> list[tuple[str, float]]:
        """The SQLite / dev lexical ranker — the house BM25 helper (D2.2).

        The house helper is ``skill_search_service._bm25_score`` (the
        ``critical_notes_selector`` precedent): stock PostgreSQL has no
        BM25, so ``ts_rank_cd`` is the closest stock approximation there,
        and RRF consumes only ORDINAL ranks, so the absolute score family
        is immaterial to the fusion.
        """
        from daemon.services.skill_search_service import _bm25_score, _tokenize

        with Session(self.engine) as session:
            rows = self._window_rows(session, agent_id, since, until)
        if not rows:
            return []

        documents: list[list[str]] = []
        for episode in rows:
            documents.append(
                _tokenize(render_episode_content(episode.title, episode.body))
            )
        total_docs = len(documents)
        if total_docs == 0:
            return []
        avg_doc_len = sum(len(doc) for doc in documents) / float(total_docs)
        if avg_doc_len <= 0:
            avg_doc_len = 1.0
        doc_freqs: dict[str, int] = {}
        for doc in documents:
            for term in set(doc):
                doc_freqs[term] = doc_freqs.get(term, 0) + 1

        query_tokens = _tokenize(query)
        scored: list[tuple[str, float]] = []
        for episode, doc in zip(rows, documents):
            score = _bm25_score(query_tokens, doc, doc_freqs, total_docs, avg_doc_len)
            if score > 0:
                scored.append((episode.id, float(score)))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit]

    def _window_rows(
        self,
        session: Session,
        agent_id: str,
        since: datetime | None,
        until: datetime | None,
    ) -> list[MemoirEpisode]:
        """The agent's episodes inside the INCLUSIVE window (on ``event_at``)."""
        stmt = select(MemoirEpisode).where(MemoirEpisode.agent_id == agent_id)
        if since is not None or until is not None:
            stmt = stmt.where(
                MemoirEpisode.event_at >= (as_aware_utc(since) or _MEMOIR_WINDOW_FLOOR)
            ).where(
                MemoirEpisode.event_at <= (as_aware_utc(until) or _MEMOIR_WINDOW_CEILING)
            )
        stmt = stmt.order_by(MemoirEpisode.event_at.desc(), MemoirEpisode.id.asc())
        return list(session.exec(stmt).all())

    # -- the write bounds (D2.13) --

    @staticmethod
    def _validate_body(body: str) -> str:
        if not isinstance(body, str) or not body.strip():
            raise MemoirWriteError("body_required", "the episode body is required")
        if len(body) > MEMOIR_BODY_CHAR_CAP:
            raise MemoirWriteError(
                "body_too_long",
                (
                    f"the body is {len(body)} characters, over the "
                    f"{MEMOIR_BODY_CHAR_CAP}-character cap"
                ),
            )
        if len(body.split()) > MEMOIR_BODY_WORD_CAP:
            raise MemoirWriteError(
                "body_too_long",
                (
                    f"the body is {len(body.split())} words, over the "
                    f"{MEMOIR_BODY_WORD_CAP}-word cap"
                ),
            )
        return body

    @staticmethod
    def _validate_title(title: str | None) -> str | None:
        if title is None:
            return None
        if not isinstance(title, str):
            raise MemoirWriteError("invalid_title", "the title must be a string")
        if len(title) > MEMOIR_TITLE_CHAR_CAP:
            raise MemoirWriteError(
                "title_too_long",
                (
                    f"the title is {len(title)} characters, over the "
                    f"{MEMOIR_TITLE_CHAR_CAP}-character cap"
                ),
            )
        return title

    @staticmethod
    def _validate_stars(stars: Sequence[str] | None) -> list[str] | None:
        """The star bounds are HYGIENE, not normalization (adjudication row 4).

        The bound is measured POST-TRIM, but the stored value is VERBATIM:
        the mention is captured exactly as the writer phrased it, because
        the alias evidence lives in the phrasing (DD-19).
        """
        if stars is None:
            return None
        if isinstance(stars, (str, bytes)):
            raise MemoirWriteError(
                "invalid_stars", "the stars must be a list of strings"
            )
        entries = list(stars)
        if len(entries) > MEMOIR_STARS_MAX_ENTRIES:
            raise MemoirWriteError(
                "too_many_stars",
                (
                    f"{len(entries)} stars were given, over the "
                    f"{MEMOIR_STARS_MAX_ENTRIES}-entry cap"
                ),
            )
        for entry in entries:
            if not isinstance(entry, str):
                raise MemoirWriteError(
                    "invalid_stars", "every star must be a string"
                )
            if len(entry.strip()) > MEMOIR_STAR_CHAR_CAP:
                raise MemoirWriteError(
                    "star_too_long",
                    (
                        f"a star is {len(entry.strip())} characters, over the "
                        f"{MEMOIR_STAR_CHAR_CAP}-character cap"
                    ),
                )
        return entries

    @staticmethod
    def _validate_importance(importance: int) -> int:
        try:
            value = int(importance)
        except (TypeError, ValueError) as exc:
            raise MemoirWriteError(
                "importance_out_of_range",
                f"the importance must be an integer; got {importance!r}",
            ) from exc
        if value < MEMOIR_IMPORTANCE_MIN or value > MEMOIR_IMPORTANCE_MAX:
            raise MemoirWriteError(
                "importance_out_of_range",
                (
                    f"the importance must be between {MEMOIR_IMPORTANCE_MIN} and "
                    f"{MEMOIR_IMPORTANCE_MAX}; got {value}"
                ),
            )
        return value

    @staticmethod
    def _parse_event_at(event_at: datetime | str | None) -> datetime | None:
        """``event_at`` is ISO-parsed, else refused (D2.13)."""
        if event_at is None:
            return None
        if isinstance(event_at, datetime):
            return as_aware_utc(event_at)
        if not isinstance(event_at, str) or not event_at.strip():
            raise MemoirWriteError(
                "event_at_invalid", "the event time must be an ISO-8601 string"
            )
        try:
            parsed = datetime.fromisoformat(event_at.strip())
        except ValueError as exc:
            raise MemoirWriteError(
                "event_at_invalid",
                f"the event time {event_at!r} is not an ISO-8601 timestamp",
            ) from exc
        return as_aware_utc(parsed)

    @staticmethod
    def _validate_pin_claim(actor: str | None, reason: str | None) -> None:
        """The pin claim is a CLAIM, never derived (adjudication row 2).

        ``pinned_by`` is restricted to the two governors (R2), and a pin
        without a reason is refused — the reason is part of the pin's
        meaning, not an optional extra.
        """
        if actor not in PIN_ACTORS:
            raise MemoirWriteError(
                "invalid_actor",
                (
                    f"the pin actor must be one of {sorted(PIN_ACTORS)}; "
                    f"got {actor!r}"
                ),
            )
        if not isinstance(reason, str) or not reason.strip():
            raise MemoirWriteError(
                "reason_required", "a pin requires a reason"
            )


__all__ = [
    "MEMOIR_BODY_CHAR_CAP",
    "MEMOIR_BODY_WORD_CAP",
    "MEMOIR_EMBEDDING_ATTEMPT_CAP",
    "MEMOIR_EMBEDDING_TERMINAL_MARKER",
    "MEMOIR_IMPORTANCE_MAX",
    "MEMOIR_IMPORTANCE_MIN",
    "MEMOIR_INSERT_COLUMNS",
    "MEMOIR_INSERT_SQL",
    "MEMOIR_KEEPSAKE_ZONE_TOKEN_BUDGET",
    "MEMOIR_PIN_COUNT_CAP",
    "MEMOIR_RANK_CANDIDATES",
    "MEMOIR_STARS_MAX_ENTRIES",
    "MEMOIR_STAR_CHAR_CAP",
    "MEMOIR_TITLE_CHAR_CAP",
    "MemoirAdmissionError",
    "MemoirPersonaRepository",
    "MemoirRepository",
    "MemoirTransitionError",
    "MemoirWriteError",
    "as_aware_utc",
    "memoir_embedding_terminal_predicate",
    "memoir_row_is_terminal",
    "render_episode_content",
]
