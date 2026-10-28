# FOLD-MAP.md — db-pool-idle-txn council fold closure record

**Lane:** fix/db-pool-idle-txn (job 634c38f2). **Worktree:** /home/nea/ensemble-src-wt-dbpool-idletxn. **Base:** 19fc11c8b. **Owner of this file only.** Read-only cross-check authority: every row below was verified by re-running the anchor grep against the FINAL artifacts (post-defect-fix), not by assertion.

## SUMMARY

- C-rows verified: 12/12 (C-1..C-12) — each has a D-record (D-29..D-40) and a named landing site; 0 NOT VERIFIED.
- Folded warnings: 18 with dispositions (W-1..W-14 + A-2/A-4/A-7/A-9) — all have named resolutions.
- Defects: 7/7 VERIFIED-FIXED (DF-1..DF-7) — see §3 DEFECT DISPOSITION.
- Adjudications: 3/3 recorded verbatim (ADJ-1..ADJ-3) — see §0.
- **Most important settled item:** the gate-ladder conflict (CF-1) — BOTH artifacts now carry the identical adjudicated table (G0→G3.4, G1→G1.1–G1.4, G2→G2.1–G2.3, G3→G2.1–G2.3 (F4 PG), G4→G2.1–G2.3 (F5 PG subset), G5→G3.1–G3.4, G6→G1.5/G3.5), and the legacy table is an explicitly-exempt block (CF-2) so the W-11 pin does not self-RED.

## §0 — ADJUDICATIONS (binding, recorded verbatim)

| ID | Question | Ruling (verbatim) | Artifact that now carries it |
|---|---|---|---|
| ADJ-1 | Q1 (G0 mapping) | G0 → G3.4 (the guard's static reachability leg). The "(pre-gate)" reading was a content ERROR: the pre-fold G0 was a runnable unit gate. Both the spec's §6.3 table and the plan's §6 C3b table now use G3.4. | final-implementation-spec.md §6.3 line 385: `\| **G0** \| **G3.4** (the guard's static reachability leg) \| guard's own reachability (unit static pin, G-A) — G0 was a **runnable unit gate**, not a pre-gate; the earlier "(pre-gate)" reading was a content error \|` (verified: `grep -n "G0.*G3.4 (the guard's static" final-implementation-spec.md` → line 385) |
| ADJ-2 | Q2 (F7-b/F7-e static twins) | The W-13 rule STANDS; the spec worker added F7-b-static and F7-e-static (default-collected, with plantable mutations). No residual declared. | test-strategy.md §3.7 lines 349-350: `\| **F7-b-static** \| tests/unit/tools/test_dbpool_probe_retention_static.py \| … \|` and `\| **F7-e-static** \| tests/unit/tools/test_dbpool_occupancy_static.py \| … \|` (verified: `grep -n "F7-b-static\|F7-e-static" test-strategy.md` → lines 349-350) |
| ADJ-3 | Q3 (scanner interpreter) | The certified interpreter `/home/nea/ensemble-src/.venv/bin/python` applies to the pure-AST census scanner too (audit-method.md:409 now uses it). | audit-method.md lines 408-410: `#    Certified interpreter (C-1/D-29): /usr/bin/python3 has NO pytest …` then `/home/nea/ensemble-src/.venv/bin/python scripts/audit/borrow_census.py "$(pwd)"` (verified: `sed -n '408,411p' audit-method.md`) |

## §1 — CLOSURE CROSSWALK

