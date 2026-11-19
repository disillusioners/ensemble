# Plan Overview: wake-path-carrier-fix — PHASE B FINAL PLAN (RECONCILED, COUNCIL-FOLDED)

**Date:** 2026-11-17 census @22:55Z; **reconciliation pass 2026-11-18 ~01:0xZ** (post-council). Plan author re-measures at every pass.
**Author:** worker (sole plan author for this lane) via `plan-creation` skill
**Status:** **RECONCILED — ready for the approver gate.** Shape set **FROZEN** by council (unanimous, zero diff at shape level, no re-architecture).
**Base commit:** `eca98397d9e0109611764ac418132c9d35afde2c` (verified `git rev-parse HEAD` == `git rev-parse origin/latest`; branch `feature/wake-path-carrier-fix`; working tree clean; artifacts untracked).
**Worktree:** `/home/nea/ensemble-src-wt-wake-path-carrier-fix` (ABSOLUTE paths; `/home/nea/ensemble-src` NEVER measured).
**Gates:** planner → architect → council (**done**) → **approver (ALWAYS a fresh instance)** → implement → tidier → tester → reviewer → staged release.
**NO LIVE PROMOTE.** The live promote is the Emperor's ceremony.
**Companion artifact:** `.../wake-path-carrier-fix/evidence-register.md`.

**Council panel outcomes folded:** DESIGN **APPROVE-WITH-ADVISORIES** (7 blocking) · EVIDENCE **REJECT-AS-WRITTEN** (curable by bounded doc edits, zero new investigation) · MINIMALITY **APPROVE-WITH-ADVISORIES** (6 blocking + 1 scope decision). **CONVERGENT: {S1,S2,S3,S4} is minimal and CONFIRMED — FROZEN.**

---

# CHANGES — RECONCILIATION PASS (items 1-21 + C-checks) AND APPROVER ITERATION 002 (B1-B5 + N1-N10 + anchor fix)

