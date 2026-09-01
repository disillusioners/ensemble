# Plan Overview: Slash-Command Subsystem + On-Demand Compaction (`/compact`)

Date: 2026-08-31 (architect-ratified same day; revised 2026-08-31 — post-review adjudication (C1 + O8–O13) folded into phase files)
Branch: `feature/slash-commands` @ `5e16f791` (cut from latest)
Status: **FINAL — architect-ratified + post-review adjudication folded (C1 + O8–O13, 2026-08-31), implementation-ready.** All 13 open decisions (O-B1…O-B13) and 9 tech questions (Q1–Q9) resolved in `architecture-recommendation.md` §8; post-review adjudication in §"Post-Review Adjudication (C1 + O8–O13)"; baseline RATIFIED with corrections folded into all phase files. Remaining openness is limited to tracked verification tasks (V-1, V-2, � items), not decisions.
Authorship: synthesized by the planner dispatcher from worker artifacts; primary content lives in the companion files cited below. Post-architect revisions applied 2026-08-31 per `architecture-recommendation.md` §9 (13 items); post-review adjudication revisions applied 2026-08-31 per §"Post-Review Adjudication (C1 + O8–O13)".

| Artifact | Owner | Content |
|---|---|---|
| `architecture-recommendation.md` | Architect (4 skill workers) | Verdict register (O-B1…O-B13, Q1–Q9), §7 normative wire schema (post-review §7 amendment: `compacted_type` enum + per-value FE copy table), §9 plan-change list, §10 risks, §"Post-Review Adjudication (C1 + O8–O13)" — **authoritative decision source** |
| `research-findings.md` | backend worker | Evidence base: compaction internals, API/lifecycle/SSE map, FE digest, config knobs, KB corrections |
| `phase1-plan.md` | backend worker (`plan-creation`) | Backend plan WS-1…WS-8, **Final/architect-ratified**: S-1…S-18 success criteria, V-1/V-2 verification exit criteria, R-1…R-17 risks |
| `phase2-plan.md` | frontend worker (`plan-creation`) | Angular 21 FE plan, tasks 1–10, **contract PINNED to §7**: SC1–SC15, R1–R9 |
| `decisions.md` | backend worker | D-B1…D-B12 (three REVISED per architect) + "Decided by architect" 13-row register + open verification tasks |
| `technical-analysis.md` | tech worker (`technical-analysis`) | Q1–Q9 options maps that fed the architect (superseded by §8 verdicts where they differ) |

---

## Objective

A user types `/compact` in the frontend chat box for the **selected instance** and that instance's message history is compacted **on demand** — through the **existing** `ContextCompactor` engine (extended via an additive, threshold-only `force` flag; no parallel path), behind a **new extensible slash-command subsystem** (first command `/compact`; future command = one `CommandSpec` registration, zero router edits). The compaction path gains an **adaptive LLM timeout** (base 90s + ~60s per 100k prompt tokens, per-call cap 300s) improving **all three** compaction paths, plus two engine-gap fixes the architect identified (partial-summary preservation on mid-chunk timeout; per-prompt token estimation). On timeout/failure the existing in-engine trim fallback applies **with a visible marker line and honest outcome reporting**. The FE walks the user through **waiting → in_progress → success / timed_out→fallback_applied / noop / rejected** states via the architect-pinned SSE contract.

## Architecture Baseline (architect-ratified — corrections folded in)

