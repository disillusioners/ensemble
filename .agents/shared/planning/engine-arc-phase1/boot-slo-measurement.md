# Engine Arc Phase-1 — Boot SLO Measurement (boot_epoch → readyz-green)

**Status:** measurement-verdict document for the Engine Arc Phase-1 planning lane. Planning only — no code, no commits, no live contact (all telemetry was gathered read-only by the explorer lane and is reproduced here, not re-measured).
**SLO under test (Emperor-set, CLOSED 2026-11-21):** `boot_epoch → readyz-green ≤ 120 s` — verbatim "Boot wall clock is 2 minutes". Binding for three consumers (§1).
**Measured:** 2026-11-21 (collector: explorer lane, read-only). Live install `/home/nea/agents-ensemble` (READ-ONLY). Repo tree `/home/nea/ensemble-src-wt-engine-arc-p1`, branch `feature/engine-arc-phase1`, HEAD `aed3eabce` = base `b71742fe3` (v0.31.23) + planning docs. Live serving version at measurement time: v0.31.23 (`releases/state.json` → `current=v0.31.23, previous=v0.31.22, in_flight=null`).
**Primary source:** `research-bootslo-raw.md` (this directory) — the explorer's raw measurement (239 lines). Anchors verified at this tree via `research-anchors.md`. Gate-wiring facts cross-checked against `test-strategy.md` §5.

---

## 1. SLO statement and binding scope

**SLO:** `boot_epoch → readyz-green ≤ 120 s`. "Boot wall clock is 2 minutes."

**Binding scope — three consumers (Emperor-ratified 2026-11-21):**

1. **Boot-phase acceptance gate** — the engine's boot phase must not push a daemon boot past 120 s. The dormant-registration default boot (engine registered, observing, executing nothing) and the `SERVICES_<CANARY>_USE_ENGINE=1` activation boot must both stay ≤120 s. `test-strategy.md` §5 carries the gate contract; the FORMAL baseline lives in *this* doc (cited by name, not re-derived there).
2. **BOOT_SWEEP deadline design** — the `BOOT_SWEEP` boot-phase class is *awaited and deadlined* (decision-report §B.2). The ~11 awaited serialized passes each get a deadline; the sweep's fail-open ×2 behaviour and default-ON polarity must be preserved (engine ADDS a deadline, never flips defaults).
3. **Watchdog boot-window expectations** — the wedge/loop-lag watchdog and the boot-subprocess sweep operate inside the boot window; their thresholds and the `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` guard (pinned `=0` explicitly, never `env -u`) must remain coherent with a 120 s outer bound.

**Measurement definition (what "readyz-green" means):** first `/readyz` response with `status == "ready"` — all of `database` / `queue_freshness` / `services` / `checkpoint_saver` true and `detail.reasons == []`. Timestamp choice is the gate-wiring decision (§5).

---

## 2. Measurement method and its one structural trap

**Clock frames — the trap (load-bearing for gate design).** `boot_epoch` comes from the PostgreSQL host (`10.44.0.2`, DB clock); `checked_at` and `uptime_seconds` come from the daemon host. On the 2026-11-21T10:39 boot the two frames differ by ≈4.0 s (DB clock *behind* host clock); on the 2026-11-21T01:56 boot ≈4.7 s. A literal `boot_epoch → checked_at` subtraction **inflates** the measured boot by the skew (§2.2: 20.8 s mixed-frame vs 16.8 s host-only for the *same* boot). The skew is not a constant the gate can assume. **The gate must pick ONE frame** (§5).

**Secondary traps (confirmed on the artefacts):**

