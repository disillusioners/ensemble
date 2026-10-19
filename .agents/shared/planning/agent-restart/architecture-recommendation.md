# Architecture Recommendation — First-Class Agent RESTART Capability

- **Status:** DESIGN-ONLY (no implementation) — commission `agent-first-class-live-restart` · **Rev 4** (surgical: approver blocking item + N-notes)
- **Date:** 2026-10-19 · **Supersedes:** Rev 3 @ `eb7d4d8ca` (rev 2 @ `6b4852b3c`, rev 1 @ `552c556a7`; same file; git history preserves all)
- **Branch/worktree:** `feature/agent-restart` @ `/home/nea/ensemble-src-wt-agent-restart` (base `630cefebb`)
- **Release vehicle:** stage-only, rides next staged release (expected v0.25.5). NO promote — user nonce ceremony owns that.
- **Method:** 3-worker fan-out → architect aggregation (rev 1) → council round 1 (rev 2) → council round 2 (rev 3 — architecture independently confirmed: all 7 requirements covered, ~40 anchors exact, no upgrade-gate weakening mechanism found) → **approver round (rev 4 — ONE blocking item folded: operator escape hatch shipped a broken recovery command; plus N1..N8)**.
- **Authoritative policy (user, verbatim):** "restart don't need my nonce, only my upgrade is need." ⇒ restart = user-triggered, NO nonce. Upgrade keeps its 3-factor nonce ceremony UNCHANGED.

**Rev-4 changelog:** 🔴 **escape hatch corrected** (§3.3/§4f/§5.3/§10): unit scope DERIVED from the scripts' supervision classification (never hardcoded; SYSTEM-level `ensemble-main.service` on the live host — `/etc/systemd/system/…`, cgroup `/system.slice/…`; NO user unit exists, `systemctl --user` cannot connect), `bash launcher.sh restart_current` alternative; "idempotent-by-run_id" claim corrected to **re-runnable only while `pending_op` + lock survive** (`restart.sh:131-175` refuses exit 78 otherwise; the failure path clears the op and releases the lock) — the scoped service restart is the ONLY recovery path for executed→gate-failed→daemon-down · N1 inherit-lane scope (`internal_agent:` only; `internal_invoke_and_wait:` never inherits) + pin · N2 `dry_run` semantics specified; §1.2 "as today" contradiction fixed · N3 gate-results data carrier = journal `detail` channel · N4 row (h) strengthening disclosed + upgrade-side pin · N5 stale-arms premise corrected (abandoned arms NEVER expire — they wedge `pipeline-busy`; boot closure is a real fix, covers `in_flight`-still-set too) · N6 restart-wake superset asymmetry noted (§10 row 4) · N7 anchor drift ×4 fixed · N8 PR-description note (no `.sql` ⇒ additive-only ⇒ `rollback_safe=true`) in §6.1.

---

## §1 Policy Change + Lineage

### 1.1 What is superseded

| Superseded ruling | Where it lives | New status |
|---|---|---|
| **A2** — live `system_restart` refused outright, "refusal-tested only", no gate/override/dry-run exception | `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md:13,116,143`; enforced `daemon/tools/upgrade_tools.py:2305-2320` (refusal string `:2319`) | **SUPERSEDED for `system_restart` live-restart-refusal ONLY.** Live restart becomes allowed with NO nonce when the calling turn carries genuine-user provenance (§2). |
| **§3.1 per-env matrix** — actor tools free dev/demo/sandbox; live behind 3-factor gate AND refused-at-execution | `.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md:34,223` | Superseded for the restart lane: live restart gated by provenance + rails (§2, §4), not by nonce. |
| **D-FA2.4** env-self-match + live outright-refusal, marked UNCHANGED by the wake supersession | `.agents/shared/planning/post-restart-arm-notify/supersession-record.md:89` | Env-self-match check order **unchanged**; only the live-refusal arm of the ruling is superseded. |
| Banner "anything touching live is USER-GATED / explicit user-confirmed action" | `.agents/shared/planning/post-restart-arm-notify/decisions.md:3-9` | Restated for restart: live restart IS user-gated — the gate is **genuine-user provenance**, not a nonce echo. |
| Maintenancer prohibition "Never run `system_restart`" / "Never restart the daemon" | `agents/maintenancer/rule.md:8,68`; enforced via `agents/maintenancer/meta.json:13` `tools.deny` | **SUPERSEDED** — maintenancer becomes the arming agent under the new gate (§7.1). Seal re-pin required (§7.5), **preserving audit pin-17**. |

### 1.2 What is explicitly NOT changed

- **The upgrade 3-factor ceremony** (factors `self-restart-upgrade-phase2/architecture-recommendation.md:123-141`, enforced `upgrade_tools.py:2779-2945`). Restart reuses ONLY the factor-2 classifier through a disjoint store; upgrade call sites keep byte-identical decision behavior **with one disclosed, strictly-strengthening delta** (N4): the row (h) hardening (`instance.py:952-955` `"api"` fallback → reserved source) removes an input the upgrade gate could previously (mis)receive — an agent-path-minted direct window. No upgrade behavior weakens; one spoof-adjacent input disappears (pinned upgrade-side, §8 T0a′). The pre-existing **write-layer** exposure of upgrade factor-2 remains out of scope, filed as a project critical note by the leader (§2.5 row (i), §2.6).
- **A3 schema stability** (`architecture-recommendation.md:116`): `system_restart` keeps `user_confirmed`/`nonce` params present-but-ignored on the live lane.
- **`ENSEMBLE_UPGRADE_LIVE`** and the promote lane's verified-arm machinery: untouched, never reused by the restart lane.
- **Non-live environments behave as today. `dry_run` semantics (N2 — specified, replacing the contradictory "as today" phrasing):** `dry_run=true` on ANY env (including live) evaluates and REPORTS the full verdict — provenance check result (with class + observed source), every rail outcome, the would-be pending override set — and returns WITHOUT arming: zero journal mutation, no lock, no executor, no wake. On live this supersedes today's outright refusal (a dry-run on live today is refused; under the new policy it reports the gate verdict instead — that is the intended change).

### 1.3 Why the asymmetry flips (rationale to record with the lineage)

A restart is strictly less dangerous than a promote: same binary, no migration, no rollback surface, no `current` symlink change. The existing asymmetry forced manual `sudo systemctl restart ensemble-main.service` for the motivating case — `limits.worker_pool_size` 5→7 written by `settings_set` (`requires_restart: true, divergent: true`) but un-appliable by the agent. Policy now: provenance replaces the nonce for restart; rails (§4) replace "refuse outright".

---

## §2 Provenance Attestation Design

### 2.1 The existing primitive (verified)

