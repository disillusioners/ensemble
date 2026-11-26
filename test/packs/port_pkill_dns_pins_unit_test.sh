#!/usr/bin/env bash
# =============================================================================
# Test Pack: port_pkill_dns_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `40301a5e3`) (re-verified at this base) — PACKS.md +
#       pack-scripts coverage registration wave, family **A4** (PORT / PKILL /
#       DNS pins).
#
# WHAT IT PINS
#   tests/unit/test_db_resolution_guard.py
#   tests/unit/test_metrics_port_derive.py
#   tests/unit/test_port_ownership_pins.py
#   Total: 58 nodes (mechanical `--collect-only` census at this freeze:
#   58 collected, RC=0, 0 collection errors; per-file 18 / 9 / 31).
#
#   - DB-resolution guard (component B, pin (iii)): the DECIDED polarity is
#     pinned — a guard-internal error is FAIL-CLOSED (exit 78, loud), BOTH
#     boot call sites are pinned, and the documented lifespan exit-3 mapping
#     is pinned structurally.
#   - Derived metrics port (component B, pin (ii)): the derive lives in
#     `daemon/services/metrics_listener.py` as the SINGLE source of truth and
#     carries an explicit edge policy — `main+1 > 65535`, `main_port < 1` and
#     invalid env values are CLAMP-BY-DISABLE (None + loud ERROR), never a
#     silent bad bind.
#   - Port ownership (component A): mutation-proven pins — cross-install claim
#     blindness (B1, a TWO-PROCESS end-to-end pin), the daemon never releases
#     the claim while alive (B3), the refusal marker has a real reader (B5),
#     the gate WIRING is pinned (B7(ii): a live foreign claim exits 78 and the
#     refusal never enters the bind-retry loop), and dev boots never resolve
#     (or write) the canonical LIVE root (N3).
#
# CONTRACT (house shape — mirrors por_b2_wedge_repro_pins_unit_test.sh)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - NO `--ff`: pytest's failed-first ordering is driven by the SHARED
#     `.pytest_cache/lastfailed`, which would couple this pack to concurrent
#     sibling lanes running in the same worktree.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash
#     — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - Repo addopts KEPT (the `-m 'not integration and not postgres ...'`
#     marker filter) so a postgres-marked node can never touch a DB; the
#     census that produced the node count was measured under the SAME filter,
#     so the pack's node count and the census are comparable.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 58 — a pack
#     that silently runs a different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
#
# NEGATIVE-CONTROL HOOKS (the pack must be able to go RED):
#   PACK_SELFTEST=1                    — THE SENTINEL. The two override vars
#     below are honored ONLY when it is set to exactly "1".
#   PORT_PKILL_DNS_PACK_FILES          — space-separated file list override.
#   PORT_PKILL_DNS_PACK_EXPECTED_NODES — expected node count override.
#   Both default to the declared family set; the negative control points them
#   at a scratch, deliberately-failing file OUTSIDE the repo so the script's
#   real RED path is exercised without touching a tracked file.
#   WITHOUT the sentinel: a present override var is IGNORED — the PINNED slice
#   is the gate — and the pack prints exactly ONE loud WARNING line. A stray
#   export can never mask census drift, and it never FAILS the pack.
# =============================================================================
set -u

PACK_NAME="port_pkill_dns_pins_unit_test"
EXPECTED_NODES=58

DEFAULT_FILES="tests/unit/test_db_resolution_guard.py tests/unit/test_metrics_port_derive.py tests/unit/test_port_ownership_pins.py"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "FATAL: .venv/bin/python not found at $PYTHON_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

# ── the file set (SENTINEL-GATED override for the negative control) ─────
# The two override vars are honored ONLY when PACK_SELFTEST=1 is ALSO set.
# Without the sentinel they are IGNORED (the PINNED slice is the gate) — a
# stray export can never mask census drift.
SELF_TEST_ARMED=0
if [ "${PACK_SELFTEST:-}" = "1" ]; then
  SELF_TEST_ARMED=1
fi

UNIT_FILES=()
if [ "$SELF_TEST_ARMED" -eq 1 ]; then
  if [ -n "${PORT_PKILL_DNS_PACK_FILES:-}" ]; then
    read -r -a UNIT_FILES <<< "${PORT_PKILL_DNS_PACK_FILES}"
  else
    read -r -a UNIT_FILES <<< "$DEFAULT_FILES"
  fi
else
  if [ -n "${PORT_PKILL_DNS_PACK_FILES+x}" ] || [ -n "${PORT_PKILL_DNS_PACK_EXPECTED_NODES+x}" ]; then
    echo "WARNING: PORT_PKILL_DNS_PACK_FILES/PORT_PKILL_DNS_PACK_EXPECTED_NODES set without PACK_SELFTEST=1 — overrides IGNORED, running the pinned family slice"
  fi
  read -r -a UNIT_FILES <<< "$DEFAULT_FILES"
