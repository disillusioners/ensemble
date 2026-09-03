# Architecture Recommendation: Job-Task Fix-Chain Retrospective — Trajectory Verdict

**Date:** 2026-09-01 (retrospective window 2026-08-22 → 2026-09-01)
**Mode:** Standard Design — 4-worker evidence fan-out (git archaeology, prod frequency measurement, trajectory assessment, structural seam evaluation)
**Instances:** 7312bc9e (git), 80427faf (frequency), d89863a7 (trade-off), f60a19bf (structural)
**Scope:** READ-ONLY. No code, config, or DB mutations. Prod DB queried SELECT-only (`PGOPTIONS default_transaction_read_only=on`, verified per session).

---

## 1. VERDICT (headline)

**WORSE on trajectory — but not for the reason the user feared.**

The architecture is *more correct* than 3 weeks ago (two real root-cause fixes; both incident classes closed forward; post-fix boots show 0 kills / 0 skips), but it is *structurally heavier* and the debt compounds: each incident class mints a new sweep + kill-switch, and no heuristic ever retires. The measured "a lot today" is a **visibility explosion, not a failure explosion** — 6 of 8 rendered mismatch pairs are benign message-mirror completions that no earlier UI surfaced. Without intervention the trajectory keeps degrading; with one minimal intervention pair (§4) it flips to structurally BETTER.

| Axis | Trend | Justification (one line) |
|---|---|---|
| **Correctness** | ↑ BETTER | Mint repair at all 4 dispatch sites + 08-11 Step-4 reconcile are genuine root-cause fixes; incidents A (69a34b35) and B (80b86e51) classes are closed forward; 0 f1 kills/skips since the 09-01 12:37 boot. |
| **Invariant-strength** | ↑ BETTER (caveated) | `Task.work_id == JobItem.job_id` now contract-documented + tripwired at every known site — but still **convention-enforced**: auto-mint default alive (`instance_messaging.py:1593-1602`), tripwire WARN-only (`messaging_types.py:70-77`). |
| **Complexity** | ↓ WORSE | ~4,000 LOC across 5 commits; 8 named recovery patterns (a–e, f, f1, f2); 3 periodic sweeps + boot recovery + manual reaper; 6+ kill-switches (real surface spans config.py + constants.py + module-level env resolvers + 5 per-lane flags). |
| **Operability** | ↓ WORSE | 6+ uncoordinated transition authorities ("which sweep owns this row?"); all kill-switches restart-to-flip; WARNs carry no metric/alert; read model has dual authority producing both false-"completed" and false-"running" displays. |

**Net:** the *derivative* is negative — correctness gains are one-shot per class, complexity gains are compounding per incident. Trend WORSE until heuristics start retiring.

---

## 2. FREQUENCY ADJUDICATION (H1–H4, with per-day counts)

**Per-day measured (prod DB, jobs bucketed by created-day, +07):**

| day | terminal | completed | orphan-stamps | dead | rendered mismatch pairs |
|---|---|---|---|---|---|
| 08-22→08-30 (daily) | 31–107 | 43–101 | **flat 0–3/day** | 0 | ~0 (survivorship-caveated) |
| 08-31 | 45 | 43 | 1 | **1 (69a34b35 — f1 misfire)** | 1 |
| 09-01 | 45 | 44 | **0** | 0 | **8** (69a34b35 carried over + 7 of 09-01) |

All-time sweep stamps: 38 (since 08-03, flat 1–3/day). f1 kills ever: **1**. f2 finalizations ever: **2** (both 09-01: f97813ae 01:22, 80b86e51 20:03). Post-fix (12:37 boot): **0 kills, 0 skips**.

| Hypothesis | Verdict | Evidence |
|---|---|---|
| **H1** frequency amplifier | **REFUTED as explosion; mechanism real but single-fire** | No post-08-30 step in orphan stamps (flat 1–3/day; 0 on 09-01). Exactly 1 kill (69a34b35: 17 min active > 900 s grace, on the 4.5-month-old mint omission). Incident B's task ran **59 s** — its 7 h lag is bus_pending gate latency, *not* grace-window crossing, so H1's "only long missions cross the window" story covers A but not B. Guard-lingering concern rejected: bounded (300 s re-eval; exits when subtree quiets); residual is a different, low-frequency class (false-positive DEAD on a quiet healthy subtree). |
| **H2** visibility illusion | **STRONGLY SUPPORTED** (survivorship-caveated) | Rendered pairs 0→1→8; 6/8 are benign completed message-mirrors against long-lived missions (28c6421b since 08-31, 809e2a59 today); first SSE work-view use 19:11:38 today; 52-min live window where derived view said `completed` while raw admission was `active`. **Label correction:** e863f010 and 5e16f791 are compact-on-completed and instant-chat-display merges — neither touches derived status (work_status.py last touched 08-06). H2's true mechanism = pre-existing read path (work_resolver consults instance liveness only for active rows) × first-at-scale rendering × long-lived missions. |
| **H3** residual blind spots | **CONFIRMED, SMALL** | 3 zombie ACTIVE message jobs (08-01, 08-14×2) with dead/gone instances — outside the task-orphan sweep by explicit skip (`job_recovery_service.py:2284`); blind active→queued resets: 7 events, all pre-08-30, 0 since; paused-orphan residual unmeasurable (0 paused instances exist). |
| **H4** WC-wake pairing | **REFUTED** | `ENSEMBLE_WC_WAKE_ENQUEUE` unset at every prod boot; zero runtime wake-pairing lines; 08-31 events predate any WC-wake runtime activity (all 689 string hits are dev tool-call content). |

