# Evidence bundle — v0.27.13 STAGED VERIFICATION

**Verdict record (authoritative, unmodified):**
`.agents/tester/RESULTS/2026-10-29-v02713-staged-verification.md`
— *STAGED-VERIFIED PASS-WITH-NOTES — the artifact is CEREMONY-READY*.

This bundle is the **machine-verified evidence** behind that record: the raw
artifacts of the four verification legs, copied verbatim from the leg scratch
areas, plus the hashes that bind them. Nothing in the verdict record was
changed while assembling this bundle.

| | |
|---|---|
| Artifact under test | `/home/nea/agents-ensemble/releases/v0.27.13` (binary `ensemble-prod`, sha `b935096359dc929b3557fb38e5257aa2693b36b5c53b1c3e1cd72ca58878423a`) |
| Staging worktree | `/home/nea/ensemble-src-wt-stage-v02713` (detached @ `b1183a637649f94c23b94dfc745df44381dc54ab`, KEPT for the ceremony) |
| Evidence branch | `tester/dbpool-functional-gate` (worktree `/home/nea/ensemble-src-wt-tdbp-gate`) |
| Bundle assembled | 2026-10-29, from the leg scratch dirs `/tmp/sv1-integrity`, `/tmp/sv2-symbols`, `/tmp/sv3-boot`, `/tmp/sv4-ladder` |
| Content policy | text evidence only — no binaries, no extracted PYZ payloads, no npm cache, no worktrees (see `SKIPPED-FOR-SIZE.txt`) |

## Source ↔ artifact map

| Bundle path | Source (leg scratch) | Leg |
|---|---|---|
| `sv1-integrity/` | `/tmp/sv1-integrity/` (27 files, verbatim) | §1 artifact integrity |
| `sv2-symbols/` | `/tmp/sv2-symbols/` (scripts + `dis_*.txt`, verbatim) | §2 lane content in the compiled artifact |
| `sv3-boot/evidence/` | `/tmp/sv3-boot/evidence/` (12 files, verbatim) | §3 isolated boot smoke |
| `sv4-ladder/` | `/tmp/sv4-ladder/` (23 files, verbatim) + `diffs/` (3 generated) | §4 ladder from the staged source tree |
| `sv4-ladder/diffs/` | generated at assembly time (read-only `diff -u`) | §4 re-point disclosure |
| `MANIFEST.sha256` | generated at assembly time | integrity of this bundle |
| `SKIPPED-FOR-SIZE.txt` | generated at assembly time | what was left out, and where it lives |

## §1 — Artifact integrity (8/8 PASS)

`sv1-integrity/` carries the re-hash of the staged artifact: the **4-way
binary sha** (`sha_staged_disk.txt` + `sidecar_binary.json` + `sidecar_fe.json` +
`rehash_result.txt`), the payload re-hash over the 5361 manifest entries
(`rehash_result.txt`, produced by the included `rehash.py`), the **tree-hash
self-validation** against the published v0.27.12 hashes (`selfvalidate.py` /
`selfvalidate_result.txt`), the **tag + annotation** proof (`tag_annotation.txt`,
`tag_remote.txt`, `bump_commit.txt`), the **migration census** and the
delta-vs-live proof (`sql_census.txt`, `src_sql.txt`, `live_v02712_sql.txt`,
`prefixes.txt`, `prefixes2.txt`, `mig_delta*.txt`, `mig_dir_diff.txt`), the
**live fence read** (`current_symlink.txt`, `state_fields.txt`), the **tag/branch
corroboration** (`remote_latest.txt`, `release_top.txt`, `scripts_dirs.txt`),
the **prompt-baseline** result (`prompt_baseline.txt`) and the hygiene greps
(`env_hits.txt`, `prov_hits.txt`). The two `.py` files are the reproducing
scripts for the two result files, kept so every number is re-derivable.

## §2 — Lane content in the compiled artifact (8/8 PASS, bytecode-level)

`sv2-symbols/` carries the method and the evidence for the eight lane claims:
the **gate script** (`gate.py`) and its supporting readers (`car.py` — the
lane's pure-python CArchive/PYZ reader, `analyze.py`, `exactsearch.py`,
`namesearch.py`, `floatscan.py`, `probe.py`, `extract.py`), and the
**per-item disassembly excerpts** (`dis_*.txt` — one per inspected code object
of the nine key daemon modules: `daemon.services.readiness` (module,
`make_db_probe`, `_refuse_pool_admission_at_capacity`), `daemon.manager`
(`_reconcile_deferred_report` + `_async`), `daemon.constants`,
`daemon.repositories.factory` (module + `create_postgres_engine`),
`daemon.persistence` (`create_postgres_checkpointer`),
`daemon.repositories.mission_record.repository`,
`daemon.tools.ens_db_tools` (`_dry_run_sqlite_safe`, `_build_repair_engine`)).
The four **consolidated outputs** are `gate-output.txt` (the 8-item gate,
`CONSOLIDATED GATE: 8/8 PASS`), `control-artifact-results.txt` (the one-way
positive control: **staged v0.27.13 = 0** exact-const `'BEGIN'` in 582
`daemon.*` modules, **v0.27.12 = 1** and **v0.27.11 = 1** hit, both at
`daemon.tools.ens_db_tools::._dry_run_sqlite_safe` `const[11]` — so the
absence is non-vacuous), `reader-cross-validation.txt` (two independent
readers — native PyInstaller 6.19.0 `CArchiveReader`/`ZlibArchiveReader` vs the
lane's custom reader — agree on **4632** PYZ modules: identical TOC key set,
**0** differing TOC tuples, byte-identical `marshal.dumps(code)` for the nine
key daemon modules) and `extraction-inventory.txt` (the CArchive/PYZ extraction
inventory: 4632 modules, type census, `daemon.*` = 582, and the full list of
python-bearing CArchive entries).

