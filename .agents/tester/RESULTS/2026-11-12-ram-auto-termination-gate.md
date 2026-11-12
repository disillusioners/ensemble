# RAM Auto-Termination — Functional Verification Gate (FINAL)

- **Date:** 2026-11-12
- **Verdict:** **PASS-WITH-DISCLOSURES — READY FOR STAGING** (no live promote)
- **Worktree:** `/home/nea/ensemble-src-wt-ram-auto-termination`
- **Branch / HEAD:** `feature/ram-auto-termination` @ `2f531319b39c3d0ac54756b46625a55c1493f484`
- **Feature diff:** `2612ae5ba..2f531319b` (50 files, +16984/−155)
- **Base for the dual-side leg:** `f3259c2a3` (independent detached worktree, own venv, CPython 3.14.7)
- **Tier:** 2 (mechanical — `daemon/api.py` on the core-seam list)
- **Workers used:** 11 (recon, 5 unit/regression packs, 3 matrix groups, base prep, envelope probe, base-leg resume) · 0 test-code edits · 0 commits · 0 live contact

## Requirement scoreboard

| # | Requirement | Result | Evidence |
|---|---|---|---|
| 1 | Real balloon kill, bounded (MiB-scale, tight teardown) | **PASS** | `ram_guard_bash_real` 74/74 + `ram_guard_proc_real` 55/55; post-kill `killpg(pgid,0)` → ProcessLookupError; no survivor in `pgrep`/`ps` |
| 2 | Under-limit unaffected (byte-identical) | **PASS** | `TestNoFalseAttribution::test_the_under_limit_output_is_byte_identical_to_the_base_commit`; proc `TestUnderLimitByteIdentical` golden |
| 3 | Kill-switch OFF byte-identical; no threads; OFF+out-of-range no abort | **PASS** | `TestKillSwitch` (15) + `test_off_starts_neither_thread` + K5 zero-DB pin + config OFF-beats-validation |
| 4 | Config ladder | **PASS** | 58/58 config+router; every rung mapped to a node id (see rung map below); 3072 MiB fallback + one WARN and “derived never upper-capped” live in `ram_guard_ceiling` (PASS 15/15) |
| 5 | Agent-visible error | **PASS** | Envelope captured verbatim (below): deciding value `confirmed_pss_bytes=135899136 ≥ limit_bytes=67108864`; message names the limit; argv/secret scan 0 |
| 6 | Both lanes end-to-end | **PASS** | bash 74/74; proc 55/55 with added `ram_breach:` line + `ram_killed: true`, still exactly 3 tasks |
| 7 | Matrix RED→GREEN proof (disclosure 6) | **PASS** | **19/19 legs DISCRIMINATING**, each RED at `rc=1`, every restore sha256-identical, no plant remained |
| 8 | Net-new reds = 0 | **PASS (deterministic)** | cand 469F vs base 487F; **deterministic net-new = 0**; 8 candidate-only in-sweep ids all 3/3 green isolated ⇒ QUARANTINE row 2026-11-12 |
| 9 | Event/counter/endpoint surfaces | **PASS** | `ram_guard_events` 26/26 (one event per breach, drop path, `ram_events_dropped`); counter ordered set incl. “no p8” (M12 discriminating); 11-field `GET /api/resources/ram-guard`; `/control-capabilities` untouched |

## Command-level evidence (all packs)

| Pack | Nodes | Result |
|---|---|---|
| substrate unit (ceiling, tree_reader, verdict, ladder) | 96 | PASS 96/96 (2.42s) |
| runtime unit (guard, phase6, errors, events, interactions) | 113 | PASS 113/113 (7.51s) |
| config ladder + router endpoint | 58 | PASS 58/58 (2.88s) |
| real bash lane | 74 | PASS 74/74 (14.79s) |
| real proc lane | 55 | PASS 55/55 (8.00s) |
| fairness (incl. in-file counter-mutation) | 6 | PASS 6/6 (7.56s) |
| existing bash/reaper packs | 149 | 146P / 3F — the 3 disclosed `refused:start-time-gate` reds |
| existing proc/seam packs | 113 | 110P / 3F — `test_auto_kill_integration` sweep reds, **base-present** (§ adjudications) |
| ensure Core `concurrency_atomic_unit_test` | 94P/74S/5F | 5F = QUARANTINE row 152 (deterministic, base `f49006b92` A/B-proven) ⇒ satisfied on intent |
| ensure Core `dev.sh` static | — | PASS (`--timeout-graceful-shutdown 10`, `dev.sh:155`) |

