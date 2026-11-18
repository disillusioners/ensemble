# Approver Evaluation — gc-fe-surfaces (Group-Chat FE Fast-Follow)

## VERDICT: REJECTED
## Iteration: 001

**Mode:** Plan approval — 2 section-parallel workers (large-plan exception), both `plan-approval`, cold context, `plan-review.md` barred from evidence.
**Workers:** approve-worker-surfaces (904f97b3-66b3-47dd-ba66-1e7ed9454f65) · approve-worker-phases (cc4dc113-ecf2-42f8-9b03-bd475bb70cf3). Both independently REJECTED.
**Base:** origin/latest @ 0181dadbf8 (worktree). Both workers verified load-bearing anchors ON DISK (~20 anchors held between them — rooms.py scoping/limit+1, manager.get_tree_ids_permanent walk direction, MAX_PAGE_LIMIT=100, room-dialog sizes, spec pins at :372/:401/:432/:457, allowlist markers, angular.json budget config, instance-list.scss 15,813 B, AM-14 scope-manifest counts 119→120, playwright configs).

All three commission surfaces are present and the design honors the binding constraints the hard way (shared decision-core extraction, not predicate re-implementation; fail-closed verified at three independent sites; no new auth surface with honest AM-1 disclosure; zero-deps credible; SSE rejected on three code-verified grounds; no live promote). The rejection is about **executability**: two binding requirements have no owning task, one requirement is stated two mutually exclusive ways, one AC has no implementable mechanism, two count pins contradict their own enumerations, and the P4 web-automation leg as written violates its own port fence. Each fix is a small, concrete fold.

---

## Blocking Issues (each must be folded before re-submission)

### 1. `truncated` is binding but owned by no task — and the fold-disposition row mis-cites its owner
- **Refs:** `decisions.md` D1.1-a (L59) · `plan-overview.md` criterion 21 (L139) + fold row C-1 (L251) · `phase1-plan.md` task 8 (L21) + task 9 (L22) · `phase2-plan.md` task 3 · `phase3-plan.md` tasks 1/3/4
- **Expected:** a phase task that RENDERS the flag — global-browser "more rooms not shown" from `globalRoomsTruncated`, and an instances-gate warning when the anchor-less gate map was truncated — each with a spec case. D1.1-a: "RETAINS AND RENDERS — not merely tolerated"; criterion 21's measure column requires the render.
- **Found:** zero owning tasks. P1-8 only normalizes/tolerates (its own AC: "never renders it as room field"); P1-9 creates `globalRoomsTruncated` with no consumer; P2-3 covers title/empty copy only; P3-4 covers `roomMapError`/Retry only. `grep trunc phase2-plan.md phase3-plan.md` → 0 hits. Fold row C-1 claims P1 task 8 "the FE surfaces the flag" — factually wrong.
- **Fold instruction:** (a) add to P2 task 3: render "more rooms not shown" in the global-browser list pane driven by `globalRoomsTruncated`, + spec case; (b) add to P3: render the gate truncation warning when the anchor-less membership map was truncated, + spec case; (c) rewrite fold row C-1 to point at the new owning tasks; (d) recommended in the same fold: apply the same notice to `joined` mode (N6 below) so surface (b) cannot silently omit rooms.

### 2. Anchor-present closure walk specified inline-synchronous on a 10 s-polled route
- **Refs:** `phase1-plan.md` task 3 (L16) `descendant_ids=manager.get_tree_ids_permanent(instance_id)` · `decisions.md` D1.9 (L120) · risk rows R11/R15, D1.4-a
- **Expected:** the closure pre-compute off-loop — `await asyncio.to_thread(manager.get_tree_ids_permanent, anchor)` or a service-level off-loop facade — matching both D1.9's own pattern for the room-service facades and the neighboring handler idiom (`daemon/routers/rooms.py:148-150`), plus a per-tick cost line in the risk register.
- **Found:** specified inline and synchronous. `get_tree_ids_permanent` (`daemon/manager.py:16466`→`:16490`, walk at `instance/repository.py:869-880`, depth cap 256) runs one SELECT per frontier node; D1.9 marks only the room-service facades `asyncio.to_thread`; no risk row covers the closure. As written, every 10 s tick of the joined-popup/global-browser poll can block the daemon event loop for O(subtree) queries — the exact hazard class the plan itself rates High, on the release the video-team pilot is live on.
- **Fold instruction:** change the task-3 call to the off-loop form, extend D1.9's to_thread list to include the closure facade, and add the per-tick cost line to R15/D1.4-a.

