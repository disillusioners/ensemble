# Registering a privileged tool category requires regenerating the tool-category artifact (+ VOCABULARY_VERSION bump)

**Date:** 2026-11-03 · **Lane:** metrics-agent-tools functional gate · **Severity:** gate-blocking when missed

## The trap

Phase 2 of the metrics-agent-tools lane registered the `metrics-read` privileged category
(`daemon/tools/_tool_registry.py:650`) — 16→17 categories, the whole 16-site pin fan-out landed
same-commit — but the **committed derived artifact** `.agents/shared/metrics/tool-categories.json`
was never regenerated, and `VOCABULARY_VERSION` (`daemon/checkpoint_metrics.py:208`) was never
bumped. Derived registry = 57 categories; committed artifact = 56. Two deterministic reds at the
final tree:

- `tests/unit/tools/test_tool_category_artifact.py::TestToolCategoryArtifact::test_artifact_covers_every_registered_category`
- `…::test_artifact_is_generated_from_the_tool_registry`

3× isolation: HEAD 3/3 FAIL vs BASE 3/3 PASS — deterministic, lane-caused.

## Why the lane missed it

`test_tool_category_artifact.py` is reachable only via the `metrics_p3_dual_side` **widened-13**
composition — it is in NO lane-owned D1 pack and in none of the lane's declared D2 sets. The
per-phase scoped legs each recorded net-new 0 at their own trees; the union composition (this
gate's G2 leg) is what exposed it.

## The rule

1. Any change that adds/renames a tool category or alters the registry-derived surface must,
   SAME-COMMIT: regenerate the artifact into a scratch path
   (`ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json`), copy it over
   `.agents/shared/metrics/tool-categories.json`, and bump `VOCABULARY_VERSION` when the pack's
   error message demands it (it does — the message names the bump).
2. Register `test_tool_category_artifact.py` as a **standing D1 leg** for privileged-category
   registration changes so the red fires at the registering phase, not at a downstream gate
   (routed as F-7 in `RESULTS/2026-11-03-metrics-agent-tools-functional-gate.md`).

## Related gotchas recorded by the same gate

- A commission's remembered count can be wrong: "808/809" matched nothing in history (pickaxe
  `-S` over all refs); always re-derive the composition from the repo's own records and prefer
  the UNION (superset) direction for net-new diffs.
- The FE dual-side pack's `LEG_KIND` heuristic (D1-marker file presence) cannot distinguish legs
  once base carries the marker; identify legs by BRANCH/GIT_SHA. Its `ERROR_SUMMARY` grep is
  vacuous for jest output; its default `METRICS_FE_LOG_DIR` is shared across legs — set a
  per-leg override before any FAILED-id comparison.
- The archived mutation-matrix pre-run manifest goes stale (1-of-4) when a post-run tidier
  renames node ids in a manifest-listed file; the manifest's `metrics_read.py` entry is the one
  that matters for "the archived log still represents the final source".
