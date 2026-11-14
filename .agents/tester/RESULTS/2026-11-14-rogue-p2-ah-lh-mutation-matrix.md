# rogue-containment-p2 — AH/LH mutation matrix + post-A2 legs (S6/D/harness)

**Lane:** post-A2 verification (worker, tester) · **Pin SHA:** `cc154c308` (A2 tip)
**Date:** 2026-11-14 · **Venue:** scratch worktree `/tmp/rc-p2-va2-a7f3` (DETACHED @ cc154c308, `uv sync --frozen`, `daemon.__file__` provenance verified inside venue)
**Full table:** `tests/bind_race_port_ownership_mutation_matrix.md` §"A2 AH/LH mutation-matrix execution" (row × mutation × pins reddened × restore × green-after) — this file is the compact cross-reference + the Leg-2/Leg-3 evidence pointer.

## Row set executed (union per A2 §8 + E2↔A2 numbering map + landed pins)

AH-M1..AH-M16 (A2 §8 skeleton) + E2-side AH-M17 (tcp4-only parse), AH-M18
(EACCES ⇒ foreign), AH-M19 (rides AH-M10 — same content per the numbering
map) + LH-M1..M3 (alert family). AH-M7 rides AH-M6 (holder check lives
inside the gate ⇒ one placement move = both mutants). **16 executed rows,
13 REDDENED, 3 findings**, clean-tree control 39 passed, sha256 PRE==POST
every row, green re-run after every restore.

## Findings (failed to redden — for the leader/architect)

| # | Row | Root cause | Re-aim |
|---|-----|-----------|--------|
| F-1 | AH-M4 (+AH-M4b proof leg) | AH-4's global `pid_alive_with_starttime=True` monkeypatch makes the planted foreign CLAIM refuse via the claim path — verdict over-determined; holder path deleted ⇒ AH-4 still passes | assert `marker["source"]=="socket-holder"` or drop the global liveness monkeypatch |
| F-2 | AH-M8 + AH-M8b | AH-8's module pin is absence-shaped (`isinstance(res, list)` — always true); neither the gate stale-degrade branch nor the confirm-match starttime has a tight pin | gate-level pin: foreign holder + dead starttime ⇒ no refusal AND the "starttime mismatch (stale)" WARNING asserted |
| F-3 | AH-M15 | `confirm_holder`'s TAIL real-/proc liveness check shields every liveness-only mutant (fixture pid 4242 is fake-dead); liveness-only vs full-rewalk indistinguishable on this fixture | fixture with a REAL live pid + re-walk dropping the inode (liveness-only confirms ⇒ RED) |

## Red evidence (pin node ids + assertion substrings)

- AH-M1/M2 → `test_AH1_foreign_holder_without_claim_refuses` (`KeyError: 'source'` for M2); M2 also reddens `test_AH10_lineage_mismatch_refuses`
- AH-M3 → `test_AH3_tables_absent_degrade` (RuntimeError raised)
- AH-M4b → `test_AH1_…` only (AH-4 green = F-1 proof)
- AH-M5 → `test_AH5_same_install_holder_non_refuse` (unexpected SystemExit 78)
- AH-M6/M7 → `test_gate_refusal_never_enters_bind_retry` (`AssertionError: bind must NOT be reached after an ownership refusal`)
- AH-M9 → AH-9 + AH-10; AH-M10/M19 → AH-10; AH-M11 → AH-11 + 2 accepted blast-radius (E_ii_M1, promote-shape); AH-M12 → AH-12
- AH-M13 → `test_AH13_hex_port_decode_and_state_filter` + `test_AH13_v4mapped_tcp6_only`; AH-M16 → `test_AH15_normalize_script_name_arg` (`h1==h2==h3` fails)
- AH-M14 → both `test_AH14_*` module pins (confirm returned True)
- AH-M17 → `test_AH13_v4mapped_tcp6_only`; AH-M18 → `test_AH3_fd_eaccess_degrade`
- LH-M1/M2/M3 → `test_LH1_write_refusal_family_record_best_effort` / `test_LH2_surface_carries_family_source` / `test_LH3_watchdog_refusal_marker_latch`

Raw logs: `/tmp/rc-p2-evidence/<ROW>.log` (red) + `<ROW>.green.log` (39-passed after restore). Spawn-safety audit + one disclosed parallel-writer incident (contaminated rows re-run serially; serial evidence authoritative) — both documented in the full table file.

