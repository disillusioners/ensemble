# Ceremony Notes — v0.29.18 (prompt-assent: The Emperor's Word / Assent)

**Prepared by:** devops · **Cut date:** 2026-11-11 · **Status:** STAGED, ceremony-ready.
**NOT promoted. NOT armed. NOT restarted.** `current` still points at `releases/v0.29.15`.

---

## The era line

> **v0.29.18 — the Emperor's Word.** The assent lane ships: the working path is
> conversation, and a relayed word with provenance is a first-class approval lane.
> The lane is **dormant** until the Emperor arms it — one env var, no code change.

## Staged version

| Field | Value |
|---|---|
| `staged_version` | **v0.29.18** |
| `binary_version` (self-report) | `0.29.18` |
| `staged_at` | `2026-11-11T09:00:12Z` |
| release dir | `/home/nea/agents-ensemble/releases/v0.29.18` |
| built from commit | **`df4a92732188ed7465673ca491f25975a0a60846`** (`docs(changelog): [0.29.18] dated section …`) |
| tagged | **`v0.29.18`** (annotated `a4d7233f2c765dc58b3bf0796c3083b6b74dd67d` → `df4a92732`) — local **and** pushed to origin by name |
| worktree | `/home/nea/ensemble-src-wt-relcut-prompt-assent` (detached, clean) |

## The promote command

```bash
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.29.18 bash scripts/upgrade/promote.sh live --f2-verified-closed
```

**CWD selects the executing script.** Run it either from the **scripts-pin worktree**
(`/home/nea/ensemble-src-wt-scripts-pin`) or from the **stage worktree**
(`/home/nea/ensemble-src-wt-relcut-prompt-assent`). The live `.env` already carries
`ENSEMBLE_UPGRADE_SCRIPTS_DIR=/home/nea/ensemble-src-wt-scripts-pin/scripts/upgrade`.

## Ceremony targets — v0.29.18 ONLY

**Do NOT promote v0.29.15, v0.29.16 or v0.29.17.** v0.29.18 **supersedes** all three
pending ceremonies: their content is already inside this tree.

How that was verified (not asserted):

1. **Git ancestry** — `git merge-base --is-ancestor <tag> HEAD` returns 0 for
   `v0.29.15` (`0efdf106f`), `v0.29.16` (`ef8eda0eb`) and `v0.29.17` (`dd57cc7d9`).
2. **Payload superset** — per-file manifest comparison of the staged release dirs:
   - `v0.29.15 → v0.29.18`: agents 379 base / **0 removed** / 8 changed / 0 added;
     frontend 95 / **0 removed** / 0 changed; plugins 4930 / **0 removed** / 0 changed.
   - `v0.29.17 → v0.29.18`: agents 379 / **0 removed** / **1 changed** / 0 added;
     frontend 95 / 0 removed / 0 changed; plugins 4930 / 0 removed / 0 changed.
   - `config_sha256`, `launcher_sha256` and `pipeline_scripts_floor_commit` are
     **byte-identical** across all three.
   - Source level: `git diff --diff-filter=D v0.29.15 HEAD` over
     `daemon agents frontend plugins config.yaml launcher.sh scripts` = **0 deletions**.
3. The 8 changed agent files between v0.29.15 and v0.29.18 are:
   `.prompt-artifacts.sha256`, `ari/meta.json`, `designer/tools_note.md`,
   `developer/tools_note.md`, `image-reader/rule.md`, `image-reader/soul.md`,
   `leader/tools_note.md`, `worker/tools_note.md`.
   The single file changed between v0.29.17 and v0.29.18 is `ari/meta.json`
   (the `tools.allow` grant of `prompt-assent`).

v0.29.16 has **no release dir** on this host (never staged). v0.29.17 **is** staged and is
now redundant — it is an eviction candidate after the promote.

## Ordinals (re-derived mechanically at `df4a92732`)

