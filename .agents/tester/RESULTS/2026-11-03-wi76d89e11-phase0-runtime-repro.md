# wi-76d89e11 — Phase 0 Runtime Repro (skill_list truncated id → skill_view refusal)

- **Date**: 2026-11-03
- **Lane**: skill-list-full-uuid — worktree `/home/nea/ensemble-src-wt-skill-list-full-uuid`, branch `fix/skill-list-full-uuid` @ base `c4161335b406b884d1de6c8e7bf4b3d1f7fe2abe` (== origin/latest at lane open)
- **Worker**: `e6ee309c-beb3-4f88-a703-eafeaf62aa56` (wi76d89e11-phase0-repro)
- **VERDICT: REPRODUCED-AT-BASE** — DB-sourced skills are listed under an 8-char prefix (`skill_id[:8]`); `skill_view(prefix)` refuses (`no skill with id`); `skill_view(full_uuid)` resolves. Project-file rows are NOT affected in the list→view roundtrip (they emit a consumable `pf:<ns>:<name>` address).
- **Role of this file**: RED-at-BASE anchor for the lane's later pin. Verbatim evidence preserved here because the scratch artifacts live in `/tmp` (`/tmp/wi-76d89e11-repro-1793726495/`) and may not survive a reboot.

## Worktree integrity

| | start | end |
|---|---|---|
| HEAD | `c4161335b406b884d1de6c8e7bf4b3d1f7fe2abe` | unchanged |
| branch | `fix/skill-list-full-uuid` | same |
| porcelain | empty | empty (`end-porcelain-empty=yes`) — zero tracked-file modifications, zero commits |

## (a) Repro recipe (re-runnable)

Scratch dir: `/tmp/wi-76d89e11-repro-1793726495/` (`harness.py`, `final.log` = authoritative run; run1–run4 = debug trail incl. two disclosed harness bugs).

```sh
SCRATCH=/tmp/wi-76d89e11-repro-1793726495; WT=/home/nea/ensemble-src-wt-skill-list-full-uuid
rm -f "$SCRATCH/repro.db"   # one-shot harness: fresh scratch SQLite per run (UNIQUE collision on instances.instance_id if reused)
cd "$WT" && timeout 300 env \
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
  -u PORT -u DATABASE_URL -u ENSEMBLE_SELF_ENV -u PGPASSWORD -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE \
  REPRO_SCRATCH="$SCRATCH" REPRO_WT="$WT" PYTHONPATH="$WT" HOME="$SCRATCH" \
  /home/nea/ensemble-src/.venv/bin/python "$SCRATCH/harness.py"
```

Harness shape — **NO list/view logic reimplemented**; real production construction only:
- Scratch SQLite `sqlite:///<scratch>/repro.db` via `SQLModel.metadata.create_all` (house norm; NOT the migration runner — fresh-SQLite runner boot is broken by `20260714_000001`).
- Real repos: `create_skill_repository` / `create_skill_lineage_repository` / `create_project_repository` / `create_instance_repository` + `ProjectSkillFileRepository` / `ProjectSkillEmbeddingRepository` / `ProjectSkillUsageRecordRepository` (same factories `manager.py` uses).
- Real services: `SkillStoreService(skill_repo, lineage_repo, embedding_service=None)` (= `manager.py:2419` shape) and `ProjectSkillSourceService(instance_repository, project_repository, file_repository, embedding_repository, embedding_service=None, llm_config={})` (= `manager.py:2321` shape).
- Real tool factory: `create_skill_tools(manager, current_instance_id)` → 6 tools `['skill_create','skill_feedback','skill_fix','skill_list','skill_search','skill_view']`; manager shim carries ONLY the real service/repo objects on the exact attributes the tool layer reads (`_skill_store_service`, `_project_skill_source`, `_project_skill_file_repository`, `_project_skill_usage_repository`, `_instance_repository`). Invoked via `tool.ainvoke({...})` — the true agent path.
- DB seed: real creation path `SkillStoreService.create_skill(...)`.
- File seed: real loader entrypoint `ProjectSkillSourceService.ensure_index(worktree, project_id)` → `skills_indexed=5, namespaces=1, db_upserted=5, invalid_skills=0` (the 5 canon `.agents/tester/skills/*/` dirs at this base).
- Import guard (verbatim, mandatory): `IMPORT_GUARD: /home/nea/ensemble-src-wt-skill-list-full-uuid/daemon/__init__.py` (shared `.venv` used as interpreter only; `PYTHONPATH=$WT` + cwd beats the static `.pth`).

## (b) skill_list output — verbatim ids with sources

```
Skills (1 of 1 total):
- [a3f15088] repro-db-skill-e5ee09ce — repro — active — source: db

Project-file skills (5 in 1 namespaces):
- [tester] pf:tester:ab-base-leg-provisioning-artifacts — gate-runner — v1.0.0 — source: project-file
- [tester] pf:tester:blast-radius-leg — gate-runner — v1.0.3 — source: project-file
- [tester] pf:tester:failed-digest-equality-gate — gate-runner — v1.0.2 — source: project-file
- [tester] pf:tester:regression-ab-partition-run — gate-runner — v1.0.1 — source: project-file
- [tester] pf:tester:whole-tree-base-proven-diff — gate-runner — v1.0.1 — source: project-file
```

