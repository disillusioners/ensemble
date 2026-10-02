# FINAL GATE — opendesign-e2e-contract two-lane commission (daemon fixes A + skill v1.2.0 B)

Date: 2026-10-02 07:03Z → 08:10Z
Commission: merge-final-gate verification, dev-lane only
Worktrees under test:
- Lane A: /home/nea/ensemble-src-wt-daemon-fixes — feature/opendesign-e2e-contract-daemon-fixes @ 954e06cb+a1a05c24 (base cca8fe4e)
- Lane B: /home/nea/ensemble-src-wt-skill-e2e — feature/opendesign-e2e-contract-skill-e2e @ 9365d61d+61deed3a (base cca8fe4e)
Worker instances: recon 45726db9 · boot e73280e0 · units-pins 4eb4b87a · units-regression e5412287 · red-A/B 89bb34b9 · repro1 c47a90ad · repro2 a0249282 · skill 97741281 · fences ac6697af (1 rate-limit revive)

## OVERALL VERDICT: ✅ PASS FOR MERGE (0 blocking findings)

| Section | Verdict |
|---|---|
| REPRO 1 — F1 (load_skill empty-allow deny-all) | **SYMPTOM DEAD** |
| REPRO 2 — F2 (leader-only MCP binding) | **SYMPTOM DEAD** |
| Unit/Regression (lane A venv) | 349 passed / 2 failed / 1 skip — **both reds A/B-proven PRE-EXISTING-AT-BASE; introduced = 0** |
| Skill v1.2.0 contract as executed | **CONTRACT-AS-WRITTEN WORKS** (2 cosmetic line-citation drifts) |
| Fences (live 9797 / ensemble_prod / no Docker / no secrets) | **HELD** |

### Scope Decision
Full commission scope as dispatched (final gate = release-gate-class blast radius: cross-cutting dispatch/MCP seams + skill contract). No expansion; regression keyword-pack trimmed 1036→222 collected tests (documented below) to stay inside the 5-min pack cap. Devs' "251 passed touched dirs" exists only as a shared-meta-KV claim (no on-disk RESULTS/commands) — an independently derived pack set from the actual 7-file diff was used instead.

## §1 REPRO 1 — F1 empty-tools.allow deny-all: SYMPTOM DEAD
Original failure (critical note 9d142f18): load_skill dispatch to bash-capable worker died at runner pre-flight `capability_missing: bash` (`tools.allow.contains('bash') -> not in []`).

Verification vehicle: dev daemon 8079 booted from LANE A worktree (PID 3021910, `ENSEMBLE_SELF_ENV=dev`, local PG `ensemble_dev`, data dir worktree-local `data_dev/`, identity verified via /proc + livez v0.16.9). Because `<meta>` load_skill tags parse ONLY on the internal_agent lane (instance_messaging.py:2802), the repro was driven by a real tester-agent instance ON the dev daemon using its genuine `send_message(..., load_skill=...)` tool — driver 91205f38 → fresh worker child c2f6befa.

Evidence:
- Meta tag parsed: `[MetaTag] Parsed <meta> tag: {'load_skill': 'opendesign-verify'}` → `Extracted load_skill='opendesign-verify' for instance c2f6befa…` (skill id 79bc7ddb… exact match)
- Skill injected: child history shows `[SYSTEM CONTEXT: Skills] → Skill: opendesign-verify (match score 1.00)`
- `capability_missing` count in dev daemon log: **before = 0, after = 0** (log 273→482 lines). Pre-flight passed BOTH requirements (`tools:[bash]` + `mcp:[opendesign]`)
- Real bash executed: `/tmp/f1_repro_marker.txt` = `F1-REPRO-OK-073901`, matching child stdout + log timeline 07:39:01Z
- Both instances hard-deleted (404 confirmed). Wall ~5 min

## §2 REPRO 2 — F2 leader-only MCP binding: SYMPTOM DEAD
Original failure: `mcp_opendesign_*` documented-but-unbound at runtime on fresh non-leader instances; bindings leader-only.

