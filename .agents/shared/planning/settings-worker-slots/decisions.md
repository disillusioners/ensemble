# Decisions: Worker-slot sizing — un-hardcoded pool config + managed keys + FE + agent settings tools

Date: 2026-10-18
Author: plan-creation worker (rulings authored by the architect in `architecture-recommendation.md`, status **BINDING**; this file records them + rationale for implementation-time reference)
Worktree: `/home/nea/ensemble-src-wt-settings-worker-slots` (branch `feature/settings-worker-slots`, base latest @ d0b475466)
Status: CLOSED — all rulings D1–D8 are DECIDED; no OPEN items. Implementation re-derives nothing. **Iteration 002 (2026-10-18): council REJECTED-as-written corrections B1/B2/B3 + F1–F6 folded IN PLACE; corrected passages marked "(corrected, iteration 002)"; summary + landing sites in "Council corrections (iteration 002)" at the bottom.**

All file:line anchors spot-checked in this worktree during plan writing. Where verification superseded an approximate anchor from the architecture doc, the verified anchor is recorded here (see Warning W3).

---

## Inherited invariants (from the db-backed-settings commission — constraints, NOT reopened)

1. **Resolver precedence, explicit:** DB > os.environ > yaml literal > default. The pydantic env tier is NEVER relied upon (init-kwargs-beat-env shadowing, config.py:5721→:6068); `resolve()` reads `os.environ` directly (settings_service.py:334-350).
2. **Boot apply ordering:** `load_config()` (api.py:401) → `apply_db_settings_overrides` in-place mutation (api.py:413-420, `_set_config_value` settings_service.py:608-613/:716) → `InstanceManager(...)` → `setup_worker_pool()` (api.py:608). Everything downstream reads the already-resolved Config.
3. **Registry mirrors the pydantic Field:** `ManagedKeySpec.value_type` + `ge` + `default` mirror the Field exactly — no second truth. Chain: `daemon.constants` → Field default → registry default.
4. **`app_settings` is the only store.** No parallel KV, no new table, no migration (Ruling 7).
5. **Reuse scopes/service/audit:** `settings:read`/`settings:write` (scopes.py:20/:91-98), `SettingsService` (wired DI global — reached per the D5-corrected seam, iteration 002: direct global read, NOT the HTTPException-raising getter) + the resolution-snapshot SSOT global (settings_service.py:239-247) — ZERO parallel settings logic.
6. **Restart-required v1:** honest `requires_restart` everywhere; no live re-arm of pools, semaphores, or caps.

---

## Ruling D1 — Config fields + env wiring: additive `LimitsConfig` Fields, zero accessor change. DECIDED.

**Decision:** two new Fields on `LimitsConfig` (`daemon/config.py:585-593`, inserted after the `llm_concurrency` line :593): `worker_pool_size: int = Field(default=WORKER_POOL_SIZE, ge=1, ...)` and `chat_worker_pool_size: int = Field(default=CHAT_WORKER_POOL_SIZE, ge=1, ...)`, defaults imported FROM `daemon.constants` (constants stay the canonical default-tier truth). Two matching `ManagedKeySpec` registry entries (`settings_service.py:128+`), mirroring the Fields per the move-together rule. ZERO changes to `load_config`/`raw_yaml_limits`/capture site.

**Rationale:** the capture site (config.py:5707-5721) copies the whole `limits:` section verbatim → new keys flow untouched. Env names fall out of `env_prefix="LIMITS_"` (config.py:588): `LIMITS_WORKER_POOL_SIZE` / `LIMITS_CHAT_WORKER_POOL_SIZE`. The ladder's env tier is read explicitly in `resolve()` and applied by in-place `_set_config_value` after `load_config` — the shadowing defect structurally cannot repeat (it never routes through pydantic).

**Registry description prose contract (binding, replaces the retired `effective_max`):** state boot-applied semantics, the ladder, and the cross-key interaction honestly: *"Running value is boot-applied; changes apply at next daemon restart. Real LLM parallelism = min(limits.llm_concurrency, this pool + the other pool's boot-applied size)."* Reference managed KEYS, never literal numbers.

## Ruling D2 — Pool seam: `None` sentinel on both signatures; resolution ONCE inside `PoolOrchestrator.setup`. DECIDED.

