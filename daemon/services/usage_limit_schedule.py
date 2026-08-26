"""Usage-limit episode schedule derivation and anchor helpers.

Shared machinery for the dedicated usage-limit deferral path
(docs/plans/usage-limit-deferral-path.md, work units 5/6/8):

* :func:`next_usage_limit_retry_at` — the STATELESS, elapsed-based wake
  schedule (3 min → 5 min → 10 min → 15 min cap). Crash-safe: the next
  slot is derived from the persisted first-sighting anchor, not from an
  attempt counter, so a restart mid-episode resumes the window instead
  of restarting it. Monotonic: a re-park after a long pause picks the
  next ABSOLUTE cumsum slot, never a slot in the past.
* Anchor read/write/clear helpers over ``instance_metadata``
  (``instances.metadata`` JSONB) — the anchor is set-once per episode
  by the worker seam, cleared on success (pipeline success callback)
  and at the race-won terminal composition. All helpers are SOFT-FAIL:
  an anchor failure must never break a deferral or a finalize.

The episode POLICY (deadline check, terminal composition) lives at the
worker seam (``worker_pool._handle_usage_limit``); this module holds
only the pure schedule math and the metadata plumbing so the worker and
stale recovery cannot drift apart.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Sequence

logger = logging.getLogger(__name__)

# Instance-metadata key holding the episode's first-sighting timestamp
# (ISO 8601, UTC). Set-once per episode; an episode ends ONLY at
# success or at the race-won terminal composition — both clear it.
USAGE_LIMIT_FIRST_SEEN_METADATA_KEY = "usage_limit_first_seen_at"

# Default episode horizon: 6 h from first sighting. This is the RETRY
# horizon (anchor + deadline), not a timeout value — attempts still
# fail in seconds; patience lives between tasks.
DEFAULT_USAGE_LIMIT_WINDOW_SECONDS = 21600

# Default wake schedule: 3 min, 5 min, 10 min, then a 15 min cap
# indefinitely until the deadline terminates the episode.
DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS: tuple[float, ...] = (
    180.0,
    300.0,
    600.0,
    900.0,
)

# Default per-wake jitter (± fraction of the slot's step delay) so a
# herd of quota-hit instances doesn't wake on identical slots.
DEFAULT_USAGE_LIMIT_RETRY_JITTER_FRACTION = 0.1

# Jitter floor: ``next_retry_at`` is never scheduled before
# ``now + floor`` under any jitter roll (review rev3 §3.2 — an
# early-jittered wake re-selects the same slot and a re-rolled negative
# jitter could otherwise land before ``now`` → immediate re-attempt).
USAGE_LIMIT_JITTER_FLOOR_SECONDS = 30.0


def next_usage_limit_retry_at(
    first_seen: datetime,
    now: datetime,
    delays: Sequence[float] = DEFAULT_USAGE_LIMIT_RETRY_DELAYS_SECONDS,
    jitter_fraction: float = DEFAULT_USAGE_LIMIT_RETRY_JITTER_FRACTION,
    floor_seconds: float = USAGE_LIMIT_JITTER_FLOOR_SECONDS,
    rng: random.Random | None = None,
) -> datetime:
    """Derive the next usage-limit retry wake time (stateless schedule).

    Schedule contract:

    * ``cumsum`` slots are the running totals of ``delays``
      (default 180, 480, 1080, 1980, ... — the last delay repeats).
    * The selected slot is the SMALLEST cumsum STRICTLY greater than
      ``elapsed = now - first_seen``; the returned time (pre-jitter) is
      ``first_seen + cumsum[k]`` — an ABSOLUTE deadline-derived slot,
      which is what makes the derivation crash-safe and monotonic.
    * When ``elapsed`` exceeds the last listed cumsum, the schedule
      EXTENDS BY THE FINAL DELAY indefinitely:
      ``cumsum[k] = cumsum[last] + final_delay * (k - last)`` for all
      further k — until the worker seam's deadline check terminates
      the episode. This contract edge is deliberate, not an error.
    * Jitter of ``±jitter_fraction`` of the slot's step delay is added
      per wake (herd decoupling), then CLAMPED so the result is never
      before ``now + floor_seconds`` — monotonic against ``now`` under
      every roll (review rev3 §3.2).

    Args:
        first_seen: The episode anchor (first quota sighting, UTC).
        now: Current time (UTC).
        delays: Wake step delays in seconds; the last entry is the
            beyond-list cap. Must be non-empty and positive.
        jitter_fraction: Per-wake jitter as a fraction of the selected
            slot's step delay (0 disables jitter).
        floor_seconds: Minimum distance from ``now`` the result may
            take after jitter (the past-scheduling clamp).
        rng: Optional deterministic RNG for tests.

    Returns:
        The next retry wake time (aware datetime, ≥ ``now + floor``).

    Raises:
        ValueError: If ``delays`` is empty or contains non-positive
            entries.
    """
    if not delays:
        raise ValueError("usage-limit retry delays must be non-empty")
    if any(d <= 0 for d in delays):
        raise ValueError("usage-limit retry delays must be positive")

    elapsed = (now - first_seen).total_seconds()

    cumulative = 0.0
    slot_cumulative: float | None = None
    step: float = delays[-1]
    for delay in delays:
        cumulative += delay
        if cumulative > elapsed:
            slot_cumulative = cumulative
            step = delay
            break

    if slot_cumulative is None:
        # Contract edge: past the last listed slot — extend by the
        # final delay until the deadline check (worker seam) ends the
        # episode.
        final_delay = delays[-1]
        last_cumulative = cumulative
        remaining = elapsed - last_cumulative
        periods = math.floor(remaining / final_delay) + 1
        slot_cumulative = last_cumulative + periods * final_delay
        step = final_delay

    candidate = first_seen + timedelta(seconds=slot_cumulative)

    if jitter_fraction > 0:
        rand = rng if rng is not None else random
        jitter_delta = rand.uniform(-jitter_fraction, jitter_fraction) * step
        candidate = candidate + timedelta(seconds=jitter_delta)

    floor_at = now + timedelta(seconds=floor_seconds)
    if candidate < floor_at:
        candidate = floor_at
    return candidate


def usage_limit_deadline(first_seen: datetime, window_seconds: float) -> datetime:
    """The episode's terminal deadline: ``first_seen + window``."""
    return first_seen + timedelta(seconds=window_seconds)


