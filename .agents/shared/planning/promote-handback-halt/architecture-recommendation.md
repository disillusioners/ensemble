# Architecture Recommendation — `promote-handback-halt` (architect enrichment pass r1)

**Commission:** make the next promote halt-proof — incident `r-20261101-212542-8279` (v0.28.5: hand-back halt by seconds → serving-green wrongful AUTO-ROLLBACK → wrongful quarantine → re-promote refusal).
**Date:** 2026-11-02 · **Base tree:** `941cb6321` (== origin/latest tip) · **Worktree:** `/home/nea/ensemble-src-wt-promote-handback-halt`
**Review wave:** resilience-design (47ed7327) · data-flow-design (3d9c4a94) · structural-design (2d8f681f) — all anchors re-verified in-tree; live journal read read-only (27,302B / 157 entries).
**Status:** ✅ COMPLETE — amendments folded into all five plan docs as `[ARCHITECT r1]` sections; this document is the synthesis.

---

## 1. Verdict

**SOUND-WITH-AMENDMENTS.** The three-phase architecture (verifier rework → serving-green split closer → cooldown rider, one release v0.28.6) is the correct shape, complies with every binding ruling, and — after the r1 amendments — closes every wrongful-rollback and never-rollback path that is system-closable. Three 🔴 gaps in the drafted plan would each have re-created an incident-shaped failure; all three are closed by amendment. One register premise was factually wrong and is corrected. No disagreement with the leader's rulings remains.

## 2. The approved architecture (as amended)

A stale, already-flipped promote txn is resolved on **observed serving health** by three decision-makers sharing one disposition table, one journal-lock discipline, and one authorization predicate:

