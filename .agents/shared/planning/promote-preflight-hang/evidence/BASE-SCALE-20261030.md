# BASE O(N²) journal-read cost — bounded k·N² measurement (wave 2)

Date: 2026-10-30 (01:41:49Z–02:00:42Z UTC) · Host: Linux 6.8.0-147-generic, 8 cores
Script: `tests/packs/preflight_base_scale_measure.sh` (committed, phase 0)
Raw log: `/tmp/pf-base-scale-20261030.log` (copy below verbatim)
Oracle: verbatim `journal_read` body from `b1183a637:scripts/upgrade/lib.sh` (git objects, never re-typed)

## Protocol (leader bounded ruling, wave-2 dispatch)

* Samples in the 55–222 KB band, each under a **hard** `timeout -k 10 1500` wrapper —
  the legacy walk is NEVER run unbounded (the prior executor's 28-minute foreground
  run was killed and its evidence lost; the 455,783 B leg is NEVER executed).
* Metric: **USER CPU seconds** (`/usr/bin/time -p`) — robust to ambient load.
  Two unrelated CPU spinners (~2 cores) were running on this host throughout
  (noted, untouched); wall-clock is recorded as advisory only.
* Fit: least-squares through the origin, T_user = k·N².
* The incident-size cost is an **EXTRAPOLATION, labelled as such**.

## Legs

| N (bytes) | user CPU (s) | sys (s) | wall (s) | k = T/N² (s/B²) | rc |
|---|---|---|---|---|---|
| 56,640   | 53.38  | 2.38  | 55.79   | 1.664e-8 | 0 |
| 111,072  | 203.16 | 11.82 | 215.03  | 1.647e-8 | 0 |
| 221,184  | 804.09 | 56.67 | 861.49  | 1.644e-8 | 0 |

## Fit + extrapolation

* **k (LSQ through origin) = 1.644e-8 s/B²** — per-point k is tight (1.644–1.664e-8,
  ±0.6% over a 4× size range): the cost model is cleanly quadratic.
* **EXTRAPOLATED T_user(455,783 B) = 3,415 s ≈ 56.9 min** [LABEL: EXTRAPOLATED — never run]
  — inside the established 45–61 min band (evidence/FORENSICS.md §4-1 predicted
  ~3,670 s / 61 min from the hung-executor observation; the wave-1 curve predicted
  ~45 min; this measurement pins it at ≈57 min with a labelled extrapolation).
* Symptom cross-check (measured, not extrapolated): the repro pack's BASE leg on the
  real 455,783 B fixture does NOT complete within its 60 s budget (rc=124, zero
  stdout, full-budget CPU spin) — the 2026-10-28 symptom reproduces.

## HEAD comparison (measured this wave)

| Operation (lib.sh path, incident fixture 455,783 B) | BASE | HEAD | ratio |
|---|---|---|---|
| `journal_read` | EXTRAPOLATED 3,415 s user | **149 ms** | ≈ 22,900× |
| `journal_read` (helper level) | — | 84 ms | — |
| `journal_update` (full RMW) | (read + own O(N²) scanner ⇒ > 2× read) | **598 ms** | — |
| `_json_sub` (full journal) | (O(N²) twice on the extracted object) | **405 ms** | — |

## Verbatim log tail

    2026-10-30T01:41:49Z LEG n=56640 START (hard cap 1500s)
    2026-10-30T01:42:45Z LEG n=56640 DONE rc=0 wall_ms=55838 time(real=55.79s user=53.38s sys=2.38s) outbytes=56640
    2026-10-30T01:42:45Z LEG n=111072 START (hard cap 1500s)
    2026-10-30T01:46:20Z LEG n=111072 DONE rc=0 wall_ms=215074 time(real=215.03s user=203.16s sys=11.82s) outbytes=111072
    2026-10-30T01:46:20Z LEG n=221184 START (hard cap 1500s)
    2026-10-30T02:00:42Z LEG n=221184 DONE rc=0 wall_ms=861532 time(real=861.49s user=804.09s sys=56.67s) outbytes=221184
    FIT points (bytes -> user-CPU s): 56640 -> 53.38, 111072 -> 203.16, 221184 -> 804.09
    FIT k(n=56640) = 1.664e-08 s/B²
    FIT k(n=111072) = 1.647e-08 s/B²
    FIT k(n=221184) = 1.644e-08 s/B²
    FIT k (LSQ through origin) = 1.644e-08 s/B²
    EXTRAPOLATED T_user(455783 B) = 3415 s = 56.9 min  [LABEL: EXTRAPOLATED — never run]
    NOTE: wall-clock on this host is advisory (ambient CPU spinners); the fit uses USER CPU only
    2026-10-30T02:00:42Z MEASUREMENT COMPLETE — every number above 455783 B is EXTRAPOLATED, never measured
