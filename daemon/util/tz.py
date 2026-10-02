"""Timezone resolution + DST anchor helpers for the scheduled-tasks feature.

This module is the single home for tz-related logic that is NOT
scheduler-specific — it is consumed by ``daemon/sources/adapters/scheduler.py``,
the phase-2 scheduling service, the agent tools, and the REST routes.

Resolution chain (D2, ADR-002)
------------------------------
A user-stated time ``when`` is always interpreted in the caller's local
timezone ``tz``. The resolution order, applied at every NEW surface
(tools, REST, service):

    1. caller-supplied ``explicit`` if non-None   → ZoneInfo (KeyError → UTC + warning)
    2. ``default`` if non-None (caller may pre-fill from
       ``SchedulingConfig.default_timezone``)     → ZoneInfo (KeyError → UTC + warning)
    3. ``detect_host_local_timezone()``            → /etc/localtime symlink + TZ env fallback
    4. terminal fallback                            → ``datetime.timezone.utc`` + warning

The terminal fallback uses :class:`datetime.timezone.utc` (NOT
``ZoneInfo('UTC')``). On a stripped container with no system tzdb and no
``tzdata`` pip package, ``ZoneInfo('UTC')`` raises ``ZoneInfoNotFoundError``;
``datetime.timezone.utc`` is a C-level constant and cannot fail (architecture
§4.3).

DST semantics (ADR-008)
-----------------------
The canonical DST rule for the whole feature is: skip-the-gap on
spring-forward, first-occurrence on fall-back ambiguity (fold=0).
:func:`anchor_local_to_utc` encodes this for the one-shot path; ``croniter``
(``>=3.0.0``, pinned in ``pyproject.toml:25``) is the source of truth for
the cron path. Both paths share the same rule so phase-5's gap/fold tests
can parameterize over both and assert no drift.

Conventions (mandatory)
-----------------------
* **No third-party detector.** The project does NOT declare ``tzlocal`` or
  ``tzdata`` as a dependency; this module uses stdlib-only detection.
* **No new tzinfo enters the system without one.** All NEW surfaces (tools,
  REST, service) emit tz-qualified ISO datetimes; phase-2 owns the
  contract pin in their surfaces.
* **Caching is two-tier** (architecture §4.4): positive results cache for
  ``host_local_tz_cache_seconds`` (default 300s); negative results cache
  for ``negative_cache_seconds`` (default 60s, capped at the positive TTL)
  so an operator fixing ``/etc/localtime`` mid-process isn't hidden for
  5 minutes. The cache is pruned on every store (entries outside a
  2× max-window window are dropped) so the cache size stays bounded
  for long-lived daemons.

Loud-warning posture
--------------------
``resolve_timezone`` always returns ``(zone, warning_message)``. The
warning is empty on clean resolution. ``for_tool=True`` keeps the warning
visible to the user surface; ``for_tool=False`` is for adapter-internal use
where the warning is logged separately.

See ``.agents/shared/planning/scheduled-tasks/decisions.md`` (ADR-002,
ADR-008) and ``architecture-recommendation.md`` §4 for the full rationale.
"""

from __future__ import annotations

import logging
import os
import time as _time
from datetime import datetime, timezone as _stdlib_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Host-local tz detection
# ---------------------------------------------------------------------------


def _read_etc_localtime_target() -> str | None:
    """Return the IANA name encoded in /etc/localtime's symlink, or None.

    Handles the common layout where ``/etc/localtime`` is a symlink into
    ``/usr/share/zoneinfo/<IANA>`` (e.g. ``/usr/share/zoneinfo/Asia/Ho_Chi_Minh``
    or ``/usr/share/zoneinfo/Etc/UTC``). The relative form
    (``../../usr/share/zoneinfo/Asia/Ho_Chi_Minh``) on macOS is also handled
    by walking parent components and finding the ``zoneinfo`` segment.
    """
    path = Path("/etc/localtime")
    try:
        if not path.is_symlink():
            return None
    except OSError:
        return None
    try:
        target_str = os.readlink(str(path))
    except OSError:
        return None
    if not target_str:
        return None

    # Common case: absolute path under zoneinfo.
    if os.path.isabs(target_str):
        # Find the ``zoneinfo`` segment in the absolute path and return
        # everything after it as the IANA name.
        parts = Path(target_str).parts
        try:
            idx = parts.index("zoneinfo")
        except ValueError:
            return None
        remainder = parts[idx + 1:]
        if not remainder:
            return None
        return "/".join(remainder)

    # Relative case (macOS): walk through path components.
    parts = Path(target_str).parts
    try:
        idx = parts.index("zoneinfo")
    except ValueError:
        return None
    remainder = parts[idx + 1:]
    if not remainder:
        return None
    return "/".join(remainder)


