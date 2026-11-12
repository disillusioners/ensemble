# Plan: Component B — Spawn-Boundary Env Scrub + Rider (d) + DB-Poison Guard

Date: 2026-11-12
Author: planner[v2] via plan-creation worker (planning worker B)
Status: Draft — Ready for Review
Worktree: `/home/nea/ensemble-src-wt-rogue-containment` (branch `feature/rogue-containment`, tip `cace21473`)
Governing analysis: `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2, §5.3 Lane 3 + rider (d) + §6 lane 3 + §7.3 + §9 decision 2 + holes H3/H4). Where the analysis is silent (the DB-poison half), this plan anchors on the blueprint facts supplied in the commission and records the divergence in §13 (Contradictions).

---

## 1. Objective

Close the **delivery channel** for the rogue-port incident (wi-2454ede3) by stripping the bind-vars, the DB-poison family, and the live-attestation flag from every environment the daemon hands to children; fence the metrics port so no tier can split it from the main port (rider (d)); and add a boot-time guard so an ambient/env-less boot can never silently resolve the live PostgreSQL database. Every containment lands in the **daemon binary** — zero changes to `launcher.sh`, the unit template, or the live `.env` (binding principle, analysis §6). Design only: NO code changes, NO test runs, NO repo writes except this plan file.

---

## 2. Scope

### In Scope

- **(e′) spawn-boundary env scrub** at the two named seams:
  `daemon/tools/bash.py:891-933` and `daemon/tools/service_spawner.py:173-181`, via one shared policy module.
- **Rider (d) metrics-port fence** — two shapes (derive vs env-wins), leader decision §9.2.
- **DB-poison half (commission root)**: (a) DB-family scrub at the same two seams; (b) boot-time DB-resolution guard at `daemon/ensemble_config.py` `load_or_create` call sites `daemon/__main__.py:211` and `daemon/api.py:727`.
- **Sanctioned-boot lane census** (7 lanes) + the silent-reliance flag.
- **F7 rules** for any harness/capture the plan introduces.
- **Mutation-discipline pin skeleton** (3 pins + RED-leg safety).
- Task breakdown, risks, leader decision points, contradictions, migration expectation (zero tables).

### Out of Scope

- **Component A** (port-ownership contract, analysis Lane 2 (b)+(c)+(f)) — sibling worker. Cross-reference by filename only.
- **Pins-integration / sequencing / overview** — later worker.
- **Lane 1** (in-flight bind-retry release) — ship as-is, unbundled (analysis §5.1).
- **Lane 4** (sliding-window burst fix) — deferred, trigger-gated (analysis §5.4).
- **Lane 5** (teardown-budget reconciliation) — durability lane, later.
- **Launcher.sh / unit template / live .env edits** — binding principle.
- **`upgrade_journal.executor_env`** — the third env-assembling seam; already allowlist-scrubbed by design; MUST remain untouched (load-bearing for promote legs).

---

## 3. Governing Analysis + Anchor Verification

The analysis §5.3 Lane 3 + rider (d) + §6 lane row 3 + §7.3 + §9 decision 2 + holes H3/H4 govern. Where it is silent (the DB-poison half), this plan anchors on the supplied blueprint facts and flags the divergence in §13.

Key verified anchors (cace21473):

- Seam 1 `daemon/tools/bash.py:891-933`: env overlay ONLY when `env` passed (`:892-894` overlay-wins-over-os.environ); no overlay ⇒ child inherits full `os.environ` (inherit-env stamp `:905`, fallback copy `:913-914`); exec `:916-923` / shell `:927-933` pass `env=assembled_env`.
- Seam 2 `daemon/tools/service_spawner.py:173-181`: `subprocess.Popen(...)` with NO `env=` kwarg (`close_fds=True :180`) — full inherit; header :4-5 declares it the ONLY file in the service track touching Popen.
- House precedent `daemon/services/vscode_server_manager.py:85-94` `_CODESERVER_STRIP_ENV` (includes `"PORT"`; comment "PORT causes EADDRINUSE by overriding --bind-addr's port"), applied `:310`, `--bind-addr 127.0.0.1:0 :268`.
- Deliberate setter to preserve: `daemon/manager.py:6410-6411` promote/restart executor `extra_env["PORT"] = str(spec["port"])` (argv assembly restart `:6419` / promote `:6439`).
- Config: `substitute_env_vars` :66-78; `DaemonConfig :564` (`env_prefix="DAEMON_" :567`, `port :570`); `bind_retry_max_wait_seconds :591-616`; `LimitsConfig :619` (`env_prefix="LIMITS_" :622`); `metrics_listener_port :821-828` (default 9798, env `LIMITS_METRICS_LISTENER_PORT`); env-wins precedent `MemoirConfig._env_tier_wins :4778`; `load_config :8365` (path `:8385` ENSEMBLE_CONFIG else ./config.yaml; yaml `:8398`; substitute `:8406`; init-kwargs daemon `:8521`; `Config(**config_dict) :8908`).
- `config.yaml:112-113` `host: ${HOST:-0.0.0.0}` / `port: ${PORT:-8088}`; `config.yaml:227-228` documents pydantic args/init_kwargs > env > defaults.
- Metrics port at tip: bind `daemon/api.py:2694-2696`; DB boot-apply `api.py:746-753` runs BEFORE the bind and mutates `config.limits` IN PLACE (`settings_service.py:1268`; precedence ladder `settings_service.py:11` "DB override > os.environ[env_var] > yaml literal > registry default"); live `config.yaml` `limits:` block OMITS the key ⇒ env lever works today, subordinate to any DB managed row.
- DB resolution `daemon/ensemble_config.py` (135 lines): `load_or_create :55`, path `data_dir/"ensemble.json" :69`. Tiers: (1) json exists ⇒ its "database" pin wins outright :71-76 (env NOT consulted; parse-error ⇒ defaults :77-79); (2) json absent + ambient `POSTGRES_HOST` AND `POSTGRES_DB` ⇒ database="postgres" :82-89 (THE ambient-poison tier); (3) json absent but sqlite DBs exist ⇒ sqlite :83-86; (4) else sqlite :90-92; (5) auto-created config persisted :95 (atomic write :98-112) ⇒ after first boot tier 1 governs forever; (6) `get_postgres_url :114-130` — per-key env override OVER json values :124-128 (`POSTGRES_HOST/PORT/DB/USER/PASSWORD`); (7) `postgres_env_available :133-135`.
- Call sites = guard hooks: `daemon/__main__.py:211` (inside `_boot_db_preflight`, docstring :180-202, exit-75/78 refusal precedent :188-196) and `daemon/api.py:727` (lifespan, before `load_config()` :735 and boot-apply :749). data_dir precedence `ENSEMBLE_DATA_DIR > DATA_DIR > ./data` (`api.py:722-726`, `__main__.py:206-210`).
- Incident mechanism (analysis §1.1, verified): live daemon env carries `PORT=9797`/`HOST=0.0.0.0` (launcher `load_env_file :201-248` exports every .env key); every daemon-spawned child inherits it via the two seams; a hand-written launcher setting only `DAEMON_PORT=8099` (INERT) and omitting `PORT` ⇒ rogue bound `0.0.0.0:9797` for 2h38m AND (env-less boot + data/ensemble.json postgres pin) wrote to the LIVE DB.
- EXECUTOR seam (verified at tip): `upgrade_journal.py:2944` `EXECUTOR_ENV_ALLOWLIST` = `PATH, HOME, INSTALL_DIR, PORT, POSTGRES_DB, TMPDIR, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, DBUS_SYSTEM_BUS_ADDRESS, ENSEMBLE_PROMOTE_WATCHDOG` + `EXECUTOR_ENV_PREFIXES = ("PG",)`. `ENSEMBLE_UPGRADE_LIVE` is NEVER in the allowlist; verified-arm extras forward it via the explicit-extra merge (`manager.py:6385-6450`). Promote/restart executor sets `extra_env["PORT"]` at `:6410-6411` and rides `extra_env` (merged LAST in `executor_env`) ⇒ deliberate-settable. Upgrade scripts read `ENSEMBLE_UPGRADE_LIVE` only via `scripts/upgrade/lib.sh:1434` `require_live_guard` (TARGET=live ⇒ exit 78 if absent); NO daemon python file reads it (only comments in `upgrade_tools.py:3282`).
- Forbidden DB names: `tests/postgres/conftest.py:136-142` `FORBIDDEN_DB_NAMES = {ensemble_prod, ensemble_dev, ensemble_prod_test}`.
- Managed-key registry: `app_settings` table (`daemon/models/app_settings.py`, migration `20261018_000002_create_app_settings.sql`); `limits.metrics_listener_port` IS a managed key (`settings_service.py:524-544`) with precedence DB > env > yaml > default, `requires_restart=True`.

---

## 4. Design — (e′) Spawn-Boundary Env Scrub

### 4.1 Mechanism

**House precedent** (`vscode_server_manager.py:85-94`): strip by name from the inherit base, explicit overlay wins. Apply the SAME mechanism at the two agent-spawn seams, for the expanded strip set.

**Shared policy module** (new, single home for the policy + pin target):
`daemon/services/spawn_env_policy.py` (or `daemon/tools/spawn_env_policy.py` — choose `daemon/services/` so both `daemon/tools/bash.py` and `daemon/tools/service_spawner.py` import a non-tool-layer constant; either is acceptable, pin the module path in the pin leg). Contents:

```python
# single source of truth for the scrub policy
SPAWN_BOUNDARY_STRIP_ENV: frozenset[str] = frozenset({
    # bind family (analysis §5.3)
    "PORT", "DAEMON_PORT", "HOST",
    "LIMITS_METRICS_LISTENER_PORT",
    # DB-poison family (commission root; exact names from
    # ensemble_config.get_postgres_url :124-128)
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
    "POSTGRES_USER", "POSTGRES_PASSWORD",
    # live-attestation flag (adjudicated §4.3)
    "ENSEMBLE_UPGRADE_LIVE",
})

