# Forensic Audit — Suspected v0.16.12 spawn→child-enqueue Regression (CLOSED)

- **Date**: 2026-10-04 (documentation pass ~06:55–07:30 UTC)
- **Status**: CLOSED — audit documentation complete; optional checkpoint decode EXECUTED (read-only)
- **Commission lineage**: follow-up to commissions cfded28b / da8e4809; leader-synthesized verdict bb100883
- **Mission constraints honored**: NO repro attempts · NO fixes · NO restarts · NO DB mutations · dev daemon PID 3392589 never disturbed (still serving) · PB-F1 secret discipline throughout (zero credential values printed; `/proc/*/environ` never read)
- **Evidence verification**: all key log excerpts re-pulled verbatim from disk with file+line citations by a read-only worker (2 dispatch rounds); checkpoint decode via second read-only worker (§12)

---

## 1. Executive Verdict

**Leader-synthesized FINAL VERDICT (reproduced verbatim, as directed):**

> (a) SUSPECTED REGRESSION REFUTED — no code defect on the spawn→enqueue seam. Root cause of the observed child stranding: `spawn_hot_instance` CONTRACT TRAP + mission-shape-dependent agent behavior. The three affected children were never sent a first message by their parents; nothing was dropped by the pipeline.
>
> THREE CONVERGENT PROOF LINES:
> - P1 (static diff-closure): every carrier file byte-identical v0.16.11..1090f308 except non-spawn hunks (manager.py: import + boot-wipe + arm-notify kwarg only; snapshot_tools.py: perm-denial branch only). A v0.16.11 spawn-side auto-enqueue would survive byte-for-byte — none exists at HEAD or at v0.16.11 (git-show grep: 0 enqueue/send refs in spawn paths). `spawn_hot_instance`'s task param consumers: snapshot search (snapshot_tools.py:995) + hint text (:989) — never forwarded, never enqueued.
> - P2 (dev-log census, affected daemon): complete [LLM] Tool call inventory = 5× spawn_hot_instance + 1× snapshot_create, ZERO send_message (string absent file-wide); complete dispatch census = 8 jobs each 1:1 preceded by HTTP POST; children's first turns begin exactly at manual POSTs; parents' post-spawn responses were narration of the tool result. The agent-tool enqueue success line (`agent_send_message routed via enqueue`, instance.py:3589) logs on ANY tool enqueue — absent.
> - P3 (prod DB taxonomy, v0.16.11 window, 386 rows): source families exclusively explicit-sender (internal_agent:<parent>, api, discord, …) — NO spawn-derived source family; every sampled spawned child's first message = internal_agent:<parent> carrying parent task text.
>
> The v0.16.11 "~6s auto-dispatch" control was a MISATTRIBUTION: control log shows spawn 20:19:30 → parent coder's OWN send_message call 20:19:35 → enqueue+task 20:19:36 (the only 'routed via enqueue' hit in the entire control file). Cohort divergence = mission shape: 10-03 E2E missions required the child's answer (graded) → coder sent; 10-04 perm-fix missions were result-shape verification → coders narrated and ended.
>
> ENVIRONMENT ARTIFACTS RULED OUT: single verified-up daemon; PG rotation 18h prior with writes working throughout; no boot overlap / port-DB switching. AFFECTED ARTIFACT: frozen v0.16.12 release binary (releases/v0.16.12/ensemble-prod, mtime Oct 3 13:30, PRE-1090f308 — /proc/3392589/exe proof), dev-fenced (ENSEMBLE_SELF_ENV=dev, ensemble_dev, cwd worktree /home/nea/ens-src-wt-snapshotfix). Incidental: 4th coder f4735227 at 05:17:07 exhibited the pre-fix perm-denial masquerade (no child, truncated result) — corroborates binary age.
>
> REAL DEFECTS / ACTION ITEMS:
> A. spawn_hot_instance contract gap (predates v0.16.12; reachable with snapshot flag R15 OFF — tool registered unconditionally; only snapshot_create is R15-gated): task accepted-but-never-delivered + result teaches no follow-up. Fix direction: auto-enqueue task as child's first turn OR add follow-up instruction (parity with spawn_instance result, instance.py:2330).
> B. Completion-lane REPORTING inconsistencies (NOT a wedge): FP4 discharge gates worked (11–40s post-child-completion on all three parents); anomalies = lineage enumeration printing "no parent, no children" while child parent_id is set (transient instance_hierarchy vs permanent parent_id), status=COMPLETED log line then WAITING_CHILDREN→RUNNING flip with no publish line, PP1 zero-watcher terminal fires. No job stranding evidenced (both anchor jobs done/completed; prod scan: every waiting_children parent has genuinely live children). Bounded follow-up audit owed.
> C. Artifact hygiene: staged releases/v0.16.12 binary predates 1090f308 perm fix — release ceremonies must rebuild from latest or verify artifact content.
> D. Ops debts: rotation-broken external DB conns re-registration owed; parent checkpoint blobs undecoded (see optional task below) — the logless S4/S6 early-return ("Instance not found") is theoretically unexcluded but has ZERO supporting evidence (0 prod log hits; no coder surfaced such an error; complete census shows no send attempt at all).
>
> PROMOTE IMPLICATION: the (a) finding does NOT block or justify rolling back v0.16.12 — refuted as a regression; the contract gap predates it. NOTE: live is already v0.16.12 since 10-03 15:56 (per pinned critical note 6ab92289) — the original promote-gate framing was stale. The LIVE operational concern is the SEPARATE prod claim-lane defect (Tasks never claimed → FE empty) under its own parallel commission — explicitly OUT OF SCOPE for this file; mention only as a clearly-fenced adjacent item.