1. **BE-side intercept RATIFIED (Q1, 4.35-weighted)**: router check in `daemon/routers/messages.py` between validation end (:240) and status capture (:243) + service-layer dispatcher in new `daemon/services/command_dispatcher.py` mirroring the proven `daemon/sources/registry.py:47-159` pattern. Non-command traffic byte-identical (regression-pinned). Flip condition recorded (byte-identity compliance → hybrid).
2. **Async execution (Q3/O-B2)**: POST acks ≤500ms (`CommandAck` with `command_id`, `state: accepted|rejected`, `ttl_seconds` 600); progress via `command_progress` SSE (10s heartbeat, `phase_seq` monotonic, server-clock `elapsed_ms`, advisory `eta_ms`); `GET /api/instances/{id}/commands/active` fallback with `{exists:false}` restart semantics. **Wire contract is ARCHITECT-PINNED (§7, verbatim in phase1 WS-5 + phase2 P1 deps)** — 400 for parse errors (`UNKNOWN_COMMAND`), 200 + `state:"rejected"` + reason enum (`terminal_instance | busy | rate_limited | pending_injections | compaction_disabled | quiescence_timeout`) for semantic refusals.
3. **Reuse with NARROWED force (Q6/O-B5)**: `force=True` on `compact_state` bypasses the **threshold check only** (:659-664); D9 dedup and min-messages stay in-engine untouched. Executor pre-checks (no engine call): `compacted_at` recency <60s → `success+noop+recently_compacted`; <5% window → `success+noop+below_floor` (knob `SLASH_COMMANDS_NOOP_FLOOR_RATIO`). `CompactionResult` gains additive `forced`/`failure_kind` (anti-drift test: `forced=False` on both auto paths). Shared `_is_terminal_checkpoint` helper extracted for the proactive site + executor.
4. **Adaptive timeout WITH two architect corrections (Q7)**: per-call cap `min(300, 90 + tokens/100k·60)` at `compaction.py:1038`; **token estimate = the prompt being sent, computed at each of the three call origins (:900/:939/:971)** — NOT `context.messages` (over-estimates chunk 2+ and merge/condense); facade `wall_clock_cap_s` = per-call cap + 5s at :1011; whole-op budget `COMPACTION_OPERATION_BUDGET_S=300` **between LLM calls only** (never between the two `aupdate_state` calls — cancellation discipline binding). **New engine fix (all paths benefit): per-chunk `try/except` in the :838-840 loop preserves completed chunk summaries on mid-run timeout** (today all are discarded at :753-772).
5. **Fallback DECIDED (Q5)**: plain `_truncate_fallback` + `failure_kind` outcome mapping + ONE id-deterministic marker line (`truncation-marker-{uuid4()}`) **inside** the existing function. LLM summary-line variant rejected (re-triggers the failure being escaped). `failure_kind="timeout"` → `timed_out→fallback_applied`; `"error"` → `failed`.
6. **Concurrency matrix (Q2/Q4, corrected)**: ExecutionGate MANDATORY for every execution; **rate-limit check ordered BEFORE gate acquisition** (1 in-flight per instance + 10s min-interval, the only abuse guard). IDLE → quiescence probe + **re-check `has_instance_busy` under gate (retry-once)**; RUNNING → emit `waiting` SSE FIRST (F3) → pause-first → quiesce ≤30s → gate → compact → resume, with **quiesce failure → `rejected+quiescence_timeout`**; PAUSED-with-or-without frozen task → direct gate → compact; instance deleted mid-command → terminal `failed`. **Terminal instances: REJECT (O-B4 DECIDED)** — `reason=terminal_instance` + guidance "Send a message to start a new turn, then /compact."; revive-then-compact is unsafe (code-verified brick chain); revive-brick behavior gets a regression test.
7. **JAFP-compliant (O-B7)**: commands ephemeral (no JobItem); `command_id` + `handler` seam keeps a future durable wrap open. Restart loses the registry by design → `{exists:false}` → FE clears silently.
8. **Extensibility contract**: `CommandSpec` (name, description, `availability` predicate hook for future per-agent policy, `rate_limit_per_instance`, handler); load-bearing dispatcher ordering `//`-escape (O-B1 ratified) → parse → lookup → availability → rate-limit → ack → background task.

## Phases

| Phase | Objective | Plan file | Key contents (post-architect) |
|---|---|---|---|
| **1 — Backend** | Command subsystem + on-demand `/compact` + adaptive timeout (general) + engine fixes + fallback + pinned contract | `phase1-plan.md` | WS-1 registry/dispatcher + ordering test; WS-2 executor (threshold-only force, pre-checks, `_is_terminal_checkpoint`, revive-brick regression test); WS-3 adaptive timeout (per-prompt, facade +5s, whole-op budget, per-chunk preservation + regression); WS-4 fallback marker + failure_kind; WS-5 **§7 schema verbatim, ARCHITECT-PINNED**; WS-6 concurrency matrix + interaction rulings + **V-1/V-2 exit criteria**; WS-7 config (`COMPACTION_*` + `SLASH_COMMANDS_*`, O-B12 two-PR mirror plan); WS-8 tests (S-1…S-18) |
| **2 — Frontend** | Angular 21 UX on the pinned contract | `phase2-plan.md` | Tasks 1–10: registry+types incl. `//` escape; `parseCommandAck` = **executable contract spec**; SSE listener forwarding `phase_seq`; state machine (monotonic guard, ack-seeded waiting, noop mapping); rejection UX incl. verbatim terminal guidance; card with **server-`elapsed_ms`** timer, `eta_ms` advisory, heartbeat no-reset, noop/truncation-honest copy; terminal-event-triggered refetch; `{exists:false}` silent clear + 5s poll-while-SSE-dead; Jest/Playwright incl. 4 new e2e scenarios; stretch autocomplete |

**Dependency direction:** Phase 2 pins to the ARCHITECT-PINNED §7 contract (formerly P1-1…P1-6 assumptions — now pinned, enforced by the FE adapter contract-spec tests). Phase 1 is independently buildable; FE can develop against the written schema with mocks in parallel.

## Decision Status (was "Open Architecture Questions" — ALL RESOLVED)

