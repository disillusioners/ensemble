# Review — Plan Council: promote-preflight-hang (board wi-fc1786a2)

**Date:** 2026-10-29 (UTC)
**Reviewer:** reviewer (controller) — dispatched governor council `review-council-preflight-hang` (governor instance `b9d9fe12-b0e7-4696-baf7-572931df449e`), per commission (house standard for promote-path changes: multi-model council).
**Council composition:** 2 models (`agentic`, `coding`), worker councilors, `plan-review` skill — one skill per councilor, Round 0, both reports received inside deadline.
**Target:** the complete plan set in this worktree (branch `fix/promote-preflight-hang`, base `b1183a637`): `plan-overview.md` (~381L), `phase0-plan.md` … `phase6-plan.md` (phase6 NEW), `decisions.md` (~510L), `architecture-recommendation.md` (~431L), `evidence/{FORENSICS.md, CODE-PATHS.md}` (sha-pinned factual base), cross-checked against this worktree's `scripts/upgrade/lib.sh` + `scripts/upgrade/promote.sh`.
**Method / evidence quality:** both councilors *independently* re-derived the 25-call-site census and produced **identical site lists** (17 lib.sh + 8 script sites); together ~65 code anchors across 10 files verified with **zero substantive misanchors**. Read-only discipline and no-live-contact fences held throughout (STAGE-ONLY lane semantics, no `env -u POSTGRES_*`, no scratch needed).

---

## VERDICT: ⚠️ APPROVE-WITH-ADVISORIES — CONDITIONAL on a fold pass

**The plan set must NOT go to implementation as currently written.** Three blocking findings (B1–B3) must be folded in ONE pre-implementation doc pass, followed by a **scoped delta re-check of phase 6 only**. **Phases 0–5 are approved as verified.**

**Verdict split, surfaced transparently:**
- `agentic` → **APPROVE-WITH-ADVISORIES** (0C/4W/5A)
- `coding` → **REJECT, narrow** — plan-set completeness only, not design — with explicit fallback APPROVE-WITH-ADVISORIES conditional on folding its C1–C3 + W1–W8 (3C/8W/5A)

**Adjudication (governor, concurred by reviewer):** this is a severity-calibration disagreement, **not a factual one** — no claim in either report is contradicted by the other; both models converge on the same two top issues independently; coding found three additional code-verified gaps agentic missed. Both models bless phases 0–5 as accurate, anchor-verified, above house norm. Every blocker sits in the **newest, least-scrutinized material (phase 6 + post-fold ACs)**. Conditional-approve chosen because a wholesale REJECT would discard verified-sound P0–P5 and re-litigate what both models already validated, while the blockers are small, specific, and doc-level. If the house reserves REJECT for "cannot be implemented as written" (which B1/B3 literally are), the coding councilor's narrow REJECT is defensible — **the operational instruction is identical either way: fold first, then implement.**

---

## CONVERGENCE ZONES (high confidence — 2/2 models)

- **Root-cause completeness:** census exactly right (independently reproduced by both); P1 migrates read + RMW + `_json_sub` as a unit; SC3 census leg GREEN-at-HEAD/RED-at-BASE.
- **P1 port methodology is sound** — the strongest part of the plan: byte-level same-state-machine port (ASCII-significance argument holds), true-BASE oracle with *captured-not-authored* verdicts, the `$(cat)` command-substitution emulation contract (verified real at `lib.sh:968`/`:998`), duplicate-key BASE-bug-preserving pin, three-algorithms rule, proxy-gated 455 KB differential leg.
- **Watchdog false-fire design is structurally sound:** phase-scoped arming, budgets derived from measured numbers, real-300s-soak negative control, derived kind-scoped RuntimeMaxSec; trap-before-lock order + journal→warn→release→exit breach order + `_HALT_JOURNALED` + unlocked-path split prevent self-inflicted orphaning.
- **Risks register:** the 25 rows are the right rows; sibling-lane merge-order handled by RULING 10 measurement + standing tip-identity re-check; live unlocked-path blast radius handled by D1-3/D1-8/D2-6 (lock states verified from code).
- **Scope discipline: CLEAN.** No unjustified expansion; every decline is rationale-backed; nothing the commission explicitly required is missing; broken-pipe deliverable covered (D3-6/D3-7, AC6/AC7).
- **Rollback posture** explicit (migration delta EMPTY, journal format unchanged, capability-regression-on-rollback disclosed).

---

## 🔴 BLOCKING FINDINGS — fold before implementation (all in P6/post-fold material)

