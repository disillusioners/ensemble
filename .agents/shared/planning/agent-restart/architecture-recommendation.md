# Architecture Recommendation — First-Class Agent RESTART Capability

- **Status:** DESIGN-ONLY (no implementation) — commission `agent-first-class-live-restart` · **Rev 2** (council revise-and-resubmit folded)
- **Date:** 2026-10-19 · **Supersedes:** Rev 1 @ commit `552c556a7` (same file; git history preserves v1)
- **Branch/worktree:** `feature/agent-restart` @ `/home/nea/ensemble-src-wt-agent-restart` (base `630cefebb`)
- **Release vehicle:** stage-only, rides next staged release (expected v0.25.5; exact number resolved at stage time). NO promote — user nonce ceremony owns that.
- **Method:** 3-worker fan-out (security/resilience/data-flow) → architect aggregation (rev 1) → governor council review (2 councilors, unanimous REJECTED → revise-and-resubmit; no re-architecture) → this revision folds C-1..C-3 (criticals), V-1..V-12 (advisories), V-13..V-15 (suggestions), and the contract-language items. All council anchors verified against code by the council.
- **Authoritative policy (user, verbatim):** "restart don't need my nonce, only my upgrade is need." ⇒ restart = user-triggered, NO nonce. Upgrade keeps its 3-factor nonce ceremony UNCHANGED.

**Rev-2 changelog:** §2.1 producer-list correction + §2.4 provenance_class capture + §2.5 row (i) wake-laundering + §2.6 two-layer isolation rewrite (C-2, V-10, contract language) · §3.2 reply-loss disclosure (V-9) · §3.3 live-guard env threading (C-1), terminal-event re-homing to WAKE_TERMINAL_EVENTS + rationale/writer corrections (C-3), unit_prefix 4-site contract (V-7), non-boot expiry residual (V-15) · §4 cooldown row + budget envelope/window fixes + JobItem boundary (V-1, V-2, V-3, V-14) · §5 resolution-source seam (V-8) · §6.2 reason charset + provenance/wake fields (V-11, V-15) · §7.2/7.3 receipt + token re-homing (V-3, V-9, C-1) · §7.4/7.5 ari docs, drills rewrite, pin-17 (V-4, V-5, V-6) · §8 nine would-go-green gaps closed + token/test-surface enumeration (V-12, V-13, contract language) · §9/§10 updated accordingly.

---

## §1 Policy Change + Lineage

### 1.1 What is superseded

| Superseded ruling | Where it lives | New status |
|---|---|---|
| **A2** — live `system_restart` refused outright, "refusal-tested only", no gate/override/dry-run exception | `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md:13,116,143`; enforced `daemon/tools/upgrade_tools.py:2305-2320` (refusal string `:2319`) | **SUPERSEDED for `system_restart` live-restart-refusal ONLY.** Live restart becomes allowed with NO nonce when the calling turn carries genuine-user provenance (§2). |
| **§3.1 per-env matrix** — actor tools free dev/demo/sandbox; live behind 3-factor gate AND refused-at-execution | `.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md:34,223` | Superseded for the restart lane: live restart gated by provenance + rails (§2, §4), not by nonce. |
| **D-FA2.4** env-self-match + live outright-refusal, marked UNCHANGED by the wake supersession | `.agents/shared/planning/post-restart-arm-notify/supersession-record.md:89` | Env-self-match check order **unchanged**; only the live-refusal arm of the ruling is superseded. |
| Banner "anything touching live is USER-GATED / explicit user-confirmed action" | `.agents/shared/planning/post-restart-arm-notify/decisions.md:3-9` | Restated for restart: live restart IS user-gated — the gate is **genuine-user provenance**, not a nonce echo. The user-confirmed *nature* of the action is preserved; the *mechanism* changes. |
| Maintenancer prohibition "Never run `system_restart`" / "Never restart the daemon" | `agents/maintenancer/rule.md:8,68`; enforced via `agents/maintenancer/meta.json:13` `tools.deny` | **SUPERSEDED** — maintenancer becomes the arming agent under the new gate (§7.1). Seal re-pin required (§7.5), **preserving audit pin-17** (§7.5, V-6). |

### 1.2 What is explicitly NOT changed

- **The upgrade 3-factor ceremony** (user_confirmed + genuine-user-turn + nonce; factors `self-restart-upgrade-phase2/architecture-recommendation.md:123-141`, enforced `upgrade_tools.py:2779-2945`). Restart reuses ONLY the factor-2 classifier through a disjoint store; the upgrade call sites keep byte-identical decision behavior (§2.6). The pre-existing **write-layer** exposure of upgrade factor-2 (wake re-delivery minting direct windows) is out of scope here and is being filed as a project critical note by the leader (referenced in §2.5 row (i), §2.6).
- **A3 schema stability** (`architecture-recommendation.md:116`): `system_restart` keeps `user_confirmed`/`nonce` params present-but-ignored on the live lane — the policy flip is a code path change, not a schema change.
- **`ENSEMBLE_UPGRADE_LIVE`** and the promote lane's verified-arm machinery: untouched, never reused by the restart lane (C-1 — the two-gate conflation the commission forbids).
- Non-live/dry-run paths behave as today.

### 1.3 Why the asymmetry flips (rationale to record with the lineage)

A restart is strictly less dangerous than a promote: same binary, no migration, no rollback surface, no `current` symlink change. The existing asymmetry (upgrade reachable via nonce ceremony, restart refused outright) forced manual `sudo systemctl restart ensemble-main.service` for the motivating case — `limits.worker_pool_size` 5→7 written by `settings_set` (`requires_restart: true, divergent: true`) but un-appliable by the agent. Policy now: provenance replaces the nonce for restart; rails (§4) replace "refuse outright".

---

## §2 Provenance Attestation Design

### 2.1 The existing primitive (verified — producer list corrected per V-10/C-2)

- **Classifier (single source of truth):** `classify_user_origin(source, registry_get)` — `daemon/tools/upgrade_journal.py:2992-3047`. Arms on exact `"api"` (HTTP chat lane) OR a registered adapter whose daemon-controlled `source_type ∈ {telegram, slack, discord, whatsapp}` (`daemon/sources/registry.py:1130,1144-1149`). **Reserved internal lanes never arm** — checked BEFORE the registry (`:3006-3013`) via `is_reserved_source` (`daemon/constants.py:928-951`; prefixes `:904-922`). Every failure mode fails closed.
- **Stamp machinery — exact shape (rev-2 corrected):** the *stamp call* has a single implementation (`InstanceManager.stamp_user_origin_window`, `daemon/manager.py:4773-4841`) but **TWO call sites** — the dispatch funnel `_process_message_with_tracking` (`manager.py:9395-9402`) and the wake-sweep re-stamp (`daemon/services/upgrade_journal_sweep.py:1305-1317`, TTL-resetting). The rev-1 "single dispatch funnel" claim was factually wrong. **All `"api"` producers that can reach a stamp:** (1) HTTP chat lane (`daemon/routers/messages.py:691`); (2) its resume fallback (`:427`); (3) `_auto_dispatch_first_turn` fallback when `caller_instance_id is None` (`daemon/tools/instance.py:952-955` — hardened by this commission, §2.5 row (h)); (4) **wake-delivery fallback `source = wake.source or "api"`** (`upgrade_journal_sweep.py:837-840`, `:1330-1333` — the C-2 laundering vector; fixed for restart-kind wakes in §2.5 row (i)).
- **TTL:** `NONCE_TTL_S = 60min` (`upgrade_journal.py:135`, ADR-036), applied at `manager.py:4823-4827`.
- **Verify site (upgrade gate, factor 2):** `upgrade_tools.py:2781-2830` — reads `_user_origin_windows` directly (`:2783-2784`); fail-closed on unparseable/expired.
- **`source` is daemon-written everywhere** (spoof-relevant): chat adapters build `source=f"{source_id}:{external_user_id}"`; agent-tool sends hardcode `internal_agent:{caller}` (`daemon/tools/instance.py:3989,4096,4200,1187`); job-queue lane `agent:{caller_agent_id}` (`daemon/tools/job_queue.py:2006,1265`); `send_message` exposes **no** source param (`instance.py:3564-3593`). `additional_kwargs["source"]` write sites known: `instance_messaging.py:395-419`, `graph.py:8406-8412` (exhaustive enumeration carried as a residual, §10).

