"""The memoir embedding worker — the semantic half's background writer.

Phase 3 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase3-plan.md``, D3.1-D3.16;
amendments AM-27-AM-36; flags #7 and #13; DD-6, DD-20).

THE SHAPE (all anchor refs are the phase3-plan decisions):

* D3.1  the worker is a dedicated lifespan asyncio task, stored on
  ``app.state.memoir_embedding_worker_task`` (naming precedent: the
  readiness refresher), cancelled and awaited on shutdown.
* D3.2  the queue IS ``embedding IS NULL`` (plus ``embedded_at IS NULL``
  semantics via the same column); boot catch-up is the first tick's
  rescan. No side table, no outbox.
* D3.3  per tick: scan up to ``embedding_batch_cap`` pending rows; embed
  ``title + body`` (title prepended, newline-joined) through the
  EXISTING ``openai`` SDK against the existing LLM lane — no new
  dependency, no new service (R4).
* D3.4  the embed call carries ``httpx.Timeout(timeout=30.0, connect=5.0)``
  (the house client sets none, so the SDK default is 600 s) and the
  tick's TOTAL embed budget is bounded (``TICK_EMBED_BUDGET_S``).
* D3.5  embeds run on the module-level DEDICATED executor
  (``thread_name_prefix="memoir-embed"``) — ``asyncio.to_thread`` is
  FORBIDDEN in this module (a cancelled ``to_thread`` does not free a
  shared worker, and the default pool is shared with LLM/compaction
  traffic). Embeds are SEQUENTIAL: the batch is a count, not a
  concurrency. The repository calls run on the same dedicated executor —
  the module holds NO other ``to_thread`` call site (pinned by test).
* D3.6  write-back WITH identity via ``MemoirRepository.store_embedding``
  (one atomic column-targeted UPDATE; clears ``embedding_error``).
* D3.7  ``record_embedding_attempt`` on ANY per-row failure — embed OR
  write-back; ``embedding_attempts >= 5`` is terminal.
* D3.8  a dimension mismatch is a per-row TERMINAL; nothing is stored.
* D3.9  classification reuses the HOUSE classifier's
  ``RETRYABLE_STATUS_CODES`` widened lane-locally with 408 (the house
  constant is NOT modified; membership is tested EXPLICITLY — the house
  helper alone classifies 408 as permanent). 401/403 are terminal at
  once and make the tick an all-fail tick (the latch engages on the
  first occurrence). Other statuses outside the transient set are
  permanent per row. Connection errors are transient.
* D3.10 the lane-down latch: after 3 consecutive all-fail ticks the
  worker slows to 300 s ticks until the first success. Every event has
  its own greppable log line.
* D3.11 the tick line is pinnable:
  ``[MemoirEmbed] pending=N embedded=K errors=E terminal=M tick_ms=T slow_mode=bool``
  where ``E`` counts rows that failed THIS tick and ``M`` is the TOTAL
  terminal rows in the table.
* D3.12 the knob and the numeric settings are re-read EVERY tick: an
  operator can park (``ENSEMBLE_MEMOIR_ENABLED=false``) or slow
  (``ENSEMBLE_MEMOIR_EMBEDDING_TICK_S``) the worker without a restart.
  A parked tick emits a park line (once per transition) and does no
  embedding work; the task stays alive and resumes on ON.
* D3.13 the loop body re-raises ``CancelledError`` and swallows every
  other ``Exception`` (a failed tick never kills the task); shutdown is
  cancel + await.
* D3.14 the worker's SQL is driver-agnostic — no ``tsv`` reference.
* D3.15 no automatic backfill in v1 (the operator requeue lives in
  ``docs/runbooks/memoir-embedding.md``).
* D3.16 this module is the ONLY place the embedder is reachable from
  (DD-20): the tools hold no embedder symbol.

TERMINAL RECORDING MECHANISM (mechanism note, recorded in
``phase-notes-p3.md``): the frozen wave-2 contract exposes exactly one
error-recording primitive — ``record_embedding_attempt(id, error)``,
which increments the attempt counter and writes the error string. A row
is therefore TERMINAL when

    ``embedding_attempts >= EMBEDDING_ATTEMPT_CAP``
    OR ``embedding_error`` starts with the ``terminal:`` marker,

and the worker prefixes IMMEDIATE-terminal classifications (401/403,
dims mismatch, other permanent statuses) with ``terminal:`` when it
records them. One failed attempt is recorded per failure (honest
counters); the marker makes the terminal state take effect on the NEXT
scan without burning the 5-attempt budget. The marker is the worker's
own vocabulary — the operator requeue (clear ``embedding_attempts`` and
``embedding_error``) resets it, which is exactly the runbook procedure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Sequence

import httpx
import openai

from daemon.config import MemoirConfig
from daemon.llm_error_classifier import (
    RETRYABLE_STATUS_CODES,
    classify_transient_apierror_body,
)
from daemon.repositories.memoir.repository import (
    MEMOIR_EMBEDDING_ATTEMPT_CAP,
    MEMOIR_EMBEDDING_TERMINAL_MARKER,
    MemoirRepository,
    memoir_embedding_terminal_predicate,
    memoir_row_is_terminal,
)
from daemon.tools.memoir_knob import resolve_memoir_knob

logger = logging.getLogger(__name__)

# ── AM-32: the explicit transient status set ────────────────────────────
#: The house retryable set WIDENED lane-locally with 408 (Request
#: Timeout — the server timed out waiting for the request). The house
#: constant itself is NOT modified (a house-pattern change is out of
#: scope). Membership is tested EXPLICITLY against this set — never by
#: calling ``is_retryable_status_code()`` alone, which classifies 408
#: as permanent and would burn a row's attempt budget on a transient.
MEMOIR_RETRYABLE_STATUS_CODES = frozenset(RETRYABLE_STATUS_CODES) | {408}

#: Dead-credential statuses: the row goes terminal AT ONCE (no
#: 5-attempt burn against a dead credential) and the tick counts as an
#: all-fail tick, so the lane-down latch engages on the first occurrence.
PERMANENT_CREDENTIAL_STATUS_CODES = frozenset({401, 403})

#: The error-string marker that makes a row terminal on the next scan.
#: W-2: SINGLE-SOURCED in the repository package — this alias exists so
#: the worker's vocabulary and the SQL scan window cannot drift.
TERMINAL_ERROR_MARKER = MEMOIR_EMBEDDING_TERMINAL_MARKER

# ── AM-30 / D3.7: the attempt budget ────────────────────────────────────
#: W-2: SINGLE-SOURCED in the repository package (the pending scan's
#: ``attempt_cap`` and this budget are THE SAME number by construction).
EMBEDDING_ATTEMPT_CAP = MEMOIR_EMBEDDING_ATTEMPT_CAP

# ── W-2: the stall warning ──────────────────────────────────────────────
#: The number of consecutive ``pending>0 and embedded==0`` ticks after
#: which the worker logs a LOUD stall WARNING. Silent-forever is
#: unacceptable: a starved window must be visible to the operator.
MEMOIR_EMBED_STALL_WARN_TICKS = 3

# ── D3.10: the lane-down latch ──────────────────────────────────────────
LATCH_THRESHOLD_TICKS = 3
LATCH_TICK_INTERVAL_S = 300

# ── AM-28: the request budgets ──────────────────────────────────────────
EMBED_CALL_TIMEOUT_S = 30.0
EMBED_CALL_CONNECT_TIMEOUT_S = 5.0
TICK_EMBED_BUDGET_S = 45.0

# ── D3.3: the default identity when the shared EMBEDDING_* family is
#    unset (the house embedding default — ``SkillEvolutionConfig`` uses
#    the same model id).
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# ── the scan window: the worker fetches a bounded working set each tick
#    and filters terminal rows out of it in memory (the terminal rows
#    still satisfy the repository's ``embedding IS NULL`` predicate —
#    the frozen contract has no terminal-aware scan).
SCAN_LIMIT = 200

#: The numeric knobs' env names (``MemoirConfig`` documents them; the
#: strings live here because the worker re-reads them PER TICK and
#: ``daemon/config.py`` is not this phase's file).
ENV_EMBEDDING_TICK_S = "ENSEMBLE_MEMOIR_EMBEDDING_TICK_S"
ENV_EMBEDDING_BATCH_CAP = "ENSEMBLE_MEMOIR_EMBEDDING_BATCH_CAP"
ENV_EMBEDDING_DIMENSIONS = "ENSEMBLE_MEMOIR_EMBEDDING_DIMENSIONS"

#: The shared embedding family (AM-28/D3.3): model/base/key for the
#: embed call. The base/key fall back to the LLM lane when unset.
ENV_EMBEDDING_MODEL = "EMBEDDING_MODEL"
ENV_EMBEDDING_BASE_URL = "EMBEDDING_BASE_URL"
ENV_EMBEDDING_API_KEY = "EMBEDDING_API_KEY"

#: The injectable embed seam's shape: called with the row text and the
#: resolved identity, returns the vector. The DEFAULT implementation is
#: the house-shape SDK call; the packs inject mocks here.
EmbedFn = Callable[..., Sequence[float]]


# ── the dedicated executor (AM-29) ──────────────────────────────────────
#: Module-level so the pool's lifetime is the daemon's lifetime and the
#: thread prefix is observable. max_workers=2 with SEQUENTIAL embeds: the
#: second worker only serves the repository calls that interleave with
#: an in-flight embed. NEVER the shared default executor.
_EMBED_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="memoir-embed"
)


def _default_embed(
    text: str,
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    timeout_s: float = EMBED_CALL_TIMEOUT_S,
) -> list[float]:
    """The house-shape embed — the ONLY SDK call site in the lane.

    Mirrors ``skill_embedding_service._do_embed_call`` (api_key, base_url,
    ``client.embeddings.create(model=..., input=text)``) with the ONE
    amendment AM-28 demands: an explicit ``httpx.Timeout`` (the house
    client sets none, so the SDK's 600 s default would apply and a
    slow-drip lane would stall the tick for ten minutes). The gzip
    exemption carries over: no ``http_client`` is passed, so the SDK
    builds its own ``DefaultHttpxClient`` — embedding endpoints reject
    gzip-compressed bodies.
    """
    client = openai.OpenAI(
        api_key=api_key or "",
        base_url=base_url or None,
        timeout=httpx.Timeout(timeout_s, connect=EMBED_CALL_CONNECT_TIMEOUT_S),
    )
    response = client.embeddings.create(model=model, input=text)
    data = getattr(response, "data", None) or []
    if not data:
        raise ValueError("embedding_response_empty: no data in the response")
    return [float(value) for value in data[0].embedding]


@dataclass(frozen=True)
class TickSettings:
    """One tick's resolved settings (D3.12 — re-read EVERY tick)."""

    enabled: bool
    tick_interval_s: int
    batch_cap: int
    dimensions: int
    model: str
    base_url: str | None
    api_key: str | None