| Ordinal | Before | **v0.29.18** | How verified |
|---|---|---|---|
| Privileged tool categories | 20 | **21** | runtime `len(PRIVILEGED_TOOL_CATEGORIES)` == 21; `prompt-assent` is a member (ari-exactly) |
| Tool-category vocabulary (`VOCABULARY_VERSION`) | 8 | **9** | `daemon/checkpoint_metrics.py:VOCABULARY_VERSION = 9`, docstring entry **9** records 62 → 63 |
| Derived artifact `.agents/shared/metrics/tool-categories.json` | 62 entries | **63 entries** | file's own `count: 63`, `vocabulary_version: 9` |
| Prompt-artifact baseline | 181 | **181 (unchanged)** | `agents/.prompt-artifacts.sha256` = 181 rows at v0.29.15 **and** at HEAD; stage's own drift leg printed `PROMPT-BASELINE: OK — 181 versioned artifact(s)` |
| Migrations (`daemon/migrations/versions/*.sql`) | 127 | **128** | `git ls-tree` count 127 at v0.29.15/v0.29.17 → 128 at HEAD |
| Migration head | `20261109_021154_create_memoir_v2_tables.sql` | **`20261110_221500_create_prompt_assent_tokens.sql`** | `manifest.known_schema_gen` + sorted tail |

`agents/ari/meta.json` grants `"prompt-assent"` in `tools.allow`; **no other agent** does
(the ari-EXACTLY posture). `prompt_assent` is **not** an od.* tool and holds no build
authority of its own.

## Binary sha256 — 4-way

**`aa78f632c4529f9c883647cae5d4743f3e52199ca6dec39e5bf5abdbcabf3e8e`**

| Leg | Value |
|---|---|
| release dir `releases/v0.29.18/ensemble-prod` | `aa78f632c4529f9c…bf3e8e` |
| build output `dist/ensemble-prod` | `aa78f632c4529f9c…bf3e8e` |
| `manifest.json.binary_sha256` | `aa78f632c4529f9c…bf3e8e` |
| `dist/ensemble-prod.build-provenance.json.artifact_sha256` | `aa78f632c4529f9c…bf3e8e` |

All four legs equal (`sha256sum` + manifest read + sidecar read). Provenance sidecar:
`git_head=df4a92732188ed7465673ca491f25975a0a60846`, `git_dirty=false`,
`build_tool=pyinstaller:ensemble.spec`, `build_at=2026-11-11T08:58:40Z`.

Other manifest checksums (all re-verified against the staged bytes):

- `launcher_sha256` = `6723d1b82b23f75ce6b89cb6338fb3a9a55a482bb3acd9c1620b85d47a856593` (identical to v0.29.15/.17)
- `config_sha256` = `25f3d595787ea94652da8ac7fbfec4d5a852bf10a543b6d0b6fdcf0b000be055` (identical to v0.29.15/.17)
- `agents_tree_sha256` = `6bb8b91d2503cb1d678f5671fe5852d9e25c03f8e0b6a921d94ff0fe91ff2742` (379 per-file entries)
- `frontend_tree_sha256` = `f4e344acf497293da156b8c1315cd1da5e7d930e02bf38aaa967eb7fefe239ba` (**identical to v0.29.17** → FE byte-identity holds; 95 entries)
- `plugins_tree_sha256` = `4715cf64dad62d209c91206c560e475d7fa4cec8f67f6182ffbcd21b723685a7` (identical to v0.29.17; 4930 entries)
- Per-file integrity: **agents 379/379, frontend 95/95, plugins 4930/4930 — 0 mismatches** (independent Python re-hash of every staged byte).
- `contains_contract_phase: true` (full-history destructive-DDL grep), **`rollback_safe: true`**, `rollback_safe_source = explicit=true` (`ENSEMBLE_ROLLBACK_SAFE=1`).
- FE sidecar: `frontend/dist/frontend/browser/index.html.build-provenance.json`,
  `artifact_sha256=15866811f684c88b788469cafb55e2d38b8e4cedd2fcee8355481cbd143dd32f`,
  `git_dirty=false`, `git_head=df4a92732…`.