### 2.2 Dispatch-chain verdict (the mandatory question)

**The primitive is DIRECT-USER-ONLY, not chain-aware.** Proof: user → ari (`source="api"`, window stamped) → ari `send_message` → maintenancer: the child's turn dispatches with `source="internal_agent:<ari_iid>"` (`instance.py:4200`), the funnel classifies it reserved, and the window is **cleared** (`manager.py:4829-4831`). `classify_user_origin` takes no ancestor argument; no code path consults the caller's window at enqueue or claim. Consequence: the delegation scenario — exactly the `worker_pool_size` case — cannot pass an upgrade-style factor-2 check today ("no stamp recorded this turn", `upgrade_tools.py:2808-2817`).

### 2.3 Options compared

| | (A) Gate-time ancestry walk | **(B) System-minted inherited stamp at dispatch ← RECOMMENDED** | (C) Capability token |
|---|---|---|---|
| Mechanism | At gate, parse `internal_agent:<caller>` → read ancestor's last stamp → recurse up | At the stamp funnel, on `internal_agent:<caller>`, if caller holds a live direct-or-inherited entry, write a target entry in a **separate store** | Mint single-use token on user-origin turns; agent presents it |
| Cost | N reads/gate; fragile (stamp dict diagnostic-grade; races ancestor turn-end; terminal/revive ancestors indistinguishable) | O(1) per dispatch; one dict read at gate | Highest: new lifecycle, LLM-transit bearer-token leak risk (shape already rejected by `auto-restart-upgrade/decisions.md:207` OQ9) |
| Upgrade-gate isolation | Read-only, safe | **Safe at the decision layer because store and reader are disjoint (§2.6); write-layer contamination fixed for restart-kind wakes (C-2)** | New auth surface — violates constraint |

### 2.4 Recommended mechanism (B) — specification

- **New in-memory store** `manager._inherited_provenance_windows` (init adjacent to `manager.py:1507`), written ONLY by the dispatch funnel (`manager.py:9395-9402`, no signature change): when `source.startswith("internal_agent:")`, parse `<caller_iid>`; **inherit iff the caller's direct window (`_user_origin_windows[caller]`) or inherited entry is live at claim time**. Entry: `{root_source, root_message_id, depth, expires_at}`.
- **Everything else clears it** — `system:*`, `agent:*`, `source=None`, any non-inheriting source — same per-turn semantics as `manager.py:4829-4831`. `_user_origin_windows` write-set stays byte-identical.
- **Freshness (monotonic decay):** `expires_at = min(parent_expiry, now + 60min)`; **depth cap 4** (beyond it, refuse with guidance "ask the user to message the executor instance directly"); cleared on the target's next non-inheriting dispatch; wiped by daemon restart (in-memory ⇒ refusal degrades fail-closed exactly like `upgrade_tools.py:2810-2816`). NOT wall-clock-only, NOT instance-lifetime: a terminal→RUNNING revive is a fresh dispatch stamped by ITS own source — stale stamps cannot ride revives.
- **Arm-time capture (C-2):** the gate records **which** check passed — `provenance_class ∈ {direct, inherited}` — plus root source/message-id/depth. `_capture_user_source` (`upgrade_tools.py:1244-1259`) currently reads only the direct store and returns `""` for inherited arms; it is extended to read BOTH stores and to stamp `provenance_class` onto the `PendingWake` (new fields: `provenance_class`, root source/message-id, depth). `restart_audits` records `provenance_kind` and the wake source (§6.2, V-15); the receipt's `provenance:` line carries the class (§7.2).
- **Strict live-parent inheritance** at claim time. Known tail: the parent's own window clears on the parent's *next non-user dispatch*, not at parent turn end — a child claimed minutes later may still inherit within the parent's ≤60min TTL. Accepted: bounded by TTL + depth + budget rails.
- **Gate predicate:** `restart_allowed = provenance_ok(turn) ∧ env-self-match (D-FA2.4 order, unchanged) ∧ pipeline rails (§4a) ∧ cooldown (§4) ∧ anti-flap rails (§4b) ∧ reason non-empty (§4c)` where `provenance_ok = direct window live ∨ inherited record live`. Reuses `classify_user_origin` (pure function) and the TTL parse helpers (`upgrade_journal.py:170-190`) — **never** the upgrade gate's store.
- **Policy line (vector g):** provenance is an **authorization-to-act channel, NOT intent-verification**. The arm receipt and journal record name root source + message id + depth + class so the user sees what was armed in their name; intent is covered by the journaled reason, end-of-turn execution, health-gated report, and budget rails.

### 2.5 Anti-spoof vector table (an agent must NOT be able to self-forge user provenance)

| Vector | Verdict | Evidence + mitigation |
|---|---|---|
| (a) Agent-authored content mimicking a user message | **BLOCKED (structural)** | Classification reads the queue row's daemon-written `source`, never content; `send_message` has no source param (`instance.py:3564-3593`). Rule: never add content-based origin detection. |
| (b) Self-send / sibling laundering | **BLOCKED (closure + decay)** | An inherited stamp exists only if the caller's own turn carries live direct-or-inherited provenance; pure-agent chains have no root ⇒ never arm. Depth cap 4 + monotonic TTL bound residual cycle replay. |
| (c) Replay of old user turns / revive | **BLOCKED** | Window is per-dispatch in-memory; restart wipes; revive = fresh dispatch stamped by its own source; stamp binds to the stamping dispatch's `message_id` only. |
| (d) `system:*` / job-queue turns claiming provenance | **BLOCKED** | Reserved prefixes fail classification before registry lookup; inherit lane keys strictly on `internal_agent:<caller>` + live parent entry. |
| (e) Cross-session / cross-project injection | **BLOCKED** | Store keyed by instance UUID, in-process only; no HTTP surface writes windows; single-host trust model as ratified (ADR-015 R10). |
| (f) Chat-source users (telegram/slack/discord/whatsapp) | **ACCEPTED BY DESIGN** | Daemon-built source values; same trust set the ratified upgrade factor-2 already trusts. |
| (g) Prompt-injection inside a genuine user message | **MITIGATED BY DISCLOSURE, NOT BLOCKED** | The turn IS user-originated. Gate passes; §2.4 disclosure + rails cover intent. Documented residual. |
| (h) 🟡 `"api"` auto-dispatch fallback | **FOLDED IN (hardening)** | `instance.py:952-955` fallback becomes a reserved internal source. Enumerated as `"api"` producer #3 in §2.1 (V-10); T0b asserts this path specifically. |
| **(i) 🔴→BLOCKED Wake-delivery laundering (C-2)** | **BLOCKED BY REV-2 FIXES** | Rev-1 chain (all five links verified by council): `_capture_user_source` (`upgrade_tools.py:1244-1259`) returns `""` for inherited arms → wake delivered with fallback `source="api"` (`upgrade_journal_sweep.py:837-840`, `:1330-1333`) → `"api"` is exact-match user-origin → funnel stamps a **DIRECT** 60-min window on the arming instance (`manager.py:4821-4827`) → and the sweep re-stamp (`:1305-1317`) is a SECOND `stamp_user_origin_window` call site re-stamping with TTL reset. Net: one user-rooted delegation (`user→ari→maintenancer`) launders inherited provenance into DIRECT provenance and re-mints it on every restart — the full 3/24h budget of autonomous restarts from a single user root. **Fixes:** (1) restart-kind wakes deliver with a RESERVED source `system:post_restart_arm_notify` — never the `"api"` fallback (scoped to restart-kind; upgrade-kind wake delivery untouched); (2) `PendingWake` carries `provenance_class` + root source/message-id/depth captured at arm time (§2.4); (3) the `:1305-1317` re-stamp is **DELETED for restart-kind wakes** (a post-restart follow-up turn carries NO user window — a new restart needs fresh user provenance; upgrade-kind re-stamp unchanged); (4) T0b pin: wake delivery never mints a user window, and a second restart armed on the wake turn must be refused `no-user-provenance` (this test FAILS against rev-1 — it is the regression proof); (5) §2.1 producer list corrected; (6) the SAME write reaches upgrade factor-2's direct-store read (`upgrade_tools.py:2781-2784`) — a **pre-existing** upgrade-lane exposure (today bounded only by factor-3 nonce-to-direct-row binding) that the leader is filing as a project critical note; referenced here, not fixed in this commission. |

