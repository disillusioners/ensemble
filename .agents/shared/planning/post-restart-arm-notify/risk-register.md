# Risk Register — Post-Restart Arm-Notify

- **Initiative:** post-restart-arm-notify
- **Branch:** `feature/post-restart-arm-notify` (the planning-authoritative tip)
- **Owner:** architect (controller) — single-author; companion `architecture-recommendation.md` (FA1–FA6) cross-referenced as §FA, `decisions.md` as ADR-NNN
- **Siblings (this directory, single-author):** `plan-overview.md` (goal / ACs / non-goals); `architecture-recommendation.md` (the analysis); `decisions.md` (ADRs); `test-strategy.md` (AC7)
- **Cross-reference:** the parent initiative's risk register at `.agents/shared/planning/self-restart-upgrade-phase2/risk-register.md` (R-SR01…R-SR16) remains **UNCHANGED** for the non-wake concerns; the wake-specific risks are enumerated below. The wake is additive; no Phase-2 risk is regressed.

---

## ⚠️ HARD CONSTRAINT (inherited verbatim from Phase 2)

> NEVER touch the live/production ensemble environment — it is the
> running environment of Ari and all live agents (~/agents-ensemble,
> port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live
> pids must remain untouched). ALL work/testing/drills in dev and
> demo only. If any plan step would require touching live, mark it
> as USER-GATED and design it as an explicit user-confirmed action.
> Sandbox instances (own port + throwaway PG) are fine.

**Every risk below is evaluated against this constraint.** The wake
record is written only for non-live arms (`system_restart`
outright-refused on live at `upgrade_tools.py:2051-2058`;
`system_upgrade` on live only on verified-arm path, which
inherits the F2 surface). A live-arming wake is structurally
impossible by construction; live-wake remains USER-GATED.

---

## Risk Summary Table (ordered by severity: likelihood × impact)

