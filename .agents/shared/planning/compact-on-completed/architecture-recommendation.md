# Architecture Recommendation: /compact on COMPLETED Instances

Date: 2026-08-31
Branch analyzed: latest @ b379e576 (BE files verified byte-identical on checked-out `feature/slash-command-autocomplete` @ 4f729f43 via `git diff b379e576 HEAD` — FE-only delta)
Mode: Standard Design — 2-worker fan-out (`resilience-design` 3916821c, `data-flow-design` af3f0c7e)
Supersedes: O-B4 rejection of terminal instances (premise eliminated by C1 Variant A persistence)

## Verdict: MEDIUM (low end) — IMPLEMENT

The original O-B4 hazard is structurally eliminated for `completed`. The code change itself is
small (a compact-specific status set + two gate edits + guidance-copy split), but it requires
precise test surgery across three parametrized suites, one new e2e canary on a real
`status="completed"` row, and strict discipline NOT to mutate the shared canonical constant.
No schema, no migration, no cross-service change. Meets the user's easy-to-medium bar.

## Findings by Assessment Question

### 1. Checkpoint/lifecycle — SAFE
- A COMPLETED instance's checkpoint is shape-identical to an idle post-turn one from the
  executor's perspective: `state.next == ()`, `messages` + `compacted_at` in `state.values`;
  the executor's reads are status-agnostic after the gate (`compact_executor.py:668-727`).
- Variant A persistence (two `aupdate_state` WITHOUT `as_node`, `compact_executor.py:1533-1548`)
  never touches `next` → revive-on-send (`instance_messaging.py:1486-1510`, `:3580-3601`
  `astream(graph_input)` with `is_retry=True`) runs the agent normally. The documented
  `as_node="agent"` collapse (`instance_messaging.py:1132-1140`) requires
  `interrupt_before=['agent']` — zero production agents carry it (`grep -rn interrupt_before
  agents/ daemon/` empty), and the no-`as_node` recipe closes the window regardless
  (`test_compact_executor_revive_brick_e2e.py:192-311` brick vs `:580-655` canary).
- Checkpoint pruning: maintenance Op B/C (`maintenance.py:534-649`) deletes COMPLETED
  checkpoints after TTL 168h or history > 500. Pre-checks tolerate `aget_state → None`
  (graceful noop, `compact_executor.py:672-675, 1043-1068`). See Risk R3 for the race.

### 2. Executor matrix — TRIVIAL PATH
- WS-6: a quiescent COMPLETED instance reads `run_status == "idle"` → the
  "quiescent by definition" branch (`compact_executor.py:942`); pause/quiesce/resume are
  skipped entirely; `needs_pause_resume` is False (`:788`); `_graph_tasks` is empty
  (`manager.py:3460-3466`).
- The D3 2-ordered-aupdate recipe holds with no in-flight work_id — it writes only the
  LangGraph checkpointer (`messages` replacement, then `compacted_at`), the exact recipe the
  C1 canary pins on a real quiescent graph (`revive_brick_e2e.py:580-655`).
- Frozen-state consumers: none violated. `reconcile_turn_mirror` operates on Task/JobItem
  mirror tables, untouched. Dependency watchers key on `source_task_id`, not checkpoint
  state — a compact is invisible to them.

### 3. Status semantics — NO INVARIANT BROKEN
- Compact mutates only the checkpointer; the `instances` row stays COMPLETED. No
  `terminal_reason`/`completed_at` columns exist to trip (`repositories/instance/models.py:47-107`);
  the ORM `before_update` listener never fires.
- Report replay: GET /messages synthetic system message is unaffected (system prompt is not
  in the checkpoint by design; compact replaces `messages` via the normal compaction surface).
