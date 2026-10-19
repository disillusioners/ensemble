# Architecture Recommendation — First-Class Agent RESTART Capability

- **Status:** DESIGN-ONLY (no implementation) — commission `agent-first-class-live-restart`
- **Date:** 2026-10-19
- **Branch/worktree:** `feature/agent-restart` @ `/home/nea/ensemble-src-wt-agent-restart` (base `630cefebb`)
- **Release vehicle:** stage-only, rides next staged release (expected v0.25.5; exact number resolved at stage time). NO promote — user nonce ceremony owns that.
- **Method:** 3-worker competitive fan-out (security-design / resilience-design / data-flow-design), architect-aggregated. All file:line citations verified against this worktree.
- **Authoritative policy (user, verbatim):** "restart don't need my nonce, only my upgrade is need." ⇒ restart = user-triggered, NO nonce. Upgrade keeps its 3-factor nonce ceremony UNCHANGED.

---

## §1 Policy Change + Lineage

### 1.1 What is superseded

| Superseded ruling | Where it lives | New status |
|---|---|---|
| **A2** — live `system_restart` refused outright, "refusal-tested only", no gate/override/dry-run exception | `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md:13,116,143`; enforced `daemon/tools/upgrade_tools.py:2305-2320` (refusal string `:2319`) | **SUPERSEDED for `system_restart` live-restart-refusal ONLY.** Live restart becomes allowed with NO nonce when the calling turn carries genuine-user provenance (§2). |
| **§3.1 per-env matrix** — actor tools free dev/demo/sandbox; live behind 3-factor gate AND refused-at-execution | `.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md:34,223` | Superseded for the restart lane: live restart gated by provenance + rails (§2, §4), not by nonce. |
| **D-FA2.4** env-self-match + live outright-refusal, marked UNCHANGED by the wake supersession | `.agents/shared/planning/post-restart-arm-notify/supersession-record.md:89` | Env-self-match check order **unchanged**; only the live-refusal arm of the ruling is superseded. |
| Banner "anything touching live is USER-GATED / explicit user-confirmed action" | `.agents/shared/planning/post-restart-arm-notify/decisions.md:3-9` | Restated for restart: live restart IS user-gated — the gate is **genuine-user provenance**, not a nonce echo. The user-confirmed *nature* of the action is preserved; the *mechanism* changes. |
| Maintenancer prohibition "Never run `system_restart`" / "Never restart the daemon" | `agents/maintenancer/rule.md:8,68`; enforced via `agents/maintenancer/meta.json:13` `tools.deny` | **SUPERSEDED** — maintenancer becomes the arming agent under the new gate (§7.4). Seal re-pin required (§7.5). |

### 1.2 What is explicitly NOT changed

- **The upgrade 3-factor ceremony** (user_confirmed + genuine-user-turn + nonce; factors `self-restart-upgrade-phase2/architecture-recommendation.md:123-141`, enforced `upgrade_tools.py:2779-2945`). Restart reuses ONLY the factor-2 primitive's classifier (§2.2) through a disjoint store and a disjoint reader — the upgrade call sites keep byte-identical behavior (§2.6).
- **A3 schema stability** (`architecture-recommendation.md:116`): `system_restart` keeps `user_confirmed`/`nonce` params present-but-ignored on the live lane — the policy flip is a code path change, not a schema change.
- Non-live/dry-run paths behave as today.

### 1.3 Why the asymmetry flips (rationale to record with the lineage)

A restart is strictly less dangerous than a promote: same binary, no migration, no rollback surface, no `current` symlink change. The existing asymmetry (upgrade reachable via nonce ceremony, restart refused outright) forced manual `sudo systemctl restart ensemble-main.service` for the motivating case — `limits.worker_pool_size` 5→7 written by `settings_set` (`requires_restart: true, divergent: true`) but un-appliable by the agent. Policy now: provenance replaces the nonce for restart; rails (§4) replace "refuse outright".

---

## §2 Provenance Attestation Design

### 2.1 The existing primitive (verified)

- **Classifier (single source of truth):** `classify_user_origin(source, registry_get)` — `daemon/tools/upgrade_journal.py:2992-3047`. Arms on exact `"api"` (HTTP chat lane, `daemon/routers/messages.py:691`) OR a registered adapter whose daemon-controlled `source_type ∈ {telegram, slack, discord, whatsapp}` (`daemon/sources/registry.py:1130,1144-1149`). **Reserved internal lanes never arm** — checked BEFORE the registry (`:3006-3013`) via `is_reserved_source` (`daemon/constants.py:928-951`; prefixes `:904-922`: `system:`, `internal_agent:`, `internal_report:`, `internal_error_report:`, `internal_invoke_and_wait:`, `agent:`, `scheduler`, `cascade_resume`, …). Every failure mode fails closed.
- **Stamp site:** `InstanceManager.stamp_user_origin_window` — `daemon/manager.py:4773-4841`, called from the single dispatch funnel `_process_message_with_tracking` at `manager.py:9395-9402`. User-origin sources stamp `{source, message_id, stamped_at, expires_at}` into `_user_origin_windows[instance_id]` (init `manager.py:1507`); **any other source CLEARS** — strictly per-turn (`:4805-4809`).
- **TTL:** `NONCE_TTL_S = 60min` (`upgrade_journal.py:135`, ADR-036), applied at `manager.py:4823-4827`.
- **Verify site (upgrade gate, factor 2):** `upgrade_tools.py:2781-2830` — window present ∧ `expires_at` parses ∧ unexpired (fail-closed on unparseable) → binds `confirmed_source`/`confirmed_msg_id`.
- **`source` is daemon-written everywhere** (spoof-relevant): HTTP chat hardcodes `source="api"`; chat adapters build `source=f"{source_id}:{external_user_id}"`; agent-tool sends hardcode `internal_agent:{caller}` (`daemon/tools/instance.py:3989,4096,4200,1187`); job-queue lane `agent:{caller_agent_id}` (`daemon/tools/job_queue.py:2006,1265`); `send_message` exposes **no** source param (`instance.py:3564-3593`). Only two daemon sites write `additional_kwargs["source"]` (`daemon/services/instance_messaging.py:395-419`, `daemon/graph.py:8406-8412`).