| Item | Adjudicated requirement | Landed at |
|---|---|---|
| **C-3** | Resolve the ERROR-lane dispatch question FIRST | **§A3 — RESOLVED NEGATIVE**, with quotes. ERROR lane does **not** run `_dispatch_post_commit_side_effects`; it runs `error_reporting._send_error_report` (`:439`) which emits at `:612` / `:654` / `:708` / `:765` |
| **ERROR ruling** | Branch (A) fold the ERROR outcome into S1, conditional on C-3 | **§A3 ADOPTED (A)** — C-3 negative ⇒ the *site* moves, not the shape. ERROR hook = `daemon/services/error_reporting.py:439` (**measured ZERO unmerged-commit pressure**). COMPLETED hook = `child_reports._dispatch_post_commit_side_effects` (`:5285`). Also folded into §(a.2), §(f) T1b, §(i), Phase 1 tasks 3a/3b |
| **1** | S1 hook site = `_dispatch_post_commit_side_effects` (`:5285`), single call site `:3130`, unconditional for `:5483`/`:6161`/`:6195`; DELETE observer framing; DELETE "entirely uncontended"; PRICE the contended cost | **§(a.2)** rewritten; **§(i)** row 8 re-priced; **§11 insight 9** rewritten; the observer is named **DEAD** in §A3 |
| **2** | S1 placement/ordering — fire AFTER the outcome branches (post-`:6256`) or a per-row delivery filter; + the discriminating test that the structural parent receives NO bus FollowUp on the healthy path | **§(a.2) step 3 + §PREDICATE LEDGER `DELIVER(w)`**; **T1b** added to §(f) |
| **3** | S1 delivery = full TERMINATE precedent: dead-letter, re-purge, `source_task_id` enrichment; the nudge payload LACKS `source_task_id` ⇒ explicit sub-task | **§(a.2) step 4 + Phase 1 tasks 2a/2b**; **§PREDICATE LEDGER `ENRICH`** |
| **4** | Delivery durability stance — at-most-once vs at-least-once EXPLICITLY; guarded UPDATE is fire-once not enqueue-once; crash-after → DOUBLE (Pass 1 has no paused filter), crash-before → LOST (`_recover_fired_unsent` only lists); add enqueue idempotency or a distinct delivery marker; make Pass 1's predicate sound; `enqueued_at` semantics already split across four write sites | **§(a.5) DELIVERY DURABILITY STANCE** (new, first-class) + **§PREDICATE LEDGER `DELIVERY-ONCE`** + Phase 1 tasks 2c/2d + risk 2 |
| **5** | Matcher robustness — `fetch_pending_for_child_instance` keys on child_id alone across every target ⇒ multiple nudgers / stale episodes all fire ⇒ false completion notices = a NEW wedge shape; key on an invariant or refuse `watch()` without child_id; add episode/generation discrimination **(the discriminator instruction is SUPERSEDED by the leader's iteration-003 ruling: NO discriminator — plain `FIRE_SET` + `watch()` refusal; see the ITER-003 rows)** | **§(a.6) MATCHER ROBUSTNESS** (new) + **§PREDICATE LEDGER `FIRE_SET`** + Phase 1 task 1b + risk 4 + risk 21 |
| **6** | Re-pin S1's delivery precedent and step order; add dead-letter skip, `finally` re-purge, `source_task_id` enrichment as Phase-1 acceptance bullets | **§(a.3) table** re-pinned to `instance_lifecycle.py:315-577` (delivery slice `:469-540`) with the **true order**; Phase 1 tasks 2a/2b acceptance |
| **7** | Re-anchor S3's liveness read to `get_statuses` (`repository.py:1071`), batched; NOT the parent-linkage-keyed `:3838-3916` (nudge watchers are not tree-linked ⇒ S3 as cited cannot cure the live wedge, T3 passes vacuously); fail-closed posture for missing child_id / ghost child = FULFILLABLE | **§(c.2)** rewritten + **§PREDICATE LEDGER `FULFILLABLE`** + Phase 3 tasks 1/2/2a |
| **8** | *(iter-002 instruction — **PARTLY SUPERSEDED** by approver B5: `:3379-3437` is a child-liveness door and must NOT be switched, while `:3664-3698` — the real park door — must be)* S3 scope across the root-lane doors; full sync-reader census; reframe the raw boundary; name the unswitched completion doors | **§(c.3)** reader table rewritten — **as landed: 6 SWITCHED (R1-R6) / 3 unswitched completion doors named (D-2/D-3/D-4)**, the D-1 door promoted to R4 by approver B5 and `:3379-3437` withdrawn as a child-liveness door + Phase 3 tasks 4/4a/4b/4c |
| **9** | S4 dedupe — split the clocks; the 1800 s window suppresses **0 of live repeats** against the 3600 s scan default (`config.py:2619-2622`); the constant's docstring (`:188-196`) defines it as a re-arm allowance; use episode-persistence with the TTL **DROPPED ENTIRELY** for suppression; episode identity = `parent_id` in `_wedge_notified` (`:561/:581`); pops only on genuine condition-false (`:1460/:1466`); restate T5 as one notice per episode; update success criterion #5; fix the digit-drop | **§(d.2)** fully rewritten; **T5** restated; **success criterion #5** restated. **Digit-drop verified ABSENT** (PO:237 already reads `3600.5-3600.7 s` — recorded as a no-op with evidence) |
| **10** | S4 population = the qualified-unfulfillable set (terminal child or self-referential); raw PENDING would put ~8 legitimately-parked instances in scope ≈ 9× notice-volume regression | **§(d.1)** + **§PREDICATE LEDGER `S4_NOTICE_SUBJECT`** + Phase 4 task 1 |
| **11** | S4 kill-switch — the registry key alone is VOID (`_enabled` is boot-time at `:536`); new per-tick read code required (precedent `metrics_gate.py:131`; `get_overrides` per-call DB read `:1111-1116`); ADD `daemon/services/settings_service.py` to §(i) (ZERO unmerged pressure, 1377 lines); correct the key count to **6** `requires_restart=False` keys; the notice's `:433` report line is a retained-verbatim risk | **§(d.3)** rewritten; **§(i)** row 12a added (`settings_service.py`); key count corrected to **6** (measured `:519/:545/:623/:644/:661/:678`); `:433` handling in §(d.1) |
| **12** | Reconcile the watcher census 2-vs-4 with a timestamped paragraph naming row ids; use "total PENDING" vs "structurally dead" consistently; fix the arithmetic (2+10+0 = 12 ≠ 13) | **§L-CENSUS** (new, timestamped) + register §6 L-6 rewritten |
| **13** | Rewrite §(h) around the **8-row** repair artifact that EXISTS; fold the widened scope + the leader ruling's four points; the CORRECTED restart analysis; the clock-pressure caveat; a pointer+status to the repair bundle | **§(h)** fully rewritten (8 rows named; restart re-analysed as of ceremony time; TTL clocks named; external-bundle citation) |
| **14** | Closure-claim scoping — do NOT assert "class closed"; name the one surviving monitorable door + the backlog residual + the engineering constraints; register each hole with an owner | **§A5 CLOSURE CLAIM (SCOPED)** (new) |
| **15** | Disclosures (iv)/(v) rewording — (iv) the ERROR lane is UNDER-DISCLOSED and load-bearing (mission-HOLD consequence); attach the (A)/(B) decision. (v) backlog rows — add the named one-query deciding evidence + a revisit trigger tied to the ERROR door | **§(j)(iv)** and **§(j)(v)** rewritten |
| **16** | S2 refusal text — name the sanctioned alternative (`send_message` to yourself, tree-guarded at `tools/instance.py:1273-1275`); keep full refusal; optional warning; flag the playbook-anchor discrepancy (`watchdog.py:262-270` vs `:436-438`) | **§(b.3)** (new sub-section) + Phase 2 tasks 1/1a/1b |
| **17** | Anchor re-pin batch (12 anchors; PACKS.md cited by NAME not line) | **§A1 re-pin table** + `evidence-register.md` §1/§3/§5/§7 patched |
| **18** | The `06f500af` correction — an INSTANCE id, NOT a commit; docstring introduced by commit `4926a2eb6`, lives at `:1597-1604` inside `cancel_for_source`'s docstring; `:2514-2515` mislabels it (pre-existing doc defect — note, do NOT fix) | **§A1 correction C-06f** + register §1 |
| **19** | Contested-tip posture — REPLACE "re-measure at the contended tip"; the contention is a STALE FORK from 2026-09-20 that neither descends from nor contains base; ZERO plan anchors at its tip; DO NOT re-pin anything to it; implement against base; if it is ever rebased and lands, re-measure only the root-gate and dead-letter-publish hunks; same staleness for `feature/langgraph-checkpoint-perf` @ `c37c870c56` (2026-08-26); implementer pre-flight on 4 files | **§(i) CONTESTED-TIP POSTURE** (new) + Phase 1 task 0 (pre-flight) |
| **20** | Drop `watch_reconcile_sweep` from the bus-caller list (JOB-watcher system, zero dependency-bus hits) | **§(e) reason (iii)** rewritten; register §3 patched; the file is **removed** from the file list |
| **21** | Name the census gap — `tool_invocation_completed` (`:6161-6195`) fires NO bus terminal at all | **§(a.1) CENSUS GAP** + §A2 + §(f) T1c |
| **E C-1** | RESOLVED: nudge mint ordering = enqueue → commission → watcher, early return on enqueue exception (`instance_ops.py:1545-1670`); failed enqueue mints nothing — add the ordering assertion | Phase 1 task 0b + Phase 2 task 2a |
| **E C-2** | OPEN: does the healer's drive-side in-session bus gate read the RAW count? If raw ⇒ a SIXTH switched reader | **§(c.3) RQ-C2** + Phase 3 task 6a |
| **E axis-1** | Implementer's still-asserted list: observer Step-2 bypass reachability (`:4517-4520`); `enqueue_message` full-body dedup absence (signature only); Phase-1 pack wake-assertion coverage | Phase 1 tasks 0c/0d + Phase 5 task 0 |
| **B1 (iter 002)** | S1 hook placement could not reach the claimed outcomes — every canonical branch early-returns (`:5345/:5360/:5448/:5622/:5694/:6014/:6123/:6139/:6158/:6192/:6464`); "post-`:6256`" was INSIDE one branch; root_completed emits NO bus terminal (`parent_id=None`, `:4060-4069`); the DELIVER "equivalence" clause was false | **§(a.2) step 3** rewritten to **PER-OUTCOME placement** (regular→before `:6464`; tool→before `:6192`; root→before `:6014` ONLY); DELIVER equivalence clause DELETED; reachability restated ("METHOD reached unconditionally; HOOK placed per-outcome"); **T1f added** → §(f), Phase 1 tasks 3a/6 |
| **B2 (iter 002) — SUPERSEDED (iter 003)** | The generation discriminator was unimplementable (in-memory, per-parent, cleared at `stop`, not persisted on rows; per-parent monotonicity would strand a just-armed row = a NEW WEDGE). **The iter-002 cure was ALSO wrong — see the iter-003 row below.** | **DELETION RECORD:** `STALE_EPISODE(w)` **removed** from the ledger, the §(a.6) body, task 1b, T1d and the attestation; `FIRE_SET` is the **plain matching form**. The only surviving fragment of B2 is the **`watch()`-refuses-missing-`child_id`** guard (load-bearing) |
| **B3 (iter 002)** | `DELIVERY-ONCE`'s "same transaction as the enqueue" is unachievable — `manager.enqueue_message` owns its own session (`instance_lifecycle.py:6015-6018` verbatim) and §(g) r4 forbids altering it | **Cured**: "marker committed BEFORE the enqueue, in its own transaction" — **the CONTRACT was later corrected to AT-LEAST-ONCE at impl-review (see the IMPL-REVIEW / 5 row); the marker does not prevent re-delivery**; the residual window is now stated as the gap between two transactions → §(a.5), ledger `DELIVERY-ONCE`, Phase 1 task 2c |
| **B4 (iter 002)** | Episode pop sites misidentified (`:1460` = hang-pass purge tail; `:1466` = comment header) and a third omitted; `:1746` pops on WC-membership while the notice's own enqueue flips WC→RUNNING (`:1643-1645`) ⇒ the plan's rule was self-contradictory and T5 non-deterministic | **Cured**: the THREE pop sites named (`:1607/:1617/:1746`); **episode-end re-based on the ROW-CHECK (`S4_NOTICE_SUBJECT`), NOT WC membership**; T5 made deterministic → §(d.2), ledger `EPISODE`, T5, Phase 4 task 4 |
| **B5 (iter 002) — MOST CONSEQUENTIAL** | `:3379-3437` is a **CHILD-LIVENESS** door (counts child-instance rows, `:3397-3410`; writes WC `:3425`) — NOT a bus-count door; switching it would break the terminal-row reactivation/ghost net. **The REAL third raw-count PARK door is `:3664-3698`** (inline `SELECT count(*) FROM dependency_watchers WHERE target=… AND state='PENDING'` → `instance.status = WAITING_CHILDREN` + `commit()` `:3688-3691`) — previously misfiled as D-1 "not a park". INTERACTION: R2/R3 qualified (0) ⇒ fall-through ⇒ `:3664` still raw ⇒ **the root park persisted** | **APPROVER'S PREFERRED CURE ADOPTED**: **`:3664-3698` SWITCHED** (in-transaction — a SYNC TWIN + in-transaction SQL form; **N7 folded here**, one task); **`:3379-3437` DROPPED from SWITCHED** (documented as a child-liveness door); **D-1 deleted** from the unswitched list (it IS a park); **three unswitched doors remain** (D-2/D-3/D-4); §A5/§(c.3)/criterion #6 re-scoped and **re-attested** → §(c.3), §A5, §(g)(ii), Phase 3 tasks 4b/4c, criterion #6 |
| **N1 (iter 002)** | The architecture companion said "at the top of that method" — the implementer reads BOTH | **architecture-recommendation.md §38 line ALIGNED** to per-outcome placement (same lane dir, edited) |
| **N2 (iter 002)** | Phase task counts wrong (P1 16 vs 17 actual; P4 12 vs 9 actual) | **reconciled: P1 = 17, P2 = 7, P3 = 13, P4 = 9, P5 = 7** — §7 phase table updated |
| **N3 (iter 002)** | The ledger's `w.metadata.child_id` is not the real path — it is `w.follow_up_payload.metadata.child_id` (DB column `watcher_metadata`, `models.py:186-193`) | **path notation fixed EVERYWHERE** (FULFILLABLE, DELIVER, FIRE_SET, §(c.2) table, §(j) table, §(g), attestation rows 1/4/5/12) — ATTESTATION re-attested |
| **N4 (iter 002)** | `RAW_PARK` ledger said "observer + recovery only" vs 9 raw consumers in §(c.3) | **RAW_PARK string rewritten**: "`count_pending_for_target(parent) > 0` — read by NINE named consumers (observer ×5, job_recovery, child_reports helper :378 + 5 callers, error_reporting :263, manager :13852) — **none of them decides a park after S3** (the three raw PARK doors are switched; D-2/D-3/D-4 defer completions, not parks)" → ledger row 4 |
| **N5 (iter 002)** | T1/T1c/T1e are tautology-prone unless driven through REAL entry points | **entry-point harness stated per test** → §(f) harness column |
| **N6 (iter 002)** | Criterion #13 was a text check with no row-state test | **`DELIVERED_MARKER` row-state assertion added** (the marker IS a row state) → §(f) T7 extension + criterion #13 rewritten |
| **N7 (iter 002)** | S3 needs a sync twin + in-transaction form (R2/R3 run in a worker thread) | **folded into B5's cure** — one Phase 3 task (4c) |
| **N8 (iter 002)** | `DELIVER(w)` needs a batched parent_id read (`get_statuses` returns status only) | **folded**: the delivery filter reads structural parent via the same batched-read helper shape (`get_statuses` + a `get_parent_ids` twin) → §(a.2) step 2, ledger `DELIVER` |
| **N9 (iter 002)** | §(a.1) F1 caller list omitted `:1087` | **added** to §(a.1) and register §1 (an internal already-fired skip inside F2's loop — noted as a skip site, not a caller seam) |
| **N10 (iter 002)** | Phase-5 task 1 dependency "1-4" → "Phases 1-4" | **fixed** |
| **Anchor fix (approver)** | F2's "Short-circuits on `parent_instance_id is None` (`:676-683`)" — **NO such guard in dependency_bus.py** (`:676-683` is F1's docstring region); the short-circuit lives in child_reports' corrective helper | **§(a.1) F2 anchor re-attributed to `child_reports.py:676-683`** (conclusion holds); register §1 corrected |
| **ITER-003 / B5-adjudication** | Leader ruling: B2's `STALE_EPISODE` cure was WRONG — keyed on the row's `source_task_id`-terminal it is TRUE at fire time and would drop the live row `8a1f60d7`; and `terminated`/`error` are not task-status values (type confusion) | **`STALE_EPISODE` DELETED**; **`FIRE_SET` = the plain matching form** (no staleness term); **`INSTANCE_TERMINAL_SET`** renamed to make the instance-status type explicit; `FULFILLABLE` keys on the **child's INSTANCE status**; `watch()`-refuses-missing-`child_id` **kept** (load-bearing); the one-spurious-wake residual **disclosed at risk 21**; the two predicates are **complementary** (§(a.6) bullet 2) | **ledger rows 4 + 4a + 12**, §(a.6) bullet 2, Phase 1 task 1b, T1d, risk 4 + **risk 21**, attestation rows 4/4a/12 |
| **ITER-003 / blocking 1** | Register §12b still prescribed the withdrawn generation mechanism (`get_generation`/`increment_generation`) | **register §12b rewritten** to the adjudicated design (plain matcher + `watch()` refusal + S3 carries "terminal") | `evidence-register.md` §12b |
| **ITER-003 / blocking 2** | `PARK(parent)` ledger cell falsified PO:949 — it read `R1-R3` / `R4-R5` (pre-B5) | **cell now reads `park sites R1-R4` / `healer legs R5-R6`**, agreeing with `RAW_PARK` and §(c.3) | ledger row 2; attestation row 13 |
| **ITER-003 / blocking 3** | Phase-4 task 4 (PO) + register P-episode taught the B4 defect — popping at `:1460`/`:1466` (a **log-string tail** and a **comment header**, not pop sites) and omitting the row-check rule | **the THREE REAL pop sites named** (`waiting_children_watchdog.py:1607/:1617/:1746`); **episode end = the `S4_NOTICE_SUBJECT(parent)` row-check being EMPTY**; `:1746`'s WC-membership pop **re-based** | Phase 4 task 4; register P-episode row |
| **ITER-003 / blocking 4** | `FIRE_SET` string mismatch against a character-for-character attestation | **ledger and attestation now carry the SAME plain string** — verified byte-identical by the sweep (see the report) | ledger row 4 ≡ attestation row 4 |
| **ITER-003 / blocking 6** | COMPANION CLUSTER STALE — `architecture-recommendation.md:140` (R3 = `:3664-3698` as switched but instructing `:3379-3437` and listing `:3578` unswitched) and `:148` ("≥7" restart keys); register `:94/:95/:98` pre-B5 door story | **all aligned**: six switched (R1-R6 incl. `:3578` and `:3664-3698`), `:3379-3437` withdrawn as a child-liveness door, **6** `requires_restart=False` keys measured | `architecture-recommendation.md` §4.4 + §5.1; register P-park-2c / P-park-3 / P-park-x |
| **ITER-003 / blocking 7** | FALSE CROSS-REFERENCES — attestation cited a nonexistent "risk 5 (iii)" (it is risk 4); CHANGES item 8 said "5 switched / 4 unswitched" vs the landed 6/3 | **both fixed**: the risk reference is gone with row 4a's rewrite; CHANGES item 8 now records **6 switched / 3 unswitched** | attestation row 4a (deletion record); CHANGES row 8 |
| **ITER-003 / notes** | T1b/T1d outcome classes; refusal anchors `:655`/`:777`; Phase-3 count 13 vs 14; register §7 watchdog 47→49 vs 51 defs; tail anchor `:6471+`; N9 bare `:1087`; branch census 19 → 22 | all folded: **T1b + T1d name `regular_child_completed` and their harness**; refusals → **`instance_ops.py:894/:1332/:1768`**; **Phase 3 = 14**; watchdog pack **49** vs **51** defs at base reconciled in register §7; tail → **`:6469+`**; N9 → **`daemon/services/dependency_bus.py:1087`**; census → **22 code-bearing branches** | §(f), §7 table, Phase 2 tasks, register §1/§7/§8 |
| **ITER-003 / risk line** | The approver disclosed a live recurrence during iteration 002 | **risk 22 added**: anomaly **`1be5734c`** (cluster **`51b544f3`**) — the cures worker's report arrived as an EMPTY PARENT WAKE and the revive-enqueue re-delivery was never consumed (1 h+ idle, recovered via transcript) — cited as the lane's **live urgency evidence** | §9 risk 22 |
| **ITER-003 addendum** | Contention census refreshed over the **UNION of unmerged branches** — local-only branches were previously unswept (only the 19 `origin/*` remote-unmerged branches had been swept), hiding contention from `feature/video-mock*`, `feature/self-restart-drill-fixes` and `feature/skill-capture-gates` | **§(i) rows updated**: row 9 (`instance_lifecycle.py` → **3**), row 10 (`tools/instance.py` → **3**), + a hard-exclusion contention line (`manager.py` → **5**); a **CENSUS BASIS** line added (19 remote-unmerged / 27 local-unmerged / union 41; caveat that the union sweep under-reports remote-only contention). **No predicate/ledger/attestation impact; no footprint change** — all three are **AVOID / hard-excluded** files (S2's guard lands in `instance_ops.py`, S1's hook in `child_reports.py`, and `instance_lifecycle.py:385` is only a fence observable) | §(i) rows 9/10, the hard-exclusion contention table, and the CENSUS BASIS line |
| **FOLD-R3 / 1 — R1 adjudication (episode-end semantics)** | Reviewer adjudicated: read failure ⇒ UNKNOWN (never "stopped holding"); `_in_scope` fail-closed on a failed subject read; `queued_report` tri-state; r3 fence (every change gated on a read failure ⇒ healthy path byte-unchanged). The body's rule WINS; the code fix is in flight | **ONE LINE ADDED** to §(d.2); the generalisable lesson recorded as §11 insight 12. **No ledger / attestation change** (the rule string is unchanged) | §(d.2), §11 insight 12 |
| **FOLD-R3 / 2 — D1 contract completion** | §(a.5) / ledger row 7 / criterion #13 / risk 4b must carry the FINAL wording: at-least-once with a bounded duplicate window; the crash window is EXACTLY ONE ROW and EXACTLY ONE DUPLICATE; the compensating control is that the duplicate is a MESSAGE not a park (the parent wakes twice, the second turn no-ops). Also note the "exactly-once" mentions legitimately scoped to the guarded `transition_state` FIRE primitive | **already in the landed text** (§(a.5), ledger row 7, criterion #13, risk 4b, attestation row 7 all carry the at-least-once contract); **one clarification added**: the `transition_state` FIRE primitive's exactly-once is a **different layer** from the delivery layer's at-least-once — a future cleanup must not conflate them | §(a.5), ledger row 7, attestation row 7, criterion #13, risk 4b |
| **FOLD-R3 / 3 — deferred register F-1..F-6** | Six reviewer advisories, with the corrected framings (F-1 = "uses old name only", not "21 mocks"; F-2 coverage gap not defect; F-3 disclosure only; F-4 stale QUARANTINE.md prose + correct attribution; F-5 already satisfied, append evidence; F-6 (a) refresh stale pack row, (b) census task with no comparative verdict, (c) one-line gate-record obligation for anomaly 9822b90d) | **§(j)(vii) extended** with D4, Res5, Res7, Res8, Res9, the error-lane fallback, `TERMINAL_INSTANCE_STATUSES` name shadowing, the missing gate-leg test, the module split, and the marker-aware Pass-1 filter | §(j)(vii) |
| **FOLD-R3 / 4 — two registered facts** | (a) `TERMINAL_INSTANCE_STATUSES` consolidation deliberately refused (deterministic bind order load-bearing for the rendered `IN (...)`); (b) PG list now EXECUTED (real PG 16.15, throwaway `ensemble_test`, 111 tables before/after, 12 scratch rows cleaned with count=0 proof); highest-risk path proven (`mark_delivered_watch_id` CAS True→False + 5/5 exactly-one-winner) | **register §10a added** with both facts; the "no PG on this box" caveat is **RESOLVED WITH EVIDENCE** | evidence-register.md §10a |
| **FOLD-R3 / 5 — live-unblock discharged** | The Emperor-attested 5-block repair EXECUTED at 21:18-21:20Z; parent 17efd2ef completed 21:21:24Z; wedge notices ceased; `--verify` checks 1-4 PASS; zero drift | **§(h) STATUS flipped to DISCHARGED-BY-EXECUTION**; register §13 flipped to the same; §(h) retained as historical procedure + executed record | §(h), register §13 |
| **FOLD-R3 / 6 — release section** | Staged-release packaging is STILL OWED: bump commit → annotated tag → stage wt detached at the tag → FE rebuild → stage.sh, per r7; version slot DERIVED AT BUMPTIME, never reserved (r7 + cn 84e24343); NO live promote | **§5 release section refreshed** (already stated at §(j) and Phase 5; restated verbatim in one place) | §(j) §5 release, Phase 5 |
| **IMPL-REVIEW R1 adjudication** | The reviewer adjudicated the EPISODE end-rule: read failure ⇒ UNKNOWN (never "stopped holding"); `_in_scope` fail-closed on a failed subject read; `queued_report` tri-state; r3 fence (every change gated on a read failure ⇒ healthy path byte-unchanged). **The body's rule WINS and the code is being fixed to it (blocking B2, in flight); the rewritten `:1655` test is the discriminating proof** | **ONE LINE ADDED** to §(d.2); the generalisable lesson recorded as §11 insight 12. **No ledger / attestation change** (the rule string is unchanged) | §(d.2), §11 insight 12 |
| **IMPL-REVIEW / 1 — S4 population → UNION** | The code implements the **union** (`watchdog.py:1884-1897`: `_subject_rows` → in scope; `elif _total_pending == 0 and not _has_non_terminal_children` → the classic wedge) but the plan carried the pre-amendment narrow population | **FOLDED into the body, the ledger cell AND attestation row 8**: `S4_NOTICE_SUBJECT` = the qualified-unfulfillable set **∪** the classic-wedge arm (zero watchers ∧ no non-terminal children). The raw-PENDING prohibition stands (≈9× regression). **No false negative, no regression.** Pinned by T4 (both arms) | §(d.1), ledger row 8, attestation row 8, Phase 4 task 1 |
| **IMPL-REVIEW / 2 — DEDUPE_KEY → set form** | The ledger said `(parent_id, reason_code)`; the code is `(parent_id, reason_code_set)` via `tuple(sorted(_codes))` (`watchdog.py:1946-1953`) | **RE-ALIGNED**: ledger row 9 + §(d.2) + T5 restatement + Phase 4 task 4 all read **`(parent_id, reason_code_set)`** | ledger row 9, §(d.2), T5, Phase 4 task 4, attestation row 9 |
| **IMPL-REVIEW / 3 — R7 gate-leg SHAPE** | The body said "sync-twin inside the gate's session, same shape as R4"; the code is a **fail-open wrapper OUTSIDE** the gate's session (`_bus_count_fulfillable_pending_for_target_sync`, docstring `child_reports.py:399-421`; gate call `:2972`) | **RE-ALIGNED + DISCLOSED**: **leader ruling (code agrees)** — the wrapper shape is the **safer** one (a fail-open wrapper cannot wedge the completion path on a read error); R4 stays in-transaction because it runs inside a worker-thread session. **Predicate identical; transactional shape deliberately differs — do not unify.** | §(c.3) R7 row + the new R4-vs-R7 disclosure paragraph, §(i) row 7a |
| **IMPL-REVIEW / 4 — EPISODE end-rule** | The body's rule ("first tick on which that reason_code no longer holds") **WINS**; the code is being fixed to it (blocking B2, in flight) | **ONE LINE ADDED** — the rule is unchanged; the code fix is in flight and the rewritten **`:1655` test** is the discriminating proof | §(d.2) |
| **IMPL-REVIEW / 5 — THE D-2 INVERSION** | The recorded D-2 ruling was **INVERTED**: a crash between the enqueue commit and the `enqueued_at` stamp leaves `FIRED ∧ enqueued_at IS NULL ∧ marker set ∧ message queued` ⇒ resume Pass 1 (`instance_lifecycle.py:6105-6112`, **no `delivered_watch_id` consultation**) **re-enqueues** ⇒ **DUPLICATE**. The lost-wake window is **covered**. ⇒ the honest contract is **AT-LEAST-ONCE with a bounded duplicate window** | **CORRECTED EVERYWHERE**: §(a.5) stance rewritten (point 1 + point 2 + point 4), the compensating-controls block rewritten (duplicate is a **MESSAGE, not a park**; the second turn is a **no-op**), **ledger row 7 renamed `DELIVERY-ONCE` → `DELIVERY-AT-LEAST-ONCE`** with the new string, **risk 4b → "crash-window DUPLICATE delivery"**, **criterion #13**, **risk 2**, Phase 1 tasks 2c/2d, the Phase-1 coupling line, and **attestation row 7**. **Do not unify** — the marker-aware Pass-1 filter is a **NAMED FOLLOW-UP** (`instance_lifecycle.py` is an AVOID file) registered in the deferred list | §(a.5), ledger row 7, attestation row 7, risk 4b, risk 2, criterion #13, Phase 1 tasks 2c/2d, §(j)(vii) follow-up register |
| **ITER-003 / D-1 (adopter-directed cure)** | The `_root_completion_gate` bus leg at `child_reports.py:2637` reads the **RAW** count → `:2638-2639` returns blocked → `root_completed` (`:5483`) calls the gate at `:5521` → `:5562-5572` sets `WAITING_CHILDREN` and commits; after S3 qualifies R2/R3/R4 and stamps the root COMPLETED, the gate reads raw > 0 (backlog rows) and **DOWNGRADES the root back to WAITING_CHILDREN** — the park persists | **SWITCHED** — the gate leg reads the **qualified** count. *(The "sync twin + in-transaction, same shape as R4" phrasing in this row was the pre-implementation plan text; the **landed** shape is a fail-open wrapper outside the gate's session — see the IMPL-REVIEW / 3 row.)* **T3 extended to T3b** (drive the full gate chain: pass + status + no downgrade commit). **RAW_PARK ledger cell extended to seven PARK readers** (the gate leg is the seventh). §A5 / criterion #6 re-scoped → §(c.3) R7, §(f) T3b, §(i) row 7a, criterion #6 | **§(c.3) R7**, §(f) T3b, §(i) row 7a, criterion #6, ledger `RAW_PARK` cell, attestation row 13 |
| **FOLD-R3 / 7 — tip-refresh** | Implementation state re-pinned to the reviewer-blocker commit `00e6fd936` (B1-B4), which implements the ratified R1/D1 semantics | **no string changes** — the plan's strings and the release tip agree | attestation header (one sentence appended) |
| **FOLD-R4 / F9 — r3 fence restated** | The reviewer's independent per-hunk classification (31 watchdog hunks + 1 each in `child_reports`/`dependency_bus`) confirms no changed line can execute on the healthy path and alter the rendered notice, the clause order, or the row set; the ONLY render literal changed is the None-count text. BUT the `count == 0 → None` normalisation is **REACHABLE WITH ALL READS SUCCEEDING** under a child-completion RACE — it corrects a fabricated "0" (not a steady-state path) but **DOES change the rendered line** in that branch | **Fence RESTATED** as covering the **failure/unknown OR CONTRADICTORY-READ branch**; the healthy-path guarantee is restated as being about the **OBSERVABLE OUTCOME** (no false wedge notice / no episode mutation), **NOT a literal line-level claim**. The reviewer's per-hunk method is recorded as the **fence evidence of record** (stronger than the dispatch wording it replaces) | **§(g.1)** (new), §(g) heading context |
| **FOLD-R4 / pre-bump trio** | F4 (fresh episode + carrier-None ⇒ NO notice + a boom-called control, pinning the in-loop carrier-None guard at the reviewer's `:2035-2041` — deleting that guard emits a FALSE WEDGE NOTICE on an unknown carrier state); F5 (queued-report tri-state: episode stamped with `queued_report` + injected queue-read failure ⇒ episode unchanged — pins a tri-state leg with ZERO coverage, a NEW production path from R1); F1-F3 (call-counters asserting each injected failure actually FIRED) | **Recorded as LANDING-IN-FLIGHT** in **ONE commit BEFORE the version slot is cut** — free today, versus a new version slot + re-stage + re-seal after the bump. **Generalisable lesson recorded: "mutation-proven is NOT the same as self-guarding"** (an inert monkeypatch, or a `get_dependency_bus()` returning None ⇒ `subjects={}` with NO exception, leaves a leg green while the mutation is live) | **§(f.1)** (new), §11 lesson (cross-ref) |
| **FOLD-R4 / next-lane register** | F6 (`_s4_notice_rows` filters on the `wedge_notice` MARKER only ⇒ a GLOBAL count, not parent-scoped) · F7 (drop or comment the INERT seeds — `s4-blip-classic-dead` with `parent_id=None`, unreferenced) · F8 (latent NameError — hoist `subjects`/`subjects_read_failed` above the `try`) · F10 (PACKS.md `:3601` `Last Run` date refresh) · Fold-in #5 (withdrawn-constant note must read "imports", not "references") · F-4 stale orphan-sweep prose (`test_06f500af…:421-430/:493`; implemented at `dependency_bus.py:3061`; grace-gated by `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS = 30` at `:208`; correct `QUARANTINE.md:50`'s attribution) · F-2 PG refusal-guard node (coverage gap ≠ defect; top follow-up test) · `TestWedgeRearmAfterCooldown` (`:1597`; survives while its named constant is withdrawn; a 2-minute look) | **Added VERBATIM** (reviewer precision) to the deferred register; **F8 marked IN-FLIGHT** (folded into the same pre-bump commit) | **§(j)(vii) NEXT-LANE REGISTER** (new) |
| **FOLD-R4 / precision corrections** | F-1's precise framing (21 files use the old name **in tests**; of the 11 "untouched" only **3** carry real call syntax, the other 8 are prose/docstring references ⇒ "11 real call sites" OVERSTATED it, harmlessly; the 10 mock/double sites stamped site-by-site with the SAME value; the real bus keeps BOTH methods; an unstamped mock would fail **LOUDLY** because `MagicMock() > 0` raises) · F-6a (the worktree `PACKS.md` is a **BRANCH-ERA SNAPSHOT** ⇒ a later grep-empty there is NOT evidence of non-registration) | **Both recorded** so a future lane **TRUSTS the F-1 fix** and does not fall into the F-6a grep trap | **§(i) PRECISION CORRECTIONS CARRIED** (new) |
| **FOLD-R4 / disclosed limits** | The reviewer ran **NO tests itself**; 113/113, 51/51, 45/45 are **implementer/tester-attested**; the PACKS numbers were verified **ARITHMETICALLY against static `def test_` counts, not against a run**; the counter-mutation RED→GREEN transitions are the **implementer's**, corroborated by the reviewer's **static trace**, **not independently observed**; PG-dialect behaviour of the changed read paths was **not exercised** (the delta adds no PG-specific path). The reviewer's **conceded anchor error**: its T5 cite `:1766-1775` did not point at T5 (real T5(a)/(b)/(c) at `:2662`/`:2686`/`:2724`) — the implementer's correction stands; and **ALL SIX** of its earlier R1 cites were **stale** (file grew ~100 lines) | **Recorded in the EVIDENCE REGISTER** — not papered over; the trust boundary (static analysis + arithmetic, not an executed pass) is stated so the green counts are read as implementer/tester-attested | **evidence-register.md §10b** (new) |
| **FOLD-R4 / release section** | Staged-release packaging still owed per r7; version slot DERIVED AT BUMPTIME, never reserved (r7 + cn 84e24343); NO live promote; the live-wedge unblock runbook stays DISCHARGED-BY-EXECUTION | **Refreshed** with the sequencing note (the pre-bump trio lands BEFORE the slot is cut) | **§(j) §5 release section**, §(f.1) |
| **FOLD-R5 / 1 — FINAL TIP RE-BASE** | The standing caveat becomes due: live implementation-state pins + the attestation header were pinned at `a2e8c7fc7` / `00e6fd936` (point-in-time by design) | **RE-BASED to the FINAL tip `077a41a33cee9af1614a7967822be1679b582f68`** — 11 live-pin lines across all three files + the §(c.3) R7 reader-table row; the attestation header now carries the final-tip sentence with the **11-commit census** (`89b2e2e28` → `077a41a33` = S1–S4 + D-1/D-2 cures + fail-soft + doubles + B1–B4 blockers + R1/R2/R3 delta + pre-bump hardening) and the **final pack counts** (`wedge_fix_suites_unit_test.sh` = **115 (47+68)**; watchdog **51**; reconciler **47**). **History rows keep their point-in-time pins by design** — only live pins and the header moved | attestation header; ledger annotations; §(a.5), §(c.3), §(d.1); register §B3/§12a/§12b; arch fold note |
| **FOLD-R5 / 2 — THE RESIDUAL ADJUDICATION** | Reviewer ruling: acceptable-as-disclosed for staging, carried on the register — **and the registered fix is NOT the proposed one-liner** | **VERBATIM** into §(j)(vii): duplicate noise on a TRUE notice, **TWO extra notices per blip** (the narrowed fingerprint is STAMPED ⇒ the first healthy tick re-misses dedupe); materially milder than R1 (parent genuinely wedged, asserts nothing false, state machine protected, self-heals); **false notices and silencing BLOCK; bounded noise on true notices REGISTERS** ⇒ does not gate. **Registered fix = the FINGERPRINT-FALLBACK**, not the `continue` one-liner (which would create a suppression window under persistent failure) + the **three required pins** (test-1 counter-mutated; test-2 fresh-parent-during-blip; test-3 the F-2 initializer flip in the same commit) | §(j)(vii) |
| **FOLD-R5 / 3 — NEXT-LANE REGISTER (final form)** | The reviewer's closing register, verbatim | **NEW §11x** — the residual fix + its 3 pins · **F-2** the F8 hoist caveat (fail-closed→fail-open flip on that path; register, not block; +1-line fix) · **F-1** r3c's RV-1 proof VACUOUS (fix = seed a fingerprint-changing event, `len == 2`, make it ORDER-INDEPENDENT — `list_waiting_children_parents()` has NO ORDER BY) · **F-3** r3c never asserts its RV-1 premise · **F-5** stale `wlts-watchdog` stamp · **F-6 THE MERGE HAZARD** (origin/latest **+156 commits**; `PACKS.md` touched upstream; the lane's `:2321`/`:3601` rows **SUPERSEDE** and must be reconciled deliberately) · **F-4** the anchor restatement (`:2041-2047`, `:986-990`, `:841-914`; the **+6 shift from F8** was unaccounted) | **§11x** (new) |
| **FOLD-R5 / 4 — DISCLOSED UNVERIFIED LIST** | The reviewer's attestation boundary | **NEW register §10c** — the restore-proof sha256s, the **115/115** pack run, the **four mutation REDs** (a static control-flow trace; the tester's earlier legs corroborate the family, not this commit's REDs), **F-2's raiser reachability** and the residual's live tick behaviour are **ATTESTED, not independently observed** | **evidence-register.md §10c** (new) |
| **FOLD-R5 / 5 — RELEASE SECTION (final wording)** | §5 | **FINAL**: bump commit → annotated tag → stage wt detached at the tag → FE rebuild → `stage.sh` (r7); version slot **DERIVED AT BUMPTIME from the repo's tags/releases state, never reserved** (r7 + cn `84e24343`); **NO live promote — the Emperor's ceremony owns it**; the live-wedge unblock runbook stays **DISCHARGED-BY-EXECUTION** (repair 21:18-21:20Z; parent completed 21:21:24Z; `--verify` 1-4 PASS) | §(j) §5 |
| **ITER-003 / D-2 (adopter-directed cure) — SUPERSEDED BY THE IMPLEMENTATION-REVIEW INVERSION** | DELIVERY-ONCE was implied to be "same transaction as the enqueue" — unachievable (`enqueue_message` owns its own session, `instance_lifecycle.py:6015-6018`) | **RE-SCOPED TO AT-MOST-ONCE** with the crash-loss residual DISCLOSED and compensating controls named (crash-before-marker loses the wake, never duplicates; S3 still releases the park; S4's corrected notice is the visibility backstop). **NO re-enqueue path added; the AVOID on the Pass-1 file is NOT lifted** | §(a.5) compensating-controls block, §(a.5) point 4, **risk 4b** |
| **ITER-003 / defect 1** | Attestation FULFILLABLE string ≠ ledger (path notation) + `TERMINAL_SET` still live at PO:430 + sweep claim at PO:969 + PO:987 cited "PO:237" for the 3600.5-3600.7s no-op | **FULFILLABLE string made byte-identical** to the ledger cell (verified); `TERMINAL_SET` → `INSTANCE_TERMINAL_SET` at PO:430; **sweep claim corrected** to state the true result (7 total / 7 deletion-records / 0 live); citation corrected to **PO:30 / PO:503** | attestation row 1, PO:430, attestation header, PO:987 |
| **ITER-003 / defect 2** | Criterion #4 at PO:906 stated the OPPOSITE of T1d/ledger ("only the live episode's watcher FIRED") | **rewritten** to state T1d's actual semantics (both nudgers fire; stale row fires once) | criterion #4, T1d |
| **ITER-003 / defect 3** | PO:788 still prescribed the DELETED discriminator (`source_task_id`-terminal) in risk 4's mitigation | **risk 4 mitigation rewritten** — the discriminator is WITHDRAWN (iteration 003 leader ruling) and must not be re-introduced | risk 4 row, §(a.6) bullet 2 |
| **ITER-003 / defect 4** | PO:382 contradicted T1d | **rewritten** to match T1d and the ledger | PO:382 |
| **ITER-003 / defect 6** | Arch R-3 vs the adopted branch (A) — said "no wake is delivered" on the ERROR lane | **Arch R-3 aligned**: S1's ERROR hook closes the fire; the residual is the backlog rows (DOOR-1) | `architecture-recommendation.md` §6 R-3 |
| **ITER-003 / defect 7** | Arch R-2 vs §(c.3) + the internal §4.4-vs-§6 contradiction — BOTH carried the "5 callers" helper error | **both aligned** to the SIX-READER + D-1 SEVEN-SITE design (`:3578` SWITCHED, `:3379-3437` child-liveness, `:3664-3698` now R4) | `architecture-recommendation.md` §6 R-2 + §4.4 |
| **ITER-003 / defect 8** | "THREE unswitched COMPLETION doors" census mis-derived — D-2 `:3702` is the deferred return of the switched R4 block; D-3 `:3757` is a child-liveness cross-check; D-4 `:4434` is a CAS idempotency_skip reading no count | **census rewritten** — D-2/D-3/D-4 are completion defers, none can create or hold a park; `:3379-3437` withdrawn from the question entirely | §(c.3) doors table, risk register, RQ-D |
| **ITER-003 / fold** | risk 4 SPLIT into "spurious-wake (Low, accepted)" + "crash-window delivery-loss (High, disclosed)"; Phase-3 count 14 everywhere; residual warnings the approver carried | **risk 4 → two rows (4 + 4b)**; Phase 3 = 14 (was already correct); PO:476 census rewritten; PO:987 citation corrected to PO:30/PO:503; PO:969 sweep result corrected | §9, §(c.3), §10 #6, §12 |

**Consistency attestation:** the plan body and every ledger/trace table use the **canonical predicate strings** of §PREDICATE LEDGER **character-for-character** (house rule `plan-body-ledger-agreement` — **body wins**). Attestation recorded at the end of this document (§ATTESTATION).

---

## 0. EXECUTIVE DETERMINATION

1. The commissioning premise was **false**: there was never a `PROCESS_REPORT` to deliver (0 completion_report rows, 0 process_report tasks, 0 report_injections, all-time, for the Ari root `17efd2ef`). The "stuck PROCESS_REPORT" was read off the wedge notice's own boilerplate.
2. The live form is **sub-case C with the watcher-leak shape**: the parent waits on PENDING `dependency_watchers` rows that can never fire — `8a1f60d7` (nudge-armed, watched child sonic-leader `542b49a6`, source task 39320, terminal 18:08:20) and `999efa71` (Ari's self-nudge, structurally unfulfillable).
3. **Measured non-fire reason:** the bus has three fire primitives; the **COMPLETED path exposes only two**, and the `tool_invocation_completed` path exposes **zero**. The one primitive that would have fired `8a1f60d7` — target-agnostic child-keyed `fire_for_terminated_target` — is reachable **only from TERMINATE**. The nudge lane is the only arm family that can produce this shape (no tree guard, no self guard).
4. **Both candidate mechanisms (OQ1) converge on one fix** — resolved by census, not inference.
5. **Shape set is exactly four and FROZEN**: S1 fire-at-terminal (PRIMARY, now also covering the ERROR lane) · S2 nudge arm-time guard · S3 park-predicate reconciliation · S4 notice surface.
6. **The ERROR lane is folded into S1 (branch A)** — C-3 is **NEGATIVE**, so the hook moves to `error_reporting._send_error_report` (uncontended), not the shape.
7. **No new table, no migration, no new column** (r10).
8. **The contention is a stale fork** — not a live rival. Implement against base; do not re-pin to it.
9. **A NEW DEFECT was measured in Phase B** (not in the commission): the ALWAYS-ON periodic orphan-watcher sweep is **permanently inert** (constructed with a NULL bus). Anomaly `fcf89d88`. The plan must not rely on it.
10. **The closure claim is SCOPED**: tonight's instance closes completely; the COMPLETED lane closes going forward; **one named, monitorable door survives — the ERROR-lane child** — until the staged release lands, plus the backlog-row residual fed by that door.

---

# PREDICATE LEDGER (canonical strings — body and tables must match CHARACTER-FOR-CHARACTER)

| Symbol | Canonical string | Owning shape | Used by |
|---|---|---|---|
| `INSTANCE_TERMINAL_SET` | `{completed, terminated, error, failed}` — **INSTANCE-status values only** (`InstanceStatus`: `idle/running/waiting/paused/completed/error/terminated/queued/waiting_children/failed`) | S3 | `FULFILLABLE`. **TYPE DISCIPLINE (leader ruling, iteration 003):** the earlier form was applied to a **TASK** status, whose vocabulary is `pending/running/paused/completed/failed/cancelled` — **no `terminated`, no `error`** ⇒ the set is only ever applied to **instance** statuses now |
| `FULFILLABLE(w)` | `NOT (w.follow_up_payload.metadata.child_id == w.target_instance_id) AND (w.follow_up_payload.metadata.child_id IS NULL OR instance-status-of(w.follow_up_payload.metadata.child_id) NOT IN INSTANCE_TERMINAL_SET)` — **keys on the CHILD'S INSTANCE STATUS, never on any task status** | **S3** | `count_fulfillable_pending_for_target`; `S4_NOTICE_SUBJECT`; T3 |
| `PARK(parent)` | `count_fulfillable_pending_for_target(parent) > 0` | **S3** | **park sites R1-R4** (`mps:1329`, `cr:3511`, `cr:3578`, `cr:3664-3698`); **healer legs R5-R6** (`msh:562`, `msh:1240`, used in the *negated* form) |
| `RAW_PARK(parent)` | `count_pending_for_target(parent) > 0` | **raw boundary** | read by **NINE named consumers** (observer ×5 `:470/:1173/:2786/:2942/:3838`, `job_recovery_service:1319`, `child_reports:378` + its 5 callers, `error_reporting:263`, `manager:13852`) — **none of them decides a PARK after S3**: the three raw PARK doors (`:3511`, `:3578`, `:3664-3698`) are switched; D-2/D-3/D-4 defer completions, not parks |
| `FIRE_SET(instance)` | `{w in PENDING : w.follow_up_payload.metadata.child_id == instance.instance_id}` — **PLAIN matching form, NO staleness exclusion** (leader ruling, iteration 003). Every PENDING row whose `child_id` matches is a **genuine waiter**; firing them all is correct semantics, not a false wake. **Two nudgers on one child = two waiters = both wake.** A stale-but-PENDING row delivers **at most ONE spurious wake** (it fires once and leaves PENDING) — a bounded, self-limiting residual, **DISCLOSED** (risk 21), not engineered around. | **S1** | the new bus method's matcher |
| `DELIVER(w)` | `NOT (w.target_instance_id == structural_parent(w.follow_up_payload.metadata.child_id))` | **S1** | the per-row delivery filter at the COMPLETED hook |
| `ENRICH(w)` | `w.follow_up_payload.metadata.source_task_id IS NULL ⇒ set it from the fired row's source_task_id before the stamp` | **S1** | delivery sub-task (the nudge payload lacks it) |
| `DELIVERY-AT-LEAST-ONCE(w)` | `enqueue_message(w) occurs AT LEAST once per watch_id; a crash between the enqueue commit and the enqueued_at stamp MAY re-enqueue on resume Pass 1 (bounded duplicate window — the duplicate is one redundant MESSAGE, never a second park); the distinct marker delivered_watch_id is committed BEFORE the enqueue in its own transaction` | **S1** | the durability stance (**D-2 INVERTED — at-least-once, not at-most-once**). **SCOPING NOTE:** the FIRE primitive's exactly-once (guarded `WHERE state='PENDING'` UPDATE) is a **row-state property**; the delivery layer's at-least-once is a **cross-transaction property**. Do not conflate them in a future cleanup |
| `S4_NOTICE_SUBJECT(parent)` | `{w in PENDING of parent : NOT FULFILLABLE(w)} != ∅  OR  ({w in PENDING of parent} == ∅ AND the parent has no non-terminal children)` — **the UNION: qualified-unfulfillable set ∪ the classic-wedge arm** (implementation state @ `077a41a33`: `watchdog.py:1884-1897`) | **S4** | reason code `pending_watchers` + the classic-wedge arm; T4 (both arms) |
| `DEDUPE_KEY` | `(parent_id, reason_code_set)` — the sorted tuple of reason codes (implementation state @ `077a41a33`: `watchdog.py:1946-1953`, `_fingerprint = tuple(sorted(_codes))`) | **S4** | T5 |
| `EPISODE` | `from the first notice-eligible tick to the first tick on which that reason_code no longer holds` | **S4** | T5; success criterion #5 |
| `SWITCH_OFF` | `wedge_notice_emission_enabled == false ⇒ the notice-enqueue branch is skipped; detection, classification, S1, S2, S3, the healer, the park predicate and the report lane ALL still run` | **S4** | T6 |

---

## ADJUDICATION — MEASUREMENTS vs THE LIVE ROWS

**There is NO CONTRADICTION between our code measurements and the live census.** The measurements **predicted** the configuration; the rows **confirmed** it.

| Phase-A measurement (base `eca98397d`) | Live row | Agreement |
|---|---|---|
| Park predicate = bus-count-only (`message_processing_pipeline.py:1279-1390`, decisive read `:1329`) | `count_pending_for_target(17efd2ef) = 2` (then 4) holds the park at every turn-end | ✔ the predicate reads exactly the population the census counted |
| Liveness = status-only (`instance/repository.py:3838-3916`), no ghost carve-out | "zero non-terminal children" trivially true — `parent_id LIKE '17efd2ef%'` is EMPTY | ✔ the two predicates describe **different populations** — the contradiction |
| No durable release: all 8 WC-exit paths need the watcher to fire or are transient wakes that re-park | Re-park observed 21:24:46 and 21:30:57 | ✔ transient-wake-then-re-park observed |
| Notice prose asserts queue state the detector never verifies (`watchdog:429-434` vs `:1466-1618`) | report-assertion FALSE in **4 of 4** notices ever minted; zero-children TRUE in 4/4 | ✔ prose false in **100%** of occurrences |
| The bus documents this exact class in its own docstrings (`dependency_bus.py:1597-1604`, `:894-906`) | — | ✔ known-shape, never exercised on the COMPLETED/ERROR/tool-invocation lanes |

**Two under-specifications owned honestly (Phase A's own gaps):** (i) **the T6 repro enumeration assumed spawned children** — the live form is a **root with NUDGE-armed watchers and ZERO instance children**; the repro is re-shaped around the nudge lane (T1) and the self-nudge (T2). (ii) **the `b0633a1e` zero-execution framing came from the commission** — the rows answer it: `b0633a1e` is `task.id=36706`, a mid-tree carrier for `5d4d2b2a`, completed 18:05:36 with `{"success":true,"skipped":true}` and delivered; **not tonight's child**. The plan does not use it as evidence.

**No-contradiction statement (the leader asked explicitly):** our bus-fire census (F1 source-keyed/child-lane-only; F2 pair-keyed/structural-parent-scoped; F3 child-keyed-across-every-target/terminate-only) matches the three-primitives analysis **exactly**, and **resolves** the explorer's OQ1.

---

## A1 — ANCHOR REGISTER

**Pointer:** `.../wake-path-carrier-fix/evidence-register.md` — full register with per-anchor status (`FOUND` / `FOUND/EXPLORER` / `MOVED` / `NOT RE-VERIFIED`), the description corrections (D1 `job_recovery_service` is a **lettered pattern family**, not a registry; D2 the watchdog performs **no bus-row read**), the resolved orphan-sweep section, and the live-row census.

**Anchor hygiene:** anchors are leads — content-match at use time. Re-pin with `git grep -n '<pat>' eca98397d -- <path>` / `git show eca98397d:<path>`.

### A1.1 RE-PIN BATCH (item 17) — corrections applied in this pass

| Anchor | Was | **Corrected** | Evidence |
|---|---|---|---|
| O-3 (`set_dependency_bus` sites) | `dependency_bus.py:5710` | **`daemon/api.py:5710 / :5720 / :5739`** | `dependency_bus.py` is **2812 lines**; its own `set_dependency_bus` def is at `:2801` — `:5710` is **past EOF**. The call sites are in `api.py` (measured: `:5710` `set_dependency_bus(bus)`, `:5720` / `:5739` `set_dependency_bus(None)`) |
| F2 matcher | `repository.py:1048-1052` | **`repository.py:239-309`** | `fetch_pending_for_target_and_child` defs at `:241`; the child-id filter at `:305-309` (`if meta.get("child_id") == child_instance_id`) — `:1048` is **past EOF** of a ~900-line file |
| mint txn | `child_reports.py:5264` | **`child_reports.py:4772-4800`** | `:4771` `report_injection_row = ReportInjection(` — the transactional-outbox MINT; `:5264` is `session.commit()` |
| `_recover_fired_unsent` | `dependency_bus.py:1699` | **`dependency_bus.py:2443`** | def at `:2443` ("Load FIRED-but-not-enqueued rows for the caller to re-enqueue") |
| P-park-4 | `:1291-1301` | **`:1297-1301`** | the deprecated self-queue park (`parent.status = InstanceStatus.WAITING_CHILDREN.value` at `:1298`) |
| healthy-lane stamp | — | **the stamp is `instance_lifecycle.py:494-499`; the re-purge docstring `:608-620`** | measured in the delivery-slice read |
| A-arm-2 nudger guard | `:665-671` | **`:655-661`** | `if not nudger_instance_id:` … `return False` |
| O-1 (boot sweep call) | `:1752` | **`:1753`** | the `swept = await self._sweep_orphan_watchers()` call inside `start()` |
| R-root6 (delivery precedent) | `:315-430` | **`instance_lifecycle.py:315-577`**, delivery slice **`:469-540`**, `_repurge_fired_follow_ups` def **`:580`** | measured |
| PACKS.md citations | by line | **BY PACK NAME** (copies diverge) | `wedge_fix_suites_unit_test`, `waiting_children_watchdog_unit_test`, `claim_guard_locks_unit_test`, `orphan_active_job_recovery_suites_unit_test`, `security_boundary_hygiene_suites_unit_test`, view `wlts-wedge` |

### A1.2 CORRECTION C-06f (item 18)

`06f500af` is the **June incident's INSTANCE id — NOT a commit**. `git show 06f500af` → *unknown revision*. The docstring was introduced by commit **`4926a2eb6`** and lives at **`daemon/services/dependency_bus.py:1597-1604`** inside `cancel_for_source`'s docstring ("Production incident 2026-06-26 (**instance** 06f500af stuck in `waiting_children` for hours)"). **`dependency_bus.py:2514-2515` mislabels it as a commit** ("the production incident pattern recorded in commit 06f500af") — a **pre-existing documentation defect**. **Note it; do NOT fix it in this lane.**

### A1.3 CORRECTION C-20 (item 20)

**`watch_reconcile_sweep` is REMOVED from the bus-caller census.** It belongs to the **JOB-watcher** system (job/work notify rows) and has **zero** `dependency_bus` hits. Citing it as a bus caller was a false premise. It is also removed from the plan's file list.

---

## A2 — DETERMINATION (per sub-case, with the census gap named)

| Sub-case | Class | Form | Tonight | Determination |
|---|---|---|---|---|
| **A** | report-level | a `completion_report` row EXISTS but its carrier/delivery is missing | **ZERO** rows for the Ari root | **REAL but DIFFERENT** — §(j)(i). **NOT fixed by this lane.** |
| **B** | wake/claim-level | report + carrier exist, but the parent's turn never re-evaluates | not tonight's shape | **The healthy-path fence lives here** — §(g)/T7 |
| **C** | **watcher-leak (LIVE)** | the parent's wait is held by a PENDING `dependency_watchers` row whose watched work is terminal-or-unfulfillable, and no lane fires it | **2 structurally dead** (`8a1f60d7`, `999efa71`) + 2 legit-while-leader-non-terminal (`5602e91d`, `c83e12a2`) | **THE PRIMARY DEFECT** — S1 + S2 + S3 |

**THE CENSUS GAP (item 21, newly named):** the `tool_invocation_completed` branch (`child_reports.py:6161-6195`) fires **NO bus terminal at all** — measured (`grep -n "emit_terminal\|bus"` over `:6161-6200` yields only the *comment* header of the NEXT branch at `:6195`, no emit). ⇒ **a parent waiting on a tool-invocation child keeps a PENDING watcher forever** — a **third** unreachable lane, structurally identical to the COMPLETED one. **S1 covers it** because the hook sits in `_dispatch_post_commit_side_effects`, which is reached unconditionally for `:6161`. Pinned by **T1c**.

---

## A3 — ERROR-LANE DETERMINATION (C-3 resolved; **branch (A) ADOPTED**)

### A3.1 C-3 — the measured question and its answer

**Question:** does `child_reports._dispatch_post_commit_side_effects` (`:5285`; single call site `:3130`) run for **ERROR** outcomes?

**ANSWER: NO — NEGATIVE.** Three independent measurements:

1. **The DB-result producer emits no error outcome.** `grep -n 'outcome="error"\|outcome="failed"' daemon/services/child_reports.py` → **NO MATCH**. The producer's complete outcome vocabulary (measured, all `outcome="…"` literals) is: `instance_not_found` (`:3255`), `deferred_waiting_children` (`:3437/:3534/:3558/:3592/:3702/:3757`), `idempotency_skip` (`:3448/:3897/:4057/:4203/:4263/:4434/:4928`), `deferred_pause` (`:3493`), `root_waiting_children` (`:3875`), `root_completed` (`:4066`), `tool_invocation_completed` (`:4271`), `child_still_running_defer` (`:4376`), `dead_parent_skip` (`:4965`), `regular_child_completed` (`:5267`). **No error member exists.**
2. **The dispatcher's branch set matches exactly** — `outcome == "…"` branches at `:5348`, `:5395`, `:5483`, `:6028`, `:6133`, `:6150`, `:6161`, `:6195`. **No `error` branch.** So even if an error outcome were produced, the dispatcher would fall through with no bus hook.
3. **The ERROR lane has its own, different site.** `daemon/services/error_reporting.py::_send_error_report` (`:439`) owns the error lane and **already emits**:
   - `:612` — `await _child_reports_svc._emit_terminal_via_bus(task_id=…, status="error", error=error, summary=f"child errored: {error_type}")` (F1);
   - `:654` — `await _child_reports_svc._emit_terminal_for_child_instance_via_bus(parent_instance_id=parent_id, child_instance_id=instance_id, status="error", …)` (F2, corrective);
   - `:708` / `:765` — the defensive fallback when `_child_reports_service` is not wired (direct `_bus.emit_terminal` / `_bus.emit_terminal_for_child_instance`).
   - Its own comment at `:590-601` states the delegation rationale: *"Delegate the bus emission + FollowUp enqueue + re-trigger loop to `child_reports._emit_terminal_via_bus` so the finalize re-trigger fires uniformly for BOTH completion and error paths."*

⇒ **`_dispatch_post_commit_side_effects` does NOT run for ERROR outcomes; the ERROR lane emits F1+F2 from `error_reporting.py` and emits NO F3-class (target-agnostic child-keyed) fire.** The ERROR lane therefore has the **same hole as the COMPLETED lane**: a nudge-armed (out-of-tree) watcher on an erroring child can never fire.

### A3.2 Why folding it is not an "extra" (the leader's rationale, adopted)

- It is the **SAME audited class** — *"a parent waits on something that can never complete, and no lane releases it"* — so the minimality doctrine (which cuts *extras*, not *closure*) does not bar it.
- It is **LOAD-BEARING**, and strictly worse than tonight's wedge: a forever-PENDING watcher eternally holds the **lane-7 mission HOLD** — `report_delivery_recovery.py:2857` `if watchers: out.skipped_watcher_pending += 1; return` **PLUS a second same-shape guard at `:2779`** (both measured) — which blocks **ALL** commission abandonment **including the TTL backstop**, so the mission gate (`child_reports.py:2691-2695` — `mission_commissions = self._count_outstanding_mission_commissions(instance_id); if mission_commissions > 0: return False, …`) **refuses root completion forever**.

### A3.3 The selected branch and the site

**BRANCH (A) — ADOPTED**, on the C-3-NEGATIVE path: *place the ERROR hook at the correct observable site within this lane (same primitive, different site — still minimal)*.

| Lane | Hook site | Contention | Note |
|---|---|---|---|
| **COMPLETED** (`root_completed` `:5483`, `regular_child_completed` `:6195`) | `child_reports._dispatch_post_commit_side_effects` (`:5285`; single call site `:3130`) | **CONTENDED** (`fix/empty-job-completed-event`, 4 commits) | **priced** in §(i) row 8 |
| **TOOL-INVOCATION** (`tool_invocation_completed` `:6161`) | **same** — reached unconditionally by the single call site | same file, same cost | covers the newly-named census gap (§A2) |
| **ERROR** (all error outcomes) | **`daemon/services/error_reporting.py::_send_error_report` (`:439`)**, beside the existing `:612` / `:654` emits | **UNCONTENDED — measured ZERO unmerged commits across all 22 code-bearing branches** | this is where the C-3 answer lands the fix |

**The observer is DEAD as a candidate hook and the framing is deleted** (item 1): `_finalize_instance` has **zero production callers** — its own code says so at `:3645-3650` — and the method contains **no bus seam**. The blind conclusion survives only via the `:2346` `not instance_was_terminal` gate, which is not a hook. **"Entirely uncontended" is DELETED from the plan** — the COMPLETED hook is in the most contended file in the lane, and that cost is now stated in §(i).

**Branch (B) is NOT selected** — C-3 is negative, so the "same primitive, different site" option exists and is cheaper than deferring the ERROR door.

---

## A4 — SCOPE-OUT REGISTER (the lane-7 scope-out)

| # | Dropped item | Why dropped |
|---|---|---|
| 1 | **Mint a carrier for a queued `PROCESS_REPORT`** | There is no queued report to carry. Fleet: `completion_report` messages ready=1 / completed=108; the single ready row is a **fresh healthy triple** (+11 ms carrier, +16 ms injection, message completed ~2 min later); message-only-no-injection 0; injection DEFERRED 0; terminal-injection-with-ready-message 0. The class **does not exist fleet-wide tonight**. |
| 2 | **Drain-predicate widening** | Same measurement. Widening a predicate over an empty population is unfalsifiable work. |
| 3 | **Exactly-once safety analysis for a widening not being done** | Dropped with (2). **However** the exactly-once question for **what S1 DOES do** is now first-class — see §(a.5). |
| 4 | **The 2026-10-18 zero-execution mint gap** (cn `6a435fe7`) | **REAL but a DIFFERENT class.** NOT fixed here — §(j)(i). |
| 5 | **Shape (f) — `mission_commissions` substrate** | Separate defect; only its churn cause documented. |
| 6 | **Shape (a) — spawn-arming Txn1/Txn2 gap** (cn `c0adede9`) | The live rows show the **nudge** lane armed both dead watchers; the spawn lane is tree-guarded. Separate, still-open commission. |
| 7 | **Shape (b) — "the parent LOSES its watcher"** | **INVERTED by the rows**: the watcher was never lost — it never FIRED. |
| 8 | **The periodic orphan-watcher sweep as a backstop** | Measured inert (§register O-3, anomaly `fcf89d88`). A backstop that provably cannot fire is not a backstop. |
| 9 | Any carrier-mint / drain / queue-shape work for the report-level class | Deleted by the dispatcher's scope adjudication and independently unsupported by the census. |
| 10 | **Fixing the `:2514-2515` "commit 06f500af" doc defect** | Pre-existing documentation defect; note only (§A1.2). |
| 11 | **Re-pinning anything to the contended tips** | They are **stale forks** (§(i) CONTESTED-TIP POSTURE) — zero plan anchors exist there. |

---

## A5 — CLOSURE CLAIM (SCOPED — item 14)

**The plan does NOT assert "the class is closed".** The precise claim:

| Statement | Truth at the staged release |
|---|---|
| Tonight's instance (`17efd2ef`) closes **completely — ONCE THE CEREMONY EXECUTES THE REPAIR** (approver note 2, iteration 001): the repair is Emperor-gated, so "completely" is ceremony-conditional, not unconditional | **YES, conditional on the ceremony** — 8-row repair (§(h)) + one dispatch ⇒ the parent leaves WAITING_CHILDREN. **And post-release, the root PARK is closed by S3 (approver B5 cure): all three raw-count PARK doors in the root flow (`:3511`, `:3578`, `:3664-3698`) read the qualified count** |
| The **COMPLETED-lane** class closes **going forward** | **YES** — S1 fires at the terminal transition (per-outcome hook: regular `:6464`, tool `:6192`, root `:6014`); S2 stops the self-watcher at birth; S3 stops an unfulfillable wait from holding a park |
| The **TOOL-INVOCATION-lane** class closes going forward | **YES** — same hook, per-outcome before `:6192` |
| The **ERROR-lane** class closes going forward | **YES** — S1's ERROR hook at `error_reporting.py:439` (branch A) |
| **The class is closed** | **NO — ONE door survives until the release lands, and it is named and monitorable** |

**The one surviving door, named and monitorable:**

> **DOOR-1 — the ERROR-lane child on the pre-release binary.** Every `_send_error_report` on today's live binary emits F1+F2 only; a nudge-armed watcher on an erroring child stays PENDING forever, and that PENDING row holds the lane-7 **mission HOLD** (both guards `:2779`, `:2857`) which blocks commission abandonment **including the TTL backstop** ⇒ the mission gate (`child_reports.py:2691-2695`) refuses root completion forever. **Strictly worse than tonight's wedge.**
> **Owner:** the **staged release** (closes it) — until then, the **post-promote escalation watch** named in §(h) watches job-finalisation delays and PENDING-watcher ages.
> **Revisit trigger:** any PENDING watcher older than 2× the sweep interval with a terminal `source_task_id`.

**Residuals that are NOT static — they are FED by DOOR-1:**

| Residual | Owner | Note |
|---|---|---|
| **Backlog rows** (watchers already PENDING on error-lane children at release time) | **next commission** (or the ceremony repair bundle's remainder path) | fed by DOOR-1, so the population keeps growing until the release lands — see §(j)(v) for its one-query deciding evidence |
| **Crash-window** (the fire-once-vs-enqueue-once gap) | **implementer check** (§(a.5)) | an engineering constraint, not a defect door |
| **Matcher key** (child_id alone across every target) | **implementer check** (§(a.6)) | an engineering constraint, not a defect door |

---

## A6 — REGRESSION FENCE (the healthy path must not change)

1. **(i) Spawn-lane child completion still fires F1/F2 exactly as today** — `child_reports.py:6222` (F1) and `:6256` (F2) unchanged; S1's supplementary fire is a **rowcount-0 no-op** there. **AND the structural parent receives NO bus FollowUp on the healthy path** (`DELIVER(w)` excludes `w.target_instance_id == structural_parent(w.follow_up_payload.metadata.child_id)`).
2. **(ii) The park predicate still parks a parent with genuinely-pending fulfillable watchers** — `PARK(parent)` still transitions RUNNING → WAITING_CHILDREN at `message_processing_pipeline.py:1335-1340`.
3. **(iii) Terminate still runs F3 unchanged** — `instance_lifecycle.py:385` `fire_for_terminated_target` + the DOWN-side `cancel_for_target` drain; fire-first ordering preserved.

**Named observables (row states, never log lines, never string greps):** `dependency_watchers.state` / `.fired_at` / `.enqueued_at`; `instances.status`; `message_queue` row presence + `source`; `task.status`.

---

## SILENT VARIANT NOTE

The watchdog has two variants: **NOTICE** (a wake is minted) and **SILENT** (no notice). The SILENT decision is made over a **different population** than the park predicate:

- **SILENT when** a live PROCESS_REPORT carrier exists (`watchdog:707-772`) **or** the parent has non-terminal children (`watchdog:1602-1608` → `parents_with_non_terminal_children`, status-only).
- **PARKED when** `PARK(parent)` is true — i.e. `count_fulfillable_pending_for_target(parent) > 0` after S3.

On the Ari root the two populations disagree: **0 non-terminal children** (⇒ SILENT-leg satisfied, "wedge" asserted) but **≥ 2 PENDING watchers** (⇒ park held). ⇒ **The instance can be simultaneously "wedged by the guard" and "legitimately parked by the bus"** — and the SILENT variant is *never* reached because the guard's subject leg (children) is not the population that actually holds the wait (watchers).

**Consequence for S4:** the SILENT/NOTICE decision must be re-based on the **same population the park uses** — `S4_NOTICE_SUBJECT(parent)` — or the guard will keep classifying a correctly-parked parent as a wedge. This is the predicate reconciliation's second half.

---

## (a) PRIMARY FIX — S1: GIVE THE TERMINAL LANES THE TARGET-AGNOSTIC CHILD-KEYED FIRE THEY LACK

### (a.1) The MEASURED non-fire reason

| | Primitive | Matches | Scope | Callers |
|---|---|---|---|---|
| **F1** | `emit_terminal(task_id)` `dependency_bus.py:667-883` | `source_task_id == task_id` (`repository.py:172`) | target-agnostic | **child lane only** — `child_reports.py:467` helper, invoked at `:5431` and `:6222`; `error_reporting.py:612/:708`; plus the F2 loop's internal already-fired skip at **`daemon/services/dependency_bus.py:1087`** (a skip site, not a caller seam — N9). **No generic task-completed seam.** |
| **F2** | `emit_terminal_for_child_instance(parent, child)` `:885-1090+` | **pair** `(target==parent, follow_up_payload.metadata.child_id==child)` (`repository.py:239-309`, filter `:305-309`) | parent-scoped | `child_reports.py:5436/:6070/:6256/:6567`; `error_reporting.py:654/:765`. **The root short-circuit lives in the CALLER helper** — `child_reports.py:676-683` (*"parent_instance_id is None — no watcher to fire (root instance)"*); **the bus method itself has NO parent-None guard** (`dependency_bus.py:676-683` is F1's docstring region). |
| **F3** | `fire_for_terminated_target(target)` `:1373-1480+` | `follow_up_payload.metadata.child_id == target` **across every target** (`repository.py:343-395`, filter `:393`) | target-agnostic | **ONE** caller: `instance_lifecycle.py:385` ← `terminate_instance` `:3189-3191`. **TERMINATE ONLY.** |

**Why `8a1f60d7` never fired — measured, both branches:**
- **F1** is keyed on the child's **FINAL** task (`child_reports.py:5421-5431`). sonic-leader took 4 team reports (multi-turn) ⇒ its watcher's `source_task_id` 39320 ≠ the final task ⇒ **no match**, root or child-of-third.
- **F2** is invoked with the child's **STRUCTURAL** parent; ari is not sonic-leader's structural parent ⇒ the pair `(target=ari, child=sonic-leader)` is **never emitted**. F2 also short-circuits for a root child (`:676-683`).
- **F3** — the only primitive whose matcher reaches ari's row — is **reachable only from TERMINATE**; sonic-leader went **COMPLETED** at 19:04:27.

⇒ **THE ASYMMETRY BETWEEN THE TERMINAL LANES IS THE DEFECT SURFACE.** Measured lane coverage of the three primitives:

| Terminal lane | F1 | F2 | **F3-class (target-agnostic child-keyed)** |
|---|---|---|---|
| TERMINATE | — | — | **YES** (`:385`) |
| COMPLETED (`root_completed`, `regular_child_completed`) | yes | yes | **NO** |
| **ERROR** (`_send_error_report`) | yes (`:612`) | yes (`:654`) | **NO** |
| **TOOL-INVOCATION** (`:6161`) | **NO** | **NO** | **NO** ← the census gap (§A2) |

### (a.2) S1 — the fix (corrected placement and ordering)

**Step 1 — the bus capability (UNCONTENDED — `daemon/services/dependency_bus.py`):** a sibling of F3, `fire_for_terminal_child(child_instance_id, outcome)`:
- reuses the **existing** `fetch_pending_for_child_instance` (`repository.py:343-395`) — the child id already rides in `follow_up_payload` JSONB (`repository.py:352-365`, filter `:393`); **no new column, no new query shape**;

**SCOPING NOTE (FOLD-R3, item 2):** the **"exactly-once"** claims elsewhere in this plan (§(a.2) step 1's guarded UPDATE, §(a.3) step 4, Phase 1 task 4, T7) are scoped to the **FIRE PRIMITIVE** — `transition_state`'s guarded `WHERE state='PENDING'` UPDATE, which is genuinely exactly-once per row. They are **NOT** claims about the **DELIVERY LAYER**, which is **at-least-once** (§(a.5)). A future cleanup must **not** conflate the two: the fire primitive's exactly-once is a row-state property; the delivery layer's at-least-once is a cross-transaction property, and the gap between them (the enqueue-commit → stamp window) is the documented duplicate-window residual.
- matcher = **`FIRE_SET(instance)`** (§PREDICATE LEDGER);
- transitions each matched row **PENDING → FIRED** via the same guarded UPDATE (`transition_state`, `WHERE state='PENDING'`) ⇒ **fire-once**;
- sets `fired_at`;
- **returns the fired FollowUps and enqueues NOTHING** — the bus is a state machine (`child_reports.py:412-417`; the ERROR lane's own fallback repeats it: *"No FollowUp enqueue — the bus does not deliver messages"*).

**Step 2 — the delivery, per-row filtered (N8 folded):** for each fired FollowUp where **`DELIVER(w)`** holds, deliver via `manager.enqueue_message` + the stamp (§(a.3)); where `DELIVER(w)` does **not** hold, the report lane owns delivery and S1 only transitions the row. **`DELIVER(w)` needs a batched structural-parent read**: `get_statuses` returns status only, so the filter is served by a sibling batched `get_parent_ids(instance_ids) -> dict[id, parent_id]` (same one-query shape, `repository.py:1071` precedent) — never an N+1 loop over fired rows.

**Step 3 — PLACEMENT / ORDERING (axis-1 B1 → approver B1 — the most dangerous branch, RE-MEASURED and RE-STATED):** every canonical outcome branch in `_dispatch_post_commit_side_effects` **early-returns** — measured return lines `:5345 / :5360 / :5448 / :5622 / :5694 / :6014 (root_completed) / :6123 / :6139 / :6158 / :6192 (tool_invocation_completed) / :6464 (regular_child_completed)`; the unknown-outcome backstop at `:6469+` (the former "method tail") is reachable only for **unknown** outcomes. ⇒ **a tail placement reaches NO canonical outcome.** The prior "post-`:6256`" clause sat **inside** the `regular_child_completed` branch (opens `:6195`) — it covered one of the three named outcomes, not three. **The "or, equivalently, with the `DELIVER(w)` per-row filter" clause is DELETED** — a filter cannot create reachability; `DELIVER(w)` stays as the **delivery filter only**.

**CURE (adopted) — PER-OUTCOME placement: the fire is placed at the END of EACH TERMINAL BRANCH, before that branch's `return`:**

| Outcome branch (opens) | Fire placed **BEFORE** | NOT before | Why |
|---|---|---|---|
| `regular_child_completed` (`:6195`) | **`:6464`** (after the pair-emit at `:6256` — preserves the healthy-path fence) | — | the branch's own emit must run first |
| `tool_invocation_completed` (`:6161`) | **`:6192`** | — | the branch emits NO bus terminal of its own (the census gap) |
| `root_completed` (`:5483`) | **`:6014` ONLY** | **NOT `:5622` / `:5694`** — those demote a **non-terminal** instance (an early exit, not a completion) | the producer stamps `parent_id=None` (`:4060-4069`), so the branch emits **no bus terminal at all** — a nudge-armed watcher on a **ROOT child** had no fire anywhere on that lane |

**Reachability is restated as: *"the METHOD is reached unconditionally; the HOOK is placed per-outcome."*** **S1-at-top remains FORBIDDEN** (it would pre-empt the pair-emit and deliver a synthetic `[dependency_bus] child X completed` message to the structural parent on EVERY healthy completion, violating fence (i)). **T1b pins the fence; T1f (new) pins the root-child fire.**

**Step 4 — hook sites (per §A3.3):**
- **COMPLETED + TOOL-INVOCATION:** `child_reports._dispatch_post_commit_side_effects` (`:5285`; single call site `:3130`; reached unconditionally for `:5483` / `:6161` / `:6195`). **CONTENDED — priced in §(i).**
- **ERROR:** `daemon/services/error_reporting.py::_send_error_report` (`:439`), beside the existing `:612` / `:654` emits, before the defensive fallback at `:708`. **UNCONTENDED.**

### (a.3) The delivery precedent — the FULL TERMINATE shape, RE-PINNED (item 6)

`instance_lifecycle.py:315-577` (`_cancel_bus_watchers_for`), delivery slice **`:469-540`**. **The true order (measured `:385-565`):**

| Step | Line | Action |
|---|---|---|
| 1 | `:385` | `fired = await bus.fire_for_terminated_target(instance_id, Outcome(status="terminated"))` |
| 2 | `:412-468` | **dead-letter skip** — if the parent (`fu.target_instance_id`) is dead/terminal, **skip the enqueue** and log the cause (`_dead_letter_parent = True` at `:437/:446`, branch at `:460-468`) |
| 3 | `:472-477` | `manager.enqueue_message(...)` (JAFP-preserved — creates NO JobItem) |
| 4 | `:494-499` | `bus.mark_enqueued_by_source_target(...)` (the C1 dedup marker) |
| 5 | `:508-540` | **re-purge** (`_repurge_fired_follow_ups`, def `:580`; docstring `:608-620`) — in a **`finally`**, so it runs even when `enqueue_message` raises (`:511` comment) |
| 6 | `:565` | `cancel_for_target` (the DOWN-side drain) — **terminate-only; S1 does NOT copy this step** |

**`source_task_id` enrichment (item 3) — a MANDATORY sub-task:** the nudge FollowUp payload **lacks `source_task_id`** (`instance_ops.py:713-725` — the metadata keys are `kind`, `child_id`, `parent_id`, `message_id`, optional `mission_id`). The post-enqueue stamp (`mark_enqueued_by_source_target`) **cannot key without it** ⇒ **`ENRICH(w)`** (§PREDICATE LEDGER) must set it from the fired row's `source_task_id` **before** the stamp. This is **Phase 1 task 2b**, not an implementer's afterthought.

### (a.4) The matcher's structural siblings (what S1 must NOT do)

- The bus must **not** enqueue (state machine).
- The fire must **not** replace F1/F2 — it is a **supplement**, exactly-once via the guarded UPDATE.
- The fire must **not** be keyed on the source task (that is F1's failure mode).
- The fire must **not** be parent-scoped (that is F2's failure mode).

### (a.5) DELIVERY DURABILITY STANCE — EXPLICIT (item 4, first-class)

**The guarded UPDATE is FIRE-ONCE. It is NOT ENQUEUE-ONCE.** Two windows exist, and either **resurrects the wedge**:

| Window | Mechanism | Consequence |
|---|---|---|
| **Crash AFTER the fire, BEFORE the enqueue** | the row is `FIRED` with `enqueued_at IS NULL`. `_recover_fired_unsent` (`:2443-2499`) **only LISTS** rows for the caller; it never re-enqueues. The boot caller **finalizes and never re-enqueues** (`api.py:5379-5530`) | **LOST DELIVERY.** The parent is woken by nothing; the wedge persists with a `FIRED` row that looks "done" |
| **Crash AFTER the enqueue, BEFORE the stamp** | the row is `FIRED` with `enqueued_at IS NULL` **and** the message already in the queue. **Resume Pass 1** re-enqueues with **no paused filter in its SQL** (`instance_lifecycle.py:6103-6116`) | **DOUBLE DELIVERY.** |

**The plan's stance, stated plainly (D-2 INVERTED — implementation review, tip `077a41a33`):**

1. **THE CONTRACT IS AT-LEAST-ONCE WITH A BOUNDED DUPLICATE WINDOW (the recorded D-2 ruling was INVERTED; corrected by implementation review at `077a41a33`).** The earlier at-most-once-with-a-lost-wake framing is **WITHDRAWN**. The corrected trace: a crash between the **enqueue commit** and the **`enqueued_at` stamp** leaves `state=FIRED` ∧ `enqueued_at IS NULL` ∧ the marker set ∧ the message **already queued** ⇒ **resume Pass 1** (`daemon/services/instance_lifecycle.py:6105-6112`, `WHERE state=:fired_state AND enqueued_at IS NULL`, **no `delivered_watch_id` consultation**) **re-enqueues** ⇒ **DUPLICATE delivery**. **The lost-wake window is covered** — Pass 1 delivers once. So the honest contract is **at-least-once**, never at-most-once.
2. **Mechanism: `DELIVERY-AT-LEAST-ONCE(w)`** (§PREDICATE LEDGER) — a **distinct delivery marker** (`delivered_watch_id`) committed **BEFORE** the enqueue in its own transaction. The "same transaction as the enqueue" phrasing is **unachievable** (`manager.enqueue_message` owns its own session; §(g) fence r4 forbids altering it) — **and the marker does NOT prevent a re-enqueue**, because resume Pass 1's SQL (`instance_lifecycle.py:6105-6112`) does not consult it. ⇒ the honest property is **at-least-once**, not at-most-once.
3. **Pass 1's predicate must be made sound** — today's `enqueued_at IS NULL` predicate is unsound **because `enqueued_at` semantics are ALREADY SPLIT across four write sites with two incompatible meanings** (`models.py:125-128` — the field is described as both a "stamp by the bus AFTER the caller successfully enqueued" dedup marker *and* a re-delivery marker). The plan **does not repair the split** (out of scope, and the four writers are in a contended file) — it **avoids depending on it** by using the distinct marker.
4. **Residual, accepted and documented:** a crash between the **enqueue commit** and the **`enqueued_at` stamp** leaves the row `FIRED` with `enqueued_at IS NULL` and the message already queued; **resume Pass 1 re-enqueues it** (`instance_lifecycle.py:6105-6112` — no `delivered_watch_id` consultation) ⇒ **one duplicate delivery**. **It does not resurrect the wedge** — the duplicate is a MESSAGE, the parent wakes twice, and the **second turn is a no-op** (nothing is parked on a `FIRED` row). ⇒ **the crash window costs one redundant wake, never a permanent park.** That is why the durability stance is a *documented constraint* rather than a blocker, and why the marker-aware Pass-1 filter is a **named follow-up** rather than this lane's work.

**Compensating controls (D-2 — INVERTED and corrected; the plan is re-scoped to AT-LEAST-ONCE with a BOUNDED DUPLICATE WINDOW disclosed. No re-enqueue path is added and the AVOID on the Pass-1 file is NOT lifted):**
- **The duplicate is a MESSAGE, not a park** — the parent wakes **twice**; the **second turn is a no-op** (the first wake already discharged the wait and the watcher row is `FIRED`, so nothing is parked on it). **The bounded cost is one redundant wake**, disclosed here and at risk 4b.
- **The lost-wake window is COVERED** — Pass 1 delivers once even when the marker landed but the stamp did not; that is precisely why the contract is at-least-once.
- **S3 keeps the park released** — a `FIRED` row is counted by neither `count_pending_for_target` nor `count_fulfillable_pending_for_target`, so no duplicate wake and no missed wake can hold the parent in `WAITING_CHILDREN`.
- **S4's corrected notice is the visibility backstop** — the union population (unfulfillable-subject ∪ classic wedge) surfaces a parent that is wedged for any reason, including a lost wake on a row that is still `PENDING`.
- **NAMED FOLLOW-UP (not this lane):** a **marker-aware Pass-1 filter** (`WHERE ... AND (metadata->>'delivered_watch_id') IS DISTINCT FROM watch_id`) would close the duplicate window entirely — **deferred because `daemon/services/instance_lifecycle.py` is an AVOID file** (stale-fork contention, §(i) row 9). Registered in the deferred list.

### (a.6) MATCHER ROBUSTNESS — plain matcher + watch() child_id refusal (item 5; the discriminator instruction is SUPERSEDED)

`fetch_pending_for_child_instance` keys on **child_id alone across every target** (`repository.py:392-393`). Consequences if used unfiltered by a durable fire:

- **multiple nudgers** on the same child ⇒ **all** are fired by one terminal event ⇒ **false completion notices** to nudgers who never commissioned that episode;
- **stale episodes** (a previous, already-resolved watch on the same child) are re-fired.

**That is a NEW wedge shape** (a nudger told "completed" for work it did not wait on, or told twice). The plan's controls:

1. **Refuse `watch()` without `child_id`** — the nudge and spawn lanes both stamp it (`instance_ops.py:717`, `tools/instance.py` payload); an arm path that cannot stamp it must **not** mint a durable watcher. *(Phase 1 task 1b.)*
2. **NO staleness exclusion in `FIRE_SET` — and NO counter (leader ruling, iteration 003).** Two candidate mechanisms were considered and BOTH are **withdrawn**: the in-memory per-parent **generation counter** (`self.generation`, `dependency_bus.py:481`; `.clear()` at `:1787`) is unusable — it is never persisted onto the matched row (`watch()` inserts only `source_task_id` + `target` + payload, `:605-611`; `repository.py:357`), so **nothing on the row exists to compare against**, and per-parent monotonicity would strand a just-armed row; and the row-level **`source_task_id`-terminal** form is **wrong at fire time** — on the live row `8a1f60d7` the source task (39320) is terminal **at 18:08Z** while the watched child (sonic-leader) only goes terminal at **19:04Z**, so that predicate would **drop exactly the row the fix exists to fire**. ⇒ **`FIRE_SET` is the plain matching form with no staleness term.** Firing **all** matching PENDING rows is correct: each is a genuine waiter, and a stale one leaves PENDING after a single wake. **The "terminal" concept lives in S3, not S1** — `FULFILLABLE(w)` keys on the **child's INSTANCE status** (`INSTANCE_TERMINAL_SET`), which is what excludes backlog rows from the park count. **The two predicates are COMPLEMENTARY, not competing:** at child-terminal S1 fires every matching PENDING row; whatever **remains PENDING afterwards is backlog** (its child is already terminal) and S3 excludes it from `PARK(parent)`. The residual — one spurious wake for a stale-but-PENDING row — is **disclosed at risk 21**.


3. **T1d** pins the plain matcher's semantics: **both** nudgers on the same child **fire** (two waiters, two wakes), and a stale-but-PENDING row fires **exactly once** before leaving PENDING. *(The prior wording — "is not fired by the first nudger's episode" — contradicted T1d and the ledger; corrected.)*

---

## (b) THE SELF-REFERENTIAL CASE — ONE mechanism + ONE prevention

**The class:** `999efa71` — Ari nudged itself. Structurally unfulfillable: an instance is never its own structural parent, and its `source_task_id` is the parent's own already-terminal task. Unfalsifiable by construction; a permanent slot in the raw count; only CANCELLED or manual disposition removes it.

**Adjudication:** **NOT fixed by S1** (the watched instance is the parent itself; a persistent root never goes terminal) and **not fixed by S1 + prevention alone** for rows that already exist. Therefore:
- **The cure is S3's `FULFILLABLE` clause** (`NOT (w.follow_up_payload.metadata.child_id == w.target_instance_id)`) — it is *not* an extra fix for the self case; it is the clause S3 needs anyway.
- **S2 is KEPT (prevention)** — because the class is **accreting live** (two new watchers within 90 minutes) and a row minted unfulfillable is a permanent table leak even if the park stops counting it.
- ⇒ **ONE mechanism (S3's `FULFILLABLE`) + ONE prevention (S2).** The minimality test — *if S3 is in place, is S2 redundant?* **No**: S3 stops the **count**; S2 stops the **row**.

### (b.3) The refusal text — name the sanctioned alternative (item 16)

The refusal must not read as a dead end. **The playbook Ari was following says `send_message` to itself** — and that instruction routes through the **tree-guarded** spawn arm at `tools/instance.py:1273-1275`, which **never mints a self-watcher** because `target_instance.parent_id != parent_instance_id` is false for a self-send ⇒ **the watcher is never armed** (the arm is skipped, the message still delivers). **Ari's `999efa71` did NOT come from that path** — it came through the **UNGUARDED `instance_nudge` lane**.

⇒ **Refusal wording (full refusal, no silent downgrade):**
> `self-surgery-refused` — *an act tool never operates on its own session. To re-enter your own turn, use `send_message` to yourself: the spawn arm is tree-guarded and will deliver the message **without** arming a completion watcher (which is what you want — a self-watcher can never fire).*

Plus **one optional half-sentence warning** against `instance_nudge`-on-self (it is the lane that minted the unfulfillable watcher).

**Playbook-anchor discrepancy to flag for the implementer:** the playbook text appears at `watchdog.py:262-270` **and** `:436-438`; the two must be checked for drift before the refusal text cites either.

**VALUE PRESERVED:** the guard is a **self**-guard only. **A nudge to a NON-CHILD is a real workflow and must keep working** — ari → sonic-leader was one and **its message DID DELIVER**. S2 must **not** introduce a tree guard on the nudge lane.

---

## (c) PARK-PREDICATE RECONCILIATION — S3, ADOPTED

### (c.1) The contradiction

Park predicate = `count_pending_for_target(parent) > 0` (raw); guard's subject leg = "zero non-terminal children" (status-only, `watchdog:1602-1608` → `parents_with_non_terminal_children`). On the Ari root they disagree — the guard kept firing on a state it misdescribed.

**Reconciliation:** the park predicate must read the same population as the liveness predicate — **`PARK(parent)`** (§PREDICATE LEDGER), built on **`FULFILLABLE(w)`**.

### (c.2) The liveness read — RE-ANCHORED (item 7)

**`FULFILLABLE(w)` reads the child's status BY ID, batched — `daemon/repositories/instance/repository.py:1071` `get_statuses(instance_ids: list[str]) -> dict[str, str]`.**

**NOT** the parent-linkage-keyed `:3838-3916`: its SQL is `WHERE child.parent_id IN … AND child.status NOT IN (terminal)` — it enumerates **tree-linked** children. **Nudge watchers are NOT tree-linked** (the watched child can be any instance in the fleet — that is the whole point of the nudge lane), so a qualification built on `:3838-3916` **cannot see the live wedge's child** and **T3 would pass vacuously**. This is the axis-2 blocking correction and it is load-bearing.

**FAIL-CLOSED posture (mandatory):**

| Case | Classification | Why |
|---|---|---|
| `w.follow_up_payload.metadata.child_id IS NULL` (arm path could not stamp it) | **FULFILLABLE** | the dangerous flip is **fail-open**: treating an unknown wait as unfulfillable ⇒ premature park release ⇒ the parent runs while a child is still in flight ⇒ re-dispatch / double-run |
| child id present but the instance row is **missing** | **FULFILLABLE** | same reason |
| child is a **ghost** (`idle`, `version=1`, no msgs/tasks) | **FULFILLABLE** | `idle ∉ INSTANCE_TERMINAL_SET` ⇒ FULFILLABLE by the canonical string; the ghost carve-out is a *child-report* concern, not a park concern |
| child status read **raises** | **FULFILLABLE** (fail-closed) | mirror the healer's fail-closed skip posture (`mission_strand_healer.py:1243-1248`) |
| `w.follow_up_payload.metadata.child_id == w.target_instance_id` | **NOT FULFILLABLE** | the self-referential clause — the only positive exclusion |

### (c.3) Call sites — SEVEN SWITCHED (incl. the REAL third raw-count PARK door AND the D-1 gate leg), the RAW BOUNDARY DOCUMENTED, THREE COMPLETION DOORS NAMED (item 8 + approver B5 + D-1)

**The approver's B5 finding, confirmed by re-measurement:** the prior R4 (`:3379-3437`) is **NOT a bus-count door** — `:3397-3410` counts **CHILD-INSTANCE rows** (ghost-filtered) and writes `WAITING_CHILDREN` at `:3425`: it is a **child-liveness** door. Switching it to `PARK(parent)` would change its meaning and break the terminal-row reactivation / ghost-dedup net. **The REAL third raw-count PARK door is `:3664-3698`**: an inline `SELECT count(*) FROM dependency_watchers WHERE target_instance_id = :1 AND state = 'PENDING'` (`:3664-3673`) that then writes `instance.status = WAITING_CHILDREN` **and commits** (`:3688-3691`). The plan had misfiled it as D-1 "a completion defer, not a park" — **FALSE: it writes and commits the park.** **Interaction that made B5 blocking:** R2 (`:3511`) and R3 (`:3578`) read the raw count BEFORE `:3664` in the same root flow; once S3 qualifies R2/R3 (0 for unfulfillable-only), execution **falls through** to the still-raw `:3664`, which sees >0 and parks ⇒ **for a ROOT — the live shape (`17efd2ef`) — S3 did not close the park.**

**APPROVER'S PREFERRED CURE ADOPTED (cure A):**

| # | Site | Line | Status |
|---|---|---|---|
| R1 | park predicate | `message_processing_pipeline.py:1329` | **SWITCHED** → `PARK(parent)` |
| R2 | root-lane completion defer | `child_reports.py:3511` | **SWITCHED** → `PARK(parent)` |
| R3 | root-lane defer door #2 | `child_reports.py:3578` (`all_children_done = bus.count_pending_for_target_sync(instance_id) == 0`) | **SWITCHED** |
| **R4 (REASSIGNED)** | **THE REAL third raw-count PARK door** | **`child_reports.py:3664-3698`** (inline dependency_watchers COUNT → `instance.status = WAITING_CHILDREN` + `session.commit()` `:3688-3691`) | **SWITCHED — in-transaction. R2/R3 run in a worker thread ⇒ a SYNC TWIN (`count_fulfillable_pending_for_target_sync`) + an in-transaction SQL form are required (N7 folded here, ONE task)** |
| **R7 (D-1)** | **THE emission-time gate's bus leg — THE SEVENTH PARK DOOR** | **implementation state @ FINAL TIP `077a41a33`:** `daemon/services/child_reports.py` — the gate's bus leg now calls `self._bus_count_fulfillable_pending_for_target_sync(instance_id)` (**`:2972`**, formerly `:2637`) → returns blocked on `> 0` → the `root_completed` branch calls the gate → the downgrade sets `WAITING_CHILDREN` and commits | **SWITCHED — a FAIL-OPEN WRAPPER OUTSIDE the gate's session** (`_bus_count_fulfillable_pending_for_target_sync`, docstring `:399-421`: *"fail-OPEN wrapper, bus-singleton-missing → 0"*, **identical envelope to the raw helper**). **NOT the R4 shape.** **Leader ruling (folded, code agrees):** the wrapper shape is the **safer** one — a fail-open wrapper **cannot wedge the completion path** if the qualified read errors, whereas R4 must stay **in-transaction** because it runs inside a worker-thread session. **The predicate is identical; the transactional shape deliberately differs.** The park cannot persist through the gate once this leg reads the qualified count |
| R5 | healer bus leg (1) | `mission_strand_healer.py:562` (refusal `:568-574`) | **SWITCHED** |
| R6 | healer RZ bus probe | `mission_strand_healer.py:1240` | **SWITCHED** |

**R4-vs-R7 TRANSACTIONAL-SHAPE DISCLOSURE (leader-ruled, code agrees at `077a41a33`):** R4 (`:3664-3698`) reads the qualified count **in-transaction inside a worker-thread session** — it is the **load-bearing same-tx authority**. R7 (the gate leg) reads through a **fail-open wrapper outside the gate's session** (`_bus_count_fulfillable_pending_for_target_sync`, `child_reports.py:399-421`, mirroring the raw sibling's envelope exactly). **The predicate is identical; the transactional shape deliberately differs, and that is the safer arrangement:** a fail-open wrapper cannot wedge the completion path on a read error, while an in-session read on the completion path would hold the parent lock longer. Do **not** "unify" the two shapes.

**`:3379-3437` is DROPPED from the SWITCHED list — documented as a CHILD-LIVENESS door, unchanged.** Its count is over child-instance rows (`:3397-3410`) and it serves terminal-row reactivation / ghost dedup; `PARK(parent)` has no meaning there.

**Why the three PARK doors (R2/R3/R4) must ALL switch:** the same raw count guards three separate park writes in one root flow. Switching one or two leaves the root park reachable through the remainder — the live shape would still park.

**The RAW boundary — documented, not accidental (NINE sync readers measured; see `RAW_PARK`):**

| Raw-count consumer | Line | Why it stays RAW |
|---|---|---|
| `job_feedback_observer._bus_count_pending_for_target_sync` | `:470` | the observer's job-finalisation gate is a **fail-closed safety net**; qualifying it would weaken a guard protecting a different invariant |
| `job_feedback_observer._process_event` | `:1173` | same |
| `job_feedback_observer` (resume / deferred finalize) | `:2786` | same |
| `job_feedback_observer` (TOCTOU re-check) | `:2942` | same |
| `job_feedback_observer` (finalize re-check) | `:3838` | same |
| `job_recovery_service` | `:1319` | drift reconciliation over task/job state, not parks |
| `child_reports._emit_terminal_via_bus` helper | `:378` + 5 callers | post-fire re-trigger bookkeeping, not a park |
| `child_reports._root_completion_gate` (the gate's bus leg — **D-1**) | **implementation state @ `077a41a33`:** `_bus_count_fulfillable_pending_for_target_sync(instance_id)` at gate call **`:2972`** (formerly `:2637`); on `> 0` the gate returns blocked and the `root_completed` branch downgrades to `WAITING_CHILDREN` + commits | **PARK door — THE SEVENTH**, switched by D-1 to the qualified count via a **FAIL-OPEN WRAPPER OUTSIDE the gate's session** (`child_reports.py:399-421`, identical envelope to the raw helper) — **NOT the R4 in-transaction shape**, deliberately (a wrapper cannot wedge the completion path on a read error; R4 stays in-tx because it runs inside a worker-thread session) |
| `error_reporting` | `:263` | dead-letter liveness, not a park |
| `manager` | `:13852` | resume routing, not a park |

**The THREE unswitched COMPLETION doors (named, so the reviewer can audit them) — D-1 DELETED (it WAS a park; now R4):**

| Door | Where | Status |
|---|---|---|
| D-2 `child_reports.py:3702` (`deferred_waiting_children` producer) | measured | **unswitched — a completion defer, not a park** |
| D-3 `child_reports.py:3757` (`deferred_waiting_children` producer) | measured | **unswitched — a completion defer, not a park** |
| D-4 `child_reports.py:4434` (`idempotency_skip` path) | measured | **unswitched — a completion defer, not a park** |
| — `child_reports.py:3379-3437` | measured `:3397-3430` | **NOT a bus door at all — a CHILD-LIVENESS door; left unchanged deliberately** |

⇒ **Honest statement (re-scoped per B5 + D-1):** switching **R1-R7** (now including `:3664-3698` and the `:2637` gate leg) closes **the root park** — all **seven** raw-count park sites in the root flow read the qualified count, including the emission-time gate that previously downgraded the root back to `WAITING_CHILDREN`. **Three further COMPLETION doors** (D-2 `:3702`, D-3 `:3757`, D-4 `:4434`) still read raw counts; they can **defer a completion** on an unfulfillable watcher but they **cannot create or hold a park** (D-2 is the deferred-return of the now-switched R4 block; D-3 is a child-liveness cross-check; D-4 is a CAS idempotency_skip return reading no count). They are named, audited and assigned to the reviewer — closing them is a **follow-on** decision (S1's fire transitions the row out of PENDING, so even the raw count drops once the fire is live).

**RQ-C2 (implementer check, C-2 OPEN):** does the healer's **drive-side in-session** bus gate read the RAW count? If it does, that is a **SIXTH** reader to switch. Phase 3 task 6a.

---

## (d) NOTICE SURFACE — S4

### (d.1) OBSERVE, DON'T ASSERT — and the population (item 10)

**Today's notice** (`watchdog.py:429-434`): *"…parked with no live carrier task and zero non-terminal children (…). **A PROCESS_REPORT message is in your queue but no worker is scheduled to deliver it.**"* — false in **4 of 4** notices ever minted, because the watchdog **reads no bus rows at all** (grep-negative; register D2).

**THE POPULATION IS THE UNION — AMENDED, AND THE CODE AGREES (implementation state, pinned at `077a41a33`):** `S4_NOTICE_SUBJECT(parent)` **∪** the **classic-wedge arm**. Measured at the tip — `daemon/services/waiting_children_watchdog.py:1884-1897`:
- `if _subject_rows: _in_scope = True` — the qualified-unfulfillable set (as originally planned);
- **`elif _total_pending == 0 and not _has_non_terminal_children: _in_scope = True`** — **the classic wedge**: a parent with **NO watchers at all**, nothing to wait for and nothing to deliver it. This arm is **pre-existing behaviour** and must not be lost by the population narrowing.

**A raw-PENDING population remains FORBIDDEN**: 13 PENDING watchers fleet-wide of which **~8 belong to legitimately-parked instances** ⇒ raw PENDING would put those instances in scope ⇒ **≈ 9× notice-volume regression**. **The union of (unfulfillable-subject) and (zero-watcher classic wedge) is the ratified population** — it closes the false-negative the narrow set would introduce without importing the regression. Pinned by **T4** (both arms).

| Reason code | Row check | Notice text (verified) |
|---|---|---|
| `pending_watchers` | **`S4_NOTICE_SUBJECT(parent)`** — the watchdog's first bus read | "**N pending dependency watchers** (watched children: …) that can no longer fire" |
| `no_live_carrier` | a live PROCESS_REPORT carrier task exists? (`:707-772`) | "no live carrier task is registered for this turn" |
| `queued_report` | a `completion_report` row exists in `message_queue`, **not yet completed** | "a completion_report message (id=…) is queued and no carrier is scheduled" — **only if the row EXISTS, and CITING the row id** |
| `non_terminal_children` | `parents_with_non_terminal_children` contains this parent | "M children are still non-terminal" |

**The `:433` line is a RETAINED-VERBATIM RISK (item 11).** Its measured false rate is **100%** (4/4). It must be **re-derived at notice time from the row check** — or **dropped**. It may **never** be emitted from the template.

**The subject population is re-based on watchers, not children** (SILENT variant note).

### (d.2) DEDUPE — SPLIT THE CLOCKS, DROP THE TTL FOR SUPPRESSION (item 9)

**The measured reason the old design fails:** the 1800 s window suppresses **0 of the 3 live repeats**. The live notice cadence is **3600.5-3600.7 s**, driven by the **1 h scan default** (`config.py:2619-2622`), not by the 1800 s constant. The constant's **own docstring** (`:188-196`) defines it as a **re-arm allowance** ("a re-parked parent is re-eligible by the next tick") — **not a suppression window**. ⇒ **Reusing it as a suppression TTL is a category error.**

**The design:**

- **Suppression is EPISODE-BASED, not TTL-based** — the TTL is **DROPPED ENTIRELY** for suppression.
- **`EPISODE`** = *from the first notice-eligible tick to the first tick on which that `reason_code` no longer holds* (§PREDICATE LEDGER).
- **Episode identity** = `parent_id` in `_wedge_notified` (`:561` / `:581`) — **already** the map's key (measured: `self._wedge_notified: dict[str, float] = {}`). The map's **value** is the **sorted reason-code SET**, and **`DEDUPE_KEY = (parent_id, reason_code_set)`**. **Implementation state, pinned at `077a41a33`:** `daemon/services/waiting_children_watchdog.py:1946-1953` — `_fingerprint = tuple(sorted(_codes))`; `if self._wedge_notified.get(parent_id) == _fingerprint: continue`. The **set** form (not a bare scalar code) is what makes a multi-code parent's episode stable under code-set equality.
- **Pops happen ONLY on genuine condition-false — and the pop SITES are re-based on the ROW-CHECK, not WC membership (approver B4).** Measured: `_wedge_notified.pop` occurs at **exactly THREE sites — `:1607`, `:1617`, `:1746`** (the prior `:1460` is the **hang-pass purge tail**, and `:1466` is a **comment header** — neither pops). **`:1746` pops on "parent not in the WC set"** — and the notice's own `enqueue_message` **flips WC→RUNNING** (`:1643-1645`) — so under WC-membership semantics the notice **ends its own episode**, which is exactly the hourly re-notification loop this plan exists to kill. ⇒ **the episode-end rule is the ROW-CHECK (`S4_NOTICE_SUBJECT(parent)` becomes empty for that `reason_code`), NOT WC membership.** The WC → RUNNING → WC flip is NOTICE-INDUCED and **does not** pop the entry: the row-check still holds, so the episode continues and T5 stays deterministic.
- **Consequence:** a **re-parked** parent whose condition never became false produces **exactly one** notice per episode — **forever** — which is the correct behaviour for a **false** alarm (silence, not hourly noise).
- **T5 restated:** *"one notice per `(parent, reason_code_set)` **EPISODE**"* — **not** "per window".
- **CODE FIX IN FLIGHT (blocking B2, implementation review):** the body's end-rule — *"the first tick on which that reason_code no longer holds"* — **WINS**; the code is being corrected to it at the tip. **The rewritten `:1655` test is the discriminating proof.** No plan change to the rule.

### (d.3) KILL-SWITCH — the registry key alone is VOID (item 11)

| Property | Value |
|---|---|
| **Surface** | the DB-backed daemon **settings** service — `daemon/services/settings_service.py` (**measured ZERO unmerged-commit pressure across all 22 code-bearing branches; 1,377 lines**) |
| **Key** | `wedge_notice_emission_enabled`, registered `requires_restart=False` |
| **MEASURED KEY COUNT** | **6** `requires_restart=False` keys exist today (`:519`, `:545`, `:623`, `:644`, `:661`, `:678`) — **not ≥7** (corrected) |
| **THE VOID PROBLEM** | **registering the key is NOT sufficient.** The watchdog's `_enabled` is **boot-time** (`:536` `self._enabled = bool(enabled)` — assigned once in `__init__`). A registry key alone changes nothing at runtime |
| **Required NEW CODE** | a **per-tick read** at the emission branch. **Precedent (measured):** `metrics_gate.py:131` reads a gate value per call; `settings_service.get_overrides` is a **per-call DB read** (`:1111-1116`) |
| **Read point** | the single **notice-enqueue** branch |
| **Default** | **NOTICES ON** |
| **Gates** | **NOTICE EMISSION ONLY** — canonical **`SWITCH_OFF`** |
| **NEVER gates** | detection, classification, **S1**, **S2**, **S3**, the healer's work, the park predicate, the report lane |
| **Honest limit** | **NEW CODE riding the staged fix.** No restart-free surface governs the watchdog **today** (env vars frozen at process start; `app_settings` holds 3 `limits.*` rows all `requires_restart`) ⇒ **the switch does NOT quiet tonight's noise.** |
| **Default-ambiguity obligation** | a **`docs/guidelines/`** file is OWED: scope, default, and that switching it off hides the symptom while leaving the defect live |

---

## (e) RECONCILER SHAPE AT THE WATCHER LAYER — DOCUMENTED REASON NOT TO ADD ONE

**NO parallel watcher-level sweep.** Three reasons, each measured:

1. **r6 (integrate, never parallel).** The healer's leg (1) **refuses to heal when `bus_pending > 0`** (`mission_strand_healer.py:568-574`) — *"the healer refuses to heal exactly the wedge a stuck PENDING watcher creates"*. S3 **re-bases the healer's own readers (R5/R6)**, so the resolution is delivered *inside* the healer.
2. **r9 + measured emptiness.** After S1+S2+S3 the residual set is empty **by construction** — S1 fires at the transition (COMPLETED, TOOL-INVOCATION, ERROR), S3 excludes the unfulfillable, S2 stops them being born. A sweep would be machinery for a set that cannot exist.
3. **The existing sweep lane is inert — and `watch_reconcile_sweep` is a FALSE PREMISE (item 20).** The ALWAYS-ON periodic orphan sweep is permanently inert (`daemon/api.py:1919-1920` constructs it with `get_dependency_bus()` **before** `init_dependency_bus` sets the singleton at `:2972` ⇒ the service holds `None` for life ⇒ every tick cancels nothing). **AND `watch_reconcile_sweep.py` belongs to the JOB-watcher system — measured ZERO `dependency_bus` hits — it was never a bus caller.** ⇒ **Do not add a second periodic lane on a precedent that provably cannot run, and do not cite a file that is not in this subsystem.** (Anomaly `fcf89d88`; disclosed at §(j)(vi).)

**How the resolution is delivered instead (two mechanisms, no third):** *immediate* — S1 fires + delivers at the terminal transition; *reactive* — S3's `PARK(parent)` means an unfulfillable waiter never holds a park and never blocks the healer. The boot-only orphan sweep remains the historical backstop; the plan does not rely on it.

---

## (f) DISCRIMINATING TESTS — ROW STATES ONLY, EACH WITH ITS REGISTERED ARTIFACT

| Test | Shape | Asserted OBSERVABLE STATE | Registered executable artifact |
|---|---|---|---|
| **T1** | **S1 (COMPLETED — `regular_child_completed`)** — **harness: the REAL entry point** `_process_child_completion_and_notify_parent` → `_dispatch_post_commit_side_effects`; the fixture names its outcome class (`regular_child_completed`, a structural child) | parent P + watcher W armed for child C **via the NUDGE lane** (`_register_nudge_completion_watcher`); C's instance row reaches a terminal status ⇒ `W.state=='FIRED'` ∧ `W.fired_at is not None` ∧ `count_pending_for_target(P)==0` | `tests/unit/test_reconciler_wedge_fix.py` → pack **`wedge_fix_suites_unit_test`** → `test/packs/wedge_fix_suites_unit_test.sh` → PACKS.md **by name** |
| **T1b** | **S1 ORDERING (item 2)** — harness: the real `regular_child_completed` producer path on a **structural** child (the healthy spawn lane) | on the **healthy** spawn path, the **structural parent receives NO bus FollowUp** — assert the parent's `message_queue` contains **no** row whose `source` is the bus/nudge lane for that child; assert `DELIVER(w)` false ⇒ no enqueue | same file → same pack |
| **T1c** | **S1 CENSUS GAP (item 21)** — **harness: the real `tool_invocation_completed` producer path** (`_process_child_completion_db_sync` → outcome `:4271` → dispatch), NOT a synthetic outcome object | a parent waiting on a **`tool_invocation_completed`** child ⇒ `W.state=='FIRED'` (today: never) | same file → same pack |
| **T1d** | **S1 MATCHER (item 5; re-scoped by the leader ruling)** — harness: the real `regular_child_completed` producer path (same as T1) | two nudgers on the **same** child ⇒ **BOTH** reach `state=='FIRED'` ∧ both `fired_at` set (two waiters, two wakes — **the plain matcher's intended semantics**); a **stale-but-PENDING** row on the same child also FIRES **exactly once** and then leaves PENDING (the disclosed residual, risk 21) | same file → same pack |
| **T1e** | **S1 ERROR LANE (branch A)** — **harness: the real `_send_error_report` path** (`error_reporting.py:439`), NOT a direct bus call | a **nudge-armed** watcher on a child that raises ⇒ `W.state=='FIRED'` (today: never) | same file → same pack |
| **T1f** | **S1 ROOT CHILD (approver B1)** — **harness: the real `root_completed` producer path** (`_process_child_completion_db_sync` stamps `parent_id=None` `:4060-4069` → dispatch), NOT a synthetic outcome | a parent P + watcher W armed for a **ROOT child** C (C.parent_id is None) via the NUDGE lane; C completes ⇒ `W.state=='FIRED'` ∧ `W.fired_at is not None` ∧ `count_pending_for_target(P)==0` — **today: never fires (the branch emits no bus terminal)** | same file → same pack |
| **T2** | **S2** | `target == watched worker == commissioner` ⇒ **NO** `dependency_watchers` row with that target and `state=='PENDING'`; include the live shape (`17efd2ef` self-nudge) | same file → same pack |
| **T2b** | **S2 VALUE PRESERVED** | a nudge to a **non-child** target ⇒ a watcher IS minted and the message IS delivered | same file → same pack |
| **T3** | **S3** | `RAW_PARK(parent)` true ∧ zero non-terminal children ∧ **the watched child is terminal or self-referential** ⇒ the parent **does not remain** in `WAITING_CHILDREN` (assert `instances.status`). **Must NOT pass vacuously** — the fixture's watcher must be a **nudge-armed, non-tree-linked** row (item 7) | same file → same pack |
| **T3b** | **S3 (D-1) — harness: drive the FULL GATE CHAIN**, not just the sync helper: `_root_completion_gate(None, instance_id)` → assert it returns pass (first element True) **AND** assert `instances.status != WAITING_CHILDREN` **AND** assert no downgrade commit occurred — for a root whose only PENDING watchers are unfulfillable (today: the gate returns blocked on raw > 0 and downgrades the root back to `WAITING_CHILDREN`, so S3 as previously scoped did not close the root park). **The gate leg (`:2637`) must read the qualified count, not raw.** | same file → same pack |
| **T4** | **S4** | with **no** queued `completion_report` row ⇒ the notice content **does not claim one**; with one ⇒ the claim **is present and cites the row id**. Also: **no notice** for a parent whose only PENDING watchers are FULFILLABLE (item 10 — the 9× regression guard) | same file (`test_wedge_notice_content` family) → same pack; native alternative `tests/unit/services/test_waiting_children_watchdog.py` → pack **`waiting_children_watchdog_unit_test`** |
| **T5** | **S4 DEDUPE (deterministic — approver B4)** | episode-end is the **ROW-CHECK** (`S4_NOTICE_SUBJECT(parent)` empty for that `reason_code`), **NOT WC membership**: (a) two passes, same `(parent, reason_code)`, condition still true ⇒ **EXACTLY ONE** notice row; (b) the notice's own WC→RUNNING→WC flip (`:1643-1645`) does **NOT** end the episode ⇒ **still one**; (c) the row-check turning false (e.g. the watched child's watcher resolved) **does** end it ⇒ a later recurrence may notify again. Assert `message_queue` row counts per episode | same file → same pack |
| **T6** | **S4 KILL-SWITCH** | **`SWITCH_OFF`** ⇒ **ZERO NEW** notice rows **WHILE** detection/classification still runs **and the healing path is still exercised**; switch ON (default) ⇒ notices ARE created. **MUST FAIL if the switch is wired into the healing path.** Also asserts the **per-tick** read (a mid-run flip takes effect without a restart) | same file → same pack |
| **T7** | **fence + N6** | normal path: parent arms a watcher, child completes normally, watcher fires, parent wakes and leaves `WAITING_CHILDREN` — **observably unchanged**; the supplementary fire is a no-op; **exactly one** delivery; **`DELIVERED_MARKER`**: the `delivered_watch_id` marker row exists and is keyed to the watch_id **before** the `message_queue` row appears (two-transaction ordering, approver B3) | same file → same pack |

### (f.1) THE THREE PRE-BUMP TEST-ARM ITEMS — **LANDING-IN-FLIGHT** (one commit, BEFORE the version slot is cut)

**Rationale (reviewer's):** they are **free today**; landing them after the bump would cost **a new version slot + a re-stage + a re-seal**. The code lane lands all three in **ONE commit before the version slot is cut**.

| Item | What it pins | Why it is load-bearing |
|---|---|---|
| **F4 (r3d)** | a **FRESH episode + carrier-None ⇒ NO notice**, plus a **boom-called control** — pins the **in-loop carrier-None guard** (the reviewer's `:2035-2041`) | on a fresh episode, **deleting that guard emits a FALSE WEDGE NOTICE on an unknown carrier state** — the exact harm class this lane exists to prevent |
| **F5** | a **queued-report tri-state test** — the episode is stamped with `queued_report` **+ an injected queue-read failure** ⇒ the **episode is unchanged** | pins the tri-state leg; it currently has **ZERO test coverage** and is a **NEW production path introduced by R1** |
| **F1-F3** | **call-counters asserting each injected failure actually FIRED** | see the generalisable lesson below |

**GENERALISABLE LESSON (F1-F3 — record with the lane's lessons):** **"mutation-proven is NOT the same as self-guarding."** An **inert monkeypatch** (or a `get_dependency_bus()` returning **None**, which yields `subjects={}` with **NO exception**) can leave a leg **green** while the **mutation is live**. This is **the same defect class this lane drove out of the earlier tests**, and it **recurs unless the legs assert their own injection** — hence the call-counters, not a hand-written "the patch fired" assumption.

**Pack / registration facts (verified by the plan author — PACKS.md is 1,523,869 bytes; the explorers could not read it):**
- `wedge_fix_suites_unit_test` runs `tests/unit/test_reconciler_wedge_fix.py` + `tests/job_queue/test_seam_invariants.py` (10 + 68 today); registered under that **name** in PACKS.md, with the `wlts-wedge` view row.
- `waiting_children_watchdog_unit_test` runs `tests/unit/services/test_waiting_children_watchdog.py` (47→49); registered **by name**.
- `claim_guard_locks_unit_test` also covers the seam file (overlap intentional); `orphan_active_job_recovery_suites_unit_test`; `security_boundary_hygiene_suites_unit_test` — all **by name**.
- **Cite PACKS.md rows BY NAME, never by line** — copies diverge (item 17).
- **REGRESSION HAZARD:** `tests/unit/services/test_a5_wedge_notify_work.py` asserts `census_stays_23_1_0` ("no new writer/creator/mint site"). **The S4 kill-switch and any new notify site must update this census deliberately**, and the reviewer must say so. **The file is in NO pack** — register it or route the affected assertion through a registered file.
- Pack script shape (measured): `set -euo pipefail`; unset `SSL_CERT_FILE`/`SSL_CERT_DIR`; `cd "$PROJECT_DIR"`; `timeout 120s .venv/bin/pytest <files> --tb=short -q`; triage `124`→TIMEOUT, `0`→PASS, else FAIL.
- **Every new case lands in an already-registered file ⇒ zero new pack machinery.**

---

## (g) HEALTHY-PATH FENCE — the three observables

1. **(i) Spawn-lane child completion still fires F1/F2 exactly as today** (`:6222` / `:6256`) and **the structural parent receives NO bus FollowUp on the healthy path** (`DELIVER(w)`). *Observable:* watcher `state=='FIRED'`, `fired_at` set, **exactly one** delivery, **no bus-sourced `message_queue` row** to the structural parent.
2. **(ii) `PARK(parent)` still parks a parent with genuinely-pending fulfillable watchers.** *Observable:* `instances.status == 'WAITING_CHILDREN'`. **(approver B5 re-scope): the fence holds across ALL THREE raw-count PARK doors** — R1 (`message_processing_pipeline.py:1329`), R2 (`child_reports.py:3511`), R3 (`:3578`), R4 (`:3664-3698`); the child-liveness door (`:3379-3437`) is **unchanged** and keeps its own semantics.
3. **(iii) Terminate still runs F3 unchanged** (`instance_lifecycle.py:385` + DOWN-side `cancel_for_target`). *Observable:* upstream watchers FIRED (`outcome='terminated'`), DOWN-side rows CANCELLED.

Also fenced (r4): the **v0.31.0 group-chat room machinery (LIVE)** and the **report-injection substrate** (RDRS + promote-fix). S1's bus method must not enqueue, must not touch `report_injections`, must not alter `manager.enqueue_message`.

### (g.1) THE r3 FENCE — RESTATED (F9; the reviewer's independent re-derivation is the EVIDENCE OF RECORD)

**Previous wording was too strong. The corrected claim:**

> **The r3 fence covers the FAILURE/UNKNOWN **OR CONTRADICTORY-READ** branch.** Every change introduced by the R1/r3 delta is gated on a read failure — **or** on a read that came back **CONTRADICTORY** to another read in the same pass. **No changed line can execute on the healthy path and alter the rendered notice, the clause order, or the row set.** The ONLY render literal changed is the **None-count text**; **all other changed strings are logger messages in failure branches.**

**The gray zone is real and is named here, not papered over:** the `count == 0 → None` normalisation is **REACHABLE WITH ALL READS SUCCEEDING**, under a **child-completion RACE** between the batched gate query and the per-parent count. In that branch it **corrects a fabricated "0"** — it is **not a steady-state path** — but it **DOES change the rendered line**. Hence the fence is restated as a branch-scoped claim, not a line-level one.

**⇒ The healthy-path guarantee is about the OBSERVABLE OUTCOME — no false wedge notice, no episode mutation — NOT a literal line-level claim.**

**EVIDENCE OF RECORD (reviewer-providced, stronger than the dispatch wording it replaces):** the reviewer's **independent per-hunk classification across BOTH tips** — **31 hunks in the watchdog, 1 each in `child_reports` and `dependency_bus`** — classifying **EVERY** hunk, confirming that no changed line can execute on the healthy path and alter the rendered notice, the clause order, or the row set. **This per-hunk classification — not the dispatch wording — is the fence evidence of record.**

---

## (h) RUNBOOK — REBUILT AROUND THE **8-ROW** REPAIR ARTIFACT (item 13)

**STATUS: DISCHARGED-BY-EXECUTION (live, 2026-11-18 21:18-21:20Z; independently verified).** The Emperor-attested 5-block repair bundle was **EXECUTED on live** at 21:18-21:20Z and independently verified: parent `17efd2ef` **completed at 21:21:24Z**, wedge notices **ceased**, `--verify` checks 1-4 **PASS**, zero drift. §(h) is retained as the historical procedure + the executed record; the runbook line below is now **a record of what happened**, not an instruction to do it.

### (h.1) The repair bundle that EXISTS

| Property | Value |
|---|---|
| **Artifact** | `.agents/shared/planning/wake-path-carrier-fix/repair/emitted/live_wedge_repair_request__20261118T010144Z.{sql,receipt.md,receipt.json}` |
| **Shape** | **8 blocks — MEASURED, not asserted:** `W1` 8a1f60d7 · `W2` 999efa71 · `W3` 5602e91d · `W4` c83e12a2 · `M1` 0cdc0b31 · `M2` 90493206 · `M3` 06001f88 · `M4` a1561f19 (`grep -c "^-- BLOCK"` = **8**) |
| **Status** | **ceremony-ready; NOTHING EXECUTED** — the receipt carries a NOT-EXECUTED banner; the generator has no `--apply` and opens every connection `default_transaction_read_only=on` |
| **Provenance — CORRECTED** | Emitted by the **repair developer** (lane-external author). The adjudication described it as *"not present in either tree's `repair/emitted/`"* — **true of the REPO-ROOT `repair/emitted/`, false of the lane**: the bundle **IS present, untracked, inside this lane's planning directory** (which is why a repo-root listing misses it). **Cite it by its in-lane path**, and treat it as **authored outside this lane and not re-derived by it**. |
| **Independent corroboration** | the receipt's own pre-checks confirm **A14: task 39564 exists and is terminal** ⇒ the §(h.3) restart re-analysis is corroborated by the bundle, not only by the census. **A3** gives task **39320 completed_at `2026-11-17 18:08:26.230850`** — a **precision correction** to the brief's "18:08:20". **A8: `pending_for_parent=4 (unknown=0)`, `outstanding_for_parent=4`** ⇒ 4 watchers + 4 commissions = the 8 blocks. **A1/A9: the parent is still `waiting_children`.** |
| **This lane's relationship** | **none** — this lane neither executes nor re-derives it; it references it and states its clock pressure |

### (h.2) The leader ruling's four points (folded)

1. **The repair scope is the widened 8-row set**, not 2 watchers + 2 missions.
2. **Nothing is executed** — the bundle is a *request*.
3. **The Emperor's word is the only gate** — **CEREMONY-CLASS**.
4. **This lane does not mutate live state.**

### (h.3) CORRECTED RESTART ANALYSIS — the plan's census is now stale (item 13-ii)

> **THE CENSUS RAN AT 22:55Z. IT WAS TRUE WHEN WRITTEN AND IT IS FALSE NOW.**

**Measured:** **task 39564 completed at 23:03:54Z.** Therefore:

| Before (22:55Z) | **Now** |
|---|---|
| 2 structurally dead + 2 legitimate (their source task still active) | **all 4 watchers' source tasks are terminal** ⇒ the **boot-only** orphan sweep (`dependency_bus.py:1753`, predicate `source_task_id NOT IN (SELECT CAST(id AS TEXT) FROM task WHERE status IN ('running','pending','paused'))`) **WOULD NOW CANCEL ALL FOUR** |
| a restart clears 2, leaves 2 ⇒ the park count stays > 0 ⇒ the parent still parks | **a restart clears the BUS gate entirely** ⇒ `count_pending_for_target(17efd2ef) == 0` ⇒ **the MISSION gate alone holds** |

⇒ **The mission gate alone holds the wedge from now on, and it drains ~04:53Z.** `child_reports.py:2691-2695` — `mission_commissions = self._count_outstanding_mission_commissions(instance_id); if mission_commissions > 0: return False, f"mission_commissions={mission_commissions}", False` ⇒ the root's completion is refused while outstanding commissions remain.

**This MATERIALLY CHANGES the Emperor's restart option** — restart is now *near*-curative for the park (it clears the bus gate), but it **still delivers no wake and still does not deliver sonic-leader's lost report**, and it is **still ceremony-class**. **State it AS OF CEREMONY-TIME RE-INSPECT** — the census must be re-read before the decision, because the 23:03:54Z transition is not the last one that can move this analysis.

### (h.4) Clock pressure (item 13-iii)

| Clock | Time | Consequence |
|---|---|---|
| **lane-7 TTL #1** | ~**02:30Z** | the emitted request is **stale** if the ceremony runs past it |
| **lane-7 TTL #2 / mission drain** | ~**04:52-04:53Z** | the mission gate releases on its own |

⇒ **REGENERATE the emitted request at ceremony time if past those clocks.** **Remainder-only re-emission is the artifact's designed path** — the bundle is built to re-emit only what is still outstanding, so a partial drain does not invalidate it.

### (h.5) THE WEDGE IS NOT FATAL

The Ari root is **reachable** — it answers every wake. Cost = an hourly wake/re-park cycle + misleading notice text. No work is lost; no report was owed.

### (h.6) Sequence (the only curative path tonight)

| Step | Action | Classification |
|---|---|---|
| 1 | **Execute the emitted 8-block repair request** (or its remainder-only re-emission if past the clocks) | **CEREMONY-CLASS — the Emperor's word is the only gate** |
| 2 | **One real dispatch to Ari** | SAFE — the **TRIGGER** for the turn-end at which `PARK(parent)` re-evaluates |
| 3 | Turn-end ⇒ `count_pending_for_target == 0` and `count_fulfillable_pending_for_target == 0` ⇒ the parent **leaves WAITING_CHILDREN** | — |
| 4 | `S4_NOTICE_SUBJECT(parent)` is empty ⇒ **notices stop** | — |

### (h.7) RUNBOOK LINE (operator-facing)

> *Ari's hourly wedge notice is a false alarm: there is no PROCESS_REPORT to deliver. Tonight's live fix is the emitted 8-block repair request (`live_wedge_repair_request__20261118T010144Z`, ceremony-class, Emperor-gated — regenerate remainder-only if past the lane-7 TTLs), then one dispatch to Ari. Do NOT flip any knob: the notices stop because the condition becomes false, not because a switch is flipped. NOTE: task 39564 completed 23:03:54Z, so a restart would NOW also clear the bus gate — but restart delivers no wake, does not deliver sonic-leader's lost report, and remains ceremony-class. Re-inspect the census at ceremony time.*

### (h.8) Alternatives, honestly classified

| Alternative | Verdict |
|---|---|
| A dispatch **without** the repair | **SAFE but NON-CURATIVE** — answers, then re-parks while any PENDING watcher/mission remains |
| `POST /recover_report_delivery` | **SAFE but predicted NO-OP** — the lanes key on obligation/report rows; the Ari root has none; already on cadence |
| **A daemon restart** | **CEREMONY-CLASS; NOW NEAR-CURATIVE FOR THE PARK, STILL NON-CURATIVE FOR THE REPORT.** All four watchers' source tasks are terminal ⇒ the boot sweep cancels all four ⇒ the bus gate clears. **But: CANCEL ≠ FIRE — no wake is delivered, sonic-leader's lost report is NOT delivered, and the mission gate still holds until ~04:53Z.** Re-inspect at ceremony time. |
| Any live DB mutation by this lane | **OUT OF SCOPE.** |

**LOG RETENTION LIMIT:** ≈ **90 minutes** (6×10 MB rotating under DEBUG `[LLM] Stream`) ⇒ the 18:06-22:33 forensic window is **already gone**; **tests must not depend on log-window forensics**.

---

## (i) FILE LIST WITH CONTESTED-FILE COST

| # | File | Contention | Change | Cost / note |
|---|---|---|---|---|
| 1 | `daemon/services/dependency_bus.py` | **UNCONTENDED** | S1 fire + S3 qualified count | the bus layer is uncontended |
| 2 | `daemon/repositories/dependency_bus/repository.py` | **UNCONTENDED** | reuse `fetch_pending_for_child_instance` (`:343-395`); one PENDING-for-target read for the qualification | child id already in JSONB (`:352-365`) |
| 3 | **`daemon/services/error_reporting.py`** | **UNCONTENDED — measured ZERO unmerged commits across all 22 code-bearing branches** (937 lines) | **S1's ERROR hook** (`:439`, beside `:612`/`:654`) | this is where C-3 lands the fix |
| 4 | `daemon/services/waiting_children_watchdog.py` | **UNCONTENDED** | S4: bus read + classification + text + episode dedupe + per-tick switch | gains its **first** bus read |
| 5 | `daemon/services/mission_strand_healer.py` | **UNCONTENDED** | S3 at `:562`, `:1240` | r6 integration |
| 6 | `daemon/tools/instance_ops.py` | **UNCONTENDED** | S2 self-surgery refusal + refusal text | ~3 lines + wording, precedent `daemon/tools/instance_ops.py:894/:1332/:1768` (the `self-surgery-refused` tokens; the guards open at `:891/:1331/:1767`) |
| 7 | `daemon/services/message_processing_pipeline.py` | **UNCONTENDED** | S3 `PARK(parent)` at `:1329` | the primary park |
| 7a | `daemon/services/child_reports.py` — **`_root_completion_gate` (a SEVENTH park site, D-1)** | inside the same contended file | the gate's bus leg at `:2637` reads `self._bus_count_pending_for_target_sync(instance_id)` (raw) → `:2638-2639` returns blocked → the `root_completed` branch (`:5483`) calls the gate at `:5521` → `:5562-5572` sets `WAITING_CHILDREN` and commits. **SWITCHED to the qualified count** via a **FAIL-OPEN WRAPPER OUTSIDE the gate's session** (`_bus_count_fulfillable_pending_for_target_sync`, `:399-421`; gate call `:2972`) — **NOT the R4 in-transaction shape, deliberately** (leader ruling: a wrapper cannot wedge the completion path on a read error; R4 stays in-tx inside a worker-thread session). **Predicate identical, transactional shape different — do not unify** | **approver adjudication D-1**: after S3 qualifies R2/R3/R4 and stamps the root COMPLETED, this emission-time gate reads raw > 0 (backlog rows) and **DOWNGRADES the root back to WAITING_CHILDREN** — the park persists. Cure: switch the `:2637` leg. **T3 extended to drive the full gate chain** |
| 8 | **`daemon/services/child_reports.py`** | **CONTENDED** — `fix/empty-job-completed-event` (4 commits: `5368a12d4`, `96f1f6d35` +2), **a STALE FORK** (§CONTESTED-TIP) | **S1's COMPLETED + TOOL-INVOCATION hook, PER-OUTCOME** (`:5285`/`:3130`); **S3 doors R2/R3/R4** (`:3511`, `:3578`, **`:3664-3698`** — approver B5; `:3379-3437` is a child-liveness door, **unchanged**) | **THE LANE'S LARGEST COST.** 4 hunk groups in the most contended file. **Countervailing:** the fork is stale (neither descends from nor contains base; zero plan anchors at its tip), so there is **no live merge conflict** — only a future reconciliation if that branch is ever rebased and lands. **cn `888d612d` land-first.** |
| 8a | **`daemon/services/settings_service.py`** | **UNCONTENDED — measured ZERO unmerged pressure** (1,377 lines) | register `wedge_notice_emission_enabled` (`requires_restart=False`) | **6** such keys exist today; this is the 7th |
| 9 | `daemon/services/instance_messaging.py`, `daemon/services/instance_lifecycle.py` | **CONTENDED — 3 branches** on `instance_lifecycle.py`: the **STALE FORK** `feature/langgraph-checkpoint-perf` @ `c37c870c56` (2026-08-26) **+ `feature/video-mock` (1) + `feature/video-mock-room` (1)** — the **video pilot** is a known separate lane. `instance_messaging.py` remains **1** (`langgraph-checkpoint-perf`) | **AVOID** — fence observable (iii) lives at `instance_lifecycle.py:385`; **the plan's decisions are unaffected (AVOID item, zero footprint)** | implementer pre-flight re-measure; **union-census count = 3** (see the census-basis line) |
| 10 | `daemon/tools/instance.py` | **CONTENDED — 3 branches**: the **STALE FORK** `feature/done-marker` @ `e5e5c4a80c` **+ `feature/video-mock` (1) + `feature/video-mock-room` (1)** (the **video pilot** lane) | **AVOID** — spawn lane is tree-guarded; **zero footprint** | S2's guard goes in `instance_ops.py`; **union-census count = 3** |
| 11 | `daemon/repositories/instance/repository.py` | **UNCONTENDED** | **`get_statuses` (`:1071`)** for the batched by-id liveness read | read-only |
| 12 | `daemon/api.py` | **UNCONTENDED** | **NOT required by this plan** — the S4 switch reads through `settings_service`, not an env var or a lifespan flag | — |
| 12a | `daemon/services/orphan_watcher_sweep.py` | **UNCONTENDED** | **DISCLOSED, NOT FIXED** (the inert periodic lane) | anomaly `fcf89d88`; §(j)(vi) |

**REMOVED from the plan (item 20):** `daemon/services/watch_reconcile_sweep.py` — **not a bus caller** (zero `dependency_bus` hits).

**PRECISION CORRECTIONS CARRIED (fold round 4 — the reviewer's framing, adopted verbatim):**
- **F-1's precise framing:** **21 files use the old name in TESTS** (91 whole-repo); of the **11 "untouched"** files only **3** carry real call syntax — the other **8** are **prose/docstring references**, so *"11 real call sites"* **OVERSTATED it** (harmless). The **10 mock/double sites** were stamped **site-by-site** (0→0, 1→1, …) with the **SAME value**; the **real bus keeps BOTH methods** so untouched call sites still resolve; and an **unstamped mock would fail LOUDLY** (`MagicMock() > 0` **raises**) rather than silently. **⇒ a future lane can TRUST the F-1 fix.**
- **F-6a (house trap):** the **worktree `PACKS.md` is a BRANCH-ERA SNAPSHOT** (its line numbers differ from the main copy) ⇒ **a later grep-empty there is NOT evidence of non-registration.** Cite the pack by **NAME** (the plan's standing rule) and check the **main copy** before concluding a pack is unregistered.

**CENSUS BASIS (reproducible — added so the approver can re-derive every count above):** **19 `origin/*` remote-unmerged vs `origin/latest`; 27 local-unmerged; union 41 short-name entries** (the two sets overlap by name) ⇒ **the census TOTAL is rule-dependent; the per-file contention table is the load-bearing evidence.** The `22 code-bearing branches` figure elsewhere in this plan is the adjudicated code-bearing set. **Measurement caveat:** the union sweep stripped the `origin/` prefix from remote names, so it **under-reports remote-only contention** — the authoritative REMOTE numbers are the `origin/*` sweep's; **both sweeps are cited and the combined per-file totals above are the file-list truth.** **Unchanged by the union pass: `child_reports.py` = 4 (`fix/empty-job-completed-event`) and `instance_messaging.py` = 1 (`feature/langgraph-checkpoint-perf`).** **EVERY OTHER file on this lane's list is ZERO under BOTH passes** (`dependency_bus.py`, `dependency_bus/repository.py`, `waiting_children_watchdog.py`, `mission_strand_healer.py`, `job_recovery_service.py`, `instance_ops.py`, `repositories/instance/repository.py`, `message_processing_pipeline.py`, `error_reporting.py`, `settings_service.py`) ⇒ **the primary-fix layer remains entirely uncontended.**

**HARD EXCLUSIONS (never touched):** `daemon/services/skill_*.py`, `daemon/tools/skill_*.py`, `daemon/services/job_processor.py`, `daemon/config.py`, `daemon/manager.py`, `daemon/graph.py`.

**Hard-exclusion contention (context only — these files are never touched, so the count carries no cost):**
| File | Contending branches (union census) | Count |
|---|---|---|
| `daemon/manager.py` | `feature/langgraph-checkpoint-perf` (1) + `fix/empty-job-completed-event` (1) + `test/test-pack-20260407015139` (1) + **`feature/self-restart-drill-fixes` (1) + `feature/skill-capture-gates` (1)** — the last two are **local-only** branches the earlier `origin/*` sweep could not see | **5** |

**MIGRATIONS:** **none** — see §(j).

### CONTESTED-TIP POSTURE (item 19 — REPLACES "re-measure at the contended tip")

> **The contention is a STALE FORK from 2026-09-20.** It **neither descends from nor contains** base `eca98397d`. **ZERO plan anchors exist at its tip.** ⇒ **DO NOT re-pin anything to it.** A prior instruction to "re-measure at the contended tip" is **withdrawn** — it would have anchored the plan to a tree that cannot produce this lane's code.
>
> **Implement against base.** If that branch is ever **rebased and landed**, re-measure **only two hunk groups**: the **root-gate** hunks and the **dead-letter-publish** hunks. Nothing else in this lane overlaps.
>
> **The same staleness applies** to `feature/langgraph-checkpoint-perf` @ `c37c870c56` (**2026-08-26**).
>
> **IMPLEMENTER PRE-FLIGHT (required, Phase 1 task 0):** re-measure **`instance_lifecycle.py`**, **`instance_messaging.py`**, **`tools/instance.py`** and **`manager.py`** at implementation time. These are the four files whose anchors the plan cites from base but touches only indirectly — a stale anchor there becomes a false citation in a code comment.

**Test files:** `tests/unit/test_reconciler_wedge_fix.py` (all T1-T7 + T1b/c/d/e/**f** — registered); optionally `tests/unit/services/test_waiting_children_watchdog.py` (registered). `tests/unit/services/test_a5_wedge_notify_work.py` — census-sensitive, in NO pack (§(f)).

---

## (j) THE HARDER QUESTION FIRST — NEW TABLE OR MIGRATION?

**ANSWER: NO NEW TABLE. NO MIGRATION. NO NEW COLUMN.**

| Need | Existing structure that already carries it |
|---|---|
| watcher state | `dependency_watchers.state` ∈ {PENDING, FIRED, CANCELLED} (`models.py:49-71`, `:208`) |
| fire timestamp | `dependency_watchers.fired_at` |
| delivery dedup | `enqueued_at` (**avoided** — semantics already split, §(a.5)) + **the plan's distinct `delivered_watch_id` marker** (carried in the existing `watcher_metadata` JSONB column — **no new column**) |
| **the watched child id** | **`follow_up_payload` JSONB** — `follow_up_payload.metadata.child_id` (DB column `watcher_metadata` is NOT written by `watch()`; `models.py:186-193`; `repository.py:352-365`, filter `:393`) |
| the child's liveness | **`get_statuses` (`instance/repository.py:1071`)**, batched |
| the notice dedupe state | the watchdog's **existing in-memory** map, `_wedge_notified: dict[str, float]` (`:581`), value → `reason_code` |
| the kill-switch | **`daemon/services/settings_service.py`** (1,377 lines, **zero unmerged pressure**) — a new **key** (`requires_restart=False`), not a new table |
| the missions | the existing mission records (ceremony repair) |

⇒ **The migration census stays at 133 `.sql` files** (newest `20261116_083000_add_room_resolution_warnings.sql`; the commission's "130 / head 20261112" figure is **stale**). Per **r7**, the version slot is derived **at bumptime, never reserved** (cn `84e24343`); order: bump commit → annotated tag → stage worktree detached at the tag → FE rebuild → `stage.sh`. Per **r8**, POK is at its 256-active cap ⇒ route knowledge via `project_cn_add` + `experience()`; **do not blind-archive** high-traffic entries (cn `7892e539`).

**§5 — RELEASE SECTION (FINAL wording, fold round 5):** the **staged-release packaging is STILL OWED** — bump commit → annotated tag → stage wt detached at the tag → FE rebuild → `stage.sh`, per r7 — and the version slot is **DERIVED AT BUMPTIME, never reserved** (r7 + cn `84e24343`). **NO live promote — the Emperor's ceremony owns it.** **The live-wedge unblock runbook stays DISCHARGED-BY-EXECUTION** (the repair ran **21:18-21:20Z**; parent completed **21:21:24Z**; `--verify` **1-4 PASS**). **Sequencing note:** the pre-bump test-arm items land in **ONE commit BEFORE the version slot is cut** — free today, versus a new slot + a re-stage + a re-seal after the bump (§(f.1)).

---

## (j)(i)-(vi) DISCLOSURE SET — the classes this lane does NOT fix

**(i) The 2026-10-18 zero-execution mint gap is REAL and DIFFERENT — NOT fixed here.** A zero-execution child mints no `completion_report`, no `PROCESS_REPORT` task, no `report_injection` obligation (mint only on actual completion — the transactional-outbox **mint** at `child_reports.py:4772-4800`, not `session.commit()`); both recovery lanes **require** an obligation row or an inbound message ⇒ compensating revival is structurally unreachable for zero-execution children. **NOT fixed** (cn `6a435fe7`).

**(ii) The queued-report-stuck-on-parked-turn class DOES NOT EXIST fleet-wide tonight and is NOT fixed.** `completion_report` messages ready=1 / completed=108; the single ready row is a fresh healthy triple; message-only-no-injection 0; injection DEFERRED 0.

**(iii) The class-(i) count is tonight's LIVE population, not an all-time total.** `dependency_watchers` holds only current state (11,858 FIRED / 256 CANCELLED) — **no archived non-fire history**.

**(iv) THE ERROR-LANE DOOR — UNDER-DISCLOSED UNTIL NOW, AND LOAD-BEARING (item 15).** The ERROR lane emits F1+F2 only (`error_reporting.py:612`/`:654`); it emits **NO F3-class fire**. Therefore **a forever-PENDING watcher on an erroring child holds the lane-7 mission HOLD** — **both** guards (`report_delivery_recovery.py:2779` *and* `:2857`, each `if watchers: … return`) — which blocks **ALL commission abandonment INCLUDING the TTL backstop** ⇒ the mission gate (`child_reports.py:2691-2695`) **refuses root completion forever**. **That is strictly worse than tonight's wedge.** ⇒ **DECISION ATTACHED: branch (A) — the ERROR lane is FOLDED INTO S1** (§A3), so this disclosure describes the **pre-release** window only. **Post-release, DOOR-1 is closed by code**; the residual is the **backlog of rows that accumulated while it was open** (see (v)).

**(v) THE BACKLOG ROWS — named deciding evidence + a revisit trigger tied to the ERROR door (item 15).** The backlog is **not static**: it is **fed by DOOR-1** (every error-lane child with a nudge-armed watcher adds a row until the release lands).
- **The one-query deciding evidence (gather during implementation):** `SELECT count(*) FROM dependency_watchers w JOIN task t ON t.id::text = w.source_task_id WHERE w.state='PENDING' AND t.status NOT IN ('running','pending','paused')` — the population that S1 will make non-recurring but will **not** retroactively fire.
- **Revisit trigger:** *any PENDING watcher older than 2× the orphan-sweep interval whose `source_task_id` is terminal* ⇒ open the backlog commission.
- **Owner:** next commission (or the ceremony bundle's remainder path).

(vii) **IMPLEMENTATION-REVIEW DEFERRED REGISTER (advisories carried by the reviewer; each is a NAMED follow-up, NOT this lane's work).**

| # | Advisory | Why deferred | Owner |
|---|---|---|---|
| **D4** | **Kill-switch tier routing** — the per-tick read consults only the **DB-override** tier while the spec declares `env_var` + `config_path`; the guideline documents the DB route (**doc honest**) | the fix is a tier-resolution change in `settings_service`; the shipped behaviour is correct for the shipped doc | follow-up |
| **Res5** | **Blocking sync read on the event loop** (the per-tick qualified read) | hourly tick, bounded read; the fix would need an async path | follow-up |
| **Res7** | **Marker read-modify-write lost-update window** | narrow; the sibling guarded UPDATE already carries exactly-once for the state transition | follow-up |
| **Res8** | **Full-table PENDING seq-scan on PG** — expression index proposed | perf, not correctness; **the tester EXPLAINs it** and the index lands in its own lane | follow-up lane |
| **Res9** | **Malformed-row aborts the remaining fire loop** (`dependency_bus.py:2005`) | fail-soft hardening, not a park defect | follow-up |
| — | **Error-lane fallback lacks the S1 fire** (`error_reporting.py` ≈`:708`) | **low probability** (the fallback fires only when `_child_reports_service` is unwired) | follow-up |
| — | **`TERMINAL_INSTANCE_STATUSES` name shadowing** | cosmetic; no behaviour change | follow-up |
| — | **Missing direct gate-leg test** | T3b covers the chain; a unit-level gate-leg test is additive | follow-up |
| — | **`dependency_bus.py` module split (>3200 lines)** | structural; no behaviour change | follow-up |
| — | **Marker-aware Pass-1 filter** (closes the D-2 duplicate window) | `instance_lifecycle.py` is an **AVOID** file (stale-fork contention, §(i) row 9) | **named follow-up, explicitly NOT this lane** |

**NEXT-LANE REGISTER (reviewer precision — VERBATIM framings; fold round 4):**

| # | Item | Verbatim framing | Status |
|---|---|---|---|
| **F6** | `_s4_notice_rows` filters on the `wedge_notice` **MARKER only**, not `source == WEDGE_SOURCE and instance_id == parent` ⇒ it is a **GLOBAL count, not a parent-scoped one** | as stated | **next lane** |
| **F7** | **drop or comment the INERT seeds** — the seeded `s4-blip-classic-dead` with `parent_id=None`, **unreferenced** | as stated | **next lane** |
| **F8** | **latent NameError**: hoist `subjects` / `subjects_read_failed` **ABOVE** the `try` so a raise in `sorted(set(...))` cannot leave the name unbound | as stated | **IN-FLIGHT** — the code lane is folding it into the same pre-bump commit |
| **F10** | **PACKS.md `:3601` `Last Run` date refresh** | as stated | **next lane** |
| **Fold-in #5** | the withdrawn-constant note should read **"imports"**, not **"references"** (there are **no references** outside its definition) | as stated | **next lane** |
| **F-4 stale orphan-sweep prose** | `test_06f500af…:421-430/:493` claims *"Phase 1 `_sweep_orphan_watchers()` not implemented yet"*; it **IS implemented at `dependency_bus.py:3061`** (the tester's `:2314` was a **STALE anchor**), grace-gated by `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS = 30` (`:208`) ⇒ the **quarantined red is deterministic and base-attributable**; correct **`QUARANTINE.md:50`'s attribution** (the `:181` row is already correct) | as stated | **next lane** |
| **F-2 PG refusal-guard node** | **no PG node behaviorally pins the S1 watch-arming refusal guard** (static census only: exactly **2** production arm sites, both stamping `child_id`; the guard itself is verified **correct** at `dependency_bus.py:725-740`) ⇒ **coverage gap ≠ defect**; **top follow-up test**, cheap now that PG runs | as stated | **top follow-up test** |
| **`TestWedgeRearmAfterCooldown`** | `tests/unit/services/test_waiting_children_watchdog.py:1597` — the class **survives** while the constant it was **NAMED for is withdrawn**; whether its body still asserts withdrawn TTL semantics is **UNVERIFIED** ⇒ a **2-minute look** | as stated | **next lane (2-minute look)** |

**THE RESIDUAL ADJUDICATION — VERBATIM (reviewer ruling, fold round 5; acceptable-as-disclosed for staging, carried on the register):**

> **Ruling: acceptable-as-disclosed for staging; carried on the register — and the registered fix is NOT the proposed one-liner.**
>
> The harm class is **duplicate noise on a TRUE notice**, slightly worse than first disclosed: the **narrowed fingerprint is STAMPED**, so a blip tick emits **one narrowed notice AND the first healthy tick re-misses dedupe — TWO extra notices per blip, not one**. It is **materially milder than the R1 class**: the parent **IS genuinely wedged** (the notice **omits a reason code**; it **asserts nothing false**), the **end-evaluation — the state machine — is protected**, and it **self-heals**. Consistent with the lane's every ruling (**false notices and silencing BLOCK; bounded noise on true notices REGISTERS**), it does **not gate the release**.
>
> **The registered fix = the FINGERPRINT-FALLBACK, not the `continue` one-liner.** The one-liner (skip the parent when `_queue_read_ok` is False) is fence-clean and kills both extra notices, but under a **PERSISTENT queue-read failure** the parent would receive **NO wedge notice through that arm at all** — a **suppression window**, adjacent to the silence-class harm this lane exists to close. The **fallback shape**: on a queue-read failure, fall back to the parent's **currently STAMPED code set** for the fingerprint — the map stays stable, dedupe hits (no notice during the blip, none after recovery), **no continue, no suppression**, composes with the **"postpone, never decide" doctrine**.
>
> **Required pins when it lands:** **(test-1)** already-notified in-scan-set parent + queue blip ⇒ **ZERO new notice rows AND episode map unchanged**; counter-mutation = revert to narrowed recompute ⇒ the **two extra rows appear**, test REDs. **(test-2)** the fresh-parent-during-blip case asserted explicitly. **(test-3)** the **F-2 initializer flip in the SAME commit**, with one fence note.

**(vi) The periodic orphan-watcher sweep is PERMANENTLY INERT — disclosed, NOT fixed.** `daemon/api.py:1919-1920` constructs `OrphanWatcherSweepService(dependency_bus=get_dependency_bus(), …)` **before** `init_dependency_bus` sets the singleton (`:2972` ⇒ `api.py:5710`) ⇒ the service holds `None` for life ⇒ it cancels nothing; its own docstring ("the structural backstop for the steady state") is false. Anomaly **`fcf89d88`** (cluster `d685a9ee`). **The plan does not fix it and does not rely on it.**

---

## L-CENSUS — THE WATCHER POPULATION, TIMESTAMPED (item 12)

**Use "total PENDING" and "structurally dead" as DIFFERENT quantities.**

| Time | Query | **Total PENDING (fleet)** | **Structurally dead (Ari root)** | **Legitimately pending** | Note |
|---|---|---|---|---|---|
| **22:55Z** | live census | **13** | **2** — `8a1f60d7`, `999efa71` | **10** | the 13th row was **unclassified** at census time; **arithmetic 2+10+0 = 12 ≠ 13** — the residual row is either (a) a 10th legitimate row counted twice or (b) an 11th legitimate row. **Do not repeat "2+10+0=13"**; state **13 total, of which 2 structurally dead and 10 confirmed legitimate, 1 unclassified** |
| **~22:52-22:53Z** | live accretion | **13 → 15 (see below)** | 2 | 10 + 2 new | `5602e91d` @22:51:59.886 and `c83e12a2` @22:53:05.940 — both keyed on source task **39564** on leader **82551f59**, mission `cb904c16` (which owns the two dead rows) ⇒ **legitimate ONLY while 82551f59 stays non-terminal** |
| **23:03:54Z** | **task 39564 COMPLETED** | 15 | **4** (all four now dead-or-about-to-be) | 10 | the two new watchers' source task is now terminal ⇒ they join the dead set **at the bus level**; the census at 22:55Z was **true when written and false now** (§(h.3)) |
| **ceremony time** | **RE-INSPECT** | re-read | re-read | re-read | the census must be re-read at the ceremony; the 23:03:54Z transition is not the last one that can move it |

**Caveat carried verbatim:** `dependency_watchers` holds only **current state** (11,858 FIRED / 256 CANCELLED) — **no archived non-fire history** ⇒ these are **live populations, never all-time totals**.

---

## 7. PHASES (inline, per the `plan-creation` skill's per-phase shape)

| Phase | Name | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| 1 | **S1 — fire at the terminal transition** | COMPLETED + TOOL-INVOCATION + **ERROR** lanes get the target-agnostic child-keyed fire, with a stated durability stance | **17** | tight with 3 (bus module) and 4 | pending |
| 2 | **S2 — nudge arm-time guard** | refuse the self-referential watcher at birth, with the sanctioned alternative named | 7 | independent of 1; tight with 3 | pending |
| 3 | **S3 — park-predicate reconciliation** | one qualified count; 6 switched readers incl. the REAL park door; 3 raw completion doors named | **14** | tight with 1 and 2 | pending |
| 4 | **S4 — notice surface** | observe-not-assert + episode dedupe + per-tick kill-switch + guidelines | **9** | tight with 3 | pending |
| 5 | **staged release** | bump → tag → stage → FE rebuild → `stage.sh`; **NO live promote** | 7 | independent | pending |

### Phase 1 — S1: fire at the terminal transition

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| **0** | **IMPLEMENTER PRE-FLIGHT** (item 19): re-measure `instance_lifecycle.py`, `instance_messaging.py`, `tools/instance.py`, `manager.py` at implementation time; content-match every `NOT RE-VERIFIED` anchor | — | a written pre-flight note listing any moved anchor; **no code change** |
| 0b | **C-1 assertion**: the nudge mint order is enqueue → commission → watcher with an early return on enqueue exception (`instance_ops.py:1545-1670`); a failed enqueue mints nothing | — | asserted in a test/docstring; a failed enqueue leaves **zero** watcher rows |
| 0c | **axis-1 check**: observer Step-2 bypass reachability (`:4517-4520`) | — | a written finding (does any path reach Step 2 without Step 1?) |
| 0d | **axis-1 check**: `enqueue_message` full-body dedup absence (signature only) | — | a written finding (confirm no internal dedup) |
| 1 | Add `fire_for_terminal_child(child_instance_id, outcome)` to `dependency_bus.py`, matcher = **`FIRE_SET(instance)`**, guarded `transition_state`, **no enqueue** | — | fires only PENDING rows with `w.follow_up_payload.metadata.child_id == child_instance_id`; `fired_at` set; the method contains **no** `enqueue_message` |
| 1b | **`watch()` REFUSES a missing `child_id`** (load-bearing: without it the matcher cannot key at all). **NO staleness exclusion and NO generation counter** (leader ruling, iteration 003 — both withdrawn; see §(a.6) bullet 2). Two nudgers on one child ⇒ **both fire**, by design | 1 | an arm without `child_id` mints nothing; **two nudgers on one child both reach `state=='FIRED'`** (**T1d**) |
| 2a | Implement the delivery: **dead-letter skip** → `enqueue_message` → **`ENRICH`** → stamp → **`finally` re-purge** (the `instance_lifecycle.py:412-540` order, minus the terminate-only DOWN-side drain) | 1 | the order matches the §(a.3) table; the re-purge runs in a `finally` |
| 2b | **`source_task_id` enrichment** (`ENRICH(w)`) before the stamp — the nudge payload lacks it (`instance_ops.py:713-725`) | 2a | the stamp keys correctly on a nudge-armed row |
| 2c | **`DELIVERY-AT-LEAST-ONCE(w)`**: the distinct `delivered_watch_id` marker, **committed BEFORE the enqueue in its own transaction** (a same-transaction write is unachievable — `enqueue_message` owns its own session — and altering it is forbidden by fence r4). **The contract is at-least-once: a crash in the enqueue-commit→stamp gap may re-enqueue on resume Pass 1 (`instance_lifecycle.py:6105-6112`, no marker consultation) — one redundant MESSAGE, never a second park** | 2a,2b | **`DELIVERED_MARKER`** asserted as a ROW STATE (N6); the duplicate window is **asserted as accepted**, not as prevented |
| 2d | **Pass 1 soundness**: document that the plan does **not** depend on `enqueued_at` (semantics split across four write sites); state the accepted **AT-LEAST-ONCE bounded-duplicate residual** | 2c | the durability stance appears verbatim in the module docstring |
| 3a | **COMPLETED + TOOL-INVOCATION hook, PER-OUTCOME (approver B1)**: `child_reports._dispatch_post_commit_side_effects` (`:5285`) — fire placed at the END of **each** terminal branch: `regular_child_completed` before its `return` at **`:6464`** (after the pair-emit `:6256`); `tool_invocation_completed` before **`:6192`**; `root_completed` before **`:6014` ONLY** (NOT `:5622`/`:5694`, which demote a non-terminal instance); with the **`DELIVER(w)`** per-row **delivery filter** | 1,2a | **T1b**: a structural parent receives **no** bus FollowUp on the healthy path; **T1c**: a tool-invocation child fires; **T1f**: a **root child's** nudge-armed watcher fires (the branch emits no bus terminal of its own — producer stamps `parent_id=None` `:4060-4069`) |
| 3b | **ERROR hook**: `error_reporting._send_error_report` (`:439`), beside `:612`/`:654`, before the fallback at `:708` | 1,2a | a nudge-armed watcher on an erroring child is FIRED |
| 3c | **D-1 — the gate leg (LANDED)**: `_root_completion_gate`'s bus leg now calls `_bus_count_fulfillable_pending_for_target_sync` (**`:2972`**) — a **FAIL-OPEN WRAPPER OUTSIDE the gate's session** (`:399-421`), **deliberately not R4's in-transaction shape** (a wrapper cannot wedge the completion path on a read error; R4 stays in-tx inside a worker-thread session) | 1,2a | **T3b** drives the full gate chain (pass + status + no downgrade commit) for a root whose only PENDING watchers are unfulfillable |
| 4 | Prove the **exactly-once fire** against the healthy path (F1 first ⇒ rowcount 0) | 1,3a | cited in the code comment (`child_reports.py:5415-5440` pattern) |
| 5 | Prove the **no-enqueue** contract in the bus module | 1 | `dependency_bus.py` gains no `enqueue_message` |
| 6 | **T1, T1b, T1c, T1d, T1e, T1f** | 1-5 | all six row-state assertions green |
| 7 | **T7** (fence) | 1-5 | spawn-lane F1/F2 unchanged; supplementary fire a no-op; exactly one delivery |
| 8 | Register | 6,7 | cases live in `tests/unit/test_reconciler_wedge_fix.py` ⇒ covered **by name** by `wedge_fix_suites_unit_test`; **no new pack** |

**Coupling:** tight with Phase 3 (same bus module, same `FULFILLABLE`/`FIRE_SET` vocabulary); tight with Phase 4. **Risks:** (i) *S1-at-top pre-empts the pair-emit* — impact High, mitigation: task 3a's **per-outcome placement** + `DELIVER(w)` + T1b; (ii) *duplicate delivery in the crash window* — impact High, mitigation: `DELIVERY-AT-LEAST-ONCE` + the documented bounded-duplicate residual (one redundant message, second turn no-op); (iii) *the matcher fires stale episodes* — impact High, mitigation: task 1b (`watch()` refuses a missing `child_id`, load-bearing) + T1d (the plain matcher's semantics — both waiters wake; the stale row fires once). **The `source_task_id`-terminal discriminator is WITHDRAWN** (iteration 003 leader ruling — it would drop the live row `8a1f60d7`); do not re-introduce it | (iv) *the child_reports reconciliation cost* — impact Medium, mitigation: cn `888d612d` land-first, and the fork is stale so there is no live conflict.
**Exit criterion:** T1, T1b, T1c, T1d, T1e, **T1f**, T7 green in the registered pack; the exactly-once / no-enqueue / durability contracts cited in code.

### Phase 2 — S2: the nudge arm-time guard

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add the `self-surgery-refused` refusal to the nudge act body (`instance_ops.py:1495-1589`), after `_resolve_target`, **before** the mint at `:1642-1652` | — | the sibling token/shape (`daemon/tools/instance_ops.py:894/:1332/:1768`); the dry-run preview refuses identically |
| 1a | **Refusal text names the sanctioned alternative** (`send_message` to yourself — tree-guarded, no watcher) + the optional `instance_nudge`-on-self warning | 1 | the text matches §(b.3) |
| 1b | **Flag the playbook-anchor discrepancy** (`watchdog.py:262-270` vs `:436-438`) for the implementer/reviewer | — | a written finding in the phase note |
| 2 | Confirm the guard is **NOT** a tree guard | 1 | a non-child nudge still mints + still delivers — **asserted** |
| 2a | **C-1 ordering assertion** (mint only after a successful enqueue) | 1 | a failed enqueue mints nothing |
| 3 | **T2** + **T2b** | 1,2 | no PENDING self row; the non-child path asserted |
| 4 | Register | 3 | same registered pack **by name** |

**Exit criterion:** T2 + T2b green; the refusal names the alternative; the playbook discrepancy recorded.

### Phase 3 — S3: park-predicate reconciliation

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add `count_fulfillable_pending_for_target(parent)` implementing **`FULFILLABLE(w)`** | — | **no new column/table/migration** |
| 2 | Liveness via **`get_statuses` (`repository.py:1071`)**, **batched** — **NOT** the parent-linkage-keyed `:3838-3916` | 1 | a **nudge-armed, non-tree-linked** child is correctly seen |
| 2a | **FAIL-CLOSED posture**: missing `child_id` / missing instance row / ghost / status-read error ⇒ **FULFILLABLE**; only the self-referential clause excludes | 1,2 | each case unit-tested |
| 3 | **R1** — park predicate (`message_processing_pipeline.py:1329`) → `PARK(parent)` | 1-2a | an unfulfillable-only parent does not park |
| 4 | **R2** — `child_reports.py:3511` | 1-2a | the defer door closes |
| 4a | **R3** — `child_reports.py:3578` (`all_children_done = … == 0`) | 1-2a | the second defer door closes |
| 4b | **R4 — THE REAL PARK DOOR (approver B5)**: `child_reports.py:3664-3698` — the inline `SELECT count(*) FROM dependency_watchers … AND state='PENDING'` that writes `WAITING_CHILDREN` **and commits** (`:3688-3691`). **`:3379-3437` is DROPPED — it is a CHILD-LIVENESS door (counts child-instance rows `:3397-3410`), deliberately unchanged** | 1-2a | the third raw-count PARK door closes; the root flow can no longer park on an unfulfillable watcher |
| 4c | **Sync twin + in-transaction form (N7 folded)**: `count_fulfillable_pending_for_target_sync` + the in-transaction SQL shape R4 needs (R2/R3/R4 run in a worker thread, inside the helper's session) | 1-2a | R2/R3/R4 all read the qualified count inside their existing transaction; no extra session |
| 5 | **R5/R6** — `mission_strand_healer.py:562`, `:1240` | 1-2a | the healer is no longer blocked by an unfulfillable watcher |
| 6 | Document the **RAW boundary** with the **nine** sync readers named | 1 | §(c.3) matches the code |
| 6a | **C-2 (OPEN)**: does the healer's **drive-side in-session** bus gate read the RAW count? If raw ⇒ a **sixth** switched reader | — | a written finding + the switch if required |
| 6b | Name the **three unswitched completion doors** (D-2/D-3/D-4) **+ the child-liveness door** (`:3379-3437`, deliberately unchanged) in the phase note for the reviewer | 6 | the reviewer can audit each |
| 7 | **T3** — must **not** pass vacuously (the fixture uses a nudge-armed, non-tree-linked watcher) | 3-5 | `instances.status` is not `WAITING_CHILDREN` |
| 8 | Register + deliberate census update if a notify site was added | 7 | `test_a5_wedge_notify_work.py` updated deliberately, if applicable |

**Exit criterion:** T3 green (non-vacuously); the raw boundary documented; the four doors named; C-2 answered.

### Phase 4 — S4: the notice surface

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add the watchdog's **first** bus read; subject = **`S4_NOTICE_SUBJECT(parent)` — the UNION** (qualified-unfulfillable set **∪** the classic-wedge arm: zero watchers ∧ no non-terminal children; **NOT** raw PENDING) | Phase 3 | a legitimately-parked parent is **out of scope** (the 9× guard); a **zero-watcher** parent is **in scope** (the classic wedge) — both arms asserted |
| 2 | Implement the four reason codes with a row check each | 1 | each code exercised |
| 3 | Replace the notice text; **the `:433` report claim is re-derived at notice time from the row check, or DROPPED** — never emitted from the template; add the "N pending dependency watchers that can no longer fire" vocabulary | 2 | T4 asserts content |
| 4 | **Episode dedupe**: `DEDUPE_KEY = (parent_id, reason_code_set)`; **TTL dropped entirely**; episode end = the **`S4_NOTICE_SUBJECT(parent)` row-check being EMPTY for that `reason_code`**. The **THREE REAL pop sites** are `waiting_children_watchdog.py:1607` / `:1617` / `:1746` — **`pop` is RE-BASED off WC-membership at `:1746`**, because the notice's own enqueue flips WC→RUNNING (`:1643-1645`); the WC→RUNNING→WC flip does **not** open a new episode. **(`:1460` is a log-string tail and `:1466` a comment header — NOT pop sites.)** | 2 | T5 |
| 5 | **Kill-switch**: register `wedge_notice_emission_enabled` in `settings_service` (`requires_restart=False` — the **7th** such key); **ADD the per-tick read** (precedent `metrics_gate.py:131`; `get_overrides` `:1111-1116`) — the key alone is VOID because `_enabled` is boot-time (`:536`) | 2 | a mid-run flip takes effect without a restart |
| 6 | Write the **`docs/guidelines/`** file (scope, default, "hides the symptom, leaves the defect live") | 5 | committed with the fix |
| 7 | **T4** (incl. the no-notice-for-fulfillable assertion) | 3,1 | row-level content assertions |
| 8 | **T5** (episode semantics incl. the flip) | 4 | one notice per episode |
| 9 | **T6** (`SWITCH_OFF` semantics + per-tick) | 5 | **fails** if the switch reaches the healing path |

**Exit criterion:** T4, T5, T6 green; the guidelines file committed; `:433` no longer emittable from the template.

### Phase 5 — staged release (NO live promote)

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 0 | **Phase-1 pack wake-assertion coverage check** (axis-1) | — | a written finding: does the Phase-1 pack assert the wake (not just the row state)? |
| 0a | **Phase-5 release re-confirmed (iteration-003 fold):** the **staged-release packaging is STILL OWED** — bump commit → annotated tag → stage wt detached at the tag → FE rebuild → `stage.sh` (r7); the version slot is **DERIVED AT BUMPTIME, never reserved** (cn `84e24343`); **NO live promote** | — | the release section states exactly this |
| 1 | Derive the **version slot at bumptime** (cn `84e24343`) | **Phases 1-4** | unique, derived from the tree at bump time |
| 2 | Bump commit → annotated tag | 1 | annotated tag; clean tree |
| 3 | Stage the worktree **detached at the tag** | 2 | at the tag commit |
| 4 | FE rebuild (if required) | 3 | the bundle matches the tag |
| 5 | `stage.sh` | 4 | staged release produced + 4-way sha verified |
| 6 | **NO live promote** | — | the promote is the Emperor's ceremony |

**Exit criterion:** a staged, verified release; the live install untouched; the §(h) runbook line delivered.

---

## 8. COUPLING MAP

| | Phase 1 (S1) | Phase 2 (S2) | Phase 3 (S3) | Phase 4 (S4) | Phase 5 |
|---|---|---|---|---|---|
| **Phase 1** | — | independent | **tight** (same bus module; `FIRE_SET` vs `FULFILLABLE` share `child_id` semantics) | **tight** (the watchdog reads the bus S1 writes) | independent |
| **Phase 2** | independent | — | **tight** (both qualify the same population) | loose | independent |
| **Phase 3** | tight | tight | — | **tight** (`S4_NOTICE_SUBJECT` is built on `FULFILLABLE`) | independent |
| **Phase 4** | tight | loose | tight | — | independent |
| **Phase 5** | independent | independent | independent | independent | — |

---

## 9. RISKS

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **S1-at-top pre-empts the pair-emit** ⇒ a synthetic bus message to the structural parent on every healthy completion | **High** | **Medium** | **task 3a's PER-OUTCOME placement** (regular→before `:6464`; tool→before `:6192`; root→before `:6014` ONLY) + `DELIVER(w)` + T1b + **T1f** — this is the most dangerous branch in the design |
| 2 | **Duplicate delivery** in the crash-after-enqueue window (Pass 1 re-enqueues with no paused filter, `instance_lifecycle.py:6103-6116`) | **High** | Medium | **THE ACCEPTED CONTRACT (at-least-once)** — `DELIVERY-AT-LEAST-ONCE` marker is pre-committed; the duplicate is one redundant MESSAGE whose second turn is a no-op. Marker-aware Pass-1 filter = named follow-up (AVOID file) |
| 3 | **Lost delivery** in the crash-before-enqueue window (`_recover_fired_unsent` only lists) | Medium | Medium | accepted + documented: a `FIRED` row **never holds the park** (S3 does not count it) ⇒ loses a notification, never causes a permanent park |
| 4 | **Spurious wake from a stale-but-PENDING row** (child_id alone across every target) | **Low** (accepted) | **Medium** | **BY DESIGN, not engineered around** (leader ruling, iteration 003): both nudgers are genuine waiters; a stale row fires **once** and leaves PENDING. `watch()` still refuses a missing `child_id` (load-bearing); T1d asserts both waiters wake; **disclosed at risk 21** |
| 4b | **Crash-window DUPLICATE delivery** (marker set + `enqueued_at` NULL ⇒ resume Pass 1 re-enqueues; **at-least-once, never at-most-once**) | **High** (disclosed) | **Medium** | **Compensating controls (D-2 INVERTED, verified at `077a41a33`):** the duplicate is a **MESSAGE, not a park** — the parent wakes twice and the **second turn is a no-op**; the lost-wake window is **covered** (Pass 1 delivers once); S3 keeps the park released; S4's union notice is the visibility backstop. **No re-enqueue path added; the AVOID on the Pass-1 file is NOT lifted.** **Marker-aware Pass-1 filter = NAMED FOLLOW-UP** (`instance_lifecycle.py` is an AVOID file) |
| 5 | **S3 passes vacuously** if anchored on the parent-linkage-keyed read | **High** | **Medium** (it was the plan's own defect) | **`get_statuses` (`:1071`) by id, batched** + T3's nudge-armed non-tree-linked fixture |
| 6 | **Fail-open qualification** releases a park while a child is in flight ⇒ re-dispatch / double-run | **High** | Low | **FAIL-CLOSED**: missing child_id / missing row / ghost / read-error ⇒ FULFILLABLE |
| 7 | The **ERROR door** stays open until the release lands and feeds the backlog | **High** | **High (certain until promote)** | branch (A) closes it in the staged release; §A5 names the door, its owner and the revisit trigger |
| 8 | The **child_reports.py** reconciliation costs wall-clock | Medium | Medium | the fork is **stale** (no live conflict); cn `888d612d` land-first; footprint limited to 4 hunk groups |
| 9 | The plan **relies on the orphan sweep** by accident (inert) | **High** | Low | §(e) states non-reliance; no shape uses it; §(j)(vi) discloses |
| 10 | The **group-chat room machinery (LIVE)** regresses | High | Low | S1-S4 live **outside** `manager.enqueue_message`'s internals; r4 check in review |
| 11 | The **report-injection substrate** regresses | High | Low | S1 never writes `report_injections` |
| 12 | The S4 switch is wired into the **healing path** | **High** | Medium | T6 exists to fail in that case |
| 13 | The S4 switch is registered but **never read per tick** (the VOID failure) | Medium | **High** (it was the plan's own defect) | task 5 requires the per-tick read code; T6 asserts a mid-run flip |
| 14 | S4's population regresses to **raw PENDING** ⇒ ~9× notice volume | Medium | Medium | `S4_NOTICE_SUBJECT`; T4's no-notice-for-fulfillable assertion |
| 15 | The **episode dedupe** pops on the WC→RUNNING→WC flip ⇒ hourly noise returns | Medium | Medium | pops only on genuine condition-false; T5 asserts the flip does **not** open an episode |
| 16 | The `:433` false report claim survives in the template | Medium | **High** (it was 100% false) | task 3: re-derive or drop; T4 |
| 17 | The census is **stale** by ceremony time (39564 completed 23:03:54Z) | Medium | **High** | §(h.3) requires a **ceremony-time re-inspect** |
| 18 | The repair bundle's **TTL clocks** (02:30Z / 04:52-53Z) invalidate it | Medium | **High** | remainder-only re-emission is the artifact's designed path |
| 19 | A stale anchor becomes a false code comment | Low | Medium | Phase 1 task 0 pre-flight + register `NOT RE-VERIFIED` flags |
| 20 | `test_a5_wedge_notify_work.py`'s census reddens silently | Low | Medium | deliberate census update; register the file or route the assertion |
| **21** | **A stale-but-PENDING watcher delivers ONE spurious wake** (the accepted consequence of the plain `FIRE_SET` — leader ruling, iteration 003) | **Low** | **Medium** | **DISCLOSED, not engineered around.** A stale row fires **once** and leaves PENDING (the guarded `WHERE state='PENDING'` UPDATE makes every later fire a no-op), so the spurious wake is **bounded and self-limiting**. The alternative — excluding it by `source_task_id`-terminal — would drop **exactly the rows the fix exists to fire** (live row `8a1f60d7`: source task terminal 18:08Z, child terminal 19:04Z). Backlog rows are excluded from the **park** by S3's `FULFILLABLE(w)` (instance-status keyed), not by narrowing the fire. T1d asserts the semantics |
| **22** | **The defect family is LIVE and not theoretical — it recurred during the approval process itself** | **High** | **Confirmed occurrence** | **Anomaly `1be5734c` (cluster `51b544f3`)**: the approver's iteration-002 cures worker's report arrived as an **EMPTY PARENT WAKE** and the revive-enqueue re-delivery was **never consumed** (1 h+ idle; recovered by transcript reads). This is the **same class** this lane repairs (a waiter that is never woken / a delivery with no carrier) — it is the lane's **urgency evidence** and the reason the §(h) repair sequence should not be deferred past the ceremony window |

---

## 10. SUCCESS CRITERIA

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | A nudge-armed watcher is FIRED when its watched child reaches a terminal state | T1 | `W.state=='FIRED'` ∧ `W.fired_at is not None` ∧ `count_pending_for_target(P)==0` |
| 2 | **The structural parent receives NO bus FollowUp on the healthy path** | T1b | zero bus-sourced `message_queue` rows to the structural parent |
| 3 | The **tool-invocation**, **ERROR** and **ROOT-CHILD** lanes fire too | T1c, T1e, **T1f** | `W.state=='FIRED'` on all three |
| 4 | **Plain-matcher wake semantics: every matching waiter fires; a stale-but-PENDING row fires exactly once then leaves PENDING** | T1d | **T1d asserts the plain matcher's semantics**: two nudgers on the same child **both** reach `state=='FIRED'` (two waiters, two wakes); a **stale-but-PENDING** row fires **exactly once** and then leaves PENDING; a second nudger is **not** suppressed by the first's episode. *(The prior wording — "only the live episode's watcher FIRED" — stated the OPPOSITE of the ledger and of T1d and is corrected.)* |
| 5 | A self-referential watcher is never minted; a non-child nudge still works | T2, T2b | zero PENDING self rows; the non-child path delivers |
| 6 | A parent whose only waits are unfulfillable does **not** remain parked — incl. the **emission-time gate** (D-1, `:2637`) | T3, **T3b** | `instances.status != 'WAITING_CHILDREN'` AND `_root_completion_gate(None, instance_id)` returns pass (first element True) AND no downgrade commit occurred |
| 7 | The notice never asserts an unverified queue state, and a legitimately-parked parent gets **no** notice | T4 | no claim without a cited row; **zero** notices for fulfillable-only parents |
| 8 | **One notice per `(parent, reason_code)` EPISODE** | T5 | exactly 1 per episode; the WC→RUNNING→WC flip opens **no** new episode |
| 9 | The kill-switch gates emission **only**, and is **read per tick** | T6 | `SWITCH_OFF` ⇒ 0 new notice rows while detection/classification/healing still run; a mid-run flip takes effect |
| 10 | The healthy path is observably unchanged | T7 | spawn-lane F1/F2 as today; supplementary fire a no-op; exactly one delivery |
| 11 | No new table, no migration, no new column | the migration census at the tag | **133 `.sql`** files, unchanged |
| 12 | The new bus capability never enqueues | code check | zero `enqueue_message` in `dependency_bus.py` |
| 13 | Delivery is **AT-LEAST-ONCE with a bounded duplicate window** — asserted as a **ROW STATE** (N6) | `delivered_watch_id` marker row + docstring | the marker row is present and keyed to the watch_id **before** the `message_queue` row exists (pre-commit ordering); **a crash in the enqueue-commit → stamp gap MAY re-enqueue on resume Pass 1 (the duplicate window, by design)**; the duplicate is **one redundant message**, never a second park; the stance text appears verbatim |
| 14 | The **3** unswitched completion doors are named and audited (+ the child-liveness door deliberately unchanged) | review report | each named with its line |
| 15 | The staged release exists and the live install is untouched | release sidecars + live `state.json` | 4-way sha; live `current` unchanged |
| 16 | The runbook is honestly classified and the census re-inspected | §(h) | repair = ceremony-class; restart = near-curative for the park / non-curative for the report; re-inspect required |
| 17 | Every unfixed class is disclosed, with the closure claim **scoped** | §(j)(i)-(vi) + §A5 | 6 disclosures + DOOR-1 named with an owner and a revisit trigger |

---

## 11. RESEARCH INSIGHTS (what shaped this plan)

1. **The bus has THREE fire primitives; the terminal lanes expose FEWER than three.** Measured coverage: TERMINATE 1-of-1 (F3-class); COMPLETED F1+F2 but **no F3-class**; ERROR F1+F2 but **no F3-class**; TOOL-INVOCATION **none at all**. The defect surface is that table.
2. **The nudge lane is the only arm family that can produce the broken shape** — the spawn lane is tree-guarded (`tools/instance.py:1273-1275`); the nudge lane has **no tree guard and no self guard** and resolves `source_task_id` lane-dependently (`instance_ops.py:673-694`).
3. **The child id is already in the payload JSONB** (`repository.py:352-365`) — the non-fire is a **match-predicate** problem, not a missing link.
4. **The park and the guard read two different populations** (watchers vs child instances) ⇒ the SILENT/NOTICE decision and the park decision can disagree ⇒ the class stays invisible.
5. **The bus is a state machine** (`child_reports.py:412-417`; echoed in the ERROR fallback) ⇒ the new fire returns FollowUps; delivery belongs to the caller.
6. **The ERRORS lane has its own emitter** (`error_reporting.py:439`) — which is why C-3 is negative and why the ERROR hook is **cheaper** than the COMPLETED hook (uncontended vs contended).
7. **The orphan sweep is permanently inert** — a wiring-order defect (`api.py:1919-1920` vs `:2972`), anomaly `fcf89d88`. It answers the restart question: the boot sweep would cancel stale-source watchers, but CANCEL ≠ FIRE.
8. **The healer's leg (1) refuses on `bus_pending > 0`** ⇒ a mid-session resolver must be integrated there, not added beside it (r6).
9. **`_finalize_instance` is DEAD as a hook** — zero production callers (its own code says so at `:3645-3650`), no bus seam. The observer framing is withdrawn; the COMPLETED hook is in `child_reports.py`, the most contended file in the lane. **"Entirely uncontended" is withdrawn.**
10. **`watch_reconcile_sweep` is not a bus caller** (zero `dependency_bus` hits) — a false premise removed.
11. **PACKS.md was readable to the plan author** — every new case lands in an already-registered file (cited **by name**), so no new pack machinery is needed.
12. **LESSON (fold round 4 — generalisable form): "mutation-proven is NOT the same as self-guarding."** An **inert monkeypatch** — or a `get_dependency_bus()` returning **None**, which yields `subjects={}` with **NO exception** — can leave a leg **green** while the **mutation is live**. This is **the same defect class this lane drove out of the earlier tests**, and it **recurs unless the legs assert their own injection** (hence the F1-F3 call-counters, §(f.1)). *(Carried in the evidence register's next-lane/deferred material as well.)*
13. **LESSON (R1 adjudication, iteration 003 — generalisable form):** the pre-delta purge was **fail-safe by construction** — a missing value compared False (`subjects.get(p,(0,[]))[0] > 0` ⇒ `0 > 0` ⇒ never dropped). The B2 delta **inverted** that to fail-open by replacing the implicit default with an explicit read. **Generalisable form:** *"a rewrite that replaces a fail-safe-by-construction default (a missing value that compares False) with an explicit read can silently invert the failure posture — the cure introduced the hazard it was meant to fix."* Record it with the lane's lessons and grep for it before any future 'rewrite the purge' pass. (The reviewer's provenance finding: the B2 delta did the inversion; the R1 adjudication restored fail-closed — read failure ⇒ UNKNOWN, never "stopped holding".)

---

## PHASE-B RESIDUAL QUESTION LIST

| # | Question | Answerable by | Consequence if unanswered |
|---|---|---|---|
| **RQ-1** | Which lane armed `8a1f60d7` — injection or enqueue? | the `lane=%s` log (`instance_ops.py:733-741`) — **the 90-min window means the evidence is gone**; only future instrumentation can answer | narrative only; the fix is identical |
| **RQ-2** | Will the two new watchers (`5602e91d`, `c83e12a2`) flip to dead? | **live rows** — task **39564 completed 23:03:54Z**, so **they already have, at the bus level** (§L-CENSUS) | re-inspect at ceremony time |
| **RQ-3** | Did the healer ever log `bus_pending=2`? | healer log — **likely gone** | corroboration only |
| **RQ-4** | Was `999efa71` minted against an already-terminal source task? | live rows + arming log (likely gone) | S3's cure is load-bearing either way |
| **RQ-5** | Investigator map #2 (spawn-half) has not landed — an equivalent spawn-lane non-fire? | the map | the spawn lane is tree-guarded; **out of scope** |
| **RQ-6** | Every `NOT RE-VERIFIED` anchor must be content-matched before citation | implementer/reviewer at edit time | Phase 1 task 0 |
| **RQ-C2** | Does the healer's **drive-side in-session** bus gate read the RAW count? | **code read at implementation** (Phase 3 task 6a) | if raw ⇒ a **sixth** switched reader |
| **RQ-D** | The residual **completion-defer** doors (`child_reports.py:3702` D-2, `:3757` D-3, `:4434` D-4) — do they create a park surface of their own? | reviewer audit | **they cannot create or hold a park** (they produce completion defers, not WAITING_CHILDREN writes with commit — that was D-1/`:3664-3698`, now R4); named, not closed; a follow-on decision. **`:3379-3437` is withdrawn from this question entirely** — it is a child-liveness door, not a bus door |

**Already answered by measurement (do not re-open):** OQ1 (resolved by census); the orphan-sweep question (resolved from source — and it yielded a new defect); **C-3 (NEGATIVE — §A3)**; **C-1 (RESOLVED — enqueue → commission → watcher, early return on exception)**.

---

## 11x. NEXT-LANE REGISTER — **FINAL FORM** (reviewer's closing register, VERBATIM)

- **The residual fix** — the **fingerprint-fallback** (above) + the **three pins** (test-1 / test-2 / test-3).
- **F-2 — the F8 hoist caveat.** `subjects_read_failed = False` initializes **BEFORE** the read, so an exception between the hoist and the subject read feeds the end-evaluation `subjects={}` + `read_failed=False` ⇒ **the fail-closed guards don't engage** ⇒ a `pending_watchers` episode could **narrow/drop on a read that never happened** — a **fail-closed→fail-open flip on that path**, contradicting the code's own comment at `:1883-1886`. **Reachability effectively nil** (the sole raiser is `sorted()` over hashable string sets) — hence **register, not block**. Fix = **+1 line** (init `True`, clear after the successful fetch); the *"two lines"* claim becomes three with its own fence note.
- **F-1 — r3c's RV-1 tick-completion proof is VACUOUS** (the sibling's asserted row was minted in tick 1; in tick 2 its fingerprint is unchanged ⇒ dedupe hit ⇒ no new row — so the assert is satisfied by pre-existing state and cannot detect a tick that dies at the blipped parent). r3c retains its primary teeth (the carrier-guard reversion is double-covered). Fix: seed the sibling with a **fingerprint-changing event** so tick 2 mints a second row (`len == 2`), and make the proof **ORDER-INDEPENDENT** — `list_waiting_children_parents()` has **NO ORDER BY** (`repository :3838-3844`).
- **F-3** — r3c never asserts its **RV-1 premise** (the seeded queue row's visibility is proven in F5 but only **ASSUMED** in r3c) — add the harness assert or the discrimination silently vanishes if seeding drifts.
- **F-5** — the stale **`wlts-watchdog`** run stamp.
- **F-6 — THE MERGE HAZARD (material for the giter, NOW).** `origin/latest` has moved **156 commits** past the lane base (merge-base still `eca98397d`) and **`.agents/tester/PACKS.md` WAS TOUCHED UPSTREAM** (upstream rows still read `10+68 / 78/78`); the lane's refreshed rows (`:2321`/`:3601`, `47+68/115`) will **COLLIDE at merge** and must be **reconciled deliberately — the lane's rows SUPERSEDE**. The other two lane files are disjoint. **Verify latest hasn't moved again at merge time.**
- **F-4** — the anchor restatement (`:2041-2047`, `:986-990`, `:841-914` — the implementer's reported "moves" were **pre-hoist positions**; the **+6 shift from F8** was unaccounted).

## 12. OPEN QUESTIONS FOR THE CALLER

| # | Question | Why it needs the caller | What I would do under each answer |
|---|---|---|---|
| Q1 | **Should the three unswitched completion doors (D-2/D-3/D-4) be closed in this lane?** | they are completion defers, not parks; closing them widens the `child_reports.py` footprint that is already the lane's largest cost | **no** — S1's fire removes the row from PENDING, so even the raw count drops; the doors are named for the reviewer and deferred |
| Q2 | **Is the S4 kill-switch worth the API-surface cost** given it cannot quiet tonight's noise? | it is NEW CODE + per-tick read code + a guidelines obligation | **build it** — it is the only durable operator lever for a class that recurs; if declined, T6 is dropped and the guidelines file becomes a "no switch exists" note |
| Q3 | **Should the inert orphan-sweep wiring defect be a 5th shape?** | real, measured, small — but the shape set is FROZEN | **no** — disclosed at §(j)(vi) and filed as anomaly `fcf89d88` |
| Q4 | **Execute the 8-block repair at ceremony time, or wait for the ~04:53Z mission drain?** | the mission gate may release on its own | **the Emperor's call** — the plan states both clocks; the runbook's step 1 is advisory to the ceremony, not an action taken here |

---

## ATTESTATION — plan-body / ledger agreement (house rule `plan-body-ledger-agreement`)

**Verified by inspection at the end of this reconciliation pass, RE-ATTESTED after approver iteration 002, RE-ATTESTED AGAIN after the leader's adjudication of blocking 5 in iteration 003, RE-VERIFIED after the D-1/D-2 addendum, and RE-ATTESTED AGAINST THE LANDED CODE (implementation-review fold, tip `077a41a33`).** **Rows changed this fold: 7** (`DELIVERY-ONCE` → **`DELIVERY-AT-LEAST-ONCE`**, D-2 inversion), **8** (`S4_NOTICE_SUBJECT` → the **union**), **9** (`DEDUPE_KEY` → **`(parent_id, reason_code_set)`**). Also re-based in the body with no row-string change: **R7's shape** (fail-open wrapper, not sync-twin-in-session), **§(d.2)** (set form), **criterion #13**, **risk 4b**. All other rows unchanged. Changed rows this pass: **row 4** (plain `FIRE_SET`), **row 4a** (now a DELETION RECORD for the withdrawn `STALE_EPISODE`), **row 12** (`TERMINAL_SET` → `INSTANCE_TERMINAL_SET`), **row 13** (PARK coverage `R1-R4` / `R5-R6` incl. the new `:2637` gate leg), **row 2** (PARK cell aligned to `R1-R4` / `R5-R6`), **row 1** (`FULFILLABLE` byte-identical to the ledger cell), **row 3** (`RAW_PARK` extended to **seven** PARK readers incl. the gate leg). **No row declares a match against a string that is no longer in the ledger.** **Sweep result (re-verified): `STALE_EPISODE` = 7 total / 7 deletion-records / 0 live** across all three files; `TERMINAL_SET` (bare) = 3 — PO:74 (the CHANGES row for defect 1), PO:990 (this attestation header's own history sentence), PO:1004 (the row-12 rename record); **ZERO** in evidence-register.md and architecture-recommendation.md; `FIRE_SET` ledger ≡ attestation **byte-identical (True)**. **FINAL TIP RE-BASE (fold round 5): the implementation-state pins and this attestation header are re-based to the FINAL tip `077a41a33cee9af1614a7967822be1679b582f68`.** **Final commit census: 11 commits `89b2e2e28` → `077a41a33`** = S1–S4 + D-1/D-2 cures + fail-soft follow-up + fixture doubles + B1–B4 blockers + delta R1/R2/R3 + the pre-bump hardening (F4/r3d, F5, F1–F3 counters, RV-1 r3c rework, F7 seed deletions, F8 hoist, F10/#5 prose). **Final pack counts: `wedge_fix_suites_unit_test.sh` = 115 (47+68); watchdog 51; reconciler 47.** *(History rows keep their point-in-time pins by design; only live implementation-state pins and this header move.)* **Re-attested against tip `077a41a33`; the reviewer-blocker commit implements exactly the ratified semantics — honest at-least-once stance, episode-end on condition-false for all open episodes, the dead TTL test rewritten to episode semantics, fail-closed children gate, honest render — so no plan string changes; the plan's strings and the release tip agree.** **Fold round 4 (F9): the r3 fence claim was RESTATED in §(g.1) — failure/unknown OR CONTRADICTORY-READ branch; healthy-path guarantee is about OBSERVABLE OUTCOME, not a literal line-level claim; the reviewer's per-hunk classification is the fence evidence of record. This touches the §(g) fence TEXT only — NO ledger cell and NO attestation row changed, so rows 1-14 stand as re-attested in this fold unchanged.** Every predicate string that appears in a body section and in a ledger/trace table is drawn **verbatim** from §PREDICATE LEDGER; the **body wins** wherever the two could diverge. The strings checked character-for-character:

1. `FULFILLABLE(w)` = `NOT (w.follow_up_payload.metadata.child_id == w.target_instance_id) AND (w.follow_up_payload.metadata.child_id IS NULL OR instance-status-of(w.follow_up_payload.metadata.child_id) NOT IN INSTANCE_TERMINAL_SET)` — **BYTE-IDENTICAL to the ledger cell (verified by the sweep).** body §(c.2)/§(c.3) ≡ ledger ≡ §(d.1) `S4_NOTICE_SUBJECT` ≡ T3 ≡ success criterion #6. **RE-ATTESTED (iteration 003, D-1/D-2).**
2. `PARK(parent)` = `count_fulfillable_pending_for_target(parent) > 0` — body §(c.1)/§(c.3) R1-R6 ≡ ledger ≡ §(g)(ii) ≡ §(h.6) step 3. **MATCH.**
3. `RAW_PARK(parent)` = `count_pending_for_target(parent) > 0` — **RE-ATTESTED (N4)**: the ledger row now names all NINE consumers (observer ×5, job_recovery, child_reports:378+5 callers, error_reporting:263, manager:13852); body §(c.3) ≡ ledger ≡ §(j) table — **never** used for a park decision after S3. **MATCH.**
4. `FIRE_SET(instance)` = `{w in PENDING : w.follow_up_payload.metadata.child_id == instance.instance_id}` — **RE-ATTESTED WITH THE NEW PLAIN STRING (leader ruling, iteration 003)**: BOTH prior forms are **withdrawn** (the generation-narrowed form, and the `STALE_EPISODE`-narrowed form — the latter would drop the live row `8a1f60d7`, whose source task is terminal 56 min before its child). body §(a.2) step 1 ≡ ledger ≡ T1 ≡ T1d ≡ task 1b. **MATCH — byte-identical to the ledger cell (verified by the sweep).**
4a. **DELETION RECORD — `STALE_EPISODE(w)` is WITHDRAWN.** The predicate no longer exists anywhere in the plan (ledger, body, tasks, tests, attestation). **Rationale:** the row's source task is terminal **at fire time** (live row `8a1f60d7`: source task 39320 terminal 18:08Z, watched child terminal 19:04Z) so the exclusion would drop **exactly the row the fix exists to fire**; and the set values `terminated`/`error` are **not task-status values** (`TaskStatus` = `pending/running/paused/completed/failed/cancelled`) — a type confusion. **The terminal concept now lives ONLY in S3's `FULFILLABLE(w)`, keyed on the child's INSTANCE status.**
5. `DELIVER(w)` = `NOT (w.target_instance_id == structural_parent(w.follow_up_payload.metadata.child_id))` — **RE-ATTESTED (N3 + N8 batched parent read)**: **the DELIVER-equivalence clause in step 3 is DELETED (approver B1)** — `DELIVER` is a delivery filter only. body §(a.2) step 2 ≡ ledger ≡ §(g)(i) ≡ T1b. **MATCH.**
6. `ENRICH(w)` — body §(a.3) ≡ ledger ≡ Phase 1 task 2b. **MATCH.**
7. `DELIVERY-AT-LEAST-ONCE(w)` — **RE-ATTESTED (D-2 INVERSION, implementation review at `077a41a33`)**: the at-most-once string is **withdrawn** and the row renamed; body §(a.5) ≡ ledger ≡ Phase 1 task 2c ≡ success criterion #13. **MATCH against the new at-least-once string.**
8. `S4_NOTICE_SUBJECT(parent)` = `{w in PENDING of parent : NOT FULFILLABLE(w)} != ∅  OR  ({w in PENDING of parent} == ∅ AND the parent has no non-terminal children)` — **RE-ATTESTED (population AMENDED to the union)**: body §(d.1) ≡ ledger ≡ §SILENT ≡ T4 ≡ risk 14. **MATCH against the union string.**
9. `DEDUPE_KEY` = `(parent_id, reason_code_set)` — **RE-ATTESTED (scalar → set)**: body §(d.2) ≡ ledger ≡ T5 ≡ success criterion #8. **MATCH against the set-form string.**
10. `EPISODE` — body §(d.2) ≡ ledger ≡ T5 ≡ success criterion #8; the WC→RUNNING→WC flip is stated **not** to open a new episode in **all four** places. **MATCH.**
11. `SWITCH_OFF` — body §(d.3) ≡ ledger ≡ T6 ≡ success criterion #9; the "never gates" list is identical in all four. **MATCH.**
12. `INSTANCE_TERMINAL_SET` = `{completed, terminated, error, failed}` — **RENAMED (iteration 003)** from `TERMINAL_SET` to make the **instance-status type explicit**; body §(c.2) fail-closed table ≡ ledger ≡ `FULFILLABLE`. **RE-ATTESTED MATCH — the type is now unambiguous: `InstanceStatus` carries all four values; `TaskStatus` carries none of `terminated`/`error`.**
13. `PARK` COVERAGE (approver B5 + D-1) — `PARK(parent)` covers **SEVEN park sites**: `mps:1329` (R1), `cr:3511` (R2), `cr:3578` (R3), **`cr:3664-3698` (R4 — writes AND commits)**, **`child_reports._root_completion_gate` bus leg `:2637` (D-1 — switched by the sync twin; the emission-time gate that downgrades to `WAITING_CHILDREN` at `:5562-5572`)**, `msh:562` (R5), `msh:1240` (R6, negated). `:3379-3437` is **withdrawn from the switched set** (child-liveness door, unchanged). body §(c.3) ≡ ledger `PARK(parent)` cell ≡ §A5 row 1 ≡ criterion #6 ≡ risk table. **RE-ATTESTED MATCH (iteration 003, D-1/D-2).**
14. `EPISODE` end-rule (approver B4) — episode-end is **the ROW-CHECK** (`S4_NOTICE_SUBJECT`), **NOT WC membership**; the THREE pop sites are `:1607/:1617/:1746`; body §(d.2) ≡ ledger `EPISODE` ≡ T5 ≡ criterion #8. **RE-ATTESTED MATCH.**

**Digit / numeric consistency:** `3600.5-3600.7 s` (the council's reported digit-drop was checked at **PO:30** (the S4-dedupe row) and **PO:503** (the dedupe section) — the text **already** reads `3600.5-3600.7 s` at both; recorded as a **no-op** with evidence). `1800 s` for `WEDGE_REARM_COOLDOWN_SECONDS` (`watchdog.py:197`). **6** `requires_restart=False` keys today; the new one is the **7th**. **133** `.sql` migrations. `22:55Z` census / `23:03:54Z` task-39564 completion / `~02:30Z` and `~04:52-53Z` TTL clocks / `010144Z` bundle timestamp — consistent across §L-CENSUS, §(h), §(j)(iv)/(v), and the residual list.