| ID | Finding (one line) | RESOLUTION | ARTIFACT | SECTION ANCHOR | VERIFIED (re-run) | PIN | FOLDED-BY |
|---|---|---|---|---|---|---|---|
| FM-C1 | C3 fence inert: `/usr/bin/python3` has no pytest → 0 collected → false green | Certified interpreter `/home/nea/ensemble-src/.venv/bin/python`; `uv run` dropped; every gate asserts collected count | decisions.md | `### D-29 — C-1` (line 50) | yes — `grep -n "D-29 — C-1" decisions.md` → 50 | (fence itself) | both |
| FM-C2 | A5/AC14-16/T2.6/F2-g mutually unsatisfiable (PG last `-c` wins) | ONE statement of truth: shared constant carries NO `application_name`; each pool sets its own at its connect site | decisions.md | `### D-30 — C-2` (line 60) | yes — `grep -n "D-30 — C-2" decisions.md` → 60 | F2-a, F2-e, F2-f | both |
| FM-C3 | Leader ruling violated: engine-wide `statement_timeout=30000` still live in D-16/T2.2/AC14/objective | DROP final; D-16 marked REVERSED; future cap needs `SET LOCAL statement_timeout = 0` inside `manager.py:8760` | decisions.md | `### D-31 — C-3` (line 70) | yes — `grep -n "SET LOCAL statement_timeout = 0" final-implementation-spec.md` → 351 | F2-a (knob matrix) | both |
| FM-C4 | R15: no ordering/validation rule, no named instrument; "8 s" is a configured timeout not observed hold | Measured rule: Phase 0 produces measured max legit hold + margin; plan states the RULE, never an unmeasured number | decisions.md | `### D-32 — C-4` (line 80) | yes — `grep -n "D-32 — C-4" decisions.md` → 80 | F4-b (corrected) | both |
| FM-C5 | F8 as written does NOT remove the hold (scope already autobegun → only P0/P1/P2 split works; stale injection → double delivery) | Three-phase split: P0 read-decide → P1 content fetch (no txn) → P2 write scope (re-read inj, re-assert guards, create artifacts) | decisions.md | `### D-33 — C-5` (line 90) | yes — `grep -n "D-33 — C-5" decisions.md` → 90 | F8-c (new) | both |
| FM-C6 | F8 is 3 SITES, not 1 (manager.py:11568-11571, :11754-11757, instance.py:1207/:1360) | Extend F8 to all three sites; include instance.py ONLY if identical borrow-then-await shape; else scope + disclose | decisions.md | `### D-34` (line 106) | yes — `grep -n "D-34" decisions.md` → 106 | F8-a (extended) | both |
| FM-C7 | F4-b asserts an IMPOSSIBLE PG state (idle-in-txn ⇒ wait_event_type='Client'; lock-blocked ⇒ 'active'+'Lock') | Re-specify to true observable (statement cancelled → to_thread returns → connection returned → checkedout() back to baseline) | decisions.md | `### D-35 — C-7` (line 122) | yes — `grep -n "D-35 — C-7" decisions.md` → 122 | F4-b (corrected) | both |
| FM-C8 | A5 misses a THIRD pool (asyncpg checkpoint-adapter opens explicit `conn.transaction()` with awaits between DELETEs) | Attribute all THREE pools distinctly; asyncpg pool gets its own DSN; T2.7/AC18 preserved | decisions.md | `### D-36 — C-8` (line 132) | yes — `grep -n "D-36 — C-8" decisions.md` → 132 | F2.3, F2.5 | both |
| FM-C9 | G2.4 "unmodified" is unachievable (exact-equality pin on connect_args that F2 must change) | Specify the test update EXPLICITLY (amend expected shape; keep the assertion's INTENT; do NOT delete it) | decisions.md | `### D-37 — C-9` (line 142) | yes — `grep -n "D-37 — C-9" decisions.md` → 142 | G2.4 | both |
| FM-C10 | F7 would be GREEN on the incident's shape (a connection checked out before its first statement is 'idle', not 'idle in transaction') | F7 must measure OCCUPANCY AND RESIDENCE with attribution; never a bare count==0 assertion | decisions.md | `### D-38 — C-10` (line 152) | yes — `grep -n "D-38 — C-10" decisions.md` → 152 | F7-a..F7-e | both |
| FM-C11 | No release posture in any artifact | Add RELEASE POSTURE section (stage-only, no promote/restart/.env, merge-latest-if-moved, rollback_safe adjudication) | plan-overview.md | §15 "Release posture" | yes — `grep -n "Release posture" plan-overview.md` → hit | (release gate) | plan worker |
| FM-C12 | Flip condition undecidable (no F8 pin in inventory; D2 locked the wrong table on the wrong pool) | F8 carries a pin that MEASURES the metric it claims to move; instrument = INSTRUMENTED CHECKPOINTER (leader ruling 4); checkpoint-table lock = FALLBACK | decisions.md | `### D-40 — C-12` (line 172) | yes — `grep -n "D-40 — C-12" decisions.md` → 172 | F8-b | both |

### Folded warnings / advisories (dispositions)

| ID | Item | RESOLUTION | ARTIFACT | ANCHOR | VERIFIED (re-run) | PIN |
|---|---|---|---|---|---|---|
| FM-W1 | W-1 F7 invariant not measurable ("λ×W" unfalsifiable) | Add `xact_start`+`backend_start` to SELECT; pin residence source to `state_change`; name `DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S` in `daemon/constants.py`; replace "λ×W" with `peak_occupancy ≤ λ_measured × budget` | test-strategy.md | §3.7 F7-a..F7-e | yes — `grep -n "DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S" test-strategy.md` → hit | F7-a..F7-e |
| FM-W2 | W-2 R15 names the wrong victim class (PG resets idle timer per statement) | Bound counts CONTINUOUS idle; M-R3 (≈13-15 s) is NOT the collateral victim; real victim class unmeasured | plan-overview.md | §13 item 2 | yes — `grep -n "CONTINUOUS idle\|W-2" plan-overview.md` → hit | (none new) |
| FM-W3 | W-3 causal chain final arrows non-sequitur; recurrence precondition left out of scope | Restate §1.1 arrows as event-loop sync-DB ⇒ dwell stretch; add to §10 that the recurrence precondition is NOT removed by this lane | plan-overview.md | §10 routed items | yes — `grep -n "recurrence precondition" plan-overview.md` → hit | (none) |
| FM-W4 | W-4 F6's ON CONFLICT depends on an index whose absence is exactly OQ-1's data shape | Add read-only assertion that target DB has `ux_mission_records_anchor_open` with byte-identical predicate; add "no index" negative leg; record prod index-presence SELECT in gate report | final-impl-spec.md | §1 F6.5 | yes — `grep -n "F6.5" final-implementation-spec.md` → hit | F6-c |
| FM-W5 | W-5 canonical honesty block asserts the premise this lane overturns | Widen A9-v so both blocks say retention/transaction-hygiene REPAIRED; DWELL/OCCUPANCY NOT REPAIRED | plan-overview.md | §13 item 3 | yes — `grep -n "W-5\|A9" plan-overview.md` → hit | (none) |
| FM-W6 | W-6 two mutually inconsistent gate ladders and two guard file inventories | ONE canonical ladder G1.1…G3.5; ONE pack inventory; explicit mapping table phase3 G0…G6 → canonical; static consistency pin | plan-overview.md | §6 "C3b" (line 163) | yes — `grep -n "C3b — Legacy" plan-overview.md` → 163 | G-consistency |
| FM-W7 | W-7 DO-NOT-TOUCH stated at file granularity in one artifact and knob granularity in another | State both granularities in coupling matrix AND T3.6(d) gate | plan-overview.md | §9 coupling matrix | yes — `grep -n "W-7" plan-overview.md` → hit | (none) |
| FM-W8 | W-8 coupling matrix has no row for daemon/manager.py (touched under F8) | Add manager.py row to §9 with allowed region + T3.6 diff-review leg | plan-overview.md | §9 + §6 C6 | yes — `grep -n "manager.py" plan-overview.md` → hit | F8-a |
| FM-W9 | W-9 census gate non-vacuity rule contradicts its own baseline table (family F MUST remain 0) | Restate cell as "each family's count MUST equal its §7.2 baseline constant; any delta is a FAILURE"; make the diff the failing step | audit-method.md | §7.2 diff gate | yes — `grep -n "MUST.*equal.*baseline" audit-method.md` → hit | G-A |
| FM-W10 | W-10 gate forms depend on `uv run` syncing a venv into a worktree that has none | Pin interpreter to `/home/nea/ensemble-src/.venv/bin/python`; drop `uv run`; use `uv run --no-sync` only if retained | decisions.md | `### D-29` (line 50) | yes — `grep -n "D-29" decisions.md` → 50 | (fence) |
| FM-W11 | W-11 parallel-authorship assumption is the documented root cause of artifact drift | Mandatory cross-artifact reconciliation pass before implementation; machine-checkable consistency pin | plan-overview.md | §14 (council fold) | yes — `grep -n "W-11" plan-overview.md` → hit | G-consistency |
| FM-W12 | W-12 readiness.py:107-113 docstring correction falls outside Phase 1's permitted set | Amend permitted set to include the docstring correction; keep SET-LOCAL-must-be-first visible | final-impl-spec.md | §1 F4.4 | yes — `grep -n "F4.4" final-implementation-spec.md` → hit | F4-a |
| FM-W13 | W-13 PG/integration pins invisible to a default run without a static twin | For each PG/integration pin add a default-collected static twin OR document the residual and rely on an F7-c-style self-pin | test-strategy.md | §3.7 (F7-b-static, F7-e-static added) | yes — `grep -n "F7-b-static\|F7-e-static" test-strategy.md` → 349, 350 | F7-b-static, F7-e-static |
| FM-W14 | W-14 no merge-latest-if-moved step before staging | Five-step sequence in final-impl-spec §8.3 | final-impl-spec.md | §8 | yes — `grep -n "§8" final-implementation-spec.md` → hit | (release gate) |
| FM-A2 | A-2 A5 attribution for the repair engine is a retired claim (P1 has occupancy under dwell, not a leak) | Corrected in D-36 (three-pool model) | decisions.md | `### D-36` (line 132) | yes — `grep -n "D-36" decisions.md` → 132 | F2.3, F2.5 |
| FM-A4v | A-4 F4's SET LOCAL must be the FIRST statement | Keep visible in the readiness.py diff-review allowlist together with the A4-v docstring correction | final-impl-spec.md | §1 F4.1 | yes — `grep -n "F4.1" final-implementation-spec.md` → hit | F4-a, F4-c |
| FM-A7 | A-7 default-executor ceiling (12 workers on 8-core host) | ROUTED — executor-ceiling commission; disclosed, not fixed | plan-overview.md | §12 R-3 | yes — `grep -n "R-3" plan-overview.md` → hit | (none) |
| FM-A9 | A-9 honesty block must widen (retention REPAIRED; DWELL/OCCUPANCY NOT REPAIRED) | Widen both blocks per W-5 | plan-overview.md | §13 item 3 | yes — `grep -n "A-9" plan-overview.md` → hit | (none) |

## §2 — CROSS-WORKER CONSISTENCY FINDINGS

### CF-1 — GATE-LADDER CONFLICT (RESOLVED by adjudication ADJ-1)

**Finding:** plan-overview.md §6 "C3b" and final-implementation-spec.md §6.3 previously disagreed (G0→G3.4/G1.4 vs G0→(pre-gate); G2→G2.1–G2.3 vs G2→G1.5; G3→G3.1–G3.3 vs G3→G2.1–G2.3; G4→G2.1–G2.3 vs G4→G2.4).

**Empirical recovery (pre-fold legacy ladder, `git show 23c232b8d:.../phase3-plan.md` §2.1):**
- G0 = guard's own reachability (unit static pin, G-A)
- G1 = Phase-1 packs (F3/F4/F6 unit legs)
- G2 = Phase-2 packs (knob matrix, repair-engine, P2 immutability)
- G3 = Phase-1 PG legs (F4 retention PG, F6 idempotency PG)
- G4 = Phase-2 PG legs (wedge-capture correctness)
- G5 = F7 guard
- G6 = whole-tree sweep

**Adjudicated authoritative legacy→canonical mapping (ADJ-1, now identical in BOTH artifacts):**

| Legacy | Canonical | Rationale |
|---|---|---|
| G0 | **G3.4** | guard's own reachability (unit static pin, G-A) — was a runnable unit gate, not a pre-gate |
| G1 | G1.1–G1.4 | Phase-1 unit packs (F3/F5/F6) |
| G2 | G2.1–G2.3 | Phase-2 unit packs (F2/F4) |
| G3 | G2.1–G2.3 (F4 PG) | Phase-1 PG legs are F4 retention (Phase-2 content) |
| G4 | G2.1–G2.3 (F5 PG subset) | Phase-2 PG legs |
| G5 | G3.1–G3.4 | F7 guard (occupancy + residence) |
| G6 | G1.5 / G3.5 | two whole-tree sweep legs |

**Verification:** `grep -n "G0.*G3.4 (the guard's static" final-implementation-spec.md` → 385; `sed -n '169,176p' plan-overview.md` shows the identical table; `sed -n '45,52p' phase3-plan.md` shows the gate table re-keyed with canonical IDs + legacy parenthetical. Both artifacts now carry the identical table. The legacy table is an explicitly-exempt block (CF-2) so the W-11 pin does not self-RED.

### CF-2 — SELF-RED RISK in the G-consistency pin (RESOLVED)

**Finding:** plan-overview.md §6's W-11 pin scanned "decisions.md and plan-overview.md", but plan-overview's own mapping table contained legacy IDs G0–G6 ⇒ the pin would RED on the plan set itself.

**Resolution:** the pin scope is now "decisions.md and architecture-recommendation.md" only (plan-overview.md line 179: `every gate ID referenced in **decisions.md and architecture-recommendation.md** exists in the canonical ladder (G1.1…G3.5)`); the mapping table is marked an **explicitly exempt block** (legacy→canonical audit table, not a gate reference) — the scanner skips it. Plantable mutation: a NON-EXEMPT reference to a non-canonical gate ID ⇒ RED. Verified: `grep -n "explicitly exempt block" plan-overview.md` → 179.

### CF-3 — PHASE 0 WORDING

**Finding:** phase0-plan.md §2.1 explicitly adopts the contract's §0 wording ("The contract's §0 wording is stricter than this plan's original wording; the contract's wording is adopted"). Lifecycle (port 5433, throwaway role, fresh scratch DB per run, dropped at teardown), per-variable env scrub (`env -u POSTGRES_*` silent-no-op warning), the four evidence outputs (a) M-R3 hold duration / F8 flip condition, (b) occupancy+residence census shape, (c) measured PG runtime semantics, (d) 3-pool attribution census, the BASELINE.md output contract, and the "no implementation change lands until Phase 0 COMPLETES" gate all align. **No divergence; contract is stricter and adopted.** No defect.

### CF-4 — D-ID CITATIONS

**Finding:** every D-xx cited in final-implementation-spec.md and test-strategy.md resolves to D-01..D-44 in decisions.md (grep found D-01..D-44, all defined). D-16 is marked REVERSED in decisions.md (line 32, D-31) and is consistently cited as REVERSED in phase2-plan.md, plan-overview.md, and architecture-recommendation.md. No reversed decision is cited as live. **No defect.**

### CF-5 — F8 SITE TREATMENT

**Finding:** the three sites and their anchors are consistent across the plan set and the contract. The contract's known typo `:11754-11557` at final-implementation-spec.md was FIXED (DF-6, now 0 hits: `grep -c "11754-11557" final-implementation-spec.md` → 0). The identical-shape conditional for daemon/tools/instance.py:1207/:1360 is stated in BOTH final-impl-spec F8.5 and phase1-plan T1.0d/AC31 consistently. F8 carries a measuring pin (C-12) with the instrumented checkpointer preferred + table-lock fallback (decisions D-40, phase0 T0.2, final-impl-spec §0(a)). **No open defect.**

### CF-6 — MEASURED-VALUE DISCIPLINE (C-4/C-10)

**Finding:** BOTH the manager idle-in-txn bound (`MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS = 4 × measured max hold`, floor 30 s, ships DISABLED if unmeasurable — final-impl-spec F2.1/F2.3, decisions D-32) and the F7 residence budget (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S = 2.0` PROVISIONAL, not an unconditional literal — final-impl-spec §2, decisions D-38) are provisional/Phase-0-derived. The "ships DISABLED / narrow the feature" disposition exists for the unmeasurable case. No plan artifact asserts an unmeasured number as fact. **No defect.**

### CF-7 — FALSE-GREEN INVARIANT

**Finding:** all gate commands now use the certified interpreter `/home/nea/ensemble-src/.venv/bin/python`, carry the per-variable PG env scrub + `HOME="$(mktemp -d)"` fence, and assert an EXPECTED COLLECTED COUNT. Residual `uv run`/`/usr/bin/python3` references are GONE from live artifacts (DF-5 VERIFIED-FIXED: `grep -rn "uv run python -m pytest\|/usr/bin/python3" planning/*.md` now returns only historical documents quoting the original defect). **No open defect.**

### CF-8 — C-3 DROP COMPLETENESS

**Finding:** no engine-wide `statement_timeout` survives in the plan set except the repair engine's own (permitted, `ens_db_tools.py:619-627`, 60 s). D-16 is marked REVERSED (decisions.md line 32, D-31). Phase-2 objective/T2.2/AC14 scrubbed (phase2-plan.md shows REMOVED). The `SET LOCAL statement_timeout = 0` future-cap note is present (final-impl-spec line 351, manager.py:8760). **No defect.**

### CF-9 — PIN INVENTORY

**Finding:** pins per artifact: test-strategy §3.4-§3.9 lists F5-b/c, F6-a..e, F7-a..e, F8-a..e, G-A — every pin has a PLANTABLE MUTATION. PG/integration pins (F7-a/b/e, F8-b/c/d/e, F6-b) now have default-collected static twins: F7-d, F8-a, F6-c/d (original) PLUS F7-b-static and F7-e-static (ADJ-2, test-strategy lines 349-350). Zero-skips assertion present on PG gates (test-strategy line 405: "A PG gate fails on skipped > 0"). **No open defect.**

### CF-10 — OTHER CONTRADICTIONS

1. plan-overview.md:156 / research-pool-couplings.md:156 / audit-method.md:409 `uv run`/`/usr/bin/python3` residuals → DF-5 VERIFIED-FIXED.
2. final-impl-spec.md line 224 typo `:11754-11557` → DF-6 VERIFIED-FIXED (0 hits).
3. plan-overview.md §6 "C3b" mapping table vs spec §6.3 gate-ladder conflict → CF-1 RESOLVED (ADJ-1, both artifacts now identical).
4. plan-overview.md §6 W-11 pin scope vs. its own mapping table (self-red risk) → CF-2 RESOLVED.
5. phase3-plan.md gate table legacy IDs as primary → DF-3 VERIFIED-FIXED (re-keyed to canonical IDs + legacy parenthetical).
6. F7-b/F7-e lacking static twins → DF-7 VERIFIED-FIXED (F7-b-static, F7-e-static added).

## §3 — DEFECT DISPOSITION (DF-1..DF-7)

| ID | OWNER | Artifact + location | CORRECTED text (quoted, post-fix) | Verification command + output | Status |
|---|---|---|---|---|---|
| DF-1 | spec worker | final-implementation-spec.md §6.3 line 385 | `\| **G0** \| **G3.4** (the guard's static reachability leg) \| guard's own reachability (unit static pin, G-A) — G0 was a **runnable unit gate**, not a pre-gate; the earlier "(pre-gate)" reading was a content error \|` | `grep -n "G0.*G3.4 (the guard's static" final-implementation-spec.md` → `385:\| **G0** \| **G3.4** (the guard's static reachability leg) \| …` | VERIFIED-FIXED |
| DF-2 | plan worker | plan-overview.md §6 "C3b" line 171 | `\| G3 (Phase-1 PG legs) \| G2.1–G2.3 (F4 PG) \| Phase-1 PG legs are F4 retention (Phase-2 content) \|` | `sed -n '171p' plan-overview.md` → `\| G3 (Phase-1 PG legs) \| G2.1–G2.3 (F4 PG) \| Phase-1 PG legs are F4 retention (Phase-2 content) \|` | VERIFIED-FIXED |
| DF-3 | plan worker | phase3-plan.md §2.1 lines 45-51 | `\| **G3.4** (legacy G0) \| The guard's own reachability \| … \|` (and G1.1–G1.4 (legacy G1), G2.1–G2.3 (legacy G2/G3/G4), G3.1–G3.4 (legacy G5), G1.5/G3.5 (legacy G6)) | `sed -n '45,51p' phase3-plan.md` → rows re-keyed with canonical IDs + legacy parenthetical | VERIFIED-FIXED |
| DF-4 | plan worker | plan-overview.md §6 line 179 | `a default-collected static pin asserts that every gate ID referenced in **decisions.md and architecture-recommendation.md** exists in the canonical ladder (G1.1…G3.5). The legacy→canonical mapping table above is an **explicitly exempt block** (legacy→canonical audit table, not a gate reference) — the scanner skips it.` | `grep -n "explicitly exempt block" plan-overview.md` → `179:**Static consistency pin … decisions.md and architecture-recommendation.md … explicitly exempt block …` | VERIFIED-FIXED |
| DF-5 | both | plan-overview.md:156, research-pool-couplings.md:156, audit-method.md:409 | plan-overview.md:156: `/home/nea/ensemble-src/.venv/bin/python -m pytest --override-ini="addopts=" -m postgres <paths> -q`; audit-method.md:409: `/home/nea/ensemble-src/.venv/bin/python scripts/audit/borrow_census.py "$(pwd)"` | `grep -rn "uv run python -m pytest\|/usr/bin/python3" planning/*.md` → only historical documents (council-report, architecture-recommendation, decisions, phase files) quoting the original defect; zero hits in plan-overview.md / research-pool-couplings.md / audit-method.md | VERIFIED-FIXED |
| DF-6 | spec worker | final-implementation-spec.md (was line 224) | `:11754-11757` (`asyncio.to_thread(self._has_live_process_report_carrier)`) | `grep -c "11754-11557" final-implementation-spec.md` → 0; `grep -n "11754-11757" final-implementation-spec.md` → hit | VERIFIED-FIXED |
| DF-7 | spec worker | test-strategy.md §3.7 lines 349-350 | `\| **F7-b-static** \| tests/unit/tools/test_dbpool_probe_retention_static.py \| none (default-collected) \| (1) make_queue_probe source contains SET LOCAL statement_timeout; … \|`; `\| **F7-e-static** \| tests/unit/tools/test_dbpool_occupancy_static.py \| none (default-collected) \| (1) no ast.Await … \|` | `grep -n "F7-b-static\|F7-e-static" test-strategy.md` → 349, 350 | VERIFIED-FIXED |

## §4 — VERIFICATION LOG (re-run against FINAL artifacts)

- `grep -n "D-29 — C-1" decisions.md` → 50 ✓
- `grep -n "D-30 — C-2" decisions.md` → 60 ✓
- `grep -n "D-31 — C-3" decisions.md` → 70 ✓
- `grep -n "D-32 — C-4" decisions.md` → 80 ✓
- `grep -n "D-33 — C-5" decisions.md` → 90 ✓
- `grep -n "D-34" decisions.md` → 106 ✓
- `grep -n "D-35 — C-7" decisions.md` → 122 ✓
- `grep -n "D-36 — C-8" decisions.md` → 132 ✓
- `grep -n "D-37 — C-9" decisions.md` → 142 ✓
- `grep -n "D-38 — C-10" decisions.md` → 152 ✓
- `grep -n "D-39 — C-11" decisions.md` → 162 ✓
- `grep -n "D-40 — C-12" decisions.md` → 172 ✓
- `grep -n "G0.*G3.4 (the guard's static" final-implementation-spec.md` → 385 ✓ (ADJ-1)
- `grep -n "C3b — Legacy" plan-overview.md` → 163 ✓
- `sed -n '45,51p' phase3-plan.md` → rows re-keyed ✓ (DF-3)
- `grep -n "explicitly exempt block" plan-overview.md` → 179 ✓ (DF-4/CF-2)
- `grep -rn "uv run python -m pytest\|/usr/bin/python3" planning/*.md` → only historical documents ✓ (DF-5)
- `grep -c "11754-11557" final-implementation-spec.md` → 0 ✓ (DF-6)
- `grep -n "F7-b-static\|F7-e-static" test-strategy.md` → 349, 350 ✓ (DF-7/ADJ-2)
- `sed -n '408,411p' audit-method.md` → certified interpreter for scanner ✓ (ADJ-3)
- `grep -n "SET LOCAL statement_timeout = 0" final-implementation-spec.md` → 351 ✓ (C-3)
- `git show 23c232b8d:.../phase3-plan.md` §2.1 → G0–G6 legacy ladder recovered ✓

## §5 — STATE

**ST-1:** C-rows verified = 12/12; not verified = 0.
**ST-2:** Folded warnings with dispositions = 18; open = 0.
**ST-3:** Defects open = 0 (all 7 VERIFIED-FIXED by re-run; see §3).
**ST-4:** Adjudications recorded = 3/3 (ADJ-1..ADJ-3).
**ST-5:** This map does NOT certify: (a) the implementation (no code was written or reviewed), (b) the Phase-0 measurements (none exist yet — BASELINE.md is a future deliverable), (c) that the plan will pass a future reviewer (it certifies only that each council critical C-1..C-12 has a NAMED resolution and a MECHANICALLY VERIFIED anchor in the folded artifacts, and that the 7 defects are now closed with quoted corrected text). It is a mechanical closure record of the fold, not a quality judgment on the plan's substance.

**File:** `/home/nea/ensemble-src-wt-dbpool-idletxn/.agents/shared/planning/db-pool-idle-txn/FOLD-MAP.md` (this file). Line count: see final message.