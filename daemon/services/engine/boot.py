"""Engine boot phases (Engine Arc P4 — T4.1).

Four boot classes as a single enum (D4.1), deadlined awaited passes
(D4.2), and first-tick scheduling with deterministic-by-seed per-job
jitter (D4.4/D4.5) that desynchronises the 300s club.

ENGINE-INTERNAL — F-13.  The ``BOOT_SWEEP`` class here is a *new* concept
owned by this module.  It is NEVER wired into
``daemon/services/boot_subprocess_sweep.py``, NEVER reads or writes
``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES``, and NEVER installs a
pre-``load_config()`` boot hook.  The alias :class:`BootSweepClass` exists
so the class is nameable as ``BootSweepClass.BOOT_SWEEP`` without a second
``sweep_stale_subprocesses`` symbol.

No lifespan wiring here (P4's ``api.py`` block composes it); no readiness
component (F-8); no route (F-15).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger(__name__)


class BootClass(str, Enum):
    """The four engine-internal boot / first-tick classes (D4.1).

    * ``BOOT_SWEEP`` — awaited before the lifespan yield, hard deadline.
    * ``BOOT_PASS`` — awaited, best-effort, per-pass deadline.
    * ``FIRST_TICK_IMMEDIATE`` — the tick fires at t=0.
    * ``FIRST_TICK_DELAYED`` — the tick fires at t=offset (+ jitter).
    """

    BOOT_SWEEP = "boot_sweep"
    BOOT_PASS = "boot_pass"
    FIRST_TICK_IMMEDIATE = "first_tick_immediate"
    FIRST_TICK_DELAYED = "first_tick_delayed"


# F-13: the engine's BOOT_SWEEP class, named to avoid collision with the
# daemon's stale-subprocess sweep.  An alias keeps ``BootSweepClass.BOOT_SWEEP``
# and ``BootClass.BOOT_SWEEP`` as the very same member (no second enum family).
BootSweepClass = BootClass


async def run_boot_pass(
    coro: Awaitable[Any],
    deadline_s: float,
    pass_name: str = "boot_pass",
) -> Any:
    """Await ``coro`` under a wall-clock deadline (D4.2 / AC1).

    An expired pass is abandoned (``asyncio.wait_for`` cancels it), logged at
    ERROR naming the pass, and boot CONTINUES — a slow pass never aborts boot.
    A pass that raises is likewise logged and swallowed (best-effort posture).
    Returns the pass result, or ``None`` on expiry/failure.
    """
    try:
        return await asyncio.wait_for(coro, timeout=float(deadline_s))
    except asyncio.TimeoutError:
        logger.error(
            "engine boot pass %s exceeded deadline %.3fs — abandoning pass, "
            "boot CONTINUES",
            pass_name,
            float(deadline_s),
        )
        return None
    except asyncio.CancelledError:
        # A cancel from outside the pass (daemon shutdown) propagates — it is
        # not a pass failure and must not be swallowed here.
        raise
    except Exception as exc:  # noqa: BLE001 — best effort, never abort boot
        logger.error(
            "engine boot pass %s failed (%s: %s) — boot CONTINUES",
            pass_name,
            type(exc).__name__,
            exc,
        )
        return None


async def run_boot_sweep(
    passes: Iterable[tuple[str, Callable[[], Awaitable[Any]], float]],
) -> dict[str, Any]:
    """Run ``(name, coro_factory, deadline_s)`` passes sequentially.

    Every pass is individually deadlined via :func:`run_boot_pass`; one slow
    or failing pass cannot starve the others (D4.2's rejection of a single
    global boot deadline).  Returns ``name -> result`` (``None`` on expiry).
    """
    results: dict[str, Any] = {}
    for name, factory, deadline_s in passes or ():
        results[name] = await run_boot_pass(factory(), float(deadline_s), name)
    return results


def first_tick_jitter(
    job_id: str,
    jitter_seconds: Optional[float],
    seed: str = "",
) -> float:
    """Deterministic-by-seed per-job first-tick jitter (D4.4 / AC2).

    ``sha256(seed + ':' + job_id) % jitter`` — stable for a given
    ``(seed, job_id)`` (re-evaluation within one boot is identical), different
    across jobs, and recomputed each boot by supplying a fresh seed (the
    engine's ``boot_id``).  Returns ``0.0`` when jitter is unset/non-positive.
    """
    if jitter_seconds is None or float(jitter_seconds) <= 0.0:
        return 0.0
    digest = hashlib.sha256(f"{seed}:{job_id}".encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")
    return float(n % float(jitter_seconds))


def compute_first_tick_offset(
    job_id: str,
    trigger: Any,
    seed: str = "",
) -> float:
    """The initial due-time offset (seconds from the registration anchor).

    ``delayed`` triggers contribute ``first_tick_offset_seconds`` (D4.5 —
    drift's +30s becomes a first-class offset); every trigger with jitter
    contributes its deterministic jitter.  ``immediate``/unset contribute 0
    before jitter.
    """
    base = 0.0
    if getattr(trigger, "first_tick_kind", None) == "delayed":
        base = float(getattr(trigger, "first_tick_offset_seconds", None) or 0.0)
    jitter = getattr(trigger, "jitter_seconds", None)
    return base + first_tick_jitter(job_id, jitter, seed)


#: Dual-path spec metadata — the Phase-2 pilot (orphan-watcher).
#:
#: The blanket "P4 observer spec — execute_fn withheld" description is
#: FALSE for the migrated spec: the orphan-watcher spec's job IS executed
#: by the engine when the ownership conjunct holds
#: (``SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE`` ∧ engine enabled ∧ engine
#: shadow OFF). The spec RECORD still carries no callable (JobSpec has no
#: ``execute_fn`` field — the executor is attached to the engine's
#: execution loop at boot), so the description states BOTH postures
#: explicitly rather than implying the job never runs.
#:
#: The metadata is extensible by design (the Emperor's self-describing
#: registration ruling): ``service_tag`` / ``category`` / ``module_info``
#: already ride the spec; this adds the ownership contract an operator
#: needs to read the row.
#:
#: DEAD-DETECTION SAFETY (CS-9 carve-out): the observer dead rule keys
#: off ``job_id not in engine._executors`` (``daemon/services/engine/
#: metrics.py``), NOT off this metadata — so a truthful description here
#: cannot create a false-dead signal on a live lane.
_DUAL_PATH_SPEC_META: dict[str, dict[str, Any]] = {
    "orphan-watcher": {
        "description": (
            "P4 dual-path spec — the standalone orphan sweep owns unless "
            "the ownership conjunct holds (the per-job USE_ENGINE flag AND "
            "the engine enabled AND the engine NOT in shadow mode), in which "
            "case the engine attaches the sweep executor at boot and the "
            "legacy loop is suppressed (exactly one owner, fail-to-legacy "
            "on a boot-wiring failure). This spec record never carries the "
            "callable — JobSpec has no execute_fn field."
        ),
        "metadata": {
            "observer": True,
            "phase": "P4",
            "dual_path": True,
            "pilot": "orphan-watcher-engine",
            "legacy_owner": (
                "daemon.services.orphan_watcher_sweep.OrphanWatcherSweepService"
            ),
            "ownership_conjunct": (
                "per-job USE_ENGINE flag AND engine enabled AND NOT shadow mode"
            ),
        },
    },
}


def build_observer_specs(config: Any) -> tuple[Any, ...]:
    """Build the N dormant observer specs from the continuity table (D4.11/AMD-1).

    One Interval spec per ``CONTINUITY_JOBS`` row, cadence read from the live
    ``ServicesConfig`` knob (fallback: the recorded default).  ``JobSpec`` has
    NO ``execute_fn`` field, so dispatch is structurally impossible regardless
    of shadow/flag state — registration-for-observation is NOT migration.
    All ``SERVICES_*_USE_ENGINE`` flags stay unset (this helper never sets one).
    """
    from daemon.services.engine.estate import CONTINUITY_JOBS
    from daemon.services.engine.spec import IntervalTrigger, JobSpec, ServiceMeta

    services = getattr(config, "services", None)
    specs: list[Any] = []
    for row in CONTINUITY_JOBS:
        cadence = row.default_seconds
        if services is not None:
            cadence = getattr(services, row.services_attr, None) or cadence
        try:
            cadence = float(cadence)
        except (TypeError, ValueError):
            cadence = float(row.default_seconds)
        flag_key = (
            f"SERVICES_{row.service_tag.upper().replace('-', '_')}_USE_ENGINE"
        )
        dual = _DUAL_PATH_SPEC_META.get(row.job_id)
        if dual is None:
            description = (
                "P4 observer spec — execute_fn withheld; "
                "registration-for-observation, not migration"
            )
            metadata: dict[str, Any] = {"observer": True, "phase": "P4"}
        else:
            description = dual["description"]
            metadata = dict(dual["metadata"])
            # The flag name is DERIVED from the estate row (the same
            # expression that builds ``kill_switch_key``) so the two can
            # never drift.
            metadata["engine_flag"] = flag_key
        specs.append(
            JobSpec(
                id=row.job_id,
                meta=ServiceMeta(
                    service_tag=row.service_tag,
                    category=row.category,
                    service_name=row.job_id,
                    module_info=row.module_info,
                    description=description,
                    metadata=metadata,
                ),
                trigger=IntervalTrigger(seconds=cadence),
                max_runtime_s=max(cadence, 60.0),
                kill_switch_key=flag_key,
            )
        )
    return tuple(specs)


def describe_use_engine_flags(specs: Iterable[Any], environ: Any = None) -> str:
    """Truthful rendering of the ``SERVICES_*_USE_ENGINE`` family at boot.

    F1: the boot posture line must NOT claim "USE_ENGINE all unset" when a
    flag is actually set. This renders the REAL state of the family: the
    distinct ``kill_switch_key`` names carried by the registered specs
    (every observer spec names its module's USE_ENGINE seam), and which of
    those names carry a value right now.

    Shapes:

    * every key unset ⇒ ``"all unset (0/N)"``
    * some keys set ⇒ ``"SERVICES_A_USE_ENGINE=1, SERVICES_B_USE_ENGINE=0 (2/N)"``

    The value is collapsed to single spaces and truncated — it is
    operator-supplied text and must never break the log line.
    """
    env = os.environ if environ is None else environ
    keys = sorted(
        {str(getattr(s, "kill_switch_key", "") or "") for s in (specs or ())}
    )
    keys = [k for k in keys if k]
    set_entries: list[str] = []
    for key in keys:
        raw = env.get(key) if hasattr(env, "get") else None
        if raw is None:
            continue
        text = " ".join(str(raw).split())
        if len(text) > 32:
            text = text[:29] + "..."
        set_entries.append(f"{key}={text}")
    if not set_entries:
        return f"all unset (0/{len(keys)})"
    return f"{', '.join(set_entries)} ({len(set_entries)}/{len(keys)})"


class FirstTickScheduler:
    """Boot-scoped first-tick offset cache (stable within one boot).

    The seed is captured once at construction (the engine's ``boot_id``), so
    re-asking for a job's offset returns the identical value for the whole
    boot while a new boot (new seed) recomputes it.
    """

    def __init__(self, seed: Optional[str] = None) -> None:
        self._seed = seed or ""
        self._cache: dict[str, float] = {}

    @property
    def seed(self) -> str:
        return self._seed

    def offset_for(self, job_id: str, trigger: Any) -> float:
        if job_id not in self._cache:
            self._cache[job_id] = compute_first_tick_offset(
                job_id, trigger, self._seed
            )
        return self._cache[job_id]

    def compute_initial_due_times(
        self, specs: Iterable[Any]
    ) -> dict[str, float]:
        """Project ``job_id -> initial due offset`` for a set of specs."""
        out: dict[str, float] = {}
        for spec in specs or ():
            job_id = getattr(spec, "id", None)
            if job_id is None:
                continue
            out[str(job_id)] = self.offset_for(
                str(job_id), getattr(spec, "trigger", None)
            )
        return out


__all__ = [
    "BootClass",
    "BootSweepClass",
    "FirstTickScheduler",
    "build_observer_specs",
    "compute_first_tick_offset",
    "describe_use_engine_flags",
    "first_tick_jitter",
    "run_boot_pass",
    "run_boot_sweep",
]
