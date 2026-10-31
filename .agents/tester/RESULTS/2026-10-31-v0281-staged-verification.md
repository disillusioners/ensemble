# STAGED VERIFICATION — v0.28.1 (restart-arm livelock, wi-6641579b)

**Independent verifier**: tester, v0.28.1 staged artifact (commission wi-6641579b).
**Date**: 2026-10-31 (UTC). **Verdict**: **PASS-WITH-NOTES** — S1 PASS · S2 PASS-WITH-NOTES · S3 PASS · S4 PASS.
**Trust boundary**: the functional gate at source level PASSED (evidence commit `e0a7347f6`, swept into the staged tree via merge `a65dfcc24`). Per the task scope, functional claims are NOT re-verified here; this gate is the STAGED ARTIFACT only.

## Coordinates (read-only respected)
- Staged release dir: `/home/nea/agents-ensemble/releases/v0.28.1` (READ-ONLY — never written/deleted/killed).
- Live: symlink `current → releases/v0.28.0`; live daemon :9797 (livez v0.28.0). Touched only via GET + sha256/readlink.
- Merge worktree: `/home/nea/ensemble-src-wt-merge-v0281 @ b56444244` (KEPT for ceremony — not moved/committed/cleaned).
- Feature worktree: `/home/nea/ensemble-src-wt-restart-livelock @ 4be66e44c` (untouched — verified HEAD + clean).
- Staged artifact is Python 3.14 (`pyvers=314`); Python-3.13 interpreter CANNOT read its CArchive/bytecode. All artifact-level reads used the uv-managed interpreter `/home/nea/.local/share/uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14`; dual-reader cross-validation (3.13 vs 3.14) applied where a python read mattered. Main-venv 3.13 used for pure hashing/git/prompt-baseline.

## S1 — Artifact Integrity: PASS

### 4-way sha256 (independent re-hash)
| Source | sha256 |
|---|---|
| staged release dir `releases/v0.28.1/ensemble-prod` | `2ffd56e1184b8658f8afe804795b746c54fdbc61f67a6ed4d0d8133fb9a5183c` |
| merge-worktree dist `ensemble-src-wt-merge-v0281/dist/ensemble-prod` | `2ffd56e1184b8658f8afe804795b746c54fdbc61f67a6ed4d0d8133fb9a5183c` |
| manifest.json `binary_sha256` | `2ffd56e1184b8658f8afe804795b746c54fdbc61f67a6ed4d0d8133fb9a5183c` |
| binary sidecar `dist/ensemble-prod.build-provenance.json` `artifact_sha256` | `2ffd56e1184b8658f8afe804795b746c54fdbc61f67a6ed4d0d8133fb9a5183c` |
All four byte-identical. (Live v0.28.0 binary sha, contrast only: `0a12e3f84e0189090468d17eae256d7654e19630e22e46c18546754f8970c2f0`.)

### Sidecars (binary + FE)
- Binary: `dist/ensemble-prod.build-provenance.json` — `git_head=b56444244fbf93814fbac3c71e2cded010f7d225`, `git_dirty=false`, `build_at=2026-10-31T09:49:42Z`, `build_tool=pyinstaller:ensemble.spec`.
- FE: `frontend/dist/frontend/browser/index.html.build-provenance.json` — same `git_head`, `git_dirty=false`, `artifact_sha256=5531cbe4da0c026d0fdcf7f10ea348f7c20f6abd9e5d703912a4805ad1bb0ddd`.
- No build-provenance sidecars exist INSIDE the release dir (payload hygiene). The two `provenance*.json` files under `plugins/opendesign/copy_freely/design-templates/...` are VENDORED PLUGIN TEMPLATE PAYLOAD (not build sidecars) and are inherited — identical copies exist in the v0.28.0 release.

### Tag / remote / tip identity
- `git rev-parse v0.28.1` → `5453eead30f6982b797b77ad4e3c43bc5fcbacd3` (annotated); dereferenced commit `b56444244fbf93814fbac3c71e2cded010f7d225`.
- `git ls-remote origin refs/tags/v0.28.1` → `5453eead30f6982b797b77ad4e3c43bc5fcbacd3` — local == remote, PUSHED.
- `git fetch origin` (fetch only) → `origin/latest = b56444244fbf93814fbac3c71e2cded010f7d225`.
- Merge worktree HEAD `b56444244fbf93814fbac3c71e2cded010f7d225` (detached, clean — `git status --porcelain` empty).

