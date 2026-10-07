# Plugin Subsystem Slice ① — Independent Test Pass (Manifest vocab v1)

- **Date**: 2026-10-06, completed 18:12 UTC
- **Commission**: Overnight master build — slice ① independent verification (report-only, no modifications, no commits)
- **Worktree**: `/home/nea/ensemble-src-wt-plugin-subsystem-01`, branch `feature/plugin-subsystem-01`, HEAD `c5fe025e`, base `d262c5e8`
- **Workers**: `slice01-suite-run` (1c19528f, skill=test-pack-execution) · `slice01-git-state` (7b886250, no skill) · `slice01-negcheck-greps` (afa7f0db, no skill)
- **Overall verdict**: 🟢 **GREEN** — 5/5 commission checks PASS; 1 yellow leader-attention flag (documented-by-design branch-pin deferral, see Check 2)
- **Code changes by tester**: NONE (report-only contract honored by all 3 workers; worktree tree clean at start and end)

---

## Check 1 — Scoped suite: PASS

- Command: `timeout 300 .venv/bin/python -m pytest tests/unit/plugin_subsystem/ -q --tb=short` (worktree root)
- Preflight: `.venv/bin/python` = **Python 3.14.7** (no 3.13 rot exposure); `daemon.__file__` → `/home/nea/ensemble-src-wt-plugin-subsystem-01/daemon/__init__.py` ✅ inside worktree (editable-install trap NOT triggered)
- Counts: **114 passed / 0 failed / 0 errors / 0 skipped**, 1 warning (pre-existing langchain_core Pydantic-V1/3.14 deprecation — environmental, project-wide)
- Runtime: pytest 2.12s, wall 6.26s; **0 retries used** (no transient infra)
- Developer claim 114/0: **EXACT MATCH** on independent run

## Check 2 — Negative spot-checks: PASS with 1 split sub-case (🟡 flag)

All citations `tests/unit/plugin_subsystem/test_manifest_reader.py::<Class>::<test>`. Every covered item was ALSO ad-hoc re-fired from `/tmp` scratch against `validate_manifest()`; all refusal codes matched the test assertions. Zero UNCOVERED-AND-SILENT outcomes.

| # | Refusal semantic | Verdict | Evidence |
|---|---|---|---|
| 1.1a | SHA-shaped pin | COVERED | `TestPluginBlockRefusals::test_non_tag_pin_sha_refused` (`a1b2c3d4e5f6a7b8` → `non_tag_pin`); ad-hoc: 7-hex `abc1234` also refused |
| 1.1b | Empty pin | COVERED | `::test_non_tag_pin_empty_refused` → `non_tag_pin` |
| 1.1c | **Branch-name pin** ("main", "release-2026.10", "HEAD") | **ACCEPTED — by design** 🟡 | `manifest_reader.py:422-432` refuses only empty/SHA-shaped; branch-vs-tag discrimination explicitly deferred to slice-③ vendoring-time sync checks — stated in code comment AND refusal message text. NOT silent. **No characterization test pins this acceptance** → recommend one so the deferral stays intentional-and-visible |
| 1.2 | execution_mode missing | COVERED | `::test_absent_execution_mode` + `::test_read_manifest_raises_on_refusal` → `absent_execution_mode` ("silence is not permission") |
| 1.3 | Empty divergence_register on non-empty snapshot paths | COVERED | `TestProvenanceClassRefusals::test_empty_divergence_register_on_non_empty_paths` → `empty_divergence_register` |
| 1.4 | alarm_owner missing (synced classes) | COVERED ×3 | copy_freely: `::test_alarm_owner_missing_empty_string`, `::test_alarm_owner_missing_absent`; snapshot: `::test_alarm_owner_missing_on_snapshot` → `alarm_owner_missing` |
| 1.5 | Bad SPDX id | COVERED (5-case parametrized + expression refusal) | `TestPluginBlockRefusals::test_license_invalid` (+ location assert), `::test_license_spdx_expression_refused` (exact-match v1), `TestLicenseValidation::test_invalid_license_refused`; positive control `::test_valid_spdx_ids_pass`; vendored list = 38 ids |
| 1.6 | Manifest > 64 KB | COVERED | `TestFileLevelRefusals::test_manifest_too_large` (+ boundary complement `::test_manifest_at_cap_passes`); ad-hoc 66,123 B → `manifest_too_large` "hard-cap is 65536 (CON §1)" |
| 1.7 | integration_path "A" without fence grant | COVERED (both shapes + acceptance) | grant absent → `missing_required_manifest_fields` naming `fence_grant` (`::test_fence_missing_grant_absent`); grant incomplete → `fence_missing` (`::test_fence_missing_grant_incomplete`); complete grant accepted (`TestValidManifests::test_valid_a_path_with_fence_passes` + ad-hoc) |

## Check 3 — Sentinel greps: PASS

**(a) Vocabulary containment** (whole worktree, `--exclude-dir=.git --exclude-dir=.venv`):

| Term | Total | daemon/plugin_subsystem | plugins-convention | tests/unit/plugin_subsystem | OTHER |
|---|---|---|---|---|---|
| execution_mode | 90 | 29 | 11 | 24 | 26 |
| lifted_symbol | 16 | 4 | 2 | 6 | 4 |
| hosted_runtime_deps | 20 | 8 | 3 | 6 | 3 |
| fence_grant | 24 | 7 | 5 | 5 | 7 |
| divergence_register | 26 | 9 | 6 | 5 | 6 |