Total: **402 dedicated ram-guard nodes PASS** (96+113+58+74+55+6).

## Agent-visible BASH envelope (verbatim, real 64 MiB run)

```
{"ok": false, "code": "ram_limit_exceeded", "surface": "bash.exec", "reason": "killed:group-dead-after-grace", "limit_bytes": 67108864, "rearm_threshold_bytes": 0, "observed_peak_bytes": 159494144, "peak_is_lower_bound": true, "poll_interval_ms": 100, "metric": "summed_rss_bytes", "trigger_metric": "summed_rss_bytes", "confirmed_pss_bytes": 135899136, "under_report_bound_bytes": 0, "pid": 1337099, "pgid": 1337099, "tree_complete": true, "members_total": 0, "members_killable": 0, "members_unreachable": 0, "grace_ms": 100, "killed_at": 1794473315.4088109, "ram_breach_observed": true, "coincident_timeout": false, "timeout_seconds": null, "message": "The enforced trigger threshold was 64 MiB (the resolved ceiling is 64 MiB); when the summed-RSS metric over-counts shared pages the threshold is re-armed above the ceiling and the kill decision is always made on the shared-page total."}
```

- 25 keys, exact `ENVELOPE_KEYS` order (`rearm_threshold_bytes` immediately after `limit_bytes`).
- Deciding value ≥ limit; `peak_is_lower_bound=true`; `tree_complete=true`; `members_*=0`.
- Secrecy scan: raw argv 0 · `--ram-probe-token=ZYZZY` 0 · sanitized prefix 0 · `sk-test-MARKER-XXX` 0 across envelope, proc_status and probe log.
- **Disclosure:** the probe used harness-injected `poll_interval_ms=100` / `grace_ms=100`; production defaults `200`/`250` are independently pinned by the config pack (C8 rungs, PASS).

## Agent-visible PROC surface (verbatim)

```
process_id: proc-c98b8a70
status: killed
pid: 1337109
exit_code: 0
buffer_bytes: 0 (limit=4194304)
ram_breach: limit_bytes=67108864 rearm_threshold_bytes=0 observed_peak_bytes=146944000 confirmed_pss_bytes=132238336 peak_is_lower_bound=true under_report_bound_bytes=132308992 metric=summed_rss_bytes trigger_metric=summed_rss_bytes coincident_timeout=false
ram_killed: true
```

## Mutation matrix — 19/19 DISCRIMINATING (gate-owned, re-run)

Runner `tests/packs/ram_guard_phase6_mutation_matrix.py`; 3 exclusive groups; before/after sha256 + `git diff --stat` empty on every group; no plant remained; all REDs `rc=1` (never a collection error).

| Group | Legs | Result | Wall |
|---|---|---|---|
| A | M1–M6 | 6/6 DISCRIMINATING | 63.15s |
| B | M7–M12 | 6/6 DISCRIMINATING | 62.4s |
| C | M13–M19 (incl. real-process M14/M15) | 7/7 DISCRIMINATING | 76s |

Fairness counter-mutation is additionally pinned inside `test_ram_guard_fairness.py::TestForkTreeFairness::test_the_broken_confirm_counter_mutation_kills_the_fair_tree` (PASS) — U4 is non-vacuous without extra source mutation. Plan §4 `DEFERRED_ROWS` are enumerated in the runner with an existing pin per row.

## Tier-2 dual-side (candidate `2f531319b` vs base `f3259c2a3`)

