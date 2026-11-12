"""Substrate tier — the DYNAMIC default-ceiling detector (arch §4.7f).

The detector is PURE and INJECTABLE — every fixture here is a mocked
``proc_root`` / ``cgroup_root``; the host is never read.
"""

from __future__ import annotations

import logging

import pytest

from daemon.services.ram_guard.ceiling import (
    CEILING_SOURCE_CGROUP,
    CEILING_SOURCE_EXPLICIT,
    CEILING_SOURCE_FALLBACK_FIXED,
    CEILING_SOURCE_MEMINFO,
    MIB,
    RAM_GUARD_DERIVED_CEILING_FLOOR_BYTES,
    RAM_GUARD_DETECTION_FALLBACK_BYTES,
    derive_ceiling_bytes,
    detect_effective_total_bytes,
    read_cgroup_limit_bytes,
    resolve_ceiling_bytes,
)
from tests.unit.services.ram_guard_fixtures import write_proc_tree

MIB_ = MIB


def build_host(
    tmp_path,
    *,
    mem_total_mib: int,
    cgroup_path: str | None = None,
    cgroup_max: str | None = None,
    cgroup_limit_bytes: int | None = None,
):
    """A mocked host: a ``proc_root`` (+ an optional cgroup v2 limit)."""
    proc_root = write_proc_tree(
        tmp_path / "proc",
        [],
        mem_total_kb=mem_total_mib * 1024,
        cgroup_lines=[f"0::{cgroup_path}"] if cgroup_path is not None else None,
    )
    cgroup_root = tmp_path / "cgroup"
    if cgroup_path is not None and (cgroup_max is not None or cgroup_limit_bytes is not None):
        unit_dir = cgroup_root / cgroup_path.lstrip("/")
        unit_dir.mkdir(parents=True, exist_ok=True)
        if cgroup_max is not None:
            (unit_dir / "memory.max").write_text(cgroup_max + "\n")
        if cgroup_limit_bytes is not None:
            (unit_dir / "memory.limit_in_bytes").write_text(str(cgroup_limit_bytes) + "\n")
    return proc_root, cgroup_root


# ── The detection (§4.7a) ──────────────────────────────────────────────────


def test_plain_host_derives_seventy_percent_of_memtotal(tmp_path):
    """A plain host ⇒ 70 % of MemTotal."""
    proc_root, cgroup_root = build_host(tmp_path, mem_total_mib=11953)
    effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    assert effective == 11953 * MIB
    assert source == CEILING_SOURCE_MEMINFO
    resolved, ceiling_source = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved == int(0.7 * 11953 * MIB)
    assert ceiling_source == CEILING_SOURCE_MEMINFO


def test_constrained_cgroup_derives_seventy_percent_of_the_limit(tmp_path):
    """A constrained cgroup ⇒ 70 % of the LIMIT, not of the host."""
    proc_root, cgroup_root = build_host(
        tmp_path,
        mem_total_mib=11953,
        cgroup_path="/system.slice/ensemble-main.service",
        cgroup_max=str(4 * 1024 * MIB),
    )
    effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    assert effective == 4 * 1024 * MIB
    assert source == CEILING_SOURCE_CGROUP
    resolved, _ = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved == int(0.7 * 4 * 1024 * MIB)


def test_unlimited_cgroup_derives_seventy_percent_of_memtotal(tmp_path):
    """An unlimited cgroup (``max``) ⇒ 70 % of MemTotal."""
    proc_root, cgroup_root = build_host(
        tmp_path,
        mem_total_mib=11953,
        cgroup_path="/system.slice/ensemble-main.service",
        cgroup_max="max",
    )
    effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    assert effective == 11953 * MIB
    assert source == CEILING_SOURCE_MEMINFO


def test_cgroup_v1_limit_is_the_fallback(tmp_path):
    """The cgroup v1 ``memory.limit_in_bytes`` is the fallback."""
    proc_root, cgroup_root = build_host(
        tmp_path,
        mem_total_mib=11953,
        cgroup_path="/system.slice/ensemble-main.service",
        cgroup_limit_bytes=2 * 1024 * MIB,
    )
    effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    assert effective == 2 * 1024 * MIB
    assert source == CEILING_SOURCE_CGROUP


def test_cgroup_v1_unlimited_sentinel_is_not_a_limit(tmp_path):
    """The cgroup v1 "unlimited" sentinel is not a limit."""
    proc_root, cgroup_root = build_host(
        tmp_path,
        mem_total_mib=11953,
        cgroup_path="/system.slice/ensemble-main.service",
        cgroup_limit_bytes=9223372036854771712,
    )
    effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    assert effective == 11953 * MIB
    assert source == CEILING_SOURCE_MEMINFO


# ── The clamp interaction (§4.7c) ──────────────────────────────────────────


def test_derived_value_is_never_upper_capped(tmp_path):
    """A >91 GiB host derives a value ABOVE 65536 MiB — no abort."""
    proc_root, cgroup_root = build_host(tmp_path, mem_total_mib=100 * 1024)
    resolved, _ = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved > 65536 * MIB
    assert resolved == int(0.7 * 100 * 1024 * MIB)