Fresh instances on dev daemon (creation metadata + runtime):
| Instance | od tools @201 | runtime od_list_projects | runtime tool list |
|---|---|---|---|
| worker afed7779… | 10 | ✅ `"No projects found."` | 10/10 ✅ |
| designer 18395e51… | 10 | ✅ `"No projects found."` | 10/10 ✅ |
| leader 5761b055… (reference lane) | 10 | ✅ `"No projects found."` | 10/10 ✅ |

- Metadata equality worker == designer == leader = TRUE (exact 10-tool family; +2 mcp_context7_* each)
- Runtime proof: actual tool_calls in message history (`name=mcp_opendesign_od_list_projects, arguments={}`) on all three — round-trip to OD daemon v0.23.1 (127.0.0.1:7456); empty list = SUCCESS per spec
- MCP bind/error log grep: 0 across window; OD daemon healthy post-test; all instances hard-deleted (404×3). Wall ~6 min

## §3 Unit/Regression (lane A worktree venv, daemon resolves in-worktree, ENSEMBLE_SELF_ENV=dev)
Pack 1 — pin/wire (exact-path, 2.03s): **130/130 PASS** — f1a_tools_allow_wire 7/7 · capability_resolver (empty-allow pin) 87/87 · mcp_tool_filter (F2 binding pin) 25/25 · skill_injection_capability_gate 11/11.

Pack 2 — touched-source regression (keyword set, 32.21s): 10 files, 222 collected → **219 P / 2 F / 1 S**. Trim disclosure: 1036→222 (19 integration-heavy mcp files excluded to respect the 5-min cap).

Both reds classified by base-SHA A/B (throwaway worktree /tmp/wt-base-cca8fe4e, fresh uv venv after PYTHONPATH editable-trap fallback; lane A worktree untouched, verified after):
1. `test_phase4_manager_decomposition.py::…test_manager_pause_instance_cascade_delegates_to_lifecycle_service` — FAIL @ base AND 3/3 FAIL @ HEAD (deterministic). Root cause commit fdd2cd12 (2026-08-25, P3 stop-subtree semantics) — ancestor of base; zero `cascade_to_root` lines in branch diff. Also pre-ledgered: 2026-09-14 consolidated sweep row ("phase4 cascade_to_root kwarg-pin ×1").
2. `test_service_tool_manager.py::test_stop_pid_already_dead_returns_pid_dead` (:603) KeyError 'reason' — FAIL @ base AND 3/3 FAIL @ HEAD (deterministic; flake hypothesis refuted). Source untouched by branch. Also pre-ledgered: 2026-09-26 agent-snapshot-v1 A/B row ("service_tool pid_dead KeyError ×1").

**Introduced failures: 0.** Both reds quarantined in QUARANTINE.md (base-attributed ×2 ledger entries each) → routed to test-debt commission.

## §4 Skill v1.2.0 contract as executed (lane B doc = procedure)
Doc verified: front-matter version 1.2.0 (672 lines), skill-set.yaml manifest 1.2.0 consistent.
- Stage 1 OD daemon health: **PASS** — GET /api/health → 200 `{"ok":true,"version":"0.23.1"}`; systemd unit active
- Stage 2 seam on dev DB: **PASS** — row id b69046ff…, name=opendesign, active, OD_DAEMON_URL=http://127.0.0.1:7456, config_schema_version 0.16.1 (read-only)
- Stage 3 tool surface (fresh non-leader, post-F1): **PASS** — 10 od tools bound + od_list_projects + JSON-RPC probe (serverInfo open-design-mcp 0.16.1, tool count 10, counts match)
- Stage 4 credentials readiness: **PASS** — missing fields exactly BYOK_BASE_URL / BYOK_MODEL / BYOK_API_KEY (bound_handles_count 0); guidance ACCURATE against real interfaces (kms_request/kms_attach @ infra.py:946/:988, __KMS_REF__ marker format, error tokens, idempotent replay) — nothing provisioned (dry only). Two cosmetic line-citation drifts: doc :329 `kms_attach:1033` → actual :1032; doc :333 `opendesign.py:146-176` → BYOK dicts at ~:205-260. Non-functional.
- Stage 5 od_generate_design smoke: **BLOCKED-AS-EXPECTED** — both lanes returned `BYOK not configured: missing BYOK_BASE_URL/BYOK_API_KEY/BYOK_MODEL. Specifically: …` → matches doc FAIL branch #1 verbatim → honest-stop classification, NOT a skill failure. Zero workarounds (worker attested). OD /api/projects stayed `[]` (proxy short-circuits pre-creation).
- Idempotent fast path: **PASS — NO MUTATION** — all 5 stages byte-identical; seam row snapshot BEFORE == AFTER; no configure-builtin ever issued.