### 1.1 Corrigendum — documentation-pass corrections (evidence re-verification)

The verdict above is preserved verbatim as directed. The read-only re-pull of disk evidence in this pass produced **six refinements**, none of which alter any conclusion (marked by section):

1. **Tool-call census is 4× `spawn_hot_instance` + 1× `snapshot_create`, not 5×+1×** (§7). Verified file-wide census = 5 `[LLM] Tool call` lines total: lines 208, 271, 440, 626 (spawn_hot_instance — the 4th being the f4735227 masquerade call) + line 385 (snapshot_create). ZERO `send_message` — unchanged, which is the material claim.
2. **Affected artifact correction (§8)**: `/proc/3392589/exe` resolves to the uv-managed interpreter `/home/nea/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13`; `cmdline` = `/home/nea/ens-src-wt-snapshotfix/.venv/bin/python -m daemon`; `cwd` = `/home/nea/ens-src-wt-snapshotfix`. **PID 3392589 ran WORKTREE CODE via the worktree venv, not the frozen `releases/v0.16.12/ensemble-prod` binary** — the "/proc exe proof" as stated conflated interpreter with artifact. The running code demonstrably predated 1090f308 (boot 05:14:34 precedes the fix merge; the 05:17:07 masquerade is behavioral confirmation), so the masquerade corroboration stands with "worktree code at boot" substituted for "frozen binary". Item C (staged release binary predates the fix) remains valid as an on-disk hygiene finding.
3. **Report-injection claim timestamp**: the `[ReportInjection] … claimed for TASK delivery` line is at **05:43:56** (line 801); the 05:44:37–38 block is the delivery/adjudication processing of that same message_id 6a2d0784 (§4, §9).
4. **Log file is 883 lines** (not ~874), and the `Starting Ensemble v0.16.12` boot line appears duplicated at lines 2–3 (PID on line 4) — cosmetic, immaterial.
5. **Four additional `Spawning instance` lines exist** (250, 366, 424, 610): these are the **coder sessions themselves** (`agent=coder, parent=None` — 715c9ef9, 526ec0ca, a4f940b2, 655a7fbe), each immediately followed by that session's first `/messages` POST. No instance-creation HTTP request of ANY variant is logged file-wide (§7) — creation mechanism not HTTP-POST-logged (message-driven auto-spawn suspected; identifying it is out of scope for this audit). **No children exist beyond the three inventoried + the one masquerade non-spawn.**
6. **Dev-DB enqueue census delta (§3)**: the secondhand 05:45:08Z census reported "ONLY ONE row total for all three children"; the verified decode (§12) shows **exactly one `api`-source `message_queue` row per child (3 total)**, each matching a logged manual POST. The material point — zero spawn-derived enqueue rows, every first message manual — holds under both censuses; the secondhand query was evidently scoped differently.

---

## 2. Commission & Method

