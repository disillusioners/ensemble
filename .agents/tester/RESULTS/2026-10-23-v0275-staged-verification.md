# Independent Staged Verification — v0.27.5 (ceremony-ready, NO promote)

- **Date**: 2026-10-23 (06:50–07:1x UTC) · **Tester**: independent staged-verification gate (final ceremony gate)
- **Staged artifact**: `/home/nea/agents-ensemble/releases/v0.27.5` · binary sha256 `26a56ea6a0be94f76cfa8445a2efed5226b9330943a8b21e3dfc5c8bcbf43135` (3-way disk==manifest==sidecar, independently recomputed)
- **Lineage**: tag `v0.27.5` (annotated `b1f05a883` → commit `6bebb9ba0`, LOCAL-ONLY, ls-remote empty) · merge `adb4c9efe` (parents `671560c3f` + `406697a0d`) · staging worktree `/home/nea/ensemble-src-wt-stage-v0275` (KEPT, sidecars intact)
- **Prior gates carried**: branch verified at `406697a0d` (G1–G7 incl. Lane B certification + granted cooldown fix — see `2026-10-23-wedge-capture-independent-verification.md` + addendum)
- **Fences**: `/home/nea/ensemble-src` never touched; live system read-only (asserted untouched, never modified); no stage.sh/promote; all dynamic work on scratch PG + `/tmp` scratch roots; per-variable env scrub everywhere (never the `POSTGRES_*` glob).

## Verdict summary

| Leg | Verdict |
|---|---|
| 1 Artifact integrity (sha 3-way, sidecars, counts, tag, porcelain) | **PASS** (1 benign deviation: staging worktree HEAD — below) |
| 2 Payload (source-less, migration census 109/109, PROMPT-BASELINE, seal) | **PASS** |
| 3 Migration behavior (scratch PG, additive + idempotent + non-destructive) | **PASS** |
| 4 Staged-binary boot smoke (zero-live-contact, fenced) | **PASS — EXECUTED, skip clause never invoked** |

**FINAL: GO for the promote ceremony** (user 3-factor nonce gate owns the flip).

## Leg 1 — Artifact integrity: PASS

- 3-way sha: disk `26a56ea6…43135` == manifest `binary_sha256` == binary sidecar; `binary_version 0.27.5`, `version v0.27.5`, `known_schema_gen 20261022_000002_create_wedge_captures.sql`, `rollback_safe true`.
- BOTH provenance sidecars (binary `dist/ensemble-prod.build-provenance.json`; FE at `frontend/dist/frontend/browser/index.html.build-provenance.json`): `git_head=6bebb9ba0e9b05e99e8b64aa99398cbf423c159f`, `git_dirty=false`; FE sidecar sha `5531cbe4…1bb0ddd` matches manifest `fe_index` record and the on-disk worktree build output (re-hashed).
- Manifest counts == disk counts: agents **339/339**, frontend **93/93**, plugins **4930/4930**.
- Tag: `v0.27.5^{commit}` → `6bebb9ba0` (annotated object `b1f05a883`, tagger giter); `git ls-remote origin refs/tags/v0.27.5` → **EMPTY** (local-only).
- Staging worktree porcelain: **EMPTY** (zero delta). ⚠️ **Benign deviation**: worktree HEAD is `f19587a4a`, not the tag commit — proven a docs-only descendant (`merge-base --is-ancestor 6bebb9ba0 HEAD` YES; delta = exactly 3 files, all `.agents/tester/**`, the tester evidence merge; **zero shipped-tree delta** for agents/frontend/daemon/plugins/scripts). Matches the staging lane's pre-documented tip-drift adjudication (PROMOTE STAGED AS-IS; a re-stage would be REFUSED by L1 tip-identity, exit 78, and is NOT needed). Provenance unaffected.

## Leg 2 — Payload: PASS

- Source-less shape: release dir contains exactly `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/` — no scripts/, no daemon/, no *.sql, no .env, no sidecars in payload.
- Migration census (from the shipped CArchive outer TOC, staging-worktree venv): **109 entries / 109 distinct prefixes / zero duplicates**; `20261022_000002_create_wedge_captures.sql` present; extracted content carries `capture_id TEXT NOT NULL UNIQUE` + `CREATE UNIQUE INDEX IF NOT EXISTS idx_wedge_captures_capture_dir` + `CREATE TABLE IF NOT EXISTS` (additive).
- PROMPT-BASELINE: `generate_prompt_artifact_baseline.py --tree releases/v0.27.5 --check` → **rc=0, 161/161 OK**.
- Seal guard at the staged tree: tripwire node `1 passed` (0.61s) + PACKS-named sibling audit `46 passed` rc=0 — **zero stale**. Ambient-env audit: exactly 7 ambient vars, all covered by the per-var `-u` scrub list.

## Leg 3 — Migration behavior (scratch PG): PASS

