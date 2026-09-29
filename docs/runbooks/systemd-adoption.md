# systemd Adoption Runbook — graduating an Ensemble install to SERVICE MODE

- **Version:** 1.0 @ `feat/supervision-detection` P4 (2026-09-29)
- **Canonical path:** `docs/runbooks/systemd-adoption.md` — this file is the
  canonical home of the **OS×deployment matrix (A2)** and the **feature-gap
  table (A4)** referenced by the supervision twins
  (`scripts/upgrade/lib.sh` §supervision + `daemon/tools/upgrade_journal.py`
  §supervision — both forward-reference THIS path; keep the five A2 rows in
  sync with those docstrings).
- **Commission:** supervision-detection P4 of 5 (+ Amendment #1 deltas
  A2/A3/A4). P1 (classifier twins), P2 (stop path), P3 (hand-back) landed at
  HEAD `1a1dd5d4`; P5 (tests) fills the test-id column below.
- **Companion docs:** `docs/runbooks/upgrade-drills.md` (drill legs incl. the
  new promote-under-real-unit leg §5(e)) · `docs/incidents/2026-09-28-promote-cgroup-teardown.md`
  (the r-f82e incident + the hand-provisioned `ensemble-live.service`
  background) · the A1 declared×verified outcome map (in-repo: the twins'
  section comments).

**Adoption is OPT-IN, EXPLICIT OPERATOR ACTION ONLY.** Nothing in the
pipeline ever adopts a host automatically; script mode remains a first-class,
conforming topology (A1 row `auto×SCRIPT_NOHUP`). This runbook exists so an
operator can graduate an existing script-mode install to service mode
WITHOUT reinstallation, in one documented procedure.

**Never run adoption against an install you did not intend to migrate.**
The tool refuses a running install (step a), a non-systemd host (step b), and
a host without the polkit rule (step c) — see §Procedure.

---

## 1. A2 — OS×deployment matrix (canonical)