## Tag state

| Ref | Value |
|---|---|
| local `refs/tags/v0.29.18` | annotated tag `a4d7233f2c765dc58b3bf0796c3083b6b74dd67d` → commits `df4a92732` |
| origin `refs/tags/v0.29.18` | `a4d7233f2c765dc58b3bf0796c3083b6b74dd67d` (peeled `df4a92732…`) |
| pushed how | `git push origin refs/tags/v0.29.18:refs/tags/v0.29.18` — **by name**, never `--tags` (the local-only `v0.29.6` shadow makes `--tags`/`--prune` fail permanently on this host) |
| `git describe --tags --exact-match HEAD` | `v0.29.18` |

`v0.29.16` — the tag that was missing locally and recovered in the previous pass — is
present locally **and** on origin (`f0a619f53` → `ef8eda0eb`), for the audit trail only.
It is **not** a promote candidate (no release dir).

## Live untouched — verified facts

| Check | Baseline (before cut) | After the cut |
|---|---|---|
| `current` symlink | `releases/v0.29.15` | **`releases/v0.29.15`** (unchanged, mtime Nov 10 18:12) |
| `/livez` | `{"status":"alive","version":"0.29.15"}` | **`{"status":"alive","version":"0.29.15"}`**, uptime continuous (47666 s → 48721 s, monotonic — no restart) |
| `/readyz` | 200 | **200** |
| `ensemble-main.service` MainPID | `2390156` | **`2390156`** |
| `NRestarts` | `0` | **`0`** |
| `ExecMainStartTimestamp` | `Tue 2026-11-10 19:37:12 UTC` | **unchanged** |
| `ActiveState` | `active` | **`active`** |
| listener :9797 | pid `2390250` | **pid `2390250`** (same process) |
| `releases/state.json` sha256 | `dc3f490cd4097216f130a2964e67b7a61627ec3a661a114fdb5bc3790e5ba2d0` | **byte-identical** — promote fields untouched: `current=v0.29.15`, `previous=v0.29.7`, `in_flight=null`, `pending_op=null`, `pending_restart=null`, `history` still 214 rows |

**No restart was performed anywhere.** `docker`/`kubectl`/`terraform` were not involved.

**One live-install write happened, by machinery design, and is disclosed:** `stage.sh`
re-stages the `ENSEMBLE_SELF_ENV=live` marker into `/home/nea/agents-ensemble/.env`
(`sed` line rewrite → temp file → `mv` → `chmod 600`). The file remains **mode 600**,
same size (5579 bytes), exactly one `ENSEMBLE_SELF_ENV=` line, value `live` — content is
semantically unchanged (inode/mtime moved). No secret was read, echoed or logged.

## Preconditions — ARM-CLEAN

| Gate | Value | Verdict |
|---|---|---|
| daemon-side floor `PIPELINE_SCRIPTS_FLOOR_COMMIT` | `2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa` | is a **commit** in the tree, **ancestor of the built tree** `df4a92732` ✔ |
| pinned-scripts stamp `PIPELINE_SCRIPTS_REV` (lib.sh) | `2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa` | **equals the floor** (D6-8), resolves in the tree ✔ |
| arm-time guard (floor ⊂ pinned scripts dir HEAD) | scripts-pin HEAD `84df6b55c` | `git merge-base --is-ancestor` → **0** ⇒ **ARM-CLEAN** |
| stop-script sibling (v0.29.16 PR1 gate) | `…-wt-scripts-pin/scripts/stop-ensemble.sh` | exists, mode `755`, tracked as `100755` ✔ |
| scripts-pin worktree cleanliness | `git status --porcelain` = 0 lines | clean ✔ |
| stage-time validation | stage.sh printed both | `floor validated … is an ancestor of the built tree (T6.15)` and `stamp validated … == the floor (W-2/T6.4/D6-8)` ✔ |