def _env_int(name: str, fallback: int) -> int:
    """Read a positive-int env knob with the boot-config fallback.

    Per-tick re-read (D3.12): a present non-empty value wins over the
    boot tier; garbage or non-positive values warn and keep the fallback
    (a mis-set tuning knob must not take the worker down).
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            f"[MemoirEmbed] {name}={raw!r} is not an integer — keeping {fallback}"
        )
        return fallback
    if value <= 0:
        logger.warning(
            f"[MemoirEmbed] {name}={raw!r} is not positive — keeping {fallback}"
        )
        return fallback
    return value


def _status_code(exc: BaseException) -> int | None:
    """The HTTP status carried by an SDK/transport exception, if any."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def classify_embed_failure(exc: BaseException) -> tuple[str, bool]:
    """Classify an embed failure into ``(reason_token, is_immediate_terminal)``.

    AM-32: the HTTP-status decision tests membership in the EXPLICIT
    lane-local transient set (the house set widened with 408) — the
    house ``is_retryable_status_code`` helper alone would classify 408
    as permanent. The house classifier IS used for the exception-shape
    decision (bare ``APIError`` bodies pattern-matched by
    ``classify_transient_apierror_body``).

    Returns:
        ``(token, terminal)`` where ``terminal`` marks IMMEDIATE-terminal
        classifications (401/403, other non-transient statuses). The
        5-attempt cap terminality is decided by the caller from the
        attempt count.
    """
    status = _status_code(exc)
    if status is not None:
        if status in PERMANENT_CREDENTIAL_STATUS_CODES:
            return ("embedding_auth_rejected", True)
        if status in MEMOIR_RETRYABLE_STATUS_CODES:
            return ("embedding_status_transient", False)
        return ("embedding_status_permanent", True)
    if isinstance(exc, (openai.APIConnectionError, httpx.TransportError)):
        return ("embedding_connection_error", False)
    if isinstance(exc, openai.APIError) and classify_transient_apierror_body(exc):
        return ("embedding_api_error_body", False)
    return ("embedding_error", False)


