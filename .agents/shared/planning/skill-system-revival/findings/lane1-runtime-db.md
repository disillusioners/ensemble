# Lane 1 — Runtime / Database Ground Truth
## Phase 0 read-only investigation — agents-ensemble skill system

**Lane:** Lane 1 (runtime/DB ground truth)
**Commission:** Skill system revival — re-enable skill capture, fix flakiness, stabilize metrics (board `wi-4d1fa093`)
**Investigation date:** 2026-11-09 (queries run 05:13Z–05:20Z UTC)
**Mode:** READ-ONLY. Zero writes to any DB (SELECT / information_schema only). Zero job/instance dispatch, pause, resume, restart. Zero source/config/.env edits. **Sole file created:** this findings file.

---

## 0. Anchors, environment, and version provenance (READ FIRST)

| Anchor | Value | How established |
|---|---|---|
| Live DB queried | `ensemble_prod` @ `10.44.0.2:5432` (user `ensemble`) | `select current_database()\|\|current_user` → `ensemble_prod \| ensemble \| 2026-11-09 05:13:42.060036+00` (PG 18.4, server reported as `x86_64-pc-linux-gnu`) |
| Dev DB (repo checkout) | `ensemble_dev` @ `127.0.0.1:5432` (user `ensemble_dev_app`) | `ensemble_dev \| ensemble_dev_app \| 2026-11-09 05:13:42.251811+00` |
| Demo DB | `ensemble_demo` @ `10.44.0.2:5432` | reachable, queried for queue-state parity |
| **LIVE daemon release** | **`v0.29.7`** | `readlink -f /home/nea/agents-ensemble/current` → `/home/nea/agents-ensemble/releases/v0.29.7`; `releases/state.json` → `current = v0.29.7`, `previous = v0.29.6` |
| Live daemon PID | `513846` (launcher `513572`, started 04:33 today; parent = PID 1, **not** a systemd user unit) | `ps aux`; `systemctl --user list-units` → empty |
| Demo daemon PID | `2133` (launcher `1016`) | `ps aux` |
| Repo checkout HEAD | `013895287` (2026-10-26) — DETACHED, ~2 weeks behind | per orchestrator steer (Lane 2, verified) |
| `origin/latest` | `5da1021f2` = the **v0.29.7 tree** → i.e. `origin/latest` ≈ LIVE code | per orchestrator steer + release-dir equality (v0.29.7) |
| Code-citation label | **[HEAD]** = `013895287` (workdir) · **[origin/latest]** = `5da1021f2` (≈ live v0.29.7) | used throughout below |

**Read-only discipline observed:** no `git checkout/branch/reset/stash/clean` was run. `origin/latest` content was read with `git show origin/latest:<path>` only. Worktree left exactly as found.

---

## (a) HEADLINE ANSWERS

**A1 — Did capture runs still happen? No. The capture lane has produced nothing since 2026-09-01.**
`skill_capture` job rows: **25 total, created 2026-08-24T19:08:00.604951+00:00 → 2026-09-01T21:45:19.915036+00:00**, then nothing.
Query: `select count(*) … where job_type='skill_capture' and created_at >= '2026-09-17'` → **0**.
→ **The claim "zero skill_capture jobs since 2026-09-17" is TRUE but materially understated: the true cut-off is 2026-09-01, sixteen days earlier.**
Only capture products that exist in the registry: **6 skills with `lineage_origin='captured'`**, all created 2026-08-24→2026-08-25, **all `status=inactive`, `is_active=false`, `last_used_at` NULL (never used)**.

**A2 — The "ten burst runs" claim: the FIX-lane part is TRUE (100%); the "ten" figure needs care.**
Every one of the **116** `skill_evolution` job rows carries `metadata->>'evolution_type' = 'FIX'` — **116/116, no exceptions** (span 2026-08-24 → 2026-10-22). The 2026-10-19→2026-10-22 burst is **110 jobs** (10-19: 8, 10-20: 37, 10-21: 58, 10-22: 7), all FIX. The code comment's *specific* "10-job burst (… same skill re-FIXed 3× in 38 min)" **[origin/latest] `daemon/services/skill_job_dispatcher.py:180-183`** is independently reproduced in the DB: skill `20f916b3-34e6-4f96-b775-8e4ecc41a38c` was re-FIXed **3× within 37.1 min** (2026-10-21T21:26:09.209063 → 21:49:02.445258 → 22:03:13.269359), inside a burst of 10 jobs (9 strictly inside the span, 10 including the boundary job).
→ **"The ten burst runs were all skill_evolution FIX-lane jobs" is VERIFIED.** Caveat for the writer: the *total* FIX population is 116 jobs / 56 distinct skills in the burst, not 10 — "ten" describes the single 38-minute incident window, not the whole burst.

**A3 — The lane is OFF at the process level, by ABSENCE, on all three switch surfaces.**
`ENSEMBLE_SKILL_CAPTURE_ENABLED`, `ENSEMBLE_SKILL_EVOLUTION_ENABLED`, and the pydantic alias `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` are **ABSENT** from: the repo `.env`, the live install `.env`, the live running process env (`/proc/513846/environ`), and the demo process env (`/proc/2133/environ`).
Unset ⇒ default: capture **OFF** (default OFF), evolution **OFF** (`evolution_lane_enabled` default `False`). Live `config.yaml` ships a `skill_evolution:` block (line 447) that does **NOT** carry `evolution_lane_enabled` → the `False` default governs. **The governing surface is the config default, reached because no env var is set — not an explicit "off" flag.**

**A4 — No paused queue is holding the lane off. There is no live-acting brake left.**
`job_queues`: **0 of 132 rows have `is_paused = true`** in `ensemble_prod`. All 26 `system_parallel_queue` rows are `is_paused=false`. The specific queue cited in project notes (`bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2`, "system_parallel_queue", noted as paused since 19:54:26Z on 2026-10-22) is now **`is_paused=false`, `updated_at=2026-10-26T21:50:04.439103+00:00`** — the pause was **lifted on 2026-10-26**. Demo (1 row) and dev (2 rows) `system_parallel_queue` rows are also `is_paused=false`.
→ **The lane is held off by the code-level kill-switch default only. There is no queue-level brake, and no database-resident "off" switch.**

**A5 — A refused admission creates NO row. The "off" is a silent no-op, invisible in the DB.**
`enqueue_evolution` returns `None` **"WITHOUT touching the job service"** when the kill-switch is OFF **[origin/latest] `daemon/services/skill_job_dispatcher.py:559, 579`**; `enqueue_capture` is the same no-op contract **[origin/latest] `:150-173`**. Therefore there is **no** `terminal_reason='lane_disabled'` row anywhere to find, and no `skill_analysis` / `skill_metric_scan` job rows either (**0 and 0** — explicit counts, despite those two lanes being ungated per `skill_job_dispatcher.py:194-201`).

**A6 — Trigger CONDITIONS are satisfiable today, yet nothing fires.**
Simulating every count-based trigger against today's usage data: **41 skills** would trip `low_completion_rate` (<0.3, ≥5 selections), **63 skills** have `total_selections>=20` (`task_count_scan`, threshold 20), **1 skill** has `consecutive_failures>=3`, **0** trip `high_fallback_rate`, **0** trip `low_usefulness`. → the trigger engine has every reason to fire; the evolution chokepoint refuses admission, so no job row is ever created. The "silence" is by design, not by the trigger failing.

**A7 — No DB-resident agent-level "off" switch exists.**
`custom_agent_configs` = **0 rows** (prod). `prompt_versions` rows for `agent_id='skill-keeper'` = **0 rows**. `app_settings` holds only three `limits.*` keys. `skill_triggers` = 13 rows, **all `is_enabled = true`** (no disabled trigger). `agents/skill-keeper/meta.json` carries **no** capture/evolution flag — it carries only tool allow/deny. **The only "off" state at the data layer is a pinned critical note** (`ed6da139`) plus code defaults.

**A8 — Skill-keeper's per-instance execution record is NOT retrievable from any DB.**
`instances` contains **zero** rows with `agent_id='skill-keeper'` (24 distinct agent_ids present, none is skill-keeper). **0 of the 141** distinct `instance_id`s referenced by skill job rows exist in `instances` — and the *source* instance ids in `metadata.task_details.instance_id` are **also absent** (0 matches). The `task` table's 7 skill-mentioning rows are all from **today** (sibling lanes of this commission). **The durable skill history is the `job_queue_items` row set only.**

**A9 — The Oct-2026 incident record lives on the BOARD, not in the anomaly store.**
`anomaly_reports` = 38 rows spanning **2026-11-06 → 2026-11-09 only** → it structurally cannot cover the Aug/Sep capture era. `wedge_captures` = 6 rows, all host-level `sustained_loop_lag`/`sustained_cpu`, **zero skill-keeper captures**. The definitive incident record is board item **`wi-7382d739`** ("CRITICAL: skill-keeper making unwanted project file/script changes — contained (instance paused)") with notes 83–89 — including the FINAL REPORT and CLOSING entries.

**A10 — The "weird metrics" are real and quantified.**
> ⚠️ **[CORRECTED — see "NUMERIC RECONCILIATION APPENDIX" R1.a/R1.b] The applied-row figure in this bullet is a TRANSCRIPTION ERROR: the query returned `6,057`, not `5,057`. The drift `+479` was always correct (6,536 − 6,057); `6,536 − 5,057 = 1,479` is arithmetic on the typo. Reconciled live value @2026-11-09T06:14:01Z: Σ=6,547, applied rows=6,067, drift=+480. Use 6,057 (or 6,067) — never 5,057 — and never 1,479.**

`skills.total_applied` sums to **6,536** while the actual applied usage rows number **5,057** (**+479 counter drift**); `total_selections` sums to **10,055** vs **10,059** actual selected rows (−4). **48 of 628 skills** have a `total_applied` that disagrees with their own usage rows (the 2026-11-04 tester census recorded 43/529 — the drift is growing). **264 of 628 skills have zero usage records**. `skills` holds **628 rows / 232 distinct names**. `feedback_applied` is NULL whenever `applied` is false (0 rows with `feedback_applied=true` and a NULL note; 0 rows with `selected=false AND applied=true`; 0 duplicate `(skill_id,instance_id,created_at)` triples) — i.e. the feedback lane is *coupled to application*, not independently sampled.

**A11 — The dirty worktree cannot change the DB schema picture and does not touch the capture path.**
Untracked files: **152 individual files** (74 collapsed porcelain entries), of which **0** under `daemon/`, **0** `.sql` files anywhere, **0** under `scripts/`. All are `.agents/**` planning/tracking docs (96 `.agents/shared`, 18 `.agents/tester`, 15 `.agents/approver`) plus root-level `tip.*`/`preexisting.txt`/`net-new.txt` junk. Tracked modifications include 4 skill source files — but **no diff line in any dirty file mentions capture** (`git diff | grep -i captur` → empty).

**A12 — All skill history is LIVE-only; dev and demo are empty of it.**
Censused in parallel: `ensemble_prod` has 628 skills / 10,056 usage rows / **141 skill jobs**; `ensemble_demo` has 1 skill / 4 usage rows / **0 skill jobs**; `ensemble_dev` has **0 skill jobs**. → The skill system's entire history lives in the production DB — which is also the DB the "re-enable" decision would land on.

**A13 — Methodological warning for the downstream writer: the live daemon log is contaminated by this investigation's own prompts.**
Naive greps of `/home/nea/agents-ensemble/data/logs/ensemble.log` for `lane_disabled` / `skill_metric_scan` / `skill` return hits that are **sibling lanes' prompt text** inside `openai._base_client` DEBUG LLM-payload dumps and `reasoning_content` echoes — not daemon behaviour. After excluding those carriers, the only skill-bearing lines in the current rotation are **my own tool calls**. Any lane drawing runtime conclusions from that log MUST filter both carriers first.

---

## (b) RAW EVIDENCE

### B1 — Schema + state inventory (live `ensemble_prod`)

Row counts (all via `select count(*) from public.<t>`):

