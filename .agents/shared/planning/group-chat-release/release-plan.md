# Group-Chat Staged-Release Lane — Vehicle, Staging, and Ceremony Brief

**Status:** PLAN (vehicle + staging only — the live promote is the Emperor's ceremony)
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
| **PostgreSQL** | `create_all` creates the three tables from the **registered** models, and `_ensure_postgres_columns` adds the four columns + the unique index idempotently | ⚠️ **No live-PG integration test exists for the room tables.** Proof rests on registration + `IF NOT EXISTS` idempotency. **Recommendation: the S2 leg must run the room-family suite against a real PG scratch DB** (`ensemble_dev_scratch` exists; `ensemble_dev_test_only` does **not** exist on this host) to close this gap. |
| **Boot** | Room tables ready at boot; boot regen is non-fatal; the GC latch is two-sided (READ pins on success+retry, WRITE pins unconditionally, RAM-only, restart re-arms) | `tests/unit/repositories/test_room_boot_f1_pin.py`, `test_room_capability_row_pin.py` |

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

This is a **ratified-phase contract**, not a lane accident (decisions.md A-P5-2 / A-P5-17b): the grant census is EIGHT agent types — the four warm-lane agents plus the video-team pilot quartet, the Emperor's designated FIRST pilot (KV `gc_pilot_video_team`). The `room` audience is **not** a fixed privileged panel — the authority is the **membership gate in the tool bodies** (a granted agent that is not an ACTIVE member gets nothing, fail-closed, before any write). Membership is granted **at spawn** (`join_room` spawn parameter), so the membership row exists before the child's tools are built.

**Release implication:** `agents/` is a staged payload tree (§2.1), so the 8 grants ship automatically. The release lane must nonetheless **assert** that all eight carry `"room"` at the release tag — the two re-pinned `ari` tail assertions (POK grant pack 6→7 tail entries; nest-anomaly-report pack 12→15 entries) are the pins that must stay green.

### 3.5 The frontend asset set

**14 files** (the §13.1 P6 FE surface): `frontend/src/app/models/room.model.ts` · `models/index.ts` · `services/room.service.ts` (+ `.spec.ts`) · `components/room-dialog/{room-dialog.component.ts,.html,.scss,.spec.ts}` · `pages/chat/chat.html` · `chat.component.ts` (+ `.spec.ts`) · `chat.scss` · `styles.scss` · `app.component.spec.ts`.

**How the FE ships:** `scripts/upgrade/_build_frontend.sh` runs the Angular build inside `frontend/` → `frontend/dist/frontend/browser/`, then stamps the provenance sidecar `frontend/dist/frontend/browser/index.html.build-provenance.json`. `stage.sh` **verifies** the sidecar against the source tree and then **`rm -f`s the sidecar out of the payload** (the payload tree is web-servable; the sidecar must not be served).

**House rule (mandatory):** TypeScript only via `frontend/`'s local devDependency — `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json`. **Never** bare `tsc` / `npx tsc` outside `frontend/` (the 2026-10-03 incident: `npx` fetched and executed a registry package named `tsc`).

**Release verification for the FE (§5.3, leg S3):** `tsc --noEmit` exit 0 · `npm run build` succeeds · the sidecar exists in the source tree with `git_dirty=false` and `git_head == release commit` · the staged `frontend/dist/frontend/browser/` contains the room-modal assets · the 14-file surface is present in the built bundle.

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

**S1-integrity leg addition (call it S1d):** at the release tag, assert

```
git diff --stat v0.30.12 v0.31.0 -- agents/.prompt-artifacts.sha256   # MUST be empty
git diff --stat v0.30.12 v0.31.0 -- 'agents/*.md'                     # MUST be empty
sha256sum agents/.prompt-artifacts.sha256                             # MUST equal the v0.30.12 value
```

and confirm the frozen binary's embedded prompt-artifact set matches the seal. **Pass criterion: all three empty/equal.** Any non-empty diff is a 🔴 stop — it means a prompt changed outside the sealed lane and the approval lane was bypassed.

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

**The alternative — the release-staged path.** Per runbook §10.1 the release-staged promote is the **default**, and it needs no pinned checkout: the payload carries the whole executor family and its own `daemon/plugin_subsystem/` slice, and `stage.sh` fail-closes at stage time if the payload's own staleness predicate cannot execute. **The non-git payload path is safe from the floor gate by design** — `scripts_dir_provenance_verdict` on a **non-git** dir is **WARNING-ONLY**: *"a stamp arms, no stamp warns; NO refusal is reachable from that branch (AC11)"*. The payload's `lib.sh` carries the stamp `1aa0065e`, so a payload-driven promote **arms**.

⇒ **Recommendation: do BOTH.** Advance the pin (so the *tool-lane* arm can succeed) **and** run the promote from inside the payload (the §10.1 default). If the Emperor prefers the operator lane only (no tool-lane arm), the payload path alone is sufficient — but then the ceremony has **no nonce and no 3-factor attestation**, and the journal shape differs (no `nonce_consumed` row). See §6.

**Floor re-pin — recommended but not blocking.** The group-chat feature introduced **zero** changes to `scripts/upgrade/` or `daemon/tools/upgrade_tools.py` (verified per-branch: p1, p2, p3, p4, p4-followups, p5, p6 all show **no** delta in those paths). The floor was last re-pinned by `f8dc874f7` (`fix(floor): ENSEMBLE-32 repair lane — PIPELINE_SCRIPTS_FLOOR_COMMIT re-pin covering config_compat_* family`), with two later scripts commits (`9d92b9f65` D-5 wrapper argv shift, `4ee4c44a1` D-6 contract resolution) landing **after** it. The floor therefore does not *name* the newest shipped script semantics — but the enforced predicate is ancestry-only, so it **passes**. 🟡 **Recommendation:** re-pin both channels to the v0.31.0 release commit (they travel together per §9.1) so the floor names the family actually shipped. **This is optional; it is hygiene, not a gate.** If re-pinned, re-stage afterwards (the manifest carries `pipeline_scripts_floor_commit`).

### 5.2 The scripts-pin floor — what floor THIS release needs

| Question | Answer |
|---|---|
| What floor does the release need? | **A floor that is an ancestor of the built tree AND an ancestor of whatever scripts dir the executor will run from.** At tip that is `1aa0065e…` on both channels. If the release re-pins (§5.1), the new floor must be the release commit itself, set **identically** in `upgrade_tools.py:4145` and `lib.sh:95`. |
| Is the pin satisfiable today? | **No** — the live pin (`bf4bfde6e`) predates the tip floor (`1aa0065e`). |
| What must be established first? | The pin advance in §5.1, steps 1–4, then a daemon restart to load the new `.env`. |
| What if it is not done? | The tool-lane arm **refuses pre-nonce** with `stale-scripts`. The nonce is preserved, so the ceremony can be retried after the fix — **no live damage, no partial flip**. |
| Is there a bypass? | `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` bypasses with a loud **warn** verdict — per-invocation only, never persisted to `.env` (T6.12). **Do not use it.** It exists for drills, and using it on live would run stale executor scripts against a new payload — the exact hazard the guard exists to stop. |

### 5.3 Stage-verification legs S1–S5

| Leg | What it proves | How | Pass criterion | Fresh? |
|---|---|---|---|---|
| **S1 — release integrity** | The staged artifact is the thing that was built and tagged | `git describe --tags --exact-match HEAD` == `v0.31.0`; manifest ⇄ staged-dir re-hash (`integrity_verify`); sidecar present, `git_head == release commit`, `git_dirty == false`, `artifact_sha256` == artifact hash; `pipeline_scripts_floor_commit` == `pipeline_scripts_rev` | All four legs match; no `non-tip-tree` / `provenance-*` / `stale-provenance` / `dirty-build` token | **fresh** |
| **S1d — prompt-artifact seal** | No prompt changed outside the sealed lane | §4.3's three checks | All empty/equal | **fresh** |
| **S2 — migrations on BOTH drivers** | The schema lands on SQLite *and* PostgreSQL | SQLite: `tests/unit/repositories/test_room_migration_runner_execution.py` + `test_room_models_and_migration.py`. PG: room-family suite against a **real PG scratch DB** (`ensemble_dev_scratch`; `ensemble_dev_test_only` does not exist here) — create if needed; **scrub `POSTGRES_*` per-variable, never `env -u POSTGRES_*`** | All 3 migrations apply clean on SQLite (ledger rows, `skip_reason=None`); on PG `create_all` creates the 3 tables and `_ensure_postgres_columns` adds the 4 columns + unique index, idempotently | **fresh — and the PG half does not exist as a test today** |
| **S3 — FE build** | The FE compiles and the staged dist carries the room surface | `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` (exit 0) → `npm run build` → sidecar written; `stage.sh` verifies the sidecar and strips it from the payload; assert the staged `frontend/dist/frontend/browser/` contains the room-modal assets | tsc exit 0; build PASS; sidecar valid; staged assets present | **fresh** |
| **S4 — gate evidence** | The feature's own gates still pass at the release commit | Re-run the registered packs at the release tag: `gc_p6_room_family` (24 files — **re-measure**, the merged-tree run says 421, the re-gate row says 420), `gc_p6_room_mutation_matrix` (9/9), `gc_p6_pilot_scenario` (9/9), SSE-adjacent corpus (111/111), TestP11 (39), nudges (42), language_check (124), ledger gate (5/5, `EXPECTED_LATEST=36`), FE specs (140/140 + modal 8/8) | Counts match the re-gate #2 record; **any count delta is explained**, not hand-waved | **re-confirm** (evidence exists; re-run at the tag) |
| **S5 — boot smoke** | The frozen binary boots green with the new schema | See §5.4's fenced invocation | `livez` version match, `readyz` green (db/queue/services/checkpoint), migrations applied, pool clean, **zero sweep kills** | **fresh** |

**Evidence lands in:** `/home/nea/agents-ensemble/data/upgrade/live/<run_id>.log` (narrative) + `run-<ts>-<pid>.payload.log` (payload), plus `--junitxml` for S4. Use `.txt` names, not `.log` — `.gitignore` has `*.log` and would silently hide a transcript.

### 5.4 The S5 boot-smoke fence — the F1 hazard, called out

**🔴 HOUSE CRITICAL NOTE, carried verbatim in intent:** frozen-binary boot smokes from daemon-child shells **MUST pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly**. `env -u` on it **strips the ambient guard**, and the sweep **KILLS LIVE processes**.

**Mechanism (verified at tip):** `daemon/services/boot_subprocess_sweep.py:120` → `ENV_KILL_SWITCH = "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"`; `:127` → `DEFAULT_ENABLED = True`; `:140` → `is_prod_lane()` returns true for a **frozen** binary. So with the variable **absent**, a frozen-binary boot defaults the sweep **ON** — and a daemon-child shell inherits the live daemon's cgroup, so the sweep classifies the live launcher + `ensemble-prod` pair as orphans and SIGTERM+SIGKILLs them. (2026-10-31 incident: exactly this; systemd auto-restart, `NRestarts=1`.)

**The live `.env` already carries the guard:** `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` (verified). The hazard is not the value — it is **`env -u` stripping it**.

**The safe invocation — pin, never strip:**

```bash
# PIN the sweep guard OFF explicitly. Never `env -u` it.
ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
ENSEMBLE_SELF_ENV=live \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_prod \
POSTGRES_USER=<live-user> POSTGRES_PASSWORD=<redacted> \
PORT=9797 \
/home/nea/agents-ensemble/releases/v0.31.0/ensemble-prod
```

**Companion fence — the `POSTGRES_*` scrub:** `env -u POSTGRES_*` is a **silent NO-OP** (bash does not glob env names), so an ambient `POSTGRES_DB=ensemble_prod` stays visible and a "fenced" run can hit prod. Scrub **per variable**:
`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT <cmd>`
— or, better, **pin the values explicitly** as above, which is what the safe invocation does.

**Also pin `PORT`** (runbook §9.2, fourth pre-step): a PORT-less boot makes the launcher's serving probe unprobeable — never counted, never rollback-eligible — so it *defers* even a `rollback_requested`. The live `.env` already carries `PORT=9797` ✓ and `config.yaml` carries `daemon.port: ${PORT:-8088}` ✓.

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

## 6. THE EMPEROR'S PROMOTE CEREMONY (his, not this lane's)

### 6.1 The two lanes — and which one the Emperor is being handed

| | **Tool lane (armed)** | **Operator lane (direct)** |
|---|---|---|
| Who runs it | ari calls `system_upgrade` on a turn with valid user-origin provenance | a human/agent runs `promote.sh` directly |
| Nonce | **Required** — minted, echoed, consumed at arm (`consume_pending_action`) | **Not used** — `promote.sh` is nonce-free |
| Scripts dir | resolved by `_resolve_scripts_dir` → **the `.env` pin** | **the payload** (CWD) — runbook §10.1 default |
| Provenance guard | **applies** → the pin must be advanced (§5.1) | n/a (non-git payload ⇒ warn-only) |
| Journal shape | includes `arm-scripts-provenance` + `nonce_consumed` | no `nonce_consumed` row |

The last two live promotes (v0.30.12, v0.30.9) used the **tool lane** with the pin. **Recommendation: run the tool lane** (it carries the attestation the brief asks for) **against an advanced pin**, and have the executor run the payload's scripts.

### 6.2 What the leader hands the Emperor — the ceremony brief

**Step 0 — the operator preconditions (before the ceremony; all four must be TRUE and stated):**

| # | Precondition | Live value at planning time | Status |
|---|---|---|---|
| P1 | `ENSEMBLE_UPGRADE_SCRIPTS_DIR` advanced to a tree whose HEAD contains `1aa0065e…` as an ancestor; daemon restarted to load it | `/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade` @ `bf4bfde6e` — **STALE** | 🔴 **OUTSTANDING — §5.1** |
| P2 | `SWEEP_SERVING_GREEN_ENABLED=1` (the launcher kill-switch) in force on the ceremony's launcher — **and the value stated in the ceremony notes** | not set in `.env` ⇒ launcher default (enabled) | 🟡 **confirm and state** |
| P3 | If `HANDBACK_VERIFY_BUDGET_S` is raised, it must live in the **install `.env`**, NOT the ceremony shell (`launcher.sh` never sources `lib.sh`; the daemon child sees the knob only through the `.env` seam) | not set ⇒ default | 🟢 unless raised |
| P4 | `PORT` set in the install `.env` **and** `config.yaml` carries a `daemon.port` key | `PORT=9797` ✓ · `daemon.port: ${PORT:-8088}` ✓ | ✅ **satisfied** |
| P5 | Install `config.yaml` is **key-shape compatible** with the v0.31.0 model contract (`promote.sh` entry check `_config_compat_check` ⇒ exit 78 `config-incompatible`) | `config.yaml` is **unchanged** between v0.30.12 and `origin/latest` ✓ ⇒ contract unchanged | ✅ **expected pass** — verify at stage time |
| P6 | Stage complete: `releases/v0.31.0/` present, `manifest.json` signed, `contains_contract_phase=true`, `rollback_safe=true` | — | pending stage |

**Step 1 — the Emperor's inputs:**
1. The **target**: `v0.31.0`.
2. The **user confirmation** — a message from the Emperor on a channel the daemon classifies as user-origin (`api`, or a registry `source_type` in `{telegram, slack, discord, whatsapp}`). The tool lane stamps a **per-turn origin window**; internal/agent relays **clear** it. A leader-relayed execution **refuses `user-confirmation-missing` BEFORE the nonce burns** — so the Emperor's own turn must carry it.
3. The **nonce echo** — the nonce minted for `(target=v0.31.0, kind=upgrade, env=live, instance)` in `state.json.pending_actions`, echoed back to the arming instance. `nonce_normalize` strips dashes and case. **TTL: `NONCE_TTL_S = 3600`** (mint → confirm, absolute).
4. The **3-factor attestation**: (i) `user_confirmed = true`; (ii) a valid per-turn user-origin window (`manager.stamp_user_origin_window` + `upgrade_journal.classify_user_origin` — exact `"api"` or the four registry source types, fail-closed elsewhere); (iii) the nonce present in `pending_actions`, bound to (target, kind, env, instance), consumed at arm via `consume_pending_action`.

**Step 2 — the command handed to the Emperor / operator:**

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

### 7.1 🔴 ICON PLACEMENT — **the Emperor adjudicates before promote**

The chatrooms icon is implemented in the **per-instance chat-page header**, next to WATCHOVER, wrapped in `@if (renderedInstance())` (`frontend/src/app/pages/chat/chat.html:89`) so it is **hidden when no instance is selected** (A-P6-36). The binding direction (`gc_ui_refinement_p6`) reads *"CHATROOM ICON next to the WATCHOVER button on the frontend instances surface"*, and the pinned architecture §13.1 reads *"chatroom icon adjacent to WATCHOVER on the instances surface"*, while the same ruling also says *"from the instance row the user already looks at"*.

**The tension:** the implementation is on the **chat page** (the per-instance view). §13.1's literal *"instances surface / instance row"* reading would place it on the **instances list**. **Adjacency is preserved; SURFACE is not.**

⇒ **The Emperor adjudicates at release/promote.** If he wants the list row, that is a **follow-up FE change BEFORE promote** (the modal is tree-scoped and instance-scoped alike, so the work is placement-only). **Resolve this before the stage is cut** — a post-promote FE change would require another release.

### 7.2 🟡 The video-team pilot runs POST-promote

The Emperor's directive (2026-11-15) is explicit: the parked video-team lane resumes **only AFTER group chat is PROMOTED** (the Emperor's ceremony) — not merely landed. The video team is the **designated FIRST pilot**: producer / capturer / editor / verifier coordinate through a chatroom as **named members** instead of pure parent-routing.

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

