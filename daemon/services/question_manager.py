"""Per-instance in-memory question-pack state manager.

Mirrors the threading.Lock + dict-keyed-by-instance_id pattern used by
``daemon.services.todo_manager.TodoGraphManager``. A single question pack
per instance is held in ``_packs``; at most one pack may be ``pending``
at any time. Once the user submits answers (via the Phase 2 answer API),
the pack transitions to ``"answered"`` but stays readable until
``clear_question_pack`` is called by the cleanup path
(``InstanceManager._cleanup_instance_state``).

Question packs are NOT persisted — they exist for the lifetime of the
daemon process and are used by the question tool surface during a single
instance pause/resume cycle.

Thread Safety:
    All state mutations and snapshot reads are guarded by a single
    :class:`threading.Lock` (NOT :class:`asyncio.Lock`), matching the
    convention used by every other per-instance manager. Tools hand in
    the question call via ``set_question_pack`` from an async context,
    so the lock is held for the synchronous mutation only — the lock is
    released before any awaitable work runs.

SSE Payload Schema:
    :func:`pack_to_dict` defines the JSON-serializable shape used for
    both the Phase 1 pending event (emitted by the ``question`` tool
    before the pause cascade) and the Phase 2 answered event (emitted
    by the answer endpoint before the resume cascade). The schema must
    stay aligned across both phases — frontend consumers read the same
    dict shape regardless of which SSE event fires.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_option(value: Any) -> str:
    """Defense-in-depth — the ``ask_questions`` tool boundary normalizes
    options to plain strings before they reach this helper. These
    branches only fire when the manager is called directly (e.g. from
    tests or a future non-tool entry); they are unreachable via the
    tool path.

    Coerce a single question option to a plain string.

    LLMs occasionally pass option entries as ``{"text": "Option A"}``
    dicts (or other JSON-object shapes) instead of plain strings. When
    those objects reach the frontend, Angular's interpolation renders
    them as ``[object Object]`` and any click-driven answer path stores
    the object verbatim — corrupting both display and submit.

    The manager's contract is ``options: list[str]``. To honor that
    contract without rejecting well-meaning callers, this helper applies
    a friendly normalization:

      * ``str`` → returned as-is (the common case).
      * ``dict`` with a usable ``"text"`` key → the extracted text value,
        recursively normalized so a nested object also collapses to a
        string.
      * ``dict`` with no usable ``"text"`` (``None``, missing, or empty
        after coercion) → ``""`` so we don't leak a Python
        ``{...}`` repr to the UI as a chip label.
      * Anything else → ``str(value)`` so the field is never silently
        dropped.

    Mirrors the ``bool(q.get("allow_custom", True))`` coercion style used
    by the surrounding ``set_question_pack`` body — never raises, always
    produces a ``str``.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("text")
        if raw is None:
            return ""
        text = _normalize_option(raw)
        return text if text else ""
    return str(value)


def _normalize_options(values: Any) -> list[str]:
    """Defense-in-depth — the ``ask_questions`` tool boundary enforces
    ``options: list`` and rejects str / tuple / set / int at the
    validation layer. These branches only fire when the manager is
    called directly (e.g. from tests or a future non-tool entry);
    set-iteration order is non-deterministic but acceptable because
    the values never reach the wire path without tool-boundary
    normalization first.

    Normalize an ``options`` field to a fresh ``list[str]``.

    Behavior:

      * ``None`` / missing / empty → ``[]`` (mirrors the existing
        ``list(q.get("options", []) or [])`` collapse).
      * ``list`` / ``tuple`` / ``set`` → iterate and normalize each
        element.
      * ``str`` → wrap into a single-element list rather than
        character-splitting (``list("Approach A")`` produced 10 garbage
        chips per option, which would otherwise leak to the UI).
      * Any other non-iterable scalar (e.g. ``int``) → wrap into a
        single-element list as a friendly fallback.

    Always returns a new list (no shared mutable state with the input).
    Never raises.
    """
    if not values:
        return []
    if isinstance(values, str):
        return [_normalize_option(values)]
    if isinstance(values, (list, tuple, set)):
        return [_normalize_option(item) for item in values]
    # Non-iterable scalar (e.g. int) — wrap as a single option rather
    # than dropping it on the floor.
    return [_normalize_option(values)]