| Table | Rows | Oldest | Newest | What it records |
|---|---:|---|---|---|
| `skills` | 628 | 2026-07-21T07:00:21.026452+00:00 | 2026-11-09T04:35:53.244043+00:00 (`updated_at` 04:56:38.168449) | Skill registry: name/desc/content/category/`is_active`/`status`/`lineage_origin`/`generation`/`ab_test_group`/`auto_load`/`source_skill_bank_id` + counters `total_selections`/`total_applied`/`total_completions`/`total_fallbacks`/`consecutive_failures` |
| `skill_usage_records` | 10056 | 2026-07-21T08:33:04.980660+00:00 | 2026-11-09T05:09:23.454463+00:00 | Per-use row: `skill_id`,`project_id`,`instance_id`,`agent_id`,`task_message`,`selected`,`applied`,`task_succeeded`,`iterations`,`duration_seconds`,`fallback`,`feedback_applied`,`feedback_note`,`ab_test_group`,`superseded`,`feedback_usefulness`,`feedback_improvement` |
| `skill_triggers` | 13 | 2026-07-11T20:02:59.658625+00:00 | 2026-10-07T13:22:42.022644+00:00 | Trigger rules: `id`,`project_id`,`name`,`condition_type`,`condition_json`,`action`,`is_enabled`,`created_at` — **NO `last_fired_at`, NO `next_fire_at` columns** |
| `skill_lineage` | 60 | 2026-08-24T21:29:42.913286+00:00 | 2026-10-22T19:37:32.050591+00:00 | `skill_id`,`parent_skill_id`,`change_summary`,`content_diff`,`created_at` |
| `skill_ab_tests` | 59 | 2026-08-24T21:29:42.870548+00:00 | 2026-10-22T19:37:32.031051+00:00 | `ab_test_group`,`skill_id_old`,`skill_id_new`,`extension_count`,`comparisons`,`created_at`,`resolved_at` (32 resolved),`winner_skill_id` |
| `skill_embeddings` | 1751 | 2026-08-23T14:17:05.155118+00:00 | 2026-11-09T04:36:08.875982+00:00 | Vector index for skill retrieval |
| `skill_bank` | 129 | — | — | Separate template store (isolated from evolution lineage) |
| `project_skill_embeddings` | **0** | — | — | Project-file skill index — never exercised |
| `project_skill_files` | **0** | — | — | Project-file skill registry — never exercised |
| `project_skill_usage_records` | **0** | — | — | Project-scope usage — never exercised |
| `wedge_captures` | 6 | 2026-11-02T20:56:42.448789+00:00 | 2026-11-08T19:17:16.623377+00:00 | Host-level wedge captures: `capture_id`,`capture_dir`,`trigger_reason`,`trigger_source`,`captured_at`,`boot_epoch`,`host`,`release_version`,`summary`,`auto_continue_mode`,`file_count`,`total_bytes`,`metrics_snapshot` |
| `anomaly_reports` | 38 | 2026-11-06T04:27:10.988501+00:00 | 2026-11-09T05:01:35.288414+00:00 | Agent-reported anomalies (+`cluster_id`,`outcome`,`refusal_reason`,`wedge_capture_ids`,`job_id`,`agent_id`,`instance_id`) |
| `anomaly_clusters` | 36 | — | — | `cluster_key_hash`,`category`,`first_report_at`,`last_report_at`,`report_count`,`max_severity`,`board_item_id` |
| `job_queue_items` | 5010 | 2026-08-24T18:41:53.952127+00:00 | 2026-11-09T05:10:07.482246+00:00 | Job rows: `job_id`,`agent_id`,`job_type`,`admission_state`,`created_at`,`failed_at`,`terminal_reason`,`retry_count`,`metadata` (jsonb),`instance_id`,`queue_id`,`idempotency_key` |
| `job_queues` | 132 | — | — | `queue_id`,`queue_name`,`queue_type`,`concurrency_limit`,`is_system`,**`is_paused`**,`default_max_retries`,`created_at`,`updated_at` |
| `instances` | 4491 | 2026-08-24T19:11:03.475091+00:00 | 2026-11-09T05:11:32.433187+00:00 | `instance_id`,`agent_id`,`status`,`parent_id`,`metadata`,`paused_at` — **no skill-keeper rows** |
| `task` | 60 | — | all 2026-11-09 | Execution tasks (`work_id`,`task_type`,`status`,`result`,`error`,`created_at`…) |
| `message_queue` | 53 | — | 2026-11-09 | Message rows; 7 mention skill-keeper — **all from today (this commission's sibling lanes)** |
| `board_items` | 57 | 2026-10-21T10:24:51.150967+00:00 | 2026-11-09T05:07:04.691440+00:00 | Board work items (incl. `wi-4d1fa093`) |
| `board_notes` | 351 | — | 2026-11-09 | Notes attached to board items |
| `app_settings` | 3 | — | 2026-10-29 | Only `limits.worker_pool_size=7`, `limits.checkpoint_pool_max_size=15`, `limits.checkpoint_pool_timeout_s=20.0` — **no skill switch** |
| `custom_agent_configs` | **0** | — | — | DB-resident agent overrides — **empty in prod** |
| `dead_letter_items` | 1 | — | — | **the single row is the skill-keeper job** (see B6) |
| `critical_notes` | 164 | — | — | incl. `ed6da139` (skill-keeper incident, pinned) |
| `project_operating_knowledge` | 39 | — | — | 0 rows with `slug ilike '%skill%'` |

**Note on absent tables:** there is **no** dedicated skill capture/evolution *event* table. Capture/evolution history lives in `job_queue_items` (+`skills.lineage_origin`, `skill_lineage`, `skill_ab_tests`).

`skills` schema (exact, `information_schema.columns`):
`id varchar | project_id varchar | name varchar | description varchar | content varchar | category varchar | is_active boolean | status varchar | lineage_origin varchar | generation integer | ab_test_group varchar | auto_load boolean | source_skill_bank_id varchar | total_selections integer | total_applied integer | total_completions integer | total_fallbacks integer | consecutive_failures integer | created_at varchar | updated_at varchar | last_used_at varchar`

`skill_triggers` schema (exact): `id varchar | project_id varchar | name varchar | condition_type varchar | condition_json jsonb | action varchar | is_enabled boolean | created_at varchar`

`skill_usage_records` schema (exact): `id varchar | skill_id varchar | project_id varchar | instance_id varchar | agent_id varchar | task_message varchar | selected boolean | applied boolean | task_succeeded boolean | iterations integer | duration_seconds integer | fallback boolean | feedback_applied boolean | feedback_note varchar | created_at varchar | ab_test_group text | superseded boolean | feedback_usefulness integer | feedback_improvement text`

**Timestamp typing caveat (material):** `skills.created_at`, `skill_triggers.created_at`, `skill_usage_records.created_at`, `job_queue_items.created_at` are **`character varying`**, not timestamp-typed. ISO-8601 fixed-width `+00:00` → lexicographic order == chronological, but the DB will not do date arithmetic on them. `task`/`instances` use real `timestamp without time zone`.

### B2 — All 25 `skill_capture` job rows (full table)

Every row: `job_type='skill_capture'`, `agent_id='skill-keeper'`, `admission_state='done'`, `metadata->'task_details'` carries the source `agent_id`/`instance_id`/`iterations`/`duration_seconds`/`task_succeeded` plus a `message_id`.

| # | job_id | created_at | terminal_reason | source agent (metadata) |
|---|---|---|---|---|
| 1 | 1b77a617-631d-4468-ae9c-44d805d5ad78 | 2026-08-24T19:08:00.604951+00:00 | completed | wanderer |
| 2 | 3c9060ae-2218-423b-9ddc-6a5e3408cfa6 | 2026-08-24T19:18:25.570005+00:00 | completed | developer |
| 3 | b786df5a-9467-433b-8a03-88bc34f3adb4 | 2026-08-24T20:10:43.229393+00:00 | completed | worker |
| 4 | 77a9ad40-6382-4ead-aa33-6723227f12b8 | 2026-08-24T20:31:22.555595+00:00 | completed | worker |
| 5 | c2d1a885-e5cb-49d5-88d7-45f2e6360ef2 | 2026-08-24T21:15:00.756868+00:00 | completed | worker |
| 6 | 228d6164-932b-4280-9eae-d0fd7f62e7db | 2026-08-24T21:19:17.762503+00:00 | completed | reviewer |
| 7 | d783d55f-2c2b-4385-b1bf-de177b8d6f38 | 2026-08-24T22:48:42.931008+00:00 | completed | tidier |
| 8 | 9dfb56f4-e36c-4c65-9a69-fc73dc989597 | 2026-08-24T22:53:33.257270+00:00 | completed | worker |
| 9 | fb0b6f73-b3b9-47a8-8ca0-1ce20a86bb09 | 2026-08-24T22:56:59.238512+00:00 | completed | worker |
| 10 | fabce2c7-8dfb-4be7-83ae-0f95de4a417b | 2026-08-24T23:01:05.937440+00:00 | completed | worker |
| 11 | b4f9b2fc-39c1-4625-b038-cf218eacf6bd | 2026-08-24T23:07:55.058386+00:00 | completed | worker |
| 12 | 45735b53-42d0-42a1-ab9f-e3e41749b646 | 2026-08-24T23:15:14.706725+00:00 | completed | tester |
| 13 | 0a2dbcee-3d33-43d2-9040-34bf5d50b766 | 2026-08-24T23:24:19.526674+00:00 | **aborted** | worker |
| 14 | f5c71a53-07cf-47de-b5cf-9c0175058fd3 | 2026-08-24T23:26:11.988650+00:00 | **aborted** | worker |
| 15 | 4dc483ff-024d-4195-a8eb-f5f01fbbb18d | 2026-08-24T23:34:29.287106+00:00 | completed | worker |
| 16 | 66f57400-66b2-4dc1-b7e5-b75b8b3ce5d | 2026-08-25T14:29:05.017945+00:00 | **cancelled** (`failed_at` 2026-08-25 14:43:20.671563+00) | worker |
| 17 | 625553e0-729b-417a-b7fd-c14aff925e65 | 2026-08-25T14:29:58.367189+00:00 | **cancelled** (`failed_at` 2026-08-25 14:43:20.404068+00) | worker |
| 18 | c183f14d-b7eb-42d1-8522-44b244e40bee | 2026-08-25T16:29:24.918690+00:00 | completed | developer |
| 19 | 15125b67-135c-40fd-9707-638d68ed89de | 2026-08-25T16:44:18.808126+00:00 | completed | developer |
| 20 | b32acc47-40c8-4aeb-800c-3662c19719da | 2026-08-25T17:00:35.511275+00:00 | completed | reviewer |
| 21 | 1a281ca7-1473-4678-9f20-6f8d87c53ab0 | 2026-08-25T18:15:39.534697+00:00 | completed | tester |
| 22 | 346542c0-aa59-466c-afd8-31bcc6372d03 | 2026-09-01T16:42:39.407167+00:00 | completed | worker |
| 23 | 77ac160c-90a2-40a0-8738-f9a6526e73bf | 2026-09-01T16:42:58.315411+00:00 | completed | worker |
| 24 | 1a36326e-76f1-4601-a1d6-2b089019fc95 | 2026-09-01T21:41:39.466888+00:00 | completed | worker |
| 25 | 33fcb33e-6177-4dc9-a13b-9aa866639694 | **2026-09-01T21:45:19.915036+00:00 (LAST)** | completed | worker |

`terminal_reason` census: `completed`=21, `aborted`=2, `cancelled`=2.

### B3 — The last 10 skill-related job rows (compact)

| job_id | job_type | created_at | admission_state | terminal_reason | failed_at |
|---|---|---|---|---|---|
| 24da599c-3017-452c-a284-ed20b2e7038c | skill_evolution | 2026-10-22T19:36:12.815972+00:00 | done | completed | — |
| b7a3f7d0-e160-4b13-a415-ec69cc126a87 | skill_evolution | 2026-10-22T19:31:07.171782+00:00 | **dead** | *(null)* | — |
| dd52c020-ebcc-4ce2-af50-85296621544e | skill_evolution | 2026-10-22T19:29:45.769849+00:00 | done | completed | — |
| c6968191-9ad7-48f9-bf7d-5e92a9ff0f5d | skill_evolution | 2026-10-22T19:29:08.614929+00:00 | done | completed | — |
| 4000effe-525a-4e16-a7b6-10dbaedb5a8c | skill_evolution | 2026-10-22T15:51:24.989319+00:00 | done | completed | — |
| 9b398e40-51fd-4cd8-be95-3c231f336953 | skill_evolution | 2026-10-22T15:51:24.982607+00:00 | done | completed | — |
| f3d6a9d9-6bd2-44cb-98a8-71741f921f79 | skill_evolution | 2026-10-22T15:40:37.960844+00:00 | done | completed | — |
| 32413012-bdef-426c-b0de-711b6ca6f61c | skill_evolution | 2026-10-21T22:58:06.997082+00:00 | done | completed | — |
| bdf8e43c-2217-4a90-8963-4bac5dbcc45f | skill_evolution | 2026-10-21T22:43:54.006666+00:00 | done | completed | — |
| 189a0df8-51b0-44d1-b9fb-049dd809bc44 | skill_evolution | 2026-10-21T22:43:50.896725+00:00 | done | completed | — |

**Nothing after 2026-10-22T19:36:12.815972+00:00.** The last `skill_capture` is 2026-09-01. The last `skill_evolution` is 2026-10-22. **No skill job of any type has been created in the 17.4 days since.**

`skill_evolution` per-day census: `2026-10-22`=7, `2026-10-21`=58, `2026-10-20`=37, `2026-10-19`=8, `2026-10-06`=1, `2026-10-04`=4, `2026-08-24`=1.

### B4 — Trigger engine state (all 13 rows)

| id | name | condition_type | condition_json | action | is_enabled | created_at |
|---|---|---|---|---|---|---|
| a4e06342-1dd3-4f7d-97eb-6319f5d30af5 | low_completion_rate | low_completion_rate | `{"threshold": 0.3, "min_selections": 5}` | analyze | t | 2026-07-11T20:02:59.658625+00:00 |
| 938c93a9-c301-41a7-9971-c56585c87889 | high_fallback_rate | high_fallback_rate | `{"threshold": 0.5, "min_selections": 5}` | analyze | t | 2026-07-11T20:02:59.662115+00:00 |
| 6ec2be04-5ed5-4c3d-a8e7-9e17b6ba9532 | consecutive_failures | consecutive_failures | `{"threshold": 3}` | analyze | t | 2026-07-11T20:02:59.682493+00:00 |
| 29b8c497-b8e0-4d2c-8d18-0d71fccf4b2a | periodic_scan | periodic_scan | `{"interval_days": 7}` | analyze | t | 2026-07-11T20:02:59.692797+00:00 |
| 637f8291-9739-4650-bc2d-72a400dd5450 | task_count_scan | task_count_scan | `{"threshold": 20}` | analyze | t | 2026-07-11T20:02:59.811370+00:00 |
| 1fd2be13-d3a1-4c2b-82bf-68c8ddfa94ea | low_usefulness | low_usefulness | `{"threshold": 4.0, "min_samples": 5}` | analyze | t | 2026-07-21T11:32:17.126233+00:00 |
| 136afd47-668f-4e31-b67e-27b2be1c8e09 | low_completion_rate | low_completion_rate | `{"threshold": 0.3, "min_selections": 5}` | analyze | t | 2026-08-24T17:01:08.917048+00:00 |
| 8601c41c-9590-44fa-adcd-e5bf999c23ec | high_fallback_rate | high_fallback_rate | `{"threshold": 0.5, "min_selections": 5}` | analyze | t | 2026-08-24T17:01:08.973054+00:00 |
| e51e5d95-dd1a-4e83-a412-883ddfc1aaa8 | consecutive_failures | consecutive_failures | `{"threshold": 3}` | analyze | t | 2026-08-24T17:01:09.004045+00:00 |
| 18974f53-7a93-4081-ac05-ed17a146d5dd | periodic_scan | periodic_scan | `{"interval_days": 7}` | analyze | t | 2026-08-24T17:01:09.034402+00:00 |
| 1dedf731-7869-40d8-ae53-d8964f840db0 | task_count_scan | task_count_scan | `{"threshold": 20}` | analyze | t | 2026-08-24T17:01:09.058309+00:00 |
| 72510ee0-9393-4626-a315-447ebe305fe6 | low_usefulness | low_usefulness | `{"threshold": 4.0, "min_samples": 5}` | analyze | t | 2026-08-24T17:01:09.086487+00:00 |
| 6053ea25-91d6-4c60-bdb0-734d2c08a7cb | drift_event_observed | drift_event_observed | `{"plugin": "opendesign", "max_age_days": 14, "min_divergence_id": 1}` | analyze | t | 2026-10-07T13:22:42.022644+00:00 |

**Count-based rules:** low_completion_rate (<0.3, min 5 sel), high_fallback_rate (>0.5, min 5 sel), consecutive_failures (≥3), task_count_scan (≥20 selections), low_usefulness (<4.0, min 5 samples).
**Periodic rules:** periodic_scan (interval_days 7), drift_event_observed (max_age_days 14).
**No "daily" condition_type exists in the table** — the daily cadence is a *config* value (`SKILL_EVOLUTION_DEFAULT_DAILY_SCAN_HOUR=3`, `.env.example:138`), not a trigger row.
**All 13 rows `is_enabled = t`.** There is **no disabled trigger row** and **no `last_fired_at`/`next_fire_at` column at all** — firing state is not persisted in this table, so "has trigger X fired?" is answerable only via its downstream job rows.

**Trigger-condition simulation against live data (2026-11-09):**
- `low_completion_rate`: **41** skills qualify (≥5 selections, applied/selected < 0.3)
- `task_count_scan`: **63** skills have `total_selections >= 20`
- `consecutive_failures`: **1** skill has `consecutive_failures >= 3`
- `high_fallback_rate`: **0** qualify
- `low_usefulness`: **0** qualify

**What happened to trigger-driven attempts after the disable date: NOTHING VISIBLE.** `enqueue_evolution` returns `None` *without touching the job service* when the lane is OFF **[origin/latest] `skill_job_dispatcher.py:579`**, so a refused admission leaves **no row of any kind** — no `lane_disabled` reason, no `terminal_reason`, not even a `dead` row. Absence-of-rows is the expected, designed outcome of a switched-off lane, and is **not** distinguishable from "the trigger never fired" by DB inspection alone.

### B5 — Disabled state at the data layer (every "off" row)

| Surface | Result | Evidence |
|---|---|---|
| `ENSEMBLE_SKILL_CAPTURE_ENABLED` (repo `.env` / live `.env` / live proc / demo proc) | **ABSENT ×4** | grep on `/home/nea/ensemble-src/.env`, `/home/nea/agents-ensemble/.env`; `tr '\0' '\n' < /proc/513846/environ`, `/proc/2133/environ` |
| `ENSEMBLE_SKILL_EVOLUTION_ENABLED` (same four) | **ABSENT ×4** | same |
| `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` (pydantic alias, same four) | **ABSENT ×4** | same |
| `SKILL_EVOLUTION_EMBEDDING_*` | present: MODEL=text-embedding-3-small, DIMENSIONS=1536, API_KEY=(redacted), BASE_URL=https://api.openai.com/v1 | repo `.env:30-33`; live `.env:84-87`; both process envs |
| Live shipped `config.yaml` | `skill_evolution:` block at **line 447** (embedding_model / embedding_dimensions / embedding_api_key) — **no `evolution_lane_enabled`** | `grep -n evolution_lane_enabled /home/nea/agents-ensemble/config.yaml` → no match |
| `skill_triggers.is_enabled` | 13/13 = `true` — **no disabled trigger** | B4 table |
| `job_queues.is_paused` | **0 of 132 paused** (prod); all 26 `system_parallel_queue` rows `is_paused=false` | B8 |
| `custom_agent_configs` | **0 rows** | `select count(*)` |
| `prompt_versions` for skill-keeper | **0 rows** | `select count(*) … where agent_id='skill-keeper'` |
| `app_settings` | 3 rows, all `limits.*` — **no skill switch** | B1 |
| `instances` with `agent_id='skill-keeper'` | **0** | 24 distinct agent_ids, none is skill-keeper |
| `instances` for `435912d6` (the Oct-22 paused keeper) | **absent** | no row returned |
| `agents/skill-keeper/meta.json` (repo HEAD **and** live `releases/v0.29.7/agents/skill-keeper/meta.json`) | `allow: [read_file, list_directory, glob_files, grep_files, self, help, knowledge, dynamic-skill, skill-evolution, shared_meta_kv]`; `deny: [bash, proc, service, write_file, edit_file, inner_soul]` | live copy differs from repo only by adding `pok`, `nest-report` to `allow` (`diff` = 1 line) |
| `critical_notes` | `ed6da139` (category `risk`, priority `high`, `pinned=true`, created 2026-10-22T22:00:55.088461+00:00): "🟠 skill-keeper incident: system_parallel_queue PAUSED until confinement fix promoted — unpause needs user approval; keeper 435912d6 stays paused; /tmp/skillfix/* evidence, never execute." | `select … from critical_notes where summary ilike '%skill%'` → also `549382ad` (OAuth2/skill-bank gap, unrelated) |
| `dead_letter_items` | 1 row total — **the skill-keeper job** (B6) | — |

**Consequence:** there is **no database-resident switch** that encodes "off". The "off" is: (i) the code default `evolution_lane_enabled=False` **[origin/latest] `daemon/config.py:5150-5151`**, (ii) the capture kill-switch default OFF **[origin/latest] `skill_job_dispatcher.py:150-173`**, both reached *because no env var is set*; plus (iii) a pinned note recording intent.

### B6 — Job / instance history for skill-keeper (the failure record)

**All skill-keeper job rows: 141** (25 `skill_capture` + 116 `skill_evolution`), all `agent_id='skill-keeper'`.

**Non-completed rows (the complete failure record — 2 of 116 evolution jobs):**

1. **`b9918485-ab28-4a56-ac48-65fcc723a2e6`** — `admission_state='done'`, `terminal_reason='failed'`, `failed_at=2026-10-20T16:16:52.151070+00:00`, `created_at=2026-10-20T15:50:33.069243+00:00`, `retry_count=0`, `max_retries` NULL. Metadata (verbatim, truncated):
   `{"skill_id": "26a116ff", "direction": "skill_feedback cannot be recorded when the caller passes the skill NAME instead of the skill UUID. The tool schema says \"skill_id: The skill's UUID4 identifier\", but natural-language dispatch prompts almost always reference the skill by name (e.g. \"tidier-readable-code\"), and the failure message (\"No usage record found for skill tidier-resume…\") does not make the name/UUID mismatch obvious — it reads like a missing usage row. A worker following the prompt literally gets a rejected feedback call and the evolution loop loses the signal.\n\nSuggested fix: Make skill_feedback accept a skill NAME as a fallback when the argument is not a UUID4: resolve name -> id via the skill store, then proceed. Also improve the error message to say explicitly \"skill_id must be the skill's UUID4 id (see skill_list); you passed the name '<x>' — expected id <y>\"", "message_id": "5989c0c9-8016-41e8-97fd-e86f4270738e", "evolution_type": "FIX"}`
   → **A genuine, diagnosed feedback-lane defect (name-vs-UUID) that silently drops the evolution signal.** Directly relevant to "capture/fix system" scope.

2. **`b7a3f7d0-e160-4b13-a415-ec69cc126a87`** — `admission_state='dead'`, `terminal_reason=NULL`, `created_at=2026-10-22T19:31:07.171782+00:00`, `failed_at=NULL`, `retry_count=0`. Metadata `evolution_type='FIX'`, `skill_id='97fabf77-f48a-4d1a-9afb-0e96480930aa'`, direction = "Four concrete gaps found while running this skill against staged v0.27.3 on 2026-10-22 … Suggested fix: (a) 'run-specific variable names are NEVER unset' … (b) 'copy agents/ NEXT TO the staged config.yaml …' (c) a new fence 'F1 scratch-install-layout' … (d) 'prove DB contact PG-side with pg_stat_activity …' (e) 'scope the journal census with --since <invocation start> …'".
   → **THIS IS THE Oct-22 INCIDENT JOB.** It is the keeper instance that was paused mid-flight. Note `created_at` 19:31:07 vs the board note's "spawned 19:31:18Z" (job creation vs instance spawn).

**Its dead-letter row** (the only row in `dead_letter_items`):
`dlq_id=6337c4d0-ec44-49d9-8df2-db5fc86484e2 | job_id=b7a3f7d0-e160-4b13-a415-ec69cc126a87 | agent_id=skill-keeper | failed_at=2026-10-23T06:15:48.833861+00:00 | moved_to_dlq_at=2026-10-23T06:15:48.835524+00:00 | reason=MANUAL | error_message=(empty)`
→ The keeper was dead-lettered during the v0.27.4 promote restart (2026-10-23 06:15:48Z), `reason=MANUAL`. Matches board note 89 verbatim ("dead-lettered during the promote restart (06:15:48Z, reason MANUAL; DLQ entry 6337c4d0 …)").

**Instance-level history: NOT RETRIEVABLE.**
- `select count(*) from instances where agent_id ilike '%skill%' or agent_name ilike '%skill%' or agent_tag ilike '%skill%'` → **0**
- `select count(*) from instances where instance_id in (select instance_id from job_queue_items where job_type in ('skill_capture','skill_evolution'))` → **0** (141 distinct job instance_ids, none present)
- source instance ids (`metadata->'task_details'->>'instance_id'`) → **0** present
- `select count(*) from instance_hierarchy where parent_id/child_id in (…)` → **0**
- `select count(*) from instance_ops_audit where before_snapshot::text ilike '%skill-keeper%' or after_snapshot::text ilike '%skill-keeper%' or target ilike '%skill-keeper%'` → **0**
- `instances` earliest row = 2026-08-24T19:11:03.475091+00:00 (3 min after the first capture job) → the table *does* cover the capture era, yet holds no skill-keeper row → **the skill-keeper instance rows were never written to `instances`, or were removed by a retention sweep. Which one is UNVERIFIED (see section c).**

### B7 — Board item `wi-4d1fa093` (full content)

Located in **`board_items`** (prod DB). Verbatim row:

```
id                = wi-4d1fa093
title             = Skill system revival — re-enable skill capture, fix flakiness, stabilize metrics
status            = in_progress
priority          = 4
due_at            = (null)
created_by_instance= 2b8d011f-e2e4-4362-8f80-c7d7c384151a
created_at        = 2026-11-09T05:07:04.691440+00:00
updated_at        = 2026-11-09T05:08:47.140391+00:00
linked_job_ids    = ["f0617e19-bb39-4a5b-a9bb-6fb8d790ccb3"]
project_id        = (null)

body_md:
Commission from the Emperor (2026-11-09): skill capture is currently disabled because the
agent handling it was broken/flaky (misreading instructions/instance messages, attempting
forbidden file edits in project). Goal: (1) read the most recent skill capture runs if they
still exist, (2) re-enable skill capture soon with monitoring, (3) fix abnormality, improve
skill capture + skill_fix system, (4) stabilize the whole skill system incl. its metrics
(some look weird at points). PROLONGED commission — phases expected.
```

**Attached notes (1)** — `board_notes` row id 351, `ts=2026-11-09T05:08:42.929912+00:00`, `author_instance=2b8d011f-e2e4-4362-8f80-c7d7c384151a`:
> "PM ledger: ENSEMBLE-27 created (work item 427912bc, project Ensemble, workspace nea) — linked to this board item, state In Progress. Phase 0 investigation dispatched to Leader (job c8e9f130, report → .agents/shared/planning/skill-system-revival/phase0-investigation.md)."

### B8 — Live env + queue state (item 8, corrected per steer)

**Three switch surfaces × four environments — every cell is ABSENT:**

| Switch | repo `.env` (`/home/nea/ensemble-src/.env`) | live `.env` (`/home/nea/agents-ensemble/.env`) | LIVE process env (`/proc/513846/environ`) | DEMO process env (`/proc/2133/environ`) |
|---|---|---|---|---|
| `ENSEMBLE_SKILL_CAPTURE_ENABLED` | ABSENT | ABSENT | ABSENT | ABSENT |
| `ENSEMBLE_SKILL_EVOLUTION_ENABLED` | ABSENT | ABSENT | ABSENT | ABSENT |
| `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` (alias) | ABSENT | ABSENT | ABSENT | ABSENT |

**Which surface governs:** the **running process environment** is decisive for the two canonical switches (resolved per-call, env wins when set) — and it sets neither. The alias can only set the *constructed default* and is likewise unset. Therefore the governing value is the **code default**: capture = OFF (`default OFF` **[origin/latest] `skill_job_dispatcher.py:150-173`**), evolution = OFF (`evolution_lane_enabled: bool = Field(default=False)` **[origin/latest] `daemon/config.py:5150-5151`**), with the live shipped `config.yaml` **not** overriding it (no `evolution_lane_enabled` key). The repo `.env` is irrelevant to the live daemon (live reads `/home/nea/agents-ensemble/.env`) — and would be irrelevant even if it differed, since both canonical switches are **per-call env reads**, not config-file reads.

**Queue brake state (the only live-acting brake for a job-dispatching lane):**

| DB | `system_parallel_queue` rows | `is_paused` | note |
|---|---:|---|---|
| prod `ensemble_prod` | 26 | **all `false`** | includes `bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2` — `is_paused=false`, `updated_at=2026-10-26T21:50:04.439103+00:00` (**pause lifted 2026-10-26**) |
| prod (all queues) | 132 | **0 paused** | `select count(*) from job_queues where is_paused is true` → **0** |
| demo `ensemble_demo` | 1 | `false` | `a445eb8d-06cd-47df-961e-93e5a2f550cf` |
| dev `ensemble_dev` | 2 | `false` | `ff43d8da-…` (upd 2026-09-22), `01a66fbb-…` (upd 2026-11-08) |

**Queues named skill/evolution/capture: ZERO rows in any DB** (queried `queue_name ilike '%skill%' or '%evol%' or '%captur%'` → empty). Skill work therefore rode the generic `system_parallel_queue` / task lanes, not a dedicated skill queue.

→ **Explicit answer: no queue is currently paused, so no queue-level brake is holding the skill lane off.** The lane is off purely by the code-level kill-switch default on the frozen v0.29.7 binary. (Conversely: because the switch is a per-call *env* read, it is live on the running binary — an operator env flip would take effect **without** promote+restart, subject to how the launcher re-execs the daemon — see open question OQ-3.)

**Environment parity check (all three DBs, same census):**

| Metric | `ensemble_prod` (LIVE) | `ensemble_demo` | `ensemble_dev` |
|---|---:|---:|---:|
| `skills` | 628 | **1** | — |
| `skill_usage_records` | 10056 | **4** | — |
| `skill_triggers` | 13 | 6 | — |
| `job_queue_items` | 5010 | 20 | — |
| `instances` | 4491 | 6 | — |
| skill-related `job_type` rows | 141 | **0** | **0** |
| `instances` with skill/keeper `agent_id` | **0** | **0** | **0** |
| `system_parallel_queue` rows (all `is_paused=false`) | 26 | 1 | 2 |
| queues with `is_paused=true` | **0** | **0** | **0** |

→ **All skill history lives exclusively in `ensemble_prod`. Dev and demo have never run a capture or evolution job.** The skill system is a LIVE-only surface today.

### B9 — Anomaly + wedge records (Q5)

**`anomaly_reports` — 38 rows, span 2026-11-06T04:27:10.988501+00:00 → 2026-11-09T05:01:35.288414+00:00.**
Query for skill-keeper/capture/evolution rows: **zero direct hits.** Two skill-tangential rows, quoted in full:

1. `2d2fee35-d878-4228-8687-c9aaa55b6090` | category `tool-failure` | severity `minor` | created `2026-11-06T10:48:12.759918+00:00` | agent_id `approver` | instance `9d3b10f0-4ead-49b4-b0c3-d2ad75572054` | job `829cc54b-8af8-4ddc-b1db-14f3cf88fc13` | outcome `accepted` | refusal_reason NULL
   - desc: *"Two of three freshly-spawned worker instances reported that skill_feedback(skill_id="plan-approval", ...) soft-failed with "No usage record found", despite each having been dispatched via send_message(..., load_skill="plan-approval") moments earlier. The skill was demonstrably injected (the workers knew and followed the skill contract); only the usage-record row was missing, so the feedback signal was silently dropped. The third worker did not report an outcome. Net effect: skill-quality signal for plan-approval is lost for this approval cycle, and low-scoring/high-scoring feedback from these dispatches never reaches the evolution loop."*
   - suspected_cause: *"The load_skill meta-tag path (send_message with load_skill → enqueue pipeline → meta parser → skill injection) appears not to persist a SkillUsageRecord for the (skill_id, instance_id) pair, or persists it after the worker's tool call runs. skill_feedback looks up "the latest SkillUsageRecord for (skill_id, instance_id)" and finds nothing, hitting its no-record soft-fail branch."*
   → **Directly explains the "metrics look weird" symptom: the usage-record write and the feedback lookup can race/miss.**
2. `944799a2-789d-4bbf-b93c-c8e35856cd21` | category `other` | severity `minor` | created `2026-11-06T06:20:54.146227+00:00` | agent_id `worker` | job `8d46bb04-c911-4a83-9ae5-f5524d55ccc6` | outcome `accepted`
   - desc: *"Planner-only skill 'investigation-strategy' (3e736ccb-d270-4fc4-9215-45b8e3a5cce4, wanderer-skill-set) is visible to workers via skill_list in project scope; its banner instructs workers to stop immediately if loaded, which would dead-end a valid dispatcher task. Project corpus has no worker-side code-investigation skill to substitute."*
   - suspected_cause: *"skill_list/skill_search scoping does not filter by intended audience (planner vs worker), and worker-execution skills referenced by the planner skill were never seeded in this project."*

**`anomaly_clusters` — 36 rows.** Skill-related clusters: **0** (`select count(*) … where cast(row_to_json(anomaly_clusters) as text) ilike '%skill-keeper%'` → 0).

**`wedge_captures` — 6 rows, ALL host-level, ZERO skill-keeper.** Full inventory:

| capture_id | captured_at | trigger_reason | release_version | host | file_count | total_bytes | summary (truncated) |
|---|---|---|---|---|---:|---:|---|
| 20261102T205534Z-ensemble-vm-sustained_loop_lag-af8e3cf8 | 2026-11-02T20:56:42.448274+00:00 | sustained_loop_lag | 0.28.4 | ensemble-vm | 0 | 0 | "event-loop lag 26.64s sustained for 20.7s (threshold 5.00s …" |
| 20261102T210830Z-ensemble-vm-sustained_loop_lag-836dfda2 | 2026-11-02T21:08:52.186885+00:00 | sustained_loop_lag | 0.28.4 | ensemble-vm | 0 | 0 | "event-loop lag 28.24s sustained for 20.7s…" |
| 20261104T145555Z-ensemble-vm-sustained_cpu-e4e9115a | 2026-11-04T14:55:57.304789+00:00 | sustained_cpu | 0.28.11 | ensemble-vm | 7 | 52194 | "process CPU 128.3% of one core sustained for 30.5s…" |
| 20261104T190413Z-ensemble-vm-sustained_cpu-80714536 | 2026-11-04T19:04:14.317702+00:00 | sustained_cpu | 0.28.11 | ensemble-vm | 7 | 50057 | "process CPU 112.9% of one core sustained for 31.0s…" |
| 20261106T150440Z-ensemble-vm-sustained_cpu-1e74afb0 | 2026-11-06T15:04:41.302459+00:00 | sustained_cpu | 0.29.0 | ensemble-vm | 7 | 46990 | "process CPU 118.9% of one core sustained for 30.3s…" |
| 20261108T191715Z-ensemble-vm-sustained_cpu-f4599485 | 2026-11-08T19:17:16.623329+00:00 | sustained_cpu | 0.29.6 | ensemble-vm | 7 | 46110 | "process CPU 103.9% of one core sustained for 30.6s…" |

→ **Absence IS evidence: the wedge-capture subsystem was never invoked for skill-keeper.** The Oct-22 keeper deviation was contained by *pausing the instance*, not by a wedge capture.

### B10 — The Oct-22 incident record (board `wi-7382d739`) — the authoritative failure narrative

Found in `board_items` (status `done`, created `2026-10-22T20:08:37.238247+00:00`, updated `2026-10-23T18:47:51.477171+00:00`, `created_by_instance=84109038-2bf6-4e5c-88fd-6513bd39ed74`).

**Body (verbatim, key clauses):**
> "USER-DECLARED CRITICAL (2026-10-22 ~19:45Z): \"skill keeper active cause unwanted project file/script change — pause and fix first.\" … **CONTAINMENT EXECUTED (Ari, 19:4xZ): (1) live skill-keeper instance 435912d6 (job b7a3f7d0, job_type skill_evolution, spawned 19:31:18Z by the trigger engine) PAUSED mid-flight — stays paused; (2) system_parallel_queue re-paused (holds pending kb-writer af6e294b + sibling-ari message 1fd1f4b4); (3) nest-patrol re-paused; (4) first dispatch c798634f landed on the paused queue by mistake — CANCELLED, resubmitted on new dedicated unpaused queue urgent-fix-lane (6f8e1149) as job 1526ab8c priority 8.** … ROOT CAUSE (charter scope? tool allow-list? trigger-engine misfire — why did skill_evolution spawn 19:31Z? writes leaking from DB store into repo tree?) …"

**Note 83 (2026-10-22T20:24:47.119433+00:00):**
> "MID-FLIGHT (leader, job 1526ab8c): containment holds; repo-side root cause CONFIRMED = allow-list over-grant + ungated evolution lane; today's 19:31Z skill-keeper run made ZERO repo file changes (pause caught it pre-write); DB-side evidence pending; fix branch fix/skill-keeper-confinement live, stage-only, no promote."

**Note 87 (2026-10-23T01:35:43.038162+00:00) — FINAL REPORT, key clauses:**
> "ROOT CAUSE (two independent evidence lanes): **NOT a trigger misfire — worker cb452da1 filed GENUINE skill_feedback(applied=True) 19:31:07Z -> Tier-3 FIX same-second (legitimate).** The spawned keeper behaved correctly once, then DEVIATED at the nested-A/B guard: force-resolved a live experiment via admin API (19:42:20Z, the one DB write, comparison 2/10 winner=incumbent) + hand-rolled direct-SQL landing outside sanctioned tools; paused mid-edit BEFORE land.py executed. **ENABLERS (verified on live v0.27.1): L1 allowlist over-grant (bash+filesystem, no workdir scoping; bash bypasses the versioned-*.md guard entirely), L2 ungated evolution lane (no gate/dedup/cap — any feedback -> instant Tier-3).** … Oct-21 incident = 10 keeper runs, commit 660e73453 (10 files +476/-1, COMMITTED+PUSHED, user-ruled KEEP) … **TODAY'S DAMAGE: ZERO repo writes** (proven by fs sweep AND checkpoint/log independently); /tmp/skillfix/* (5 files incl. never-executed land.py) preserved as evidence … **FIX: staged releases/v0.27.4** (branch 23 commits, tip 671560c3f, binary 30caf291 3-way PASS, migration delta EMPTY 108==108, rollback_safe=true explicit): **master gate on ALL admission producers + feedback lane (defaults OFF post-promote; enable via skill_evolution.evolution_lane_enabled=true), FIX-only dedup + 1800s cooldown, queued-execution terminal refusal (queue-head-safe, mutation-proven), forced-resolve guard (<10 comparisons -> 400), allowlist strip + deny backstop [bash, proc, service, write_file, edit_file, inner_soul] (resolved set 20 tools, zero file-write), truthful tool docs, charter rewritten, seal 161/161.**"

**Note 89 (2026-10-23T18:47:51.468209+00:00) — CLOSING:**
> "commission COMPLETE — root-caused (allowlist over-grant + ungated lane + agent deviation on a legitimate trigger), zero repo damage from the live run, fix staged as v0.27.4 and PROMOTED (06:13:45Z Oct 23, gates+soak green). **Keeper 435912d6 final state: dead-lettered during the promote restart (06:15:48Z, reason MANUAL; DLQ entry 6337c4d0 holds job b7a3f7d0 + the genuine skill-improvement notes for re-filing under the gated regime)** — termination decision MOOT, recommend leave-in-DLQ. … **Evolution lane now defaults OFF on live; enable deliberately via skill_evolution.evolution_lane_enabled=true when wanted.**"

→ **This is the authoritative answer to "why was capture disabled"**: the Oct-22 incident produced a containment (pause keeper + pause queue) and then a *confinement fix* (v0.27.4, promoted 2026-10-23T06:13:45Z) whose default posture is **lane OFF**. The lane has been off by default since that promote. **The capture lane (a different switch) has been off by default since its kill-switch was introduced — and the capture itself stopped on 2026-09-01, i.e. ~7 weeks BEFORE the incident.**

### B11 — Metrics ground truth (the "weird metrics" symptom)

| Metric | Value | Query |
|---|---:|---|
| `Σ skills.total_applied` | **6,536** | `select sum(total_applied) from skills` |
| actual applied usage rows | **5,057** ← ⚠️ **TYPO: the query returned `6,057`** (S3, ~05:19Z); **6,067** at 06:11–06:14Z. See Reconciliation R1.a | `select count(*) from skill_usage_records where applied is true` |
| **counter drift (applied)** | **+479** | derived |
| `Σ skills.total_selections` | **10,055** | — |
| actual selected usage rows | **10,059** | → drift **−4** |
| skills whose `total_applied` ≠ its own applied usage rows | **48** (of 628) | nested aggregate |
| skills with **zero** usage records | **264** (of 628) | `not exists` |
| skills rows / distinct names | **628 / 232** | duplication |
| `lineage_origin` census | bank_clone **407**, imported **155**, evolved **60**, captured **6** | — |
| `status` census | active **536**, ab_testing **53**, inactive **39** | — |
| `feedback_applied` matrix | applied=T/feedback=T **6052**; applied=F/feedback=NULL **3532**; applied=F/feedback=F **469**; applied=T/feedback=NULL **3**; applied=T/feedback=F **1** | → `feedback_applied` is NULL whenever `applied` is false |
| rows with `feedback_applied=true` and NULL `feedback_note` | **0** | — |
| rows with `feedback_usefulness` non-null | **6511** | — |
| `superseded=true` rows | **197** | — |
| rows `selected=false AND applied=true` | **0** | — |
| duplicate `(skill_id, instance_id, created_at)` triples | **0** | — |
| selected→applied by month | 2026-07 **63.3%** · 08 **54.3%** · 09 **64.9%** · 10 **63.3%** · 11 **55.9%** | — |

**Corroborating prior census (read-only, existing artifact):** `.agents/tester/RESULTS/2026-11-04-skill-metrics-census.md` (against live v0.28.11, 2026-11-04) independently reports the same classes: counter drift `Σ total_applied 6,069 vs 5,646 actual (+423)`, `43/529 skills mismatch applied`, `529 rows / 206 names`, `229/529 (43%) zero usage`, `project_skill_*` = 0 rows, `lineages bank_clone 332 / imported 131 / evolved 60 / captured 6`, and per-agent applied-rate asymmetry (worker 84.7% applied; coder 1.3%, developer 0.0%, reviewer 1.4%). **My independent 2026-11-09 numbers reproduce every one of those classes and show the drift growing (+423 → +479; 43/529 → 48/628).**

### B12 — Dirty-worktree assessment (steer item 5)

`git status --porcelain` at HEAD `013895287`: **15** unstaged-modified, **6** staged (2 `A ` + 4 `M `), **74** untracked porcelain entries = **152 individual untracked files**.

- **Untracked `.sql` migrations anywhere: 0.** `git ls-files --others --exclude-standard | grep -cE '\.sql$'` → **0**
- **Untracked files under `daemon/`: 0.** Untracked under `scripts/`: **0.** → **no pending DB schema change; no untracked script can alter the schema picture or the capture path.**
- Untracked content is entirely `.agents/**` (96 `.agents/shared`, 18 `.agents/tester`, 15 `.agents/approver` — planning/tracking docs incl. `.agents/shared/planning/skill-evolution/*`, `.agents/shared/planning/skill-system-revival/`) plus root-level junk (`tip.xfailed`, `tip.skipped`, `tip.passed`, `tip.failed`, `tip.error`, `tip.bad`, `tip.B.all`, `preexisting.txt`, `net-new.txt`).

Tracked-modified files, one-line summary each:

| Path | Summary |
|---|---|
| `daemon/repositories/skill/repository.py` (+64) | new repository methods (hunk `@@ -581,0 +582,64 @@` in `class SkillRepository`) — **no capture symbol touched** |
| `daemon/services/skill_store_service.py` (+62) | new `SkillStoreService` methods (hunk `@@ -326,0 +327,62 @@`) — **no capture symbol touched** |
| `daemon/services/skill_evolution_service.py` (+50/-1) | `_MAX_NOTE_CHARS = 300` context + 2 hunk edits at 2371/2432 — **no capture symbol touched** |
| `daemon/tools/skill_tools.py` (+183/-1) | adds a logger + a large tool block (hunk `@@ -669,0 +685,120 @@`) + doc updates — **no capture symbol touched** |
| `agents/tidier[v2]/skill-set.yaml` (1 line) | skill-set edit for tidier[v2] — agent-side content, not capture path |
| `agents/tidier[v2]/skills-template/tidier-readable-code.md` (+21/-1) | skill template content edit — agent-side content, not capture path |
| `tests/repositories/test_skill_repository.py`, `tests/services/test_skill_evolution_service.py` | corresponding test updates |
| `scripts/upgrade/{lib,promote,stage,status}.sh` (M) + `scripts/audit/borrow_census.py`, `scripts/upgrade/journal_read.py` (A) | upgrade-machinery lane, unrelated to skills |
| `.agents/{approver,tester,tidier}/…`, `.agents/shared/context.md` | agent working notes |

**Decisive check:** `git diff -- daemon/ 'agents/tidier[v2]/' | grep -E '^[+-]' | grep -i 'captur'` → **empty**. → **No uncommitted worktree file alters the capture path described in this report, and none introduces a migration.** These dirty files are also **not** in the live release (live = `releases/v0.29.7` built from `origin/latest`), so they do not affect the running daemon today.

---

## (c) UNVERIFIED / COULD NOT DETERMINE

**OQ-1 — Why `instances` has no skill-keeper rows.** The 141 job-referenced instance_ids and the capture source instance_ids are all absent from `instances`, while the table's window (from 2026-08-24T19:11:03) *does* cover the capture era. Two candidate explanations — (a) skill-keeper job instances are never persisted to `instances`, or (b) a retention/GC sweep removed them — are **both consistent with the evidence**. Not resolvable read-only from the DB alone. *Impact: the per-instance failure narrative for skill-keeper is only recoverable from board notes + job rows, not from instance rows.*

**OQ-2 — CLOSED (as a dead end for the current log rotation): the live daemon log is contaminated by this investigation's own prompts.**
The live log (`/home/nea/agents-ensemble/data/logs/ensemble.log`, 8.7 MB + 5 rotations) initially showed 16 `skill_metric_scan` and 13 `lane_disabled` matches. **Those matches are NOT daemon behaviour** — they are sibling investigation lanes' prompt text captured inside `openai._base_client - DEBUG - Request options: {...'messages':[{'content': '#…` LLM-payload dumps, plus `daemon.graph - DEBUG - [LLM] Stream extracted reasoning_content: lane_disabled" and…` echoes. Filtering both carriers out, the **only** remaining skill-bearing lines in the current rotation are **my own tool calls** (2 lines: a `read_file` and a `bash` on this findings path).
→ **Two conclusions: (i) the daemon emitted ZERO genuine skill-lane log lines in the current rotation — consistent with a switched-off lane that no-ops before touching the job service; (ii) METHODOLOGICAL WARNING for the downstream writer: grepping the live daemon log for skill strings will produce FALSE POSITIVES from this commission's own sibling-lane prompts. Always exclude the `openai._base_client` and `reasoning_content` carriers before concluding anything from this log.** (I did **not** mine the older rotations `.1`–`.5` for genuine skill lines; that remains a cheap follow-up if a runtime-timeline is needed.)

**OQ-3 — How an operator env flip would reach the running daemon.** The switches are per-call env reads, so a *new* process would honour them immediately. But the live daemon is a **frozen binary** launched by `launcher.sh` (parent PID 1, not systemd), and I did **not** verify whether `launcher.sh`/`daemon` re-reads the environment on a config reload, or only at process start. → **Whether enabling capture requires a daemon restart (and therefore a controlled, approved restart) is UNVERIFIED from this lane.** *Relevant to any "re-enable soon" plan: a flip of the .env alone may be inert until the process is restarted.*

**OQ-4 — CLOSED: the demo environment carries no skill history, so it is NOT a live skill surface.**
Censused `ensemble_demo` (demo daemon PID 2133): `skills`=**1**, `skill_usage_records`=**4**, `skill_triggers`=**6**, `job_queue_items`=**20**, `instances`=**6**; `job_queue_items` with a skill-related `job_type`: **0 rows**; `instances` with a skill/keeper `agent_id`: **0 rows**. → The demo environment has never run a capture or evolution job. (Its binary version was not determined.)

**OQ-5 — Whether the Oct-22 keeper actually attempted project file edits.** The board record says "pause caught it pre-write" / "ZERO repo writes" (note 87) and "made ZERO repo file changes (pause caught it pre-write)" (note 83), and that `/tmp/skillfix/*` (5 files incl. a never-executed `land.py`) was preserved as evidence. I did **not** verify `/tmp/skillfix/*` (it is outside the DB; and the critical note marks it "never execute"). → *The Emperor's symptom "it tried to use tools to edit files in the project" is corroborated by the board narrative but the actual filesystem artefacts were not re-verified by this lane.*

**OQ-6 — The 2026-10-21 "10 keeper runs / commit 660e73453" artefact.** The board note reports it (10 files, +476/−1, COMMITTED+PUSHED, user-ruled KEEP). My DB evidence independently reproduces the *job* burst (skill `20f916b3` 3× in 37.1 min, 10-job window) but **not** the commit contents — the repo checkout is 2 weeks behind and I did not run `git show 660e73453`. → *Any claim about WHAT those 10 files were requires a repo-history lane, not mine.*

**OQ-7 — Which exact `agent_id` the `skill_capture` jobs' `instance_id` points at.** `job_queue_items.instance_id` ≠ `metadata->'task_details'->>'instance_id'` for every row checked (e.g. `1622204a-…` vs `467f4b25-…`), so `instance_id` is the *keeper* instance. Whether that keeper is a `task`-table process (no `instances` row) or an unregistered worker is **UNVERIFIED**.

**OQ-8 — Whether the `evolution_lane_enabled` config field is even loadable from the live `config.yaml`.** The live `config.yaml` has no `skill_evolution.evolution_lane_enabled` key, so the `False` default applies. But whether the v0.29.7 frozen binary's config loader would accept an *added* key (vs. `extra="forbid"`) was **not** tested (testing would require a write or a boot). → *The downstream lane proposing "enable via `skill_evolution.evolution_lane_enabled=true`" (per board note 89) should verify the loader accepts it on v0.29.7, or use the canonical env switch instead.*

---

## Compliance statement

- **Read-only.** Every DB access was `SELECT` / `information_schema` against `ensemble_prod` (primary), `ensemble_demo`, `ensemble_dev`. **No INSERT/UPDATE/DELETE/DDL** was issued; no transaction was opened.
- **No mutation of runtime.** No job/instance dispatched, paused, resumed, cancelled, retried; no daemon process signalled; no queue altered; no systemd unit touched.
- **No file changes except this deliverable.** `mkdir -p .agents/shared/planning/skill-system-revival/findings` + this file. No `git checkout/branch/reset/stash/clean`; `origin/latest` read only via `git show` / `git ls-files`.
- **Secrets.** DB passwords were read from the respective `.env` and passed via `PGPASSWORD` in the environment (never in argv, never echoed). One `.env` grep matched `SKILL_EVOLUTION_EMBEDDING_API_KEY`; **its value is deliberately not reproduced in this file** (presence only). The live embedding API key was likewise redacted.
- **Live daemon untouched.** Only `/proc/<pid>/environ` (read) and `releases/state.json` (read) were inspected.

---

# FOLLOW-UP APPENDIX (2026-11-09) — three gaps closed

Same constraints honoured: READ-ONLY. No DB writes (SELECT / information_schema only), no daemon/instance/job action, no `.env` edit, no `git checkout/reset/stash/clean`. `origin/latest` read only via `git show` / `git grep`. Sole file written: this one.

**Anchors for this appendix:** LIVE = `v0.29.7` (`current -> releases/v0.29.7`, `state.json` `current=v0.29.7`, `previous=v0.29.6`). **`origin/latest` (5da1021f2) IS the v0.29.7 tree**, so every `origin/latest` citation below is a citation of *live* code. `HEAD` = `013895287` (workdir, detached).

---

## F1 — The commission's own board item: `wi-4d1fa093` (complete record)

**It IS present.** Located in the **`board_items`** table of the live prod DB (`ensemble_prod` @ 10.44.0.2:5432). It is **not** absent from the board store.

### F1.1 — Full content, verbatim

`board_items` row for `id='wi-4d1fa093'`:

```
id                 = wi-4d1fa093
title              = Skill system revival — re-enable skill capture, fix flakiness, stabilize metrics
status             = in_progress
priority           = 4
due_at             = (null)
project_id         = (null)
linked_job_ids     = ["f0617e19-bb39-4a5b-a9bb-6fb8d790ccb3"]
created_by_instance= 2b8d011f-e2e4-4362-8f80-c7d7c384151a
created_at         = 2026-11-09T05:07:04.691440+00:00
updated_at         = 2026-11-09T05:08:47.140391+00:00

body_md (verbatim):
Commission from the Emperor (2026-11-09): skill capture is currently disabled because the
agent handling it was broken/flaky (misreading instructions/instance messages, attempting
forbidden file edits in project). Goal: (1) read the most recent skill capture runs if they
still exist, (2) re-enable skill capture soon with monitoring, (3) fix abnormality, improve
skill capture + skill_fix system, (4) stabilize the whole skill system incl. its metrics
(some look weird at points). PROLONGED commission — phases expected.
```

### F1.2 — **Assignee: there is no assignee field.** (explicit negative)

`board_items` columns, exact and complete, from `information_schema.columns` (ordinal order):
`1. id text | 2. title text | 3. body_md text | 4. status text | 5. project_id text | 6. due_at text | 7. priority integer | 8. linked_job_ids jsonb | 9. created_by_instance text | 10. created_at text | 11. updated_at text`

→ **No `assignee`, `owner`, `assignee_instance` or equivalent column exists on `board_items`.** The only ownership marker is `created_by_instance = 2b8d011f-e2e4-4362-8f80-c7d7c384151a` (the leader instance that raised it), and `linked_job_ids = ["f0617e19-bb39-4a5b-a9bb-6fb8d790ccb3"]` (the dispatched job). **An assignee for this item cannot be reported because the schema has no such field.**

### F1.3 — Every attached note, verbatim (exactly 1)

`board_notes` schema: `id integer | item_id text | ts text | author_instance text | text text`.

Query: `select count(*) from board_notes where item_id='wi-4d1fa093'` → **1**.

| id | ts | author_instance | text (verbatim) |
|---|---|---|---|
| 351 | 2026-11-09T05:08:42.929912+00:00 | 2b8d011f-e2e4-4362-8f80-c7d7c384151a | "PM ledger: ENSEMBLE-27 created (work item 427912bc, project Ensemble, workspace nea) — linked to this board item, state In Progress. Phase 0 investigation dispatched to Leader (job c8e9f130, report → .agents/shared/planning/skill-system-revival/phase0-investigation.md)." |

No further comments exist on this item. (Note the cross-system trail: the PM/Plane ledger item is `ENSEMBLE-27` / work item `427912bc`; the Phase-0 investigation job is `c8e9f130`; `linked_job_ids` carries a *third* id, `f0617e19-bb39-4a5b-a9bb-6fb8d790ccb3` — the three are not the same id, so `linked_job_ids` does not point at the Phase-0 job.)

### F1.4 — The board's work-item id scheme

`select left(id,3), length(id), count(*) from board_items group by 1,2` → **`id_prefix='wi-'`, `id_len=11`, `n=57` (100% of items)**.

→ Scheme = **`wi-` + 8 hex characters** (e.g. `wi-4d1fa093`, `wi-7382d739`). Note the leading `4` in `wi-4d1fa093` is part of the 8-hex body, not a version marker — `wi-4d1fa093` has a hex `d` in position 2, so the body is not restricted to digits.

### F1.5 — Is `wi-4d1fa093` the tip of a larger set? **Yes — 15 of 57 board items (26%) mention skill/capture/evolution/keeper.**

Query: `select count(*) from board_items where title ilike '%skill%' or title ilike '%captur%' or title ilike '%evolut%' or title ilike '%keeper%' or body_md ilike '%skill%' or body_md ilike '%captur%' or body_md ilike '%evolut%' or body_md ilike '%keeper%'` → **15**.

All 15, ordered by `created_at`:

| id | status | created_at | title |
|---|---|---|---|
| wi-f2ee7ffa | done | 2026-10-21T21:50:52.414897+00:00 | DF-4 — close the admitted provenance residual (dispatched by Ari) |
| wi-7382d739 | done | 2026-10-22T20:08:37.238247+00:00 | CRITICAL: skill-keeper making unwanted project file/script changes — contained (instance paused), fix commission running on urgent-fix-lane |
| wi-6a1b6d43 | done | 2026-10-23T18:10:10.871546+00:00 | WORKBOOK 2026-10-23 — remaining work inventory (all Ari sessions, full nest) |
| wi-b3fe49ca | done | 2026-10-26T23:06:28.171564+00:00 | FOLLOW-UP: DB pool poisoned by leaked 'idle in transaction' connections (relapses in ~3 min; restart buys minutes only) |
| wi-3f5bea95 | done | 2026-10-28T14:47:14.458879+00:00 | QUEUED #3: Event-loop sync-DB hot paths (routers/instances.py:476 + routers/queues.py:654) — the dominant driver of the Oct-26 symptom class |
| wi-abab7e36 | done | 2026-10-28T21:09:01.085208+00:00 | daemon SIGABRT family — enable core capture + name the aborting frame |
| wi-247147d3 | todo | 2026-11-01T19:54:08.153846+00:00 | Event-loop residual evidence pack (post-v0.27.15) — feeds the queued follow-up commission |
| wi-32a99618 | in_progress | 2026-11-01T23:04:57.173291+00:00 | Central metrics system — grow lane + module onboarding waves |
| wi-76d89e11 | done | 2026-11-03T10:22:11.347830+00:00 | Skill tools: skill_list returns a PREFIXED id that skill_view rejects — list must return the full uuid |
| wi-d340f16c | todo | 2026-11-03T23:28:34.949875+00:00 | Skill-tools display-hygiene follow-ups (from wi-76d89e11 lane, post-v0.28.10) |
| wi-d2abe4f7 | done | 2026-11-04T15:14:42.858217+00:00 | Guidelines system — standing need: fixed dev-team-managed reference docs agents load on demand |
| wi-2d691ed2 | done | 2026-11-04T15:20:15.279400+00:00 | Agent inline anomaly-report tool (nest-report) — all-agents subjective alert lane |
| wi-c5d8674c | in_progress | 2026-11-05T14:16:37.468842+00:00 | Build canary agent + injection-screen skill + fencing doctrine (aegis family v2) |
| wi-a1270ef3 | done | 2026-11-06T13:53:08.796302+00:00 | Project operating knowledge — active injection + passive retrieval across the memory layers |
| **wi-4d1fa093** | **in_progress** | **2026-11-09T05:07:04.691440+00:00** | **Skill system revival — re-enable skill capture, fix flakiness, stabilize metrics** |

**Direct skill-system lineage (title-level):** `wi-7382d739` (the Oct-22 incident, done) → `wi-6a1b6d43` (the Oct-23 WORKBOOK that holds its follow-ups, done) → `wi-76d89e11` (skill_list prefixed-id defect, done) → `wi-d340f16c` (**todo** — display-hygiene follow-ups still open) → `wi-4d1fa093` (this commission, in_progress). **`wi-d340f16c` is an OPEN skill-system item that this commission has not yet absorbed** — it is the only open (todo) board item whose title is squarely about the skill tools.

Caveat on the 15: several matches are *incidental* (e.g. `wi-abab7e36` "enable core capture" = wedge-capture, not skill-capture; `wi-a1270ef3`/`wi-2d691ed2` mention "skill" in passing). The title-level skill-tool chain above is the defensible subset.

---

## F2 — ENV-FLIP vs FROZEN BINARY (material for the re-enable plan)

### F2.1 — Where the `.env` is parsed, and whether the parse is per-call or boot-time

**Answer: BOOT-TIME ONLY, on two independent paths. There is NO hot-reload, NO SIGHUP handler, NO config-reload endpoint.**

| # | Path | Anchor | Behaviour |
|---|---|---|---|
| 1 | **Launcher** (systemd `ExecStart`) | `/home/nea/agents-ensemble/launcher.sh:185` `load_env_file()`; `:225` `export "$key=$value"`; `:2827` `load_env_file "$INSTALL_DIR/.env"` | Parses `INSTALL_DIR/.env` = `/home/nea/agents-ensemble/.env` **once, in the launcher's main path**, and **exports** each key into the launcher's own environment, which the daemon child **inherits**. Comment at `launcher.sh:35-40` (ADR-014): *"Env precedence: INSTALL_DIR/.env (staged from repo .env.prod by …) … the binary's own .env loader (run_app.py) only sets vars that are still unset"* → the launcher's export wins. |
| 2 | **Daemon binary** | `run_app.py:17-18` (`env_file = os.path.join(app_dir, '.env')`), `:30-31` (`if key not in os.environ: os.environ[key] = value`) | Daemon-side `.env` loader, also **boot-time only**, and it only fills vars **not already set** (so it never overrides the launcher). |
| 3 | **Config object** | `daemon/__main__.py:402` `config = load_config()`; `daemon/api.py:533` `config = load_config()`; `daemon/config.py:7116` `def load_config(...)`, `:7149` `yaml.safe_load(f)`, `:7157` `substitute_env_vars(raw_config)` | The YAML + env substitution → pydantic model happens **once at boot**. `load_config` has a few *ad-hoc* callers later (`daemon/graph.py:6325`, `daemon/services/keyword_extraction.py:354`, `daemon/services/attestation_resolver.py:516`, `daemon/auth/cli.py:141/:264`) — **none of them is a general config hot-reload, and none re-reads the process environment.** |
| 4 | **Hot-reload search** | `git grep -n -E 'SIGHUP\|signal\.signal\|reload_config\|config_reload\|/reload' origin/latest -- daemon/` | **EMPTY** — no SIGHUP handler, no reload endpoint anywhere in `daemon/`. |
| 5 | **The switch itself** | `daemon/services/skill_job_dispatcher.py:104` `raw = os.environ.get(env_name)` (inside `_resolve_switch`) | The switch **is** read per-call — but from the **process's own `os.environ`**, which is fixed at boot. |

**Code-verified conclusion:** the daemon re-evaluates the switch *per call* against `os.environ`; `os.environ` is populated **once at boot** (launcher export + `run_app.py` loader) and is never re-read from disk. `git grep -n -E 'os\.environ\[[^]]+\] *=|os\.environ\.update|os\.putenv' origin/latest -- daemon/` → **EMPTY** — **no tool, route, or code path in the daemon mutates its own `os.environ`**, so there is no in-process lever to flip the switch on a running daemon.

### F2.2 — Does an `.env` edit reach the LIVE daemon without a restart? **NO — a process restart is REQUIRED.**

Plain statement: an `.env` edit changes a **file on disk**. The running daemon's `os.environ` is a per-process copy taken at boot. Nothing re-reads the file (F2.1 items 1-5). Therefore **`ENSEMBLE_SKILL_EVOLUTION_ENABLED=1` written into `/home/nea/agents-ensemble/.env` has NO effect on the running daemon** until a new daemon process starts. This is *code-verified*, not inferred.

**What the restart actually is (operational, verified read-only):**

The live daemon is a **system-scope systemd unit** — *not* a user unit. (`systemctl --user` is unusable here: `XDG_RUNTIME_DIR` is unset → "Failed to connect to bus". The only user unit on disk is `opendesign-daemon.service`.)

`systemctl show ensemble-main.service --no-pager` (read-only):

```
FragmentPath=/etc/systemd/system/ensemble-main.service
ExecStart={ path=/home/nea/agents-ensemble/launcher.sh ; argv[]=/home/nea/agents-ensemble/launcher.sh ; ... pid=513572 }
MainPID=513572
ActiveEnterTimestamp=Mon 2026-11-09 04:32:43 UTC
Environment=                      <-- EMPTY (no Environment=, no EnvironmentFiles=)
Restart=on-failure
User=nea
CanStart=yes
CanStop=yes
```

Consequences, in order of operational weight:

1. **`Environment=` is EMPTY and there is no `EnvironmentFile=`.** The unit contributes *nothing* to the daemon's environment. The whole env comes from the launcher's `load_env_file` export (F2.1 item 1) + systemd's minimal default env. ⇒ **An `.env` edit is honoured only by the next `launcher.sh` start.**
2. **The restart is `systemctl restart ensemble-main.service`** (or `stop`+`start`). `CanStart=yes`/`CanStop=yes` are reported by systemd for this unit; the unit file is root-owned at `/etc/systemd/system/`, so **whether the `nea` user can execute the restart unprivileged (polkit) was NOT tested — testing it is a write action and is out of this lane's bounds.** (The project's L1 host-armor plan describes a "P1 polkit mirror rule (manage-units)"; that plan is the likely source of the permission, but I did not verify it here.)
3. **A restart is already a routine, budgeted ceremony operation.** `scripts/upgrade/promote.sh:587` `if ! restart_via_launcher; then` is the promote path's restart; `scripts/upgrade/lib.sh:158` states the budget envelope: *"preflight 45 / stop 225 / flip 30 / handback 90 / livez 180 / …"*, with `HANDBACK_START_BUDGET_S` at `lib.sh:180` and `promote.sh:598` `_enforce_deadline "$promote_deadline" restart`. ⇒ **A plain env-flip restart is materially cheaper than a full promote** (no release swap, no journal/ceremony), but it is the *same class* of operation the upgrade machinery already automates and gates.
4. **The restart is also implicit:** `Restart=on-failure` ⇒ if the launcher ever exits non-zero, systemd starts a fresh launcher, which re-runs `load_env_file` ⇒ **the `.env` edit would then be picked up automatically**, without anyone asking for it. Operationally that means an `.env` edit is "eventually applied", not "applied now" — the re-enable plan must not assume immediacy.
5. **Cost of a restart, operationally:** the daemon must re-boot (DB preflight → core services). The project's own upgrade blueprint records *"daemon boot to core services takes ~10s"* and *"a promote stop during a DB outage makes graceful DRAIN wait on dead connections (~20 min observed)"*. ⇒ A restart on a healthy DB is a ~10 s-to-low-minutes service window with a livez/readyz re-gate; a restart **during a DB outage is the dangerous case** (~20 min DRAIN wait). Verify DB health before restarting.

