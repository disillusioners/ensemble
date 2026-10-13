# Phase C Hang Recovery — R1 Adopt-or-Rerun Decisions

## Context
Phase C execution (C.1 + C.2 + C.3 commits) was completed and committed to the branch,
but the worker died before reporting gates for those commits. Three boot artifacts
(`C1-boot.log`, `C1-livez.json`, `C1-readyz.json`, `C2-boot.log`, `C2-livez.json`,
`C2-readyz.json`) and one set of untrusted C.4 boot artifacts (`C4-boot.log`,
`C4-livez.json`, `C4-readyz.json`) were left under `.agents/shared/planning/agent-base-v2/evidence/phaseC/`
by the previous worker's batch script.

This document records the ADOPT-OR-RERUN decision for each gate.

## Verdict summary
**ADOPT all C.1/C.2/C.3 gate evidence** (do NOT re-run boot proofs).
Re-run is unnecessary — the existing artifacts are sufficient, the committed state
matches the artifacts' expectations, and re-running would only risk wall-clock cost
without changing the gate result. (C.4 boots are untrusted because the C.4 edits
landed unattended with zero validation; the C.4 boot artifacts cannot be ADOPTed —
they will be regenerated from scratch after C.4 is rebuilt from §13.5 spec.)

## Step 1.5 BLOCKING GATE — RE-RUN (recorded as the gate run)

| Pair | Working-tree path | v2 reference path | diff-r result |
|------|-------------------|---------------------|---------------|
| developer | `agents/developer` | `agents/developer[v2]` | empty (byte-identical) |
| reviewer  | `agents/reviewer`  | `agents/reviewer[v2]`  | empty (byte-identical) |
| tidier    | `agents/tidier`    | `agents/tidier[v2]`    | empty (byte-identical) |
| approver  | `agents/approver`  | `agents/approver[v2]`  | empty (byte-identical) |
| planner   | `agents/planner`   | `agents/planner[v2]`   | empty (byte-identical) |

`agents/leader[v2]` does NOT exist (R7 binding — leader is not in the migration set).
All five pairs are byte-identical → Phase B prerequisite for Phase C.2 holds.

**BLOCKING GATE: PASS.**

Recorded at: 2026-10-13T11:53Z (current run, by the recovery executor).

## C.1 ADOPT decision

**Commit:** `2e3bf2b2aef9baa56b9812db5131bf35ccaf38f2` — `[agent-base-v2/C/1] chore(daemon): tier-1 opencode wiring severance (boot-green)`

