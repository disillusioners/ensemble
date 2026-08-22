# Risk Register — Self-Restart / Self-Upgrade (Phase 2)

- **Initiative:** self-restart-upgrade-phase2
- **Branch:** `plan/self-restart-upgrade-phase2` @ `653e8e71`
- **Owner:** W2 (companion: `tool-api-design.md` — cross-referenced as §N)
- **Siblings:** W1 `plan-overview.md` + `phaseN-plan.md`; W3 `test-strategy.md`, `promotion-ladder.md`, `decisions.md` — referenced by filename.
- **Owner-phase legend (FIXED IDs):** P2.1 = release/upgrade pipeline; P2.2 = agent-facing tools; P2.3 = rollout ladder + drills + carry-overs.

---

## ⚠️ HARD CONSTRAINT (user directive — VERBATIM)

> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**Every risk below is evaluated against this constraint.** Risks marked **USER-GATED** involve any step that could touch live and are designed as explicit user-confirmed actions (see `tool-api-design.md` §3, §4 for the structural encoding: env self-match rule, 3-factor confirmation gate, no raw kills).

---

## Risk Summary Table (ordered by severity: likelihood × impact)

| ID | Risk | L | I | Mitigation (one-line) | Owner phase |
|----|------|---|---|----------------------|-------------|
| R-SR11 | Port confusion 7979 (demo) vs 9797 (live) — one-digit typo targets live | M | **H** | Tool: env self-match rule refuses cross-env (§3.2). Scripts/humans: port echo in every result + dry_run prints resolved install dir + port; refuse when target port == 9797 without live gate | P2.2 / P2.3 |
| R-SR05 | PG schema drift on rollback — boot-time destructive drops make binary rollback unsafe across schema-changing releases | M | **H** | Accepted residual (daemon_meta OUT of scope); manifest `rollback_safe` interim gate; **USER-GATED** live promotion crossing schema changes | P2.1 |
| R-SR07 | Confirmation-gate circumvention — LLM fabricates `user_confirmed=true` | M | **H** | 3-factor server-side gate (§4.3): param + HUMAN-origin marker + action-binding nonce; single-host trust bounds residual (R10 original) | P2.2 |
| R-SR01 | Self-restart-while-agents-running — in-flight jobs/instances/checkpoints/report-injections | **H** | M | SINGLE-TERM graceful stop + checkpoint-resume semantics + StaleTaskRecovery boot sweep; residual = delayed child reports (~10min), Task↔JobItem gap amplified | P2.1 / P2.3 |
| R-SR16 | Empty-allow permission leak — agents WITHOUT `tools.allow` get ALL categories incl. `system_upgrade` | M | **H** | Special-case: `system_upgrade` excluded from empty-allow universe (§3.5); only constructed when explicitly in `tools.allow` | P2.2 |
| R-SR03 | Concurrent upgrade attempts — two Ari turns, Ari+jober later, manual `deploy.sh` while pipeline runs | M | H | Per-env pipeline lock (`rollback.lock.d` mkdir-lock, D-FA5.1: owner pid + `run_id` + heartbeat files, stale-breakable >300s via `mv` to `rollback.lock.stale.<pid>`, §5.4); second invocation returns `pipeline-busy` | P2.1 / P2.2 |
| R-SR04 | Partial-trio rollback / mixed flat+trio layouts — launcher `current/`-then-flat fallback masks a missing release | M | H | Migration step in P2.1: convert flat→trio before first tool-driven promote; launcher logs which path resolved; journal refuses promote when layout is mixed | P2.1 |
| R-SR02 | Tool-call-return-vs-process-death — sequencing residual + rejected alternatives | M | M | Hybrid arm→return→poll (§6.3): on-disk pending-op + post-turn deferred-pause trigger + daemonized executor; rejected alternatives' risks documented below | P2.2 |
| R-SR06 | Launcher/binary version skew — old launcher + new binary knob/contract drift (exit codes, journal schema, `.launcher-state` fields) | M | M | Launcher is versioned WITH the release trio (deploy stages it — `deploy.sh:278`); journal schema versioned + tolerant reader; P7 fact: knob clear requires daemon restart (`readiness.py:48-67`) documented in runbook | P2.1 / P2.3 |
| R-SR13 | Launcher journal-sweep misfire — new sweep code in the restart path mistakes a tool-armed pending-op for an orphaned promote | M | M | Pending-op carries `owner` + `kind=restart|promote` + heartbeat; sweep only acts on `kind=promote` transactions past the 10-min window with a dead owner (ADR-012) | P2.1 / P2.2 |
| R-SR10 | Ephemeral MessageQueue loss of confirmation trail — approvals live nowhere durable | **H** | M | Nonce + pending-action persisted to `releases/state.json` (disk — survives `clear_all(preserve_in_flight=True)` at startup, `manager.py:596`); nonce consumption journaled | P2.2 |
| R-SR08 | Burst-budget interaction with intentional restarts — uptime reset / misattribution of post-restart crashes | M | M | Executor produces CLEAN shutdown (exit 0 path — no crash-counter tick); journal `kind` field prevents misattribution; `restart-under-burst-abort` refusal in exit-1 latch hold | P2.1 / P2.2 |
| R-SR09 | Daemonized-executor orphaning / credential leak — inherits daemon env incl. `.env` API keys | M | M | Env-allowlist exec (PORT, INSTALL_DIR, PATH only); close fds; `/dev/null` stdio; pid-file + journal entry; bounded lifetime ≤~3min | P2.2 |
| R-SR12 | SSE notification loss when daemon is down — NotificationBroadcaster requires daemon up | **H** | L | Accepted (drills document the gap); watchdog-watcher precedent (ADR-008) covers >10min absence; pull-model outcome via `upgrade_status` is the primary UX (§6.5) | P2.3 |
| R-SR14 | Rollback into a quarantine-loop — rollback target itself quarantined or evicted | L | H | Eviction pins `previous` (ADR-004); rollback gate checks target manifest `rollback_safe` AND not-quarantined; halt-for-human on cap (ADR-005 D2) | P2.1 |
| R-SR15 | N-cycle gate measurement gaming/staleness — `/readyz` cached composite sampled stale; version lying | L | H | Gate samples post-restart fresh (10s refresher documented); version verify `/livez` vs `manifest.binary_version` (§5.10); soak 300s defeats transient-green | P2.1 |
| R-SR17 | Deferred-pause marker lost (in-memory) — Ari said "scheduled" but restart never fires | M | M | On-disk pending-op is authoritative (§6.3 step 1); boot sweep converges (ADR-012 pattern); in-memory marker is only the trigger, not the state | P2.2 |
| R-SR18 | Daemonized-executor env-allowlist surface — restart executor inherits a minimized env; allowlist drift leaks secrets | L | M | Allowlist fixed (PORT, INSTALL_DIR, PATH, PG_* minimum; no `.env` passthrough; fds closed; stdio → `data/upgrade.log`); unit asserts child env contains no `.env` secrets (arch D-FA1.3/R-SR18) | P2.2 |
| R-SR19 | `nonce-verification-unavailable` — daemon restart between nonce issuance and consumption wipes the HUMAN row → fail-closed refusal | L | M | Fail-closed by design (refuse, "re-run dry_run"); UX cost only; the path is unit-tested (S-31) | P2.2 |
| R-SR20 | Corrupted staged artifact / TOCTOU in the stage→promote window — artifact+manifest swapped together passes self-attestation | L | M | Journal records `manifest_sha256` at stage; preflight/`--verify` compare on-disk vs journal; single-host trust bounds; version verify + gate surface functional corruption; no-`.env` assertion at both ends (detail below) | P2.1 |

