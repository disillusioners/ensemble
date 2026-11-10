# G5 — Stage 1 Relocation: `~/agents-ensemble` → `/opt/ensemble`

**Status:** PREPARE-ONLY gated checklist. The OPERATOR executes every step from a Terminal OUTSIDE the daemon. No agent — including lanes inside the live daemon — ever runs `launchctl bootout/bootstrap/kickstart` for ensemble jobs (self-kill, proven exit −9).
**Gate (two-factor, per the gate-convention matrix — this op is service-interrupting):** `ENSEMBLE_STAGE1_MOVE_LIVE=1` env **+** `--confirm-move` flag on the move script (developer implements the script; until then the operator treats this document as the script).
**Env-var naming (repo convention):** `ENSEMBLE_<VERB>_LIVE=1` — `ENSEMBLE_DEPLOY_LIVE` on the `deploy.sh` lane, `ENSEMBLE_INSTALL_LIVE` on the `make install` lane; this move gate uses the same shape. The confirm var states INTENT only — it never implies a path (the path comes from `INSTALL_DIR`).
**Result of Stage 1:** tree at `/opt/ensemble`, **operator-owned** (root ownership is Stage 2), plists edited in place, daemon re-bootstrapped, demo untouched.
**Evidence base:** worker `7989fe00` host-verified failure-mode stress (same-volume ✓, substring hazard ✓, state-file path-neutrality ✓, `.env` census ✓); rehearsal-a live proofs [RA]; **corrections + lossless/round-trip proofs from rehearsals b+c (2026-11-09, demo env)** [RB]/[RC]. Companion model: `architecture-recommendation.md`.

---

## Phase 0 — DEMO REHEARSAL FIRST (first-use protocol, first-class)

Rehearse the ENTIRE move sequence against a throwaway clone — zero contact with live or demo trees:

