# Release-Payload Seam Research — docs/guidelines → releases/<v>/guidelines/

Worktree: `/home/nea/ensemble-src-wt-guideline-read-tool` (branch `feature/guideline-read-tool` @ 5316e0cd3).
All line numbers below are **from this worktree** (v0.30.2-era tree; the commission's ~:NNN guesses from v0.28.11 are stale — corrected here).

`docs/guidelines/` exists at this base: `README.md`, `TEMPLATE.md`, `guideline-live-views-passcode.md`, `guideline-memoir-reflection-lane.md`, `guideline-notify-user-bounds.md` (5 files).

---

## A. stage.sh insertion points

### A.1 Current payload copy mechanics (scripts/upgrade/stage.sh)

Assembly happens into a temp dir then swap-in (`STAGE_TMP="$INSTALL_DIR/releases/.staging.$VERSION.$$"`, :432-435):

| Payload | Anchor | Quote |
|---|---|---|
| binary | :438 | `cp "$BINARY_SRC" "$STAGE_TMP/ensemble-prod"` |
| agents/ tree | :440 | `cp -R "$REPO_ROOT/agents" "$STAGE_TMP/agents"` |
| frontend dist | :441-442 | `mkdir -p "$STAGE_TMP/frontend/dist/frontend"` + `cp -R "$REPO_ROOT/frontend/dist/frontend/browser" "$STAGE_TMP/frontend/dist/frontend/browser"` |
| plugins/ tree | :450 | `cp -R "$REPO_ROOT/plugins" "$STAGE_TMP/plugins"` — mirrors agents/FE pattern; precondition guard at :318-321 refuses stage if `$REPO_ROOT/plugins` missing (exit 78, v0.18.0 regression rationale) |
| FE sidecar strip | :461-465 | `rm -f "$STAGE_TMP/frontend/dist/frontend/browser/index.html.build-provenance.json"` (build-time state never ships) |
| config.yaml | :466 | `cp "$REPO_ROOT/config.yaml" "$STAGE_TMP/config.yaml"` |
| launcher.sh | :467-468 | `cp "$REPO_ROOT/launcher.sh" "$STAGE_TMP/launcher.sh"` + chmod |
| executor script family | :484-488 | `executor_family_install "$REPO_ROOT" "$STAGE_TMP"` (from scripts/upgrade/executor_family.sh) |
| no-.env invariant | :493-496 | `_no_env_in_release "$STAGE_TMP"` → exit 1 |

**stage.sh copies NO docs/ tree today — confirmed.** The only staged sources are: binary, `agents/`, `frontend/dist/frontend/browser/`, `plugins/`, `config.yaml`, `launcher.sh`, and the executor-family files. `docs/` is never touched.

### A.2 Manifest build (:498-723)

- Per-file sha256 scalars: :500-502 (`BIN_SHA`, `LAUNCHER_SHA`, `CONFIG_SHA` via `_sha256`).
- One bounded walk per tree feeds BOTH aggregate + per-file map: :507-509
  `AGENTS_LINES="$(_stage_tree_walk agents "$STAGE_TMP/agents")"` (same for frontend, plugins).
- Aggregates: :510-512 `AGENTS_TREE="$(_tree_hash_of_lines "$AGENTS_LINES")"`.
- Executor family: :521-522 `SCRIPTS_LINES="$(executor_family_manifest_lines "$STAGE_TMP/scripts")"` / `SCRIPTS_TREE="$(executor_family_tree_hash ...)"` — byte-compatible with `_tree_manifest` (same find|sort order, same `shasum -a 256`, same two-space separator; comment :513-520).
- Per-file maps (linear streaming, 2026-10-07 O(n²) fix): `_stage_join_map` python one-pass :539-548; frag-loop pattern per tree :549-559 (agents), :560-570 (frontend), :577-587 (plugins), :593-603 (scripts). Each entry: `printf '"%s":"%s"\n' "$(_json_escape "$rel")" "$sha"` parsed from `"sha␠␠rel"` walk lines.
- `STAGED_AT` fresh per stage :605-625.
- Floor/stamp/coverage gates :627-699 (see §E).
- **Manifest heredoc :701-723**: identity fields (:703-710) + `pipeline_scripts_floor_commit`/`pipeline_scripts_rev` (:706-707) + `known_schema_gen`/`contains_contract_phase`/`rollback_safe` (:708-710) + `launcher_sha256`/`binary_sha256`/`config_sha256` (:711-713) + tree shas `agents_tree_sha256`/`frontend_tree_sha256`/`plugins_tree_sha256`/`scripts_tree_sha256` (:714-717) + maps `agents_manifest`/`frontend_manifest`/`plugins_manifest`/`scripts_manifest` (:718-721).
- Post-stage integrity: `integrity_verify ".staging.$VERSION.$$"` :729-733; **local fail-closed re-hash legs for trees lib.sh does NOT verify**: plugins :747-753, executor family :760-767 (each refuses the swap-in on aggregate mismatch).
- Swap-in rename-aside :769+.

### A.3 Exact insertion points for the feature

1. **(i) Copy `docs/guidelines/` → `releases/<v>/guidelines/`**
   - Precondition guard: next to the plugins guard :318-321 — `if [ ! -d "$REPO_ROOT/docs/guidelines" ]; then _warn ...; exit 78; fi` (fail-closed, same exit-78 family).
   - Copy: after :450 (plugins copy), before :484 (`executor_family_install`): `cp -R "$REPO_ROOT/docs/guidelines" "$STAGE_TMP/guidelines"` — mirrors the plugins/verbatim copy; comment should cite the plugins precedent (:443-449).
   - Note: `_no_env_in_release` (:493) walks the whole staged tree — .md-only content is safe. No FE-sidecar-style strip needed (docs/guidelines has no build artifacts today).

2. **(ii) guidelines tree sha (alongside agents/frontend/plugins/scripts)**
   - Walk+aggregate: after :521-522 — `GUIDELINES_LINES="$(_stage_tree_walk guidelines "$STAGE_TMP/guidelines")"` + `GUIDELINES_TREE="$(_tree_hash_of_lines "$GUIDELINES_LINES")"` (the small-tree budget note at :519-520 applies: guidelines is small/fixed-shape, no unbounded-walk concern).
   - Manifest key: in the heredoc after :717 — `"guidelines_tree_sha256": "$GUIDELINES_TREE",`.
   - **Post-stage re-hash leg REQUIRED** (mirror plugins :747-753): `integrity_verify` does NOT cover plugins/scripts/guidelines — without a local leg a copy glitch ships silently. Alternatively extend lib.sh's loop (see B).
   - Optionally a post-swap verify mirrors nothing further — the pattern is complete with the local leg.

3. **(iii) `guidelines_manifest` per-file map**
   - Frag block mirroring :577-587 (plugins) using `$GUIDELINES_LINES`; join via `_stage_join_map` (:539-548); frag file inside `$STAGE_TMP` so existing `rm -rf` failure paths clean it (:535-538 rationale).
   - Manifest key after :720: `"guidelines_manifest": {$guidelines_map}`.
   - Map keys are TREE-relative (`"README.md"`, `"TEMPLATE.md"`, ...) — scripts map precedent :588-592 ("keys are TREE-relative ... composes with lib.sh's `_manifest_map_lines` verifier exactly like the other trees").

### A.4 Tree-hash ordering discipline (lib.sh)

- `_tree_manifest <dir> <relprefix>` (lib.sh:3074): `find . -type f | sed 's|^\./||' | sort > "$list"` (**lib.sh:3116**), then ONE batched `xargs -0 -n 500 shasum -a 256` (:3131-3133) — output order == argv order == sorted-list order, byte-identical to whole-list form (:3121-3130).
- `_tree_hash` (lib.sh:3227): `_tree_manifest "$1" "" | shasum -a 256 | awk '{print $1}'` — hash of the `"sha␠␠rel"` LINES (one trailing newline per line).
- `_stage_tree_walk` (lib.sh:3191-3205): bounded wrapper (`_run_bounded "$STAGE_TREE_BUDGET_S"`), stdout is DATA — any log marker to stdout corrupts the manifest (:3193-3196).
- `_tree_hash_of_lines` (lib.sh:3232): aggregate over precomputed lines (avoids a second walk — stage.sh uses this).
- `_manifest_map_lines <manifest-json> <tree>` (lib.sh:3243): verifier-side flat-map reparse.
- **Caveat (bracket dirs / independent recompute)**: any independent tree-hash recompute must replicate this ordering EXACTLY — `find|sed|sort` feeding the line hash; a per-directory `os.walk` sort yields FALSE MISMATCH (reviewer[v2] precedent; POK `mutation-01`/audit tips). Per-file map values must also match `shasum -a 256`'s two-space separator format.

---

## B. Manifest consumers — would `guidelines_tree_sha256` + `guidelines_manifest` break anything?

| Consumer | What it reads | Verdict for new keys |
|---|---|---|
| `scripts/upgrade/lib.sh` `integrity_verify` (:4051-4129) | `binary_sha256` (:4064), `launcher_sha256` (:4076), `config_sha256` (:4088, optional), then **`for tree in agents frontend`** (:4102) — tree sha + per-file map diff per tree. plugins/ and scripts/ are NOT verified here (stage.sh:734-737 documents this as an open escalation item) | **IGNORED** as-is. To get integrity coverage, either add `guidelines` (and optionally `plugins`/`scripts`) to the loop at :4102, or add the stage-local re-hash leg (§A.3-2). Extra manifest keys cost nothing — every lookup is a targeted `_json_field` |
| `lib.sh` `manifest_field` (:3063-3070) / `manifest_path` (:3061) | key-addressed reads only | IGNORED (no enumeration) |
| `lib.sh` adopt/rollback guard (:7840-7843) | `rollback_safe` | IGNORED |
| `lib.sh` retention/eviction identity (:2839-2853; stage.sh:616-624 comment) | `staged_at` epoch only | IGNORED |
| `scripts/upgrade/promote.sh` | `manifest_field "$VERSION" binary_version` (:496-498, missing → refuse — field still present); T5 gate `PREV_SAFE="$(manifest_field "$PREV" rollback_safe)"` (:890-931) | **UNAFFECTED** — no top-level sha enumeration anywhere |
| `scripts/upgrade/rollback.sh` (:174-177) | `rollback_safe` | UNAFFECTED |
| `scripts/upgrade/status.sh` | `rollback_safe` (:88), `manifest_path` + `binary_version` (:123-124) — all key-addressed (CORRECTION 2026-11-13: the earlier "zero manifest references" claim was FALSE) | UNAFFECTED |
| `scripts/upgrade/ledger_check.py`, `journal_read.py`, `journal_splice.py` | **zero** manifest/tree-sha references (grep-verified) | UNAFFECTED |
| `daemon/tools/upgrade_tools.py` `_scan_releases` (:798-836) | loads whole manifest JSON into a dict; renders IDENTITY fields only (:818-836; :2276-2294 "manifest schema — showing manifest identity fields per release") | **TOLERATED** — `.get()` style, extra keys ride silently in the dict |
| `upgrade_tools.py` version gate (:2200-2208) | `manifest.get("binary_version")` | TOLERATED |
| `upgrade_tools.py` `_target_release_state` (:1957-1978) | `manifest.get("rollback_safe")` | TOLERATED |
| `upgrade_tools.py` drop-release arm (:3415-3471) | `rollback_safe` / `binary_version` / `known_schema_gen` via `.get` | TOLERATED |
| `daemon/services/release_manifest.py` (NEW reader, exists at this base) | reads ONLY `agents_manifest` (:53, :113-126); "Missing TOP-LEVEL keys other than `agents_manifest` are TOLERATED (the demo v0.16.3 manifest predates `plugins_manifest`)" (:22-23); `verify_paths` (:164+) is per-requested-path | **TOLERATED** — new keys invisible to it |
| `daemon/services/prompt_refresh.py` (:745-780) | uses `load_release_manifest`/`verify_paths` only | TOLERATED |
| Tests | CORRECTED 2026-11-13 (earlier "ONE tangential hit / no test pins the key SET" was FALSE): presence-pin loop `tests/test_release_journal.sh:782-787` (14 keys, presence-only, additive-safe) + hits at `tests/packs/preflight_watchdog_test.sh:141-145`, `tests/test_stage_plugins_tree.sh:247-269`/`:357`, `tests/test_executor_family_payload.sh:318`; `tests/test_bind_retry.py:149` tangential | No exact-set rejection anywhere |

**Files that MUST change: none (consumer side).** Optional learn-the-field changes: lib.sh `integrity_verify` loop :4102 (+ the field-group doc comment lib.sh:3052-3059, documentation-only) OR the stage-local re-hash leg.

---

## C. Frozen-binary runtime resolution

### C.1 The anti-pattern (plugins) — why `plugins/` is unresolvable frozen

`daemon/plugin_subsystem/plugin_registry.py:63-64`:
```python
# Default plugins root = <repo-root>/plugins/ (CON §1: "plugins/<name>/").
DEFAULT_PLUGINS_ROOT: Path = Path(__file__).resolve().parents[2] / "plugins"
```
Boot scan uses it with NO override — `daemon/manager.py:3856-3860`: `registry = load_registry(DEFAULT_PLUGINS_ROOT)`. Under PyInstaller, `__file__` resolves inside the extraction bundle (`sys._MEIPASS` / onedir `_internal`), so `parents[2]` is the BUNDLE root — where no `plugins/` exists. The staged tree lives at `releases/<v>/plugins/`, a SIBLING of the binary, never inside the bundle → boot scan sees 0 plugins (v0.18.0 regression class; stage now refuses without the tree, stage.sh:304-321). **Lesson: never derive a payload base from `__file__`; derive from `sys.executable` (frozen) or a runtime-resolved release root.**

### C.2 The positive pattern — how payload trees are located at runtime

The canonical frozen/dev fork, repeated across the runtime lane:
```python
if getattr(sys, "frozen", False):
    base = Path(sys.executable).parent            # → releases/<v>/
else:
    base = Path(__file__).resolve().parent[.parent...]   # → repo root
```
Sites: `daemon/loader.py:446-448` (`_base_dir`; agents payload), `daemon/manager.py:4815-4820` (`agents_base` → registry), `daemon/registry.py:1581-1583`, `daemon/graph.py:11045-11047` (`_WATCHER_AGENTS_BASE`), `daemon/api.py:169-171` (`BASE_DIR`), `daemon/routers/agents.py:20-22` (`BASE_DIR`, three parents up), `daemon/tools/agent_mother.py:141-143`. In frozen mode `sys.executable` = the real binary path `…/releases/<v>/ensemble-prod`, so `base/agents` resolves the staged payload. Frozen topology facts: install dir = `binary.parent.parent.parent`, release root = `binary.parent` (`upgrade_tools.py:453-458` `_auto_derive_env` comment).

**The most robust runtime anchor** — derive the release root from the loader's own resolved dir: `daemon/services/prompt_refresh.py:660` takes `resolved_dir = Path(agent_meta.path)` from the registry, then `:745`: `release_root = resolved_dir.parent.parent`, and reads the manifest via `load_release_manifest(release_root)` (:747) — the function takes the RELEASE ROOT and joins `manifest.json` internally (`release_manifest.py:96` def, join at `:104`), it does NOT take a manifest path. This is anchored to the tree the daemon ACTUALLY reads, immune to CWD and to bundle-vs-binary confusion. A guidelines reader should either (a) reuse the `sys.frozen → Path(sys.executable).parent` fork (matches the agents/ tree the loader serves) or (b) ride the registry-resolved agents root (`registry._agents_dir.parent` / `resolved_dir.parent.parent`).

### C.3 Dev-checkout sibling behavior

Runtime-lane sites use **repo-path fallback** (`Path(__file__)`-derived), NOT an env var: in dev mode a `sys.frozen`-fork reader lands at `<repo-root>/guidelines` — which does NOT exist (source lives at `<repo-root>/docs/guidelines/`). So the reader needs an explicit dev fallback (`<repo>/docs/guidelines`) or an explicit override knob. The only HOME-based candidates resolver is upgrade-lane, not runtime: `daemon/tools/upgrade_tools.py:396-438` `_install_root_candidates`/`_canonical_install_root` (HOME basename == install name → HOME; `HOME/<name>`; sibling) — resolves the INSTALL dir, mirrored bash-side by lib.sh `_install_dir_candidates` (referenced upgrade_journal.py:2191). Nothing runtime-lane reads `<install>/releases/<v>/…` via HOME today except this upgrade lane.

**Recommended reader base**: `Path(sys.executable).parent / "guidelines"` when `sys.frozen` (→ `releases/<v>/guidelines/`), `<repo>/docs/guidelines` in dev (derive via the loader.py:446-448 fork), optionally an explicit override parameter for tests.

---

## D. Test injection precedent

| Test file | What it pins | Injection pattern |
|---|---|---|
| `tests/test_executor_family_payload.sh` (repo tests/ root, 331 lines) | payload layout pin for the executor family: present + sibling path + executable + integrity-contract + stamp-coverage positive/negative + no-plist | **Hermetic mktemp fixtures only** — fixtures built by the SAME shipping function (`executor_family_install`), stamp-coverage against a SYNTHETIC git repo, `make -n install` dry run; live install NEVER touched (header :22-33). Direct precedent for a "guidelines payload pin" bash test |
| `tests/unit/services/test_release_manifest.py` | `load_release_manifest`/`verify_paths` incl. tolerance legs (`test_missing_other_top_level_keys_tolerated` :92-99) | **Constructor-arg injection**: `_stage_release(tmp_path)` builds `<tmp>/install/releases/v9.9.9/agents/…` with real sha256s (:38-52), `_write_manifest` (:55-59); `release_root` passed as an explicit function arg — no env vars, no base-resolution monkeypatching |
| `tests/unit/services/test_prompt_refresh_primitive.py` | the primitive's dev/live-shape gating + manifest verify legs | **monkeypatch module attr + provenance dict injection**: `monkeypatch.setattr(registry_mod, "get_registry", lambda: SimpleNamespace(get_resolved=…, _agents_dir=REPO_ROOT/"agents"))` (:265-298); live-shape simulated by setting `provenance["install_root"]` to a fake path (:87: `"/tmp/nonexistent-install"`, :288) — never real HOME-shaped paths |
| `upgrade_tools.py` docstring contract (:411-412) | `_install_root_candidates` is a pure function of `Path.home()` | "tests redirect it with a fake HOME; never construct fixtures at real live-resolved paths" |

Pattern summary: explicit `release_root`/base argument > monkeypatch of the resolver seam > fake HOME; never ambient-env or live-path fixtures.

---

## E. PIPELINE_SCRIPTS_FLOOR_COMMIT interaction

**Current constants (this base):** `daemon/tools/upgrade_tools.py:4145` `PIPELINE_SCRIPTS_FLOOR_COMMIT = "087f782afbf1957dd0bf6acea5198314093d2a89"`; `scripts/upgrade/lib.sh:95` `PIPELINE_SCRIPTS_REV=087f782afbf1957dd0bf6acea5198314093d2a89` (identical — D6-8 pairing holds).

**Stage-time gates (stage.sh):**
1. Floor SET+VALIDATE (:627-651): reads the constant from the built tree's `upgrade_tools.py` (:644-645); `_pipeline_scripts_floor_verdict` (lib.sh:746-775) requires it to be a commit sha AND **an ancestor of the built tree** (`git -C $REPO_ROOT merge-base --is-ancestor <floor> HEAD`, refusal text lib.sh:773) — i.e. the floor must name a commit the release itself contains. Comparison is against the BUILT TREE's HEAD — **origin/latest ancestry is not directly checked; containment in the built tree is** (the built tree is the tagged feature-merged tip, so containment implies latest-ancestry in practice).
2. Stamp SET+VALIDATE W-2 (:653-672): `PIPELINE_SCRIPTS_REV` must be 40-hex, resolve in the tree, and EQUAL the floor (D6-8; `_pipeline_scripts_stamp_verdict` = lib.sh:727-742 — the earlier :707-738 span was the doc-comment-inclusive range, off-target).
3. Stamp COVERAGE (:674-699): `executor_family_verify_stamp_coverage` (executor_family.sh:48-50) lists family paths **that do not EXIST** at the stamped commit — existence-only, not content-equality. Remedy text :695: "re-pin PIPELINE_SCRIPTS_REV (scripts/upgrade/lib.sh) AND PIPELINE_SCRIPTS_FLOOR_COMMIT (daemon/tools/upgrade_tools.py) to a merge commit that contains the WHOLE shipped family".

**Arm-time guard (daemon):** `scripts_dir_provenance_verdict` (`upgrade_tools.py:4210-4390`): git dir → stamp must parse from `<dir>/lib.sh`, floor compared by **ancestry against the pinned scripts dir's HEAD** (:4352-4354 `merge-base --is-ancestor floor HEAD`); `anc_rc==1` → refuse `stale-scripts` (:4360-4367); unset floor → refuse `floor-unset` (:4343-4351); escape `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` warn-only (:4259-4266); non-git pinned dir warn-only (:4377-4390).

**What THIS feature owes:**
- `stage.sh` and `lib.sh` ARE executor-family members (executor_family.sh:72,76). **Content edits to existing members do NOT trip the coverage gate** (existence-only). Adding NO new family file (guidelines payload is not a family member) → coverage stays green at the old stamp.
- Mechanically, an UNADVANCED floor/stamp still passes every gate: old floor `087f782a` remains an ancestor of the merged tree; stamp == floor; coverage passes. Nothing about this feature's stage is BLOCKED by the gates themselves.
- BUT the D6-8 cadence rule (stage.sh:633-641; lib.sh:93-95 comment) makes re-pinning an OBLIGATION of the release bump: "the release bump (the merge commit that introduces the ceremony-relevant scripts/upgrade semantics) writes the constant … at the SAME cadence as the PIPELINE_SCRIPTS_REV stamp". Editing stage.sh (stage-time semantics) — and lib.sh if the `integrity_verify` loop is extended — IS ceremony-relevant scripts/upgrade semantics. **So: advance BOTH constants to this feature's merge-into-latest commit, in the release-bump commit, by whoever runs the bump (the `chore(release): bump version` producer); both edits travel in ONE commit (upgrade_tools.py:4145 + lib.sh:95 together).** Advancing requires the merge commit to exist → the constants are pinned AFTER `merge --no-ff` to latest, BEFORE `stage.sh` runs (the stage gate validates against the built tree, which must contain the merge).
- Own v0.30.3 stage verdict: with the constants advanced → all three gates pass cleanly (floor ancestor ✓, stamp==floor ✓, coverage ✓). Without advancing → still passes (old floor is an ancestor) but violates the documented cadence and leaves the stamp truthfully vouching for a scripts tree that no longer matches the shipped content of two family members — disclose at ceremony either way.
- Unrelated residual: the arm-time `stale-scripts` refusal (:4360-4367) binds the OPERATOR's pinned scripts worktree (`ensemble-src-wt-scripts-pin` / `ENSEMBLE_UPGRADE_SCRIPTS_DIR`), not this branch — after the floor advances, that pinned tree must be moved to/after the new merge commit or the NEXT ceremony's arm refuses (or `ENSEMBLE_ALLOW_STALE_SCRIPTS=1` warn bypass).

---

## F. rollback_safe fit

Confirmed path (stage.sh):
- Full-history destructive-DDL grep is INFORMATIONAL and ALWAYS runs: :356-363 — `grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN' "$REPO_ROOT"/daemon/migrations/versions/*.sql` → `CONTAINS_CONTRACT_PHASE=true` on ANY match anywhere in history (this tree matches: v0.15.3-era schema history carries DROPs), regardless of delta.
- Derivation case :368-394: `ENSEMBLE_ROLLBACK_SAFE=1|true` → `explicit=true` (:371); `0|false` → `explicit=false` (:372); **unset + contract-phase match → REFUSE exit 78** (:382-388, v0.14.2/v0.15.1 incidents); unset + no match → `default=true` (:389-391).
- Legitimacy rule (in-file :379-385 + operated precedent): `=1` is legitimate ONLY when the migration DELTA between the previous release tag and `$VERSION` is empty (destructive DDL already applied pre-tag). Precedents v0.21.0 / v0.22.0 / v0.23.0 (leader-authorized, journaled); refusal path :368-391.
- Consumers of the derived value: promote T5 gate (:890-931), manual rollback gate (rollback.sh:174-177), adopt guard (lib.sh:7840-7843), drop-release arm (upgrade_tools.py:3415-3471) — all read-only on `rollback_safe`.

**Docs+tool-only feature with ZERO migrations fits cleanly:** no new `daemon/migrations/versions/*.sql` → `KNOWN_SCHEMA_GEN` unchanged (:356-357, informational); delta since previous tag is DDL-empty → the explicit `ENSEMBLE_ROLLBACK_SAFE=1` + tag-to-tag delta disclosure is the correct, precedented adjudication. Expect `contains_contract_phase=true` (full-history artifact) — normal, disclosed, non-blocking. NOTE the POK-stage trap: stage.sh receipts are STDOUT-only (POK `stageverify-01`); verify via the release manifest (`rollback_safe`, `staged_at`, `known_schema_gen`) after staging.

---

## Cross-cutting notes for the plan

- **Single-writer seams**: everything the feature touches on the scripts side lives in `stage.sh` (+ optionally `lib.sh` `integrity_verify` loop + its :3052-3059 field-group comment). `executor_family.sh` is untouched (guidelines is not a family member; do NOT add it to `executor_family_paths` — that would trip the coverage gate at :688-698 unless the stamp is re-pinned).
- **Layout pin test**: `tests/test_executor_family_payload.sh` pins "NO plist / layout" over the staged tree via the family list only — a new `guidelines/` dir does not disturb it, but the natural home for a guidelines-payload pin test is a sibling hermetic bash test following its fixture discipline.
- **`releases/<v>/guidelines/` naming**: flat, NOT `docs/guidelines` under the release root — the runtime reader resolves `<release-root>/guidelines` per §C.2; keep stage copy and reader symmetric.
- **Existing reader contract to respect**: `release_manifest.py` tolerates extra top-level keys — but `verify_paths` only checks REQUESTED paths against `agents_manifest`; a guidelines reader that wants integrity coverage must read `guidelines_manifest` itself (no existing consumer will).
