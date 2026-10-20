# Staged Verification — Release v0.25.5 (2026-10-20)

**Staging:** `/home/nea/agents-ensemble/releases/v0.25.5` · `staged_at 2026-10-20T00:14:37Z` · `stage.sh` exit 0 (live mode, `ENSEMBLE_ROLLBACK_SAFE=1` — legitimate: migration delta vs v0.25.4 verified EMPTY).
**Provenance source:** merge `fadf1f69c25a2c464edc800cf55aad340b1d76f2` (--no-ff, 9 branch commits `6ed385752..7592d35ff`) → bump `b85804a152c3…` (chore(release): bump version to 0.25.5). Both pushed to origin; tag `v0.25.5` local-only (`cb160cac54df9bdb8919b6c825dbb6cb74b0bca9` → `b85804a15…`); `git ls-remote origin refs/tags/v0.25.5` **EMPTY**. Audit-trail branch `fix/job-scoped-answer-resolution` pushed, local branch + fix worktree removed; staging worktree `/home/nea/ensemble-src-wt-stage-v0255` RETAINED so sidecars survive the promote ceremony (closes the v0.25.2 sidecar-loss lesson).
**Runner / fences:** all hashing + git inspection in MAIN checkout + stage worktree; read-only throughout — no DB sessions, no daemon starts, no `systemctl`, no signals, no writes outside `/tmp/jaaf-s{1,2}/`. Stage-2 boot smoke: scrub → source main-dev `.env` (POSTGRES_DB=ensemble_dev asserted), FAKE_HOME=mktemp+escape, port 8079 verified free before, freed after, TERM only the exact child PID (triple-verified own binary + own port + own env).

## VERDICT: 🟡 PASS-WITH-NOTES — payload integrity + ancestry + boot smoke ALL PASS; 3 disclosed operational notes below, none affecting the staged artifact.

---

## 1. Manifest vs on-disk re-hash (independent) — ✅ ALL MATCH

| Field | My value | Manifest / claim | Verdict |
|---|---|---|---|
| Staged dir inventory | `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/` (root set exactly 4 non-keyed files) | as listed | ✅ PASS |
| Binary `sha256` | `529a0196d4d64d1c99f9b82f77a92677292eef445dc7cc81038c9ae89c6c6dd8` | manifest == same; given `529a0196…c6dd8` | ✅ **triple-match** (manifest + sidecar + re-hash) |
| Binary size | `80135520` B | claim `80,135,520` B | ✅ PASS |
| `binary_version` | `0.25.5` (manifest field; `daemon/__init__.py:3` + `pyproject.toml:3` @ `b85804a15`) | claim `0.25.5` | ✅ PASS |
| `agents_tree` | `4c1a89a5389e16027f5ec93617fe13581e4b503ea3d0ffa0a4aa3868ed547631` | manifest == same; claim == same | ✅ PASS (339/339 files, 0 per-file mismatch) |
| `frontend_tree` | `8d6afeaa8c7fda70acbc05deec17fca89e1a43256179c8e6405fd35a2293ae03` | manifest == same; claim == same | ✅ PASS (92/92, 0 mismatch) |
| `plugins_tree` | `4715cf64dad62d209c91206c560e475d7fa4cec8f67f6182ffbcd21b723685a7` | manifest == same; claim == same | ✅ PASS (4930/4930, 0 mismatch) |
| `config` / `launcher` | `666a70d9…5f43` / `a3a44461…bffebd3f` | manifest == same | ✅ PASS |
| `fe_index` | `a017dd2135b0f532b382a8842b3b39e650763cb605b7426f356c2c4f9ba85af1` (9257 B) | claim `a017dd21…` | ✅ PASS — **byte-identical across v0.25.1/2/3/4/5** (5-way chain; daemon-only release) |
| `known_schema_gen` | `20261019_120822_alter_resource_monitor_booleans.sql` (git tree head at `b85804a15`) | manifest == same | ✅ PASS |

**Replicated recipe (citation):** tree re-hash = `scripts/upgrade/stage.sh:441-446` → `lib.sh::_tree_manifest` + `_tree_hash_of_lines` (`find . -type f`, symlinks excluded, strip `./`, **sort** relpaths, `"<sha256>  <rel>"` two-space, sha256 of the whole block *including trailing newline*). Manifest location = `<REL>/manifest.json`. `fe_index` location = `frontend_manifest["dist/frontend/browser/index.html"]` → `<REL>/frontend/dist/frontend/browser/index.html`. Algorithm **self-validated against published v0.25.3 values** before touching v0.25.5 (reproduced all three tree hashes exactly). Precedent: `RESULTS/2026-10-19-v0252-staged-verification.md` §2-§4, §5, §9 + `RESULTS/2026-10-19-boot-orphan-sweep-r2-merged-staged-verification.md` Item 4.