def usage_limit_in_window(
    first_seen: datetime,
    now: datetime | None = None,
    window_seconds: float = DEFAULT_USAGE_LIMIT_WINDOW_SECONDS,
) -> bool:
    """Whether ``now`` is still INSIDE the episode window.

    The SINGLE boundary predicate, shared by the worker seam's deadline
    check and ``live_usage_limit_first_seen``'s liveness gate so the two
    consumers cannot drift (exact-boundary semantics: ``now == deadline``
    is OUT of window — the episode terminalizes). ``now`` defaults to
    the current UTC time.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return now < usage_limit_deadline(first_seen, window_seconds)


def parse_usage_limit_first_seen(value: object) -> datetime | None:
    """Parse the persisted anchor timestamp as an aware UTC datetime.

    Returns ``None`` for missing/unparseable values — callers treat
    that as "no anchor" (degenerate fresh-window case).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_usage_limit_first_seen(
    instance_repo: object | None,
    instance_id: str | None,
) -> datetime | None:
    """Read the usage-limit episode anchor (SOFT-FAIL → ``None``).

    ``None`` means "no usable anchor" — either absent (fresh episode)
    or the read failed (degenerate case: the worker seam re-stamps
    ``now``; the plan accepts the window restart on a hard DB failure).
    """
    if instance_repo is None or not instance_id:
        return None
    try:
        # Prefer the targeted single-key read (no full-row hydration)
        # when the repo provides it; fall back to ``get()`` for
        # duck-typed/partial repos.
        targeted = getattr(instance_repo, "get_metadata_value", None)
        if callable(targeted):
            raw = targeted(instance_id, USAGE_LIMIT_FIRST_SEEN_METADATA_KEY)
        else:
            instance = instance_repo.get(instance_id)
            metadata = getattr(instance, "instance_metadata", None)
            raw = (
                metadata.get(USAGE_LIMIT_FIRST_SEEN_METADATA_KEY)
                if isinstance(metadata, dict)
                else None
            )
        return parse_usage_limit_first_seen(raw)
    except Exception as e:  # noqa: BLE001 — soft-fail by contract
        logger.warning(
            "usage-limit anchor read failed for instance %s: %s: %s",
            (instance_id or "")[:8],
            type(e).__name__,
            e,
        )
        return None


