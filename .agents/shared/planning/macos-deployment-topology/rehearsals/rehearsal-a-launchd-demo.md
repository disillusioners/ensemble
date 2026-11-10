# Rehearsal (a) — launchd supervision for the demo env (`com.ensemble.demo`)

**Task:** wi-52666630 · commission `macos-deployment-topology` · branch `feature/macos-deployment-topology`
**Date:** 2026-11-09, 18:42–18:47 UTC (2026-11-10 01:42–01:47 local, UTC+07)
**Executor:** DevOps agent (inside the live daemon; demo env only — every launchctl call targeted `com.ensemble.demo` under the label-assertion ritual below)
**Status:** ✅ COMPLETE — start (×2), verify, PATH decision, GAP-2 stop test, prod-untouched assertions.
**Artifact kept on disk:** `~/Library/LaunchAgents/ensemble-demo.plist` (1680 B, lint OK) — end state: label **booted out** (original unloaded state), nothing on `:7979`.

> Transcript conventions: timestamps are **UTC** unless a log line shows local (+0700). Commands are shown verbatim; outputs trimmed where marked `…`. Secret-like values are redacted where noted.

---

## 0. Safety framing used for every launchctl call

Before **every** launchctl invocation (bootstrap / bootout / print):

1. `echo "PLANNED COMMAND: $CMD"` — full command printed first.
2. Assert the demo target string is present (`ensemble-demo.plist` for bootstrap; `com.ensemble.demo` for print/bootout).
3. Assert the command does **not** match `prod|watchdog` (word grep) — abort on match.
4. For bootstrap: additionally assert the **on-disk plist** carries `<string>com.ensemble.demo</string>` and its whole file is grep-clean of the words `prod` / `watchdog`.
5. UID resolved via `id -u` and asserted numeric/non-empty.

`com.ensemble.prod` / `com.ensemble.watchdog` were never named in any mutating command. Prod plist files were read-only (POSIX read, `plutil -extract` for PATH reference) and their mtimes confirm no writes (see §7).

---

## 1. Pre-flight state (read-only baselines)

```
=== R0. identity ===
uid=501 user=nguyenminhkha host=Nguyens-MacBook-Air.local

=== R7. baseline launchctl labels ===
51569	0	com.ensemble.prod
-	0	com.ensemble.watchdog
-	0	com.apple.ensemble

=== R8. baseline prod livez (READ-ONLY GET :9797) ===
HTTP 200

=== R9. baseline :7979 listener ===
:7979 FREE

=== R10. ~/Library/LaunchAgents ensemble files ===
-rw-r--r--  1 nguyenminhkha  staff  1541 Nov 10 01:12 ensemble-prod.plist
-rw-r--r--  1 nguyenminhkha  staff  3629 Nov 10 00:57 ensemble-watchdog-watcher.plist
```

Demo install facts:

```
current -> releases/v0.10.8-p2.3-b7cyc1
launcher.sh: 58364 B, -rwxr-xr-x, Aug 24 05:37
current/ensemble-prod: 48059264 B, -rwxr-xr-x, Aug 24 05:36
data/: ensemble.json, launcher.log (848895 B, Aug 24 13:27), logs/, opencode_sessions.db*, upgrade.log
.env: PORT=7979 (.env:22), PLANE_MCP_WORKSPACE_SLUG="nea" (:81), PLANE_SYNC_ENABLED=0 (:83)
.env key census (names only): …PORT POSTGRES_DB POSTGRES_HOST POSTGRES_PASSWORD POSTGRES_PORT
  POSTGRES_USER QUEUE_DISCARD_ON_STARTUP ENSEMBLE_SELF_ENV OPENAI_* PLANE_* …
launcher.err.log: did not exist yet (launchd creates it)
releases/state.json: in_flight = null  (launcher journal-sweep will no-op)   [verified]
.launcher-state: last_exit=75 crash_count=4 window_start=1787507522 last_backoff=60
                 notified_75=1 last_uptime=2   (Aug-era; window aged out → budget resets)
```

Two pre-existing journal observations (read-only, no action taken — rehearsal mandate is "rehearse against what is there"):

