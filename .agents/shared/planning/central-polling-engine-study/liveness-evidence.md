# Phase-0 Production Liveness Evidence — Central Polling/Event-Management Engine Study

Date: 2026-11-21T07:28Z · Lane: Maintenancer (read-only forensics, instance live-evidence 73cc7df5) · Target: live ensemble daemon v0.31.22

Fence honored: GET-only probes, log reads, metrics reads, read-only DB SELECTs. Zero writes, zero restarts, zero arms.

## Live identity block

| Field | Value | Evidence |
|---|---|---|
| Version | **v0.31.22** | `release_info`: `releases/current → releases/v0.31.22`; `/livez version=0.31.22`; manifest smoke OK |
| Release pointer | current=v0.31.22, journal previous=**v0.31.10**, in-flight=none | `release_info`/`upgrade_status` |
| Boot epoch (this process) | **2026-11-21T01:56:29.025Z** | `wedge_captures.boot_epoch`; corroborated: shutdown reaps 01:55:09/01:56:10, old-daemon final gc 01:56:11 (`gc@540796`) |
| Uptime at last read | ≈ **5h30m** (01:56:29 → 07:26:37Z) | computed |
| Daemon PID | **1208513** (evidence-derived) | `bash_spawn_registry.daemon_pid` on new-daemon gc rows (`gc@1208513`); old daemon 540796 |
| Metrics restart-epoch | `6cc647cead8049afa933ba009805eb5b` | every metrics call |
| Prior boot epochs | 2026-11-19T13:31:01Z; pre-11-19 (11-11 restart loop) | wedge_captures; 12 interrupted `checkpoint-cleanup` rows 10-06→11-11 |
| PostgreSQL | 18.4, up since 2026-11-08 02:21:42Z (outlives daemon) | `pg_postmaster_start_time()` |
| Launcher telemetry | last_exit=3, crash_count=7, window_start≈2026-11-19T07:00Z | `release_info` launcher block |

**Measurement windows (UTC):** evidence reads 2026-11-21T07:19:16→07:26:37 (31 tool calls); log window **05:27–07:26** across `ensemble.log` + `ensemble.log.5` (retention ≈1h50m — 6×10MB DEBUG rotation evicted everything before 05:27, including the 01:56 boot); DB lookback per table: days–months (maintenance_runs since 09-27, schedule_executions since 11-08, bash_spawn_registry/reports 11-19+).

## Verdict table (33 rows)

