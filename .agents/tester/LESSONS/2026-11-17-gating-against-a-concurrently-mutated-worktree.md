# A pinned sha is NOT a pinned measurement — gating against a concurrently-mutated worktree

**Date:** 2026-11-17
**Lane:** `feature/prompt-reload-compact-note` @ `e387d2297` (compact-note gate)
**Applies to:** any gate/leg run against a worktree that another lane may be writing to.

---

## The failure mode

The branch TIP never moved (`e387d2297` throughout the whole gate), so every leg "ran
against the right sha". But a concurrent developer lane wrote **uncommitted** changes
into the worktree under test, continuously — 11 tracked files, mtimes advancing
*after* dispatch (`09:33:38, 09:33:56, 09:40:55, 09:41:28, 09:43–09:49`).

Consequences, all observed:

1. Any run that imported `daemon` after the last production-file write measured a tree
   that was **not** the commit under test. `daemon/graph.py` alone carried 311
   insertions / 73 deletions versus the pin.
2. The dev daemon runs `uvicorn --reload` (`dev.sh:159`), so a concurrent source write
   became a **mid-turn behaviour change** — the live smoke's `tail_spliced=3` matched
   the writer's brand-new `min_window=3`, proving it executed their code, not the branch.
3. A leg *self-evidenced* the contamination by reporting a known-stale pin as passing
   (`the test now asserts == 14`) — true only of the dirty tree.
4. A dirty-file list handed to a task is a **LOWER BOUND**: the brief named 6 files,
   there were 11.

## The rule

**Classify every leg by its IMPORT WINDOW, not by its declared sha.**

- A leg that imported the changed modules *before* the last relevant write is VALID.
- A leg that imported them *after* is INVALID — report it as invalid, not as a pass
  and not as a failure of the code.
- A leg whose result is **invariant** to the mutation needs no re-run. Proof by
  byte-identity of the changed region (here: the refusal classifier's 12 markers and
  its `[:120]` head slice were byte-identical between the pin and the dirty tree, so
  item 5 was not re-run).

## Pinning techniques (pick by disk cost)

| technique | cost | notes |
|---|---|---|
| `git clone --shared` + `uv sync` inside the clone | ~600 MB | full venv; own editable install; the most robust |
| `git archive <sha> \| tar -x -C $PIN` + `PYTHONPATH=$PIN` + an existing venv | ~0 MB | `git archive` reads the COMMITTED object — structurally immune to the writer |

Disk check first: the 2026-11-17 run had the root fs at **98% / 6.1 G free**. One pin
must serve every contaminated leg; re-running `uv sync` per leg is what burns the disk.

## Traps

1. **A pinned tree is NOT cwd-independent.** Its editable install is a path-based
   `.pth`, not a MetaPathFinder, so `sys.path[0]` (cwd / script dir) **outranks** it.
   Measured: probing with cwd = the main checkout resolved `daemon` to the main
   checkout — a forbidden tree. Same ordering applies to `PYTHONPATH`
   (`sys.path` = [0] cwd/script-dir, THEN PYTHONPATH, then site-packages).
   **Assert `daemon.__file__` AND each subpackage's `__file__`, before AND after every run.**
2. **Symlinking the source worktree's venv into a pin is a TRAP** — that venv's `.pth`
   pins the mutating tree, so every re-run silently re-measures the contamination.
3. **`git diff --stat` is weaker than `git diff HEAD --stat`.** The first compares
   worktree→index; only the second proves the worktree matches the pinned COMMIT.
4. **The pinned source of truth for an anchor is `git show <sha>:<path>`** — it reads
   the committed blob and is immune to a dirty tree. Anchors drift between the dirty
   working tree and the pin (this run: +36 lines) — cite the pinned line numbers.
5. **Legacy gate scripts that invoke `bash` must be run as `bash <script>`** — the
   bash tool runs `/bin/sh`, where `set -o pipefail` is an illegal option.
6. **A pack with `set -euo pipefail` + an unguarded `RC=$?` exits before printing
   `RESULT: FAIL`.** Treat `rc != 0 AND no RESULT line` as FAIL, not an infra error.
   (The lane pack `prompt_reload_compact_note_unit.sh` is CORRECT — `set -uo pipefail`,
   no `-e`; the defect lives in the sibling `compaction_unit_test.sh` and
   `concurrency_atomic_unit_test.sh`.)
7. **Always state the coverage boundary when a base red-set is reported as empty.**
   "∅" over a name-pattern selection is a lower bound, not a fact. Scope by affected
   symbol and take the **transitive** closure of test files importing/asserting on the
   changed symbols.
8. **A changed-symbol set must come from an AST diff, never from diff hunk context.**
   Hunk headers (`@@ -141,8 @@ INFO_OUTCOMES: tuple[str, ...] = (`) show the
   *enclosing* symbol, not the changed one. Seeding a symbol set from them
   over-states the branch's reach AND under-states its omission simultaneously.
   Measured 2026-11-17: 6 of 12 seeded symbols (`COMPACTION_TARGET_RATIO`,
   `INFO_OUTCOMES`, `CODE_COMPOSE_FAILED`, `CODE_ENVELOPES`, `format_info_line`) were
   **byte-identical** base↔tip, while genuinely-changed symbols (`build_compact_note_request`,
   `DEFAULT_COMPACT_NOTE_MAX_CHARS`, `_resolve_compact_note_max_chars`,
   `DELIBERATE_COMPACT_RATE_LIMIT_S`, `load_config`, ~40 new `prompt_refresh` symbols)
   were **missing from the seed**. Both errors bias the test set in opposite
   directions — files pulled in that assert on untouched symbols, and files missed
   that assert on new ones.
9. **A pin under `/tmp` breaks PATH-SENSITIVE tests — that is a method artifact, not a
   branch red.** Measured 2026-11-17: 10 nodes in `test_plugin_registry` (1),
   `test_filesystem_walk_guardrails` (4) and `test_guidelines_registration` (5) failed
   ONLY at the `/tmp` pin and were GREEN at the repo-shaped base worktree — they
   resolve a repo-root `plugins/` dir, the real data dir, and shipped guideline files.
   When a pin is the measurement target, re-measure path-sensitive tests at a
   repo-shaped path or exclude them explicitly; never report a `/tmp`-location red as
   a branch regression.
10. **A pinned tree makes sharded runs non-reproducible** (cross-file `sys.modules`
   langgraph eviction, 12+ evictor files in the derived set) — so the **per-file
   isolated** run is the authoritative measurement, and evictor files are no-ops
   (rc=5, all deselected).

## Verifying a pin is a pin

```
git -C "$PIN" rev-parse HEAD          # == target sha
git -C "$PIN" status --porcelain      # empty
git -C "$PIN" diff --stat             # empty
git -C "$PIN" diff HEAD --stat        # empty  (STRONGER)
diff -q "$PIN/<f>" "$WT/<f>"          # must DIFFER, per dirty file
```
And the import-provenance assertion (4× — before the pin proof, after it, after the
`.env` write, after the test run).

## Where the durable artefacts live

- Gate report: `.agents/tester/RESULTS/2026-11-17-prompt-reload-compact-note-gate.md`
  (§1 contamination, §1.1 leg-validity table, §5.2.1 cwd trap, §0.1 coverage boundary)
- Base evidence: `/tmp/compact-note-base-evidence-20261116T225534Z/`
- Contamination snapshot: `/tmp/compact-gate-contamination/CONTAMINATION_SNAPSHOT.txt`
- Skill minted by the pin worker: `pinned-clone-for-contaminated-gate-reruns`
  (id `475e5ceb-b911-4031-a17d-ac0823242a29`)