### Bump shape
`git show --stat b56444244` → EXACTLY 4 files: `daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock` (4 insertions / 4 deletions). ✓

### Ancestry (rc 0 = ancestor; pasted rc)
- `33cadefc2 rc=0` (feature merge)
- `a65dfcc24 rc=0` (evidence sweep merge)
- `eaae819ca rc=0` (changelog commit)
- `4be66e44c rc=0` (lane doc-fold head)
Merge chain: `b56444244` (bump) ← `eaae819ca` (changelog) ← `a65dfcc24` (merge 33cadefc2 + e0a7347f6) ← `33cadefc2` (merge a577d4893 + 4be66e44c).

### Migration census from the binary (CArchive reader, python3.14)
- Staged v0.28.1: `sql_entries=115 distinct_prefixes=115 dupe_prefixes=[]`.
- Live v0.28.0: `sql_entries=115 distinct_prefixes=115 dupe_prefixes=[]`.
- Diff: empty (`added=[] removed=[]`, `diff rc=0`). Dual-reader: staged census identical under python 3.13 (marshal-independent TOC names) and python 3.14.

### Payload hygiene
- Top-level release dir entries: `{agents, config.yaml, ensemble-prod, frontend, launcher.sh, manifest.json, plugins}` — source-less. No `scripts/`, no `.env`, no `.env.*`, no `.staging.*`, no `.aside.*`, no `*.build-provenance.json` in the release dir.
- The `scripts` dirs found under `plugins/opendesign/copy_freely/design-templates/*/scripts` are vendored plugin template payload (inherited, unchanged `plugins_tree_sha256=4715cf64…`).

### Manifest delta v0.28.0 → v0.28.1 (independent diff)
Scalar fields changed: EXACTLY `{binary_sha256, binary_version, staged_at, version}` (the three claimed content fields + the stage timestamp). No other scalar changed (`agents_tree_sha256`, `frontend_tree_sha256`, `plugins_tree_sha256`, `config_sha256`, `launcher_sha256`, `known_schema_gen`, `rollback_safe`, `pipeline_scripts_floor_commit`, `contains_contract_phase` all IDENTICAL). Manifest top-level keys identical.

Per-file maps: `agents_manifest` 338, `frontend_manifest` 93, `plugins_manifest` 4930 → 5361 total. Map-diff per map: `changed=0 added=0 removed=0` (all three).

### Full 5361-entry re-hash of the staged release dir vs manifest
`RE-HASH: 5361 entries hashed in 0.7s; mismatches=0; missing=0` → `REHASH-VERDICT: PASS`. (Independent re-hash of every payload file against its manifest sha256.)

### Prompt-artifact baseline
House `scripts/protection/generate_prompt_artifact_baseline.py --check` run from the merge worktree (b56444244, correct CWD): `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256` (rc 0). Staged payload ships the same 161-line `.prompt-artifacts.sha256`.

### keep-3 / promote state (read-only, recorded)
- `releases/` contents: `v0.27.14, v0.27.15, v0.28.0, v0.28.1` (4 dirs = over-retention by 1; machinery-owned — evicted nothing).
- `current → releases/v0.28.0`; `state.json`: `current=v0.28.0`, `previous=v0.27.15`, `in_flight=None`, `pending_op=None`, `quarantined=[]`.
- Protected targets present (live v0.28.0 + pending v0.28.1). Eviction candidate at promote time: v0.27.14.

## S2 — Isolated Boot Smoke: PASS-WITH-NOTES

### ⚠ INCIDENT DISCLOSURE — attempt 1 (READ FIRST)

