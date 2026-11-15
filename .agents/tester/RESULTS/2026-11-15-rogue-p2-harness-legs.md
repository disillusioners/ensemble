# Rogue-P2 Gate 3 — Fenced Mirror Harness Extension + 6-Leg Run (2026-11-15)

**Lane:** rogue-containment-p2 tester pass, Gate 3 (harness gap-fixes #2–#5 + legs a/b/c + full 6-leg execution)
**Worker:** worker instance (dispatched by Ari)
**Status:** ALL 6 LEGS PASS — v1 (original run) AND v2 (post-incident re-verification on the reconstruction)
**Worktree state at report time:** RECONSTRUCTED at /home/nea/ensemble-src-wt-rogue-contain, DETACHED HEAD @ a7aec8582 (branch claim released for finalize; NO commits made by this lane; harness + template ride as uncommitted edits; nothing on the branch)

## ⚠️ INCIDENT (first-class disclosure — report b740d48c, nest cluster ad9184ac)

At 00:51–00:53Z, mid-run, the commissioned worktree /home/nea/ensemble-src-wt-rogue-contain was DELETED. Forensics from this lane's own execution timeline: the deletion window coincides EXACTLY with leg-6's (same-install-handover) teardown — the harness's inherited cleanup line `rm -rf "$HOLD_DIR"` fired with `HOLD_DIR="$WORKTREE_ROOT"` (leg (c) uses the worktree as the holder's cwd = the daemon's resolved install dir, per the T9 attribution contract). The pre-extension harness treated HOLD_DIR as an owned scratch dir; leg (c) aliased it to the worktree. **Root cause: my leg-(c) implementation interacting with the landed cleanup — the same defect class as the 2026-10-12 live-install deletion incident (test code rm-ing a path it did not create).** A sibling report characterizes the actor as external; this lane's timeline evidence points at its own leg-6 cleanup — both reports stand on the record; the mechanism above is the one this lane can prove.

**Loss:** the day's uncommitted harness edits (gap-fixes #2–#5 + legs a/b/c) and the worktree checkout. **Not lost:** the branch ref (tip a7aec8582, unmoved — verified `git log --oneline -3 a7aec8582` contains no commit from this lane), all 6 v1 leg run logs (preserved in /tmp), PG-cluster logs, live-port probes.

**Fix:** cleanup rewritten to an OWNED_RM_DIRS allowlist — the harness rm's ONLY paths it created (SCRATCH_DIR/FAKE_HOME/CLAIMS_DIR + the refuse-foreign holder dir); HOLD_DIR is attribution-only and NEVER removed. Header carries the ⛔ OWNERSHIP GUARD banner citing this incident. Verified empirically: leg-6 v2 ran to PASS and the reconstruction SURVIVED its own cleanup.

**Recovery:** worktree re-created from the branch (pre-guidance), venv rebuilt (`uv sync`, python 3.14.7, RC=0), edits re-applied IN FULL from session context, HEAD detached to release the branch claim for the finalize owner. Rescue pack (NEVER delete): `/tmp/rogue-p2-g3-evidence-rescue-20261115T005850Z/` — 20 files: both reconstructed harness files, all 9 leg logs (v1 ×6 + v2 ×6 incl. leg1-3), live-port probes, PG lifecycle logs, prior tgz.

## Per-leg results (v1 = original run @ pre-deletion worktree; v2 = re-verification on the reconstruction @ a7aec8582 — both ALL GREEN)

| # | Leg | Expected | Actual rc | Marker assert | readyz | livez | Holder/claim | Verdict v1 | Verdict v2 |
|---|-----|----------|-----------|---------------|--------|-------|--------------|------------|------------|
| 1 | refuse-foreign | 78 + PORT OWNERSHIP REFUSED | 78 | ✓ claim-source refusal line, holder_pid/exe/cwd present, install=foreign-install-root ≠ self_install | n/a | n/a | claim planted at resolved gate path; live sleep-holder TERMed verified | **PASS** | **PASS** |
| 2 | db-poison | 78 + DB RESOLUTION REFUSED | 78 | ✓ `tier=ambient (ensemble.json absent)` — pre-TCP (guard text says so verbatim) | n/a | n/a | — | **PASS** | **PASS** |
| 3 | happy | {0,143} + graceful log + readyz 200 | 143 | ✓ no refusal markers; graceful_log_lines=1 | **200** | **200** | — | **PASS** | **PASS** |
| 4 | tier1-name-refusal (a) | 78 + tier-1 marker | 78 | ✓ `DB RESOLUTION REFUSED (rogue-containment): tier=resolved-dsn-targets-prod db=ensemble_prod self_env=sandbox resolution=explicit` (daemon/ensemble_config.py:346-349) — PRE-TCP, prod name existed only as scratch-json STRING, DSN host 127.0.0.1:1 CLOSED | n/a | n/a | — | **PASS** | **PASS** |
| 5 | foreign-holder (b) | 78 + socket-holder marker | 78 | ✓ `PORT OWNERSHIP REFUSED (socket-holder)` (port_ownership.py:931) + holder_pid=<spawns' holder pid> + holder_exe=/usr/bin/python3.12 + holder_cwd/install=<scratch foreign-install dir> (all token asserts green) | n/a | n/a | claim-less socket holder; TERMed verified | **PASS** (after holder fix) | **PASS** |
| 6 | same-install-handover (c) | NON-refuse: readyz 200, {0,143}+graceful, claim taken | 143 | ✓ no refusal markers; graceful_log_lines=1; claim takeover: recorded_pid == daemon_pid (rewritten by the daemon) | **200** | **200** | same-parent holder + v2 claim w/ matching lineage; holder observed takeover → released; TERMed verified | **PASS** | **PASS** |

Log sha256 prefixes (v1): leg1 8ce183317d9d, leg2 cdca4a5d92e0, leg3 7bb8ef9eeae7, leg4 bb34a627aa71, leg5 3cf35974c8a3, leg6 2b96ca53de0e. (v2 log files preserved whole in the rescue pack.)

## Gap-fix implementation notes

- **#2 provenance assert** — before any leg: `(cd worktree && $MIRROR_PY -c 'import daemon, sys; sys.stdout.write(daemon.__file__)')`, realpath-compared inside the worktree (WORKTREE_ROOT via `pwd -P`); mismatch ⇒ FATAL 99. Verified live: `daemon.__file__=/home/nea/ensemble-src-wt-rogue-contain/daemon/__init__.py`.
- **#3 FAKE_HOME 4-stage guard** — completed to tests/upgrade_test_lib.sh:69-102: (1) export-took (TRUE_HOME captured pre-export), (2) under /tmp, (3) resolved LIVE_PARENT (`$(cd $HOME && pwd -P)/agents-ensemble`) under /tmp, (4) never /home/nea.
- **#4 scratch-config template** — shipped `harness/scratch-config.yaml` (literal `port: 18790`), GENERATED per leg into the scratch dir with the ACTUAL port substituted (sed over the literal), grep-verified baked + `${PORT`-interpolation grep-REFUSED (whole file, comments included — the template's own prose was reworded when the check caught it). `ENSEMBLE_CONFIG=<generated>` exported every leg. Repo-root config.yaml untouched. The minimal daemon-only template BOOTS the real daemon (leg 3/6 readyz 200 proves it).
- **#5 bounded readyz poll** — happy-shaped legs: 45×1s poll of /readyz (early-break on daemon death) then /livez probe; the fixed sleep 25 is gone. Leg 3/6 answered 200 inside the budget.
- **Legs a/b/c** — see table; leg (b) needed two harness fixes en route: the holder originally closed its socket immediately when unwatched (hold-forever branch added — first run false-RED, daemon boot completed, caught loudly); leg (c)'s holder releases on claim-pid change (deterministic handover signal — takeover observed in holder log).

## T9 contract note (leg c) — MECHANISM: SAME-PARENT (primary) + v2-claim-with-matching-lineage (secondary)

Both mechanisms implemented and disclosed: holder and daemon are BOTH children of the harness script (spawned via `( cd … && exec … ) &` and `eval … &` — daemon ppid == harness `$$`; holder ppid == `$$`; identical parent cmdline ⇒ ratified claim-lineage layer reads match, AH-5). ADDITIONALLY the harness plants a v2 claim whose `ppid_cmdline_sha` = `lineage_token_hash(read_parent_cmdline($$))` computed with the worktree's own helpers — so the takeover path (`_lineage_foreign` → False ⇒ unlink + rewrite, port_ownership.py:576-584) is exercised POSITIVELY: post-readyz assert showed recorded_pid == daemon_pid (427606/427606 in v1; 434037/434037 in v2).

## Live-untouched invariants

- `ss -tlnp` probes of 9797/9798/7979 (users-column normalized — the raw probe output carries the probing pipeline's own transient pids; first leg caught this as a false-drift and the snapshot was normalized to the address shape): **IDENTICAL before/after every leg AND across the whole run** (final probe re-verified after PG teardown).
- DB name guard: `ens_e2e_scrub_probe` only; never ensemble_prod/ensemble_dev/ensemble_prod_test (harness FATAL 99 guard intact).
- ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY every run; umask 077; FAKE_HOME; per-variable PG scrub; scratch ENSEMBLE_DATA_DIR + SELF_ENV=sandbox (ambient `live` from the daemon env overridden); per-run ENSEMBLE_PORT_CLAIMS_DIR.
- Teardown: TERM-only everywhere (daemon real-pid TERM via daemon_descendant_pid; holder TERM with verified exit; pg_ctl fast stop for the scratch PG). No pkill anywhere. Captures redacted (log sha256 only; backend echo password-redacted).
- Scratch ports: 18790–18795 only (band guard enforces 18790–18796; 9797/9798/7979/8079 refused).

## PG cluster lifecycle (disposable, NEVER 5432)

- `initdb -A trust -U roguep2` at /tmp/rogue-p2-pgcluster; started via pg_ctl on **127.0.0.1:15432** (listen_addresses pinned; unix_socket_directories moved to /tmp after the root-owned /var/run/postgresql lock refusal — first start failed pre-bind, zero side effects).
- `createdb ens_e2e_scrub_probe OWNER roguep2` over TCP; PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=15432 PG_TEST_USER=roguep2 passed to the harness every leg.
- Teardown: `pg_ctl -m fast stop` → server stopped; TCP probe → connection refused; cluster dir + sock dir + logs rm'd. ensemble_dev/ensemble_prod NEVER touched (the worktree .env's ensemble_dev pointer was never read by the harness — all DB env translated from PG_TEST_* onto the scratch cluster).
- RECORDED addendum (POK rogue-cleanup-rider-recorded-addendum) for non-commit riders: /tmp/rogue-p2-pgcluster (PG data dir, ~21MB initdb footprint) + /tmp/rogue-p2-pgsock + two PG logs — all rm'd at teardown (connection-refused + port-freed proofs above); the 6 v1 leg logs + probes are preserved (not deleted) in the rescue pack; per-leg harness scratch dirs (~200KB × 12 runs) were self-cleaned by the harness (its own rm, ownership-guarded).

## Interpreter

/home/nea/ensemble-src-wt-rogue-contain/.venv/bin/python (rebuilt post-deletion via `uv sync`, RC=0, Python 3.14.7). Never bare python; never the main checkout's venv.

## Deviations from the original task spec (disclosed)

1. **No commit** — the test leader's incident guidance (mid-run) forbids branch writes; finalize owns the tip. The evidence file + harness + template sit in the rescue pack and as uncommitted edits on the DETACHED reconstruction, ready for the finalize to land.
2. **Worktree re-created** (pre-guidance, then HEAD detached to release the branch claim) — disclosed; no git mutations beyond the checkout state; branch ref verified unmoved at a7aec8582.
3. Two harness quick-fixes en route (both <20 lines, test-code-only, authorized by the task): the holder hold-forever branch, and the live-probe users-column normalization. Plus the incident-mandated OWNED_RM_DIRS cleanup rewrite.
4. Legs 1–5 v1 ran against the pre-deletion worktree at branch tip ccb35112b-equivalent content (the branch had advanced to a7aec8582 mid-lane — pin-robustness commit by the prior lane; no harness-file overlap); leg 6 v1 ran against the same content. All v2 legs ran at a7aec8582 on the reconstruction. Both sets ALL GREEN — the harness content is identical in both (reconstruction is a byte-faithful re-application).

## Deliverables

- Harness: `.agents/shared/planning/rogue-containment/harness/boot_rogue_mirror.sh` (757 lines, extended; syntax-checked) + `harness/scratch-config.yaml` — in the reconstruction AND the rescue pack.
- Evidence: this file (+ raw logs in `/tmp/rogue-p2-g3-evidence-rescue-20261115T005850Z/`).
- Commit: **DEFERRED to finalize** per leader guidance (originally tasked as on-branch commit).
