# Engine Phase-2 OrphanWatcher — STAGED BINARY Verification (2026-11-24)

**Artifact under verification:** `/home/nea/agents-ensemble/releases/v0.31.49` — manifest `source_commit=ac34da1d2`, `rollback_safe=true`, `staged_at=2026-11-24T01:17:05Z`; tag `v0.31.49` (annotated `6c35d35445f111f45d332616837d472e4b566455` → peeled `ac34da1d234fd53f23548529088a4a36ac16f408`); stage worktree `/home/nea/ensemble-src-wt-stage-v03149-enginep2` (detached @ `ac34da1d2`, CLEAN — untouched by this gate).
**Gate type:** staged-release behavioral verification (independent second witness) — boots the staged BINARY under a fenced, cgroup-isolated unit and observes real execution.
**Workers:** preflight `fea6ac4c` · legs A/B/C `63263211` (single instance, scaffolding reused) · docs worktree `cb117a3a`. Scratch/evidence: `/tmp/engine_p2_stage/{preflight.txt, legA_EVIDENCE/, legB_EVIDENCE/, legC_EVIDENCE/}`.
**Fence (every leg):** own PG (dev `ensemble_dev` @127.0.0.1:5432 — read/write only there, no prod contact), own port (19112/19113/19114/19115, all freed), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + `ENSEMBLE_BASH_ORPHAN_REAPER=0` + `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP=0` + `ENSEMBLE_RAM_GUARD=0` + `ENSEMBLE_ORPHAN_F1_ENABLED=0` pinned explicitly, per-variable env scrub (a `POSTGRES_*` glob is a silent no-op), `ENSEMBLE_SELF_ENV=dev`, `ENSEMBLE_DB_AMBIENT_ALLOW=1`, FAKE_HOME + prefix-guarded `rm`, own tmproot copy of the release, `systemd-run --user` transient unit (cgroup isolation).

---

## P1 — 4-WAY SHA, SECOND WITNESS (MEASURED)
Expected: `528e089539f690e9d5bcb7f48f8a6db4fc36c551834d7fbcbaddbc07ec9577e5`.

| Leg | Source | First witness (giter) | Second witness (this gate) | Verdict |
|---|---|---|---|---|
| 1 | release-dir binary `sha256sum releases/v0.31.49/ensemble-prod` | 528e0895…9577e5 | 528e089539f690e9d5bcb7f48f8a6db4fc36c551834d7fbcbaddbc07ec9577e5 | **MATCH** |
| 2 | stage-wt `sha256sum dist/ensemble-prod` | 528e0895…9577e5 | 528e089539f690e9d5bcb7f48f8a6db4fc36c551834d7fbcbaddbc07ec9577e5 | **MATCH** |
| 3 | `manifest.json` → `binary_sha256` | 528e0895…9577e5 | 528e0895…9577e5 | **MATCH** |
| 4 | `dist/ensemble-prod.build-provenance.json` → `artifact_sha256` | 528e0895…9577e5 | 528e0895…9577e5 | **MATCH** |

**4/4 ALL MATCH.** Re-verified after EVERY leg (A, B1, B2, C) — unchanged; release-dir binary mtime still `2026-11-24 01:15:08`; `find releases/v0.31.49 -newermt "<boot-start>"` → empty each time (release dir never written).
Sidecar extras: `git_head=ac34da1d2…`, `git_dirty=false`, `build_at=2026-11-24T01:13:22Z`, `build_tool=pyinstaller:ensemble.spec`. Manifest: `source_commit=ac34da1d2…`, `rollback_safe=true`.

## P2 — PROMPT-BASELINE GATE, BOTH TREES (MEASURED)
Mechanism: `daemon/tools/prompt_artifacts.py` (`VERSIONED_PROMPT_ARTIFACTS`, `.prompt-artifacts.sha256`) driven by `scripts/protection/generate_prompt_artifact_baseline.py`. `--tree` is MANDATORY (a bare `--check` verifies the SCRIPT's repo root — trap filed).

```
python3 <stageWT>/scripts/protection/generate_prompt_artifact_baseline.py --check --tree <stageWT>
  → PROMPT-BASELINE: OK — 217 versioned artifact(s) match .prompt-artifacts.sha256
python3 <stageWT>/scripts/protection/generate_prompt_artifact_baseline.py --check --tree /home/nea/agents-ensemble/releases/v0.31.49
  → PROMPT-BASELINE: OK — 217 versioned artifact(s) match .prompt-artifacts.sha256
```
Both trees = **217**, baseline files byte-identical (`01fbc51db10a2ec44fbfc498df0a468905ec1a982cee53a41c906ff8f52dad01`), matching the manifest's `agents_manifest/".prompt-artifacts.sha256"`; `wc -l` cross-check 217/217. **AGREE + PASS** (stage receipt's "217 OK" confirmed).

