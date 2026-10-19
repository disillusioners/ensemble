# Architecture Recommendation — First-Class Agent RESTART Capability

- **Status:** DESIGN-ONLY (no implementation) — commission `agent-first-class-live-restart` · **Rev 3** (council round 2 folded)
- **Date:** 2026-10-19 · **Supersedes:** Rev 2 @ `6b4852b3c` (rev 1 @ `552c556a7`; same file; git history preserves both)
- **Branch/worktree:** `feature/agent-restart` @ `/home/nea/ensemble-src-wt-agent-restart` (base `630cefebb`)
- **Release vehicle:** stage-only, rides next staged release (expected v0.25.5). NO promote — user nonce ceremony owns that.
- **Method:** 3-worker fan-out → architect aggregation (rev 1) → council round 1 (unanimous REJECTED → rev 2) → council round 2 (REJECTED, narrow delta: all round-1 criticals adjudicated CLOSED-WITH-GAPS; this revision closes the gaps — F-1 mixed-kind coalescing, F-2 mutation-guard rewrite, F-3 mint-condition carrier, F-4 guard/labels/ordering, F-5 kind-aware terminal reader, F-6/F-9/F-10/F-11 hygiene).
- **Authoritative policy (user, verbatim):** "restart don't need my nonce, only my upgrade is need." ⇒ restart = user-triggered, NO nonce. Upgrade keeps its 3-factor nonce ceremony UNCHANGED.

**Rev-3 changelog:** §2.1 producer list corrected — `messages.py:427` resume fallback is a RESERVED value that CLEARS, not a stamp producer (F-10) · §2.4 mint-condition carrier (`spec["provenance_ok"]`) + silent-resume residual line (F-3) · §2.5 row **(j)** mixed-kind coalescing (F-1) · §3.3 acceptance-branch wording (ONLY `ENSEMBLE_RESTART_LIVE`), guard-trip event ordering, `_OUTCOME_LABELS` ×4, `live-restart-refused` rides `executor_exit` (F-4), **kind-aware terminal reader** mandated (F-5) · §4e predicate home anchor (F-9) · §4f/§5.3 escape hatch prefixed for live (F-11) · §7.3 cross-acceptance fix (F-4) · §8: mixed-group pin, kind-aware-reader e2e, mutation-guard REWRITE enumerated with C-3-trap warning (F-1/F-2/F-5) · §9: lib.sh splice bullet DELETED — `restart_window` Python-only (F-6), item 9 coalesce partition (F-1), mint carrier (F-3) · §10 residuals = council round-2 unverified list.

---

## §1 Policy Change + Lineage

### 1.1 What is superseded

| Superseded ruling | Where it lives | New status |
|---|---|---|
| **A2** — live `system_restart` refused outright, "refusal-tested only", no gate/override/dry-run exception | `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md:13,116,143`; enforced `daemon/tools/upgrade_tools.py:2305-2320` (refusal string `:2319`) | **SUPERSEDED for `system_restart` live-restart-refusal ONLY.** Live restart becomes allowed with NO nonce when the calling turn carries genuine-user provenance (§2). |
| **§3.1 per-env matrix** — actor tools free dev/demo/sandbox; live behind 3-factor gate AND refused-at-execution | `.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md:34,223` | Superseded for the restart lane: live restart gated by provenance + rails (§2, §4), not by nonce. |
| **D-FA2.4** env-self-match + live outright-refusal, marked UNCHANGED by the wake supersession | `.agents/shared/planning/post-restart-arm-notify/supersession-record.md:89` | Env-self-match check order **unchanged**; only the live-refusal arm of the ruling is superseded. |
| Banner "anything touching live is USER-GATED / explicit user-confirmed action" | `.agents/shared/planning/post-restart-arm-notify/decisions.md:3-9` | Restated for restart: live restart IS user-gated — the gate is **genuine-user provenance**, not a nonce echo. The user-confirmed *nature* of the action is preserved; the *mechanism* changes. |
| Maintenancer prohibition "Never run `system_restart`" / "Never restart the daemon" | `agents/maintenancer/rule.md:8,68`; enforced via `agents/maintenancer/meta.json:13` `tools.deny` | **SUPERSEDED** — maintenancer becomes the arming agent under the new gate (§7.1). Seal re-pin required (§7.5), **preserving audit pin-17** (§7.5). |

### 1.2 What is explicitly NOT changed

- **The upgrade 3-factor ceremony** (factors `self-restart-upgrade-phase2/architecture-recommendation.md:123-141`, enforced `upgrade_tools.py:2779-2945`). Restart reuses ONLY the factor-2 classifier through a disjoint store; upgrade call sites keep byte-identical decision behavior (§2.6). The pre-existing **write-layer** exposure of upgrade factor-2 is out of scope, filed as a project critical note by the leader (§2.5 row (i), §2.6).
- **A3 schema stability** (`architecture-recommendation.md:116`): `system_restart` keeps `user_confirmed`/`nonce` params present-but-ignored on the live lane.
- **`ENSEMBLE_UPGRADE_LIVE`** and the promote lane's verified-arm machinery: untouched, never reused by the restart lane (C-1/F-4 — the two-gate conflation the commission forbids).
- Non-live/dry-run paths behave as today.

### 1.3 Why the asymmetry flips (rationale to record with the lineage)

A restart is strictly less dangerous than a promote: same binary, no migration, no rollback surface, no `current` symlink change. The existing asymmetry forced manual `sudo systemctl restart ensemble-main.service` for the motivating case — `limits.worker_pool_size` 5→7 written by `settings_set` (`requires_restart: true, divergent: true`) but un-appliable by the agent. Policy now: provenance replaces the nonce for restart; rails (§4) replace "refuse outright".

---

## §2 Provenance Attestation Design

### 2.1 The existing primitive (verified — producer list corrected per F-10)

- **Classifier (single source of truth):** `classify_user_origin(source, registry_get)` — `daemon/tools/upgrade_journal.py:2992-3047`. Arms on exact `"api"` (HTTP chat lane) OR a registered adapter whose daemon-controlled `source_type ∈ {telegram, slack, discord, whatsapp}` (`daemon/sources/registry.py:1130,1144-1149`). **Reserved internal lanes never arm** — checked BEFORE the registry (`:3006-3013`) via `is_reserved_source` (`daemon/constants.py:928-951`; prefixes `:904-922`). Every failure mode fails closed.
- **Stamp machinery — exact shape:** the *stamp call* has a single implementation (`InstanceManager.stamp_user_origin_window`, `daemon/manager.py:4773-4841`) but **TWO call sites** — the dispatch funnel `_process_message_with_tracking` (`manager.py:9395-9402`) and the wake-sweep re-stamp (`daemon/services/upgrade_journal_sweep.py:1305-1317`, TTL-resetting). **Stamp-capable `"api"` producers (F-10-corrected):** (1) the HTTP chat lane (`daemon/routers/messages.py:691`); (2) the `_auto_dispatch_first_turn` fallback when `caller_instance_id is None` (`daemon/tools/instance.py:952-955` — hardened by this commission, §2.5 row (h)); (3) the wake-delivery fallbacks `source = wake.source or "api"` (`upgrade_journal_sweep.py:837-840`, `:1330-1333` — the C-2/F-1 laundering vectors; fixed for restart-kind wakes in §2.5 rows (i)/(j)). **Not stamp-capable:** the resume fallback at `messages.py:427` sources the RESERVED exact value `api_resume_fallback`, which **CLEARS** a window — it is not a user-origin producer (rev-2 over-claimed; corrected).
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
| Upgrade-gate isolation | Read-only, safe | **Safe at the decision layer (store/reader disjoint, §2.6); write-layer contamination fixed for restart-kind wakes (C-2/F-1)** | New auth surface — violates constraint |

