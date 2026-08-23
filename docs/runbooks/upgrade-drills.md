# Upgrade Drills Runbook — Self-Restart / Self-Upgrade Phase 2 (P2.3)

- **Version:** 1.0 @ <commit> *(commit hash filled at commit time)*
- **Canonical path:** `docs/runbooks/upgrade-drills.md` — **this file is the canonical drill runbook** (P2.3 design decision D2, `phase3-plan.md`; the alternative `.agents/devops/RUNBOOKS/` location was NOT chosen — this line is the canonical-path statement D2 requires).
- **Scope:** DR-1…DR-5 are executed on **demo (7979) / sandbox only** by this initiative. §8 (live promotion) is **USER-GATED DESIGN — NEVER EXECUTED BY THIS INITIATIVE** (D3). §9 is a hard gate that precedes any live eligibility.
- **Companion docs:** `phase3-plan.md` (D1–D4, carry-over matrix, tasks T1–T9) · `test-strategy.md` §3 (drill matrix) / §4.1 (canonical clean-cycle definition) / §5 (environment discipline) · `promotion-ladder.md` (S0–S6, U1–U6) · `decisions.md` (ADR-016…034).

**Live-contact prohibition — VERBATIM from `phase3-plan.md:3-4`** (governs every drill in this runbook):

> **⛔ HARD CONSTRAINT (user directive — VERBATIM, applies throughout this phase):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port `<live-port>`, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

*Redaction note: the source's live-port numeral is rendered `<live-port>` here per this file's port-literal rule — the single deviation; every other character is byte-identical to source.*

> **Port-literal rule for this file:** zero live-port literals appear anywhere below. The live port is always written *live port* in prose; the operator resolves its value at runtime from the live install's own config, never from this document. Demo port 7979 and sandbox ports (8377/8378 precedent) are the only port numbers written here.

**Standing live-pid-checkpoint invariant (every drill, no exceptions):** capture the live listener pid set read-only (`ps`/`lsof`, never a signal) at every drill start and end; the checkpoint output must be **byte-identical** to the DR-0 baseline at both ends. Any drift ⇒ abort the drill, change nothing else, escalate. (Phase-1 §5 precedent; `test-strategy.md` §5.5.)

---

## 0. Prerequisites (pre-drill checklist — run before every drill)

**0.1 Demo launcher/daemon version check** (`test-strategy.md` §7 assumption: the demo daemon runs the Phase-1 launcher lineage — verified, not assumed):

```bash
bash scripts/upgrade/status.sh demo
# EXPECT: resolved env triple → target=demo dir=~/agents-ensemble-demo port=7979 db=ensemble_demo
# EXPECT: journal readable; current -> releases/<ver>; no [QUARANTINED] on current
# EXPECT: "daemon :7979 /livez version=<ver>" AND "version smoke: OK" (D2/ADR-027)
curl -s localhost:7979/livez   # 200, {"status":"alive", ... "version":"<ver>"}
curl -s localhost:7979/readyz  # 200, reasons: []
```

**0.2 DR-0 record exists and is current.** First batch reference: `.agents/tester/RESULTS/2026-08-23-p2-3-dr0-preflight.md` (verdict `DR-0: CONTINUE`). If demo/live state has changed since the last DR-0 (release promoted, daemon restarted, launcher swapped), re-run the DR-0 shape (S1–S5) and record a fresh dated file before drilling.

**0.3 Dev environment:** `uv sync --extra dev` — **bare `uv sync` STRIPS the dev extra** (pytest-timeout drift; project critical note). Any pack run in the same session happens after this.

