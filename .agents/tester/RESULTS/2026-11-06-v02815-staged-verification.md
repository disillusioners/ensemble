# v0.28.15 Staged Verification — canary-injection-defense (+ absorbed ari-plane drift)

**Date:** 2026-11-06
**Stage state:** STAGE-ONLY — no promote performed; the user's 3-factor nonce ceremony owns the flip.
**Artifact:** `/home/nea/agents-ensemble/releases/v0.28.15` · binary `37df42db1989e66c3fb7da269e0b666e0fe991925f341846547c47265f117abd`
**Build tree / tag:** `eb32a7d3ddffd11c6f9a022311a168c9768d437a` (annotated tag object `33e67971…`, pushed; peeled target == origin/latest)
**Stage worktree:** `/home/nea/ensemble-src-wt-stage-v02815` (detached @ `eb32a7d3d`, both provenance sidecars)
**Live release at verification:** v0.28.13 (`state.json` sha `6eee0f90dd007be2e5787905fa711c189900af0820fa6fdb7b0babb0362f3fb8`, current symlink `/home/nea/agents-ensemble/current` → `releases/v0.28.13`)

## Verdict

| Leg | Verdict |
|---|---|
| S1 — artifact integrity | **PASS (8/8 legs)** |
| S2 — isolated boot smoke | **PASS (assertions a–f; live fence held)** |
| S3 — one-way content proof | **PASS** |
| S4 — ceremony inputs | **PASS** (tip-identity exact, zero drift) |
| Demo leg (read-only) | **PASS** |
| **Overall** | **READY FOR THE USER'S NONCE CEREMONY — PROMOTE STAGED AS-IS** |

## S1 — Integrity (independent re-hash; sidecar never trusted)

1. **4-way binary sha** — release-dir binary == stage-wt dist == manifest `binary_sha256` == binary sidecar `artifact_sha256`, all `37df42db1989e66c3fb7da269e0b666e0fe991925f341846547c47265f117abd`.
2. **Sidecars** — both `git_head=eb32a7d3ddffd11c6f9a022311a168c9768d437a`, `git_dirty=false` (binary: `dist/ensemble-prod.build-provenance.json`; FE: `frontend/dist/frontend/browser/index.html.build-provenance.json`, `artifact_sha256=7a050d4e3ab906a7f9c63eb46c9303887da23324a5aefac25ef4d3a9350ba4f0` == payload FE index).
3. **Tag peel 3-way** — local peel `eb32a7d3d…`; origin ls-remote `33e67971…` → peels `eb32a7d3d…`; stage-wt HEAD `eb32a7d3d…`.
4. **Manifest counts + re-hash** — agents **379** / frontend **95** / plugins **4930**; agents per-file map re-hashed against the payload: **379/379, 0 mismatch, 0 missing**. Tree hashes: agents `5249b874…`, frontend `9c717d02…`, plugins `4715cf64…`, config `0f0875bd…`, launcher `c4f640e1…`.
5. **Migration census** — source: 121 files, 121 distinct prefixes (`uniq -d` empty); binary-level: live v0.28.13 = **121**, staged v0.28.15 = **121**, **sets IDENTICAL** (diff empty). Zero lane-authored `.sql` (newest prefixes `2026-11-05_020500/020501` are the nest-anomaly drift, foreign to canary).
6. **Payload source-less** — top level exactly `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/`; no `scripts/`, `.env`, `daemon/`, `.git`, `build/`, no `.staging*`/`.aside*` residue.
7. **Stage-wt porcelain** — 0; HEAD `eb32a7d3d…` (detached).
8. **Payload prompt-baseline** — generator `--check` in the stage worktree: `PROMPT-BASELINE: OK — 181 versioned artifact(s)`, exit 0; `diff -r` payload agents vs stage-wt agents = EMPTY (transitive proof); payload baseline 181 rows, stage-wt 181, live v0.28.13 177.

## S2 — Isolated boot smoke (house isolation contract)

