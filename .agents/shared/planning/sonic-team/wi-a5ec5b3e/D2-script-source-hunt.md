# D2 — Script-Source Hunt (wi-a5ec5b3e)

## Findings — is there a shared "system_restart / pok / nest-" script?

**No.** There is NO shared script file anywhere that carries the phrase or the killer body. The premise of the hunt is REFUTED at the file level:

- Exhaustive literal grep across the main checkout (`/home/nea/ensemble-src`), ALL 26 worktrees (`/home/nea/ensemble-src-wt-*`, `/tmp/*`), the live install tree (`/home/nea/agents-ensemble`), and `/tmp` returned the phrase in exactly five places, ALL of which are the incident/commission documentation itself:
  - `/home/nea/agents-ensemble/.agents/shared/incident-2026-11-18-outage.md` (the incident doc)
  - `/home/nea/agents-ensemble/.agents/shared/context.md`
  - the D3 incident-append block and its receipt (`/home/nea/ensemble-src-wt-wia5ec5b3e-doc/.agents/shared/planning/sonic-team/wi-a5ec5b3e/D3-appended-block.md`)
  - this commission's briefs (`brief-hunt.md`, `plan-overview.md`)
- `git log --all -S'system_restart / pok / nest'` across the FULL history returns EXACTLY ONE commit: `11b16ff29 docs(sonic): D3 incident-doc append receipt — wi-a5ec5b3e` — i.e. the phrase entered the repo's history only via THIS commission's own receipt. There is no historical origin in the repo.
- `git log --all -S'pok / nest'` → same single commit.
- Live DB: `prompt_versions` 5 rows matching `nest-%`/`systemctl` (all false positives — "HONEST-" substring); `skills` 29 (all false positives); `board_items` 10 (false positives: "full nest", "ensemble-main" in prose); none carry "system_restart / pok" as a literal. `skills` has 0 rows matching `system_restart /%`.

So there is no shared script. The phrase is an LLM-composed INLINE body — specifically an **echo-header for a READ-ONLY tool-registry category audit**.

### The shape's real origin (the one place the phrase actually appears)

The live DB holds the phrase in exactly one pre-existing context — the checkpoint transcript of instance `784c0e90` (worker, "w6-maintenancer-state", dispatch source `agent:ari`). In blob version `00000000000000000000000000000052` (checkpoint ts `2026-11-18T09:14:13.517297Z`), message index 54 is an AI tool call whose body begins:

```
cd /home/nea/ensemble-src && echo "=== system_restart / pok / nest-report category defs ===" && \
  grep -rn "system_restart" --include=*.py daemon/tools/_tool_registry.py | head -10 && \
  grep -rn "register_tool_category" daemon/tools/service_tools.py daemon/tools/pok.py daemon/tools/nest_report.py ... && \
  ...
```

That is a **read-only `grep`/`awk` inspection of the tool registry** — it does NOT restart anything. The header "=== system_restart / pok / nest-report category defs ===" is an audit-section label naming the three PRIVILEGED TOOL CATEGORIES being enumerated (`system_restart` at `daemon/tools/_tool_registry.py:160,1959`; `pok`; `nest-report`). The incident doc's "occurrence #2 attribution: bash tool call at 09:14:13 beginning `cd /home/nea/ensemble-src && echo "=== system_restart / pok / nest-..."`" quoted the FIRST 40 chars of that **benign** read-only command and then inferred (in the D3 appended block) a "common shape (all three): an inline audit-style script body ... ending in a `systemctl stop`/`systemctl restart` of `ensemble-main`". That inference is NOT supported by the transcript: the quoted command is a pure grep, and the three killer transcripts contain **ZERO** `systemctl` token in any tool call (784c0e90: 2,466 bash calls; 72f8c435: 1,706; 0269bb55: 822; e1426d90: 15,558 — all scanned across every checkpoint blob version).

