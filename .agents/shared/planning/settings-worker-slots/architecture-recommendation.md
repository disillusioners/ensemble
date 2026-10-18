# Architecture Recommendation: Worker-slot sizing — un-hardcoded config + managed keys + FE + agent settings tools

Date: 2026-10-18
Author: architect via technical-analysis worker (worktree-verified; every anchor spot-checked against `feature/settings-worker-slots` @ d0b475466)
Worktree: `/home/nea/ensemble-src-wt-settings-worker-slots` (branch `feature/settings-worker-slots`, base latest @ d0b475466)
Status: **BINDING** for implementation. Answers commission design questions D1–D8; reuses the db-backed-settings machinery (`plan-overview` at `.agents/shared/planning/db-backed-settings/`) and its binding rulings verbatim — resolver ladder, boot seam, frozen GET contract, and the security addendum are NOT re-derived here. **Amended in place per council REJECTED-as-written review (iteration 002, 2026-10-18): B1 (seal red/green model), B2 (five-pin list), B3 (envelope seam option (b) + F6 import direction), F1–F6 folded; corrected passages are marked "(CORRECTED, iteration 002)".**

Binding invariants inherited from the prior commission (restated as constraints, not reopened): explicit resolver precedence **DB > os.environ > yaml literal > default** (pydantic env tier never relied upon); boot apply between `load_config()` and `InstanceManager` (api.py:413-420); registry-side type validation (`ManagedKeySpec.value_type` + `ge` mirror the pydantic Field exactly — no second truth); registry = additive entries only; `app_settings` is the only store; extend the existing router/service/audit; FE extends the existing registry-driven daemon-settings section; restart-required v1 (no live re-arm).

Scope exclusions (binding): the scheduler lane is OUT (`SCHEDULER_DEFAULT_MAX_CONCURRENT` is a separate lane with its own semantics); NO promote; no new migration; no live-value re-arming machinery.

---

## Ruling 1 (D1) — Config fields + env wiring: additive `LimitsConfig` Fields, zero accessor change

**DECISION: two new Fields on `LimitsConfig` (daemon/config.py:585-593), defaults imported FROM `daemon.constants` so the constant stays the single canonical default-tier truth; two matching `ManagedKeySpec` registry entries; ZERO changes to `load_config`/`raw_yaml_limits`/the capture site.**

### Exact recipe

config.py — inside `LimitsConfig` (after the `llm_concurrency` line at :593):

```python
worker_pool_size: int = Field(
    default=WORKER_POOL_SIZE, ge=1,
    description="Default-pool worker threads (boot-applied; DB/env/yaml resolve at boot via managed keys)",
)
chat_worker_pool_size: int = Field(
    default=CHAT_WORKER_POOL_SIZE, ge=1,
    description="Chat-lane worker threads (boot-applied; DB/env/yaml resolve at boot via managed keys)",
)
```

`from daemon.constants import WORKER_POOL_SIZE, CHAT_WORKER_POOL_SIZE` (config.py already imports constants elsewhere — verify at edit time; add if absent). Env names fall out of `env_prefix="LIMITS_"` (config.py:590): **`LIMITS_WORKER_POOL_SIZE`** and **`LIMITS_CHAT_WORKER_POOL_SIZE`**.

settings_service.py — two registry entries appended to `MANAGED_KEYS` (:129+), each mirroring its Field per the move-together rule (:134-137 "registry default must mirror the pydantic Field"):

```python
"limits.worker_pool_size": ManagedKeySpec(
    key="limits.worker_pool_size",
    config_path="limits.worker_pool_size",
    env_var="LIMITS_WORKER_POOL_SIZE",
    value_type="int",
    default=WORKER_POOL_SIZE,      # mirrors LimitsConfig Field default — chain: constants → Field → registry
    ge=1,
    requires_restart=True,
    sensitive=False,
    description="Default-pool worker threads ... (see full prose contract below)",
),
"limits.chat_worker_pool_size": ManagedKeySpec(  # same shape, CHAT_WORKER_POOL_SIZE / LIMITS_CHAT_WORKER_POOL_SIZE
```