- **Classifier (single source of truth):** `classify_user_origin(source, registry_get)` — `daemon/tools/upgrade_journal.py:2992-3047`. Arms on exact `"api"` (HTTP chat lane) OR a registered adapter whose daemon-controlled `source_type ∈ {telegram, slack, discord, whatsapp}` (`daemon/sources/registry.py:1130,1144-1149`). **Reserved internal lanes never arm** — checked BEFORE the registry (`:3006-3013`) via `is_reserved_source` (`daemon/constants.py:928-951`; prefixes `:904-922`). Every failure mode fails closed.
- **Stamp machinery — exact shape:** the *stamp call* has a single implementation (`InstanceManager.stamp_user_origin_window`, `daemon/manager.py:4773-4841`) but **TWO call sites** — the dispatch funnel `_process_message_with_tracking` (`manager.py:9395-9402`) and the wake-sweep re-stamp (`daemon/services/upgrade_journal_sweep.py:1305-1317`, TTL-resetting). **Stamp-capable `"api"` producers:** (1) the HTTP chat lane (`daemon/routers/messages.py:691`); (2) the `_auto_dispatch_first_turn` fallback when `caller_instance_id is None` (`daemon/tools/instance.py:952-955` — hardened by this commission, §2.5 row (h)); (3) the wake-delivery fallbacks `source = wake.source or "api"` (`upgrade_journal_sweep.py:837-840`, `:1330-1333` — the C-2/F-1 vectors; fixed for restart-kind wakes). **Not stamp-capable:** the resume fallback at `messages.py:427` sources the RESERVED exact value `api_resume_fallback`, which **CLEARS** a window.
- **TTL:** `NONCE_TTL_S = 60min` (`upgrade_journal.py:135`, ADR-036), applied at `manager.py:4823-4827`.
- **Verify site (upgrade gate, factor 2):** `upgrade_tools.py:2781-2830` — reads `_user_origin_windows` directly (`:2783-2784`); fail-closed on unparseable/expired.
- **`source` is daemon-written everywhere:** chat adapters build `source=f"{source_id}:{external_user_id}"`; agent-tool sends hardcode `internal_agent:{caller}` (`daemon/tools/instance.py:3989,4096,4200,1187`); job-queue lane `agent:{caller_agent_id}` (`daemon/tools/job_queue.py:2006,1265`); `send_message` exposes **no** source param (`instance.py:3564-3593`). `additional_kwargs["source"]` write sites known: `instance_messaging.py:395-419`, `graph.py:8406-8412` (exhaustive enumeration carried as a residual, §10).

### 2.2 Dispatch-chain verdict (the mandatory question)

**The primitive is DIRECT-USER-ONLY, not chain-aware.** Proof: user → ari (`source="api"`, window stamped) → ari `send_message` → maintenancer: the child's turn dispatches with `source="internal_agent:<ari_iid>"` (`instance.py:4200`), the funnel classifies it reserved, and the window is **cleared** (`manager.py:4829-4831`). `classify_user_origin` takes no ancestor argument; no code path consults the caller's window at enqueue or claim. Consequence: the delegation scenario — exactly the `worker_pool_size` case — cannot pass an upgrade-style factor-2 check today ("no stamp recorded this turn", `upgrade_tools.py:2808-2817`).

### 2.3 Options compared

| | (A) Gate-time ancestry walk | **(B) System-minted inherited stamp at dispatch ← RECOMMENDED** | (C) Capability token |
|---|---|---|---|
| Mechanism | At gate, parse `internal_agent:<caller>` → read ancestor's last stamp → recurse up | At the stamp funnel, on `internal_agent:<caller>`, if caller holds a live direct-or-inherited entry, write a target entry in a **separate store** | Mint single-use token on user-origin turns; agent presents it |
| Cost | N reads/gate; fragile (stamp dict diagnostic-grade; races; terminal/revive indistinguishable) | O(1) per dispatch; one dict read at gate | Highest: new lifecycle, LLM-transit bearer-token leak risk (rejected by OQ9) |
| Upgrade-gate isolation | Read-only, safe | **Safe at the decision layer (§2.6); write-layer fixed for restart-kind wakes (C-2/F-1)** | New auth surface — violates constraint |

### 2.4 Recommended mechanism (B) — specification

- **New in-memory store** `manager._inherited_provenance_windows` (init adjacent to `manager.py:1507`), written ONLY by the dispatch funnel (`manager.py:9395-9402`, no signature change): when `source.startswith("internal_agent:")`, parse `<caller_iid>`; **inherit iff the caller's direct window (`_user_origin_windows[caller]`) or inherited entry is live at claim time**. Entry: `{root_source, root_message_id, depth, expires_at}`.
- **Inherit-lane scope (N1 — explicit):** the lane keys ONLY on the `internal_agent:` prefix. The `internal_invoke_and_wait:{parent}` lane (invoke-and-wait dispatch) is a RESERVED prefix and **NEVER inherits** — no provenance propagation through the invoke-and-wait path, by design (it is a synchronous tool-call lane, not a conversational delegation; the motivating path user → ari → maintenancer runs on `send_message`/`internal_agent:` and is covered). Pinned in T0b.
- **Everything else clears it** — `system:*`, `agent:*`, `internal_invoke_and_wait:*`, `source=None`, any non-inheriting source — same per-turn semantics as `manager.py:4829-4831`. `_user_origin_windows` write-set stays byte-identical. **Residual (fail-closed default):** silent-resume × inherited-store interplay unverified — silent resume MUST NOT inherit (treat as clearing) until verified.
- **Freshness (monotonic decay):** `expires_at = min(parent_expiry, now + 60min)`; **depth cap 4** (beyond: refuse, "ask the user to message the executor instance directly"); cleared on the target's next non-inheriting dispatch; wiped by daemon restart (fail-closed like `upgrade_tools.py:2810-2816`). Revive = fresh dispatch stamped by its own source.
- **Arm-time capture (C-2):** the gate records `provenance_class ∈ {direct, inherited}` plus root source/message-id/depth; `_capture_user_source` (`upgrade_tools.py:1244-1259`) extended to read BOTH stores and stamp `provenance_class` onto the `PendingWake` (new fields). `restart_audits` records `provenance_kind` + wake source (§6.2); receipt carries the class (§7.2).
- **Mint-condition carrier (F-3):** the ARM TOOL stamps `spec["provenance_ok"] = True` (+ `provenance_class`) AFTER the gate passes; the post-turn drain mints `ENSEMBLE_RESTART_LIVE=1` **IFF present**; absent ⇒ no mint ⇒ guard exits 78 (fail-closed). **Invariant:** marker present ⇒ gate passed at arm time; daemon death between arm and drain ⇒ boot reconcile closes as `restart_abandoned` — never executed.
- **Strict live-parent inheritance** at claim time (parent-window tail bounded by TTL + depth + budget).
- **Gate predicate:** `restart_allowed = provenance_ok(turn) ∧ env-self-match (D-FA2.4 order, unchanged) ∧ pipeline rails (§4a) ∧ cooldown (§4) ∧ anti-flap rails (§4) ∧ reason non-empty (§4)` where `provenance_ok = direct window live ∨ inherited record live`. `dry_run=true` evaluates the full predicate and every rail, reports the verdict, and returns WITHOUT arming (N2). Reuses `classify_user_origin` (pure) and the TTL parse helpers (`upgrade_journal.py:170-190`) — **never** the upgrade gate's store.
- **Policy line (vector g):** provenance is an **authorization-to-act channel, NOT intent-verification**. Receipt + journal name root source + message id + depth + class; intent is covered by the journaled reason, end-of-turn execution, health-gated report, and budget rails.

