# Tidier Quality Pass — wi-1fbee2ec (report-obligation visibility + WC-orphan detection)

- **Date:** 2026-11-01 · **Iteration:** 001 (of 3 shared with reviewer)
- **Scope:** lane diff `f7039c3ff..17d45fd5c` — 10 commits, 17 files, +2799/−54, worktree `/home/nea/ensemble-src-wt-obligation` (branch `fix/report-obligation-visibility`)
- **Mandate:** quality-only pass post-reviewer-APPROVE (0 critical/0 blocking). Polarity-sensitive gate-aggregate change → any behavior-touching finding is REPORT-ONLY. Two developer-routed items (b)/(d) carried text-only fix authority.
- **Dispatch:** 2 parallel read-only workers (readable `e406e8fb` / `tidier-readable-code`, hygiene `5d53921a` / `tidier-static-hygiene`) + apply phase reusing worker A (revived, context intact). All workers reported fully; no gaps, no re-dispatches.

## Verdict

**PASS — after in-lane fold.** 14 merged findings: **0 High / 7 Medium / 7 Low**. 9 fixed in-lane as two zero-behavior commits; 5 reported-only. Both routed items resolved with premise-verified, worker-proven text.

## Routed items

**(b) attestation_gate.py:466-481 — `-1` sentinel docstring.** Developer's "dense-but-accurate" held ONLY for the `db_exc` row. For per-input fail-open rows (e.g. the `outstanding_commissions == MISSION_READ_UNKNOWN` early return), the obligation read DID run — the value was simply not attached and the dataclass default `-1` landed. A forensic reader trusting "read never ran" could wrongly clear the obligation arm. **Fixed:** both `#:` blocks rewritten (worker-proposed exact texts) — `-1` = "not carried / unknown", never "the tree is quiet"; `report_obligation_read_error=False` on early-return rows = "not carried", not "no error occurred".

**(d) report_delivery_recovery.py :1771/:1907 — "constant restatement".** Developer's premise **corrected**: the sites are not comment/docstring restatements — they are function-local deferred imports binding `_PARENT_TERMINAL_STATUSES` from `daemon.repositories.report_injection.repository:262`, shadowing THIS module's own pre-existing member-identical constant at `:450-460`. Found independently by both workers; member-identity proven against both definitions (COMPLETED/ERROR/TERMINATED/FAILED). **Fixed:** both deferred imports deleted (name now resolves to the module-level `:450` constant). Pre-existing triple-definition debt (`job_queue_service.TERMINAL_STATUSES` → repository `:262` → recovery `:450`) routed, not touched.

## Findings & dispositions

| # | Sev | Category | file:line | Finding | Disposition |
|---|---|---|---|---|---|
| M-1 | 🟡 | Readability | attestation_gate.py:466-481 | routed (b): `-1` semantics wrong for per-input fail-open rows | **fixed** (918cdec7e) |
| M-2 | 🟡 | Imports/Smells | report_delivery_recovery.py:1769-1771, 1906-1910 | routed (d): deferred imports shadow module's own identical constant (:450) | **fixed** (918cdec7e) |
| M-3 | 🟡 | Readability | attestation_gate.py:872-874, 453, 1354, 1554 | stale R2 enumeration anchors (FOUR/FIVE) vs the lane's sixth input; `:2136-2139` verified accurate as-is — untouched | **fixed** (918cdec7e) |
| M-4 | 🟡 | Imports | test_report_obligation_emission_pins.py:39 | unused `StaticPool` import (AST-proven; docstring mentions are prose) | **fixed** (918cdec7e) |
| M-5 | 🟡 | Coding Style | report_delivery_recovery.py:1929 | magic string `"waiting_children"` vs imported `InstanceStatus` (enum value verified `== "waiting_children"`, models.py:30) | **fixed** (aa5f2cd6c) |
| M-6 | 🟡 | Code Smells | report_delivery_recovery.py:1760, 1923 | inline `256` depth-cap duplicated across both walkers | **fixed** (aa5f2cd6c) — `_ANCESTOR_WALK_DEPTH_CAP = 256`, both sites + both docstrings cite it |
| M-7 | 🟡 | Code Smells | report_delivery_recovery.py:1562-1580 vs 1695-1705 | **[REPORT-ONLY]** root-terminal suppression check duplicated caller/callee; callee branch would mis-count `orphan_disposition` + divergent log prefixes (`"sweep suppressed_root_terminal:"` vs `"sweep orphan suppressed_root_terminal:"`) | **reported** — control-flow ruling needed |
| M-8 | 🟢↓ | Imports | report_injection/repository.py:1163 | deferred `from sqlalchemy import func, or_` — flagged Medium by readable worker; hygiene worker refuted with verified pre-existing deferred-import sites in the same file (:1280/:2007/:2092) | **downgraded, no change** — conflicting evidence resolved toward the site census |
| L-1 | 🟢 | Pin naming | test_report_obligation_emission_pins.py:311 | typo `idie` → `idle` in test id | **reported** — rename blocked by evidence-stability (tester receipts / collected-count fences may cite the id); grep RESULTS/PACKS before any future rename |
| L-2 | 🟢 | Types | report_delivery_recovery.py:1884 | `dict[str, Any]` diagnostic bag in `_classify_wc_orphan` | reported — future `TypedDict` when the surface is touched anyway |
| L-3 | 🟢 | Readability | report_delivery_recovery.py:638-640 | "NEVER silent" overstatement (skip counters silent by design) | **fixed** (918cdec7e) |
| L-4 | 🟢 | Readability | report_delivery_recovery.py:1828 / 633-668 | `probe_errors` docstring omitted feed-level-failure semantics | **fixed** (918cdec7e) |
| L-5 | 🟢 | Readability | report_delivery_recovery.py:1851, 1864 | dense inline conditionals in FIRE log f-strings | reported (Consider: hoist `authority_short`) |
| L-6 | 🟢 | Coding Style | manager.py:13250 | only backslash continuation in the file | **fixed** (aa5f2cd6c) — parenthesized |

