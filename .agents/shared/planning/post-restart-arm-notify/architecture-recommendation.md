# Architecture Recommendation — Post-Restart Arm-Notify (validation pass)

- **Date:** 2026-10-03 · **Author:** architect (controller); analysis by 3 dispatched validation workers (trade-off-analysis, resilience-design, data-flow-design) + controller adjudication
- **Status:** READY — supersedes the prior draft of this file (preserved at commit `18430b04`); §FA1–FA6 anchors preserved so sibling-artifact citations resolve
- **Base:** branch `feature/post-restart-arm-notify`; code verified at current HEAD (post-`dac38fd8` report-delivery fix pack — FP1–FP4, PP1/PP2 — which changed the boot-wipe / premature-terminal surfaces this plan rides)
- **Method:** every load-bearing plan claim was checked against actual code. Verdicts below are CONFIRMED / CORRECTED-TO / REFUTED, each with the verified file:line. One cross-worker conflict was adjudicated by direct controller read (§Adjudication).

---

## 0. Executive Summary

**The planner's architecture is VALIDATED on all six focus areas**: `pending_wakes` on `releases/state.json` (ADR-039), boot + 90s-tick sweep delivery via `manager.enqueue_message` (ADR-040), terminal-class-event gating (ADR-042, with one critical fix), re-stamp routing for AC3 (ADR-041 — **proven end-to-end, no dispatch-logic change needed**), bounded D-FA1.2 supersession, and the env kill-switch (ADR-044, with one gap to close).

Three findings force plan deltas before Phase 2 lands:

1. 🔴 **The wake predicate as planned would never fire for the dominant case.** `_TERMINAL_EVENTS` (`upgrade_journal.py:983`) is `("commit", "rollback", "halt", "sweep_rollback", "sweep", "quarantine")` — **no `"restart"`** — and `reconcile_pending_op` returns `None` for `kind == "restart"` (`:1016`). Fix: sibling `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` + a wake-sweep-owned terminal reader.
2. 🔴 **Kill-switch + persisted records is unspecified.** With `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`, in-journal `pending_wakes` records linger unprocessed forever. Fix: abandon-on-switch-off semantics.
3. 🔴 **"Atomic by construction" is misleading — atomicity is by CALLER-ACQUIRED lock.** `journal_write` (`upgrade_journal.py:254-294`) acquires no lock; the arm sites do (`upgrade_tools.py:2167`, `:2719`). `arm_pending_wake` must sit INSIDE the lock-holding `try` block, pinned by a structural test.

Overall confidence: **High**. The one assumption that would flip FA1: a future requirement for DB queryability / multi-month audit retention over wake records (none exists today; the dict is bounded by coalesce-cap + grace).

### Headline verification table

