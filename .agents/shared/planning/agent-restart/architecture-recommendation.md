# Architecture Recommendation — First-Class Agent RESTART Capability

- **Status:** DESIGN-ONLY (no implementation) — commission `agent-first-class-live-restart` · **Rev 5** (surgical: approver rejection 2 of 3 — final pass next)
- **Date:** 2026-10-19 · **Supersedes:** Rev 4 @ `2fdca3c43` (rev 3 @ `eb7d4d8ca`, rev 2 @ `6b4852b3c`, rev 1 @ `552c556a7`; same file; git history preserves all)
- **Branch/worktree:** `feature/agent-restart` @ `/home/nea/ensemble-src-wt-agent-restart` (base `630cefebb`)
- **Release vehicle:** stage-only, rides next staged release (expected v0.25.5). NO promote — user nonce ceremony owns that.
- **Method:** 3-worker fan-out → rev 1 → council round 1 → rev 2 → council round 2 (architecture confirmed: 7/7 requirements, ~40 anchors exact, no upgrade-gate weakening) → approver round → rev 4 (escape-hatch scope) → **approver round 2 → rev 5: alternative-hatch command verified NON-EXISTENT (dropped), stale runbook added to rewrite list, drills §8.3 re-derivation, env-scope column + notes folded**.
- **Authoritative policy (user, verbatim):** "restart don't need my nonce, only my upgrade is need." ⇒ restart = user-triggered, NO nonce. Upgrade keeps its 3-factor nonce ceremony UNCHANGED.

**Rev-5 changelog:** 🔴 **alternative escape-hatch command DROPPED** — `bash launcher.sh restart_current` does not exist (`launcher.sh:1668-1700` takes no subcommands; `restart_current` zero repo hits except the stale runbook); executing it would enter the foreground supervision loop (hangs an agent bash call) and create a second supervisor against the systemd-managed unit (the dualfight class `lib.sh` itself guards). The sole alternative-free recovery form is the worker-verified `sudo systemctl restart ensemble-main.service` (system-level unit, `sudo -n` NOPASSWD, polkit manage-units authorized) — fixed at §4(f)/§5.3/§10 row 7/Appendix · `docs/runbooks/upgrade-overhaul-watch.md:77` ADDED to §7.4 (carries both the stale `restart_current` and the broken `--user` form) · `upgrade-drills.md` §8.3 manual procedure to be RE-DERIVED (nohup lineage → systemd-unit-consistent) · **env-scope column** added to §4 + §2.4 env conditioning + §8 demo-restart assertion (no silent gating of demo/dev/sandbox — currently free, `upgrade_tools.py:2570-2576`) · dry_run verdicts via a read-only formatter, `_refusal()` untouched · scope derivation discriminators named (cgroup slice prefix + `SYSTEMCTL_BIN`/`SUPERVISION_UNIT_DIR`; Python→shell bridge for `_format_wake_body`) · stale-arm closure also FREES the journal lock · `root_stamped_at`/`expires_at` (authorization age) in receipt + `restart_audits` + notify · `stop_forced_kill` re-worded as a NEW event · `restart_gate`-vs-`halt` journal mapping specified · `busy-check` receipt line renders actual state.

---

## §1 Policy Change + Lineage

### 1.1 What is superseded

| Superseded ruling | Where it lives | New status |
|---|---|---|
| **A2** — live `system_restart` refused outright, "refusal-tested only" | `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md:13,116,143`; enforced `daemon/tools/upgrade_tools.py:2305-2320` (refusal string `:2319`) | **SUPERSEDED for `system_restart` live-restart-refusal ONLY.** Live restart allowed with NO nonce when the calling turn carries genuine-user provenance (§2). |
| **§3.1 per-env matrix** — actor tools free dev/demo/sandbox; live behind 3-factor gate AND refused-at-execution | `.agents/shared/planning/self-restart-upgrade-phase2/tool-api-design.md:34,223` | Superseded for the restart lane **on LIVE only** — demo/dev/sandbox stay FREE (env-scope column, §4). |
| **D-FA2.4** env-self-match + live outright-refusal | `.agents/shared/planning/post-restart-arm-notify/supersession-record.md:89` | Env-self-match order **unchanged**; only the live-refusal arm superseded. |
| Banner "anything touching live is USER-GATED" | `.agents/shared/planning/post-restart-arm-notify/decisions.md:3-9` | Restated for restart: the gate is genuine-user provenance, not a nonce echo. |
| Maintenancer prohibition "Never run `system_restart`" / "Never restart the daemon" | `agents/maintenancer/rule.md:8,68`; `agents/maintenancer/meta.json:13` `tools.deny` | **SUPERSEDED** — maintenancer becomes the arming agent (§7.1). Seal re-pin preserving pin-17 (§7.5). |

### 1.2 What is explicitly NOT changed

- **The upgrade 3-factor ceremony** (factors `self-restart-upgrade-phase2/architecture-recommendation.md:123-141`, enforced `upgrade_tools.py:2779-2945`). Restart reuses ONLY the factor-2 classifier through a disjoint store; upgrade call sites keep byte-identical decision behavior **with one disclosed, strictly-strengthening delta** (N4/rev-4: the row (h) hardening removes an agent-path-minted direct-window input; pinned T0a′). The pre-existing write-layer exposure of upgrade factor-2 remains out of scope — leader-filed critical note (§2.5 row (i), §2.6).
- **A3 schema stability**: `user_confirmed`/`nonce` present-but-ignored on the live lane.
- **`ENSEMBLE_UPGRADE_LIVE`** and the promote verified-arm machinery: untouched, never reused.
- **Non-live environments behave as today — FREE** (rev-5 env-scope made explicit: the provenance gate and the new anti-flap rails are **live-only**; see the Env column in §4). **`dry_run` on ANY env evaluates and REPORTS the full verdict — provenance result, every rail, would-be override set — WITHOUT arming: zero journal mutation, no lock, no executor, no wake.** On live this supersedes today's outright refusal (intended change).

### 1.3 Why the asymmetry flips

A restart is strictly less dangerous than a promote: same binary, no migration, no rollback surface. The existing asymmetry forced manual `sudo systemctl restart ensemble-main.service` for the motivating case (`limits.worker_pool_size` 5→7 written by `settings_set`, `requires_restart: true, divergent: true`, un-appliable by the agent). Policy now: provenance replaces the nonce **on live**; rails (§4) replace "refuse outright".

---

## §2 Provenance Attestation Design

### 2.1 The existing primitive (verified)