## Commits (branch tip `aa5f2cd6c`, no push)

1. `918cdec7e` — `tidier: doc-accuracy + import-hygiene fold — routed (b) sentinel docstrings, routed (d) deferred-import dedup, stale R2 anchors, dead StaticPool import, counter-doc one-liners` (3 files, +34/−27)
2. `aa5f2cd6c` — `tidier: value-identical anchoring — WAITING_CHILDREN enum literal, _ANCESTOR_WALK_DEPTH_CAP constant, manager backslash reformat` (2 files, +15/−9)

Mechanical safety: content-anchored edits (exact current-text match, skip-not-improvise on mismatch); V1/V2 verification gates before code-operand edits; `ast.parse` syntax check (main-venv interpreter, zero bytecode writes) before each commit; hunk-by-hunk `git diff` review; targeted `git add --` only; porcelain clean at end.

## Needs reviewer/leader eyes

1. **M-7** suppression-check duplication (report-only): drop the callee pre-check as dead defense, or have the callee distinguish suppression from revive-failure; unify the log prefix either way.
2. **D1 × UNKNOWN-row interaction:** on the `MISSION_READ_UNKNOWN` early-return row, a genuine `report_obligation_read_error=True` and a real count are both discarded — the D1 fail-closed DENY never reaches `decide()` step (2b) on that path (pre-existing fail-open row × new input).
3. **Dual-default pairing:** `GateDecision.outstanding_report_obligations = -1` (forensic) vs `decide()` kwarg default `0` (legacy-preserving) — both deliberate and documented in-place; polarity record should name the pairing explicitly.
4. **Triple terminal-frozenset definition** (`job_queue_service` → repository:262 → recovery:450) — single-source consolidation is a module-structure commission.
5. `_dormant_descendants_with_work_enroute` now dead-in-production (documented + pinned) — keep-vs-retire lifecycle ruling.

## Out-of-scope (pre-existing, not fixed)

File-size giants (manager 15,809 / child_reports 6,585 / instance-repo 4,172 / rdr 3,687 — lane +10.9% into an already >3000 file, routed-debt class); `list_waiting_children_parents` unbatched vs capped `list_non_root_waiting_children`; OR-keyed subtree-count perf shape unproven on large trees; dry-mode promotion metric not extended with the obligation input (its four-term predicate is accurate for its own code).

## Process notes

- Both routed items required premise verification before acceptance — (d)'s "comment restatement" claim was factually wrong; routed-premise checking stays mandatory.
- Apply-phase `edit_file` false-positived "found 2 times" on a uniquely-matching 12-space literal → count-asserted Python replacement (`assert count == 1`) is the reliable fold mechanism.
- skill_feedback relay visibility nit recurring (~12th occurrence); accepted on concrete-artifact strength per established precedent.
