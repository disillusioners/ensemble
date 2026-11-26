# Soak-Gate Evidence Carry-In + F-11 Static Re-Pin + M22 Plant-Drift Rider — Gate RESULTS (2026-11-25)

**Summary.** Two gate verdicts for this lane — the tester's independent **PASS** and the reviewer's **APPROVE-WITH-CHANGES** (Part 1, shape adjudication) / **APPROVE** (Part 2, re-gate) — existed ONLY in instance transcripts: both producing instances hit the known revive-wedge (a revived child that already delivered one report cannot terminalize or deliver a second) and their reports were never delivered. This record files them as an artifact. The lane (5 commits on `feature/soak-gate-evidence-carryin`, base `dd007f16d`) carried in the Oct-26 soak RCA — cited 7 times across 5 files at base while ABSENT from the tree under any name — and replaced a 149-line PARTIAL gate verdict with the 166-line COMPLETE copy, both byte-identical to the branch-preserve evidence manifest (rows 56–57); re-pinned the F-11 static pin as a two-clause composite (dispatch count over `resolve_spawn_env` NAME loads + a direct-call guard) that is genuinely RED at base (1 failed / 9 passed — the stale counter's own assertion) and GREEN at head (12 passed), with three mutation regressions caught by three DISTINCT test nodes; and refreshed the M22 mutation-plant literal, which had drifted behind the live `PIPELINE_SCRIPTS_REV` stamp (base `PLANT-FAILED`, exit 1 → head `PLANTED`, exit 0). The merge landed on `origin/latest` as fast-forward `f418bdd6e → 40301a5e3`; the five version-bearing files agree at 0.31.58 = max(0.31.55, 0.31.55, 0.31.58). No `daemon/` or `scripts/` file changed.

| | |
|---|---|
| **Project** | agents-ensemble |
| **Lane** | soak-gate evidence carry-in + F-11 static re-pin + M22 plant-drift rider |
| **Date** | 2026-11-25 |
| **Branch / base** | `feature/soak-gate-evidence-carryin` @ `dd007f16d03a1bfa5d399940e438f09a72cdb775` |
| **Landed** | merge `40301a5e3fae29931d0fd4063666036c01594e39` on `origin/latest` — parents `f418bdd6e2f466ed4b2c7544b6bfd928f92b7798` (first) + `991c41e8c7136092de614f52842b0e5474815fae` (second) |
| **Verdicts** | tester `0e7ea97e-504c-4848-a39d-2b6e5a61781d` — **PASS** (independent gate) · reviewer `9e18a802-c81f-4fa9-9d42-e0b5cc882f08` — **APPROVE-WITH-CHANGES** (Part 1) / **APPROVE** (Part 2) |
| **Record provenance** | Filed post hoc from instance transcripts (§5). Every git-derivable value below was RE-DERIVED from `origin` on 2026-11-25 at the merge tip `40301a5e3` (`git fetch origin`, no `--tags`, no `--prune`). Runtime outputs (pytest legs, mutation legs, pack legs) are quoted as the tester and reviewer reported them; they were NOT re-executed for this record. |

## Lane shape (re-derived from git)

Commits on the branch, oldest → newest (`git log dd007f16d..991c41e8c`; count = 5):

| # | Commit | Subject |
|---|---|---|
| 1 | `55e46617ad22610f3d629224627234087a4d42f8` | docs(soak-gate): carry in the Oct-26 soak RCA + the COMPLETE gate verdict |
| 2 | `263029e83c105f2b5c0303b9269ec9516fffe46d` | test(secrets): re-pin the F-11 spawn-site static pin to the resolve_spawn_env seam |
| 3 | `0e2b02bac4dc0178034d60f77b62a7420f1fc4c4` | test(preflight): re-pin the M22 mutation-plant literal to the live scripts stamp |
| 4 | `53252119f60d8367a4905bc0b208e062280c713e` | test(packs): register the soak-gate F-11 pin as a named pack (row-only) — cherry-pick of `601fcffdc51a08a31ffc4d81698676801b64ff0f`, tree-identical (both trees `e389487078f61f1162885d4617e79202888e890d`) |
| 5 | `991c41e8c7136092de614f52842b0e5474815fae` | docs(packs): align F-11 row prose; make Invocation cell verbatim |

**Merge.** `40301a5e3fae29931d0fd4063666036c01594e39` — `merge: land soak-gate evidence carry-in + F-11 pack row onto latest`; first parent `f418bdd6e2f466ed4b2c7544b6bfd928f92b7798` (the prior `origin/latest`), second parent `991c41e8c7136092de614f52842b0e5474815fae` (branch tip). `origin/latest` reflog: `f418bdd6e → 40301a5e3` ("update by push") — a fast-forward.

**Paths landed — exactly 5** (`git diff --name-status dd007f16d 991c41e8c`; the merge's first-parent diff is identical):

| Status | Path | Base → landed |
|---|---|---|
| A | `.agents/shared/analysis-20261026-v0278-soak-failure.md` | absent → 203 lines, sha256 `405f4c71…` |
| M | `.agents/tester/RESULTS/2026-10-28-soak-gate-mcp-warmup-functional-gate.md` | 149 lines `1f432168…` → 166 lines `7500b620…` (+26/−9) |
| M | `tests/unit/test_secret_do_not_touch_lane_static.py` | 10 nodes → 12 nodes (+107/−6) |
| M | `tests/helpers/preflight_mutation_apply.py` | plant literal `087f782af…` → `46d55b002…` (one line) |
| M | `.agents/tester/PACKS.md` | +18 lines (F-11 row, row-only — no pack script) |

`git diff --name-only dd007f16d 991c41e8c -- scripts/ daemon/` is empty: NO `daemon/` or `scripts/` change.

## 1. The carry-in and the dangling citations

**The RCA was cited but absent.** At base, `git ls-tree -r --name-only dd007f16d | grep -i analysis-20261026` is EMPTY — the Oct-26 soak RCA did not exist in the tree under any name. Yet it was cited **7 times across 5 files** (`git grep -c 'analysis-20261026-v0278-soak-failure' dd007f16d`):

| File | Occurrences (lines) |
|---|---|
| `.agents/shared/planning/db-pool-idle-txn/research-audit-census.md` | 1 (155) |
| `.agents/shared/planning/db-pool-idle-txn/test-strategy.md` | 1 (48) |
| `.agents/shared/planning/db-pool-idle-txn/wi-78967dba-verdict.md` | 3 (186, 263, 305) |
| `.agents/shared/planning/soak-gate-mcp-warmup/decisions.md` | 1 (19) |
| `.agents/shared/planning/soak-gate-mcp-warmup/requirements.md` | 1 (48) |
| **Total** | **7 / 5 files** |

The two soak-gate-mcp-warmup citations reference the canonical checkout's absolute path of the untracked copy (see the trap below); the three db-pool-idle-txn citations use the bare filename. At the merge tip the same 7 citations across the same 5 files still stand, and now resolve to a tracked file.

**What landed.** `.agents/shared/analysis-20261026-v0278-soak-failure.md` — 203 lines, sha256 `405f4c717ad7e7b8a250fb7041bf1ebda52ac32760e11bd5d8e9ec28f9e732d0` (identical at branch tip `991c41e8c` and merge tip `40301a5e3`).

**The verdict doc.** `.agents/tester/RESULTS/2026-10-28-soak-gate-mcp-warmup-functional-gate.md`: base held the PARTIAL 149-line copy (sha256 `1f432168001384caf20ebe508965865a308ba6ab27ef516b53effc30702a34e0`; "Commission: PARTIAL — Gate 3 base-side digest incomplete"); landed the COMPLETE 166-line copy (sha256 `7500b620d56bbc3859049efd9e51c230d60301672f9563481e1e80499b146634`; "Commission: COMPLETE — Gates 1, 2, 3, 4 all delivered", base digests present). The verdict's **F-11 row exists only in the 166-line version** (0 `F-11` mentions in the 149-line copy; 1 in the COMPLETE copy, line 156): *"Secrets static pin counts the OLD seam (`EXPECTED_SPAWN_SITES` connection_manager×2) — resolution alive behind `resolve_spawn_env`; re-pin to the new seam, NEVER re-add direct calls (double-resolve)"*.

**Source discipline.** Bytes copied verbatim from the byte-verified evidence tree `/home/nea/agents-ensemble/data/evidence/branch-preserve-2026-11-25/` (`MANIFEST-sha256.txt` rows 56–57: `d-feature-soak-gate-mcp-warmup/.agents/shared/analysis-20261026-v0278-soak-failure.md` → `405f4c71…`; `d-feature-soak-gate-mcp-warmup/.agents/tester/RESULTS/2026-10-28-soak-gate-mcp-warmup-functional-gate.md` → `7500b620…`) and cross-checked against the branch tip: manifest == disk == branch == landed (re-verified: the two evidence-tree disk files hash to exactly those values, 203 and 166 lines).

**The trap.** A THIRD copy exists in the canonical checkout — `/home/nea/ensemble-src/.agents/shared/analysis-20261026-v0278-soak-failure.md`, untracked (`??`), 138 lines, sha256 `d109adf2208b6c2ee1be3adfcdb77c333a911fe873fc55890834526c834bf627` — the pre-appendix version, deliberately NOT used.

## 2. The F-11 static re-pin (RED-at-base / GREEN-at-head)

**File:** `tests/unit/test_secret_do_not_touch_lane_static.py`.

**What was stale.** `EXPECTED_SPAWN_SITES` already carried `("daemon/mcp/warmup_pool.py", 1)` and `("daemon/mcp/connection_manager.py", 2)` (unchanged by the lane). The STALE element was the COUNTER `_count_resolve_calls_in_module`, which at base counted `ast.Call` nodes named `resolve_secret_markers`. `daemon/mcp/connection_manager.py` contains ZERO occurrences of that literal (`git grep -c resolve_secret_markers dd007f16d -- daemon/mcp/connection_manager.py` → rc=1, at base AND at the merge tip) because it dispatches to the shared helper instead — so the pin computed `0 >= 2` and was GENUINELY RED at base.

**Production truth (at the merge tip).** All three spawn sites pass `resolve_spawn_env` as an ARGUMENT to `run_in_executor`:

- `daemon/mcp/connection_manager.py:216` and `daemon/mcp/connection_manager.py:497` — `SECRET_RESOLVE_EXECUTOR, resolve_spawn_env, config`
- `daemon/mcp/warmup_pool.py:588` — `SECRET_RESOLVE_EXECUTOR, resolve_spawn_env, config`
- helper defined at `daemon/mcp/warmup_pool.py:121` (`def resolve_spawn_env(config: McpStdioConfig) -> dict[str, str] | None`); its body's single `resolve_secret_markers(` call spans lines 149–154.

`connection_manager.py` is NOT dead code for this path.

**The landed fix** (`263029e83`, +107/−6) is a COMPOSITE implementing BOTH clauses of the COMPLETE verdict's F-11 row:

- **Clause 1 — "resolution alive behind `resolve_spawn_env`".** `_count_resolve_calls_in_module` now counts `ast.Name` LOADS of `resolve_spawn_env` (the helper is handed to `run_in_executor`, never called, so a Call-node count would be 0 in BOTH files and the pin vacuous). `EXPECTED_SPAWN_SITES` is therefore UNCHANGED.
- **Clause 2 — "NEVER re-add direct calls (double-resolve)".** New `_count_direct_resolve_calls_in_module` + new test `test_resolution_lives_only_behind_the_helper`, parametrized over the two `EXPECTED_SPAWN_SITES` modules: a non-helper module must own ZERO direct `resolve_secret_markers` calls; the helper module must own EXACTLY ONE, and it must sit INSIDE `resolve_spawn_env` (`_resolve_calls_in_function`).

**BASE RED at `dd007f16d`** — the stale-expectation assertion itself, NOT a collection or import error. Message rendered from the base source (lines 193–197):

```
AssertionError: daemon/mcp/connection_manager.py lost its spawn-point resolve_secret_markers call(s): expected >= 2, found 0 — the §1c spawn-point design regressed
assert 0 >= 2
1 failed, 9 passed
```

Exactly one `^FAILED` node: `TestSpawnPointsStillResolve::test_spawn_site_resolution_present[daemon/mcp/connection_manager.py-2]`.

**HEAD GREEN at `991c41e8c`: 12 passed.** Census = 12 nodes, mechanically consistent with the source: the base file collected 10 (3 `test_no_p2_resolver_needles` over `DO_NOT_TOUCH_FILES` + 1 + 1 + 2 spawn-site + 3 detector-sensitivity); the 2 `test_resolution_lives_only_behind_the_helper` nodes are new (7 → 8 test functions).

**MUTATION PROOF — three regressions, three DISTINCT catchers** (this is what makes the composite non-redundant):

| Leg | Mutation | RED node | Verbatim message | The other clause |
|---|---|---|---|---|
| M1 | double-resolve re-added in `connection_manager.py` | `test_resolution_lives_only_behind_the_helper[daemon/mcp/connection_manager.py]` | `daemon/mcp/connection_manager.py: resolution must live ONLY behind resolve_spawn_env -- 1 direct resolve_secret_markers call(s) found (double-resolve)` | dispatch-count clause stayed GREEN at 2 |
| M2 | `resolve_spawn_env` body gutted in `warmup_pool.py` | `test_resolution_lives_only_behind_the_helper[daemon/mcp/warmup_pool.py]` | `daemon/mcp/warmup_pool.py: the shared helper must own EXACTLY ONE resolve_secret_markers call, and it must sit INSIDE resolve_spawn_env; found 0 module-wide / 0 inside resolve_spawn_env` | dispatch-count clause stayed GREEN |
| M3 | one dispatch lost in `connection_manager.py` | `test_spawn_site_resolution_present[daemon/mcp/connection_manager.py-2]` | `daemon/mcp/connection_manager.py lost its spawn-point resolve_secret_markers call(s): expected >= 2, found 1 — the §1c spawn-point design regressed` | — |

M2 proves the dispatch-count clause ALONE cannot catch a gutted helper (the dispatch survives while the resolution is deleted). M1 proves it alone cannot catch a double-resolve (a re-added direct call ADDS an `ast.Call` node and leaves the `ast.Name` loads untouched — the inversion the reviewer measured on the leader's original counter shape). Every mutant restored; porcelain empty after each; final re-run 12 passed.

**NO-COLLATERAL.** `_resolve_calls_in_function` and `FORBIDDEN_NEEDLES` are byte-identical to base (re-verified by extracting both from `dd007f16d` and `991c41e8c` and `cmp`-ing: identical; the only diff line naming `_resolve_calls_in_function` is the NEW call site). The section-1c detector still fires: pristine GREEN → injected direct `resolve_secret_markers` call into `build_pooled_stdio_config` → RED on `TestPooledStdioConfigStaysStored::test_no_resolve_secret_markers_call_in_builder`; 6 collateral detector nodes green (tester-run).

## 3. The M22 plant-drift rider

**The drift.** `tests/helpers/preflight_mutation_apply.py:282` planted the literal `PIPELINE_SCRIPTS_REV=087f782afbf1957dd0bf6acea5198314093d2a89`, while `scripts/upgrade/lib.sh:95` (`PIPELINE_SCRIPTS_REV=46d55b002d35092c29c391fc76e6b897c0819e11`) and `daemon/tools/upgrade_tools.py:4610` (`PIPELINE_SCRIPTS_FLOOR_COMMIT = "46d55b002d35092c29c391fc76e6b897c0819e11"`) were BOTH `46d55b002d35092c29c391fc76e6b897c0819e11` (three re-pins stale, per the dispatch). Production pairing was CONSISTENT, so the live arm was unaffected — but any lane consuming the plant asserted a stale floor.

Staleness re-derived from `git log -L95,95:scripts/upgrade/lib.sh`: the plant literal was set at `fe362aedd` (2026-11-12, "stamp re-pin + M22 coupling refresh to 087f782af") and never refreshed in the helper since (pickaxe: no intermediate stamp value ever appeared in the helper). The stamp then moved SIX times underneath it: `087f782af` → `68c6ee055` (`3f45cd6dd`, 2026-11-14) → `1aa0065eb` (`f8dc874f7`, 2026-11-15) → `70a0960e5` (`b66c0eab5`, 2026-11-19) → `e8695f173` (`96cc3855e`, 2026-11-20) → `32fa64735` (`5aa5d01ae`, 2026-11-22) → `46d55b002` (`4becc1de5`, 2026-11-22). Three of the six were `chore(release)` pair bumps.

**Why a literal refresh, not derivation.** The helper's own guard comment (lines 279–281 at head) makes exact-byte pinning plus a loud ERROR-plant a deliberate drift detector — *"re-pin coupling: when PIPELINE_SCRIPTS_REV is re-pinned, refresh this literal in the SAME change — plant literals pin exact bytes and ERROR-plant loudly on drift (see .agents/tester/PACKS.md matrix row; precedent dec3a82c2/M8)"*. Runtime derivation would defeat the loudness; the cure is a LITERAL REFRESH in the SAME change (`0e2b02bac`, one line).

**BASE RED — the drift was loud, not silent** (the helper raises `SystemExit` on a non-unique match, which exits 1):

```
PLANT-FAILED: scripts/upgrade/lib.sh pair#1 matched 0 times (want exactly 1) — the source drifted; the mutation leg would be vacuous
```

exit 1.

**HEAD** (`0e2b02bac`):

```
PLANTED M22 in scripts/upgrade/lib.sh (1 pair(s))
```

exit 0; line 95 flips to `# PLANT M22: stamp line removed (a stale tree predates the stamp)`, proving the mutation stays non-vacuous. Pack-level: `M22 ok / red_rc=1 / green_rc=0 / DEFERRED_RED C1`, `M22: PASS (rc=1)`, `MUTATION MATRIX: PASS`. Provenance: anomaly `d40816dc`, critical note `04b5117f`.

## 4. Post-merge version assertion (Ari, terminalization lane)

The five version-bearing files were DERIVED from `scripts/bump_version.py` (the tool that mints a version is the authority on which files carry it — it names `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock`, `CHANGELOG.md`), never hardcoded. Re-derived at the four refs:

| Ref | `pyproject.toml` | `daemon/__init__.py` | `tests/test_api.py` | `uv.lock` (`name = "ensemble"`) | `CHANGELOG.md` head |
|---|---|---|---|---|---|
| base `dd007f16d` (V_base) | 0.31.55 | 0.31.55 | 0.31.55 | 0.31.55 | `## [v0.31.55] — 2026-11-25` |
| branch tip `991c41e8c` (V_branch) | 0.31.55 | 0.31.55 | 0.31.55 | 0.31.55 | `## [v0.31.55] — 2026-11-25` |
| latest parent `f418bdd6e` (V_latest) | 0.31.58 | 0.31.58 | 0.31.58 | 0.31.58 | `## [v0.31.58] — 2026-11-25` |
| merge tip `40301a5e3` | 0.31.58 | 0.31.58 | 0.31.58 | 0.31.58 | `## [v0.31.58] — 2026-11-25` |

At the merge tip all five agree at **0.31.58**, and that equals max(V_base 0.31.55, V_branch 0.31.55, V_latest 0.31.58) = 0.31.58 → **PASS** (no split-brain, highest slot). The lane carried no version file (`git diff --name-only dd007f16d 991c41e8c` over the five = empty); the merge correctly took origin/latest's own bump.

**Transparency.** The assertion's FIRST run reported FAIL. That was a defect in the CHECKER (it assumed `CHANGELOG.md` carries an `[Unreleased]` header and read `heads[1]`; this repo has none, so the second-newest release was read). Nothing was pushed before the corrected check passed; the five values were then re-verified against the PUSHED tree.

**Note.** `git describe --exact-match HEAD == VERSION` is NOT applicable at a merge commit (a merge tip is never an exact tag match — `git tag --points-at 40301a5e3` is empty) — that leg belongs at the bump/tag step only.

## 5. Provenance and residuals

**Produced by:** tester instance `0e7ea97e-504c-4848-a39d-2b6e5a61781d` (independent gate, verdict PASS) and reviewer instance `9e18a802-c81f-4fa9-9d42-e0b5cc882f08` (Part 1 shape adjudication APPROVE-WITH-CHANGES, Part 2 re-gate APPROVE). Both verdicts were recovered from instance transcripts because both instances hit the known revive-wedge (a revived child that already delivered a report cannot terminalize or deliver a second) and their reports were never delivered.

**The reviewer's catch.** A REAL defect in the leader's original directive: the first mandated counter shape would have been GREEN on the double-resolve regression (measured inverted), because re-adding a direct call ADDS an `ast.Call` node without touching the `ast.Name` dispatch nodes. The composite fixes it (M1 above).

**Residuals:**

1. The F-11 pin file was in no registered PACKS.md row before this lane (`git grep -n test_secret_do_not_touch_lane_static dd007f16d -- .agents/tester/PACKS.md` → rc=1). The row landed here (`53252119f` + `991c41e8c`, +18 lines, row-only, no pack script), and the census 12 was independently confirmed by the tester, the reviewer, and the row's own Scope cell ("12 nodes = 8 test functions in 4 classes: byte-clean 4 · stored-config 1 · spawn-site + clause-2 companion 4 · detector-sensitivity 3").
2. The "warmup_pool appears in ZERO latest test files" premise in the original brief was wrong — it appears in 24 (`git grep -l warmup_pool 40301a5e3 -- tests/ | wc -l` = 24).
3. The leader's dispatch text carried a base-sha typo (`…cdb755` for `…cdb775`) which the tester workers caught and refused to proceed on rather than verifying the wrong tree.
4. The lane worktree was created with no fenced `.env` (anomaly `76d5f305`, minor) — harmless here, tests only, through the scrubber.

**Filing note.** Where the dispatch text and git disagreed, git was written and the discrepancy reported to the dispatcher at filing time: (a) the base-RED assertion renders `§1c`, not `section-1c`; (b) the M22 plant was stale through SIX stamp transitions (three of them `chore(release)` bumps), not three.
