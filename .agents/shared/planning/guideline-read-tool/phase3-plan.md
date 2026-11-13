# Phase 3: Release payload seam (stage.sh + manifest)

## Objective

`stage.sh` copies `docs/guidelines/` into the payload as `releases/<v>/guidelines/` (flat), the manifest gains a guidelines tree sha (alongside agents/frontend/plugins/scripts) + per-file map, and a MANDATORY stage-local re-hash leg closes the integrity gap `lib.sh` leaves open. Consumer audit result: zero consumer changes. NO `lib.sh` / `executor_family.sh` edits.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Precondition guard next to the plugins guard (`stage.sh:318-321`): `[ ! -d "$REPO_ROOT/docs/guidelines" ]` → `_warn` + `exit 78` (fail-closed, same exit-78 family; plugins rationale comment style — a release whose guidelines/ cannot ship must refuse to stage) | none | Stage without the dir refuses with exit 78 and a clear warning |
| 2 | Payload copy after the plugins copy (:450), before `executor_family_install` (:484): `cp -R "$REPO_ROOT/docs/guidelines" "$STAGE_TMP/guidelines"` — mirrors the plugins verbatim copy; comment cites the plugins precedent (:443-449). Flat `guidelines/` at the release root, NOT `docs/guidelines/` (reader symmetric, research §cross-cutting). Note: `_no_env_in_release` (:493-496) walks the whole tree — .md-only content is safe; no FE-sidecar-style strip needed (no build artifacts in the tree today) | 1 | Synthetic stage contains `guidelines/` with exactly the 5 files |
| 3 | Walk + aggregate after the scripts walk (:521-522): `GUIDELINES_LINES="$(_stage_tree_walk guidelines "$STAGE_TMP/guidelines")"` + `GUIDELINES_TREE="$(_tree_hash_of_lines "$GUIDELINES_LINES")"` (lib.sh `_stage_tree_walk` :3191-3205 — stdout is DATA, no log markers; `_tree_hash_of_lines` :3232). Small/fixed-shape tree → the small-tree budget note (:519-520) applies, no unbounded-walk concern | 2 | `GUIDELINES_TREE` computed; ordering discipline `find \| sed \| sed -n s`→sort (lib.sh:3116) preserved |
| 4 | Per-file map frag after the plugins frag (:577-587): linear streaming via `_stage_join_map` (:539-548); frag file INSIDE `$STAGE_TMP` (existing `rm -rf` failure paths clean it, :535-538 rationale); map keys TREE-relative (`"README.md"`, `"TEMPLATE.md"`, … — scripts map precedent :588-592; composes with `lib.sh` `_manifest_map_lines` :3243) | 3 | Every file appears in the map with the correct sha (two-space separator format) |
| 5 | Manifest heredoc keys: `"guidelines_tree_sha256": "$GUIDELINES_TREE",` after :717 (tree shas) and `"guidelines_manifest": {$guidelines_map}` after :720 (maps) — a guidelines tree sha alongside agents/frontend/plugins/scripts | 4 | Manifest JSON valid; keys present; `python -m json.tool` clean |
| 6 | **MANDATORY stage-local re-hash leg** mirroring plugins (:747-753): re-walk `$STAGE_TMP/guidelines` → `_tree_hash_of_lines` → compare against `GUIDELINES_TREE`; mismatch → `_warn` + `rm -rf "$STAGE_TMP"` + `exit 1`. WITHOUT this leg the guidelines hash is NEVER verified — `lib.sh integrity_verify` loops only `agents frontend` (:4102) and is NOT edited in this feature (open escalation item stays open for plugins/scripts alike) | 5 | Corrupt-a-file negative leg refuses the swap-in (exit 1); positive leg passes |
| 7 | Hermetic stage-smoke test `tests/test_guidelines_payload.sh` (repo tests/ root, sibling of `tests/test_executor_family_payload.sh`, 331 L — THE fixture-discard precedent): hermetic mktemp fixtures built by the SAME shipping functions; synthetic git repo where needed; live install NEVER touched (header :22-33 discipline); legs: (i) staged tree has `guidelines/` (5 files); (ii) manifest keys present + hashes match an INDEPENDENT recompute (`find \| sed \| sort` → line-hash — replicate lib.sh ordering EXACTLY, bracket-dir caveat per research §A.4); (iii) per-file map values match; (iv) local re-hash leg green; (v) negative: corrupt one staged file → refuse | 6 | All legs green; no live-path fixtures (POK fence rule: mktemp FAKE_HOME-style fencing, never live-shaped paths) |

## Consumers — audited, ZERO changes required (verify, don't trust)