### F2.3 — Interim live-acting brakes other than `job_queues.is_paused`

**Verified answer: `job_queues.is_paused` IS the brake, and it is the only one. It is currently OFF — and I now know exactly WHICH queue to pause.**

All 141 skill job rows carried **exactly two** queue ids (queried from `job_queue_items.queue_id`):

| queue_id | queue_name | type | is_system | **is_paused** | updated_at | skill jobs carried |
|---|---|---|---|---|---|---|
| **bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2** | system_parallel_queue | parallel | true | **false** | **2026-10-26T21:50:04.439103+00:00** | 25 `skill_capture` + 114 `skill_evolution` |
| cd02c5d2-02ea-4eae-bba7-6876db4124c5 | system_parallel_queue | parallel | true | **false** | 2026-08-24T18:43:36.498454+00:00 | 7 `skill_evolution` |

⇒ **`bd075f0b-…` is precisely the queue the Oct-22 containment re-paused** (board `wi-7382d739` body: *"system_parallel_queue re-paused"*), and it is the queue named in pinned critical note `ed6da139`. Its `updated_at=2026-10-26T21:50:04` is the row-level proof that **the pause was lifted on 2026-10-26** — i.e. the brake the incident relied on has been off for ~2 weeks.

**Brake inventory:**
- `job_queues.is_paused` on `bd075f0b-…` — the **only** live-acting brake for this lane; currently `false`. **Un-pausing/p pausing it is a DB write (out of scope here), and it is the correct interim brake if a re-enable needs a stop-hammer.**
- The `skill_evolution` queued-execution terminal refusal (board note 87: *"queued-execution terminal refusal (queue-head-safe, mutation-proven)"*) — this refuses *already-queued* rows; it is not a brake on *new* admissions (admission is blocked by the kill-switch).
- No in-process env lever exists (F2.1 item 5) and no reload endpoint exists (F2.1 item 4) ⇒ **there is no way to turn the lane ON without a process restart.**

