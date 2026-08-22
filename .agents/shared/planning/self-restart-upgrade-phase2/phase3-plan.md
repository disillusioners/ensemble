# Phase 3 (P2.3): Rollout Ladder, Drills & Carry-Overs

> **⛔ HARD CONSTRAINT (user directive — VERBATIM, applies throughout this phase):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**Basis:** P2.1 pipeline + P2.2 tools shipped on demo/sandbox. This phase turns them into a *governed rollout*: evidence-gated ladder, executable runbook, abort alerting, and formal closure of the 3 Phase-1 carry-overs.

---

## Objective

Establish the demo-first rollout ladder with an N-clean-cycles gate, author the **drill runbook (new doc — does not exist today)**, wire alerting for abort/rollback-cap-halt events, close the three Phase-1 carry-overs as formal drills with recorded verdicts, and design — never execute — the USER-GATED live promotion.

**Exit in one sentence:** the runbook exists and every drill in it has been executed on demo/sandbox with recorded PASS verdicts + evidence files, the N-cycle ledger shows the gate measurement design with the current count, and the live-promotion design is documented as explicit user-confirmed actions that this initiative did not perform.

---

## Verified Starting Point (do not re-derive)

**The 3 carry-overs, VERBATIM** (`.agents/tester/RESULTS/2026-08-22-ar-phase1-followups-verification.md` lines 45-47):

1. "Launcher retry loop not observed end-to-end live: the exit-75 smoke proves the daemon exits 75 and the launcher *message* says it will retry; the full real loop (exit 75 → supervisor actually respawns with capped backoff → recovery) remains unit-level (launcher suite) + sandboxed. Phase 2 (auto-restart hardening) should include one live tempfail→recovery cycle on demo."
2. "Exit-78 (config error) path remains unit-covered only (63/63) — consistent with pre-merge 'optional' framing; fold into Phase 2 live smoke if cheap."
3. "P7 drill on deployed daemon requires restart to restore (env knob read per refresh tick; `readiness.py:50-67`) — document in Phase 2 drill runbook so green-restore steps aren't assumed instant."

**Supporting facts:**
- P7 = readiness drill green→red→green via `ENSEMBLE_READINESS_FORCE_DEGRADED` — one-way fail-safe; the knob is read per refresh tick, so clearing it on a *deployed* daemon requires a restart to restore green (`daemon/services/readiness.py:48-67`; verified on demo: restore-via-restart matches the contract).
- Exit-78 sandboxing conventions (Phase-1 precedent): own port (e.g. 8377 precedent), throwaway data dir, throwaway PG (like `ensemble_test`), `conftest` `SYSTEM_DEFAULT_PROJECT_ID` monkeypatch; captured exit code + post-conditions; live pids verified unchanged at checkpoints.
- Launcher tempfail semantics: exit 75 → capped 5s→60s backoff, burst-budget-exempt; burst budget 5 crashes/600s; uptime ≥600s resets (launcher.sh:187-247). The plist (`scripts/ensemble-prod.plist`, KeepAlive, ThrottleInterval 10s) + `scripts/ensemble-watchdog-watcher.plist` + `scripts/watchdog-watcher.sh` (watches `/livez` only, 300s interval, notifies absent >600s) are shipped artifacts — the alerting precedent.
- Alerting surface: SSE `NotificationBroadcaster` (existing daemon service) — ADR-008's Phase-6 LLM observer is NOT in scope; only deterministic notifications are.
- E2E rule: `.agents/tester/rules/ensure.md:44-53` (mandatory full e2e if job/task/queue code touched).
- Tester RESULTS convention: `.agents/tester/RESULTS/YYYY-MM-DD-<slug>.md` with verdict table, evidence audit, independent runs, constraint-compliance section (Phase-1 file is the shape to follow).

---

## Design Decisions (this phase)