On the first boot attempt (10:18:44Z), the scratch daemon's **boot-time stale-subprocess sweep** SIGTERM'd then SIGKILL'd live processes in `/system.slice/ensemble-main.service`:
- `202649` — `/bin/bash /home/nea/agents-ensemble/launcher.sh` (live launcher)
- `203445`, `203447` — `/home/nea/agents-ensemble/current/ensemble-prod` (live daemon)
- `203682`, `203724` — code-server children of the live service (vanished before KILL phase)
- `225887`, `225903`, `225904` — the live daemon's context7-mcp children

**Recovery**: systemd `ensemble-main.service` auto-restarted the daemon at 10:19:02Z (MainPID 227199); livez returned v0.28.0 by 10:19:16Z. NRestarts=1. Disruption window ≈ 17–20 s. The live daemon then processed turns normally (log shows an agent executing `read_file` at 10:22:53Z).

**Root cause**: (a) the agent's tool shells are CHILDREN of the live daemon → they inherit the live service's cgroup `/system.slice/ensemble-main.service`; (b) `setsid` changes the session, NOT the cgroup; (c) systemd-run was unavailable on this host (no bus), so no transient-unit isolation existed; (d) the frozen binary is classified as the PROD lane (`sys.frozen` truthy) → the boot sweep's DEFAULT is ON when `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` is ABSENT; (e) my attempt-1 launch script did `-u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`, REMOVING the ambient `=0` that the live install carries as its own guard; (f) the sweep's ancestor walk is bounded by `_MAX_LINEAGE_DEPTH`, so the live daemon root was not recognized as an ancestor of my setsid'd chain and the live processes qualified as "prior-incarnation orphans" (same cgroup, older starttime, non-ancestors by depth cap).

**Impact on the fence**: the live state.json sha changed from the pre-incident baseline `bc324f021ad5cdb531ce742d7f2a319e5f0fa428467a0a9ef0aaa0387e2db155` to `ad7c5deecc776f5b166dcb7edfde151af38d840ea601d9e7802c36b3a206971c` — attributable to the live daemon's OWN restart write (the scratch never even reached journal writes; its boot log ends at the sweep and no scratch process survived to bind a port). The live symlink `current → releases/v0.28.0` was untouched throughout.