### 2.6 Upgrade-gate isolation — two-layer statement (contract language adopted)

- **Decision layer (gate reads): AIRTIGHT, and kept so by rule.** The restart gate reads `_inherited_provenance_windows` + `_user_origin_windows` through a NEW helper; upgrade factor-2 keeps **explicit direct-store access** (`upgrade_tools.py:2783-2784`) and **NEVER calls the restart-side helper** (directional isolation rule — T0 pin). Factor 3 binds the nonce to the direct-window `confirmed_msg_id`'s MessageQueue row (`:2906-2919`) — a chain can never place a nonce in a direct user row.
- **Write layer (window minting): historically contaminated, now scoped.** Rev-1's implicit single-writer assumption was wrong (multiple `"api"` producers, second stamp call site). Post-rev-2: restart-kind wake delivery uses a reserved source and never re-stamps (§2.5 row (i)). The remaining write-layer exposure on the **upgrade** lane (upgrade-kind wake `"api"` fallback minting direct windows reachable by factor-2) is pre-existing, out of scope, and filed by the leader as a project critical note.

---

## §3 Deferred Execution Choreography (ordering guarantee)

### 3.1 Reused machinery (the promote lane — do not invent a second path)

Arm: tool → pipeline-lock acquire (`upgrade_tools.py:2433` → `upgrade_journal.py:675`, mkdir-atomic `rollback.lock.d`) → durable `in_flight` + `pending_op` writes (`:2470`, `:2475`; `PendingOp.kind` already carries `"restart"` at `upgrade_journal.py:793`) → arm-notify wake write in the SAME journal envelope (`:2481` → `:1308` → `:986`) → in-memory execution marker (`:2513` → `manager.py:4837`) → return `RESTART SCHEDULED`.
Execution: turn ends → post-graph `finally` drain seam (`instance_messaging.py:5216`, shielded `:1983`, one-shot marker pop `manager.py:4910`) → spawn-refusal guard (`manager.py:4972`) → daemonized executor `systemd-run [--user] --unit=<prefix>-<run_id> --wait --collect` (`upgrade_journal.py:2267`) → `scripts/upgrade/restart.sh` (preflight `:119`; **lock adoption** with run_id match `:160`; supervision preflight `:192`; grace `:204-212`; stop `:216`; start `:223`; gate `:236`; journal finalize `:258`).
Boot: reconcile (`daemon/api.py:2439`) → wake sweep (`:2450`, def `daemon/services/upgrade_journal_sweep.py:1063`, runs BEFORE auto-continue `:2477-2492`) → arm-notify delivered (`upgrade_journal_sweep.py:1300`, coalesce cap 16, abandon grace 600s, ari front-door fallback) → auto-continue re-dispatches interrupted turns (boot orphan sweep, v0.25.3).

### 3.2 Ordering guarantee — precise statement

1. **HARD (today):** the stop can never precede the turn's completion — the executor spawns only from the turn's own post-graph `finally` (`instance_messaging.py:5216`), after the graph ended and `_graph_tasks` popped; marker is one-shot.
2. **SOFT (today, gap — disclosed, V-9):** the *reply* dispatches AFTER the drain (`instance_messaging.py:5231→:5244`); the stop is held off only by `GRACE_S` + the daemon's `wait_inflight` grace (10s, `manager.py:13716`). **Grace is a head-start, not a bound** — a slow chat-adapter upload can still be cut by SIGTERM. Dispositions: restart-lane grace default **5s → 15s** (keep `--grace-s` exposed, `restart.sh:71`); the **receipt discloses** that reply delivery is best-effort and the arm-notify is the durable report (§7.2); cheap **TERM-while-dispatch-pending journal note** — the executor journals a note if the reply dispatch was still pending when TERM fired (operator-visible reply-loss signal); the structurally stronger drain-below-dispatch restructure stays in the **improve lane** (touches the shared `system_upgrade` path).

### 3.3 Restart-flavor deltas vs promote

