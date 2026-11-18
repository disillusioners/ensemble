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

**DEFERRED items recorded by wave 4:** none new — the wave's only disposition change (BL-8) DEFERS unit-arm retry-parity to the T12 sibling lane, which is itself the blocker's leader ruling (recorded as register row `D-P1-6` + decisions §6 row 8, not as a deferred fold item). Every other instruction in the wave-4 spec was applied.

---

## "Applied not literally" disclosures (carried forward + this wave)

1. **Wave 2 — T5-cell pointer-not-copy:** the instruction "add the canonical predicate string to phase1 T5's acceptance cell" was applied as a **pointer plus the canonical string**, not a verbatim copy of the fenced block — a table cell cannot carry the block faithfully, and duplicating it is the copy-risk the fold exists to remove. Documented in decisions §10.8 ("Not applied as written" #1).
2. **Wave 2 — lane-citation premise:** the "[T5] research-findings-lane.md line ~88 cites `stage.sh:~810-816`" premise did not hold as stated (no such citation existed; the real defect was `executor_family.sh:176-181` naming the wrong anchor) — repaired to the verified pair `executor_family.sh:305-314` + `stage.sh:803-816` and disclosed. Documented in decisions §10.8 ("Not applied as written" #2).
3. **Wave 3 — assertion (6) placement:** the read-back assertion (6) **executes inside T5 post-tag-creation** (immediately after `git tag -a`, gating every push) rather than as a stand-alone earlier task, to honour the BL-6 pre-tag plumbing sequencing — the tag must exist for its annotation to be parsed, and a failing check must never leave a cut-or-pushed tag behind. Documented in phase3 §6 T5/T7.
4. **Wave 4 — architecture-recommendation anchor policy:** the `:541-544`→`:546-556` PROMOTE_EXPECTED_VERSION arming-site repair was applied to the living plan files (phase1 §5.7 P13; decisions §10.6) but **not** inside `architecture-recommendation.md` §"§0" (line 18), which keeps its original cite — the wave-2 precedent leaves that historical file as written (only the §4.3 `P3-T08` mapping marker was added there, per the wave-4 instruction). The stale cite is harmless: the file is an input record, and the canonical anchor lives in phase1/decisions.

---

*Ledger maintained by the wave-4 plan-repair worker, 2026-11-17. Verification method: every anchor in this ledger was re-verified against the worktree (`scripts/upgrade/**`, `tests/**`, the fixture log read-only) before the corresponding edit; nothing was invented — see the wave-4 commit and `decisions.md` §10.9.*
