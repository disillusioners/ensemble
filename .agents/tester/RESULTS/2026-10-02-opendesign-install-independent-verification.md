# Test Report: OpenDesign daemon install — INDEPENDENT verification (4 ACs)

Date: 2026-10-02T04:18–04:35Z (UTC)
Commissioner: leader (independent verification of devops installer's report; installer's claims NOT trusted — own evidence produced)
Tester instance: this tester (test leader) + 3 dispatched workers (all read-only)

Instance IDs used:
- bedb24be-1348-4640-b279-3e4a2de3ccd1 (od-verify-ac1-host, no skill) — AC1 host evidence
- 58cb7548-9fbc-42c7-abb6-709d71f7cd13 (od-verify-ac2ac4-seam, no skill) — AC2+AC4 JSON-RPC seam probes
- e6741718-0194-4e95-993d-9d77c349bc7f (od-verify-ac3-skill, load_skill=opendesign-verify) — FAILED at skill pre-flight (capability_missing: bash); zero tool calls
- 0e85150d-c256-4ff3-82e6-6712de5a12ce (od-verify-ac3-retry2, manual skill_search→skill_view load) — AC3 PASS (one permitted re-dispatch, different load mechanism)

## Summary

| AC | Verdict | Path used |
|----|---------|-----------|
| AC1 durable + healthy | ✅ PASS | worker bash evidence session (read-only) |
| AC2 seam end-to-end, pristine empty list | ✅ PASS | caller-specified FALLBACK: MCP stdio JSON-RPC vs registered proxy (native mcp_opendesign_* tools unavailable — see Finding F2) |
| AC3 opendesign-verify skill | ✅ PASS (mechanism deviation disclosed) | manual skill_search→skill_view load (load_skill pre-flight defect — see Finding F1); skill executed per its own body |
| AC4 od_generate_design BYOK gating + read-only spot-checks | ✅ PASS | JSON-RPC tools/call vs proxy |

- Daemon PRISTINE confirmed 4× (seam worker before+after; skill worker before+after): `{"projects":[]}` / "No projects found."
- Quick fixes applied: 0 (none authorized — verification-only commission; no repairs attempted)
- Quarantined: n/a. Packs: n/a (host-install verification; no repo test packs involved)
- Code changes / commits: NONE. Nothing to commit. Repo working tree untouched (fence honored — uncommitted skill-template edit under review NOT disturbed).

## Scope Decision

Verification of a HOST install (systemd unit, daemon, MCP seam, skill) — not repo code. No PACKS.md packs apply; no ensure.md gates apply (repo quality gates out of scope for host state). Optional `kill -9` respawn re-test DECLINED to keep strictly read-only (installer already proved respawn once; AC1 evidence independently corroborates it — see NRestarts note).

## AC1 — durable + healthy: PASS (worker bedb24be)

- Health: `{"ok":true,"version":"0.23.1","amrTerminalReporter":{...all-zero...}}` — HTTP 200, 10.7 ms loopback.
- Unit: `is-active` → active; `Restart=on-failure`, `MainPID=2965860`, `ActiveState=active/running`.
- Unit file ~/.config/systemd/user/opendesign-daemon.service: `ExecStart=/home/nea/.nvm/versions/node/v24.21.0/bin/node apps/daemon/dist/cli.js --no-open` (nvm ABSOLUTE path ✓), `Restart=on-failure` ✓, `RestartSec=5` ✓, `WantedBy=default.target` ✓; plus OD_PORT=7456, OD_BIND_HOST=127.0.0.1, NoNewPrivileges/PrivateTmp hardening.
- Linger: `Linger=yes` (user nea).
- Live process: PID 2965860, ppid 2965344 (systemd --user), cmdline EXACT match, etime 16m22s at check.
- Node: nvm v24.21.0 present at ExecStart path; system node /usr/bin/node v22.23.2 UNTOUCHED; unit does not depend on shell PATH.
- Clone: HEAD 53231d40b778d88eba23f35547bf99485d3ae9fc (= claimed 53231d40); cli.js built 04:02, process started 04:05:36 — consistent build→start sequence.
- Proxy path exists (npm bin-shim SYMLINK → ../open-design-mcp/dist/src/server.js).
- Single unit, no stale/duplicate units.
- Anomaly (non-blocking): `NRestarts=1` — exactly consistent with the installer's own kill -9 respawn proof (first process died pre-04:05:36, systemd restarted per Restart=on-failure/RestartSec=5). Current PID stable entire verification window; no storm (1 ≪ 5/10min burst threshold).

## AC2 — seam end-to-end: PASS (worker 58cb7548; caller-specified fallback path)