### 2.4 Recommended mechanism (B) — specification

- **New in-memory store** `manager._inherited_provenance_windows` (init adjacent to `manager.py:1507`), written ONLY by the dispatch funnel (`manager.py:9395-9402`, no signature change): when `source.startswith("internal_agent:")`, parse `<caller_iid>`; **inherit iff the caller's direct window (`_user_origin_windows[caller]`) or inherited entry is live at claim time**. Entry: `{root_source, root_message_id, depth, expires_at}`.
- **Everything else clears it** — `system:*`, `agent:*`, `source=None`, any non-inheriting source — same per-turn semantics as `manager.py:4829-4831`. `_user_origin_windows` write-set stays byte-identical. **Residual (unverified, fail-closed default):** the silent-resume path skips the funnel stamp call — its interplay with the inherited store (clear vs carry) is unverified; until verified, silent resume MUST NOT inherit (treat as clearing).
- **Freshness (monotonic decay):** `expires_at = min(parent_expiry, now + 60min)`; **depth cap 4** (beyond it, refuse with guidance "ask the user to message the executor instance directly"); cleared on the target's next non-inheriting dispatch; wiped by daemon restart (in-memory ⇒ refusal degrades fail-closed like `upgrade_tools.py:2810-2816`). NOT wall-clock-only, NOT instance-lifetime: revive is a fresh dispatch stamped by ITS own source.
- **Arm-time capture (C-2):** the gate records `provenance_class ∈ {direct, inherited}` plus root source/message-id/depth. `_capture_user_source` (`upgrade_tools.py:1244-1259`) is extended to read BOTH stores and stamp `provenance_class` onto the `PendingWake` (new fields: `provenance_class`, root source/message-id, depth). `restart_audits` records `provenance_kind` and the wake source (§6.2); the receipt's `provenance:` line carries the class (§7.2).
- **Mint-condition carrier (F-3 — the C-1 gate-to-executor linkage, specified):** the ARM TOOL stamps `spec["provenance_ok"] = True` (+ `provenance_class`) onto the pending-execution spec AFTER the gate passes; the post-turn drain mints `ENSEMBLE_RESTART_LIVE=1` into the executor env **IFF that field is present**; absent ⇒ no mint ⇒ `restart.sh` live guard exits 78 (fail-closed). **Invariant:** marker present ⇒ gate passed at arm time; daemon death between arm and drain ⇒ boot reconcile closes the op as `restart_abandoned` — the arm is NEVER executed.
- **Strict live-parent inheritance** at claim time. Known tail: the parent's window clears on its *next non-user dispatch*, not at turn end — bounded by TTL + depth + budget rails.
- **Gate predicate:** `restart_allowed = provenance_ok(turn) ∧ env-self-match (D-FA2.4 order, unchanged) ∧ pipeline rails (§4a) ∧ cooldown (§4) ∧ anti-flap rails (§4) ∧ reason non-empty (§4)` where `provenance_ok = direct window live ∨ inherited record live`. Reuses `classify_user_origin` (pure) and the TTL parse helpers (`upgrade_journal.py:170-190`) — **never** the upgrade gate's store.
- **Policy line (vector g):** provenance is an **authorization-to-act channel, NOT intent-verification**. Receipt + journal name root source + message id + depth + class; intent is covered by the journaled reason, end-of-turn execution, health-gated report, and budget rails.

### 2.5 Anti-spoof vector table (an agent must NOT be able to self-forge user provenance)

| Vector | Verdict | Evidence + mitigation |
|---|---|---|
| (a) Agent-authored content mimicking a user message | **BLOCKED (structural)** | Classification reads the queue row's daemon-written `source`, never content; `send_message` has no source param (`instance.py:3564-3593`). Rule: never add content-based origin detection. |
| (b) Self-send / sibling laundering | **BLOCKED (closure + decay)** | An inherited stamp exists only if the caller's own turn carries live direct-or-inherited provenance; pure-agent chains have no root ⇒ never arm. Depth cap 4 + monotonic TTL. |
| (c) Replay of old user turns / revive | **BLOCKED** | Window per-dispatch in-memory; restart wipes; revive = fresh dispatch; stamp binds to the stamping dispatch's `message_id`. |
| (d) `system:*` / job-queue turns claiming provenance | **BLOCKED** | Reserved prefixes fail classification before registry lookup; inherit lane keys strictly on `internal_agent:<caller>` + live parent entry. |
| (e) Cross-session / cross-project injection | **BLOCKED** | Store keyed by instance UUID, in-process only; single-host trust model (ADR-015 R10). |
| (f) Chat-source users | **ACCEPTED BY DESIGN** | Daemon-built source values; same trust set the ratified upgrade factor-2 already trusts. |
| (g) Prompt-injection inside a genuine user message | **MITIGATED BY DISCLOSURE, NOT BLOCKED** | The turn IS user-originated. Gate passes; §2.4 disclosure + rails cover intent. Documented residual. |
| (h) 🟡 `"api"` auto-dispatch fallback | **FOLDED IN (hardening)** | `instance.py:952-955` fallback becomes a reserved internal source. Producer #2 in §2.1 (F-10); T0b asserts this path. |
| (i) 🔴→BLOCKED Wake-delivery laundering (C-2) | **BLOCKED BY REV-2 FIXES** | Chain: `_capture_user_source` (`upgrade_tools.py:1244-1259`) returns `""` for inherited arms → wake delivered with fallback `"api"` (`upgrade_journal_sweep.py:837-840`, `:1330-1333`) → `"api"` stamps a **DIRECT** window (`manager.py:4821-4827`) → sweep re-stamp (`:1305-1317`) re-mints with TTL reset. **Fixes:** restart-kind wakes deliver with RESERVED `system:post_restart_arm_notify` (never the `"api"` fallback; upgrade-kind untouched); `PendingWake` carries `provenance_class` + root fields; the re-stamp is **DELETED for restart-kind wakes**; T0b pin. The same write reaching upgrade factor-2 (`upgrade_tools.py:2781-2784`) is a **pre-existing upgrade-lane exposure** (bounded only by factor-3 nonce binding) filed by the leader as a project critical note. |
| **(j) 🔴→BLOCKED Mixed-kind wake coalescing (F-1 — rev-2 gap)** | **BLOCKED BY REV-3 FIX** | `_coalesce_wakes` groups by `arming_instance_id` ONLY (newest-first); `:1330-1333` applies `head.source or "api"` to the WHOLE group; the re-stamp loop (`:1305-1317`, `if w.source: …; break`) fires on the first sourced wake. Failure: an undelivered restart wake + a later 3-factor promote completing to the SAME instance ⇒ an upgrade-headed group delivers the restart notification in an envelope sourced from the promote wake's REAL user source ⇒ direct window re-minted on the arming instance ⇒ a second restart armed on that wake turn PASSES provenance. Reachable across two boots; the pure-restart T0b pin stays green while the invariant has a hole (post-fix, restart wakes are "sourced" with the reserved value, so the first-sourced wake in a mixed group is the promote wake ⇒ its user-source re-stamp fires pre-delivery). **Fix (adjudicated): PARTITION coalesce groups by `wake.kind`** — restart-kind and upgrade-kind wakes to the same instance deliver as separate groups, each with its own kind-correct source and re-stamp policy (restart: reserved source, no re-stamp; upgrade: unchanged). The alternative "any-restart-member ⇒ reserved source + skip whole group's re-stamp" was rejected: it suppresses the upgrade wake's re-stamp for the entire group, altering upgrade-lane semantics — cross-kind coupling again. T0b mixed-group pin added (§8). |

