# Staged-release verification — method traps and fence blind spots (v0.28.12, 2026-11-05)

**Context**: independent staged-artifact verification of `v0.28.12` (`aegis-security-family`) — 7 worker lanes, 0 re-dispatches, zero artifact discrepancies, zero live contact. This file records the *durable* traps the run surfaced. Six of the eight below produce a **false reading on a perfectly healthy artifact** — i.e. they manufacture FAILs or (worse) vacuous PASSes rather than revealing real defects.

Companion record: `.agents/tester/RESULTS/2026-11-05-v02812-staged-verification.md`.

---

## 1. The tree-hash sort key is RELPATH, not the emitted line

`manifest.agents_tree_sha256` / `frontend_tree_sha256` / `plugins_tree_sha256` are NOT git tree objects and NOT a hash of the per-file map. House algorithm (`scripts/upgrade/lib.sh:2727`, `:2885`):

```
_tree_manifest <dir> <prefix>:  find . -type f | sed 's|^\./||' | sort    # sort key = RELPATH
                                → hash each → printf '%s  %s\n' "$sha" "$prefix$rel"
_tree_hash_of_lines <lines>:    printf '%s\n' "$1" | shasum -a 256 | awk '{print $1}'
```

**Trap**: sorting the *emitted* lines (sha-first) yields a different aggregate — 3 false MISMATCHes on a healthy artifact. The sha is payload, not sort key. Replicating exactly → 3/3 MATCH.

## 2. `generate_prompt_artifact_baseline.py --check` ignores your cwd

`--check` with NO `--tree` resolves the tree from the **script's** repo root, not the current directory. So `cd <staging_worktree> && python <main-repo>/scripts/protection/generate_prompt_artifact_baseline.py --check` silently verifies the **MAIN** checkout (it printed `161 versioned artifact(s)` for a 177-artifact staging worktree — the main checkout was a stale v0.27.8-era tree).

**Always pass `--tree <dir>` explicitly**, and assert the printed artifact count equals the count you meant (`wc -l <tree>/agents/.prompt-artifacts.sha256`). `--check --tree <release_dir>` resolves the **staged payload** directly — no copy into scratch needed.

## 3. The embedded `.sql` census: path prefix + a 15-char uniqueness key

```
strings -n 20 "$BIN" | grep -E '(^|/)[0-9]{8}_[0-9]{6}_[a-z0-9_]+\.sql$' | sed 's#^.*/##' | sort -u > census.txt
```

- A strict **full-line** pattern yields **0** on both binaries — the CArchive prefixes the repo path. A zero result is a **METHOD FAILURE**, not an empty registry. Sanity-check the count is non-zero and near the source count.
- The uniqueness key is the **15-char `YYYYMMDD_HHMMSS` prefix**: `cut -d_ -f1,2 census.txt | sort -u | wc -l` (distinct) and `cut -d_ -f1,2 census.txt | sort | uniq -d` (must be empty). A cut down to the **8-char date** collapses same-day files and MANUFACTURES a false duplicate alarm — v0.28.12 has 119 files over 67 distinct dates, 21 of them multi-file.
- The `.sql` **DATA-TOC** census is **interpreter-independent** (a plain `CArchiveReader.toc` read under a 3.13 `.venv` matched the house `strings` method exactly) — unlike a PYZ `co_consts` walk, which needs the BUILD interpreter for a 3.14 artifact.

**What the census does NOT prove**: the PG runner is a NO-OP on non-SQLite URLs, so a `.sql` never executes against live PostgreSQL (PG evolves via `SQLModel.metadata.create_all` + `_ensure_postgres_columns`). It is a **payload-integrity** fact, not a schema-change fact; rollback-safety is judged separately.

## 4. Manifest per-file map keys are SUBTREE-RELATIVE

```
agents_manifest   key K → <release>/agents/K
frontend_manifest key K → <release>/frontend/K      # keys INCLUDE the dist/frontend/... prefix
plugins_manifest  key K → <release>/plugins/K
```

e.g. `agents_manifest[".prompt-artifacts.sha256"]` → `<release>/agents/.prompt-artifacts.sha256`; `frontend_manifest["dist/frontend/browser/index.html"]` → `<release>/frontend/dist/frontend/browser/index.html`. Reading the mapping as release-root-relative produces MISSING-IN-MANIFEST for **every** file of a healthy payload — a false FAIL that costs a re-run and can be misread as a packaging defect. The non-keyed files under the release root are exactly `config.yaml`, `ensemble-prod`, `launcher.sh`, `manifest.json`; anything else is an anomaly.

## 5. The live fence: WHICH systemd, and WHICH pid

- **`systemctl --user` is INERT on this host.** `systemctl --user show ensemble-main.service -p MainPID` returns EMPTY with `Failed to connect to bus: No medium found` — the unit is **system-level**. Using `--user` makes the MainPID / NRestarts / ActiveEnterTimestamp fence legs **VACUOUS**: an empty value compares equal to an empty value across pre/mid/post. **Always assert the MainPID field is non-empty and numeric before recording that leg PASS.**
- **The systemd MainPID is the launcher shell** (`/proc/<MainPID>/cmdline` = `/bin/bash <install>/launcher.sh`), and its exec-time environ carries **no** `ENSEMBLE_*` vars — the launcher exports `.env` AFTER exec. Any environ read (e.g. `ENSEMBLE_UPGRADE_SCRIPTS_DIR`) must target the `ensemble-prod` **child** pid.
- **`pgrep -f 'ensemble.*ensemble-prod|ensemble-prod'` self-matches the capturing shell** → its pre/post PID delta is fiction. Use `pgrep -P <MainPID>` or read `/proc/*/cmdline` directly.
- The live `current` pointer is at the **install-dir level** (`<install>/current`), **not** `releases/current` — the latter does not exist, and reading a missing path returns the path itself, making that fence leg vacuous too.

