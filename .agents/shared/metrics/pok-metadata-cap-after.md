# POK D4 — metadata-cap AFTER measurement (post-cap renderer)

Captured 2026-11-07T10:11:23.930701+00:00 @ HEAD 0befdf0b4.
BEFORE numbers are read from the frozen baseline artifact (pre-edit capture, vendored-proven). AFTER numbers are the live `_format_project_json_section` post-edit.

```json
{
  "captured_at_utc": "2026-11-07T10:11:23.930701+00:00",
  "deltas": {
    "fixture_est_tokens_after": 3886,
    "fixture_est_tokens_before": 88248,
    "fixture_section_bytes_after": 15543,
    "fixture_section_bytes_before": 352991,
    "fixture_section_bytes_saved": 337448,
    "fixture_section_saved_pct": 95.6
  },
  "dev_real": {
    "after": {
      "payload_class": "ascii",
      "section_bytes": 704,
      "section_est_tokens": 176
    },
    "before": {
      "section_bytes": 704,
      "section_est_tokens": 176
    },
    "metadata_keys": 3,
    "row": "__system_default__ (the only metadata-bearing row in dev)",
    "source": "ensemble_dev @ 127.0.0.1:5432 (READ-ONLY SELECT, fence-asserted)"
  },
  "fixture": {
    "after": {
      "payload_class": "ascii",
      "section_bytes": 15543,
      "section_est_tokens": 3886
    },
    "allowlist_size_exact_names": 11,
    "before": {
      "section_bytes": 352991,
      "section_est_tokens": 88248
    },
    "before_recomputed_via_vendored": {
      "payload_class": "ascii",
      "section_bytes": 352991,
      "section_est_tokens": 88248
    },
    "metadata_basis_bytes": 279810,
    "metadata_keys": 69,
    "non_allowlisted_keys": 54,
    "omitted_count_tail": 49,
    "per_key_cap_chars": 2000,
    "ratio_percent": 68.45,
    "renderer_visible_basis_bytes": 352958,
    "section_cap_chars": 16384,
    "source": "tests/measurements/pok_d4_payload_fixture.py (design-calibrated)",
    "whole_json_basis_bytes": 408809
  },
  "head_at_capture": "0befdf0b4",
  "renderer_phase": "post-cap",
  "token_method": "ceil(chars/4); ASCII payloads so chars==bytes"
}
```
