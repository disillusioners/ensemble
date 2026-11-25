# DECISIONS-l1.md — cpu-throughput-arc2 L1 (M1 ram-guard poll cadence)

## Scope

M1 only: RamGuardConfig.poll_interval_ms DEFAULT 200 → 1000 ms
(`RAM_GUARD_POLL_INTERVAL_MS`). No guard logic, no scoping change, no other knob.

## Rulings (verbatim, Emperor)

1. "but scan every second" — the ram-guard /proc scan cadence moves from
   200 ms to 1000 ms (one tick per second).
2. "Cpu still used when agent run task like build code, etc. we still try
   to use less cpu as possible." — CPU is still consumed during agent work
   (builds, LLM streams); we keep reducing background CPU wherever possible.

## Change

- daemon/config.py: RamGuardConfig.poll_interval_ms Field default 200 → 1000;
  `_empty_string_is_the_default` map 200 → 1000; OFF-path
  `model_construct(poll_interval_ms=…)` 200 → 1000; docstring default 1000.
- daemon/services/ram_guard/guard.py: `RamGuard.__init__` ctor kwarg
  `poll_interval_ms: int = 200` → `1000` (default only).
- Bounds (50, 2000) unchanged. Env mechanism `RAM_GUARD_POLL_INTERVAL_MS`
  unchanged. `api.py` untouched (already passes the config value).

## Measurement gate (per-tick cost, frequency arithmetic)

Fake /proc at host scale (~265 pids), timing `scan_proc` (one tick's shared
scan) in the worktree .venv:

- per-tick `scan_proc` cost: ~11.3 ms (median 11.1 ms, min 10.2, max 14.0)
  over 50 iterations, 265 pid dirs.

Baseline (live-measured, M1 Q3, cite): `ram-guard-tick` thread 16.9 / 15.7 /
18.4% of one core (tester re-derive 17.87%). That baseline is at 5 ticks/s
(200 ms). Moving to 1 tick/s (1000 ms) is a 5× reduction in tick frequency;
the per-tick cost is unchanged, so the steady-state CPU share of the scan
path falls ~5× (≈17.9% → ≈3.6% of one core) by frequency arithmetic. This
is arithmetic on a measured per-tick cost; a live re-measure is post-promote.

## Confirm-window interplay (1 s poll + 250 ms confirm deadline)

The PSS confirm runs on the EXECUTION-LANE thread, not the tick thread, under
a fixed 250 ms one-shot deadline (`pss_confirm_deadline_ms`, unchanged). The
poll cadence does NOT change the confirm deadline or its fail-open semantics:

- Breach DETECTION latency becomes ≤1000 ms (was ≤200 ms): a breach is only
  observed on a tick. The claim is then handed to the lane, which runs the
  one-shot PSS confirm with its 250 ms deadline (fail-open ⇒ kill on
  timeout/failure). The confirm itself is unchanged.
- Worst-case kill latency from actual threshold crossing: up to ~1000 ms
  detection + up to 250 ms confirm + the unchanged TERM→KILL grace (250 ms)
  ≈ 1.5 s, vs ≈0.7 s before (200 + 250 + 250). Roughly +0.8 s on the
  detection leg.
- A missed tick (tick-thread exception) increments `missed_samples` and
  drops the sample — no kill on no evidence (fail-closed on the kill
  decision: no kill without a confirmed breach). This is unchanged by the
  cadence; with 1 s polls a single missed tick simply skips one sample and
  the next tick re-evaluates. No kill is ever issued from a missed sample.
- Growth on the pathological (balloon) path: at historical balloon rates of
  1–3 GiB/s, the extra ≤0.8 s detection delay allows up to ~0.8–2.4 GiB
  more growth before the kill decision is even reached (the brief's
  1–3 GB envelope reflects the full ≤200 ms → ≤1 s window including
  confirm). The Emperor accepted this trade-off for the CPU saving.

## Trade-off disclosure

Breach-detection latency rises from ≤200 ms to ≤1 s. At historical balloon
rates (1–3 GiB/s) that is up to ~1–3 GB more growth before kill on the
pathological path. Accepted by the Emperor for the ~5× CPU saving on the
ram-guard-tick thread (≈17.9% → ≈3.6% of one core).

## Scoping-skip rationale (watch-only-guarded-pgids)

The /proc-scan scoping change (guard.py:486-493 / tree_reader.py:300-350,
restricting the scan to watched pgids) is OUT OF SCOPE for M1:

- It changes the observation set on the kill-decision surface (which pids
  are visible to membership/liveness), i.e. behavior-changing, not just a
  frequency change.
- No measurement-first evidence exists that the scoping is safe/correct
  (the guarded-pgid set must be proven to cover all members including
  late-spawned descendants and recycled pgids under the carry-forward rule).
- M1 ships the frequency change only (measurement-first, reversible, single
  knob). The scoping optimization is deferred to a follow-up commission with
  its own measurement + test plan.

## Files

- daemon/config.py
- daemon/services/ram_guard/guard.py
- tests/unit/test_ram_guard_config.py
- CHANGELOG.md
- .agents/shared/planning/cpu-throughput-arc2/DECISIONS-l1.md (this file)
