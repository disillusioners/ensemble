# Round 2 Review — delivery copy

_Written 2026-11-27 at the orchestrator's instruction after a delivery wedge (the 2026-11-26 20:06 UTC message never reached the requester). Content below is the Round 2 report verbatim._

- Reviewed artifact: `.agents/shared/planning/boot-restart-semantics/architecture-recommendation.md` — 1225 lines / 181,822 B, md5 `67ed4f9b996c4490db1ad8dcffe0778d`, mtime 2026-11-26 17:19:40Z (worktree `/home/nea/ensemble-src-wt-boot-restart-semantics`, branch `design/boot-restart-semantics` @ `d7c15d45f` = `origin/latest`). Unchanged through the review.
- Reviewer instance: `d8fe1208-eb4a-4d69-a1c4-51f65f131783` (reviewer[v2]). Governor: `c036ac49-df07-4d97-96ec-907321faf09f` (`architecture-review` council, 2 councilors).
- Anomalies filed: `e2adff1d` (governor — refinement round wedged, both councilors force-terminated at the 1 h cap), `d2704511` (reviewer — stale WAITING_CHILDREN park with all descendants terminal).

---

**Wedge handling (for the record):** the park was stale — all descendants terminal, no carrier. First occurrence → playbook step 1 (reconciler self-heal); I did **not** self-message or re-spawn. Filed once as anomaly `d2704511`. If the notice repeats, I'll apply step 2. Because the completion receipt resolves to my last message, the round-2 deliverable is restated in full below so nothing is lost.

---

## Review Summary: Boot & Restart Semantics — Round 2 (targeted re-review)
Date: 2026-11-26 20:06 UTC (restated 2026-11-27 01:15 UTC)
Mode: 🔴 Deep-Review (governor council, `architecture-review`) — governor `c036ac49-df07-4d97-96ec-907321faf09f` (`completed`), 2 councilors, ~78 citations opened; refinement round wedged (anomaly `e2adff1d`, filed by the governor); the open §1.8 call closed by my own bounded read of the artifact (`:204-213`, `:233-243`, `:280-294`, `:308-321`).

### Status
# 🔴 REJECT (narrow) — 2 CRITICAL · 9 MAJOR · 7 MINOR (new) · prior ledger **29 FIXED / 7 PARTIAL / 0 NOT FIXED**

**Not everything I raised is fixed, and the revision introduced new defects.** C-1 is genuinely closed (EC-5 a first-class framed choice `:309-320`, mirrored in §7/B-3/App. A; §1.5(b) says "not settled here"). But the revision **relocated the reserved-choice leak into the new §1.8**, left a budget hole in SP-2, and omitted two `"sweep"` writers from the journal inventory.

**Rulings on the council's split (A: APPROVE-WITH-CHANGES · B: REJECT · governor: REJECT):**
- **NEW-1 upheld CRITICAL** (verified by my read).
- **NEW-2 escalated by me MAJOR → CRITICAL**: same §1.8 header/mechanism; EC-3.3's own work-shape cell reads *"PC-3 surfaces (§1.8 O-2)"* (`:288`) while EC-3.2 is *"ERROR log only"* (`:287`).
- **F12(b) escalated by me MINOR → MAJOR** (the M-8 residual). F1/F2 stay MAJOR (the M-2 class).
- Count correction: the ledger is **36** items, not 37 (my dispatch miscount; Councilor B caught it).
- Robust: REJECT stands on NEW-1 alone even if you overrule NEW-2.

