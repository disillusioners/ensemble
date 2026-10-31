# Phase 4 — Functional Gate (net-new=EMPTY)

## 1. Objective

Prove the lane introduces ZERO net-new test failures: run the FULL lane corpus at HEAD with the
EXACT commands captured at Phase 0 (same commands both sides), diff the complete FAILED lists,
and account for every delta. Order/load-sensitive reds are isolated 3× per side before
classification.

Design references: D-13; findings-riders §8 (net-new discipline + worked examples
`.agents/tester/RESULTS/2026-10-06-designer-od-lane-fix-final-gates.md`,
`.agents/tester/RESULTS/2026-10-29-dbpool-functional-gate.md`).

## 2. Prerequisites

Phase 3 green (all packs + matrix complete); Phase-0 base artifacts intact (verbatim command
set + FAILED list + counts); fences standing; main-venv interpreter path.

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-4.1 | The gate corpus == the Phase-0 command set, verbatim — daemon default shard + `tests/job_queue/` family + static pin pack + PG packs (serial) — NO substitution, NO extra deselects | Net-new discipline requires same commands both sides (Repo & Dev Conventions [TESTING]) | "Close enough" corpus matching (invalidates the diff) |
| PD-4.2 | Classification ladder per FAILED delta: (1) byte-identical to base ⇒ pre-existing; (2) order/load-sensitive ⇒ isolate 3× PER SIDE (BASE and HEAD independently) before classifying; (3) genuinely new ⇒ fix-and-rerun (the v0.27.13 G3.5 pattern: 11 net-new reds caught → fixed → re-verify net-new=0 with a +N/−N fingerprint) | House discipline; the db-pool lane's worked example on this exact code area | Single-run classification (flaky reds misattributed as net-new or silently excused) |
| PD-4.3 | Also compute `fixed = BASE_FAILED − FIX_FAILED` — previously-red tests that went green at HEAD — and account each (intentional fix vs incidental) | The discipline's second clause: "gate=net_new∅ AND previously-red green at HEAD" accounting | Diffing only the HEAD side (misses unintended behavior flips in both directions) |

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|---------------|----|
| T4.1 | Run the gate corpus at HEAD (Phase-0 commands verbatim; explicit `tests/` lists; `tee LOG >/dev/null` + `tail`; PG packs serial + explicit `--timeout` where the 24.5-26s-wall precedent applies); capture HEAD FAILED list + collection counts | HEAD-side artifact symmetric to AC-0.4 | gate logs (lane evidence surface) | P3 | R4 | AC-4.1 |
| T4.2 | Full FAILED-list diff BASE vs HEAD; classify every delta via PD-4.2 (pre-existing byte-identical / isolated-3× flaky / genuinely new); fix-and-rerun any genuinely-new until net-new=∅ with the +N/−N fingerprint recorded | `net_new=∅` verdict + classification table + fingerprints | gate report (lane evidence surface) | T4.1 | R4 | AC-4.2 |
| T4.3 | `fixed` accounting (PD-4.3): every previously-red-now-green test attributed (intentional vs incidental); incidentals investigated to a named cause or escalated in the report | `fixed` table with attributions | gate report | T4.2 | — | AC-4.3 |
| T4.4 | Fixture-level end-to-end re-proof inside the gate context: the Phase-0 fixture shape closes at HEAD under the SAME run root/fences as the corpus (belt: pack evidence + gate-context evidence agree) | Concordant close evidence | gate report | T4.1 | R1 | AC-4.4 |
| T4.5 | Write the functional-gate RESULTS doc (`.agents/tester/` convention) with: commands verbatim, both FAILED lists, diff table, classifications, fingerprints, fixture concordance, disclosures | RESULTS doc delivered (input to Phase 5 tester lane + stage evidence) | `.agents/tester/RESULTS/<date>-zombie-close-functional-gate.md` | T4.2-T4.4 | — | AC-4.5 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-4.1 | HEAD corpus run complete with symmetric artifacts (commands identical to Phase 0, verbatim) |
| AC-4.2 | `net_new=∅` after classification/fix cycles; 3×-isolation evidence for every load-sensitive delta; fingerprints recorded |
| AC-4.3 | `fixed` list fully attributed |
| AC-4.4 | Fixture close re-proven in gate context (concordant with Phase 3 pack evidence) |
| AC-4.5 | Functional-gate RESULTS doc complete with disclosures |

## 6. Verification method

Mechanical diff discipline (both lists byte-compared); the RESULTS doc is the reviewable
artifact. Any genuinely-new red that cannot be fixed within the lane's charter is a STOP-and-
report (the leader adjudicates fix vs disclosure — never silently excised).

## 7. Risks & notes

- The corpus is large — use chunked runs via `mapfile` + quoted arrays if the shell needs it;
  loops in a script file (tool bash may be `/bin/sh` — `${var%%...}`/`PIPESTATUS`/`pipefail` die
  on Bad substitution mid-loop).
- If the R-10 rider was flipped OUT (D-8), drop its pack rows from BOTH sides' corpora — never
  from one side only.
