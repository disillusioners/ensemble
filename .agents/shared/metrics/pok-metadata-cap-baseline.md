# POK D4 — metadata-cap BASELINE (BEFORE truth, pre-cap renderer)

Captured 2026-11-07T09:43:57.484509+00:00 @ HEAD 3cbf42721 (pre-edit capture; vendored renderer proven byte-identical to the live pre-cap `_format_project_json_section`).

Two payloads: the DEV DB's REAL row (ensemble_dev, read-only, fence-asserted — the dev DB carries only `__system_default__`, tiny) and the design-calibrated fixture (bases pinned to the design-recorded live figures; content synthetic, size distribution design-faithful). LIVE re-measure stays gated on Phase 1.5.

```json
{
  "captured_at_utc": "2026-11-07T09:43:57.484509+00:00",
  "design_recorded": {
    "metadata_basis_bytes": 279810,
    "note": "LIVE bases (arch \u00a72 row 3 / \u00a74.8); live row fenced from this lane",
    "ratio_percent": 68.4,
    "whole_json_basis_bytes": 408809
  },
  "dev_real": {
    "metadata_keys": 3,
    "payload_class": "ascii",
    "row": "__system_default__ (the only metadata-bearing row in dev)",
    "section_bytes": 704,
    "section_est_tokens": 176,
    "source": "ensemble_dev @ 127.0.0.1:5432 (READ-ONLY SELECT, fence-asserted)"
  },
  "fixture": {
    "design_metadata_basis_bytes": 279810,
    "design_ratio_percent": 68.4,
    "design_whole_json_basis_bytes": 408809,
    "metadata_basis_bytes": 279810,
    "metadata_keys": 69,
    "payload_class": "ascii",
    "ratio_percent": 68.45,
    "section_bytes": 408842,
    "section_est_tokens": 102211,
    "source": "tests/measurements/pok_d4_payload_fixture.py (design-calibrated)",
    "whole_json_basis_bytes": 408809
  },
  "head_at_capture": "3cbf42721",
  "renderer_phase": "pre-cap",
  "token_method": "ceil(chars/4); ASCII payloads so chars==bytes",
  "vendored_matches_live": true
}
```