1. **Hand-back verifier (Phase 1)** — dedicated `HANDBACK_VERIFY_BUDGET_S` (default 300, `clamp(remaining − 120, 180, 300)`, no-clock ⇒ ceiling), real serving conjunct (port-listener necessary ∧ `/livez` 2xx ∧ version == expected, wrong-version fail-fast, house-classifier non-2xx taxonomy), degenerate branches preserved, one progress-gated grace now **deadline-aware** (`min(grace, remaining − tail)`). Halt-for-human stays for genuinely unconfirmable hand-backs (Amendment #1 intact).
2. **Split closer (Phase 2)** —
   - **Launcher** (purity R-08): `/dev/tcp` + bounded `read -t` probe, tri-state verdict, **not-positively-green ⇒ DEFER**; commit-close requires **probe GREEN ∧ intent_flip ∧ owner-dead**; rollback fires only on a **positive** unhealthy verdict or a `rollback_requested` disposition or the defer ceiling (ceiling itself eligible only on positive verdicts); `rollback_requested` check at **position 0** (T14). **[PLANNER r2]** This bullet's *eligibility* clause is **superseded by the bounded absent-ceiling** (Phase 2 §AM-P2-10): the ambiguous classes (no answer / wrong version / `/readyz` not ready) are no longer excluded from the rollback-eligibility set — they are admitted under a **bounded** path (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed), and the observation taxonomy becomes **four-way** (GREEN / BROKEN / AMBIGUOUS / request). **[PLANNER r2]** The canonical GREEN predicate also **includes `/readyz`** (leader ruling OQ-2, §AM-P2-11) — the launcher now issues a second GET over the same `/dev/tcp` fd.
   - **Daemon** (new lane in `sweep_once`): narrow predicate P1–**P9** (P9 = intent_flip via a new daemon history read), boot-grace, in-process health (version + cached composite; `composite-missing` ⇒ DEFER not unhealthy), lock `wait_s=0` + busy ⇒ no-action-this-tick + revalidate-under-lock; GREEN ⇒ commit-close with the **history-before-close** mutation order; NOT-GREEN ⇒ journals `rollback_requested` and nothing else. **[PLANNER r3]** — the daemon bullet gains the **grace conjunct** and its **own** boot-grace horizon (F-1, `phase2-plan.md` §AM-P2-24): the NOT-GREEN verdict additionally requires **`grace_active == False`** on the composite (the `GraceTracker` never changes `ready` — `daemon/services/readiness.py:757-760` — so **the gate decides to wait and the closer honours the same discipline**), and the boot-grace horizon becomes the named constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, **≥ `HANDBACK_VERIFY_BUDGET_S`**) instead of the borrowed 120 s pool-busy constant (`READINESS_GRACE_HORIZON_S`, `daemon/constants.py:165`). ⇒ **degraded-but-grace-held ⇒ DEFER, never `rollback_requested`** — the restored invariant: *`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.* The never-green direction is **untouched**.
   - **Adopt path (T11)** — same table, `lib.sh` classified probe + new `journal_history_has_intent_flip` reader.
   - **Quarantine clearance** — `quarantine_cleared_serving_green` on both sides, event-first (the event is the gate's acceptance signal ⇒ crash-proof), explicit alert entry (silence was the hazard; the prefix-collision hypothesis was **refuted** — exact dict lookup).
3. **Cooldown rider (Phase 3)** — epoch two-arm uname dispatch; `*)` arm ruled **(iii)-scoped fail-closed marker** (option (ii) refuted by the consumer code); property pins + the cross-component round-trip anchor.

**Canonical predicate (all three decision-makers, verbatim):** a serving-green commit-close fires only when **BOTH** hold: **(i)** a positive serving verdict for the txn's target — **GREEN** ⇔ the launcher obtained **`/livez` 200** ∧ the reported `version` equals the **v-stripped** target ∧ the launcher obtained **`/readyz` 200** whose body reports ready. The `/readyz` GET is a **second request over the same `/dev/tcp` fd** (no second connect, no second port race). A **wrong version fails fast** — it is not a "keep polling" condition. **Daemon side:** GREEN ⇔ `__version__` == the v-stripped target **AND** the cached readiness composite is not `None` **AND** `composite.ready` is True. — **AND (ii)** the journal history contains an `intent_flip` event naming that same target. Absent (ii) ⇒ launcher DEFER / daemon no-action-this-tick / adopt existing-chain — never commit, never immediate rollback. **[PLANNER r3]** *(doc-only correction — closes the pre-existing r2 drift recorded in §12.3; the OQ-2 predicate was adjudicated in r2 and is **NOT** reopened. The predicate sentences above are quoted **verbatim** from `phase2-plan.md` §AM-P2-11, which is itself restated at §AM-P2-4 and in the **T14** task row — this paragraph is the architect-facing restatement of that one chain, **not** a fourth source of the predicate. The *fail-fast* clause is **probe-level vocabulary only**: a version-mismatch **disposition** is **AMBIGUOUS** — counted and bounded — never an immediate rollback.)*

## 3. The three closed 🔴 gaps (each would have re-created an incident shape)

| Gap | Failure it would have caused | Amendment |
|---|---|---|
| **Crash window in the GREEN path** — drafted order closed the txn (`in_flight→null`) before history/quarantine-clear | A serving-green release **permanently quarantined** by a crash between writes — tonight's punishment shape surviving the fix; plus closed-txn-without-terminal-event ⇒ pipeline-busy | AM-P2-1 history-before-close invariant; clearance event appended before close (the event is the gate's acceptance signal); pin P-19 fault-injection over every crash prefix |
| **Defer-counter mechanism** — history-array count: cross-entry false positives + measured O(N²) (35.7s at 27KB; ≈285× at incident size; no launcher-side size guard) + "over-count is safe" doctrine inverted under the ruling (live `rollback_window_count` = 2/3 — one false rollback arms a 24h promote halt) | A wrongful rollback **caused by the fix's own counter** — the incident shape, self-inflicted | AM-P2-5: launcher-local `.sweep-defer-<run_id>` counter; ceiling eligible only on **positive** unhealthy verdicts; single history extraction per boot; launcher journal-size guard (breach ⇒ defer/halt, never rollback); pin P-20 (`≤ CEILING−1`) **[PLANNER r2]** — the clause *"ceiling eligible only on positive unhealthy verdicts"* is **amended** by the bounded absent-ceiling (AM-P2-10): the ambiguous classes now count toward the same bounded path under three conjuncts. The rest of the row (the counter home, the single extraction, the size guard, the pin) **stands**. |
| **intent_flip absent from D.1/T11** — only the launcher branch carried it | Authorization bypass on two of three decision-makers (a forged journal entry could commit-close an unauthorized flip via the daemon or adopt path) | AM-P2-4: P9 predicate + T11 conjunct + new lib.sh reader + canonical text + three-axis mutation-proven pin (leader ruling OQ-C-11) |

Plus one 🔴 **hidden consumer** (AM-P2-8): `serving_green_commit` ∈ `_TERMINAL_EVENTS` auto-grows `PROMOTE_WAKE_TERMINAL_EVENTS`, whose reader is run_id-blind — an unrelated commit-close would silently **drop a pending promote-wake**. Fixed by run_id-scoped tie-break (precedent `upgrade_journal_sweep.py:439-450`), pin P-21.

## 4. Register dispositions (25 OQs — shortlist detailed, remainder one-lined)

| ID | Disposition |
|---|---|
| OQ-C-01 | **RULED** (900 stays) + amended (deadline-aware grace; honest "only (a) fixes the arithmetic" disclosure stands) |
| OQ-C-02 | Confirmed (c) enforce-when-known; verified bare↔bare chain (no strip Phase-1); absent-version cell added (fail-fast) |
| OQ-C-03 | Confirmed preserve; operative rule restated; `lsof`-present+`curl`-absent cell enumerated (today's verdict) |
| OQ-C-04 | Confirmed (a) grace — now deadline-aware (AM-P1-3) |
| OQ-C-05 | Confirmed split (hand-back knobs shell-local; only a cap raise would be `.env`-scoped — moot under OQ-C-01(b)) |
| OQ-C-06 | **ADJUDICATED, premise corrected**: no promote-txn 6h reaper exists; tempfail-75/clean-exit-0 unbounded AND daemon-unreachable ⇒ T15 + runbook terminal backstop + disclosed residual (leader direction executed, lane not blocked) |
| OQ-C-07 | **ADJUDICATED — Option 2 confirmed**; seam = T14 (event contract, position-0, re-probe precedence, one chain, P-13..P-18); Option-1 rejection recorded with the flip-first reason; T15 composes |
| OQ-C-08 | Confirmed (a) vocabulary (owner-ratified name preserved; side-neutral commit event) |
| OQ-C-09 | Confirmed same-phase T11 (the operator's most-likely next walk) + intent_flip conjunct added |
| OQ-C-10 | Confirmed (b) + detail-names-state refinement (request vs executed) |
| OQ-C-11 | **RULED YES — folded** (P9 + T11 + lib.sh reader + canonical predicate + three-axis pin) |
| OQ-C-12 | Confirmed (b) waiver, upgraded to acceptance condition (unverified platform ⇒ deterministic NOT-GREEN ⇒ defer) |
| OQ-C-13 | **AMENDED** — counter re-homed off the history array (launcher-local file; measured O(N²) + cross-entry hazards); positive-verdict-only ceiling eligibility **[PLANNER r2 — the "positive-verdict-only" clause is superseded by the bounded absent-ceiling, AM-P2-10; the counter re-home stands]** |
| OQ-C-14 | Confirmed (a) + strengthened (history-before-close makes terminal membership crash-safe) |
| OQ-C-15 | Confirmed (a) lock+revalidate — T8 made explicit (wait_s=0, busy ⇒ no-action, re-read + predicate re-check) + fault-injection pin |
| OQ-C-16 | Confirmed Option A (leader may still prefer B — override documented) |
| OQ-C-17 | **RULED (iii)-scoped** — (ii) refuted by consumer code; lib.sh twin + date-fallback twins = named follow-up |
| OQ-C-18 | Confirmed launcher-only (R-08 + payload shape force it) |
| OQ-C-19 | Confirmed dedicated pack (+ carries the round-trip pin) |
| OQ-C-20 | Confirmed doc-only rider (+ the run-id typo rides with it) |
| OQ-C-21 | Closed (curl launcher-side is binding-rejected; verdict-not-transport is the shared contract) |
| OQ-C-22 | **RULED one release — confirmed + W1–W4 matrix** (W1 conditional on scripts-pin advance + same-commit floor) |
| OQ-C-23 | Confirmed (cooldown untouched on commit-close) + C5 re-shape must be parameterisation, not deletion |
| OQ-C-24 | **RULED no re-pin — confirmed** (base-red named on both legs) |
| OQ-C-25 | Settled (append-only; this pass complies) |

## 5. Amended phase task lists (numbered; T14/T15 new)

**Phase 1** (unchanged T-P1-1..11 +): AM-P1-1 two semantics cells + L6b extension · AM-P1-2 house-classifier alignment · AM-P1-3 deadline-aware grace · AM-P1-4 version-form chain statement.

**Phase 2** (unchanged T1..T13 +):
- **T14 — daemon→launcher rollback-disposition handoff**: `rollback_requested` history event (under journal lock, detail = target+run_id+reason) → `_js_history_has_rollback_request` conjunction reader → **position-0** check → re-probe (GREEN ⇒ commit-close per ruling; NOT-GREEN ⇒ existing chain byte-identical, detail `disposition=rollback_requested`). Pins P-13..P-18.
- **T15 — run-loop re-sweep hook**: re-run the idempotent lock-guarded `_journal_sweep` after each child exit; executes pending dispositions in the no-boot shapes; its own absent-observation is NOT ceiling-eligible.
- AM-P2-1 GREEN-path ordering (history-before-close; clearance event first) + P-19. **[PLANNER r4 → blocker 2]** this bullet was already correct; the r4 fold's work was to stop three **implementer-facing primary references** from contradicting it (`phase2-plan.md` §D.1 / §D.2 / T2, plus this register's D.2 row and §4.5 direction ①) by prescribing *"mirror `launcher.sh:1108-1127` exactly"* — **verified in-tree at the r4 fold: that range is the OLD close-before-history order** (`:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append) ⇒ mirroring it defeats AM-P2-10.3's journal check and reds P-19 / P3-PIN-23.
- AM-P2-2 owner-liveness conjunct on the launcher stale path (both branches).
- AM-P2-3 T8 lock discipline explicit.
- AM-P2-4 P9 + T11 conjunct + lib.sh reader + canonical predicate + three-axis pin.
- AM-P2-5 counter re-home + eligibility predicate + single extraction + size guard + P-20.
- AM-P2-8 vocabulary requirements (explicit alert entries; wake-reader run_id scoping P-21; two named test updates P-22; composite-missing ⇒ DEFER; quote-anchored clearance reader). **[PLANNER r4 → blocker 3] The "quote-anchored clearance reader" clause is SUPERSEDED** (append-only; the text above is retained for the audit trail): the quote-anchored shape matches the **`quarantined[]` array** (whose entries really are quoted — `lib.sh:1967`), **not** the history-detail string, where the version is an **unquoted** literal inside a JSON-escaped detail ⇒ a quote-anchored match on the history **never fires** and the clearance path would never lift the frozen refusal (tonight's punishment class reproduced). **The ONE pinned reader** is the **per-entry, delimiter-anchored** one in `phase2-plan.md` §D.3's contract box (event kind + delimiter-anchored `target $VERSION` + optional `run_id $RUN_ID`, all inside **ONE** entry) ⇒ prefix collision and cross-entry both closed. Pin: `P-36` / `P3-PIN-28`.
- AM-P2-9 probe acceptance (truncated-body ⇒ NOT-GREEN + mutation; wall-clock deadline in the body loop; exec-chain fact).
- P-7 split: P-7a launcher-only restart / P-7b full restart (daemon closer commits).

**Phase 3** (unchanged P3-T01..T12 +): AM-P3-1 OQ-C-17 (iii)-scoped + named twin follow-up · AM-P3-2 W1–W4 matrix + hard ceremony pre-step (scripts-pin advance; current pin target lacks the new floor) + W3 rollback gate (resolve open txns BEFORE rollback) + P3-PIN-18/19/20.

## 6. Scenario completeness (the six shapes, post-amendment)

| Shape | Actor → disposition | Terminal state |
|---|---|---|
| (i) Recovery-restart window (port down because this boot starts the daemon) | launcher DEFER → daemon closer commits at first tick past staleness+grace | commit-close; no punishment |
| (ii) Daemon-up-green at launcher boot | launcher commit-close (probe ∧ intent_flip ∧ owner-dead) | commit-close |
| (iii) Never-green crash-loop | burst-budget abort → unit restart → sweep re-runs (~10min) → **[PLANNER r2] the qualifying observations are AMBIGUOUS (no answer / the previous version / `/readyz` not ready) ⇒ the BOUNDED ABSENT-CEILING fires after ≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed (AM-P2-10)** ⇒ rollback **[PLANNER r3] ≈10–15min** (3 qualifying launcher observations × ≈3.5 min per boot in a `RestartSec=10` + ~200 s dying-boot environment; the earlier '≈30–40min' figure assumed a ~10 min-per-boot cadence, which conflated the **burst-budget window** (`BUDGET_WINDOW_S=600`) with the **boot cadence** — see `decisions.md` §OQ-C-06(d)). **[PLANNER r2]** *Before the r2 fold this row's mechanism did not exist in the ambiguous shape: the ceiling was eligible only on positive verdicts, so a crash-loop whose daemon was silent or answering the previous version rolled back **nothing** — the council's REJECT basis.* | rolled back, quarantined, counted (bounded ✓) |
| (iv) tempfail-75 | launcher stays (exempt); daemon down; T15 executes any `rollback_requested` left in an up-window; **[PLANNER r2] a tempfail-class (exit 75) child exit is NOT counted toward the absent-ceiling** — a persistent tempfail is normally *"PG unreachable?"*, an environmental failure the house burst budget **deliberately exempts** (`launcher.sh:1412-1414`), and converting it into a release rollback would repeat tonight's punishment class ⇒ if the daemon never boots again ⇒ **operator-bounded residual** (runbook: restart, terminal escape = manual `rollback.sh`)** | disclosed residual (leader-sanctioned; **narrowed** by the r2 exclusion, not widened) |
| (v) clean-exit-0 | run_loop exits 0; nothing restarts ⇒ operator-bounded residual (same runbook) | disclosed residual (leader-sanctioned) |
| (vi) Tonight's exact shape | P-7a/P-7b legs: launcher-only restart ⇒ launcher commits; full restart ⇒ launcher defers, daemon commits | commit-close; quarantine cleared; re-promote accepted |

Both composition directions remain pinned (P3-PIN-05/06/08): serving-green ⇒ stay; genuinely unhealthy ⇒ the existing never-green chain fires untouched.

**[PLANNER r3]** **And the boundary between them is now explicit.** The r3 fold closes F-1: the daemon closer's NOT-GREEN verdict is reachable only when the composite's **`grace_active == False`**, and its boot-grace horizon is its own constant **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, **≥ `HANDBACK_VERIFY_BUDGET_S`**), **not** the borrowed 120 s pool-busy constant. ⇒ **degraded-but-grace-held ⇒ DEFER**; the restored invariant is *`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.* The two directions are now pinned as a **pair**: `P3-PIN-27` (the defer leg) and `P3-PIN-11a` (the never-green leg, on a **not-grace-held** fixture). The never-green path itself is **untouched**.

**[PLANNER r2]** **Both directions remain pinned — and the second one now has a named mechanism.** The council's REJECT (2/2 converged, **scoped to Phase 2**) found that the second direction was true in *intent* but false in *mechanism*: the ceiling was eligible only on positive verdicts, so the canonical never-green shape (every observation ambiguous) rolled back **nothing**. After the r2 fold the second direction is armed by **two** paths — a **BROKEN** observation (`/livez` answered non-200) ⇒ the existing chain fires **immediately**; an **AMBIGUOUS** observation ⇒ the existing chain fires after the **bounded absent-ceiling** (≥3 qualifying observations ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed). The **single normative statement** is `phase2-plan.md` §AM-P2-10; the reconciled locations are listed in `decisions.md` §9.3. **[PLANNER r2]** New pins for the second direction: `P-23`/`P-24` (the ceiling + its stickiness), `P3-PIN-20`, `P3-PIN-26`.

## 7. New risks introduced by the amendments (and their mitigations)

- 🟡 **T15 is new launcher-loop surface** — mitigated: it re-runs the existing idempotent, lock-guarded `_journal_sweep`; its observation cannot fire a rollback (eligibility predicate); outside the 8b-r1 pin's extraction scope (verified).
- 🟡 **Wake-reader tie-break changes a shipped consumer** (`PROMOTE_WAKE_TERMINAL_EVENTS` lane) — mitigated: precedent exists (`:439-450`); pin P-21; alternative (exclude from wake set) documented if the tie-break is rejected at code review.
- 🟡 **Two existing test pins hard-redden** on the vocabulary growth — mitigated: named as plan tasks (P-22) so the gate reddens predictably, not as a surprise.
- 🟢 **Same-version re-promote overlap** (old process drains answering the same version) — benign, disclose-only.
- 🟢 **Port-squatter responder-blindness** — pre-existing in `gate_livez`, not widened; MainPID≠prestop conjunct retained.

## 8. Disagreements resolved at aggregation

1. **T14 write contract — history event (A) vs top-level field (B):** **event wins.** The field adds an `ensure_extensions` schema surface + reader-freshness problem on pre-existing journals + a second clear-write crash window; the event mirrors the proven intent_flip conjunction technique and is one atomic append. B's cost concern (35.7s history extraction) is solved by B's own mandates folded alongside: single extraction per boot + launcher size guard. B's "absent ⇒ no disposition, never spurious" degradation principle adopted verbatim for the event reader.
2. **"Over-count is safe" doctrine (plan) vs ruling:** the register's doctrine is **inverted** — the count gates the rollback itself, and at live `rollback_window_count` 2/3 a false count arms a 24h promote halt. Counter re-homed; ceiling eligibility restricted to positive verdicts.
3. **GAP-A1-3 fix — reorder (A) vs independent self-healing lane (A, alternative):** history-before-close ordering adopted (minimal, closes the window); the independent per-tick clearance lane recorded as optional hardening, not mandated (scope discipline).

## 9. Operational notes for the leader (read before the ceremony)

- **Live state is one rollback from the promote-halt cap**: `rollback_window_count` 24h = **2/3**; `cooldown_until` is a past stamp (zero-length — the rider defect, live-corroborated). Any further wrongful rollback before v0.28.6 lands refuses all promotes for 24h. This is urgency, not a blocker.
- **The v0.28.6 ceremony is covered only if the scripts pin advances** (current `.env` target `ensemble-src-wt-scripts-pin @ 22fe83317` predates the new floor). Without the advance the ceremony refuses pre-nonce (guard working; nonce unburned). With it, the ceremony itself runs the fixed verifier end-to-end (W1 conditional, AM-P3-2).
- **Before any rollback OF v0.28.6**: resolve open flipped/deferred txns first (W3 gate) — the old launcher's unconditional auto-rollback would re-create the incident for a deferred txn.
- **W4 interim (merge→promote)**: no new protection is live; decisions §6.1's claim to the contrary is corrected in §8.2.

## 10. Open questions remaining

None blocking. Named follow-ups (own lanes): the lib.sh unrecognized-platform twin + date-failure fallbacks; `journal_quarantine_clear`'s unanchored grep; bash-3.2 probe verification; RuntimeMaxSec cap-raise (only if stop-loss is hit in practice); journal history growth/rotation. The plan council and approver gates own the next review; every amendment above is evidence-cited to file:line in the `[ARCHITECT r1]` sections.

---

## 11. [PLANNER r2] FOLD ROUND 2 — the council REJECT (scoped to Phase 2) + three binding leader rulings — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r2]`; **no existing section, id, OQ, pin or task is renumbered or deleted**. New ids are appended in the phase files (`AM-P2-10`..`AM-P2-23`, `AM-P1-5`, `P-23`..`P-34`, `P3-PIN-18`..`P3-PIN-26`, `T14`..`T17`, `T-P1-12`, `L13`..`L15`, `M11`..`M13`, `AC-P1-17`..`AC-P1-19`, `S-11`..`S-13`, `R-13`..`R-17`).

