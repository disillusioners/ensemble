# Lessons — user-timezone-setting independent gate (2026-10-02, @ 4ac8fd4a)

## 1. Temp-worktree base-proof pattern (decisive, cheap)

To classify a red as pre-existing vs branch-caused WITHOUT touching the feature worktree or the main checkout:
`git -C <feature-wt> worktree add /tmp/<slug>-base <base-sha> --detach` → `uv sync` in it (MANDATORY: the feature worktree's `.venv` is editable-pinned to the feature tree and would silently import feature code) → verify `import daemon; print(daemon.__file__)` resolves inside the temp worktree → run the suspect file with identical invocation/env → `git worktree remove --force` → confirm feature worktree HEAD + porcelain unchanged.
This settled `deletion_paths_prune` in one round trip (byte-identical 2F/3P at base). Reusable for any "dev said green, gate says red" dispute.

## 2. Concurrent packs sharing dev PG can fake interference theories — isolation re-run kills them cheaply

Two wave-2 packs ran concurrently against `ensemble_test@localhost:5432`; when 2 reds appeared, the interference hypothesis was plausible. 3× isolated serial re-runs (5.2–5.9 s each) reproduced 3/3 — hypothesis dead, deterministic. Always spend the 3× isolation run BEFORE building the temp-worktree proof.

## 3. Test-stub rot: repo contract changes don't update old MagicMock return shapes

`SQLModelInstanceRepository.list` grew a 3-tuple `(instances, total, truncated)` on 2026-09-09 (`02918951`); `test_message_metadata_deletion_paths_prune.py` (born 09-04) still stubs `([], 0)` → ValueError inside `_get_all_instance_ids`, swallowed by maintenance's broad `except` (`maintenance.py:970-972`) → zero `adelete_thread` calls → misleading "await not found" assertion failure FAR from the root cause. Signature to remember: `ERROR maintenance.py:972 ... not enough values to unpack`. Also: `repository.py:783` still type-hints the OLD 2-tuple while `:1131` returns 3 — grep annotations when stubbing repo returns.

## 4. Canonical invocation for `-m postgres` suites (re-confirmed)

Bare `pytest tests/test_settings_api.py` is a SILENT NO-OP under default addopts (whole file deselected). Canonical: `--override-ini="addopts=" -m postgres <file>`. Always assert on "N passed", never accept "N deselected" as a result.

## 5. Env-poison guardrail is load-bearing in e2e lanes

The e2e worker's ambient shell carried `POSTGRES_HOST=10.44.0.2` (LIVE DB) and a stale `POSTGRES_PASSWORD`. Explicit `ENSEMBLE_SELF_ENV=dev` + full `POSTGRES_*=localhost/ensemble_dev` exports overrode it; post-run verification confirmed 9797/7979 untouched and alive. Never rely on absence of ambient vars — always export the full dev set for any daemon boot (env-poison family ×4 history).

## 6. FE Jest direct-binary pattern still correct; npm test script differs per branch

`node_modules/.bin/jest <specs> --ci --runInBand` worked; on this branch `npm test` is plain `jest` (the broken `--testPathPattern` variant isn't present) — check package.json per branch, prefer direct binary.

## 7. Angular settings picker is an autocomplete with auto-save

`app-searchable-select` (Material autocomplete), labels `Zone (UTC±HH:MM)`, saves automatically on selection; the Save button exists only in the no-`Intl.supportedValuesOf` fallback branch. E2e specs should assert on input value after reload, not on a Save button click.
