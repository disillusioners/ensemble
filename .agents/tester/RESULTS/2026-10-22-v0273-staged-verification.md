# Independent Staged-Release Verification — v0.27.3 (commission `scheduler-deferred-alarm-delivery`)

- **Date:** 2026-10-22 (19:1x–19:4x UTC)
- **Artifact:** `/home/nea/agents-ensemble/releases/v0.27.3` (STAGED — promote = user 3-factor nonce ceremony)
- **Build tree:** `6768724dd3dc265b17982a273e9b9abdf7518510` (== bump commit; origin/latest at stage time)
- **Tag:** `v0.27.3` = annotated `a46420aa5859a68b46e7724f0b10ee05b3123fcf` → `6768724dd` — **LOCAL-ONLY** (`git ls-remote origin | grep -c 'v0\.27'` → 0; 895 remote refs scanned)
- **Binary sha256 (3-way, recomputed):** `d9c937bcfa6bb0b9ab89c780c291363be65fdcd73c2dbadc9b80a0c285688115`
- **Mode:** read-only + ONE isolated boot smoke. 4 parallel ad-hoc lanes (no pack registration — read-only mandate forbids repo writes; every lane command-level `timeout`-wrapped; v0.20.2/v0.27.1 precedent) + 1 docs lane. 0 promotes, 0 symlink flips, 0 state.json writes, 0 tag ops.
- **Live state (read-only, UNTOUCHED):** `:9797` **v0.27.1** (MainPID 2199019, NRestarts 0, epoch 2026-10-22T05:25:49Z), `current → releases/v0.27.1`, `state.json` sha `76d477cb…` unchanged (mtime unchanged 05:26:34), live-install write-detector **0 files**.

---

## VERDICT: 🟢 STAGED-VERIFIED (PASS) — 8/8 commission checks — **CEREMONY-READY**

| # | Commission check | Verdict | Lane (instance) |
|---|---|---|---|
| 1 | Sha 3-way + sidecars sane + tag local-only | ✅ PASS | integrity `6dbaf8be` |
| 2 | Manifest 4-field delta + payload hygiene + trees byte-identical | ✅ PASS | integrity `6dbaf8be` |
| 3 | Migration registry + EMPTY commission delta + known_schema_gen | ✅ PASS | migseal `359d69a7` |
| 4 | Feature IS in the binary (anti-wrong-payload) | ✅ PASS | binary `0f22bf83` |
| 5 | Isolated boot smoke (banner/livez/readyz v0.27.3, teardown) | ✅ PASS | smoke `cb452da1` |
| 6 | Prompt-artifact seal 161/161 at the tag | ✅ PASS | migseal `359d69a7` |
| 7 | Rollback-safety adjudication (reality matches manifest) | ✅ PASS | binary `0f22bf83` (+B2) |
| 8 | Fences (symlink / state.json / releases listing / live daemon) | ✅ PASS | smoke `cb452da1` (+integrity A7) |

---

## 1 — Sha 3-way + provenance sidecars + tag local-only: PASS

```
disk     d9c937bcfa6bb0b9ab89c780c291363be65fdcd73c2dbadc9b80a0c285688115
manifest d9c937bcfa6bb0b9ab89c780c291363be65fdcd73c2dbadc9b80a0c285688115
sidecar  d9c937bcfa6bb0b9ab89c780c291363be65fdcd73c2dbadc9b80a0c285688115
```
- Sidecars (2, in the KEPT staging worktree `/home/nea/ensemble-src-wt-stage-v0273`): `dist/ensemble-prod.build-provenance.json` + `frontend/dist/frontend/browser/index.html.build-provenance.json` — BOTH `git_head=6768724dd3dc265b17982a273e9b9abdf7518510`, `git_dirty=false`.
- FE index `960d78b8aa95f03b6f65a3ffe709dd48575392fc1b8321c0ca1e8839bbb3457d` byte-identical to v0.27.2 (sidecar value corroborated by manifest-map + on-disk re-hash in BOTH releases). Disclosure: the v0.27.2 worktree no longer exists, so the v0.27.2 FE sidecar file itself was unreadable — the manifest+disk re-hash pair is the equivalent (stronger) evidence.
- Tag: annotated `a46420aa…` peels to `6768724dd…`; `ls-remote origin` → **0** `v0.27*` refs; staging worktree HEAD `6768724dd…`, porcelain 0.

## 2 — Manifest delta + payload hygiene + tree byte-identity vs v0.27.2: PASS