## P3 — HARNESS SHIPMENT CHECK (MEASURED) — FINDING, not a failure
`find releases/v0.31.49 -name 'engine_pilot*'` → **empty**. `scripts/soak/engine_pilot_orphan_soak.py` and `scripts/measure/engine_pilot_boot_slo.sh` are **ABSENT from the release payload** and PRESENT in the source tree. By design: the payload's `scripts/` holds exactly the 14-file canonical executor family (`scripts/upgrade/*` ×12 + `stop-ensemble.sh` + `install-dir-guard.sh`); it does not ship repo scripts, and the boot tool takes `--release <dir>` and copies the release into its own tmproot.
**Runbook wording (explicit):** *"run the harness from a checkout at tag v0.31.49"* — the stage worktree (`ac34da1d2` = the tag) is that checkout; the release dir itself never needs to carry the scripts.

---

## LEG A — FLAG ON + REAL EXECUTION (the money leg) — **PASS**
Env: `SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1`, `ENSEMBLE_ENGINE_SHADOW_MODE=0`, `ENSEMBLE_ENGINE_ENABLED=1`, interval override 15 s. Port 19112 · unit `ep2stg-legA-840749` · isolation `0::/user.slice/user-1000.slice/user@1000.service/app.slice/ep2stg-legA-840749.service` (contains `/user.slice/`, **not** `ensemble-main.service`).

| id | started_at | outcome | skip_reason | dur_s | attempt | error |
|---|---|---|---|---|---|---|
|730|02:53:12.808|SUCCEEDED|—|0.0256|1|—|
|738|02:53:28.301|SUCCEEDED|—|0.0147|1|—|
|739|02:53:43.847|SUCCEEDED|—|0.0138|1|—|
|740|02:53:58.917|SUCCEEDED|—|0.0142|1|—|
|741|02:54:13.967|SUCCEEDED|—|0.0154|1|—|
|742|02:54:29.042|SUCCEEDED|—|0.0103|1|—|
|745|02:54:44.133|SUCCEEDED|—|0.0108|1|—|
(`job_id='orphan-watcher'`, `boot_id=ce8a6131f6a74f13b947145476f52ed7`, `host_pid=1191682`; `engine_runs` columns: id, job_id, service_tag, started_at, finished_at, outcome, skip_reason, duration_seconds, attempt, error_class, error_digest, boot_id, host_pid.)
- (a) SUCCEEDED rows in-window ✅ · control: all 11 other jobs in-window = `SKIPPED_SHADOW`.
- (b) legacy suppressed ✅ — `02:53:10 daemon.api INFO OrphanWatcherSweepService legacy loop SUPPRESSED — engine owns execution (SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1, engine enabled, shadow off); sweep object retained for shutdown` + `02:53:12 orphan-watcher pilot: engine executor registered, legacy loop suppressed`; **legacy tick lines = 0**.
- (c) posture ✅ — `engine boot posture: enabled=True shadow=False registrations=12 observer specs (execute_fn withheld); use_engine_flags=SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1 (1/12); executable_specs=1 (orphan-watcher); block_elapsed_ms_gated=435.900 block_elapsed_ms_total=462.694`; `engine tick loop started: resolution=1.0 shadow_mode=False`.
- (d) **daemon-reported boot_epoch→readyz = 9.000 s** (boot_epoch 2026-11-24T02:53:02.846472 → readyz checked_at 02:53:11.693129) — **≤120 s: PASS** · **launch→readyz upper bound = 26.438 s**.
- (e) no wedge ✅ — 7 ticks / 92.3 s at ~15 s cadence, no gaps or doubles; `engine_jobs`: `consecutive_skips=0`, `dead=0`, `last_outcome=SUCCEEDED`, `attempt=1`, `error_digest` NULL on all rows.
- (f) bus_wired: **absent as a field** (no payload/return column), **present as evidence** — `DependencyBus registered` + `dependency_bus late-bound at tick`, and every tick SUCCEEDED (a failed late-bind would not).