(CORRECTED, iteration 002 — council F1: NO `effective_max` kwarg anywhere in the recipe. Ruling 3 DELETES the dataclass field; copying the original recipe's `effective_max=None` line would be a `TypeError` at import.)

Description prose contract for both entries (replaces the retired `effective_max` — see Ruling 3): state the boot-applied semantics, the ladder, and the **cross-key interaction honestly**: *"Running value is boot-applied; changes apply at next daemon restart. Real LLM parallelism = min(limits.llm_concurrency, this pool + the other pool's boot-applied size)."*

### Why zero accessor change — and why the yaml-shadowing defect cannot repeat

Verified mechanics (all spot-checked in this worktree):

1. The capture site (config.py:5707-5721) copies the **WHOLE `limits:` section verbatim** (`raw_limits_section = processed_config.get("limits")`; `config_dict["limits"] = processed_config["limits"]`). New keys under `limits:` flow through untouched — `raw_yaml_limits()` (config.py:5541-5549) and `_RAW_YAML_LIMITS` need no edits.
2. The pydantic env tier is BYPASSED by construction: yaml reaches `Config(**config_dict)` (config.py:6068) as **init kwargs** (`config_dict["limits"] = ...` at :5721), and in pydantic-settings init kwargs beat env. This is the exact defect that made `LIMITS_LLM_CONCURRENCY` silently dead pre-P1.
3. The settings ladder's env tier is NOT pydantic's. `resolve()` reads `os.environ` **explicitly** (settings_service.py:334-350, tier 2) and the boot apply **mutates the Config field in place** via `_set_config_value` (`setattr` down the dotted `config_path` — settings_service.py:608-613, applied at :716) inside `apply_db_settings_overrides`, which runs between `load_config()` and `InstanceManager` (api.py:411-420). Because `worker_pool_size`/`chat_worker_pool_size` are ordinary `limits.*` config paths, the SAME generic machinery resolves them: a `LIMITS_WORKER_POOL_SIZE=8` env var wins over a yaml literal precisely because the boot apply overwrites whatever the init-kwargs merged. **The shadowing defect structurally cannot repeat — the env tier never routes through pydantic at all.**

Dead-constant note (feeds Ruling 8): `daemon/constants.py:329 LLM_CONCURRENCY=10` is CONFIRMED dead — the only hits are the definition itself, docstrings/prose mentioning the env var name (`schemas.py:1886`, `settings_service.py:18/:132/:151`), and tests pinning the env var string. Zero code importers; absent from the `TestConstantsCompleteness` expected set (tests/unit/test_constants.py:180-250 — grep-verified). Deletion trips nothing except the seal.

## Ruling 2 (D2) — Pool seam: resolve inside `PoolOrchestrator.setup` via a `None` sentinel; config-reader for the chat lane, doctrine preserved

**DECISION: switch BOTH signatures from `num_workers: int = WORKER_POOL_SIZE` to `num_workers: int | None = None` (sentinel = "resolve from config"); resolution happens ONCE, inside `PoolOrchestrator.setup`, reading `self._manager.config.limits.worker_pool_size` / `.chat_worker_pool_size`. Explicit kwargs (test harness) keep winning unchanged.**

### Chosen shape (and why over threading values from api.py)

```python
# pool_orchestrator.py
def setup(self, num_workers: int | None = None) -> None:
    ...
    limits = self._manager.config.limits          # ALREADY boot-applied at this point (Ruling 1)
    resolved_workers = num_workers if num_workers is not None else limits.worker_pool_size
    resolved_chat   = limits.chat_worker_pool_size
    # :483-499 default lane: num_workers=resolved_workers
    # :527-544 chat lane:    num_workers=resolved_chat   (replaces CHAT_WORKER_POOL_SIZE at :529)
    # :566-569 boot line:    f"...workers={resolved_chat}..." (renders the RESOLVED value)
    set_invoke_semaphore_cap(max(1, resolved_workers - 1))   # Ruling 4 — see below
```

`manager.setup_worker_pool` (manager.py:8358-8384) mirrors: default becomes `None`, body forwards unchanged. The production caller (api.py:608 `manager.setup_worker_pool()`) passes no args → resolved path. The ~20 TEST call sites (`grep -n "setup_worker_pool(" tests/` — including chat_source_harness.py:344 `build_live_pool_manager(..., num_workers=WORKER_POOL_SIZE)`) ALL pass explicit values → **byte-identical behavior for every existing test** (the constant travels through as today). (CORRECTED, iteration 002 — council F5: the original "only test caller" claim was false; the load-bearing fact is that every one of the ~20 sites passes explicit values, so behavior is unaffected.)

Why resolve inside the orchestrator rather than at api.py:608:
- **Single resolution point covers BOTH lanes** — the chat lane is constructed inside `setup` (:527-544); a manager/api-level resolution would still have to smuggle a second value down or read config twice.
- The orchestrator already holds `self._manager` (constructed `PoolOrchestrator(self)` at manager.py:1591-1592) — no new wiring, no signature churn at the api.py call site.
- Resolved config is GUARANTEED live on `manager.config` at setup time: `load_config()` → boot apply mutates in place (api.py:415-420) → `InstanceManager(...)` (api.py:427 region) → `setup_worker_pool()` at api.py:608. Ordering is transitive from the existing P1 seam; no new boot-order invariant is created.

### Chat lane + the "no size kwarg" doctrine — reconciled

The doctrine (pool_orchestrator.py:180-186 docstring) says the chat pool takes **no per-call size kwarg** — bumping it meant editing the constant and rebuilding. We do NOT add a chat size kwarg: `setup`'s public signature stays single-knob (`num_workers` = default pool only). The chat lane's size now comes from the **same resolved config object read internally** — a config *reader*, not a call-site *writer*. The doctrine's intent (one call-site signature; no divergent per-lane sizing arguments at call sites) is preserved byte-for-byte; its *mechanism* (edit the constant) is what R1 un-hardcodes. The :180-186 docstring and the constants.py:90-93 comment ("hardcoded, NO ENSEMBLE_* flags; activation = rebuild+restart") are rewritten to state the new discipline: *default tier = the constant; runtime value resolves DB > env > yaml > default at boot; activation = restart* — which is now literally true instead of "rebuild".

Kill-switch interplay (pool_orchestrator.py:189-195): `USE_WORKER_POOL=false` returns early before both the resolution side-effects — unchanged semantics (no pools, no boot line, no cap override; see Ruling 4 fallback).

### worker_pool.py:1252 dormant default (`num_workers: int = 4`)

CONFIRMED dormant: both production construction blocks pass explicit values; the 4≠5 drift trap stays latent. Handling: **change the default to `None` + assert** — `WorkerPool.__init__(..., num_workers: int | None = None)` raising `ValueError("num_workers is required (drift trap: explicit sizing is the contract)")` when None. One-line change in an unsealed file that converts a silent-drift trap into a loud construction failure. (Alternative — leave it — was rejected: this commission exists to kill silent defaults.)

## Ruling 3 (D3) — `effective_max`: REMOVE the field; the prose becomes cross-key truth, not hardcoded arithmetic

**DECISION: REMOVE `ManagedKeySpec.effective_max` (dataclass field settings_service.py:115-117, the import-time computation at :144, and the pin at tests/test_daemon_settings_api.py:455). Do NOT make it dynamic. Reword the registry description + the FE hint to reference the OTHER MANAGED KEYS instead of literal numbers.**

Why removal beats dynamic derivation:
- The GET body never carried `effective_max` (DaemonSettingEntry fields: key/value/source/default/env_value/yaml_value/db_override/requires_restart/sensitive/description — schemas.py:1855-1923, field list grep-verified). Dynamic derivation would require a **schema change to the frozen contract** (new response field) + router-side recomputation from the boot snapshot — real cost, for a field the FE doesn't render.
- Dynamic-per-request prose (`description` templated with live pool numbers) makes the description a second, computed copy of the truth — the exact two-truths drift class this commission exists to kill (the stale FE hint at settings.component.html:343-345 is the live specimen).
- The no-drift mechanism Ruling 5 chose last time ("computed from constants at import time") protected against **constant drift** — but R1 makes constants merely the *default tier*, so the protection target evaporates. Honest endpoint: state the RELATIONSHIP (`min(llm_concurrency, worker + chat)`) instead of any number.

Move-together mechanics (all three artifacts move in ONE commit):
1. Dataclass field `effective_max: int | None = None` — deleted (settings_service.py:115-117); the `limits.llm_concurrency` entry's `effective_max=constants.WORKER_POOL_SIZE + constants.CHAT_WORKER_POOL_SIZE` argument (:144) — deleted.
2. Description prose (settings_service.py:145-153): reworded per the Ruling 1 prose contract — "pools" referenced as managed keys `limits.worker_pool_size` / `limits.chat_worker_pool_size`, no literal "5 + 2 = 7".
3. Pin tests/test_daemon_settings_api.py:455 (`spec.effective_max == 7`) — deleted; the class (test_v1_registry_entry_pins :448-455) gains pins for the two NEW entries instead (env_var, default, ge, requires_restart — same pin style).
- The FE hint (settings.component.html:343-345, exact string `"Real parallelism is also capped by the fixed worker thread pools (5 worker + 2 chat = 7 concurrent graphs — constants, not settable here)."`) is rewritten to the registry-neutral wording: *"Real parallelism is also capped by the worker thread pools — `limits.worker_pool_size` + `limits.chat_worker_pool_size`, themselves managed settings on this page. Effective concurrent graphs = min(llm_concurrency, worker + chat), boot-applied."*
- Frozen GET contract: UNCHANGED — no field added, no field removed from the HTTP body, `value: int` already covers both new keys. Zero FE code beyond the hint string (confirmed: new int + ge=1 + requires_restart keys render registry-driven with zero component logic; html :254-320 @for loop, :261-263 restart badge, :303-311 number input, TS daemonDraftError :1022-1034 integer/min-1 validation — all generic).

## Ruling 4 (D4) — Derived invoke cap: explicit cap override set from the RESOLVED pool size at pool setup

**DECISION: replace the frozen-constant read inside the lazy singleton with a module-global cap that `PoolOrchestrator.setup` sets explicitly, immediately after default-pool construction. Constant remains as the test/no-boot fallback. Deadlock invariant preserved by construction (override = resolved_workers − 1).**

Current defect: `_get_invoke_semaphore` (daemon/utils.py:588-603) reads `WORKER_POOL_SIZE` at FIRST USE, builds the singleton once, never re-reads — LAZY ≠ DYNAMIC (module docstring + utils.py:639 deadlock note). Once pools become configurable, the cap silently diverges from the real pool.

New shape:

```python
# daemon/utils.py
_invoke_semaphore: asyncio.Semaphore | None = None
_invoke_cap_override: int | None = None

def set_invoke_semaphore_cap(cap: int) -> None:
    """Set the invoke cap BEFORE first use (boot seam: PoolOrchestrator.setup).
    Raises if the singleton already exists — a post-boot override would be a lie."""
    global _invoke_cap_override
    if _invoke_semaphore is not None:
        raise RuntimeError("invoke semaphore already created; cap override must precede first use")
    _invoke_cap_override = cap

def _get_invoke_semaphore() -> asyncio.Semaphore:
    global _invoke_semaphore
    if _invoke_semaphore is None:
        from .constants import WORKER_POOL_SIZE
        cap = _invoke_cap_override if _invoke_cap_override is not None else max(1, WORKER_POOL_SIZE - 1)
        _invoke_semaphore = asyncio.Semaphore(cap)
        logger_utils.info(f"invoke_agent_and_wait: concurrency cap = {cap}")
    return _invoke_semaphore
```

Design points:
- **Deadlock invariant (utils.py:595, :639 — cap ≤ real default-pool size − 1):** the override is `max(1, resolved_workers − 1)` computed from the SAME resolved value that sized the pool in the same function — they cannot diverge. Guarding against an override > resolved−1 at the setter is optional hardening; the single call site makes it structural.
- **Lazy-singleton lifecycle / timing safety (commission question):** VERIFIED SAFE — every `invoke_agent_and_wait` caller is an agent-tool body or the MCP kb_server (daemon/tools/image_tools.py:549, chart_tools.py:541, knowledge_tools.py:754, compare_tools.py:1478, daemon/mcp/kb_server.py:266; consumer utils.py:677). All run inside agent turns, strictly AFTER lifespan startup completes (pools are constructed at api.py:608 inside lifespan; uvicorn serves no requests before it finishes). So at first-use time the override is already set. The `RuntimeError` guard in the setter converts any future boot-order regression (override arriving after first use) into a loud failure instead of a silently-wrong cap — the failure mode this commission eliminates, applied to itself.
- Kill-switch: `USE_WORKER_POOL=false` skips setup → no override → constant fallback (cap 4) — exactly today's behavior on a no-pool boot where the guarantee is moot.
- Existing sanity pin unaffected: tests/integration/test_messages_ref_path_worker_pool.py:157,165 asserts cap ≥ 2 (tolerant of defaults ≥ 3); harness callers pass explicit `num_workers` ≥ WORKER_POOL_SIZE, and the override follows the explicit value → still ≥ 4 in every existing test path.
- NEW pin (CORRECTED, iteration 002 — council F4: injection SPECIFIED, and F3: reset fixture): inject via the CONFIG OBJECT — set `manager.config.limits.worker_pool_size = 8` (plain mutable `BaseSettings`, config.py:4070-4073) on the wired harness manager BEFORE `setup()`, then assert `utils_module._get_invoke_semaphore()._value == 7` on a fresh module. NOT env-var monkeypatching: env/DB tier fidelity is the ladder tests' job (tests/test_daemon_settings_api.py:262 harness), and an env var alone can pass VACUOUSLY through pydantic's env tier when no yaml literal exists. Fresh-module discipline: an AUTOUSE reset fixture sets `utils_module._invoke_semaphore = None` + `_invoke_cap_override = None` before AND after each test (mirror the existing fixture at tests/integration/test_messages_ref_path_worker_pool.py:108-115) — `daemon.utils._invoke_semaphore` is NOT reset across tests, so without the fixture the setter's late-set `RuntimeError` fires order-dependently.

## Ruling 5 (D5) — Agent-facing settings tool category: `settings` (3 flat tools), ari + leader, RuntimeError→envelope idiom

**DECISION: new privileged tool category `settings`, module `daemon/tools/settings_tools.py`, factory `create_settings_tools(manager, current_instance_id)`, three flat NOUN_VERB tools: `settings_get`, `settings_set`, `settings_clear`. Allow-list: `ari` + `leader` (meta.json only — no .md edits, per the secrets P1 precedent). Reuses `SettingsService` + `settings:read`/`settings:write` scopes + the audited-write path via the module-global accessors — ZERO parallel settings logic. Unwired-service idiom: RuntimeError → `{"error": "RuntimeError"}` class-name envelope (secrets precedent), NOT the 503 string.**

### Naming + tool set (with justification)

- Category name: **`settings`** — the resource noun already owns the scopes (`settings:read`/`settings:write`, scopes.py:20/:89-93) and the router prefix; one name across scopes/router/category is greppable and prevents a second spelling of the same surface. (Rejected: `daemon-settings` — introduces a third name for the same thing; `settings-*` category with dotted tool names — dotted forms are the convention-rejected shape, Explorer B.)
- Tools (NOUN_VERB flat, `secret_*` / `ens_env_read` precedent):
  - **`settings_get()`** — resolved view of ALL managed keys (or one key via optional arg): per-key `{key, value (boot-applied running truth), source, default, env_value?, yaml_value?, db_override?, requires_restart, sensitive, divergent}`. `divergent = (value != resolved-tier value)` — mirrors the GET endpoint's blind-spot-(b) snapshot semantics (running truth vs live re-resolve) EXACTLY, sourced from the same `get_settings_resolution_snapshot()` the router uses. This field is the honest-restart contract's core: it is what lets the tool say "a change is pending" without lying about liveness.
  - **`settings_set(key, value)`** — writes the DB override via `SettingsService.set_override` (validation-before-write, `SettingsValidationError` surfaced as `"ERROR: <detail>"` in the result string; registry-gate `UnknownManagedKeyError` likewise). Response carries `updated_by` (the calling agent — see actor note), `requires_restart`, and **explicit non-liveness**: `{"written": <value>, "applies_at": "next daemon restart", "running_value_still": <old boot-applied value>, "live": false}`. **The string `"live": false` is mandatory in every successful write result.**
  - **`settings_clear(key)`** — deletes the override row via `clear_override` (idempotent), same honest-shape response. Justification for the third tool: `set`-to-default leaves a db-sourced row (source tier still "db"); only `clear` restores env/yaml/default tier semantics. Without it an agent cannot undo its own write honestly. Marginal cost ~15 lines on an existing service method.
- NO `_auth.py` change: `TOOL_REQUIRED_AGENTS` (_auth.py:35-51) stays untouched — `SettingsService` is not agent-backed (it is wired module-global, not per-agent-team).

### Honest-response contract (binding, verbatim requirement)

Every settings tool result MUST:
1. Report per-key: `value` (boot-applied), `source` (live re-resolved tier), `requires_restart`, and `divergent` — never a bare value.
2. On any write/clear: state `applies_at: "next daemon restart"` and `live: false`; NEVER claim the change is active in the running daemon. If the key requires restart (all three v1 keys do), say so explicitly (`requires_restart: true`).
3. Never claim liveness for a divergent key: when `divergent=true`, the result says the running value remains `<value>` while the `<source>` tier resolves to `<resolved>` — pending restart.

### Reuse seams (NO parallel logic)

- Service (CORRECTED, iteration 002 — council B3, option (b) ADOPTED): `_resolve_service()` reads the router-owned DI global `daemon/routers/settings.py:142 _settings_service` DIRECTLY (module attribute) and raises its OWN `RuntimeError` when None — mirroring secrets_tools.py:261-263. It must NOT call `get_settings_service()` (:145-148), which raises `HTTPException(503)` and would yield the WRONG envelope `{"error":"HTTPException"}` instead of the pinned `{"error":"RuntimeError"}`. Wiring unchanged (api.py:2582-2588 block; reconcile the exact line at edit time). **Import direction (council F6, recorded):** the SNAPSHOT accessor is the SSOT module-global in `daemon/services/settings_service.py:239-247` → tools → SERVICES (clean direction); the SERVICE global exists ONLY in the router module (the prior commission's P1-t6 Shape A deliberately placed the DI there) → the tools→routers import of `_settings_service` is an ACCEPTED COUPLING, rationale: adding a duplicate services-layer global would create a SECOND DI truth for one service, strictly worse than a one-hop import; constructing `SettingsService(engine=manager.engine)` inside the tool was also rejected (bypasses the wired instance — parallel-construction smell). SettingsService intentionally does NOT live on the manager; do not add a manager field.
- Snapshot: `get_settings_resolution_snapshot()` (routers/settings.py:163-165) — the pristine tier record for honest divergence reporting.
- Audit: inherited free — writes flow through the router? **NO — clarification:** the router-level INFO audit lines (settings.py:1011/:1043) decorate the HTTP path only. The tool path writes via `set_override` directly, so the tool factory adds the SAME audit shape itself: one INFO line per tool write — `"[Settings][tool] db override set key=%s value=%r agent=%s instance=%s"` (sensitive redaction: `value` → `<redacted>` when `spec.sensitive`; subject = `agent:<name>/instance:<id>` in the `_actor_subject_from_request` spirit — agent id, NOT HTTP subject, per the secrets `_resolve_actor` Cardinal-#2 discipline at secrets_tools.py:268+). This is not parallel settings logic — it is the same service call plus the tool-lane's own audit attribution, exactly as the secrets category does.

### Unwired-idiom choice: RuntimeError → envelope (justified)

The settings tools return STRUCTURED per-key payloads (dicts rendered as JSON strings), like secrets tools — not single text blobs like job/scheduling tools. The secrets idiom (secrets_tools.py:146-197, :257-268: catch exception → `json.dumps({"error": type(exc).__name__})`) keeps the machine-readable shape on failure and never leaks internals. The job tools' `"503 SERVICE_UNAVAILABLE: ..."` literal is a text-lane convention and would force agents to string-parse an otherwise-JSON surface. Chosen: RuntimeError → class-name envelope. Envelope REACHABILITY (correction, iteration 002 — council B3): the pinned `{"error":"RuntimeError"}` is reachable ONLY via the option-(b) seam above — the router getter raises `HTTPException(503)`, whose class name would render `{"error":"HTTPException"}`; option (c) (accept + re-pin the HTTPException envelope) is REJECTED — it leaks router-layer exception taxonomy into tool contracts.

### `_tool_registry.py` + construction seam touch-points (FOUR name-set/frozenset touch-points + factory wiring — the secrets same-PR rule, _tool_registry.py:213-222)

1. **DYNAMIC_TOOL_NAMES** (annotation at _tool_registry.py:23; the :137-142 block sits INSIDE this frozenset): add `"settings_get"`, `"settings_set"`, `"settings_clear"`. (Label corrected, iteration 002 — council F2: was mislabeled KNOWN_TOOL_NAMES.)
2. **PRIVILEGED_TOOL_CATEGORIES** (:223-294 frozenset): add `"settings"` (eighth entry) with a provenance comment (this commission; why privileged: the tools mutate daemon-global runtime state and read tier values — the empty-allow inherit universe must NOT auto-grant them; explicit `tools.allow: ["settings"]` opt-in is the only path, structurally enforced by `_strip_privileged_category_tools` (instance.py:6202) + the `resolve_tool_filter` empty-allow branch (instance.py:373)).
3. **CATEGORY_MODULES** (:645 dict; `secrets` entry sits at :813-814): `"settings": "daemon.tools.settings_tools"`.
4. **KNOWN_TOOL_NAMES tail / frozen-binary fallback** (frozenset declared at :848; the :1055-1060 block is its tail): add the three names. (Label corrected, iteration 002 — council F2: was mislabeled as a standalone fallback set.) BOTH #1 (DYNAMIC_TOOL_NAMES) and #4 (KNOWN_TOOL_NAMES) must carry the three names or `test_frozen_tool_name_discovery` FAILS.
5. **Construction seam** (daemon/tools/instance.py, after the secrets block at :6128-6130): `from .settings_tools import create_settings_tools` + `tools.extend(create_settings_tools(manager, current_instance_id))` — decorator-only registration is SILENTLY INVISIBLE (instance.py:6091-6095 warning, verified). CATEGORY_DOC in the new module mirrors secrets_tools.py:206-229 (surfaces in help-tool list).
6. Same-commit hygiene (optional, ADOPTED): correct the stale "triple-pin" comment at _tool_registry.py:286-292 — it says three pin sites; five exist (see below).

### SAME-PR FIVE-pin test updates (mandatory per the A14/D18 silent-additions rule; CORRECTED, iteration 002 — council B2: the original list was SHORT BY ONE)

There are FIVE exact-set equality pins over `PRIVILEGED_TOOL_CATEGORIES` — ALL FIVE gain `"settings"` (+ provenance comment) or one of them fails:

- tests/unit/tools/test_upgrade_registration.py (:136-153 privileged-set pin)
- tests/unit/tools/test_attestation_registration.py (:184-199 pin)
- tests/integration/test_maintenancer_spawn_resolves_tools.py (:318-330 `== frozenset({...})` pin; count comment →8)
- tests/integration/test_service_tool_flag_off_byte_identical.py (:802-846 exact-set pin + drift message)
- tests/unit/tools/test_secrets_registration.py (:212 exact-set pin — the OMITTED FIFTH: `assert PRIVILEGED_TOOL_CATEGORIES == frozenset({…7 entries…})` fails the moment the eighth entry lands)

### Agent-file edit surface + SEAL verdict

- **Edit ONLY the two meta.json files:** agents/ari/meta.json (`"allow"` array :11-36 — append `"settings"` after `"secrets"` at :35) and agents/leader/meta.json (`"allow"` :14-15 — append `"settings"`). **NEITHER FILE IS SEALED** (verified against the 35-entry dict at tests/test_chart_image_delivery_e2e.py:1325-1368).
- **NO .md edits** — the empirical secrets P1 precedent edited only the two meta.json files; the prompt-writing guide §1 forbids daemon paths in prompt prose and tools_note.md is optional reference. Skip tools_note.md v1 (the CATEGORY_DOC carries the tool reference in-system). If the dispatcher wants tools_note.md entries anyway, they are unsealed and free.
- **SEAL verdict for D5: no re-pin needed for the tool lane** (meta.json files unsealed). Re-pin applies ONLY via Ruling 8 (constants.py).

## Ruling 6 (D6) — Test-pin/harness update list (exhaustive, each file:line → change)

| # | File:Line | Current | Required change |
|---|-----------|---------|-----------------|
| 1 | tests/unit/test_constants.py:125-127 | `WORKER_POOL_SIZE == 5` | KEEP assertion (constant remains canonical default); update docstring: "default tier; runtime resolves DB > env > yaml > this at boot" |
| 2 | tests/unit/test_constants.py:133-136 | `CHAT_WORKER_POOL_SIZE == 2` + "D7 — hardcoded, no env flag" docstring | KEEP assertion; docstring rewritten same as #1 ("hardcoded, no env flag" is now false prose) |
| 3 | tests/unit/test_constants.py:192-196 | completeness set | NO change (both constants remain defined; LLM_CONCURRENCY absent — grep-verified — so its deletion trips nothing here) |
| 4 | tests/test_daemon_settings_api.py:448-455 | `spec.effective_max == 7` pin class | DELETE the effective_max assertion (Ruling 3); ADD registry pins for `limits.worker_pool_size` + `limits.chat_worker_pool_size` (env_var/default/ge/requires_restart/sensitive — same style) |
| 5 | tests/integration/test_chat_source_kill_switch.py:194-195 | `== 5` / `== 2` | NO change (defaults unchanged) |
| 6 | tests/integration/chat_source_harness.py:344 | `num_workers=WORKER_POOL_SIZE` explicit | NO change required (explicit kwarg keeps winning); OPTIONAL: new param `resolve_from_config: bool = False` for a resolved-path test |
| 7 | tests/integration/chat_source_harness.py:758-759 | `__all__` re-exports | NO change (re-exports, not assertions) |
| 8 | tests/integration/test_chat_source_boot_line.py:74,98-99 | `len(...) == CHAT_WORKER_POOL_SIZE` / `workers={CHAT_WORKER_POOL_SIZE}` substring | GREEN as-is under defaults (resolved == constant in tests); ADD one new test: config-object injection `manager.config.limits.chat_worker_pool_size = 3` pre-setup (injection SPECIFIED, iteration 002 — council F4; env/DB tier fidelity stays with the ladder tests) → assert `workers=3` in the boot line (pins RESOLVED-value rendering — the boot line carries ONLY the chat count) |
| 9 | tests/integration/test_chat_source_pool_wiring.py:121-127 | explicit `setup_worker_pool(num_workers=WORKER_POOL_SIZE)` | NO change (explicit path); ADD resolved-path sibling (config-object injection `manager.config.limits.worker_pool_size = 8` → `len(manager._worker_pool._workers) == 8`; F4 spec) |
| 10 | tests/integration/test_chat_source_shutdown.py:335 | `total == WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE` | NO change under defaults; symbolic — follows automatically |
| 11 | tests/integration/test_chat_source_two_way_isolation.py / test_chat_source_saturation_isolation.py / test_chat_source_late_wire.py | symbolic couplings | NO change — follow automatically (verified: all reference the constants symbolically; RE-CONFIRMED @ base d0b475466, iteration 002 — council O8: zero literal 5/2 pins in any of the three) |
| 12 | tests/integration/test_messages_ref_path_worker_pool.py:157,165 | invoke-cap sanity ≥ 2 | NO change (override follows explicit harness size → 4); see Ruling 4 NEW pin for the resolved-cap test |
| 13 | tests/unit/tools/test_upgrade_registration.py ~:147-153 | privileged frozenset pin | ADD `"settings"` |
| 14 | tests/unit/tools/test_attestation_registration.py ~:193-199 | privileged frozenset pin | ADD `"settings"` |
| 15 | tests/integration/test_maintenancer_spawn_resolves_tools.py ~:323-330 | `== frozenset({...})` + count comment | ADD `"settings"`; comment 7→8 |
| 16 | tests/integration/test_service_tool_flag_off_byte_identical.py ~:800-846 | exact-set pin + drift message | ADD `"settings"`; update drift message |
| 16b | tests/unit/tools/test_secrets_registration.py :212 | FIFTH exact-set pin (council B2 — OMITTED from the original list; `== frozenset({…7…})` fails the moment the 8th entry lands) | ADD `"settings"` |
| 16c | daemon/tools/_tool_registry.py :286-292 | stale "triple-pin" comment (says 3 sites; 5 exist) | correct the count in the same commit (optional, ADOPTED) |
| 16d | tests/integration/test_messages_ref_path_worker_pool.py :108-115 | existing autouse `utils_module._invoke_semaphore = None` fixture | MIRROR it (F3) as an autouse reset (semaphore + `_invoke_cap_override`) in every module gaining an invoke-cap/ resolved-pool test |
| 17 | NEW: tests/test_daemon_settings_api.py (same file) | — | ADD: two-key ladder test (DB override + env for worker_pool_size resolves db > env — reuses the existing ladder test harness at :262) |
| 18 | NEW: tests/.../test_settings_tools.py | — | Tool-lane tests: honest contract (`live: false`, divergent flag), registry-gate rejection, unwired-service envelope, audit line, actor attribution |

Env discipline (load-bearing): `LIMITS_WORKER_POOL_SIZE` / `LIMITS_CHAT_WORKER_POOL_SIZE` must NOT enter conftest (mirrors `LIMITS_LLM_CONCURRENCY` exclusion, tests/test_daemon_settings_api.py:74 / tests/test_settings_resolution.py:28) — tests that need them monkeypatch explicitly.

## Ruling 7 (D7) — NO new migration

**CONFIRMED: zero migration work.** Overrides are rows in the EXISTING `app_settings` table (created by `daemon/migrations/versions/20261018_000001_create_app_settings.sql`, present in this worktree — verified). The table is key-generic (`key TEXT PRIMARY KEY` + JSON value + type + actor stamps); new managed keys are registry entries, invisible to the schema. The boot read resolves whatever valid rows exist; an unknown-key row is ignored by `_read_managed_rows` exactly as today. No `.sql`, no model change, no column-ensure.

## Ruling 8 (D8) — Seal: EDIT `daemon/constants.py` (yes), with the explicit pre-merge re-pin step

**DECISION: YES — edit constants.py in this same PR, for three tight changes: (a) rewrite the :90-93 chat-lane comment block (currently states "hardcoded, NO ENSEMBLE_* flags; activation = rebuild+restart" — becomes FALSE the moment R1 lands; a stale seal-protected comment is worse than none); (b) mirror-note on :86 that WORKER_POOL_SIZE is now the default tier; (c) DELETE the dead `LLM_CONCURRENCY` line :329 (trivial: zero importers, absent from the completeness set — verified).** Deleting the dead constant is exactly the "un-hardcode" hygiene this commission is for, and leaving a dead constant named identically to the managed key `limits.llm_concurrency` invites a future agent to "fix" the semaphore by editing it.

Re-pin procedure (EXPLICIT, ADJUDICATED, PRE-MERGE). **Baseline fact (CORRECTED, iteration 002 — council B1):** the 35-entry `SEALED_SHA_BASELINES` dict is 27 green / 8 stale at base, and `daemon/constants.py` is ALREADY one of the stale — pinned `64c1dbe3…`, actual `7026ed31…`, last-touch c6f14a72c (2026-10-12), AFTER the seal commit 62934ef6a. The tripwire is red on 8 pins TODAY, independent of anything this PR edits; there is NO "goes red when touched" transition:

1. **Attribute the mismatch** (lockstep rule): `git log --follow -p -- daemon/constants.py` → record the last-touch commit(s) + this commission's PR id in the PR description as the re-pin justification.
2. **Update SEALED_SHA_BASELINES ONLY** for the `daemon/constants.py` entry (dict at tests/test_chart_image_delivery_e2e.py:1325-1368, constants.py pinned at :1346) — recompute the sha from the NEW file content. Touch NO other pin. (The 7 OTHER stale pins stay exactly as they are — pre-existing, separately adjudicated, out of scope: install-mermaid-cli.md, ari/workflow.md, approver/rule.md, developer/rule.md, planner/rule.md, reviewer/rule.md, tidier/rule.md.)
3. **Targeted seal test pass**: post-re-pin the run must be red on EXACTLY the 7 OTHER stale pins {install-mermaid-cli.md, ari/workflow.md, approver/rule.md, developer/rule.md, planner/rule.md, reviewer/rule.md, tidier/rule.md} and GREEN on daemon/constants.py — **reds drop 8→7**. Any other state (constants still red, an extra red, a green where red belongs) = stop and re-derive.
4. The three files NOT touched: agents/ari/workflow.md (:1334), agents/ari/rule.md (:1351), agents/leader/rule.md (:1358) — this plan deliberately avoids them (D5 meta.json-only surface), so no additional re-pins.

## R3 (commission) — The interaction call-out (why these keys are the REAL limit)

The user-facing truth this commission must state everywhere it matters (FE hint, registry descriptions, tool docs): `limits.llm_concurrency` is a **semaphore on the shared main asyncio loop** (manager.py:562 constructs it from the boot-applied config value — anchor corrected, iteration 002, was :555), but every graph execution is dispatched from a pool thread that bridges onto that loop — so the real concurrent-graph cap is **`min(llm_concurrency, worker_pool_size + chat_worker_pool_size)`**, all three boot-applied. Raising `llm_concurrency` past the pool total is a no-op; raising the pool sizes past `llm_concurrency` is equally a no-op. Before this commission the pools were constants — the DB-managed knob could be silently capped by an invisible wall; after it, ALL THREE legs of the min() are visible, DB-managed, and honestly reported (divergence flag + restart semantics). The invoke-cap (Ruling 4) is a fourth, derived leg that now follows the resolved pool instead of a frozen constant.

## Files to touch (complete list)

| Path | Change |
|------|--------|
| daemon/config.py | +2 Fields on LimitsConfig (:593+), defaults from constants; +1 import |
| daemon/services/settings_service.py | +2 ManagedKeySpec entries; −effective_max field + :144 computation; reword llm_concurrency description (:145-153) |
| daemon/services/pool_orchestrator.py | setup(): sentinel default + config resolution both lanes (:163, :483-499, :527-544, :566-569); docstring rewrite (:180-186); call `set_invoke_semaphore_cap` |
| daemon/manager.py | setup_worker_pool(): default `None`, docstring touch (:8358-8384) |
| daemon/services/worker_pool.py | __init__ num_workers default → None + loud ValueError (:1252) |
| daemon/utils.py | `_invoke_cap_override` + `set_invoke_semaphore_cap()` + fallback read (:588-603); docstring :639 stays true |
| daemon/constants.py | comment rewrite :86-93; DELETE dead LLM_CONCURRENCY :329 — **SEALED, re-pin per Ruling 8** |
| daemon/tools/settings_tools.py | NEW: factory + 3 tools + CATEGORY_NAME/CATEGORY_DOC + `_resolve_service` + tool-lane audit |
| daemon/tools/_tool_registry.py | 4 touch-points: DYNAMIC_TOOL_NAMES (:23, set :137-142), PRIVILEGED set, CATEGORY_MODULES, KNOWN_TOOL_NAMES tail (:848, :1055-1060) + stale triple-pin comment :286-292 (labels corrected, iteration 002) |
| daemon/tools/instance.py | construction list-extend after secrets block (:6128-6130 region) |
| agents/ari/meta.json | +`"settings"` in tools.allow (:35) — unsealed |
| agents/leader/meta.json | +`"settings"` in tools.allow (:14-15) — unsealed |
| frontend/src/app/pages/settings/settings.component.html | rewrite hint :343-345 (only FE change) |
| tests: table in Ruling 6 | 5 privileged-pin updates (B2) + registry-comment fix + registry-pin update + docstring updates + 3 new test groups |

Explicitly NOT touched: daemon/api.py (seam already correct at :608 + :413-420); daemon/routers/settings.py (generic over the registry); daemon/auth/scopes.py (settings:{read,write} already registered); daemon/tools/_auth.py (no agent-backed category); all sealed .md agent files; FE component TS (validation already generic); any migration file.

## Phases (grouped by shared context; ONE developer, this worktree)

- **Phase A — Config + pool seam (one context: resolution chain).** config.py Fields → settings_service registry entries (incl. effective_max removal + prose) → pool_orchestrator/manager/worker_pool sentinel + resolution + boot line → utils cap override. Gate: unit tests #1-#4, #8-#12 green incl. new resolved-path tests.
- **Phase B — Agent tool category (one context: tool surface).** settings_tools.py → _tool_registry 4 touch-points → instance.py construction → FIVE-pin test updates (B2, incl. test_secrets_registration.py:212) → new tool-lane tests. Gate: maintenancer spawn test + byte-identical test green.
- **Phase C — Allow-list + FE hint (one context: surfaces).** 2 meta.json edits → FE hint rewrite → FE manual sanity (two new keys render with restart badges; edit/save/clear flows). Gate: jest suite + manual GET/PUT cycle.
- **Phase D — Seal re-pin + close-out.** Ruling 8 procedure (constants.py) → targeted seal run → full test pass → commit + merge to latest. **NO promote.**

Sequence note (CORRECTED, iteration 002): `daemon/constants.py` is ALREADY stale at base (one of the 8 red pins) — do the constants edit + re-pin as ONE commit (first or last, consistently) so that pin flips red→green atomically with the behavior change; every gate run must show reds 8→7 on exactly the 7 other stale pins (they are not this commission's failures).

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | Seal re-pin done sloppily (wrong sha, extra file touched) → silent seal integrity loss | Low | High | Ruling 8 procedure is mechanical: one file, one entry, targeted run verifying "red on exactly the 7 OTHER stale pins, green on constants.py (reds 8→7)" |
| 2 | `num_workers=None` sentinel leaks: a caller relying on the old default-kwarg value indirectly (introspection, docs) | Low | Medium | ~20 test call sites + 1 production caller (api.py:608), ALL explicit — re-grep `setup_worker_pool(` / `PoolOrchestrator.setup(` at edit time and confirm no NEW no-kwarg caller (caller-set claim corrected, iteration 002 — council F5) |
| 3 | WorkerPool `None` + ValueError breaks an unlisted direct constructor call | Low | Medium | Grep `WorkerPool(` across daemon/ + tests/ before merging; every hit must pass explicit sizing (both prod sites do today) |
| 4 | Invoke-cap override fires after first use (future code calls invoke during lifespan) | Low | High | Setter raises RuntimeError loudly (Ruling 4); today's caller set is strictly post-lifespan (grep-verified) |
| 5 | Resolved pool size < 3 breaks the invoke-cap ≥2 sanity pin in exotic envs | Low | Low | The cap override follows whatever size resolved; pin asserts ≥2 only in default-sized harness runs; document `LIMITS_WORKER_POOL_SIZE ≥ 2` as the practical floor (ge=1 is the enforced floor) |
| 6 | FE hint rewrite still claims something stale if a future 4th lane appears | Low | Low | New wording references managed keys, not numbers; a 4th lane would be a new managed key + its own prose |
| 7 | Tool-lane audit diverges from router audit shape over time | Low | Low | Same INFO-line format prefix `[Settings]`; tool lane tagged `[Settings][tool]`; one-line note in both docstrings cross-referencing |
| 8 | Chat-lane doctrine dispute ("config reader = kwarg by stealth") | Low | Low | Ruling 2 reconciles explicitly: public signature unchanged, doctrine intent (call-site discipline) preserved; docstring rewritten to the new mechanism |
| 9 | Test shards on the shared box contend while booting resolved-pool managers with larger pools (env override 8+ workers in tests) | Medium | Low | New resolved-path tests use modest overrides (8/3) and the existing shard discipline; no test raises pool counts beyond current prod max |

## Verdict

The commission is a low-risk additive extension of machinery proven last week: the resolver ladder, boot seam, and GET contract take the two new keys with ZERO changes to themselves (whole-section yaml capture + generic registry + in-place boot apply, all verified); the only genuinely new mechanics are (1) the pool-seam sentinel swap — three signatures, one resolution point, harness byte-compatibility verified — (2) the invoke-cap override with a loud late-set guard, and (3) the settings tool category, which is the secrets playbook (4 registry touch-points + construction extend + 4 pin updates + meta.json-only agent surface) with a SettingsService seam instead of a manager field. The two judgment calls that could have seeded future drift are resolved by removal rather than cleverness: `effective_max` dies (the prose references keys, not numbers), and the dormant `WorkerPool` default becomes a loud failure. One sealed file is touched by design (constants.py) and carries an explicit pre-merge re-pin procedure.

**Confidence: High.** Flip assumptions: (i) if any production caller of `setup_worker_pool`/`WorkerPool(...)` exists beyond the two verified sites, the sentinel/None handling must cover it — the grep surface is small and re-checkable at edit time; (ii) if the GET contract ever needs an effective-max-style field, it must go through a conscious schema-change decision, not registry prose; (iii) the freeze on `USE_WORKER_POOL` kill-switch semantics holds — resolution stays strictly inside `setup` after the early return.