def write_usage_limit_first_seen(
    instance_repo: object | None,
    instance_id: str | None,
    now: datetime,
) -> bool:
    """Stamp the episode anchor (set-once; SOFT-FAIL → ``False``).

    The caller owns the set-once discipline (only writes when the read
    returned ``None``); this helper is the plain metadata write.
    """
    if instance_repo is None or not instance_id:
        return False
    try:
        instance_repo.set_metadata(
            instance_id,
            USAGE_LIMIT_FIRST_SEEN_METADATA_KEY,
            now.isoformat(),
        )
        return True
    except Exception as e:  # noqa: BLE001 — soft-fail by contract
        logger.warning(
            "usage-limit anchor write failed for instance %s: %s: %s",
            instance_id[:8],
            type(e).__name__,
            e,
        )
        return False


def clear_usage_limit_first_seen(
    instance_repo: object | None,
    instance_id: str | None,
) -> bool:
    """Clear the episode anchor (SOFT-FAIL → ``False``).

    Called on episode end — success (pipeline success callback) or the
    race-won terminal composition. Clearing must NEVER break a
    finalize: failures are logged and swallowed. Idempotent (deleting
    an absent key is a no-op on both dialects).
    """
    if instance_repo is None or not instance_id:
        return False
    try:
        # Prefer the conditional delete (zero-row match when the key is
        # absent — no UPDATE on the hot success path); fall back to the
        # unconditional ``delete_metadata`` for duck-typed/partial repos.
        conditional = getattr(instance_repo, "delete_metadata_if_present", None)
        if callable(conditional):
            conditional(instance_id, USAGE_LIMIT_FIRST_SEEN_METADATA_KEY)
        else:
            instance_repo.delete_metadata(
                instance_id,
                USAGE_LIMIT_FIRST_SEEN_METADATA_KEY,
            )
        return True
    except Exception as e:  # noqa: BLE001 — soft-fail by contract
        logger.warning(
            "usage-limit anchor clear failed for instance %s: %s: %s",
            (instance_id or "")[:8],
            type(e).__name__,
            e,
        )
        return False


def live_usage_limit_first_seen(
    instance_repo: object | None,
    instance_id: str | None,
    window_seconds: float = DEFAULT_USAGE_LIMIT_WINDOW_SECONDS,
) -> datetime | None:
    """Return the anchor when a LIVE (in-window) episode exists, else ``None``.

    "Live" = anchor present AND ``usage_limit_in_window`` (the shared
    boundary predicate — ``now == deadline`` counts as NOT live).
    Used by stale recovery (W8) as the deadline-bounded-caller proof
    for the retry-budget bypass: an instance WITHOUT a live anchor is
    not in an episode and must take the default-budget path
    byte-identically. A past-deadline anchor is a stale episode whose
    terminal composition was interrupted — the default path
    permanently fails it (correct terminal outcome, generic message).
    """
    first_seen = read_usage_limit_first_seen(instance_repo, instance_id)
    if first_seen is None:
        return None
    if not usage_limit_in_window(first_seen, window_seconds=window_seconds):
        return None
    return first_seen
