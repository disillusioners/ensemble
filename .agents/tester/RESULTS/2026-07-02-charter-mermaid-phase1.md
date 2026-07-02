# Test Report: Charter Agent + Mermaid Chart Support — Phase 1

**Date:** 2026-07-02
**Branch:** `feature/charter-mermaid-support`
**Target Commit:** `9fa6303d` (HEAD at test time: `3ab80032`, frontend-only +1)
**Sessions:**
- `charter-p1-backend-tests` (ses_0dcf8da35ffebExE5ihu5oREWp)
- `charter-p1-config-verify` (ses_0dcf8da42ffeEHjICx323VDpIx)

---

## Summary

| Area | Tests/Checks | Result |
|------|-------------|--------|
| Backend Tests | 40 tests (27 + 13) | ✅ ALL PASS |
| Charter Agent Registration | 5 checks | ✅ ALL PASS |
| INNATE_SKILL_TOOL_CATEGORIES | 3 checks | ✅ ALL PASS |
| Agent meta.json Updates | 7 agents | ✅ ALL PASS |
| Chart Skill File | 3 checks | ✅ ALL PASS |

**Overall Status: ✅ READY — Phase 1 complete, no failures.**

---

## 1. Backend Python Tests

### test_spawn_team_members.py
- **Total: 27 | Passed: 27 | Failed: 0 | Errors: 0 | Skipped: 0**
- Expected: 27 tests — **MATCH: YES**

### test_innate_skills_refactoring.py
- **Total: 13 | Passed: 13 | Failed: 0 | Errors: 0 | Skipped: 0**
- Expected: 13 tests — **MATCH: YES**

**Pre-existing failures noted:** None.
**Warnings (non-fatal):** PytestConfigWarning (timeout option), DeprecationWarning (asyncio.iscoroutinefunction), UserWarning (Pydantic V1 / Python 3.14). None affect outcomes.

---

## 2. Charter Agent Registration (5/5 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| `agents/charter/` exists with meta.json | ✅ PASS | Files: meta.json, soul.md, rule.md, workflow.md |
| All required fields in meta.json | ✅ PASS | id=charter, name=Charter, description, version=1.0.0, tools |
| `innate_skills: []` (empty) | ✅ PASS | `"innate_skills": []` — charter is maker not user |
| `team_members: ["explorer"]` | ✅ PASS | `"team_members": ["explorer"]` |
| Discoverable via registry | ✅ PASS | `get_registry()` discovers charter; innate_skills=[], team_members=['explorer'] |

**Note:** Charter uses soul.md/rule.md/workflow.md (modern schema), NOT prompt.md. This matches `_baby_template/` and loader.py expectations.

---

## 3. INNATE_SKILL_TOOL_CATEGORIES Mapping (3/3 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| `"chart": ["instance"]` present | ✅ PASS | `instance.py:52-55` — alongside existing `"opencode": ["external_opencode"]` |
| `expand_allow_for_innate_skills()` expands "chart" | ✅ PASS | Tested: `["chart"]` → `["chart", "instance"]` |
| Developer gets BOTH categories | ✅ PASS | See below |

**Developer agent expanded tools.allow:**
- Raw: `['bash','filesystem','time','self','help','knowledge','mcp','context','db']`
- innate_skills: `['opencode','chart']`
- **Expanded:** `['bash','filesystem','time','self','help','knowledge','mcp','context','db','external_opencode','instance']`
- `external_opencode` present: ✅ True
- `instance` present: ✅ True

---

## 4. Agent meta.json Updates (7/7 PASS)

| Agent | innate_skills includes "chart" | team_members includes "charter" | Result |
|-------|-------------------------------|--------------------------------|--------|
| leader | N/A (coordination only) | `["planner","developer","reviewer","tidier","approver","tester","giter","devops","explorer","charter"]` | ✅ PASS |
| developer | `["opencode","chart"]` | `["explorer","charter"]` | ✅ PASS |
| tester | `["opencode","chart","test-pack"]` | `["explorer","charter"]` | ✅ PASS |
| planner | `["opencode","chart"]` | `["explorer","charter"]` | ✅ PASS |
| reviewer | `["opencode","chart"]` | `["explorer","charter"]` | ✅ PASS |
| tidier | `["opencode","chart"]` | `["explorer","charter"]` | ✅ PASS |
| approver | `["opencode","chart"]` | `["explorer","charter"]` | ✅ PASS |

---

## 5. Chart Skill File (3/3 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| skill.md exists & readable | ✅ PASS | 5178 bytes, 93 lines, `# Chart Skill` heading |
| Contains charter spawn instructions | ✅ PASS | Mentions charter, mermaid, diagram, spawn. Includes spawn_instance example |
| Loadable by skill loader | ✅ PASS | `load_agent_skills()` returns chart skill for developer; charter returns {} (innate_skills=[]) |

---

## Contextual Notes (Not Failures)

1. **Commit offset:** HEAD (`3ab80032`) is 1 commit ahead of target (`9fa6303d`). The intervening commit is frontend-only (mermaid.js rendering + charter color). No impact on the 5 verified areas. Verified via `git diff --stat`.

2. **No prompt.md:** Charter uses the modern soul.md/rule.md/workflow.md schema (matching `_baby_template/`). The loader does not require prompt.md.

3. **Pre-existing security gap (out of scope):** `send_message` and `terminate_instance` tools lack team-membership authorization checks — any agent with "instance" category access can message/terminate ANY instance. This is a pre-existing design issue, NOT introduced by the chart feature. Worth tracking as follow-up.

---

## Quick Fixes Applied
None needed — all tests and checks passed on first run.

---

## Conclusion
Phase 1 of the Charter Agent + Mermaid Chart Support feature is fully verified. All 40 backend tests pass, and all 18 configuration checks pass across 5 areas. No blockers for proceeding to subsequent phases.
