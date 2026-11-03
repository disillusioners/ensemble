# Staged Verification — release v0.28.8, lane `promote-handback-halt` (ceremony-gate grade)

**Staged:** `/home/nea/agents-ensemble/releases/v0.28.8` · 2026-11-03T03:56:49Z · tag `v0.28.8` = `1215e0e9b` → bump `84df6b55c` (PUSHED; origin/latest FF) · merge `2147c0298` (--no-ff) · floor bump `e71a66e55` (FLOOR==REV==`2147c0298`) · binary `9e96104c1b65b7f27500ccc38362c7e5d7fa1aa9309f4892e12e1ca8899eee72` · launcher `c4f640e1…` (first launcher payload change since v0.25.x) · FE index `5fef719b…` · manifest 338/95/4930.
**Tester:** ca6584a3 (dispatcher) · integrity worker `50ae4865` (S1+S3-PRE+S4) · smoke worker `c5606d52` (S2+S3-MID/POST). Evidence: `/tmp/phh-sv1/`, `/tmp/phh-sv2/`.

# VERDICT: STAGED-VERIFIED — **GO for the ceremony**, one user-owned precondition UNMET at verification time (S4: advance the pinned scripts checkout — restated below, BY DESIGN refuse otherwise)

## S1 — Artifact integrity: 9/9 PASS (independently re-derived; nothing trusted)

- **Binary 4-way**: release dir == staging-wt dist == manifest `binary_sha256` == sidecar `artifact_sha256` == `9e96104c…`. Sidecars: `git_head=84df6b55c…`, `git_dirty=false`; FE sidecar `5fef719b…` == manifest per-file map == on-disk payload.
- **Tag**: local `v0.28.8^{}` → `84df6b55c`; remote tag object `1215e0e9b…` peels to `84df6b55c`; origin/latest == `84df6b55c`.
- **Lineage**: merge `2147c0298` parents `03a2297c3`+`3d1230380` (--no-ff); floor bump touches exactly upgrade_tools.py + lib.sh; `12349cf17` (gate-frozen tip) AND `f5f0f5b88` (tester evidence branch) both ancestors — evidence merged into the staged lineage.
- **Migrations**: git-diff `04b44ab79..84df6b55c` EMPTY; 119 files == 119 distinct prefixes, zero dupes; **embedded binary census 119 == source set (PYZ-decompressed; raw grep -a is blind on PyInstaller)**; lane-authored `.sql` over the full lane range: ZERO.
- **Baseline**: 161/161 in BOTH the staging worktree and the release payload (house authority).
- **Payload source-less**: exactly 7 entries; no scripts/, .env, provenance files, .staging residue, venv, node_modules.
- **Manifest**: 5363/5363 per-file re-hash, 0 mismatch/0 missing; tree shas re-derived and the method self-validated against v0.28.7's published hashes (3/3). Daemon-only release — all three payload trees identical to live v0.28.7.
- **Launcher**: staged `c4f640e1…` vs live `a3a44461…` (both claims confirmed).

## S2 — Isolated boot smoke: PASS (verdict basis = boot r3; 2 earlier attempts were sandbox-shape corrections, zero live contact ss-proven)

Scratch sandbox: COPY of binary+payload at `/tmp/phh-sv2-smoke/install` (sha re-verified post-copy), scratch native PG (`127.0.0.1:15432/phh_smoke`, throwaway, dropped at teardown), **staged launcher** booting the normal path under a `systemd-run` transient unit (`phh-sv2-smoke.service` — cgroup proof: `/proc/<pid>/cgroup = 0::/system.slice/phh-sv2-smoke.service`, no ensemble-main), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicit** (proven in `/proc/<pid>/environ`), full per-variable scrub (no BLUEPRINT_*/API-key/PG*/SYSTEM_ENCRYPTION_KEY leakage), `ENSEMBLE_SELF_ENV=sandbox`.