- Scalar delta re-derived (not trusted): **EXACTLY 4** changed — `binary_sha256` (`24ae5213…`→`d9c937bc…`), `binary_version` (0.27.2→0.27.3), `version` (v0.27.2→v0.27.3), `staged_at` (16:11:23Z→19:03:46Z). Unchanged: `config_sha256 7bab75e9…`, `launcher_sha256 a3a44461…`, `agents_tree abb1cce9…`, `frontend_tree 27107f72…`, `plugins_tree 4715cf64…`, `known_schema_gen`, `rollback_safe=true`, `contains_contract_phase=true`.
- Per-file maps: agents **339** / frontend **93** / plugins **4930**; pairwise vs v0.27.2 maps added=0 removed=0 changed=0; independent re-hash of every payload file → missing=0 extra=0 hash_mismatch=0.
- Payload hygiene: top level EXACTLY `{agents, config.yaml, ensemble-prod, frontend, launcher.sh, manifest.json, plugins}`; zero `*.build-provenance.json` / `.env*` / `.staging*` / `.agents*` in the payload; no release-root `scripts|daemon|tests|.git`; config+launcher re-hash == manifest. (`.gitkeep` ×2 under `agents/maintenancer/` are keyed payload files, not a .git dir; `scripts|daemon` dirs under `plugins/opendesign/**` are vendored plugin payload.)
- Tree byte-identity: `diff -rq` rc=0 on all three trees (339/93/4930 both sides) **and** house tree-hash algorithm re-derived from `lib.sh:1952-1970`+`stage.sh:441-446` (fidelity control: python re-impl byte-identical to the house bash dump) → all 6 recomputed tree hashes MATCH (3 trees × 2 releases). Trap avoided: the house `sed 's|^\./||'` strip is load-bearing.

## 3 — Migration registry: PASS

- `git ls-tree 6768724dd -- daemon/migrations/versions/` → **108** `*.sql`, **108 distinct** `date_sequence` prefixes (`sort | uniq -d` EMPTY); FS cross-check in the staging worktree → 108.
- Commission delta: `git diff --name-status a0eb5639f..6768724dd -- daemon/migrations/versions/` → **0 entries** (`--no-renames` control also 0). **No DDL ships in this release.**
- `known_schema_gen=20261022_000001_create_mcp_gateway_audit.sql` — matches manifest, file exists at the tag, prefix is the lexically-max of the 108.

## 4 — The feature IS in the binary (PYZ-level, anti-wrong-payload): PASS

Method: `CArchiveReader` → `open_embedded_archive('PYZ.pyz')` → per-module `extract` → recursive walk of `co_consts`/`co_names`/`co_varnames`/`co_freevars`/`co_cellvars` (identifier namespaces included — 5/7 needles live there). **CPython 3.14 reader required** (bundles are 3.14 builds; the main `.venv` 3.13 reader raises `bad marshal data` and SIGSEGVs — resolved via the staging worktree build venv 3.14.7 + an independent uv 3.14.7 run; byte-identical hit sets, 2 passes).

| symbol | v0.27.3 | v0.27.2 | module(s) |
|---|---|---|---|
| `deferred_durable` | **PRESENT** | absent | daemon.sources.adapters.scheduler |
| `refused_permanent` | **PRESENT** | absent | daemon.sources.adapters.scheduler |
| `nondelivery_age_cap` | **PRESENT** | absent | scheduler + message_queue.repository |
| `taskless_enqueue` | **PRESENT** | absent | daemon.sources.adapters.scheduler |
| `_defer_since` | **PRESENT** | absent | daemon.sources.adapters.scheduler |
| `update_ready_content` | **PRESENT** | absent | scheduler + message_queue.repository |
| `BoundCallerRefusal` | **PRESENT** | absent | scheduler + mapper + tools.self_alarm |

- Version anchors: `daemon` co_consts → `0.27.3` (staged) / `0.27.2` (old), mutually exclusive. 4603 code modules scanned per binary, 0 unmarshal failures.
- All 4 test-pin names (`test_mapper_e1_stranger_spawn_refusal_pin`, `test_scheduler_deferred_delivery_pin`, `test_scheduler_deferred_mechanism_probe`, `test_self_alarm_deferred_visibility_pin`) **ABSENT** from v0.27.3 — content-level AND PYZ-TOC-module-level. Tests do not ship. ✔

## 5 — Isolated boot smoke: PASS

Scratch install-layout copy (`$S/releases/v0.27.3s/` — **F1 mitigation in force**: path derivation binds SCRATCH, never the live root), scratch PG role+DB `ens_smoke_v0273` on local 127.0.0.1:5432, system-level transient unit `ensemble-smoke-v0273.service`, port 19121 with BOTH `PORT`+`DAEMON_PORT` exported (8088 never bound).