- [ ] R1. `cp -a ~/agents-ensemble-demo /tmp/ensemble-stage1-rehearsal` (allowed write surface: /tmp).
- [ ] R2. Edit the clone's staged plist (`/tmp/ensemble-stage1-rehearsal/data/ensemble-prod.plist`) with the SAME anchored sed as Phase 2, retarget: install path → `/tmp/ensemble-stage1-rehearsal`, label → `com.ensemble.rehearsal.prod` (MUST NOT collide with real labels), port → a rehearsal port (e.g. 8199) via the clone's `.env`.
- [ ] R3. From the OPERATOR Terminal: `launchctl bootstrap gui/$(id -u)` the rehearsal plist; verify state=running + `/livez` on the rehearsal port (era caveat: release ID via `readlink current` + binary sha256 — see 4.2; the binary's self-reported version is NOT a release discriminator [RC-F2]).
- [ ] R4. Run the FULL Phase 2–4 sequence against the rehearsal clone (rename to `/tmp/ensemble-stage1-rehearsal-moved` — mv onto a NONEXISTENT target, per corrected 2.1/2.2 — in-place plist sed with the literal-prefix anchor, `plutil -lint` + the 2.6 path assert, a PlistBuddy **human** before/after diff (never a programmatic compare — see 2.6), bootout → bootstrap, verification battery, then the rollback sequence). Every guard below must fire or the rehearsal FAILS.
- [ ] R5. Bootout the rehearsal job; `rm -rf /tmp/ensemble-stage1-rehearsal*` (named, /tmp-scoped — legitimate); confirm zero residue: `launchctl print gui/$(id -u)/com.ensemble.rehearsal.prod` → "No such process".
- [ ] R6. Rehearsal transcript archived to `.agents/shared/planning/macos-deployment-topology/rehearsals/` (alongside rehearsal-a).

**Gate to Phase 1:** rehearsal passed end-to-end INCLUDING rollback, and the operator names the drill in the go-decision.
**Rehearsal record (2026-11-09):** the real-tree variant of this drill (demo ⇄ home-sibling target, operator-executed) PASSED end-to-end including rollback — `rehearsals/rehearsal-b-relocation.md` (lossless move: 1415-file sha manifest identical; rollback byte-identical). The corrections folded into 2.1/2.2/2.6/4.2 below come from that drill — follow the CORRECTED steps.

## Phase 1 — Preconditions & evidence capture (read-only)

- [ ] 1.1 Live daemon healthy: record `launchctl print gui/$(id -u)/com.ensemble.prod | grep -E 'state|pid'` (expect state=running + pid; pids observed at design time: 51569/51591) and `curl -s http://127.0.0.1:9797/livez`.
- [ ] 1.2 Layout census (expected, host-verified at design time): flat tree — `launcher.sh` + `ensemble-prod` at root; **NO** `.launcher-state`, NO `.sweep-defer-*`, NO `releases/` (journal dormant). If ANY of these now exist → STOP: re-verify state-file path-neutrality (worker-verified for the pure-number format, launcher.sh:2468-2470/:866-893) before proceeding.
- [ ] 1.3 **Same-volume check (hard refuse if unequal):** `stat -f %d ~/agents-ensemble` vs `stat -f %d /opt` — both `/dev/disk3s5` at design time (rehearsal-b re-proved the method: 16777232 == 16777232 ✓). Unequal ⇒ `mv` silently degrades to copy+delete (hours, inconsistent) — DO NOT PROCEED.
- [ ] 1.4 Plist inventory: `ls -l ~/Library/LaunchAgents/ | grep ensemble` — expect `ensemble-prod.plist`, `ensemble-watchdog-watcher.plist`, `ensemble-demo.plist`. **Only the FIRST TWO are ever edited.** 🔴 Substring hazard (host-verified; rehearsal-b exercised the inverted direction — live path is a PREFIX of the demo anchor — and held): `/Users/nguyenminhkha/agents-ensemble` is a PREFIX of `/Users/nguyenminhkha/agents-ensemble-demo` — a glob `sed` over `*.plist` would corrupt the demo plist into `/opt/ensemble-demo`. Never glob. Never touch `ensemble-demo.plist`.
- [ ] 1.5 Pre-edit assertion on both edit-target plists: `grep -c 'agents-ensemble-demo' <file>` → **0** (proves the anchored sed cannot hit a demo path inside them).
- [ ] 1.6 Byte-state baselines: `cp -p` each target plist to `<name>.bak-stage1` AND `shasum -a 256` both; same for the staged copies inside the tree (`data/ensemble-prod.plist`, `data/ensemble-watchdog-watcher.plist` — they carry the substituted OLD install path from Makefile:296-297/:303-304 and must be updated too). Also baseline the supervisor PATH value: `plutil -extract EnvironmentVariables.PATH raw -o - -- ~/Library/LaunchAgents/ensemble-prod.plist | shasum -a 256` (verified on this macOS 14.4.1: returns the BARE value). **The watchdog plist has NO `EnvironmentVariables` key** — that keypath errors there ("No value at that key path") and that is EXPECTED for that file, not a failure.
- [ ] 1.7 `.env` census recorded: `.env` contains ZERO path-valued keys (design-time census + rehearsal-b re-proof: `grep -c 'agents-ensemble-demo' .env` → 0) — nothing to edit inside `.env`. Re-verify with `grep -n 'agents-ensemble' /opt/ensemble/.env` AFTER the move (expect 0).
- [ ] 1.8 Read-only reference census (report-only, no action): `grep -rl 'agents-ensemble' ~/Library/LaunchAgents/ ~/.zshrc ~/.zprofile 2>/dev/null` — record anything found for the operator's own awareness.
- [ ] 1.9 (Optional, recommended — rehearsal-b F2 method) Pre-move tree manifest: relpath+sha256 of every file + `du -sk` (~seconds for ~227 MB) — gives 2.3 a hard lossless guarantee instead of `ls` spot-checks.

## Phase 2 — The move (operator Terminal, sudo)

- [ ] 2.1 **Target-freshness TEST ONLY — do NOT `mkdir` the leaf.** 🔴 Rehearsal-b F1 (proven in /tmp): pre-creating the target makes `mv tree target` NEST the tree one level inside it on BSD (`/opt/ensemble/agents-ensemble/…`) — 2.3's verify would fail with the operator mid-outage. Instead assert the target is ABSENT: `[ ! -e /opt/ensemble ] && echo FRESH || { echo EXISTS-STOP; exit 1; }`. Sanity only: `ls -ld /opt` (root-owned parent, not SIP-protected; `/opt/homebrew` coexists).
  - ⚠️ If `/opt/ensemble` EXISTS → STOP. Investigate; never merge into a pre-existing dir, never blind-`rmdir` it.
- [ ] 2.2 `sudo mv /Users/nguyenminhkha/agents-ensemble /opt/ensemble` — mv onto the **NONEXISTENT** target path: same-volume `rename(2)`, atomic, CREATES the leaf, preserves ownership/modes. **Lossless-move proven** (rehearsal-b: 1415-file sha manifest byte-identical across the rename, `du` identical). `sudo` is required ONLY because `/opt` is a root-owned parent — a home-sibling drill move needs no sudo anywhere (rehearsal-b F6, the one live-vs-drill difference); everything else transfers 1:1. The RUNNING daemon keeps working through moved inodes (open fds); absolute-path opens will ENOENT until Phase 3/4 — the window is minutes, and bootout/bootstrap closes it.
- [ ] 2.3 Verify: `ls /opt/ensemble/launcher.sh /opt/ensemble/ensemble-prod` exist as DIRECT children (NOT under `/opt/ensemble/agents-ensemble/` — the nesting signature of the 2.1 trap); `ls ~/agents-ensemble` → No such file or directory. If 1.9's manifest was taken: re-run and diff → identical count + total sha (rehearsal-b's marker-A==marker-B proof shape).
- [ ] 2.4 **In-place plist edits — the PATH-PRESERVATION TRAP.** The installed plists' `EnvironmentVariables.PATH` contains `/Users/nguyenminhkha/.nvm/versions/node/v22.21.1/bin` which MUST survive verbatim (it is REQUIRED for MCP spawning — rehearsal-a A/B proof; the value contains no `agents-ensemble` substring, host-verified, so the anchored sed cannot touch it — **preservation rehearsal-proven: the PATH value stayed byte-identical (shasum) across the anchored sed AND the round-trip, and context7 booted healthy at the drill path [RB]**). Edit ONLY the two named files, with the literal-prefix anchor and BSD sed's empty-suffix in-place form:
  ```bash
  for f in ~/Library/LaunchAgents/ensemble-prod.plist ~/Library/LaunchAgents/ensemble-watchdog-watcher.plist; do
    sed -i '' 's|/Users/nguyenminhkha/agents-ensemble|/opt/ensemble|g' "$f"
  done
  ```
  Anchors rewritten (verify each before/after — `PlistBuddy -c 'Print :<key>'` is fine for a HUMAN diff; its array dump carries `Array { … }` structure noise, so NEVER compare it programmatically — use `plutil -extract … raw`, see 2.6):
  - `ensemble-prod.plist`: `ProgramArguments[1]`, `WorkingDirectory`, `StandardOutPath`, `StandardErrorPath`
  - `ensemble-watchdog-watcher.plist`: `ProgramArguments[1]` (script path — resolves via the tree) and `ProgramArguments[2]` (the explicit INSTALL_DIR arg), `StandardOutPath`, `StandardErrorPath`