**D1 — N = 3 clean demo cycles (per ADR-021 in sibling `decisions.md` — the final ruling on N; ⚠ user-gated, default-if-silent 3; rationale there: each cycle exercises ≥2 periodic recovery ticks, symmetry with rollback cap 3/24h; staleness — any release/manifest change mid-count resets to 0).** **The clean-cycle definition is CANONICAL in `test-strategy.md` §4.1 (approver-ruled 2026-08-22 — c5/c6 below folded there as clauses 4-5); the table here is the per-criterion evidence mapping subordinate to §4.1.** A "clean cycle" is **objectively defined** — a single promote-to-staged-version run on demo satisfying ALL of:

| # | Clean-cycle criterion | Proof artifact |
|---|---|---|
| c1 | Promote completes end-to-end: preflight → stop → flip → gate → commit | journal `history` terminal `commit` event |
| c2 | Gate green within budgets: `/livez` ≤60s, `/readyz` ≤120s, version verify pass, 300s soak clean | promote transcript (probe outputs + timestamps) |
| c3 | No auto-rollback, no sweep, no halt in the cycle | journal: zero `rollback`/`sweep_rollback`/`halt` events in window |
| c4 | Post-cycle system healthy: `/readyz` 200 with `reasons: []` after knob-free restart-less settle | `curl :7979/readyz` transcript |
| c5 | No unintended work loss: any in-flight job at stop resumed and completed | job id list before stop ↔ terminal states after (checkpoint resume evidence) |
| c6 | Zero live contact | live pid checkpoint (byte-identical pids at start/end, Phase-1 §5 precedent) |
| c7 | Restart cycle clean (§4.1 clause 2): ari-driven or drill restart → respawn → gates green → no degradation attributable to the restart | restart transcript + probe outputs |

**Clause↔criterion mapping to canonical `test-strategy.md` §4.1 (approver-aligned 2026-08-22):** §4.1 clause 1 (ari-driven upgrade cycle) = c1+c2+c3 below; **clause 2 (restart cycle clean) = c7**; §4.1 clause 3 (no readiness degradation outside drills) = c4; clause 4 (no unintended work loss) = c5; clause 5 (zero live contact) = c6. A cycle is clean iff all five §4.1 clauses pass — the c-rows are the evidence decomposition, not a second definition.

**Why 3 (aligned to ADR-021 default — body corrected from an earlier 5; architect edit 2026-08-22):** large enough to catch flaky-gate and retention/eviction edge interactions (P2.1 T8 only proves eviction logic in sandbox; repeated demo cycles exercise it against real journal accumulation), small enough to fit demo cadence (each cycle ≈ 10–15 min incl. soak). Cycles need not be consecutive versions — upgrade-to-same-version cycles count (the pipeline is the object under test, not the payload). **Ledger:** `.agents/tester/RESULTS/` verification files per cycle + a cumulative ledger table inside the runbook; the journal's own `history` is the primary evidence (tamper-evident via commit chain of events), RESULTS files are the human-auditable copy. **Gate:** promotion out of the demo rung (i.e., any USER-GATED live step becoming eligible) requires the ledger showing 3 consecutive clean cycles with zero criterion violations.

**D2 — Drill runbook is a NEW doc, versioned in-repo.** Location: `docs/runbooks/upgrade-drills.md` (or `.agents/devops/RUNBOOKS/` if the devops convention prefers — implementer picks per existing tree; the runbook itself lists its canonical path). Contents (minimum): prerequisites, per-drill procedure (commands, expected outputs, pass criteria, evidence to capture), rollback-of-the-drill (how to restore demo to a known-good state), the P7 restart-to-restore note (verbatim, see T2), sandbox conventions (ports, throwaway PG, monkeypatch), and the live-contact prohibition restated. Every drill has a pass criterion nameable in one line.

**D3 — USER-GATED live promotion = designed as an explicit user-confirmed action sequence, never executed here.** The design (documented in the runbook + `promotion-ladder.md`, W3) enumerates: (1) user verifies the 3-cycle ledger (N=3 per ADR-021); (2) user runs the live guard-bearing commands themselves (`ENSEMBLE_UPGRADE_LIVE=1 ...` for the initial staged-mode migration, then promote); (3) each step's expected journal events + verification probes; (4) the abort path (manual `rollback.sh`, halt-for-human conditions). **This initiative's deliverable stops at the documentation; the ladder's live rung is executable only by the user.**