**Note on §1's verdict.** §1 records the architect's *r1* verdict (**SOUND-WITH-AMENDMENTS**). The plan council's subsequent verdict was **REJECT, scoped to Phase 2** (Phases 1 + 3 approvable with folds) — see §11.1. §1 is **not** rewritten here (the architect owns it); this section records the council's verdict and the fold that answers it.

### 11.1 The council verdict and the fold that answers it

> **REJECT — scoped to Phase 2** (2/2 converged). **Basis:** `AM-P2-5`'s ceiling-eligibility predicate fired the defer-ceiling **only** on a positive-unhealthy observation or a `rollback_requested` disposition, **never** on an absent/port-down observation. In the canonical never-green shape (the daemon never serves ⇒ **every** launcher observation is absent) **nothing rolled back** — a regression against today's unconditional rollback and a violation of the ratified COMPOSITION REQUIREMENT. **At least four plan texts claimed "never-green stays armed" while the mechanism no longer armed it.**

**The fold's answer:** the **bounded absent-ceiling** (leader ruling OQ-1(a)) — `phase2-plan.md` §AM-P2-10, which is the **single normative statement** of the absent-only disposition across the five docs.

### 11.2 The three binding leader rulings, as folded

| Ruling | The folded form | Where |
|---|---|---|
| **OQ-1 — absent-observation disposition** | **(a) BOUNDED ABSENT-CEILING**: rollback eligible after **≥3 absent observations AND owner-dead AND `intent_flip` AND never-green-observed**. Option (b) (any-absent ⇒ rollback) **REFUSED** — it would rewrite an owner-ratified principle; the standing COMPOSITION REQUIREMENT decides the question. | `phase2-plan.md` §AM-P2-10; `decisions.md` §OQ-C-06(d-r2) and §4.5 |
| **OQ-2 — canonical GREEN** | The launcher probes **`/readyz` too** (a second GET over the same `/dev/tcp` fd); **wrong-version fails fast**; daemon side = version **AND** `composite.ready`. The user ruling's own vocabulary names *"unhealthy livez/readyz post-flip"*. The predicate sits **verbatim** in `AM-P2-4` **and** in **T14 step 1**. | `phase2-plan.md` §AM-P2-11 (and the amended `AM-P2-4` text + the `T14` task row) |
| **OQ-3 — cap interaction** | **Document-only; no exclusion mechanism.** Commit-closes **never** increment the rollback cap (they are not rollbacks); the cap stays frozen; a breach = **halt-for-human by design**. Documented, noting tonight's wrongful rollback consumed window budget (live at **2/3**). | `phase2-plan.md` §AM-P2-23 |

