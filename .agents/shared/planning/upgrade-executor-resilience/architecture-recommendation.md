# Upgrade-Executor Resilience & Simplification — Architecture Recommendation

**Date:** 2026-10-07 (delivered ~11:15 UTC)
**Status:** DECISION DOCUMENT — awaiting user adoption decision
**Commission:** Read-only assessment (one-file deliverable) in response to the 2026-10-07 v0.18.0 live-promote incident chain
**Assessment legs:** forensics worker `c0c88804` (`resilience-design`; file:line facts) + council `8614af1b` (`trade-off-analysis`; councilors `agentic` + `coding`, 2/2 completed, zero factual conflicts, no refinement round needed)
**Repo state at assessment:** branch `latest` @ `2753ee78d` — frozen; live v0.18.0 promote IN FLIGHT via service lane (pid 1574846) — untouched throughout

---

## 0. Executive Summary

**Recommendation: Option D — the service-lane pattern becomes the durable default for every promote.** The executor runs as its own systemd service (transient unit, `Restart=no`), surviving daemon death **by construction** — it has neither the daemon's process-group lineage nor its cgroup. Tool lane and operator lane converge on it; in-daemon gating authority (nonce, 3-factor LIVE arm, F2 fencing, staleness, cycle ledger) is unchanged. The watchdog (C) is **not bundled** — it is an evidence-gated follow-up. A and B are rejected (A adds a brittle kill-exemption with zero simplification; B's only robust form *is* D). The two cheap correctness wins — reconcile labeling fix + the already-ratified allow-stale flip — land first (Stage 1). The mutable-checkout coupling (scripts + predicate resolving from the live repo) is a **separate, parallel track** (FL-23 scripts bundling) that composes with D but must not be conflated with it.

---

## 1. The Problem — 2026-10-07 Incident Chain (evidence-pinned)

| # | Lane | Time (UTC) | Outcome |
|---|------|-----------|---------|
| 1 | Tool-lane arms ×2 | 06:42, 07:04 | exit-78 **refused pre-mutation** by the new slice-⑥ plugin-staleness predicate (pre-remediation state). Runs `r-20261007-064216-611d`, `r-20261007-070405-3dc4`. Journal holds `nonce_consumed` + `refusal` + `executor_exit`. The reconcile sweep **mislabeled both** "executor died pre-open" (§2.6). |
| 2 | Operator ceremony (`systemd-run --user --scope`, runbook manual lane) | 08:44–08:55 | exit-78 at **CURRENT-release integrity**: v0.17.2 baseline hot-patched by agent self-modification (`agents/developer[v2]/workflow.md`; manifest `8134ce81` vs disk `c6ec405d`; recurring incident family). Baseline repaired byte-exact ~09:00. Transcript `upgrade-transcript-r-20261007-084402-e067.log`; manual-scope refusals are NOT journaled — transcript is the only record. |
| 3 | Operator ceremony re-run (via agent bash tool) | 09:27→09:53 | **All gates green** (`PLUGIN-STALENESS=fresh` at HEAD `2753ee78d`; integrity CURRENT+TARGET verified), txn opened (`pid=1570117`, outer window 600s), stop step issued → daemon graceful shutdown 09:53:50→09:53:57Z **killed the executor mid-ceremony, pre-flip**. ~28 min outage; cold restart 10:21:49Z on OLD v0.17.2 by external intervention; leader terminate 10:23:40 hit `Killpg ProcessLookupError ×4` (process groups already dead). Transcript `upgrade-transcript-r-20261007-092750-a6a0.log` truncates exactly at the unit-owned-stop banner → **launched-then-killed confirmed** (settles the fused-judge "stopped before launch" contradiction). |
| 4 | **Service lane** (systemd service → promote.sh) | 10:28:43 → in flight | pid 1574846 (`ensemble.log:55219` `service_started name=v0180-promote-survivor`). Survivor log `data/services/v0180-promote-survivor.log`: at 10:31Z — `resolved env: target=live dir=/home/nea/agents-ensemble port=9797 db=ensemble_prod` + `preflight: lock · integrity · journal txn · entry checks`; council later observed it proceed through `staleness=fresh` → integrity. **Live proof of the D pattern.** |

