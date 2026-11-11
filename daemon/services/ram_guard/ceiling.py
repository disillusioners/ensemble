"""``ceiling`` — the DYNAMIC default-ceiling detector.

Contract: architecture-recommendation.md §4.7 (the dynamic default —
USER RULING #2's mechanism), §15 B24 (the resolution contract, pinned
char-for-character), phase2-plan.md T25.  Phase 2 owns this module
(§15 B15: "Phase 2 owns the event HOOK, the DQ6 consumer-gate artifact
(pins-only), the execution-lane thread, and the ceiling detector
(ceiling.py).").

**The resolution contract (§15 B24, verbatim):**

    detect_effective_total_bytes(proc_root: Path, cgroup_root: Path) -> tuple[int, str]
    resolved_ceiling_bytes = max(67108864, int(0.7 * effective_total_bytes))
    effective_total_bytes = min(MemTotal_bytes, cgroup_limit_bytes) when the cgroup limit is finite, else MemTotal_bytes
    ceiling_mib unset ⇒ derived; explicit ⇒ validated [64, 65536] — the clamp NEVER applies to the derived value
    detection failure ⇒ resolved_ceiling_bytes = 3221225472 (3072 MiB) + one WARN; never aborts boot
    limit_bytes = the RESOLVED ceiling (derived or explicit)

**Pure + injectable** (the §4.1 discipline): both roots are arguments;
unit tests never touch the host.

**The clamp interaction (§4.7c).**  ``[64, 65536]`` validates EXPLICIT
values only.  The derived value is FLOORED at 64 MiB and NEVER
upper-capped — capping at 64 GiB would kill a legitimate 100 GiB job on
a 200 GiB host, against USER RULING #1's binding intent (disaster
prevention, not a constraint on legitimate heavy work).  On hosts
> ~91 GiB the derived value legitimately exceeds 65536 MiB; that is
correct, not a defect.

**Timing (§4.7b).**  ONE boot-time snapshot: the resolved ceiling +
the source are stable for the daemon's lifetime; a restart re-derives.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module constants (§4.7 / §15 B24 — NOT config knobs) ────────────────────

#: The dynamic default is 70 % of the detected effective total RAM
#: (USER RULING #2).
RAM_GUARD_DERIVED_CEILING_RATIO = 0.7

#: The derived value is floored at 64 MiB (a pathological-host guard).
RAM_GUARD_DERIVED_CEILING_FLOOR_BYTES = 67108864

#: Detection failure ⇒ this fixed value + one WARN; never aborts boot.
RAM_GUARD_DETECTION_FALLBACK_BYTES = 3221225472  # 3072 MiB

#: The ceiling sources (§15 B23: ``ceiling_source ∈ {meminfo, cgroup,
#: fallback-fixed}``).
CEILING_SOURCE_MEMINFO = "meminfo"
CEILING_SOURCE_CGROUP = "cgroup"
CEILING_SOURCE_FALLBACK_FIXED = "fallback-fixed"

#: Bytes per MiB.
MIB = 1024 * 1024

#: A cgroup v1 "unlimited" limit is a huge sentinel, not a small
#: number — anything at or above this is treated as unlimited.
_CGROUP_V1_UNLIMITED_FLOOR = 1 << 62


class CeilingDetectionError(RuntimeError):
    """The effective total RAM could not be detected."""


def read_mem_total_bytes(proc_root: Path) -> Optional[int]:
    """``MemTotal`` from ``<proc_root>/meminfo`` → bytes (or ``None``).

    A read error **and** a decode error (a non-UTF-8 file raises
    ``UnicodeDecodeError`` — a ``ValueError``, NOT an ``OSError``) both
    yield ``None``: the detector must never raise out of here.
    """
    try:
        text = (Path(proc_root) / "meminfo").read_text()
    except (OSError, ValueError):
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split(":", 1)[1].split()
            if not parts:
                return None
            try:
                return int(parts[0]) * 1024
            except (ValueError, TypeError):
                return None
    return None


def _cgroup_relative_path(proc_root: Path) -> Optional[tuple[str, str]]:
    """The daemon's own cgroup path → ``(kind, relative_path)``.

    ``kind`` is ``"v2"`` for the unified ``0::<path>`` line, ``"v1"``
    for a controller line.  ``None`` when the file is unreadable or
    carries no usable line.
    """
    try:
        text = (Path(proc_root) / "self" / "cgroup").read_text()
    except (OSError, ValueError):
        # A decode error (a non-UTF-8 file) is unreadable — the
        # detector must never raise out of here.
        return None
    v1_path: Optional[str] = None
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, path = parts
        if hierarchy == "0" and not controllers:
            return ("v2", path)
        if controllers == "memory" and v1_path is None:
            v1_path = path
    if v1_path is not None:
        return ("v1", v1_path)
    return None


def _read_cgroup_limit_file(path: Path) -> Optional[int]:
    """Read one cgroup limit file → bytes (or ``None`` when unlimited).

    A decode error (a non-UTF-8 file raises ``UnicodeDecodeError`` — a
    ``ValueError``, NOT an ``OSError``) is treated as "unreadable" ⇒
    unlimited; the detector must never raise out of here.
    """
    try:
        raw = path.read_text().strip()
    except (OSError, ValueError):
        return None
    if not raw:
        return None
    if raw == "max":
        return None  # cgroup v2 unlimited
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return None
    if value <= 0 or value >= _CGROUP_V1_UNLIMITED_FLOOR:
        return None  # v1 "unlimited" sentinel
    return value


def read_cgroup_limit_bytes(proc_root: Path, cgroup_root: Path) -> Optional[int]:
    """The daemon's own cgroup memory limit → bytes (or ``None``).

    cgroup v2: ``<proc_root>/self/cgroup`` → ``<cgroup_root>/<path>/memory.max``
    (the literal ``max`` or an absent file ⇒ unlimited).  cgroup v1
    fallback: ``memory.limit_in_bytes``.
    """
    located = _cgroup_relative_path(proc_root)
    if located is None:
        return None
    _kind, rel = located
    base = Path(cgroup_root) / rel.lstrip("/")
    limit = _read_cgroup_limit_file(base / "memory.max")
    if limit is not None:
        return limit
    limit = _read_cgroup_limit_file(base / "memory.limit_in_bytes")
    if limit is not None:
        return limit
    # cgroup v1 mounts the controller trees under ``memory/``.
    return _read_cgroup_limit_file(Path(cgroup_root) / "memory" / rel.lstrip("/") / "memory.limit_in_bytes")


def detect_effective_total_bytes(proc_root: Path, cgroup_root: Path) -> tuple[int, str]:
    """The host's EFFECTIVE total RAM → ``(bytes, source)``.

    ``effective_total_bytes = min(MemTotal_bytes, cgroup_limit_bytes)``
    when the cgroup limit is finite, else ``MemTotal_bytes`` — a
    constrained cgroup therefore never derives 70 % of the host.

    ``source`` is ``"cgroup"`` when a finite cgroup limit BOUND the
    effective total, else ``"meminfo"``.

    Raises :class:`CeilingDetectionError` when ``MemTotal`` cannot be
    read — the caller resolves that to the fixed fallback (never a boot
    abort).
    """
    mem_total = read_mem_total_bytes(proc_root)
    if mem_total is None:
        raise CeilingDetectionError(f"MemTotal unreadable under {proc_root}")
    cgroup_limit = read_cgroup_limit_bytes(proc_root, cgroup_root)
    if cgroup_limit is None:
        return mem_total, CEILING_SOURCE_MEMINFO
    effective = min(mem_total, cgroup_limit)
    source = CEILING_SOURCE_CGROUP if cgroup_limit < mem_total else CEILING_SOURCE_MEMINFO
    return effective, source


def derive_ceiling_bytes(effective_total_bytes: int) -> int:
    """``max(67108864, int(0.7 * effective_total_bytes))`` — never capped."""
    derived = int(RAM_GUARD_DERIVED_CEILING_RATIO * effective_total_bytes)
    return max(RAM_GUARD_DERIVED_CEILING_FLOOR_BYTES, derived)


def resolve_ceiling_bytes(
    proc_root: Path,
    cgroup_root: Path,
    *,
    ceiling_mib: Optional[int] = None,
) -> tuple[int, str]:
    """The boot-time resolution → ``(resolved_ceiling_bytes, ceiling_source)``.

    NEVER raises — a failed detector must not disarm or kill the daemon;
    the guard stays armed at the conservative fixed figure
    (``3221225472``) and ONE WARN is emitted.

    An EXPLICIT ``ceiling_mib`` overrides the derived VALUE (the
    ``[64, 65536]`` validation is the config layer's job — arch §4.6).
    The pinned source set is ``{meminfo, cgroup, fallback-fixed}`` and
    has no ``explicit`` member, so an explicit ceiling keeps reporting
    the DETECTED host context (the effective total's source); the
    resolved VALUE is the explicit ceiling either way.
    """
    try:
        effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    except Exception as exc:  # NEVER raises — a failed detector must not
        # abort the boot (arch §4.7f).  The readers swallow the decode
        # class themselves; this is the outer guarantee.
        logger.warning(
            "ram-guard: ceiling detection failed (%s); using the fixed "
            "fallback %d bytes (3072 MiB)",
            exc,
            RAM_GUARD_DETECTION_FALLBACK_BYTES,
        )
        if ceiling_mib is not None:
            return ceiling_mib * MIB, CEILING_SOURCE_FALLBACK_FIXED
        return RAM_GUARD_DETECTION_FALLBACK_BYTES, CEILING_SOURCE_FALLBACK_FIXED
    if ceiling_mib is not None:
        return ceiling_mib * MIB, source
    return derive_ceiling_bytes(effective), source


__all__ = [
    "CEILING_SOURCE_CGROUP",
    "CEILING_SOURCE_FALLBACK_FIXED",
    "CEILING_SOURCE_MEMINFO",
    "MIB",
    "RAM_GUARD_DERIVED_CEILING_FLOOR_BYTES",
    "RAM_GUARD_DERIVED_CEILING_RATIO",
    "RAM_GUARD_DETECTION_FALLBACK_BYTES",
    "CeilingDetectionError",
    "derive_ceiling_bytes",
    "detect_effective_total_bytes",
    "read_cgroup_limit_bytes",
    "read_mem_total_bytes",
    "resolve_ceiling_bytes",
]
