# systemd Adoption Runbook — graduating an Ensemble install to SERVICE MODE

- **Version:** 2.0 @ `feature/promote-quiesce-adoption-protocol` P5 (2026-09-29)
- **Canonical path:** `docs/runbooks/systemd-adoption.md` — this file is the
  canonical home of the **OS×deployment matrix (A2)** and the **feature-gap
  table (A4)** referenced by the supervision twins
  (`scripts/upgrade/lib.sh` §supervision + `daemon/tools/upgrade_journal.py`
  §supervision — both forward-reference THIS path; keep the five A2 rows in
  sync with those docstrings).
- **Commission:** supervision-detection P4 of 5 (+ Amendment #1 deltas
  A2/A3/A4) at HEAD `1a1dd5d4`; P5 (this rewrite) layered on top via the
  **3-layer adoption protocol** (commission v0.16.6 component 2, 2026-09-29)
  after incident `r-20260929-170301-0cb2` proved the pre-P5 procedure
  structurally racy.
- **Companion docs:** `docs/runbooks/upgrade-drills.md` (drill legs incl. the
  new promote-under-real-unit leg §5(e)) · `docs/incidents/2026-09-28-promote-cgroup-teardown.md`
  (the r-f82e incident + the hand-provisioned `ensemble-live.service`
  background) · `docs/incidents/2026-09-29-promote-halt-forced-rollback.md`
  (the r-20260929-170301 incident; the motivating case for the 3-layer
  protocol below) · the A1 declared×verified outcome map (in-repo: the
  twins' section comments).

**Adoption is OPT-IN, EXPLICIT OPERATOR ACTION ONLY.** Nothing in the
pipeline ever adopts a host automatically; script mode remains a first-class,
conforming topology (A1 row `auto×SCRIPT_NOHUP`). This runbook exists so an
operator can graduate an existing script-mode install to service mode
WITHOUT reinstallation, in one documented procedure.

**Never run adoption against an install you did not intend to migrate.**
The tool refuses a running install (step a), a non-systemd host (step b),
a host without the polkit rule (step c), and — since v0.16.6 component 2 —
an UNSETTLED pipeline state (step pre / Layer i) or a held pipeline lock
(step pre / Layer ii). See §3.

---

## 1. A2 — OS×deployment matrix (canonical)

The two named topologies (A1/Amendment #1) crossed with the OS families the
repo actually runs on. **`ensemble-vm` live is the third row TODAY**
(2026-09-29): systemd present, install not adopted — declared `auto` (the
default), verified `SCRIPT_NOHUP` → **conforming** per the A1 map. Adoption
moves an install from row 3 to row 4.

| # | Deployment × OS | What works TODAY | What this commission (P1–P4) ADDS | Tests covering it |
|---|---|---|---|---|
| 1 | **script × macOS** | The byte-identical no-systemd arm: classifier short-circuits before ANY `/proc` / `/run` read (P1 guard discipline — `supervision_classify` non-Linux gate; python twin `supervision_detect` same). Launcher lineage self-respawns; launchd plist (`scripts/ensemble-prod.plist`) owns boot-restart where installed. | **Nothing on this row — by design.** `adopt-unit.sh` REFUSES non-Linux (step b). A1 naming only (`SCRIPT_NOHUP` = conforming). | `tests/test_supervision_classify.sh` §5 (Darwin uname stub: ZERO /proc reads, auto + explicit-unit arms); `tests/test_supervision_stop_handback.sh` A′4 (BSD degrade-loud stop fallback) + C10 (hand-back degrades LOUD to nohup); `tests/test_adopt_unit.sh` (b1) non-Linux refusal; `tests/unit/tools/test_supervision_python.py` 8d (platform guard cells). |
| 2 | **script × ubuntu (no systemd)** | The SAME byte-identical arm: `/run/systemd/system` absent → classifier returns `SCRIPT_NOHUP` without further reads; launcher backoff loop is the whole supervision story. | **Nothing on this row.** `adopt-unit.sh` refuses (step b host guard — no systemd to adopt into). | `tests/test_supervision_classify.sh` §1a (ladder-top zero-read cells) + the NAMED FENCE "no /run/systemd/system" (the auto-chain's /run-absent early return IS this row's classifier shape — the suite exercises it live on hosts without the marker, fences it with the name on hosts that have it); `tests/test_supervision_stop_handback.sh` A′1–A′3 (byte-identical pid-path arms, zero systemctl calls); `tests/test_adopt_unit.sh` (b2) ADOPT_SYSTEMD_RUN_DIR refusal. |
| 3 | **script × ubuntu (systemd present, NOT adopted) — TODAY'S LIVE TOPOLOGY** | systemd exists but the install runs as a direct/nohup lineage; cgroup leaf = `session-*`/user-slice → verified `SCRIPT_NOHUP`; declared `auto` → **conforming** (A1). This is ensemble-vm live (port = live port) TODAY. | **A documented, one-command graduation path** (this runbook + `adopt-unit.sh`), plus the P3 in-promote self-heal: with a unit configured (`ENSEMBLE_RESTART_UNIT`), the next promote's hand-back moves the install INTO the unit (scope→unit / script→unit). | `tests/test_supervision_classify.sh` §2 (§0 allowlist-strip pin: INVOCATION_ID absent + upgrade-scope cgroup → SCOPE_SURVIVOR, never SCRIPT) + §1b scope-leaf cells + §3h; `tests/test_supervision_stop_handback.sh` C6 (scope-no-unit → nohup byte-identical + WARN, zero systemctl) + C5 (scope→unit self-heal + `supervision_handback` journal event, outcome=degraded); `tests/test_supervision_twins.sh` (ladder agreement incl. the scope leaf); `tests/test_supervision_e2e.sh` E3 (real scope-survivor heal under real systemd). |
| 4 | **service × ubuntu (systemd substrate) — THE NEW PATH** | Before P4: reachable ONLY by hand-provisioning a unit (the `ensemble-live.service` precedent on ensemble-vm — out-of-repo, incident doc §5). | **The adoption lane (P4):** static base template (`scripts/systemd/ensemble-daemon.service`) + `scripts/upgrade/adopt-unit.sh` generation from CURRENT install config + verified handover (polkit-gated, fail-loud, rollback documented below). Declared `unit` × verified `UNIT_MANAGED` = conforming; P3 unit hand-back keeps the unit across promotes (NO nohup fallback — Amendment #1). **Layered on top of P4 (commission v0.16.6 component 2, 2026-09-29): the 3-layer adoption protocol (Layer i settle-check + Layer ii mutex + Layer iii this runbook) closes the racy-precondition class proven by incident `r-20260929-170301-0cb2`.** | `tests/test_adopt_unit.sh` (all arms: refusals a/b/c, naming table, DRY_RUN unit content + %h rule + zero-env contract + zero mutations); `tests/test_supervision_stop_handback.sh` A (unit-path stop incl. b″ respawn sim + escalation + fail-loud) + C1–C4 (hand-back happy/stale/timeout/start-fail — NO nohup fallback) + C7–C9 (comp7 a′); `tests/test_supervision_journal.sh` J3 (halt-B4 stamped open txn); `tests/test_supervision_e2e.sh` E1 (boot under a real service: INVOCATION_ID + livez + UNIT_MANAGED boot advisory) + E2 (full real promote under the unit: classify → unit-stop → hand-back NEW-MainPID verify → commit) — the DR-4 leg (e) drill, `upgrade-drills.md` §5(e). **New for v0.16.6 component 2**: `tests/test_release_journal.sh` §15 (pipeline_settled reason-token matrix) + §16 (Layer-ii mutex: stop-ensemble.sh acquire + PIPELINE_LOCK_HELD_BY_CALLER escape + --force pre-parse all positions) + §17 (adoption marker preflight + clear path) — see §8 below. **`tests/test_adopt_unit.sh` is UNCHANGED for v0.16.6 component 2** — it pins the MUTATION-mode refusal surface only (a/b/c + naming + DRY_RUN), which is the same surface the 3-layer protocol inherits (no new mutation-mode lock/marker integration). MUTATION-mode lock/marker integration coverage lives in the NAMED FENCE `tests/test_release_journal.sh` §19 — a real-systemd host is required to exercise the lock+marker acquisition under the actual unit lifecycle, and the suite emits a `SKIP(no-systemd-host)` when that host is absent (no faked pass). |
| 5 | **service × macOS (launchd) — documented FUTURE scope** | `scripts/ensemble-prod.plist` (daemon) + `scripts/watchdog-watcher.plist` (watchdog) exist as launchd substrates. | **Nothing — FUTURE scope by decision.** The A1/A2 vocabulary (declared×verified) is substrate-neutral; a launchd adoption tool would slot here. Not commissioned. | Named future-scope fence: `tests/test_supervision_e2e.sh` FENCE "no /run/systemd/system (non-systemd host)" (all legs counted-SKIP, never FAIL) + `tests/test_supervision_stop_handback.sh` C10 (a UNIT_MANAGED classification on a non-Linux host degrades LOUD to nohup — the safe intersection while launchd is unbuilt). |

*(Test ids filled by P5 (2026-09-29): every row is covered by stub-driven
pins in the six new suites, or by a NAMED fence that counts a SKIP (never
a FAIL) when the row's substrate is absent on the host. Real-seat E2E:
`tests/test_supervision_e2e.sh` (user-manager lane via the `SYSTEMCTL_BIN`
seam; the ensemble-vm system-manager lane additionally evidenced by the
DR-4 §5(e) drill procedure).)*

---

## 2. A4 — feature-gap table: script mode vs service mode

**Script mode is a KNOWN, DOCUMENTED state — not a deprecated one.** It
closes none of the rows below "wrongly"; each row states what script mode
provides TODAY (evidence-annotated) and what service mode ADDS on top.

| Capability | Script mode TODAY (evidence) | Service mode ADDS |
|---|---|---|
| **Auto-restart on crash (`Restart=`)** | Launcher's own backoff loop: crash 10s→300s ×2, tempfail 5s→60s, burst abort >5/10min (`launcher.sh` `CRASH_BACKOFF_*` / `BUDGET_*` tunables; ADR-011). Revival authority = the launcher process ITSELF — if the launcher dies (burst-abort exit 1), nothing revives it but a human. | `Restart=on-failure` + `RestartSec=10` at the UNIT level: the launcher's burst-abort (exit 1 = failure) gets ONE paced OS-level retry cycle, and systemd's own start-rate-limit (default `StartLimitBurst=5`/`StartLimitIntervalSec=10s`) converts a true crash-loop into a terminal `failed` unit (halt-for-human) instead of an unwitnessed dead lineage. Exit 78 (`RestartPreventExitStatus`) is never restarted — the refuse contract holds. |
| **Structured logs (journald)** | File logs: launcher `_log` → stderr → `data/launcher.log` / `data/launcher.err.log` (deploy paths); daemon log under `data/logs/`. Rotation/retention is the operator's problem; no structured metadata (unit, invocation id, priority). | `StandardOutput=journal` / `StandardError=journal`: every line lands in the journal tagged with the unit — `journalctl -u <unit> -f`, priority filtering, `journalctl --boot` scoping, and journald's own retention. File logs continue to exist (the launcher still writes them); journald is ADDITIVE capture. |
| **Boot-on-host-restart recovery** | **Manual/absent on Linux.** A nohup lineage dies with the host; nothing re-starts it at boot (no installer support, no systemd presence). (On macOS the launchd plist covers this where installed.) | `WantedBy=multi-user.target` + `systemctl enable`: the daemon comes back at host boot, before login, owned by PID 1's supervisor chain. |
| **Supervision / cgroup resource accounting** | Absent as a boundary: the nohup'd launcher inherits the SPAWNER's cgroup (the r-f82e survivorship family — incident doc; `restart_via_launcher` comp7 comment: "the nohup launcher inherits the launcher's cgroup"). No per-install cgroup → no `systemd-cgtop` accounting, no unit-scoped resource limits, unpredictable teardown blast radius. | The unit IS the cgroup boundary: per-install accounting (`systemd-cgtop`/`systemd-cgls`), optional `CPU=/Memory=` limits (drop-in, not in the base template), and `KillMode=mixed` makes teardown bounded + predictable (TERM to launcher only — the SINGLE-TERM contract — then SIGKILL to stragglers after `TimeoutStopSec=90`). |
| **Watchdog revival unpinning** | The watchdog (`scripts/watchdog-watcher.sh`) is ALERT-ONLY (journal terminal classes `halt` / `sweep_rollback`; burst-abort watch) — it never revives anything. Revival is pinned to the launcher lineage: whatever session/scope spawned the launcher owns the revival semantics, and a dead lineage stays dead. | Revival is UNPINNED from any spawning session: systemd revives the unit regardless of who started it or whether the original session/scope still exists. The watchdog keeps its alert role (terminal classes unchanged); its `INSTALL_DIR` explicit-only contract is orthogonal to who supervises the daemon. (An in-process `WatchdogSec`/sd_notify remains DEFERRED — see §Deferred.) |
| **Mutual exclusion with concurrent promote/stop** (added v0.16.6 component 2) | NO mutex — the pipeline's only lock is the mkdir-based `releases/rollback.lock.d` held by promote/stage/rollback through their mutate window; `stop-ensemble.sh` and `adopt-unit.sh` took no lock and read no pipeline state. A concurrent adopt over a live flip was structurally possible (incident `r-20260929-170301-0cb2`). | `stop-ensemble.sh` and `adopt-unit.sh` now acquire the SAME mkdir-lock (Layer ii), with `PIPELINE_LOCK_HELD_BY_CALLER=1` escape for the in-pipeline invocation path (`lib.sh stop_via_stop_script`). The lock + Layer-i settle-check together eliminate the racy-precondition class. |

---

## 3. The adoption procedure (single, one-time)

**Goal:** graduate `<INSTALL_DIR>` from script mode to service mode without
reinstalling. Prereqs: Linux host with systemd; the install STOPPED; the
polkit rule of §4 installed; the upgrade pipeline SETTLED (Layer i); root
(sudo) for the unit-file install step (or a writable `UNIT_DIR` override
on test hosts).

0. **Plan the maintenance window** — the daemon is down from step 3 until the
   unit starts (typically < 1 min). Verify the install is at a COMMITTED
   `current` (a settle-check pre-condition; §3a); the unit's halt+restart
   path operates on the committed baseline, never on a half-staged flip.
1. **(One time, root) Install the polkit rule** — §4 below.
2. **Stop the install (script-mode stop, ownership-scoped; Layer i+ii gated):**
   ```bash
   bash scripts/stop-ensemble.sh <INSTALL_DIR>
   ```
   The tool performs the **Layer-i settle-check** (refuses 78 with a
   machine-readable reason if the pipeline is not in a quiescent state —
   journal/symlink drift, open txn, non-null `pending_op`, lock held) and
   acquires the **Layer-ii pipeline lock** before any TERM. The lock is
   released on every exit path via the EXIT trap; promote/rollback/stage
   detect the held lock via the same `lock_acquire` helper. If the
   pipeline is mid-mutation (a promote in flight, an adopt mid-sequence),
   the settle-check refuses — wait for it to settle, or pass `--force`
   for operator emergencies (documented below).
3. **Preview (recommended first run):**
   ```bash
   DRY_RUN=1 bash scripts/upgrade/adopt-unit.sh <INSTALL_DIR>
   ```
   Runs every refusal check except the Layer-ii lock acquire (pre/i/a/
   b/c — DRY_RUN exits before the lock call so the preview stays
   side-effect-free), reads config, generates the unit, and prints the
   sequence + the unit (zero-env contract — no env values to mask).
   Zero mutations; no
   marker written; no lock acquired.
4. **Adopt:**
   ```bash
   sudo bash scripts/upgrade/adopt-unit.sh <INSTALL_DIR>
   ```
   (Under sudo the unit's `User=` still defaults to the login user via
   `SUDO_USER`.) The tool, in order, fail-loud at every step:
   - **(pre Layer i)** refuses if the upgrade pipeline is not settled
     (`pipeline_settled` — journal current unset, journal/symlink drift,
     open txn, non-null `pending_op`, lock held). Single machine-readable
     `reason=<token>: …` line on stdout/stderr; exit 78. **NEVER**
     bypassed by `--force` (adoption refuses on the same surface as the
     pipeline lock — the lock IS the live pipeline).
   - **(pre Layer ii)** acquires the same mkdir-lock the pipeline
     scripts use (`lock_acquire`); release on every exit path via EXIT
     trap.
   - **(a)** refuses if ANY owned pid is alive (DUAL_FIGHT — two masters;
     reuse of the anchored pid-discovery tiers via
     `lib.sh _supervision_owned_pids`);
   - **(b)** refuses non-Linux / no `/run/systemd/system` / no systemctl;
   - **(c)** refuses (🔴) without the polkit rule of §4;
   - **(d)** generates the instance unit from the static base template
     (`scripts/systemd/ensemble-daemon.service`) reading ONLY `PORT` from
     `<INSTALL_DIR>/.env` (the `_resolve_wait_s` way — staged file, never
     ambient env; `User` defaults to the adopting user; `ExecStart`
     = the EXISTING `<INSTALL_DIR>/launcher.sh` — the launcher's exit map
     and backoff stay in the loop; the unit adds NO second supervisor).
     **ZERO-ENV CONTRACT (unit-secret-exposure, 2026-10-23):** the generated
     unit carries NO `.env`-derived `Environment=` lines — runtime env is
     supplied entirely by `launcher.sh` (see the precedence contract below);
     systemd exposes `Environment=` to any local uid via D-Bus, so minted
     secrets in the unit were pure exposure with zero runtime benefit.
     then: **writes the adoption-in-progress marker** (Layer ii — see §3b) →
     install unit (0600 — hygiene; the unit carries no secrets) → `daemon-reload` →
     stage `ENSEMBLE_RESTART_UNIT=<unit>` into `.env` (ADDITIVE — only that
     key is touched) → `systemctl enable --now` → **verify**: `/livez` on
     the staged port + the boot journal's `supervision_boot` advisory shows
     `state=UNIT_MANAGED unit=<unit>` + the declared×verified outcome map
     lands on `conforming` → **clears the marker** (Layer ii closure) →
     exits 0.
     Any failure = loud exit + a printed partial-state ledger (this tool
     never auto-rolls-back; §5 is the manual procedure). The marker is
     cleared ONLY on verify success — a step-d failure leaves the marker
     present so a concurrent promote refuses; see §3b for stale-recovery.

**PRECONDITION (stage ownership — unit-secret-exposure, 2026-10-24):** any
pipeline stage against this install (`scripts/upgrade/stage.sh`) must run as
the INSTALL OWNER — the account named by the unit's `User=` — because the
stage's `.env` marker rewrite replaces the file's inode and re-owns it to
the staging uid, so a cross-uid stage leaves a 0600 `.env` the unit's
`User=` cannot read (launcher WARN → no `POSTGRES_*` → boot-block) — a
DOCUMENTED precondition, with no mechanical check enforcing it.

5. **Confirm:**
   ```bash
   systemctl status <unit>            # active; MainPID = the launcher
   journalctl -u <unit> -f            # unit journal (structured capture)
   bash scripts/upgrade/status.sh <target>   # pipeline view unchanged
   ```
   The NEXT promote on this install performs the P3 **unit hand-back**
   (`reset-failed → is-active → start → verify NEW MainPID`) — no nohup
   fallback ever re-creates the survivor lineage (Amendment #1).

**Env sourcing & precedence — the 4-rung contract (zero-env, unit-secret-exposure 2026-10-23).**
The generated unit carries NO `.env`-derived `Environment=` lines (zero-env
contract — the unit rung is EMPTY BY DESIGN; only the static
`Environment=MALLOC_ARENA_MAX=2` directive remains). Effective precedence,
highest wins:

1. `.env` — `launcher.sh load_env_file` (per-key export, LAST-wins)
2. unit `Environment=` — now EMPTY by design (zero-env contract; only the
   static MALLOC_ARENA_MAX directive)
3. `systemctl set-environment` (inherited) — vacuous in-repo (zero
   producers/consumers)
4. code defaults — launcher `: "${MALLOC_ARENA_MAX:=2}"` (`launcher.sh:1658-1659`);
   `config.yaml` defaults

`load_env_file` is called **ONCE per launcher process** (`launcher.sh:1649`,
env-first in `main()`, OUTSIDE `run_loop`): child restarts inside the loop
re-inherit the exports without re-reading `.env`. (Precision: `run_app.py`'s
only-unset `.env` load is **frozen-binary-only** (`sys.frozen` gate) and
quote-unaware — the structural precedence guarantee lives solely in the
launcher path.)

**Missing `.env` after adoption — INTENDED fail-fast (with its conditions).**
With the unit no longer carrying minted env, a missing/unreadable `.env`
means the launcher continues with inherited env (loud WARN
`env file not found`) → no `POSTGRES_*` → the daemon's boot preflight
blocks. Exact behavior is CONDITIONAL on `ensemble.json` (the installed
norm — it declares postgres, persisted at first boot): the boot-block exits
**75** (PG unreachable). Exit taxonomy: **75** = PG unreachable; **78** =
auth refused AND auth-config misconfig (78 → unit FAILED via
`RestartPreventExitStatus=78`, visible in `systemctl status`); a 75-loop
keeps the unit **"active"** while the launcher retries internally —
**watch the journal, not unit state** for 75-loops. There is **no
"config-absent" refusal code**: absent/corrupt `ensemble.json` silently
auto-derives + persists a SQLITE config — on a fresh-dir or corrupt-config
install a missing `.env` yields a silent SQLITE boot on an empty DB (known
residual; candidate for a future refusal-hardening commission).
Restart-storm: NON-ISSUE — tempfail backoff 5s start ×2, cap 60s
(`launcher.sh:55-56`, ladder `:1349-1370`, arm `:1569-1585`), so a 75-loop
costs ≈2 journal lines per ≤60s steady state.

**Unit naming rule** (the polkit pattern `^ensemble-[0-9A-Za-z@._-]+\.service$`):
`ensemble-<slug>.service` where `slug` = `basename(INSTALL_DIR)` minus any
leading `agents-`, minus any leading `ensemble-` (no double prefix),
sanitized to `[0-9A-Za-z@._-]`; empty or bare `ensemble` → `main`.
`~/agents-ensemble` → `ensemble-main.service` (deliberately NOT colliding
with the hand-provisioned `ensemble-live.service` on ensemble-vm);
`~/agents-ensemble-demo` → `ensemble-demo.service`. An explicit 2nd argument
(or `ENSEMBLE_UNIT_NAME`) overrides, validated against the same pattern.

**Test seams** (P5 drives these; also usable by operators on fixture hosts):
`SYSTEMCTL_BIN` (stub the systemctl interactions), `UNIT_DIR` (unit-file
destination), `POLKIT_RULES_DIR` (the §(c) scan), `ADOPT_SYSTEMD_RUN_DIR`
(host guard), `ADOPT_USER`, `ADOPT_LIVEZ_BUDGET_S`, `DRY_RUN=1`
(preview-only mode — print the sequence + the generated unit; the unit
carries no env values, zero mutations), plus `curl`/`uname` PATH-stubs for
the verify/host arms. The P4 smoke used all of these against fixture
dirs — no live/demo contact, no real daemon-reload.

---

## 3a. The "current == X + health" anti-pattern (FORBIDDEN)

**This pattern is structurally racy and is forbidden in any adoption
automation.** A systemd-adoption mission gated on `current == v0.16.5 +
healthy` passed mid-flight during the incident `r-20260929-170301-0cb2`
(2026-09-29), because the `current` symlink flips at `promote.sh:258`
BEFORE all gates + soak complete; the check passed and the adoption
raced a live flip. Forensics:
- Pre-flight said `current == v0.16.5 + livez 200`.
- The promote was mid-soak; `current` had ALREADY flipped from a prior
  stage that left it advanced; the env self-marker was correct.
- The adoption tool took no lock and read no pipeline state — no
  evidence the promote was mid-flight.
- Result: adoption halted partway, no harm to the env, but the
  condition class is a real hazard for any automation that adopts the
  same shape.

The CORRECT gate is the **3-layer protocol**:

1. **Layer i — settle-check**: journal `current` set AND on-disk symlink
   matches journal `current` AND no in_flight txn AND no `pending_op` AND
   lock free. Adoption/stop MUST call `pipeline_settled` (lib.sh) before
   any mutating action.
2. **Layer ii — mutual exclusion**: adoption/stop MUST acquire the same
   `releases/rollback.lock.d` mkdir-lock the pipeline scripts use; the
   lock is owned by the lock-holder, released on every exit path via
   EXIT trap.
3. **Layer iii — this runbook**: the operator procedure. Adoption is
   a one-time, EXPLICIT, OPERATOR-ACTION event — never an automatic
   trigger of any kind. Mission automation that wants to adopt MUST use
   `pipeline_settled` + the lock; it MUST NOT gate on
   `current == <fixed-version> + health`.

`scripts/stop-ensemble.sh` and `scripts/upgrade/adopt-unit.sh` are now
the canonical sources of Layer i + Layer ii (commission v0.16.6
component 2, 2026-09-29). Any external automation adopting a similar
shape MUST consume the same helpers.

---

## 3b. `--force` override on stop-ensemble.sh (operator emergency only)

`scripts/stop-ensemble.sh` accepts a single `--force` flag in **any
position** of the argv (the m2 pre-parse walk extracts it from the full
`$@`, so `--force <dir>`, `<dir> --force`, `--force <dir> <port>`, and
`<dir> <port> --force` all resolve correctly — `tests/test_release_journal.sh`
§16c pins three of the four forms (`--force <dir> <port>`, `<dir> <port>
--force`, `--force <dir>`) while §16a pins `<dir> --force`; the legacy
`<dir> <port>` no-flag form leaves
`STOP_FORCE=0` and both remaining slots intact). On `--force`:

- The Layer-i settle-check refusal is BYPASSED with a LOUD warning
  printed to stderr:
  ```
  stop-ensemble: ⚠️  --force OVERRIDE: bypassing settle refusal (<reason>)
  stop-ensemble: ⚠️  --force can break a running promote (the pipeline is by definition in flight). Use only for emergency recovery when the operator intends to proceed despite a half-completed promote.
  ```
- The Layer-ii lock-busy refusal is BYPASSED with a LOUD warning:
  ```
  stop-ensemble: ⚠️  --force OVERRIDE: bypassing lock-busy refusal (the lock is held by another pipeline action)
  stop-ensemble: ⚠️  --force can race a live promote; expect journal divergence / sweep recovery / restart thrash
  ```

`--force` is the operator emergency escape when a promote is wedged
under a wedged daemon and the operator MUST stop the install anyway.
Risks:
- **journal divergence**: a `--force` stop mid-promote leaves the
  journal's `current` out of sync with the symlink; the launcher's
  next-start sweep will sweep-rollback to previous (per B4 leave-txn-
  open policy). This is the SAFE recovery — env stays on a known-good
  release.
- **restart thrash**: a `--force` stop races the promote's
  `restart_via_launcher`. The promote's halt event is left in the
  journal; the next launcher start observes the orphan and clears it.
- **adoption-marker left behind** (only if `--force` happened during
  an in-progress adoption): see §3c.

`scripts/upgrade/adopt-unit.sh` has NO `--force` override. Adoption
already refuses on a running daemon (step a) and an UNSETTLED pipeline
(Layer i); refusing to adopt over a live pipeline is the same defense
surface — the lock IS the live pipeline.

### `PIPELINE_LOCK_HELD_BY_CALLER=1` (env-var escape — trust-assumption statement)

`stop-ensemble.sh` also honors the **env var** `PIPELINE_LOCK_HELD_BY_CALLER=1`,
which SKIPS BOTH the Layer-i settle-check AND the Layer-ii lock-acquire.
This is `--force`-equivalent in safety: any caller can set the env var
and bypass both gates silently.

**TRUST ASSUMPTION: ONLY `lib.sh stop_via_stop_script` (the in-pipeline
caller inside `promote.sh` / `rollback.sh` / `restart.sh`) may set this
env var.** It exists because every in-pipeline stop call already holds
the rollback.lock.d — without the escape, every promote would busy-wait
15s on the lock the parent already holds (every promote would deadlock
on its own stop span). The env var is the only shape that lets the
parent flag the child without changing the argv.

**External use is equivalent to `--force`** — there is no audit trail
(env var does not show in shell history). The script emits a LOUD
two-line stderr WARN at the honor site (`stop-ensemble.sh:178-181`,
commission v0.16.6 c2 fix-back) the moment the env var is honored, so
a misconfigured / foreign caller cannot silently sidestep the gates.

**Operator preference: pass `--force` explicitly for any operator
emergency.** It is auditable in shell history, the bypass warnings are
more granular (settle-bypass vs lock-busy-bypass), and there is no
in-pipeline-call ambiguity. Reach for `PIPELINE_LOCK_HELD_BY_CALLER=1`
ONLY if you are inside `lib.sh stop_via_stop_script`.

---

## 3c. Adoption-marker stale-recovery (operator lane)

The adoption-in-progress marker (`<INSTALL_DIR>/releases/.adoption_in_progress`,
plain-text `pid=`/`run_id=`/`started_at=` lines; NOT a journal field —
schema-gen-safe by construction) is written by `adopt-unit.sh` IMMEDIATELY
before any mutation and cleared on verify success only. **No auto-expiry
is implemented** (the spec explicitly disallows one without justification;
the recovery is the documented manual procedure below).

When the marker is present:
- A concurrent `promote.sh` preflight refuses (78) with the
  `adoption-in-progress` reason token (see `promote_entry_check`,
  lib.sh).
- A re-run of `adopt-unit.sh` itself proceeds (the marker presence
  does not block a re-run; it blocks promote).

When a stale marker SHOULD be cleared:

1. **Verify the adoption is genuinely NOT in flight** — check the
   install is NOT mid-sequence:
   ```bash
   ls -la $INSTALL_DIR/releases/.adoption_in_progress
   cat $INSTALL_DIR/releases/.adoption_in_progress
   # Inspect pid, run_id, started_at — is the pid alive?
   # kill -0 <pid>  # if EPERM/0 → pid alive → adopt may still be running
   # Is there an active systemctl is-active for the unit?
   systemctl is-active <unit>
   ```
2. **Confirm there is no live unit half-staged**:
   - If `systemctl is-active <unit>` reports `active` / `activating`,
     the adoption completed; the marker is a cleanup miss. Clear it.
   - If `is-active` reports `inactive` / `failed` / unknown, AND
     `<unit>`'s unit file is installed in `<UNIT_DIR>` — partial state,
     follow the §5 rollback procedure FIRST, then clear the marker.
   - If the unit file is NOT installed — the adopt never landed the
     unit file (early abort). Clear the marker (no install to roll
     back).
3. **Clear the marker:**
   ```bash
   rm -f $INSTALL_DIR/releases/.adoption_in_progress
   ```
   The next promote preflight now proceeds.

**NEVER auto-clear via a timer or a stale-mtime check** — the spec
explicitly disallows this without operator justification. A stale
marker that prevents promote is a SAFE state (the env stays where it
was; the operator can run `status.sh` and decide); auto-clearing
silently under operator-confusion would re-create the original
half-staged hazard.

**Coordination note (race window):** if an operator `rm -f`s the marker
while a concurrent `adopt-unit.sh` is mid-sequence (between marker
write and verify-clear), the clear's idempotent-absent path still
returns 0, but the marker was absent during that window — a concurrent
promote preflight could have proceeded through the refusal surface.
Coordinate the manual removal with no adoption in flight (the `pid=`
line in the marker file names the writer; check it is dead before
clearing).

---

## 4. Polkit prerequisite (🔴 adoption blocker without it)

`systemctl enable --now` on a SYSTEM unit needs polkit authorization for
`org.freedesktop.systemd1.manage-units` (+ `manage-unit-files` for enable,
`reload-daemon` for daemon-reload). Install this pattern-restricted rule
ONCE as root — it grants ONLY the ensemble unit family to a local group, nothing else:

```javascript
// /etc/polkit-1/rules.d/50-ensemble-units.rules
// Ensemble systemd adoption (P4) — pattern-restricted unit management.
// Grants the ensemble-*.service family (daemon units AND the transient
// ensemble-e2e-*.service drill units) to local members of ensemble-admins.
polkit.addRule(function(action, subject) {
    var managed = ["org.freedesktop.systemd1.manage-units",
                   "org.freedesktop.systemd1.manage-unit-files",
                   "org.freedesktop.systemd1.reload-daemon"];
    if (managed.indexOf(action.id) < 0) return null;
    var unit = (action.lookup("unit") || "");
    if (action.id !== "org.freedesktop.systemd1.reload-daemon" &&
        !/^ensemble-[0-9A-Za-z@._-]+\.service$/.test(unit)) return null;
    if (subject.isInGroup("ensemble-admins") && subject.local) {
        return polkit.Result.YES;
    }
    return polkit.Result.NOT_AUTHORIZED;
});
```

Notes:
- The unit-name regex charset (`[0-9A-Za-z@._-]`) is the SAME charset family
  as the existing scope rule on ensemble-vm (incident doc §5 — the
  `ensemble-upgrade-*.scope` transient scopes) — one character-class
  convention across both ensemble unit families.
- `reload-daemon` has no unit to restrict — the rule gates it on the group +
  locality only, which is the same trust boundary the other two actions get.
- `adopt-unit.sh` step (c) scans `*.rules` under `/etc/polkit-1/rules.d` for
  a rule mentioning the action id AND the ensemble service pattern, and
  REFUSES early without it (better a refusal before any file lands in
  `/etc` than an enable failure after). The authoritative gate remains
  systemctl itself (a missing grant makes step d fail loud).
- Legacy pkla/localauthority hosts are out of scope — port the rule manually
  if your distro predates `.rules`.

---

## 5. Rollback-of-the-adoption (manual, operator lane)

Reverse of step (d), in reverse order. Run as root; the install is DOWN
during steps 1–4 (same maintenance-window shape as adoption).

```bash
UNIT=ensemble-<slug>.service        # §3 naming rule
INSTALL_DIR=<the adopted install>

# 1. disable + stop the unit (systemd owns the stop; graceful TERM path)
sudo systemctl disable --now "$UNIT"

# 2. remove the generated unit file + reload
sudo rm "/etc/systemd/system/$UNIT"
sudo systemctl daemon-reload
sudo systemctl reset-failed "$UNIT" 2>/dev/null || true

# 3. un-stage ENSEMBLE_RESTART_UNIT from the install's .env (additive-in,
#    additive-out — touch ONLY that key):
sed -i '/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}ENSEMBLE_RESTART_UNIT[[:space:]]*=/d' \
    "$INSTALL_DIR/.env"

# 4. restart script-mode (the pipeline's own documented nohup shape —
#    lib.sh restart_via_launcher):
( cd "$INSTALL_DIR" && nohup ./launcher.sh >> data/launcher.log 2>&1 & )

# 5. confirm: /livez on the staged port + classification now verifies
#    SCRIPT_NOHUP (declared auto → conforming per the A1 map)
```

After rollback the install is row 3 of the A2 matrix again (conforming,
nothing deprecated). The polkit rule (§4) may stay — it is harmless without
ensemble units and is reused by any future re-adoption. If the adoption
left an adoption-in-progress marker (§3c), clear it AFTER step 4 — the
unit-disable + .env-unstage above may or may not clear it depending on
where the prior adopt aborted (the marker is removed on verify success
only; partial-state failures leave it present).

---

## 5a. ONE-TIME LIVE REMEDIATION — purge the pre-strip unit's minted env (USER DECIDES — no live action tonight)

> **Status: DOCUMENTATION ONLY (unit-secret-exposure, 2026-10-23).** The
> zero-env generator strip fixes FUTURE generations; the EXISTING live unit
> (generated Sep-30) still carries six minted `Environment=` lines
> (`PORT` + `POSTGRES_HOST` + `POSTGRES_PORT` + `POSTGRES_DB` +
> `POSTGRES_USER` + `POSTGRES_PASSWORD`) that systemd re-serves — in
> cleartext — to ANY local uid via D-Bus (`systemctl show -p Environment`;
> man 5 systemd.exec: the unit environment is "exposed to unprivileged
> clients via D-Bus IPC … not suitable for passing secrets"). Purging them
> is a one-time operator action **the user decides on**; no lane performs
> it tonight. Key NAMES only appear below — never values.

**Fix-delivery path (read first):** `scripts/` does **NOT** ship in the
staged release payload — `adopt-unit.sh` runs from a repo checkout, and
`deploy.sh` never invokes it. **The fixed generator reaches operators via
an updated checkout post-merge; the staged release does not carry it.**

**Remediation census (run BEFORE/WITH either option — W6):** enumerate ALL
`ensemble-*.service` units on the host and check EACH — any unit still
carrying minted env needs the same remediation (the demo install is the
same generator class and must not be forgotten):

```bash
systemctl list-units 'ensemble-*' --all --no-legend     # running/loaded set
ls -1 /etc/systemd/system/ensemble-*.service 2>/dev/null  # on-disk set
for u in $(systemctl list-unit-files 'ensemble-*' --no-legend | awk '{print $1}'); do
  echo "== $u"; systemctl show -p Environment "$u"
done
# ANY unit whose Environment property lists POSTGRES_* (or PORT) KEY=VALUE
# entries carries minted env → remediate it too.
```

### 5a.(a) NON-DISRUPTIVE DROP-IN — full-list env reset (RECOMMENDED: apply now)

A systemd drop-in can reset the unit's `Environment=` list without
regenerating anything and without stopping the daemon:

```bash
# 1. SCRATCH-UNIT EMPIRICAL CHECK FIRST (note (i) — cheap, zero live risk;
#    validates the lone-reset mechanics on THIS host's systemd BEFORE the
#    live unit is touched; also — via the read-only census above — confirms
#    the live unit's env set is the six minted keys the reset will clear):
sudo tee /etc/systemd/system/ensemble-scratch-envcheck.service >/dev/null <<'EOF'
[Unit]
Description=scratch — zero-env reset empirical check (delete after)
[Service]
Type=oneshot
ExecStart=/bin/true
Environment=PORT=1
Environment=POSTGRES_PASSWORD=scratch_dummy_value
EOF
sudo mkdir -p /etc/systemd/system/ensemble-scratch-envcheck.service.d
sudo tee /etc/systemd/system/ensemble-scratch-envcheck.service.d/10-reset.conf >/dev/null <<'EOF'
[Service]
Environment=
EOF
sudo systemctl daemon-reload
echo "--- scratch property after lone-reset (expect BARE 'Environment=' — no KEY=VALUE):"
systemctl show -p Environment ensemble-scratch-envcheck
#    → if the scratch property still lists KEY=VALUE entries, STOP: this
#      host's systemd does not reset via the lone form — escalate instead
#      of applying to the live unit.
# CLEANUP (throwaway — leave nothing behind):
sudo rm -rf /etc/systemd/system/ensemble-scratch-envcheck.service.d
sudo rm -f /etc/systemd/system/ensemble-scratch-envcheck.service
sudo systemctl daemon-reload

# 2. THE DROP-IN — ONE LONE `Environment=` LINE (the FULL-LIST reset):
sudo mkdir -p /etc/systemd/system/ensemble-main.service.d
sudo tee /etc/systemd/system/ensemble-main.service.d/10-zero-env-reset.conf >/dev/null <<'EOF'
[Service]
# LONE Environment= — the option-level FULL-LIST reset (man 5 systemd.exec).
# This is deliberately NOT the per-variable empty-string form
# (`Environment=POSTGRES_PASSWORD=` …) — that form is WRONG and must NOT
# appear: it leaves each var present-but-empty (not unset), and whether the
# manager's stored list drops the earlier non-empty entry is unverifiable
# on this host — under either behavior the gate below would fail while
# this runbook reported success, and if systemd retains BOTH entries the
# plaintext STAYS exposed. Do not "improve" this line per-variable.
Environment=
EOF

# 3. RELOAD (this is where the manager's merged view loses the values):
sudo systemctl daemon-reload

# 4. VERIFICATION GATE:
systemctl show -p Environment ensemble-main
#    PASS = NO `KEY=VALUE` entries in the output. EXPECTED & CORRECT: an
#    emptied list still prints the BARE `Environment=` line — that bare
#    line is NOT a failure; do not re-apply anything over it.
#    FAIL = any `POSTGRES_*=` / `PORT=` entry still listed → STOP, do not
#    proceed on any assumption; investigate with the scratch-unit check.
```

**Honest caveat (running process):** the exposure CLOSES at
`daemon-reload` — that is when the manager's merged view drops the values.
The RUNNING daemon keeps its inherited environment (copied at fork/exec)
until its NEXT RESTART — the next promote performs one. The drop-in closes
the D-Bus read surface immediately; the process memory surface closes at
the next restart.

Required notes:

1. **Scratch-unit empirical check BEFORE live application** (step 1 above)
   — cheap, zero live risk, and it confirms the six-key set the reset will
   clear.
2. **The reset also clears `MALLOC_ARENA_MAX`** — harmless: the launcher
   re-establishes it default-only (`: "${MALLOC_ARENA_MAX:=2}"` + export,
   `launcher.sh:1658-1659`) at every start.
3. **Order-sensitivity:** drop-ins sort AFTER the main unit. A FUTURE
   drop-in that re-adds `Environment=` values RE-CREATES the exposure —
   the lone-reset drop-in must stay the LAST-sorting env-bearing drop-in
   (or be re-verified after any future drop-in change).
4. **Post-re-adoption:** once the unit is regenerated by the fixed
   generator (option (b)), the lone reset is an idempotent no-op-to-
   harmless (the regenerated unit sets only `MALLOC_ARENA_MAX`, which the
   reset clears and the launcher re-establishes) — removal is OPTIONAL at
   re-adoption.

### 5a.(b) FULL RE-ADOPTION — §5 teardown THEN §3(d) adoption (apply at the next maintenance window)

**Not §5 alone:** §5 above is the ROLLBACK-TO-SCRIPT-MODE recipe — it is
NOT a re-adoption recipe and must not be cited as one. The re-adoption
composite is **§5 teardown (steps 1–4) THEN §3(d) adoption**, in that
order:

- **HARD precondition:** §5 step 2 (unit file removed + `daemon-reload`)
  MUST have completed — `adopt-unit.sh` REFUSES (exit 78) when the
  destination unit already exists (one-time migration never clobbers,
  `adopt-unit.sh:534-539`). No clobber-free shortcut exists by design.
- **UNWRITTEN COMPOSITE STEP (spelled here):** §5 step 4 restarts the
  install in script-mode (nohup). **STOP that interim script-mode daemon
  BEFORE the adoption's `systemctl enable --now`** (step (d).4) — the
  interim daemon holds the port and the adoption would mint DUAL_FIGHT.
  Concretely: run `bash scripts/stop-ensemble.sh <INSTALL_DIR>` between
  §5 step 4 and the adoption (step (a) of the tool refuses on any owned
  pid otherwise). The §5 script-mode interim exists to keep the downtime
  window near-zero (stop → adopt → start is seconds); going
  removal→adopt DIRECTLY widens the outage.
- **UNIT-BACKUP HAZARD:** do NOT "back up" the removed unit file
  (`sudo cp /etc/systemd/system/... ~/backup/`): a backup RE-PERSISTS the
  POSTGRES_PASSWORD-bearing artifact at a new path — the exact defect this
  section exists to close, now untracked. FORBID backups; if a copy is
  truly required for forensics, make it root-owned 0600 AND delete it
  after verification.
- **What the regenerated unit looks like:** drop-in replacement for the
  old one, GAINS `After=postgresql.service` + `Environment=MALLOC_ARENA_MAX=2`
  (both post-date the Sep-30 generation), and LOSES the six minted
  `Environment=` lines (zero-env contract).

**Recommendation:** apply **(a) now** (non-disruptive, closes the D-Bus
read surface immediately) and schedule **(b) at the next maintenance
window** (purges the values from the unit file itself and from the running
process at its next start). **User decides — no live action tonight.**

---

## 6. Deploy-ownership ledger (arm closure status)

The deploy-ownership conflict family had two arms (critical-notes
"DEPLOY-OWNERSHIP"; incident `docs/incidents/2026-09-28-promote-cgroup-teardown.md`):

| Arm | Status | Closed by |
|---|---|---|
| **In-flight promote-death** (cgroup teardown killed the promote executor) | **CLOSED — v0.16.2** (`d7cdaf3b`: `systemd-run --scope` escape + death-anchored journaling; demo-E2E-proven 2026-09-28, zero manual recovery) | fix/promote-cgroup-survivorship |
| **Steady-state port race / unit ownership** (nohup lineage vs unit teardown; who owns the daemon between deploys) | **CLOSED — P4 commission** (P1–P4): the adoption lane gives the install a REAL unit (`KillMode=mixed`, `systemctl start` the default start path, unit = the cgroup boundary). With the unit adopted, steady-state ownership is unambiguous — no nohup/unit port race exists on an adopted host. P2/P3 close the interaction paths (unit-aware stop, DUAL_FIGHT refusal, unit hand-back). | supervision-detection P1–P4 |
| **Racy-precondition adoption (incident r-20260929-170301-0cb2)** | **CLOSED — v0.16.6 component 2** (the 3-layer protocol: settle-check + pipeline lock + this runbook). The adoption tool can no longer race a live flip; the operator runbook explicitly forbids the "current == X + health" anti-pattern. | commission v0.16.6 component 2 |

**Residual (documented, not a defect):** hosts that never adopt stay
script-mode — a conforming topology (A1 `auto×SCRIPT_NOHUP`), with the
r-f82e-family cgroup caveats unchanged. Adoption is the operator's explicit
opt-in; this runbook is the lane.

---

## 7. Deferred (out of P4 scope, by decision)

- **`WatchdogSec` / sd_notify** — not wired into the launcher/daemon; a
  watchdog interval without notify support would be an unconditional restart
  loop. The base template carries NONE by design.
- **service×macOS (launchd adoption tool)** — A2 row 5, FUTURE scope.
- **Auto-provisioning of the unit + polkit rule by `stage.sh`/installer** —
  the incident doc §9 deferred family ("installer adoption of host-side
  artifacts"); host-side artifacts stay operator-installed (§4) until that
  commission exists.
- **StartLimit tuning** — systemd defaults (`5` starts / `10s`) are relied
  upon as the outer crash-loop breaker; no explicit directives in the base
  template. Revisit only with evidence.
- **P5 tests** — the A2 test-id column and the drill leg's test ids land in
  P5 (this file's placeholders get filled there).

---

## 8. Test-id cross-reference (P5 fill — commission v0.16.6 component 2)

The v0.16.6 component 2 commission added five new test sections to
`tests/test_release_journal.sh`. They are PART of the existing
`test/packs/release_journal_unit_test.sh` wrapper (no new pack file —
the wrapper stays the single home for the journal unit battery).

| Suite section | Subject | Refs |
|---|---|---|
| §15 — `pipeline_settled` reason-token matrix | Each of the 7 settle refusal tokens fires exactly once with the named token; the no-journal case asserts the NEW INFO contract (rc 0 + INFO line, NOT a `reason=no-journal:` refusal — see fix-back cycle); happy path returns 0 silently. | This runbook §3a; `scripts/upgrade/lib.sh` `pipeline_settled`. |
| §16 — Layer-ii mutex (adopt + stop) | Adopt takes the lock; concurrent promote_entry_check fails; `--force` overrides with loud warning; `PIPELINE_LOCK_HELD_BY_CALLER=1` escape path keeps promote's in-pipeline stop call lock-free (and emits a LOUD stderr WARN at the honor site — see §3b). | This runbook §3a/§3b; `scripts/upgrade/lib.sh` `lock_acquire`. |
| §17 — Adoption-marker preflight + clear | `adopt-unit.sh` writes the marker pre-mutation; promote preflight refuses with `adoption-in-progress` reason; verify-success path clears; stale-marker manual removal path. | This runbook §3c; `scripts/upgrade/lib.sh` `promote_entry_check`, `adoption_marker_*`. |
| §18 — stage.sh uv hard-refuse (fix-back add) | PATH-scrubbed + HOME-overridden stage.sh invocation refuses (78) when neither `command -v uv` nor `$HOME/.local/bin/uv` resolves; the remedy text is asserted. | `scripts/upgrade/stage.sh` (refuse gate). |
| §19 — adopt-unit MUTATION-mode lock/marker integration (NAMED FENCE; deferred when host lacks systemd) | Under a real-systemd host, `adopt-unit.sh` in MUTATION mode (DRY_RUN=0) acquires `rollback.lock.d`, writes the adoption-in-progress marker, runs the install/enable/daemon-reload sequence, clears the marker on verify success, releases the lock — pinned end-to-end. Hosts WITHOUT systemd emit `SKIP(no-systemd-host)` (no fake pass). | `scripts/upgrade/adopt-unit.sh` (MUTATION-mode lifecycle). |

Reason-token taxonomy (two tiers; every refusal carries exactly one
`reason=<token>` — L10 discipline: a token-less refusal never journals):

- **Single-concept legacy tokens** — `journal-current-unset`,
  `current-symlink-absent`, `current-symlink-mismatch`,
  `current-symlink-garbled`, `in-flight-txn`, `pending-op`, `lock-held`,
  `cap`, `cooldown`, `quarantine`, `not-staged`, `txn-busy`,
  `adoption-in-progress`. Short names for the STATE that fired, nothing
  more; downstream tooling and older runbooks key on them verbatim —
  never rename.
- **Descriptive newer tokens** — `f2-not-verified`,
  `supervision-unit-incapable-host`, `supervision-unit-unresolved`,
  `layer-i-pipeline-unsettled`, `layer-ii-lock-busy`. Carry the gate
  family in the token itself, so a journal/history grep reads without
  this runbook open. New tokens SHOULD be descriptive unless they extend
  an existing legacy family.