### 3. Global-lane publication target stated two mutually exclusive ways; dialog binding unowned
- **Refs:** `phase1-plan.md` task 9 (ii) vs (v) (L22) · `phase2-plan.md` tasks 1–3 (L12-13) · shipped template binds `rooms()` (`room-dialog.component.html:15,33,40`)
- **Expected:** ONE publication target and a dialog task that reads it.
- **Found:** (ii) says `loadGlobalRooms` publishes "into the existing `rooms` signal"; (v) says a dedicated `globalRooms` signal "beside, not on top of, the shipped `rooms`". Mutually exclusive. No P2 task binds the list pane to a second signal — pick (v) and `global` mode renders an empty list; pick (ii) and the "shipped instance mode stays byte-unchanged" AC is false.
- **Fold instruction:** keep the dedicated `globalRooms` signal (v), delete the (ii) wording, and add/extend a P2 dialog task that binds the global-mode list pane to `globalRooms()`; re-check the instance-mode byte-unchanged AC wording against the surviving choice.

### 4. Per-row geometry has no implementable discriminator — AC (viii) cannot pass
- **Refs:** `phase3-plan.md` task 3 (L13), task 5 (L15), task 7 case (viii) (L17) · `instance-list.html:123-130` (row anchor binds only `active`/`has-children`/`pinned` + inline padding; no child/depth class; `depth` used only for padding), `.pin-btn` root-gating at `:217`
- **Expected:** a mechanism distinguishing root rows from child rows.
- **Found:** `.rooms-btn { right: 104px }` (root) and "child rows their own offset" are two rules on the same element with no discriminator; no task adds one; case (viii) is unachievable as specified.
- **Fold instruction:** EITHER add `[class.child-row]="depth > 0"` to the row anchor in task 3 + `.instance-item.child-row .rooms-btn { right: 72px }` in task 5, OR specify `:not(:has(.pin-btn))` (root rows always render `.pin-btn`, child rows never — `instance-list.html:217`); make case (viii) name the chosen discriminator.

