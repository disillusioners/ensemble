# Maintenance Report: Rogue-write forensics — wi-2454ede3 / rogue-containment component C
Date: 2026-11-12T01:20Z (forensics window: 2026-11-11). Lane: strictly read-only (ens_db_postgres_select + ens_db_inspect only; no act tools, no repair, no anomaly filing). Instance IDs (rogue-touched): e64b78d5 bde0ae1c a16dc01c 2c5407bf ce052e67 faa48ae9 1b71602a 4b58b22c f74383d0.

## Status
Escalated to leader — investigation complete; remediation decision routed Emperor-gated per commission terms. Recommendation: LEAVE-CLEAN, with 2 optional gated instance-ops.

## Root cause (context, already attributed in wi-2454ede3)
Worker e64b78d5 (gate3-e2e-final) ran `pkill -f "ensemble-prod"` (bash_spawn_registry row 96028, DB-exact 2026-11-11T09:15:05.923667Z); the same worker booted staged v0.29.17 which — via the env-less boot + data/ensemble.json "database":"postgres" pin — attached to live ensemble_prod and bound ports 9797+9798 until systemd killed the orphans at 11:54:52Z; next real boot bound 11:55:29Z.

## 1. Window (pinned + cross-checked)
- pkill: 2026-11-11T09:15:05.923667Z (bash_spawn_registry 96028 == incident record)
- Rogue first DB write (boot_id e2020838…, pid 3546348): 09:15:12.575652Z (row 96030)
- Dying live daemon (pid 2390250) last write: ~09:16:51Z (bash 96045, checkpoints to 09:16:51)
- Rogue last DB activity: 11:42:21Z (interrupted maintenance run end); last task fail 11:15:00.001
- Rogue death / recovery bind: 11:54:52Z / 11:55:29Z (corroborated by recovery-era DB writes ≥11:55)
- Duration (port capture): 09:15:12→11:54:52 = 2h39m40s (recorded "2h38m" ✓)
Phase model: drain [09:15:00–09:17:00) = dying live + early rogue; rogue-solo [09:17:00–11:55:00) = only the rogue serves; recovery [11:55–12:30) = live daemon back.

