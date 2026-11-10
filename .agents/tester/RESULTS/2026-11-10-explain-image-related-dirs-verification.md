# TEST VERIFICATION — explain_image related-dirs path confinement (pre-merge gate)

- **Date**: 2026-11-10
- **Gate subject**: `fix/explain-image-related-dirs` @ HEAD `0efd07f038f3a4633a8f42b3f1651dae8c14facc`, base `9969f0c9b` (11 files, +568/−59)
- **Worktree**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-explain-image-related-dirs`
- **Dispatcher**: tester (Test Leader); 6 workers, all read-only on git, tree left clean, HEAD unmoved
- **VERDICT: ✅ SHIP** — every dev claim independently confirmed; zero branch-caused reds; acceptance criterion proven end-to-end in both directions; no fail-open anomaly.

---

## 1. Scope & tier decision (ensure.md doctrine)

- **Tier 1** (feature-lane default): diff touches `daemon/tools/{image_tools,compare_tools}.py` — NOT in the Tier-2 core-seam prefix list (`graph.py`, `manager.py`, `api.py`, `instance_messaging.py`, `task_processor.py`, `job_queue_service.py`); not a release/promote gate. No whole-tree leg warranted.
- **Two-class declaration** (blast-radius-leg recipe):
  - **D1 (lane-owned, HEAD-only pins)**: `TestExplainImagePathScope` ×12 in `tests/test_image_tools.py`; `TestWorkdirScopeIncludesRelatedDirectories` ×2 in `tests/test_compare_tools.py`. Rule: MUST PASS at HEAD — **confirmed 14/14**. Non-vacuity additionally proven by execution at base (see §2/§4).
  - **D2 (dual-side comparable)**: `tests/unit/agents/test_aegis_security_family.py` (branch-widened pin, exists both revs) and `tests/unit/tools/test_prompt_section_reference_integrity.py` (unchanged file, corpus-level comparability). FAILED-digest diff **zero delta** on both (§4).
- **Pack/wrapper baseline**: no standing PACKS.md pack covers these files at this HEAD (aegis/prompt-integrity appear only in other lanes' commit-scoped rows — canary_p5, v02812_staged_tree_pack, sweep_tools_A); prior verification gates in this repo used targeted scrubbed pytest of changed files — same shape used here, via `scripts/run_tests_scrubbed.sh` + outer `timeout 300` + per-test 30s (dual layer). No pack scripts were created ⇒ no PACKS.md rows owed (PACK CONTRACT: rows match script reality).
- **Quarantine check**: QUARANTINE.md has zero active rows touching these suites — no skip masking; `-ra` skip ledgers were empty in all four suite runs (source-level grep proof: no skip/skipif/xfail in the two changed test files).
- **Process disclosure**: the blast-radius-leg recipe body was loaded at aggregation time, not before leg dispatch (order deviation). Legs nevertheless satisfied its substance: scoped sets, slicing proofs (`--collect-only` census per suite), `FAILED|ERROR` digest discipline, per-run env scrub (ambient PROD `POSTGRES_*`/`PLANE_*`), total wall-clock far under the 30-min Tier-1 budget.

## 2. Closing test — ORIGINAL SYMPTOM SHAPE (mission acceptance criterion) — ✅ PROVEN

Probe `/tmp/ei_gate/closing_probe.py` (real SQLModel `Project` — `main_directory: str|None`, `related_directories: list[str]` — real production chain `create_image_tools → _get_project_scope_roots → explain_image → _load_image_from_path → _validate_local_path`; only repo `.get()` plumbing faked; dispatch sink `invoke_agent_and_wait` AsyncMocked; scrub proof `leaked_vars: []`):

- **HEAD**: image deep under a related dir → **ACCEPT**, dispatch=1 (`MOCK-READER-REPLY`). Image outside all roots → **REFUSE**, message names ALL roots + remedy ("Remedy: copy the image under one of those directories, or add its directory to the project's related directories."), dispatch=0.
- **BASE `9969f0c9b` (executed, not just quoted)**: base module run against the identical synthetic project → closing shape **REFUSED** ("outside the project workdir boundary", dispatch=0) while under-main control **ACCEPTED** — pre-fix bug reproduced. Base code evidence: `_get_project_workdir` returned `main_directory` only (base `:465-477`); single-workdir containment (base `:295-310`); zero `related_directories` references in base file.
- **PROBE: PASS 14/14** (3 runs, rc=0, 3.0s; inner alarm 240s + outer 300s never engaged).

## 3. Edge cases — ✅ no crash, no fail-open

| Case | Outcome | Note |
|---|---|---|
| empty `related_directories` | main-only ACCEPT; former-related path REFUSED | no fail-open |
| related == main (dedup) | ACCEPT, roots deduped | |
| non-existent related root | root **dropped** (skip-root), remaining roots still confine, outside still REFUSED | `_resolve_scope_roots` isdir gate — not fail-open |
| path IS a related root (dir) | REFUSE "Path is not a regular file" | containment inclusive; S_ISREG gate closes boundary — cross-validated by source audit |
| traversal `../` (to real outside file) | REFUSE **on containment** | 08b strict variant |
| symlink escape | REFUSE (resolved side) | |
| unicode / space paths | ACCEPT inside / REFUSE outside | |

## 4. Suite results — claims vs measured (all scrubbed, dual-layer timeout)

| Suite | Claim | Measured | Verdict |
|---|---|---|---|
| `tests/test_image_tools.py` | 113P | **113P/0F/0S** in 1.30s; 12/12 PathScope executed+passed (traversal red, symlink-escape red, both fail-closed cases, e2e accept, e2e refuse-without-spawning) | ✅ CONFIRMED |
| `tests/test_compare_tools.py` | 78P | **78P/0F/0S**; 2 new parity cases executed; **non-vacuity: base impl + branch tests overlay → 76P/2F, failures exactly the 2 new ids (`workdir_invalid`)** | ✅ CONFIRMED |
| `tests/unit/agents/test_aegis_security_family.py` | 555P/1F | **555P/1F** in 41.73s; failing node `TestScannerExecution::test_t38_three_cwd_byte_identical` | ✅ CONFIRMED; red = **PRE-EXISTING/ENVIRONMENTAL** |
| `tests/unit/tools/test_prompt_section_reference_integrity.py` | 5 reds | **5F/1630P/0S** in 3.72s | ✅ count CONFIRMED; all 5 **PRE-EXISTING** |

**A/B attributions (evidence):**
- **aegis t38**: (i) branch's only hunk in the file = +6 lines in `TestWorkerUntouched` expected-file set (`grep t38|three_cwd` over diff → 0 hits); (ii) solo traceback: `_release_root()` → `None` because `~/agents-ensemble/releases/` absent on this host — the test's own A7 comment mandates loud failure there, never silent degradation; (iii) real-history base leg (`git clone --shared` @ `9969f0c9b`, import-proof severed from WT): **1F/555P, identical failing-id set**, failure line shifted 4410→4416 exactly matching the +6 hunk. Zero branch delta.
- **prompt-integrity 5**: (i) each subject file NOT in branch diff, `git diff base..HEAD -- <subject>` EMPTY, blob SHAs identical (`ea144bff…` ×2, `d6ccb0d1…`, `4e295073…`, `38a04ba9…`); (ii) independent base leg (`git archive` @ `9969f0c9b`, .pth+shebang rewrite, import proof): 5F/1630P, normalized logs byte-identical both legs (sha `549f04f5…`). Branch-touched prompt files (image-reader rule/soul, tools_note ×3) all in prompt-surface scope and all PASS.
- **Claim-detail correction (identity, not count)**: the dispatch's "known set" description misidentified 3/5 prompt-integrity nodes — `[memory.md*]` reds do not exist (0 `memory.md` files under `agents/`); the developer pair is bare-md (`design/design-spec.md`), the charter pair is odd-fence. Count 5 and attribution unaffected.

## 5. Mock fidelity — ✅ FAITHFUL (no live fiction)

- Production shape (`daemon/repositories/project/models.py:393,395-398`): `main_directory: str|None`, `related_directories: list[str]` (JSONB); two-hop instance→project lookup (`image_tools.py:579-581`). Suite mock `_scope_manager` (`tests/test_image_tools.py:807-826`) mirrors field names, types, object-access pattern, and both hops; read-only probe confirmed the production collector genuinely consumes the mock.
- **URL/SSRF branch UNTOUCHED — proven**: AST body comparison base↔HEAD identical (`_ensure_public_url`, `_load_image_from_url`); no diff hunk inside the SSRF branch; URL dispatch line appears only as context.
- **compare parity — OK today**: union collection duplicated (`compare_tools.py:819-827` ≡ `image_tools.py:585-592`), containment logic shared via `_validate_local_path`; no behavioral divergence found.

## 6. Fail-closed negatives & no-dispatch pins

- All 12 new PathScope tests always execute (no skip/xfail/skipif anywhere in the file; asyncio_mode=auto).
- E2E refuse case pins `mock_invoke.assert_not_awaited()` (`:1064`); e2e accept pins `assert_awaited_once()` (`:1039`) — green-controlled. Closing probe: every REFUSE → dispatch sink 0 awaits; every ACCEPT → 1 await.
- **Gaps (non-blocking)**: `unresolvable scope roots`, `traversal escape`, `symlink escape` have no tool-level (E2E) no-dispatch twin; `unresolvable_scope_roots` unit asserts only `err is not None` (does not pin the fail-closed marker, unlike its sibling `:926`).

## 7. ensure.md validation (scoped to blast radius)

- **Critical — no regressions in changed packs**: image + compare suites PASS at HEAD (D1 14/14); D2 reds all base-attributed (§4) ⇒ **PASS** (pre-existing reds per quarantine doctrine, not left to red the gate).
- **Critical — concurrency_atomic_unit_test / sync-DB-on-loop**: OUT OF SCOPE — diff touches no concurrency/lock/event-loop seam (tools layer only; the repo-lookup call pattern predates the branch unchanged).
- **Critical — dev.sh graceful-shutdown flag**: OUT OF SCOPE — `dev.sh` untouched by the 11-file diff.
- **Important — await-correctness of the 3 named functions**: OUT OF SCOPE — none touched.
- **Nice-to-have — no dead code from the fix**: satisfied by inspection — old single-workdir validator fully replaced (base file has no `related_directories`; HEAD collector supersedes `_get_project_workdir`).
- Release Gate: not triggered (Tier 1 lane, no core seam).

## 8. Gate hygiene

- All 6 workers: porcelain pre/post EMPTY, HEAD `0efd07f03` unchanged, no commits/merges/pushes/ref mutations/stash/checkout, no daemon start/stop, no make/deploy contact, no `~/agents-ensemble` mutation (read-only `ls`/`stat` only), no network. All pytest through `scripts/run_tests_scrubbed.sh`; import proofs inside the correct tree on every leg (incl. hardened pytest-session proof + negative control on the compare leg).
- Disclosed byproducts: gitignored `.pytest_cache` + `__pycache__` writes inside WT by solo runs (two workers; attributed by content+mtime; left in place, tree porcelain-clean). One transient base-leg harness fault (`ModuleNotFoundError: _pytest`, pre-test, non-reproducing) — no test outcome derived from it.

## 9. Non-blocking follow-ups (test debt — for a future test lane, none blocks this merge)

1. 🟠 Add E2E no-dispatch twins for the 3 unpinned negative cases (unresolvable roots, traversal, symlink escape); pin the fail-closed marker in `test_unresolvable_scope_roots_fail_closed` (parity with `:926`).
2. 🟠 Union-collector branch coverage: 10/12 new tests bypass `_get_project_scope_roots` (only 2 e2e tests exercise it) — untested branches: non-list `related_directories` drop, blank/None `main_directory` with related present, relative-anchor shift when primary root missing.
3. 🟢 Extract the duplicated union collector (image vs compare) into a shared helper + cross-file parity pin.
4. 🟢 Mock hardening: pin `manager._project_repository.get` call key (`assert_called_with(project_id)`); replace MagicMock-tolerant project mock pattern (G2); guard `_scope_manager`'s `list(related_directories)` str-coercion trap (G1).
5. 🟢 Pin the path==root boundary outcomes (file-root → fail-closed; dir-root → "not a regular file").
6. 🟢 Weak message-form pin (`assert str(main) in err` matches declared or resolved form on macOS `/private/var` vs `/var`).

## 10. Methodology lessons (see LESSONS/2026-11-10-ei-gate-base-leg-methodology.md)

- `git archive` base legs are INVALID for suites that shell out to git (aegis `test_worker_untouched` runs `git diff` → phantom red `Could not access '1f6898ae...'`); fix = `git clone --shared` + detach. Archive legs remain fine for pure-FS suites (prompt-integrity reproduced exactly).
- Non-vacuity overlay recipe (base impl + branch tests) proves new tests gate the change (76P/2F with failures exactly the new ids) — worker codified it as skill `new-test-red-at-base-non-vacuity-overlay`.
- Solo re-runs need `-p no:cacheprovider` too; pytest-session import proof > `python -c` proof.

## 11. Worker / artifact ledger

| Node | Worker | Skill | Key artifacts |
|---|---|---|---|
| n1 image | `ad4e9108` | test-pack-execution | `/tmp/ei_gate/w1_image_tools.log` (sha `8266c9aa…`), collect.log |
| n2 compare | `b2359b7a` | test-pack-execution | `/tmp/ei_gate/` w2 logs + base_tree overlay |
| n3 aegis | `01a7eabb` | test-pack-execution | `/tmp/ei_gate/w3_*`, `/tmp/ei-base-git/` |
| n4 prompt-int | `61aba667` | test-pack-execution | `/tmp/ei_gate/w4_*` (`.sig` pairs), `/tmp/ei-base` |
| n5 fidelity | `31b7aa64` | — (analysis) | `/tmp/ei_gate/w5_notes.md`, `probe.py` |
| n6 closing | `c996ffcf` | mock-test | `/tmp/ei_gate/closing_probe{,.log,.run1,.run3}`, `base_proof.log`, `image_tools_BASE.py` |

Operational note: POK store hit its 64-active-entry cap during this gate (two workers' `pok_upsert` refused; fell back to `experience()`; anomaly `417b48dd` filed). Curation/archiving is leader-only.
