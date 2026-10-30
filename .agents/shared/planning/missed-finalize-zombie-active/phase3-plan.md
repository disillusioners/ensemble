# Phase 3 — Test Packs

## 1. Objective

Ship the full mutation-verified pin family for every decision leg — positives, negatives
(incl. the five starvation vectors as NON-triggers for healthy trees), race/fail-closed/
kill-switch pins, knob-wiring pins, zero-watcher correlation, and the real-PG trigger-suite leg —
plus PACKS.md entries per convention. Every pin is plant→RED→GREEN with digests; exact-string
pins where the design already cites exact shapes; collected-count fences on every pack.

Design references: D-13 (test-surface contract); technical-analysis §4 (vector table), §8
(concurrent-actor table), §12; findings-riders §8 (test surface + house discipline).

## 2. Prerequisites

Phases 1 + 2 complete (code exists to pin); Phase-0 fixture recipe available; fences standing;
main-venv interpreter path (no worktree `.venv`); PG packs: `PG_TEST_*` DSN, serial-only under
xdist.

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-3.1 | Packs live in `tests/job_queue/` as `test_zombie_active_close_*.py` (family neighborhood: `test_orphan_active_job_recovery.py`, `test_pattern_f_mission_live_gate.py`, `test_running_zombie_sweep.py`); the E1-default pin extends the mission-live-gate neighborhood; the R-10 legs stay in their existing packs | D-13 — the dense existing home; the f-arm harness (bus mocks, E1 fixtures) is reusable | A new top-level test dir (splits the family; conftest/fixture duplication) |
| PD-3.2 | Mutation verification = plant the DEFECT FORM (e.g. remove the discount conjunct; restore `c.n_live_tup`; drop the NOT EXISTS leg; invert fail-closed polarity), assert the pin goes RED, restore, assert GREEN — with file digests recorded per leg (planted-sha discipline) | House discipline (Repo & Dev Conventions [TESTING]): "regression pins MUST be mutation-verified"; worked precedents `PACKS.md:22`, `test_dbpool_wedge_capture_pg.py:56-59` | Green-only pins (unfalsifiable — cannot catch their defect; the v0.27.14 council explicitly rejected a pin that could not catch its defect) |
| PD-3.3 | Negative suite asserts NON-triggers for HEALTHY trees via predicate leg 1: any non-terminal Task row on the instance (pending retry child, fresh dispatch, PAUSED task) keeps the job OUT of the candidate set entirely | technical-analysis §4 "What a legitimate mission WAITS for"; PAUSED protection (D-3 rationale tail) | Testing only the hold-path (weaker: the predicate is the primary safety) |
| PD-3.4 | PACKS.md entries written keep-both newest-first with invocation/scope/run-anchor/status rows incl. collected-count fences | House convention (findings-riders §8); the file is multi-lane shared — never rewrite foreign sections | Single consolidated section (collides with concurrent lanes) |

## 4. Tasks (pack map)