- `releases/state.json` already contained `mcp_server_state` history events timestamped **2026-11-09T16:45–16:55Z** (context7 init_failed / dns_servfail → quarantined) — i.e., written ~2 h **before** this rehearsal by some earlier process. `in_flight` was `null`, so the launcher's journal sweep correctly no-ops.
- A `pending_op` (kind=promote, armed 2026-08-23T22:37Z, `expires_at` 22:47Z same day) sits in the journal; `pending_restart` is null. The daemon boot recovery logged `Job recovery complete: 0 recovered, 0 alive, 0 total` — no attempt visible in this era's logs. Left untouched.

---

## 2. What the repo template says about the demo variant (and where installed reality diverges)

Source read (worktree, read-only): `scripts/ensemble-prod.plist`. The template's header documents the demo variant explicitly:

> **DEMO USAGE (3-env topology — dev 8079 / demo 7979 / live 9797):** the same shape works for the demo rehearsal env with three edits —
> `Label` → `com.ensemble.demo` (must differ from prod's);
> port → `7979` comes from `~/.env` staged by `scripts/deploy.sh` — **nothing to edit in the plist itself (ADR-014: env, not plist, is the port source)**;
> `INSTALL_DIR` → `~/agents-ensemble-demo` (the placeholder you substitute; logs land in its `data/launcher.log`).

KeepAlive semantics per template (ADR-010), quoted in substance:

> `{SuccessfulExit=false, Crashed=true}` is an **OR**: launchd restarts when the job exits nonzero (SuccessfulExit=false covers ANY nonzero exit, incl. 75 and 78) **or** dies on a crash signal. **Exit 0 (clean stop): not matched by either key → launchd stays hands-off.** Exit 78 repeats one no-op re-exec per ThrottleInterval (guard is structural — launcher exits 78 again before starting a child). **`launchctl bootout` keeps it down for good.**

**Divergence found (doc-writer should reconcile):** the repo template says `EnvironmentVariables: deliberately ABSENT` (all env from `.env` via launcher). The **installed** prod plist (`~/Library/LaunchAgents/ensemble-prod.plist`, 1541 B) actually **carries** an `EnvironmentVariables.PATH` block:

```
{"PATH":"/opt/homebrew/share/google-cloud-sdk/bin:/opt/homebrew/opt/postgresql@14/bin:…:/Users/nguyenminhkha/.nvm/versions/node/v22.21.1/bin:…:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:…"}
```

This rehearsal proved the installed-plist pattern is the **correct** one for macOS (see §5 — minimal PATH breaks stdio/npx MCP servers). The demo plist therefore mirrors the installed prod PATH value verbatim.

---

## 3. The artifact — `~/Library/LaunchAgents/ensemble-demo.plist` (final, 1680 B)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ensemble.demo</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/nguyenminhkha/agents-ensemble-demo/launcher.sh</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>WorkingDirectory</key>
    <string>/Users/nguyenminhkha/agents-ensemble-demo</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/share/google-cloud-sdk/bin:/opt/homebrew/opt/postgresql@14/bin:/Users/nguyenminhkha/.bun/bin:/Users/nguyenminhkha/.nvm/versions/node/v22.21.1/bin:/Users/nguyenminhkha/.antigravity/antigravity/bin:/Users/nguyenminhkha/bin:/Users/nguyenminhkha/.local/bin:/Users/nguyenminhkha/.cargo/bin:/Users/nguyenminhkha/.yarn/bin:/Users/nguyenminhkha/.config/yarn/global/node_modules/.bin:/Users/nguyenminhkha/All/env/sonar-scanner-4.7.0.2747-macosx/bin:/usr/local/opt/libpq/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/nguyenminhkha/Library/Android/sdk/platform-tools</string>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/nguyenminhkha/agents-ensemble-demo/data/launcher.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/nguyenminhkha/agents-ensemble-demo/data/launcher.err.log</string>
</dict>
</plist>
```

Design decisions:

| # | Decision | Rationale |
|---|---|---|
| D1 | **No port in the plist.** `PORT=7979` lives in `~/agents-ensemble-demo/.env:22`; the launcher exports it (ADR-014). | Verified from `.env` + launcher `load_env_file`. Precedence is structural (launcher exports win over the binary's own loader). |
| D2 | **No XML comments** in the installed file | Keeps the on-disk artifact grep-clean of `prod`/`watchdog` words — the safety ritual greps the file. |
| D3 | **Minimal plist first, PATH added only on evidence** (see §5) | Template-pure first pass; data decided the addition. |
| D4 | `KeepAlive {SuccessfulExit=false, Crashed=true}`, `ThrottleInterval 10`, `RunAtLoad true` | Exactly the template's documented semantics; GAP-2 stop test proves bootout stays down (§6). |
| D5 | `WorkingDirectory` = demo dir; std paths under `data/launcher{,.err}.log` | Template shape. |

Lint + parse checks before any bootstrap:

```
$ plutil -lint ~/Library/LaunchAgents/ensemble-demo.plist
…ensemble-demo.plist: OK
$ plutil -extract KeepAlive json -o - …            → {"Crashed":true,"SuccessfulExit":false}
$ plutil -extract ThrottleInterval raw -o - …      → 10
$ plutil -extract Label raw -o - …                 → com.ensemble.demo
```

---

## 4. Bootstrap #1 + verify (minimal PATH — the control run)

### Bootstrap (ritual applied)

```
=== B1. PLANNED COMMAND: launchctl bootstrap gui/501 /Users/nguyenminhkha/Library/LaunchAgents/ensemble-demo.plist ===
ASSERT PASS: demo plist target present in command
ASSERT PASS: command contains no 'prod'/'watchdog'
ASSERT PASS: on-disk plist Label = com.ensemble.demo
--- all asserts green, executing ---
=== B2. EXECUTE @ 2026-11-09T18:44:02Z ===
bootstrap exit code: 0
```

### Job state immediately after (t+4 s)

```
gui/501/com.ensemble.demo = {
	active count = 1
	path = /Users/nguyenminhkha/Library/LaunchAgents/ensemble-demo.plist
	type = LaunchAgent
	state = running
	program = /bin/bash
	arguments = { /bin/bash  /Users/nguyenminhkha/agents-ensemble-demo/launcher.sh }
	working directory = /Users/nguyenminhkha/agents-ensemble-demo
	stdout path = …/data/launcher.log
	stderr path = …/data/launcher.err.log
	inherited environment = { OPENCODE_SERVER_PASSWORD => <redacted: dev-default>, OPENCODE_PORT => 4095, SSH_AUTH_SOCK => … }
	default environment = { PATH => /usr/bin:/bin:/usr/sbin:/sbin }
	environment = { XPC_SERVICE_NAME => com.ensemble.demo }
	…
}
```

> Note: the gui-domain inherited env (`OPENCODE_*`, `SSH_AUTH_SOCK`) flows into the job — inert for the demo, recorded for completeness.

### Boot evidence (launcher + daemon)

```
=== V1. state @ 18:44:22Z ===
	state = running
	runs = 1
	pid = 45090
	last exit code = (never exited)

=== V5. process tree @ 18:44:40Z ===
  PID  PPID USER          ELAPSED COMMAND
45090     1 nguyenminhkha   00:38 /bin/bash /Users/nguyenminhkha/agents-ensemble-demo/launcher.sh
45112 45090 nguyenminhkha   00:37 /Users/nguyenminhkha/agents-ensemble-demo/current/ensemble-prod
```

Topology is exactly as designed: **launchd (ppid=1) → launcher (bash) → daemon binary.**

```
=== launcher.err.log (fresh, created by launchd this boot) ===
2026-11-10T01:44:02+0700 launcher[45090]: ensemble launcher starting (INSTALL_DIR=/Users/nguyenminhkha/agents-ensemble-demo)
2026-11-10T01:44:03+0700 launcher[45090]: starting: /Users/nguyenminhkha/agents-ensemble-demo/current/ensemble-prod
01:44:14 - daemon.api - INFO - Starting Ensemble v0.10.5
…
01:44:15 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: localhost:5432/ensemble_demo
01:44:16 - daemon.manager - INFO - Cleared 0 backlog message(s) (discard_on_startup=backlog-clear; in-flight/paused preserved)
01:44:16 - daemon.manager - INFO - Bootstrapping 3 built-in MCP servers...
01:44:16 - daemon.manager - INFO - Built-in MCP server 'webfetch' disabled (MCP_DISABLE_BUILT_IN_WEBFETCH), skipping creation
01:44:16 - daemon.mcp.warmup_pool - ERROR - Failed to create pooled connection for 'context7': FileNotFoundError: [Errno 2] No such file or directory
  … uvloop subprocess_exec traceback (npx not executable from this PATH) …
01:44:16 - daemon.manager - INFO - MCP warm-up pool ready: {'context7': {'available': 0, 'pool_size': 1, 'healthy': False}}
01:44:17 - daemon.mcp.connection_manager - ERROR - Failed to create session for 'plane': Connection closed
…
01:44:20 - daemon.services.mcp_service - INFO - eager_warm_schemas: primed 0/2 MCP server schema(s) (plane: 0 tools, context7: 0 tools)
…
INFO:     Uvicorn running on http://127.0.0.1:7979 (Press CTRL+C to quit)
```

Health endpoints — old-era contract (note what it **doesn't** have):

```
GET /livez  → 200  {"status":"alive","uptime_seconds":5.084641933441162,"version":"0.10.5"}
GET /readyz → 200  {"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},
                    "detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":"2026-11-09T18:44:17Z"},
                    "draining":false}
