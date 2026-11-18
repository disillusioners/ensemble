# Group-Chat Staged-Release Lane — Vehicle, Staging, and Ceremony Brief

**Status:** PLAN (vehicle + staging only — the live promote is the user's ceremony)
**Author:** architect (planning lane)
**Date:** 2026-11-17
**Target tree:** `origin/latest = 56a5f4a00818a624f01a04d16bedbaca7bd344cd`
**Recommended version:** **v0.31.0**
**Audit trail:** `origin/feature/group-chat-{design,p1,p2,p3,p4,p4-followups,p5,p6}`

---

## 0. PREMISE CORRECTIONS — read before anything else

The commissioning brief carried **three factual errors** and **one unstated blocker**. All four are verified against the repo at tip; none is inherited from notes.

| # | Brief said | **Verified reality** | Consequence |
|---|---|---|---|
| C-1 | "BOTH new migrations (…080000 + …120000)" | **THREE** — `20261116_083000_add_room_resolution_warnings.sql` also ships. All three are the last three of 133 migration files at tip. | The release carries **3** room migrations. A two-file release would ship a schema missing `room_messages.resolution_warnings`. |
| C-2 | "current live v0.30.13 ⇒ likely v0.31.0" | Live is **v0.30.12** (`state.json: current=v0.30.12, previous=v0.30.9`). v0.30.13 exists as a **tag + CHANGELOG section + pyproject value**, was never promoted, and has no `releases/v0.30.13/`. | v0.30.13 is a **consumed slot** (tag published, CHANGELOG `[0.30.13]` present, `pyproject = 0.30.13`). It must never be re-cut. **v0.31.0** confirmed — and for the *stronger* reason: features ⇒ minor bump (SemVer; CHANGELOG states the project "adheres to Semantic Versioning"). |
| C-3 | "the scripts-pin worktree floor … name what floor THIS release needs and how to establish it" | The scripts-pin worktree is **stale against the tip floor** and **cannot be used as-is**. The brief's implicit model (v0.29.0 precedent = run from the scripts-pin worktree) is **inverted** by the post-Family-3 invoke-path policy. | **This is the lane's one hard blocker.** See §5.1 — it is *satisfiable*, but only by advancing the live `.env` pin. |
| C-4 | "the thing most likely to be missed" | The brief's own list is missing the **release-dir retention eviction** (a 4th stage evicts `v0.30.11`) and the **`config.yaml` does-not-travel-with-releases** gate. | Both added below (§2.6, §5.4). |
| **C-6** | — *(added by the 2026-11-17 S3 measurement pass)* | The **S3 FE-asset leg is now MEASURED** (devops dry-run, release worktree @ `913088277`, no commits). It also surfaced a **stale "system v22" toolchain reference**, an **Angular 21 artifact-set shape** that reads as missing files, and a **verification trap** (esbuild mangles identifiers). | Folded into **§3.5** (new sub-sections 3.5.1–3.5.5) and **§5.3**. **New host-disk precondition** added at §5.2.1. |
| **C-5** | — *(added by the 2026-11-17 correction pass)* | The **release-prep tester** found **two CRITICAL ceremony-facing defects in this plan** and two wrong recipe facts. They are **fixed in place**: **BLK-1** (§5.4 was booting the **not-yet-promoted binary against `ensemble_prod` on the live port** — a prod-schema-creating, daemon-colliding, fence-open smoke) and **BLK-2** (the five-variable scrub left **`POSTGRES_URL`** live — the one variable that bypasses the prod fence entirely). | **§5.4 is rewritten.** The PG proof (top risk #2) is **CLOSED** by measured evidence. Two items are recorded as **follow-ups, not in this cut** (§8.4). |

**Also corrected (worker-report conflicts, adjudicated against the code):**
- The ceremony worker read the **detached main checkout** (`bf4bfde6e`) and reported the floor as `68c6ee05…`. **At `origin/latest` the floor is `1aa0065e…`** (`daemon/tools/upgrade_tools.py:4145`). The conclusion was right for the wrong reason; the corrected mechanism is in §5.1.
- The ceremony worker stated the group-chat migrations contain "no DROP". **They do** — in their **DOWN** sections (`DROP TABLE`/`DROP COLUMN`). `stage.sh`'s derive-path grep is **whole-file, not delta-scoped**, so it matches. This is exactly the house note *"stage.sh rollback_safe derive-path is UNUSABLE on legacy trees — `stage live` must pass `ENSEMBLE_ROLLBACK_SAFE=1` explicitly (with tag-to-tag delta disclosure); contains_contract_phase=true is EXPECTED."* See §5.5.

---

## 1. VERSION SELECTION — **v0.31.0**

### 1.1 Recommendation

**`v0.31.0`**, cut from `origin/latest` at the tip of the group-chat merge (`56a5f4a00`).

### 1.2 House-convention reasoning

| Signal | Evidence | Bearing on version |
|---|---|---|
| SemVer | `CHANGELOG.md:5` — "this project adheres to [Semantic Versioning]" | A user-facing feature ⇒ **minor** bump |
| Feature ≠ fix | The feature adds a new subsystem (3 tables, 5 new tool verbs, a new FE surface, a new tool category) | Not a patch |
| v0.30.13 consumed | Annotated tag `v0.30.13` → commit `83a237bc5`; `CHANGELOG.md` has a `## [0.30.13] — 2026-11-15` section; `pyproject.toml` at tip already reads `version = "0.30.13"` | **v0.30.13 is spent.** Re-cutting it = force-moving a published tag |
| The prior lane's shape | `## [0.30.12]` and `## [0.30.13]` are both **feature-shaped** (`### Added — rogue-process containment p2`) but were cut as patch/minor | The repo bumps generously; minor is safe |
| Slot discipline | The v0.29.0 ceremony notes record `next_free_version_slot: v0.29.1` — slots are tracked explicitly | v0.31.0 is free; no tag, no CHANGELOG section, no `releases/` dir |

### 1.3 Implications of the version choice

1. **`pyproject.toml` is already at `0.30.13`.** `bump_version.py` reads the current version from `pyproject.toml` — so the bump MUST run against the repo, not be hand-authored to `0.31.0` from `0.30.13` by editing `pyproject` alone. `bump_version.py` writes **four** sites: `pyproject.toml`, `daemon/__init__.py` (`__version__`), `tests/test_api.py` (version assertion), and `uv.lock` (ensemble package entry). It then runs `uv sync` and commits `! New version <v>`.
2. **Migration / upgrade-journal ordinals.** The three room migrations are the **last three of 133** migration files at tip (`20261115_080000`, `20261115_120000`, `20261116_083000`). `stage.sh` derives `known_schema_gen` automatically as the last filename — so `known_schema_gen = 20261116_083000_add_room_resolution_warnings.sql`. **No migration renumbering, no ordinal record, and no `schema_migrations` pre-seeding is required**: the ledger table (`schema_migrations`, `runner.py:213-232`) is created and populated by the runner itself, and PG never sees the runner at all (see §3.2).
3. **Upgrade-policy metadata.** The project metadata's `upgrade_policy` and `upgrade_workflow_preference` are unchanged by this release. The operative preference still stands: **prefer speed over maximal safety** — skip or slim the `pg_dump` preflight; health gates (livez/readyz/soak) stay. Also note the `staged_release_v0290_ceremony_notes` block is stale (it names v0.29.0 as the next slot); it must **not** be read as governing this cut.
4. **`EXPECTED_LATEST` is hard-coded** in the group-chat ledger gate (`tests/unit/services/test_group_chat_ledger_gate.py`) at **36** (the live row count). Any ledger row appended during the release lane **must bump `EXPECTED_LATEST` in the same change** or the ledger gate REDs. *Recommendation: append nothing.* The release lane is not a design lane; if a row is added, it must be a release-lane row and must carry the bump.

---

## 2. THE RELEASE VEHICLE

### 2.1 Staged payload file set (`releases/<ver>/`)

Assembled by `scripts/upgrade/stage.sh` from the repo tree:

| Path in release | Source | Notes |
|---|---|---|
| `ensemble-prod` | PyInstaller build (or `--skip-build <path>`) | The frozen binary. **Carries the migrations inside it** — see §3.1 |
| `agents/` | `$REPO_ROOT/agents` (recursive) | Includes the 8 `meta.json` room grants (§3.4) |
| `frontend/dist/frontend/browser/` | `$REPO_ROOT/frontend/dist/frontend/browser` | The built FE (§3.5) |
| `plugins/` | `$REPO_ROOT/plugins` | **Required** — stage refuses (exit 78) without it; the plugin registry boot-scans `<cwd>/plugins/*/MANIFEST.yaml`; an empty tree is the v0.18.0 regression |
| `guidelines/` | `$REPO_ROOT/docs/guidelines` (flattened to release root) | **Required** — the runtime guideline reader resolves `<exe-dir>/guidelines`; missing ⇒ exit 78 |
| `config.yaml` | repo root | See §5.4 — does **not** travel with releases in the general case; the install's copy is operator-owned |
| `launcher.sh` | repo root (chmod +x) | The promote swaps it in during the stopped window; manifest carries `launcher_sha256` |
| `scripts/upgrade/*` + `scripts/stop-ensemble.sh` + `scripts/install-dir-guard.sh` | `executor_family.sh` (one definition for payload + flat install) | The **complete executor family** — the release-staged invoke path depends on it (§5.1) |
| `daemon/plugin_subsystem/` slice | `promote_staleness.py` + `config_compat_check.py` + `config_compat_contract.py` | The promote-time staleness gate + config-compat entry check |
| `manifest.json` | generated | §2.2 |

**Never in a payload:** any `.env` (ADR-014/m6 — the `ENSEMBLE_SELF_ENV` marker is staged into `INSTALL_DIR/.env` instead); any `launchd` plist (operator-side; `make plist-install` stages them into `<install>/data/`).

### 2.2 Manifest + provenance sidecar + the "4-way" discipline

**`manifest.json` field set** — identity: `version`, `binary_version`, `staged_at`, `known_schema_gen`, `contains_contract_phase`, `rollback_safe` · checksums: `binary_sha256`, `config_sha256`, `launcher_sha256` · tree aggregates: `agents_tree_sha256`, `frontend_tree_sha256`, `plugins_tree_sha256`, `scripts_tree_sha256`, `guidelines_tree_sha256`, `daemon_slice_tree_sha256` · per-file maps: `agents_manifest`, `frontend_manifest`, `plugins_manifest`, `guidelines_manifest`, `scripts_manifest`, `daemon_slice_manifest` · provenance: `pipeline_scripts_floor_commit`, `pipeline_scripts_rev`.

**Provenance sidecar** — `<artifact>.build-provenance.json`, sibling to the artifact (e.g. `dist/ensemble-prod.build-provenance.json`, `frontend/dist/frontend/browser/index.html.build-provenance.json`). Required fields: `git_head`, `git_head_short`, `git_dirty`, `build_at`, `build_tool`, `artifact_sha256`, `artifact_path`. Written atomically.

**The "4-way sha" is a chain, not a single equality.** The brief's framing ("release dir == dist == manifest == sidecar") is a useful shorthand, but the enforced legs are:

1. **Build time** — sidecar `artifact_sha256` == `sha256(artifact)` → else `provenance-hash-mismatch`.
2. **Build time** — sidecar `git_head` == current HEAD → else `stale-provenance`.
3. **Build time** — sidecar `git_dirty` == false → else `dirty-build`.
4. **Stage time** — `manifest.json` records the per-tree/per-file shas; `integrity_verify` re-hashes the staged release dir and compares against the manifest (binary, launcher, config, and each tree's per-file map + aggregate).

`promote.sh` runs `integrity_verify` on **both** CURRENT (drift check) and TARGET.

**Refusal tokens (stage-time freshness guard):** `non-tip-tree` · `provenance-missing` · `provenance-malformed` · `stale-provenance` · `dirty-build` · `provenance-hash-mismatch`. Override: `--allow-stale-stage` (argv-only, journaled durably on the install dir).

### 2.3 Annotated tag

**Convention:** annotated (`git tag -a`), tagger `giter <giter@ensemble.local>`, message beginning `Release v<VERSION>`, pointing at the **version-bump commit** (the commit HEAD at stage time).

> Observed drift: `v0.30.13`'s target commit subject is `chore(release): bump version to 0.30.13`; `v0.30.11`'s is `! New version 0.30.11` (bump_version.py's own message). Both forms exist in history. **Use `chore(release): bump version to 0.31.0`** — it is the most recent precedent and reads correctly against `git describe`.

**Hard constraint:** `stage.sh:203` runs `git -C "$REPO_ROOT" describe --tags --exact-match HEAD` and requires the result to equal `VERSION`, else **exit 78**. The tag must therefore be created **before** staging, on the exact commit being staged.

### 2.4 The promote command shape

```
# PRIMARY — release-staged (runbook §10.1 default; the payload is the invoke path)
cd /home/nea/agents-ensemble/releases/v0.31.0
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.31.0 \
  bash scripts/upgrade/promote.sh live --f2-verified-closed
```

Two **independent** live guards, both required (`promote.sh:129`, `:142`):
- `require_live_guard` — `ENSEMBLE_UPGRADE_LIVE=1`.
- `--f2-verified-closed` — the explicit operator flag; absence ⇒ refusal token **`f2-not-verified`**.

`promote.sh` is **nonce-free**; it gates on `require_live_guard`, `--f2-verified-closed`, and livez/readyz/soak. The nonce belongs to the **tool lane** (§6.1).

### 2.5 The version bump

`scripts/bump_version.py` (house tool) — reads the current version from `pyproject.toml`, writes **four** sites (`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`), runs `uv sync`, and commits `! New version <v>`. It folds `CHANGELOG.md` into the commit **only if** the `[Unreleased]` header was already renamed.

**Recommended order (giter):**
1. Edit `CHANGELOG.md`: `[Unreleased]` → `## [0.31.0] — 2026-11-17`, and paste the §3.6 block under a `### Added — Agent group chat` heading.
2. Run `python scripts/bump_version.py minor` → `0.31.0`.
3. Commit the release-limb files (`pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`, `CHANGELOG.md`) with subject `chore(release): bump version to 0.31.0`.
4. `git tag -a v0.31.0 -m "Release v0.31.0"`.

*(If `bump_version.py` is bypassed, all four version sites must be updated by hand and `uv.lock` re-synced — the four-site lockstep is what `tests/test_api.py` and the frozen binary's `__version__` both assert.)*

#### 2.5.1 The cut sequence — ORDERED (the FE rebuild MUST follow the bump)

> **bump commit → FE REBUILD at that HEAD → sidecar re-stamped → `stage.sh`.**

**Why the order is forced (not stylistic) — four code facts:**

| Step | Code fact |
|---|---|
| `stage.sh` does **NOT** rebuild the FE | `:348` — if `frontend/dist/frontend/browser` is missing ⇒ **exit 78** ("refusing to stage a UI-less release"). The FE is a **prebuilt input**, not a stage step |
| `stage.sh` **VERIFIES** the sidecar, then **COPIES** the prebuilt tree | `:360` (verify) · `:468` (`cp -R` of the prebuilt `browser/` into the payload) · `:498` (`rm -f` the sidecar out of the staged copy) |
| `_verify_artifact_provenance` refuses a mismatched sidecar | the sidecar's `git_head` must equal the staging `HEAD_SHA`, else refusal token **`stale-provenance`** (exit 78) |
| `_build_frontend.sh` does **NOT** install dependencies | it runs `npm run build` only — **no `npm ci` / `npm install`** ⇒ **`node_modules` must already exist** |

⇒ **The bump commit changes HEAD ⇒ the sidecar's `git_head` goes stale ⇒ the stage refuses.** The only correct order is to **rebuild the FE at the bumped HEAD** (re-stamping the sidecar with the new `git_head`) and only then stage.

> ⚠️ **The currently-present `frontend/dist/` is ALREADY STALE and MUST NOT be treated as stageable.** Its sidecar stamps `git_head = 913088277b9a55cabfed84ba4c10d6b5c327068c`, while the branch HEAD has moved to `af661ac8e…` ⇒ `_verify_artifact_provenance` would refuse the stage with `stale-provenance` (exit 78). The S3 recipe (§3.5.5) must be **re-run at the bumped HEAD**.

**`frontend/node_modules` is deliberately KEPT** for exactly this reason: `_build_frontend.sh` does not install dependencies, so the post-bump rebuild needs the existing `node_modules` (691 MB) in place. (It is also what the disk decision in §5.2.1 keeps warm.)

### 2.6 Release-dir retention — the eviction nobody plans for

`RETENTION_KEEP=3` (`lib.sh:403`); `retention_evict` (`lib.sh:8504`) keeps the 3 newest by manifest `staged_at` (dir-mtime fallback) and **never evicts `current` or journal `previous`**.

Current state: `releases/` = `v0.30.9` (journal `previous` — **pinned**), `v0.30.11`, `v0.30.12` (journal `current` — **pinned**). Staging a 4th dir makes the total 4 > 3 ⇒ **evicts `v0.30.11`** (oldest non-pinned). Expected and harmless — but it must be stated in the ceremony notes so the missing dir is not mistaken for damage.

---

## 3. WHAT THE RELEASE MUST CARRY

### 3.1 The three migrations — and how they actually reach a live PostgreSQL

| # | File | UP shape | DOWN shape | Destructive-DDL grep match? |
|---|---|---|---|---|
| M1 | `20261115_080000_create_chatroom_tables.sql` | 3 × `CREATE TABLE IF NOT EXISTS` (`chatrooms`, `chatroom_members`, `room_messages`) + 4 indexes | `DROP INDEX` ×4 + `DROP TABLE` ×3 | **YES** |
| M2 | `20261115_120000_add_room_proposal_dedup_columns.sql` | 3 × `ALTER TABLE room_messages ADD COLUMN` (`source_message_id`, `trigger`, `member`) + `CREATE UNIQUE INDEX uq_room_messages_proposal_dedup` | `DROP INDEX` + `DROP COLUMN` ×3 | **YES** |
| M3 | `20261116_083000_add_room_resolution_warnings.sql` | `ALTER TABLE room_messages ADD COLUMN resolution_warnings JSON` | `ALTER TABLE … DROP COLUMN resolution_warnings` | **YES** |

**The runner is SQLite-only.** `MigrationRunner.run_pending_migrations` (`runner.py:395-410`) is a **no-op for non-SQLite engines** ("Skipping migrations for non-SQLite database"). PostgreSQL schema therefore arrives by a **different** path:

- **Fresh PG** → `SQLModel.metadata.create_all(self._engine)` (`manager.py:1068`), which runs **after** the room models are registered (`manager.py:1062-1067`: `from .repositories.room.models import ChatRoom, ChatRoomMember, RoomMessage  # register before create_all`). ⇒ **the three room tables are created on PG by `create_all`, not by the `.sql` files.**
- **Existing PG** → `_ensure_postgres_columns` (`manager.py:8578`) adds the P3/P5 columns and the unique index via `ALTER TABLE room_messages ADD COLUMN IF NOT EXISTS …` (`manager.py:8908-8945`) — **idempotent**, `IF NOT EXISTS` throughout.

**Do the `.sql` files even ship?** Yes — **inside the binary**. `ensemble.spec:12-13` bundles `('daemon/migrations', 'daemon/migrations')` as datas, and the runner resolves `Path(__file__).parent / "versions"` (`runner.py:211`) → under PyInstaller that is `_MEIPASS/daemon/migrations/versions`. So a staged release **can** apply migrations on a fresh SQLite DB; there is **no** top-level `daemon/migrations/` tree in the payload, and none is needed.

**Net answer to the brief's question:** the release carries the three `.sql` files *via the frozen binary*; on **SQLite** the runner executes them; on **PostgreSQL** the runner never runs and the schema is established by `create_all` (tables) + `_ensure_postgres_columns` (columns/index). **The release verification must prove BOTH drivers separately** (§5.3, leg S2).

**Dialect hazards checked — none present.** No partial-index `WHERE` clauses (so the SQLModel Boolean partial-index trap, `WHERE col<>0` being PG-ILLEGAL, does not apply). No `NOT NULL` on a pre-existing population (all three tables are new). FKs are `NO ACTION` and self-contained; `M2`/`M3` require SQLite ≥ 3.35.0 for the **DOWN** `DROP COLUMN` only (the runner gates on it).

**Conclusion — the release can boot green on a live PG that has never seen these migrations.** `create_all` creates the tables; `_ensure_postgres_columns` is idempotent; `run_room_boot_regen` (`daemon/repositories/room/boot.py`) is **best-effort** (wrapped, failure ⇒ WARNING, boot proceeds — `manager.py:4908-4928`); `RoomGcSweeper` no-ops safely when the service is absent (`unavailable=True`) rather than crashing boot.

### 3.2 What the release verification must PROVE per driver

| Driver | What must be proven | Where it is proven |
|---|---|---|
| **SQLite** | The runner executes **all three** migrations end-to-end: `pending_versions == ["20261115_080000","20261115_120000","20261116_083000"]`, applied list matches, ledger rows written with `skip_reason=None`, the F1 pre-marked migration is not re-pending, columns pinned, `NO ACTION` FK enforced, index/schema lockstep, DOWN drops exactly what UP created, and the proposal-dedup UNIQUE admits one proposal per `(source_message_id, trigger, member)` while never colliding on all-NULL rows | `tests/unit/repositories/test_room_migration_runner_execution.py` (O-1) |
| **SQLite** | Exactly one `*_create_chatroom_tables.sql` exists; the stamp convention; 3 × `CREATE TABLE IF NOT EXISTS`; models registered with `SQLModel.metadata`; lifecycle columns at birth; unique-backstop names | `tests/unit/repositories/test_room_models_and_migration.py` |
| **PostgreSQL** | `create_all` creates the three tables from the **registered** models, and `_ensure_postgres_columns` adds the four columns + the unique index idempotently | ✅ **CLOSED by measurement (2026-11-17 release-prep tester, LEG 1 — PASS-WITH-NOTES).** PG parity **HOLDS** on a real scratch PG. See §3.2.1 for the measured evidence. |
| **Boot** | Room tables ready at boot; boot regen is non-fatal; the GC latch is two-sided (READ pins on success+retry, WRITE pins unconditionally, RAM-only, restart re-arms) | `tests/unit/repositories/test_room_boot_f1_pin.py`, `test_room_capability_row_pin.py` |

**Conclusion for §3.2:** the release is green on **both** drivers — the SQLite runner path is pinned by `test_room_migration_runner_execution.py` + `test_room_models_and_migration.py`, and the PostgreSQL path is **measured** (§3.2.1). The one thing no leg substitutes for is a **live boot** of the staged payload — that is S5 (§5.4).

### 3.2.1 PG parity — MEASURED (CLOSES top risk #2)

**Verdict: PG parity HOLDS. No release blocker.** (Source: `.agents/tester/RESULTS/2026-11-17-group-chat-v0310-release-verification.md`, LEG 1, commit `463044ce4`.)

**Harness:** `ensemble_dev_scratch` @ 127.0.0.1:5432, role `ensemble_dev_app`, **PostgreSQL 16.15**, driver `postgresql+psycopg` (matching `daemon/repositories/factory.py:387-406`). Per-variable scrub; effective `POSTGRES_DB` asserted `ensemble_dev_scratch` — **never** `ensemble_prod`, never `ensemble_dev`. Isolation via a **private schema** `gc_pg_probe` (`connect_args options="-c search_path=gc_pg_probe"`), `public` deliberately off the path.

| Probe | Result |
|---|---|
| Fresh-PG `create_all` — the daemon's own registration imports (ast-extracted from `InstanceManager.__init__`, `daemon/manager.py:860-1067`) then `SQLModel.metadata.create_all` (`:1068`) → **100 tables** | ✅ `uq_chatroom_members_room_member_name UNIQUE (room_id, member_name)` and `uq_room_messages_room_seq UNIQUE (room_id, seq)` both live in `pg_constraint` |
| `_ensure_postgres_columns` ("existing PG" path) — fixture = the genuine pre-P3/P5 shape (cross-checked against the real pre-existing `public.room_messages`) | ✅ 10 cols → **14 cols**; adds `source_message_id` / `trigger` / `member` / `resolution_warnings` + `uq_room_messages_proposal_dedup`; **run 2 idempotent** (no duplicate column, identical index/constraint counts) |
| Behavior — real `RoomChatService` → `RoomRepository` | ✅ create + owner seed (`state='active'`, `message_count=0`, `role='owner'`, cursors `0`); P2 join-with-collider → `capturer-1`; seq `[1,2,3]` strictly monotonic and per-room; cursor advance `2`→True, `1`→**False** (monotonic MAX guard); `after_seq=2`→exactly 1 message; **cross-room leakage = 0 rows**; non-member read → `RoomNotFoundError`; purge → **all three tables emptied for that room**, control room untouched, **exactly one** content-free `room_purged` event |
| P3 dedup + P5 JSONB | ✅ PG NULLs distinct (matches SQLite); duplicate proposal → `UniqueViolation`; different `trigger` accepted; `resolution_warnings` round-trips as `jsonb` |
| `public` never written | ✅ marker row landed in the private schema; `public.event` `n_tup_ins` stayed 0→0 |
| PG-vs-SQLite DDL parity | ✅ column set + nullability + types identical on all 3 tables (parsed with the runner's own UP/DOWN split) |

**Cleanup re-verified (tester, post-run):** `schemas now = ['public']` (baseline MATCH) · probe residue (`gc_pg_probe%`) = **0** · the P3/P5 columns were **never** added to `public` · the scratch DB left **as found**.

**Notes (non-blocking):** **N1** PG has **no server-side DEFAULTs** where the SQLite DDL carries `DEFAULT 'active'/0/'message'` — every product path builds rows through the ORM with Python-side defaults, so no impact today (→ follow-up §8.4 F-2). **N2** `uq_room_messages_proposal_dedup` is a `pg_constraint` on the fresh-`create_all` path but a bare `CREATE UNIQUE INDEX` on the `_ensure_postgres_columns` path — **enforcement identical** (proven firing). **N3** physical column ORDER differs between drivers (set/nullability/types identical) — not a contract. **N5** `ensemble_dev_scratch` is **not** a blank DB; its `public` room tables are a genuine pre-P3/P5 shape and were left untouched.

### 3.3 The tool-category artifact + VOCABULARY_VERSION — ship as a PAIR, do not regenerate

| Item | Value at tip | Location |
|---|---|---|
| `room` category registered | `CATEGORY_MODULES["room"] = "daemon.tools.room"`; `register_tool_category("room")` call sites | `daemon/tools/_tool_registry.py` (:1534, :350, :903) |
| Tool verbs | `room_create`, `room_post`, `room_read`, `room_task_decision`, `room_close` (**five**) | `daemon/tools/_tool_registry.py` (:359-363, :2117-2121) |
| Artifact | `count: 66`, `vocabulary_version: 11`, contains `"room"` | `.agents/shared/metrics/tool-categories.json` |
| Constant | `VOCABULARY_VERSION: int = 11` | `daemon/checkpoint_metrics.py:290` |
| Agreement pin | artifact ⇄ derived-from-registry ⇄ constant | `tests/unit/tools/test_tool_category_artifact.py::TestToolCategoryArtifact::test_artifact_is_generated_from_the_tool_registry` |

**Rules for the release lane:**
- The artifact is **frozen**. `room_close` was added **after** the category was minted and deliberately added **no new category and no vocabulary bump** (decisions.md A-P6-35(a): *"a new VERB in an existing category adds no CATEGORY — the category now carries FIVE verbs; the version stays 11 and the frozen artifact is untouched"*). **DO NOT regenerate the artifact** — regeneration is a scratch-path operation (`ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=…`, never hand-edit, never rewrite the committed file from a test run), and the release has no reason to touch it.
- **Transport — the artifact rides INSIDE the frozen binary**, not as a payload tree. `ensemble.spec` bundles `('.agents/shared/metrics/tool-categories.json', '.agents/shared/metrics')` as a **datas** entry, and `daemon/checkpoint_metrics.py:418` (`TOOL_CATEGORY_ARTIFACT_RELPATH`) resolves it against the repo root (`Path(__file__).parents[1]`), which under PyInstaller lands at `_MEIPASS/.agents/shared/metrics/tool-categories.json`. The datas comment states *why* the artifact is canonical over a live derivation: *"`doc_maintenance` is registered only inside tool factories, so a live derivation misses it"*. ⇒ **a rebuild at the release commit ships the tip artifact automatically** — no payload copy, no staging step, nothing for the release lane to move. What must NOT happen is an *edit* to it (first bullet above).
- The artifact + the constant **must ship together** (both are inside the binary: the artifact as datas, the constant as compiled code). If either drifts, the drift check REDs in CI; at runtime an unknown category folds into the reserved `"other"` bucket (silent degradation, not an error).
- **The 8 agent `meta.json` grants are meaningless without the category, and the category is meaningless without the grants** — both must ship in the same release. `room` is a **privileged** category (not auto-granted).

### 3.4 The 8 agent meta.json grants — verified at tip

`ari` · `leader` · `sonic-leader` · `worker` · `video-capturer` · `video-editor` · `video-producer` · `video-verifier` — each gained `"room"` in its `tools.allow` array in the feature range.

This is a **ratified-phase contract**, not a lane accident (decisions.md A-P5-2 / A-P5-17b): the grant census is EIGHT agent types — the four warm-lane agents plus the video-team pilot quartet, the user's designated FIRST pilot (KV `gc_pilot_video_team`). The `room` audience is **not** a fixed privileged panel — the authority is the **membership gate in the tool bodies** (a granted agent that is not an ACTIVE member gets nothing, fail-closed, before any write). Membership is granted **at spawn** (`join_room` spawn parameter), so the membership row exists before the child's tools are built.

**Release implication:** `agents/` is a staged payload tree (§2.1), so the 8 grants ship automatically. The release lane must nonetheless **assert** that all eight carry `"room"` at the release tag — the two re-pinned `ari` tail assertions (POK grant pack 6→7 tail entries; nest-anomaly-report pack 12→15 entries) are the pins that must stay green.

### 3.5 The frontend asset set

**14 source files** (the §13.1 P6 FE surface): `frontend/src/app/models/room.model.ts` · `models/index.ts` · `services/room.service.ts` (+ `.spec.ts`) · `components/room-dialog/{room-dialog.component.ts,.html,.scss,.spec.ts}` · `pages/chat/chat.html` · `chat.component.ts` (+ `.spec.ts`) · `chat.scss` · `styles.scss` · `app.component.spec.ts`.

**How the FE ships:** `scripts/upgrade/_build_frontend.sh` runs the Angular build inside `frontend/` → `frontend/dist/frontend/browser/`, then stamps the provenance sidecar `frontend/dist/frontend/browser/index.html.build-provenance.json`. `stage.sh` **verifies** the sidecar against the SOURCE tree and then **`rm -f`s it out of the payload** (`stage.sh:498`) — the payload tree is web-servable, and the manifest per-file map is computed **after** the rm, so the sidecar is **not** part of the release's integrity contract either.

> **⇒ The sidecar must exist in the SOURCE tree at stage time. It does NOT ship.**

**House rule (mandatory):** TypeScript only via `frontend/`'s local devDependency — `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`. **Never** bare `tsc` / `npx tsc` outside `frontend/` (the 2026-10-03 incident: `npx` fetched and executed a registry package named `tsc`).

#### 3.5.1 Toolchain — the "system v22" reference is STALE

| Claim | Measured truth |
|---|---|
| The stage-lane blueprint says "system v22" | ❌ **STALE.** What actually runs is **nvm v24.21.0 / npm 11.19.0** — nvm is first on PATH |
| `frontend/package.json` `engines` | **NO `engines` block at all.** `packageManager: npm@10.9.4` is **advisory** only |
| `@angular/cli@21.2.5` engines | `node: ^20.19.0 \|\| ^22.12.0 \|\| >=24.0.0` · `npm: ^6.11.0 \|\| ^7.5.6 \|\| >=8.0.0` — **both** v22 and v24.21.0 satisfy it, and so does npm 11.19.0 |

⇒ **The recipe PINS v24.21.0 / 11.19.0 rather than inheriting PATH** — a pinned toolchain is the reproducible one. (The house "no Node upgrades without reporting back" rule is respected: nothing was upgraded; an already-installed nvm runtime was *selected*.)

#### 3.5.2 The built artifact set — the Angular 21 shape (NOT a defect)

A reviewer expecting a polyfills bundle or an `assets/` directory finds neither. That is the **correct Angular 21 output**, not a missing-file bug:

| Expected | Actual | Why |
|---|---|---|
| `polyfills-*.js` | **ABSENT** | `angular.json` has **no `polyfills` key** — Angular 17+ inlines polyfills into `main` |
| `assets/` dir | **ABSENT** | the only `assets` entry is `{"glob": "**/*", "input": "public"}` ⇒ the `public/` tree is flattened to the **top level**: `favicon.ico`, `favicon.svg`, `apple-touch-icon.png`, `icons/` |
| `scripts-*.js` | **present — 3,559,319 B** | it is `mermaid.min.js` — the only entry in `angular.json`'s `scripts` array |

**Measured inventory of `frontend/dist/frontend/browser/` — 96 files / 7,949,189 B (7.58 MiB):**

| Item | Count | Bytes |
|---|---|---|
| `chunk-*.js` | **85** | 3,480,246 |
| `index.html` | 1 | 9,257 |
| `main-*.js` | 1 | 217,094 |
| `styles-*.css` | 1 | 655,629 |
| `scripts-*.js` (mermaid) | 1 | 3,559,319 |
| icons + favicons (`favicon.ico`, `favicon.svg`, `apple-touch-icon.png`, `icons/favicon-{16,32,48}.png`) | 6 | 27,292 |
| provenance sidecar | 1 | 352 |
| **total** | **96** | **7,949,189** |

*Correction to the briefing note:* the chunk count is **85**, not 87 — the **byte total (3,480,246) agrees exactly**. The file census reconciles as **85 chunks + 8 root entries + 3 `icons/` files = 96**. (The sidecar's `build_tool` label is the static string `"npm run build:frontend"` stamped by `_build_frontend.sh:105`; the npm script actually invoked is `build` → `ng build`. The label is descriptive, **not** a resolvable script name.)

**Sidecar content (measured):** `git_head = 913088277b9a55cabfed84ba4c10d6b5c327068c` (**== HEAD**) · `git_head_short = 913088277b9a` · `git_dirty = false` · `build_at = 2026-11-17T11:36:28Z` · `artifact_sha256 = 14d5b946c1c13258181887461f5dc81957ac0dce18d66c21f3f077bc0a24f7ee` — **re-verified independently** against the on-disk `index.html` ⇒ **MATCH**.

#### 3.5.3 🔎 VERIFICATION TRAP — the room surface IS present, but NOT greppable by identifier

The room-dialog surface **is** in the bundle. But **esbuild MANGLES identifiers**, so the natural greps **false-negative**:

| Marker | Greppable? | Where |
|---|---|---|
| `RoomDialogComponent` | ❌ **0 files** | — (mangled) |
| `ROOM_DIALOG_SIZE` | ❌ **0 files** | — (mangled) |
| `app-room-dialog` | ✅ 1 file | `chunk-HD2VKB7Y.js` |
| `/api/rooms` | ✅ 1 file | `chunk-HD2VKB7Y.js` |
| `next_after_seq` | ✅ 1 file | `chunk-HD2VKB7Y.js` |
| `room-dialog-panel` | ✅ 2 files | `chunk-HD2VKB7Y.js` **+** `styles-T4RYPQ7P.css` |

⇒ **Anchor the S3 assertion on the four string-literal / CSS-class markers above** — never on the identifier names.

> **Run the marker greps against the REBUILT bundle at the bumped HEAD** (§2.5.1). The marker set is **unchanged** (string literals / CSS classes) — only the bundle changes.

**Second trap — the chunk is NOT a modulepreload.** `index.html` contains exactly **one** `modulepreload` occurrence and it does **not** name the room chunk; the room chunk is **lazily referenced from `main-*.js`**. ⇒ a naive *"is the room chunk listed in `index.html`?"* check **false-negatives**. Assert on the **bundle** (the four markers), **not** on `index.html`'s preload list.

#### 3.5.4 DIRT PRECISION — `frontend/.angular` is NOT ignored

`frontend/.gitignore` ignores **only** `/.angular/cache` (plus `/dist`, `/node_modules`). The `.angular` directory **as a whole is not ignored** ⇒ a cache write **outside** `.angular/cache` **dirties the tree** — and `stage.sh` counts `??` (untracked) as dirty. ⇒ after an FE build, **`git status --porcelain` must be EMPTY**; that is part of the S3 pass criterion, not a nicety.

#### 3.5.5 The measured S3 recipe

```bash
# ── S3 FE BUILD — measured recipe (devops dry-run, release worktree @ 913088277).
#    PIN the toolchain; do NOT inherit PATH.
cd /home/nea/ensemble-src-wt-group-chat-release
export PATH="/home/nea/.nvm/versions/node/v24.21.0/bin:$PATH"   # node v24.21.0 / npm 11.19.0
node --version && npm --version                                  # → v24.21.0 / 11.19.0

cd frontend
npm ci                                                          # exit 0
#   npm 11 SKIPS 5 install-scripts behind allowScripts — EXPECTED, non-fatal.
#   Verified: esbuild ships via @esbuild/linux-x64 (no postinstall needed).
./node_modules/.bin/tsc --noEmit -p tsconfig.app.json            # exit 0 (TS 5.9.3, LOCAL devDependency)
cd ..

#   IMPORTANT: invoke THIS worktree's scripts BY PATH. The ambient
#   ENSEMBLE_UPGRADE_SCRIPTS_DIR still points at the STALE pin
#   (ensemble-src-wt-scripts-pin @ bf4bfde6e — 8 commits behind the floor).
#   THIS worktree's own scripts/upgrade/lib.sh:95 declares
#   PIPELINE_SCRIPTS_REV=1aa0065e… — which SATISFIES the floor.
bash scripts/upgrade/_build_frontend.sh                          # exit 0

# ── verify ────────────────────────────────────────────────────────────────
test -f frontend/dist/frontend/browser/index.html                              # the FE index
test -f frontend/dist/frontend/browser/index.html.build-provenance.json       # the SOURCE-tree sidecar
git status --porcelain                                                         # MUST be EMPTY

# ── assert the room surface IS in the bundle (string literals, NOT identifiers)
grep -rl "app-room-dialog"  frontend/dist/frontend/browser/   # → chunk-*.js
grep -rl "/api/rooms"       frontend/dist/frontend/browser/   # → chunk-*.js
grep -rl "next_after_seq"   frontend/dist/frontend/browser/   # → chunk-*.js
grep -rl "room-dialog-panel" frontend/dist/frontend/browser/  # → chunk-*.js + styles-*.css
```

### 3.6 The CHANGELOG entry

**Convention:** root `CHANGELOG.md` in Keep-a-Changelog format, `[Unreleased]` at the top, one `## [<version>] — <date>` section per release, subsections `### Added` / `### Fixed`, each bullet citing commit SHAs and gate results. The per-feature precedent is `docs/changelog-pending/<feature>.md` — a file holding **the exact block to be sliced in verbatim** (the release coordinator copies the block, no edits).

**This lane should:** create `docs/changelog-pending/group-chat.md` holding the exact `### Added` block below, and the release coordinator slices it into `CHANGELOG.md` under `[0.31.0] — 2026-11-17`.

> **Ready-to-slice block:**
>
> ```markdown
> ### Added — Agent group chat (this lane; three additive migrations)
>
> - **Substrate — a 3-table room store** (`daemon/repositories/room/`, migration `20261115_080000_create_chatroom_tables.sql`): `chatrooms` / `chatroom_members` / `room_messages` with per-room `seq` cursors, denormalized `message_count` / `last_post_at`, and a durable log as the single source of truth. Every existing delivery lane is reused as transport; `message_queue` and `shared_meta_kv` are untouched. PostgreSQL is established by `create_all` + `_ensure_postgres_columns` (the migration runner is SQLite-only); the `.sql` files ship inside the frozen binary.
> - **Tool surface #23 — the `room` category** (`daemon/tools/room.py`, five verbs `room_create` / `room_post` / `room_read` / `room_task_decision` / `room_close`): privileged category registered in `daemon/tools/_tool_registry.py`; the tool-category artifact regenerated to 66 categories at `vocabulary_version` 11 (paired with `VOCABULARY_VERSION` in `daemon/checkpoint_metrics.py`); `room` allow-grants land on eight agent types (`ari`, `leader`, `sonic-leader`, `worker`, `video-producer`, `video-capturer`, `video-editor`, `video-verifier`). Authority is the membership gate in the tool bodies (fail-closed), not the grant.
> - **Delivery, naming, and nudges**: the naming cascade (parent-provided name → quick-model inline generation → deterministic `agent-name + N` fallback → per-room numeric suffix); the hybrid delivery model (unread nudge at todo-step boundaries, completion-boundary nudge with a loop-guard, `@tag` = mid-turn injection, tagged-to-terminal = task proposal routed to the parent); the joined-room digest block (guidelines + short digest only — never the history).
> - **Observability**: a content-free room SSE channel (`add_room_channel`), a durable content-free audit trail (`room_tag_delivery` and friends — ids and outcomes, never message text), and a frontend chatrooms icon opening a large modal (instance → rooms → conversation) riding the same fail-closed room-read predicate as the agent tool.
> - **GC**: a two-sided dry-run-first room sweeper with an authoritative read, a fail-safe latch, and P-close retention (a closed room is archived after 14 days; an active room silent 72h with no live members is archived).
> ```
>
> *(`docs/changelog-pending/group-chat.md` is the file to hold this block — the release coordinator copies it verbatim.)*

### 3.7 The decisions / ledger docs, PACKS, QUARANTINE

| Artifact | State at tip | Release implication |
|---|---|---|
| `.agents/shared/planning/group-chat/` | 5 docs at tip: `architecture-recommendation.md`, `decisions.md`, `emperor-open-questions.md`, `plan-overview.md`, `substrate-map.md` | **Committed in-branch** — they travel with the source, not the payload. No release action. |
| `.agents/tester/PACKS.md` | The group-chat P6 re-gate #2 block (2026-11-17 @ `7e0cd2db0`) is registered, with pack rows `gc_p6_room_family` (24 files / 420 nodes), `gc_p6_room_mutation_matrix` (9/9), `gc_p6_pilot_scenario` (9/9), SSE-adjacent corpus (111/111), ledger gate (5/5), FE (140/140 + modal 8/8) | The **gate evidence already exists**. The release lane re-runs (leg S4) for confirmation at the release commit; it does not re-register packs. ⚠️ Note the count discrepancy: the merged-tree run measured **421**, the re-gate row says **420** — re-measure at the release commit and disclose. |
| `.agents/tester/QUARANTINE.md` | Two group-chat-adjacent entries: (a) a **STANDING STALE PIN** in `tests/unit/test_nest_report_rest_route.py` — base-attributed, lane-innocent, proven RED AT BASE; (b) the **P5 composition-flake class** (30 ids, shard-composition-driven, zero branch causal reach) | Both are **pre-existing and quarantined**. The release lane must **not** clear them, and must not count them as release reds. |

### 3.8 The two room migrations the brief named only two of — plus the third (§0 C-1)

Reiterated because it is the highest-severity omission in the original brief: **M3 (`20261116_083000_add_room_resolution_warnings.sql`) ships in this release.** A release that carried only M1+M2 would leave `room_messages.resolution_warnings` absent — and the P6 resolution-warning surface (the SSE room-resolution pre-branch + the audit trail) would fail at runtime on a PG served by `create_all`+`_ensure_postgres_columns` (the column would never be added, because `_ensure_postgres_columns` at `manager.py:8943` is what adds it — and it *is* present at tip, so this only bites if M3's PG-side ALTER is missing; it is present, so the release is correct **only because M3 is included**).

---

## 4. PROMPT-ARTIFACT DISCIPLINE CHECK

### 4.1 Verification result — **CLEAN, and the baseline MUST NOT move**

| Check | Method | Result |
|---|---|---|
| Did the feature touch any `agents/*.md`? | `git diff --stat <base> origin/latest -- 'agents/*.md'` where base = `f3cfb98fe4eeac4b0f6b2a2b0fd5f4e81a04e6ee` (merge-base of `origin/feature/group-chat-design` and `origin/latest`) | **EMPTY — zero prompt artifacts touched** |
| Did the seal file change? | `git diff --stat <base> origin/latest -- agents/.prompt-artifacts.sha256` | **EMPTY — untouched** |
| Does the feature's design forbid it? | decisions.md **A-P5-8**: *"The joined-room GUIDELINES are a STATIC CONSTANT in the builder module — NEVER an `agents/*.md` file. The prompt-artifact baseline (agents/*.md is DB-versioned and prompt-sealed) must not move for this feature (RK-8), so the guidelines text lives beside the code that renders it (`ROOM_GUIDELINES_TEXT` in `daemon/services/context_messages.py`)."* | Consistent — the design deliberately kept the guidelines **out** of the prompt surface |
| House seal state | `agents/.prompt-artifacts.sha256` present, 20325 bytes, mtime 2026-11-14 (pre-feature) | Unchanged since before the feature range |

### 4.2 Does the release need a baseline bump?

**NO. It must not.** The release carries **no** prompt-artifact change, so `agents/.prompt-artifacts.sha256` is **byte-identical** between the v0.30.12 baseline and the v0.31.0 release. **No baseline bump, no seal re-pin, no prompt re-approval** — a bump here would be a false signal (it would assert a prompt change that did not happen).

### 4.3 The release verification leg for this

### 4.3.1 The fourth clause SPLITS — the binary embeds the set CONSTANT, not the artifacts

**Measured (release-prep tester, LEG 2).** The clause *"the frozen binary's embedded prompt-artifact set matches the seal"* is **HYBRID**, and the two halves have different gates:

| Half | What actually ships | Where | Gate |
|---|---|---|---|
| **The set CONSTANT** | `daemon.tools.prompt_artifacts`' frozenset `['growth.md','memory.md','rule.md','skill.md','soul.md','tools.md','tools_note.md','workflow.md']` + the `BASELINE_FILENAME` literal + the summary token | **Inside the binary's PYZ** | **Closable pre-cut — and CLOSED.** The constant's source is **byte-identical** across the range (`git rev-parse v0.30.12:daemon/tools/prompt_artifacts.py` == `HEAD:` == `24286fc4c47af95594b7e8783b9d1b6cfa54274c`), so the binary that will be frozen embeds the identical set; the seal's basenames are a strict subset of it (5 distinct basenames on disk ⊆ the 8-member constant) |
| **The artifacts + the seal** | The `.md` files and `agents/.prompt-artifacts.sha256` | **A PAYLOAD DIRECTORY** `releases/<ver>/agents/` — **not** inside the binary | **Byte-level re-hash is strictly POST-STAGE and already automated**: `stage.sh` runs `prompt_artifact_drift_leg` (`stage.sh:1001-1035`), which re-hashes the staged `agents/` tree against the shipped baseline. It is **reported, not gating** (allow-stale policy): drift is logged + journaled and staging continues |

**Proof that the binary carries no `agents/` tree:** `ensemble.spec`'s `datas` holds exactly **four** entries — `pyproject.toml`, `daemon/migrations`, `daemon/services/live_views/assets`, `.agents/shared/metrics/tool-categories.json` — and **no `agents/` entry**; confirmed against the **live** binary via `pyi-archive_viewer -l`. The frozen daemon resolves its agents root at runtime as `Path(sys.executable).parent/'agents'` — i.e. the **staged payload directory**.

**And `meta.json` is provably outside the seal:** `VERSIONED_PROMPT_ARTIFACTS` (`daemon/tools/prompt_artifacts.py`) is `.md`-only and `_depth2_match()` matches only filenames in that set ⇒ `meta.json` can never be sealed or emitted (so the 8 room grants never trip the seal).

> **Ceremony brief, one line:** *S1d = **3 git checks** (hard gate; closable and closed pre-cut, but **MUST be re-run AT the tag**) + a **stage-time payload re-hash** (reported, non-blocking, automated).*

### 4.3.2 WATCH-1 — S1d must be re-run AT THE TAG

The parked branch **`feature/video-mock-prompts`** carries commit **`e559b4b80`** ("re-pin 11 video-team seal lines after mock-scene prompt edits"), which changes **11 seal rows** *and* **8 `agents/*.md`** (video-capturer / editor / producer / verifier `rule.md` / `soul.md` / `workflow.md` + `video-producer/skills-template/video-planning.md`).

It is **not an ancestor of HEAD nor of v0.30.12** ⇒ outside today's range, and S1d is unaffected **today**. **But if that branch merges into the release line before the v0.31.0 tag is cut**, §4.3 check #2 flips non-empty and check #3 becomes a **legitimate re-pin**.

⇒ **S1d is a PRE-CUT check that must be RE-RUN AT THE TAG.** The user's 2026-11-15 directive parks the video-team lane until *after* group-chat promotion, and the video-team pilot runs post-promote — so the merge order matters. (Filed as anomaly `c2a34dcb`.)

**Scope trap:** use the **plain** (no `--all`) form of `git log`. `git log v0.30.12..HEAD --all -- agents/.prompt-artifacts.sha256` returns `e559b4b80` — a commit on the **parked** branch, not an ancestor of HEAD or v0.30.12.

### 4.3.3 The S1d leg — as it now stands (re-run AT THE TAG)

**S1-integrity leg addition (call it S1d):** at the release tag, assert

```
git diff --stat v0.30.12 v0.31.0 -- agents/.prompt-artifacts.sha256   # MUST be empty
git diff --stat v0.30.12 v0.31.0 -- 'agents/*.md'                     # MUST be empty
sha256sum agents/.prompt-artifacts.sha256                             # MUST equal the v0.30.12 value
```

**Pass criterion: all three empty/equal.** Any non-empty diff is a 🔴 stop — it means a prompt changed outside the sealed lane and the approval lane was bypassed.

**Measured at the release tip (LEG 2, PASS):** the three checks anchored at `v0.30.12` are all EMPTY/EQUAL — `git diff --stat v0.30.12 HEAD -- agents/.prompt-artifacts.sha256` rc 0 · `git diff --stat v0.30.12 HEAD -- 'agents/*.md'` rc 0 · `git show v0.30.12:agents/.prompt-artifacts.sha256 | sha256sum` == `sha256sum agents/.prompt-artifacts.sha256` == `50561606a464df3d9484b93f78b1eb6880cc594badf27d69502d8d1f74c54979`. **Stronger than a content diff: the git blob is identical at both ends** — `git rev-parse v0.30.12:agents/.prompt-artifacts.sha256` == `git rev-parse HEAD:…` == **`cb51a277bea6334100f7f6139adfe6bef8512f7a`** — and the seal blob has been **frozen since before the previous release** (its last modification on the release line is `f0fa135e7`, 2026-11-14, an ancestor of v0.30.12). The **live shipped** `releases/v0.30.12/agents/.prompt-artifacts.sha256` (and `v0.30.11/`, `v0.30.9/`) carry the same digest, and the staged `manifest.json` pins `agents_manifest[".prompt-artifacts.sha256"]` to it. The repo's own checker passes `rc 0` against the **live staged payload**: `.venv/bin/python scripts/protection/generate_prompt_artifact_baseline.py --check --tree <worktree>` → `PROMPT-BASELINE: OK — 217 versioned artifact(s) match .prompt-artifacts.sha256` (0 missing / 0 drifted / 0 added / 0 malformed).

**Pathspec non-vacuity (proved):** the same `agents/*.md` pathspec against a known historical prompt edit (`f0fa135e7`, which edited `agents/ari/rule.md` + `tools_note.md`) correctly lists both — an empty diff on a pathspec that matches nothing would prove nothing.

**Census completeness:** of the **197** changed paths across the whole `v0.30.12..HEAD` range, exactly **8** are under `agents/` and **0** end in `.md`; `git diff --name-status` shows only the 8 `M` meta.json rows (no adds/deletes/renames).

**Chain-shape note (non-blocking):** P2's merge first-parent is `907f2f5917`, not P1's `e3787d4b8` — P2 was branched from an earlier tip. The census is unaffected (the aggregate base→HEAD diff is contiguous; every per-merge `agents/*.md` diff is empty), but **the release brief must not assume a linear phase chain**.

*(Context for the ceremony note: `agents/*.md` is DB-versioned, not file-written — runtime writes are refused at two `daemon/tools/filesystem.py` sites by `daemon/tools/prompt_artifacts.py` `check_write`, and edits run through a DB state machine with a human-session-only approval lane. A release that changes no `.md` file changes no prompt artifact and needs no approval.)*

---

## 5. PRECONDITIONS + CEREMONY

### 5.1 🔴 THE BINDING PRECONDITION — the scripts pin is stale against the tip floor

**The floor is a hardcoded daemon constant, not a file or a state.json field.**

| Channel | Site | Value at `origin/latest` |
|---|---|---|
| Daemon-side arm-time floor | `daemon/tools/upgrade_tools.py:4145` → `PIPELINE_SCRIPTS_FLOOR_COMMIT` | `1aa0065eb818ff00cb32730fab54d7f2060c42d8` |
| Scripts-side stamp | `scripts/upgrade/lib.sh:95` → `PIPELINE_SCRIPTS_REV` | `1aa0065eb818ff00cb32730fab54d7f2060c42d8` |

**They agree ✓** (D6-8: "the two travel together"), and the floor **is an ancestor of `origin/latest`** ✓ — so `stage.sh`'s release-time gate (`_pipeline_scripts_floor_verdict` + `_pipeline_scripts_stamp_verdict`) **will not refuse**: set ✓, 40-hex ✓, resolves in the tree ✓, ancestor of the built tree ✓, stamp == floor ✓.

**But the live pin is stale.** The live `.env` carries:

```
ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade
```

That worktree is at `bf4bfde6e`, which **does not contain `1aa0065e`** as an ancestor. The arm-time guard (`scripts_dir_provenance_verdict`, `upgrade_tools.py:4210`) compares the floor to the **pinned dir's HEAD** by git ancestry:

- git dir + stamp parsed from `<dir>/lib.sh` ✓ → floor set ✓ → `git merge-base --is-ancestor 1aa0065e <pin HEAD>` → **rc=1 → REFUSE `stale-scripts`**.
- On an **explicit** `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pin, a refusal is a **hard refusal** (the caller downgrades to a warning only for *fallback*-resolved dirs).
- **The refusal happens BEFORE `consume_pending_action` — the nonce is NOT burned.** The guard is working as designed, not regressing.

**And there is no fallback path.** `_resolve_scripts_dir` (`upgrade_tools.py:2031`) resolves in order: (1) explicit `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → (2) the repo checkout the module lives in — for a **frozen** binary `Path(__file__).parents[2]/scripts/upgrade` lands under `_MEIPASS`, where the frozen bundle does **not** carry `scripts/` (the executor family is staged as a *separate payload tree*) → (3) `<install_dir>/scripts/upgrade` — **verified absent**: `/home/nea/agents-ensemble/scripts/upgrade` does not exist. ⇒ With the pin unset, resolution yields `None` and the arm refuses `executor-scripts-unavailable` (fail-closed).

⇒ **The pin MUST be advanced. This is the one hard blocker, and it is a `.env` + worktree operation, not a code change.**

**How to establish it (giter / operator, before the ceremony):**

1. Re-point the scripts-pin worktree (or create a sibling pin worktree) at a commit whose HEAD **contains `1aa0065e` as an ancestor** — i.e. `origin/latest` (`56a5f4a00`) or the v0.31.0 release commit itself. Verify:
   `git -C <pin> merge-base --is-ancestor 1aa0065eb818ff00cb32730fab54d7f2060c42d8 HEAD` ⇒ rc 0.
   *(Do **not** touch the canonical `/home/ensemble-src` detached checkout. Respect the worktree claim KV.)*
2. Update the install `.env`: `ENSEMBLE_UPGRADE_SCRIPTS_DIR=<new pin>/scripts/upgrade`.
3. **Restart the daemon** so the tool lane re-reads `.env` — the resolution and the guard both read the live env of the daemon process.
4. Confirm the pin's `scripts/upgrade/lib.sh` carries the **same** stamp `1aa0065e` (the two-channel rule) and that `sha256sum <pin>/scripts/upgrade/lib.sh` matches the payload's copy.

**🔧 Invoke the release worktree's scripts BY PATH.** The ambient `ENSEMBLE_UPGRADE_SCRIPTS_DIR` still points at the **stale** pin, so anything that shells out to the upgrade scripts from the release worktree must call them **by path** (`bash scripts/upgrade/_build_frontend.sh`, `bash scripts/upgrade/stage.sh …`) rather than through the ambient pin. The release worktree's own `scripts/upgrade/lib.sh:95` declares `PIPELINE_SCRIPTS_REV=1aa0065eb818ff00cb32730fab54d7f2060c42d8` — which **satisfies** the floor — so the worktree's own scripts are the correct ones to run. (The measured S3 run used exactly this path.)

**The alternative — the release-staged path.** Per runbook §10.1 the release-staged promote is the **default**, and it needs no pinned checkout: the payload carries the whole executor family and its own `daemon/plugin_subsystem/` slice, and `stage.sh` fail-closes at stage time if the payload's own staleness predicate cannot execute. **The non-git payload path is safe from the floor gate by design** — `scripts_dir_provenance_verdict` on a **non-git** dir is **WARNING-ONLY**: *"a stamp arms, no stamp warns; NO refusal is reachable from that branch (AC11)"*. The payload's `lib.sh` carries the stamp `1aa0065e`, so a payload-driven promote **arms**.

⇒ **Recommendation: do BOTH.** Advance the pin (so the *tool-lane* arm can succeed) **and** run the promote from inside the payload (the §10.1 default). If the user prefers the operator lane only (no tool-lane arm), the payload path alone is sufficient — but then the ceremony has **no nonce and no 3-factor attestation**, and the journal shape differs (no `nonce_consumed` row). See §6.

**Floor re-pin — recommended but not blocking.** The group-chat feature introduced **zero** changes to `scripts/upgrade/` or `daemon/tools/upgrade_tools.py` (verified per-branch: p1, p2, p3, p4, p4-followups, p5, p6 all show **no** delta in those paths). The floor was last re-pinned by `f8dc874f7` (`fix(floor): ENSEMBLE-32 repair lane — PIPELINE_SCRIPTS_FLOOR_COMMIT re-pin covering config_compat_* family`), with two later scripts commits (`9d92b9f65` D-5 wrapper argv shift, `4ee4c44a1` D-6 contract resolution) landing **after** it. The floor therefore does not *name* the newest shipped script semantics — but the enforced predicate is ancestry-only, so it **passes**. 🟡 **Recommendation:** re-pin both channels to the v0.31.0 release commit (they travel together per §9.1) so the floor names the family actually shipped. **This is optional; it is hygiene, not a gate.** If re-pinned, re-stage afterwards (the manifest carries `pipeline_scripts_floor_commit`).

### 5.2 The scripts-pin floor — what floor THIS release needs

| Question | Answer |
|---|---|
| What floor does the release need? | **A floor that is an ancestor of the built tree AND an ancestor of whatever scripts dir the executor will run from.** At tip that is `1aa0065e…` on both channels. If the release re-pins (§5.1), the new floor must be the release commit itself, set **identically** in `upgrade_tools.py:4145` and `lib.sh:95`. |
| Is the pin satisfiable today? | **No** — the live pin (`bf4bfde6e`) predates the tip floor (`1aa0065e`). |
| What must be established first? | The pin advance in §5.1, steps 1–4, then a daemon restart to load the new `.env`. |
| What if it is not done? | The tool-lane arm **refuses pre-nonce** with `stale-scripts`. The nonce is preserved, so the ceremony can be retried after the fix — **no live damage, no partial flip**. |
| Is there a bypass? | `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` bypasses with a loud **warn** verdict — per-invocation only, never persisted to `.env` (T6.12). **Do not use it.** It exists for drills, and using it on live would run stale executor scripts against a new payload — the exact hazard the guard exists to stop. |

### 5.2.1 ✅ Host disk — RESOLVED BY MEASUREMENT (not by pruning)

**Reading the gauge:** the host has been reported at **99 % / 3.3 GB**, then **100 % / 2.2 GB**, and is now **1.9 GiB free @ 100 %** (`/dev/sda1` — 230 G total, 229 G used). **`df` fluctuates with concurrent lanes** — during the measurement window `/tmp` moved **79 GB → 71.9 GB** via another lane's cleanup. **Re-measure immediately before the stage; do not trust a stale figure.**

**The stage's real footprint, MEASURED** (from the live v0.30.12 release + a stage worktree):

| Component | Size |
|---|---|
| frozen binary `ensemble-prod` | 79 MB |
| PyInstaller build workdir (`build/`) | 122 MB |
| `dist/ensemble-prod` | 79 MB |
| payload copy | ~175 MB |
| **gross** | **~376 MB** |
| keep-3 eviction of `v0.30.9` (offset) | −175 MB |
| **net** | **~200 MB** |

⇒ **Against 1.9 GiB free, the stage FITS with ~9× headroom.** (Corroborated on this host: the live `releases/v0.30.12` measures **175 MB**, its `ensemble-prod` **79 MB**.)

**Sanctioned reclamation already done — 281.6 MiB:** pip cache · uv cache · `.angular/cache` · 294 `__pycache__` dirs · `.pytest_cache` ⇒ free disk **1.9 GiB @ 100 %**.

**DECISIONS — two levers deliberately NOT taken:**

| Lever | Size on this host | Decision |
|---|---|---|
| **Shared-cache prune** | go-build **7.2–7.6 GB** · npm **2.1 GB** | ❌ **NO PRUNE** — the caches stay **WARM for live lanes** |
| **Reconcile sweep of the `wt-stage-*` worktrees** | **38 worktrees / 46 GB** | ❌ **NO SWEEP** — **foreign-lane disruption is the larger risk**; these are other lanes' worktrees |

⇒ **Host pressure is NOT a release blocker.** Both levers remain available to the operator as **discretionary** actions, **not** as a precondition. A stage that ran out of disk mid-PyInstaller would produce a partial payload (at best a provenance/freshness refusal, at worst a half-written `releases/<ver>/`) — but the measured footprint says that will not happen.

⇒ **R10 is ACCEPTED-WITH-MEASUREMENT** (stage fits; shared caches and foreign worktrees deliberately preserved).

### 5.3 Stage-verification legs S1–S5

| Leg | What it proves | How | Pass criterion | Fresh? |
|---|---|---|---|---|
| **S1 — release integrity** | The staged artifact is the thing that was built and tagged | `git describe --tags --exact-match HEAD` == `v0.31.0`; manifest ⇄ staged-dir re-hash (`integrity_verify`); sidecar present, `git_head == release commit`, `git_dirty == false`, `artifact_sha256` == artifact hash; `pipeline_scripts_floor_commit` == `pipeline_scripts_rev` | All four legs match; no `non-tip-tree` / `provenance-*` / `stale-provenance` / `dirty-build` token | **fresh** |
| **S1d — prompt-artifact seal** | No prompt changed outside the sealed lane | §4.3's three checks | All empty/equal | **fresh** |
| **S2 — migrations on BOTH drivers** | The schema lands on SQLite *and* PostgreSQL | SQLite: `tests/unit/repositories/test_room_migration_runner_execution.py` + `test_room_models_and_migration.py`. PG: room-family suite against a **real PG scratch DB** (`ensemble_dev_scratch`; `ensemble_dev_test_only` does not exist here) — create if needed; **scrub `POSTGRES_*` per-variable, never `env -u POSTGRES_*`** | All 3 migrations apply clean on SQLite (ledger rows, `skip_reason=None`); on PG `create_all` creates the 3 tables and `_ensure_postgres_columns` adds the 4 columns + unique index, idempotently | **fresh — and the PG half does not exist as a test today** |
| **S3 — FE build** | The FE compiles and the staged dist carries the room surface | **§3.5.5** — the measured recipe: PIN `PATH=/home/nea/.nvm/versions/node/v24.21.0/bin:$PATH` (node v24.21.0 / npm 11.19.0) → `npm ci` (exit 0; npm 11 skips 5 install-scripts behind `allowScripts` — expected, non-fatal; esbuild ships via `@esbuild/linux-x64`) → `./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` (exit 0, TS 5.9.3, local devDep) → `cd ..` → `bash scripts/upgrade/_build_frontend.sh` (exit 0, **by path** — the ambient scripts pin is stale) → assert `index.html` + the SOURCE-tree sidecar + `git status --porcelain` EMPTY + the **four bundle markers** (§3.5.3) | tsc exit 0 · build exit 0 · sidecar present with `git_head == HEAD` and `git_dirty == false` · `git status --porcelain` EMPTY · 96 files / 7,949,189 B in the dist · the room surface present **by string-literal markers** | ✅ **VERIFIED (dry-run)** — measured on the release worktree @ `913088277`; sidecar `git_head == HEAD`, `git_dirty == false`, `artifact_sha256` independently re-verified. ⚠️ **BUT the present dist is STALE** (sidecar stamps `913088277`, HEAD is `af661ac8e`) ⇒ **must be REBUILT at the bumped HEAD** (§2.5.1) |
| **S4 — gate evidence** | The feature's own gates still pass at the release commit | Re-run the registered packs at the release tag: `gc_p6_room_family` (**24 files / 421 nodes — REGISTRY CORRECTED, see §7.7**), `gc_p6_room_mutation_matrix` (9/9), `gc_p6_pilot_scenario` (9/9), SSE-adjacent corpus (111/111), TestP11 (39), nudges (42), language_check (124), ledger gate (5/5, `EXPECTED_LATEST=36`), FE specs (140/140 + modal 8/8) | Counts match the re-gate #2 record; **any count delta is explained**, not hand-waved | ✅ **RE-MEASURED (LEG 3a, PASS)** — 24 files / 421 nodes, **421 passed / 0 red**, 70.93 s under the 300 s cap (xdist `-n 8`; the historical serial run took ~6:02), junitxml agrees (421 tests / 0 failures / 0 errors / 0 skipped), fence proven `ensemble_dev` (never `ensemble_prod`). **Re-confirm at the tag.** |
| **S5 — boot smoke** | The frozen binary boots green with the new schema — **in a SCRATCH sandbox, never against prod** | **§5.4.3** — the rewritten leg: scratch payload **copy** + scratch DB + scratch `HOME`/`DATA_DIR` + scratch port + the **34-name per-variable scrub** + the **three terminators pinned to `0`** + `ENSEMBLE_SELF_ENV=sandbox` | `livez` version `0.31.0`, `readyz` green (db/queue/services/checkpoint), the three room tables created **in the scratch DB only**, pool clean, **zero** sweep/reaper kills, and **zero trace left behind** | **fresh** — and it must be re-shaped per **BLK-1** |

**Leg status at the release tip (2026-11-17):**

| Leg | Status |
|---|---|
| **S1 — release integrity** | pending (stage-time) |
| **S1d — prompt-artifact seal** | 🟡 **CLOSED pre-cut** (3/3 checks at the `v0.30.12` anchor; seal blob `cb51a277…` byte-identical) — **must be RE-RUN AT THE TAG** (§4.3.2) |
| **S2 — migrations, both drivers** | ✅ SQLite pinned by the room-migration tests · ✅ PG parity **CLOSED** by measurement (§3.2.1) |
| **S3 — FE build** | ✅ **VERIFIED (dry-run)** — §3.5.5 + the inventory in §3.5.2 — **but the present dist is stale** (`git_head 913088277` vs HEAD `af661ac8e`) ⇒ **rebuild at the bumped HEAD** (§2.5.1) |
| **S4 — gate evidence** | 🟡 **unverified at the tip** — the room-family re-measure stands at 24 files / 421 nodes (§7.6); the remaining packs are **not** re-measured |
| **S5 — boot smoke** | 🟡 **unverified** — the leg is now correctly specified (§5.4) but **has not been executed** |

⇒ **S1d (at the tag), S4 and S5 remain.**

**Evidence lands in:** `/home/nea/agents-ensemble/data/upgrade/live/<run_id>.log` (narrative) + `run-<ts>-<pid>.payload.log` (payload), plus `--junitxml` for S4. Use `.txt` names, not `.log` — `.gitignore` has `*.log` and would silently hide a transcript.

### 5.4 The S5 boot smoke — 🔴 REWRITTEN (BLK-1 + BLK-2)

> **CORRECTION PASS 2026-11-17.** The release-prep tester found **two CRITICAL ceremony-facing defects in this section** and two wrong recipe facts. Both are fixed below; the previous text is superseded. Evidence: `.agents/tester/RESULTS/2026-11-17-group-chat-v0310-release-verification.md` (commits `463044ce4` → `4b56f992e` → `92fcf26af`).

#### 5.4.0 🔴 BLK-1 — the smoke must NEVER boot the pre-promote binary against prod

**What the previous revision said (wrong):** boot `releases/v0.31.0/ensemble-prod` with `ENSEMBLE_SELF_ENV=live`, `POSTGRES_DB=ensemble_prod`, `PORT=9797`.

**Why that is a critical, ceremony-facing defect — three independent failures:**

1. **Schema drift under the old binary.** `SQLModel.metadata.create_all` (`daemon/manager.py:1068`) runs at boot. Booting the **new** binary against `ensemble_prod` creates the three room tables **outside the promote ceremony**. If the ceremony then aborts, **prod carries the new schema while the old binary still serves** — a schema/binary skew the rollback story does **not** cover (rollback does not drop the tables; §5.5).
2. **Daemon collision.** `PORT=9797` is the **live** daemon's port (live `.env`: `PORT=9797`, `POSTGRES_DB=ensemble_prod`). The smoke collides with the running production daemon.
3. **The prod-DSN carve-out is OPEN.** `assert_prod_dsn_sanctioned` (`daemon/ensemble_config.py:241`) **sanctions** `ensemble_prod` when the identity is `live` (`:269`) — so `ENSEMBLE_SELF_ENV=live` turns the fence **off** exactly when the smoke needs it **on**.

**The rule, stated once:**

> **The S5 boot smoke NEVER boots against prod, NEVER on the live port, NEVER with the not-yet-promoted binary pointed at the live DSN.**
> It runs a **scratch copy of the payload** against a **scratch database**, in a **scratch HOME/DATA_DIR**, on a **scratch port**, with a **non-live `ENSEMBLE_SELF_ENV`** — so the prod-DSN fence is **armed**, not sanctioned.

**Why a scratch *copy* of the payload (never in place):** a payload sitting under a canonical install dir **auto-derives `ENSEMBLE_SELF_ENV=live`** (install-dir + DB cross-check) and re-opens the prod carve-out. Boot from a copied tree **outside** the install dir.

#### 5.4.1 🔴 BLK-2 — the scrub must include `POSTGRES_URL` (the five-var scrub is INSUFFICIENT)

**The decisive fact, in code.** `daemon/persistence.py:233-235` — `_build_pg_connection_string` reads `POSTGRES_URL` **first** and returns it **verbatim**:

```python
    # 1. Full DSN shortcut
    direct = os.environ.get("POSTGRES_URL")
    if direct:
        return direct
```

It **short-circuits** the `POSTGRES_HOST/PORT/DB/USER/PASSWORD` builder entirely.

**And it is invisible to the prod fence.** `assert_prod_dsn_sanctioned` resolves the DB name via `resolved_postgres_db_name` (`daemon/ensemble_config.py`), whose entire body is:

```python
    return os.environ.get("POSTGRES_DB", config.postgres.db).strip().lower()
```

Its docstring is explicit: *"the rendered URL is **NEVER** parsed"* (passwords containing `?`/`@` break URL parsing). ⇒ the fence's view of the database comes from `POSTGRES_DB`, while the **actual connection** uses the ambient `POSTGRES_URL`. **One ambient variable redirects a "fenced" smoke at prod, and the prod fence will NOT catch it.**

**Measured (tester dry-run probe, no connection opened):** with the five canonical vars scrubbed and `POSTGRES_URL` ambient, the resolver returned the poisoned prod DSN **verbatim**; with both present, the ambient `POSTGRES_URL` **still won**.

**The full scrub — per-variable ONLY.**

> 🔴 **`env -u POSTGRES_*` is a silent NO-OP.** Bash does **not** glob env names. The tester's counter-example proves it: after `env -u POSTGRES_*`, `POSTGRES_DB` / `USER` / `PASSWORD` / `HOST` / `PORT` **all remained set**, and `PGHOST` / `PGDATABASE` survived too. **Per-variable scrubbing is the only correct form.**

**The names** — the union of the two house scrub sources (`scripts/run_tests_scrubbed.sh:47-84` and `daemon/services/spawn_env_policy.py:51-77`) plus the boot-redirect and libpq-completion names, grouped by the reason each is in the list:

```bash
# DSN carriers — POSTGRES_URL IS THE DECISIVE ONE (BLK-2)
unset POSTGRES_URL POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
# URL aliases — hygiene: NOT boot-time redirect vectors (the daemon always passes a complete URL)
unset DATABASE_URL DATABASE_URL_POSTGRES
# libpq connection-selection + auth — hygiene: PGSSLMODE / PGPASSFILE / PGSERVICE can still
#   fill gaps the URL omits (the built URL carries no sslmode; PGPASSFILE applies when the
#   URL's password is empty). Enumerated deliberately, NOT a `^PG` wildcard.
unset PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE \
      PGSSLMODE PGSERVICE PGSERVICEFILE PGSSLCERT PGSSLKEY PGSSLROOTCERT \
      PGCONNECT_TIMEOUT PGAPPNAME PGOPTIONS PGDATA
# Bind/port family — a live-shaped port re-points the smoke at the running daemon
unset PORT DAEMON_PORT HOST LIMITS_METRICS_LISTENER_PORT
# Daemon identity / config / data-dir redirect channels
unset ENSEMBLE_UPGRADE_LIVE ENSEMBLE_CONFIG ENSEMBLE_DATA_DIR DATA_DIR
# Plane workspace hygiene — an unset-or-'nea' selection resolves to the PROD workspace
unset PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL
```

*(The tester's canonical scrub was **34** names; the union written above is 35 — the count is **not** the contract, the **membership** is. `POSTGRES_URL` must be present, and every name must be scrubbed **per-variable**.)*

**Two further poison vectors the smoke must close:**

- **`data/ensemble.json` is file-authoritative.** `daemon/ensemble_config.py:56-90` (`load_or_create`): if `<data_dir>/ensemble.json` exists, its `database` flag + `postgres.*` fields are used (`:74`); the `POSTGRES_*` auto-detect only runs when the file is **absent** (`:82`). The `database` flag therefore **outranks** a scrubbed env (`POSTGRES_*` overrides `postgres.*` at runtime, `:124-128` / `factory.py:358-362`). `stage.sh` does **not** copy `data/` (`scripts/upgrade/stage.sh:464-504` copies `ensemble-prod`, `agents`, `frontend`, `plugins`, `guidelines`, `config.yaml`, `launcher.sh`, `scripts/`) ⇒ a **freshly staged payload carries no `ensemble.json`** — but a scratch smoke must still point `ENSEMBLE_DATA_DIR` at a **fresh empty dir**, because a stale `./data/ensemble.json` left in the payload dir or CWD by a prior boot is the second poison path.
- **The prod fence must be ARMED.** Set `ENSEMBLE_SELF_ENV` to a **non-live** value (`sandbox`) so `assert_prod_dsn_sanctioned` **refuses** `ensemble_prod` — the live identity *sanctions* it.

#### 5.4.2 The two recipe facts the previous revision got wrong

**(a) `PORT` must be EXPORTED — `DAEMON_PORT` alone is IGNORED.** Measured against the real loader (`daemon/config.py`, `substitute_env_vars` → `os.environ.get()` at `:75-76`):

| `PORT` | `DAEMON_PORT` | resulting `daemon.port` |
|---|---|---|
| unset | unset | `8088` (the `config.yaml` default) |
| **set** | unset | **`PORT` wins** |
| unset | **set** | **`DAEMON_PORT` IGNORED → `8088`** |
| set | set | `PORT` wins |
| `''` (empty) | any | **empty string** — *not* the 8088 default |

The launcher keeps its own copy of the resolution (`launcher.sh:1435-1460`). ⇒ **export `PORT` explicitly; `DAEMON_PORT` is not a substitute.**

**(b) THREE boot-time terminators default ON — pin all three.** The previous revision pinned only the first and understated its rule.

| Knob | Site | Default when absent | Disable values |
|---|---|---|---|
| `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` | `daemon/services/boot_subprocess_sweep.py:120` | `DEFAULT_ENABLED = True` (`:127`); applied in the **prod lane** (`is_prod_lane()` == `sys.frozen`, `:140-180`) | `{0, false, no, off}` (`:132`; stripped — `" 0 "` is OFF) |
| `ENSEMBLE_BASH_ORPHAN_REAPER` | `daemon/services/bash_spawn_reaper.py:109` | **ON in ALL lanes** (not lane-conditional) | same set |
| `ENSEMBLE_RAM_GUARD` | `daemon/config.py:1173-1204` | **ON** (absent ⇒ ON; empty/whitespace ⇒ ON; explicit anything else ⇒ ON) | `{0, false, no, off}` (case-insensitive, stripped) |

**The sweep's truth table (measured, prod lane):**

| environ value | sweep | |
|---|---|---|
| **ABSENT** | **ON** | destructive |
| `""` / `"   "` | **ON** | destructive |
| `"1"` / `"true"` / `"yes"` / `"on"` | **ON** | destructive |
| `"0"` / `"false"` / `"no"` / `"off"` | OFF | safe |
| `" 0 "` (padded) | OFF | safe (stripped) |

⇒ **The value IS load-bearing** (the previous revision said only that "`env -u` was the hazard" — true, but incomplete): anything outside the disable set ⇒ **ON**.

**The pin is load-bearing, and the ambient guard is NOT there:** the **live** install's `.env` carries `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, but a **worktree** `.env` does **not** (0 occurrences) — a worktree smoke that relies on ambient inheritance gets **no guard at all**.

#### 5.4.3 The corrected S5 leg (the verified shape)

```bash
# ── S5 BOOT SMOKE — scratch everything. Never prod, never the live port,
#    never the pre-promote binary against the live DSN. (BLK-1)
SCRATCH="$(mktemp -d /tmp/gc_v0310_smoke.XXXXXX)"                   # scratch root, outside any install dir
cp -a /home/nea/agents-ensemble/releases/v0.31.0 "$SCRATCH/payload"  # COPY: never boot in place
mkdir -p "$SCRATCH/home" "$SCRATCH/data"                            # fresh empty HOME + DATA_DIR (no ensemble.json)
cp "$SCRATCH/payload/config.yaml" "$SCRATCH/config.yaml"            # scratch config copy

# ── 34-name per-variable scrub. `env -u POSTGRES_*` is a SILENT NO-OP. (BLK-2)
env -u POSTGRES_URL -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB \
    -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u DATABASE_URL -u DATABASE_URL_POSTGRES \
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGPASSFILE \
    -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE -u PGSSLCERT -u PGSSLKEY \
    -u PGSSLROOTCERT -u PGCONNECT_TIMEOUT -u PGAPPNAME -u PGOPTIONS -u PGDATA \
    -u PORT -u DAEMON_PORT -u HOST -u LIMITS_METRICS_LISTENER_PORT \
    -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR -u DATA_DIR \
    -u PLANE_MCP_WORKSPACE_SLUG -u PLANE_BASE_URL \
    \
    # ── the three boot-time terminators, pinned OFF (absent ⇒ ON) ─────────
    HOME="$SCRATCH/home" \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_BASH_ORPHAN_REAPER=0 \
    ENSEMBLE_RAM_GUARD=0 \
    \
    # ── scratch identity: the prod fence must be ARMED, not sanctioned ───
    ENSEMBLE_SELF_ENV=sandbox \
    ENSEMBLE_CONFIG="$SCRATCH/config.yaml" \
    ENSEMBLE_DATA_DIR="$SCRATCH/data" \
    PORT=19101 HOST=127.0.0.1 \
    \
    # ── scratch DB (never ensemble_prod) ─────────────────────────────────
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev_scratch \
    POSTGRES_USER=<scratch-role> POSTGRES_PASSWORD=<redacted> \
    timeout -k 5 30 "$SCRATCH/payload/ensemble-prod"
```

**Pre-boot assertions (fail closed before the binary is allowed to start):**
- `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` is **set** and equals `0` — **never unset**; likewise the reaper and the RAM guard.
- `PORT` is set, is in the scratch range (**not 9797**), and `PORT != DAEMON_PORT`.
- `POSTGRES_DB != ensemble_prod`; `POSTGRES_HOST != 10.44.0.2` (the remote prod host).
- The payload copy is **not** under `/home/nea/agents-ensemble` (⇒ no live auto-derive).
- `ENSEMBLE_SELF_ENV` is **not** `live`.

**Isolation:** run inside a **systemd transient unit** whose `/proc/<MainPID>/cgroup` shows `/user.slice/` and **NOT** `ensemble-main.service` — no shared-cgroup fallback. A daemon-child shell inherits the live daemon's cgroup; the sweep would then classify live processes as orphans.

**Pass criterion:** the binary boots in the scratch sandbox; `livez` reports version `0.31.0`; `readyz` is green; the three room tables are created **in the scratch DB only**; the log shows **zero** sweep/reaper kills.

**Abort path — the smoke must leave NO trace:**
1. `timeout -k 5 30` bounds the run; if the binary does not exit, it is killed.
2. Drop the probe's private schema / scratch DB and re-verify: `schemas now = ['public']` (baseline MATCH) and probe residue (`gc_pg_probe%`) = **0** — the tester's cleanup discipline.
3. `rm -rf "$SCRATCH"` — the scratch tree lives under `/tmp`, never under an install dir.
4. **Nothing was written to prod** — because prod was never a target: no `POSTGRES_URL`, `POSTGRES_DB != ensemble_prod`, `ENSEMBLE_SELF_ENV=sandbox`, a fresh empty `ENSEMBLE_DATA_DIR`, and a scratch port.
5. Assert post-hoc: the three room tables are **absent** from prod — they must not exist until the ceremony's own boot.

#### 5.4.4 What the previous revision got right (kept)

- The **pin** (not strip) is the right form for the sweep: `env -u ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` strips the guard ⇒ prod-lane default ON ⇒ the sweep SIGTERM+SIGKILLs the live launcher + `ensemble-prod` pair (the 2026-10-31 incident; systemd auto-restart, `NRestarts=1`).
- `env -u POSTGRES_*` is a silent NO-OP (now **proven** by the tester's counter-example, not merely cited).
- `PORT` must be set in the install `.env` for the **ceremony itself** (runbook §9.2 pre-step 4) — unchanged; the S5 smoke uses a **scratch** port.

### 5.5 Rollback story

**`rollback_safe` must be set EXPLICITLY at stage time:**

```bash
ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.31.0 \
  bash scripts/upgrade/stage.sh live
```

**Why explicit:** the derive path greps the **full migration history** (not the tag-to-tag delta) for `DROP TABLE|DROP COLUMN`. All three room migrations match — in their **DOWN** sections — so the derived value would be `false` and the unset path **exits 78**. `contains_contract_phase=true` is **EXPECTED** and harmless: it reflects historical DDL, not this release's delta. The `=1` is an explicit adjudication by the release author and must be **disclosed** with the tag-to-tag delta at the ceremony (house rule).

**The delta is purely additive** (v0.30.12 → v0.31.0): 3 new tables, 4 new columns, 1 new unique index, 1 new tool category, 8 agent grants, 14 FE files, 3 planning docs. **No destructive DDL in the UP path of any migration.**

**What a rollback DROPS:** the room data (chatrooms, memberships, room messages) in the live PG; the `room` tool category and its 8 grants (the reverted binary does not register the category — the artifact reverts with it); the FE room-modal assets (the dist reverts).
**What it leaves behind:** **orphan room tables in `ensemble_prod`** — harmless (the reverted binary never queries them; the schema simply sits unused). The room tables are additive and carry no FK to pre-existing tables, so nothing else is affected.

**Auto-rollback behaviour:** `promote.sh` fires auto-rollback to `OLD_CUR` (v0.30.12) on gate failure (livez/readyz/version/soak) **only if `PREV` has `rollback_safe=true`**; otherwise it **halts for human with the txn left open**. Post-commit rollback is **operator-driven** (`scripts/upgrade/rollback.sh`).

**The disposition doctrine (runbook §9.3, owner-ruling):** **serving-green ⇒ NEVER rolled back.** Rollback is reserved exclusively for genuinely unhealthy serving. If the daemon is serving green after a hand-back halt, **stay** — do not roll back (this is the v0.28.5 incident's ruling, and the v0.29.0/v0.29.1/v0.29.3 halts were all closed with `serving_green_commit`, not rollback).

### 5.6 The upgrade_journal entry shape (expected, from the v0.30.12 precedent)

```
arm-scripts-provenance: verdict=arm floor-ancestor-ok: scripts_dir=<path> lib.sh_sha256=<sha> stamp=1aa0065e… git_rev=<rev> dirty=no floor=1aa0065e…
nonce_consumed:        nonce for run_id=r-<ts>-<id> consumed by message <uuid> (kind=upgrade env=live target=v0.31.0)
promote-start:         pipeline-scripts rev=1aa0065e… pid=<pid> scripts=<path>
run_log:               run_id=run-<ts>-<pid> narrative=/home/nea/agents-ensemble/data/upgrade/live/r-<run_id>.log payload=/home/nea/agents-ensemble/data/upgrade/live/run-<ts>-<pid>.payload.log
python_floor_probe:    interpreter=/usr/bin/python3 version=3.12
plugin_staleness_observed: plugin=/home/nea/.../plugins/opendesign reasons=PLUGIN-STALENESS=stale code=pin-stale …   # EXPECTED (opendesign pin is 50+ days old) — journaled, non-blocking
intent_flip:           preflight: intentional promote target v0.31.0 — integrity verified (CURRENT drift-check + TARGET manifest match); operator-initiated
supervision_boot:      state=UNIT_MANAGED mode=unit unit=ensemble-main.service outcome=conforming
commit:                promote v0.31.0 committed (gate+soak green; previous=v0.30.12)
sweep:                 pending_op run_id=… closed by reconcile: terminal event 'commit' at <ts> (promote v0.31.0 committed …)
```

**Not journaled:** the receipt line (stdout-only). The narrative/payload logs are the durable evidence.

**Operator check after the ceremony:** the `commit` row must be present with `previous=v0.30.12`, `state.json.current == v0.31.0`, and `in_flight == null`.

---

## 6. THE USER'S PROMOTE CEREMONY (his, not this lane's)

### 6.1 The two lanes — and which one the user is being handed

| | **Tool lane (armed)** | **Operator lane (direct)** |
|---|---|---|
| Who runs it | ari calls `system_upgrade` on a turn with valid user-origin provenance | a human/agent runs `promote.sh` directly |
| Nonce | **Required** — minted, echoed, consumed at arm (`consume_pending_action`) | **Not used** — `promote.sh` is nonce-free |
| Scripts dir | resolved by `_resolve_scripts_dir` → **the `.env` pin** | **the payload** (CWD) — runbook §10.1 default |
| Provenance guard | **applies** → the pin must be advanced (§5.1) | n/a (non-git payload ⇒ warn-only) |
| Journal shape | includes `arm-scripts-provenance` + `nonce_consumed` | no `nonce_consumed` row |

The last two live promotes (v0.30.12, v0.30.9) used the **tool lane** with the pin. **Recommendation: run the tool lane** (it carries the attestation the brief asks for) **against an advanced pin**, and have the executor run the payload's scripts.

### 6.2 What the leader hands the user — the ceremony brief

**Step 0 — the operator preconditions (before the ceremony; all four must be TRUE and stated):**

| # | Precondition | Live value at planning time | Status |
|---|---|---|---|
| P1 | `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advanced to a tree whose HEAD contains `1aa0065e…` as an ancestor; daemon restarted to load it | `/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade` @ `bf4bfde6e` — **STALE** | 🔴 **OUTSTANDING — §5.1** |
| P2 | `SWEEP_SERVING_GREEN_ENABLED=1` (the launcher kill-switch) in force on the ceremony's launcher — **and the value stated in the ceremony notes** | not set in `.env` ⇒ launcher default (enabled) | 🟡 **confirm and state** |
| P3 | If `HANDBACK_VERIFY_BUDGET_S` is raised, it must live in the **install `.env`**, NOT the ceremony shell (`launcher.sh` never sources `lib.sh`; the daemon child sees the knob only through the `.env` seam) | not set ⇒ default | 🟢 unless raised |
| P4 | `PORT` set in the install `.env` **and** `config.yaml` carries a `daemon.port` key | `PORT=9797` ✓ · `daemon.port: ${PORT:-8088}` ✓ | ✅ **satisfied** |
| P5 | Install `config.yaml` is **key-shape compatible** with the v0.31.0 model contract (`promote.sh` entry check `_config_compat_check` ⇒ exit 78 `config-incompatible`) | `config.yaml` is **unchanged** between v0.30.12 and `origin/latest` ✓ ⇒ contract unchanged | ✅ **expected pass** — verify at stage time |
| P6 | Stage complete: `releases/v0.31.0/` present, `manifest.json` signed, `contains_contract_phase=true`, `rollback_safe=true` | — | pending stage |

**Step 1 — the user's inputs:**
1. The **target**: `v0.31.0`.
2. The **user confirmation** — a message from the user on a channel the daemon classifies as user-origin (`api`, or a registry `source_type` in `{telegram, slack, discord, whatsapp}`). The tool lane stamps a **per-turn origin window**; internal/agent relays **clear** it. A leader-relayed execution **refuses `user-confirmation-missing` BEFORE the nonce burns** — so the user's own turn must carry it.
3. The **nonce echo** — the nonce minted for `(target=v0.31.0, kind=upgrade, env=live, instance)` in `state.json.pending_actions`, echoed back to the arming instance. `nonce_normalize` strips dashes and case. **TTL: `NONCE_TTL_S = 3600`** (mint → confirm, absolute).
4. The **3-factor attestation**: (i) `user_confirmed = true`; (ii) a valid per-turn user-origin window (`manager.stamp_user_origin_window` + `upgrade_journal.classify_user_origin` — exact `"api"` or the four registry source types, fail-closed elsewhere); (iii) the nonce present in `pending_actions`, bound to (target, kind, env, instance), consumed at arm via `consume_pending_action`.

**Step 2 — the command handed to the user / operator:**

```bash
# PRIMARY (runbook §10.1 default): promote from INSIDE the staged payload.
cd /home/nea/agents-ensemble/releases/v0.31.0
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.31.0 \
  bash scripts/upgrade/promote.sh live --f2-verified-closed
```

Both live guards are mandatory and independent: `require_live_guard` (`ENSEMBLE_UPGRADE_LIVE=1`) and `--f2-verified-closed` (else refusal token `f2-not-verified`).

**Step 3 — the PRE-promote checklist the leader runs:**

| # | Check | Command / evidence |
|---|---|---|
| C1 | The release commit is tagged `v0.31.0` and HEAD is exactly it | `git describe --tags --exact-match HEAD` |
| C2 | `origin/latest` has not moved since the tag | `git rev-parse origin/latest` vs the tag; `git merge-base --is-ancestor <tag> origin/latest` |
| C3 | The staged payload's manifest matches the staged tree | `integrity_verify` output / manifest sha comparison |
| C4 | The floor/stamp pair agree and the floor is an ancestor of the built tree | `PIPELINE_SCRIPTS_FLOOR_COMMIT` == `PIPELINE_SCRIPTS_REV`; `merge-base --is-ancestor` |
| C5 | **P1 satisfied** — the pin advanced and the daemon restarted | the pin's `merge-base --is-ancestor 1aa0065e… HEAD` ⇒ rc 0; `state.json.pending_actions` empty of stale nonces |
| C6 | `rollback_safe=true` in the manifest, `contains_contract_phase=true` disclosed | `manifest.json` |
| C7 | `state.json` shows `current=v0.30.12`, `in_flight=null`, no open txn | `state.json` |
| C8 | The **version slot** is free and `v0.30.13` is not being re-cut | `git tag -l 'v0.31*'` empty before the cut |
| C9 | `PORT` + `daemon.port` present; `SWEEP_SERVING_GREEN_ENABLED` value stated | `.env`, `config.yaml` |
| C10 | No other lane is mid-flight on the tree | `list_instances`; the local-latest ref reconciled (§7.3) |

**Step 4 — POST-promote verification:**

| # | Check | Criterion |
|---|---|---|
| V1 | `livez` | HTTP 200, **version == 0.31.0** |
| V2 | `readyz` | all-components-true (db / queue / services / checkpoint) |
| V3 | **soak** | the promote's own soak window is green (the promote gate includes it; `soak_grace_extended` may extend the window by 180 s — bounded) |
| V4 | Journal | `commit` row present: `promote v0.31.0 committed (gate+soak green; previous=v0.30.12)`; `in_flight == null` |
| V5 | `state.json` | `current == v0.31.0`, `previous == v0.30.12` |
| V6 | Service | `systemctl --user status ensemble-main.service` — `MainPID` stable, `NRestarts` unchanged |
| V7 | Schema | the three room tables exist in `ensemble_prod`; `room_messages` carries `source_message_id`, `trigger`, `member`, `resolution_warnings`; index `uq_room_messages_proposal_dedup` exists |
| V8 | **FE-visible** | the chatrooms icon renders in the per-instance chat-page header next to WATCHOVER; clicking opens the **large modal**; selecting an instance lists its tree's rooms; a room's conversation reads through the same fail-closed predicate. **Hidden when no instance is selected** (`@if (renderedInstance())`, `chat.html:89` — A-P6-36). The FE is served from the release's `frontend/dist` — a hard reload may be needed to bust the browser cache |
| V9 | No sweep kills | the boot log shows **no** stale-subprocess sweep terminations |
| V10 | Pilot gate | the **video-team pilot scenario** runs **post-promote** (§7.2) |

### 6.3 The abort / undo path (if the ceremony aborts)

| Stage | Symptom | Action |
|---|---|---|
| Before the flip | any refusal (`stale-scripts`, `f2-not-verified`, `executor-scripts-unavailable`, `config-incompatible`, `target-not-staged`) | **No live change.** The nonce is NOT burned on a pre-nonce refusal. Fix the cause (§5.1 for `stale-scripts`) and re-run |
| During the flip | `intent_flip` written, then a halt (`unit hand-back failed`) → `halt` row, txn left open | **Check `state.json.in_flight`.** If the target is **serving green** ⇒ **STAY** (runbook §9.3: serving-green ⇒ never rolled back); close the txn with `txn_repair_manual` (the v0.29.3 precedent: `operator: halted promote r-<run_id> (target vX) closed manually. Flip applied + integrity-verified pre-flip (current=vX). … in_flight cleared for a clean boot.`) and record `serving_green_commit` |
| After the flip, launcher loop | the launcher's journal sweep sees a stale flipped txn and loops at 100 % CPU (the v0.29.3 reproducer) | Clear `in_flight` to `null` in `state.json` (operator, manual, journaled as `txn_repair_manual`); back up `state.json` first (`state.json.pre-reconcile-<ts>.bak` is the house pattern) |
| Committed but unhealthy | `commit` row present, daemon not serving | Operator-driven rollback: `scripts/upgrade/rollback.sh` (or re-promote v0.30.12). Auto-rollback only fires *inside* the ceremony and only when `PREV.rollback_safe == true` |
| After any abort | confirm | `livez` version match, `readyz` green, `journalctl -u ensemble-main.service` clean, no sweep kills, `state.json.in_flight == null` |

---

## 7. KNOWN PRE-PROMOTE ITEMS — disclose verbatim in the ceremony brief

### 7.0 Risk register — status at the release tip (updated 2026-11-17)

| # | Risk | Status |
|---|---|---|
| **R1** | The **stale scripts pin** refuses the arm (`stale-scripts`, pre-nonce) | 🟡 **OPEN — remediable.** The `.env` pin advance (§5.1) resolves it. The refusal burns **no** nonce, so nothing is damaged and the ceremony can be retried. |
| **R2** | **PG schema proof absent as a test** (the release could be green on SQLite and missing on PG) | ✅ **CLOSED.** PG parity **HOLDS** on a real scratch PG (16.15, `ensemble_dev_scratch`, private schema `gc_pg_probe`): both UNIQUE constraints live in `pg_constraint`; `_ensure_postgres_columns` 10→14 cols and **idempotent**; seq monotonic; cursor predicate correct; **cross-room leakage 0**; non-member read denied; purge empties all three tables + **exactly one** content-free `room_purged`; control room untouched. Evidence §3.2.1. |
| **R3** | **Icon placement** ambiguity (§13.1's instances-row reading vs the built chat-page header) | 🔴 **OPEN — the user adjudicates before the cut** (§7.1). A list-row choice is a **follow-up FE change BEFORE promote**. |
| **R4** | The **boot smoke touching prod** (schema drift under the old binary + daemon collision + the carve-out open) | ✅ **CLOSED** — §5.4 is rewritten: scratch payload **copy** + scratch DB + scratch `HOME`/`DATA_DIR` + scratch port + `ENSEMBLE_SELF_ENV=sandbox`, with a no-trace abort path (§5.4.3). |
| **R5** | The **five-variable scrub leaving `POSTGRES_URL` live** (bypasses the prod fence entirely) | ✅ **CLOSED** — the scrub is now the full per-variable list including `POSTGRES_URL`, with the `persistence.py:233-235` short-circuit and the `resolved_postgres_db_name` bypass cited (§5.4.1). |
| **R6** | The **prompt-artifact seal moving** under the release | 🟡 **WATCH — sealed and frozen.** Seal blob `cb51a277…` byte-identical at `v0.30.12` and at the tip; zero `agents/*.md` changed; the binary embeds the **set constant** (byte-identical source) while artifacts+seal ship as a **payload directory** whose re-hash is **post-stage and automated** (`prompt_artifact_drift_leg`, reported-not-gating). **S1d must be re-run AT THE TAG** (§4.3.2, WATCH-1 / `feature/video-mock-prompts` @ `e559b4b80`). |
| **R7** | The **local-latest ref lag** → `non-tip-tree` refusal at stage | 🟡 **OPEN — owned by the ari-stop-int lane**, not this lane (§7.3). A **precondition** for the cut. |
| **R8** | `rollback_safe` **derive path exits 78** (the whole-file `DROP` grep matches the migrations' DOWN sections) | 🟡 **KNOWN — `ENSEMBLE_ROLLBACK_SAFE=1` must be explicit** at stage time; `contains_contract_phase=true` is expected (§5.5). |
| **R9** | The stage **evicts `releases/v0.30.11`** (keep-3) | 🟢 **COSMETIC** — disclosed so the missing directory is not read as damage (§2.6). |
| **R10** | **Host disk exhaustion at stage time** | ✅ **ACCEPTED-WITH-MEASUREMENT.** The stage's footprint was **MEASURED**: ~376 MB gross (binary 79 + build 122 + `dist/ensemble-prod` 79 + payload ~175), **~200 MB net** after the keep-3 eviction of `v0.30.9` (−175) ⇒ the stage **fits with ~9× headroom** against 1.9 GiB free. 281.6 MiB of sanctioned reclamation is already done. **No shared-cache prune** (go-build 7.2–7.6 GB, npm 2.1 GB stay **warm for live lanes**) and **no reconcile sweep of the 38 `wt-stage-*` worktrees (46 GB)** — **foreign-lane disruption is the larger risk**. Both remain **discretionary operator levers**, not preconditions. `df` fluctuates with concurrent lanes (3.3 GB → 2.0 GB → 1.9 GiB observed; `/tmp` moved 79 GB → 71.9 GB mid-window via another lane's cleanup) — **re-measure before the stage** (§5.2.1). |
| **R11** | **S3 verified by a DRY-RUN only**; **S1d (at the tag), S4 and S5 remain unverified** | 🟡 **OPEN** — the FE leg is proven; the remaining legs must be run before the ceremony (§5.3 leg status). |
| **R12** | The S3 **verification trap** — esbuild mangles `RoomDialogComponent` / `ROOM_DIALOG_SIZE` (0 hits) and the room chunk is **not** a `modulepreload` ⇒ a naive grep or `index.html` check **false-negatives** | ✅ **CLOSED** — the S3 assertion now anchors on four proven string-literal / CSS-class markers (§3.5.3). |

### 7.1 🔴 ICON PLACEMENT — **the user adjudicates before promote**

The chatrooms icon is implemented in the **per-instance chat-page header**, next to WATCHOVER, wrapped in `@if (renderedInstance())` (`frontend/src/app/pages/chat/chat.html:89`) so it is **hidden when no instance is selected** (A-P6-36). The binding direction (`gc_ui_refinement_p6`) reads *"CHATROOM ICON next to the WATCHOVER button on the frontend instances surface"*, and the pinned architecture §13.1 reads *"chatroom icon adjacent to WATCHOVER on the instances surface"*, while the same ruling also says *"from the instance row the user already looks at"*.

**The tension:** the implementation is on the **chat page** (the per-instance view). §13.1's literal *"instances surface / instance row"* reading would place it on the **instances list**. **Adjacency is preserved; SURFACE is not.**

⇒ **The user adjudicates at release/promote.** If he wants the list row, that is a **follow-up FE change BEFORE promote** (the modal is tree-scoped and instance-scoped alike, so the work is placement-only). **Resolve this before the stage is cut** — a post-promote FE change would require another release.

### 7.2 🟡 The video-team pilot runs POST-promote

The user's directive (2026-11-15) is explicit: the parked video-team lane resumes **only AFTER group chat is PROMOTED** (the user's ceremony) — not merely landed. The video team is the **designated FIRST pilot**: producer / capturer / editor / verifier coordinate through a chatroom as **named members** instead of pure parent-routing.

**Its scenario file is the gate:** `test/scenarios/group_chat_video_team_pilot_scenario.py` — nine checks (C1 four named members on one room · C2 parallel lane posts, monotonic log · C3 per-shot tasking **through the parent** · C4 milestone reports ride the digest · C5 a tag across lanes delivers (durable cursor + one content-free audit row) · C6 the unread→nudge path fires once per turn with the **real** unread count · C7 Ruling A — a completed member is never revived, the tag becomes a parent-routed proposal · C8 the §9 cost cap (4 posts OK, 5th typed-refused) · C9 the §13-P6 backend exit criteria incl. a purged room rendering as not-found). **Measured 9/9 PASS, mutation-proven.**

⇒ **Disclosure:** the pilot's E2E shakedown is **post-promote**; the scenario file is the gate. **There is no live room traffic before the pilot** — which is also why the §9 per-turn post cap has no exposure window pre-promote (A-P6-1).

### 7.3 🟡 The local-latest ref ownership — a precondition, not this lane's to fix

The local `latest` ref **lags** the merged tip, and the **ari-stop-int lane owns the local-latest fast-forward**. The group-chat merge landed at `56a5f4a00`; the local ref was ~57 commits behind at merge time.

⇒ **Precondition:** the local-latest ref **must be reconciled before the release cut**, because `stage.sh`'s tip-identity check (`_verify_tip_identity`) compares HEAD against `_tip_sha` (`refs/remotes/origin/latest` or `refs/heads/latest`) and refuses **`non-tip-tree`** on a mismatch. **This lane does not touch it** — the ari-stop-int lane owns the fast-forward, and this lane must wait for it rather than race it.

### 7.4 🟡 `EXPECTED_LATEST` is hard-coded

The group-chat ledger gate (`tests/unit/services/test_group_chat_ledger_gate.py`) anchors the **latest expected entry number** at **`EXPECTED_LATEST = 36`** (the live row count) — A-P6-35(g): the anchoring was deliberately strengthened so a deletion of the **highest** row REDs the node (proved with a scratch copy; the ledger file is never written).

⇒ **Disclosure:** any **future** `A-P6-NN` append (or a release-lane row) **must bump `EXPECTED_LATEST` in the same change** or the ledger gate REDs. **Recommendation for this release: append no ledger row.**

### 7.5 🟢 POK store at cap

The project operating-knowledge store is **at its 256-entry cap**; POK writes are **refused** (the cure is curator-only — archive). Facts the release lane would otherwise POK are recorded via RAG `experience()` instead. **Not a release blocker** — disclosure only, so nobody reads a refused POK write as a release defect.

### 7.6 🟡 The registry correction — the room-family composition row (append-only)

The registered `gc_p6_room_family` history carried **three disagreeing composition labels** — `21 files` @ `e6d984272` and @ `5d13a1c6c`; `24 files / 421` @ `419469b3f`; `24 files / 420` @ `7e0cd2db0` — and **no row registered the composition at the release tip**.

**Fix (append-only, commit `b47e9165f`):** a new `PACKS.md` section `<!-- gc-v0310-release-verification-20261117-begin -->` … `-end -->` was **appended** at the end of the file, registering the release-tip truth: **24 files / 421 nodes, 421 passed in 70.93 s** (609 warnings, exit 0; junitxml agrees — 421 tests / 0 failures / 0 errors / 0 skipped). **The historical rows were NOT edited** — their "21 files" labels were accurate at their own tips; only the composition/node **label** is superseded, and the new row says so explicitly. `PACKS.md` only, **+9 / −0** (append-only, zero deletions), tree clean after commit.

⇒ **Ceremony note:** the room-family truth at the release tip is **24 files / 421 nodes**. A re-run at the tag must reproduce it; a delta must be explained, not hand-waved.

### 7.7 🟡 WATCH — the parked video-mock-prompts branch carries a future seal re-pin

`feature/video-mock-prompts` @ `e559b4b80` changes **11 seal rows** and **8 `agents/*.md`** (video-team prompt edits). It is **not an ancestor of HEAD nor of v0.30.12**, so it is outside today's range and S1d is unaffected **today**.

**But if it merges into the release line before the v0.31.0 tag is cut**, §4.3 check #2 flips non-empty and check #3 becomes a **legitimate re-pin** ⇒ **S1d must be re-run AT THE TAG, not merely pre-cut** (§4.3.2). (Filed as anomaly `c2a34dcb`.)

### 7.8 🟢 Two further disclosures for completeness

- **The tool-category artifact is frozen** (§3.3). `room_close` was added after the category was minted and deliberately added **no category and no vocabulary bump**. The release ships the artifact **as-is**; it must **not** be regenerated.
- **`releases/v0.30.11` is evicted by the stage** (§2.6). Stated so the missing directory is not read as damage.

---

## 8. EXECUTION PLAN

### 8.1 Phases and ownership

| Phase | Owner | What | Exit criterion |
|---|---|---|---|
| **R0 — Preconditions** | **ari-stop-int lane** (owner) → then **giter** | Reconcile the local-latest ref to `56a5f4a00`. **Do not start the release cut until this lands.** | `git rev-parse latest == 56a5f4a00` |
| **R1 — Pin advance** | **giter / operator** | Advance `ENSEMBLE_UPGRADE_SCRIPTS_DIR` to a tree containing `1aa0065e…` as an ancestor; update the install `.env`; **restart the daemon**; verify the stamp matches (§5.1) | `merge-base --is-ancestor` rc 0; daemon restarted; `.env` verified |
| **R2 — Release cut** | **giter** | Create the release branch from `origin/latest`; add the plan doc + `docs/changelog-pending/group-chat.md`; edit `CHANGELOG.md` (`[Unreleased]` → `[0.31.0] — 2026-11-17`, paste the §3.6 block); run `python scripts/bump_version.py minor`; commit `chore(release): bump version to 0.31.0`; **annotated tag** `Release v0.31.0` | `git describe --tags --exact-match HEAD` == `v0.31.0`; the 4 version sites agree |
| **R3 — Build + stage** | **giter** | Build the binary + FE with provenance sidecars — **in the ordered sequence of §2.5.1: the FE is REBUILT at the bumped HEAD (sidecar re-stamped) BEFORE `stage.sh` runs**; `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.31.0 bash scripts/upgrade/stage.sh live` | `releases/v0.31.0/` complete; `manifest.json` signed; `rollback_safe=true`; `contains_contract_phase=true`; `pipeline_scripts_floor_commit == pipeline_scripts_rev` |
| **R4 — Verify S1–S5** | **giter + tester** | Run the five legs (§5.3), including S1d (prompt seal) and the **PG half of S2** | All legs green; every count delta explained |
| **R5 — Ceremony brief** | **architect → leader** | Hand the user the §6.2 brief: preconditions, inputs, command, pre-checklist, post-verify, abort path, and the §7 disclosures | The user has the brief and the §7.1 icon question is resolved |
| **R6 — Ceremony** | **the user** | The promote itself | `commit` row; `current == v0.31.0`; §6.2 V1–V10 green |
| **R7 — Post-promote** | **leader + pilot lane** | FE-visible verification (V8); resume the **video-team pilot**; the pilot scenario is the gate | Pilot scenario 9/9; no open txn; `in_flight == null` |

### 8.2 Rollback / undo path if the ceremony aborts

Per §6.3 — **the abort ladder**: no-flip refusals are free (nonce intact); a flip+halt with a **serving-green** target ⇒ **stay** + `txn_repair_manual`; a launcher sweep loop ⇒ clear `in_flight` to `null` (back up `state.json` first); a committed-but-unhealthy target ⇒ operator-driven `rollback.sh`. **Auto-rollback never fires outside the ceremony window**, and the release is additive — a rollback leaves harmless orphan room tables.

### 8.3 What this lane does NOT do

- It does **not** perform the promote (the user's).
- It does **not** touch the local-latest ref (the ari-stop-int lane's).
- It does **not** re-pin the `room` tool-category artifact or bump `VOCABULARY_VERSION` (frozen by design).
- It does **not** bump the prompt-artifact baseline (nothing changed).
- It does **not** append a group-chat ledger row (avoiding the `EXPECTED_LATEST` bump).

---

### 8.4 Follow-ups recorded — NOT in this cut (with the tester's reasoning)

| # | Follow-up | Why it is NOT in this cut |
|---|---|---|
| **F-1** | **Spawn-boundary `POSTGRES_URL` strip** — `daemon/services/spawn_env_policy.py` strips the `PG*` family and `DATABASE_URL` but **not** `POSTGRES_URL`, so spawned children inherit an ambient `POSTGRES_URL` (BLK-3, minor/code-level, **not release-blocking**) | The **plan-level scrub is the ceremony guard**. The spawn boundary is a **live-surface hardening** item, not a release-gate item — changing it would put code on the release line for a defect that does not gate this cut. Recommended for a later release, together with making `assert_prod_dsn_sanctioned` resolve the DB name from `POSTGRES_URL` **as well as** `POSTGRES_DB` (the second half of the same bypass). |
| **F-2** | **PG-side server-DEFAULT asymmetry** — PG has **no server-side DEFAULTs** where the SQLite DDL carries `DEFAULT 'active'/0/'message'`; a raw-SQL INSERT omitting those columns is refused on PG but accepted on SQLite (tester note N1) | Adding `sa_column_kwargs={"server_default": ...}` to `daemon/repositories/room/models.py` would **change the emitted DDL** and **invalidate the PG proof just captured** in §3.2.1, requiring a re-run before the cut. **No product impact today** — every path builds rows through the ORM with Python-side defaults (proven by probe 3). Backlog for a future release. |
| **F-3** | `uq_room_messages_proposal_dedup` is a `pg_constraint` on the fresh-`create_all` path but a bare `CREATE UNIQUE INDEX` on the `_ensure_postgres_columns` path (tester note N2) | Enforcement is **identical** (proven firing). Relevant only to a consumer that introspects `pg_constraint` for that index. No action. |
| **F-4** | `room_messages` physical column ORDER differs between drivers (tester note N3) | Set / nullability / types are identical; order is **not a contract**. No action. |

**Also noted, not actioned:** the tester's LEG 3a worker quoted its fenced command inline, including the **raw dev-role `POSTGRES_PASSWORD`** — exposure was **transcript-only** (0 git hits; the committed `PACKS.md` row carries `<redacted>`). Root cause: a **dispatch-template omission** (the redaction rule was in the LEG 3b brief but not the LEG 3a brief). **Not rotated unilaterally** — the dev PG password is shared by in-flight lanes and rotation is a live-op outside the tester's mandate; surfaced as a rotation candidate. *This lane did not rotate it either; the standing deferred-rotation item covers it.*

## 9. OPEN QUESTIONS FOR THE USER

1. **§7.1 — icon placement.** Per-instance chat-page header (as built) vs the instances-list row (§13.1's literal reading). **If the list row, this is a FE change BEFORE the release cut** — not after.
2. **§6.1 — which lane.** Tool lane (nonce + 3-factor attestation, needs the R1 pin advance) or operator lane (no nonce, run from inside the payload). The brief asked for the attestation; the tool lane is recommended, which makes R1 mandatory.

*(Both are decidable by the user before the stage is cut; neither blocks R0–R2.)*

---

## 10. APPENDIX — evidence index

**Repo facts verified at `origin/latest = 56a5f4a00`** — version source: `pyproject.toml:3`, `daemon/__init__.py:3` · floor: `daemon/tools/upgrade_tools.py:4145`, `scripts/upgrade/lib.sh:95` · guard: `daemon/tools/upgrade_tools.py:4210` (`scripts_dir_provenance_verdict`), `:2031` (`_resolve_scripts_dir`) · tag/describe: `scripts/upgrade/stage.sh:203` · promote live guards: `scripts/upgrade/promote.sh:129`, `:142` · retention: `scripts/upgrade/lib.sh:403`, `:8504` · boot sweep: `daemon/services/boot_subprocess_sweep.py:120`, `:127`, `:140` · migrations bundle: `ensemble.spec:12-13` · runner: `daemon/migrations/runner.py:211`, `:213-232`, `:395-410` · PG schema: `daemon/manager.py:1062-1068`, `:8578`, `:8908-8945` · boot regen: `daemon/repositories/room/boot.py` + `daemon/manager.py:4908-4928` · tool registry: `daemon/tools/_tool_registry.py:350`, `:903`, `:1534` · artifact/constant: `.agents/shared/metrics/tool-categories.json`, `daemon/checkpoint_metrics.py:290` · FE icon: `frontend/src/app/pages/chat/chat.html:89`.

**Doctrine sources** — `docs/runbooks/promote-handback-serving-green-closer.md` §9 (the two-sided release-bump requirement; the four user-owned ceremony pre-steps; the disposition doctrine) and §10 (invoke-path policy: release-staged PRIMARY, pinned worktree = recovery) · house critical notes (boot-sweep pin; `env -u POSTGRES_*` no-op; stage.sh `rollback_safe` derive-path; install-dir protection).

**Feature facts** — `.agents/shared/planning/group-chat/{plan-overview,architecture-recommendation,decisions,emperor-open-questions,substrate-map}.md` at tip; A-P5-2, A-P5-8, A-P5-17a, A-P5-17b, A-P6-1, A-P6-22, A-P6-23, A-P6-26, A-P6-35, A-P6-36 · KV `gc_pilot_video_team`, `gc_phase2_go`, `gc_ui_refinement_p6`, `gc_emperor_rulings_20261115` · `.agents/tester/PACKS.md` (P6 re-gate #2 block), `.agents/tester/QUARANTINE.md` (2 group-chat-adjacent entries).

**Disk measurement (2026-11-17)** — stage footprint ~376 MB gross / **~200 MB net** after the keep-3 eviction ⇒ **fits with ~9× headroom** against **1.9 GiB free @ 100 %** (`/dev/sda1` 230 G / 229 G used). Live `releases/v0.30.12` measures **175 MB**, its `ensemble-prod` **79 MB**. Sanctioned reclamation already done: **281.6 MiB** (pip cache · uv cache · `.angular/cache` · 294 `__pycache__` dirs · `.pytest_cache`). Levers deliberately **not** taken: shared-cache prune (go-build **7.2–7.6 GB**, npm **2.1 GB** — kept **warm for live lanes**) and a reconcile sweep of the **38 `wt-stage-*` worktrees (46 GB)** — **foreign-lane disruption is the larger risk**; both remain discretionary operator levers. `df` fluctuates with concurrent lanes (3.3 GB → 2.0 GB → 1.9 GiB observed; `/tmp` moved 79 GB → 71.9 GB mid-window via another lane's cleanup) — **re-measure before the stage**.

**Cut-sequence anchors (verified at tip by this lane)** — `scripts/upgrade/stage.sh:348` (FE dir required ⇒ exit 78) · `:360` (sidecar verify) · `:468` (`cp -R` of the prebuilt `browser/` into the payload) · `:498` (`rm -f` the sidecar out of the staged copy) · `scripts/upgrade/_build_frontend.sh` (runs `npm run build` only — **no `npm ci` / `npm install`**) · the present dist's sidecar stamps `git_head 913088277…` while the branch HEAD is `af661ac8e…` ⇒ **the present dist is stale and not stageable**.

**S3 FE measurement (2026-11-17, devops dry-run, release worktree @ `913088277`, no commits)** — the measured recipe, the Angular 21 artifact-set shape, the verification-trap markers, the dirt precision and the sidecar contents are recorded in §3.5.1–§3.5.5. Anchors verified by this lane at the release tip: `scripts/upgrade/_build_frontend.sh:105` (the static `build_tool` label) · `scripts/upgrade/stage.sh:498` (the sidecar `rm -f` out of the payload, and the manifest per-file map computed after it) · `scripts/upgrade/lib.sh:95` (`PIPELINE_SCRIPTS_REV=1aa0065e…` in the release worktree) · `frontend/.gitignore` (only `/.angular/cache`, `/dist`, `/node_modules`) · `frontend/angular.json` (no `polyfills` key; `assets` = one `public` glob; `scripts` = `mermaid.min.js`) · `frontend/package.json` (no `engines`; `packageManager npm@10.9.4` advisory) · `node_modules/@angular/cli/package.json` `engines` (`node ^20.19.0 || ^22.12.0 || >=24.0.0`). Measured host state: node **v24.21.0** / npm **11.19.0** · `frontend/node_modules` **691 MB** · `/dev/sda1` **100 % used, 2.2 GB free** · dist census **96 files / 7,949,189 B** (85 `chunk-*.js` = 3,480,246 B; `index.html` 9,257; `main-*.js` 217,094; `styles-*.css` 655,629; `scripts-*.js` 3,559,319; icons+favicons 27,292; sidecar 352) · sidecar `artifact_sha256 14d5b946c1c13258181887461f5dc81957ac0dce18d66c21f3f077bc0a24f7ee` **independently re-verified MATCH** · `git status --porcelain` **EMPTY** after the build.

**Release-prep verification (2026-11-17, the correction pass)** — `.agents/tester/RESULTS/2026-11-17-group-chat-v0310-release-verification.md`, commits `463044ce4` → `4b56f992e` → `92fcf26af` in `feature/group-chat-release`; worktree `/home/nea/ensemble-src-wt-group-chat-release`. LEG 1 (PG parity) **PASS-WITH-NOTES** · LEG 2 (prompt seal) **PASS** · LEG 3a (room family) **PASS** · LEG 3b (boot-smoke recipe) **PASS-WITH-NOTES — plan defect**. The only code change in that session is `b47e9165f` (`.agents/tester/PACKS.md` only, append-only, +9/−0) — **no production code, no version bump, no release dir, no tag, no push, no live process started/stopped/killed, no live port bound.**

**Correction-pass code anchors (verified at tip by this lane, not inherited):** `daemon/persistence.py:233-235` (`POSTGRES_URL` returned verbatim) · `daemon/ensemble_config.py` `resolved_postgres_db_name` (reads only `POSTGRES_DB`; *"the rendered URL is NEVER parsed"*) · `daemon/ensemble_config.py:241` `assert_prod_dsn_sanctioned`, live carve-out at `:269` · `daemon/ensemble_config.py:56-90` `load_or_create` (file-authoritative `ensemble.json`) · `daemon/services/boot_subprocess_sweep.py:120/127/132/140-180` · `daemon/services/bash_spawn_reaper.py:109` · `daemon/config.py:1173-1204` (RAM guard) and `:75-76` (`PORT` resolution; `DAEMON_PORT` ignored) · `daemon/config.py` `substitute_env_vars` · `scripts/run_tests_scrubbed.sh:47-84` · `daemon/services/spawn_env_policy.py:51-77` · `scripts/upgrade/stage.sh:464-504` (no `data/`) and `:1001-1035` (`prompt_artifact_drift_leg`, reported-not-gating) · `ensemble.spec` datas = exactly 4 entries (no `agents/`) · `daemon/tools/prompt_artifacts.py` `VERSIONED_PROMPT_ARTIFACTS` (`.md`-only).

**This document** now lives on the **release branch** (`feature/group-chat-release`, worktree `/home/nea/ensemble-src-wt-group-chat-release`) and is committed there — per the house convention that planning lands **in-branch**. An earlier revision was drafted in the main checkout's working tree (detached `@ bf4bfde6e`); that scratch copy has been **removed** so there is exactly one copy of truth.
