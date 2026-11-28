# FINAL GATE VERDICT — revive-wedge terminalization (release gate closeout)

Date: 2026-11-26
Lane: branch `fix/revive-wedge-terminalization`, worktree `/home/nea/ensemble-src-wt-revive-wedge`
Tip under test: `1ace8e202` (C6 `6ccd0e59b` + C7 `1ace8e202`); base `35ec50e3c`
Evidence dir: `.agents/tester/RESULTS/2026-11-26-revive-wedge-gate/`

## OVERALL VERDICT: PASS

No blocking leg. The gate-matrix §3(d) STOP condition did **not** trigger: with the corrected D2 the
expected pin NEW-6a goes RED.

## Disposition table

| Leg | Form / anchor | RED observation | Restore | Verdict | Evidence |
|---|---|---|---|---|---|
| L2d (corrected D2) | D1 terminal-entry short-circuit + D2 = **whole** terminal-CAS early-return block removed (`child_reports.py:5504` block) | `test_a_second_invocation_without_a_revive_mints_no_second_row` RED (expected pin HIT) | byte-identical YES; restored run GREEN | **PASS** | `mut-L2d.out`, `22-mutation-matrix.jsonl` |
| L2d-prev | previous executed form (D1 + D2 keeping the `if` guard, only the `return` suppressed) — measured for reconciliation | same NEW-6a pin RED | byte-identical YES; restored GREEN | **PASS** | `mut-L2d-prev.out` |
| B-M5b | NOT-SEARCHED clause removed at the **no-match path** (`system_log_tools.py:683-686`, `msg += (...)`) | `test_explicit_window_reports_unsearched_region_and_resume_point` RED (expected pin HIT; collateral `test_start_line_one_is_bounded_at_scan_cap` also RED) | byte-identical YES; restored GREEN | **PASS** | `mut-B-M5b.out` |
| B-M5 (prior form, retained) | NOT-SEARCHED clause removed at the **header path** (`:699-702`) | case 5 NOT red; a DIFFERENT pin red (`test_explicit_window_starts_at_start_line_and_excludes_earlier_lines`) — the two sites serve different cases | byte-identical YES; restored GREEN | FAIL-NO-RED (superseded by B-M5b) | `mut-B-M5.out` |
| B-M8 | match-cap clause removed at the **emitted header clause** (`:703-704`) | `test_explicit_window_respects_match_limit` RED (expected pin HIT) | byte-identical YES; restored GREEN | **PASS** | `mut-B-M8.out` |
| B-M8b | scan-loop match-cap **break** removed (`:656-657`) | `test_explicit_window_respects_match_limit` RED (expected pin HIT — `out.count(">>>")==1` / `"1 match(es)"`) | byte-identical YES; restored GREEN | **PASS** | `mut-B-M8b.out` |
| B-M9 | redaction dropped from the **scan body** (disambiguated anchor: comment+`line = _redact_line(line)`, 12-space site) | `test_explicit_window_preserves_context_level_and_redaction` RED (expected pin HIT) | byte-identical YES; restored GREEN | **PASS** | `mut-B-M9.out` |
| M-C6 form (i) — executed form | recency conjunct applied at the NON-ROOT site **in addition to** root (`:5441` `_recency_call_site(False)` -> `True`) | **PC-9 RED**, PC-3 **NOT** red | byte-identical YES; restored GREEN | PASS (1 of 2 named pins) | `mut-M-C6.out` |
| M-C6b form (ii) — literal inverted split (D-D) | recency conjunct REMOVED at the **ROOT** site (`:4463` `_recency_call_site(True)` -> `False`) | **PC-3 RED**, PC-9 **NOT** red | byte-identical YES; restored GREEN | PASS (1 of 2 named pins) | `mut-M-C6b.out` |
| L5 sweep rider | `tests/unit/services/test_stale_running_sweep_pins.py` — BASE scratch (ONLY that file copied, PYTHONPATH pinned, module path asserted inside the base tree) vs HEAD | BASE: `1 failed, 21 passed` — RED `TestReviveWedgeDriveOutcome::test_revive_wedge_victim_lands_in_healed`, wedge reproduced (`drive_no_effect=1, healed=0`); HEAD: `22 passed` | n/a (read-only leg) | **PASS** | `44-L5-base.out`, `44-L5-head.out` |
| Base/merge collection (L1 SUITE) | BASE `35ec50e3c` + only the 5 specified pin files copied over, PYTHONPATH pinned + `daemon.__file__` asserted inside the base tree; HEAD = tip | BASE 31 reds; HEAD 0 reds (`132 passed`); **NET_NEW_COUNT = 0** (`comm -13` empty); L2N net byte-identical PASSED set (`cmp` rc=0); L8 Component-C base RED / head GREEN | n/a | **PASS** | `24-L1-remeasure-console.txt`, `24-comm-23-fixed-at-merge.txt` |
| L1 3x tally recount | `21-L1-reruns.log` | **60 groups / 180 RC lines / 0 unstable**; BASE RC=1 x3 on 30/30, HEAD RC=0 x3 on 30/30 | n/a | **CONFIRMED** | `21-L1-reruns.log`, `45-L1-tally-recount.txt` |

