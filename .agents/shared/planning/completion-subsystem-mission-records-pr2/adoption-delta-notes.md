# Adoption-Delta Notes — Proposal B PR2 (the enforce reads)

Completion-subsystem Proposal B, **PR2 of 3**. PR1 shipped the record
substrate (the tables, the mints, the stamps) with a zero-behavior-change
contract. **PR2 ships the READS**: the census, the SOURCE U anchor, the
5th R2 input, the L3 mission leg and the judge's SOURCE C membership
now consult the record instead of re-deriving "what is this mission?"
from message shapes and tree topology.

The authoritative design docs are UNTRACKED files in the MAIN checkout
(``.agents/shared/planning/completion-subsystem/``) and are deliberately
NOT copied here. This note records only what PR2 decided and shipped.

---

## (a) The AMENDED PR2 list (the flip-list PR2 actually executed)

The PR1 note's "PR2/PR3 flip-list preview" section is amended in place
(strike-through + markers) — this section restates the shipped list.

| # | Item | Shipped in PR2 | Notes |
|---|------|----------------|-------|
| 1 | Census ← commissions | **YES** | ``attestation_required`` = the open mission has commission rows; the legacy scan is the declared fallback leg. **UNION, not replacement** — see (b) below. |
| 2 | SOURCE U anchor ← record | **YES** | The anchor is the epoch's ``founding_message_id``; the six-step exclusion ladder is the declared legacy fallback (record miss ⇒ today's behavior, exactly). |
| 3 | 5th R2 input ``outstanding_commissions`` | **YES** | Mission-scoped; fail-open ``-1`` sentinel (never a 0 default); the quiet predicate requires ``== 0``. |
| 4 | L3 root-gate mission leg | **YES** | The publish is held while the open mission has outstanding commissions. The bus-pending leg (the watchers) is UNTOUCHED. |
| 5 | SOURCE C membership ∪ commissions | **YES** | The judge's C section = structural tree ∪ commission membership (the out-of-tree commissioned worker finally appears). The subordination clause itself is preserved unchanged. |
| 6 | mission_id stamp at the 4 remaining tap sites | **YES** | ``agent_node_return`` + the 3 compaction taps; the stamp is the CARRIED value (never re-derived from message shapes). |
| 7 | amend-at-delivery | **YES — AMENDED (ruling ③)** | The amendment fires AT DELIVERY (the tap site), NOT when the window is scanned. |
| 8 | Epoch-boundary ledger clear + epoch-tagged ``denial_epochs`` | **YES — MODE-GATED (see (c))** | The clear is armed in ``enforce``; the epoch tag rides the deny entry. |
| 9 | **Term-1 bypass-recording + the quiet-mission blocking POLICY** | **NO — REMOVED (ruling ①)** | The attested arm stays a no-op/bypass BY DESIGN. The proposal-b §3 NEW(ii) bypass-recording text and the quiet-mission blocking policy are SUPERSEDED by the ruling. |
| 10 | Scheduler anchoring | **NO — UNCHANGED (ruling ②)** | The scheduler mint hook stays inert; ``ENSEMBLE_MISSION_RECORDS_SCHEDULER_MINT`` stays default-OFF. No code change. |

### PR3 carries (NOT in PR2 — the boundary is explicit)

* the D1 routing flip (the commission-row routing + the
  ``report_injections`` writers);
* the C3 backstop's same-PR patch (the duplication firewall — it MUST
  land in the same release as the routing flip, per the architecture
  map §7.4);
* the commission reconciliation sweep (the eternal-mission escape);
* the committing backfill run + the retention story;
* the multi-commissioner routing tie-break (a USER decision).

---

## (b) The census is a UNION, not a replacement (deliberate deviation)

Proposal-b §3 D2(a) says the census is "a DB read **replacing** the
tool-name-literal walk". PR2 ships the **union** of the record's
answer and the legacy scan's answer.