| ID | Risk | L | I | Mitigation (one-line) | Owner § |
|----|------|---|---|----------------------|---------|
| **R-1** | Wake delivered but agent's response interrupted by another restart — user never receives the report | M | M | One-shot delivery; pull-model recovery via `upgrade_status`. Accepted (Q3 in `plan-overview.md`). | §FA1.2 / §FA5.3 |
| **R-2** | Multiple arms over a long downtime create N wake records — wake surface floods | M | M | Coalesce by `arming_instance_id`; bounded by `PENDING_WAKE_COALESCE_MAX = 16`; overflow journaled to `history` as `wake_coalesce_overflow`. | §FA5.2 |
| **R-3** | Wake re-stamp of user-origin window could enable a forged live arm | L | H | Re-stamp source is the recorded arm-time source, which already passed the F2 surface on the verified-arm path. For non-live arms, re-stamp is informational; the 3-factor gate does not run. | §FA3.3 (ADR-041) |
| **R-4** | `journal_write` failure during arm — wake record absent, but `pending_op` also absent (atomic) | L | M | The arm itself failed; the existing `JournalTorn`-or-`OSError`-or-`KeyError` catch unwinds. No asymmetry (single `journal_write` envelope). | §FA2.1 (ADR-039) |
| **R-5** | Long-running pipeline that does NOT journal a terminal event for >`abandon_after` | M | M | Wake is abandoned; user-inquiry recovery. Grace = `PENDING_WAKE_GRACE_S = 600` (default); tunable. The `abandoned` record journals a `wake_abandoned` history event. | §FA4.1 / §FA5.2 (ADR-042) |
| **R-6** | `enqueue_message` raises `InstanceNotFound` — the arming instance was terminated/expired between arm and wake | M | M | Front-door ari fall-back; otherwise journaled `arm_notify_no_instance` notice. AC5 covered. | §FA5.1 (ADR-043) |
| **R-7** | Wake is delivered but the LLM in the agent's first turn does NOT call `upgrade_status` — the user receives a "this is an auto-wake" pointer but no outcome | M | M | Wake body is a self-describing pointer; prompt-level instruction in ari/jober prompts is the prompt-side guard. **Out of scope for this feature's code**; flagged for the prompt-maintenance initiative. | §FA3.2 (ADR-040) |
| **R-8** | The live-outright-refusal for `system_restart` is bypassed — wake record is written for a live arm | L | **H** | The refusal at `upgrade_tools.py:2051-2058` is the same code path the existing live-outright-refusal uses; the wake inherits the gate by structural coupling (the wake is written in the same `journal_write` envelope as the `pending_op`, and the `pending_op` write is gated on the live check). Kill-switch is a NO-OP for the live case. | §FA5.5 (ADR-044) |
| **R-9** | The journal's `pending_wakes` dict grows unbounded over many arm cycles (records removed only on `delivered` or `abandoned`) | L | M | Records are removed on terminal transition (structural); the `abandon_after` clock bounds un-deliverable records. **GC-deferral bound (architecture delta #11): ~300 B/record; 10K records ≈ 3 MB worst case — watchdog-blind and human-readable, so deferral is SAFE.** A new test (T1.6 / T5.7) asserts the structural removal. A potential follow-up: a periodic GC sweep that drops records past `abandon_after + 24h` (out of scope for this feature; flagged for follow-up). | §FA1.2 (ADR-039) |
| **R-10** | ✅ **CLOSED (architect validation c77c5ff1, post-dac38fd8):** boot ordering CONFIRMED — `discard_on_startup` wipe (`manager.py:761-776`, FP1 `preserve_in_flight=True` `:772`) runs BEFORE sweep construction (`api.py:1489`), so wake rows created by the sweep survive the discard; workers ready via `setup_worker_pool` (`api.py:439`) which precedes the sweep. The wake's `enqueue_message` fires during the boot pass while the worker pool is still spinning up — the wake is held in PENDING and may miss the boot window | — | — | Boot order verified at HEAD: `manager.initialize()` (`api.py:399`) → `setup_worker_pool` (`api.py:439`) → sweep construction (`api.py:1489`); boot reconcile wrapped `try/except` (`api.py:1506-1511`). T2.1 (test) asserts the wake is enqueued, not held. | §FA3.1 (ADR-040) |
| **R-11** | Two arming instances race the same arm (e.g. two Aries in the same project) — both write wake records with the same `run_id` | L | L | The journal lock (`journal_lock_acquire`) serializes the arm; the second arm is refused with `pipeline-busy`. The wake record is per-`run_id`; the lock guarantees one writer. Tested by the existing pipeline-busy tests. | §FA2.1 (ADR-039) |
| **R-12** | A long-downtime pipeline's terminal event is journaled but the wake was abandoned — user is not woken even though the pipeline is terminal | M | L | The `abandon_after` is `expires_at + 600s` (default 1800 + 600 = 2400s for restart; 600 + 600 = 1200s for promote). The terminal event is journaled within this window in all known paths (the wake predicate at `WAKE_TERMINAL_EVENTS` = `_TERMINAL_EVENTS` + `restart` = `commit/rollback/halt/sweep_rollback/sweep/quarantine` + `restart` — architecture delta #1: the base `_TERMINAL_EVENTS` constant at `upgrade_journal.py:983` is the 6-member PROMOTE-only set and is never mutated). A pipeline that does NOT journal a terminal event for 40 minutes is itself a defect (R-SR13 in the parent register covers this); not a wake-specific risk. | §FA4.1 (ADR-042) |
| **R-13** | The `coalesce` body exceeds the `MessageQueue.message` size cap (a 16-entry coalesce with verbose run_id is plausibly >64KB) | L | L | The coalesce cap is 16; the body format is a self-describing pointer (run_id, terminal_outcome, armed_at per entry — ~80 chars each → 1280 chars for 16 entries, well under the 64KB cap). The cap exists for the `+24h GC` overflow journal event (text, no body-size concern), not for the coalesced wake. | §FA5.2 (ADR-043) |
| **R-14** | The wake is delivered to a non-chat source (e.g. a webhook or scheduler) — the response has no human to receive it | M | L | The recorded `source` is filtered to the user-origin classification set on the verified-arm path (FA1.1). For non-live arms, the recorded `source` may be non-user-origin (e.g. `scheduler:`); the response is delivered to that source, which is operator-forensic. The user can still query interactively. | §FA3.3 (ADR-041) |
| **R-15** | A wake for a paused instance creates a PENDING Task that is held until resume — the user does NOT receive the wake while the instance is paused | M | M | The wake is delivered (the `MessageQueue` row is created); the `Task` is held `PENDING` until resume. This is the same `PAUSED`-exempt behavior as every other wake in the system (existing claim-side pause gate at `instance_messaging.py:2154-2161, :1925-1934`). The user re-engages the instance (resume) and the wake is delivered. **Accepted** — pausing an instance is an explicit operator action; the wake deferral is consistent. | §FA3.1 (ADR-040) |
| **R-16** | The `MessageQueue.message_metadata` size grows with each wake — the JSONB column has no documented size cap | L | L | The metadata is `{"system_context": {"kind": ..., "run_id": ..., ...}, "delivery": {"channel": "post_restart_arm_notify"}}` — ~200 bytes per wake. Existing pattern: the WC watchdog wake carries similar metadata. No new sizing concern. | §FA3.2 (ADR-040) |
| **R-17** | The `PendingWake` dataclass is extended post-merge by a follow-up — an old binary reading a new journal drops the new field silently (`from_json` filter discipline) | L | L | The discipline (`upgrade_journal.py:738`) is documented in the existing code; the wake's `from_json` follows the same pattern. The old binary never had a wake path, so the field drop is moot. The risk is symmetric with the existing `PendingOp` discipline. | §FA1.1 (ADR-039) |
| **R-18** | A future change to `_TERMINAL_EVENTS` (e.g. adding a new event name) does not update the wake's terminal-state predicate | L | M | The wake's predicate reads the SIBLING constant `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` (architecture delta #1) — a new event name added to `_TERMINAL_EVENTS` propagates through the `+` concat automatically. `_TERMINAL_EVENTS` itself is NEVER mutated (PROMOTE-only reconcile at `:1016` depends on the 6-member set). T4.8 (mutation guard) pins both directions. Documented in the code; review at PR-time. | §FA4.1 (ADR-042) |
| **R-19** | The wake record is leaked across projects (e.g. an arming instance is project-A but the wake delivery fires for project-B) | L | M | The `arming_instance_id` is the wake's target; `enqueue_message` routes to the instance's project. The recorded `source` is the arm-time source; the response routes to that source. A cross-project leak is structurally impossible (the instance is project-scoped; the source is the same). | §FA3.3 (ADR-041) |
| **R-20** | A malformed journal (e.g. `pending_wakes` is a non-dict value) crashes the boot sweep | L | M | The `from_json` filter discipline (`upgrade_journal.py:738`) and the `list_pending_wakes` helper both tolerate malformed values — they return `[]` (the empty-list sentinel) on any non-dict. The sweep is best-effort, never raises. T1.2 / T5.9 assert the garbage tolerance. Phase 2 T17 extends the hardening: per-record `from_json` field-filter (mirroring `PendingOp.from_json` `:735-742`), `JournalTorn`/`OSError` catch (precedent `:1013`/`:1039`), `install_dir=None` → no-op sweep (precedent `UpgradeJournalSweepService.__init__ :99-103`). | §FA1.1 / §FA5.4 (ADR-039, ADR-044) |
| **R-21** | Launcher burst-abort gap (architecture delta #9): the launcher's plain exit-1 path (`launcher.sh:902-939`) journals NO terminal event → the wake never observes a terminal event and ABANDONS at grace (600s); the user receives NO notification of the burst-abort itself | M | M | Acknowledged (SHOULD delta #9 — acknowledged, not fixed in this feature): the wake abandons at grace and the record is removed with a `wake_abandoned` history event (operator forensics). The STAYS-DOWN story remains watchdog ADR-025(b) — COMPLEMENTARY, not superseded (the watcher covers daemon-down; burst-abort-with-daemon-up remains a blind spot shared with the pre-feature pull model). A future follow-up could journal a synthetic terminal event from the launcher's abort path — out of scope here (shell-side change, distinct blast radius). T5.16/T5.17 verify the abandon+no-stale behavior on the OFF path; the burst-abort path rides the same grace-abandonment machinery. | §FA4 (ADR-042 downtime blind spot) |

---

## Detailed Risk Narratives (the 5 highest-impact risks)

### R-3 — Wake re-stamp of user-origin window could enable a forged live arm

**Severity:** L × H = **Medium** (low likelihood, high impact).

**Source:** §FA3.3 (ADR-041).

**Concern:** the `stamp_user_origin_window` re-stamp is the
mechanism that allows a follow-up `upgrade_status` call within
the same wake turn to pass the 3-factor gate's factor-2. A
forged re-stamp (e.g. an attacker writing a fake window entry
into the manager's in-memory state) could allow a `system_upgrade`
on live without the user being in the loop.

**Mitigation (defense in depth, four layers):**

1. **Recorded source already passed the F2 surface.** The
   `pending_wakes.source` field is the arm-time user-origin
   source. On the verified-arm path, the 3-factor gate
   (`upgrade_tools.py:2494-2660`) is a precondition for the
   arm; the source ALREADY passed the gate. The re-stamp
   is just replaying a source that was already approved.
2. **In-memory state only.** The
   `_user_origin_windows[instance_id]` is a Python `dict`
   attribute on the `InstanceManager` singleton. An
   attacker would need code-execution on the daemon to
   write to it — at which point the security model is
   already broken (the attacker has the same privileges
   as the daemon).
3. **Source-format validation.** The
   `classify_user_origin` function
   (`upgrade_journal.py:1962-2012`) is the single source
   of truth for user-origin classification. The wake
   delivery code path does NOT re-classify; it reads
   the recorded value verbatim. A recorded value of
   `"agent:bypass"` would NOT have passed the arm-time
   F2; the wake never has this value.
4. **TTL bound.** The window has an `expires_at`
   (`now + NONCE_TTL_S = 60min`). The re-stamp is
   bound to the same TTL; a forged re-stamp is also
   bound.

**Residual:** the re-stamp is safe by construction. The
risk is documented for review-hygiene only.

### R-7 — Wake is delivered but the LLM does NOT call `upgrade_status`

**Severity:** M × M = **Medium**.

**Source:** §FA3.2 (ADR-040).

**Concern:** the wake body is a self-describing pointer
("call upgrade_status(run_id=...) and report back"). The
agent's LLM is expected to read the pointer and call the
tool. If the LLM does NOT call `upgrade_status` (e.g. the
agent's prompt is misconfigured, or the LLM is "lazy"
and just acknowledges the wake), the user receives a
"this is an auto-wake" message but no outcome.

**Mitigation:**

1. **Wake body is explicit.** The body says "call
   upgrade_status(run_id=...) and report back to the user."
   The instruction is in the message itself, not in
   prompt-level instructions.
2. **Prompt-level instruction (a follow-up).** The
   ari/jober prompts should be updated to recognize the
   `system_context.kind=post_restart_arm_notify` metadata
   and treat the wake as a high-priority instruction. This
   is a prompt-maintenance task, NOT this feature's
   code; flagged for the prompt-maintenance initiative.
3. **The user can re-query.** The pull model is the
   recovery (D-FA1.2 unchanged). The user, on their
   next message, can ask "did the upgrade complete?"
   and the agent can call `upgrade_status` interactively.

**Residual:** the wake may be informational-only if the
LLM is misbehaving. The user-inquiry path is the
recovery. Accepted per the feature spec ("the user can
ask if needed").

### R-8 — Live-outright-refusal bypass could write a wake record for a live arm

**Severity:** L × H = **Medium** (low likelihood, high impact).

**Source:** §FA5.5 (ADR-044).

**Concern:** the live-outright-refusal at
`upgrade_tools.py:2051-2058` returns BEFORE any journal
write. If this gate is bypassed (e.g. a future code
change that re-orders the gate), a wake record could
be written for a live arm, and a daemon restart could
wake the arming instance with the live source
recorded.

**Mitigation (structural coupling, three layers):**

1. **The wake is in the same `journal_write` envelope as
   the arm.** The `arm_pending_wake` call is in the
   same `journal_write` envelope as `write_pending_op`.
   The `pending_op` is the existing arm record; its
   write is gated on the live-outright-refusal. The
   wake inherits the gate by structural coupling.
2. **The kill-switch is a NO-OP for the live case.**
   The kill-switch disables the wake branch and the
   arm-side wake write. A live arm with the kill-switch
   ON still produces no wake. The kill-switch is a
   secondary defense, not a primary one.
3. **The test T5.12 asserts the live refusal writes
   no wake.** The test is a structural assertion
   (the journal is empty after a live arm attempt);
   a future regression would be caught by the test
   in CI.

**Residual:** the live path is structurally gated.
A regression is a release-blocker.

### R-10 — Wake fires during boot while worker pool is still spinning up

**Severity:** M × L = **Low**.

**Source:** §FA3.1 (ADR-040).

**Concern:** the boot pass fires the wake sweep
*before* the lifespan yield (api.py:1477-1523). The
worker pool is spun up earlier in the lifespan
(setup_worker_pool at api.py:439). If the wake
fires before the worker pool is ready, the wake's
`enqueue_message` call would either block or fail.

**Mitigation (boot order, verified):**

1. **Worker pool is up before the wake sweep.** The
   lifespan order in `__main__.py` is:
   `setup_worker_pool :439` → `JobProcessor.start :1371`
   → `start_sources :1416` → `UpgradeJournalSweepService
   :1477-1523`. The wake sweep is in the
   `UpgradeJournalSweepService` boot pass. The worker
   pool is up.
2. **T2.1 asserts the wake is enqueued.** The test
   mocks the `enqueue_message` call; the assertion
   is that the call is made (not held). If the worker
   pool were not ready, the call would be held in a
   PENDING Task and the test's `AsyncMock` would
   not be invoked within the boot pass window.

**Residual:** the boot order is verified. The
risk is documented for review-hygiene.

### R-12 — Long-downtime pipeline's terminal event misses the wake window

**Severity:** M × L = **Low**.

**Source:** §FA4.1 (ADR-042).

**Concern:** a pipeline runs for >`abandon_after` and
the wake is abandoned before the terminal event is
journaled. The user is not woken.

**Mitigation (multiple layers):**

1. **`abandon_after` is `expires_at + 600s`.** The
   existing arm expiry is 1800s for restart, 600s for
   promote. The grace is 600s. The window is 2400s
   for restart, 1200s for promote. A pipeline that
   does not journal a terminal event in 40 minutes
   is itself a defect (R-SR13 in the parent register).
2. **The wake is held `pending` until the terminal
   event.** The sweep's CAS acquires the journal lock;
   the wake is re-tried on every tick (90s) until
   either `delivered` or `abandoned`.
3. **The user can re-query.** The pull model is the
   recovery. The user can call `upgrade_status` and
   get the answer.

**Residual:** the wake is best-effort. Long-downtime
recovery is the pull model.

---

## Risk → Test-Case Cross-Reference (the verification matrix)

| Risk | Test case(s) that verify the mitigation |
|---|---|
| R-1 | T5.7, T5.8 (idempotency); T5.15 (terminal-revival) |
| R-2 | T5.3, T5.4, T5.5, T5.6 (coalesce); T1.6 (struct removal) |
| R-3 | T3.3 (re-stamp assert); the existing 3-factor-gate tests cover the arm-side path |
| R-4 | T1.3 (atomic write) |
| R-5 | T5.9, T5.10 (grace + abandonment); T1.6 (history event) |
| R-6 | T5.1, T5.2 (missing instance fall-back) |
| R-7 | T2.1 (wake body format); T3.1, T3.2 (routing) — the LLM-side guard is a follow-up prompt-maintenance task |
| R-8 | T5.12 (live refusal assertion) |
| R-9 | T1.5, T1.6, T5.7 (struct removal); the `+24h GC` follow-up is out of scope (GC bound: ~300 B/record, 10K ≈ 3 MB — delta #11) |
| R-10 | T2.1 (boot-pass wake delivery) — ✅ CLOSED (boot order confirmed post-dac38fd8) |
| R-11 | the existing pipeline-busy tests cover the lock; T1.3 (atomic) covers the write |
| R-12 | T5.9, T5.10 (terminal-state predicate); the `abandon_after` is documented in `decisions.md` ADR-042 |
| R-13 | T5.5, T5.6 (coalesce cap + body format) |
| R-14 | T3.4 (empty source fall-back) |
| R-15 | T5.14 (paused instance defer) |
| R-16 | T2.1 (metadata shape); the existing `enqueue_message` size-cap test covers the column |
| R-17 | T1.1, T1.2 (round-trip + garbage tolerance) |
| R-18 | T4.1, T4.2, T4.3 (terminal-state walker) + T4.8 (mutation guard, delta #1) |
| R-19 | T3.1, T3.2, T3.3 (routing is project-scoped) |
| R-20 | T1.2, T5.9, T5.10 (garbage tolerance + never-wedge) + T17 hardening (from_json filter / JournalTorn+OSError / install_dir=None) |
| R-21 | T5.16, T5.17 (abandon-on-switch-off + re-enable-no-stale ride the same grace-abandonment machinery; the burst-abort path itself is acknowledged, not test-pinned — shell-side) |

---

## Live-Outright-Refusal Invariant (the cardinal rule)

The single most important property of this feature is that
**a live-arming wake is structurally impossible by
construction**. The structural coupling is:

```
upgrade_tools.py system_restart :2051-2058:
  if self_env == "live":
      return "Error: RESTART REFUSED ... live-restart-refused: ..."
  # ↓ (no journal write before this return)
```

The wake is in the same `journal_write` envelope as
`pending_op`. The `pending_op` write is gated on the
refusal. The wake inherits the gate. **A live arm writes
no record. A live wake does not exist.**

This invariant is asserted by T5.12. A regression is a
release-blocker.
