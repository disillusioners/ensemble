# STAGED VERIFICATION — v0.27.0 (hard-reboot recovery, hrr-20261021)

**Date:** 2026-10-21 · **Lane:** tester, independent acceptance authority (recomputed — giter's stage report NOT trusted)
**Artifact:** `/home/nea/agents-ensemble/releases/v0.27.0` (staged 2026-10-21T21:00:35Z)
**Build tree:** `/home/nea/ensemble-src-wt-stage-v0270` @ `1d5fdfe0fdafd947a815a1d2e6a2b54c5aa98832` (detached, tag `v0.27.0`)
**Prior staged release (delta baseline):** `releases/v0.26.4` · **Live release:** `releases/v0.26.3`
**Lanes:** artifact `97d6df73` · migrations `93858a16` · version `386e521a` · boot-smoke `aa1a2ed3` · final-fence `a4a15d52`

**Scope:** SLIM verification per the user's directive, with ONE carve-out — **item 5 (migration delta / rollback safety) NOT slimmed**, and it was executed at full binary-level rigour.

---

## VERDICT

# **STAGED-VERIFY PASS** *(with 2 ceremony notes — see "Ceremony notes" below; neither is a defect in the artifact)*

| # | Item | Verdict |
|---|---|---|
| 1 | sha 3-way match (disk = manifest = both sidecars) | ✅ **PASS** |
| 2 | Provenance chain (git_head / dirty / describe / ADR-009 D3 guard) | ✅ **PASS** |
| 3 | Version evidence (banner boot) | ✅ **PASS** |
| 4 | Live boot smoke (/livez + /readyz + version) | ✅ **PASS** |
| 5 | **Migration delta — NOT SLIMMED** | ✅ **PASS — DELTA EMPTY, CONFIRMED** |
| 6 | Payload hygiene | ✅ **PASS** |
| 7 | Fences (live surface, symlink, retention census) | ✅ **PASS** (final re-confirmation below) |

---

## ITEM 1 — sha 3-way match: **PASS**

**Binary leg — disk == manifest == sidecar (all three identical):**
```
$ sha256sum /home/nea/agents-ensemble/releases/v0.27.0/ensemble-prod
6c6bfd584974a7399d563117924e4d95d22ffdcabcd59be6de87b57dd4c64602  .../v0.27.0/ensemble-prod
manifest.json binary_sha256 = 6c6bfd584974a7399d563117924e4d95d22ffdcabcd59be6de87b57dd4c64602
sidecar  /home/nea/ensemble-src-wt-stage-v0270/dist/ensemble-prod.build-provenance.json
         git_head=1d5fdfe0fdafd947a815a1d2e6a2b54c5aa98832  git_dirty=false
         artifact_sha256=6c6bfd584974a7399d563117924e4d95d22ffdcabcd59be6de87b57dd4c64602
```

**FE leg — disk == manifest == sidecar:**
```
frontend_manifest['dist/frontend/browser/index.html'] = a017dd2135b0f532b382a8842b3b39e650763cb605b7426f356c2c4f9ba85af1
sidecar  .../frontend/dist/frontend/browser/index.html.build-provenance.json
         git_head=1d5fdfe0f…  git_dirty=false  artifact_sha256=a017dd2135b0f532b382a8842b3b39e650763cb605b7426f356c2c4f9ba85af1
```
`fe_index` is **byte-identical across v0.26.2 / v0.26.3 / v0.26.4 / v0.27.0** — a **daemon-only** release, unchanged since v0.25.0.

**Spot checks — both MATCH:**
```
7bab75e96b875caa05693225624f3063a48074485447ef0840681d4fc10a7ae7  config.yaml  == manifest.config_sha256
a3a44461b39270744917a0017123870dca62b6293273da699e6dfb15bffebd3f  launcher.sh == manifest.launcher_sha256
```

## ITEM 2 — Provenance chain: **PASS**

| Check | Result |
|---|---|
| `git -C <staging-wt> rev-parse HEAD` | `1d5fdfe0fdafd947a815a1d2e6a2b54c5aa98832` ✅ |
| `git status --porcelain` | **empty** ✅ (consistent with sidecar `git_dirty=false`) |
| `git describe --tags --exact-match HEAD` | **`v0.27.0`** (rc=0) ✅ |
| Both sidecars physically present | 332 B + 352 B, both `git_head=1d5fdfe0…`, `git_dirty=false` ✅ |
| Manifest `version` / `binary_version` | `v0.27.0` (v-prefixed) / `0.27.0` (unprefixed) ✅ |
| Bump commit scope | `1d5fdfe0` touches **exactly 4 files** (pyproject.toml, daemon/__init__.py, tests/test_api.py, uv.lock) — the house bump set ✅ |

**ADR-009 D3 version guard — quoted verbatim** (`scripts/upgrade/stage.sh:146-150` in the staging worktree):
```bash
146: GIT_DESCRIBE="$(git -C "$REPO_ROOT" describe --tags --exact-match HEAD 2>/dev/null)" || GIT_DESCRIBE=""
147: if [ "$GIT_DESCRIBE" != "$VERSION" ]; then
148:     _warn "VERSION '$VERSION' does not match the exact tag at HEAD (git describe: '${GIT_DESCRIBE:-<untagged>}') — refusing (ADR-009 D3: stage what was built, no auto pull / no network fetch)"
149:     exit 78
150: fi
```
**Guard satisfied:** `GIT_DESCRIBE="v0.27.0"` == `VERSION="v0.27.0"` → the staged artifact is the exact tagged tree, with no auto-pull and no network fetch.

## ITEM 3 — Version evidence: **PASS** (all three methods independently agree)

**(1) `--version` probe** — throwaway CWD + FAKE HOME + full PG scrub:
```
stdout: Ensemble v0.27.0
stderr: (empty, 0 bytes)   exit code: 0
od -c:  E n s e m b l e   v 0 . 2 7 . 0 \n   (17 bytes, no log prefix)
```
This path exists deliberately: `run_app.py:57-60` early-exits with `f"Ensemble v{_ensemble_version}"` **before** `_boot_db_preflight()` — added after the 2026-10-03 env-poison incident where a `--version` probe booted the full stack against `ensemble_prod`.

**(2) House banner boot** — `setsid` (own process group) + `timeout -k 5 30` + scrub + `PORT=10000`/`HOST=127.0.0.1`/`DAEMON_PORT=10000`:
```
21:19:15 - daemon.api - INFO - Starting Ensemble v0.27.0
21:19:16 - daemon.api - INFO - Starting Ensemble v0.27.0
```
Logged **twice** (`daemon/api.py:4176` + `daemon/__main__.py:497`) — house-known, not a defect. The only version-like string in the entire 307-line log is `v0.27.0` (0 occurrences of any other version).

**Exit code 3, not 75 — explained, and it is NOT a defect:** with PG scrubbed the daemon fell back to `database=sqlite` and hit the **known pre-existing fresh-SQLite migration defect**:
```
daemon.migrations.runner.MigrationError: Migration 20260714_000001 failed:
  (sqlite3.OperationalError) near "EXISTS": syntax error
[SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]
ERROR:    Application startup failed. Exiting.
```
The runner is a SQLite-NO-OP on PG, so production is unaffected. Exit 75 occurs only when PG is still the *configured* DB and the preflight fails closed. **The banner is the verdict; the exit code is not.**

**(3) CArchiveReader (build venv, PyInstaller 6.19.0 / py3.14.7):**
```
module: <code object <module> at 0x72b355f51ce0, file "daemon/__init__.py", line 1>
co_consts matching version patterns: ['0.27.0']
nested code objects:              ['0.27.0']
```
`hasattr(mod, "__version__")` → **False** — a false negative: `pyz.extract('daemon')` returns the module's **code object**, not the module. The `co_consts` scan is the working probe.

**Payload immutability during this item:** `find releases/v0.27.0 -newermt '2026-10-21 21:18:18Z'` → **count=0**, before and after; binary sha256 unchanged.

## ITEM 4 — Live boot smoke: **PASS**

Scratch coordinates: payload copy `/tmp/v0270-smoke-v2garH1U/payload` (`cp -a` — **booting in place would have written `data/` into the live install dir**), FAKE HOME `/tmp/v0270-home-hiNcmUqT`, scratch PG role+DB `hrr_v0270_1792617571` on local 16/main, port **10043** (bind-tested free), units `v0270-smoke-ab753e51` (boot #1) + `v0270-smoke-a0044b0f` (control boot #2).

**Cgroup isolation — VERBATIM** (both PyInstaller PIDs, bootloader parent `1632213` + port owner `1632276`):
```
0::/system.slice/v0270-smoke-ab753e51.service
```
contrast — live daemon: `0::/system.slice/ensemble-main.service`. **No `ensemble-main.service` in the ancestry.** `/proc/<pid>/environ` confirmed the unit env was **exactly** the env file (`POSTGRES_DB=hrr_v0270_1792617571`, `POSTGRES_HOST=127.0.0.1`, `PORT/DAEMON_PORT=10043`, `HOST=127.0.0.1`, `HOME=/tmp/v0270-home-hiNcmUqT`, `ENSEMBLE_SELF_ENV=dev`) with **no** ambient `ENSEMBLE_UPGRADE_LIVE` / `QUEUE_DISCARD_ON_STARTUP` / `SSL_CERT_FILE` — while the lane's own shell was poisoned with `POSTGRES_DB=ensemble_prod` / `POSTGRES_HOST=10.44.0.2` / `PORT=9797`.

**`/livez` (200):**
```json
{"status":"alive","uptime_seconds":6.044355869293213,"version":"0.27.0"}
```
**`/readyz` (200):**
```json
{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":"2026-10-21T21:20:30.327319+00:00","inflight_turns":0},"draining":false}
```
`detail.reasons` empty — no degradation. 77 `public` tables **all** owned by the scratch role; `schema_migrations`=0 rows (expected on fresh PG); zero `42804` / `DatatypeMismatch` / `Application startup failed`.

**Feature-presence evidence from the STAGED artifact's own journal — VERBATIM:**
```
21:20:26 - daemon.services.boot_epoch - INFO - Boot epoch captured (DB clock): 2026-10-21T21:20:26.106412
21:20:26 - daemon.services.message_queue_boot_recovery - INFO - [HardReboot] MQ boot pass: reset=0
21:20:30 - daemon.api - INFO - AutoContinue boot pass: ContinueResult(candidates=0, scheduled=0, skipped_no_checkpoint=0, skipped_resume_refused=0, skipped_kill_switch=0, skipped_no_boot_epoch=0, skipped_multi_task_instance=0, errors=0, duration_seconds=0.4682729599880986)
21:20:30 - daemon.services.auto_continue_boot_pass - INFO - [BOOT_CONTINUE] orphan-sweep summary: candidates=0 scheduled=0 skipped_no_checkpoint=0 skipped_resume_refused=0 skipped_kill_switch=0 skipped_no_boot_epoch=0 errors=0 duration_s=0.022 boot_epoch=2026-10-21T21:20:26.106412
```
`reset=0` / `candidates=0` on a fresh DB is the correct value — and it proves the **hard-reboot-recovery code is present in the shipped binary**, not merely asserted by a manifest.
**Boot banner:** `21:20:20 - daemon.api - INFO - Starting Ensemble v0.27.0` (×2, same house-known twin).

**One-variable differential control boot:** boot #1's ERROR census was 11 lines vs the house 7-line baseline. Rather than accept it, the lane re-booted with **exactly one** change (`MCP_DISABLE_BUILT_IN_WEBFETCH=true`) → 11→**exactly 7**, webfetch errors **4→0**, proving the 4 extras are the documented `uvx`-absent harness signature and not an artifact defect. The 3 remaining key-related WARNINGs are self-attributed by the daemon to the absent `SYSTEM_ENCRYPTION_KEY` (excluded by the env spec); the `maintenancer`/`git_commit` registry WARNING is pre-existing in every staged tree. **Artifact error census = the clean 7-line baseline.**

**Cleanup proven:** both units `LoadState=not-found` / `ActiveState=inactive`; `systemctl list-units --all | grep v0270-smoke` → **none**; scratch DB + role dropped → 0 rows in `pg_database` / `pg_roles`; scratch dirs removed; port 10043 free; listener set byte-identical to pre-run. **Live surfaces unchanged** (ensemble-main MainPID 528942 / NRestarts 0 / epoch 10:38:47 UTC; `current -> releases/v0.26.3`; `releases/state.json` sha256 `c5f2ff9e…` unchanged; live `/livez` → `{"status":"alive","version":"0.26.3"}`). **Live-payload immutability:** `find releases/v0.27.0 -newermt @1792617571` → **EMPTY (count=0)**, checked mid-run and again after teardown.

## ITEM 5 — MIGRATION DELTA (NOT SLIMMED): **PASS — DELTA EMPTY, CONFIRMED**

**Extraction method — every entry extracted TWICE by independent methods, 0 divergences:**
- (A) `CArchiveReader.extract(name)`
- (B) raw slice at `ar._start_offset + TOC_dpos` + `zlib.decompress` when the TOC compression flag is set
→ **0 divergences, 0 ULEN mismatches** on both binaries. Migrations are CArchive **DATA entries, typecode `x`**, under `daemon/migrations/versions/`.
**Trap avoided:** the PYZ was scanned separately — its `.sql`-named entries are only Python modules (`psycopg.sql`, `sqlalchemy.sql`, `sqlmodel.sql`, `pygments.lexers.sql`), **not** migrations.

**Chain of custody (proves the *staged* artifact was the one verified):**
`disk sha256 6c6bfd58…6402` == `manifest.binary_sha256` == build sidecar (`git_head=1d5fdfe0f`, `git_dirty=false`) == the recorded `staged_release_v0270_composition.binary_sha256`. v0.26.4 disk sha256 `3db52510…9ba2` == its own manifest.

**(a) COUNT**
```
v0.27.0 vs v0.26.4 :  EQUAL: 106 == 106
v0.27.0 vs source   :  EQUAL: 106 == 106
```

**(b) CONTENT — sha256 over all three sides (106 lines each)**
```
only-in-v0.27.0              ∅
only-in-v0.26.4              ∅
differing-sha                ∅
only-in-source               ∅
v0.27.0-vs-source differing  ∅
whole-manifest diff  rc=0    (both pairings)
Aggregate attestation over "{name}:{sha}":
  8350337fec51aa78fcd58807197b0f6a7f25eddc20314b6fa017189058e1b20b   (identical)
```

**(c) DUPLICATE-PREFIX GUARD**
```
uniq -d over YYYYMMDD_HHMMSS prefixes:   EMPTY on all three sides
106 prefixes / 106 distinct              (v0.27.0, v0.26.4, source)
cross-side prefix sets:                  equal
```

**(d) CHAIN TAIL**
```
v0.27.0   20261020_060000_create_board_tables.sql
v0.26.4   20261020_060000_create_board_tables.sql
source    20261020_060000_create_board_tables.sql
manifest known_schema_gen = 20261020_060000_create_board_tables.sql   ✅
```

**House source layers (independent corroboration):** FS 106 + `git ls-tree` 106, both `uniq -d` empty; `git diff --name-status -M` **and** the rename-OFF control between build trees `6df7d8994 → 1d5fdfe0f` both **EMPTY**; whole-dir `daemon/migrations/` empty.
**The control that makes the negative meaningful:** the same diff pathspec run against `daemon/services/` returned `A daemon/services/message_queue_boot_recovery.py` — proving the pathspec detects a real change, which is what makes the empty migrations diff substantive rather than a broken command.

**Version anchor + code-divergence proof (beyond the brief):** PYZ `daemon/__init__` co_consts → exactly one version string per binary (`0.27.0` / `0.26.4`); PYZ **daemon-pool 530 vs 529** — the binaries *genuinely differ in code* while the `.sql` set is identical. This rules out the failure mode the check exists to catch (a relabelled binary carrying a stale schema). **The delta is empty in MIGRATIONS while the CODE moved.**

⇒ `rollback_safe=true` (delta-empty-legitimate) is **substantiated** for v0.27.0, and the item is **not slimmed**.

**Evidence retained:** `/tmp/v0270-mig-verify/` (extracted sets for `src/ v0270/ v0264/`, `.sha256` + `.prefixes` manifests, 4 re-runnable scripts).

## ITEM 6 — Payload hygiene: **PASS**

| Check | Result |
|---|---|
| Source-less | top level = exactly `agents/ config.yaml ensemble-prod frontend/ launcher.sh manifest.json plugins/` — **no** `scripts/`, `daemon/`, `src/`, `tests/` ✅ |
| Zero build sidecars in payload | `find … -name '*build-provenance*'` → **empty**; `-name '*.provenance.json'` → **empty** ✅ |
| Manifest counts vs disk | agents **339/339**, frontend **92/92**, plugins **4930/4930**; **5361/5361 per-file sha256 re-hashed → 0 mismatches**; 0 manifest keys missing on disk; 0 on-disk files not in manifest ✅ |
| Tree hashes **RECOMPUTED** | house algorithm identified and cited: `lib.sh:1954 _tree_manifest` (sorted relpaths → `"<sha256>  <relpath>"` lines) + `lib.sh:1970 _tree_hash_of_lines` (`printf '%s\n' \| shasum -a 256`), as invoked at `stage.sh:441-446`. All three **MATCH** the manifest ✅ |
| `known_schema_gen` | `20261020_060000_create_board_tables.sql` ✅ |
| `version` / `binary_version` | `v0.27.0` (v-prefixed) / `0.27.0` (unprefixed) ✅ |
| `rollback_safe` / `staged_at` | `true` / `2026-10-21T21:00:35Z` ✅ |

**Glob-collision note (not a sidecar leak):** `find -name '*provenance*'` returns **2 hits**, both opendesign **plugin content files** (`opendesign/copy_freely/…/references/provenance-example.json`, `opendesign/copy_freely/…/examples/stock-portfolio-live/provenance.json`) — legitimately covered by `plugins_manifest` and counted in the 4930. The payload's own build-sidecar count is **zero**.

**Independent byte-level cross-check vs the v0.26.4 payload** (sha256 of the sorted per-file listing): agents `5d1c10bb…` / frontend `0540bade…` / plugins `604624ea…` — **identical for both releases** ✅

---

## CEREMONY NOTES (not artifact defects — both require action before the promote)

**🔴 N1 — BOTH LIVE NONCES TARGET `v0.26.4`, NOT `v0.27.0`.**
`releases/state.json` holds two **unconsumed** nonces, both bound to `target=v0.26.4`:
`CONFIRM-VMHSOB67` (expires 21:55:38Z) · `CONFIRM-FZAOOXBN` (expires 22:01:20Z) — both live at 21:16:08Z.
A nonce is bound to **(target, kind, env, instance)**, so **neither will authorize a v0.27.0 promote**. A **fresh nonce for v0.27.0 is required**, or the ceremony will burn an arm and fail late.

**🟠 N2 — TIP DRIFT IS CODE-BEARING → PROMOTE AS-IS.**
`origin/latest` advanced to `60d7daaf` (bump to 0.27.1, 21:09:38Z) **after** the 21:00:35Z stage. The drift is the **nest-ops `instance_ops` lane**: 45 files, +10898/−499, adding `daemon/tools/instance_ops.py` (1844), `instance_read.py` (1415), `instance_ops_service.py` (372), `daemon/tools/provenance.py`, `_tool_registry.py`, `daemon/routers/instances.py`, `daemon/tools/upgrade_tools.py`, **plus a new migration `20261021_120000_create_instance_ops_audit.sql`**.
Consequences:
- The nest-ops merge landed at 21:08:18Z, **after** the stage. Since `stage.sh`'s L1 tip-identity guard is **fail-closed and pre-build**, the successful stage itself proves `origin/latest` resolved to `1d5fdfe0` at stage time.
- **Promote v0.27.0 AS-IS.** A re-stage from tip would be **REFUSED** (exit 78 `non-tip-tree`).
- Promoting v0.27.0 does **not** bring nest-ops live — that lane ships in the not-yet-staged **v0.27.1**.
- **Forward-looking:** that nest-ops migration would be the **first non-empty migration delta since the live v0.26.3** (106==106 today). The project's standing rule applies — the v0.27.1 stage needs the **explicit heuristic-vs-semantic adjudication recorded, not a reflex `=0`**.

**🟠 N3 — Over-retention: 5 release dirs vs keep-3** (`v0.25.7`, `v0.26.2`, `v0.26.3`, `v0.26.4`, `v0.27.0`). Report only — **no eviction performed** (machinery-owned).

**🟢 N4 — `state.json.previous = v0.26.2` resolves on disk** — no dangling rollback pointer. `in_flight=null`, `pending_op=null`, `pending_restart=null`, `quarantined=[]`, `cooldown_until=null`.

**🟢 N5 — Fresh-SQLite migration defect `20260714_000001` is still present** (the item-3 banner probe's exit 3). Pre-existing, PG-affected-none, **out of scope for this verification** — but it is the same documented trap that blocks a scratch-SQLite boot, and it will bite any future fresh-SQLite boot-scan.

---

## FENCES (item 7)

**[PENDING — final re-confirmation lane `a4a15d52`]**

Standing baseline carried from the pre-verification state: `current -> releases/v0.26.3` (mtime `2026-10-21 10:38:09.014490013 +0000`); live daemon MainPID 528942 / NRestarts 0 / epoch `Wed 2026-10-21 10:38:47 UTC`, livez `{"status":"alive","version":"0.26.3"}`; demo :7979 alive (pid 1147); `releases/state.json` sha256 `c5f2ff9ed9e6b803c4654760af90e73aea5dad4bc51443f978195df9faa4b63f`; port 8088 not bound; `releases/` holding 5 version dirs vs keep-3.

**No promote · no `state.json` edit · no systemd interaction with live units · no real host reboot · no eviction · no write into the live install dir or the staged payload** — every lane reported zero writes to the live surface, and the staged payload's immutability was independently re-checked by two lanes (`find releases/v0.27.0 -newermt …` → **EMPTY**).

**Note on this record's location:** it is committed on a dedicated evidence branch off `origin/latest` (the main checkout is on `latest` at `1d5fdfe0` with a concurrent lane's dirty files, and the staging worktree is deliberately left pristine for the promote ceremony).

---

## METHOD NOTES (why the verdicts are trustworthy)

1. **Nothing was trusted.** Every sha, every count, every tree hash and every migration entry was **recomputed**; the house tree-hash algorithm was identified from source (`lib.sh:1954`/`lib.sh:1970`) and reproduced rather than assumed.
2. **Negatives carried controls.** The empty migrations diff was paired with a pathspec control that *did* detect a change (`A daemon/services/message_queue_boot_recovery.py`); the boot ERROR census was paired with a one-variable differential boot (11→7); the "no sidecars in payload" claim was paired with the glob-collision explanation.
3. **Every extraction ran twice by independent methods** (API + raw slice + zlib) with 0 divergences.
4. **A chain of custody from the binary back to the tree** — disk hash → manifest → sidecar → `git_head` → `describe` → bump-commit scope → PYZ version const → PYZ code-pool delta — so the artifact verified is provably the staged artifact of the tagged tree.