Isolation: binary **copied** to scratch (`/tmp/v2815-smoke.Ipk7DT`), scratch sha == `37df42db…`; `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly; `ENSEMBLE_SELF_ENV=sandbox`; curated env (no ambient leak — ambient `PORT=9797` poison vector explicitly excluded); throwaway PG DB `ensemble_smoke_v2815` on the local server; scratch port **18050**; SIGTERM-only teardown to the exact binary PID.

- (a) Banner `Starting Ensemble v0.28.15`; time-to-livez ≈ 12 s (second boot).
- (b) `livez` 200 `{"status":"alive","version":"0.28.15"}`; `readyz` 200 `status=ready`, all components true, `reasons=[]`, `grace_active=false`, `leak_signal detected=false`, MCP all ok, `skill_sources enabled=true / db_stack_present=true`.
- (c) **44 registered agents, 0 parse failures** (43 + canary); boot log 686 lines archived.
- (d) canary payload/meta parses: `allow=[bash, filesystem, time, help]`, `deny_spawn=[knowledge]`, `team_members=[]`.
- (e) skill bank: 69 seeded rows; `injection-screen` auto_load polarity **EXACT** — canary=true, sweeper=true, aegis=false, worker=false.
- (f) persona dual shape: all five (aegis, hacker, cracker, sweeper, worker) `rule.md` carry BOTH `[UNTRUSTED-ADJACENCY:` (1 each) and `## Reporting Anomalies` (1 each) — merged-tree shape confirmed.

**Live fence (PRE == MID == POST):** `state.json` sha `6eee0f90…` identical all three rounds; `/home/nea/agents-ensemble/current` → `releases/v0.28.13` identical; live `livez` v0.28.13 with monotone uptime 15698.7 → 16097.0 → 16123.9 s. Teardown: SIGTERM exact PID → exit 1 s; port 18050 freed; scratch DB dropped (0 rows post); scratch dir removed.

**Harness lesson (disclosed, not a binary defect):** the copied binary resolves `agents/` from its own scratch install root — the first boot registered 0 agents until the payload `agents/` tree (48 dirs) was copied alongside the binary; second boot = 44 agents / 69 seeded rows. Future smoke recipes must copy the agents tree with the binary.

## S3 — One-way content proof (staged payload vs live v0.28.13 payload)

- **Canary 9-file tree** — all present staged / absent live; shas: meta `391e8631…`, soul `8e32617c…`, rule `2b9669bf…`, workflow `32d36075…`, tools_note `d5105e4f…`, skill-set `3a86d7ec…`, injection-screen.md `62bcff25…`, injection_scan.py `4c8f4a0b…`, injection-rules.json `2b9e8d84…`.
- **3 mirrors** — aegis `c2773f2a…` (differs from canonical ONLY in `auto_load: true→false`), worker `c2773f2a…` (same), sweeper `62bcff25…` (**byte-identical to canonical — correct per T31 polarity**: sweeper is an auto_load-true recipient); all three absent live.
- **5 persona doctrine anchors** — aegis/hacker/cracker/sweeper/worker staged `rule.md` each carry 1 `[UNTRUSTED-ADJACENCY` anchor; live carries 0 in all five.
- **Baseline rows** — staged 4 canary rows (rule/soul/tools_note/workflow) present; live 0.
- **injection-screen v1.0.1** in all 4 manifests (canary/aegis/sweeper/worker `skill-set.yaml`); live has no injection-screen manifests.
- **Full agents diff (stacked lineage disclosed)** — 12 changed + 5 added + 0 removed: canary-attributable = canary/ (9 files), aegis adoption (meta/rule/workflow/skill-set), 3 mirrors, 5 persona rule.md, baseline re-stamp; **`agents/ari/{rule,workflow}.md` changes are the absorbed ari-plane drift (v0.28.14 content), not canary** — leader adjudicates at the ceremony (expected per supersession).

## S4 — Ceremony inputs

- **Promote command:** `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.15 bash scripts/upgrade/promote.sh live --f2-verified-closed` (env-first; run from `/home/nea/ensemble-src-wt-scripts-pin` or the stage worktree — CWD selects the executing script).
- **Scripts-pin fence:** pin HEAD `84df6b55c` (3 commits ahead of floor `2147c0298`); `merge-base --is-ancestor 2147c0298 HEAD` → **exit 0** ⇒ the arm-time provenance guard passes.
- **Tip-identity 3-way:** origin/latest == tag target == stage tree == `eb32a7d3d…`; `git diff --name-only eb32a7d3d origin/latest` = EMPTY ⇒ **PROMOTE STAGED AS-IS**.
- **Manifest:** `rollback_safe=true` (source=explicit lives in the stage journal/tag annotation, not the manifest field); `config_sha256=0f0875bd…`, `launcher_sha256=c4f640e1…`, `known_schema_gen=20261105_020501_create_anomaly_clusters.sql`, agents_tree `5249b874…`, staged_at 2026-11-06T06:31:45Z, `pipeline_scripts_floor_commit=2147c0298`. No top-level `fe_index_sha256` field (the FE sidecar carries `7a050d4e…`).
- **state.json (read-only):** current `v0.28.13`, previous `v0.28.12`, `in_flight=null`, `pending_op=null`, `quarantined=["v0.28.5"]` (pre-existing), rollback window {2/24h, start 2026-11-01T22:32:36Z}, `cooldown_until` lapsed.
- **releases/ inventory:** v0.28.11 … v0.28.15 present; protected targets live v0.28.13 + staged v0.28.14 + v0.28.15 all present; **v0.28.15 supersedes v0.28.14 content-wise (stacked lineage) — one ceremony carries both.**
- **Live health probe:** `livez` 200 `{"status":"alive","uptime_seconds":15816.4,"version":"0.28.13"}`.

## Demo leg (read-only, zero writes)

- `/home/nea/agents-ensemble-demo/.env:107` reads EXACTLY `ENSEMBLE_SELF_ENV=demo` (mode 600, mtime 2026-11-06 06:29:23 UTC).
- Demo `/home/nea/agents-ensemble-demo/releases/v0.28.15` present; binary sha `37df42db…` == live stage (**byte-identical**).

## Disclosures

1. `ari/rule.md` + `ari/workflow.md` in the staged agents diff = ari-plane drift (v0.28.14 lineage riding v0.28.15); not canary; supersession expected.
2. Sweeper mirror byte-identical to canonical = correct T31 `auto_load: true` polarity, not a defect.
3. Smoke harness needed the payload `agents/` tree copied alongside the scratch binary (first-boot 0 agents; corrected; harness lesson only).
4. Manifest carries no top-level `source=explicit` / `fe_index_sha256` fields — the markers live in the stage journal/tag annotation and the FE sidecar respectively.
5. No promote, no nonce, no live-symlink/state.json contact: the fence held byte-identical across the entire verification window (state.json `6eee0f90…` PRE==MID==POST; live uptime monotone).

— Verified by tester (functional gate GO @ `5314ee5d3`; staged verification on artifact `37df42db…`).