**D4 — Alerting on abort/halt: deterministic SSE notifications in-daemon; watchdog-watcher journal-watch extension for daemon-down [RESOLVED 2026-08-22 — ADR-025(b), per architecture-recommendation.md D-FA1.2/S-10: the offline notifier IS the watchdog-watcher extension, now a HARD dependency of the failure-path story, not optional polish].** Three events fire a `NotificationBroadcaster` SSE notification (+ journal `history` entry, which is the durable record): (a) launcher burst-abort (stay-down), (b) rollback-cap halt-for-human, (c) any auto-rollback + quarantine. Delivery when daemon is down = **the watchdog-watcher extension watches `.launcher-state` abort markers + `releases/state.json` halt/sweep journal events** (files readable without the daemon — file-format stability is pre-freeze assumption #3, `decisions.md` checklist; the watcher reads `releases/state.json` which does not exist until P2.1 T4 creates it via the pipeline — hard dependency, T8). Anything pointed at live = USER-GATED (ladder U6).

---

## Carry-Over Closure Matrix (the 3 verbatim items → concrete drill steps)

| Carry-over (verbatim ref) | Drill | Procedure (summary) | Pass criteria | Closure evidence |
|---|---|---|---|---|
| **#1** "one live tempfail→recovery cycle on demo" (verification.md:45) | **DR-1: tempfail→respawn full cycle on demo** | On demo: point the daemon's PG at an unreachable endpoint (sandbox-style: restart demo daemon with throwaway-bad `DATABASE_URL` in a *drill-scoped* env override) → observe exit 75 → launcher respawns with capped backoff (5s→60s, budget-exempt) → restore PG → recovery boot → green | Captured: ≥2 exit-75 cycles with launcher backoff logs (timestamps + backoff values within cap), zero burst-budget decrement (`crash_count` unchanged in `.launcher-state`), final `/livez` 200 + `/readyz` 200, `.launcher-state` shows no abort | Runbook DR-1 section + RESULTS file with transcript; verdict line "DR-1 PASS: full loop observed end-to-end" |
| **#2** "fold exit-78 into Phase 2 live smoke if cheap" (verification.md:46) | **DR-2: exit-78 sandboxed smoke** | Sandbox (own port e.g. 8377 + throwaway PG + throwaway data dir — Phase-1 exit-75 smoke precedent): boot with fatal-config (missing binary / schema-refuse class) → capture exit 78 → assert launcher does NOT loop (no respawn within observation window) | Captured exit code 78; launcher log shows refuse-no-loop handling; zero respawns in N seconds; live pids unchanged | Runbook DR-2 section + RESULTS file with captured exit code + post-conditions |
| **#3** "P7 knob-clear-requires-restart … document in Phase 2 drill runbook" (verification.md:47) | **DR-3: P7 green→red→green with restart-restore documented VERBATIM** | On demo: readiness drill via `ENSEMBLE_READINESS_FORCE_DEGRADED` (knob read per refresh tick): green → set knob → red (503 + forced-reason + `[Readiness] degraded` log) → clear knob + **restart to restore green** | 5-row timestamped transition (Phase-1 §1 P7 shape: 200/`reasons:[]` → 503/forced-reason → 200/`reasons:[]`); `/livez` 200 throughout (independence); **runbook contains the verbatim note** (T2) | Runbook DR-3 section (with verbatim note) + RESULTS file |

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **T1** | **Author the drill runbook** (new doc, D2): DR-1/DR-2/DR-3 + DR-4 promote/rollback drills (P2.1 pipeline on demo: clean promote, induced-failure rollback, cap-exhaustion halt, sweep-recovery) + DR-5 Ari-driven drills (P2.2: restart + upgrade via tools) + prerequisites, sandbox conventions, restore procedures, live-contact prohibition | P2.1, P2.2 | Runbook exists at its canonical path; every drill has: exact commands, expected outputs, one-line pass criterion, evidence-to-capture list; P7 verbatim note present (T2); reviewer sign-off that a fresh operator could execute DR-1..DR-5 without asking questions |
| **T2** | **Embed the P7 note VERBATIM** — the runbook's DR-3 section quotes verification.md:47 word-for-word: "P7 drill on deployed daemon requires restart to restore (env knob read per refresh tick; `readiness.py:50-67`) — document in Phase 2 drill runbook so green-restore steps aren't assumed instant." | T1 | `grep -F` of the verbatim string in the runbook returns the line (checked in review) |
| **T3** | **Execute DR-1** (carry-over #1): tempfail→respawn full cycle on demo | T1, P2.1 | Closure-matrix pass criteria met; RESULTS file with verdict "DR-1 PASS"; `.launcher-state` inspected (no abort, no budget decrement) |
| **T4** | **Execute DR-2** (carry-over #2): exit-78 sandboxed smoke | T1 | Captured exit code 78; no-loop assertion; RESULTS file with verdict; sandbox conventions verified (own port + throwaway PG + throwaway data dir; live pids checkpoint unchanged) |
| **T5** | **Execute DR-3** (carry-over #3): P7 green→red→green with restart-restore | T1 | 5-row timestamped transition captured; `/livez` stayed 200; demo restored green (restart performed + verified); RESULTS file |
| **T6** | **Execute DR-4 (pipeline drills on demo)**: clean promote; induced-failure auto-rollback; cap-exhaustion (3 rollbacks/24h → halt-for-human); sweep-recovery (stale `in_flight` journal → launcher-start sweep). **Sequenced journal-reset REQUIRED after DR-4 (2026-08-22):** the cap-exhaustion leg leaves 3 rollbacks + a halt in the demo journal's 24h window — execute the R3.2 ledger/journal reset (archive + fresh state) and verify counters zeroed BEFORE T9 clean cycles begin, so DR-4's deliberate rollbacks cannot pollute the N-gate ledger | T1, P2.1 | 4 transcripts; journal events match ADR-005/012 expectations exactly (commit / rollback+quarantine+cooldown / halt / sweep_rollback); demo restored to good state after each; **post-DR-4 journal reset executed + verified (counters zeroed, halt cleared) — the reset transcript is part of DR-4 evidence** |
| **T7** | **Execute DR-5 (Ari-driven drills)**: Ari performs `system_restart` and `system_upgrade` end-to-end on demo/sandbox via P2.2 tools; fake-confirmation + live-target refusals exercised | T1, P2.2 | Tool transcripts + journal events; refusal paths asserted; Ari's reported state matches journal ground truth (parity check) |
| **T8** | **Alerting wiring** (D4): SSE NotificationBroadcaster events on burst-abort / cap-halt / auto-rollback+quarantine; journal `history` entries as durable record; **watchdog-watcher journal-watch extension per ADR-025(b) (D-FA1.2) — implemented, not just documented** (watch set gains `.launcher-state` abort markers + `releases/state.json` halt/sweep events) | **P2.1 T4 (HARD: `releases/state.json` does not exist until the pipeline writes it — the watcher extension cannot be built or tested before the journal exists)**, P2.2 | Demo: induced halt (from DR-4) produces an SSE notification captured by a subscribed test client; journal event present; **watchdog-watcher extension detects a daemon-down halt/burst scenario from `.launcher-state`/journal files alone in sandbox** (assumption-#3 closure, `decisions.md` pre-freeze checklist: file-format stability confirmed by the test reading the real files) |
| **T9** | **N-cycle ledger + USER-GATED live promotion design**: run ≥3 clean cycles **per the CANONICAL clean-cycle definition in `test-strategy.md` §4.1** (per-criterion evidence mapping: D1's table) — or record current count + gate design if cadence limits; ledger table in runbook (criterion × cycle matrix); live-promotion user-confirmed action sequence documented (D3); **no live step executed** | T1-T8 | Ledger shows cycle-by-criterion PASS/FAIL against **§4.1's five clauses** with evidence links; gate rule stated ("3 consecutive, zero violations"); live-promotion doc enumerates the user-only commands with guard variables; constraint-compliance section asserts zero live contact across the whole phase |

---

## USER-GATED (designed, documented, NEVER executed by this initiative)

1. **Live staged-mode migration** — first `ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/stage.sh live ...` converting the live dir from flat/legacy (`.bak`s) to staged mode. The design documents preconditions (3-cycle ledger green per ADR-021 N=3; backup guidance for the live dir — its content is out of bounds to us but not to the user), steps, expected journal events, and abort path.
2. **Live promote** — `ENSEMBLE_UPGRADE_LIVE=1 scripts/upgrade/promote.sh live VERSION=<v>` executed by the user; verification probes listed (read-only GETs only for any agent involvement).
3. **Live restart via tools** — remains refused by P2.2 tool ruling; the manual procedure (stop-ensemble.sh + launcher, both already shipped) is what the runbook documents.

---

## Coupling

- **Tight with P2.1** — DR-4 exercises the pipeline exactly as shipped; the ledger consumes journal `history`.
- **Tight with P2.2** — DR-5 exercises the tools; parity between Ari's reports and journal ground truth is the acceptance.
- **Tight with sibling `promotion-ladder.md` (W3)** — D1's N-cycle definition and D3's live rung are this phase's contribution to the ladder; W3 owns the full rung/evidence/rollback-of-ladder structure. Any W3 divergence on N or cycle definition resolves there; this doc's D1 is the default recommendation.
- **Loose with `test-strategy.md` (W3)** — drill automation mapping (which drills become packs vs. manual-with-transcript).
- **Independent of** daemon core code — T8's alerting touches the notification path only (existing service usage); if implementation instead adds new daemon surfaces, blast-radius re-assessment per `ensure.md`.

---

## Risks (phase-specific — full register: sibling `risk-register.md`, W2)

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R3.1 | DR-1 drill itself degrades demo (bad PG override persists) | Medium | Drill-scoped env override + explicit restore step in runbook; post-drill `/readyz` green assertion is part of DR-1 pass criteria |
| R3.2 | Demo cycles accumulate journal/quarantine state that poisons later cycles | Medium | Runbook includes ledger-reset procedure (documented journal archive + fresh state) — itself a tested restore path (T6 restore steps) |
| R3.3 | 3 consecutive clean cycles not achievable in initiative window | Medium | Ledger design tolerates partial completion: gate = measurement design + current count; promotion stays USER-GATED regardless (the gate protects the user's decision, not our schedule) |
| R3.4 | Alerting false-positives spam SSE consumers | Low | Events are terminal-class only (abort/halt/rollback+quarantine); no per-probe notifications |
| R3.5 | Runbook drift from shipped scripts (scripts evolve after authoring) | Medium | Runbook versioned + references script by version/commit; DR execution includes a script-version checkpoint line in evidence |

---

## Exit Criterion

**All objectively verifiable:**

1. **Runbook exists** at its canonical path with DR-1…DR-5 fully specified (commands, expected outputs, pass criteria, evidence lists) and the P7 verbatim note grep-verified (T1/T2).
2. **Carry-overs closed:** 3 RESULTS files with verdict lines "DR-1/DR-2/DR-3 PASS" + the exact evidence the closure matrix demands (T3-T5) — the three verbatim carry-overs are mappable line-for-line to drill verdicts.
3. **Pipeline + tool drills executed** on demo/sandbox with transcripts + journal events matching ADR-005/012 semantics (T6/T7).
4. **Alerting demonstrated:** induced halt → SSE notification captured + journal event (T8).
5. **Ledger shows the gate measurement design** — criterion × cycle matrix, ≥1 recorded cycle (target 3), gate rule stated (T9).
6. **Zero live contact** across the entire phase — constraint-compliance section in the final RESULTS file, live pid checkpoints at every drill start/end (Phase-1 §5 precedent).
