# Plan Overview: Worker-slot sizing — un-hardcoded pool config + DB-managed keys + FE hint + agent settings tools

Date: 2026-10-18
Author: planner[v2] via plan-creation worker — rulings D1–D8 folded verbatim from the BINDING `architecture-recommendation.md` (this directory; every anchor spot-checked in this worktree)
Status: Ready for Review
Worktree: `/home/nea/ensemble-src-wt-settings-worker-slots` (branch `feature/settings-worker-slots`, base latest @ d0b475466)
Companion docs: `architecture-recommendation.md` (**BINDING** — D1–D8; do not re-derive), `decisions.md` (rulings + inherited invariants + binding warnings), `phase1-plan.md` … `phase4-plan.md`

---

## Objective

Both worker-pool sizes (`worker_pool_size`, `chat_worker_pool_size`) become ordinary config resolved by the existing 4-tier ladder (**DB > os.environ > yaml literal > default**) and applied at boot — the constants remain only the default tier — with the derived invoke-semaphore cap following the resolved default pool, the stale `effective_max` two-truths artifact removed, and an agent-facing `settings` tool category (ari + leader) that reports and writes managed keys with an honest never-live restart contract.

Testable completion sentence: *With `LIMITS_WORKER_POOL_SIZE=8` in the environment and a DB override `limits.chat_worker_pool_size=3`, a daemon restart constructs 8 default-pool and 3 chat-lane workers, renders `workers=3` in the boot line, sets the invoke-semaphore cap to 7, and both the settings page and `settings_get` report all three managed keys with value/source/divergence and explicit "applies at next daemon restart" semantics — without editing any code, constant, or config.yaml.*

## Background (evidence-verified, this worktree @ d0b475466)

- **The knobs are compile-time constants today:** `WORKER_POOL_SIZE=5` (`daemon/constants.py:86`), `CHAT_WORKER_POOL_SIZE=2` (`daemon/constants.py:94`, comment :92-93 declares "hardcoded, NO ENSEMBLE_* flags; activation = rebuild+restart"). Consumed at `PoolOrchestrator.setup` (`daemon/services/pool_orchestrator.py:163` default kwarg, `:529` chat lane, `:567` boot line) via the manager facade (`daemon/manager.py:8358-8384`, default kwarg `WORKER_POOL_SIZE`).
- **The real concurrent-graph cap is emergent:** `limits.llm_concurrency` is a semaphore on the shared loop (`daemon/manager.py:562`; anchor corrected, iteration 002 — was :555) but every graph executes from a pool thread — true parallelism = `min(llm_concurrency, worker_pool_size + chat_worker_pool_size)` = min(10, 5+2) = **7** today. Raising either leg past the other is a silent no-op. Before this commission the pools were invisible constants; after it, ALL legs of the min() are visible, DB-managed, and honestly reported.
- **The machinery is proven:** resolver ladder reads `os.environ` explicitly (`daemon/services/settings_service.py:334-350`), boot apply mutates Config in place between `load_config()` and `InstanceManager` (`daemon/api.py:413-420`, `_set_config_value` at settings_service.py:608-613/:716), registry at `:128`, frozen GET contract (`daemon/routers/schemas.py:1831-1926`). New `limits.*` keys ride it with ZERO changes to any of these — the yaml capture site copies the whole `limits:` section (`daemon/config.py:5707-5721`), so the env-shadowing defect structurally cannot repeat.
- **Two drift traps die here:** `effective_max` (`settings_service.py:116` field, `:144` import-time computation `5+2=7`, FE hint literal `settings.component.html:343-345`) is the live two-truths specimen — REMOVED, not made dynamic; and the dormant `WorkerPool.__init__(num_workers: int = 4)` (`daemon/services/worker_pool.py:1252`) silent default — becomes a loud `ValueError`.
- **The invoke-cap defect:** `_get_invoke_semaphore` (`daemon/utils.py:588-603`) reads `WORKER_POOL_SIZE` lazily at FIRST USE and never again — once pools are configurable, a frozen-constant cap silently diverges from the real pool. Fixed by an explicit boot-set override with a loud late-set guard.
- **The tool category is the secrets playbook:** 4 `_tool_registry.py` touch-points + `instance.py:6128-6130` construction extend + meta.json-only agent surface (both UNSEALED) + same-PR frozenset-pin updates.

## Scope

### In Scope

