# R18 spawn_hot_instance auto-dispatch — Phase 2 Dev E2E + Phase 3 Evidence

**Date:** 2026-10-04 · **Commission:** R18 spawn-hot auto-dispatch dev E2E (real agent runs on 8079)
**Overall verdict: PASS (5/5 scenarios E1–E5)**

---

## 1. Environment

| Item | Value |
|---|---|
| Worktree | `/home/nea/ens-src-wt-r18` (branch `feature/spawn-hot-auto-enqueue`) |
| Commission commit | `6cb0a2ec` ("chore: R18 hygiene bundle") |
| Actual branch tip under test | `d4eeecad` ("test: pin message_id field in F2 census test (review W1)") — one test-only commit on top of 6cb0a2ec; `git describe` = `v0.16.12-25-gd4eeecad` |
| Daemon version (livez) | `0.16.12` |
| Port | 8079 ONLY (pid 3558741 during run; verified via `ss -tlnp` before killing predecessor) |
| DB | PostgreSQL `ensemble_dev` @ 127.0.0.1:5432 (user `ensemble_dev_app`) — see isolation evidence |
| Data dir | `/home/nea/dev-daemon-8079-r18/data` (ensemble.json copied verbatim from `/home/nea/dev-daemon-8079-srcfix/data/ensemble.json`) |
| Boot wrapper | `/tmp/r18-boot.sh` (env-scrub → source `/home/nea/dev-daemon-8079-v0.16.11/boot.env` → `ENSEMBLE_DATA_DIR=/home/nea/dev-daemon-8079-r18/data` → `cd /home/nea/ens-src-wt-r18 && exec .venv/bin/python -m daemon`) |
| Prior daemon freed | pid 3392589 (`python -m daemon`, cwd `/home/nea/ens-src-wt-snapshotfix`, PORT=8079 verified via `ps -fp` + `/proc environ`) — killed by pid, never by name; 9797/7979/8088 untouched |

**Boot-pattern provenance note:** the referenced pattern doc
`.agents/tester/RESULTS/2026-10-04-agent-snapshot-v1-dev-e2e.md` was **MISSING from disk AND git** in
both the worktree and the main tree (verified `ls` + `git ls-files | grep 2026-10-04` → only
`2026-10-04-post-restart-arm-notify-gate-1b75f23f.md` exists). The boot pattern was reconstructed from
the commission KB context block + `/proc/3392589/environ` of the prior daemon (cwd, PORT,
ENSEMBLE_DATA_DIR observed live before kill).

### Isolation evidence (all four, mandatory)

a. **Engine line** (`/home/nea/dev-daemon-8079-r18/logs/daemon-r18.stdout.log:14`):
   ```
   14:50:42 - daemon.repositories.factory - INFO - Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev
   ```