| ID | Pack / task | Pins (outcome, testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|-------------|--------------------------|---------------------|------------|---------------|----|
| T3.1 | **Pack A — core close RED→GREEN:** zombie fixture (3 verdict variants) closes at HEAD with correct token + admission DONE + same-txn lock release, within one cycle of age eligibility; the IDENTICAL fixture HELD at BASE (no writer, no token, lock row persists) — base-side asserted from the Phase-0 evidence or re-derived in-pack at the base revision | Positive close assertions ×3 verdicts; age-boundary test (age = bound−ε ⇒ hold, ≥ bound ⇒ eligible — INCLUSIVE per the strand-healer precedent); batch-cap remainder-next-tick assertion | `tests/job_queue/test_zombie_active_close_core.py` (new) | P1 | R1, R5 | AC-3.1 |
| T3.2 | **Pack B — E1 surface zero-change + discount semantics:** existing-caller verdicts byte-identical with `discount_instance_id=None` (drive the f2/E1 call shapes with and without the param); discounted evaluation excludes EXACTLY the named instance (child job shape: member instance discounted, root still counts; root job shape: root discounted, members still hold) | Default-None byte-identity pin; discount-scoping pin (both root-job and member-job shapes) | `tests/job_queue/test_pattern_f_mission_live_gate.py` neighborhood (extend) + new `test_zombie_active_close_e1_discount.py` | P1 | R2 | AC-3.2 |
| T3.3 | **Pack C — healthy-tree negatives + PAUSED:** the five starvation vectors (root self-count, childless-root fallback, missing-anchor disarm, non-root activity re-roll, member-alive fail-MISS) exercised as NON-triggers for healthy trees (predicate leg 1 fails — non-terminal task present ⇒ never a candidate); PAUSED task on the instance ⇒ never a candidate; live sibling member ⇒ discounted-E1 HOLDS (mission_live_holds token, no write) | Zero-terminalization assertions across the vector matrix; hold-token assertions; PAUSED exclusion | `tests/job_queue/test_zombie_active_close_negatives.py` (new) | P1 | R1 | AC-3.3 |
| T3.4 | **Pack D — race + ordering:** two concurrent actors on one candidate (h-leg vs f2-shaped actor; h-leg vs observer-shaped actor) ⇒ exactly ONE finalize, loser no-ops INCLUDING lock side (`race_lost_noop` counter); same-transaction finalize→lock-release ordering (no interleaving state observable: job done ∧ lock present post-crash-sim is impossible); operator-lane concurrent shape (guarded predicate loses cleanly) | One-winner assertions; ordering pin; loser-side no-write incl. lock | `tests/job_queue/test_zombie_active_close_races.py` (new) | P1 | R3 | AC-3.4 |
| T3.5 | **Pack E — fail-closed polarity:** guard error/timeout ⇒ SKIP + `error_skips` + reason token, ZERO writes (plant: fail-open form ⇒ pin RED); bus-unavailable ⇒ fail-safe skip (f2 Gate-1 precedent shape) | Error-polarity pin; bus-unavailable pin | `tests/job_queue/test_zombie_active_close_failclosed.py` (new) | P1 | R1 | AC-3.5 |
| T3.6 | **Pack F — kill-switch + knob wiring:** OFF ⇒ COMPLETE no-op (no candidate query issued — assert via query-counter mock — no probe, no write; pre-fix behavior byte-restored); config Fields read at api.py boot → kwargs → reconciler args (knob-wiring pin in the `test_f1_killswitch_tz_matrix.py` / strand-heal wiring style); out-of-range value FAILS FAST at boot (pydantic `ge=1`); boot INFO log emitted once | OFF no-op pin; wiring pin; FAIL-FAST pin; boot-log pin | `tests/job_queue/test_zombie_active_close_knobs.py` (new) | P1 | R1 | AC-3.6 |
| T3.7 | **Pack G — tokens + watcher surface:** three tokens derive truthful canonical statuses (`terminal_reason_variants_for` round-trip); `is_terminal`/status-filter surfaces unchanged; zero-watcher WARN correlation (the (h) line references the PP1 token family); held `mission_terminal` rows SURVIVE the (h) fire (mission may still be live — admission-scoped close); terminal cleanup rides the notify path (`remove_all_watches_for_job` idempotence); **W1 pin (reviewer council): the FAILED-variant close notifies status='failed' (the canonical string via `canonicalize_status(token)`), never 'completed' and never the raw `zombie_active_failed` token — planted forms (raw token / f2-literal) both RED; companion assertions on the completed- and cancelled-variants** | Token-map pins; WARN-correlation pin; held-row survival pin; **canonical-notify-status pin ×3 verdicts (W1)** | `tests/job_queue/test_zombie_active_close_tokens_watchers.py` (new) | P1, T1.4 | R6, R8 | AC-3.7 |
| T3.8 | **Pack H — candidate query shape (static/exact-string):** exact-string pins over the candidate SQL (task-kind filter, active filter, `deleted_at IS NULL`, work_id JOIN, instance NOT EXISTS, `MAX(completed_at)` age key — NOT `last_activity_at`, LIMIT, **deterministic ORDER BY per S5: oldest-terminal-first `MAX(task terminal stamp) ASC` with `job_id` tiebreak — LIMIT without ORDER BY is non-deterministic across ticks**); greppable log-line assertions (`reconcile_drift_states: Pattern (h) zombie-active close`, skip tokens) in the `test_running_zombie_sweep.py:1158` style; **S4 pin (reviewer council): NULL-instance candidate (instance_id IS NULL) passes the instance NOT EXISTS probe vacuously — pin that the close finalizes WITHOUT attempting/requiring lock release (the lock DELETE skips safely at `repository.py:4270-4273`; no crash, no vacuous DELETE issued)** | Exact-string source pins (incl. ORDER BY); log-line pins; **NULL-instance finalize-without-lock-release pin (S4)** | `tests/job_queue/test_zombie_active_close_static.py` (new) | P1 | R4 | AC-3.8 |
| T3.9 | **Pack I — real-PG leg:** trigger suite present (`trg_job_locks_active_guard` both directions) + the close survives it; same-txn lock release verified on real PG; seeded data end-to-end (fixture→cycle→DONE+token+lock gone); runs under the postgres marker, serial-only | Real-PG close assertion under the live trigger suite | `tests/postgres/test_zombie_active_close_pg.py` (new) | P1 | R5 | AC-3.9 |
| T3.10 | **R-10 legs:** static pack green UNCHANGED (run-only, no edits — the isolation proof); PG pack re-pointed green; the old-form plant (`c.n_live_tup`) RED→GREEN recorded in the consolidated matrix | Mutation leg row in the matrix; both packs green | (Phase 2 files) | P2 | — | AC-3.10 |
| T3.11 | **Mutation matrix + PACKS.md:** consolidate EVERY pin's plant→RED→GREEN evidence (defect form, planted sha/digest, RED proof, restore, GREEN proof) into the lane's mutation matrix (TSV/MD per the v0.27.15 precedent); write PACKS.md entries (keep-both newest-first) with invocation/scope/run-anchor/status + collected-count fences per pack | Matrix complete (every leg accounted, incl. Pack J and the T1.9 A2 disposition leg); PACKS.md rows added; counts fenced | `.agents/tester/PACKS.md` + lane evidence doc | T3.1-T3.12 | — | AC-3.11 |
| T3.12 | **Pack J — architect-amendment pins (A1/A3/A5/A6 per architect §10, folded):** (i) **A1** — the (h) counter block is emitted whenever `examined>0` (plant: gate on `reconciled>0` ⇒ pin RED); non-zero `error_skips` surfaces in the drift-stats WARN; the escalating WARN fires at ≥K≈40 consecutive error_skips/mission_live_holds for the same job (plant: K unchecked ⇒ RED); (ii) **A3** — every close log line carries `notify=<outcome>`; a post-commit notify-loss shape leaves the follow-up ticket token (terminal job with surviving watcher rows routed to the `watch_reconcile_sweep` follow-up semantics); (iii) **A5** — attribution query shape is the explicit IN-list `('zombie_active_completed','zombie_active_failed','zombie_active_cancelled')`, never a prefix-LIKE (static exact-string pin; plant a LIKE form ⇒ RED); (iv) **A6 defensive E1 pins** — (a) the member-alive candidate builder never re-arms fail-MISS on the discounted instance (`mission_live_guard.py:605-612` invariant: candidate set excludes root + non-terminal-only), (b) the childless-root `member_count==0` fallback (`:925-930`) is never on the (h) close path (anti-"unify the anchor" regression: plant a shape where the fallback gates the close ⇒ RED); (v) the **A2 disposition leg** mirrors T1.9's parameterized shape — the in-txn re-check pin: with `recheck_instance_inflight=True` a retry child minted before the UPDATE makes the txn abort (planted no-recheck form ⇒ RED); the DEFAULT-False byte-identity pin: existing callers (`cleanup_non_terminal_jobs` orphan reaper, `job_queue_service.py:2180`) issue byte-identical SQL with the flag absent; if the pin+disclose fallback was taken instead, the fallback disclosure names the RETRY-REARM ORPHAN CORNER explicitly (the one non-consistent terminal ordering, bounded by the 7200s gate — never softened to "shared window") and the window pin (retry child minted in-window ⇒ close aborts/foreclosed next cycle) is RED under the planted no-check form | Every amendment pin plant→RED→GREEN with digests; Pack J collected-count fenced | `tests/job_queue/test_zombie_active_close_amendments.py` (new) | P1 (incl. T1.9 disposition) | R1, R2, R6 | AC-3.12 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-3.1 | Core close proven both directions (HEAD closes / BASE holds) for all three verdicts; age boundary inclusive; batch-cap remainder behavior pinned |
| AC-3.2 | Default-None byte-identity pinned; discount scoping pinned for root-job AND member-job shapes |
| AC-3.3 | Five starvation vectors + PAUSED + live-sibling all NON-triggers (zero terminalizations); hold tokens asserted |
| AC-3.4 | One-winner race pinned (loser no-op incl. lock side); same-txn ordering pinned; operator-shape loses cleanly |
| AC-3.5 | Fail-closed + bus-unavailable skips pinned with zero writes; fail-open plant RED |
| AC-3.6 | OFF complete no-op (incl. no candidate query); knob wiring + FAIL-FAST + boot log pinned |
| AC-3.7 | Token derivation + terminal-set unchanged + WARN correlation + held-row survival pinned |
| AC-3.8 | Candidate SQL exact-string pins + log-line pins green |
| AC-3.9 | Real-PG close under the live trigger suite green (serial-only, `PG_TEST_*` DSN) |
| AC-3.10 | R-10: static pack green UNCHANGED; PG pack re-pointed green; old-form plant RED |
| AC-3.11 | Mutation matrix complete with digests (incl. Pack J + the A2 disposition leg); PACKS.md keep-both entries with collected-count fences |
| AC-3.12 | A1/A3/A5/A6 amendment pins green and mutation-verified (each plant form RED); A2 leg mirrors the T1.9 disposition |

## 6. Verification method

Every pack run with explicit `tests/` paths; `tee LOG >/dev/null` + `tail -2 LOG` for floods;
collected counts recorded and fenced (`--collect-only -q` byte-faithful for identity); PG packs
serial + explicit `--timeout` where needed; mutation legs recorded in the matrix with digests.
**GNU grep discipline for any gate grep baked into packs/scripts: plain `|` under `-E`, NEVER
`\|`; paste-execute every grep before freezing it as an assertion.**

## 7. Risks & notes

- Pack D's concurrent actors must use the REAL guarded seams (repository methods), not mocks of
  the guard — the race proof is about the SQL predicate, and mocked guards prove nothing (the
  v0.27.14 council's "pin that could not catch its defect" rejection).
- Pack I needs the PG trigger suite actually INSTALLED on the scratch fixture (the manager
  install path, `manager.py:8201-8206`) — assert trigger existence first, else the leg is vacuous.
- If D-3 flips (pass-through tokens): **foreclosed by the architect proof of harm** — record
  only. Mechanically T3.7's token-map pins re-point to pass-through assertions
  (AC-3.7 adjusts; the WARN/hold/survival pins unchanged). If D-6 flips: T3.6's posture pins
  invert (OFF-by-default + opt-in), same mechanics.
