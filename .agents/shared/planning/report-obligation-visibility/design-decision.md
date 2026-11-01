# report-obligation-visibility — Design Decision

**Commission:** wi-1fbee2ec · worktree `/home/nea/ensemble-src-wt-obligation` · branch `fix/report-obligation-visibility` · base `f7039c3ff` == `origin/latest` (clean)
**Provenance split:** the commission dispatch = design source for the five parts; the frozen core-layering brief (context-pointer: `/home/nea/ensemble-src/.agents/shared/planning/core-layering/` — MAIN checkout only copy, never copy-modified) = constraint source. The brief explicitly declares this commission's design OUT of scope (`architecture-recommendation.md:244`); it binds constraints only (polarity doctrine, HOLD-never-RELEASE, revive-on-send/holds invariants, ordering constraint, zero-"interim" scope gate). Companion: `plan-overview.md` (seam tables, polarity matrices, pins, sequencing — anchor authority).
**Anchor provenance:** all citations below verified at current tip `f7039c3ff` (2026-11-01) via worktree reads; base == tip, no drift.

---

## 1. Decision log (settled by commission + verified at tip)

### DEC-1 — Substrate = reuse `report_injections` (no new table)
The obligation IS a `report_injections` row (non-terminal `PENDING`/`DEFERRED`). The write-once obligation-triple index, the state machine, the claim primitives, and the RDRS lanes already exist and are load-bearing (`models.py:227-235`; migration `20260819_000001:114-120`; `transition_deferred_to_pending repository.py:1011`; `ensure_deferred repository.py:389`).
- Rejected: new obligations table — violates the commission ruling; duplicates machinery; forces a migration.
- Rejected: `dependency_watchers` as substrate — watcher rows model bus liveness, not delivery debt.

### DEC-2 — Same-tx mint riding the existing SAVEPOINT region (transactional outbox)
The mint attaches adjacent to the ReportInjection INSERT region `:4700-4751` (inside the `begin_nested()` SAVEPOINT) and is released before the outer commit `:5173` — obligation row persists **iff** the terminalization transaction persists. The outbox pattern ALREADY exists at this seam: the completion txn covers CAS `:4334-4350` + report msg `:4392-4407` + PROCESS_REPORT task `:4532-4543` + ReportInjection `:4700-4751` + events `:5136-5160`, one commit `:5173`.

