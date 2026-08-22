# Demo-Env Live Validation — Auto-Restart Phase 1 Follow-ups (P4/P7 + exit-75)

- **Date:** 2026-08-22 (15:07–15:40 +07 / 08:07–08:40 UTC)
- **Operator:** ensemble devops agent
- **Branch:** `fix/auto-restart-phase1-followups` @ `e326b731` (2 commits on `latest` `5e33789d`: `a24bf643` launcher P5b dedupe fix, `e326b731` launcher tests 74/74)
- **Scope:** post-merge follow-up #1 from `.agents/tester/RESULTS/2026-08-22-auto-restart-phase1-premerge.md` §5/§7-1 (demo-env re-validation of P4/P7), plus follow-up #4 (`python -m daemon` exit-75 live smoke).
- **Verdict: ✅ ALL GATES PASS.** Demo daemon ends UP and healthy. **Live (9797) NEVER touched** — pids `30054 31150` asserted identical at pre-deploy baseline, after deploy, after every stop, and at final state.

---

## 0. Environment identity (asserted, not assumed)

| Item | Value | Evidence |
|---|---|---|
| Repo/branch | `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble` @ `fix/auto-restart-phase1-followups` | `git log` (tip `e326b731`) |
| Demo install dir | `~/agents-ensemble-demo` | deploy log phase 0 |
| Demo port | 7979 (free pre-deploy) | `lsof` |
| Demo DB | `ensemble_demo` — engine log marker in BOTH boots: `Creating PostgreSQL engine: localhost:5432/ensemble_demo` (15:08:10 and 15:35:34) | `data/launcher.log` |
| Demo pre-deploy state | FAKE-STAGED (52-byte stub `ensemble-prod`, 87-byte stub `launcher.sh`) — replaced by this deploy with the real 47,985,824-byte binary + 25,346-byte launcher | `ls -la` before/after |
| LIVE (never touched) | `~/agents-ensemble` :9797, pids `30054 31150` — identical at every checkpoint | `lsof -ti:9797` ×6 |
| Sandbox (task D) | port 8377 (verified free, never bound), unreachable PG `127.0.0.1:54329`, throwaway data dir `/tmp/ar-exit75-smoke.*` (removed) | smoke transcript |
| Live guard | `ENSEMBLE_DEPLOY_LIVE` never set; `deploy.sh` invoked with `demo` only | command history |

---

## A. Real `deploy.sh demo` redeploy + health gates — PASS

**Invocation:** `scripts/deploy.sh demo --build` (bare `uv run python -m PyInstaller ensemble.spec` — no make targets; `--build` forced so binary provenance provably = this checkout).

### Deploy log excerpts (raw)

```
deploy[demo]: phase 0/5 preflight — target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo
deploy[demo]: port 7979 is free
deploy[demo]: live 9797 baseline pids: 30054 31150  (asserted unchanged after every phase)
deploy[demo]: phase 1/5 build
deploy[demo]: build forced (--build)
... PyInstaller 6.19.0 / Python 3.13.3 / arm64 ... Building EXE ... completed successfully.
deploy[demo]: phase 2/5 stage → /Users/nguyenminhkha/agents-ensemble-demo
deploy[demo]: env source: .env.prod.demo
deploy[demo]: phase 3/5 stop (ownership-scoped ...)
stop-ensemble: WAIT_S resolved to 70s — default (70 = 60s graceful + 10s margin)
stop-ensemble: no processes owned by ... — nothing to stop
deploy[demo]: phase 4/5 start
deploy[demo]: launcher started (nohup) — logs: ~/agents-ensemble-demo/data/launcher.log
deploy[demo]: phase 5/5 health gate (livez ≤60s / readyz ≤120s) on :7979
deploy[demo]: livez OK:
{"status":"alive","uptime_seconds":1.7008566856384277,"version":"0.10.5"}
deploy[demo]: readyz OK:
{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":"2026-08-22T08:08:11.445058+00:00"},"draining":false}
deploy[demo]: health gate GREEN — demo deploy complete
deploy[demo]: live 9797 survival: pids unchanged (30054 31150 )
deploy[demo]: done — demo deployed to /Users/nguyenminhkha/agents-ensemble-demo (port 7979, db ensemble_demo)
```

### Health-gate timings (both within budget)

| Gate | Budget | Deploy-run | Restart-run (15:35) | Final (15:39) |
|---|---|---|---|---|
| `/livez` 200 | ≤60s | green at daemon-uptime **1.70s** (checked_at 08:08:11Z) | green **t+17.09s** from launcher spawn | 200 in 2.1ms |
| `/readyz` 200 | ≤120s | green same second (08:08:11.44Z), reasons `[]` | green **t+17.09s** | 200, reasons `[]` |