- `uptime_seconds` is measured from `app.state.start_time` set at `daemon/api.py:1012` — *after* `manager.initialize()`, `capture_boot_epoch`, MQ boot recovery, critical-notes probe, and lease recovery. It is a **lower bound** (interpreter/import/manager-init time excluded) — optimistic direction.
- `checked_at` is refreshed on a **10 s** interval by `_periodic_readiness_refresh_loop` (`daemon/api.py:5190+`); first tick fires immediately at t=0 (`:5215-5217`), so a healthy-DB boot can be green at the first refresh; later observations are quantized to ±10 s.
- `data/upgrade.log` carries **no timestamps on older entries**; only the `/readyz` body's `checked_at` dates them. Per-run files `data/upgrade/live/r-*.log` (2026-11-17 onward) DO timestamp both probes.
- `.launcher-state` was NOT used (updates only on exit — not current-boot evidence).
- Log rotation: `data/logs/ensemble.log*` (6 files) contain **zero** `Boot epoch captured` lines for recent boots; `data/launcher.log` holds one stale boot (v0.28.4, 2026-11-01T22:32) and is not a current-boot source.

**Method labels used below:** `promote-log-derived` (probe age at readyz-green, host clock) · `boot_epoch-derived` (DB clock, mixed-frame — trap above) · `journal-derived` (drill harness, cursor-anchored) · `recorded-evidence` (tester RESULTS / incident file). No two classes share a clock frame, so they are aggregated separately, not pooled.

---

## 3. Measurement table (from the explorer's data — not re-measured)

### 3.1 Live promote boots — probe age at readyz-green (n = 19)

**Method label:** `promote-log-derived (probe-age at readyz-green, host clock)`. Derivation: `readyz_probe_age = (readyz probe wall clock) − (process start)`, with `process start = livez_probe_wall − livez.uptime_seconds`. Rows 1–17: `upgrade.log` carries no probe wall timestamp, only the `/readyz` body; livez→readyz probe spacing observed directly in the 2026-11-21 runs is 1 s, so `readyz_probe_age ≈ uptime_seconds + 1 s` (upper bound; residual error a few seconds at most, never material at a 120 s bar). Rows 18–19: both probes timestamped (`livez OK:` / `readyz OK:` lines), so the value is exact.

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

**Aggregates (n=19):** median **13.1 s** · mean **13.9 s** · p90 **22.8 s** (nearest-rank) · max **23.2 s** (v0.31.22, 2026-11-21) · min **5.8 s**. Worst case consumes **19.3 % of the 120 s budget**. Sensitivity: switching rows 18–19 to their composite-stamp ages (17.0 / 16.8) lowers the max to 21.2 s (v0.27.8) and leaves the median at 13.1 s — every aggregation path stays comfortably under the bar.

Note on rows 18–19 — two different readings, both inside budget:
- **Probe-age reading** (reported above): the readyz probe's own wall clock minus derived process start = 23.2 s / 22.8 s. This is what the promote gate "sees".
- **Composite-stamp reading**: the `/readyz` body's `checked_at` (readiness refresher stamp, 6 s older than the probe) minus derived process start = **17.0 s / 16.8 s**. The composite was already `ready` at that stamp, so green existed **no later than** 17.0 s / 16.8 s.

The same staleness applies to rows 1–17 (their `checked_at` is also a refresher stamp), so every number in the table is an **upper bound on time-to-first-green**, inflated by at most one refresh interval (10 s) plus the 1 s livez→readyz spacing. Trend note: early-October boots (rows 1–5) run 6–12 s; late-October/November boots (rows 15–19) run 16–23 s, tracking payload growth (skills / MCP warm-up / memoir workers).

### 3.2 Live boots with an EXACT `boot_epoch` (boot-records × promote `/readyz` body) — n = 2

**Method label:** `boot_epoch-derived (mixed clock — see §2)`. Trap: `boot_epoch` (DB clock) vs `checked_at` (host clock) → ≈4 s inflation.

| Boot | `boot_epoch` (DB clock) | `/readyz` green `checked_at` (host clock) | delta (s) | Host-only delta for the same boot (s) | Evidence |
|---|---|---|---|---|---|
| 2026-11-21T01:56 | `2026-11-21T01:56:29.025080Z` | `2026-11-21T01:56:50.749529` | **21.7** | 17.0 | `boot-records/20261121T015629025080Z.json`; `r-20261121-015322-3192.log:31-34` |
| 2026-11-21T10:39 | `2026-11-21T10:39:02.137280Z` | `2026-11-21T10:39:22.940126` | **20.8** | 16.8 | `boot-records/20261121T103902137280Z.json`; `r-20261121-103633-ca48.log:256-259` |

