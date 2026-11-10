"""Memoir Store v2 — the POIGNANCY scorer (the narrative-weight axis).

Phase 4 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase4-plan.md`` T4.1/T4.4;
decisions V2-D 4.1, 4.4, 4.6, 4.7; flags V2-10, V2-14, V2-16).

THE IMPORTANCE RULING (V2-10 / §3-A2 / Register #3) — THE LOAD-BEARING
CONTRACT OF THIS LANE:

* ``importance`` is **WRITE-FROZEN**. It is the AUTHORED, bounded 1-10
  judgment the writer granted at INSERT time. NOTHING in this lane
  rewrites it — not the scorer, not the worker, not the repository
  write-back. The standing proof is the ``RECORD_COLUMNS`` purity pin
  (``tests/unit/test_memoir_write_purity_unit.py``), which stays GREEN
  and UNTOUCHED.
* the LLM's score lands in the SEPARATE ``poignancy INTEGER NULL``
  column, and the state lives in ``poignancy_status``;
* every CONSUMER reads the EFFECTIVE importance
  ``COALESCE(poignancy, importance)`` — the reflection trigger
  (``reflection_repository.py``) already does, and the recall ranker
  (``memoir_retrieval.py``) is the phase-4 consumer (T4.6 / AC7).

THE HOT PATH IS NEVER TOUCHED (DD-20 / R4): ``memoir_write`` stays ONE
fast INSERT and NOTHING calls the LLM on it. The scoring is
FIRE-AND-FORGET from a background worker.

THE SCALE (V2-D 4.4): 1 = trivial pleasantry, 5 = standard operational,
8-9 = significant milestone, 10 = constitutional decree. The score is an
INTEGER, clamped strictly to ``1..10`` — a floating-point score would
violate the integer column contract, so a fractional answer is rounded
to the nearest integer (half up) and then clamped.

THE FAILURE CLASSIFICATION (V2-D 4.6) is a CLONE of the embedding
worker's :func:`daemon.services.memoir_embedding_worker.classify_embed_failure`:
a 401/403 is an IMMEDIATE terminal (a dead credential must not burn a
row's attempt budget against a wall); a transient status or a connection
error increments the attempt counter; a permanent status terminalizes
at once. The parse failure — the model answered with something that
carries no usable score — is the generic case: it counts an attempt and
becomes terminal only when the attempt cap is reached (AC3's
``terminal:parse_failure``).

THE HTTP-REQUEST CEILING (the TRUE number, not the remembered one):

* the SDK client's own retry is OFF (``max_retries=0``) — the facade
  owns the retry discipline, and a second invisible ladder on top of it
  would inflate the request count;
* the facade's budget is ``transient_max=3`` / ``timeout_max=2``
  (``daemon/services/llm_failover.py``), and
  :func:`daemon.llm_error_classifier.derive_ha_attempt_ceiling` gives
  ``max(3, 2) = 3`` attempts for the primary phase and
  ``3 + max(3, 2) = 6`` when a BACKUP is configured
  (``base_url_backup`` present and different from the primary);
* so ONE scoring LLM call issues **at most 6 HTTP requests with a
  configured backup, 3 without one** — each facade attempt being exactly
  one HTTP request because ``max_retries=0``.

THE KNOB FAMILY is ISOLATED (N1 / V2-D 4.9): this module reads the
``memoir_poignancy`` section ONLY — it never calls the global knob
resolver (``resolve_memoir_knob``) and it keeps NO second copy of the
truth table: the raw values are classified by the SHARED helpers
(:func:`daemon.config._classify_enabled_flag` and
:func:`daemon.config._classify_numeric`), so the boot tier and the
per-tick tier can never disagree about what a value MEANS.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from daemon.llm_error_classifier import (
    RETRYABLE_STATUS_CODES,
    classify_transient_apierror_body,
)
from daemon.services.memoir_reflection_service import _clamp_prompt

logger = logging.getLogger(__name__)

__all__ = [
    "MEMOIR_POIGNANCY_BACKFILL_MAX",
    "MEMOIR_POIGNANCY_BATCH_CAP",
    "MEMOIR_POIGNANCY_CALL_TIMEOUT_S",
    "MEMOIR_POIGNANCY_MAX",
    "MEMOIR_POIGNANCY_MAX_PROMPT_TOKENS",
    "MEMOIR_POIGNANCY_MIN",
    "MEMOIR_POIGNANCY_TICK_S",
    "MemoirPoignancyService",
    "POIGNANCY_AUTH_REJECTED_REASON",
    "POIGNANCY_CALL_TIMEOUT_REASON",
    "POIGNANCY_EXCEPTION_REASON",
    "PoignancyOutcome",
    "PoignancySettings",
    "build_poignancy_prompt",
    "classify_poignancy_failure",
    "parse_poignancy_score",
    "resolve_poignancy_settings",
]


# ── the scale (V2-D 4.4) ──────────────────────────────────────────────────
#: The lowest legal score. The column's CHECK constraint mirrors it.
MEMOIR_POIGNANCY_MIN = 1

#: The highest legal score.
MEMOIR_POIGNANCY_MAX = 10

#: The default tick interval (seconds) — V2-D 4.2 / §3.1.
MEMOIR_POIGNANCY_TICK_S = 20

#: The default batch cap (episodes per tick) — V2-D 4.2 / §3.1.
MEMOIR_POIGNANCY_BATCH_CAP = 10

#: The default legacy backfill budget — 0 means NO legacy episode is
#: ever scored (V2-14 / §3-A3).
MEMOIR_POIGNANCY_BACKFILL_MAX = 0

#: The per-LLM-call timeout (V2-D 4.2's "30s timeout").
MEMOIR_POIGNANCY_CALL_TIMEOUT_S = 30.0

#: The prompt token budget — the composed INPUT is clamped to it.
MEMOIR_POIGNANCY_MAX_PROMPT_TOKENS = 1200

#: The body characters shown to the model (the prompt is clamped
#: anyway — this keeps the composed prompt cheap to build).
MEMOIR_POIGNANCY_BODY_CHARS = 2000


# ── the failure classification (V2-D 4.6 — a clone of the embed lane) ────
#: The house retryable set WIDENED lane-locally with 408 (Request
#: Timeout) — the house constant itself is NOT modified, and membership
#: is tested EXPLICITLY (the house helper alone classifies 408 as
#: permanent, which would burn a row's attempt budget on a transient).
MEMOIR_POIGNANCY_RETRYABLE_STATUS_CODES = frozenset(RETRYABLE_STATUS_CODES) | {408}

#: Dead-credential statuses: the row goes terminal AT ONCE and the tick
#: counts as an all-fail tick — the WORKER's lane-down latch engages on
#: the FIRST occurrence (``daemon.services.memoir_poignancy_worker``).
PERMANENT_CREDENTIAL_STATUS_CODES = frozenset({401, 403})

# ── the reason tokens the WORKER matches on (the lane's vocabulary) ─────
#: The reason token of a DEAD CREDENTIAL (V2-D 4.6). The worker matches
#: on it to arm the lane-down latch (the embedding lane's D3.9 shape).
POIGNANCY_AUTH_REJECTED_REASON = "poignancy_auth_rejected"

#: The reason token of a RAISING EPISODE (the worker's per-episode
#: isolation): the attempt is counted and the row terminalizes at the
#: cap like any other failure — the queue ADVANCES past it.
POIGNANCY_EXCEPTION_REASON = "poignancy_exception"

#: The reason token of a scoring call that exceeded the tick's remaining
#: budget (the per-episode wall-clock bound): a TRANSIENT failure — the
#: attempt is counted, the row stays in the queue.
POIGNANCY_CALL_TIMEOUT_REASON = "poignancy_call_timeout"


def _status_code(exc: BaseException) -> int | None:
    """The HTTP status carried by an SDK/transport exception, if any."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def classify_poignancy_failure(exc: BaseException) -> tuple[str, bool]:
    """Classify a scoring failure into ``(reason_token, is_immediate_terminal)``.

    The CLONE of :func:`daemon.services.memoir_embedding_worker.classify_embed_failure`
    with this lane's vocabulary (V2-D 4.6):

    ==============================  ==========================  =============
    Failure                         reason token                terminal?
    ==============================  ==========================  =============
    HTTP 401 / 403                  ``poignancy_auth_rejected``  IMMEDIATE
    HTTP 408/429/5xx (retryable)    ``poignancy_status_transient``  no
    any other HTTP status           ``poignancy_status_permanent``  IMMEDIATE
    connection / transport error    ``poignancy_connection_error``   no
    APIError with a transient body  ``poignancy_api_error_body``      no
    anything else                   ``poignancy_error``               no
    ==============================  ==========================  =============

    The attempt cap is the CALLER's decision (AC3): a row that keeps
    failing becomes terminal at the cap, with the reason token of its
    LAST failure — so a row whose model kept answering garbage ends as
    ``terminal:parse_failure``.
    """
    import httpx
    import openai

    status = _status_code(exc)
    if status is not None:
        if status in PERMANENT_CREDENTIAL_STATUS_CODES:
            return (POIGNANCY_AUTH_REJECTED_REASON, True)
        if status in MEMOIR_POIGNANCY_RETRYABLE_STATUS_CODES:
            return ("poignancy_status_transient", False)
        return ("poignancy_status_permanent", True)
    if isinstance(exc, (openai.APIConnectionError, httpx.TransportError)):
        return ("poignancy_connection_error", False)
    if isinstance(exc, openai.APIError) and classify_transient_apierror_body(exc):
        return ("poignancy_api_error_body", False)
    return ("poignancy_error", False)