| # | Mechanism | Verdict | Key evidence (timestamped) |
|---|---|---|---|
| 1 | WatchReconcileSweepService | **EXECUTES** | counter `sweep_cycles_total{lane=watch_reconcile}=65` @07:19:55 = exactly 300s cadence over 5h24m; 613 watchers FIRED since 11-19, last 07:11:55 |
| 2 | EligiblePendingSweepService | **EXECUTES** | 07:18:07/07:19:37/07:21:07 "tick 216/217 healed 2/5 eligible PENDING row(s); notify_work dispatched=True" (~90s) |
| 3 | **OrphanWatcherSweepService** | **DORMANT-TRAPPED** 🔴 | 07:17:58/07:19:28/07:20:58 ticks 215-217, every tick: "dependency_bus is not wired (legacy test fixture / pre-wiring lifespan) — relying on the startup sweep"; counter 216 counts no-op ticks; live orphan+husk 9c985fc7 unhandled |
| 4 | Maintenance tier — checkpoint-cleanup (+blob prune) | **EXECUTES** | run `ckpt-20261121_015731` 01:57:31→02:55:27 succeeded: 96,450 blobs / **43.2 GB** freed / 382,004 writes deleted; 72 lifetime success rows; **starvation gap 11-14 12:44 → 11-21 01:57 = 6.54d on v0.31.10** |
| 5 | Maintenance tier — vacuum | **EXECUTES** | `vac-20261121T025527` 02:55:27→03:05:33 succeeded (1 skip: checkpoint_blobs VACUUM stmt-timeout); 34 lifetime rows |
| 6 | Maintenance boot CAS sweep | **BOOT-ONLY (executed)** | cleanup start 01:57:31 = boot+62s (matches boot-sweep+60s pattern) |
| 7 | Maintenance tier — other ~8 jobs (skill_metric_scan, orphan sweep, drift/zombie reconcile…) | **INSUFFICIENT-EVIDENCE** 🔴 | only 2 sections in all 118 maintenance_runs rows (since 09-27); ZERO `daemon.services.maintenance` lines in both sampled log windows (05:27-05:32, 07:17-07:26); last_run in-memory → unreadable read-only. Not disproven — unverifiable by design |
| 8 | Scheduler sources → schedule_executions | **EXECUTES** | 49 rows/40 schedules, latest **today 02:34:56**; daily cron 1e723ff9 fired 11/11 days (11-10→11-20 22:00); deferred one-shots delivered days late (e.g. 11-18→11-21) |
| 9 | message_queue_boot_recovery | **BOOT-ONLY (executed)** | 2 pre-boot-stuck msgs (enq 01:45:37/01:49:14) resolved 02:07:04; events `task_recovery_force_cancelled_and_retried` 02:06:34/02:07:04; boot logs evicted |
| 10 | auto_continue_boot_pass + sweep_orphaned_live_turns | **BOOT-ONLY (executed)** | 2 tasks with `auto_continued_at > 01:56:29` |
| 11 | Ram-guard / wedge watchdog thread | **EXECUTES** | captures today 03:16:43, 05:48:21, **07:23:05** ("trigger fired … sustained 39.5s"; capture written, row_written=True) |
| 12 | Resource telemetry flusher (daemon bucket) | **EXECUTES** | `max(bucket_ts)=07:25:48`, 1,282 rows today (earlier "0 rows" was my format false-negative — corrected) |
| 13 | Resource instance sampler | **EXECUTES** | `max(bucket_ts)=07:26:15`, 34,856 rows today; F0.3 lifetime counter 28.3M |
| 14 | Metrics **PG census sampler** | **DORMANT (flag-off)** 🟠 | `activation.census_enabled=false`; F6 dark, reason=census_gated, 0 live series |
| 15 | Wedge detectors (sustained_cpu/loop_lag) | **EXECUTES** | 20 capture rows 11-20 10:29→11-21 07:23; F3 durable counters 18+2; 2 sustained_loop_lag captures (12.12s, 20.57s lags) |
| 16 | Loop-lag probe | **EXECUTES** | F1 fresh (14s age): probe_state=ok, 300 samples, p50 0.999s / p95 1.62s / max 3.66s |
| 17 | Instance-status sampler (F13) | **EXECUTES** | gauges fresh 9s: 5,338 instances bucketed; WC=6, max WC age 6,259s |
| 18 | Queue-freshness probe | **EXECUTES** | `readiness_probe_total{queue_freshness,ok}=1840` @~10.6s cadence; component_state=1 |
| 19 | Readiness/livez probes | **EXECUTES** | `/livez` 0.31.22 OK, `/readyz` ready reasons=[]; F9 per-component ok 1,801–1,840 (11 pool_busy + 28+12 timeouts, streaks 0) |
| 20 | stale_pause_sweep | **EXECUTES** | 07:21:44 "candidates=0 resumed=0 (age_bound=7200s)"; detector agreement (0 paused) |
| 21 | RDRS lanes 2+7 (mission-records reconcile) | **EXECUTES** | 05:27:05/05:32:05/07:17:19 (~300s): "lane 7 admitted 3 outstanding (ttl=21600s)"; abandoned last resolved **03:01:42** (post-boot); satisfied 5,532, last 07:11:54 |
| 22 | RDRS delivery machinery (lanes 3-6) | **EXECUTES** | TASK_DELIVERED 15,253 last 07:17:00; INJECTED 863 last 06:10:15; FAILED only 5 (last 11-19) |
| 23 | Memoir poignancy worker | **EXECUTES** | ticks ~20s (07:17:19→07:19:00, pending=0 idle); 22 episodes scored, last reflected **02:00:20**; newest episode 06:46:08 |
| 24 | Memoir embedding worker | **EXECUTES** | 07:17:52/07:18:52 ticks (tick_ms 127/60) |
| 25 | Memoir nightly reflection + JEV/constellation | **EXECUTES** | `last_nightly_date=2026-11-21`, claim released; JEV ledger 23 drafted/40 pending updated **02:02:09** |
| 26 | MCP health evaluator (init_guard state) | **EXECUTES** | context7/plane/webfetch all `ok`, published 0.5s ago; `mcp_failures_total` no series (zero failures) |
| 27 | MCP init_guard **recovery-probe firing** | **INSUFFICIENT-EVIDENCE** | no degraded server in window → no recovery event observable; state publisher proven, probe leg not Settling probe: none exists read-only post-log-rotation — would need a wider journalctl watch or a live degraded-MCP window. (per review) |
| 28 | MissionStrandHealer | **EXECUTES** | 05:27:46 + 05:32:34 (~300s): evaluated running-zombie 0fdc3ce0, correct fail-closed skip (completed_age=0s < 7200s) |
| 29 | WaitingChildrenWatchdog | **EXECUTES (thin)** 🟡 | 2 notices EVER, last delivered **02:56:47** post-boot (completed); 6 WC roots all < 6h25m (nothing to wedge); known under-reporting defect stands |
| 30 | Bash spawn **registration** | **EXECUTES** | 4,404 rows today (110–1,058/hour through 07:00) |
| 31 | Bash spawn **reaper / registry-gc** | **DORMANT-TRAPPED** 🔴 | last reap **02:57:58** (`gc@1208513`); **0 reaps of 3,604 spawns since hour 02:00**; table already 194k rows/70 MB — unbounded growth while daemon runs |
| 32 | upgrade_journal_sweep | **INSUFFICIENT-EVIDENCE** | F21 dark (series_absent); no log lines in retention; journal itself readable (v0.31.22) Settling probe: extended log retention or a durable F21 counter — none exists read-only today. (per review) |
| 33 | SourcesGcService / JobLockSweepService | **INSUFFICIENT-EVIDENCE** | no sources_gc/journal logs; drift_events=0; JobLockSweep is boot-only class, boot logs evicted, no side-effect trace; 4 job_locks rows currently held (no zombie evidence either way) Settling probe: boot-window logs (evicted by rotation) or a durable run-row per sweep — none exists read-only today. (per review) |

