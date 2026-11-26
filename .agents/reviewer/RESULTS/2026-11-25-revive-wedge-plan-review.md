# Plan Review — Revive-Wedge Terminalization (fleet-wide terminalization defect)

**Reviewer:** reviewer[v2] · **Date:** 2026-11-25 · **Mode:** Standard (read-only plan review, no sub-agents per the commission fence)
**Target:** `.agents/shared/planning/revive-wedge-terminalization/` — `plan.md` (490 L) + `design.md` (485 L), `gate-matrix.md` (372 L), `decisions.md` (133 L), `release-staging.md` (180 L), `observability.md` (968 L)
**Base measured:** `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` (= tag `v0.31.54`), worktree `/home/nea/ensemble-src-wt-revive-wedge`, branch `fix/revive-wedge-terminalization` (clean except the untracked planning dir)
**Method:** every claim below re-measured by content match at base (`grep`/`sed`/`awk`) plus read-only `git show`/`git diff`/`git log` in the worktree. No writes except this file; no test runs; no lifecycle actions; no revives; the canonical checkout `/home/nea/ensemble-src` was never read.

---

## VERDICT

**CONDITIONAL APPROVE — the DESIGN is approved; the PLAN TEXT is not implementable as written.**

- The narrow, the decoupling, the sweep rider and the observability component are sound, and the gate matrix is unusually complete (real RED-at-base, two mutation directions, real-PG, victim-class, ownership chains).
- **C1–C5 below must be repaired in `plan.md` / `design.md` / `gate-matrix.md` before an implementer is handed the document.** C2 and C3 are not cosmetic: C2 would push an implementer into violating a fence and a leader ruling, and C3 is a factually wrong load-bearing model (the anchor is a per-call token, not a per-turn key).
- The crux recommendation is in §"CRUX RECOMMENDATION": **do not change the drive path's anchor**; instead state the invariant that actually prevents duplicates, fix the id-space model, and add the missing ARM A mutation leg.

---

## CRITICAL — blocking (must be repaired before implementation)

### C1 — The leader's R-K2 observability condition is absent from the plan

R-K2 was accepted **conditionally**: *"ACCEPT with observability (loud log with child + both anchors + the outstanding marker id, plus a counter)"*. The plan records the acceptance but never the condition:

- `plan.md:107` — "The lost turn-2 report is an accepted, documented residual." No log, no counter.
- `plan.md:364` K10 / `design.md:440` / `decisions.md:73` — "accepted residual R-K2 — the parent receives the older turn's report." No observability.
- A grep for `both anchors` / `loud log` across all five planning files returns **zero** hits. The only `counter` hits are the *withdrawn* sweep counter (`plan.md:185`) and `observability.md`'s `total_lines` counter — unrelated.

**Why it blocks:** the acceptance was conditional on making the loss *observable*. As written the release would ship a silent content-loss path (the parent's newest turn outcome is never delivered) with no signal that it happened.

**Fix:** specify the ARM B suppression signal in the ledger + body: a loud log line carrying `child_instance_id`, the **incoming** anchor (`completed_message_id`), the **existing** outstanding row's `source` anchor and its `message_id` (the outstanding marker id), plus a counter with a named ledger ID (e.g. `P-ARM-B-SUPPRESS-LOG`, `P-ARM-B-SUPPRESS-COUNTER`). Name where the counter lives (a module-level counter in `child_reports.py` surfaced in the existing summary, or the sweep's summary — note the sweep file is currently *not* modified, so the counter must land on the Component-A side) and add the pack row/pin for it. Cross-reference it from `plan.md:107`, `plan.md:364` (K10), `design.md:440`, `decisions.md:73`.

### C2 — The withdrawn sweep pre-flight is still in the WORK ORDER: an implementer following §6 would modify the sweep file

The pre-flight, its `cured_already_reported` counter, the `class=cured-already-reported` log and the `cured=%d` summary token are **withdrawn** everywhere they are described as decisions — but they survive as **instructions**:

