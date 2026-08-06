# Quick Fix: Duplicate `## Speed` Section in Watcher workflow.md

**Date**: 2026-08-05
**File**: `agents/watcher/workflow.md`
**Commit**: `930c3b68`
**Instance**: b64a70e6

## Issue
Static validation (agent definition check) found that `agents/watcher/workflow.md` had a duplicated `## Speed` section. The first occurrence (lines 131-133) was correct and complete. Lines 134-140 contained:
1. A corrupted fragment: `y line of an allow response.` (line 134)
2. An orphan `---` separator (line 136)
3. An identical duplicate `## Speed` section (lines 138-140)

## Root Cause
Copy-paste artifact during initial agent prompt authoring.

## Fix
Deleted lines 134-140 (7 lines), keeping only the correct first Speed section (lines 131-133).
File went from 140 → 133 lines.

## Verification
- Exactly ONE `## Speed` section remains
- Corrupted fragment gone
- All 26 watchover graph tests re-run PASS (0.89s)
- No regressions

## Convention
Violates `docs/agent-prompt-writing-guide.md` §2 canonical-home rule: "If an artifact appears in more than one file, pick one canonical home and make the others link."