**Bonus — payload↔git equivalence (stronger than any payload hash):** staged `agents/` (339) and `plugins/` (4930) blob-ids vs `git ls-tree -r b85804a15` → **0 missing, 0 blob mismatch, 0 payload-only files**. `git diff --name-only 630cefebb..b85804a15 -- agents/ frontend/ plugins/` → **EMPTY** (zero payload drift; the change is daemon-code + tests + `.agents/` evidence only, 42 files / +8133 / −46).

## 2. Migration delta vs v0.25.4 — ✅ EMPTY (105==105)

| | count | diff |
|---|---|---|
| `daemon/migrations/versions/` @ `b85804a15` | 105 | — |
| `daemon/migrations/versions/` @ `630cefebb` | 105 | — |
| `git diff --stat 630cefebb..b85804a15 -- daemon/migrations/versions/` | — | **EMPTY** |

`known_schema_gen` (= head migration at `b85804a15`) = `20261019_120822_alter_resource_monitor_booleans.sql` (matches manifest + claim). v0.25.4 baseline vs its own git tree: agents 339/339 + plugins 4930/4930, 0 blob mismatch, 0 payload-only — baseline intact.

## 3. Composition / ancestry — ✅ ALL PROVEN

### Provenance sidecar (binary, verbatim):
```json
{
  "git_head": "b85804a152c378b017e08128bce1da886196774c",
  "git_head_short": "b85804a152c3",
  "git_dirty": false,
  "build_at": "2026-10-20T00:07:46Z",
  "build_tool": "pyinstaller:ensemble.spec",
  "artifact_sha256": "529a0196d4d64d1c99f9b82f77a92677292eef445dc7cc81038c9ae89c6c6dd8",
  "artifact_path": "dist/ensemble-prod"
}
```
FE sidecar: same `git_head`, `git_dirty:false`, `build_at 2026-10-20T00:05:31Z`, `artifact_sha256 a017dd21…`. Stage worktree `git status --porcelain` = **0 lines** → `git_dirty:false` is truthful. **RETAINED in stage worktree** — closes the v0.25.2 sidecar-loss lesson; the attestation is *quotable*, not entailed.

### Ancestry (`git merge-base --is-ancestor <X> b85804a15`):

| X (commit) | Role | rc |
|---|---|---|
| `6ed385752` | fix(answer): resolve job-scoped answers to the asking child instance | 0 ✅ |
| `e71477e75` | fix(answer): single candidate-collection pass in the asker resolver | 0 ✅ |
| `ab6d2727f` | fix(answer): route a pack-id answer to its holder regardless of pack status | 0 ✅ |
| `829feb94d` | test(answer): real query-counter discriminators + contract-doc truthfulness | 0 ✅ |
| `fadf1f69c` | merge: job-scoped answer resolution fix | 0 ✅ |
| `7592d35ff` | (tester gate-evidence commit, second merge parent) | 0 ✅ |

**Merge `fadf1f69c`:** parents `630cefebb…` (v0.25.4 bump tip) + `7592d35ff…` (tester evidence). Message: `merge: job-scoped answer resolution fix (fix/job-scoped-answer-resolution)`.

### Binary marker (belt-and-braces, bounded 60s):
`timeout 60 strings ensemble-prod | grep -m3 answer_resolver` → **HIT** — `daemon.services.answer_resolver` present in the compressed PyInstaller bundle (bonus confirmation that the fix module reached the staged binary).

## 4. No-promote invariants — ✅ ALL HOLD