The two named topologies (A1/Amendment #1) crossed with the OS families the
repo actually runs on. **`ensemble-vm` live is the third row TODAY**
(2026-09-29): systemd present, install not adopted — declared `auto` (the
default), verified `SCRIPT_NOHUP` → **conforming** per the A1 map. Adoption
moves an install from row 3 to row 4.

| # | Deployment × OS | What works TODAY | What this commission (P1–P4) ADDS | Tests covering it |
|---|---|---|---|---|
| 1 | **script × macOS** | The byte-identical no-systemd arm: classifier short-circuits before ANY `/proc` / `/run` read (P1 guard discipline — `supervision_classify` non-Linux gate; python twin `supervision_detect` same). Launcher lineage self-respawns; launchd plist (`scripts/ensemble-prod.plist`) owns boot-restart where installed. | **Nothing on this row — by design.** `adopt-unit.sh` REFUSES non-Linux (step b). A1 naming only (`SCRIPT_NOHUP` = conforming). | *(P5-TBD — placeholder; P5 fills test ids)* |
| 2 | **script × ubuntu (no systemd)** | The SAME byte-identical arm: `/run/systemd/system` absent → classifier returns `SCRIPT_NOHUP` without further reads; launcher backoff loop is the whole supervision story. | **Nothing on this row.** `adopt-unit.sh` refuses (step b host guard — no systemd to adopt into). | *(P5-TBD)* |
| 3 | **script × ubuntu (systemd present, NOT adopted) — TODAY'S LIVE TOPOLOGY** | systemd exists but the install runs as a direct/nohup lineage; cgroup leaf = `session-*`/user-slice → verified `SCRIPT_NOHUP`; declared `auto` → **conforming** (A1). This is ensemble-vm live (port = live port) TODAY. | **A documented, one-command graduation path** (this runbook + `adopt-unit.sh`), plus the P3 in-promote self-heal: with a unit configured (`ENSEMBLE_RESTART_UNIT`), the next promote's hand-back moves the install INTO the unit (scope→unit / script→unit). | *(P5-TBD — classifier pin + refusal arms)* |
| 4 | **service × ubuntu (systemd substrate) — THE NEW PATH** | Before P4: reachable ONLY by hand-provisioning a unit (the `ensemble-live.service` precedent on ensemble-vm — out-of-repo, incident doc §5). | **The adoption lane (P4):** static base template (`scripts/systemd/ensemble-daemon.service`) + `scripts/upgrade/adopt-unit.sh` generation from CURRENT install config + verified handover (polkit-gated, fail-loud, rollback documented below). Declared `unit` × verified `UNIT_MANAGED` = conforming; P3 unit hand-back keeps the unit across promotes (NO nohup fallback — Amendment #1). | *(P5-TBD — incl. the real-seat E2E drill leg, upgrade-drills.md §5(e))* |
| 5 | **service × macOS (launchd) — documented FUTURE scope** | `scripts/ensemble-prod.plist` (daemon) + `scripts/ensemble-watchdog-watcher.plist` (watchdog) exist as launchd substrates. | **Nothing — FUTURE scope by decision.** The A1/A2 vocabulary (declared×verified) is substrate-neutral; a launchd adoption tool would slot here. Not commissioned. | *(P5-TBD — n/a while future-scoped)* |

*(The test-id column is intentionally a placeholder — mission A2: "P5 will
fill test ids".)*

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

---

## 3. The adoption procedure (single, one-time)

**Goal:** graduate `<INSTALL_DIR>` from script mode to service mode without
reinstalling. Prereqs: Linux host with systemd; the install STOPPED; the
polkit rule of §4 installed; root (sudo) for the unit-file install step (or a
writable `UNIT_DIR` override on test hosts).

0. **Plan the maintenance window** — the daemon is down from step 2 until the
   unit starts (typically < 1 min).
1. **(One time, root) Install the polkit rule** — §4 below.
2. **Stop the install (script-mode stop, ownership-scoped):**
   ```bash
   bash scripts/stop-ensemble.sh <INSTALL_DIR>
   ```
3. **Preview (recommended first run):**
   ```bash
   DRY_RUN=1 bash scripts/upgrade/adopt-unit.sh <INSTALL_DIR>
   ```
   Runs every refusal check (a)–(c), reads config, generates the unit, and
   prints the sequence + the unit with the PG password masked. Zero mutations.
4. **Adopt:**
   ```bash
   sudo bash scripts/upgrade/adopt-unit.sh <INSTALL_DIR>
   ```
   (Under sudo the unit's `User=` still defaults to the login user via
   `SUDO_USER`.) The tool, in order, fail-loud at every step:
   - **(a)** refuses if ANY owned pid is alive (adopting over a running
     daemon mints `DUAL_FIGHT` — two masters; reuse of the anchored
     pid-discovery tiers via `lib.sh _supervision_owned_pids`);
   - **(b)** refuses non-Linux / no `/run/systemd/system` / no systemctl;
   - **(c)** refuses (🔴) without the polkit rule of §4;
   - **(d)** generates the instance unit from the static base template
     (`scripts/systemd/ensemble-daemon.service`) reading config the
     `_resolve_wait_s` way (port + `POSTGRES_*` straight from
     `<INSTALL_DIR>/.env`; `User` defaults to the adopting user; `ExecStart`
     = the EXISTING `<INSTALL_DIR>/launcher.sh` — the launcher's exit map
     and backoff stay in the loop; the unit adds NO second supervisor),
     then: install unit (0600 — it carries secret env) → `daemon-reload` →
     stage `ENSEMBLE_RESTART_UNIT=<unit>` into `.env` (ADDITIVE — only that
     key is touched) → `systemctl enable --now` → **verify**: `/livez` on
     the staged port + the boot journal's `supervision_boot` advisory shows
     `state=UNIT_MANAGED unit=<unit>` + the declared×verified outcome map
     lands on `conforming`.
     Any failure = loud exit + a printed partial-state ledger (this tool
     never auto-rolls-back; §5 is the manual procedure).
5. **Confirm:**
   ```bash
   systemctl status <unit>            # active; MainPID = the launcher
   journalctl -u <unit> -f            # unit journal (structured capture)
   bash scripts/upgrade/status.sh <target>   # pipeline view unchanged
   ```
   The NEXT promote on this install performs the P3 **unit hand-back**
   (`reset-failed → is-active → start → verify NEW MainPID`) — no nohup
   fallback ever re-creates the survivor lineage (Amendment #1).

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
(host guard), `ADOPT_USER`, `ADOPT_LIVEZ_BUDGET_S`, `DRY_RUN=1`, plus
`curl`/`uname` PATH-stubs for the verify/host arms. The P4 smoke used all of
these against fixture dirs — no live/demo contact, no real daemon-reload.

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
ensemble units and is reused by any future re-adoption.

---

## 6. Deploy-ownership ledger (arm closure status)

The deploy-ownership conflict family had two arms (critical-notes
"DEPLOY-OWNERSHIP"; incident `docs/incidents/2026-09-28-promote-cgroup-teardown.md`):

| Arm | Status | Closed by |
|---|---|---|
| **In-flight promote-death** (cgroup teardown killed the promote executor) | **CLOSED — v0.16.2** (`d7cdaf3b`: `systemd-run --scope` escape + death-anchored journaling; demo-E2E-proven 2026-09-28, zero manual recovery) | fix/promote-cgroup-survivorship |
| **Steady-state port race / unit ownership** (nohup lineage vs unit teardown; who owns the daemon between deploys) | **CLOSED — THIS commission (P4)**: the adoption lane gives the install a REAL unit (`KillMode=mixed`, `systemctl start` the default start path, unit = the cgroup boundary). With the unit adopted, steady-state ownership is unambiguous — no nohup/unit port race exists on an adopted host. P2/P3 close the interaction paths (unit-aware stop, DUAL_FIGHT refusal, unit hand-back). | supervision-detection P1–P4 |

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
