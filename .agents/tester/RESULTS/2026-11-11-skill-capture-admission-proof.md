# 2026-11-11 — skill-capture ADMISSION proof (scope D): TWO NAMED DAEMON DEFECTS — lane verdict STOP (fix required, out of test-infra scope)

Worktree: `/home/nea/ensemble-src-wt-skill-capture-wiring` @ `4fea841e7` (feature/skill-capture-prod-wiring, tree clean before and after this lane).
Staged artifact: v0.29.17 frozen binary, sha256 `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8` — the temp copy and the live release dir carry the SAME sha (byte-identical copy, verified pre-boot).
Isolated env: `/tmp/ens-e2e-CGZYkzmr` (fresh mktemp root), own PG 16 cluster on 127.0.0.1:18912 (db `ens_e2e_v02917`), own port 18790, mock LLM on 18791, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned, `ENSEMBLE_SELF_ENV=sandbox`, per-variable ambient scrub, FAKE_HOME escape guard PASS.

---

## 0. THE DISCRIMINATING ANSWER — why the prior run admitted nothing

**The CAPTURED admission never ran because the BM25-injected skill ids never reach the instance metadata on the normal injection path.** `SkillMetricsService.record_task_completion` early-returns at the `if not injected_ids: return 0` guard BEFORE the capture eligibility check, so every downstream gate is unreachable. This is a daemon-code defect (DEFECT-1), not a fixture problem. A second daemon-code defect (DEFECT-2) blocks the admission even after DEFECT-1 is fixed. Both are named below with exact sites, lines, and discriminating evidence.

### DEFECT-1 (blocking on the staged binary — proven twice)

**Site:** `daemon/services/instance_messaging.py`, inside `_process_message_with_tracking` → `_run_search_and_cache`, the branch pair at ~lines 4417–4485 (worktree @4fea841e7; identical bytes in the staged source).

```python
if injection_text:
    injection_service.track_injection(...)          # in-memory only
    setter = getattr(self._manager, "set_context_skill_result", None)
    if setter is not None:
        setter(instance_id, (injection_text, injected_skill_ids))   # in-memory cache only
else:
    setter = getattr(self._manager, "set_context_skill_result", None)
    if setter is not None:
        setter(instance_id, (None, list(injected_skill_ids)))
    # Persist injected skill IDs to instance metadata ...
    if injected_skill_ids:
        await asyncio.to_thread(_dedup_merge_skill_ids, ...)        # ← ONLY HERE
```

