# B-Arc Closure Table — Completion-Subsystem Proposal B (PR1 → PR3)

**The ARC-level verdict view.** The three adoption-delta notes are the
IMPLEMENTATION TRUTH; this file is the CLOSURE VERDICT per defect, per the
design's own §3 per-defect table (proposal-b) and the architecture map. It
does not restate implementation prose beyond what a verdict row needs.

**Branch:** `feature/mission-records-pr3` — range `013895287..` the branch's final pre-merge HEAD
(base `013895287`). The counts below are measured **at the branch's final
pre-merge HEAD**: 45 commits, 50 files, +10273/−241. The `daemon/` code
anchors remain pinned at `2453e271c` — `git diff 2453e271c..HEAD -- daemon/`
is EMPTY (the last `daemon/`-touching commit in the range is `7c9dfb47d`);
every commit after `2453e271c` is tests/docs/tooling only. (Later commits
on the branch change the totals — cite the range, not a self-referential
count.)

**Rule for every row below:** a defect counts as CLOSED only if the defect AS
CATALOGUED is closed, not merely made non-load-bearing. A row that says
"closed as a consequence" says so explicitly. A row that says "deferred" is
NOT closed.

**Sibling docs (the implementation truth — read these for the WHY):**
`.agents/shared/planning/completion-subsystem-mission-records-pr1/`,
`…-pr2/`, `…-pr3/adoption-delta-notes.md`.

**Reference-only design docs** (UNTRACKED in the MAIN checkout,
`.agents/shared/planning/completion-subsystem/`): `proposal-b-mission-record-anchor.md`
(§3 = the per-defect table this file closes out), `architecture-map.md`,
`reconciliation-sweep-design.md`, `comparison.md`.

---

## (a) The per-defect closure table (D1–D5, NEW(i)–NEW(v))

### (a.1) The five catalogued defects