| Where | Text | Contradicts |
|---|---|---|
| `plan.md:252` | "**Dependency order inside A:** (1) guard narrow → (2) terminalization seam → (3) twin consistency → **(4) sweep pre-flight + counters** → (5) tests + PACKS registration" | `plan.md:242` ("READ-ONLY DEPENDENCY — **NOT MODIFIED**"), `plan.md:250`, `plan.md:452` (PL-7) |
| `plan.md:254` | "**5.** insert the pre-flight at `:1198`; add `cured_already_reported` + `cured=%d` + `class=cured-already-reported`; add the disposition pins." | `plan.md:185` ("**WITHDRAWN** (ledger: DELETION RECORD)"), `plan.md:185` (withdrawn because the counter would never fire for the live-victim shape) |
| `design.md:352` | "(4) sweep pre-flight + counters (§4, depends on the chain now curing)" | `design.md:273`, `design.md:284`, `design.md:344` |
| `plan.md:438` (release-notes outline (d)) | "the riders (the drive stops being pointless; **the pre-flight kills the 2–7 s per-victim read**; no new SQL/column/migration)" | `release-staging.md §7(d)`: "**The sweep file is not modified**; the 2–7 s per-victim checkpoint read is paid once per victim" — the two release-note drafts contradict each other |

**Why it blocks:** step 5 of the work order is an executable instruction to edit `daemon/services/stale_running_sweep.py` and to re-add a counter that the plan elsewhere proves would never fire. That violates the fence ("no report-delivery semantic change beyond the narrow", sweep file unmodified) **and** leader ruling PL-7. This is exactly the "plan body disagrees with itself" failure mode the house POK row `plan-body-ledger-agreement` warns about — and here the stale body is the one an implementer follows.

**Fix:** delete step (4)/(5) from `plan.md:252`/`plan.md:254` and `design.md:352` (renumber to "(4) tests + PACKS registration"), and rewrite `plan.md:438(d)` to match `release-staging.md §7(d)` verbatim in substance. Add a "no pre-flight / no counter" assertion to the implementation checklist so the withdrawn items cannot re-enter.

### C3 — The id-space model is wrong: the anchor is a per-call token, not a per-turn key

The plan's whole ARM A rationale rests on "the anchor is a per-turn key that the guard can reconstruct":

- `design.md:92` — "**`BaseMessage.id` = `completed_message_id` = the ANCHOR written into `source` at `:4967`**"
- `design.md:96` — "The anchor IS reconstructible at check time … **A per-turn key is reconstructible; the design uses it (ARM A)**"
- `design.md:181` (§2.5) — same claim, "No id-space mismatch blocks the narrow."
- `plan.md:70` (§1.4) — "(1) `BaseMessage.id` = the anchor written into `source` at `:4967`"
- `plan.md:101-105` (§2 "Empty-anchor convention") — "The drive path invokes the chain with `completed_message_id = ""` … ARM A's exact key becomes `internal_report:{child}:` — the drive path's **idempotence anchor**"; `plan.md:364` K12 repeats it.

**Measured at base — the anchor is the CALLER's token, not a `BaseMessage.id`:**

| Entry path | Anchor actually supplied | Evidence |
|---|---|---|
| Natural completion (resume-driven turn) | a **fresh uuid4 minted per resume** — `message_id = str(uuid.uuid4())`, comment: *"Create a fresh message_id for tracking (not enqueued, just for internal tracking)"* | `daemon/manager.py:16512` → passed at `daemon/manager.py:16758` → `child_reports.py:3283` → `:3534/:3541` → param `:3639` → mint `:4967` |
| Router/deferred re-entry | the **original** anchor (the obligation-triple member) | `daemon/manager.py:12698` (`_reenter_completion_async`) → `:12738` `child_message_id` = "the obligation-triple member" (`report_injection/models.py:147-151`) |
| Root stale-task failure | `message_id or ""` | `daemon/manager.py:9044-9046` |
| Sweep drive | `latest_msg_id or ""` — the newest **COMPLETED `message_queue` row id** for the child | `daemon/services/stale_running_sweep.py:1198-1209`, `:1266-1275` |
| Strand-healer drives | `latest_msg_id or ""` | `daemon/services/mission_strand_healer.py:772-774`, `:1316-1318` |

The house's own vocabulary names this exact hazard: `daemon/repositories/message_queue/repository.py:288-293` — *"the PREFIX ledger is the cross-path check that survives the F-2 wedge's **id-space mismatch (`BaseMessage.id` vs `MessageQueue.message_id`)**"*. (Its line-pointer "child_reports.py:3498-3507" is itself stale — that region is the parent-lock comment; `child_reports.py` has **no** live `source ==` equality check; only the two prefix guards at `:1403` and `:4680`.)

**Consequences the plan must absorb:**

