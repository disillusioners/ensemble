# P2.3 B8a — Clean cycles #2 + #3 (same-version b7cyc1, ari-driven) — ledger to 3 consecutive; completed-ledger checker matrix — the single evidence commit

- **Date:** 2026-08-23 · **Recorded by:** worker (B8a dispatch)
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `3a9c9c10` (B7 evidence commit; the b7cyc1 tag `v0.10.8-p2.3-b7cyc1` remains at `cc3a7d5a` — one commit behind HEAD, docs-only delta: exactly 1 RESULTS file, verified `git diff --stat cc3a7d5a 3a9c9c10`)
- **Runbook:** `docs/runbooks/upgrade-drills.md` §0 (prereqs) + §6 (DR-5 step 2 shape) + §7 (ledger) + §9 (F2 hard block); §4.1 canonical clauses per `test-strategy.md`; ledger machine source: `scripts/upgrade/ledger_check.py`
- **Verdict lines:**
  - `CYCLE 2 PASS: ari-driven same-version re-promote of v0.10.8-p2.3-b7cyc1 — job d2802d0a (JAFP, self-executed dry-run→armed pair) → "UPGRADE ARMED — run_id=r-20260823-222757-9aea" (journal pending_op armed_by_instance=fd8be3c2 = ari's instance) → daemonized promote pid 49198 → SINGLE-TERM stop (launcher[41913]+wrapper 41911, 22:28:11Z, zero kill-9) → flip → livez ~2s / readyz 22:28:18.49 / version verify OK → 300s soak green → journal commit 22:33:21Z (arm→commit ≈324s ≪ 600s); ari received STRUCTURED TERMINAL state (outcome=committed) via fresh post-restart turn (job 21734cb3); §4.1 c1–c7 all PASS — consecutive clean 2`
  - `CYCLE 3 PASS: ari-driven same-version re-promote of v0.10.8-p2.3-b7cyc1 — job 98e4749b → "UPGRADE ARMED — run_id=r-20260823-223728-6c04" (armed_by_instance=2daca0e2 = ari's instance) → daemonized promote pid 71135 → SINGLE-TERM stop (launcher[64680] 22:37:39Z, zero kill-9) → flip → boot-sweep leaves fresh txn alone (3s ≤ 600s) → livez/readyz green → 300s soak green → journal commit 22:42:47Z (arm→commit ≈319s ≪ 600s); ari received STRUCTURED TERMINAL state (outcome=committed) via fresh post-restart turn (job cb44e8e4); §4.1 c1–c7 all PASS — consecutive clean 3 — LEDGER COMPLETE`
  - `LEDGER MATRIX: ELIGIBLE-pending-F2 (f2-closed) / BLOCKED (f2-open) — §9 proven on completed ledger — 3 consecutive CLEAN @ v0.10.8-p2.3-b7cyc1 (txns 22:06:58Z, 22:33:21Z, 22:42:47Z); cycle 1 b65 SUPERSEDED (staleness reset at the version change)`