## Leg 2 — fast-refuse-ON single cycle (E2 §D deferred slot)

See `.agents/tester/RESULTS/2026-11-14-rogue-p2-burst-window-U-measurement.md`
(appended section "Fast-Refuse-ON Single Cycle — DIRECT U EVIDENCE"). Result summary:
**exit 78 + `PORT OWNERSHIP REFUSED (socket-holder)` marker with pid/exe/cwd
tokens, U = 5111 ms (5.111 s) ≪ 58s, ladder never entered** — Lane-1 constraint (U ≤ 58s) survived the A2 gate;
closure conditions C1 (A2 merged) TRUE, C3 (U ≤ 58s) TRUE (median 34.472s from
the pre-A2 leg + this direct post-A2 refused-boot U), C2 lives in S9b.

## Leg 3 — scratch-port boot regression per harness (9b59bafcd)

Three harness legs + disposable PG cluster (user-space `initdb -A trust -U nea`
@ port 55432, never 5432; scratch DB `rc_p2_happy_scratch`; superuser verified
`rolsuper=t`):

- **refuse-foreign PASS** (exit 78 + `PORT OWNERSHIP REFUSED`, holder pid/exe/cwd
  + `holder_install=foreign-install-root` vs `self_install=<worktree>`)
- **db-poison PASS** (exit 78 + `DB RESOLUTION REFUSED` tier=ambient, pre-TCP)
- **happy: HARNESS-SHAPE BLOCKED — substance GREEN.** The disposable cluster
  CLEARED the known DB wall (boot reached full startup: `_ensure_postgres_columns`
  drift_events/boolean-repair/autovacuum legs applied cleanly, MCP warmed,
  claim written by the passing gate, zero refusal markers). The harness leg
  itself FAILED on two latent harness defects never before exercised (every
  prior happy attempt died at the DB wall):
  1. **stale readyz probe path** — the harness probes `/api/health/readyz`;
     the shipped public route is ROOT-LEVEL `/readyz` (`daemon/api.py:6318`,
     public per `daemon/middleware/auth.py:87`) ⇒ probe observed **401
     no_credentials** from the auth middleware (audit line in the log).
     Supplementary boot with correct probes: `/readyz` = **200**, `/livez` = 200.
  2. **exit-code expectation** — harness expects `exit_code=0` after
     `kill -TERM`; observed **143** in BOTH the harness leg and a
     directly-TERM'd boot whose log shows `Graceful shutdown complete` +
     `Application shutdown complete`. Empirically: TERM-at-ready ⇒ graceful
     shutdown logs ⇒ wait-rc 143 (reproducible; mechanism not chased in this
     lane — uvicorn capture_signals disposition question for the harness owner).
- **Residue incident (cleaned, disclosed):** the harness's `eval "$CMD" &` +
  `kill -TERM $DPID` TERMed an intermediate shell, NOT the daemon python —
  the happy-leg daemon (pid 335807) SURVIVED as an orphan holding 18790 (fd
  inherited by its MCP children 335966/336117). All three verified as this
  run's own (environ HOME=/tmp/rogue-mirror-home-335796, PORT=18790) and
  TERMed; scratch ports 18790/18792/55432 all released; no strays remain.
  Harness fix suggestion (owner's call, NOT applied here): resolve the daemon
  pid via pgrep on the ROGUE_MIRROR_CMD pattern (or drop eval), probe `/readyz`,
  and set EXPECT_RC to the observed TERM disposition.
- Teardown: `pg_ctl stop -m fast` + cluster dir removed; 55432 released; live
  trio identical pre/post (`0.0.0.0:9797`, `127.0.0.1:9798`, `127.0.0.1:7979`);
  secret scans clean; zero live mutation; supplementary run.log kept at
  `/tmp/rc-p2-happy-manual-336990/run.log` (sha256 `c36314a9f0e08d7448b3183c39cd35a56485b34d3c6eac7bc7b7845bd20d6993`).

## Artifacts

- Full mutation table: `tests/bind_race_port_ownership_mutation_matrix.md` (this commit)
- U-measurement append: `.agents/tester/RESULTS/2026-11-14-rogue-p2-burst-window-U-measurement.md` (this commit)
- Commit: test(rogue-containment-p2): AH/LH mutation matrix + post-A2 fast-refuse U cycle (sha recorded in the lane report)