| Defect | Verdict | Where it is NOW provably closed | Evidence (commit / test) |
|---|---|---|---|
| **D1** — reports route to the structural parent, not the commissioner | ✅ **CLOSED** | The routing authority moved from the A-resolver to the `mission_commissions` lookup, taught INSIDE the resolver (`resolve_report_recipient` gained the commission-first leg internally — the design's "teach the RESOLUTION, not the downstream guards"). Three legs: **LEG 1** (enforce-gated) the child's OLDEST outstanding commission row → `commissioner_instance_id`, advisory backfill rows excluded FOREVER; **LEG 2** the A-resolver audit-recency heuristic — kept byte-identical, **demoted to the declared LEGACY FALLBACK** (renamed `_resolve_legacy_audit_recipient`); **LEG 3** the structural parent. All three production call sites (the `child_reports` producer, the pipeline Site-1 marker, RDRS lane 2) inherit the flip with ZERO call-site churn. The `report_injections` provenance writers (mission_id + commissioner_instance_id) land at the natural completion write path. **The C3 duplication firewall landed in the SAME commit** (the architecture map §7.4's same-PR mandate — the no-row backstop's admission predicate is child-keyed, so a mission-routed delivery already counts as delivered). | `cbe88401b` · `tests/unit/test_mission_record_pr3_routing_flip.py` — `TestTheRoutingFlip` (7 nodes: the enforce flip, the dry byte-identical leg, advisory never routes, ruling ④ tie-break, satisfied does not route, leg-1 fail-open to leg 2, the audit leg intact under enforce) + `TestC3CrossParentFirewall` (2 nodes: delivered-not-readmitted, undelivered-still-admitted) |
| **D2** — premature settle (tree-scoped R2 counters + send_message-only census) | ✅ **CLOSED** (PR2 read flip) **+ hardened in PR3** | PR2 shipped the three legs: the **census union** (the record's answer UNION the legacy delegation scan — the record's contribution is ADDITIVE, it can only arm the gate, never disarm it), the **5th R2 input** `outstanding_commissions` (mission-scoped, fail-open `-1` sentinel — never a 0 default; quiet requires `== 0`), and the **L3 mission leg** (the publish is held while the mission has outstanding commissions). PR3 hardened the count to be **ANCHOR-scoped across the anchor's OPEN and SUPERSEDED epochs** per MAJOR-1 ruling (c) — so a commission orphaned by the `job_continue` supersession still holds the publish. | PR2: `tests/unit/test_mission_record_pr2_gate_reads.py` (`TestCensusFlip`, `TestFifthR2Input`, `TestSourceCCommissionUnion`, `TestQuietPredicate`) + `tests/unit/test_mission_record_pr2_l3_ledger.py` (`TestL3MissionLeg`, `TestEpochBoundaryLedgerClear`) · PR3: `a3e033984` · `tests/unit/test_mission_record_pr3_cross_epoch_hold.py::test_the_orphaned_commission_on_a_superseded_epoch_still_holds_the_publish` · live enforce-e2e proof: the PR2 runtime gate (`.agents/tester/RESULTS/2026-10-25-mission-records-pr2-runtime-gate.md`) item 3 — the L3 hold fired with `reason=mission_commissions=1` + the R2 `outstanding_commissions` 0→1→0 transition + the census arm |
| **D3** — no unique-prefix instance-id resolution (a phantom id still 404s) | ⛔ **DEFERRED BY DESIGN — NOT CLOSED** (orthogonal to the record) | The record does not fix id resolution. B reduced the blast ONLY: nudge-path reports route via commission rows keyed on FULL ids resolved at mint time, so the composite-source-token LIKE-fusion seam loses its role there. The unique-prefix-or-raise lane is a self-contained patch in another architect's seam territory; proposal-b §9 assumes it lands separately. **It has not.** A phantom id can still be synthesized on the legacy path. | proposal-b §3 D3 + §9 assumption 1 · the reduction is the D1 routing flip's leg-1 (`cbe88401b`) |
| **D4(i)** — only 4 prefixes stamped internal | 🟡 **PARTIAL — the anchor corruption is closed; the stamp widening is DEFERRED** | The catalogued defect ("only 4 prefixes stamped internal") is NOT directly fixed. What IS closed is the load-bearing consequence: SOURCE U became a record lookup, so the reserved-but-unstamped lanes and the `instance_ops:` no-man's-land stopped being load-bearing for anchor correctness. The `_INTERNAL_STAMPED_SOURCE_PREFIXES` widening and the `system:`→HUMAN else-branch fix remain defense-in-depth + user-origin-window hygiene, deferred to the seam-hardening track. | PR2 (`SOURCE U` = record anchor; the six-step ladder survives as the declared legacy fallback) — `tests/unit/test_mission_record_pr2_gate_reads.py::TestAnchorFlip` |
| **D4(ii)** — spawn read-back WARNING-only | 🟡 **PARTIAL — mint-path validation SHIPPED; general hardening DEFERRED** | The mint lanes validate their own writes: a commission row whose worker target row is missing at mint time is an immediate RAISE (the mint is the semantic contract — a commission that can never be satisfied must not be recorded as outstanding). The general enqueue read-back/masking hardening for NON-mission enqueues is seam territory — deferred. | PR1 — `tests/unit/test_mission_record_service.py::TestMintPathValidation` |
| **D4(iii)** — enqueue masking (mis-addressed row invisible to the sender) | 🟡 **PARTIAL — the FIX is DEFERRED; the OBSERVABILITY SHIPPED** | The seam patch itself is still deferred. What shipped is the observability half: RDRS lane 7 detects a whole-window queue-row absence past the grace window and counts it as `masked_suspected` with a WARNING line. **Counter + log ONLY** — never an auto-action, never an abandonment on this signal alone. | `13aa51e1a` · `tests/unit/test_mission_reconciliation_sweep.py::TestTheMaskedEnqueueObservability` (3 nodes) |
| **D4(iv)** — `send_message` result.status swallowed | ⛔ **DEFERRED — NOT CLOSED** | A pure seam patch; the commission ledger adds an audit surface but the tool-result surfacing is independent. | proposal-b §3 D4(iv) |
| **D5-latent** — stale anchoring on `internal_agent:*` lanes; `denial_epochs` never cleared; the ~14-minute anchor lag | ✅ **CLOSED** | Epoch semantics are the core construction: the anchor is a durable row minted at enqueue and **amended AT DELIVERY** (ruling ③) — no would-be-END window re-derivation, no lag seam. The `denial_epochs` ledger is epoch-tagged and the **epoch-boundary clear is armed in `enforce`** (it is a gate-visible behaviour change, so it rides the same operator promotion). The F2 amend guard was hardened in PR3 into ONE shared single-statement guarded UPDATE (the status predicate lives in the WHERE, the outcome is decided by the ROWCOUNT, no prior read) — the PR2 flake fix. | PR1 (the records/mints/stamps) + PR2 (amend-at-delivery; the boundary clear; the epoch tag) — `tests/unit/test_mission_record_pr2_tap_amend.py`, `tests/unit/test_mission_record_pr2_l3_ledger.py::TestEpochBoundaryLedgerClear` / `::TestEpochTaggedDenyLedger`, `tests/unit/test_mission_record_pr2_carries.py::TestF2AmendGuard` · PR3 hardening: `0bff3a4dd` + `tests/unit/test_mission_record_f2_concurrency.py` (**MEASURED: 6 nodes, 6 passed** — `uv run python -m pytest tests/unit/test_mission_record_f2_concurrency.py -q` → `6 passed in 0.70s`; see §(d.2.1) for the correction of the unsubstantiated "12/12" figure) |

### (a.2) The five NEW rows

| Defect | Verdict | Where it is NOW provably closed | Evidence |
|---|---|---|---|
| **NEW(i)** — `attest_completion` is a static no-op | ⛔ **REMOVED BY RULING ① — NOT IMPLEMENTED** | The binding user ruling ① removed the Term-1 bypass-recording item from the flip list: the attested arm stays a no-op/bypass BY DESIGN (no bypass-recording, no blocking rule). The proposal-b §3 NEW(i) text is SUPERSEDED by the ruling. Nothing was built; the PR2 runtime gate proved the arm untouched at arm-block sha256 level (3 blocks byte-identical base == head; zero `bypass` diff lines; zero `attestation_bypassed=True` call sites; zero callers of `record_attestation`). | PR1 notes §(a) ruling ① · PR2 notes §(a) item 9 ("NO — REMOVED (ruling ①)") · PR2 runtime gate item 6 |
| **NEW(ii)** — the judge is skipped when the leader attests / `attestation_required=False` (Term-1) | ⛔ **SUPERSEDED BY RULING ① — NOT IMPLEMENTED** | Same ruling: the bypass-recording text AND the quiet-mission blocking POLICY are both superseded. The attested arm stays a bypass; the quiet-mission blocking policy does not exist. | PR1 notes §(a) ruling ① · PR2 notes §(a) item 9 · PR2 runtime gate item 6 |
| **NEW(iii)** — no epoch semantics anywhere | ✅ **CLOSED — core construction** | The mission record IS the epoch: one `mission_records` row per epoch (the M4(ii) `mission_events` cure, named 9× and never built, ships as per-epoch rows with real timestamps rather than a third event table). Epoch transitions are guarded (`open → superseded` record-side only; the continuation mint supersedes BEFORE inserting — F1's ordering constraint). | PR1 (the substrate) · `tests/unit/test_mission_record_pr2_carries.py::TestF1OpenEpochUniqueness` (`test_the_continuation_mint_supersedes_before_inserting`) |
| **NEW(iv)** — SOURCE U is a heuristic, not a mission record | ✅ **CLOSED — core construction** | SOURCE U became a LOOKUP: the anchor is the epoch's `founding_message_id`; the delegation window starts at the founding/amendment message. The six-step exclusion ladder SURVIVES as the declared legacy fallback (a record miss ⇒ today's behaviour, exactly) — the migration seam, not a second opinion. | PR2 — `tests/unit/test_mission_record_pr2_gate_reads.py::TestAnchorFlip` (`test_source_u_is_the_record_anchor`, `test_the_legacy_ladder_is_the_fallback`) · `tests/unit/test_mission_record_reads.py::TestAnchorRead` |
| **NEW(v)** — SOURCE C subordination reads the same structural tree | ✅ **CLOSED — core construction** | C's membership = structural tree ∪ commission membership, so the out-of-tree commissioned worker finally appears; the subordination clause itself is preserved UNCHANGED. The tree half was restored in a later PR2 round. | PR2 — `tests/unit/test_mission_record_pr2_gate_reads.py::TestSourceCCommissionUnion` (`test_the_out_of_tree_commission_is_visible_in_c`, `test_a_union_read_failure_is_logged_and_fails_open`) · tree-half restore: `d9e8fbdd2` |

### (a.3) Tally against the design's own §3 tally

The proposal-b §3 tally read: *9 fixed (7 as consequences of the record, 2 with
named seam patches in the same PR), 4 explicitly deferred (D3, D4
stamp-widening, D4(iii), D4(iv)), 1 partial (D4(ii) mint-validation in scope).*

Against the PR3 tree, re-derived row by row:

| Bucket | Rows |
|---|---|
| **CLOSED** | D1, D2, D5-latent, NEW(iii), NEW(iv), NEW(v) = **6** |
| **PARTIAL** (half closed) | D4(i) (anchor corruption closed, stamp widening deferred), D4(ii) (mint-path validation shipped, general hardening deferred), D4(iii) (observability shipped, fix deferred) = **3** |
| **DEFERRED / NOT CLOSED** | D3, D4(iv) = **2** |
| **REMOVED OR SUPERSEDED BY RULING (never built)** | NEW(i), NEW(ii) = **2** |

**The two tallies differ and the difference is meaningful.** The design's "9
fixed" counted D4(i) as fixed (anchor corruption) and counted NEW(i) and
NEW(ii) as fixed — but both NEW rows were subsequently REMOVED/SUPERSEDED by
binding user rulings ① (never built), and D4(i) is only half closed. The
comparison doc already flagged this asymmetry: *"B's '9 fixed' is therefore 8
fixed + 1 partial under like-for-like scoring"* — and the rulings moved it
further. **6 closed / 3 partial / 2 deferred / 2 removed-by-ruling** is the
honest PR3-tree figure.

---

## (b) MAJOR-1 — the epoch-supersession orphan commissions

**Ruled: (c) CROSS-EPOCH HOLD** (a binding user ruling, delivered in PR3).

The `job_continue` continuation supersedes the anchor's prior OPEN epoch; the
prior epoch's commission rows are ORPHANED (they keep `state='outstanding'`
and keep pointing at the superseded epoch). Ruling (c): **NO re-pointing, NO
auto-supersede — attribution stays with the owning epoch.** The L3 publish
hold and the R2 quiet predicate release only when EVERY epoch of the anchor is
commission-quiet.