**Aggregate:** median 21.3 s · max 21.7 s. **Both under 120 s.** The ~4 s gap to the host-only figure is DB-clock skew, not boot cost. (Other boot records on disk with no paired `/readyz` evidence — 12+ records back to 2026-11-11 — are not countable; listed in `research-bootslo-raw.md` §2.2.)

### 3.3 Hard-reboot drill — n = 3 (journal-anchored, daemon recovery path)

**Method label:** `journal-derived (drill harness)`. Anchor = transient-unit stop → fresh start; end = `/readyz` 200 with all components true. Harness explicitly excludes host firmware/kernel boot ("host boot time + ~14 s"). Closest existing mechanical precedent for a boot-SLO measurement; cursor anchoring prevents latching a pre-crash marker.

| Run | T_restart → T_readyz | Evidence |
|---|---|---|
| #3 (pre-anchor) | **14 s** | `.agents/tester/RESULTS/2026-10-21-hard-reboot-p4-drill-harness.md:103` |
| #4 (pre-anchor) | **13 s** | same, `:104` |
| final @ `8eb8ef643` (journal-cursor anchored) | **15 s** | same, `:105` |

### 3.4 Recorded dev / sandbox cold boots — n = 2 (fresh PG, column-ensure path)

| Boot | Measurement | delta | Evidence |
|---|---|---|---|
| designer→sketcher→critic dev E2E, v0.19.0, 2026-10-11 | launch 07:57:38Z → banner +9 s → `/readyz` green 07:58:45Z (`uptime 60 s`) | **67 s** | `.agents/tester/RESULTS/2026-10-11-designer-pipeline-dev-e2e.md:28` |
| resource-telemetry fix verification, v0.25.3, 2026-10-19 | first `/livez` 200 at t=23.15 s (process age 5.49 s); `/readyz` 200 same poll | **≈23 s from launch** | `.agents/tester/RESULTS/2026-10-19-resource-telemetry-fix-verification.md:395` |

The 67 s case is the **worst non-outage boot on record** and is a *dev sandbox with a disposable fresh PG16 cluster* (`initdb -A trust`, 56 tables) — sandbox-cold-DB shape, not a live promote boot. Still under 120 s.

### 3.5 Demo promotes (fast-class corroboration, different install — NOT counted)

| Boot | livez / readyz | Evidence |
|---|---|---|
| 2026-08-23 v0.10.7-p2.3-b65 | livez ~3 s, readyz ~4 s (from flip) | `.agents/tester/RESULTS/2026-08-23-p2-3-b65-promote-t8-recapture.md:48,:99` |
| 2026-08-23 DR-5 ari-driven | readyz ~4 s from flip | `.agents/tester/RESULTS/2026-08-23-p2-3-b7-dr5-ari-e2e.md:164` |
| 2026-08-22 P2.1 demo pipeline | livez OK (uptime 1.35 s / 0.10 s), readyz OK same round | `.agents/tester/RESULTS/2026-08-22-p2-1-demo-e2e-release-pipeline.md:46,:105` |
| 2026-08-22 AR phase-1 restart run | `/livez` green t+17.09 s; `/readyz` green ≪120 s | `.agents/tester/RESULTS/2026-08-22-ar-phase1-followups-verification.md:23` |

These anchor on *flip/respawn*, not `boot_epoch`, so they are corroboration only.

### 3.6 Outlier / failure classes on record