def scrub_spawn_env(base: dict[str, str]) -> dict[str, str]:
    """Return a copy of base with every name in
    SPAWN_BOUNDARY_STRIP_ENV removed. Does NOT mutate input."""
    return {k: v for k, v in base.items()
            if k not in SPAWN_BOUNDARY_STRIP_ENV}

def apply_spawn_boundary(
    os_env: dict[str, str],
    overlay: dict[str, str] | None = None,
    deliberate: dict[str, str] | None = None,
) -> dict[str, str]:
    """Strip the inherit base, then re-add explicit overlay and
    deliberate setters (last wins). Explicit overlay + deliberate
    setters are the sanctioned re-add channels."""
    out = scrub_spawn_env(dict(os_env))
    if overlay:
        out.update(overlay)          # explicit-setter wins
    if deliberate:
        out.update(deliberate)       # deliberate setter wins last
    return out
```

**Seam 1 — `daemon/tools/bash.py:891-933`.** Today `assembled_env` is `os.environ.copy()` only when an overlay is passed; otherwise `None` ⇒ full inherit. Change:

- ALWAYS materialise `assembled_env = apply_spawn_boundary(os.environ, env)` (strip + overlay). This removes the `None`-inherit path that carried `PORT`/`HOST`/`POSTGRES_*`/`ENSEMBLE_UPGRADE_LIVE`. The inherit-env stamp path (`:905`) and fallback copy (`:913-914`) collapse into the single materialised copy — semantics-preserving for all non-stripped vars.
- Secret-marker resolution (`_resolve_secret_markers`) and `_kms_resolve_env` still run on the assembled dict; ordering preserved (strip FIRST, then secret resolution, then stamp).
- The bash reaper tracking stamp (`_BASH_TRACKING_ENV_VAR`) is applied AFTER the strip (it is a daemon-internal var, not in the strip set).
- Explicit overlay wins: an agent passing `env={"PORT":"12345"}` re-adds `PORT` (the sanctioned setter channel, matching the analysis's deliberate-PORT preservation).

**Seam 2 — `daemon/tools/service_spawner.py:173-181`.** Today `subprocess.Popen(...)` has NO `env=` kwarg. Change:

- Add `env=apply_spawn_boundary(os.environ, None, extra_env)` to the `Popen` call.
- Add a new parameter to the spawn function: `extra_env: dict[str, str] | None = None` (deliberate-setter pass-through, mirroring `manager.py:6410-6411`). Callers in `daemon/services/service_tool_manager.py` pass `extra_env` when a deliberate bind is intended (today no caller does; the seam gains the capability so future deliberate binders work — same shape as the promote executor).
- `close_fds=True` and `start_new_session=True` remain.

**Seams NOT touched (and why):**
- `upgrade_journal.executor_env` (`upgrade_journal.py:2934-2966`): already allowlist-scrubbed (allowlist + `PG*` prefix + explicit extras merged LAST). It is the only seam that legitimately forwards `PORT` (deliberate) and `POSTGRES_DB`/`PG*` to upgrade scripts (promote legs need DB access). Stripping there would break promote/restart. The `ENSEMBLE_UPGRADE_LIVE` strip already holds there (never in allowlist; only verified-arm extras forward it). Document as an explicit seam-census line so the pin and future readers know it was considered and intentionally excluded.

### 4.2 Sanctioned-Boot Lane Census (trip risks)

Your scrub changes EVERY daemon child's env — walk each lane. These are the sanctioned boot lanes; the plan instructs the developer to add the DB fence var (§6.2) where each needs tier-2 resolution:

| # | Lane | Why it survives / what changes | File anchor |
|---|---|---|---|
| 1 | Promote/restart executor | Seam untouched (executor_env allowlist); `extra_env["PORT"]` deliberate-setter rides merged-last ⇒ stays deliberate-settable. | `manager.py:6385-6450`, `upgrade_journal.py:2944-2966` |
| 2 | dev.sh children | dev.sh exports `PORT=8079 HOST=...` at `:141-142` for the daemon itself; daemon children lose ambient PORT/HOST ⇒ agent scripts reading `$PORT` fail LOUDLY (H4 desired direction). dev worktree `.env` fence (`:79-117` ENSEMBLE_SELF_ENV=dev). | `dev.sh:79-117,141-142` |
| 3 | Pytest transient-unit boots | `wedge_smoke.scratch_env :552-586` sets `DAEMON_PORT` (INERT under shipped config.yaml) and NO `LIMITS_METRICS_LISTENER_PORT` ⇒ 9798 default collision today (anomaly 07e8410c). After rider (d) derive ⇒ scratch main+1 (FIX — pin ii). scratch_env sets POSTGRES_* explicitly ⇒ needs `ENSEMBLE_DB_AMBIENT_ALLOW=1` for the child daemon's tier-2 (§6.2). | `tests/support/wedge_smoke.py:552-586` |
| 4 | Tester scratch mirror `boot_v2.sh` | Per-var ambient scrub `:22-26` + scrub-then-re-read PGPORT `:27-28` + FAKE_HOME `:30-34` + scratch `ENSEMBLE_CONFIG :36-38` + explicit `PORT`/`DAEMON_PORT`/`HOST :40` + scratch PG `:42-46` + sweep pin `:48`. Everything explicit ⇒ unaffected by daemon-side scrub; mirror scripts need the DB fence var (tier-2). | `.agents/tester/RESULTS/assets/2026-11-11-skill-capture-admission-proof/scripts/boot_v2.sh` |
| 5 | `scripts/test_builtin_mcp_servers.sh:89-116` | Sources repo `.env` then sets `PORT=18088` explicitly ⇒ survives explicit-set. Note: the `.env`-sourcing is itself an env-poison surface (carryover). | `scripts/test_builtin_mcp_servers.sh:89-116` |
| 6 | Shell packs sourcing launcher.sh | `scripts/run_tests_scrubbed.sh:25-52` enumerated unset + `:53-62` unset PORT + `:64-70` zero-survivor verify gate ⇒ the daemon-side scrub codifies this class. | `scripts/run_tests_scrubbed.sh:25-70` |
| 7 | Soak/pack fences | `tests/packs/dbpool_gate_ladder.sh:103-105` per-var fence incl. `DAEMON_PORT`; `tests/packs/soak_gate_mcp_warmup_acceptance.sh:388-389`. Consistent direction. | `tests/packs/dbpool_gate_ladder.sh:103-105`, `tests/packs/soak_gate_mcp_warmup_acceptance.sh:388-389` |

**Silent-reliance flag (H4):** any agent bash snippet that read `$PORT`/`$HOST`/`$POSTGRES_*` from ambient now receives an empty value ⇒ loud failure. That is the desired direction; agent-side scripts must switch to explicit values or read the daemon's actual bind from its config. Flag as a risk (R4).

### 4.3 ENSEMBLE_UPGRADE_LIVE Adjudication

- **Readers at tip**: `scripts/upgrade/lib.sh:1434` `require_live_guard` (TARGET=live ⇒ exit 78 if `ENSEMBLE_UPGRADE_LIVE != 1`); `promote.sh:43,136`; `status.sh:19`. NO daemon python file reads it (`upgrade_tools.py:3282` is a comment only).
- **Sanctioned path**: never through bash.py/service_spawner; only via `upgrade_journal.executor_env` (allowlist already excludes it; verified-arm extras forward it explicitly — `manager.py:6385-6450`, `_verified_arm_extras`). F2 fence (f2-not-verified) sits in the scripts themselves.
- **Decision**: **strip `ENSEMBLE_UPGRADE_LIVE` from the inherit base at both seams.** An ambient `ENSEMBLE_UPGRADE_LIVE=1` in a daemon child can only be poison (it would make an upgrade script the child runs treat itself as live-sanctioned, though the F2 gate still refuses without `--f2-verified-closed`).
- **Overlay residual**: an agent passing `env={"ENSEMBLE_UPGRADE_LIVE":"1"}` re-adds it (explicit overlay wins). F2-script-side fences bound the blast radius. Flag as a leader decision point (§12 item 6): strip-even-from-overlay vs overlay-wins. Default recommendation: overlay-wins (consistency with the deliberate-setter channel), with the F2 gate as the real fence.

---

## 5. Rider (d) — Metrics-Port Fence

### 5.1 The Problem (receipts)

The metrics port (9798) is on a different config class (`LimitsConfig`, `env_prefix="LIMITS_"`), is NOT moved by `PORT`, and is unfenced: the live `config.yaml` `limits:` block omits `metrics_listener_port`, so the 9798 default always applies, and the DB tier (`apply_db_settings_overrides`, `settings_service.py:1268`) can move it after the fact (consequence 2). Anomaly 07e8410c is structural: a perfectly fenced daemon still collides on 9798.

### 5.2 Shape (i) — DERIVE

**Mechanism**: at `daemon/api.py:2694-2696`, replace `port=int(getattr(config.limits,"metrics_listener_port",9798))` with a derived value: `metrics = os.environ.get("LIMITS_METRICS_LISTENER_PORT")` if explicitly set, else `config.daemon.port + 1`. The derive reads the FINAL `config.daemon.port` (already resolved from yaml interpolation + init kwargs; the DB tier does not manage `daemon.port`).

- Resolves AFTER the DB boot-apply (the bind site `:2694` is inside the same lifespan, after `:749`) — but by construction the DB tier's metrics value is **ignored**, so no tier can split them.
- Live: 9797 → 9798 (unchanged). Scratch/wedge: main+1 instead of 9798 (fixes anomaly 07e8410c — pin ii).
- Consequence: the managed-key entry `limits.metrics_listener_port` becomes inert-by-construction (DB override, yaml literal, env default all ignored unless the env escape is taken). The registry entry must be RETIRED (settings UI offering a dead knob) and any stored override row left orphaned-but-ignored (no new table; a cleanup task). `requires_restart=True` semantics gone for this key.

### 5.3 Shape (ii) — ENV-WINS VALIDATOR

**Mechanism**: a `model_validator(mode="before")` on `LimitsConfig` (precedent `MemoirConfig._env_tier_wins`, `config.py:4778`) reading `LIMITS_METRICS_LISTENER_PORT`: if present and non-empty, force `metrics_listener_port` to the env value, overriding yaml literals/init kwargs. Keeps the env lever working regardless of yaml.

- Does NOT fix anomaly 07e8410c (lanes not setting the env still default 9798 and collide).
- DB tier still outranks env (apply runs after model construction and setattr's config.limits) ⇒ the env lever stays subordinate to the DB tier.
- Weaker shape; preserved as the escape-hatch mechanism.

### 5.4 Recommendation (leader decision §9.2)

**Derive primary + env-wins escape hatch**: derived default = resolved main+1, with `LIMITS_METRICS_LISTENER_PORT` honored ONLY when explicitly set in `os.environ` (the sanctioned operator override). Retire the DB managed-key entry. This satisfies "a fence that no tier can split" while keeping a deliberate, documented escape hatch. Present both shapes with receipts in the plan; §9.2 is the leader decision point (analysis leans derive-or-env-wins, never a YAML literal — H3).

### 5.5 H3 Operator Step (read-only, never a test)

Plan as an operator SELECT, run against the live DB before relying on the env lever:

```sql
SELECT key, value_json, updated_by, updated_at
FROM app_settings
WHERE key = 'limits.metrics_listener_port';
```

- If a row exists ⇒ the env lever is already subordinate to the DB tier for that key; derive (shape i) removes that subordination entirely (the row becomes inert). If no row exists ⇒ the env lever works today and derive is a no-op improvement.
- Read-only; no mutation; no test.

---
### 5.6 Rollback-safe adjudication shape (rider (d))

Rider (d) retires the `limits.metrics_listener_port` managed key (registry
entry removed) and derives the metrics port from the resolved main port + 1,
with `LIMITS_METRICS_LISTENER_PORT` as the env escape hatch. No schema change
and no new table; the only DB touch is an optional, idempotent DELETE of an
orphaned `app_settings` row (dev/fenced DBs only, never live). Adjudication:
`rollback_safe=true` with `source=explicit (fix-loss-only)` — consistent with
v0.29.19's `rollback_safe=true source=explicit (fix-loss-only)`. If a schema
need surfaces, STOP and flag (changes rollback_safe and ceremony scope).

---
## 6. DB-Poison Half (commission root; analysis-silent — flag it)

The analysis's Lane 3 strip list covers only the four bind vars. The commission's B root — `data/ensemble.json` DB pin — is analysis-silent. This plan's DB half EXTENDS the (e′) MECHANISM (same seams, same explicit-setter allowlist); it does NOT carry analysis authority. Recorded as a contradiction in §13.

### 6.1 (a) Spawn-boundary DB-family scrub

Already in the strip set (§4.1): `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — the EXACT names from `ensemble_config.get_postgres_url :124-128`. After this, a daemon-spawned child carries NO ambient `POSTGRES_*` ⇒ its `load_or_create` tier 2 can never auto-detect postgres from inherited env ⇒ the rogue's env-inherit channel dies. The executor seam (`upgrade_journal.executor_env`) is NOT touched — promote legs legitimately need `POSTGRES_DB`/`PG*`.