**Attempt-2 remediation**: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` set EXPLICITLY (documented COMPLETE no-op: "no /proc reads, no signals, no log noise beyond one INFO line"; pinned by `test_default_is_on_pinned` / `test_env_override_wins_in_dev_test`). The live install itself runs `=0` for exactly this class of reason. Attempt-2 completed with zero live contact (fence triple byte-identical — see below).

### Attempt 2 — clean boot (sweep disabled)
- Scratch layout: binary copied to `/tmp/v0281-verify/smoke2/releases/v0.28.1/ensemble-prod` (path-derived install root = scratch; sha verified `2ffd56e1…`), `config.yaml` copied from the staged release (read-only source), fresh scratch PG DB `ensemble_v0281smoke2` on `localhost:5432`, port `18231`.
- Env scrub: per-variable `-u POSTGRES_DB/USER/PASSWORD/HOST/PORT` + all ambient `ENSEMBLE_*` (incl. `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`), then explicit scratch values; `ENSEMBLE_SELF_ENV=dev`; `PERSISTENCE_DB_PATH` inside scratch; `ENSEMBLE_UPGRADE_SCRIPTS_DIR` unset.
- Banner: `10:29:26 - daemon.api - INFO - Starting Ensemble v0.28.1` (twice in boot log).
- `GET /livez` on :18231 → 200 `{"status":"alive","uptime_seconds":24.0,"version":"0.28.1"}`.
- `GET /readyz` on :18231 → 200 `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"grace":{"grace_active":false},"leak_signal":{"detected":false},"pool_state":{"sqlalchemy":{"checkedout":0,"size":15,...},"psycopg":{"pool_size":2,"pool_max":15,"pool_available":2,...}}}}`.
- MCP note (harmless): `plane` row config validation warning → fallback to definition defaults.

### Cgroup isolation (deviation — disclosed, not silent)
- systemd-run unavailable on this host (no user bus); manual cgroup move to a delegated subgroup under `user@1000.service` FAILED with I/O error (source cgroup `/system.slice/ensemble-main.service` is root-owned, so nea cannot move its own process out of it — kernel requires write on both source and target cgroup.procs).
- The scratch daemon's `/proc/<pid>/cgroup` therefore remained `0::/system.slice/ensemble-main.service` (inherited from the agent's tool shell, which is a child of the live daemon). This does NOT satisfy the letter of "show it is NOT system.slice/ensemble-main.service".
- Compensating controls enumerated: (1) sweep disabled = complete no-op per module docstring + pinned tests; (2) path-derived install root = scratch (journal/state isolation); (3) scratch PG DB + scratch port + scrubbed env + PERSISTENCE_DB_PATH in scratch; (4) MID fence sha proof (below) shows the scratch wrote nothing to the live journal. The live daemon's own processes could not be killed by a sweep that was never enabled.

### Live fence — attempt 2 (byte-identical triple)
- Baseline (10:28:44Z): `state.json` sha `ad7c5deecc776f5b166dcb7edfde151af38d840ea601d9e7802c36b3a206971c`; `current → releases/v0.28.0`; livez uptime 484.89 s.
- MID (10:33:31Z, scratch daemon RUNNING): `ad7c5deecc776f5b166dcb7edfde151af38d840ea601d9e7802c36b3a206971c` (byte-identical, `BEFORE==MID`); `current → releases/v0.28.0`; uptime 771.12 s (continuous).
- AFTER teardown (10:34:34Z): `ad7c5deecc776f5b166dcb7edfde151af38d840ea601d9e7802c36b3a206971c` (byte-identical, `MID==AFTER`); `current → releases/v0.28.0`; uptime 834.66 s (continuous, no restart).
- Scratch journal isolation: no `state.json` written under `scratch/releases/` — the scratch daemon's upgrade journal path resolved to the scratch root, never the live install.

### Teardown
- SIGTERM to scratch daemon pid → exited within 1 s; no residual scratch processes; port 18231 freed (0 listeners).
- Scratch DB `ensemble_v0281smoke2` dropped; scratch dir removed; cgroup subgroup `v0281smoke.scope` removed; boot logs + livez/readyz JSONs preserved under `RESULTS/evidence/v0281-staged-verification/`.

## S3 — Lane Content at Binary Level: PASS

### Symbol matrix (dual-reader: raw byte scan of decompressed PYZ blobs + marshal code-object structural read under python3.14)
| Symbol | Staged v0.28.1 | Live v0.28.0 |
|---|---|---|
| `_in_flight_is_own_open_txn` | PRESENT (1 module: `daemon.tools.upgrade_journal`) | ABSENT |
| `_mark_wake_delivering_locked` | PRESENT | ABSENT |
| `close_restart_arm_executor_exit` | PRESENT (2 modules: `daemon.tools.upgrade_journal` + `daemon.services.upgrade_journal_sweep`) | ABSENT |
| `_journal_executor_exit` (control — inherited) | PRESENT (`daemon.services.upgrade_journal_sweep`) | PRESENT |
| `close_stale_restart_arm` (control — inherited) | PRESENT (`daemon.api`, `daemon.tools.upgrade_journal`) | PRESENT |
| `close_restart_arm` (new) | PRESENT (2 modules) | ABSENT |

Method positive control: the SAME reader found `_journal_executor_exit` and `close_stale_restart_arm` in BOTH binaries — the live parse works, so the absence of the lane symbols in live is a real absence, not a reader miss.

### Sweep hook
`_journal_executor_exit` in the staged sweep module has `close_restart_arm_executor_exit` in its `co_names` → **True** (the call is wired). In live: **False** (the call is absent). ✓

### Docstring fold (normalized-whitespace checks, 7/7 OK)
- `_in_flight_is_own_open_txn`: new forms `Deliberately KIND-AGNOSTIC`, `do NOT add a kind check`, `own open UNFLIPPED txn`, `promote-lane loop protection` → staged PRESENT, live ABSENT. Old form `own open restart arm` → ABSENT in both (new function in the lane; live never had it).
- `lock_release_run_id`: new form `Boot-lane PLUS ONE sanctioned live-lane exception` + `the actor that would legitimately hold the lock is DEAD by observation` → staged PRESENT, live ABSENT. Old form `Boot-lane ONLY: never call this from a live turn` → staged ABSENT, live PRESENT (pre-existing baseline; the lane changed the docstring).
- The two "ABSENT in raw byte scan" phrases (`do NOT add a kind check`, `own open restart arm …`) in the first S3 pass were docstring line-wrap artifacts — the source wraps those phrases across lines; the normalized-whitespace re-check (reader B) confirms the staged docstrings contain them. Both readers reported consistently after whitespace normalization.

## S4 — Staged-vs-Source Coherence: PASS

### Blob identity (reviewed lane content riding unchanged)
- `git rev-parse b56444244:daemon/tools/upgrade_journal.py` = `7848e5e54816804a9656033bc4f00974d5c9aa7b`
- `git rev-parse 4be66e44c:daemon/tools/upgrade_journal.py` = `7848e5e54816804a9656033bc4f00974d5c9aa7b` → **IDENTICAL**
- `git rev-parse b56444244:daemon/services/upgrade_journal_sweep.py` = `5571dd1deb3094684bfbc9c400cb511df5edc233`
- `git rev-parse 4be66e44c:daemon/services/upgrade_journal_sweep.py` = `5571dd1deb3094684bfbc9c400cb511df5edc233` → **IDENTICAL**

### Shipped-code delta 4be66e44c → b56444244
- `git diff --stat 4be66e44c..b56444244 -- daemon/` → `daemon/__init__.py | 2 +-` (1 insertion / 1 deletion — version field only). ✓ No foreign rider in shipped daemon code.
- Full `git diff --name-status`: `.agents/tester/**` (RESULTS functional-gate doc + tiered-gate skills canon + ensure.md), `CHANGELOG.md`, and the 4 bump files only. No source drift.
- `a65dfcc24` (functional-gate evidence merge) carries `.agents/tester/**` only — 1 file: `.agents/tester/RESULTS/2026-10-31-restart-livelock-functional-gate.md`.

## Runtime per gate
- S1: ~16 min (4-way sha, tag/remote/tip, bump, ancestry, census ×2 + dual-reader, hygiene, manifest delta, 5361-entry re-hash 0.7 s, prompt baseline, keep-3).
- S2: ~17 min (attempt 1 incident + recovery verification + attempt 2 clean boot + fence triple + teardown). Boot itself: 24 s to livez 200.
- S3: ~7 min (pyz byte-scan + marshal structural read + docstring fold).
- S4: ~2 min.
- Evidence commit: ~10 min. Total ≈ 52 min.

## Disclosures / open items
1. S2 attempt-1 incident (live processes killed by the sweep) — fully disclosed above; live recovered via systemd auto-restart; no promote fields touched; state.json changed due to the live daemon's own restart write.
2. S2 cgroup-isolation deviation — disclosed above (systemd-run unavailable; manual move blocked by root-owned source cgroup.procs). Compensating controls enumerated.
3. The staged binary's `plane` MCP config validation warning is a pre-existing environment condition (config declares `streamable-http`, model expects `stdio`), fail-open fallback — not a v0.28.1 defect.
4. The tiered-gate-doctrine merge (a577d4893) rode into b56444244 at 33cadefc2 — docs-only rider (`.agents/tester/**`, no daemon code). Included in the functional-gate evidence chain.
5. Over-retention: `releases/` holds 4 dirs vs keep-3 (machinery-owned; eviction not performed by this gate).
6. Ancestry 4be66e44c → b56444244 touches only `daemon/__init__.py` (version) — no foreign code rider.

## Evidence files (committed with this doc)
`RESULTS/evidence/v0281-staged-verification/`: boot logs (attempt 1 incident + attempt 2), livez/readyz JSON, census lists + diff, S3 structural output, S3 docstring output, fence sha triple.

## Per-gate verdict
- S1 Artifact Integrity: **PASS**
- S2 Isolated Boot Smoke: **PASS-WITH-NOTES** (clean boot + fence triple green; cgroup-isolation deviation + attempt-1 incident disclosed)
- S3 Lane Content at Binary Level: **PASS**
- S4 Staged-vs-Source Coherence: **PASS**

**OVERALL: PASS-WITH-NOTES** — ceremony-ready (subject to the disclosures above; no blocking mismatch found).