| Plan claim | Verdict | Verified evidence |
|---|---|---|
| `journal_write` tmp+fsync+os.replace `:281-294` | CONFIRMED | `upgrade_journal.py:277-294` (pid/tid/uuid/ms tmp name, `os.fsync` `:287`, `os.replace` `:288`) |
| `ensure_extensions` idempotent extension point `:332-352` | CONFIRMED | three `if "X" not in data` blocks, single conditional `journal_write` |
| Cross-process lock shared shell↔Python | CONFIRMED | shell `lib.sh:1038` `lock_dir_path()` ≡ Python `upgrade_journal.py:562-563` `lock_dir()` → `<install_dir>/releases/rollback.lock.d`; both write `owner`/`run_id`/`heartbeat` (Python `:623-625`, shell `lib.sh:1070-1072`). **mkdir-lock, not flock**; stale-break `:633`; `lock_acquire(wait_s=0.0)` default |
| `_TERMINAL_EVENTS` includes `restart` `:983` | **REFUTED** | `:983` = 6 members, no `"restart"`; comment `:980-981`: "boot sweep owns restart-kind convergence" — the exclusion is deliberate for the PROMOTE-only reconcile; the wake sweep is the intended owner but must add its own constant |
| Centralized `is_pipeline_terminal` predicate | CORRECTED-TO | actual reader `_terminal_event_after(journal, armed_at)` `:986`, used only by `reconcile_pending_op` (`:1016` returns `None` for `kind=="restart"`) — wake sweep needs its own helper |
| `restart.sh` clears then journals terminal `:250-262` | CONFIRMED | `:259-262`: `close_txn` → `pending_op=null` → `pending_restart=null` → `history append restart`. GATE_FAIL branch `:248-256` clears same two + journals `halt`. **Neither branch touches `pending_wakes`** |
| `clear_pending_op` `:761-766` | CONFIRMED | writes only `pending_op` (+opt `pending_restart`) — `pending_wakes` survives by omission |
| rollback journals terminal LAST | CONFIRMED | `rollback.sh:154 atomic_flip` → `:169 restart_via_launcher` → `:177-` re-gate → `:209 journal rollback`; `:203`/`:211` journal `halt` (cap reached) |
| Live outright refusal `:2051-2058` | CONFIRMED | `return` before any journal write — live restarts structurally produce no wake record |
| `enqueue_message` `manager.py:7935-7996`, one txn, revive, PAUSED-hold | CONFIRMED | MessageQueue `:1771-1777` + Task `:1900-1923` + Event `:2061-2069` under one `session.commit()` `:2071`; revive `:1954-1976` (PAUSED excluded `:1949-1953`); hold `:2154-2161` |
| `internal_report:`/`internal_error_report:` inherit `original_source` at both dispatch sites | CONFIRMED | Site 1 in-graph `instance_messaging.py:3053-3128`; Site 2 final `message_processing_pipeline.py:720-795` |
| `system:*` final dispatch silently dropped | CONFIRMED | `dispatcher.py:158-165`: `registry.get("system")` → `None` → silent return. **Unreachable for chat-format wake sources** — see FA3 |
| `USER_ORIGIN_SOURCES` at `:1081-1092` | REFUTED on lines | `:1081-1092` is `EXECUTOR_ENV_ALLOWLIST`. Real classification: `classify_user_origin` `upgrade_journal.py:1962-2012`; `USER_ORIGIN_CHAT_SOURCE_TYPES = {telegram, slack, discord, whatsapp}` `:1944-1946`; `_USER_ORIGIN_EXACT = {"api"}` `:1940`. **`discord` IS whitelisted** |
| `stamp_user_origin_window` `:4230-4245` | CORRECTED-TO | spans `manager.py:4191-4253` (`:4230-4245` is the window write); dicts init `:1119`/`:1129`; **binding stamp for the wake turn is the natural one at `manager.py:8112`** |
| WC watchdog wake precedent `manager.py:7984` | CORRECTED-TO | `:7984` is the enqueue wrapper; actual watchdog wake sites `daemon/services/waiting_children_watchdog.py:1167,1215,1265,1593` (`source="system:watchdog"`) |
| Boot order: discard → worker pool → sweep | CONFIRMED | `manager.initialize()` `api.py:399` (discard `manager.py:761-776`, FP1 `preserve_in_flight=True` `:772`) → `setup_worker_pool` `api.py:439` → sweep construction `api.py:1489`; boot reconcile wrapped try/except `api.py:1506-1511` |
| Watchdog unaffected by new top-level key | CONFIRMED | `watchdog-watcher.sh:302` regex `'"event": *"(halt|sweep_rollback)"'` via `grep -oE` — history-events only, no top-level parser |
| Whitespace divergence benign | CONFIRMED | lib.sh `:638` compact vs launcher `:421` spaced vs Python `:281` compact; all three readers whitespace-tolerant (Python `json.loads` `:237`; shell brace-balance `lib.sh:540-565`; `"key":`+any-ws regex) |
| Launcher exports beat frozen-binary env | CONFIRMED | `launcher.sh:30-31` — env kill-switch reaches the daemon in prod installs |
| Env kill-switch precedent | CONFIRMED | `ENSEMBLE_SERVICE_TOOL_ENABLED` `api.py:1552-1573`, `ENSEMBLE_ORPHAN_F1_ENABLED`, `ENSEMBLE_PROACTIVE_COMPACTION_ENABLED` |
| `arm_pending_wake` sits after every refusal gate | CONFIRMED | restart path: call site ~`:2209` (after `write_pending_op` `:2176-2208`, inside lock `try`, lock at `:2167`); upgrade path: ~`:2850` (3-factor `:2494-2581+`, lock `:2719`, preflight `:2726-2802`, nonce burn + `write_pending_op` `:2827-2849`, inside `try`) |

### Adjudication note (cross-worker conflict)

Worker A marked `_TERMINAL_EVENTS` "CONFIRMED via downstream usage" (the events scripts journal match the listed set); Worker B **directly read the constant** and refuted membership of `"restart"`. Controller re-read `:983` directly: 6 members, no `"restart"`. Direct read overrules downstream inference — the plan delta in FA2 below is required, not optional.

---

