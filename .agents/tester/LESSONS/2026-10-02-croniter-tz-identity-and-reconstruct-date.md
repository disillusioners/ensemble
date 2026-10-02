# Lesson: croniter 6.0.0 tz-object-identity bug + reconstruct-must-not-trust-emission-date (re-gate, 2026-10-02)

## Context
Re-gate of the F2 DST fix (roundtrip phantom detector + literal reconstruct) at `34b4db96`. The fix killed the entire phantom/double-fire family (verified: 26 scenarios, zero phantoms, adversarial window probes clean) but exposed a new missed-fire defect (F4).

## Findings
1. **croniter 6.0.0 does tz-object IDENTITY (`is`) checks, not value equality.** The same expression + same instant returns a correct fire with a fixed-offset tzinfo and a wrong-hour+wrong-DATE fire with `ZoneInfo` (repro: `0 23 * * *` seeded 2026-10-31T23:00 America/New_York). Any wrapper that normalizes seeds to ZoneInfo (ours does, correctly) walks into the bad path. Characterize croniter behavior per tzinfo TYPE, not per expression.
2. **A repair pipeline that validates croniter's OUTPUT but reconstructs from croniter's EMISSION DATE inherits croniter's date bugs.** Roundtrip detection caught the wrong emission; the literal reconstruct then re-anchored HH:MM onto the wrong DAY → schedule silently skipped a fire (worse than a phantom: no dedupe net exists for cron by design). Rule: **when repairing a distrusted source's output, derive the repair's date/anchor from the trusted input (`after_aware` cursor), never from the distrusted emission.**
3. Fix shape that closes F4: `phantom_date = max(candidate_local_date, after_aware.astimezone(tz).date())` or day-advance from the cursor until roundtrip-clean; regression test must seed from the post-fire instant (the live loop's actual state), not from a synthetic mid-day seed.

## Fold-semantics adjudication note (ratified)
Across a 25h fall-back night, NO fold preference gives uniform UTC spacing for sub-daily schedules (each leaves exactly one 2h hole; wrapper post: 04Z→06Z; native pre: 05Z→07Z). "UTC continuity" as a rationale phrase is wrong; the ratifiable properties are: exactly one fire per wall-clock HH:MM, deterministic pass choice (post = later, conservative), monotonic. Document accurate properties, not appealing ones.
