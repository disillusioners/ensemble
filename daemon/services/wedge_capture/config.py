"""Wedge-capture instrument configuration (D14 / D15 / T1).

Pattern B — **env-direct** ``os.environ.get``. No ``ServicesConfig``
field, no ``AliasChoices``, no config-schema migration for a
diagnostic instrument. This is the exact model the AutoContinue
kill-switch already uses (``daemon/services/auto_continue_boot_pass.py``
reads ``os.environ.get("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1")``
with no config field), so the two levers read the same way.

THE CENSUS IS **15** KNOBS (D15 / F-07)
--------------------------------------
``ENSEMBLE_WEDGE_CAPTURE_ENABLED``, ``_DIR``, ``_CPU_PCT``,
``_CPU_SUSTAIN_S``, ``_LAG_S``, ``_LAG_SUSTAIN_S``,
``_THREAD_CONCENTRATION_PCT``, ``_SAMPLE_INTERVAL_S``, ``_COOLDOWN_S``,
``_DEADLINE_S``, ``_MAX_CAPTURES``, ``_MAX_AGE_HOURS``,
``_MAX_DISK_MB``, ``_SLOW_SAVER_MS``, ``_OBJECT_CENSUS``.

The manual-capture rate cap (2/min, F-16) is **NOT** an env knob — it
lives in the watchdog's in-process counter (D38), because it is a
rate limit on a single process, not a configuration surface.

VALIDATION IS HARD BUT NEVER FATAL
----------------------------------
An invalid value falls back to the default and logs ONE warning. A
diagnostic instrument must never be the reason the daemon refuses to
boot.

THE LAG / INTERVAL COUPLING GUARD (approver N1b)
------------------------------------------------
``lag_s`` MUST be strictly greater than ``sample_interval_s``. Under
the D4(b) probe mechanics a HEALTHY daemon measures a lag of roughly
ONE tick (the probe posted at tick *T* is executed at tick *T+1*), so
``lag_s <= sample_interval_s`` would fire the lag trigger on a
perfectly healthy daemon — a false-fire that turns a diagnostic into
a capture-spam engine. A violating value falls back to the default
lag threshold with a warning.

All knobs are read ONCE at watchdog start (D14) — the watchdog is
per-process and there is no per-boot re-read requirement for these
knobs. (The AutoContinue *mode* IS re-read per boot, but that is the
AutoContinue lever, not this instrument's.)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("daemon.wedge_capture.config")

#: Every knob lives under this prefix (D14 — Pattern B).
ENV_PREFIX = "ENSEMBLE_WEDGE_CAPTURE_"

# ── the 15 knob names ───────────────────────────────────────────────────────
ENV_ENABLED = ENV_PREFIX + "ENABLED"
ENV_DIR = ENV_PREFIX + "DIR"
ENV_CPU_PCT = ENV_PREFIX + "CPU_PCT"
ENV_CPU_SUSTAIN_S = ENV_PREFIX + "CPU_SUSTAIN_S"
ENV_LAG_S = ENV_PREFIX + "LAG_S"
ENV_LAG_SUSTAIN_S = ENV_PREFIX + "LAG_SUSTAIN_S"
ENV_THREAD_CONCENTRATION_PCT = ENV_PREFIX + "THREAD_CONCENTRATION_PCT"
ENV_SAMPLE_INTERVAL_S = ENV_PREFIX + "SAMPLE_INTERVAL_S"
ENV_COOLDOWN_S = ENV_PREFIX + "COOLDOWN_S"
ENV_DEADLINE_S = ENV_PREFIX + "DEADLINE_S"
ENV_MAX_CAPTURES = ENV_PREFIX + "MAX_CAPTURES"
ENV_MAX_AGE_HOURS = ENV_PREFIX + "MAX_AGE_HOURS"
ENV_MAX_DISK_MB = ENV_PREFIX + "MAX_DISK_MB"
ENV_SLOW_SAVER_MS = ENV_PREFIX + "SLOW_SAVER_MS"
ENV_OBJECT_CENSUS = ENV_PREFIX + "OBJECT_CENSUS"

#: The knob names, in census order — the read-back surface renders this.
ENV_KNOB_NAMES: tuple[str, ...] = (
    ENV_ENABLED,
    ENV_DIR,
    ENV_CPU_PCT,
    ENV_CPU_SUSTAIN_S,
    ENV_LAG_S,
    ENV_LAG_SUSTAIN_S,
    ENV_THREAD_CONCENTRATION_PCT,
    ENV_SAMPLE_INTERVAL_S,
    ENV_COOLDOWN_S,
    ENV_DEADLINE_S,
    ENV_MAX_CAPTURES,
    ENV_MAX_AGE_HOURS,
    ENV_MAX_DISK_MB,
    ENV_SLOW_SAVER_MS,
    ENV_OBJECT_CENSUS,
)

#: The default capture root name, under the resolved data dir (D9).
CAPTURE_DIR_NAME = "wedge-captures"

# ── the 15 defaults (D15) ───────────────────────────────────────────────────
DEFAULT_ENABLED = True
DEFAULT_CPU_PCT = 95.0
DEFAULT_CPU_SUSTAIN_S = 30.0
DEFAULT_LAG_S = 5.0
DEFAULT_LAG_SUSTAIN_S = 15.0
DEFAULT_THREAD_CONCENTRATION_PCT = 85.0
DEFAULT_SAMPLE_INTERVAL_S = 1.0
DEFAULT_COOLDOWN_S = 300.0
DEFAULT_DEADLINE_S = 20.0
DEFAULT_MAX_CAPTURES = 20
DEFAULT_MAX_AGE_HOURS = 168
DEFAULT_MAX_DISK_MB = 512
DEFAULT_SLOW_SAVER_MS = 5000
DEFAULT_OBJECT_CENSUS = False

# ── validation floors (T1) ──────────────────────────────────────────────────
#: The sample interval may never go below this (a faster sweep is a
#: ``/proc`` storm, not a better instrument).
MIN_SAMPLE_INTERVAL_S = 0.2
#: The capture deadline may never go below this (a capture that cannot
#: finish in a second is not a capture).
MIN_DEADLINE_S = 1.0


def _env(name: str) -> str | None:
    """Read one knob — ``None`` when unset (an empty string IS a value)."""
    return os.environ.get(name)


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    """Boolean knob — ``0`` / ``false`` / ``no`` / ``off`` are OFF."""
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    logger.warning(
        "WedgeCapture: invalid boolean for %s=%r — falling back to %s",
        name, raw, default,
    )
    return default


def _parse_float(
    name: str,
    raw: str | None,
    default: float,
    *,
    minimum: float | None = None,
    minimum_exclusive: bool = False,
) -> float:
    """Float knob with a floor; invalid → default + one warning."""
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "WedgeCapture: invalid number for %s=%r — falling back to %s",
            name, raw, default,
        )
        return default
    if minimum is not None:
        too_small = value <= minimum if minimum_exclusive else value < minimum
        if too_small:
            logger.warning(
                "WedgeCapture: %s=%r is below the allowed minimum %s — "
                "falling back to %s",
                name, raw, minimum, default,
            )
            return default
    return value


def _parse_int(
    name: str,
    raw: str | None,
    default: int,
    *,
    minimum: int | None = None,
) -> int:
    """Int knob with a floor; invalid → default + one warning."""
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "WedgeCapture: invalid integer for %s=%r — falling back to %s",
            name, raw, default,
        )
        return default
    if minimum is not None and value < minimum:
        logger.warning(
            "WedgeCapture: %s=%r is below the allowed minimum %s — "
            "falling back to %s",
            name, raw, minimum, default,
        )
        return default
    return value


def resolve_data_dir() -> Path:
    """The daemon's writable scratch dir — ``ENSEMBLE_DATA_DIR > DATA_DIR > ./data``.

    Byte-identical to the chain in ``daemon/api.py`` (``app.state.data_dir``
    is derived from the same precedence), so the capture root lands
    beside the daemon's own scratch state (D9).
    """
    return Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )


@dataclass(frozen=True)
class WedgeCaptureConfig:
    """The resolved instrument configuration (D15).

    Attributes:
        enabled: The kill-switch. ``False`` ⇒ the watchdog never starts
            and a manual capture is refused with ``watchdog_disabled``.
        capture_dir: The ABSOLUTE capture root (D9). Never inside the
            repo, never inside a release dir.
        cpu_pct: Process CPU threshold, as a percentage of ONE core.
        cpu_sustain_s: Wall-time sustain window for the CPU trigger.
        lag_s: Event-loop lag threshold, in seconds.
        lag_sustain_s: Wall-time sustain window for the lag trigger.
        thread_concentration_pct: One thread's share of process CPU.
        sample_interval_s: The sampler cadence.
        cooldown_s: Per-reason cooldown between captures.
        deadline_s: The whole-capture wall-clock budget.
        max_captures: The count cap (directory-authoritative, D23).
        max_age_hours: The age cap, in hours.
        max_disk_mb: The disk cap, in megabytes.
        slow_saver_ms: The stall-ledger cut (D18).
        object_census: Whether the O(heap) object census runs (D20).
        resolved: The resolved knob map, for the read-back surface.
    """

    enabled: bool = DEFAULT_ENABLED
    capture_dir: Path = field(default_factory=lambda: resolve_data_dir() / CAPTURE_DIR_NAME)
    cpu_pct: float = DEFAULT_CPU_PCT
    cpu_sustain_s: float = DEFAULT_CPU_SUSTAIN_S
    lag_s: float = DEFAULT_LAG_S
    lag_sustain_s: float = DEFAULT_LAG_SUSTAIN_S
    thread_concentration_pct: float = DEFAULT_THREAD_CONCENTRATION_PCT
    sample_interval_s: float = DEFAULT_SAMPLE_INTERVAL_S
    cooldown_s: float = DEFAULT_COOLDOWN_S
    deadline_s: float = DEFAULT_DEADLINE_S
    max_captures: int = DEFAULT_MAX_CAPTURES
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS
    max_disk_mb: int = DEFAULT_MAX_DISK_MB
    slow_saver_ms: int = DEFAULT_SLOW_SAVER_MS
    object_census: bool = DEFAULT_OBJECT_CENSUS
    resolved: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        *,
        data_dir: str | Path | None = None,
    ) -> "WedgeCaptureConfig":
        """Read the 15 knobs from the environment.

        Args:
            data_dir: An explicit data dir (the resolved
                ``app.state.data_dir``). When given it wins over the
                env chain — the running process's truth (D9 / A-1).

        Returns:
            The resolved configuration. Never raises.
        """
        enabled = _parse_bool(ENV_ENABLED, _env(ENV_ENABLED), DEFAULT_ENABLED)

        if data_dir is not None:
            root = Path(data_dir) / CAPTURE_DIR_NAME
        else:
            raw_dir = _env(ENV_DIR)
            if raw_dir and raw_dir.strip():
                root = Path(raw_dir.strip())
            else:
                root = resolve_data_dir() / CAPTURE_DIR_NAME
        try:
            root = root.expanduser().resolve()
        except (OSError, RuntimeError):  # pragma: no cover — defensive
            root = Path(os.path.abspath(str(root)))

        cpu_pct = _parse_float(
            ENV_CPU_PCT, _env(ENV_CPU_PCT), DEFAULT_CPU_PCT, minimum=0.0,
            minimum_exclusive=True,
        )
        cpu_sustain_s = _parse_float(
            ENV_CPU_SUSTAIN_S, _env(ENV_CPU_SUSTAIN_S), DEFAULT_CPU_SUSTAIN_S,
            minimum=0.0,
        )
        lag_s = _parse_float(
            ENV_LAG_S, _env(ENV_LAG_S), DEFAULT_LAG_S, minimum=0.0,
            minimum_exclusive=True,
        )
        lag_sustain_s = _parse_float(
            ENV_LAG_SUSTAIN_S, _env(ENV_LAG_SUSTAIN_S), DEFAULT_LAG_SUSTAIN_S,
            minimum=0.0,
        )
        concentration = _parse_float(
            ENV_THREAD_CONCENTRATION_PCT, _env(ENV_THREAD_CONCENTRATION_PCT),
            DEFAULT_THREAD_CONCENTRATION_PCT, minimum=0.0, minimum_exclusive=True,
        )
        sample_interval_s = _parse_float(
            ENV_SAMPLE_INTERVAL_S, _env(ENV_SAMPLE_INTERVAL_S),
            DEFAULT_SAMPLE_INTERVAL_S, minimum=MIN_SAMPLE_INTERVAL_S,
        )
        cooldown_s = _parse_float(
            ENV_COOLDOWN_S, _env(ENV_COOLDOWN_S), DEFAULT_COOLDOWN_S, minimum=0.0,
        )
        deadline_s = _parse_float(
            ENV_DEADLINE_S, _env(ENV_DEADLINE_S), DEFAULT_DEADLINE_S,
            minimum=MIN_DEADLINE_S,
        )
        max_captures = _parse_int(
            ENV_MAX_CAPTURES, _env(ENV_MAX_CAPTURES), DEFAULT_MAX_CAPTURES,
            minimum=1,
        )
        max_age_hours = _parse_int(
            ENV_MAX_AGE_HOURS, _env(ENV_MAX_AGE_HOURS), DEFAULT_MAX_AGE_HOURS,
            minimum=1,
        )
        max_disk_mb = _parse_int(
            ENV_MAX_DISK_MB, _env(ENV_MAX_DISK_MB), DEFAULT_MAX_DISK_MB, minimum=1,
        )
        slow_saver_ms = _parse_int(
            ENV_SLOW_SAVER_MS, _env(ENV_SLOW_SAVER_MS), DEFAULT_SLOW_SAVER_MS,
            minimum=0,
        )
        object_census = _parse_bool(
            ENV_OBJECT_CENSUS, _env(ENV_OBJECT_CENSUS), DEFAULT_OBJECT_CENSUS,
        )

        # ── the lag / interval coupling guard (approver N1b) ─────────────
        # A HEALTHY daemon measures a lag of ~ONE tick under the D4(b)
        # probe mechanics, so ``lag_s <= sample_interval_s`` would fire
        # the lag trigger on a healthy daemon. Fall back to the default.
        if lag_s <= sample_interval_s:
            logger.warning(
                "WedgeCapture: %s=%s must be strictly greater than "
                "%s=%s (a healthy daemon measures ~one tick of loop lag) — "
                "falling back to %s=%s",
                ENV_LAG_S, lag_s, ENV_SAMPLE_INTERVAL_S, sample_interval_s,
                ENV_LAG_S, DEFAULT_LAG_S,
            )
            lag_s = DEFAULT_LAG_S

        resolved: dict[str, Any] = {
            ENV_ENABLED: enabled,
            ENV_DIR: str(root),
            ENV_CPU_PCT: cpu_pct,
            ENV_CPU_SUSTAIN_S: cpu_sustain_s,
            ENV_LAG_S: lag_s,
            ENV_LAG_SUSTAIN_S: lag_sustain_s,
            ENV_THREAD_CONCENTRATION_PCT: concentration,
            ENV_SAMPLE_INTERVAL_S: sample_interval_s,
            ENV_COOLDOWN_S: cooldown_s,
            ENV_DEADLINE_S: deadline_s,
            ENV_MAX_CAPTURES: max_captures,
            ENV_MAX_AGE_HOURS: max_age_hours,
            ENV_MAX_DISK_MB: max_disk_mb,
            ENV_SLOW_SAVER_MS: slow_saver_ms,
            ENV_OBJECT_CENSUS: object_census,
        }

        return cls(
            enabled=enabled,
            capture_dir=root,
            cpu_pct=cpu_pct,
            cpu_sustain_s=cpu_sustain_s,
            lag_s=lag_s,
            lag_sustain_s=lag_sustain_s,
            thread_concentration_pct=concentration,
            sample_interval_s=sample_interval_s,
            cooldown_s=cooldown_s,
            deadline_s=deadline_s,
            max_captures=max_captures,
            max_age_hours=max_age_hours,
            max_disk_mb=max_disk_mb,
            slow_saver_ms=slow_saver_ms,
            object_census=object_census,
            resolved=resolved,
        )

    def as_status(self) -> dict[str, Any]:
        """The status-surface view of the effective configuration (D37).

        Every field is present and concrete — the status payload never
        renders a ``null`` for a value the instrument knows.
        """
        return {
            "enabled": self.enabled,
            "capture_dir": str(self.capture_dir),
            "cpu_pct": self.cpu_pct,
            "cpu_sustain_s": self.cpu_sustain_s,
            "lag_s": self.lag_s,
            "lag_sustain_s": self.lag_sustain_s,
            "thread_concentration_pct": self.thread_concentration_pct,
            "sample_interval_s": self.sample_interval_s,
            "cooldown_s": self.cooldown_s,
            "deadline_s": self.deadline_s,
            "max_captures": self.max_captures,
            "max_age_hours": self.max_age_hours,
            "max_disk_mb": self.max_disk_mb,
            "slow_saver_ms": self.slow_saver_ms,
            "object_census": self.object_census,
        }


__all__ = [
    "CAPTURE_DIR_NAME",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_CPU_PCT",
    "DEFAULT_CPU_SUSTAIN_S",
    "DEFAULT_DEADLINE_S",
    "DEFAULT_ENABLED",
    "DEFAULT_LAG_S",
    "DEFAULT_LAG_SUSTAIN_S",
    "DEFAULT_MAX_AGE_HOURS",
    "DEFAULT_MAX_CAPTURES",
    "DEFAULT_MAX_DISK_MB",
    "DEFAULT_OBJECT_CENSUS",
    "DEFAULT_SAMPLE_INTERVAL_S",
    "DEFAULT_SLOW_SAVER_MS",
    "DEFAULT_THREAD_CONCENTRATION_PCT",
    "ENV_KNOB_NAMES",
    "ENV_PREFIX",
    "MIN_DEADLINE_S",
    "MIN_SAMPLE_INTERVAL_S",
    "WedgeCaptureConfig",
    "resolve_data_dir",
]