- [ ] 2.5 **Staged copies inside the moved tree** — same anchored sed on `/opt/ensemble/data/ensemble-prod.plist` and `/opt/ensemble/data/ensemble-watchdog-watcher.plist` (they are the `make plist-install` source of truth, Makefile:331-345).
- [ ] 2.6 **Lint + path assert on BOTH installed plists — THREE DISTINCT VERDICTS, never a false "stale path".** Tool choice: **`plutil -extract <keypath> raw -o - -- <plist>`** — verified on this macOS 14.4.1 to return the BARE path/value (no quotes, no structure) for `ProgramArguments.1`, `ProgramArguments.2`, `WorkingDirectory`, `EnvironmentVariables.PATH`; it exits non-zero with a distinct message for an absent key/index ("No value at that key path or invalid key path") and for a malformed file ("Property List error"). A bare `PlistBuddy -c 'Print :ProgramArguments:1'` compare is NOT used: `Print` of an array emits `Array { … }` structure noise, an absent key yields an empty/error capture, and a missing binary yields "command not found" — all three collapse into a FALSE "stale path" verdict on a healthy plist.
  - **TOOL MISSING** (abort, exit 78): `/usr/libexec/PlistBuddy` (ABSOLUTE path — a bare `PlistBuddy` can resolve to a shell stub) and `plutil` must be present BEFORE any verdict.
  - **UNREADABLE KEY / MALFORMED PLIST** (exit 1): the key or index is absent, or the file is not a plist. Distinct from stale — never "fix" a healthy plist on this verdict.
  - **STALE PATH** (exit 1): a readable path that is not the expected one for THIS step.
  ```bash
  # 2.6 — path-extraction assert (tool-missing / unreadable / stale are DISTINCT)
  [ -x /usr/libexec/PlistBuddy ] || { echo "TOOL MISSING: /usr/libexec/PlistBuddy"; exit 78; }
  command -v plutil >/dev/null 2>&1 || { echo "TOOL MISSING: plutil not on PATH"; exit 78; }

  plist_get() {   # $1=plist $2=keypath -> bare value; non-zero exit if the key is absent/malformed
    out="$(plutil -extract "$2" raw -o - -- "$1" 2>/dev/null)" || return 1
    printf '%s' "$out" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'
  }
  assert_under() {   # $1=label $2=plist $3=keypath
    v="$(plist_get "$2" "$3")" || { echo "UNREADABLE KEY $3 in $2 (absent key/index or malformed plist — NOT a stale-path verdict)"; exit 1; }
    case "$v" in
      "$EXPECT_ROOT"|"$EXPECT_ROOT"/*) [ -e "$v" ] || { echo "STALE PATH $3 -> $v (under $EXPECT_ROOT but absent on disk)"; exit 1; } ;;
      *) echo "STALE PATH $3 -> $v (expected under $EXPECT_ROOT)"; exit 1 ;;
    esac
  }
  EXPECT_ROOT=/opt/ensemble     # POST-move root (RB.4 runs the same block with the PRE-move root)
  assert_under prod ~/Library/LaunchAgents/ensemble-prod.plist             ProgramArguments.1  # <root>/launcher.sh
  assert_under prod ~/Library/LaunchAgents/ensemble-prod.plist             WorkingDirectory   # <root>
  assert_under wd   ~/Library/LaunchAgents/ensemble-watchdog-watcher.plist ProgramArguments.1  # <root>/scripts/watchdog-watcher.sh
  assert_under wd   ~/Library/LaunchAgents/ensemble-watchdog-watcher.plist ProgramArguments.2  # <root> (INSTALL_DIR arg)
  echo "PATH ASSERTS OK (expected root: $EXPECT_ROOT)"
  ```
  → must print `PATH ASSERTS OK` and ZERO stale/unreadable lines. Per-file key shapes (verified on the installed plists): the prod plist has ProgramArguments[0]=`/bin/bash`, [1]=launcher.sh and a `WorkingDirectory`; the watchdog plist has [0]=`/bin/bash`, [1]=watchdog-watcher.sh, [2]=INSTALL_DIR and **NO** `WorkingDirectory` and **NO** `EnvironmentVariables` — do NOT assert those keys there (an absent key is "unreadable", not stale). `plutil -lint` (exit 0) is RETAINED as a necessary-but-BLIND pre-check (it cannot see a stale path — rehearsal-b F3) · `grep -c 'agents-ensemble'` on each plist → **0** · `EnvironmentVariables.PATH` (prod plist only) → shasum UNCHANGED vs the 1.6 baseline (nvm path intact).

