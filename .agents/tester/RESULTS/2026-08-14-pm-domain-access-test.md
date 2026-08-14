# Test Report: PM Domain Access (mcp_full_access)
Date: 2026-08-14
Branch: `feature/pm-domain-access`
Commit under test: `57d1e07d` (test commits landed during round: `05937b63`, `8cd206f1`)
Instance IDs: 3cc7210c (plane), ab962ec2 (pm-agent), 4e7f8c17 (registry), e0c38a02 (authz), 486d417a (version-tag), 855ccc04 (mcp-builtin), c39a247b (mcp-service), 9e332eff (concurrency), 6a5f84f9 (security-author), 53036df2 (static), 13a93f79 (gap-pack + quick-fix)

## Summary
- **Total: 10 packs | 10 PASS | 0 FAIL | 0 TIMEOUT** (613 tests executed + 26 new security tests)
- Developer's 125 targeted tests independently verified: ✅ (plane 60/60 + PM agent 65/65)
- New tests added: 26 (plane_domain_access security pack, commit `05937b63`)
- Quick fixes applied: 3 test-mock drifts (commit `8cd206f1`, pre-existing, NOT PM-caused)
- Quarantined: 1 (pre-existing SQLite migration failure, see QUARANTINE.md)
- Security findings: **0**
- **Overall Status: ✅ READY**