**No gap. Nothing was force-fixed.**

## ROLLBACK SAFETY — the evidence actually checked

`rollback_safe = true`, source `explicit=true` (`ENSEMBLE_ROLLBACK_SAFE=1`; the stage
script's auto-derivation refuses when a full-history DDL grep matches, and this tree's
migration history does contain `DROP TABLE` in **older** files' DOWN sections).

What was checked:

1. **Migration delta `v0.29.15 → HEAD` is exactly ONE file, ADDED:**
   `A daemon/migrations/versions/20261110_221500_create_prompt_assent_tokens.sql`.
2. **The UP section is purely additive:** `CREATE TABLE IF NOT EXISTS prompt_assent_tokens`
   + 3 `CREATE ... INDEX IF NOT EXISTS`. Zero `DROP`, zero `ALTER`, zero `REFERENCES`.
   (The `DROP TABLE` / `DROP INDEX` statements live **only** in the `-- DOWN` section.)
3. **No hard FK:** `version_id` is an indexed SOFT reference to `prompt_versions`; the
   model file carries **zero `ForeignKey` fields** (its only `ForeignKey` hit is docstring
   prose explaining *why* there is none).
4. **`_ensure_postgres_columns` is untouched:** `grep -c prompt_assent daemon/manager.py` = 0;
   the PG schema comes from `SQLModel.metadata.create_all` via the N1 seam. The boot-abort
   surface is unchanged.
5. **The old binary references no new table:** `git grep -l prompt_assent v0.29.15 -- daemon/ agents/`
   → **0 files**. A rollback to v0.29.15 runs code that never touches
   `prompt_assent_tokens`; the extra table simply sits unused.
6. **The new lane is DEFAULT-OFF and therefore INERT after a promote:**
   `PROMPT_ASSENT_DEFAULT_ENABLED: bool = False` (`daemon/config.py`), consumed by BOTH
   the config class and the tool's call-time resolver. With
   `ENSEMBLE_PROMPT_ASSENT_ENABLED` unset, `prompt_assent` refuses `lane-disabled`
   (and `mint` is refused too). Tool registration alone changes nothing.

**Consequence:** a promote of v0.29.18 followed by a rollback to v0.29.15 loses **nothing**
but the (unused) table and the option to use the lane — hence *fix-loss-only*.

## Eviction report

Release dirs on the host, newest `staged_at` first:

| Dir | `staged_at` | Status |
|---|---|---|
| `v0.29.18` | `2026-11-11T09:00:12Z` | **TARGET — protect** |
| `v0.29.17` | `2026-11-11T07:03:51Z` | eviction candidate #1 (content ⊂ v0.29.18) |
| `v0.29.15` | `2026-11-10T18:05:29Z` | **LIVE — protect** (`current`) + it is the rollback target |
| `v0.29.13` | `2026-11-10T13:36:07Z` | eviction candidate #2 |
| `v0.29.7` | `2026-11-09T03:39:30Z` | pinned by `state.json.previous` — retention **never** evicts `previous` |

- **Count: 5** release dirs vs `RETENTION_KEEP=3` (`scripts/upgrade/lib.sh:375`).
- **Stage did NOT evict anything** — `retention_evict` is called only from
  `promote.sh` (`:771` post-commit, `:927` final), never from `stage.sh`. Verified: all
  four pre-existing dirs are intact and `releases/state.json` is byte-identical.
- **At promote time** the machinery will evict down to 3 total, pinning `current` and
  `previous`. With `current=v0.29.15` / `previous=v0.29.7` at the start of the ceremony and
  the post-promote `current=v0.29.18` / `previous=v0.29.15`, the two unprotected dirs are
  **v0.29.17** and **v0.29.13** (oldest-first by `staged_at`). Expect `v0.29.7`,
  `v0.29.13` and/or `v0.29.17` to be swept depending on the final pin set — **nothing was
  evicted by hand.**
- Over-retention is a known house condition (5 vs keep-3); do not hand-prune before the
  ceremony — the live dir is `current`'s target and the promote path owns eviction.

## Ceremony WARNINGS

1. **Cumulative content — this release is NOT only prompt-assent.** +22 commits from
   another lane ride it (skill-revival Phase 3 prod wiring, image-tools unconfined
   local-path read with its **caller-visible `workdir_*` → `path_*` envelope rename**,
   upgrade arm-preflight stop-script gate, two version bumps). A consumer that
   string-matches `compare_images` envelopes must update before upgrading.
2. **Three branch subjects carry the `prompt-assist` slug typo** (not renamed; shas
   listed for the record): `bfb8c22ca` `refactor(prompt-assist): route both inlined
   digest-rule sites through the render_sha256 wrapper (S-1)`, `5bbd57c9e`
   `docs(prompt-assist): align the stale privileged-category census prose with the
   21-entry pins (S-2)`, `029416f2e` `docs(prompt-assent): …` (assent-spelled subject,
   assist-spelled slug in the other two). Cosmetic only — the code's own retired-literal
   pin forbids the `PromptAssist` **stem** in the tree, and that pin is green.
3. **The §16 tester lane record was carried onto latest as `1616ee8d1` (a cherry-pick).**
   The feature branch tip `a4d32e611` is deliberately **NOT** an ancestor of `latest`
   (`git merge-base --is-ancestor` → 1). Equivalence was proven by `git patch-id --stable`:
   both commits yield **`fc59b9af95d0227e31e78610770685817ec1232b`**. Do not treat the
   branch tip as missing content.
4. **`v0.29.16`'s tag was missing locally and was recovered in the previous pass**; it is
   now on origin as well (`f0a619f53` → `ef8eda0eb`). It has **no release dir** and is
   **not** a ceremony target.
5. **Eviction candidates**: `v0.29.17`, `v0.29.13` (+ `v0.29.7` if `previous` moves).
   Protectors: `v0.29.18` (target), `v0.29.15` (live + rollback target).
6. **K-3 FLIP-ON GATE — arming is BLOCKED until the live Discord probe is run.** AR-1
   leg 2 classifies a channel private as `is_dm AND NOT is_group_dm`, fail-closed on an
   absent/unparseable discriminator. The adapter-side `is_group_dm` discriminator is
   **unit-proven only**; the live confirmation is outstanding. Procedure
   (`docs/runbooks/prompt-assent.md` §"The K-3 live confirmation"): arm the lane, mint from
   a true 1:1 DM (**expect a mint response**), then mint from a group DM of ≤10 recipients
   including one other human (**expect `mint-requires-private-channel`** and **no token
   row**). Record both outcomes before relying on the lane. A miss is bounded to
   *availability* (the lane refuses), never to authority.
   The kill-switch is **default OFF** and **invalid values fail closed AT BOOT, not at call
   time** (the `PromptAssentConfig` pydantic `bool` field is validated when
   `load_config()` runs) — a typo'd value is a boot-time refusal, so verify the value
   after any manual `.env` edit.
7. **PG server-side-defaults gap (disclosed, not fixed).** The `prompt_assent_tokens`
   model declares **client-side** defaults only (`version_tag=""`, `mint_is_dm=0`,
   `attempts=0`, `status='live'`); there is **no `server_default`** on the PG side. The
   ORM is unaffected (SQLModel fills them). A **raw-SQL INSERT that omits those columns
   hits `NOT NULL` on PostgreSQL**. The SQLite `.sql` file does carry `DEFAULT` clauses —
   so the two drivers differ here by construction. Do not write raw INSERTs against this
   table.
8. **Freshness-leg flake (~13–20%, deferred fix).** `test_prompt_assent_freshness.py`'s
   positive leg is wall-clock flaky (`now_iso()` truncation race) — measured 2 of 6 runs
   in the gate lane; it reddens pack attempts and passes on retry with no code change.
   The durable backdate-widening fix is **deferred**; expect spurious reds from it.
9. **Pre-existing reds that ship with this release** (identical on both legs of the
   tester's net-new comparison — *not* introduced here):
   - **7 failures** in `tests/unit/test_anomaly_report_duty_prompts.py` — re-confirmed on
     the shipped tree at `df4a92732` (`7 failed, 12 passed` in 1.46 s). Root: the file's
     `BASELINE_EXPECTED_ENTRIES = 177` vs the actual **181**-row prompt-artifact baseline,
     plus the derived duty-walk/cardinality expectations. The lane touched only this
     file's `SEAL_REPINNED_BASE_VALUES` map (N-1 fix, `f5b51cf99`) — the numbers were
     already stale before it.
   - **2 migration-census pins** pinned at the OLD counts, re-confirmed red:
     `tests/test_prompt_version_resolution_core.py::TestMigrationParity::test_migration_files_present`
     → `assert 128 == 96`; and
     `tests/unit/test_anomaly_report_models.py::TestTheMigrationCensus::test_the_census_is_121_files_with_121_distinct_prefixes`
     → `assert 128 == 121`. Neither file was modified by this lane.
   The tester's verdict on the merged tree is **GO-WITH-DISCLOSURES** (packs 249 + 107 +
   memoir 28 green, mutation teeth intact).
10. **The DOCTRINE, verbatim** (`.agents/shared/planning/prompt-assent/doctrine.md`):

    > **FE-UI is a deeper view; the working path is conversation. The Emperor's word,
    > relayed by C2 with provenance, is a first-class approval lane.**

    "FE-UI is a deeper view" is *not* "the FE goes away" — the FE approval lane remains.
11. **Follow-up commissions (not blockers):**
    (a) a **dev-only Discord-shaped CI stub** so the K-3 discriminator has a permanent
    regression surface; (b) a **migration-census hygiene lane** — the `== 96` / `== 121`
    pins and the `= 177` baseline count are single-lane-owned global facts that break on
    every new lane (re-express them as deltas, as the memoir lane already learned);
    (c) the **duty-block reds** (item 9) deserve their own lane — they are unrelated to
    assent.
12. **CHANGELOG note.** The bump commit did not create the dated section; this cut added
    `## [0.29.18] — 2026-11-11` as a docs-only commit (`df4a92732`) and **relocated** the
    prompt-assent entry there verbatim (it had landed under the `[0.29.16]` heading via a
    two-branch merge race: `ef8eda0eb` and `4fea841e7` each inserted a dated section
    against the same base blob `c6164a574`). Diff = 11 insertions / 9 deletions, added and
    removed line sets identical apart from the heading. `CHANGELOG.md` is not part of the
    staged payload, so no payload byte depends on it.

---

## Quick reference card for the ceremony

```
staged_version   v0.29.18
built from       df4a92732188ed7465673ca491f25975a0a60846
tag              v0.29.18  (annotated a4d7233f2 → df4a92732; local + origin)
binary sha256    aa78f632c4529f9c883647cae5d4743f3e52199ca6dec39e5bf5abdbcabf3e8e
floor            2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa   (ARM-CLEAN, scripts-pin @ 84df6b55c)
migration head   20261110_221500_create_prompt_assent_tokens.sql   (128 files)
rollback_safe    true  (explicit; additive table, no FK, old binary blind to it)
lane state       DEFAULT-OFF — inert until ENSEMBLE_PROMPT_ASSENT_ENABLED=1
armed by         the Emperor, after the K-3 live DM probe — not before
live now         v0.29.15 @ :9797, MainPID 2390156, NRestarts 0, state.json byte-identical
```