| Delta | Decision |
|---|---|
| **Live guard env (C-1 — was DoA in rev 1)** | Rev-1 would die at `restart.sh:92`: the restart-kind executor argv is built with **no attestation extras** (`manager.py:4934-4940`; only promote calls `_uj._verified_arm_extras`, `:4967-4970`), `is_verified_arm` is promote-only (`upgrade_journal.py:1348-1363`), the sole source of `ENSEMBLE_UPGRADE_LIVE=1` is `:1366-1383`, the var is not in `EXECUTOR_ENV_ALLOWLIST` (ambient stripped, `:1782-1787`, module comment `:1336`), and the script guard exits **78** on live without it (`restart.sh:92` → `lib.sh:715-728`) — receipt issued, executor dead before preflight/lock adoption, `rollback.lock.d` wedged until owner-dead reaping, `pending_op` open to 1800s expiry. **Fix:** mint a RESTART-SCOPED `ENSEMBLE_RESTART_LIVE=1` on provenance-passed arms ONLY, threaded through the existing `extra_env` explicit-extra merge; add a restart-scoped acceptance branch in `restart.sh` while `require_live_guard` stays **byte-identical**; **NEVER reuse `ENSEMBLE_UPGRADE_LIVE`** (two-gate conflation). Negative test: restart lane never sets the upgrade var (T0c). Script-guard trip journals a typed **`live-restart-refused`** event (the retired tool token re-homed as the script-guard journal event class). |
| No version swap / no `atomic_flip` / no symlink change / no migration ops | Version-verify becomes an **identity** assertion, not a drift detector. |
| No rollback lane | A failed restart has no automatic recovery: recovery = launcher burst/backoff supervision + operator escape hatch (§4e), which MUST appear in the arm-notify body. |
| Health gate | Report-only `/livez` (180s) **+ `/readyz` (120s, NEW for this lane)**, config `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`. Result lands in: arm-notify body; journal event **`restart_gate`**; `data/upgrade.log` + daemon log. **Corrected rationale (C-3):** the existing `halt` event already fires the wake on failure (`restart.sh:262-271`) — `restart_gate` exists for explicit gate-outcome classification in the audit trail and notify body, NOT to make failure visible to the matcher. |
| **Terminal events (C-3 — re-homed)** | `restart_gate` / `restart_abandoned` / `stop_forced_kill` go into **`WAKE_TERMINAL_EVENTS`** (`upgrade_journal.py:1623` — the set the wake matcher actually reads; introduce as a concatenated `RESTART_TERMINAL_EVENTS` if cleaner), **NEVER into `_TERMINAL_EVENTS`** (`:1581-1591`, 9-member set consumed by the promote-lane `reconcile_pending_op` `:1677-1697`; `:1596-1616` carries an explicit comment that a restart event MUST NEVER close a promote op; `tests/unit/services/test_post_restart_arm_notify_sweep.py:844-869` hard-pins `"restart" not in _TERMINAL_EVENTS` with set equality). **Rev-1's "lib.sh shell twin" instruction is withdrawn — lib.sh has no such constant.** Writer anchor corrected: `stop_forced_kill` is written by `scripts/stop-ensemble.sh:485-501` (the TERM→KILL escalation site), not restart.sh. |
| Graceful stop | Unchanged SINGLE-TERM contract (`lib.sh:3570` → `stop-ensemble.sh`); KILL escalation bounded (`WAIT_S=70`; unit kill grace 10s). A forced KILL voids the in-flight turn's checkpoint write ⇒ `stop_forced_kill` event. |
| Transient unit name | Parameterized `unit_prefix` on `PendingOp`/marker spec — restart passes **`ensemble-restart-<run_id>`**. **V-7 contract:** `UNIT_NAME_PREFIX` is used at **4 sites** (`upgrade_journal.py:1900/:2116/:2197/:2362`); every site must accept the parameterized prefix, the reaper/boot-sweep unit-matching behavior for a non-`ensemble-upgrade-` prefix is **unverified** (carried as residual, §10) — enumerate the sites in the PR, define the matching contract (`ensemble-{upgrade,restart}-<run_id>` family), add a unit-name test. |
| Stale arms | Boot reconcile explicitly closes an unexecuted `kind=restart` `pending_op` (no `in_flight`, no terminal event) with journal **`restart_abandoned`** + wake-abandonment notify — replacing silent 1800s expiry (`PENDING_OP_EXPIRE_RESTART_S`, `upgrade_journal.py:136`; periodic-tick skip at `:1690` stays; boot lane only). **Residual (V-15):** non-boot paths keep the silent 1800s expiry — launcher behavior verified safe (`launcher.sh:859-860`). Never auto-execute a stale arm. |
| Double-arm | The journal lock IS the arm-level mutex (second arm ⇒ `pipeline-busy`). Belt-and-braces: the tool refuses if the marker map already holds an entry for the same instance. Backstop: marker overwrite + `run_id` mismatch ⇒ `executor_orphaned` (`manager.py:4972`). |

### 3.4 Executor environment contract (C-1 summary)

`EXECUTOR_ENV_ALLOWLIST` governs **ambient** env (restart lane inherits nothing live-ish by default); the **explicit `extra_env` merge** is the only sanctioned lane for attestation vars. Promote threads `ENSEMBLE_UPGRADE_LIVE` via `_verified_arm_extras`; restart threads `ENSEMBLE_RESTART_LIVE` the same way, minted only on provenance-passed arms, consumed only by the restart-scoped acceptance branch. The two vars stay lexically and semantically disjoint forever (negative test T0c + §10 risk 11).

---

## §4 Safety Rails (each justified, each configurable where a knob is safe)

| Rail | Mechanism | Justification | Default | Config | Refusal token |
|---|---|---|---|---|---|
| (a) Pipeline-in-flight | Check in order: journal `in_flight` → `pending_op` → `rollback.lock.d` held → `.adoption_in_progress` marker → `pipeline_settled` (mirrors `upgrade_tools.py:2331-2400`) | All five are the promote/adoption interlock; a restart concurrent with a promote could kill the promote mid-adopt | all 5 blocking | **no knob** | `pipeline-busy` (existing) + new `adoption-in-progress`, `pipeline-not-settled` |
| (b) Cooldown (**V-3 — now normative**) | Read journal `cooldown_until`; refuse while in the future; **fail CLOSED on unparseable value** (mirrors the upgrade-lane cooldown semantics, `upgrade_tools.py:2963`) | A cooldown left by a prior aborted/halted operation is an operator signal that the daemon was recently unstable; unparseable = untrusted = refuse | — (set by halt paths) | — | `cooldown-active` (cross-tool consistent; receipt renders the ACTUAL state, never hardcoded `none` — §7.2) |
| (c1) Minimum uptime | `now() − get_boot_epoch()` (`daemon/services/boot_epoch.py:70`) ≥ threshold; **fail CLOSED when boot epoch unavailable** | A restart right after boot re-enters the boot-storm window and burns the crash budget; 600s reuses the launcher's fresh-instance horizon (`launcher.sh:59`) | **600s** | `ENSEMBLE_RESTART_MIN_UPTIME_S` | `restart-too-soon` |
| (c2) Restart budget | **True sliding window** — timestamp list `restart_window.events[]` in the journal file; count = entries within `now − window`; prune on every read/write (**V-2: deliberately NOT the fixed `JOURNAL_EMPTY` 24h bucket**, `upgrade_journal.py:157`, which permits 2×N across a boundary — 6 restarts in <24h — defeating the anti-flap intent) | The counter must survive the restart it meters; in-memory resets on every restart; PG may be mid-restart (the arm-time PG read for overrides happens inside the pipeline lock — bounded, acceptable, noted per V-15) | **3 / 24h** (matches `ROLLBACK_CAP_24H=3` precedent) | `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400` | `restart-budget-exceeded` |
| **Budget envelope (V-1)** | The increment happens **inside the arm's lock-holding try, in the SAME journal envelope as `in_flight`+`pending_op`+wake, and FIRST** — a crash between increment and op-write over-counts (safe direction); a crash after op-write without increment under-counts (unsafe) so ordering is increment-first. **Abandoned arms consume budget; no refund** (an abandoned arm still proves restart intent was armed; refunds would open a flap window via arm-abandon cycles) | Durable-correctness core of the anti-flap rail | — | — | — |
| (d) Reason required | `reason` becomes `str = ""`; empty/whitespace ⇒ typed refusal; **persisted form is charset-constrained** (§6.2, V-11); echoed into journal + arm-notify | Every restart is an operator-visible discontinuity; the reason is the only post-hoc explanation | mandatory (min 3 chars) | — | `reason-required` |
| (e) In-flight work | Graceful SINGLE-TERM stop (bounded 70s) + checkpoint freeze + **boot orphan sweep v0.25.3** (`daemon/api.py:2477-2492` → `auto_continue_boot_pass.py`; StaleTaskRecovery backstop boot+10min, `daemon/config.py:1271`). **V-14 — the sweep is THREE-SIGNAL conditioned: task-less RUNNING ∧ healthy checkpoint ∧ ACTIVE JobItem. A bare `send_message` turn (no JobItem) is NOT re-dispatched.** This is the most load-bearing assumption of the rail — disclosed in the arm-notify/report ("turns without an active JobItem are not auto-resumed") and pinned by test (§8). **Explicitly NOT protected:** mid-tool-call side effects outside checkpoints; bash `setsid` orphans / crash-recovery leaks; background processes (proc cleanup best-effort at shutdown, `manager.py:13688`; auto-kill-bg-processes scopes root-instance completion, NOT daemon restart); non-checkpointed external state | Resuming killed turns is only safe because checkpoints persist AND the three-signal sweep re-dispatches them | on (existing kill-switch) | existing | advisory `busy-check` (never blocking) |
| (f) Post-boot health gate | Report-only `/livez` + `/readyz`; failure ⇒ `restart_gate` event + notify + logs; **no auto-rollback** | Same version — nothing to roll back to; auto-rollback would flap a healthy-ish daemon. Operator escape hatch (in the notify body): `systemctl --user restart ensemble-main.service` (authoritative on the unit-managed host) or idempotent re-run `bash scripts/upgrade/restart.sh <env> --run-id <r-…>` (`restart.sh:139-175`); `rollback.sh` only if a release change was involved | report-only | `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1` | `restart-gate-failed` (notify/journal, not a refusal) |

