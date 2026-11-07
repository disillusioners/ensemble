# POK Phase-3 — llmproxy seed payload: table + insert receipts (dev)

**Status:** dev-proven on `ensemble_dev` — **LIVE-GATED** (Phase-1.5 live replay is leader-owned, Emperor promote gate; the re-runnable script is `scripts/pok/seed_llmproxy_payload.py` + `scripts/pok/payload_llmproxy.py`, commit `739edbe9a`).
**Write path:** the REAL `pok_upsert` path — `POKService.upsert` → `POKRepository` from the worktree tree against `ensemble_dev` (fence, escape-aware budget, attribution, append-with-supersede all live). NOT SQL backfill.
**Attribution:** `agent_id='leader'` (plan §4.5 "leader-curated"; also enables the `pok_curate` pin). `provenance='observed'` on every entry.
**Run discipline:** per-variable `env -u POSTGRES_*` scrub (ambient shell carries PROD vars — anomaly 101a02e4), worktree venv, `current_database()`/`current_user()` asserted == `ensemble_dev_app@ensemble_dev` BEFORE any write (caught clean both runs).

## ⚠️ FLAG — dev project row was absent and was CREATED

`ensemble_dev` held NO `llmproxy` row (only `__system_default__`; `critical_notes` empty). Created via the REAL `SQLModelProjectRepository.create` path per plan §4.5b's convention:

| field | value |
|---|---|
| project_id | `3f401aa9-0775-4501-84be-0f741ed33677` |
| name | `llmproxy` |
| main_directory | `/home/nea/Code/opensource-projects/llm-supervisor-proxy` (verified existing on disk) |
| project_type | `general` |
| description | "llm-supervisor-proxy — POK P-1 dev row (Phase-3 seeding); live row lands in Phase 1.5 (leader-gated)." |
| creator_agent_id | `leader` (the commissioning curator-of-record; the working-lead coder lane executed the insert) |
| created_at | 2026-11-07T11:44:14Z (run 1) |

## The 8-slug payload (§11/§4.5 verbatim slugs · domains · audiences)

| # | slug | family | domain | audience | body (chars) | body source note(s) |
|---|------|--------|--------|----------|--------------|---------------------|
| 1 | `artifact-is-container-image` | (unordered) | artifact | both | Artifact is a container image; identity is its digest plus push timestamp, not a build sha. (91c) | 68a80f90-detail |
| 2 | `pipeline-01-merge-master-triggers-image-ci` | pipeline-01 | deploy | both | Master pushes build the image (paths filter, workflow_dispatch); version = CI run number. (89c) | b82409f4 + 1e5a371f |
| 3 | `pipeline-02-verify-image-tag-digest-feature` | pipeline-02 | deploy | operation | Post-build, verify the deployed tag resolves to the built digest; only then is it shipped. (90c) | 68a80f90-detail + f7511668 |
| 4 | `pipeline-03-deploy-order-daoduc-backup-first` | pipeline-03 | deploy | operation | Deploy order: backup daoduc rolls first (Helm), gate on health, then ensem primary updates. (91c) | 6dcc0ed9 |
| 5 | `deploy-01-daoduc-backup` | deploy-01 | environment | operation | The backup deployment is a Helm release; rollouts and rollbacks move by Helm revision number. (93c) | 68a80f90-detail + 6dcc0ed9 |
| 6 | `deploy-02-ensem-dev-primary` | deploy-02 | environment | operation | The primary deployment is plain kubectl apply; no Helm — roll forward by applying manifests. (92c) | 68a80f90-detail |
| 7 | `healthz-in-cluster-only` | (unordered) | verify | both | Health answers only in-cluster; an outside probe gets 404 — false-negative, not an outage. (90c) | 68a80f90-detail |
| 8 | `model-catalogs-19-vs-22-not-drift` | (unordered) | catalog | operation | Model catalogs differ per environment (19 vs 16); a count difference is config, not drift. (90c) | 14b4bbdc |

All bodies: single paragraph, fence-clean (no newline / leading `#` / `[SYSTEM CONTEXT:` / backticks), escape-aware budget ≤240 trivially (no `&`/`<`/`>` in any body), §11 bound 54–93c held (89–93c). Families: `pipeline` 01→02→03 ordered spine; `deploy` 01/02 ordered atomic contrast pair; 1/7/8 unordered. Views (ER-1): ari/leader/devops → 8; developer → 3 (rows 1, 2, 7).

## Insert receipts — run 1 (2026-11-07T11:44:14Z, action=created × 8)

| slug | entry id (uuid4) | audience | tier | created_at (UTC) |
|---|---|---|---|---|
| artifact-is-container-image | `a57ed63a-832e-46e3-859f-3ab6ed6100e0` | both | agent | 11:44:14.351002 |
| pipeline-01-merge-master-triggers-image-ci | `fc1c77a6-5d8d-4a51-b1a2-6432de560d7e` | both | agent | 11:44:14.389683 |
| pipeline-02-verify-image-tag-digest-feature | `113b2917-52d1-471e-8123-54bc6d239188` | operation | agent | 11:44:14.414935 |
| pipeline-03-deploy-order-daoduc-backup-first | `33ca12cb-1be3-4f08-bb76-531f02129fcf` | operation | agent | 11:44:14.439227 |
| deploy-01-daoduc-backup | `f1185763-7fa0-4add-bd2a-aaba15dd1544` | operation | agent | 11:44:14.465106 |
| deploy-02-ensem-dev-primary | `fb820947-295b-4880-b4ff-7e01952cc2ca` | operation | agent | 11:44:14.490917 |
| healthz-in-cluster-only | `411d4441-c8ef-49ec-ad6d-02f278be6217` | both | agent | 11:44:14.513226 |
| model-catalogs-19-vs-22-not-drift | `cba42550-56bc-432d-a3be-63cb9af60bd8` | operation | agent | 11:44:14.535288 |

**Total: 8 active entries** (store-wide `count(*) = 8`), attribution `leader` on every row, `provenance=observed`, `source_tier=agent` (Phase-2 default — tier statements are curator calls and none were made at capture), `status=active`.

## Pin receipt — `pok_curate` (§4.5 row 4)

| field | value |
|---|---|
| slug | `pipeline-03-deploy-order-daoduc-backup-first` |
| entry id | `33ca12cb-1be3-4f08-bb76-531f02129fcf` |
| flip | `pinned: false → true` via the real `POKService.curate` path (leader gate passed, attribution `leader`) |
| original pin | run 1, ≈2026-11-07T11:44:14Z (in-place curate — no history row) |
| re-assert (run 2) | `updated_at=2026-11-07T11:44:31.687282+00:00` — the standing pinned state |

## Idempotency proof — run 2 (re-runnable; Phase-1.5 replay depends on it)

Second run of the same script: project row looked up (never re-created), all 8 slugs echoed **`action=no-op`** (exact-dup, tier unchanged, zero new rows — entry ids byte-identical to run 1), pin re-asserted, post-write verify `8/8`. Append-with-supersede makes re-runs safe by construction.

## Schema materialization (dev)

`project_operating_knowledge` + `project_operating_knowledge_embeddings` did not yet exist in `ensemble_dev`; materialized the PG boot way (`daemon/manager.py`: register the POK models → `SQLModel.metadata.create_all`; the file-based migration runner is deliberately a NO-OP on non-SQLite engines). Both `to_regclass`-verified post-create. Additive-only, rollback-inert posture per the migration headers; no `schema_migrations` ledger rows (PG path never runs the runner).
