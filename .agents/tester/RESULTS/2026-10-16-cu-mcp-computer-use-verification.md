# cu-mcp Verification — Computer Use MCP Integration (regression + base-claim A/B + live benign smoke)

Date: 2026-10-16
Task: cu-mcp (worktree `agents-ensemble-wt-cu-mcp`, branch `feature/computer-use-mcp`)
Commits: implementation `cdc9e8633`, docs-only tip `e4f0832d0`, base `772ca92b9`
Host: macOS 14.4.1, Apple Silicon (darwin-arm64), Node v22.21.1 / npx 10.9.4
Worker instances: 43b5f5eb (n1), ebd7c2e0 (n2), d86a476b (n3), f8399d61 (n4), 83378f5a (n5), 28816db4 (n6), 47f9003b (n7), 182d3c13 (n8)

## Summary

| Area | Verdict |
|---|---|
| Part 1 regression suites (4 packs) | ✅ PASS — 180/180 (45+45+22+68), zero failures |
| Part 1.2 disputed-pair A/B | ✅ CLAIM CONFIRMED — 21E identical node sets at base 772ca92b9 and branch; `service_tool` mock-gap; **0 branch-caused regressions** |
| Part 2 live smoke (ON path) | ✅ PASS within safety line — boot, DB row (pinned pkg + core profile), session 1.06s, 38 tools, 4 read-only calls, clean teardown. **TCC caveat: Screen Recording + Accessibility NOT granted → wallpaper-only capture; doctor is authoritative** |
| Part 2 kill-switch (OFF path) | ✅ PASS — no row, other builtins bootstrapped, one-shot log ×1, zero cu spawns, ports freed |
| Safety compliance | ✅ ZERO input injection — 4 tools called (screenshot/doctor/read_window_text/cursor_position), 34 refused by structural allowlist |
| ensure.md (Core, scoped) | ✅ PASS — no regressions in changed packs (quarantine-aware); deadlock/dev.sh reqs out of blast radius |

**Overall: VERIFIED — no regressions found; integration behaves as claimed. Remaining unverified-by-design: window-content capture fidelity + OCR (blocked by TCC grants), all mutating cu_ surfaces (blocked by safety line).**

## Scope Decision

Full suite NOT run — not warranted. Change set (cdc9e8633) = 1 new builtin module + tool_adapter privilege-stamp (+115) + `_tool_registry` cu_/privileged-set + ari/leader meta.json allow + 4 same-PR pin test files + docs. Scoped packs: the new suite, the pin suites, the kill-switch/reference suite, and the two disputed suites (A/B both legs). Skipped: rest of tests/ (~500 files) — no touched module outside the listed surface. Release Gate not triggered (scoped feature add, not architecture refactor).

## Part 1 — Regression suites (execution gate: `uv run python -m pytest` from worktree root; /bin/sh POSIX; per-call timeouts)

| Pack | Files | Result | Runtime | Evidence |
|---|---|---|---|---|
| cu_new_suite_unit_test | tests/unit/test_computer_use_mcp.py | ✅ PASS 45/45 (45 collected from 39 funcs/10 classes — matches dev claim 45/45) | 0.73s | /tmp/cu-mcp-verify/n1_new_suite.log |
| cu_pins_unit_test | tests/unit/tools/test_upgrade_registration.py + test_attestation_registration.py | ✅ PASS 45/45; privileged-set pin now exactly 6 entries incl. `cu` (`test_privileged_categories_is_exactly_six_entries`, attestation pin :177) | 6.16s | /tmp/cu-mcp-verify/n2_pins_unit.log |
| cu_pins_integration_test | tests/integration/test_maintenancer_spawn_resolves_tools.py + tests/integration/test_service_tool_flag_off_byte_identical.py (`--override-ini="addopts="`; NOTE: the byte-identical file lives under tests/integration/, not tests/unit/) | ✅ PASS 22/22 (13+9); `test_privileged_categories_contains_six_entries` + `test_privileged_category_set_is_pinned` both green | 1.54s | /tmp/cu-mcp-verify/n3_pins_integration.log |
| cu_plane_reference_unit_test | tests/unit/test_plane_mcp.py | ✅ PASS 68/68 (baseline 60 → +8 growth, all green); zero `_tool_category`/stamp references in suite — plane_ un-stamped zero-regression confirmed | 1.03s | /tmp/cu-mcp-verify/n4_plane_reference.log |