- **cgroup isolation proven:** smoke `/system.slice/ensemble-smoke-v0273.service` (MainPID 2958789) vs live `/system.slice/ensemble-main.service` (MainPID 2199019).
- **F1 neutralized by evidence:** `UpgradeJournalSweepService started: … install_dir=/tmp/v0273smoke.5gfciE` — the sweep bound the scratch root, NOT `/home/nea/agents-ensemble`.
- **Banner (verbatim):** `daemon.api - INFO - Starting Ensemble v0.27.3` ×2.
- **livez** 200 `{"status":"alive",…,"version":"0.27.3"}` · **readyz** 200 all-true (`database/queue_freshness/services/checkpoint_saver`), `draining=false`.
- Error census: 7 ERRORs, ALL the known-benign `computer-use` MCP `libXtst.so.6` class; fatal-pattern census 0 (`42804`/`DatatypeMismatch`/startup-failed/`DuplicateMigrationVersionError`/`InsufficientPrivilege`/CRITICAL). Skill seeding `{'new': 64, 'errors': 0}`.
- **No prod contact:** 8/8 smoke PG sockets → 127.0.0.1:5432; sockets to 10.44.0.2 from smoke pids = 0; local `pg_stat_activity` only `ens_smoke_v0273`; local `ensemble_prod` DBs = 0.
- **Teardown:** SIGTERM-only (`Result=success`, `ExecMainStatus=0`), port freed, scratch DB+role dropped (catalog 0/0), scratch dir removed, staged payload re-sha == `d9c937bc…` (NOT mutated by the boot).

## 6 — Prompt-artifact seal: PASS

```
cd /home/nea/ensemble-src-wt-stage-v0273 && timeout 240 python3 scripts/protection/generate_prompt_artifact_baseline.py --check
→ PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256   RC=0
(porcelain 0 pre/post — --check wrote nothing)
cd /home/nea/agents-ensemble/releases/v0.27.3 && sha256sum -c agents/.prompt-artifacts.sha256
→ RC=0 | OK: 161 | FAILED: 0
```

## 7 — Rollback-safety adjudication: PASS (reality matches manifest)

- No DDL in the release: migration delta EMPTY (§3), payload trees byte-identical (§2) — only the binary + version fields differ.
- **Binary-level proof:** ALL 7 feature symbols ABSENT from the v0.27.2 binary (`24ae5213…` — matches the project's recorded v0.27.2 sha), with a positive-control census: every carrying module (scheduler adapter 438 strings, mapper 162, self_alarm 343, mq.repository 333) IS present and fully walked in v0.27.2 with **0 needle hits** — the absence is meaningful, not a scan artifact. The old binary has zero references to the new code paths → `rollback_safe=true source=explicit` HOLDS.

## 8 — Fences: PASS (live UNTOUCHED)

| Item | pre (D0) | post (D7) |
|---|---|---|
| MainPID / NRestarts / epoch | 2199019 / 0 / 05:25:49Z | identical |
| livez :9797 | 200, version **0.27.1** | identical |
| `current` symlink | `releases/v0.27.1` | identical |
| `state.json` sha | `76d477cb955f9215d81105dee8386e3e65549b0a516779bb063e30636dc503a9` | identical (mtime unchanged) |
| `releases/` listing | state.json + 2 baks + v0.26.3/0.27.0/0.27.1/0.27.2/0.27.3 | identical |

Live-install write-detector (`find /home/nea/agents-ensemble -newermt 19:20`, excl. the live daemon's own log): **0 files**. No eviction performed (machinery-owned).

---

## Disclosures & observations (none blocking)

1. **Live :9797 `readyz` = 503 degraded (`checkpoint_saver:false`)** — PRE-EXISTING (identical at D0 and D7), consistent with the known chronic checkpoint-store saturation incident. Not caused by this lane and NOT an artifact defect — but the leader should know the live daemon is degraded while the ceremony approaches. The staged binary's own scratch readyz was all-true.
2. Smoke runs 1–3 were harness faults in the worker's own wrapper (runs 1–2 died pre-`exec` on a `set -u` self-unset bug — no daemon, no port, no PG contact, no live write; run 3 booted but with a harness-induced agents-tree miss). **Run 4 alone carries the verdict** (clean, agents wired, both warnings gone). Fully contained and disclosed.
3. CPython-3.14 reader requirement for PYZ inspection (3.13 SIGSEGVs) — recorded to KB by the worker; affects future binary-level lanes.
4. v0.27.2 FE sidecar file unreadable (staging worktree evicted) — equivalent evidence supplied (§1).
5. `PostgreSQL checkpointer (localhost:5432/ensemble)` boot-log label is the persisted-config default, not the DSN; real DB contact proven PG-side (§5).

## Ceremony path (for the record — the user's 3-factor nonce gate owns everything after this)

```
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.27.3 bash scripts/upgrade/promote.sh live --f2-verified-closed
```

Workers: integrity `6dbaf8be-f2bd-4873-8159-034572ac47e3` · migseal `359d69a7-16ce-4ca9-af7b-91fe6f481cb8` · binary `0f22bf83-07bd-45e1-b13b-f9155c7c70f9` · smoke `cb452da1-e4e7-457f-a4b9-5d9e9fe4e000`. Skills consumed: `independent-staged-release-integrity-verification`, `release-build-provenance-recovery`, `pyinstaller-co-consts-doc-surface-proof`, `binary-rollback-posture-co-consts-schema-delta`, `staged-binary-boot-smoke`, `host-isolated-drill-harness` (feedback + 2 skill_fix requests filed by the lanes).
