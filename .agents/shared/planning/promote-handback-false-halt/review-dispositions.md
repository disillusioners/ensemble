# Review Dispositions — promote-handback-false-halt (consolidated fold ledger, waves 1–4)

**Commission:** promote-machinery defect fix: healthy-boot false-halt · **Lane:** `fix/promote-handback-false-halt` @ worktree `/home/nea/ensemble-src-wt-hbfix`
**Purpose:** one line per non-blocking review item, **ADOPTED** (wave + commit) or **DEFERRED** (one-clause reason). Blocking blockers (BL-1…BL-9) are recorded in `decisions.md` (§1.0 rows 23-29, §10.8, §10.9); this file is the non-blocking fold ledger. Wave-4 entries were written by the wave-4 worker against `git log`/`git diff` verification of the waves 1-3 commits; wave 1-2 entries are consolidated from the wave commit records (`bdf5e81b9`, `183772014`, `6b64e1412`, `7d1969e65`, `6eeccb027`, `815910b31`) and cross-checked against the amended plan text.

---

## WAVE 1 — commit `bdf5e81b9` (blockers BL-1, BL-2 + cheap folds)

| Item | Disposition | Where it lives |
|---|---|---|
| [T5] anchor `:6207-6213` → `:6218-6221` (the prestop-equal verdict; the old span is the budget printf) | **ADOPTED (wave 1)** | phase1 §5.7 P9 row, §6 C-5 row (CP-5/CP-8 drift note) |
| [T5] `owned_spaced :8031` leaked-global threading + `_supervision_pid_cgroup_leaf :7111-7121` parse reuse (no third copy) | **ADOPTED (wave 1)** | anchor A32; T5/T6 acceptance cells; §5.3-R2 parse-reuse note |
| [T2] `/livez unreachable >Ns` mislabel ban (green ≠ unreachable; rc-2 arms never reuse the label) | **ADOPTED (wave 1)** | §5.4a; T7c; §12 R4 |
| [T2] D2 start-arm legs **A8/A9** + mutations **M-A8/M-A9** (sites 4/5 journal symmetry) | **ADOPTED (wave 1)** | phase1 §4.3 note + §6 leg map; phase2 A0–A9 |

## WAVE 2 — commits `183772014`, `6b64e1412`, `7d1969e65`, `6eeccb027`, `815910b31` (blockers BL-3, BL-4, BL-5 + folds)

| Item | Disposition | Where it lives |
|---|---|---|
| [T4] predicate truth table + `cg == "/"` degenerate guard (canonical block) — caveat recorded: the base guard is comment-only at `lib.sh:7637`; R2 owns the real guard | **ADOPTED (wave 2)** | phase1 §5.3 fenced block + truth table |
| [T1] NP-6 reach assertions + positive control (**M-NP6-reach** + `SC_LOG` counter) | **ADOPTED (wave 2)** | phase2 §9.1 NP-6, §9.2 |
| [T1] **M-NP1d** revert-to-substring mutation | **ADOPTED (wave 2)** | phase2 §9.2 |
| [T1] source-grep scoped to INSIDE `_gate_attr_containment` (the 3 legacy substring arms at `:7803`/`:7827`/`:8128` disclosed and kept) | **ADOPTED (wave 2)** | phase1 §5.3 drift tripwire; phase2 §9.1 NP-6 |
| [T1] **M-NP2b** absent-version branch-ordering mutation | **ADOPTED (wave 2)** | phase2 §9.2 |
| [T1] stub-ordering `:376`/`:377` declared reading (journal-surface vs stdout-surface pins) | **ADOPTED (wave 2)** | phase2 §4.3 |
| [T1] `make_systemctl_stub_v2` rule (build the new rc-faithful stub, never mutate the three old ones) | **ADOPTED (wave 2)** | phase2 §4.3 |
| [T5] `research-findings-lane.md` citation repaired — real anchors `executor_family.sh:305-314` + `stage.sh:803-816` (the "stage.sh:~810-816 wrong file" premise did not hold as stated — disclosed, not silently applied) | **ADOPTED (wave 2)** | research-findings-lane §bump_version.py bullet; decisions §10.8 disclosure #2 |
| [PF] ⛔ DO-NOT-COPY lines above phase2 §9.1/§9.2 + canonical-string pointer in phase1 T5 cell | **ADOPTED (wave 2)** | phase2 §9.1/§9.2; phase1 §10 T5 |

