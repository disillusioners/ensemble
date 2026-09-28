"""Shared byte-pin sentinels for the maintenance-checkpoint-cleanup tests.

Pin the byte-magnitude window that overflowed PG int4 on 2026-09-28
(see ``docs/2026-09-28-checkpoint-cleanup-500-int4-overflow.md``).
Used by both the unit suite
(``tests/unit/services/test_maintenance_checkpoint_cleanup_service.py``)
and the integration suite
(``tests/integration/test_maintenance_checkpoint_cleanup_api.py``);
hoisted here so the values stay byte-identical across both files.

* ``_INCIDENT_EXPECTED_BYTES`` — exact byte count from the incident
  log evidence (27,233,813,846 bytes ≈ 27.2 GiB dry-run echo at
  execute-time, which overflowed int4 and produced HTTP 500).
* ``_INT4_MAX`` — 2^31 - 1 = 2,147,483,647 (the largest value PG int4
  accepts).
* ``_JUST_OVER_INT4`` — 2^31 (the first value that overflows int4).
"""
from __future__ import annotations

# Incident value — the dry-run's orphaned-blob layer hit 27.2 GiB; the
# execute payload echoed the same value, which overflowed PG int4 and
# produced HTTP 500. Pinning the exact number anchors the regression
# test against future refactors.
_INCIDENT_EXPECTED_BYTES = 27_233_813_846
# Threshold: 2^31 is the first value that overflows int4 (max 2^31-1).
# Below this the legacy int4 column accepts the value; at or above it
# the legacy column raises NumericValueOutOfRange.
_INT4_MAX = 2**31 - 1
_JUST_OVER_INT4 = 2**31

__all__ = [
    "_INCIDENT_EXPECTED_BYTES",
    "_INT4_MAX",
    "_JUST_OVER_INT4",
]