## Phase 3 — Supervisor reload (OUTSIDE the daemon — operator Terminal ONLY)

🔴 Never from inside the daemon (exit −9 self-kill). launchd caches the plist at bootstrap, so edited jobs MUST be re-bootstrapped, not just re-kicked:

- [ ] 3.1 `launchctl bootout gui/$(id -u)/com.ensemble.watchdog` then `launchctl bootout gui/$(id -u)/com.ensemble.prod`
  - GAP-2 fact (live-proven [RA], re-observed ×4 in rehearsals b/c with TERM→exit 143 in 1–2 s): bootout KEEPS the job down (no restart at t+28 s with ThrottleInterval 10); the launcher exits 143 on graceful stop. A bare SIGTERM would re-engage KeepAlive 10 s later — that is why stop = bootout ONLY.
- [ ] 3.2 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ensemble-prod.plist` then the watchdog plist.

## Phase 4 — Verification battery (era caveats apply [RA][RC])

- [ ] 4.1 `launchctl print gui/$(id -u)/com.ensemble.prod | grep -E 'state|pid|program'` → state=running, fresh pid, program = `/opt/ensemble/launcher.sh`.
- [ ] 4.2 `curl -s http://127.0.0.1:9797/livez` → 200. **Era caveat (rehearsal-c F2, proven): version strings COLLIDE across releases in this era** — v0.10.7-p2.3 and v0.10.8-p2.3 both self-report `"version":"0.10.5"`, and the embedded string lags release tags generally — the `/livez` version is NOT a release discriminator. **Release ID = `readlink current` + binary sha256** wherever the releases/ layout exists; never gate a flip/move verification on the version string alone.
- [ ] 4.3 `curl -s http://127.0.0.1:9797/readyz` → all-green. **Component set:** `database`, `queue_freshness`, `services`, `checkpoint_saver`. **MCP is ADVISORY — reported under `detail.mcp` and NEVER gates status** ⇒ `/readyz` is NOT MCP proof (on older binaries it carries no MCP component at all). MCP health (context7 / webfetch / plane) is evidenced separately in `/opt/ensemble/data/launcher.err.log` — stderr is the daemon log surface under launchd (rehearsal-b capture: `Context7 … v4.6.0 running on stdio` + `healthy: True` proves the preserved PATH works at the moved path). Grep for MCP server startup lines.
- [ ] 4.4 Watchdog: `launchctl print gui/$(id -u)/com.ensemble.watchdog` → running, `ProgramArguments[2]` = `/opt/ensemble`; its log shows a successful pass against the new path.
- [ ] 4.5 Old path absent: `ls ~/agents-ensemble` → No such file. Demo untouched: `shasum` `ensemble-demo.plist` == baseline; `launchctl print gui/$(id -u)/com.ensemble.demo` state unchanged (booted-out per rehearsal-a/c end-state); demo tree listing unchanged.
- [ ] 4.6 `.launcher-state`: absent today is CORRECT (pure-number state is written on supervised child exits, launcher.sh:2468-2470). After the first crash/stop event, confirm it appears at `/opt/ensemble/.launcher-state` (pre-Stage-2 default) — nothing else to do; relocation with the tree is by design.
- [ ] 4.7 Transcript: operator archives command log + outputs to `.agents/shared/planning/macos-deployment-topology/` (stage1-execution-<date>.md).

