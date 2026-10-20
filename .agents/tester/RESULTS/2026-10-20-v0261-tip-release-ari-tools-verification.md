# Scoped Tip-Release Verification — v0.26.1 — does the ari-tools feature survive intact?

**Commission:** SCOPED TIP-RELEASE VERIFICATION — "does OUR ari-tools feature survive intact in the staged v0.26.1?"
**Date:** 2026-10-20 (evidence window 20:53–21:4xZ UTC · 03:53–04:4x ICT 2026-10-21)
**Tester:** instance `834f29a0-0ee4-43e5-b88a-a8f1804f350a` · branch `tester/v0261-tip-verification`
**Target artifact:** `/home/nea/agents-ensemble/releases/v0.26.1` · tag `170da3c8` → `a4f28c26b` (annotated, LOCAL-ONLY) · binary sha256 `3bc32f60…cfe9e` · staged_at `2026-10-20T20:36:46Z`
**Trees under test:** BASE `14a7f3762` (our independently staged+verified v0.26.0) → TIP `a4f28c26b` (the v0.26.1 build tree) → tip-of-day `efee25a27` (origin/latest; 3 docs-only commits past TIP — see §L7)
**Lanes:** 10 parallel workers, one skill each · 0 re-dispatches · 0 timeouts · **0 production-code changes · 0 promote**

## Verdict

# 🟢 TIP-VERIFIED — ari-tools intact in v0.26.1

Every leader item is green, on recomputed evidence: the staged `releases/v0.26.1` is internally consistent and **byte-identical to commit `a4f28c26b`** (binary sha 3-way match; `git archive` diff EMPTY with a mutated-copy positive control; all 5,361 manifest entries re-hashed); **our feature's 30-file surface is blob-sha IDENTICAL** to our independently verified v0.26.0 tree and the entire `agents/` tree has **zero** delta; the four privileged categories sit contiguous at **#9–#12** (count 12, no foreign insertion) with the exact authorized-agent sets; the payload's `agents/` tree is **339/339 byte-identical** to the build tree; the shipped binary boots clean on a disposable scratch PG with **banner v0.26.1, livez/readyz 200 and all 3 board tables materialized as its own role**; the seal gates are green (**161/161 baseline, tripwire 0 stale of 35 pins — the phase-3 residual of 7 stale pins is RESOLVED — audit 46/46**); and our corpus re-runs green (**431/434**, the only 3 reds proven INHERITED with the pin file *and* the pinned daemon code unchanged across the foreign lane).

**The D1 defect (LOW, phase-3) is CLOSED BEHAVIORALLY in the shipped tree** — not by ancestry and not by trusting the `c903fef8` line: `view_write(path="%2e%2e%2fx.html")` returns the error envelope *"percent-encoded path … '%' escapes are refused (nothing was written and no passcode was minted)"*, the live-view root listing is unchanged `[] → []`, the passcode table is unchanged `0 → 0`, and the positive control (`ok.html`) succeeds and mints exactly one row `0 → 1` — the probe is provably non-vacuous. The stale **"D1 ships as-is"** line from the v0.26.0 gate report is therefore retracted with evidence.

**Tip drift (M1-class, recurring) is adjudicated BENIGN and is the only thing the leader must consciously accept**: `origin/latest` advanced `a4f28c26b` → `efee25a27` via 3 commits that touch **only `.agents/tester/`** documentation (341 insertions); every shipped subtree is **tree-sha identical** at both revs and all 5,361 payload entries re-hash identical against **both**. **No re-stage is required, and a re-stage would be REFUSED by the L1 tip-identity guard (exit 78, `non-tip-tree`).** Promote the staged artifact as-is.

## 1. Scope decision

The brief asks one question about one artifact: does the ari-tools feature (P1 board workbook + P2 self_alarm + P3 view_write/board_link/live-board-view + the step-4 `agents/ari` conventions) survive intact in the staged **v0.26.1** — the release the promote ceremony will most likely ship — and what exactly would have to ship if it does not.

Scope = **the staged artifact + the tip tree + our feature's surface**. It is explicitly NOT a re-verification of the concurrent lane's own feature (the question-pause / stale-pause-backstop lane), which owns its own gates. ensure.md Release Gate was **not** re-run (out of scope for an artifact gate; the change set touches none of its paths). The inherited maintenancer 3-red was counted, not fixed (routed separately by the leader).

Nine lanes were derived by splitting along verification-mechanism boundaries (read-only payload forensics / git delta archaeology / privileged-surface forensics / static prompt+migration read-back / real-engine tool behaviour / seal gates / corpus re-run / staged-binary boot / tip-drift adjudication) so that no lane exceeded the 5-minute pack cap and each carried exactly one skill.

## 2. Fences held by every lane