ALL "OTHER" hits = `.md` planning docs (`.agents/shared/planning/plugin-subsystem/{v1-interface-contracts,architecture-recommendation,architecture-decision-record}.md` — the vocabulary's source-of-truth) + stale `.pytest_cache/v/cache/nodeids` artifacts. **Zero code hits outside allowed homes; `frontend/` and `docs/`: 0 hits for all 5 terms.**

**(b) No dynamic plugin loading**: `pkg_resources`/`entry_points`/`__import__`/`pkgutil`/`importlib.metadata` = **0 hits**; `importlib` = 3 hits, ALL negative-assertion comments/docstrings stating the invariant itself (`__init__.py:10`, `path_type_registry.py:5`, `path_types.yaml:7`). Effective machinery usage = **ZERO**.

**(c) Tier-1 isolation**: `grep -rn "plugin_subsystem" daemon/ --include="*.py" | grep -v "daemon/plugin_subsystem"` → **0 hits**. Tier-1 structurally blind to the new package (matches in-repo `TestNoImportInvariant` sentinel).

## Check 4 — Scoped regression fence: PASS

`git diff --name-only d262c5e8..HEAD` = **19 files, all inside the 3 allowed prefixes, 0 violators**:
- `daemon/plugin_subsystem/` (5): `__init__.py`, `manifest_reader.py`, `path_type_registry.py`, `plugin_declaration.py`, `schema_ci.py`
- `plugins-convention/` (6): `CONVENTION.md`, `ci_runner.py`, `execution_mode.enum.json`, `manifest.schema.json`, `path_types.yaml`, `spdx_ids.json`
- `tests/unit/plugin_subsystem/` (8): `__init__.py`, `_manifest_fixtures.py`, `conftest.py`, `test_dual_path_equivalence.py` (11), `test_manifest_reader.py` (67), `test_path_type_registry.py` (18), `test_schema_ci.py` (7), `test_sentinels.py` (6)

No tier-1 files (manager/graph/__main__), no pyproject/uv.lock, no agents/ prompts, no frontend/, no scripts/. Collection: `pytest tests/unit/plugin_subsystem/ --collect-only -q` → **114 collected, 0 collection errors**, exit 0.

## Check 5 — Branch state: PASS

- HEAD `c5fe025efc7bdd10ddd45d6e35a6d8a0e2d4a397` (matches expected c5fe025e)
- Branch: `feature/plugin-subsystem-01` ✓ · `status --porcelain` empty (clean) ✓
- `rev-list --count d262c5e8..HEAD` = **3** ✓ — commits (oldest→newest): `610b8096` feat: manifest vocab v1 (frozen convention assets + tier-2 reader/registry) → `db44c8e9` test: unit suite (112 cases) → `c5fe025e` fix: slice-① review minors (5 findings)
- Base `d262c5e8bb90941c21f8997669b228f50a38ba13` resolves ✓

---

## Anomalies & flags for the leader

1. 🟡 **Branch-name pins ACCEPTED at manifest layer** (only deviation from commission's literal "non-tag → refusal"). Documented-by-design deferral to slice ③ (code comment + refusal message text); NOT silent. Gap: no characterization test pins the acceptance — recommend adding one (or it lands with slice ③).
2. 🟢 Hex-named tags (e.g. `cafebabe`) false-positive as SHAs → `non_tag_pin`. Inherent to offline shape check; acceptable v1; deserves a code comment.
3. 🟢 In-repo sentinel (`test_sentinels.py` VOCABULARY_STRINGS) omits `fence_grant` + `divergence_register` (has execution_mode/lifted_symbol/ipc_version/hosted_runtime_deps). My greps confirm both confined anyway — suggest developer adds them.
4. 🟢 **Shared/live worktree**: `.pytest_cache/v/cache/nodeids` refreshed 18:07:35Z by a different agent mid-verification (negcheck worker ran `-p no:cacheprovider` — proven in /tmp it writes nothing); `lastfailed` @ 17:40Z (pre-slice green) also observed. Non-blocking — all my runs were against clean tree @ c5fe025e — but future slice passes in this worktree should expect concurrent traffic.
5. 🟢 Import-check CWD-shadowing false alarm: running `python -c "import daemon..."` from the MAIN repo CWD resolves main-repo `daemon/` via `sys.path[0]=''` — looks like the editable trap but isn't. Always `cd <worktree>` first. (→ LESSONS/2026-10-06-worktree-import-check-cwd-shadowing.md)

## Scope decision

Commission-scoped (5 checks) — no expansion. Full repo suite deliberately NOT run (known unrelated 3.13 collection rot out of scope tonight; commission explicitly forbade). ensure.md: Core "no regressions in changed packs" satisfied by Check 1 (scoped pack, PASS); boot-dependent Release-Gate items out of scope per commission no-boot boundary — deferred to merge/promote time per overnight plan.

## Boundary compliance

No live promote · no ~/agents-ensemble (9797) / ~/agents-ensemble-demo (7979) / systemd / OD-daemon touches · no server boots · port 8088 untouched · no key/credential changes · zero tracked-file modifications · zero commits.
