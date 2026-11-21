"""Dual-path harness (Engine Arc Phase 3 — T3.3).

``SERVICES_<MODULE>_USE_ENGINE`` is a NEW flag family (D3.7): ONE flag per
module; OFF (or unset) ⇒ the engine unregisters the job and the module's
legacy loop starts; ON ⇒ the legacy loop does not start and the engine owns
it. Both code paths stay compiled (D3.7/D3.8); rollback is one flag.

No ``ServicesConfig`` edit (F-9) — flags are read from the environment via
``use_engine_enabled``.

Wiring owner: P4 — T4.4 wires the dual-path flags through EngineService;
until then (D4.11 dormancy) both paths stay legacy-default, not live.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


def _env(environ: Any) -> dict:
    if environ is None:
        return os.environ
    return environ


def use_engine_enabled(module_slug: str, environ: Any = None) -> bool:
    """Read ``SERVICES_<MODULE>_USE_ENGINE``. Default OFF/unset (D3.8)."""
    env = _env(environ)
    key = "SERVICES_{}_USE_ENGINE".format(module_slug.upper())
    raw = env.get(key) if hasattr(env, "get") else None
    if raw is None:
        return False
    s = str(raw).strip().lower()
    if s == "":
        return False
    return s in ("1", "true", "yes", "on")


@dataclass
class EngineOwnership:
    """Single decision record consulted by BOTH paths (D3.7)."""

    module_slug: str
    use_engine: bool

    @property
    def engine_owns(self) -> bool:
        return bool(self.use_engine)

    @property
    def legacy_owns(self) -> bool:
        return not bool(self.use_engine)

    def decide(self) -> str:
        return "engine" if self.use_engine else "legacy"


def ownership_for(module_slug: str, environ: Any = None) -> EngineOwnership:
    return EngineOwnership(
        module_slug=module_slug, use_engine=use_engine_enabled(module_slug, environ)
    )


class FixtureModule:
    """A fixture module proving both paths (T3.3).

    Holds a legacy tick counter and an engine run counter. ``tick()`` increments
    exactly ONE of them per call based on the current ownership (runtime flip
    moves ownership on the next tick, no restart).
    """

    def __init__(self, slug: str = "fixture", environ: Any = None) -> None:
        self.slug = slug
        self._environ = environ
        self.legacy_ticks: int = 0
        self.engine_runs: int = 0
        self.registered_with_engine: bool = False

    def _ownership(self) -> EngineOwnership:
        return ownership_for(self.slug, self._environ)

    def set_environ(self, environ: Any) -> None:
        self._environ = environ

    def tick(self) -> str:
        own = self._ownership()
        if own.engine_owns:
            self.engine_runs += 1
            self.registered_with_engine = True
            return "engine"
        else:
            self.legacy_ticks += 1
            self.registered_with_engine = False
            return "legacy"

    def reset(self) -> None:
        self.legacy_ticks = 0
        self.engine_runs = 0
        self.registered_with_engine = False


__all__ = [
    "EngineOwnership",
    "FixtureModule",
    "ownership_for",
    "use_engine_enabled",
]