# ── the tolerant score parser (V2-D 4.4) ─────────────────────────────────
#: A JSON-ish answer: ``{"score": 7}`` / ``"score": 7`` / ``score = 7``.
_SCORE_FIELD_RE = re.compile(r"[\"']?score[\"']?\s*[:=]\s*(-?\d+(?:\.\d+)?)", re.I)

#: A bare number anywhere in the answer.
_SCORE_NUMBER_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def parse_poignancy_score(raw: str) -> int | None:
    """Parse the model's answer into an INTEGER score in ``1..10``.

    TOLERANT by design: the model is asked for a bare integer, but a
    sentence ("I would rate this a 7."), a JSON envelope
    (``{"score": 7}``) or a padded answer all parse. The result is
    CLAMPED strictly to ``1..10`` — the column's CHECK constraint is the
    last line of defence, and a clamped value is still a usable score.

    A fractional answer is ROUNDED to the nearest integer (half up) —
    the STORED value is an integer either way (the integer schema
    contract), and refusing a "7.5" would throw away a usable answer.

    Returns:
        The clamped integer score, or ``None`` when the answer carries
        NO usable number (the parse failure — AC3's ``parse_failure``).
    """
    text = (raw or "").strip()
    if not text:
        return None
    match = _SCORE_FIELD_RE.search(text) or _SCORE_NUMBER_RE.search(text)
    if match is None:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):  # pragma: no cover — the regex guarantees it
        return None
    rounded = int(value + 0.5) if value >= 0 else -int(-value + 0.5)
    return max(MEMOIR_POIGNANCY_MIN, min(MEMOIR_POIGNANCY_MAX, rounded))