**0.4 Live pid baseline capture** (write to the drill's evidence dir; never signal):

```bash
mkdir -p <evidence-dir>
# resolve the live listener READ-ONLY; identify by install-dir association, not by a port literal here:
lsof -nP -iTCP -sTCP:LISTEN | grep -F "$(sed -n 's/^PORT=//p' ~/agents-ensemble/.env | head -1)"
ps -o pid,ppid,lstart,command -p <live-pids> | tee <evidence-dir>/live-pid-start.txt
# at drill end: re-run both, diff against live-pid-start.txt — MUST be byte-identical
```

DR-0 baseline for this batch: live listener pid 31150 (ppid 31130), port `<live-port>` (redacted per this runbook's rule).

**0.5 Target-triple assertion discipline.** Before ANY action on any environment, assert the **install-dir path + port + DB name** triple together — `status.sh` prints it (`echo_env_triple`); a mismatch aborts. The **one-digit-typo hazard**: the demo port and the live port differ by a single digit, and no pipeline command may ever reach live by typo. The exact install-dir string (`~/agents-ensemble-demo`) is the anchor — path mismatch aborts before any HTTP or process action (`test-strategy.md` §5.1–5.2).

**0.6 String discipline (live dir ⊂ demo dir).** The live dir name is a **substring of the demo dir name** (`~/agents-ensemble` matches inside `~/agents-ensemble-demo`). Any grep/pattern that must match the live dir — and only it — is **anchored with a trailing slash or end-anchor** (`~/agents-ensemble/`), never a bare prefix. Unanchored live-path patterns false-match the demo install.

---

## 1. Sandbox conventions (DR-2 and any sandbox leg)

1. **Own port** — 8377/8378 precedent; never 7979 (demo), 8079 (dev), or any live port.
2. **Throwaway PG** — `ensemble_test`-style DB **created and dropped per run**; never a shared/real DB name.
3. **Throwaway install dir + data dir** — e.g. `/tmp/ens-sbx-$$`; removed at drill end (it is drill-owned).
4. **Isolated HOME fixture** — run harness steps under `HOME=$(mktemp -d)` (`tests/test_release_journal.sh` precedent) so nothing resolves from the real home — which contains the live and demo install dirs.
5. **Zero live-port literals** in any script, command, or evidence file you author; the sandbox port is explicit (`PORT=8377`) and asserted.
6. **`ENSEMBLE_DEPLOY_LIVE` is never set** — scripts that read it treat unset = refuse live (`deploy.sh` exit-78 semantics). The upgrade suite's own live guard variable `ENSEMBLE_UPGRADE_LIVE` is likewise **never set by any drill** (live targeting exists only in §8, user-executed).
7. **Sandbox stage invocation shape** (from `stage.sh` usage): `TARGET=sandbox INSTALL_DIR=<dir> PORT=<port> [POSTGRES_DB=<db>] bash scripts/upgrade/stage.sh sandbox --version v1 --skip-build ./stub-prod`. Arg styles: the `VERSION=<v>` env form is equivalent to the `--version` flag form (both documented in `stage.sh` usage); DR-4 demo staging uses the env form — a real build from the repo unless `--skip-build` passes a stub.

---

## 2. DR-1 — tempfail→respawn full cycle (demo) [carry-over #1]

**What it closes:** "one live tempfail→recovery cycle on demo" — the exit-75→capped-backoff→respawn→recovery loop observed end-to-end (launcher suite proves it unit-level only). **Pass criterion (one line):** ≥2 exit-75→capped-backoff(5s→60s)→respawn cycles observed end-to-end with `crash_count` unchanged and `/livez`+`/readyz` green after PG restore.

### Procedure

```bash
# D1.0 — §0 checklist + pid baseline (0.4) into <ev> = evidence dir
# D1.1 — record the real demo DATABASE_URL (evidence):
grep -n '^DATABASE_URL=' ~/agents-ensemble-demo/.env | tee <ev>/env-before.txt
# D1.2 — drill-scoped bad-PG override (R3.1: drill-scoped, restorable):
bash scripts/stop-ensemble.sh ~/agents-ensemble-demo 7979        # clean stop
# pick an unreachable LOCAL socket verified closed first (never a real service port):
nc -z 127.0.0.1 <closed-port>; echo "probe=$?"                   # expect refused
(cd ~/agents-ensemble-demo && \
 DATABASE_URL='postgresql://drill:drill@127.0.0.1:<closed-port>/drill_unreachable' \
 nohup ./launcher.sh >> data/launcher.log 2>&1 &)
# D1.3 — observe ≥2 full cycles:
tail -f ~/agents-ensemble-demo/data/launcher.log                 # exit-75 lines + backoff waits
cp ~/agents-ensemble-demo/.launcher-state <ev>/launcher-state-cycle1.txt
sleep 70; cp ~/agents-ensemble-demo/.launcher-state <ev>/launcher-state-cycle2.txt
# D1.4 — restore + recovery. FIRST stop the tempfail-looping launcher from D1.2
# (the demo launcher supervising nothing, looping on exit 75): the stop line owns it
# via stop-ensemble.sh's own verification tiers (cmdline exe-path / cwd ==
# ~/agents-ensemble-demo, launcher.sh shape; never kills by port — no live-port
# resource), assert-then-SIGTERM per the DR-0 ownership protocol precedent
# (.agents/tester/RESULTS/2026-08-23-p2-3-dr0-preflight.md — kill withheld on
# assertion failure). Restart only once the stop transcript shows the launcher
# TERMined — else a second launcher stacks on the looping one.
bash scripts/stop-ensemble.sh ~/agents-ensemble-demo 7979
(cd ~/agents-ensemble-demo && nohup ./launcher.sh >> data/launcher.log 2>&1 &)
curl -s localhost:7979/livez; curl -s localhost:7979/readyz      # both 200, reasons: []
diff <ev>/env-before.txt <(grep -n '^DATABASE_URL=' ~/agents-ensemble-demo/.env)  # unchanged
```

If the deployed daemon's config layer does not honor the ambient-env override, fall back to: `cp .env .env.drill-backup` → edit `DATABASE_URL` in place → D1.4 restores from `.env.drill-backup` and verifies byte-identical. The shared `.env` is never left edited.

### Expected outputs

- Daemon boot PG-preflight fails → **exit 75** (boot-time tempfail); launcher logs the tempfail wait with **capped backoff 5s→60s** (ADR-011 ≤60s; `TEMPFAIL_BACKOFF_START_S=5`, `TEMPFAIL_BACKOFF_CAP_S=60`).
- `.launcher-state`: `last_exit=75`, `last_backoff` within 5..60, **`crash_count` UNCHANGED** (tempfail track is burst-budget-EXEMPT per ADR-011), `notified_75` may flip to 1 (informational only).
- After restore: recovery boot → `/livez` 200 within ≤60s, `/readyz` 200 `reasons: []` (**R3.1 mitigation — the post-drill `/readyz` green assertion is part of PASS**, not polish).

### Evidence to capture

Timestamped `.launcher-state` copies (≥2, showing exit-75 + unchanged `crash_count`) · `data/launcher.log` excerpt with exit-75 + backoff lines and timestamps · env override record (set + restore diff) · post-restore `/readyz` transcript · live pid checkpoint start/end (byte-identical).

### Restore of the drill

Override removed (or `.env` restored byte-identical from backup) → normal launcher restart → `/readyz` 200 asserted → no launcher abort state (`burst` track untouched) → journal untouched (no pipeline op ran).

---

## 3. DR-2 — exit-78 sandboxed smoke [carry-over #2]

**What it closes:** "fold exit-78 into Phase 2 live smoke if cheap" — the config-refuse path exercised beyond unit coverage (63/63). **Pass criterion (one line):** launcher exits **78 exactly once** with a logged refuse reason and **zero respawns** within the observation window; live pids byte-identical.

### Procedure

```bash
SBX=/tmp/ens-sbx-dr2-$$; PORT=8377; PG=ensemble_dr2_$$
createdb "$PG"
TARGET=sandbox INSTALL_DIR="$SBX" PORT=$PORT POSTGRES_DB="$PG" \
  bash scripts/upgrade/stage.sh sandbox --version v1 --skip-build ./<stub-binary>
# induce the fatal-config class (Phase-1 exit-75-smoke precedent shapes):
rm "$SBX/releases/v1/ensemble-prod"            # missing binary → boot refuses (exit 78 class)
HOME=$(mktemp -d) PORT=$PORT INSTALL_DIR="$SBX" \
  bash "$SBX/launcher.sh"; echo "exit=$?" | tee <ev>/exit-code.txt   # expect: exit=78
sleep 60; pgrep -fl "$SBX" | tee <ev>/no-respawn-check.txt           # expect: no output (no loop)
# cleanup (drill-owned throwaway):
rm -rf "$SBX"; dropdb "$PG"; lsof -nP -iTCP:$PORT -sTCP:LISTEN      # expect: nothing listening
```

(Alternative induction of the same class: invalid/unsatisfiable env in the sandbox install — any fatal-config boot refusal, exit 78.)

### Expected outputs

- Captured **exit code 78**, immediately (no backoff — 78 is refuse-not-retry; only exit 75 is the tempfail-retry track).
- Launcher log shows the **refuse reason** (config-error class).
- **No respawn** within the observation window (exactly one attempt) — the no-loop assertion.
- Live pid checkpoint unchanged (the drill never left its sandbox).

### Evidence to capture

Exit-code transcript (`exit=78`) · launcher log excerpt with the refuse reason · no-respawn observation output · sandbox tree listing pre/post cleanup · live pid checkpoint.

### Restore of the drill

Throwaway PG dropped + sandbox dir removed + port verified free. Nothing else existed — the drill is fully self-contained; demo and live were never touched.

---

## 4. DR-3 — P7 readiness green→red→green (demo) [carry-over #3]

**What it closes:** the P7 readiness drill on a deployed daemon, with the restore semantics documented **verbatim** (T2). **Pass criterion (one line):** 5-row timestamped `200 → 503 → 200` readiness transition captured with `/livez` 200 throughout and green restored **by restart** with the knob verified absent from the demo `.env`.

### The restore-semantics note — VERBATIM (from `.agents/tester/RESULTS/2026-08-22-ar-phase1-followups-verification.md:47`)

```text
**P7 drill on deployed daemon requires restart to restore** (env knob read per refresh tick; `readiness.py:50-67`) — document in Phase 2 drill runbook so green-restore steps aren't assumed instant.
```

I.e.: **restore = restart-required, NOT instant.** The knob (`ENSEMBLE_READINESS_FORCE_DEGRADED`) is read **per refresh tick** (`daemon/services/readiness.py:48-67`) — a one-way fail-safe that cannot false-green, but also cannot self-clear on a deployed daemon without a restart.

### Procedure

```bash
# P7.0 — §0 checklist + green baseline (row 1 of 5, timestamped):
date -u +%FT%TZ; curl -s -w '\n%{http_code}\n' localhost:7979/readyz | tee <ev>/p7-transcript.txt
# P7.1 — set the knob (drill-scoped edit, backed up — RESULTS §2 row (h) precedent):
cp ~/agents-ensemble-demo/.env ~/agents-ensemble-demo/.env.dr3-backup
echo 'ENSEMBLE_READINESS_FORCE_DEGRADED=1' >> ~/agents-ensemble-demo/.env
# P7.2 — restart to load it (manual restart procedure — stop-ensemble.sh + launcher):
bash scripts/stop-ensemble.sh ~/agents-ensemble-demo 7979
(cd ~/agents-ensemble-demo && nohup ./launcher.sh >> data/launcher.log 2>&1 &)
# P7.3 — red rows (2-3 of 5, timestamped):
date -u +%FT%TZ; curl -s -w '\n%{http_code}\n' localhost:7979/readyz   # 503 + forced reason
date -u +%FT%TZ; curl -s localhost:7979/livez                          # 200 (independence)
grep '\[Readiness\] degraded' ~/agents-ensemble-demo/data/launcher.log | tail -1
# P7.4 — clear the knob AND restart (restore is NOT instant — see verbatim note):
cp ~/agents-ensemble-demo/.env.dr3-backup ~/agents-ensemble-demo/.env
bash scripts/stop-ensemble.sh ~/agents-ensemble-demo 7979
(cd ~/agents-ensemble-demo && nohup ./launcher.sh >> data/launcher.log 2>&1 &)
# P7.5 — green row (4-5 of 5, timestamped):
date -u +%FT%TZ; curl -s -w '\n%{http_code}\n' localhost:7979/readyz   # 200, reasons: []
date -u +%FT%TZ; curl -s localhost:7979/livez                          # 200
diff ~/agents-ensemble-demo/.env ~/agents-ensemble-demo/.env.dr3-backup  # empty = knob gone
```

### Expected outputs

5-row timestamped transition (Phase-1 §1 P7 shape; precedent pacing 15:36:31 → 15:36:49 → 15:37:27):

| row | probe | expected |
|---|---|---|
| 1 | `/readyz` baseline | 200, `reasons: []` |
| 2 | `/readyz` post-knob+restart | **503** + forced reason |
| 3 | `/livez` same window | **200** (independence) |
| 4 | `/readyz` post-clear+**restart** | 200, `reasons: []` |
| 5 | `/livez` same window | 200 |

Plus one `[Readiness] degraded` log line while red, and a clean `.env` diff after restore.

### Evidence to capture

The 5-row timestamped transcript · `.env` set/clear diff (backup ↔ working copy) · the `[Readiness] degraded` log line · live pid checkpoint start/end.

### Restore of the drill

Knob removed (`.env` restored byte-identical from `.env.dr3-backup`; backup kept as evidence) · **restart performed** · `/readyz` 200 `reasons: []` asserted. Do not report green on a knob-cleared-but-not-restarted daemon — per the verbatim note above, that state is still red until the next restart.

---

## 5. DR-4 — pipeline drills (demo) — 4 legs + MANDATORY journal reset

**What it proves:** the P2.1 pipeline (stage/promote/gate/rollback/sweep) behaves per ADR-005/012 semantics on the real demo install. **Pass criterion (one line):** the 4 legs produce journal events exactly matching ADR-005/012 semantics (`commit` / `rollback`+quarantine+cooldown / cap-`halt` / `sweep_rollback`) with demo restored green after each leg and the post-DR-4 journal reset executed + verified (counters zeroed, halt cleared).

**⚠ Rollback-safety drill constraint (D-FA4.5).** Releases built from this repo derive **`rollback_safe=false`** — the migration set contains `DROP TABLE` (→ `contains_contract_phase=true`). DR-4's rollback legs (b)/(c) therefore stage drill releases with the documented **`ENSEMBLE_ROLLBACK_SAFE=1` author override at STAGE time** (`stage.sh`'s sanctioned author-call knob — P2.1 demo-evidence precedent: drill payloads are byte-identical across the drill set, so intra-set rollback is schema-neutral). **Record EVERY use of the override in the evidence.** Real releases from this repo keep the derived `false` until the migration set is delta-scoped.

### (a) Clean promote

```bash
VERSION=v0.10.6-dr4a ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo
VERSION=v0.10.6-dr4a bash scripts/upgrade/promote.sh demo; echo "exit=$?"   # expect 0
bash scripts/upgrade/status.sh demo    # current -> v0.10.6-dr4a; version smoke: OK
```

Expected: exit 0; journal history terminal **`commit`**; gates green within budgets (`/livez` ≤60s, `/readyz` ≤120s); 300s soak clean (re-probed every 30s); version verify pass.

### (b) Induced-failure auto-rollback

Deterministic gate-failure induction via `ENSEMBLE_READINESS_FORCE_DEGRADED=1` **on the TARGET env** — set in the demo `INSTALL_DIR/.env` (with `.env.dr4-backup` made first) **before** the promote's restart, so the newly-booted target daemon reads it per refresh tick and `/readyz` fails the gate.

> **⚠ HALT-TRAP LESSON — never leave the knob set.** The knob lives in the **shared** `INSTALL_DIR/.env`; the rollback path **re-gates the PREVIOUS release on the same `.env`** — a still-set knob fails the previous's re-gate too, and the leg ends in **halt-for-human instead of a clean rollback**. Clear the knob (restore from `.env.dr4-backup`) **the moment the target's gate failure appears in the promote transcript** — the repoint→restart window is the clearance window; the re-gated previous then boots green.

Sanctioned alternative (P2.1 demo-evidence precedent — avoids the knob hazard entirely): stage the target with `ENSEMBLE_BINARY_VERSION=0.99.0-gate-fail-drill`, a manifest `binary_version` the healthy daemon can never self-report — the D2/ADR-027 version-verify gate fails deterministically while `/livez`/`/readyz` stay green and nothing needs clearing. Either induction is acceptable; record which was used.

```bash
VERSION=v0.10.6-dr4b ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo
cp ~/agents-ensemble-demo/.env ~/agents-ensemble-demo/.env.dr4-backup
echo 'ENSEMBLE_READINESS_FORCE_DEGRADED=1' >> ~/agents-ensemble-demo/.env
VERSION=v0.10.6-dr4b bash scripts/upgrade/promote.sh demo &   # watch transcript…
# …gate failure logged ⇒ IMMEDIATELY run the clearance (halt-trap lesson):
cp ~/agents-ensemble-demo/.env.dr4-backup ~/agents-ensemble-demo/.env
wait; echo "exit=$?"                                            # expect 1 (rolled back)
```

Expected: promote exit **1** (rolled back, env recovered); journal **`rollback`** event + target **quarantined** + **cooldown 10min stamped** + counter +1; `current` → previous; `/readyz` of previous green post-flip; all inside the 10-min outer window.

### (c) Cap-exhaustion → halt-for-human

Repeat the (b) induction **3 times total, one FRESH version per leg** (rollbacks 1–3 in the 24h window) — `VERSION=v0.10.6-dr4b1`, then `-dr4b2`, then `-dr4b3`, each staged with the `ENSEMBLE_ROLLBACK_SAFE=1` override per the constraint above: each leg stages its own version, promotes it, lets it fail the gate, rolls it back — each rollback increments the cap counter and quarantines **that** version (re-promoting a rolled-back version is refused `quarantine`; it never reaches the cap leg). Sequencing note: respect cooldowns (~30 min wall-clock between rollback legs — the cooldown refuses entry-side promotes inside the window; the refusal is journaled with reason=cooldown; the boundary math itself is unit-proven in `release_journal_unit_test`). Rollback #3 arms the cap (journal `halt` event, exit 1). Then:

```bash
VERSION=v0.10.6-dr4c ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo  # 4th-leg target staged FIRST — not-staged refuses BEFORE the cap check (wrong reason)
for i in 1 2 3; do VERSION=v0.10.6-dr4c bash scripts/upgrade/promote.sh demo; echo "attempt $i exit=$?"; done
# expect: 3× exit 78 + "HALT-FOR-HUMAN: rollback cap 3/24h reached (count=3)"  (lib.sh entry check)
```

Expected: **4th entry refused** at preflight — repeated ×3 to prove halt stability: **all 3 refusals `rollback-cap-exceeded`** (exit 78), each journaled (P2.3 refusal journaling, commit `22f9a839`) as a `refusal` history event — shell token `reason=cap`; the tool layer surfaces the same condition as `rollback-cap-exceeded` (`upgrade_tools.py`); journal **`halt`** event (armed at rollback #3); SSE notification emitted + captured by a subscribed test client (D4/T8 alert); counters visible in the journal dump. Note D-FA4.2: cap enforcement is **entry-side only** — the rollback/sweep recovery path itself never refuses on cap. Operational recovery is the documented `halt_ack` (recorded journal action naming who/when; counters reset) — but **the drill's restore is the R3.2 reset below**, which is mandatory anyway.

### (d) Sweep-recovery of a stale `in_flight`

Induce an **orphaned flip**: run a promote against a version-lie target (the (b) alternative induction — no knob needed) and **SIGKILL `promote.sh` after the flip step** (abort-lane policy — P2.1 B4 batch, commit `edbadebf` "post-stop aborts leave the txn open for sweep recovery", stated in the `scripts/upgrade/promote.sh` header: the journal txn is left OPEN by design; `current` is left flipped). Then let the ADR-012 journal sweep converge it:

```bash
# … promote killed mid-window (post-flip); txn left in_flight, flipped:true
sleep 610   # exceed SWEEP_STALE_S (600s) — sweep fires only on STALE txns
bash scripts/stop-ensemble.sh ~/agents-ensemble-demo 7979
(cd ~/agents-ensemble-demo && nohup ./launcher.sh >> data/launcher.log 2>&1 &)
grep -i 'sweep' ~/agents-ensemble-demo/data/launcher.log | tail -5
bash scripts/upgrade/status.sh demo   # journal: sweep_rollback event; current repointed
```

Expected (decision table): in-flight **>600s & flipped** → sweep **executes rollback** (journal `sweep_rollback`; counts toward the cap + cooldown per ADR-024); in-flight **>600s & NOT flipped** → sweep **clears the txn** (no rollback — also acceptable to capture as the second branch); **≤600s (fresh)** → sweep leaves it + refuses (pipeline-busy). Capture whichever branch was induced; the flipped branch is the primary acceptance.

### MANDATORY post-DR-4 journal reset (R3.2) — REQUIRED before any clean cycles (T9)

The cap-exhaustion leg leaves **3 rollbacks + a halt-armed counter** in the demo journal's 24h window — deliberate drill state that would **poison the N-gate ledger**. Execute and verify the reset BEFORE T9 clean cycles begin; **the reset transcript is DR-4 evidence** (it is itself a tested restore path):

```bash
bash scripts/upgrade/status.sh demo          # 1. assert: in_flight null (post-sweep), pipeline lock free
mv ~/agents-ensemble-demo/releases/state.json \
   ~/agents-ensemble-demo/releases/state.json.archive-dr4-$(date -u +%Y%m%d-%H%M)   # 2. archive (evidence)
bash scripts/upgrade/promote.sh demo --help >/dev/null 2>&1 || true   # smoke: the script still runs post-archive (--help exits in arg-parsing, before any journal op — cannot touch the journal)
# 3. fresh init: the next pipeline op auto-inits the empty journal (lib.sh journal_init):
#    {"current":null,"previous":null,"in_flight":null,
#     "rollback_window_count":{"24h":0,"window_start":null},
#     "cooldown_until":null,"quarantined":[],"history":[]}
VERSION=v0.10.6-dr4reset ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh demo   # triggers init
bash scripts/upgrade/status.sh demo          # 4. VERIFY: counters 24h=0, cooldown null,
                                            #    quarantined [], in_flight null → halt CLEARED
```

Notes: journal `current`/`previous` re-anchor on the next **commit**; until then `status.sh` resolves the `current` symlink independently (readlink + manifest verify) — verify the daemon's serving version stayed put across the reset. The archive file is a plain file in `releases/` — invisible to the release inventory glob; keep it as audit evidence. **Verify counters zeroed + halt cleared BEFORE any clean cycles.**

### Evidence to capture (DR-4)

Per leg: command transcript with exit codes · journal `history` JSON (per-leg event: `commit` / `rollback`+quarantine+cooldown / `halt` / `sweep_rollback`) · launcher log excerpts · SSE alert capture (leg c) · **every `ENSEMBLE_ROLLBACK_SAFE` override use** · SIGKILL timestamp + orphaned-flip proof (leg d) · **the R3.2 reset transcript + post-reset journal dump** · live pid checkpoints per leg.

### Restore of the drill

R3.2 reset executed + verified (counters zeroed, halt cleared, archive kept) · demo serving the last-known-good release with `/readyz` 200 `reasons: []` · `.env` byte-identical to its pre-drill copy (no knob left behind) · no stray `.staging.*` / lock dirs (status.sh labels any; clear via the documented stale-break if present).

---

## 6. DR-5 — Ari-driven drills (demo, sandbox rehearsal first)

**What it proves:** the P2.2 agent-facing tools (`system_restart`, `system_upgrade`, `release_info`, `upgrade_status`) work end-to-end with their gates and refusal paths, and Ari's reports match journal ground truth. **Pass criterion (one line):** `system_restart` + `system_upgrade` complete e2e via Ari's tools on demo with both refusal paths asserted (fake-confirmation, live-target) and Ari's reported state matching the journal field-for-field.

### Procedure (rehearse on sandbox first — D7 row `sandbox → demo`)

1. **`system_restart` e2e (demo-resident Ari):** tool call → graceful stop (SIGTERM path, never raw kill — ADR-016) → launcher respawn → `/livez` ≤60s → tool result delivered post-restart (sequencing verified: result is the structured terminal state, not an intermediate).
2. **`system_upgrade` e2e:** first call `dry_run` (default true, ADR-022) → plan output (target, version, rollback safety, gates) → confirming call `dry_run: false` → arm → pipeline → gates → commit; `upgrade_status` polls report the journal state-machine position (staged → flipped → gating → soaking → committed).
3. **Fake-confirmation refusal:** a turn attempting `user_confirmed: true` **without** the genuine HUMAN-origin marker + action-binding nonce → **refused** (3-factor LIVE gate, D-FA3.1; refusal tokens incl. `nonce-instance-mismatch` / `nonce-action-mismatch`; a fabricated param must NOT unlock).
4. **Live-target refusal:** `target_env=live` from a demo-resident Ari → **refused** (`env-self-match`, D-FA2.3/D-FA2.4 — self-env resolves from the staged `ENSEMBLE_SELF_ENV` marker; marker absent → actor tools refuse fail-closed). Live `system_restart` is refused outright this initiative (ADR-016/A2).
5. **Report↔journal parity:** diff Ari's `release_info` / `upgrade_status` output against `bash scripts/upgrade/status.sh demo` journal dump — version, counters, `in_flight`, quarantine must match field-for-field.

**Restart-semantics accounting note (design constraint on this drill — from `test-strategy.md` §3):** an **in-flight tool call at stop is LOST** (turn resumes from the node boundary); tasks stay `PROCESSING` on crash (not `FAILED`); `StaleTaskRecovery` sweeps stale `RUNNING` >15min; MessageQueue is EPHEMERAL (`clear_all(preserve_in_flight=True)` at startup); ReportDeliveryRecovery is periodic-only (300s interval / 10-min age bound / batch 100, **no boot sweep**). **Therefore: never assert instant delivery of anything queued pre-stop — assert delivery within the recovery window (worst case: next 300s recovery tick + 10-min age bound).** The known Task↔JobItem reconciliation gap is pre-existing and OUT of P2 scope — if tripped, document, do not fix.

### Expected outputs

- `system_restart` transcript: stop→respawn→`/livez` ≤60s, and the tool result is the **structured terminal state delivered post-restart** — never an intermediate.
- `system_upgrade` transcript: `dry_run` plan (target, version, rollback safety, gates) → confirming call **arms** the run (deferred/armed banner + **run_id**) → `upgrade_status` polls by run_id through the journal phases (staged → flipped → gating → soaking → committed) → terminal state.
- Refusal paths (exact tokens): fake-confirmation → `user-confirmation-missing` / `nonce-instance-mismatch` / `nonce-action-mismatch` (a fabricated param must NOT unlock); live-target → `env-self-match`.
- Parity check: Ari's `release_info` / `upgrade_status` fields (version, counters, `in_flight`, quarantine) match the `status.sh demo` journal dump **field-for-field — zero diffs**.

### Evidence to capture

Tool transcripts (restart + upgrade + both refusals) · journal events per step · the parity diff (Ari report vs `status.sh` dump) · recovery-window delivery timestamps (restart semantics above) · live pid checkpoints.

### Restore of the drill

Demo green assertion (`/readyz` 200 `reasons: []`) · no `pending_op` left in the journal (`in_flight: null`) · sandbox (if used) torn down per §1.

---

## 7. Cumulative N-cycle ledger (human-copy table)

The gate measurement for S3 → live eligibility (ADR-021, N=3 user-ruled). **The machine-checkable source of truth is the demo journal (`releases/state.json`) + the ledger checker (`scripts/upgrade/ledger_check.py` + pack `test/packs/drill_ledger_unit_test.sh` — the P2.3 B2 batch); this table is the human-auditable copy, DERIVED from checker output — when they disagree, the journal wins.** Per-cycle RESULTS files: `.agents/tester/RESULTS/2026-MM-DD-selfrestart-phase2-clean-cycle-{n}-{env}.md`.

**Cycle verdicts are judged against the CANONICAL five clauses of `test-strategy.md` §4.1** (clause 1 ari-driven upgrade cycle; clause 2 restart cycle clean; clause 3 no readiness degradation outside drills; clause 4 no unintended work loss; clause 5 zero live contact). A cycle is clean iff all five pass. **Staleness:** any release/manifest change mid-count resets the count to 0 — the N gate must be satisfied by cycles all targeting the SAME release version. Failed cycles do NOT auto-reset the count; record them with cause.

| Cycle # | Date | Version | Journal txn id | Verdict (§4.1 clauses 1–5) | Evidence link |
|---|---|---|---|---|---|
| *(none recorded yet — table populated from `drill_ledger_unit_test` checker output as cycles complete)* | | | | | |

**Gate rule (see §9 for the hard block):** promotion out of the demo rung requires this ledger showing **3 consecutive clean cycles with zero clause violations** — **AND** F2 closed. F2-open ⇒ hard-blocked regardless of cycle count.

---

## 8. LIVE PROMOTION — USER-GATED DESIGN (D3 — NEVER EXECUTED BY THIS INITIATIVE)

Everything in this section is **designed, documented, rehearsed-on-demo — and executed ONLY by the user** (`promotion-ladder.md` §5 U1–U6). Agents never set the guard variable; automation stops at demo **permanently** (ADR-017: S4–S6 have no automation path). Commands below show **guard VARIABLE names only** (`ENSEMBLE_UPGRADE_LIVE=1`); the user runs them with the live install's own resolved values. §9's hard block applies **before any of this is even eligible**.

### 8.1 Live staged-mode migration (first `stage.sh live` — converts the live dir from flat/legacy `.bak`s to staged mode) — U1

- **Preconditions:** §9 gate satisfied (3-clean-cycle ledger + F2 CLOSED) · user has taken their own backup of the live dir (its content is out of bounds to us, not to the user) · live daemon serving normally; journal on live absent (not yet initialized).
- **User-executed command (guard-bearing):** `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/stage.sh live --version <v>` (guard variable shown by name; the user supplies the values).
- **Expected journal events:** first pipeline op on live initializes `releases/state.json` (empty journal: counters 0, no quarantine) + the stage txn; `releases/<v>/` assembled with manifest (`rollback_safe` per D-FA4.5 derivation or the user's explicit author override — the override use is recorded in the journal).
- **Verification probes (user-run):** `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/status.sh live --verify` → staged release listed, integrity clean, `current` untouched (stage never flips).
- **Abort path:** stage is non-destructive to `current` — abort = leave the staged release un-promoted and restore any pre-staged flat-layout backups the user made; nothing has flipped. If the journal initializes but the migration is abandoned, halt-for-human posture: do not promote; the user decides rollback of the layout change from their backup.

### 8.2 Live promote — U1 (verification U2; rollback U3)

- **Preconditions:** 8.1 done · staged release integrity-verified (`status.sh live --verify` clean) · manifest `rollback_safe` checked by the user against the live DB's real migration state (ADR-020 interim rule: two enforcement layers, one rule; releases that drop columns are NEVER rollback targets — halt-for-human instead).
- **User-executed command (guard-bearing):** `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/promote.sh live VERSION=<v>`.
- **Expected journal events:** `in_flight{kind:promote}` → `flipped:true` → gates (`/livez` ≤60s, `/readyz` ≤120s, version verify, 300s soak) → **`commit`** (current/previous updated, retention) — or on gate failure: auto-**`rollback`** + quarantine + cooldown + counter (cap 3/24h; at cap → `halt` for human).
- **Verification probes (user-run; any agent involvement is read-only GETs only, explicitly approved per action — U2):** `GET /livez` (200, version == manifest `binary_version` — ADR-027), `GET /readyz` (200, `reasons: []`), `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/status.sh live`.
- **Abort path:** auto-rollback is built-in on gate failure. Manual: `ENSEMBLE_UPGRADE_LIVE=1 bash scripts/upgrade/rollback.sh live` (manifest `rollback_safe` gate; cap + cooldown apply). If the rollback target is itself bad/quarantined → **halt-for-human**; recovery = **ADR-028 flip-forward**: the user picks a known-good version and promotes it through the standard gate (never automatic).

### 8.3 Live restart — stays MANUAL per the P2.2 ruling

Live `system_restart` via tools is **refused outright** this initiative (ADR-016/A2; U5 — live `system_restart` is refusal-only, the 3-factor gate applies to `system_upgrade` only). The manual procedure — both scripts already shipped — is what the user runs:

- **Command shape:** `bash scripts/stop-ensemble.sh ~/agents-ensemble <live-port> && (cd ~/agents-ensemble && nohup ./launcher.sh >> data/launcher.log 2>&1 &)` — port value resolved by the user from the live install config (never from this file).
- **Expected:** clean SIGTERM-bounded stop chain (SINGLE-TERM ownership) → launcher respawn → `/livez` ≤60s; journal `restart` event if invoked via the executor path, else launcher log evidence only.
- **Verification probes:** `GET /livez`, `GET /readyz` (user-run or per-action approved agent GET — U2).
- **Abort path:** launcher burst-abort posture (exit-1 class, >5 crashes/10min) → the daemon stays down by design; watchdog-watcher notifies (ADR-025(b), if the user enabled U6) → human decides. Halt-for-human conditions (rollback cap) per §2 of the ladder.

**U-marker map (promotion-ladder.md §5):** U1 live promote/STAGE confirmations (journal `user_confirmed_by/at`) · U2 post-promote verification (read-only, per-action approved) · U3 live rollback (per-action) · U4 live reads by agents (**fenced** to the loopback-API-auth/F2 follow-up workstream — see `decisions.md` P2.3 Gate Rulings & Fences) · U5 tool-path live gate (3-factor, refusal-only for restart) · U6 watchdog-watcher on live (user-approved install/enable).

---

## 9. F2 HARD-BLOCK GATE — live promotion eligibility (must-read before §8 is even considered)

**Gate rule — live promotion eligibility requires BOTH:**

1. **Ledger:** 3 consecutive clean demo cycles with **zero** `test-strategy.md` §4.1 clause violations (§7 table, same release version, no staleness reset), **AND**
2. **F2 CLOSED:** the **unauthenticated loopback API user-origin forge lane** closed or the local API authenticated. The lane (critical note e5a83653, **HIGH**): `POST /jobs` accepts `body.source` **verbatim** (`daemon/routers/jobs_crud.py:275-278`); `POST /messages` stamps `source="api"` (`daemon/routers/messages.py:391`); the `USER_ORIGIN_SOURCES` whitelist cannot distinguish a genuine web-UI human from a localhost forger (`daemon/tools/upgrade_journal.py:714-717` lane; `decisions.md` OBS residual §4.2(a) — single-host trust model, closes only with an auth boundary on the local API).

**F2-open ⇒ the gate is HARD-BLOCKED regardless of cycle count.** No number of clean cycles substitutes for the forge lane being closed — a forged user-origin on live would bypass the very gate the cycles exist to certify.

**And even with F2 closed:** the live rung remains **USER-EXECUTED** — automation stops at demo **permanently** (ADR-017 env-target model; `promotion-ladder.md` S4–S6 are USER rows; §8 above is a design, never an agent procedure).

---

*Runbook maintenance (R3.5): this file is versioned in-repo and references scripts by name + the commit at its header; every drill execution records a script-version checkpoint line in its evidence. When `scripts/upgrade/` changes, re-verify §2–§5 command shapes against the scripts' own usage headers before the next drill.*