**Decision:** `num_workers: int | None = None` on BOTH `manager.setup_worker_pool` (manager.py:8358-8384) and `PoolOrchestrator.setup` (pool_orchestrator.py:163). Resolution happens ONCE inside `setup`, reading boot-applied `self._manager.config.limits.worker_pool_size` / `.chat_worker_pool_size`; default lane (:483-499), chat lane (:527-544, replaces the `CHAT_WORKER_POOL_SIZE` literal at :529), and boot line (:566-567) all render RESOLVED values. Explicit kwargs (the harness caller, chat_source_harness.py:344) keep winning → byte-identical harness behavior.

**Rationale:** single resolution point covers BOTH lanes (the chat lane is constructed inside `setup`; a manager/api-level resolution would still need a second value smuggled down). The orchestrator already holds `self._manager` (constructed manager.py:1591-1592) — no new wiring. The chat lane is fed by a config READER, not a call-site kwarg: the "no chat size kwarg" doctrine (:180-186 docstring) keeps its intent (one call-site signature) while its mechanism (edit the constant) is what D1 un-hardcodes. Kill-switch interplay: `USE_WORKER_POOL=false` (:189-195) returns early BEFORE resolution side-effects — unchanged semantics.

**Drift-trap kill:** `WorkerPool.__init__(..., num_workers: int = 4)` (worker_pool.py:1252) is CONFIRMED dormant (both production construction sites pass explicit values) → default becomes `None` + loud `ValueError("num_workers is required (drift trap: explicit sizing is the contract)")`. Silence-to-loudness is the commission's own medicine applied to itself.

## Ruling D3 — `effective_max`: REMOVE entirely; prose becomes cross-key truth. DECIDED.

**Decision:** delete the dataclass field (`settings_service.py:116`), the import-time computation (`:144`, `WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE`), and the pin (`tests/test_daemon_settings_api.py:455`) in ONE commit. Reword the `llm_concurrency` registry description (:145-153) and the FE hint (`settings.component.html:343-345`) to reference managed keys (`limits.worker_pool_size` / `limits.chat_worker_pool_size`), not literal numbers. NOT made dynamic.

**Rationale:** the GET body never carried `effective_max` (`DaemonSettingEntry` fields: key/value/source/default/env_value/yaml_value/db_override/requires_restart/sensitive/description — schemas.py:1831-1923) → dynamic derivation would require a frozen-contract schema change for a field the FE doesn't render. Dynamic-per-request prose = a second computed copy of the truth — the exact drift class this commission kills (the stale literal "5 worker + 2 chat = 7" hint is the live specimen). D1 makes constants merely the default tier, so the old drift-protection target evaporates. Frozen GET HTTP contract: UNCHANGED — `value: int` already covers both new keys; zero FE code beyond the hint string.

## Ruling D4 — Derived invoke cap: explicit boot-set override with loud late-set guard. DECIDED.

**Decision:** module-global `_invoke_cap_override: int | None` + `set_invoke_semaphore_cap(cap)` in `daemon/utils.py` (:588-603 rework); the setter RAISES `RuntimeError("invoke semaphore already created; cap override must precede first use")` if the singleton exists. `PoolOrchestrator.setup` calls `set_invoke_semaphore_cap(max(1, resolved_workers - 1))` right after default-pool construction. `_get_invoke_semaphore` uses the override when set, else the constant fallback (`max(1, WORKER_POOL_SIZE - 1)`), which remains the test/no-boot path.

**Rationale:** the current read is LAZY ≠ DYNAMIC — once pools are configurable, a frozen-constant cap silently diverges (the failure mode this commission eliminates, applied to itself). The override is computed from the SAME resolved value that sized the pool in the same function → the deadlock invariant (cap ≤ default-pool − 1, utils.py:595/:639) holds by construction. Timing VERIFIED SAFE: every `invoke_agent_and_wait` caller is a post-lifespan agent-tool body (image_tools.py:549, chart_tools.py:541, knowledge_tools.py:754, compare_tools.py:1478, kb_server.py:266; consumer utils.py:677). Kill-switch: no setup → no override → constant fallback — exactly today's no-pool boot. Existing cap-≥2 pin (test_messages_ref_path_worker_pool.py:157-165) unaffected; NEW pin: env 8 → `_value == 7` on a fresh module. (Corrected, iteration 002 — council F3/F4: the pin injects via the CONFIG OBJECT — `manager.config.limits.worker_pool_size = 8`, plain mutable BaseSettings — NOT env monkeypatching, which could pass vacuously through pydantic's env tier when no yaml literal exists; and tests MUST use an AUTOUSE reset fixture zeroing `utils_module._invoke_semaphore` + `_invoke_cap_override` before/after each test, mirroring test_messages_ref_path_worker_pool.py:108-115 — `_invoke_semaphore` is NOT reset across tests, so the setter's late-set `RuntimeError` would otherwise fire order-dependently.)