### 11.3 The mechanism-level finding that forced OQ-2 (verified in-tree at this fold)

`tests/helpers/soak_gate_fake_daemon.py` answers **`/livez` 200 in EVERY mode** — the `/livez` handler branch is unconditional and the mode degrades **only** `/readyz` (`:289-303` vs the mode table `:20-21`) — and `hard-red` is specifically *"/livez 200, /readyz 500"*. ⇒ **against the pre-ruling `/livez`-only launcher probe, `hard-red` is GREEN.** The direction-② pin would therefore not be discriminating against a livez-only implementation ⇒ the canonical GREEN **must** include `/readyz`. This is a mechanism-level fact, not a style preference, and it is pinned (`P3-PIN-25`).

### 11.4 The four-way observation taxonomy (the fold's core addition)

| Class | Definition | Disposition |
|---|---|---|
| **GREEN** | `/livez` 200 ∧ `version` == v-stripped target ∧ `/readyz` 200 ∧ ready (**AM-P2-11**) | commit-close (`intent_flip` required) |
| **BROKEN** | `/livez` answered **non-200** / unparseable body | the existing chain fires **immediately** |
| **AMBIGUOUS** | no answer at all · `/livez` 200 with a wrong version · `/livez` 200 + right version but `/readyz` not ready | **defer**, unless the **bounded absent-ceiling** predicate holds ⇒ the existing chain |
| **request** | the daemon's `rollback_requested` for this `(target, run_id)` | the launcher re-probes; GREEN ∧ `intent_flip` ⇒ commit-close (request superseded) · GREEN ∧ ¬`intent_flip` ⇒ **defer** (the R3 hinge) · NOT-GREEN ⇒ the existing chain |