### 6.2 (b) Boot-time DB-resolution guard

**Predicate (fail-closed)**: a new shared function `assert_db_resolution_sanctioned(data_dir)` in `daemon/ensemble_config.py`, called at BOTH `load_or_create` call sites BEFORE `EnsembleConfig.load_or_create(data_dir)`:

```
fire (sys.exit(78) + marker line) iff:
  - (data_dir / "ensemble.json") does NOT exist   [tier 2 is the only path that can fire]
  - AND os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB") are both set
  - AND os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW") != "1"
```

Tier 1 (json exists) ⇒ guard does NOT fire (live boots, promote legs, staged boots all have their json pin) ⇒ sanctioned. Tier 2 + ambient POSTGRES_* without the fence var ⇒ REFUSE before any TCP connection (assert zero connections — see pin iii). Tier 3/4 (sqlite) ⇒ guard does not fire (sqlite default is safe — writes to local scratch, not live).

**Fence env var**: `ENSEMBLE_DB_AMBIENT_ALLOW=1` — attests "ambient POSTGRES_* may drive DB resolution". Where it lives:
- `dev.sh` (dev lane; first-boot postgres on dev) — add to the dev `.env`/dev.sh env block.
- `wedge_smoke.scratch_env` — add to the env dict (§4.2 lane 3).
- Mirror harness scripts (new, modeled on `boot_v2.sh`) — explicit set in the launch block.
- Operator fresh-install first boot with postgres — set once; the persisted json makes it tier 1 forever after (documented in the guard's refusal message).
- Live boots: NOT needed (tier 1) ⇒ zero live-.env touch (binding principle holds).
- Promote legs: NOT needed (tier 1 on any install that has booted once; a brand-new install dir booted by a promote leg would need the var — flag as a leader decision point §12 item 7).

**Exit code**: **78** (analysis §5.5 contract — "restarting cannot fix this"; launcher `classify_exit 78→refuse`, unit `RestartPreventExitStatus=78`, no auto-restart). Marker line naming: the resolution tier attempted, the ambient var names observed (names only — never values, F7), and the fence var name. Reuse 78 (do NOT commission a dedicated code now; piecemeal codes land in `*→crash` and loop unless the launcher learns them — analysis §5.5).

**Site ordering**: the guard runs BEFORE `load_or_create` (so the tier-2 poison cannot auto-create + persist the json) and BEFORE any engine construction / `SELECT 1` (so refusal is before any TCP connection). At `__main__.py:211` the guard calls `sys.exit(78)` directly (same home as the existing exit-75/78 verdicts). At `api.py:727` (lifespan, `run_preflight=False` path) the guard raises `SystemExit(78)` ~~(propagates through uvicorn startup as a clean exit-78, not exit-1 crash-loop)~~. **CORRECTION (2026-11-12, iter-1 D4):** the lifespan call is a defense-in-depth backstop whose failure mode is exit 3 (crash track) — uvicorn's `LifespanOn` catches `BaseException` and `_serve_with_bind_retry`'s `not server.started` tail yields `STARTUP_FAILURE=3`. It is unreachable when the `__main__` preflight ran. The `__main__` preflight (before uvicorn) is the exit-78 owner on BOTH entry paths. Code is NOT restructured; only this doc note + the api.py/ensemble_config comments are corrected.

**Adjudication of ENSEMBLE_UPGRADE_LIVE**: covered in §4.3 — strip at the two seams, overlay-wins residual flagged.

### 6.3 Copied-json residual (declared hole)

The guard covers the ambient tier (tier 2). A copied `ensemble.json` pinning a live-shaped postgres DB (tier 1) is NOT caught — tier 1 is the system's own declared config mechanism. Catching it requires install-identity provenance inside `ensemble.json` (e.g. an `install_dir` field checked against the booting install; mismatch ⇒ refuse). That is a future hardening, NOT in scope — flagged as a leader decision point (§12 item 8) and recorded as a declared hole (§11 R6).

### 6.4 G5 Tension — RED-leg safety (live-DB refusal without a forbidden name)

`tests/postgres/conftest.py:136-142` forbids `ensemble_prod`/`ensemble_dev`/`ensemble_prod_test` even on disposable scratch clusters. The RED-leg must prove live-DB refusal WITHOUT a forbidden name. **Pick: unit-level synthetic `ensemble.json` + create_engine sentinel (primary), with a mirror-side disposable-cluster leg for end-to-end shape.**

Justification:
- **Primary (unit-level)**: the guard sits BEFORE `load_or_create` and BEFORE any engine construction. A unit test patches `sqlalchemy.create_engine` (and the ensemble engine factory) with a sentinel that FAILS the test if called, runs the guard against a SYNTHETIC `ensemble.json`-absent data dir with ambient `POSTGRES_HOST`/`POSTGRES_DB` set to scratch values (e.g. `127.0.0.1` / `ensemble_mirror_scrub_probe` — NOT a forbidden name) and `ENSEMBLE_DB_AMBIENT_ALLOW` unset, asserts `SystemExit(78)` + marker line + sentinel untouched (zero connections by construction). No TCP, no forbidden name, deterministic.
- **Mirror leg (end-to-end)**: a harness script modeled on `boot_v2.sh` boots a staged copy against a disposable PG cluster (`PG_TEST_*` → scratch DB name `ens_e2e_scrub_probe`, never a forbidden name) with the fence var UNSET and ambient POSTGRES_* present, asserts exit 78 + zero connections observable on the cluster (pg_stat_activity count delta for the scratch DB). Proves the guard fires in a real boot before any TCP connect.
- **Rejected alternative (parameterized guard target / name-based denylist)**: a daemon-side denylist of "live-shaped" DB names is brittle and duplicates conftest's fence at the wrong layer; and it is exactly the thing G5 forbids testing against. The ambient-tier predicate (§6.2) sidesteps name-matching entirely.

---

## 7. F7 Rules (binding for any harness/capture this plan introduces)

1. Scrub ambient BEFORE effect (per-variable, never `env -u POSTGRES_*` glob — bash does not glob env names; scrub one-by-one or set explicitly). Precedent: `boot_v2.sh:22-26`.
2. Captures are redacted: name + value sha256 prefix, NEVER the raw value.
3. Fail-closed (refuse on uncertainty; never silently proceed).
4. `umask 077` on scratch artifacts.
5. FAKE_HOME + escape guard (`tests/upgrade_test_lib.sh:69-102`): HOME must resolve under `/tmp`, must not touch `/home/nea`.
6. Scrub-then-re-read-your-own-fence: after the ambient scrub, re-read your own port/PG file (`boot_v2.sh:27-28` lesson).

---

## 8. Pin Skeleton (mutation discipline)

Positive shapes only; each pin carries a counter-mutation leg that reddens it; absence-only pins forbidden. Exemplars: `tests/unit/test_ari_pm_terminology_pins.py` (:88-92 positive-count gate; :118-150 pure checker; :329-422 in-memory mutation legs; :409-412 non-vacuity control); `tests/bind_retry_mutation_matrix.md` (13 mutants, sha256 restore protocol :13-17, venue discipline :24-36); `tests/unit/agents/test_aegis_security_family.py:2527-2564` (wrong-pin refusal probe).

### Pin (i) — scrub-effect positive shape

- **What**: a probe child spawned through the scrubbed seam asserts stripped vars ABSENT from its env AND explicit-setter vars PRESENT (the allowlist path works).
- **Shape**: unit test invokes `apply_spawn_boundary` (and/or the bash tool end-to-end) with a poisoned `os.environ` (`PORT=9797`, `HOST=0.0.0.0`, `POSTGRES_HOST=live.example`, `ENSEMBLE_UPGRADE_LIVE=1`) plus an explicit overlay `{"PORT":"12345"}`; child prints its env; assert `PORT==12345` (overlay wins), and `HOST`/`DAEMON_PORT`/`LIMITS_METRICS_LISTENER_PORT`/`POSTGRES_*`/`ENSEMBLE_UPGRADE_LIVE` are all ABSENT. Service-spawner leg: `subprocess.Popen` with the new `env=` kwarg + `extra_env={"PORT":"9"}` ⇒ child sees PORT=9 and stripped set absent.
- **Counter-mutation**: remove one strip entry from `SPAWN_BOUNDARY_STRIP_ENV` ⇒ that var survives ⇒ RED. Remove the overlay-merge line ⇒ PORT absent ⇒ RED. Proves the pin is not vacuous.

### Pin (ii) — rider-(d) derived metrics port

- **What**: derived/scoped metrics port never equals live 9798 when main is scratch (fixes anomaly 07e8410c for census lane 3).
- **Shape**: with main port 8099 (scratch), metrics = 8100 (not 9798); with main port 9797 (live), metrics = 9798 (unchanged); with `LIMITS_METRICS_LISTENER_PORT=19999` explicitly set, metrics = 19999 (escape hatch honored). Pin the bind-site code reads the env AFTER the derive and prefers it. Also pin: a DB managed row for `limits.metrics_listener_port` does NOT override the derived value (the registry entry retired).
- **Counter-mutation**: revert derive to `getattr(config.limits, "metrics_listener_port", 9798)` ⇒ scratch lane collides on 9798 ⇒ RED.

### Pin (iii) — DB-refusal before any TCP connection

- **What**: poisoned ambient + synthetic `ensemble.json`-absent data dir ⇒ refuse (exit 78) BEFORE any TCP connection; assert zero connections to the disposable PG / closed port.
- **Shape**: primary unit leg patches `create_engine` with a failing sentinel; guard raises `SystemExit(78)` with a marker line naming the tier + ambient var names + fence var; sentinel never called. Mirror leg: harness boot (§6.4) exits 78 with zero pg_stat_activity rows for the scratch DB.
- **Counter-mutation**: remove the ambient-tier predicate (or the fence-var check) ⇒ guard never fires ⇒ boot proceeds to engine construction ⇒ sentinel called / connection made ⇒ RED. Non-vacuity control: with `ENSEMBLE_DB_AMBIENT_ALLOW=1` set, guard does NOT fire and engine construction proceeds ⇒ green (proves the fence var is the load-bearing condition, not a vacuous always-refuse).

### RED-leg safety (non-negotiable)

- Fenced mirror only: scratch port + per-var scrub (never `env -u POSTGRES_*` glob) + FAKE_HOME/escape guard (`tests/upgrade_test_lib.sh:69-102`) + fresh disposable PG via `PG_TEST_*` (`tests/support/wedge_smoke.py:552-586` translation `PG_TEST_* → POSTGRES_*`).
- Model on `boot_v2.sh` (closest ancestor; scrub-then-re-read lesson `:22-28`); pytest machinery from `tests/support/wedge_smoke.py`.
- Frozen-binary boots MUST pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly (critical note).
- No test touches live-shaped DB names (`FORBIDDEN_DB_NAMES`); no test contacts the live install; no `env -u POSTGRES_*` glob.

---

## 9. Task Breakdown (developer, ordered)

1. **Create `daemon/services/spawn_env_policy.py`** (or `daemon/tools/`): `SPAWN_BOUNDARY_STRIP_ENV` frozenset + `scrub_spawn_env` + `apply_spawn_boundary`. (No new table.)
2. **Seam 1 (`daemon/tools/bash.py`)**: collapse the None-inherit path; always materialise `apply_spawn_boundary(os.environ, env)`; preserve secret-marker resolution + KMS resolution + reaper stamp ordering; explicit overlay wins.
3. **Seam 2 (`daemon/tools/service_spawner.py`)**: add `env=apply_spawn_boundary(...)` to `Popen`; add `extra_env` deliberate-setter parameter; thread it through `service_tool_manager` callers.
4. **Rider (d) — derive (`daemon/api.py:2694-2696`)**: derive metrics = env `LIMITS_METRICS_LISTENER_PORT` if explicitly set else `config.daemon.port + 1`; retire the `limits.metrics_listener_port` managed-key entry in `settings_service.py` (registry + `app_settings` row cleanup task, no schema change); update the settings UI/registry docstring.
5. **DB guard (`daemon/ensemble_config.py`)**: add `assert_db_resolution_sanctioned(data_dir)`; call at `daemon/__main__.py:211` (before `load_or_create`, `sys.exit(78)`) and `daemon/api.py:727` (before `load_or_create`, `SystemExit(78)`). Marker line with tier + ambient var names + fence var name (names only).
6. **Sanctioned-lane fence-var additions**: `dev.sh` env block; `wedge_smoke.scratch_env` env dict; new mirror harness script (modeled on `boot_v2.sh`) for the RED leg.
7. **Pin files (new tests)**:
   - `tests/unit/test_spawn_boundary_scrub.py` — pin (i).
   - `tests/unit/test_metrics_port_derive.py` — pin (ii).
   - `tests/unit/test_db_resolution_guard.py` — pin (iii) (unit-level synthetic json + create_engine sentinel).
   - `tests/unit/test_spawn_env_policy.py` — positive-count gate on `SPAWN_BOUNDARY_STRIP_ENV` (bind family 4 + DB family 5 + attestation 1 = 10 names) — mutation-discipline positive shape.
8. **Mirror harness script** (new, under `.agents/tester/RESULTS/assets/...` or a test pack): RED leg for pin (iii) end-to-end against a disposable PG.
9. **Operator doc note** (in the plan / a runbook line): H3 SELECT + first-boot postgres fence var usage.
10. **Commit** pathspec-limited to the plan file (this commit) and later, per the developer's own commits, the code changes above.

Test files to create (component B owned): the four listed in step 7 + the mirror harness in step 8. Component A (sibling) and pins-integration (later worker) own their own files; do not write them.

---

## 10. Coupling

| | B (this) | A (sibling) | Pins-integration (later) |
|---|---|---|---|
| B | — | loose (shared incident; A owns port-ownership, B owns delivery channel + DB) | tight (B's pins feed the integration sequence) |
| A | loose | — | tight |
| Pins-integration | tight | tight | — |

- **Tight within B**: seams 1+2 share the policy module; rider (d) binds at `api.py:2694` which is the same lifespan as the DB boot-apply `:749` (ordering constraint §5.2); DB guard shares `ensemble_config.py` with `load_or_create` (must fire before it).
- **Loose**: B does not depend on A's claim-before-bind mechanism; B's scrub works regardless of A. A's refuse-on-foreign-owner (exit 78) is a complementary control, not a prerequisite.
- **Independent**: migration expectation (zero tables) means no schema coupling with any lane.

---

## 11. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | Rider (d) derive retires the `limits.metrics_listener_port` managed key; operators who set it via settings UI see a dead knob | Medium | Medium | Retirement task + UI copy + orphaned-row ignore; document the env escape hatch. |
| R2 | Guard (b) refuses a legit fresh-postgres boot whose operator forgot the fence var | Medium | Medium | Clear refusal marker naming the fence var + one-line remediation; first-boot json persists ⇒ only the FIRST boot needs it. |
| R3 | Scrub breaks agent scripts that read `$PORT`/`$HOST`/`$POSTGRES_*` from ambient | Low-Medium | High | Desired (H4 loud failure); document; agent scripts switch to explicit values. |
| R4 | `wedge_smoke.scratch_env` must add the fence var or its tier-2 postgres selection refuses | High (test breakage) | High | Task 6 adds `ENSEMBLE_DB_AMBIENT_ALLOW=1` to scratch_env; pin (iii) non-vacuity control proves it. |
| R5 | Promote leg on a brand-new install dir (no json) needs the fence var | Medium | Low | Flag as leader decision (§12 item 7); default: promote legs set it via the executor's explicit-extra merge when target install lacks json (mirrors demo_arm_extras). |
| R6 | Copied `ensemble.json` pin (tier 1) pointing at live DB is NOT caught by guard (b) | High | Low | Declared hole (§6.3); future hardening = install-provenance field in json. Leader decision (§12 item 8). |
| R7 | Overlay re-add of `ENSEMBLE_UPGRADE_LIVE` via agent env= | Medium | Low | F2 gate (`--f2-verified-closed`) still refuses; flag as leader decision (§12 item 6). |
| R8 | Derive changes metrics port for lanes that pinned 9798 explicitly via yaml | Low | Low | Env escape hatch (`LIMITS_METRICS_LISTENER_PORT`) preserves explicit pin; derive only replaces the default/yaml-9798 path. |
| R9 | Service-spawner `extra_env` parameter threading touches callers | Low | Medium | Default None ⇒ byte-identical behavior for existing callers; pin (i) covers the new path. |
| R10 | Guard at `api.py:727` (lifespan) must not become exit-1 crash-loop | High | Medium | Design uses `SystemExit(78)` (propagates as clean exit-78), not a raised Exception; pin (iii) asserts exit code 78. |

---

## 12. Leader Decision Points

1. **§9.2 rider (d) shape** — derive (recommended) vs env-wins vs combined. Analysis leans derive-or-env-wins, never a YAML literal; H3 unverified.
2. **H3 live DB row** — run the operator SELECT before relying on the env lever (read-only, §5.5).
3. **Lane 4 (burst-window fix)** — defer or adopt now (analysis §5.4) — NOT in scope here, but the decision affects whether (e′) alone is sufficient.
4. **Operator alert channel for the refusal path** — analysis §5.2 mandates an alert ships with (f); (e′)/(b) inherit the same need for the DB guard's refusal (silent-down risk). Which channel?
5. **Teardown-budget reconciliation** — later (Lane 5).
6. **ENSEMBLE_UPGRADE_LIVE overlay re-add** — strip-even-from-overlay vs overlay-wins (§4.3).
7. **Promote leg on json-less install** — should the executor forward `ENSEMBLE_DB_AMBIENT_ALLOW=1` via explicit-extra merge when target lacks json (mirrors demo_arm_extras)?
8. **Copied-json residual** — ship install-provenance field in `ensemble.json` now or defer (§6.3).

---

## 13. Contradictions (known + any discovered)

1. **The commission's B root — `data/ensemble.json` DB pin — is analysis-silent.** The analysis's Lane 3 strip list covers only the four bind vars (`PORT`/`DAEMON_PORT`/`HOST`/`LIMITS_METRICS_LISTENER_PORT`). This plan's DB half (§6) extends the (e′) MECHANISM to the DB-poison family and adds the boot-time guard; it does NOT carry analysis authority for the DB half. Recorded per commission instruction.
2. **The analysis's binding principle ("touches launcher.sh = ZERO, unit template = ZERO, live .env = ZERO") vs. the DB guard's fence var.** The guard (§6.2) needs `ENSEMBLE_DB_AMBIENT_ALLOW=1` somewhere for sanctioned fresh-postgres boots. Resolution: the var lives in dev.sh, test harnesses, and operator first-boot — NOT in launcher.sh/unit/live-.env — so the principle holds for the live install. Flagged as a tension, resolved by scoping (live boots are tier 1 and need no var).
3. **Rider (d) derive retires a managed key** that the db-backed-settings commission shipped as managed (`limits.metrics_listener_port`, `settings_service.py:524-544`, `requires_restart=True`). This is a deliberate narrowing of that commission's surface; the settings UI must stop offering the knob. Not a contradiction per se, but a cross-commission impact to record.
4. **No other contradictions found** against the supplied blueprint facts; all anchors in §3 verified at cace21473.

---

## 14. Migration Expectation

**ZERO new SQLModel tables.** No schema change. The `app_settings` table already exists; the only data change is retiring one managed-key row (optional cleanup of an orphaned `limits.metrics_listener_port` row — a DELETE, not a migration). State it explicitly: component B is code + config-registry + test only.

---

## 15. Out of Scope / Cross-References

- **Component A** (port-ownership contract) — sibling worker; owns `daemon/services/port_claim.py` (or equivalent) + claim-before-bind + exit-78 refuse + operator alert. Cross-reference by filename; do not write its content.
- **Pins-integration / sequencing / overview** — later worker; owns the integration sequence combining A + B + Lane 1.
- **Lane 1** (bind-retry) — in flight, ship unbundled.
- **Lane 4 / 5** — deferred.
- **Install-provenance field in ensemble.json** (§6.3) — future hardening, leader decision.

---

## 16. Plan Format Metadata

- Feature: `rogue-containment` (component B)
- Output: `.agents/shared/planning/rogue-containment/component-B-spawn-boundary-scrub.md`
- Phases: 1 (this plan is a single implementation phase for the developer; task breakdown in §9 is the ordered execution list)
- Tasks: 10
- Coupling: tight-with A (loose), tight-with pins-integration (tight)
- Migration: zero tables
- Risks: 10 (R1-R10)
- Leader decisions: 8
- Contradictions: 4 (3 substantive + 1 "none further")