**Bottom line for the re-enable plan (code-verified + operational):** to turn the lane ON you must (a) edit `.env` or `config.yaml`, **and** (b) restart `ensemble-main.service` — and you can turn it back OFF with the same two steps, or *immediately* with `is_paused=true` on `bd075f0b-…` (no restart needed for the brake, because the pause is read per job, not per boot). **That asymmetry — instant off via queue pause, on only via restart — is the operational shape of the plan.**

---

## F3 — CONFIG KEY ACCEPTANCE on v0.29.7 (is `skill_evolution.evolution_lane_enabled` live or dead?)

### F3.1 — The class definition (origin/latest = live v0.29.7)

`daemon/config.py:5049` `class SkillEvolutionConfig(EmbeddingConfig):`
`daemon/config.py:5052` `model_config = SettingsConfigDict(env_prefix="SKILL_EVOLUTION_")`
`daemon/config.py:5150-5151`:
```python
    evolution_lane_enabled: bool = Field(
        default=False,
        description=(
```
→ **The field name is `evolution_lane_enabled`; there is NO `validation_alias`, NO `alias`, and NO `populate_by_name` on it.** (Every `validation_alias=` in this file belongs to other classes — `PersistenceConfig` at :1181, the `CompactionConfig` family at :1491-1677, `ReportRepairConfig` at :5223 — **not** to `SkillEvolutionConfig`.) The class body of `SkillEvolutionConfig` is **byte-identical** between HEAD `013895287` and `origin/latest` `5da1021f2` (verified by `diff` of the extracted class bodies → "IDENTICAL class body"), so the two trees behave the same here.

