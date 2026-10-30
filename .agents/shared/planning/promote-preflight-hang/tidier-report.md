# Tidier Review — promote-preflight-hang (board wi-fc1786a2)

**Verdict: NEEDS WORK — PARTIAL COVERAGE (1 of 3 lanes delivered)**
**Findings: 29 — 1 High, 12 Medium, 16 Low**
**Iteration:** 001
**Diff:** `b1183a637..f76765e6a` @ `f76765e6a`, worktree `/home/nea/ensemble-src-wt-preflight-hang`
**Reviewed:** 2026-10-30

---

## ⚠️ Coverage warning — read before acting

Three skill lanes were dispatched in parallel. **Only the static-hygiene lane reached a report.**

| Lane | Skill | Outcome |
|---|---|---|
| `w-hygiene` | `tidier-static-hygiene` (File Hygiene + Type Cleanliness + Test Organization) | ✅ **delivered** |
| `w-readable` | `tidier-readable-code` (Coding Style + Code Smells + Readability) | ❌ **NO COVERAGE** — original instance died twice on upstream rate limit, replacement died, revive died |
| `w-robust` | `tidier-robustness` (Error Handling + error-message clarity) | ❌ **NO COVERAGE** — original died twice, replacement died on its first provider call, zero findings produced |

Every failure was `execution_error: Upstream request failed: Rate limit exceeded` (provider-side), never a task or code failure. The per-node revive budget (one) and the one-replacement ladder were both fully consumed on both lost lanes before stopping.

**Unverified surface (must not be read as clean):**

- `scripts/upgrade/lib.sh` (+854) — wrapper/trap/rc behaviour, and the **`set -euo pipefail` × bounded-wrapper interaction**, which is the commission's own highest-priority focus area
- `scripts/upgrade/{promote,stage,status}.sh` — control flow, refusal paths, message clarity
- `scripts/upgrade/journal_read.py` — sentinel/exit-code contract, exception hygiene
- `daemon/tools/upgrade_tools.py` — bounded git probe sentinel distinguishability, guard/knob validation
- `daemon/tools/upgrade_journal.py`, `daemon/services/upgrade_journal_sweep.py` — swallow paths, refusal vocabulary
- The 25 converted call sites — uniformity and greppability (readable lane)

**Recommendation:** re-run the `tidier-readable-code` and `tidier-robustness` lanes on this same commit once the provider limiter clears, before the lane is treated as craftsmanship-reviewed. The shell surface is where this commission's risk concentrates, and it is the surface with no coverage.

---

## Scope

Commission focus 1 (shell hazards / `set -euo pipefail` / naming / 25-site routing / comment accuracy), focus 2 (Python single-responsibility, dead code, exception hygiene, contracts, logging), focus 3 (test pack organization, fixture reuse, mutation-runner structure, diagnosability), focus 4 (docs/comments: stale or overstating claims), focus 5 (maintainability of the +1,958 lines).

Only focus 3 and the type-hygiene/file-size parts of focus 2 received coverage.

---

## Findings

### 🔴 High

#### File Hygiene

