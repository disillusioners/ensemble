# Rehearsal (d) — the `/usr/local`-shaped quick-start install target

**Task:** wi-52666630 · commission `macos-deployment-topology` · branch `feature/macos-deployment-topology`
**Date:** 2026-11-09, 23:00–23:03 UTC (06:00–06:03 local +07)
**Result: SPLIT —** 🔴 **the literal `/usr/local` leg is BLOCKED-ON-OPERATOR with a hard finding (subdir creation needs sudo on this box — the "admin-writable, no sudo" assumption is FALSE)**; ✅ **the drill's substance (non-`$HOME` user-owned install root) PASSED end-to-end on an authorized `/tmp` surrogate** with a distinct drill label `com.ensemble.drill`, full write-location proof, and a third independent MCP/PATH confirmation.
**Demo install: byte-identical after the drill (copy, never move). Demo plist + prod plists untouched. Prod healthy throughout.**

---

## 1. 🔴 FINDING D-F1 — `/usr/local` is root:wheel 755 on stock macOS; sudo is REQUIRED, not optional

The drill's first command is the quick start's own first command, run honestly as the admin user with **no sudo**:

```
$ ls -ld /usr/local
drwxr-xr-x  10 root  wheel  320 Mar 10  2026 /usr/local
$ id -Gn → … staff everyone localaccounts … admin _appserveradm …   (user IS in admin)
$ mkdir /usr/local/ensemble-drill
mkdir: /usr/local/ensemble-drill: Permission denied
```

- Group is **wheel**, not admin, and there is no group-write bit → an admin user cannot create subdirs in `/usr/local` on this stock ARM Mac (Homebrew lives in `/opt/homebrew`, so nothing ever chowned `/usr/local`).
- Volume note: `stat -f %d` → `/usr/local`, `/tmp`, and `$HOME` all report **16777232** (same volume) — the blocker is **purely permissions**, not volume topology.
- Consequence for the canonical doc: the quick start's **one-time `sudo mkdir -p /usr/local/ensemble` + `sudo chown "$USER"` step is MANDATORY** and must stay in the doc. The parenthetical "creating a subdir there needs no sudo" is wrong on stock macOS and would strand a reader at step 1 with EACCES. Per the mission's escalation rule, I stopped the `/usr/local` leg rather than sudo-ing.

**Surrogate decision (documented):** the drill's substance — a **non-`$HOME`, user-owned install root** — was still provable on `/tmp/ensemble-drill` (an explicitly authorized surface, no sudo anywhere). Everything below exercises exactly the quick-start mechanics short of the root:wheel permission itself.

## 2. Surrogate prep — fidelity-proved copy (COPY, never move)

```
mkdir /tmp/ensemble-drill            → OK as nguyenminhkha, no sudo
cp -a ~/agents-ensemble-demo/. /tmp/ensemble-drill/    (exit 0)
readlink /tmp/ensemble-drill/current → releases/v0.10.8-p2.3-b7cyc1   (symlink survived cp -a ✓)
du -sk → 227008

Spot-sha fidelity (demo == copy):
  launcher.sh            1e6a35fc86b26bed6a28d5eec6e4b829cda1dd13475611e74f8d7eca4eb15ffa
  current/ensemble-prod  bd740467e7d312a28cd4cb1c41d92254207236a4c14a9611aea94e2cf8823838
  releases/state.json    e70c5a8a781b0ddcd568b0cc48eae884f2dcea32cc19f69eeaebba15b6164ed6
.env PORT (copy): 22:PORT=7979    (nothing touches :9797)
Demo baseline snapshot for the untouched-check: /tmp/reh-d/demo-baseline.txt
  (incl. demo launcher.err.log 2ced903a… · .launcher-state ac663cbd… · demo plist 5e2bf45c…)
```

## 3. The drill plist — `~/Library/LaunchAgents/ensemble-demo-drill.plist` (DISTINCT name; label `com.ensemble.drill`)

