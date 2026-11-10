# Phase-1 Skill-System-Revival — BASE-LEG EVIDENCE PACK

> **PROVENANCE (copy):** copied VERBATIM on 2026-11-09 from the out-of-repo original at `/home/nea/skill-revival-base-evidence-20261109-0813Z/` — that original is the raw of record and was KEPT IN PLACE (not moved, not deleted). File set and bytes are identical; only this header line was added here.

**Pack:** `/home/nea/skill-revival-base-evidence-20261109-0813Z/`
**Produced by:** giter (git/worktree lane), 2026-11-09
**Purpose:** INPUT to the Phase-1 tester lane. Pre-measured base leg + the
quarantine classification the tester must apply to both legs.

**Provenance of every number below**
- Base worktree: `/home/nea/ensemble-src-wt-skill-revival-base1` — **detached** at
  `ae5bbafedde90c7af1065b9660196bbffbbbf9c1` (= `origin/latest` = the Phase-1 base sha).
- Base worktree venv: **CPython 3.14.7**, own `uv sync --frozen --python 3.14`
  (`uv.lock` sha256 unchanged). The canonical checkout's stale 3.13 venv was NOT used.
- All runs were executed with CWD = the base worktree (the agent bash tool's default
  CWD is `/home/nea/ensemble-src` — see §5 — so an un-`cd`'d run would import the
  canonical tree and silently corrupt the leg).
- No daemon booted. No PostgreSQL touched. No binary executed. The canonical checkout
  was read-only throughout (96-line status identical before/after).

---

## 1. THE TRUE BASE RED SET — 3 stable reds

Run **3 consecutive times** over the 12-file / 459-test skill blast radius
(`base-leg/file-set.txt`). **The counts are NOT constant across runs** — that is the
whole point of measuring ≥3:

| run | result | AB-resolution node |
|---|---|---|
| `base-leg/run1.txt` | `4 failed, 455 passed` (32.87s) | **RED** |
| `base-leg/run2.txt` | `3 failed, 456 passed` (27.03s) | green |
| `base-leg/run3.txt` | `4 failed, 455 passed` (28.13s) | **RED** |

⇒ a **single-run** base measurement would have produced either a 3-red or a 4-red base
set, purely by chance. This is exactly why the protocol diffs **sets over ≥3 runs**.

```
FAILED tests/manager/test_skill_service_init.py::test_skill_services_have_correct_dependencies
FAILED tests/manager/test_skill_service_init.py::test_skill_services_initialized_when_config_present
FAILED tests/manager/test_skill_service_init.py::test_skill_services_none_when_skill_evolution_disabled
```

**Root cause (all three, one cause):** the known, pre-existing, **NOT-Phase-1**
SQLite migration defect `20260714_000001` —
`ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type` is invalid
SQLite syntax; the runner is SQLite-only so PostgreSQL never hits it.
Raw evidence: `base-leg/run1.txt` (the `ERROR daemon.migrations.runner:runner.py:723
Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error` line).

**Stability census** (`base-leg/stability-census.txt` — `xN` = present in N of 3 runs):
```
x3  FAILED tests/manager/test_skill_service_init.py::test_skill_services_have_correct_dependencies
x3  FAILED tests/manager/test_skill_service_init.py::test_skill_services_initialized_when_config_present
x3  FAILED tests/manager/test_skill_service_init.py::test_skill_services_none_when_skill_evolution_disabled
x2  FAILED tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met
```
⇒ the first three are **STABLE** (in every base run); the fourth is **FLAKY** (see §2).

> ⚠️ **CLASSIFIER FILTER — read before diffing.** `base-leg/union.txt` and
> `base-leg/stability-census.txt` contain a **4th non-`FAILED` line**:
> `ERROR daemon.migrations.runner:runner.py:723 Migration 20260714_000001 failed: …`
> It appears **3× per run** (x9) because `--tb=short` prints the migration error in the
> traceback *and* in the summary block. It is the **cause** of the 3 stable reds, not a
> 4th red. **Always classify with `grep '^FAILED'`**, never with a bare
> `grep -E '^(FAILED|ERROR)'` over these files.

**These 3 reds are the TRUE base. A head-leg red on any of them is a REGRESSION.
A head-leg red on the §2 node is a CLASSIFICATION, not a net-new red.**

---

## 2. THE AB-RESOLUTION FLAKE — excluded from the base set, but recorded

**Node id (exact):**
```
tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met
```

**Failure signature:**
```
AssertionError: assert True is False
 +  where True = Skill(...).is_active
WARNING  daemon.repositories.skill.repository:repository.py:333
         Skill not found for update: id=<uuid>
```
→ the old skill was never deactivated. Full traceback: `flake-ab-resolution/failing-traceback.txt`.