# ── the prompt (§3.2 — title, event time, body, star tags) ──────────────


def build_poignancy_prompt(episode: Any) -> str:
    """Compose the scoring prompt for ONE episode.

    The prompt carries the four things the plan names — the title, the
    event time, the body and the star tags — plus the scale anchors and
    the answer format. The body is truncated to
    :data:`MEMOIR_POIGNANCY_BODY_CHARS` so a 16k-character episode can
    never blow the prompt budget.
    """
    title = str(getattr(episode, "title", "") or "")
    body = str(getattr(episode, "body", "") or "")
    if len(body) > MEMOIR_POIGNANCY_BODY_CHARS:
        body = body[:MEMOIR_POIGNANCY_BODY_CHARS] + "…"
    event_at = getattr(episode, "event_at", None) or getattr(
        episode, "created_at", None
    )
    if isinstance(event_at, datetime):
        event_text = event_at.astimezone(timezone.utc).isoformat()
    else:
        event_text = str(event_at or "")
    stars = getattr(episode, "stars", None) or []
    star_text = ", ".join(str(star) for star in stars) if stars else "(none)"

    lines = [
        "You are the narrative-weight meter of a person who writes her own "
        "memories.",
        "Rate how much this episode MATTERS to her — its narrative "
        "significance, not its length.",
        "",
        "Scale:",
        "1 = trivial pleasantry",
        "5 = standard operational event",
        "8-9 = significant milestone",
        "10 = constitutional decree",
        "",
        f"title: {title or '(untitled)'}",
        f"event_at: {event_text}",
        f"stars: {star_text}",
        f"body: {body}",
        "",
        "Answer with ONE integer from 1 to 10 and nothing else.",
    ]
    return "\n".join(lines)


