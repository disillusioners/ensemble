# v0.28.11 STAGED VERIFICATION — FE T1 metrics dashboard lane (wi-32a99618)

Date: 2026-11-04 · Tester staged-verification gate · Executors: `fe-t1-v3-artifact` (e2619b48) +
`fe-t1-v3-smoke` (08f426a0), unique /tmp prefixes, zero cross-contact.
Artifact: /home/nea/agents-ensemble/releases/v0.28.11 (staged_at 2026-11-04T13:36:04Z) · merge
`86233bb52` · bump `f5ee0432a` · tag `bfb6bc995` (annotated, PUSHED, annotation carries the
rollback adjudication + no-promote disclosure + promote command) · stage worktree
`/home/nea-ensemble-src-wt-stage-v02811` (KEPT, detached @ f5ee0432a, BOTH provenance sidecars).
NO promote performed — this gate readies the artifact for the user's ceremony. Nothing in the
artifact was modified (verified: `find releases/v0.28.11 -newermt '2026-11-04 13:45:00Z'` empty).

## VERDICT: **STAGED-VERIFIED PASS-WITH-NOTES — ceremony-ready.** Every note below is informational; none gates the ceremony.

## S1 — Artifact integrity — PASS (independently re-hashed; sidecars never trusted)

| check | result |
|---|---|
| Binary 4-way | release dir == stage-worktree dist (`/home/ensemble-src-wt-stage-v02811/dist/ensemble-prod`, 81,592,656 B) == manifest `.binary_sha256` == binary sidecar `.artifact_sha256` — all **`03c74e5df3060163252c977fba90c4669e8b8a5e2ec4814027c02a768cfd414b`** |
| Sidecars | binary + FE both `git_head=f5ee0432a82c30dd78758ce37b955ebf63f4ce05`, `git_dirty=false`; FE sidecar artifact sha `8c34ed4f…3acb9` |
| Tag | `v0.28.11` → annotated `bfb6bc995…` → peeled commit `f5ee0432a…` ✅; `git ls-remote` confirms PUSHED (tag + peeled both on origin) |
| Manifest | **5363/5363 per-file entries re-hashed, 0 mismatch, 0 missing, 0 undeclared files on disk**; counts **agents 338 / frontend 95 / plugins 4930**; `known_schema_gen 20261031_120002_create_project_skill_usage_records.sql`; `contains_contract_phase true`; `rollback_safe true`; config sha `0f0875bd…` + launcher sha `c4f640e1…` re-hashed from the release dir, matching |
| Payload hygiene | top level exactly `agents/ frontend/ plugins/ config.yaml ensemble-prod launcher.sh manifest.json` — NO scripts/, NO .env, NO sidecars, NO .staging.*/.aside.* residue |
| Migrations vs live v0.28.10 | **EMPTY at both levels.** SOURCE: `git diff v0.28.10..f5ee0432a -- daemon/migrations/versions/` → 0 lines; 119 .sql in the tree, no duplicate prefixes. EMBEDDED: census of all 119 names inside BOTH binaries — staged 119/119, live 119/119, present-set sha **identical** (`5f2fdebd…29ce`), zero added/removed |
| Prompt baseline | `PROMPT-BASELINE: OK — 161 versioned artifact(s)` — in the stage worktree AND re-run against the staged release tree; agents tree sha `3ad0ea53…` reproduced by the house algorithm from the payload; all 338/338 agents files blob-identical to `git show f5ee0432a:agents/<path>` |
| CHANGELOG | `## [Unreleased]` (line 8) above `## [0.28.11] — 2026-11-04` (line 96); changelog commit `df330077b` (+10); not part of the source-less payload |

## S3 — Staged-content one-way proof (FE lane) — PASS