### F3.2 — Is the YAML path wired in? **YES — verbatim pass-through.**

`daemon/config.py:7545-7559` (origin/latest):
```python
    if "skill_evolution" in processed_config:
        # Drop keys whose YAML value is ``null`` (None). pydantic-settings
        # treats an explicitly-passed init kwarg — even ``None`` — as taking
        # priority over environment variables, …
        se_raw = processed_config["skill_evolution"]
        config_dict["skill_evolution"] = {
            k: v for k, v in se_raw.items() if v is not None
        }
```
→ The YAML `skill_evolution:` section is copied **key-for-key** (minus `None` values) into `config_dict["skill_evolution"]`, which becomes the init kwargs for the nested `SkillEvolutionConfig`. **A YAML key `evolution_lane_enabled` under `skill_evolution:` is therefore a recognised field name and IS accepted.**

Also relevant: `SkillEvolutionConfig.model_config` resolves to `{'extra': 'forbid', …, 'env_prefix': 'SKILL_EVOLUTION_', …}` (observed at runtime). ⇒ **a MISSPELLED key inside the YAML `skill_evolution:` section would raise at boot** (`extra='forbid'`), i.e. the failure mode of a typo is a boot crash, not a silent no-op.

### F3.3 — Empirical verification (read-only, no daemon start)

