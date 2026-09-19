"""Tmp-image retention sweep — phase 3 of clipboard-image-chat.

Periodic background service that reaps ``data/tmp_images/`` entries
older than the configured retention window (default **30 days**),
modeled on the proven ``JobLockSweepService`` pattern
(``daemon/services/job_lock_sweep.py``) — the ``__init__`` /
``start()`` / ``stop()`` / ``sweep_once()`` / ``_run()`` shapes are
reused verbatim (interval clamp, ``_task``/``_stopping`` flags,
idempotent start, graceful cancel+await, defensive ``except
Exception`` → WARNING + return 0).

**ALWAYS ON** (no kill-switch). Per the project owner's HARD POLICY
codified in ``job_lock_sweep.py`` (architect amendment #15): a
kill-switch defaulting OFF that gates cleanup is an unacceptable
deliverable — the unique failure mode is silent permanent storage
growth when flipped by accident. The ONLY knobs are the interval
(``ServicesConfig.tmp_image_cleanup_interval_seconds``, pinned
hourly) and the retention window
(``ServicesConfig.tmp_image_cleanup_retention_days``, default 30d) —
the retention knob IS the operator lever (set it very large to
effectively disable reaping without removing the service). The
``enabled`` constructor kwarg is an INTERNAL TEST SEAM ONLY: no
production path passes ``False`` (the boot anchor hardcodes
``enabled=True``) and there is deliberately NO "DISABLED" log branch.

**Age source (dispatcher refinement, supersedes the plan's
mtime-only wording).** For each blob the sweep resolves age from the
sidecar's ``uploaded_at`` timestamp (written by phase 1 via
``now_utc_iso()`` — an aware UTC ISO-8601 string) when the sidecar
exists and parses; otherwise it falls back to that file's own POSIX
mtime. Both shapes are compared as absolute epoch seconds against
``now - retention_days`` — no naive/aware ``datetime`` arithmetic
anywhere (naive-UTC discipline per ``daemon/services/timestamps.py``;
a hypothetical naive ``uploaded_at`` is attached UTC under the
documented assume-UTC policy via ``coerce_to_aware_utc``).

**Pair semantics + idempotent unlink.** A blob and its ``<id>.json``
sidecar are deleted as a PAIR. ``FileNotFoundError`` is swallowed
silently — the FE DELETE endpoint racing this sweep is EXPECTED
traffic (architect §7) and a double-delete is benign. Orphans (a
sidecar whose blob is already gone, or a blob whose sidecar never
landed) are aged by their OWN mtime and reaped the same way.

Image loss is acceptable: the FE phase 6 ships an ``onerror``
placeholder for 404s on cleaned-up files (text descriptions persist
via phase 2's conversion).

Self-test seam: tests call ``sweep_once()`` directly to exercise a
single deterministic tick without spawning the asyncio task (mirrors
``JobLockSweepService.sweep_once``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from daemon.services.timestamps import coerce_to_aware_utc, now_utc, now_utc_iso
from daemon.services.tmp_image_store import _METADATA_SUFFIX

if TYPE_CHECKING:
    from daemon.services.tmp_image_store import TmpImageStore

logger = logging.getLogger(__name__)


# Default cadence — PINNED hourly (architect §7; plan draft said
# 86400). Hourly keeps deletion latency ≤ retention + interval while
# the scan stays a cheap filesystem stat-walk (no DB).
DEFAULT_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS: int = 3600

# Default retention window (days) — the plan's objective value. The
# config knob (``ServicesConfig.tmp_image_cleanup_retention_days``)
# is the operator lever; this constant only backs the constructor
# default.
DEFAULT_TMP_IMAGE_CLEANUP_RETENTION_DAYS: int = 30

# Reap-log sample cap — bounded so a mass-reap tick (e.g. first sweep
# after a long downtime) cannot flood the log line with ids.
_REAP_SAMPLE_IDS_MAX = 5

_SECONDS_PER_DAY = 86400


class TmpImageCleanupService:
    """Periodic tmp-image retention sweep (phase 3, always-on).

    Each tick walks the store directory, resolves each entry's age
    (sidecar ``uploaded_at`` → else file mtime), and unlinks entries
    older than ``now - retention_days`` as a blob+sidecar pair
    (idempotent — missing files are not errors). Orphans are aged by
    their own mtime and reaped the same way.

    Args:
        tmp_image_store: The phase-1 ``TmpImageStore`` whose
            directory this service sweeps. The service consumes the
            store's dir + sidecar layout (extensionless blobs +
            ``<id>.json`` sidecars); it does NOT touch the store's
            byte cap or its read paths.
        enabled: INTERNAL TEST SEAM ONLY (architect amendment #15).
            ``False`` makes ``start()`` a silent no-op so unit tests
            can exercise the constructor + ``sweep_once()`` without
            spawning the asyncio task. NO production path passes
            ``False`` — the boot anchor hardcodes ``enabled=True`` —
            and there is no "DISABLED" log branch.
        interval_seconds: How often the sweep runs. Default 3600s
            (pinned hourly). Floor 1 to avoid spin; out-of-range
            config values FAIL FAST AT BOOT via the pydantic
            ``Field(ge=1)`` constraint; this constructor additionally
            clamps via ``max(1, int(...))`` (mirrors
            ``JobLockSweepService.__init__``).
        retention_days: Age threshold for reaping. Default 30 days.
            An entry is reaped when its age is STRICTLY older than
            ``now - retention_days`` (exactly-at-cutoff is retained —
            pinned by test).

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent; first
          tick runs IMMEDIATELY (sweep before the first sleep).
        * ``stop()`` — cancel + await the asyncio task. Safe to call
          when the task was never started (silent no-op).
    """

    def __init__(
        self,
        tmp_image_store: "TmpImageStore",
        *,
        enabled: bool = True,
        interval_seconds: int = DEFAULT_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS,
        retention_days: int = DEFAULT_TMP_IMAGE_CLEANUP_RETENTION_DAYS,
    ) -> None:
        self._store = tmp_image_store
        self._enabled = enabled
        self._interval_seconds = max(1, int(interval_seconds))
        self._retention_days = max(1, int(retention_days))
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False
        # Per-sweep observability state, surfaced via the gated
        # debug-listing health endpoint (``GET /api/tmp_images`` →
        # ``cleanup`` field). Updated after EVERY ``sweep_once``.
        self._last_sweep_at: str | None = None
        self._last_sweep_deleted: int = 0
        self._last_sweep_error: str | None = None
        # Bounded sample of ids reaped by the most recent tick (log
        # + potential future diagnostics). Populated by each
        # ``_sweep_store_dir`` pass.
        self._last_reap_sample_ids: list[str] = []
        self._last_reap_freed_bytes: int = 0

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def interval_seconds(self) -> int:
        """Current sweep interval (seconds). Read-only."""
        return self._interval_seconds

    @property
    def retention_days(self) -> int:
        """Current retention window (days). Read-only."""
        return self._retention_days

    @property
    def last_sweep_at(self) -> str | None:
        """ISO-8601 UTC timestamp of the last completed tick, or None."""
        return self._last_sweep_at

    @property
    def last_sweep_deleted(self) -> int:
        """Entries reaped by the last completed tick."""
        return self._last_sweep_deleted

    @property
    def last_sweep_error(self) -> str | None:
        """Error text from the last failed tick, or None when healthy."""
        return self._last_sweep_error

    # ------------------------------------------------------------------
    # Lifecycle — mirrors JobLockSweepService verbatim
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — a second call while a task is already running
        is a no-op (matches the ``JobLockSweepService`` pattern).
        The FIRST tick runs immediately: ``_run`` calls
        ``sweep_once()`` BEFORE the first ``asyncio.sleep``, so the
        boot-time sweep is satisfied by the immediate-first-tick
        design (no separate synchronous pre-tick is wired).

        When ``enabled=False`` (INTERNAL TEST SEAM ONLY — no
        production path) this returns without spawning the task and
        without logging any "DISABLED" line (that branch was deleted
        per architect amendment #15).
        """
        if not self._enabled:
            return
        if self._task is not None and not self._task.done():
            logger.debug(
                f"TmpImageCleanupService: start() called while "
                f"already running — no-op"
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name="TmpImageCleanupService"
        )
        logger.info(
            f"TmpImageCleanupService started: interval="
            f"{self._interval_seconds}s retention="
            f"{self._retention_days}d"
        )

    async def stop(self) -> None:
        """Cancel the sweep task and await its cancellation.

        Safe to call when the task was never started — silent
        no-op (matches ``JobLockSweepService.stop``).
        """
        if self._task is None:
            return
        self._stopping = True
        task = self._task
        self._task = None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            # CancelledError is the normal shutdown path; Exception
            # is a defensive catch in case the task raised something
            # unexpected during shutdown. Either way the sweep is
            # stopped — no re-raise.
            pass
        logger.info("TmpImageCleanupService stopped")

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    async def sweep_once(self) -> int:
        """Run a single retention tick. Returns the reaped-entry count.

        Public deterministic seam so tests can exercise a single tick
        without spawning the asyncio task (mirrors
        ``JobLockSweepService.sweep_once``). The daemon lifespan
        starts the periodic loop exclusively via ``start()``; the
        first reclaim tick runs immediately inside ``_run`` (sweep
        before first sleep).

        Count semantics: one entry per reaped IMAGE id — a
        blob+sidecar pair counts 1, an orphan sidecar (or orphan
        blob) counts 1. Files that vanished mid-tick (FE DELETE ∥
        sweep race) are excluded from the count — nothing was
        removed. A per-file ``OSError`` (EBUSY/EACCES/read-only) is
        skipped with a WARNING and excluded from the count; the next
        tick retries.
        """
        try:
            sweep_started = time.monotonic()
            deleted = self._sweep_store_dir()
            sweep_duration_s = time.monotonic() - sweep_started
        except FileNotFoundError:
            # Missing store dir — the sweep has nothing to walk.
            # WARNING + 0 (not a crash; the store ``init()``s at
            # boot so production only hits this in tests).
            self._last_sweep_at = now_utc_iso()
            self._last_sweep_deleted = 0
            self._last_sweep_error = "store directory missing"
            logger.warning(
                f"[TmpImages] cleanup sweep skipped: store dir "
                f"missing ({self._store.dir}) — next tick will retry"
            )
            return 0
        except Exception as sweep_err:  # noqa: BLE001
            # Defensive: a transient filesystem error must not crash
            # the sweep loop. Log and return 0 — the next tick will
            # retry. Mirrors the ``JobLockSweepService.sweep_once``
            # fail-soft pattern.
            self._last_sweep_at = now_utc_iso()
            self._last_sweep_deleted = 0
            self._last_sweep_error = (
                f"{type(sweep_err).__name__}: {sweep_err}"
            )
            logger.warning(
                f"[TmpImages] cleanup sweep failed: "
                f"{type(sweep_err).__name__}: {sweep_err} — "
                "next tick will retry",
                exc_info=True,
            )
            return 0
        # Healthy tick — clear any stale error from a previous tick.
        self._last_sweep_at = now_utc_iso()
        self._last_sweep_deleted = deleted
        self._last_sweep_error = None
        if deleted:
            sample_ids = ", ".join(self._last_reap_sample_ids)
            logger.info(
                f"[TmpImages] reaped {deleted} image(s) older than "
                f"{self._retention_days}d: {sample_ids}"
            )
            # Phase-1+3 review S6 — one compact, self-contained summary
            # line per REAPING tick (count + freed bytes + duration).
            # No-op ticks stay silent (pinned log-noise discipline), so
            # the hourly cadence cannot spam the log.
            logger.info(
                f"[TmpImages] reap tick summary: deleted={deleted} "
                f"freed_bytes={self._last_reap_freed_bytes} "
                f"duration_s={sweep_duration_s:.3f}"
            )
        return deleted

    async def _run(self) -> None:
        """Periodic tick loop — exits on ``stop()`` cancellation.

        FIRST TICK IMMEDIATE: ``sweep_once()`` runs BEFORE the first
        ``asyncio.sleep`` (boot-time sweep is satisfied by this — no
        separate synchronous pre-tick). ``asyncio.sleep`` raises
        ``CancelledError`` promptly when ``stop()`` invites the task
        via ``task.cancel()``; the ``_stopping`` flag is only a
        defensive guard for the path between the ``sweep_once``
        return and the next sleep, not the primary exit mechanism.
        """
        try:
            while not self._stopping:
                await self.sweep_once()
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            # Normal shutdown path via stop() — exit cleanly.
            return
        except Exception as loop_err:  # noqa: BLE001
            # Defensive: an unexpected loop error must not crash
            # the daemon. Log loudly and exit. The next start()
            # (if any) will re-spawn the loop.
            logger.error(
                f"TmpImageCleanupService: unexpected loop error: "
                f"{type(loop_err).__name__}: {loop_err} — exiting",
                exc_info=True,
            )
            return

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sweep_store_dir(self) -> int:
        """Walk + reap. Sync body of ``sweep_once`` (raises per contract).

        Raises ``FileNotFoundError`` when the store dir itself is
        missing (the ``os.scandir`` in
        ``TmpImageStore.list_ids_with_mtime`` surfaces it via the
        ``exists()`` guard — this explicit check keeps the semantics
        obvious and covers the removed-mid-call window).

        Side effects (mirrors ``_last_reap_sample_ids``): resets and
        repopulates ``_last_reap_freed_bytes`` — the byte total of
        everything actually reaped this tick (S6 summary line).
        """
        store_dir = self._store.dir
        if not store_dir.exists():
            raise FileNotFoundError(str(store_dir))

        cutoff = now_utc().timestamp() - (
            self._retention_days * _SECONDS_PER_DAY
        )
        names_mtime: dict[str, float] = dict(
            self._store.list_ids_with_mtime()
        )
        blob_names = [
            n for n in names_mtime if not n.endswith(_METADATA_SUFFIX)
        ]
        deleted = 0
        freed = 0
        self._last_reap_sample_ids: list[str] = []
        self._last_reap_freed_bytes = 0

        # Pass 1 — blobs (each with its sidecar pair, present or not).
        for blob_name in blob_names:
            image_id = blob_name
            blob_mtime = names_mtime[blob_name]
            sidecar_name = f"{image_id}{_METADATA_SUFFIX}"
            sidecar_mtime = names_mtime.get(sidecar_name)
            age_ts = self._resolve_age_seconds(
                image_id,
                blob_mtime=blob_mtime,
                sidecar_name=sidecar_name,
                sidecar_present=sidecar_name in names_mtime,
                fallback_mtime=blob_mtime,
            )
            if age_ts is None or age_ts >= cutoff:
                continue
            pair_bytes = self._stat_entry_bytes(
                image_id
            ) + self._stat_entry_bytes(f"{image_id}{_METADATA_SUFFIX}")
            if self._reap_pair(image_id):
                deleted += 1
                freed += pair_bytes
                if len(self._last_reap_sample_ids) < _REAP_SAMPLE_IDS_MAX:
                    self._last_reap_sample_ids.append(image_id)

        # Pass 2 — orphan sidecars (blob already gone). Aged by the
        # sidecar's OWN mtime; reaped the same idempotent way.
        for sidecar_name in names_mtime:
            if not sidecar_name.endswith(_METADATA_SUFFIX):
                continue
            image_id = sidecar_name[: -len(_METADATA_SUFFIX)]
            if image_id in names_mtime:
                # Blob still present — the pair was handled in pass 1.
                continue
            age_ts = self._resolve_age_seconds(
                image_id,
                blob_mtime=None,
                sidecar_name=sidecar_name,
                sidecar_present=True,
                fallback_mtime=names_mtime[sidecar_name],
            )
            if age_ts is None or age_ts >= cutoff:
                continue
            orphan_bytes = self._stat_entry_bytes(sidecar_name)
            if self._unlink_quietly(store_dir / sidecar_name):
                deleted += 1
                freed += orphan_bytes
                if len(self._last_reap_sample_ids) < _REAP_SAMPLE_IDS_MAX:
                    self._last_reap_sample_ids.append(image_id)
        self._last_reap_freed_bytes = freed
        return deleted

    def _stat_entry_bytes(self, name: str) -> int:
        """Best-effort byte size of one store entry (0 on race/IO error).

        Feeds the S6 summary line. A file that vanishes between the
        mtime walk and this stat (FE DELETE ∥ sweep race = expected
        traffic) contributes 0 rather than aborting the tick.
        """
        try:
            return (self._store.dir / name).stat().st_size
        except OSError:
            return 0

    def _resolve_age_seconds(
        self,
        image_id: str,
        *,
        blob_mtime: float | None,
        sidecar_name: str,
        sidecar_present: bool,
        fallback_mtime: float,
    ) -> float | None:
        """Resolve an entry's age as epoch seconds (None → skip entry).

        Precedence (dispatcher refinement): the sidecar's
        ``uploaded_at`` timestamp when the sidecar exists AND parses;
        otherwise the file's own mtime. A per-file ``OSError`` while
        stat-ing/logging the entry is downgraded to a WARNING + skip
        (None) so one locked file cannot abort the whole tick — the
        count excludes it and the next tick retries.
        """
        try:
            if sidecar_present and blob_mtime is not None:
                uploaded_ts = self._sidecar_uploaded_epoch(
                    sidecar_name
                )
                if uploaded_ts is not None:
                    return uploaded_ts
            return fallback_mtime
        except OSError as stat_err:
            logger.warning(
                f"[TmpImages] cleanup skipped unreadable entry "
                f"{image_id}: {stat_err} — next tick will retry"
            )
            return None

    def _sidecar_uploaded_epoch(self, sidecar_name: str) -> float | None:
        """Parse the sidecar's ``uploaded_at`` into epoch seconds.

        Returns None when the sidecar is unreadable, invalid JSON,
        missing the key, or carries an unparseable timestamp — the
        caller falls back to the file mtime. Phase 1 wrote the value
        via ``now_utc_iso()`` (aware UTC ISO-8601, ``+00:00``);
        ``datetime.fromisoformat`` handles that natively. A naive
        value (never produced by phase 1, but defensive) is attached
        UTC under the documented assume-UTC policy
        (``coerce_to_aware_utc``) — no naive/aware comparison ever
        happens.
        """
        image_id = sidecar_name[: -len(_METADATA_SUFFIX)]
        meta_path = self._store.dir / sidecar_name
        try:
            raw = meta_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            meta = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(meta, dict):
            return None
        uploaded_raw = meta.get("uploaded_at")
        if not isinstance(uploaded_raw, str) or not uploaded_raw:
            return None
        try:
            parsed = coerce_to_aware_utc(
                datetime.fromisoformat(uploaded_raw)
            )
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        return parsed.timestamp()

    def _reap_pair(self, image_id: str) -> bool:
        """Delete a blob + sidecar pair, idempotently.

        Delegates to ``TmpImageStore.delete`` — it unlinks both paths
        and swallows ``FileNotFoundError`` (the FE DELETE ∥ sweep
        race is expected traffic). Returns True when at least one
        file was actually removed (a fully-raced delete counts as
        nothing reaped). A per-file ``OSError`` (EBUSY/EACCES/
        read-only) is downgraded to WARNING + skip; the count
        excludes it and the next tick retries.
        """
        try:
            return self._store.delete(image_id)
        except FileNotFoundError:
            # Lost a race with the FE DELETE endpoint — expected
            # traffic, silent per architect §7.
            return False
        except OSError as unlink_err:
            logger.warning(
                f"[TmpImages] cleanup could not remove {image_id}: "
                f"{unlink_err} — next tick will retry"
            )
            return False

    @staticmethod
    def _unlink_quietly(path) -> bool:
        """Unlink a single orphan file; idempotent + OSError-soft."""
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as unlink_err:
            logger.warning(
                f"[TmpImages] cleanup could not remove orphan "
                f"{path.name}: {unlink_err} — next tick will retry"
            )
            return False