- **Commission**: follow-up forensic audit commissioned off the leader instance bb100883, closing out investigations cfded28b / da8e4809. Three parallel investigator tracks produced the convergent proof lines P1 (static diff-closure), P2 (dev-log census), P3 (prod DB taxonomy), plus a discriminator round contrasting the v0.16.11 control cohort.
- **Method**: two-lane convergence — (i) code-path audit across the v0.16.11..1090f308 delta with git-show greps on both refs; (ii) behavioral census over the affected daemon's complete log (tool calls, dispatches, enqueue-success line, Traceback, ERRORs) cross-checked against DB row taxonomy in both dev and prod.
- **User directive honored — no blind repro**: the user's no-blind-repro directive was honored. A live reproduction was **DECLINED on evidence sufficiency**: three independent proof lines (static, log census, DB taxonomy) converge on the same mechanism, the causal question ("did the pipeline drop a message?") is answered negatively by complete censuses rather than by a single observation, and any repro would require spawning children against the still-serving dev daemon PID 3392589 — violating the mission's no-disturbance constraints for zero marginal evidentiary value. The optional checkpoint decode (§12) was executed instead as read-only formal closure.
- **This documentation pass**: verbatim re-pull of all cited log lines from disk (worker c4d43e59, two rounds), read-only checkpoint decode (worker 85ce15b1), and this file. All constraints (no repro / no fixes / no restarts / no DB mutations / PID 3392589 untouched) held throughout.

---

## 3. Affected Children Inventory

Dev DB (`ensemble_dev`); original table driver-quoted via psql (secondhand-but-contemporaneous); spawn/first-msg times cross-verified against the affected daemon log (§4) and the decode (§12):

| Child | Spawn path | Parent | Created | First msg (manual) | Idle | Outcome |
|---|---|---|---|---|---|---|
| 60392714-0cfe-4a54-9958-cb915cc7cf6d | warm (explicit snapshot b7471da1) | a4f940b2 (coder-C) | 05:27:21.307Z | 05:33:44.067Z | 6m23s | completed ~9s |
| c654e3b1-fe8f-4451-a26f-b60434c8329c | cold (default search no-hit) | 715c9ef9 (coder-A; held WAITING_CHILDREN) | 05:20:41.343Z | 05:43:45.577Z | 23m04s | completed after send |
| ea2ae968-26e2-4810-a1f8-1163915bd2f9 | cold (nonsense tag) | 655a7fbe (coder-D) | 05:37:13.504Z | 05:43:12.224Z | 5m59s | completed after send |

- **Incidental 4th**: coder f4735227 called `spawn_hot_instance` at 05:17:07 (log line 208) — **no child spawned** (pre-1090f308 perm-denial masquerade: zero `Spawning instance` lines with parent=f4735227 file-wide; investigator-reported truncated tool result). Corroborates that the running code predated the 1090f308 fix.
- **Dev-DB enqueue census (05:45:08Z, secondhand)**: reported "only one row total for all three children — the manual send to 60392714". **Verified decode (§12)**: exactly one `message_queue` row + one task row per child, all `source=api`, `root_source=None`, `error_message=None` — the three manual sends. See corrigendum #6.
- Spawning tool results returned **SUCCESS** in all three cases (warm result carried snapshot_id/staleness — log line 447; cold carried `started:"cold"`, `error:null` per investigator report). No enqueue failure was ever logged because no enqueue was ever attempted.

---

## 4. Timeline Reconstruction

Affected log: `/home/nea/dev-daemon-8079-srcfix/logs/daemon-dev-8079-srcfix.stdout.log` (883 lines). All line numbers verified this pass.

**Environment events ruled out (investigator-reported, 10-03)**: 11:13Z prior daemon stop; 11:17Z PG role rotation — both ~18h before the window, with writes demonstrably working throughout the affected window (children completed, checkpoints and report injections written; §8).