| Consumer | Reads | Verdict |
|---|---|---|
| `lib.sh integrity_verify` (:4051-4129) | targeted `_json_field` reads; tree loop = `agents frontend` only (:4102) | IGNORED — extra keys cost nothing; coverage comes from T6 |
| `lib.sh manifest_field`/`manifest_path` (:3061-3070); adopt/rollback guard (:7840-7843); retention identity (:2839-2853) | key-addressed / `rollback_safe` / `staged_at` | IGNORED |
| `scripts/upgrade/promote.sh` (:496-498, :890-931) | `binary_version`, `rollback_safe` | UNAFFECTED — no top-level sha enumeration |
| `scripts/upgrade/rollback.sh` (:174-177) | `rollback_safe` | UNAFFECTED |
| `scripts/upgrade/status.sh` | `rollback_safe` (:88), `manifest_path` + `binary_version` (:123-124) — ALL key-addressed (grep-verified; the earlier "zero manifest refs" claim was FALSE) | UNAFFECTED |
| `scripts/upgrade/ledger_check.py`, `journal_read.py`, `journal_splice.py` | zero manifest refs (grep-verified) | UNAFFECTED |
| `daemon/tools/upgrade_tools.py` `_scan_releases` (:798-836), version gate (:2200-2208), `_target_release_state` (:1957-1978), drop arm (:3415-3471) | whole-JSON dict + `.get()` fields; renders identity fields only | TOLERATED — extra keys ride silently |
| `daemon/services/release_manifest.py` | ONLY `agents_manifest` (:53, :113-126); missing top-level keys tolerated (:22-23, demo v0.16.3 precedent) | TOLERATED — new keys invisible |
| Tests | presence-pin loop `tests/test_release_journal.sh:782-787` (14 keys, presence-only, additive-safe); further hits at `tests/packs/preflight_watchdog_test.sh:141-145`, `tests/test_stage_plugins_tree.sh:247-269` and `:357`, `tests/test_executor_family_payload.sh:318`; `tests/test_bind_retry.py:149` tangential | No exact-set rejection anywhere |

**Explicit non-edits:** `lib.sh` (including the `integrity_verify` loop and its :3052-3059 field-group comment), `executor_family.sh` (guidelines is NOT a family member — adding it to `executor_family_paths` would trip the coverage gate at stage.sh:688-698 unless the stamp were re-pinned; do NOT), `rollback.sh`, `promote.sh`, all daemon consumers.

## Floor constants + rollback_safe (decided here; executed in release-recipe.md)

- **Floor (D6-8):** editing `stage.sh` content is ceremony-relevant scripts/upgrade semantics, but the coverage gate is EXISTENCE-only (`executor_family.sh:48-50`) — gates pass mechanically even WITHOUT advancing. The cadence rule (stage.sh:633-641) still OBLIGES the advance: BOTH `PIPELINE_SCRIPTS_FLOOR_COMMIT` (`upgrade_tools.py:4145`) and `PIPELINE_SCRIPTS_REV` (`lib.sh:95`) move to this feature's MERGE commit, in the release-bump commit, post-merge pre-stage (research §E). Disclose either way at ceremony. Architect confirms ordering (Q2).
- **rollback_safe:** this is a docs+tool-only feature with ZERO migrations → tag-to-tag delta is DDL-empty → operated adjudication `ENSEMBLE_ROLLBACK_SAFE=1` + explicit tag-to-tag delta disclosure (:379-385 rule; v0.21/22/23 precedents). `contains_contract_phase=true` EXPECTED (full-history DDL grep :356-363 always fires) — normal, disclosed, non-blocking. `KNOWN_SCHEMA_GEN` unchanged. Receipt is STDOUT-only (POK `stageverify-01`) — verify via the release MANIFEST (`rollback_safe`, `staged_at`, `known_schema_gen`) after staging, not via stage output.

## Coupling

- **Loose with Phase 1** — one shared contract: the payload layout name `guidelines/` (flat at release root). No shared code.
- **Independent of Phases 2 and 4.**
- Timeline-wise this phase can run in parallel with Phase 2; the release RECIPE (execution) sequences it after merge (floor constants need the merge sha).

## Risks

- Omitting T6 (local re-hash leg) = a copy glitch ships SILENTLY — the single highest-severity omission on this seam; T7's negative leg exists to prove the leg works.
- Independent recompute false-mismatch: a per-directory `os.walk` sort yields FALSE MISMATCH vs the `find|sed|sort` line-hash ordering (reviewer[v2] precedent; POK audit tips) — T7(ii) must replicate the ordering exactly.
- Walk stdout corruption: any log marker echoed inside `_stage_tree_walk` usage corrupts the manifest (:3193-3196) — keep the call result-as-data discipline.

## Exit Criterion

A hermetic synthetic stage produces a payload with `guidelines/` (5 files), a valid manifest carrying `guidelines_tree_sha256` + `guidelines_manifest`, local re-hash green, negative leg refusing corruption; consumer audit re-run post-implementation shows zero consumer files touched.