b. **livez** (poll hit after ~8s): `{"status":"alive","uptime_seconds":0.74,"version":"0.16.12"}` — branch version, NOT 0.16.11 ✓
c. **readyz**: `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},...}` ✓
d. **/proc/3558741/environ**: `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `POSTGRES_HOST=127.0.0.1` — dev lane, no strays ✓

Snapshot-create gate: `PUT /api/settings/snapshot-create {"enabled": true}` → `{"enabled":true}`
(R15 gate = system-default-project metadata `snapshot_create_enabled=on`; `spawn_hot_instance` itself never gated.)

---

## 2. Phase 1 counts (from commission context block — reproduced verbatim)

- `preexisting_base_confirmed`: same 5 TestAccessMemoryArchive failures on base 2ae91046, identical signatures
- `regressions`: 0
- `sweep_fallback`: 5 failed 44 passed (all 5 = pre-existing TestAccessMemoryArchive 'Access denied'); full-dir sweep TIMEOUT at unrelated test_upgrade_tools.py::TestSystemRestartMatrix
- `targeted`: test_snapshot_behavior_spot.py 14 passed (1.68s); test_snapshot_tools.py 64 passed (4.86s); test_snapshot_v3.py 57 passed (4.12s); **total 135 passed, 0 failed, 0 skipped, 0 errors in 12.91s**

---

## 3. Scenario setup

- **Parent agent:** `tester` (holds `instance` category → `spawn_hot_instance`; AND `snapshot_create`/`snapshot_search` — the only candidate satisfying the same-parent snapshot+spawn requirement; ari excluded by design, `worker`/`explorer` lack the instance category).
- **Child agent:** `worker` (in tester's `team_members=['explorer','worker']` + implied {kb-writer, image-reader}).
  ⚠️ Deviation from the suggested `wanderer` child: **wanderer is NOT in tester's allow-set** — spawning it would deterministically hit the E4 permission gate (`_check_team_membership`, daemon/tools/_auth.py:54; effective allow-set verified in the live E4 error text). The commission explicitly allows "another lightweight agent you confirm". `wanderer` was used for E4 instead — a guaranteed-denied pairing.
- **Parent instance:** `b986c31e-83f5-485c-9381-78d14fcbaa89` (created 14:54:17Z, project `d395d775-853f-432a-8598-66f94b77fd66`, fresh project `r18-e2e-auto-dispatch` created for the run).
- **Warm baseline:** parent called `snapshot_create` on itself → snapshot `cb75f8ba-7dd0-4179-8aa2-8843ed317f6c` (tool result status "running"; DB row flipped to `active` before the spawn — verified via read-only psql).
- Poll method: `GET /api/instances/{id}` + `GET /api/instances/{id}/messages` every 5s; children received **NO follow-up message** in E1–E4 (E5's manual follow-up is the scenario itself).
- All artifacts: `/home/nea/dev-daemon-8079-r18/logs/e2e-artifacts/` (raw JSON payloads, poll transcripts, census greps).

---

## 4. Scenarios

### E1 — WARM auto-start (HEADLINE): **PASS**

Step timings (UTC):

| Step | Time |
|---|---|
| T3 spawn instruction POST to parent | 14:55:47.62 |
| Census `[SnapshotSpawnWarm]` + `[SnapshotAutoDispatch]` + child row created | 14:56:10.03 |
| Child turn-1 context messages (incl. digest) created | 14:56:17.24 |
| Parent reply with raw tool-result JSON | 14:56:18.41 |
| Child first assistant tool call | 14:56:27.17 |
| Child completion (status=completed) | 14:56:30.09 |

**spawn-call → first child activity ≈ 17s; spawn-call → completion ≈ 20s** (deadline 4 min ✓). No follow-up sent.

Tool-result JSON captured verbatim from parent's reply:

```json
{"instance_id": "979cdaa9-a4e8-4874-8a99-b70b6df5dc55", "started": "warm", "snapshot_id": "cb75f8ba-7dd0-4179-8aa2-8843ed317f6c", "staleness": {"snapshot_age_days": 0.001, "freshness": "fresh", "warnings": [], "repo_state": null}, "hint": "Warm-started from snapshot cb75f8ba-7dd0-4179-8aa2-8843ed317f6c (age 0.001d; tags project:d395d775-853f-432a-8598-66f94b77fd66, agent:tester, role:tester, lineage:b986c31e-83f5-485c-9381-78d14fcbaa89, runtime:0.16.12, kind:investigation, subsystem:snapshot, topic:r18-e2e-warm) — auto-dispatched as first turn (do NOT call send_message again — the child is already working on this task)", "error": null}
```

**Warm-ordering check (step 5)** — child message history `e1-child-messages.json`:
`[0] system` → `[1] user [SYSTEM CONTEXT: Related Project]` → `[2] user [SYSTEM CONTEXT: Agent Snapshot Digest]` (4075 chars, provenance source_instance_id=b986c31e…, captured_at 14:55:35.9, LIVE-CAPTURE marker) → `[3] user [SYSTEM CONTEXT: Skills]` → `[4] user "Compute 17*23, state the result, then finish."` → `[5] assistant tool_calls` → `[6] assistant final`.
**Digest block ([2]) appears BEFORE the task HumanMessage ([4]) in turn-1 context** ✓ (all context blocks share created_at 14:56:17.243259; task rides last).

Child final answer: "✅ Task Complete: Computed 17 × 23 … **391** (verified via shell arithmetic)".

### E2 — COLD auto-start: **PASS**

| Step | Time |
|---|---|
| Spawn instruction POST | 14:57:10.99 |
| Census `[SnapshotAutoDispatch]` + child row created | 14:57:27.77 |
| Child turn-1 context created | 14:57:35.76 |
| Child completion | 14:57:44.74 |

spawn-call → completion ≈ **33.6s** (deadline 4 min ✓). Tool result:

```json
{"instance_id": "2a2d53eb-8d0a-4238-8029-acd679f82099", "started": "cold", "snapshot_id": null, "staleness": {}, "hint": "No matching snapshot — spawned cold (searched: Compute 12+30, state the result, then finish.; reason: no-hit) — auto-dispatched as first turn (do NOT call send_message again — the child is already working on this task)", "error": null}
```

Child history has **NO digest block** (cold parity ✓): system → project ctx → skills ctx → task → answer "**12 + 30 = 42**". Auto-started with no follow-up ✓.

### E3 — Census log: **PASS**

Verbatim lines from `/home/nea/dev-daemon-8079-r18/logs/daemon-r18.stdout.log` (the daemon's only log sink in this boot pattern; no file log written under data/logs):

```
14:56:10 - daemon.tools.snapshot_tools - INFO - [SnapshotSpawnWarm] {"snapshot_id": "cb75f8ba-7dd0-4179-8aa2-8843ed317f6c", "new_instance_id": "979cdaa9-a4e8-4874-8a99-b70b6df5dc55", "counter": "spawn_warm", "project_id": "d395d775-853f-432a-8598-66f94b77fd66"}
14:56:10 - daemon.tools.snapshot_tools - INFO - [SnapshotAutoDispatch] {"event": "spawn_hot_auto_dispatch", "caller_iid": "b986c31e-83f5-485c-9381-78d14fcbaa89", "target_iid": "979cdaa9-a4e8-4874-8a99-b70b6df5dc55", "content_len": 45, "message_id": "1540ffed-2157-4f80-b34b-4fe03006d98a"}
14:57:27 - daemon.tools.snapshot_tools - INFO - [SnapshotAutoDispatch] {"event": "spawn_hot_auto_dispatch", "caller_iid": "b986c31e-83f5-485c-9381-78d14fcbaa89", "target_iid": "2a2d53eb-8d0a-4238-8029-acd679f82099", "content_len": 45, "message_id": "85fa9cef-acac-4ff5-9544-8df2c1d41a4c"}
14:59:52 - daemon.tools.snapshot_tools - INFO - [SnapshotAutoDispatch] {"event": "spawn_hot_auto_dispatch", "caller_iid": "b986c31e-83f5-485c-9381-78d14fcbaa89", "target_iid": "fc288cf5-b38d-4d62-bbd0-00d775eddf02", "content_len": 46, "message_id": "c1b57037-b860-4680-9d4d-45efe438580e"}
```

Exactly one line per dispatched spawn (E1 warm, E2 cold, E5); **zero lines for E4 (blocked)** ✓. `content_len` matches each task byte-length (45/45/46).

### E4 — Negative `blocked` (1090f308 contract): **PASS**

Pairing: tester → `wanderer` (guaranteed denied; wanderer ∉ tester's effective allow-set). Tool result verbatim (14:58:33.8):

```json
{"instance_id": null, "started": "blocked", "snapshot_id": null, "staleness": {}, "hint": "Permission denied — spawn blocked (reason: permission-denied). Nothing was spawned. Resolve the authorization problem (e.g. add the requested agent to the caller's team_members) and retry. Detail: ERROR: Agent 'tester' is not allowed to spawn 'wanderer'. Allowed team members: ['explorer', 'image-reader', 'kb-writer', 'worker']", "error": "ERROR: Agent 'tester' is not allowed to spawn 'wanderer'. Allowed team members: ['explorer', 'image-reader', 'kb-writer', 'worker']"}
```

Absence evidence:
- **No child row:** psql `SELECT instance_id, agent_id, parent_id FROM instances WHERE created_at > '2026-10-04T14:50:00'` returns EXACTLY 3 rows — parent b986c31e (tester) + workers 979cdaa9 (E1) + 2a2d53eb (E2). **Zero wanderer rows** created by the run. (API instance list also shows the only in-window spawns are those two workers; third worker `b22e6b75…` predates boot at 14:03:00Z, different project/parent — pre-existing shared-DB residue, not ours.)
- **No enqueue:** census grep count stays at 2 (E1+E2) immediately after E4; no line mentions the blocked probe (final count 3 = +E5 only).
- `started:"blocked"`, `instance_id:null`, actionable hint naming the resolution ✓ (exact `_denied_result` 6-key shape, daemon/tools/snapshot_tools.py:498-540).

### E5 — No-double-enqueue smoke (auto + manual follow-up): **PASS**

- Spawn (topic:r18-e5-double-echo) → cold, child `fc288cf5-b38d-4d62-bbd0-00d775eddf02`, auto-dispatched at 14:59:52.4 (census), auto answer "100 − 58 = **42**" at 15:00:02.9.
- Manual legacy follow-up POSTed at 15:00:07.1 ("Legacy-habit follow-up (E2E): ALSO compute 7*7…") → arrived as a new user turn at 15:00:16.8 → second answer "7 × 7 = **49**" at 15:00:23.4; status completed 15:00:27; **no crash**.
- History shows BOTH messages: auto-dispatched task turn AND manual follow-up turn, each with its own assistant completion — v1 behavior honestly documented: the manual message does NOT collide, dedupe, or abort the auto-dispatched turn; the child simply processes two sequential turns.

### EXTRA — enqueue-result semantics (reviewer-relevant, ba963551)

Result payload is **exactly 6 keys** — `instance_id`, `started`, `snapshot_id`, `staleness`, `hint`, `error` (snapshot_tools.py:1496-1508; the conditional 7th `warnings` key was removed per Wave 2b review FIX 4 — warnings surface via `staleness.warnings` + hint text). Enqueue outcome surfaces ONLY through:

1. **`hint` suffix** (4 mutually exclusive variants, snapshot_tools.py:1466-1487):
   - success: `" — auto-dispatched as first turn (do NOT call send_message again — the child is already working on this task)"` — observed live in E1, E2, E5;
   - empty task: `" — auto-dispatch SKIPPED: task was empty; caller MUST call send_message(instance_id=\"…\", message=<task>) to start the child"` (code-inspected; not E2E-exercised — no scenario forces it);
   - opt-out: `" — auto_dispatch=False: caller MUST call send_message(…)"` (code-inspected; not E2E-exercised);
   - failure: `" — auto-dispatch ERROR: {auto_dispatch_error}"` appended.
2. **`error` field** — normally `null`; on enqueue failure carries the loud F1 contract text `"ERROR: spawn succeeded but auto-dispatch failed: {type}: {exc}. Call send_message(instance_id=\"…\", message=<your task>) explicitly to deliver the first turn."` — **`started` is deliberately NOT downgraded to "blocked"** (spawn DID succeed; snapshot_tools.py:1427-1444). Not observed live (all 3 enqueues succeeded; zero `[Snapshot] spawn_hot_instance auto-dispatch enqueue failed` F1 warning lines in the log — grep clean).
3. **`[SnapshotAutoDispatch]` census line** carries the internal `message_id` (only place the enqueue's message id is exposed) — live-verified with 3 real ids matching the children's turn-1 task rows.

No dedicated top-level `auto_dispatch`/`dispatched`/`enqueue_*` key exists — a caller infers enqueue state from hint text + census log; this matches ba963551's "result-inspection invariant" intent (the hint is always one of the four enumerable states).

Unrelated log noise during the run (pre-existing dev-env items, NOT R18): startup `JOURNAL: …clear_all preserve_in_flight=True` queue-discard lines (QUEUE_DISCARD_ON_STARTUP=true), MCP server 'plane' config-invalid/connection-closed retries, maintenancer deny-entry config warning. Zero R18-family WARN/ERROR lines.

---

## 5. Summary

| Scenario | Verdict | Key evidence |
|---|---|---|
| E1 warm auto-start | **PASS** | started:"warm" payload; digest-before-task ordering; 391; no follow-up; 20s spawn→done |
| E2 cold auto-start | **PASS** | started:"cold", snapshot_id:null; auto-start; 42; 33.6s spawn→done |
| E3 census log | **PASS** | 3× `[SnapshotAutoDispatch]` + 1× `[SnapshotSpawnWarm]`, content_len exact |
| E4 blocked | **PASS** | started:"blocked", instance_id:null, 0 new rows (psql), 0 census lines |
| E5 double-echo smoke | **PASS** | both turns processed (42 → 49), no crash |

**Overall: PASS — the R18 auto-dispatch feature works end-to-end on real LLM runs** (warm + cold auto-start, blocked does NOT enqueue, double-message legacy habit degrades gracefully).

## 6. Reference

`2026-10-04-v01612-spawn-enqueue-forensic-audit.md` is **NOT present in this worktree's tree**
(`git ls-files | grep -i forensic` → only maintenancer knowledge/skill files; `ls` confirms absent).
Per commission instructions, referenced here without an addendum (addendum path applies only if the
file existed in-tree). Fix commits for the audited spawn-enqueue contract trap, as commissioned:
branch tip 6cb0a2ec; key commits 4e5d14f4, b9982225, ba963551, 6cb0a2ec.

## 7. Cleanup record

(Executed immediately AFTER this evidence commit — commission ordering: commit precedes cleanup.
Final verified proof lives in the commission report; the steps below are the executed procedure.)

- R18 daemon (pid 3558741, verified owner of 8079 via `ss -tlnp` immediately before kill) stopped.
- 8079 restored to frozen v0.16.11 via `bash /home/nea/dev-daemon-8079-v0.16.11/boot.sh`; livez re-verified: version 0.16.11, engine line `ensemble_dev`.
- Worktrees removed from the main repo: `/tmp/r18-base-2ae91046` and `/home/nea/ens-src-wt-r18` (evidence commit confirmed in `git log` of the branch ref first).
- Main tree `/home/nea/ensemble-src` never modified (verification-only commission; single commit = this evidence file on the feature branch).