---

## 3. FIX-CHAIN SCORECARD

| Change | Root-cause vs patch | Invariant vs heuristic | Caused the next bug? |
|---|---|---|---|
| Pattern-f sweep default-ON (44d5b4cf+c6c9dfac, 08-30) | Symptom patch | Heuristic | **YES — Incident A.** Sweep's `task is None` predicate assumed the linkage invariant that the 4.5-month-old mint omission (call site born 134bd782, 2026-04-19; `work_id` param added 06-27 but never threaded; 07-02 fix covered 1 of 4 sites) had silently broken. First-cycle kill 08-31 11:38. **"A fix caused the new bug" — CONFIRMED for A.** |
| Observability pair (5e16f791, e863f010, 08-31) | n/a (feature merges, mislabeled as observability) | — | No. But the era's SSE work-view rendered pre-existing semantics as alarming (H2). **Incident B was NOT caused by the chain** — no-inline-mirror-transition dates to the 07-03 message-jobs POC; f2 merely resolved it 7 h late. |
| f1 mint repair, 3 sites (04fd0c52, e6cd5fc8, 09-01) | **Root-cause fix** | Invariant (asserts linkage at all 4 dispatch sites) | Closes A's class forward. Gap: auto-mint default remains for future callers (M5). |
| f1 tripwire (messaging_types.py:42-77) | Symptom (detection) | Heuristic (WARN-only, never blocks) | No new failure mode; no enforcement. |
| f1 subtree-alive guard (:2601-2744) | Symptom (shields live subtrees from the sweep) | Heuristic (tree MAX-activity proxy, 900 s) | No lingering-ACTIVE mode (bounded). New low-freq residual: false-positive DEAD when a healthy subtree quiets. |
| f1 kill-switch (ENSEMBLE_ORPHAN_F1_ENABLED) | Operational mitigation | Config (default ON, restart-to-flip) | n/a |
| f2 backstop (:3033-3240, gate :2294-2336) | Symptom backstop | Heuristic | Structurally fragile for mirrors: bus-drain fires no event → gate never opens → hours-late DONE (Incident B). |
| Step-4 reconcile_terminal_task (114d1cc5, 08-11 — pre-window) | **Root-cause fix** (inverse direction) | Invariant (EXISTS-guarded UPDATE) | n/a — landed clean, different class. |

**User's core claim, adjudicated:** the chain *did* cause exactly one new bug (A: Pattern-f × latent mint omission — direct, documented in the 04fd0c52 commit message). It did *not* cause B; it *revealed* B and rendered it alarming.

---

## 4. THE SINGLE HIGHEST-LEVERAGE STRUCTURAL CHANGE

**Challenge to the leader's candidate** ("Job↔Instance terminal-state reconciliation seam + inline mirror-job transition"): the reconciliation seam **partially exists in both directions** — JobItem-terminal→Task (`reconcile_terminal_task`, job_feedback_observer.py:3615-3642) and Instance-terminal→JobItem (conditional-UPDATE cascade, instance_lifecycle.py:3978-3993, **no job_type filter** — W1's mirror-exclusion reading corrected by W4: the `job_type <> 'message'` filter at :4863 is the W1-resume drift-guard, not the cascade; zombies escape via crash/no-cascade paths + the f-sweep skip at :2284). "Add the seam" would duplicate existing writes. What's actually missing: **(1)** a write at the true event for mirrors, **(2)** a single authority for terminal transitions (6+ surfaces race today), **(3)** a read model that cross-checks terminal rows.

**Recommendation — the minimal pair `A → B → C` (D deferred):**