### 2.2 Dispatch-chain verdict (the mandatory question)

**The primitive is DIRECT-USER-ONLY, not chain-aware.** Proof: user → ari (`source="api"`, window stamped) → ari `send_message` → maintenancer: the child's turn dispatches with `source="internal_agent:<ari_iid>"` (`instance.py:4200`), the funnel classifies it reserved, and the window is **cleared** (`manager.py:4829-4831`). `classify_user_origin` takes no ancestor argument; no code path consults the caller's window at enqueue or claim. Consequence: the delegation scenario — exactly the `worker_pool_size` case — cannot pass an upgrade-style factor-2 check today ("no stamp recorded this turn", `upgrade_tools.py:2808-2817`).

### 2.3 Options compared

| | (A) Gate-time ancestry walk | **(B) System-minted inherited stamp at dispatch ← RECOMMENDED** | (C) Capability token |
|---|---|---|---|
| Mechanism | At gate, parse `internal_agent:<caller>` → read ancestor's last stamp → recurse up | At the stamp funnel, on `internal_agent:<caller>`, if caller holds a live direct-or-inherited entry, write a target entry in a **separate store** | Mint single-use token on user-origin turns; agent presents it |
| Cost | N reads/gate; fragile (stamp dict diagnostic-grade; races ancestor turn-end; terminal/revive ancestors indistinguishable) | O(1) per dispatch; one dict read at gate | Highest: new lifecycle, LLM-transit bearer-token leak risk (shape already rejected by `auto-restart-upgrade/decisions.md:207` OQ9) |
| Upgrade-gate isolation | Read-only, safe | **Safe because the store and reader are disjoint** (§2.6) | New auth surface — violates constraint |

### 2.4 Recommended mechanism (B) — specification

- **New in-memory store** `manager._inherited_provenance_windows` (init adjacent to `manager.py:1507`), written ONLY by the dispatch funnel (`manager.py:9395-9402`, no signature change): when `source.startswith("internal_agent:")`, parse `<caller_iid>`; **inherit iff the caller's direct window (`_user_origin_windows[caller]`) or inherited entry is live at claim time**. Entry: `{root_source, root_message_id, depth, expires_at}`.
- **Everything else clears it** — `system:*`, `agent:*`, `source=None`, any non-inheriting source — same per-turn semantics as `manager.py:4829-4831`. `_user_origin_windows` write-set stays byte-identical.
- **Freshness (monotonic decay):** `expires_at = min(parent_expiry, now + 60min)`; **depth cap 4** (beyond it, refuse with guidance "ask the user to message the executor instance directly"); cleared on the target's next non-inheriting dispatch; wiped by daemon restart (in-memory ⇒ refusal degrades fail-closed exactly like `upgrade_tools.py:2810-2816`). NOT wall-clock-only, NOT instance-lifetime: a terminal→RUNNING revive is a fresh dispatch stamped by ITS own source — stale stamps cannot ride revives.
- **Strict live-parent inheritance** at claim time. Known tail: the parent's own window clears on the parent's *next non-user dispatch*, not at parent turn end — so a child claimed minutes later may still inherit within the parent's ≤60min TTL. Accepted: bounded by TTL + depth + budget rails; the alternative (clearing at parent turn end) risks false refusals in fast worker-pool handoffs.
- **Gate predicate:** `restart_allowed = provenance_ok(turn) ∧ env-self-match (D-FA2.4 order, unchanged) ∧ pipeline rails (§4a) ∧ anti-flap rails (§4b) ∧ reason non-empty (§4c)` where `provenance_ok = direct window live ∨ inherited record live`. Reuses `classify_user_origin` (pure function) and the TTL parse helpers (`upgrade_journal.py:170-190`) — **never** the upgrade gate's store.
- **Disclosure (policy line for vector g):** provenance is an **authorization-to-act channel, NOT intent-verification**. The arm receipt and journal record name the **root source + root message id + depth** so the user sees what was armed in their name; intent is covered by the journaled reason, end-of-turn execution, health-gated report, and budget rails.

### 2.5 Anti-spoof vector table (an agent must NOT be able to self-forge user provenance)

| Vector | Verdict | Evidence + mitigation |
|---|---|---|
| (a) Agent-authored content mimicking a user message | **BLOCKED (structural)** | Classification reads the queue row's daemon-written `source`, never content; `send_message` has no source param (`instance.py:3564-3593`); only two daemon sites write `additional_kwargs["source"]` (`instance_messaging.py:395-419`, `graph.py:8406-8412`). Rule: never add content-based origin detection. |
| (b) Self-send / sibling laundering | **BLOCKED (closure + decay)** | An inherited stamp exists only if the caller's own turn carries live direct-or-inherited provenance; pure-agent chains have no root ⇒ never arm. Depth cap 4 + monotonic TTL bound residual cycle replay. |
| (c) Replay of old user turns / revive | **BLOCKED** | Window is per-dispatch in-memory (`:1507`, `:4805-4809`); restart wipes; revive = fresh dispatch stamped by its own source; stamp binds to the stamping dispatch's `message_id` only. |
| (d) `system:*` / job-queue turns claiming provenance | **BLOCKED** | Reserved prefixes fail classification before registry lookup (`:3006-3013`); inherit lane keys strictly on `internal_agent:<caller>` + live parent entry. |
| (e) Cross-session / cross-project injection | **BLOCKED** | Store keyed by instance UUID, in-process only; no HTTP surface writes windows; single-host trust model as ratified (ADR-015 R10). |
| (f) Chat-source users (telegram/slack/discord/whatsapp) | **ACCEPTED BY DESIGN** | They classify user-origin via registry `source_type`; source value is daemon-built from adapter + `external_user_id` — not spoofable by message text. Same trust set the ratified upgrade factor-2 already trusts. |
| (g) Prompt-injection inside a genuine user message | **MITIGATED BY DISCLOSURE, NOT BLOCKED** | The turn IS user-originated. Gate passes; §2.4 disclosure + rails cover intent. This is the documented residual. |
| (h) 🟡 Hardening finding | **FOLD IN** | `_auto_dispatch_first_turn` falls back to `source="api"` when `caller_instance_id is None` (`instance.py:952-955`) — one refactor away from minting a DIRECT user window from an agent path. **Required change:** fallback becomes a reserved internal source. |