# ── the settings (the per-tick re-read — N1 / V2-D 4.7 / 4.9) ───────────


@dataclass(frozen=True)
class PoignancySettings:
    """One tick's resolved settings (re-read EVERY tick).

    The knob resolution lives HERE (not in the knob module): the plan's
    T4.4 names the knob module a TOUCHPOINT for the tool-layer boot-state
    surfacing and the status inspection ONLY — the WORKER's knob
    resolution belongs to the lane's own module (N1 / V2-D 4.9).
    """

    enabled: bool
    tick_interval_s: int
    batch_cap: int
    backfill_max: int
    call_timeout_s: float
    max_prompt_tokens: int
    #: The knob's provenance — the resolution code and the rejected value
    #: (the operator-facing surface reads these).
    resolution: str
    source: str
    rejected_value: str | None


def _poignancy_section(config: Any) -> Any:
    """The lane's OWN config section — never the global resolver."""
    from daemon.config import MemoirPoignancyConfig

    section = getattr(config, "memoir_poignancy", None) if config is not None else None
    if section is None:
        try:
            section = MemoirPoignancyConfig()
        except Exception:  # noqa: BLE001 — a mis-set knob never kills the worker
            from types import SimpleNamespace

            section = SimpleNamespace(
                enabled=True,
                backfill_max=MEMOIR_POIGNANCY_BACKFILL_MAX,
                tick_s=MEMOIR_POIGNANCY_TICK_S,
                batch_cap=MEMOIR_POIGNANCY_BATCH_CAP,
            )
    return section