The `_dedup_merge_skill_ids` persist (the ONLY writer of `last_injected_skill_ids` for BM25-injected ids) sits inside the **`else` arm — the "search yielded nothing" branch**. On the normal path (skills actually injected, `injection_text` truthy) the ids go only to in-memory structures (`track_injection`, `set_context_skill_result`) and the metadata is never written. The enclosing `_run_search_and_cache` docstring lists step 4 as "``track_injection`` + dedup-persist" — the code does not do it. `context_messages.assemble_context_messages` deliberately performs NO metadata writes (module comment: "the orchestrator itself performs NO metadata writes … deferred to the messaging path"), and the auto-load merge at `instance_messaging.py:~5088` carries only auto-load ids (the worker's bank row has `auto_load=false`, so nothing). Result: **the key is never written in production flows.**

**Chain:** no `last_injected_skill_ids` → `SkillMetricsService._read_injected` → `[]` → `record_task_completion` early-return (`daemon/services/skill_metrics_service.py:466–471`, "if not injected_ids: return 0") → `_check_capture_eligibility` never invoked → no twin call, no `enqueue_capture`, no `skill_capture` job, no sink record, `admissions=0`, `admission_refusals={}` — **silently, with zero log lines.**

**Empirical proof on the STAGED BINARY (this lane's boot, 14:28–14:41):**
- job `7f55f8c1-8322-4d94-8ccf-0019b1812513` (`job_type=message`) → `admission_state=done`, `terminal_reason=completed`; instance `eafef7ab-b724-4caa-b9a0-dec0a7315be1` → `completed`; Observer finalize lines at 14:38:59 (log lines 458/460).
- `skill_usage_records` count = **0**; instances.metadata contains NO `last_injected_skill_ids` key (queried post-run; the early-return skips the metadata clear, so absence is the pre-hook state, not a clear).
- grep of the daemon log: `skill row not found` = 0, `SkillMetricsService` = 0, `_get_task_details` = 0 (the seam never reached its first statement).
- capture-lane JSON verbatim (14:41:04Z):
  `{"counters":{"admission_refusals":{},"execution_refusals":{},"admissions":0,"deviations":0,"fix_admissions":0},"source":"daemon.services.skill_capture_observability","exposition_neutral":true}`
- No sink dir under the isolated data dir ⇒ Gate 0 did not refuse ⇒ the kill-switch flag DID reach the process (`ENSEMBLE_SKILL_CAPTURE_ENABLED=1` exported in the boot script). The refusal counters being empty across both runs also proves Gates 1–5 were never reached.

### DEFECT-2 (blocks the admission after DEFECT-1 is fixed — proven in a DIAGNOSTICS-ONLY source boot)

**Site:** `daemon/services/task_processor.py`, `_compute_iterations_and_duration`, the message filter at ~line 1010 (pre-edit numbering):

```python
if parsed >= task_created_at:
    filtered.append(message)
```

**Mechanism:** the `Task` row is created ~0.7 ms AFTER the triggering message row is enqueued, so the strict comparison drops the task's OWN message. `task_message` extracts to `""`, and the twin gate `SkillEvolutionService.check_and_capture` (`daemon/services/skill_evolution_service.py:1688` — "Blank task message → nothing to distill") returns `None` silently.

**Arithmetic from this lane's run (job `2980effe-356a-4f42-a167-bc00d58482fe`, instance `f94c1c4b-cdc7-44e9-9dc4-5c6447edb51b`):**

| row | timestamp (UTC) |
|---|---|
| `message_queue` human row `ba5065a8…` enqueued_at | 2026-11-11 14:50:08.988008 |
| `task` row id=2 created_at | 2026-11-11 14:50:08.988731 |
| delta | **message enqueued 0.723 ms BEFORE the task row** |
| `task.completed_at − task.created_at` | 160 s — **exactly the recorded `duration_seconds=160`** |

The TP path fired (its duration math matches the recorded 160 s exactly), its filter dropped the trigger message, and the usage row's `task_message` column is `''` — the direct fingerprint of the blank-input refusal. The JQ path (`JobQueueService._get_task_details`) computes the message NON-blank (verified in-process against the same DB: `task_message='Run the e2e capture test task. Please complete it.'`, iterations=0, duration≈160) — but the JQ metrics hook never runs because the job was already finalized (`finalize_active_to_done` → `finalized is None` → early return before `_record_task_metrics_sync`).

**Twin verified in-process against the live run DB** (real repos, real config `capture_min_iterations=5`, `capture_min_duration_seconds=60`, `has_genuinely_evolved_for_instance=False`):

| iterations | duration | task_message | twin verdict |
|---|---|---|---|
| 0 | 160 | 'Run the e2e capture test task. Please complete it.' | **ADMIT** |
| 0 | 160 | '' | REFUSE (blank-message gate) |
| 0 | 20 | 'msg' | REFUSE (triviality gate) |
| 6 | 0 | 'msg' | ADMIT |

So after DEFECT-1 is fixed, the ONLY remaining refusal input on the production path is the blank `task_message` from DEFECT-2.

---

## 1. Named STOP condition (per lane scope)

A daemon-code change is genuinely required for the capture admission to fire; this lane is TEST-INFRA ONLY. **No daemon/ or tests/ change was committed.** All four touched files were reverted byte-exact (sha256 == HEAD for each; `git status --porcelain` empty):

```
daemon/services/instance_messaging.py            f94ea0d991c091be… == HEAD
daemon/services/task_processor.py                a8350a6159a6f9f2… == HEAD
tests/services/test_instance_messaging_skill_injection.py 3189e778d8bdc6f9… == HEAD
tests/services/test_process_message_metrics.py            4d3f32ac015712a8… == HEAD
```

The temporary changes (diagnostic + the proposed fixes + mutation-proven regression tests) are preserved as disclosure artifacts at `/tmp/ens-e2e-evidence/v2/disclosure/`:
- `defect1-instance_messaging.persist-in-wrong-branch.diff` (59 lines) — the proposed fix: hoist the `_dedup_merge_skill_ids` persist out of the `else` arm so it runs whenever `injected_skill_ids` is non-empty (matching the routine's own docstring step 4).
- `defect2-task_processor.enqueue-race.diff` (26 lines) — the proposed fix: always include the message whose `message_id == task.message_id` in the filtered set (`parsed >= task_created_at OR message_id match`), preserving the junk-skill guard for prior tasks' rows.
- `regression-tests.diff` (157 lines) — two mutation-proven tests:
  - `TestInjectedIdPersistence::test_bm25_ids_persisted_when_skills_are_injected` — asserts `set_metadata(inst-1, last_injected_skill_ids, [skill-A, skill-B])` on the normal injection path. **Counter-mutation proof: with the exact pre-fix source the test FAILS (1 failed/25 passed); with the fix it passes (26 passed).**
  - `TestRecordMetricsWiring::test_trigger_message_survives_submillisecond_enqueue_race` — drives the real processor with a queue row enqueued 1 ms before `task.created_at`; asserts `record_task_completion` receives the NON-blank trigger message. (Note: the surrounding `TestRecordMetricsWiring` class has 10 PRE-EXISTING failures on this worktree's HEAD — the harness's task stub lacks `priority` read at `task_processor.py:577` — unrelated to this lane; baseline re-verified via `git stash`.)

**Required change (for the leader's verdict):** land the two diffs in `daemon/` via an implementation lane, re-stage, and re-run this evidence on the new staged binary. Until then the scope-D ADMISSION leg on the frozen artifact is a NAMED GAP, not a pass.

---

## 2. What WAS proven on the STAGED BINARY (this lane)

### 2.1 Fence (all green)
- Pre-flight port check (boot aborts if 18790 is already bound) — the prior session's 08:43/08:45 double-boot shape is NOT reproduced; exactly ONE daemon tree served each boot.
- Temp-root path derivation: frozen binary at `<tmproot>/releases/v0.29.17/ensemble-prod` derives `install_dir == tmproot` (≠ LIVE, ≠ DEMO, not under /home/nea) — `fence_proof.txt`.
- FAKE_HOME escape guard: `/tmp/ens-e2e-CGZYkzmr/fakehome` — problems: NONE.
- sha256 of the booted binary: `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8` (== live release dir == the handoff's 4-way pin).
- Boot log wiring canaries (verbatim, boot at 14:28:22):
  - `14:28:49 - daemon.manager - INFO - SkillJobDispatcher wired to manager (evolution_lane_resolved=True [env-resolved-or-default], evolution_lane_config_default=False, fix_cooldown_seconds=1800, fix_burst_cap=3, …)`
  - `14:28:49 - daemon.api - INFO - Skills router wired: store=True search=True metrics=True evolution=True triggers=True dispatcher=True usage_repo=True capture_dispatcher=True`
  → the Phase-3 api-seam metrics↔dispatcher binding IS live on the frozen binary (`capture_dispatcher=True` is computed from the metrics service's own `_skill_job_dispatcher`).

### 2.2 Fixture fixes (§5 of the commission) — verified
- **Mock per-request sleep (§5.1):** the v2 mock sleeps 80 s on EVERY non-selector chat call → hook-time `duration_seconds=160` (≫ the 60 s floor), deterministic, not one-shot.
- **Real injected-id rows (§5.2):** both candidate skills pre-created via `POST /api/skills` (`e2e-capture-test-skill` id `620d4d26…` and `injection-screen` id `2868bf4f…`, project `71931ae0…`) plus the global-scope pair — the clone-on-miss path no longer mints ids at injection time; the prior run's `skill row not found` warning did not recur (grep = 0).

### 2.3 Trigger leg — lands, then dies at the DEFECT-1 early-return
- Task `Run the e2e capture test task. Please complete it.` via `POST /api/instances/{id}/messages` → job done/completed, instance completed (14:33:43 → 14:38:59).
- The metrics seam did NOT run (no usage row, no warning) because the injected ids were never persisted (DEFECT-1). Admission 0. Evidence verbatim in §0.

---

## 3. Diagnostics-only source boot (LABELED — NOT the scope-D proof)

A `python -m daemon` boot of the fixed worktree (same isolated PG/data dir/port, same fences) was used as a DIAGNOSTIC INSTRUMENT ONLY, with the staged artifact untouched (sha re-verified unchanged after the lane). Findings from it:
- With DEFECT-1 patched, the production seam RUNS on a succeeded task: `skill_usage_records` gains a row `2e44e987…` — `selected=t, task_succeeded=t, iterations=0, duration_seconds=160, feedback_applied=NULL, task_message=''` — and the injected-id metadata is written and then cleared by the hook (the intended lifecycle). This confirms DEFECT-1 is the exact early-return site: fixing it moves the seam from "never runs" to "runs".
- Admission STILL 0 → isolated the remaining refusal to DEFECT-2 (§0 arithmetic + twin table). This source-boot evidence is labeled diagnostic; the frozen-binary admission leg remains a NAMED GAP (§1).

---

## 4. Prior-run anomaly (follow-up, not load-bearing for this verdict)

`fdd0f92c-ef1c-4592-9ab0-675026aaad01` — the id recorded in the PRIOR run's `instances.instance_metadata.last_injected_skill_ids` — exists in NO table: `skills` (2 rows: `0f9640d2…` e2e-capture-test-skill, `baee9e7c…` injection-screen clone), `skill_bank` (69 rows), `skill_usage_records`, `skill_lineage`, `skill_embeddings`, `skill_triggers`, `project_skill_files` — all scanned (row_to_json + per-table count sweep); the PG heap (`base/16384/16410`) and WAL (`pg_wal/000000010000000000000001`, 7 hits, all instances-metadata context) contain it ONLY inside instance metadata. A uuid minted in memory and persisted to instance metadata with no backing row. Recommend a follow-up defect lane if a post-fix run reproduces a phantom (it would then be observable with the DEFECT-1 fix in place, since the seam will actually read real ids).

---

## 5. Ground truth (final DB state, staged-binary run)

```
job_queue_items: 7f55f8c1-8322-4d94-8ccf-0019b1812513 | message | done | completed   (created 14:33:43Z)
instances:       eafef7ab-b724-4caa-b9a0-dec0a7315be1 | completed | metadata has NO last_injected_skill_ids
skills:          5d4855d5(global)+bc03f117(global)+620d4d26(project)+2868bf4f(project) — 4 rows
skill_usage_records: EMPTY        skill_capture jobs: NONE        sink dir: NOT CREATED
capture-lane:    {"counters":{"admission_refusals":{},"execution_refusals":{},"admissions":0,"deviations":0,"fix_admissions":0}, …}
```

Prior-run DB (read-only restart, then STOPPED again): 2 message jobs done/completed, 2 human queue rows only (iterations=0), usage table empty, the phantom id in metadata only — all consistent with DEFECT-1 (the prior run's second task reached the seam via the phantom id and warned `skill row not found`, i.e. the twin never ran because `_record_one` short-circuited on a missing row AND the capture gate still refused downstream — same terminal state, no admission).

---

## 6. Live baselines — live install untouched

- live-before: prior session's `/tmp/ens-e2e-evidence/live-before/snapshot.txt`.
- live-after (this lane, 15:03Z): `/tmp/ens-e2e-evidence/live-after-v2/snapshot.txt` — `current → releases/v0.29.15`; `livez {"status":"alive","uptime_seconds":11328.4,…,"version":"0.29.15"}`; `state.json` sha `302d2511c5654e00d3fa27fd3f5c9cb629c3515c6ce4dd3dd82a59f227ff0eca`; systemd `MainPID=3612228 NRestarts=1 ActiveState=active` (NRestarts=1 is the morning bind-race restart, pre-existing).

## 7. Hygiene

- Ambient env scrubbed per-variable before every boot/capture (`env -u POSTGRES_*` never used). No live credential ever captured to evidence (F7 rule); the bearer token lived only in `/tmp/ens-e2e-evidence/v2/.token_secret` (mode 600) and appears in NO evidence file.
- Teardown: isolated ports 18790/18791/18912 all free; no stray processes; isolated PG STOPPED; prior-run PG STOPPED (was restarted read-only for forensics, then stopped).
- The diagnostics source-boot process tree was killed before the RESULTS commit; no `.venv` daemon is running.

---

# GAP CLOSURE — commission §1–§4 (second commit, test-infra only)

## 8. 4-way sha recompute (commission §1) — ALL FOUR verified

| # | Source | Value | Location |
|---|---|---|---|
| (i) | `sha256sum /home/nea/agents-ensemble/releases/v0.29.17/ensemble-prod` | `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8` | live release dir (read-only) |
| (ii) | `sha256sum /home/nea/ensemble-src-wt-stage-v02917/dist/ensemble-prod` | `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8` | stage-worktree dist (read-only) |
| (iii) | `manifest.json` field `.binary_sha256` | `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8` | `/home/nea/agents-ensemble/releases/v0.29.17/manifest.json` |
| (iv) | `ensemble-prod.build-provenance.json` field `.artifact_sha256` | `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8` | `/home/nea/ensemble-src-wt-stage-v02917/dist/ensemble-prod.build-provenance.json` (the release dir ships WITHOUT a `dist/` subdir — the provenance sidecar lives in the stage worktree's dist, recorded here verbatim) |

All four equal the dispatcher-pinned `b5b54b01d9424410c35a83cde9593d0c0b97dd234ae7dfa95e2cd6b6b68243d8`. The temp copy booted in this lane (`/tmp/ens-e2e-CGZYkzmr/releases/v0.29.17/ensemble-prod`) is byte-identical to (i) by `sha256sum`. Full output: `assets/…/evidence/sha_4way.txt`.

---

## 9. Live capture-lane counter baselines (commission §2) — READ-ONLY

### 9.1 Live capture-lane door
- `GET http://127.0.0.1:9797/api/metrics/capture-lane` with the isolated-lane bearer token (the only token this lane held, at `/tmp/ens-e2e-evidence/v2/.token_secret`, mode 600 — it is NOT in the live auth DB):
  - **HTTP 401** `{"error":"AUTH_REQUIRED","message":"Authentication required"}`.
  - Unauthenticated reference read: HTTP 302 (redirect to login).
  - **Gap (named):** this lane holds NO valid live bearer token; the door is closed to it. The live counter body was NOT captured. The most recent available live reading is the prior session's, recorded in `/tmp/ens-e2e-evidence/live-before/snapshot.txt` (17:55Z, 2026-11-11): `{"counters":{"admission_refusals":{},"execution_refusals":{},"admissions":0,"deviations":0,"fix_admissions":0},…,"exposition_neutral":true}`. Auth was NOT changed (out of scope per the commission). Reading the live door requires a valid live bearer token (operator / project-manager lane).
- Live sink rows (read-only, `wc -l` + grep, never rm/mv):
  - `skill-capture-20261110.jsonl`: **11 lines**, `outcome=admitted` **0**, `capture_lane_off` **11**.
  - `skill-capture-20261111.jsonl`: **2 lines**, `outcome=admitted` **0**, `capture_lane_off` **0**.
- Live `.env` flag lines 133/134 (verbatim): `ENSEMBLE_SKILL_CAPTURE_ENABLED=1` / `ENSEMBLE_SKILL_EVOLUTION_ENABLED=1` (both ON in live).
- **A4 gauge** (`scripts/audit/skill_counter_reconcile.py --measure`, dry-run, read-only, against the live DB): `drift=503`, `mismatched_skills=50`, `diverging_keys=34` (baseline 32), `skills_total=668`, `taken_at=2026-11-11 15:13:05`. Baseline for the C7 gate = 34 (record this as the window-start value, not the runbook's 32).

### 9.2 Fresh live fence snapshot (after the reads)
- `/tmp/ens-e2e-evidence/live-after-v3/snapshot.txt` taken 15:03Z. Drift vs `live-after-v2` (14:05Z): `current → releases/v0.29.15` unchanged, `livez 0.29.15` alive, uptime 11328→11905 s (≈577 s elapsed — continuous, expected), `MainPID=3612228`, `NRestarts=1` (unchanged). No drift. Live install untouched by this lane.

---

## 10. Post-promote observation plan (commission §3)

Scope: a v0.29.17 promote (the staged artifact, sha pinned above) in the live environment, with the capture lane **already ON** (live `.env` 133/134 = 1). Window: 24 h (the plan's stated Phase-2 observation window, `phase1-plan.md` §15 step 2 / §8 D4; sink retention ≥ 30 days covers it). Instruments per C-row are §6.1–6.7 of `phase2-runbook.md` (routes + auth: `maintenance:read` OR `metrics:read`).

| # | Condition (must ALL hold) | Threshold | Instrument (§6.x) |
|---|---|---|---|
| **C1** | The capture lane **actually admitted work** | `counters.admissions` increases by ≥ 1 over the window | §6.1 `/api/metrics/capture-lane` |
| **C2** | The capture switch **never flapped off** | `counters.admission_refusals["capture_lane_off"]` does not increase | §6.1 |
| **C3** | No keeper deviation | `counters.deviations` unchanged | §6.1 |
| **C4** | No Tier-3 mutation happened (evolution lane still OFF) | `counters.fix_admissions` unchanged | §6.1 |
| **C5** | No queued evolution row refused at execution | `counters.execution_refusals["lane_disabled"]` unchanged | §6.3 |
| **C6** | No burst-cap trip | `counters.admission_refusals["fix_burst_cap"]` unchanged | §6.6 |
| **C7** | A4 within-identity-key divergence did not grow | `diverging_keys` ≤ window-start value (baseline 34) | `scripts/audit/skill_counter_reconcile.py --measure` (§6.5) |
| **C8** | A4 counter drift did not grow | `drift` ≤ value at window start (baseline 503) | same |
| **C9** | The sink recorded admitted runs | ≥ 1 row with `"outcome":"admitted"` | `<data_dir>/skill-capture-observability/skill-capture-<YYYYMMDD>.jsonl` (§6.7) |
| **C10** | No new lane anomaly row | 0 new rows with `category` in (`tool-failure`, `queue-scheduling`) **AND `instance_id = 'daemon:skill-capture-observability'`** | `anomaly_reports` (§6.4, lane-scoped) |

**C1's depth:** C1 requires a real CAPTURED `skills` row (`lineage_origin='captured'`), not just a `skill_capture` job row — a job row alone proves the admission, not the distillation. The first live CAPTURED row is the Phase-2 proof (`phase1-plan.md` §15 step 8, §9 E2). Note the capture→evolution diversity gap remains UNEXERCISED by design (CAPTURED-origin `evolve_skill` raises `ValueError` — pinned, protected).

**Process-local counters (§6.5 caveat):** C2–C6 counters reset on daemon restart — take the reading immediately AFTER the restart as the window baseline, and compare every later reading against that baseline, never against zero.

### THE LOAD-BEARING AMENDMENT (forced by DEFECT-1)

**Post-promote of v0.29.17 AS-IS, C1 (admissions ≥ +1) CANNOT MOVE.** DEFECT-1 blocks every BM25-injection capture candidate at the `if not injected_ids: return 0` early-return in `SkillMetricsService.record_task_completion` — the `last_injected_skill_ids` metadata key is never written on the normal injection path, so the CAPTURED eligibility check never runs, and no amount of eligible traffic post-promote will change `admissions` from 0. **The operator watching C1 after the ceremony would otherwise misread a flat 0 as "lane healthy, no eligible tasks."** That reading is WRONG — a flat 0 post-promote proves only that DEFECT-1 is still present, not that no eligible tasks occurred.

**Effective health signals until DEFECT-1 lands:** C2 (capture switch never flapped off), C5 (no execution refusal), C6 (no burst-cap trip), plus C7/C8 (A4 drift did not grow) and C9 (sink recorded admitted runs — will be 0 until the fix lands, which itself is a DEFECT-1 signal). C1 and C9 are expected to stay flat and must NOT be read as regressions. The admission leg is a **NAMED GAP** until the implementation lane lands the two diffs in §1 and re-stages.

---

## 11. Durable evidence assets (commission §4)

Preserved under `.agents/tester/RESULTS/assets/2026-11-11-skill-capture-admission-proof/` (108 KB; NO binary, NO pgdata, NO token file):

| Subdir | Contents |
|---|---|
| `scripts/` | `boot_v2.sh`, `setup_pg.sh`, `mock_llm.py`, `live_snapshot.sh` (the v2 fixture method) |
| `evidence/` | `fence_proof.txt`, `boot_log_excerpt.txt`, `evidence_v2.txt` (capture-lane JSON + completion facts), `ground_truth_db.txt`, `sha_4way.txt` |
| `live/` | `live-before.txt`, `live-after-v2.txt`, `live-after-v3.txt` |
| `disclosure/` | `defect1-instance_messaging.persist-in-wrong-branch.diff`, `defect2-task_processor.enqueue-race.diff`, `regression-tests.diff` (the leader's decision material) |

The disclosure diffs are the implementation lane's starting point; the asset paths are referenced above in §1, §5, §8, §9.

---

## 12. Pre-existing red (unrelated to this lane)

`TestRecordMetricsWiring` (10 failures) on this worktree's HEAD — the harness's task stub lacks `priority`, which `task_processor.py:577` reads in the claim path (`is_fresh_episode_user_message = message.priority == 1 and message.type == MessageType.HUMAN.value`). Re-verified as pre-existing via `git stash` (10 failed / 6 passed on the untouched tree). Recommend a small test-infra follow-up; not in this lane's blast radius.
