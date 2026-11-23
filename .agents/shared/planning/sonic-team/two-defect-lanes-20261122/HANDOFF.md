# HANDOFF — two-defect-lanes-20261122 (Leg A sentinel boot-warm + Leg B L-11 event-enum retirement)

Commission: sonic-developer, Emperor green-lit both legs (Discord 2026-11-22). Route STANDARD.
Base origin/latest 5f0eafa83 (v0.31.36). Integration: merge/v03137 @ f30d0d917 (bump tip) == tag v0.31.37 (annotated 018607204) == origin/latest. Staged: /home/nea/agents-ensemble/releases/v0.31.37 (rollback_safe=true source=explicit; binary_sha256 a42185ff38e359aa…, 4-way verified release==dist==manifest==sidecar; scripts pin advanced 5f0eafa83→f30d0d917). NO live promote — Emperor ceremony only (live was promoted to v0.31.35 by the leader's own ceremony r-20261122-233929-69fa mid-commission, outside this lane).

## Files touched across modules

Leg A — sonic/readiness-sentinel-boot (commits bb9e893c2, 862b179fc; merge 721e2faf6):
- daemon/services/readiness.py — boot-warm tolerance branch (window helper :159; tolerance :1724-1760), class token in degraded reason strings, per-tick sentinel latency DEBUG log; probe budget and hot-path design UNCHANGED
- daemon/constants.py — READINESS_BOOT_WARM_WINDOW_S = 120.0 (:191)
- tests/test_readiness_checkpoint_saver.py — 6 pre-existing stale-assertion reds repaired (verified at base: 6 failed / 14 passed)
- tests/unit/services/test_readiness_boot_window.py — NEW, 16 pins

Leg B — sonic/event-enum-retire (commits 2275192cf, f72639d9e; merge 5de22dc22):
- daemon/repositories/event/models.py — 6 enum members removed; L-11 annotations; no-reader-parses note
- daemon/services/event_registry/catalog.py — RETIRED_EVENT_KINDS ledger (6 rows: reason/retired_at/revive_procedure); BRIDGE_PENDING/DECLARED_SILENT_KINDS emptied (ZERO_PRODUCER_KINDS alias follows)
- daemon/services/event_registry/__init__.py — export
- tests/unit/services/event_registry/{test_catalog.py,test_bridges.py,test_emit.py} — census 24=23/0/1 + ledger completeness + retired-kind refused pins (78 nodes)
- Lockstep test re-points ONLY (no behavior change): tests/message_queue_redesign/{test_event_bus.py,test_event_repository.py}, tests/postgres/test_smoke.py, tests/unit/repositories/test_room_models_and_migration.py
- .agents/tester/PACKS.md — event_registry_unit_test row re-baselined 69→78 nodes

Release fileset: CHANGELOG.md, pyproject.toml, daemon/__init__.py, tests/test_api.py, uv.lock (scripts/bump_version.py, author giter, house format).

## What changed

Leg A (root cause: readiness.py:2034 inner-budget expiry under saver-lock contention → :2046 returns (False, pool_busy) → :1539-1546 orchestrator degraded a grace-eligible class → :1247-1255 reason carried no class): inside a 120.0s window from the daemon boot epoch, grace-eligible checkpoint_saver classes (pool_busy/read_timeout) no longer degrade /readyz — component stays True with OBSERVABLE marker (detail.checkpoint_saver_boot_warm + WARNING). Fail-closed both ways: hard_component_red and OUTER probe_timeout degrade IN-window; everything degrades post-window; missing epoch ⇒ no window ⇒ degrade. GraceTracker semantics untouched; api.py untouched (engine-pilot fence). Degraded reasons now carry " (class=…)". Subsumes the never-landed 2026-10-30 "checkpoint-amplification" lane (zero commits in any ref — re-verified).

Leg B: six zero-producer EventKind members retired with deprecation ledger; census 30=23/6/1 → 24=23/0/1; deferred turn_transition + 23 bridged kinds untouched; tripwire pins make silent revival RED. Dispositions (all RETIRE): processing_started/completed/failed + message_completed (April-2026 legacy remnants, no emission site), child_failed (error_reporting's same-name is an SSE stream event_type, not an event row), room_post (superseded — room posts ride room_tag_delivery/room_proposal/room_decision rows; no row producer).

## What is UNTESTED

- Leg A: the NATURAL live-restore-storm transient was not re-observed end-to-end — the contention was reproduced by a lock-hold harness in a throwaway extract (natural dev boot: 18 ticks, min 0.007s / median 0.009s / max 0.111s at first tick, ZERO contended ticks). The live 2026-11-22 storm class was reproduced as 17 ticks at 0.900-0.902s class=pool_busy: BEFORE = first contended tick flipped readyz 503 with the live-shaped reason; AFTER = in-window ticks stayed GREEN with marker (boot_elapsed 21.9s-109.3s), first post-window tick degraded with class in both reason strings.
- Leg A: boot-epoch clock-skew guard unit-pinned only, never observed live; tests/postgres/test_readiness_pg.py deselected by repo gate (9 collected, 0 ran) — PG readiness probe file not exercised by this lane.
- Leg B: out-of-repo consumer reach = live ensemble_prod + demo ensemble_demo event tables (read-only census 2026-11-23: ZERO rows for all six kinds anywhere) + FE (zero references). NOT checked: any hypothetical external script consumer outside this host.
- Integration: merged-tree boot smoke green (readyz 200 from t+30s, sustained t+160s, zero sentinel/degraded lines, startup complete) but the smoke shares ensemble_dev with the dev daemon — cross-talk possible, and no contended boot was naturally present (same limitation as Leg A's natural boot).
- tests/test_api.py version pin: -k filter matched 0 nodes (not run as a selected test); the assertion line was rewritten mechanically by bump_version.py exactly as in every prior release.

## Known risks

- Leg A tolerates SUSTAINED pool_busy/read_timeout for up to 120s post-boot (a merely-slow-but-alive checkpoint path reports healthy in-window; hard hangs still degrade via exception/probe_timeout). Bounded by window + epoch; post-window everything degrades.
- Reason strings changed shape (" (class=…)" suffix). Verified consumer surface: promote gate reads components/component_classes not reasons (scripts/upgrade/lib.sh:1273); test_upgrade_reasons_reader does substring containment on synthetic payloads (green). A consumer regexing the EXACT legacy string would drift — none found in-repo.
- Leg B: historical DB rows carrying retired kind strings remain readable (kind column is a plain str; no EventKind(value) reverse-parse site exists in-repo — verified). Historical SQLite CHECK constraint in migration 20260412 lists old kinds — untouched audit trail, not a consumer.
- Stage WARN (pre-existing, NOT in this delta): PROMPT-DRIFT agents/maintenancer/workflow.md modified vs .prompt-artifacts.sha256 (allow-stale, staging continued) — the pin likely lags a prior lane; owning lane should refresh.
- Hygiene disclosure: during live-.env debugging this lane echoed a 24-char prefix of the live OPENAI_API_KEY into the lane transcript (anomaly c703ee01, minor; no file written; the standing user-gated key-rotation item covers remediation).
- Pre-existing at base: 6 stale-assertion reds in tests/test_readiness_checkpoint_saver.py (repaired by Leg A); fresh-SQLite boot still broken by migration 20260714_000001 (documented 🟠 defect, untouched).

## How to verify

On the merged tree (origin/latest f30d0d917 or tag v0.31.37), from a worktree with .venv:
- Leg A pins: env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_readiness_checkpoint_saver.py tests/unit/services/test_readiness_boot_window.py -q (36 passed)
- Leg A neighbors: … -m pytest tests/unit/services/test_readiness_queue_age_render.py tests/unit/tools/test_readiness_failure_class.py tests/unit/tools/test_upgrade_reasons_reader.py tests/test_health_probes.py -q (102 passed total across neighbors+health)
- Leg B: … -m pytest tests/unit/services/event_registry/ -q (78 passed) AND bash test/packs/event_registry_unit_test.sh (PASS 78)
- Boot smoke (fenced): source .env (set -a) + PORT/DAEMON_PORT fresh + POSTGRES_DB=ensemble_dev + DATA_DIR=./data_smoke + ENSEMBLE_DB_AMBIENT_ALLOW=1 + ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 + ENSEMBLE_SELF_ENV=dev, python -m daemon → readyz 200 ≤120s, no sentinel degraded lines.
- Release identity: git ls-remote origin refs/heads/latest refs/tags/v0.31.37 'refs/tags/v0.31.37^{}' → f30d0d917… / 0186072040… / f30d0d917…; staged dir /home/nea/agents-ensemble/releases/v0.31.37, binary_sha256 a42185ff38e359aa….

## Bash-raises used

none — every blocking bash ≤600s; the long builds (PyInstaller ~2.5min, npm ci + Angular build ~8min, stage walks) ran under background procs capped at the tool layer (1500s proc caps, no leader raise consumed).

## Complexity self-assessment

medium — assessed at §2.5 before dispatch (two small disjoint defect lanes, no migrations, existing test seams, no engine-arc file overlap). No override; verdict unchanged at close.