| Step | Change | Cost | Retires |
|---|---|---|---|
| **A** (same-PR-first) | Fail-closed linkage: require explicit `work_id` on job-driven dispatch paths; demote the auto-mint fallback (`instance_messaging.py:1593-1602`) to error; keep WARN tripwire as backstop | ~1 day | Nothing — freezes sweep-family regrowth at the root |
| **B** (core) | Inline idempotent mirror transition in task_processor on_success (`UPDATE … WHERE admission_state IN ('queued','active')`, rowcount=0 no-op — proven shape from instance_lifecycle.py:3978-3993) **+** replace the f-sweep's blanket message-skip (:2284) with an instance-liveness predicate to reap the 3 zombies | 2–4 days | **f2's mirror slice + the zombie class** |
| **C** (paired, never alone) | Read-model truth: derived status consults instance liveness for terminal rows; render mission rows vs mirror rows as distinct kinds; keep raw `admission_state` always visible as secondary | 1–2 days | The alarm churn (the thing that actually exploded) |
| **D** (deferred, not rejected) | Ground-truth linkage column + unified idempotent reconciler absorbing f1/f2/reaper | High | The sweep family — but lands on the most incident-dense surface while the family is starving (flat 1–3/day, 0 today); revisit with new frequency data after B+C |

**Structural diagnosis (W4):** message-mirror jobs are **read-model projections misimplemented as stateful rows** — created eagerly, updated by nobody at event time, then "reconciled" by polling predicates that guess. B fixes the projection write; C makes the read side authoritative; the sweep family is the compensator for the missing projection discipline. 🔴 **C alone is dangerous** — it masks the 7 h-lag and zombie signals instead of fixing them; hence the pair. Ordering: A before B (else a forgotten `work_id` yields a phantom handle and B's UPDATE silently no-ops); B no later than C.

---

## 5. CONFIDENCE + GAPS

**Confidence: HIGH** on — Incident A causal chain (commit-documented, code-verified, log-timestamped); H2 dominance (rendered-pair counts + first-use timestamp + 6/8 benign composition); H4 refuted (env unset every boot); seam map + scorecard classifications (4 workers independently converged, cross-verified at file:line).

**Confidence: MEDIUM** on — H2's "rate unchanged before 08-31" (survivorship: instances store current status only, historical pairs unreconstructable; supported indirectly by flat finalization counts); H3 sizing (task-table retention purge — 191 rows vs ids ≥27547 — hides old orphans).

**Gaps (measurement ceilings, not analysis gaps):**
1. `job_queue_items` has **no terminal-transition timestamp** (no `updated_at`; `failed_at` on 47/6134) — per-day *transition* dates unrecoverable; per-day table buckets by created-day.
2. Task retention purge makes "terminal job with no task row" ≈ artifact for old rows; mint-omission identifiable only where stamped/logged.
3. No intermediate log evidence for the 7 h f2 window (13:01→20:03) — mechanism inferred from gate code, not observed.
4. `transition_terminal_if_open`-equivalent repo method existence unverified (SQL shape verified, reuse not); FE rendering path for mission/mirror split unread; exact f2 cycle (60 s comment vs 300 s service) unresolved — none load-bearing for the ranking.

**Corrections to the project record (for the notes-keeper):**
- The critical note "no terminal-state reconciliation Job↔Instance in either direction" is **inaccurate** — both write directions exist; the real gaps are the mirror carve-outs (no inline transition; f-sweep skip; terminal rows ignore instance liveness in the read model).
- 5e16f791/e863f010 labels ("echo/jobs_streaming", "work view/derived status") are wrong — instant-chat-display and compact-on-completed respectively.

## Appendix: Evidence Index

- Mint omission: call site 134bd782 (2026-04-19); `work_id` param d058314f (06-27); partial fix 645656e2 (07-02, 1 of 4 sites); auto-mint 7d42f6b5 (07-03, `instance_messaging.py:1593-1602`); full repair 04fd0c52+e6cd5fc8 (09-01).
- Kill A: log 08-31 11:38:18 `Pattern (f1) finalized orphan ACTIVE JobItem 69a34b35 … to DEAD`; mission 28c6421b alive (waiting_children, 36 h+).
- Lag B: task 27547 completed 13:01:35 (59 s); derived view "completed" 19:11:38; admission `active→done` 20:03:26 (`Pattern (f2)`).
- Boots: 08-30 16:50 (Pattern-f era), 09-01 12:37:49 (+`Pattern-f1 … ENABLED` banner 12:37:50).
- Sweep census: f1 `job_recovery_service.py:2774/:2995`; f2 `:2387/:3033`, gate `:2294-2336`; message-skip `:2284`; watchdog `instance/repository.py:2270-2295/:2383-2388`, `waiting_children_watchdog.py`; 5-lane recovery `report_delivery_recovery.py:255-267`; reaper `job_queue_service.py:1155-1262`; observer 5 s deferred `job_feedback_observer.py:1034-1041`.
- Full worker reports: message transcripts of instances 7312bc9e / 80427faf / d89863a7 / f60a19bf.
