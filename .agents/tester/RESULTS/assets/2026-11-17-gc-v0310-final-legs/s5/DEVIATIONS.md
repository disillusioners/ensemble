# S5 SCRATCH-SANDBOX BOOT SMOKE — DEVIATION LEDGER (v0.31.0 staged payload)

**Leg:** S5 (scratch-sandbox boot smoke) · **Payload:** `/home/nea/agents-ensemble/releases/v0.31.0`
(binary sha256 `5473a25f759ec6ef21fd85ebfb76939658ff4fa4fe9b73e07870ce17d9514743`)
**Date:** 2026-11-17 · **Evidence:** `/tmp/gc_v0310_smoke_evidence.dCM1Wx`

## A. 🔴 PLAN-DEFECT FINDINGS (the plan text must be amended before the ceremony)

**PD-1 — §5.4.3's recipe is NOT EXECUTABLE against the v0.31.0 payload (exit 78, pre-TCP).**
Run verbatim, it dies at the payload's own DB-resolution guard:

```
DB RESOLUTION REFUSED (rogue-containment): tier=ambient (ensemble.json absent) with ambient
POSTGRES_* present (...) and ENSEMBLE_DB_AMBIENT_ALLOW != 1. Refusing before any TCP
connection. Set ENSEMBLE_DB_AMBIENT_ALLOW=1 for a sanctioned first boot ...
```
`daemon/ensemble_config.py:186` (tier-2 ambient guard) — the plan's recipe sets
`POSTGRES_HOST`+`POSTGRES_DB` with a fresh empty `ENSEMBLE_DATA_DIR` (no `ensemble.json`),
which is exactly the refused combination. **Measured exit code: 78.**
`ENSEMBLE_DB_AMBIENT_ALLOW=1` grants ONLY the tier-2 sanction and is **never** consulted by
`assert_prod_dsn_sanctioned` (`daemon/ensemble_config.py:289-294`) → the prod fence stays armed.
The house harness template already sets it on every booting leg
(`tests/support/wedge_smoke.py:581`).

**PD-2 — the plan's scrub list omits the ambient `ENSEMBLE_AUTH_*` family → exit 78.**
With the ambient env inherited from the live daemon (7 `ENSEMBLE_AUTH_*` vars, `ENABLED=true`,
`EXTERNAL_REDIRECT_BASE=https://www.ensem.dev`), the auth preflight refuses the scratch bind
origin:
```
Auth config misconfig: auth.external_redirect_base must be same-origin as the daemon bind host
(external_redirect_base origin https://www.ensem.dev != bind origin http://127.0.0.1:19101)
→ exiting 78 (EX_CONFIG)
```
The payload's own `config.yaml` ships `auth.enabled: false`; the refusal comes only from the
leaked ambient family. Fix = scrub the ambient `ENSEMBLE_*` family (house doctrine: family-wide,
not enumerated).

**PD-3 — the plan's `timeout -k 5 30` bound CANNOT reach `livez`.**
`daemon/__main__.py:832-841` (source-verified): the socket is pre-bound but **not listening**
during the lifespan startup; a probe in that window gets `ECONNREFUSED`. The daemon's own
source documents a **~29 s lifespan startup** ⇒ a 30 s bound leaves no room for the probe.
Measured: `livez`/`readyz` first answered at **t = 22 s** in this environment, but only after
two config fences were cleared. **Recommended plan bound: ≥ 120 s.**

**PD-4 — the scratch-DB role cannot complete `create_all` in place (privilege wall).**
`ensemble_dev_scratch` holds 58 pre-existing tables, **55 owned by the superuser role
`ensemble`**; `ensemble_dev_app` owns only the 3 room tables. `create_all` aborts on
`CREATE TABLE project_future ... REFERENCES projects` → *permission denied for table projects*
(exit 3). `ALTER` is **not grantable** for tables, so the dev role cannot satisfy
`_ensure_postgres_columns` either (→ *must be owner of function job_queue_items_active_lock_guard*).
This matches the standing POK note *"no scratch DB ensemble_dev_app can own (rolcreatedb=FALSE),
project_future exits 3; needs superuser scratch"*.

## B. DISCLOSED DEVIATIONS APPLIED (each one recorded in the transcript)

| ID | Deviation | Rationale / safety |
|----|-----------|--------------------|
| D1 | `+ ENSEMBLE_DB_AMBIENT_ALLOW=1` | PD-1. Grants tier-2 only; prod-name fence unaffected (`ensemble_config.py:289`). |
| D2 | `+ family-wide ambient `ENSEMBLE_*` scrub (19 names in this env, incl. 7 `ENSEMBLE_AUTH_*`) | PD-2. Restores the payload's own `auth.enabled: false`; the ambient `ENSEMBLE_UPGRADE_SCRIPTS_DIR` (stale pin `bf4bfde6e`) is removed too. |
| D3 | bound `timeout -k 5 30` → `-k 5 180` | PD-3. Teardown semantics unchanged (hard bound + SIGKILL). |
| D4 | boot DB = `ensemble_dev_scratch_s5`, a **template clone** of `ensemble_dev_scratch` | PD-4. Same server, same role (`ensemble_dev_app`), same password, **same pre-P3/P5 room shape (10 cols)** so the `_ensure_postgres_columns` 10→14 upgrade path is still exercised — but the shared fixture `ensemble_dev_scratch` was **never a boot target and is byte-for-byte unchanged** (58 tables, 9/10/10 cols, owners `ensemble=55`/`dev=3`, 0 rows). |
| D5 | clone objects re-owned to `ensemble_dev_app` (58 tables + sequences + functions + views + types) | D4 enabler; applied to the CLONE only. The live `ensemble_prod` database was never in scope (it lives on the remote host `10.44.0.2`; the local cluster's `ensemble_prod` was never touched — no `REASSIGN OWNED`, no cross-DB ownership move). |
| D6 | live-unit read uses `systemctl show` (system manager), not `systemctl --user` | `ensemble-main.service` lives in the **SYSTEM** manager (`/system.slice/…`); the commissioned `--user` form returns `MainPID=0`/empty. Both forms were recorded. |
| D7 | termination by SIGTERM to the `systemd-run` client after the health evidence was captured | The 180 s bound had not expired (the daemon was serving). The daemon shut down **gracefully** ("Application shutdown complete", "Graceful shutdown complete"); no SIGKILL needed. |
| D8 | tool-vocab check executed against `agents/*/meta.json` + `daemon/tools/room.py` | The commissioned path `.agents/shared/metrics/tool-categories.json` **does not exist** in the payload; the category is registered in code and granted per-agent. |
| D9 | abort-path item 5 ("assert room tables absent from prod") satisfied BY CONSTRUCTION | No prod connection was ever possible: `POSTGRES_URL` scrubbed, `POSTGRES_DB` ≠ `ensemble_prod`, `POSTGRES_HOST=127.0.0.1` (prod is `10.44.0.2`), `ENSEMBLE_SELF_ENV=sandbox` (prod fence armed). The live prod credential was proven unusable against the local server (`FATAL: password authentication failed for user "ensemble"` → fails closed). |