---

## Per-Risk Detail

### R-SR11 — Port confusion: 7979 (demo) vs 9797 (live) — **the one-digit catastrophe**

**Description.** Demo env binds PORT=7979 (`deploy.sh` `.env.prod` overrides); live binds 9797 (ADR-014 D1 FINAL). A one-digit transposition in a script flag, env var, or URL targets the LIVE environment — catastrophic given the hard constraint (live pids must remain untouched).

**Likelihood M / Impact H.**

**Verified facts.** `deploy.sh` stages `.env` from `ENV_SOURCE` (`:286`); demo DB is `ensemble_demo` (`:262`); live port 9797 comes from `.env.prod` (ADR-014). No structural cross-env guard exists in any script today.

**Mitigation.**
- **Tool layer (P2.2):** `target_env` self-match rule (§3.2) — the tool refuses any target ≠ the running daemon's own env (`ENSEMBLE_SELF_ENV` marker). Dev-Ari cannot touch live, period. This eliminates the typo class for all tool-driven actions.
- **Script layer (P2.3):** every pipeline script result echoes the resolved install dir + port; `dry_run` prints them before any mutation; refuse when resolved port == 9797 unless the full live gate (ENSEMBLE_DEPLOY_LIVE=1-equivalent + explicit operator confirmation) is present.
- **Drills (P2.3):** the drill matrix includes a "wrong-env refusal" case (attempt demo-target from a live context → expect refusal).

