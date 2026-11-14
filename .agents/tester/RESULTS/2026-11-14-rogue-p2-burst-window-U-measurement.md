# E2 Leg (D): Burst-Window U Measurement at the PRE-A2 Tip — PASS (trigger dormant)

- **Date:** 2026-11-14 (UTC) — run window 21:15:47Z→21:19:44Z (attempt 2, the recorded measurement)
- **Commission:** rogue-containment-p2 lane, component E2 § D MEASUREMENT — PURE TELEMETRY, arms nothing. Gates the lane's code phases (B2/A2 build starts only after this evidence lands).
- **Worktree:** `/home/nea/ensemble-src-wt-rogue-contain` · branch `feature/rogue-port-containment` · **HEAD `9b59bafcdb9c0fa0f20182f8f87ad008d82b0538`** (exactly the E2 step-0 fixed-harness commit — the commission allowed `9b59bafcd` or later docs-only; tip is exactly `9b59bafcd`)
- **Method:** fenced mirror measurement — N=6 cycles; each cycle boots the daemon DIRECTLY (`<worktree>/.venv/bin/python -m daemon`, CWD=worktree, NEVER launcher.sh) against a scratch port held by a LIVE claim-less holder; per-cycle U = wall-clock child start → exit via `date +%s%N`. NO claim planted by the harness; per-cycle `ENSEMBLE_PORT_CLAIMS_DIR` tmp dir EMPTY at cycle start (asserted by the script); the claim gate PASSES and the LEGACY bind-retry ladder (30 s ceiling, exhaustion ⇒ exit 1) is what is measured. Provenance asserted pre-run: `daemon.__file__` = `/home/nea/ensemble-src-wt-rogue-contain/daemon/__init__.py`.

## VERDICT (telemetry-only — arms nothing)

**Measured median U = 34.472 s ≤ 58 s ⇒ "trigger dormant".**

Per the binding verdict rule: measured median U > 58 s ⇒ "Lane-4 trigger would have fired (historical evidence only — trigger retired by CLOSED-BY-A2 once C1+C2+C3 hold)"; median ≤ 58 s ⇒ "trigger dormant". The measured median is 34.472 s, so: **trigger dormant**. Consistent with the in-code arithmetic (`bind_retry.py`: U ≈ boot-to-prebind B + 30.0 s budget; measured B ≈ 4.4–5.0 s on the non-frozen worktree venv ⇒ U ≈ 34.5 s). NO launcher.sh edits (either verdict routes, never edits).

## Per-cycle results (N = 6)

