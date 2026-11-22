# Engine Arc Phase-1 — Boot SLO RAW MEASUREMENT (boot_epoch → readyz-green)

**Commission:** Read-only telemetry measurement for the Engine Arc Phase-1 planning lane.
**SLO under test (Emperor-set, CLOSED 2026-11-21):** `boot_epoch → readyz-green ≤ 120 s` ("Boot wall clock is 2 minutes").
**Measured:** 2026-11-21 (collector: explorer lane, read-only).
**Live install:** `/home/nea/agents-ensemble` (READ-ONLY — no daemon/binary invoked, no writes, no restarts).
**Repo tree:** `/home/nea/ensemble-src-wt-engine-arc-p1` (branch `feature/engine-arc-phase1`, HEAD `aed3eabce` = base `b71742fe3` v0.31.23 + planning docs).
**Live serving version at measurement time:** v0.31.23 (`/home/nea/agents-ensemble/releases/state.json` → `current=v0.31.23, previous=v0.31.22, in_flight=null`).

---

## 0. VERDICT (numbers first)

| Source class | n | median | p90 | max | vs 120 s |
|---|---|---|---|---|---|
| **Live promote boots** (`/home/nea/agents-ensemble/data/upgrade.log` + `data/upgrade/live/*.log`, probe-age at readyz-green) | 19 | **13.1 s** | **22.8 s** | **23.2 s** | **COMFORTABLY UNDER** (max = 19.3 % of budget) |
| **boot_epoch→readyz (exact pair, mixed clock — see §1.2)** | 2 | 21.3 s | — | 21.7 s | UNDER |
| **Hard-reboot drill** (transient unit stop → fresh start → `/readyz` 200) | 3 | 14 s | — | 15 s | UNDER |
| **Recorded dev/sandbox cold boots** (fresh PG, column-ensure path) | 2 | 45 s | — | **67 s** | UNDER — thinnest margin; **PRE-EXISTING** (sandbox-cold-DB shape, 2026-10-11) |
| **Recorded DB-outage class (2026-10-09 v0.18.5)** | 1 | — | — | **never green inside the 120 s probe budget** | FAILED — **PRE-EXISTING** (probe-timeout-under-load class) |

**Method labels used in this file:** `promote-log-derived` (probe age at readyz-green, host clock) · `boot_epoch-derived` (DB clock, mixed-frame — trap in §1.2) · `journal-derived` (drill harness, cursor-anchored) · `recorded-evidence` (tester RESULTS / incident file). No two classes share a clock frame, which is why they are aggregated separately rather than pooled.

**Plain statement:** the *typical* live boot is confirmed green by the promote probe at a process age of **~11–15 s** (median 13.1 s); the observed maximum across 19 live promote boots is **23.2 s** (v0.31.22, 2026-11-21), and using the earlier-composite reading it is 21.2 s (v0.27.8, 2026-10-26). Every recorded live boot is under 120 s by a wide margin (worst case ≤19.3 % of the budget). The only two ≥120 s-shaped observations on record are (a) a dev/sandbox cold boot against a fresh PostgreSQL cluster at **67 s** (2026-10-11) — under the bar but within 1.8× of it — and (b) the **2026-10-09 v0.18.5** promote where `/readyz` **never** went green inside the 120 s gate budget because the database was mid-outage. Both are **PRE-EXISTING conditions, not Phase-1 regressions** (they predate this lane by months and carry no relation to the engine build).

**Sample count and independence:** **27 source-labeled boot observations** (19 live promote boots + 2 exact `boot_epoch` pairs + 3 drill runs + 2 dev/sandbox cold boots + 1 recorded outage-class boot), spanning 4 distinct measurement methods and 8 distinct artefact families (`data/upgrade.log`, `data/upgrade/live/r-*.log`, `data/promote-v0296.log`, `wedge-captures/boot-records/*.json`, `releases/state.json`, tester RESULTS, the incident file, and live installer logs). Four demo-install promotes are listed as corroboration only and are NOT counted. All required bars are met: ≥8 live promote boots (19), exact `boot_epoch` pairs (2), an independent drill harness (3), and a cold/loaded-DB class (2).

**Reading discipline:** every figure is *time to a `readyz` 200 with all components true*. Two readings exist per boot — "probe says green now" (probe wall clock) and "the cached composite was already green at `checked_at`" (≤1 refresh interval earlier). §2.1 reports the probe reading (the conservative, larger one); the composite reading is ~4–6 s smaller and is stated where it matters. **No reading, in either direction, approaches 120 s for any live boot.**