**PROVEN MECHANISM — concurrent use of ONE shared SQLite connection.**
1. The test engine is
   `create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)`
   (`tests/services/conftest.py:42`). **StaticPool hands the same DBAPI connection to
   every thread** — there is no per-thread database and no per-thread isolation.
2. `SkillEvolutionService.check_ab_test_resolution` fans its resolve-path side effects
   out concurrently:
   `asyncio.gather(asyncio.to_thread(self._skill_repo.deactivate, loser_id),
   asyncio.to_thread(self._ab_test_repo.resolve, ab_test_group, winner_id), …)`
   (`daemon/services/skill_evolution_service.py:1449-1451`, and `:1475-1476`).
3. Concurrent sessions therefore interleave their transactions on that single
   connection; one session's COMMIT/ROLLBACK can invalidate the other's read.
4. `SkillRepository.update` (`daemon/repositories/skill/repository.py:315-333`) does
   `session.get(Skill, skill_id)` → returns `None` for a row that WAS committed →
   logs `Skill not found for update` → returns `None` → the loser is never deactivated
   → the assertion fails.

**DECISIVE EXPERIMENT** (`flake-ab-resolution/experiment.txt`) — *same tree, same
interpreter (3.14.7), same command; the ONLY difference is the thread hop*:

| leg | `asyncio.to_thread` | result |
|---|---|---|
| A — normal | → thread pool | **11 passed / 1 failed** (12 runs) |
| B — monkey-patched to call inline | no thread hop | **12 passed / 0 failed** (12 runs) |

⇒ **Causality, not correlation.** The flake disappears when the concurrent fan-out
becomes sequential.

*Aggregate across all measurements this session:* isolated node **7 failures in 46 runs ≈ 15%**;
in full-file-set context **3 failures in 8 runs ≈ 38%** (more concurrent activity ⇒ higher rate).

**THE 3 PRE-EXISTING `QUARANTINE.md` ROWS THIS CONFIRMS AND NOW *EXPLAINS***
(their prior diagnosis was hypothesis-only; the mechanism above is now proven):

| row | date | prior wording |
|---|---|---|
| **45** | 2026-09-23 | "xdist/class-level shared-SQLite fixture pollution … Durable fix = fixture isolation for TestCheckABTestResolution." |
| **56** | 2026-08-30 | sibling `test_ab_resolution_force_resolve` — "Flaky: `StaleDataError: UPDATE statement on table 'skills' expected to update 1 row(s); 0 were matched` — non-deterministic shared-state/timing in A/B force-resolve path." |
| **90** | 2026-08-30 | "Pre-existing red at clean base `459557b2`: `AssertionError: assert True is False` (old-skill `is_active` not deactivated by threshold-met path) … signature-drift follow-up open." |

Same family as the 2026-08-29 dependency_bus StaticPool row. **Durable fix shape:
fixture isolation for `TestCheckABTestResolution`.** That fix lands in the SAME file
as workstream E (`skill_evolution_service.py`) — sequence E and G deliberately.

---

## 3. PROTOCOL FOR THE TESTER LANE (leader-ruled, 2026-11-09)

1. **TRUE base red set = the 3 stable reds of §1.** The AB-resolution node is
   **EXCLUDED** from the base set.
2. **ALSO record the AB node's flake signature** (§2: node id, 15–38% rate, proven
   mechanism, the 3 quarantine rows it now explains) in the base evidence, so a future
   head-leg red on that node is **CLASSIFIED, not counted as net-new**.
3. **Diff base SETS against head SETS** — base measured over **≥3 runs**; head likewise.
   Never a single-run diff.
4. **Both legs checked against a named quarantine list** — the same list this pack carries:
   * `tests/manager/test_skill_service_init.py` ×3 → pre-existing SQLite `20260714_000001` defect (base-evidenced).
   * `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met` → quarantined flaky (proven cause, above).
   * `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_force_resolve` → quarantined flaky sibling (QUARANTINE row 56).
5. **Both legs must share the interpreter 3.14.7.** Never measure a base leg in the
   canonical checkout's stale 3.13 venv.

---

## 4. INTERPRETER — the release's UNRECORDED variable

Full detail: `interpreter/interpreter-provenance.txt`.

- **LIVE runtime = CPython 3.14.** `releases/v0.29.7`, `v0.29.6`, `v0.29.5` binaries all
  contain `libpython3.14` / `cpython-314` (measured by `grep -a` on the bytes; **not executed**).
- The canonical checkout's `.venv` is **3.13.15** (created 2026-08-24) — a **stale outlier**,
  not the runtime of record. Left exactly as-is by decision.
