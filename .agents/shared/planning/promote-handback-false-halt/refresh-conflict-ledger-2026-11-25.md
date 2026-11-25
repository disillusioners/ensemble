# Refresh Conflict Ledger — promote-handback-false-halt ← origin/latest (2026-11-25)

## 1. Header

| Field | Value |
|---|---|
| Date | 2026-11-25 (UTC) |
| Executor | coder (merge executor; sole instance driving this branch) |
| Branch | `fix/promote-handback-false-halt` @ `573c5c4b10ee3c71b079eb0c711d7cb61aa26f6c` (clean at start) |
| Worktree | `/home/nea/ensemble-src-wt-fix-promote-handback` (only checkout touched) |
| Archive tag object | `bd2d5a6187a30903ed6d3ce4c9ddf815d9f163eb` → `573c5c4b1` (preserved; not retired) |
| origin/latest merged | `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` — `docs(handoff): dump-health-nits-v03154 — HANDOFF-v2 integration record` (2026-11-25T06:55:37Z) |
| Merge base | `0a9b8cd36b79d68d037dc5d51f30df1304838685` |
| Method | `git merge --no-ff --no-commit 35ec50e3c…` (by SHA); **no rebase, no amend, no force, no push, no promote/stage, no fetch, no stash**; 24 commits of branch history preserved |
| Conflict result | 1 file (`scripts/upgrade/lib.sh`), 3 hunks — matches forecast |
| ROW 20 | folded in as the **immediately following separate commit** (see §7) |

## 2. STEP 0 — not-landed evidence (Gate G3, re-run on the exact bound SHA)

Cheap read-only gates re-run at `35ec50e3c` (all results below are from this lane, not inherited):

| Gate | Command | Result |
|---|---|---|
| G1 | `git rev-parse refs/remotes/origin/latest` | `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` — matches bound SHA |
| G2 | `git rev-parse HEAD` / `git status --short` | `573c5c4b10ee3c71b079eb0c711d7cb61aa26f6c` / empty |
| G3a | `git grep -n -i withhold 35ec50e3c -- scripts/upgrade/` | **0 hits** (exit 1) |
| G3b | `git grep -n 'if ! _gate_serving_attribution "hand-back verify"' 35ec50e3c -- scripts/upgrade/lib.sh` | `35ec50e3c:scripts/upgrade/lib.sh:6838` — L's fail-fast `if !` consumer intact |
| G3c | `git grep -n 'child: timeout(' 35ec50e3c -- scripts/upgrade/lib.sh` | hand-back sites `:7290` (reset_failed), `:7330` (is_active), `:7417` (start); additional **unrelated** pre-existing sites `:5647`, `:5677` (stop_via_stop_script) and `:7758`, `:7808` (scope_arm) — not attribution mechanisms |
| G3d | `git log --oneline -3 -S'<id>' 35ec50e3c -- scripts/` for `_GATE_ATTR_DEFER_S`, `gate_defer_reason`, `__attr_rc`, `_bounded_rc_reason` | **empty for all four** |

Four mechanisms × NOT LANDED on L:

| # | Mechanism | Branch identifier(s) | L evidence |
|---|---|---|---|
| i | Honest rc taxonomy at is-active | `_bounded_rc_kind` (def `573c5c4b1:lib.sh:6432`), `_bounded_rc_reason` | pickaxe empty; L still hardcodes `child: timeout(...)` at `:7290/:7330/:7417` and journals on every answered rc≠124 |
| ii | Containment ladder + deferral pool + rc=2 | `_GATE_ATTR_DEFER_S`, `_GATE_ATTR_DEFER_REMAIN`, `ladder_*`, `gate_defer_reason` | pickaxe empty; L's `_gate_serving_attribution` (`35ec50e3c:lib.sh:9171–9308`) has no ladder and no deferral rung — returns only 0/1 |
| iii | HB_ERR stash → **R2 form**: named transient carrier | `gate_defer_reason` helper family (`_gate_consume`/`_gate_outcome`/`_gate_defer_reset`); `_handback_stash` exists on **neither** side | pickaxe empty; no carrier on L; no stash to resurrect |
| iv | Attribution-gate reason handling + T7b rc capture + rc-2 producer | `__attr_rc` case at hand-back verify; `kind=attr_deferred` + `return 2` inside `_gate_serving_attribution` | pickaxe empty; L's consumer at `:6838` still `if !` (any nonzero = refusal) |

Overlap note (not a landing): L commit `865ec4ad3` carries a **different two-way split** — rc 124 vs every answered rc≠124, journaling `handback_is_active_state` on each answered rc (`35ec50e3c:lib.sh:7326–7342`). It overlaps the branch's rc taxonomy **in effect** but contradicts its detail (L journal-rows rc=3; branch's D-P1-1 says INFO-only / no failure-shaped row). Its decision ids are `D6-*` / `AC-D6-*`, never this branch's `D-P1-*`. None of the four branch mechanisms is present on L.

## 3. Conflict summary

