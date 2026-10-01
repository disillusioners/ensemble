# _js_run_bounded orphaned-sleep pipe stall — feature-caused runtime regression in v0.16.9 fix #4

Date: 2026-10-01 · Gate: v0.16.9 fix-bundle verification (Gate 1b) · Found by worker c5eb825b (revived after one junk turn).

## Symptom

`tests/test_launcher.sh` FULL run at HEAD `a52a0321` exits 124 at the 300 s cap (hung in §8l, `_js_run "$JS_L"` → `launcher.sh::_journal_sweep`). Base `f87a397c` completes the same suite in **10 s** (exit 1, 192P/3F). A 900 s diagnostic soak at HEAD completes: **202P/3F in 652 s** — slow, not wedged; zero new reds.

## Root cause

Fix #4 (sweep-hang timeout) introduced `_js_run_bounded` with a watcher:

```bash
( sleep N; kill -KILL $pid ) &
```

When the watched child wins the race, the watcher subshell is SIGKILLed but its `sleep N` child is **orphaned (ppid=1) and inherits + holds the command-substitution pipe's write end**. The `$( ... )` substitution blocks until pipe EOF → every bounded call stalls ~N s even when the wrapped command completed instantly. Fix #4 routes ALL sweep calls (cat/date/kill -0) through the wrapper → ~25–45 s per sweep → suite runtime 10 s → 652 s (~65×).

Classic bash pitfall: killing a subshell does not kill its children, and any descendant holding the pipe fd extends the substitution's lifetime.

## Why it still "works"

The intended fix goal is met: nothing hangs forever (c5 real sweep at HEAD completed rc 0; soak completed). Steady-state boots are unaffected — the sweep only does work when a stale txn exists (drill scenarios without open txns ran sub-second). The regression lands on **recovery paths** (post-halt boot sweep, promote preflight `adopt_stale_txn`) where tens of seconds per sweep call is operationally significant.

## Fix shape (for the council / next commission — tree was frozen, tester did not fix)

- Detach the watcher from the substitution pipe: redirect watcher stdio (e.g. `( sleep N; kill -KILL $pid ) >/dev/null 2>&1 &` — ensure the sleep inherits NO pipe fd), or
- Kill the watcher's process group on child success (`set -m` / `kill -- -$watcher_pid`), or
- Use `timeout` command semantics instead of a hand-rolled watcher.

~1–3 lines in `_js_run_bounded` (launcher.sh / lib.sh family).

## Second lesson — premise falsification via A/B

The commission asserted the launcher full-run hang was "pre-existing since v0.13.10 (§8 BSD-date fixture family, GNU host)". The base leg falsified it at `f87a397c` on this host: base completes in 10 s. **Always run the base leg before attributing any hang/red to history** — documented families can be stale, host-specific, or fixed since. Only 8c×2 + 8g survived A/B as pre-existing.

## RESOLUTION (2026-10-01, same day)

Adjudicated RIDE-INTO-v0.16.9; fixed as **7957332e** (watcher stdio detached from the capture pipe via `>/dev/null 2>&1`) + new regression pin **8b-c6** (5 instant `_js_run_bounded 3 -- true` calls ≤5 s; calibrated buggy ≈15 s / fixed ≈0 s). Re-gate @ 7957332e: launcher FULL **12 s, 203P/3F** (only pre-existing reds); §8b **11/11 ×3, c6 = 0 s**; stop_handback **152/0**. Stall DEAD. Ledgered (non-blocking): launcher.sh:521 chained-local (bash 3.2 portability); optional unmodified §8b sanity run on macOS/BSD.

## Verification patterns that paid off

- Instrumented /tmp copy with disclosed output-only deltas (`_pass` printf) — reveals PASS progress in suites that only print FAILs; diff-verified byte-identical otherwise.
- 900 s soak OUTSIDE the pack cap as diagnostic evidence (disclosed as such; the pack itself honestly reported TIMEOUT).
- bash -x trace to pin the hang site + `ps` process-tree to catch the orphaned sleeps (ppid=1).
