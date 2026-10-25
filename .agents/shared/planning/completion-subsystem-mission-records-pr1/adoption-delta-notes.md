# Adoption-Delta Notes — Proposal B PR1 (mission-records dry mints)

Completion-subsystem Proposal B, PR1 of 3. This note covers the TWO
piecemeal-adoption deltas (comparison.md §4c) that PR1 had to decide,
plus the lane-boundary judgment calls the docs left open. **The design
docs are authoritative; where this note records a deviation, the
deviation is flagged with its justification.**

---

## (a) Tie-break: A-resolver (latest committed nudge) vs B (commission rows)

**Decision (verbatim intent from comparison.md §4c):** multi-commissioner
is undefined in B. PR1 therefore:

* A second commission of the SAME worker mints a SECOND OUTSTANDING row
  — the guarded-state lifecycle (`outstanding` → `satisfied` /
  `abandoned` / `superseded`) is the discriminator. No unique
  constraint on `(mission_id, worker_instance_id)` — deliberate.
* The rung-2 ladder lookup takes the OLDEST outstanding row
  (deterministic; `created_at ASC`).
* **Routing authority STAYS with the A-resolver**
  (`daemon/services/report_recipient.py` — the live D1
  latest-committed-nudge tie-break) until the PR2/PR3 flip. NOTHING in
  PR1 routes reports by commission rows. The precedence decision is
  documented on BOTH sides: the service module docstring +
  `mint_commission` docstring, and the `report_recipient.py` module
  docstring (the "ADOPTION DELTA (a)" section).
* When the flip happens (PR3), the tie-break question (latest-committed
  vs oldest-open vs newest-outstanding) is a USER decision, not a PR1
  artifact. The oldest-first rung-2 read is the PR1 *resolution*
  order, not a routing ruling.

## (b) Historical coverage: the 6 existing `instance_ops_audit` rows

Best-effort advisory backfill, rules per proposal §5.2
(`MissionRecordService.backfill_advisory_nudge_commissions`):

* Source: `action='instance_nudge'` AND `outcome='committed'` AND
  `dry_run=false` (the audit's default row is a PREVIEW — erratum 7;
  preview rows never backfill).
* Target still NON-TERMINAL → ADVISORY nudge commission row
  (`lane='instance_nudge'`, `provenance='backfill'`,
  `advisory_backfill=True`). Idempotent: DETERMINISTIC UUID5
  commission ids (uuid5 over mission+worker+lane+audit-row) so
  re-runs conflict on the PK and no-op; epoch-0 coverage via
  resolve-if-none.
* Unrecoverable targets (already-terminal; no-actor; missing rows) =
  ACCEPTED ONE-TIME GAP, counted per-cause in the result dict.
* Dry-run-loggable: boot runs the DRY-RUN ONLY (manager `__init__`,
  envelope-guarded, skipped in mode `off`) — the committing run is an
  operator action (`dry_run=False`).
* **Advisory backfill rows are EXCLUDED from routing authority
  FOREVER.** Any future read that consults commission rows for
  routing must filter `advisory_backfill = False` (the
  "if dropped, the ledger is worse than no ledger" rule).

---

## Lane-boundary judgment calls (documented, docs-anchored)

1. **Nudge commission vs "same transaction as the audit row"
   (proposal §2.1 lane 4).** The nudge's audit is Tier-2 WRITE-AHEAD —
   `write_audit_ahead` and `settle_audit` are TWO transactions today,
   so "the audit transaction" is not a single unit to ride. The
   commission is minted AFTER `settle_audit("committed")`: an
   act-known-committed posture that never records a failed act, and
   dry-run previews mint nothing. Adjacent, not same-txn — flagged as
   the one deliberate deviation from the §2.1 wording.
2. **send_message commission rides the watcher boundary** (§2.2 lane 5
   "written alongside the existing watcher mint"): the tree-guard
   means a send to a NON-child target mints no watcher and therefore
   no commission either. Same-txn as the watcher region realized via
   the helper's `WriteGuardSession` + savepoint (mint failures roll
   back the savepoint only — the watcher/notify contract is untouched).
3. **job_continue commissioner stays NULL.** The source token is
   AGENT-grain (`agent:{caller_agent_id}`); recording it in
   `commissioner_instance_id` (an instance-id column) would poison the
   PR2/PR3 instance-keyed joins. This is exactly the attribution-grain
   gap comparison.md §3-B flags as unacknowledged; PR1 acknowledges it
   and logs the caller instead of mis-typing the column.
