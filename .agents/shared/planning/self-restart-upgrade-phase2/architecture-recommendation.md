# Architecture Recommendation — Self-Restart / Self-Upgrade Phase 2 (Seam Resolution)

- **Date:** 2026-08-22 · **Author:** architect (controller) — synthesis of a 4-councilor governor council (2 models × `trade-off-analysis`; 2/2 completed, ~80% unanimous, 4 forks adjudicated — dissent preserved per fork)
- **Input:** plan corpus `plan-overview.md` + 8 siblings @ branch `plan/self-restart-upgrade-phase2`; parent ADR-001…015 (`.agents/shared/planning/auto-restart-upgrade/decisions.md`); sibling ADR-016…027 (`decisions.md`, this dir)
- **Status:** DECIDED — every focus area ends in one implementable recommendation. Where I override a planner decision, it is marked **⟲ OVERRIDE** with rationale for the reviewer to arbitrate.
- **Scope:** planning only. No code was written or run. Read-only repo inspection.

> **⛔ HARD CONSTRAINT (user directive — VERBATIM, governs every decision below):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**Constraint compliance:** every LIVE execution path designed here is **refusal-tested only** — never exercised by this initiative. The tool layer structurally cannot cross environments (env self-match, D-FA2.4); the script layer asserts install-dir + port + DB triples and carries **no literal `9797`**; `system_restart` on live is refused outright (A2 ratified); live promotion/restart/migration are USER-GATED runbook steps only.

---

## 0. Executive Summary

| FA | Decision (one line) |
|----|---------------------|
| FA1 | Arm→return→journal-poll **ratified**; pending-op record field set frozen; **daemonized executor for BOTH restart and promote** (exit-74 deferred as future ADR); post-turn trigger = deferred-pause marker + post-graph callback; daemon-down notifier = watchdog-watcher journal-watch extension |
| FA2 | **4 tools** (`upgrade_status` stays separate — ⟲ OVERRIDE of W1 D1); `"Error:"` prefix; `dry_run` default true; `ENSEMBLE_SELF_ENV` staged marker, fail-closed; 5-step check order with env-self-match **before** live gates; R-SR16 → `PRIVILEGED_TOOL_CATEGORIES` frozenset |
| FA3 | 3-factor gate ratified **with a verified correction** (HUMAN stamp is an else-branch default — hardened by `USER_ORIGIN_SOURCES` whitelist); marker = instance-scoped window stamped only on whitelisted sources; nonce store = journal extension (`releases/state.json`); `nonce-verification-unavailable` fail-closed refusal added |
| FA4 | Pipeline validated; **launcher.sh joins the staged payload**; cap enforcement = **promote-preflight refuse at ≥3, rollback always executes** (recovery-first); directory integrity = per-file sha256 manifest; `rollback_safe` = the decision field (halt + NO flip when unsafe), `known_schema_gen` = informational |
| FA5 | mkdir-based lock dir (no flock — macOS); busy-check advisory everywhere; flat-fallback = **WARN + divergence journal event + boot proceeds + all mutations frozen** (schema-conditional halt deferred to Phase-5 `daemon_meta` hook); dual-sweep convergence ratified |
| FA6 | Landed with FA2 (same mechanism, unanimous) |