- Two additive `LimitsConfig` Fields (`worker_pool_size`, `chat_worker_pool_size`, defaults imported FROM `daemon.constants`, `ge=1`) → env names `LIMITS_WORKER_POOL_SIZE` / `LIMITS_CHAT_WORKER_POOL_SIZE` via the existing `env_prefix="LIMITS_"` (`daemon/config.py:588`). ZERO change to `load_config`/`raw_yaml_limits`/capture site.
- Two `ManagedKeySpec` registry entries mirroring the Fields (move-together rule: constants → Field default → registry default is ONE truth chain).
- Pool-seam sentinel: `num_workers: int | None = None` on `PoolOrchestrator.setup` (`pool_orchestrator.py:163`) + `manager.setup_worker_pool` (`manager.py:8358`); resolution ONCE inside `PoolOrchestrator.setup` reading boot-applied `self._manager.config.limits`; chat lane fed by config reader (no new kwarg — doctrine preserved); `WorkerPool.__init__` (`worker_pool.py:1252`) `None` + loud `ValueError` (drift trap killed).
- Derived invoke-cap override: `set_invoke_semaphore_cap(resolved_workers − 1)` called from `setup` right after default-pool construction (`daemon/utils.py:588-603` rework; setter raises if singleton exists; constant stays as test/no-boot fallback).
- `effective_max` REMOVED entirely (field `:116` + computation `:144` + pin `tests/test_daemon_settings_api.py:455` in ONE commit); registry description + FE hint reworded to reference managed keys, never literal numbers.
- `daemon/constants.py` edits (comment rewrite :86-93 to the new default-tier discipline; DELETE dead `LLM_CONCURRENCY` :329) **+ the SEALED_SHA_BASELINES re-pin as ONE atomic commit** (Ruling 8 procedure).
- Agent-facing tool category `settings`: `daemon/tools/settings_tools.py` (3 flat tools `settings_get`/`settings_set`/`settings_clear`), 4 `_tool_registry.py` touch-points, `instance.py` construction extend, `agents/ari/meta.json` + `agents/leader/meta.json` allow-list entries, same-PR privileged-pin test updates.
- FE hint rewrite (`settings.component.html:343-345`) — registry-driven rendering means ZERO other FE code.
- D6 test-pin/harness updates (19-row table after iteration 002 — council B2 added rows 16b/16c; distributed to the phases that own their gates) + 3 new test groups (resolved-path pool tests with SPECIFIED config-object injection, two-key ladder test, tool-lane contract tests) + the F3 autouse reset fixture.

### Out of Scope