---

## 1. MEASUREMENT METHOD AND ITS ONE STRUCTURAL TRAP

### 1.1 Anchors that exist

| Anchor | Where | Clock frame | Persisted? |
|---|---|---|---|
| `Boot epoch captured (DB clock): <iso>` | `daemon/services/boot_epoch.py:132` (log), captured once per process | **DB clock** (`SELECT now()` on the PG host) | log line only (never survives log rotation) |
| `boot_epoch` field in a boot record | `data/wedge-captures/boot-records/<YYYYMMDDTHHMMSSffffffZ>.json` (43 files present, cap 50, prune-oldest) | **DB clock** (same value as the log line) | **YES — durable per boot** |
| `uptime_seconds` in `/livez` | `daemon/api.py:6325` (`time.time() - start_time`), `start_time` set at `daemon/api.py:1012` | **host clock** | point-in-time probe only |
| `detail.checked_at` in `/readyz` | readiness composite stamp (`daemon/services/readiness.py`, `refresh_readiness_composite`) | **host clock** | point-in-time probe only |
| Promote gate probes `livez OK` / `readyz OK` + bodies | `scripts/upgrade/lib.sh:9095-9096` (`gate_livez` / `gate_readyz`), emitted by `promote.sh` | host clock | **YES — appended to `data/upgrade.log` and `data/upgrade/live/r-*.log`** |
| `[Readiness] Refresher started: interval=10s` | `data/launcher.log:231` (and every boot) | host clock | log line |

### 1.2 The trap (found, load-bearing for gate design)

**`boot_epoch` and `checked_at` are in DIFFERENT clock frames.** `boot_epoch` comes from the PostgreSQL host (`10.44.0.2`, DB clock); `checked_at` and `uptime_seconds` come from the daemon host. On the 2026-11-21T10:39 boot the two frames differ by **≈4.0 s** (DB clock *behind* host clock):

- `boot_epoch` (DB clock) = `2026-11-21T10:39:02.137280Z` (`boot-records/20261121T103902137280Z.json`)
- process start (host clock, derived) = `/livez` logged `10:39:28Z` − `uptime_seconds 21.820` = **`10:39:06.18`**
- ⇒ DB clock lags host clock by ≈4.0 s on that boot. (The 2026-11-21T01:56 boot shows ≈4.7 s.)

Consequence: a literal `boot_epoch → checked_at` subtraction **inflates** the measured boot by the skew (see §2.2: 20.8 s mixed-frame vs 16.8 s host-only for the *same* boot). A gate that mixes frames is conservative but not exact, and the skew is not a constant the gate can assume. **The gate must pick ONE frame** (§4).

### 1.3 Secondary traps confirmed on the artefacts