**Why.** The record's commission rows are minted at the WATCHER
boundary — the ``send_message`` lane's tree-guard means a send to a
**NON-CHILD** target mints NO watcher and therefore NO commission row
(PR1's adoption note (2)). A replacing census would therefore read
"no delegation" for a mission that DID delegate — un-arming the gate
for exactly the missions the D2 premature-settle class is about. The
same holds for a delegation whose tool call was folded away by a
compaction (the list walk cannot see it; the record remembers).

**The cost.** The record's contribution is ADDITIVE — it can only arm
the gate, never disarm it. That is the safe direction for a
premature-settle fix, and it preserves the legacy detection exactly.

**What the record buys.** The legacy scan is re-anchored by a new user
message (the window moves); the record's commission rows belong to the
EPOCH. A mission that delegated and then received a new user message
keeps its census ON with the record consulted — the re-anchoring
blindness closes.

---

## (c) The epoch-boundary ledger clear is armed with ``enforce``

Proposal-b §2.4 generalizes the clear from "fresh-episode revive only"
to "every recorded boundary". PR2 implements it, **gated on
``enforce``**:

* the mode ladder's ``dry`` contract is "mint + stamp + log
  everything" with the gate on legacy behavior, and the clear IS a
  gate-visible behavior change (it resets the deny bound);
* so the clear rides the SAME operator promotion as the reads
  (``ENSEMBLE_MISSION_RECORDS=enforce`` + restart).

The epoch TAGGING of the ``denial_epochs`` entries rides the same
promotion (the tag needs the mission id, which only the enforce-mode
reads produce). The ``deny_bound`` counts only current-epoch entries
because the boundary clear resets the counter; the replay-proofing
WITHIN an epoch (the uuid5 fingerprints) is untouched; the one-shot
sentinel consumption keyed by message id stays as the in-graph half.

**What deliberately does NOT reset:** the withhold budget (per
dispatch) and the run-deny cap (per run) — already scoped tighter than
the epoch.

**Promotion note:** the ledger clear is not the only enforce-gated
behavior — the L3 mission leg's HOLD has no terminator until PR3 (§(f)
below). Both ride the same promotion, so the enforce promotion is
gated on PR3 as a whole.

---

## (d) The F1/F2 carries (from PR1's report)

* **F1** — the open-epoch partial UNIQUE index: at most ONE ``open``
  ``mission_records`` row per anchor. Three sites, byte-identical name
  (``ux_mission_records_anchor_open``) and predicate
  (``status = 'open'``): the SQLite migration, the SQLModel
  ``__table_args__``, and the PostgreSQL ``_ensure_postgres_columns``
  mirror. The migration's idempotent normalization UPDATE closes the
  crash window in which the ``job_continue`` lane had inserted a new
  epoch but not yet superseded the prior one — and the continuation
  mint now supersedes BEFORE inserting (F1's ordering constraint).
* **F2** — the amend guard: the founding-message attach and the
  re-founding amendment touch ONLY an ``open`` epoch (the
  ``dependency_watchers`` PENDING→FIRED guarded-UPDATE shape). A
  closed epoch's anchor is immutable.

---

## (e) The mode ladder after PR2

``ENSEMBLE_MISSION_RECORDS = off | dry | enforce`` (restart-read, the
cached house-switch pattern):

* ``off`` — no mints, no reads.
* ``dry`` — **the ship default**: mints + stamps + logs; every read
  site stays on legacy behavior (zero behavior change; the soak
  posture).
* ``enforce`` — the read sites consult the records, with the legacy
  behavior as the declared fallback leg. The epoch-boundary ledger
  clear is armed HERE (see (c)).

The ``enforce`` promotion is an operator action: set the env var and
RESTART (the knob is restart-read by design) — **but it is GATED on
PR3's commission terminator: see §(f) above. Do not promote to
``enforce`` before the reconciliation sweep lands, or missions with
commission rows hold until their epoch closes.**

## (f) ENFORCE-PROMOTION PRECONDITION — the commission TERMINATOR (PR3)

**DO NOT PROMOTE TO `enforce` BEFORE THE PR3 TERMINATOR LANDS.**

The PR2 read flip makes the mission record LOUD: the L3 root-completion
gate's mission leg (`child_reports.py::_root_completion_gate`) HOLDS the
terminal publish while the open mission has an OUTSTANDING commission
row, and the 5th R2 input keeps ``c_quiet`` False for the same mission
(the deny band does not fire either).

**PR2 ships NO writer that retires a commission row.** The terminators
exist as record-side seams only:

* `MissionRecordService.satisfy_commission` (``outstanding → satisfied``)
* `MissionRecordService.abandon_commission` (``outstanding → abandoned``)

…and **neither has a production caller** (PR1 shipped them as
record-shape-complete seams; the sweep that calls them is the PR3
reconciliation sweep). So in enforce mode today:

* a minted commission row stays ``outstanding`` for the life of the
  epoch;
* the root publish is HELD for that epoch (the L3 leg), and
* the mission cannot reach its quiet state (the 5th R2 input).

**Consequence:** promoting to `enforce` before PR3's terminator lands
means **every mission with a commission row holds until its epoch
closes** — the "eternal mission" class (proposal-b §8) with no escape
valve. PR2's default is ``dry`` precisely so this cannot happen by
accident.

**Promotion checklist (the operator's order):**

1. Land PR3's commission reconciliation sweep (the terminator: the
   ``satisfied`` write on delivery evidence + the ``abandoned`` write on
   worker-terminal / TTL, with the fail-MISS probes).
2. THEN flip ``ENSEMBLE_MISSION_RECORDS=enforce`` + restart.

The PR3 sweep is NOT part of PR2 and was NOT implemented here — the
PR3 fence (the D1 routing flip, the C3 backstop, the reconciliation
sweep, the committing backfill, the multi-commissioner tie-break) is
intact. This section only records that the enforce promotion is
**gated on PR3**, not merely "accompanied by" it.

---