**IMPLEMENTATION REALITY (2026-11-01, verified at implementation):** the natural-path ReportInjection INSERT *already mints* the obligation row same-transaction (the F3-era crash-consistency work) — every Part-1 invariant (i)–(iv) is already enforced by the CAS + the frozen guards + the write-once triple index. Part 1 therefore landed as: (a) the **provenance stamp** `deferred_reason=SYNC_MINT` (new `DEFERRED_REASON_SYNC_MINT` constant, `daemon/constants.py`; models.py docstring extended — zero DDL, the TEXT column is an open vocabulary by design) so a row's sync-vs-backfill origin is readable from the row itself; (b) the invariant documentation at the mint site (who-wins: first writer wins the triple index; the loser — either side — takes IntegrityError → SAVEPOINT rollback → skip; a later `ensure_deferred` touch may flip the reason in place — provenance best-effort under the race); (c) pins (b) asserting the pre-existing mint's same-tx semantics. NO second INSERT was added — a duplicate row would violate the write-once substrate the commission ruled to reuse.
- Rejected: post-commit mint — loses the atomicity guarantee (the pattern's whole point); reopens the orphan-row window.
- Rejected: dedicated-session mint — that shape is reserved for the two DEFERRED skip-path markers (`:3395-3422`, `:4076-4091`, deliberate best-effort semantics per 1d); NOT refactored here.
- Rejected: minting on the dead-parent branch (recipient None/TERMINATED) — that path's FAILED-message disposition is the designed T8 (a) semantics; out of scope.

### DEC-3 — Idempotency = CAS + frozen guard + write-once triple index; races benign
Three independent, already-frozen layers make the sync mint once-per-lifecycle:
1. **Child CAS** — `WHERE status NOT IN (paused, completed, error)`; `rowcount == 0` → rollback + skip (`:4351-4369`): re-entrant terminalization cannot re-drive the branch.
2. **Status-level guard** — terminal row → `idempotency_skip` return `:3378-3383`, BEFORE the mint region (T3 re-arm exception `:3352-3372` and PAUSED marker `:3384-3428` untouched); message-level `existing_report` by source `internal_report:{child}:{msg}` → skip `:4111-4126`.
3. **Write-once triple index** — partial unique on `(parent_instance_id, child_instance_id, child_message_id)` WHERE `state IN ('PENDING','DEFERRED')` (`models.py:227-235`; migration `:114-120`): the one race the guards cannot serialize (sync mint ∥ RDRS backfill tick) becomes benign — first writer wins; the loser takes IntegrityError → SAVEPOINT rollback `:4785` → discriminator `_is_obligation_triple_integrity_error :4786` → skip.
- Provenance: sync-vs-backfill distinguished via `deferred_reason` (open TEXT vocabulary `models.py:313-314`, doc `:181`) + the app-constant mirror convention (`daemon/constants.py`, C1 UPPERCASE `:308`) — **no schema change**.
- Noted (not closed): a re-owed cycle CAN mint a fresh row — the index covers PENDING/DEFERRED only (fresh-lifecycle carve-out `:214-219`); this is the substrate's designed semantics.

### DEC-4 — Part 2 = HOLD input at BOTH checkpoints, with TWO distinct postures
- **OUTSTANDING (> 0):** in-turn → hold the completion WITHOUT advancing `denied_count` (legitimate-pending family semantics per `:663-680` — the R2 non-reset IS the loop protection); emission-time → DENY leg joining the existing downgrade COMPLETED→WAITING_CHILDREN path `:5462-5486`.
- **QUERY-ERROR:** OPEN DECISION D1 below (recommended fail-closed DENY).
- Why bound-exempt on outstanding: the commission's own pin (a) GREEN — "hold persists past what would have been the 4th evaluation" — requires the outstanding posture NOT to advance `denied_count`; a bound-advancing arm would reproduce the 10:35 closure it exists to prevent (`deny_bound_exceeded :486-539`; TERMINAL_AFTER_BOUND `:925-931`; escalation `graph.py:6908-6918`).
- Untouched: all five existing arms, the attested-split `:881-891`/`:897-910`, deny bound 3, withhold budget 8 (`:588`), D2.8 LOCKED ordering, the fail-to-SKIP guard count `:3319-3351`.
- Rejected: single uniform posture (conflates "work genuinely pending" with "observation failed"); bound-advancing outstanding arm (reproduces the incident); treating the input as a plain R2 ALLOW-input (would RELAX the gate — forbidden by HOLD-never-RELEASE `arch-rec:177-179`).

### DEC-5 — Part 3 = pre-revival ancestor/root-terminal check at the RDRS revival seam ONLY
Check the recipient's ancestor/root terminal state BEFORE reviving (`_recover_one_deferred_row` step 2 `:1477-1493` → `_try_revive_terminal_parent :1541-1585`); suppress revival when the root is terminal. Suppression ≠ deletion — the row's disposition is OPEN DECISION D3.
- Composition, not opposition: T1 funnel-top reactivation (`instance_messaging.py:3855-3889`, LIVE since v0.28.2), the send_message carve-out (`:2432-2466` defer-forcing; `:2562-2567` terminal set; `:2568-2580` reactivation), and `_revive_terminal_instance` (`manager.py:10754-10867`) itself are ALL untouched — T1 keeps serving real message flow. The check narrows only the RDRS backfill path, where the recipient has NO live message driving it.
- Rejected: extending the check into `_revive_terminal_instance` or T1 (would fight live message flow; breaks the T1/T3 canonical pins); relaxing L2's `parent_not_terminal=True` (`:1717-1720`).

### DEC-6 — Part 4 = WC-orphan detector; surface = OPEN DECISION D2
Detects WC-instance-with-TERMINAL-parent plus the pure-WC-grandchildren sub-shape — the two shapes NO lane sees today (healer roots-only `:546-553`; RDRS L2 terminal-parent exclusion `:1717-1720` + `repository.py:1180-1186`; L5 existing-rows-only `:1146-1156`; ghost-signature abort `:650-662` + terminal-parent bound `:663-679`; `watch_reconcile_sweep.py:88-115` and `orphan_watcher_sweep.py:75-99` touch watcher/job rows, not WC status).
- Posture: observe → fire/disposition per the chosen surface's conventions, NEVER silent; wedge captures remain diagnostic-only.
- Rejected: widening the healer to non-roots (structurally hostile — see D2(b)); wedge-pass extension (diagnostic-only, no disposition machinery).

### DEC-7 — Part 5 = ANNOTATE-ONLY (planner-adjudicated disposition)
Both annotations recorded as ready-to-place text in §4 below; NO code scope; DROP if time-boxed. State in the plan: disposition explicit (done — plan-overview §2/§8).

## 2. How to read this set

`plan-overview.md` carries the EXECUTION content: scope, seam tables (§4, §5), the polarity matrices with per-leg assertions (§6 emission-time, §7 in-turn), per-part WHAT/invariant notes (§8), sequencing (§9), pins with mutation shapes (§10), risks (§11), out-of-scope (§12). This file carries the DECISION content: settled decisions (§1), the three open decisions with recommended defaults (§2), the pattern-names rider (§4), the part-5 annotation texts (§5), verification receipts (§6). An implementer starts at plan-overview §8 → §10; a reviewer gates against §6/§7 assertion columns FIRST, then the pins.

## 3. OPEN DECISIONS (recommendation-only; the plan proceeds on the recommended defaults unless the leader overrides)

### D1 — Obligation-leg polarity when the obligation QUERY ITSELF errors
**RESOLVED (leader adjudication 2026-11-01, BINDING): (ii) fail-closed.** Implemented: DENY → bound-composed degradation; the read's error path is an isolated try/except (never the whole-eval fail-open-ALLOW catch, never a −1 sentinel); mutation-pinned. The original analysis is retained below for the record.
The commission said "follow the brief"; the brief is SILENT on obligation-input error polarity — a genuine open decision.
- **(i) fail-open** — consistent with emission-time legs 1–2 (bus error returns 0 `:2636-2638`; unknown commission sentinel → inactive → proceed `:2690-2694`): a query error reads as zero → the gate proceeds.
- **(ii) fail-closed HOLD-until-bound** — consistent with the brief doctrine "the record may only HOLD, never RELEASE" (`arch-rec:177-179`), the leg-3 member-probe-error→HOLD precedent (`:2729-2735`), and the incident class itself (fail-open legs are exactly what allowed all-zero inputs to close the root). The arm contributes a DENY on query error, composing with the existing deny-bound-3 so a persistent error degrades to `terminal_after_bound` — never wedges forever; same degradation as any persistent deny.
- **RECOMMENDED DEFAULT: (ii) fail-closed.**
- **Structural requirement (either way):** the new arm's error path must be EXPLICITLY isolated from the in-turn whole-eval fail-open-ALLOW catch (`:1550-1636` / `:1638-1649`) and from the emission-time caller-wrap fail-open (`:5426-5451`, FP4-L5 `:5403-5425`) — under (ii) by converting the error to a HOLD/DENY verdict INSIDE the arm; under (i) by explicit propagation. Whichever posture is chosen gets a mutation pin.

### D2 — Part-4 WC-orphan detector firing surface
**RESOLVED (leader adjudication 2026-11-01, BINDING): (a) sweep-side detector** — implemented as a new RDRS-family lane (`wc_orphan_detector`), observability-only, wedge captures diagnostic-only. The original analysis is retained below for the record.
The commission said "follow the brief (wedge/sweep)"; the brief is silent.
- **(a) sweep-side detector** — a new RDRS-family lane or an adjacent periodic sweep. Composes with part 3; matches the PATTERN NOTES framing of RDRS as the level-triggered reconciler; the ORPHAN lane is already "the terminal-parent entry path" (`repository.py:1180-1186`), so a terminal-parent posture has an established home in that family.
- **(b) extend `mission_strand_healer` posture to non-roots** — structurally hostile: roots-only predicate (`:546-553`), ghost-signature fail-closed abort rejects WC members bearing execution evidence (`:650-662`), terminal-parent bound (`:663-679`).
- **(c) watchdog/wedge-pass extension** — the passes that exclude these shapes today (KB: waiting-children-strand-detection-gaps); wedge captures are diagnostic-only today.
- **RECOMMENDED DEFAULT: (a) sweep-side detector; wedge captures remain diagnostic.**

### D3 — part-3 sub-decision: disposition of a suppressed terminal obligation row
**RESOLVED (leader adjudication 2026-11-01, BINDING): (α) hold DEFERRED + observability surface** — implemented as the `suppressed_root_terminal` LaneResult counter + structured WARNING log; no FAILED dead-letter. The original analysis is retained below for the record.
When revival is suppressed because the root is terminal and the obligation can never deliver in-session:
- **(α) hold DEFERRED as durable convergence record + surface in observability** (sweep result counters + structured log per lane conventions) — the level-triggered reconciler keeps the row as a convergence record; it remains queryable and re-examinable.
- **(β) dead-letter to FAILED** — FAILED is the dead-letter SENTINEL (`models.py:83-122`, T8 dead-parent seam); using it here conflates "parent is dead, delivery impossible" (T8 semantics) with "root closed, nothing to converge toward".
- **RECOMMENDED DEFAULT: (α) hold DEFERRED + observability surface. Do not conflate sentinel semantics.** (recommendation-only)

## 4. PATTERN NOTES (user-directed doc rider — reproduced per commission §6)

- **Parts 1+2 = TRANSACTIONAL OUTBOX pattern.** Delivery intent is minted atomically with the event, in the SAME transaction as child terminalization. The obligation row's existence is guaranteed the instant the terminalization is guaranteed — no detection lag, no loss window.
- **RDRS = LEVEL-TRIGGERED RECONCILER backstop.** The edge path now writes durable intent; the reconciler demotes from source-of-truth to convergence guarantee. It still sweeps (pre-change rows, crash windows, the dedicated-session markers), but correctness no longer DEPENDS on its 300s tick.
- **The completion gate = checkpoint aggregate with deliberately MIXED failure polarities** (fail-OPEN legs 1–2 + caller wrap; split-polarity leg 3; resolver-decides leg 5; fail-CLOSED same-tx writer legs). **This commission EXTENDS the aggregate WITHOUT harmonizing polarities** — each existing leg keeps its verdict and polarity byte-for-byte (`sequencing-detail.md:71-78`; `risk-register.md` R3); the new obligation leg carries its own declared posture (hold-on-outstanding; error posture per D1).
- Purpose: the next reader sees the pattern names instead of re-deriving them mid-incident.

## 3a. MID-FLIGHT LEADER ADJUDICATION (2026-11-01, ~08:4xZ — BINDING; folded into the in-flight part-2 work)

**DISPATCH-WINDOW NOTE (live receipt, v0.28.3 dogfood):** leader row `7ceb4685` read COMPLETED ~08:25Z 2026-11-01 while child developer `fea6f7be` was LIVE (waiting_children, own children running); planner+giter terminal; `completion_gate_escalated=FALSE` — the in-turn gate verified-clean on an all-quiet snapshot. Likeliest shape (leader diagnosis): the gate sampled during the **DISPATCH→RUNNING window** — the child was MINTED but not yet RUNNING when `live_descendants`/`outstanding_commissions` were counted → all-zero → verified-clean. The live v0.28.2 T2 WC re-arm does NOT cover this micro-race (the parent turn ended before the child flipped RUNNING).

**Adjudication dispositions (implemented):**

1. **Liveness input-set widening (a deliberate leg-extension, same doctrine as the obligation leg).** Verified at implementation: the EMISSION-time liveness leg (`evaluate_mission_live` leg b/c) ALREADY counts any non-terminal descendant as live (IDLE canonicalized to live; only the aged strand exception — which requires a TERMINAL direct parent, not this window — drops out) → documented + pinned, no change needed. The IN-TURN arm (`count_live_descendants`) did NOT — dormant IDLE/QUEUED were conditional on message/job evidence, leaving the micro-race → **WIDENED**: any NON-TERMINAL descendant now counts as NON-QUIET (`_count_descendants_busy_and_live`; the b08f40fe conditional-dormancy check retired for the LIVE count — an aged never-dispatched IDLE orphan now reads non-quiet to the in-turn arm; reaping belongs to the strand healer + the new WC-orphan detector lane). The BUSY subset (LCA trigger suppression) keeps the strict two-set semantics — UNCHANGED.
2. **New mutation pin (e)** — the dogfood window: child minted, NOT yet RUNNING, tree otherwise quiet → OLD verify-clean, NEW must NOT verify-clean (both checkpoints' liveness surfaces). Red-green per house rule.
3. **Scope guard honored:** part 1's mint seam UNMOVED — obligation rows stay minted at CHILD TERMINALIZATION. The dispatch-spanning alternative ("obligation/commission row from DISPATCH" — an outbox spanning the whole round trip) is a **DOCUMENTED CONSIDERATION ONLY** (below), NOT implemented.
4. **Orthogonality:** the 10:35 mutation shape (terminal children + undelivered reports → the OBLIGATION leg) and the dogfood window (non-terminal children pre-RUNNING → the LIVENESS leg) are two different windows on two different legs — pins (a) and (e) each prove their OWN window.

**Documented future consideration (NOT implemented):** a dispatch-spanning outbox — minting the delivery obligation (or a commission-row sibling) at DISPATCH time so the round trip (dispatch → execution → report) is covered end-to-end by durable intent, rather than only from child terminalization onward. This would structurally subsume the liveness-leg widening (the obligation leg would cover the dispatch window too), but changes the write path for EVERY spawn (write amplification + a new retraction semantics for never-completed children). Cited incident: the 2026-11-01 dogfood window above. Deferred to a future commission; this lane's mint stays at child terminalization per the commission text.

**Updated matrices (superseding the plan §6/§7 rows they amend; all other rows unchanged):**

| Gate | Leg/arm | Amendment |
|---|---|---|
| emission-time (§6) | leg 3 tree liveness | UNCHANGED in code — verified as ALREADY counting minted-not-yet-RUNNING descendants (leg b/c canonicalizes IDLE → live; the strand exception requires a TERMINAL direct parent + age, orthogonal to the dispatch window). Pin (e) emission-side asserts the window. |
| emission-time (§6) | **leg 6 obligations (NEW)** | implemented as planned: count > 0 → DENY (reason carries the count) joining the existing downgrade path; D1-ii fail-closed — the leg catches its OWN query error INSIDE the leg and converts to a HOLD verdict, never riding the caller-wrap fail-open. |
| in-turn (§7) | live_descendants arm | **INPUT-SET WIDENED** (adjudication #1): any NON-TERMINAL descendant = NON-QUIET (dormant IDLE/QUEUED unconditional). Posture stays ALLOW-leg; the −1 sentinel contract, the busy subset, and every other arm unchanged. This row supersedes the plan §7 "two-set … unchanged" assertion for THIS arm only. |
| in-turn (§7) | **outstanding_report_obligations arm (NEW)** | implemented as planned + leader-endorsed: > 0 → ALLOWED_LEGITIMATE_PENDING_WAKEUP hold, counter UNCHANGED (bound-exempt — holds at/past the bound while an obligation is outstanding); D1 fail-closed error flag → decide() step (2b): bound-composed DENY (DENIED + increment + nudge below the bound; TERMINAL_AFTER_BOUND at/past it), isolated from the whole-eval fail-open-ALLOW catch and from the −1 sentinel short-circuit. |

## 5. Part-5 annotation texts (annotate-only; ready to place; DROP if time-boxed)

**SITES LOCATED AT IMPLEMENTATION (2026-11-01, this commission):**

* **(i)** the `epochs[]` read-model construction site = `daemon/tools/missions.py:326-342` (the best-effort single-element `epoch_entry` construction inside the mission-detail renderer; the array is returned at `:349`). The renderer derives the entry from `record.started_at` / `record.last_activity_at` — a PROJECTION of the mission-record read, exactly the twin shape the annotation names. NO code was touched (annotate-only disposition).
* **(ii)** the report-delivery re-cycle path = the two exactly-once claim seams on `ReportInjectionRepository` — `claim_for_injection` (`daemon/repositories/report_injection/repository.py:1509`, driven by the graph's `ReportInjectionSlot` drain at `daemon/graph.py:680/737`) and `claim_for_task_delivery` (`:1798`, the PROCESS_REPORT fallback). A re-cycle delivery lands through one of these two claims OUTSIDE the revive-on-send reset trigger (`instance_messaging.py:2568-2580`) and outside `safe_set_escalated_and_reset` (`graph.py:6908-6918`) — the two ledger-clearing sites the annotation names. NO code was touched.

**(i) `epochs[]` read-model µs-twin annotation** — place at the `epochs[]` read-model construction site (implementer locates the exact site during implementation; low-priority):
> NOTE (µs-twin): `epochs[]` is a read-model twin of the epoch state derived from the underlying mission-epoch records — it can diverge from the source at µs granularity (stale twin within a tick). It is a PROJECTION, not a second authority: readers needing the authoritative epoch state must read the epoch records, not this array. Do not add writers to this shape without reconciling the twin.

**(ii) Report-delivery re-cycles bypass the attestation-ledger reset** — place at the report-delivery re-cycle path (a descendant report delivered mid-episode or after revival, outside the revive-on-send reset trigger):
> NOTE (ledger bypass): a report-delivery RE-CYCLE does NOT reset the attestation ledger — `completion_gate_escalated` and `attestation_denied_count` are cleared only by the revive-on-send reset trigger (3) at `instance_messaging.py:2568-2580` and by `safe_set_escalated_and_reset` (`graph.py:6908-6918`). A re-cycled delivery therefore arrives at a parent whose ledger may still carry a prior episode's state; the deny count is NOT a per-delivery counter. Recorded as observed behavior, not a defect.

## 6. Verification receipts (what this plan verified at the tip, 2026-11-01)

Every anchor cited in plan-overview §4–§7 and here was read at `f7039c3ff` in the worktree: the five brief constraint sites (`arch-rec:121-134/:177-179/:236-246`, `sequencing:71-78/:90`, `risk-register:19/:35-40`, zero "interim" matches); part-1 seams (`:2792/:2823-2834/:3180/:3240-3428/:3378-3383/:3979/:4185/:4334-4369/:4700-4751/:4785-4790/:4835-4852/:5173`); part-2 gates (`decide() :640-939`, reads `:1483-1549`, `:1550-1636`, `:486-539`, `:925-931`, `:588`, `graph.py:5932/6602-6703/6908-6918/6948-6984`; `_root_completion_gate :2574-2790` legs 1–5, caller `:5403-5451`, downgrade `:5462-5486`; D2.8 comments `:1226/:3589/:3836`; sync-helper legs `:3444-3451/:3480-3493/:3560-3567/:3653-3660`); part-3 seams (`:1001-1100/:1146-1150/:1474-1493/:1541-1548/:1717-1720/:2016/constants.py:827`; T1 `:3855-3870`; carve-out `:2432-2440`; set+reactivation `:2562-2582`; `manager.py:10754`); part-4/5 seams (healer `:546-554/:597-608/:650-662/:663-672/:84-90`; sweeps `:88-100/:75-85`; `_shielded_marker :1029-1034`; migration `:110-120/:140-146`; index `models.py:214-235`; enum `:83-95/:306-310`; `deferred_reason :313`; repo primitives `:1011/:1109/:2052/:389`; `repository.py:1176-1190`). Discrepancy check: `report_delivery_recovery.py:85/:139/:1372` still carry stale `:2773-2787` refs (doc-only debt, out of scope).

## 7. Out of scope (restated)

Interim-report typing (FLAG-ONLY — "interim" appears ZERO times in the brief; the diagnosis fact is flagged, not planned); core-layering P1–P5 phase work (ordering constraint only); ANY polarity harmonization; shortening ANY hold; changing deny bound 3 or withhold budget 8; D2.8 reordering; refactoring the dedicated-session DEFERRED markers (`:3395-3422`, `:4076-4091`) to same-tx; "fixing" the idempotency-guard fail-to-SKIP liveness count (`:3319-3351`); revive-on-send semantics changes (`:2432-2466`, `:2562-2580`, T1 `:3855-3889`); delivery semantics beyond the named seams; stale-docstring cleanup (`report_delivery_recovery.py:85/:139/:1372`).

## 8. Migration note

**No migration expected.** `report_injections` is reused as-is (DEC-1); sync-vs-backfill provenance rides the `deferred_reason` open TEXT vocabulary + the `daemon/constants.py` app-constant mirror (C1 UPPERCASE contract) — zero DDL. IF the implementation ends up adding any table or column, a matching `daemon/migrations/versions/*.sql` file is ABSOLUTE (the file is the persistent record; the runner is SQLite-only; "every new SQLModel table requires its own `*.sql` file").