**Why the taxonomy is four-way and not two-way.** The original GREEN / NOT-GREEN split collapsed three different situations into "not green": an affirmatively broken release, a release that has not come up yet (the boot window), and a release that is serving the *previous* version. Only the first justifies an immediate rollback; the other two are exactly the ambiguous cases the defer exists for — and they are the cases the canonical never-green shape presents.

**Documented extension (for the approver).** The leader's predicate names the **absent** class. The canonical never-green shape may also present as a **version mismatch** or as **`/readyz` not ready**. Excluding those two would have reproduced the council's REJECT in a narrower form ⇒ they are admitted into the same bounded tally under the same three conjuncts. The leader's predicate is honoured **verbatim** when the qualifying observations are absent; the strict reading is recorded and available.

### 11.5 The reconciled absent-only disposition — the five locations

| # | Doc | Location |
|---|---|---|
| 1 | `phase2-plan.md` | **§D.2 — the canonical ladder** (rung 3 ⇒ AM-P2-10), restated exactly once |
| 2 | `phase2-plan.md` | **the state/sequence matrix, row 4** |
| 3 | `phase2-plan.md` | **§(c) — Never-green crash-loop** |
| 4 | `decisions.md` | **§4.5 — direction ② (three triggers)** |
| 5 | `decisions.md` | **OQ-C-06(d-r2) — the leader's adjudication** |
| *(6)* | `architecture-recommendation.md` | **§6(iii)/(iv)** + **§2** (this document) |
| *(7)* | `phase1-plan.md` | the out-of-scope note naming the two mechanisms |

### 11.6 The amendments this round did **not** reopen (credited)

The architect's three gap-closures stand: **AM-P2-1** (history-before-close — the crash-prefix pin P-19), **AM-P2-5** (the counter re-home, the single extraction, the launcher size guard — only its *one* eligibility clause is amended), **AM-P2-4** (`intent_flip` on 2 of 3 decision-makers — now also on the request path). **OQ-C-11** (intent_flip both sides), **OQ-C-22** (one release), **OQ-C-24** (no seal re-pin), and the **OQ-C-06 premise** (no 6 h promote-txn `in_flight` reaper in-tree — both councilors grepped) are all **re-affirmed**, not re-litigated.

### 11.7 The `v0.28.5` note (carried)

`quarantine_cleared_serving_green` **cannot rescue `v0.28.5` itself** — it fires only while the target **is serving** green, and `v0.28.5` was rolled back off the live symlink (live `current` = `v0.28.4`, `quarantined` = `['v0.28.5']`). It stays quarantined and must be **re-staged** — which is this lane's delivery: **the content re-stages as `v0.28.6`**.

---

## 12. [PLANNER r3] FOLD ROUND 3 — F-1 (the surviving boundary defect) + the carried timing correction — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r3]`; **no existing id — row, section, OQ, pin, scenario or number — is renumbered or deleted**. This document carries **no new ids of its own**; the ids it references are appended in the other four docs (`phase2-plan.md` §AM-P2-24 / `T18` / `P-35` / `S-14` / `R-18` · `phase3-plan.md` `P3-PIN-27` / `P3-T13` · `phase1-plan.md` `R-P1-15`).