GET /docs   → 200
```

**Ready in ~15 s** from `launchctl bootstrap` (18:44:02 → app startup complete 18:44:17). Binding: `127.0.0.1:7979` (loopback only).

---

## 5. PATH decision — evidence-driven, then re-bootstrap with the block

### The question
Does the (v0.10.8-era) demo daemon need a plist `EnvironmentVariables.PATH`? The era **does** run MCP servers (`context7` stdio via npx; `plane` streamable-HTTP), and the demo `.env` has **no PATH key** → under launchd the daemon inherits only `/usr/bin:/bin:/usr/sbin:/sbin`. This era's `/readyz` has **no MCP component** (see above), so the evidence came from the daemon boot log instead.

### The evidence (same box, same env, two PATH contexts)

| Boot | PATH context | context7 (stdio / npx) | eager_warm |
|---|---|---|---|
| Aug-era run (`launcher.log`, 05:52:47) | full user PATH (interactive) | `Context7 Documentation MCP Server v4.0.3 running on stdio` · `Warmed up pool for 'context7' (1/1 connections)` · `healthy: True` | `primed 2/2 (plane: 177 tools, context7: 2 tools)` |
| **This boot #1** (18:44:16, launchd minimal PATH) | `/usr/bin:/bin:/usr/sbin:/sbin` | `FileNotFoundError: [Errno 2] …` from `uvloop subprocess_exec` · `available: 0 … healthy: False` | `primed 0/2 (plane: 0 tools, context7: 0 tools)` |

Mechanism proof: `npx` resolves at `/Users/nguyenminhkha/.nvm/versions/node/v22.21.1/bin/npx` — a directory that exists only in the full user PATH (and in the installed prod plist's PATH value).

**Decision: ADD `EnvironmentVariables.PATH`, mirroring the installed prod plist value verbatim.** (This also confirms the installed prod plist's divergence from the "EnvironmentVariables deliberately ABSENT" template note is deliberate and necessary on macOS.)

`plane` fails with `Connection closed` in **both** contexts → **not PATH-class** (it's a streamable-HTTP MCP per `PLANE_MCP_URL`; the failure is remote endpoint/credential-side and pre-exists this rehearsal — Plane key rotation is a known pending item). Expected to persist; recorded, not fought.

### Bootout #1 (for the PATH iteration — ritual applied)

```
PLANNED COMMAND: launchctl bootout gui/501/com.ensemble.demo
ASSERT PASS: demo label present
ASSERT PASS: no prod/watchdog in command
--- executing @ 2026-11-09T18:45:12Z ---
bootout exit code: 0