## LEG B — FLAG OFF (two arms, both measured) — **PASS**
| arm | flag | port | orphan-watcher rows | legacy | posture | SLO (daemon / upper) |
|---|---|---|---|---|---|---|
| B1 | explicit `0` | 19113 | 7 × `SKIPPED_KILL_SWITCHED` / `KILL_SWITCHED` (ids 750,755,756,757,758,759,762; attempt=1) | **RUNS** — `OrphanWatcherSweepService started: interval=15s` (+ api line `started: interval=15s (default 90s), grace=30s`); "legacy loop SUPPRESSED" = **0** | `use_engine_flags=…=0 (1/12); executable_specs=0` | 8.000 s / 23.391 s |
| B2 | **UNSET** (shipped default) | 19114 | 7 × `SKIPPED_SHADOW` / `SHADOW` (ids 765,766,767,768,775,776,779) | **RUNS** (same two "started" lines); SUPPRESSED = 0 | `use_engine_flags=all unset (0/12); executable_specs=0` | 8.000 s / 23.392 s |
- (c) other observer specs in both arms: `drift-reconcile, eligible-pending ×2, job-lock ×2, mission-strand-heal, plane-sync-watchdog, report-delivery-recovery, stale-pause, watch-reconcile` = all `SKIPPED_SHADOW`. The remaining 3 of 12 (`waiting-children-watchdog`, `sources-gc`, `tmp-image`) are 3600 s-cadence and did not tick in-window (due-ness derives from persisted `engine_jobs.last_run_at`); their `last_outcome` remains `SKIPPED_SHADOW`.
- `engine_jobs` orphan-watcher: B1 `last_outcome=SKIPPED_KILL_SWITCHED, last_skip_reason=KILL_SWITCHED`; B2 `last_outcome=SKIPPED_SHADOW, last_skip_reason=SHADOW`; both `consecutive_skips=0, dead=0, enabled=1, kill_switch_key=SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE, interval_seconds=15.0, version=3`.
- **Corrected fact (measured, not forced):** the kill-switch gate (Tier 1) fires BEFORE the shadow gate, so explicit-`0` ⇒ `SKIPPED_KILL_SWITCHED` while UNSET ⇒ `SKIPPED_SHADOW` (like the other 11 observer specs). The commission's "all rows are SKIPPED_SHADOW" premise is imprecise for the explicit-0 deployment and accurate for the unset one — both arms now measured on the staged binary.

## LEG C — FLAG ON + SHADOW ON (hazardous combo) — **PASS**
Env: flag `=1`, `ENSEMBLE_ENGINE_SHADOW_MODE=1`, `ENSEMBLE_ENGINE_ENABLED=1`. Port 19115 · unit `ep2stg-legC-8d1528` · isolation `…/app.slice/ep2stg-legC-8d1528.service`.
- (a) legacy NOT suppressed ✅ — `OrphanWatcherSweepService started: interval=15s` (+ api line) PRESENT; `"legacy loop SUPPRESSED"` = **0**; `"engine executor registered"` = **0**; `"engine job registered"` = **0**; `"orphan-watcher pilot:"` = **0**; `engine tick loop started: resolution=1.0 shadow_mode=True`.
- (b) engine records `SKIPPED_SHADOW` / `SHADOW` for orphan-watcher — 7 rows (ids 784,789,790,791,792,793,796; `boot_id=444809e89f6a4295815e8254b389dc78`); **0 SUCCEEDED rows in-window** ⇒ no executor executed.
- (c) posture — `enabled=True shadow=True registrations=12 observer specs (execute_fn withheld); use_engine_flags=SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=1 (1/12); executable_specs=0; block_elapsed_ms_gated=107.887 block_elapsed_ms_total=125.695`.
- (d) `engine_jobs` orphan-watcher — `last_outcome=SKIPPED_SHADOW, last_skip_reason=SHADOW, consecutive_skips=0, dead=0, trigger_kind=interval, version=3`.
- (e) other observer specs in-window — all `SKIPPED_SHADOW`.
- (f) **daemon-reported boot_epoch→readyz = 9.000 s** · **launch→readyz upper bound = 25.424 s**.
- **ONE-SENTENCE CONCLUSION:** *This combo is NOT a dead lane — the ownership conjunct fails on the shadow leg, so the legacy loop owns execution (service started, suppression line absent) while the engine still records a `SKIPPED_SHADOW` row for orphan-watcher on every tick with no executor attached (`executable_specs=0`, 0 SUCCEEDED rows), i.e. the engine observes the spec and the sweep lane keeps working.*