### 7.6 🟢 Two further disclosures for completeness

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
| **R3 — Build + stage** | **giter** | Build the binary + FE with provenance sidecars; `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.31.0 bash scripts/upgrade/stage.sh live` | `releases/v0.31.0/` complete; `manifest.json` signed; `rollback_safe=true`; `contains_contract_phase=true`; `pipeline_scripts_floor_commit == pipeline_scripts_rev` |
| **R4 — Verify S1–S5** | **giter + tester** | Run the five legs (§5.3), including S1d (prompt seal) and the **PG half of S2** | All legs green; every count delta explained |
| **R5 — Ceremony brief** | **architect → leader** | Hand the Emperor the §6.2 brief: preconditions, inputs, command, pre-checklist, post-verify, abort path, and the §7 disclosures | The Emperor has the brief and the §7.1 icon question is resolved |
| **R6 — Ceremony** | **the Emperor** | The promote itself | `commit` row; `current == v0.31.0`; §6.2 V1–V10 green |
| **R7 — Post-promote** | **leader + pilot lane** | FE-visible verification (V8); resume the **video-team pilot**; the pilot scenario is the gate | Pilot scenario 9/9; no open txn; `in_flight == null` |

### 8.2 Rollback / undo path if the ceremony aborts

Per §6.3 — **the abort ladder**: no-flip refusals are free (nonce intact); a flip+halt with a **serving-green** target ⇒ **stay** + `txn_repair_manual`; a launcher sweep loop ⇒ clear `in_flight` to `null` (back up `state.json` first); a committed-but-unhealthy target ⇒ operator-driven `rollback.sh`. **Auto-rollback never fires outside the ceremony window**, and the release is additive — a rollback leaves harmless orphan room tables.