| Check | Value | Verdict |
|---|---|---|
| `readlink -f /home/nea/agents-ensemble/current` | `releases/v0.25.3` | ✅ PASS — not flipped to v0.25.5 |
| `releases/state.json` | `current=v0.25.3`, `previous=v0.25.1`, `in_flight=null`, `pending_op=null`, `pending_restart=null`, `pending_actions={}`, `pending_wakes={}`, `quarantined=[]`; **mtime `2026-10-19 18:00:20Z`** | ✅ PASS — last event = v0.25.3 `supervision_boot` epoch; **zero 2026-10-20 entries** |
| `ls releases/` | `v0.25.1 v0.25.2 v0.25.3 v0.25.4 v0.25.5` | ✅ PASS — all 5 present (v0.25.0 evicted per keep-3, expected) |
| Live daemon continuity (read-only) | PID **969387**, started **2026-10-19T17:59:58Z** (`/proc/969387/stat` field 22 = 3,803,789 ticks @ CLK_TCK 100; btime 1,792,394,761), uptime 6h27m, cmdline `/home/nea/agents-ensemble/current/ensemble-prod`, `:9797` listener | ✅ PASS — **continuous, zero `systemctl`, zero signals** |
| Tag `v0.25.5` (local-only) | object `cb160cac54df9bdb8919b6c825dbb6cb74b0bca9` → `b85804a15…`; `git ls-remote origin refs/tags/v0.25.5` **EMPTY** | ✅ PASS |
| Pre-promote gate | `journalctl -t ensemble-treecheck --since "-10 min"` → `-- No entries --` | ✅ PASS — green |
| Files in `releases/v0.25.5/` modified | **0** | ✅ PASS — staged payload byte-stable, not promoted |

## 5. Optional staged-binary boot smoke — ✅ PASS

Boot path chosen: **C (fallback)** after `ensemble_dev ` ``ensemble_sandbox`` absent (count=0), `CREATE DATABASE` **denied** (role `ensemble_dev_app` lacks CREATEDB), pre-existing `ensemble_dev_scratch` privilege-incompatible (owned by role `ensemble`, my role lacks CREATE). Path C used the sanctioned dev sandbox `ensemble_dev` — pre-verified **IDLE** (0 `task`, 0 `message_queue`, all 1142 `job_queue_items` `done`, 0 instances RUNNING/PROCESSING/PAUSED, 0 active connections) so the boot could not interfere with anyone else's work.

**First attempt disclosed:** I initially targeted `ensemble_dev_scratch` — daemon died with `InsufficientPrivilege: permission denied for table instances` on `CREATE TABLE stored_watches … REFERENCES instances`. Root cause: `ensemble_dev_scratch` is **not** empty (55 tables owned by role `ensemble`); my earlier `information_schema.tables` read was **privilege-filtered** to zero. The privilege-independent catalog (`pg_class`) exposed it. **Residue from the failed attempt: none** (`app_owned_relations=0`, `total_tables=55` unchanged, boot never reached an instance insert).

**Gate results:**
- `/livez` → **200** — `{"status":"alive","uptime_seconds":7.697,"version":"0.25.5"}`
- `/readyz` → **200** — `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"inflight_turns":0},"draining":false}`
- Banner (verbatim, ×2 dual sites): `00:38:33 - daemon.api - INFO - Starting Ensemble v0.25.5`
- Migration observation (EMPTY as the manifest predicts):
  - `Skipping migrations for non-SQLite database (schema evolution handled by EnsembleManager._ensure_postgres_columns)`
  - `_ensure_postgres_columns: resource_monitor boolean repair complete (applied=[], already_ok=['resource_instance_sample.estimated=boolean', 'resource_limit_policy.enabled=boolean', 'resource_limit_policy.user_gated=boolean'], unrecognised=[], missing=[])`
  - `checkpoint trio autovacuum reloptions ensure complete (applied=0, already_set=3, table_missing=0)`
- Error greps over 324-line log: `42804` 0 · `DatatypeMismatch` 0 · `InsufficientPrivilege` 0 · `Application startup failed` 0