Every lane-exclusive marker PRESENT in the staged dist / ABSENT in live v0.28.10's dist
(staged carrier = the lazy metrics chunk `chunk-WFDAM46P.js` unless noted):
`ensemble-metrics-route-v1` (the route-key constant) · tab-manifest vocabulary
(`disposition:"bound"`, `planned-unbound`, `"db-pool"`, `"checkpoint"`) · resource column
(`resource-live-strip`, `resource-timeline-panel`, `anomalies-panel`) · redirect
(`[ResourcesRedirect]` in `main-VUJOGCCR.js`, the `"resource"` tab id) · door-state union
(`metrics-door-state-cause/-meaning/-action`) · house overlays (`house-anomaly-overlay`,
`house-data-end-overlay`, `house-crosshair`, `house-anomaly-marker` ×7) · 11 component selectors
(`app-metrics-resource-tab`, `app-metrics-door-state`, `app-metrics-central-tab`,
`app-metrics-db-pool-tab`, `chart-panel-canvas`, `chart-panel-proxy`, `chartjs-panel`,
`house-svg-trend`, `resource-range-select`, `families-filter-input`, `resource-anomalies-panel`).
Honest disclosure: `queryParams`, `AUTH_NOT_CONFIGURED`/`auth_not_configured`, and
`metrics-registry-degraded-badge` ALSO exist in live — proven pre-existing (git-grep at the
v0.28.10 tag: the central-metrics-v1 lane, live since v0.28.6), not lane content.
Vendor corroboration: chart.js ships LAZY (`chunk-TXUCW6LI.js`, 206,857 B, referenced only via
`import()` from the lazy route chunk; zero static edges; absent from index.html / initial vendor
bundle / main; live dist contains zero chart.js). Initial vendor bundle `scripts-DN54Y2NQ.js`
byte-identical staged↔live — the initial-payload posture is unchanged.
**fe_index 3-way**: payload `frontend/dist/frontend/browser/index.html` == stage-worktree copy ==
FE sidecar == **`8c34ed4f42690629f61993d4c09affe1f7b5b92dfa97c43bbdde3b3ea743acb9`**.
**FE tree reconcile**: 95 == 95 (staged vs live manifest); per-file diff 83 added / 83 removed /
12 common (11 byte-identical incl. the initial vendor bundle + favicons; `index.html`
changed-in-place `7461aa8d→8c34ed4f`); 0 pure renames — total chunk churn is the expected
content-hash rebuild after the route-graph change; initial payload 4.23 MB in both.

## S4 — Ceremony inputs — PASS

- **Live fence state**: `{"current":"v0.28.10","previous":"v0.28.9","in_flight":null,"pending_op":null}`;
  `quarantined:["v0.28.5"]` (pre-existing, NOT the target); cooldown lapsed. Live pointer:
  `/home/nea/agents-ensemble/current -> releases/v0.28.10`.
- **Promote command (verbatim, NOT executed)**:
  `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.11 bash scripts/upgrade/promote.sh live --f2-verified-closed`
- **Scripts-pin ancestry — PASS**: live `.env:107 ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade`;
  pin HEAD `84df6b55c8…` (porcelain 0); `git merge-base --is-ancestor 2147c0298 84df6b55c` →
  **rc=0** ⇒ the arm-time provenance guard will PASS. Manifest
  `pipeline_scripts_floor_commit=2147c0298a09…` and the pinned tree's `lib.sh:95` stamp agree
  (floor and stamp travel together); `promote.sh` present at the pinned path (55,515 B).
- **Retention**: releases/ holds **v0.28.8/9/10/11** (4 vs keep-3); promote-time machinery-owned
  candidate = **v0.28.8**; nothing evicted by this verification.
- **Drift: ZERO.** `origin/latest == f5ee0432a == staged tree == tag target` (fetch of the
  remote-tracking ref only): `f5ee0432a..origin/latest` → 0 commits, 0 files. **PROMOTE STAGED
  AS-IS — no tip-identity ambiguity; a re-stage would be REFUSED by the L1 guard (exit 78) BY DESIGN.**

## S2 — Isolated boot smoke — PASS (8/8 assertions; binary COPY, scratch root, throwaway PG)

Setup: binary copied to `/tmp/fe-t1-v3-smoke/root/` (sha re-verified == 03c74e5d…; the
release-dir binary was NEVER run in place); payload trees (agents/frontend/plugins) copied beside
it; scratch `config.yaml` byte-identical to the artifact's (`0f0875bd…`); throwaway DB
`ensemble_smoke_v02811` created via the house test superuser (`ensemble_dev_app` lacks CREATEDB —
known); per-var scrub asserted `env|grep POSTGRES` hits=0 (ambient poison ensemble_prod/10.44.0.2/
PORT=9797/SELF_ENV=live/RAG_IS_REQUIRED all removed one-by-one); fences: `PORT=18711` (free
asserted), `HOST=127.0.0.1`, **`ENSEMBLE_SELF_ENV=sandbox` PINNED**, **`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` PINNED**, `ENSEMBLE_ORPHAN_F1_ENABLED=0`, `ENSEMBLE_CONFIG`+`ENSEMBLE_DATA_DIR` to the scratch root, fake HOME; scratch `ensemble.json` jq-pinned to the throwaway DB (mode 600).