## Ruling D5 — Agent-facing `settings` tool category: 3 flat tools, ari + leader, RuntimeError→envelope. DECIDED.

**Decision:** new privileged category `settings`; module `daemon/tools/settings_tools.py`; factory `create_settings_tools(manager, current_instance_id)`; tools `settings_get` / `settings_set` / `settings_clear` (NOUN_VERB flat, `secret_*` precedent). Allow-list: `agents/ari/meta.json` (`tools.allow`, append `"settings"` after `"secrets"` at :35) + `agents/leader/meta.json` (:14-15) — BOTH UNSEALED; NO `.md` edits. NO `_auth.py` change (`TOOL_REQUIRED_AGENTS` :35-51 untouched — not agent-backed). `_resolve_service()` mirrors secrets_tools.py:261-263; SettingsService intentionally does NOT live on the manager; do NOT add a manager field. **Envelope seam (CORRECTED, iteration 002 — council B3, option (b) ADOPTED):** `_resolve_service()` reads the router-owned DI global `routers/settings.py:142 _settings_service` DIRECTLY and raises its OWN `RuntimeError` when None — it must NOT call `get_settings_service()` (:145-148), which raises `HTTPException(503)` and would yield `{"error":"HTTPException"}`, NOT the pinned `{"error":"RuntimeError"}` (option (c) — accept and re-pin the HTTPException envelope — REJECTED: leaks router-layer exception taxonomy into tool contracts). **Import direction (council F6, binding):** snapshot accessor = the SSOT module-global in `daemon/services/settings_service.py:239-247` → tools → SERVICES (clean); the SERVICE global exists ONLY in the router module (prior commission P1-t6 Shape A placed DI there) → the tools→routers import of `_settings_service` is an ACCEPTED COUPLING — a duplicate services-layer global would create a second DI truth for one service; constructing `SettingsService(engine=manager.engine)` in the tool was also rejected (bypasses the wired instance). Unwired-service failure → `{"error": "RuntimeError"}` class-name envelope (secrets idiom secrets_tools.py:146-197/:257-268), NOT the 503 text literal.

**Tool semantics:** `settings_get` = resolved view of all (or one) managed keys with per-key `{key, value (boot-applied), source, default, env_value?, yaml_value?, db_override?, requires_restart, sensitive, divergent}` where `divergent = (value != resolved-tier value)` — the honest-restart core. `settings_set(key, value)` = `set_override` with validation surfaced as `"ERROR: <detail>"`; response carries `updated_by` (calling agent), `requires_restart`, and MANDATORY `{"applies_at": "next daemon restart", "running_value_still": <old>, "live": false}`. `settings_clear(key)` = `clear_override` (idempotent) — exists because set-to-default leaves a db-tier row; only clear restores env/yaml/default semantics.

**Honest-response contract (binding, verbatim):** (1) every result reports per-key value/source/requires_restart/divergent — never a bare value; (2) every write/clear states `applies_at: "next daemon restart"` and `live: false` — NEVER claim liveness; (3) when `divergent=true`, say the running value remains `<value>` while the `<source>` tier resolves to `<resolved>` — pending restart.

