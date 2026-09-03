# M2 Gate Lessons (2026-09-03, feature/mission-class @ 8eddeb3d → gate HEAD)

## L1 — grep on `.agents/` paths silently returns no-matches (TOOL GOTCHA)
`grep_files` with an explicit `.agents/tester/PACKS.md` path returned "No matches found" for a string
that verifiably existed at line 122 (section header) — the hidden-tree skip applies even to explicit
`.agents/` paths, while `read_file` reads the same file fine. Consequences this gate:
- I dispatched a "repair the dropped registration" task off the false negative.
- The repair worker's count==0 assertion returned 1 → it correctly REFUSED to write (preventing a
  duplicate-section corruption of PACKS.md).
- Two worker reports ("pack is not registered in any PACKS.md") were the same false-negative class.
**Rule: verify `.agents/` content with `read_file`, never trust grep no-match on hidden-tree paths.**

## L2 — Authored pack scripts shipped with the strict-bash RESULT-echo defect (11/18 packs)
Every partition pack + one acceptance pack authored in commit 3b0b98b6 contained:
`EXIT_CODE=$?` on its own line after the pytest invocation under `set -euo pipefail` — any pytest
non-zero exit kills the script BEFORE the `RESULT:` echo (silent wrong-exit-code class).
Fix pattern (commit 8132747f, missions_api): `EXIT_CODE=0` initializer + `|| EXIT_CODE=$?`
list-context capture. Replicated per-pack by partition runners (guard commits f6e03bc4, f617fde6,
10a50c2a, dfa81292, 81b3f532, 4cd25db0, a799c5f8, 7f925c6c, 5548a92c, + m_r/i_q entries).
**Rule: pack authoring MUST pre-check for the bare `EXIT_CODE=$?` pattern; house packs now carry the
guard, and partition dispatch messages bake in the pre-check instruction.**

## L3 — Constitution census labeling: 23/6/1 = writers / mints / creators
The task shorthand "census 23/6/1" reads writers=23, KNOWN_MINT_SITES=6, KNOWN_JOBITEM_CREATORS=1
(NOT writers/creators/mints). Discovery initially reported the pair "swapped"; the blueprint +
introspection agree on writers 23 | mints 6 | creators 1. Always print labeled sizes.

## L4 — Missions OpenAPI contract: routes stay REGISTERED while OFF
The M2 kill-switch (`ENSEMBLE_MISSION_PROJECTION_ENABLED`, default OFF) gates IN-HANDLER
(missions.py:251-259 raises 404 pre-query); `daemon/api.py` includes the router unconditionally.
§8.4 + router docstring + unit pin `test_off_routes_stay_registered_in_openapi` all document:
OFF ⇒ 404 + ZERO queries but /openapi.json still documents both paths. A dispatch expectation of
"OFF-hidden/ON-visible" was wrong; the probe correctly pinned documented behavior. OFF zero-query
verified with a live positive control (ON list = 2 SELECTs through the same engine listener).

## L5 — Missions pagination: limit=0 clamps to 1, not to default 10
`resolve_page()` semantics: limit<1 ⇒ 1 (unit pin `test_limit_clamped_to_minimum_one`); default 10
only when absent; max 100. Paraphrasing "limit=0 ⇒ default" in a dispatch produced a spec-vs-impl
discrepancy the probe resolved in favor of the pinned contract.

## L6 — Integration partition shape: pyproject addopts marker-filter is the prior-gate protocol
`addopts = "-m 'not integration and not postgres'"` deselects integration-marked tests inside the
regression partition (~262 deselected) — this IS the house full-suite shape (baseline-comparable
across M1/Fix-B/Fix-C gates). Integration coverage is carried by dedicated packs; those need
`--override-ini="addopts="` (house pattern) or they collect zero tests (exit 5). New integration
test files decide their own fate via `pytestmark` — the runtime-contract probe (unmarked) runs
in-suite AND in its pack; the OFF probe (marked) runs pack-only.