### 8.3 What this lane does NOT do

- It does **not** perform the promote (the Emperor's).
- It does **not** touch the local-latest ref (the ari-stop-int lane's).
- It does **not** re-pin the `room` tool-category artifact or bump `VOCABULARY_VERSION` (frozen by design).
- It does **not** bump the prompt-artifact baseline (nothing changed).
- It does **not** append a group-chat ledger row (avoiding the `EXPECTED_LATEST` bump).

---

## 9. OPEN QUESTIONS FOR THE EMPEROR

1. **§7.1 — icon placement.** Per-instance chat-page header (as built) vs the instances-list row (§13.1's literal reading). **If the list row, this is a FE change BEFORE the release cut** — not after.
2. **§6.1 — which lane.** Tool lane (nonce + 3-factor attestation, needs the R1 pin advance) or operator lane (no nonce, run from inside the payload). The brief asked for the attestation; the tool lane is recommended, which makes R1 mandatory.

*(Both are decidable by the Emperor before the stage is cut; neither blocks R0–R2.)*

---

## 10. APPENDIX — evidence index

**Repo facts verified at `origin/latest = 56a5f4a00`** — version source: `pyproject.toml:3`, `daemon/__init__.py:3` · floor: `daemon/tools/upgrade_tools.py:4145`, `scripts/upgrade/lib.sh:95` · guard: `daemon/tools/upgrade_tools.py:4210` (`scripts_dir_provenance_verdict`), `:2031` (`_resolve_scripts_dir`) · tag/describe: `scripts/upgrade/stage.sh:203` · promote live guards: `scripts/upgrade/promote.sh:129`, `:142` · retention: `scripts/upgrade/lib.sh:403`, `:8504` · boot sweep: `daemon/services/boot_subprocess_sweep.py:120`, `:127`, `:140` · migrations bundle: `ensemble.spec:12-13` · runner: `daemon/migrations/runner.py:211`, `:213-232`, `:395-410` · PG schema: `daemon/manager.py:1062-1068`, `:8578`, `:8908-8945` · boot regen: `daemon/repositories/room/boot.py` + `daemon/manager.py:4908-4928` · tool registry: `daemon/tools/_tool_registry.py:350`, `:903`, `:1534` · artifact/constant: `.agents/shared/metrics/tool-categories.json`, `daemon/checkpoint_metrics.py:290` · FE icon: `frontend/src/app/pages/chat/chat.html:89`.

**Doctrine sources** — `docs/runbooks/promote-handback-serving-green-closer.md` §9 (the two-sided release-bump requirement; the four user-owned ceremony pre-steps; the disposition doctrine) and §10 (invoke-path policy: release-staged PRIMARY, pinned worktree = recovery) · house critical notes (boot-sweep pin; `env -u POSTGRES_*` no-op; stage.sh `rollback_safe` derive-path; install-dir protection).

**Feature facts** — `.agents/shared/planning/group-chat/{plan-overview,architecture-recommendation,decisions,emperor-open-questions,substrate-map}.md` at tip; A-P5-2, A-P5-8, A-P5-17a, A-P5-17b, A-P6-1, A-P6-22, A-P6-23, A-P6-26, A-P6-35, A-P6-36 · KV `gc_pilot_video_team`, `gc_phase2_go`, `gc_ui_refinement_p6`, `gc_emperor_rulings_20261115` · `.agents/tester/PACKS.md` (P6 re-gate #2 block), `.agents/tester/QUARANTINE.md` (2 group-chat-adjacent entries).

**This document** was written to the main checkout's working tree (detached `@ bf4bfde6e`) and is **not committed**. Per the house convention that planning lands **in-branch**, giter must copy it onto the release branch (R2) and commit it there — do not commit it on the detached checkout.
