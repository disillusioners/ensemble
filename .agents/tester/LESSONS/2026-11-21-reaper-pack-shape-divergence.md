# Lesson: Reaper pack shape divergence — scrubbed-runner vs PG-pinned populations (2026-11-21)

## Context
Engine Arc live-defects gate (`fix/phase1-live-defects` @ c8db252e8, base b71742fe3). The dev's evidence
pointed at `bash_spawn_reaper_feature_test` "passing" historically (177/177 @ 5f89691a1, 2026-10-31), and at
a 2026-11-02 EVIDENCE dir from a DIFFERENT lane for the 4 pre-existing reaper reds. The gate had to adjudicate
both premises.

## Finding 1 — pack shape ≠ direct-run shape
`test/packs/bash_spawn_reaper_feature_test.sh` runs through `scripts/run_tests_scrubbed.sh`, which
UNCONDITIONALLY unsets `POSTGRES_*`. So:
- Pack lane (scrubbed): 6 reds (4 reaper + 2 stamp) — even when the caller exports PG pins, the pack strips them.
- Direct PG-pinned lane: 8 reds (4 reaper + 4 stamp).
Same node-set relationship both legs (base ≡ head). A pack result can therefore NEVER be compared 1:1 against a
direct fenced invocation — name the shape whenever citing a red count for this family.

## Finding 2 — "ambient-dependent" was the wrong axis
The 2×2 env-shape matrix (PG pin × ambient `ENSEMBLE_BASH_TRACKING_ID` scrub) on the stamp probe showed:
- The 4 reaper reds survive EVERY shape (deterministic — D9 start-time gate: synthetic `start_time=500` can never
  match real `/proc/<pid>/stat` jiffies; boot leg: bare `sleep 60` carries no matching tracking id).
- Only the 2-4 stamp reds respond to shape (green under PG-off+scrubbed).
Adjudicating "is this red ambient-dependent?" requires the 2×2 matrix, not a single fenced run.

## Rule of thumb
- Cite pack results WITH the runner shape (scrubbed vs PG-pinned).
- Before calling a red "ambient-dependent", run the 2×2 (env-family pin × ambient scrub).
- A dev's EVIDENCE pointer from another lane is a LEAD, not a base attribution — re-execute at the lane's own base.

## Before/after
No runtime change — this is an adjudication-methodology lesson. Gate runtime: ~1h38m regression worker (uv sync
~4m + deliberate 200+ rep flake matrix + 2×2 sweep); the flake matrix is what killed the false "head burst"
(6F/67 head vs 1F/67 base under plain order — does not survive order-swapped/alternating/settled control).

## Cross-refs
- Evidence: `.agents/tester/EVIDENCE/2026-11-21-live-defects-p1/` (SUMMARY, S1-*, S7-stamp-2x2clean, S5-order-swap-*)
- QUARANTINE.md rows appended 2026-11-21 (4 reaper reds deterministic; ram-guard flake pair)
- Anomalies: 48f2fc17 (pack premise, worker-filed), 9ee4975a (credential-in-evidence class)