- Mechanism (house reality confirmed): **SQLModel ORM `create_all` on PG boot**; migration runner logs `Skipping migrations for non-SQLite database (schema evolution handled by EnsembleManager._ensure_postgres_columns)`; `schema_migrations` = 0 rows (board-tables precedent shape).
- `wedge_captures` created: `capture_id` UNIQUE (`wedge_captures_capture_id_key`), `idx_wedge_captures_capture_dir` UNIQUE, `metrics_snapshot jsonb` (model shape; the .sql's SQLite-face `json` + record note verified in-archive at leg 2).
- Boot 1 created 80 tables; **boot 2 on the same scratch DB: census identical (80), zero DDL errors, the boot-1 manual-capture row PRESERVED (count==1, same capture_id)** — additive, idempotent, non-destructive.

## Leg 4 — Staged-binary boot smoke (fenced): PASS — EXECUTED with ZERO live contact

Structural isolation: byte-identical binary copy (sha re-verified `26a56ea6…`) executed from a `/tmp` scratch root — never from inside the install tree; fake HOME, throwaway CWD, scratch PG (`wedge_v5_1792738653`, dropped with proof 0/0), scratch port 18097, per-var scrub incl. `ENSEMBLE_UPGRADE_*`; systemd-run transient units (`wedge-v5-smoke-*-b1/-b2`).

**Live-fence assertions — before | after, ALL MATCH (verbatim):**
- `state.json` sha256 `8e39b13b…292d26` | identical; stat `36797 2026-10-23 06:25:16.587204942` | identical (no write)
- current pointer `releases/v0.27.4` | unchanged (note: the pointer is `/home/agents-ensemble`-root `current`; no `releases/current` exists — premise corrected)
- Live daemon `MainPID=3522801 NRestarts=0`, livez `version 0.27.4` | identical, same process continuous (uptime 2512s → 3319s)
- Journal `history_len=144`, `pending_op=null` | identical — **ZERO new rows** (the v0.27.1 incident vector)
- v0.27.5 payload stat (ensemble-prod `80743608 @06:26:09`, manifest `733737 @06:33:09`) | identical; `find releases/v0.27.5 -newer <marker>` → empty; in-place binary sha re-verified

**Hazard behaviorally dead**: the upgrade-journal sweep bound the SCRATCH journal — `JournalTorn: journal absent at /tmp/wedge-v0275-smoke.*/releases/state.json` (+ `close_stale_restart_arm … leaving any stale arm untouched`); engine `127.0.0.1:5432/wedge_v5_…`; `pg_stat_activity` census: scratch role 8 connections, ALL to the scratch DB, **0 to ensemble_prod/ensemble_dev**; cgroup `/user.slice/.../wedge-v5-smoke-*-b1.service` — NOT `/system.slice/ensemble-main.service`.

**Boot evidence (staged binary)**: banner `Starting Ensemble v0.27.5` (×2 startup sites) · livez `[200] version 0.27.5` · readyz `[200] all components true, draining false` · **watchdog ALIVE**: `/api/wedge-captures/status` → `enabled/running/started_at_monotonic`, `capture_dir` under the SCRATCH data root, `pool_counters_source=psycopg_pool.get_stats` with live pool counters, `boot_epoch` set · **manual capture end-to-end**: `POST /capture → 202 {"capture_id":"20261023T070350Z-…-ee859332","reason":"manual","enqueued":true}` → serviced on the watchdog's FIRST tick → 7 artifacts + manifest (`trigger_source:"manual"`, `executor_native_id` — the AC27 manifest additions live in the frozen binary) → `SELECT count(*) FROM wedge_captures = 1`, row `trigger_reason=manual, release_version=0.27.5`. Boot 2: banner + livez 200 + watchdog re-armed with NEW boot_epoch; graceful SIGTERM shutdowns both (`Database engine disposed` / `Graceful shutdown complete`).

**Teardown**: transient units reaped (list-units empty), port 18097 freed, scratch DB+role DROPPED (pg_database/pg_roles counts 0/0), scratch root removed after evidence capture.

## Disclosures (read before the nonce echo)

1. **Staging worktree tip drift** (leg 1): HEAD `f19587a4a` is a docs-only descendant of tag commit `6bebb9ba0` — zero shipped-tree impact; PROMOTE STAGED AS-IS (re-stage would be refused by L1 and is unnecessary).
2. **Discovery pre-run (06:58–07:02Z)**: an initial launch attempt bound port **8088** because the shipped `config.yaml` interpolates `${PORT:-8088}` (NOT `DAEMON_PORT`) on this path. It ran fully scratch-isolated (its own sweep log proves it) and was gracefully stopped before the verdict-bearing boots. **Runbook note for any future staged smoke: set `PORT` explicitly.** No damage: the self-system remained alive throughout (this report chain itself is the evidence).
3. **Leftover staging temp observed (read-only)**: `releases/.staging.v0.27.5.3526836` (mtime 06:22) exists on disk, while the staging lane's record claims "no partial dir" for its SIGTERM'd second attempt. Ownership stays with the giter lane; cosmetic, outside the payload, does not affect integrity.
4. Scratch-context warnings (expected, non-fatal): skill-bank seeding miss under the binary-only scratch root; `ViewPasscodeCipherUnavailable` (SYSTEM_ENCRYPTION_KEY deliberately absent from scratch); MCP warmup noise.
5. Rollback posture: additive-only delta vs live v0.27.4 (+1 migration, CREATE TABLE/INDEX IF NOT EXISTS, DOWN neutralized non-executable); `rollback_safe=true` in manifest; the v0.27.4 binary never references `wedge_captures` → inert on rollback (standing additive reasoning class).

## Promote command (ceremony, env-first, verbatim)

```
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.27.5 bash scripts/upgrade/promote.sh live --f2-verified-closed
```

(User 3-factor nonce ceremony owns the flip; SC-15 stage-time gates were the giter lane's S-5 and passed at stage time.)

*Evidence: this file, docs-only commit on `tester/wedge-capture-verification` (NOT merged; feature branch `feature/wedge-capture-watchdog @ 406697a0d` and the staged artifact untouched by this gate).*