- CommandStateRegistry: the success terminal entry sits in the per-instance ring until
  TTL 600s / LRU 20 (D-B8 residue) — observable, benign; the 10s dispatcher rate-limit bounds
  re-issues. The dispatcher's terminal gate continues to reject FUTURE commands on COMPLETED
  only if we keep `completed` out of the compact-specific reject set — which we do not; after
  this change `/compact` itself is eligible on COMPLETED, other commands unchanged.

### 4. Blast radius — ONE CONSTANT, SEVEN HOMES, EDIT EXACTLY ONE
`TERMINAL_INSTANCE_STATUSES` canonical home is `daemon/constants.py:250-255` with 5+
downstream consumers that MUST NOT change: queue-stats short-circuit
(`instance_messaging.py:4239`), agent-tool terminal-revive (`tools/instance.py:908`),
post-fire re-purge (`instance_lifecycle.py:416`), stale-task recovery
(`stale_task_recovery.py:330`), job recovery (`job_recovery_service.py:225`), job feedback
observer (`job_feedback_observer.py:2342/2850/3533`, PAUSED-inclusive variant at `:103-108`).
The dispatcher holds a LOCAL duplicate (`command_dispatcher.py:108-110`) consumed only by the
dispatcher gate (`:956`) and the executor defense-in-depth guard (`compact_executor.py:613`).
**Introduce the compact-specific set at that local site; leave the canonical untouched.**
`tests/unit/tools/test_instance_tools.py:199-201` pins the canonical frozenset — it must stay
green (it is the tripwire against accidental canonical mutation).

### 5. Edge cases — ALL BENIGN OR PRE-EXISTING
- Noop floors/recency: identical behavior on COMPLETED (status-blind pre-checks).
- Compact-then-never-message: bounded residue (ring entry TTL 600s); `next=()` unchanged.
- N2 parked-injection: neutral — compact never reads/writes the RAM FIFO or `set_injection`;
  parked entries stay parked. Add a "does not interact with N2" line to the spec.
- Concurrent compact + send: the per-instance ExecutionGate (`execution_gate.py:108-144`)
  serializes engine+persistence against turn dispatch; a mid-compact send persists its
  MessageQueue+Task row and claims after the gate releases. Either order is correct
  (compact-then-turn runs on compacted state; turn-then-compact persists on fresh state).
- Crash mid-compact: the two `aupdate_state` calls are non-atomic (pre-existing, same shape
  as the proactive path); a crash between them leaves no `compacted_at` → next `/compact`
  re-proceeds. Idempotent in effect.

## Risk List (probability × impact × mitigation)

| # | Risk | P | I | Mitigation |
|---|------|---|---|----------------|
| R1 🟡 | Implementer mutates canonical `TERMINAL_INSTANCE_STATUSES` (constants.py:250) → breaks revive-on-send queue-stats short-circuit, agent-tool revive, recovery sweeps | Low (with this plan) | High | Plan mandates compact-specific set at `command_dispatcher.py:108-110` only; `test_instance_tools.py:199-201` pin must stay green — treat as merge blocker |
| R2 🟡 | Compact-vs-send revive race mid-compact | Low | Medium | Already serialized by ExecutionGate + ack-time guard; add one race-order test in the e2e canary file (optional but cheap) |
| R3 🟢 | Maintenance sweep deletes COMPLETED checkpoint mid-compact (Op B/C does not take the execution gate; 168h TTL or >500 history) | Low (stale instances only) | Medium (failed terminal at worst) | Pre-existing class, not introduced here; graceful noop if deleted pre-read. File as follow-up: maintenance should acquire the per-instance gate |
| R4 🟢 | C1 canary gap: recipe proven on quiescent IDLE rows, not `status="completed"` rows via the executor status-read path; `interrupt_before` + COMPLETED + no-`as_node` combination uncovered | Medium | Low (structural immunity holds) | New canary in this change plan closes it |
| R5 🟢 | Guidance-copy drift: shared `TERMINAL_INSTANCE_GUIDANCE` ("Send a message to start a new turn, then /compact.") becomes wrong for `completed` | Certain if unaddressed | Low | Split copy in plan step 1 |
| R6 🟢 | D-B8 ring residue: success entry visible until TTL 600s | Certain | Negligible | No action; document |
| R7 🟢 | Non-atomic two-aupdate crash window; N2 interplay | Pre-existing / neutral | Low | No action; note in spec |