### Per-item table (36)
| Item | Verdict | Proving line + quote |
|---|---|---|
| **C-1** | **FIXED** | `:309` EC-5 defined, options `:315-318`; `:320` *"recommendation, NOT a decision"*; `:206` *"framed as EC-5 … not settled here"*; `:212` *"does not choose between EC-5.1/5.2/5.3"*; B-3 `:1057`; App. A `:1215` |
| M-2 | FIXED | `:142` *"PC-2 and PC-3 state the recommended contract, pending the Emperor's ruling on EC-3 … EC-3.1 … conflicts with PC-2 as written"* |
| M-3 | FIXED | `:661` *"This is a journal FORMAT change, and the format does not carry the field today"* + guard chain `journal_splice.py:92-96/:304/:316/:363/:370/:381-385` |
| M-4 | FIXED | `:746` *cleanup_old lives at daemon/repositories/event/repository.py:176 (corrected)*; §5.3 `:945` → **YES** |
| **M-5** | **PARTIAL** | `:1037` four deps + lockstep test ✓ — residual **F3** (`upgrade_journal.py:654`, `:2811` absent) |
| M-6 | FIXED | `:181` arm-1 literal hazard + same-commit / `NOT IN` disposition |
| M-7 | FIXED | `:391-397` three-class table w/ re-derivability; `:399` skip-record *"…NOT in the upgrade journal"* |
| M-8 | FIXED | `:382` B covers uvicorn drain + lifespan P1 + MUST set, B-derived; six bounds `:362-373` — see **F12(b)** |
| **M-9** | **PARTIAL** | `:180` *"test_p3 breaks exactly as test_p2 does"* ✓ — residual **F5** (parent-wedge absent `:258`) |
| M-10 | FIXED | `:1038` *"S3 — NOT S4 … changes what is DONE"* + commit-then-restore pin test |
| **M-11** | **PARTIAL** | `:647` writer set + exclusion rule/CAS ✓ — residual **NEW-5** (B-8 `:1062` no concurrency test) |
| M-12 | FIXED | `:491` *"5 s × N inside B … one SIGKILL per live service … must not call ServiceToolManager.stop"* |
| M-13 | FIXED | `:817-826` 7 reconstructed + 8 count-only, `work_id=NULL`; `:824` *"must be NULLABLE"* — see **F8** |
| m-1 | FIXED | `:494` conditional on **B-9** |
| m-2 | FIXED | `:53` *"…that armed the watchdog hold"* |
| m-3 | FIXED | `:538` unachievable — `config.py:591` `env_prefix="DAEMON_"` |
| m-4 | FIXED | `:384` launcher reads `DAEMON_SHUTDOWN_BUDGET_SECONDS` from `INSTALL_DIR/.env` |
| **m-5** | **PARTIAL** | `:159` discloses `f78907d10` vs `518913da1` ✓ — residual **F6b** (*"pins unchanged"* FALSE) |
| m-6 | FIXED | `:190` both mechanisms/log lines, *"UNVERIFIED BY EXECUTION"*; §8 discriminator |
| m-7 | FIXED | `:604-614` two distinct halt rows; `:616` REQUIRED shape |
| m-8 | FIXED | `:360` *"ceilings, not floors"* |
| m-9 | FIXED | `:369` post-stop verification poll only; `:386` real bounds |
| m-10 | FIXED | `:1020` premise unverifiable → conditional; `.62+` if frozen |
| m-11 | FIXED | `:527` pre-refuted by unit template `:51-57` |
| m-12 | FIXED | `:696` writers `graph.py:8206`, `instance_messaging.py:1700`, `:4300` — see cross-round note |
| **m-13** | **PARTIAL** | `:1022` S1–S5 renamed ✓; `:689` A8 footnoted ✓ — residual **F9/F10** |
| m-14 | FIXED | `:223` consequence question + §8 item 7 `count(*) … status='processing'` |
| m-15 | FIXED | `:1036` Low — earlier claim overstated |
| N-1 | FIXED | `:239` *"it is EC-18"* + table `:325-333` |
| N-2 | FIXED | `:753` ⇒ **EC-16** |
| N-3 | FIXED | A-2 is the prerequisite |
| **N-4** | **PARTIAL** | `:1027` S4 gloss narrowed ✓ — residual **F6a** (excludes A-1/A-3 from (a)'s own gate) |
| **N-5** | **PARTIAL** | `:572` RT modelled as a second, lock-less writer ✓ — residual **F11** (`:11923` omitted) |
| N-6 | FIXED | `:1043` scratch-promote tests *"must pass BEFORE that ceremony"* |
| N-7 | FIXED | `:423` *"60 s hard cap and simply leaves more rows open"* |
| N-8 | FIXED | §6.4 `:1085-1093` four-row scorecard |

### New findings

#### 🔴 Critical
**NEW-1 · §1.8 O-4 re-decides the reserved EC-5 counter in the body** — `:234` *"no Emperor ruling needed"*, `:236` *"engineering, not semantics"*, `:241` *"O-4 — Purge generation counter"*; contradicts `:212` and EC-5.3 (`:317`, zero work, no counter); EC-5.2 (`:316`) **is** the counter. Also leaks from `:206` *"Layers 2 and 3 are engineering and are specified"*. **Fix:** move O-4 out of the no-ruling set — *"specified (designed); BUILT only under EC-5.1/EC-5.2"* — and align `:206`/`:212`.

**NEW-2 · §1.8 O-2 pre-commits EC-3.3** — `:239` *"O-2 — Metric + readyz + anomaly on PC-2"* under the same header, vs `:142` *"pending the Emperor's ruling on EC-3"*; EC-3.2 `:287` *"ERROR log only"*; EC-3.3 `:288` *"PC-3 surfaces (§1.8 O-2)"*. **Fix:** qualify O-2 *"if EC-3.3 is chosen; EC-3.2 drops it"* — same edit as NEW-1.

#### 🟡 Warnings (MAJOR)
- **F1 · JT-3 selects EC-13.3 while §3.5/A-1 select EC-13.2** — `:568` *"scoped by run_id"*, no pending-EC qualifier; EC-13.2 `:660` justified via `run_log` (`promote.sh:412-417`), refuted by `:661` *"no run_id on the commit row"*. **Fix:** demote as PC-2/PC-3 were at `:142`.
- **F2 · §2.2 "The correct contract" while EC-6 is open** — `:377`→`:397`; SP-1…5 are EC-6.3; EC-6.1/6.4 incompatible. **Fix:** *"recommended contract (pending EC-6 / EC-11)"*.
- **F3 · §0.2 "three unrelated writers" — there are FIVE** — `upgrade_journal.py:654` (`reclaim_stale_txn`), `:2811` (`reconcile_pending_op` **expiry** closure, fires **after** the commit row) absent from §0.2 and A-2's Writers cell → the static label *"swept (orphaned pre-flip txn cleared)"* (`upgrade_tools.py:1493`) can still win the reverse scan — the commission's named defect. **Fix:** five writers; extend A-2 + lockstep test to the expiry closure.
- **F4 · SP-2 MUST-NEVER-SKIP holds an unbounded step** — `:393` snapshot-capture drain; SP-3 table `:401-407` omits it; `manager.py:17824`→`:17834` `await service.drain_capture_tasks()` no timeout, step 9 of 13 before durability → `:397` *"sum ≤ B"* cannot hold. **Fix:** inner bound in SP-3, or reclassify MUST-BEST-EFFORT with record.
- **F5 / NEW-4 · EC-1.3 consequences omit the parent-wedge** — `:258` *"cheap — work is lost"*; lane `message_queue/repository.py:1661-1669` raw UPDATE skips `reconcile_turn_mirror` + `_notify_pending_task`; `WAITING_CHILDREN` watchdog `config.py:2608+` may mitigate — UNVERIFIED. **Fix:** add wedge + watchdog to consequences; require `fail_task` routing or labelled waiver.
- **NEW-5 · No concurrency test for the compaction exclusion rule** — `:647` stated; B-8 `:1062` lacks a writer-race test. **Fix:** add it.
- **F6a / NEW-3 · (a)'s own S1–S5 gate excludes A-1 and A-3** — `:1022` + `:1027` S4 *"no change to what the system decides"*; A-3's cell *"S3 — NOT S4"*; A-1 changes outcome derivation. **Fix:** restate S4 or move A-1/A-3 to (b). *(Not an R-e ride-safety defect — no (a) item overlaps the lane's files.)*
- **F6b · m-5 "pins unchanged" is FALSE** — `:159`; `git diff f78907d10 518913da1` removes `assert ":tname"/":id_" not in pred` from `test_f4` and moves the arm-1 literal `:586`→`:592`. **Fix:** *"the local tip changes the pins; the pins cited are the pushed tip's."*
- **F12(b) · "B-derived" names no derivation (M-8 residual)** — `:382`; if it equals B, uvicorn's drain consumes the whole budget before the lifespan MUST steps — the M-8 mechanism. **Fix:** name the derivation (B − launcher slack − lifespan P1 − MUST sum).

#### 🟢 Suggestions (MINOR)
- **F7** `:246` *"Consolidated to four"* vs six (`:338`). · **F8** `:717` `| work_id | text |` vs `:824` NULLABLE (builder would reject the 8 count-only rows + §4.5 rows; 4th seam omitted) — fix-first. · **F9** `:689` A8 is shipped source `promote_txn_hold.py:501`, not a lane note. · **F10** `:1040` A-5 still *"C5-risky"*. · **F11** `:572` 4 of 5 restore sites, `:11923` omitted. · **F12(a)** `:190`/`:819` cite "§8.5" (§8 items are 1–8; log lines = item 5). · **NEW-8** `:159` 685 lines is the test suite, not `manager.py` (17,946).

### What survived
C-1 discharged; citation discipline strong (every opened citation landed; §5.5 "all YES" not false; no stale flag after a code-touching fix; M-3's guard chain exact); §2's bound inventory exact, "six bounds" correct; EC-1…EC-18 each exactly once, Appendix A/§7 agree; §6.4 honest; lane pins exact (`test_p3 :398`, `test_f4 :586`, belt `:1679-1690`/`:1661-1669`); no (a) item overlaps the lane's four files.

### Gaps
Refinement round never ran (F1–F4 not cross-examined); neither councilor re-derived all ~154 new lines; §4.2 drop-path table + §6.2 B-1..B-9 merit a second pass. Cross-round writer discrepancy (UNVERIFIED): round 1 named `thread_id` writers `instance_messaging.py:1195`/`manager.py:11275`; revision names `graph.py:8206`/`instance_messaging.py:1700`,`:4300` — confirm the full set with one grep. Live evidence and NO-marked citations unopened (fence); nothing executed. UNVERIFIED: WAITING_CHILDREN watchdog compensation; staged-stack freeze (m-10 premise). Councilor A's header miscounts its own findings (8 vs 12) — report defect only.

### Recommendations (next cycle — one edit closes both CRITICALs)
1. §1.8: condition O-4 on EC-5.1/5.2, O-2 on EC-3.3 (or move both out); align `:206`/`:212`.
2. Demote JT-3 `:568` ("pending EC-13") and §2.2 `:377` ("pending EC-6/EC-11").
3. Snapshot drain into SP-3's inner-bound table; name the B derivation at `:382`.
4. Five `"sweep"` writers in §0.2 + A-2 + lockstep test; writer-race test in B-8.
5. Parent-wedge + `fail_task` directive in EC-1.3 `:258`; restate S4 or move A-1/A-3; fix `:159`; then the MINORs in one pass.
6. Re-review scope: §1.8 + `:206/:212`, `:568`, `:377`, SP-2/SP-3 table, `:382`, §0.2/A-2 writers, EC-1.3 `:258`, B-8, §6.0/§6.1 — plus a line-by-line pass over the ~154 new lines.

### Process notes
Artifact unchanged through the review (md5 `67ed4f9b996c4490db1ad8dcffe0778d`, 181,822 B, mtime 17:19:40Z). Fences held (no writes, no runs, lane via `git show` only). My direct reads: 4 bounded artifact reads, no source. Anomalies: `e2adff1d` (governor — wedged refinement), `d2704511` (mine — stale WAITING_CHILDREN park, all descendants terminal).