def test_derived_value_is_floored_at_64_mib(tmp_path):
    """A sub-64 MiB effective total ⇒ the 64 MiB floor."""
    proc_root, cgroup_root = build_host(tmp_path, mem_total_mib=32)
    resolved, _ = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved == RAM_GUARD_DERIVED_CEILING_FLOOR_BYTES == 67108864


def test_derive_ceiling_bytes_is_floored_and_never_capped():
    """The pure derivation — floored, never capped."""
    assert derive_ceiling_bytes(0) == 67108864
    assert derive_ceiling_bytes(100 * 1024 * MIB) == int(0.7 * 100 * 1024 * MIB)
    assert derive_ceiling_bytes(1000 * 1024 * MIB) > 65536 * MIB


# ── The explicit path + the detection failure ──────────────────────────────


def test_explicit_ceiling_overrides_the_derived_value(tmp_path):
    """An explicit knob overrides the derived VALUE."""
    proc_root, cgroup_root = build_host(tmp_path, mem_total_mib=11953)
    resolved, _ = resolve_ceiling_bytes(proc_root, cgroup_root, ceiling_mib=3072)
    assert resolved == 3072 * MIB


def test_the_explicit_ceiling_path_reports_explicit(tmp_path):
    """§18.6 — ``ceiling_source`` is ``explicit`` on the explicit path.

    One-branch capture at the resolution site: an explicit ``ceiling_mib``
    never runs detection, never warns, never falls back.
    """
    proc_root, cgroup_root = build_host(tmp_path, mem_total_mib=11953)
    resolved, source = resolve_ceiling_bytes(proc_root, cgroup_root, ceiling_mib=3072)
    assert resolved == 3072 * MIB
    assert source == CEILING_SOURCE_EXPLICIT


def test_detection_failure_yields_the_fixed_fallback_and_one_warn(tmp_path, caplog):
    """A failed detection ⇒ the fixed fallback + ONE WARN; never an abort."""
    proc_root = tmp_path / "no-such-proc"
    cgroup_root = tmp_path / "cgroup"
    with caplog.at_level(logging.WARNING):
        resolved, source = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved == RAM_GUARD_DETECTION_FALLBACK_BYTES == 3221225472
    assert source == CEILING_SOURCE_FALLBACK_FIXED
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_a_non_utf8_meminfo_never_raises(tmp_path, caplog):
    """D4 — a non-UTF-8 meminfo must not escape the detector.

    ``UnicodeDecodeError`` is a ``ValueError``, NOT an ``OSError`` — an
    ``except OSError`` alone let it escape the "NEVER raises / never
    aborts boot" contract.  The decode class is swallowed ⇒ the fixed
    fallback + exactly ONE WARN.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True, exist_ok=True)
    (proc_root / "meminfo").write_bytes(b"MemTotal: \xff\xfe 12239768 kB\n")
    cgroup_root = tmp_path / "cgroup"
    with caplog.at_level(logging.WARNING):
        resolved, source = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved == RAM_GUARD_DETECTION_FALLBACK_BYTES
    assert source == CEILING_SOURCE_FALLBACK_FIXED
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_a_non_utf8_cgroup_file_never_raises(tmp_path):
    """D4 — the same for the cgroup file (the decode class is swallowed)."""
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True, exist_ok=True)
    (proc_root / "meminfo").write_text("MemTotal:       12239768 kB\n")
    (proc_root / "self").mkdir(exist_ok=True)
    (proc_root / "self" / "cgroup").write_bytes(b"0::/system.slice/\xff\xfe\n")
    effective, source = detect_effective_total_bytes(proc_root, tmp_path / "cgroup")
    assert effective == 12239768 * 1024
    assert source == CEILING_SOURCE_MEMINFO


def test_detection_failure_never_raises(tmp_path):
    """A failed detector must not abort the boot."""
    proc_root = tmp_path / "no-such-proc"
    cgroup_root = tmp_path / "cgroup"
    resolved, source = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved > 0
    assert source in {
        CEILING_SOURCE_MEMINFO,
        CEILING_SOURCE_CGROUP,
        CEILING_SOURCE_FALLBACK_FIXED,
        CEILING_SOURCE_EXPLICIT,
    }


def test_cgroup_memory_max_zero_is_a_real_limit_not_unlimited(tmp_path):
    """Cycle-2 pin — ``memory.max = 0`` is a REAL limit (0 bytes), never
    the cgroup-v2 unlimited sentinel (only the literal ``max`` is).  The
    effective total floors to 0 ⇒ the derived ceiling floors to 64 MiB
    with the ``derived-cgroup`` provenance."""
    proc_root, cgroup_root = build_host(
        tmp_path,
        mem_total_mib=11953,
        cgroup_path="/system.slice/ensemble-main.service",
        cgroup_max="0",
    )
    limit = read_cgroup_limit_bytes(proc_root, cgroup_root)
    assert limit == 0, "0 is a real limit — never parsed as unlimited"
    effective, source = detect_effective_total_bytes(proc_root, cgroup_root)
    assert effective == 0
    assert source == CEILING_SOURCE_CGROUP
    resolved, ceiling_source = resolve_ceiling_bytes(proc_root, cgroup_root)
    assert resolved == RAM_GUARD_DERIVED_CEILING_FLOOR_BYTES == 64 * MIB
    assert ceiling_source == CEILING_SOURCE_CGROUP
