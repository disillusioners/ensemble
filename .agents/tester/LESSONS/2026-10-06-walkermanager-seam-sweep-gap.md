# WalkerManager seam-sweep gap — test-double drift caught by wider-surface delta gate

**Date:** 2026-10-06 · Commission: designer-od-lane-fix final gates (G2b)

## Root cause
When a production seam gains a new awaited manager call (here: `ensure_mcp_preloaded` added to the council spawn lanes in `daemon/tools/instance.py` by 037adb83), every manager TEST DOUBLE must model it or the new call sites AttributeError. Commit e5af15ca swept doubles by grepping for manager constructions and patched 3 sites — but `WalkerManager` in `tests/test_governor_recursion_acceptance_walk.py` is an **empty inline harness class** (not a `MagicMock()` construction), so the grep-driven sweep never saw it. Result: 10 deterministic net-new reds at HEAD on the first G2b pass, misattributed-by-letter as regressions until mechanism analysis showed `AttributeError: 'WalkerManager' object has no attribute 'ensure_mcp_preloaded'` at the fix's own new await sites.

## Fix (test-code only, +4/−1)
```python
mgr.ensure_mcp_preloaded = AsyncMock(return_value=None)
```
in `build_manager(engine)` right after the `mgr.spawn_instance` facade wiring — mirroring the e5af15ca pattern, matching the real signature at `daemon/manager.py:7837`. Before/after for the file: 10F+6P → **16P/0F**. Commit `0859ee53` on `feature/designer-od-lane-fix` (local).

## Lesson
1. Seam sweeps must find doubles by BOTH construction pattern AND by "class with manager-shaped facade methods" — inline harness classes are grep-invisible to `MagicMock()` searches. A cheap proxy: after adding any `manager.<new_method>` call in production code, `grep -rn "class .*Manager" tests/` and check each hit for the method.
2. Wider-surface FAILED-list delta (symmetric base/HEAD, literal diff) is the gate that catches this class of drift — scoped-pack-only gating (G1's two pinned files) would have shipped the stack with 10 latent reds on an untouched file.
3. Attribution discipline matters: "net-new at HEAD" ≠ production regression until the mechanism is read. All 10 failures shared one AttributeError pointing at the DOUBLE, not the code under test.
