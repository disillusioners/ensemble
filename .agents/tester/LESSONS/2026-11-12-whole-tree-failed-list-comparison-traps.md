# 2026-11-12 — Whole-tree FAILED-list comparison traps (RAM auto-termination gate)

Context: the RAM auto-termination gate ran the Tier-2 dual-side leg as a chunked runner (13 × 3000 node ids, `-n 8`, `--tb=line -rEf --capture=no`) on candidate and base.

## 1. `-rEf` FAILED lines are NOT bare node ids — and the suffix differs per leg
With `-rEf`, each failure prints `FAILED <nodeid> - <message>`. Messages embed PIDs, counts, and sometimes nothing at all. Comparing the raw lines produces phantom diffs; comparing only lines WITHOUT a suffix silently DROPS real failures (this run: 6 instead of 8).

**Rule:** resolve every `FAILED <nodeid>[ - <msg>]` to the bare node id (strip at the first ` - ` only when the stripped form is a collected node), then set-compare bare ids. Prove the rebuild: the merged list must equal the sum of the per-chunk lists with zero duplicates.

## 2. `grep -E '^(FAILED|ERROR)'` over-captures under `--capture=no`
Daemon log lines such as `ERROR    daemon.graph:graph.py:10020 …` are interleaved into the stream. This inflated one chunk from 1 error to 11 and another from 21 to 23.

**Rule:** anchor the extractor to `^(FAILED|ERROR) +tests/`.

## 3. INTERNALERROR is not a failure
An xdist `INTERNALERROR: assert not crashitem` aborts the session and does not produce a FAILED id. If a leg is re-run with chunks, state explicitly whether the INTERNALERROR entered the FAILED set (here: it did not). xdist worker crashes that DO land as FAILED appear symmetrically on both sides and are not net-new.

## 4. Before re-running a "lost" leg, check for live orphaned drivers
A worker died from a provider quota error mid-leg; the dispatch premise (“per-chunk logs not preserved ⇒ re-run in full”) was WRONG — the per-chunk logs existed and the orphaned driver process was still running (`/proc/<pid>/cmdline` gave the exact flags). Verifying first saved ~45 min and, more importantly, preserved flag identity between legs.

**Rule:** recover the command from `/proc/<pid>/cmdline` + the on-disk per-chunk logs before re-running anything, and prefer resume over re-run.

## 5. Quarantine classification
Candidate-only in-sweep reds that pass 3× isolated, on files with zero reach from the branch delta, are load-composition flakes — record them as a QUARANTINE row, not as net-new regressions. Deterministic net-new stays the gate metric.