**New ADRs to mint in `decisions.md`** (W3's doc — edits listed in §6, not applied by me; **numbering shifted 2026-08-22 post-review: proposed 028…031 → 029…032 to clear the collision with the already-minted ADR-028 (rollback-of-rollback)**): **ADR-029** daemonized executor for both / exit-74 deferred · **ADR-030** launcher.sh in staged payload · **ADR-031** `PRIVILEGED_TOOL_CATEGORIES` · **ADR-032** `USER_ORIGIN_SOURCES` whitelist.

---

## FA1 — Self-Restart-While-Agents-Running Semantics

### D-FA1.1 — Pending-op record (the journaled contract) ✅ DECIDED

`releases/state.json` (single durable state file — ADR-004/D4 discipline; a second `pending_actions.json` was unanimously rejected: two files = two atomicity surfaces) gains:

```jsonc
// releases/state.json — pending_op (extends phase1-plan D4 sketch; supersedes the looser in_flight sketches)
"pending_op": {              // null when idle; ONE op at a time (lock-enforced, D-FA5.1)
  "run_id": "r-<utcstamp>-<4hex>",   // cross-death join key — returned by the tool, consumed by upgrade_status
  "kind": "restart | promote | rollback | sweep_rollback",
  "env": "demo | dev | sandbox | live",
  "target": "1.2.3 | null",          // null for restart
  "mode": "graceful-now | after-turn",  // restart only
  "reason": "<free-text, journaled audit>",
  "armed_at": "<iso>", "armed_by_instance": "<ari-iid>",
  "owner_pid": 12345, "owner_kind": "executor|script",
  "owner_heartbeat_at": "<iso>",      // refreshed ~30s by the live owner (R-SR13 discrimination input)
  "trigger": "post-turn-callback | boot-sweep",
  "nonce": "CONFIRM-… | null", "nonce_consumed": true|false,
  "confirmed_by_human": true|false, "confirmed_source": "api|telegram:|…",
  "flipped": false,                    // promote only; true once `current` moved
  "expires_at": "<iso>"                // restart +30min; promote +10min outer window
}
```

Written **before the tool returns** (atomic temp+mv). Survives `MessageQueue` wipe (`manager.py:596`) and daemon death — the tool's "SCHEDULED" promise is backed by disk, not memory.

### D-FA1.2 — Polling, reporting, and the daemon-never-returns path ✅ DECIDED (unanimous)

- **Who polls:** Ari, on its NEXT turn, via `upgrade_status(run_id)` / `release_info`. **Pull model only** — no reliance on `ReportDeliveryRecoveryService` (verified periodic-only: 300s interval, 10-min age bound, NO boot sweep — `report_delivery_recovery.py:136,275`).
- **What Ari reports post-restart:** journal terminal entry (`committed` / `rolled_back` + reason + quarantine / `refused` / `halted-for-human`) + `/livez` version verify + `/readyz` composite + `rollbacks_24h`/cooldown state. Prompt-level instruction (P2.2 T3): during daemon-down polling errors, Ari relays "daemon restarting" — never "tool broken".
- **When the daemon does NOT come back:** the launcher owns recovery (exit-75 tempfail loop → burst-abort latch at 5 crashes/600s; exit-78 refuse-no-loop). The **only component alive when the daemon is down is the watchdog-watcher** → the daemon-down notifier is the **ADR-025(b) watchdog-watcher extension**: watch set gains journal `halt` / burst markers (`.launcher-state`, `releases/state.json`) so stay-down, cap-halt, and sweep-rollback are notified without the daemon. Anything pointed at live = USER-GATED (ladder U6). P2.3 wires it; the architecture depends on it, so P2.3 T8 is now a **hard dependency for the failure-path story**, not optional polish.

### D-FA1.3 — Executor seam (Fork #1) ✅ DECIDED: **daemonized executor for BOTH; exit-74 deferred**

- **Decision:** one mechanism — the daemonized executor (`subprocess` + `start_new_session=True` ≡ double-fork + `setsid`, env-allowlisted, pid-filed, journal-logged) serves **restart and promote**. `scripts/upgrade/restart.sh` and `promote.sh` are the two executor payloads; both are daemonized identically (phase2-plan D4 spec). Exit-code 74 is **deferred to a future ADR** with its design preserved (ADR-010 amendment + launcher capability probe + `launcher-not-74-aware` refusal — exactly how it ships later).
- **Why (over the hybrid 74+scripted):** (1) **R-SR06 ship-ordering** — 74 must ship launcher-first or an old launcher crash-tracks it; (2) **bootstrapping window** — until a 74-aware launcher is deployed everywhere, `system_restart` would be dead-on-arrival on fresh installs (the capability-refusal makes it *worse*, two paths); (3) the council's converged trigger design (D-FA1.4) fires the executor at **exact turn-end**, so the bounded-waiter race that exit-74 was meant to kill **does not exist**; (4) R-SR09 env-leak is real but bounded (allowlist: `PORT`, `INSTALL_DIR`, `PATH`, `PG_*` minimum; no `.env` passthrough; fds closed; stdio → `data/upgrade.log`; bounded lifetime).
- **Dissent preserved (councilor `coding`):** launcher-as-executor has a smaller orphan window and scores higher if availability of the trigger mechanism itself is weighted highest; its launcher-in-payload amendment is **carried forward regardless** (D-FA4.1). Reviewer may flip this fork to the hybrid — it is fully specified in the council record.
- **Restart path concretely:** post-graph callback fires `restart.sh` daemonized → wait-for-idle (short, ~30s courtesy) → SINGLE-TERM stop (`stop-ensemble.sh` contract — **never** a raw kill) → detached `exec launcher.sh` → health gate → journal `restart` event. The launcher does NOT respawn on clean exit (verified `launcher.sh:~470-490`) — hence the detached re-exec by the executor is *required*, not optional, in this design.

### D-FA1.4 — Post-turn trigger mechanics ✅ DECIDED (unanimous): deferred-pause marker + post-graph callback

Additive consumer of the existing post-graph path (`graph.py:3359-3520` question-tool precedent): graph node sets the marker; post-graph callback runs the executor-spawn OUTSIDE the task, `asyncio.shield`'d; marker pop OUTSIDE the identity guard (C2 torn-state pattern). **NOT inside the graph** — a turn-finalization node would trip the full-e2e release-gate re-assessment (phase2-plan Coupling flags this). The `restart.sh` bounded waiter survives **only as the fallback trigger** (daemon died between tool-return and callback → boot sweep converges; D-FA5.4). **Caveat carried:** the e2e-gate avoidance holds only if implementation stays additive — **PR-time confirmation required** (test-strategy §2 already mandates the check).

### D-FA1.5 — Restart-vs-in-flight semantics (R-SR01) ✅ CONFIRMED + tightened

Ratified as the definitive model: in-flight jobs freeze at the last committed node boundary and resume `is_retry=True`; child reports deliver ≤~10min late (recovery cadence); `MessageQueue` queued-unprocessed rows wipe; Ari's own turn is never the casualty (tool returns before any stop signal — fixed contract). **Tightening added to the register:** the pre-existing Task↔JobItem reconciliation gap (`instance_lifecycle.py`, `task/repository.py:2126-2241`) is **amplified** by restarts (more tasks in intermediate states at once) and is NOT fixed here — carried as a named interaction, flagged to the reconciliation initiative.

---

## FA2 — Agent-Tool API Surface

### D-FA2.1 — A1: tool inventory ✅ DECIDED (unanimous): **4 tools** — ⟲ OVERRIDE of phase2-plan D1

`system_restart`, `system_upgrade`, `release_info`, **`upgrade_status`** — all category `system_upgrade`. The run-id correlation across process death is the load-bearing feature; folding it into `release_info` (W1 D1) hides the cross-death join key behind a parameter, and "fewer tools" does not shrink the confirmation-gated surface (both candidates are read-only). Sides with W2 §1 + ADR-023 against W1 D1. **Fallback clause:** if the reviewer overrides back to the fold, `release_info(section=journal, run_id=…)` MUST carry the run-id filter + journal tail — non-negotiable.

### D-FA2.2 — Conventions ✅ DECIDED (unanimous)

- **Error prefix: `"Error:"`** (forward normalization; `question_tools.py` `ERROR:` is the outlier — no bulk rewrite). Refusal taxonomy ratified with additions: `env-marker-absent`, `layout-divergence`, `nonce-verification-unavailable`, `restart-under-burst-abort`, `unknown-mode` (+ `launcher-not-74-aware` reserved for the future 74 path).
- **Payloads:** line-oriented structured strings (LLM-friendly, `release_info`/`system_health` precedent).
- **`dry_run` default TRUE** (ADR-022 rec ratified) — a hallucinated parameter set must never execute a real promote on the first call.
- **Budget correction:** gate = `/livez` ≤60s + `/readyz` ≤**120s** (deploy.sh phase-5 budget, canonical) + 300s soak + version verify. Corpus §2.1's 180s corrected in-place.

### D-FA2.3 — `ENSEMBLE_SELF_ENV` marker ✅ DECIDED (unanimous)

Staged by `deploy.sh` **and** `scripts/upgrade/stage.sh` into `INSTALL_DIR/.env` (ADR-014 mechanism; launcher exports it — `launcher.sh:95-149`). Values `dev|demo|live|sandbox`. **Fail-closed when absent:** every ACTOR tool refuses (`env-marker-absent`); read tools (`release_info`, `upgrade_status`) still answer. Port-derived fallback **rejected** — it reintroduces the R-SR11 typo class. The live marker is staged only during the USER-GATED live migration; until then live actor calls are doubly unreachable.

### D-FA2.4 — Check order (env self-match interlock) ✅ DECIDED (merged, unanimous on the load-bearing property)

1. Schema/enum validation (reject `unknown-mode`, bad `target_env` literal)
2. Resolve self-env from the staged marker — absent → refuse (fail-closed)
3. **`target_env == self_env` — BEFORE any live-gate logic** (cross-env attempts can never reach the live gate; kills the 7979↔9797 typo class for tool actions, R-SR11)
4. Per-env gate (live: 3-factor §FA3 — and USER-GATED execution refusal per A2; dev/demo: free)
5. Pipeline preconditions (lock free, cap/cooldown clear, target staged, manifest safe, layout clean)

### D-FA2.5 / FA6 — R-SR16 exclusion mechanism ✅ DECIDED (unanimous; placement reconciled)

- **Mechanism:** `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade"})` **defined in `daemon/tools/_tool_registry.py`** (adjacent to `CATEGORY_MODULES`; covered by the frozen-name regen workflow) and **consumed in the empty-allow branch of allow resolution** (`daemon/tools/instance.py:276-281`): absent/empty allow → universe = all categories **minus privileged**; explicit `"system_upgrade"` in allow → normal expansion (`:284-289`). No universe-flip (breaking change, rejected).
- **⟲ Corpus correction (both councilors verified independently):** the corpus's "worker, explorer are empty-allow" claim is **factually wrong** — worker has 14 explicit entries, explorer 8 + deny. **`watcher` is the only empty-allow agent today**, and watcher receiving zero upgrade tools is the *desired* outcome. Fixed in-place (tool-api-design §3.5/§12, risk-register R-SR16).
- **Regression tests:** (a) absent-allow and `allow: []` → 0 of the 4 tools, all other categories intact; (b) ari + `"system_upgrade"` → exactly the 4; (c) `allow: ["system_upgrade"]` alone → exactly the 4; (d) frozen-name drift test green after `KNOWN_TOOL_NAMES`/`DYNAMIC_TOOL_NAMES` regen.

### D-FA2.6 — Permission model ratification

Per-env matrix (§3.1) ratified as written: reads free everywhere; actor tools free on dev/demo/sandbox; live actor tools behind the 3-factor gate **and** refused-at-execution this initiative (A2) with refusal-tested-only validation; jober deferred (D-4/ADR-019). A3 ratified: `user_confirmed`/`nonce` params present-but-ignored on non-live so the future live opt-in flips a config flag, not a schema.

---

## FA3 — LIVE Confirmation-Gate Enforcement (R-SR07)

### D-FA3.1 — The 3 factors + a verified hardening correction ✅ DECIDED

**Factors (ratified):** (1) `user_confirmed: true` param — necessary, never sufficient; (2) server-side HUMAN-origin turn marker; (3) action-binding nonce contained in the triggering HUMAN message content.

**⟲ Verified correction (both councilors, independently):** the corpus's foundation — "`MessageType.HUMAN` is stamped only at `instance_messaging.py:1319`" — is **imprecise**. HUMAN is the **else-branch default** of the source classification (`:1310-1319`): any source without an `internal_*` prefix mints HUMAN, and `cascade_resume` (watchover_service.py:675), `internal_invoke_and_wait:` (utils.py:601), `agent:{caller}` (job_queue.py:986) rows are HUMAN-typed **today**. The claim "an LLM cannot enqueue a HUMAN row" holds only by **caller discipline** (all agent paths use `internal_agent:` prefixes — verified at instance.py:1375/1537/1718), not by structure.

**Hardening (Fork #2, adjudicated): positive source whitelist at the stamp site.** `USER_ORIGIN_SOURCES = {"api", "telegram:", "discord:", "slack:"}` (exact prefix strings to be verified before freeze — see §7 assumptions) checked at `:1310-1319`: only whitelisted sources stamp the marker. This converts the accidental property into a structural one with **zero API-surface change**; the alternative `is_user_origin` explicit flag threaded through `enqueue_message` is a **compatible future hardening** (marker can require type=HUMAN AND whitelisted-source AND flag). The else-branch mis-typing itself (`cascade_resume`/`internal_invoke_and_wait`/`agent:` rows stamped HUMAN) is recorded as a **separate follow-up defect — do NOT fix in this initiative** (wide blast radius across watchover/job-queue semantics).

### D-FA3.2 — Marker plumbing ✅ DECIDED

Stamp at the classification point (`:1319` vicinity), only when source ∈ whitelist: `manager._user_origin_windows[instance_id] = {turn_id, message_id, source, stamped_at, expires_at (+15min)}`. **Instance-scoped** (child instances never inherit — their sources are `internal_*`, so they cannot stamp); per-turn, never session-wide. The gate reads **only the triggering MessageQueue row by `message_id`** for the nonce substring match — never bulk history (no content leak beyond the one row the gate must see).

### D-FA3.3 — Nonce store (Fork: journal vs dedicated file) ✅ DECIDED (unanimous): journal extension

`releases/state.json` gains `pending_actions` keyed by run_id: `{nonce ("CONFIRM-" + 8 base32, single-use, TTL 15min), kind, env, target, issued_at, ttl_expires_at, issued_to_instance, consumed_at|null, consumed_by_message_id}`. One atomic-write discipline, one crash-consistency surface; consumption appends a journal history event — the audit trail survives the MessageQueue wipe (R-SR10). **Gate-time read:** the MessageQueue row (durable at gate time); **post-arm authority:** the journal. **New failure mode, decided fail-closed:** daemon restarted between issuance and consumption → the HUMAN row is wiped → `nonce-verification-unavailable` refusal, "re-run dry_run" (**R-SR19**).

### D-FA3.4 — Refusal semantics, trust bounds, cross-restart authority ✅ DECIDED (unanimous)

- Refusal table (§4.4) ratified verbatim; Ari relays structured refusals verbatim and **never retries autonomously** (no LLM in the go/rollback decision — inherited hard rule).
- **What the gate DEFENDS:** LLM-fabricated `user_confirmed` (factor 2 fails); prompt-injection nonce echo (an echoed nonce in an AGENT-typed / non-whitelisted row fails factors 2+3). **What it does NOT defend:** host compromise — an attacker with daemon-process access forges anything (single-host trust model; unauthenticated single-user API). Stated, accepted, documented — the honest R-SR07 residual.
- **Cross-restart:** `confirmed_by_human` binds to the journal txn, not the turn — post-death re-execution of the same txn never re-asks (ratified).
- **A2/A3 ratified:** live `system_restart` refused outright this initiative (refusal message points to the manual procedure); params stay schema-stable for the future opt-in.

---

## FA4 — Upgrade Pipeline Architecture (P2.1)

### D-FA4.1 — Layout, atomicity, lock, retention ✅ VALIDATED (unanimous) + amendment

Trio + `manifest.json` + `releases/state.json` + atomic `current` + `rollback.lock` + retention (3 newest, `previous` pinned) — all validated as specced (phase1-plan D1-D6). **Amendment: `launcher.sh` joins the staged payload.** Trio = binary + `agents/` + `frontend/dist/` + **`launcher.sh`** + `manifest.json` + `config.yaml`; manifest gains `launcher_sha256`; launcher swaps in the stopped window (launcher+daemon both exited post SINGLE-TERM). Rationale: the launcher is part of the release surface (R-SR06 — an old launcher running a new binary's contract is drift); carrying it in-payload makes launcher/binary skew self-healing at the next promote. Applied in-place to phase1-plan T2.

### D-FA4.2 — Rollback cap 3/24h enforcement point (Fork #3) ✅ DECIDED: entry-side refuse, rollback always executes

- **Promote preflight refuses at `rollbacks_24h ≥ 3`** (`rollback-cap-exceeded`, halt-for-human until window reset / explicit ack per ladder §2).
- **The rollback itself NEVER refuses on cap** — it is the recovery; refusing it leaves a flipped-broken environment down. A 4th rollback is unreachable via a 4th promote (preflight blocks entry); the only would-exceed path is the launcher sweep acting on an orphaned flipped txn, where refusing would strand the env. Reaching 3 arms halt + cooldown for the *next entry*.
- Dissent preserved (`agentic`: check-before-flip scopes the loop-brake tighter; if the reviewer prefers it, scope it to the sweep path only).

### D-FA4.3 — ADR-012 launcher journal sweep ✅ DECIDED (unanimous, concretized)

Runs at launcher start **before binary resolution** (`launcher.sh:567-568`; stub at `:151-174`). Decision table: `kind == promote` AND `now − started_at > 600s` AND owner dead (`kill -0` fails or heartbeat stale >300s) → `flipped:true` → execute rollback (counts toward cap — ADR-024); `flipped:false` → clear the txn; else leave (owner may be alive). **`kind=restart` is NEVER swept by the launcher** — restarts are self-completing; the daemon boot sweep owns them (expired + dead owner → clear + `expired` journal event). This is the R-SR13 discrimination contract, field-level.

### D-FA4.4 — Integrity checks ✅ DECIDED (unanimous): per-file sha256 manifests

- **Directories are hashed as per-file manifests** — `{relative_path: sha256}` map + a hash over the sorted listing. Deterministic, diffable, pinpoints the tampered file. Tar-stream hash rejected (opaque to diagnosis).
- **What is checksummed:** binary, `launcher.sh`, `config.yaml`, `agents/` (tree manifest), `frontend/dist/` (tree manifest). Written to `manifest.json` at stage.
- **When:** at stage (compute + write); at promote preflight on the CURRENT release (drift detection) AND the target; on demand via `status.sh --verify`. **No-`.env`-inside-release** asserted at stage AND preflight (ADR-014/m6 invariant).
- Version smoke stays `/livez`-version vs `manifest.binary_version` (ADR-027) — self-report mitigated by payload checksums (integrity) + version check (running process).

### D-FA4.5 — Interim `rollback_safe` gate (R-SR05) ✅ DECIDED (unanimous + reconciled semantics)

- **Enforcement:** auto-rollback checks the `previous` manifest **before repointing**; `rollback_safe=false` → **halt-for-human + notify + NO flip** — the daemon stays on the failed-but-booted new release (degraded, alerted) rather than flipping into schema drift (boot destructive drops, `manager.py:478-501`).
- **Field semantics reconciled:** `rollback_safe` (boolean, set by the release author at stage per ADR-007 M5) is **the decision field**; `known_schema_gen` (migration head at stage time) is **informational** — consumed by the future `daemon_meta` gate (Phase 5). Nothing in P2 branches on `known_schema_gen`.
- **Residual (stated honestly):** binary rollback across schema-changing releases remains unsafe until `daemon_meta`; the manifest flag + halt is the only gate. USER-GATED for live regardless.

### D-FA4.6 — Script env discipline ✅ DECIDED (unanimous)

Assert install-dir + port + DB triple before any action; echo the resolved triple in every result; **no literal `9797` anywhere in `scripts/upgrade/`** (test-strategy §5.2); refuse-unresolved = fail-closed; live guard `ENSEMBLE_UPGRADE_LIVE=1` else exit 78 (mirrors `deploy.sh:139-148`).

---

## FA5 — Concurrent-Attempt & Failure Isolation

### D-FA5.1 — Lock (Fork-free, unanimous): mkdir-based lock directory

`$INSTALL_DIR/releases/rollback.lock.d` (per-install-dir = per-env in this topology). **mkdir is the atomic acquire** — portable, no `flock` (**no `flock(1)` CLI on stock macOS; no repo precedent** — both councilors independently rejected it). Contents: `owner` (pid), `run_id`, `heartbeat` (epoch, rewritten ~30s). Stale-break: heartbeat >300s → `mv` the dir to `rollback.lock.stale.<pid>` (avoids racy rmdir) → re-acquire. **The protocol — not shared code — is the contract:** implemented identically in `scripts/upgrade/lib.sh` and the Python journal module (P2.2 T4), and **`deploy.sh` gains the same acquire** so manual deploys serialize with the pipeline (R-SR03's manual-race case). Second invocation → `pipeline-busy run_id=…` (structured, not error). Two upgrades racing, Ari+jober (future), tool-vs-script: all serialize here; drills D8 assert it.

### D-FA5.2 — Busy semantics ✅ DECIDED (unanimous): advisory everywhere

`has_instance_busy` (`task/repository.py:523`) is reported in every actor-tool result (and promote preflight) but **never gates** — restart AND promote proceed. Hard-refuse rejected: an env wedged busy (the Task↔JobItem gap makes that realistic) would deadlock upgrades forever; checkpoint resume is the correctness net; drain remains Phase-4 courtesy (M3).

### D-FA5.3 — Flat-fallback / partial-trio (R-SR04) (Fork #4) ✅ DECIDED: availability-first, mutations frozen

When the journal exists but `current/` fails to resolve and only the flat binary does: **WARN + journal `divergence` event ALWAYS; boot proceeds on flat; ALL pipeline mutations refuse (`layout-divergence`) until reconciled.** Exit-78-on-divergence rejected for pure layout drift — it takes the whole env down when a stale-but-bootable binary + frozen mutations + journaled marker is safer and self-describing. **The schema-safety concern (a stale flat binary booting against a newer-schema DB → destructive drops) is REAL but is deferred to the Phase-5 `daemon_meta` hook** — implementing the conditional halt pre-`daemon_meta` requires schema-expectation metadata that does not exist yet; `known_schema_gen` in the journal is the seam it will consume. Migration: `stage.sh --migrate-flat` (one-time flat→trio conversion + journal init), before the first tool-driven promote; demo/sandbox in-initiative; live = USER-GATED runbook step (ladder).

### D-FA5.4 — Boot convergence (dual sweep) ✅ RATIFIED (unanimous)

Launcher sweep before binary resolution + daemon boot sweep for restart-kind pending-ops. Worst case an armed op executes at the next boot — **latency, never silent loss** (R-SR17; the on-disk pending-op is authoritative, the in-memory marker only the trigger).

---

## Resolved-Seam Table (⟪SEAM⟫ → decision)

| # | Location (doc §) | Seam | Decision |
|---|------------------|------|----------|
| S-01 | tool-api-design §2 | `"Error:"` vs `"ERROR:"` | `"Error:"` — forward normalize, no bulk rewrite (D-FA2.2) |
| S-02 | tool-api-design §3.2 | `ENSEMBLE_SELF_ENV` staging + fallback | Staged by deploy.sh/stage.sh into `INSTALL_DIR/.env` (ADR-014); absent → actor tools refuse fail-closed; port fallback rejected (D-FA2.3) |
| S-03 | tool-api-design §3.5 / §12-3 / R-SR16 | Empty-allow exclusion placement | `PRIVILEGED_TOOL_CATEGORIES` frozenset in `_tool_registry.py`, consumed at `instance.py:276-281` empty-allow branch; only `watcher` affected — desired (D-FA2.5) |
| S-04 | tool-api-design §4.4 (1) | User-origin marker plumbing | Whitelist-gated stamp at `instance_messaging.py:1319` vicinity → `manager._user_origin_windows[instance_id]` (turn-scoped, +15min) (D-FA3.1/3.2) |
| S-05 | tool-api-design §4.4 (2) / §12-5 / R-SR10 | Nonce store location | Journal extension: `releases/state.json` `pending_actions`; consumption journaled; `nonce-verification-unavailable` fail-closed (D-FA3.3) |
| S-06 | tool-api-design §4.4 (3) | Multi-instance scoping | Window keyed by instance_id; children never inherit (their sources are `internal_*`) (D-FA3.2) |
| S-07 | tool-api-design §4.4 (4) | Row vs bus read | Gate-time: MessageQueue row by message_id (single row, no bulk history); post-arm: journal (D-FA3.3) |
| S-08 | tool-api-design §6.4 / §12-1 / phase2-plan D2-adjacent | Exit-74 vs daemonized executor | Daemonized executor for BOTH; exit-74 deferred as future ADR (design preserved) (D-FA1.3) |
| S-09 | phase2-plan D2 | Post-turn trigger mechanics | Deferred-pause marker + post-graph callback (`graph.py:3359-3520` precedent); bounded waiter = fallback only; additive → PR-time e2e confirmation (D-FA1.4) |
| S-10 | tool-api-design §6.5 / phase3-plan D4 | Push-notification post-restart / offline notifier | ADR-025(b) watchdog-watcher journal-watch extension — now a hard dependency of the failure-path story; live-pointing = USER-GATED (D-FA1.2) |
| S-11 | tool-api-design §1a A1 / phase2-plan D1 / ADR-023 | `upgrade_status` fold vs separate | **Separate — 4 tools** (⟲ OVERRIDE of W1 D1; fallback clause: run_id filter mandatory if folded) (D-FA2.1) |
| S-12 | tool-api-design §1a A2 / phase2-plan D6 | Live `system_restart` | Refused outright this initiative; 3-factor design ships as future opt-in, schema-stable (ratified) |
| S-13 | tool-api-design §1a A3 | `user_confirmed` on demo | Present-but-ignored (schema-stable for the future opt-in) (ratified) |
| S-14 | phase1-plan D4 / Coupling | Journal schema enrichment (pending_restart, drain handoff) | Superseded by `pending_op` + `pending_actions` (D-FA1.1/D-FA3.3); drain fields remain a future-initiative extension point |
| S-15 | risk-register R-SR03 | Scripts lock-aware | mkdir lock-dir protocol in `lib.sh` + Python journal module + **deploy.sh** (manual deploys serialize) (D-FA5.1) |
| S-16 | risk-register R-SR04 | Launcher flat-fallback hardening | WARN + divergence journal event + boot-on-flat + freeze all mutations (`layout-divergence`); schema-conditional halt deferred to Phase-5 hook; `stage.sh --migrate-flat` before first tool-driven promote (D-FA5.3) |
| S-17 | risk-register R-SR09 / R-SR02 | Executor spec | Daemonized both tools; env-allowlist exec; not in BashProcessRegistry; pid-file + journal; bounded lifetime (D-FA1.3) |
| S-18 | phase2-plan Coupling | e2e blast radius of the trigger seam | Stays additive (outside the graph) → core packs + PR-time confirmation; if implementation moves it into the graph, full e2e gate triggers (D-FA1.4) |
| S-19 | phase1-plan T5 (implicit) | Cap enforcement point | Promote preflight refuses ≥3; rollback always executes (recovery-first) (D-FA4.2) |
| S-20 | phase1-plan T2/T3 | What is checksummed + directory hashing | Per-file sha256 tree manifests for dirs; checksum binary/launcher/config/agents/frontend at stage + preflight (current & target) + `status.sh --verify`; no-`.env` invariant (D-FA4.4) |
| S-21 | phase1-plan D2/T3 | Version smoke | `/livez` version vs manifest (ADR-027 ratified); `--version` CLI rejected (D-FA4.4) |
| S-22 | risk-register R-SR05 / phase1-plan R1.2 | Interim rollback_safe gate design | Decision field = `rollback_safe`; halt + NO flip when unsafe; `known_schema_gen` informational (Phase-5 seam); residual stated (D-FA4.5) |
| S-23 | risk-register R-SR13 | Sweep vs pending-op discrimination | kind=promote only + >600s + dead owner (kill -0 / heartbeat >300s); restart-kind never launcher-swept (D-FA4.3) |
| S-24 | plan-overview §6 #1 | Restart tool vs ADR-015 | `system_restart` added (ADR-016) — ratified with health-gated routing constraint |
| S-25 | plan-overview §6 #2 | Env-target permission model | Ratified (ADR-017) + check order S-…/D-FA2.4 with self-match before live gates |
| S-26 | plan-overview §6 #4 / ADR-018 | Scripts vs make | Ratified — scripts canonical; make thin wrappers optional; no pipeline logic in make |
| S-27 | phase2-plan D5 | Confirmation across daemon death | Journal-bound (`confirmed_by_human` on the txn); no re-ask post-death (ratified) |
| S-28 | phase2-plan D7-adjacent | Cap/cooldown surfacing | `release_info`/`upgrade_status` expose counters, cooldown, quarantine, halt/sweep events (ratified) |
| S-29 | phase3-plan D4 | Offline alert channel | ADR-025(b) watchdog-watcher extension (S-10) — ratify as the decided channel |
| S-30 | phase2-plan T2 registration | Category registration mechanics | 4-step checklist ratified (decorator, CATEGORY_MODULES `:423-457`, allow expansion, list-append) + frozen-name regen; line refs corrected in corpus |
| S-31 | test-strategy §1 P2.2 (gate matrix) | Fabricated-param test shape | Matrix stands; add `nonce-verification-unavailable` + `env-marker-absent` + `layout-divergence` refusal cases; "agent with no tools.allow sees 0" now anchored on `watcher` |

---

## Architect Disagreements with the Planner (for reviewer arbitration)

| # | Planner position | Architect position | Rationale |
|---|------------------|--------------------|-----------|
| 1 | phase2-plan D1: fold `upgrade_status` into `release_info` (3 tools) | **4 tools — separate `upgrade_status`** | Run-id correlation across process death is the load-bearing feature; a param fold hides the join key; "fewer tools" doesn't shrink the gated surface (both read-only). Unanimous council. Aligns with W2 §1 + ADR-023. |
| 2 | tool-api-design §4.1 / risk-register R-SR07: "HUMAN is stamped ONLY at :1319 (user input via API)" | **Imprecise — HUMAN is the else-branch default; whitelist hardening required** | Both councilors verified `cascade_resume`/`internal_invoke_and_wait:`/`agent:` rows are HUMAN-typed today. The anti-forgery claim holds by caller-discipline, not structure. `USER_ORIGIN_SOURCES` whitelist makes it structural. |
| 3 | tool-api-design §3.5 / risk-register R-SR16: "empty-allow agents (e.g., worker, explorer)" | **Factually wrong — only `watcher` is empty-allow** | worker=14 explicit entries; explorer=8+deny. Exclusion regresses nobody; watcher losing upgrade tools is desired. Corpus corrected in-place. |
| 4 | tool-api-design §6.4: "(B) exit-74 preferred if the contract change is acceptable" | **74 deferred; daemonized executor for both** | R-SR06 ship-ordering + pre-74 bootstrapping window; the converged turn-end trigger removes the race 74 was designed to kill. Close call — dissent preserved (§D-FA1.3). |
| 5 | phase1-plan T5-adjacent: cap as rollback-arm gate (agentic reading) | **Entry-side refuse; rollback always executes** | The cap bounds the promote→fail→rollback loop at entry; refusing the recovery strands a flipped-broken env (esp. the sweep path). |
| 6 | phase3-plan D1 body: "Why 5 / 5 consecutive" | **N=3 per ADR-021 default** | Internal contradiction with its own header + ladder + decisions.md. Body corrected in-place; user-gated N itself NOT re-litigated. |

---

## Corpus Edits Applied In-Place (each flagged in the doc)

| # | File | Edit |
|---|------|------|
| 1 | phase3-plan.md | D1 body + T9 + R3.3 + exit-criterion: 5→3 cycles (aligned to ADR-021 default; flagged "architect edit 2026-08-22") |
| 2 | tool-api-design.md §3.5, §12-3; risk-register R-SR16 | worker/explorer → `watcher` correction + mechanism note |
| 3 | phase2-plan.md:22,70; plan-overview.md:56,179 | `CATEGORY_MODULES` line ref `:206-236` → `:423-457` |
| 4 | tool-api-design.md §2.1 | `/readyz` ≤180s → ≤120s (canonical gate budget) |
| 5 | phase2-plan.md (objective, D1, T2, T3, exit-1); plan-overview.md (P2.2 row, §8 Q3); test-strategy.md (§1 header, registration row, drift row) | A1 fallout: 4 tools everywhere; Q3 marked DECIDED |
| 6 | tool-api-design.md §6.4, §12-1; phase2-plan.md D2 seam; risk-register R-SR09 | Executor seam RESOLVED (daemonized both; 74 deferred; waiter = fallback) |
| 7 | phase1-plan.md T2 | `launcher.sh` joins the staged payload + `launcher_sha256` |
| 8 | tool-api-design.md §4.1; risk-register R-SR07 | HUMAN-stamp else-branch qualification + whitelist hardening + separate-defect note |

**Required follow-up edits (NOT applied — W3's docs, listed for the implementer):** mint **ADR-029** (daemonized executor for both / exit-74 deferred), **ADR-030** (launcher.sh in staged payload), **ADR-031** (`PRIVILEGED_TOOL_CATEGORIES`), **ADR-032** (`USER_ORIGIN_SOURCES` whitelist) in `decisions.md` — **numbering renumbered +1 on 2026-08-22 post-review (original proposal collided with the minted ADR-028). NUMBERING CHECK (mandatory in the minting task): before minting, confirm the current max ADR number in `decisions.md` and mint sequentially above it — never reuse or skip a minted number;** update `promotion-ladder.md` §3 channel table to name the watchdog extension as decided (ADR-025(b)); test-strategy §1 P2.2 gains the three new refusal cases (S-31).

---

## Risk-Register Additions / Updates

| ID | Change | Detail |
|----|--------|--------|
| **R-SR18** (new, L/M) | Restart-executor env-allowlist surface | The daemonized restart executor (now used for BOTH tools) inherits a minimized env (PORT, INSTALL_DIR, PATH, PG_* minimum); allowlist drift is the risk — test asserts the child env contains no `.env` secrets |
| **R-SR19** (new, L/M) | `nonce-verification-unavailable` | Daemon restart between nonce issuance and consumption wipes the HUMAN row → fail-closed refusal; UX cost only (re-run dry_run); test the path |
| R-SR01 (tightened) | Task↔JobItem gap amplification | Restarts multiply intermediate-state tasks; pre-existing gap NOT fixed here; flagged to the reconciliation initiative |
| R-SR04 (updated) | Flat-fallback semantics | WARN + divergence event + frozen mutations (replaces "optionally exit 78"); schema-conditional halt = Phase-5 hook |
| R-SR05 (updated) | rollback_safe semantics | `rollback_safe` = decision field, halt + NO flip; `known_schema_gen` informational; residual unchanged until daemon_meta |
| R-SR07 (updated) | Origin-marker foundation | Else-branch default verified; whitelist hardening; else-branch mis-typing logged as separate follow-up defect |
| R-SR16 (updated) | Agent population correction | `watcher` only; mechanism = PRIVILEGED_TOOL_CATEGORIES |

**Separate follow-up defect (NOT this initiative):** `instance_messaging.py:1310-1319` else-branch stamps `cascade_resume` / `internal_invoke_and_wait:` / `agent:` rows as HUMAN — mis-typed origin for internal machinery; fixing it touches watchover/job-queue semantics (wide blast radius). Record in the defect backlog.

---

## ADR Consistency

**No unresolved conflicts** with ADR-001/002/003/004/005/006/007/008/009/010/011/012/013/014/015 or ADR-016…027 (council-verified). Notable alignments/amendments: ADR-004 gains launcher-in-payload (ADR-030-to-be); ADR-005's cap semantics clarified entry-side (D-FA4.2, consistent with D2's intent); ADR-010 **unchanged** (74 deferred — no exit-code amendment ships); ADR-012 sweep concretized + restart-kind exclusion; ADR-015's two-factor extended to three (deviation #2 as planned); ADR-018/022/023/024/027 ratified as written.

---

## Unverified Assumptions (verify before implementation freeze)

1. **`USER_ORIGIN_SOURCES` prefix strings** — exact external-channel source prefixes (`telegram:`/`discord:`/`slack:` forms) must be read from the adapter dispatch paths before the whitelist freezes; a wrong prefix silently de-gates genuine user messages (fail-closed direction: refusal, not bypass).
2. **Executor daemonization primitive** — `subprocess.Popen(..., start_new_session=True)` ≡ double-fork+setsid on macOS/PyInstaller; confirm no BashProcessRegistry/BashProcessRegistry-adjacent teardown reaches it (spec says: NOT registered).
3. **`watchdog-watcher.sh` extension surface** — confirm the script can read `.launcher-state` + `releases/state.json` (file-format stability) before P2.3 depends on it.
4. **e2e-gate additivity** — the post-graph trigger consumer must be reviewed at PR time to confirm it stays additive (if it moves into the graph, the full e2e release gate triggers — `ensure.md:44-53`).

---

## Confidence

**High.** Unanimous council convergence on ~80% of decisions; all four forks adjudicated with preserved dissent; every decision lands on a named file/function/field; three corpus factual defects corrected with verification. The two genuine judgment calls — executor seam (Fork #1) and cap semantics (Fork #3) — are close calls with fully specified alternatives if the reviewer flips them.