Executed in the worktree venv (`/home/nea/ensemble-src/.venv/bin/python`, pydantic 2.12.5 / pydantic-settings 2.13.1), importing `daemon.config` from the worktree — whose `SkillEvolutionConfig` class body is byte-identical to `origin/latest` (F3.1). **No daemon, no DB, no writes.**

| # | Scenario | Result |
|---|---|---|
| 1 | `Config(skill_evolution={"evolution_lane_enabled": True}).skill_evolution.evolution_lane_enabled` | **`True`** — the YAML-style nested dict **is accepted** |
| 2 | env `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED=1` → `SkillEvolutionConfig().evolution_lane_enabled` | **`True`** — the pydantic-prefix alias works |
| 3 | nothing set → `SkillEvolutionConfig().evolution_lane_enabled` | **`False`** — the default |
| 4 | alias env `=0` **and** YAML `=True` together | **`True`** — **the YAML/init-kwarg value BEATS the alias env var** (pydantic init-kwarg priority over env) |
| 5 | env `ENSEMBLE_SKILL_EVOLUTION_ENABLED=1` → pydantic field | **`False`** — the canonical var is **not** bound by pydantic at all (no `ENSEMBLE_` prefix on this model); it is read separately, per call |
| 5b | same env → `is_evolution_lane_enabled(False)` (the per-call resolver) | **`True`** — the canonical var acts **only** through the per-call resolver |

