# Lesson: croniter DST traps + driving TZ code instead of trusting its tests (scheduled-tasks validation, 2026-10-02)

## Context
Independent validation of `feature/scheduled-tasks` @ `3a939a76`. All 277 commissioned tests green, yet driving the production TZ/cron paths with independently computed expectations found real defects the suite encodes away.

## Findings that motivated this lesson
1. **croniter 6.0.0 recurring-cron DST defects (production path):** daily 06:00 America/New_York fires TWICE on spring-forward day (phantom 05:00 EDT + real 06:00 EDT on 2026-03-08), fires 1 h late on fall-back day (07:00 EST on 2026-11-01), and emits nonexistent local times with pre-DST offsets for gap-day crons. Idempotency keys are one-shot-only by design, so the phantom = duplicate dispatch. The unit DST tests pass because they assert the anchor path / narrow cron cases and never cross a real transition on the cron path.
2. **Anchor gap-shift contract drift:** `anchor_local_to_utc` docstring says "next valid local time" (02:30 → 03:00) but the impl does `naive + gap_seconds` (→ 03:30) — and the tests assert the implementation value. Classic assertion-drift family (cf. 2026-08-18 reasoning-echo drift).

## Method (reusable)
- Drive the PRODUCTION resolver/service functions, never test doubles; compute expectations **in the driver** from `zoneinfo` first principles, never from the code under test.
- For recurring paths, iterate `get_next` ACROSS both 2026 transitions (Mar 8 / Nov 1 for US) and assert each UTC instant — single-step or same-day assertions miss the phantom/drift chain.
- Hand-compute the anchor table before writing the driver so a transcription bug in the driver can't silently agree with the code: gap 02:30→03:00 EDT (07:00Z) per next-valid semantics; fold 01:30→first occurrence 05:30Z (EDT); daily 06:00 NY = 11:00Z(EST)/10:00Z(EDT)/11:00Z(EST) around the two transitions.
- Also generalized: **exact-kwargs / exact-order pins break the moment a kwarg is threaded through a wrapper** (F3: `idempotency_key=None` broke `test_manager_wrapper_forwards_queue_id` while the feature itself was correct). Prefer asserting the forwarded subset you care about, or update pins in the same commit that threads the kwarg.

## Before/after
Before: 277/277 green, pack deterministic ×2 — looked merge-ready. After driving: 1 critical (croniter DST double-fire) + 1 contract drift + 1 stale pin. Green suites ≠ correct TZ behavior; DST correctness must be driven across real transitions.