**USER-GATED.** Any live-targeting action is an explicit user-confirmed action (§4 gate).

---

### R-SR05 — PG schema drift on rollback: boot-time destructive drops make binary rollback unsafe

**Description.** Boot runs destructive legacy column drops unconditionally (`daemon/manager.py:478-501` — `_ensure_postgres_drop_admission_legacy()`; the SQL migration runner is a NO-OP on PostgreSQL, so the equivalent ALTERs run at startup). Rolling a binary back across a schema-changing release is unsafe: the old binary boots against a DB the new release already mutated destructively.

**Likelihood M / Impact H.**

**Verified facts.** `manager.py:478-501` (boot-time unconditional drops); migrations runner SQLite-only (Phase-1 plan C4); `daemon_meta` migration guard (ADR-007) is **OUT of this initiative's scope** — it lands in a later phase.

**Mitigation.**
- **Accepted residual risk** — carried explicitly (per mandate). The interim gate is the release **manifest `rollback_safe`** flag (ADR-007 M5 amendment): a release that crosses schema changes ships `rollback_safe=false`; auto-rollback HALTS instead of flipping (halt-for-human + notify).
- The promote preflight surfaces the flag to Ari (`release_info` / dry_run payload, §2.1) so the user sees "rollback unsafe" BEFORE authorizing.
- **USER-GATED:** any LIVE promotion crossing schema changes is an explicit user-confirmed action, with the `rollback_safe=false` fact stated in the confirmation flow.

**Residual (accepted):** binary rollback across schema changes remains unsafe until `daemon_meta` + contract-phase gating land (ADR-007 Phase 5). The register carries this; the manifest flag is the only gate.

---

### R-SR07 — Confirmation-gate circumvention: LLM fabricates `user_confirmed=true`

**Description.** An agent (or a prompt-injected instruction) calls `system_upgrade`/`system_restart` with `user_confirmed=true` fabricated. If the param were the only gate, LIVE would unlock from an LLM decision — violating "human-triggered only, never autonomous" (ADR-015) and the hard constraint.

**Likelihood M / Impact H.** (R10 in the original auto-restart-upgrade plan.)

**Verified facts.** No user-originated trigger marker exists today (ADR-015 OQ9 open). `MessageQueue.type` is the strongest origin discriminator (`models.py:19-25,49`): `MessageType.HUMAN` is stamped ONLY at `instance_messaging.py:1319` (user input via API); agent-injected messages (`enqueue_message`) stamp AGENT/SYSTEM. An LLM cannot enqueue a HUMAN-type message through any tool surface **[verified correction 2026-08-22: HUMAN is the else-branch default at `instance_messaging.py:1310-1319`; the claim holds by caller-discipline today — agent paths use `internal_agent:` prefixes — and is made STRUCTURAL by the `USER_ORIGIN_SOURCES` whitelist at the stamp site (architecture-recommendation.md FA3/D-FA3.1). The else-branch mis-typing of `cascade_resume`/`internal_invoke_and_wait`/`agent:` rows is a SEPARATE follow-up defect — wide blast radius, NOT fixed in this initiative.]**