| assertion | observed (verbatim digest) |
|---|---|
| banner | `Starting Ensemble v0.28.11` (13:47:16) |
| /livez | 200 `{"status":"alive",…,"version":"0.28.11"}` |
| /readyz | 200 READY, all components true, `reasons:[]`, `grace_active:false`, `leak_signal:false`, mcp context7/webfetch/plane ok, skill_sources enabled |
| metrics door | **503 `{"detail":{"error":"AUTH_NOT_CONFIGURED","message":"Auth not configured (fail-closed)"}}`** (snapshot/series identical) — the fail-closed door LIVE in the artifact |
| resources door | **200** open posture, `daemon.rss_mb=322.02` non-zero |
| DSN + sqlite | engine + checkpointer lines name `ensemble_smoke_v02811`; `No PostgreSQL ENV vars detected, defaulting to sqlite` → count **0**; `Loaded ensemble config: database=postgres` |
| port owner | `127.0.0.1:18711` held by `ensemble-prod` pid 3749325 (descendant of the launched bootloader PID — PyInstaller onefile shape) |
| FE surface (bonus) | `GET /` 200 (SPA index 9257 B, `main-VUJOGCCR.js`), `GET /metrics` 200 (the lane's page route in-payload) |

Teardown: SIGTERM (no SIGKILL) → graceful chain (`MCP warm-up pool drained` → `Database engine
disposed` → `Graceful shutdown complete`) → port refused/free → scratch DB DROPPED (catalog count
0; no pre-existing DB touched; `pg_stat_activity` 0 sessions on prod-class DBs — local PG has no
`ensemble_prod` at all, live prod is remote) → orphans cascaded (unrelated context7 trees of other
lanes untouched) → `/tmp/fe-t1-v3-smoke` swept, zero residue → artifact mtime-window empty, stage
worktree porcelain 0.

**LIVE FENCE (the only live touches):**

| probe | PRE 13:42:10Z | MID 13:48:38Z | POST 13:49:04Z |
|---|---|---|---|
| state.json sha256 | `d0ddfb36…9c71f6be` | identical | identical |
| current → | `releases/v0.28.10` | same | same |
| :9797 /livez | 200 · uptime 22052.8 · v0.28.10 | 22440.7 | 22466.9 — **monotone, no restart** |

## Notes (all informational; none gate the ceremony)

1. Three marker literals (`queryParams`, `AUTH_NOT_CONFIGURED`, `metrics-registry-degraded-badge`) pre-exist in live — proven at the v0.28.10 tag (central-metrics-v1, since v0.28.6); the lane's own surfaces are all one-way.
2. FE chunk churn is total (83/83 rebuilt, 0 byte-identical renames) with the count unchanged at 95 — expected for a content-hash rebuild after the route-graph change; the initial vendor bundle + CSS are byte-identical.
3. `manifest.agents_tree_sha256` is the house walk-algorithm hash, NOT a git tree id — verifying with `git rev-parse <commit>:agents` yields a false mismatch; the payload recomputation reproduces it exactly.
4. Pre-existing `quarantined:["v0.28.5"]` (unrelated; the promote target is clean); releases/ over-retention is machinery-owned.
5. Smoke-method notes: the frozen onefile's listener is a DESCENDANT of the launched PID (assert on the process tree, not the literal PID); `systemd-run --user` unavailable on this host ⇒ cgroup inherited from ensemble-main (compensating fences: sweep=0 pinned + F1 kill-switch + copy-not-in-place + loopback bind + fake HOME + no journal bound at all — `install_dir=None`); banner emitted twice (cosmetic); zero ERROR/CRITICAL lines; zero supervision_boot rows.
6. The release payload deliberately omits the FE provenance sidecar (stage.sh strips it; `browser/` is web-servable) — the sidecar lives in the KEPT stage worktree and is correctly absent from the manifest map.

## Ceremony readiness

**STAGED-VERIFIED PASS-WITH-NOTES.** The artifact is byte-consistent (4-way sha, 5363/5363
re-hash), provenance-complete (sidecars/tag/drift-free tip), migration-neutral (119==119 at both
levels), one-way-proven as the lane's FE content, boots green in isolation with the lane's doors
live in the payload, and every ceremony input checks out (fence clean, pin ancestry rc=0,
retention candidate v0.28.8, zero drift ⇒ promote AS-IS). The user's 3-factor nonce ceremony owns
the flip.