Same shape as the demo/prod plists: `ProgramArguments` = `/bin/bash` + `/tmp/ensemble-drill/launcher.sh`; WorkingDirectory `/tmp/ensemble-drill`; `RunAtLoad true`; `KeepAlive {SuccessfulExit=false, Crashed=true}`; `ThrottleInterval 10`; std paths → `/tmp/ensemble-drill/data/launcher{,.err}.log`; PORT from the copied `.env` (not in the plist). **Deliberately NO `EnvironmentVariables` block** — the mission's honest-capture instruction: a fresh quick-start reader gets the template plist, and we record what that actually does to MCP.

Asserts before bootstrap: `plutil -lint` OK · Label exactly `com.ensemble.drill` · no `com.ensemble.demo` label · word-grep clean (no `prod`/`watchdog`) · **no `9797` anywhere** · KeepAlive JSON `{"Crashed":true,"SuccessfulExit":false}` · ThrottleInterval 10.

## 4. Boot + verification battery — 23:01:36Z

```
launchctl bootstrap gui/501 ~/Library/LaunchAgents/ensemble-demo-drill.plist   (ritual asserts PASS) → exit 0
state = running · runs = 1 · pid 54245 · working directory = /tmp/ensemble-drill
stdout path = /tmp/ensemble-drill/data/launcher.log · stderr path = …/launcher.err.log
tree: 54245 (ppid=1) bash /tmp/ensemble-drill/launcher.sh
      54268 (child)  /private/tmp/ensemble-drill/current/ensemble-prod     ← see D-F3
/livez  → 200 {"status":"alive","uptime_seconds":4.28,"version":"0.10.5"}
/readyz → 200 all-green (database/queue_freshness/services)
```

### 4.1 WRITE-LOCATION PROOF — the point of the drill (launcher writes under a NON-`$HOME` root)

```
money-line: 2026-11-10T06:01:36+0700 launcher[54245]: ensemble launcher starting
            (INSTALL_DIR=/private/tmp/ensemble-drill)                 ← self-location works anywhere
fresh daemon lines: 06:01:48 Starting Ensemble v0.10.5 · Uvicorn running on 127.0.0.1:7979
data/launcher.err.log  94101 B, mtime = this boot (fresh writes at the new root ✓)
data/opencode_sessions.db-wal  0 B, mtime = this boot (recreated at the new root ✓)
.launcher-state present at /tmp/ensemble-drill/.launcher-state (copied; sha ac663cbd…)
```

`.launcher-state` nuance (matches rehearsal-a S7): it is **not rewritten during a clean run or a deliberate stop** — only crash-classified child exits write it. Its binding to the new root is structural (`STATE_FILE="$INSTALL_DIR/.launcher-state"`, launcher.sh) and the INSTALL_DIR money-line is the proof the binding resolved to the new root. The doc must not claim "state file created at boot".

### 4.2 MCP/PATH observation — quick start as written ships BROKEN stdio MCP (third independent confirmation)

```
06:01:51 daemon.mcp.warmup_pool - ERROR - Failed to warm up pool for 'context7' (0/1 connections created)
06:01:51 daemon.manager - INFO - MCP warm-up pool ready: {'context7': {'available': 0, 'pool_size': 1, 'healthy': False}}
06:01:55 daemon.services.mcp_service - INFO - eager_warm_schemas: primed 0/2 (plane: 0 tools, context7: 0 tools)
```

No `EnvironmentVariables.PATH` → launchd's `PATH=/usr/bin:/bin:/usr/sbin:/sbin` → npx-based context7 stdio server cannot spawn (`healthy: False`), exactly as in rehearsal-a boot #1 and the Aug-era contrast. The daemon itself is fully healthy (livez/readyz green) — the failure is silent at the health-endpoint level (this era's `/readyz` has no MCP component). **A fresh quick-start install without a PATH block has degraded MCP and no health signal says so.** Not worked around, per instruction — this is the doc input.

## 5. Stop + cleanup + untouched assertions — 23:02:45Z

