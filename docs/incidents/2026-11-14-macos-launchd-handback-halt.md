# macOS launchd Hand-back Halt Incident — 2026-11-14 (v0.30.5)

**Date:** 2026-11-14
**Environment:** macOS live install `~/agents-ensemble` (launchd gui/501, port 9797, PG `ensemble_prod`)
**Incident run:** `v0.30.5@2026-11-14T04:53:43Z` (fleet-autonomous promote, staged from worktree
`agents-ensemble-wt-stage-v03005` @ `5e6586cf3` = repo `latest` HEAD)
**Status:** Recovered by operator assistant 2026-11-14 17:07 +07 (bootstrap → boot sweep
`serving_green_commit`). Defects open for the repair lane.
**Source of truth (READ-ONLY forensic records):**
`/var/folders/3m/p68dltbj1xv7hjkdxr_gqnc00000gn/T/kilo/ensemble-incident-2026-11-14-v0.30.5-handback-halt.md`
(forensics) and `.../ensemble-incident-2026-11-11-upgrade-wedge.md` (precursor). This file is a
**dev-repo summary** — those records plus the live journal stay authoritative.

> **Root cause in one line:** the v0.30.5 promote correctly refused the nohup fallback
> (Amendment #1), stopped every daemon (including `launchctl bootout` of `com.ensemble.prod`),
> flipped `current` — then its launchd **hand-back bootstrap failed with no recorded error**,
> so it halted-for-human with the daemon stopped and unsupervised. Compounding: the previous
> v0.30.3 promote had "committed" while a **nohup'd daemon was the serving process** (the
> launchd-adopted tree crash-looped on EADDRINUSE for 9h) — the dual-supervisor race again.

---

## 1. Timeline (local +07; journal timestamps UTC in parens)