### 5. Case-count pins contradict their own enumerations
- **Refs:** `phase3-plan.md` task 7 (L17) "the seven DOM cases (i)–(viii)" — enumerates 8; criterion 25 repeats "7/7" · `phase2-plan.md` task 8 (L18) "5 new cases" + Tests block "+5" — enumerates (i)–(vi) = 6
- **Expected:** declared count == enumerated count (the plan's own gates are count-assertion based).
- **Found:** a tester cannot satisfy both the number and the enumeration; gate evidence becomes ambiguous exactly where the plan demands count pins.
- **Fold instruction:** make the numbers match the enumerations (P3: 8, fix criterion 25; P2: 6, fix the Tests block), or trim the enumerations to the stated counts — one of the two, consistently.

### 6. P4 web-automation leg as specified violates its own port fence
- **Refs:** `phase4-plan.md` task 6 (L17) — commits spec under `frontend/e2e/`, cites `playwright.config.ts:4` · `frontend/playwright.config.ts:19-33` (webServer = `cd .. && bash dev.sh` on **8079** + `ng serve --port 4199`; no `testMatch` → default sweep of all 35 e2e specs) · house precedents: `playwright.metrics-gate.config.ts`, `playwright.resources-gate.config.ts`, `playwright.snapshots.config.ts` (dedicated testMatch + ports)
- **Expected:** a harness that binds NEITHER 9797/9798 NOR 8079/8088 and asserts its own unused port pair.
- **Found:** inheriting the cited config either touches the dev daemon lane port (8079) or is unrunnable; no dedicated config is named. A dedicated config is a second committed `frontend/` file, which also makes task 7(b)'s allowlist accounting one row short (see #7).
- **Fold instruction:** name a dedicated Playwright config for this leg (house pattern: own ports + `testMatch` pinning exactly the one spec); state explicitly that the leg does NOT inherit `playwright.config.ts`.

### 7. P4 Files block does not own the files its own tasks edit
- **Refs:** `phase4-plan.md` Files block (L24-26) — lists only `.agents/shared/planning/gc-fe-surfaces/*`, states "no production code changes are expected in this phase" · tasks 6 and 7(b) (L17-18) edit `frontend/e2e/<spec>.spec.ts`, the new dedicated config (per #6), and `tests/unit/repositories/test_source_gc_repository.py`
- **Expected:** every file a phase edits is enumerated in that phase's Files block — the plan's own REJECT-grade single-ownership discipline (`plan-overview.md` L88).
- **Found:** those paths are owned by no phase's Files block.
- **Fold instruction:** add the three paths to P4's Files block; amend "no production code changes" to scope it to `frontend/src/**` + `daemon/**`; add the config row to task 7(b)'s allowlist accounting (fold with #6).

---

## Notes (non-blocking)

1. **Allowlist count "five" vs four** (merged finding): `plan-overview.md` R5 (L100) and `phase3-plan.md` R5 (L59) say "four modified + new spec = five"; only three instance-list sources are modifiable + new spec = FOUR — the operative cells (P3 task 6, criterion 17, P4 task 7(b)) correctly say four and forbid the mirror spec. Fix the two R5 numbers; "five" invites allowlisting the forbidden mirror.
2. **Stale gate-1 statuses** in `decisions.md` Q4 (L501-511) / Q5 (L516-526) vs CLOSED everywhere else (D2.1, D1.5, addendum §Q4/§Q5, overview L205/211). Add one-line "→ CLOSED (architect gate 1)" pointers.
3. **"Four evidence-backed reasons"** (`decisions.md` D5.2 row 8, L409) vs three (reason 4 withdrawn, AM-6). Residual.
4. **Criterion numbering skips 20** (1–19 then 21–26; A-7 claims sequential 1–26; likely missed renumber after the C-1 split). Cosmetic — no doc references criterion 20.
5. **Naming self-contradiction** D1.9 (L120): `list_readable_rooms` exists on both service and repository layers; P1 task 2 gives two signatures of it in one cell. Harmless; one edit.
6. **Joined-mode truncation not surfaced** (N6): the `joined` popup is a third bounded consumer with no render requirement — a high ancestor with >limit readable rooms silently omits joined rooms in the commissioned surface (b). Recommended fold alongside blocker #1.
7. **D2.4 stale-copy render** only implied (P1-9(iv) service layer); no phase task renders honest copy in `room-dialog.component.html`. Same class as #1, weaker — fold or make explicit.
8. **Global browser bounded at 200/500 with flag only, no load-more** — defensible (8 active rooms/tree; purged at +14 d) but copy should say "first N of the nest," not imply completeness.
9. **`?instance_id=` present-but-empty degrades to nest-wide** (knob ON) — a malformed joined-popup call silently widens instead of erroring; consider 422 for present-but-empty, keep anchor-absent for true absence.
10. **Disclosure precision**: `tree_root_id` / `owner_instance_id` sit in neither D1.11 list (both derivable but AM-1's standard is explicit enumeration). One clause closes it.
11. **"byte-behaviour-identical" overstated**: `providers: [RoomService]` (D3.4/AM-2) changes the shipped instance-mode modal's DI lifecycle — rendering + list source identical, DI scope changed and disclosed; reword.
12. **Cosmetics**: risk-row ordering R14 out of place; `app.routes.ts` :136 vs :135; `live_event_hub.py:38-56` vs `:41-56` (AM-6 verified correct); P4 FE named-set command omits the new rooms-btn spec (covered by whole-tree jest); P2 task 5 pin anchor is the `it(` line (:372), assertion at :392; P1 task 2 rule expression redundant (`rating_ids` is caller-inclusive); P3 TestBed DI list may need `InstancesViewStateService`/`Clipboard`/`MatSnackBar` (root-provided in house — validate at impl); "9 stylesheets over 8 kB at base" should be re-measured at the P4 base capture rather than quoted.
13. **Emperor-disclosure suggestion**: commission (c)'s "near-watchover button, no dead icon on room-less instances" reading (new row button = target; shipped header icon untouched) is natural but worth one explicit disclosure line alongside Q1.

## Positive Observations (why this is close to approvable)
- Anchor discipline is real: ~20 load-bearing anchors re-verified on disk at base by the two workers — every BE/layout/spec anchor held (the two failures were in the plan's own task text, not in its anchor claims).
- Constraint 1 satisfied the hard way: one shared decision-core extracted from `_require_room_access`, re-pointed by both sites, fail-closed verified at three sites, AM-10 narrowing edge pinned two-sided.
- No new auth surface; AM-1 corrected to name the genuinely new class (ACTIVE roster); P4's 12-item disclosure duty list is exemplary ownership.
- Constraint 3 decided on code-verified evidence (three reasons; the fourth honestly withdrawn).
- Zero-deps credible; budgets honestly framed ("net-new WARN vs base capture"; pre-existing instance-list.scss WARN base-attributed).
- Scope tight: one route serves all three surfaces, no N+1, no route/chunk/WATCHOVER invention; C-4 seam hazard (graph-restore in `get_instance` can terminate an instance) caught and fenced to a pure repository read.

## Skills Used
plan-approval (both workers)

## Session IDs
- approve-worker-surfaces: 904f97b3-66b3-47dd-ba66-1e7ed9454f65
- approve-worker-phases: cc4dc113-ecf2-42f8-9b03-bd475bb70cf3

## Aggregation
Dedup: workers' #1 (surfaces) ≡ #2 (phases) merged into blocker #1; allowlist-count notes merged; criterion-numbering notes merged. No Blocking→Note downgrades; no Note→Blocking upgrades; no approver-introduced blockers. Both worker verdicts REJECTED → aggregated REJECTED.

*Tracking: .agents/approver/gc-fe-surfaces-tracking.md (iteration 001 appended; next iteration 002).*

---

# Iteration 002 — Fold Verification (fold commit fd590eafb)

## VERDICT: REJECTED
## Iteration: 002

**Workers:** approve-worker-fold-phases (85f19040) · approve-worker-fold-render (8b763950), both cold, `plan-review.md`/`approval.md` barred. Both REJECTED.

## Fold status (001 blockers)
| 001 blocker | Status |
|---|---|
| 1. truncated render ownership (P2-3/P3-4/C-1/P1-8 split + N6/N7) | **FOLDED — all legs** (render + named spec cases everywhere; C-1 re-pointed; P1-8 split clean) |
| 2. off-loop subtree walk | **FOLDED** (`await asyncio.to_thread(manager.get_tree_ids_permanent, …)` in P1 task 3; D1.9 row; D1.4-a per-tick cost line) |
| 3. dedicated globalRooms signal | **FOLDED** ((ii) gone; P2-3 owns template binding; shipped `rooms()` untouched; instance-mode AC preserved) |
| 4. child-row discriminator | **FOLDED** (`[class.child-row]="depth > 0"` + `.instance-item.child-row .rooms-btn { right: 72px }`; alternative rejected WITH reason; case (viii) names the mechanism) |
| 5. counts == enumeration | **PARTIAL** (core landed: P3 9/(i)–(ix) + criterion 24 "9/9"; P2 8/+8 — but 2 stale refs survived; see blocker 4) |
| 6. dedicated rooms-gate Playwright config | **FOLDED** (rooms-gate config named, testMatch pinned, own port pair, explicit no-inheritance from the 8079 shared config, port-fence assertion; house precedents verified on disk) |
| 7. P4 Files-block ownership | **FOLDED** (e2e spec + config + allowlist test enumerated; claim scoped to frontend/src/** + daemon/**; 7(b) counts the config row) |

Notes: N1 **FOLDED** (four paths everywhere, mirror spec unsanctioned, no surviving "five"). Structural re-scan **CLEAN** (one Risks section R1–R17 in order; criteria 1–25 no gaps/dupes; orphan R14 removed).

## Blocking Issues (4 — all fold residue, all small)

### 1. N9 not folded — the 422 typed refusal is an assertion without an owner
- **Refs:** `decisions.md:61` + `:100` (decision, claims "each is a spec leg (P1 task 5)") vs `phase1-plan.md:18` task 5 (no 422 leg — pins `?instance_id=`/`%20` to **404** "anchor-absent spelling") + `:37` Tests block + `phase1-plan.md:16` task 3 (`instance_id: str = Query("")` — cannot distinguish absent from present-but-empty) + `plan-overview.md:124` criterion 5 (4 codes, no 422) + `plan-overview.md:282` A-2 taxonomy label conflict. `grep "422\|bad_request"` across phase plans → zero.
- **Fold:** add the 422 `bad_request` leg to **P1 task 5** (knob-ON present-but-empty spellings, wire-distinguishable from both anchor-absent 404 and bad-anchor 404) + its Tests-block row; **criterion 5 → 5 codes "5/5"**; change the router spec to `Query(None)` with explicit presence detection so the handler can implement the distinction; fix the A-2 row to label `?instance_id=`/`%20` as present-but-empty (distinct regime from true absence).
- *(Orchistrator answer to the worker's open question 1: yes — task 5 additive + criterion 5, not a new task.)*

### 2. N11 partial — and the fold introduced two NEW direct contradictions
- **Refs:** `decisions.md:266` (honest form: "byte-identity is NOT claimed") vs `decisions.md:137` ("the shipped mode's byte-identity is the constraint-4 duty") and `:303` ("preserves the shipped modal byte-for-byte") — plan's own prose, now self-contradicting. Stale variants: `decisions.md:506` (US spelling "byte-behavior-identical" — invisible to a UK-spelling grep), `:511`; `plan-overview.md:205`, `:209` (plan-prose mirrors; the fold DID reword the sibling at `decisions.md:182`).
- **Fold:** correct the plan's own prose (`:137`, `:303`, overview `:205`/`:209`) to the D3.4 honest form; for the verbatim leader-ruling record regions (`:506`, `:511`) do NOT rewrite the quotes — append a one-line annotation "(historical ruling record; superseded by the N11 honest form at D3.4)". *(Orchistrator answer to open question 2: quoted records stay byte-untouched + annotated; only plan prose is corrected.)*

### 3. Five stale criterion cross-references after the 21–26 → 20–25 renumber
- **Refs:** `plan-overview.md:228` AM-4 "criteria 21/22" → now **20/21**; `:229` AM-5 "criterion 23" → **22**; `:233` AM-9 "criterion 24" → **23**; `:272` W-11 "criterion 24" → **23**; `:292` A-12 "criterion 23" → **22**. (The fold swept only AM-7 and C-3.)
- **Fold:** re-point the five refs to the post-renumber ids. *(Orchistrator answer to open question 3: re-pointing is the smaller edit — do not restore the old numbering.)*

### 4. Count residue (merged: fold-phases #1/#2 ≡ fold-render #3)
- **Refs:** `phase3-plan.md:17` task 7 mid-cell prose "the **seven** DOM cases below (i)–(viii)" (title says NINE, enumeration (i)–(ix), acceptance "9/9" — three of four carriers agree; the prose is stale) · `plan-overview.md:231` AM-7 row "criterion **24** (seven cases)" → "(nine cases)".
- **Fold:** two one-word/one-number edits.

## Notes (non-blocking)
- `decisions.md:123` D1.9 row has an unescaped `|` (`set[str] | None`) breaking the 3-column table render — the fold rewrote that row and left the pipe; escape it.
- `plan-overview.md:253` C-1 still says "criterion 21 split" while citing "criteria 20/21" — reword to "criteria 20/21".
- R15 risk row does not mirror the closure per-tick cost line (D1.4-a carries it) — recommend mirroring for discoverability.
- Task 7(b) "THREE committed-path rows … the spec, the config, and the four instance-list paths" groups six files in three rows — loose but not a count contradiction.
- P4 references to criterion 15 unaffected by the renumber — verified.

## Skills Used
plan-approval (both workers)

## Session IDs
- approve-worker-fold-phases: 85f19040-a5e7-43ec-abc5-4d33e08ab2da
- approve-worker-fold-render: 8b763950-0812-4028-9469-1aad1c5645e1

## Aggregation
Merges: fold-phases#1 ≡ fold-render#3 (same P3 cell); fold-phases#2 grouped into blocker 4 (same defect class). No downgrades, no upgrades, no approver-introduced blockers. Both worker verdicts REJECTED → aggregated REJECTED. **Iteration 003 is the cap** — a third rejection escalates to the Leader per the 3-iteration rule. The four residues are one-word-to-one-cell folds; a single careful sweep clears them.

*Tracking: .agents/approver/gc-fe-surfaces-tracking.md (iteration 002 appended; next 003 — CAP).*