**Verdict context.** The plan council's delta pass returned **APPROVE-WITH-ADVISORIES**, *conditional* on folding **F-1** (one surviving 🔴 critical) plus **REV-R-1**..**REV-R-4**; **REV-R-5**/**REV-R-6** are trivial; one carried doc-hygiene item rides with this fold. The **leader confirmed the reviewer's F-1 remediation adjudication** — adopt **both (a)** and **(b)** plus the pin.

**Finding-id namespace (r3).** `REV-R-1` … `REV-R-6` are the **council's delta-pass finding ids, quoted from the review report** — they are **NOT** this plan set's risk ids (this plan set's risk series is the bare `R-n`). The namespace is introduced by this fold precisely so the two cannot collide (`phase2-plan.md` already carries risks `R-1`…`R-17`).

### 12.1 The r3 fold map (this document's share)

| Finding | Where | What changed |
|---|---|---|
| **F-1 (a) + (b)** | **§2 — the approved architecture**, the **Daemon** bullet | the daemon closer's NOT-GREEN verdict gains the **`grace_active == False`** conjunct, and the boot-grace horizon becomes the closer's **own** **`UPGRADE_CLOSER_BOOT_GRACE_S`** (default **300**, **≥ `HANDBACK_VERIFY_BUDGET_S`**) instead of the borrowed 120 s pool-busy constant (`READINESS_GRACE_HORIZON_S`, `daemon/constants.py:165`) ⇒ **degraded-but-grace-held ⇒ DEFER** |
| **F-1 — the restored invariant** | **§2** (the daemon bullet) + **§6**'s closing paragraph | *`rollback_requested` is writable ONLY when genuinely unhealthy OUTSIDE grace (`grace_active == False`); no path may write it during warmup.* — quoted identically in all five docs |
| **F-1 — the both-direction pin pair** | **§6**'s closing paragraph | the pair is named: `P3-PIN-27` (the defer leg) + `P3-PIN-11a` (the never-green leg, on a **not-grace-held** fixture) |
| **The planner's r3 adjudication — the §2 canonical-predicate restatement** | **§2** (the "Canonical predicate (all three decision-makers, verbatim)" paragraph) **+ §12.3** (the drift's closure record) | the **pre-r2** restatement (no `/readyz` conjunct, no `composite.ready`) is corrected to the **normative chain**, quoted **verbatim** from `phase2-plan.md` §AM-P2-11 with AM-P2-4's **(i)/(ii)** framing and the *fail-fast* **probe-level-vocabulary** caveat. **Doc-only — no id impact**: the OQ-2 predicate was adjudicated in r2 and is **NOT** reopened; the change is marked **[PLANNER r3]** and the drift's closure is recorded in §12.3 |
| **Carried doc-hygiene** | **§6(iii)** | the never-green crash-loop's rollback estimate is corrected to **≈10–15 min** (3 qualifying launcher boots × ≈3.5 min in a `RestartSec=10` + ~200 s dying-boot environment). The earlier **"≈30–40 min"** figure assumed a ~10 min-per-boot cadence — it conflated the **burst-budget window** (`BUDGET_WINDOW_S=600`) with the **boot cadence** and was **wrong**; the same correction is carried in `decisions.md` §OQ-C-06(d) and `phase2-plan.md` matrix row 4 |

### 12.2 What this fold does NOT change

- The **three decision-makers**, the **one disposition table**, the **one lock discipline** and the **one authorization predicate** (§2's opening statement) are **unchanged**.
- The **never-green protection is untouched**; the **path set is untouched** (**BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded** — there is still no fourth path); the **launcher-side request gate is unchanged** (F-1 constrains the **writer**).
- The scenario table's six shapes (§6(i)–(vi)) keep their dispositions; only shape **(iii)**'s **timing** is corrected, and shape **(i)** (the recovery-restart window) gains the explicit statement that a degraded-but-grace-held composite **defers** rather than triggers a verdict.

### 12.3 The anchor drift found at this fold — **CLOSED by the planner's r3 adjudication** (doc-only)

**The drift (as first observed).** This document's **§2 "Canonical predicate (all three decision-makers, verbatim)"** paragraph restated the **pre-r2** predicate text (*"`/livez` 2xx, version == v-stripped target"*) and did **not** name the `/readyz` conjunct that the r2 fold added (**AM-P2-11**, leader ruling OQ-2) — while the normative chain itself (`phase2-plan.md` §AM-P2-11, restated at §AM-P2-4 and in the **T14** task row, and recorded in `decisions.md` §9.2) was correct. ⇒ a **stale restatement in the architect-facing summary**, not a defect in the normative chain.

**[PLANNER r3] The adjudication and the correction.** The **planner ruled (r3)**: fold the correction **now** — it is a **one-paragraph, doc-only** change with **no id impact**; an implementer reading §2's paragraph would otherwise implement the **wrong (pre-OQ-2)** predicate — no `/readyz` conjunct, no `composite.ready` — which is exactly the landmine class the fold discipline exists to prevent. ⇒ **§2's paragraph is now byte-consistent with the normative chain**: the predicate sentences are quoted **verbatim** from `phase2-plan.md` §AM-P2-11, the **(i)/(ii)** framing is AM-P2-4's, and the *fail-fast* clause carries the same **probe-level-vocabulary** caveat. **OQ-2 was adjudicated in r2 and is NOT reopened** — the blame line is preserved by this change being marked **[PLANNER r3]** and recorded here; **no pin, task, scenario or risk id is added or changed** (the change is text-only).

**Status: CLOSED.** The §12.1 fold-map row for this change is the record; this entry is the audit trail for the drift and its closure.


---

## 13. [PLANNER r4] FOLD ROUND 4 — the fresh approver's iteration-001 REJECT (4 blockers) + 12 carried notes — 2026-11-02

**Fold mode:** additive (Mode 2). Every change is marked `[PLANNER r4]`; **no existing id is renumbered or deleted**. This document is the **architect-facing synthesis** — it is not an implementer-facing primary reference, so the r4 fold touches it only where it (a) **repeats a superseded prescription** or (b) must **record the fold's outcome** for the next reader.

**Verdict context.** The fresh approver **REJECTED iteration 001** (2/2 workers), with **4 blocking issues**; both verifiers recorded that **all four are doc-level folds over content that already exists correctly elsewhere in the plan set — no new design work is implied**. The leader adjudicated all four **ACCEPTED**, binding this fold: *implementer-facing primary references must never prescribe superseded mechanics*. **12 non-blocking notes** rode with the same fold.

### What this document already had right (and therefore did not need changing)

| # | The approver's blocker | Why this document was already conformant |
|---|---|---|
| **BLOCKER 1** (rung-1 authorization conjuncts) | `phase2-plan.md` §D.2 rung 1 carried only the serving clause | §2's launcher bullet already reads *"commit-close requires **probe GREEN ∧ intent_flip ∧ owner-dead**"* and §2's canonical-predicate paragraph already carries clause **(ii)** verbatim with *"Absent (ii) ⇒ launcher DEFER / daemon no-action-this-tick / adopt existing-chain"*. ⇒ the defect was **local to phase2's rung 1**, not to the architecture |
| **BLOCKER 2** (inverted commit-close order) | three phase-2 sites prescribed *"mirror `launcher.sh:1108-1127` exactly"* | §2's daemon bullet already says *"GREEN ⇒ commit-close with the **history-before-close** mutation order"* and §3's first gap row already names AM-P2-1's history-before-close invariant. ⇒ the defect was **local to the implementer-facing sites**, not to the architecture |
| **BLOCKER 3** (clearance reader/writer contract) | §D.3 vs AM-P2-8 prescribed two incompatible readers with no pinned writer format | §2's quarantine bullet already says *"on both sides, event-first (the event is the gate's acceptance signal ⇒ crash-proof), explicit alert entry"* — the **event-first** half was right; only the *reader shape* was unspecified/contradictory |
| **BLOCKER 4** (the register teaching superseded content) | `decisions.md` D.6 / OQ-C-13 / §5.1 were un-annotated | §3's defer-counter gap row already records the **inverted** *"over-count is safe"* doctrine and the launcher-local counter home, and §3's row already carries the r2 amendment of the ceiling-eligibility clause ⇒ the register's **arch-facing** summary was correct; its **primary tables** were not |