- STEP 0 on MY instance: `mcp_opendesign_od_list_projects` NOT a valid runtime tool (documented in prompt tool table, rejected at runtime) → fallback mandated and used. Same symptom reproduced on the fresh seam worker. (Finding F2.)
- JSON-RPC MCP handshake vs proxy (OD_DAEMON_URL=http://127.0.0.1:7456): initialize OK — serverInfo open-design-mcp 0.16.1, protocol 2024-11-05; cold start 447 ms.
- `tools/list` → exactly 10 od_* tools (matches expected surface).
- `od_list_projects` → `{"projects":[]}` / "No projects found." — EMPTY (pristine baseline), 83 ms.
- No project created at any point; final `od_list_projects` after all probes → still EMPTY.

## AC3 — opendesign-verify skill: PASS (worker 0e85150d; mechanism deviation disclosed)

- Attempt 1 (e6741718, sanctioned load_skill path): DIED at skill pre-flight — `{"kind":"capability_missing","capability":"bash","detection_evidence":"pre_flight: tools.allow.contains('bash') -> not in []"}` — zero tool calls; treated as interim per REPORT SANITY marker; NOT a skill failure. (Finding F1.)
- Attempt 2 (permitted re-dispatch, same task, manual load): skill FOUND via skill_search (id 5aeef425-f39d-4af4-b2bd-9e844f190b01, name opendesign-verify, v1.0.0, category worker-skill-set), loaded via skill_view, EXECUTED per its body.
- Skill verdict: **PASS — "opendesign capability present"** (capability_check gate satisfied; verification route GET /api/projects → `{"projects":[]}`).
- 10-tool surface listed (project-oriented): od_list_projects, od_get_project, od_create_project, od_update_project, od_delete_project, od_compose_brief, od_generate_design, od_save_project_file, od_save_artifact, od_lint_artifact.
- Extra corroboration: GET /api/version → version 0.23.1, `packaged:false` (source build), platform linux/x64; listener 127.0.0.1:7456 owned by PID 2965860 (unit's MainPID).
- skill_feedback recorded by worker: applied=true, usefulness 6/10, improvement notes = F1 pre-flight bug + stale tool-name examples in skill body.
- Daemon pristine after: YES (final GET /api/projects empty).

## AC4 — generate gating + spot-checks: PASS (worker 58cb7548)

- `od_generate_design` prompt-only, NO creds (3 ms): `"BYOK not configured: missing BYOK_BASE_URL/BYOK_API_KEY/BYOK_MODEL. Specifically: BYOK_BASE_URL, BYOK_API_KEY, BYOK_MODEL."` — EXACT expected refusal, gated BEFORE project lookup/generation. isCredential-free path confirmed.
- `od_get_project` nonexistent id `od-verify-nonexistent-000` (18 ms): clean tool-level error `"Project not found: od-verify-nonexistent-000"` (isError:true; no crash, no hang, no JSON-RPC protocol error).
- `od_list_projects` (baseline + final): clean empty-list success both times.
- Schema adaptation (once, schema-driven): actual args `projectId`/`prompt` (not project_id/design_brief).

## Findings (anomalies — informational, none blocking)

- **F1 — load_skill pre-flight defect (ensemble, not OD):** `load_skill="opendesign-verify"` to a bash-capable worker died at pre-flight reading empty declarative `tools.allow` as "no bash". Skill content itself is fine (loads and passes via skill_search/skill_view). Needs a skill-runner/pre-flight fix; already fed into skill evolution via worker skill_feedback.
- **F2 — mcp_opendesign_* documented-but-unbound:** tools appear in system-prompt inventory but are NOT valid at runtime for this tester AND fresh worker instances (reproduced 3×: tester + 2 workers). Leader toolset reportedly works. Fresh non-leader instances currently must use the JSON-RPC/REST fallback. Root cause (tool-binding vs prompt-inventory divergence) is an ensemble-side commission candidate.
- **F3 — NRestarts=1:** explained by installer's kill -9 respawn proof; steady-state healthy.
- **F4 — "binary" is an npm bin-shim symlink** (docs nit).
- **F5 — skill body stale examples** (`od_list_designs`/`od_get_design` vs real project-oriented surface) — flagged via skill_feedback.
- **F6 — systemd user-bus env in detached worker shells:** one worker hit "Failed to connect to bus: No medium found" without XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS exports; AC1 worker with exports succeeded. Env-procedure note for future host checks.
- Latency: health 10.7 ms; seam steady-state 3–83 ms; init 447 ms cold. No flapping observed.

### Gaps

None — all 4 ACs verified with independently produced evidence.

## Documentation Updated

- [x] RESULTS/2026-10-02-opendesign-install-independent-verification.md (this file)
- [x] LESSONS/2026-10-02-loadskill-preflight-and-mcp-binding-gaps.md
- [ ] PACKS.md / MOCK_TESTS.md / QUARANTINE.md — n/a (no packs, no mock tests, host verification)
- [ ] rules/ensure.md — untouched (user-owned)

## Overall Status

- AC1 ✅ · AC2 ✅ · AC3 ✅ (deviation disclosed) · AC4 ✅ — daemon pristine throughout
- **VERDICT: ALL 4 ACCEPTANCE CRITERIA PASS — install VERIFIED.** No repairs attempted (per commission). F1/F2 are ensemble-side follow-ups, not OD-install defects.