| Time (10-04 UTC) | Event | Log lines |
|---|---|---|
| 05:14:13 | Restart marker `ATTEMPT2_UVSYNC_RESTART` | 1 |
| 05:14:34 | Boot: `Starting Ensemble v0.16.12` (duplicated) ; server process **PID 3392589** | 2–3, 4 |
| 05:14:40–42 | Boot-time MCP 'plane' Connection-closed cluster (ruled out — tool lane unaffected) | 164–169 |
| 05:16:53 | f4735227 first POST + dispatch | 183–184 |
| 05:17:07 | f4735227 `[LLM] Tool call: spawn_hot_instance` → **MASQUERADE, no child** (no `parent=f4735227` spawn anywhere) | 208 |
| 05:19:48 | coder-A session 715c9ef9 spawned (agent=coder, parent=None, model=coding2) | 249–250 |
| 05:19:59 | coder-A first POST + dispatch (job baace4c3) | 251–252 |
| 05:20:25 | coder-A `[LLM] Tool call: spawn_hot_instance` (no snapshot_id → cold) | 271 |
| 05:20:41 | Child **c654e3b1** spawned | 279 |
| 05:20:55 | coder-A narration response (`spawn_hot_instance called exactly as specified…`); tree_liveness gate → WAITING_CHILDREN, publish suppressed | 285, 293 |
| 05:26:15 | coder session 526ec0ca spawned (agent=coder, parent=None) + POST + dispatch | 365–368 |
| 05:26:20 | 526ec0ca `[LLM] Tool call: snapshot_create` (snapshot-only mission; no child) | 385 |
| 05:27:16 | coder-C session a4f940b2 spawned + POST + dispatch | 423–426 |
| 05:27:19 | coder-C `[LLM] Tool call: spawn_hot_instance` (explicit snapshot_id b7471da1) | 440 |
| 05:27:21 | Child **60392714** spawned — `[SnapshotSpawnWarm]` | 445, 447 |
| 05:27:25 | a4f940b2 tree_liveness → WAITING_CHILDREN | 459 |
| 05:33:44 | **Manual POST → child 60392714** + dispatch | 533–534 |
| 05:34:16 | a4f940b2 FP4 L1 discharge (WAITING_CHILDREN→RUNNING); observer already_finalized | 573, 582 |
| 05:37:06 | coder-D session 655a7fbe spawned + POST + dispatch | 609–612 |
| 05:37:11 | coder-D `[LLM] Tool call: spawn_hot_instance` (nonsense tag → cold) | 626 |
| 05:37:13 | Child **ea2ae968** spawned | 631 |
| 05:37:22 | coder-D narration response (`## spawn_hot_instance result … verbatim JSON`); tree_liveness → WAITING_CHILDREN | 635, 643 |
| 05:43:12 | **Manual POST → child ea2ae968** + dispatch | 717–718 |
| 05:43:32 | 655a7fbe FP4 L1 discharge; observer already_finalized | 764, 773 |
| 05:43:44–45 | **Manual POST → child c654e3b1** + dispatch | 777–778 |
| 05:43:56 | Child c654e3b1 READY response; `[ReportInjection] … claimed for TASK delivery` (parent=715c9ef9) | 791, 801 |
| 05:44:37 | coder-A adjudication response; completion processing; 715c9ef9 FP4 L1 discharge; observer already_finalized | 809, 810, 817, 826 |
| 05:44:38 | PP1 zero-watcher terminal fires (work_notifier + observer outbox) for work_id=baace4c3 | 827–828 |

Key shape: **every child's first turn begins at a manual `/messages` POST, never at its spawn**; the parents' post-spawn turns were narrations (lines 285, 635), not sends.

---

## 5. Contrast Cohorts

### 5.1 Control decomposition (v0.16.11, the "~6s auto-dispatch" myth-killer)

Control log `/home/nea/dev-daemon-8079-v0.16.11/logs/daemon-dev-8079.stdout.log`, lines 2119–2132 (all verified verbatim this pass):

```
2119: 20:19:24 - daemon.graph - INFO - [LLM] Tool call: spawn_hot_instance — {'agent_id': 'worker', 'instance_name': 'snapshot-e2e-dev-state-reporter', 'task..., tools: ['spawn_hot_instance']
2125: 20:19:30 - daemon.services.instance_lifecycle - INFO - Spawning instance d56f6779-b2a0-4459-ad4d-23406afc10c5 (agent=worker, parent=91b22f49-fc05-4d35-9fa4-58a785bc967e, name=snapshot-e2e-dev-state-reporter, model=coding, source=llm_models)
2127: 20:19:30 - daemon.tools.snapshot_tools - INFO - [SnapshotSpawnWarm] {"snapshot_id": "b7471da1-02b6-4a0a-ba0d-61b500b112b5", "new_instance_id": "d56f6779-b2a0-4459-ad4d-23406afc10c5", "counter": "spawn_warm", "project_id": "4c3b171f-67d4-49c6-b5ab-ff17966cc8c8"}
2130: 20:19:35 - daemon.graph - INFO - [LLM] Tool call: send_message — {'instance_id': 'd56f6779-b2a0-4459-ad4d-23406afc10c5', 'message': 'Report the c..., tools: ['send_message']
2131: 20:19:36 - daemon.tools.instance - INFO - agent_send_message routed via enqueue
2132: 20:19:36 - daemon.services.task_processor - INFO - Processing message task 1137: message=a47b0ff1..., instance=d56f6779...
```