- `uptime_seconds` is measured from `app.state.start_time`, which is set at `daemon/api.py:1012` — *after* `manager.initialize()`, `capture_boot_epoch`, message-queue boot recovery, critical-notes probe, and lease recovery. It is therefore **later** than real process start (interpreter/import/manager-init time is excluded) — a **lower bound**, slightly optimistic.
- `checked_at` is refreshed on a **10 s** interval by `_periodic_readiness_refresh_loop` (`daemon/api.py:5190+`, `interval_seconds` from `[Readiness] Refresher started: interval=10s`). First tick fires immediately at `t=0` (docstring `daemon/api.py:5215-5217`), so the first green can be near-instantaneous when the DB is healthy; but every later observation is **quantized to ±10 s**.
- The `livez OK` / `readyz OK` gate lines in `data/upgrade.log` carry **no timestamps on the older entries**; only the `/readyz` body's `checked_at` dates them. All pre-2026-11-17 promote rows in `data/upgrade.log` were dated from that one field. The per-run files `data/upgrade/live/r-*.log` (2026-11-17 onward) DO timestamp both probes.
- `.launcher-state` was NOT used (it updates only on exit — not current-boot evidence).
- Log rotation: `data/logs/ensemble.log*` (6 files) contain **zero** `Boot epoch captured` lines for recent boots (the daemon's uvicorn/readiness lines are interleaved with `httpcore` DEBUG noise and rotate fast). `data/launcher.log` holds one stale boot (v0.28.4, 2026-11-01T22:32) and is not a current-boot source.

---

## 2. SAMPLE TABLE

### 2.1 Live promote boots — probe age at readyz-green (n = 19)

**Method label:** `promote-log-derived (probe-age at readyz-green, host clock)`.
**Derivation for a comparable number across all 19:** `readyz_probe_age = (readyz probe wall clock) − (process start)`, with `process start = livez_probe_wall − livez.uptime_seconds`.
- Rows 1–17: `upgrade.log` carries no probe wall timestamp, only the `/readyz` body. The livez→readyz probe spacing observed directly in the 2026-11-21 runs is 1 s, so `readyz_probe_age ≈ uptime_seconds + 1 s`. **Upper bound**; residual error is a few seconds at most (the probe loop's own retry cadence), never enough to matter at a 120 s bar.
- Rows 18–19: both probes are timestamped (`livez OK:` / `readyz OK:` lines), so the value is exact.

| # | Release promoted | `/livez` `uptime_seconds` (s) | `/readyz` green `checked_at` (host) | boot time at readyz-green probe (s) | Evidence file |
|---|---|---|---|---|---|
| 1 | v0.20.1 | 4.761 | 2026-10-13T04:46:11.198 | **5.8** | `data/upgrade.log:27-30` |
| 2 | v0.20.3 | 9.908 | 2026-10-14T16:38:53.413 | **10.9** | `data/upgrade.log:75-78` |
| 3 | v0.20.4 | 8.740 | 2026-10-17T11:07:24.571 | **9.7** | `data/upgrade.log:112-115` |
| 4 | v0.21.0 | 10.768 | 2026-10-17T17:20:45.122 | **11.8** | `data/upgrade.log:149-152` |
| 5 | v0.25.0 | 9.026 | 2026-10-19T06:12:51.622 | **10.0** | `data/upgrade.log:186-189` |
| 6 | v0.25.1 | 12.117 | 2026-10-19T09:59:02.877 | **13.1** | `data/upgrade.log:227-230` |
| 7 | v0.25.3 | 15.072 | 2026-10-19T17:45:19.084 | **16.1** | `data/upgrade.log:264-267` |
| 8 | v0.25.5 | 12.967 | 2026-10-20T03:50:09.733 | **14.0** | `data/upgrade.log:305-308` |
| 9 | v0.25.7 | 10.729 | 2026-10-20T06:49:20.860 | **11.7** | `data/upgrade.log:343-346` |
| 10 | v0.26.2 | 13.330 | 2026-10-21T07:22:26.387 | **14.3** | `data/upgrade.log:382-385` |
| 11 | v0.26.3 | 13.646 | 2026-10-21T10:39:28.704 | **14.6** | `data/upgrade.log:419-422` |
| 12 | v0.27.0 | 13.268 | 2026-10-22T04:08:11.812 | **14.3** | `data/upgrade.log:457-460` |
| 13 | v0.27.1 | 10.965 | 2026-10-22T04:32:20.691 | **12.0** | `data/upgrade.log:496-499` |
| 14 | v0.27.4 | 10.111 | 2026-10-23T06:11:19.969 | **11.1** | `data/upgrade.log:532-535` |
| 15 | v0.27.8 | 20.242 | 2026-10-26T20:42:31.083 | **21.2** | `data/upgrade.log:571-574` |
| 16 | rollback → v0.27.4 | 10.056 | 2026-10-26T20:47:49.124 | **11.1** | `data/upgrade.log:600-603` |
| 17 | v0.29.6 | 15.372 | 2026-11-08T18:11:13.605 | **16.4** | `data/promote-v0296.log:64-67` |
| 18 | v0.31.22 | 22.230 | 2026-11-21T01:56:50.750 | **23.2 exact** | `data/upgrade/live/r-20261121-015322-3192.log:31-34` |
| 19 | v0.31.23 | 21.820 | 2026-11-21T10:39:22.940 | **22.8 exact** | `data/upgrade/live/r-20261121-103633-ca48.log:256-259` |

Note on rows 18–19 — two different readings, both inside budget:
- **Probe-age reading** (reported above): the readyz probe's own wall clock minus derived process start = 23.2 s / 22.8 s. This is what the promote gate "sees".
- **Composite-stamp reading**: the `/readyz` body's `checked_at` (readiness refresher stamp, 6 s older than the probe) minus derived process start = **17.0 s / 16.8 s**. The composite was already `ready` at that stamp, so green existed **no later than** 17.0 s / 16.8 s.
- The same staleness applies to rows 1–17 (their `checked_at` is also a refresher stamp), so every number in the table is an **upper bound on time-to-first-green**, inflated by at most one refresh interval (10 s) plus the 1 s livez→readyz spacing.

**Aggregates (n=19):** median **13.1 s** · mean **13.9 s** · p90 **22.8 s** (nearest-rank) · max **23.2 s** (v0.31.22, 2026-11-21) · min 5.8 s.
Worst case consumes **19.3 % of the 120 s budget**. Sensitivity: switching rows 18–19 to their composite-stamp ages (17.0 / 16.8) lowers the max to 21.2 s (v0.27.8) and leaves the median at 13.1 s — **every aggregation path stays comfortably under the bar**.
Trend note: early-October boots (rows 1–5) run 6–12 s; late-October/November boots (rows 15–19) run 16–23 s, tracking payload growth (skills / MCP warm-up / memoir workers).

### 2.2 Live boots with an EXACT `boot_epoch` (boot-records × promote `/readyz` body) — n = 2

**Method label:** `boot_epoch-derived (mixed clock — see §1.2)`.

| Boot | `boot_epoch` (DB clock) | `/readyz` green `checked_at` (host clock) | delta (s) | Host-only delta for the same boot (s) | Evidence |
|---|---|---|---|---|---|
| 2026-11-21T01:56 | `2026-11-21T01:56:29.025080Z` | `2026-11-21T01:56:50.749529` | **21.7** | 17.0 | `boot-records/20261121T015629025080Z.json`; `r-20261121-015322-3192.log:31-34` |
| 2026-11-21T10:39 | `2026-11-21T10:39:02.137280Z` | `2026-11-21T10:39:22.940126` | **20.8** | 16.8 | `boot-records/20261121T103902137280Z.json`; `r-20261121-103633-ca48.log:256-259` |

**Aggregate:** median 21.3 s · max 21.7 s. **Both under 120 s.** The ~4 s gap to the host-only figure is DB-clock skew, not boot cost.

Other boot records on disk that have **no** paired `/readyz` evidence (their promotes halted at hand-back attribution before the readyz probe, or no promote log survives): `20261117T172943087771Z` (v0.31.0, halt at attribution), `20261118T041104940518Z`, `20261118T042906183628Z` (v0.31.2, halt at attribution), `20261118T080418487748Z`, `20261118T135046599902Z`, `20261118T140752788266Z`, `20261119T044631244552Z` (v0.31.8, halt: never confirmed within 300 s), `20261119T062730800406Z`, `20261119T070223877928Z`, `20261119T133101236066Z` (v0.31.10, halt at attribution), plus 30 older records back to 2026-11-11.

### 2.3 Hard-reboot drill — n = 3 (journal-anchored, daemon recovery path)

**Method label:** `journal-derived (drill harness)`. Anchor = transient-unit stop → fresh start; end = `/readyz` 200 with all components true.

| Run | T_restart → T_readyz | Evidence |
|---|---|---|
| #3 (pre-anchor) | **14 s** | `.agents/tester/RESULTS/2026-10-21-hard-reboot-p4-drill-harness.md:103` |
| #4 (pre-anchor) | **13 s** | same, `:104` |
| final @ `8eb8ef643` (journal-cursor anchored) | **15 s** | same, `:105` |

The harness explicitly excludes host firmware/kernel boot ("`host boot time + ~14 s`", `:110`). It is the closest existing mechanical precedent for a boot-SLO measurement: cursor anchoring prevents latching a pre-crash marker (`:112-118`).

### 2.4 Recorded dev / sandbox cold boots — n = 2 (fresh PG, column-ensure path)

| Boot | Measurement | delta | Evidence |
|---|---|---|---|
| designer→sketcher→critic dev E2E, v0.19.0, 2026-10-11 | launch 07:57:38Z → banner +9 s → `/readyz` green 07:58:45Z (`uptime 60 s`) | **67 s** | `.agents/tester/RESULTS/2026-10-11-designer-pipeline-dev-e2e.md:28` |
| resource-telemetry fix verification, v0.25.3, 2026-10-19 | first `/livez` 200 at t=23.15 s (process age 5.49 s); `/readyz` 200 same poll | **≈23 s from launch** | `.agents/tester/RESULTS/2026-10-19-resource-telemetry-fix-verification.md:395` |

The 67 s case is the **worst non-outage boot on record** and it is a *dev sandbox with a disposable fresh PG16 cluster* (`initdb -A trust`, 56 tables) — sandbox-cold-DB shape, not a live promote boot. Still under 120 s.

### 2.5 Demo promotes (fast-class corroboration, different install)

| Boot | livez / readyz | Evidence |
|---|---|---|
| 2026-08-23 v0.10.7-p2.3-b65 | livez ~3 s, readyz ~4 s (from flip) | `.agents/tester/RESULTS/2026-08-23-p2-3-b65-promote-t8-recapture.md:48,:99` |
| 2026-08-23 DR-5 ari-driven | readyz ~4 s from flip | `.agents/tester/RESULTS/2026-08-23-p2-3-b7-dr5-ari-e2e.md:164` |
| 2026-08-22 P2.1 demo pipeline | livez OK (uptime 1.35 s / 0.10 s), readyz OK same round | `.agents/tester/RESULTS/2026-08-22-p2-1-demo-e2e-release-pipeline.md:46,:105` |
| 2026-08-22 AR phase-1 restart run | `/livez` green t+17.09 s; `/readyz` green ≪120 s | `.agents/tester/RESULTS/2026-08-22-ar-phase1-followups-verification.md:23` |

These anchor on *flip/respawn*, not `boot_epoch`, so they are corroboration only — not counted in the aggregates.

### 2.6 Outlier / failure classes on record

| Instance | Observation | Attribution |
|---|---|---|
| 2026-10-09 v0.18.5 promote | `/readyz` **never** green inside the 120 s gate budget; ADR-005 auto-rollback of a healthy build. Same DB (`10.44.0.2:5432/ensemble_prod`) had a 19:53–20:50Z outage window. | `.agents/shared/incident-20261010-checkpoint-connection-pool.md:104-106`; class analysis `.agents/shared/planning/soak-gate-mcp-warmup/decisions.md:72,:193` |
| 2026-10-26 v0.27.8 soak | `/readyz` **transient-exhausted** at 45/60/90/105 s → cap-reach, rollback (probe-level, not boot) | `data/upgrade.log:577-583` |
| 2026-11-19 v0.31.8 | hand-back "not confirmed within 300 s"; **no readyz evidence** — the :9797 answerer was outside the unit lineage (attribution refusal class), not a slow boot | `data/upgrade/live/r-20261119-043446-c8da.log:22` |
| 2026-11-17 / 18 / 19 promotes (v0.31.0 / v0.31.1 / v0.31.2 / v0.31.10) | halted at hand-back **attribution** (`port-holder NOT in the supervision-owned pid set`) — the boot itself was not measured; the daemon-side `serving_green_commit` converged afterwards | `r-20261117-171207-e2f9.log:22-25`, `r-20261118-034632-19db.log:22-25`, `r-20261118-042553-f188.log:22-25`, `r-20261119-132823-3a3c.log:22-25` |

**Explicitly NOT a boot-time signal:** `is-active probe answered rc=3 (state=inactive)` and `is-active probe exceeded budget (5s)` appear in every promote log; they are `systemctl`-poll artefacts. The `unit hand-back COMPLETE` line that follows is the real start confirmation (new MainPID + port + `/livez`).

---

## 3. TELEMETRY LOCATION SUMMARY (what exists today)

| What | Absolutely where | Content | Duration? |
|---|---|---|---|
| Boot start sentinel | `daemon/services/boot_epoch.py:91-133`; log line at `:132`; call site `daemon/api.py:969-971` (in lifespan, after `manager.initialize()`, before MQ boot recovery) | `"Boot epoch captured (DB clock): <naive-UTC iso>"` | no — instant only |
| Per-boot durable record | `data/wedge-captures/boot-records/<YYYYMMDDTHHMMSSffffffZ>.json` (43 files, cap `BOOT_RECORDS_MAX_FILES = 50`, prune-oldest; `daemon/services/wedge_capture/boot_record.py:54,58,136-150`) | `boot_epoch`, `effective_mode`, `mode_semantics`, per-pass `duration_seconds` (autocontinue / orphan_sweep) — **no readyz field** | no |
| Readiness composite | `daemon/services/readiness.py:1377+`; refresher `daemon/api.py:5190-5265`; `/readyz` handler reads cached `app_state.readiness_composite` (zero DB, ADR-003) | `status`, `components`, `detail.checked_at`, `detail.grace`, `detail.component_classes`, `detail.inflight_turns` | no — but `checked_at` is a usable readyz-green timestamp |
| Process age | `daemon/api.py:6325` (`/health`), `:6594` (`/livez`) — `time.time() - app.state.start_time`, `start_time` at `:1012` | `uptime_seconds` | yes, derived |
| Promote gate | `scripts/upgrade/lib.sh:9095-9096` (`gate_livez` / `gate_readyz`), budgets `LIVEZ_BUDGET_S=180` (`lib.sh:146`), `READYZ_BUDGET_S=120` (`lib.sh:147`); consumed in `promote.sh:1003,:1069,:1123` | `livez OK:` / `readyz OK:` + full bodies | gate-phase only (clock starts after hand-back verify) |
| Promote narratives | `data/upgrade.log` (613 lines, all live promotes to 2026-10-26) + `data/upgrade/live/r-*.log` (per-run) | arm-journal capture, flip, hand-back, livez/readyz bodies, COMMITTED | only as event-vs-body deltas |
| Upgrade journal | `releases/state.json` (+ `.pre-reset.*.bak`, `.pre-trim.*.bak`) | events with `ts`: `commit`, `supervision_boot`, `serving_green_commit`, `sweep*`, `halt`, `intent_flip`, `nonce_consumed`, `promote-start`, `run_log`, `plugin_staleness_observed`, `arm-scripts-provenance`, `mcp_server_state` | no — timestamps only; live `history` is trimmed |
| Existing boot-SLO-shaped harness | `scripts/upgrade/boot_smoke.sh` — fenced scratch-PG boot, polls `/livez` **and** `/readyz` at 1 s cadence (`:496-524`), `POLL_TIMEOUT` default **45 s** (`:60`), own `systemd-run --user` transient unit, both kill-switches pinned | livez/readyz codes + component verdict | yes (implicit, not emitted as a number) |
| Drill harness | `.agents/tester/RESULTS/2026-10-21-hard-reboot-p4-drill-harness.md` (cursor-anchored T_restart→T_readyz) | 13/14/15 s | yes |
| Log rotation | `data/logs/ensemble.log{,.1..5}` | uvicorn/daemon lines; **zero** `Boot epoch captured` lines for recent boots; `data/launcher.log` holds one stale boot (2026-11-01T22:32) | loses markers fast |

**Answer to "is boot wall-clock measured anywhere today?" — No.** It is derivable, per boot, but never recorded as a number. The only durable per-boot artefact (`boot-records/*.json`) carries the start anchor and **no** end anchor.

---

## 4. GATE-WIRING FACTS (for the Phase-1 acceptance gate)

1. **Start anchor options, in order of honesty:**
   - (a) **systemd unit start** (`systemd-run`/`ActiveEnterTimestamp`, or the launcher's `starting: <binary>` line — `data/launcher.log:3` shows the shape `launcher[616924]: starting: /home/nea/agents-ensemble/current/ensemble-prod`, immediately before the daemon's first import-time log line). Host clock; the **most complete** wall clock, because it includes interpreter start, imports, config load, engine creation, `manager.initialize()` and the boot passes.
   - (b) **`boot_epoch` log line** (`boot_epoch.py:132`) — the Emperor's named anchor. DB clock; captured at `api.py:969-971`, i.e. **after** `manager.initialize()` (but **before** MQ boot recovery). Using it as the SLO start therefore **excludes** interpreter/import/config/engine/manager-init cost and makes the SLO easier than "boot wall clock" implies. Its only advantage is durability (mirrored into `boot-records/*.json`).
   - (c) **`/livez` process start** = `probe_time − uptime_seconds` — host clock, available on any boot with one probe, but `start_time` is set at `api.py:1012` (after manager-init) so it is the same *late* anchor as (b) in host-clock form; it is the only anchor that is **frame-compatible with `checked_at`**.
2. **End anchor:** the first `/readyz` response with `status == "ready"` (all of `database` / `queue_freshness` / `services` / `checkpoint_saver` true, `detail.reasons == []`). Timestamp choice: `detail.checked_at` (host clock, the composite's own stamp) or the probe wall time. **Quantization ceiling: ±10 s** — the refresher interval (`[Readiness] Refresher started: interval=10s`, `launcher.log:231`; `daemon/api.py:5190+`). A 120 s SLO therefore has ±10 s granularity unless the refresher interval is tightened for the measurement window or the gate records probe wall time and accepts the ≤1-interval lag.
3. **First tick is immediate** (`daemon/api.py:5215-5217`: "the first tick fires immediately (t=0)") — so a healthy-DB boot can be green at the first refresh. In the 19 live samples, the `readyz` probe green trailed the `livez` probe by 1 s and trailed the derived process start by **5.8–23.2 s**.
4. **Do NOT mix frames** (§1.2). Two acceptable designs:
   - **Host-frame gate:** `boot_start = unit ActiveEnterTimestamp` (or `livez_probe − uptime`) → `readyz_green = first 200 with status=ready`, both host clock. Exact, no skew.
   - **DB-frame gate:** require a readyz-green stamp written in DB clock (does not exist today — would need a new field, e.g. persist `first_ready_db_clock` into the boot record; the boot-record writer already exists at `boot_record.py:269`).
5. **Existing machinery reusable without new code:** `scripts/upgrade/boot_smoke.sh` already polls `/livez` + `/readyz` at 1 s cadence in a fenced scratch-PG boot and asserts the components; it lacks only a start/end delta emission and uses a 45 s poll timeout (vs the 120 s SLO). The drill harness proves cursor-anchored journal measurement is workable and already has a negative control against latching stale markers.
6. **Never use these as boot evidence:** `.launcher-state` (updates only on exit), the `is-active rc=3` / `is-active exceeded budget (5s)` lines (systemctl-poll artefacts), the `hand-back verify … not confirmed within 300s` line (attribution/`MainPID` conjunct, not boot duration), or `uptime at livez OK` alone when the promote probe is in a retry loop.
7. **Where a gate would read history from:** `data/upgrade/live/r-*.log` gives `checked_at` + `uptime_seconds` per promote (the 19 samples in §2.1 were extracted this way, mechanically, with `grep`-able line shapes: `livez OK:`, `{"status":"alive","uptime_seconds":…}`, `readyz OK:`, `{"status":"ready",…"checked_at":"…"}`); `boot-records/*.json` gives `boot_epoch` per process; `releases/state.json` gives event ordering (`intent_flip` → `supervision_boot` → `serving_green_commit`) but not durations.
8. **Mechanical recipe that needs no new daemon telemetry (host frame, exact):**
   1. `boot_start := ActiveEnterTimestamp` of the unit (or the launcher's `starting: <binary>` line write time).
   2. Poll `/readyz` until `status == "ready"` and `detail.reasons == []`; record the poll's own wall clock as `readyz_green`.
   3. `boot_wall = readyz_green − boot_start`; assert `≤ 120 s`.
   4. Record `detail.checked_at` alongside it — the difference between the two is the refresher staleness (observed 6 s on 2026-11-21), which is measurement overhead, not boot cost.
   Steps 2–4 are already implemented in `scripts/upgrade/boot_smoke.sh:496-524` (1 s cadence, component assertions); only step 1 and the subtraction are missing, plus a `POLL_TIMEOUT` raise from 45 s to ≥120 s.
9. **If the gate must bind to `boot_epoch` literally** (Emperor's naming): the arithmetic is `readyz_green_db_clock − boot_epoch`, which requires a `readyz`-green stamp recorded in DB time. Nothing writes that today. The cheapest honest wiring is to have the readiness refresher record its **first** green as `SELECT now()` into the existing per-boot record (`write_boot_record`, `boot_record.py:269`) — one field, no new subsystem — after which the gate is a two-field subtraction inside one clock frame.
10. **Tolerance to state:** the 120 s bar has ~5× headroom against the observed live maximum (23.2 s) and ~1.8× against the worst recorded cold-DB boot (67 s). A gate at 120 s therefore does not false-fire on any boot class on record except the DB-outage class, which fails closed by design.

---

## 5. CORRECTIONS TO THE INHERITED LEADS

### 5.1 UNVERIFIED LEADS — NOT COUNTED (listed for traceability, do not cite as measurement)

| Lead | Status |
|---|---|
| "v0.16.6 dev smoke +9 s livez / +13 s readyz" | **UNVERIFIED** — no matching artefact in this tree or the live install. Not counted. |
| "Integration Gate Check 4 ~22 s first-healthy" | **UNVERIFIED** as named; a *different* verified boot at `.agents/tester/RESULTS/2026-10-19-resource-telemetry-fix-verification.md:395` measures `/livez` first 200 at t=23.15 s (same magnitude, different event). |
| "v0.18.4 / v0.18.5 ~10 s core services" | **UNVERIFIED** in files. |
| "sonic dry-run ~16 s cold boot" | **UNVERIFIED** — sonic docs describe a fenced scratch-daemon procedure, no boot delta recorded. |
| "demo recovery ENSEMBLE-32 ~30 s boot-to-livez" | **UNVERIFIED**; demo promotes independently show livez ~3 s / readyz ~4 s from flip (§2.5). |
| "2026-11-08 reconcile ~54 s migration-bearing boot" | **UNVERIFIED**; `data/promote-v0296.log` (2026-11-08) shows livez uptime 15.372 s and readyz green 18:11:13.605 → **16.4 s**, not 54 s. |
| "2026-10-09 v0.18.5 livez only at uptime 302 s" | The **readyz-never-green** half is corroborated; the `uptime 302 s` figure is **not** re-verifiable in files. |

### 5.2 Leads that WERE verified

| Lead | Status |
|---|---|
| "`daemon/services/boot_epoch.py:91-133`, log at `:132`" | **CONFIRMED** (read verbatim: module docstring lines 91-133, `logger.info("Boot epoch captured (DB clock): %s", ...)` at :132). |
| "`boot_epoch` captured at `daemon/api.py:412`" | **STALE.** Call site is `daemon/api.py:969-971` at this tree. (`instance_lifecycle.py:127` still cites `api.py:412`.) |
| "`gate_readyz` at `lib.sh:~5987`, `READYZ_BUDGET_S=120` at `lib.sh:~147`" | Budget **CONFIRMED** at `lib.sh:147`. Function moved: `gate_readyz()` at `scripts/upgrade/lib.sh:9096`, `gate_livez()` at `:9095`; `:5987` is unrelated now. |
| "promote clock starts only after hand-back verify (`lib.sh:~4801`)" | **DIRECTIONALLY CONFIRMED:** `r-20261121-103633-ca48.log` — flip 10:38:45 → hand-back COMPLETE 10:39:26 → livez 10:39:28 → readyz 10:39:29. Gate budgets measure gate-phase, not boot wall clock. Code anchor not re-verified line-exactly. |
| "upgrade journal records events, never durations" | **CONFIRMED.** `releases/state.json` vocabulary verified (`supervision_boot`, `serving_green_commit`, `sweep`, `halt`, `intent_flip`, `nonce_consumed`, `promote-start`, `run_log`, `plugin_staleness_observed`, `arm-scripts-provenance`, `mcp_server_state`, `commit`); live `history` is trimmed. |

---

## 6. OPEN QUESTIONS (genuinely unanswerable read-only)

1. **`journalctl` for `ensemble-main.service` is not reachable from this lane** (read-only, no bash). The most recent boots' `Started server process` / `Application startup complete` timestamps, and therefore a true unit-start→readyz wall clock for the current v0.31.23 process, were not collected. The promote-side numbers in §2.1 are the substitute.
2. **The 2026-10-09 v0.18.5 `uptime 302 s` figure and the 2026-11-08 "~54 s migration-bearing boot"** exist only in narrative/derived form; no artefact in the live install or this tree carries them. Both are under-or-near the bar respectively, so neither changes the verdict.
3. **The exact DB-vs-host clock offset is measured on two boots only** (≈4.0 s and ≈4.7 s on 2026-11-21, DB behind host). Whether it is stable across the DB host's k8s pod moves is unknown; a DB-frame gate would need it monitored, which is why a host-frame gate is the safer wiring.
4. **Whether the readiness refresher interval was ever ≠10 s** on the sampled boots was not checked per-boot; every sampled `/readyz` body is consistent with a 10 s cadence.
5. **A true "unit start → readyz green" wall clock was not obtainable** — that needs `journalctl -u ensemble-main.service` (or `systemctl show -p ActiveEnterTimestamp`), neither reachable from a read-only explorer lane. Every number in this file is therefore anchored on a post-manager-init start (promote probe age / derived process start) or on the drill's own harness clock. That direction of error is **optimistic by the interpreter+import+manager-init span**, which no artefact on hand quantifies.
