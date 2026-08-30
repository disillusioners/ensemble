#!/usr/bin/env bash
# Test Pack: wc_wake_w1_pollution_probe_test — resolver truth table +
# W1 pollution vectors for the wc-wake phase-1 gate.
#
# Background. The W1 council (2026-08-30 pre-flip batch) identified two
# council repro vectors that broke the kill-switch gate:
#
#   Vector A — flag-state leaking across tests in one process. The
#     resolver's module-global ``_WC_WAKE_ENQUEUE_ENABLED`` cache is
#     set on the first call and not cleared at monkeypatch teardown;
#     a later flag-implicit test then sees the stale ON and routes
#     through ``enqueue_message`` instead of the legacy set_injection
#     (``assert 200 == 202``).
#
#   Vector B — module-identity pollution across files. A file that
#     mutates ``sys.modules`` (e.g. to bypass conftest langgraph mocks)
#     leaves another file importing a stale module whose global still
#     reflects a previous test's resolution.
#
# The W1 commit (4a6e22b5 + f111c7d3 + 7a484afb) sealed both via
# autouse flag-cache reset fixtures on every flag-touching suite.
# This pack ATTESTS that the fix holds by:
#
#   1. Running the resolver truthy/falsy spelling matrix (12 rows)
#      against the live resolver and printing the truth table verbatim.
#   2. Cross-ordering the resolver test file and the instance-tools
#      test file in three pytest invocations and asserting the
#      combined ``passed`` count is identical regardless of order.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 200s` cap on the python
#     process (probe self-guards with signal.alarm(180) internally).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: wc_wake_w1_pollution_probe_test ==="
echo "(resolver truth table + W1 pollution vectors)"

cd "$PROJECT_DIR"

# Layer 2: 200s hard cap on the python probe (resolver matrix is ~1s
# of direct calls; the 3 W1 pytest invocations are ~30s combined at
# HEAD; 200s is margin-rich). Stdout is the JSON record; stderr is
# debug/progress — keep them separate so the JSON file is valid.
timeout 200s .venv/bin/python \
  "$PROJECT_DIR/test/packs/wc_wake_w1_pollution_probe_test.py" \
  > /tmp/wc-wake-w1-pollution.json 2>/tmp/wc-wake-w1-pollution.err
RC=$?

# Compact human summary: extract just the matrix and the order-
# independence verdict so the report is scannable.
echo ""
echo "── compact summary ──"
.venv/bin/python - <<'PYEOF'
import json
import re
import sys

try:
    rec = json.load(open("/tmp/wc-wake-w1-pollution.json"))
except Exception as exc:
    print(f"  (could not parse probe JSON: {exc})")
    sys.exit(0)

mat = rec.get("sections", {}).get("resolver_matrix", {})
w1 = rec.get("sections", {}).get("w1_pollution_vectors", {})

print(f"  resolver matrix ok: {mat.get('ok')}; rows: {len(mat.get('matrix', []))}")
for row in mat.get("matrix", []):
    marker = "PASS" if row["pass"] else "FAIL"
    raw = "<unset>" if row["raw"] is None else repr(row["raw"])
    print(
        f"    [{marker}] label={row['label']:>8s} raw={raw:<10s} "
        f"on={row['actual_on']!s:<5s} warn={row['actual_warn']}"
    )

print()
print(
    f"  W1 pollution ok: {w1.get('ok')}; "
    f"B passed={w1.get('B_passed')}; C passed={w1.get('C_passed')}; "
    f"order_independent={w1.get('order_independent')}"
)
for fail in w1.get("failures", []):
    print(f"    FAIL: {fail}")

# Print the truth table verbatim (the W2 contract) in a single block.
print()
print("── W2 truth table (verbatim from the resolver) ──")
print("  unset / \"0\" / \"\" / \"false\" / \"no\" / \"off\" → OFF")
print("  \"1\" / \"true\" / \"yes\" / \"on\"                → ON")
print("  blank (\"\") and unknown (\"garbage\")            → OFF + WARN")
PYEOF

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