**H1. `lib.sh` is >2× the refactor threshold and its "why it is this big" rationale is stale and silent about this lane's growth — `scripts/upgrade/lib.sh:24`**
- **Problem:** File is now **6,640 lines**; the `>3000 ⇒ flag for refactor` threshold is met. The only compliance artifact is the `# Size rationale: ~5560 lines …` block at `:24`, understating by ~1,080 lines. This lane added **+854 lines / 19 functions** (`_log*`/`_log_write_failed` `:296-486`, `_run_bounded_step:486`, `_pipeline_scripts_stamp_verdict:534`, `_json_sub_legacy:914`, `_journal_read_helper_path`/`_journal_read_probe`/`_journal_read_legacy`/`_journal_read_fallback`/`journal_read` `:1324-1500`, `_run_log_retention:6297`) without refreshing the count and without adding a split candidate to the `BACKLOG (a)(b)(c)` list at `:33-39`.
- **Impact:** the next author believes the file is 5.5k lines and that only three extraction seams exist; the two largest *new* families are invisible. This is the exact failure the rationale exists to prevent.
- **Fix:** refresh `:24` to `~6640 lines (+854 for the promote-preflight-hang journal/logging work)`; append two candidates to `:33-39`: **(d)** journal-read family `lib.sh:1324-1500` (`_journal_read_helper_path` → `journal_read`) **+** `_json_sub_legacy:914` → `scripts/upgrade/journal_read_lib.sh` (all five consumers source `lib.sh`, so the sourcing line stays byte-identical; the parity pack's `git show … :lib.sh` oracle extraction needs a one-line path update); **(e)** per-run logging family `:296-486` + `_run_log_retention:6297` → `scripts/upgrade/log_lib.sh`. Do **not** attempt (a)/(b)/(c) in the same pass.

### 🟡 Medium

#### File Hygiene

**M1. Size-rationale counters drifted on all three modules this lane grew — `upgrade_tools.py:62`, `upgrade_journal.py:90`, `upgrade_journal_sweep.py:46`** (plus pre-existing `manager.py:220`)
- **Problem:** `upgrade_tools.py:62` claims `~3931` (actual 4,125); `upgrade_journal.py:90` claims `~3433, refreshed 2026-10-19` (actual 3,667); `upgrade_journal_sweep.py:46` claims `~2300` (actual 2,388); `manager.py:220` claims `~13990` (actual 15,317 — pre-existing drift, +24 is this lane's).
- **Impact:** in a 1000–3000+ file the rationale is the *only* thing making it threshold-compliant; a wrong number turns it into decoration and lets the next increment pass unnoticed.
- **Fix:** update the four counts to post-diff values and add one clause each naming what grew (e.g. `upgrade_tools.py:62` → "`~4125` — +230 for the phase-6 scripts-dir provenance guard, appended at the END with a load-bearing position constraint (see `:3905`)").

**M2. Three docstrings reference a fixture file that does not exist — `tests/fixtures/journal/CORPUS.md:7`, `tests/helpers/journal_fixture_corpus.py:14`, `:186`**
- **Problem:** all three say verdicts are "CAPTURED from the BASE bash scanner … and frozen into `EXPECTED.json`". No `EXPECTED*` file exists (`find . -name 'EXPECTED*'` → empty), because `preflight_journal_parity_test.sh:37-56` captures live via `pf_base_oracle` + `cmp_verify` — the differential *is* the oracle.
- **Impact:** a future author will hunt for the file, and the natural "fix" — actually freezing verdicts — converts the differential into a self-fulfilling golden and weakens the pin.
- **Fix:** replace with "captured live from a verbatim BASE-body oracle each run (deliberately no frozen expectation file — a freeze would make the differential self-fulfilling)" in all three; the string is generated at `journal_fixture_corpus.py:186`, so fix the source and regenerate `CORPUS.md`.

**M3. Two of 24 helpers carry no annotations and no `from __future__ import annotations` — `tests/helpers/journal_fixture_corpus.py:1`, `tests/helpers/journal_fixture_incident.py:1`**
- **Problem:** 20 of 24 sibling helpers (and both new helpers this lane added — `preflight_ceremony_probe.py:42`, `preflight_mutation_apply.py:42`) begin with that import; these two leave 11 signatures bare (`_pad_to(size, seed=…)`, `write_all(out_dir)`, `build()`, `build_stats()`, `main(argv)`).
- **Impact:** intra-lane inconsistency in one directory; `build()` is explicitly advertised as importable (`journal_fixture_incident.py:24`) — an importable entrypoint with no return contract needs the type most.
- **Fix:** add the import and annotate the 11 (`build() -> bytes`, `build_stats() -> dict[str, int]`, `write_all(out_dir: str | Path) -> list[Path]`, `main(argv: list[str]) -> int`).

**M4. 295 KB of generated near-identical fixtures committed while the larger one is runtime-generated — `tests/fixtures/journal/G1..G4-guard-*.json`**
- **Problem:** 98,303/98,304/98,305/98,305 B of generator output committed, while the 455 KB incident fixture is deliberately *not* committed and built at runtime (`journal_fixture_incident.py:16-19`). The generator is in-tree with `--out DIR` (`journal_fixture_corpus.py:204`).
- **Impact:** duplicated blobs plus a "do not hand-edit" hazard, for files every consumer regenerates anyway.
- **Fix:** generate G1–G4 into the pack's scratch dir at run time (the size-guard pack already owns one); keep only the ≤2 KB corpus committed. Judgement call — the counter-argument is documented at `CORPUS.md:3`.

#### Type Cleanliness

**M5. `scripts_dir_provenance_verdict` returns a stringly-typed closed vocabulary — `daemon/tools/upgrade_tools.py:3972`**
- **Problem:** `-> tuple[str, str]` where element 0 is exactly `{"arm","warn","refuse"}`, compared by equality at `:3717`/`:3743`. The repo uses `Literal` in **41 files** (`daemon/config.py:14`, `daemon/tools/comment_edit.py:40`, …).
- **Impact:** a typo (`"refused"`) compiles, reviews clean, and silently takes the **arm** branch — the fail-closed guard degrades to fail-open.
- **Fix:** `ScriptsProvenanceVerdict = Literal["arm","warn","refuse"]`; annotate `-> tuple[ScriptsProvenanceVerdict, str]`; type the `prov_verdict` local in `system_upgrade` accordingly.

**M6. Enriched reaper row is a bare 3-tuple of stringly sentinels — `daemon/services/upgrade_journal_sweep.py:2240`**
- **Problem:** `_per_run_observation(...) -> tuple[str, str, str]` returns `(narrative_path_str, last_phase_str, elapsed_str)`, every element encoding "absent" as a string (`"(no per-run narrative)"`, `"unknown"`, `"unknown"`). The same module already uses `@dataclass(frozen=True)` for this shape (`ReaperJob:117`, another at `:377`); `upgrade_journal` uses `NamedTuple` (`SupervisionOutcome:3370`).
- **Impact:** three positional strings, two of them "maybe a number"; a caller cannot distinguish `"0s"` from `"unknown"` except by string compare, and a 4th field silently reorders every call site.
- **Fix:** `class PerRunObservation(NamedTuple): narrative: Path | None; last_phase: str | None; elapsed_s: int | None`, keeping the `"unknown"` rendering in the f-string at `:2355`.

**M7. Duplicated per-run-file resolver with already-drifted validation — `daemon/tools/upgrade_tools.py:942` vs `daemon/services/upgrade_journal_sweep.py:2226`**
- **Problem:** two implementations of "glob `data/upgrade/<env>/<run_id>.log` by run_id": `upgrade_tools._resolve_per_run_log` (`:942`, validates `run_id` against `[A-Za-z0-9._-]+`) and `UpgradeJournalSweepService._per_run_narrative_path` (`:2226`, **no** validation, `Path` not `Path | None` for `install_dir`). Both modules already import `upgrade_journal as uj`, and `upgrade_journal` owns the writer `executor_run_log_paths:2274`.
- **Impact:** writer + two readers in three places; validation and env-scope rules can drift independently (the regex already has), and `_log_tail:2290` can read a different file than the tools tail when inputs differ.
- **Fix:** move the resolver to `upgrade_journal.per_run_narrative_path(install_dir: Path, run_id: str | None) -> Path | None` (owner of `_UPGRADE_ENV_SCOPES` and `executor_run_log_paths`), keep the regex guard there, delegate from both call sites.

#### Test Organization

**M8. Two independent drain-escapement harnesses stub the same seam — `tests/unit/tools/test_upgrade_preflight_daemon_side.py:221` vs `tests/unit/tools/test_upgrade_tools.py:3602`**
- **Problem:** `_DrainHarness` (`:221-289`) and `TestManagerDrainPendingExecution`'s `spawn_calls` (`:3624-3661`) both `object.__new__(InstanceManager)`, both seed `_pending_system_executions`, both write a `PendingOp`, both define the same recorder stub — the new one cites the old by name in its docstring. This lane had to patch the `unit_properties` kwarg into **both** copies (`:246`, `:3634`).
- **Impact:** the spawn-seam signature is pinned twice; the next kwarg needs two coordinated edits or one harness silently stops seeing it.
- **Fix:** hoist one harness into `tests/unit/tools/conftest.py` (already exists, `:1-24`) or a shared fixture helper, capturing `unit_properties`; have both test classes consume it.

**M9. The mutation matrix rewrites tracked files but bypasses the lane's own fence helper — `tests/packs/preflight_mutation_matrix_test.sh:39`, `:171`**
- **Problem:** 13 of 14 packs source `tests/helpers/preflight_pack_common.sh`; this one does not. It computes `REPO_ROOT` from its own location (`:39`) and rewrites tracked files under it (`PLANT_TARGETS:54`) with only a *dirty-tree* guard and **no `pf_assert_not_live` equivalent** — so running it from the shared checkout `/home/nea/ensemble-src` plants defects there instead of failing closed. It also never sets `PF_PACK_NAME`, so `pf_summary` names it generically and the shared `pf_ok`/`pf_bad` vocabulary is absent.
- **Impact:** the one script in the lane that mutates real source is the one outside the shared safety vocabulary; muscle memory from the pack family will assume the same fences.
- **Fix:** source the helper's `PF_REAL_HOME`, add `pf_assert_not_live "$REPO_ROOT"`, set `PF_PACK_NAME=preflight_mutation_matrix`, and add one header line stating the shared helper is deliberately not sourced and why.

**M10. Exec-bit split down the middle inside the new pack family — `tests/packs/preflight_{journal_parity,journal_scale,repro,census,anchor_index,size_guard,shasum_batch_parity}_test.sh`**
- **Problem:** 7 of 14 new `*_test.sh` are `100644`, 7 are `100755`. House convention in `tests/packs/` is `100755` for 17 of 18 pre-existing `*_test.sh`.
- **Impact:** invisible in review; surfaces as "permission denied" in a live gate, at the worst time.
- **Fix:** `git update-index --chmod=+x` the 7 so the family is uniformly `100755`.

### 🟢 Low

**File Hygiene**
1. `import sys` unused — `tests/unit/tools/test_upgrade_preflight_daemon_side.py:48`. Delete the line.
2. Four consecutive blank lines in the new phase-6 block — `daemon/tools/upgrade_tools.py:3967`. Collapse to two.
3. One-argument `dict` generic on the seam this lane extended — `daemon/tools/upgrade_journal.py:2176` `executor_env(...) -> dict[str]`; body only inserts `str` values → `-> dict[str, str]`. Pre-existing, 1-character-class fix.
4. `PF_PACK_DIR` named for the *pack* dir but resolves the **helper** dir (`tests/helpers/`), with `PF_REPO_ROOT` computed as `../..` from there — `tests/helpers/preflight_pack_common.sh:211-212`. Correct today, misleading if the helper moves → rename `PF_HELPER_DIR` and derive from it.

**Type Cleanliness**
5. `journal_read.py` opts out of annotations while its explicit sibling twin opts in — `journal_read.py:151` `_balance_scan(data)`, `:186` `cmd_verify(path)`, `:207` `_json_sub(data, key)` all bare, vs `journal_splice.py:72` `from __future__ import annotations` with `:114`/`:269` annotated. Copy the sibling's header discipline.
6. `discover_seam()` returns an unannotated 3-tuple in an otherwise fully-annotated file — `tests/helpers/preflight_ceremony_probe.py:155`; contract is prose at `:16-19` → `-> tuple[Callable[..., tuple[str, str]] | None, str, str]`.
7. `Recorder.popen/run` leave `argv` bare and `run` without a return type — `test_upgrade_preflight_daemon_side.py:91`, `:95` → `argv: Sequence[str], **kwargs: Any` / `-> subprocess.CompletedProcess`.
8. The stub mirroring the real seam is the least annotated thing around it — `_DrainHarness.spawn_stub` (`test_upgrade_preflight_daemon_side.py:246`) hand-copies `upgrade_journal.spawn_executor` (`:2824`) which **is** fully annotated. Copy the annotations verbatim so a signature change breaks visibly.

**Test Organization**
9. `tests/packs/preflight_packs.txt:1-3` claims "machine-readable … so the enumeration cannot drift" but no script reads it (`grep -rl preflight_packs.txt` → only itself + two planning docs; `gate.sh` has zero `packs/` references). Either drive `gate.sh` from it or restate the header as "human-readable index (not consumed by any script)".
10. `preflight_base_scale_measure.sh` is deliberately outside the gate and correctly lacks the `_test` suffix, but `preflight_packs.txt:5-6` defines "support rows are measurement/negative-control companions" and then lists `preflight_real_soak_test.sh` — the manifest's own rule suggests this file is missing. Add one commented line naming it excluded-by-design.
11. `preflight_shasum_batch_parity_test.sh` is listed at `preflight_packs.txt:29` with **no letter** in the A–J mapping (`:9-25`) despite 4+ assertions and parity-family membership. Give it a letter so the letter space stays exhaustive.
12. Three failure lines print no expected-vs-actual — `tests/packs/preflight_journal_parity_test.sh:97`, `:99`, `:101` ("G1 size wrong" while the success line prints `(98303 B)`); also `preflight_logging_test.sh:123`. → `pf_bad "G1 size wrong: got $(pf_bytes …) want 98303"`.
13. Three scenario ids collapse into one failure token after dumping the whole buffer — `preflight_logging_test.sh:269` ("E7/E12/E14 python pins failed"). Assert per id, or append the first `^FAIL` line so the failing leg is named in the summary.
14. `install` fixture defined twice in the same package with different shapes — `test_upgrade_preflight_daemon_side.py:61` (minimal) vs `test_upgrade_tools.py:106` (two releases + manifest + symlink); no conftest precedence explains which wins. Rename the minimal one `install_min`, or move to `conftest.py` under a distinct name.

**Routed, not owned here**
15. 14 new packs + a 40-leg mutation matrix have **zero** rows in `.agents/tester/PACKS.md` (`grep -c preflight_ …` → 0), the registry other lanes read to discover reusable packs. Tester owns that file; route at merge (shape precedent: the dbpool table at `PACKS.md:9-15`).

---

## TOP 5 MAINTAINABILITY RISKS

1. **`scripts/upgrade/lib.sh:24`** — a 6,640-line shared substrate whose own "why it is big" note is 1,080 lines stale and whose split backlog does not mention the +854 lines this lane just added; the file is growing faster than its explanation.
2. **`daemon/tools/upgrade_tools.py:3905`** — the phase-6 block's *position* is load-bearing ("Relocate only downwards", enforced by a **textual** pack pin at Pack I C5); a routine tidy-up that moves it silently turns a nonce-burn guard pin vacuous.
3. **`daemon/tools/upgrade_tools.py:942` + `daemon/services/upgrade_journal_sweep.py:2226`** — the same per-run log resolver implemented twice with already-drifted validation, while the writer (`upgrade_journal.py:2274`) is a third site.
4. **`tests/unit/tools/test_upgrade_preflight_daemon_side.py:221`** — a second hand-copied drain harness for a seam this lane changed, so the spawn signature is pinned in two places that must be edited together.
5. **`tests/fixtures/journal/CORPUS.md:7`** — three references to a non-existent `EXPECTED.json` invite a future author to "complete" the design by freezing verdicts, converting the BASE-oracle differential into a self-fulfilling golden.

---

## Recommendations (prioritised)

1. **Re-run the two lost lanes** (`tidier-readable-code`, `tidier-robustness`) on `f76765e6a` before treating this lane as craftsmanship-reviewed. The uncovered surface is the commission's top focus area.
2. **H1 + M1 in one commit** — the size rationale is a single-file edit and it is the only threshold-compliance artifact the two largest files have. Cheap, high leverage.
3. **M5 (Literal verdict)** — smallest fix with the sharpest failure mode: a typo in `{"arm","warn","refuse"}` silently arms instead of refusing.
4. **M7 (single resolver)** before the sweep's tail diverges from the tools' tail in production; `upgrade_journal` already owns both the path writer and the env-scope table.
5. **M8 (single harness)** now, while both copies are fresh — every later kwarg is a two-file edit under this design.
6. **M9 (matrix fence)** — the mutating script should fail closed from the shared checkout, not plant defects there.
7. **M2 (EXPECTED.json ghost)** — wording fix that prevents a future weakening of the oracle. Do it with a "deliberately not frozen" clause so the intent is explicit.
8. M10 (chmod), M3/M6 (annotations/dataclass), M4, and the Low items last.

---

## Deferred to the correctness council (not adjudicated here)

- Whether the provenance verdict branches (git-shaped vs non-git) behave correctly.
- W-1 sentinel semantics and whether the packs actually prove the fix.
- Shell style/readability across `lib.sh` +854 and the 18 packs — **deferred only because the lane produced no report; this is an open gap, not a clean bill.**
- `PIPELINE_SCRIPTS_REV=unreleased-fix-promote-preflight-hang` placeholder at `lib.sh:81` — release-time obligation, policy lane.
- Live-contact and scope-policy conformance — owned by the stage-adjudication artifact.

## Positive observations (explicit, from the delivered lane)

- **Threshold compliance is otherwise exemplary:** every 1000–3000+ file touched carries a real "why it is this big" rationale naming an intended extraction seam (`upgrade_tools.py:62-72`, `lib.sh:24-39`) — better than the minimum the standard asks for; the H1 defect is staleness, not absence.
- **Fixture design is documented so nobody "fixes" it** — `tests/fixtures/journal/CORPUS.md` covers all 27 fixtures including the 0-byte and NUL/binary traps, and `G5` records why a "correct-looking" raw-byte scan must go RED.
- **Pack assertion discipline is high where it counts** — the parity pack prints BASE/NEW `rc/len/sha/class/stderr` on one line (`preflight_journal_parity_test.sh:73`); the mutation runner prints leg id + `red_rc`/`green_rc` + expected pin + the first two `FAIL` lines (`preflight_mutation_matrix_test.sh:293-296`).
- **Fence vocabulary is single-sourced** — `preflight_pack_common.sh:1-21` states the contract, self-tests its escape guard (`:96-99`), and explicitly forbids the `env -u POSTGRES_*` no-op idiom (`:81`); 13 of 14 packs consume it.
- **Lane-local extraction was done correctly where done** — `journal_read.py` (381 lines) and `journal_splice.py` separated by concern with the reason in the docstring; `preflight_mutation_apply.py` centralises all 40 plants with an exactly-once match guard.

---

## Process note (for the leader)

This review is **29 findings from one of three lanes**. Publication was delayed by a provider-side rate-limit storm: 5 worker instances, 9 failed turns, 2 revives, 2 replacement spawns, all `Upstream request failed: Rate limit exceeded`. The ladder was followed to its end (one revive per node, one replacement per node, then stop) and no further spawns were issued. Nothing in the code under review caused these failures.

Re-running the two missing lanes is a ~single dispatch each once the limiter clears; the work is not lost, only deferred.
