# Plan: Component B-Residual — Tier-1 Prod-DSN Fence (occ#9 closer)

Date: 2026-11-14
Author: planning worker B2 (plan-creation skill; plan only — no code, no tests run, no repo writes except this file)
Status: Draft — Ready for Review
Worktree: `/home/nea/ensemble-src-wt-rogue-contain` (branch `feature/rogue-port-containment`, tip `bf4bfde6e720d889bb5dc00dfe9490ceb5dbafc1` — verified at read time)
Feature: rogue-containment phase 2 (p2 lane)
Component: B-RESIDUAL — the tier-1 prod-DSN fence (the lane's main build, closing occurrence #9 of incident wi-2454ede3)
Mode: planning-only (developer-executable plan below; NO implementation by this worker)
Governing authority: `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (FINAL rev 2) — §5.2 (exit-78 + marker mechanics), §5.5 (exit code), §5.3/§6 lane rows (containment lands in the daemon binary; launcher.sh/unit/live-.env untouched), §7.3 (metrics-port fence + H3 verify), §8 (declared holes H1/H3), §9 (leader decisions 2/3/4)

---

## 1. Objective

A boot whose **resolved** DB resolution targets `ensemble_prod` — the live production database — is refused with exit 78 + a marker line **unless the boot's identity is the sanctioned live environment**, closing the occ#9 shape: today a non-live boot resolving `ensemble_prod` via `data/ensemble.json` is "sanctioned by construction" (`daemon/ensemble_config.py:170-171` — tier-1 early `return`; no prod-NAME predicate exists anywhere in the boot path), which is exactly how the 2026-11-11 rogue (env-less staged binary, inherited `PORT=9797`) also attached to live `ensemble_prod` and wrote to prod for 2h38m. The fence additionally closes: (1) the env-wins DSN override (`get_postgres_url`, `ensemble_config.py:122-130`) as a poison vector for refusable shapes, (2) the inheritable `ENSEMBLE_DB_AMBIENT_ALLOW` bypass of the tier-2 guard, and (3) the `daemon/auth/cli.py:150` guard exemption. Sanctioned boots — the live boot, the promote/upgrade lane, every dev/demo/test lane on non-prod names — must never be refused.

---

## 2. Scope

### In Scope

- **Prod-name predicate on the RESOLVED DSN**, evaluated in the boot path for BOTH resolution branches: tier-1 (json-present) and tier-2 (ambient-driven), via one new guard function on the loaded `EnsembleConfig` (§4.2).
- **Sanctioned-boot carve-out** reusing the repo's existing identity machinery (`ENSEMBLE_SELF_ENV` / auto-derive / install-dir), per §4.3.
- **Env-wins closure for refusable shapes**: the predicate evaluates the POST-resolution DB name (the same env-wins rule `get_postgres_url` applies), so an ambient `POSTGRES_DB=ensemble_prod` override of a tier-1 file boot is caught. Non-prod env-wins (credential rotation) is explicitly preserved (§4.5).
- **`ENSEMBLE_DB_AMBIENT_ALLOW` semantics tightening**: the fence var grants tier-2 (ambient-first-boot) sanction only; it must never exempt the prod-name predicate (§4.6).
- **`auth/cli.py:150` exemption closure** + the sanctioned operator path for the CLI (§4.7).
- **Exit-78 refusal mechanics**: same code, marker line, fail-closed on guard-internal error (mirror `ensemble_config.py:195-206`); no new exit codes (§4.8).
- **Pin skeleton with mutation discipline** (§7), extending `tests/unit/test_db_resolution_guard.py` + a new pin file.
- Rider remainder: the **H3 read-only DB-row verification step** (§5) — the 9798 fence build itself is ALREADY SHIPPED (see §5.0 and §12, contradiction C2).
- Task breakdown (§8), coupling (§9), risks (§10), leader decision points (§11), contradictions (§12), migration expectation (§13), cross-references (§14).

### Out of Scope

- **Component A** (bind-time socket-holder identity at the port gate) — parallel worker; ordering contract stated in §9.1, cross-referenced by filename only.
- **The tier-2 ambient-DB guard as shipped** — phase-1 build, already live in this worktree's ancestry (commit `1ec2afb1b`, "rider (d) metrics derive + retire managed key + DB-resolution guard [phase 4]"); this plan only TIGHTENS its fence-var semantics (§4.6) and rewrites its docstring claims (§4.2).
- **The 9798 metrics-port fence build** — shipped as rider (d) phase 4 (`1ec2afb1b`; `derive_metrics_port` at `daemon/services/metrics_listener.py:75-142`, pins in `f4157fbf0`). Only the H3 read-only verification remains (§5).
- **Lane 1** (bind-retry), **Lane 4** (sliding-window burst fix, trigger-gated), **Lane 5** (teardown budget) — analysis §5.1/§5.4/§6.
- **`launcher.sh`, the systemd unit template, the live `.env`** — binding principle (analysis §6): zero touches.
- **Credential/host-level DSN poison** (ambient `POSTGRES_HOST` re-pointing at an impostor host with a non-prod DB name) — different threat class (data exfil, not prod attachment); declared hole in §10-R7, not fenced here.
- **Migrations / schema changes** — none; CODE-ONLY confirmed (§13).
- **Promote ceremony, live mutation, pkill, `/home/nea/ensemble-src` writes** — forbidden (§6).

---

## 3. Governing Analysis + Anchor Verification

The analysis governs for refusal mechanics (§5.2: exit 78 + holder/marker line, positive-proof-only, degrade-never-refuse on mechanism failure), exit code (§5.5: 78 is exact — "restarting cannot fix this"; a new code lands in `*→crash` and loops; 75 is budget-exempt tempfail), containment placement (§6: all containment in the daemon binary), and leader decisions §9-2 (9798 shape), §9-3 (reuse 78), §9-4 (operator alert). Where the analysis is silent (the prod-NAME predicate and the sanctioned-identity carve-out are post-analysis lane additions), this plan anchors on verified in-repo precedent and flags the deltas in §12.

**All anchors below were re-verified by this worker at tip `bf4bfde6e` (file:line as read).**

Primary file — `daemon/ensemble_config.py` (206 lines):

- `:71-76` json-present branch: file "database" pin wins outright; `:170-171` guard tier-1 early `return  # tier 1 — sanctioned` — THE gap this fence closes.
- `:82-92` env auto-detect only in the file-absent branch (tier-2 shape: `POSTGRES_HOST` AND `POSTGRES_DB` both set ⇒ `database="postgres"`; sqlite-dbs-present sub-branch :83-86).
- `:114-130` `get_postgres_url`: per-key env override `:124-128` — `db = os.environ.get("POSTGRES_DB", pg.db)` at `:126` (the env-wins aggravator; docstring `:8-9` declares env-over-file intentional for credential rotation — the fence must NOT kill that).
- `:138-206` `assert_db_resolution_sanctioned`: docstring `:145` "Documented exemption: daemon/auth/cli.py:150 (no code change there)"; fires iff json ABSENT + ambient `POSTGRES_HOST`&`POSTGRES_DB` + `ENSEMBLE_DB_AMBIENT_ALLOW != "1"` (`:147-150`, code `:170-177`); `sys.exit(78)` `:192` after `logger.error` marker `:183-191` (var NAMES only, never values — F7-compliant); fail-closed on internal error `:195-206` ("UNVERIFIABLE … refusing boot (fail-closed; exit 78)"); docstring `:152` states "Tier 1 (json exists) => does not fire" — the claim this build flips (§4.2).

Call sites of the tier-2 guard (verified):

- `daemon/__main__.py:211-212` — `_boot_db_preflight`: `from .ensemble_config import assert_db_resolution_sanctioned` / `assert_db_resolution_sanctioned(data_dir)`, then `EnsembleConfig.load_or_create(data_dir)` `:213`, then the `is_postgres` branch `:214-216` leading into the connectivity probe (`create_engine` + `connect_timeout` later in the same function). The preflight is the exit-78 owner on BOTH entry paths (python -m daemon and frozen `run_app`) per guard docstring `:156-163`.
- `daemon/api.py:752-753` — lifespan backstop: import + call, AFTER the port-ownership marker surface (`surface_refusal_marker()` `:735-737`, comment block `:727-733`) and BEFORE `EnsembleConfig.load_or_create(data_dir)` `:754`; pinned structural order by `tests/unit/test_db_resolution_guard.py:123-133`. SystemExit(78) here maps to exit 3 (crash track) through uvicorn's LifespanOn (comment `:744-751`; pin `:136-145`).
- Port-ownership gate (component A, shipped): `daemon/__main__.py:450-457` — comment + `run_port_ownership_gate(host, port)` `:457`, inside `_serve_with_bind_retry` AFTER `install_root_logging()` `:444` and BEFORE bind. Confirms DB preflight ordering precedes the port gate (§9.1).
- `daemon/auth/cli.py:126-163` — `resolve_engine`: data_dir precedence `:144-147` (`ENSEMBLE_DATA_DIR > DATA_DIR > ./data`), `EnsembleConfig.load_or_create(data_dir)` `:150`, engine resolution `:151-163`; `resolve_engine` is called once, at `cli.py:265` (CLI `main`, subcommands `issue` `:87` / `revoke` `:115`). Single DB entry point for the CLI.

Identity precedent (the carve-out's machinery — verified):

- `daemon/tools/upgrade_tools.py:564-606` `_self_env_marker` (docstring spans :566-606): resolution order (1) explicit staged `ENSEMBLE_SELF_ENV=dev|demo|live|sandbox` wins (`_explicit_marker` `:261`, reads `:254`), (2) explicit opt-out false-values (`:222` vocabulary) → None, (3) auto-derive `_auto_derive_env` `:441-562`: frozen-binary + `releases/` + non-canonical install dir → `sandbox` (`:476-491`); canonical install-dir topology + DB cross-check → `live`/`demo` (`:494-533` — lanes `("agents-ensemble", "ensemble_prod", "live")`, `("agents-ensemble-demo", "ensemble_demo", "demo")`; own-`$POSTGRES_DB` evaluated FIRST under the flipped-HOME shape `:536-546`); dev-shape `POSTGRES_DB=ensemble_dev` → `dev` (`:545-547`); no signal → None (fail-closed preserved).
- `_LIVE_INSTALL_DIR_NAMES = ("agents-ensemble", "agents-ensemble-demo")` `:373`; `_resolve_install_dir` `:666-688` (live → `_canonical_install_root("agents-ensemble")`).
- **The import direction is proven precedent**: `daemon/services/port_ownership.py:207-214` (`resolve_self_install_dir`) already imports `_resolve_install_dir, _self_env_marker` from `daemon.tools.upgrade_tools` — boot-path guards consuming actor-identity machinery is established (this fence does the same).
- Deliberate-PORT-setter allowlist precedent: `daemon/manager.py:6411` `extra_env["PORT"] = str(spec["port"])` (promote/restart executor). The executor allowlist itself: `upgrade_journal.py` `EXECUTOR_ENV_ALLOWLIST` = `PATH, HOME, INSTALL_DIR, PORT, POSTGRES_DB, TMPDIR, XDG_RUNTIME_DIR, DBUS_*, ENSEMBLE_PROMOTE_WATCHDOG` (+ `PG` prefix) — note `ENSEMBLE_SELF_ENV` is NOT forwarded by the executor (§11-D6).
- Forbidden-name precedent: `tests/postgres/conftest.py:136-142` `FORBIDDEN_DB_NAMES = frozenset({"ensemble_prod", "ensemble_dev", "ensemble_prod_test"})` — the harness fence. This plan's predicate is NARROWER: `ensemble_prod` ONLY (§4.4) — `ensemble_dev`/`ensemble_prod_test` are legitimate non-prod names a scratch boot may carry.
- Env-wins precedent for the rider (already shipped): `daemon/config.py` `MemoirConfig._env_tier_wins` model_validator `:4746` (house env-wins shape); the commissioned citation `config.py:795-798` has drifted — the `metrics_listener_port` Field now sits at `:821-828` (default 9798, `LIMITS_METRICS_LISTENER_PORT`, 127.0.0.1-only) (§12-C1).
- Spawn-boundary scrub (phase-1, shipped): `daemon/services/spawn_env_policy.py` — `SPAWN_BOUNDARY_STRIP_ENV` = 12 names (bind 4 incl. `LIMITS_METRICS_LISTENER_PORT`; DB 7 = `POSTGRES_HOST/PORT/DB/USER/PASSWORD` + `ENSEMBLE_CONFIG` + `DATABASE_URL`; attestation 1 = `ENSEMBLE_UPGRADE_LIVE`) + `PG` prefix family with `PG_TEST_` exemptions (`:43-86`); consumed by `daemon/tools/bash.py`, `daemon/tools/service_spawner.py`, `daemon/tools/proc_tools.py`. **`ENSEMBLE_DB_AMBIENT_ALLOW` is NOT in the set** — confirming aggravator 2 (§4.6). The scrub guards only daemon→child spawns — a directly-launched boot (shell script, lane runner, cron) never traverses it, which is why boot-path fences are required (§4.6).
- Rider (d) shipped state: `daemon/services/settings_service.py:241-246` — "the managed key `limits.metrics_listener_port` is RETIRED (registry entry removed)"; registry carries EIGHTEEN entries (`:88`, `:220`); derive consumed at `daemon/api.py:2736-2749` (`derive_metrics_port(config.daemon.port, …)` `:2738`, non-fatal disable on None `:2744`). Pins: commit `f4157fbf0` ("mutation-proven pin suite (port-ownership + spawn-boundary + metrics-derive + db-guard + overlay)"); docs `715f4eedc`.
- Existing pin file: `tests/unit/test_db_resolution_guard.py` (145 lines, exactly 10 tests — counted). Key pins: `test_guard_does_not_fire_tier1` `:30-37` (json `{"database":"sqlite"}` + ambient `ens_e2e_scrub_probe` → no raise) — see §12-C3 for the precise flip analysis; scratch name `ens_e2e_scrub_probe` used throughout (`:23,35,53,71,108`); refuse-path engine sentinel `:58-75` ("engine must not be constructed on refuse path"); fail-closed pin `:78-84`; call-site pins `:101-133`; exit-3 mapping pin `:136-145`.
- House style: `.agents/shared/planning/rogue-containment/component-B-spawn-boundary-scrub.md` (394 lines) — metadata block + numbered sections; followed here.

---

## 4. Design

### 4.1 Mechanism overview

ONE new guard function in `daemon/ensemble_config.py`, evaluated at the three DB-resolution entry points AFTER `EnsembleConfig.load_or_create` and only when resolution is postgres:

```
assert_prod_dsn_sanctioned(ensemble_config: EnsembleConfig) -> None
```

The tier-2 pre-guard (`assert_db_resolution_sanctioned`, json-absent + ambient) stays EXACTLY where it is (`__main__.py:212`, `api.py:753`) — its pins hold untouched; the new check is additive and runs on the ACTUAL `EnsembleConfig` instance the boot will use. Rejected alternative: extend the tier-2 guard to re-parse the json itself (would double-parse and drift from the instance the boot actually uses; also churns the guard<load pin structure). Second rejected alternative: one shared `run_db_preflight_checks()` helper used by all three sites (cleaner, but churns both pinned call sites and the CLI for no behavioral gain — three small insertions beat one pin-breaking refactor).

### 4.2 The predicate (design requirement 1)

- Helper `resolved_postgres_db_name(config) -> str` mirrors `get_postgres_url:126` EXACTLY: `os.environ.get("POSTGRES_DB", config.postgres.db)` — the POST-resolution name, so the env-wins override is inside the predicate's view (closes aggravator 1 for refusable shapes; §4.5). Normalization follows the house idiom (`_auto_derive_env`'s `db_hint` at `upgrade_tools.py:529`): `.strip().lower()` before comparison. (Postgres identifiers are case-sensitive in general; the house already normalizes DB-name hints this way, and the predicate is a name-safety check, not a SQL identifier match.)
- Predicate: `resolved_postgres_db_name(config) == "ensemble_prod"` — EXACT match after normalization. Nothing else.
- Rejected alternatives, with the mutants that kill them (pin P2 in §7 counter-proves):
  - Prefix/pattern (`startswith("ensemble_prod")`, glob, or the full `FORBIDDEN_DB_NAMES` set): `ensemble_prod_test` is a LEGITIMATE scratch DB name (in the harness forbidden set only because the harness DROPS tables — `tests/postgres/conftest.py:224-230`), and `ensemble_dev` is the sanctioned dev DB (`_auto_derive_env` signal 3). A sloppy pattern breaks test lanes and dev boots.
  - File-value-only check (`config.postgres.db` without the env tier): misses the env-wins override shape (the incident's own aggravator 1).
- **Semantic flip, stated for the ledger**: the guard docstring `:152` ("Tier 1 (json exists) => does not fire") is superseded — tier-1 is no longer categorically "sanctioned"; the tier-2 PRE-guard's branch is unchanged (json-present still short-circuits the AMBIENT tier at `:170-171`), but the new POST-load check fires on a tier-1 config whose resolved name is `ensemble_prod` without live identity. The docstring rewrite (and the surviving pin's comment, §7-P0) is a required task (T1), not optional hygiene — a plan's body and its ledger must agree.

### 4.3 The sanctioned carve-out (design requirement 2)

On predicate hit, resolve identity via `_self_env_marker()` (import from `daemon.tools.upgrade_tools` — precedent `port_ownership.py:207-214`):

| Resolved identity | Verdict | Rationale |
|---|---|---|
| `"live"` | SANCTIONED — proceed, one INFO line naming the resolution source (explicit / derived) | The live boot and promote-executor boots resolve live: explicit staged marker wins when present; otherwise auto-derive hits install-dir topology + `POSTGRES_DB=ensemble_prod` cross-check (`upgrade_tools.py:494-546`) under BOTH HOME shapes. The promote lane boots from `<install>/releases/<ver>/` with `HOME` + `POSTGRES_DB` forwarded by the executor allowlist ⇒ derives live without any executor change (§11-D6). |
| `"dev"` / `"demo"` / `"sandbox"` | REFUSE — exit 78 + marker | Positive proof of a NON-live boot targeting prod. The incident's rogue derives `sandbox` (frozen staged binary + `releases/` + non-canonical dir, `upgrade_tools.py:476-491`) ⇒ the exact wi-2454ede3 shape is refused by construction. |
| `None` (no signal; includes explicit opt-out marker) | DEGRADE — loud WARNING, proceed (commission polarity: degrade-never-refuse for UNVERIFIABLE identity) | Declared residual (§10-R2): a non-frozen, marker-less, topology-less process with a copied prod-targeting json passes. Narrowed by: tier-2 already refuses the json-ABSENT ambient shape regardless of identity; component A's bind gate narrows the same-host port shape. Leader may flip (§11-D1). |
| resolver RAISES | FAIL-CLOSED — exit 78 + "UNVERIFIABLE" marker (mirror `:195-206`) | Same polarity as the shipped N5 leader decision: an evaluation failure is not a resolution of None. |

**Why refuse on positive non-live proof but not on None** (commission requirement 2, stated as failure modes): refusing on `None` would down a live boot whose identity evidence is catastrophically degraded (marker absent AND install `.env` missing AND own `$POSTGRES_DB` empty — a live install that broken fails at DB auth moments later anyway, but the fence must not be the thing that downs live). Conversely `"sandbox"`/`"dev"`/`"demo"` are POSITIVE derivations — the resolver found affirmative non-live evidence, which cannot be a degraded live. This mirrors component A's accepted polarity (refuse ONLY on positive proof; unknown ⇒ degrade/retry).

**Residual (declared, not closed here)**: a same-host rogue whose ambient `POSTGRES_DB=ensemble_prod` + `~/agents-ensemble/.env` cross-check derives `"live"` passes the carve-out. This is the DB-fence analog of port-ownership's H1/same-uid residuals (analysis §8-H1; `port_ownership.py` module docstring "SAME-UID claim deletion"): identity here is HOST-scoped, not process-scoped. The containment boundary remains the uid + DB credential. Documented in the guard docstring and §10-R1.

### 4.4 Predicate scope (why prod-only)

`ensemble_prod` is the only name whose non-live targeting produced an incident (occ#9 wrote prod). `ensemble_dev` is the sanctioned dev DB; `ensemble_prod_test`/scratch names are harness territory (`tests/postgres/conftest.py` already refuses the forbidden set for harness boots). The boot-path fence refuses on ONE name and stays out of the harness's lane. Widening is a leader decision (§11-D2), not a default.

### 4.5 Env-wins closure, precisely scoped (aggravator 1)

Env-wins per-key override (`:124-128`) is INTENTIONAL (module docstring `:8-9`: credential rotation without rewriting the file). Therefore: no change to `get_postgres_url` itself. The closure is EVALUATION-SIDE: the predicate reads the post-resolution name (§4.2), so:

- tier-1 file `db=ensemble` + ambient `POSTGRES_DB=ensemble_prod` → resolved name `ensemble_prod` → predicate hits → refused unless live. (The refusable shape, closed.)
- tier-1 file `db=ensemble_prod` + ambient `POSTGRES_DB=ensemble_prod` → hits. (Sanctioned only via live identity.)
- any boot with resolved name ∉ {`ensemble_prod`} → env-wins untouched (rotation keeps working; host/port/user/password overrides unaffected).
- Declared hole: ambient `POSTGRES_HOST` re-pointing at a non-prod-named impostor host is NOT caught by this fence (§10-R7) — different threat class, different lane if commissioned.

### 4.6 `ENSEMBLE_DB_AMBIENT_ALLOW` tightening (aggravator 2)

Decision: **independent-gates semantics** (the allow-list option, sharpened). The fence var continues to grant exactly what it grants today — tier-2 (json-absent + ambient) first-boot sanction — and is permanently REMOVED from the prod-name decision path: `assert_prod_dsn_sanctioned` consults identity only, never `ENSEMBLE_DB_AMBIENT_ALLOW`. Consequence: a rogue inheriting `ALLOW=1` (it is NOT in the spawn-boundary strip set — verified, `spawn_env_policy.py:43-65`) that resolves a prod DSN is now refused by the prod-name gate even though it passes tier-2. Rejected alternative: non-inheritable re-derivation (replace the env var with an on-disk sanction record) — rejected because (i) the var's legitimate consumer (a deliberate first boot) is exactly a lane that cannot pre-stage files it doesn't own yet, (ii) any same-data_dir record is equally visible to a rogue resolving that data_dir, so the re-derivation buys nothing against the copied-json class, and (iii) the persisted `ensemble.json` ALREADY converts every sanctioned first boot to tier-1 (`:95`), making the var's lifetime naturally one boot.

**Why spawn-boundary scrubbing is NOT sufficient (required citation)**: `spawn_env_policy` fences only the environments the DAEMON hands to its children (`bash.py` / `service_spawner.py` / `proc_tools.py` seams). The incident's rogue was launched by a hand-written shell script — a DIRECT boot that inherits the ambient shell environment and never traverses any daemon spawn seam. `boot_isolated.sh` proved the per-run-script fence regresses (analysis §1.2/§5.3: the good fence was untracked scratch). Hence the fence must live IN the boot path (`__main__.py` preflight / `api.py` lifespan), which is this component.

### 4.7 `auth/cli.py:150` closure (aggravator 3)

**Decision: CLOSE the exemption.** Reasoning: the current shape is worse than a bypass — `load_or_create` at `:150` AUTO-CREATES and PERSISTS `ensemble.json` (`:95`, atomic write `:98-112`): a CLI invocation from a foreign cwd with ambient `POSTGRES_HOST`+`POSTGRES_DB` not only connects to the ambient DB (token ops against prod!) but WRITES the tier-1 sanction artifact, poisoning every later boot in that cwd. The "documented intentional" status (guard docstring `:145`) predates the incident-class evidence.

Sanctioned path for the CLI's legitimate needs (operator mints/revokes tokens on the live install):
1. `resolve_engine` calls `assert_db_resolution_sanctioned(data_dir)` BEFORE `load_or_create` (a refused CLI writes no poison artifact — the tier-2 guard fires pre-persist).
2. After `load_or_create` (`:150`), if `is_postgres`, call `assert_prod_dsn_sanctioned(ensemble_config)` — the CLI inherits the same carve-out: operator shell on the live host derives `live` (ambient `POSTGRES_DB=ensemble_prod` + install `.env` cross-check), dev CLI keeps working on `ensemble_dev`, scratch-dir CLI with prod ambient is refused.
3. Operator escape hatch for degraded identity: stage `ENSEMBLE_SELF_ENV=live` explicitly (explicit marker wins, `upgrade_tools.py:566-571`). Documented in the guard docstring + CLI help note.
4. Update guard docstring `:145` (exemption sentence removed; replaced by the closure note).

Exit mechanics: `sys.exit(78)` propagates out of the argparse `main` → CLI exits 78 with the marker line on stderr. Consistent with the house "configuration refusal" contract.

Rejected alternative: keep the exemption on the theory that the CLI is human-invoked. Rejected because agent lanes DO invoke DB-touching CLIs from task shells (the incident worker was shell-class), and the CLI's auto-persist behavior makes it an active poisoner, not a passive reader.

### 4.8 Refusal mechanics (design requirement 3)

- Same exit code `78` (`sys.exit(78)`, `classify_exit` `0→clean | 75→tempfail | 78→refuse | *→crash`; unit `RestartPreventExitStatus=78`) — reuse, never a new code (analysis §5.5; a new code lands in `*→crash` and loops; 75 is budget-exempt).
- Marker line via `logger.error`, same channel/style as the shipped tier-2 marker (`:183-191`): `"DB RESOLUTION REFUSED (rogue-containment): tier=resolved-dsn-targets-prod db=ensemble_prod self_env=<dev|demo|sandbox> resolution=<explicit|derived> — refusing boot; set ENSEMBLE_SELF_ENV=live only on the live install."` F7 discipline: ambient var NAMES only, never values — with one deliberate exception: the DB NAME (`ensemble_prod`) and the derived self-env label are named because they ARE the refusal reason (neither is a credential; the tier-2 marker's no-values rule exists for ambient var VALUES like passwords, not for the target name).
- Fail-closed on internal error: wrap the whole evaluation; any exception → the `:195-206` shape ("UNVERIFIABLE … refusing boot (fail-closed; exit 78)"). The `except SystemExit: raise` guard (`:193-194`) is mirrored so the deliberate exit is not swallowed into the fail-closed path.
- NO durable marker FILE for this fence (considered and rejected): component A's `port-ownership-refusal.json` + `surface_refusal_marker` (`port_ownership.py:473,535`; `api.py:735-737`) exists because bind refusals repeat silently across boots; the shipped tier-2 DB guard chose log-only, and a second writer type in the same marker file would corrupt the exactly-once rotation contract. The operator-alert question (analysis §9-D4) is lane-wide and already raised for BOTH refusal families — one decision, one channel (§11-D4).

### 4.9 Exit-track mapping per call site

| Site | Track on refusal | Notes |
|---|---|---|
| `__main__.py` preflight (python -m daemon AND frozen `run_app`) | exit 78 (refuse track) — pre-uvicorn, pre-TCP | The exit-78 owner; must run BEFORE the preflight's own connectivity probe (the probe creates an engine + connects — §8-T2 places the check before it). |
| `api.py` lifespan backstop | SystemExit → uvicorn STARTUP_FAILURE → exit 3 (crash track) | Identical to the shipped backstop mapping (`:744-753` comment; pin `:136-145`). Unreachable when the preflight ran; defense-in-depth only. |
| `auth/cli.py` | exit 78 straight out of `main` | Documented in §4.7. |

---

## 5. Rider — 9798 Metrics-Port Fence: SHIPPED; remainder is the H3 read-only verification (SEPARABLE — cuttable without touching §4)

### 5.0 Rider status correction (read this first)

The commissioned rider ("the 9798 metrics-port fence per analysis §7.3/§9-D2") is **ALREADY BUILT AND COMMITTED in this worktree's ancestry** — verified:

- Commit `1ec2afb1b` "feat(rogue-containment): rider (d) metrics derive + retire managed key + DB-resolution guard [phase 4]" (touches `daemon/__main__.py`, `daemon/api.py`, `daemon/ensemble_config.py`, `daemon/services/settings_service.py`).
- `derive_metrics_port` at `daemon/services/metrics_listener.py:75-142`: precedence = explicit env escape hatch > `main_port + 1`; clamp-by-disable (derived > `MAX_METRICS_PORT` or `PORT=0` ⇒ listener DISABLED, loud log, boot continues); NEVER a YAML literal — the analysis §9-D2 recommendation, implemented.
- Managed key `limits.metrics_listener_port` RETIRED from the registry (`settings_service.py:241-246`; registry now EIGHTEEN entries `:88,:220`); orphaned-row cleanup is a documented operator DELETE (dev/fenced DBs only, NEVER live — in-code comment).
- Consumption: `daemon/api.py:2736-2749` (`derive_metrics_port(config.daemon.port, …)` `:2738`; None ⇒ non-fatal disable `:2743-2749`).
- Pins: `f4157fbf0` covers metrics-derive (mutation-proven). Docs: `715f4eedc` (rollback_safe=fix-loss-only note).

This plan therefore does NOT re-plan the fence build. The rider below is the ONLY remainder, and it is cuttable by the leader with zero effect on §4.

### 5.1 H3 operator step (READ-ONLY — never a test, never this lane's mutation)

Analysis §8-H3 ("the DB-managed metrics-port tier's live state is unverified") is still open in its POST-retirement form: does an ORPHANED `app_settings` row for the retired key exist on live?

- Step: `SELECT key, value, updated_at FROM app_settings WHERE key = 'limits.metrics_listener_port';` against the LIVE database — READ-ONLY SELECT, single statement, no transaction, no DELETE in this lane (the DELETE guidance lives in the in-code comment and is operator-owned, dev/fenced DBs only).
- Fencing: per-variable PG scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT` individually — `env -u POSTGRES_*` is a silent no-op, bash does not glob env names), explicit read-only connection coordinates for the live DB, `umask 077` for any scratch credentials file, no live mutation of any kind.
- Interpretation: row ABSENT ⇒ clean (nothing to do). Row PRESENT ⇒ the retired key's row is inert by construction (registry entry removed — the boot-apply ladder no longer reads it), but its presence means an old DB tier once won; report the finding to the leader and let the OPERATOR decide on cleanup (never this lane).
- Timing: leader's call (§11-D5) — executable any time after this plan is adjudicated; independent of §4's build.

---

## 6. Rules & Constraints (binding for the developer lane)

1. **Design only for this worker** — this plan is the artifact; the developer lane implements §8.
2. **Worktree discipline**: all reads/writes in `/home/nea/ensemble-src-wt-rogue-contain`; NEVER touch `/home/nea/ensemble-src` (canonical checkout — detached HEAD + dirty; writes forbidden).
3. **No live mutation anywhere**: no connections to `ensemble_prod`, no DELETE/UPDATE on live, no state.json promote-field changes, NO promote (the Emperor's ceremony owns promotes).
4. **launcher.sh / systemd unit template / live `.env`: ZERO touches** (analysis §6 binding principle).
5. **No new exit codes**; 78 only, via the shipped refuse track. 75 stays budget-exempt-tempfail; everything else stays crash.
6. **No pkill**; no `rm -rf` outside tmp_path fixtures; no background-process kills.
7. **No bare `npx`/`tsc`** — TypeScript only via `frontend/`'s local devDependency (not expected in this backend-only lane; stated for completeness).
8. **Test foot-guns (each one is a prior incident)**:
   - Never bind or reference ports 9797 / 9798 / 7979 in tests.
   - Any boot-shaped test pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` EXPLICITLY (`monkeypatch.setenv`) — never `env -u` on it (stripping re-arms the sweep; the sweep kills live processes).
   - FAKE_HOME fence: `mktemp -d` HOME for anything HOME-derived — never `/home/nea` (2 live-install deletions on 2026-10-12).
   - Per-variable PG scrub in any env-touching harness: `POSTGRES_DB/USER/PASSWORD/HOST/PORT` + `DATABASE_URL/PGHOST/PGPORT/PGUSER/PGPASSWORD` individually.
   - `umask 077` for any scratch file carrying credentials.
   - Scratch DB coordinates only: names like `ens_e2e_scrub_probe` / `PG_TEST_*` exemptions; the predicate pins NEVER point at a real DB (refusal fires pre-`create_engine` — extend the `:58-75` sentinel pattern to the new check).
9. **F7**: refusal logs name var NAMES / labels only — never ambient var VALUES (the db name + self-env label excepted per §4.8 — they are the reason, not secrets).
10. **Pins are mutation-proven or they don't count** (house mutation discipline): every §7 pin names its mutant and must REDDEN under it; absence-only and raw-text pins are prose-satisfiable and rejected.

---

## 7. Pin Skeleton (mutation discipline)

Extend `tests/unit/test_db_resolution_guard.py` + new file `tests/unit/test_prod_dsn_fence.py`. Every pin: positive shape, named mutant, RED-leg evidence recorded. Identity is monkeypatched (`monkeypatch.setattr("daemon.tools.upgrade_tools._self_env_marker", lambda: "sandbox")` etc.) — no filesystem/live dependence; scratch json in `tmp_path`; per-variable PG scrub per §6.

| # | Pin (positive shape) | Named mutant it must redden under | File |
|---|---|---|---|
| P0 | `test_guard_does_not_fire_tier1` (`:30-37`) survives UNCHANGED as a SQLITE case; its comment/docstring is re-scoped to say the no-fire claim holds for non-prod resolutions only (ledger agreement, §4.2) | (not a behavioral pin — ledger edit; the behavioral replacement is P1) | existing file |
| P1 | tier-1 json `{"database":"postgres","postgres":{"db":"ensemble_prod",…}}` + identity `sandbox`/`dev`/`demo` → `SystemExit` code 78 from `assert_prod_dsn_sanctioned` | delete/invert the predicate branch → green-where-should-red; invert identity comparison → false pass | new file |
| P2 | EXACT-match discriminator: same json with `db="ensemble_prod_test"`, `db="ensemble_dev"`, `db="ens_e2e_scrub_probe"` → NO raise (sanctioned-scratch-boot-still-works; §4.4) | mutant predicate `startswith("ensemble_prod")` or `db in FORBIDDEN_DB_NAMES` → RED (kills the sloppy-pattern design) | new file |
| P3 | ENV-WINS closure: tier-1 json `db="ensemble"` (safe name) + `monkeypatch.setenv("POSTGRES_DB","ensemble_prod")` → 78; same with `POSTGRES_DB="ensemble_dev"` → no raise | predicate reads only `config.postgres.db` (file tier) → RED on the first leg | new file |
| P4 | SANCTIONED-LIVE-BOOT-STILL-WORKS: prod DSN + identity `live` (explicit and derived labels both) → proceeds, no raise, INFO logged | identity check removed or inverted → this pin REDDENS as a false refusal (false-positive guard — the cardinal sin this design must never commit) | new file |
| P5 | identity `None` → proceeds + WARNING in caplog (degrade polarity, §4.3) | mutant `None → refuse` (red as false refusal) or `None → silent proceed` (no WARNING) → RED | new file |
| P6 | identity resolver raising → 78 + "UNVERIFIABLE" in log (mirror `:195-206` polarity) | `except → pass` (fail-open) → RED | new file |
| P7 | ALLOW-var non-exemption (aggravator 2): json ABSENT + ambient `POSTGRES_HOST/DB`(prod name) + `ENSEMBLE_DB_AMBIENT_ALLOW=1` → tier-2 pre-guard passes, then `assert_prod_dsn_sanctioned` on the loaded config → 78 | mutant where `ALLOW=1` short-circuits the prod check → RED | new file |
| P8 | Ordering: in `_boot_db_preflight` source, `assert_prod_dsn_sanctioned` appears AFTER `load_or_create` and BEFORE the connectivity `create_engine` probe; same-before-engine in `api.py` lifespan after `:754` (structural source pins, `:123-133` pattern; the existing `guard < load` assertions stay green) | reordering mutant → RED | new file |
| P9 | CLI closure: `resolve_engine` source calls `assert_db_resolution_sanctioned` BEFORE `load_or_create` and the prod check after (structural); behavioral: CLI-shaped resolve with prod ambient + sandbox identity → 78, and NO `ensemble.json` created in the tmp data dir (anti-poison assertion, §4.7) | exemption restored (checks deleted) → RED | new file |
| P10 | Refuse-path engine sentinel extended: on ANY `assert_prod_dsn_sanctioned` refusal, `sqlalchemy.create_engine` is never constructed (extend the `:58-75` sentinel pattern) | refusal moved after engine creation → RED | new file |

RED-leg safety (non-negotiable): every RED leg uses scratch coordinates (§6.8); no RED leg ever points at a live-shaped DB; the RED evidence (mutant → observed failure) is recorded in the test docstrings per house practice (`f4157fbf0` style).

---

## 8. Task Breakdown (developer-executable, ordered, single-owner)

| # | Task | Depends on | Acceptance (testable) |
|---|---|---|---|
| T1 | `daemon/ensemble_config.py`: add `resolved_postgres_db_name()` + `assert_prod_dsn_sanctioned()` (§4.2-§4.4, §4.8: exact match, identity carve-out per §4.3 table, fail-closed mirror `:195-206`, marker text per §4.8); rewrite docstring claims `:152` (tier-1 semantics) and `:145` (exemption sentence) | none | unit pins P1-P7, P10 green; guard's existing 10 tests still green |
| T2 | `daemon/__main__.py` `_boot_db_preflight`: insert `if ensemble_config.is_postgres: assert_prod_dsn_sanctioned(ensemble_config)` AFTER `load_or_create` (`:213`)/`is_postgres` branch and BEFORE the connectivity probe's engine construction | T1 | pin P8 (main) green; a preflight-level refusal test (sandbox identity + prod json via `ENSEMBLE_DATA_DIR` tmp dir) exits 78 before any TCP |
| T3 | `daemon/api.py` lifespan: same insertion immediately after `EnsembleConfig.load_or_create(data_dir)` (`:754`), before `app.state.ensemble_config` | T1 | pin P8 (api) green; existing structural pins `:123-133`, `:136-145` still green |
| T4 | `daemon/auth/cli.py` `resolve_engine`: tier-2 guard call BEFORE `:150`; prod check after load when postgres; docstring `:145` updated (done in T1) | T1 | pin P9 green (structural + behavioral + no-poison-artifact assertion) |
| T5 | Pins: implement §7 (P0 ledger edit, P1-P10), RED legs executed and recorded | T1-T4 | every pin demonstrated RED under its named mutant, then GREEN on the real code; full `test_db_resolution_guard.py` + new file pass |
| T6 | Rider H3 operator step packaged: the §5.1 SELECT + fencing instructions appended to this plan's execution notes for the leader/operator (NOT executed by the build lane) | none (independent) | step present, fencing complete, marked READ-ONLY/operator-owned |
| T7 | Boot smoke (fenced, scratch): one tmp-data-dir boot with prod json + sandbox identity → observes exit 78 + marker; one with dev/scratch name → boots to the normal failure point (no DB) with NO refusal | T2-T5 | both observations recorded in the lane's evidence notes; sweep var pinned `=0` explicitly; no live contact |

Sequencing note: T1 is the sole owner of `ensemble_config.py`; T2/T3/T4 are sole owners of their respective files (no shared-file ownership conflicts with parallel p2 workers — this plan's files are disjoint from the A-worker's port-gate files).

---

## 9. Coupling

### 9.1 With component A (bind-time socket-holder identity, parallel worker)

- **Ordering**: DB preflight (`__main__.py:211-216`) runs BEFORE the port-ownership gate (`:450-457`) on every boot. **PRESERVE.** Rationale: the DB fence refuses before ANY socket activity (no bind attempt, no claim write, no TCP) — cheaper, earlier, and a prod-targeting rogue should stop at the DB gate, not after acquiring a port claim. If both fences refuse the same boot, the DB refusal fires first and the boot dies with the DB marker; the port gate never runs. No ordering change is needed — this plan explicitly preserves the shipped layout.
- **Marker namespace**: distinct refusal reasons in distinct channels (port gate: durable JSON file + `surface_refusal_marker`; DB fence: log marker only, §4.8) — no file collisions possible.
- **Identity machinery shared**: both consume `upgrade_tools` identity (`port_ownership.py:207-214`; this fence §4.3). A change to `_self_env_marker` semantics affects both — any such change is a cross-component event for the leader to adjudicate (§11-D6 adjacent).

### 9.2 With the phase-1 shipped build

- The tier-2 guard and its pins are load-bearing and stay byte-identical in behavior; only docstring claims change (T1) and the fence-var loses prod-exemption power by ADDITION of an independent gate (P7 pins the composition).
- `spawn_env_policy` untouched: the scrub stays the agent-boundary layer; this fence is the boot-path layer (§4.6 — complementary, neither substitutes for the other).

### 9.3 With the leader finalize flow

- Per the lane's shared-Meta-KV finalize rulings: evidence branch PUSHES as audit trail, explicitly never-merge; NO promote; NO state.json promote-field mutation; live untouched. This component plans code + pins only — nothing in it requires a promote to be verified (all pins are offline unit/structural).

---

## 10. Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | Same-host identity false-live: a rogue with ambient `POSTGRES_DB=ensemble_prod` on the live host derives `live` via the `.env` cross-check and passes the carve-out | High (prod attachment survives) | Low (requires same host + ambient prod env + prod-targeting resolution — the rare deliberate-impersonation corner) | Declared residual (§4.3), docstring-documented; boundary = uid + DB credential (same accepted boundary as port-ownership claims); H1-analog. A stronger process-scoped attestation is a future lane, not this one |
| R2 | None-identity degrade residual: non-frozen, marker-less, topology-less process + copied prod json passes | High | Low (the copied-json class; tier-2 already refuses the json-absent variant) | Declared hole (§4.3); leader may flip polarity (§11-D1); component A narrows the same-host port half; WARNING is loud enough to surface in any incident review |
| R3 | Predicate false-positive breaks test/dev lanes (`ensemble_prod_test`, `ensemble_dev`, scratch names) | High (lane outage) | Low (exact-match design) | P2 counter-mutation pin kills the sloppy-pattern mutant BY CONSTRUCTION; scratch-name pin P2 third leg is the explicit sanctioned-scratch-still-works proof |
| R4 | False refusal of the LIVE boot (cardinal sin) | Critical | Very Low (live always resolves live: marker, own-DB, or `.env` cross-check — three independent signals, `upgrade_tools.py:494-546`) | P4 false-positive pin; degrade-on-None chosen over refuse-on-None precisely so a degraded-evidence live boot is never fenced; promote lane derives live without executor changes (§4.3) |
| R5 | Docstring/pin churn: the `:152` flip surprises prose that repeated "tier 1 ⇒ sanctioned" elsewhere | Low | Medium (house has prose pins) | T1 greps the repo for the claim during implementation; P0 re-scopes the surviving pin's comment; the behavioral pins are unaffected |
| R6 | CLI closure breaks an operator workflow in a degraded-identity shell | Medium (operator blocked during incident response — the CLI is exactly the incident tool) | Low | Escape hatch documented: `ENSEMBLE_SELF_ENV=live` explicit marker wins (§4.7); refusal text names the hatch (§4.8 marker) |
| R7 | Impostor-HOST poison out of scope: ambient `POSTGRES_HOST` re-point + non-prod db name | Medium (data exfil to attacker-controlled host) | Very Low | Out of scope by design (§2); declared here so it is a conscious gap, not an oversight |
| R8 | Guard cost on the boot path (identity resolution reads `.env` files) | Negligible | Certain (one-time, two small file reads) | Accepted — boot-path-only, no hot loop |

---

## 11. Leader Decision Points (mapped to analysis §9)

| # | Decision | Options | This plan's recommendation |
|---|---|---|---|
| D1 | None-identity polarity on a prod-resolved DSN (§4.3) | (a) degrade-never-refuse [commission default, recommended]; (b) refuse-on-None (N5-aligned fail-closed) | (a) — with R2 declared; revisit after component A lands. If (b): P5 flips (None → 78) and R4's degraded-live exposure is accepted by the leader explicitly |
| D2 | Predicate scope (§4.4) | (a) exact `ensemble_prod` only [recommended]; (b) widen toward the harness forbidden set for NON-dev-labeled boots | (a) — widening breaks sanctioned dev/test lanes for no incident-class gain; the harness already fences harness boots |
| D3 | CLI exemption closure (§4.7) | (a) close [recommended]; (b) keep documented-exemption | (a) — the CLI's auto-persist makes the exemption an active poisoner; escape hatch exists |
| D4 | Operator alert channel for refusal paths (analysis §9-D4 — lane-wide) | one daemon-side alert for BOTH refusal families (port + DB) | defer to the lane-wide decision already pending for (f); this fence emits the same log-marker style so any chosen channel can subscribe uniformly. The DB fence should NOT ship its own separate channel |
| D5 | Rider H3 execution timing (§5.1) | now, post-build, or fold into the next live maintenance window | leader's call; step is read-only and independent of the build |
| D6 | Promote-lane attestation hardening | (a) rely on auto-derive (recommended — no executor change); (b) add `ENSEMBLE_SELF_ENV` to `EXECUTOR_ENV_ALLOWLIST` | (a) — the executor boots derive live via install topology + forwarded `POSTGRES_DB` with zero seam changes; (b) touches the promote executor seam (`upgrade_journal` deliberately not a scrub consumer) for belt-and-braces only — not worth the ceremony-adjacent blast radius this cycle |

---

## 12. Contradictions (known + discovered during verification)

- **C1 — drifted citation**: the commission cites `daemon/config.py:795-798` for the metrics knobs; at tip `bf4bfde6e` the `metrics_listener_port` Field sits at `:821-828` (the `:795-798` range is the section comment boundary). Plan cites verified lines. Correction carried per house correction-carry-through discipline.
- **C2 — rider already shipped**: the commission's rider text implies the 9798 fence is unbuilt; it SHIPPED in phase 4 (`1ec2afb1b` + pins `f4157fbf0` + docs `715f4eedc`). This plan reduces the rider to the H3 verification remainder (§5). The leader should treat the rider section as status-confirmation + H3 packaging, not as new build scope.
- **C3 — the "pin flip" is a docstring flip, not a test deletion**: the commission states a guard pin "explicitly pins that the guard does NOT fire in the tier-1/json-present branch" and that this build FLIPS it. Verified precisely: `test_guard_does_not_fire_tier1` (`:30-37`) uses a SQLITE json, so it survives the build UNCHANGED as a behavioral test; what flips is (i) the guard docstring's universal claim (`:152` "Tier 1 (json exists) => does not fire") and (ii) the previously-unpinned tier-1 POSTGRES+prod shape, which now fires via the new check (pinned by P1). The plan implements the flip as a ledger re-scope (P0) + new positive pins — body and ledger agree.
- **C3b — guard docstring `:145` vs T4**: the shipped docstring declares the CLI exemption "no code change there"; this plan changes exactly that. Both sides (docstring + CLI) are updated in T1/T4 as one owner pair.
- **C4 — analysis silence**: the prod-NAME predicate and the identity carve-out are post-analysis additions (the analysis governs mechanics/exit-code/placement). This plan anchors them on in-repo precedent (§3) and flags the delta here rather than silently extending the analysis.

---

## 13. Migration Expectation

**CODE-ONLY, zero migrations — this plan AGREES with the leader.** The fence reads `ensemble.json` + env + identity machinery; it adds no table, no column, no registry entry (the rider's managed-key retirement already shipped without a migration — the `app_settings` schema is untouched; the orphaned-row SELECT in §5.1 is a read). No `daemon/migrations/versions/` file is created (the 122-file audit trail stands; the runner remains SQLite-only and this lane touches no models).

---

## 14. Cross-References

- Governing analysis: `.agents/shared/planning/bind-race/rogue-port-containment-analysis.md` (§5.2, §5.3, §5.5, §6, §7.3, §8-H1/H3, §9-2/3/4)
- Prior lane component (house style + phase-1 shape): `.agents/shared/planning/rogue-containment/component-B-spawn-boundary-scrub.md`
- Primary code: `daemon/ensemble_config.py` (`:71-76`, `:82-92`, `:114-130`, `:138-206`), `daemon/__main__.py` (`:211-216`, `:450-457`), `daemon/api.py` (`:727-742`, `:752-754`, `:2736-2749`), `daemon/auth/cli.py` (`:126-163`, `:265`)
- Identity machinery: `daemon/tools/upgrade_tools.py` (`:254-261`, `:373`, `:441-562`, `:566-606`, `:666-688`); consumed by `daemon/services/port_ownership.py:207-214`
- Phase-1 shipped surfaces: `daemon/services/spawn_env_policy.py` (12-name strip set + PG prefix family), `daemon/services/port_ownership.py` (claims + refusal marker `:60-61`, `:473`, `:535`, `:587`), `daemon/services/metrics_listener.py:75-142` (`derive_metrics_port`), `daemon/services/settings_service.py:241-246` (managed-key retirement)
- Harness fence precedent: `tests/postgres/conftest.py:136-142` (`FORBIDDEN_DB_NAMES`), `:215-233` (fail-closed harness gate)
- Pins: `tests/unit/test_db_resolution_guard.py` (10 tests; the P0/P8/P10 anchors); phase-1 pin commit `f4157fbf0`
- Sibling: component A (bind-time socket-holder identity) — p2 lane parallel worker; §9.1 is the binding contract between the two builds
