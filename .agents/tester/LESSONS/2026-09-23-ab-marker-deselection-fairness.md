# LESSON — A/B base legs must mirror pack SELECTION, not just the node list (marker-deselection fairness)

**Date:** 2026-09-23 · **Gate:** fs-tool-guardrails full regression (feature/fs-tool-guardrails @ 948c0f06, base 3c09c6ae)

## What happened

Base-leg 6 ran 34 branch-red integration node IDs at base with the default `addopts` (`-m 'not integration and not postgres'`). Result: `22 deselected` — 22 of 34 nodes silently never ran, and the leg initially read as "3 PASS / 9 FAIL / 22 NOT-COLLECTED". That reading produced **4 false branch-caused candidates** (wc_wake, dead-letter ×2, workspace_sse) and mislabeled 18 real base-failures as "absent at base".

Root cause: the pack under A/B (`regression_integration_test.sh`) selects with `--override-ini="addopts="`, which REMOVES the marker filter; my base leg did not. Same node list, different selection semantics → incomparable runs.

## The fix (and the rule)

**An A/B base leg must be byte-identical in invocation SHAPE to the branch run it adjudicates: same selection flags (`--override-ini`, `-m`), same xdist mode, same per-test timeout overrides.** Node-ID lists alone are not the invocation.

Corroboration pattern that caught it: "22 NOT-COLLECTED" was impossible — the branch diff touched zero integration test files, so every node ID must exist identically at base. **When a base leg reports NOT-COLLECTED for nodes whose files the diff did not touch, suspect selection mismatch before suspecting test addition.**

## Second pattern: base-PASS ≠ branch-caused until solo-decisive

Every base-PASS + branch-FAIL candidate in this gate (5 total across the sweep: jsonb, skillab, wc_wake, dead-letter ×2+workspace_sse) resolved to **xdist/shared-resource flakes** via the solo retry budget (2–3× solo, `-p no:xdist`, pack-identical selection). In a 1,000+ test xdist pack, shared SQLite locks, PG type catalogs, and event loops produce order-dependent failures that A/B legs (smaller xdist runs) do not reproduce. Protocol that worked every time:

1. Candidate flagged (base-PASS + branch-FAIL)
2. Solo ×2–3 on branch, pack-identical selection, `-p no:xdist`
3. All-PASS → flake confirmed; any deterministic FAIL → base solo symmetry run → then and only then call it branch-caused
4. Quarantine row with the full evidence chain; recommend per-worker DB isolation as the durable fix

Also: for a diff with a plausible NEW error surface (e.g. new refusal messages), grep the tiebreak tracebacks for those exact strings — it converts a flake-or-regression coin-flip into a mechanism ruling (here: zero guardrail strings in traces + zero tool usage in the failing test files = mechanism excluded).

## Third pattern: outer-cap vs script-internal budget

`regression_integration_test.sh` carries an internal `timeout 350s`; my dispatch rule caps the outer wrapper at 300s. The outer strangles first by design — the pack finished at 264.7s naturally, so no conflict. Rule kept: never extend the 5-min cap to match a script's larger internal budget; if a pack legitimately needs >300s it presents as TIMEOUT and gets SPLIT.