**Mitigation — the 3-factor server-side gate (§4.3):**
1. `user_confirmed: true` param (necessary, never sufficient)
2. Triggering turn originated from a genuine HUMAN `MessageQueue` row (server-side marker — the LLM cannot forge this)
3. The triggering HUMAN message **content contains the action nonce** issued by the tool's dry_run (action-binding — the user confirmed THIS action, not just "said upgrade")

**Residual (accepted, bounded):** single-host trust model — an attacker with daemon-process access can forge anything (R10's original bound). The API is unauthenticated single-user; the browser is trusted as "the user". On this host model the residual is accepted.

**USER-GATED.** LIVE execution requires all 3 factors; refusal is structured (§4.4) and Ari relays verbatim.

---

### R-SR01 — Self-restart-while-agents-running semantics

**Description.** A restart (or upgrade's stop phase) interrupts in-flight jobs, instances, checkpoints, and report-injections. The semantics must be understood, bounded, and documented — not "fixed".

**Likelihood H / Impact M.**

**Verified facts to build on:**
- Tasks stay `PROCESSING` on crash/pause (not FAILED) — pause cancels the graph task; checkpoints freeze at the last committed node boundary; resume is DB-only with `is_retry=True` from the checkpoint.
- `StaleTaskRecovery.recover_on_startup()` sweeps stale RUNNING >15min at boot (`daemon/services/stale_task_recovery.py:637-795`): force-cancel + atomic retry-scheduling + bus-cancel notification so parents don't hang in `waiting_children`.
- `ReportDeliveryRecovery` is **periodic-only** (interval 300s, age-bound 10min — `daemon/services/report_delivery_recovery.py:136,275`; NO boot sweep) → **child reports delivered near-restart are delayed up to ~10 minutes after the daemon returns**.
- `MessageQueue` is EPHEMERAL: `clear_all(preserve_in_flight=True)` at startup (`daemon/manager.py:596`) — queued-but-unprocessed messages from the pre-restart lifetime are wiped (in-flight preserved).
- **Known Task↔JobItem reconciliation gap (pre-existing project risk):** JobItem done/cancelled but linked Task stays paused, blocking idle-gates forever (`daemon/services/instance_lifecycle.py`, `task/repository.py:2126-2241`). **Restart amplifies it** — more tasks in intermediate states at once.
- **Pause-tool-result known limitation:** an in-flight tool result is lost when the task is cancelled mid-tool (pause-tool-result fix history).

**Mitigation.**
- SINGLE-TERM graceful stop (`stop-ensemble.sh` contract: launcher-only TERM, `CHILD_STOP_WAIT_S=70`, `WAIT_S` clamp 10..600) + uvicorn `timeout_graceful_shutdown` gives in-flight finalization its drain window (bounded — drain is a courtesy, not correctness; ADR-006).
- The deferred-execution design (§6.3) fires the restart AFTER the triggering turn completes — the tool's own turn is never the casualty.
- Drill matrix (P2.3, W3 `test-strategy.md`): restart-with-children-running, restart-mid-report-injection, restart-during-job-dispatch cases; assert recovery within documented bounds (stale sweep ≤15min boot path; report delivery ≤~10min).

**Residual (accepted):** child-report latency up to ~10min post-restart (ReportDeliveryRecovery cadence); the Task↔JobItem gap is pre-existing and NOT fixed by this initiative — restarts increase its exposure. Carry as a known interaction; flag to the task-job-reconciliation initiative.

---

### R-SR16 — Empty-allow permission leak: agents without `tools.allow` get ALL categories

**Description.** ADR-015 claims `tools.allow` default-deny satisfies "deny rules for other agents" structurally. **Verified nuance:** `daemon/tools/instance.py:276-281` — when `allow` is None or empty, *"No allow list means everything is potentially allowed"* — the agent gets ALL categories, which after P2.2 includes `system_upgrade`/`system_restart`. Any agent created without an explicit allow-list silently gains live-capable tools (gated only by the confirmation gate — which a demo-env agent would bypass for demo targets, and which protects live but not the principle of least privilege).

**Likelihood M / Impact H.**

**Mitigation (P2.2, §3.5):** special-case the `system_upgrade` category in `create_instance_tools()`: excluded from the empty-allow universe; only constructed when explicitly present in `tools.allow`. Structural, no deny rules needed. ⟪SEAM: confirm no regression on existing empty-allow agents — architect.⟫ **RESOLVED 2026-08-22: corpus correction — worker/explorer are explicit-allow (14 / 8+deny); `watcher` is the ONLY empty-allow agent; exclusion is the desired outcome, no regression. Mechanism: `PRIVILEGED_TOOL_CATEGORIES` frozenset (`_tool_registry.py`) consumed in the empty-allow branch (`instance.py:276-281`). See architecture-recommendation.md FA2/D-FA2.5.**

**Test (W3):** unit test — agent with no `tools.allow` sees 0 system_upgrade tools; ari sees 4.

---

### R-SR03 — Concurrent upgrade attempts

**Description.** Scenarios: (1) two Ari turns both call `system_upgrade` (user double-sends; retry after timeout); (2) Ari + jober later (post-D-4) race; (3) the user runs `deploy.sh`/promote manually from a shell while a tool pipeline runs.

**Likelihood M / Impact H.**

**What breaks if violated:** two pipelines racing stop/flip/start produce a torn release state (flip A then flip B, journals diverge, rollback targets ambiguous); manual `deploy.sh` mid-pipeline destroys the staged trio (`rm -rf` clean staging — `deploy.sh:276,282`).

**Mitigation.**
- **Per-env pipeline lock** (`rollback.lock.d` mkdir-lock — D-FA5.1 canonical: mkdir = atomic acquire; `owner`/`run_id`/`heartbeat` files, stale >300s → `mv` to `rollback.lock.stale.<pid>`): tool preflight acquires; a second tool call returns `pipeline-busy run_id=...` (structured, not error).
- The lock is shared with the script pipeline (same file) — manual `deploy.sh`-driven promotes take it too (P2.1 requirement; ⟪SEAM: W1 to make the scripts lock-aware⟫).
- Idempotency: `run_id` dedup; nonce single-use prevents double-arm from one confirmation.
- Drill (P2.3): concurrent-invocation case → expect one `SCHEDULED` + one `pipeline-busy`.

---

### R-SR04 — Partial-trio rollback / mixed flat+trio layouts

**Description.** Today's install is **flat**: `rm -rf` + `cp` with NO backup (`deploy.sh:276,282` — the `.baks` in the live dir are hand-rolled legacy, NOT deploy.sh behavior). P2.1 introduces trios (`releases/<ver>/{binary,agents,frontend/dist,manifest.json}` + atomic `current`). Risks: (a) pre-P2.1-pipeline state has no journal → tools cannot reason about it; (b) **mixed flat+trio layouts** — a flat binary at `INSTALL_DIR/ensemble-prod` coexisting with `releases/`; (c) launcher prefers `current/` then falls back to flat (`launcher.sh:349-374`) — **the fallback can MASK a missing/evicted release**, silently running a stale flat binary while the journal claims a trio version.

**Likelihood M / Impact H.**

**Mitigation.**
- P2.1 includes a **one-time layout migration**: convert flat→trio (stage the current flat payload as `releases/<current-version>/`, create `current`, remove the flat binary) BEFORE any tool-driven promote; journal initialized at migration.
- Promote preflight refuses when layout is mixed (flat binary present + trios present) — halt-for-human with a remediation hint.
- Launcher hardening (⟪SEAM: W1⟫): when the journal exists, a flat-fallback resolution logs a **WARN + journal divergence marker** (not silent); optionally exit 78 when journal says trio-layout but only flat resolves.
- Drill (P2.3): flat→trio migration; missing-release fallback behavior.

---

### R-SR02 — Tool-call-return-vs-process-death sequencing

**Description.** The chosen design's residual + the rejected alternatives' risks. Verified constraints: all tools in-process; an in-flight tool call at daemon death is LOST (turn frozen at last committed node boundary, NOT re-executed on `is_retry` resume); `MessageQueue` ephemeral; bash tool kills process groups (`bash.py:138-160`) so a tool-spawned child needs daemonization to survive.

**Likelihood M / Impact M.**

**Chosen design (§6.3 — hybrid arm→return→poll):** residual risks:
- The post-turn trigger (deferred-pause seam) may crash/never fire → **mitigated by the on-disk pending-op + boot sweep** (ADR-012 pattern) — convergence within one rollback window.
- Ari's "SCHEDULED" promise spans the death: if the daemon dies BEFORE the executor starts (unrelated crash in the window between tool return and post-turn callback), the pending-op executes at next boot (restart latency, not loss).
- `upgrade_status` polling during daemon-down returns connection errors to Ari — Ari must relay "daemon restarting" rather than "tool broken" (prompt-level; W1 P2.2 agent-instruction).

**Rejected alternatives' risks:**
- (iii)-pure (immediate daemonized stop, return "before death"): race — daemon may die before the tool result commits to the checkpoint → lost result, frozen turn, undelivered report. **Rejected.**
- (i)-pure (in-memory marker only): lost marker on death-before-callback → silent no-restart; Ari's "scheduled" claim false. **Rejected as sole mechanism.**
- (ii)-pure (marker only, execute at next natural stop): unbounded latency; not "restart now". **Rejected as sole mechanism.**

---

### R-SR06 — Launcher/binary version skew

**Description.** Old launcher + new binary (or vice versa) drift on: exit-code table (a new "restart-me" code per §6.4 would be read as unknown by an old launcher), journal schema (new fields vs old sweep), `.launcher-state` fields (new counters vs old readers).

**Likelihood M / Impact M.**

**Verified facts.** The launcher is staged WITH the release (`deploy.sh:278` stages `launcher.sh` into INSTALL_DIR) — but the RUNNING launcher process is the one that started the daemon; a promote swaps files under it. The next launcher start picks up the new script; the current cycle runs the old one. P7 fact: `ENSEMBLE_READINESS_FORCE_DEGRADED` is read per refresh tick but **requires a daemon restart to clear** (`readiness.py:48-67`) — knob-clear semantics belong in the runbook (P2.3 carry-over).

**Mitigation.** Journal schema carries a version field + tolerant reader (unknown fields ignored, missing fields defaulted); exit-code additions are backward-compatible (old launcher treats unknown nonzero as crash-track — so a §6.4 "restart-me" code MUST NOT ship before the launcher that understands it; sequencing constraint for W1 P2.1); runbook documents knob-clear-requires-restart.

---

### R-SR13 — Launcher journal-sweep misfire

**Description.** The ADR-012 launcher-start journal sweep (new code in the restart path — currently a STUB at `launcher.sh:151-174` with the Phase-3 contract in comments) could misfire: mistaking a tool-armed pending-op (restart scheduled post-turn) for an orphaned promote transaction and executing a rollback the user never asked for.

**Likelihood M / Impact M.**

**Mitigation.** Pending-op records carry `owner` (executor PID + heartbeat) + `kind=restart|promote`; the sweep acts ONLY on `kind=promote` transactions past the 10-min window with a dead owner (the ADR-012 contract as written — flip-happened → rollback, flip-never-happened → clear). Tool-armed restarts are `kind=restart` with a live owner → untouched. Sweep counts as an auto-rollback for cooldown/counters (ADR-012/005 interaction — already decided). Test (W3): sweep-vs-pending-op discrimination cases.

---

### R-SR10 — Ephemeral MessageQueue loss of the confirmation trail

**Description.** `MessageQueue` is wiped at startup (`manager.py:596` `clear_all(preserve_in_flight=True)`). If approvals (nonce issuance/consumption, user-origin evidence) lived only in `MessageQueue` rows or in-memory, they would be lost on restart — and a restart is exactly what this tooling causes. The audit trail of WHO authorized WHAT must survive.

**Likelihood H (every restart) / Impact M.**

**Mitigation.** Nonce + pending-action persisted to `releases/state.json` (disk); nonce consumption appended to the journal (durable audit: "nonce CONFIRM-... consumed by HUMAN msg <id> for run_id r-..."). The `MessageQueue` row is evidence at gate-time only; the journal is the durable record. ⟪SEAM: nonce store location (§4.4 OQ5).⟫

---

### R-SR08 — Burst-budget interaction with intentional restarts

**Description.** The launcher's burst budget (5 crashes/600s → abort-hold) and uptime-reset (≥600s → counter reset) interact with intentional restarts: (a) an intentional restart resets uptime → a subsequent genuine crash-loop starts a fresh budget (masks a pre-existing flakiness trend); (b) if the stop were mishandled as a crash (wrong exit path), each intentional restart would consume budget and eventually latch a burst-abort; (c) a restart during burst-abort hold is contradictory (restart would clear the condition that the hold exists to surface).

**Likelihood M / Impact M.**

**Mitigation.** Executor produces a CLEAN shutdown (exit 0 path — launcher exits, no crash tick); journal `kind` field records intentionality (post-restart crash attribution reads it); `system_restart` refuses with `restart-under-burst-abort` when the daemon is in exit-1 latch hold. Drill (P2.3): restart-after-burst-abort → expect refusal + runbook path.

---

### R-SR09 — Daemonized-executor orphaning / credential leak

**Description.** The pipeline executor (double-fork + `os.setsid` + `execv` — required to survive the daemon's death and escape bash-tool process-group kills, `bash.py:138-160`) inherits the daemon's environment — including `INSTALL_DIR/.env` contents (DB credentials, API keys) — and can orphan if it misbehaves.

**Likelihood M / Impact M.**

**Mitigation.** Env-allowlist exec (PORT, INSTALL_DIR, PATH, PG_* minimum — nothing else); close inherited fds; redirect stdio to a dedicated executor log; pid-file + journal entry (observable); bounded lifetime (stop+start+gate ≤~3min, self-terminate on completion); Phase-1 lesson encoded: `nohup` within the group dies — only `setsid` escapes. Alternative (§6.4 exit-code 74) eliminates the executor entirely — ⟪SEAM⟫ **RESOLVED 2026-08-22: executor chosen for BOTH restart and promote (one mechanism); exit-74 deferred as a future ADR (R-SR06 ship-ordering + pre-74 bootstrapping window). See architecture-recommendation.md FA1.]

---

### R-SR12 — SSE notification loss when the daemon is down

**Description.** `NotificationBroadcaster`/SSE requires the daemon up. During restart/upgrade downtime, notifications (including terminal pipeline outcomes) are lost to connected frontends; the reconnect gap is silent.

**Likelihood H (every restart) / Impact L.**

**Mitigation.** Accepted + documented: the primary UX is pull-model (`upgrade_status` on next interaction, §6.5); the watchdog-watcher precedent (ADR-008 — daemon absent >10min → notify) covers extended absence; P2.3 drills document the gap; frontend reconnect shows a "daemon restarted" nudge (⟪SEAM: frontend, P2.3⟫). **Do not** rely on ReportDeliveryRecovery for sub-minute UX (periodic-only 300s/10min-age — verified `report_delivery_recovery.py:136,275`).

---

### R-SR14 — Rollback into a quarantine-loop

**Description.** Auto-rollback targets `previous`; if `previous` is itself quarantined, evicted, or manifest-unsafe, the rollback loop could flap or halt in a degraded state.

**Likelihood L / Impact H.**

**Mitigation.** Eviction pins `previous` (ADR-004 — rollback target can never be evicted); rollback gate checks target manifest `rollback_safe: true` AND not-quarantined; unsafe → halt-for-human + notify (no blind flip); cap 3/24h + cooldown 10min (ADR-005 D2) bounds any residual flapping; quarantined versions are skipped by future promotes.

---

### R-SR15 — N-cycle gate measurement gaming/staleness

**Description.** The health gate (N consecutive probes within the window) can be fooled: (a) `/readyz` is a CACHED composite (10s refresher) — a gate sampling immediately post-restart may read a stale pre-restart value (false green) or a not-yet-refreshed degraded value (false red); (b) a stale binary could serve `/livez` green with the WRONG version (flip failed but old daemon answers).

**Likelihood L / Impact H.**

**Mitigation.** Gate samples post-restart FRESH: first `/livez` reachability, then `/readyz` green within ≤120s (the canonical deploy.sh phase-5 budget; the refresh cadence bounds staleness at 10s), then **version verify** — `/livez` reports version (`api.py:1719-1733`); it must equal `manifest.binary_version` of the promoted release (§5.10); 300s soak defeats transient-green. The `draining` flag is reserved always-false today (`api.py:1735-1779`) — gate code must not depend on it flipping.

---

### R-SR17 — Deferred-pause marker lost (in-memory)

**Description.** The §6.3 post-turn trigger uses the deferred-pause seam (graph marker + post-graph callback + `asyncio.shield` — `graph.py:3359-3520` precedent). If the daemon dies between tool return and callback fire, the in-memory marker is lost — Ari already told the user "scheduled".

**Likelihood M / Impact M.**

**Mitigation.** The in-memory marker is ONLY the trigger; the authoritative state is the on-disk pending-op (written by the tool BEFORE returning). Boot sweep (ADR-012 pattern) converges: pending-op with dead owner + past window → execute-or-clear. Worst case: restart happens at next boot instead of immediately — latency, never silent loss. Belt-and-braces with the daemon boot sweep (ADR-012's own dual-sweep design).

---

### R-SR20 — Corrupted staged artifact / TOCTOU in the stage→promote window (carry-note addition 2026-08-22)

**Description.** Between `stage.sh` (checksums computed + manifest written) and `promote.sh` preflight (checksums verified), a staged artifact can be corrupted — or, in the adversarial variant, artifact AND manifest swapped **together** (the manifest self-attestation then passes). D-FA4.4's per-file sha256 tree manifests cover accidental corruption and single-sided tampering; the paired swap is the residual TOCTOU window.

**Likelihood L / Impact M.**

**Mitigation.** (1) The journal records `manifest_sha256` at stage time; promote preflight and `status.sh --verify` compare the **on-disk manifest** against the journal-recorded hash — a swapped pair is caught unless the journal itself is also rewritten; (2) single-host, single-user trust model (R-SR07's bounds) — an attacker with write access to the install dir is already inside the trust boundary; (3) version verify (`/livez` version == `manifest.binary_version`) + health gate would surface a functionally corrupted binary at boot; (4) the no-`.env`-inside-release assertion runs at BOTH stage and preflight (D-FA4.4), narrowing the mutation window. Owner phase: **P2.1** (T2/T3 + journal field). Residual accepted and stated.

---

## Cross-References

> **Sibling alignment (2026-08-22):** W1's `phase2-plan.md` landed after this register. Three deltas are flagged in `tool-api-design.md` §1a — notably (A2) live `system_restart` is **refused outright this initiative** per W1 D6 (the safer hard-constraint reading; this register's R-SR07/R-SR11 mitigations unchanged — the refusal path is what drills assert); ~~and (A1) `upgrade_status` may be folded into `release_info` per W1 D1~~ **[SUPERSEDED 2026-08-22, reviewer-ratified D-FA2.1: `upgrade_status` stays a separate 4th tool; the fold is off the table (the §6.5/R-SR12 pull-model outcome reporting is unaffected — the journal read exists either way)]**.

- `tool-api-design.md` §3 (permission model / env self-match) — R-SR11, R-SR16
- `tool-api-design.md` §4 (confirmation gate) — R-SR07, R-SR10
- `tool-api-design.md` §5 (interlocks) — R-SR03, R-SR08, R-SR14, R-SR15
- `tool-api-design.md` §6 (process-death sequencing) — R-SR01, R-SR02, R-SR09, R-SR17
- W1 `phase1-plan.md` (P2.1 pipeline) — R-SR03, R-SR04, R-SR05, R-SR06, R-SR13, R-SR14, R-SR15
- W1 `phase2-plan.md` (P2.2 tools) — R-SR02, R-SR07, R-SR09, R-SR10, R-SR16, R-SR17
- W1 `phase3-plan.md` (P2.3 rollout/drills) — R-SR01, R-SR06, R-SR08, R-SR11, R-SR12
- W3 `test-strategy.md` — drill cases named per risk above
- W3 `promotion-ladder.md` — ladder steps carry the USER-GATED markers (R-SR05, R-SR11)
- W3 `decisions.md` — deviations D-1..D-8 from `tool-api-design.md` §11 should be ratified there