### 2.6 Upgrade-gate isolation argument

The upgrade gate reads only `_user_origin_windows` (`upgrade_tools.py:2783-2784`) and factor 3 binds the nonce to the direct-window `confirmed_msg_id`'s MessageQueue row (`:2906-2919`). Option B adds a **disjoint store** + a **disjoint reader** (new helper called only from the restart gate site); `classify_user_origin` is pure. No upgrade call site can observe the change. Tests pin this (§8, case T0).

---

## §3 Deferred Execution Choreography (ordering guarantee)

### 3.1 Reused machinery (the promote lane — do not invent a second path)

Arm: tool → pipeline-lock acquire (`upgrade_tools.py:2433` → `upgrade_journal.py:675`, mkdir-atomic `rollback.lock.d`) → durable `in_flight` + `pending_op` writes (`:2470`, `:2475`; `PendingOp.kind` already carries `"restart"` at `upgrade_journal.py:793`) → arm-notify wake write in the SAME journal envelope (`:2481` → `:1308` → `:986`) → in-memory execution marker (`:2513` → `manager.py:4837`) → return `RESTART SCHEDULED`.
Execution: turn ends → post-graph `finally` drain seam (`instance_messaging.py:5216`, shielded `:1983`, one-shot marker pop `manager.py:4910`) → spawn-refusal guard (op absent or `run_id` mismatch ⇒ no spawn + `executor_orphaned` event, `manager.py:4972`) → daemonized executor `systemd-run [--user] --unit=<prefix>-<run_id> --wait --collect` (`upgrade_journal.py:2267`, prefix hardcoded `ensemble-upgrade-` at `:1900`; systemd unavailable ⇒ loud `ExecutorSystemdUnavailable`, never silent fallback) → `scripts/upgrade/restart.sh` (preflight `:119`; **lock adoption** with run_id match `:160`; supervision preflight `:192`; grace `:204-212`; stop `:216`; start `:223`; gate `:236`; journal finalize `:258`).
Boot: reconcile (`daemon/api.py:2439`) → wake sweep (`:2450`, def `daemon/services/upgrade_journal_sweep.py:1063`, runs BEFORE auto-continue `:2477-2492`) → arm-notify delivered (`upgrade_journal_sweep.py:1300`, coalesce cap 16, abandon grace 600s, ari front-door fallback) → auto-continue re-dispatches interrupted turns (boot orphan sweep, v0.25.3).

### 3.2 Ordering guarantee — precise statement

1. **HARD (today):** the stop can never precede the turn's completion — the executor spawns only from the turn's own post-graph `finally` (`instance_messaging.py:5216`), after the graph ended and `_graph_tasks` popped; marker is one-shot.
2. **SOFT (today, gap):** the *reply* dispatches AFTER the drain (`instance_messaging.py:5231→:5244`) — the executor already runs while the reply goes out; the stop is held off only by `GRACE_S` (default **5s**, `restart.sh:79`) + the daemon's `wait_inflight` grace (10s, `manager.py:13716`). An adapter dispatch exceeding the grace loses the reply (arm-notify remains as backstop report).
   **Adjudication:** restart-lane grace default **5s → 15s** and keep `--grace-s` exposed (`restart.sh:71`). The structurally stronger fix (drain moved below the deferred-final-message dispatch at `:5231`) touches the shared `system_upgrade` path — **deferred to the improve lane**, recorded here as future hardening.

### 3.3 Restart-flavor deltas vs promote

| Delta | Decision |
|---|---|
| No version swap / no `atomic_flip` / no symlink change / no migration ops | Version-verify becomes an **identity** assertion (the binary answering is the one expected), not a drift detector. |
| No rollback lane | A failed restart has no automatic recovery: recovery = launcher burst/backoff supervision + operator escape hatch (§4e). The escape-hatch line MUST appear in the arm-notify body. |
| Health gate | Report-only, `/livez` (180s budget) **+ `/readyz` (120s budget, NEW for this lane — today `restart.sh:236` gates livez only)**, config `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`. Gate result lands in three places: arm-notify body; new journal terminal event **`restart_gate`** (success/fail — required so the wake terminal matcher `upgrade_journal_sweep.py:1209` can fire on a FAILED gate, not only success); `data/upgrade.log` + daemon log. |
| Graceful stop | Unchanged SINGLE-TERM contract (`lib.sh:3570` → `scripts/stop-ensemble.sh`); TERM→KILL escalation bounded (`WAIT_S=70`, `stop-ensemble.sh:496-497`; unit kill grace 10s). A forced KILL voids the in-flight turn's checkpoint write ⇒ journal **`stop_forced_kill`** (NEW event) so the operator knows. |
| Transient unit name | **Parameterize the prefix**: `PendingOp`/marker spec gains `unit_prefix` (default `ensemble-upgrade-` unchanged for promote); restart passes **`ensemble-restart-<run_id>`**. One derivation path, parameterized — collision-class isolation from promote units + operator legibility in `systemctl --user list-units`. |
| Stale arms | Boot reconcile explicitly closes an unexecuted `kind=restart` `pending_op` (no `in_flight`, no terminal event) with journal **`restart_abandoned`** (NEW event) + wake-abandonment notify — replacing today's silent 1800s expiry (`PENDING_OP_EXPIRE_RESTART_S`, `upgrade_journal.py:136`; periodic-tick skip at `:1690` stays, this is a boot-lane-only change). Never auto-execute a stale arm — a restart armed before a crash is stale intent that would fight recovery. |
| Double-arm | The journal lock IS the arm-level mutex (second arm ⇒ `pipeline-busy`). Belt-and-braces: the tool refuses if the marker map already holds an entry for the same instance. Backstop stays: same-instance marker overwrite + `run_id` mismatch ⇒ `executor_orphaned` (`manager.py:4972`). |