Forecast: exactly 1 file, 3 hunks (`git merge-tree --write-tree` preview reported `scripts/upgrade/lib.sh` only). Actual: `git merge --no-ff --no-commit 35ec50e3c…` → `CONFLICT (content): Merge conflict in scripts/upgrade/lib.sh`; markers at merged-blob lines **6890 / 7413 / 7535**. **Delta vs forecast: none** (file set and hunk count identical; marker lines within the forecast's ~6890/~7413/~7535). All other changes from L auto-merged cleanly and were reviewed via `git diff 35ec50e3c -- scripts/upgrade/lib.sh` (§5c).

## 4. Per-conflict record

### H1 — hand-back verify attribution consumer

- **File / merged line range / function:** `scripts/upgrade/lib.sh:6890–6914`, `_handback_serving_probe()` (green-class arm).
- **OURS (`573c5c4b1`):** T7b (`D-P1-4`, BL-1, I-B13) explicit `__attr_rc` capture with `case`: `0` pass, `1` `_warn` + `return 1`, `2` `return 2` (keep polling), `* → 2` (contract violation treated as transient). No L reason-channel write.
- **THEIRS (`35ec50e3c`, from `0f133d2b9`):** `if ! _gate_serving_attribution "hand-back verify"; then` — any nonzero refuses; on refusal `_handback_verify_reason_write "attribution-refused: …"` plus `_hb_verify_diag` (dual sink, payload-log flush F16) then `return 1`.
- **KEPT:** the branch's `case "$__attr_rc"` (all four arms); into arm `1` inserted L's `_handback_verify_reason_write` and `_hb_verify_diag` verbatim; arm `2` returns 2 **without** a reason write; arm `*` returns 2.
- **WHY:** the false-halt cure is the rc-2 path — collapsing it into `if !` is the exact defect (branch `D-P1-4` / `I-B13`, `decisions.md:90`, `phase1-plan.md` T7b). L's reason write is legitimate and load-bearing for the halt row (`D6-(iv)`, `AC-D6-6`, `0f133d2b9:lib.sh:6199-6207`), so it survives on the only arm that actually halts. Reason-channel decision on the rc-2 path: **no write** — rc 2 is not a halt; writing an "attribution-refused" reason would poison a later halt in the same run (the success-hygiene invariant at merged `lib.sh:7551–7555` — the `HANDBACK_VERIFY_REASON=""` + `_handback_verify_reason_clear` reset). **ANCHOR CORRECTION (2026-11-25, C-1 lane):** this ledger previously cited the invariant at `lib.sh:7291–7294`; that range is a DIFFERENT comment (the reason-vocabulary note inside the D6 taxonomy block) and was WRONG. The true location, re-derived from the tree, is `lib.sh:7551–7555`.

### H2 — is-active taxonomy

- **File / merged line range / function:** `scripts/upgrade/lib.sh:7408–7443`, `_supervision_handback_unit()` (probe 2.2b arm).
- **OURS:** D-P1-1 three-way: `_bounded_rc_kind` → `124` = `subprocess_wait_timeout kind=budget` with dynamic reason and true rc; rc `3` = `_log` INFO with site token/state/rc and **no failure-shaped row**; other nonzero = `subprocess_wait_timeout kind=nonzero_exit` with true rc.
- **THEIRS (from `865ec4ad3`):** two-way: `124` = `subprocess_wait_timeout` with hardcoded `child: timeout(...)`; every answered rc≠124 = `handback_is_active_state kind=state` row carrying `state=` and rc (D6-(iii)/AC-D6-1/2), plus a `_warn`.
- **KEPT:** branch's three-way branch structure, dynamic `_bounded_rc_reason`, true `$isact_rc`, and the rc-3 INFO line (site token + state + `unknown` fallback, CP-10); L's `handback_is_active_state` event kept for **every** answered rc≠124 — `kind=state` for rc 3, `kind=nonzero_exit` with the branch's true-rc reason for rc 4/1/other; **no** `subprocess_wait_timeout` row for any answered rc.
- **WHY:** two L-side readers depend on the state row: `tests/test_bounded_subprocess_waits.sh:404` (`S4b` asserts `handback_is_active_state` for rc=3) and `tests/packs/promote_handback_halt_gate_legs_test.sh:611–614` (`D6-A2` asserts zero `subprocess_wait_timeout` rows with `site=handback_is_active`; `D6-A4` asserts the state row). Dropping L's row would silently break both. Conversely dropping the branch's rc-3 INFO / reintroducing a timeout-shaped row would re-pollute every clean ceremony (D-P1-1 rationale). Combination satisfies both: no `kind=budget`, no `timeout(` token, INFO line present, state row present. **Behavioral delta recorded:** D-P1-1's literal "no journal row for rc=3" is refined to "no **failure-shaped** row"; the additive `handback_is_active_state` row survives. Branch's A4 (`kind=nonzero_exit`, true rc, state hint) is satisfied for rc 4/1, but the event is L's additive `handback_is_active_state` instead of `subprocess_wait_timeout` — required by L's `:422` pin. Tester adjudication requested (§8/§10).

### H3 — handback_start budget reason

- **File / merged line range / function:** `scripts/upgrade/lib.sh:7521–7523` (the journal row and its `|| _warn` tail inside the start-timeout arm of `_supervision_handback_unit()`); the RESOLVED-H3 comment block sits immediately ABOVE the command at `:7515–7520`. (At the MERGED commit the range was `:7515–7523` and the comment block was `:7516–7521` — INSIDE the argument list; see the C-1 correction below.)
- **OURS:** `reason=child: $(_bounded_rc_reason "$sbrc" "" "HANDBACK_START_BUDGET_S=…") … rc=$sbrc — SIGTERM→SIGKILL, halting for caller's B4 policy`.
- **THEIRS (from `0f133d2b9`/`865ec4ad3`):** hardcoded `reason=$(printf 'child: timeout(HANDBACK_START_BUDGET_S=%s)' …) … rc=124 — SIGTERM→SIGKILL of the CLIENT; NOT terminal (D6): continuing into the bounded verify (the start job continues server-side)`.
- **KEPT (as TEXT — but NOT as RUNTIME; see the C-1 correction below):** branch's dynamic `_bounded_rc_reason` + true `$sbrc`; L's truthful NOT-terminal tail (`SIGTERM→SIGKILL of the CLIENT`, `continuing into the bounded verify`, `start job continues server-side`) and D6 AC-D6-4 reference.
- **WHY (as re-derived at RUNTIME, 2026-11-25, C-1 lane):** `_bounded_rc_reason 124 "HANDBACK_START_BUDGET_S=<n>"` emits `timeout(HANDBACK_START_BUDGET_S=<n>)`, so with the caller's `child: ` prefix the reason string IS byte-identical to L's hardcoded text — the STRING is right, but the ROW never carried it. The branch's stale tail ("halting for caller's B4 policy") contradicts the code on both sides (the verify continues after a start timeout) and L's D6 AC-D6-4, so L's tail wins.

> **C-1 CORRECTION (2026-11-25, re-derived by execution — this entry OVER-CLAIMED).** The merged resolution pasted the six RESOLVED-H3 comment lines **between the `\` and the argument line**, i.e. INSIDE the `journal_history_append` argument list. Bash removes a backslash-newline during **TOKENIZATION, BEFORE comment recognition**, so the logical line ENDS AT THE COMMENT: the quoted argument line becomes a SEPARATE COMMAND (`command not found`, rc=127) and the `|| _warn` tail attaches to THAT command, so the misleading `journal event append FAILED` warning fires. `journal_history_append` received only `$1`; its `$2` was unset, so:
>
> - under `set -u` — the posture of EVERY production upgrade script (`promote.sh:69`, `stage.sh:117`, `rollback.sh:32`, `restart.sh:63`, `status.sh:26`) — the executor **ABORTED** with `$2: unbound variable` (`lib.sh:1890`) and **NO row was written at all**;
> - without `set -u`, the row was written with an **empty `detail`** — the `site=handback_start … NOT terminal` record silently lost.
>
> The dynamic `_bounded_rc_reason` call therefore **never executed**: the `_bounded_rc_reason` + true `$sbrc` KEPT-claim above was **RUNTIME-FALSE**. `bash -n` returns 0 on the broken construct (it is syntactically legal), which is why every text-level gate in this ledger passed it. **FIX (C-1):** the six comment lines moved ABOVE the `journal_history_append` line (`:7515–7520`), restoring the contiguous argument list and the byte-structure both parents share. The row's content, the rc semantics, the event kind and the reason string were NOT touched — a pure reorder, net line count unchanged (11978 → 11978), so every line number at/after `:7524` is stable.

No marker remains: `git grep -n -E '^(<<<<<<<|=======|>>>>>>>)' scripts/upgrade/lib.sh` → 0 hits; `bash -n scripts/upgrade/lib.sh` clean.

## 5. Semantic-conflict audit

### 5a. rc-contract widening — caller table

Direct callers of `_gate_serving_attribution` in the merged tree (all four are in `lib.sh`):

| Merged line | Call site | Classification | Contract handling | Decision |
|---|---|---|---|---|
| 6895 | `"hand-back verify"` | **branch-updated** (`72f8a58c4`/`573c5c4b1`) | `__attr_rc` case 0/1/2/*; 2 → `return 2` | keep (H1) |
| 9971 | `"/livez"` | **branch-updated** | `__grc` case 2 → `return 2`; probe-2 normalized to 1 | keep |
| 9986 | `"/readyz"` | **branch-updated** | `__grc` case 2 → `return 2`; probe-2 normalized to 1 | keep |
| 10015 | `"version verify"` | **branch-updated** | `__vrc != 0` → `return "$__vrc"` (2 propagates) | keep |
| `scripts/upgrade/status.sh:127` | `"status smoke"` | **pre-existing-untouched** (blob `9cbdc107…` identical on base, branch and L) | `if ! …` → any nonzero = mismatch | **ROW 20** fold-in (§7) — separate commit, T7e |

No **L-added** direct caller exists: `0f133d2b9` and `865ec4ad3` changed the bodies around the existing hand-back caller, not the caller set. Definition and comment occurrences (`lib.sh:9321`, `:9653`, `:9972` in the pre-merge numbering) are not calls.

Indirect consumers of the widened `gate_livez` / `gate_readyz` / `gate_version` rc (not direct `_gate_serving_attribution` callers) — enumerated for completeness:

| Site | Classification | Treats nonzero as | Decision |
|---|---|---|---|
| `lib.sh:10158`, `:10267` (soak ticks) | branch-updated | rc captured into `__svrc` / `__soak_version_rc`; 2 → INCONCLUSIVE/freeze | keep |
| `promote.sh:1076`, `:1085`, `:1139`, `:1440`, `:1445`, `:1451` | pre-existing-untouched | `if …` / `\|\| gate_fail_reason=` → definitive failure | **leave as-is** with reason: outside the branch's pre-merge diff (`git diff 0a9b8cd36 573c5c4b1 --stat` lists no `promote.sh`), no L-side reader/test added there, and changing it would be a new cross-surface behavior change beyond a merge refresh. **Flagged as follow-up** (§10). |
| `restart.sh:323`, `:333`, `:365`; `rollback.sh:298`, `:303`, `:309` | pre-existing-untouched | same hard-failure shape | same decision + same follow-up |

Consumers of `handback_is_active` / `handback_start` / `handback_reset`: the only consumers are journal readers/tests, all enumerated in §8; no runtime control decision reads those rows (the pre-start D6 state machine keys on the **stdout** state word `isact_state`, `lib.sh:7458`, not on the journal row). No L-side consumer depends on the old hardcoded reason string: `tests/test_bounded_subprocess_waits.sh:450/:496` and `:380/:402` grep only the `site=` token.

### 5b. Reason-channel coherence

- Single definitions in the merged tree: `_bounded_rc_reason` (`:5278`), `_handback_verify_reason_write` (`:6229` area), `_handback_verify_reason_text` (`:6240` area), `_hb_verify_diag` (`:6168` area), `gate_defer_reason` (`:9370`), `_gate_consume` (`:9391`), `_gate_outcome` (`:9443`), `_gate_defer_reset` (`:9469`). Duplicate-definition check (§5c) is empty.
- H1 writes `attribution-refused: …` on the definitive rc=1 arm only; all other L reason writers (version absent/mismatch, hard-red, mainpid-unchanged, ceremony-time-exhausted, verify-not-confirmed, unit-start-failed) survive unchanged — `git diff 35ec50e3c -- lib.sh` shows no `-` line for any of them.
- `GATE_ATTRIBUTION_DETAIL` remains the refusal-detail source for both the reason write and the `_hb_verify_diag` line (H1), and for the ladder refusal (`lib.sh:9714`, `:9784`).
- `child: timeout(` after resolution: `:5699` and `:5729` (stop_via_stop_script_unit/pid — pre-existing on base **and** L, byte-preserved, unrelated to hand-back attribution) plus `:7517`, which IS a **comment** (the backtick line carrying the `child: timeout(HANDBACK_START_BUDGET_S=<n>)` text). **THE OBSERVATION WAS CORRECT — THE CONCLUSION WAS NOT (corrected 2026-11-25, C-1 lane).** The earlier entry read "`:`7517`, which is a comment inside the H3 resolution, not a journal row. Zero hardcoded hand-back timeout labels remain in executable journal lines." That last sentence does not follow: the comment did not merely sit "inside the H3 resolution" — it sat INSIDE THE ARGUMENT LIST of a command whose argument list was truncated at parse level, so the executable journal row at `:7522` carried **no reason token at all**. "The label is in a comment" is not "the label is harmless": the row was the thing that carried the label, and the row was dead. **METHOD CORRECTION (what the audit method should have been):** the §5c evidence is a TEXT / LINE-SET comparison of added and removed lines, and that method is **structurally blind to parse-level damage** — the six comment lines and the argument line were all present in the diff, the conflict-marker count was 0, and `bash -n` returned 0, so every text-level gate passed a construct that never ran. The method that WOULD have caught it is a **per-line RUNTIME re-derivation of the resolved command's ARGUMENT LIST** (drive the command, observe what it actually receives) — see the standing procedure in §9.5. A grep or a line-set comparison can never answer "does this command still receive its arguments".

### 5c. Duplicate / orphan audit and the `-` lines

- `grep -nE '^[a-zA-Z_][a-zA-Z0-9_]*\(\)' scripts/upgrade/lib.sh | sed 's/(.*//' | sort | uniq -d` → **empty** (no function defined twice).
- L-added functions all present exactly once: `journal_mark_captured_previous`, `_rollback_target_valid`, `_rollback_target_resolve`, `_rollback_request_to` (e1601e67b); `_handback_repair_dualfight_probe`, `_handback_wrong_version_repair`, `journal_resume_txn`, `_promote_resume_decision`, `_promote_resume_mutation_ok`, `_watchdog_hold_dir`, `_watchdog_probe`, `_watchdog_rt_main` (8662207a0); `_handback_channel_write`, `_handback_channels_cleanup` (c4b322b03); mktemp-minted side channels at `:6129`, `:6229`, `:6268` (c62b03cfc/fe338ff6f). D6 PENDING taxonomy intact at `:7448–7502`; DUAL_FIGHT census (30 references), watchdog containment (126 references), measured verify ceiling (`HANDBACK_VERIFY_MEASURED_CEILING_S`, 3 references) all intact.
- `git diff 35ec50e3c -- scripts/upgrade/lib.sh`: **+828 / −76**. Branch pre-merge `git diff 0a9b8cd36 573c5c4b1 -- scripts/upgrade/lib.sh`: **+815 / −63** (the `878` in `--stat` is total changed lines). Set comparison of added lines: every one of the 41 `+` lines not in the branch diff is a conflict-resolution line from H1/H2/H3; no L logic was added or re-added. Set comparison of removed lines: 76 = 63 branch removals + 13 net conflict removals. The 19 removed lines **not** in the branch pre-merge diff are all inside the three conflicts, listed and justified here:

| # | Removed line (from L) | Justification |
|---|---|---|
| 1 | `# D6-(iv) / AC-D6-6: the REFUSAL DETAIL is the reason the` | H1: replaced by a shortened 5-line comment block carrying the same D6-(iv)/AC-D6-6/F16 rationale. |
| 2 | `# halt must carry — it is captured on the dedicated` | same H1 comment rewrap; content preserved. |
| 3 | `# verifier-reason channel (the launchd HB_ERR channel is` | same H1 comment rewrap; content preserved. |
| 4 | `# untouched) and emitted to BOTH sinks (the payload log` | same H1 comment rewrap; content preserved. |
| 5 | `# included — F16).` | same H1 comment rewrap; content preserved. |
| 6 | `_handback_verify_reason_write "attribution-refused: …"` | H1: re-added verbatim inside the branch's rc=1 case arm (not dropped). |
| 7 | `_hb_verify_diag "hand-back verify: serving-process attribution REFUSED…"` | H1: re-added verbatim inside the branch's rc=1 case arm (not dropped). |
| 8 | `if [ "$isact_rc" = "124" ]; then` | H2: replaced by the branch's `_bounded_rc_kind` three-way dispatch (124 branch retained with dynamic reason). |
| 9 | `"site=handback_is_active kind=budget reason=$(printf 'child: timeout(SYSTEMCTL_PROBE_BUDGET_S=%s)' …)"` | H2: replaced by the branch's dynamic `child: $(_bounded_rc_reason …)` — byte-identical reason for 124, plus true rc. |
| 10 | `# NOT a timeout: systemctl ANSWERED with a non-zero rc (rc=3 =` | H2: replaced by per-arm comments distinguishing routine rc 3 from genuine failures; same meaning. |
| 11 | `# not active — the expected pre-start value; rc=1 = failed /` | same H2 comment replacement. |
| 12 | `# unknown; ...). The answer + the observed state word are` | same H2 comment replacement. |
| 13 | `# journaled on their OWN event kind (additive; NOT a terminal` | same H2 comment replacement; the additive event is retained in both arms. |
| 14 | `# kind, NOT in _TERMINAL_EVENTS) — the timeout row is reserved` | same H2 comment replacement. |
| 15 | `# for rc=124 alone. VERDICT UNCHANGED: still treat as` | same H2 comment replacement; verdict unchanged. |
| 16 | `# NOT-active and continue into the start attempt.` | same H2 comment replacement. |
| 17 | `_warn "is-active probe answered rc=$isact_rc for $unit (state=…) — NOT a timeout …"` | H2: replaced by the branch's rc-3 INFO `_log` (with site token) and the branch's genuine-failure `_warn`; both keep "treat as not-active, continue into the start attempt". |
| 18 | `"site=handback_is_active kind=state unit=$unit rc=$isact_rc state=…"` | H2: re-issued with an explicit `reason=state:<state>` field and the same `kind=state`, rc and state tokens that L's tests read. |
| 19 | `"site=handback_start kind=budget reason=$(printf 'child: timeout(HANDBACK_START_BUDGET_S=%s)' …) rc=124 … NOT terminal (D6)…"` | H3: replaced by the byte-equivalent dynamic reason (`_bounded_rc_reason` emits `timeout(HANDBACK_START_BUDGET_S=<n>)`), true `$sbrc`, and L's NOT-terminal tail verbatim in substance. |

## 6. Mechanism-survival proof (merged file:line)

| Mechanism | Merged evidence |
|---|---|
| i — honest rc taxonomy | `_bounded_rc_kind` def `:5264`; `_bounded_rc_reason` def `:5278`; call sites `:7362/:7365/:7370` (reset_failed), `:7415/:7419/:7441` (is-active), `:7522/:7529` (start), `:7872/:7875/:7884/:7935/:7943` (scope arm). rc=3 takes the INFO arm `:7428–7429` with site token + state + rc; no `kind=budget`/`timeout(` row for rc 3. **RUNTIME-SURVIVAL CORRECTION (2026-11-25, C-1 lane):** the START site's `:7522` was **DEAD at the merged commit** — its argument list was truncated at parse level, so the row never carried the dynamic reason (see the C-1 correction in §4-H3). The taxonomy itself survived; the start arm's ROW did not, until the C-1 fix restored the contiguous argument list. |
| ii — ladder + deferral + rc=2 | `_gate_attr_containment` `:9520`; ladder vars `:9691–9701`, accept/refuse arms `:9707/:9718`; deferral pool init/clamp `:9477–9492`; rung `:9742–9813`; single `kind=attr_deferred` row `:9808`; rc-2 return `:9813`; `gate_livez`/`gate_readyz` rc-2 propagation `:9974/:9989`; `gate_version` propagation `:10015–10017`. |
| iii — R2 carrier (stash superseded) | `gate_defer_reason` `:9370`; `_gate_consume` `:9391`; `_gate_outcome` `:9443`; `_gate_defer_reset` `:9469`; single-writer/reader comments `:9357–9368`; `_handback_stash` **0 hits** (absent on both sides — not resurrected). Note: helper family is present as in the branch; no new call-site wiring was introduced by this merge. |
| iv — attribution reason handling / T7b / rc-2 producer | `__attr_rc` local `:6881`, capture `:6895`, case `:6896–6914` (rc 2 → `return 2` at `:6910`; contract violation → 2 at `:6913`); definitive refusal writes L's reason at `:6902`; rc-2 **producer** `return 2` inside `_gate_serving_attribution` at `:9813` with `kind=attr_deferred` row at `:9808`; dynamic reason at `:7522` replacing the hardcoded start label. |

## 7. ROW 20 decision — (a) fold in, separate commit

- Ruling: **(a) fold in** as the commit immediately after the merge, not (b) leave for a later lane. (b) is unsupported: L has no rc-2 producer anywhere (`git log -S` pickaxe for the four branch identifiers is empty at `35ec50e3c`, L's `_gate_serving_attribution` returns only 0/1), so the transient case that row 20 handles cannot arise from L — the sole implementation remains this branch's.
- Evidence: pinned ref `refs/ensemble/archive/promote-handback-false-halt-row20` → `e6fd0eeeb357a61b32f96b7ac8136b7d45b4f1fa` (first parent `573c5c4b1`); diff touches only `scripts/upgrade/status.sh` (19+/4−, lines 126–144): explicit rc capture + `case` (0 pass / 2 → `STATUS_ATTR_MISMATCH=""` + informational DEFERRED warn so `--verify` does not fail / * → original mismatch).
- Ledger refs: `architecture-recommendation-r2.md:73` (row 20), `phase1-plan.md:484/:700/:729` (T7e/C3b), `decisions.md:106` (D-P1-16), `D-P1-15` (ruling E sanction).
- Blobs: `status.sh` = `9cbdc10776dafe055732ce3f53b85d0eb2ce0e32` on branch tip, merge base and L; pinned target = `5dcce7775e1c27951e7142b8dde773180f252b86`.
- `git diff e6fd0eee^1 e6fd0eee -- scripts/upgrade/status.sh | git apply --check` — dry-run clean (see §10 for the executed result). Applied with `git apply`, **not** cherry-pick (it is a merge commit).
- Folded in as the immediately following commit: `fix(upgrade): status.sh transient note for attribution deferral` (subject as ruled; body states T7e/row 20, `--verify` must not fail on rc 2, rc 0/1 byte-unchanged, provenance `e6fd0eee`, dispatcher ruling (a)).

## 8. L-side readers/tests touched by the behavioral deltas — NONE executed in this lane

| File:line | What it reads | Effect of this resolution |
|---|---|---|
| `tests/test_bounded_subprocess_waits.sh:380`, `:402` | S4 is-active hang → `subprocess_wait_timeout` with `site=handback_is_active` | unchanged (rc 124 arm kept) |
| `tests/test_bounded_subprocess_waits.sh:404` | S4b rc=3 → `handback_is_active_state` row | satisfied by the H2 combination |
| `tests/test_bounded_subprocess_waits.sh:422` | S4c rc=1 → **no** `subprocess_wait_timeout` row with that site | satisfied: nonzero_exit row moved to `handback_is_active_state` |
| `tests/test_bounded_subprocess_waits.sh:450`, `:496` | start-timeout row with `site=handback_start` | **RUNTIME-FALSE at the merged commit — both asserts RED (corrected 2026-11-25, C-1 lane).** The row never carried the `site=` token: the argument list was truncated at parse level, so under `set -u` the executor aborted (`$2: unbound variable`) and NO row was written (without `set -u` the row was written with an empty `detail`). **Executed evidence:** at the merged commit `8d306f2e3` the file reports `PASS=72 FAIL=3` — `:450` and `:496` both RED; the same file against pristine `origin/latest` (`35ec50e3c`) reports `PASS=74 FAIL=1` with the SAME two asserts GREEN, which is how the third failure was attributed as pre-existing and unrelated. After the C-1 fix the file reports `PASS=74 FAIL=1` — both GREEN. The reason text is byte-equivalent **as text**; that was never the problem. |
| `tests/packs/promote_handback_halt_gate_legs_test.sh:611–614` | D6-A rc=3: no timeout row + state row | satisfied |
| `tests/packs/handback_verify_budget_test.sh`, `tests/packs/promote_handback_halt_serving_green_test.sh` | rc-2 budget / serving-green legs | branch behavior preserved; not executed here |
| `tests/test_adaptive_soak.sh` | branch-only ruling-E re-cut (already in branch) | untouched by this merge |

**No test script, pack, suite, boot smoke, promote/stage or launcher flow was executed in this lane** (hard fence 4). All regression execution is **DEFERRED → tester**.

## 9. Drift notes

1. **Mechanism iii form:** the original HB_ERR stash (`_handback_stash`) was superseded inside the branch by the R2 redesign; the surviving carrier is the `gate_defer_reason` helper family. `_handback_stash` appears on neither side and was not resurrected.
2. **"+878 vs +815/−63" measurement:** `git diff --stat 0a9b8cd36 573c5c4b1 -- scripts/upgrade/lib.sh` reports `878` = 815 insertions + 63 deletions (total changed lines). After resolution, `git diff 35ec50e3c -- scripts/upgrade/lib.sh` reports +828/−76; the +13/+13 net delta is entirely the three conflict resolutions (§5c), not dropped L logic.
3. **D6 vs D-P1 naming trap:** L's comments citing `D6-(iv)` / `AC-D6-6` at `35ec50e3c:lib.sh:6836–6846` document L's own change and were kept; they are not this branch's `D-P1-*` ids. Conversely, this ledger's H1/H2/H3 records use branch ids for the branch side and L commit shas for the L side.
4. **Preview tree sha:** `git merge-tree --write-tree` returned `48a71e1c…` with argument order `(L, branch)`; the commission's forecast `48b82dcc…` differs only by ours/theirs ordering in marker labels — the conflicted file set and hunk positions matched exactly, so no delta is recorded.

## 9.5 STANDING PROCEDURE — post-resolution re-read (argument-list re-derivation)

**Added 2026-11-25 by the C-1 lane, after the C-1 defect proved this ledger's own §5c method insufficient.**

**THE RULE.** A conflict resolution that rewrites a command's **ARGUMENT LIST** needs a **per-line RUNTIME re-derivation** of that command — not a text diff, not a line-set comparison, not a grep, and not `bash -n`.

**WHY (the failure mode it catches).** Bash removes a backslash-newline during **TOKENIZATION, BEFORE comment recognition**. A resolution that pastes a comment block between a `\` and the argument line therefore ends the logical line AT the comment: the command silently loses the rest of its argument list, the following quoted argument becomes a SEPARATE COMMAND, and any `||` tail attaches to that separate command. The construct stays **syntactically legal**, so `bash -n` returns 0 and the conflict-marker count is 0 — every text-level gate passes a command that never receives its arguments. The blast radius is silent: a lost journal row, an unbound-variable abort under `set -u`, and a misleading `|| _warn` — and **none of it is visible in a diff**.

**THE METHOD THAT CATCHES IT.** For every resolved call site whose argument list was rewritten:

1. **Re-derive the argument list at RUNTIME**, not from the text. Drive the command (a stub callee that echoes its argv) and observe what it actually receives — `$1`, `$2`, the full arg count. Assert the argument count and the argument VALUES, not the source text.
2. **Run the caller under the production posture.** `set -u` is on in every production upgrade script; an argument-list truncation that a non-`set -u` run hides as an empty value becomes an ABORT under `set -u`. The re-read MUST use `set -u`.
3. **Assert the OBSERVABLE, not the literal.** A green `bash -n`, a zero marker count and a byte-equal diff are all compatible with a dead command.
4. **Do not infer health from a token that merely EXISTS in the tree.** "The `site=` token is present in the source" is not "the `site=` token reaches the journal". The §5b entry in this ledger made exactly that inference and got it wrong.

**WHERE IT IS ENFORCED NOW.** `tests/shell_continuation_scan.py` (tree-wide lint for the `\` + comment-only shape) and `tests/test_handback_start_timeout_journal_row_test.sh` (the runtime leg: the row must land with its full detail and the executor must not abort). Both are registered in `.agents/tester/PACKS.md`.

**SCOPE OF THE RULE.** Applies to every merge/refresh resolution that touches a command's argument list, not only to this branch. The defect class is invisible to diff-based review by construction — the only defence is a runtime re-derivation of the resolved command.

## 10. Verification results and follow-ups

### 10.1 C-1 correction record (2026-11-25, added by the C-1 lane)

The merged resolution at `5ba3223ed` (and therefore at `6056d1d05` and at the landed merge `8d306f2e3`) truncated the `journal_history_append` argument list at the start-timeout site — the six RESOLVED-H3 comment lines sat between the `\` and the argument line. Details and the over-claims corrected are in §4-H3 (the KEPT/WHY entries), §5 (the `child: timeout(` bullet and the method correction), §6 (the mechanism-survival row), §8 (the `:450/:496` row) and §4-H1 (the success-hygiene anchor `:7291–7294` → `:7551–7555`).

Provenance (re-derived from the blobs, not from a checkout): the construct is **contiguous** (gap = 0) at `0a9b8cd36` (merge base), `35ec50e3c` (origin/latest at the time) and `573c5c4b1` (the branch tip), and **broken** (gap = 6) at `5ba3223ed`, `6056d1d05` and `8d306f2e3`. The comment block was introduced by the refresh merge `5ba3223ed` itself ("refresh with 3-hunk lib.sh resolution") — the resolution note was pasted into the source between the `\` and the argument. It is **MERGE-INTRODUCED**, and it is **NOT** on shipped `origin/latest` (`35ec50e3c`, and the current `origin/latest` `dd007f16d`): neither carries the comment block.

Fix: the six comment lines moved above the `journal_history_append` line (`:7515–7520`), a pure reorder — net line count unchanged (11978 → 11978), the row's content / rc semantics / event kind / reason string untouched, and every line number at/after `:7524` stable.

Executed evidence:

| Leg | Command | Pre-fix | Post-fix |
|---|---|---|---|
| landed asserts `:450` / `:496` | `bash tests/test_bounded_subprocess_waits.sh` | `PASS=72 FAIL=3` (both RED) | `PASS=74 FAIL=1` (both GREEN) |
| pristine-baseline attribution | same, with pristine `origin/latest` `lib.sh` | — | `PASS=74 FAIL=1` — identical to post-fix; the one remaining failure (`R happy-path: rc 0`) is **pre-existing on `origin/latest`** and unrelated to C-1 |
| runtime leg (row content + no abort) | `bash tests/test_handback_start_timeout_journal_row_test.sh` | `PASS=5 FAIL=8` (executor aborts: `lib.sh: line 1890: $2: unbound variable`; journal carries NO row) | `PASS=13 FAIL=0` |
| tree-wide parse-class lint | `python3 tests/shell_continuation_scan.py <root>` | `RESULT: FAIL` — `scripts/upgrade/lib.sh:7515 -> 7516` | `RESULT: PASS` |
| syntax | `bash -n scripts/upgrade/lib.sh` / `status.sh` | rc 0 | rc 0 |

Pre-commit verification (this lane):

- `bash -n scripts/upgrade/lib.sh` → clean; `bash -n scripts/upgrade/status.sh` → clean.
- Conflict markers in `lib.sh` → 0 hits; duplicate function definitions → empty.
- `git check-ignore -v .env` → `.gitignore:44:.env` (never staged/read/source'd).
- Hooks: `core.hooksPath` unset; `$(git rev-parse --git-path hooks)` contains only `*.sample` files — no active hook; no bypass flag used.
- `git apply --check` for the ROW 20 patch — clean (see executor report for the exact command/result).
- Ancestry: `git merge-base --is-ancestor 573c5c4b1 HEAD` = 0; `git merge-base --is-ancestor bd2d5a6187a30903ed6d3ce4c9ddf815d9f163eb HEAD` = 0; `git rev-list --count 0a9b8cd36..573c5c4b1` = 24; `MERGE_HEAD` = `35ec50e3c…`. Post-commit ancestry/counts are reported in the executor's final report (this ledger is committed with the merge).

Follow-ups (routed, not executed here):

1. **T7e fixture** for the status.sh rc-2 transient note — out of scope for this lane; tester to add/pin.
2. **Regression suite → tester:** run the L-side tests in §8 plus the branch's promote-handback legs on the merged tree; specifically adjudicate the H2 behavioral delta (D-P1-1's literal "no journal row for rc=3" vs L's additive `handback_is_active_state` row) and the event-kind adaptation for rc 4/1 (`kind=nonzero_exit` on L's additive event, not on `subprocess_wait_timeout`).
3. **Indirect rc-2 consumers** (`promote.sh:1076/1085/1139/1440/1445/1451`, `restart.sh:323/333/365`, `rollback.sh:298/303/309`) still treat any nonzero gate rc as a definitive failure. This is pre-existing in the branch (not a merge regression and not touched by L); a T7c-style rc-capture across those surfaces needs its own commission or tester finding.
4. `_gate_consume` / `_gate_outcome` / `_gate_defer_reset` remain defined but unwired at call sites, exactly as in the branch pre-merge tree; no wiring change was made in this refresh.
5. **The parse-class lint found 7 PRE-EXISTING occurrences of the same defect class in TEST SCAFFOLDING** (not fixed by the C-1 lane — out of scope; they are PINNED in `tests/shell_continuation_scan.py` with their file:line and a one-line reason, so they are visible on every lint run and a fix cannot land silently): `test/packs/por_b2_wedge_repro_pins_unit_test.sh:137`, `test/packs/por_b3_receipt_supervisor_unit_test.sh:136`, `test/packs/por_b4_stale_running_sweep_unit_test.sh:139`, `test/packs/por_b_boot_consistency_unit_test.sh:133`, `test/packs/por_c_checkpoint_retention_unit_test.sh:136`, `tests/packs/soak_gate_mcp_warmup_acceptance.sh:389` and `:442`. Each truncates the `env` scrub prefix (the `env … VAR=x \` + comment shape), so the pytest child runs WITHOUT `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `ENSEMBLE_SELF_ENV=dev`, `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_TIMEOUT=280` and WITHOUT the POSTGRES_* scrub. The truncation is NOT masked by anything: those variables are ABSENT from the continuation prefixes (they are not truncated out of them), and NOTHING compensates: the PACKS.md INVOCATION column for those five registered `por_*` packs carries **NO scrub at all** — each is literally `cd $WT && timeout 300 bash test/packs/<name>.sh`, with no `env`, no per-variable scrub and no `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` boot guard (the sixth affected script, `tests/packs/soak_gate_mcp_warmup_acceptance.sh`, has no PACKS.md row at all, so it has no INVOCATION column either). The ONLY scrub that exists lives INSIDE each pack script — the `por_*` packs carry a `SCRUB=(…)` array plus the `env …` prefix, and `soak_gate_mcp_warmup_acceptance.sh` carries its own per-variable `env -u POSTGRES_*` prefix — and every one of those prefixes is itself truncated by the SAME continuation-then-comment defect, so none of them compensates either. Therefore the pytest child can run with an ambient `POSTGRES_DB`/`POSTGRES_HOST`/… visible; in this project an ambient `POSTGRES_DB=ensemble_prod` is a LIVE-PROD-CONTACT HAZARD. This is a REAL defect in those packs; fixing them is OUT OF SCOPE for this lane — reported here as a follow-up finding, not fixed. **Separate commission required.** The C-1 lane's own `lib.sh` occurrence was the only one on the PRODUCTION surface.