| assertion | evidence |
|---|---|
| banner | `Starting Ensemble v0.28.8` @ +5 s |
| livez :17800 | 200 `{"status":"alive",…,"version":"0.28.8"}` |
| readyz | 200 READY — database/queue_freshness/services/checkpoint_saver all true, `reasons:[]`, grace false, leak false, MCP context7/webfetch/plane all ok |
| sweep no-op | fresh install ⇒ journal write-lazy absent; resolved journal path IS scratch; zero rollback/quarantine lines (deviation: "state.json exists" sub-assert N/A on fresh installs — path-resolution + zero-action proof stands) |
| resolved root | 13/13 log paths inside scratch; `grep /home/nea/agents-ensemble` in smoke log = 0 hits |
| DB fencing | repo + checkpointer engines on scratch PG (pg_stat_activity `ensemble-checkpoint-adapter` on scratch); root-ss: zero connections to 127.0.0.1:5432 ever |
| floor constants | `2147c0298` staged=1/live=0; `953603108` staged=0/live=1 — exact expected shape |
| daemon surfaces (9/9) | ENSEMBLE_UPGRADE_SERVING_GREEN_CLOSER 2/0 · serving_green_commit 5/0 · quarantine_cleared_serving_green 4/0 · rollback_requested 5/0 · serving_unhealthy 5/0 · _closer_enabled 2/0 · serving_green_closer 2/0 · M5 latch `_closer_warn_active` 1/0 + `_closer_warn_transition` 2/0 |
| launcher surfaces | SWEEP_SERVING_GREEN_ENABLED 3/0 · _js_sweep_flipped_disposition (T15) 6/0 · handback refs ×6 staged / 0 live · **correction:** `_js_count_rollback` PRE-EXISTS ×3 in live launcher (the rider adds a 4th site); `HANDBACK_VERIFY_BUDGET_S` is a **daemon-side** knob (staged daemon corpus 5/live 0), not a launcher token |
| teardown | SIGTERM-only (0 s exits, no SIGKILL anywhere), no orphans, ports 17800/15432 freed, scratch DB/role dropped, dirs removed |

**Smoke disclosures:** r1 = crash-loop diagnostic (binary resolves `./config.yaml` from CWD — `ENSEMBLE_CONFIG`/cwd must be pinned; house smoke-recipe note re-confirmed) and incidentally exercised the launcher restart-loop contract (backoff doubling, burst-abort, halt) correctly; r2 = daemon auto-wrote a default-template `data/ensemble.json` pin (`localhost:5432/ensemble`) on the fresh install — **no connection was ever established** (empty-password vs password-requiring server, root-ss proven); fixed by pre-seeding the file-pin (the file pin outranks env by design). **Observation routed (informational, pre-existing config behavior, not this release):** a fresh install auto-writing a default-template DB pin could surprise env-only fresh deployments — owning lane may want a note. Transient unit ran as root (system-manager default; cgroup isolation unaffected). r2's `checkpoint_saver:true` was a shallow pass on an unconnected pool (consistent with routed NOTE-8). OPENAI_API_KEY set to a non-functional placeholder (real ambient key scrubbed).

## S3 — Live fence: HELD (byte-identical at every poll)

`state.json` sha `50b73e20…` identical PRE / MID×2 / MID-2×2 / POST×2 (post-teardown); `current → releases/v0.28.7` unchanged; livez :9797 v0.28.7 uptime monotone across all polls; :9797 contact = read-only GET /livez only; :8088 untouched. Recorded for the ceremony: stale rollback window (2/3, window_start 2026-11-01), `quarantined=[v0.28.5]` (target NOT quarantined).

## S4 — Ceremony inputs: **precondition UNMET at verification time — user-owned, must run BEFORE the ceremony**

- Command: `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.28.8 bash scripts/upgrade/promote.sh live --f2-verified-closed`
- **Precondition:** live `.env:107` pins `ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade` @ `22fe83317` — **309 commits BEHIND floor `2147c0298`** (is-ancestor rc=1 the required direction). Empirical guard verdict with the staged code: floor=`2147c0298` ⇒ **refuse (stale-scripts)**; control (staging-wt scripts) ⇒ arm. Two consequences: (1) the executor would run the pinned dir's pre-cure `promote.sh`(+56)/`lib.sh`(+570) — the scripts-side half of the cure would not be exercised; (2) post-flip, the v0.28.8 daemon carries floor `2147c0298` while the explicit pin stays stale ⇒ **every subsequent arm refuses stale-scripts** (the warn carve-out applies only to fallback dirs without an explicit pin).
- **Remedy (ff-safe, ancestry-proven):** `git -C /home/nea/ensemble-src-wt-scripts-pin fetch origin && git -C /home/nea/ensemble-src-wt-scripts-pin checkout 84df6b55c` (or `2147c0298`). NOT performed by the tester — mutates the live-pinned tree; operator/user-owned.
- Manifest ceremony facts: `rollback_safe=True`, `known_schema_gen=20261031_120002…`, floor `2147c0298…`; stage log `rollback_safe=true source=explicit=true`, "NO flip performed (promote.sh owns current)".

## Evidence roots

`/tmp/phh-sv1/` (SUMMARY.md, manifest-rehash.tsv ×5363, migration censuses, baseline logs, state snapshots) · `/tmp/phh-sv2/` (EVIDENCE-SUMMARY.md, boot logs ×3, 8 scripts, fence polls).