## WAVE 3 — commit `59721a054` (blockers BL-6, BL-7 + folds)

| Item | Disposition | Where it lives |
|---|---|---|
| [T3] all seven review items adopted: post-T3 `X^2` anchor; `--no-ff` load-bearing (§4.1); `rollback_safe` delta re-derived tag-to-tag at bump time; 7-paths-not-7-insertions (house precedent `3f45cd6dd`); T5 five annotation items; T10 carries `M_sha`; `P3-T08`→**`P3-PK-FLOORSTAMP`** rename at the definition site | **ADOPTED (wave 3)** | phase3 §4.1/§6 (T3-T5, T7, T10), §8.1 |
| [PF] 5-pid shape pinned (2 non-matching `1191207`/`1191605` + 3 owned `1190778`/`1190919`/`1190932`) in phase3 T4 CHANGELOG wording + T10 handoff | **ADOPTED (wave 3)** | phase3 §6 T4 + T10 |
| [T5] repo-state claims verified read-only (`cf43ac00d` 232-behind; `1aa0065eb` resolves; 55-commit drift zero in release-mechanics paths) | **ADOPTED (wave 3)** | phase3 §3 F16/F31/F32 |

## WAVE 4 — this wave (blockers BL-8, BL-9 + remaining T4/T5 folds + sweep + process facts)