### 2.5 Anti-spoof vector table

| Vector | Verdict | Evidence + mitigation |
|---|---|---|
| (a) Agent-authored content mimicking a user message | **BLOCKED (structural)** | Classification reads the queue row's daemon-written `source`, never content; no source param on `send_message` (`instance.py:3564-3593`). Never add content-based origin detection. |
| (b) Self-send / sibling laundering | **BLOCKED (closure + decay)** | Inherited stamp exists only with a live caller-side root; pure-agent chains never arm. Depth cap 4 + monotonic TTL. |
| (c) Replay / revive | **BLOCKED** | Per-dispatch in-memory; restart wipes; revive = fresh dispatch; stamp binds to the stamping dispatch's `message_id`. |
| (d) `system:*` / job-queue turns | **BLOCKED** | Reserved prefixes fail before registry lookup; inherit lane keys strictly on `internal_agent:<caller>` + live parent entry (N1). |
| (e) Cross-session / cross-project injection | **BLOCKED** | Instance-UUID-keyed in-process store; single-host trust model (ADR-015 R10). |
| (f) Chat-source users | **ACCEPTED BY DESIGN** | Daemon-built sources; same trust set as ratified upgrade factor-2. |
| (g) Prompt-injection inside a genuine user message | **MITIGATED BY DISCLOSURE, NOT BLOCKED** | The turn IS user-originated; disclosure + rails cover intent. Documented residual. |
| (h) 🟡 `"api"` auto-dispatch fallback | **FOLDED IN (hardening; N4 disclosure)** | `instance.py:952-955` fallback becomes a reserved internal source. **This removes an input the UPGRADE gate could previously receive** (an agent-path-minted direct window) — a strictly-STRENGTHENING change to upgrade factor-2 inputs, disclosed in §1.2/§2.6 and pinned upgrade-side (T0a′). T0b asserts the path. |
| (i) 🔴→BLOCKED Wake-delivery laundering (C-2) | **BLOCKED BY REV-2 FIXES** | Chain + fixes as adjudicated: reserved `system:post_restart_arm_notify` for restart-kind wakes; `provenance_class` on `PendingWake`; re-stamp deleted for restart-kind; T0b pin. Upgrade factor-2 write exposure = pre-existing, leader-filed critical note. |
| (j) 🔴→BLOCKED Mixed-kind wake coalescing (F-1) | **BLOCKED BY REV-3 FIX** | `_coalesce_wakes` partitions groups by `wake.kind` (adjudicated over the any-restart-member rule, which would suppress upgrade-lane re-stamp semantics). T0b mixed-group pin. |

### 2.6 Upgrade-gate isolation — two-layer statement

- **Decision layer (gate reads): AIRTIGHT, kept so by rule.** Restart gate reads both stores through a NEW helper; upgrade factor-2 keeps **explicit direct-store access** (`upgrade_tools.py:2783-2784`) and **NEVER calls the restart-side helper** (directional rule — T0a). Factor 3 binds the nonce to the direct-window row (`:2906-2919`). **One disclosed delta (N4):** the row (h) hardening changes the SET of inputs that can reach factor-2 (the `"api"` auto-dispatch fallback disappears) — strictly strengthening; no weakening path exists; pinned T0a′.
- **Write layer (window minting): scoped.** Restart-kind wakes: reserved source, no re-stamp, kind-partitioned coalescing. Upgrade-lane write exposure (upgrade-kind wake `"api"` fallback) is pre-existing, out of scope, leader-filed critical note.

---

## §3 Deferred Execution Choreography (ordering guarantee)

### 3.1 Reused machinery (the promote lane — do not invent a second path)

Arm: tool → pipeline-lock acquire (`upgrade_tools.py:2433` → `upgrade_journal.py:675`) → durable `in_flight` + `pending_op` writes (`:2470`, `:2475`; `PendingOp.kind` carries `"restart"` at `:793`) → arm-notify wake write in the SAME journal envelope (`:2481` → `:1308` → `:986`) → in-memory execution marker + spec (`:2513` → `manager.py:4837`) → return `RESTART SCHEDULED`.
Execution: turn ends → post-graph `finally` drain seam (`instance_messaging.py:5216`, shielded `:1983`, one-shot marker pop `manager.py:4910`) → spawn-refusal guard (`manager.py` ~`:4980`) → daemonized executor `systemd-run --unit=<prefix>-<run_id> --wait --collect` (`upgrade_journal.py:2270`) → `scripts/upgrade/restart.sh` (preflight `:119`; lock adoption `:160`; supervision preflight `:192`; grace `:204-212`; stop `:216`; start `:223`; gate `:236`; journal finalize `:258`).
Boot: reconcile (`daemon/api.py:2439`) → wake sweep (`:2450`, def `upgrade_journal_sweep.py:1063`, BEFORE auto-continue `:2477-2492`) → arm-notify delivered (`upgrade_journal_sweep.py` ~`:1330`) → auto-continue (boot orphan sweep, v0.25.3).

### 3.2 Ordering guarantee — precise statement

1. **HARD (today):** the stop can never precede the turn's completion — executor spawns only from the turn's own post-graph `finally` (`instance_messaging.py:5216`), marker one-shot.
2. **SOFT (disclosed):** the reply dispatches AFTER the drain (`:5231→:5244`); the stop is held off only by `GRACE_S` + `wait_inflight` grace (10s, `manager.py:13716`). **Grace is a head-start, not a bound.** Dispositions: restart-lane grace default **5s → 15s**; receipt discloses best-effort reply delivery (§7.2); executor journals a TERM-while-dispatch-pending note; drain-below-dispatch restructure stays in the improve lane.

### 3.3 Restart-flavor deltas vs promote