## Rollback — exact inverse restoring today's byte-state

Safe at any point; each step is the inverse of its forward step:

- [ ] RB.1 `launchctl bootout gui/$(id -u)/com.ensemble.watchdog` + `launchctl bootout gui/$(id -u)/com.ensemble.prod` (if bootstrapped on new paths).
- [ ] RB.2 `sudo mv /opt/ensemble /Users/nguyenminhkha/agents-ensemble` — target absent ⇒ true atomic rename back (the same corrected mv-onto-nonexistent shape as 2.2; rehearsal-proven in both directions).
- [ ] RB.3 Restore plists from the `.bak-stage1` copies (`cp -p` back) — **byte-state restore, NO reverse-sed** — for both installed plists AND the staged copies inside the tree. **Rollback byte-identity proven** (rehearsal-b: `cp -p`/inverse-sed round-trip both restored shasum-identical files; `cp -p` stays canonical — zero pattern risk).
- [ ] RB.4 `launchctl bootstrap` both from the restored plists; re-run the 2.6 assert with `EXPECT_ROOT=/Users/nguyenminhkha/agents-ensemble` (the **PRE-move** root — the comparison follows the step, not a fixed value) + Phase 4 battery (expect all green on the OLD path).
- [ ] RB.5 Demo + `/opt` residue check: `ls /opt` → no `ensemble` entry.

## Silent half-move inventory (know these before you start)

| Hazard | Detection | Severity |
|---|---|---|
| (a) Plists edited but jobs NOT re-bootstrapped | Jobs keep running on old inodes; looks healthy until next reboot → boot failure | 🔴 masked until reboot — Phase 3 is mandatory, not optional |
| (b) Tree moved but plists NOT edited | Next launchd load invokes a missing program → persistent boot-fail loop. **`plutil -lint` does NOT catch this (lint is syntax-only — rehearsal-b F3); the 2.6 path assert is the guard** | 🔴 the worst state — Phase 2.4–2.6 must complete before any reboot |
| (c) Watchdog left on old INSTALL_DIR arg | Watcher `cd` fails → FATAL exit 2 (watchdog-watcher.sh:126-130); transient log noise; possible one `/livez`-absent notification (>600 s) | 🟡 expected transient during the window |
| (d) Staged in-tree plist copies left stale | Next `make plist-install` would install old-path plists | 🟡 Phase 2.5 |
| (e) External rc/scripts referencing the old path | Phase 1.8 census (report-only) | 🟢 operator awareness |
| (f) Target pre-created (mkdir) before the mv | `mv tree target` NESTS the tree inside the target on BSD — silent, exit 0 (rehearsal-b F1, /tmp-proven) | 🔴 eliminated by design — 2.1 is a freshness TEST only, never a mkdir |
| (g) Assert cries wolf (missing tool / absent key read as "stale path") | Operator "fixes" a HEALTHY plist — or trusts a real stale path that was masked | 🟡 eliminated by design — 2.6 separates TOOL MISSING / UNREADABLE KEY / STALE PATH, and uses `plutil -extract … raw` (bare value) rather than a PlistBuddy compare |