The "~6s" delta decomposes into **spawn 20:19:30 → the parent coder's OWN `send_message` call 20:19:35 → enqueue+task 20:19:36**. Line 2131 is the only `routed via enqueue` hit in the entire control file — the auto-dispatch attribution was a misattribution of the coder's own explicit send.

### 5.2 Prod DB taxonomy (v0.16.11 window)

386 rows (investigator-reported, P3): source families exclusively explicit-sender (`internal_agent:<parent>`, `api`, `discord`, …) — **no spawn-derived source family exists**. 55/55 sampled spawned children had first message = `internal_agent:<parent>` carrying parent task text.

### 5.3 Mission-shape explanation (cohort divergence)

- 10-03 E2E missions **required the child's answer** (graded outputs) → coders sent (`send_message` observed in control cohort).
- 10-04 perm-fix missions were **result-shape verification** → coders narrated the tool result and ended. Decode corroboration (§12): all three spawn tasks were literally `"Reply with the single word READY and nothing else."` — the parents' missions never needed the child's output for their own reports.

---

## 6. Code-Path Audit (investigator P1, static)

- **Diff-closure v0.16.11..1090f308**: every carrier file byte-identical except non-spawn hunks — `manager.py`: import + boot-wipe + arm-notify kwarg only; `snapshot_tools.py`: perm-denial branch only. A hypothetical v0.16.11 spawn-side auto-enqueue would have survived byte-for-byte into 1090f308; none exists at either ref (git-show grep: 0 enqueue/send refs in spawn paths).
- **`spawn_hot_instance` task-param consumers**: snapshot search (snapshot_tools.py:995) + hint text (:989) — the task is never forwarded, never enqueued.
- **Caller families**: four send-message caller families converge on the single chokepoint `_prepare_enqueued_message` (daemon/services/instance_messaging.py:1522-1540) — all 13 pre-INSERT exits were enumerated and eliminated against the observed evidence (no enqueue attempt exists at all, so no exit could have fired).
- **Contract gap (defect A)**: `spawn_hot_instance` accepts `task` but never delivers it and its result teaches no follow-up — parity gap vs `spawn_instance`'s result (instance.py:2330). Reachable with snapshot flag R15 OFF (tool registered unconditionally; only `snapshot_create` is R15-gated). Predates v0.16.12.

---

## 7. Log Census (both files, verified this pass)

### 7.1 Affected daemon — `/home/nea/dev-daemon-8079-srcfix/logs/daemon-dev-8079-srcfix.stdout.log`