- **Classifier (single source of truth):** `classify_user_origin(source, registry_get)` — `daemon/tools/upgrade_journal.py:2992-3047`. Arms on exact `"api"` (HTTP chat lane) OR a registered adapter whose daemon-controlled `source_type ∈ {telegram, slack, discord, whatsapp}` (`daemon/sources/registry.py:1130,1144-1149`). **Reserved internal lanes never arm** — checked BEFORE the registry (`:3006-3013`) via `is_reserved_source` (`daemon/constants.py:928-951`; prefixes `:904-922`). Fails closed everywhere.
- **Stamp machinery:** single implementation (`InstanceManager.stamp_user_origin_window`, `daemon/manager.py:4803-4865`), **TWO call sites** — the dispatch funnel (`manager.py:9551-9559`, post-build) and the wake-sweep re-stamp (`daemon/services/upgrade_journal_sweep.py:1668-1686`, TTL-resetting — post-build lines). **Stamp-capable `"api"` producers:** (1) HTTP chat lane (`daemon/routers/messages.py:691`); (2) `_auto_dispatch_first_turn` fallback (`daemon/tools/instance.py:952-955` — hardened here, §2.5 row (h)); (3) wake-delivery fallbacks (`upgrade_journal_sweep.py:1183-1190` ari-fallback, `:1706-1711` deliver pass — post-build lines; fixed for restart-kind wakes). **Not stamp-capable:** `messages.py:427` `api_resume_fallback` is RESERVED and **CLEARS**.
- **TTL:** `NONCE_TTL_S = 60min` (`upgrade_journal.py:135`), applied at `manager.py:4856` (post-build).
- **Verify site (upgrade factor 2):** `upgrade_tools.py:2781-2830` — direct-store read (`:2783-2784`); fail-closed.
- **`source` is daemon-written everywhere:** chat adapters build `source=f"{source_id}:{external_user_id}"`; agent sends `internal_agent:{caller}` (`instance.py:3989,4096,4200,1187`); job lane `agent:{caller_agent_id}` (`job_queue.py:2006,1265`); no source param on `send_message` (`instance.py:3564-3593`). `additional_kwargs["source"]` write sites known: `instance_messaging.py:395-419`, `graph.py:8406-8412` (exhaustive enumeration = residual, §10).

### 2.2 Dispatch-chain verdict

**DIRECT-USER-ONLY, not chain-aware.** user → ari (`api`, stamped) → ari `send_message` → maintenancer dispatches with `internal_agent:<ari_iid>` (`instance.py:4200`) → reserved → window **cleared** (`manager.py:4829-4831`). No ancestor argument anywhere. The motivating delegation cannot pass today ("no stamp recorded this turn", `upgrade_tools.py:2808-2817`).

### 2.3 Options compared

| | (A) Gate-time ancestry walk | **(B) System-minted inherited stamp ← RECOMMENDED** | (C) Capability token |
|---|---|---|---|
| Mechanism | Walk ancestors at gate | Funnel-minted inherited entry in a **separate store** on `internal_agent:` dispatch | Single-use token |
| Cost | N reads/gate; fragile | O(1) per dispatch | Highest; bearer-shape rejected (OQ9) |
| Upgrade isolation | Read-only, safe | Decision-layer safe (§2.6) | New auth surface — violates constraint |

### 2.4 Recommended mechanism (B) — specification

