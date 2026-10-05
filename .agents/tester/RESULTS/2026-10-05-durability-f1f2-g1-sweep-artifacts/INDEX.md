# G1 Sweep Artifacts Index — 2026-10-05

**Authoritative lane summary tables:** `.agents/tester/RESULTS/2026-10-04-durability-f1f2-merge-gate.md` §2 (G1 Full-Dir Sweep)

## Identity files (verbatim from /tmp/ac-gate/)

| File | Provenance |
|---|---|
| `g1-A-identity.txt` | Lane A G1 sweep — tests/unit/ sub-batches a-m (35 unique nodeids) |
| `g1-B-identity.txt` | Lane B G1 sweep — tests/unit/ sub-batches n-z (90 unique nodeids) |
| `g1-E-identity.txt` | Lane E G1 Full-Dir sweep — Chunks E1 (a-m), E2 (n-z), D1 (postgres) — 150 unique nodeids |
| `g1-C-identity.txt` | Lane C G1 sweep — C1 (29 unique) + C2b (1 hung) + C2d (9 [NEW?]) + C2e (4 [QUARANTINE-FAMILY]) — final 161 lines |

## Verdict + base identity

| File | Provenance |
|---|---|
| `g1-ab-verdict.md` | Final A/B verdict: PRE-EXISTING=283, BRANCH-CAUSED=8, DIVERGENT=7, TOTAL=298 (after C1+C2d/C2e+attestation appends) |
| `g1-ab-base-identity.txt` | Base worktree failure identity (18827dbd): 292 unique nodeids |
| `uv-lock-check.txt` | `git diff 18827dbd..fb0f4655 -- uv.lock` — EMPTY (venv-drift cause, not dep-pin delta) |
| `g1-ensure-concurrency.log` | Concurrency probe log for the chunked sweep |

## Base-side pytest run logs

| File | Provenance |
|---|---|
| `g1-ab-base-laneC.log` | 2 lane-C files at base: test_process_message_metrics.py + test_turn_reconciler.py (13 failed, 28 passed) |
| `g1-ab-base-laneC2.log` | 4 lane-C2 (C2d/C2e [NEW?]) files at base: test_spawn_intelligence_tier + test_spawn_default_unchanged + test_m2_missions_runtime_contract + test_mission_final_vocab_runtime (10 failed, 28 passed) |
| `g1-ab-base-attestation.log` | 1 attestation file at base with `--timeout-method=signal`: test_attestation_bound_escalation.py (1 failed: signal-mode 30s timeout — env-class) |

## Compressed chunk logs (lane A/B/C/E sub-batch raw pytest output)

| File | Source | Compressed bytes |
|---|---|---:|
| `g1-A1.log.gz` | /tmp/ac-gate/g1-A1.log (A lane sub-batch 1) | 3282 |
| `g1-A2.log.gz` | /tmp/ac-gate/g1-A2.log (A lane sub-batch 2) | 5815 |
| `g1-A3.log.gz` | /tmp/ac-gate/g1-A3.log (A lane sub-batch 3) | 1335 |
| `g1-B1.log.gz` | /tmp/ac-gate/g1-B1.log (B lane sub-batch 1) | 5958 |
| `g1-B2.log.gz` | /tmp/ac-gate/g1-B2.log (B lane sub-batch 2) | 5048 |
| `g1-B3.log.gz` | /tmp/ac-gate/g1-B3.log (B lane sub-batch 3) | 3836 |
| `g1-C1.log.gz` | /tmp/ac-gate/g1-C1.log (C1 chunk: tests/job_queue, tests/services, tests/repositories, tests/migration) | 9846 |
| `g1-C2.log.gz` | /tmp/ac-gate/g1-C2.log (C2 chunk — TIMEOUT, 1 hung test identified) | 2387 |
| `g1-C2b.log.gz` | /tmp/ac-gate/g1-C2b.log (C2b: tests/integration only — TIMEOUT, 1 hung test identified) | 4136 |
| `g1-C2d.log.gz` | /tmp/ac-gate/g1-C2d.log (C2d: tests/integration --deselect=attestation_hung) | 15256 |
| `g1-C2e.log.gz` | /tmp/ac-gate/g1-C2e.log (C2e: tests/opencode + e2e --ignore=hybrid + property --deselect=state_machine) | 1092 |
| `g1-D1.log.gz` | /tmp/ac-gate/g1-D1.log (D1: PG lane for E chunk) | 3793 |
| `g1-E1.log.gz` | /tmp/ac-gate/g1-E1.log (E lane sub-batch 1) | 9500 |
| `g1-E2.log.gz` | /tmp/ac-gate/g1-E2.log (E lane sub-batch 2) | 73690 |
| `g1-ab-base-default-batch00.log.gz` | A/B default lane batch00 (24 files) at base | 81661 |
| `g1-ab-base-default-batch01.log.gz` | A/B default lane batch01 (19 files) at base | 7968 |
| `g1-ab-base-default-batch02.log.gz` | A/B default lane batch02 (19 files) at base | 8425 |
| `g1-ab-base-pg.log.gz` | A/B PG lane at base | 2410 |

## Total artifact count
- 11 verbatim text files (identity + verdict + base identity + lane-C/C2/attestation logs + uv-lock check + ensure-concurrency)
- 14 gzipped chunk logs (A1-A3, B1-B3, C1-C2e, D1, E1-E2) + 4 gzipped A/B base batch logs
- 1 INDEX.md (this file)
- = **30 files total** in this directory
