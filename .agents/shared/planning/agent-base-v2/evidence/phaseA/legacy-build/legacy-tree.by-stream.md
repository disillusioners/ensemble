# Legacy tree partitioned by retirement stream / role (FINAL — addendum-resolved)

Generated post-archive; reflects the BEFORE/AFTER MAP exactly.

## Base-content (5 dirs — pre-overwrite snapshots for Phase B rollback source)
- approver: 6 files under legacy/agents/approver/
- developer: 8 files under legacy/agents/developer/
- planner: 6 files under legacy/agents/planner/
- reviewer: 6 files under legacy/agents/reviewer/
- tidier: 6 files under legacy/agents/tidier/

## Opencode stack — protocol/comm role (server.py + sole consumer shim)
- daemon/opencode/server.py (protocol/comm dispatch)
- daemon/tools/external_opencode.py (sole comm-consumer shim)

## Opencode stack — session/API role (lifecycle + transport + state + repo)
- daemon/opencode/__init__.py
- daemon/opencode/client.py
- daemon/opencode/constants.py
- daemon/opencode/registry.py
- daemon/opencode/repository.py
- daemon/opencode/session_manager.py
- daemon/opencode/state.py

## Shared/infrastructure (cross-role)
- agents/_prompt_system/innate-skills/opencode/skill.md
- agents/_prompt_system/innate-skills/opencode/skill.md.bak

## Tests + test packs
- tests/opencode/ (12 files)
- test/packs/opencode_native_tools_unit_test.sh
- test/packs/ab/top_opencode_unit_test.sh
- test/packs/regression_opencode_e2e_test.sh

## Mission planning history (D-A1 addendum-locked)
- .agents/shared/planning/opencode-native-tools/ (7 .md files)

## Bug docs (opencode-era)
- docs/bugs/opencode-session-manager-memory-leak.md
- docs/bugs/external-opencode-wait-latency.md

## Ported Go-binary contract spec (D-A2 addendum-resolved INCLUDED)
- example/SKILL.md

## Retained-live (boundary-rule exception) — EMPTY per Verdict 2
(none — `boundary-exceptions/` directory created empty as schema future-proofing)
