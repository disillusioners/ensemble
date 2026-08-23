# DR-1 RE-RUN — tempfail→respawn full cycle on demo (P2.3, carry-over #1 closure)

- **Date:** 2026-08-23 · **Recorded by:** worker (DR-1 re-run dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `91ace51c` (`91ace51c fix(p2.3): frozen entry owns boot-DB preflight explicitly (F-DR1-1 hardening)`)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §2 (DR-1 procedure) at HEAD `91ace51c` — the B5.5/B5.6-corrected D1.2 (`.env` POSTGRES_PORT part edit); executed as written (one disclosed intra-step ordering note, friction log FL-4)
- **Prior attempt:** `2026-08-23-p2-3-dr1-tempfail-cycle.md` @ `68b54d96` — FAIL via dead knobs (F-DR1-1/F-DR1-3), crash_count 1→4. This re-run executes the corrected induction against the SAME deployed binary (v0.10.5-p2.1-e2e2 — already reaches the preflight via `main()` delegation; no redeploy needed, per user ruling in dispatch)
- **Verdict line: `DR-1 PASS: full loop observed end-to-end; carry-over #1 closed`**

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/dr1r2-ev/` (redaction applied at capture time, before tee). Demo port 7979 and the drill's closed probe port 39421 are not restricted.

---

## 1. Inline DR-0 — S1–S5-shaped re-inventory (FL-1 ruling; no repo tests this drill)

| Item | Observed (18:54–18:55Z) | Match vs expectation |
|---|---|---|
| `status.sh demo` triple + journal | `target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo`; journal healthy, `current → releases/v0.10.5-p2.1-e2e2`, no quarantine on current, `version smoke: OK` | ✓ §0.1 |
| Demo probes | `/livez` 200 `{"status":"alive","uptime_seconds":2331.6,"version":"0.10.5"}`; `/readyz` 200 `reasons:[]` (18:54:41Z) — uptime ⇒ daemon born 18:15:50Z = DR-3's restore boot | ✓ green baseline |
| **Demo family (re-derived, not trusted)** | wrapper **35085** (`/bin/sh -c`, DR-3's own nohup wrapper) → launcher **35088** (`/bin/bash ./launcher.sh`) → bootloader **35104** → daemon **35105** (listener 127.0.0.1:7979); all lstart `Mon Aug 24 01:15:44/45 2026` local (+0700) = 18:15:44Z; all cwd = demo install | ✓ exactly DR-3's final lineage (~35088 family per dispatch) |
| **Live pid baseline** | listener pid **31150** / ppid **31130**, lstart `Sat Aug 22 10:04:07 2026`, `./ensemble-prod` — resolved read-only (port from live install's own `.env`; lsof lines sed-redacted to `<live-port>` BEFORE capture) | ✓ all-day baseline (31150/31130) |
| D1.1 part record | `POSTGRES_HOST=localhost` / **`POSTGRES_PORT=5432` (L35)** / `POSTGRES_DB=ensemble_demo` / `POSTGRES_USER=ensemble` / `POSTGRES_PASSWORD=` (L34–38); md5 `1ba30c018078a60281cba4baeacc03c4` (== DR-1/DR-3 records) | ✓ |
| `.launcher-state` pre-snapshot | `last_exit=3 crash_count=4 window_start=1787507522 last_backoff=80 notified_75=0 last_uptime=4` — **re-run STARTING baseline: `crash_count=4`** (F-DR1-4 historical residue from the original DR-1's crash-track attempts; the exemption assertion is invariance FROM THIS VALUE, not absolute zero) | ✓ recorded, not asserted |
| `uv sync --extra dev` | **Skipped by dispatch authority** (no repo tests run this drill; §0.3 not applicable) | per dispatch |

---

## 2. Verdict table (pass criteria vs observed)

| # | Criterion (runbook §2 + dispatch) | Evidence excerpt | Result |
|---|---|---|---|
| C1 | ≥2 exit-75→capped-backoff→respawn cycles observed end-to-end | **5 cycles**: `child exited 75 (boot tempfail, uptime 1–2s) — retrying in {5,10,20,40,60}s (75-track, cap 60s)` at 01:55:48 / 01:55:54 / 01:56:06 / 01:56:27 / 01:57:09 local (§3 transcript) | **PASS** |
| C2 | Backoff timestamps on the TEMPFAIL schedule (capped 5s→60s), NOT the 10→20→40→80 crash schedule | Observed **5→10→20→40→60** (40×2=80 clamped to cap 60); grep `child exited [0-9]+` minus `exited 75` over the window = **empty** — zero crash-track contamination | **PASS** |
| C3 | Burst-budget EXEMPT — `.launcher-state` `crash_count` byte-identical to re-run baseline | `crash_count=4` in pre (18:55Z), cycle1 (18:56:07Z), cycle2 (18:57:28Z), and final (18:58:51Z) — **4 at every sample**, across 5 exit-75s; `window_start=1787507522` also unchanged (no burst-window activity); `last_exit` 3→75, `last_backoff` 80→60, `notified_75` 0→1 (informational only, runbook-allowed) | **PASS** |
| C4 | Zero abort marker | `grep -iE "burst\|abort"` over the drill window: 6 hits, **all** the benign `burst budget untouched, ADR-011` phrase inside exit-75 lines; `grep -icE "abort\|BURST ABORT\|burst-abort"` = **0** | **PASS** |
| C5 | Post-restore `/livez` 200 + `/readyz` 200 `reasons: []` | `/livez` 200 after **~6s** (18:58:36Z, ≤60s budget); `/readyz` 200 `{"database":true,"queue_freshness":true,"services":true},"reasons":[]` (18:58:41Z); engine line `Creating PostgreSQL engine: localhost:5432/ensemble_demo` (01:58:34 local) | **PASS** |
| C6 | `.env` restored bit-exact (md5 before==after), no drill knob left | before `1ba30c018078a60281cba4baeacc03c4` → working (part-edit) `b0c5d208f1304349b28e46cc0f889b26` (single-line diff: L35 `POSTGRES_PORT=5432`→`39421`) → restored `1ba30c018078a60281cba4baeacc03c4`; `diff` vs backup EMPTY; runbook D1.4 `diff env-before.txt <parts>` EMPTY | **PASS** |
| C7 | Live pid checkpoints byte-identical start/end | pid 31150 / ppid 31130 / lstart `Sat Aug 22 10:04:07 2026` — start (18:54Z) and end (18:59Z) `ps`+`lsof` captures **byte-identical** (§6) | **PASS** |

**All criteria met → DR-1 PASS. The one-line pass criterion of §2 (≥2 cycles, crash_count unchanged, probes green after PG restore) is satisfied with margin (5 cycles).**

---

## 3. Exit-75 / backoff timestamp transcript (launcher log drill window, local +0700; launch 18:55:47Z)

```text
2026-08-24T01:55:47+0700 launcher[3496]: ensemble launcher starting (INSTALL_DIR=/Users/nguyenminhkha/agents-ensemble-demo)
2026-08-24T01:55:47+0700 launcher[3496]: starting: /Users/nguyenminhkha/agents-ensemble-demo/current/ensemble-prod
PostgreSQL unreachable at boot (timeout/conn refused): (psycopg.OperationalError) connection failed:
  connection to server at "127.0.0.1", port 39421 failed: could not receive data from server: Connection refused
  → exiting 75 EX_TEMPFAIL; launcher will retry with capped backoff (burst budget untouched, ADR-011)
2026-08-24T01:55:48+0700 launcher[3496]: NOTIFY[tempfail-75]: boot-time temporary failure (PG unreachable?) — retrying with capped backoff (cap 60s), burst budget untouched (ADR-011)
2026-08-24T01:55:48+0700 launcher[3496]: child exited 75 (boot tempfail, uptime 1s) — retrying in 5s (75-track, cap 60s)     ← cycle 1
2026-08-24T01:55:53+0700 launcher[3496]: starting: …/current/ensemble-prod
2026-08-24T01:55:54+0700 launcher[3496]: child exited 75 (boot tempfail, uptime 1s) — retrying in 10s (75-track, cap 60s)    ← cycle 2
2026-08-24T01:56:04+0700 launcher[3496]: starting: …/current/ensemble-prod
2026-08-24T01:56:06+0700 launcher[3496]: child exited 75 (boot tempfail, uptime 2s) — retrying in 20s (75-track, cap 60s)    ← cycle 3
2026-08-24T01:56:26+0700 launcher[3496]: starting: …/current/ensemble-prod
2026-08-24T01:56:27+0700 launcher[3496]: child exited 75 (boot tempfail, uptime 1s) — retrying in 40s (75-track, cap 60s)    ← cycle 4
2026-08-24T01:57:07+0700 launcher[3496]: starting: …/current/ensemble-prod
2026-08-24T01:57:09+0700 launcher[3496]: child exited 75 (boot tempfail, uptime 2s) — retrying in 60s (75-track, cap 60s)    ← cycle 5 (cap reached: 40×2=80 clamped to 60)
```

Backoff arithmetic: gaps between cycle N's exit and cycle N+1's start = 5s, 10s, 20s, 40s — each exactly the announced backoff; schedule `5,10,20,40,60` = `TEMPFAIL_BACKOFF_START_S=5` doubling, `TEMPFAIL_BACKOFF_CAP_S=60` clamping (ADR-011 ≤60s). Distinct from the crash track (10→20→40→80→…→300) observed in the original DR-1.

Every failing boot logged the preflight message `PostgreSQL unreachable at boot … port 39421 … → exiting 75 EX_TEMPFAIL` (5/5); **zero `Creating PostgreSQL engine` lines occurred during the tempfail window** — the preflight exits before engine construction (§7 mechanism).

---

## 4. `.launcher-state` invariance (crash_count proof)

| Sample | Time (UTC) | `last_exit` | `crash_count` | `window_start` | `last_backoff` | `notified_75` | `last_uptime` |
|---|---|---|---|---|---|---|---|
| pre (re-run baseline) | 18:55 | 3 | **4** | 1787507522 | 80 | 0 | 4 |
| cycle1 | 18:56:07 (after 3 exit-75s) | 75 | **4** | 1787507522 | 20 | 1 | 2 |
| cycle2 (runbook sleep-70 capture) | 18:57:28 (after 5 exit-75s) | 75 | **4** | 1787507522 | 60 | 1 | 2 |
| final (post-restore, daemon running) | 18:58:51 | 75 | **4** | 1787507522 | 60 | 1 | 2 |

`crash_count` = **4 at every sample** — five exit-75 boots and two clean launcher TERMs left the burst budget byte-untouched. Deltas confined to the runbook-allowed informational fields (`last_exit`→75, `last_backoff`→60, `notified_75`→1). F-DR1-4 confirmed still-true (history retained after healthy restore; launcher-owned state, ages out per its own window logic).

---

## 5. `.env` md5 proof + demo lifecycle

| Moment | md5 | Note |
|---|---|---|
| DR-0 baseline (D1.1) | `1ba30c018078a60281cba4baeacc03c4` | == DR-1 original / DR-3 records |
| backup `env-drill-backup` (pre-edit copy) | `1ba30c018078a60281cba4baeacc03c4` | backup == baseline |
| working (D1.2 part edit active) | `b0c5d208f1304349b28e46cc0f889b26` | `diff` vs backup = exactly one line: `35c35 < POSTGRES_PORT=5432 --- > POSTGRES_PORT=39421` |
| restored (D1.4, BEFORE relaunch) | `1ba30c018078a60281cba4baeacc03c4` | `diff` vs backup EMPTY; parts `diff` vs `env-before.txt` EMPTY; `POSTGRES_PORT=5432` at L35 |
| final (close-out) | `1ba30c018078a60281cba4baeacc03c4` | bit-exact, no drill residue |

Closed-port candidate selection (runbook: "nc refused AND lsof unlisted; never a real service port"): **39421** — `nc -z 127.0.0.1 39421` rc=1 (refused), `lsof -nP -iTCP:39421` 0 lines (unlisted). Verified before use; the port never belonged to a service (all 5 boots' refusals confirm).

---

## 6. Live pid checkpoint table (read-only; zero live contact)

| Checkpoint | Moment | pid/ppid | lstart | Byte-diff vs start |
|---|---|---|---|---|
| start (§0.4) | 18:54Z, pre-drill | 31150 / 31130 | Sat Aug 22 10:04:07 2026 | — (baseline; matches all-day record) |
| end (final) | 18:59Z, post-restore | 31150 / 31130 | same | **identical ✓** (ps AND redacted-lsof captures both byte-identical) |

Resolve method per §0.4: port read from the live install's own `.env`; `lsof` LISTEN filtered by that port string; only `ps` lines tee'd (cmdline carries no port literal); lsof lines sed-redacted to `<live-port>` before capture. **No signal, no HTTP, no write to live at any point.**

---

## 7. Mechanism note (why the part override is the end-to-end 75-track)

- **Single moving part:** the D1.2 `.env` edit changes ONLY `POSTGRES_PORT` (a repositories-chain part). Both consumers of PG config in the boot path read the parts chain — the **boot preflight** (`_boot_db_preflight`, SELECT 1 under `BOOT_DB_TIMEOUT_S=10`) and the **repositories engine** (`daemon/repositories/factory.py` parts-only). One edit, both move together: the deployed binary's boot hit the drill socket 5/5 times in the preflight, `exit 75 EX_TEMPFAIL`, **before any engine construction** (zero engine lines in the window) — the original DR-1's split-brain (F-DR1-2: engine on real DB, checkpointer on drill socket via `POSTGRES_URL`) is structurally impossible with the part override.
- **Why ambient knobs are inert:** the launcher re-exports `INSTALL_DIR/.env` AFTER inheriting ambient env (ADR-014 `load_env_file` — unconditional export), so ambient `POSTGRES_*` parts are clobbered back; only the `.env` part edit survives into the child's environment. This is the runbook's B5.6 knob-rider rationale, now observed live.
- **Frozen-binary entry status (forward-reference):** the deployed demo binary `v0.10.5-p2.1-e2e2` reaches the preflight **via `main()` delegation** — this drill is REAL 75-track proof on the CURRENT binary (per user ruling; no redeploy needed). The NEW explicit preflight call site in the frozen entry introduced by HEAD `91ace51c` (`fix(p2.3): frozen entry owns boot-DB preflight explicitly (F-DR1-1 hardening)`) is one layer earlier than what this binary exercises; its frozen-binary proof lands with the **B6/S1 pipeline redeploy** — tracked there, not here.
- **Supervisor semantics proven end-to-end:** exit map 75→tempfail track (NOT crash track); backoff 5s→60s capped; burst budget untouched (ADR-011); `notified_75` one-shot informational; clean TERM of a mid-backoff launcher leaves no child residue (child was between restarts — stop transcript TERMed wrapper 3494 + launcher 3496, port free, zero residue).

---

## 8. Execution timeline (UTC; local log lines +0700)

| Time (UTC) | Step | Event |
|---|---|---|
| 18:54:41 | §0.1 | `status.sh demo`: triple + journal healthy + version smoke OK; `/livez` 200 (uptime 2331s ⇒ DR-3's restore daemon), `/readyz` 200 `reasons:[]` |
| 18:54–18:55 | §0 inv | Live baseline 31150/31130 (lstart verified); demo family re-derived 35085/35088/35104/35105 (lstart 01:15:44/45 local = DR-3 restore); D1.1 parts + md5 `1ba30c01…`; `.launcher-state` pre `crash_count=4` (re-run baseline); `uv sync` skipped per dispatch |
| 18:55:2x | D1.2 | Assert battery on family ×4: cwd=demo, 0 live-port resources, 0 anchored live-path opens — PASS; closed-port 39421 verified (nc refused + lsof unlisted) |
| 18:55:27 | D1.2 | Clean stop: `stop-ensemble.sh` TERMed 35085+35088 (launcher-owned), port clear, zero residue, rc=0 |
| 18:55:3x | D1.2 | Backup md5 == baseline; part edit L35 `POSTGRES_PORT=5432`→`39421` (working md5 `b0c5d208…`, single-line diff) |
| 18:55:47 | D1.2 | Drill launch (wrapper 3494 → launcher 3496, lstart 01:55:47 local) |
| 18:55:48 | D1.3 | **Cycle 1**: exit 75, backoff **5s** |
| 18:55:54 | D1.3 | **Cycle 2**: exit 75, backoff **10s** |
| 18:56:06 | D1.3 | **Cycle 3**: exit 75, backoff **20s**; cycle1 state capture (18:56:07): `last_exit=75 crash_count=4 last_backoff=20 notified_75=1` |
| 18:56:27 | D1.3 | **Cycle 4**: exit 75, backoff **40s** |
| 18:57:09 | D1.3 | **Cycle 5**: exit 75, backoff **60s** (cap); cycle2 state capture (18:57:28, runbook sleep-70): `last_backoff=60`, `crash_count=4` |
| 18:58:09 | D1.4 | Assert battery on wrapper 3494 + launcher 3496 (cwd=demo, lstart identity 01:55:47, 0/0 live resources; child mid-backoff — none alive); `stop-ensemble.sh` TERMed both, port free, no residue, rc=0 |
| 18:58:1x | D1.4 | `.env` restored from backup **BEFORE relaunch**: md5 back to `1ba30c01…`, diff vs backup EMPTY, parts diff EMPTY |
| 18:58:30 | D1.4 | Restore relaunch (wrapper 8394 → launcher 8396 → daemon 8416, lstart 01:58:30 local) |
| 18:58:36 | D1.4 | `/livez` 200 after ~6s (≤60s budget) |
| 18:58:41 | D1.4 | `/readyz` 200 `reasons:[]`; engine `localhost:5432/ensemble_demo` (01:58:34) — real DB, no residue knob |
| 18:58:51 | close | `.launcher-state` final `crash_count=4` unchanged; abort-marker scan 0 true hits; 6 "burst" grep hits all the benign ADR-011 phrase |
| 18:59 | close | Live end-checkpoint byte-identical (§6); final `.env` md5 `1ba30c01…`; `git status --porcelain` = pre-existing approver line only (+ this file) |

Evidence files: `/tmp/dr1r2-ev/` — status-demo, livez/readyz baseline+restored, env-before / env-drill-backup / env-drill-working / env-md5-before, launcher-state pre/cycle1/cycle2/final, d1.2 + d1.4 stop assertions/transcripts, d1.3 loop lines (early + full), abort-check, log-offset, demo family start, live pid/lsof start+end (redacted), d1.2-launch, d1.4-restore-launch.

---

## 9. Constraint compliance

- **Live:** READ-ONLY throughout — `ps`, `lsof`, `.env` PORT read (for the redacted-at-capture resolve). Zero signals, zero HTTP, zero writes. Invariance proven by §6 (byte-identical start/end).
- **Demo:** mutations exactly the runbook DR-1 set — one clean stop, one `.env` backup, one drill-scoped single-line `POSTGRES_PORT` part edit, one drill launch (5 exit-75 boots, launcher-supervised), one assert-then-TERM stop of the looping launcher, one restore launch. `.env` bit-exact at close (§5); no pipeline/journal op ran (`status.sh` is read-only; journal untouched); closed probe port 39421 verified closed pre-use, never a real service.
- **PID discipline:** every stop preceded by the full assertion battery — cwd = demo install, exe/cmdline shapes (incl. the relative `./launcher.sh` Tier-1b form), lstart identity vs MY recorded starts (01:15:44/45 family; 01:55:47 drill family), live-port resource count 0, trailing-slash-anchored live-path open count 0. 2 stops, 2 stop-script-issued TERM sets (35085+35088; 3494+3496), all TERM-confirmed in transcripts BEFORE any relaunch. No bare-pid kill at any point.
- **Repo:** single artifact written (this file). `git status --porcelain` at close: ` M .agents/approver/active.md` (pre-existing, untouched) + this new file.
- **Port literals:** none for live in this file or any evidence file (redaction at capture time). Demo 7979 / probe 39421 only.

---

## 10. Operator friction log (T1 signal #2 — corrected runbook's first real execution)

| runbook §/step | doc says | observed | classification |
|---|---|---|---|
| §2 D1.1 | record `POSTGRES_*` parts from demo `.env` | As corrected: parts exist at L34–38, grep recorded them, reconstruction trivial. First-try correct (was WRONG pre-B5.5) | **clean** (fix verified) |
| §2 D1.2 knob | `.env` `POSTGRES_PORT` part edit (backup → sed → launch); ambient overrides inert | Exactly as documented: single-line sed edit; induction fired on the FIRST boot (preflight refused 39421 → exit 75); ADR-014 clobber rationale held (no ambient vars set at all this time — the runbook's explanation is why the old knob class failed) | **clean** (fix verified) |
| §2 D1.2 closed-port | "nc refused AND lsof unlisted" double-check | Now explicit (was AMBIGUOUS pre-B5.5); `nc -z` rc=1 + `lsof` 0-line on 39421 — one command pair, unambiguous | **clean** (fix verified) |
| §2 D1.2 expected outputs | exit 75 + capped 5s→60s backoff + `crash_count` unchanged + `notified_75` may flip | All four matched verbatim (5 cycles, 5→10→20→40→60, crash_count 4 throughout, notified_75→1) | **clean** (fix verified) |
| §2 D1.2 launch shape | `(cd … && nohup ./launcher.sh … &)` leaves a `/bin/sh -c` wrapper (F-DR1-5 note in runbook) | Wrapper present as documented (3494); stop script TERMed both tiers correctly (observed 2×) | **clean** (documented) |
| §2 D1.2 step order | comment block lists stop-ensemble.sh FIRST, then closed-port pick | I ran the port double-check in the same block as the pre-stop assert battery (before the stop) — order swap within D1.2; functionally equivalent (the socket's closed state is independent of the demo stop; nothing in the drill binds it). Disclosed for fidelity | **OK-but-noted** (cosmetic ordering) |
| §2 D1.3 | `tail -f` the launcher log | Same as original DR-1: interactive-only shape; non-interactive executor used offset-anchored `tail -n +N` + timed captures (equivalent evidence). Carried friction, still unfixed, low impact | **OK-but-confusing** (carried) |
| §2 D1.4 stop step | stop the tempfail-looping launcher; cmdline exe-path / cwd == demo / launcher.sh shape tiers; restart only after TERM confirmed | The drill launcher's cmdline is RELATIVE (`/bin/bash ./launcher.sh`) — a `pgrep -f agents-ensemble-demo` family re-derive MISSES it (only the log pid or cwd identifies it); stop-ensemble.sh's Tier-1b cwd tier caught it, assert battery used cwd+lstart. Operator note: re-derive family pids by cwd, not by path-grep | **MINOR gap** (assert guidance could name the relative-cmdline case; the stop tool handles it) |
| §2 D1.4 checks | `curl /livez; curl /readyz`; `diff` env before/after | Executed as written (200/200 `reasons:[]`; parts diff empty). `/bin/sh` here rejects `<(…)` process substitution in the runbook's diff line — needed a temp file; trivial | **OK-but-noted** (portability nit) |
| §0 checklist | triple assertion, string discipline, DR-0 scope (FL-1 inline), pid baseline | All executed as written; inline re-inventory per FL-1 (no fresh DR-0 file mint — correct: this drill is within the DR-3 batch, demo state unchanged since 18:15:44Z restore) | **clean** |

**Friction summary:** the B5.5/B5.6 corrections are all verified on first real execution — induction, knob, port guidance, and expected outputs each landed exactly as written (5 clean/fix-verified rows). Residual friction is minor and carried: the interactive `tail -f` shape (D1.3), one cosmetic step-ordering ambiguity (D1.2), and a small assert-guidance gap for the relative-cmdline launcher (D1.4) — none blocked or distorted the drill.

---

## 11. Verdict

`DR-1 PASS: full loop observed end-to-end; carry-over #1 closed`

5 exit-75→capped-backoff(5→10→20→40→60s)→respawn cycles observed on the deployed demo binary (v0.10.5-p2.1-e2e2, via `main()`-delegation preflight); `crash_count` 4→4 (burst-budget exempt); zero abort markers; post-restore `/livez` 200 (6s) + `/readyz` 200 `reasons:[]`; `.env` bit-exact; live byte-identical at every checkpoint. Environment left healthy: demo serving v0.10.5 on 7979 (family 8394/8396/8416), no drill residue. F-DR1-1's original blocker is behaviorally closed on the current binary; the explicit frozen-entry call site from `91ace51c` awaits its frozen-binary proof at B6/S1's pipeline redeploy (forward-reference, §7).
