# Stage 2 — Hardening: root-owned tree, gated promote, deletion backstop

**Status:** ⛔ **BLOCKED-ON v0.29.9** (see Journal Rationale). Design COMPLETE per council `22da8b7f` (unanimous Family A) + worker `7989fe00`; checklist PREPARE-ONLY — the OPERATOR executes; agents prepare. **Corrections folded 2026-11-09 from rehearsals b+c** (mv -hf flip, version-string collision, manual-flip journal drift — see Promote section) **+ the doc review's PlistBuddy false-alarm fix (see C7)**.
**Preconditions:** Stage 1 complete (`/opt/ensemble`, operator-owned, plists converged paths) · v0.29.9 deployed + soaked · demo rehearsal green · developer legs landed (privrun wrapper, macOS twin scripts, launcher PATH/STATE_FILE/refuse-marker patches, watcher channels).
**Model reference:** `architecture-recommendation.md` §iii (the stack), §iv (PATH), §v (STATE_FILE), §vii (residuals), §viii (compatibility).

---

## Journal Rationale for the v0.29.9 gate (H-accurate)

The VM **did** have the wedge: 2026-11-01 outage — journal-size boot wedge (579 history entries / 95 KB → each boot's pure-bash journal sweep burned ~13 min at 100% CPU, retry-doomed by its own bounded-timeout watchdog; daemon never spawned; systemd showed "active") compounded by a halted-promote open txn. Resolved by promoting v0.28.4 → **v0.28.7**, which carries the `_journal_sweep` FAST-PATH ("Boot now ~1 s").

**Label the two minute-figures correctly (they are different quantities):** the **≈13 min** number is the **journal-SWEEP DURATION at wedge size on the OLD code** (canonical VM README:221-223). The **30 min** that appears in handoffs is a DIFFERENT quantity — the **in-flight-txn TTL** (`pending_op.expires_at`, README:159), i.e. how long an abandoned transaction lingers — NOT a sweep duration. The canonical doc must never present 30 min as a sweep cost. Standing rule unchanged: if a boot sweep exceeds ~1 min, trim history first (README:233-235).

**The escape is the fast-path CODE, not journal size.** Journal regrowth is structural (~675 entries/day, `mcp_server_state` flooding); ~100 KB was the wedge zone on OLD code. Real numbers: VM journal trimmed to 5.4 KB on Nov-2, since regrown to 37,315 B; the Mac pre-wipe journal was 42,121 B — a similar size that is HARMLESS on fast-path code. The canonical doc must state real sizes + the real threshold honestly, and reconcile them against the synthetic test fixtures (220–250 KB ≈ 2–3× the observed real wedge of ~95 KB — fixtures deliberately over-stress).

v0.29.9 adds the deeper cure: O(n) validation + O(n) sub-extraction (`feature/launcher-journal-sweep-fix`: d9274053d, 14d342bda, 510e72f64 — **in flight, actively committing; NOT this commission's branch to merge**).

**Why Stage 2 gates on it:** (1) Stage 2 re-shapes supervision and ownership around a boot path that re-reads the journal on every boot — the O(n) cure must be in the RUNNING binary before we rely on boot behavior under changed supervision; (2) the launcher-surface changes (PATH block, STATE_FILE relocation, refuse marker) ship in the same launcher — one coherent launcher window, one drill cycle; (3) promotes under the new privrun flow should carry the journal-safe launcher from day one.

---

## The chosen layer stack (Family A — council unanimous)

```
                    ┌─ root:wheel ──────────────────────────────────────────┐
 /opt/ensemble      │ read-only: launcher.sh, ensemble-prod, agents/,       │
                    │ frontend/, config.yaml                                │
                    │ .env: root:<operator-group> 640 (see note)            │
                    ├─ operator-writable ───────────────────────────────────┤
                    │ data/**  (DBs, logs, tmp_images, state post-reloc)    │
                    │ releases/ (journal, staging, payloads)                │
                    └───────────────────────────────────────────────────────┘
 supervisor         gui LaunchAgent com.ensemble.prod (operator, NEVER root)
                    gui LaunchAgent com.ensemble.watchdog (notifications)
                    root LaunchDaemon com.ensemble.snapshot  (1800 s)
                    root LaunchDaemon com.ensemble.treecheck  (60 s)
                    root LaunchDaemon com.ensemble.health     (R1, optional)
 root touchpoint    /usr/local/bin/ensemble-privrun  (promote only; INERT until armed)
 store              /private/var/ensemble/{snapshots,alerts}  root:root 700
```

### Ownership / mode plan (exact)

| Path | Owner:group | Mode | Notes |
|---|---|---|---|
| `/opt/ensemble` (top) | root:wheel | 755 | Operator cannot unlink/replace top-level entries ⇒ bare `rm -rf` dies at the first root-owned child; `make uninstall` EACCES-blocked AND guard-gated |
| `launcher.sh`, `ensemble-prod`, `config.yaml` | root:wheel | 755 / 644 | Read-only to daemon + lanes |
| `agents/`, `frontend/` | root:wheel | 755 (files 644) | **Behavior change (state in the doc): prompt edits flow repo→release payload** — DB overlay rows still override files; loader mtime cache reaches new spawns via release |
| `.env` | root:<operator-primary-group> | 640 | **Adjudication:** NOT root:wheel 600 — the launcher (operator uid) must read it to source env; lanes-as-operator can read it regardless (impossible to hide from same-uid lanes — VM parity, accepted). Edits via `sudo` (or shipped in release payloads). Group readable so the daemon reads; NOT world-readable |
| `data/**` | operator:staff | 755 | Writable set — instances.db (config.py:1105/7311), `data/logs/` (logging_setup.py:72), `tmp_images/` (config.py:2895), launcher/watchdog logs+state (plists:101-105, watchdog-watcher.sh:27/:56), vscode-user-data (config.py:5629) |
| `releases/` | operator:staff | 755 | Writable set — journal, staging, payloads; promote allowlist anticipates `releases/<ver>/scripts/upgrade/promote.sh` (ensemble-promote:112-113) |
| `.launcher-state`, `.sweep-defer-*` | (relocated into `data/`) | — | Via `ENSEMBLE_LAUNCHER_STATE_FILE=/opt/ensemble/data/.launcher-state` — resolves the top-dir seam (root-owned top dir ⇒ operator cannot create state files there). **Default stays `$INSTALL_DIR/.launcher-state` — VM untouched.** Rejected: top-dir ACL (deploy.sh plain-cp drops ACLs) |
| `/private/var/ensemble/{snapshots,alerts}` | root:root (self-enforced, snapshot.sh:50/:58-60 pattern) | 700 / alerts 755 | Above the no-sudo agent class; NOT under `$HOME`; NOT colocated with `/opt` (a `rm -rf /opt/ensemble*` glob must not take the store with it) |

Deploy consequence (state in the doc): raw top-level deploys (binary/launcher/agents direct cp as operator) die at EACCES → **deploy becomes drop-into-`releases/` + promote**; deploy.sh:326-334 staging legs that target top-level paths route through the wrapper.

### Promote: `/usr/local/bin/ensemble-privrun`

- C1 port of `scripts/protection/ensemble-promote` gates: canonicalize (`readlink -f`) → exact allowlist → invoker-ownership refusal; BSD `stat -f '%Sp'` replaces GNU `stat -c`; refusal exits 76/77 [C][B].
- **Shipped INERT** (mirrors the VM's staged/inert wrapper parity, ensemble-promote:40-44): no sudoers entry ⇒ unreachable. **Arm = user-gated window** (operator decision): stock admin-group `sudo ensemble-privrun …`, password EVERY time, **never NOPASSWD** (pre-C1 NOPASSWD = ACE, ensemble-promote:60-63). Optional tightening: granular sudoers Cmnd-alias pinning exactly this wrapper.
- **Sole root touchpoint.** Daemon NEVER root (council upheld, unchallenged). Deliberate divergence from VM: human-gated promote (agent lanes cannot answer an interactive sudo prompt) — VM keeps agent-initiable nonce+polkit promote. Operator may revisit (Decisions Pending).
- **🔴 Flip mechanic (rehearsal-c F1, /tmp-proven): `ln -sfn releases/<ver> current.new.$$ && mv -hf current.new.$$ current` — `-hf` is MANDATORY.** When `current` is a symlink-to-directory, `mv -f` (and bare `mv`) FOLLOWS the destination and nests the temp link INSIDE the old release — exit 0, `current` UNCHANGED: a **silent no-op flip**. `mv -hf` matches the repo's own launcher twin `_js_flip_current` exactly (the twin is authoritative); on GNU/Linux the parity flag is `mv -T`. Any wrapper/script/doc that flips `current` uses the `-hf` form verbatim.
- **Flip verification (rehearsal-c F2, proven): `readlink current` + binary `sha256`, NEVER the `/livez` version string alone** — this-era binaries share version strings across releases (v0.10.7-p2.3 and v0.10.8-p2.3 both self-report `"0.10.5"`) and the embedded string lags release tags. Rehearsal-c's identity map (per-release binary sha256) is the pattern to replicate in every flip verification step.
- **🟡 Manual flips are invisible to `releases/state.json` (rehearsal-c F4, byte-proven)** — the journal only moves via the promote/rollback tooling, so during ANY manual-flip window the journal's `current` field is stale (journal said v0.10.8-p2.3 while b65 ran; reverted, so consistent again). **Operator rule for the canonical doc:** a manual flip outside `promote.sh` (drill or emergency) must be followed by a journal `current` reconciliation, or the drift explicitly accepted-and-reverted as the drills do.

### Deletion backstop port (Directive B)

macOS TWINS under `scripts/protection/` (VM keeps its ARMED systemd units + rsync scripts — no shared-code change, no `uname` coupling; twin files carry the SOURCE-OF-TRUTH header convention):

| Element | Spec |
|---|---|
| Jobs | `/Library/LaunchDaemons/com.ensemble.snapshot.plist` (StartInterval 1800, RunAtLoad) + `com.ensemble.treecheck.plist` (StartInterval 60, RunAtLoad) — root:root, sudo to modify |
| Snapshot mechanics | `cp -Rc` (APFS clonefile; host-verified probe; man-documented fallback to copyfile(2) — never hard-fails; copies xattrs/flags). **Rejected:** `rsync -aHAX` (host `/usr/bin/rsync` = samba 2.6.9, no `-X`), `flock` (absent → `shlock`), GNU `head -n -96` (BSD rejects → portable count-prune) [B] |
| Cadence/retention | 30-min snapshots, keep 96 (48 h), UTC-stamped dirs, staging `.incoming-*` + atomic `mv` publish (snapshot.sh:74-92 pattern), stale-incoming cleanup (:78) |
| Guards | 5 GB free-floor → syslog CRIT + skip (snapshot.sh:67-72 pattern; tree ≈122 MB, 96 clones share blocks); `shlock` serialize |
| Coverage | **NOTHING excluded — `.env` and `data/` included** (a restore must reconstruct a working install; secrets protection = root-only 700 store — the VM's stated rationale, snapshot.sh:31-36) |
| Treecheck sentinel | `[ -d /opt/ensemble ] && [ -x /opt/ensemble/ensemble-prod ] && [ -f /opt/ensemble/launcher.sh ]` — 🔴 the VM's `releases/state.json` sentinel DOES NOT EXIST in the Mac flat layout; naive port = permanently green. Add `state.json` to the predicate when the releases/ layout is adopted [B] |
| Alerts | BOTH: root-side `logger` (unified log) + world-readable `/private/var/ensemble/alerts/<epoch>.txt` (outside the watched tree); gui watchdog relays to Notification Center; optional direct `launchctl asuser <uid> osascript` from root daemon (gui-domain osascript itself works — exit 0, rehearsal-c D7 capture; the **root-LaunchDaemon `asuser` arm remains unproven — operator probe**). Detection ≤60 s + ≤15 min watchdog `/livez` second net |
| Restore | stop job → `cp -Rc <snap>/ /opt/ensemble/` → chown per plan → bootstrap → `/livez` (release ID via `readlink current` + binary sha256 per the flip-verification rule) |

### Watchdog additions (gui agent, stays)

Refuse-marker predicate (`.launcher-refused` next to the relocated state file) + `last_exit=78` from `.launcher-state` (already read; written pre-exit at launcher.sh:2700-2702/:2744-2745) + root-alert relay (poll the alerts dir) — each with the existing dedup-latch pattern (watchdog-watcher.sh:281-326); absent files skipped silently (:48-56) ⇒ all additions no-op when absent.

### GAP-2 / GAP-3 ergonomics

- `ensemble-svctl` shim (optional, recommended): `systemctl stop`-shaped calls → `launchctl bootout gui/$(id -u)/<label>` via the `SYSTEMCTL_BIN` seam (stop-ensemble.sh:552-560); byte-identical when unset (:40-42). **Stop = bootout ONLY** — stated in every runbook step (live-proven [RA]).
- Stop budget GAP-3: `CHILD_STOP_WAIT_S=70` (launcher.sh:60-62) is the budget. **Measured on demo (rehearsals b/c, 4 bootouts): SIGTERM → `Graceful shutdown complete` → exit 143 in 1–2 s every time; the 70 s budget NEVER engaged; no KILL escalation observed.** The unexercised remainder is only the >70 s pathological case — document ≈70 s as the bound, cite the measurement.
- Detach-before-stop helper (Directive F): `/usr/bin/python3` double-fork + `os.setsid()` (macOS has no `setsid(1)`) — applied to EVERY actor path that stops/restarts the daemon.

### Optional layers

- **O1** root-owned plist copy at `/Library/LaunchAgents` (closes operator-writable plist tampering; caveat: loads at any login — single-user Mac acceptable).
- **R1** root health timer `com.ensemble.health` (e.g. 60 s): root polls `launchctl print gui/$(id -u)/com.ensemble.prod` + `/livez`; absent/unhealthy → re-bootstrap from the O1 root-owned plist copy. Heals the proven Ari-class gui-bootout silence within ~60 s. **Recommended include.**
- **chflags uchg:** REJECTED by default (breaks sanctioned mutations; snapshots already cover the accident class). Narrow fallback (`launcher.sh` + `current` only) documented, requires a `nouchg` runbook step — revisit only on wrapper-arm slip or accident recurrence.

---

## Demo rehearsal plan (first-use protocol — BEFORE any live step)

All drills on demo (:7979, `~/agents-ensemble-demo`) or throwaway paths; operator executes; agents prepare:

- [ ] D1. Guard refusals: every gated artifact refuses without its env var (exit 78) and names the override in stderr (gate-convention: `ENSEMBLE_<VERB>_LIVE=1`, heredoc stderr, exit 78; two-factor for service-interrupting ops).
- [ ] D2. `ensemble-privrun` end-to-end on the DEMO allowlist (inert → armed in the drill sandbox): allowlist hit, refusal paths (non-allowlisted target, wrong invoker ownership). Flip verification per the Promote rules: `mv -hf` shape + `readlink current` + binary sha256 (never the version string — rehearsal-c F2).
- [ ] D3. Exit-78 visibility: force a demo refusal → marker written → watchdog latch fires ONCE → auto-clear on recovery; `last_exit=78` alert path.
- [ ] D4. Treecheck on a throwaway path (`ENSEMBLE_TREECHECK_PATH` override, treecheck.sh:35-36 pattern): healthy → alert; delete the throwaway tree → alert ≤60 s in unified log + alerts dir + watchdog relay.
- [ ] D5. Snapshot + restore round-trip on demo: snapshot lands in the drill store; kill the demo tree; restore; `/livez` green; retention prune behaves.
- [ ] D6. R1 re-bootstrap: bootout the demo job (from Terminal — never from inside a daemon) → R1-equivalent detects + re-bootstraps.
- [ ] D7. Notification channel: gui-domain LaunchAgent `osascript` **works (exit 0, delivered — rehearsal-c capture)**; the open arm is the **root-LaunchDaemon → `launchctl asuser` → osascript hop** — operator probe from a root context.
- [ ] D8. GAP-3: **MEASURED (rehearsals b/c, 4 bootouts): TERM→exit 143 in 1–2 s, 70 s budget never engaged, no KILL escalation.** Re-run only if the shutdown path changes.
- [ ] D9. Launcher PATH block on demo: boot under minimal launchd PATH with and without the plist PATH block (rehearsal-a A/B re-run on the CURRENT binary era) + synthesis path (fake-HOME nvm fixture).
- [ ] D10. STATE_FILE override on demo: `.env` line → state + defer land in `data/`; watcher + upgrade-tools UI read the new location; remove line → back to default.

## Cutover sequence (gated checklist — prepare-only; two-factor gate `ENSEMBLE_STAGE2_HARDEN_LIVE=1` + `--confirm-harden` — env-var naming follows the repo convention `ENSEMBLE_<VERB>_LIVE=1`: `ENSEMBLE_DEPLOY_LIVE` on the `deploy.sh` lane, `ENSEMBLE_INSTALL_LIVE` on the `make install` lane; the confirm var states INTENT only, never a path)

- [ ] C1. Preconditions re-verified: v0.29.9 live + soaked (journal fast-path verified in `data/launcher.err.log` boot lines); Stage 1 done; D1–D10 green; operator go-decision recorded.
- [ ] C2. **Full pre-change snapshot** (manual `cp -Rc` of the tree to `/private/var/ensemble/snapshots/pre-stage2-<ts>`) + `pg_dump ensemble_prod` (DB is NOT covered by tree snapshots — residual §vii-3).
- [ ] C3. Ownership flip (single scripted pass, sudo, gated): top-level + read-only subset → root:wheel per the plan; `data/` + `releases/` → operator; `.env` → root:<operator-group> 640. Verify with `ls -laR` spot checks; daemon keeps running through the flip (open fds unaffected).
- [ ] C4. STATE_FILE wiring: append `ENSEMBLE_LAUNCHER_STATE_FILE=/opt/ensemble/data/.launcher-state` to `.env` (sudo — `.env` is root-owned now); restart via bootout/bootstrap (operator Terminal) → verify state+defer appear under `data/`; watchdog reads them; upgrade-tools UI shows launcher state.
- [ ] C5. `ensemble-privrun` installed to `/usr/local/bin` (root:wheel 755) — **INERT** (no sudoers change). Drill refusal paths (D2 shape) on live-adjacent allowlist entries.
- [ ] C6. Backstop timers: install both LaunchDaemon plists + twin scripts (root:root; scripts also staged into the repo `scripts/protection/` by the developer leg); `launchctl bootstrap system/` + `kickstart`; verify first snapshot lands, treecheck healthy-quiet.
- [ ] C7. Plist convergence: replace installed `~/Library/LaunchAgents/ensemble-prod.plist` with the template-staged copy (stable-anchor PATH — removes the rot-prone nvm version dir from supervisor env; the launcher synthesis block is now the guaranteed layer, plist PATH is the belt); `plutil -lint` **+ the G5-2.6 path assert run VERBATIM with `EXPECT_ROOT=/opt/ensemble`** — i.e. the three-verdict block (TOOL MISSING / UNREADABLE KEY / STALE PATH) using **`plutil -extract <keypath> raw -o - -- <plist>`** (verified on macOS 14.4.1: returns the BARE value; non-zero exit + distinct message for an absent key/index or a malformed file). **Never** a bare `PlistBuddy -c 'Print :ProgramArguments:1'` compare — `Print` of an array emits `Array { … }` structure noise, an absent key yields an empty/error capture, and a missing binary yields "command not found"; all three collapse into a FALSE "stale path" verdict on a healthy plist. `PlistBuddy -c 'Print'` remains fine for a HUMAN before/after diff (absolute `/usr/libexec/PlistBuddy`). Then bootout/bootstrap both jobs.
- [ ] C8. (If adopted) O1 root-owned plist copy + R1 health timer; kickstart + verify D6 behavior.
- [ ] C9. Verification battery (Stage-1 Phase-4 shape, plus): writable-set writes succeed from a lane (e.g. tmp_images upload); read-only write attempt fails EACCES; snapshot timer fired ≥1; treecheck quiet; watchdog relays a test alert; `/livez` `/readyz` green — `/readyz` components are `database`, `queue_freshness`, `services`, `checkpoint_saver`, and **MCP is advisory under `detail.mcp` and NEVER gates status ⇒ `/readyz` is NOT MCP proof** — MCP health (context7/webfetch/plane) is evidenced from `data/launcher.err.log`; any release flip verified via `readlink current` + binary sha256.
- [ ] C10. Operator decision recorded on **arming promote** (the only remaining root touchpoint): stay inert (deploys = releases/+promote without root) or arm the sudo window.

## Rollback (inverse per element; byte-state where applicable)

- [ ] RB1. Remove the STATE_FILE `.env` line (sudo) → restart → state back at tree top… **note:** post-C3 the top dir is root-owned ⇒ rollback ALSO restores top-dir operator ownership (or pre-creates the state file as root). Cleanest: run RB in the order C3←C2: undo ownership flip (scripted inverse chown per the C3 record), then `.env` line removal is trivial.
- [ ] RB2. `launchctl bootout system/` both timers + remove plists + scripts (named rm, operator-named rollback — legitimate under the narrowed no-touch rule).
- [ ] RB3. Restore installed plists from pre-C7 `.bak` copies (byte-state) + re-run the G5-2.6 assert with `EXPECT_ROOT=/opt/ensemble` (the paths restored here are the post-Stage-1 ones).
- [ ] RB4. `ensemble-privrun`: `sudo rm /usr/local/bin/ensemble-privrun` (named) — or leave inert (it is unreachable without arming).
- [ ] RB5. Ownership inverse from the C3 record; verify daemon healthy end-to-end; `cp -Rc` the pre-stage2 snapshot aside before it ages out of retention if extended rollback horizon is wanted.

## Residual risks after Stage 2

See `architecture-recommendation.md` §vii (13 items, honest). Headlines: process-kill flap (KeepAlive ≤10 s); gui-domain bootout incl. watchdog (R1 heals the daemon job ~60 s; relay is the weak point); writable-set destruction ≤30-min RPO; **DB backup lane still open** (Decision Pending); local API = full control (no native auth — never public); sudo-capable actors above the backstop (Mac has NO NOPASSWD surface today — keep it that way).

## Compatibility notes

All Stage-2 elements classified in `architecture-recommendation.md` §viii (rows 5–8, 11–12): macOS-only host state + macOS-only mechanisms (twins, LaunchDaemons) + cross-platform launcher patches whose VM no-op-ness is probe/default-verified. The VM's own Layer-1 arming (layer1 drop-in candidate) is a separate VM-side decision — not affected by this design.