| Question | Verdict (architect §8) |
|---|---|
| FE vs BE intercept | **BE-side ratified** (Approach A, 4.35-weighted; Maintainability dominant) |
| Per-instance vs global availability | Global now + `availability` predicate hook; terminal = **REJECT** (O-B4, code-verified) |
| Trim-fallback consistency | **Plain `_truncate_fallback` + marker line + failure_kind**; summary-line rejected (Q5) |
| Sync vs async | Pure async (O-B2) |
| Timeout scope | Per-prompt input, facade +5s, whole-op budget; same formula all three paths (Q7 + corrections) |
| `'/'`-escape | `//` passthrough, checked before `/` (O-B1) |
| Durability | Ephemeral + structural seam (O-B7) |
| Unknown command / refusals | 400 `UNKNOWN_COMMAND` / 200 `state:"rejected"` + reason enum (O-B9/O-B10 + §7 split) |
| Rate limiting | 1 in-flight + 10s min-interval, before gate (O-B13) |
| Pending injections | Reject with reason (O-B11) |
| constants.py mirror | Update in-PR + separate tidy PR to delete (O-B12) |
| Subsystem shape | Service dispatcher + `CommandSpec`, sources/registry.py pattern (Q8) |
| Progress granularity | 6 phases + 10s heartbeat + phase_seq/elapsed_ms/eta_ms/ttl (Q9) |

Full register: `architecture-recommendation.md` §8; per-file roll-up: `decisions.md` ("Decided by architect" + "Post-review adjudication" sections). **No open decisions remain.** Tracked verification tasks (not decisions): **V-1** gate release on pause-cancel + resume-path gate coverage (🟡, WS-6 exit criterion); **V-2** tenacity facade at ~305s wall clock (🟡, WS-6 exit criterion); 🟢 noop-floor 5% tuning, SSE keepalive-on-idle (FE V-1 in phase2), GET-fallback auth mirroring, rate-limit rapid-click integration test.

## Risks (severity-ordered, post-architect — full registers in phase files)

1. 🔴 **Checkpoint write race vs astream commit** — ExecutionGate is the ONLY defense; any future ungated checkpoint write breaks both compaction paths. Mitigation: gate-layer code comment + V-1 verification.
2. 🔴 **Terminal-instance mutation** — resolved by rejection; the brick mode (aupdate on `next=()`) now gets a **regression test pinning its load-bearing status**.
3. 🟡 **Mid-chunk partial-summary loss** — engine gap; fixed by the new per-chunk try/except (WS-3.4) with regression test.
4. 🟡 **Resume-path gate coverage** + **facade at ~305s** — V-1/V-2 verification tasks gate WS-6 completion.
5. Contract drift between phases — eliminated: schema is architect-pinned and FE adapter tests are the executable contract spec.
6. SSE live-only/no-replay — GET `/commands/active` + 5s poll-while-dead + `{exists:false}` silent clear.
7. 5-min perceived freeze — server-`elapsed_ms` timer, 10s heartbeat, `eta_ms` advisory, non-blocking card.
8. Silent-write/edit failures in this repo — grep-marker + read-back verification mandated for all edits (applied throughout these plan files).

## Research Insights (evidence in `research-findings.md`; two architect-added engine findings)

- No on-demand compaction entry existed — two automatic call sites; `/compact` is a third caller of the same engine.
- The adaptive-timeout seam is clean, but the **token input had to be corrected to per-prompt** — `context.messages` over-estimates every call after the first chunk (architect Correction 1).
- **Engine gap found by architect**: mid-run timeout discards ALL completed chunk summaries (compaction.py:753-772) — fixed for all three paths by per-chunk try/except (Correction 2).
- KB corrections recorded (proactive path pre-invocation; 30s per-call cap already existed; ID-rename emergency-only). FE is Angular 21 (brief said React).
- Trim fallback already existed in-engine — the plan adds only reporting + one marker line.
- The terminal-checkpoint guard is load-bearing (O-B4 chain code-verified) and **the regression test that pins it is task 2.5 in this plan** (planned, not yet implemented).

## Test Strategy (summary — details in phase files)

- **Backend unit/integration**: threshold-only force + `forced=False` anti-drift; per-origin timeout table tests; partial-summary preservation; revive-brick; noop pre-checks; heartbeat/phase_seq monotonicity; `{exists:false}`; byte-identity marker test; dispatcher-ordering test; rapid-click race. Patch `daemon.graph` for lazy imports; file-backed SQLite.
- **Frontend**: Jest logic-mirror (state machine, adapter-as-contract-spec); Playwright `slash-command-compact.spec.ts` + 4 new scenarios (rejection guidance, restart silent-clear, noop, heartbeat/poll); `ng build` strictTemplates gate; EventSource-mock spike (half-day budget flagged).

## Success Criteria

Phase 1: S-1…S-18 + V-1/V-2 evidence (phase1-plan.md). Phase 2: SC1–SC15 (phase2-plan.md). Feature-level acceptance: the completion sentence in phase1-plan.md §Objective (noop paths included).

## Next Step

**Ready for implementation** — hand to the developer. Sequence: WS-1/WS-2/WS-3 first (subsystem + executor + engine fixes are independently testable), WS-5 schema is frozen (changes require architect sign-off), Phase 2 can start against the pinned contract with mocks in parallel. V-1/V-2 land inside WS-6, not after.