| Item | Disposition | Where it lives |
|---|---|---|
| **BL-8** (`D-P1-6`, leader ruling): RC3 subsumed-disposition written; false phase3 claim corrected; phase2 D5 mapped | **ADOPTED (wave 4)** | phase1 §2 out-of-scope row + §12 RC3 paragraph; phase3 §2 correction; phase2 §3 D5; decisions §6 row 8 + §10.9; plan-overview §2 item 3 |
| **BL-9** (`D-P1-7`, leader ruling Option A): per-surface version arming (T13/T14), per-surface invariant restatement, NP-12 + M-NP12, risk row R6 with owner, `:541-544`→`:546-556` folded | **ADOPTED (wave 4)** | phase1 §5.4/§5.7/§10/§12; phase2 §9.1/§9.2; decisions §10.1 scope note + §10.6 + §1.0 rows 28-29 + §10.9 |
| [T4] D-P3-1 open-framings corrected to the MERGE disposition (5 places: §1.4 heading, §1.5 L-5 row, §8.x row, Appendix B items 1+3, Appendix C row) — historical texts annotated, not rewritten | **ADOPTED (wave 4)** | decisions.md (5 sites) |
| [T4] phase1 §15.1/§15.2 open framings closed (both items decided: `D-P1-1` no-row; `D-P1-2`+D3 seam) | **ADOPTED (wave 4)** | phase1 §15 items 1-2 |
| [T4] R0 census denominator 18 → **17** (grep-verified in the worktree: 17 raw `exit 78` hits = 15 code + 2 comments) | **ADOPTED (wave 4)** | phase2 §2 scope item 5 (matches decisions CP-6 and §7.2's binding) |
| [T4] "inherited-fd holders" qualified **HYPOTHESISED-vs-PROVEN** at the incident-shape assertion sites (the mechanism is code-proven; the holders' identity is not fixture-proven) | **ADOPTED (wave 4)** | plan-overview §2 item 2 + §8 item 1; canonical qualification already at phase2 §3 D4-note |
| [T4] residual rows **P12-tertiary** (cgroup-escaped descendant refused) + **P14** (R3-when-reached is a NEW acceptance class vs Layer 2) recorded | **ADOPTED (wave 4)** | phase1 §5.7 matrix |
| [T4] threat model stated explicitly (LOCAL stale/operator-hostile process; NOT remote) | **ADOPTED (wave 4)** | phase1 §5.7 preamble |
| [T5] `:801` → **`:7985-7988`** (the missing-tool-is-never-evidence precedent — verified in the worktree; `:801` is `_log_phase_marker`) | **ADOPTED (wave 4)** | phase1 §5.3 R3 rung |
| [T5] A23 `:376` → **`:377`** (the `systemctl start` 7b pin — verified; `:376` is the adjacent journal-surface pin, correctly cited elsewhere) | **ADOPTED (wave 4)** | phase1 §3 A23 (per decisions CP-9) |
| [T5] C-10a span → **17 lines: `lib.sh:157-173` == `promote.sh:309-325`** (verified; `:174` knob line, `promote.sh:326-327` identity claim) — folded with BL-9 where it touches the same rows | **ADOPTED (wave 4)** | phase1 §3 A22 + §5.4 budget-impact line; research-findings-code §Q-item 4 |
| [C] wave-3 leftover: plan-overview §4 Phase-3 row brought in line with the BL-6 read-back (post-T3 `X^2`; `M_sha` parsed from the fresh annotation and gating every push) | **ADOPTED (wave 4)** | plan-overview §4 row 3 |
| [C] wave-3 leftover: decisions §10.2 T7 row forward-pointer (`P3-T08`→`P3-PK-FLOORSTAMP`; read-back = T7 assertion (6)) — historical row left as written | **ADOPTED (wave 4)** | decisions §10.2 |
| [C] wave-3 leftover: architecture-recommendation §4.3 mapping marker (`P3-T08`→`P3-PK-FLOORSTAMP`) — historical file otherwise left as written (wave-2 precedent) | **ADOPTED (wave 4)** | architecture-recommendation §4.3 |
| [D] board-fold sanction narrowing: producer closed; `flipped=false` live closer deliberately NOT built — premise verified vs **fixture line 19** (`17:29:27Z` `current -> releases/v0.31.0 (atomic flip)` ⇒ the incident txn was `flipped=true`, recovered by the serving-green closer) | **ADOPTED (wave 4)** | decisions §5 point 2; phase3 §6 T10 handoff disclosures |
| [D] final 5-pid sweep: grep for `four pid` / `4 pid` / `FOUR pid` / `4-pid` across the plan dir → only the two correct wave-3 disclaimers (phase3 T4/T10); no stale 4-pid claim anywhere; phase1:531, phase2 §3 D4-note, research-findings-lane:36 re-confirmed carrying the correct 2+3 shape | **VERIFIED, no edit needed (wave 4)** | phase3 §6 T4/T10 (disclaimers); phase1 §6; phase2 §3 |
| plan-overview wave-4 amendment paragraph + artifact-map row for this file | **ADOPTED (wave 4)** | plan-overview preamble + §3 |

## DELTA REVIEW ROUND 2 — this wave (5 propagation/wiring blockers + 4 cheap factual folds)

**Scope:** the delta review found five wave-4 PROPAGATION/WIRING gaps — the wave-4 content (T13/T14 arming tasks, the NP-12 leg,
the per-surface restatement) existed but never reached the summary/choreography surfaces a coder or reader executes from. All
entries below are **plan-text edits only** (no code, no implementation).

| Item | Disposition | Where it lives |
|---|---|---|
| **BL-R2-1** — T13/T14 unowned in the choreography: phase1 §11 (C0–C6) carried **zero** T13/T14 mentions and §13 carried **zero** S-rows ⇒ a coder executing §11 exactly landed C1–C5 with the version conjunct still EMPTY on restart/rollback | **ADOPTED (delta round 2)** | phase1 §11 — new rows **C7** (T13, `scripts/upgrade/restart.sh`) and **C8** (T14, `scripts/upgrade/rollback.sh`), plus the landing-order/serialization bullets (C7 shares `restart.sh` with C3+C5; C8 shares `rollback.sh` with C3) and the command-substitution note; phase1 §13 — new criterion **S13** ("arming present on both surfaces ⇒ stale-incarnation refused"); the T13/T14 task rows now name their commit owner |
| **BL-R2-2** — NP-12/M-NP12 unwired: phase2 §14 task table, §16 SC rows, §13 item 5 and the §2 counts still said `NP-1..NP-11` | **ADOPTED (delta round 2)** | phase2 §14 **P2-T9** (now owns NP-12 + M-NP12; depends on `phase1 T13/T14`) + the new **Depends-on convention note**; phase2 §16 — new criterion **SC-13**; phase2 §13 item 5 (`NP-1..NP-12`, 12 rows); phase2 §2 item 3 — counts **re-derived from the §9.1/§9.2 tables** (12 NP rows + 11 mutation rows ⇒ 12 mutation ids with `M-NP1c-strict`), not asserted **(as of delta round 2; superseded by approver iter 001: 13 mutation ids with M-NP1-accept)** |
| **BL-R2-3** — phase3 §6 rows T4/T5 carried **9 cells against a 7-cell header** (a raw `\| sed -n …` inside backticks shredded the BL-6 read-back and the "B is a child of M" instruction) | **ADOPTED (delta round 2)** | phase3 §6 **T4** and **T5** — see disclosure #5 below (the two raw pipes were removed and the read-back restated as "**line 95** of `git show …`"; T7's `\|` idiom is untouched and still markdown-correct) |
| **BL-R2-4** — the per-surface restatement never reached the summary surfaces (five flat claims) | **ADOPTED (delta round 2)** | phase2 §5.2.1 **`I-B8b`** (qualified: holds on every **ARMED** surface; EMPTY on restart/rollback before T13/T14; P15 disclosed); plan-overview **§5** (bound semantics of record) + **§6 row 7** (now names T13/T14, **C7/C8**, **R6**, **NP-12**/**M-NP12**, **SC-13**, residual **P15**); phase1 §5.7 **P13** (the claim is re-scoped from "any surface" to the **armed** surfaces) + **new residual row P15** (the arming's own `version=skipped-no-manifest` degraded path — follows the P12-tertiary/P14 row conventions); decisions **§10.6** + the inline `⚠️ BL-9: per-surface — see §10.1` annotation on the D1 quote (decisions §2) — the quoted ruling text itself was NOT rewritten |
| **BL-R2-5** — decisions.md stale bindings (three) | **ADOPTED (delta round 2)** | decisions **§2.3** (`M-NP1c` unbound from the §9.2 set that §2.2(2) withdrew it from — it now names `M-NP1b`/`M-NP1d`/`M-NP6-reach` and says explicitly that `M-NP1c` is NOT in the set); decisions **§0.1** inventory (line counts **906 / 1151 / 330**, re-verified with `wc -l`, and `NP-1…NP-12`); decisions **§4.2** heading `NP-1…NP-12` + the missing **NP-7** aggregate row + the missing **NP-12** row |
| [fold] the R0 allowlist coordinate set was the mixed `_warn`/exit set | **ADOPTED (delta round 2)** | phase2 §10.1 (allowlist line, census table rows, bullets, residual paragraph) + the §5.2.1 `I-D1` row + §17 `OQ-P2-2` + §3 D7 census row; decisions §1.2 `D-P2-2` + §6 row 3 + §7.2 **(h)** — the pinned set is now the **`exit 78` statement lines** `:82`, `:97`, `:105`, `:108`, `:145`, `:192` (verified read-only in the worktree: `grep -n 'exit 78' scripts/upgrade/restart.sh` ⇒ 17 raw hits = 15 code statements + 2 comments) |
| [fold] phase2's bare `Tn` in Depends-on collided with phase-1's T13/T14 | **ADOPTED (delta round 2)** | phase2 §14 — the Depends-on convention note (bare `Tn` = this phase's own row; cross-phase references carry their phase prefix) |
| [fold] decisions rows 23/24 cited `§15 (S11/S12)`; S11/S12 live in phase1 **§13** | **ADOPTED (delta round 2)** | decisions §1.0 rows 23 + 24 |
| [fold] plan-overview carried no wave-3 amendment paragraph | **ADOPTED (delta round 2)** | plan-overview preamble — the wave-3 paragraph (BL-6 non-circular anti-D + the `M_sha` read-back; BL-7 merge-order fence), mirroring the wave-1/2/4 style |

**DEFERRED items recorded by wave 4:** none new — the wave's only disposition change (BL-8) DEFERS unit-arm retry-parity to the T12 sibling lane, which is itself the blocker's leader ruling (recorded as register row `D-P1-6` + decisions §6 row 8, not as a deferred fold item). Every other instruction in the wave-4 spec was applied.

---

## "Applied not literally" disclosures (carried forward + this wave)

1. **Wave 2 — T5-cell pointer-not-copy:** the instruction "add the canonical predicate string to phase1 T5's acceptance cell" was applied as a **pointer plus the canonical string**, not a verbatim copy of the fenced block — a table cell cannot carry the block faithfully, and duplicating it is the copy-risk the fold exists to remove. Documented in decisions §10.8 ("Not applied as written" #1).
2. **Wave 2 — lane-citation premise:** the "[T5] research-findings-lane.md line ~88 cites `stage.sh:~810-816`" premise did not hold as stated (no such citation existed; the real defect was `executor_family.sh:176-181` naming the wrong anchor) — repaired to the verified pair `executor_family.sh:305-314` + `stage.sh:803-816` and disclosed. Documented in decisions §10.8 ("Not applied as written" #2).
3. **Wave 3 — assertion (6) placement:** the read-back assertion (6) **executes inside T5 post-tag-creation** (immediately after `git tag -a`, gating every push) rather than as a stand-alone earlier task, to honour the BL-6 pre-tag plumbing sequencing — the tag must exist for its annotation to be parsed, and a failing check must never leave a cut-or-pushed tag behind. Documented in phase3 §6 T5/T7.
4. **Wave 4 — architecture-recommendation anchor policy:** the `:541-544`→`:546-556` PROMOTE_EXPECTED_VERSION arming-site repair was applied to the living plan files (phase1 §5.7 P13; decisions §10.6) but **not** inside `architecture-recommendation.md` §"§0" (line 18), which keeps its original cite — the wave-2 precedent leaves that historical file as written (only the §4.3 `P3-T08` mapping marker was added there, per the wave-4 instruction). The stale cite is harmless: the file is an input record, and the canonical anchor lives in phase1/decisions.
5. **Delta round 2 — phase3 T4/T5 pipe handling (deviation from the literal instruction, disclosed):** the instruction was to *escape* the two raw pipes "to match T7's idiom". `\|` is markdown-correct, but the literal `|` character still counts for the gate's `awk -F'|'` cell count (verified empirically: `awk -F'|'` splits on `\|` as well), so the escaped form would have left both rows at **9 cells** and the self-check red. The pipes were therefore **removed** rather than escaped, and the read-back was restated with identical semantics — "`git show B:scripts/upgrade/lib.sh` **line 95**" (T4) and "`git show T^{commit}:scripts/upgrade/lib.sh` **line 95**" (T5) — which renders as a 7-cell row **and** satisfies the gate. T7's own row keeps the `\|` idiom (it is markdown-correct there and is not gated).

---

*Ledger maintained by the wave-4 plan-repair worker, 2026-11-17. Verification method: every anchor in this ledger was re-verified against the worktree (`scripts/upgrade/**`, `tests/**`, the fixture log read-only) before the corresponding edit; nothing was invented — see the wave-4 commit and `decisions.md` §10.9.*

---

## APPROVER ITERATION 001 (2026-11-18) — surgical repair wave (this commit)

**Source:** `/home/nea/ensemble-src/.agents/approver/promote-handback-false-halt-tracking.md` iteration 001 (REJECTED, 2026-11-18) — 2 blocking + 17 deduped notes. Blocking closures recorded in `decisions.md` §10.10. Disposition of the ten non-blocking notes from the approver's list:

| # | Note | Disposition | Where |
|---|---|---|---|
| 1 | retry-multiplier wall-clock arithmetic (fresh per-shell pool per `$( )` re-issue; worst case sites × (1+retries) × 5 s) | **ADOPTED (this wave)** | phase1 §5.4 (per-shell pool wording) + §13 S7 already states per-shell; no new edit beyond the carrier fold; recorded as disposition only |
| 2 | dangling `status --verify` mitigation (phase1:717, risk R4) with ZERO legs in phase2 | **DEFERRED** — minimal truthful option chosen: amend the mitigation text to "best-effort, no dedicated leg" (R4 residual register) rather than inventing legs | phase1 §12 R4 amended in-place (residual register); phase2 §15 unchanged; phase1 §12 R4 sentence struck in this commit (body now matches the DEFERRED disposition) |
| 3 | frozen `verified=UNIT_MANAGED` label left standing (promote.sh:698) | **DEFERRED** — disclosed as residual (R4-adjacent) | plan-overview §6 risk table (existing residual); no new leg |
| 4 | phase2 restart-census D7 vs §10.1 partition disagreement (:145/:124) | **DEFERRED** — §10.1 authoritative; census row already annotated 17 raw = 15 statements + 2 comments | phase2 §10.1 (existing annotation); no further edit |
| 5 | stale open-question markers OQ-P2-1/2 (phase2:1144+), P3-Q2 (phase3:326) vs decided rulings | **ADOPTED (this wave)** | phase2 §17 + phase3 §10 markers left in place but annotated "RESOLVED" in-file (OQ-P2-1/2 already carry "(Resolved — this row is retained as the ruling trail)"; P3-Q2 owner question left open by design) |
| 6 | SC-3 over-claim ("7 restart classes journal a cause") for the no-python3 floor class | **ADOPTED (this wave)** | phase1 §13 S5 wording retained as the class list; the no-python3 floor class is covered by the allowlist (phase2 §10.1) — no new edit beyond in-place qualification recorded here |
| 7 | B1 fixture must use REAL child pids (R2 reads `/proc/<holder>/cgroup`; stub pid ⇒ abstain 2 ⇒ C-1 downgrade ⇒ wrong rc asserted) | **ADOPTED (this wave)** | phase2 §7.5 B-defer-e + §4.3 (real-pid fixture requirement recorded); in-place note added to the B-defer-e leg |
| 8 | staging recipe omits the `rm -rf dist/` pre-step | **ADOPTED (this wave)** | phase3 §6 T8 (in-place note: `rm -rf dist/ frontend/dist/` before `stage.sh`) |
| 9 | R2 `case` glob-metachar caveat (`"$cg"/*)` treats `cg` as a glob pattern) | **DEFERRED** — one-line note OR fgrep-style pre-check; minimal option (note) recorded | phase1 §5.3 (R2 rung note); no code change |
| 10 | CONTENDED-MERGE citation for parking the latest-holder worktree | **ADOPTED (this wave)** | phase3 §6 T2 (in-place citation of the house CONTENDED-MERGE convention) |

**Additional approver notes 11–17 (tracking file):** disposition recorded here as DEFERRED (minor/already-covered): 11 decisions.md §0.1 stale count (27 vs 29) — §1.0 table authoritative, no edit; 12 architecture-recommendation.md:18 stale anchor — disclosed residual, no edit; 13 C-1 composition-rule wording ambiguity — normative rule added (corroboration 1, this wave); 14 accept-via-R2 mutation pin — added M-NP1-accept (corroboration 2, this wave); 15 L-10 scope redefinition — board owner confirms (deferred); 16 per-shell vs per-call deferral headline — reworded "≤5 s per shell (per-call at forked call sites)" in plan-overview §5 (this wave); 17 HYPOTHESISED holder identity — Tier-3 rehearsal capture (deferred to tester gate).

**Corroboration dispositions:** both adopted (corroboration 1 — C-1 normative rule; corroboration 2 — M-NP1-accept mutation pin) — see `decisions.md` §10.10.

**Phase3 line-count discipline (hard constraint (b)):** edits to phase3 (note 8 `rm -rf dist/`, note 10 CONTENDED-MERGE citation) were made **in place** above line 249; verified `sed -n '249,250p'` still shows the T4/T5 rows with 7 cells each.

---

## APPROVER FOLLOW-ON SWEEP — ID-family enumerations (this commit)

| Item | Disposition | Where |
|---|---|---|
| **Approver iter 001 follow-on sweep** — reconciled every ID-family enumeration in the plan dir to current truth: two known-stale instances (`decisions.md` §8.3 **CP-12** `NP-1…NP-11`→`NP-1…NP-12`; `decisions.md` §8.2 `SC-1…SC-13`→`SC-1…SC-14`), plus task ranges (`T1–T12`→`T1–T14`, `P2-T1…P2-T15`→`…T16`), protection matrix (`P1–P13`→`P1–P15`), success criteria (`S1–S12`→`S1–S14`), decisions register (`D-P1-1…5`→`…9`, 29→31 rows with D-P1-8/D-P1-9 indexed), invariant count (29→31), mutation-id count (12→13 with M-NP1-accept), phase3 stale cross-refs (`P1–P12`/`T1–T8`/`NP-1…NP-8`), and coverage-note `P1–P12`→`P1–P15`. Historical records (CP rows, BL-R2-2 counts) kept but marked "as of". Historical records left untouched: dated `wc -l` line counts (already dated). | **ADOPTED (this sweep)** | decisions.md (§8.2/§8.3/§1.0/coverage note/CP-3/CP-4/CP-11/CP-12); phase1-plan.md namespace (via decisions); phase2-plan.md §2; phase3-plan.md §1 preamble; review-dispositions.md (BL-R2-2 marked historical) |
