# report-obligation-visibility — Plan Overview

**Commission:** wi-1fbee2ec — report-delivery obligation visibility + WC-orphan detection
**Worktree:** `/home/nea/ensemble-src-wt-obligation` · branch `fix/report-obligation-visibility` · base `f7039c3ff` == `origin/latest` (verified clean)
**Doc class:** LEAN EXECUTION PLAN (verify-don't-redesign; design direction FIXED). Companion: `design-decision.md` (same dir).
**Anchor provenance:** all line numbers verified at current tip `f7039c3ff` (2026-11-01) via worktree bash reads; oversized files (`child_reports.py`, `instance_messaging.py`, `graph.py`, `manager.py`) greped directory-scoped / read directly. Base == tip, no drift.

---

## 1. Objective

Make report-delivery obligations **visible the moment they exist** (minted synchronously in the child-terminalization transaction — transactional outbox), instead of surfacing up to 300s later at the RDRS tick; make **both completion checkpoints HOLD** while a descendant report obligation is outstanding; stop **RDRS from reviving a terminal recipient under a terminal root** (the apparition shape); and give the **WC-orphan shape** (WC instance with a TERMINAL parent, incl. the pure-WC-grandchildren sub-shape) a detector, because no lane sees it today.

**Mutation shape under repair (10:35Z 2026-10-31 incident class):** undelivered descendant report + quiet tree. OLD: spawn+auto-dispatch children mint no watchers → all in-turn liveness arms read REAL zeros (no fail-open fires) → DENIED ×3 → 4th evaluation trips `deny_bound_exceeded` (`attestation_gate.py:925-931`) → `safe_set_escalated_and_reset` (`graph.py:6908-6918`) → root closes `terminal_after_bound` with an undelivered report → 41s later RDRS backfill revives the TERMINAL recipient → completed-root + WAITING_CHILDREN-child apparition. NEW: the obligation exists at child terminalization; the gates hold on it; the root cannot close at bound while a descendant report is owed; RDRS revival is root-terminal-aware.

## 2. Scope — five parts (binding)

| Part | What | Atomicity / sequencing |
|---|---|---|
| **1** | Mint the report-delivery obligation SYNCHRONOUSLY at child terminalization (NOT at RDRS 300s-tick detection). Transactional-outbox semantics: same transaction as the child terminalization commit. Substrate: **reuse `report_injections`** (commission ruling; brief silent). Define idempotency across re-entrant terminalization AND who-wins vs RDRS backfill mints. | **ONE ATOMIC CHANGE with Part 2 — each useless alone.** |
| **2** | "Outstanding descendant report obligations" as a **HOLD input** to BOTH gate checkpoints: the in-turn attestation gate AND the emission-time gate. Hold-on-outstanding. | ↑ (Part 1's atomic partner) |
| **3** | RDRS late-backfill revival ordering: check the recipient's ancestor/root terminal state BEFORE reviving a terminal recipient. Must COMPOSE with the live v0.28.2 T1 funnel-top reactivation (T1 continues to serve real message flow). | independent |
| **4** | WC-orphan detector: WC-instance-with-TERMINAL-parent + pure-WC-grandchildren sub-shape. Firing surface = OPEN DECISION D2. | independent |
| **5** | LOW-PRIORITY annotations: (i) `epochs[]` read-model µs-twin; (ii) report-delivery re-cycles bypass the attestation-ledger reset. **DISPOSITION (adjudicated by planner): ANNOTATE-ONLY in design-decision.md — no code scope.** DROP if time-boxed. | rides docs only |

## 3. Provenance split (binding, stated honestly)

- **The commission dispatch IS the design source** for the five parts: substrate ruling, idempotency shape, RDRS ordering, WC-orphan firing-surface open decision, part-5 annotate-only disposition, interim-typing flag.
- **The frozen core-layering brief is the CONSTRAINT source** — context-pointer: `/home/nea/ensemble-src/.agents/shared/planning/core-layering/` (MAIN checkout only copy; the worktree copy DOES NOT EXIST; FROZEN — reference by pointer only, never copy-modified).
- **Material discrepancy (verified):** the brief is the LAYERING brief (phases P0–P5) and explicitly declares this commission's design OUT OF SCOPE — `architecture-recommendation.md:244`: "Report-delivery obligation semantics (wi-1fbee2ec family) … Designing it here is out of scope". The five-part rulings have ZERO presence in the brief. Consequence: for design substance, the commission text rules; the brief binds constraints only.
- **What the brief binds (cited):** the five-leg gate table (`architecture-recommendation.md:121-134`); polarity doctrine (`sequencing-detail.md:71-78` — "three different polarities exist today — do not 'harmonise' them… Naming it is P3; changing it is a separate decision"; `risk-register.md` R3 — "polarity per leg preserved byte-for-byte"); HOLD-never-RELEASE cross-cutting rule (`architecture-recommendation.md:177-179`); invariant table (`:236-246` — revive-on-send untouched; completion-gate holds untouched; "Any phase that would shorten a hold is out of scope"); ordering constraint (wi-1fbee2ec lands BEFORE core-layering P4; P1–P3 must not touch delivery semantics — `arch-rec:244`, `sequencing-detail.md:90`, `risk-register.md:37`); scope gate ("interim" appears ZERO times in the brief → interim-report typing is OUT of scope, FLAG-ONLY).
- Seam findings below are **current-tip authority** (`f7039c3ff`), superseding the brief's v0.28.0-era receipts.

## 4. Current-tip seam table (one row per part; all anchors @ `f7039c3ff`)

| Part | Seam | Current location | Invariant to preserve |
|---|---|---|---|
| 1 | Entry | `child_reports.py:2792` `_process_child_completion_and_notify_parent` | content fetched pre-txn `:2823-2834`; root-only pre-stamp recursive liveness hold `:2897-2992`; T2 hold-branch reactivation `:2938-2991` |
| 1 | Sync DB half + txn | `_process_child_completion_db_sync`; `WriteGuardSession(Session(engine))` `:3180` (worker thread via `asyncio.to_thread` `:3040-3045`; per-parent bus lock `:3031-3034`) | ONE session/transaction for all four branches |
| 1a | Terminalization commits | root `:3979`; tool-invocation `:4185`; regular child `:5173` — single commit covering child CAS UPDATE `:4334-4350`, report msg `:4392-4407`, PROCESS_REPORT task `:4532-4543`, ReportInjection `:4700-4751`, events `:5136-5160` | child CAS `WHERE status NOT IN (paused, completed, error)`; `rowcount == 0` → rollback + skip `:4351-4369` |
| 1b | Mint attach point | ReportInjection INSERT inside `begin_nested()` SAVEPOINT `:4700-4751` (SAVEPOINT rollback `:4785`; discriminator `_is_obligation_triple_integrity_error` `:4786`; outer commit `:4835+`; F5 guard `:4850-4851`) | the outbox pattern ALREADY exists here — a mint joins this txn, adjacent, before the outer commit |
| 1c | FROZEN idempotency guard | status-level `:3240-3428` (terminal row → `idempotency_skip` return `:3378-3383`, BEFORE the mint region); v0.28.2 T3 exception: terminal row + live direct children → re-arm WAITING_CHILDREN `:3352-3372`; PAUSED → deferred_pause marker `:3384-3428`; message-level `existing_report` by source `internal_report:{child}:{msg}` → skip `:4111-4126` | re-entrant terminalization never reaches the mint → once-per-lifecycle via CAS + guard |
| 1d | Dedicated-session markers | the two DEFERRED skip-path markers (`:3395-3422`, `:4076-4091`) deliberately use a dedicated `ReportInjectionRepository(engine)` session — NOT same-tx | correctness = W6 write-once index + best-effort; **do NOT refactor them to same-tx** |
| 2 | In-turn gate | `attestation_gate.py` `decide()` `:640-939`; R2 input reads `:1483-1549`; whole-eval fail-open-ALLOW `:1550-1636` (+ `-1` sentinel contract `:1638-1649`); deny bound `deny_bound_exceeded` `:486-539` (default 3); TERMINAL_AFTER_BOUND `:925-931`; withhold budget 8 `:588`; graph node `graph.py:5932`; never-spoke composition gate `graph.py:6602-6703` (loud terminal `:6649-6673`); ledger write `graph.py:6908-6918`; terminal event `:6948-6984` | 10:35 mechanism: real-zero arms → DENIED ×3 → 4th eval trips bound → escalation |
| 2 | Emission-time gate | `_root_completion_gate` `child_reports.py:2574-2790`; caller wrap `:5426-5451` (exception → `(True, None, False)` fail-OPEN per FP4-L5 `:5403-5425`); deny → downgrade COMPLETED→WAITING_CHILDREN + suppress publish `:5462-5486` (commit `:5481`); runs in `_dispatch_post_commit_side_effects` `:5194` | five legs enumerated in §6 — verdicts and polarities byte-identical |
| 3 | RDRS lane dispatch | `_run_all_lanes_sync` `report_delivery_recovery.py:1001-1100` (L1 `:1042`, L2 `:1047`, L3 `:1059`, L4 `:1066`, L5 orphan `:1074→:1146-1156`, L6 stuck-wake `:1088→:1261-1441`, L7 `:1095`); LANE-6 exception doc `:121-169` | seven lanes; lane map unchanged |
| 3 | Terminal-parent revival | `_recover_one_deferred_row` step 2 `:1477-1493` → `_try_revive_terminal_parent` `:1541-1585` → `manager._revive_terminal_instance` `manager.py:10754-10867` (terminal set `:10810-10817`; CAS `:10835-10841`; write+commit `:10842-10855`); busy-check `:1474` (exception → default-busy fail-closed `:1234-1248`) | today: unconditional revival for non-busy terminal recipients — **no ancestor/root-terminal check exists** |
| 3 | Lane-6 wake-only | `enqueue_message(parent, source="system:wedge-resolve", priority=1)` via `run_coroutine_threadsafe(...).result(8.0)` `:1411-1428` | bypasses the frozen guard (guard `idempotency_skip`s before any wake row) |
| 3 | Lane-2 backfill mint (sole RESUME_ROUTER writer) | `_run_no_row_backstop_lane` `:1691-1769` (`parent_not_terminal=True` at `:1717-1720`); `ensure_deferred(..., deferred_reason=RESUME_ROUTER)` `:2016`; constant `daemon/constants.py:827`; `find_completed_children_without_delivery` `repository.py:1109` | backfill mints only for non-terminal recipients today |
| 3 | v0.28.2 T1 funnel-top reactivation (LIVE) | `instance_messaging.py:3855-3889` (top of `_process_message_with_tracking`: every turn incl. lanes bypassing enqueue flips terminal row → RUNNING via `_revive_terminal_instance`, best-effort fail-open); canonical pins `tests/unit/services/test_terminal_row_status_strand.py:1-27` | **Part 3 must COMPOSE, not fight** — T1 keeps serving real message flow |
| 3 | send_message revival carve-out | `instance_messaging.py`: defer-forcing `:2432-2466`; fresh-read terminal set `:2562-2567`; reactivation IDLE/WC/terminal → RUNNING `:2568-2580` (incl. attestation-ledger reset trigger (3)); enqueue txn `:2311`; fresh-episode reset `:2613-2645` | revive-on-send semantics unchanged (pin d) |
| 4 | Write-once index | migration `20260819_000001_report_injections_deferred_marker.sql:114-120` (DOWN `:143`); model `Index` `models.py:227-235` (`sqlite_where`/`postgresql_where` `state IN ('PENDING','DEFERRED')`) | write-once per obligation triple among NON-terminal rows; terminal dupes allowed (fresh lifecycle `:214-219`) |
| 4 | State enum | `models.py:83-122`: PENDING, DEFERRED, INJECTED, TASK_DELIVERED, FAILED; UPPERCASE TEXT `:308`; `deferred_reason` free-text open vocabulary `:313-314` (doc `:181`) | FAILED = dead-letter sentinel; index covers PENDING/DEFERRED only → a re-owed cycle CAN mint a fresh row (noted in §8) |
| 4 | Shielded-marker precedent | `_shielded_marker()` `message_processing_pipeline.py:1029-1032` (MOVED from child_reports); FM-11 `asyncio.shield` inside async def; `ensure_deferred` via dedicated session `:998-1004` | precedent for shielded marker writes |
| 4 | Claim/repo primitives | `transition_deferred_to_pending` `repository.py:1011`; `find_stuck_wake_candidates` `:2052`; colon-anchored delivery evidence `LIKE 'internal_report:'||child||':%'` (documented `child_reports.py:4374-4389`) | ID-semantics split unchanged |
| 5 | WC enumeration — strand healer | `mission_strand_healer.py:546-553` (`status == WAITING_CHILDREN AND parent_id IS NULL`; non-root → "not a root instance" skip); running-zombie posture `:84-98` (drive `:1300-1351`); ghost-signature abort `:650-662`; terminal-parent safety bound `:663-679`; obligation leg `:597-608` | ROOTS ONLY by explicit predicate |
| 5 | WC enumeration — others | `watch_reconcile_sweep.py:88-115` (held `mission_terminal` job-watchers, 300s `:88`); `orphan_watcher_sweep.py:75-99` (dead-`source_task_id` PENDING watcher cancel, 90s `:75`); RDRS = obligation rows only; wedge captures = diagnostic; watchover = user-invoked | NO lane enumerates WC instances except the healer, which is roots-only |

**WC-orphan gap (code-proven, current tip):** a WAITING_CHILDREN instance whose parent row is TERMINAL is seen by nobody — healer excludes non-roots (`:552-553`); RDRS L2 excludes terminal parents (`:1717-1720` + `repository.py:1180-1186` "the ORPHAN lane is the terminal-parent entry path"); L5 sees only existing DEFERRED rows (`:1146-1156`) — a WC instance with NO marker row is invisible; other sweeps touch watcher/job rows, not WC status. Pure-WC-grandchildren are double-blind: the healer tree leg aborts fail-closed when any non-root non-terminal member bears execution evidence (`:650-662` — WC members fail `filter_never_dispatched_ids`), plus the terminal-parent bound `:663-679` and the roots-only posture upstream. T1–T3 reactivate terminal PARENT rows under live trees; nothing re-drives a non-root WC row.

## 5. Moved anchors (old → new)

| Old (brief-era anchor) | New @ `f7039c3ff` |
|---|---|
| child_reports `:2458` | `:2792` |
| `:2773-2787` (child-status / idempotency guard) | `:3240-3428` + `:4036-4138` |
| `:2543-2760` (emission-time gate) | `_root_completion_gate :2574-2790` + caller `:5426-5486` |
| `_dispatch_post_commit_side_effects` | `:5194` |
| instance_messaging revival carve-out `:2252-2274` | defer-forcing `:2432-2466`; terminal set `:2562-2567`; reactivation `:2568-2580` |
| RDRS dispatch `:720-768` | `:1001-1100` (constructor now) |
| RESUME_ROUTER writer `:946-951` | `:2016` |
| `find_completed_children_without_delivery :1380` | `repository.py:1109` |
| `find_stuck_wake_candidates :1974` | `:2052` |
| `_shielded_marker` | `message_processing_pipeline.py:1029-1032` |
| `_revive_terminal_instance` | `manager.py:10754-10867` |
| Atomic Mint precedent `child_reports ~3268-3565` (v0.28.2 anchor) | same txn shape; commit `:5173` |

Stale refs survive in `report_delivery_recovery.py` docstrings `:85/:139/:1372` — doc-only debt; do NOT chase in this commission.

## 6. THE POLARITY TRAP — emission-time gate: five-leg × polarity matrix

`_root_completion_gate` (`child_reports.py:2574-2790`), evaluated at emission inside `_dispatch_post_commit_side_effects` (`:5194`). Deny → downgrade COMPLETED→WAITING_CHILDREN + suppress publish (`:5462-5486`). Three different failure polarities exist today across five legs / four authorities — the brief binds them preserved exactly (`sequencing-detail.md:71-78`; `risk-register.md` R3 "byte-for-byte"; `arch-rec:177-179` HOLD-never-RELEASE). **This commission EXTENDS the aggregate WITHOUT harmonizing polarities.**

| # | Leg | Seam (current tip) | Fires DENY when | Error polarity TODAY (binding) | Assertion vs this commission |
|---|---|---|---|---|---|
| 1 | bus pending | `_bus_count_pending_for_target_sync :2636-2638` | count > 0 | fail-OPEN (error returns 0) | **unchanged except the new leg is appended after it** |
| 2 | outstanding commissions | `_count_outstanding_mission_commissions :2690-2694` (anchor-scoped) | count > 0 | fail-OPEN (unknown sentinel reads inactive → 0) | unchanged |
| 3 | tree liveness | `evaluate_mission_live :2741-2750` | live=False | SPLIT: tree-walk error → live=False+error → PROCEED fail-OPEN `:2723-2727`; member-alive probe error → live=True+error → **HOLD** `:2729-2735`; guard exception → fail-OPEN `:2753-2765` | unchanged — all three sub-paths preserved byte-for-byte |
| 4 | pending messages | same-session COUNT `:2767-2779` | count > 0 | exception propagates → caller-wrap fail-OPEN `:5426-5451` | unchanged |
| 5 | message freshness | `_gate_wedge_resolver :2787` | wedge-unresolvable → DENY; verdicts forwarded (P1-2) | resolver decides | unchanged |
| **6** | **NEW — outstanding descendant report obligations** | non-terminal (`PENDING`/`DEFERRED`) `report_injections` owed to the gated instance's mission subtree, anchored per the commissions-leg precedent | count > 0 → DENY, reason carries the obligation count → joins the existing downgrade path `:5462-5486` | **OPEN DECISION D1** — recommended default **fail-closed** (query error → DENY, HOLD-until-bound, composing with at-least-once re-drive; see design-decision.md) | NEW leg appended; legs 1–5 verdicts and polarities byte-identical |
| wrap | caller wrap | `:5426-5451` | — | exception → `(True, None, False)` fail-OPEN (FP4-L5 `:5403-5425`) | unchanged. If D1-ii adopted, the NEW leg catches its OWN query error INSIDE the leg and converts it to a HOLD verdict — it must NOT ride the caller-wrap fail-open |

**Context-only — the same-tx sync-helper gate legs (must not be disturbed):** A8 bus hard-error fail-CLOSED `:3444-3451`; pending-task guard fail-OPEN `:3480-3493` (D2.8); inline bus COUNT fail-CLOSED `:3595-3637` (`:3560-3567` no-catch rationale); live-children cross-check fail-CLOSED `:3661-3692` (`:3653-3660`); own-queue finalizer `:3736-3810`; FP4 L1 WC-discharge `:3900-3932`; CAS stamp `:3948-3978`. **D2.8 LOCKED ordering: bus (fail-CLOSED, same-tx) > tasks (fail-OPEN) > declared-waiting (fail-OPEN, LAST)** — part-2 work preserves this order. The terminal-row idempotency-guard liveness COUNT is deliberately **fail-to-SKIP** (`:3319-3351`) — OPPOSITE to the writer's fail-CLOSED by design ("a transient DB error must not churn a genuinely-completed row"); do NOT "fix" it.

## 7. In-turn attestation gate: arms table + assertions

`decide()` `attestation_gate.py:640-939`; R2 reads `:1483-1549`; ALLOW-leg family `:663-680`; whole-eval fail-open-ALLOW on −1 sentinels `:1550-1636` + `:1638-1649`; attested+text → ALLOW+reset `:881-891`; attested non-text → HOLD `:897-910`; deny bound `:486-539` (default 3) → TERMINAL_AFTER_BOUND `:925-931`; never-spoke composition `graph.py:6602-6703` (withhold budget 8 `:588`; loud terminal `:6649-6673`); ledger write `graph.py:6908-6918`; terminal event `:6948-6984`.

| Arm | Read seam | Posture | Assertion |
|---|---|---|---|
| user_answer_pending | `manager.has_open_user_answer :655-662` (read `:1506-1511`) | on-absent reads False; ALLOW-leg | unchanged |
| pending_children | `manager.count_pending_children :1484` | ALLOW-leg; −1 sentinel → whole-eval fail-open ALLOW | unchanged |
| queued_or_expected_wakeups | `manager.get_queued_or_expected_wakeups :1487` | ALLOW-leg; same sentinel contract | unchanged |
| live_descendants | `manager.count_live_descendants manager.py:13010-13069` (two-set: RUNNING/WAITING/WC/PAUSED unconditional; IDLE/QUEUED only with unprocessed msg/unsettled job; terminal excluded) `:1489` | ALLOW-leg; same sentinel contract | unchanged |
| outstanding_commissions | anchor-scoped cross-epoch, read `:1539-1549` | ALLOW-leg; same sentinel contract | unchanged |
| **NEW — outstanding descendant report obligations** | non-terminal `report_injections` owed to the instance's mission subtree (anchor pattern per commissions leg) | **HOLD-on-outstanding**: > 0 → hold the completion WITHOUT advancing `denied_count` (legitimate-pending family semantics per `:663-680` — the R2 non-reset IS the loop protection). Query-error posture = OPEN DECISION D1 (recommended fail-closed DENY → bound-composed degradation; the arm's error path must be isolated from the `:1550-1636` whole-eval fail-open-ALLOW catch) | NEW arm; all five existing arms, the attested-split `:881-891`/`:897-910`, bound 3, and budget 8 unchanged |

**Why bound-exempt on outstanding (derived from the commission's own pin (a)):** the OLD shape closed the root at the 4th evaluation (`denied_count+1 > 3`). Pin (a) GREEN requires the hold to PERSIST past what would have been the 4th evaluation — so the outstanding posture must NOT advance `denied_count`, or the new arm would reproduce the closure it exists to prevent. The ERROR posture (D1) is deliberately different: a persistent query error is not evidence of pending work and degrades to `terminal_after_bound` like any persistent deny — never wedges forever.

## 8. Per-part implementation notes (WHAT seam / WHAT invariant — not how-to-code)

- **Part 1 — sync obligation mint (transactional outbox).** WHAT: at child terminalization, mint the report-delivery obligation row on the `report_injections` substrate in the SAME transaction as the terminalization commit — attached adjacent to the existing ReportInjection INSERT region `:4700-4751` (inside the `begin_nested()` SAVEPOINT), released before the outer commit `:5173`. Invariants: (i) obligation row persists **iff** the terminalization transaction commits (rollback ⇒ no orphan obligation row); (ii) re-entrant terminalization never reaches the mint (CAS `:4334-4369` rowcount=0 → rollback+skip; status-level guard `:3240-3428` → `idempotency_skip :3378-3383` BEFORE the mint region; message-level `:4111-4126`); (iii) sync-vs-backfill race benign: write-once triple index (`models.py:227-235`; migration `:114-120`) — the loser takes IntegrityError → SAVEPOINT rollback `:4785` + triple-discriminator `:4786` → skip; first writer wins; sync-vs-backfill provenance distinguished via the `deferred_reason` free-text vocabulary (`models.py:313-314`) + the app-constant mirror convention (`daemon/constants.py`, C1 UPPERCASE contract `:308`) — **no schema change**; (iv) re-owed cycles (terminal dupes) CAN mint fresh rows — the index covers PENDING/DEFERRED only (fresh-lifecycle carve-out `:214-219`); note this in the implementation, do not close it.

- **Part 2 — obligation as HOLD input at both checkpoints.** WHAT: the §6 leg-6 row and the §7 new arm, per the matrix assertions. Invariants: hold-on-outstanding at BOTH checkpoints; legs 1–5 / five arms byte-identical (§6/§7 assertion columns); D2.8 ordering preserved; deny bound 3 (`:486-539`) and withhold budget 8 (`:588`) untouched; the in-turn outstanding posture is bound-exempt (§7 rationale); the new reads' error paths are explicitly structured per D1 and pinned by mutation.

- **Part 3 — pre-revival ancestor/root-terminal check.** WHAT: at the RDRS revival seam ONLY — `_recover_one_deferred_row` step 2 `:1477-1493` → `_try_revive_terminal_parent :1541-1585` — check the recipient's ancestor/root terminal state BEFORE reviving; suppress revival when the root is terminal (the obligation cannot deliver in-session; row disposition = OPEN DECISION D3). Invariants: T1 funnel-top reactivation `instance_messaging.py:3855-3889` untouched (T1 continues to serve real message flow); send_message carve-out `:2432-2466`/`:2562-2580` untouched; `_revive_terminal_instance` itself untouched (the check lives in the RDRS caller); lane map + lane-6 wake-only `:1411-1428` unchanged; L2 backfill `parent_not_terminal=True :1717-1720` + `ensure_deferred(RESUME_ROUTER) :2016` unchanged.

- **Part 4 — WC-orphan detector.** WHAT: a detector for WC-instance-with-TERMINAL-parent plus the pure-WC-grandchildren sub-shape, firing on the surface chosen in OPEN DECISION D2 (recommended: sweep-side — a new RDRS-family lane or adjacent periodic sweep; the ORPHAN lane is already "the terminal-parent entry path" per `repository.py:1180-1186`). Invariants: detector posture = observe → fire/disposition per the chosen surface's existing conventions (NEVER silent); wedge captures remain diagnostic-only; the healer's roots-only predicate `:546-553`, ghost-signature abort `:650-662`, and terminal-parent bound `:663-679` are NOT relaxed (option (b) was assessed structurally hostile — see design-decision.md D2).

- **Part 5 — DISPOSITION: ANNOTATE-ONLY.** Both annotations live in design-decision.md §4 as ready-to-place text. NO code scope. DROP if time-boxed.

## 9. Sequencing

1. **Parts 1+2 as ONE atomic change** (each useless alone — the mint without the hold is invisible; the hold without the mint has nothing to hold on).
2. **Part 3** (RDRS ordering) — composes with 1+2.
3. **Part 4** (WC-orphan detector) — independent; surface per D2.
4. **Part 5** — rides docs only.
External ordering constraint (brief, binding): this commission lands **BEFORE core-layering P4** (`arch-rec:244`; `sequencing-detail.md:90`; `risk-register.md:37`).

## 10. Pins the plan specifies (implementer writes them; every pin mutation-verified)

**(a) REPRO/behavior pin — undelivered report → gate holds.** Plant the 10:35 shape: terminal child with an outstanding undelivered descendant report obligation; all liveness arms REAL zero; zero watchers. OLD: denied ×3 → `terminal_after_bound` closes root. NEW: obligation arm reads > 0 → hold persists → root HOLDS. Red-green: RED = revert/disable the part-2 obligation input → bound-closure recurs (old shape); GREEN = part-2 live → hold persists past what would have been the 4th evaluation. **Must cover BOTH checkpoints** (in-turn arm + emission-time leg).

**(b) Obligation-mint-on-completion pin.** Child terminalization commit ⇒ exactly one obligation row, in the SAME transaction (terminalization rollback ⇒ no orphan row). Mutations: remove mint → row absent (RED); simulate re-entrant terminalization → no second row (CAS + guard + triple index); sync+backfill race → single row (integrity-error loser skip).

**(c) WC-orphan detector pin.** WC instance + terminal parent (+ pure-WC-grandchildren variant) → detector fires on the chosen surface (D2). Mutation: disable detector → no fire.

**(d) NO-REGRESSION pin.** v0.28.2 T1/T2/T3 canonical pins keep passing (`tests/unit/services/test_terminal_row_status_strand.py`); revive-on-send reactivation semantics unchanged (`instance_messaging.py:2568-2580`); RDRS backfill mint + sync mint race ⇒ single row (write-once), no double-delivery; **part-3 suppression pin**: terminal root + deferred obligation ⇒ NO revival (mutation: remove the pre-check → apparition reproduced).

**House conventions for every pin/test:** explicit `tests/` lists (never bare pytest); PG tests SERIAL-only (`tests/postgres/conftest.py`; DSN = `PG_TEST_*`); `tee LOG >/dev/null` for the 150K output cap; regression pins MUST be mutation-verified (planted-mutation RED → restore GREEN); net-new-failure = full FAILED-list diff vs TRUE pre-fix base, identical commands both sides; NO Docker. **Migration note: NO new table/column is expected** — `report_injections` is reused; a new `deferred_reason` value rides the open TEXT vocabulary + app-constant mirror (no DDL). IF the design ends up adding any table/column, a matching `daemon/migrations/versions/*.sql` is ABSOLUTE.

## 11. Risks

| ID | Risk | Impact | Mitigation |
|----|------|--------|------------|
| R1 | Polarity drift while extending the aggregate (the trap this commission is named for) | high | §6/§7 per-leg assertion columns; mutation-verified pins (a)/(d); reviewer gate diffs the matrix, not the code, first |
| R2 | In-turn arm mis-designed as bound-advancing → reproduces the 10:35 closure | high | §7 bound-exempt rationale (derived from pin (a) GREEN); pin (a) asserts persistence past the 4th evaluation |
| R3 | Sync-mint transaction weight / SAVEPOINT interactions (F5 guard `:4850-4851`, discriminator `:4786`) | medium | mint attaches adjacent inside the existing SAVEPOINT; pin (b) covers commit/rollback both directions |
| R4 | Double-delivery (sync mint × backfill mint × drain/task claim) | medium | write-once triple index + claim guards; pin (b) race leg + pin (d) no-double-delivery |
| R5 | Part-3 check fights T1/T3 (over-suppresses live-message flow) | medium | check confined to the RDRS revival caller; pin (d) T1/T2/T3 canonical pins + suppression pin both directions |
| R6 | WC-orphan detector false positives sweeping legitimately-paused shapes | medium | D2 surface posture (observe → fire, disposition before act); pure-WC-grandchildren variant pinned separately |
| R7 | Stale anchors (the files move fast; brief receipts already stale) | low | §5 moved-anchors table; directory-scoped grep / direct reads for oversized files; re-verify anchors at implementation start |

## 12. Out of scope (binding)

- **Interim-report typing** — OUT, FLAG-ONLY ("interim" appears ZERO times in the brief; the diagnosis fact — planner ended its turn on an interim '2/3 explorers reported' message typed as a completion_report — is flagged, not planned).
- Core-layering P1–P5 phase work (separate commissions; ordering constraint only).
- ANY polarity harmonization (naming the polarities is P3's business; changing them is a separate decision).
- Shortening ANY hold; changing deny bound 3 or withhold budget 8; D2.8 reordering.
- Refactoring the dedicated-session DEFERRED markers (`:3395-3422`, `:4076-4091`) to same-tx.
- "Fixing" the idempotency-guard fail-to-SKIP liveness count (`:3319-3351`) — deliberately opposite polarity, by design.
- Revive-on-send semantics changes (`instance_messaging.py:2432-2466`, `:2562-2580`, T1 `:3855-3889`).
- Anything in delivery semantics beyond the named seams; stale-docstring cleanup (`report_delivery_recovery.py:85/:139/:1372` — doc-only debt).