**Verdict counts:** EXECUTES 21 steady + 1 thin (WC watchdog) · BOOT-ONLY-executed 3 · DORMANT-TRAPPED 2 (#3, #31) · DORMANT-by-flag 1 (#14) · INSUFFICIENT-EVIDENCE 5 (#7, #27, #32, #33×2 services) — 33 rows total.

## Headline findings — LIVE reality vs code-truth

1. 🔴 **OrphanWatcherSweepService is a watch-#9-class no-op on v0.31.22**: the counter says 216 "cycles" (looks healthy), the log says every tick is structurally skipped ("dependency_bus is not wired"), and the exact failure it owns is live and aging — orphan/never-dispatched-husk `9c985fc7` (worker, idle, parent completed, **206,213s ≈ 2.39 days**, survived ≥2 boots). Registration ≠ execution, reproduced.
2. 🔴 **Bash spawn reaper stopped at 02:57:58Z** (≈1h after boot): 3,604 spawns since 02:00, zero reaped; registry grows unbounded (194k rows / 70 MB).
3. 🔴 **Maintenance tier verification gap is structural**: only checkpoint-cleanup + vacuum have EVER written durable rows (2 sections / 118 rows since 09-27). The other ~8 tier jobs are read-only-unverifiable (in-memory last_run, no rows, no logs in sampled windows). Watch #9's starvation is confirmed on v0.31.10: **6.54-day execution gap (11-14→11-21), 43.2 GB backlog cleared only by the promote boot**.
4. 🟠 **Metrics census sampler is flag-off** (`census_enabled=false`, F6 dark) and **F0.3 `tables_accepting_rows`=0 for all three resource tables while writes flow continuously** — the metrics layer itself contains counter-vs-reality divergences (also: DLQ gauge 6 vs `dead_letter_items` table 1 — different measures, per known doc).
5. 🟠 **Worker pools are polling-dominated**: wakeup efficiency 4.6%/3.3% — 29,606 timeout-wakeups vs 613 notifications in 5.5h (12,757+18,035 empty claim attempts). Directly relevant to the central-polling-engine question.
6. 🟡 Stale-running leader `68f04ccd` idle **262,820s (3.04d)** — flagged by wedge detectors, handled by nothing (known `last_activity_at`-freeze caveat applies).

## Limitations / remaining

- Boot-window (01:56–02:10) log evidence is unrecoverable (DEBUG rotation keep-6); boot verdicts rest on DB side-effects only.
- Maintenance-tier 15-min skip cadence not observed in the two sampled windows (05:27–05:32, 07:17–07:26); a wider journalctl/log-tail watch would close row #7 — read-only but outside log-tool retention.
- Code-truth follow-ups routed to the repo census lane: OrphanWatcherSweepService "dependency_bus not wired" wiring site; bash spawn reaper scheduling/death semantics; JobLockSweep + SourcesGc trigger models.
