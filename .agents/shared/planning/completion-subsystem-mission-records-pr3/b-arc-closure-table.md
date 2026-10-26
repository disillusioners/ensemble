# B-Arc Closure Table — Completion-Subsystem Proposal B (PR1 → PR3)

**The ARC-level verdict view.** The three adoption-delta notes are the
IMPLEMENTATION TRUTH; this file is the CLOSURE VERDICT per defect, per the
design's own §3 per-defect table (proposal-b) and the architecture map. It
does not restate implementation prose beyond what a verdict row needs.

**Branch:** `feature/mission-records-pr3` — range `013895287..4e37aa711`
(base `013895287`). The counts below are measured **as of the review
commit `4e37aa711`**: 23 commits, 38 files, +6098/−231. (Later commits
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
| **D5-latent** — stale anchoring on `internal_agent:*` lanes; `denial_epochs` never cleared; the ~14-minute anchor lag | ✅ **CLOSED** | Epoch semantics are the core construction: the anchor is a durable row minted at enqueue and **amended AT DELIVERY** (ruling ③) — no would-be-END window re-derivation, no lag seam. The `denial_epochs` ledger is epoch-tagged and the **epoch-boundary clear is armed in `enforce`** (it is a gate-visible behaviour change, so it rides the same operator promotion). The F2 amend guard was hardened in PR3 into ONE shared single-statement guarded UPDATE (the status predicate lives in the WHERE, the outcome is decided by the ROWCOUNT, no prior read) — the PR2 flake fix. | PR1 (the records/mints/stamps) + PR2 (amend-at-delivery; the boundary clear; the epoch tag) — `tests/unit/test_mission_record_pr2_tap_amend.py`, `tests/unit/test_mission_record_pr2_l3_ledger.py::TestEpochBoundaryLedgerClear` / `::TestEpochTaggedDenyLedger`, `tests/unit/test_mission_record_pr2_carries.py::TestF2AmendGuard` · PR3 hardening: `0bff3a4dd` + `tests/unit/test_mission_record_f2_concurrency.py` |

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
| Attribution never re-points; `supersede_commission` stays ZERO-CALLERS (grep-verified at HEAD: no call site anywhere in `daemon/`) | `daemon/services/mission_record_service.py` |
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
| **③** The amendment fires AT DELIVERY | **SHIPPED** (ruling ③) — the re-founding amendment fires from the message-tap site, not when the window is scanned. Kills the ~14-minute mid-turn correction lag seam. | PR2 notes §(a) item 7 (AMENDED) · `tests/unit/test_mission_record_pr2_tap_amend.py` · the PR2 runtime gate's lag-seam item (11/11, with the real unmocked seam + the guarded UPDATE in SQL + the delivery-not-scan structural proof) |
| **④** Multi-commissioner tie-break | **THE OLDEST-OUTSTANDING DISCRIMINATOR STANDS** — the routing leg takes the OLDEST outstanding row (`created_at ASC`), the same deterministic order the rung-2 resolution always used, now carrying ROUTING authority. | PR3 notes §(b) · `tests/unit/test_mission_record_pr3_routing_flip.py::TestTheRoutingFlip::test_multi_commissioner_takes_the_oldest_outstanding` |
| **⑤** The TTL | **A KNOB, NOT A DECISION — no hard-coded decision was taken.** The knob is `ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS` with the house default 21600 s (6 h). The DEFAULT is a starting point, not a decision: the `dry`-mode counters are the evidence that tunes it. | PR3 notes §(h) + §(j) · `tests/unit/test_mission_reconciliation_sweep.py::TestTheTtlKnob` (4 nodes: the 6-hour house default, the env override, the invalid-value fallback that never raises, the config default when the env knob is absent) |

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

### (d.3) The doc-drift this table found

The PR3 adoption notes §(e) (the terminator call-site table) was authored at
`3ee9d6673` — BEFORE lane 7 landed — and its "RDRS lane 7 (next cycle)" and
"`abandon_commission` still has ZERO production callers" lines are now
**STALE**. At HEAD the grep-verified caller census is:

| Function | Production callers at HEAD |
|---|---|
| `satisfy_commission` | `daemon/services/child_reports.py:4742` (via `satisfy_commission_fail_open`) + `daemon/services/report_delivery_recovery.py:2314` (`_satisfy_commission_on_evidence`) |
| `abandon_commission` | `daemon/services/report_delivery_recovery.py:2217`, `:2321`, `:2332` — **three callers, all in RDRS lane 7** |
| `supersede_commission` | **ZERO** (no call site anywhere in `daemon/`) |

**Action for the next editor of the PR3 notes:** §(e)'s table and its
"ZERO production callers" line should be corrected to match (f) below. The
notes are the implementation truth, so a stale line in them is a live
misstatement. **CLOSED at `4e37aa711`** — the notes' §(e) table + the
"ZERO production callers" line were corrected in this branch (the sweep's
three abandon call sites + the "the service METHOD has zero callers" split).
The remaining census-pin drift is a SEPARATE item — see (d.4) below.

### (d.4) The census `hooked_at` pin drift — a RE-ANCHOR note (NOT re-anchored here)

**Pre-existing + quarantined.** The fixture
`tests/job_queue/test_terminal_write_census.py` pins each `hooked` entry's
`hooked_at` at the `notify_watchers` call that covers the site; the anti-rot
check requires such a call within **±5 lines** of the pin. The
`daemon/services/child_reports.py` pins are **already stale AT BASE** — the
live calls sit far outside the ±5 window there — so the check was red
before this branch and is a known quarantined red (`.agents/tester/
QUARANTINE.md` rows 38 + 50 — the `test_terminal_write_census` family;
row 50 names the `child_reports.py:3941 ×4` pins explicitly).

**The branch's shift (+107 lines).** The PR3 commits added lines above the
call sites, so the live `notify_watchers` calls moved:

| `notify_watchers` call in `daemon/services/child_reports.py` | base `013895287` | branch `4e37aa711` | Δ |
|---|---|---|---|
| first call | `:5540` | `:5647` | **+107** |
| second call | `:5556` | `:5663` | **+107** |

(the third `notify_watchers` mention at `:5668` is a log string, not a call.)

**The pins are stale BEFORE and AFTER.** The fixture pins
`daemon/services/child_reports.py:3941` (×4 — the root-completion /
dispatch-wrapper family) and `:5457` (×2 — the mission-strand-healer
family). At base, `:5457` was already 83 lines from the live `:5540` call
and `:3941` was 1599 away; after the branch's +107 shift the live calls are
`:5647` / `:5663`, i.e. **190 lines** past the `:5457` pin. The fixture was
**deliberately NOT touched by PR3** — re-anchoring it here would absorb an
unrelated, already-quarantined rot into this branch's diff and mask it.

**Re-anchor note for whoever re-anchors next (outside this PR):**

* the two `:5457` pins → the live pair `:5647` / `:5663` (a plain line bump);
* the four `:3941` pins → these point at a call that no longer exists at
  that site at all (the live pair is the one above) — they need a RE-SURVEY
  of the root-completion hook, not a line bump;
* re-run `tests/job_queue/test_terminal_write_census.py` and confirm the
  family's other pins (the row-50 list: `task_processor.py:1087`,
  `job_feedback_observer.py:1989`) in the same pass;
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
the terminator commit body, and still accurate at HEAD):

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