fi
if [ "${#UNIT_FILES[@]}" -eq 0 ]; then
  echo "FATAL: empty test file list" >&2
  echo "RESULT: FAIL"
  exit 1
fi

if [ "$SELF_TEST_ARMED" -eq 1 ] && [ -n "${PORT_PKILL_DNS_PACK_EXPECTED_NODES:-}" ]; then
  EXPECTED_NODES="${PORT_PKILL_DNS_PACK_EXPECTED_NODES}"
fi

echo "declared file set (${#UNIT_FILES[@]} file(s)), expected nodes: ${EXPECTED_NODES}"

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (never a POSTGRES_* glob) ─────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_BASH_TRACKING_ID
  -u ENSEMBLE_SELF_ENV
)

# NORMALISE, never refuse: the host's ambient ENSEMBLE_SELF_ENV is `live` (measured
# on this host). A pack that REFUSED on the parent's ambient value would refuse to
# start for every tester — every PACKS.md Invocation cell runs
# `cd $WT && timeout 300 bash test/packs/<script>.sh` with NO prefix. So the pack
# normalises to `dev` (the value its own legs require) and the assert below can only
# fire if a future edit drops THIS export — which is exactly the drift it exists to
# catch.
export ENSEMBLE_SELF_ENV=dev

# FENCE (self-verifying): the legs below force ENSEMBLE_SELF_ENV=dev, so the child can
# never inherit the host's ambient value (measured `live` on this host). This assert makes
# that guarantee LOUD — if a future edit drops the normalising export, the pack refuses
# rather than silently running against the wrong environment.
if [ "${ENSEMBLE_SELF_ENV:-}" != "dev" ]; then
  echo "FENCE FAILURE: ENSEMBLE_SELF_ENV='${ENSEMBLE_SELF_ENV:-<unset>}' — this pack requires 'dev'." >&2
  echo "RESULT: FAIL (env fence)"
  exit 1
fi

# ── the SLICING PROOF (node-count parity vs the declared census) ────────
echo
echo "--- slicing proof: --collect-only over the declared file set ---"
COLLECT_OUT="$(mktemp)"
timeout 120 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m pytest \
    -p no:randomly \
    -p no:xdist \
    --collect-only \
    -q \
    --color=no \
    "${UNIT_FILES[@]}" > "$COLLECT_OUT" 2>&1
COLLECT_EXIT="$?"
COLLECTED="$(grep -oE '^[0-9]+ tests? collected' "$COLLECT_OUT" | head -1 | grep -oE '^[0-9]+')"

if [ "$COLLECT_EXIT" -ne 0 ]; then
  echo "SLICING PROOF: FAIL (collection exit=$COLLECT_EXIT)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ -z "$COLLECTED" ]; then
  echo "SLICING PROOF: FAIL (VACUOUS — no 'collected' summary line)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ "$COLLECTED" -ne "$EXPECTED_NODES" ]; then
  echo "SLICING PROOF: FAIL (node-count drift: expected $EXPECTED_NODES, collected $COLLECTED)"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
echo "SLICING PROOF: OK ($COLLECTED nodes collected, expected $EXPECTED_NODES)"
rm -f "$COLLECT_OUT"

echo
echo "--- family A4: DB-resolution guard / derived metrics port / port-ownership pins ---"
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_TIMEOUT=280 \
  "$PYTHON_BIN" -m pytest \
    -q \
    -p no:randomly \
    -p no:xdist \
    --tb=short \
    --color=no \
    --override-ini="timeout=120" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
# NOTE: under `-q` the summary line STARTS with the count ("58 passed, ..."),
# so the extraction anchors on the TOKEN, not on a preceding non-digit.
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

# NOTE (ordering): the TIMEOUT verdict is checked FIRST, before the
# vacuous-verdict guard. A killed pytest produces NO summary line at all,
# so the vacuous guard would fire and report `RESULT: FAIL` (exit 1) for a
# genuine timeout — and the pack contract's `RESULT: TIMEOUT` / exit 124
# would be unreachable. Timeout is a distinct verdict and must win.
if [ "$PYTEST_EXIT" -eq 124 ]; then
  echo
  echo "RESULT: TIMEOUT"
  exit 124
fi

if [ -z "$SUMMARY" ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — pytest produced NO summary: the suite did not run)"
  exit 1
fi
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — zero tests passed: '${SUMMARY}')"
  exit 1
fi
echo "SUMMARY: ${SUMMARY}"

case "$PYTEST_EXIT" in
  0)
    echo "RESULT: PASS"
    exit 0
    ;;
  124)
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