## Scope Decision
Scoped regression, not full suite. Change = agent_id threading through spawn/MCP-preload machinery + PM agent tool-surface change: 12 files (6 listed in phase context + 4 PM prompt .md files + 1 planning doc + 1 docstring-only plane.py change — phase context under-reported; extras covered by PM-agent pack's convention/composition tests). Not an architecture refactor (no job/queue state-machine change, no DB schema change) → Release Gate NOT triggered; ensure.md Core critical requirement (concurrency pack) ran because spawn machinery is adjacent to job/queue code. Skipped: full non-integration suite (~200 packs), E2E workflows (LLM-calling; deferred e2e with mock Plane MCP server remains developer-flagged follow-up), frontend (no FE change).

## Per-Area Results (leader's 6 requested areas)

### 1. Targeted Suites (verify developer's 125) — ✅ PASS
| Pack | Result |
|------|--------|
| plane_mcp_unit_test | PASS 60/60 in 1.18s |
| project_manager_agent_unit_test | PASS 65/65 in 0.97s |

### 2. Regression Sweep (spawn machinery) — ✅ PASS
| Pack | Result |
|------|--------|
| blueprint_registry_unit_test (tests/test_registry.py — registry.py changed) | PASS 102/102 in 1.98s |
| authz_auto_derive_unit_test (spawn/team-members + ari) | PASS 82/82 in 2.72s |
| version_tag_tool_resolution_unit_test | PASS 19/19 in 1.15s |
| mcp_disable_flags_unit_test (test_builtin_mcp_servers.py) | PASS 83/83 in 1.83s |
| mcp_service_pool_unit_test (test_mcp_service.py — mcp_service.py changed) | PASS 45/45 in 0.97s |
| concurrency_atomic_unit_test (ensure.md Critical) | PASS 91 pass / 74 skip / 0 fail in 8.46s |
| spawn_mcp_preload_gap_test (NEW ad-hoc, static-discovery gap) | PASS 74 pass / 1 quarantine-skip / 0 fail (after quick-fix) |

Gap pack details: static sweep found 4 test files referencing spawn_instance_with_mcp/ensure_mcp_preloaded in NO other pack. First run FAIL 71/75 — attribution via git diff proved all 4 failures pre-existing (2 sync-mock vs awaited `_restore_instance`, 1 `suspension_reason` kwarg drift from earlier ask_questions fix, 1 SQLite `DROP CONSTRAINT` migration failure). Quick-fixed 3 (commit `8cd206f1`), quarantined 1 (skip marker + QUARANTINE.md). Re-run green. **Zero PM regressions.**
Worker meta.json unchanged: ✅ (git diff empty for agents/worker/, JSON parses, no mcp_full_access field).

### 3. Security Verification — ✅ PASS (NEW pack: plane_domain_access_unit_test, 26/26, commit 05937b63)
| Matrix case | Verdict | Evidence |
|---|---|---|
| Bypass scope (mcp_full_access=["plane"]) | ✅ writes present | `_get_read_only_tools` returns False → strip skipped (mcp_service.py:800-870) |
| No-bypass, real agents (leader, developer, ari) | ✅ writes stripped | 6 tests vs real meta.json files on disk |
| Typo fail-closed (["planee"]) | ✅ strip applied | exact-match membership check |
| Empty field ([]) | ✅ strip applied | verified via helper + realistic filter chain |
| Field absent | ✅ strip applied | Pydantic default [] |
| Leader isolation | ✅ no plane writes | 4 tests incl. deny-list fallback path |
| **Security findings** | **0** | no production bugs surfaced |

### 4. PM End-to-End Behavior (meta.json) — ✅ PASS
- version 2.1.0 ✅ | mcp_full_access == ["plane"] ✅
- 18 project write tools in allow ✅ (26 project_* = 8 reads + 18 writes)
- 8 plane writes removed from deny ✅ (mechanism: bare `plane` umbrella in allow + mcp_full_access exemption, NOT per-tool enumeration — approach per planning doc)
- project_delete + project_history_delete: in deny ✅, absent from allow ✅

### 5. Drift Alarm — ✅ PASS (with clarification)
- No production drift-alarm callable exists; the mechanism is a test helper (symmetric difference vs pinned surface) in test_project_manager_agent.py:1062.
- New pack re-implements helper locally and pins semantics in BOTH directions (added verb fires, removed verb fires, silent on match, symmetric-difference contract, runtime-classifier round-trip). 5/5 drift tests PASS.

### 6. Convention Compliance — ✅ PASS (advisory notes)
- 7 Cardinal Rules exactly ✅ (rule.md:5-17; the 10-item list at lines 25-43 is a guidelines block, not cardinals)
- Canonical forbidden-token gate (inside PM agent pack convention class): PASS 65/65
- Advisory (from my broader ad-hoc grep list, NOT the canonical gate): `checkpoint` ×1 at rule.md:41 (cardinal-adjacent implementation-detail mention); `.agents/` path refs ×8 in workflow.md (legitimate working-dir paths). 🟢 nice-to-have: reword rule.md:41.

## ensure.md Validation Results
### Critical Requirements — 4/4 PASS
- ✅ No regressions in changed packs (all 10 PASS; only failures found were pre-existing, quick-fixed or quarantined)
- ✅ Deadlock/concurrency integrity — concurrency_atomic_unit_test PASS (91p/74s/0f)
- ✅ No sync DB calls on event loop — covered by same pack PASS
- ✅ dev.sh `--timeout-graceful-shutdown 10` present (line 102, verified by static worker)

## Quick Fixes Applied
| Instance | Fix | Commit |
|---|---|---|
| 13a93f79 | test_mcp_cold_load_race.py: 2 sync mocks → AsyncMock (production awaits `_restore_instance`) | 8cd206f1 |
| 13a93f79 | test_paused_instance_ttl.py: `_mock_pause_db_sync` + `suspension_reason=None` kwarg | 8cd206f1 |
| 13a93f79 | test_mcp_cold_load_race.py:241 quarantine skip marker (SQLite DROP CONSTRAINT) | 8cd206f1 |

## Quarantine
1 test quarantined (pre-existing, not PM-related): TestManagerGetInstanceAsync::test_manager_get_instance_delegates_to_lifecycle_service — migration 20260714_000001 `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS` fails on SQLite (dual-driver issue; PG-primary constraint documented in critical notes). Production migration fix recommended as follow-up.

## Follow-ups (non-blocking)
1. 🟠 SQLite compat: migration 20260714_000001 uses `DROP CONSTRAINT` (PG-only syntax) — production fix needed so SQLite-path tests stop needing quarantine.
2. 🟢 rule.md:41 `checkpoint` mention — consider rewording to remove implementation detail from cardinal section.
3. 🟢 Developer-flagged deferred e2e: PM calls plane_create_issue end-to-end with mock Plane MCP server — remains open (outside this round's scope; unit+integration surface green).
4. 🟢 pytest-timeout plugin absent from .venv (config options warn; command-level timeout carries the dual-layer contract) — recurring flag since 2026-08-13.

## Documentation Updated
- [x] PACKS.md — 8 rows refreshed, 2 packs registered (plane_domain_access_unit_test, spawn_mcp_preload_gap_test), summary line added
- [x] QUARANTINE.md — 1 entry added
- [x] RESULTS/2026-08-14-pm-domain-access-test.md — this report
- [x] LESSONS/2026-08-14-static-discovery-gap-sweep.md
- [ ] rules/ensure.md — user-maintained, unchanged

## Code Changes Summary (test code only; all committed)
- 05937b63: tests/unit/test_plane_domain_access.py (903 lines, 26 tests) + test/packs/plane_domain_access_unit_test.sh
- 8cd206f1: tests/unit/test_mcp_cold_load_race.py + tests/unit/test_paused_instance_ttl.py (mock drift + quarantine marker)
- Working tree note: .agents/tester/{PACKS.md,QUARANTINE.md} documentation updates are tester-owned and outside repo test commits.

---

### Overall Status
- Unit/Regression Tests: ✅ PASS (10/10 packs)
- Security Matrix: ✅ PASS (26/26 new tests, 0 findings)
- ensure.md: ✅ PASS (Core 4/4 Critical)
- **Testing Complete: ✅ READY — no PM regressions; fail-closed semantics verified; gaps closed with new permanent pack**