| Closure element | Evidence |
|---|---|
| The ANCHOR-scoped read — `count_outstanding_commissions_for_anchor` (joins `mission_records` × `mission_commissions`; counts `state='outstanding'` across the anchor's OPEN **and** SUPERSEDED epochs; advisory rows excluded by the one consistent predicate) | `a3e033984` · `daemon/repositories/mission_record/repository.py` |
| The read consumer — `mission_record_reads.count_outstanding_commissions` flips to the anchor-scoped read; the fail-open `-1` sentinel contract is preserved EXACTLY (never a 0 default); "anchor has no epochs at all" stays a real `0`; "no open epoch" is NO LONGER automatically quiet | `a3e033984` |
| **The orphan-hold test** (the mandatory pin — built so the L3 gate is actually REACHED, per the PR2 runtime gate's caveat (e): a live non-terminal descendant is seeded and the hold reason names `mission_commissions=1`, proving the mission leg fired before the liveness leg could defer) | `tests/unit/test_mission_record_pr3_cross_epoch_hold.py::test_the_orphaned_commission_on_a_superseded_epoch_still_holds_the_publish` (+ the release-on-satisfy, the R2 cross-epoch non-quiet, the advisory exclusion, the `-1` sentinel, the real-zero, and the census-scope pins — 7 nodes) |
| **The retirement path** — the sweep is the only thing that retires an orphan the delivery path cannot reach | `13aa51e1a` (RDRS lane 7) |
| Attribution never re-points; `supersede_commission` stays ZERO-CALLERS (grep-verified at the last content commit `2453e271c`: no call site anywhere in `daemon/`) | `daemon/services/mission_record_service.py` |
| The census scope ruling — the attestation CENSUS deliberately STAYS open-epoch-scoped; the ruling text names the L3 hold + the R2 quiet input only | `tests/unit/test_mission_record_pr3_cross_epoch_hold.py::test_the_census_stays_open_epoch_scoped` |

**The deliberate cost, stated plainly:** the eternal-mission class. An orphan
that nothing will ever satisfy holds forever — the escape valve is the
reconciliation sweep + its TTL backstop. This is the accepted price of
attribution honesty (ruling (c)), and it is the reason the sweep had to land
before the enforce promotion.

---

## (c) The original incident chain (the `fd302262` shape) → its closures

The incident was re-investigated and its PREMISE OVERTURNED. This chain is
what actually happened and where each link now stands.

| # | Link in the chain | Status | Closure |
|---|---|---|---|
| 1 | **The premise** — "an announced-but-never-spawned worker". **OVERTURNED: all the workers were real.** The phantom was in the RECORD of what happened, not in what happened. | ✅ premise closed | The investigation (read-only, no code) found three real defects underneath the phantom |
| 2 | **D1 — the `instance_nudge` report routed to the STRUCTURAL PARENT, not the nudger** (the audit ledger had ZERO readers) | ✅ **CLOSED** | The routing flip: the commission-first leg inside the resolver, with the A-resolver demoted to the declared legacy fallback. `cbe88401b` + `tests/unit/test_mission_record_pr3_routing_flip.py` |
| 3 | **D2 — the completion gate was blind to OUT-OF-TREE commissioned work** (the mission settled ~5 minutes early while a commissioned worker was still running) | ✅ **CLOSED** | The quiet + L3 legs (PR2), then hardened to the anchor-scoped count (PR3). `tests/unit/test_mission_record_pr2_l3_ledger.py::TestL3MissionLeg` + `tests/unit/test_mission_record_pr3_cross_epoch_hold.py` |
| 4 | **D3 — the phantom-id LIKE-fusion seam** (an 8-char prefix fused with another instance's UUID suffix produced an id that 404s) | ⛔ **DEFERRED BY DESIGN — reduced blast, NOT closed** | The record's routing keys on FULL ids resolved at mint time, so the nudge path loses the LIKE-fusion seam. The unique-prefix-or-raise lane itself is another architect's seam and has NOT landed. |

**End state of the chain:** links 2 and 3 are closed and provably so; link 4 is
deferred with the blast reduced. The incident's *observable* symptom (a
mission that settles before its commissioned work finishes) is closed by D2 —
and, thanks to the terminator + the sweep, the closed state has an escape
valve so it cannot become an eternal mission.

---

## (d) Open BY DESIGN (the kept-open list)

### (d.1) The binding user rulings

| Ruling | Disposition | Where |
|---|---|---|
| **①** Term-1 bypass-recording + the quiet-mission blocking policy | **REMOVED** — the attested arm stays a no-op/bypass BY DESIGN. No bypass-recording, no blocking rule. NEW(i) and NEW(ii) are superseded by the ruling and were never built. | PR1 notes §(a) · PR2 notes §(a) item 9 · PR2 runtime gate item 6 (sha256 proof the arm is untouched) |
| **②** Scheduler anchoring | **UNCHANGED — the scheduler mint hook stays INERT.** `ENSEMBLE_MISSION_RECORDS_SCHEDULER_MINT` stays default-OFF (the mode ladder alone does not arm it — both gates must open). Lane 9 stays bare. | PR1 notes §(a) item 6 · PR2 notes §(a) item 10 · PR2 runtime gate item 6 |
| **③** The amendment fires AT DELIVERY | **SHIPPED** (ruling ③) — the re-founding amendment fires on EVERY DELIVERY (it is DECOUPLED from the message-tap site, so it does not depend on the tap's side table), not when the window is scanned. Kills the ~14-minute mid-turn correction lag seam. | PR2 notes §(a) item 7 (AMENDED) · `tests/unit/test_mission_record_pr2_tap_amend.py` · the PR2 runtime gate's lag-seam item (11/11, with the real unmocked seam + the guarded UPDATE in SQL + the delivery-not-scan structural proof) |
| **④** Multi-commissioner tie-break | **THE OLDEST-OUTSTANDING DISCRIMINATOR STANDS** — the routing leg takes the OLDEST outstanding row (`created_at ASC`), the same deterministic order the rung-2 resolution always used, now carrying ROUTING authority. | PR3 notes §(b) · `tests/unit/test_mission_record_pr3_routing_flip.py::TestTheRoutingFlip::test_multi_commissioner_takes_the_oldest_outstanding` |
| **⑤** The TTL | **A KNOB, NOT A DECISION — no hard-coded decision was taken.** The knob is `ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS` with the house default 21600 s (6 h). The DEFAULT is a starting point, not a decision: the `dry`-mode counters are the evidence that tunes it. | PR3 notes §(h) + §(j) · `tests/unit/test_mission_reconciliation_sweep.py::TestTheTtlKnob` (5 nodes: the 6-hour house default, the env override, the invalid-value fallback that never raises, the invalid value that does not poison the other defaults, the config default when the env knob is absent) |

### (d.2) The deferrals and the residual list

| Item | Status |
|---|---|
| **D3** — the unique-prefix-or-raise lane | Deferred-by-design (orthogonal); the record reduces the blast only |
| **D4 stamp-widening** (`_INTERNAL_STAMPED_SOURCE_PREFIXES` + the `system:`→HUMAN else-branch) | Deferred — defense-in-depth + user-origin-window hygiene |
| **D4(iii) — the enqueue-masking FIX** | Deferred (the observability shipped as `masked_suspected`) |
| **D4(iv) — `send_message` result.status surfacing** | Deferred (a pure seam patch) |
| **OQ-6 — the `(state, created_at)` composite index** | DEFERRED with a perf note: at the expected cardinality (the outstanding set is tiny) the state-only filter suffices |
| **OQ-7 — the commission row's ``work_id`` is never stamped (the JobItem-live leg is INERT)** | **OPEN — IMPLEMENTED + WIRED, UNREACHABLE.** The lane-7 JobItem-live probe reads ``mission_commissions.work_id``, but NO mint lane stamps it: the ``send_message`` lane (``daemon/tools/instance.py``) and the ``instance_nudge`` lane (``daemon/tools/instance_ops.py``) mint commissions WITHOUT a work id, and the only job-driven lane (``job_continue``, ``daemon/tools/job_queue.py``) attaches the work id to the EPOCH row (``mission_records.work_id``) and mints NO commission row. Every production commission therefore takes the "no linkage → not live" branch — the leg is dead code in production. **FAIL-SAFE (why this is not a live defect):** the worker-side ``instance_repo.has_live_work`` leg covers the JobItem-queued-on-the-worker case, so a live worker is never abandoned at any age. **FOLLOW-UP:** stamp the linkage at a mint lane (deliberately NOT invented in PR3 — the sweep's job-item lookup shape is not settled). The probe's own tests (``tests/unit/test_mission_reconciliation_sweep.py::TestTheJobItemLiveCheck``) build the row BY HAND and prove the code path, NOT the wiring. |
| **OQ-3(a) — the `enqueue_message_id` column** | NOT RULED IN — the nullable column (precise D4(iii) matching) was never adopted; `resolved_at` (OQ-3(b)) WAS, `provenance` (OQ-3(c)) went to `mission_records` only |
| **Test-scaffolding consolidation** | RE-DEFERRED — there is no house helper to consolidate onto (`tests/helpers/` carries no mission-record fixture) |
| **The pre-existing environment hangs + the A/B-proven base reds** | Not branch-caused — the tester lane's standing quarantine rows + the A/B-parity proof (the PR2 full-pool A/B measured zero branch-caused reds) |
| **The "four vs five tap sites" prose** | PRE-EXISTING, outside the PR2 surface — `daemon/services/message_tap.py`'s module docstring says "the four approved tap sites" while the module carries FIVE source labels (the fifth, `compaction_precall_95`, was added by the proactive-compaction fix). A separate hygiene pass, not this arc |
| **F2 (Batch A) — the delivery-evidence legs 2/3 (the queue-side PREFIX ledger + the parent-history PREFIX ledger) are PAIR-keyed and TIME-unbounded ⇒ an EARLIER commission's delivery — or a mere ENQUEUE — satisfied a FRESH commission** | ✅ **CLOSED — NOT deferred** (leader ruling, PR3 Batch A). **The trace (the DEFECT's path — `_delivery_evidence_exists` is the PRE-SPLIT name):** `report_delivery_recovery.py` `_delivery_evidence_exists` → `_reconcile_one_commission` (the `has_evidence` branch) → `_satisfy_commission_on_evidence` → the guarded UPDATE → `mission_record/repository.py` `transition_commission_state` (counter-only under `dry`, a LIVE write under `enforce`). **Batch A's own ruling SPLIT that probe — the method `_delivery_evidence_exists` was REMOVED and replaced by TWO methods:** `_commission_bound_delivery_evidence` (leg 1 — the commission-bound, state-filtered injection-row read, the ONLY authority) and `_note_diagnostic_delivery_evidence` (legs 2/3 — the DIAGNOSTIC queue-side + parent-history PREFIX-ledger pair). The LIVE trace today runs through `_commission_bound_delivery_evidence`; the name above records the DEFECT's path, not the current code. **Why it was wrong:** leg 2 matches the source `internal_report:{worker}:%` with NO commission id and NO time bound (and counts a `ready` — ENQUEUED, never processed — row as evidence); leg 3 scans the commissioner's WHOLE history. Neither can tell THIS commission's delivery from an EARLIER one's on the same pair, so the publish hold released EARLY (the premature-publish harm class). **The ruling applied:** legs 2/3 may NEVER authorise a `satisfied` and may NEVER authorise a hold-against-abandon — they are DIAGNOSTIC ONLY (they keep running; their result is logged; their FAULTS are counted in their own counter). Leg 1 (MAJ-1's commission-bound, state-filtered injection-row read) is the ONLY authority. **The consequence, stated deliberately:** a commission whose delivery is visible ONLY through legs 2/3 is no longer satisfied early — it resolves via the terminator on the real delivery path, or via the TTL-bounded abandon; the publish hold therefore releases NO EARLIER than the TTL horizon (fail-closed, bounded, safe). **The fault semantics (the leader's binding interpretation):** a diagnostic leg's fault does NOT hold the row (a persistently failing diagnostic probe would make the hold UNBOUNDED, contradicting the ruling's own boundedness promise) — it is counted in its OWN counter, `diagnostic_probe_errors` (the `masked_probe_errors` precedent), and the row stays on the abandon path; ONLY leg 1's fault may HOLD. **The pins:** `tests/unit/test_mission_reconciliation_sweep.py::TestTheDiagnosticEvidenceLegs` (6 nodes — Batch B added the V-F1 pin that asserts the diagnostic POSITIVE path is LOGGED; without it the positive path was UNPINNED and could have been deleted silently) + the re-anchored authority-fault HOLD pin. **The F1 dangling pointer this closes:** the old `_delivery_evidence_exists` docstring claimed "the residual is recorded in the B-arc closure table's deferral list" — NO SUCH ROW EXISTED; the docstring now states the ruling and cites THIS row. **A WORDING IMPRECISION IN THE IMMUTABLE COMMIT MESSAGE (Batch A `e95405296`):** that message says the diagnostic legs' "result is logged + counted" — the code counts only the FAULTS (in `diagnostic_probe_errors`); the POSITIVE result is LOGGED only. The accurate wording — "their result is logged; their FAULTS are counted in their own counter" — is what the code, the docstrings, this row and the enforce-promotion runbook §3.3 now carry. (Commit messages are immutable — the message is NOT amended; the correction is recorded here.) |
| **F13 — the worker-missing branch runs NO delivery-evidence leg** (a commission whose delivery is visible but whose WORKER ROW was deleted is abandoned `worker_row_missing` instead of satisfied) | ⛔ **DEFERRED — DESIGN-CONFORMANT, NOT A DEFECT** (NO code change was made). **The shape:** in `report_delivery_recovery.py`'s `_reconcile_one_commission`, the `worker is None` branch (the T2 shape) runs the R4 bus-watcher probe (MAJ-5) and then abandons the row with the frozen reason `worker_row_missing` once it is past the TTL — it never runs the leg-1 delivery-evidence read, so a DELIVERED-but-worker-row-deleted commission is abandoned rather than satisfied. **The design-conformance citation:** the design's own per-row pseudo-code omits the evidence read from that branch exactly (`reconciliation-sweep-design.md` lines 331-333: `if worker is None:` → `if age > TTL: abandon(commission, "worker_row_missing")` / `else: hold(commission, "below_ttl_hold"); continue` — the `read_delivery_evidence` step appears only AFTER the `worker is None` branch), so the implementation MATCHES the design and the shape is a deliberate consequence of it. **The blast radius is the AUDIT TRAIL ONLY:** the row resolves to `abandoned` (with the frozen reason) at the TTL horizon instead of `satisfied` at the evidence read — the commission is still retired, the hold still releases, and no premature publish and no wrong-side live transition is introduced (the abandon is TTL-bounded and loud). **No code change was made** — the branch is recorded here as a DESIGN-LEVEL DEFERRAL so the audit trail shows the shape instead of leaving it as an unexplained discrepancy. |

**F14 — the MAJ-1 commission-bound comparison is a PARSED-INSTANT comparison, BY DESIGN (a later reader must NOT "correct" it).** The MAJ-1 ruling record (the commit `MAJ-1 — the delivery-evidence probe is state-filtered + commission-bounded`) settles the commission linkage as a PYTHON-SIDE bound: a delivered injection row STRICTLY OLDER than the commission's mint belongs to an EARLIER commission on the same pair, so it is not THIS commission's evidence. The comparison is made on **PARSED, tz-aware UTC instants** (`parse_iso_utc`) — DELIBERATELY, NOT as a TEXT-ISO string comparison: the two columns carry DIFFERENT ISO shapes (the commission's `now_utc_iso()` vs the injection row's `datetime.now(timezone.utc).isoformat()`), so a TEXT comparison across shapes is not safe, and the TEXT-ISO convention forbids a string comparison in SQL anyway. A row whose stamp EQUALS the mint counts as evidence; only a STRICTLY older row is excluded, and an undateable stamp on either side is fail-MISS. **Do not "fix" the parsed-instant comparison back into a TEXT-ISO comparison** — that would reintroduce the cross-shape comparison bug the MAJ-1 ruling closed. (The code docstring at `report_delivery_recovery.py`'s `_delivered_row_is_this_commission` already carries this rationale; the code is unchanged by this note, and no planning-subtree note records the MAJ-1 commission-bound ruling text — hence the note sits here, in the table's MAJ-1 area.)

### (d.2.1) The PR2 tidy-pass RIDER items this branch handled — the auditable list

The prior iteration's report claimed "**9 handled** (PR2 tidier M/L items)". **That
figure had NO auditable source.** `.agents/tidier/notes.md` carries a
mission-records entry — the 2026-10-25 "mission-records PR2 enforce-reads tidy
pass" (2 High / 18 Medium / 24 Low) — and it ships NO per-item ledger, so "9"
could not be sourced from it (or from anywhere else in either tree). The honest
figure is the set of rider items this branch's commits actually address —
the rows below (each verified by reading the commit's diff).

| # | PR2 rider item (the tidier note, 2026-10-25) | What this branch did | Commit |
|---|---|---|---|
| 1 | **High #2** — the mutation harness scored ANY non-zero pytest exit as "killed" (exit-4 "no tests ran" ⇒ a false PASS N/N) | The SCORER fix itself landed at `d9e8fbdd2` (PR2 round 3 — already in this branch's base). What this branch fixed is the FOLLOW-ON: two mutations whose ANCHORS the PR2 sentinel-unification + PR3's F2 hardening had made stale — the matrix was FAILING (21/23), not falsely green. Re-anchored ⇒ 23/23 killed. | `710785e34` |
| 2 | **Medium** — the PACKS figure ledger cited a superseded measurement sha | The PR2 section's FIGURE LEDGER now cites the tester's measured gate sha `3fc16bc66` (figures only — no count changed) | `7ce4b2cdd` |
| 3 | **Low** — two unused PR2 test imports (F401) | Both dropped (ruff F401 on the two files: 2 hits → 0). Provenance note: the commit cites the PR2 *evidence table*'s F401 row, and that table is NOT in either tree — the row's own text is not re-verifiable here. | `db11128db` |
| 4 | **"OUTER except → []"** (the tidy pass routed it to PR3) — the SOURCE C tree-rows provider swallowed ANY exception and returned `[]` with NO log line | The swallow becomes a LOGGED fail-open (the RETURN value stays: the empty list IS the contract — the C seam's whole-eval fail-open ALLOWS the leader mission, a looser posture than the counts-only degradation) | `10097fb1f` |
| 5 | **"_engine reach-through @ instance_messaging.py"** (routed to PR3) | The public `InstanceManager.engine` accessor replaces the private reach-through (behaviour-neutral — the property returns the same object) | `c1afe2a3d` |
| 6 | **Recurring pattern** — the sentinel spelled two ways (`-1` literal vs `MISSION_READ_UNKNOWN`) | ONE spelling on the producing side (the gate side already imported the constant); behaviour-neutral — the constant IS `-1` | `7f778b42d` |
| 7 | **Recurring pattern** — the ordinal/counter prose drift ("THREE vs FOUR input"; "three vs four tree counters"; the FIFTH collision) | All the sites re-derived at HEAD and corrected (prose only — no decision branch changed) | `e08d36089` |
| 8 | **Recurring pattern** — the same "three vs four tree counters" drift, in the SOURCE C signals docstring | Corrected to four (the site that already said four is what made the contradiction visible) | `10097fb1f` |

**Not tidier items — the review batch** (listed so the count cannot be muddled):
`826664811` (the REVIEW's batch: M-A + six MINORs) and `49f58cb24` (that
batch's docs half). They are the independent review's riders, not the tidy
pass's.

**The measured F2 concurrency pack — the "12/12" correction.** The prior
iteration reported the F2 concurrency pack as "**12/12**". **That figure was
MEASURED nowhere in either tree and was UNSUBSTANTIATED** (this row and the
paragraph below QUOTE the string in order to correct it — they do not measure
it) — and the pack defines
exactly **6** test functions. The MEASURED run:

```
uv run python -m pytest tests/unit/test_mission_record_f2_concurrency.py -q
→ 6 passed in 0.70s
```

**The honest figure is 6 nodes, 6 passed** (the pack's classes:
`TestTheSingleStatementGuard` 3 · `TestTheOrderingContract` 2 ·
`TestTheConcurrentPair` 1). The string "12/12" appears NOWHERE in the
MISSION-RECORDS surface — **EXCEPT in this very table, which sits IN the
PR3 planning directory and so cannot be inside the surface it denies** (the
PR1/PR2 planning directories, the enforce-promotion runbook and the F2 pack
itself carry no such string). This row is the single
place the pack's figure is recorded. (The branch's other "12/12" hits are
unrelated subsystems — among them the approver's nest-ops tracker, the
designer-critic orchestration map, the langgraph-checkpoint-perf phase notes
and the oauth2 rollout notes; the branch carries further occurrences in other
unrelated files, so this is an ILLUSTRATION, not an exhaustive list.)

**The commit-record corrections (immutable commit messages, corrected HERE).**
Two branch commit messages carry claims that a later reader must not take at
face value. Commit messages are IMMUTABLE — they are NOT amended and NOT
rebased; the correction lives here, in the auditable rider list.

| # | The claim (verbatim, in the immutable commit message) | The correction |
|---|---|---|
| **F3** | MAJ-6 (`1c6f8f15f`): "the discovered-site set is **byte-equal** between the base (013895287) and this tree, so the branch and this commit add nothing." | **THE MEMBERSHIP CLAIM HOLDS; THE BYTE-EQUALITY CLAIM DOES NOT.** The census MEMBERSHIP is identical — the SAME 40 sites, none added and none removed — so the branch's substantive conclusion ("it adds no site and removes none") stands. But byte-equality is FALSE at TUPLE level: the per-site tuples carry a LINE anchor, and **11 of the 40 sites moved lines** because the branch's commits shifted the call sites above them. **The correct claim would have been:** "the census MEMBERSHIP is identical between the base and this tree — the same 40 sites, none added, none removed; the per-entry TUPLES are NOT byte-equal (11 of the 40 entries' line anchors moved, because the branch's commits shifted the call sites above them), so the branch adds no site and removes none." (The closure table's §(d.4) records the same shift for the `child_reports.py` hooks.) |
| **V-F4** | Batch A (`e95405296`): "the 154 failing/erroring node ids are byte-identical with and without this change." | **THE TWO QUOTED FIGURES DISAGREE, AND NEITHER IS PUBLISHED IN-TREE.** The commit message says **154** ids; the dispatch relayed "109 failed / 49 errors" (= **158**). No artifact recording either census exists in the tree (grepped `.agents/` and `docs/`), so the batch's A/B census is an **UNPUBLISHED MEASUREMENT** and neither figure can be presented as verified. **NO number is asserted here** — the authoritative census is the TESTER's lane (a whole-suite A/B census was deliberately NOT re-run for this correction). The claim's substance — that the change introduces NO branch-caused red — is unchanged and is what the §(d.2.1) rider list already records; only the COUNT is unverifiable from the tree. |

### (d.3) The doc-drift this table found

The PR3 adoption notes §(e) (the terminator call-site table) was authored at
`3ee9d6673` — BEFORE lane 7 landed — and its "RDRS lane 7 (next cycle)" and
"`abandon_commission` still has ZERO production callers" lines are now
**STALE**. At the last CONTENT commit (`2453e271c`) the grep-verified caller
census is:

| Function | Production callers (anchors at the last content commit `2453e271c`) |
|---|---|
| `satisfy_commission` | `daemon/services/child_reports.py:4753` (via `satisfy_commission_fail_open`) + `daemon/services/report_delivery_recovery.py:3269` (the service call inside `_satisfy_commission_on_evidence`, which is defined at `:3241` and invoked from `:2532`) |
| `abandon_commission` | `daemon/services/report_delivery_recovery.py:3196` (the service call inside `_abandon_commission`, defined at `:3157`) — the helper has **three call sites, all in RDRS lane 7**: `:2397`, `:2539`, `:2550` |
| `supersede_commission` | **ZERO** (no call site anywhere in `daemon/`; the method is defined at `daemon/services/mission_record_service.py:1045` and never called) |

**Action for the next editor of the PR3 notes:** the PR3 adoption notes' §(e) table
and its "ZERO production callers" line should be corrected to match the
grep-verified caller census table above (the census at the last content commit,
`2453e271c`) — NOT to a "(f)": neither this table's §(f) nor the adoption notes'
§(f) carries that census. The notes are the implementation truth, so a stale
line in them is a live misstatement. **CLOSED at `4e37aa711`** — the notes' §(e)
table + the "ZERO production callers" line were corrected in this branch (the
sweep's three abandon call sites + the "the service METHOD has zero callers"
split). The remaining census-pin drift is a SEPARATE item — see (d.4) below.

### (d.4) The census `hooked_at` pin drift — a RE-ANCHOR note (the FIXTURE was NOT re-anchored here)

**Pre-existing + quarantined.** The fixture
`tests/job_queue/test_terminal_write_census.py` pins each `hooked` entry's
`hooked_at` at the `notify_watchers` call that covers the site; the anti-rot
check requires such a call within **±5 lines** of the pin. The
`daemon/services/child_reports.py` pins are **already stale AT BASE** — the
live calls sit far outside the ±5 window there — so the check was red
before this branch and is a known quarantined red (`.agents/tester/
QUARANTINE.md` rows 38 + 50 — the `test_terminal_write_census` family;
row 50 names the `child_reports.py:3941 ×4` pins explicitly).

**The branch's shift (+118 lines).** The PR3 commits added lines above the
call sites, so the live `notify_watchers` calls moved:

| `notify_watchers` call in `daemon/services/child_reports.py` | base `013895287` | branch `2453e271c` | Δ |
|---|---|---|---|
| first call | `:5540` | `:5658` | **+118** |
| second call | `:5556` | `:5674` | **+118** |

(the third `notify_watchers` mention at `:5679` is a log string, not a call.)

**The pins are stale BEFORE and AFTER.** The fixture pins
`daemon/services/child_reports.py:3941` (×4 — the root-completion /
dispatch-wrapper family) and `:5457` (×2 — the mission-strand-healer
family). At base, `:5457` was already 83 lines from the live `:5540` call
and `:3941` was 1599 away; after the branch's +118 shift the live calls are
`:5658` / `:5674`, i.e. **201 lines** past the `:5457` pin. The fixture was
**deliberately NOT touched by PR3** — re-anchoring it here would absorb an
unrelated, already-quarantined rot into this branch's diff and mask it.

**The observer pin is stale AT BASE too — the same class, previously
omitted from this list.** The fixture pins
`daemon/services/job_feedback_observer.py:2071` (×1 — the atomic-transition
site). Its LIVE `notify_watchers` calls are at `:2226` / `:2232` — **155
lines** from the pin, so it was ALREADY outside the ±5 anti-rot window at
BASE. The branch never shifted that file: the live pair is byte-identical
at base `013895287`, at Batch A `e95405296` and at the anchor `2453e271c`.
That pin is therefore PRE-EXISTING rot, not branch-caused — the same
verdict the `child_reports.py` pins carry.

**Re-anchor note for whoever re-anchors next (outside this PR):**

* the two `:5457` pins → the live pair `:5658` / `:5674` (a plain line bump);
* the four `:3941` pins → these point at a call that no longer exists at
  that site at all (the live pair is the one above) — they need a RE-SURVEY
  of the root-completion hook, not a line bump;
* re-run `tests/job_queue/test_terminal_write_census.py` and confirm the
  family's other pins (the row-50 list) in the same pass. **The two values
  below are the FIXTURE's pinned values — NOT live anchors.** Read each
  against its OWN live pair before moving it: `task_processor.py:1087` (the
  FIXTURE's pin → the live call is `daemon/services/task_processor.py:1275`,
  unchanged by this branch) and `job_feedback_observer.py:2071` (the
  FIXTURE's pin → the live pair is
  `daemon/services/job_feedback_observer.py:2226` / `:2232`, also unchanged
  by this branch);
* the fixture's own anti-rot guidance (its module docstring) is the
  sanctioned procedure — follow it rather than editing the numbers blind.

---

## (e) The terminator call-site map

Production terminal paths → the terminator that retires the commission row.

| Path | Terminator | Owner | Commit |
|---|---|---|---|
| Natural completion write path (the report is written AND addressed to its commissioner) | `satisfied` — a guarded `UPDATE … WHERE state='outstanding'`, ATOMIC with the delivery write (rides the `WriteGuardSession` via a SAVEPOINT), FAIL-OPEN (a satisfy failure never breaks the delivery); rowcount 0 = a lost race, counted, never retried in place | PR3 | `be8806ff5` |
| Deferred-marker / recovered-marker deliveries (RDRS lanes 1/3/4 + drain claims) + the delivery-evidence probes | `satisfied` on evidence (R1b) — positive delivery evidence with a still-outstanding row means the natural satisfy write was lost | RDRS lane 7 | `13aa51e1a` |
| Worker-terminal / TTL / no-report-ever | `abandoned` under the fail-MISS probes (R1 / R2 / R3) | RDRS lane 7 | `13aa51e1a` |
| Epoch supersession | **NOTHING** — ruling (c): the rows keep pointing at their owning epoch; the read scope absorbs them instead | — | — |

**The "not satisfied by the delivery path" list** (each with an owner — from
the terminator commit body, and still accurate at the last content commit
`2453e271c`):

* **A dead commissioner** — the write path's dead-recipient guard fell back to
  the structural parent, so no delivery to the commissioner happened ⇒ the row
  stays OUTSTANDING for the sweep.
* **Deferred markers** — no delivery happened at all.
* **Idempotency / recovered-marker races** — a prior or a concurrent path owns
  the delivery.
* **Marker-recovered deliveries** — the sweep's delivery-evidence probes own
  them (R1b).

**The states written by the sweep:** `satisfied` and `abandoned` only.
`superseded` stays mint-lane-only and is NEVER written by the sweep.
The resolution stamp `mission_commissions.resolved_at` (TEXT ISO) is written
for EVERY resolution — abandoned OR satisfied; it stays NULL while the row is
outstanding. It is DISTINCT from `satisfied_at` on purpose: dual-purposing
`satisfied_at` for abandoned rows would make the column name lie.

**The abandon-reason vocabulary is FROZEN:** `worker_terminal_no_report` ·
`ttl_expired_not_live` · `worker_row_missing`.

---

## (f) The enforce-promotion gate — where it stands

The PR2 §(f) checklist (the operator's order):

1. ✅ Land PR3's commission reconciliation sweep — `13aa51e1a` + `3bd21ecbb`.
2. ✅ Close MAJOR-1 — `a3e033984` (ruling (c)) + `be8806ff5` (the terminator).
3. ⬜ Flip `ENSEMBLE_MISSION_RECORDS=enforce` + restart — the operator step.

The remaining promotion step has its own runbook:
`docs/mission-records-enforce-promotion-runbook.md` (this branch).

**What the promotion still needs:** the sweep's own evidence — the `dry`-mode
counters (`abandoned_ttl` / `skipped_live` / `masked_suspected`) are the
TTL-tuning dataset, and they should be read BEFORE the lane is flipped.
