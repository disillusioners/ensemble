# Phase 4: Tests + review gate

## Objective

Pin the precedence ladder (especially the env-shadowing case), the bootstrap-read failure postures, the API surface, and the FE section in automated tests — then pass the commission's normal review gate (reviewer convenes the governor council).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | `tests/test_settings_resolution.py` — **unit, the acceptance centerpiece**: (a) shadowing case: yaml literal `llm_concurrency: 10` present + `LIMITS_LLM_CONCURRENCY=7` → effective 7, `source="env"` (this test MUST fail against any implementation that reads pydantic's env tier); (b) db > env: override row + env set → db wins; (c) yaml tier: no env, no row → yaml literal wins, `source="yaml"`; (d) default: nothing set → `default=10`, `source="default"`; (e) invalid DB value (`0`, `"junk"`) → skipped, lower tier wins, ERROR logged; (f) unknown key refused by registry | P1 t3-t5 | All green; (a) demonstrably fails on a naive pydantic-env-tier implementation (author it that way on purpose) |
| 2 | Bootstrap-read tests (same file): temp SQLite engine + seeded row → `apply_db_settings_overrides` returns Config with mutated `limits.llm_concurrency` (assert the exact value `InstanceManager.__init__` would read at manager.py:555); missing-table DB → Config unchanged, no raise; unwritable/dead engine path → Config unchanged + ERROR log naming fallback tier | P1 t5 | Mutation assertion + all failure postures green |
| 3 | `tests/test_daemon_settings_api.py` — API layer per the Phase-2 test list (GET shape/tiers incl. `value`-vs-`source` divergence, **GET `settings:read` gate fixture: 403-without / 200-with scope**, `sensitive` masking test + v1-key `sensitive=False` pin, divergence expectation concrete (post-DELETE payload: `value`=<boot-applied number, e.g. 20 — carried by `snapshot.applied_value`> alongside `source`=<new lower tier>), PUT happy/404/422, DELETE fallback, 503 unwired, PUT/DELETE `settings:write` 403, **PUT/DELETE audit-line + actor-capture assertions: INFO log emitted per write with subject + key; `updated_by` = auth subject under auth-on and NULL (no raise) under auth-off**), fixture shape modeled on `tests/test_scheduler_api.py:22-143` with settings-service DI + temp SQLite | P2 t1-t5 | Full API file green |
| 4 | FE specs (Phase-3 list) landed and green; run the full FE jest suite | P3 t1-t5 | 0 new failures vs base e0e826866; existing settings-page specs unbroken |
| 5 | **Source-order pin test** (architect Ruling 6, adopted P3): text-order grep-pin asserting `apply_db_settings_overrides(` appears in `daemon/api.py` strictly between the **lifespan-scoped** `load_config()` call (:401 — the lifespan body occurrence; the pin text must disambiguate this from the module-level/auth occurrence at :3639, e.g. anchor by byte offset ordering: pin < :401 occurrence < pin < :543 `InstanceManager(`) and `InstanceManager(` (:543) — the cheapest lockstep pin on the load-order invariant (grep-pin pattern per the secrets migration header) | P1 t6 | Pin passes on the wired lifespan; fails if the call is moved outside the window or anchored to the wrong `load_config` occurrence |
| 6 | **Migration-runner smoke test** (adopted O1): ~10-line test applying JUST `20261018_000001_create_app_settings.sql` to a temp SQLite DB via the real migration runner, then asserting the `app_settings` table exists with the exact model column set. The runner applies a migrations DIRECTORY — copy JUST `20261018_000001_create_app_settings.sql` into an isolated temp migrations dir so the runner's scan applies only that file | P1 t1, t2 | Table created by the .sql alone; column set byte-matches the model — makes migration acceptance non-vacuous on PG dev hosts |
| 7 | **Review gate (convention)**: hand to reviewer — reviewer convenes the governor council via `convene_council_with_skill(councilor_skill="code-review", max_councilors≤4)` (`agents/reviewer/workflow.md:163-194`); address findings; approver sign-off does NOT convene councils. Reviewer checklist should include: migration sorts after `20261017_000001` + dual-driver header; exactly one managed key in the registry (`sensitive=False`, import-computed `effective_max`); manager.py diffs limited to the late-import registration; config.py diff limited to the raw-limits accessor; no instance_messaging.py diffs; `config.yaml` diff comment-only; BOTH scopes registered in `SCOPE_TAXONOMY` | t1-t6 | Council verdict no 🔴 critical; exit criteria of overview §Success Criteria all demonstrable |

## Tests

This phase IS the tests. No additional layer. Full-lifespan PG e2e deliberately EXCLUDED (architect Ruling 6 — disproportionate: the daemon cannot boot on SQLite for e2e, so it would need disposable-PG spin-up); the load-bearing invariants are instead pinned by the temp-SQLite bootstrap unit (t2), the source-order pin (t5), and the migration-runner smoke (t6). The true end-to-end acceptance stays the manual completion sentence (overview §Objective).

## Coupling

- **Loose with Phases 1-3** — consumes every contract; runs after all three.
- Sequencing note: tasks 1-2 can be authored in parallel with Phase 2/3 (they only need Phase 1), task 3 needs Phase 2, task 4 needs Phase 3, task 5 needs Phase 1 t6, task 6 needs Phase 1 t1-t2.

## Risks

- Test (a) passing vacuously against the final implementation — guard by construction: write it first against a deliberately naive resolver, watch it fail, then implement.
- Scope creep in review (council suggesting live-apply or second keys) — out of scope per overview; council feedback on those goes to future-commission notes, not this slice.

## Exit Criterion

All test layers green, full backend + FE suites green vs base, council reviewed with no critical findings, feature slice ready for merge per normal review flow.