1. **ARM A is a same-TOKEN guard, not a same-turn guard.** Rows (i)/(ii) of the truth table (`plan.md:93-94`, `design.md:138-139`) only hold for the ONE path that re-supplies the same token (the router re-entry, `manager.py:12738`). On every other path "same turn, different token" is indistinguishable from "new turn, different token" — both look like *different anchor + delivered prior* ⇒ row (iii) ⇒ **mint**.
2. **The drive path is not the empty-anchor case.** `latest_msg_id` is normally a real `MessageQueue.message_id` (the child's newest completed inbound row — the revive message included), so the drive's ARM A key is `internal_report:{child}:{queue-id}`, not `internal_report:{child}:`. `plan.md:102`'s "a second drive of the same child hits ARM A and suppresses a second mint" therefore holds only while `latest_msg_id` is stable across drives; `plan.md:103`'s "ARM A is a MISS whenever the prior report was minted under a non-empty anchor" is wrong as a general rule (ARM A misses when the *anchors differ*, not when the prior anchor is non-empty).
3. **The invariant the plan claims is not the invariant that holds.** `plan.md:164` / `design.md:248` claim "Exactly one report row per `(child, turn)` (ARM A + CAS rowcount)". What actually prevents a second report row for a child is: **(i)** the terminal-entry guard — any chain invocation on a `COMPLETED`/`ERROR` child short-circuits with `outcome="idempotency_skip"` before the mint (`daemon/services/child_reports.py:3781-3874`; the terminal-row/live-children exception at `:3785-3863` re-arms to `WAITING_CHILDREN` and still does not mint); **(ii)** the CAS precedes the mint in the same transaction (`:4901-4917` before `:4959-4974`, one commit at `:5762`); and **(iii)** the per-parent lock serializing the chain for one child (`daemon/services/child_reports.py:3524-3525`, `bus._get_parent_lock(instance_id)`; no lock only on the bus-less legacy path, which the code comments declare concurrent-writer-free). **ARM A is not the load-bearing duplicate guard** — and the plan never states the invariant that is.

**Fix (required):** rewrite `design.md §1.6`, `§2.5`, `plan.md §1.4` and the §2 "Empty-anchor convention" to the measured provenance table above; rename ARM A's purpose from "same-turn duplicate" to "**same-anchor (same-token) duplicate**" and restate the truth table with the token dimension; and add a new invariant row stating the real duplicate-freedom chain (terminal-entry guard + CAS-before-mint atomicity + per-parent lock). Add the pin named in A6.

### C4 — GAP-1 (the leader-approved new postgres-marked pin) is not propagated: L4 cannot carry a genuine-red-at-base proof

`plan.md:354` (GAP-1) requires ONE new postgres-marked file (`tests/postgres/test_child_reports_narrow_guard_pg.py`) because the existing PG pins are stay-GREEN nets that "cannot be RED at base". The leader approved it (commission item 5) and `plan.md:459` carries it as open item 1.

But the gate matrix still describes the pre-GAP-1 world:

- `gate-matrix.md:§5` — "**No new integration file is named by design.md §6.1** — the real-PG surface is the EXISTING obligation/boundary pins below"; L4 file list = the two existing PG pins + the boundary pin; "PACKS.md row | Existing postgres/boundary rows — **no new row**".
- `gate-matrix.md:§0` file map — no row for the new PG file; `§10` "PENDING slots" — no row either.
- `gate-matrix.md:§8.3` ownership chain, T-L4 row — lists only the existing stay-GREEN pins; the chain for the approved scope addition is **missing**.
- `design.md §6.1` — the file list the plan says must be extended (`plan.md:354`) was not extended.

And L4's own base expectation is incoherent: "Expected at BASE | **RED for the victim repro on real PG**" — but the victim repro lives in the UNIT rider pins (`tests/unit/services/test_stale_running_sweep_rider_pins.py`), not in the L4 file list, so on the listed PG files the base leg is GREEN, not RED.

**Fix:** propagate the new PG pin into `design.md §6.1`, `gate-matrix.md §0` (file map), `§5` (L4: file list + harness command + a real RED-at-base expectation + the new PACKS row), `§8.3` (T-L4 ownership row) and `§10`; correct L4's base expectation to "the new PG pin is RED at base; the existing PG pins are stay-GREEN".

### C5 — ARM A is load-bearing and **no mutation leg proves it**

The plan's mutation legs (L2a / L2b(b) / L2b(b2), `gate-matrix.md:§3`) are:

- **L2a** — no-op the **whole** disjunction ⇒ `tests/unit/services/test_b4_child_report_obligation.py` RED.
- **L2b(b)** — revert **ARM B** to anchor-exact-only ⇒ `test_prior_pending_report_under_a_different_anchor_blocks_a_second` RED.
- **L2b(b2)** — restore the lifetime prefix ⇒ the victim-class pins NEW-1/NEW-2 RED.

**ARM A is never mutated in isolation.** With ARM A deleted (guard collapsed to ARM B alone), every leg still passes: L2a still reds (both arms gone), L2b(b) still reds (ARM B gone), L2b(b2) still reds (lifetime prefix). ⇒ **ARM A could be dead code and the whole gate matrix would go green.**

**Why ARM A is load-bearing (measured):** ARM B's status set is `('ready','processing')` — it misses a **delivered** prior. The one path that re-supplies the *same* anchor is the router/deferred re-entry (`daemon/manager.py:12698` → `:12738`, `child_message_id` = the obligation-triple member). On that path, with a prior report row already `COMPLETED`, **ARM A is the only arm standing between the chain and a second report row + a second obligation for the same child** — b4's exact shape (2 obligations / 1 child, parent gets 2 reports), which the write-once index does **not** arbitrate (see C3/A-note: the unique index is keyed on `(parent_instance_id, child_instance_id, child_message_id)` — `daemon/repositories/report_injection/models.py:234-242` — so a *different* anchor is a *different* obligation, not a conflict).

**Fix:** add **L2c** — mutate **ARM A only** (collapse the guard to ARM B) and require a new pin to go RED: *"the same-anchor re-entry of a child whose prior report is already DELIVERED mints no second report row"* (drive it through `_reenter_completion_async`'s anchor, i.e. re-supply the identical anchor). Add the pin to `design.md §2.6` (NEW-5), to the rider-pins file (or a new method in the same class), to the L1 suite list in `gate-matrix.md §2.2`, and to the ownership chain.

---

## ADVISORY — should fix, does not block

### A1 — The entry-path enumeration is incomplete (question #1's list)

The plan enumerates: normal completion; retry/re-entry; the sweep DRIVE; RDRS recovery re-entry; report-delivery lane-6 (`plan.md:101`, `design.md:62-72`). Missing, all measured at base:

- `daemon/services/mission_strand_healer.py:772-774` and `:1316-1318` — **two more drive paths**, both `latest_msg_id or ""`, both reaching the same mint guard. They drive **root** instances, and roots take the root lane *before* the mint guard (`child_reports.py:3925-3960`, `:4306`), so they never mint — but the plan's §3 step 3 ("the fall-through traverses the tool-invocation branch … and reaches the CAS") should say that the root lane never reaches the mint guard at all, and `no_parent_id` (`:4650-4652`) is a secondary guard only.
- `daemon/manager.py:9044-9046` — the root stale-task-failure path (`message_id or ""`).
- `daemon/manager.py:12738` — the router/deferred re-entry, the **only** same-anchor path (see C5).

### A2 — On the suppress path the PARENT cascade is skipped; the sweep's `healed` verdict cannot see a still-parked parent

`plan.md:155` correctly places the parent cascade inside the mint block (`:5300-5762`), and `plan.md:141` correctly forbids falling through to the final return at `:5775-5792` (it references mint-produced locals — the "suppress-return trap", real and correctly named). But the consequence is not drawn: the inlined `_update_parent_on_child_complete` region (≈`:5519-5700`) is what advances the **parent** to COMPLETED when its last child finishes. On the suppress path the only parent-unpark mechanism is the B4 cycle-2 bus re-mint (`:6563` → `:6581` → `:6604-6614`), and that helper returns `[]` unless there is a **PENDING (parent, child) watcher** (`:6588-6597`). The sweep's verdict is about the **child's** status (`stale_running_sweep.py:1222-1231`), so a parent that stays parked is invisible to L5.

**Fix:** add a pin (or an explicit, evidenced statement) that the parent reaches completion on the suppress path — e.g. a NEW-2-style test asserting the parent's bus watcher is fired / the parent advances — and state the precondition (the wedge-victim shape always carries a PENDING watcher) with the evidence for it.

### A3 — §10.1's evidence numbers are not reproducible as stated (the VERDICT itself is correct)

Independently re-measured at base:

- `git diff --stat origin/latest...5368a12d4 -- daemon/services/child_reports.py` → **456 insertions / 74 deletions** ✓ matches `plan.md:388`.
- All four added defs exist at base (1 occurrence each): `_assistant_message_fresh`, `_gate_wedge_resolver`, `_latest_terminal_message`, `_root_completion_gate` ✓.
- Guard-token census over the branch diff (`internal_report|startswith|existing_report|idempotency_skip|should_send`) → **0 hits** ✓.
- `def`/`class` lines among the absent set → **0** ✓.
- **But:** my reproduction of "320 unique added lines > 12 chars → 68 absent from base" yields **359 / 184**, and the absent set is **not** only "log strings / docstring fragments" — it includes executable statements of the root-emission downgrade path (e.g. `row.status = InstanceStatus.WAITING_CHILDREN.value`, `downgrade_session.add(row)`, `parent_row.version = (parent_row.version or 1) + 1`).

**VERDICT confirmed and unchanged: `fix/empty-job-completed-event` @ `5368a12d4` is ANCESTOR-OF-LATEST for its subject matter and is NOT a distinct mint-guard variant — no overlap with this lane's touch set, no cherry-pick value, action for this lane: NONE.** Correct the numbers and the characterization (`plan.md:394`); the caveat "absence of evolved text is not evidence of a missing predicate" stands.

### A4 — PL-8's precedent is VERIFIED (no action, recorded so it is not re-litigated)

`git show 58fc594eb` confirms the commit message: *"both guards (`child_reports._should_send_completion_report` and the live inline guard in `_process_child_completion_db_sync`) now match"*, and `git log -S 'source.startswith('` shows that commit as the only one to have introduced the prefix form. The twin's deprecation note (`child_reports.py:1289-1298`, "kept verbatim (no deletion, no edits to the logic)") predates it, as `plan.md:453` states. The twin's "kept verbatim" sentence must still be corrected truthfully in the same commit (`plan.md:465`).

### A5 — Twin reachability: real but harmless; keep the lockstep edit

The twin is genuinely dead in production (docstring `:1289-1298`; the live site is the inline guard) and the live site explicitly names it as its sibling (`:4668`). The plan's PL-8 (keep in lockstep) is right, and `plan.md:453`'s claim that no existing twin test exercises the prefix arm holds (zero `internal_report:` literals in `tests/unit/test_ready_message_completion_report.py`). The twin pin (COMPLETED prior, different anchor) is the right shape. Only note: **the twin pin cannot substitute for the live-site L2c mutation (C5)** — a dead twin can drift forever.

### A6 — Add the pin that proves the *real* invariant

Because ARM A is token-scoped (C3) and the true duplicate-freedom comes from the terminal-entry guard + CAS-before-mint (§C3 consequence 3), the gate set needs one more pin (alongside L2c): **"two chain invocations for the same child yield at most ONE report row unless the child was revived in between"** — i.e. invoke the chain twice with the same anchor and with a *different* anchor against a non-terminal child, and assert one row. This is the pin that would catch a future refactor that reorders the mint before the CAS, or relaxes the terminal-entry guard — the two edits that would silently reinstate b4.

### A7 — The mutation legs DO cover the DRIVE-path shape (answering the question directly) — but the plan never says so

Measured: the b4 companion test drives the chain exactly as the sweep does — `_process_child_completion_and_notify_parent(CHILD, "", skip_repair=True)` (`tests/unit/services/test_stale_running_sweep_rider_pins.py:442-445`) — i.e. **empty anchor + drive-style invocation**, with a prior `READY` report under a different anchor, asserting exactly one row and the prior anchor (`:447-451`). And the row-(iii) drive shape (prior **DELIVERED**, different anchor ⇒ a second row is minted) is what NEW-1 pins in the same harness. So:

- "A plain revert would reinstate b4's duplicate" — the L2b(b2) lifetime-prefix restore is the mutation that catches the *victim* side; L2b(b) catches the b4 side; **L2c (C5) is what is missing** (ARM A's own non-vacuity).
- State in `design.md §2.6` / `gate-matrix.md §3` that the b4 companion test and NEW-1 are **drive-path** pins (empty anchor, `skip_repair=True`), so the coverage is legible rather than inferred.

### A8 — The ARM-B loss alternative: I agree with the rejection

The defer-and-retry alternative (`plan.md:107`) re-introduces a non-terminal window (the exact shape ruling #2 forbids) and makes the cure depend on a second drive (another age-bound window, `stale_running_sweep.py:1076-1104`, `:181`). There is no cheaper correct alternative: the only cheap improvement is the observability in **C1**, which converts an invisible loss into a countable one without changing the semantics.

### A9 — Two stale cross-references and one stale collision note

- `plan.md:331` points the ownership chains at "`decisions.md §5`" — that section is the **Component B risk register**; the chains are in `gate-matrix.md §8.3`.
- `gate-matrix.md §9.1` says `find_wake_already_delivered_evidence` is "reused by pre-flight, not changed" — the pre-flight is withdrawn, so the helper is not touched by this release at all.
- `gate-matrix.md §5` "Expected at BASE | RED for the victim repro on real PG" — see C4.

### A10 — New-1 codifies "exactly TWO report rows" for one child

`design.md:191` (NEW-1) asserts two report rows for a child under the new policy. That is *intended* (PL-6 variant B, K11), but the pin file should say so in its docstring, or a later reader will read it as a leak. `plan.md §11(b)` states it in the release notes ✓ — mirror it in the test docstring.

### A11 — Release staging / fences: verified sound

- Slot `v0.31.59` — free at plan time (`v0.31.55`→`dd007f16d`, `v0.31.56`→`ed8ab508c` taken; `origin/latest`=`ed8ab508c`); W9 re-derive-before-cut present (`plan.md:416`, `release-staging.md §1`); never re-cut a pushed tag ✓.
- Version-file rule with the five files + "make the landing take the HIGHER version" + post-merge assertion ✓ (`plan.md:418`).
- Ceremony binding stated in substance; INTERIM posture labelled interim and lifting on the stack promote ✓ (`plan.md:435`, `release-staging.md §8`).
- `rollback_safe` caveat: full-history derive ⇒ 78 ⇒ explicit `=1`, legitimate only under an EMPTY tag-to-tag migration delta, with the delta disclosed ✓ (`release-staging.md §4`); this release adds no `daemon/migrations/versions/*.sql` ✓.
- 4-way sha check + stage re-hash ✓; NO PROMOTE, staged-only ✓.
- Fences: no config flip, no migration, no lifecycle action, no new `instances.status` writer (CAS `:4901` stays the single writer) ✓.

### A12 — Component B: structurally sound, verified against the source

`daemon/tools/system_log_tools.py`: `MAX_LINES_SCAN = 50_000` (`:58`), `deque(maxlen=…)` (`:512`), `scanned` from the deque only (`:527`), the window-opaque no-match string (`:617-621`), the header (`:623-626`), the sibling read's 0-based `offset` skip (`:386-387`, documented 0-based at `:346`), the deliberately unannotated `context` (`:418`) — all as the plan states. The schema is a pure function of the signature (`daemon/tools/_schema_registry.py:26`), so appending `start_line: int | None = None` **last** is the correct no-shift move. The privileged-category surface is untouched by a parameter add. `PACKS.md` currently carries **0** rows for the new file (verified) ⇒ L7's "rows absent → rows present" is correctly stated. The only open item was the census placeholders (GAP-2) — legitimately deferred to the freeze and filled at the 2026-11-26 freeze.

---

## CRUX RECOMMENDATION (question #1)

**Entry paths into the mint guard, all measured at base, with the two-arm behaviour:**

| # | Path | Anchor supplied | Prior report = none | prior = OUTSTANDING (ready/processing) | prior = DELIVERED (completed) |
|---|---|---|---|---|---|
| P1 | Natural completion (resume-driven) | fresh uuid4 (`manager.py:16512` → `:16758`) | ARM A miss, ARM B miss ⇒ **mint** + terminalize | ARM B HIT ⇒ **suppress** + terminalize (new turn's report LOST — R-K2) | ARM A/B miss ⇒ **mint** (row iii: the parent's second report for the child) + terminalize |
| P2 | Router / deferred re-entry | the **original** anchor (`manager.py:12738`) | mint | ARM B HIT ⇒ suppress + terminalize | **ARM A HIT ⇒ suppress** + terminalize — *the only path where ARM A is the sole guard* (C5) |
| P3 | Sweep drive (stale_running_sweep) | `latest_msg_id or ""` (`stale_running_sweep.py:1208`, `:1266-1275`) | mint + terminalize ⇒ the sweep's post-read lands in `healed` | ARM B HIT ⇒ suppress + terminalize | ARM A/B miss ⇒ **mint** + terminalize ⇒ `healed` |
| P4 | Strand-healer drives (`mission_strand_healer.py:772`, `:1316`) | `latest_msg_id or ""` | n/a — ROOT lane (`:3925-3960`) runs before the mint guard; no mint | same | same |
| P5 | Root stale-task failure (`manager.py:9044`) | `message_id or ""` | n/a — root lane | same | same |

**Does the fix mint a SECOND obligation for the same turn on the DRIVE path of a child whose latest-turn report is already DELIVERED?**

**No — not for the same turn, and the reason is one the plan does not state.** On that path the drive mints (row iii), but what it mints is the child's **latest** turn — the revived turn whose report was never minted — carrying the child's last assistant content; the already-delivered report was the **previous** turn's. So the drive creates a **second report row + a second obligation for the child** (intended under the new policy: PL-6 variant B, K11 — the parent's turn-2 outcome reaches it), **not** a duplicate of the same turn.

The reason it cannot become a same-turn duplicate is **not** ARM A (which is token-scoped — C3) but: the terminal-entry guard short-circuits any later invocation on the now-`COMPLETED` child (`child_reports.py:3781-3874`); the CAS precedes the mint inside one transaction (`:4901` before `:4959`, commit `:5762`); and the per-parent lock serializes the chain per child (`:3524-3525`). A child can only carry a report row while being terminal, or after an explicit revive — a new episode, which the new policy intends to report.

**RECOMMENDATION — do NOT change the drive path's anchor.**
Resolving a "real `completed_message_id`" on the drive path would (a) not improve duplicate protection — the protection comes from the terminal-entry guard + CAS ordering, not from the anchor; (b) require re-deriving a per-turn key that the natural path does not supply today (its token is a fresh uuid4, `manager.py:16512`), i.e. it would change the anchor semantics on **both** paths, alter the obligation-triple member `child_message_id` (`report_injection/models.py:147-151`) and the delivery-evidence consumers — a semantic change **beyond the narrow**, which the fence forbids; and (c) make the drive path's mint depend on a checkpoint read that the plan already prices at 2–7 s per victim (`plan.md:177`).

Instead, before implementation:
1. **State the real invariant** (C3 consequence 3) and pin it (A6).
2. **Correct the id-space model** (C3) — the anchor is a per-call token; ARM A is a same-anchor guard; the drive anchor is normally a real queue-row id, not `""`.
3. **Add L2c** proving ARM A is load-bearing (C5).
4. If the leader wants ARM A to become a true per-turn key (a strictly stronger policy than "one outstanding obligation per child"), that requires making the anchor turn-derived on **both** paths — `manager.py:16512` plus the obligation-triple semantics — and belongs in a **separate lane**, explicitly out of scope for v0.31.59. Record it as a future item, not as an implicit part of this release.

---

## Question-by-question disposition

| # | Question | Disposition |
|---|---|---|
| 1 | Entry paths; drive-path second obligation; mutation coverage of the drive shape | Answered in §CRUX + A1 + A7. Drive-path mint = new turn, not a same-turn duplicate. Drive shape **is** covered (L2b(b)+NEW-1) but the plan never says so; **ARM A's non-vacuity is uncovered (C5)**. |
| 2 | ARM B's loss case; better alternative | Agree with rejecting defer-and-retry (A8). The leader's conditional acceptance is **not implemented (C1)**. |
| 3 | Decoupling correctness | Verified: CAS `:4901` precedes the mint `:4959` in one transaction; `:4901` stays the single status writer; B4 gate `:6563`/`:6581` fires once the row is COMPLETED; the suppress-return trap at `:5775-5792` is real and correctly avoided. **But** the write-once index is keyed on `(parent, child, child_message_id)` (`report_injection/models.py:234-242`) ⇒ it arbitrates **same-anchor** duplicates only, not one-per-child — the per-child property came from the guard being narrowed (state this). Concurrency is serialized by the per-parent lock (`:3524-3525`). Parent-cascade skip on the suppress path → A2. |
| 4 | Riders / sweep | The plan's PL-7 ("no sweep change") is **correct** and its rejection of the pre-flight is **correct on both measured grounds** (no anchor ⇒ `:4579-4590` returns before `suppress_mint` is set — verified at `:4579-4590`; and `find_wake_already_delivered_evidence` is a lifetime-prefix any-of check over `('ready','processing','completed')` — `message_queue/repository.py:277-330` — so it cannot separate the live-victim shape). The sweep's selection is on completion evidence, not on `last_activity_at` (`:1076-1104`; `last_activity_at` never read by the sweep) ✓; the ≤7200 s young window for pre-fix victims (R-K3) is real and correctly disclosed. **Unaddressed in the plan:** the two mission_strand_healer drives (A1) and the parent-side unpark on the suppress path (A2). |
| 5 | Gates | L1 is thorough (temp worktrees, per-variable scrub, `.git` pre-flight, digest FORM, `comm` both ways) ✓; RED-at-base with only the new/changed test files applied ✓; L2a/L2b present but **ARM A unmutated (C5)**; L4 **cannot** carry a genuine red-at-base as written (C4); victim-class + PACKS registration ✓ (PACKS rows currently absent — verified); ownership chain complete **except** for the GAP-1 PG pin (C4). |
| 6 | Fences | Verified sound (A11): no config flip, no migration, staged-only, no lifecycle action, ceremony binding + INTERIM posture, slot `.59` with W9 + version-file rule + post-merge assertion. |
| 7 | §PREDICATE LEDGER vs body | The canonical strings agree **character-for-character** between `plan.md:270-289` and `design.md:376-391` (P-ARM-A-PY, P-ARM-B-PY, P-SUPPRESS, P-EARLY-RETURN, P-MINT-SOURCE, S-A/S-B, P-B4-GATE). The DELETION RECORD correctly withdraws P-GUARD-PREFIX-ONLY / P-GUARD-ANCHOR-EXACT / P-PREFLIGHT / P-PREFLIGHT-DISPOSITIONS / P-CURE-STATUS / P-NEW-CLASS-LOG. **But** the withdrawn pre-flight survives as live instructions (C2) and the ledger lacks the R-K2 observability rows required by the leader (C1) — add `P-ARM-B-SUPPRESS-LOG` / `P-ARM-B-SUPPRESS-COUNTER`. |
| 8 | §10.1 verdict on `fix/empty-job-completed-event` | **VERDICT CONFIRMED** (ANCESTOR-OF-LATEST, not a guard variant, no overlap, no action). Evidence numbers partly not reproducible — A3. |
| 9 | Twin guard | The twin is real and reachable-as-source-only; PL-8's lockstep edit is right and the precedent is verified (A4). A diverged twin IS a trap (it is the canonical copy an implementer will copy from) — the lockstep pin is required, and it cannot replace the live-site L2c mutation (A5/C5). |

---

## What I verified as CORRECT (do not re-check)

- The live guard is at `child_reports.py:4677-4690` (the brief's "~1394-1419" is the dead twin) — `plan.md:74` / `design.md:100` are right.
- The wedge chain (`:4677` guard → `:4698` early return before `:4901` CAS → B4 gate `:6563`/`:6581` no-op → sweep drive `stale_running_sweep.py:1207` → `class=drive-no-effect` `:1259-1264`) is accurate.
- 58fc594eb widened both guards in lockstep (git-verified) — the root cause and b4's intent are as stated.
- The sweep selects on completion evidence (`:1076-1104`), never on `last_activity_at`; bound `7200` (`:181`), cap `25` (`:184`).
- `message_queue.source` is unindexed (`models.py:64`), `instance_id` is indexed (`:61`) — the single-query-with-OR choice is sound and needs no migration.
- The RDRS backstop correctly stays per-child; the plan's refusal to make it per-turn is right (it would re-introduce the RESUME_ROUTER duplicate).
- The pre-flight's two rejection grounds are both real (verified above).
- Component B's surface, schema derivation, byte-identity strategy and the privileged-pin surface argument all check out.

---

## Gaps I could not close (unverified)

- No test run, no venv, no PG leg — every gate statement is a **design** judgement, not an executed result. The GAP-2 census placeholders were open by design and are now filled at the 2026-11-26 freeze.
- The live victims `a5c2c2be` / `99b3c1a7` were not inspected (read-only lane, no live DB contact); the victim-class repro is a UNIT-shape pin, not a live probe.
- The exact runtime value of `latest_msg_id` for a live wedged child was not measured against the live DB (the code path shows a non-empty value whenever the child has ≥1 completed inbound queue row, which the revive message guarantees — but that last step is inference, not a live read).
- `origin/latest` was read as `ed8ab508c` per the plan; the slot re-derivation must be repeated at bump time (W9).

---

## Recommended next step for the leader

1. Return the plan to the finisher with **C1–C5** (C2 and C3 are the ones that change what gets built, not just what gets written).
2. On the return, re-check: (a) the R-K2 observability rows exist in both ledgers and the body; (b) the word "pre-flight" and the strings `cured_already_reported` / `class=cured-already-reported` / `cured=%d` appear **nowhere** in an instruction position (only in the DELETION RECORD); (c) the id-space section matches the measured provenance table; (d) L2c + the NEW-5 pin exist and the L1 suite list includes them; (e) the GAP-1 PG pin appears in `design.md §6.1`, `gate-matrix §0/§5/§8.3/§10` and the L4 base expectation is RED-on-real-PG.
3. Then proceed to implementation; the design itself does not need re-litigating.