### 2.6 Upgrade-gate isolation — two-layer statement

- **Decision layer (gate reads): AIRTIGHT, kept so by rule.** The restart gate reads `_inherited_provenance_windows` + `_user_origin_windows` through a NEW helper; upgrade factor-2 keeps **explicit direct-store access** (`upgrade_tools.py:2783-2784`) and **NEVER calls the restart-side helper** (directional isolation rule — T0 pin). Factor 3 binds the nonce to the direct-window row (`:2906-2919`) — a chain can never place a nonce in a direct user row.
- **Write layer (window minting): historically contaminated, now scoped.** Post-rev-3: restart-kind wake delivery uses a reserved source, never re-stamps, and coalescing is kind-partitioned (§2.5 rows (i)/(j)). The remaining upgrade-lane write exposure (upgrade-kind wake `"api"` fallback minting direct windows reachable by factor-2) is pre-existing, out of scope, filed by the leader as a project critical note.

---

## §3 Deferred Execution Choreography (ordering guarantee)

### 3.1 Reused machinery (the promote lane — do not invent a second path)

Arm: tool → pipeline-lock acquire (`upgrade_tools.py:2433` → `upgrade_journal.py:675`) → durable `in_flight` + `pending_op` writes (`:2470`, `:2475`; `PendingOp.kind` carries `"restart"` at `upgrade_journal.py:793`) → arm-notify wake write in the SAME journal envelope (`:2481` → `:1308` → `:986`) → in-memory execution marker + spec (`:2513` → `manager.py:4837`) → return `RESTART SCHEDULED`.
Execution: turn ends → post-graph `finally` drain seam (`instance_messaging.py:5216`, shielded `:1983`, one-shot marker pop `manager.py:4910`) → spawn-refusal guard (`manager.py:4972`) → daemonized executor `systemd-run [--user] --unit=<prefix>-<run_id> --wait --collect` (`upgrade_journal.py:2267`) → `scripts/upgrade/restart.sh` (preflight `:119`; lock adoption `:160`; supervision preflight `:192`; grace `:204-212`; stop `:216`; start `:223`; gate `:236`; journal finalize `:258`).
Boot: reconcile (`daemon/api.py:2439`) → wake sweep (`:2450`, def `upgrade_journal_sweep.py:1063`, BEFORE auto-continue `:2477-2492`) → arm-notify delivered (`upgrade_journal_sweep.py:1300`) → auto-continue (boot orphan sweep, v0.25.3).

### 3.2 Ordering guarantee — precise statement

1. **HARD (today):** the stop can never precede the turn's completion — the executor spawns only from the turn's own post-graph `finally` (`instance_messaging.py:5216`), marker one-shot.
2. **SOFT (today, gap — disclosed):** the *reply* dispatches AFTER the drain (`:5231→:5244`); the stop is held off only by `GRACE_S` + `wait_inflight` grace (10s, `manager.py:13716`). **Grace is a head-start, not a bound.** Dispositions: restart-lane grace default **5s → 15s** (`--grace-s` stays exposed); receipt discloses best-effort reply delivery (§7.2); executor journals a **TERM-while-dispatch-pending note** if the reply dispatch was still pending at TERM; drain-below-dispatch restructure stays in the improve lane.

### 3.3 Restart-flavor deltas vs promote