t+3 s:  com.ensemble.demo NOT in launchctl list   |   :7979 FREE

launcher.err.log tail:
01:45:12 - daemon.manager - INFO - Cleaning up resources...
01:45:12 - daemon.manager - INFO - Database engine disposed
01:45:12 - daemon.manager - INFO - Graceful shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [45120]
2026-11-10T01:45:13+0700 launcher[45090]: shutdown complete — exiting 143 (child's exit code)

.launcher-state unchanged (last_exit=75 …) — the deliberate-stop path exits with the child's
code BEFORE write_state; expected, see §9 surprise S7.
```

### Bootstrap #2 (with PATH) + verify

```
=== P3. BOOTSTRAP #2 with ritual @ 2026-11-09T18:45:33Z ===
PLANNED COMMAND: launchctl bootstrap gui/501 /Users/nguyenminhkha/Library/LaunchAgents/ensemble-demo.plist
ASSERT PASS: demo plist target present
ASSERT PASS: no prod/watchdog in command
bootstrap exit code: 0

t+8 s print:  state = running · runs = 1 · pid = 65684 · last exit code = (never exited)
              environment PATH => /opt/homebrew/share/google-cloud-sdk/bin:…:/Users/nguyenminhkha/.nvm/versions/node/v22.21.1/bin:…:/usr/sbin:/sbin:…
livez HTTP 200 after 9 s of polling
```

Outcome under the PATH block (`launcher.err.log`, second boot onward):

```
01:45:49 - daemon.manager - INFO - MCP warm-up pool initialized: 1 server(s) registered, warmup running in background
Context7 Documentation MCP Server v4.6.0 running on stdio
01:45:51 - daemon.mcp.warmup_pool - INFO - Warmed up pool for 'context7' (1/1 connections)
01:45:51 - daemon.manager - INFO - MCP warm-up pool ready: {'context7': {'available': 1, 'pool_size': 1, 'healthy': True}}
01:45:51 - daemon.mcp.connection_manager - ERROR - Failed to create session for 'plane': Connection closed
01:45:53 - daemon.services.mcp_service - INFO - eager_warm_schemas: primed 1/2 MCP server schema(s) (plane: 0 tools, context7: 2 tool(s))
```

**PATH decision validated: `healthy: False → True`, `available: 0 → 1`, `primed 0/2 → 1/2`.** (Aside: context7 stdio version drifted v4.0.3 → v4.6.0 because npx resolves latest — a supply-chain/pinning note for the topology doc; not changed tonight.)

Health re-check + tree:

```
GET /livez  → 200  {"status":"alive","uptime_seconds":7.4635419845581055,"version":"0.10.5"}
GET /readyz → 200  {"status":"ready","components":{"database":true,"queue_freshness":true,"services":true}, …}
  PID  PPID USER          ELAPSED COMMAND
65684     1 nguyenminhkha   00:24 /bin/bash …/agents-ensemble-demo/launcher.sh
66724 65684 nguyenminhkha   00:23 …/agents-ensemble-demo/current/ensemble-prod
```

---

## 6. GAP-2 stop test (deliberate stop semantics)

```
=== ST1. pre-stop @ 2026-11-09T18:46:10Z ===
	state = running · runs = 1 · pid = 65684 · last exit code = (never exited)
pre-stop :7979 livez → HTTP 200

=== ST2. BOOTOUT — ritual ===
PLANNED COMMAND: launchctl bootout gui/501/com.ensemble.demo
ASSERT PASS: demo label present in command
ASSERT PASS: no prod/watchdog in command
--- executing @ 2026-11-09T18:46:10Z ---
bootout exit code: 0

=== ST3. t+3 s ===
PASS: com.ensemble.demo ABSENT from launchctl list
PASS: :7979 FREE

=== ST4. launchctl print after bootout ===
Bad request.
Could not find service "com.ensemble.demo" in domain for user gui: 501      ← UNLOADED, not merely stopped

=== ST5. launcher.err.log ===
01:46:10 - daemon.manager - INFO - Checkpointer adapter closed
01:46:10 - daemon.mcp.warmup_pool - INFO - MCP warm-up pool drained
01:46:10 - daemon.manager - INFO - Cleaning up resources...
01:46:10 - daemon.manager - INFO - Database engine disposed
01:46:10 - daemon.manager - INFO - Graceful shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [67191]
2026-11-10T01:46:11+0700 launcher[65684]: shutdown complete — exiting 143 (child's exit code)

=== ST6. NO-AUTO-RESTART WINDOW: +25 s (t+28 s total; > ThrottleInterval 10) @ 18:46:38Z ===
PASS: still ABSENT from launchctl list (no auto-restart)
PASS: :7979 still FREE
PASS: no demo launcher/daemon processes alive
```

**GAP-2 answer: `launchctl bootout` keeps the demo down.** The launcher receives TERM → forwards to the daemon → daemon shuts down gracefully (checkpointer closed, pool drained, DB engine disposed — clean, ~1 s) → launcher exits **143** (the child's exit code) → **no restart**, because bootout removed the job before KeepAlive could evaluate. This is the deliberate-stop semantics the VM's systemd model needs a macOS twin for: **stop = bootout, never a bare SIGTERM** (a bare SIGTERM would exit 143 and `KeepAlive {SuccessfulExit=false, Crashed=true}` would re-engage it).

---

## 7. Prod-untouched assertions (after all demo mutations)

```
=== PR1. @ 2026-11-09T18:46:46Z ===
51569	0	com.ensemble.prod          ← same pid as baseline, still running
-	0	com.ensemble.watchdog      ← still loaded
-	0	com.apple.ensemble

pid=51569 status=0 label=com.ensemble.prod
:9797/livez → HTTP 200             ← READ-ONLY GET, unchanged

=== PR2. prod plists untouched (mtimes = pre-session) ===
-rw-r--r--  … 1541 Nov 10 01:12 ensemble-prod.plist
-rw-r--r--  … 3629 Nov 10 00:57 ensemble-watchdog-watcher.plist
```

---

## 8. End state

```
com.ensemble.demo: NOT LOADED (booted out — its original state)
:7979 FREE
~/Library/LaunchAgents/ensemble-demo.plist   -rw-r--r-- 1680 B, plutil OK   ← KEPT for rehearsal (b)
```

Nothing on `:7979`. No demo processes alive. Prod (pid 51569) + watchdog untouched. The plist remains the working artifact; activation for later rehearsals is one `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ensemble-demo.plist` away.

---

## 9. Surprises & operational notes (for the topology doc / runbook)

| # | Observation | Implication |
|---|---|---|
| S1 | **Binary self-reports `version: "0.10.5"`** in `/livez` while the release dir is `v0.10.8-p2.3-b7cyc1`. | Embedded version string lags the release tag in this era. Don't use `/livez.version` for release identification in old-era demos — use the `current` symlink / release dir. (Era-authentic; not "fixed".) |
| S2 | **This era's `/readyz` has no MCP component** — only `database`, `queue_freshness`, `services`. | MCP health must be verified from `data/launcher.err.log` warmup lines (or `/livez`+logs), not `/readyz`. Runbooks written against newer eras will mis-read this. |
| S3 | **Under launchd the daemon log surface is `data/launcher.err.log`** (stderr). `data/launcher.log` received **nothing fresh** (its last lines are Aug-era, 13:27). | Point runbook "tail the log" at `launcher.err.log` (or both). The template's header says "logs land in data/launcher.log" — true for stdout, but this daemon logs to stderr. |
| S4 | **PATH is load-bearing on macOS**: without the plist PATH block, every stdio/npx MCP server dies with `FileNotFoundError: [Errno 2]` (`context7` proven). | Installed prod plist already carries PATH (1541 B) though the repo template says "deliberately ABSENT" — reconcile the template docs; treat `EnvironmentVariables.PATH` as required for macOS stdio MCP. |
| S5 | context7 stdio came up as **v4.0.3 in Aug, v4.6.0 tonight** (npx resolves latest). | Unpinned MCP supply chain — a version-pinning recommendation belongs in the topology doc. |
| S6 | `plane` MCP: `Connection closed` both boots (both PATH contexts). Not PATH-class; streamable-HTTP endpoint issue; pre-existing. | Consistent with Plane key/URL known-pending state. Sync itself is off on demo (`PLANE_SYNC_ENABLED=0`), so no Plane write hazard from this rehearsal. |
| S7 | **`.launcher-state` is not rewritten on a deliberate stop** — the shutdown branch exits with the child's code before `write_state`; the file kept Aug-era values (`last_exit=75, crash_count=4`). | Harmless (window aged out → budget resets on first real crash), but a doc note: don't read `.launcher-state` after a stop as "last run" telemetry. |
| S8 | Boot latency: **~15 s** bootstrap→ready (launcher start 18:44:02 → startup complete 18:44:17; readyz green; livez 200). | Useful expectation for runbooks; old binary is fast — no migration work in this era (`Skipping migrations for non-SQLite database`). |
| S9 | Demo boot cleared backlog: `Cleared 0 backlog message(s) (discard_on_startup=backlog-clear …)` — `QUEUE_DISCARD_ON_STARTUP` in effect in the demo `.env`. | Demo-only semantics. **Do not port to live without checking live `.env` intent.** |
| S10 | Demo **checkpointer** connects to `localhost:5432/ensemble` while the **app DB** is `localhost:5432/ensemble_demo`. | Possible dev/demo DB entanglement for checkpoints (same DB name `ensemble`). Not touched tonight — flag for the topology survey: list Postgres databases and confirm the demo checkpointer target before documenting the data topology. |
| S11 | Gui-domain inherited env (`OPENCODE_SERVER_PASSWORD`, `OPENCODE_PORT=4095`, `SSH_AUTH_SOCK`) flows into the launchd job. | Inert here; note that launchd jobs inherit the user domain env — a hygiene item for hardening docs. |
| S12 | Journal pre-state: `in_flight=null` (sweep no-op) + an expired Aug-23 `pending_op` promote + `mcp_server_state` history written **today 16:45Z by an earlier process**. | The launcher explicitly logs/handles in-flight txns (never fires on fresh/absent); the pending_op is era-inerts. Nothing required, but the topology doc should mention the journal's `pending_op` vs `in_flight` distinction. |
| S13 | `launchctl bootout` returns 0 and the job disappears from `launchctl list` within ~3 s; graceful drain took ~1 s (checkpointer closed, MCP pool drained, DB disposed). | Stop budget is comfortable; `CHILD_STOP_WAIT_S=70` never engaged. The VM's ~70 s stop budget concern (KV item F) has huge headroom on the demo daemon. |

---

## 10. Reusable procedure (as rehearsed — copy-paste-able for the doc)

```bash
# 0) Pre-flight (read-only)
launchctl list | grep -i ensemble
curl -s -o /dev/null -w 'livez: %{http_code}\n' http://127.0.0.1:7979/livez   # expect 000 before start
lsof -nP -iTCP:7979 -sTCP:LISTEN || echo ":7979 free"

# 1) Plist (final artifact in §3) — lint
plutil -lint ~/Library/LaunchAgents/ensemble-demo.plist

# 2) Start (label-assert ritual for every launchctl call)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ensemble-demo.plist

# 3) Verify
launchctl print gui/$(id -u)/com.ensemble.demo | grep -E 'state|pid|last exit'   # state=running
ps -o pid,ppid,command -p <launcher-pid>                                        # ppid=1
curl http://127.0.0.1:7979/livez; curl http://127.0.0.1:7979/readyz
tail -40 ~/agents-ensemble-demo/data/launcher.err.log                           # daemon log surface (S3)

# 4) Stop (GAP-2: bootout, NEVER bare SIGTERM)
launchctl bootout gui/$(id -u)/com.ensemble.demo
launchctl list | grep -i com.ensemble.demo || echo "unloaded"                   # stays down
lsof -nP -iTCP:7979 -sTCP:LISTEN || echo ":7979 free"                          # t+3 s and t+30 s
curl -s -o /dev/null -w 'prod livez: %{http_code}\n' http://127.0.0.1:9797/livez # PROD 200
```

---

## 11. Verification checklist (for the reviewer / next rehearsals)

- [x] `plutil -lint` OK; Label `com.ensemble.demo`; KeepAlive `{SuccessfulExit=false, Crashed=true}`; ThrottleInterval 10
- [x] Bootstrap → `state = running`, launcher ppid=1, daemon child process
- [x] `/livez` 200 (`v0.10.5`), `/readyz` 200 (database/queue_freshness/services true) in ~15 s
- [x] PATH decision made + proven (context7 `healthy: False → True`; mechanism `npx` on plist PATH)
- [x] GAP-2: bootout → unloaded (`Could not find service`), no auto-restart at t+28 s (> ThrottleInterval), `:7979` free
- [x] Prod untouched: pid 51569 running, `:9797/livez` 200, prod plist mtimes unchanged
- [x] End state: demo booted out (original state), plist kept on disk for rehearsal (b)
- [x] Transcript written: this file