**Root cause (pinned):** the daemon's bash-tool cleanup kills by **process group**, not cgroup. `systemd-run --scope` escapes the *cgroup* but keeps the child in the *caller's session/pgroup* — so a ceremony run through an agent bash tool was registrable and killable even inside a user scope. Promote's own `systemctl stop ensemble-main.service` step fired the daemon's graceful shutdown, whose teardown swept the ceremony's pgroup. Self-referential kill by construction of lane 3.

---

## 2. Grounded Facts (forensics, file:line — repo @ `2753ee78d`)

### 2.1 The kill path
- `BashProcessRegistry` (`daemon/tools/bash.py:28-95`) is the only bash-tool teardown registry: process-wide singleton, `_entries[instance_id] → [(pid, pgid)]`, eager PGID capture at `bash.py:316-321`.
- Spawn: `subprocess_kwargs["start_new_session"] = True` (`bash.py:256-305`) — each tool bash is its own session/pgroup leader.
- Timeout/cancel: `os.killpg(pgid, SIGTERM)` → 5 s wait → `os.killpg(pgid, SIGKILL)` (`bash.py:138-186`).
- **Shutdown sweep:** `InstanceManager.shutdown` Phase 2 (`daemon/manager.py:12548-12563`) → `cleanup_all()` → per-instance `_kill_group` = `os.killpg(pgid, SIGKILL)` (`bash.py:71-108`).
- **There is NO exemption/skip mechanism today.** Documented limitation (`bash.py:74-80`): only setsid-orphans escape.