### The r4 fold map (this document's share)

| # | What folded | Where (this file) | What changed |
|---|---|---|---|
| **BLOCKER 3** | §5's Phase-2 task list repeated AM-P2-8's *"quote-anchored clearance reader"* | **§5, the AM-P2-8 bullet** | **SUPERSEDED** (append-only): the quote-anchored shape matches the **`quarantined[]` array**, not the history-detail string ⇒ it would **never fire** (tonight's punishment class reproduced). The **one** pinned reader is the **per-entry, delimiter-anchored** reader in `phase2-plan.md` §D.3's contract box. Pin `P-36` / `P3-PIN-28` |
| **BLOCKER 2** | §5's AM-P2-1 bullet was already correct, but the doc carried no record that three implementer-facing sites contradicted it | **§5, the AM-P2-1 bullet** | a short annotation records the r4 correction and the **in-tree verification** that `launcher.sh:1108-1127` is the OLD close-before-history order ⇒ the fold's scope is now visible from the synthesis |
| **note (c)** | the boot-grace constant's home | **§2, the daemon bullet** | the constant's **one** declaration home is now stated consistently across the set: **`daemon/services/upgrade_journal_sweep.py`** beside the other sweep-local constants (`:96-115`; verified `:99`/`:105`/`:112`) — **not** `daemon/constants.py` (that module is cited only as the site whose `READINESS_GRACE_HORIZON_S` at `:165` is **not** borrowed). The constant is **daemon-side only**; there is no shell counterpart |
| **note (l)** | the bounded ceiling vs the commission's literal *"NEVER rollback"* | **§12's successor note below** | the ceremony-gate surfacing is recorded here as well as in `decisions.md` §4.5/§6.3 and `phase2-plan.md` |

### The reconciled dispositions after the r4 fold (the architect-facing summary)

| Shape | Launcher | Daemon | Adopt | Outcome |
|---|---|---|---|---|
| stale flipped txn, target **GREEN**, `intent_flip` present, owner dead | **commit-close** (history-before-close order) | commit-close | commit-close + promote continues | **STAY** — no rollback, no quarantine, no cap increment, no cooldown |
| stale flipped txn, target **GREEN**, **no** `intent_flip` | **DEFER + journal** + operator surfacing (the R3 hinge) | no-action-this-tick | the **existing chain** (the documented divergence, `phase2-plan.md` §D.4 (a-i)) | **no commit** (unauthenticated), **no rollback** (serving-green) — the operator-bounded residual family |
| stale flipped txn, target **BROKEN** (`/livez` non-200 / unparseable) | the **existing** chain, immediately | `serving_unhealthy` + `rollback_requested` | the existing chain | **rollback** — the never-green protection, path 1 |
| stale flipped txn, target **AMBIGUOUS** (no answer / wrong version / not ready) | defer + count → the **bounded absent-ceiling** (≥3 qualifying ∧ owner-dead ∧ `intent_flip` ∧ never-green-observed) | the same predicate (its own verdict is the accelerator) | the existing chain | **bounded** rollback — path 3 |
| stale flipped txn, daemon wrote `rollback_requested` | re-probe: GREEN ∧ `intent_flip` ⇒ commit-close (request superseded) · GREEN ∧ ¬`intent_flip` ⇒ defer · else the existing chain | — | — | path 2 |

### In-tree re-verification performed at this fold (base `941cb6321`)

| Claim | Verdict | Evidence |
|---|---|---|
| `launcher.sh:1108-1127` is the OLD close-before-history order | **CONFIRMED** | `:1110` current → `:1118` previous → `:1120` `in_flight`→null → `:1122` history append |
| the launcher's bullet already carried all three commit conjuncts | **CONFIRMED** | §2's launcher bullet: *"commit-close requires **probe GREEN ∧ intent_flip ∧ owner-dead**"* |
| the daemon bullet already carried the history-before-close order | **CONFIRMED** | §2's daemon bullet: *"GREEN ⇒ commit-close with the **history-before-close** mutation order"* |
| the defer-counter gap row already recorded the inverted doctrine | **CONFIRMED** | §3's row: *"'over-count is safe' doctrine inverted under the ruling (live `rollback_window_count` = 2/3 — one false rollback arms a 24h promote halt)"* |
| `lib.sh` has **no** `intent_flip` reader | **CONFIRMED** | `grep -n intent_flip scripts/upgrade/lib.sh` ⇒ **0 hits** |

### What this fold does NOT change

- The **approved architecture** (§2) is unchanged — the three decision-makers, the one disposition table, the one lock discipline, the one authorization predicate.
- The **canonical predicate** is unchanged in substance; the r4 fold adds **authorization** conjuncts at the implementer-facing sites, not new health semantics.
- The **never-green protection** is untouched — the path set is still **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**.
- The **owner ruling** is unchanged: serving-green ⇒ stay; rollback is reserved for genuinely unhealthy serving.
- The **frozen quarantine rule** is unchanged — extended by one explicit journaled event, never bypassed.

---

## [PLANNER r5] SWEEP RECORD — the systematic claim-class sweep found **no fold required** in this document

**Fold mode:** additive (Mode 2). **Zero content changes were made to this document by the r5 fold** — and that is the finding, not an omission: every occurrence of each of the eight swept claim-classes in this file was enumerated, and **all of them are already correct**. The record below states **why** each is already correct, so the enumeration is auditable rather than asserted. (This document is architect-owned; the r5 fold therefore **records** here and **folds** in the worker-authored files.)

| Claim-class | Hit sites in this file | Disposition | **Why it is already correct** |
|---|---|---|---|
| **(a)** predicate/closer restatements missing the P9/`intent_flip` authorization conjunct | **§2's Daemon bullet** (`:21`) | **ALREADY CORRECT** | the bullet already reads *"narrow predicate P1–**P9** (P9 = intent_flip via a new daemon history read)"* — the daemon-side authorization conjunct is **in the approved architecture itself**, and §5's reconciled-dispositions table's *GREEN / no-`intent_flip`* row already says the daemon takes **no-action-this-tick**. The r5 fold's work was to make that conjunct visible in the **implementer-facing** files (§D.1's table, `T8`, `P-6`, `P3-PIN-05/-11/-11a`, `D.1`), which this document already stated |
| **(b)** close-before-history order claims | **§5's AM-P2-1 bullet** (`:75`) · **§7's BLOCKER-2 rows** (`:235`, `:244`) · **the in-tree re-verification row** (`:262`) · **§2's daemon bullet** (`:264`) | **ALREADY CORRECT** | the r4 fold already recorded that *"this bullet was already correct; the r4 fold's work was to stop three phase-2 sites from contradicting it"*; `:262` records the **CONFIRMED** in-tree verification that `launcher.sh:1108-1127` is the OLD close-before-history order; `:264` records that §2's daemon bullet already carried the **history-before-close** order. **All four are r4 records, not prescriptions** |
| **(c)** pre-r2 firing-rule text | **§6's REJECT-basis paragraph** (`:138`) · **§6(iii)** (`:92`) | **ALREADY CORRECT** | `:138` is the **council's REJECT basis as a record** (the basis is quoted *because* it was rejected — it is history, not a prescription); `:92` already carries the r2 correction (the observations are **AMBIGUOUS**, the bounded ceiling applies) and `:205` records the ≈10–15 min timing correction |
| **(d)** history-count counter claims | **§5's AM-P2-1 bullet** (`:75`) · **§7's BLOCKER-2/4 rows** | **ALREADY CORRECT** | `:75` already records the **defer-counter re-home** (launcher-local file; the drafted history-count's measured **O(N²)** cost and cross-entry false-positive class) — i.e. this document already states the **post-r2** mechanism, not the superseded one |
| **(e)** launcher-primary precedence | — | **NO HIT** | this document's reconciled-dispositions table and §6 already treat the daemon closer as the in-daemon disposer and the launcher as the pre-daemon backstop |
| **(f)** superseded timing | **§6(iii)** (`:92`) · **the carried doc-hygiene row** (`:205`) | **ALREADY CORRECT** | `:205` records the **≈10–15 min** correction and names the *old* figures as corrected; `:92` carries the r2 annotation |
| **(g)** unscoped adopt-parity | **§7's reconciled-dispositions table** (`:253` and the row below it) | **ALREADY CORRECT** | the table has an **explicit `intent_flip` dimension**: the *GREEN / no-`intent_flip`* row states **launcher DEFER · daemon no-action-this-tick · adopt the existing chain** with the outcome *"no commit (unauthenticated), no rollback (serving-green)"* and cites `phase2-plan.md` §D.4 (a-i) as the documented divergence. **This is the reconciliation the r5 fold propagated back into `phase2-plan.md`'s R-8, `phase3-plan.md`'s `P3-PIN-12` and `decisions.md` §5.1's `P3-PIN-12`** — the doc was right; the three worker-authored sites were the ones that lagged |
| **(h)** "recommended" alert entries vs REQUIRED | — | **NO HIT** | this document does not restate the alert-map entries; the requirement lives in `phase2-plan.md` AM-P2-8/AM-P2-19 and `decisions.md` D.7 (both **REQUIRED**) |

**One architectural observation the r5 sweep produced (recorded, not folded — it changes no design):** the plan set's **implementation-facing** files had drifted from this document's **approved architecture** on the authorization conjunct (class a) and on the adopt-path parity (class g) — in both cases the drift ran in the **unsafe** direction (a daemon closer that could commit on health alone; a shared-polarity pin that could not hold for the unauthorized-flip fixture). The r5 fold's value is therefore exactly the kind the architecture doc deserves: **the design was right; the sites that implement it were not all reading it.** The r5 fold's job was to carry the architecture's own words down to the implementer-facing sites, not to change the architecture.

### What this fold does NOT change

- The **approved architecture** (§2) is unchanged — the three decision-makers, the one disposition table, the one lock discipline, the one authorization predicate.
- The **canonical predicate** is unchanged in substance; the r5 fold adds **visibility of an existing conjunct**, not new health semantics.
- The **never-green protection** is untouched — the path set is still **BROKEN ⇒ immediate · `rollback_requested` ⇒ immediate · AMBIGUOUS ⇒ bounded**.
- The **owner ruling** is unchanged: serving-green ⇒ stay; rollback is reserved for genuinely unhealthy serving.
- The **frozen quarantine rule** is unchanged — extended by one explicit journaled event, never bypassed.
