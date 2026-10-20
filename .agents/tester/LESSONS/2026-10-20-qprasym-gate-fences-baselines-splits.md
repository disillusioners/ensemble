# Lesson — qprasym functional gate (2026-10-20): fence gaps, baseline anchoring, structural pack splits

Gate: `fix/question-pause-resume-asymmetry` @ `b1ec0c0d7` (base `1ebdf469f`) — 9/9 PASS, READY FOR MERGE + STAGING. Full evidence: `RESULTS/2026-10-20-question-pause-resume-asymmetry-functional-verification.md`.

## 1. Env-fence gaps that bite dev-lane daemon boots (PORT > DAEMON_PORT)
- **`PORT` leaks through `config.yaml` (`daemon.port: ${PORT:-8088}`) and the yaml-derived pydantic init kwarg BEATS the `DAEMON_PORT` env var.** Ambient `PORT=9797` (live prod) made the first boot run its whole lifespan then die at bind — protective only because prod owns the port. On a host with 9797 free, the same leak binds prod's port silently.
- **House scrub list must include `-u PORT -u HOST` (+ explicit `PORT=8079 HOST=127.0.0.1`)** in addition to POSTGRES_*/ENSEMBLE_*/SCHEDULER*/SSL_* (16→18 vars). Recorded in KB + PACKS.md maintenance ledger.
- **The `.env` dev credential `ensemble_dev_app` had a stale SCRAM verifier host-wide.** Repair recipe: socket peer-auth superuser → `ALTER ROLE … WITH PASSWORD <.env value>` → verify TCP. Dev DB is LOCAL (127.0.0.1:5432 / `/var/run/postgresql`), not the prod host.

## 2. Partition-baseline adjudication: anchor to the CURRENT ledger, never the PACKS.md row
- Three partitions (P-4/P-5/P-6) would have produced false-NEW verdicts from their 2026-09 rows; the current QUARANTINE.md (rows through 2026-10-20) + newer RESULTS rows are the authoritative anchors. P-6's row was even self-inconsistent (1,895 ≠ 1,890).
- **Static exculpation is not enough for big unledgered clusters.** P-10's ×29 vision-facade family and P-8's 43F both got real base legs (detached worktree at merge-base + symlinked `.venv` + `PYTHONPATH` pin + import-resolution guard) — head-only failure count = 0 is the only executed proof. The idiom is cheap (~73s for 1,661 nodes); prefer it whenever a partition yields >5 unledgered reds.
- Wall-clock budget nodes (perf 5ms tick, 2× anchors) need quiet-host + base-leg arbitration before ANY regression claim — both directions: load can false-red (P-10's 5.4ms) and baseline drift can true-red identically at base (perf anchors at 4–8× pins, base≡HEAD).

## 3. Structural pack overruns → split, never raise the cap (two shapes this gate)
- **P-8/perf:** single-process-only suites (module-level producer globals read by consumers) CANNOT run under xdist — the "producer-die consumer-fail" signature is worker isolation, not load. Serial pack + quarantine-aware `-k` with written removal conditions. A 266s-passing cell is pack-unrunnable by budget → pack-level exclusion + solo-run + split-debt note.
- **P-12:** the registered pack had been deleted upstream weeks earlier — PACKS.md pointed at a dead path. Verify pack EXISTENCE (not just registration) before dispatch; when recovering a deleted pack's scope, take the invocation verbatim from `git show <del-commit>^:<path>` and mirror its addopts/deselects/timeout-override exactly. Explicit file lists (not globs) in split packs so new files are never silently skipped.
- **Runner style:** bare `uv run` fails off-PATH (uv at `~/.local/bin`). New packs: `.venv/bin/pytest` direct, or 3-way resolution (`~/.local/bin/uv run --no-sync` → `uv` → `.venv/bin/python -m pytest`).

## 4. Commission-craft notes
- Killed runs yield NO failure node ids (`-rf` never prints; `lastfailed` not updated) — a completing pass with `--junitxml` is mandatory before any bucket assignment; magnitude-consistency guesses about family composition are unreliable (P-12's httpx-class guess was 17/23 wrong in composition).
- A gate's own lanes contaminate the shared worktree: porcelain tripwires must pre-declare the expected dirty set (it grew to 9 lines by gate end, all attributed); `install-audit.jsonl` kms_issue rows appear whenever a lane imports daemon code with cwd in a worktree — revert, never commit.
- Tip drift: "code frozen at X" claims should be verified (`b1ec0c0d7` carried a comment-swap beyond the stated freeze `e7634c39d` — behavior-neutral, but verdicts must be tip-bound).