### Truth table as MEASURED on staged v0.31.49 (all four cells)
`flag=1 ∧ shadow=0` → `SUCCEEDED`, legacy SUPPRESSED, `executable_specs=1` · `flag=1 ∧ shadow=1` → `SKIPPED_SHADOW`, legacy RUNS, `executable_specs=0` · `flag=0` → `SKIPPED_KILL_SWITCHED`, legacy RUNS, `executable_specs=0` · `flag UNSET` → `SKIPPED_SHADOW`, legacy RUNS, `executable_specs=0`.

### Boot-SLO table (both numbers, labeled; judged number = daemon-reported)
| Leg | daemon-reported boot_epoch→readyz (≤120 s judged) | launch→readyz UPPER BOUND | verdict |
|---|---|---|---|
| A (flag ON, shadow OFF) | **9.000 s** | 26.438 s | PASS |
| B1 (flag=0) | 8.000 s | 23.391 s | PASS (report-only) |
| B2 (flag UNSET) | 8.000 s | 23.392 s | PASS (report-only) |
| C (flag ON, shadow ON) | 9.000 s | 25.424 s | PASS (report-only) |

Cadence override: `SERVICES_ORPHAN_WATCHER_SWEEP_INTERVAL_SECONDS=15` (default 90 s) — shrunk so ≥5 ticks land inside a bounded window WITHOUT extending any timeout; the soak harness (`scripts/soak/engine_pilot_orphan_soak.py`) remains the long-window judge at production cadence.

---

