# Staged Verification — v0.26.0 (ari-tools) — INDEPENDENT GATE

**Commission:** INDEPENDENT STAGED VERIFICATION, final gate for v0.26.0 (ari-tools: board tables + board view + conventions rider)
**Date:** 2026-10-20 (evidence window 20:21–20:30Z)
**Staging:** `/home/nea/agents-ensemble/releases/v0.26.0` · merge `bfeca369d` · bump `0ae3d5d59` · re-pin `14a7f3762` · tag object `6f47b76b2` → `14a7f3762` (annotated, LOCAL-ONLY)
**Composition (leader's numbers of record):** ari-tools phases 1–3 + step-4 conventions + seal re-stamp + bump; known_schema_gen `20261020_060000_create_board_tables.sql`; migration delta +1 (105→106)
**Lanes:** 6 parallel workers, one skill each, 0 re-dispatches, 0 timeouts, 0 production-code changes, 0 promote

## Verdict

🟢 **STAGED-VERIFIED (payload integrity) — 8/8 leader items verified green; 2 disclosed mismatches vs the numbers of record, one of them CEREMONY-MATERIAL and requiring the leader's promote-vs-restage call.**

Every integrity property of the staged artifact re-derived independently and matched: provenance chain, binary sha (3-way), all 5,361 manifest entries re-hashed (0 bad), 3 tree hashes re-derived from scratch after self-validating the implementation against a prior release, the migration registry (106 files / 106 distinct prefixes / delta exactly +1), the rollback-safety claim, the seal gates (161/161, tripwire zero-stale, audit 46/46), a real boot of the staged binary against a disposable scratch PostgreSQL (banner v0.26.0, livez/readyz 200, board tables materialized), and the full ari-tools corpus green with 0 silently deselected.

**⚠️ M1 (ceremony-material, NOT a payload defect):** `origin/latest` has moved 19 commits past the staged commit. The staged artifact is *internally* consistent and complete, but it is no longer tip-bound — a re-stage from the staging worktree would now be **refused by the L1 tip-identity guard** (exit 78 `non-tip-tree`), and a newer local-only tag `v0.26.1` exists that is **not staged**. The leader must settle promote-v0.26.0 vs restage-v0.26.1 before the ceremony. Details in §M1.

---

## 1. Scope decision

Leader brief = 8 verification items against a staged artifact (NO promote). Scope = **the staged artifact + the staged tree**, not the repository's full suite. Six lanes were derived by splitting the 8 items along verification-mechanism boundaries (read-only payload forensics / git registry forensics / boot execution / seal gates / two suite legs) so that no lane exceeded the 5-minute cap and each lane carried exactly one skill. ensure.md Release Gate was **not** re-run (out of scope for a staged-artifact gate; it was validated on the dev side and the change set touches none of its paths). The inherited maintenancer 3-red was **not** fixed (routed separately by the leader) — only counted.

## 2. Method — 6 parallel lanes, one skill each

| Lane | Instance | Skill | Leader items | Runtime |
|---|---|---|---|---|
| W-A payload/provenance | `57606c7e` | `independent-staged-release-integrity-verification` | 1, 2, 3 (+4, 6 static) | 6m39s |
| W-R registry/rollback | `5f94d608` | none (infra) | 4, 7 | 5m32s |
| W-B boot smoke | `890a727a` | `staged-binary-boot-smoke` | 5 | 2m10s |
| W-S seal gates | `1e18b298` | `test-pack-execution` | 6 | 4m07s |
| W-C1 suites 1 | `b3533f72` | `test-pack-execution` | 8a | ~2m |
| W-C2 suites 2 | `d4f4309b` | `test-pack-execution` | 8b | ~2m30s |

**Fences held by every lane (verified in each lane's audit):**
- Staging worktree `/home/nea/ensemble-src-wt-ari-tools` (detached @ `14a7f3762`) read-only — **tracked tree byte-identical before and after every lane**; `HEAD` unmoved; tags unmoved.
- Production `:9797` never contacted beyond one read-only `livez` GET; `ensemble-main` MainPID `2296104` / epoch `2026-10-20T06:48:44Z` / `NRestarts=0` unchanged; `current → releases/v0.25.7` unchanged before AND after; `releases/state.json` sha identical; ports 7979/8088 untouched; **no process killed that a lane did not own** (boot lane: ownership 3/3 proven before any signal).
- Ambient `POSTGRES_*` (`ensemble_prod` @ 10.44.0.2) scrubbed before every DB-touching command; suites re-pinned to the dev fence (`ensemble_dev`, unix-socket peer auth, `PGHOST=/var/run/postgresql`); boot lane used a **disposable scratch DB** — `ensemble_dev` and `ensemble_prod` never written.
- No live-shaped paths resolved; every `rm -rf` escape-guarded to `mktemp` scratch only (2026-10-12 convention).
- No `git checkout/stash/reset/add/commit/push` in any shared tree by any verification lane; no `systemctl` mutations; no bare `npx`/`tsc`; `PYTHONDONTWRITEBYTECODE=1` on suite runs; `-p no:cacheprovider`; every invocation `timeout`-wrapped; no `pytest -x`.
- Tree-loss gate read via the **recency window** (`journalctl -t ensemble-treecheck --since "-10 min"` → `-- No entries --` = green), never `-n`.

---

## 3. Per-item results

| # | Item | Verdict | Lane | Headline evidence |
|---|------|---------|------|-------------------|
| 1 | Provenance (HEAD, sidecars, tag, local-only) | ✅ PASS · ⚠️ M1 | W-A | sidecars `git_head=14a7f3762`/`git_dirty=false`; tag `6f47b76b2`→`14a7f3762` annotated; `ls-remote` tags empty |
| 2 | Binary (sha 3-way, version) | ✅ PASS | W-A/W-B | `5445da08…` disk==manifest==sidecar; `binary_version=0.26.0`; banner `Starting Ensemble v0.26.0`; livez `"version":"0.26.0"` |
| 3 | Payload trees (agents/plugins/frontend/fe_index) | ✅ PASS · ⚠️ M2 | W-A | `ec9d3179…` / `4715cf64…` / `8d6afeaa…` / `a017dd21…` all re-derived; 5,361/5,361 manifest entries re-hashed 0 bad; plugins+frontend `diff -r` EMPTY |
| 4 | Migration integrity (106 / delta +1 / dup-prefix) | ✅ PASS | W-R | 106 files, 106 distinct prefixes, `uniq -d` EMPTY; delta = 1 `A` row; guard pin `1 passed` |
| 5 | Boot smoke (staged build, dev fence) | ✅ PASS 7/7 | W-B | banner v0.26.0; livez+readyz 200; **3 board tables created** on fresh scratch PG; 161 prompt baseline; teardown clean |
| 6 | Seal gates (161/161, tripwire, audit) | ✅ PASS | W-S | `PROMPT-BASELINE: OK — 161 versioned artifact(s)`; tripwire `1 passed` zero-stale; audit `46 passed`, 0 deselected |
| 7 | Rollback-safety spot-proof (old tree) | ✅ PASS | W-R | old binary board-module identifiers **0/0/0/0/0** vs new **1/1/1/1/1**; old `git grep -- daemon/` → 0 files |
| 8 | Scoped suites at staged tree | ✅ PASS | W-C1/W-C2 | 274 + 326 + 157 + 137 nodes, **0 deselected**; maintenancer exactly **3F/10P** (inherited) |

---

## 4. Item detail

### Item 1 — Provenance: PASS (M1 disclosed)

- Staging worktree `HEAD` = `14a7f3762432a32a43611b92095b2ed1c5bf5ef5`, **detached** (no symbolic HEAD), `status --porcelain -uall` = **0 entries** (the "known-foreign" `install-audit.jsonl` is tracked and **unmodified** — it never appears in porcelain).
- Both provenance sidecars present in the worktree (exactly 2, no others): binary `git_head=14a7f3762…`, `git_dirty=false`, `artifact_sha256=5445da08…`; frontend `git_head=14a7f3762…`, `git_dirty=false`, `artifact_sha256=a017dd21…`.
- Tag: `git cat-file -t 6f47b76b2` = `tag`; `cat-file -p` → `object 14a7f3762…` / `type commit` / `tag v0.26.0`; `git rev-parse v0.26.0^{commit}` = `14a7f3762…`. `git ls-remote origin refs/tags/v0.26.0` → **EMPTY** ⇒ local-only, push discipline intact.
- Superseded tag object `38328c199…` still present in the object DB pointing at `0ae3d5d59` (the pre-re-pin cut) but its **ref is deleted** — `show-ref` lists only `6f47b76b` for `v0.26.0`. Exactly as predicted.
- ⚠️ **M1:** `origin/latest` is **not** `14a7f3762` at verification time — see §M1.

### Item 2 — Binary: PASS

- 3-way match: on-disk `sha256sum` = `manifest.binary_sha256` = sidecar `artifact_sha256` = `5445da08d07b529cd2ef4696542b4cd42805f2a8b317fe62cc66ea48f276309c`.
- `manifest.version = "v0.26.0"` (with v), `manifest.binary_version = "0.26.0"` (no v).
- Source-of-truth version proof (no binary strings-grep — house lesson R2-2): `git show 14a7f3762:daemon/__init__.py` → `__version__ = "0.26.0"`; `pyproject.toml:3` → `version = "0.26.0"`.
- Runtime confirmation from the boot lane: banner `20:25:51 - daemon.api - INFO - Starting Ensemble v0.26.0`; `/livez` body `"version":"0.26.0"`; post-teardown re-hash of the staged binary unchanged.

### Item 3 — Payload trees: PASS (M2 disclosed)

Tree-hash implementation **self-validated first** against the prior release (agents `9a168ed7…`/339 files, frontend `8d6afeaa…`/92, plugins `4715cf64…`/4930, binary `26fc6db3…` — all reproduce) before being trusted against v0.26.0.

| Field | Recomputed | Manifest | Match |
|---|---|---|---|
| `agents_tree_sha256` | `ec9d3179bbbc28b28d2061fb39df8245821aaf2ee1ed2cb9ab57841506d62fda` (339 files) | same | ✅ |
| `plugins_tree_sha256` | `4715cf64dad62d209c91206c560e475d7fa4cec8f67f6182ffbcd21b723685a7` (4930) | same | ✅ |
| `frontend_tree_sha256` | `8d6afeaa8c7fda70acbc05deec17fca89e1a43256179c8e6405fd35a2293ae03` (92) | same | ✅ |
| `fe_index` (derived — no manifest field) | `a017dd2135b0f532b382a8842b3b39e650763cb605b7426f356c2c4f9ba85af1` | manifest `frontend_manifest["dist/frontend/browser/index.html"]` == FE sidecar == on-disk re-hash (4-way) | ✅ |
| `config_sha256` / `launcher_sha256` | `7bab75e9…` / `a3a44461…` | same | ✅ |

- **Byte-identity vs v0.25.7:** `diff -r` plugins/ **EMPTY**; frontend/ **EMPTY**; agents/ **CHANGED** (`9a168ed7…` → `ec9d3179…`) — expected (ari conventions + one foreign-lane rider, see M2).
- **Full key-set compare:** agents 339/339, frontend 92/92, plugins 4930/4930 — 0 missing / 0 extra each; **all 5,361 manifest entries individually re-hashed → 0 bad**.
- **Independent cross-check:** `git archive 14a7f3762` → scratch → payload `agents/` and `plugins/` re-derive to the same hashes ⇒ the payload equals the committed tree, not just the manifest.
- **Payload shape:** top level exactly `{agents/, plugins/, frontend/, config.yaml, ensemble-prod, launcher.sh, manifest.json}`; no `scripts/`, no `daemon/` tree, no `*.sql` outside `plugins/` (vendored), no `*.build-provenance.json` leaked.
- `staged_at = 2026-10-20T19:52:44Z` == `manifest.json` mtime **to the second**; `known_schema_gen = 20261020_060000_create_board_tables.sql`; `rollback_safe = true`.
- **Re-pin purity:** `git diff --name-status 0ae3d5d59..14a7f3762` = **exactly one file**, numstat `1 1` — `M tests/test_chart_image_delivery_e2e.py` (the `SEALED_SHA_BASELINES` constant). `git diff 0ae3d5d59..14a7f3762 -- agents/ plugins/ frontend/ daemon/ scripts/` = **EMPTY** ⇒ the re-pin was tests-only, exactly as claimed.
- Bump commit `0ae3d5d59` = `chore(release): bump version to 0.26.0`, **5 files**: `CHANGELOG.md` (+17), `daemon/__init__.py`, `pyproject.toml`, `tests/test_api.py`, `uv.lock`.
- *Probe correction (lane-side):* payload `frontend/` is build output, not git-tracked source — the correct alignment is payload `frontend/dist/frontend/` vs worktree `frontend/dist/frontend/` **minus exactly 3 build byproducts** (`3rdpartylicenses.txt`, `prerendered-routes.json`, and the FE provenance sidecar itself, intentionally not shipped); all 92 shipped files byte-identical.

### Item 4 — Migration integrity: PASS

- **106 `.sql` files** in `daemon/migrations/versions/` — measured twice (worktree filesystem **and** `git ls-tree` of the tag) ⇒ identical. Baseline `v0.25.7` = 105 ⇒ **delta exactly +1**.
- 106 distinct version prefixes (pattern `^[0-9]{8}_[0-9]{6}`), `sort | uniq -d` **EMPTY**, 0 non-conforming filenames.
- Git-level delta: `git diff --name-status -M v0.25.7 v0.26.0 -- daemon/migrations/versions/` = **exactly one `A` row** (`20261020_060000_create_board_tables.sql`), 0 M/D/R; the **rename-detection-off control** is identical; plain diff body = additions only (`@@ -0,0 +1,127 @@`). Whole-directory diff (`daemon/migrations/`) = **also exactly one `A` row** ⇒ no runner/`__init__` changes to disclose.
- New file semantics proven with the **real runner parser**, not by eye: **UP section = 9 statements** (3 × `CREATE TABLE IF NOT EXISTS` — `board_items`, `board_notes`, `board_item_tags` — + 6 × `CREATE INDEX IF NOT EXISTS`), **no DROP / ALTER / TRUNCATE in UP**; the **DOWN section** (9 statements) contains the DROP pair but sits after the `-- DOWN` marker and is **never auto-executed** by the runner. `MANUAL:TRUE` absent.
- Dup-prefix guard pin: `tests/migration/test_migration_dup_prefix_guard.py::test_real_versions_dir_has_no_duplicate_version_prefix` → **1 passed** (0.48s), with the editable-install assertion proving `daemon.__file__` resolves inside the worktree. *(Lane note: the node id differs from the path in the dispatch brief — the real node was located and run.)*

### Item 5 — Boot smoke (staged artifact): PASS 7/7

- **Provisioning:** ladder Path B (preferred) — `sudo -n -u postgres` provisioned a fresh role `ens_e2e_v0260` (LOGIN, CREATEDB) + scratch DB `ensemble_e2e_board_v0260` owned by it. `ensemble_dev` and `ensemble_prod` were **never written**.
- **Banner:** `20:25:51 - daemon.api - INFO - Starting Ensemble v0.26.0`.
- **Health:** `/livez` → `{"status":"alive","uptime_seconds":4.66,"version":"0.26.0"}`; `/readyz` → `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"draining":false}` — READY at poll #6 (18s).
- **Board tables (the new capability):** `board_items`, `board_notes`, `board_item_tags` all present, owner `ens_e2e_v0260`, out of 77 public tables with a single owner — i.e. **created by the staged binary on a fresh PG**. Verified via the privilege-independent `pg_class` catalog (not `information_schema`).
- **Prompt baseline at boot:** `[Startup] Prompt-artifact baseline clean: 161 versioned artifact(s) match .prompt-artifacts.sha256` (exactly 1 occurrence).
- **Migration posture:** PG skip line present (`Skipping migrations for non-SQLite database (schema evolution handled by EnsembleManager._ensure_postgres_columns)`); `DuplicateMigrationVersionError` = 0; `42804`/`DatatypeMismatch` = 0; `Application startup failed` = 0; fresh-DB `schema_migrations` = **0 rows** (expected — the PG path never enumerates the `.sql` dir; PG schema comes from the ORM).
- **Teardown:** ownership 3/3 on the child PID (`/proc/<pid>/exe` = staged binary, `POSTGRES_DB=ensemble_e2e_board_v0260`, `PORT=8079`) → SIGTERM child (gone in 1s, rc=0, **no SIGKILL**), bootloader parent self-exited, 8079 freed, `dropdb --force` rc=0 + `DROP ROLE` rc=0 + reconnect `FATAL` (gone), scratch dirs escape-guarded-rm'd, `find -newermt` on the payload = **0 modifications**, binary sha unchanged after.
- **Live fences before AND after:** `current → releases/v0.25.7`; `MainPID=2296104`, `ActiveEnterTimestamp=Tue 2026-10-20 06:48:44 UTC`, `NRestarts=0`, `ActiveState=active`; `releases/state.json` sha identical before/after (`current=v0.25.7 previous=v0.25.5 in_flight=None pending_op=None quarantined=[]`); `:9797/livez` → `0.25.7`; tree-loss journal → `-- No entries --`; 8079/8088 never bound.

### Item 6 — Seal gates: PASS

| Gate | Result |
|---|---|
| Prompt-artifact baseline | house script `scripts/protection/generate_prompt_artifact_baseline.py --check` → `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256` (exit 0); raw `sha256sum -c` from the tree root → **161 OK / 0 FAILED**; stale list **EMPTY** |
| Seal tripwire | `test_phase_d_does_not_modify_sealed_artifacts` → **1 passed** (0.55s), **zero stale mismatches** (no stale-pin list emitted) — vs 7 stale pins at phase-3 HEAD ⇒ the adjudicated re-pin closed them |
| Chart-delivery audit | `tests/test_chart_image_delivery_audit.py` → **46 passed**, 0 failed, 0 deselected, 0 errors, 0 skipped |

- **Re-pin forensics:** `git show --stat 14a7f3762` = 1 file / 1 insertion / 1 deletion — `tests/test_chart_image_delivery_e2e.py:1359`, constant `SEALED_SHA_BASELINES["agents/ari/workflow.md"]` `4ffa5bee…` → `f31f5bc4…`; commit message attributes it to the step-4 conventions edit `2d29190da` and states "no other row touched". Independently corroborated inside the payload: the shipped `agents/.prompt-artifacts.sha256` entry for `agents/ari/workflow.md` = `f31f5bc4…` (W-A) ⇒ the re-pin is present in the artifact, not only in the source.

### Item 7 — Rollback-safety spot-proof: PASS

The claim under test: *the v0.25.7-era binary's code paths never reference `board_items` / `board_notes` / `board_item_tags`* ⇒ on rollback the new tables are inert.

| Probe | OLD (v0.25.7) | NEW (v0.26.0) — control |
|---|---|---|
| module-path identifiers in the binary (`daemon.models.board`, `daemon.services.board_service`, `daemon.services.board_view_service`, `daemon.tools.board_tools`, `daemon.tools.board_link`) | **0 / 0 / 0 / 0 / 0** | **1 / 1 / 1 / 1 / 1** |
| `strings \| grep -ci board` | 4 hits — **all attributed** to `discord.soundboard` / `discord.onboarding` (unrelated) | 11 hits = the board modules |
| payload grep (`agents/`, `frontend/`, `plugins/`, `config.yaml`, `launcher.sh`) | **0 hits** | — |
| `git grep -l -E "board_(items\|notes\|item_tags)" -- daemon/` | **0 files** (rc=1) | **6 files**: `daemon/models/board.py`, `daemon/services/board_service.py`, `daemon/services/board_view_service.py`, `daemon/routers/live_views.py`, `daemon/tools/_tool_registry.py`, `daemon/migrations/versions/20261020_060000_create_board_tables.sql` |

⇒ The old binary contains **no board code path** and the old tree has **no board models**, so `SQLModel.create_all` on rollback cannot touch `board_*` — the tables are orphaned and never read. Corroboration for "additive-only": the release payload ships **0 `.sql` files** (source-less) and the runner is a **PG no-op**, so the new `.sql` never executes on the live PG deployment at all; **no down-migration runs on rollback** in either direction.

### Item 8 — Scoped suites at the staged tree: PASS

`--collect-only` (zero nodes executed) confirmed the four declared pack scopes resolve to **exactly** the PACKS.md-declared counts (326 / 157 / 137 / 13) *before* any execution ⇒ the slicing is the declared scope, not a narrower pick.

| Pack | Passed | Failed | Deselected | Runtime |
|---|---|---|---|---|
| `tests/unit/routers/test_live_views_board.py` | 50 | 0 | **0** | 37s |
| `tests/unit/tools/test_view_write_tool.py` | 53 | 0 | **0** | 26s |
| `tests/unit/tools/test_board_link_tool.py` | 23 | 0 | **0** | 10s |
| self_alarm corpus (5 files, marker-correct) | 148 | 0 | **0** | 9s |
| live-views regression (7 files) | 326 | 0 | **0** | 55s |
| board corpus (6 files) | 157 | 0 | **0** | 36s |
| 7-pin exact set | 134 | **3** (inherited) | **0** | 50s |
| maintenancer pin file | 10 | **3** (inherited) | 0 | 6s |

- **Zero silently deselected** in every invocation — the corpus's known failure mode (phase-3 trap leg: 128 passed / 9 silently deselected) did not reproduce; the marker-correct leg `-m "integration or not integration"` was used wherever the pack declares markers.
- **Inherited 3-red — confirmed exactly as routed, not fixed:** node ids identical across both invocations that touched the file —
  1. `TestRealMaintenancerMetaShape::test_real_meta_allow_is_exact_category_set` — allow resolves to `[…, 'system_restart', 'db']`, expected `[…, 'system_upgrade', 'db']`
  2. `TestRealMaintenancerMetaShape::test_real_meta_denies_system_restart_per_d20`
  3. `TestMaintenancerResolves::test_maintenancer_gets_system_upgrade_excluding_system_restart` — *"system_restart leaked through resolve_tool_filter despite tools.deny entry (D20 enforcement violated)"*
  Signature matches the known inherited defect (`tools.allow` gained `system_restart` @ `ff27305c4`; pin expectations never updated). **Reported only — no fix, no commit.**
- **F1 time-bomb (self_alarm):** latent, did not fire — the deterministic reds in `test_self_alarm_tools.py` only appear ~23:20–23:50 ICT; this run was outside that window (148/148). The corpus also **grew** vs the phase-3 reference (144/146 → 148/148) after step-4 conventions + re-pin.
- **Drift vs the dev-side count set `{53,54,73,316,9}`:** only `53` maps 1:1 (→ `test_view_write_tool.py`). `54`, `73`, `316` do not correspond to any single file or group under this slicing (nearest actuals: regression 326, passcode 74, board corpus 157, pin set 137) — the dev set is evidently a different grouping granularity. `9` matches the `service_tool_flag_off` file. **No coverage gap is implied**: every one of the five named areas (board / self_alarm / view_write / board_link / live-views) was run in full, green, and matched the declared pack counts. Authoritative per-file counts are the table above.

---

## M1 — ceremony-material: tip drift (NOT a payload defect)

**Claim:** `origin/latest == 14a7f3762432a32a43611b92095b2ed1c5bf5ef5`.
**Observed at verification time:** `origin/latest = fa0f718d2362b3b4111c7f22e81adf555d86cc37` (18 commits ahead) at 20:23–20:24Z → `a4f28c26b2cfac067169d298bc33fc945ffe265b` (**19 commits ahead**, subject `chore(release): bump version to 0.26.1`) at 20:29–20:30Z. `14a7f3762` **remains an ancestor** (superseded, not orphaned).

- Payload-tree delta staged→tip (`agents/ plugins/ frontend/`) = **0 files** ⇒ the ari-tools prompt content in the staged payload is still byte-current with the tip.
- `daemon/` delta = **11 files / 2,761 insertions** ⇒ the staged v0.26.0 binary **does not contain the question-pause lane** that landed after staging.
- A **local-only annotated tag `v0.26.1`** now exists (`170da3c83eac2fc46444f909bf55326cd29a121e` → `a4f28c26`); its message states it *"supersedes v0.26.0 content"*. `git ls-remote --tags origin 'refs/tags/v0.26*'` → **EMPTY** (push discipline intact).
- **Operational consequences (flagged, not acted on — verification lane):**
  1. `releases/` contains **no v0.26.1** ⇒ v0.26.0 remains the only *staged* artifact.
  2. A re-stage from the staging worktree would now be **refused by the L1 tip-identity guard** (`HEAD ≠ origin/latest` ⇒ exit 78 `non-tip-tree`); a re-stage would need a fresh tip worktree + rebuild (the L2 sidecar binds `git_head`, so the binary must be rebuilt — a tests-only commit still changes the binary hash).
  3. Promoting v0.26.0 installs a binary reporting `0.26.0` while the repository's newest tag is `0.26.1`.
4. **POST-VERIFICATION LANDSCAPE CHANGE (observed 20:43Z — OUTSIDE this gate's scope):** a concurrent lane **staged `v0.26.1` at ~20:36Z** (sibling worktree `/home/nea/ensemble-src-wt-stage-v0261` @ `a4f28c26b`; `releases/v0.26.1` now present; install `.env` rewritten 20:43:14Z). That same lane removed the pipeline's transient `releases/rollback.lock.d` mutex directory — a **transient lock artifact, not a payload change** (no version dir, no `current` symlink, no sidecar touched). At this gate's 20:30Z inventory **no `v0.26.1` release dir existed**. ⇒ **`v0.26.1` is now staged but has NOT passed this gate**; this verdict applies to **v0.26.0 only**. Promoting v0.26.1 would ship an artifact this gate never examined.
- **Not a verification failure:** the stage itself passed L1 tip-identity at `19:52:44Z`; the drift happened *after* staging. The provenance chain inside the artifact is complete and self-consistent.

## M2 — scope-claim: undisclosed foreign-lane file in the payload (integrity intact)

**Claim:** agents/ delta vs v0.25.7 = "ari conventions" (`agents/ari/*` only).
**Observed:** 9 files across 4 areas — ari-tools P1–P3 (`agents/ari/{meta.json, tools_note.md, workflow.md}`), plus `agents/leader/meta.json` (grants `board` + `board-link` categories to leader) and `agents/.prompt-artifacts.sha256` (re-stamp) — **plus four foreign developer-lane files**: `agents/developer/skill-set.yaml`, `agents/developer/skills-template/dev-strategy.md`, `agents/developer[v2]/skill-set.yaml`, `agents/developer[v2]/skills-template/dev-strategy.md` (`dev-strategy` 1.3.0 → 1.4.0, commit `843b669c5` "fix(developer): dev-strategy v1.4.0"). That commit is the **first parent of the ari-tools merge** (`git log -1 --format=%P bfeca369d` → `843b669c5… e802c3809…`) ⇒ it rode into the release payload *via the merge*. It is committed history and is on `origin/latest` — **not drift, not an uncommitted edit** — but the numbers of record did not disclose it. The payload nonetheless matches the committed tree exactly (`git archive` cross-check) ⇒ integrity unaffected, disclosure gap only.

---

## Disclosures (non-blocking)

1. **Method defects found in the dispatch brief itself — both repaired by the lane with working controls, disclosed rather than buried:**
   - *"grep the old binary for the table names"* is **not mutation-grade** — it returns 0 even on the **new** binary (PyInstaller compresses bytecode, so table-name literals never surface as raw strings). Repaired with module-path identifiers (OLD 0/0/0/0/0 vs NEW 1/1/1/1/1) + the `git grep` pair.
   - *"cd agents/ && sha256sum -c .prompt-artifacts.sha256"* is **CWD-unsafe** — the baseline keys are tree-root-relative, so that exact command yields **161 spurious "FAILED open or read"**. The house script `scripts/protection/generate_prompt_artifact_baseline.py --check` is the authority; re-run from the tree root → 161 OK / 0 FAILED.
2. **"No destructive DDL" is true of the applied path only** — the new `.sql` *does* contain `DROP TABLE`/`DROP INDEX` after the `-- DOWN` marker (never auto-executed; proven with the real parser: 9 UP statements, 0 DROP/ALTER in UP).
3. **`staged_version`, `fe_index_sha256`, `migration_delta_vs_prev` are not manifest fields** — they live only in the shared meta-KV staging record (`fe_index` is *derived*; manifest carries it as `frontend_manifest["dist/frontend/browser/index.html"]`).
4. **Pre-existing boot WARNING (not a v0.26.0 regression):** `daemon.registry - WARNING - Tool config validation: Agent 'maintenancer': deny entry 'git_commit' is neither a known category nor a known tool` — identical deny list in the v0.25.7 staged tree and 10 occurrences in the live v0.25.7 daemon logs. Boot-non-blocking.
5. **Benign boot noise:** `computer-use` MCP — `libXtst.so.6: cannot open shared object file` (host lacks X11 XTest) → 7 ERROR lines, one class; `context7` initialize TimeoutError (attempt 1/3); `plane` row config invalid for warmup; `[KbKeys] key store EMPTY` (fresh-DB artifact); plugin registry 0 plugins (frozen onefile build); `Worker … did not stop within 0s` at shutdown.
6. **Lane-hygiene note (attribution resolved):** two lanes reported a "concurrent foreign lane" writing inside the staging worktree. Attribution: the writer was **this verification's own sibling lanes** (W-R's migration-guard pytest, W-C1's self_alarm corpus pytest), and the `data/logs/ensemble.log` mtime bump is the suite lanes' module-level `create_app()` import side-effect (gitignored). **No external agent wrote to the worktree**; the tracked tree was byte-identical before and after every lane. Untracked byproducts: one `__pycache__` `.pyc` (W-R omitted `PYTHONDONTWRITEBYTECODE=1` on one invocation) and the gitignored `data/logs/` — both gitignored, tracked tree unaffected.
7. **Known LOW defect D1 ships as-is (leader-adjudicated ride-along, not re-verified here):** `view_write` accepts a literal percent-encoded path (`%2e%2e%2fx.html`) — containment holds, but the page is unservable, the passcode row orphaned, and the success envelope's public URL 404s. Phase-3 verdict: PASS-WITH-ONE-LOW-DEFECT. Not a containment/safety break; carried as a disclosed residual into v0.26.0.
8. **Reusable recipe captured:** lane W-R created skill `staged-release-migration-registry-and-rollback-safety-gate` (`02140ca4`) — runner UP/DOWN parse proof, PG runner no-op fact, the `strings`-needs-a-positive-control trap, the real dup-prefix test node id, and the zero-writes attestation checklist.

## Action needed (for the leader)

- [ ] **Settle M1 before the ceremony.** As of this gate, **v0.26.0 is fully verified** (this report). **`v0.26.1` was staged at ~20:36Z by a concurrent lane and is UNVERIFIED** — it would also require a fresh tip rebuild (L2 binds `git_head`; a tests-only commit still changes the binary hash), and the current staging worktree can no longer be re-staged at all (L1 `non-tip-tree`, exit 78). Options: promote v0.26.0 as verified here, or commission a verification pass against the staged v0.26.1. No promote was performed by this lane.
- [ ] Note the v0.26.0 binary **omits the question-pause lane** (11 daemon files / 2,761 insertions landed after staging) — decide whether that omission is acceptable for the promote.
- [ ] M2 disclosure: the payload ships the **developer dev-strategy v1.4.0** rider (via merge first-parent `843b669c5`) — accept, or re-note the composition record.
- [ ] D1 (LOW, `view_write` percent-encoded path) rides into v0.26.0 as-is — accept or schedule the ~10-line pre-merge-style fix.
- [ ] Retention: `releases/` now holds v0.25.5/6/7 + v0.26.0 (4 dirs) against a keep-3 policy — reconciled at the next promote.
- [ ] The inherited maintenancer 3-red (D20 / `system_restart` leak) remains routed to its owning lane — untouched here by design.

## Documentation updated

- [x] `RESULTS/2026-10-20-v0260-staged-verification.md` — this report
- [x] `PACKS.md` — staged-verification v0.26.0 block (lane invocations, counts, seal/boot results)
- [x] `LESSONS/2026-10-20-v0260-staged-verification-gotchas.md` — the six reusable gotchas (strings-vs-module-path probe, tree-root-relative baseline, manifest field vs meta-KV fields, UP/DOWN scoping, ORM-create_all vs `.sql` on PG, L1 tip-drift ⇒ re-stage refused)
- [ ] `rules/ensure.md` — no changes (user-owned, read-only)
- [x] `QUARANTINE.md` — no change: the 3 maintenancer reds are **deterministic inherited debt**, not flakes (no retry budget consumed; nothing quarantined)

## Code changes

**None.** No source edit, no fix, no commit by any verification lane, no promote, no flip, no live mutation. Evidence documents only (see commit posture in the leader report).