def resolve_poignancy_settings(config: Any = None) -> PoignancySettings:
    """Resolve the lane's settings NOW — the ONE per-tick reader.

    The ENV tier is read LIVE (call time) and WINS over the boot-frozen
    section, so a mid-run flip of the kill-switch takes effect on the
    next tick WITHOUT a restart (AC4: "setting back to true resumes
    scoring backlog").

    THE TRUTH TABLE IS NOT COPIED HERE: the raw env values go through the
    SHARED classifiers (:func:`daemon.config._classify_enabled_flag` and
    :func:`daemon.config._classify_numeric`) — the same helpers the
    config section's own ``mode="before"`` validators use. A garbage
    value therefore CLAMPS to the constitutional default and NEVER
    raises; the daemon never aborts on a mis-set tuning knob.
    """
    from daemon.config import (
        MEMOIR_FALSY_VALUES,
        MEMOIR_POIGNANCY_ENABLED_ENV_VAR,
        MEMOIR_RESOLUTION_CONFIG,
        MEMOIR_RESOLUTION_DEFAULT,
        MEMOIR_RESOLUTION_ERROR,
        MEMOIR_RESOLUTION_EXPLICIT_OFF,
        MEMOIR_RESOLUTION_EXPLICIT_ON,
        MEMOIR_RESOLUTION_INDETERMINATE,
        MEMOIR_TRUTHY_VALUES,
        _MEMOIR_POIGNANCY_NUMERIC_SPEC,
        _classify_enabled_flag,
        _classify_numeric,
    )

    section = _poignancy_section(config)

    # ── the kill-switch (Enabled-flags class) ──────────────────────────
    raw = os.environ.get(MEMOIR_POIGNANCY_ENABLED_ENV_VAR)
    env_present = raw is not None and bool(raw.strip())
    if env_present:
        enabled = _classify_enabled_flag(
            raw, name=MEMOIR_POIGNANCY_ENABLED_ENV_VAR, default=True
        )
        lowered = raw.strip().lower()
        if lowered in MEMOIR_FALSY_VALUES:
            resolution = MEMOIR_RESOLUTION_EXPLICIT_OFF
        elif lowered in MEMOIR_TRUTHY_VALUES:
            resolution = MEMOIR_RESOLUTION_EXPLICIT_ON
        else:
            resolution = MEMOIR_RESOLUTION_INDETERMINATE
        source = "env"
        rejected = raw if resolution == MEMOIR_RESOLUTION_INDETERMINATE else None
    else:
        enabled = bool(getattr(section, "enabled", True))
        resolution = (
            MEMOIR_RESOLUTION_DEFAULT if enabled else MEMOIR_RESOLUTION_CONFIG
        )
        source = "config"
        rejected = None

    # ── the numerics (Budget-bounds / Cadence-tunables classes) ───────
    def _numeric(field: str) -> Any:
        env_name, numeric_class, floor = _MEMOIR_POIGNANCY_NUMERIC_SPEC.get(
            field, (field, "cadence", 1)
        )
        default = getattr(section, field, None)
        if default is None:
            default = {
                "backfill_max": MEMOIR_POIGNANCY_BACKFILL_MAX,
                "tick_s": MEMOIR_POIGNANCY_TICK_S,
                "batch_cap": MEMOIR_POIGNANCY_BATCH_CAP,
            }.get(field, 0)
        raw_value = os.environ.get(env_name)
        if raw_value is None or not raw_value.strip():
            return default
        return _classify_numeric(
            raw_value,
            default=default,
            numeric_class=numeric_class,
            name=env_name,
            minimum=floor,
        )

    try:
        return PoignancySettings(
            enabled=enabled,
            tick_interval_s=int(_numeric("tick_s")),
            batch_cap=int(_numeric("batch_cap")),
            backfill_max=int(_numeric("backfill_max")),
            call_timeout_s=MEMOIR_POIGNANCY_CALL_TIMEOUT_S,
            max_prompt_tokens=MEMOIR_POIGNANCY_MAX_PROMPT_TOKENS,
            resolution=resolution,
            source=source,
            rejected_value=rejected,
        )
    except Exception as exc:  # noqa: BLE001 — a knob never raises
        logger.error(
            "[MemoirPoignancy] the lane settings could not be resolved "
            "(%s) — the lane is DISABLED (fail closed)",
            type(exc).__name__,
        )
        return PoignancySettings(
            enabled=False,
            tick_interval_s=MEMOIR_POIGNANCY_TICK_S,
            batch_cap=MEMOIR_POIGNANCY_BATCH_CAP,
            backfill_max=MEMOIR_POIGNANCY_BACKFILL_MAX,
            call_timeout_s=MEMOIR_POIGNANCY_CALL_TIMEOUT_S,
            max_prompt_tokens=MEMOIR_POIGNANCY_MAX_PROMPT_TOKENS,
            resolution=MEMOIR_RESOLUTION_ERROR,
            source="none",
            rejected_value=None,
        )


# ── the outcome ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PoignancyOutcome:
    """The result of ONE scoring attempt."""

    #: ``True`` when a score was parsed and stored.
    scored: bool
    #: The clamped score (``None`` on a failure).
    score: int | None = None
    #: The failure's reason token (``None`` on a success).
    reason: str | None = None
    #: ``True`` when the failure can never succeed (401/403, a permanent
    #: status) — the row is terminal at once.
    immediate_terminal: bool = False
    #: The prompt's token cost (the metered truth of the call).
    prompt_tokens: int = 0
    #: The raw model answer (kept for the logs and the tests).
    raw: str = ""


