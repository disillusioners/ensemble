# A leaked process-global test fixture manufactured an order-dependent false red

**Date:** 2026-11-02 · **Commission:** central-metrics-v1 tester functional gate · **Severity:** important (closed in-lane; the class remains a trap for any pack with cross-directory file ordering)

## The symptom

`tests/unit/metrics/test_metrics_sampler.py::TestIdentityDedupe::test_a_changed_value_without_a_publish_appends_nothing`
failed in a 626-node batch run and passed 3/3 in isolation. The lazy reading is "flaky test". The
correct reading was **order-dependent contamination** — and the mechanism is worth memorising
because the pin was doing its job: it correctly detected that its PREMISE had been silently
falsified by a different file's fixture.

## The mechanism (four hops, all in test code)

1. `tests/unit/services/test_metrics_listener_registry.py` installs
   `cm.set_publisher_cadence_provider(lambda: 0.0)` in an autouse fixture and its teardown called
   only `cm.reset_for_tests()` — **the provider is a process-global and was never restored**. Every
   test that runs after it in the same pytest session inherits cadence `0.0`.
2. At cadence `0.0`, the re-publish guard in `daemon/checkpoint_metrics.py:1035`
   (`(now - last_publish) < cadence`) is always False, so `Metric.set()` **re-publishes
   immediately** — "unpublished" stops existing.
3. A re-publish replaces the `_published` snapshot with a NEW dict object.
4. The trend sampler's identity-dedupe (`metrics_query/sampler.py:231`,
   `if self._last_snapshots.get(name) is snapshot:`) compares by object identity, sees a different
   object, and appends — so `tick()` returns 1 where the pin asserts 0.

## Why this is a TEST-ARCHITECTURE class, not product flakiness

- The sampler file cannot fail standalone (5/5 green alone) — no race, no timing assumption.
- The contaminator-first order reproduces the red deterministically on demand.
- Production behaviour was correct throughout: with a normal cadence, an unpublished change is
  genuinely not re-published.

## The fix (two halves — the second is the one that matters)

1. **Immunise the consumer** (`3eb52bc86`): the sampler file pins its own cadence
   (`monkeypatch.setattr(cm, "_publisher_cadence_provider", lambda: 3600.0)`, autouse) so its
   "unpublished" premise is guaranteed rather than inherited, and the pin gained an explicit
   PRECONDITION assertion so a future cadence change fails at the premise with a crisp message
   instead of silently testing something else. Mutation-verified: pinning the cadence to `0.0`
   (the contaminating value) turns the pin RED.
2. **Close the leak at its source** (`4f3bac7f`): the listener-registry fixture now restores BOTH
   process-global providers (`_publisher_cadence_provider`, `_cardinality_cap_provider`) in
   teardown, copying the canonical house pattern (`test_checkpoint_metrics_registry.py:47-59`
   `_restore_providers`).

**The class-closure proof is the leg that matters:** with the PRE-FIX sampler restored (the pin's
own cadence fixture removed) and only the leak closed, the contaminator-first pair is GREEN
(36/36). The leak no longer reaches any consumer — the fix is not masking by the pin. Verify any
"fixed" leak this way; "it passes now" is compatible with "the pin is doing the masking".

## The diagnostic recipe (reuse this ladder)

1. Alone ×5 — can it fail standalone? (No ⇒ not random flake, not timing.)
2. Contaminator-first ×1 — deterministic? (Yes ⇒ order-dependent.)
3. Probe the suspected global — print the provider/cadence after the contaminator file runs
   (`is_default: False` is the smoking gun).
4. Bisect the predecessor file if unknown.
5. Fix at the LEAK, immunise at the consumer, then prove closure with the consumer's fix REMOVED.

## Generalisation

Any test module that installs a **process-global** (module attribute, singleton registry, provider
callback, cache) must restore it in teardown — and "reset_for_tests()" style helpers do NOT
count as restoring unless they explicitly reset that global. The house pattern to copy is
`_restore_providers`: save-both-before, restore-both-after. When a pack's registered file order
puts `services/` before `metrics/` (as the P4 21-file composition does), any leaked global becomes
a cross-file order coupling invisible to per-file green runs.
