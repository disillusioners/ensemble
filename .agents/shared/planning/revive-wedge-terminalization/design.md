# Revive-Wedge Terminalization — Core Design (Component A)

Base: worktree `/home/nea/ensemble-src-wt-revive-wedge`, branch `fix/revive-wedge-terminalization`, HEAD `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` (exact tag `v0.31.54`; **NOT `origin/latest`** — HISTORY: at repair time `origin/latest` = `ed8ab508c` = `v0.31.56`, two tags ahead of the base; base→tip drift over this lane's touch set = `.agents/tester/PACKS.md` only, +2/−2. **As fetched 2026-11-26T00:27Z: `origin/latest` = `f83fb5d81`; `v0.31.59` IS an ancestor; `v0.31.60` untagged — RE-DERIVE at cut time**). All `file:line` anchors below were re-verified by CONTENT MATCH at this base by the plan writer (2026-11-25); the canonical checkout `/home/nea/ensemble-src` was never read. This is a DESIGN artifact — no source edits, commits, builds, test runs, config flips, or live-instance actions were performed. Staged release only, NO promote (the ceremony is the Emperor's).

**Scope**: Component A = the guard narrow + terminalization decoupling + sweep riders + their tests. Component B (observability, `observability.md`, owned by a parallel worker) is disjoint (§6).

---

## §1 VERIFIED MECHANISM

### 1.1 The live guard (authoritative, inlined)

`daemon/services/child_reports.py`, `_process_child_completion_db_sync` (def at `:3636`, signature `instance_id, completed_message_id: str | None = None, last_content: str = ""`), mint-site guard at `:4654-4696`. Verbatim predicate (`:4677-4690`):

```python
existing_report = session.exec(
    select(MessageQueue)
    .where(
        MessageQueue.source.startswith(
            f"internal_report:{instance_id}:",
            autoescape=True,
        )
    )
    .where(MessageQueue.status.in_([
        MessageStatus.READY.value,
        MessageStatus.PROCESSING.value,
        MessageStatus.COMPLETED.value,
    ]))
).first()
if existing_report is not None:            # :4691
    should_send = False                    # :4692
    skip_reason = "idempotency_skip"       # :4693
else:
    should_send = True                     # :4695
    skip_reason = "all_checks_passed"      # :4696
```

Early return (`:4698-4705`), BEFORE the terminal stamp:

```python
if not should_send:                        # :4698
    logger.info(f"Instance {instance_id[:8]}... completion report skipped: reason={skip_reason}")  # :4699
    return _ChildCompletionDbResult(
        outcome="idempotency_skip", instance_id=..., agent_id=..., parent_id=...,
    )
```

### 1.2 The mint (where the marker is written)

`child_reports.py:4959-4974`. Fresh wake-row id `report_message_id = str(uuid.uuid4())` (`:4959`); source format string VERBATIM (`:4967`):

```python
source=f"internal_report:{instance.instance_id}:{completed_message_id}",
```

The COLON terminator is load-bearing (comment `:4938-4958`; regression pin `tests/repositories/test_report_injection_prefix_boundary.py`): a sibling whose id *extends* the child's id yields `internal_report:{child_id}2:...` — the char after the id is `2`, not `:` — so it does not alias.

### 1.3 The terminal stamp (downstream of the guard)

Non-root guarded CAS at `child_reports.py:4901-4917`: `UPDATE instances SET status=COMPLETED, updated_at=now_iso, last_activity_at=now_dt, version=version+1 WHERE instance_id=:id AND status NOT IN (paused, completed, error)`; `rowcount==0 → idempotency_skip` (`:4918-4936`). It is reached ONLY when `should_send` is True — i.e. never on the revive-wedge path.

### 1.4 The full wedge chain (all anchors verified)

1. Child completes a turn → report minted (`:4959-4974`) → child stamped COMPLETED (`:4901`).
2. `send_message` revives the terminal child → `status=RUNNING` + `last_activity_at=now_utc_naive()` + `version+1` (`instance_messaging.py:2942-2952, 3090-3093`; mirror `manager.py:12652-12676`).
3. Revived child re-completes → `_process_child_completion_and_notify_parent` (`:3283`) → `_process_child_completion_db_sync` (`:3636`).
4. Terminal-entry guard (`:3731-3875`) does NOT fire: `instance.status in (COMPLETED, ERROR)` (`:3770`) is false — the row is RUNNING. (Its terminal-row re-arm exception at `:3786-3862` is irrelevant here.)
5. Pending-message gate (`:4579-4601`) passes; mint-site guard (`:4677-4696`) FIRES because the prior report row still exists under the OLD anchor with status COMPLETED (the widened guard includes COMPLETED).
6. Early return (`:4698-4705`) → `outcome="idempotency_skip"`, NO status flip. Child stays `running` forever.
7. `_dispatch_post_commit_side_effects` (`:5797`) runs the B4 cycle-2 re-mint (`:6551-6630`): gated on `outcome == "idempotency_skip"` (`:6563`) AND `inst.status == InstanceStatus.COMPLETED.value` (`:6581`). The instance is RUNNING → the re-mint is a no-op. **Chicken-and-egg**: terminalization needs the mint guard to pass; the re-mint needs terminalization.
8. Shape-B stale-running sweep selects the child (age leg keys on completion evidence, `stale_running_sweep.py:1076-1104`, NOT on `last_activity_at`), drives the SAME chain (`:1207`), gets `idempotency_skip`, logs `class=drive-no-effect` (`:1260`). Live proof: 3 consecutive ticks 07:13/07:18/07:23Z with `completion report skipped: reason=idempotency…` (the `:4699` line).

### 1.5 Commit 58fc594eb — before/after (verified from `git show`)

Commit `58fc594eb` ("fix(rider): b4 verification nits — per-child mint guard, holding sentinel, ORDER BY, typed reason"; touches `child_reports.py` +80, `stale_running_sweep.py` +218, rider pins +705) widened BOTH guard sites:

OLD (anchor-exact, with recipient predicate), at the live site:
```python
.where(MessageQueue.instance_id == (report_recipient_id or inst_check.parent_id))
.where(MessageQueue.source == f"internal_report:{instance_id}:{completed_message_id}")
```
NEW (per-child lifetime prefix, recipient predicate dropped), at the live site:
```python
.where(MessageQueue.source.startswith(f"internal_report:{instance_id}:", autoescape=True))
```
and the status set `[READY, PROCESSING, COMPLETED]` was attached. The dead-code twin (`:1400-1412`) got the identical widening.

**Why widened (must be preserved)**: a b4 candidate whose PRIOR report was still PENDING under a DIFFERENT anchor (different `completed_message_id`, i.e. a different **anchor token** — NOT a turn identity; see §1.6) slipped past anchor-exact and minted a SECOND report row + SECOND obligation for one child (probe: 2 obligations / 1 child; parent got 2 reports). The duplicate is CROSS-ANCHOR with an UNDELIVERED prior report. **A pure anchor-exact narrow RE-OPENS this hole** — see §2 ARM B, which exists precisely to keep it closed.

### 1.6 The anchor is the CALLER'S PER-CALL TOKEN (corrected 2026-11-25, review C3)

**The prior claim in this section — "`BaseMessage.id` = `completed_message_id` = the ANCHOR", "the anchor IS reconstructible at check time", "a per-turn key is reconstructible" — is WITHDRAWN: it was measured and it is WRONG.** The anchor is whatever token the CALLER supplies as `completed_message_id`; the guard compares it character-for-character, so the anchor's **provenance** — not a turn identity — decides which arms hit.