4. **Delivery/resume lanes (boot, cascade, revive) skip ladder rung 2**
   — they use mint-if-NO-OPEN-EPOCH, not the full ladder: resume is
   delivery, not founding, and a resumed worker's epoch boundary is
   its OWN, not its commissioner's. The commissioning lanes
   (user_api, chat, nudge, send_message) use the full 3-rung ladder.
5. **user_api/chat mint-then-attach.** The founding message id is
   minted INSIDE the enqueue, so the epoch is minted before the
   dispatch (so the carrier rides the enqueue kwarg) and the id is
   attached after (`attach_founding_message`, guarded on NULL — first
   founding wins). A failed enqueue can leave an open epoch with no
   founding message; the next successful enqueue on the same instance
   resolves to it (rung 1) and attaches — bounded, honest, and
   read-invisible until PR2.
6. **Scheduler (lane 9) is INERT by standing product ruling.** The
   mint SITE is wired — `SchedulerAdapter._route_via_job_queue` (the
   scheduler's own durable-enqueue point) calls the fail-open
   resolve-or-mint behind `ENSEMBLE_MISSION_RECORDS_SCHEDULER_MINT`
   (default OFF); the mode ladder alone (`dry`) does NOT arm it — both
   gates must open. Lane 9 stays bare.
   *(F2/F3 correction: the site is wired in the code — it was a dead
   extension point with a false claim until the independent-review fix
   pass. The wiring is INERT in every default configuration, so the
   zero-behavior-change contract is unchanged.)*
7. **Epoch-boundary ledger clears are NOT generalized in PR1** (the
   §2.4 policy): mints write records only; the revive lane's existing
   fresh-episode clear keeps its exact current behavior. The
   "recorded consequence" is the revive epoch row, not a new clear.

## PR2/PR3 flip-list preview (per comparison §5 / proposal §7)

PR2 (read flip, behind `enforce`):
- Census: `attestation_required` consults open commission rows (legacy
  scan = fallback leg).
- 5th R2 input `outstanding_commissions` (fail-open `-1` sentinel, never
  a 0 default) beside the four tree counters (`manager.py` R2 region).
- SOURCE U anchor = record lookup (`founding_message_id`), the 6-step
  ladder = declared legacy fallback.
- L3 root gate mission-scoped leg (publish requires no outstanding
  commissions on the open epoch).
- Term-1: bypass RECORDING (already record-shaped via `record_attestation`
  seam) + the quiet-mission blocking POLICY — default OFF until the
  user rules (the three non-binding rulings stay non-binding).
- Epoch-boundary ledger clears generalize from "fresh-episode revive
  only" to "every recorded boundary"; `denial_epochs` goes
  epoch-tagged.
- mission_id carrier threading to the remaining four MessageTapSlot
  sites (agent_node_return + 3 compaction taps; PR1 lands the entry
  site + the mechanism).
- amend-at-delivery: fire `amend_anchor` from the tap site.

PR3 (routing + reconciliation):
- D1 flip: routing resolves the child's outstanding commission row →
  `commissioner_instance_id` (fallback structural); `report_injections`
  mission_id + commissioner columns gain their writers; the C3 backstop
  + PREFIX-ledger taught in the SAME release (the duplication firewall).
- Commission reconciliation sweep (eternal-mission escape: worker
  terminal + no report row → `abandoned` with reason; TTL backstop).
- The committing backfill run + retention story.
- Multi-commissioner routing tie-break: USER decision (see (a)).

## PR1 train scope note — the D2(c) real-DB test is a PRE-EXISTING gap closure

The PR1 branch's test train carries
`tests/integration/test_d2c_settle_hold_real_db.py`. That file is a
**PRE-EXISTING dependency-watchers coverage-gap closure** (the gap was
flagged by the 2026-10-24 end-of-train verification — it predates PR1),
**NOT** a PR1 deliverable: it covers the LEGACY dependency-watchers /
C-union path and intentionally contains NO `mission_records` /
`mission_commissions` references. It rides in the train because that is
where the real-DB harness work landed. PR1's own coverage is the four
`tests/unit/test_mission_record_*` files.

*(Independent-review F7 — framing correction. The commit that added
the file described it as part of the "PR1 test train"; commit bodies
are immutable history, so the correction is recorded here and in the
test module's own docstring.)*