| Cycle | U (ms) | Exit code | Ladder ran ("Bind retry budget exhausted" lines) | Refusal markers | Claims dir after cycle |
|---|---|---|---|---|---|
| 1 | 34414 | 1 | 1 | 0 | `any-18790.json` (daemon's own claim — gate passed) |
| 2 | 35010 | 1 | 1 | 0 | `any-18790.json` (same shape) |
| 3 | 34427 | 1 | 1 | 0 | `any-18790.json` (same shape) |
| 4 | 34410 | 1 | 1 | 0 | `any-18790.json` (same shape) |
| 5 | 34726 | 1 | 1 | 0 | `any-18790.json` (same shape) |
| 6 | 34517 | 1 | 1 | 0 | `any-18790.json` (same shape) |

- **Median U = (34427 + 34517) / 2 = 34472 ms = 34.472 s.** Min 34410 / max 35010 / spread 600 ms.
- Per-cycle expected exit **1** (bind-retry exhaustion ⇒ legacy crash track) — ALL 6 cycles match. Exit 78 count: **0** in the recorded run (invalid-leg rule not triggered). No exit 3 / STARTUP_FAILURE / unexpected codes.
- Every cycle's log carries exactly one `Bind retry budget exhausted on 0.0.0.0:18790 after 6 attempt(s) over 30.0s (total budget 30.0s, errno 98) — exiting 1` line (the ladder, not some other exit-1 path).
- **NO CLAIM PLANTED held throughout:** each per-cycle claims dir started EMPTY (script-asserted); the only file appearing is the daemon's OWN claim written by the passing gate (`try_claim` → kind="taken"), payload `install_dir=/home/nea/ensemble-src-wt-rogue-contain, port=18790, host=0.0.0.0, version=0.30.11` — the claim-gate-passes shape this leg requires (NOT the refuse-foreign leg).

## Holder

- **Holder port: 18790** (first free port in the 18790-class scratch band 18790–18799; live trio never touched).
- Holder shape: live python socket squatter — `bind(("0.0.0.0", 18790))` + `listen()`, **cwd = scratch install dir** `/tmp/rogue-u-measure-277961/holder-install-dir` (verified via `/proc/<pid>/cwd` before the cycles), claim-less (no claim file anywhere for it).
- Holder alive + port LISTEN re-asserted before EVERY cycle (a mid-run holder death would have flipped later cycles into successful boots — would have been STOP-and-investigate, never data).
- Teardown: SIGTERM to the holder PID this script owned only; verified exited within 10 s ("HOLDER-TEARDOWN: exited on TERM"); scratch port 18790 unbound post-run. No SIGKILL anywhere.

## Fence digest (redacted — names + shapes, never values)

| Fence var | Value shape |
|---|---|
| umask | 077 |
| HOME | FAKE_HOME under `/tmp` (escape-guarded: export-took assert + `/tmp/*` prefix assert + not-under-`/home/nea` assert) |
| POSTGRES_HOST / POSTGRES_PORT | `127.0.0.1` / `5432` (from the worktree fence `.env`) |
| POSTGRES_USER / POSTGRES_PASSWORD | `<set, len 16>` / `<set, len 32>` (from the worktree fence `.env`; never printed, never captured) |
| POSTGRES_DB | `ensemble_dev_scratch` (scratch only; forbidden-name guard `ensemble_prod/ensemble_dev/ensemble_prod_test` ⇒ FATAL 99, copied from the fixed harness) |
| ENSEMBLE_SELF_ENV | sandbox |
| ENSEMBLE_DATA_DIR | `/tmp/rogue-u-measure-277961/data-cycle-<i>` (fresh per cycle) |
| ENSEMBLE_PORT_CLAIMS_DIR | `/tmp/rogue-u-measure-277961/claims-cycle-<i>` (fresh per cycle, mode 700, EMPTY at cycle start) |
| ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES | 0 — pinned EXPLICITLY (never `env -u` on it; 2026-10-31 sweep-killed-live incident) |
| ENSEMBLE_DB_AMBIENT_ALLOW | 1 (see attempt-1 disclosure below) |
| PORT / HOST / DAEMON_PORT | 18790 / 0.0.0.0 / 18790 |
| PG scrubs | per-variable ONLY: parent `unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT` + `DATABASE_URL PGHOST PGPORT PGUSER PGPASSWORD`; child invocation additionally wrapped in `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PGHOST -u PGPORT -u PGUSER -u PGPASSWORD` (the `env -u POSTGRES_*` glob idiom is a silent no-op and was never used) |
| OPENAI_* / EMBEDDING_* / SYSTEM_ENCRYPTION_KEY | inherited from the worktree fence `.env` per the lane's env_scope (fence + OPENAI_*/EMBEDDING_*/SYSTEM_ENCRYPTION_KEY, mode 600); values never printed |
| Interpreter | `<worktree>/.venv/bin/python` (worktree venv, NEVER main-checkout `.venv`, no bare `python`) |

## Attempt-1 disclosure (invalid leg — recorded as leg FAILURE, never data)

Attempt 1 ran ONE cycle and was stopped by the binding invalid-leg rule:

- Cycle 1 exited **78** with the `DB RESOLUTION REFUSED` marker (NOT `PORT OWNERSHIP REFUSED`). Root cause: my cycle fence omitted **`ENSEMBLE_DB_AMBIENT_ALLOW=1`** — the existing iter-1 ambient-PG guard (`daemon/ensemble_config.py`, tier=ambient: no `ensemble.json` in the fresh data dir + `POSTGRES_HOST`/`POSTGRES_DB` set + `ALLOW != "1"` ⇒ exit 78 pre-TCP) refused the boot BEFORE the port gate/bind stage.
- This is **not** fast-refuse leakage (A2's fast-refuse does not exist at the PRE-A2 tip — `daemon/services/port_holder.py` is absent, no knob declared); it is a fence-completeness gap vs the fixed harness template, which sets `ENSEMBLE_DB_AMBIENT_ALLOW=1` on every booting leg (happy `:168`, gate-must-pass refuse-foreign `:159`). The guard's own refusal text prescribes the same sanctioned value ("Set ENSEMBLE_DB_AMBIENT_ALLOW=1 for a sanctioned first boot").
- Correction was therefore harness-template-faithful, not an improvised fence. Attempt-1 evidence retained: log `/tmp/rogue-u-measure-277219/run-cycle-1.log` (sha256 prefix `cdca4a5d92e039d7`), holder TERMed cleanly, scratch port released before attempt 2. Attempt-1 U (4260 ms, exit 78) is **leg-failure evidence, never data** — excluded from every statistic above.

## Evidence excerpts (redacted; log sha256 prefixes of the recorded run)

- Ladder shape (cycle 1): `Bind retry 1/20 … retrying in 1.0s (elapsed 0.0s of 30.0s budget)` → `2/20 … 2.0s` → `3/20 … 4.0s` → `4/20 … 8.0s` → `5/20 … 15.0s` (5th wait truncated to remaining budget) → `Bind retry budget exhausted on 0.0.0.0:18790 after 6 attempt(s) over 30.0s (total budget 30.0s, errno 98) — exiting 1`.
- First bind attempt at 21:16:09Z, exhaustion 21:16:39Z ⇒ exactly the 30.0 s budget; U − 30 s ⇒ boot-to-prebind B ≈ 4.4–5.0 s per cycle (non-frozen worktree venv).
- Secret scan over ALL cycle + holder logs: zero hits for `sk-(proj|ant)-`, `POSTGRES_PASSWORD=`, private-key PEM needles.
- Per-cycle log sha256 prefixes (recorded run, cycles 1→6): `f1008d75dd0e8cd7`, `d1817c8f1a30bc86`, `a16bbf559a30fbd2`, `1b44443578cd3e23`, `d87337be83e12b5c`, `6912de3a289907fe`. Raw run root (ephemeral `/tmp`, not committed): `/tmp/rogue-u-measure-277961/`.

## Live-untouched invariants (asserted BEFORE and AFTER the whole run)

- Port probes 9797 / 9798 / 7979: pre-run 21:14:55Z and post-run 21:19:51Z **identical** — `0.0.0.0:9797 LISTEN (backlog 2048)`, `127.0.0.1:9798 LISTEN (backlog 5)`, `127.0.0.1:7979 LISTEN (backlog 2048)`.
- Shared host claims location `/tmp/ensemble-port-claims-1000/`: no writes in the run window (newest mtime 12:43:05Z, hours pre-run) — the per-cycle `ENSEMBLE_PORT_CLAIMS_DIR` override contained every claim write.
- Teardown TERM-only; the only process the harness ever signalled is the holder PID it planted; verified exited; scratch port 18790 unbound post-run; no stray holder/daemon processes remain.
- Zero live mutation: no DB writes outside the scratch DB (`ensemble_dev_scratch` @ 127.0.0.1:5432, reachable pre-run as `ensemble_dev_app`, PG 16.15); no migrations run (daemon dies pre-lifespan, before any evolution step — confirmed by the pre-bind shape and the absence of any post-bind activity in the logs); no repo-root `config.yaml` change; no launcher.sh edit; no pkill; no promote/tag/merge/push.

## Deferred to the post-A2 verify lane

The **fast-refuse-ON single-cycle U evidence** (the direct proof that A2's gate keeps the Lane-1 constraint — refusal near-instant, U ≪ 58 s) is **DEFERRED to the post-A2 verify lane, which will APPEND to this file**. The PRE-A2 tip has no fast-refuse knob to arm (RESOLVED: A2 declares none; the knob expectation is DELETED in § D leg-ordering).

## Disclosed residual (per § D)

Launchd-host residual (GAP-1, `docs/deployment.md:327,348`) sits outside `RestartPreventExitStatus` entirely and is untouched by this measurement — observability residual only, not a blocker.

## Notes

- Measurement script (not committed, `/tmp` scratch): `/tmp/rogue-u-measure-e2d.sh` — fence block copied from the fixed harness `boot_rogue_mirror.sh` @ `9b59bafcd`; the harness's pkill-overlay guard (A1 hygiene, HY pins) correctly flagged the script's `TERM`-of-holder + live-token co-occurrence on first write; restructured so the script carries scratch-named targets only and live-port probes run as separate read-only `ss` calls outside it. `ENSEMBLE_FENCED_TEST_CONTEXT=1` was NOT needed for the final script (scratch-clean by the guard's own standard).
- The `bind_retry.py` ceiling note's frozen-path estimate (B ≈ 18.3 s ⇒ U ≈ 48.3 s) remains the worst-case coupling anchor; this worktree-venv measurement (B ≈ 4.5 s ⇒ U ≈ 34.5 s) is a LOWER bound shape on a faster interpreter path — both sit under 58 s; the C3 closure arithmetic is unaffected either way.