| Entry path | Anchor actually supplied | Evidence (content match at base `35ec50e3c`) |
|---|---|---|
| Natural completion (resume-driven turn) | a **fresh uuid4 minted per resume** — `message_id = str(uuid.uuid4())`, comment *"Create a fresh message_id for tracking (not enqueued, just for internal tracking)"* | `daemon/manager.py:16510-16512` → passed at `:16758` → `child_reports.py:3283` → `:3534/:3541` → param `:3639` → guard `:4677` / mint `:4967` |
| Router / deferred re-entry | the **original** anchor (the obligation-triple member) — **the only path that re-supplies the SAME *non-degenerate* anchor by design** — two other paths CAN repeat a token (the degenerate `""` on the root stale-task path, `manager.py:9044-9046`, and a re-drive's same `latest_msg_id`, §2.4), both base-equivalent behaviour | `daemon/manager.py:12698` (`_reenter_completion_async`) → `:12738-12739`; the anchor is the obligation-triple member (`daemon/repositories/report_injection/models.py:147-151`) |
| Root stale-task failure | `message_id or ""` | `daemon/manager.py:9044-9046` |
| Sweep drive | `latest_msg_id or ""` — the newest **COMPLETED `message_queue` row id** for the child (`ORDER BY completed_at DESC LIMIT 1`) | `daemon/services/stale_running_sweep.py:1198-1199`, the call `:1207-1209`, the helper `:1266-1275` |
| Strand-healer drives (2) | `latest_msg_id or ""` | `daemon/services/mission_strand_healer.py:772-774`, `:1316-1318` |

**Still true (the id-space fact that matters):** the anchor is threaded from `:3283` → `:3534/:3541` → param `:3639` → guard `:4677` / mint `:4967`, and the wake row's own `MessageQueue.message_id` is a fresh uuid4 at `:4959` (`report_message_id`), the row's own PK, and it is **never** used as a guard key. **No id-space mismatch forced the prefix widening** — it was a deliberate cross-anchor-duplicate choice (§1.5). **What is now WRONG in the prior text:** the anchor is NOT `BaseMessage.id`, it is NOT a per-turn key, and it is NOT stable across natural-path calls (a fresh uuid4 each time) ⇒ **ARM A is a same-ANCHOR (same-token) guard, not a same-turn guard** (§2.2), and the duplicate-freedom that holds on every path is the real invariant in §3.4 (terminal-entry guard + CAS-before-mint atomicity + per-parent lock).

**The house's own vocabulary names this hazard** (`daemon/repositories/message_queue/repository.py:288-293`): *"the PREFIX ledger is the cross-path check that survives the F-2 wedge's id-space mismatch (`BaseMessage.id` vs `MessageQueue.message_id`)"*. (Its in-code line-pointer `child_reports.py:3498-3507` is itself stale — that region is the parent-lock comment; `child_reports.py` has no live `source ==` equality check, only the two prefix guards at `:1403` and `:4680`.) **Out of this lane's touch set — flagged for a future lane; no code edit here.**

### 1.7 CLAIMS CORRECTED (body wins over the brief)

- **(a)** The brief's "~1394-1419" points at the **dead-code** helper `_should_send_completion_report` (def `:1279`, its `existing_report` SELECT `:1400-1412`, docstring `:1282-1297` declares it dead in production). The **LIVE** guard is the inlined one at `:4654-4696` (query `:4677-4690`, decision `:4691-4696`, early return `:4698-4705`). All design predicates target the LIVE site; the twin is handled in §5.
- **(b)** The brief's "frozen at epoch-start" is imprecise. There is NO epoch-start freeze write. `last_activity_at` is frozen because it is written **only on lifecycle events, never on turn progress** (`mission_live_guard.py:355-358` verbatim: "``last_activity_at`` is NOT written on turn progress"), and **REVIVE restamps it** (`instance_messaging.py:3090-3093`; `manager.py:12674`). `task.completed_at` and `message_queue.completed_at` DO advance (sweep aliases `:322-332`).
- **(c)** The brief/POK premise "the sweep cannot select the wedge" is **WRONG**. The age leg keys on COMPLETION EVIDENCE (`stale_running_sweep.py:1076-1104`: `task_completed_at`/`message_completed_at`, `newest_age = min(ages)`, skip only if `< age_bound`), so the wedge IS selectable past 7200 s — live log: 3 consecutive ticks. What the sweep **cannot** do is CURE: the chain it drives is structurally blocked (§1.4 step 6). The fix is in the chain, not in selection.

---

## §2 THE NARROW — EXACT SEMANTICS (core deliverable)

### 2.1 Policy (one sentence, do not re-open)

**At most one OUTSTANDING (undelivered) report obligation per child; a DELIVERED prior report does not block a new turn's report.** This is NOT "one report per child, ever" (§5).

### 2.2 The guard as a TWO-ARM disjunction

`SUPPRESS MINT IFF (ARM_A OR ARM_B)`. Each arm is stated with name, purpose, exact Python predicate, exact SQL predicate, status set, and anchor. The strings in this subsection are the canonical §7 strings — character-for-character stable.

**ARM A — same-ANCHOR (same-token) duplicate.**
- Purpose: idempotency for the CURRENT ANCHOR (a report already minted for this exact anchor — READY, PROCESSING, or already COMPLETED — must not be minted twice). **The anchor is the caller's per-call token (§1.6), NOT a turn id.**
- **Load-bearing on exactly ONE path (review C5):** the router/deferred re-entry (`daemon/manager.py:12698` → `:12738-12739`) is the only path that re-supplies an IDENTICAL non-degenerate anchor. There, with the prior report already DELIVERED (COMPLETED ∉ `S-B`), **ARM A alone** stands between the chain and b4's exact shape (2 obligations / 1 child, parent gets 2 reports) — the write-once index cannot arbitrate it (it keys on `(parent_instance_id, child_instance_id, child_message_id)`, `daemon/repositories/report_injection/models.py:234-242`, so a different anchor is a different obligation, not a conflict). Non-vacuity is proved by mutation leg **L2c** + pin **NEW-5**.
- Python (canonical `P-ARM-A-PY`):
  `MessageQueue.source == f"internal_report:{instance_id}:{completed_message_id}" and MessageQueue.status.in_([MessageStatus.READY.value, MessageStatus.PROCESSING.value, MessageStatus.COMPLETED.value])`
- SQL (canonical `P-ARM-A-SQL`):
  `source = 'internal_report:' || :child || ':' || :anchor AND status IN ('ready','processing','completed')`
- Status set `S-A = ('ready','processing','completed')`. Anchor: FULL key `child + ':' + completed_message_id`.

**ARM B — outstanding obligation (b4's protection).**
- Purpose: a prior report for this child is still UNDELIVERED (READY/PROCESSING) under ANY anchor — suppress a second mint so the child carries at most one obligation. This is the exact duplicate b4 fixed; it stays.
- Python (canonical `P-ARM-B-PY`):
  `MessageQueue.source.startswith(f"internal_report:{instance_id}:", autoescape=True) and MessageQueue.status.in_([MessageStatus.READY.value, MessageStatus.PROCESSING.value])`
- SQL (canonical `P-ARM-B-SQL`):
  `source LIKE 'internal_report:' || :child || ':%' ESCAPE '/' AND status IN ('ready','processing')`
- Status set `S-B = ('ready','processing')`. Anchor: child PREFIX with colon terminator; SQLAlchemy `autoescape=True` neutralises `%`/`_` inside the id (raw SQL needs the matching `ESCAPE` clause). **`retrying` is deliberately EXCLUDED from `S-B`** (leader ruling 6): a report row in `retrying` (a live `message_queue` status — `daemon/repositories/message_queue/models.py:47`, writer `message_queue/repository.py:678`) does NOT block a cross-anchor second mint, because a stuck `retrying` row must never become a new forever-block class. The exclusion is residual **R-K4**, made observable by `P-RETRYING-INFO-LOG` (§2.4).

**Mint source format (canonical `P-MINT-SOURCE`)**: `internal_report:{instance.instance_id}:{completed_message_id}` — unchanged from `:4967`.

### 2.3 Truth table — the dimension is the ANCHOR TOKEN, not the turn

| # | Case | ARM A | ARM B | Suppress mint? | Child terminalizes? | Report rows for child |
|---|------|-------|-------|----------------|---------------------|-----------------------|
| i | **SAME anchor**, prior report COMPLETED (the router re-entry of a DELIVERED obligation) | HIT | miss (COMPLETED ∉ S-B) | YES (A) | YES (via §3) | 1 (no new) |
| ii | **SAME anchor**, prior report READY/PROCESSING (the same obligation, still outstanding) | HIT | HIT | YES (A and B) | YES (via §3) | 1 (no new) |
| iii | **DIFFERENT anchor**, prior report COMPLETED (DELIVERED) | miss | miss (COMPLETED ∉ S-B) | **NO** | YES (via §3) | **2** — the new-anchor report is minted AND delivered to parent |
| iv | **DIFFERENT anchor**, prior report READY/PROCESSING — b4's duplicate | miss | HIT | YES (B) | YES (via §3) | 1 (b4 duplicate stays blocked; the suppressed call's report is **LOST** — R-K2, made observable by `P-ARM-B-SUPPRESS-LOG` / `P-ARM-B-SUPPRESS-COUNTER`) |
| v | Sibling child id that is a prefix-extension of this child (`rider-child2` vs `rider-child`; `tests/unit/services/test_stale_running_sweep_rider_pins.py:66`) | miss (exact key) | miss (colon boundary: `...:rider-child2:` ≠ `...:rider-child:`) | NO | YES | 1 new for the real child; sibling's row untouched |

Rows (i)/(ii) are the same-ANCHOR idempotency path — reachable **only** on the router/deferred re-entry (§1.6), the one path that re-supplies an identical non-degenerate token; on every other path a re-completion of the same turn is indistinguishable from a new turn. Row (iii) is the planned cure consequence (§5). Row (iv) is why a pure anchor-exact narrow is a DEFECT. Row (v) is the colon-boundary invariant (`child_reports.py:4938-4958`, `tests/repositories/test_report_injection_prefix_boundary.py:113,141`).

**Read the table by ANCHOR, not by turn (§1.6).** On the natural path every call supplies a FRESH uuid4 token (`manager.py:16510-16512`), so "the same turn called again" is indistinguishable from "a new turn" — both are *different anchor + delivered prior* ⇒ row (iii) ⇒ mint. Only the router re-entry re-supplies an identical non-degenerate anchor (rows i/ii). The duplicate-freedom that holds on every path is the real invariant in §3.4, not ARM A.

### 2.4 Evidence query — both forms, one shape chosen

SQLAlchemy (single query with OR; `or_`/`and_` from `sqlalchemy`):
```python
child_prefix = f"internal_report:{instance_id}:"
anchor_exact = f"internal_report:{instance_id}:{completed_message_id}"
existing_report = session.exec(
    select(MessageQueue).where(
        or_(
            and_(MessageQueue.source == anchor_exact,
                 MessageQueue.status.in_(S_A_PY)),
            and_(MessageQueue.source.startswith(child_prefix, autoescape=True),
                 MessageQueue.status.in_(S_B_PY)),
        )
    )
).first()
suppress_mint = existing_report is not None
```

Raw SQL (equivalent; `:child`, `:anchor` bound; `ESCAPE '/'` to mirror `autoescape=True`):
```sql
SELECT message_id FROM message_queue
WHERE ( source = 'internal_report:' || :child || ':' || :anchor
           AND status IN ('ready','processing','completed') )
   OR ( source LIKE 'internal_report:' || :child || ':%' ESCAPE '/'
           AND status IN ('ready','processing') )
LIMIT 1;
```

**Decision: ONE query with OR, not two selects.** Justification: (1) both arms read the same table with the same intent (a single "is there a blocking report row?" question); one round-trip and one scan. (2) ARM A's equality is a strict subset of ARM B's prefix **in source coverage**, so a two-select short-circuit saves nothing on the common case and adds a second query. (3) The caller needs `suppress_mint: bool` plus `skip_reason` (which stays `"idempotency_skip"` for §3's b4 gate); the **per-arm classification is now REQUIRED** by `P-ARM-B-SUPPRESS-LOG` (the R-K2 observability condition) and is a cheap Python discriminator on the returned row's `source`/`status` — **not** a second query (see below).

**ARM-A / ARM-B discriminator (no second query — required by `P-ARM-B-SUPPRESS-LOG`).** The single returned row classifies the suppression: it is an ARM-B hit iff `existing_report.status ∈ S-B` (`ready`/`processing`); the ARM-A hit is `existing_report.source == anchor_exact`. At the guard the chain captures the classification plus the blocking row's `source` / `message_id` / `status` into locals (no API change): the ARM-B classification carries the outstanding row's anchor (parsed from its `source` by `P-ANCHOR-PARSE` — prefix-strip, never `.split(":")`) and `message_id` (the outstanding marker id) for the log record and the counter (§7 `P-ARM-B-SUPPRESS-LOG` / `P-ARM-B-SUPPRESS-COUNTER`); both are emitted in the SUPPRESS BRANCH after the CAS — never at the guard, never on the defer path (emission rules (i)-(v), §3.1 step 4). **Documented edge:** when a same-anchor COMPLETED row and a cross-anchor outstanding row coexist, the single-row fetch may return either, so a `loss=1` may be logged for a suppression that lost nothing — an observability-only over-count, never a control signal (removing it would need a multi-row fetch; out of the narrow).

**ARM-A logging and the `retrying` probe (leader rulings 7 / 6 — repair 005).** Exactly ONE suppress record per ACTUAL suppression — emitted in the SUPPRESS BRANCH (normative rules (i)-(v): §3.1 step 4; the guard captures locals only, the defer path emits nothing): an ARM-B classification emits `P-ARM-B-SUPPRESS-LOG`; an ARM-A hit with NO ARM-B hit (row (i)) emits `P-ARM-A-SUPPRESS-LOG` (same loud shape — child, both anchors, the matched marker id). `P-RETRYING-INFO-LOG` is a SEPARATE, best-effort, mint-path-only existence probe (child prefix + `status = 'retrying'`): it never influences `suppress_mint`, it runs only when the mint proceeds, any probe error is swallowed, and it costs one additional prefix scan per MINT (never per suppressed call) — so a completion that MINTS pays TWO unindexed scans (the guard + this probe) and a suppressing completion pays ONE (stated plainly, repair 006). It is the ONE sanctioned second query on this path and is not the discriminator.

**Index/perf implication**: `message_queue.source` is **UNINDEXED** (`daemon/repositories/message_queue/models.py:64`: `source: str | None = Field(default=None)`), while `instance_id` IS indexed (`:61`). The guard therefore scans `message_queue` (the current widened guard already does — it dropped the recipient predicate in 58fc594eb). The guard scan runs ONCE per completion, not per sweep tick, and is acceptable — and, stated plainly (repair 006), a completion that MINTS pays a SECOND unindexed prefix scan (the `P-RETRYING-INFO-LOG` probe), so the mint path is two scans and the suppress path one. **No sweep-side query variant ships in this release** — the sweep file is not modified (§4.1), so no indexed `instance_id = parent` form exists here (the withdrawn cost-guard pre-flight was the only such variant — §4.2, DELETION RECORD). **No new index, no migration** in this release (§3, §4).

### 2.5 Id-space analysis

**The anchor is the CALLER'S PER-CALL TOKEN — not a `BaseMessage.id`, not a per-turn key** (corrected 2026-11-25, review C3; the measured provenance table is §1.6). `completed_message_id` is passed into `_process_child_completion_db_sync` at `:3639`, threaded at `:3534/:3541`, and is the anchor written at `:4967`; its VALUE comes from the caller — a fresh per-resume uuid4 on the natural path (`manager.py:16510-16512`), the original obligation-triple member on the router re-entry (`manager.py:12698` → `:12738-12739`), the newest completed queue-row id on the sweep drive (`stale_running_sweep.py:1198-1209`), `latest_msg_id or ""` on the two strand-healer drives (`mission_strand_healer.py:772-774`, `:1316-1318`) and `message_id or ""` on the root stale-task path (`manager.py:9044-9046`). ARM A therefore needs **no new state** — it compares the incoming token against the stored anchor — but it is a **same-ANCHOR** guard, not a same-turn one (§2.2). ARM B uses only the child prefix and is anchor-agnostic. The wake row's own uuid4 is never used as a guard key. **No id-space mismatch blocks the narrow** — but the earlier claim that the anchor is a reconstructible per-turn key is **WITHDRAWN** (superseded by §1.6).

### 2.6 Test impact — exact list

**Existing tests that STAY GREEN unchanged** (verify, do not edit):
- `tests/unit/services/test_stale_running_sweep_rider_pins.py::TestNit1MintSitePrefixGuard` — all three: `test_prior_pending_report_under_a_different_anchor_blocks_a_second` (prior READY under `anchor-A` → ARM B HIT → still suppressed; `:413-451`), `test_a_genuinely_new_child_report_still_mints` (no prior → both miss → mints; `:454-474`), `test_a_sibling_child_id_does_not_alias_onto_the_child` (colon boundary; `:477-507`). **Correction to the research brief**: these do NOT need updating — the two-arm design preserves their asserted behavior; only the module-docstring NIT-1 narrative (`:6-16`, "PER-CHILD PREFIX invariant") needs an additive clarifying note that the invariant is now "at most one OUTSTANDING obligation".
- `tests/unit/services/test_b4_child_report_obligation.py`, `test_report_obligation_emission_pins.py`, `tests/postgres/test_obligation_triple_discriminator_pg.py`, `test_report_deferred_savepoint_pg.py`, `tests/unit/services/test_terminal_row_status_strand.py`, `tests/repositories/test_report_injection_prefix_boundary.py` — b4 duplicate protection / boundary / terminal-row pins; unchanged. **Where they run (repair 005):** the colon-boundary pin rides **L1** with its own PACKS row (leader ruling 3); the two PG nets ride **L4**; `test_b4_child_report_obligation.py`, `test_report_obligation_emission_pins.py` and `test_terminal_row_status_strand.py` are the **L2N stay-GREEN net** (gate-matrix §3N — run at BASE and MERGE, not merely claimed).
- `tests/job_queue/test_fp4_premature_terminal_pins.py`, `test_round2_council_fixes.py`, `test_job_result_summary_and_gate.py`, `tests/unit/services/test_child_reports.py::TestNaturalCompletionRacingRecoveredMarker::test_natural_completion_races_recovered_pending_marker` (the "F4-race" pin — a nickname, not a pytest node id; `:941` asserts `idempotency_skip`) — exercise the chain's terminal paths; the suppress path still returns `idempotency_skip` (§3), so they stay green. The three `tests/job_queue/` files are in the **L2N stay-GREEN net**; the F4-race pin is in the L1 `$SUITE`.

**Tests to ADD (new pins, alongside the change) — one file per pin, so the census is unambiguous:**

| Pin | File | Assertion |
|---|---|---|
| NEW-1 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` (class `TestReviveWedgeNarrow`) | a DIFFERENT anchor + prior report COMPLETED under a different anchor → exactly TWO report rows, the second source uses the new anchor, child status COMPLETED (row (iii)). **The docstring must state that the two rows are the INTENDED consequence of the new policy (PL-6 variant B / K11), not a leak** (review A10). |
| NEW-2 | `tests/unit/services/test_child_reports.py` | mint-suppressed cases (rows (i)/(ii)/(iv)) STILL terminalize the child to COMPLETED — the decoupling pin. |
| NEW-3 | `tests/unit/services/test_child_reports.py` | the outcome stays `"idempotency_skip"` on the suppress path AND the B4 re-mint path is reached with `inst.status == COMPLETED` (§3 verification pin). |
| NEW-4 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` | mutation leg — revert ARM B to anchor-exact-only → `test_prior_pending_report_under_a_different_anchor_blocks_a_second` goes RED (proves ARM B is non-vacuous). |
| NEW-5 | `tests/unit/services/test_stale_running_sweep_rider_pins.py` | **ARM A is load-bearing** (review C5): the SAME anchor re-supplied (the router-re-entry shape) with the prior report already DELIVERED → NO second report row AND the child terminalizes (row (i)). RED at base (no terminalization at base); RED under mutation **L2c** (collapse the guard to ARM B alone). |
| NEW-6 | `tests/unit/services/test_child_reports.py` | **the real duplicate-freedom invariant** (review A6) — TWO methods. **NEW-6a (the L2d target):** invocation 1 mints + stamps COMPLETED; the prior report is marked **COMPLETED (delivered)**; invocation 2 with a DIFFERENT anchor and NO revive → still exactly ONE report row. GREEN at base and at merge; RED under mutation **L2d** (BOTH status-based mechanisms neutralized — the terminal-entry short-circuit `:3781-3784` AND the CAS rowcount-0 return `:4918-4936` — so the second invocation reaches the mint at `:4959`). **NEW-6b:** the prior report is marked **COMPLETED** BEFORE the revive half (a READY/PROCESSING prior is suppressed by ARM B — row (iv)), then an explicit revive + a new anchor → a second row. RED at base, GREEN at merge; GREEN under L2d. The COMPLETED-prior mark is load-bearing in both halves — a READY prior sets `suppress_mint=True` and returns before the mint even under L2d. |
| NEW-7 | `tests/unit/services/test_child_reports.py` | **the R-K2 observability condition** (review C1): exactly one `P-ARM-B-SUPPRESS-LOG` record per ARM-B suppression carrying child + both anchors + the outstanding marker id, `loss=1` on a different-anchor suppression and `0` on a same-anchor one; the counter moves by exactly 1 on the loss and by 0 otherwise; exactly one `P-ARM-A-SUPPRESS-LOG` record on an ARM-A-only suppression (row (i)) and none on an ARM-B-classified one; exactly one `P-RETRYING-INFO-LOG` record when a mint proceeds over a `retrying` obligation and none otherwise; concurrent counter increments lose no update (the lock); **(e) the DEFER CASE (repair 006):** an invocation whose guard sets `suppress_mint` and that then takes either defer guard (`outcome="child_still_running_defer"`) emits NO suppress record and leaves the counter UNCHANGED (+0); records are captured with pytest's `caplog` and counted by class — exactly ONE record per ACTUAL suppression. RED at base, GREEN at merge. |
| NEW-8 | `tests/postgres/test_child_reports_narrow_guard_pg.py` (**NEW file**, postgres-marked) | the narrowed evidence query on real PG: the new-anchor report IS minted and the child terminalizes (row (iii)). **RED at base** — the lifetime-prefix guard blocks the delivered-report + new-anchor mint, so the assertion that the mint occurs fails — and **GREEN at merge**. The target is a **sanctioned scratch DB** (`PG_TEST_DB=ensemble_test`): `ensemble_dev`/`ensemble_prod` are **FORBIDDEN by name** by `tests/postgres/conftest.py` (its shared `pg_engine` runs `drop_all` on the target), `SELECT current_database()` is asserted before the run, and a **SKIP is a leg FAILURE, not a green**. It owes **its own PACKS.md row** (plus one for each of the two stay-GREEN PG nets — none of the three is registered today). The full L4 spec is `plan.md §8 GAP-1` + `gate-matrix.md §5`. |

**The DRIVE shape IS covered (review A7) — stated, not inferred.** The b4 companion test drives the chain exactly as the sweep does — `_process_child_completion_and_notify_parent(CHILD, "", skip_repair=True)` (`tests/unit/services/test_stale_running_sweep_rider_pins.py:442-445`) — i.e. the empty-anchor + drive-style invocation, with a prior READY report under a different anchor, asserting exactly one row and the prior anchor (`:447-451`); and the row-(iii) drive shape (prior DELIVERED, different anchor ⇒ a second row is minted) is what NEW-1 pins in the same harness. So the drive shape is covered by the b4 companion + NEW-1 (mutation legs L2b and L2b(b2)); **ARM A's own non-vacuity is the missing leg — L2c + NEW-5** (review C5).

**Test invocation** (house-scrubbed, from `research-findings-conventions.md` B4): `cd $WT && ENSEMBLE_SELF_ENV=dev timeout 300 ./scripts/run_tests_scrubbed.sh <file> -p no:randomly -q`. New file under `tests/unit/services/` named `test_<topic>_pins.py`; register as ONE dated block + table row in `.agents/tester/PACKS.md` with exact invocation, `--collect-only` node census, fence statement, mutation self-proof (B2). Genuine-red-at-base procedure = B1 canon (two detached worktrees, per-tree venv, sorted FAILED-node-id digest, `comm` both directions).

---

## §3 DECOUPLING TERMINALIZATION FROM THE MINT

### 3.1 Exact control-flow change in `child_reports.py`

Seam at `:4691-4705`. Current: `existing_report is not None → should_send=False, skip_reason="idempotency_skip"` then `if not should_send: early return`. Change:

1. At `:4691-4696` (existing_report branch): set `suppress_mint = True` alongside `should_send=False / skip_reason="idempotency_skip"`. In the else branch set `suppress_mint = False`. Initialise `suppress_mint = False` before the guard (alongside `should_send`). **Also capture, in the same branch, the observability locals (no API change): the classification (`ARM_A` / `ARM_B`), the suppressed (incoming) anchor, and the blocking row's `source` / `message_id` / `status`; the anchor is parsed from the carried `source` by `P-ANCHOR-PARSE` (prefix-strip, never `.split(":")`). NOTHING is logged and the counter is NOT touched at the guard (repair 006 — emission rules (i)-(v), step 4).**
2. At `:4698`: change the early-return condition to `if not should_send and not suppress_mint:` — so the OTHER `should_send=False` paths (`no_completed_message_id` `:4590`, `pending_messages_exist` `:4606`, `instance_not_found` `:4606`, `no_parent_id` `:4651`) still early-return exactly as today; ONLY the existing_report (mint-guard) case falls through.
3. Let the fall-through traverse the tool-invocation branch (`:4707-4773`, which is not applicable to the wedge child — it has no `invoked_as_tool` marker), the active-children guard (`:4819-4828`) and pending-task guard (`:4858-4878`) (both pass for the wedge child — sweep legs 2/3/5 guarantee no live task/child/watcher), and REACH the CAS at `:4901-4917`. **Both defer guards return `outcome="child_still_running_defer"` at `:4874` BEFORE the CAS — on that path nothing is suppressed: NO suppress record is emitted and the counter is NOT touched (rules (ii)/(v), step 4).**
4. After a successful CAS (`rowcount != 0`), insert the suppress branch:
   ```python
   if suppress_mint:
       session.commit()
       # terminal observability emission (kept OUTSIDE the mint guard)
       _event_registry_record("event_bus:instance_completed", instance_id=instance_id)
       _event_registry_record("event_bus:child_completed",
                              instance_id=instance.parent_id, child_instance_id=instance_id)
       # ← THE emission point (repair 006): P-ARM-B-SUPPRESS-LOG / P-ARM-A-SUPPRESS-LOG, + the counter on loss=1 only
       #   — rules (i)-(v) below. NOTHING is emitted at the guard or on the defer path.
       return _ChildCompletionDbResult(
           outcome="idempotency_skip", instance_id=instance_id,
           agent_id=instance.agent_id, parent_id=instance.parent_id,
       )
   ```
   This is a DEDICATED return, NOT a fall-through to the final return at `:5775-5792`: that final return references mint-produced locals (`report_message_id`, `completed_parent_id`, `completed_parent_parent_id`, `parent_agent_id`, SSE hints) that do not exist on the suppress path. (The research brief's "guard the mint block with `if not suppress_mint:`" is directionally right but incomplete — the terminal return must be split, not merely the mint wrapped.)

   **Emission-point rules — NORMATIVE (repair 006; approver iteration-003 BLOCKING 2; leader ruling, option (a)).** The suppress records and the counter are emitted HERE — in the suppress branch, after the fall-through CAS has succeeded (`rowcount != 0`) and after `session.commit()` — and nowhere else: **(i)** exactly ONE record per ACTUAL suppression, emitted in the suppress branch — an ARM-B classification emits `P-ARM-B-SUPPRESS-LOG` (`loss=0` when the anchors are equal — row (ii)); an ARM-A hit with NO ARM-B hit (row (i)) emits `P-ARM-A-SUPPRESS-LOG`; **(ii)** the defer path (`outcome="child_still_running_defer"`, `:4874`) emits NO record and does NOT touch the counter — nothing was suppressed, nothing was lost (the retry may still mint later); **(iii)** the record carries the classification (ARM_A / ARM_B), the suppressed anchor, the blocking row's `source` and `message_id`, and the outcome (same-turn duplicate suppressed vs R-K2 loss) — carried from the guard across the fall-through via locals (no API change); the log format strings are UNCHANGED (the anchor is rendered from the carried `source` by `P-ANCHOR-PARSE`; the outcome is the `loss` field for ARM B and the record class for ARM A); **(iv)** `_ARM_B_SUPPRESS_LOSS_TOTAL` increments ONLY on an R-K2 loss — an ARM-B suppression with `loss=1`, where the newer turn's report was actually dropped — never on `loss=0`, never on ARM A, never on a defer; every increment and `total=` read runs under `_ARM_B_SUPPRESS_LOCK` (`threading.Lock`); **(v)** a re-invocation after a defer is a NEW invocation and may emit its own record. A commit failure raises before the emit, so a record exists only for a suppression whose terminalization actually committed.
5. If the CAS `rowcount == 0` (`:4918-4936`), keep the existing `idempotency_skip` return (another terminalizer won) — correct for both paths.

### 3.2 Side effects: INSIDE vs OUTSIDE the mint guard

| Side effect | Location | suppress_mint path |
|---|---|---|
| Terminal status flip (guarded CAS to COMPLETED, `last_activity_at`, `version+1`) | `:4901-4917` | **OUTSIDE — runs** (this IS the cure) |
| CAS rowcount==0 idempotency return | `:4918-4936` | OUTSIDE — runs |
| `session.commit()` | new suppress branch (and existing `:5762` on mint path) | OUTSIDE — runs |
| Terminal observability records `event_bus:instance_completed` / `child_completed` | existing `:5769-5773` | **OUTSIDE — emitted in the suppress branch** (content-free ids only) |
| Completion-report `MessageQueue` row (`source=internal_report:{child}:{anchor}`) | `:4959-4974` | **INSIDE — skipped** |
| `PROCESS_REPORT` Task + recipient fallback + dead-parent FAILED marking | `:4976-5110` | **INSIDE — skipped** |
| `ReportInjection` sync-outbox row + SAVEPOINT (F3) | `:5140-5300` | **INSIDE — skipped** |
| Parent cascade / SSE hints / `completed_parent_id` capture | `:5300-5762` | **INSIDE — skipped** |
| B4 cycle-2 re-mint (bus watcher obligation) | `:6551-6630`, async caller | **OUTSIDE — runs** via outcome (§3.3) |
| Async `regular_child_completed` completion hooks — `CompletionRegistry.complete` (`:6874`), SSE (`:6879-6943`), `on_complete` lifecycle hooks (`:6955-7018`), S1 child-keyed fire (`:7046`) | `:6874-7046` | **INSIDE the mint outcome — SKIPPED** (the suppress path returns `idempotency_skip`). Harmless for the STALE victims: their turn already completed and the wedge sits in the REPORT path, so no live waiter remains when the sweep drives them (iteration-002 note 3). For a LIVE row-(iv) suppression a waiter on that completion is NOT released by this invocation — residual **R-K6** (§8); the B4 bus-watcher emit still runs (row above). |

No new status writer: the CAS at `:4901` remains the single terminal-writer. No mirror reconciliation is added (see §4.4). No migration.

### 3.3 Outcome-value semantics — VERIFIED in code (not assumed)

- The suppress branch returns `outcome="idempotency_skip"` (NOT `"regular_child_completed"`). Verified at `_ChildCompletionDbResult` docstring `:205-210`: `"idempotency_skip"` is the outcome that feeds B4 cycle-2.
- `_dispatch_post_commit_side_effects` (`:5797`) reads `outcome = result.outcome` (`:5820`). The B4 block is gated `if outcome == "idempotency_skip":` (`:6563`), fetches `inst` (`:6566-6579`), and fires `_emit_terminal_for_child_instance_via_bus` **only when `inst is not None and inst.status == InstanceStatus.COMPLETED.value`** (`:6581`).
- PRE-fix: the early return (`:4698`) skipped the CAS, so `inst.status == RUNNING` → re-mint no-op → wedge. POST-fix: the suppress branch commits the CAS FIRST, so when `:6581` re-reads `inst`, status is COMPLETED → the corrective emit runs if a PENDING (parent, child) watcher exists. **The chicken-and-egg is broken.** Precise statement: the outcome string is unchanged; what changes is that the row is now COMPLETED by the time the re-mint checks it.
- Consequence: on the suppress path the parent does NOT get a new report row from the mint; it gets the terminal via the bus watcher emit (B4). On the NEW-turn path (row (iii)) the mint is allowed, so the parent gets the new report normally (§5).
- **The async `regular_child_completed` hooks are skipped on the suppress path** (`CompletionRegistry.complete` `:6874`, SSE `:6879-6943`, `on_complete` hooks `:6955-7018`, S1 child-keyed fire `:7046` — they key on the mint outcome). Harmless for the STALE victims (their turn already completed; no live waiter remains). For a LIVE row-(iv) suppression a completion-registry / `on_complete` / child-keyed waiter for THAT turn is not released by this invocation (the B4 bus-watcher emit still runs) — residual **R-K6**, §8.

### 3.4 Invariants

1. **Duplicate-freedom — the REAL invariant (corrected 2026-11-25, review C3/A6).** A child carries **at most one report row per episode**; a second row only after an explicit **revive** (a new episode, which the new policy intends to report). It is delivered by three EXISTING mechanisms — **not** by ARM A: the **terminal-entry guard** (`child_reports.py:3781-3874` — any chain invocation on a `COMPLETED`/`ERROR` child short-circuits with `outcome="idempotency_skip"` BEFORE the mint; its terminal-row/live-children exception `:3785-3863` re-arms to `WAITING_CHILDREN` and still does not mint); **CAS-before-mint atomicity** (`:4901-4917` before `:4959-4974`, one commit `:5762`); the **per-parent lock** (`:3524-3525`, `bus._get_parent_lock(instance_id)`) serializing the chain for one child. ARM A is the **same-ANCHOR** guard — load-bearing on ONE path only (the router re-entry; review C5); ARM B is the outstanding-obligation guard (b4). The write-once index keys on `(parent_instance_id, child_instance_id, child_message_id)` (`daemon/repositories/report_injection/models.py:234-242`) ⇒ it arbitrates **same-anchor** duplicates only. **Pinned by NEW-6a; non-vacuous under mutation L2d — the TWO-site mutation (terminal-entry short-circuit `:3781-3784` AND CAS rowcount-0 return `:4918-4936`); the per-parent lock is not a mutation target (it serializes concurrent invocations, not a sequential second one).**
2. At most one OUTSTANDING obligation per child **in READY/PROCESSING** — ARM B (`S-B`) is preserved. **`retrying` is deliberately EXCLUDED** (leader ruling 6 — a stuck `retrying` row must never become a new forever-block class); residual **R-K4**, observable via `P-RETRYING-INFO-LOG` (§2.4).
3. Terminalization never depends on a mint — the CAS runs on the suppress path.
4. No new `instances.status` writer; no new table/column; no migration; no constitution entry (`daemon/job_state/constitution.py` governs admission-state writers only, not `instances.status`).

### 3.5 REQUIRED ANALYSIS — must the RDRS backstop become per-TURN?

**Answer: NO. Do NOT change RDRS delivery-evidence to per-turn in this release. SQL touched: NO.**

Evidence:
- RDRS `find_completed_children_without_delivery` (`daemon/repositories/report_injection/repository.py:1235-1360`) is a **NO-ROW backstop**: it selects a COMPLETED child with ZERO delivery evidence — `~has_delivery_row` (parent-side `internal_report:{child}:%` prefix, any anchor any status, `:1345-1351`), `~has_injection_row` (any `report_injections` row for (parent, child), `:1366-1374`), `~has_fired_watcher` (`:1380-1396`). Its docstring (`:1276-1277`) says "delivery evidence is PER CHILD, never per anchor; one obligation per child" — that is the deliberate D1/RESUME_ROUTER fix for the 4× duplicate re-delivery (`:1247-1265`). The queue-side twin `find_wake_already_delivered_evidence` (`message_queue/repository.py:277-330`, callers in `report_delivery_recovery.py:2268,3369`) is the same per-child prefix check.
- Making evidence per-turn by exact anchor would RE-INTRODUCE the exact defect 58fc594eb/RDRS fixed: the true terminal anchor is the child's LAST CHECKPOINT message, NOT a `message_queue` row (`report_injection/repository.py:1250-1256`, `:1408-1420`), so exact-anchor matching "could never see the already-delivered evidence" → 4× duplicate re-delivery. The id spaces differ (§1.6).
- Under the narrow, the natural path mints a report per NEW turn (row (iii)); each mint writes a report row + injection row in the SAME transaction (`:4959-5300`). A "lost wake on a new turn" that leaves NO evidence is not reachable through the transactional outbox; and if an obligation is merely READY/PROCESSING (outstanding), ARM B suppresses a duplicate — that state is the obligation, not a loss. RDRS's per-child prefix correctly recognizes "this child's report was delivered" and avoids double-delivery.
- Residual (documented, accepted): RDRS cannot distinguish a lost wake on turn N from the delivered report of turn N-1 by prefix alone. This is intentional — RDRS is a no-row backstop, not a per-turn completeness auditor. If per-turn delivery auditing is ever wanted, it must key on the `report_injections.child_message_id` triple (which IS the mint anchor), never on the `message_queue.source` prefix — a separate future lane, not this release.

---

## §4 RIDERS — the sweep side

### 4.1 The drive becomes a CURING drive — the sweep file is NOT modified

With §2+§3 in place, `_drive` (`stale_running_sweep.py:1173-1264`) no longer re-enters a suppressed no-op: the chain now terminalizes the victim, so the sweep's **existing** post-drive re-read lands in `healed` (`:1230-1231`) + terminal-reason persist (`:1236-1238`) + the `shape-b` transition WARNING (`:1239-1246`) instead of `drive_no_effect` (`:1259`).

**Nothing in `daemon/services/stale_running_sweep.py` changes in this release.** Its counters (`healed`, `drive_no_effect`) and log lines are unchanged, and they already prove the cure per victim: `stale_running_sweep|shape-b: instance=… transitioned=completed reason=finalize_gap_steady_state`. **THE POINTLESS DRIVE STOPS BECAUSE THE DRIVE NOW CURES** — the victim leaves the candidate set after one tick and is never driven again.

`drive_no_effect` (`:1259-1264`) is retained for genuine no-effect (chain unwired, a concurrent terminalizer winning) — it must NOT be deleted.

### 4.2 The cost-guard pre-flight is REJECTED (measured — two independent reasons)

The prior draft of this design proposed a three-disposition pre-flight (`drive` / `cure` / `skip`) that skips the 73-message / 82 KB checkpoint read and calls the chain's DB half directly. Two measured facts kill it:

1. **No anchor ⇒ no terminalization.** Without the checkpoint read there is no `completed_message_id`. `None` takes the `:4579-4590` branch (`should_send = pending_count > 0`, `skip_reason = "no_completed_message_id"`) and returns at `:4698` **before** the guard at `:4654-4696` ever sets `suppress_mint` — the proposed "cure" would **NOT** terminalize: a second wedge of the same class, nested inside the cure path. (Passing `""` avoids the `None` branch but mints the degenerate anchor `internal_report:{child}:`.)
2. **The helper cannot see the live shape.** `find_wake_already_delivered_evidence(parent_id, child_id)` (`message_queue/repository.py:277-330`) is a **lifetime-prefix, any-of-{ready,processing,completed}** check on the indexed `instance_id = parent`. It returns `True` for the live-victim shape (prior report **COMPLETED under a DIFFERENT anchor**) — the shape that **must mint** (truth-table row iii). As a gate it would suppress the live victims' turn-2 reports and permanently lose the parent's outcome (PL-6 variant A — rejected).

**Cost accounting (the replacement):** the 2–7 s per-victim checkpoint read is paid **once per victim** (the first post-fix drive cures it; the victim then leaves the candidate set). Worst case at deploy: `DEFAULT_BATCH_CAP = 25` (`:184`) victims in the first tick — a one-time 50–175 s tick, no per-tick recurrence. The `cured_already_reported` counter, the `class=cured-already-reported` log and the appended `cured=%d` summary token are **WITHDRAWN** (ledger DELETION RECORD) — the live-victim shape heals as a normal `healed` **with a new report minted**, so that counter would not fire for the victims it was designed to count.

### 4.3 Selectability ledger — "selectable AND curable", measured

| Leg | Measured (content match at base `35ec50e3c`) | Consequence |
|---|---|---|
| Age leg | `stale_running_sweep.py:1077-1104` — stamps = `candidate["task_completed_at"]` + `candidate["message_completed_at"]`; `newest_age = min(ages)`; `newest_age < age_bound` ⇒ `class=young` skip | selection requires the **newest completion evidence** to be older than `age_bound` (default `7200` s, `:181`) |
| `instances.last_activity_at` | **never read by the sweep** (sole occurrence: module docstring `:8`) | the "frozen activity clock" does not gate selection; the completion-evidence clock does, and it clears at the bound |
| Class selectable? | **YES** — 3 consecutive live ticks drove the victims (`class=drive-no-effect`) | selection was never the blocker |
| Class curable? | **YES after this fix** — the drive lands in `healed` (§4.1) | the wedge stops |
| Post-fix young window | a pre-fix victim is not selectable until its newest completion evidence ages past 7200 s | accepted residual **R-K3**: ≤ 2 h per pre-fix victim at deploy time; no new victims post-fix |

### 4.4 Rider failure modes

- Chain unwired (`_child_reports_service is None`) → existing `[STALE_RUNNING_SWEEP_DRIVE_ERROR]` (`:1191`), `result.errors += 1` — unchanged.
- Drive raised → existing `[STALE_RUNNING_SWEEP_DRIVE_ERROR]` (`:1214-1220`), `result.drive_no_effect += 1` — unchanged.
- CAS rowcount 0 on the suppress path (a concurrent terminalizer won) → the suppress branch returns `idempotency_skip`; the sweep's post-read sees terminal → `healed`.
- Post-drive status re-read still RUNNING (unexpected) → `drive_no_effect` (existing `:1259`) + re-examined next tick, bounded by the cap.
- New-turn mint hits the obligation-triple IntegrityError → the existing F3 SAVEPOINT (`:5160-5300`) absorbs that one race; other IntegrityErrors re-raise → `drive_no_effect` + ERROR.
- **No new failure surface in the sweep file**: the pre-flight is withdrawn, so the sweep's code paths are exactly what they are today — the fix changes only the chain's outcome.


### 4.5 CURE-PATH EVALUATION — `finalize_boot_consistency_instance`

`daemon/repositories/task/repository.py:2195-2219`: `UPDATE instances SET status='completed', updated_at, version+1 WHERE instance_id=:iid AND status='running'` (+ optional `version` CAS). Precedent `boot_consistency_reconciler.py:455-459`.

**Ruling: NOT appropriate as the primary cure — REJECT for this release.** Reasons: (1) it introduces a SECOND `instances.status` writer, violating §3.4's "CAS at `:4901` is the single terminal-writer"; (2) it does NOT run the B4 cycle-2 re-mint (`:6551-6630`), so a PENDING watcher obligation would stay unhealed; (3) it does NOT reconcile the other 7 mirrors (`reconcile_turn_mirror`, `task/repository.py:2919`, over `ALL_8_MIRRORS`, `turn_transitions.py:26-29`); (4) the boot reconciler is boot-only (`boot_consistency_reconciler.py:23-25`) — reusing it steady-state changes its frame. **The cure goes through the chain's own CAS (the §3 suppress branch, reached by the sweep's EXISTING drive), which preserves all four.**

Preconditions that WOULD make a direct status stamp safe (recorded for a future lane, NOT used here): running ∧ no non-terminal task ∧ no holding message ∧ no open injection ∧ no bus pending ∧ closed tree ∧ no live member ∧ no pending question ∧ terminal completion evidence ∧ parent-side `internal_report:{child}:%` evidence; plus the provenance stamp via `set_metadata` (`stale_running_sweep.py:1156-1162`, key `terminal_transition_reason` `:237`, value `finalize_gap_steady_state` `:240`). The 7-mirror desync would be acceptable ONLY because sweep legs 2/3/5 guarantee no live task/JobItem/watcher rows (`research-findings-sweep.md` §6.3). Also REJECT `terminate_instance` (`instance_lifecycle.py:2648`, DB half `_terminate_instance_db_sync`, def `:5401`): it stamps `terminated`, DELETES the child's `message_queue` (`:5679`) and `task` (`:5701`) rows — destroying the sweep's own selection evidence — and fires parent-facing abort side effects.

---

## §5 THE PLANNER'S RULING

**RULING (do not re-open)**: the per-turn narrow is the PRIMARY cure, and the parent's outcome for a revived episode DOES reach it. The policy is NOT "one report per child, ever" — it is "at most one OUTSTANDING obligation per child"; a DELIVERED prior report never blocks a new turn.

**Explicit consequence**: a revived child that completes a new turn produces a **SECOND report row for the same child with a DIFFERENT anchor** (row (iii)), and **the parent receives that report**. This is intended: the parent must learn the revived episode's outcome. (The rejected alternative — terminal-stamp-only cure / variant A — leaves the parent with the FIRST episode's report forever; `research-findings-sweep.md` §7 item 4.)

Every place that assumes "one report per child, ever" (searched at this base), with anchor and what each needs:

| Place | Anchor | Assumption | Needs |
|---|---|---|---|
| Live mint guard (widened) | `child_reports.py:4677-4690` + comment `:4654-4676` | per-child LIFETIME prefix blocks all turns | Replace with §2 two-arm; update the NIT-MEDIUM rationale comment to state the new policy |
| Dead-code twin `_should_send_completion_report` | `child_reports.py:1279` (def), `:1360-1361` ("one obligation per CHILD ... NOT one per anchor"), SELECT `:1400-1412` | per-child prefix | **KEEP CONSISTENT**: apply the same two-arm predicate to its `existing_report` SELECT. Do NOT retire it — its docstring (`:1288-1297`) locks "kept verbatim, no deletion", `manager.py:12498-12517` wraps it, and `tests/unit/test_ready_message_completion_report.py` (463 lines) exercises it. Its tests assert only pending-message/no-anchor/no-parent reasons (`:145-147,460-463`), so the two-arm update does not break them; add one pin that a COMPLETED prior under a different anchor no longer blocks the helper. |
| RDRS delivery evidence | `report_injection/repository.py:1276-1277,1345-1351`; `message_queue/repository.py:277-330` | "one obligation per child" (per-child prefix, any anchor) | NO logic change (§3.5). Additive doc note only: the invariant is now "at most one OUTSTANDING obligation per child"; a delivered prior report does not block a new turn. Keep the prefix evidence (it is the anti-4×-duplicate guard). |
| Rider pin module docstring | `tests/unit/services/test_stale_running_sweep_rider_pins.py:6-16` | "PER-CHILD PREFIX invariant" | Additive note: invariant refined to at-most-one-outstanding; the three behavioral pins stay green unchanged (§2.6). |
| POK / KB rows | e.g. `child-report-per-child-obligation-invariant.md` and `child-reports-mint-anchor-exact-idempotency.md` in `.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-24/fig1-rag-inventory.tsv:3,14` (captured facts) | "ONE OBLIGATION PER CHILD" / "ANCHOR-EXACT" | Out of code scope; flag to the POK librarian post-merge as a successor fact (the live guard is neither pure anchor-exact nor lifetime-prefix — it is the §2 two-arm disjunction). Do NOT edit in this lane. |
| Interim posture | dispatcher directive | "cold-spawn all follow-ups; do not reuse/revive a completed instance" | Document as **INTERIM**, lifting when this fix lands. NOT permanent guidance. |

---

## §6 COMPONENT BREAKDOWN (parallel implementation)

### 6.1 Component A — core fix + riders + tests

**Files and roles** (all paths relative to the worktree):

| File | Role | Change |
|---|---|---|
| `daemon/services/child_reports.py` | LIVE guard + terminalization seam + mint block + B4 gate | §2 two-arm guard (`:4654-4696`); §3 seam (`:4691-4705`), suppress branch after CAS (`:4901-4936`), twin consistency (`:1400-1412`); comments |
| `daemon/services/stale_running_sweep.py` | **READ-ONLY DEPENDENCY — NOT MODIFIED** (§4.1/§4.2) | none — the cure is observed through its existing `healed` counter + `shape-b` WARNING |
| `tests/unit/services/test_stale_running_sweep_rider_pins.py` | existing guard pins + docstring | additive NIT-1 note; **NEW-1, NEW-4, NEW-5** pins (class `TestReviveWedgeNarrow`) |
| `tests/unit/services/test_child_reports.py` | chain terminal paths | **NEW-2, NEW-3, NEW-6a, NEW-6b, NEW-7** pins; keep the F4-race pin (`test_natural_completion_races_recovered_pending_marker`) green |
| `tests/unit/test_ready_message_completion_report.py` | twin tests | one new pin for the COMPLETED-prior-different-anchor case |
| `tests/postgres/test_child_reports_narrow_guard_pg.py` | **NEW file** — real-PG proof of the narrowed evidence query (leader-approved GAP-1 scope addition; §6.1 of the plan set) | NEW postgres-marked file; RED at base (the lifetime-prefix guard blocks the delivered-report + new-anchor mint ⇒ the mint assertion fails), GREEN at merge; **its own PACKS.md row** |
| `tests/repositories/test_report_injection_prefix_boundary.py` | **EXISTING, UNCHANGED — L1 stay-GREEN net** (leader ruling 3) | none; joins the L1 `$SUITE` with its own PACKS row |
| `.agents/tester/PACKS.md` | registration | one dated block + table row per NEW test file (B2) — the NEW postgres file owes its own row; the colon-boundary pin and the five unregistered L2N nets owe NEW rows (gate-matrix §8.2) |

Files explicitly NOT changed: `daemon/services/stale_running_sweep.py` (§4.2 — no pre-flight, no new counter, no summary token), `daemon/repositories/task/repository.py` (reuse `finalize_boot_consistency_instance` only as a rejected alternative; no second `instances.status` writer), `daemon/repositories/message_queue/repository.py` + `daemon/repositories/report_injection/repository.py` (**NO change by this release** — `find_wake_already_delivered_evidence` had only the withdrawn cost-guard pre-flight as a would-be consumer, so it is not touched at all; RDRS's per-child prefix evidence is unchanged, §3.5), `daemon/api.py` (no new knob), `daemon/migrations/versions/` (no migration), any config/flag.

**Dependency order inside A**: (1) guard narrow (§2) → (2) terminalization seam (§3) — the guard must be two-arm before the suppress branch is meaningful → (3) twin consistency **+ the R-K2 observability records** (§2 ARM B's cost, §7) → (4) tests + PACKS registration. Steps (1)–(3) are one atomic commit in `child_reports.py`; (4) rides with each. **There is NO sweep step and NO second commit** — the sweep file is not modified (D13); the rider IS the core fix, observed through the sweep's existing counters.

**Parallelizable sub-parts**: the sweep needs **no** implementation (its rider is the core fix itself, observed through the existing counters), so Component A's implementation is one sequential change in `child_reports.py` + its tests; Component B (observability) runs fully in parallel with it.

**Implementation work order (executable without further research)**:
1. In `_process_child_completion_db_sync`, replace the `:4677-4696` query with the §2.4 single-OR query; set `suppress_mint` in both branches; initialise it with `should_send`. Write NEW-4 mutation pin first (revert ARM B → RED).
2. Change `:4698` to `if not should_send and not suppress_mint:`; keep all other early returns.
3. Insert the suppress branch after the CAS rowcount guard (`:4936`), with commit + two `_event_registry_record` emissions + `outcome="idempotency_skip"` return. Write NEW-2 and NEW-3 pins.
4. Add the R-K2 observability with its emission point IN THE SUPPRESS BRANCH of step 3 — never at the guard, never on the defer path (§2.4 rules (i)-(v)): at the guard (step 1) capture the classification (`ARM_A`/`ARM_B`), the suppressed anchor and the blocking row's `source` / `message_id` / `status` into locals, parsing the anchor by `P-ANCHOR-PARSE` (prefix-strip, never `.split(":")`); in the suppress branch, after the CAS, emit the `P-ARM-B-SUPPRESS-LOG` WARNING record (child + both anchors + the outstanding marker id + `loss` + `total`) and move the `P-ARM-B-SUPPRESS-COUNTER` module-level counter (+1 only on `loss=1`; increment + `total=` read under the module-level `_ARM_B_SUPPRESS_LOCK`; §2 ARM B's cost, §7) on an ARM-B classification, and the `P-ARM-A-SUPPRESS-LOG` WARNING record on an ARM-A-only classification; add the `P-RETRYING-INFO-LOG` INFO record (mint-path best-effort probe). Write the NEW-7 pin including its defer case.
5. Apply the same two-arm predicate to the twin (`:1400-1412`); add its one new pin.
6. Write NEW-5 (same-anchor re-entry / ARM A non-vacuity), NEW-6a/NEW-6b (the real duplicate-freedom invariant + the new episode) and NEW-8 (the NEW postgres-marked file `tests/postgres/test_child_reports_narrow_guard_pg.py`).
7. Register in PACKS.md (B2, append-only): the NEW postgres file, the colon-boundary pin and the five unregistered L2N nets; restate the Scope census of the existing rows; run the B1 genuine-red-at-base gate (L1 + L2N) before merge.

### 6.2 Component B — disjointness claim

Component B (`observability.md`, parallel worker) owns `daemon/tools/system_log_tools.py`, `daemon/tools/_tool_registry.py`, `tests/test_system_log_tools.py`, `tests/unit/tools/test_privileged_category_system_log.py`, `tests/unit/tools/test_frozen_tool_name_discovery.py`, and the `start_line` schema/impl/doc changes on `ens_system_log_search` (`system_log_tools.py:414-627`). **Component A touches none of these files; Component B touches none of A's files.** The only shared surface is the planning directory (both write distinct filenames) and the merge lane — no source-file overlap. Collision measurement for B's files: only stale path-checkouts on `feature/video-mock`/`video-mock-room` (§9), no authored changes.

---

## §7 §PREDICATE LEDGER + ATTESTATION

One canonical string per predicate. These are the strings the body gates on; the body uses them verbatim. A companion `decisions.md` (transcribed from §10) must not restate them differently — BODY WINS ON CONFLICT.

| ID | Canonical string | Kind |
|---|---|---|
| `P-ARM-A-PY` | `MessageQueue.source == f"internal_report:{instance_id}:{completed_message_id}" and MessageQueue.status.in_([MessageStatus.READY.value, MessageStatus.PROCESSING.value, MessageStatus.COMPLETED.value])` | Python predicate |
| `P-ARM-A-SQL` | `source = 'internal_report:' \|\| :child \|\| ':' \|\| :anchor AND status IN ('ready','processing','completed')` | SQL predicate |
| `P-ARM-B-PY` | `MessageQueue.source.startswith(f"internal_report:{instance_id}:", autoescape=True) and MessageQueue.status.in_([MessageStatus.READY.value, MessageStatus.PROCESSING.value])` | Python predicate |
| `P-ARM-B-SQL` | `source LIKE 'internal_report:' \|\| :child \|\| ':%' ESCAPE '/' AND status IN ('ready','processing')` | SQL predicate |
| `P-SUPPRESS` | `suppress_mint = (ARM_A or ARM_B)` | decision |
| `P-MINT-SOURCE` | `internal_report:{instance.instance_id}:{completed_message_id}` | format string |
| `S-A` | `('ready','processing','completed')` | status set |
| `S-B` | `('ready','processing')` | status set |
| `P-EARLY-RETURN` | `if not should_send and not suppress_mint:` | control-flow gate |
| `P-CAS` | `UPDATE instances SET status='completed' WHERE instance_id=:id AND status NOT IN ('paused','completed','error')` | terminal writer |
| `P-OUTCOME-SUPPRESS` | `idempotency_skip` | outcome value |
| `P-OUTCOME-MINT` | `regular_child_completed` | outcome value |
| `P-B4-GATE` | `outcome == "idempotency_skip" and inst.status == InstanceStatus.COMPLETED.value` | re-mint gate |
| `P-SELECTION-AGE` | `newest_age = min(ages)` where `ages = [age(task_completed_at), age(message_completed_at)]`; skip when `newest_age < age_bound` (`stale_running_sweep.py:1077-1104`) | selection predicate |
| `P-SELECTION-BOUND` | `DEFAULT_AGE_BOUND_SECONDS = 7200` (`stale_running_sweep.py:181`) | bound |
| `P-DRIVE-OUTCOME` | `healed` — the sweep's post-drive terminal classification (`stale_running_sweep.py:1229-1231`); the sweep's counters and log lines are **UNCHANGED** | classification |
| `P-DRIVE-LOG` | `stale_running_sweep\|shape-b: instance=%s task=%s message=%s transitioned=%s reason=%s` (`stale_running_sweep.py:1239-1246`) | log line |
| `P-DRIVE-NOEFFECT-LOG` | `class=drive-no-effect` (retained — genuine no-effect only) | log class |
| `P-SKIP-LOG` | `class=young` / `class=drive-no-effect` (retained, unchanged) | log class |
| `P-TERMINAL-REASON` | `terminal_transition_reason = finalize_gap_steady_state` (unchanged) | metadata (unchanged) |
| `P-ARM-B-SUPPRESS-LOG` | `child_reports\|arm-b-suppress: class=arm-b-suppress child=%s incoming_anchor=%s outstanding_anchor=%s outstanding_message_id=%s loss=%s total=%d` — **emission point (repair 006):** emitted in the SUPPRESS BRANCH after the fall-through CAS — exactly ONE record per ACTUAL ARM-B suppression; NEVER at the guard; the defer path (`outcome="child_still_running_defer"`) emits NOTHING; the anchors are parsed by `P-ANCHOR-PARSE`; the emission context carries the blocking row's `source` / `message_id` and the outcome (`loss=1` = R-K2 loss, the newer turn's report dropped; `loss=0` = same-obligation duplicate suppressed); the format string is UNCHANGED | log line |
| `P-ARM-B-SUPPRESS-COUNTER` | `_ARM_B_SUPPRESS_LOSS_TOTAL` — module-level counter in `daemon/services/child_reports.py`; **+1** on every ARM-B suppression with `loss=1` (the two anchors differ ⇒ an R-K2 content-loss event), **+0** otherwise; the running value is surfaced in every `P-ARM-B-SUPPRESS-LOG` line as `total=%d`; it resets on daemon restart; **the increment and the `total=` read run under the module-level lock `_ARM_B_SUPPRESS_LOCK` (`threading.Lock`)** — the increment happens on `asyncio.to_thread` workers, where a bare `+= 1` can lose an update; **emission point (repair 006):** the counter moves ONLY in the SUPPRESS BRANCH after the fall-through CAS, and ONLY on an R-K2 loss (`loss=1`) — never at the guard, never on `loss=0`, never on ARM A, never on the defer path (`outcome="child_still_running_defer"` ⇒ +0) | counter |
| `P-ARM-A-SUPPRESS-LOG` | `child_reports\|arm-a-suppress: class=arm-a-suppress child=%s incoming_anchor=%s matched_anchor=%s matched_message_id=%s matched_status=%s` — **emission point (repair 006):** emitted in the SUPPRESS BRANCH after the fall-through CAS — exactly ONE record per ACTUAL ARM-A-only suppression (row (i)); NEVER at the guard; the defer path emits NOTHING; `matched_anchor` is parsed from the carried `source` by `P-ANCHOR-PARSE`; the emission context carries the blocking row's `source` / `message_id` / `status`; the outcome is the record class itself (same-anchor duplicate suppressed); the format string is UNCHANGED | log line |
| `P-RETRYING-INFO-LOG` | `child_reports\|retrying-obligation: class=retrying-obligation child=%s incoming_anchor=%s retrying_anchor=%s retrying_message_id=%s retrying_count=%d` — `retrying_anchor` is read from the oldest `retrying` row's `source` by `P-ANCHOR-PARSE`; emitted on the MINT path only (never on a suppression, never on a defer); it costs a SECOND unindexed prefix scan per mint | log line |
| `P-OUTSTANDING` | `at most one outstanding obligation per child in ('ready','processing') — 'retrying' EXCLUDED` | invariant |
| `P-ANCHOR-PARSE` | `anchor = row.source[len(f"internal_report:{instance_id}:"):]` — PREFIX-STRIP, NEVER `.split(":")`; the degenerate source yields `""`; OBSERVABILITY-ONLY: it feeds the `outstanding_anchor` / `matched_anchor` / `retrying_anchor` log fields of `P-ARM-B-SUPPRESS-LOG` / `P-ARM-A-SUPPRESS-LOG` / `P-RETRYING-INFO-LOG` and NEVER feeds `suppress_mint`, the ARM-A/ARM-B classification, the CAS or any control decision; it runs only on rows the guard / the `retrying` probe already selected by that exact prefix | parse rule (observability-only) |

### Closing ATTESTATION (changed rows, old → new)

**RE-ISSUED 2026-11-25 (repair iteration 005 — after the approver's iteration-002 REJECT: 2 blockers + 10 notes, ALL folded; the LAST approver pass).** **Rows CHANGED in iteration 005:** `P-ARM-A-SUPPRESS-LOG`, `P-RETRYING-INFO-LOG` (**ADDED** — leader rulings 7 and 6), `P-OUTSTANDING` (**ADDED**), `P-ARM-B-SUPPRESS-COUNTER` (**AMENDED** — the `_ARM_B_SUPPRESS_LOCK` clause). **Named INLINE predicates changed in iteration 005 — NOT rows of the §7 tables (each is stated in the body):** `P-DUPLICATE-FREEDOM` (**AMENDED** — NEW-6a is the pin; L2d is the TWO-site mutation; stated at §3.4 invariant 1), `P-DRIVE-ANCHOR` (**REPLACED** — stated at §1.6 / §2.5 and §8 K12). **Row carried unchanged from iteration 004:** `P-ARM-B-SUPPRESS-LOG` (added in 004). **Rows verified character-for-character against this body in this pass (unchanged):** `P-ARM-A-PY`, `P-ARM-A-SQL`, `P-ARM-B-PY`, `P-ARM-B-SQL`, `P-SUPPRESS`, `P-MINT-SOURCE`, `S-A`, `S-B`, `P-EARLY-RETURN`, `P-CAS`, `P-OUTCOME-SUPPRESS`, `P-OUTCOME-MINT`, `P-B4-GATE`, `P-SELECTION-AGE`, `P-SELECTION-BOUND`, `P-DRIVE-OUTCOME`, `P-DRIVE-LOG`, `P-DRIVE-NOEFFECT-LOG`, `P-SKIP-LOG`, `P-TERMINAL-REASON`. **BODY WINS ON CONFLICT** — where a companion restates a string differently, this body is authoritative. **RE-ISSUED AGAIN 2026-11-25 (repair 006 — the lane's planner-repair-4 / "BOUNDED PLAN REPAIR 004" dispatch; after the approver's iteration-003 REJECT: 2 blockers + the material notes).** **§7 TABLE rows changed in repair 006:** `P-ANCHOR-PARSE` (**ADDED** — the normative prefix-strip parse rule; observability-only, never feeds `suppress_mint`), `P-ARM-B-SUPPRESS-LOG` (**AMENDED** — emission point: the suppress branch, never the guard, nothing on the defer path), `P-ARM-A-SUPPRESS-LOG` (**AMENDED** — the same emission point + `P-ANCHOR-PARSE`), `P-ARM-B-SUPPRESS-COUNTER` (**AMENDED** — moves only in the suppress branch and only on an R-K2 loss, `loss=1`), `P-RETRYING-INFO-LOG` (**AMENDED** — `retrying_anchor` read by `P-ANCHOR-PARSE`; the second mint-path scan stated). **Every other row of both tables is carried unchanged** (26 rows in this table; the Component-B ledger is not in this file) and was compared character-for-character in this pass — Component A: this table vs `plan.md §7` (Component B: `plan.md §7` vs `observability.md §9`); method and result: `plan.md §14.3`. **BODY WINS ON CONFLICT.**

- `P-GUARD` (old): `source.startswith(f"internal_report:{instance_id}:", autoescape=True) AND status IN ('ready','processing','completed')` → **new**: `P-SUPPRESS` = `(P-ARM-A-PY) OR (P-ARM-B-PY)` (two arms, split status sets; the COMPLETED status moves from the blanket arm into ARM A only).
- `P-EARLY-RETURN` (old): `if not should_send:` → **new**: `if not should_send and not suppress_mint:`.
- `P-TERMINAL-WRITER` (old): terminal stamp unreachable on mint-suppress → **new**: CAS at `:4901` runs on the suppress path; remains the single writer (value unchanged).
- `P-OUTCOME-SUPPRESS` (old): emitted with NO status flip → **new**: emitted AFTER a committed COMPLETED stamp (string unchanged, semantics changed).
- `P-DRIVE-OUTCOME` (old): the wedge drive observed `drive_no_effect` → **new**: the same drive observes `healed` — the sweep's classification code is **unchanged**; what changed is the chain's outcome (it now terminalizes).
- `P-SUMMARY` (old): no `cured=` token → **new**: **unchanged** — the withdrawn pre-flight would have appended `cured=%d`; no summary change ships.
- `P-TWIN` (old): lifetime prefix → **new**: same two-arm as `P-SUPPRESS`.
- `P-ARM-B-SUPPRESS-LOG` (old): no ARM-B observability — the R-K2 content loss was SILENT → **new**: a WARNING record per ARM-B suppression carrying the child, BOTH anchors (the suppressed call's anchor + the outstanding marker's anchor), the outstanding marker id, `loss` and the running `total`.
- `P-ARM-B-SUPPRESS-COUNTER` (old): no counter → **new**: `_ARM_B_SUPPRESS_LOSS_TOTAL` — +1 per R-K2 content-loss event, surfaced in every log line.
- `P-DUPLICATE-FREEDOM` (old — a named INLINE predicate stated at §3.4 invariant 1; NOT a row of the §7 table): "exactly one report row per `(child, turn)` — ARM A + the CAS rowcount" → **new**: the real invariant: the terminal-entry guard (`:3781-3874`) + CAS-before-mint atomicity (`:4901` before `:4959`, one commit `:5762`) + the per-parent lock (`:3524-3525`); ARM A is a **same-ANCHOR** guard, load-bearing only on the router re-entry (L2c/NEW-5); the real invariant is pinned by NEW-6a and proved non-vacuous by L2d — the TWO-site mutation (the terminal-entry short-circuit `:3781-3784` AND the CAS rowcount-0 return `:4918-4936`); a one-site mutation leaves NEW-6a GREEN because the CAS is the backstop (defense in depth, not the mutation leg).
- `P-DRIVE-ANCHOR` (old — a named INLINE predicate stated at §1.6 / §2.5 and §8 K12; NOT a row of the §7 table): "the drive path invokes the chain with `completed_message_id = ""`; ARM A is the drive's idempotence anchor" → **new**: the drive's anchor is `latest_msg_id or ""` — normally a real completed queue-row id (`""` only when the child has no completed queue row); **the drive path's anchor is NOT changed by this release** — this release only READS the stored anchor back, observability-only, under `P-ANCHOR-PARSE` (repair 006); it does not alter what the drive supplies.

### DELETION RECORD (withdrawn predicates)

- `P-PREFLIGHT` (withdrawn) — the cost-guard pre-flight predicate. Without the checkpoint read there is no anchor, and `completed_message_id = None` returns at `:4698` before `suppress_mint` is set ⇒ the proposed "cure" would not terminalize (§4.2).
- `P-PREFLIGHT-DISPOSITIONS` (withdrawn) — `drive` / `cure` / `skip`; withdrawn with `P-PREFLIGHT`.
- `P-CURE-STATUS` (withdrawn) — `completed` as a pre-flight stamp; withdrawn with `P-PREFLIGHT`.
- `P-NEW-CLASS-LOG` (withdrawn) — `class=cured-already-reported`; withdrawn with `P-PREFLIGHT`.


- `P-GUARD-PREFIX-ONLY` (withdrawn): `source.startswith(f"internal_report:{instance_id}:", autoescape=True) AND status IN ('ready','processing','completed')`. Reason: blanket lifetime prefix blocks a new turn's report after a COMPLETED prior — the wedge root cause. Superseded by `P-SUPPRESS`. NOT deleted from history; recorded here as withdrawn.
- `P-GUARD-ANCHOR-EXACT` (already withdrawn by 58fc594eb; re-affirmed withdrawn): `source == f"internal_report:{instance_id}:{completed_message_id}"` alone. Reason: cross-anchor duplicate (b4). Its anchor-exact predicate survives ONLY as ARM A — a **same-ANCHOR (same-token)** guard — paired with ARM B.

No stale cross-references (re-verified 2026-11-25, repair iteration 005 — body-vs-ledger grep re-run over all six artifacts; plan.md §14.2): companion files must cite these IDs, not paraphrase the predicates. The ONLY places the withdrawn cost-guard pre-flight / its `cured_already_reported` counter / `class=cured-already-reported` log / `cured=%d` token are named are explicit WITHDRAWAL statements — this DELETION RECORD, the `P-SUMMARY`/`P-DRIVE-OUTCOME` changed rows above, `decisions.md` D13, and `gate-matrix.md` §10's L5-strings row — plus the pre-decision research artifacts `research-findings-*.md` (evidence of a REJECTED option, not instructions). Any conflict → this body is authoritative.

---

## §8 RISKS + RESIDUALS

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| K1 | Narrow re-opens b4's duplicate hole if implemented as pure anchor-exact | **High** | ARM B keeps READY/PROCESSING cross-anchor suppression; NEW-4 mutation leg reverts ARM B → `test_prior_pending_report_under_a_different_anchor_blocks_a_second` goes RED (non-vacuous proof); all b4 pins stay green |
| K2 | Double report to the parent on a revived episode | Medium | ARM A blocks the same-ANCHOR (same-token) duplicate — load-bearing only on the router re-entry (L2c/NEW-5); the **per-child duplicate-freedom** is delivered by the terminal-entry guard + CAS-before-mint atomicity + the per-parent lock (`P-DUPLICATE-FREEDOM`; pinned by NEW-6a, proved non-vacuous by the two-site L2d); the second report is the intended NEW-turn outcome (§5), delivered once via the mint; B4 re-mint is guarded by the bus helper's `transition_state` rowcount (`:608-613`) |
| K3 | RDRS backstop erosion | Medium | §3.5 ruling: RDRS stays per-child (no logic change); prefix evidence remains the anti-4× guard; residual (can't distinguish lost wake N from delivered N-1) documented and accepted |
| K4 | Mirror desync from any direct status stamp | Medium | Cure goes through the chain's CAS at `:4901` (single writer), not `finalize_boot_consistency_instance`; no direct stamp in this release; the chain's existing no-reconcile posture is unchanged |
| K5 | Sweep drive cost (the pre-flight is withdrawn) | Low→Medium | The 2–7 s per-victim checkpoint read is paid **once per victim** — the drive cures and the victim leaves the candidate set; worst case `DEFAULT_BATCH_CAP=25` in the first post-fix tick (one-time). The cost-guard pre-flight is **withdrawn** (§4.2) — it would not have terminalized |
| K6 | Two live victims `a5c2c2be`, `99b3c1a7` stay running until fix lands | Medium | Their owning parents may terminate them — NOT this lane; **no instance lifecycle action (terminate/pause/revive/spawn) in this lane**; they self-heal after the fix promotes (cold-spawn interim posture lifts then) |
| K7 | Final-return locals undefined on suppress path | Low | Dedicated suppress return after CAS (§3.1 step 4) avoids the `:5775` return that references mint-only locals |
| K8 | Twin divergence (dead-code helper drifts from live guard) | Low | §5 keep-consistent ruling + one new twin pin; do not retire (docstring + manager wrapper + 463-line test surface) |
| K9 | `source` unindexed scan at the mint guard | Low | Once per completion (not per tick); the sweep pre-flight is withdrawn, so no indexed `instance_id` variant is used; a new index = migration = OUT OF SCOPE |
| K10 | ARM B loses the new turn's report | Medium | Accepted residual R-K2 — the parent receives the OLDER turn's report; the defer-and-retry alternative is rejected (it re-introduces a non-terminal window — the shape the leader's ruling #2 forbids) — **accepted WITH OBSERVABILITY (the leader's condition)**: every ACTUAL ARM-B suppression emits `P-ARM-B-SUPPRESS-LOG` (child + BOTH anchors + the outstanding marker id + `loss`) — emitted in the SUPPRESS BRANCH, never at the guard, nothing on the defer path — and moves `P-ARM-B-SUPPRESS-COUNTER` (`_ARM_B_SUPPRESS_LOSS_TOTAL`) by +1 only on `loss=1` (else +0); pinned by NEW-7 (§7 owns the canonical strings) |
| K11 | The cure DELIVERS a backlog report to the recipient | Medium | Expected — PL-6 variant B: the parent's outcome DOES reach it; a live recipient may wake on a stale report at promotion time (the leader may instead have owning parents terminate the two pinned victims) |
| K12 | The drive-path anchor | Low | The drives supply `latest_msg_id or ""` — normally a **real** completed queue-row id (`stale_running_sweep.py:1198-1199`), so ARM A is a same-ANCHOR guard keyed by that id (`""` only when the child has no completed queue row); no consumer parses the anchor back out of `source` on the DELIVERY path at base (measured) — THIS release adds the FIRST parse, observability-only in `child_reports.py` and by the `P-ANCHOR-PARSE` prefix-strip rule; it never feeds `suppress_mint`, so both forms stay inert for control flow; **the drive path's anchor is NOT changed by this release** (leader-accepted, review C3) |
| K13 | `retrying` is EXCLUDED from ARM B (leader ruling 6) | Low→Medium | A stuck `retrying` row must never become a new forever-block class; a `retrying` report does not block a cross-anchor second mint, so two obligations can briefly coexist for one child — residual **R-K4**, observable via `P-RETRYING-INFO-LOG` |
| K14 | The ARM-A drop race | Low | A router re-entry re-supplying a DELIVERED anchor while a revived child is non-terminal can win the per-parent lock ⇒ ARM A suppresses + terminalizes ⇒ that episode's natural-path report is dropped; strictly better than the pre-fix wedge — residual **R-K5**, now LOUD via `P-ARM-A-SUPPRESS-LOG` (leader ruling 7) |
| K15 | The ARM-B-case watcher gap | Low→Medium | On a LIVE row-(iv) suppression the async `regular_child_completed` hooks are skipped (`:6874-7046`), so a completion-registry / `on_complete` / child-keyed waiter for THAT turn is not released by this invocation; B4's bus-watcher emit still runs; harmless for the stale victims — residual **R-K6**, a future lane |
| K16 | The loss counter is thread-incremented | Low | A bare `+= 1` from `asyncio.to_thread` workers can undercount — the increment and the `total=` read run under `_ARM_B_SUPPRESS_LOCK`; pinned by NEW-7 |

**Residuals**: RDRS per-turn delivery auditing deferred (§3.5); `finalize_boot_consistency_instance` steady-state cure rejected (§4.5); the ≤7200 s young-skip window for pre-fix victims (one-time deploy transient — no new victims post-fix); **R-K2: ARM B suppresses the new turn's report** (the parent receives the older turn's report — accepted **WITH OBSERVABILITY**: `P-ARM-B-SUPPRESS-LOG` + `P-ARM-B-SUPPRESS-COUNTER`, §2.4/§7, pin NEW-7; deferral rejected); **R-K4: `retrying` is excluded from ARM B** (K13 — observable via `P-RETRYING-INFO-LOG`); **R-K5: the ARM-A drop race** (K14 — now loud via `P-ARM-A-SUPPRESS-LOG`); **R-K6: the ARM-B-case watcher gap** (K15 — a future lane); no new migration

---

## §9 FILE-OWNERSHIP COLLISIONS (measured at base 35ec50e3c)

Command (from worktree): `git branch --no-merged origin/latest` → **39 branches**. For every file A or B plans to touch: `git diff --stat origin/latest...<branch> -- <path>` and `git log --oneline origin/latest..<branch> -- <path>`. Results:

| Path | Branch (tip SHA) | Commits touching | +/- (net vs merge-base) | Assessment |
|---|---|---|---|---|
| `daemon/services/child_reports.py` | `fix/empty-job-completed-event` @ `5368a12d4d746a78d45462c7c87aa8cfe50035f0` | 4 (`5368a12d4`, `96f1f6d35`, `614ab41f3`, `964f6b11a`) | **+456/−74** | **SEMANTIC — real collision.** Re-measured at this base (old FILE-OWNERSHIP note was @ eca98397d, stale). Hunks at old lines 9-768: imports, root/cascade completion gate (`_root_completion_gate`), event-emission helpers, `completion_content` extraction. Does NOT touch the mint guard (`:4654-4696`), CAS (`:4901`), mint (`:4959`), or B4 gate (`:6551-6630`) by content — grep of the branch diff for `internal_report|startswith|existing_report|mint|idempotency_skip` returned ZERO. Overlap is same-FILE + the dead-code-twin/event-emission neighborhood (§5). **Leader arbitrates at merge.** Branch is old (merge-base `997c670d3`, 2026-05-25) so line numbers are not directly comparable — re-diff at merge time. |
| `daemon/repositories/task/repository.py` | `feature/video-mock` @ `ac0b59cc…`, `feature/video-mock-room` @ `b4d3a950…` | 1 merge commit each (`85ed86b46` "sync onto origin/latest 0181dadbf") | **0** (net diff vs merge-base empty) | **TRIVIAL / stale path-checkout.** Branch blob == merge-base blob (`0181dadbf`); origin/latest advanced +852/−1 since. Not an authored change. No collision. |
| `daemon/tools/_tool_registry.py` (B) | `feature/video-mock`, `feature/video-mock-room` | same merge commit | **0** | TRIVIAL / stale path-checkout (blob == merge-base). No authored change. |
| `daemon/services/stale_running_sweep.py`, `daemon/repositories/message_queue/repository.py`, `report_injection/repository.py`, all A/B test files, `daemon/tools/system_log_tools.py`, other privileged-category test files | none of the 39 | 0 | 0 | No contention recorded; absence-of-evidence caveat — the loop above covered all 39 branches and returned only the rows shown. |

**Bottom line**: one real semantic collision (`fix/empty-job-completed-event` on `child_reports.py`), disjoint function regions, leader arbitrates at merge; all other apparent hits are stale path-checkouts byte-identical to their merge-base. Post-merge hygiene: fold/merge that branch's completion-gate work without dropping either side's pins (re-run the B1 gate at the merge commit).

---

## §10 DECISION LOG (transcribe into decisions.md)

| # | Decision | Rationale | Rejected alternative |
|---|---|---|---|
| D1 | Two-arm guard (ARM A same-**ANCHOR** (same-token) exact + ARM B outstanding prefix), single OR query | Preserves b4's cross-anchor duplicate protection (ARM B) while allowing a new turn's report after a COMPLETED prior (ARM A only) | Pure anchor-exact narrow — re-opens b4's 2-obligations/1-child hole; pure lifetime prefix — is the wedge root cause |
| D2 | Decouple terminalization: suppress mint via flag, fall through to CAS, dedicated suppress return | Breaks the chicken-and-egg (mint guard before stamp; B4 gate needs stamp); keeps single terminal writer | Early-return on suppress (current, wedges) / wrap mint only and fall through to `:5775` (references undefined locals) |
| D3 | Outcome stays `"idempotency_skip"` on suppress path | B4 cycle-2 re-mint (`:6563`) is gated on it; with the row now COMPLETED the re-mint fires (`:6581`) | New outcome value (e.g. `already_reported_terminalize`) — would bypass the B4 gate and leave PENDING watchers unhealed |
| D4 | Keep the dead-code twin consistent (do not retire) | Docstring locks "kept verbatim"; manager wrapper + 463-line test surface; divergent twin is a latent trap | Retire the helper — breaks the existing test surface, out of scope |
| D5 | RDRS stays per-child (no per-turn change, no SQL) | RDRS is a no-row backstop; exact-anchor per-turn reintroduces the 4× RESUME_ROUTER duplicate (id-space mismatch) | Per-turn exact-anchor evidence — proven defective; per-turn auditing via `report_injections.child_message_id` is a future lane |
| D6 | ~~Sweep pre-flight returns drive/cure/skip~~ — **SUPERSEDED by D13** | — | — |
| D13 | **The sweep rider needs NO code change** — the drive cures via the core fix; the cost-guard pre-flight is withdrawn | The pre-flight's "cure" had no anchor ⇒ `completed_message_id = None` returned at `:4698` before `suppress_mint` was set ⇒ it could not terminalize; and `find_wake_already_delivered_evidence` (lifetime prefix, any status) cannot distinguish the live-victim shape (prior COMPLETED under a DIFFERENT anchor ⇒ MUST mint) from a true suppress — as a gate it would have dropped the parents' outcomes | Keep the pre-flight; keep `cured_already_reported` / `class=cured-already-reported` / `cured=%d` |
| D14 | **Version slot `v0.31.60`** (EXPECTED, front door 2026-11-25 ~21:45Z; not a reservation; W9 re-derived free as fetched 2026-11-26) + the version-file rule (the tip's five files decide, never the tag) | The prior draft derived `v0.31.55`, which the POK-gate lane took (and `v0.31.56` followed); `origin/latest` is now two tags ahead of the branch base; a naive merge of a bump cut against an older tip regressed 5 version files to 0.31.51 in house precedent | Re-derive the slot at bump time without the assignment; merge the bump naively |
| D15 | **The `5368a12d4` branch is ANCESTOR-OF-LATEST, not a guard variant** — no action in this lane | All 4 of its added defs exist at base; its diff holds ZERO mint-guard tokens; its verbatim-absent lines (**184** under the reviewer's reproduction — 359 unique added lines > 12 chars — superseding the front door's non-reproducible 320/68) are evolved root-emission-gate log/docstring text **and executable statements of the root-emission downgrade path**, not mint-guard predicates | Merge / cherry-pick / rebase onto it |
| D7 | REJECT `finalize_boot_consistency_instance` and `terminate_instance` as the cure | Second status writer; no B4 re-mint; no mirror reconcile; `terminate_instance` deletes selection evidence and fires abort side effects | Direct status stamp — only safe under a listed precondition set for a future lane |
| D8 | New-turn cure produces a SECOND report row; parent receives it | Revived episode's outcome must reach the parent; this is the planner's ratified policy | Terminal-stamp-only (variant A) — parent never learns the revived outcome |
| D9 | No migration / no index / no config flip / no live-instance action | No new table/column; `source` unindexed scan is once-per-completion; staged release only | Add `source` index or per-episode marker column — migration = OUT OF SCOPE |
| D10 | Interim cold-spawn posture is INTERIM, lifts when fix lands | Dispatcher directive; permanent guidance would be wrong post-fix | Document it as permanent — rejected |
| D11 | Single OR query, not two selects | One round-trip/scan; ARM A ⊆ ARM B in row coverage; caller needs only a bool | Two selects with short-circuit — no savings, extra query |
| D12 | Additive-only test edits; existing three rider pins stay green | Two-arm design preserves their asserted behavior; only docstring narrative needs a note | Rewrite the three pins — unnecessary churn, would mask the non-vacuous proof |
| D16 | **`retrying` stays OUT of ARM B** (leader ruling 6) | A stuck `retrying` row must never become a new forever-block class; the exclusion is residual R-K4, observable via `P-RETRYING-INFO-LOG` | Widening `S-B` to include `retrying` |
| D17 | **ARM-A suppressions are LOGGED** (`P-ARM-A-SUPPRESS-LOG`) | Leader ruling 7; closes the silent drop race R-K5 | ARM-B-only logging |
| D18 | **The colon-boundary pin rides L1 with its own PACKS row** | Leader ruling 3; it is SQLite-in-memory, so it CAN ride the unit suite; both negations struck | citing it as "an existing invariant" with no suite and no row |
| D19 | **L2d is a TWO-site mutation; L2a's target is the NIT-1 companion** | The CAS rowcount-0 return is the backstop behind the terminal-entry short-circuit — a one-site mutation cannot reach the mint; `test_b4_child_report_obligation.py` never runs the mint guard | the one-site mutation; the b4 file as a RED target |

---

**End of design.md (Component A).** No source files were edited; no builds/tests/commits/branches/tags/worktrees created; no live instance touched; no config/flag changed. The only file written is this deliverable.