## FA1 — Persistence home: journal vs DB vs parallel file (ADR-039) — **VALIDATED**

**Options:** (A) `pending_wakes: dict[str, PendingWake]` keyed by `run_id` on `releases/state.json` · (B) SQLModel table (ordered+checksummed migration, PG primary) · (C) parallel `wake_records.json`.

| Approach | Complexity (20%) | Scalability (20%) | Maintainability (25%) | Risk (20%, inv) | Cost (15%, inv) | Weighted |
|---|---|---|---|---|---|---|
| **A: journal section** | 4 — one dataclass + helpers reusing `ensure_extensions`+`journal_write` | 4 — bounded by coalesce-cap 16 + 600s grace; file-scoped | 5 — same lock, same envelope, same `from_json` discipline (`:735-742`); runbook-known surface | 4 — single-writer proven; **shared cross-process lock verified** | 5 — no migration, no DB schema across live/demo/sandbox/dev | **4.40** |
| B: DB table | 2 — SQLModel + repo + migration + runner updates | 5 — queryable, indexable | 2 — parallel system-of-record; 4-DB blast | 2 — **second atomicity surface**: table write and journal write not co-transactional; crash between = unknown state | 2 — migration fleet cost (42+ existing files) | 2.60 |
| C: parallel file | 3 — new file/lock/init path | 3 — bounded, O(N) parse | 3 — two surfaces to keep consistent | 3 — two-file coordination (D-FA1.1 rejection) | 4 — no migration but new machinery | 2.95 |