**Audit:** router-level INFO lines (settings.py:1011/:1043) decorate the HTTP path only; the tool factory adds the SAME audit shape itself — one INFO line per tool write, `"[Settings][tool] db override set key=%s value=%r agent=%s instance=%s"` (`value` → `<redacted>` when `spec.sensitive`; subject = agent id per the secrets `_resolve_actor` Cardinal-#2 discipline). Same service call + tool-lane audit attribution = the secrets pattern, NOT parallel settings logic.

**Registry touch-points (FOUR + construction — the same-PR rule; labels CORRECTED, iteration 002 — council F2):** (1) `DYNAMIC_TOOL_NAMES` (annotation at _tool_registry.py:23; the :137-142 block sits INSIDE this frozenset) += the three names — was mislabeled `KNOWN_TOOL_NAMES`; (2) `PRIVILEGED_TOOL_CATEGORIES` (:223-294, `"settings"` becomes the EIGHTH entry at :293+ with a provenance comment — privileged because the tools mutate daemon-global runtime state; empty-allow inherit universe must NOT auto-grant; structural enforcement via `_strip_privileged_category_tools` instance.py:6202 + `resolve_tool_filter` empty-allow branch instance.py:373); (3) `CATEGORY_MODULES` (:645 dict; `secrets` entry sits at :813-814) → `"settings": "daemon.tools.settings_tools"`; (4) `KNOWN_TOOL_NAMES` tail / frozen-binary fallback (frozenset declared at :848; the :1055-1060 block is its tail) += the three names — was mislabeled a standalone fallback set. **BOTH #1 and #4 must carry the three names or `test_frozen_tool_name_discovery` FAILS.** Same-commit hygiene (optional, ADOPTED): correct the stale "triple-pin" comment at :286-292 (5 pin sites exist — see D6 row 16c). Construction seam: extend after the secrets block (instance.py:6128-6130) — decorator-only registration is SILENTLY INVISIBLE (instance.py:6091-6095). `CATEGORY_DOC` mirrors secrets_tools.py:206-229.

**Seal verdict for D5:** no re-pin — both meta.json files are unsealed (verified against the 35-entry dict at tests/test_chart_image_delivery_e2e.py:1325-1368). Re-pin applies ONLY via D8 (constants.py). Skip tools_note.md v1 (CATEGORY_DOC carries the in-system reference; unsealed and free if the dispatcher wants it later).

## Ruling D6 — Test-pin/harness update table (19 rows after iteration 002 — council B2 added rows 16b/16c; gates distributed to owning phases). DECIDED.

| # | File:Line (verified) | Change | Phase |
|---|----------------------|--------|-------|
| 1 | tests/unit/test_constants.py:125-127 | KEEP `WORKER_POOL_SIZE == 5`; docstring → "default tier; runtime resolves DB > env > yaml > this at boot" | 1 |
| 2 | tests/unit/test_constants.py:133-137 | KEEP `CHAT_WORKER_POOL_SIZE == 2`; docstring: "hardcoded, no env flag" is now false prose → same rewrite as #1 | 1 |
| 3 | tests/unit/test_constants.py:192-196 | NO change (constants remain; dead `LLM_CONCURRENCY` absent from the completeness set — grep-verified) | — |
| 4 | tests/test_daemon_settings_api.py:448-455 | DELETE `spec.effective_max == 7`; ADD pins for both new entries (env_var/default/ge/requires_restart/sensitive) | 1 |
| 5 | tests/integration/test_chat_source_kill_switch.py:194-195 | NO change (defaults unchanged) | — |
| 6 | tests/integration/chat_source_harness.py:344 | NO change (explicit kwarg wins); OPTIONAL `resolve_from_config: bool = False` param for a resolved-path test | 1 (optional) |
| 7 | tests/integration/chat_source_harness.py:758-759 | NO change (re-exports) | — |
| 8 | tests/integration/test_chat_source_boot_line.py:74,98-99 | GREEN as-is; ADD test: config-object injection `manager.config.limits.chat_worker_pool_size = 3` pre-setup (F4 spec, iteration 002) → `workers=3` in the boot line (pins RESOLVED-value rendering — the boot line carries ONLY the chat count, O7) | 1 |
| 9 | tests/integration/test_chat_source_pool_wiring.py:121-127 | NO change (explicit path); ADD resolved-path sibling (config-object injection `manager.config.limits.worker_pool_size = 8` → `len(manager._worker_pool._workers) == 8`; F4 spec) | 1 |
| 10 | tests/integration/test_chat_source_shutdown.py:335 | NO change (symbolic) | — |
| 11 | two_way_isolation / saturation_isolation / late_wire | NO change (symbolic couplings; RE-CONFIRMED @ base d0b475466, iteration 002 — council O8: zero literal 5/2 pins in any of the three) | — |
| 12 | tests/integration/test_messages_ref_path_worker_pool.py:157-165 | NO change; see D4 NEW pin (config-object injection 8 → cap 7, fresh module; F4) + MIRROR the :108-115 autouse reset fixture (F3) in every module gaining an invoke-cap/resolved-pool test | 1 |
| 13 | tests/unit/tools/test_upgrade_registration.py:136-153 | frozenset pin += `"settings"` | 2 |
| 14 | tests/unit/tools/test_attestation_registration.py:184-199 | frozenset pin += `"settings"` | 2 |
| 15 | tests/integration/test_maintenancer_spawn_resolves_tools.py:318-330 | exact-set pin += `"settings"`; count comment 7→8 | 2 |
| 16 | tests/integration/test_service_tool_flag_off_byte_identical.py:802-846 | `test_privileged_category_set_is_pinned` exact-set pin += `"settings"` + drift message update | 2 |
| 16b | tests/unit/tools/test_secrets_registration.py:212 | FIFTH exact-set pin (CORRECTED, iteration 002 — council B2: OMITTED from the original list; `== frozenset({…7…})` fails the moment the 8th entry lands) | 2 |
| 16c | daemon/tools/_tool_registry.py:286-292 | stale "triple-pin" comment (says 3 sites; 5 exist) | correct the count in the same commit (optional, ADOPTED) | 2 |
| 17 | NEW (tests/test_daemon_settings_api.py) | two-key ladder test (db > env; env-only) reusing the :262 ladder harness | 1 |
| 18 | NEW tests/…/test_settings_tools.py | honest contract (`live: false`, divergent), registry-gate rejection, unwired envelope, audit line, actor attribution | 2 |

**Env discipline (load-bearing):** neither `LIMITS_WORKER_POOL_SIZE` nor `LIMITS_CHAT_WORKER_POOL_SIZE` enters conftest — tests needing them monkeypatch explicitly (mirrors the `LIMITS_LLM_CONCURRENCY` exclusion, tests/test_daemon_settings_api.py:74 / tests/test_settings_resolution.py:28).

## Ruling D7 — NO new migration. DECIDED.

**Decision:** zero migration work. Overrides are rows in the existing `app_settings` table (`daemon/migrations/versions/20261018_000001_create_app_settings.sql`, present in this worktree). The table is key-generic; new managed keys are registry entries invisible to the schema; an unknown-key row is ignored by `_read_managed_rows` exactly as today. No `.sql`, no model change, no column-ensure.

## Ruling D8 — Seal: EDIT `daemon/constants.py`, re-pin EXPLICITLY in the SAME commit. DECIDED.

**Decision:** edit constants.py in this PR for exactly three changes: (a) rewrite the chat-lane comment block (:92-93 — currently "hardcoded, NO ENSEMBLE_* flags; activation = rebuild+restart", which becomes FALSE the moment D1/D2 land; a stale seal-protected comment is worse than none) to the new discipline: *default tier = the constant; runtime resolves DB > env > yaml > default at boot; activation = restart*; (b) mirror-note on `WORKER_POOL_SIZE` (:86) that it is now the default tier; (c) DELETE the dead `LLM_CONCURRENCY` (:329 — zero importers, absent from the completeness set; a dead constant named identically to managed key `limits.llm_concurrency` invites a future agent to "fix" the semaphore by editing it).

**Re-pin procedure (EXPLICIT, ADJUDICATED, PRE-MERGE). Baseline fact (CORRECTED, iteration 002 — council B1): the 35-entry dict is 27 green / 8 stale at base, and `daemon/constants.py` is ALREADY one of the stale — pinned `64c1dbe3…`, actual `7026ed31…` (sha re-verified in this worktree), last-touch c6f14a72c 2026-10-12, AFTER seal commit 62934ef6a. The tripwire is red on 8 pins TODAY, regardless of this PR; there is NO "goes red when touched" transition:**
1. **Attribute** (lockstep rule): `git log --follow -p -- daemon/constants.py` → record last-touch commit(s) + this PR id in the PR description as re-pin justification.
2. **Update `SEALED_SHA_BASELINES` ONLY** for the `daemon/constants.py` entry (:1346; dict :1325-1368) — recompute the sha from the NEW file content. Touch NO other pin. The 7 OTHER stale pins stay exactly as they are (out of scope: 5 rule.md — approver/developer/planner/reviewer/tidier — plus install-mermaid-cli.md and ari/workflow.md).
3. **Targeted seal run:** post-re-pin, red on EXACTLY the 7 OTHER stale pins {install-mermaid-cli.md, ari/workflow.md, approver/rule.md, developer/rule.md, planner/rule.md, reviewer/rule.md, tidier/rule.md} + GREEN on `daemon/constants.py` — **reds drop 8→7**. Any other state (constants still red, an extra red, a green where red belongs) = stop and re-derive.
4. **NOT touched (so no additional re-pins):** `agents/ari/workflow.md` (:1334), `agents/ari/rule.md` (:1351), `agents/leader/rule.md` (:1358) — the D5 surface is meta.json-only.

**Commit discipline:** constants edit + re-pin = ONE consistent commit inside Phase 1 (see plan-overview commit-discipline invariant 1). Never leave the tripwire red across a phase boundary or a push.

---

## Binding warnings (dispatch-mandated, encoded above)

- **W1 — seal tripwire:** one-commit rule (D8 + plan-overview invariant 1).
- **W2 — conftest env discipline:** D6 env-discipline note.
- **W3 — line-number correction:** the privileged-frozenset exact-set pin lives in `tests/integration/test_service_tool_flag_off_byte_identical.py:802-846`, NOT `test_upgrade_registration.py`; BOTH files are in the update list (D6 rows 13-16b carry the full verified anchor set).

## Council corrections (iteration 002, 2026-10-18 — REJECTED-as-written review folded; landing sites)

- **B1 — seal red/green model** (arch §Ruling 8; decisions D8; plan-overview criterion 8 + invariant 1 + risk 1; phase1 t8/gate; phase4 t3): constants.py ALREADY stale at base → post-re-pin reds 8→7 on the 7 OTHER stale pins, GREEN on constants.py; "8 rule.md pins" phrasing fixed (actual: 5 rule.md + install-mermaid-cli.md + ari/workflow.md); false "goes-red-when-touched" premise removed.
- **B2 — five-pin list** (arch §Ruling 5 + §Ruling 6 rows 13-16b; decisions D5/D6; plan-overview invariant 3/4 + criterion 7 + files-to-touch; phase2 objective/task 5/gate/exit): FIVE exact-set `PRIVILEGED_TOOL_CATEGORIES` pins — added `tests/unit/tools/test_secrets_registration.py:212`; "5/5 green" everywhere; + `_tool_registry.py:286-292` stale "triple-pin" comment fix (adopted optional).
- **B3 — envelope seam, option (b) ADOPTED** (arch §Ruling 5; decisions D5; phase2 task 1/acceptance): read router global `routers/settings.py:142 _settings_service` directly, raise OWN `RuntimeError` → pinned `{"error":"RuntimeError"}`; `get_settings_service()` (HTTPException-503) NOT called; option (c) rejected. **F6 import direction RECORDED in D5:** snapshot = tools → services (SSOT global, settings_service.py:239-247); service global = tools → routers (ACCEPTED COUPLING — router-owned DI by prior-commission design; a duplicate services-layer global = second DI truth; `SettingsService(engine=manager.engine)` construction in-tool rejected).
- **F1 — `effective_max=None` kwarg deleted** from the Ruling-1 recipe (arch) + phase1 task 2 — the field is DELETED by D3; the kwarg would `TypeError`.
- **F2 — registry touch-point labels swapped** (arch §Ruling 5 + files table; decisions D5; phase2 task 3; plan-overview files-to-touch): #1 = `DYNAMIC_TOOL_NAMES` (:23, set :137-142); #4 = `KNOWN_TOOL_NAMES` tail (:848, :1055-1060); BOTH sets need the 3 names or `test_frozen_tool_name_discovery` FAILS.
- **F3 — autouse reset fixture** (arch §Ruling 4; decisions D4/D6 row 12; plan-overview criterion 4; phase1 test section): zero `_invoke_semaphore` + `_invoke_cap_override` before/after each test (mirror test_messages_ref_path_worker_pool.py:108-115) — otherwise the setter's `RuntimeError` is order-dependent.
- **F4 — resolved-path injection SPECIFIED = config-object** (`manager.config.limits.worker_pool_size = 8` / `.chat_worker_pool_size = 3`, plain mutable BaseSettings) (arch §Ruling 4 + D6 rows 8/9; decisions D4/D6; plan-overview criterion 3; phase1): env/DB tier fidelity stays with the ladder tests; env-var injection could pass vacuously via pydantic's env tier.
- **F5 — caller-set claim corrected:** ~20 test call sites (NOT 2), ALL explicit → behavior unaffected (arch §Ruling 2 + risk 2; plan-overview risk 2; phase1 task 4/risks).
- **O1-O8 optionals:** all landed in this pass (see per-file edits); O8 = symbolic re-confirmation grep run @ base (zero literal 5/2 pins).

## Scope exclusions (binding, restated)

Scheduler lane OUT (`SCHEDULER_DEFAULT_MAX_CONCURRENT` is a separate lane); NO promote; NO new migration; NO live re-arm; FE code beyond the hint string; `_auth.py`; agent `.md` files; the 7 OTHER pre-existing stale seal pins (5 rule.md + install-mermaid-cli.md + ari/workflow.md — constants.py is the 8th and IS re-pinned by this commission).