> **Provenance of the four consolidated outputs (disclosed).** The lane's own
> runs of these steps printed to its terminal only; no stdout file was
> persisted. The four files above were therefore **re-run at bundle-assembly
> time** with the lane's own scripts and the lane's own inputs: `gate.py`
> (unchanged, byte-identical copy), the PYZ re-extracted **from the staged
> artifact itself** and verified byte-identical to the lane's `pyz.bin`
> (sha `44fa2e06…`), and the two control PYZs. All four reproduce the verdict
> record's §2 numbers exactly (8/8, 0-vs-1-vs-1 BEGIN hits, 4632 modules,
> 0 TOC diffs, 9/9 identical code objects). The driver scripts used are
> included as `xval.py` and `control-begin.py`; the re-extraction was done
> with the lane's own `extract.py`. The reader cross-validation needs
> CPython **3.14** (the artifact is 3.14-built; the house's certified 3.13
> interpreter cannot `marshal`-load its code objects) — the uv-managed
> `cpython-3.14.7` was used with the certified venv on `PYTHONPATH` for
> PyInstaller's readers, exactly as the lane did.

## §3 — Isolated boot smoke from the staged binary (PASS)

`sv3-boot/evidence/` carries the twelve evidence files of the boot leg: the
**launch + cgroup proof** (`launch.txt` — transient user unit
`sv3-v02713-boot.service`, `ControlGroup=/user.slice/…/app.slice/…`, i.e.
**not** the live `/system.slice/ensemble-main.service`), the **HTTP bodies**
(`livez.body`, `readyz.body`), the **pool-state stability probe**
(`probe-stability.txt` — three further probes, `checkedout 0 / size 15 /
overflow -6`, all components true, `grace_active=false`, `leak_signal=false`),
the **scratch-PG setup and teardown** (`initdb.log`, `pg.log`,
`pgdata_path.txt`, `teardown-pg.txt` — DROP DATABASE + DROP ROLE, residual
census `0|0`, cluster stopped, PGDATA removed, port 5461 free), the
**unit teardown** (`teardown-unit.txt` — SIGTERM-only, `Result=success`,
MainPID 0, cgroup gone, port 9783 free, all five pids gone, no name-kill),
the **journal excerpt** (`unit-journal-full.txt`) and the **live fence**
(`live-baseline.txt` before, `live-fence-final.txt` after — live `version
0.27.12`, MainPID 2865182 unchanged, `ActiveEnterTimestamp` unchanged,
`NRestarts 0`, `state.json` sha `c745b9df…` byte-identical to the baseline).
The 81 MB arena binary that the smoke executed is **not** in this bundle; its
path and sha are recorded in `SKIPPED-FOR-SIZE.txt` §3.

## §4 — The ladder from the staged source tree (17/17 PASS)

`sv4-ladder/` carries every ladder artifact: the **full run**
(`ladder-run.log` — `LADDER: OK (mode=--run, rows matched=17)`, 17/17 PASS,
zero skips), the **check mode** (`check-mode.log` — every row `COLLECTED-OK`),
the **T-H1 sanity negative** (`t-h1-g99.log` — `LADDER: FATAL … ZERO gates
were executed`, exit 1), the **full re-runs of the two rows whose summaries
were displaced by the ladder's `tail -3` window** (`g2.1-rerun-full.log`,
`g2.4-rerun-full.log`), the **§5 affected-pack results**
(`3a-census-families.log` — 5 passed; `3b-version-assert.log` — 1 passed),
and the **scratch-PG setup/teardown** (`raw/initdb.log`, `raw/pg-server.log`,
`pg-teardown.log`, `pg-teardown-prestate.txt`). `raw/` holds the pristine
copies of the same logs as they were first written. The three **re-pointed
script copies** the run used are included (`dbpool_gate_ladder.sh`,
`dbpool_collected_fence.sh`, `sv4-fence.sh`) together with `diffs/`, which
shows the re-point is copy-only: the ladder's `WORKTREE` and `FENCE=`, the
fence's `WORKTREE`, and the wrapper's `SV4_WORKTREE` — **four path-valued
lines, no logic change**; the staging worktree itself was never modified
(proof in the verdict record §4: the ladder certified
`/home/nea/ensemble-src-wt-stage-v02713/daemon/__init__.py 9.0.2`).

## Assembly notes

* **Fences honoured.** Read-only on `releases/**` and on the staging worktree;
  no test runs; no source or test edits; no verdict change; no
  promote/rollback/flip/restart; nothing killed; `.env` never printed; the
  staging worktree was **not** deleted. The bundle lives only under
  `.agents/tester/RESULTS/raw/2026-10-29-v02713-staged-verification/` in the
  evidence worktree.
* **Counts.** `sv1-integrity/` = **27** files (25 outputs + 2 reproducing
  scripts) — the brief said 23; every file present in the leg scratch dir was
  taken, none dropped. `sv2-symbols/` = **27** files. `sv3-boot/evidence/` =
  **12** files (exactly the 12 named). `sv4-ladder/` = **23** files + 3
  generated diffs. `diffs/` is generated at assembly time and is labelled as
  such; everything else is a byte-verbatim copy (verified with `cmp`).
* **`*.log` and the git index.** `.gitignore:54` excludes `*.log` silently, so
  the bundle must be staged with `git add -f` on this exact directory.
* **`MANIFEST.sha256`** covers every file of this bundle except itself;
  verify with `sha256sum -c MANIFEST.sha256` from the bundle root.