**Recommendation: A (the planner's choice).** Maintainability dominates (25%): everything about the wake record reuses verified machinery. The crux — write-contention/atomicity across the shell/Python boundary — resolves in A's favor because the lock is genuinely shared: `rollback.lock.d` with the owner/run_id/heartbeat protocol on both sides (`lib.sh:1038`+`1070-1072` ≡ `upgrade_journal.py:562-563`+`623-625`). Crash consistency: one `journal_write` envelope covers arm + wake; `JournalTorn` (`:215`, `:231-241`) is the proven safe-no-op signal. Debuggability: few-KB human-readable file, visible via `release_info`; watchdog provably ignores the new key.

**Sibling-record lifetime (verified):** `pending_op` is cleared by `restart.sh:259-262` (and GATE_FAIL `:248-256`) and by `clear_pending_op` `:761-766` — **none of these touch `pending_wakes`**, so the wake record outlives the executor's clearing exactly as the plan claims. Rollback/halt paths journal terminal events (`rollback.sh:209`, `:203`, `:211`) that the wake predicate consumes. `pending_wakes` readers are daemon-only: launcher `_journal_sweep` operates on `in_flight` only (`launcher.sh:746`, restart-kind excluded `:775-778`); watchdog regexes history only. If the daemon never returns, records are bounded by grace-abandonment and remain harmless (watchdog-ignored, human-readable).

**Plan deltas:**
- **MUST** — reword "atomic by construction" → "atomic under the caller-acquired journal lock": `journal_write` (`:254-294`) has no internal lock; serialization comes from `journal_lock_acquire` at the arm sites (`upgrade_tools.py:2167`, `:2719`). `arm_pending_wake` MUST be called inside the same lock-holding `try` (restart ~`:2209`, upgrade ~`:2850`, both after `write_pending_op`).
- **MUST** — add a structural test (extend T6.x) pinning `arm_pending_wake`'s call-site position inside the lock-holding `try` — the next refactor that pulls it out is a silent torn-write risk.

---

## FA2 (plan §FA4 / ADR-042) — Terminal-state gating — **VALIDATED with one CRITICAL fix**

**The critical defect:** the plan's predicate set is wrong at HEAD. `_TERMINAL_EVENTS` (`:983`) lacks `"restart"`, and the only existing terminal reader (`_terminal_event_after` `:986`) is scoped to `reconcile_pending_op`, which is PROMOTE-only (`:1016` returns `None` for restart-kind — deliberate per comment `:980-981`, which assigns restart-kind convergence to "the boot sweep", i.e. this feature). As planned, **the wake would never fire for intentional restarts — the dominant case** (`restart.sh:262` journals `"restart"`, which the constant rejects).

**Fix (MUST, <20 LOC):** `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` in `upgrade_journal.py`, consumed by a wake-sweep-owned helper mirroring `_terminal_event_after(journal, armed_at)` (armed_at-scoped, run_id-matched, restart-kind aware). Do NOT mutate `_TERMINAL_EVENTS` itself — the reconcile's PROMOTE-only semantics depend on it.

**Can the wake fire while a rollback is still possible? No — harmless by ordering.** `rollback.sh` journals `rollback` LAST (`:154 atomic_flip` → `:169 restart_via_launcher` → `:177-` re-gate → `:209 journal`). A wake firing on the `rollback` event therefore fires post-completion; and even a mid-rollback wake is harmless by message design — the wake instructs `upgrade_status(run_id)`, which reads live `state.json`. Cap-halt (`:211` journals `halt`) is covered by the constant.

**Downtime blind spot (confirmed):** the daemon-side executor drains die with the daemon; during downtime the shell scripts are the terminal-event source — `restart.sh:262` (restart), `rollback.sh:209/:203/:211` (rollback/halt), promote commit — all journal into `state.json`, which the post-boot sweep reads. **Uncovered gap:** launcher burst-abort (`launcher.sh:902-939`) journals NO terminal event on the plain exit-1 path → the wake abandons at grace (600s) and the user gets no notification of the burst-abort itself. Acknowledge in the risk register; the stays-down story remains ADR-025(b)'s watchdog (complementary, not superseded).

**Alternatives (5-axis):** A terminal-gated + 90s tick [plan] vs B immediate-wake-on-boot + poll-in-body vs C deferred-until-terminal + backoff. A wins: B fires pre-terminal (AC4 race) and wakes on every arm including no-op arms; C adds scheduler complexity for zero gain over A's tick-retry. **Validate A.**

---

## FA3 (plan §FA3.3 / ADR-041) — Routing re-stamp, AC3 — **VALIDATED: delivers as-is**

**AC3 verdict: YES.** End-to-end proof (all hops verified):

1. Wake enqueued `manager.enqueue_message(instance_id, body, source="discord:user123", priority=2, metadata={system_context…})` → MessageQueue row `source="discord:user123"`, `type=HUMAN` (msg_type derives from source prefix at `instance_messaging.py:1697-1710`: `internal_report:`→COMPLETION_REPORT, `internal_error_report:`→ERROR_REPORT, `internal_agent:`→AGENT, **else→HUMAN** — the plan §8 concern resolves correctly: the wake is a HUMAN-type trigger, and the agent's report is dispatched separately).
2. **Site 1 (in-graph, progressive — the path that actually carries the message):** `instance_messaging.py:3053-3128` — `"discord:user123"` is neither `internal_report:`/`internal_error_report:` nor `system:` → `else` branch → `dispatch_source = message_source = "discord:user123"` (load-bearing).
3. Each AI chunk → `source_dispatcher.dispatch_message` (`instance_messaging.py:4561` → `dispatcher.py:189-266`) → parses `discord`/`user123` → `registry.get("discord")` → `adapter.send(OutgoingMessage(external_user_id="user123", …))` → **Discord REST → user sees the report in the arming chat**.
4. **Site 2 (final completion):** `message_processing_pipeline.py:720-795` — non-internal → `dispatch_source = context.message_source` → `dispatcher.py:124-128` skips duplicate (source already in `_progressive_sent_sources`). Zero-progressive-chunk turns converge via `dispatch_completed` on the same adapter.
5. The `system:*` silent drop is real (`dispatcher.py:158-165`) but **unreachable** for chat-format wake sources; `internal_report:*` original_source inheritance is equally bypassed. The planner's choice of `source=<recorded arm-time chat source>` sidesteps both drop paths with zero dispatch-logic changes — the minimal-risk path is confirmed minimal.

**Re-stamp mechanism (corrected):** the plan's pre-enqueue `stamp_user_origin_window` is **redundant-but-defensive**. The binding stamp is the natural one at `manager.py:8112` (`_process_message_with_tracking` stamps every processed message with its own source) — it fires for the wake turn even if the daemon restarts between enqueue and processing. Window lifetime: `NONCE_TTL_S = 3600s` (`manager.py:4220`), NOT turn-bound — an `upgrade_status` call mid-wake-turn passes 3-factor factor-2 (`upgrade_tools.py:2540-2554`, fail-closed on expiry/unparseable). `discord` is whitelisted via `USER_ORIGIN_CHAT_SOURCE_TYPES` (`:1944-1946`). Window clear on next non-user-origin dispatch (`manager.py:4247`) does not endanger the wake turn.

**5-axis (A re-stamp+recorded source 9.0 vs B reply-to-source field 6.0 vs C channel-override envelope 5.5, worker scoring):** A wins Complexity/Maintainability/Cost — zero dispatch-path code, AC6 intact; B adds two read sites to keep in sync (drift hazard); C is a new messaging primitive (AC6 violation).

**Plan deltas:**
- **SHOULD** — add a Site 1 unit test (progressive dispatch, `instance_messaging.py:3053-3128`, `message_source="discord:user123"` → assert `dispatch_source` verbatim); T3.1–T3.4 cover Site 2 only. The routing stub must capture `dispatch_message` AND `dispatch_completed`.
- **SHOULD** — T3.3 must assert "window is set after wake delivery", NOT "exactly one stamp call" — double-stamping (plan stamp + natural `:8112`) is structural.
- **SHOULD** — document the pre-enqueue stamp as defensive (natural stamp is binding) or drop it.
- 🟢 known limitation (flag, out of scope): `_progressive_sent_sources` is keyed by source_id (`"discord"`), not full source — multi-user concurrent arms on one instance could cross-suppress; single-user arming verified safe. Non-discord chat formats ride the identical dispatcher path but were not exercised.

---

## FA4 — D-FA1.2 supersession — **VALIDATED, bounded; record sufficient + optional polish**

Bounding verified on all three axes: (i) **only successfully-armed runs mint records** — both `arm_pending_wake` call sites sit after every refusal gate (live outright refusal `:2051-2058` returns pre-journal; pipeline-busy at lock `:2167`/`:2719`; 3-factor `:2494-2581+`; nonce burn `:2827-2849`); (ii) **only the arming instance is woken** — record carries `armed_by_instance` (`PendingOp` `:719`); front-door ari fallback is strictly the missing-instance recovery, journaled, never a default; (iii) **pull model intact** — `upgrade_status`/`release_info` untouched; the sweep adds a subroutine, changes no shared surface.

`supersession-record.md` is **sufficient** (project convention: historical ADRs stay as-written). Optional polish (NICE): inline one-line SUPERSEDED banner on parent §D-FA1.2 (the record §8 already anticipates it); CHANGELOG/RELEASE_NOTES line at release; ari prompt-level note stays with the prompt-maintenance initiative (R-7 follow-up).

---

## FA5 (plan §FA3.1/§FA5 / ADR-040+043) — Boot ordering, wedge-safety, idempotency, GC — **VALIDATED with deltas**

- **Ordering confirmed at HEAD (post-dac38fd8):** `manager.initialize()` `api.py:399` — `discard_on_startup` (`manager.py:761-776`, FP1 `preserve_in_flight=True` `:772`) runs BEFORE sweep construction `api.py:1489`; wake rows are CREATED by the sweep AFTER the discard → they survive. `setup_worker_pool` `:439` precedes the sweep → workers ready (R-10 closed).
- **Wedge-safety proven by pattern:** boot reconcile wrapped `try/except Exception` `api.py:1506-1511`; `JournalTorn`/`OSError` caught at `:1013`/`:1039`; malformed `pending_wakes` must go through a `from_json` field-filter mirroring `PendingOp.from_json` `:735-742`; `install_dir=None` (dev envs) → no-op sweep (precedent `UpgradeJournalSweepService.__init__ :99-103`).
- 🟡 **Lock discipline (SHOULD):** `lock_acquire(wait_s=0.0)` default fails fast under concurrent shell-writer contention → the sweep's CAS writes (`pending→delivering→delivered`) MUST use `wait_s≈30` + single retry. Deadlock with `launcher._journal_sweep` is structurally absent (launcher runs pre-daemon).
- **Idempotency:** CAS lifecycle sound; crash between enqueue and `mark_wake_delivered` → next boot re-delivers (at-least-once; harmless duplicate; R-1 accepted — `upgrade_status` is idempotent). **SHOULD:** specify `mark_wake_delivered` failure semantics (retry once, else leave `delivering` for next boot). Edge: `discard_on_startup=False` operator override can pile crashed-boot leftovers — lifecycle removal bounds it (🟢).
- **GC deferral is SAFE:** ~300 B/record; 10K records ≈ 3 MB worst case; watchdog-blind; grace-abandonment removes un-deliverable records. Cite the bound in risk-register R-9 (NICE).

---

## FA6 (ADR-044) — Kill-switch — **env-var VALIDATED; one gap to close**

Env-var beats meta/config: precedent (`api.py:1552-1573` family), launcher-export precedence guarantees delivery to the frozen binary (`launcher.sh:30-31`), restart-to-take-effect is acceptable (matches precedent). Default-ON validated (the deliverable IS zero-user-action).

🔴 **Gap (MUST):** persisted-record interaction is unspecified. With the switch OFF, in-journal records are never processed (sweep short-circuits) and linger indefinitely; later re-enable → stale-wake flood. **Recommended semantics: abandon-on-switch-off** — when the sweep observes the disabled env with records present, it marks them `abandoned` with `reason=kill_switch_off` + one `history` event, one-time. Reject hold (operator footgun, manual purge) and late-deliver (user-hostile flood). Arm-side short-circuit (no new records while OFF) is already correct.

---

## Consolidated plan deltas (developer MUST apply)

**MUST (before Phase 2 lands):**
1. `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` (`upgrade_journal.py`); wake-sweep helper mirrors `_terminal_event_after` (`:986`) but is restart-kind aware. Do not mutate `_TERMINAL_EVENTS` (PROMOTE-only reconcile depends on it, `:1016`).
2. Kill-switch off-semantics: abandon-on-switch-off (`reason=kill_switch_off` + history event, one-time sweep pass).
3. Reword ADR-039/§FA2.1 atomicity claim (caller-acquired lock, not `journal_write`-internal) + structural test pinning `arm_pending_wake` inside the lock-holding `try` (restart ~`:2209`, upgrade ~`:2850`).

**SHOULD:**
4. Sweep CAS writes: `lock_acquire(wait_s=30)` + single retry (default `0.0` fails fast under shell contention).
5. `mark_wake_delivered` failure semantics: retry once, else leave `delivering` for next boot.
6. Site 1 progressive-dispatch unit test (`instance_messaging.py:3053-3128`) — T3.1–T3.4 cover Site 2 only; stub captures `dispatch_message` AND `dispatch_completed`.
7. T3.3 asserts "window set after delivery", not "exactly one stamp" (double-stamp is structural via `manager.py:8112`).
8. Document pre-enqueue re-stamp as defensive/redundant (natural stamp `manager.py:8112` is binding).
9. Risk-register: acknowledge launcher burst-abort gap (no halt journaled → wake abandons at grace; no separate burst-abort user notification).
10. Sweep hardening: `from_json` filter for malformed `pending_wakes` (mirror `:735-742`); `JournalTorn`/`OSError` catch; `install_dir=None` no-op.

**NICE:**
11. Cite GC bound in R-9 (~300 B/record; 10K ≈ 3 MB).
12. Supersession polish: parent §D-FA1.2 banner + CHANGELOG line.
13. Flag `_progressive_sent_sources` source_id-keying as a known multi-user limitation; note telegram/slack ride the identical (unexercised) path.
14. Optional hardening: stamp `original_source` on wake metadata (not required for AC3; hardens R-14).

## Risks

- 🔴 Wake-never-fires-for-restarts if delta #1 is skipped (predicate defect is at HEAD, verified `:983`).
- 🔴 Kill-switch footgun if delta #2 is skipped (records linger → stale flood on re-enable).
- 🔴 Silent torn-write if a future refactor moves `arm_pending_wake` outside the lock try (delta #3 pins it).
- 🟡 `wait_s=0.0` CAS failures under shell contention (delta #4).
- 🟡 Burst-abort: user receives no notification of the abort itself (watchdog ADR-025(b) covers stays-down only).
- 🟢 GC deferral (bounded, 3 MB worst case); duplicate wake on `delivering`-crash (idempotent consumer); multi-user progressive-suppression cross-talk (out of scope, flagged).

## Open questions / unverified

- 3-factor gate tail `upgrade_tools.py:2582-2660` and arm tail `:2850-2880` were read to `:2581+`/`:2849` — placement consistency verified, endpoints not byte-verified (bounded risk; Phase 1 tests will pin).
- Telegram/slack chat-format dispatch: identical code path, not exercised by this analysis.
- Fresh-instance first-message progressive behavior (no prior `original_source`) — converges via `dispatch_completed`; Phase 3 T5.15 is the structural test.

## Cross-references

- `decisions.md` ADR-039…ADR-044 (all six stand; ADR-039 wording fix + ADR-042 predicate fix + ADR-044 off-semantics per deltas #1–#3)
- `risk-register.md` R-1…R-20 (R-9 bound citation, R-10 closed, burst-abort acknowledgment)
- `test-strategy.md` T3.* / T5.* / T6.* (deltas #6, #7, #10 adjust assertions; delta #3 extends T6.x)
- Prior draft of this file: commit `18430b04` (superseded; FA/D-FA anchors preserved)