## 6. `git_dirty=false` is TRACKED-FILE-BLIND — never read it as byte-identity

The provenance sidecar's `git_dirty` is computed from `git status --porcelain`, which by default **excludes ignored files**; the house `_git_dirty_porcelain` (`lib.sh:3413`) uses neither `--ignored` nor `--untracked-files=all`.

Measured: the ceremony-retained staging worktree carried **97 verification-window residue entries** (81 files + 16 dirs — 80 of them pytest/pluggy/pygments bytecode inside the worktree's OWN `.venv`, plus 1 `daemon/tools/__pycache__/prompt_artifacts.cpython-313.pyc`) while `git status --porcelain` stayed **0 lines** and `git_dirty` stayed `false` throughout. Ignored-file population **62,693 → 62,612** after restore (delta exactly the 81 files deleted).

**Rule**: a rigorous "pristine worktree" claim must add `git status --ignored --untracked-files=all` (or diff the ignored set explicitly). `git_dirty=false` certifies **source identity only**.

**Corollary — delete by BIRTH TIME, not mtime.** `stat --printf='%w'` proved 81/81 files were CREATED inside the window and 0 were REWRITTEN_PREEXISTING, making the delete a true restore rather than a loss. Where a `__pycache__` dir held pre-existing build bytecode, the dir was LEFT and only the window files removed (5 mixed dirs retained). Unavoidable residual: deleting files bumps the mtime of the retained parent dirs — **byte content is restored; directory mtimes are not**.

**Corollary — ceremony materiality is a separate, provable question.** The residue was INERT for the promote, proven by reading the machinery, not assumed: `promote.sh:163-164` resolves `REL="$INSTALL_DIR/releases"` / `TARGET_REL="$REL/$VERSION"`; `grep -n 'build-provenance\|provenance' promote.sh` → **zero hits**; `_verify_artifact_provenance` is called **only** from `stage.sh:252` and `stage.sh:308`; `_verify_tip_identity` only from `stage.sh:163` — both stage-time gates; `grep -rn 'wt-stage\|ensemble-src-wt' scripts/upgrade/` → **zero hits**. The sidecars serve stage gates + human audit, never the flip.

## 7. The import-shadow hazard is REAL, not theoretical

Running a pack against a disposable worktree resolves `import daemon` to the **MAIN** checkout: the shell CWD (`sys.path[0]=''`) wins over the editable `.pth`. A probe showed **all three** worktree venvs (lane 3.14.7, stage 3.14.7, main 3.13.15) importing `daemon` from `/home/nea/ensemble-src`.

Unforced, a staged-tree pack re-run would have silently tested the **wrong tree** and reported green for a tree that was never executed. Force with `PYTHONPATH=<worktree>` + cwd, and **prove** it before trusting any result:

```
cd <worktree> && PYTHONPATH=<worktree> $PY -c "import daemon; print(daemon.__file__)"
```

Combine with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider` so the run leaves no residue in a tree whose cleanliness is itself under audit.

## 8. `env -u POSTGRES_*` is a silent NO-OP

bash does not glob env names, so `env -u POSTGRES_*` unsets **nothing** and an ambient `POSTGRES_DB=ensemble_prod` survives a supposedly-fenced run. Scrub **per variable** (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD …`), then SET what you want, and assert `POSTGRES_DB != ensemble_prod` programmatically before the run. Ambient poison observed in this run: `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `PORT=9797`, `ENSEMBLE_SELF_ENV=live`.

Companion: `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` must be **pinned explicitly** — `env -u` on it strips the ambient guard and the boot sweep then KILLS LIVE processes (the 2026-10-31 incident).

---

## Skill routing

| Skill | Verdict | Note |
|---|---|---|
| `binary-embedded-sql-census-and-prefix-trap` (c595f7cf) | applied, **9/10** | The house `.sql` census method; the 15-char prefix trap is the load-bearing part. |
| `staged-release-artifact-verification` (a2220107) | applied, **7/10** | Two defects filed: the live-fence systemd form (§5) and the manifest key convention (§4). Also carries a **stale expectation**: it expects a *local-only* tag, but v0.28.12's tag is PUSHED — the tag-peel leg must accept both. |
| `staged-release-verification-method-traps` (3b7a21df) | **created by this run** | Captures §1, §2, §3; §4/§5/§7 filed as additions. |
| `independent-staged-release-integrity-verification` (1910ed59) | consulted | Already documents the §4 key mapping correctly. |
| `prepare-tester-evidence-branch-and-worktree` (439d01c1) | **created by this run** | Evidence branch + worktree prep, including the `--no-track` upstream-hazard proof. |

**NOTE**: the skill-evolution admission lane is **DISABLED** (`ENSEMBLE_SKILL_EVOLUTION_ENABLED` off). The two `skill_fix` requests were **recorded** in the skill store with **NO keeper job dispatched** — they will not self-apply. A keeper must pick them up when the lane is re-enabled.