- **New in-memory store** `manager._inherited_provenance_windows` (init at `manager.py:1539`, post-build — was adjacent to the pre-build `:1507`), written ONLY by the funnel (`manager.py:9551-9559`, post-build): on `source.startswith("internal_agent:")`, parse `<caller_iid>`; **inherit iff the caller's direct window or inherited entry is live at claim time**. Entry: `{root_source, root_message_id, root_stamped_at, depth, expires_at}`.
- **Inherit-lane scope (N1):** keys ONLY on `internal_agent:`; `internal_invoke_and_wait:{parent}` NEVER inherits (reserved; the motivating path runs on `send_message`/`internal_agent:`). Pinned T0b.
- **Everything else clears it** (`system:*`, `agent:*`, `internal_invoke_and_wait:*`, `source=None`); `_user_origin_windows` write-set byte-identical. **Residual (fail-closed default):** silent-resume × inherited store — silent resume MUST NOT inherit until verified.
- **Freshness:** `expires_at = min(parent_expiry, now + 60min)`; depth cap 4; cleared on next non-inheriting dispatch; wiped by daemon restart (fail-closed). Revive = fresh dispatch. **Authorization-age visibility (rev-5):** `root_stamped_at` + `expires_at` surface in the receipt (§7.2), `restart_audits` (§6.2), and the notify body (§5.3) — an accepted arm can rest on a window up to ~60 min old, and the operator must be able to SEE that.
- **Arm-time capture (C-2):** gate records `provenance_class ∈ {direct, inherited}` + root source/message-id/depth/**stamped-at**; `_capture_user_source` (`upgrade_tools.py:1244-1259`) extended to both stores; `PendingWake` carries the class + root fields.
- **Mint-condition carrier (F-3):** arm stamps `spec["provenance_ok"]=True` (+ class) post-gate; drain mints `ENSEMBLE_RESTART_LIVE=1` IFF present; absent ⇒ guard exits 78 fail-closed. **Invariant:** marker ⇒ gate passed; daemon death between arm and drain ⇒ `restart_abandoned`, never executed.
- **Gate predicate — ENV-SCOPED (rev-5, explicit):**
  `live:  restart_allowed = provenance_ok ∧ env-self-match ∧ pipeline rails (a) ∧ cooldown (b) ∧ min-uptime (c1) ∧ budget (c2) ∧ reason (d)`
  `demo/dev/sandbox:  today's behavior — pipeline interlock (a) + reason (d, already schema-mandatory today); NO provenance gate, NO new anti-flap rails (b/c1/c2 are live-only — global application would silently gate envs that are free today, `upgrade_tools.py:2570-2576`).`
  `dry_run=true (any env): evaluates the env-appropriate predicate + rails, reports the verdict, returns WITHOUT arming — zero mutation.`
  Reuses `classify_user_origin` (pure) + TTL helpers (`upgrade_journal.py:170-190`) — never the upgrade gate's store. §8 asserts the demo path stays free (no user window ⇒ still schedules).
- **Policy line (vector g):** provenance = **authorization-to-act, NOT intent-verification**; disclosure fields + rails cover intent.

### 2.5 Anti-spoof vector table

| Vector | Verdict | Mitigation |
|---|---|---|
| (a) Content mimicking a user message | **BLOCKED (structural)** | Classification reads daemon-written `source`, never content; no source param. |
| (b) Self-send / sibling laundering | **BLOCKED (closure + decay)** | Live caller-side root required; depth cap + monotonic TTL. |
| (c) Replay / revive | **BLOCKED** | Per-dispatch in-memory; restart wipes; revive = fresh dispatch. |
| (d) `system:*` / job-queue turns | **BLOCKED** | Reserved prefixes fail pre-registry; inherit lane keys on `internal_agent:` only (N1). |
| (e) Cross-session / project injection | **BLOCKED** | UUID-keyed in-process store (ADR-015 R10). |
| (f) Chat-source users | **ACCEPTED BY DESIGN** | Daemon-built sources; same trust set as upgrade factor-2. |
| (g) Prompt-injection in a genuine user message | **MITIGATED BY DISCLOSURE** | Turn IS user-originated; disclosure + rails; documented residual. |
| (h) 🟡 `"api"` auto-dispatch fallback | **FOLDED IN (N4-disclosed strengthening)** | `instance.py:952-955` (`:954` mints `auto_dispatch_fallback`) → reserved source; removes an agent-path-minted input from upgrade factor-2; T0a′ pin. |
| (i) 🔴→BLOCKED Wake-delivery laundering (C-2) | **BLOCKED (rev-2)** | Reserved `system:post_restart_arm_notify`; class on `PendingWake`; re-stamp deleted for restart-kind; T0b. |
| (j) 🔴→BLOCKED Mixed-kind coalescing (F-1) | **BLOCKED (rev-3)** | `_coalesce_wakes` partitions by `wake.kind`; T0b mixed-group pin. |

### 2.6 Upgrade-gate isolation — two-layer statement

- **Decision layer: AIRTIGHT by rule.** Restart gate reads both stores via a NEW helper; upgrade factor-2 keeps explicit direct-store access (`upgrade_tools.py:3454-3455` post-build; +651 lines of pure file growth, no semantic drift — the access shape is byte-preserved) and NEVER calls the restart helper (T0a). **Disclosed strengthening (N4):** the row (h) hardening shrinks factor-2's reachable input set — strictly strengthening; T0a′.
- **Write layer: scoped.** Restart-kind wakes reserved-sourced, un-restamped, kind-partitioned. Upgrade-lane `"api"`-fallback exposure = pre-existing, leader-filed critical note.

---

## §3 Deferred Execution Choreography (ordering guarantee)

### 3.1 Reused machinery

Arm: tool → pipeline-lock acquire (`upgrade_tools.py:2433` → `upgrade_journal.py:675`) → `in_flight` + `pending_op` (`:2470/:2475`; `PendingOp.kind` carries `"restart"`, `:793`) → wake write, SAME envelope (`:2481` → `:1308` → `:986`) → marker + spec (`:2513` → `manager.py:4837`) → `RESTART SCHEDULED`.
Execution: turn ends → post-graph `finally` drain (`instance_messaging.py:5216`, shielded `:1983`, one-shot pop `manager.py:4910`) → spawn-refusal guard (`manager.py` ~`:4980`) → `systemd-run --unit=<prefix>-<run_id> --wait --collect` (`upgrade_journal.py:2270`) → `scripts/upgrade/restart.sh` (preflight `:119`; lock adoption `:160`; supervision preflight `:192`; grace `:204-212`; stop `:216`; start `:223`; gate `:236`; finalize `:258`).
Boot: reconcile (`daemon/api.py:2439`) → wake sweep (`:2450`, BEFORE auto-continue `:2477-2492`) → arm-notify (`upgrade_journal_sweep.py` ~`:1330`) → auto-continue (boot orphan sweep, v0.25.3).

### 3.2 Ordering guarantee

1. **HARD:** the stop can never precede turn completion (executor spawns only from the turn's own post-graph `finally`; marker one-shot).
2. **SOFT (disclosed):** reply dispatches AFTER the drain (`:5231→:5244`); grace is a **head-start, not a bound**. Dispositions: grace 5s→15s; receipt discloses best-effort reply delivery; TERM-while-dispatch-pending journal note; drain-below-dispatch restructure stays improve-lane.

### 3.3 Restart-flavor deltas vs promote

| Delta | Decision |
|---|---|
| **Live guard env (C-1/F-3/F-4)** | `ENSEMBLE_RESTART_LIVE=1` minted only on provenance-passed arms: arm stamps `spec["provenance_ok"]` post-gate; drain mints IFF present via the `extra_env` explicit-extra merge; absent ⇒ guard exits 78 (`restart.sh:99` post-build → `lib.sh:715-728` for `require_live_guard`; the NEW sibling `require_live_guard_restart` sits adjacent in lib.sh). Acceptance branch takes **ONLY** `ENSEMBLE_RESTART_LIVE`; `require_live_guard` byte-identical; `ENSEMBLE_UPGRADE_LIVE` never reused. Guard-trip `live-restart-refused` event: best-effort **lock-free** (pre-adoption); notification rides `executor_exit`; no terminal-set membership. |
| No version swap / no symlink change / no migration ops | Version-verify = **identity** assertion. |
| **No rollback lane — recovery (rev-5 final form)** | A failed restart has no automatic recovery: (i) launcher burst/backoff supervision, (ii) the **operator escape hatch — §4(f), the sole executable form `sudo systemctl restart ensemble-main.service` with unit scope DERIVED at render time (system-level on the live host)**. The rev-4 "alternative" `bash launcher.sh restart_current` is **DROPPED — it does not exist** (`launcher.sh:1668-1700` takes no subcommands; running `launcher.sh` enters the foreground supervision loop — hangs an agent bash call — and would create a second supervisor against the systemd-managed unit: the dualfight class `lib.sh` guards against). Rendered verbatim-derived in the notify body (§5.3). |
| **Health gate + journal outcome mapping (rev-5 specified)** | Report-only `/livez` (180s) + `/readyz` (120s, NEW for this lane), `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`. **Journal mapping:** the EXISTING terminals stay authoritative — gate FAILURE journals `halt` (`restart.sh:258-271`, wake-firing, unchanged), gate SUCCESS journals `restart` (existing, wake-set member). The NEW `restart_gate` event is written **ALONGSIDE** the existing terminal in BOTH directions, carrying the per-gate results (pass/fail, latency, budget per probe) in its `detail` channel — it is the classification/detail record, never a replacement terminal. `restart_audits.outcome` maps: `halt` ⇒ `halted`; `restart` ⇒ `applied`; `restart_gate.detail` informs the notify body. |
| **Terminal events (C-3/F-4/F-5)** | `_TERMINAL_EVENTS` untouched (9, `upgrade_journal.py:1581-1591`; promote-lane `reconcile_pending_op` `:1677-1697`; `:1596-1616` comment: restart events must NEVER close promote ops). `RESTART_TERMINAL_EVENTS = ("restart_gate", "restart_abandoned", "stop_forced_kill")` (NEW concatenated constant). `WAKE_TERMINAL_EVENTS` (`:1623`) = `_TERMINAL_EVENTS + ("restart",) + RESTART_TERMINAL_EVENTS` (F-2-rewritten assertion). **Kind-aware reader MANDATED:** `wake_terminal_event_after(journal, armed_at, kind)` — promote-kind wakes match ONLY `_TERMINAL_EVENTS + ("restart",)`; restart-kind wakes the full superset (**N6 asymmetry accepted, §10 row 4**). `live-restart-refused`: no terminal membership — rides `executor_exit`. `_OUTCOME_LABELS` ×4 (`upgrade_tools.py:1179`). **`stop_forced_kill` is a NEW event this commission ADDS** (rev-5 wording fix — it exists nowhere in the repo today): written at the TERM→KILL escalation site `scripts/stop-ensemble.sh:485-501` by this commission's §9 step 6. |
| Graceful stop | Unchanged SINGLE-TERM contract (`lib.sh:3570` → `stop-ensemble.sh`); KILL bounded (`WAIT_S=70`; unit kill grace 10s). Forced KILL voids the in-flight checkpoint write ⇒ `stop_forced_kill` (NEW, above). |
| Transient unit name | Parameterized `unit_prefix` — `ensemble-restart-<run_id>`. **V-7:** `UNIT_NAME_PREFIX` at 4 sites (`upgrade_journal.py:1900/:2116/:2197/:2362`); reaper/boot-sweep matching under the new prefix unverified (residual); family contract + unit-name test. |
| **Stale arms (N5 + rev-5 lock release)** | Abandoned restart arms **NEVER expire — they wedge `pipeline-busy` indefinitely**. Boot-lane closure is a real liveness fix: boot reconcile closes an unexecuted `kind=restart` op with `restart_abandoned` + wake-abandonment notify, covering BOTH variants — (a) no `in_flight`, (b) `in_flight` still set with no live executor — **AND FREES the arm's journal LOCK** (`rollback.lock.d`, `upgrade_tools.py:2433` acquisition site; run_id-matched release) — without the lock release, a killed arm wedges the PROMOTE pipeline too until daemon restart. Periodic-tick skip at `:1690` stays; boot lane only. Never auto-execute a stale arm. F-3 invariant: daemon death between arm and drain ⇒ `restart_abandoned`, never execution. |
| Double-arm | Journal lock = arm mutex (second arm ⇒ `pipeline-busy`); same-instance marker-map guard; backstop `executor_orphaned` (`manager.py` ~`:4980`). |

### 3.4 Executor environment contract

`EXECUTOR_ENV_ALLOWLIST` governs ambient env; the **explicit `extra_env` merge** is the only sanctioned attestation lane. Promote threads `ENSEMBLE_UPGRADE_LIVE`; restart threads `ENSEMBLE_RESTART_LIVE` gated on `spec["provenance_ok"]`. The two vars stay disjoint forever (T0c + §10 risk 11).

---

## §4 Safety Rails (env-scoped — Env column added rev-5)

| Rail | Env | Mechanism | Justification | Default | Config | Refusal token |
|---|---|---|---|---|---|---|
| (a) Pipeline-in-flight | **all** (as today) | `in_flight` → `pending_op` → `rollback.lock.d` → `.adoption_in_progress` → `pipeline_settled` (mirrors `upgrade_tools.py:2331-2400`) | The promote/adoption interlock — protects every env today already | all 5 blocking | no knob | `pipeline-busy` + `adoption-in-progress`, `pipeline-not-settled` |
| (b) Cooldown | **live only** | journal `cooldown_until`; refuse while future; fail CLOSED on unparseable (`:2963` semantics) | Leftover cooldown = recent instability signal; unparseable = refuse | — | — | `cooldown-active` (receipt renders ACTUAL state) |
| (c1) Minimum uptime | **live only** | `now() − get_boot_epoch()` (`daemon/services/boot_epoch.py:70`) ≥ threshold; fail CLOSED when unavailable | Post-boot restart burns crash budget; 600s = launcher horizon (`launcher.sh:59`) | **600s** | `ENSEMBLE_RESTART_MIN_UPTIME_S` | `restart-too-soon` |
| (c2) Restart budget | **live only** | True sliding window — `restart_window.events[]` (Python-only); deliberately NOT the fixed `JOURNAL_EMPTY` bucket (`upgrade_journal.py:157`) | Counter must survive the restart it meters; fixed bucket allows 2×N across a boundary | **3 / 24h** | `ENSEMBLE_RESTART_BUDGET_24H`, `…_WINDOW_S` | `restart-budget-exceeded` |
| **Budget envelope (V-1)** | live | Increment FIRST, inside the lock-holding try, SAME envelope as `in_flight`+`pending_op`+wake; abandoned arms consume budget, no refund | Crash-ordering safety (over-count = safe) | — | — | — |
| (d) Reason required | **all** (as today — schema-mandatory now) | `reason: str = ""`; empty ⇒ typed refusal; charset-constrained persisted form | Only post-hoc explanation of an operator-visible discontinuity; already mandatory today via positional schema | mandatory (min 3) | — | `reason-required` |
| (e) In-flight work | all | Graceful SINGLE-TERM stop + checkpoints + **boot orphan sweep v0.25.3** (`daemon/api.py:2477-2492`; predicate home `find_orphaned_live_turns`, `daemon/repositories/task/repository.py:1165+`; StaleTaskRecovery backstop, `daemon/config.py:1746/:1812/:1821`). **THREE-SIGNAL: task-less RUNNING ∧ healthy checkpoint ∧ ACTIVE JobItem — a bare `send_message` turn is NOT re-dispatched** (most load-bearing assumption; disclosed + pinned). NOT protected: mid-tool side effects, bash orphans, bg processes, non-checkpointed state | Kill-turn resume is safe only via persisted checkpoints + the sweep | on (existing switch) | existing | advisory `busy-check` (receipt renders ACTUAL state — §7.2 agreement, rev-5) |
| **(f) Health gate + THE operator escape hatch (rev-5 final)** | live (gate report) | Report-only `/livez`+`/readyz`; failure ⇒ existing `halt` terminal (wake-firing, unchanged) + `restart_gate` detail event + notify + logs; **no auto-rollback**. **Escape hatch — SOLE executable form:** `sudo systemctl restart ensemble-main.service` with **unit scope DERIVED at render time** by a new `supervision_scope()` helper reading the NAMED discriminators: cgroup slice prefix of the daemon process (`/system.slice/…` ⇒ system scope; `/user.slice/…` ⇒ user scope) with `SUPERVISION_UNIT_DIR` / `SYSTEMCTL_BIN` overrides — never a hardcoded scope, never a default `--user`. Python→shell bridge: the helper lives daemon-side and mirrors `lib.sh`'s classifier input set exactly; `_format_wake_body` calls it directly (no shell-out at render time); drift against `lib.sh` is unit-tested (both slice fixtures). Rendered command: `sudo systemctl restart <unit>` (system scope — the live host: `/etc/systemd/system/ensemble-main.service`, cgroup `/system.slice/ensemble-main.service`, `sudo -n` NOPASSWD, polkit manage-units authorized) or `systemctl --user restart <unit>` (user scope, if a future host classifies there). **The script-path re-run** `ENSEMBLE_RESTART_LIVE=1 bash scripts/upgrade/restart.sh live --run-id <r-…>` is **arm-intact-only** (`restart.sh:131-175` refuses exit 78 when the op is absent/mismatched or the lock is missing; the failure path clears the op and releases the lock) — in the worst case (executed → gate failed → daemon down) the op/lock are gone, so the **scoped systemctl restart is the ONLY executable recovery**. The rev-4 `bash launcher.sh restart_current` alternative is DROPPED (non-existent command; would hang the caller and create a dual supervisor) | Same version — nothing to roll back to; the hatch must be copy-paste executable on the live host | report-only | `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1` | `restart-gate-failed` (notify/journal) |

Also reused unchanged: restart-under-burst-abort (`upgrade_tools.py:1551`).

---

## §5 Boot Arm-Notify (incl. applied-overrides enumeration)

### 5.1 Mechanism (reused, fail-open)

Wake record at arm (`upgrade_journal.py:864-960`, `arm_pending_wake` `:986`; kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY` ON); boot sweep (`daemon/api.py:2450` → `upgrade_journal_sweep.py:1063`) BEFORE auto-continue; delivery `enqueue_message(arming_instance_id, priority=2, metadata.system_context.kind="post_restart_arm_notify")` (~`:1330`); coalesce cap 16; abandon grace 600s; ari front-door fallback. Restart-kind wakes: `system:post_restart_arm_notify`, no re-stamp, **kind-partitioned coalescing**. **V-8:** `get_last_applied_resolution()` (`settings_service.py:426-440`) module-global — `_format_wake_body` (`:547`)/`_format_coalesced_wake_body` (`:574`) import it directly; `None` (unavailable) ≠ zero-dict (no overrides). Fail-open at three wrap levels (`api.py:2453-2459`).

### 5.2 Applied-overrides enumeration

- Overrides live in `app_settings` (`daemon/models/app_settings.py:70`); boot applies them ONCE via `apply_db_settings_overrides` (`settings_service.py:807`, called `daemon/api.py:510`); SSOT ladder DB > env > yaml > default (`resolve()` `:462`); live `Config` mutated in place (`:889-893`); `worker_pool_size` effective at `PoolOrchestrator.setup` boot read (`pool_orchestrator.py:206`). All 6 managed keys `requires_restart=True` (`:155-320`).
- **Arm-time pending set** = `requires_restart=True` ∧ divergent (`settings_tools.py:358`; `daemon/routers/settings.py:898-928`), captured inside the lock-holding try into `restart_audits[run_id].overrides`, SAME envelope. (Bounded PG read in-lock — accepted.)
- **Access seam:** `SettingsService` is a router module-global (`daemon/routers/settings.py:143-154`) — wire onto the manager (`api.py`, mirroring `set_project_repository` `:2740-2742`); tool reads `manager._settings_service`.
- **Applied ⇔** post-boot live == arm-time DB value; failure detectable via `resolution["source"] != "db"` (invalid row ⇒ tier skipped, `settings_service.py:511-556`); late-write/cleared diffed separately.
- **Render:** `limits.worker_pool_size: 5→7 (applied)` / `(not applied — resolved tier=env)`.

### 5.3 Notify body contents (contract)

Restart outcome (existing terminal + `restart_gate` detail **with per-gate `/livez`+`/readyz` results from the journal `detail` channel**) · version now live (identity check) · applied-override list (§5.2) · reason echo · provenance class + root source **+ authorization age (`root_stamped_at`/`expires_at`)** · in-flight-work disclosure (three-signal JobItem boundary sentence) · **escape-hatch line: the DERIVED-scope form ONLY — `sudo systemctl restart <unit>` as classified (system-level on the live host); no launcher alternative (dropped, rev-5); the script re-run noted as arm-intact-only** · `run_id`.

---

## §6 Audit Journal

### 6.1 Decision: **(A) REUSE** — no new table, no migration

The journal is a **file-backed JSON document** (`<install_dir>/releases/state.json`, `upgrade_journal.py:250-251`; atomic envelope `:326-402`); the discriminator is `PendingOp.kind` (`"restart" | "promote"`, `:793`). (A) wins: no dual write (one locked envelope, `upgrade_tools.py:2476-2484`, ADR-039); **zero migration — additive-only delta keeps `rollback_safe=true`** (105 `.sql` files untouched, none `restart_*` — approver-confirmed; **N8: this statement must appear in the PR description**); sweep/settle semantics exist; new top-level keys are invisible to `lib.sh`'s field-targeted splice (ADR-034, `upgrade_journal.py:34-38`).

*(B) sketch for the record:* `2026102X_000001_create_restart_audits.sql` — only if cross-install querying is ever required. **Not recommended.**

### 6.2 Additive schema: `restart_audits` + `restart_window`

`ensure_extensions` (`upgrade_journal.py:404-430`), keyed by `run_id`, capped 50 (drop-oldest):

```jsonc
{ "run_id":"…", "env":"live", "kind":"restart",
  "reason":"apply worker_pool_size 5→7",                     // charset-constrained
  "arming_instance_id":"…", "arming_agent_id":"maintenancer",
  "provenance_kind":"direct|inherited",
  "user_source":"discord:<user>", "user_message_id":"…", "provenance_depth":1,
  "provenance_stamped_at":"ISO",                              // rev-5 authorization age
  "wake_source":"system:post_restart_arm_notify",
  "armed_at":"ISO", "executed_at":"ISO|null",
  "outcome":"applied|halted|failed|unknown",                  // maps halt⇒halted, restart⇒applied
  "overrides":[{"key":"limits.worker_pool_size","live_before":5,"db_value":7,
                "status":"applied|not-applied|cleared|late-write"}],
  "notify":{"status":"delivered|abandoned|pending","at":"ISO","message_id":"…"} }
```

`restart_window: {"events":["ISO", …]}` — Python-only sliding budget list. Arm writes static fields + pending set + budget increment (FIRST, same envelope); boot-settle updates `executed_at`/`outcome`/`overrides[].status`/`notify` in place. **Reason charset (V-11):** printable, no newlines, no splice-sentinel class, ≤200 chars, constrained at the tool boundary.

### 6.3 Query surface: extend `upgrade_status`

`section: Literal["status","restarts"] = "status"` on `upgrade_status` (`upgrade_tools.py:2078`), reusing `run_id`/`tail`:

```
RESTART AUDIT — env=live  (n=3, showing 3)
when | who | reason | outcome | overrides
2026-10-20T01:30:00Z | maintenancer/inst-ab12 | apply worker_pool_size 5→7 | applied | limits.worker_pool_size: 5→7 (applied)
```

Filters: `run_id`, `outcome`, `since`, `tail`. New tool rejected (privileged-category registration + duplicated machinery, `tests/unit/tools/test_upgrade_registration.py:106-120`).

---

## §7 Tool Contract

### 7.1 Tool identity — keep `system_restart`, lift the deny

`meta.json:13` deny-by-name strips (`instance.py:6342` post-build); ~~deny ≠ allow ⇒ policy edit, not allow-list change~~ **CORRECTED (review fix-cycle-1, Finding 2):** this codebase's allow semantics are EXCLUSIVE-ALLOW (`resolve_tool_filter`, `instance.py:460-490`) — a tool absent from `tools.allow` is withheld even with no deny entry, and the factory quartet's `system_upgrade` category allow entry does NOT expand in the cold registry (factory tools register their category per-instance). BOTH edits are therefore required and both landed: the deny LIFT **and** an explicit `tools.allow` entry (`"system_restart"` in `agents/maintenancer/meta.json`; the same one-line entry in `agents/ari/meta.json`). `user_confirmed`/`nonce` present-but-ignored (A3). Arming agent = maintenancer; **the gate is provenance, not agent identity** (ari holds it too, `agents/ari/meta.json:30` post-build).

### 7.2 Receipt

```
RESTART SCHEDULED — run_id=<id> env=live mode=graceful-now reason="<reason>"
executes_after_turn: yes
reply_delivery: best-effort (head-start grace 15s; reply may be lost — post-restart arm-notify is the durable report)
notify: armed (post-restart auto-wake to this instance; kill-switch ENSEMBLE_POST_RESTART_ARM_NOTIFY)
overrides_pending: limits.worker_pool_size 5→7
journal: releases/state.json pending-op opened (kind=restart) audit=restart_audits/<run_id>
budget: restarts_24h=<n>/3 cooldown=<actual state>
provenance: class=<direct|inherited> root=<root_source> msg=<root_message_id> depth=<d> stamped_at=<ISO> expires=<ISO>
trigger: post-turn-callback armed
busy-check: <actual state — idle|busy>            # agrees with §4(e) advisory token (rev-5)
```

(`dry_run=true` returns the verdict block with NO `run_id`/journal line — zero mutation, N2.)

### 7.3 Typed refusals — house style, single writer

One writer for REAL refusals: `_refusal()` (`upgrade_tools.py:1425-1433`) ⇒ `Error: RESTART REFUSED — reason=<token>: <message>`; journaled `refusal` (lock-busy carve-out `:1394-1400` preserved). **Rev-5 adjudication (dry_run collision):** `dry_run` verdicts NEVER call `_refusal()` — a would-refuse dry-run renders through a separate read-only verdict formatter (same token vocabulary, zero journal side-effect); `_refusal()` remains the single writer for executable refusals. Tokens: `pipeline-busy` (+`adoption-in-progress`, `pipeline-not-settled`), `cooldown-active`, `no-user-provenance`, `reason-required`, `restart-too-soon`, `restart-budget-exceeded`. **`live-restart-refused`: removed from the TOOL vocabulary (`:2570-2576`), re-homed as the script-guard journal event (rides `executor_exit`).** `RESTART_TOKENS` pin extended ×8.

### 7.4 Docs surface

| File | Change |
|---|---|
| `daemon/tools/upgrade_tools.py:2318-2327` | live refusal string → provenance-gated path |
| `daemon/tools/upgrade_tools.py:2545-2585` (`_full_doc_`) | "LIVE: refused outright…" → new contract |
| `daemon/tools/upgrade_tools.py:11,112,131` | module docstring + checklist |
| `agents/maintenancer/meta.json:13` | lift `system_restart` from `tools.deny` |
| `agents/maintenancer/rule.md:8,68` | policy rewrite — preserve audit pin-17 (`test_chart_image_delivery_audit.py:467-483`) |
| `agents/maintenancer/tools_note.md:20,77` | deny-table + prose |
| `agents/maintenancer/README.md:37,74` | "the user pulls the restart lever" + skill one-liner |
| `agents/maintenancer/skills-template/restart-upgrade-ops.md` | Boundary / Contract / Focus-area 5 / report format |
| `agents/ari/tools_note.md:421` | old "refused outright" contract → rewrite; provenance-not-identity |
| `agents/maintenancer/ROLLOUT.md:52,269,286,381,444` | deny-list references (optional) |
| `docs/runbooks/post-restart-arm-notify.md` §1 | invariant-8 + ADR-044 — rewrite restart-only |
| `docs/runbooks/upgrade-drills.md:468/535` | live-refused passages — **REWRITE**; **§8.3 manual procedure RE-DERIVED (rev-5): its nohup lineage is inconsistent with the systemd-managed live unit — align with the derived-scope systemd form** |
| **`docs/runbooks/upgrade-overhaul-watch.md:77` (rev-5 ADDED)** | carries BOTH the non-existent `restart_current` alternative AND the iteration-001-broken `systemctl --user` form — rewrite to the derived-scope form |

**V-12 caveat:** template edit ≠ seeded bank-row update (`faf6e86e…`; `skill_seed_service.py:325-352`) — developer executes the bank-row path; T9 pins it.

### 7.5 Seal re-pin (same-merge discipline)

`agents/maintenancer/rule.md` pinned (`test_chart_image_delivery_e2e.py:1389`, tripwire `:1423`) ⇒ same-merge re-pin (git-log attribution, targeted run, provenance-labeled commit). Pin-17 preserved + T9. **F-2 second site:** sweep-test mutation guard (`test_post_restart_arm_notify_sweep.py:856-870`) assertion REWRITE — expected `set(_TERMINAL_EVENTS) | {"restart"} | set(RESTART_TERMINAL_EVENTS)`; `len==9` + absence pins verbatim. ⚠️ Adding the 3 names to `_TERMINAL_EVENTS` = the C-3 trap — forbidden.

---

## §8 Test Plan

**T0 — precondition pins:** (a) upgrade factor-2/3 byte-identity + directional isolation; **(a′ N4)** hardened-fallback strengthening pin (agent-path auto-dispatch no longer stamps `"api"` — upgrade-side, asserted); (b) terminal-event mutation guard (F-2-rewritten); (b′) kind-aware reader e2e (promote wake NOT completed by `stop_forced_kill`); (c) executor env negative (`ENSEMBLE_UPGRADE_LIVE` never set by restart lane; `ENSEMBLE_RESTART_LIVE` only with `spec["provenance_ok"]`); (d) wake-provenance side-effect (restart-kind wake mints NO user window).

**T0b — spoof vectors (a)–(j) + N1/N2 pins:** `internal_invoke_and_wait:` never inherits; `dry_run` on live: full verdict, ZERO journal mutation **rendered via the read-only formatter — `_refusal()` untouched (rev-5)**; mixed-group pin; second-restart-on-wake-turn refused.

**Mandated cases:**

| # | Case | Approach |
|---|---|---|
| 1 | genuine-user turn → scheduled | seed `_user_origin_windows[iid]` (`:2461` fixture); receipt + `provenance: class=` + no REFUSED; inherited variant via `internal_agent:<caller>`; **env-scope assertion (rev-5): same matrix run on demo — NO window seeded, arm still SCHEDULED (demo/dev/sandbox stay free; no silent gating)** |
| 2 | agent-initiated → refused | no window on LIVE ⇒ `no-user-provenance` + diagnostic; ZERO journal mutation |
| 3 | promote in-flight → refused | `test_pipeline_busy_armed:2374` shape ⇒ `pipeline-busy`; + `adoption-in-progress`, `pipeline-not-settled` |
| 4 | cooldown/budget → refused | `test_cooldown_active_armed_refused:2339` mirror (+ unparseable fail-closed); budget cap; window-boundary (T, T+window−ε); crash-ordering + concurrent arms; min-uptime; epoch-unavailable fail-closed — **all LIVE-only (env-scope: demo matrix asserts these rails do NOT fire off-live)** |
| 5 | notify contents | `_format_wake_body` unit: override render; `None` ≠ zero-dict; per-gate livez/readyz detail from the journal `detail` channel; `_OUTCOME_LABELS` ×4; **authorization age (`stamped_at`/`expires`) rendered**; **escape-hatch line = DERIVED-scope form ONLY (both slice fixtures: `/system.slice/` ⇒ `sudo systemctl restart <unit>`; `/user.slice/` ⇒ `--user` form) — NO launcher alternative string anywhere in the body**; `restart_audits` record + boot-settle; pin-17; bank row; JobItem disclosure; drill D7 |
| 6 | deferred ordering | `test_armed_demo_returns_scheduled_no_spawn:2396` mirror |
| 7 | **REAL restart-sim** | drill fake-live executor-crossing leg (provenance-seeded arm → `spec["provenance_ok"]` → `ENSEMBLE_RESTART_LIVE` through the real executor env → acceptance → stop/start → wake + `restart_gate` + `halt`/`restart` terminal coexistence). Mirror `tests/e2e/test_promote_stop_frozen_amnesty_e2e.py`, `tests/test_promote_cgroup_survivorship.sh`. ⛔ `mktemp` FAKE_HOME + escape guard |
| T8 | Rails extras | same-instance re-arm refused; **stale-arm boot closure covers BOTH variants AND FREES THE LOCK (post-closure pipeline-busy is gone; promote arming succeeds)**; budget survives simulated restart; `stop_forced_kill` NEW-event writer at `stop-ensemble.sh:485-501`; unit-name test; hatch: scoped-form render (system fixture) + script re-run succeeds arm-intact / refuses 78 post-cleanup; **`supervision_scope()` unit test vs both slice fixtures + lib.sh-drift guard** |
| T9 | Flips + enumeration | `:1936`, `:1500`, `:1397`, integration docstring; V-13 surfaces (mock `:516-519`, `RESTART_TOKENS` ×8, journal `:772`, banner `:519`); ari `tools_note.md:421`; drills `:468/535` + **§8.3 re-derivation asserted**; **`upgrade-overhaul-watch.md:77` rewrite asserted (no `restart_current`, no `--user` on the live form)**; audit pin-17; F-2 rewrite green |

---

## §9 File-Level Implementation Breakdown (ordered)

1. **`daemon/manager.py`** — `_inherited_provenance_windows` (adjacent `:1507`); funnel inherit lane (`:9395-9402`; `internal_agent:` only); drain mints `ENSEMBLE_RESTART_LIVE` IFF `spec["provenance_ok"]` via `extra_env` (`:4934-4940` region; promote `:4967-4970` untouched); `manager._settings_service` wiring.
2. **`daemon/tools/instance.py`** — `:952-955` fallback → reserved source (T0a′).
3. **`daemon/tools/upgrade_journal.py`** — `restart_audits` + `restart_window` (Python-only) in `ensure_extensions`; `unit_prefix` across 4 `UNIT_NAME_PREFIX` sites; `RESTART_TERMINAL_EVENTS` + `WAKE_TERMINAL_EVENTS` superset + kind-aware `wake_terminal_event_after`; `_OUTCOME_LABELS` ×4; boot-lane abandoned-closure BOTH variants **+ lock release (run_id-matched)**; `PendingWake` class + root fields.
4. **`scripts/upgrade/lib.sh`** — acceptance branch for ONLY `ENSEMBLE_RESTART_LIVE` (`:715-728` consumer); `require_live_guard` byte-identical.
5. **`scripts/upgrade/restart.sh`** — grace 15s; `ENSEMBLE_RESTART_LIVE`-only acceptance (`:92`); lock-free guard-trip event; report-only `/readyz`; **`restart_gate` written ALONGSIDE the existing terminal in both directions (detail channel = per-gate results)**; `unit_prefix`; TERM-pending note.
6. **`scripts/stop-ensemble.sh`** — **ADD the NEW `stop_forced_kill` event at the `:485-501` escalation site** (event does not exist today).
7. **`daemon/config.py`** — `ENSEMBLE_RESTART_MIN_UPTIME_S=600`, `ENSEMBLE_RESTART_BUDGET_24H=3`, `ENSEMBLE_RESTART_BUDGET_WINDOW_S=86400`, `ENSEMBLE_RESTART_GATE_REQUIRE_READY=1`.
8. **`daemon/tools/upgrade_tools.py`** — live refusal → provenance helper (**env-scoped: live-only gate + live-only rails b/c1/c2**) → rails → arm (`spec["provenance_ok"]` stamp, override capture in-lock, `_capture_user_source` extension, budget increment-first, reason constraint, **`dry_run` read-only verdict formatter — never `_refusal()`**); receipt (+`stamped_at`/`expires`, `busy-check` actual); tokens; `_full_doc_`; `_OUTCOME_LABELS` ×4.
9. **`daemon/services/upgrade_journal_sweep.py`** — `_coalesce_wakes` partitions by `wake.kind`; restart-kind reserved source; re-stamp deleted; **new `supervision_scope()` helper (cgroup slice prefix + `SUPERVISION_UNIT_DIR`/`SYSTEMCTL_BIN` discriminators, mirroring `lib.sh`'s classifier inputs, unit-tested against both slice fixtures)**; wake body per §5.3 (derived-scope hatch ONLY — launcher alternative absent; authorization age; None≠dict); fail-open preserved.
10. **`agents/maintenancer/meta.json`** — lift the deny entry.
11. **`agents/maintenancer/rule.md`** — rewrite preserving pin-17; same-merge seal re-pin; F-2 same-merge step.
12. **Docs** — maintenancer + ari tools_notes, README, ROLLOUT (optional), skill template **+ bank row**, `post-restart-arm-notify.md` §1, `upgrade-drills.md:468/535` + **§8.3 re-derivation**, **`upgrade-overhaul-watch.md:77` rewrite**.
13. **Tests** — per §8. **PR description must state (N8): no `.sql` migration ⇒ additive-only ⇒ `rollback_safe=true` (105 files, none `restart_*`).**

---

## §10 Risks & Mitigations

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | Spoofable provenance | 🔴 | Daemon-minted only; structural classification; TTL+depth+clear+wipe; rows (i)/(j) closed; T0/T0b |
| 2 | Upgrade-gate weakening | 🔴 | §2.6 two-layer; directional rule + T0a; N4 strengthening disclosed + T0a′; upgrade write exposure = leader-filed note |
| 3 | Executor live-guard DoA | 🔴→closed | `spec["provenance_ok"]` carrier; T0c; fake-live D7 |
| 4 | Terminal-set mutation closes promote ops | 🔴→closed | `_TERMINAL_EVENTS` untouched; kind-aware reader; T0b guard + e2e. **N6 asymmetry accepted** (restart wakes match the full superset — conservative direction only) |
| 5 | Wake re-mints direct provenance | 🔴→closed | Reserved source; no re-stamp; kind partition; T0d/T0b |
| 6 | Reply lost in soft grace window | 🟡 | 15s head-start disclosed; receipt disclosure; TERM-pending note; drain-reorder improve-lane |
| 7 | **Failed restart: daemon down, no rollback lane** | 🟡 | **Rev-5 final:** sole executable hatch = `sudo systemctl restart ensemble-main.service` (scope DERIVED via `supervision_scope()` — cgroup slice + env discriminators; system-level on live; `sudo -n` NOPASSWD, polkit manage-units authorized); the non-existent launcher alternative DROPPED everywhere (§3.3/§4f/§5.3); script re-run documented arm-intact-only; T8 asserts render + caveat |
| 8 | In-flight side effects + JobItem boundary | 🟡 | §4e gap list + three-signal predicate disclosed + pinned |
| 9 | Rails fail open | 🟡 | Epoch/unparseable-cooldown fail closed; budget journal-durable increment-first sliding window |
| 10 | Journal RMW growth / splice collision | 🟢 | Cap 50; reason charset |
| 11 | Live-guard var drift | 🟡 | Disjoint vars (§3.4); T0c; restart-only acceptance |
| 12 | `unit_prefix` reaper matching unverified | 🟡 | 4-site contract + unit test; residual |
| 13 | Docs drift / gate conflation | 🟡 | §7.4 incl. `upgrade-overhaul-watch.md` + drills §8.3 re-derivation; bank row; seal re-pin + pin-17; F-2 step |
| 14 | Stage-only ride | 🟢 | v0.25.5 expected; additive-only |
| 15 | **Carried residuals (council round-2 list, unchanged)** | 🟢 | `_capture_user_source` body; drain-vs-reply ordering `:5216/:5231/:5244`; silent-resume × inherited store (fail-closed default); reaper unit matching under `ensemble-restart-*`; `_format_wake_body` bodies `:671/:728` (post-build); guard-trip event-write self-locking (lock-free mandated, verify); exhaustive `additional_kwargs["source"]` enumeration; launcher `_journal_sweep` restart-branch e2e; **T0(a) pin caveat (review fix-cycle-1):** the upgrade-never-calls-restart-helper pin is a SOURCE-SUBSTRING check over `system_upgrade`'s body — it cannot see TRANSITIVE reachability; hardened belt-and-braces by the 1c literal-strict narrowing (`if op.kind == "restart":` guards the provenance capture inside `_arm_pending_wake_for_op`, making `_restart_provenance` unreachable from the upgrade arm path structurally) |

---

## Appendix — Provenance of this document

- **Rev 1** `552c556a7`: 3-worker fan-out (security/resilience/data-flow), architect-aggregated.
- **Rev 2** `6b4852b3c`: council round 1 — C-1/C-2/C-3, V-1..V-15, contract language.
- **Rev 3** `eb7d4d8ca`: council round 2 — F-1..F-6, F-9..F-11 (narrow delta; round-1 criticals CLOSED-WITH-GAPS).
- **Rev 4** `2fdca3c43`: approver round 1 — escape-hatch scope derivation (system-level verified); N1..N8.
- **Rev 5 (this)**: approver round 2 — **blocking:** the rev-4 ALTERNATIVE hatch command `bash launcher.sh restart_current` verified NON-EXISTENT (`launcher.sh:1668-1700` no subcommand dispatch; zero repo hits beyond the stale runbook) and DROPPED from §3.3/§4f/§5.3/§10 row 7 — sole form is the worker-verified `sudo systemctl restart ensemble-main.service` (system unit, `sudo -n` NOPASSWD, polkit manage-units); `docs/runbooks/upgrade-overhaul-watch.md:77` added to §7.4 (stale `restart_current` + broken `--user` both live there); `upgrade-drills.md` §8.3 manual procedure flagged for RE-DERIVATION (nohup lineage vs systemd-managed unit). **Notes:** env-scope column added to §4 + §2.4 env-conditioned predicate + §8 demo-restart assertion (no silent gating of free envs); dry_run verdicts via read-only formatter (`_refusal()` untouched); `supervision_scope()` discriminators named (cgroup slice prefix + `SUPERVISION_UNIT_DIR`/`SYSTEMCTL_BIN`) with the Python→shell bridge specified and drift-tested; stale-arm closure also frees the journal lock; authorization age (`root_stamped_at`/`expires_at`) surfaced in receipt/audits/notify; `stop_forced_kill` re-worded as a NEW event; `restart_gate`-vs-`halt` journal mapping specified (existing terminals stay authoritative; `restart_gate` rides alongside with per-gate detail); `busy-check` receipt line renders actual state. **Confirmed clean (no change):** version hygiene — 105 migrations, none `restart_*` ⇒ the `rollback_safe=true` statement is correct.