### F3.4 — The runtime wiring (where the value is consumed)

`daemon/manager.py:7646-7659` (origin/latest) — the metric-scan fallback reads the **boot-time config** value and asks the per-call resolver:
```python
                        if job_type == "skill_evolution":
                            _skill_evo_cfg = getattr(getattr(self, "config", None), "skill_evolution", None)
                            _lane_default = bool(getattr(_skill_evo_cfg, "evolution_lane_enabled", False))
                            if not is_evolution_lane_enabled(_lane_default):
```
`daemon/manager.py:7927-7943` — the dispatcher's constructed default:
```python
            _skill_evo_cfg = getattr(self.config, "skill_evolution", None)
            …
            self._skill_job_dispatcher = SkillJobDispatcher(
                job_service=service,
                queue_repo=queue_repo,
                evolution_lane_enabled=bool(
                    getattr(_skill_evo_cfg, "evolution_lane_enabled", False)
                ),
```
`daemon/services/skill_job_dispatcher.py:205-232` `_resolve_evolution_enabled(configured_default)` → `_resolve_switch(env_name, configured_default)` (`:83-117`), whose order is: **canonical env set → wins; unset/empty → `configured_default`; recognised OFF token → False; recognised ON token → True; anything else → `ValueError` (fail-closed).**

### F3.5 — **PLAIN ANSWER: precedence, and is (iii) live or dead?**

**On v0.29.7, ALL THREE surfaces work. None is a dead key. The precedence, highest first:**

1. **`(i) ENSEMBLE_SKILL_EVOLUTION_ENABLED`** (canonical) — **highest**. Read per-call from `os.environ`; when set to a valid token it **wins over everything**, including a YAML `true`. It is the only surface that can flip the lane **without a config change** — but see F2: it still needs a process restart to reach the running daemon's `os.environ`.
2. **`(iii) YAML `skill_evolution.evolution_lane_enabled`** — **LIVE, not dead.** Verified by code (`config.py:7545-7559` pass-through + `:5150-5151` field name) **and empirically** (F3.3 #1 → `True`). It sets the dispatcher's `configured_default` (F3.4), so it governs whenever the canonical env is unset. **Caveat: it beats the alias env var** (F3.3 #4).
3. **`(ii) SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED`** (pydantic alias) — **LIVE but weakest**: it is the `env_prefix`-derived spelling of the same field, consulted only when neither the canonical env nor the YAML key supplies a value. Note the disclosed residual in the code's own docstring (`config.py:5141-5142`): *"validation_alias pinning breaks the by-name yaml/test/wiring surface"* — i.e. the alias was deliberately left in place rather than eliminated.

**Therefore: board note 89's recommendation (`skill_evolution.evolution_lane_enabled=true`) is NOT a trap on v0.29.7** — the key is accepted and live. **But it is not sufficient by itself:** it changes the *configured default*, and it only reaches the running daemon at the **next `load_config()`** (i.e. the next daemon boot) — F2. **The canonical env var (i) is the better lever for a re-enable plan, because it is per-call and is the documented kill-switch surface; but it too requires the restart in F2.** Whichever surface is chosen, the plan must include a restart of `ensemble-main.service`.

**Un-pinning note 89's wording:** note 89 says *"enable deliberately via skill_evolution.evolution_lane_enabled=true"*. That statement is **correct but incomplete**: it omits (a) that the restart is required for the live daemon to read it, and (b) that the canonical env var overrides it when set. A re-enable plan built on note 89's sentence alone would (i) edit a file the running daemon has already read, and (ii) be silently overridden if anyone had set `ENSEMBLE_SKILL_EVOLUTION_ENABLED=0`.

**Capture side, for completeness:** `ENSEMBLE_SKILL_CAPTURE_ENABLED` has **no** config-field counterpart — `SkillEvolutionConfig` has no `capture_enabled` field (full field list enumerated earlier: 26 fields, none capture-related beyond `capture_min_iterations` / `capture_min_duration_seconds`, which are thresholds, not switches). So for the capture lane the **canonical env var is the ONLY switch surface**, and `_resolve_capture_enabled()` defaults to **OFF** when it is unset (`skill_job_dispatcher.py:150-173`). ⇒ **Re-enabling capture requires setting `ENSEMBLE_SKILL_CAPTURE_ENABLED=1` AND restarting** — there is no YAML path for it at all.

---

## F4 — Open-question status after this follow-up

| Original OQ | Status now |
|---|---|
| OQ-3 (env flip vs frozen binary) | **RESOLVED** — F2: `.env` is read at boot only (launcher `:2827` → export `:225`; `run_app.py:17-31`), no SIGHUP/reload endpoint, no in-process `os.environ` mutation ⇒ **a restart of `ensemble-main.service` is REQUIRED**; `Restart=on-failure` makes it implicit on the next crash; restart is budgeted/ceremony-sanctioned (`promote.sh:587` `restart_via_launcher`, `lib.sh:158`). |
| OQ-8 (does v0.29.7 accept `skill_evolution.evolution_lane_enabled`?) | **RESOLVED** — F3: **the key is LIVE, not dead** (code + empirical). Precedence: canonical env > YAML key > alias env > default False. It still needs a restart to reach the running daemon. |
| OQ-4 (demo env skill history) | RESOLVED in the prior appendix (demo has 0 skill jobs). |

**New open questions raised by this follow-up:**

**OQ-9 — Can the `nea` user restart `ensemble-main.service` without a password?** `systemctl show` reports `CanStart=yes`/`CanStop=yes` for the unit and `User=nea`, but the unit file is root-owned at `/etc/systemd/system/ensemble-main.service` and no `EnvironmentFile=`/`Environment=` is set. **I did not test the actual restart** (a restart is a write action, forbidden in this lane). → **The re-enable plan must confirm the restart path's privilege model (polkit rule vs. sudo) before proposing it.** The project's L1 host-armor plan (`docs/runbooks/install-dir-protection-layer1-arm.md`, "P1 polkit mirror rules (manage-units …)") is the likely but **unverified** source of permission.

**OQ-10 — Does a restart re-read `config.yaml` from the release dir, and which one?** `load_config` resolves `os.environ.get("ENSEMBLE_CONFIG", "./config.yaml")` (`config.py:7136`) — i.e. **relative to the daemon's CWD**, and the launcher execs `$INSTALL_DIR/current/ensemble-prod` (`launcher.sh:2583`, ADR-004). ⇒ **A YAML edit to `skill_evolution.evolution_lane_enabled` must target the file the daemon actually opens at boot** (`$INSTALL_DIR/current/config.yaml`, which resolves through the `current` symlink to `releases/v0.29.7/config.yaml` — the same file I inspected), not the repo checkout's `config.yaml`. **I did not verify the daemon's actual CWD at runtime** (`/proc/513846/cwd` was not read) ⇒ **UNVERIFIED whether the effective path is `$INSTALL_DIR/current/config.yaml` or `$INSTALL_DIR/releases/v0.29.7/config.yaml`** — they are the same inode only while the symlink points at v0.29.7.

**OQ-11 — Is there a third, non-queue brake: the `skill_evolution` queued-execution refusal semantics on v0.29.7?** Board note 87 (Oct-23) describes a *"queued-execution terminal refusal"* for rows already queued. I verified the **admission** path is a silent no-op (F2/A5) but did **not** read the queued-execution refusal code at origin/latest. → **UNVERIFIED whether, on v0.29.7, an already-queued `skill_evolution` row is terminally refused (a visible row) when the lane is OFF** — which matters if the re-enable plan expects queued rows to surface rather than vanish.

**OQ-12 — Live `.env` write discipline for the switch.** `launcher.sh:225` `export "$key=$value"` parses `KEY=VALUE` lines with **no eval**; the switch token sets are `1/true/yes/on` vs `0/false/no/off` (`skill_job_dispatcher.py:79-80`), and an **invalid value raises `ValueError` at first use** (`:114-117`) — i.e. a typo in the `.env` would not merely fail to enable, it would **raise inside the dispatcher on first use** (fail-closed). → **The re-enable plan must use an exact token.** *This is code-verified; the operational consequence of the raise inside a live job path was not exercised.*

---

### Appendix compliance (this follow-up)

- **Read-only throughout.** DB: `SELECT` / `information_schema` only. Daemon: only `systemctl show`/`list-units`/`cat` (read-only), `/proc/<pid>/environ` (read). No service started/stopped/restarted, no job dispatched, no queue mutated, no `.env` written, no `git checkout/reset/stash/clean`.
- **The one executable step** was an isolated Python snippet in the worktree venv that imported `daemon.config` and constructed config objects in memory. It started **no** daemon, opened **no** DB connection, and wrote **no** file (only `/tmp/orig_se.txt` + `/tmp/head_se.txt`, two class-body extracts used for the HEAD-vs-origin/latest identity diff).
- **No secrets reproduced.** The embedding API key value remains redacted (presence only).

---

# NUMERIC RECONCILIATION APPENDIX (2026-11-09, measured 06:11:47Z–06:14:55Z)

Triggered by the composed-report review (phase0-review.md B1/B2). **Read-only reconciliation — the DB was not touched; no numbers were "adjusted".** Every figure below comes from single-statement snapshots (one SQL statement ⇒ one consistent snapshot for all its fields), each stamped with `clock_timestamp()`.

## R0 — Method and snapshot registry

| Snapshot | DB `clock_timestamp()` | total usage rows | taken by |
|---|---|---:|---|
| S1 (original B1 census) | ~05:14Z (statement unstamped; bounded by the 05:13:42Z connectivity check) | 10,056 | Lane 1 |
| S2 (original B11 matrix) | ~05:16Z | 10,057 (sum of cells) | Lane 1 |
| S3 (original drift statement) | ~05:19Z | selected_true = 10,059 | Lane 1 |
| S4 (Lane 3 H5) | ~05:18–05:29Z (file mtime 05:29; quoted total 10,057 ⇒ ≈ S2's moment) | 10,057 (quoted) | Lane 3 |
| **S5 (reconciliation A)** | **2026-11-09 06:11:47.362771+00** | **10,072** | Lane 1, this appendix |
| **S6 (reconciliation B)** | **2026-11-09 06:14:01.934204+00** | **10,072** | Lane 1, this appendix |
| S7 (reconciliation C) | 2026-11-09 06:14:55.146002+00 | — (instance cuts) | Lane 1, this appendix |

Nov-4-2024 census figures cited below are from the pre-existing artifact `.agents/tester/RESULTS/2026-11-04-skill-metrics-census.md` (against live v0.28.11, window 15:18–15:22Z on 2026-11-04).

---

## R1 — CONFLICT 1: the drift arithmetic

### R1.a — What each number counts (exact definitions)

**`6,536` (and now `6,547`) — a COUNTER SUM over the skills registry:**
```sql
select sum(total_applied) from skills;
```
= the sum of the maintained per-skill counter column `skills.total_applied` (628 skill rows at S1/S5 — unchanged between them). This is **not** an event count; it is what the increment path in `skill_metrics_service.py` has added up, per skill, over all time.

**`5,057` — MY TRANSCRIPTION ERROR. The SQL returned `6,057`.** The statement was:
```sql
select 'sum(skills.total_applied)='||(select sum(total_applied) from skills)
    ||'  actual_applied_rows='||(select count(*) from skill_usage_records where applied is true) …