### 2.2 Why the user scope did not protect the 09:28 ceremony
The operator lane wrapped promote.sh in `systemd-run --user --scope` **inside an agent bash-tool subprocess**. `--scope` shares the caller's session; the wrapped child stayed in the bash tool's tracked pgid. `killpg` is group-wide and cgroup-blind → the ceremony died despite the cgroup escape. (Tool lane's `spawn_executor` deliberately uses `start_new_session=False` for the systemd-run client *precisely so systemd-run becomes the session leader* — `upgrade_journal.py:1893-1909`.)

### 2.3 The tool lane was ALREADY immune
`spawn_executor` (`daemon/tools/upgrade_journal.py:1823-1924`) is **deliberately NOT registered in `BashProcessRegistry` or any teardown registry** (comment at `:1850-1851`: "the child must survive BOTH tool-harness teardown AND daemon death"). Three branches: systemd scope (L1, `:1893-1909`), legacy `start_new_session=True` (L2, `:1911-1924`, known-bad under `KillMode=control-group`), non-Linux identical L2 ("designed on launchd semantics", `:1842-1844`). Env allowlist at `:1621-1641`.

**Consequence:** the unprotected lane was specifically *an operator ceremony run through an agent bash tool*. The defect is lane hygiene, not a universal coupling — this reshapes the option analysis (§5.4).

### 2.4 The service lane (current survivor) — mechanics
Spawn primitive `daemon/tools/service_spawner.py:108-127`: `Popen(start_new_session=True, close_fds=True, stdin=DEVNULL, stdout=log_fh)` — own session/pgid, **not** in `BashProcessRegistry`; tracked instead as a `service_tracking` DB row (`daemon/repositories/service_tool/models.py`) reconciled at next daemon boot (`ServiceReconciliationService`, via `daemon/services/service_tool_manager.py`). The manager's bash cleanup is try/except and reaches only the bash registry — service rows untouched.

### 2.5 Machinery coupling map (what assumes a daemon-coupled executor)
- **Deferred-executor drain:** `manager._pending_system_executions` (`manager.py:1188-1192`), `drain_pending_system_execution` (`:4432-4595`, the only `spawn_executor` call site at `:4540-4542`), messaging drain (`instance_messaging.py:1572-1595`, call site `:4797`). All three assume a `Popen` child of the daemon.
- **Lock roles:** restart = tool acquires → `restart.sh` adopts by `run_id`; promote = tool acquires → drain releases as handoff → `promote.sh` re-acquires. `rollback.lock.d` mkdir-lock with owner/heartbeat/run_id.
- **`stop_via_stop_script`** (`lib.sh:2452-2511`) invokes `stop-ensemble.sh` with `PIPELINE_LOCK_HELD_BY_CALLER=1` (skips settle-check + lock-acquire for in-pipeline callers). The 09:28 transcript shows the stop script resolved from the **mutable checkout** (`/home/nea/ensemble-src/scripts/upgrade/../stop-ensemble.sh`).
- **pending_op durable serializer** (`releases/state.json`; `write_pending_op` `upgrade_journal.py:753`; owner_pid stamped at `manager.py:4579-4583`; unconditional reaper enqueue `:4563-4575`).
- **Halt-for-human / auto-rollback:** `_refuse`/`_freshness_refuse` (`lib.sh:3798+`, `:1878+`) write `halt` + exit 78, txn left OPEN; `rollback_safe` manifest gate (`:4005+`); four recovery paths gate identically (promote auto-rollback, manual rollback, `adopt_stale_txn`, launcher sweep).
- **Journal dual-writer:** Python twin `upgrade_journal.py` (~2555 lines) ↔ `lib.sh` (~4070 lines); `pending_op` is Python-authority-only (`lib.sh:1314-1333`); `in_flight.last_heartbeat` is dual-writer.

### 2.6 Reconcile misclassification (the cosmetic defect) — pinned
`reconcile_pending_op` (`upgrade_journal.py:1533-1604`): terminal-event set = {`commit`, `rollback`, `quarantine`, `halt`} only. `refusal` and `executor_exit` are **not terminal** (`_terminal_event_after`, ~`:1485-1530`) → a clean pre-mutation exit-78 refusal waits past `expires_at + grace` and is then closed as "executor died pre-open?" (`:1593-1594`). Fix = add `refusal`/`executor_exit` to the accepted set (both Python twin and the shell mirror; the shell sweep operates on a different axis — `in_flight` txn + `flipped`, restart-kind never swept, `launcher.sh:746+`).

### 2.7 Staleness predicate & mutable-checkout coupling — settled
- Gate: `promote_plugin_staleness_check` (`lib.sh:1893-2034`), called by `promote.sh:193` **before** integrity; fail-closed unless `--allow-stale-plugins` / `PROMOTE_STALENESS_OVERRIDE=1` (`lib.sh:1991-1996`); N=14 escalation for `snapshot_with_drift_alarm`; predicate module `daemon/plugin_subsystem/promote_staleness.py` invoked by file path with `REPO_ROOT` resolved from **lib.sh's own location** (`lib.sh:1940-1945`) → the mutable checkout.
- **Discrepancy settled by git:** HEAD of `latest` = `2753ee78d` ("opendesign staleness remediation to v0.24.1"), on top of `f77672260 ! New version 0.18.0`, **unreverted**; both post-08:44 transcripts show `PLUGIN-STALENESS=fresh`. The 10:30Z "trail unproven, pin 16d>14 — retry will refuse" note was **stale journal-payload quoting**, not current state.
- The same mutable-checkout resolution carried the 09:28 stop script. `ENSEMBLE_UPGRADE_SCRIPTS_DIR` (tool-lane resolution, `upgrade_tools.py:908` chain: env → repo-of-module → `install_dir/scripts` MISSING) is the entry vector for BOTH — closed by FL-23 release-local scripts bundling (§6 Stage 5).

---

## 3. Options Assessed

| Option | One-line |
|---|---|
| **A** | Exempt the executor from shutdown cleanup (killpg skip-list at the 3 registry sites) |
| **B** | Fully detach the executor from daemon process tracking |
| **C** | systemd watchdog for stopped-not-failed (external, notify-only safety net) |
| **D** | Service-lane as durable default — every promote runs as its own systemd service; tool + operator lanes converge |
| Combos | D+C, B+C explicitly assessed |

---

## 4. Five-Axis Trade-Off Matrix (council consensus; Risk & Cost inverted — higher = better)

Weights: Complexity 20% · Scalability 20% · Maintainability 25% · Risk 20% · Cost 15%

| Option | Complexity | Scalability | Maintainability | Risk⁻¹ | Cost⁻¹ | **Weighted** |
|---|---|---|---|---|---|---|
| A — killpg skip-list | 2 | 3 | 2 | 2 | 3 | **2.3** |
| B — full detach | 3 | 3 | 3 | 2 | 4 | **2.5** |
| C — watchdog (alone) | 3.5 | 3.5 | 3.5 | 3.5 | 3.5 | **3.5** |
| **D — service-lane default** | 3 | 5 | 5 | 4.5 | 3.5 | **4.3** |
| D + C (bundled) | 2.5 | 5 | 4 | 5 | 2.5 | **3.9** |

Per-axis rationale (consensus):
- **D Complexity 3** — one new spawn branch replacing the scope branch; not trivial (env threading, unit hygiene) but bounded and replaces rather than adds.
- **D Scalability 5** — unattended + multi-host promotes with no daemon-coupling ceiling; the only option that improves as automation grows.
- **D Maintainability 5** — structural invariant replaces convention ("never run promote via agent bash tool"); Tier-1/2 deletions (§7).
- **D Risk⁻¹ 4.5** — fixes the kill class by construction; residual risk concentrated in the env-forwarding seam (§9 🔴).
- **D Cost⁻¹ 3.5** — real but one-time engineering + pack work; offsets via deletions.

---

## 5. Recommendation — **D**, with implementation rulings

### 5.1 Why D
1. **Root cause killed by construction.** A transient service unit has neither the daemon's pgid lineage nor its cgroup: unreachable by `BashProcessRegistry.cleanup_all` AND by `KillMode=mixed` straggler sweeps. Live-proven by lane 4 right now.
2. **Misuse becomes safe.** An operator launching the unit through an agent bash tool registers only the short-lived `systemd-run` client, which exits immediately — nothing long-lived is tracked.
3. **Gating authority stays in-daemon.** Nonce/3-factor arm, F2 fencing, staleness, cycle ledger all verified at arm time; the payload still hits `require_live_guard` fail-closed (`lib.sh:327-341`). **The unit carries survivability, not authority.**
4. **Orphaned-promote is handled, not invented.** Executor alive + daemon dead → promote.sh completes independently (txn → stop → flip → gates → commit, shell-side journal), hand-back restarts the daemon (`promote.sh:296-308`), the new daemon adopts nothing — lazy reconcile closes pending_op on terminal journal events. Executor death mid-ceremony = today's executor-crash class → existing four-path recovery (launcher sweep / `adopt_stale_txn` / halt-for-human / auto-rollback).
5. **Portability is a strict improvement.** Linux+systemd gains the unit; Linux-no-systemd and macOS/BSD keep byte-identical legacy `start_new_session=True` behavior (L2 branch, launchd-designed). No host regresses.

### 5.2 C is NOT bundled — evidence-gated follow-up only
Councilor disagreement, resolved: the ratified P4 deferral ("NO `WatchdogSec` — would be an unconditional restart loop") targets sd_notify/WatchdogSec *restart* semantics; a notify-only external watchdog doesn't strictly hit that objection — but honoring the ratified deferral, **C ships only on an evidence trigger**: (a) a residual-class event occurs (host reboot mid-ceremony, OOM-killed unit, polkit regression), or (b) post-v0.18.0 recovery latency proves unacceptable, or (c) promotes become frequent + fully unattended after the allow-stale flip. Until then: **D alone**.

### 5.3 Implementation rulings (pin these in the test pack)
- **`Restart=no` on the executor unit.** A promote unit that re-runs `promote.sh` after a non-78 crash would re-enter a partially-completed ceremony against live lock/txn state — exactly what halt-for-human exists to prevent. Recovery belongs to the four-path invariants. (`RestartPreventExitStatus=78` alone is insufficient — it fences only the refusal exit class.)
- Unique unit name per `run_id`; `--setenv` threading of the full `extra_env` set (incl. `ENSEMBLE_UPGRADE_LIVE` post-gate); `reset-failed` hygiene; reaper observes unit exit **or stays journal-truth** (journal is authoritative, not pid-liveness).
- Env-forwarding verification via the existing `"resolved env:"` echo pattern + a pack test pinning the forwarded set.

### 5.4 Rejections
- **A (2.3)** — pure addition at 3 registry sites (`bash.py:321`, `bash.py:57-72`, `instance_lifecycle.py:2749-2752`), brittle argv sniffing, a standing hole in shutdown hygiene, **zero simplification**. Dominated: under D it is never built.
- **B (2.5)** — `setsid` escapes the pgid but **not the cgroup**: on a unit-managed host `KillMode=mixed` SIGKILLs cgroup stragglers after the stop timeout — *later* in the ceremony, worse. It also fixes the wrong lane (09:28 was the operator lane; the tool lane was already unregistered). **B's only robust form is D.**

**Flip conditions (if any hold, re-open):** polkit transient-unit minting unavailable on hosts that matter → fall back to scope-lane + C; `--setenv` forwarding produces recurring exit-78 classes → reconsider scope+C; user reverses systemd adoption → re-open entirely.

---

## 6. Migration Path (staged; repo frozen until v0.18.0 lands)

| Stage | Change | Size / risk |
|---|---|---|
| **0 — now, zero repo changes** | Let the in-flight service-lane promote land. Capture journal + survivor log **+ `systemctl show` of the unit** as reference evidence. | None |
| **1 — first post-v0.18.0 set (tiny)** | (a) Reconcile labeling fix: `refusal`/`executor_exit(78)` → "refused," not "executor died pre-open" (`upgrade_journal.py` terminal set ~`:1452-1530`, closer `:1581-1603`; mirror in shell twin). (b) The already-ratified allow-stale default flip (`promote.sh:83-91`, `lib.sh:1991-1996`; visible-not-blocking). | Small, independent of D |
| **2** | `spawn_executor` service branch: replace scope branch (`upgrade_journal.py:1890-1914`) with transient unit per §5.3 rulings. **Precondition:** verify polkit transient-unit minting on the remote VMs. | Medium — the core change |
| **3** | Runbook §8.2 convergence: one canonical operator shape (unit lane); record A as rejected; retire "never run promote via agent bash tool" convention into the structural invariant. | Small |
| **4 — conditional** | C watchdog, only on the §5.2 evidence trigger. Notify-only, journal-correlated (`in_flight` + no terminal event + unit stopped ⇒ alarm). | Gated |
| **5 — parallel track, do NOT conflate** | FL-23 release-local scripts bundling — kills the `ENSEMBLE_UPGRADE_SCRIPTS_DIR` mutable-checkout coupling (`upgrade_tools.py:1526-1546`); it was BOTH the staleness entry vector AND the 09:28 stop-script path. Composes with D; sequence independently. | Separate commission |

---

## 7. What Gets Simpler (deletion inventory, tiered)

**Tier 1 — high confidence, both councilors + forensics agree:**
- A's skip-list never built (3 sites avoided).
- Scope branch → service branch *replacement* (not addition) in `spawn_executor`.
- Runbook §8.2 special-casing + the "never via agent bash tool" convention collapse into one structural invariant (misuse-safe by construction).

**Tier 2 — candidate deletions; VERIFY before deleting (councilor-flagged unverified cites):**
- Scope-detection seam (`upgrade_journal.py:1697-1704`, ~90 loc) + `build_scope_argv` (~24 loc).
- r-f82e bus-env allowlist keys (`:1623-1625`) — subsumed by unit env threading.
- L2 double-fork rung (`:1911-1924`) — **only on systemd-guaranteed hosts**; keep for macOS/BSD portability.
- `SCOPE_SURVIVOR` state + `scope_heal` rung (`:1941-1942`; `lib.sh:2849-2861`; Python twin `:2018-2039`; 4 test files) — cross-language twins must move together (P5 constraint).
- LIVE-arm deferred-drain branch + LIVE-arm reaper enqueue (`manager.py:4432-4547` verified-arm branch, `:4560-4575`) — **unverified-arm + restart branches stay**.
- LIVE-arm repo-fallback in scripts-dir resolution.

**Tier 3 — explicitly KEPT (do not delete):** pending_op serializer + lazy reconcile; adopt-by-run_id lock handoff (`restart.sh:105-151`); halt-for-human + auto-rollback invariants; cycle-ledger crediting (`ledger_check.py:19-22`); DUAL_FIGHT check; macOS/no-systemd legacy path; the full in-daemon gating stack.

---

## 8. Risks & Caveats

- 🔴 **Env-forwarding seam (Stage 2):** a dropped `--setenv` degrades to fail-closed-but-confusing exit-78s. Mitigate: `"resolved env:"` echo verification + pack test pinning the forwarded set.
- 🔴 **`Restart=no` is load-bearing** (§5.3) — a copied `Restart=on-failure` from the daemon unit would create mid-ceremony re-entry. Pin in test pack.
- 🟡 **Polkit transient-unit minting on remote VMs unverified** — check BEFORE Stage 2 lands there (also a flip condition).
- 🟡 **09:28 journal nuance:** the transcript shows a `txn open` banner while the KB records "no journal txn/commit trace" — post-promote read-only journal pull should settle whether `in_flight` was written and subsequently reclaimed by the boot sweep. (Does not affect the recommendation; affects recovery-invariant accounting.)
- 🟡 Tier-2 deletions carry unverified citations (sweep reaper sites `upgrade_journal_sweep.py:437, 450-456`; `test_promote_cgroup_survivorship_python.py` pack existence; lib.sh twin surface) — verify before deleting.
- 🟢 Reconcile labeling fix (Stage 1a) also improves the sweep's post-incident forensics (no more "died pre-open" mislabels of clean refusals).

---

## 9. Decisions Pending (user)

1. **Adopt D?** (converge both lanes on the systemd service-lane as durable default — the pattern currently landing v0.18.0).
2. **Accept C's evidence-gated posture** (not bundled; trigger list in §5.2)?
3. **Sequence Stage 1** immediately post-v0.18.0 (reconcile fix + allow-stale flip — both tiny, both already policy-aligned)?
4. **Schedule FL-23 (Stage 5)** as its own commission — the mutable-checkout decoupling.
5. **Ratify `Restart=no`** on the executor unit (§5.3).

## 10. Open Questions / Verification Items

- Remote-VM polkit transient-unit minting (pre-Stage-2 gate).
- Post-promote journal pull for the 09:28 ceremony's `in_flight` fate (§8 🟡).
- Tier-2 deletion verifications (per-item file:line confirmation).
- `systemctl show` capture of the survivor unit (Stage 0 evidence).

## 11. Gaps & Confidence

- **No dedicated remote-DevOps investigation doc file exists** (searched docs/, docs/runbooks/, docs/incidents/, .agents/shared/, .agents/devops/, live data/ + services/ vicinity). The investigation record = KB entries + both transcripts + runbook §8.2/§9 + systemd-adoption.md; the forensics leg digested these as the record. **Confidence: HIGH** on the recommendation, **MEDIUM** on Tier-2 line-level deletion claims (verify-first discipline applied).
- One council-vs-dispatch nuance resolved: zero *journal mutation* events (no commit/flip) from 09:28 is the operative fact; the `txn open` banner question is tracked as a verification item, not a contradiction.
- Both assessment legs honored strict read-only; the in-flight promote, pipeline lock, rollback.lock.d, and live state were untouched.

---

## Appendix A — Evidence Index

- Transcripts: `/home/nea/upgrade-transcript-r-20261007-084402-e067.log` (08:44 integrity refusal), `/home/nea/upgrade-transcript-r-20261007-092750-a6a0.log` (09:28 kill; truncation at unit-owned-stop banner).
- Survivor log: `/home/nea/agents-ensemble/data/services/v0180-promote-survivor.log`; service start `daemon/logs/ensemble.log:55219`.
- Git: `latest` @ `2753ee78d` (staleness remediation, unreverted) atop `f77672260` (v0.18.0).
- Kill path: `daemon/tools/bash.py:28-108, 138-186, 256-321`; `daemon/manager.py:12548-12563`.
- Spawn seams: `daemon/tools/upgrade_journal.py:1621-1641, 1704-1820, 1823-1924` (scope branch `:1890-1914`; deliberate non-registration `:1850-1851`); `daemon/manager.py:4432-4595` (drain; `spawn_executor` call `:4540-4542`; reaper `:4563-4575`); `daemon/services/instance_messaging.py:1572-1595, 4797`.
- Service lane: `daemon/tools/service_spawner.py:108-127`; `daemon/services/service_tool_manager.py`; `daemon/repositories/service_tool/models.py`.
- Reconcile defect: `daemon/tools/upgrade_journal.py:1485-1604` (terminal set; closer text `:1593-1594`).
- Staleness gate: `scripts/upgrade/lib.sh:1893-2034`; `promote.sh:193`; flip sites `promote.sh:83-91`, `lib.sh:1991-1996`; predicate `daemon/plugin_subsystem/promote_staleness.py`.
- Stop/hand-back: `lib.sh:2452-2511`; `stop-ensemble.sh:62-92`; `restart.sh:105-151`; launcher sweep `launcher.sh:746+`.
- Units: `scripts/systemd/ensemble-daemon.service:81-89` (`KillMode=mixed`, `Restart=on-failure`, `TimeoutStopSec=90`, NO `WatchdogSec` — P4-deferred).
- Assessment instances: forensics `c0c88804-3918-4b0c-bc07-9a066703fe68`; council governor `8614af1b-1b06-404d-8f2c-08f42693755e` (councilors `e5399444` agentic, `1384ed17` coding).