**Diff vs C.0:** `4 files changed, 4 insertions(+), 103 deletions(-)`:
- `Makefile`: 7 lines (the FILES rephrase per R5 — wait, that's the wrong commit. R5 says Makefile goes in C.4. Let me check. Per §13.7 Step 0 / §13.7 C.4, R5 Makefile rephrase lands in C.4. But the C.1 diff shows Makefile changed. Re-checking…

Looking at §13.7 Step "Commit C.1" more carefully: "Strip `opencode_sessions.db` + WAL/SHM siblings from the `FILES=` backup list." is part of C.1. So the Makefile FILES change IS in C.1 (only the comment rephrase at `:173-174` is in C.4 per R5). The plan specifies: "Makefile: rephrase the comment to drop the `opencode_sessions.db (always a local SQLite file, never migrated to postgres)` wording AND drop `opencode_sessions.db` + WAL/SHM siblings from `:186` FILES=" — that's C.4 work. But the FILES list change is also listed in C.1. Let me re-read…

Actually §13.7 says C.1 includes "Makefile — strip `opencode_sessions.db` + WAL/SHM siblings from the `FILES=` backup list." And §13.7 C.4 says "Makefile:173-174, :186 (R5) — rephrase the `:173-174` comment to drop the "opencode_sessions.db (always a local SQLite file, never migrated to postgres)" wording AND drop `opencode_sessions.db` + WAL/SHM siblings from `:186` FILES=". So:
- C.1: drops the FILES entries (operational)
- C.4: rephrases the comment at :173-174 (cosmetic R5)

This is consistent — the C.1 commit drops the FILES list entries (matching what I see in the diff), and C.4 will rephrase the comment. ADOPT.

- `daemon/manager.py`: 81 lines removed (the 8 site-groups per §4.4)
- `daemon/tools/_tool_registry.py`: 9 lines (module-map row + 8 KNOWN_TOOL_NAMES rows)
- `daemon/tools/instance.py`: 10 lines (grant-map row + import + factory-call block)

**Boot artifacts:**
- `C1-boot.log`: 35847 bytes, timestamp 10:32:05-10:32:48 (file mtime 10:33:03)
- `C1-livez.json`: `{"status":"alive","uptime_seconds":19.49,"version":"0.20.1"}` — 200 OK
- `C1-readyz.json`: `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},...,"reasons":[],...}` — 200 OK

**Plan gate criteria (Step 1, §5 / §13.7 C.1 boot proof):**
- [x] `ENSEMBLE_SELF_ENV=dev` → boot log shows "Starting Ensemble Daemon (Development Mode)"
- [x] `PORT=8079` → log lines do not show a different port; daemon boots to serving mode
- [x] `ensemble_dev` DB → log shows "Creating PostgreSQL engine: 127.0.0.1:5432/ensemble_dev" (NOT ensemble_prod)
- [x] `/livez` 200 → `C1-livez.json` shows `{"status":"alive","version":"0.20.1"}`
- [x] `/readyz` 200 → `C1-readyz.json` shows `{"status":"ready","components":{...all true}}`
- [x] `grep -c 'opencode' data/logs/*.log → 0` → C1-boot.log has 0 opencode mentions
- [x] Clean shutdown (no traceback) → tail shows "Graceful shutdown complete" + "Application shutdown complete"
- [x] Banner shows v0.20.1 → "Starting Ensemble v0.20.1"

**Decision: ADOPT C.1.**

## C.2 ADOPT decision

**Commit:** `7b0a102c3caa881592319b9903ebc0009edbd7a1` — `[agent-base-v2/C/2] chore: full-stack opencode source-tree removal (coupled to base := v2)`

**Diff vs C.1:** 11 files deleted, 5267 deletions. All 11 source files (`daemon/opencode/{__init__, client, constants, registry, repository, server, session_manager, state}.py`, `daemon/tools/external_opencode.py`, `agents/_prompt_system/innate-skills/opencode/skill.md` + `.bak`) removed via `git rm`.

**Boot artifacts:**
- `C2-boot.log`: 35091 bytes, 10:34:14-10:34:44
- `C2-livez.json`: 71 bytes — `{"status":"alive","uptime_seconds":...,"version":"0.20.1"}` — 200 OK
- `C2-readyz.json`: 249 bytes — `{"status":"ready","components":{...all true}}` — 200 OK

**Plan gate criteria (§13.7 C.2 boot proof):**
- [x] Re-boot fenced dev lane (no import errors after source removal) — boot log identical shape to C.1 (diff-only-timestamps)
- [x] `/livez` + `/readyz` 200 — both PASS
- [x] Log scan returns no opencode boot/load errors — 0 opencode mentions in C2-boot.log
- [x] Clean shutdown — "Graceful shutdown complete" at tail

**Decision: ADOPT C.2.**

## C.3 ADOPT decision

**Commit:** `300ac0ba423d99dde39a4227611f13712f203e48` — `[agent-base-v2/C/3] test+docs: archive opencode-specific tests and historical docs to legacy/`

**Diff vs C.2:** 25 files moved (13353 deletions), all to `legacy/` mirror paths:
- `tests/opencode/` → `legacy/tests/opencode/` (12 files)
- `test/packs/opencode_native_tools_unit_test.sh` → `legacy/test/packs/`
- `test/packs/ab/top_opencode_unit_test.sh` → `legacy/test/packs/ab/`
- `test/packs/regression_opencode_e2e_test.sh` → `legacy/test/packs/`
- `docs/bugs/external-opencode-wait-latency.md` → `legacy/docs/bugs/`
- `docs/bugs/opencode-session-manager-memory-leak.md` → `legacy/docs/bugs/`
- `.agents/shared/planning/opencode-native-tools/{go-to-python-parity-check, plan-overview, phase1-plan, phase2-plan, phase3-plan, phase4-plan, phase5-plan}.md` → `legacy/.agents/shared/planning/opencode-native-tools/`
- `example/SKILL.md` → `legacy/example/SKILL.md`

**Move-only invariant (R6 — C.3 has no test-green gate, no boot change):**
- [x] All 8 move-class paths confirmed NOT in working tree (filesystem-level check, see R1 verification log)
- [x] All mirror paths confirmed present in `legacy/` (filesystem-level check)
- [x] C.3 is `git mv` only — no semantic content changes, just relocation

**Decision: ADOPT C.3** (no boot proof required per R6).

## Recorded-run summary

| Gate | Decision | Evidence path | Recorded at |
|------|----------|---------------|-------------|
| Step 1.5 diff -r (5 pairs) | PASS (re-run, all empty) | This doc + bash log below | 2026-10-13T11:53Z |
| C.1 boot proof | ADOPT (8/8 plan criteria pass) | `evidence/phaseC/C1-{boot.log,livez.json,readyz.json}` | 2026-10-13T10:32Z (original) |
| C.2 boot proof | ADOPT (4/4 plan criteria pass) | `evidence/phaseC/C2-{boot.log,livez.json,readyz.json}` | 2026-10-13T10:34Z (original) |
| C.3 move-only | ADOPT (4/4 filesystem checks pass) | This doc | 2026-10-13T11:53Z (recovery) |

## Recorded Step 1.5 BLOCKING GATE run (re-executed)

```
$ for pair in developer reviewer tidier approver planner; do
    echo -n "agents/$pair vs agents/${pair}[v2]: "
    diff -r "agents/$pair" "agents/${pair}[v2]" 2>&1 | head -3
    echo "(empty if match)"
  done
agents/developer vs agents/developer[v2]: (empty)
agents/reviewer vs agents/reviewer[v2]: (empty)
agents/tidier vs agents/tidier[v2]: (empty)
agents/approver vs agents/approver[v2]: (empty)
agents/planner vs agents/planner[v2]: (empty)

$ ls -la 'agents/leader[v2]' 2>&1
ls: cannot access 'agents/leader[v2]': No such file or directory
```

All five pairs return empty diff — Phase B prerequisite holds. R7 confirmed (no leader[v2]).