## §5 Fences audit: HELD
- Live 9797: PID 2890792 unchanged, same lstart (Oct 1 17:46:12), livez 200 v0.16.9; demo 7979 PID 2185455 unchanged. Only live-system writes post-07:00Z = each daemon's own ensemble.log.
- **Live ensemble_prod opendesign row (single authorized read-only SELECT): `updated_at = NULL` → row never UPDATEd; instance_metadata = {} (no bound_handles, no __KMS_REF__ markers)** — commission could not have touched it.
- No apt (history shows only unattended-upgrade @ 06:41Z, pre-window), no docker/podman.
- Plaintext-secret scan of all commission artifacts (/tmp logs, recipe, evidence dirs, data_dev tree): **0 hits**. Known incident: one worker's TRANSCRIPT-only env dump (no file persistence) — PB-F1 exposure class, disclosed.
- Disclosed commission mutations (all dev-lane): local-PG `ALTER ROLE ensemble PASSWORD` re-alignment on 127.0.0.1:5432 (required for dev daemon connect; local cluster only — ensemble_prod/demo live on remote 10.44.0.2); dev DB rows from boot + test instances (all hard-deleted); worktree data_dev/.
- Worktrees final: lane A clean @ a1a05c24 (data_dev/ gitignored), lane B clean @ 61deed3a.
- Dev daemon 8079 left RUNNING (PID 3021910, dev-identity verified) for follow-up poking — tear down at leader's discretion.
- Correction: port 8088 has no listener in this deployment (no opencode config on live) — fences attestation is "nothing binds 8088 before/during window"; the "listening normally" framing in the dispatch was wrong.

## ensure.md Validation (Core, blast-radius scoped)
- Critical: no regressions in changed packs → **PASS** (pins 130/130; regression reds = quarantined base-attributed)
- Critical: concurrency_atomic_unit_test → **N/A-scoped-out** (diff touches tool-allow wire + capability resolver semantics; no async/DB-lane changes)
- Critical: dev.sh `--timeout-graceful-shutdown 10` static check → **PASS** (hit at :99/:102 in main + lane A)
- Important/Nice-to-have items (await-audit, dead-code) → N/A (no async signature changes; pin tests cover accessor liveness)
- No contradictions with ensure.md methods this gate.

## Quick Fixes Applied
None (authorization NO — final gate; report-only).

## Gaps
- None blocking. Notes: (1) devs' "251 passed" is KV-claim-only — replaced by independently derived packs (349 green tests total); (2) regression pack trim 1036→222 documented; (3) skill doc 2 cosmetic line-citation drifts (fix in a future doc-touch commit — do not block merge); (4) dev DB row's stored config_schema snapshot is stale (2 keys vs 7 in current definition — validation uses current definition, no functional impact; KB gotcha recorded).

## Action Needed
- [ ] Test-debt commission: re-anchor 2 quarantined fixtures (cascade_to_root kwarg-pin; service_tool exited-shape) — base-attributed, unrelated to this merge
- [ ] Optional doc touch-up: skill v1.2.0 line citations (:329, :333)
- [ ] Leader decision: tear down dev daemon 8079 (PID 3021910) when done

## Documentation Updated
- [x] RESULTS/2026-10-02-opendesign-e2e-contract-final-gate.md (this file)
- [x] QUARANTINE.md — 2 new base-attributed rows
- [x] LESSONS/2026-10-02-dev-daemon-api-repro-vehicle.md
- [x] rules/ensure.md — untouched (user-owned)

## Code Changes Summary
None to source/test code. Tester docs only (this file + QUARANTINE + LESSONS) — committed to the main checkout (hash in §Commit below; worker-verified).