| Instance | Observation | Attribution |
|---|---|---|
| 2026-10-09 v0.18.5 promote | `/readyz` **never** green inside the 120 s gate budget; ADR-005 auto-rollback of a healthy build. Same DB (`10.44.0.2:5432/ensemble_prod`) had a 19:53–20:50Z outage window. | `.agents/shared/incident-20261010-checkpoint-connection-pool.md:104-106`; class analysis `.agents/shared/planning/soak-gate-mcp-warmup/decisions.md:72,:193` |
| 2026-10-26 v0.27.8 soak | `/readyz` **transient-exhausted** at 45/60/90/105 s → cap-reach, rollback (probe-level, not boot) | `data/upgrade.log:577-583` |
| 2026-11-19 v0.31.8 | hand-back "not confirmed within 300 s"; **no readyz evidence** — the :9797 answerer was outside the unit lineage (attribution refusal class), not a slow boot | `data/upgrade/live/r-20261119-043446-c8da.log:22` |
| 2026-11-17 / 18 / 19 promotes (v0.31.0 / v0.31.1 / v0.31.2 / v0.31.10) | halted at hand-back **attribution** (`port-holder NOT in the supervision-owned pid set`) — the boot itself was not measured; the daemon-side `serving_green_commit` converged afterwards | `r-20261117-171207-e2f9.log:22-25`, `r-20261118-034632-19db.log:22-25`, `r-20261118-042553-f188.log:22-25`, `r-20261119-132823-3a3c.log:22-25` |

**Explicitly NOT a boot-time signal:** `is-active probe answered rc=3 (state=inactive)` and `is-active probe exceeded budget (5s)` appear in every promote log; they are `systemctl`-poll artefacts. The `unit hand-back COMPLETE` line that follows is the real start confirmation (new MainPID + port + `/livez`).

### 3.7 Summary aggregate table (all classes)

| Source class | n | median | p90 | max | vs 120 s |
|---|---|---|---|---|---|
| Live promote boots (`data/upgrade.log` + `data/upgrade/live/*.log`, probe-age at readyz-green) | 19 | **13.1 s** | **22.8 s** | **23.2 s** | **COMFORTABLY UNDER** (max = 19.3 % of budget) |
| boot_epoch→readyz (exact pair, mixed clock — see §2) | 2 | 21.3 s | — | 21.7 s | UNDER |
| Hard-reboot drill (transient unit stop → fresh start → `/readyz` 200) | 3 | 14 s | — | 15 s | UNDER |
| Recorded dev/sandbox cold boots (fresh PG, column-ensure path) | 2 | 45 s | — | **67 s** | UNDER — thinnest margin; **PRE-EXISTING** (sandbox-cold-DB shape, 2026-10-11) |
| Recorded DB-outage class (2026-10-09 v0.18.5) | 1 | — | — | **never green inside the 120 s probe budget** | FAILED — **PRE-EXISTING** (probe-timeout-under-load class) |

**Sample count and independence:** 27 source-labeled boot observations (19 live promote boots + 2 exact `boot_epoch` pairs + 3 drill runs + 2 dev/sandbox cold boots + 1 recorded outage-class boot), spanning 4 distinct measurement methods and 8 distinct artefact families (`data/upgrade.log`, `data/upgrade/live/r-*.log`, `data/promote-v0296.log`, `wedge-captures/boot-records/*.json`, `releases/state.json`, tester RESULTS, the incident file, and live installer logs). Four demo-install promotes are listed as corroboration only and are NOT counted. All required bars met: ≥8 live promote boots (19), exact `boot_epoch` pairs (2), an independent drill harness (3), and a cold/loaded-DB class (2).

---

## 4. VERDICT vs 120 s

**Typical live boots are COMFORTABLY UNDER.** The promote probe confirms green at a process age of ~11–15 s (median 13.1 s); the observed maximum across 19 live promote boots is 23.2 s (v0.31.22, 2026-11-21), and using the earlier-composite reading it is 21.2 s (v0.27.8, 2026-10-26). Every recorded live boot is under 120 s by a wide margin (worst case ≤19.3 % of the budget). No reading, in either direction, approaches 120 s for any live boot.

**The near/over cases are PRE-EXISTING conditions, stated plainly — NOT Phase-1 regressions:**