@dataclass
class Question:
    """A single question inside a :class:`QuestionPack`.

    Identity is the string ``id`` (UUID4 hex by default — auto-generated
    when the tool caller omits an explicit id).

    Attributes:
        id: Stable identifier for the question. Used as the key inside
            :attr:`QuestionPack.answers` so callers can correlate
            question/answer pairs without parsing the question text.
        text: Human-readable question body shown to the user.
        options: Optional list of pre-canned option strings the frontend
            may render as buttons / a dropdown. Empty list means the
            question is open-ended (free-text answer).
        allow_custom: When ``True`` the frontend allows a free-text
            answer in addition to the canned options. Defaults to
            ``True`` — most questions benefit from allowing a custom
            answer.
        required: When ``True`` the question must be answered before the
            pack can transition out of ``pending``. Defaults to
            ``True``.
        answer: Reserved for any answer the manager writes back. The
            Phase 2 answer API stores the answer as a generic dict in
            :attr:`QuestionPack.answers` keyed by id (so multiple-shape
            JSON is accepted), so this field stays ``None`` for now.
        option_descriptions: Optional display metadata mapping option
            string → description, carried through from label-object
            options (``{"label": ..., "description": ...}``) accepted by
            the ``ask_questions`` tool boundary. Purely additive
            metadata — the frontend-facing ``options`` contract stays
            ``list[str]``; consumers that don't know this key ignore it.
    """

    id: str
    text: str
    options: list[str] = field(default_factory=list)
    allow_custom: bool = True
    required: bool = True
    answer: str | None = None
    option_descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class QuestionPack:
    """A bundle of one or more questions for a single instance.

    Attributes:
        instance_id: Owning instance identifier.
        questions: Ordered list of :class:`Question` entries.
        status: One of ``"pending"`` (waiting for user) or
            ``"answered"`` (user has responded).
        answers: User-supplied answers. Shape is intentionally flexible —
            the manager stores whatever dict the Phase 2 API receives.
            Each key may be a question id (preferred) or question text
            (for backward compatibility with ad-hoc clients).
        created_at: ISO-8601 UTC timestamp at which the pack was
            created. Auto-generated at construction time.
    """

    instance_id: str
    questions: list[Question]
    status: str = "pending"
    answers: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class QuestionManager:
    """In-memory, per-instance question-pack manager.

    Each ``instance_id`` owns at most one :class:`QuestionPack` at a
    time. A second ``set_question_pack`` call for an instance whose
    existing pack is still ``pending`` is rejected (returns ``None``)
    — the tool surfaces a user-visible error rather than overwriting a
    still-pending pack. Once the pack is ``answered``, the next
    ``set_question_pack`` replaces it.

    Thread Safety:
        All state mutations and snapshot reads are serialized through a
        single :class:`threading.Lock`. Helpers document this
        requirement; they must NOT be called from outside a ``with
        self._lock:`` scope.
    """

    def __init__(self) -> None:
        """Initialize the manager with empty per-instance packs."""
        self._packs: dict[str, QuestionPack] = {}
        self._lock = threading.Lock()

    def set_question_pack(
        self,
        instance_id: str,
        questions: list[dict],
    ) -> QuestionPack | None:
        """Store a question pack for ``instance_id``.

        Rejects duplicate pending packs — at most one ``pending`` pack
        per instance. Auto-generates UUID ids for questions missing one.

        Args:
            instance_id: Owning instance identifier.
            questions: List of question spec dicts. Each spec accepts
                ``id`` (str, optional), ``text`` (str, required),
                ``options`` (list[str], optional), ``allow_custom``
                (bool, optional, default ``True``), and ``required``
                (bool, optional, default ``True``).

        Returns:
            The newly stored :class:`QuestionPack`, or ``None`` when an
            existing pack is still ``pending`` for the same instance.
            Callers (the ``question`` tool) translate ``None`` into a
            user-visible error.
        """
        with self._lock:
            existing = self._packs.get(instance_id)
            if existing is not None and existing.status == "pending":
                logger.warning(
                    "QuestionManager rejected duplicate pending pack for "
                    "instance %s",
                    instance_id,
                )
                return None

            parsed: list[Question] = []
            for q in questions:
                if not isinstance(q, dict):
                    raise ValueError(
                        f"Question spec must be a dict, got "
                        f"{type(q).__name__}"
                    )
                text = q.get("text")
                if not isinstance(text, str) or not text:
                    raise ValueError(
                        f"Question spec missing required non-empty 'text': "
                        f"{q!r}"
                    )
                qid = q.get("id") or str(uuid.uuid4())
                if not isinstance(qid, str) or not qid:
                    qid = str(uuid.uuid4())
                # Optional label-object description metadata (carried
                # through from the ask_questions tool's normalization).
                # Defensively sanitized: keep only non-empty str→str
                # entries; anything else collapses to {}.
                raw_descriptions = q.get("option_descriptions")
                descriptions: dict[str, str] = {}
                if isinstance(raw_descriptions, dict):
                    descriptions = {
                        k: v
                        for k, v in raw_descriptions.items()
                        if isinstance(k, str) and k and isinstance(v, str) and v
                    }
                parsed.append(
                    Question(
                        id=qid,
                        text=text,
                        options=_normalize_options(q.get("options")),
                        allow_custom=bool(q.get("allow_custom", True)),
                        required=bool(q.get("required", True)),
                        option_descriptions=descriptions,
                    )
                )

            pack = QuestionPack(
                instance_id=instance_id,
                questions=parsed,
            )
            self._packs[instance_id] = pack
            return pack

    def get_question_pack(self, instance_id: str) -> QuestionPack | None:
        """Return the current pack for ``instance_id``, or ``None``.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            The stored :class:`QuestionPack`, or ``None`` when no pack
            exists for the instance.
        """
        with self._lock:
            return self._packs.get(instance_id)

    def set_answers(
        self,
        instance_id: str,
        answers: dict,
    ) -> QuestionPack | None:
        """Transition the pack to ``"answered"`` and store ``answers``.

        Idempotent on already-answered packs: a second call overwrites
        ``answers`` and keeps ``status="answered"``. This matches the
        PAUSED-branch semantics in the answer endpoint (the second call
        would be a retry from the frontend).

        Args:
            instance_id: Owning instance identifier.
            answers: User-supplied answer dict. Shape is intentionally
                flexible — accepted as-is.

        Returns:
            The updated :class:`QuestionPack`, or ``None`` when no pack
            exists for the instance. The Phase 2 API translates
            ``None`` into a 404.
        """
        with self._lock:
            pack = self._packs.get(instance_id)
            if pack is None:
                logger.warning(
                    "QuestionManager.set_answers: no pack for instance %s",
                    instance_id,
                )
                return None
            pack.status = "answered"
            pack.answers = dict(answers) if isinstance(answers, dict) else {}
            return pack

    def clear_question_pack(self, instance_id: str) -> None:
        """Drop the pack for ``instance_id`` entirely.

        Called from ``InstanceManager._cleanup_instance_state`` so
        terminate / release / hard-delete paths uniformly free memory.
        Safe to call when no pack exists.

        Args:
            instance_id: Owning instance identifier.
        """
        with self._lock:
            self._packs.pop(instance_id, None)


