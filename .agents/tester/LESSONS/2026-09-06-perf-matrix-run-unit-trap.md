# Lesson — perf-matrix run-unit trap: class-scoped selection is structurally vacuous

Discovered 2026-09-06 during the defer-self-witness full gate (attribution worker attr-p8-perf).

## The trap

`tests/performance/test_message_api_cost.py` is a PRODUCER/CONSUMER module:
- `TestPerfMatrix` (producer) — 6 parametrized cells, each ~30s real-PG measurement, each writing
  into the MODULE-LEVEL dict `_CELL_RESULTS` (in `finally`-protected teardown).
- `TestPerfMatrixAcceptance` (consumer) — asserts on `_CELL_RESULTS` contents; atomically 6×-skips
  when the dict is empty (module's own honesty contract: "skip is NOT green", lines 82/925-928).

Selecting ONLY the consumer class (`pytest ...::TestPerfMatrixAcceptance`) never runs the producer
⇒ `_CELL_RESULTS` is empty ⇒ the consumer skips in 0.10s with EXIT 0. That reads as "3/3 PASS" while
validating NOTHING. A false green.

## The flake mechanics (partition context)

Under N-way concurrent gate load, some PRODUCER cells die or get timeout-killed mid-measure. A
hard SIGKILL bypasses the `finally` DB-drop (orphaned `ensemble_blob_prune_*` DBs observed) and can
leave `_CELL_RESULTS` PARTIALLY populated. Partial state does NOT trigger the atomic skip — instead
the consumer fails with `KeyError _CELL_RESULTS[(100,150)]` / "missing cells" asserts. Different
acceptance nodes fail depending on which cells died ⇒ "different perf test fails each run" signature.

Classification this gate: LOAD-FLAKE (3/3 solo whole-module PASS, 167–179s each; zero solo failures).
WATCH entry added to QUARANTINE.md (not quarantined — solo-deterministic).

## Rules

1. **Run unit for this file MUST be the whole module** (producer + consumer in one process).
   Never class-scope it.
2. **Solo runtime ~3 min = ~60% of the 5-min cap** — under parallel load it is a contention victim.
   Future full-gate waves: give this pack a dedicated low-concurrency slot or xdist isolation group,
   or expect partition-context KeyErrors (classify LOAD-FLAKE, don't re-litigate).
3. **"Exit 0 with atomic-skip" output is a red flag** — when a suite documents "skip is NOT green",
   a suspiciously fast green run means the selection was vacuous. Check node counts vs expectation.
4. Mid-run branch drift (user commit landing during a long run) invalidates that run as evidence —
   discard + recover via disposable worktree at the bracketed commit (pattern proven this gate).