- Identical flags both sides: `-p no:cacheprovider -q --tb=line -rEf --capture=no -n 8`, chunks of 3000 node ids, `timeout 600`.
- Census: base **36927** collected, candidate **36800** (1261 deselected both; candidate excludes the 3 candidate-only real-process files, covered by dedicated lanes).
- Accounting closed: 36927 counted = 36927 collected.
- **cand FAILED = 469 · base FAILED = 487 · NET-NEW = 8 (all load-flake, 3/3 green isolated) ⇒ deterministic net-new = 0.**
- 26 base-only ids (resolved/no regression risk), incl. one base-side xdist worker crash.
- Adjudications:
  - (a) the 3 disclosed `test_bash_spawn_reaper.py` reds — **present in BOTH sets**, `refused:start-time-gate`, differing only by PID ⇒ pre-existing, symmetric.
  - (b) the 3 `test_auto_kill_integration.py` sweep reds — **base-present** (pre-existing). A 4th candidate-only sibling (`TestPathADurableTombstoneReap::test_sibling_alive_row_skipped_by_terminal_lane`) passes 3/3 isolated ⇒ flake (quarantined).
  - (c) the 5 `test_instance_metadata_atomic.py` `DetachedInstanceError` nodes — **identical both sides** (`comm -3` = 0) ⇒ QUARANTINE row 152 confirmed.
- xdist worker crashes: symmetric on both sides (property state-machine, 3 compaction lanes) — all entered BOTH sets. One asymmetric base-only crash landed in RESOLVED, not net-new. The earlier candidate `INTERNALERROR` (pre-chunk attempt) is absent from the chunked legs and did not enter any FAILED set.

## Disclosures carried (council-mandated — verified, not defects)

1. **No re-kill / one-shot scope** — retention until member-count-0 on a COMPLETE scan; no second signal. Pinned by M18 (release gate refuses an incomplete scan) and M19 (ever-members carry-forward, C1 across ticks). Fail-safe direction (over-long retention) unchanged.
2. **`_lane_failures` is log-only; `last_lane_progress_at` advances even on a failing pass** — the advance lives in the `finally` block; M10 mutates exactly that block and the lane-heartbeat pin reddens ⇒ property is pinned, traffic-light semantics stand.
3. **`breach_at` deferral** — no fresh crossing time on re-armed episodes; event payload key set (O2, incl. `breach_at`) and the re-arm pins hold. Load-bearing for the pending proc surface; unchanged.
4. **PSS early-abort deferred** — the 256-member confirm reads the whole set; the 250 ms deadline is post-hoc bounded (perf, not correctness). `TestBoundedWaitDeadline` (2) + disclosure class (7) PASS.
5. **MemTotal-1kB deferred** — conflicts §18.7 as stated; detector legs (15) PASS including cgroup/fallback/clamp.
6. **Matrix RED→GREEN was the tester's** — DONE: 19/19.
7. **3 pre-existing `bash_spawn_reaper` reds at base** — CONFIRMED symmetric in the dual-side leg (see (a)).
8. **Tidier notes** — kill-switch parser duplicated across `bash.py`/`proc_tools.py`: **parity verified** (`TestSwitchConsistency` 13/13, bash `TestKillSwitch` 15/15, config K3 parsing). `_sample_record` ~70 lines and duplicated member-closure remain routed follow-ups, no test impact.

## Artifacts

- `/tmp/ram-gate-2/` — census, chunk logs, `cand.failed.txt` (469), `base.failed.txt` (487), `NETNEW.txt` (8), `RESOLVED.txt` (26), `netnew_iso/` (24 runs), `BASE_LEG_REPORT.txt`
- `/tmp/ram-matrix-gate-A|B|C.tsv`, `/tmp/ram-gate-hashes-*.txt`
- `/tmp/ramguard-envelope.txt`, `/tmp/ramguard_envelope_probe.py`
- PACKS.md: 8 new rows registered (was 0 `ram_guard` rows) · QUARANTINE.md: load-flake row added · LESSONS: normalization-trap entry

## Worktree state

`git status --short` on candidate: only the pre-existing `.agents/approver/*` lane artifacts **plus this gate's three tester files** (RESULTS, PACKS.md, QUARANTINE.md, LESSONS) — for the merge lane to commit. Base worktree clean. Zero commits by the gate. The base worktree `/home/nea/ensemble-src-wt-ram-base-f3259c2a3` is left in place for re-verification and can be removed after merge.
