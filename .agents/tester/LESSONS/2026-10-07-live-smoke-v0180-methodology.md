# LESSONS: v0.18.0 live-smoke methodology (2026-10-07)

Reusable knowledge for future live-install smoke passes on promote. Sourced from workers bd1e68f8 / 5a25787c / b113b12d during the READ-ONLY v0.18.0 compensating smoke.

## 1. The live release is a PyInstaller binary — there is no plain `daemon/` tree
- `/home/nea/agents-ensemble/releases/vX.Y.Z/ensemble-prod` (stripped ELF, ~73MB). Packaged-code verification requires CArchive/PYZ extraction (worker pattern saved as skill `pyinstaller-release-readonly-forensics`, id ccaf2483; extraction to /tmp only).
- PYZ modules marshal-load only under the build's Python (3.14) — a 3.13 host cannot unmarshal the PYZ; CArchive-level SQL/migration files still extract fine.

## 2. Plugin registry is a FILESYSTEM boot-scan, not a DB table
- `PluginRegistry` = in-memory scan of `$CWD/plugins/<name>/MANIFEST.yaml` (`daemon.manager._bootstrap_plugin_registry`). The only plugin-subsystem DB object is `drift_events`.
- To verify plugin registration on live: check the `plugins/` dir exists + grep the boot log for `Plugin registry boot-scan: N plugin(s)`.
- **v0.18.0 payload contract ships NO `plugins/` tree** (manifest.json has no `plugins_tree_sha256`) → live installs register 0 plugins/0 plugin-skills. Future smokes must first confirm the payload contract includes the tree.

## 3. Migration ledger is a no-op on PG — verify schema by catalog, never by ledger
- `daemon/migrations/runner.py:721-726` skips `schema_migrations` for non-SQLite engines; PG schema comes from `SQLModel.metadata.create_all` + `_ensure_postgres_columns`. The ledger will be empty on PG — that is expected, not a failure.
- **Divergence trap (live case):** the model's index declarations did not match the migration's index names → `drift_events` exists on live PG with its PK but WITHOUT its 3 secondary indexes (`ix_drift_events_plugin`, `ix_drift_events_plugin_divergence`, `ix_drift_events_observed_at`). Always compare `pg_indexes` output against the migration's CREATE INDEX list; a migration header claiming "both dialects converge" is a claim, not evidence.

## 4. Live log slicing and clock facts
- `data/logs/ensemble.log` lines are `HH:MM:SS - logger - LEVEL - msg` — **no date prefix**; the file is continuous across boots. Slice by line anchors (boot markers like `Starting Ensemble v`, `Boot epoch captured`), never by time alone.
- Host timezone = **Etc/UTC**; DB clock == log clock == wall time.
- Boot-timeline reconstruction: `releases/state.json` `history` events (`supervision_boot`, `commit`) + `journalctl -u ensemble-main.service` + `/livez` `uptime_seconds` back-calculation — all three must agree. On 2026-10-07 the state.json `supervision_boot 12:34:59Z` belonged to a PRIOR session; the live daemon booted 13:22:26Z (flip commit 13:19:58Z with MANUAL RECOVERY stamp).

## 5. Access patterns (read-only discipline)
- Live HTTP: port **9797** (`PORT` in install `.env`; confirm with `ss -ltnp` — listener held by the uvicorn worker child, parent+child both `ensemble-prod`). Demo daemon is separate on `127.0.0.1:7979`. `/api/plugins*`, `/api/ports`, `/api/tools` return 404 — the plugin lane is agent-tool-lane only, no HTTP surface.
- Live DB: `POSTGRES_*` from `/home/nea/agents-ensemble/.env` → `ensemble_prod` @ 10.44.0.2. Use `PGOPTIONS='-c default_transaction_read_only=on' psql` + `timeout 60`; mask secrets.
- Port 8088 = ensemble self-system — never kill; also never kill anything during read-only smokes.

## 6. Known live findings carried into future runs (until fixed)
- `plugins/` tree absent from payload → 0 plugins on live; opendesign MCP tombstone `is_active=false` in `mcp_servers`.
- `drift_events` secondary indexes missing (see §3).
- Chronic `plane` MCP retry (48 ERROR pairs/~50 min, 2-min cadence, graceful fallback) — config entry persists post-retirement.