DB row returned id: `"a3f15088"` (8 chars). File rows returned ids: the five `pf:tester:<name>` addresses.

## (c) skill_view on EVERY returned id — verbatim envelopes

DB path (FAILS):

```
----- skill_view(skill_id='a3f15088')  [taken verbatim from: db-list-bracket] -----
ERROR: skill_view: no skill with id 'a3f15088'
```

Refusal envelope exact text: `ERROR: skill_view: no skill with id '<id>'` (`daemon/tools/skill_tools.py:971`, raised when `SkillStoreService.view_skill` → `skill_repo.get` (`session.get(Skill, skill_id)` — exact-PK lookup, NO prefix resolution) returns None). No fuzzy/prefix fallback anywhere in the DB branch.

File path (ALL 5 RESOLVE — full render, e.g.):

```
----- skill_view(skill_id='pf:tester:ab-base-leg-provisioning-artifacts')  [pf-list-address] -----
# ab-base-leg-provisioning-artifacts
- address: pf:tester:ab-base-leg-provisioning-artifacts
- id: pf-6aef39fb305c287e
... full body rendered ...
```

Machine verdicts in `final.log`: `RESOLVED` ×5 for `pf:` addresses. (An earlier run flagged these REFUSED — harness heuristic grepping `"ERROR"` inside skill *bodies'* prose; corrected and re-run clean; raw renders above are authoritative.)

## (d) Per-failing-id — correct consumable id + where it lives

| failing id (from list) | correct consumable id | where it lives |
|---|---|---|
| `'a3f15088'` | `'a3f15088-49a7-47a8-993d-d7074c9005ba'` | DB column **`skills.id`** (UUID4 TEXT PK; `SkillRepository.create` mints `uuid4()`) |
| — (file rows not failing) | `pf:<ns>:<name>` address (already what the list emits) | minted via `mint_address(namespace, name)` from `.agents/<ns>/skills/<name>/skill.json` manifest field `name` + namespace dir — **no uuid exists in the file path** |

Control leg (measurement only): the full uuid DOES resolve —

```
----- skill_view(skill_id='a3f15088-49a7-47a8-993d-d7074c9005ba') -----
# repro-db-skill-e5ee09ce
- id: a3f15088-49a7-47a8-993d-d7074c9005ba
...
prefix == full_uuid[:8]: True
```

## (e) livez (GET-only, single endpoint)

```
{"status":"alive","uptime_seconds":6101.683519124985,"version":"0.28.9"}
```

## (f) Surprises that could change the fix shape

1. **Truncation site is list-side, one line**: `daemon/tools/skill_tools.py:765` `short_id = skill_id[:8] …` rendered at `:770`. Fix candidates: emit full uuid there (defect text's stated fix) and/or add prefix-resolution in `skill_view`'s DB branch. NOTE: the `[short_id]` bracket is load-bearing for line compactness — the later pin may want to pin BOTH the list shape AND view acceptance.
2. **Only the DB source is affected.** File rows already emit a consumable `pf:` address (resolved by the `is_pf_address` branch at `:924`). "skill_list returns the FULL uuid" is meaningful ONLY for DB rows — file rows have no uuid to return.
3. **Adjacent facet — `pf-<hex16>` pseudo-ids are NOT consumable by `skill_view`**: feeding the loader's internal id yields `ERROR: skill_view: no skill with id 'pf-6aef39fb305c287e'` (view accepts only full uuids or `pf:` addresses). The **injection path renders exactly these `pf-` pseudo-ids** as the file skill's `id` (and `skill_view`'s own header renders `- id: pf-…`), so agents receiving ids from injection hit the same refusal class. If the fix is "view resolves more id forms", this facet argues pseudo-id resolution too; if the fix is "list emits consumable ids", this facet is outside its blast radius. **Dispatcher decision required on scope.**
4. **No truncation marker in list output** — nothing tells the agent the bracket id is a prefix; a consumer cannot distinguish "consumable id" from "display prefix" without hitting the refusal.
5. Minor, unrelated (no fix claimed): `_render_pf_view` renders `body_path` root-joined (omits `.agents/<ns>/skills/<name>/` intermediate) while body CONTENT renders correctly from the real path — display-only oddity in the view header.
6. Harness hygiene: one-shot harness — re-run against a persisted scratch DB collides on `instances.instance_id` UNIQUE; always `rm` the scratch DB first.

## Fences & lifecycle (all held)

- Per-variable env scrub of live-shaped vars (POSTGRES_*, PORT, DATABASE_URL, ENSEMBLE_SELF_ENV, PG*) — `env -u POSTGRES_*` never used (silent no-op).
- `HOME` → scratch (FAKE_HOME-style); no live-shaped path resolution; no daemon boot; no prod contact; GET-only `/livez` on 127.0.0.1:9797.
- Scratch DB lifecycle: created via `create_all` per run → `rm -f` → `PROOF: repro.db GONE (drop complete)`.

## Gaps

None. All 6 mission steps delivered with verbatim evidence; artifacts retained at `/tmp/wi-76d89e11-repro-1793726495/`.