def pack_to_dict(pack: QuestionPack) -> dict[str, Any]:
    """Serialize a :class:`QuestionPack` to a JSON-safe dict.

    This is the **frozen SSE payload schema** for question packs. Both
    the Phase 1 pending event (emitted by the ``question`` tool before
    the pause cascade) and the Phase 2 answered event (emitted by the
    answer endpoint before the resume cascade) reuse this shape so the
    frontend can consume either with the same parser.

    Shape::

        {
            "instance_id": str,
            "status": "pending" | "answered",
            "created_at": str,             # ISO-8601 UTC
            "questions": [
                {
                    "id": str,
                    "text": str,
                    "options": list[str],
                    "allow_custom": bool,
                    "required": bool,
                    "answer": str | None,
                    "option_descriptions": dict[str, str],
                    # display metadata from label-object options; always
                    # emitted (empty dict when no descriptions were
                    # supplied). Additive key — the frontend-facing
                    # options contract stays list[str].
                },
                ...
            ],
            "answers": dict,               # whatever the API received
        }

    Args:
        pack: The :class:`QuestionPack` to serialize.

    Returns:
        Plain-dict copy safe for JSON serialization and SSE transport.
    """
    return {
        "instance_id": pack.instance_id,
        "status": pack.status,
        "created_at": pack.created_at,
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "options": _normalize_options(q.options),
                "allow_custom": q.allow_custom,
                "required": q.required,
                "answer": q.answer,
                "option_descriptions": dict(q.option_descriptions),
            }
            for q in pack.questions
        ],
        "answers": dict(pack.answers),
    }