## Exact Change Plan (ordered)

1. **`daemon/services/command_dispatcher.py`**
   - At the local terminal-set site (`:108-110`): add
     `COMPACT_REJECT_STATUSES = frozenset({"terminated", "error", "failed"})`
     (do NOT derive from / mutate the canonical import).
   - Step-4 sync gate (`:948-969`): reject only on `COMPACT_REJECT_STATUSES`; `completed`
     falls through to availability → pending-injections → rate-limit → record_start.
     Gate ordering invariant (terminal beats busy/rate-limited) is preserved for the other 3.
   - Split `TERMINAL_INSTANCE_GUIDANCE` (`:108-119`): the remaining 3 statuses keep
     "Send a message to start a new turn, then /compact." (still accurate — only a real
     message revives them); `completed` no longer produces this rejection.
2. **`daemon/services/compact_executor.py`**
   - Defense-in-depth guard (`:613-627`): switch to `COMPACT_REJECT_STATUSES` (import from
     the dispatcher, mirroring the existing import at `:118-119`).
   - Update the status-gate rationale comment/docstring (`:36-52`): O-B4 superseded by C1
     Variant A for `completed` only; `terminated/error/failed` remain rejected.
3. **Tests (split the all-4 parametrizations)**
   - `tests/unit/services/test_command_dispatcher.py:1094-1133`
     (`test_each_terminal_status_rejects_at_dispatch`): keep terminated/error/failed
     rejecting; add COMPLETED-passes-the-terminal-gate case.
   - `tests/unit/routers/test_slash_commands_router.py:1212-1248`: dedicated COMPLETED case
     flips from rejection to accepted (proceeds past the gate).
   - `tests/unit/services/test_compact_executor.py:1492` (all-4 rejection): keep 3, add
     COMPLETED success-path test through the executor.
4. **`tests/unit/services/test_compact_executor_revive_brick_e2e.py`**
   - Update the 4-status rejection pins: `completed` now proceeds.
   - ADD canary (closes R4): real LangGraph, real instance row `status="completed"`,
     full executor path → Variant A persist → revive-on-send `astream` runs the agent.
     Variant with `interrupt_before=['agent']` to pin no-`as_node` immunity on COMPLETED.
5. **`daemon/services/_checkpoint_utils.py:1-64`** — amend the brick-rationale doc block:
   O-B4 hazard closed for `completed` by Variant A; cross-reference this recommendation.
6. **FE** — NO required change (`chat.component.ts:1628` renders `ack.detail` verbatim; the
   `terminal_instance` branch simply stops firing on COMPLETED). Optional: availability note
   in `command-registry.service.ts:42-43` description. Autocomplete branch unaffected.
7. **Verify**: `tests/unit/tools/test_instance_tools.py:199-201` stays green (canonical
   untouched); run the three split suites + the new canary.

## What NOT to do
- Do NOT edit `daemon/constants.py:250-255` (canonical set).
- Do NOT relax the executor's other status branches (WS-6 unchanged — COMPLETED already
  takes the idle branch).
- Do NOT extend to `terminated/error/failed` — their revive semantics and error-surfaces
  were never assessed here and O-B4-era caution still applies to them.

## Open Questions / Follow-ups (non-blocking)
- R3: make maintenance Op B/C acquire the per-instance execution gate (separate ticket).
- N2 (pre-existing, own ticket): injected-on-COMPLETED messages never persist — unrelated to
  compact, but a compacting user should know parked injections are not rescued.
- Optional: assert `_graph_tasks` emptiness invariant on COMPLETED in the new canary
  (currently code-inspection-only per worker B).