### B1 — Arm-time floor comparison semantics undefined *(agentic W1 + coding C1/W8 — 2/2 models)*
**Refs:** `phase6-plan.md` §4.2; `decisions.md` D6-4 (:441-446), D6-8 (:465-468), D6-2 (:425-431).
The stamp is specified as a git SHA ("`PIPELINE_SCRIPTS_REV=<this lane's merge commit>`") and the check fires on "stamp absent-or-**older-than-floor**" — but git SHAs are **unordered**; no ordering mechanism exists anywhere in the set (both models grepped). The non-git pinned-dir case (permitted by D6-2 option b) is undefined: fail-open defeats R-A, fail-closed recreates the ceremony-blocking R-G shape. D6-2's "sync to **≥** this lane's merge rev" is the same invalid predicate. `MIN_PIPELINE_SCRIPTS_REV` has no owner, storage, or cadence.
**Remediation (merged):** pick and state ONE orderable scheme —
(a) monotonic `PIPELINE_SCRIPTS_SEQ=<int>` + numeric floor; or
(b) **content-addressed** (council-preferred: daemon holds the expected `lib.sh` sha256 for the minimum acceptable rev and refuses iff the pinned dir's `lib.sh` sha256 differs — no ordering needed, no false refusals from newer-unrelated trees); or
(c) stamp allow-list.
Replace "≥" with `git merge-base --is-ancestor <lane-merge> <synced-rev>`, record synced rev + ancestry result in the arm row. Define floor ownership/storage/cadence. Add a Pack I leg for the non-git-with-stamp pinned dir.

### B2 — Mutually exclusive RuntimeMaxSec constants across ACs *(agentic W2 + coding C2 — 2/2 models)*
**Refs:** `phase2-plan.md` T2.8 (:229) + AC8 (:258) pin `RuntimeMaxSec=1200`; AC16 (:266)/AC17 (:267) pin derived 1320; `phase5-plan.md` Pack H (:36), T5.9 (:115), AC9 (:138) repeat 1200 while AC15 (:144) pins the derivation; stale 1200 also in `decisions.md:173` and `phase2-plan.md:111` ladder rows.
A pack **cannot satisfy both**; 1200 is precisely the architect-superseded constant (fires mid-soak, AFTER the symlink flip).
**Remediation:** one sweep → `<derived per D2-9>` with AC17/AC15 as single source of truth; fix both ladder rows; add a grep-assertion that the literal `1200` appears nowhere in the lane's pins (existing house pattern).

### B3 — Guard input acquisition fires after the event it gates *(coding C3 — 1/2; agentic did not examine this seam)*
**Refs:** `phase6-plan.md` §4.2 (:85), T6.4/T6.5 (:118-119), D6-4; R-G: "the guard must fire **before the nonce burn**".
The only stated acquisition is "stamped in lib.sh; **echoed at executor start**" — but the executor starts **after** `consume_pending_action` (the nonce burn). Read literally, the guard's only input arrives too late to gate anything.
**Remediation:** daemon opens `<scripts_dir>/lib.sh` at arm time and parses `PIPELINE_SCRIPTS_REV=` (bounded first-N-KB read; parse failure ⇒ "stamp absent" ⇒ refuse on an explicitly pinned dir). Reposition the executor-start echo as a second **corroborating** signal (proves running code == stamped code — a different property). Add M25: stamp is read from the file, not from the executor.

---

## 🟡 WARNINGS — should-fix, same fold pass

### W1 — Budget-scope table contradicts scope paragraph; one listed site is a WRITE path *(coding W1)*
`retention_evict` (`lib.sh:5528`, called at `promote.sh:571` — commit phase, after the flip, outside the armed window) is **mutating** (`rm -rf` release dirs); D2-14(ii) forbids wrapping write paths.
**Fix:** split §3.3 into in-window/out-of-window tables; add a *side-effecting?* column; mark `retention_evict` must-not-wrap; name commit-phase sites deliberately uncovered in §3.6.

### W2 — Size guard can block the EMERGENCY ROLLBACK path; threshold underived *(coding W2 ⊃ agentic A5)*
`rollback.sh:90` maps read failure ⇒ halt-for-human exit 78 — once the journal crosses the threshold the operator cannot run the pipeline's own rollback. Threshold default never stated (only implied 600 KB), no headroom derivation, no env-override name, no recovery runbook.
**Fix:** refuse on the **promote** path only; warn+continue on rollback/restart/status; state default + derivation + override; add trim/recovery runbook step + a risk row.

### W3 — Two breach producers emit different row shapes *(coding W3)*
`_enforce_deadline` journals `daemon-state=` (the field the runbook's first-recovery-step reads); the new `_budget_breach` shape omits it.
**Fix:** normalize; pin BOTH shapes in AC20 (or scope AC20 to L2a + add an L1 AC).

### W4 — Stale-count / single-file-era drift cluster *(agentic W2/W3/W9 + coding W4/W5)*
Corpus "15" vs 22+3 in phase1 T1.1/AC1, phase5 Pack A/AC2, phase0 "≥14"; "M1–M21" vs M1–M24; gate row "packs A–H" missing Pack I; phase0 §4.2 4-vs-5 extra-sites count; phase3 AC4/T3.4 single-file text contradicting D3-12 two-file sink; anchor-number drift (phase2 §2 vs §3.3 site numbers; D3-10 `:5569` vs actual `:5567`); task-ID style drift.
**Fix:** one mechanical sweep + a count-sweep gate step before the fresh approver.

### W5 — Staged verification cannot see the shell surface *(coding W6)*
Payloads ship no `scripts/` since v0.27.4, yet D1–D4 live entirely there; the plan never states the split.
**Fix:** add AC — staged gate covers daemon-side surface; shell surface verified from the pinned checkout at the synced rev; make `git rev` + `lib.sh` sha256 **required** arm-row provenance fields.

### W6 — `_run_bounded` blast radius; consumer census 7-not-5 *(coding W7)*
Consumers are **7** (incl. `:3844`/`:3874` `bash "$stop_script"` — side-effecting, omitted from both plan lists); modified-primitive-vs-new-variant unstated (modifying changes every existing consumer); `setsid` is a util-linux dependency with no portability statement.
**Fix:** state the side-effecting/kill-mode predicate table, the variant decision + 7-consumer re-verification, setsid assumption + fallback, corrected count.

---

## 🟢 ADVISORIES — nice-to-have

1. **M25 token-refusal mutation leg** *(agentic #7)* — fold into B3's pack addition.
2. **`ENSEMBLE_ALLOW_STALE_SCRIPTS` .env-durable persistence foot-gun** *(agentic #8)* — per-invocation only, or T6.7 runbook checklist item.
3. **phase1 §3.3 case-15 verdict cell** *(agentic #6)* — → "**accept (BASE)**".
4. **Helper single-read concurrency leg** *(coding A2)* — writer atomically replaces journal; assert no torn view.
5. **`_HALT_JOURNALED` post-completion TERM semantics** *(coding A3)* — first-row-wins, idempotent.
6. **Guard/journal-growth-fix coupling disclosure** *(coding A4)* — at stage adjudication + promote-time notes.
7. **Size-guard×fallback ordering + over-threshold×interpreter-absent matrix cell** *(agentic #5, subsumed by W2 remediation)*.

---

## PER-FOCUS-AREA COVERAGE

| # | Area | Verdict |
|---|------|---------|
| 1 | Completeness | **COVERED** (2/2; all deliverables have tasks+ACs in every phase) — with the P6 mechanism gaps (B1/B3) and W2/W5 noted |
| 2 | Feasibility | **P0–P5 SOUND** (2/2, code-verified); **P6 guard NOT implementable as written** (B1/B3) |
| 3 | Risks | **COVERED** (2/2; 25 rows right) — 4 missing classes folded into B1/W1/W2/W6/W-items |
| 4 | Test strategy | **STRONG** — SC1 wall-time gate, true-BASE differential, firing AND non-firing watchdog, SIGKILL log survival, stale-fixture guard leg all present; unpinned surfaces listed in W2/W3/W4/W5 + advisories |
| 5 | Scope discipline | **CLEAN** (2/2) — nothing required missing, no unjustified expansion; only borderline = W2's read-path refusal reach |

---

## OPEN QUESTIONS → leader rulings requested (reviewer-carried; recommendations attached)

1. **B1 floor ownership/mechanism** — daemon constant bumped in-commit vs allow-list (resolves the scheme choice among (a) SEQ / (b) content-addressed sha256 — council-preferred / (c) allow-list). *Any single orderable scheme satisfies B1; the review requires ONE to be stated with owner/storage/cadence.*
2. **Must the pinned dir be a git checkout?** Council recommendation: pinned-but-not-a-checkout ⇒ WARNING only (like the fallback case); sha256 provenance carries the burden.
3. **Rollback-path exemption owner (W2)** — council recommends the guard refuses on the promote path only; leader rule confirms.
4. *(Standing rule, not a question)* **Sibling lane (wi-3f5bea95) surface re-verification at implementation time** (RULING 10) — the "zero overlap" record is as-of-2026-10-29 against a base-sitting worktree; re-verify and record in the stage adjudication.

---

## RECOMMENDED NEXT ACTIONS (for the lane)

1. **One pre-implementation fold pass:** B1–B3 + W1–W6 + advisories (all doc-level; several remediations already concrete above).
2. **Scoped delta re-check of phase 6 only** (governor council or fresh approver) after the fold — the blocking items are concentrated where scrutiny has been least.
3. Then proceed to implementation P0→P5 as planned; the measured claims both councilors left un-re-executed are owned by Phase 0 re-measurement (T0.11–T0.13) — the correct disposition.

---

*Reviewer controller note: this file is the sole write authorized by the commission (plan files untouched; no commits; no live contact). Councilor reports preserved verbatim in the governor's conversation (governor `b9d9fe12-b0e7-4696-baf7-572931df449e`); council instances cleaned up, error flag cleared, manifest removed from shared KV (lane keys untouched).*


---

# DELTA RE-CHECK — fold round-3 (appended 2026-10-29)

**Scope:** phase 6 + the W-sweep surfaces + W2/W6 discharge + rulings verbatim-encoding + fold hygiene — per the council's recommendation #2. NOT a full re-review.
**Executed by:** `review-worker-preflight-delta` (worker `8bd8146c-5e76-4979-9f87-d0bf8b908f2a`, `plan-review` skill, fresh-eyes single-worker pass — one council per review; the delta is deliberately a worker pass as a rubber-stamp guard). Adjudicated and recorded by the reviewer controller.

## DELTA VERDICT: ✅ CLEAR-WITH-NOTES

**All three blockers (B1–B3) and the mechanism level of all six warnings are folded. No CRITICAL finding survives. The plan set is implementable as written and materially stronger than at council time.** Five warnings + five advisories remain — all documentation/consistency items with one-line remediations, no mechanism change required. CLEAR-WITH-NOTES rather than plain CLEAR because W1 carries a functional consequence and W3/W4 can misdirect an implementer.

### Verified PASS (mechanism level)
- **B1** — hybrid floor per ruling Q1: git-ancestry PRIMARY (`merge-base --is-ancestor`, pass iff ancestor; missing/unknown floor object or command error ⇒ REFUSE), non-git fallback via stamp, WARN-only non-git (Q2), owner/storage/cadence stated — `phase6-plan.md:93`, `:109-112`, D6-10 `:117-128`, D6-11 `:130-133`; Pack I non-git-with-stamp leg present (`phase5-plan.md:37`).
- **B3** — daemon opens `<scripts_dir>/lib.sh` at ARM time via bounded first-N-KB read, BEFORE the nonce burn (`phase6-plan.md:92`, T6.10 `:163`, AC10 `:184` incl. planted-implementation REDs); executor echo demoted to corroborating (`:92`); M25 pin present (`phase5:37`).
- **B2** — zero live literal-`1200` pins; all `<derived per D2-9>` (`phase2:111/:285/:316`, `phase5:36/:138`); AC17/AC15 single source of truth; grep-assertion gate step (`phase5:122/:146`).
- **W5** — T6.13/AC13 daemon/shell staged-verification split; `git rev` + `lib.sh` sha256 required arm-row provenance (`phase6:166/:187`).
- **W6** — 7-consumer predicate table with side-effecting/kill-mode columns incl. the previously-omitted `:3844`/`:3874` `stop_script` sites (`phase2:221-233`); NEW-VARIANT decision (`_run_bounded_pg`, shared primitive byte-unchanged, `phase2:234-239`); setsid fallback stated (`:241-244`); 7-consumer re-verification T2.24/AC24.
- **W2 (mechanism)** — Q3 promote-path-only refusal; rollback/restart/status WARN+continue (`phase1:59` D1-14); runbook step T1.15 `:146`; risk row R23 (`plan-overview:166`).
- **Rulings verbatim** — Q1 (`plan-overview:460-471` = D6-10 `decisions:515-528`; fallback clause discharged, not dropped, `:530-532`), Q2 (`:473-476` = D6-11 `:530-533`), Q3 (`:478-481` = D1-12 `:138-144`). No semantic paraphrase drift.
- **W4 sweep** — corpus 22+3 consistent set-wide; packs A–I complete; zero single-file-era remnants; anchor `lib.sh:5567` independently verified correct at tip (the BSD `stat -f '%m'` site; the original `:5569` was off by two).
- **Advisories** — Adv2 `ENSEMBLE_ALLOW_STALE_SCRIPTS` per-invocation-only (`phase6:96/:165/:186`); Adv6 guard×growth coupling at the council-specified home (`plan-overview §8 :231-238`).
- **Fold hygiene** — PASS: HEAD unchanged at base `b1183a637`, no commits, `git diff` empty, only the planning dir untracked; `evidence/FORENSICS.md`, `evidence/CODE-PATHS.md`, `architecture-recommendation.md` byte-identical (sha256-verified); `review-plan-council.md` untouched (sha `1a00a553…`); `phase4-plan.md` correctly untouched.

### Adjudication — mutation-span extension M1–M24 → M1–M28: **ACCEPTED**
Each new leg covers a distinct, separately-implementable branch the fold introduced — M25 (stamp-from-file, B3), M26 (non-ancestor pinned dir ⇒ RED), M27 (missing floor object ⇒ RED-must-refuse), M28 (non-git branch made to refuse ⇒ RED-must-warn-only). The house rule (regression pins MUST be mutation-verified) makes the extension required, not optional: without M25–M28 the newly-ruled B1/B3/Q1/Q2 mechanisms would ship with zero mutation coverage. Cost proportionate (+4 on 24); bookkeeping clean (`plan-overview §11.1 :376-380` provenance, `phase5:186-188` enumeration, count-sweep asserts the span).

### Residual items — doc sweep before/with the fresh approver (no mechanism change)

**🟡 W1** — Size-guard numeric default still unstated, and D1-15 contradicts T1.6: "order of magnitude below 455 KB" (⇒ ≈45 KB) vs "a 100 KB fixture does not trip" (⇒ >100 KB); the implied 600 KB default would NOT have fired on the 455,783 B incident journal — the guard must fire before the cliff. *Fix: pin one numeric default (assert explicitly < 455,783 B) across D1-15/D1-13/T1.6/AC7 + the boundary trio; reconcile phrasing.* **Highest-value residual — the only one with functional consequence.**
**🟡 W2** — The size-guard path scope (Q3) has NO named pack: zero hits for `size guard`/`JOURNAL_SIZE_GUARD_BYTES` in `phase5-plan.md`; a regression making the guard refuse on `rollback.sh` has no RED leg. *Fix: name the pack + attach promote-refuses + over-threshold×no-interpreter matrix legs; re-run the count-sweep (pack-set ripple A–J).*
**🟡 W3** — Superseded constant `MIN_PIPELINE_SCRIPTS_REV` survives at `phase6-plan.md:158` (T6.4) + `decisions.md:512` (D6-8) — an implementer reading T6.4 mints the wrong constant name (and the "min/ordering" mental model B1 was about). *Fix: replace with `PIPELINE_SCRIPTS_FLOOR_COMMIT`; leave `architecture-recommendation.md` frozen.*
**🟡 W4** — The invalid ordering predicate survives at 4 user-facing prose sites — `phase6-plan.md:23`, `phase6-plan.md:58`, `plan-overview.md:266`, `plan-overview.md:336` ("sync … to ≥ this lane's merge") — the operator-facing unblock instructions. *Fix: replace all 4 with the ancestry phrasing at `phase6:52-55`.*
**🟡 W5** — Cross-file `D1-*` ID collision (phase1 vs decisions, offset by 2), pre-existing but widened by the fold and now load-bearing: Q3 cited as `phase1 D1-14` (`plan-overview:455`) AND `decisions D1-12` (`decisions:594`); the colliding pair are the emergency-recovery exemption (safety-critical) and the single-read guard. *Fix: per-ID topic column in §11.1; normalise citations to one ID set.*
**🟢 A1** — False fold-log pointers "Pack A matrix cell" (`phase6:221`, `plan-overview:191`) — the cell is `phase1` T1.17/AC18. **A2** — Count-sweep gate rule (T5.17, 5 classes) is narrower than its own result record (§9, 6 classes incl. anchor class (f) + `M1–M24`): a gate re-implemented from the rule would under-assert; extend T5.17 + align AC17. **A3** — No gate class for superseded identifiers (the W3 residue survived because of this); add class (g). **A4** — `phase6:159` (T6.5) pre-B1 vocabulary "absent-or-older-than-floor"; align to §4.2 `:93`. **A5** — AC table ordering (AC8 listed last) — cosmetic.

### Next action
1. ~30-minute documentation sweep: W1–W5 + A1–A4 (one-line edits; W1 first).
2. Re-run the count-sweep gate with T5.17 extended per A2 (+ class (g) per A3 recommended).
3. **Fresh approver → implementation P0→P6.** The fold itself is sound; B1/B2/B3 properly discharged; floor mechanism, arm-time acquisition, Q3 exemption, 7-consumer blast-radius table, and derived RuntimeMaxSec are all implementable as written.

*Delta pass ground rules held: read-only except this file; no commits; no live contact (verified by the worker: read-only commands only, no live surfaces touched).*