| Delta | Decision |
|---|---|
| **Live guard env (C-1/F-3/F-4)** | Rev-1 would die at `restart.sh:92` (restart executor argv built with no attestation extras, `manager.py:4934-4940`; `is_verified_arm` promote-only, `upgrade_journal.py:1348-1363`; sole source of `ENSEMBLE_UPGRADE_LIVE=1` at `:1366-1383`; not in `EXECUTOR_ENV_ALLOWLIST`, ambient stripped, `:1782-1787`; guard exits 78, `restart.sh:92` → `lib.sh:715-728`). **Fix:** mint a RESTART-SCOPED `ENSEMBLE_RESTART_LIVE=1` **only on provenance-passed arms** — the arm tool stamps `spec["provenance_ok"]=True` post-gate (§2.4 carrier), the drain mints the var IFF present via the existing `extra_env` explicit-extra merge, absent ⇒ no mint ⇒ guard exits 78 fail-closed. **The acceptance branch accepts ONLY `ENSEMBLE_RESTART_LIVE`** (F-4 — the upgrade var is irrelevant to the restart lane; no cross-acceptance); `require_live_guard` stays **byte-identical**; `ENSEMBLE_UPGRADE_LIVE` never reused. **Guard-trip journal event (F-4):** the `live-restart-refused` event is written best-effort **lock-free** (the guard trips PRE-adoption while the arm holds the lock — the event write MUST NOT attempt lock acquisition; append-side path or defer). Its **notification rides the kind-agnostic reaper's `executor_exit`** (already wake-terminal) — `live-restart-refused` has **NO terminal-set membership**. |
| No version swap / no `atomic_flip` / no symlink change / no migration ops | Version-verify becomes an **identity** assertion, not a drift detector. |
| No rollback lane | Recovery = launcher burst/backoff supervision + operator escape hatch (§4f), which MUST appear in the arm-notify body. |
| Health gate | Report-only `/livez` (180s) **+ `/readyz` (120s, NEW for this lane)**, config `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`. Result lands in: arm-notify body; journal event `restart_gate`; `data/upgrade.log` + daemon log. **Rationale (C-3-corrected):** the existing `halt` event already fires the wake on failure (`restart.sh:262-271`) — `restart_gate` exists for explicit gate-outcome classification in the audit trail and notify body. |
| **Terminal events (C-3/F-4/F-5 — kind-aware, final shape)** | Constants: `_TERMINAL_EVENTS` **untouched** (9 members, `upgrade_journal.py:1581-1591`, consumed by promote-lane `reconcile_pending_op` `:1677-1697`; `:1596-1616` comment: a restart event MUST NEVER close a promote op). New concatenated **`RESTART_TERMINAL_EVENTS = ("restart_gate", "restart_abandoned", "stop_forced_kill")`**. `WAKE_TERMINAL_EVENTS` (`:1623`) is extended to `_TERMINAL_EVENTS + ("restart",) + RESTART_TERMINAL_EVENTS` (superset, per F-2's rewritten assertion). **Reader (F-5, MANDATED — kind-aware):** `wake_terminal_event_after(journal, armed_at, kind)` selects the terminal set BY `wake.kind` (`PendingWake.kind` exists, `:934`): for `kind=="restart"` the full superset applies; for promote-kind wakes the reader matches ONLY `_TERMINAL_EVENTS + ("restart",)` — **restart-only events can never complete a promote wake** (without this, a shared-set `stop_forced_kill` — written by `stop-ensemble.sh`, which also runs in the promote lane — would prematurely complete promote wakes mid-promote, `upgrade_tools.py:3131` arming site, with mis-attributed outcome + mid-promote re-stamp: C-3 contamination reborn in the wake dimension). **`live-restart-refused`: journal event class ONLY, no terminal-set membership** — notification rides `executor_exit` (F-4). **Outcome labels (F-4):** the 4 new event classes (`restart_gate`, `restart_abandoned`, `stop_forced_kill`, `live-restart-refused`) get `_OUTCOME_LABELS` entries (`upgrade_tools.py:1179`) so notify bodies render labels, not raw keys. Writer anchor: `stop_forced_kill` is written by `scripts/stop-ensemble.sh:485-501` (TERM→KILL escalation). |
| Graceful stop | Unchanged SINGLE-TERM contract (`lib.sh:3570` → `stop-ensemble.sh`); KILL escalation bounded (`WAIT_S=70`; unit kill grace 10s). A forced KILL voids the in-flight turn's checkpoint write ⇒ `stop_forced_kill` event. |
| Transient unit name | Parameterized `unit_prefix` — restart passes **`ensemble-restart-<run_id>`**. **V-7 contract:** `UNIT_NAME_PREFIX` is used at **4 sites** (`upgrade_journal.py:1900/:2116/:2197/:2362`); every site must accept the parameterized prefix; reaper/boot-sweep unit matching for the new prefix is **unverified** (residual, §10) — enumerate sites in the PR, define the `ensemble-{upgrade,restart}-<run_id>` family contract, add a unit-name test. |
| Stale arms | Boot reconcile explicitly closes an unexecuted `kind=restart` `pending_op` (no `in_flight`, no terminal event) with journal `restart_abandoned` + wake-abandonment notify — replacing silent 1800s expiry (`PENDING_OP_EXPIRE_RESTART_S`, `:136`; periodic-tick skip at `:1690` stays; boot lane only). **Residual:** non-boot paths keep silent 1800s expiry — launcher verified safe (`launcher.sh:859-860`). Never auto-execute a stale arm. **F-3 invariant:** daemon death between arm and drain ⇒ `restart_abandoned`, never execution. |
| Double-arm | The journal lock IS the arm-level mutex (second arm ⇒ `pipeline-busy`). Belt-and-braces: the tool refuses if the marker map already holds an entry for the same instance. Backstop: marker overwrite + `run_id` mismatch ⇒ `executor_orphaned` (`manager.py:4972`). |

### 3.4 Executor environment contract

`EXECUTOR_ENV_ALLOWLIST` governs **ambient** env (restart inherits nothing live-ish by default); the **explicit `extra_env` merge** is the only sanctioned lane for attestation vars. Promote threads `ENSEMBLE_UPGRADE_LIVE` via `_verified_arm_extras`; restart threads `ENSEMBLE_RESTART_LIVE` the same way, gated on `spec["provenance_ok"]` (F-3), consumed only by the restart-scoped acceptance branch (F-4). The two vars stay lexically and semantically disjoint forever (T0c + §10 risk 11).

---

## §4 Safety Rails (each justified, each configurable where a knob is safe)

| Rail | Mechanism | Justification | Default | Config | Refusal token |
|---|---|---|---|---|---|
| (a) Pipeline-in-flight | Check in order: journal `in_flight` → `pending_op` → `rollback.lock.d` held → `.adoption_in_progress` marker → `pipeline_settled` (mirrors `upgrade_tools.py:2331-2400`) | The promote/adoption interlock; a restart concurrent with a promote could kill the promote mid-adopt | all 5 blocking | **no knob** | `pipeline-busy` + new `adoption-in-progress`, `pipeline-not-settled` |
| (b) Cooldown | Read journal `cooldown_until`; refuse while future; **fail CLOSED on unparseable** (upgrade-lane semantics, `upgrade_tools.py:2963`) | A cooldown left by a prior aborted/halted operation signals recent instability; unparseable = untrusted = refuse | — | — | `cooldown-active` (receipt renders ACTUAL state, §7.2) |
| (c1) Minimum uptime | `now() − get_boot_epoch()` (`daemon/services/boot_epoch.py:70`) ≥ threshold; **fail CLOSED when unavailable** | Post-boot restart re-enters the boot-storm window; 600s = launcher fresh-instance horizon (`launcher.sh:59`) | **600s** | `ENSEMBLE_RESTART_MIN_UPTIME_S` | `restart-too-soon` |
| (c2) Restart budget | **True sliding window** — `restart_window.events[]` timestamp list in the journal, **Python-only** (F-6: no shell consumer; the arm/increment runs Python-side under the lock; unknown top-level keys are carried harmlessly by the shell splice); count = entries within `now − window`; prune on every read/write. **Deliberately NOT the fixed `JOURNAL_EMPTY` 24h bucket** (`upgrade_journal.py:157`) which permits 2×N across a boundary | Counter must survive the restart it meters; in-memory resets; PG may be mid-restart (arm-time PG read inside the pipeline lock — bounded, accepted) | **3 / 24h** (matches `ROLLBACK_CAP_24H=3`) | `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400` | `restart-budget-exceeded` |
| **Budget envelope (V-1)** | Increment **inside the arm's lock-holding try, in the SAME journal envelope as `in_flight`+`pending_op`+wake, FIRST** — crash between increment and op-write over-counts (safe); after-without-increment under-counts (unsafe) ⇒ increment-first. **Abandoned arms consume budget; no refund** | Durable-correctness core of the anti-flap rail | — | — | — |
| (d) Reason required | `reason: str = ""`; empty/whitespace ⇒ typed refusal; persisted form charset-constrained (§6.2, V-11); echoed into journal + arm-notify | Every restart is an operator-visible discontinuity; the reason is the only post-hoc explanation | mandatory (min 3 chars) | — | `reason-required` |
| (e) In-flight work | Graceful SINGLE-TERM stop (bounded 70s) + checkpoint freeze + **boot orphan sweep v0.25.3** (`daemon/api.py:2477-2492` → `auto_continue_boot_pass.py`; **predicate home: `find_orphaned_live_turns`, `daemon/repositories/task/repository.py:1165+` — F-9**; StaleTaskRecovery backstop boot+10min, `daemon/config.py:1271`). **The sweep is THREE-SIGNAL conditioned: task-less RUNNING ∧ healthy checkpoint ∧ ACTIVE JobItem. A bare `send_message` turn (no JobItem) is NOT re-dispatched** — the most load-bearing assumption of this rail, disclosed in the arm-notify/report and pinned by test. **Explicitly NOT protected:** mid-tool-call side effects outside checkpoints; bash `setsid` orphans; background processes (proc cleanup best-effort, `manager.py:13688`; auto-kill-bg-processes scopes root-instance completion); non-checkpointed external state | Resuming killed turns is only safe because checkpoints persist AND the three-signal sweep re-dispatches them | on (existing kill-switch) | existing | advisory `busy-check` |
| (f) Post-boot health gate | Report-only `/livez` + `/readyz`; failure ⇒ `restart_gate` event + notify + logs; **no auto-rollback** | Same version — nothing to roll back to. **Operator escape hatch (F-11 — live-target form):** `ENSEMBLE_RESTART_LIVE=1 bash scripts/upgrade/restart.sh live --run-id <r-…>` (the unprefixed form is dead-on-live post-C-1 — it hits `require_live_guard` exit 78); non-live targets run unprefixed. Idempotent-by-`run_id` (`restart.sh:139-175`). Plus `systemctl --user restart ensemble-main.service` (authoritative on the unit-managed host); `rollback.sh` only if a release change was involved | report-only | `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1` | `restart-gate-failed` (notify/journal) |

Also reused unchanged: restart-under-burst-abort (`upgrade_tools.py:1551`).

---

## §5 Boot Arm-Notify (incl. applied-overrides enumeration)

### 5.1 Mechanism (reused, fail-open — C-2/F-1 source + partition fixes, V-8 seam)

Wake record at arm (`upgrade_journal.py:864-960`, `arm_pending_wake` `:986`; kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY`, default ON); boot sweep (`daemon/api.py:2450` → `upgrade_journal_sweep.py:1063`) BEFORE auto-continue; delivery `enqueue_message(arming_instance_id, priority=2, metadata.system_context.kind="post_restart_arm_notify")` (`:1300`); coalesce cap 16; abandon grace 600s; ari front-door fallback. **C-2:** restart-kind wakes deliver with source **`system:post_restart_arm_notify`** — the `"api"` fallback (`:837-840`, `:1330-1333`) never applies to them; the `:1305-1317` re-stamp is deleted for restart-kind wakes. **F-1:** `_coalesce_wakes` **partitions groups by `wake.kind`** (§2.5 row (j)) — mixed groups cannot exist; each kind keeps its own source/re-stamp policy. **V-8:** `get_last_applied_resolution()` (`settings_service.py:426-440`) is a **module global** — `_format_wake_body` (`:547`) / `_format_coalesced_wake_body` (`:574`) import the getter directly (daemon-side, no router import) and MUST distinguish `None` (**unavailable**) from a zero-divergence dict (**no overrides**) — never a bare "0 applied". Notify failure never blocks boot (three wrap levels, `api.py:2453-2459`); failures journal into the `arm_notify_*` family.

### 5.2 Applied-overrides enumeration

- **Where overrides live:** `app_settings` rows (`daemon/models/app_settings.py:70`). **How boot applies them:** `apply_db_settings_overrides` (`daemon/services/settings_service.py:807`) called ONCE from lifespan (`daemon/api.py:510`); SSOT ladder **DB > env > yaml > default** (`resolve()` `:462`); live `Config` mutated in place (`:889-893`). `worker_pool_size` takes effect at `PoolOrchestrator.setup` boot read (`daemon/services/pool_orchestrator.py:206`). All 6 managed keys carry `requires_restart=True` (`settings_service.py:155-320`).
- **Arm-time pending set** = `requires_restart=True` ∧ running value ≠ DB override value (the `divergent` flag, `settings_tools.py:358`; `daemon/routers/settings.py:898-928`). Captured **inside the lock-holding try** into `restart_audits[run_id].overrides` in the SAME `journal_write` envelope. (Bounded PG read inside the lock — accepted.)
- **Access seam:** `SettingsService` is a router module-global (`daemon/routers/settings.py:143-154`) — wire onto the manager at `api.py` (mirroring `set_project_repository`, `:2740-2742`); the tool reads `manager._settings_service`. No tool→router import.
- **Applied ⇔** post-boot live value == arm-time DB override value. **Failure IS detectable:** invalid stored row ⇒ resolver skips the DB tier (`settings_service.py:511-556`) ⇒ `resolution["source"] != "db"` ⇒ render *not applied* with the winning tier. **Late writes / cleared:** diff arm-time `db_value` vs post-boot row ⇒ `late-write` / `cleared`, reported separately.
- **Render format:**
  ```
  applied overrides (2):
    limits.worker_pool_size: 5→7 (applied)
    limits.chat_worker_pool_size: 7→9 (not applied — resolved tier=env)
  ```

### 5.3 Notify body contents (contract)

Restart outcome (`restart_gate` result) · version now live (identity check) · `/livez` + `/readyz` gate results · applied-override list (§5.2 render) · reason echo · provenance class + root source (disclosure) · **in-flight-work disclosure** (three-signal JobItem boundary sentence) · escape-hatch line (§4f, live-prefixed form) · `run_id`.

---

## §6 Audit Journal

### 6.1 Decision: **(A) REUSE the upgrade journal** — no new table, no migration

**Corrected premise:** the "upgrade journal" is a **file-backed JSON document** at `<install_dir>/releases/state.json` (`upgrade_journal.py:250-251`; atomic temp+fsync+`os.replace` `:326-402`). The discriminator is `PendingOp.kind` (`"restart" | "promote"`, `:793`). The "every new SQLModel table requires its own `.sql` migration" rule applies only to the REJECTED option (B).

Why (A) wins: (1) **no dual write** — arm writes `pending_op` + `pending_wakes` + budget increment in one locked envelope (`upgrade_tools.py:2476-2484`, ADR-039; V-1); (2) **zero migration cost; additive-only delta** keeps `rollback_safe=true` (105 `.sql` files untouched); (3) sweep/settle semantics exist (`upgrade_journal_sweep.py:1063`); (4) a new top-level key is invisible to `lib.sh`'s field-targeted splice (ADR-034; unknown fields carried, `upgrade_journal.py:34-38`).

*(B) sketch retained for the record:* `2026102X_000001_create_restart_audits.sql` — only if cross-install querying is ever required. **Not recommended.**

### 6.2 Additive schema: top-level `restart_audits` list + `restart_window`

Added idempotently in `ensure_extensions` (`upgrade_journal.py:404-430`), keyed by `run_id`, append-only parallel to `history`, **capped at 50 (drop-oldest)**:

```jsonc
{ "run_id":"…", "env":"live", "kind":"restart",
  "reason":"apply worker_pool_size 5→7",           // charset-constrained
  "arming_instance_id":"…", "arming_agent_id":"maintenancer",
  "provenance_kind":"direct|inherited",
  "user_source":"discord:<user>", "user_message_id":"…", "provenance_depth":1,
  "wake_source":"system:post_restart_arm_notify",
  "armed_at":"ISO", "executed_at":"ISO|null",
  "outcome":"applied|halted|failed|unknown",
  "overrides":[{"key":"limits.worker_pool_size","live_before":5,"db_value":7,
                "status":"applied|not-applied|cleared|late-write"}],
  "notify":{"status":"delivered|abandoned|pending","at":"ISO","message_id":"…"} }
```

Sibling key `restart_window: {"events":["ISO", …]}` — Python-only sliding-window budget list (§4c2). Arm writes static fields + pending override set + **budget increment (FIRST, same envelope)**; boot-settle updates `executed_at`/`outcome`/`overrides[].status`/`notify` in place.

**Reason charset constraint (V-11):** the **persisted** reason is constrained at the tool boundary — printable, no newlines, no splice-sentinel class, ≤200 chars; the conversation may carry fuller prose but journal/wake/audit surfaces store the constrained form.

### 6.3 Query surface: extend `upgrade_status` (no new tool)

`upgrade_status` (`upgrade_tools.py:2078`, category `system_upgrade`, in the maintenancer allow-list) gains `section: Literal["status","restarts"] = "status"`, reusing `run_id`/`tail`:

```
RESTART AUDIT — env=live  (n=3, showing 3)
when | who | reason | outcome | overrides
2026-10-20T01:30:00Z | maintenancer/inst-ab12 | apply worker_pool_size 5→7 | applied | limits.worker_pool_size: 5→7 (applied)
```

Filters: `run_id`, `outcome`, `since`, `tail`. `release_info(section=journal)` remains the raw view. A new tool was rejected: registration + allow-list decision in a privileged category (`tests/unit/tools/test_upgrade_registration.py:106-120`) + duplicated env gate/journal split/lock probes.

---

## §7 Tool Contract

### 7.1 Tool identity — **keep `system_restart`, lift the deny**

`agents/maintenancer/meta.json:13` `"deny": [..., "system_restart"]` — deny-by-name strips (`instance.py:6333`); **deny ≠ allow** ⇒ lifting is a policy edit, not an allow-list change. Signature keeps `user_confirmed`/`nonce` present-but-ignored (A3). Arming agent = **maintenancer** — but **the gate is provenance, not agent identity** (V-4): ari also holds `system_restart` (`agents/ari/meta.json:29`, no deny) and is governed identically.

### 7.2 Receipt (keep the byte-shape prefix — tests grep it)

```
RESTART SCHEDULED — run_id=<id> env=live mode=graceful-now reason="<reason>"
executes_after_turn: yes
reply_delivery: best-effort (head-start grace 15s; reply may be lost — post-restart arm-notify is the durable report)
notify: armed (post-restart auto-wake to this instance; kill-switch ENSEMBLE_POST_RESTART_ARM_NOTIFY)
overrides_pending: limits.worker_pool_size 5→7
journal: releases/state.json pending-op opened (kind=restart) audit=restart_audits/<run_id>
budget: restarts_24h=<n>/3 cooldown=<actual state>
provenance: class=<direct|inherited> root=<root_source> msg=<root_message_id> depth=<d>
trigger: post-turn-callback armed
busy-check: idle
```

### 7.3 Typed refusals — house style, single writer

One writer: `_refusal()` (`upgrade_tools.py:1425-1433`) ⇒ `Error: RESTART REFUSED — reason=<token>: <message>`; journaled `refusal` (lock-busy `pipeline-busy` carve-out `:1394-1400` preserved). **No second vocabulary.**

| Commission token | House token | Status |
|---|---|---|
| `refused: pipeline-busy` | `pipeline-busy` (+ `adoption-in-progress`, `pipeline-not-settled`) | exists + 2 new |
| `refused: cooldown` | **`cooldown-active`** (`:2963`) | NEW check for restart |
| `refused: no-user-provenance` | `no-user-provenance` (sub-cause missing \| expired \| depth-capped + observed-source diagnostic, `:2790-2805` shape) | NEW |
| `refused: reason-required` | `reason-required` (via `reason: str = ""`) | NEW |
| — | `restart-too-soon`, `restart-budget-exceeded` | NEW |

**`live-restart-refused`: removed from the TOOL vocabulary (`:2570-2576`), re-homed as the script-guard JOURNAL event class — its notification rides the kind-agnostic reaper's `executor_exit` (already wake-terminal); it has NO terminal-set membership (F-4).** `RESTART_TOKENS` pin (`test_upgrade_tools.py:1537/3468-3480`) extended with the 8 restart-lane tokens.

### 7.4 Docs surface (every file whose text must change)

| File | Change |
|---|---|
| `daemon/tools/upgrade_tools.py:2318-2327` | live outright-refusal string → provenance-gated path |
| `daemon/tools/upgrade_tools.py:2545-2585` (`_full_doc_`) | "LIVE: refused outright…" → new contract (receipt fields, refusal tokens, provenance requirement, executor live-guard note) |
| `daemon/tools/upgrade_tools.py:11,112,131` | module docstring + checklist note |
| `agents/maintenancer/meta.json:13` | lift `system_restart` from `tools.deny` |
| `agents/maintenancer/rule.md:8,68` | restart policy rewrite — **must preserve audit pin-17** (`tests/test_chart_image_delivery_audit.py:467-483`; maintenancer in `CHART_CAPABLE_AGENTS`, `:67`) |
| `agents/maintenancer/tools_note.md:20,77` | deny-table row + "structurally excluded" prose |
| `agents/maintenancer/README.md:37,74` | "the user pulls the restart lever" + skill one-liner |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | Boundary / Contract / Focus-area 5 / report format |
| `agents/ari/tools_note.md:421` | still teaches the old "refused outright" contract — rewrite; provenance-not-identity stated |
| `agents/maintenancer/ROLLOUT.md:52,269,286,381,444` | deny-list references (optional) |
| `docs/runbooks/post-restart-arm-notify.md` §1 | "Live never records (invariant 8)" + ADR-044 paragraph — rewrite **restart-only**; two gates never conflated |
| `docs/runbooks/upgrade-drills.md:468/535` | still state live restart refused outright — **REWRITE these passages**, not append |

**V-12 caveat:** the template edit does NOT update the seeded skill-bank row (`faf6e86e…`, `bank_clone`; `skill_seed_service.py:325-352`) — the developer must execute the bank-row update path; a T9 case pins it.

### 7.5 Seal re-pin (same-merge discipline)

`agents/maintenancer/rule.md` is seal-pinned (`tests/test_chart_image_delivery_e2e.py:1389`, tripwire `:1423`) ⇒ same-merge re-pin with git-log attribution, targeted seal test, provenance-labeled commit. **Pin-17** (`tests/test_chart_image_delivery_audit.py:467-483`) constrains the rule.md rewrite — preserved and pinned by T9. **F-2 (same discipline, second site):** the sweep-test mutation guard (`tests/unit/services/test_post_restart_arm_notify_sweep.py:856-870`) asserts `set(WAKE_TERMINAL_EVENTS) == set(_TERMINAL_EVENTS) | {"restart"}` — **BREAKS under the extension**; the assertion REWRITE is an explicit adjudicated same-merge step: expected set becomes `set(_TERMINAL_EVENTS) | {"restart"} | set(RESTART_TERMINAL_EVENTS)`, the `len(_TERMINAL_EVENTS)==9` and absence pins stay verbatim, the file runs targeted. ⚠️ **The fast wrong fix — adding the 3 names to `_TERMINAL_EVENTS` to restore the old equality — is the C-3 trap** (restart events closing promote ops); it is forbidden.

---

## §8 Test Plan (7 mandated cases + restart-sim + round-2 pins)

Primary surfaces: `tests/unit/tools/test_upgrade_tools.py` (`TestSystemRestartMatrix:1928`, `TestLiveThreeFactorGate:2461`, `RESTART_TOKENS` `:1537/3468-3480`), `tests/unit/tools/test_post_restart_arm_notify_journal.py:772`, `tests/unit/services/test_post_restart_arm_notify_sweep.py` (`:844-869` terminal pins, `:856-870` mutation guard), `tests/job_queue/test_post_restart_arm_notify_{routing,edge_cases}.py`, `test/drills/post_restart_arm_notify_drill.sh` + `wake_drill_driver.py:519` + `tests/test_post_restart_arm_notify_drill_smoke.py`, `tests/mocks/upgrade_tools_live_safety_mock.py:516-519`.

**T0 — precondition pins:**
- (a) Upgrade factor-2/3 byte-identity; directional isolation (factor-2 never calls the restart helper); factor-3 nonce-to-direct-row binding.
- (b) **Terminal-event mutation guard (REWRITTEN per F-2):** new names in `WAKE_TERMINAL_EVENTS`, absent from `_TERMINAL_EVENTS`, `len(_TERMINAL_EVENTS)==9`, expected equality `set(_TERMINAL_EVENTS) | {"restart"} | set(RESTART_TERMINAL_EVENTS)`.
- (b′) **Kind-aware reader (F-5) e2e:** promote armed → `stop_forced_kill` lands → **promote wake NOT completed**; promote `pending_op` NOT closed by any restart event.
- (c) **Executor env negative:** restart lane NEVER sets `ENSEMBLE_UPGRADE_LIVE`; `ENSEMBLE_RESTART_LIVE` minted only when `spec["provenance_ok"]` present (F-3 carrier pin).
- (d) **Wake-provenance side-effect:** restart-kind wake delivery mints NO user window.

**T0b — spoof vectors §2.5 (a)–(j):** structural pins + row (h) `instance.py:952-955` path + row (i) second-restart-on-wake-turn refused `no-user-provenance` + **row (j) MIXED-GROUP pin (F-1): a restart wake coalesced with an upgrade-headed group (pre-partition shape, or any future regression) ⇒ no user window minted; second arm refused `no-user-provenance`.**

| # | Case | Approach |
|---|---|---|
| 1 | genuine-user turn → scheduled | seed `_user_origin_windows[iid]` (fixture `:2461`); assert receipt + `provenance: class=` + no REFUSED; inherited variant via `internal_agent:<caller>` |
| 2 | agent-initiated → refused | no window ⇒ `no-user-provenance` + diagnostic; ZERO journal mutation (mirror `:2796`) |
| 3 | promote in-flight → refused | `test_pipeline_busy_armed:2374` shape ⇒ `pipeline-busy` (no refusal event); + `adoption-in-progress`, `pipeline-not-settled` variants |
| 4 | cooldown/budget → refused | `test_cooldown_active_armed_refused:2339` mirror ⇒ `cooldown-active` (+ unparseable ⇒ fail-closed); budget cap ⇒ `restart-budget-exceeded`; **window-boundary: arms at T and T+window−ε both counted**; **crash-ordering (increment-first over-count direction) + concurrent arms (one envelope, one refusal)**; min-uptime ⇒ `restart-too-soon`; epoch-unavailable ⇒ fail-closed |
| 5 | post-restart notify contents | `_format_wake_body` unit: applied/not-applied/tier render; **`None` ≠ zero-dict** (V-8); **`_OUTCOME_LABELS` entries present for the 4 new events — bodies render labels, not raw keys (F-4)**; journal unit: `restart_audits` record (incl. `provenance_kind`, `wake_source`) + boot-settle update; pin-17 preserved; seeded bank row updated; JobItem-boundary disclosure present; drill **D7** + D1–D6 grep extension |
| 6 | deferred execution ordering | `test_armed_demo_returns_scheduled_no_spawn:2396` mirror: `pending_op` armed, NO executor spawned, `executes_after_turn: yes` |
| 7 | **REAL restart-simulation** | drill extension with **fake-live executor-crossing leg: provenance-seeded arm → `spec["provenance_ok"]` → `ENSEMBLE_RESTART_LIVE` through the real executor env → acceptance branch → real stop/start → wake with applied overrides + `restart_gate`** (would have caught C-1). Mirror `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py` + `tests/test_promote_cgroup_survivorship.sh`. ⛔ `mktemp` FAKE_HOME + escape guard |
| T8 | Rails extras | same-instance re-arm refused; stale-arm boot reconcile ⇒ `restart_abandoned` + abandonment notify; budget survives simulated restart; `stop_forced_kill` from `stop-ensemble.sh:485-501`; unit-name test (V-7); **escape-hatch one-liner live-prefixed form works / unprefixed hits 78 (F-11)** |
| T9 | Expectation flips + surface enumeration | `:1936`, `:1500`, `:1397`, integration docstring (upgrade intact); V-13 surfaces (mock `:516-519`, `RESTART_TOKENS` ×8, journal `:772`, banner `:519`); ari `tools_note.md:421`; drills `:468/535` rewrite; audit pin-17; **F-2 mutation-guard rewrite runs green** |

---

## §9 File-Level Implementation Breakdown (ordered, for the developer)

1. **`daemon/manager.py`** — init `_inherited_provenance_windows` (adjacent `:1507`); inherit lane in the funnel (`:9395-9402`) extending `stamp_user_origin_window` (`:4773-4841`), direct-store write-set byte-identical; **drain mints `ENSEMBLE_RESTART_LIVE=1` IFF `spec["provenance_ok"]` present** (F-3), threaded via the `extra_env` explicit-extra merge (`:4934-4940` region; promote extras `:4967-4970` untouched); wire `manager._settings_service` (`api.py` near `:2740-2742`).
2. **`daemon/tools/instance.py`** — `:952-955` `"api"` fallback → reserved internal source.
3. **`daemon/tools/upgrade_journal.py`** — `restart_audits` + `restart_window` (**Python-only**, F-6) top-level keys via `ensure_extensions` (`:404-430`); `unit_prefix` parameterized across **all 4 `UNIT_NAME_PREFIX` sites** (`:1900/:2116/:2197/:2362`); **`RESTART_TERMINAL_EVENTS` concatenated constant + `WAKE_TERMINAL_EVENTS` superset extension + kind-aware `wake_terminal_event_after(journal, armed_at, kind)`** (`_TERMINAL_EVENTS` untouched — C-3/F-5); `_OUTCOME_LABELS` entries ×4 (F-4); boot-lane abandoned-closure for `kind=restart` (near `:1690`, boot only); `PendingWake` gains `provenance_class` + root fields (C-2).
4. **`scripts/upgrade/lib.sh`** — restart-scoped acceptance branch for **only** `ENSEMBLE_RESTART_LIVE` at the live-guard consumer (`:715-728`); `require_live_guard` byte-identical. *(rev-2's `restart_window` splice bullet DELETED — no shell consumer, F-6.)*
5. **`scripts/upgrade/restart.sh`** — grace default 15s; acceptance of `ENSEMBLE_RESTART_LIVE` only, at `:92`; guard-trip `live-restart-refused` event **best-effort lock-free** (pre-adoption; never acquires the arm-held lock; notification rides `executor_exit`); report-only `/readyz`; `restart_gate` on success AND failure; `unit_prefix` passthrough; TERM-while-dispatch-pending note.
6. **`scripts/stop-ensemble.sh`** — `stop_forced_kill` event at the `:485-501` escalation site.
7. **`daemon/config.py`** — `ENSEMBLE_RESTART_MIN_UPTIME_S=600`, `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400`, `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`.
8. **`daemon/tools/upgrade_tools.py`** — replace live refusal (`:2305-2320`): provenance helper (both stores) → rails (pipeline 5-check, cooldown fail-closed, min-uptime, budget increment-FIRST in-envelope, reason constraint) → arm: **stamp `spec["provenance_ok"]=True` + `provenance_class` post-gate (F-3)**; override capture inside lock via `manager._settings_service`; `_capture_user_source` `:1244-1259` extended to both stores + class; extended receipt; tokens per §7.3; `_full_doc_` rewrite; module docstring; `_OUTCOME_LABELS` ×4 (`:1179`).
9. **`daemon/services/upgrade_journal_sweep.py`** — **`_coalesce_wakes` partitions groups by `wake.kind` (F-1)**; restart-kind wake source `system:post_restart_arm_notify`; restart-kind re-stamp deleted (`:1305-1317`); wake body per §5.3 (module-global resolution getter, None≠dict); fail-open preserved.
10. **`agents/maintenancer/meta.json`** — lift `system_restart` from `tools.deny`.
11. **`agents/maintenancer/rule.md`** — policy rewrite (`:8`, `:68`) preserving pin-17; same-merge seal re-pin (`:1389` baseline) + audit-test run; **F-2 same-merge step: sweep-test mutation-guard assertion rewrite** (`test_post_restart_arm_notify_sweep.py:856-870`).
12. **Docs** — maintenancer `tools_note.md` + **ari `tools_note.md:421`**, `README.md`, `ROLLOUT.md` (optional), `skills-template/restart-upgrade-ops.md` **+ seeded skill-bank row update** (`skill_seed_service.py:325-352`), `docs/runbooks/post-restart-arm-notify.md` §1, `docs/runbooks/upgrade-drills.md:468/535` **rewrite**.
13. **Tests** — per §8 (T0/T0b first — including the F-2 rewrite and F-5 kind-aware-reader e2e — then 1–7 with the fake-live executor-crossing D7 leg, T8, T9).

---

## §10 Risks & Mitigations

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | Spoofable provenance ⇒ autonomous live restarts | 🔴 | Mechanism B daemon-minted only; structural classification; TTL + depth + per-turn clear + restart-wipe; vector table §2.5 incl. rows (i) wake-laundering and **(j) mixed-kind coalescing — closed**; pins T0/T0b |
| 2 | Provenance propagation weakens the upgrade gate | 🔴 | §2.6 two-layer: decision layer airtight (directional rule + T0a); write-layer restart-kind contamination fixed (C-2 + F-1 partition); upgrade-lane exposure = leader-filed critical note |
| 3 | Executor live-guard DoA (C-1 class) | 🔴→closed | `spec["provenance_ok"]` carrier (F-3) → `ENSEMBLE_RESTART_LIVE` via `extra_env`; acceptance branch; T0c + fake-live D7 leg |
| 4 | Terminal-event mutation closes a promote op (C-3 class) | 🔴→closed | `_TERMINAL_EVENTS` untouched; `RESTART_TERMINAL_EVENTS` concatenated; **kind-aware reader (F-5)**; T0b guard (F-2-rewritten) + promote-not-completed e2e |
| 5 | Wake delivery re-mints direct provenance (C-2/F-1 class) | 🔴→closed | Reserved wake source; re-stamp deleted for restart-kind; **coalesce partitioned by kind**; T0d + T0b rows (i)/(j) regression proofs |
| 6 | Reply lost in the soft grace window | 🟡 | Grace 15s (head-start, disclosed); receipt disclosure; TERM-pending note; drain-reorder in improve lane |
| 7 | Failed restart has no rollback lane | 🟡 | Launcher supervision; escape hatch in notify (live-prefixed form, F-11); `restart_gate` classification; `systemctl --user restart ensemble-main.service` |
| 8 | Unprotected in-flight side effects + JobItem boundary | 🟡 | §4e gap list + three-signal predicate (home `find_orphaned_live_turns`, `task/repository.py:1165+` — F-9) disclosed + pinned |
| 9 | Rails fail open on missing data | 🟡 | Epoch/unparseable-cooldown ⇒ fail closed; budget journal-durable, increment-first, true sliding window |
| 10 | Journal RMW growth / splice collision | 🟢 | Cap 50; reason charset-constrained |
| 11 | Two live-guard vars drift together | 🟡 | Lexical/semantic disjointness (§3.4); T0c; acceptance branch takes ONLY the restart var (F-4) |
| 12 | `unit_prefix` reaper/boot-sweep matching unverified | 🟡 | 4-site enumeration + family contract + unit test; residual until verified |
| 13 | Docs drift / two gates conflated | 🟡 | §7.4 list (ari + drills rewrite); bank-row update + T9; seal re-pin + pin-17; F-2 mutation-guard rewrite as adjudicated same-merge step |
| 14 | Stage-only ride on concurrent staging worktree | 🟢 | v0.25.5 expected; additive-only delta |
| 15 | **Council round-2 residual-unverified list (carried honestly)** | 🟢 | `_capture_user_source` body; drain-vs-reply ordering `:5216/:5231/:5244`; silent-resume × inherited-store interplay (§2.4 fail-closed default); reaper unit matching under `ensemble-restart-*`; `_format_wake_body` bodies `:547/:574`; guard-trip event-write self-locking pre-adoption (best-effort lock-free mandated, verification residual); exhaustive `additional_kwargs["source"]` write-site enumeration; launcher `_journal_sweep` restart-branch e2e |

---

## Appendix — Provenance of this document

- **Rev 1:** worker reports `architect-worker-restart-provenance` (`c5a12c21`), `architect-worker-restart-choreography` (`6bb6574b`), `architect-worker-restart-journal` (`97b28adb`); architect verified meta.json deny `:13`, rule.md seal pin `:1389`, `journal_path()` `:250-251`, receipt/reason signature. Commit `552c556a7`.
- **Rev 2:** council round 1 (2 councilors, unanimous REJECTED → revise-and-resubmit). Folded C-1/C-2/C-3, V-1..V-12, V-13..V-15, contract language. Commit `6b4852b3c`.
- **Rev 3:** council round 2 (REJECTED, narrow delta; round-1 criticals adjudicated CLOSED-WITH-GAPS). Folded: F-1 (mixed-kind coalescing — **partition by `wake.kind`** adjudicated over the any-restart-member rule, which would suppress upgrade-lane re-stamp semantics), F-2 (mutation-guard assertion rewrite as adjudicated same-merge step, C-3-trap warning recorded), F-3 (`spec["provenance_ok"]` mint-condition carrier + never-execute-on-death invariant), F-4 (restart-var-only acceptance; `live-restart-refused` rides `executor_exit`, no terminal membership; `_OUTCOME_LABELS` ×4; lock-free guard-trip event write), F-5 (**kind-aware terminal reader** mandated — `RESTART_TERMINAL_EVENTS` consumed only by the restart path; promote-wake-not-completed e2e), F-6 (lib.sh splice bullet deleted; `restart_window` Python-only), F-9 (`find_orphaned_live_turns` predicate home), F-10 (`api_resume_fallback` clears, not stamps — producer list corrected), F-11 (escape hatch live-prefixed). F-7/F-8 were council non-findings — no doc change; the `:1995` probe is noted for the PR description. Residual list per council round 2 carried in §10 row 15.