```
and its output line was `sum(skills.total_applied)=6536  actual_applied_rows=6057  sum(total_selections)=10055  actual_selected_rows=10059`. My findings file (headline A10 and B11 table) then wrote the second figure as **5,057** — dropping the leading 6. **Mis-transcription at source, in Lane 1.** The correct figure at S3 is **6,057**; at S5/S6 it is **6,067**.

`6,057`/`6,067` is the **count of event rows** with `applied is true` in `skill_usage_records` — the ledger, not a counter.

### R1.b — The TRUE counter drift, both measurement points, with the deriving query

One statement, one snapshot (this is the exact query to re-run; it prints both endpoints and their difference):

```sql
select clock_timestamp() as measured_at,
       (select sum(total_applied) from skills)          as sum_counter,
       count(*) filter (where applied is true)          as ledger_applied_rows,
       (select sum(total_applied) from skills)
         - count(*) filter (where applied is true)      as counter_drift
from skill_usage_records;
```

| Moment | Σ `skills.total_applied` (counter) | `applied is true` rows (ledger) | **drift** |
|---|---:|---:|---:|
| 2026-11-04 15:18–15:22Z (Nov-4 census) | 6,069 | 5,646 | **+423** |
| 2026-11-09 ~05:19Z (my original S3) | 6,536 | **6,057** | **+479** |
| 2026-11-09 06:11:47.362771+00 (S5) | 6,547 | 6,067 | **+480** |
| 2026-11-09 06:14:01.934204+00 (S6) | 6,547 | 6,067 | **+480** |

**Verdict: `+479` is CORRECT** (it was always `6,536 − 6,057`); the printed `5,057` was the typo. **`+1,479` is arithmetic performed on my typo** and corresponds to no real measurement. The drift genuinely grew **+423 → +479/+480** over the five days (drift Δ = +56/+57). The composed report's trend cell `actual applied usage rows | 5,646 | 5,057 | −589` is wrong twice: the true trend is `5,646 → 6,067 = +421` (or `→ 6,057 = +411` at S3).

**For the Phase-3 exit criterion:** restate the baseline as *"counter drift ≤ **+480** (2026-11-09T06:14Z reconciled measurement; +479 at the original 05:19Z measurement)"*, with the R1.b query as the re-run instrument. The criterion's arithmetic now closes at both endpoints.

### R1.c — Do the two numbers measure different things?

**They measure different *artifacts* (counter column vs event ledger) — and that is exactly why subtracting them is legitimate.** `skills.total_applied` is a denormalized counter; `applied is true` rows are the source-of-truth events. **Counter-vs-ledger drift is a standard integrity measure and the subtraction IS the correct presentation.** The defect in my B11 was purely the transcribed comparator (5,057 for 6,057), not the method. (For completeness: `Σ skills.total_selections` = 10,068 vs `selected is true` rows = 10,072 at S5 ⇒ selection drift **−4**, same method, also closes.)

### R1.d — The `48/628` vs `43/529` denominators

**Both denominators are SKILLS REGISTRY ROWS — not usage rows.**

```sql
-- denominator (both ends):
select count(*) from skills;                        -- 628 @ S1/S5 (2026-11-09); 529 @ Nov-4 census
-- numerator (skill-level mismatch, same method both ends):
select count(*) from (
  select s.id, s.total_applied, count(u.id) filter (where u.applied is true) as actual
  from skills s left join skill_usage_records u on u.skill_id = s.id
  group by s.id, s.total_applied
  having s.total_applied <> count(u.id) filter (where u.applied is true)
) t;                                                 -- 48 @ S1 AND re-confirmed 48 @ S5/S6
```

So the trend is **43 of 529 skills (Nov 4) → 48 of 628 skills (Nov 9)** — a skills-denominated mismatch census on both ends. Note the numerator is snapshot-sensitive in BOTH directions (a skill's counter and its rows can re-sync as rows arrive); at S5 it was unchanged at 48 while the row total grew 10,057→10,072.

---

## R2 — CONFLICT 2: the 6,052 population

### R2.a — Every disputed number under BOTH definitions, same snapshots

Single-statement census (S5, 2026-11-09 06:11:47.362771+00; S6 identical):

```sql
select clock_timestamp(),
       count(*)                                                        as total_rows,
       count(*) filter (where applied is true)                         as applied_true,
       count(*) filter (where feedback_applied is true)                as feedback_true,
       count(*) filter (where feedback_applied is null)                as feedback_null,
       count(*) filter (where applied and feedback_applied)            as cell_TT,      -- Lane 1's def
       count(*) filter (where selected and task_succeeded
                        and feedback_applied is null)                  as lane3_def     -- Lane 3's def
from skill_usage_records;
```

Results (S5; S6 (06:14:01Z) byte-identical on every field):

| Population | Definition | S2/S4 window (~05:16–05:29Z) | S5/S6 (06:11–06:14Z) |
|---|---|---:|---:|
| **`6,052`** | **`applied=true AND feedback_applied=true`** (Lane 1 B11 cell TT; ≡ `feedback_true` rows) | **6,052** | **6,063** |
| Lane 3's stated def | `selected=true AND task_succeeded=true AND feedback_applied IS NULL` | **≈3,44x** (not directly measured then; bounded by `feedback_null`=3,539 minus false/null-not-succeeded) | **3,450** |
| `feedback_applied=true` | raw flag | ≈6,05x | **6,063** |
| `applied=true` | raw flag | 6,056 (S2) / 6,057 (S3) | **6,067** |
| total rows | `count(*)` | 10,057 (S2/S4) | **10,072** |
| `selected=true` | raw flag | 10,059 (S3) | **10,072 (100.0%)** |
| `task_succeeded=true` | raw flag | — | 9,280 (false=792, null=0) |

Full cross-cell matrix at S5 (rows sum exactly to 10,072): `TT=6,063 · TF=1 · TNULL=3 · FT=0 · FF=469 · FNULL=3,536` — confirming my original B11 matrix's *shape* (at S2: 6,052/1/3/0/469/3,532 summing to 10,057).

### R2.b — Canonical definition for the re-enable plan's monitoring

The capture gate at issue (Lane 3 H5, `has_applied_for_instance` [origin/latest] `daemon/repositories/skill/repository.py:1550-1573`) evaluates **per instance**, not per row. **Canonical metric = INSTANCE-level** (this is the number that measures gate-4 misclassification):

```sql
-- CANONICAL: instances the attribution gate misreads as "no skill applied"
--            (has_applied_for_instance == False) despite a SUCCEEDED task.
select clock_timestamp(),
       (select count(distinct instance_id) from skill_usage_records where task_succeeded is true)
     - (select count(distinct instance_id) from skill_usage_records
         where task_succeeded is true and feedback_applied is true)
       as false_eligible_instances;
```
**= 3,782 @ 2026-11-09T06:14:55Z** (components: 9,207 succeeded instances total; 5,425 of them have ≥1 `feedback_applied=true` row).

Supporting row-level census (continuity with Lane 3's published H5 wording — keep for trend comparison, do NOT use as the gate metric):

```sql
select count(*) from skill_usage_records
 where selected is true and task_succeeded is true and feedback_applied is null;
```
**= 3,450 @ 06:11–06:14Z.** All-instances variant (no task_succeeded filter): 9,724 instances with usage rows − 5,824 with a feedback-true row = **3,900** gate-false instances.

**Scope caveat the plan must carry:** these figures measure *misclassification by gate 4*, not captures that would fire — the other eligibility gates still apply (blank-`task_message` P0 refusal, `iterations > 5`, `duration > 60s`, kill-switch; per Lane 3 §6.3, `task_message` is NULL on 100% of rows and `iterations=0` on 10,058/10,059, so **3,782 is an upper bound on gate-4 misclassification, not a capture forecast**).

### R2.c — Different populations or different moments? **BOTH — and here is the evidence for each.**

**(i) The dominant cause is genuinely different populations, provable at a single instant:** at S5 (one statement, one snapshot) `cell_TT = 6,063` and `lane3_def = 3,450` **simultaneously** — a 2,613-row gap inside one consistent snapshot. No timing explanation can produce that.

**(ii) Census-window skew is real but bounded — evidenced, not asserted:**
- Within my own original sequence: total rows **10,056 (S1, ~05:14Z) → 10,057 (S2, ~05:16Z) → selected_true 10,059 (S3, ~05:19Z) → 10,072 (S5, 06:11Z)** ⇒ ≈ **+16 rows / 57 min** of live insertion; and **zero** growth S5→S6 (06:11:47→06:14:01, every field identical).
- Lane 3's quoted total (10,057) equals S2's — their window was within minutes of mine.
- Same-population cross-checks close exactly under this growth rate: matrix applied_true 6,056 (S2) vs drift-statement applied_true 6,057 (S3) — **one row apart, same population**; `feedback_true` 6,063 vs `applied_true` 6,067 at S5 — delta 4 = `TNULL(3)+TF(1)`.
- Therefore cross-lane minutes-apart windows shift any of these counts by **single digits**, never by 2,600.

---

## R3 — Verdict table (what is correct, what was mis-transcribed, what is genuinely different)

| Figure | Where it appeared | Verdict |
|---|---|---|
| **+479 drift** (6,536−6,057) | Lane 1 A10/B11; report §5.7/§6.2; Phase-3 exit criterion | **CORRECT** at its measurement moment (S3, ~05:19Z). Reconciled live value **+480** (S5/S6). |
| **5,057 "actual applied rows"** | Lane 1 A10/B11 (root); report §5.7 | **MIS-TRANSCRIBED in Lane 1** — the SQL returned **6,057**. This single typo is the origin of review-defect B1 (the 1,479/−589 phantom arithmetic). |
| **−589 / +467 trend cells** | report §5.7 table only | **WRONG derived figures** in the composed report (built on the typo). True: applied rows 5,646→6,067 (**+421**); counter 6,069→6,547 (**+478**); drift +423→+480 (**+57**). |
| **6,052** | Lane 1 B11 cell `applied=T/feedback=T`; Lane 3 H5 (labeled "selected & succeeded & feedback NULL") | **The number is Lane 1's TT cell** (now 6,063). **Lane 3's label is the error**: under Lane 3's stated definition the count is **3,450** (S5/S6). The two lanes' 6,052s are the **same population** — review-defect B2 dissolves once Lane 3's label is corrected. |
| **3,446 "feedback_applied true"** | Lane 3 L184 only (grep: absent from Lane 1) | **MIS-PAIRED at source in Lane 3** with its own query's other output: `feedback_applied=true` was ≈6,05x at their moment and is **6,063** now; **3,44x is what Lane 3's own `selected&succeeded&fb_null` filter actually returned** (3,450 at S5). Independent impossibility proof: `feedback_true` was already **5,642** on Nov-4 (prior census) and the flag only ever stamps NULL→true — it cannot have fallen to 3,446. |
| **6,057 / 6,067** | Lane 1 S3 / S5 | Same population (`applied is true` rows) at two moments; +10 rows of live growth. |
| **10,056 / 10,057 / 10,059** | Lane 1 S1/S2/S3; Lane 3 quoted 10,057 | Same denominator (total usage rows) at successive moments; evidenced growth chain in R2.c(ii). Not a population difference. |
| **43/529 vs 48/628** | Nov-4 census vs Lane 1 B11 | **SKILLS rows on both ends** (`count(*) from skills`); numerator = skill-level `total_applied` mismatch (R1.d query). Genuinely comparable. |

**Net corrections owed by each lane:** Lane 1 — one transcription (5,057→6,057; two spots: headline A10, B11 table). Lane 3 — one label mis-pairing (swap "6,052" and "3,446" between the two populations in L184). The composed report — replace the §5.7 trend cells and restate the Phase-3 criterion per R1.b; disclose definitions rather than merging per review B1/B2.

---

### Appendix compliance (this reconciliation)

- **Read-only.** All DB access was `SELECT` with `clock_timestamp()` stamping; one statement per snapshot so all fields share one consistent view. **No writes, no DDL, no daemon contact, no `.env`/config access.**
- Cross-lane files (`lane3-flakiness-evidence.md`, `phase0-review.md`, `phase0-investigation.md`) were **read only**, to locate the origin of each disputed figure; nothing in them was modified.
- The `sleep 75` before S6 was to bound the growth rate empirically (it measured zero growth in 2m14s — consistent with the ≈16 rows/hour chain).