- The repo has **no interpreter pin**: no `.python-version`, no `[tool.uv]` python,
  `requires-python = ">=3.13"` is a **floor**. uv's default python moved 3.13 → 3.14.
- **The live `manifest.json` carries NO python key** → the interpreter is an unrecorded
  variable in every staged release.
- **3.13-vs-3.14 differential on this blast radius: IDENTICAL failed-lists**
  (`interpreter/differential-leg313.txt`, `differential-leg314.txt` — 3 failed / 456 passed both sides).
  ⇒ the interpreter is **not** a confounder for this lane's red/green math.

**Phase-1 release disclosure (measured, not asserted) — run against the STAGED artifact,
then compare with live v0.29.7's measurement in `interpreter/interpreter-provenance.txt`:**
```bash
grep -aoE 'libpython3\.[0-9]+' <staged>/ensemble-prod | sort -u
grep -aoE 'cpython-3[0-9]+'   <staged>/ensemble-prod | sort -u
```
Disclosure lands in **three** places: tester evidence, staged-release composition metadata,
project history. (A code-level record lands only from the release *after* Phase 1 — both
the manifest heredoc and the provenance sidecar are shell-side, the daemon never reads
the sidecar, and the promote is driven by the currently-live daemon binary.)

---

## 5. WORKTREE FACTS THE TESTER MUST NOT RE-DERIVE BY GUESSING

- **Base worktree:** `/home/nea/ensemble-src-wt-skill-revival-base1` — detached @ `ae5bbafed`, venv **3.14.7**.
- **Feature worktree:** `/home/nea/ensemble-src-wt-skill-revival-phase1` — branch `feature/skill-system-revival-phase1`, venv **3.14.7**.
- **`cd` first, always.** The agent bash tool's default CWD is the **project main_directory**
  (`/home/nea/ensemble-src`) — auto-filled by `daemon/tools/instance.py:2396 _make_workdir_aware`
  → `_get_project_workdir` → project `main_directory`. Because Python puts CWD first on
  `sys.path`, an un-`cd`'d run imports the **canonical** tree even with a correct worktree
  venv (proven: worktree CWD → worktree path; `/tmp` → worktree path; canonical CWD → canonical path).
  Scope is **per-project** (per instance→project link), not host-global.
- **`uv` is not on PATH** — it lives at `/home/nea/.local/bin/uv`. Packs that call bare
  `uv run` (e.g. `test/packs/skill_capture_killswitch_unit_test.sh`) fail with
  `uv: command not found` unless `PATH=/home/nea/.local/bin:$PATH` is exported.
- Both worktrees carry an identical fenced `.env` (mode 600, 7 assignments,
  `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`). Same
  `DAEMON_PORT` is safe only because **no daemon boots** in either.

---

## 6. FILE INVENTORY

```
base-leg/file-set.txt               the 12 test files (459 tests)
base-leg/run{1,2,3}.txt             raw pytest output, 3 consecutive base runs
base-leg/run{1,2,3}-failed.txt      exact failed-list per run
base-leg/all-failed-lists.txt       concatenated failed-lists (3 runs)
base-leg/union.txt                  union of reds across the 3 base runs
base-leg/stability-census.txt       xN = present in N of 3 runs  ← the classifier
flake-ab-resolution/experiment.txt  the decisive experiment, verbatim
flake-ab-resolution/failing-traceback.txt   the failing traceback
interpreter/interpreter-provenance.txt       live binaries, venvs, repo pins, manifest gap
interpreter/differential-leg313.txt          3.13 leg raw output
interpreter/differential-leg314.txt          3.14 leg raw output
README.md                           this document
```

## 7. SCOPE NOTES

- This pack contains **no commits** and touches **no repo**: it lives outside the repo
  precisely so it cannot collide with the Phase-1 commit point (which the developer
  lane owns for `phase0-facts-extract.md` + `phase1-plan.md` + `decisions.md`).
- The feature worktree's `.agents/shared/planning/skill-system-revival/phase0-facts-extract.md`
  is the **leader's** artifact (written by wanderer at the leader's commission). giter has
  **not** moved, deleted, staged, or modified it, and will not.
- Promote for this release runs from `/home/nea/ensemble-src-wt-scripts-pin` @ `84df6b55c`
  — **not** the feature worktree, **not** the canonical dir (the canonical dir is
  disqualified on three independent counts: floor `2147c0298…` is **not** an ancestor of
  its HEAD `013895287`; its working-tree stamp is `953603108f…` ≠ floor and **no stamp
  exists at HEAD**; and its dirty 6-entry staged index would execute uncommitted
  ceremony code).