## Fence proofs (M6)

- `daemon/services/child_reports.py` sha256 `5d137dd16f1e6a7e174a762bc11fa7de9c11a76423b3542511d1adf02f6e18d4` — MATCHES the pinned value.
- `daemon/tools/system_log_tools.py` sha256 `fe5d569c059d920fc4953a5ab582de399e83fb5735fb977ccf2604caa83a2181` — MATCHES the pinned value.
- `git status --porcelain --untracked-files=no` — EMPTY (lane tree clean; harness edits live in the untracked evidence dir).
- Scratch worktrees removed (`git worktree remove --force` x4 + `prune`); no scratch tree left under `/tmp/revive-wedge-*` or `/tmp/rw-*`.
- `file:memdb*` rows untouched.
- No commit to the lane branch; no production-source edit outside the scratch trees; no sub-agents; no instance lifecycle action; no revive.

## Findings for the leader (plan-set reconciliation — NOT edited by me)

1. **M-C6 ledger wording vs measurement.** The ledger (as amended by C6) names M-C6 => "PC-9 Arm B AND PC-3 RED". Measured at the current tip: **no single mutation form turns both pins RED.** Form (i) (recency at the non-root site in addition to root) turns **PC-9** RED and leaves PC-3 GREEN; form (ii) (the literal inverted split: recency removed at the ROOT site) turns **PC-3** RED and leaves PC-9 GREEN. The two pins therefore pin **different** halves of the recency contract and are not co-satisfied by one mutation. Suggested ledger rewrite: M-C6 (form i) => PC-9 RED; M-C6b (form ii, root-without-recency) => PC-3 RED.
2. **The prior L2d FAIL-NO-RED was stale-by-tip, not a pin weakness.** The prior mutation matrix was last written at 16:14Z; C6 landed at 17:35Z and C7 at 17:44Z. At the current tip (`1ace8e202`) BOTH the corrected whole-block D2 and the previous executed form turn NEW-6a RED, so §3(d)'s STOP condition does not trigger.
3. **The NOT-SEARCHED clause has two coverage sites.** The header path (`:699-702`) is read by `test_explicit_window_starts_at_start_line_and_excludes_earlier_lines`; the no-match path (`:683-686`) is read by `test_explicit_window_reports_unsearched_region_and_resume_point`. The prior B-M5 (header path) therefore could never turn case 5 RED — B-M5b closes that gap.
4. **B-M9 anchor weakness closed.** `text.count()` is substring-based, so the 12-space anchor `line = _redact_line(line)` also matched the 20-space occurrence in another function (count 2). Disambiguated to the scan-body site by including its unique preceding comment lines; count is now 1.
