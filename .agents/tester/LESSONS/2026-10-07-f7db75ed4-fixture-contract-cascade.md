# Production contract change landed without fixture sweep → 86+68 node cascade

**Date:** 2026-10-07 (upgrade-resilience final gate)
**Commits at fault:** `f7db75ed4 fix(payload): ship plugins/ tree in release payload + hash stamp` (+ `55dd7f78c` predicate)

## Root cause
`stage.sh:261-276` gained a fail-closed precondition (`$REPO_ROOT/plugins` MUST exist — added to prevent a repeat of the v0.18.0 empty-plugin-registry regression) but NO test fixture that stages via the REAL `stage.sh` was updated. Three packs cascade-failed:

| Pack | Pre-fix | Post-fix | Fix |
|---|---|---|---|
| tests/test_release_journal.sh | 229P/105F (86 FIX-only vs BASE 315/19) | 316P/18F | `dbd37dbac` — seed `plugins/opendesign/MANIFEST.yaml` (dict-style `plugin.name` — string form trips `manifest.get("plugin").get("name")` AttributeError in the REAL predicate) + stub `daemon/plugin_subsystem/promote_staleness.py` (unconditional FRESH rc=0 — the predicate gate ALSO fails closed without its module) |
| tests/test_supervision_e2e.sh | 12P/43F | 52P/0F | `b78c9fbba` — same pattern |
| tests/test_supervision_journal.sh | 11P/26F | 39P/0F | `b78c9fbba` — same pattern (own FAKE_REPO, copies real lib/stage/promote) |

## Lesson
When a fail-closed precondition is added to a shared production script (`stage.sh`, `promote.sh`, `lib.sh`), grep EVERY test that invokes the real script — not just the pack adjacent to the change. The fixture pattern for plugins/ staging is now canonical in TWO places (`dbd37dbac`, `b78c9fbba`): MANIFEST (dict-style) + predicate stub. `tests/test_stage_plugins_tree.sh` fixtures are the reference for valid shapes.

## Discrimination discipline that caught it
The worker's initial "pre-existing" claim was REFUTED by the mandated BASE re-run (fix-range attribution: 86 of 105 were real FIX-only regressions in test-fixture terms). Never accept "pre-existing" without a BASE leg at the branch point.