New journal event classes `restart_gate`, `restart_abandoned`, `stop_forced_kill` must be added to `_TERMINAL_EVENTS`/history classes in **both** `lib.sh` and `upgrade_journal.py` (shell twin + Python twin must match for the wake matcher).

---

## §4 Safety Rails (each justified, each configurable where a knob is safe)

| Rail | Mechanism | Justification | Default | Config | Refusal token |
|---|---|---|---|---|---|
| (a) Pipeline-in-flight | Check in order: journal `in_flight` → `pending_op` → `rollback.lock.d` held → `.adoption_in_progress` marker → `pipeline_settled` (mirrors `upgrade_tools.py:2331-2400` + settle contract) | All five are the promote/adoption interlock; a restart concurrent with a promote could kill the promote mid-adopt | all 5 blocking | **no knob** (a knob weakens the interlock) | `pipeline-busy` (existing, `:2366/:2376/:2384/:2441`) + new `adoption-in-progress`, `pipeline-not-settled` |
| (b1) Minimum uptime | `now() − get_boot_epoch()` (`daemon/services/boot_epoch.py:70`) ≥ threshold; **fail CLOSED when boot epoch unavailable** | A restart right after boot re-enters the boot-storm window and burns the crash budget; 600s reuses the launcher's own fresh-instance horizon (`launcher.sh:59` `BUDGET_UPTIME_RESET_S=600`) so the two agree | **600s** | `ENSEMBLE_RESTART_MIN_UPTIME_S` | `restart-too-soon` |
| (b2) Restart budget | Sliding-window counter **in the journal file** (`restart_window_count`, mirroring `rollback_window_count` shape `upgrade_journal.py:1574-1597`) | The counter must survive the restart it meters; in-memory resets on every restart (the exact failure it prevents), PG may be mid-restart | **3 / 24h** (matches the proven `ROLLBACK_CAP_24H=3`, `lib.sh:171` — the project's own "human must look" number) | `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400` | `restart-budget-exceeded` |
| (c) Reason required | `reason` becomes `str = ""` (today a required positional, `upgrade_tools.py:2298`); empty/whitespace ⇒ typed refusal; persisted in `PendingOp.reason` + echoed into journal `restart` event + arm-notify | Every restart is an operator-visible discontinuity; the reason is the only post-hoc explanation and is already a first-class `PendingOp` field | mandatory (min 3 chars) | — | `reason-required` |
| (d) In-flight work | Graceful SINGLE-TERM stop (bounded 70s) + LangGraph checkpoint freeze + **boot orphan sweep v0.25.3** (`daemon/api.py:2477-2492` → `auto_continue_boot_pass.py`; StaleTaskRecovery backstop boot+10min, `daemon/config.py:1271`) | **STATED DEPENDENCY:** resuming killed turns is only safe because checkpoints persist and the boot pass re-dispatches them (task-less RUNNING + healthy checkpoint + active JobItem). **Explicitly NOT protected:** mid-tool-call side effects outside checkpoints (DB/file/git/HTTP mutations persist and may repeat on re-dispatch), bash `setsid` orphans / crash-recovery leaks, background processes (proc cleanup is best-effort at shutdown, `manager.py:13688`; the auto-kill-bg-processes plan scopes root-instance completion, NOT daemon restart), non-checkpointed external state. Known gaps — documented, not papered over. | on (kill-switch `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` exists) | existing | advisory `busy-check` (never blocking) |
| (e) Post-boot health gate | Report-only `/livez` + `/readyz`; failure ⇒ `restart_gate` event + notify + logs; **no auto-rollback** | Same version — nothing to roll back to; auto-rollback would flap a healthy-ish daemon (worse). Operator escape hatch (in the notify body): `systemctl --user restart ensemble-main.service` (authoritative on the unit-managed host) or re-run `bash scripts/upgrade/restart.sh <env> --run-id <r-…>` (idempotent-by-run_id, `restart.sh:139-175`); `rollback.sh` only if a release change was involved | report-only | `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1` | `restart-gate-failed` (notify/journal, not a refusal) |

Also reused unchanged: restart-under-burst-abort (`upgrade_tools.py:1551`, crash budget >5 in 600s) — a launcher under burst-abort refuses new arms.

---

## §5 Boot Arm-Notify (incl. applied-overrides enumeration)

### 5.1 Mechanism (reused, fail-open)

Wake record at arm (`upgrade_journal.py:864-960`, `arm_pending_wake` `:986`; kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY`, default ON); boot sweep `sweep_wake_records()` (`daemon/api.py:2450` → `upgrade_journal_sweep.py:1063`) runs BEFORE auto-continue; delivery `enqueue_message(arming_instance_id, priority=2, metadata.system_context.kind="post_restart_arm_notify")` (`:1300`); coalesce cap 16; abandon grace 600s; ari front-door fallback. **Notify failure never blocks boot** — the sweep is wrapped per-wake, sweep-level, and at the lifespan envelope (`api.py:2453-2459`); failures journal into the `arm_notify_*` event family. Content assembly in `_format_wake_body` (`:547`) / `_format_coalesced_wake_body` (`:574`), reading the `restart_audits` record (§6) + `get_last_applied_resolution()`.

### 5.2 Applied-overrides enumeration

- **Where overrides live:** one row per managed key in `app_settings` (`daemon/models/app_settings.py:70`). **How boot applies them:** `apply_db_settings_overrides` (`daemon/services/settings_service.py:807`) called ONCE from lifespan (`daemon/api.py:510`) between `load_config()` and `InstanceManager(...)`; resolves each key through the SSOT ladder **DB > env > yaml > default** (`resolve()` `:462`) and mutates the live `Config` in place (`:889-893`). `worker_pool_size` takes effect because `PoolOrchestrator.setup` reads it once at boot (`daemon/services/pool_orchestrator.py:206`). All 6 current managed keys carry `requires_restart=True` (`settings_service.py:155-320`).
- **Arm-time pending set** = keys where `requires_restart=True` AND running value ≠ DB override value — exactly the existing `divergent` flag (`settings_tools.py:358`; `daemon/routers/settings.py:898-928`). Captured **inside the lock-holding try** (same discipline as `_arm_pending_wake_for_op`, `upgrade_tools.py:1339`) and persisted into `restart_audits[run_id].overrides` in the SAME `journal_write` envelope.
- **Access seam (layering fix):** the arm needs the live settings tier but `SettingsService` is a router module-global (`daemon/routers/settings.py:143-154`) — **wire it onto the manager** at `api.py` (mirroring `set_project_repository`, `:2740-2742`); the tool reads `manager._settings_service`. No tool→router import.
- **Applied ⇔** post-boot live value == arm-time DB override value. **Failure IS detectable:** an invalid stored row makes the resolver skip the DB tier (`settings_service.py:511-556`) ⇒ post-boot `resolution["source"] != "db"` ⇒ render *not applied* with the winning tier.
- **Late writes** (override changed after arm, before exec) and **cleared** overrides: detected by diffing arm-time `db_value` vs the post-boot row; reported as `late-write` / `cleared` — never silently folded into the arm intent.
- **Fail-open hazard:** on a boot DB-read failure the resolver silently falls to lower tiers (`:846-857`) with `applied_count=0` — the notify MUST distinguish **"no overrides"** from **"resolution unavailable"**: render per-key `source=="db"` vs not; never a bare "0 applied".
- **Render format:**
  ```
  applied overrides (2):
    limits.worker_pool_size: 5→7 (applied)
    limits.chat_worker_pool_size: 7→9 (not applied — resolved tier=env)
  ```

### 5.3 Notify body contents (contract)

Restart outcome (`restart_gate` result) · version now live (identity check) · `/livez` + `/readyz` gate results · applied-override list (§5.2 render) · reason echo · escape-hatch line (§4e) · `run_id`.

---

## §6 Audit Journal

### 6.1 Decision: **(A) REUSE the upgrade journal** — no new table, no migration

**Corrected premise:** the "upgrade journal" is NOT a SQLModel table — it is a **file-backed JSON document** at `<install_dir>/releases/state.json` (`upgrade_journal.py:250-251`; atomic temp+fsync+`os.replace` envelope `:326-402`). The discriminator is `PendingOp.kind`, which **already** carries `"restart" | "promote"` (`:793`). Therefore the project rule "every new SQLModel table requires its own `.sql` migration" applies only to the REJECTED option (B).

Why (A) wins: (1) **no dual write** — the arm already writes `pending_op` + `pending_wakes` in one locked envelope (`upgrade_tools.py:2476-2484`, ADR-039); a SQL table adds a cross-store, non-transactional second write (torn-arm hazard). (2) **Zero migration cost; additive-only release delta** keeps v0.25.x `rollback_safe=true` (current count 105 `.sql` files — untouched). (3) Sweep/settle semantics already exist (`upgrade_journal_sweep.py:1063`, 90s tick `:75`, terminal gating `:394`, coalescing `:510`). (4) A new top-level key is invisible to `lib.sh`'s field-targeted splice (ADR-034; unknown fields carried through, `upgrade_journal.py:34-38`).

*(B) sketch retained for the record:* `2026102X_000001_create_restart_audits.sql` + SQLModel + manager late-import — only if cross-install querying is ever required; a journal is per-install-dir and per-env. **Not recommended.**

### 6.2 Additive schema: top-level `restart_audits` list

Added idempotently in `ensure_extensions` (`upgrade_journal.py:404-430`, next to `pending_wakes`), keyed by `run_id`, append-only parallel to `history`, **capped at 50 entries (drop-oldest)** to bound the whole-document RMW growth:

```jsonc
{ "run_id":"…", "env":"live", "kind":"restart",
  "reason":"apply worker_pool_size 5→7",
  "arming_instance_id":"…", "arming_agent_id":"maintenancer",
  "user_source":"discord:<user>", "user_message_id":"…", "provenance_depth":1,
  "armed_at":"ISO", "executed_at":"ISO|null",
  "outcome":"applied|halted|failed|unknown",
  "overrides":[{"key":"limits.worker_pool_size","live_before":5,"db_value":7,
                "status":"applied|not-applied|cleared|late-write"}],
  "notify":{"status":"delivered|abandoned|pending","at":"ISO","message_id":"…"} }
```

Arm writes the static fields + pending override set; boot-settle updates `executed_at`/`outcome`/`overrides[].status`/`notify` **in place** — same document, same atomic write. (Wake records themselves are structurally removed on delivery — removal IS their idempotency key, `:1138` — hence `notify` status lives here.) Budget counter `restart_window_count` (§4b2) is a sibling top-level key.

### 6.3 Query surface: extend `upgrade_status` (no new tool)

`upgrade_status` (`upgrade_tools.py:2078`, category `system_upgrade`, already the documented cross-death join surface and already in the maintenancer allow-list) gains `section: Literal["status","restarts"] = "status"`, reusing `run_id`/`tail`. Output (line-oriented, house style):

```
RESTART AUDIT — env=live  (n=3, showing 3)
when | who | reason | outcome | overrides
2026-10-20T01:30:00Z | maintenancer/inst-ab12 | apply worker_pool_size 5→7 | applied | limits.worker_pool_size: 5→7 (applied)
```

Filters: `run_id`, `outcome`, `since`, `tail` (1..100 clamp `:2093`). `release_info(section=journal)` remains the raw view. A new tool was rejected: it would duplicate the env gate/journal split/lock probes and require a registration + allow-list decision in a privileged category (`tests/unit/tools/test_upgrade_registration.py:106-120` pins the 8-entry default-deny set).

---

## §7 Tool Contract

### 7.1 Tool identity — **keep `system_restart`, lift the deny** (adjudicated)

`agents/maintenancer/meta.json:13` currently carries `"deny": [..., "system_restart"]` — deny-by-name strips the tool (`instance.py:6333`); **deny ≠ allow**, so lifting it is a policy edit, not an allow-list change (the `system_upgrade` category allow already covers it). Decision: **(a) lift the deny entry, keep the tool name.** A new name (`agent_restart`) would cost a new registration + `KNOWN_TOOL_NAMES`/`DYNAMIC_TOOL_NAMES` frozen-binary-universe churn + a docs fork, while rule.md must change under EITHER option (it teaches the old prohibition). Signature keeps `user_confirmed`/`nonce` present-but-ignored on the live lane (A3 schema stability). Arming agent = **maintenancer**.

### 7.2 Receipt (keep the byte-shape prefix — tests grep it)

```
RESTART SCHEDULED — run_id=<id> env=live mode=graceful-now reason="<reason>"
executes_after_turn: yes
notify: armed (post-restart auto-wake to this instance; kill-switch ENSEMBLE_POST_RESTART_ARM_NOTIFY)
overrides_pending: limits.worker_pool_size 5→7
journal: releases/state.json pending-op opened (kind=restart) audit=restart_audits/<run_id>
budget: restarts_24h=<n>/3 cooldown=none
provenance: root=<root_source> msg=<root_message_id> depth=<d>
trigger: post-turn-callback armed
busy-check: idle
```

New fields vs today (`:2529-2537`): `overrides_pending` (the motivating case — say what the restart is FOR), `budget`, `provenance` (§2.4 disclosure).

### 7.3 Typed refusals — house style, single writer

One writer only: `_refusal()` (`upgrade_tools.py:1425-1433`) ⇒ `Error: RESTART REFUSED — reason=<token>: <message>`; journaled as `refusal` (except the lock-busy `pipeline-busy` carve-out, `:1394-1400` — preserved). **Do NOT introduce a second `refused: <token>` vocabulary** (breaks the parser/`_reason_token` contract). Commission tokens map onto house tokens:

| Commission token | House token (canonical) | Status |
|---|---|---|
| `refused: pipeline-busy` | `pipeline-busy` (+ new `adoption-in-progress`, `pipeline-not-settled`) | exists (`:2366/2376/2384/2441`) + 2 new |
| `refused: cooldown` | **`cooldown-active`** (cross-tool consistency with `system_upgrade` `:2963`) | NEW check for restart (restart has no cooldown check today) |
| `refused: no-user-provenance` | `no-user-provenance` — message names the sub-cause (missing \| expired \| depth-capped) + observed-source diagnostic (reuse `:2790-2805` shape: "observed source 'X' classified 'reserved-internal'") | NEW |
| `refused: reason-required` | `reason-required` (via `reason: str = ""` default) | NEW |
| — | `restart-too-soon`, `restart-budget-exceeded` (§4b) | NEW |

`live-restart-refused` is REMOVED from the vocabulary block (`:2570-2576`) — superseded.

### 7.4 Docs surface (every file whose text must change)

| File | Change |
|---|---|
| `daemon/tools/upgrade_tools.py:2318-2327` | live outright-refusal string → provenance-gated path |
| `daemon/tools/upgrade_tools.py:2545-2585` (`_full_doc_`) | "LIVE: refused outright this initiative (reason=live-restart-refused — A2…)" → new contract (receipt fields, refusal tokens, provenance requirement) |
| `daemon/tools/upgrade_tools.py:11,112,131` | module docstring + checklist note |
| `agents/maintenancer/meta.json:13` | lift `system_restart` from `tools.deny` |
| `agents/maintenancer/rule.md:8,68` | "Never run system_restart / Never restart the daemon" → new policy (provenance-gated arm, rails, receipt disclosure) |
| `agents/maintenancer/tools_note.md:20,77` | deny-table row + "structurally excluded" prose |
| `agents/maintenancer/README.md:37,74` | "the user pulls the restart lever" + skill one-liner |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | Boundary / Contract / Focus-area 5 / report format — AND the seeded skill-bank row `faf6e86e…` (lineage `bank_clone`) must be updated too, else the bank keeps the old "live arms via 3-factor gate only" text |
| `agents/maintenancer/ROLLOUT.md:52,269,286,381,444` | deny-list references in the rollback recipe (optional) |
| `docs/runbooks/post-restart-arm-notify.md` §1 | "Live never records (invariant 8)" + ADR-044 paragraph — rewrite **restart-only**; the upgrade 3-factor gate is a DIFFERENT gate and must not be conflated |
| `docs/runbooks/upgrade-drills.md` | add the restart drill leg |

### 7.5 Seal re-pin (same-merge discipline)

`agents/maintenancer/rule.md` is the ONLY affected file that is seal-pinned: `tests/test_chart_image_delivery_e2e.py:1389` `SEALED_SHA_BASELINES["agents/maintenancer/rule.md"] = "386a6373…6737"` (tripwire `:1423`). Because rule.md changes in this commission, the seal re-pin is an **explicit adjudicated step in the SAME merge**: attribute the mismatch via `git log` last-touch, update the constant, run the seal test targeted, commit provenance-labeled. `tests/test_chart_image_delivery_audit.py` has no upgrade/maintenancer entries — untouched.

---

## §8 Test Plan (mapped to the 7 mandated cases + restart-sim)

Primary surfaces: `tests/unit/tools/test_upgrade_tools.py` (`TestSystemRestartMatrix:1928`, `TestLiveThreeFactorGate:2461`), `tests/unit/tools/test_post_restart_arm_notify_journal.py`, `tests/unit/services/test_post_restart_arm_notify_sweep.py` (`_format_wake_body`), `tests/job_queue/test_post_restart_arm_notify_{routing,edge_cases}.py`, `test/drills/post_restart_arm_notify_drill.sh` + `wake_drill_driver.py` + `tests/test_post_restart_arm_notify_drill_smoake.py` (D1–D6 today).

| # | Case | Approach |
|---|---|---|
| **T0** | **Upgrade-gate byte-identity pins** (precondition) | factor-2/3 behavior identical with inherited entries present in the NEW store; factor 3 still binds to direct-window `confirmed_msg_id` (`:2905-2929`) — a chain can never place a nonce in a direct user row |
| T0b | Spoof vectors (a)–(h) of §2.5 | structural pins: no source param; reserved-prefix classification; pure-agent chain never arms; revive = fresh stamp; depth cap; `instance.py:952-955` fallback hardened |
| 1 | genuine-user turn → scheduled | seed `_user_origin_windows[iid]` (fixture shape `TestLiveThreeFactorGate:2461`); assert receipt `RESTART SCHEDULED` + `run_id` + `overrides_pending:` + `provenance:` and NO `REFUSED`; inherited variant: seed caller window + dispatch with `internal_agent:<caller>` |
| 2 | agent-initiated (no user turn) → refused | no window ⇒ `reason=no-user-provenance` + observed-source diagnostic; assert ZERO journal mutation (mirror `test_live_refusals_never_mutate_pipeline:2796`) |
| 3 | promote in-flight → refused | reuse `test_pipeline_busy_armed:2374` shape (`PendingOp(kind="promote")` + `in_flight`) ⇒ `reason=pipeline-busy`; assert NO `refusal` history event (lock-busy carve-out); plus `adoption-in-progress` + `pipeline-not-settled` variants |
| 4 | cooldown/budget → refused | mirror `test_cooldown_active_armed_refused:2339` (`journal_update_field(install,"cooldown_until",…)`) ⇒ `cooldown-active`; NEW: `restart_window_count` at cap ⇒ `restart-budget-exceeded`; min-uptime ⇒ `restart-too-soon`; boot-epoch-unavailable ⇒ fail-closed refusal |
| 5 | post-restart notify contents (incl. applied overrides) | `_format_wake_body` unit: assert `limits.worker_pool_size: 5→7 (applied)` AND the `not applied — resolved tier=env` branch AND "resolution unavailable" ≠ "no overrides"; journal unit: arm-side `restart_audits` record + boot-settle in-place update; drill scenario **D7** + extend the D1–D6 PASS-line grep |
| 6 | deferred execution ordering | mirror `test_armed_demo_returns_scheduled_no_spawn:2396`: after return, `pending_op` armed, NO executor spawned, receipt `executes_after_turn: yes` |
| 7 | **REAL restart-simulation** | extend `test/drills/post_restart_arm_notify_drill.sh` (D1 IS arm(restart)+kill+restart+observe-wake; fake staged install tree, in-process, no DB/network) with a live-gate leg (provenance-seeded arm → real stop/start via the drill harness → wake with applied overrides + `restart_gate` event). Mirror promote sims `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py` + `tests/test_promote_cgroup_survivorship.sh`. ⛔ drill stays sandboxed — `mktemp` FAKE_HOME + escape guard (project rule: never resolve live-shaped HOME paths) |
| T8 | Rails extras | same-instance re-arm refused; stale-arm boot reconcile ⇒ `restart_abandoned` + abandonment notify; budget counter survives a simulated restart (journal-file persistence); `stop_forced_kill` event on KILL escalation |
| T9 | Expectation flips | `test_live_restart_refused_unconditional:1936`, `test_auto_resolved_live_refuses_restart_outright:1500`, `test_auto_resolved_live_still_requires_three_factor_gate:1397`, `tests/integration/test_maintenancer_upgrade_gate_refuses.py` (its docstring pins the 3-factor gate for UPGRADE — restart is a different gate; keep upgrade assertions intact) |

---

## §9 File-Level Implementation Breakdown (ordered, for the developer)

1. **`daemon/manager.py`** — init `_inherited_provenance_windows` (adjacent `:1507`); inherit lane in the stamp funnel (`:9395-9402`) extending `stamp_user_origin_window` (`:4773-4841`) — `_user_origin_windows` write-set stays byte-identical; wire `manager._settings_service` (set at `api.py` near `:2740-2742`).
2. **`daemon/tools/instance.py`** — `:952-955` `_auto_dispatch_first_turn` fallback `"api"` → reserved internal source (🟡 hardening, folded in).
3. **`daemon/tools/upgrade_journal.py`** — `restart_audits` + `restart_window_count` top-level keys via `ensure_extensions` (`:404-430`); new event classes `restart_gate` / `restart_abandoned` / `stop_forced_kill` (+ `_TERMINAL_EVENTS`); `unit_prefix` on PendingOp/spec + `spawn_executor` parameterization (`:1900`, `:2267`); boot-lane abandoned-closure for `kind=restart` (narrow change near `:1690`, boot only — periodic-tick skip stays).
4. **`scripts/upgrade/lib.sh`** — `_TERMINAL_EVENTS` shell twin; `restart_window_count` splice support (mirror `rollback_window_count` `:1367`).
5. **`scripts/upgrade/restart.sh`** — grace default 15s; add report-only `/readyz` gate (`lib.sh:4968`); write `restart_gate` terminal event on success AND failure; `stop_forced_kill` on KILL escalation; `unit_prefix` passthrough.
6. **`daemon/config.py`** — `ENSEMBLE_RESTART_MIN_UPTIME_S=600`, `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400`, `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`.
7. **`daemon/tools/upgrade_tools.py`** — replace live refusal (`:2305-2320`) with: provenance helper (reads BOTH stores, reuses `classify_user_origin` `:2992` + TTL helpers) → rails (pipeline 5-check, cooldown, min-uptime, budget, reason) → arm (settings override capture inside the lock-holding try via `manager._settings_service`) → extended receipt (`:2529-2537`); refusal tokens per §7.3; `_full_doc_` rewrite (`:2545-2585`); module docstring.
8. **`daemon/services/upgrade_journal_sweep.py`** — wake body: outcome, version-live identity, livez/readyz, applied-override list (§5.2 render incl. fail-open distinction), reason echo, escape hatch; fail-open preserved (3 wrap levels).
9. **`agents/maintenancer/meta.json`** — lift `system_restart` from `tools.deny` (`:13`).
10. **`agents/maintenancer/rule.md`** — rewrite restart policy (`:8`, `:68`); **same-merge seal re-pin** (`tests/test_chart_image_delivery_e2e.py:1389`, adjudicated step, provenance-labeled commit).
11. **Docs** — `tools_note.md`, `README.md`, `ROLLOUT.md` (optional), `skills-template/restart-upgrade-ops.md` + skill-bank row `faf6e86e…`, `docs/runbooks/post-restart-arm-notify.md` §1 (restart-only invariant-8 rewrite), `docs/runbooks/upgrade-drills.md` (restart drill leg).
12. **Tests** — per §8 (T0/T0b first, then 1–7, T8, T9; drill D7 last).

---

## §10 Risks & Mitigations

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | Spoofable provenance ⇒ autonomous live restarts | 🔴 | Mechanism B is daemon-minted only (disjoint store, funnel-only writer); structural classification (never content-based); monotonic TTL + depth cap + per-turn clear + restart-wipe (fail-closed); vector table §2.5 with pins T0/T0b; residual (g) documented as disclosure-mitigated |
| 2 | Provenance propagation weakens the UPGRADE nonce gate | 🔴 | Disjoint store + disjoint reader + pure classifier (§2.6); byte-identity pins (T0); nonce factor 3 binds to direct-window rows a chain can never write |
| 3 | Reply lost in the soft grace window (stop before adapter dispatch completes) | 🟡 | Grace 5s→15s (restart lane); arm-notify is the durable backstop report; dispatch-before-drain restructure recorded as improve-lane hardening (§3.2) |
| 4 | Failed restart has no rollback lane (daemon down) | 🟡 | Launcher burst/backoff supervision; operator escape hatch REQUIRED in notify body (§4e); `restart_gate` failure event fires the wake so the arming instance reports it; `systemctl --user restart ensemble-main.service` one-liner |
| 5 | Unprotected in-flight side effects (mid-tool mutations, bash/bg orphans) | 🟡 | Stated dependency + explicit gap list (§4d); `stop_forced_kill` journal event flags checkpoint-guarantee voiding; never claimed as protected |
| 6 | Journal whole-document RMW growth (`restart_audits` append-only) | 🟢 | Cap 50 drop-oldest; restart frequency is inherently low (budget 3/24h) |
| 7 | Budget/uptime rails fail open on missing data | 🟡 | Boot-epoch unavailable ⇒ fail CLOSED; budget counter in the journal file (survives the restart it meters), never in memory/PG |
| 8 | Docs drift / two gates conflated | 🟡 | §7.4 file list; invariant-8 rewritten restart-only; skill-bank row updated (not just the template); rule.md seal re-pin same-merge |
| 9 | Stage-only release riding a concurrent staging worktree (v0.25.4 bump already at latest HEAD) | 🟢 | Expect v0.25.5; additive-only delta (no migration) keeps rollback_safe; exact number resolved at stage time |
| 10 | Unverified residuals from analysis | 🟢 | (i) launcher `_journal_sweep` restart-branch end-to-end, (ii) effective `WAIT_S` derivation on the live host, (iii) sweep-tick wake-retention semantics, (iv) lock-still-held-at-re-arm assumption — all non-blocking; (iv) additionally guarded by the same-instance marker check (§3.3). Developer verifies during implementation |

---

## Appendix — Provenance of this document

- Worker reports: `architect-worker-restart-provenance` (security-design, instance `c5a12c21-f4f1-484e-8ecc-9801a0fd8964`), `architect-worker-restart-choreography` (resilience-design, `6bb6574b-3092-4443-b4df-b11d760961eb`), `architect-worker-restart-journal` (data-flow-design, `97b28adb-7bbb-4204-aabb-c99188460e06`). All three completed with skill loaded; architect verified the four load-bearing claims (meta.json deny `:13`, rule.md seal pin `:1389`, `journal_path()` file-backed JSON `:250-251`, receipt/reason signature `:2296-2300/:2529`) directly before writing.
- Adjudication log (architect decisions over worker open questions): depth cap 4 · strict claim-time inheritance (parent-tail accepted, TTL-bounded) · single `no-user-provenance` token with sub-causes · provenance disclosure fields in receipt+journal · `instance.py:952-955` hardening folded in · parameterized `unit_prefix` (`ensemble-restart-<run_id>`) · grace 15s now, dispatch-before-drain deferred · keep `system_restart` + lift deny · house-style refusal tokens · budget 3/24h separate journal counter, fail-closed uptime · boot-lane-only `restart_abandoned` closure · readyz report-only default-on · terminal-event twins in lib.sh + Python · same-instance re-arm tool guard · escape hatch = `ensemble-main.service` · extend `upgrade_status` (no new tool) · `restart_audits` cap 50 · two-gate non-conflation mandated.
