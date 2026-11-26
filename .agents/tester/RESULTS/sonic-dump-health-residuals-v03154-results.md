# Sonic test results — dump-health-residuals-v03154

TEST: PASS

Pins run (merged tree @ 5accddb6e5c6ee81419f90b3e935de000e2c7b34, base ed8ab508):

- T1 tests/unit/test_dump_lane_staleness_probe.py: 39 passed, 0 failed (0.70s)
- T2 tests/unit/test_pok_gate_prompt_mutations_scratch.py: 5 passed, 0 failed (0.95s)
- T1+T2 combined: 44 passed
- T3 CLI overflow: exit=78, RESULT: lines=1, reason=bad_max_age_hours, tracebacks=0
- T4 interleaved (2 throwaway checkouts): both 4/4 PASS, rc=0; post-sha A==pre (8c2fc809…), B==pre (b9b114e5…), shas differ; scratch dirs left=0
- T5 runbook-coverage node: 1 passed
- T6 real mutation script (merged tree): 4/4 PASS, rc=0; pok_gate.py sha unchanged 8c2fc809…; git status clean
- T7 probe sha=320ea02b…; spelling tuple contains "1e305"

Pre-fix non-vacuity: base (ed8ab508) script raised OverflowError traceback at int(round(1e305*3600)), exit=1. Confirmed.

Residuals (not FAILs):
- PACKS.md row dump_lane_staleness_probe_unit_test pins stale probe sha 9f87040f (now 320ea02b) — owned by coverage lane d3997df2.
- tests/unit/test_pok_gate_prompt_mutations_scratch.py not registered in PACKS.md (same fence).