| Time | Event |
|------|-------|
| Nov 14 02:44–02:58 | Fleet promotes to v0.30.3; **commit 02:58:51** "gate+soak green" (19:58:51Z Nov 13) |
| 02:59:04 | First `EADDRINUSE` — launchd-adopted daemon can't bind; the **nohup'd daemon serves** :9797 all morning. /livez green, watchdog quiet — gate+soak had validated the wrong daemon |
| 06:41–11:31 | launchd launcher burst-abort cycle (crash #1→#4 → abort → re-arm → repeat); watchdog `launcher-abort` alerts every ~10 min |
| 11:30–11:42 | Active release mission (instance `1a9f3079`, "GIT CHOREOGRAPHY") cuts v0.30.5 itself: worktree at `5e6586cf3`, release staged to `releases/v0.30.5` at 11:51 |
| 11:52:37 | Promote refusal #1: `releases/0.30.5` (unprefixed name) does not exist |
| 11:53:15 | Promote refusal #2 from release-staged scripts: plugin-staleness module missing — FAIL-CLOSED |
| 11:53:37 | Promote re-run from the **worktree** scripts (rev `68c6ee055`) — passes (04:53:37Z) |
| 11:53:46 | Stop phase: TERMs launchd launcher; **bootouts `com.ensemble.prod`** (04:53:46Z) |
| 11:54:56 | **SIGKILL ×3** (pids 1131/1283/1332): the nohup tree holding :9797 — "survived bootout + 70s drain" — graceful checkpoint writes VOIDED (04:54:56Z) |
| 11:54:58 | **halt**: "unit hand-back failed for v0.30.5 — halt-for-human, txn left open (declared=launchd verified=LAUNCHD_MANAGED unit=com.ensemble.prod; no nohup fallback — Amendment #1)" (04:54:58Z) |
| 11:54:58 → 17:07 | **OUTAGE (~5h12m, halt-for-human by design)** — no daemon, launchd unit absent; watchdog absent-alert fires 12:11 |
| 17:07 | Operator-authorized recovery: `launchctl bootstrap gui/501` → v0.30.5 serving; boot sweep defers stale txn → verifies **serving green** → `serving_green_commit` (10:07:53Z); journal `current: v0.30.5`, `in_flight` cleared; FE 200, ready, interrupted mission resumed (`inflight_turns: 1`) |

**Impact:** ~5h12m downtime (deliberate halt-for-human window), zero queue loss
(11,217 done items intact; 3 `dead` items date to Jun–Aug), 3 checkpoint writes voided by
SIGKILL (resumed turns may replay from last committed node boundary).

## 2. Failure signature (for future recognition)

A failure in this class looks like:

- `com.ensemble.prod` **absent from the launchd domain** (`launchctl print` → "Could not find
  service"), plist file still present in `~/Library/LaunchAgents/`
- `releases/state.json`: last event `halt` with "unit hand-back failed", `in_flight` txn open,
  **post-flip** (`current` symlink = halted target, journal `current` = previous)
- No process on :PORT; watchdog logging `miss` + absent-notification
- That morning's precursor: hundreds of `EADDRINUSE` bind-retry lines in `launcher.err.log`
  + `launcher-abort` burst storm in `watchdog.log`, while `/livez` stays green
- `data/upgrade.log` NOT updated (the v0.30.x pipeline no longer writes it)

## 3. Root causes

1. **Commit validated the wrong daemon (v0.30.3).** The promote's start phase left a nohup'd
   launcher+daemon serving while ALSO adopting the launchd unit. Gate+soak passed on /livez
   answers from the nohup daemon; the launchd tree crash-looped on EADDRINUSE from one second
   after commit. The supervision check (`verified=LAUNCHD_MANAGED`) confirmed the unit existed,
   not that the unit's daemon was the one serving.
2. **Hand-back bootstrap failed silently.** The stop phase's `bootout` succeeded, the flip
   succeeded, the re-`bootstrap` failed — and the failure reason was not recorded anywhere
   (journal halt carries no launchctl error; `upgrade.log` unwritten by the new pipeline).
3. **Release-staged scripts incomplete:** `releases/v0.30.5/scripts/upgrade` lacked the
   plugin-staleness predicate module and refused FAIL-CLOSED; the working invoke came from a
   dev worktree. "Executor scripts must ship with the deployment" (Nov 11 lesson) is still untrue.
4. Minor: unprefixed version naming (`0.30.5` vs `v0.30.5`) burned a promote attempt.

## 4. Recovery performed (2026-11-14 17:07 +07)

1. Pre-checks: :9797 free, zero ensemble processes, plist lint OK
2. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ensemble-prod.plist`
3. Result: `launchd(1) → launcher → current/ensemble-prod` single tree;
   `/livez` = `0.30.5`; `/readyz` ready (all components, MCP all ok); FE HTTP 200
4. Boot journal sweep reconciled autonomously: `sweep_deferred` (ambiguous-absent) →
   in-process health read → **`serving_green_commit`** — the halted txn committed cleanly,
   no operator journal surgery needed

## 5. Open items for the repair lane

1. Serving-process attribution: gate/soak/supervision checks must verify the *supervised*
   daemon answers /livez (e.g. compare the launchd job's child pid against the port holder),
   not just that something answers.
2. Record the hand-back `launchctl bootstrap` stderr/stdout into the journal halt detail (or
   a per-run log file) — today's failure is unreproducible from the artifacts.
3. Ship complete upgrade scripts inside the release (plugin-staleness module), or make the
   promote refuse staging that omits them.
4. `supervision_boot state=LAUNCHD_NOT_VERIFIED ... "auto on Darwin could not verify a single
   l…"` at recovery boot — the daemon's supervision self-check cannot confirm launchd on
   Darwin even when the chain is genuinely `launchd → launcher → daemon` (ppid 1). Same
   verification family as the failed hand-back; cosmetic today (commit gate used in-process
   health) but should be fixed with #2.
5. Stale rollback locks accumulating in `releases/` (`rollback.lock.d.stale.1099`,
   `.stale.92146`) — need reaping.
6. macOS FYI: each promote's bootout/bootstrap cycle re-registers the LaunchAgent and triggers
   the macOS Background Items permission notification — expect a prompt per promote until the
   pipeline's registration settles.

## 6. Related incidents

- **2026-11-11** (`docs/…` forensics in tmp records): same dual-supervisor race class — promote
  stop/start vs launchd KeepAlive restart raced for :9797; version-verify mismatch →
  halt-for-human on a flipped symlink with v0.29.8 serving and wedged (journal-sweep boot bug).
  Amendment #1 ("no nohup fallback") dates from that incident and worked as designed today.
- **2026-09-28** (`2026-09-28-promote-cgroup-teardown.md`): executor survivorship designed on
  macOS launchd broke under systemd cgroups — the mirror image of this incident's supervision
  assumptions.

## 7. Recurrence — 2026-11-15 00:49–01:01 +07 (pre-flip executor self-kill)

Same family, new failure point. The fleet attempted to promote **v0.30.9** (staged 00:38,
worktree `agents-ensemble-wt-stage-v03009`):

| Time (+07) | Event |
|---|---|
| 00:49:01 | Promote from release-staged scripts → **refused FAIL-CLOSED (plugin-staleness module missing — defect #3, verbatim recurrence)** |
| 00:49:23 | Promote re-run from the worktree; plugin gate passes |
| 00:49:29 | `intent_flip` v0.30.9 |
| 00:50:53 | Daemon shut down **gracefully** (clean drain in logs), but the stop phase's SIGKILL **swept the promoting agent's own shell** (the executor runs as the agent's bash child inside the daemon's tree — exit -9, killing the promote mid-stop, **before the flip**) |
| 00:51–01:01 | Daemon down; launchd unit booted out and never handed back (executor dead); journal's last event is `intent_flip` — no terminal event, txn orphaned **pre-flip**; `current` still → v0.30.5 |
| 01:01 | Operator-authorized recovery: `launchctl bootstrap` → v0.30.5 serving (~10 min downtime) |
| 00:59:56 | Boot sweep (worked as designed): "orphaned pre-flip promote txn (target=v0.30.9, owner pid 52340, age 627s) **cleared — never flipped**"; `rollback.lock.d` stale-marked → `rollback.lock.d.stale.84635`; FE 200, ready, `in_flight: false` |

**New root cause for the ledger (#7): the promote executor is not detached from the daemon's
process tree on macOS.** The stop phase must kill the daemon tree (by design) — and on macOS
the executor lives inside it, so a promote that needs SIGKILL escalation kills itself before
completing or journaling a terminal event. The ensemble-vm dodges this via `systemd-run`
detachment; macOS needs the equivalent (a one-shot LaunchAgent / `launchctl submit` wrapper
for the executor). Note the asymmetry: Amendment #1 forbids nohup fallback for the *daemon*,
which is correct — the *executor* is the component that needs supervised detachment.

Also observed again: `supervision_boot state=LAUNCHD_NOT_VERIFIED` (defect #4) on the
recovery boot, despite a genuine `launchd(1) → launcher → daemon` chain.

### 7b. Second recurrence same night (01:03–01:15 +07) — ROOT CAUSE FOUND and fixed

The fleet retried v0.30.9 immediately after recovery. This run proves the incidents were
**not the agent's fault** — the agent lane had already fixed defect #7 by itself (detached
one-shot LaunchAgent `com.ensemble.promote-v03009`, RunAtLoad, log `/tmp/ens-promote-v03009.log`,
itself label-verbatim named). That executor survived the daemon stop, drained gracefully,
flipped `current → v0.30.9` — and then halted on the same hand-back failure as 2026-11-14
11:54. Executor log carried the exact error:

```
WARN: launchd hand-back: cannot resolve the VERBATIM plist path for job 'com.ensemble.prod'
(label ≠ filename) — failing (never label-derive); caller halts fail-safe
```

**Root cause of BOTH hand-back halts (and likely of the `LAUNCHD_NOT_VERIFIED` family): the
launchd plist filename must equal the job label.** The original macOS runbook deploy
(2026-11-09) installed `ensemble-prod.plist` (label `com.ensemble.prod`) and
`ensemble-watchdog-watcher.plist` (label `com.ensemble.watchdog`) — filename ≠ label, so the
pipeline's hand-back refuses to resolve the plist and halts fail-safe after every flip.

**Fix applied 2026-11-15 ~01:06 +07** (deployment layout, `~/Library/LaunchAgents` only):

- `ensemble-prod.plist` → `com.ensemble.prod.plist` (label-verbatim)
- `ensemble-watchdog-watcher.plist` → `com.ensemble.watchdog.plist` (label-verbatim)
- old mis-named plists removed; spent one-shot `com.ensemble.promote-v03009` bootout + plist
  removed (RunAtLoad would have re-fired the promote at next login — footgun)
- both jobs re-bootstrapped from the new names

**Result:** v0.30.9 serving under launchd within ~10 min; boot sweep `sweep_deferred`
(1-of-3 observations, ambiguous-absent) → **`serving_green_commit` at 18:14:11Z**: journal
`current: v0.30.9`, `previous: v0.30.5`, `in_flight: false`. FE 200, ready.

**Verification pending:** the next self-promote should now complete its hand-back cleanly.
If it halts again with the same warning, the hand-back may resolve the plist from a different
directory (e.g. the install's `data/` staged copy — which is still named `ensemble-prod.plist`;
the staged names would need the same rename at source).

## 8. Artifacts

- Journal: `~/agents-ensemble/releases/state.json` (halt 2026-11-14T04:54:58Z; commit 10:07:53Z)
- Serving daemon stdout to death: `data/launcher.log` (ends 11:54:49)
- Crash-loop stderr: `data/launcher.err.log` (2,055 EADDRINUSE lines, 02:59:04→11:52)
- Watchdog: `data/watchdog.log` (burst-abort storm 06:41–11:31; absent-alert 12:11)
- Staging worktree: `agents-ensemble-wt-stage-v03005` @ `5e6586cf3` (== repo `latest` HEAD)
- DB (read-only SELECTs): `ensemble_prod.instances` / `.event` / `.job_queue_items` /
  `.wedge_captures` — fleet healthy until 11:42, queue clean, no new wedges

## 9. Repair Lane (2026-11-14/15)

Families 1–3 shipped on branch `fix/ensemble-32-repair-lane` (base `bf4bfde6e`);
this section is family 4 (records). Each commit's own message is the
authoritative per-family gate list; this section is the cross-family ledger.

### 9.1 What shipped

- **Fix #1 + PC-1 + Fix #6 + §5.6 FYI** — `c28b64ec37bbe194b8979762294f184f6bf6add8`
  (`fix(launchd)`). Launchd plists renamed to label-verbatim names:
  `scripts/com.ensemble.prod.plist`, `scripts/com.ensemble.watchdog.plist`
  (labels unchanged); every normative reference updated across
  Makefile/scripts/tests/docs; demo guidance now mandates label-verbatim naming
  and documents the operator rename of the legacy
  `~/Library/LaunchAgents/ensemble-demo.plist` (host file untouched — this repo
  never touches it). PC-1 closes the halt killer: hand-back plist-path
  resolution no longer depends on the job being registered at resolve time.
  Three rungs — live `launchctl print` → classify-time path stash →
  deterministic `~/Library/LaunchAgents/<label>.plist` fallback under the
  ratified filename==label convention — each rung `-f`-verified, fail-closed.
  The stop phase bootouts the job *before* hand-back, so the print probe alone
  returned "Could not find service" → empty resolver → WARN → halt (§3.2).
  Fix #6: age-bounded reap of `rollback.lock.d.stale.*` siblings (mtime-based,
  env-overridable `ROLLBACK_LOCK_STALE_PRUNE_S`, default 7d, garbage→default)
  at all three `lock_acquire` sites (lib.sh, launcher.sh, upgrade_journal.py),
  shell/Python twins mirrored, reaps logged.

- **Fix #2 + Fix #4 + PC-2** — `b7c9403a1d1a76fc5718e42c4c76958cfd182818`
  (`fix(supervision)`). Every promote/restart/rollback/status gate and the
  hand-back serving probe now verify the *supervised* daemon answers, not
  merely the port: an attribution conjunct after curl-green requiring the
  port-holder pid (lsof LISTEN census) to belong to the supervision-owned pid
  set (launchd lineage on darwin, systemd MainPID path on linux). Fail-closed:
  mismatch refuses (`gate_fail_reason` → 8b auto-rollback), never warns-and-
  passes. Adds the missing launchd arm to `supervision_dualfight_check` Check
  B (darwin previously returned 0 immediately) and requires owned-pid
  membership in promote flip-verify (a crash-looping launcher no longer
  satisfies "any new pid != prestop mainpid"). Council-caught CRITICAL
  (membership normalization): the owned set is newline-joined but the case
  match was space-delimited, so the healthy production shape (launcher+daemon
  both anchored) false-refused; normalize once before matching (lib.sh +
  promote.sh flip-verify); refusal details emit the space-flattened list
  (journal single-line contract); unit-arm holders word-split per holder.
  Pinned both directions by pack leg M18 (two-pid owned set: holder=daemon
  passes, foreign pid refuses). Fix #4: hand-back error capture — bootstrap
  output + resolver stderr stashed in `HANDBACK_LAST_ERROR`, surfaced in WARN
  lines, appended to the three halt templates so journal halt detail carries
  launchctl stderr; stash cleared on success and on the benign
  already-bootstrapped arm. PC-2: `launcher.sh` exports `INSTALL_DIR`; the
  sweep service passes its own install dir into the Python detect path (no
  os.environ dependence), so plist-lineage daemons no longer structurally lack
  `INSTALL_DIR` — closes the `LAUNCHD_NOT_VERIFIED` false-positive class.
- **E2E round-1 defects (D-1/D-2, demo promote 2026-11-15)** — this commit.
  D-1: flip-verify boot-grace — the Fix #2-ii census false-fired on a
  healthy migration-heavy boot (~15-20s boot-to-listen) and auto-rolled
  back mid-boot; the census moved into `lib.sh
  _flip_verify_port_attribution` with a bounded listen-grace poll
  (`FLIP_VERIFY_LISTEN_GRACE_S`, default = `LIVEZ_BUDGET_S`; foreign
  holders still fail immediately — no grace for wrong-process). D-2:
  config-compat ENTRY check (before stop/flip) — slice modules
  `daemon/plugin_subsystem/config_compat_{check,contract}.py`, stage-time
  contract mint, exit-78 refusals `config-incompatible` (remedy: "migrate
  config or pin a compatible release") and `config-compat-unrunnable`
  (fail-closed); demo runbook §10.1 carries the config-currency note; the
  per-release config transport structural fix is DEFERRED (§9.4(e)).

- **Fix #3 + Fix #5 + §10 runbook policy** —
  `6b2f2c1911e06195998c2a54d8b1de88924abd19` (`fix(upgrade)`). Fix #3:
  release payload completeness — stage the `daemon/plugin_subsystem/
  promote_staleness.py` slice at payload top-level (minimal import closure) so
  lib.sh's `<lib.sh-dir>/../../daemon/plugin_subsystem` resolution works from
  the staged payload; manifest/hash/stamp-gate coverage added; a fail-closed
  staging assertion refuses to build a payload whose promote would fail its own
  staleness predicate (exit 78, no warning) — closes the defect-#3 class that
  killed the v0.30.5 and v0.30.9 release-staged promotes at 11:53:15 and
  00:49:01 (§3.3, §7). Fix #5: darwin executor self-detach via a transient
  one-shot LaunchAgent (`com.ensemble.promote-<runid>`, label-verbatim) with
  mandatory bootout+rm on success *and* failure and exit-code propagation;
  Amendment #1 untouched — only the executor detaches. Stop/kill sweeps gain
  identity-only self-exclusion (`$$`, `$PPID`, in-tree `$PPID`'s parent) — a
  pgid filter would drop the daemon (same launchd job group), verified by the
  release_journal 16d TERM pin. Runbook §10 invoke-path policy: release-staged
  PRIMARY, `ENSEMBLE_UPGRADE_SCRIPTS_DIR` pinned worktree = recovery/escape
  hatch (§10.1–§10.4).

### 9.2 New lane invariants

(i) **Filename == label for LaunchAgents.** The plist filename must equal the
job label (`com.ensemble.prod.plist` for `com.ensemble.prod`, etc.). The
pipeline never derives a path from a label and never touches the host file:
demo rename of the legacy `ensemble-demo.plist` is operator work, documented
not performed. This is the invariant whose violation produced both hand-back
halts (§7b).

(ii) **Darwin executor self-detach.** A promote must never run inside the
daemon process tree on darwin; the executor re-execs itself in a transient
one-shot LaunchAgent and survives the launchd bootout of the daemon job. The
daemon itself stays launchd-supervised (Amendment #1 unchanged — it forbids
nohup fallback for the *daemon*, and the *executor* is the component that
needs supervised detachment).

### 9.3 Test evidence

- `tests/test_supervision_stop_handback.sh` 93P/0F/4S (family 1: 36P L
  section — post-bootout resolution, fallback rung, fail-closed no-bootstrap;
  family 2: M15–M19 + N4–N5 — foreign-pid refuse legs per gate class,
  production two-pid shape both directions, driven status legs, stash-clear
  pins).
- `tests/test_supervision_twins.sh` 61P/0F (PC-2 D-pins).
- `tests/unit/tools/test_supervision_python.py` 98P (incl. 9g).
- NEW `tests/test_promote_launchd_survivorship.sh` 44P/0F + python twin
  26P/0F (darwin survivorship mirroring the 2026-09-28 cgroup-teardown
  guarantee; plist cleanup on success/failure legs).
- `tests/test_executor_family_payload.sh` 70P/0F (+17 slice/entry-check legs).
- `tests/test_stage_plugins_tree.sh` 38P/0F (was 14P/17F; fixture seam repair
  + slice legs).
- `tests/test_rollback_lock_reap.sh` 27P/0F.
- `tests/unit/tools/test_upgrade_journal.py` 145P/1S (new
  `TestStaleSiblingPrune`).
- `tests/unit/test_makefile_install_guard.py` 162P/1S.
- Red baselines (pre-existing, A/B-proven at base `bf4bfde6e`, 0
  branch-caused): `tests/test_launcher.sh` 36F; `tests/test_release_journal.sh`
  4F {8c×3, 12c}; `tests/test_promote_cgroup_survivorship.sh` 6F
  {6a, 4a, 4b, 4c, 7b, 7c}; `promote_soak_fail_fast` 4F + `never_green` 4F
  (Darwin supervision-preflight sandbox class); `stage_freshness_guard` 26F
  (host-fence class); guidelines FATAL D7 (host-fence); `supervision_journal`
  10F. POK entry: `ens32-f1-redset-ab-attribution`.

### 9.4 Records

Plane filing attempted for the ENSEMBLE-32 follow-up repair lane but
`plane_sync_state=error` (400 "Project name cannot contain special
characters") — issue text preserved here for later filing; never block on PM
tooling.

Deferred items (my rulings):

(a) Darwin sandbox-promote supervision posture (hermetic launchctl emulator
in the soak fixture vs `UP_TARGET=sandbox` preflight exemption) — out of lane
scope, pre-existing.
(b) `grep -oP` BSD-port debt in `test_adaptive_soak.sh` `_stop_stub` —
cosmetic, pre-existing, ledger entry.
(c) `handback_verify_budget` Linux-only legs — seam driver-proven; Linux-CI
run DEFERRED → tester.
(d) Minor council notes carried: daemon-slice stamp-gate dedicated negative
leg (optional hardening), python-twin 6g pin precision, dual-SIGKILL
spent-plist residual (accepted, documented in §7b).
(e) **Per-release config transport** (E2E round 1, D-2 deferred future item):
`config.yaml` does not travel with releases (only `launcher.sh` swaps
per-release), so rollback targets can become unbootable across a
config-format boundary (the 2026-11-15 demo promote halted on exactly this:
v0.10.8 binary vs refreshed v0.30-era config, pydantic `extra_forbidden`,
re-gate exhausted 180s). This lane shipped the MINIMAL fail-closed guard
(promote ENTRY config-compat check + stage-time contract mint — runbook
§10.1); the STRUCTURAL fix — config traveling in the payload per-release —
is deferred to a future lane. Also recorded (R-1, verification-only): a job
run BY the demo daemon calling `system_upgrade(user_confirmed=true,
dry_run=false)` spawns the promote executor via manager.py's sole spawn seam
(descends from the demo daemon's tree ⇒ §7 self-detach fires E2E); no
ADR-017/live-rung constraint blocks the daemon lane on a DEMO install.

### 9.5 Open items carried (not closed by this lane)

- §5.1 serving-process attribution — closed by Fix #2.
- §5.2 hand-back error recording — closed by Fix #4.
- §5.3 release-staged script completeness — closed by Fix #3.
- §5.4 `LAUNCHD_NOT_VERIFIED` — closed by PC-2.
- §5.5 stale rollback locks — closed by Fix #6.
- §5.6 Background Items FYI — documented (§5.6 above).
- §7 pre-flip executor self-kill — closed by Fix #5.
- §7b label≠filename halt — closed by Fix #1 + PC-1.

No product-code or test-code changes in this family; only this doc,
`CHANGELOG.md`, and `docs/runbooks/promote-handback-serving-green-closer.md`.
