# Branch-correlated xdist test-isolation pollution — discrimination protocol (2026-09-05)

## Context
ReviveGuard-scope gate: `regression_unit_tools` pack (pytest-xdist `-n auto`) failed 2 tests (`TestDocsDefaultDeny` ×2) at branch HEAD in ONE sweep run. Nothing in the branch touched the failing test file or its production surface. A naive read: "flaky infra, ignore" or "stale baseline" — both wrong.

## Discrimination protocol that worked
1. **Solo vs context at HEAD**: run the failing file alone → PASS. Two-file combo with suspected polluter → PASS. ⇒ not a deterministic production break.
2. **Base reproduction (scratch worktree at merge-base)**: solo AND full-pack → clean (3/3). ⇒ not pre-existing.
3. **Retry budget BOTH sides** (same registered pack, no code change): HEAD 2P/2F with identical node set; base 3P/0F. ⇒ FLAKY at HEAD only ⇒ branch-correlated.
4. **Transience probe**: solo re-run immediately after a failing pack run → PASS. ⇒ in-process state pollution, not persistent damage.

## Signature of the class
- Victim reads process-global state with no fixture isolation (here: `daemon.tools._tool_registry.list_tools_by_category()`); failure shape = partially-booted/reset globals (KeyError on a category key that production always registers; subset-assertion missing registered tools).
- Manifests only under xdist worker adjacency: the polluter and victim must land in the same worker with polluter first — 2-file sequential combos do NOT reproduce.
- Small-n caveat: 2/4 vs 0/3 is strong with the mechanism, but report n honestly.

## Handling
- Do NOT quarantine-and-forget when base is clean — that masks a branch-introduced defect. Register as branch-correlated WATCH; make it a merge blocker; route the fix (registry-state isolation in the new tests, or mock-filter à la 2026-08-27 singleton-pollution fix).
- Polluter bisect when simple combos fail: full pack `-n 1` (deterministic adjacency) or xdist `--dist loadgroup`.
- Re-gate bar: 4× clean full-pack runs at fixed HEAD + original matrix intact.

## Discovered by
Workers `fd5ae94f` (detection) + `4a62cea4` (attribution + budget), gate 2026-09-05. RESULTS/2026-09-05-revive-guard-scope-empirical-gate.md §5.

## RESOLUTION (2026-09-05, R3 @ 1d166d54)
Fixed at the VICTIM, not the suspected polluter — and that was correct: bisection showed serial `-n 1` green while a failing xdist worker ran ZERO new guard tests; the true root was the victim's latent missing isolation (the `system_upgrade` category is lazily registered at factory-build time — `_tool_registry.py:18` empty-at-import, `instance.py:4475` — so any test reading the global registry needs its own populated-registry precondition). Fix shape: class-scoped autouse fixture that conditionally builds the registry + hard-asserts the precondition (non-vacuous), idempotent via conditional skip, no teardown (populated == booted-daemon invariant). **Lesson addendum**: when the suspected polluter's 2-file combo does not reproduce, check whether the VICTIM lacks its own precondition before hunting xdist adjacency — a victim-side autouse precondition is the deterministic fix; polluter-side fixtures cannot guarantee worker co-location.