def _is_terminal_row(row: Any, attempt_cap: int) -> bool:
    """The worker-side terminal predicate (see the module docstring).

    W-2: a THIN DELEGATE to the repository-package predicate — the SQL
    scan window and this belt cannot drift because both are the same
    function."""
    return memoir_row_is_terminal(row, attempt_cap)


class MemoirEmbeddingWorker:
    """The lifespan-owned embedding loop (D3.1-D3.16).

    Constructed by the lifespan with the shared manager engine's
    ``MemoirRepository`` and the resolved daemon ``Config``; driven by
    :meth:`run` inside ``asyncio.create_task``. Every blocking operation
    (repository reads/writes AND the embed call) runs on the dedicated
    ``memoir-embed`` executor — the event loop never blocks and the
    shared default executor is never touched.
    """

    def __init__(
        self,
        repository: MemoirRepository,
        *,
        config: Any = None,
        embedder: EmbedFn | None = None,
        scan_limit: int = SCAN_LIMIT,
        tick_embed_budget_s: float = TICK_EMBED_BUDGET_S,
        embed_call_timeout_s: float = EMBED_CALL_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._config = config
        self._embedder: EmbedFn = embedder or _default_embed
        self._scan_limit = max(1, int(scan_limit))
        self._tick_embed_budget_s = float(tick_embed_budget_s)
        self._embed_call_timeout_s = float(embed_call_timeout_s)
        self._clock = clock
        # D3.10 latch state (per-process; a restart re-evaluates).
        self._all_fail_streak = 0
        self._latched = False
        # W-2 stall state: consecutive ``pending>0, embedded==0`` ticks
        # and whether the loud WARNING has already fired for this stall.
        self._stall_streak = 0
        self._stall_warned = False
        # D3.12 park state.
        self._parked = False
        # The published snapshot (read by the readiness surface — T0.5).
        self._phase = "running"
        self._last_tick_at: str | None = None
        self._last_tick_ms: int | None = None
        self._last_tick_counts: dict[str, int] = {}
        self._last_model: str | None = None

    # ── the published state (T0.5 — advisory, cheap, no DB) ────────────

    def snapshot(self) -> dict[str, Any]:
        """The worker's park/latch/tick state for the readiness surface.

        In-memory reads only — never a DB round trip (T0.5: the
        readiness path stays side-effect free).
        """
        return {
            "phase": self._phase,
            "parked": self._parked,
            "latched": self._latched,
            "consecutive_all_fail_ticks": self._all_fail_streak,
            "last_tick_at": self._last_tick_at,
            "last_tick_ms": self._last_tick_ms,
            "last_pending": self._last_tick_counts.get("pending", 0),
            "last_embedded": self._last_tick_counts.get("embedded", 0),
            "last_errors": self._last_tick_counts.get("errors", 0),
            "last_terminal": self._last_tick_counts.get("terminal", 0),
            "embedding_model": self._last_model,
        }

    # ── the loop (D3.13) ────────────────────────────────────────────────

    async def run(self) -> None:
        """The lifespan task body: tick, sleep, repeat; cancel-safe.

        House contract (``daemon/api.py`` readiness refresher): a
        ``CancelledError`` propagates; any other per-tick failure is
        logged and the NEXT tick still runs.
        """
        while True:
            try:
                interval = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad tick is not fatal
                logger.error(f"[MemoirEmbed] tick failed: {exc}", exc_info=True)
                interval = await self._fallback_interval()
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

    async def _fallback_interval(self) -> int:
        """The sleep interval after a failed tick (settings stay honest)."""
        settings = self._resolve_settings()
        return settings.tick_interval_s

    # ── one tick ────────────────────────────────────────────────────────

    async def _tick(self) -> int:
        """Run one worker tick; return the sleep interval for the next.

        Returns the interval, not a verdict — the latch decision and the
        knob re-read both happen INSIDE the tick so the next sleep honours
        them (D3.10/D3.12).
        """
        settings = self._resolve_settings()

        # ── D3.12: the knob gates every tick ────────────────────────────
        if not settings.enabled:
            if not self._parked:
                self._parked = True
                self._phase = "parked"
                logger.info(
                    "[MemoirEmbed] parked: memoir knob is OFF — the worker "
                    "stays alive and resumes when the knob returns ON"
                )
            return settings.tick_interval_s
        if self._parked:
            self._parked = False
            self._phase = "running"
            logger.info(
                "[MemoirEmbed] resumed: memoir knob is ON — pending rows "
                "embed on the following ticks"
            )

        loop = asyncio.get_running_loop()
        started = self._clock()

        # ── the queue census (driver-agnostic reads) ────────────────────
        # ``pending`` is measured at tick START — it is the queue this
        # tick considered. ``terminal`` is measured AFTER the tick's
        # work: it is the STANDING backlog (D3.11 — the operator slice
        # that says how much needs a requeue or an acceptance).
        pending_total = await loop.run_in_executor(
            _EMBED_EXECUTOR, self._count_pending
        )

        # ── the scan window + the terminal filter (D3.2/D3.7) ───────────
        # W-2: the REPOSITORY's pending query is terminal-aware — the
        # oldest-N window ADVANCES past the rows this predicate would
        # exclude, so pending rows BEHIND a head of terminal rows are
        # reachable. The Python filter stays as the belt.
        scan = self._repository.get_pending_embeddings
        rows = await loop.run_in_executor(
            _EMBED_EXECUTOR,
            lambda: scan(None, self._scan_limit),
        )
        candidates = [
            row
            for row in rows
            if not _is_terminal_row(row, EMBEDDING_ATTEMPT_CAP)
        ][: settings.batch_cap]

        embedded = 0
        errors = 0
        credential_rejected = False
        budget_exhausted_deferred = 0

        for row in candidates:
            elapsed = self._clock() - started
            remaining = self._tick_embed_budget_s - elapsed
            if remaining <= 0:
                budget_exhausted_deferred = len(candidates) - embedded - errors
                if budget_exhausted_deferred > 0:
                    logger.info(
                        "[MemoirEmbed] tick embed budget exhausted "
                        f"({self._tick_embed_budget_s:.0f}s) — deferring "
                        f"{budget_exhausted_deferred} row(s) to the next tick"
                    )
                break
            outcome = await self._process_row(row, settings, remaining)
            if outcome == "embedded":
                embedded += 1
            elif outcome is not None:
                errors += 1
                if outcome == "credential_rejected":
                    credential_rejected = True

        # ── D3.10: the latch assessment ─────────────────────────────────
        if embedded > 0:
            if self._latched:
                self._latched = False
                logger.info(
                    "[MemoirEmbed] latch released: embed succeeded — "
                    "returning to the configured tick interval"
                )
            self._all_fail_streak = 0
        elif errors > 0:
            if credential_rejected:
                # D3.9: a dead credential is lane-down at the first
                # occurrence — the streak jumps to the threshold.
                self._all_fail_streak = LATCH_THRESHOLD_TICKS
            else:
                self._all_fail_streak += 1
            if self._all_fail_streak >= LATCH_THRESHOLD_TICKS and not self._latched:
                self._latched = True
                logger.warning(
                    "[MemoirEmbed] latch engaged: "
                    f"{self._all_fail_streak} consecutive all-fail ticks — "
                    f"slowing to {LATCH_TICK_INTERVAL_S}s ticks until the "
                    "first success"
                )
        # errors == 0 and embedded == 0: a neutral tick (nothing to do)
        # — the streak and the latch hold their state.

        tick_ms = int(round((self._clock() - started) * 1000))
        # Re-count the standing terminal backlog AFTER this tick's work so
        # the tick line's ``M`` reflects the rows that are terminal NOW
        # (D3.11 — the operator slice).
        terminal_total = await loop.run_in_executor(
            _EMBED_EXECUTOR, self._count_terminal
        )

        # ── W-2: the stall assessment (the starved-window WARNING) ──────
        # ``pending > 0`` while ``embedded == 0`` tick after tick means
        # the window is starved — by terminal rows the scan could not
        # advance past (pre-W-2), by a perpetual budget exhaustion, or
        # by anything else. Silent-forever is unacceptable: after N
        # consecutive starved ticks the worker names the pending count,
        # the scan window and the terminal-row count OUT LOUD.
        if pending_total > 0 and embedded == 0:
            self._stall_streak += 1
            if (
                self._stall_streak >= MEMOIR_EMBED_STALL_WARN_TICKS
                and not self._stall_warned
            ):
                self._stall_warned = True
                logger.warning(
                    "[MemoirEmbed] STALL: pending=%d but embedded=0 for %d "
                    "consecutive ticks — the scan window (limit=%d) may be "
                    "blocked by terminal rows (terminal=%d); pending rows "
                    "behind them are starved",
                    pending_total,
                    self._stall_streak,
                    self._scan_limit,
                    terminal_total,
                )
        else:
            # A healthy tick (progress made, or nothing pending) clears
            # the streak and re-arms the warning for the NEXT stall.
            self._stall_streak = 0
            self._stall_warned = False

        self._publish_tick(
            pending=pending_total,
            embedded=embedded,
            errors=errors,
            terminal=terminal_total,
            tick_ms=tick_ms,
            model=settings.model,
        )
        logger.info(
            f"[MemoirEmbed] pending={pending_total} embedded={embedded} "
            f"errors={errors} terminal={terminal_total} "
            f"tick_ms={tick_ms} slow_mode={self._latched}"
        )

        if self._latched:
            return LATCH_TICK_INTERVAL_S
        return settings.tick_interval_s

    # ── per-row pipeline (D3.3-D3.9) ────────────────────────────────────

    async def _process_row(
        self, row: Any, settings: TickSettings, remaining_s: float
    ) -> str | None:
        """Embed + write back ONE row.

        ``remaining_s`` is the tick's REMAINING embed budget (the tick
        loop computes it before dispatching the row — AM-28 bounds the
        tick's TOTAL embed time at ``TICK_EMBED_BUDGET_S``).

        Returns ``"embedded"`` on success, a failure token on a recorded
        failure (``"credential_rejected"`` for the 401/403 latch arm),
        or ``None`` when the row needs no outcome (impossible in v1 —
        kept for exhaustiveness).
        """
        loop = asyncio.get_running_loop()
        title = getattr(row, "title", None)
        body = getattr(row, "body", "") or ""
        text = f"{title}\n{body}" if title else body

        try:
            # run_in_executor passes only positional args — the identity
            # (model/base/key/timeout) binds through the closure.
            future = loop.run_in_executor(
                _EMBED_EXECUTOR,
                lambda: self._embedder(
                    text,
                    model=settings.model,
                    base_url=settings.base_url,
                    api_key=settings.api_key,
                    timeout_s=self._embed_call_timeout_s,
                ),
            )
            vector = await asyncio.wait_for(future, timeout=max(0.05, remaining_s))
        except asyncio.TimeoutError:
            return await self._record_failure(
                row,
                "embedding_call_timeout "
                f"(tick budget {self._tick_embed_budget_s:.0f}s exceeded)",
                immediate_terminal=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — classified below
            token, immediate = classify_embed_failure(exc)
            return await self._record_failure(
                row,
                f"{token}: {exc}",
                immediate_terminal=immediate,
                credential=(
                    token == "embedding_auth_rejected"
                ),
            )

        # ── D3.8: the dimension gate — nothing is stored on mismatch ────
        vector = list(vector)
        if len(vector) != settings.dimensions:
            return await self._record_failure(
                row,
                "terminal:embedding_dimensions_mismatch "
                f"expected={settings.dimensions} got={len(vector)}",
                immediate_terminal=True,
                already_marked=True,
            )

        # ── D3.6: the identity write-back ───────────────────────────────
        try:
            stored = await loop.run_in_executor(
                _EMBED_EXECUTOR,
                lambda: self._repository.store_embedding(
                    row.id,
                    vector,
                    settings.model,
                    settings.dimensions,
                    datetime.now(timezone.utc),
                ),
            )
            if not stored:
                # The row vanished between scan and write (memoir rows
                # are append-only, so this is defensive) — record it.
                return await self._record_failure(
                    row,
                    "embedding_write_back_failed: row vanished before the "
                    "write-back (rowcount=0)",
                    immediate_terminal=False,
                )
            return "embedded"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — AM-30: the write-back counts
            return await self._record_failure(
                row,
                f"embedding_write_back_failed: {exc}",
                immediate_terminal=False,
            )

    async def _record_failure(
        self,
        row: Any,
        message: str,
        *,
        immediate_terminal: bool,
        credential: bool = False,
        already_marked: bool = False,
    ) -> str:
        """Record ONE failed attempt (D3.7) and classify the outcome.

        Returns the outcome token the tick bookkeeping consumes:
        ``"credential_rejected"`` arms the latch at the first
        occurrence; any other non-None token counts as a plain error.
        """
        loop = asyncio.get_running_loop()
        error_text = message if already_marked else self._mark(message, immediate_terminal)
        try:
            attempts = await loop.run_in_executor(
                _EMBED_EXECUTOR,
                self._repository.record_embedding_attempt,
                row.id,
                error_text[:500],
            )
        except asyncio.CancelledError:
            raise
        except Exception as record_exc:  # noqa: BLE001 — the DB itself is down
            # The attempt record failed — re-raise so the tick-level
            # guard logs the bad tick and the next tick rescans.
            raise RuntimeError(
                f"embedding attempt record failed for {row.id}: {record_exc}"
            ) from record_exc

        became_terminal = immediate_terminal or attempts >= EMBEDDING_ATTEMPT_CAP
        if became_terminal:
            logger.warning(
                f"[MemoirEmbed] terminal episode={row.id} "
                f"reason={self._reason_token(error_text)} "
                f"attempts={attempts} error={error_text[:200]}"
            )
        if message.startswith("embedding_write_back_failed"):
            logger.warning(
                f"[MemoirEmbed] write-back failed episode={row.id} — "
                f"recorded as an attempt ({attempts}): {message[:200]}"
            )
        if credential:
            return "credential_rejected"
        return error_text.split(":", 1)[0] or "embedding_error"

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _mark(message: str, immediate_terminal: bool) -> str:
        if immediate_terminal and not message.startswith(TERMINAL_ERROR_MARKER):
            return f"{TERMINAL_ERROR_MARKER}{message}"
        return message

    @staticmethod
    def _reason_token(error_text: str) -> str:
        stripped = error_text.removeprefix(TERMINAL_ERROR_MARKER)
        return stripped.split(":", 1)[0].split(" ", 1)[0] or "unknown"

    def _resolve_settings(self) -> TickSettings:
        """D3.12 — the knob AND the numeric settings, re-read every tick.

        Precedence per field: a present non-empty env var wins over the
        boot config tier (the operator's live lever); the boot tier (the
        lifespan's resolved ``Config``, which already merged env > yaml
        at boot) is the fallback. A garbage env value warns and keeps
        the fallback — a mis-typed tuning knob never takes the worker
        down.
        """
        knob = resolve_memoir_knob(self._config)
        memoir_cfg = getattr(self._config, "memoir", None)
        if memoir_cfg is None:
            try:
                memoir_cfg = MemoirConfig()
            except Exception:  # noqa: BLE001 — a garbage numeric env would
                # make the whole model unparseable; fall back to the
                # class defaults so a mis-typed tuning knob never takes
                # the worker down (D3.12).
                memoir_cfg = SimpleNamespace(
                    embedding_tick_s=MemoirConfig.model_fields["embedding_tick_s"].default,
                    embedding_batch_cap=MemoirConfig.model_fields["embedding_batch_cap"].default,
                    embedding_dimensions=MemoirConfig.model_fields["embedding_dimensions"].default,
                )
        llm_cfg = getattr(self._config, "llm", None)
        return TickSettings(
            enabled=bool(knob.enabled),
            tick_interval_s=_env_int(
                ENV_EMBEDDING_TICK_S, int(memoir_cfg.embedding_tick_s)
            ),
            batch_cap=_env_int(
                ENV_EMBEDDING_BATCH_CAP, int(memoir_cfg.embedding_batch_cap)
            ),
            dimensions=_env_int(
                ENV_EMBEDDING_DIMENSIONS, int(memoir_cfg.embedding_dimensions)
            ),
            model=(os.environ.get(ENV_EMBEDDING_MODEL, "").strip()
                   or DEFAULT_EMBEDDING_MODEL),
            base_url=(os.environ.get(ENV_EMBEDDING_BASE_URL, "").strip()
                      or getattr(llm_cfg, "base_url", None)),
            api_key=(os.environ.get(ENV_EMBEDDING_API_KEY, "").strip()
                     or getattr(llm_cfg, "api_key", None)),
        )

    # ── the driver-agnostic counts (D3.14) ──────────────────────────────

    def _count_pending(self) -> int:
        """Pending = ``embedding IS NULL`` rows that are NOT terminal."""
        from sqlmodel import Session, func, select

        from daemon.repositories.memoir.models import MemoirEpisode

        statement = select(func.count()).select_from(MemoirEpisode).where(
            MemoirEpisode.embedding.is_(None)
        )
        with Session(self._repository.engine) as session:
            total = int(session.exec(statement).one() or 0)
        return max(0, total - self._count_terminal())

    def _count_terminal(self) -> int:
        """M — the TOTAL terminal rows in the table (D3.11).

        Terminal = still missing its embedding AND (attempts at/over the
        cap OR the ``terminal:`` marker present). W-2: the predicate is
        SINGLE-SOURCED — this SELECT and the pending scan's exclusion
        are the same :func:`memoir_embedding_terminal_predicate`.
        Driver-agnostic: no ``tsv`` reference; ``LIKE`` works on SQLite
        and PG alike.
        """
        from sqlmodel import Session, func, select

        from daemon.repositories.memoir.models import MemoirEpisode

        statement = (
            select(func.count())
            .select_from(MemoirEpisode)
            .where(
                MemoirEpisode.embedding.is_(None),
                memoir_embedding_terminal_predicate(EMBEDDING_ATTEMPT_CAP),
            )
        )
        with Session(self._repository.engine) as session:
            return int(session.exec(statement).one() or 0)

    def _publish_tick(
        self,
        *,
        pending: int,
        embedded: int,
        errors: int,
        terminal: int,
        tick_ms: int,
        model: str,
    ) -> None:
        self._last_tick_at = datetime.now(timezone.utc).isoformat()
        self._last_tick_ms = tick_ms
        self._last_tick_counts = {
            "pending": pending,
            "embedded": embedded,
            "errors": errors,
            "terminal": terminal,
        }
        self._last_model = model


# ── the module-level snapshot accessor (T0.5) ───────────────────────────
#: The most recently constructed worker in this process. The readiness
#: surface reads THIS (never a DB, never app.state) so the advisory
#: block is available even when the lifespan did not run (probe apps).
_LAST_WORKER: MemoirEmbeddingWorker | None = None


def register_worker(worker: MemoirEmbeddingWorker) -> None:
    """Record the process's live worker (called by the lifespan)."""
    global _LAST_WORKER
    _LAST_WORKER = worker


def latest_snapshot() -> dict[str, Any]:
    """The worker's state, or the honest ``not_started`` block."""
    if _LAST_WORKER is None:
        return {
            "phase": "not_started",
            "parked": None,
            "latched": None,
            "consecutive_all_fail_ticks": 0,
            "last_tick_at": None,
            "last_tick_ms": None,
            "last_pending": 0,
            "last_embedded": 0,
            "last_errors": 0,
            "last_terminal": 0,
            "embedding_model": None,
        }
    return _LAST_WORKER.snapshot()


def executor_thread_prefix() -> str:
    """The dedicated executor's thread prefix (the AM-29 pin reads this)."""
    return "memoir-embed"


def run_embed_on_executor(fn: Callable[[], Any]) -> Any:
    """Test/support helper: run ONE callable on the dedicated executor.

    Exposed so the packs can prove the executor's thread prefix without
    constructing a full worker.
    """
    return _EMBED_EXECUTOR.submit(fn).result()


def current_executor_thread_name() -> str:
    """The executor thread's name as seen from INSIDE the executor."""
    return _EMBED_EXECUTOR.submit(threading.current_thread).result().name