- **Production `:9797` never mutated** — `livez` still reports `0.25.7`; `ensemble-main` MainPID `2296104` / epoch `2026-10-20T06:48:44Z` / `NRestarts=0` unchanged; `current → releases/v0.25.7` unchanged; `releases/state.json` sha identical before/after every lane.
- **`releases/v0.26.1` read-only** — binary sha256 re-hashed after every lane that touched it; payload `find -newermt` = 0 modifications.
- **Foreign trees untouched** — `/home/nea/ensemble-src-wt-stage-v0261` (concurrent lane's staging worktree) and `/home/nea/ensemble-src-wt-ari-tools` (our own ceremony evidence, detached @ `14a7f3762`) were read-only; tracked trees byte-identical before/after.
- **Shared main checkout `/home/nea/ensemble-src` never modified** — it carries FOREIGN uncommitted WIP (`daemon/repositories/skill/*`, `daemon/services/skill_evolution_service.py`, `daemon/services/skill_store_service.py`, `daemon/tools/skill_tools.py` + 3 test files). Every source read was anchored to a **git object** with an explicit rev (`git show <rev>:<path>`, `git grep <pat> <rev>`); no rev-less `git diff`/`git status` was run against it.
- **Ambient live-prod env scrubbed** before every DB-touching/importing command (`POSTGRES_HOST=10.44.0.2`, `POSTGRES_DB=ensemble_prod`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`); scratch PostgreSQL only — `ensemble_tipv_v0261_boot` / `ensemble_tipv_v0261_tool`, fresh owned roles, both dropped and proven gone. **`ensemble_prod` and `ensemble_dev` were never written.**
- **No port collided**: the concurrent ari lane was booting the same artifact; this lane used dedicated dev ports (8085+) and proved ownership via `/proc/<pid>/exe` + `/proc/<pid>/environ` before any signal. 9797 / 8088 / 7979 never bound or killed.
- **No pipeline script executed** (no `stage.sh` / `promote.sh` / `rollback.sh`), no `systemctl` mutation, no bare `npx`/`tsc`, no live-shaped path resolved, every `rm -rf` escape-guarded to a `mktemp` scratch dir, `PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider` on every pytest call, dual-layer `timeout 300` on every pack, no `pytest -x`.

## 3. Per-item results

| # | Leader item | Verdict | Lane | Headline evidence |
|---|--------------|---------|------|-------------------|
| 1 | Artifact integrity of v0.26.1 | ✅ PASS 9/9 | L1 `e20d4997` | sha `3bc32f60…cfe9e` 3-way; sidecars `git_head=a4f28c26b`/`git_dirty=false`; 5,361/5,361 entries re-hashed 0 bad; tag annotated + local-only |
| 2 | Our surface intact in the staged v0.26.1 | ✅ PASS | L2a `544a3759` + L2b `f311d9aa` | 4 categories #9–#12 contiguous (count 12), sets exact; 7 tools 3-leg registered; allow-lists exact; payload `agents/` 339/339 byte-identical; migration present + additive-only UP (9 stmts, 0 destructive); 7 pin files present; conventions sections + 5 obligations + `board_list(status="open")` + ≥30min floor present; re-stamp 161/161 MATCH |
| 3 | Behavioural spot-checks vs the staged build | ✅ PASS 4/4 | L3 `2d1f32ce` | board tables + **board_list global envelope** (`count:0 → create → count:1`, raw-SQL corroborated) · **self_alarm floor**: `29m` → `ERROR …use bash with \`sleep <seconds>\`…` + row delta 0; `30m`/`45m` ACCEPTED (Δ = 1800.000000 s / 2700 s) · **D1 CLOSED**: `%2e%2e%2fx.html` → Error envelope, root `[]→[]`, passcode `0→0`, positive control `ok.html` succeeds `0→1` · shipped binary: 9/9 ari-tools ids hit **1** (v0.25.7 arm all **0**), negative control **0** |
| 3b | Our corpus re-run at tip (regression net) | ✅ PASS 431/434 | L4b `b4d5e6a4` | P1–P5 green, P6 = 134P/3F all INHERITED (pin file + pinned code unchanged; `ff27305c4` predates base) · 0 deselected anywhere · node counts identical to the v0.26.0 gate |
| 4 | Seal gates at the tip tree | ✅ PASS 3/3 | L4 `14ec8c6c` | house baseline `--check` **161/161, 0 stale** · tripwire **0 stale pins** of 35, proven armed by a fenced mutation control · audit **46/46, 0 deselected** |
| 5 | Boot smoke of the staged v0.26.1 | ✅ PASS 11/11 | L5 `cceb234a` | banner `v0.26.1` · livez/readyz 200 (database+checkpoint_saver true) · **3 board tables** as the scratch role · baseline 161/161 · teardown SIGTERM-only, zero residue |
| 6 | Delta report `14a7f3762` → `a4f28c26b` | ✅ PASS | L6 `d80ef2e3` | **our surface 30/30 blob-sha IDENTICAL**; `agents/` delta ZERO; migration registry unchanged (106) |
| 7 | Tip-drift adjudication (landscape change) | ✅ BENIGN | L7 `9cfda79e` | drift = 3 commits / 341 ins **confined to `.agents/tester/`** · every shipped subtree tree-sha IDENTICAL A↔B · 5,361 payload entries re-hash identical against BOTH revs · re-stage NOT required; a re-stage WOULD be refused (exit 78 `non-tip-tree`) |

## 4. Lane detail

### L1 — Artifact integrity of `releases/v0.26.1`: PASS 9/9

Every claim re-derived from disk (not trusted).

| # | Item | Recomputed |
|---|------|------------|
| 1 | Binary sha256 3-way | disk `3bc32f60bf4c0c68fa7b9a53a3110ff87e1ccd52db00eb0a8e9dff02f67cfe9e` == `manifest.binary_sha256` == sidecar `artifact_sha256` ✅ |
| 2 | 3 tree hashes | agents `ec9d3179…d2fda` (339) · plugins `4715cf64…85a7` (4930) · frontend `8d6afeaa…9ae03` (92) — **3/3 match** (method self-validated on v0.25.7 FIRST: 3/3 exact on first attempt) |
| 3 | Full inventory re-hash | **5,361 re-hashed, 0 bad**; 0 missing / 0 extra per subtree; 5,365 payload files fully accounted |
| 4 | Payload shape | exactly `{agents, plugins, frontend, config.yaml, ensemble-prod, launcher.sh, manifest.json}`; `daemon/` absent, `scripts/` absent, **0** `.sql` anywhere, **0** leaked `*.build-provenance.json`, 0 symlinks |
| 5 | Version proof (no `strings`-grep) | `daemon/__init__.py` → `__version__ = "0.26.1"`; `pyproject.toml` → `version = "0.26.1"` |
| 6 | Tag | `cat-file -t` = `tag`; object `a4f28c26b…`; `rev-parse v0.26.1^{commit}` = `a4f28c26b…`; `ls-remote origin refs/tags/v0.26*` **EMPTY** |
| 7 | Payload == committed tree | `git archive a4f28c26b` → `diff -r` agents/ **exit 0**, plugins/ **exit 0** — with a mutated-copy positive control (1 byte appended to a *copy*) correctly returning exit 1 |
| 8 | Fences before/after | `current → releases/v0.25.7` identical; `state.json` sha identical; `:9797` listener pid `2296421` identical; `MainPID=2296104`, `NRestarts=0` identical |
| 9 | Staging worktree state | HEAD `a4f28c26b…` before+after, detached; `status --porcelain -uall` **not empty** (1 untracked path) — see §5 D1 |

**Provenance sidecars** live in the **staging worktree**, not in the payload (by design — item 4 proves zero leak):
`dist/ensemble-prod.build-provenance.json` → `git_head=a4f28c26b2cfac067169d298bc33fc945ffe265b`, `git_dirty=false`, `build_at=2026-10-20T20:28:15Z`, `artifact_sha256=3bc32f60…cfe9e`;
`frontend/dist/frontend/browser/index.html.build-provenance.json` → same `git_head`, `git_dirty=false`, `artifact_sha256=a017dd21…1b0c` (== `frontend_manifest["dist/frontend/browser/index.html"]`).

**Manifest fields:** `version=v0.26.1` · `binary_version=0.26.1` · `staged_at=2026-10-20T20:36:46Z` (== manifest mtime to the second) · `known_schema_gen=20261020_060000_create_board_tables.sql` · `contains_contract_phase=true` · `rollback_safe=true` · `config_sha256=7bab75e9…` · `launcher_sha256=a3a44461…`.

### L6 — Delta report `14a7f3762` → `a4f28c26b`: PASS (our surface untouched)

**The `daemon/` delta is EXACTLY the claimed "11 files / 2,761 insertions"** (file count 11, insertions 2,761 hand-summed and cross-checked against `--stat`; deletions 89). Full-repo rollup: `daemon/` 11 files / 2,761 ins · `tests/` 4 / 2,416 · `.agents/` 4 / 870 · `test/` 6 / 594 · root 2 / 2 · **`agents/` 0** · `plugins/` 0 · `frontend/` 0 · `scripts/` 0 · `docs/` 0 · **total 27 files / 6,643 ins / 94 del**.

**Decisive test — blob-sha comparison of our 30-file surface, BASE vs TIP: 30/30 IDENTICAL.**

| group | files | result |
|---|---|---|
| `daemon/models/board.py`, `models/__init__.py` | 2 | IDENTICAL |
| `daemon/services/{board_service,board_view_service,board_view_pages,self_alarm_service,live_views}.py` | 5 | IDENTICAL |
| `daemon/tools/{board_tools,board_link,self_alarm,view_write,live_views,_tool_registry,__init__}.py` | 7 | IDENTICAL |
| `daemon/routers/live_views.py` | 1 | IDENTICAL |
| `daemon/migrations/versions/20261020_060000_create_board_tables.sql` (+ live-view-passcode migration) | 2 | IDENTICAL |
| `daemon/constants.py` | 1 | IDENTICAL |
| `agents/ari/{meta.json,tools_note.md,workflow.md}`, `agents/leader/meta.json`, `agents/.prompt-artifacts.sha256` | 5 | IDENTICAL |

**`agents/` delta = ZERO** (empty numstat) ⇒ our seal re-pin (`14a7f3762`) still holds at TIP.

**Migration registry:** `git diff --name-status -M BASE..TIP -- daemon/migrations/versions/` is **EMPTY**; 106 files at BASE and 106 at TIP; our board migration present and unmodified.

**Classification of all 11 changed `daemon/` files** — none can alter our behaviour:

| file | class | touches our behaviour? |
|---|---|---|
| `daemon/services/question_pause_set.py` (A, 1389) | foreign lane | NO |
| `daemon/services/stale_pause_sweep.py` (A, 589) | foreign lane | NO |
| `daemon/routers/answer_helper.py` (168/42) | foreign lane | NO — answer path only |
| `daemon/routers/instances.py` (147/43) | foreign lane | NO — resume/pending_question/dismiss routes |
| `daemon/services/instance_lifecycle.py` (188/1) | foreign lane | NO — pause/resume cascade |
| `daemon/services/pool_orchestrator.py` (39) | foreign lane | NO — wires the stale-pause sweep |
| `daemon/repositories/instance/repository.py` (146) | foreign lane | NO — our `BoardService` uses the engine directly |
| `daemon/manager.py` (9/2) | shared infra | NO — 3 hunks, all ≥ line 11985: `resume_instance_cascade` gains a keyword-only `exclude_ids`. Our board wiring (`@134` import, `@784-800` model-registration-before-`create_all`, `@1434` `BoardService(engine=…)`, `@3111-3140` live-views property) byte-identical |
| `daemon/api.py` (35) | shared infra | NO — exactly 1 hunk (a 35-line stale-pause boot pass in `lifespan`); the live-views router mount is byte-identical |
| `daemon/config.py` (50) | shared infra | NO — exactly 1 hunk (4 `stale_pause_sweep_*` settings in `ServicesConfig`); `SchedulingConfig` (imported by `self_alarm.py`) and the `live_views` config block byte-identical |
| `daemon/__init__.py` (1/1) | release metadata | NO — `__version__` `0.26.0`→`0.26.1` only |

**Indirect-coupling check:** grepped our surface for the foreign lane's coupling tokens `question_paused|paused_at|suspension_reason` → **zero hits** in our tool/service/router modules (`self_alarm.py`'s `paused` hits are its own alarm-record field, not instance pause state). Every other module our surface imports (`view_passcode_service.py`, `view_passcode_pages.py`, `views_auth.py`, `util/tz.py`, `user_timezone_utils.py`) is byte-identical.

> **Plain statement:** the v0.26.1 tree touches our ari-tools surface: **NO** — every one of the 30 files that constitute the feature is byte-identical to our verified v0.26.0 tree, and the entire `agents/` tree has zero delta.

### L2a — Our privileged surface at the TIP tree and in the payload: PASS 7/7

**Privileged category registry** (`daemon/tools/_tool_registry.py:289`, `PRIVILEGED_TOOL_CATEGORIES`) — parsed with **AST** (not regex: category names also appear quoted in comments and inflate a regex count): **12 categories**, ours at the tail and **contiguous**:

```
#1 system_upgrade  #2 system-log  #3 ens-db  #4 ens-env  #5 view-views  #6 cu  #7 secrets  #8 settings
#9 board  ← ours   #10 self-alarm  ← ours   #11 view-write  ← ours   #12 board-link  ← ours
```

No foreign lane inserted a category between ours ⇒ our category **numbers are unchanged** from the phase-3 record ⇒ the exact-set pins keep their meaning.

**Allowed-agent sets** are layer-3 in-body runtime gates (the registry holds category NAMES only) — all four match the spec exactly:

| category | # | allowed agents | source |
|---|---|---|---|
| `board` | 9 | `{ari, leader}` | `board_tools.py:100` `BOARD_AUTHORIZED_AGENTS = frozenset({"ari","leader"})` |
| `self-alarm` | 10 | `{ari}` | `self_alarm.py:96` `SELF_ALARM_AUTHORIZED_AGENT = "ari"` (check `:647`) |
| `view-write` | 11 | `{ari}` | `views_auth.py` `VIEW_WRITE_AGENTS = frozenset({"ari"})` (check `view_write.py:331`) |
| `board-link` | 12 | `{ari, leader}` | `views_auth.py` `BOARD_LINK_AGENTS = frozenset({"ari","leader"})` (check `board_link.py:195`) |

**Tool registration — 3-leg seam present for all 7 tools** (`board_create`, `board_update`, `board_list`, `board_get`, `self_alarm`, `view_write`, `board_link`): decorator `@register_tool_category(...)` + `CATEGORY_MODULES` entry + factory construction/`tools.extend(...)` in `daemon/tools/instance.py` (`:5874`, `:5896`, `:5928`, `:5935`). All 7 names also appear in `DYNAMIC_TOOL_NAMES` and `KNOWN_TOOL_NAMES`.

**Allow-lists.** `agents/ari/meta.json` → `tools.allow` contains all four (`board`, `self-alarm`, `view-write`, `board-link`); `tools.deny` = `["edit_file","write_file","watch_job","watch_jobs"]` — **none of ours**. `agents/leader/meta.json` → `tools.allow` contains `board` + `board-link` and **does not** contain `self-alarm` or `view-write`; leader has **no `deny` key at all** (reported as absent, not as an empty array).

**Shipped bytes:** the four requested files sha256-identical TIP ↔ staged payload, and the whole tree **339/339 IDENTICAL** (each blob read via `git cat-file --batch`, sha256-compared against the payload). The sidecar binds `git_head=a4f28c26b` / `artifact_sha256=3bc32f60…` ⇒ **the shipped `agents/` tree is the tree the binary was built from**.

**Pin inventory — SEVEN files, all present at TIP, all carrying our four categories:** 5 **exact-set** pins (`tests/unit/tools/test_upgrade_registration.py:152`, `test_attestation_registration.py:189`, `test_secrets_registration.py:226`, `tests/integration/test_maintenancer_spawn_resolves_tools.py:344`, `tests/integration/test_service_tool_flag_off_byte_identical.py:823` — each asserting the full 12-name frozenset) + 2 **count** pins (`tests/unit/test_board_registration.py:185`, `tests/unit/test_self_alarm_registration.py:187` — `len(PRIVILEGED_TOOL_CATEGORIES) == 12`). AST-verified: every exact-set pin's literal `== REGISTRY` True, `missing_from_pin=[]`, `extra=[]`, our-4 present in all five.

> **Correction to the phase-3 record's phrasing:** the set is **SEVEN files = 5 exact-set + 2 count**, not "7 exact-set". The phase-3 file *list* was exactly right; only the label was imprecise. A lane that greps only for `== frozenset` finds 5 files and could wrongly conclude pins are missing.

Adjacent allowed-agent-set pins also present at TIP: `tests/unit/test_board_gap_pins.py:380`, `tests/unit/tools/test_board_tools.py:444`, `tests/unit/tools/test_view_write_tool.py:891-893`, `tests/unit/tools/test_board_link_tool.py:363-367`.

### L5 — Boot smoke of the STAGED v0.26.1 artifact: PASS 11/11

Two independent boot rounds of the **source-less shipped payload** against a disposable scratch PostgreSQL (local PG 16.15, unix socket, fresh role `ens_tipv_v0261` owning `ensemble_tipv_v0261_boot`). Boot env was `env -i` with an explicit allowlist — **no `.env` sourced**; ambient `POSTGRES_HOST=10.44.0.2 / ensemble_prod / PORT=9797 / ENSEMBLE_SELF_ENV=live` fully excluded. `SSL_CERT_FILE`/`SSL_CERT_DIR` were scrubbed because they pointed at the **live daemon's** PyInstaller extraction dir (`/tmp/_MEInfiaCD/certifi/cacert.pem`).

| # | Item | Observed |
|---|------|----------|
| 1 | Boot | ready at poll #6 (18s) both rounds; R1 PID pair 3713542 (bootloader parent) → 3713556 (port owner) on **8085**; R2 3717883 → 3717897 on **8086** (both proven free via `ss -ltn` immediately before bind) |
| 2 | Banner | `21:07:47 - daemon.api - INFO - Starting Ensemble v0.26.1` — count **2** (PyInstaller onefile: parent + child — benign, reproduced identically in both rounds) |
| 3 | `/livez` | **200** · `{"status":"alive","uptime_seconds":6.07…,"version":"0.26.1"}` |
| 4 | `/readyz` | **200** · `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"draining":false}` |
| 5 | **Board tables** | `board_items`, `board_notes`, `board_item_tags` all present, **owner `ens_tipv_v0261`** (privilege-independent `pg_class` catalog) — 77/77 public tables owned by the scratch role ⇒ created by the staged binary on a fresh PG. `board_items` columns: id, title, body_md, status, project_id, due_at, priority(int), linked_job_ids(jsonb), created_by_instance, created_at, updated_at. `schema_migrations` = **0 rows** (expected — the PG path is ORM `create_all`, the runner is SQLite-only) |
| 6 | Log hygiene | `42804` = **0** · `DatatypeMismatch` = **0** · `Application startup failed` = **0** · `DuplicateMigrationVersionError` = **0** · `Skipping migrations for non-SQLite database…` present ×1 |
| 7 | Prompt-artifact baseline at boot | `[Startup] Prompt-artifact baseline clean: 161 versioned artifact(s) match .prompt-artifacts.sha256` — count **1**; **161** for v0.26.1 (same as v0.26.0 — consistent with the identical `agents_tree_sha256`) |
| 8 | Teardown | ownership re-proven 3/3 pre-signal (`/proc/<pid>/exe` == staged binary + `POSTGRES_DB=<scratch>` + `PORT=<mine>`); SIGTERM child → exited in **1s**, **SIGKILL not needed**; bootloader parent self-exited; port freed; `dropdb --force` rc=0 + `DROP ROLE` rc=0 → `pg_database` count 0, `pg_roles` count 0, reconnect `FATAL: password authentication failed`, `pg_stat_activity` rows 0 |
| 9 | Payload immutability | sha256 `3bc32f60…cfe9e` identical pre-boot / post-boot / manifest; `find releases/v0.26.1 -newermt` → **0 modifications**; the 3 tree hashes independently re-derived and **reproduce the manifest exactly** |
| 10 | Live fences before/after | `current → releases/v0.25.7` unchanged; `state.json` sha `0a29954d…` identical; `ensemble-main` MainPID **2296104** / NRestarts **0** / epoch `2026-10-20 06:48:44 UTC` unchanged; 9797 still listening (pid 2296421), 7979 up, **8088 not listening** before or after; single optional `:9797/livez` → `0.25.7`; tree-loss journal → `-- No entries --` |
| 11 | Benign noise | 11 ERROR lines, **all inside `daemon.mcp.*`** (computer-use `libXtst.so.6` ×5, context7 first-attempt timeout, webfetch missing binary); 12 WARNINGs, incl. 2 expected: our own `ENSEMBLE_ORPHAN_F1_ENABLED=0` kill-switch and the **INHERITED** `maintenancer` deny-entry warning (same signature as the known live-tree 3-red) |

**Round-1 → Round-2 catch (harness artifact, not a staged defect).** Round 1's `env -i` allowlist omitted `SYSTEM_ENCRYPTION_KEY` (present in the ambient env), which produced `[LiveViews] passcode substrate init failed (uniform 404 on the public lane, direct lane unaffected): ViewPasscodeCipherUnavailable` — i.e. the **ari-tools P3 live-view passcode substrate** did not initialise. Round 2 included the key and the shipped binary logged `[LiveViews] passcode substrate ready: strike_ceiling=10 grant_ttl=86400s rate_limit=5/5-per-min` with **0 failures** and zero key warnings. Both rounds independently produced the 3 board tables, banner v0.26.1 and livez/readyz 200 ⇒ the artifact is sound; the first round's finding is a **harness** defect.

**Residue: none.** Scratch DB dropped and proven absent; role dropped; all scratch dirs removed; ports free; no stray `ensemble-prod` processes; staged payload, `current` symlink, `state.json` and the `ensemble-main` unit byte/state-identical. **No promote performed.**

### L2b — Our migration + prompt conventions + re-stamp: PASS 13/13

**Part A — the board migration.** `git ls-tree a4f28c26b daemon/migrations/versions/20261020_060000_create_board_tables.sql` → blob `f703337713922a234026ee28b6394454e5b2af22` (5398 B). Parsed with the **repo's own runner parser** (`daemon/migrations/runner.py` blob `344b8b37…`, real `MigrationFile.parse()`), not a hand-rolled regex:

```
version = 20261020_060000   name = create board tables
manual_only = False   precondition = None
checksum = 84c037f6fe882083b6a0d29da3a17323bcb980e37fbfa4a1cef97df80341cbca
```

| Question | Answer |
|---|---|
| UP statements | **9** (runner's own split; 0 comment lines contain `;`, so the split cannot be corrupted) |
| CREATE TABLE names | `board_items`, `board_notes`, `board_item_tags` (3) |
| CREATE INDEX names | `idx_board_items_status_updated`, `idx_board_items_updated_at`, `idx_board_items_project_id`, `idx_board_items_due_at`, `idx_board_notes_item_id_ts`, `idx_board_item_tags_tag_item` (6) |
| Other statement kinds in UP | **NONE** — all 9 statement-leading keywords are `CREATE` |
| DROP / ALTER / TRUNCATE in UP | **0 / 0 / 0** |
| `MANUAL:TRUE` | **absent** (`manual_only = False`) — correct by design: this file is auto-applied on SQLite; the PG runner is a NO-OP for it |

*Scan subtlety recorded so it cannot become a false row:* the single `DELETE` token in UP is the FK referential-action clause `project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL` — not a statement. No statement-leading `DROP/ALTER/TRUNCATE/DELETE/UPDATE`.

**Registry:** **106** files at TIP, **106** distinct prefixes, `sort | uniq -d` **empty**; the runner's own `_assert_no_duplicate_versions()` guard **PASSED** over the whole extracted TIP set. `git diff --name-status -M 14a7f3762..a4f28c26b -- daemon/migrations/versions/` → **EMPTY** — the v0.26.1 lane added **zero** migration files. (The `+1` accounting is one hop earlier: `1ebdf469f` v0.25.7 = 105 → `14a7f3762` v0.26.0 = 106, our board migration `A`.) The shipped manifest's `known_schema_gen = 20261020_060000_create_board_tables.sql` is consistent.

**Payload `.sql` count = 0** (source-less). The board tables are created on PG by the ORM: `daemon/models/board.py` declares the same 3 tables with the **same 6 index names**, and `daemon/manager.py:792` registers the models via `from .models.board import (  # noqa: F401  # ari-tools P1: register before create_all` — i.e. before `create_all`.

**Part B — our conventions sections (TIP tree).**

| section | location | evidence |
|---|---|---|
| Board | `agents/ari/tools_note.md` L638 `## Board — Durable Cross-Instance Work Items` | "`board_create`, `board_update`, `board_list`, `board_get` give me a durable…" |
| Self-Alarm | `agents/ari/tools_note.md` L713 `## Self-Alarm` | "**Five obligations I keep straight every time I arm an alarm:**" — numeric labels `['1'..'5']`, count **exactly 5**, no 6th: (1) Lateness (both cap states + skip signature) (2) The board is the user-visible surface — promise the board, never a ping (3) What I cannot observe about a fired alarm (4) List semantics (5) The `self-alarm:` label namespace |
| Live Views | `agents/ari/tools_note.md` L793 `## Live Views — Published Pages & the Public Board` | `### view_write` L797 · `### board_link` L821 |
| session-open | `agents/ari/workflow.md` L282 `## Session Open & Close — the Board Habit` → L289 `### Session open — read the FULL open board` → L294 | `board_list(status="open")   # the FULL open board — limit defaults to 50 (range 1-200)` |
| ≥30 min floor | stated in **BOTH** files | `tools_note.md` L720-724: "**The >=30-minute decision rule:** anything I must be reminded of in 30 minutes or more, I arm `self_alarm`. Anything shorter, I `bash` + `sleep` in-turn… The tool enforces the floor: a sub-30-minute arm is rejected with exactly this hint." · `workflow.md` L314-318 `### Timing — alarm vs sleep`: "**30 minutes or more** — arm `self_alarm`" / "**Shorter than 30 minutes** — `bash` + `sleep` in-turn" |

**Part B4 — payload bytes:** `agents/ari/tools_note.md` `37d10642…5ad7e` and `agents/ari/workflow.md` `f31f5bc4…a5fcd` — TIP == payload, **IDENTICAL** both (33 548 B / 17 480 B). `f31f5bc4…` matches the recorded re-pin value for `agents/ari/workflow.md` (`4ffa5bee → f31f5bc4`). Stronger cross-check: whole-`agents/`-tree TIP vs payload → **339 files each, 0 only-in-TIP, 0 only-in-payload, 0 byte-differing**, and the shipped `manifest.json` `agents_manifest` verifies **339/339** against disk (0 missing, 0 mismatch, 0 unkeyed extras).

**Part C — prompt-artifacts re-stamp.** Baseline = `agents/.prompt-artifacts.sha256` (blob `7ef834b8…`), **161 entries**; the payload's copy is byte-identical to TIP.

| path | recorded | recomputed at TIP | verdict |
|---|---|---|---|
| `agents/ari/tools_note.md` | `37d10642…5ad7e` | `37d10642…5ad7e` | **MATCH** |
| `agents/ari/workflow.md` | `f31f5bc4…a5fcd` | `f31f5bc4…a5fcd` | **MATCH** |
| `agents/ari/meta.json` | *(absent)* | `7f7a9a3e…b6987` | NOT PINNED — by design |
| `agents/leader/meta.json` | *(absent)* | `a1964c42…b9611` | NOT PINNED — by design |

Recomputation used **three independent byte sources** in agreement (`git show <rev>:<path>`, `git cat-file blob`, `git archive`-extracted file). Independent house-algorithm confirmation — `daemon/tools/prompt_artifacts.py`'s own `parse_baseline()` + `compute_versioned_artifact_hashes()` run over both trees:

```
[TIP tree agents/]   computed 161  status OK  modified NONE  missing NONE  added NONE
[PAYLOAD agents/]    computed 161  status OK  modified NONE  missing NONE  added NONE
```

**Re-stamp provenance:** `git diff 14a7f3762..a4f28c26b -- agents/.prompt-artifacts.sha256` → **EMPTY (byte-identical)**; the recorded shas for our two pinned files are identical at BASE and TIP ⇒ the foreign lane changed neither our file content nor the baseline.

### L4b — Our ari-tools corpus at the tip tree: PASS 431/434 (3 inherited reds)

Run in the isolated worktree with the 11-var env scrub on every pack. Worktree HEAD `efee25a27` proven tree-equivalent for the surface under test (`git diff --name-only a4f28c26b..efee25a27 -- tests/ daemon/ agents/` = **0 files**).

| Pack | Files | Passed | Failed | Deselected | Runtime | RESULT |
|---|---|---|---|---|---|---|
| P1 live-views board router | `tests/unit/routers/test_live_views_board.py` | 50 | 0 | **0** | 38.9 s | PASS |
| P2 view_write tool | `tests/unit/tools/test_view_write_tool.py` | 53 | 0 | **0** | 28.5 s | PASS |
| P3 board_link tool | `tests/unit/tools/test_board_link_tool.py` | 23 | 0 | **0** | 11.8 s | PASS |
| P4 self_alarm corpus | 5 files | 148 | 0 | **0** | 10.5 s | PASS |
| P5 board corpus | 6 files (`test_board_service.py`, `tools/test_board_tools.py`, `test_board_gap_pins.py`, `test_board_registration.py`, `tests/migration/test_board_index_parity.py`, `tests/migration/test_board_runner_apply_rollback.py`) | 157 | 0 | **0** | 29.8 s | PASS |
| P6 exact-set privileged pins | the SEVEN (derived by grep + cross-checked against commit `f585adea8`'s authoritative enumeration) | 134 | **3** | **0** | 46.3 s | FAIL — 3 INHERITED |

Node counts are **identical to the v0.26.0 staged gate** (50 / 53 / 23 / 148 / 157 / 137) — zero drift.

**Deselection control:** the same 7 P6 files under **default** addopts collect **128/137 (9 deselected)** — the 9 dropped nodes are the *entire* `test_service_tool_flag_off_byte_identical.py` file. The marker-correct invocation collects **137/137, 0 deselected**. The trap is real and was avoided.

**The 3 reds — adjudicated INHERITED (not new):**
1. `test_maintenancer_spawn_resolves_tools.py::TestRealMaintenancerMetaShape::test_real_meta_allow_is_exact_category_set` (`'system_restart' != 'db'` at index 4)
2. `…::test_real_meta_denies_system_restart_per_d20`
3. `…::TestMaintenancerResolves::test_maintenancer_gets_system_upgrade_excluding_system_restart` ("system_restart leaked through resolve_tool_filter… D20 enforcement violated")

Same three node ids as the documented inherited set; `git diff --name-only 14a7f3762..a4f28c26b -- tests/integration/test_maintenancer_spawn_resolves_tools.py agents/maintenancer/meta.json daemon/tools/instance.py daemon/tools/_tool_registry.py` → **EMPTY** (the pin file *and* all the daemon code it pins are unchanged across the foreign lane); `agents/maintenancer/meta.json` allow/deny byte-identical at base and tip; `git merge-base --is-ancestor ff27305c4 14a7f3762` → **YES** (the commit that granted `system_restart` predates our base). The file contributes 10P/3F, matching the v0.26.0 gate's recorded "3F/10P (inherited)". **Routed to the owning lane — not fixed here.**

**Time-bomb:** `test_self_alarm_tools.py` did **not** fire (P4 = 148/148; run window ~04:20–04:30 ICT, outside the ~23:20–23:50 ICT red window).

> **Dispatcher correction to L4b's residual note.** L4b stated "no T-A3 encoded-case test exists, hence P2 is 53/53 green regardless" — that inference is **wrong**, and the recorded counts prove it: `test_view_write_tool.py` ran **44/44** at the phase-3 HEAD (`f67232a67`, pre-merge — PACKS.md `live_views_p3_suites_unit`) and **53/53** at the v0.26.0 tree (`14a7f3762`) and at the tip (`a4f28c26b`). The **+9 nodes** are exactly the 9 D1 pins added by fix commit `c903fef8` (which sits inside merge `bfeca369d`, i.e. inside BASE). The pins are present; the behavioral settlement is L3's job, below.

### L4 — Seal gates at the tip tree: PASS 3/3

Run in the isolated worktree `/home/nea/ensemble-src-wt-tester-v0261` (own venv; `daemon.__file__` proven inside). **Precondition disclosure:** the worktree HEAD is `efee25a27` = `origin/latest`, **3 commits ahead** of the commissioned `a4f28c26b`, all touching only `.agents/tester/**` (RESULTS + PACKS + gotchas docs); the gated surface was proven byte-identical per path — `git diff --quiet a4f28c26b..HEAD` on `daemon/ agents/ plugins/ frontend/ scripts/ tests/` all **IDENTICAL** ⇒ the results are valid for the commissioned tree.

| Gate | Result | Exit | Counts | Runtime |
|---|---|---|---|---|
| G1 prompt-artifact baseline | **PASS** | 0 | **161/161 match, 0 stale** | <1 s |
| G2 seal tripwire | **PASS** | 0 | 1 passed / 0 failed, **0 stale pins** (35 pins) | 1.62 s |
| G3 chart-delivery audit | **PASS** | 0 | **46 passed / 0 failed / 0 deselected / 0 skipped** (confirmed via `--collect-only`: 46 collected) | 1.18 s |

- **G1** exact output: `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256`; stale list EMPTY. **Control from the tree root**: `sha256sum -c agents/.prompt-artifacts.sha256` → exit 0, **161 OK / 0 FAILED**. **CWD-trap demonstration**: the same command from inside `agents/` → exit 1, **0 OK / 161 "FAILED open or read"** — proving (not reproducing) the trap that makes the house script the authority.
- **G2** the phase-3 gate's **7 stale pins are RESOLVED** — zero stale mismatches now. Independently recomputed (not trusting the test's own green): **35 seal pins** (28 under `agents/`, 7 under `daemon/`) → 0 stale, 0 missing. **Fenced mutation control** (byte appended to `agents/leader/rule.md` in a `/tmp` mirror only) → gate exits **1** and **names the artifact** (`baseline=11affca67d4c… current=b109a0a4a3a1…`) ⇒ the tripwire is **armed**; the green is not a false-green. `agents/ari/workflow.md` actual sha `f31f5bc4fc38e2cb…` == its pin ⇒ the adjudicated tripwire re-pin (`14a7f3762`, `4ffa5bee` → `f31f5bc4`) is correctly reflected.
- No gate needed a database and none was touched (`ensemble_prod` / `ensemble_dev` never contacted).

### L7 — Tip-drift adjudication: BENIGN (staged v0.26.1 not invalidated; re-stage not required)

**The delta** `A=a4f28c26b` (build tree) → `B=efee25a27` (current `origin/latest`): 3 files, 341 insertions, 0 deletions — `A .agents/tester/LESSONS/2026-10-20-v0260-staged-verification-gotchas.md` (+106), `M .agents/tester/PACKS.md` (+16), `A .agents/tester/RESULTS/2026-10-20-v0260-staged-verification.md` (+219). `git diff --name-only A..B | grep -cv '^\.agents/'` → **0**. Per-path zero-changed-file proofs, one by one: `daemon/` **0** · `agents/` **0** · `plugins/` **0** · `frontend/` **0** · `scripts/` **0** · `tests/` **0** · `daemon/migrations/versions/` **0** · `pyproject.toml` **0** · `uv.lock` **0** · `config.yaml`/`launcher.sh`/`CHANGELOG.md`/`daemon/__init__.py` **0** each. Stronger than a diffstat — **git tree-sha identity per subtree**: `daemon/` `56b02bb0…`==, `agents/` `cf596338…`==, `plugins/` `30208ef1…`==, `frontend/` `1c18681a…`==, `scripts/` `b6270dfc…`==, `tests/` `881aa786…`== (only the whole-repo tree differs, because of `.agents/`).

**Ancestry:** `merge-base --is-ancestor A B` → rc 0; `merge-base A B` == A exactly; `B..A` EMPTY (linear, no divergence). All three B-side commits are `test(tester):` / `merge: tester/v0260-staged-verification` — the v0.26.0 evidence lane.

**Version identity:** `daemon/__init__.py` is the **identical blob** (`046d0718…`) at A and B → `__version__ = "0.26.1"` at both.

**Payload relevance:** `.agents/` inside the payload = **0** paths (find), 0 `.sql` anywhere in the payload; `.agents/` is absent from `ensemble.spec`'s `datas` (which is `{pyproject.toml, daemon/migrations, daemon/services/live_views/assets}` only) and contributes **0 bytes** to the binary (string probe: `.agents/`, `agents/tester`, `PACKS.md`, `RESULTS/2026-10-20`, the RESULTS filename → all **0** hits, with the positive controls firing: `daemon.services.board_service` → 1, `daemon.tools.board_tools` → 1, `daemon.services.board_view_service` → 1, `daemon.tools.board_link` → 1, `daemon/migrations` → 9). **All 5,361 manifest entries re-hashed against BOTH A and B — 0 mismatches vs either** ⇒ the payload is byte-reproducible from both revs. *Binary hash on a hypothetical rebuild: application inputs identical, but bit-identity is not guaranteed by construction (PyInstaller sets no `SOURCE_DATE_EPOCH` on this path) — inferred, not verified; moot because no rebuild is required.*

**Bonus shipped-binary proof:** `CArchiveReader.toc` over the staged `ensemble-prod` contains **106** `daemon/migrations/versions/*` entries **including `20261020_060000_create_board_tables.sql`** — our board migration is embedded in the shipped binary.

**Stage-guard consequence** (guard logic replicated read-only from `lib.sh:2753-2783` + `_tip_sha` `lib.sh:2263-2270` + `_refuse` `lib.sh:5325-5342`; `stage.sh` NOT executed): a re-stage from the staging worktree (HEAD = A) against tip B → **REFUSE mode, exit 78, token `non-tip-tree`** (the tip resolves via full refspec `refs/remotes/origin/latest`, so this is not WARN+SKIP). Escape hatches are operator-gated and journaled (`--allow-stale-stage` argv-only / `STAGE_FRESHNESS_OVERRIDE=1`) and **should not be used — the drift is inert**. The already-staged artifact is **not** invalidated: the L1/L2 freshness guards are **stage-time** checks; the artifact's own provenance binds `git_head=A` / `git_dirty=false` / `artifact_sha256=3bc32f60…` and that binding is intact (3-way match).

**Dup-prefix / registry invariant at B:** 106 files, **106 distinct** `YYYYMMDD_HHMMSS` prefixes at both A and B, `uniq -d` empty, prefix sets diff → IDENTICAL. Our board migration present at B, blob `f7033377…` == A, content sha `84c037f6…` == A.

> **Operational conclusion for the leader:** the staged v0.26.1 is **not** invalidated by the tip drift; **no re-stage is required**; a re-stage **would** be refused (exit 78 `non-tip-tree`). Proceed to the v0.26.1 promote ceremony **as staged**, subject to the remaining ceremony gates.

### L3 — Behavioural spot-checks at the exact build tree: PASS 4/4 (D1 closed)

**Method note (disclosed deviation, handled correctly):** the shared verification worktree had moved to the docs-only descendant `efee25a27`; rather than stop on the brief's stale sha assertion, the lane **proved the delta is zero for shipped code** (`git diff --name-only a4f28c26b..efee25a27 -- daemon/ agents/ plugins/ frontend/ scripts/` → EMPTY), then provisioned its own **exact-tip detached worktree** at `a4f28c26b` and proved `daemon.__file__` resolves inside it. Every probe below exercised `a4f28c26b` exactly. DB = disposable scratch `ensemble_tipv_v0261_tool` (role `ens_tipv_v0261`), dropped and **proven gone** (catalog 0/0 + reconnect `FATAL` both via TCP and unix socket); `ensemble_prod` never contacted, `ensemble_dev` never written.

**P1 — board tables + `board_list` (real tool, real PG).** Catalog query → `board_items` / `board_notes` / `board_item_tags`, all owner `ens_tipv_v0261`. Real factory `create_board_tools_if_available(manager, …)` → `["board_create","board_update","board_list","board_get"]`. Fresh board readback: `{"_pagination":{"applied_limit":50,"has_more":false,"returned":0},"count":0,"items":[]}` — the **global board envelope**, not an error, not an empty tool list. Real `board_create` → item `wi-d3a780e7` (`status:"todo"`, `priority:3`, tag `l3v0261`); `board_list` again → `count:1, returned:1` with the item present. Raw SQL second path corroborates both the `board_items` row and the `board_item_tags` row. `board_get("wi-d3a780e7")` → full item + `notes: []`.

**P2 — `self_alarm` 30-minute floor.** Real factory `create_self_alarm_tools_if_available(manager, …)` → `["self_alarm"]`, wrapping the **real** scheduling engine over the real `source_configs` table.
- **REJECT `in 29m`** — exact tool return: `ERROR: self_alarm requires a minimum horizon of 30 minutes — the requested fire time is 29m away (2026-10-20T21:54:15.343137+00:00). For anything shorter, use bash with `sleep <seconds>` inside the current turn instead of an alarm.` — the **bash-sleep hint** verbatim (`enforce_min_horizon`, family-aware). `source_configs` count **0 → 0 (delta 0)**: the floor is enforced **before** any engine call — no row, no label burn.
- **ACCEPT `in 30m`** (exactly at the floor): `{"ok":true, "source_id":"05c0da81-…", "label":"self-alarm:l3-v0261-exactly-30m", "recurrence":"once", …}` with `first_fire − armed` = **1800.000000 s exactly** (the relative grammar is computed against the tool's own clock and compared against the same `now`).
- **ACCEPT `in 45m`**: Δ = **2700 s**. Both rows read back with RAW SQL (`agent: "ari"`, `recurrence: "once"`, `instance_mode: "new_instance"`, `status:"running"`).
- Honest adapter-status note: the tool response said `status:"starting"` while raw SQL said `"running"` — the registry supervisor owns the transition asynchronously; the row is the evidence. Cleanup: 0 l3 rows remain; scratch DB dropped.

**P3 — `view_write` percent-encoded path (the D1 settlement).** Real factory `create_view_write_tools(manager, …)` → `["view_write"]`; `LiveViewsService` with a `/tmp` reports root; a **fresh local Fernet key** (not `SYSTEM_ENCRYPTION_KEY`), so the board-passcode singleton cannot apply; `ViewPasscodeService` over the same scratch PG.

`view_write(path="%2e%2e%2fx.html")` → exact return:
```
Error: percent-encoded path '%2e%2e%2fx.html' is not writable — the reports root stores plain paths, so '%' escapes are refused (nothing was written and no passcode was minted)
```

| Assertion | BEFORE | AFTER | Result |
|---|---|---|---|
| live-view root file listing | `[]` | `[]` | **no file written** |
| `live_view_passcodes` (total) | `0` | `0` | **no row minted** |
| `live_view_passcodes` (root=`reports`) | `0` | `0` | **no row minted** |

The D1 on-disk signature is **absent**: `…/reports/%2e%2e%2fx.html` `exists() == False`, and a recursive `%2e`-name scan of the whole scratch tree returns `[]`. **Positive control** `ok.html` → success envelope (`Public URL: /views/public/reports/ok.html`, `Passcode: pt27ay`, `Generation: 1`, `Bytes: 45`), file listing `[] → ["ok.html"]`, on-disk bytes verbatim, passcode rows `0 → 1` ⇒ the probes are **provably non-vacuous**. *(Probe-side correction disclosed: the first assertion expected a dict success envelope; the tool's real envelope is a multi-line string — an assertion defect in the probe, not the tool; corrected and the whole suite re-run clean.)*

> **D1 BEHAVIORAL RESULT — CLOSED.** `view_write` **rejects** the percent-encoded path, with **no file written and no passcode row minted**, while the positive control succeeds and mints exactly one row. The guard is a fail-closed whole-class `%`-kill, not a decode-then-revalidate. The "D1 ships as-is" line carried in the v0.26.0 gate report is a **stale-context echo** and is retracted.

**P4 — shipped-binary proof.** `strings`-probe of `releases/v0.26.1/ensemble-prod` (read-only; sha256 identical before/after): all 9 ari-tools-specific module-path identifiers (`daemon.services.board_service`, `board_view_service`, `board_view_pages`, `self_alarm_service`, `daemon.tools.board_tools`, `board_link`, `self_alarm`, `view_write`, `daemon.models.board`) read **1** in v0.26.1 and **0** in the v0.25.7 binary (the old-binary arm proves the hits are caused by *this feature shipping*); the 3 pre-existing live-views ids read 1 in both; the negative control (`daemon.services.totally_absent_module_zzz_l3_control`) reads **0** in both ⇒ the scan has discriminating power. (Honest note: `strings` on a PyInstaller binary is a *positive* proof of presence, not an absence oracle — corroborated by L7's independent `CArchiveReader.toc` finding all **106** migration entries including our board migration embedded in the shipped binary.)

### W-C — Isolated verification worktree: PASS

| item | value |
|---|---|
| worktree | `/home/nea/ensemble-src-wt-tester-v0261` on branch `tester/v0261-tip-verification` |
| HEAD | `efee25a2796ebe63b7f64118b1758cb798e5c548` (actual `origin/latest`; the brief's `a4f28c26b` was **stale**) |
| tree | clean (`status --porcelain -uall` empty), 0 commits ahead of tip |
| venv | own `uv sync --frozen` (rc=0, lock untouched) · Python 3.14.7 · pytest 9.0.2 |
| editable-install proof | `daemon.__file__` → `/home/nea/ensemble-src-wt-tester-v0261/daemon/__init__.py` — **inside** the worktree (the main checkout's `.pth` was never reused) |
| import proof | `daemon.api` imports OK under a **full ambient-env scrub**, with a `socket.connect` guard proving **zero** outbound connects; no port bound |

**Tip-drift adjudication (the lane's loud disclosure).** `origin/latest` advanced `a4f28c26b` → `efee25a27`; `git merge-base --is-ancestor a4f28c26b efee25a27` = rc 0 (strict descendant). The 3 intervening commits are the v0.26.0 staged-verification evidence merge (`merge: tester/v0260-staged-verification — v0.26.0 staged independent verification (8/8)` + two `test(tester):` commits). Delta: **3 files / 341 insertions, documentation only** — `daemon` 0 · `agents` 0 · `plugins` 0 · `frontend` 0 · `tests` 0 · `scripts` 0 · `migrations` 0. Both shas carry `__version__ = 0.26.1`. The verification lanes therefore ran against a tree that is **code-identical** to the v0.26.1 build tree. (L7 adjudicates the ceremony consequence.)

## 5. Disclosures

1. **D1 (L1 item 9) — the staging worktree is not pristine.** `git status --porcelain -uall` in `/home/nea/ensemble-src-wt-stage-v0261` shows **one untracked path**: `.agents/tester/RESULTS/2026-10-20-v0261-staged-verification.md`, authored by the **concurrent tester lane** ("Staged Final Gate — v0.26.1") at 20:59:50Z — i.e. **31 minutes after** the sidecar's `build_at 20:28:15Z` stamp. It is **not** inside any payload subtree (the payload excludes `.agents/` entirely), **no tracked file is modified**, and the value was byte-identical before and after L1's run ⇒ **no artifact impact**. PASS-with-disclosure, not a defect.
2. **D2 — sidecars live only in the staging worktree**, not in the payload (by design). A post-hoc verifier without the worktree would be reduced to exit-0 entailment + git corroboration — the known *stage-provenance silent-on-pass* risk. Same shape as v0.26.0, so not a v0.26.1 regression.
3. **D3 — v0.26.1's three payload trees are byte-identical to v0.26.0's** (`ec9d3179…` / `4715cf64…` / `8d6afeaa…`) while the **binary differs** (`5445da08…` → `3bc32f60…`). Consistent with the delta being daemon-side only: the ari-tools **prompt content was already carried by v0.26.0**; v0.26.1 adds the foreign lane's compiled code.
4. **D4 — the pin-set label** in the phase-3 record is imprecise (5 exact-set + 2 count, not 7 exact-set) — see §L2a.
5. **D5 — tip drift** (see §W-C / §L7): the staged v0.26.1 was built from a tree that is now 3 docs-only commits behind `origin/latest`.

## 6. Action needed (for the leader)

- [x] **Promote path is clear for OUR feature.** `releases/v0.26.1` carries the ari-tools feature complete and intact. Verdict: **TIP-VERIFIED (ari-tools intact in v0.26.1)**. Nothing of ours would need to ship differently.
- [x] **D1 adjudication is resolved in favour of "already fixed, behaviorally proven"** — the phase-3 LOW defect does NOT ride into v0.26.1. The "D1 ships as-is" line in the v0.26.0 gate report is a stale-context echo (the fix `c903fef8` sits inside merge `bfeca369d`, i.e. inside BASE `14a7f3762`) and is retracted with behavioral evidence (§L3 P3).
- [x] **No re-stage needed.** The tip drift (`a4f28c26b` → `efee25a27`) is docs-only and `.agents/`-confined; every shipped subtree is tree-sha identical; a re-stage would be REFUSED (exit 78 `non-tip-tree`). Promote the staged artifact as-is.
- [x] **No action on the inherited maintenancer 3-red** — already routed to its owning lane (latest-side pin-file fix); reproduced here exactly (3F/10P), proven inherited with the pin file *and* the pinned daemon code unchanged across the foreign lane and the granting commit `ff27305c4` predating our base.
- [ ] **Leader-side bookkeeping:** the `wt.claim.ari-tools` KV `phase3_status` should note that v0.26.1 now carries the feature and has passed this scoped tip-verification gate (the concurrent lane's own staged gates cover its own feature).
- [ ] Optional follow-up (nice-to-have, not blocking): the *stage-provenance silent-on-pass* gap (disclosure D2) is the third recurrence across v0.25.2 → v0.26.0 → v0.26.1; a `_log provenance verified head=… dirty=… sha=…` line on the pass path would close it.

## 7. What would have to ship if this had FAILED (for completeness)

Nothing — no condition of this verdict requires a code change. The only failure modes found were harness-side (L5 round 1's missing `SYSTEM_ENCRYPTION_KEY`; L3's first envelope-shape assertion) and both were repaired within the lanes with disclosed corrections; neither touches the artifact.

## 8. Lanes and skills (attribution)

| Lane | Instance | Skill (`load_skill`) | Result |
|---|---|---|---|
| L1 payload integrity | `e20d4997` | `independent-staged-release-integrity-verification` | PASS 9/9 |
| L6 delta archaeology | `d80ef2e3` | `git-archaeology-invariant-verification` | PASS |
| W-C worktree/venv bootstrap | `3427b1ac` | `merged-tree-functional-round-bootstrap` (+2) | PASS |
| L5 staged-binary boot | `cceb234a` | `staged-binary-boot-smoke` | PASS 11/11 |
| L2a privileged forensics | `544a3759` | `ari-tools-privileged-pin-ceremony-forensics` | PASS 7/7 |
| L2b migration/prompt/re-stamp | `f311d9aa` | none (static, git-object anchored) | PASS 13/13 |
| L4 seal gates | `14ec8c6c` | `test-pack-execution` | PASS 3/3 |
| L4b corpus re-run | `b4d5e6a4` | `test-pack-execution` | PASS 431/434 (3 inherited) |
| L3 tool behaviour | `2d1f32ce` | `real-engine-real-pg-tool-verification` | PASS 4/4 (D1 closed) |
| L7 tip-drift adjudication | `9cfda79e` | `remote-tip-drift-adjudication` | BENIGN |

Evidence artifacts: `/tmp/l3_v0261_run/probe_out.txt` + `l3_probe.py` (L3), `/tmp/l4b/P1..P6.log` + `RESULTS.md` (L4b), `/tmp/l4-seal-gates/` (L4), `/tmp/l2b-scratch/` (L2b). No tester-side code changes; nothing committed outside `tester/v0261-tip-verification`.