**Cleanup proof:** port 8079 owner PID `1832045` **triple-verified as mine** (own binary, own POSTGRES_DB=ensemble_dev, own DAEMON_PORT=8079, own ENSEMBLE_SELF_ENV=dev) → SIGTERM → exited OK; bootloader parent `1832033` also gone; **port freed**. Residue = **ZERO**: instances 34→34, task 0→0, message_queue 0→0, job_queue_items 1142→1142, dependency_watchers 52→52, skills/projects 29/49→29/49, schema_migrations 0→0, all `resource_*` 0→0; no instance row created in the boot window. Live untouched: 9797 listener continuous (PID 969387 since 17:59:58Z, never signalled — the mid-evening pid churn was the live daemon's own forked children, ppid=969387, not mine); demo 7979 untouched; 8088 never bound; `current` symlink still `releases/v0.25.3`; `releases/state.json` mtime unchanged; **0 files modified inside `releases/v0.25.5/`**; **no promote performed**.

**Runtime:** `T0=2026-10-20T00:38:26Z → T2=2026-10-20T00:38:59Z` = **33 s** (healthy at +21 s; teardown ~7 s). Whole pack under `timeout 300`.

## 6. Discipline + fences honored

- **Env scrub (every command, from the first):** full 12-var ambient scrub (`POSTGRES_*`, `DATABASE_URL`, `ENSEMBLE_*`, `SSL_*`) — ambient verifiably carries live-prod poison in agent shells; the v0.25.5 path-C boot required `POSTGRES_DB=ensemble_dev` AND verified-asserted before any connection.
- **No live-shaped paths:** FAKE_HOME via `mktemp -d /tmp/jaaf-s2-home.XXXXXX`, escape-guard for every `rm -rf`; only `ensemble_dev` (the sanctioned dev DB) written, and the writes were the standard boot state rows the dev lane always produces (here: zero instance/task/message-queue writes — boot was a clean ensure + probe cycle).
- **No `systemctl`, no signals to live:** live PID 969387 untouched throughout. Boot only TERMed its own PID after triple verification.
- **No `pytest -x`, no broad commands:** SV-1 ran no pytest at all; SV-2 ran no pytest.
- **Every "N passed/M deselected" claim names files:** n/a for SV-1 (no pytest); SV-2 boot asserted specific livez/readyz status codes + version + banner line.
- **Sidecar retention:** staging worktree `ensemble-src-wt-stage-v0255` retained for the promote ceremony — the attestation is durable until promote; closes the v0.25.2 lesson.

## Disclosed notes (none weakening the verdict)

1. 🟡 **Path-C boot was on the sanctioned dev sandbox (`ensemble_dev`), not on `ensemble_sandbox` (absent) or a throwaway DB (`CREATE DATABASE` denied 42501).** `ensemble_dev` is the standard dev DB for this lane (dev.sh boots here daily); the boot performed a clean ensure + probe cycle, wrote zero instance/task/message_queue rows, and applied zero migrations (already-ok shape on the v0.25.4-era schema, as the EMPTY migration delta predicts). Disclosed honestly per project convention for any staging verification using DB-impacted paths.
2. 🟡 **Two targeted kill-switches used during the boot to protect the shared sandbox:** `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` (this shell shares the live daemon's cgroup — protects live's aux children from sweep) and `ENSEMBLE_ORPHAN_F1_ENABLED=0` (blocks orphan re-dispatch on the shared dev sandbox; log-confirmed). Both are narrow guards, documented.
3. 🟡 **MCP computer-use libXtst.so.6 (X11 XTest) missing** in this environment — 5 ERROR + 1 traceback, non-blocking (`context7` healthy, "MCP warmup complete: 2 server(s) ready"), same class as the v0.25.4 gate's disclosed MCP errors. Environmental, unrelated to v0.25.5.
4. 🟡 **First boot attempt against `ensemble_dev_scratch` failed cleanly** with `InsufficientPrivilege` (privilege-filtered empty read masked the populated state). Recovered with zero DB mutation; `ensemble_dev_scratch` total table count unchanged.

## Notes on what's NOT in this report

- **No `release_journal` mutation:** the giter's stage lane writes stage events to the journal; this is a read-only verification — the verify stage must NOT mutate the journal (it would defeat the post-stage promotion ladder's truthfulness).
- **No promote, no live ops:** the promote ceremony remains with the dispatcher's lane; this gate's job ends with the integrity verdict.
- **No `promote.sh` invocation:** zero `current`-symlink flips; zero `state.json` writes; zero `systemctl` calls.

## Gaps

None. All 6 commission items executed; payload integrity + ancestry + no-promote + boot smoke + sidecar retention all green. Deferred: `ensemble_sandbox` DB provisioning (would unlock sandbox-path boot isolation for future gates; independent housekeeping).

## Evidence

`/tmp/jaaf-s1/` — 29 files (hashes.txt · migration.txt · ancestry.txt · sidecar.json · nopromote.txt + 24 raw per-step outputs).
`/tmp/jaaf-s2/` — EVIDENCE.md, boot.log, livez.final, readyz.final, ss_before/during/after.txt, ps_before/during/after.txt, baseline_counts.txt, after_counts.txt, provisioning probe scripts + outputs.
Stage worktree `/home/nea/ensemble-src-wt-stage-v0255/` — **RETAIN** until the promote ceremony so sidecars survive (v0.25.2 lesson).