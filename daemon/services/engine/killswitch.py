"""Kill switches (Engine Arc Phase 3 — T3.2).

FAIL-TO-LEGACY polarity (D3.5): an unreadable engine-wide switch resolves
to "engine does not own execution" (legacy path). A per-job ``kill_switch_key``
that is unreadable resolves to "job skipped, counted as KILL_SWITCHED".

Resolution is read at TICK TIME through a ladder (D3.6): per-job
``kill_switch_key`` -> ``ENSEMBLE_ENGINE_ENABLED`` -> ``ENSEMBLE_ENGINE_SHADOW_MODE``
-> spec. A flip takes effect on the next tick, no restart.

Reads are PURE functions over an injected resolver (D3.12): tests never
touch ``os.environ``.

Wiring owner: P4 — T4.4 constructs EngineService with the killswitch
resolver; until then (D4.11 dormancy) this switch surface is INERT, not live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

_OFF_VALUES = frozenset({"0", "false", "no", "off"})
_ON_VALUES = frozenset({"1", "true", "yes", "on"})


def normalize_switch_value(raw: Any) -> Optional[bool]:
    """Resolve a raw switch value to True/False/None.

    None means "absent or unreadable/malformed" — the caller decides the
    polarity (absent => default, malformed => fail-to-legacy).
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw == 0:
            return False
        if raw == 1:
            return True
        return None
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == "":
            return None  # empty => unreadable/malformed
        if s in _OFF_VALUES:
            return False
        if s in _ON_VALUES:
            return True
        return None  # malformed => unreadable
    return None


@dataclass
class EngineSwitchResolver:
    """Pure resolver over an injected ``resolver(key) -> value | None``."""

    resolver: Callable[[str], Any]

    def engine_enabled(self) -> bool:
        """Engine-wide enabled flag (fail-to-legacy on unreadable)."""
        raw = self.resolver("ENSEMBLE_ENGINE_ENABLED")
        if raw is None:
            # absent => pydantic default True (observing)
            return True
        val = normalize_switch_value(raw)
        if val is None:
            # malformed/unreadable => fail-to-legacy => False
            return False
        return bool(val)

    def shadow_mode(self) -> bool:
        raw = self.resolver("ENSEMBLE_ENGINE_SHADOW_MODE")
        if raw is None:
            return True  # default True (shipped dormant posture)
        val = normalize_switch_value(raw)
        if val is None:
            # malformed => safe (shadow on => never invoke execute_fn)
            return True
        return bool(val)

    def job_killed(self, kill_switch_key: Optional[str]) -> bool:
        """Whether a per-job kill switch forces a skip (KILL_SWITCHED)."""
        if not kill_switch_key:
            return False
        raw = self.resolver(kill_switch_key)
        if raw is None:
            # absent per-job key => no switch active => not killed
            return False
        val = normalize_switch_value(raw)
        if val is None:
            # unreadable => KILL_SWITCHED (counted)
            return True
        return not bool(val)

    def source_value(self, key: str) -> Any:
        """The RAW injected-source value for ``key`` (``None`` when absent).

        The ABSENT-vs-EXPLICIT distinction the D3.6 ladder needs: an absent
        key means "this tier has no opinion" (the caller falls through to
        the next tier), while an explicit value is that tier's decision.

        The decision helpers above deliberately COLLAPSE the two (e.g.
        ``shadow_mode()`` defaults to the shipped posture when the key is
        absent), so a caller that must honour the config tier needs this
        raw read. Pure over the injected resolver (D3.12) — nothing is
        cached.
        """
        return self.resolver(key)

    def resolve_job(self, spec: Any) -> tuple[bool, Optional[str]]:
        """Return (should_run, skip_reason). skip_reason is KILL_SWITCHED or None."""
        if self.job_killed(getattr(spec, "kill_switch_key", None)):
            return False, "KILL_SWITCHED"
        if not self.engine_enabled():
            return False, "KILL_SWITCHED"
        return True, None


def kill_switch_from_env(environ: dict, key: str = "ENSEMBLE_ENGINE_ENABLED") -> bool:
    """Pure env-based resolver (boot_subprocess_sweep pattern).

    Absent/empty => ON-ish default for the engine? Fail-to-legacy: absent
    engine switch => engine does not own execution => False at runtime via
    the resolver. This helper mirrors the reaper's default-ON semantics for
    an arbitrary key: absent/empty => True, OFF values => False, else True.
    """
    val = environ.get(key) if isinstance(environ, dict) else None
    if val is None:
        return True
    s = str(val).strip().lower()
    if s == "":
        return True
    return s not in _OFF_VALUES


__all__ = [
    "EngineSwitchResolver",
    "kill_switch_from_env",
    "normalize_switch_value",
]
