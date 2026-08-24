# DR-3 — P7 readiness green→red→green with RESTART-RESTORE on demo (P2.3 batch B5, drill 3 of 3)

- **Date:** 2026-08-23 · **Recorded by:** worker (B5 DR-3 dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `68b54d96` (full hash `68b54d962a2f222e58aeabeb27fe8080f70049bb`)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §4 (DR-3 procedure), executed as written with the dispatch-mandated extra still-red-after-clear assertion (see friction log FL-5) and one §0.2 authority tension (FL-1)
- **Verdict line: `DR-3 PASS: 5-row transition captured; restart-restore proven`**

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/dr3-ev/` (redaction applied at capture time, before tee). Demo port 7979 is not restricted.

---

## 1. Verdict table (pass criteria vs observed)

| # | Criterion (runbook §4 + dispatch) | Evidence excerpt | Result |
|---|---|---|---|
| C1 | Row 1 baseline: `/readyz` 200 `reasons: []` + `/livez` 200 | 18:13:24Z — readyz `{"status":"ready",...,"reasons":[],...}` / livez `{"status":"alive","uptime_seconds":1144.9,"version":"0.10.5"}` | **PASS** |
| C2 | Row 2 red: knob set → restart → `/readyz` 503 + forced reason | 18:14:33Z — HTTP 503, `"reasons":["readiness: degraded forced by ENSEMBLE_READINESS_FORCE_DEGRADED (drill)"]`, components all `true` (real readings preserved) | **PASS** |
| C3 | `/livez` STILL 200 in the red window (probe independence) | 18:14:33Z — `{"status":"alive","uptime_seconds":27.1,...}` HTTP 200; and again 18:15:21Z (uptime 74.8) HTTP 200 while readyz 503 | **PASS** |
| C4 | `[Readiness] degraded` log line while red | `data/launcher.log` (and mirrored in `data/logs/ensemble.log`): `01:14:16 - daemon.api - WARNING - [Readiness] degraded: readiness: degraded forced by ENSEMBLE_READINESS_FORCE_DEGRADED (drill) (queue_max_age=Nones)` — repeats at 10s cadence (01:14:16/26, 01:15:16/26/36 local = +0700) | **PASS** |
| C5 | **Still-red-after-clear (drill thesis): knob removed from `.env`, NO restart → probe stays 503** | Knob cleared 18:14:56Z; probed 18:15:21Z (25s = 2+ refresh ticks later): HTTP **503**, same forced reason, composite `checked_at 18:15:16` — i.e. a FRESH tick computed AFTER the clear still reports degraded. P7 note confirmed: no self-clear | **PASS** |
| C6 | Rows 4–5: restart with knob absent → `/readyz` 200 `reasons: []` + `/livez` 200, green held | 18:16:02Z — 200 `reasons:[]` (`checked_at 18:15:59`); 18:16:27Z re-probe — 200 `reasons:[]` (`checked_at 18:16:19`), livez 200 both. No `[Readiness] degraded` line after the 01:15:44 local restore boot (last is 01:15:36) | **PASS** |
| C7 | Demo `.env` restored bit-exact, knob absent | md5 `1ba30c018078a60281cba4baeacc03c4` == §0 baseline == DR-1's recorded md5; knob grep rc=1; `diff .env .env.dr3-backup` empty. Backup `.env.dr3-backup` kept as evidence per §4 "Restore of the drill" | **PASS** |
| C8 | Live pid checkpoints byte-identical start/end | pid 31150 / ppid 31130 / lstart `Sat Aug 22 10:04:07 2026` — lsof and ps captures byte-identical (§6) | **PASS** |

**All criteria met → DR-3 PASS.**

---

## 2. The 5-row timestamped transition (UTC; log lines are +0700)

| Row | Time (UTC) | State | `/readyz` | `/livez` (independence column) |
|---|---|---|---|---|
| 1 GREEN baseline | 18:13:24 | pre-drill daemon (pid 94004, uptime 1145s) | **200** `reasons:[]` (`checked_at 18:13:20`) | **200** alive |
| 2 RED (knob set @18:13:29 + restart 18:14:02) | 18:14:33 | knob-loaded daemon (pid 31919, uptime 27s) | **503** — `"readiness: degraded forced by ENSEMBLE_READINESS_FORCE_DEGRADED (drill)"`, components `{database:true, queue_freshness:true, services:true}` | **200** alive — daemon alive, merely degraded |
| 3 STILL-RED after `.env` clear, NO restart (thesis row) | 18:15:21 (clear @18:14:56; 25s / 2+ ticks elapsed) | same daemon 31919 (uptime 75s) | **503** — same forced reason, composite `checked_at 18:15:16` = computed AFTER the clear | **200** alive |
| 4 GREEN via restart (clear + restart @18:15:44) | 18:16:02 | restored daemon (pid 35105, uptime 13s) | **200** `reasons:[]` (`checked_at 18:15:59`) | **200** alive |
| 5 GREEN held (stability re-probe) | 18:16:27 | same daemon 35105 (uptime 37s) | **200** `reasons:[]` (`checked_at 18:16:19`) | **200** alive |

Transition arc: `200/reasons:[] → 503/forced-reason → still-503-after-clear → 200/reasons:[]`, `/livez` 200 at every row. Pacing 18:13:24 → 18:14:33 → 18:15:21 → 18:16:02 → 18:16:27 (precedent shape 15:36:31→15:36:49→15:37:27 respected; rows separated by ≥9s, all transitions deliberate and timestamped).

Row-3 detail (why this is proof, not a stale cache): the 503 served at 18:15:21 carries `checked_at 2026-08-23T18:15:16` — the background refresher ran at least twice between the `.env` clear (18:14:56) and the probe, and each fresh tick still forced degraded, because the knob lives in the **running process's environment** (exported by the launcher's `load_env_file "$INSTALL_DIR/.env"` at `launcher.sh:1068`, ADR-014) — the daemon never re-reads `.env`. Mechanism in code: `daemon/services/readiness.py:63-67` ("read per refresh tick … a live daemon needs a restart with the env set"), `apply_forced_degradation` appends the named reason (`readiness.py:269/280`); log line emitted by the refresher at `daemon/api.py:1096`.

---

## 3. Execution timeline (UTC; local log lines +0700)

| Time (UTC) | Step | Event |
|---|---|---|
| 18:12:21 | §0.1 | `status.sh demo`: triple `demo / ~/agents-ensemble-demo / 7979 / ensemble_demo`; journal healthy (`current → releases/v0.10.5-p2.1-e2e2`, quarantined only `…-bad2`); `daemon :7979 /livez version=0.10.5`; version smoke OK |
| 18:12:30 | §0.1 | `/livez` 200 alive 0.10.5 (uptime 1090s ⇒ daemon born 17:54:16Z = DR-1's restore launch); `/readyz` 200 `reasons:[]` |
| 18:12 | §0.2 (adapted, FL-1) | Re-inventory instead of fresh DR-0 file (dispatch write-authority). Demo family: wrapper 93986 → launcher **93988** → bootloader 94003 → **daemon 94004** (listener), all lstart `Aug 24 00:54:16` local = 17:54:16Z. **Dispatch's "~69871-family" guess corrected** — DR-1's restore relaunch replaced the DR-0 family (69871 was stopped at 17:49Z in DR-1) |
| 18:12 | §0.3 | `uv sync --extra dev` clean (resolved 120 / audited 113, no changes) |
| 18:12 | §0.4 | Live baseline: **31150/31130**, lstart `Sat Aug 22 10:04:07 2026` — matches DR-0/DR-1 record; port redacted at capture |
| 18:13:02 | §0 inv | `.env` md5 `1ba30c018078a60281cba4baeacc03c4` (== DR-1's record); knob grep rc=1 (absent); trailing byte `\n` (append-safe); `.launcher-state` pre-snapshot: `last_exit=3 crash_count=4 window_start=1787507522 last_backoff=80 notified_75=0 last_uptime=4` — **F-DR1-4 known-stale drill history, recorded NOT asserted** |
| 18:13:24 | P7.0 | **Row 1** green baseline captured |
| 18:13:29 | P7.1 | `cp .env .env.dr3-backup` (backup md5 == baseline) → `echo 'ENSEMBLE_READINESS_FORCE_DEGRADED=1' >> .env` → knob at line 83, diff exactly `82a83 > ENSEMBLE_READINESS_FORCE_DEGRADED=1`; working md5 `41a18a728c5e0321c42c0745fcf5f2b4` |
| 18:13:44 | P7.2 | Pre-stop pid assertions on {93986, 93988, 94003, 94004}: cwd=demo ×4, exe/cmdline shapes correct, lstart identity `00:54:16`, live-port resource count 0, anchored live-path open count 0 → all PASS |
| 18:13:51 | P7.2 | `stop-ensemble.sh ~/agents-ensemble-demo 7979`: TERMed 93986+93988 (launcher-owned stop), port clear, zero family residue, rc=0 |
| 18:14:02 | P7.2 | Relaunch (knob set). `/livez` 200 after ~6s (18:14:08). New family: wrapper 31883 → launcher 31885 → bootloader 31904 → **daemon 31919** (listener), lstart `01:14:02/03` local |
| 18:14:33 | P7.3 | **Row 2** red: readyz 503 + forced reason, livez 200; `[Readiness] degraded` lines at 01:14:16/26 in `data/launcher.log` AND `data/logs/ensemble.log` |
| 18:14:56 | P7.4a | Knob cleared: `cp .env.dr3-backup .env`; md5 back to baseline; knob grep rc=1; diff empty. **No restart yet** |
| 18:15:21 | P7.4a | **Row 3** still-red after 25s (2+ ticks): 503 + forced reason, `checked_at 18:15:16` (post-clear fresh tick), livez 200 — **restart-required proven** |
| 18:15:31 | P7.4b | Pre-stop pid assertions on {31883, 31885, 31904, 31919}: all PASS (same battery) |
| 18:15:40 | P7.4b | Stop: TERMed 31883+31885, port clear, rc=0 |
| 18:15:44 | P7.4b | Relaunch (knob absent). `/livez` 200 after ~6s (18:15:50). Final family: wrapper 35085 → launcher 35088 → bootloader 35104 → **daemon 35105** (listener), lstart `01:15:44/45` local |
| 18:16:02 | P7.5 | **Row 4** green via restart: readyz 200 `reasons:[]`, livez 200 |
| 18:16:27 | P7.5 | **Row 5** green held + close-out proofs: `.env` md5 == baseline, knob rc=1, diff empty; `.launcher-state` post == pre byte-identical (`crash_count=4` unchanged — drill's clean TERM stops added no crash history); last `[Readiness] degraded` line 01:15:36 local — none after restore boot |
| 18:16:47 | close | Live end-checkpoint byte-identical (§6); `git status --porcelain`: approver line (pre-existing) + dr1/dr2/dr3 RESULTS files |

Evidence files: `/tmp/dr3-ev/` — `p7-transcript.txt` (all rows, timestamped), `status-demo.txt`, `livez/readyz-baseline.txt`, `demo-listener-start.txt`, `demo-tree-start.txt`, `env-md5-before.txt`, `launcher-state-pre.txt` / `launcher-state-post.txt`, `p7.2-stop-assertions.txt` / `p7.2-stop-transcript.txt`, `p7.4-stop-assertions.txt` / `p7.4-stop-transcript.txt`, `live-lsof-start/end.txt`, `live-pid-start/end.txt`, `demo-tree-end.txt`, `uvsync.txt`, `drill-open.txt`.

---

## 4. Mechanism (why restore requires restart — the P7 contract, proven)

- **Knob ingestion:** `launcher.sh:1068` `load_env_file "$INSTALL_DIR/.env"` exports every KEY=VALUE at launch (ADR-014: launcher exports beat the frozen binary's own loader, which "only sets vars that are still unset"). The daemon's process environment therefore changes ONLY at launch.
- **Knob consumption:** the readiness refresher (`daemon/api.py` `_periodic_readiness_refresh_loop`, default 10s) evaluates `forced_degradation_active(os.environ[…])` **per tick** (`readiness.py:63-67`), so the knob bites within one tick of a boot — but the tick reads the *process environment*, never the `.env` file. Clearing the key from `.env` cannot reach the running process ⇒ still-red (Row 3) until a restart re-exports a clean environment (Row 4).
- **Honest degradation:** `apply_forced_degradation` (`readiness.py:244-286`) preserves the true component readings (`database/queue_freshness/services` all `true` in the 503 body) and appends the self-naming reason — observed verbatim in Row 2/3 bodies. One-way by construction (`forced_degraded=True` can only degrade).
- **Verbatim P7 note honored** (runbook §4 quote of `2026-08-22-ar-phase1-followups-verification.md:47`): "P7 drill on deployed daemon requires restart to restore (env knob read per refresh tick; readiness.py:50-67) — document in Phase 2 drill runbook so green-restore steps aren't assumed instant." Rows 3→4 are that sentence, observed.

---

## 5. Demo environment proofs

- **`.env` lifecycle:** baseline md5 `1ba30c018078a60281cba4baeacc03c4` (82 lines, knob absent, trailing newline) → knob appended (line 83, md5 `41a18a728c5e0321c42c0745fcf5f2b4`) → restored via backup: md5 back to `1ba30c01…`, knob grep rc=1, `diff` vs backup empty. Backup `.env.dr3-backup` kept in `~/agents-ensemble-demo/` per §4 "Restore of the drill" ("backup kept as evidence").
- **`.launcher-state`:** pre and post snapshots byte-identical (`last_exit=3 crash_count=4 window_start=1787507522 last_backoff=80 notified_75=0 last_uptime=4`). Per **F-DR1-4** these counters are known-stale background from DR-1's window (ages out per launcher window logic) — recorded, NOT asserted on. Drill-relevant fact: the two clean launcher-owned TERM stops left the crash counters untouched (no new history introduced by DR-3).
- **Boot health:** both restarts reached `/livez` 200 in ~6s (deploy-gate budget ≤60s); engine stayed on the real demo PG throughout (no DB mutation of any kind in this drill).

---

## 6. Live pid checkpoint table (read-only; zero live contact)

| Checkpoint | Moment | pid/ppid | lstart | Byte-diff vs start |
|---|---|---|---|---|
| start (§0.4) | 18:12Z, pre-drill | 31150 / 31130 | Sat Aug 22 10:04:07 2026 | — (baseline; matches DR-0/DR-1 records) |
| end (final) | 18:16:47Z, drill close | 31150 / 31130 | Sat Aug 22 10:04:07 2026 | identical ✓ (lsof capture AND ps capture both byte-identical) |

Listener resolve method (§0.4 as written): port read from the live install's own `.env` at runtime into a shell variable; `lsof -nP -iTCP -sTCP:LISTEN` filtered by that port; lsof lines sed-redacted to `<live-port>` BEFORE tee; only `ps` lines (cmdline carries no port literal) captured verbatim. **No signal, no HTTP, no write to live at any point.**

---

## 7. Constraint compliance

- **Live:** READ-ONLY throughout — `ps`, `lsof`, one `.env` PORT read. Zero signals, zero HTTP, zero writes. Invariance proven by §6 (byte-identical start/end, both capture formats).
- **Demo:** mutations exactly the runbook DR-3 set — `.env` backup + one-line knob append (P7.1), knob clear via backup restore (P7.4), two launcher-owned restarts (stop-ensemble.sh + launcher relaunch, P7.2/P7.4). Nothing else: no pipeline/journal op (`status.sh` is read-only), no DB mutation, no release change (`current` untouched at `v0.10.5-p2.1-e2e2`).
- **PID discipline (DR-0 rules):** every stop preceded by the full assertion battery on the whole family {wrapper, launcher.sh, bootloader, daemon}: exe/cwd under `~/agents-ensemble-demo`, cmdline shape, lstart identity vs the launch this drill recorded, live-port resource count 0, trailing-slash-anchored live-path open count 0 (`~/agents-ensemble/` grep excluding `-demo`, count-only output so no port/path literal could leak). 2 stop batteries, 8/8 pid assertions PASS; stops executed only via `stop-ensemble.sh` (never a bare-pid kill).
- **Repo:** single artifact written (this file). `git status --porcelain` at close: ` M .agents/approver/active.md` (pre-existing per DR-0 record, untouched) + 3 untracked RESULTS files (dr1, dr2, this).
- **Port literals:** zero live-port literals in this file and all `/tmp/dr3-ev/` evidence (redaction at capture time; pid-assertion greps emitted counts only).
- **String discipline (§0.6):** live-path greps anchored `~/agents-ensemble/` with demo exclusion; the unanchored form was never used.

---

## 8. Operator friction log (T1-closure deliverable)

| runbook §/step | doc says | observed | classification |
|---|---|---|---|
| §0.1/0.3/0.4/0.5/0.6 | Prereq checklist: status triple, probes, dev env, live baseline, triple assertion, string discipline | All executed as written, all PASS (triple exact, probes green, uv sync clean, baseline byte-matches DR-0/DR-1) | clean |
| §0.2 | "If demo/live state has changed since the last DR-0 (… daemon restarted …), re-run the DR-0 shape (S1–S5) and record a fresh dated file before drilling" | Demo daemon HAD been restarted (DR-1's restore, 17:54:16Z) — but the dispatch's write authority is "Repo: write ONLY your RESULTS file", so a fresh dated DR-0 file is out of scope for this worker. Resolved: full S1–S5-shaped re-inventory (triple, probes, family pids + lstart, launcher-state, live baseline) captured inside §3 of this file. The re-inventory DID catch a real drift: the launcher family is NOT the dispatch-assumed ~69871 set — it is 93988-family from DR-1's relaunch | **TENSION** (runbook instruction vs dispatch authority; dispatcher should decide whether a standalone fresh DR-0 record file is wanted) |
| §4 P7.0 | Green baseline with timestamp + tee | Executed as written; body/code both captured | clean |
| §4 P7.1 | `cp .env .env.dr3-backup` + `echo '…=1' >> .env` | Executed as written and clean — BUT only because the demo `.env` happens to end with a newline (verified `tail -c 1` = `\n` before appending). The runbook never says to check; on a no-trailing-newline `.env` the echo would corrupt the last KEY=VALUE line and the boot would silently lose a config key | **MISSING** (latent hazard — add a trailing-newline pre-check to P7.1) |
| §4 P7.2 | stop-ensemble.sh + nohup relaunch | Executed as written; clean TERM stop (~2s drain), `/livez` 200 in ~6s. Family shape = F-DR1-5's documented `/bin/sh -c` wrapper parent — expected, TERMed by the stop script | clean |
| §4 P7.3 | `grep '\[Readiness\] degraded' data/launcher.log \| tail -1` | Works as written — line present (`daemon.api - WARNING - [Readiness] degraded: …`), repeated at the 10s refresh cadence. Informational: the same lines are mirrored in `data/logs/ensemble.log` (both sinks receive daemon logging) — either file satisfies the step | clean (mirror noted) |
| §4 P7.4 | "clear the knob AND restart" as one bundled step; expected-outputs table has NO row between clear and restart | The drill's own thesis (verbatim P7 note: restore is NOT instant) is never OBSERVED by the runbook's procedure — bundling clear+restart skips straight to green without proving the still-red intermediate state. Dispatch mandated the missing assertion (executed: Row 3). Recommend the runbook insert a probe between the `cp` and the stop, else the pass criterion "green restored **by restart**" is asserted, not demonstrated | **MISSING** (thesis row absent from procedure and expected-output table) |
| §4 P7.5 | `diff .env .env.dr3-backup` # empty = knob gone | Tautological immediately after P7.4's `cp` from that very backup — cannot fail. The independent proof is md5-vs-§0-baseline + knob grep (both executed here, both clean). Recommend the runbook use the §0 baseline md5 as the restore oracle | **WEAK** (as-written check is vacuous; supplement performed) |
| §4 "Restore of the drill" | Backup kept as evidence; `/readyz` 200 asserted; "Do not report green on a knob-cleared-but-not-restarted daemon" | Followed exactly (backup retained; green asserted post-restart; the warned-about intermediate state was deliberately entered, observed red, and exited via restart per the note) | clean |

**Friction summary:** §0 and the P7.0–P7.3 mechanism are accurate and clean (the DR-1-era knob/dead-end problems do not recur — this knob is real and consumed). Two genuine gaps: (1) P7.4 never observes the still-red state that the drill exists to prove (dispatch closed it this run; runbook should too); (2) P7.1's append is newline-fragile. §0.2's fresh-DR-0-file instruction collides with per-drill dispatch write-scoping and needs a dispatcher ruling.

---

## 9. Verdict

`DR-3 PASS: 5-row transition captured; restart-restore proven`

Environment left healthy: demo daemon v0.10.5 green on 7979 (`/readyz` 200 `reasons:[]` held, `/livez` 200; family 35085→35088→35104→35105), `.env` bit-exact (`1ba30c018078a60281cba4baeacc03c4`), knob absent, `.env.dr3-backup` retained as evidence per runbook, `.launcher-state` unchanged by the drill (known-stale `crash_count=4` is F-DR1-4 background); live byte-identical to baseline at close. No findings requiring triage — zero anomalies, zero contradictions (the thesis row behaved exactly as the P7 note predicts).