Coverage cross-check (leader item f): the new suite pins (i) cu_ prefix mapping (`TestComputerUsePrefixOverride`, `TestPrivilegeStamp` ×8), (ii) kill-switch (`TestComputerUseMcpKillSwitch` ×10 + bootstrap-skip + D7 matrix ×11), (iii) agent gating (`TestEmptyAllowAgentLeak`: empty-allow excluded, explicit-cu granted, mcp-only NOT granted). ari/leader-specific allow resolution is covered at the registry/meta level via the pin suites + meta.json diffs; no agent-name-specific test exists in the new suite (gating primitive is what's pinned — acceptable).

## Part 1.2 — Disputed pair: developer base-failure claim — **CONFIRMED**

Developer claim: "21+4 ERRORS in test_builtin_mcp_servers.py + test_context7_builtin.py, pre-existing at base (fixture `mock_config` lacking `service_tool` attr)".

A/B method: identical invocation (`uv run python -m pytest <pair> --tb=short -q`, timeout 120) on branch worktree (e4f0832d0) and detached base worktree at 772ca92b9 (import-proven: `IMPORT_FROM=/private/tmp/ensemble-base-cu-772ca92b9/daemon/manager.py`; .env parity; worktree removed after).

| Leg | Passed | Errors | Failed | Error signature |
|---|---|---|---|---|
| Branch e4f0832d0 | 92 | **21** (17 builtin + 4 context7) | 0 | `AttributeError: Mock object has no attribute 'service_tool'` @ daemon/manager.py:827 (`InstanceManager.__init__` → `self.config.services.service_tool.max_concurrent`) |
| Base 772ca92b9 | 92 | **21** (17 builtin + 4 context7) | 0 | identical, byte-for-byte same 21 node ids |

- Per-node set difference: **∅** — the verbatim 21 node ids are identical across legs (TestBootstrap ×5, TestBootstrapDisableEnable ×6, TestBootstrapSkipsUnavailable ×2, TestOrphanedBuiltinCleanup ×4, TestContext7Bootstrap ×4). All setup-phase ERRORs; no test bodies execute; zero FAILED nodes on either leg.
- **Verdict: pre-existing at base. Branch-caused regressions = 0. QUARANTINE family already ledgered (rows 2026-09-04/10-02/10-12); this gate's adjudication row appended.**
- Claim-shape note: "21+4" is accurate only under the "21 total, of which 4 in context7" reading. Per-file, builtin is **17**, not 21. Fix remains the documented test-debt: wire `services.service_tool` into the `mock_config` fixtures (tests/unit/test_builtin_mcp_servers.py:368, tests/unit/test_context7_builtin.py:340).
- Evidence: /tmp/cu-mcp-verify/n5_branch_pair.log (236 lines), /tmp/cu-mcp-verify/n6_base_pair.log (16 KB).

## Part 2 — Live benign smoke (SAFETY-CRITICAL; zero input injection)

Isolation (both legs): worktree `frontend/scripts/boot-e2e-maintenance-daemon.sh` (boots the WORKTREE daemon via REPO_ROOT self-location), disposable PG cluster + DB, canary `state=ready`, SIGTERM teardown via script traps, ports verified free before + after. NEVER dev.sh/8079/8088/4199; prod daemon + prod DB untouched; fenced dev .env respected (OPENAI_API_KEY passed into subprocess env only, never printed).

### ON-path leg (ports 15433/18099, DB ensemble_cu_smoke_main) — PASS

Checklist:
- **a. Boot + row** ✅ — canary ready 6.09s; `mcp_servers` row `{"name":"computer-use","is_active":true,"config":{"transport":"stdio","command":"<nvm>/bin/npx","args":["-y","@zavora-ai/computer-use-mcp@7.6.1"],"env":{"COMPUTER_USE_PROFILE":"core"}}}` — pin ✅ core profile ✅. Builtin roster: computer-use + context7 + webfetch (3 rows).
- **b. Session + tools/list** ✅ — `create_test_session(row_config, 60)` initialized in 1.06s; server banner `[computer-use-mcp] Server running (MCP 2026-07-28 + legacy 2025)`. **38 tools** (raw names unprefixed — daemon cu_ prefixing is unit-pinned by n1; heavy preload path intentionally not exercised). Primitives present: click/type/key/mouse_move/screenshot/doctor ✅.
- **c. Screenshot** ✅ wire-level / ⚠️ TCC-limited — JPEG 68,031 B (not PNG — magic-byte sniff + sips convert needed; PIL absent from venv), 1024×640, mean 123.52, stddev 71.74, 21,567 unique colors. Numerically non-trivial BUT **wallpaper-only** (app windows omitted) — the macOS TCC partial-denial signature. See TCC section.
- **d. Doctor** ✅ — `ok:false, passed:10, failed:2, skipped:2`. The 2 fails ARE the TCC state (below). Platform darwin-arm64/Node v22.21.1, native NAPI binary loaded, clipboard/automation/policy/audit (`~/.computer-use-mcp/audit.jsonl`)/user-active-guard all pass.
- **e. TCC** ⚠️ **DENIED (both grants)** — see below.
- **f. Teardown** ✅ — SIGTERM → exit 143, trap teardown ×2 idempotent, 15433+18099 free, data dir removed, no orphan cu processes.

Called vs not-called (structural allowlist in `call_tool_safe()`; every call logged pre-exec in call_log.json):
- CALLED (4): `screenshot`, `doctor`, `read_window_text` (server refused: `target_required` — needs target_app/target_window_id, which would require non-allowlisted enumeration; zero side effects), `cursor_position` (rule-vetted pure read: "Get current cursor position" → `(456, 304)`).
- REFUSED (34): 10 input-injection (click family, mouse_move, scroll, type, key, hold_key), 4 window/app control (open_application, unhide_app, activate_app, activate_window), 1 ambiguous (zoom — skipped per safety line), 19 not-in-allowlist (policy_status, clipboards, window/display enumeration, set/get_target, control_status, etc.).
- Disclosed deviation: calls 3–4 fired after doctor had already reported the TCC fails (metrics-gated sequencing); both were allowlisted pure reads, OCR was server-refused pre-action, cursor read is not capture-dependent. No injection, no workaround, no grant manipulation.

### TCC state — harness host needs BOTH grants

Doctor (authoritative):
- FAIL `display_capture`: "Screen Recording is not granted to this host process; captured 320x200 but application windows are omitted."
- FAIL `accessibility`: "Accessibility is not granted to this host process (frontmost app: com.apple.loginwindow); UI tree, element and input tools will be refused."

Harness host identity (process tree needing grants): `Terminal.app (pid 8648) → login → zsh → bash → ensemble-prod daemon (54302→54303) → /bin/sh driver → uv run python driver_main.py → npx → node (computer-use-mcp)`.

**Manual grant steps (for the user):**
1. System Settings → Privacy & Security → **Screen & System Audio Recording** → enable **Terminal.app** (the app hosting the ensemble daemon chain).
2. System Settings → Privacy & Security → **Accessibility** → enable **Terminal.app**.
3. **Restart the harness chain** (TCC changes require process restart): the terminal session hosting the daemon (or the daemon + driver chain) must be relaunched after granting.
4. Re-run the smoke (driver at /tmp/cu-mcp-smoke/driver_main.py; spec in MOCK_TESTS.md) to verify window-content capture + OCR fidelity.

No workarounds attempted (no tccutil, no sudo, no DB tricks) per safety line.

### OFF-path kill-switch leg (COMPUTER_USE_MCP_ENABLED=0; ports 15434/18198, DB ensemble_cu_smoke_ks) — PASS

- a. `SELECT count(*) FROM mcp_servers WHERE name='computer-use'` → **0** ✅
- b. Other active builtins bootstrapped: context7, plane ✅ (bootstrap ran; skipped ONLY cu)
- c. One-shot log EXACTLY once: `Computer Use MCP server disabled by kill-switch (COMPUTER_USE_MCP_ENABLED is falsy) — builtin skipped at registration` (L69, count=1) ✅
- d. Zero cu-related npx/zavora spawn lines (hard counters npx=0/zavora=0/subprocess=0; path-strip filter excluded worktree-path false positives) ✅
- CU_NPX_PATH was set absolute so the skip reason is unambiguously the kill-switch, not npx resolution. Canary ready 7.15s. Teardown clean, both ports free. NO MCP session, NO tool calls.
- Evidence: /tmp/cu-mcp-smoke/ks-evidence/ (12 files), /tmp/cu-mcp-smoke/main-evidence/ (20 files), drivers + logs under /tmp/cu-mcp-smoke/.

## ensure.md Validation (Core, blast-radius scoped)

- **Critical: no regressions in changed packs** ✅ — all 4 change-set packs PASS; disputed pair failures are QUARANTINE.md pre-existing (quarantine-aware rule: do not fail the requirement).
- Critical: deadlock/concurrency pack — OUT OF BLAST RADIUS (no concurrency/lock-path changes in cdc9e8633; scoped out, not skipped silently).
- Critical: dev.sh `--timeout-graceful-shutdown 10` — OUT OF BLAST RADIUS (dev.sh untouched by the PR).
- Release Gate — NOT TRIGGERED (scoped feature add; A/B proved the only red families pre-existing).
- Contradiction notices: none (no ensure.md requirement conflicted with pack/timeout rules this cycle).

## Action Needed

- [ ] USER: grant Screen Recording + Accessibility to Terminal.app, restart harness chain, re-run ON-path smoke for capture-fidelity + OCR verification (driver + spec ready).
- [ ] Test-debt (pre-existing, quarantined): wire `services.service_tool` into `mock_config` fixtures at test_builtin_mcp_servers.py:368 + test_context7_builtin.py:340 (heals 21E).
- [ ] Cross-platform round (hard gate per worktree note): Windows + Ubuntu legs pending — macOS leg complete.
- [ ] Optional commission: agent-name-specific ari/leader cu_ resolution test (currently covered via registry pins + meta.json, not a named-agent test).

## Documentation Updated

- [x] RESULTS/2026-10-16-cu-mcp-computer-use-verification.md (this file)
- [x] PACKS.md — ad-hoc pack section appended (8 packs)
- [x] QUARANTINE.md — cu-mcp gate adjudication row (base 772ca92b9 A/B confirmation)
- [x] MOCK_TESTS.md — two smoke specs registered
- [x] LESSONS/2026-10-16-cu-mcp-tcc-wallpaper-signature.md
- [ ] rules/ensure.md — no changes (user-owned)

## Code Changes Summary

None to production or test code — all packs green or pre-existing-quarantined; smoke harness lives in /tmp only. Doc/ledger updates in .agents/tester/ committed on the feature branch.