1. **Dev/sandbox cold boot against a fresh PostgreSQL cluster at 67 s** (2026-10-11, designer→sketcher→critic dev E2E, disposable PG16 `initdb -A trust`, 56 tables). Under the bar but within 1.8× of it. This is the sandbox-cold-DB shape, not a live promote boot, and it predates this lane by months.
2. **2026-10-09 v0.18.5 promote where `/readyz` never went green inside the 120 s gate budget** because the database was mid-outage (19:53–20:50Z outage window on `10.44.0.2:5432/ensemble_prod`). This is the probe-timeout-under-load class, which predates this lane by ~6 weeks and carries no relation to the engine build.

**Reading discipline:** every figure is *time to a `readyz` 200 with all components true*. Two readings exist per boot — "probe says green now" (probe wall clock) and "the cached composite was already green at `checked_at`" (≤1 refresh interval earlier). §3.1 reports the probe reading (the conservative, larger one); the composite reading is ~4–6 s smaller and is stated where it matters. No reading approaches 120 s for any live boot.

---

## 5. Gate-wiring recommendation

**RECOMMENDATION: (b) — the cleanest host-frame gate.** Do not mix clock frames.

The three candidate wirings (from explorer facts):

- **(a) NEVER subtract `boot_epoch` (DB clock) from `readyz detail.checked_at` (host clock).** The ~4 s offset inflates the measured boot and is not a constant across the DB host's k8s pod moves. Hard prohibition.
- **(b) Host-frame gate (RECOMMENDED):** `boot_start = unit ActiveEnterTimestamp` (or the launcher's `starting: <binary>` line write time, `data/launcher.log:3` shape) → poll `/readyz` until `status == "ready"` and `detail.reasons == []` → `boot_wall = readyz_green − boot_start`; assert `≤ 120 s`. Both anchors host clock; exact, no skew. `scripts/upgrade/boot_smoke.sh:496-524` already implements the 1 s poll with component assertions; it lacks only (i) a start anchor and (ii) the subtraction, plus a `POLL_TIMEOUT` raise from 45 s to ≥120 s. Mechanical recipe, no new daemon telemetry.
- **(c) If the gate must bind to `boot_epoch` literally (Emperor's named anchor):** requires a readyz-green stamp recorded in DB time, which does not exist today. Cheapest honest wiring: have the readiness refresher record its **first** green as `SELECT now()` into the existing per-boot record (`write_boot_record`, `daemon/services/wedge_capture/boot_record.py:269`) — one field, no new subsystem — after which the gate is a two-field subtraction inside one clock frame (`readyz_green_db_clock − boot_epoch`).

**Tradeoff (why (b) over (c)):** (b) is the most honest and complete wall clock — it includes interpreter start, imports, config load, engine creation, and `manager.initialize()`, which (c)'s `boot_epoch` anchor (captured at `daemon/api.py:969-971`, *after* `manager.initialize()`) silently excludes, making the SLO easier than "boot wall clock" implies. (b) needs one piece of new wiring (start-anchor capture, which requires `journalctl`/`systemctl show -p ActiveEnterTimestamp` — not reachable from a read-only explorer lane, open question 1) but no daemon code change. (c) gives durability (mirrored into `boot-records/*.json`) and binds to the Emperor's named anchor, but is optimistic by the unquantified interpreter/import/manager-init span and requires monitoring the DB-host clock offset across pod moves. Given the ~5× headroom (live max 23.2 s) and ~1.8× (cold-DB 67 s), neither would false-fire on any boot class on record except the DB-outage class (which fails closed by design) — but for a gate that must not false-fire on slow boots, exactness (b) is the defensible choice.

**Coordination note:** `test-strategy.md` §5 currently defaults to the (c)-shaped path (start = boot-epoch stamp log line `boot_epoch.py:132`, end = `/readyz` green, pack-side timed probe). Adopting (b) means reconciling that default. This is a decision-level item (Emperor/architect), not a blocker — flagged in §7. **[APPLIED — approver iteration-001 (fold B-1, 2026-11-21):** the reconciliation is done; test-strategy §5's default is now the (b) host-frame probe — the "currently defaults to (c)-shaped" sentence above was true only pre-fold.**]**

**Prohibited as boot evidence regardless of wiring:** `.launcher-state` (updates only on exit); the `is-active rc=3` / `is-active exceeded budget (5s)` lines (systemctl-poll artefacts); the `hand-back verify … not confirmed within 300s` line (attribution/`MainPID` conjunct, not boot duration); `uptime at livez OK` alone when the promote probe is in a retry loop.

---

## 6. Caveats (carrying the explorer's honesty)

1. **Unit-start→readyz full span is unquantified from a read-only lane.** Every number here is anchored on a post-manager-init start (promote probe age / derived process start) or on the drill's own harness clock. The excluded span = interpreter + imports + manager-init. That direction of error is **optimistic** (makes the SLO easier, not harder) — so the "comfortably under" verdict is if anything understated, but the exact margin is unknown.
2. **DB-vs-host clock offset stability is unknown** across the DB host's k8s pod moves (measured ≈4.0 s and ≈4.7 s on two 2026-11-21 boots, DB behind host). This argues for the host-frame gate (§5 (b)) and for monitoring the offset if a DB-frame gate is ever adopted.
3. **Inherited narrative figures are UNVERIFIED and NOT counted:** "uptime 302 s" (2026-10-09 v0.18.5) and "~54 s migration-bearing boot" (2026-11-08) exist only in narrative/derived form; no artefact carries them. The 2026-11-08 promote on disc reads 16.4 s, not 54 s. Neither changes the verdict (both are under-or-near the bar), and both are listed in `research-bootslo-raw.md` §5.1 as not-counted for traceability.
4. **Log rotation evicted boot markers** — `data/logs/ensemble.log*` (6 files) contain zero `Boot epoch captured` lines for recent boots; `data/launcher.log` holds one stale boot (v0.28.4). The durable per-boot artefact (`boot-records/*.json`, 43 files, cap 50) carries the start anchor and **no** end anchor.
5. **10 s quantization ceiling** — the readiness refresher interval means the end anchor is ±10 s unless the refresher interval is tightened for the measurement window or the gate records probe wall time and accepts the ≤1-interval lag.
6. **Probe-age is an upper bound** (rows 1–17: `uptime + 1 s` approximation; rows 18–19 exact). Switching to composite-stamp ages only lowers numbers (max 21.2 s, median unchanged).
7. **`journalctl` for `ensemble-main.service` was not reachable** from the read-only explorer lane — a true unit-start→readyz wall clock for the current v0.31.23 process was not collected. The promote-side numbers are the substitute.

---

## 7. Engine-design implication

**Available envelope:** 120 s SLO − 23.2 s worst observed live max = **≈96.8 s (≈97 s) headroom** on the live-promote class; 120 s − 67 s worst recorded cold-DB boot = **≈53 s headroom** on the cold-DB class. Typical (median 13.1 s) leaves ≈107 s.

**Constraint:** the dormant registration sweep (engine registered, observing, executing nothing in Phase 1) must stay cost-bounded within that envelope — registration + observability overhead only, no execution transfer. The Phase-2+ execution transfer (one module at a time, behind one-flag reverts) runs the awaited passes through the engine with per-job deadlines, so the boot-phase cost must be budgeted per migrant, not assumed.

**Boot-phase classes (decision-report §B.2):** `BOOT_SWEEP` (awaited, deadlined), `BOOT_PASS` (awaited best-effort), `FIRST_TICK_IMMEDIATE`, `FIRST_TICK_DELAYED(+offset)`. Per-job first-tick jitter desynchronizes the 300 s club; drift's +30 s becomes a first-class `first_tick_offset_s`. The ~11 awaited passes stay serialized but each gets a deadline. The engine adds scheduling overhead only (house <1-core constraint) — it must not be sold as an efficiency play.

**The actual budget decomposition** (how the ~97 s / ~53 s is split across interpreter, imports, config, engine creation, manager-init, boot passes, readiness refresh) is carried by the sibling's boot-phase plan (`phaseN-plan.md`), not duplicated here. This doc supplies the measured baseline that plan must not regress against (must-not-regress clause in `test-strategy.md` §5: ≤120 s hard AND within the recorded baseline dispersion THIS DOC ESTABLISHES — its §3/§4 aggregates: live-promote median 13.1 s / p90 22.8 s / max 23.2 s; recorded dev/sandbox cold boots 45/67 s; any engine-attributable delta measured against those recorded numbers, not assumed) **[re-pointed — approver iteration-002 (N-d): the earlier "within the noise band this doc establishes" cited a term this doc never defines; the conjunct now reads on the doc's own recorded aggregates, mirroring the test-strategy §5 fold]**.

**Open questions (decision-level, Emperor/architect):**
1. Start-anchor capture for the (b) gate — `journalctl`/`systemctl show -p ActiveEnterTimestamp` is not reachable from a read-only explorer lane; needs an operator-lane or build-side capture. Does the Emperor want the build to emit an `ActiveEnterTimestamp`-equivalent line (the `api.py:1082-1093` deferred-task pattern is the candidate seam), or is operator-lane capture acceptable? [RESOLVED-BY-RULING (d): harness/promote-lane owns the start anchor; daemon-side emit = named fallback]
2. Reconcile `test-strategy.md` §5's default (boot-epoch start, (c)-shaped) with this doc's recommendation ((b), host-frame). Adopt (b) and update the test-strategy default, or ratify (c) as the Emperor's-named-anchor gate? [RESOLVED-BY-RULING (d): (b) host-frame adopted. **Amended — approver iteration-001 (2026-11-21):** the "test-strategy §5 probe rewritten per AMD-6" claim was asserted at round-1 but the rewrite itself LANDED only at approver iteration-001 (fold item B-1) — the claim is true as of that fold, not round-1.]
3. Is the cold-DB class (67 s, 1.8× headroom) in scope for the gate, or only the live-promote class? If in scope, the gate's effective budget tightens to ~53 s for that class and the engine's per-boot overhead must be budgeted against the tighter envelope. [RESOLVED-BY-RULING — approver iteration-002 (N-f), per `plan-overview.md` §6 / decisions D-2: **gate scope covers ALL boot classes this lane produces, dev cold-DB included; live-promote stays the binding reference class.** The tighter cold-DB envelope (~53 s class per §3.2) is a must-not-regress budget input for the engine's per-boot overhead, not a second gate bar.]

---

## 8. References

- `research-bootslo-raw.md` (this directory) — explorer's raw measurement (primary source; all numbers reproduced here).
- `research-anchors.md` (this directory) — re-verified code anchors (boot_epoch, readiness refresher, boot_smoke.sh, boot_record writer).
- `../central-polling-engine-study/decision-report.md` §B.2 (frozen engine design incl. boot ordering + `SERVICES_<MODULE>_USE_ENGINE`), §C.1 (dormant shadow), §E.3 (gate protocol).
- `test-strategy.md` (this directory) §5 (boot-SLO acceptance gate; cites this doc as the FORMAL baseline).
- `../central-polling-engine-study/liveness-evidence.md`, `census-periodic.md` — liveness verdicts + mechanism inventory (pilot context).
- Code anchors: `daemon/services/boot_epoch.py:91-133` (log at `:132`); `daemon/api.py:969-971` (capture call site), `:1012` (start_time), `:5190-5265` (readiness refresher, first tick t=0 at `:5215-5217`), `:6325`/`:6594` (livez/health uptime); `daemon/services/readiness.py:1377+`; `daemon/services/wedge_capture/boot_record.py:54,58,136-150,269`; `scripts/upgrade/lib.sh:9095-9096` (`gate_livez`/`gate_readyz`), `:146-147` (`LIVEZ_BUDGET_S=180`/`READYZ_BUDGET_S=120`); `scripts/upgrade/boot_smoke.sh:60,496-524` (poll, 45 s default timeout).
- Artefacts: `data/upgrade.log` (613 lines), `data/upgrade/live/r-*.log`, `data/promote-v0296.log`, `data/wedge-captures/boot-records/*.json` (43 files, cap 50), `releases/state.json`, tester RESULTS, incident file.