Also reused unchanged: restart-under-burst-abort (`upgrade_tools.py:1551`).

---

## §5 Boot Arm-Notify (incl. applied-overrides enumeration)

### 5.1 Mechanism (reused, fail-open — with C-2 source fix and V-8 seam)

Wake record at arm (`upgrade_journal.py:864-960`, `arm_pending_wake` `:986`; kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY`, default ON); boot sweep (`daemon/api.py:2450` → `upgrade_journal_sweep.py:1063`) runs BEFORE auto-continue; delivery `enqueue_message(arming_instance_id, priority=2, metadata.system_context.kind="post_restart_arm_notify")` (`:1300`); coalesce cap 16; abandon grace 600s; ari front-door fallback. **C-2:** restart-kind wakes deliver with source **`system:post_restart_arm_notify`** — the `"api"` fallback (`:837-840`, `:1330-1333`) never applies to them; the `:1305-1317` re-stamp is deleted for restart-kind wakes. **V-8:** `get_last_applied_resolution()` (`settings_service.py:426-440`) is a **module global, not a manager attr** — `_format_wake_body` (`:547`) / `_format_coalesced_wake_body` (`:574`) obtain it by direct module import of the settings-service getter (the sweep service is daemon-side; no router import), and MUST distinguish `None` (**resolution unavailable**) from a dict with zero divergent keys (**no overrides**) — never render a bare "0 applied". Notify failure never blocks boot (three wrap levels, `api.py:2453-2459`); failures journal into the `arm_notify_*` event family.

### 5.2 Applied-overrides enumeration

- **Where overrides live:** `app_settings` rows (`daemon/models/app_settings.py:70`). **How boot applies them:** `apply_db_settings_overrides` (`daemon/services/settings_service.py:807`) called ONCE from lifespan (`daemon/api.py:510`); SSOT ladder **DB > env > yaml > default** (`resolve()` `:462`); live `Config` mutated in place (`:889-893`). `worker_pool_size` takes effect at `PoolOrchestrator.setup` boot read (`daemon/services/pool_orchestrator.py:206`). All 6 managed keys carry `requires_restart=True` (`settings_service.py:155-320`).
- **Arm-time pending set** = `requires_restart=True` ∧ running value ≠ DB override value (the existing `divergent` flag, `settings_tools.py:358`; `daemon/routers/settings.py:898-928`). Captured **inside the lock-holding try** (discipline of `_arm_pending_wake_for_op`, `upgrade_tools.py:1339`) into `restart_audits[run_id].overrides` in the SAME `journal_write` envelope. (Bounded PG read inside the pipeline lock — accepted, V-15.)
- **Access seam:** `SettingsService` is a router module-global (`daemon/routers/settings.py:143-154`) — wire it onto the manager at `api.py` (mirroring `set_project_repository`, `:2740-2742`); the tool reads `manager._settings_service`. No tool→router import.
- **Applied ⇔** post-boot live value == arm-time DB override value. **Failure IS detectable:** an invalid stored row makes the resolver skip the DB tier (`settings_service.py:511-556`) ⇒ `resolution["source"] != "db"` ⇒ render *not applied* with the winning tier. **Late writes / cleared** overrides: diff arm-time `db_value` vs post-boot row ⇒ `late-write` / `cleared`, reported separately.
- **Render format:**
  ```
  applied overrides (2):
    limits.worker_pool_size: 5→7 (applied)
    limits.chat_worker_pool_size: 7→9 (not applied — resolved tier=env)
  ```

### 5.3 Notify body contents (contract)

Restart outcome (`restart_gate` result) · version now live (identity check) · `/livez` + `/readyz` gate results · applied-override list (§5.2 render) · reason echo · provenance class + root source (disclosure) · **in-flight-work disclosure** (V-14: three-signal JobItem boundary sentence) · escape-hatch line (§4f) · `run_id`.

---

## §6 Audit Journal

### 6.1 Decision: **(A) REUSE the upgrade journal** — no new table, no migration

**Corrected premise:** the "upgrade journal" is NOT a SQLModel table — it is a **file-backed JSON document** at `<install_dir>/releases/state.json` (`upgrade_journal.py:250-251`; atomic temp+fsync+`os.replace` envelope `:326-402`). The discriminator is `PendingOp.kind`, which **already** carries `"restart" | "promote"` (`:793`). The "every new SQLModel table requires its own `.sql` migration" rule applies only to the REJECTED option (B).

Why (A) wins: (1) **no dual write** — the arm writes `pending_op` + `pending_wakes` + budget increment in one locked envelope (`upgrade_tools.py:2476-2484`, ADR-039; V-1); a SQL table adds a cross-store, non-transactional second write. (2) **Zero migration cost; additive-only release delta** keeps v0.25.x `rollback_safe=true` (105 `.sql` files untouched). (3) Sweep/settle semantics exist (`upgrade_journal_sweep.py:1063`, 90s tick `:75`). (4) A new top-level key is invisible to `lib.sh`'s field-targeted splice (ADR-034; unknown fields carried, `upgrade_journal.py:34-38`).

*(B) sketch retained for the record:* `2026102X_000001_create_restart_audits.sql` + SQLModel + manager late-import — only if cross-install querying is ever required. **Not recommended.**

### 6.2 Additive schema: top-level `restart_audits` list + `restart_window`

Added idempotently in `ensure_extensions` (`upgrade_journal.py:404-430`), keyed by `run_id`, append-only parallel to `history`, **capped at 50 entries (drop-oldest)**:

```jsonc
{ "run_id":"…", "env":"live", "kind":"restart",
  "reason":"apply worker_pool_size 5→7",           // charset-constrained, see below
  "arming_instance_id":"…", "arming_agent_id":"maintenancer",
  "provenance_kind":"direct|inherited",             // V-15
  "user_source":"discord:<user>", "user_message_id":"…", "provenance_depth":1,
  "wake_source":"system:post_restart_arm_notify",   // V-15
  "armed_at":"ISO", "executed_at":"ISO|null",
  "outcome":"applied|halted|failed|unknown",
  "overrides":[{"key":"limits.worker_pool_size","live_before":5,"db_value":7,
                "status":"applied|not-applied|cleared|late-write"}],
  "notify":{"status":"delivered|abandoned|pending","at":"ISO","message_id":"…"} }
```

Sibling key `restart_window: {"events":["ISO", …]}` — the true sliding-window budget list (§4c2). Arm writes static fields + pending override set + **budget increment (FIRST, same envelope)**; boot-settle updates `executed_at`/`outcome`/`overrides[].status`/`notify` in place.

**Reason charset constraint (V-11):** ADR-034's last-occurrence splice + free agent prose can collide (a reason containing the splice sentinel/newlines could corrupt field-targeted journal updates). The **persisted** reason is constrained at the tool boundary: printable, no newlines, no splice-sentinel class, ≤200 chars; the conversation may carry fuller prose but the journal/wake/audit surfaces store the constrained form.

### 6.3 Query surface: extend `upgrade_status` (no new tool)

`upgrade_status` (`upgrade_tools.py:2078`, category `system_upgrade`, already the documented cross-death join surface and in the maintenancer allow-list) gains `section: Literal["status","restarts"] = "status"`, reusing `run_id`/`tail`:

```
RESTART AUDIT — env=live  (n=3, showing 3)
when | who | reason | outcome | overrides
2026-10-20T01:30:00Z | maintenancer/inst-ab12 | apply worker_pool_size 5→7 | applied | limits.worker_pool_size: 5→7 (applied)
```

Filters: `run_id`, `outcome`, `since`, `tail`. `release_info(section=journal)` remains the raw view. A new tool was rejected: registration + allow-list decision in a privileged category (`tests/unit/tools/test_upgrade_registration.py:106-120` pins the 8-entry default-deny set) + duplicated env gate/journal split/lock probes.

---

## §7 Tool Contract

### 7.1 Tool identity — **keep `system_restart`, lift the deny** (adjudicated)

`agents/maintenancer/meta.json:13` carries `"deny": [..., "system_restart"]` — deny-by-name strips the tool (`instance.py:6333`); **deny ≠ allow**, so lifting it is a policy edit, not an allow-list change (the `system_upgrade` category allow already covers it). Decision: lift the deny, keep the name. A new name costs registration + `KNOWN_TOOL_NAMES`/`DYNAMIC_TOOL_NAMES` churn + a docs fork, while rule.md changes under EITHER option. Signature keeps `user_confirmed`/`nonce` present-but-ignored (A3). Arming agent = **maintenancer** — but **the gate is provenance, not agent identity** (V-4): any allow-listed holder of the tool obeys the same provenance check; ari also holds `system_restart` (`agents/ari/meta.json:29`, no deny) and is governed identically.

### 7.2 Receipt (keep the byte-shape prefix — tests grep it)

```
RESTART SCHEDULED — run_id=<id> env=live mode=graceful-now reason="<reason>"
executes_after_turn: yes
reply_delivery: best-effort (head-start grace 15s; reply may be lost — post-restart arm-notify is the durable report)
notify: armed (post-restart auto-wake to this instance; kill-switch ENSEMBLE_POST_RESTART_ARM_NOTIFY)
overrides_pending: limits.worker_pool_size 5→7
journal: releases/state.json pending-op opened (kind=restart) audit=restart_audits/<run_id>
budget: restarts_24h=<n>/3 cooldown=<actual state, never hardcoded>
provenance: class=<direct|inherited> root=<root_source> msg=<root_message_id> depth=<d>
trigger: post-turn-callback armed
busy-check: idle
```

New vs today (`:2529-2537`): `overrides_pending`, `budget` (actual cooldown state — V-3), `provenance` (with class — C-2 disclosure), `reply_delivery` disclosure (V-9).

### 7.3 Typed refusals — house style, single writer

One writer only: `_refusal()` (`upgrade_tools.py:1425-1433`) ⇒ `Error: RESTART REFUSED — reason=<token>: <message>`; journaled as `refusal` (except the lock-busy `pipeline-busy` carve-out, `:1394-1400`). **No second `refused: <token>` vocabulary.**

| Commission token | House token (canonical) | Status |
|---|---|---|
| `refused: pipeline-busy` | `pipeline-busy` (+ new `adoption-in-progress`, `pipeline-not-settled`) | exists + 2 new |
| `refused: cooldown` | **`cooldown-active`** (cross-tool consistency, `:2963`) | NEW check for restart (§4 row b) |
| `refused: no-user-provenance` | `no-user-provenance` — message names the sub-cause (missing \| expired \| depth-capped) + observed-source diagnostic (reuse `:2790-2805` shape) | NEW |
| `refused: reason-required` | `reason-required` (via `reason: str = ""` default) | NEW |
| — | `restart-too-soon`, `restart-budget-exceeded` (§4c) | NEW |

**`live-restart-refused` is removed from the TOOL vocabulary block (`:2570-2576`) and re-homed as the script-guard JOURNAL event class** (C-1: typed event at `restart.sh`'s live guard when neither attestation var is present). `RESTART_TOKENS` test pin (`test_upgrade_tools.py:1537/3468-3480`) is extended with all 8 restart-lane tokens (V-13).

### 7.4 Docs surface (every file whose text must change)

| File | Change |
|---|---|
| `daemon/tools/upgrade_tools.py:2318-2327` | live outright-refusal string → provenance-gated path |
| `daemon/tools/upgrade_tools.py:2545-2585` (`_full_doc_`) | "LIVE: refused outright this initiative (reason=live-restart-refused — A2…)" → new contract (receipt fields, refusal tokens, provenance requirement, executor live-guard note) |
| `daemon/tools/upgrade_tools.py:11,112,131` | module docstring + checklist note |
| `agents/maintenancer/meta.json:13` | lift `system_restart` from `tools.deny` |
| `agents/maintenancer/rule.md:8,68` | restart policy rewrite — **must preserve audit pin-17 constraints** (`tests/test_chart_image_delivery_audit.py:467-483`; maintenancer is in `CHART_CAPABLE_AGENTS`, `:67`) |
| `agents/maintenancer/tools_note.md:20,77` | deny-table row + "structurally excluded" prose |
| `agents/maintenancer/README.md:37,74` | "the user pulls the restart lever" + skill one-liner |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | Boundary / Contract / Focus-area 5 / report format |
| `agents/ari/tools_note.md:421` (**V-4**) | still teaches the old "refused outright" contract — rewrite; state the gate is provenance, not agent identity |
| `agents/maintenancer/ROLLOUT.md:52,269,286,381,444` | deny-list references in the rollback recipe (optional) |
| `docs/runbooks/post-restart-arm-notify.md` §1 | "Live never records (invariant 8)" + ADR-044 paragraph — rewrite **restart-only**; the upgrade 3-factor gate is a DIFFERENT gate and must not be conflated |
| `docs/runbooks/upgrade-drills.md:468/535` (**V-5**) | still state live restart refused outright — **REWRITE these passages**, do not merely append a restart drill leg |

**V-12 caveat:** editing `agents/maintenancer/skills-template/restart-upgrade-ops.md` does NOT update the seeded skill-bank row (`faf6e86e…`, `bank_clone` lineage; seeding keys off `skill-set.yaml` via `skill_seed_service.py:325-352`) — the developer must confirm and execute the bank-row update path, and a T9 case must pin it (else the bank keeps teaching "live arms via 3-factor gate only").

### 7.5 Seal re-pin (same-merge discipline — V-6 corrected)

`agents/maintenancer/rule.md` is seal-pinned by `tests/test_chart_image_delivery_e2e.py:1389` (`SEALED_SHA_BASELINES`, tripwire `:1423`) ⇒ same-merge re-pin: attribute via `git log` last-touch, update the constant, run the seal test targeted, commit provenance-labeled. **Additionally (V-6):** `tests/test_chart_image_delivery_audit.py` is NOT "untouched" — its **pin 17** (`:467-483`) constrains `rule.md` content for chart-capable agents (maintenancer included, `:67`); the rule.md rewrite must preserve pin-17's constraints and a T9 case must run the audit test. Drop rev-1's "audit test untouched" wording.

---

## §8 Test Plan (mapped to the 7 mandated cases + restart-sim + the 9 would-go-green gaps)

Primary surfaces: `tests/unit/tools/test_upgrade_tools.py` (`TestSystemRestartMatrix:1928`, `TestLiveThreeFactorGate:2461`, `RESTART_TOKENS` pin `:1537/3468-3480`), `tests/unit/tools/test_post_restart_arm_notify_journal.py` (`:772` token refs), `tests/unit/services/test_post_restart_arm_notify_sweep.py` (`:844-869` terminal-set pin), `tests/job_queue/test_post_restart_arm_notify_{routing,edge_cases}.py`, `test/drills/post_restart_arm_notify_drill.sh` + `wake_drill_driver.py` (`:519` drill banner) + `tests/test_post_restart_arm_notify_drill_smoke.py`, `tests/mocks/upgrade_tools_live_safety_mock.py:516-519` (V-13).

**T0 — precondition pins (upgrade-gate + structural invariants; close gaps 2/3/9 of the would-go-green list):**
- (a) Upgrade factor-2/3 byte-identity with inherited entries present; **directional isolation**: factor-2 keeps direct-store access and never calls the restart-side helper; factor 3 still binds to direct-window rows.
- (b) **Terminal-event mutation guard:** new names present in `WAKE_TERMINAL_EVENTS` (or `RESTART_TERMINAL_EVENTS`), ABSENT from `_TERMINAL_EVENTS`, `len(_TERMINAL_EVENTS) == 9` — and end-to-end: **promote armed → restart event lands → promote `pending_op` NOT closed**.
- (c) **Executor env negative:** restart lane NEVER sets `ENSEMBLE_UPGRADE_LIVE`; `ENSEMBLE_RESTART_LIVE` minted only on provenance-passed arms (C-1).
- (d) **Wake-provenance side-effect:** restart-kind wake delivery (source `system:post_restart_arm_notify`) mints NO user window.

**T0b — spoof vectors §2.5 (a)–(i):** structural pins (no source param; reserved-prefix classification; pure-agent chain never arms; revive = fresh stamp; depth cap) + **row (h): the `instance.py:952-955` auto-dispatch fallback specifically** (V-10) + **row (i): wake delivery never mints a user window AND a second restart armed on the wake turn is refused `no-user-provenance`** (must FAIL against rev-1 — regression proof).

| # | Case | Approach |
|---|---|---|
| 1 | genuine-user turn → scheduled | seed `_user_origin_windows[iid]` (fixture `TestLiveThreeFactorGate:2461`); assert receipt `RESTART SCHEDULED` + `run_id` + `overrides_pending:` + `provenance: class=` and NO `REFUSED`; inherited variant: caller window + `internal_agent:<caller>` dispatch |
| 2 | agent-initiated → refused | no window ⇒ `reason=no-user-provenance` + observed-source diagnostic; ZERO journal mutation (mirror `:2796`) |
| 3 | promote in-flight → refused | reuse `test_pipeline_busy_armed:2374` shape ⇒ `pipeline-busy` (no `refusal` event, lock-busy carve-out); + `adoption-in-progress`, `pipeline-not-settled` variants |
| 4 | cooldown/budget → refused | cooldown: mirror `test_cooldown_active_armed_refused:2339` ⇒ `cooldown-active` (+ unparseable `cooldown_until` ⇒ fail-closed); budget: `restart_window.events` at cap ⇒ `restart-budget-exceeded`; **window-boundary case (V-2 gap 4): arms at T and T+window−ε both counted** (true sliding-window semantics); **crash-ordering + concurrent-arms cases (V-1 gap 5): increment-first over-count direction; two concurrent arm attempts ⇒ exactly one envelope, one refusal**; min-uptime ⇒ `restart-too-soon`; boot-epoch-unavailable ⇒ fail-closed |
| 5 | post-restart notify contents (incl. applied overrides) | `_format_wake_body` unit: `limits.worker_pool_size: 5→7 (applied)`; `not applied — resolved tier=env` branch; **`None` (unavailable) ≠ dict-with-zero (no overrides)** (V-8); journal unit: arm-side `restart_audits` record (incl. `provenance_kind`, `wake_source`) + boot-settle in-place update; **audit pin-17 preserved (V-6 gap 6)**; **seeded skill-bank row updated (V-12 gap 7)**; **JobItem boundary disclosure present (V-14 gap 8)**; drill scenario **D7** + extend the D1–D6 PASS-line grep |
| 6 | deferred execution ordering | mirror `test_armed_demo_returns_scheduled_no_spawn:2396`: after return, `pending_op` armed, NO executor spawned, receipt `executes_after_turn: yes` |
| 7 | **REAL restart-simulation** | extend `test/drills/post_restart_arm_notify_drill.sh` with a **fake-live executor-crossing leg (gap 1): provenance-seeded arm → `ENSEMBLE_RESTART_LIVE` threaded through the real executor env → script-guard acceptance → real stop/start via the drill harness → wake with applied overrides + `restart_gate` event** — this leg would have caught C-1. Mirror promote sims `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py` + `tests/test_promote_cgroup_survivorship.sh`. ⛔ sandboxed: `mktemp` FAKE_HOME + escape guard (never resolve live-shaped HOME paths) |
| T8 | Rails extras | same-instance re-arm refused; stale-arm boot reconcile ⇒ `restart_abandoned` + abandonment notify; budget counter survives a simulated restart (journal-file persistence); `stop_forced_kill` event from `stop-ensemble.sh:485-501`; unit-name test for the parameterized prefix (V-7) |
| T9 | Expectation flips + surface enumeration | `test_live_restart_refused_unconditional:1936`, `test_auto_resolved_live_refuses_restart_outright:1500`, `test_auto_resolved_live_still_requires_three_factor_gate:1397`, `tests/integration/test_maintenancer_upgrade_gate_refuses.py` (upgrade docstring intact); **V-13:** `tests/mocks/upgrade_tools_live_safety_mock.py:516-519`, `RESTART_TOKENS` extended with the 8 restart-lane tokens, `test_post_restart_arm_notify_journal.py:772`, drill banner `wake_drill_driver.py:519`; **V-4/V-5:** ari `tools_note.md:421` rewrite asserted; `upgrade-drills.md:468/535` rewrite (not append) asserted; **V-6:** audit test (pin 17) runs green post-rewrite |

---

## §9 File-Level Implementation Breakdown (ordered, for the developer)

1. **`daemon/manager.py`** — init `_inherited_provenance_windows` (adjacent `:1507`); inherit lane in the stamp funnel (`:9395-9402`) extending `stamp_user_origin_window` (`:4773-4841`) — `_user_origin_windows` write-set byte-identical; **restart-kind executor argv: mint `ENSEMBLE_RESTART_LIVE=1` via the `extra_env` explicit-extra merge on provenance-passed arms only** (`:4934-4940` region; promote extras `:4967-4970` untouched — C-1); wire `manager._settings_service` (set at `api.py` near `:2740-2742`).
2. **`daemon/tools/instance.py`** — `:952-955` `"api"` fallback → reserved internal source (🟡 hardening, folded).
3. **`daemon/tools/upgrade_journal.py`** — `restart_audits` + `restart_window` top-level keys via `ensure_extensions` (`:404-430`); `unit_prefix` on PendingOp/spec parameterized across **all 4 `UNIT_NAME_PREFIX` sites** (`:1900/:2116/:2197/:2362` — V-7); `WAKE_TERMINAL_EVENTS`/`RESTART_TERMINAL_EVENTS` additions (`restart_gate`, `restart_abandoned`, `stop_forced_kill`) — `_TERMINAL_EVENTS` untouched (C-3); boot-lane abandoned-closure for `kind=restart` (near `:1690`, boot only); `PendingWake` gains `provenance_class` + root fields (C-2).
4. **`scripts/upgrade/lib.sh`** — restart-scoped acceptance branch for `ENSEMBLE_RESTART_LIVE` at the live guard (`:715-728` consumer); `require_live_guard` byte-identical; `restart_window.events` splice support mirroring the rollback-counter discipline (ADR-034 field-targeted).
5. **`scripts/upgrade/restart.sh`** — grace default 15s; acceptance of `ENSEMBLE_RESTART_LIVE` at `:92` guard + typed `live-restart-refused` journal event on guard trip (C-1); report-only `/readyz` gate; `restart_gate` event on success AND failure (classification rationale, C-3); `unit_prefix` passthrough; TERM-while-dispatch-pending note (V-9).
6. **`scripts/stop-ensemble.sh`** — `stop_forced_kill` journal event at the `:485-501` escalation site (C-3 writer anchor).
7. **`daemon/config.py`** — `ENSEMBLE_RESTART_MIN_UPTIME_S=600`, `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400`, `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`.
8. **`daemon/tools/upgrade_tools.py`** — replace live refusal (`:2305-2320`) with: provenance helper (both stores, `classify_user_origin` `:2992` + TTL helpers) → rails (pipeline 5-check, cooldown fail-closed, min-uptime, budget increment-FIRST in-envelope, reason constraint) → arm (override capture inside lock via `manager._settings_service`; `_capture_user_source` `:1244-1259` extended to both stores + `provenance_class`) → extended receipt (`:2529-2537`); refusal tokens per §7.3; `_full_doc_` rewrite; module docstring.
9. **`daemon/services/upgrade_journal_sweep.py`** — restart-kind wake source `system:post_restart_arm_notify` (never `"api"` fallback); delete restart-kind re-stamp (`:1305-1317`); wake body per §5.3 (overrides via module-global `get_last_applied_resolution()` with None≠dict handling — V-8); fail-open preserved.
10. **`agents/maintenancer/meta.json`** — lift `system_restart` from `tools.deny` (`:13`).
11. **`agents/maintenancer/rule.md`** — policy rewrite (`:8`, `:68`) **preserving pin-17**; same-merge seal re-pin (`test_chart_image_delivery_e2e.py:1389`) + audit-test run (`test_chart_image_delivery_audit.py:467-483`).
12. **Docs** — `tools_note.md` (maintenancer + **ari `:421`**), `README.md`, `ROLLOUT.md` (optional), `skills-template/restart-upgrade-ops.md` **+ seeded skill-bank row update path** (`skill_seed_service.py:325-352` — V-12), `docs/runbooks/post-restart-arm-notify.md` §1, `docs/runbooks/upgrade-drills.md:468/535` **rewrite** (V-5).
13. **Tests** — per §8 (T0/T0b first, then 1–7 incl. the fake-live executor-crossing D7 leg, T8, T9).

---

## §10 Risks & Mitigations

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | Spoofable provenance ⇒ autonomous live restarts | 🔴 | Mechanism B daemon-minted only; structural classification; monotonic TTL + depth cap + per-turn clear + restart-wipe; vector table §2.5 incl. **row (i) wake-laundering closed**; pins T0/T0b |
| 2 | Provenance propagation weakens the UPGRADE nonce gate | 🔴 | §2.6 two-layer statement: decision layer airtight (directional isolation rule + T0a pin); write-layer restart-kind contamination fixed (C-2), upgrade-lane write exposure pre-existing → leader-filed critical note |
| 3 | **Executor live-guard DoA (C-1 class)** | 🔴→closed | Restart-scoped `ENSEMBLE_RESTART_LIVE` via `extra_env` explicit merge; script acceptance branch; negative test T0c; fake-live executor-crossing D7 leg |
| 4 | Terminal-event set mutation closes a promote op (C-3 class) | 🔴→closed | Events in `WAKE_TERMINAL_EVENTS` only; T0b mutation guard + e2e promote-not-closed test; `len(_TERMINAL_EVENTS)==9` pinned |
| 5 | Wake delivery re-mints direct provenance (C-2 class) | 🔴→closed | Reserved wake source; re-stamp deleted for restart-kind; T0d + T0b row-(i) regression proof |
| 6 | Reply lost in the soft grace window | 🟡 | Grace 15s (head-start, disclosed as such); receipt `reply_delivery` disclosure; TERM-while-dispatch-pending journal note; drain-reorder in improve lane |
| 7 | Failed restart has no rollback lane | 🟡 | Launcher supervision; escape hatch REQUIRED in notify body; `restart_gate` classification; `systemctl --user restart ensemble-main.service` one-liner |
| 8 | Unprotected in-flight side effects + **JobItem boundary** | 🟡 | §4e explicit gap list + three-signal sweep disclosure in notify/report (V-14) + test; `stop_forced_kill` event flags checkpoint-voiding |
| 9 | Rails fail open on missing data | 🟡 | Boot-epoch unavailable ⇒ fail closed; unparseable `cooldown_until` ⇒ fail closed; budget counter journal-file-durable, increment-first (V-1), true sliding window (V-2) |
| 10 | Journal RMW growth / splice collision | 🟢 | `restart_audits` cap 50; reason charset-constrained at tool boundary (V-11) |
| 11 | Two live-guard vars drift together | 🟡 | Lexical/semantic disjointness mandated (§3.4); T0c negative pin; any future executor-env change must preserve disjointness |
| 12 | `unit_prefix` reaper/boot-sweep matching under new prefix unverified | 🟡 | 4-site enumeration + matching contract (§3.3) + unit-name test; carried as residual until verified |
| 13 | Docs drift / two gates conflated | 🟡 | §7.4 file list incl. ari + drills REWRITE; skill-bank row update path (V-12) + T9; rule.md seal re-pin preserving pin-17 |
| 14 | Stage-only ride on concurrent staging worktree | 🟢 | Expect v0.25.5; additive-only delta keeps rollback_safe |
| 15 | **Carried residuals (honest list)** | 🟢 | Skill-bank row current DB text until V-12 path executed; live-host effective `WAIT_S` derivation; launcher `_journal_sweep` restart-branch e2e (`launcher.sh:859-860` verified safe for the silent-expiry residual); reaper unit-matching under `ensemble-restart-` prefix; exhaustive `additional_kwargs["source"]` write-site enumeration; non-boot paths keep silent 1800s expiry |

---

## Appendix — Provenance of this document

- **Rev 1:** worker reports `architect-worker-restart-provenance` (security-design, `c5a12c21-f4f1-484e-8ecc-9801a0fd8964`), `architect-worker-restart-choreography` (resilience-design, `6bb6574b-3092-4443-b4df-b11d760961eb`), `architect-worker-restart-journal` (data-flow-design, `97b28adb-7bbb-4204-aabb-c99188460e06`); architect verified meta.json deny `:13`, rule.md seal pin `:1389`, `journal_path()` `:250-251`, receipt/reason signature before writing. Committed `552c556a7`.
- **Rev 2:** governor council review (2 councilors, unanimous REJECTED — revise-and-resubmit, no re-architecture; anchors verified against code by the council). Folded: C-1 (executor live-guard DoA), C-2 (wake provenance laundering, 5-link chain), C-3 (terminal-event set isolation), V-1..V-12, V-13..V-15, contract language (§2.6 two-layer isolation, §8 nine would-go-green gaps closed).
- **Rev-2 adjudication log:** C-1 restart-scoped var via `extra_env` merge, script-guard event re-homes the retired token · C-2(3) re-stamp DELETED for restart-kind wakes (upgrade-kind unchanged; post-restart follow-up needs fresh provenance) · C-3 events in `WAKE_TERMINAL_EVENTS` (concatenated `RESTART_TERMINAL_EVENTS` allowed), lib.sh-twin claim withdrawn, `restart_gate` rationale corrected to audit classification, `stop_forced_kill` writer re-anchored to `stop-ensemble.sh:485-501` · V-2 TRUE sliding window (timestamp list) diverging deliberately from the fixed `JOURNAL_EMPTY` bucket · V-11 charset-constrain at tool boundary (not encoding — keeps state.json greppable) · V-8 sweep imports the module-global getter; None ≠ dict handling mandated · V-9 grace framed as head-start + receipt disclosure + TERM-pending note; drain-reorder stays improve-lane · V-4/V-5 ari docs + drills REWRITE folded · V-6 pin-17 preserved + audit test now in scope.