```
bootout gui/501/com.ensemble.drill (ritual: drill label ✓ · no prod/watchdog ✓ · not the demo label ✓) → exit 0
com.ensemble.drill NOT LOADED ✓ · com.ensemble.demo NOT LOADED ✓ (its artifact state preserved) · :7979 FREE ✓
D8 window: 06:02:45 bootout → 06:02:47 launcher exiting 143   [≈2 s]

cleanup (verified the tree IS the drill copy via its money-line first; demo original present at its own path):
  rm -rf /tmp/ensemble-drill           (literal path, scratch I created)
  rm ~/Library/LaunchAgents/ensemble-demo-drill.plist
  → /tmp/ensemble-drill GONE ✓ · LaunchAgents = exactly the 3 original files (mtimes 01:12 / 00:57 / 02:09) ✓
  → /usr/local/ensemble-drill: NEVER EXISTED (EACCES at mkdir — no residue) ✓

DEMO UNTOUCHED: diff /tmp/reh-d/demo-baseline.txt demo-after.txt → EMPTY
  (readlink current · launcher.sh · current/ensemble-prod · state.json · data/launcher.err.log ·
   .launcher-state · ensemble-demo.plist — all byte-identical)

PROD (after every phase): pid=51569 status=0 · :9797/livez 200 ✓
```

## 6. Findings for the canonical doc (ranked)

| # | Finding | Doc action |
|---|---|---|
| **D-F1** | `/usr/local` is `root:wheel 755` on stock macOS — an admin user gets **EACCES** creating the install dir. The quick-start `sudo mkdir -p` + `sudo chown` step is **mandatory**; any "no sudo needed" wording is false and strands the reader at step 1. (Same volume as `$HOME` — permission blocker only.) | Keep + emphasize the one-time sudo step; delete the admin-writable assumption. State the exact error the reader would otherwise hit. |
| **D-F2** | Everything else about the non-`$HOME` shape **works**: launcher self-locates (`INSTALL_DIR` money-line), daemon logs + opencode WAL land under the new root, plist boot/state/pid tree identical, livez+readyz green, `.launcher-state` bound to the new root. Proven at `/private/tmp/ensemble-drill`; nothing in the tree references `$HOME`. | Quick start is sound mechanically once the dir exists and is user-owned. |
| **D-F3** | `/tmp` canonicalizes to `/private/tmp` — the INSTALL_DIR line and `ps` show the canonical form. Cosmetic, but doc examples grepping `/tmp/...` paths should mention it. | Note in the doc; no action. |
| **D-F4** | **Quick start as written ships degraded MCP**: without an `EnvironmentVariables.PATH` block, context7 (npx/stdio) is `healthy: False`, `primed 0/2`, with **no health-endpoint signal** (era `/readyz` lacks an MCP component). Third independent confirmation (rehearsal-a A/B, Aug-era contrast, this drill at a third root+label). | Quick start must either ship the PATH block in its plist step or the Stage-2 launcher must self-provision PATH (already on the stage-2 patch list). Silent degradation is the worst variant — at minimum document the check (`grep warm-up launcher.err.log`). |
| **D-F5** | `.launcher-state` is not written on clean runs/stops (only crash-classified exits); its location binding is structural. | Don't document "state file created at boot"; document it appears after the first crash-classified exit. |
| **D-F6** | Copy-fidelity method (`cp -a` + `readlink current` + spot sha256) is sufficient; the demo ended **byte-identical** (empty diff) — the copy-never-move discipline costs nothing. | — |
| **D-F7** | No sudo was used anywhere in the completed drill; the ONLY sudo-requiring step in the whole quick-start shape is the initial `/usr/local` mkdir+chown (operator's). | — |
| **D-F8** | Stop window again ≈ 2 s (exit 143, graceful) — consistent across 6 total bootouts this commission. | Runbook number. |

## 7. End state (verified)

`com.ensemble.drill` and `com.ensemble.demo` both NOT LOADED · `:7979` FREE · `/tmp/ensemble-drill` gone · drill plist gone · `~/Library/LaunchAgents` back to exactly its 3 pre-drill files · demo tree byte-identical · `/usr/local` untouched (no `ensemble-drill` ever created) · PROD pid 51569 · `:9797/livez` 200.