## 2. Per-surface write inventory (rogue window [09:15:00–11:55:00))
LangGraph trio (time anchor: checkpoints.checkpoint->>'ts'; checkpoint ids NOT wall-clock decodable — uuid-nibble layout ≠ RFC v6 epoch math, verified garbage before pivot):
- e64b78d5 (worker/pkill worker): 47 ckpts (drain) + 120 writes (drain), 09:15:01.96→09:16:51.08, max step 173
- ce052e67 (leader session): 32 ckpts (21 drain / 11 rogue-solo) + 89 writes (56/33), 09:15:03.57→10:21:33.42, max step 201
- bde0ae1c (tester-phase3): 30 ckpts (rogue-solo) + 78 writes, 10:08:04.19→10:24:11.90, max step 257
- a16dc01c: 22 ckpts (rogue-solo) + 64 writes, 10:15:12.07→10:24:35.16, max step 190
- faa48ae9 (giter): 13 ckpts (drain) + 31 writes (drain), 09:15:16.71→09:16:20.27, max step 99
- 1b71602a (coder): 3 ckpts (drain) + 4 writes (drain), 09:16:30.68→09:16:32.77, max step 755
- 2c5407bf (leader/gate3 commissioner): 3 ckpts (rogue-solo) + 13 writes, 11:12:43.85→11:14:59.46, max step 166
- TOTAL: 150 ckpts (84/66) + 399 writes (211/188)
- checkpoint_blobs: upsert surface (no per-row clock) — 1,315 rows across these 7 threads (512/196/147/140/129/116/75); in-window mutations bounded by the 399 writes, not separately countable. NO orphan threads created — rogue-created instances have zero checkpoints.
Instance rows / hierarchy: created in-window: 4b58b22c (wanderer, parent ce052e67, 09:16:12, drain-era most likely) and f74383d0 (worker, parent bde0ae1c, 10:13:13, rogue). Mutated: e64b78d5→terminated 10:13:03; 1b71602a→terminated 10:21:32; bde0ae1c→updated 10:07:51, left status='running' (phantom); faa48ae9→updated 09:15:11 (drain revive, left 'running' phantom). instance_hierarchy: 2 rows (the two creations).
Work/task/message surfaces: task 3 rows — 34308 (bde0ae1c, 10:07:48→10:24:12), 34309 (a16dc01c, 10:14:51→10:24:35), 34311 (2c5407bf process_report, 10:25:36→11:15:00) — ALL failed: "Resume failed: DependencyBus is None during resume finalize". message_queue 3 rows (2 watchdog hang-notices 10:07:48/10:14:51, 1 tester completion-report 10:25:36; all terminal-failed). event 7 rows (59255–59261: message_received ×4, instance_lifecycle terminated ×2, midflight_report ×1). message_metadata 62 rows (lc_run--… bookkeeping; per-thread: e64b78d5 15, bde0ae1c 11, ce052e67 11, a16dc01c 8, 1b71602a 6, 2c5407bf 6, faa48ae9 5).
KV/metadata: shared_context_metadata 0 rows — the feared KV poisoning did NOT occur (no wt.claim/census writes). instance_mappings 0. project_metadata_records 0.
Schedule/job: schedule_executions 0 (nothing fired, nothing armed). job_queue_items/job_locks/job_watchers/dead_letter_items 0 in window (6/1/1/0 land in recovery, legit).
Metrics/telemetry: resource_daemon_bucket 1 row (id 117615, hourly rollup bucket_ts 11:00:00, rss 488,684 kB, 53 threads — rogue's sampler effectively down). resource_instance_sample 0. resource_pid_map.spawned_at 0.
Other non-zero: bash_spawn_registry 18 (15 under dying-live pid 2390250 incl. the pkill itself; 3 under rogue boot_id e2020838…: 96030 pkill 09:15:12.575, 96034 pgrep 09:15:17.671, 96035 kill 09:15:22.801 — the rogue resumed the pkill-worker's turn and executed host-level process commands); maintenance_runs 10 (see §4); mission_records 2 (56b07bce revive faa48ae9 @09:15:11 drain; 40686cf7 boot adopt bde0ae1c @10:07:51 rogue; both still open); project_history 2 (milestones @10:19:05 by ce052e67 and @11:10:32 by 2c5407bf — content-legit leader output, executed via rogue-resumed turns; the ce052e67 one ran with no daemon task row); project_blueprint_pending_updates 2 (index-queue rows for those milestones, status 'available', unclaimed); report_injections 1 (6b867cbf, tester→2c5407bf report, TASK_DELIVERED 10:25:37).
Verified zero (rogue window): processed_external_messages (no user/external traffic consumed), sources, skills/skill_usage/skill_ab_tests/skill_lineage, snapshots, board, critical_notes, projects, prompts (versions/audit/baselines), anomalies, secrets_audit, live_view_passcode_audit, drift, notify_audit, dependency_watchers, memoir_* (all), repair_log, instance_ops_audit, leases, stored_watches, wedge_captures, auth_*/app_settings/mcp/infra (empty).

## 3. Rogue vs legitimate split
- Rogue-certain (rogue-solo phase + boot_id-attributed): checkpoints 66 + writes 188 (4 real threads), tasks 3, message_queue 3, events 7, instances 1 created + 2 terminated + 1 left phantom-running, hierarchy 1, missions 1, project_history 2, blueprint-pending 2, report_injection 1, bash_spawn 3, resource bucket 1, message_metadata ~30.
- Dying-live drain (incident collateral, legitimate writer): checkpoints 84 + writes 211, bash_spawn 15, instances (faa48ae9 phantom, 4b58b22c creation — attribution most-consistent with drain), hierarchy 1, mission 1, task 34306 failure, message_metadata ~26.
- No third writer found; remote instances did not write this DB in-window.

## 4. Failed-boot residue (63 Errno-98 boots)
Only surface: maintenance_runs — 10 rows, all checkpoint-cleanup/auto, run_ids ckpt-20261111_1117… → …114034…, starts 11:17:47→11:40:34, all status=interrupted ("Run interrupted by daemon restart") — each respawn started the due sweep then died at bind. No instance/task/event/message/lease rows from failed boots. FOLLOW-UP: no completed checkpoint-cleanup run since 2026-11-10 (0 runs post-recovery 11-11, 0 on 11-12 through 01:0x) — retention sweep backlog; 168h TTL puts first expiry risk ~2026-11-17; monitor next due cycle. Deletion check: pre-kill tasks 34304/34306 still present (weak-positive the rogue's boot-recovery discard pruned nothing); post-hoc deletion-proof from live tables impossible — archives needed for certainty.

## 5. Recommendation — LEAVE-CLEAN
Every rogue row is (a) incident evidence, (b) content-legit output the system would have written anyway, or (c) benign bookkeeping. No destructive remediation warranted. Risk per surface: checkpoints — all on real threads, resume evidence, TTL-governed (deleting would corrupt parent chains) → leave. KV — nothing written. Schedules — nothing armed/fired. message_queue — terminal-failed, no retry loop. missions — ledgers, non-firing. blueprint-pending — let the indexer consume naturally. maintenance_runs — incident evidence.
Two optional gated ops (ordinary audited instance_ops, NOT DB repair; run only on Emperor/lead decision):
1. Phantom-running rows (stale >26h, wedge-noise + parent-wait risk): instance_terminate(id='bde0ae1c-…', dry_run=true) → review cascade → confirm; same for faa48ae9-… (its stall began in drain). Non-destructive alternative first: instance_nudge revive-once to let each finalize its own state.
2. Never-dispatched husks 4b58b22c / f74383d0 (idle, no checkpoints, no activity): instance_terminate dry-run → apply; zero firing risk either way.
No ens_db_repair_execute call is justified by any finding.

## Verification
All counts via SELECT-only readers on ensemble_prod (identity-verified: current_database()=ensemble_prod, PG 18.4); 3-bucket sweep (pre/rogue/recovery) across every timestamped column in the schema; checkpoint times via checkpoint->>'ts' after the uuid-decode pivot (garbage years 2952–3180 proved the decode invalid — pivot documented). Cross-checks: pkill timestamp DB==record; recovery-bind corroborated by recovery-era writes; 63-boot count from incident record, partially corroborated (10 interrupted runs on the restart cadence). Limitation: external RO re-read refused (InsufficientPrivilegeError, ensemble_prod_ro lacks bash_spawn_registry grant) — disclosed, primary path self-consistent.

## Remaining (routed by leader)
- Defect signals routed: (1) "DependencyBus is None during resume finalize" — v0.29.17 boot-recovery resumes turns with incomplete service wiring, 3/3 failures, yet partial side-effects still commit (leader filed anomaly 78fc79b5); (2) rogue-executed turn (ce052e67, 10:19:05 milestone) ran with no daemon task row — observability gap; (3) checkpoint-cleanup sweep backlog since 2026-11-10.
- KB: method captured as POK rogue-01-write-quant-method.