- **883 lines**; boot 05:14:34 v0.16.12 (lines 2–3, duplicated), PID 3392589 (line 4, sole occurrence file-wide); uvicorn on 127.0.0.1:8079 (line 163).
- **Complete `[LLM] Tool call` census — 5 lines total** (see corrigendum #1):
```
208: 05:17:07 - daemon.graph - INFO - [LLM] Tool call: spawn_hot_instance — {'agent_id': 'worker', 'task': 'Reply with the single word READY and nothing els..., tools: ['spawn_hot_instance']        (f4735227 — masquerade)
271: 05:20:25 - daemon.graph - INFO - [LLM] Tool call: spawn_hot_instance — {'agent_id': 'worker', 'task': 'Reply with the single word READY and nothing els..., tools: ['spawn_hot_instance']        (coder-A)
385: 05:26:20 - daemon.graph - INFO - [LLM] Tool call: snapshot_create — {'name': 'scenario-b-fresh-verification-snapshot', 'tags': ['kind:implementation..., tools: ['snapshot_create']                     (526ec0ca)
440: 05:27:19 - daemon.graph - INFO - [LLM] Tool call: spawn_hot_instance — {'agent_id': 'worker', 'instance_name': 'snapshot-e2e-ready-check', 'snapshot_id..., tools: ['spawn_hot_instance']            (coder-C)
626: 05:37:11 - daemon.graph - INFO - [LLM] Tool call: spawn_hot_instance — {'agent_id': 'worker', 'task': 'Reply with the single word READY and nothing els..., tools: ['spawn_hot_instance']        (coder-D)
```
- **Absences (grep counts = 0, all confirmed)**: `Traceback` · `agent_send_message routed via enqueue` · any Tool-call line containing `send_message`.
- **Dispatch census — 8/8** `found PENDING job` lines (184, 252, 368, 426, 534, 612, 718, 778), each 1:1 immediately preceded by an HTTP `POST /api/instances/<id>/messages 200` (lines 183, 251, 367, 425, 533, 611, 717, 777). Targets in order: f4735227, 715c9ef9, 526ec0ca, a4f940b2, **60392714 (child)**, 655a7fbe, **ea2ae968 (child)**, **c654e3b1 (child)** — the children's first turns begin exactly at their manual POSTs.
- **Spawn census — 7 `Spawning instance` lines file-wide** (250, 279, 366, 424, 445, 610, 631): three affected children (279=c654e3b1, 445=60392714, 631=ea2ae968, all `agent=worker, parent=<coder>`) + the four coder sessions themselves (250=715c9ef9, 366=526ec0ca, 424=a4f940b2, 610=655a7fbe, all `agent=coder, parent=None`). **Zero** creation HTTP POSTs of any variant file-wide (`POST /api/instances` minus `/messages` = 0; `/api/spawn` = 0; any non-/messages POST = 0) — coder-session creation was not HTTP-POST-logged (corrigendum #5).
- **ERROR census (05:14–05:50)**: 24 ERROR lines, all MCP 'plane' Connection-closed; six clusters — the four in-window ones cited by the verdict (05:19:44 @237, 05:26:12 @353, 05:27:12 @411, 05:37:03 @597) plus two earlier boot/first-turn clusters (05:14:40/42 @164–169, 05:16:53/55 @189–194). All retry-and-surrender (`WARNING … after 2 attempt(s)`); MCP lane only, spawn/message lanes unaffected.
- **f4735227 arc**: 34 log hits; tool call @208 between LLM invocations @207→@209 (05:16:58→05:17:07→05:17:13); **no perm-denial text appears in any logged line** (the only "denied/truncated" token is line 215's unrelated attestation-gate field `denied_count=0 … scanner_window_truncated=True`); no child (`parent=f4735227` absent from all Spawning lines).

### 7.2 Control daemon — `/home/nea/dev-daemon-8079-v0.16.11/logs/daemon-dev-8079.stdout.log`

Lines 2119–2132 verbatim in §5.1; line 2131 confirmed to contain `routed via enqueue`; block spans 20:19:24→20:19:36 (spawn→own-send→enqueue ≈ 6s).

---

## 8. Environment-Artifact Ruling

- **Single verified-up daemon**: one boot in the affected log (05:14:34, restart marker 05:14:13); PID 3392589 occurs exactly once; no second boot, no port/DB switching in-window.
- **PG rotation ruled out**: role rotation ~11:17Z on 10-03 (~18h prior); writes demonstrably worked throughout the window (three children completed, checkpoints + report injections written, queue/task rows written).
- **MCP 'plane' errors ruled out**: connection failures confined to the MCP lane (all 24 ERRORs); spawn and message lanes completed successfully around every cluster.
- **Affected artifact (corrected — corrigendum #2)**: PID 3392589 runs `/home/nea/ens-src-wt-snapshotfix/.venv/bin/python -m daemon` (cmdline, 3 argv entries, nothing credential-shaped), interpreter `/home/nea/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13` (exe readlink), cwd `/home/nea/ens-src-wt-snapshotfix` (cwd readlink) — **worktree code at boot, not the frozen `releases/v0.16.12/ensemble-prod` binary**. Running code predated 1090f308 (boot 05:14:34 precedes the fix merge; 05:17:07 masquerade is behavioral confirmation). Dev-fencing corroborated: DB = `ensemble_dev` (decode-time assert, §12); `ENSEMBLE_SELF_ENV=dev` per investigator report (environ deliberately not re-read — PB-F1).
- **Staged release binary staleness (item C)** remains true as an on-disk hygiene fact (mtime Oct 3 13:30, pre-1090f308) but is NOT what PID 3392589 executed.

---

## 9. FP4 / waiting_children Analysis (defect B — reporting, not a wedge)

- **FP4 discharge gates worked on all three parents**, post-child-completion: a4f940b2 05:34:16 (line 573; child completed ~05:33:53 → ~23s), 655a7fbe 05:43:32 (line 764; child completed ~05:43:21 → ~11s), 715c9ef9 05:44:37 (line 817; child READY 05:43:56 (line 791) / ReportInjection claim 05:43:56 (line 801) → ~41s).
- **tree_liveness holds**: all three parents downgraded to WAITING_CHILDREN with publish suppressed at first completion attempt (lines 293 @05:20:55, 459 @05:27:25, 643 @05:37:22 — `non-terminal member present (live leg holds) … mission is plausibly alive; defer to the next tick`).
- **Anomalies (investigator-reported, unchanged by this pass)**: lineage enumeration printing "no parent, no children" while child `parent_id` is set (transient `instance_hierarchy` vs permanent `parent_id` — decode corroborates the permanent side: 715c9ef9 `parent_id=None` at top level, children carry parent ids); a `status=COMPLETED` log line followed by WAITING_CHILDREN→RUNNING flip with no publish line; PP1 zero-watcher terminal fires (lines 827–828 @05:44:38, work_id=baace4c3 — `notify_watchers returned 0, the terminal report is NOT being delivered`).
- **No job stranding**: both anchor jobs done/completed; prod scan found every `waiting_children` parent has genuinely live children. Bounded follow-up audit owed (item B).

---

## 10. Verdict & Promote Implication

- **(a) SUSPECTED REGRESSION REFUTED** — see §1. The three children were never sent a first message by their parents; nothing was dropped by the pipeline. Root cause: `spawn_hot_instance` contract trap + mission-shape-dependent agent behavior. Corrigendum items §1.1 refine evidence details, not conclusions.
- **Promote implication**: the (a) finding does **not** block or justify rolling back v0.16.12 — refuted as a regression; the contract gap (defect A) predates it. **Live is already v0.16.12 since 10-03 15:56** (pinned critical note 6ab92289) — the original promote-gate framing was stale.
- **Fenced adjacent item (OUT OF SCOPE for this file)**: the LIVE operational concern is the separate prod claim-lane defect (tasks never claimed → FE empty) under its own parallel commission. Not analyzed here; no inference in this file bears on it.

---

## 11. Open Items & Follow-ups

| # | Item | Owner-state |
|---|---|---|
| A | `spawn_hot_instance` contract gap: task accepted-but-never-delivered + result teaches no follow-up. Fix direction: auto-enqueue task as child's first turn OR add follow-up instruction (parity with `spawn_instance` result, instance.py:2330). Predates v0.16.12; reachable at R15 OFF. | OPEN — needs a fix commission |
| B | Completion-lane reporting inconsistencies: lineage-enumeration "no parent, no children" vs set `parent_id`; COMPLETED-log-then-WAITING_CHILDREN→RUNNING flip with no publish line; PP1 zero-watcher fires. | OPEN — bounded follow-up audit owed |
| C | Artifact hygiene: staged `releases/v0.16.12/ensemble-prod` predates 1090f308 — release ceremonies must rebuild from latest or verify artifact content. (Independently: this audit proved the dev daemon ran worktree venv code, not the staged binary — corrigendum #2 — so artifact-content verification is not hypothetical.) | OPEN — release-ceremony change |
| D | Ops debts: rotation-broken external DB conns re-registration owed. The logless S4/S6 early-return ("Instance not found") remains theoretically unexcluded but with ZERO supporting evidence — and the decode (§12) now shows **zero send attempts at all** in the parents' checkpoints, closing the practical gap. | OPEN (conns); S4/S6 concern now decode-refuted in practice |
| — | Parent checkpoint decode | **EXECUTED this pass** (§12); no longer owed |

---

## 12. Optional Decode Results (EXECUTED — read-only formal closure)

Worker 85ce15b1, ~5 min elapsed (0.7s script runtime), zero mutations/writes/installs, zero secret exposure. Method: `daemon/checkpoint_adapter.py` inspected (lifecycle/pruning wrapper — no message-decode API) → sanctioned fallback: direct langgraph decode via `PostgresSaver.list(limit=1)` → `channel_values['messages']`; `setup()` never called; SELECT-only (with `statement_timeout=15s` via conninfo). Python: `/home/nea/ens-src-wt-snapshotfix/.venv/bin/python`. DB: `ensemble_dev` @127.0.0.1:5432, creds sourced in-process from boot.env with assert `POSTGRES_DB == 'ensemble_dev'`.

**Spec deviation (recorded)**: the stated boot.env path `/home/nea/dev-daemon-8079-srcfix/boot.env` does not exist; the real file used was `/home/nea/dev-daemon-8079-v0.16.11/boot.env` (names-only listing; values never printed).

### 12.1 Per-parent tool-call census (latest checkpoint, 11 checkpoints / 8 messages each)

| Parent | send/spawn tool_calls | "not found; no message dispatched" in results | First user message |
|---|---|---|---|
| a4f940b2-e68c-4d03-b592-3ab3837cfa59 | 1× `spawn_hot_instance` (call_01a1056183967381880f5a34; args: agent_id=worker, instance_name=snapshot-e2e-ready-check, snapshot_id=b7471da1-…, task="Reply with the single word READY and nothing else.") | **NONE** (every tool message scanned) | `[SYSTEM CONTEXT: Related Project]` snapshot-e2e-dev project JSON (identical across parents) |
| 715c9ef9-f0ee-429f-9eeb-a5f270f3218e | 1× `spawn_hot_instance` (call_0d1c5395fb344177859efc4e; no snapshot_id/instance_name — matches cold default-search) | **NONE** | identical |
| 655a7fbe-f506-4cb1-988e-fa9cc12e38fd | 1× `spawn_hot_instance` (call_01a1056a8daf76738b4d996b; task + tags=["kind:nonexistent-xyz-20261004"] — matches nonsense-tag cold) | **NONE** | identical |

**Zero `send_message` tool calls in any parent's history.** (Caveat: per-message timestamps are not serialized in checkpoint state; nearest proxy = each parent's latest checkpoint ts: 05:34:16 / 05:44:37 / 05:43:32.)

### 12.2 Row queries

- **instances row 715c9ef9** (PK is `instance_id`, 16 columns): `agent_id=coder`, `status=completed`, `parent_id=None`, created 05:19:48.308698Z, updated 05:44:37.806906Z, `paused_at=None`, metadata = title "Spawn Hot Instance Tool Call" + project_id + mcp_tool_names list (nothing credential-shaped).
- **Children (agent=worker, status=completed)** — each has **exactly 1 `message_queue` row + 1 task row**, nothing else references them:
  - 60392714: msg de44c2e7… enqueued 05:33:43.952003Z, task 1145 — `source=api`
  - ea2ae968: msg 4dd4f44f… enqueued 05:43:12.118678Z, task 1148 — `source=api`
  - c654e3b1: msg 8c5a5084… enqueued 05:43:45.498496Z, task 1150 — `source=api`
  - All: content `"Reply with the single word READY and nothing else."`, status=completed, `root_source=None`, `error_message=None`.

### 12.3 Decode verdict

Decoded evidence **confirms** the closed verdict's shape: parents issued `spawn_hot_instance` only (never `send_message`); the S4/S6 "Instance not found" absence-marker appears in **zero** tool results across all three parents; each child's only queue presence is its single manual `api`-source send. Nothing contradicts §1; formal closure delivered.

---

## 13. Sources & Attribution

| Evidence | Source | Nature |
|---|---|---|
| Final verdict synthesis | Leader instance bb100883 (reproduced verbatim by direction) | leader-synthesized from 3 investigator reports |
| Proof lines P1/P2/P3, prod taxonomy (386 rows, 55/55), diff-closure, caller-family audit, env-event ruling (11:13Z stop, 11:17Z rotation), artifact/binary-age claim | Commissions cfded28b / da8e4809 investigator reports | investigator-reported (P1 static git evidence; P3 secondhand DB census) |
| Affected-children inventory + 05:45:08Z enqueue census | Driver-quoted psql (via prod report_injections) | secondhand-but-contemporaneous (census delta recorded, §1.1 #6) |
| All verbatim log excerpts + line citations + absence grep counts + spawn-endpoint census (both files) | Worker c4d43e59-b2bc-4619-bfbe-cc3842156862 (2 dispatch rounds; read-only sed/grep/wc/readlink) | first-hand, this pass |
| `/proc/3392589` exe/cmdline/cwd strings | Worker c4d43e59 (read-only; environ NOT read — PB-F1) | first-hand, this pass |
| Checkpoint decode + instances/message_queue/task row queries | Worker 85ce15b1-8445-4952-8d2c-db1b3eae50e9 (read-only langgraph decode; SELECT-only; script /tmp/forensic_decode.py) | first-hand, this pass |
| Documentation, corrigendum, aggregation | Tester (this instance) | — |

**Files referenced**: `/home/nea/dev-daemon-8079-srcfix/logs/daemon-dev-8079-srcfix.stdout.log` (883 lines) · `/home/nea/dev-daemon-8079-v0.16.11/logs/daemon-dev-8079.stdout.log` · `/home/nea/dev-daemon-8079-v0.16.11/boot.env` (names only) · dev DB `ensemble_dev` (instances / message_queue / task / checkpoints).