| Delta | Decision |
|---|---|
| **Live guard env (C-1/F-3/F-4)** | Restart-scoped `ENSEMBLE_RESTART_LIVE=1` minted **only on provenance-passed arms** — arm stamps `spec["provenance_ok"]` post-gate, drain mints IFF present via the existing `extra_env` explicit-extra merge, absent ⇒ guard exits 78 fail-closed (`restart.sh:92` → `lib.sh:715-728`). **The acceptance branch accepts ONLY `ENSEMBLE_RESTART_LIVE`** (no cross-acceptance); `require_live_guard` byte-identical; `ENSEMBLE_UPGRADE_LIVE` never reused. **Guard-trip journal event (F-4):** `live-restart-refused` written best-effort **lock-free** (guard trips pre-adoption while the arm holds the lock — never acquires it); its notification rides the kind-agnostic reaper's `executor_exit`; **no terminal-set membership**. |
| No version swap / no symlink change / no migration ops | Version-verify becomes an **identity** assertion. |
| **No rollback lane — recovery paths (rev-4 corrected)** | A failed restart has no automatic recovery: recovery = (i) the launcher's burst/backoff supervision, and (ii) the **operator escape hatch — see §4(f) for the authoritative, correctly-scoped form** (unit scope DERIVED from the scripts' supervision classification at render time — SYSTEM-level `ensemble-main.service` on the live host; NEVER `systemctl --user`, which cannot even connect there) — which MUST appear verbatim-derived in the arm-notify body (§5.3). |
| Health gate | Report-only `/livez` (180s) **+ `/readyz` (120s, NEW for this lane)**, config `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`. **Data carrier (N3):** the per-gate results (pass/fail, latency, budget consumed per probe) ride the journal **`detail` channel** of the `restart_gate` history event; `_format_wake_body` reads them from there — the notify body carries real per-gate results, not a summary verdict alone. Result also lands in `data/upgrade.log` + daemon log. **Rationale (C-3-corrected):** the existing `halt` event already fires the wake on failure (`restart.sh:262-271`) — `restart_gate` exists for explicit gate-outcome classification. |
| **Terminal events (C-3/F-4/F-5 — kind-aware, final shape)** | Constants: `_TERMINAL_EVENTS` **untouched** (9 members, `upgrade_journal.py:1581-1591`; promote-lane `reconcile_pending_op` `:1677-1697`; `:1596-1616` comment: a restart event MUST NEVER close a promote op). Concatenated **`RESTART_TERMINAL_EVENTS = ("restart_gate", "restart_abandoned", "stop_forced_kill")`**. `WAKE_TERMINAL_EVENTS` (`:1623`) = `_TERMINAL_EVENTS + ("restart",) + RESTART_TERMINAL_EVENTS` (superset, per F-2's rewritten assertion). **Reader (F-5, MANDATED — kind-aware):** `wake_terminal_event_after(journal, armed_at, kind)` selects the terminal set BY `wake.kind` (`PendingWake.kind`, `:934`): promote-kind wakes match ONLY `_TERMINAL_EVENTS + ("restart",)`; restart-kind wakes match the full superset (**N6 asymmetry, accepted + noted §10 row 4** — a promote-terminal event landing after a restart arm could terminalize the restart wake; low risk, conservative direction: it can only complete a wake EARLIER, never block it). **`live-restart-refused`: journal event class ONLY, no terminal-set membership** — rides `executor_exit`. **Outcome labels (F-4):** `_OUTCOME_LABELS` entries ×4 (`upgrade_tools.py:1179`). Writer anchor: `stop_forced_kill` written by `scripts/stop-ensemble.sh:485-501`. |
| Graceful stop | Unchanged SINGLE-TERM contract (`lib.sh:3570` → `stop-ensemble.sh`); KILL escalation bounded (`WAIT_S=70`; unit kill grace 10s). Forced KILL voids the in-flight checkpoint write ⇒ `stop_forced_kill`. |
| Transient unit name | Parameterized `unit_prefix` — restart passes **`ensemble-restart-<run_id>`**. **V-7 contract:** `UNIT_NAME_PREFIX` at **4 sites** (`upgrade_journal.py:1900/:2116/:2197/:2362`); every site accepts the parameterized prefix; reaper/boot-sweep matching for the new prefix **unverified** (residual) — enumerate sites in the PR, define the `ensemble-{upgrade,restart}-<run_id>` family contract, unit-name test. |
| **Stale arms (N5 — premise corrected)** | An abandoned restart arm **NEVER expires silently — it wedges `pipeline-busy` indefinitely** (the surviving `pending_op` blocks every new arm; the rev-2 "1800s silent expiry" premise was wrong). The boot-lane closure is therefore a **real liveness fix, not notify hygiene**: boot reconcile explicitly closes an unexecuted `kind=restart` `pending_op` with journal `restart_abandoned` + wake-abandonment notify, covering **BOTH variants** — (a) no `in_flight` (armed, never executed) and (b) `in_flight` still set with no live executor (crashed mid-execution). Periodic-tick skip at `:1690` stays; boot lane only. Never auto-execute a stale arm. **F-3 invariant:** daemon death between arm and drain ⇒ `restart_abandoned`, never execution. (Launcher `_journal_sweep` restart-branch e2e remains a carried residual.) |
| Double-arm | The journal lock IS the arm-level mutex (second arm ⇒ `pipeline-busy`). Belt-and-braces: the tool refuses if the marker map already holds an entry for the same instance. Backstop: marker overwrite + `run_id` mismatch ⇒ `executor_orphaned` (`manager.py` ~`:4980`). |

### 3.4 Executor environment contract

`EXECUTOR_ENV_ALLOWLIST` governs **ambient** env (restart inherits nothing live-ish by default); the **explicit `extra_env` merge** is the only sanctioned lane for attestation vars. Promote threads `ENSEMBLE_UPGRADE_LIVE` via `_verified_arm_extras`; restart threads `ENSEMBLE_RESTART_LIVE` gated on `spec["provenance_ok"]` (F-3), consumed only by the restart-scoped acceptance branch. The two vars stay lexically and semantically disjoint forever (T0c + §10 risk 11).

---

## §4 Safety Rails (each justified, each configurable where a knob is safe)

| Rail | Mechanism | Justification | Default | Config | Refusal token |
|---|---|---|---|---|---|
| (a) Pipeline-in-flight | Check in order: journal `in_flight` → `pending_op` → `rollback.lock.d` held → `.adoption_in_progress` marker → `pipeline_settled` (mirrors `upgrade_tools.py:2331-2400`) | The promote/adoption interlock; a restart concurrent with a promote could kill the promote mid-adopt | all 5 blocking | **no knob** | `pipeline-busy` + new `adoption-in-progress`, `pipeline-not-settled` |
| (b) Cooldown | Read journal `cooldown_until`; refuse while future; **fail CLOSED on unparseable** (`upgrade_tools.py:2963` semantics) | A leftover cooldown signals recent instability; unparseable = untrusted = refuse | — | — | `cooldown-active` (receipt renders ACTUAL state) |
| (c1) Minimum uptime | `now() − get_boot_epoch()` (`daemon/services/boot_epoch.py:70`) ≥ threshold; **fail CLOSED when unavailable** | Post-boot restart re-enters the boot-storm window; 600s = launcher fresh-instance horizon (`launcher.sh:59`) | **600s** | `ENSEMBLE_RESTART_MIN_UPTIME_S` | `restart-too-soon` |
| (c2) Restart budget | **True sliding window** — `restart_window.events[]` timestamp list in the journal, **Python-only** (F-6); count within `now − window`; prune on every read/write. **Deliberately NOT the fixed `JOURNAL_EMPTY` 24h bucket** (`upgrade_journal.py:157`) which permits 2×N across a boundary | Counter must survive the restart it meters; PG may be mid-restart | **3 / 24h** (matches `ROLLBACK_CAP_24H=3`) | `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400` | `restart-budget-exceeded` |
| **Budget envelope (V-1)** | Increment **inside the arm's lock-holding try, SAME envelope as `in_flight`+`pending_op`+wake, FIRST**; **abandoned arms consume budget; no refund** | Crash-ordering safety (over-count = safe direction) | — | — | — |
| (d) Reason required | `reason: str = ""`; empty/whitespace ⇒ typed refusal; persisted form charset-constrained (§6.2); echoed into journal + arm-notify | The only post-hoc explanation of an operator-visible discontinuity | mandatory (min 3 chars) | — | `reason-required` |
| (e) In-flight work | Graceful SINGLE-TERM stop (bounded 70s) + checkpoint freeze + **boot orphan sweep v0.25.3** (`daemon/api.py:2477-2492` → `auto_continue_boot_pass.py`; **predicate home: `find_orphaned_live_turns`, `daemon/repositories/task/repository.py:1165+`**; StaleTaskRecovery backstop boot+10min, `daemon/config.py:1746/:1812/:1821`). **THREE-SIGNAL conditioned: task-less RUNNING ∧ healthy checkpoint ∧ ACTIVE JobItem — a bare `send_message` turn (no JobItem) is NOT re-dispatched** — the most load-bearing assumption of this rail, disclosed in the arm-notify/report and pinned by test. **Explicitly NOT protected:** mid-tool-call side effects outside checkpoints; bash `setsid` orphans; background processes (proc cleanup best-effort, `manager.py:13688`; auto-kill-bg-processes scopes root-instance completion); non-checkpointed external state | Resuming killed turns is safe only because checkpoints persist AND the three-signal sweep re-dispatches them | on (existing kill-switch) | existing | advisory `busy-check` |
| **(f) Post-boot health gate + THE operator escape hatch (rev-4 BLOCKING fix)** | Report-only `/livez` + `/readyz`; failure ⇒ `restart_gate` event (with per-gate `detail`, N3) + notify + logs; **no auto-rollback**. **Operator escape hatch — authoritative form:** the notify body and this doc render the service-restart command with its **unit scope DERIVED at render time from the same supervision classification the scripts use** (`scripts/upgrade/lib.sh` unit classify; project runbooks `resource-hygiene-operator-checklist.md:60,71,256`, `install-dir-protection-layer1-arm.md:168` all use system scope). **Never hardcode a scope; never default to `--user`.** On the live host the classification resolves to the SYSTEM-level unit: `sudo systemctl restart ensemble-main.service` (`/etc/systemd/system/ensemble-main.service`, cgroup `/system.slice/ensemble-main.service`; **NO user-level unit exists — `systemctl --user` cannot even connect on this host**; the rev-1..3 `systemctl --user` form was verified broken by the approver). Alternative: `bash launcher.sh restart_current`. **Re-run claim corrected (was FALSE in rev ≤3):** the script-path re-run `ENSEMBLE_RESTART_LIVE=1 bash scripts/upgrade/restart.sh live --run-id <r-…>` is **re-runnable ONLY while `pending_op` + lock survive** — `restart.sh:131-175` refuses (exit 78) when the op is absent/mismatched or the lock is missing, and restart.sh's own failure path CLEARS `pending_op` and releases the lock. In the worst case this hatch exists for (**restart executed → gate failed → daemon down**) the op/lock are already gone, so the **correctly-scoped service restart / launcher is the ONLY executable recovery path** — which is why the scope derivation above is load-bearing and must be verified on the live target | Same version — nothing to roll back to; auto-rollback would flap a healthy-ish daemon. The hatch must be copy-paste executable on the live host | report-only | `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1` | `restart-gate-failed` (notify/journal) |

Also reused unchanged: restart-under-burst-abort (`upgrade_tools.py:1551`).

---

## §5 Boot Arm-Notify (incl. applied-overrides enumeration)

### 5.1 Mechanism (reused, fail-open)

Wake record at arm (`upgrade_journal.py:864-960`, `arm_pending_wake` `:986`; kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY`, default ON); boot sweep (`daemon/api.py:2450` → `upgrade_journal_sweep.py:1063`) BEFORE auto-continue; delivery `enqueue_message(arming_instance_id, priority=2, metadata.system_context.kind="post_restart_arm_notify")` (~`:1330`); coalesce cap 16; abandon grace 600s; ari front-door fallback. Restart-kind wakes: source **`system:post_restart_arm_notify`**, re-stamp deleted, **coalescing partitioned by `wake.kind`** (§2.5 rows (i)/(j)). **V-8:** `get_last_applied_resolution()` (`settings_service.py:426-440`) is a module global — `_format_wake_body` (`:547`) / `_format_coalesced_wake_body` (`:574`) import the getter directly; `None` (**unavailable**) ≠ zero-divergence dict (**no overrides**) — never a bare "0 applied". Notify failure never blocks boot (three wrap levels, `api.py:2453-2459`).

### 5.2 Applied-overrides enumeration

- **Where overrides live:** `app_settings` rows (`daemon/models/app_settings.py:70`). **How boot applies them:** `apply_db_settings_overrides` (`daemon/services/settings_service.py:807`) called ONCE from lifespan (`daemon/api.py:510`); SSOT ladder **DB > env > yaml > default** (`resolve()` `:462`); live `Config` mutated in place (`:889-893`). `worker_pool_size` takes effect at `PoolOrchestrator.setup` boot read (`daemon/services/pool_orchestrator.py:206`). All 6 managed keys carry `requires_restart=True` (`settings_service.py:155-320`).
- **Arm-time pending set** = `requires_restart=True` ∧ running value ≠ DB override value (the `divergent` flag, `settings_tools.py:358`; `daemon/routers/settings.py:898-928`), captured **inside the lock-holding try** into `restart_audits[run_id].overrides` in the SAME envelope. (Bounded PG read inside the lock — accepted.)
- **Access seam:** `SettingsService` is a router module-global (`daemon/routers/settings.py:143-154`) — wire onto the manager at `api.py` (mirroring `set_project_repository`, `:2740-2742`); the tool reads `manager._settings_service`.
- **Applied ⇔** post-boot live value == arm-time DB override value. **Failure detectable:** invalid row ⇒ DB tier skipped (`settings_service.py:511-556`) ⇒ `source != "db"` ⇒ *not applied* + winning tier. **Late writes / cleared:** diff vs post-boot row ⇒ `late-write` / `cleared`.
- **Render format:**
  ```
  applied overrides (2):
    limits.worker_pool_size: 5→7 (applied)
    limits.chat_worker_pool_size: 7→9 (not applied — resolved tier=env)
  ```

### 5.3 Notify body contents (contract)

Restart outcome (`restart_gate` result **with per-gate `/livez`+`/readyz` detail from the journal `detail` channel — N3**) · version now live (identity check) · applied-override list (§5.2 render) · reason echo · provenance class + root source · **in-flight-work disclosure** (three-signal JobItem boundary sentence) · **escape-hatch line rendered with the DERIVED unit scope (§4f — system-level `sudo systemctl restart ensemble-main.service` on the live host; `bash launcher.sh restart_current` alternative; the script re-run noted as arm-intact-only)** · `run_id`.

---

## §6 Audit Journal

### 6.1 Decision: **(A) REUSE the upgrade journal** — no new table, no migration

**Corrected premise:** the "upgrade journal" is a **file-backed JSON document** at `<install_dir>/releases/state.json` (`upgrade_journal.py:250-251`; atomic temp+fsync+`os.replace` `:326-402`). The discriminator is `PendingOp.kind` (`"restart" | "promote"`, `:793`). The "every new SQLModel table requires its own `.sql` migration" rule applies only to the REJECTED option (B).

Why (A) wins: (1) **no dual write** — arm writes `pending_op` + `pending_wakes` + budget increment in one locked envelope (`upgrade_tools.py:2476-2484`, ADR-039); (2) **zero migration cost; additive-only delta keeps `rollback_safe=true`** (105 `.sql` files untouched — **N8: this statement must appear verbatim in the implementation PR description**); (3) sweep/settle semantics exist; (4) a new top-level key is invisible to `lib.sh`'s field-targeted splice (ADR-034; unknown fields carried, `upgrade_journal.py:34-38`).

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

Sibling key `restart_window: {"events":["ISO", …]}` — Python-only sliding-window budget list. Arm writes static fields + pending override set + **budget increment (FIRST, same envelope)**; boot-settle updates `executed_at`/`outcome`/`overrides[].status`/`notify` in place.

**Reason charset constraint (V-11):** persisted reason constrained at the tool boundary — printable, no newlines, no splice-sentinel class, ≤200 chars.

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

(`dry_run=true` returns the same verdict block with `RESTART DRY-RUN` verdict lines and **no** `run_id`/journal line — zero mutation, N2.)

### 7.3 Typed refusals — house style, single writer

One writer: `_refusal()` (`upgrade_tools.py:1425-1433`) ⇒ `Error: RESTART REFUSED — reason=<token>: <message>`; journaled `refusal` (lock-busy `pipeline-busy` carve-out `:1394-1400` preserved). **No second vocabulary.**

| Commission token | House token | Status |
|---|---|---|
| `refused: pipeline-busy` | `pipeline-busy` (+ `adoption-in-progress`, `pipeline-not-settled`) | exists + 2 new |
| `refused: cooldown` | **`cooldown-active`** (`:2963`) | NEW check for restart |
| `refused: no-user-provenance` | `no-user-provenance` (sub-cause + observed-source diagnostic) | NEW |
| `refused: reason-required` | `reason-required` (via `reason: str = ""`) | NEW |
| — | `restart-too-soon`, `restart-budget-exceeded` | NEW |

**`live-restart-refused`: removed from the TOOL vocabulary (`:2570-2576`), re-homed as the script-guard JOURNAL event class — notification rides `executor_exit`; no terminal-set membership.** `RESTART_TOKENS` pin (`test_upgrade_tools.py:1537/3468-3480`) extended with the 8 restart-lane tokens.

### 7.4 Docs surface (every file whose text must change)

| File | Change |
|---|---|
| `daemon/tools/upgrade_tools.py:2318-2327` | live outright-refusal string → provenance-gated path |
| `daemon/tools/upgrade_tools.py:2545-2585` (`_full_doc_`) | "LIVE: refused outright…" → new contract (receipt fields, refusal tokens, provenance requirement, executor live-guard note) |
| `daemon/tools/upgrade_tools.py:11,112,131` | module docstring + checklist note |
| `agents/maintenancer/meta.json:13` | lift `system_restart` from `tools.deny` |
| `agents/maintenancer/rule.md:8,68` | restart policy rewrite — **must preserve audit pin-17** (`tests/test_chart_image_delivery_audit.py:467-483`) |
| `agents/maintenancer/tools_note.md:20,77` | deny-table row + "structurally excluded" prose |
| `agents/maintenancer/README.md:37,74` | "the user pulls the restart lever" + skill one-liner |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | Boundary / Contract / Focus-area 5 / report format |
| `agents/ari/tools_note.md:421` | still teaches the old "refused outright" contract — rewrite; provenance-not-identity stated |
| `agents/maintenancer/ROLLOUT.md:52,269,286,381,444` | deny-list references (optional) |
| `docs/runbooks/post-restart-arm-notify.md` §1 | "Live never records (invariant 8)" + ADR-044 paragraph — rewrite **restart-only**; two gates never conflated |
| `docs/runbooks/upgrade-drills.md:468/535` | still state live restart refused outright — **REWRITE these passages** |

**V-12 caveat:** the template edit does NOT update the seeded skill-bank row (`faf6e86e…`, `bank_clone`; `skill_seed_service.py:325-352`) — the developer must execute the bank-row update path; a T9 case pins it.

### 7.5 Seal re-pin (same-merge discipline)

`agents/maintenancer/rule.md` is seal-pinned (`tests/test_chart_image_delivery_e2e.py:1389`, tripwire `:1423`) ⇒ same-merge re-pin with git-log attribution, targeted seal test, provenance-labeled commit. **Pin-17** (`tests/test_chart_image_delivery_audit.py:467-483`) constrains the rewrite — preserved and pinned by T9. **F-2 (second site, same discipline):** the sweep-test mutation guard (`tests/unit/services/test_post_restart_arm_notify_sweep.py:856-870`) assertion REWRITE is an adjudicated same-merge step — expected `set(_TERMINAL_EVENTS) | {"restart"} | set(RESTART_TERMINAL_EVENTS)`, `len==9` + absence pins verbatim. ⚠️ **Adding the 3 names to `_TERMINAL_EVENTS` to restore the old equality is the C-3 trap — forbidden.**

---

## §8 Test Plan (7 mandated cases + restart-sim + round-2/3/4 pins)

Primary surfaces: `tests/unit/tools/test_upgrade_tools.py` (`TestSystemRestartMatrix:1928`, `TestLiveThreeFactorGate:2461`, `RESTART_TOKENS` `:1537/3468-3480`), `tests/unit/tools/test_post_restart_arm_notify_journal.py:772`, `tests/unit/services/test_post_restart_arm_notify_sweep.py` (`:844-869`, `:856-870`), `tests/job_queue/test_post_restart_arm_notify_{routing,edge_cases}.py`, `test/drills/post_restart_arm_notify_drill.sh` + `wake_drill_driver.py:519` + smoke, `tests/mocks/upgrade_tools_live_safety_mock.py:516-519`.

**T0 — precondition pins:**
- (a) Upgrade factor-2/3 byte-identity; directional isolation (factor-2 never calls the restart helper); factor-3 nonce binding. **(a′ N4) Upgrade-side strengthening pin:** with the hardened `instance.py:952-955` fallback, an agent-path auto-dispatch NO LONGER stamps `"api"` — the upgrade gate can no longer receive that input (intentional, strengthening; asserted explicitly, not silently).
- (b) Terminal-event mutation guard (F-2-rewritten): names in `WAKE_TERMINAL_EVENTS`, absent from `_TERMINAL_EVENTS`, `len==9`, expected equality with `RESTART_TERMINAL_EVENTS` unioned.
- (b′) Kind-aware reader (F-5) e2e: promote armed → `stop_forced_kill` lands → **promote wake NOT completed**; promote `pending_op` NOT closed by any restart event.
- (c) Executor env negative: restart lane NEVER sets `ENSEMBLE_UPGRADE_LIVE`; `ENSEMBLE_RESTART_LIVE` minted only when `spec["provenance_ok"]` present.
- (d) Wake-provenance side-effect: restart-kind wake delivery mints NO user window.

**T0b — spoof vectors §2.5 (a)–(j):** structural pins + row (h) path + row (i) second-restart-on-wake-turn refused + row (j) mixed-group pin + **N1 pin: `internal_invoke_and_wait:{parent}` dispatch NEVER inherits** + **N2 pin: `dry_run=true` on live returns the full verdict block with ZERO journal mutation / no lock / no wake**.

| # | Case | Approach |
|---|---|---|
| 1 | genuine-user turn → scheduled | seed `_user_origin_windows[iid]` (fixture `:2461`); assert receipt + `provenance: class=` + no REFUSED; inherited variant via `internal_agent:<caller>` |
| 2 | agent-initiated → refused | no window ⇒ `no-user-provenance` + diagnostic; ZERO journal mutation |
| 3 | promote in-flight → refused | `test_pipeline_busy_armed:2374` shape ⇒ `pipeline-busy`; + `adoption-in-progress`, `pipeline-not-settled` variants |
| 4 | cooldown/budget → refused | `test_cooldown_active_armed_refused:2339` mirror (+ unparseable fail-closed); budget cap; **window-boundary (T and T+window−ε both counted)**; **crash-ordering + concurrent arms**; min-uptime; epoch-unavailable fail-closed |
| 5 | post-restart notify contents | `_format_wake_body` unit: applied/not-applied/tier render; `None` ≠ zero-dict; **per-gate livez/readyz detail rendered from the journal `detail` channel (N3)**; `_OUTCOME_LABELS` ×4 (labels not raw keys); `restart_audits` record + boot-settle update; pin-17 preserved; seeded bank row updated; JobItem-boundary disclosure present; **escape-hatch line carries the DERIVED unit scope (system-level form on the classification fixture — never `--user`) and the arm-intact-only script re-run caveat (rev-4)**; drill D7 + D1–D6 grep extension |
| 6 | deferred execution ordering | `test_armed_demo_returns_scheduled_no_spawn:2396` mirror: armed, NO executor spawned, `executes_after_turn: yes` |
| 7 | **REAL restart-simulation** | drill fake-live executor-crossing leg: provenance-seeded arm → `spec["provenance_ok"]` → `ENSEMBLE_RESTART_LIVE` through the real executor env → acceptance branch → real stop/start → wake with applied overrides + `restart_gate` (would have caught C-1). Mirror `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py` + `tests/test_promote_cgroup_survivorship.sh`. ⛔ `mktemp` FAKE_HOME + escape guard |
| T8 | Rails extras | same-instance re-arm refused; **stale-arm boot reconcile closes BOTH variants (no-`in_flight` AND `in_flight`-still-set) ⇒ `restart_abandoned` + notify — and unblocks the wedged `pipeline-busy` (N5)**; budget survives simulated restart; `stop_forced_kill` from `stop-ensemble.sh:485-501`; unit-name test; **escape hatch: scoped-form service restart rendered from the classification (system-scope fixture); script re-run succeeds while arm intact, refuses 78 after failure-path cleanup (rev-4)** |
| T9 | Expectation flips + surface enumeration | `:1936`, `:1500`, `:1397`, integration docstring (upgrade intact); V-13 surfaces; ari `tools_note.md:421`; drills `:468/535` rewrite; audit pin-17; F-2 mutation-guard rewrite green |

---

## §9 File-Level Implementation Breakdown (ordered, for the developer)

1. **`daemon/manager.py`** — init `_inherited_provenance_windows` (adjacent `:1507`); inherit lane in the funnel (`:9395-9402`) — **`internal_agent:` prefix only (N1); `internal_invoke_and_wait:*` clears**; drain mints `ENSEMBLE_RESTART_LIVE=1` IFF `spec["provenance_ok"]` via the `extra_env` explicit-extra merge (`:4934-4940` region; promote extras `:4967-4970` untouched); wire `manager._settings_service` (`api.py` near `:2740-2742`).
2. **`daemon/tools/instance.py`** — `:952-955` `"api"` fallback → reserved internal source (**disclosed upgrade-strengthening, N4 — T0a′ pin**).
3. **`daemon/tools/upgrade_journal.py`** — `restart_audits` + `restart_window` (Python-only) via `ensure_extensions` (`:404-430`); `unit_prefix` across **all 4 `UNIT_NAME_PREFIX` sites** (`:1900/:2116/:2197/:2362`); `RESTART_TERMINAL_EVENTS` + `WAKE_TERMINAL_EVENTS` superset + **kind-aware `wake_terminal_event_after(journal, armed_at, kind)`**; `_OUTCOME_LABELS` ×4 (`:1179`); **boot-lane abandoned-closure for BOTH stale variants (N5)** (near `:1690`, boot only); `PendingWake` gains `provenance_class` + root fields.
4. **`scripts/upgrade/lib.sh`** — restart-scoped acceptance branch for **only** `ENSEMBLE_RESTART_LIVE` (`:715-728` consumer); `require_live_guard` byte-identical. (`restart_window` needs NO shell splice — Python-only, F-6.)
5. **`scripts/upgrade/restart.sh`** — grace default 15s; acceptance of `ENSEMBLE_RESTART_LIVE` only (`:92`); guard-trip `live-restart-refused` best-effort lock-free; report-only `/readyz`; `restart_gate` on success AND failure **with per-gate detail in the `detail` channel (N3)**; `unit_prefix` passthrough; TERM-while-dispatch-pending note.
6. **`scripts/stop-ensemble.sh`** — `stop_forced_kill` event at `:485-501`.
7. **`daemon/config.py`** — `ENSEMBLE_RESTART_MIN_UPTIME_S=600`, `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400`, `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`.
8. **`daemon/tools/upgrade_tools.py`** — replace live refusal (`:2305-2320`): provenance helper → rails → arm (`spec["provenance_ok"]` stamp, override capture inside lock via `manager._settings_service`, `_capture_user_source` `:1244-1259` extended, budget increment-first, reason constraint, **`dry_run` verdict-only path — zero mutation, N2**); extended receipt; tokens per §7.3; `_full_doc_` rewrite; module docstring; `_OUTCOME_LABELS` ×4.
9. **`daemon/services/upgrade_journal_sweep.py`** — `_coalesce_wakes` partitions by `wake.kind`; restart-kind source `system:post_restart_arm_notify`; restart-kind re-stamp deleted (`:1305-1317`); wake body per §5.3 **including the DERIVED-scope escape-hatch rendering (rev-4 — render from the lib.sh supervision classification, never a hardcoded scope)**; module-global resolution getter, None≠dict; fail-open preserved.
10. **`agents/maintenancer/meta.json`** — lift `system_restart` from `tools.deny`.
11. **`agents/maintenancer/rule.md`** — policy rewrite preserving pin-17; same-merge seal re-pin; **F-2 same-merge step: sweep-test mutation-guard assertion rewrite**.
12. **Docs** — maintenancer `tools_note.md` + ari `tools_note.md:421`, `README.md`, `ROLLOUT.md` (optional), `skills-template/restart-upgrade-ops.md` + seeded skill-bank row update, `docs/runbooks/post-restart-arm-notify.md` §1, `docs/runbooks/upgrade-drills.md:468/535` rewrite.
13. **Tests** — per §8 (T0/T0b first incl. T0a′, F-2 rewrite, F-5 e2e; then 1–7 with fake-live D7 leg; T8 with the N5 both-variant closure + rev-4 hatch assertions; T9). **PR description must state (N8): no `.sql` migration ⇒ additive-only delta ⇒ `rollback_safe=true`.**

---

## §10 Risks & Mitigations

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | Spoofable provenance ⇒ autonomous live restarts | 🔴 | Mechanism B daemon-minted only; structural classification; TTL + depth + per-turn clear + restart-wipe; §2.5 rows (i)/(j) closed; T0/T0b |
| 2 | Provenance propagation weakens the upgrade gate | 🔴 | §2.6 two-layer; directional rule + T0a; N4 strengthening disclosed + T0a′; upgrade write exposure = leader-filed note |
| 3 | Executor live-guard DoA (C-1 class) | 🔴→closed | `spec["provenance_ok"]` carrier → `ENSEMBLE_RESTART_LIVE` via `extra_env`; T0c + fake-live D7 |
| 4 | Terminal-event mutation closes a promote op (C-3 class) | 🔴→closed | `_TERMINAL_EVENTS` untouched; `RESTART_TERMINAL_EVENTS`; kind-aware reader; T0b guard + promote-not-completed e2e. **N6 accepted asymmetry:** restart-kind wakes match the FULL superset (a promote-terminal event could terminalize a restart wake) — conservative direction only (earlier completion, never blocking); low risk |
| 5 | Wake delivery re-mints direct provenance (C-2/F-1) | 🔴→closed | Reserved source; re-stamp deleted; kind-partitioned coalescing; T0d + T0b rows (i)/(j) |
| 6 | Reply lost in the soft grace window | 🟡 | Grace 15s (head-start, disclosed); receipt disclosure; TERM-pending note; drain-reorder in improve lane |
| 7 | **Failed restart: daemon down, no rollback lane** | 🟡 | **Rev-4-corrected:** the escape hatch is executable on the live host — unit scope DERIVED from the scripts' supervision classification (system-level `sudo systemctl restart ensemble-main.service` on live; `bash launcher.sh restart_current` alternative); the script re-run is arm-intact-only (`restart.sh:131-175` refuses otherwise; failure path clears op+lock) and is documented as such; rendered in the notify body; T8 asserts the scoped form + the caveat |
| 8 | Unprotected in-flight side effects + JobItem boundary | 🟡 | §4e gap list + three-signal predicate (home `find_orphaned_live_turns`) disclosed + pinned |
| 9 | Rails fail open on missing data | 🟡 | Epoch/unparseable-cooldown fail closed; budget journal-durable, increment-first, sliding window |
| 10 | Journal RMW growth / splice collision | 🟢 | Cap 50; reason charset-constrained |
| 11 | Two live-guard vars drift together | 🟡 | Lexical/semantic disjointness (§3.4); T0c; acceptance takes ONLY the restart var |
| 12 | `unit_prefix` reaper/boot-sweep matching unverified | 🟡 | 4-site enumeration + family contract + unit test; residual until verified |
| 13 | Docs drift / two gates conflated | 🟡 | §7.4 list; bank-row update + T9; seal re-pin + pin-17; F-2 rewrite step |
| 14 | Stage-only ride on concurrent staging worktree | 🟢 | v0.25.5 expected; additive-only delta |
| 15 | **Carried residuals (council round-2 list, unchanged)** | 🟢 | `_capture_user_source` body; drain-vs-reply ordering `:5216/:5231/:5244`; silent-resume × inherited-store (fail-closed default); reaper unit matching under `ensemble-restart-*`; `_format_wake_body` bodies `:547/:574`; guard-trip event-write self-locking pre-adoption (lock-free mandated, verify); exhaustive `additional_kwargs["source"]` enumeration; launcher `_journal_sweep` restart-branch e2e |

---

## Appendix — Provenance of this document

- **Rev 1:** worker reports `architect-worker-restart-provenance` (`c5a12c21`), `architect-worker-restart-choreography` (`6bb6574b`), `architect-worker-restart-journal` (`97b28adb`); architect-verified anchors. Commit `552c556a7`.
- **Rev 2:** council round 1 (unanimous REJECTED → revise-and-resubmit). Folded C-1/C-2/C-3, V-1..V-15, contract language. Commit `6b4852b3c`.
- **Rev 3:** council round 2 (REJECTED, narrow delta; round-1 criticals CLOSED-WITH-GAPS). Folded F-1..F-6, F-9..F-11. Commit `eb7d4d8ca`.
- **Rev 4:** approver round — council approval stood (architecture independently confirmed: 7/7 requirements, ~40 anchors exact, no upgrade-gate weakening mechanism); ONE blocking item + N1..N8 folded. **Blocking fix:** operator escape hatch corrected — unit scope DERIVED from the scripts' supervision classification at render time (live host = SYSTEM-level `/etc/systemd/system/ensemble-main.service`, cgroup `/system.slice/…`; NO user unit; `systemctl --user` cannot connect — verified read-only by the approver; runbooks `resource-hygiene-operator-checklist.md:60,71,256`, `install-dir-protection-layer1-arm.md:168` corroborate system scope); "idempotent-by-run_id" corrected to **re-runnable only while `pending_op` + lock survive** (`restart.sh:131-175`; failure path clears op + lock) — making the scoped service restart the ONLY executable path for executed→gate-failed→daemon-down. **Notes:** N1 inherit-lane scope (`internal_agent:` only) + pin; N2 `dry_run` verdict-only/zero-mutation semantics, §1.2 contradiction fixed; N3 per-gate results via journal `detail` channel; N4 row (h) upgrade-input strengthening disclosed + T0a′ pin; N5 stale-arms premise corrected (never expire; wedge `pipeline-busy`; boot closure covers `in_flight`-still-set too); N6 restart-wake superset asymmetry noted; N7 anchor drift ×4 fixed (`config.py:1746/:1812/:1821`, `manager.py`~`:4980`, `upgrade_journal.py:2270`, `upgrade_journal_sweep.py`~`:1330`); N8 PR-description statement requirement recorded in §6.1/§9.
