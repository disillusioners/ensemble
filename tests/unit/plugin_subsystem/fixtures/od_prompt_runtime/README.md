# od_prompt_runtime — node-evaluated runtime-string fixtures

Reference bytes for the four vendored prompt symbols the Mode-P compose
chain embeds, produced by EVALUATING the pinned vendored TypeScript with
Node (offline pure-function evaluation — no network, no daemon boot):

| Fixture | Source module | Runtime bytes |
|---|---|---|
| `DISCOVERY_AND_PHILOSOPHY.txt` | `prompts/contracts/discovery.ts` | 35652 |
| `OFFICIAL_DESIGNER_PROMPT.txt` | `prompts/contracts/official-system.ts` | 12631 |
| `DECK_FRAMEWORK_DIRECTIVE.txt` | `prompts/contracts/deck-framework.ts` | 29955 |
| `MEDIA_GENERATION_CONTRACT.txt` | `prompts/contracts/media-contract.ts` | 9541 |

`composed/` carries the same property one level up: the FULL composed
system prompt (Mode-P chain, fixed `GenerateInput` args per filename)
byte-equal against the Python composer at the recorded sha. Sizes are in
`composed/INDEX.json` (prototype 50557, deck 80514, video 60094,
prototype-skip-discovery 51103).

Regenerate (from the repo root, `node` ≥ 22.18 for default-on type
stripping):

```
node tests/unit/plugin_subsystem/fixtures/od_prompt_runtime/generate_fixtures.mjs
```

The generator assembles a throwaway eval tree from the committed vendored
sources (`snapshot_with_drift_alarm/prompts/contracts/*.ts` +
`snapshot_with_drift_alarm/runtime/deck-protocol.ts`), mechanically
rewrites `.js'` import specifiers to `.ts'` in the THROWAWAY COPIES ONLY
(Node's ESM resolver requires real extensions for type-stripped modules),
and dumps the four runtime strings. Vendored bytes are never modified.

Evidence at generation time: node v22.23.2, sizes as in the table; the
pytest corpus in `../test_opendesign_prompt_extraction.py` asserts the
PYTHON extractor (`daemon/plugin_subsystem/opendesign/ts_prompt_eval.py`)
reproduces these bytes exactly — a differential test, not a copy check:
the Python side computes the strings from the vendored TS source with its
own scanner + bounded evaluator.

If a sync-runner pull lands upstream prompt changes at a new pin,
regenerate this fixture set in the same change and reconcile the
mirrored evaluator (drift-alarm rule: re-apply or drop, update the log).