### P5b tie-in — deployed launcher contains the fix — PASS

```
$ grep -n "P5b dedupe" ~/agents-ensemble-demo/launcher.sh
369:        # P5b dedupe: only warn when this is the sole non-executable report
```
Deployed launcher.sh is 25,346 bytes @ 15:08 (staged from repo tip `e326b731`, which carries `a24bf643`) — demo provably runs the fixed code.

### Operational note (incident, self-inflicted, resolved)

The first launcher session (started by deploy.sh inside my shell tool) was TERMed ~24 min later when the wrapping bash call hit its own timeout — the tool kills its process tree. **Net effect was a by-the-book graceful stop**: launcher trap forwarded ONE SIGTERM, daemon ran the full 9-step teardown (`Graceful shutdown complete`, launcher exited 143 with child's code). No crash, no burst-budget consumption (the SHUTDOWN_REQUESTED path writes no state — `.launcher-state` relics `crash_count=1`/`last_exit=137`/`window_start` are Aug-16 fake-staging leftovers, never written today). All subsequent starts used a double-fork/setsid detach immune to tool-tree kills. **Lesson for future ops: never let a tool-scoped shell be the ancestor of a long-lived daemon.**

---

## B. P7 — `ENSEMBLE_READINESS_FORCE_DEGRADED` drill (green→red→green) — PASS

Knob applied via staged `~/agents-ensemble-demo/.env` (the launcher's env source, ADR-014), restarts via the real launcher.

| # | Time (+07) | Action | Result |
|---|---|---|---|
| 1 | 15:36:31 | Baseline | `/readyz` **200** `{"status":"ready",...,"reasons":[]}`, `/livez` 200 |
| 2 | 15:36:31 | Append `ENSEMBLE_READINESS_FORCE_DEGRADED=1` → REAL stop (single-TERM via launcher, 2.3s) → restart | — |
| 3 | 15:36:49 | Probe | `/readyz` **503** `{"status":"degraded","components":{"database":true,"queue_freshness":true,"services":true},"detail":{"reasons":["readiness: degraded forced by ENSEMBLE_READINESS_FORCE_DEGRADED (drill)"],...}}`; `/livez` still **200** (`t+4.18s` to livez-green). Log: `[Readiness] degraded: readiness: degraded forced by ENSEMBLE_READINESS_FORCE_DEGRADED (drill)` |
| 4 | 15:37:21 | Remove knob + restart | — |
| 5 | 15:37:27 | Probe | `/readyz` **200** `"reasons":[]` (t+3.66s to green) |

Semantics confirmed live on the DEMO frozen binary: fail-safe one-way (only degrades), `/livez` independent of readiness, env read at boot (restart required for a daemon — matches `readiness.py:63-66` comment).

---

## C. P4 — WAIT_S 11-case edge table (real deployed demo stop path) — PASS

Resolution cases executed with the **real script against the real demo install** (`DRY_RUN=1 bash scripts/stop-ensemble.sh ~/agents-ensemble-demo 7979` — resolution happens before ownership check and no signals are sent; only the single `DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` line in the demo `.env` was mutated between probes, restored pristine after). Canonical table: `tests/test_stop_ownership.sh` §7 (pre-merge record).

| Case | Staged condition | Expected | Actual (resolution line, verbatim) | |
|---|---|---|---|---|
| a | env 30, explicit `WAIT_S=""` | 40 (env-derived wins) | `WAIT_S resolved to 40s — derived from ~/agents-ensemble-demo/.env (graceful + 10s, clamped 10..600)` | ✅ |
| b | env 30, explicit `WAIT_S=-5` | 70 (malformed→default, not env) | `WAIT_S resolved to 70s — malformed WAIT_S='-5' — fell back to 70` | ✅ |
| c | env 30, explicit `WAIT_S=abc` | 70 | `WAIT_S resolved to 70s — malformed WAIT_S='abc' — fell back to 70` | ✅ |
| d | env `=""` (empty value) | 70 (digits-only fails) | `WAIT_S resolved to 70s — default (70 = 60s graceful + 10s margin)` | ✅ |
| e | env `=-100` | 70 | `WAIT_S resolved to 70s — default (70 = 60s graceful + 10s margin)` | ✅ |
| f1 | env 601 (+10=611) | **600 cap** | `WAIT_S resolved to 600s — derived from …/.env (graceful + 10s, clamped 10..600)` | ✅ |
| f2 | env 599 (+10=609) | **600 cap** | `WAIT_S resolved to 600s — derived from …/.env (graceful + 10s, clamped 10..600)` | ✅ |
| g | env 0 (+10=10) | **10 floor** | `WAIT_S resolved to 10s — derived from …/.env (graceful + 10s, clamped 10..600)` | ✅ |
| h1 | env `"30"` (double-quoted) | 40 | `WAIT_S resolved to 40s — derived from …` | ✅ |
| h2 | env `'30'` (single-quoted) | 40 | `WAIT_S resolved to 40s — derived from …` | ✅ |
| i | `export `-prefixed env 30 | 40 | `WAIT_S resolved to 40s — derived from …` | ✅ |
| j | explicit 9 / 599 / 600 / 601 (env 30 staged) | **pass-through unclamped** 9 / 599 / 600 / 601 | `resolved to 9s|599s|600s|601s — explicit WAIT_S=… (override wins)` ×4 | ✅ |
| def | env line absent | 70 | `WAIT_S resolved to 70s — default (70 = 60s graceful + 10s margin)` | ✅ |

### Genuine executions against the live demo daemon (3 real stops + restarts; ≥1 normal + ≥1 clamp required)

| # | Time (+07) | Config | Resolution observed | Stop behavior | Duration | Restart + health |
|---|---|---|---|---|---|---|
| 1 | 15:36:31 | default (no env line) | `70s — default` | single-TERM launcher only (pid 3306), daemon graceful teardown | **2.3s** | next start green (P7 red phase) |
| 2 | 15:37:21 | env 601 | `600s — derived … clamped 10..600` | single-TERM launcher (5682), graceful | **2.3s** | green t+3.66s (P7 restore) |
| 3 | 15:38:36 | env 0 | `10s — derived … clamped 10..600` (floor) | single-TERM launcher (6910), graceful | **2.3s** | green t+3.66s |

Notes:
- **No scaled-down substitution was needed**: WAIT_S is a *bound*, not a sleep — all real stops drained in ~2.3s regardless of resolved 70/600/10, so even the cap-clamped case was executed at full resolution live. Clamp math verified on-script both in DRY_RUN resolution lines and in the real 601→600 stop.
- Port report was REPORT-ONLY in every stop (`port 7979 is held by: <pid> (REPORT ONLY — ports are not a kill selector)`); selection was ownership-scoped Tier 1a/1b every time.
- Every stop left live 9797 pids identical (`30054 31150`).
- Harness caveat recorded for honesty: my first attempt at the trailing `def` case forgot to strip the `export `-prefixed line from case (i), yielding a false 40s — harness bug, re-run clean → 70. Script logic was never at fault.

---

## D. Exit-75 sandboxed live smoke (`python -m daemon`) — PASS

Isolated from demo, dev, and live: throwaway data dir, port 8377 (verified free, NOT 8079/7979/9797), unreachable PG at `127.0.0.1:54329` (nothing listens), DB name `ensemble_exit75_smoke` (never created — connection refused before any PG contact).

```
$ ENSEMBLE_DATA_DIR=$TMP PORT=8377 POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=54329 \
  POSTGRES_DB=ensemble_exit75_smoke POSTGRES_USER=… POSTGRES_PASSWORD=… \
  .venv/bin/python -m daemon
EXIT CODE: 75   wall: 1.11s   (budget: BOOT_DB_TIMEOUT_S=10 → ~10-15s expected)
--- output ---
[Config] OPENAI_REASONING_ECHO_MODELS is set but no longer read; …
PostgreSQL unreachable at boot (timeout/conn refused): (psycopg.OperationalError) connection failed:
connection to server at "127.0.0.1", port 54329 failed: could not receive data from server: Connection refused
  → exiting 75 EX_TEMPFAIL; launcher will retry with capped backoff (burst budget untouched, ADR-011)
--- post-conditions ---
port 8377 free (exit BEFORE uvicorn bind, as designed) ✓
throwaway artifacts removed (data dir + logs) ✓
live 9797: 30054 31150 (untouched) ✓
```

Confirms the pre-merge gap (§3 scope note): `_boot_db_preflight` exit-75 path now proven LIVE on a real boot, not just unit-parametrized.

---

## E. Final state

- Demo daemon: **UP** (launcher → PyInstaller bootloader 12158 → daemon 12245), `/livez` 200 (2.1ms), `/readyz` 200 full-ready, DB `ensemble_demo`, `.env` pristine (`PORT=7979`, `POSTGRES_DB=ensemble_demo`, zero test keys).
- Live: **never touched** — 9797 pids `30054 31150` at all 6 checkpoints (pre-deploy, post-deploy, 3× stops, final).
- Branch: evidence committed on `fix/auto-restart-phase1-followups`; **not pushed**.