So the "audit-style script shape" is the house idiom `cd <repo> && echo "=== <section> ===" && <read-only probe>` (224 distinct echo-header variants in 784c0e90's transcript alone — emergent LLM convention, not an authored file; no skill or agent prompt teaches it), and the `system_restart / pok / nest-` header is one such section label naming three tool categories.

### What actually killed the unit three times

`/tmp/.ensemble-stop-sc.XXXXXX` sentinel files (created by `scripts/stop-ensemble.sh:784` as `SC_ERR="$(mktemp /tmp/.ensemble-stop-sc.XXXXXX)"`, immediately before `_run_bounded "$STOP_SCRIPT_BUDGET_S" -- "$SYSTEMCTL_BIN" stop "$SU_UNIT"`) survive with mtimes matching the three systemd "Stopping ensemble-main.service" events to the second:

| # | sentinel mtime | systemd "Stopping" (journald) |
|---|---|---|
| 1 | 06:57:27.720796246 | 06:57:27 |
| 2 | 09:16:26.022401476 | 09:16:26 |
| 3 | 14:00:24.908980331 | 14:00:24 |

`scripts/stop-ensemble.sh`'s `UNIT_MANAGED` branch issues `systemctl stop "$SU_UNIT"` where `SU_UNIT` is composed from the shell variable `ENSEMBLE_SUPERVISION_RESULT` (line 767 `SU_UNIT="${ENSEMBLE_SUPERVISION_RESULT#UNIT_MANAGED:}"`). That is the "unit token composed from a shell variable" the incident doc describes — and it is **inside the repo's own sanctioned stop script**, so the bash tool-lane guard's raw-text substring check never sees "systemctl" or the live unit name (the command text is e.g. `bash scripts/stop-ensemble.sh ...` → `_pkill_overlay_is_kill_like` returns False → no refusal). The guard only inspects the agent's command text, not what the invoked script does.

Note: the incident doc's timeline for occurrence #2 ("09:17:36") is off by ~70 s from the actual systemd stop (09:16:26) — the `09:14:13` attribution is the last tracked shell call before the *claimed* 09:17:36 death, not the kill.

## Author (git log -S evidence)

The `system_restart` / `pok` / `nest-report` category names (the words in the audit header) are real privileged tool categories in `daemon/tools/_tool_registry.py` (system_restart at lines 160 and 1959; `pok` at line 1959; `nest-report` at line 1959), introduced by:

- `5ef35262ac4a86766454993cd0cddd71ace394c2` (2026-08-23) — `feat(p2.2): action tools system_restart/system_upgrade + interlocks + ari wiring` (Worker)
- `eb2a19056888ce92a5f6e2d54b56d3cb433136ac` (2026-11-07) — `feat(tools): pok_upsert / pok_query / pok_curate + 'pok' category (POK Phase 1 T4)` (giter)
- `3c95bdf04ae9f796d685566f2a37e023b9ac2d39` (2026-11-05) — `feat(anomaly): report_anomaly tool registration, category artifact regen and vocabulary bump 3 to 4` (giter)

The shared destructive script itself:
- `61d03cfa55db1e39a328819e54006bc35c391c82` (2026-08-16) — `fix(ops): ownership-scoped process management — make stop/install can no longer port-kill foreign daemons` (Worker) — origin of `scripts/stop-ensemble.sh`
- `c2542e431d83c2ba15c95bd783d4bf05b5232f68` (2026-09-29) — `feat(supervision): P2 unit-aware stop branch — b″ fix + SYSTEMCTL_BIN stub seam` (Worker) — added the `SU_UNIT`-from-env variable-composed stop branch + sentinel
- `ENSEMBLE_SUPERVISION_RESULT` env contract frozen in `scripts/upgrade/lib.sh` (line 5276: `ENSEMBLE_SUPERVISION_RESULT="${SUPERVISION_STATE}:${SUPERVISION_UNIT}"`), mirrored in `daemon/tools/upgrade_journal.py:4069+`

The guard's false-negative surface:
- `67b3b86e551ec165a0d1b856d8e50a795a7fc638` (2026-11-12) — `fix(rogue-containment): overlay word-boundary patterns + ensemble-main/demo + input/proc_run bypass channels [D2]` (giter) — origin of `_PKILL_OVERLAY_LIVE_NAME_PATTERNS`
- `55ef81af3543b5653d16b43326df5a886d9d8b4c` (2026-11-18 15:41) — `fix(tools): systemctl intent guard — resolve composed unit operands, allow read-only, cover proc lane` (giter) — D1's in-flight fix on branch `sonic/wi-a5ec5b3e/guard-inversion`, NOT an ancestor of origin/latest at the time of this hunt.

Prompt-level precedent for "restart the daemon from a shell" (the recipe an agent would improvise when the guard blocks read-only `systemctl status`):
- `agents/gaia/workflow.md` "daemon-restart trap" (`3430865e0` 2026-10-24, Councilor C2) — the only in-repo text documenting the detached stop→sleep→launch supervisor shape (`setsid nohup bash -c '<stop>; sleep 2; <launch>' &`).
- `agents/maintenancer/ROLLOUT.md` §W3 Step 9 "Restart the daemon (one-time)" (`012de2522` 2026-09-10) — `./dev.sh restart; sleep 5` (dev.sh has no restart case, so an agent improvising this recipe has no safe sanctioned path).

## Reference map

| Surface | Match? | Notes |
|---|---|---|
| Repo files (all worktrees) | none | Exhaustive grep of `/home/nea/ensemble-src` + all `/home/nea/ensemble-src-wt-*` — only incident/commission docs |
| `/tmp` files | none | No script file contains the phrase |
| Live DB prompt_versions | 0 | 5 rows matched `nest-%`/`systemctl` but all false positives ("HONEST-" substring) |
| Live DB skills | 0 | 29 rows matched `systemctl`/`nest-` but all false positives (e.g. staged-binary-boot-smoke skills) |
| Live DB board_items | 0 | 10 rows matched the wider pattern; all false positives ("full nest", "systemctl" in prose) |
| Live DB job_queue_items | 1 | The commission job `703bfb64` itself (quotes the incident text) |
| Live DB instances | 3 | The commission's own lanes + this hunt module (33227ca9) |
| Live DB checkpoint_blobs | 7 threads | 33227ca9 (272), 90e176a4 (56), 382e02cf (46), 7f72694e (44), 784c0e90 (38), 1321c971 (12), 702c398e (6) — only 784c0e90 is pre-existing, and its command is read-only |
| Live DB skills (DB) | no authored source | 0 skills contain `echo "==="`; the audit-header idiom is emergent LLM style |
| agents/*.md | none | No prompt file teaches the `echo "=== ... ==="` audit-header convention |

The killer instances' own transcripts (`784c0e90`, `72f8c435`, `0269bb55`, `e1426d90`) contain **zero** `systemctl` tool calls across 20,552 scanned bash commands — the fatal command's text was never checkpointed (the daemon died mid-turn) and the registry keeps only argv[0] ("cd").

## Fourth-occurrence watch list

**Live DB state at hunt time (read-only).** Nothing currently pending/queued carries an executable kill-shaped payload — but the revival path and the live killer-class agents are the real surface.

| # | iid | agent | state | what it would run | recommended interim handling |
|---|---|---|---|---|---|
| 1 | `784c0e90-4f18-47cc-baa7-a3d590ce0467` | worker | TERMINATED (13:53:31) | Its checkpoint (mver 108, 105 msgs) carries the audit-header body and the same turn that was in flight when the daemon died; `send_message` to a TERMINATED instance REVIVES it and reuses the checkpoint (`instance_messaging.py:2432` carve-out) | TOP — do not revive; keep parked. Keepalive watchdog is the interim net. |
| 2 | `019a9a9a-e548-4336-9caa-515b6df2dcd8` | leader | WAITING_CHILDREN (live, updated 17:20:45) | Occurrence-#3 lineage root; still re-firing children (child_completed at 14:15 / 14:56 / 15:26 / 16:00 / 17:20) | TOP — park it; do not dispatch more workers into this lineage until D1 is promoted |
| 3 | `0269bb55-a960-4d35-a29c-b489c7d00b09` | tidier | COMPLETED (revived twice: 13:52:38, 15:26) | Occurrence-#3 lineage; revived again at 15:26 | Revoke revive-eligibility; treat as killed until guard ships |
| 4 | `72f8c435-965c-4bf3-a17a-139dadfdbaff` | worker | COMPLETED (14:10:01) | Occurrence-#3 killer; COMPLETED instances are revive-eligible via send_message | Do not revive |
| 5 | `e1426d90-a916-4f91-b009-510692a29833` | coder | COMPLETED | Occurrence-#1 killer | Do not revive |
| 6 | `a413128a-3e3c-422e-ace4-e4e1fd57eed1` | coder | PAUSED (14:15:43) | Paused trio member; ran `pgrep`/`pkill` at 06:57:38/06:57:44 (within the occ-#1 kill window) | Keep paused |
| 7 | `b4c3cad7` / `45324b76` / `51b0e9cf` | leader/leader/developer | PAUSED | Paused trio — "parked (deliberate, not re-arming)" per ari | Keep paused |
| 8 | `9f0de677`, `d2b2c968`, `31565dc4` | worker | RUNNING (17:15-17:20) | Killer class (worker) with full bash tool lane | Structurally at risk until D1 ships; no explicit restart instruction found |
| 9 | `445d8e63`, `15ed5ff4` | worker | IDLE | Same class | Watch |
| 10 | `2719ee8b-c5ef-4138-88db-781ed8a86a76` | worker | COMPLETED | Ran top-level `systemctl` at 08:11:07/37 and 08:12:40 | Audit its transcript; do not revive |
| 11 | `1a83f767-c15c-4ac9-8b7a-a6a49f8c9453` | wanderer | COMPLETED (13:58:41) | Occurrence-#2 lineage parent; revive-eligible | Do not revive |

QUEUED/ACTIVE jobs (`job_queue_items`, admission_state in queued/active, deleted_at null): 6 active + 1 queued. Of these, only the sonic-leader commission job `703bfb64` (this guard commission) and two leader jobs (`3179a320`, `29897f01`) carry `systemctl`-related strings in their payload — and only as read-only evidence prose (journalctl -u ensemble-main, `systemctl is-active` labels), not as an executable instruction. `dependency_watchers`/`job_watchers`: 7 job_watchers rows, none targeting the killer lineage; `stored_watches`: 0 rows.

## Method & evidence appendix

- `git worktree list` enumerated 26 worktrees; all grepped for the literal phrase (READ ONLY).
- `git log --all -S'system_restart / pok / nest'` → 1 commit (`11b16ff293d985a13ec075e1c5595fe5e159bee2`, 2026-11-18 15:26:15, giter).
- `git log --all -S'pok / nest'` → same single commit.
- `git log --all -S'system_restart'` → 84 commits (origin `5ef35262ac4a86766454993cd0cddd71ace394c2`, 2026-08-23).
- `git log --all -S'"pok"'` → `eb2a19056888ce92a5f6e2d54b56d3cb433136ac` (2026-11-07).
- `git log --all -S'"nest-report"'` → `3c95bdf04ae9f796d685566f2a37e023b9ac2d39` (2026-11-05).
- `git log --all -S'.ensemble-stop-sc'` → only `scripts/stop-ensemble.sh`.
- `git log --diff-filter=A -- scripts/stop-ensemble.sh` → `61d03cfa55db1e39a328819e54006bc35c391c82` (2026-08-16, Worker).
- `git log --all -S'SYSTEMCTL_BIN" stop "$SU_UNIT"' -- scripts/stop-ensemble.sh` → `c2542e431d83c2ba15c95bd783d4bf05b5232f68` (2026-09-29, Worker).
- `git log --oneline -S'daemon-restart trap' -- agents/gaia/workflow.md` → `3430865e02066be3e935739aea69da67cbe0624e` (2026-10-24, Councilor C2).
- `git log --oneline -S'### W3 Step 9' -- agents/maintenancer/ROLLOUT.md` → `012de2522da48c4b081fc0006d8865af11f19dd2` (2026-09-10, Councilor C2).
- `git log --all -S'_PKILL_OVERLAY_LIVE_NAME_PATTERNS' -- daemon/tools/bash.py` → `67b3b86e5` (2026-11-12) and `55ef81af3` (2026-11-18, branch `sonic/wi-a5ec5b3e/guard-inversion`, NOT yet ancestor of origin/latest).
- DB (SELECT-only, explicit -h/-p, per-variable scrub): live prod `ensemble_prod` @ 10.44.0.2:5432 (user `ensemble`); dev `ensemble_dev` @ 127.0.0.1:5432.
- `prompt_versions`: 5 rows matching `nest-%`/`systemctl` (all "HONEST-" false positives); 0 rows with `system_restart /%`.
- `skills`: 29 rows matching `systemctl`/`nest-` (false positives); 0 with `echo "==="`; 0 with `system_restart /%`.
- `board_items`: 10 rows matched the wider pattern; none carry the audit phrase; none carry a composed mutating verb.
- `job_queue_items`: 1 row with `system_restart / pok` (the commission itself); 3 active jobs reference `systemctl` only in read-only evidence prose.
- `instances`: 3 non-terminal instances carry the audit phrase/mutating verbs (all are this commission's own lanes).
- `checkpoint_blobs`: 7 threads contain "system_restart / pok": 33227ca9 (272), 90e176a4 (56), 382e02cf (46), 7f72694e (44), 784c0e90 (38), 1321c971 (12), 702c398e (6). The only pre-existing occurrence is 784c0e90 (38 blobs = cumulative repeats of one command at msg index 54 of blob v52, checkpoint ts `2026-11-18T09:14:13.517297Z`).
- 784c0e90 full transcript scan: 2,466 bash tool calls, 0 containing "systemctl", 0 containing "stop-ensemble", 0 containing "sleep". Same for 72f8c435 (1,706), 0269bb55 (822), e1426d90 (15,558). Total scanned: 20,552 tool calls across 394 checkpoint blobs.
- `bash_spawn_registry`: 8,137 rows with `command_sanitized='cd'` on 2026-11-18 (the sanitizer keeps only the first non-env token of a string command, so a `cd /home/nea/ensemble-src && ...` body is indistinguishable from any other "cd" command). 33 rows with `command_sanitized='systemctl'` (all pre-2026-11-18 except 3 on 2026-11-10).
- `/tmp/.ensemble-stop-sc.*` sentinels: 06:57:27.720796246 (occ #1), 09:16:26.022401476 (occ #2), 14:00:24.908980331 (occ #3) — created by `scripts/stop-ensemble.sh:784`, matching journald "Stopping ensemble-main.service" at 06:57:27, 09:16:26, 14:00:24.
- journald: confirmed "Stopping ensemble-main.service" at 06:57:27, 09:16:26, 14:00:24 with no human login; launcher received SIGTERM at 09:16:26 and 06:57:27 and 14:00:24.
- a413128a (coder, paused): 455 blobs, 342 contain 'pkill' — only `pkill -f 'tests/helpers/serving_probe_fake_listener.py'` (test cleanup); no systemctl.
- `code-investigation` skill (id 331292b6, injected into 784c0e90): does not teach the audit-header convention.
- guard source: `daemon/tools/bash.py` `_PKILL_OVERLAY_LIVE_NAME_PATTERNS` (67b3b86e5) and `_pkill_overlay_is_kill_like` (mutating-verb branch); D1 fix 55ef81af3 on branch `sonic/wi-a5ec5b3e/guard-inversion`.

## Complexity

medium — the hunt was broad (26 worktrees, 4 DBs, 394 checkpoint blobs, 20,552 tool calls scanned) but well-bounded. The refutation (no shared script; the kill mechanism is `scripts/stop-ensemble.sh`'s unit-owned branch with a composed unit token) is the load-bearing finding and should be folded into the D1 guard fix's rationale: the guard must ALSO refuse `bash scripts/stop-ensemble.sh` (and any command that transitively stops the unit) — not just composed-argv systemctl — because the script hides the destructive verb entirely.