def _validate_iana(name: str) -> bool:
    """Return True if ``name`` resolves to a real IANA tz via ZoneInfo."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


# Module-level cache: ``(cache_window, kind, value)``.
#   kind = "positive" → value is the IANA name
#   kind = "negative" → value is None (no detection)
# ``cache_window`` is the TTL used for THIS cache entry; positive and
# negative windows may differ.
#
# Pruning (architecture §4.4 footnote): on each store the cache drops
# any entry whose bucket is older than ``max(2 * pos_seconds,
# 2 * neg_seconds)`` of monotonic time — bounded by O(1) since each
# detection produces at most two new buckets and the prune drops
# everything outside a 2–3 window window. Pre-prune behavior leaked one
# row per TTL window over the daemon's lifetime (unbounded growth).
_cache: dict[int, tuple[str, str | None]] = {}


def _cache_clear_for_tests() -> None:
    """Reset the module-level cache. For tests only."""
    global _cache
    _cache = {}


def _cache_prune(*, now_monotonic: float, pos_seconds: int, neg_seconds: int) -> None:
    """Drop entries whose bucket is older than the active window.

    Keeps the cache size bounded: at most ``pos_seconds`` + ``neg_seconds``
    bucket slots are retained (a few entries per active TTL window). No-op
    when the cache is empty.
    """
    if not _cache:
        return
    # ``max_window`` covers both positive and negative bucket keys
    # (the 2× factor leaves a one-window buffer for late lookups).
    max_window = max(2 * pos_seconds, 2 * neg_seconds if neg_seconds > 0 else 2 * pos_seconds)
    cutoff_monotonic = now_monotonic - max_window
    # Bucket key → pos bucket mapping (inverse of ``pos_key = bucket*2+0``
    # / ``neg_key = bucket*2+1``). Even keys → positive; odd → negative.
    stale_keys = []
    for key in _cache.keys():
        bucket_idx = key // 2
        window = pos_seconds if (key % 2 == 0) else (neg_seconds if neg_seconds > 0 else pos_seconds)
        bucket_monotonic = bucket_idx * window
        if bucket_monotonic < cutoff_monotonic:
            stale_keys.append(key)
    for key in stale_keys:
        _cache.pop(key, None)


def detect_host_local_timezone() -> str | None:
    """Resolve host-local IANA tz name via ``/etc/localtime`` symlink + TZ env fallback.

    Returns ``None`` when no IANA name is resolvable (e.g. minimal containers
    where ``/etc/localtime`` is missing and ``TZ`` is unset).

    Caching (architecture §4.4):
        * Positive results cache for ``SchedulingConfig.host_local_tz_cache_seconds``
          (default 300s).
        * Negative results cache for ``SchedulingConfig.negative_cache_seconds``
          (default 60s, capped at ``host_local_tz_cache_seconds``) so an
          operator fixing ``/etc/localtime`` mid-process isn't hidden for
          the full positive TTL.

    Robust stdlib only — NO ``tzlocal`` and NO ``tzdata`` pip package
    (project declares neither dependency; ``pyproject.toml`` zero hits).
    """
    try:
        from daemon.config import SchedulingConfig
        cfg = SchedulingConfig()
        pos_seconds = max(0, int(cfg.host_local_tz_cache_seconds or 0))
        neg_seconds = max(0, int(cfg.negative_cache_seconds or 0))
    except Exception as exc:  # noqa: BLE001 — bootstrap-safety
        # Config not yet importable (boot ordering); default to documented values.
        logger.debug("tz.detect_host_local_timezone: SchedulingConfig unavailable (%s); using defaults", exc)
        pos_seconds = 300
        neg_seconds = 60

    # Cap negative cache at positive cache (architecture §4.4).
    neg_seconds = min(neg_seconds, pos_seconds)

    if pos_seconds <= 0:
        # Cache disabled — resolve fresh every call.
        return _resolve_host_local_uncached()

    now_monotonic = _time.monotonic()
    # Two distinct cache slots live side by side in ``_cache``:
    #   - even key → positive bucket (TTL = pos_seconds)
    #   - odd key  → negative bucket (TTL = neg_seconds)
    pos_bucket = int(now_monotonic // pos_seconds)
    neg_bucket = int(now_monotonic // neg_seconds) if neg_seconds > 0 else -1
    pos_key = pos_bucket * 2 + 0
    neg_key = (neg_bucket * 2 + 1) if neg_seconds > 0 else None

    # Positive cache first: if a recent detection succeeded, honor it
    # (the operator's mid-process fix is observed on the next window).
    pos_entry = _cache.get(pos_key)
    if pos_entry is not None and pos_entry[0] == "positive":
        return pos_entry[1]

    # Negative cache: if the last detection failed, return None until the
    # negative TTL expires — the operator's fix is picked up in ≤60s
    # (architecture §4.4) instead of waiting the full 300s.
    if neg_key is not None:
        neg_entry = _cache.get(neg_key)
        if neg_entry is not None and neg_entry[0] == "negative":
            return None

    # Cache miss — resolve fresh.
    result = _resolve_host_local_uncached()

    # Prune stale entries before the new store (keeps the cache
    # size bounded — see module docstring footnote).
    _cache_prune(
        now_monotonic=now_monotonic,
        pos_seconds=pos_seconds,
        neg_seconds=neg_seconds,
    )

    # Store under the appropriate key.
    if result is None:
        if neg_key is not None:
            _cache[neg_key] = ("negative", None)
    else:
        _cache[pos_key] = ("positive", result)

    return result


def _resolve_host_local_uncached() -> str | None:
    """Resolve host-local IANA tz name WITHOUT touching any cache."""
    name = _read_etc_localtime_target()
    if name and _validate_iana(name):
        return name

    # TZ env fallback — POSIX form. ``TZ`` is conventionally an absolute
    # path (``TZ=:/etc/localtime`` on glibc; colon-prefixed); also accept
    # a bare IANA name for portability (``TZ=Asia/Ho_Chi_Minh``).
    tz_env = os.environ.get("TZ")
    if tz_env:
        candidate = tz_env[1:] if tz_env.startswith(":") else tz_env
        if candidate and _validate_iana(candidate):
            return candidate

    return None


# ---------------------------------------------------------------------------
# resolve_timezone — the 4-step chain
# ---------------------------------------------------------------------------


def resolve_timezone(
    explicit: str | None,
    *,
    default: str | None = None,
    for_tool: bool = False,
) -> tuple[ZoneInfo | _stdlib_timezone, str]:
    """Resolve a timezone through the 4-step chain (D2, ADR-002).

    Args:
        explicit: Caller-supplied IANA name. Non-None short-circuits the chain.
        default: Caller-supplied fallback (typically
            ``SchedulingConfig.default_timezone``). Non-None is the second rung.
        for_tool: When ``True``, the warning string is returned even on the
            internal fallbacks so callers can echo it into tool / REST output.
            When ``False`` (default), the warning is empty string on clean
            resolution and the adapter logs the loud warning separately.

    Returns:
        ``(zone, warning_message)``. ``zone`` is a ``ZoneInfo`` on clean
        resolution (steps 1–3) or :class:`datetime.timezone.utc` on the
        terminal rung (step 4). ``warning_message`` is empty on a clean
        resolution path and populated when the chain falls through to UTC.
    """
    def _to_zone(name: str) -> tuple[ZoneInfo | _stdlib_timezone | None, str]:
        try:
            return ZoneInfo(name), ""
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "tz.resolve_timezone: invalid tz '%s', falling back to UTC", name
            )
            return None, f"Invalid tz '{name}', fell back to UTC"

    # Step 1: explicit caller-supplied.
    if explicit:
        zone, warn = _to_zone(explicit)
        if zone is not None:
            return zone, warn
        # Fall through to UTC with explicit warning.
        return _stdlib_timezone.utc, warn

    # Step 2: caller-supplied default (SchedulingConfig.default_timezone).
    if default:
        zone, warn = _to_zone(default)
        if zone is not None:
            return zone, warn
        return _stdlib_timezone.utc, warn

    # Step 3: host-local detection.
    host_name = detect_host_local_timezone()
    if host_name:
        zone, warn = _to_zone(host_name)
        if zone is not None:
            return zone, warn
        return _stdlib_timezone.utc, warn

    # Step 4: terminal fallback. Use datetime.timezone.utc — NEVER ZoneInfo('UTC').
    warn_msg = "No host tz detected, fell back to UTC"
    if for_tool:
        # Echo the warning into caller-supplied output surfaces.
        return _stdlib_timezone.utc, warn_msg
    # for_tool=False: log loud, return empty string (adapter internal path).
    logger.warning("tz.resolve_timezone: %s", warn_msg)
    return _stdlib_timezone.utc, ""


# ---------------------------------------------------------------------------
# anchor_local_to_utc — DST helper for one-shot path
# ---------------------------------------------------------------------------


def anchor_local_to_utc(
    naive_local: datetime,
    tz: ZoneInfo,
) -> tuple[datetime, str]:
    """Anchor a naive local datetime into an aware UTC datetime.

    Implements the canonical DST rule (architecture §4.2, ADR-008):

    1. **Already aware** → trust caller, return ``(aware, "")``.
    2. **Ambiguous (fold)** → use ``fold=0`` (first occurrence / pre-DST).
       Matches croniter's default; one DST rule for the whole feature.
    3. **Nonexistent (gap)** → shift forward to the next valid local time
       (equivalent to ``astimezone``-roundtrip semantics) and emit a loud
       warning. Caller decides whether to surface the warning.

    The returned datetime is always tz-aware, anchored in the *given* ``tz``
    (NOT yet converted to UTC — callers that need a UTC-anchored value call
    ``.astimezone(ZoneInfo("UTC"))`` after this helper returns).

    Args:
        naive_local: A naive (tzinfo is None) or aware datetime. If aware,
            the caller is trusted and the function is a no-op.
        tz: The IANA timezone to anchor into. Must resolve via ZoneInfo.

    Returns:
        ``(anchored_aware, warning)``. The anchored datetime is in ``tz``.
        ``warning`` is empty when no shift was needed.
    """
    # Rule 1: already aware → trust caller.
    if naive_local.tzinfo is not None:
        return naive_local, ""

    # Try the naive-as-pre-transition anchor (fold=0) first.
    pre = naive_local.replace(tzinfo=tz, fold=0)
    pre_utc = pre.astimezone(_stdlib_timezone.utc)

    # Try the naive-as-post-transition anchor (fold=1).
    post = naive_local.replace(tzinfo=tz, fold=1)
    post_utc = post.astimezone(_stdlib_timezone.utc)

    # If pre and post map to the same UTC instant, the time is UNAMBIGUOUS.
    if pre_utc == post_utc:
        return pre, ""

    # The local time is in a DST fold. The pre offset != post offset means
    # there's a gap OR a fold — distinguish by checking if the naive time
    # exists when converted back from UTC.
    #
    # Gap case: the pre-offset is the WRONG one (pre-transition offset
    # applied to a nonexistent wall-clock time). Detected by roundtripping
    # ``pre`` through UTC and back to local: if the wall-clock no longer
    # matches ``naive_local``, the wall-clock is in the gap.
    #
    # NOTE: ``pre.astimezone(tz)`` is a no-op (same instant, different
    # representation), so it cannot detect the gap. The roundtrip via
    # UTC forces ``astimezone`` to apply the actual offset at that UTC
    # instant — which is the post-transition offset for the gap case.
    pre_local_roundtrip = pre.astimezone(_stdlib_timezone.utc).astimezone(tz).replace(tzinfo=None)
    if pre_local_roundtrip != naive_local:
        # Gap case: shift forward by the gap duration to land on the
        # first valid post-transition local time. The shifted wall-clock
        # is the naive time + gap seconds, anchored with fold=1 so its
        # displayed offset matches the post-transition side.
        gap_seconds = int((pre_utc - post_utc).total_seconds())
        from datetime import timedelta as _td
        shifted_naive = naive_local + _td(seconds=gap_seconds)
        shifted = shifted_naive.replace(tzinfo=tz, fold=1)
        # Verify the shifted instant's wall-clock in tz equals shifted_naive.
        shifted_check = shifted.astimezone(tz).replace(tzinfo=None)
        # MINOR e (review-2026-10-02): the previous ``assert`` here was
        # silently elided under ``python -O`` (which strips all
        # assertions), so a DST gap-side math regression would have
        # produced a wrong fire-time in production without tripping the
        # verification. Raise ValueError instead — the call site is
        # already inside a try-shaped caller in ``anchor_local_to_utc``
        # and the helper is one-shot-only (every wrong call surfaces,
        # not just optimized builds).
        if shifted_check != shifted_naive:
            raise ValueError(
                f"tz.anchor_local_to_utc: shifted verification failed "
                f"(naive={shifted_naive}, tz-roundtrip={shifted_check}) "
                "— DST-gap shift math regression; refusing to anchor an "
                "unverified wall-clock instant."
            )
        warning = (
            f"shifted-forward from nonexistent local time "
            f"{naive_local.isoformat()} (gap of {gap_seconds}s) to "
            f"{shifted_naive.isoformat()}"
        )
        logger.warning("tz.anchor_local_to_utc: %s", warning)
        return shifted, warning

    # Fold case (fall-back ambiguity): naive time maps to TWO valid UTC
    # instants. Use fold=0 (first occurrence / pre-DST) per ADR-008.
    return pre, ""