# ── the service ──────────────────────────────────────────────────────────


class MemoirPoignancyService:
    """The scoring engine — the DECISIONS; the repository owns the SQL.

    The service is SYNC by design: it is called from the worker's
    dedicated thread, never from the event loop.

    The LLM caller is INJECTABLE (``llm_caller``) so the lane is
    testable without a provider; the default is the house failover path.
    """

    def __init__(
        self,
        *,
        repository: Any,
        config: Any = None,
        llm_config: dict[str, Any] | None = None,
        llm_caller: Callable[[str], str] | None = None,
        settings: PoignancySettings | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        #: The RAW LLM config dict — ``base_url_backup`` MUST survive in
        #: it: the HA facade reads the backup URL from the raw dict, and
        #: a dict without it can never swap.
        self._llm_config = dict(llm_config or {})
        self._llm_caller = llm_caller or self._house_llm_call
        self._settings = settings or resolve_poignancy_settings(config)

    # ── the settings (the per-tick re-read) ─────────────────────────────

    @property
    def settings(self) -> PoignancySettings:
        """The resolved settings (the worker re-resolves them per tick)."""
        return self._settings

    def apply_settings(self, settings: PoignancySettings) -> None:
        """Adopt freshly resolved settings (the per-tick re-read)."""
        self._settings = settings

    # ── the queue ────────────────────────────────────────────────────────

    def pending_episodes(
        self,
        *,
        limit: int | None = None,
        backfill_max: int | None = None,
    ) -> list[Any]:
        """The rows this tick may score (oldest first).

        The NEW rows always; the LEGACY rows only when the operator
        raised the backfill budget (V2-14 / §3-A3).
        """
        settings = self._settings
        batch = settings.batch_cap if limit is None else int(limit)
        backfill = settings.backfill_max if backfill_max is None else int(backfill_max)
        return list(
            self._repository.get_pending_poignancy_episodes(
                batch, backfill_max=backfill
            )
        )

    # ── the scoring ──────────────────────────────────────────────────────

    def score_episode(self, episode: Any) -> PoignancyOutcome:
        """Score ONE episode and write the result back.

        The write-back is the repository's ``record_poignancy_score`` —
        the statement that writes ``poignancy`` and NEVER ``importance``
        (V2-10 / §3-A2).
        """
        prompt = build_poignancy_prompt(episode)
        # APPLY THE PROMPT BUDGET (phase-4 carry: ``max_prompt_tokens`` was
        # plumbed into the settings and NEVER read — a decorative knob).
        # The clamp is the reflection lane's canonical one (single truth for
        # the "cut at a line boundary, never mid-line" rule); the body is
        # already truncated to MEMOIR_POIGNANCY_BODY_CHARS, so this is the
        # defence for unusually long titles/tags, not the primary bound.
        prompt = _clamp_prompt(prompt, int(self._settings.max_prompt_tokens))
        tokens = _estimate_tokens(prompt)
        try:
            raw = self._llm_caller(prompt)
        except Exception as exc:  # noqa: BLE001 — classified below
            reason, immediate = classify_poignancy_failure(exc)
            logger.warning(
                "[MemoirPoignancy] the scoring call failed (%s) — the "
                "episode keeps its place in the queue",
                type(exc).__name__,
            )
            return PoignancyOutcome(
                scored=False,
                reason=reason,
                immediate_terminal=immediate,
                prompt_tokens=tokens,
                raw="",
            )

        score = parse_poignancy_score(raw)
        if score is None:
            logger.warning(
                "[MemoirPoignancy] the answer carried no usable score — the "
                "episode counts an attempt"
            )
            return PoignancyOutcome(
                scored=False,
                reason="parse_failure",
                immediate_terminal=False,
                prompt_tokens=tokens,
                raw=str(raw),
            )

        stored = self._repository.record_poignancy_score(
            getattr(episode, "id", ""), score
        )
        if not stored:
            logger.warning(
                "[MemoirPoignancy] the score write-back found no row — the "
                "episode vanished before the write"
            )
            return PoignancyOutcome(
                scored=False,
                reason="poignancy_write_back_failed",
                immediate_terminal=False,
                prompt_tokens=tokens,
                raw=str(raw),
            )
        return PoignancyOutcome(
            scored=True,
            score=score,
            prompt_tokens=tokens,
            raw=str(raw),
        )

    def record_failure(self, episode_id: str, outcome: PoignancyOutcome) -> tuple[int, str | None]:
        """Record a failed scoring attempt and terminalize when due.

        Returns:
            ``(attempts, terminal_status)`` — the attempt count AFTER
            the increment and the terminal status when the row went
            terminal (else ``None``).
        """
        reason = outcome.reason or "poignancy_error"
        return self._repository.record_poignancy_attempt(
            episode_id,
            reason,
            immediate_terminal=bool(outcome.immediate_terminal),
        )

    # ── the operator recovery path (W6) ─────────────────────────────────

    def requeue_episode(
        self, episode_id: str, *, restore_legacy: bool = False
    ) -> bool:
        """Requeue ONE scoring row — THE operator recovery path.

        The lane CREATES terminal states (a dead credential, a permanent
        status, the attempt cap), so a transient outage excludes rows
        PERMANENTLY unless something puts them back. This is that
        something: it clears the terminal marker, resets the attempt
        counter and drops the stale score, so the row re-enters the
        scoring queue on the next tick.

        Args:
            episode_id: The episode to requeue.
            restore_legacy: Restore the LEGACY status (``'legacy'``)
                instead of the unscored NULL. A legacy row MUST keep the
                legacy marker — restoring NULL would put it in the
                ALWAYS-scored set and BYPASS the backfill fence
                (V2-14 / §3-A3), which is the constitutional default the
                operator raised deliberately.

        Returns:
            ``True`` when a row was requeued, ``False`` when the id
            matched no row.
        """
        return self._repository.requeue_poignancy_episode(
            episode_id, restore_legacy=restore_legacy
        )

    # ── the house LLM lane ───────────────────────────────────────────────

    def _house_llm_call(self, prompt: str) -> str:
        """The house LLM call: failover + a bounded client.

        :func:`daemon.services.llm_failover.invoke_raw_with_failover` is
        the ONLY entry point (the house pattern); the factory is
        re-entrant so every retry attempt reads the CURRENT failover
        URL.

        The client construction matches the reflection lane's CORRECTED
        one: the SDK's own retry is OFF (``max_retries=0`` — the facade
        owns the retry discipline) and the RAW dict keeps
        ``base_url_backup`` so the facade can actually swap.
        """
        from daemon.services.llm_failover import (
            current_failover_url,
            invoke_raw_with_failover,
        )

        config = self._llm_config
        model = str(config.get("model") or "gpt-4")
        api_key = str(config.get("api_key") or "")
        base_url = config.get("base_url")
        timeout_s = float(self._settings.call_timeout_s)

        def _factory() -> str:
            import openai

            from daemon.services.memoir_reflection_service import httpx_timeout

            url = current_failover_url() or base_url
            client = openai.OpenAI(
                api_key=api_key,
                base_url=url or None,
                timeout=httpx_timeout(timeout_s),
                # THE SDK'S OWN RETRY IS OFF — the house lane's rule: the
                # facade's 6-attempt ladder (3 without a backup) would
                # silently become up to 18 HTTP requests per scoring call
                # and inflate the failure window.
                max_retries=0,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("poignancy_response_empty: no choices")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if not content:
                raise ValueError("poignancy_response_empty: no content")
            return str(content)

        result = invoke_raw_with_failover(_factory, self._llm_config)
        return str(result)


def _estimate_tokens(text: str) -> int:
    """The house token estimator (``daemon/loader.py``)."""
    from daemon.loader import estimate_tokens

    return estimate_tokens(text)