## P4 — LIVE-UNTOUCHED ASSERTION (MEASURED, with one external event disclosed)
Pre-gate baseline (preflight): `current=v0.31.42`, `previous=v0.31.37`, `in_flight=null`, `pending_op=null`, `quarantined=["v0.28.5","v0.31.8"]`; symlink `current → releases/v0.31.42`.
**External event during LEG A (NOT attributable to this gate):** an OPERATOR-initiated live promote of the same version landed inside leg A's boot window — `arm 02:52:04Z → nonce_consumed 02:52:06Z → promote-start 02:52:22Z → intent_flip 02:52:40Z ("intentional promote target v0.31.49 … operator-initiated") → watchdog_hold 02:52:45Z → flip_pending_verdict 02:53:59Z → commit 02:56:25Z (gate+soak green) → sweep 02:56:37Z → retention_evict of v0.31.37/.45/.46/.48 (02:56:38–02:57:03Z)`, `run_id=r-20261124-024726-0080`, `pid=1189974`. The `ensemble-main.service` stop at 02:52:47Z was that flip's restart (it terminated our in-flight launcher shell, rc=-9 — our isolated daemon was unaffected and kept ticking).
**Attribution proof (ours wrote ZERO live-journal rows):** every leg's daemon `INSTALL_DIR` was its own `/tmp` tmproot ⇒ no `releases/state.json` was ever created there (`find <tmproot> -name state.json` → empty); all in-window live rows carry the ceremony's `run_id`/`pid`, never ours. Post-event baseline (legs B1/B2/C): `current=v0.31.49, previous=v0.31.42, in_flight=None, pending_op=None, history_len=427, last_ts=2026-11-24T02:57:03Z` — **IDENTICAL before and after every leg** ⇒ 0 promote-class rows (arm, nonce_consumed, promote-start, intent_flip, watchdog_hold, flip_pending_verdict, commit, sweep, executor_exit, halt) inside our windows. Symlink now `current → releases/v0.31.49` (operator's promote, not ours). Live daemon healthy throughout: `livez {"status":"alive","version":"0.31.49"}`, `readyz {"status":"ready", database/queue_freshness/services/checkpoint_saver all true}`.

## Fence audit (per boot) — MEASURED
- Isolation: every daemon's `/proc/<pid>/cgroup` = `0::/user.slice/user-1000.slice/user@1000.service/app.slice/ep2stg-<leg>-<hex>.service` — `/user.slice/` present, `ensemble-main.service` ABSENT (units via `XDG_RUNTIME_DIR=/run/user/1000 systemd-run --user --collect`).
- Terminator pins, all explicitly OFF: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `ENSEMBLE_BASH_ORPHAN_REAPER=0` (master), `ENSEMBLE_BASH_ORPHAN_REAPER_BOOT_SWEEP=0`, `ENSEMBLE_RAM_GUARD=0`, `ENSEMBLE_ORPHAN_F1_ENABLED=0` — boot log confirms: *"bash reaper: master switch OFF — boot reconcile is a no-op (no repository access)"*.
- FAKE_HOME mktemp + prefix-guarded `rm -rf`; own tmproot copy per leg; `ENSEMBLE_CONFIG=<tmproot>/config.yaml`; `PORT == DAEMON_PORT` (the `.env`'s `PORT=8079` trap avoided via LEG_PORT capture); `POSTGRES_URL` plain `postgresql://` (no `+psycopg`); `ENSEMBLE_SELF_ENV=dev`; `ENSEMBLE_DB_AMBIENT_ALLOW=1`.
- DB: dev `ensemble_dev` @127.0.0.1:5432 only — **no prod contact in any leg**. Teardown: SIGTERM→SIGKILL of own children only; no `/proc/*/cmdline` references any tmproot/FAKE_HOME after teardown; ports 19112/19113/19114/19115 all FREE; scratch + FAKE_HOMEs removed by prefix-guarded `rm`.
- Secrets never printed. No code/test modification. No promote/stage/tag/merge/push, no live flip, no daemon restart, no writes to the live install or prod DB.

---

## Findings (REPORT-ONLY — no code, config, or documentation was modified by this gate)
1. 🟠 **Harness not in the release payload (P3).** By design, but the ceremony runbook must state: *"run the harness from a checkout at tag v0.31.49"* — the release dir carries only the 14-file executor family.
2. 🟠 **`scripts/measure/engine_pilot_boot_slo.sh` cannot serve staged-release legs as-is:** (a) its EXIT trap tears the daemon down AT READYZ ⇒ no observation window; (b) its `ENV_FILE` resolution finds no `.env` for the stage worktree (absent) ⇒ it would boot with no PG credentials. Mitigation used here: a manual wrapper that replicates the fence verbatim and reproduces the tool's SLO method exactly (boot_epoch log line → readyz `checked_at`; launch stopwatch for the upper bound). Needed tool change: `--observe/--keep-alive` + explicit `--env-file`.
3. 🟠 **The boot tool pins only 1 of 3 boot-time process terminators** (sweep pinned; bash-orphan-reaper family and RAM guard unpinned ⇒ their ON-by-default paths apply). Our wrapper pinned all three; the tool should too.
4. 🟡 **External live promote of v0.31.49 landed during LEG A** (operator-initiated; journal-evidenced; not ours). The commission's P4 baseline `current=v0.31.42` was superseded mid-gate; legs B/C were re-baselined to `current=v0.31.49` and assert "unchanged across the leg" instead.
5. 🟡 **Legacy per-tick lines do not exist** in the source (emitted only on cancel / dead-detection) ⇒ legacy ownership is proven by the "started" line + suppression-line ABSENCE, not by a tick count. Any future leg asserting "legacy tick count" must assert those two structural facts instead.
6. 🟡 **3 of 12 observer specs have 3600 s cadence** ⇒ they cannot tick inside a ~2-minute window; their `last_outcome=SKIPPED_SHADOW` is carried over from an earlier boot (measured, not inferred).
7. 🟢 Corpus defect (fix filed by the preflight worker): `staged-release-artifact-verification` §5 still states "the release dir must have no `scripts/`", cites a stale "161 versioned artifact(s)" literal, and omits the mandatory `--check --tree <dir>`.
8. 🟢 Per-leg boot SLO: daemon-reported 8.000–9.000 s (bound ≤120 s) and launch→readyz upper bound 23.391–26.438 s across four boots — no boot approached the SLO.

Measured vs inferred: every count, row, log line, hash, SLO number, cgroup line and journal ts above is MEASURED (commands + captured lines in the evidence dirs). Inferred only: (i) the leg-A attribution of the in-window promote rows to the operator ceremony (supported by `run_id`/`pid` distinctness, the flip's own log vocabulary, and our tmproot proof of zero writes) and (ii) that the 3 non-ticking specs would stamp `SKIPPED_SHADOW` at their next due tick (inferred from their persisted `last_outcome`, not observed in-window).

---

## VERDICT: PASS

(Staged v0.31.49 behaves exactly as designed across all three legs — real execution under flag ON, kill-switch precedence over the shadow gate, legacy ownership preserved in both non-owning combos; 4-way hash 4/4; prompt-baseline 217/217 on both trees; boot SLO 8–9 s daemon-reported against the ≤120 s bound; the live install was untouched by this gate — its mid-gate version change was an operator promote, evidenced in the journal, with zero journal rows written by any of our legs. Findings 1–3 are runbook/tooling items for the owning lane; none blocks the staged artifact.)
