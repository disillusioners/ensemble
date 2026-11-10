#!/usr/bin/env bash
# RETIRED (skill-system-revival Phase 1, 2026-11-10) — this pack is kept
# as a historical record ONLY.  It executes NOTHING and exits 0; it can
# never redden a future gate.
#
# WHY IT WAS RETIRED — the evidence (all three legs):
#
#   1. STRUCTURAL ROT.  The pack invoked
#      ``tests/unit/test_auto_load_skills.py`` — a file DELETED at
#      ``eeef8845a`` ("refactor: clean up dead code and restore
#      project_injected test coverage").  That deletion PREDATES this
#      lane's base (``ae5bbafed``), so the rot is PRE-EXISTING and
#      not lane-caused.  Under ``set -euo pipefail`` the pack aborted
#      with rc=4 ("file or directory not found") BEFORE it could reach
#      its own "don't fail" branch — and therefore BEFORE it could
#      print ANY ``RESULT:`` line.  A pack that dies before its RESULT
#      line is exactly the registration rot this retirement removes.
#
#   2. ZERO POSTGRES-MARKED COLLECTION.  Even with the missing file
#      restored, the pack ran three SQLite-only test files under
#      ``-m postgres``.  Measured on the repaired shape: pytest reported
#      "no tests collected (47 deselected)" — the marker filter matched
#      NOTHING, so the pack would have verified PostgreSQL not at all.
#      Restoring the dead SQLite unit file would NOT have restored the
#      pack's purpose.
#
#   3. THE PURPOSE IS NOW COVERED — PROPERLY.  The real live-PG
#      verification of the Phase-1 dialect surfaces (the E1 burst
#      guard's PG/SQLite metadata-extraction split, the E4/A4
#      dual-column write, and the A4 within-identity-key divergence
#      gauge) lives in the replacement pack:
#
#          test/packs/skill_revival_pg_dialect_unit_test.sh
#          -> tests/postgres/test_skill_revival_pg_dialect.py
#
#      That pack provisions a DISPOSABLE native PostgreSQL cluster
#      (initdb -A trust on a free private port), runs the leg SERIALLY
#      against it, prints ``SELECT current_database()`` as its pre-run
#      proof, and tears the cluster down.  It is the successor, not a
#      restoration of this file.
#
# The retirement is recorded in ``.agents/tester/PACKS.md`` (the house
# record of pack registrations), and the pack's own row there now points
# at the successor.
#
# NOTE: this script intentionally does NOT invoke pytest.  A retired
# pack must not execute anything, must not touch a database, and must
# not be able to fail — so it prints its reason and exits 0.
set -euo pipefail

echo "=== Test Pack: skill_evolution_pg_test (RETIRED) ==="
echo "RETIRED 2026-11-10 (skill-system-revival Phase 1): structurally rot —"
echo "  1. invoked tests/unit/test_auto_load_skills.py, DELETED at eeef8845a"
echo "     (pre-existing rot; under 'set -euo pipefail' it aborted rc=4"
echo "     BEFORE any RESULT: line was printed);"
echo "  2. even repaired it ran three SQLite-only files under '-m postgres'"
echo "     and collected ZERO postgres-marked tests ('no tests collected"
echo "     (47 deselected)') — it never verified PostgreSQL at all;"
echo "  3. the live-PG coverage now lives in the REPLACEMENT pack:"
echo "       test/packs/skill_revival_pg_dialect_unit_test.sh"
echo "       -> tests/postgres/test_skill_revival_pg_dialect.py"
echo "See .agents/tester/PACKS.md for the registration record."
echo "RESULT: RETIRED (no assertions executed; superseded by skill_revival_pg_dialect_unit_test.sh)"
exit 0