**Redaction rule:** the live port is rendered `<live-port>` throughout — zero live-port literals in this file or any evidence file under `/tmp/b8a-ev/` (lsof output resolved from the live install's own `.env`, port value never written to a captured file; grep-gated pre-commit). Demo port 7979 is not restricted.

---

## 1. Inline DR-0 (FL-1 — fresh dated re-inventory at the batch boundary; 22:24:35–22:24:54Z)

| Item | Observed | Match vs B7 handoff (F-B7-4) |
|---|---|---|
| Live baseline (READ-ONLY) | listener pid **31150** / ppid **31130**, lstart `Sat Aug 22 10:04:07 2026`, `./ensemble-prod` — resolved from the live install's own `.env` port; redacted before capture (`/tmp/b8a-ev/live-lsof-redacted.txt`, `live-pid-start.txt`) | ✓ all-day baseline, byte-identical |
| `status.sh demo` triple | `target=demo dir=/Users/nguyenminhkha/agents-ensemble-demo port=7979 db=ensemble_demo` | ✓ |
| Demo probes | `/livez` 200 `{"status":"alive",…,"version":"0.10.5"}` uptime 1364s ⇒ born ≈22:01:56Z (continuous since the cycle-#1 promote boot); `/readyz` 200 `reasons:[]` | ✓ green |
| Demo family (re-derived + lstart) | **99843** (launcher) → **99887** (bootloader) → **99890** (daemon, listener :7979), lstart `Mon Aug 24 05:01:53 2026` local (+0700) = 22:01:53Z | ✓ dispatch expectation "family 99843→99890" confirmed exactly |
| Journal | `current=v0.10.8-p2.3-b7cyc1, previous=v0.10.7-p2.3-b65, in_flight=null`, `24h:0`, cooldown null, quarantined `[]`, history **9 events** (md5 `8ebbb6ae81f65750bf1348ea85721a5e`) — tail: commit 22:06:58Z, sweep 22:10:13Z, refusal ×2 22:10:1xZ, refusal ×2 22:11:55Z | ✓ the B7 post-drill state verbatim |
| Demo `.env` | md5 `1ba30c018078a60281cba4baeacc03c4` == B6a→B7 baseline; `ENSEMBLE_UPGRADE_SCRIPTS_DIR` **ABSENT** from the file (B7 restored it) | ✓ (pre-provisioning) |
| Daemon env nuance (recorded honestly) | the RUNNING daemon (99890, booted 22:01:53Z under B7's provisioned .env) still carried `ENSEMBLE_UPGRADE_SCRIPTS_DIR` in its PROCESS env (`ps -E` count=1) — file-clean, process-live. Per the FL-23 user ruling the cycles still re-provision via disclosed .env append + restart each time (consistency within banked evidence) | noted |
| Tags | `v0.10.7-p2.3-b65` @ `8a0f252c` + `v0.10.8-p2.3-b7cyc1` @ `cc3a7d5a`; **zero tags at HEAD `3a9c9c10`** (`git tag --points-at HEAD` empty; `git describe --tags --exact-match HEAD` fatal 128) — the b7cyc1 stage tag sits one commit behind HEAD | ✓ STALENESS RULE honored: NO new tag/version minted; re-stage reuses the existing tag (§3 mechanics) |
| Releases on disk | `dr4reset / b65 / b7cyc1` (keep-3); dist/ensemble-prod sha `bd740467e7d312a28cd4cb1c41d92254207236a4c14a9611aea94e2cf8823838` == deployed b7cyc1 binary == manifest `binary_sha256` (byte-identical twin in place) | ✓ |
| `.launcher-state` | `last_exit=75 crash_count=4` — DR-1 residue, unchanged through this batch | recorded (B7 precedent) |
| `uv sync --extra dev` | Not run — no repo packs this batch (B6a/B6c/B6.5/B7 precedent; §0.3 applies only when packs run) | per precedent |

## 2. FL-23 executor-scripts provisioning — BOTH cycles (disclosed; identical mechanism per user ruling)

Mechanism (user-ruled, mirror cycle #1 exactly): disclosed `.env` append of the ops knob + one manual restart to load it (the runbook §4/P7.2 manual procedure), the var live during the armed call, `.env` restored bit-exact after the cycle's evidence is captured. Value used (the b7cyc1 release's scripts path, cycle-#1 precedent):

```text
ENSEMBLE_UPGRADE_SCRIPTS_DIR=/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble/scripts/upgrade
```

| Step | Cycle #2 (22:25:39–22:26:19Z) | Cycle #3 (22:35:59–22:36:12Z) |
|---|---|---|
| Backup | `cp .env /tmp/b8a-ev/env-backup-c2` (byte-identical to B7's `/tmp/b7-ev/env-drill-backup` — verified `diff` empty) | `cp .env /tmp/b8a-ev/env-backup-c3` |
| FL-4 pre-check | trailing `\n` present — append safe (no newline added) | same |
| Provision | append exactly ONE line (value above); `diff` = `82a83` single-line delta | same; `diff` = `82a83` |
| `.env` md5 path | `1ba30c01…` → **`ed965628dd8dd7b211b3c6db3c4fe815`** (provisioned) | `1ba30c01…` → **`ed965628dd8dd7b211b3c6db3c4fe815`** (identical provisioned hash — reproducible) |
| Load (manual restart) | stop-ensemble SINGLE-TERM 99843 → nohup launcher → livez 200 ~4s, readyz `[]`; new family 41911(wrapper)→41913(launcher)→42016→**42017** (daemon), born 22:26:08Z | stop SINGLE-TERM 50763 → livez 200 ~4s; new family →**64708** (daemon), born 22:36:02Z |
| Var live in daemon env | `ps -E -p 42017` → count=1 ✓ | `ps -E -p 64708` → count=1 ✓ |
| Live checkpoint mid-cycle | byte-identical to DR-0 ✓ (22:26:19Z) | — (cycle #3 mid-checkpoint taken post-commit, §7) |
| **Restore proof** | `cp env-backup-c2 .env` → md5 **`1ba30c018078a60281cba4baeacc03c4`** + `cmp` byte-exact ✓ (22:35:39Z, AFTER all cycle-2 evidence captured) | `cp env-backup-c3 .env` → md5 **`1ba30c018078a60281cba4baeacc03c4`** + `cmp` byte-exact ✓ (22:52:41Z, AFTER all cycle-3 evidence captured) |
| Post-restore unload | (running daemon keeps the var in process env until next boot — B7 §2 semantics; cycle #3's load-restart follows immediately) | **FINAL restart executed** (22:52:41–47Z): new daemon **99173**, `ps -E` count=**0** — var ABSENT from the final daemon process env ✓ (restoration verified file AND process side) |

**B7-hash discrepancy (friction FL-28):** B7 §2 recorded its provisioned-state md5 as `e9967ede…`; the identical baseline + identical one-line append reproduces as `ed965628…` (both cycles). B7's recorded value evidently captured a different intermediate state; zero functional impact — provisioning is defined by the single-line diff (value verbatim above), and the bit-exact restore is proven against the shared, verified backup.

## 3. Idempotent RE-STAGE of b7cyc1 — mechanics (both cycles; friction-logged per dispatch)

| Step | Cycle #2 (22:26:28–22:26:55Z) | Cycle #3 (22:36:13–22:36:30Z) |
|---|---|---|
| Tag-guard friction (FL-29) | stage.sh exact-tag guard (ADR-009 D3) requires `git describe --tags --exact-match HEAD` == VERSION; HEAD `3a9c9c10` is UNTAGGED (tag at `cc3a7d5a`, one docs-only commit behind). Resolution: `git checkout cc3a7d5a` (detached; dirty `.agents/approver/active.md` carried cleanly — blob identical in both commits) → stage → `git checkout feature/self-restart-p2p3-ladder-drills` (HEAD restored `3a9c9c10`; porcelain = pre-existing ` M .agents/approver/active.md` only). No tag moved, no new version minted | same dance, same clean restoration |
| Stage cmd | `VERSION=v0.10.8-p2.3-b7cyc1 ENSEMBLE_BINARY_VERSION=0.10.5 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo` (overrides: BINARY_VERSION ×1 + ROLLBACK_SAFE ×1 per cycle, D-FA4.5 recorded) | same |
| Binary | **"using existing binary"** — no rebuild: `dist/ensemble-prod` sha == deployed b7cyc1 binary sha (the B7 fresh build left in place) → re-staged payload byte-identical INCLUDING the binary | same |
| Manifest | md5 `5f1c5fb1b626eb4cf769b56f59ad54e9` BEFORE == AFTER — **BYTE-IDENTICAL** (idempotent: `staged_at=2026-08-23T22:00:22Z` preserved from cycle #1; all checksums equal) | same — byte-identical again |
| Swap-in | rename-aside; zero `.staging.*` / `.aside` droppings post-swap; releases on disk unchanged `dr4reset / b65 / b7cyc1` | same |
| Journal / .env | journal md5 UNCHANGED by staging (`8ebbb6ae…` — staging never touches it); `.env` SELF_ENV sed no-op (md5 stable at provisioned value) | journal md5 `4d0309b1…` unchanged by staging; same no-op |
| exit | 0 (`NO flip performed (promote.sh owns current)`) | 0 |

## 4. Cycle #2 — ari-driven same-version re-promote (§6 step 2 shape; CYCLE #2 of the ledger run)

### 4.1 The ari turn (job `d2802d0a-842c-459c-b95b-c02ed7a37ea3`, `idempotency_key=b8a-cyc2-u-1`, JAFP public path)

Queued 22:27:12Z → processing (instance `fd8be3c2`, agent_dir = the b7cyc1 release's ari) → **completed 22:28:07Z** `{"success": true}`. Two tool calls (verbatim from the instance transcript):

```json
{"name": "system_upgrade", "arguments": {"dry_run": true,  "target_env": "demo", "version": "v0.10.8-p2.3-b7cyc1"}}
{"name": "system_upgrade", "arguments": {"dry_run": false, "target_env": "demo", "version": "v0.10.8-p2.3-b7cyc1"}}
```

Call 1 (plan — read-only): `UPGRADE PREFLIGHT (dry-run) — env=demo target=v0.10.8-p2.3-b7cyc1 / current=v0.10.8-p2.3-b7cyc1 (via releases/current) rollback_safe=true / target staged … manifest rollback_safe=true … binary_version=0.10.5 / journal: current=v0.10.8-p2.3-b7cyc1 previous=v0.10.7-p2.3-b65 rollbacks_24h=0/3 cooldown=none quarantine=[] / in-flight=none / lock: free / cooldown=clear / PLAN: pg_dump preflight → stop (SINGLE-TERM) → flip current→v0.10.8-p2.3-b7cyc1 → start → gate (/livez ≤60s, /readyz ≤120s, 300s soak) → commit | auto-rollback / NO mutation happened (dry-run).`

Call 2 — the ARMED banner (verbatim):
```text
UPGRADE ARMED — run_id=r-20260823-222757-9aea env=demo target=v0.10.8-p2.3-b7cyc1 mode=promote
executes: after this turn completes (deferred — daemonized promote.sh)
watch: upgrade_status(run_id="r-20260823-222757-9aea") for phase transitions; terminal state readable post-restart
journal: releases/state.json txn opened (started_at=2026-08-23T22:27:57Z, owner=exec-pending)
confirmation: none required (non-live target)
trigger: post-turn-callback armed
busy-check=BUSY (live tasks exist — restart proceeds; in-flight work resumes from checkpoints, D-FA5.2)
```

**c1 provenance (in-journal):** pending_op captured from disk at 22:28:14Z — `run_id=r-20260823-222757-9aea`, `armed_at=2026-08-23T22:27:57Z`, **`armed_by_instance=fd8be3c2-2acd-4fef-8641-f50b7258cc3d` (ari's instance — the job's instance_id)**, `trigger=post-turn-callback`, owner_pid 49198 (executor).

### 4.2 Execution timeline (UTC; promote transcript verbatim in `release_info`'s upgrade.log tail, `/tmp/b8a-ev/c2-job2-ari-output.txt`)

| Phase | Timestamp | Evidence | Result |
|---|---|---|---|
| Arm (ari call 2) → pending_op | 22:27:57 | journal `armed_at` | ✓ |
| Job terminal (turn finalized) | 22:28:07 | `GET /api/jobs/d2802d0a` completed — **pre-stop** (post-turn trigger by design) | ✓ |
| Executor fired | 22:28:05 local-log / spawned at turn end | launcher.log `[system-execution] fired promote executor run_id=r-20260823-222757-9aea pid=49198 (daemonized, start_new_session)` | ✓ |
| Preflight | ≈22:28:11 | `txn open: promote target=v0.10.8-p2.3-b7cyc1 pid=49198 (outer window 600s)`; integrity **TARGET-only** — the same-version guard `[ "$CUR" != "$VERSION" ]` skips CURRENT drift-verification ("Same-version re-promote verifies once", promote.sh) | ✓ |
| Stop (SINGLE-TERM) | 22:28:11–14 | `launcher[41913]: received SIGTERM — forwarding to child (pid 42016)`; stop-ensemble TERMed 41911 (/bin/sh wrapper) + 41913 (launcher); **zero kill-9/SIGKILL lines in the window** | ✓ graceful |
| Atomic flip | ≈22:28:16 | `current -> releases/v0.10.8-p2.3-b7cyc1 (atomic flip)` (same-version repoint) | ✓ |
| `/livez` 200 | uptime 2.16s at gate probe | `livez OK: {"status":"alive",…}` | ✓ ~2s ≤60s |
| `/readyz` 200 | **22:28:18.49** (`checked_at`) | `readyz OK … reasons:[]` | ✓ ~2–4s from flip ≤120s |
| Version verify | 22:28:18 | `version verify OK: 0.10.5` | ✓ |
| Soak | 22:28:18 → 22:33:18 | `soak 300s (re-probe /livez + /readyz every 30s)` → `soak complete (300s green)`; poll observed livez 200 every 10s throughout | ✓ 300s green |
| **Commit** | **22:33:21Z** (journal ts) | `COMMITTED: current=v0.10.8-p2.3-b7cyc1 previous=v0.10.8-p2.3-b7cyc1`; `retention: 3 releases ≤ keep=3 — nothing to evict` | ✓ arm→commit **≈324s ≪ 600s** |

Journal commit event verbatim:
```text
2026-08-23T22:33:21Z | commit | promote v0.10.8-p2.3-b7cyc1 committed (gate+soak green; previous=v0.10.8-p2.3-b7cyc1)
```

### 4.3 Terminal state delivered to ari (job `21734cb3`, fresh post-restart turn, completed 22:35:01Z)

`upgrade_status(run_id="r-20260823-222757-9aea")` — the STRUCTURED TERMINAL state (not an intermediate):
```text
UPGRADE STATUS — env=demo
TERMINAL
outcome=committed last event: 2026-08-23T22:33:21Z commit — promote v0.10.8-p2.3-b7cyc1 committed (gate+soak green; previous=v0.10.8-p2.3-b7cyc1)
current=v0.10.8-p2.3-b7cyc1 previous=v0.10.8-p2.3-b7cyc1 rollbacks_24h=0/3 cooldown=none
pipeline lock: free
daemon :7979 /livez version=0.10.5
daemon :7979 /readyz status=ready
```
`release_info(section=journal)` in the same turn returned the full journal dump — every field (current/previous/in-flight/counters/cooldown/quarantine/history/pending_op) matched ground truth reads taken from disk at the same moment (zero diffs; parity table §6).

### 4.4 §4.1 clause evidence — cycle #2

| Clause | Evidence | Verdict |
|---|---|---|
| c1 — promote e2e preflight→stop→flip→gate→commit, **ARI-DRIVEN** | §4.1–4.2: ari's call pair → ARMED banner run_id=r-20260823-222757-9aea → journal pending_op `armed_by_instance=fd8be3c2` (ari) + executor pid 49198 + commit 22:33:21Z | **PASS** |
| c2 — gates green in budget | livez ~2s ≤60s; readyz 22:28:18.49 ~2–4s ≤120s; version verify OK; 300s soak green | **PASS** |
| c3 — no rollback/sweep-rollback/halt in cycle window | cycle window (commit 22:06:58Z → commit 22:33:21Z) contains ZERO violation events (next-window `sweep` 22:37:22Z lands in cycle #3's ledger window — see §5.4); readiness log-scan from the promote boot anchor: **0** `Readiness] degraded`, **0** ` 503 ` lines (`c2-launcherlog-bootwindow.txt`) | **PASS** |
| c4 — post-cycle healthy | `/readyz` 200 `reasons:[]` at 22:35:18 (post-commit settle); daemon 50809 uptime continuous since 22:28:14 (no restart post-commit until the cycle-#3 provisioning restart) | **PASS** |
| c5 — no unintended work loss | jobs pre-promote 8 → post 10: zero pre-existing jobs changed, 2 new (the cycle's own jobs) both completed; all-terminal (FL-20 basis `GET /api/jobs?limit=100`) | **PASS** |
| c6 — zero live contact | §7 checkpoints — DR-0 / mid-provisioning / post-commit all byte-identical to baseline | **PASS** |
| c7 — restart cycle clean | promote's own restart: SINGLE-TERM (~3s) → respawn → livez ~2s / readyz ~4s green → 0 degraded/503/kill-9 lines in the boot window | **PASS** |

## 5. Cycle #3 — identical shape (CYCLE #3 of the ledger run — LEDGER COMPLETE)

### 5.1 The ari turn (job `98e4749b-c9da-4462-9177-004dc3cc0440`, `idempotency_key=b8a-cyc3-u-1`)

Queued 22:36:35Z → processing (instance `2daca0e2`) → **completed 22:37:37Z** `{"success": true}`. Same two-call pair (dry_run=true → dry_run=false, target `v0.10.8-p2.3-b7cyc1`). Call-1 plan now reads `current=v0.10.8-p2.3-b7cyc1 previous=v0.10.8-p2.3-b7cyc1` (the cycle-#2 re-anchor). ARMED banner (verbatim):

```text
UPGRADE ARMED — run_id=r-20260823-223728-6c04 env=demo target=v0.10.8-p2.3-b7cyc1 mode=promote
executes: after this turn completes (deferred — daemonized promote.sh)
watch: upgrade_status(run_id="r-20260823-223728-6c04") for phase transitions; terminal state readable post-restart
journal: releases/state.json txn opened (started_at=2026-08-23T22:37:28Z, owner=exec-pending)
confirmation: none required (non-live target)
trigger: post-turn-callback armed
busy-check=BUSY (live tasks exist — restart proceeds; in-flight work resumes from checkpoints, D-FA5.2)
```

**c1 provenance:** pending_op `armed_by_instance=2daca0e2-1807-4e8e-b449-c6d55f9c9cd4` (ari's instance), `armed_at=2026-08-23T22:37:28Z`, owner_pid 71135, `trigger=post-turn-callback`.

### 5.2 Execution timeline (UTC)

| Phase | Timestamp | Evidence | Result |
|---|---|---|---|
| Arm → pending_op | 22:37:28 | journal `armed_at` | ✓ |
| Job terminal | 22:37:37 | completed, pre-stop | ✓ |
| Executor fired | 22:37:33 (local log) | `[system-execution] fired promote executor run_id=r-20260823-223728-6c04 pid=71135 (daemonized, start_new_session)` | ✓ |
| Preflight + txn open | ≈22:37:38 | TARGET-only integrity (same-version); txn open pid=71135 | ✓ |
| Stop (SINGLE-TERM) | 22:37:39–40 | `launcher[64680]: received SIGTERM — forwarding to child (pid 64705)` → `shutdown complete — exiting 143`; **zero kill-9** | ✓ |
| Respawn + boot sweep | 22:37:41–42 | `launcher[72798]: ensemble launcher starting` → `journal sweep: in_flight promote txn (target=v0.10.8-p2.3-b7cyc1) is fresh (3s ≤ 600s) — leaving alone` (D-FA4.3 — boot sweep defers to the live owner) | ✓ |
| Atomic flip + gates | ≈22:37:43 | flip → livez ~2s / readyz green / version verify OK (0.10.5) | ✓ |
| Soak | 300s green | `soak complete (300s green)`; livez 200 observed every 10s | ✓ |
| **Commit** | **22:42:47Z** | `COMMITTED: current=v0.10.8-p2.3-b7cyc1 previous=v0.10.8-p2.3-b7cyc1`; retention nothing to evict | ✓ arm→commit **≈319s ≪ 600s** |

Journal commit event verbatim:
```text
2026-08-23T22:42:47Z | commit | promote v0.10.8-p2.3-b7cyc1 committed (gate+soak green; previous=v0.10.8-p2.3-b7cyc1)
```

### 5.3 Terminal state delivered to ari (job `cb44e8e4`, fresh post-restart turn, completed 22:52:00Z)

```text
UPGRADE STATUS — env=demo
TERMINAL
outcome=committed last event: 2026-08-23T22:42:47Z commit — promote v0.10.8-p2.3-b7cyc1 committed (gate+soak green; previous=v0.10.8-p2.3-b7cyc1)
current=v0.10.8-p2.3-b7cyc1 previous=v0.10.8-p2.3-b7cyc1 rollbacks_24h=0/3 cooldown=none
pipeline lock: free
daemon :7979 /livez version=0.10.5
daemon :7979 /readyz status=ready
```
(+ `release_info(section=journal)` full dump — field-for-field parity with ground truth, zero diffs.)

### 5.4 §4.1 clause evidence — cycle #3

| Clause | Evidence | Verdict |
|---|---|---|
| c1 — ari-driven promote e2e | §5.1–5.2: ARMED run_id=r-20260823-223728-6c04, `armed_by_instance=2daca0e2` (ari), executor 71135, commit 22:42:47Z | **PASS** |
| c2 — gates in budget | livez ~2s; readyz green ≤120s; version verify OK; 300s soak green | **PASS** |
| c3 — no violation events in cycle window | window (commit 22:33:21Z → commit 22:42:47Z) contains EXACTLY ONE event: plain `sweep` 22:37:22Z — the cycle-#2 pending_op's lazy closure fired by cycle #3's own `system_upgrade` actor entry (FL-26 predicted shape; plain `sweep` ∉ VIOLATION_EVENTS `rollback|sweep_rollback|halt` — FL-25); readiness log-scan from the promote boot anchor (line 8127): **0** degraded / **0** 503 lines | **PASS** |
| c4 — post-cycle healthy | `/readyz` 200 `reasons:[]` at 22:52:06; daemon 72842 uptime continuous since 22:37:42 through the terminal-read job (final restart only after evidence capture) | **PASS** |
| c5 — no work loss | jobs 10 → 12: zero pre-existing changed; 2 new (the cycle's own) completed; all-terminal | **PASS** |
| c6 — zero live contact | §7 post-commit checkpoint byte-identical | **PASS** |
| c7 — restart clean | SINGLE-TERM → respawn 22:37:41 → boot sweep defers fresh txn → livez/readyz green → 0 degraded/503/kill-9 in boot window (`c3-launcherlog-bootwindow.txt`, 152 lines) | **PASS** |

## 6. Parity proofs (ari-reported vs journal ground truth vs probes — field-for-field, both cycles)

| Field | Cycle #2 (ari `upgrade_status` 22:34–22:35Z) | Cycle #3 (ari `upgrade_status` 22:51–22:52Z) | Ground truth | Diff |
|---|---|---|---|---|
| current | v0.10.8-p2.3-b7cyc1 | v0.10.8-p2.3-b7cyc1 | journal `current` + `status.sh` | **0** |
| previous | v0.10.8-p2.3-b7cyc1 | v0.10.8-p2.3-b7cyc1 | journal `previous` (same-version re-anchor) | **0** |
| in_flight | none | none | `null` post-terminal | **0** |
| rollbacks_24h | 0/3 | 0/3 | `24h:0` | **0** |
| cooldown / quarantine | none / [] | none / [] | null / [] | **0** |
| lock | free | free | 0 lock dirs | **0** |
| last event | commit 22:33:21Z | commit 22:42:47Z | journal tail verbatim | **0** |
| /livez version | 0.10.5 | 0.10.5 | probe | **0** |
| /readyz | ready | ready | probe 200 `[]` | **0** |
| terminal outcome | committed @22:33:21Z | committed @22:42:47Z | journal | **0** |

## 7. Live pid checkpoints (read-only, zero live contact — every boundary)

| Checkpoint | Moment (UTC) | pid/ppid | lstart | Diff vs DR-0 |
|---|---|---|---|---|
| DR-0 / drill start | 22:24:35 | 31150/31130 | Sat Aug 22 10:04:07 2026 | baseline |
| mid — post cycle-#2 provisioning restart | 22:26:19 | same | same | identical ✓ |
| post-commit cycle #2 | 22:35:2x | same | same | identical ✓ |
| post-commit cycle #3 | 22:52:1x | same | same | identical ✓ |
| **final (post-restore + final restart)** | 22:53:05 | same | same | **identical ✓ (`diff live-pid-start.txt live-pid-final.txt` empty)** |

No signal was ever sent to a live pid; the live install was read only via `lsof`/`ps` with the port value never written to disk.

## 8. Completed-ledger matrix (§7 machine source of truth — `scripts/upgrade/ledger_check.py`, both f2 states, VERBATIM, 22:52:54Z; journal md5 at capture `3817b176cdd6c631665969147c70ff85`, 12 events)

```text
$ python3 scripts/upgrade/ledger_check.py --journal ~/agents-ensemble-demo/releases/state.json --f2-state closed
ledger-check: journal=/Users/nguyenminhkha/agents-ensemble-demo/releases/state.json
f2-state: closed
cycles: 4
cycle 1: version=v0.10.7-p2.3-b65 txn=2026-08-23T21:39:13Z verdict=SUPERSEDED
cycle 2: version=v0.10.8-p2.3-b7cyc1 txn=2026-08-23T22:06:58Z verdict=CLEAN
cycle 3: version=v0.10.8-p2.3-b7cyc1 txn=2026-08-23T22:33:21Z verdict=CLEAN
cycle 4: version=v0.10.8-p2.3-b7cyc1 txn=2026-08-23T22:42:47Z verdict=CLEAN
staleness: reset — count re-entered at cycle 2 (version changed v0.10.7-p2.3-b65 → v0.10.8-p2.3-b7cyc1); superseded cycles: 1
current version: v0.10.8-p2.3-b7cyc1
journal current: v0.10.8-p2.3-b7cyc1
consecutive clean: 3 (need 3, ADR-021)
gate verdict: ELIGIBLE
  - 3 consecutive clean cycles at version v0.10.8-p2.3-b7cyc1 (≥ N=3, ADR-021)
  - F2 closed (caller-supplied)
note: coverage: journal-checkable clauses of test-strategy.md 4.1 only; clauses 3-5 (readiness log-scan, work-loss resume evidence, live-pid checkpoint) are external evidence audited in RESULTS files — the gate consumer folds both
```

```text
$ python3 scripts/upgrade/ledger_check.py --journal ~/agents-ensemble-demo/releases/state.json --f2-state open
ledger-check: journal=/Users/nguyenminhkha/agents-ensemble-demo/releases/state.json
f2-state: open
cycles: 4
cycle 1: version=v0.10.7-p2.3-b65 txn=2026-08-23T21:39:13Z verdict=SUPERSEDED
cycle 2: version=v0.10.8-p2.3-b7cyc1 txn=2026-08-23T22:06:58Z verdict=CLEAN
cycle 3: version=v0.10.8-p2.3-b7cyc1 txn=2026-08-23T22:33:21Z verdict=CLEAN
cycle 4: version=v0.10.8-p2.3-b7cyc1 txn=2026-08-23T22:42:47Z verdict=CLEAN
staleness: reset — count re-entered at cycle 2 (version changed v0.10.7-p2.3-b65 → v0.10.8-p2.3-b7cyc1); superseded cycles: 1
current version: v0.10.8-p2.3-b7cyc1
journal current: v0.10.8-p2.3-b7cyc1
consecutive clean: 3 (need 3, ADR-021)
gate verdict: BLOCKED
  - F2-open: the unauthenticated loopback API user-origin forge lane is open — gate hard-blocked regardless of cycle count (runbook §9)
note: coverage: journal-checkable clauses of test-strategy.md 4.1 only; clauses 3-5 (readiness log-scan, work-loss resume evidence, live-pid checkpoint) are external evidence audited in RESULTS files — the gate consumer folds both
```

**Per-cycle ledger table (§7 human-copy, DERIVED from checker output — journal wins on disagreement):**

| Cycle # | Date | Version | Journal txn id | Verdict (§4.1) | Evidence link |
|---|---|---|---|---|---|
| (rehearsal) | 2026-08-23 | v0.10.7-p2.3-b65 | 2026-08-23T21:39:13Z | SUPERSEDED (was CLEAN-with-flag; superseded by the version change) | `2026-08-23-p2-3-b65-promote-t8-recapture.md` |
| #1 | 2026-08-23 | v0.10.8-p2.3-b7cyc1 | 2026-08-23T22:06:58Z | CLEAN (c1–c7; ari-driven, in-journal provenance) | `2026-08-23-p2-3-b7-dr5-ari-e2e.md` |
| #2 | 2026-08-23 | v0.10.8-p2.3-b7cyc1 | 2026-08-23T22:33:21Z | CLEAN (c1–c7, §4.4 above) | this file |
| #3 | 2026-08-23 | v0.10.8-p2.3-b7cyc1 | 2026-08-23T22:42:47Z | CLEAN (c1–c7, §5.4 above) | this file |

Gate rule: 3 consecutive clean ✓ AND F2 CLOSED required. **F2-open ⇒ hard-blocked regardless of count — proven above on the COMPLETED real ledger (first time): the §9 hard-block line fires at consecutive=3 exactly as at consecutive=1.** Even with F2 closed, live promotion remains USER-EXECUTED (ADR-017; §8 never an agent procedure).

## 9. FL-21 checks (rollback-dependent assertions — `previous` on disk)

| Check | Cycle #2 | Cycle #3 | Final |
|---|---|---|---|
| journal `previous` | v0.10.8-p2.3-b7cyc1 (same-version re-anchor: OLD_CUR == VERSION) | v0.10.8-p2.3-b7cyc1 | v0.10.8-p2.3-b7cyc1 |
| `previous` on disk? | ✓ `releases/v0.10.8-p2.3-b7cyc1/` exists (integrity-verified at preflight) | ✓ same | ✓ same |
| Distinct older LKG also on disk | ✓ b65 retained (keep-3: dr4reset, b65, b7cyc1 — retention evicted nothing either cycle: "3 releases ≤ keep=3") | ✓ | ✓ |

Note (honest semantics): after cycle #1, `previous` re-anchored from b65 to b7cyc1 at each same-version commit — `previous == current` is the designed M4-shape for re-promotes (promote.sh: "when the promote WAS a same-version re-promote (CUR == VERSION), the 'old current' IS the version"; the degenerate pair is never quarantined, and b65 remains on disk as a distinct flip-forward target regardless).

## 10. Constraint compliance

- **Zero live contact:** every actor surface was the demo daemon (install-dir anchor `~/agents-ensemble-demo`, §0.5/§0.6; live-path patterns anchored; lsof port value never written to a captured file); §7 byte-identical at all 5 checkpoints; no signal ever sent to a live pid.
- **Port-literal rule:** zero live-port literals in this file and all `/tmp/b8a-ev/` evidence (grep-gated pre-commit); demo 7979 written freely.
- **Demo `.env`:** FL-23 provisioning appended + restored BIT-EXACT **per cycle** (§2 md5 paths; `cmp` proofs); post-cycle-3 restoration verified file-side (md5 `1ba30c01…`) AND process-side (final restart → `ps -E` count=0).
- **Repo writes:** this RESULTS file ONLY (+ two transient detached-checkouts of the already-tagged commit `cc3a7d5a` for the stage tag-guard, each restored immediately — working tree returned to `3a9c9c10`, porcelain unchanged: pre-existing ` M .agents/approver/active.md` only, NEVER staged); no new tag/version minted (STALENESS RULE); `dist/ensemble-prod` gitignored (reused, not rebuilt).
- **Overrides (D-FA4.5):** per cycle `ENSEMBLE_BINARY_VERSION=0.10.5` ×1 + `ENSEMBLE_ROLLBACK_SAFE=1` ×1 at stage time — recorded §3.
- **STALENESS RULE:** both cycles at the SAME version b7cyc1; ledger confirms no reset (checker: "consecutive clean: 3").
- **No mid-drill debugging:** every anomaly in-window is recorded as finding/friction; nothing patched drill-side.
- **Restore / final state:** demo green (`/livez` 200, `/readyz` 200 `reasons:[]` at 22:52:47Z on final daemon 99173, born 22:52:44Z); `in_flight: null`; pending_op note below (FL-26 recurrence, closable-by-next-actor-read); lock free; `.launcher-state` crash_count unchanged (4 — graceful restarts never touched it); version smoke OK.

## 11. Friction log — B8a additions

| # | Where | Doc/ruling says | Observed | Classification |
|---|---|---|---|---|
| FL-28 | B7 §2 provisioned-hash reproducibility | B7 recorded provisioned `.env` md5 `e9967ede…` | Identical baseline + identical one-line append reproduces `ed965628…` (both cycles, deterministic). B7's value evidently captured a different intermediate state. Provisioning should be specified by its DIFF (one line, value recorded), not by a provisioned-state hash | **technique note** |
| FL-29 | stage.sh exact-tag guard vs re-stage after evidence commits | ADR-009 D3 "stage what was built": HEAD must be exactly tagged VERSION | After an evidence commit lands on the drill branch, the release tag sits ≥1 commit behind HEAD and the re-stage REFUSES until HEAD is temporarily detached to the tagged commit. Worked cleanly (docs-only delta; dirty file carried), but the dance is undocumented — future same-version cycles after commits need it named in the runbook | **doc gap (minor)** |
| FL-30 | FL-23 recurrence + process-env carryover | FL-23 ruling: same disclosed provisioning each cycle | The daemon booted at cycle-#1's promote still carried the var in its PROCESS env at DR-0 (file restored, process not). Re-provisioning per the ruling was therefore belt-and-braces for the armed call (var was already live) but restores FILE-state consistency — which is what the banked evidence pattern requires. Final restart clears the process env (verified count=0) | **OK-but-noted** |
| FL-26 (recurrence) | pending_op lazy closure | B7 FL-26: pending_op closes on the next ACTOR tool entry | Observed exactly: cycle-#2's pending_op closed by cycle-#3's first `system_upgrade` entry (+`sweep` 22:37:22Z, cycle-neutral); cycle-#3's pending_op lingers at drill end (read-pair `upgrade_status`/`release_info` do not close it). `in_flight` is null; `expires_at` bounds it; next actor entry on the demo closes it. Restore bar remains "pending_op null OR closable-by-next-actor-read" (B7 wording) | **OK-but-noted (as predicted)** |
| FL-31 | same-version re-promote specifics | promote.sh M4 no-strand guard contemplates CUR==VERSION | First real same-version re-promotes on the demo: CURRENT drift-verification correctly skipped ("verifies once"), commit sets previous==current (degenerate pair, never quarantined), retention stable (no eviction). All as designed; recording the observed semantics for future ledger readers (the §7 table's "same version" rows produce previous==current journal anchors) | **technique note** |
| FL-32 | boot-sweep vs in-flight promote | D-FA4.3 boot sweep owns restarts | Cycle #3's respawn log line: `journal sweep: in_flight promote txn … is fresh (3s ≤ 600s) — leaving alone` — clean confirmation the boot sweep defers to a live promote owner (previously only unit-proven) | **OK-but-noted (positive)** |

## 12. Findings + final state handoff

1. **F-B8a-1 (LEDGER COMPLETE — 3 consecutive clean @ v0.10.8-p2.3-b7cyc1):** cycles #2 (txn 22:33:21Z) and #3 (txn 22:42:47Z) join cycle #1 (txn 22:06:58Z) — all ari-driven (in-journal `armed_by_instance` provenance each time), all §4.1 c1–c7 PASS, zero violation events in any cycle window; the b65 rehearsal cycle stays SUPERSEDED. The checker reports `consecutive clean: 3 (need 3, ADR-021)` on the real completed journal.
2. **F-B8a-2 (completed-ledger matrix captured):** f2-closed ⇒ **ELIGIBLE** ("3 consecutive clean cycles … F2 closed (caller-supplied)") — the FIRST ELIGIBLE verdict on a completed real ledger; f2-open ⇒ **BLOCKED** (§9 hard-block regardless of count) — the §9 line proven on the completed ledger (it fires at 3 exactly as at 1: no count substitutes for the forge lane being closed). ELIGIBLE-pending-F2 is the honest label: F2 is OPEN today (the unauthenticated loopback user-origin lane, runbook §9 item 2).
3. **F-B8a-3 (same-version cycle mechanics banked):** the idempotent re-stage is byte-identical end-to-end (existing-binary reuse → manifest md5 stable across re-stages, staged_at preserved), the tag-guard detach dance (FL-29) is the only friction, and same-version re-promote semantics (single integrity verify, previous==current anchor, retention no-op) are now demo-proven, not just unit-proven.
4. **F-B8a-4 (final demo state handoff):** demo green on `v0.10.8-p2.3-b7cyc1` (serving 0.10.5 per manifest binary_version; final daemon **99173** born 22:52:44Z, clean of the FL-23 var in process env); journal `current=v0.10.8-p2.3-b7cyc1, previous=v0.10.8-p2.3-b7cyc1`, `in_flight:null`, counters `24h:0`, cooldown null, quarantine [], history **12 events** [commit 21:39:13Z, refusal 21:42:35Z, restart 21:57:48Z, commit 22:06:58Z, sweep 22:10:13Z, refusal 22:10:13Z, refusal 22:10:17Z, refusal 22:11:55Z ×2, **commit 22:33:21Z, sweep 22:37:22Z, commit 22:42:47Z**] (md5 `3817b176cdd6c631665969147c70ff85`); pending_op = cycle-#3's (FL-26 recurrence, closable); lock free; releases on disk `dr4reset / b65 / b7cyc1`; `.env` bit-exact baseline `1ba30c018078a60281cba4baeacc03c4`; tags `v0.10.7-p2.3-b65` @ `8a0f252c` + `v0.10.8-p2.3-b7cyc1` @ `cc3a7d5a` (zero at HEAD — next NEW-version stage needs a fresh tag); live untouched at every checkpoint.