- **The scheduler lane** — `SCHEDULER_DEFAULT_MAX_CONCURRENT` has its own semantics; a separate commission.
- **Promote** — merge to `latest` only; NO release, NO nonce ceremony.
- **Live re-arm** — restart-required v1; no runtime semaphore/pool resizing (prior commission's binding constraint).
- **New migration** — overrides land in the existing `app_settings` table (migration `20261018_000001` present in this worktree); new keys are registry entries invisible to the schema (Ruling 7).
- **Other managed keys** — exactly the two pool keys join `limits.llm_concurrency`; adding more is registry-only later.
- **Compaction chunk concurrency** (`config.py:1325`, default 3) — bypasses the semaphore; not exposed.
- **FE code beyond the hint string** — new int/`ge=1`/`requires_restart` keys render registry-driven with zero component logic (verified: `@for` loop :254-320, restart badge :261-263, number input :303-311, TS validation :1022-1034 all generic).
- **`daemon/tools/_auth.py`** — `TOOL_REQUIRED_AGENTS` untouched (`SettingsService` is not agent-backed).
- **Agent `.md` files** — meta.json-only surface (secrets P1 precedent); the sealed `.md` pins untouched (D8 names exactly 3: ari/workflow.md, ari/rule.md, leader/rule.md — corrected, iteration 002, council O7).
- **The 7 OTHER pre-existing stale seal pins** (5 rule.md — approver/developer/planner/reviewer/tidier — plus install-mermaid-cli.md and ari/workflow.md) — out-of-scope noise; do NOT attempt to fix them. `daemon/constants.py` is the 8th stale pin and IS re-pinned by this commission (corrected, iteration 002 — council B1).

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Config layer + pool seam + invoke cap | Fields + registry + sentinel resolution + cap override + `effective_max` removal + constants edit **with seal re-pin in the same commit** | 8 | Tight with 2 (service accessors); owns its own D6 gate rows | pending |
| 2 | Agent-facing settings tool category | `settings_tools.py` + registry touch-points + construction + same-PR pin updates + tool tests | 5 | Tight with 1 (service/snapshot accessors); tight with 3 (category name frozen here) | pending |
| 3 | Surfaces: allow-list + FE hint | ari/leader meta.json + FE hint rewrite + manual sanity | 4 | Tight with 2 (category name); independent of 1 | pending |
| 4 | Verification + seal re-check + merge | Full suites, pre-merge seal verification, env-discipline grep, review gate, merge to latest (NO promote) | 5 | Loose — consumes all contracts | pending |

**Phase-grouping note (deviation from the dispatch's suggested P1–P4, and why):** phases follow the architecture recommendation's own A–D list (config-seam / tools / surfaces / close-out). Two load-bearing adjustments: (1) the `constants.py` edit + `SEALED_SHA_BASELINES` re-pin live in **Phase 1 as ONE commit** (binding warning: `constants.py` is ALREADY stale at base — one of the 8 red pins, council B1 — so edit + re-pin must flip that pin red→green atomically; "edit in P1, re-pin in P4" would leave the corrected behavior and the pin inconsistent across P2–P3); (2) the FIVE privileged-frozenset pin updates live in **Phase 2** (the same-PR rule per A14/D18 — the frozenset changes in P2's commit, so the pins must change in the same commit or every P3/P4 gate run is red; council B2: the fifth pin is `tests/unit/tools/test_secrets_registration.py:212`).

**Phase legend (O4):** architecture-recommendation.md Phase A ↔ plan Phase 1; Phase B ↔ Phase 2; Phase C ↔ Phase 3; Phase D ↔ Phase 4.

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Phase 1 | — | **tight** — Phase 2's tools call `get_settings_service()`/`get_settings_resolution_snapshot()` (routers/settings.py:146/:163) and read registry entries P1 wrote; P1's constants+re-pin commit must land before P2's first gate run (tripwire green-on-constants) | independent (meta.json + FE hint never import P1 code) | loose (P4 runs P1's gate rows again in the full suite) |
| Phase 2 | tight | — | **tight** — the category NAME `settings` + tool names are frozen in P2; P3's meta.json allow-list entries must match byte-for-byte | loose |
| Phase 3 | independent | tight | — | loose |
| Phase 4 | loose | loose | loose | — |

**Cross-phase runtime couplings (the four the dispatch names):**

1. **Pool sizes ↔ llm_concurrency emergent bound:** real LLM parallelism = `min(limits.llm_concurrency, worker_pool_size + chat_worker_pool_size)` — today min(10, 5+2)=7. All three legs are boot-applied managed keys after P1; the registry descriptions (both new entries + reworded `llm_concurrency` at `settings_service.py:145-153`) and the FE hint must state this RELATIONSHIP honestly — never a literal number.
2. **Invoke-cap ≤ default-pool − 1:** the override is `max(1, resolved_workers − 1)` computed from the SAME resolved value that sized the pool in the same function (`PoolOrchestrator.setup`) — divergence is structurally impossible; the setter's `RuntimeError` guard converts any future late-set into a loud failure. Boot ordering makes it safe: pools + cap are set inside lifespan (`api.py:608`); every `invoke_agent_and_wait` caller is a post-lifespan agent-tool body.
3. **Constants ↔ registry default move-together:** `daemon.constants` → `LimitsConfig` Field default (`config.py` import) → `ManagedKeySpec.default` (registry) is ONE truth chain; `tests/unit/test_constants.py:125-136` keeps the value pins (constants remain the canonical default tier) — if a constant value ever changes, Field + registry follow automatically via the import chain, and the seal re-pin procedure fires.
4. **Boot ordering (inherited, unchanged):** `load_config()` (api.py:401) → `apply_db_settings_overrides` in-place mutation (api.py:413-420) → `InstanceManager(...)` → `setup_worker_pool()` (api.py:608) → pool construction reads the ALREADY-resolved config. No new boot-order invariant is created — the two new keys are ordinary `limits.*` config paths on the existing seam. The kill-switch (`USE_WORKER_POOL=false`, pool_orchestrator.py:189-195) returns early BEFORE resolution side-effects: no pools, no boot line, no cap override (constant fallback — today's no-pool behavior).

## Commit-discipline invariants (binding warnings encoded)

1. **Seal tripwire one-commit rule:** the `constants.py` edit + the `SEALED_SHA_BASELINES` re-pin (`tests/test_chart_image_delivery_e2e.py:1346`, dict at :1325) are ONE consistent commit inside Phase 1. **Red/green model (corrected, iteration 002 — council B1):** the 35-entry dict is 27 green / 8 stale at base and `daemon/constants.py` is ALREADY stale (pinned 64c1dbe3…, actual 7026ed31…, last-touch c6f14a72c 2026-10-12) — there is NO "goes red when touched" transition; the commit flips that pin red→green atomically with the behavior change. Post-re-pin targeted run: red on EXACTLY the 7 OTHER stale pins, GREEN on constants.py — reds 8→7. Never leave constants.py red across a phase boundary or a push.
2. **Conftest env discipline:** `LIMITS_WORKER_POOL_SIZE` / `LIMITS_CHAT_WORKER_POOL_SIZE` must NOT enter conftest (mirrors the `LIMITS_LLM_CONCURRENCY` exclusion at `tests/test_daemon_settings_api.py:74` / `tests/test_settings_resolution.py:28`); tests that need them monkeypatch explicitly.
3. **Same-PR pin rule:** the 5 privileged-frozenset pins move in Phase 2's commit, not a later phase (corrected, iteration 002 — council B2: five, not four).
4. **Line-number correction (supersedes the architecture doc's `~` anchors — verified in this worktree):** the privileged-frozenset exact-set pins — FIVE of them (B2) — live at `tests/unit/tools/test_upgrade_registration.py:136-153` (frozenset assert starts :136), `tests/unit/tools/test_attestation_registration.py:184-199`, `tests/integration/test_maintenancer_spawn_resolves_tools.py:318-330` (count comment 7→8), `tests/integration/test_service_tool_flag_off_byte_identical.py:802-846` (`test_privileged_category_set_is_pinned` — NOT in `test_upgrade_registration.py`), and `tests/unit/tools/test_secrets_registration.py:212` (the fifth, omitted from the original list).

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Seal re-pin done sloppily (wrong sha, extra entry touched) → silent seal integrity loss | High | Low | Ruling 8 procedure is mechanical: ONE file, ONE dict entry (:1346), targeted run must show red on exactly the 7 OTHER stale pins and green on constants.py (reds 8→7 — corrected, iteration 002, council B1); any other state = stop and re-derive |
| 2 | `num_workers=None` sentinel leaks to an unlisted caller relying on the old default kwarg | Medium | Low | Caller set (corrected, iteration 002 — council F5): 1 production caller (api.py:608) + ~20 test call sites, ALL passing explicit values → behavior unaffected; re-grep `setup_worker_pool(` + `PoolOrchestrator.setup(` at edit time and confirm no NEW no-kwarg caller |
| 3 | `WorkerPool(None)` + ValueError breaks an unlisted direct constructor call | Medium | Low | Grep `WorkerPool(` across daemon/ + tests/ before merge; every hit must pass explicit sizing (both production sites do today); the loud failure is the DESIRED drift-trap kill |
| 4 | Invoke-cap override fires after first use (future code invokes during lifespan) | High | Low | Setter raises `RuntimeError` loudly (Ruling 4); today's caller set is strictly post-lifespan (grep-verified: image/chart/knowledge/compare tools + kb_server, all agent-tool bodies) |
| 5 | Resolved pool size < 3 breaks the invoke-cap ≥2 sanity pin in exotic envs | Low | Low | Override follows whatever size resolved; the pin (`test_messages_ref_path_worker_pool.py:157-165`) asserts ≥2 only in default-sized harness runs; document `LIMITS_WORKER_POOL_SIZE ≥ 2` as the practical floor (`ge=1` is the enforced floor) |
| 6 | FE hint rewrite goes stale again when a future 4th lane appears | Low | Low | New wording references managed keys, not numbers; a 4th lane = a new managed key + its own prose |
| 7 | Tool-lane audit diverges from router audit shape over time | Low | Low | Same `[Settings]` INFO-line prefix; tool lane tagged `[Settings][tool]`; docstrings cross-reference (Ruling 5) |
| 8 | Chat-lane doctrine dispute ("config reader = a kwarg by stealth") | Low | Low | Ruling 2 reconciles explicitly: public signature stays single-knob; doctrine intent (call-site discipline) preserved byte-for-byte; `:180-186` docstring + constants comment rewritten to the new mechanism |
| 9 | Test shards on the shared box contend while booting resolved-pool managers with larger pools | Low | Medium | New resolved-path tests use modest overrides (8/3) + existing shard discipline; no test exceeds current prod max |
| 10 | Description/hint prose drifts from the min() relationship if a leg is renamed | Low | Low | Prose names managed KEYS (greppable) not numbers; reviewer checklist item |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Env tier effective for both new keys despite yaml literal | Unit: yaml literal present + `monkeypatch.setenv("LIMITS_WORKER_POOL_SIZE","8")` → resolver returns 8, `source="env"` (ladder test reuses the `tests/test_daemon_settings_api.py:262` harness) | passes; fails against any pydantic-env-tier impl |
| 2 | DB tier beats env (full ladder) | New two-key ladder test: DB override + env set on `worker_pool_size` → db wins; `chat_worker_pool_size` env-only → env wins | 100% of ladder assertions pass |
| 3 | Boot-applied pools construct the pools | Resolved-path integration tests with SPECIFIED injection (F4, iteration 002): config-object override `manager.config.limits.worker_pool_size = 8` → `len(manager._worker_pool._workers) == 8`; `manager.config.limits.chat_worker_pool_size = 3` → boot line contains `workers=3` (env/DB tier fidelity is covered separately by criteria 1-2) | both green; explicit-kwarg harness tests unchanged-green (byte-compat) |
| 4 | Invoke cap follows resolved pool | Fresh-module pin (config-object injection per F4): `manager.config.limits.worker_pool_size = 8` → `utils._get_invoke_semaphore()._value == 7`; no-boot fallback still 4; an AUTOUSE reset fixture zeroes `_invoke_semaphore` + `_invoke_cap_override` around every test (F3 — mirror test_messages_ref_path_worker_pool.py:108-115) | both green; late `set_invoke_semaphore_cap` after first use raises RuntimeError; fixture present (no order dependence) |
| 5 | `effective_max` fully gone, GET contract frozen | `grep -rn effective_max daemon/` = 0 hits OUTSIDE the unrelated `blueprint_matcher.py:189/:228` locals (allowlisted — O3, iteration 002); `DaemonSettingEntry` schema diff = none; new pins cover both entries (env_var/default/ge/requires_restart/sensitive) | grep clean modulo allowlist; schema-identical; pins green |
| 6 | Tool category live + honest | Tool-lane tests: `settings_set` result carries `written`/`applies_at: "next daemon restart"`/`running_value_still`/`live: false`; `settings_get` carries per-key `value/source/divergent`; unwired service → `{"error": "RuntimeError"}`; registry-gate rejects unknown keys; non-allow agents hold zero settings tools | all contract tests green |
| 7 | Privileged-pin integrity | All 5 frozenset pins updated in P2's commit (incl. `test_secrets_registration.py:212` — B2); `test_maintenancer_spawn_resolves_tools.py` count comment 7→8 | 5/5 green |
| 8 | Seal integrity | Targeted seal run: red on EXACTLY the 7 OTHER stale pins {install-mermaid-cli.md, ari/workflow.md, approver/rule.md, developer/rule.md, planner/rule.md, reviewer/rule.md, tidier/rule.md}, GREEN on `daemon/constants.py` — reds 8→7 (corrected, iteration 002 — council B1); no other diff in `SEALED_SHA_BASELINES` | exactly that state |
| 9 | Env discipline | `grep -rn "LIMITS_WORKER_POOL_SIZE\|LIMITS_CHAT_WORKER_POOL_SIZE" tests/conftest.py` = 0 | grep clean |
| 10 | Full suites green, scope tight | backend pytest + FE jest vs base d0b475466; `ls daemon/migrations/versions/` diff = empty; registry contains exactly 3 managed keys | 0 new failures; both greps clean |

## Files-to-Touch Summary

**Edited (backend):**
- `daemon/config.py` — +2 Fields on `LimitsConfig` (after :593), defaults imported from `daemon.constants`; +1 import if absent
- `daemon/services/settings_service.py` — +2 `ManagedKeySpec` entries (after :128 block); −`effective_max` field (:116) + :144 computation; reword `llm_concurrency` description (:145-153)
- `daemon/services/pool_orchestrator.py` — `setup()` sentinel default + one-point resolution of BOTH lanes (:163, :483-499, :527-544, :566-567); docstring rewrite (:180-186); `set_invoke_semaphore_cap` call; kill-switch ordering untouched (:189-195)
- `daemon/manager.py` — `setup_worker_pool()` default `None`, docstring touch (:8358-8384)
- `daemon/services/worker_pool.py` — `__init__` `num_workers` default → `None` + loud `ValueError` (:1252)
- `daemon/utils.py` — `_invoke_cap_override` + `set_invoke_semaphore_cap()` + fallback read (:588-603); :639 deadlock note stays true
- `daemon/constants.py` — comment rewrite :86-93 (default-tier discipline; "rebuild+restart" → "restart"); DELETE dead `LLM_CONCURRENCY` :329 — **SEALED; re-pin per Ruling 8 in the SAME commit**
- `daemon/tools/_tool_registry.py` — 4 touch-points (labels corrected, iteration 002 — council F2): `DYNAMIC_TOOL_NAMES` (:23, set :137-142), `PRIVILEGED_TOOL_CATEGORIES` (:223-294, 8th entry + provenance comment), `CATEGORY_MODULES` (:645 dict; `secrets` entry sits at :813-814), `KNOWN_TOOL_NAMES` tail (:848, :1055-1060) — BOTH name-sets need the 3 names (`test_frozen_tool_name_discovery`) + stale "triple-pin" comment fix :286-292
- `daemon/tools/instance.py` — construction list-extend after the secrets block (:6128-6130)
- `agents/ari/meta.json` — +`"settings"` in `tools.allow` (after :35 `"secrets"`) — UNSEALED
- `agents/leader/meta.json` — +`"settings"` in `tools.allow` (:14-15) — UNSEALED

**New (backend):**
- `daemon/tools/settings_tools.py` — factory `create_settings_tools(manager, current_instance_id)` + 3 flat tools + `CATEGORY_NAME`/`CATEGORY_DOC` + `_resolve_service()` + tool-lane audit line

**Edited (frontend):**
- `frontend/src/app/pages/settings/settings.component.html` — rewrite hint :343-345 (ONLY FE change)

**Tests:** `tests/unit/test_constants.py` (:125-136 docstrings; :192-196 completeness set NO change), `tests/test_daemon_settings_api.py` (:448-455 pin swap; new two-key ladder test), `tests/integration/test_chat_source_boot_line.py` (:74/:98-99 green as-is + new resolved-render test), `tests/integration/test_chat_source_pool_wiring.py` (:121-127 + resolved-path sibling), `tests/integration/chat_source_harness.py` (:344 NO change), `tests/integration/test_messages_ref_path_worker_pool.py` (:157-165 NO change + new cap pin + :108-115 reset-fixture mirror), the 5 privileged-pin files (see invariant 4 — B2), NEW `tests/…/test_settings_tools.py`.

**Explicitly NOT touched:** `daemon/api.py` (seam already correct), `daemon/routers/settings.py` (generic over registry; accessors exist at :146/:152/:157/:163), `daemon/auth/scopes.py` (`settings:read`/`settings:write` already registered :20/:91-98), `daemon/tools/_auth.py`, `daemon/routers/schemas.py`, all sealed `.md` agent files, FE component TS, any migration file.

## Research Insights

- The env tier never routes through pydantic: yaml reaches `Config(**config_dict)` as **init kwargs** (config.py:5721→:6068) and init kwargs beat env — that is exactly why `LIMITS_LLM_CONCURRENCY` was silently dead pre-P1, and why the settings ladder reads `os.environ` explicitly (settings_service.py:334-350) and applies by in-place `setattr` (settings_service.py:608-613/:716). New keys inherit this for free; the shadowing defect structurally cannot repeat.
- The capture site copies the WHOLE `limits:` section verbatim (config.py:5707-5721), so new yaml keys flow to the raw-limits snapshot with zero accessor edits.
- The `secrets` category (2026-10-17, P1) is the exact mechanical playbook for P2: registry frozenset + KNOWN_TOOL_NAMES + CATEGORY_MODULES + frozen fallback + instance.py construction extend + 4 pin updates + meta.json-only agent surface + RuntimeError→class-name envelope (`secrets_tools.py:146-197/:257-268`).
- `LLM_CONCURRENCY` at `daemon/constants.py:329` is confirmed dead: zero code importers, only docstring/prose mentions, absent from the `TestConstantsCompleteness` expected set (`tests/unit/test_constants.py:192-196`, grep-verified). Deletion trips only the seal.
- `_get_invoke_semaphore` is LAZY but not DYNAMIC (utils.py:588-603): first use happens strictly post-lifespan, so a boot-time override is always in place before first use.
- Prior-set experience files flag: per-test pytest timeouts + `workers=1` shard discipline on this box; keep new resolved-path tests modest (8/3 overrides).
