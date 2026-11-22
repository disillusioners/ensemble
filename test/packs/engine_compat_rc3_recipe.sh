#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_compat_rc3_recipe
#
# Phase: engine-arc-phase1 P7 — reproduction recipe for the RC=3 signature
# that underpins the ADD-A-MAP-ROW adjudication (planner 6e97cdce2).
#
# WHAT IT PROVES
#   The promote ENTRY gate returns RC=3 (incompatible) when the target
#   release's config-compat contract does NOT tolerate the retired keys that
#   load_config silently pops (T1.12 dead-knob sweep), and RC=0 when it does.
#   Reverting daemon/plugin_subsystem/config_compat_contract.py removes the
#   tolerated-retired-key extension from the minted contract, so an install
#   config.yaml carrying a retired key (e.g. persistence.checkpoint_interval)
#   is refused at entry — the functional regression the adjudication avoids.
#
#   Direction A (WITH-file shape): mint the contract with the extension ->
#     checker on a config.yaml carrying a retired key -> RC=0.
#   Direction B (revert shape, HEAD-minus-tolerated-keys): strip the retired
#     keys from an otherwise valid contract -> checker on the same config ->
#     RC=3 (code=config-incompatible).
#
# CONTRACT (F-21 preamble)
#   - Per-variable env scrub (a `POSTGRES_*` glob is a silent no-op).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned explicitly (never
#     `env -u`).
#   - ENSEMBLE_SELF_ENV=dev, PYTHONDONTWRITEBYTECODE=1.
#   - timeout 280 (inner); caller wraps with 300.
#   - Scratch dir cleaned via trap on exit.
#   - Output: header, per-direction rc, `RESULT: PASS|FAIL`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_compat_rc3_recipe"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PY="$PROJECT_DIR/.venv/bin/python"
MINTER="$PROJECT_DIR/daemon/plugin_subsystem/config_compat_contract.py"
CHECKER="$PROJECT_DIR/daemon/plugin_subsystem/config_compat_check.py"
for f in "$PY" "$MINTER" "$CHECKER"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── per-variable env scrub ──────────────────────────────────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
)

SCRATCH="$(mktemp -d /tmp/eng_compat_rc3_XXXXXX)"
trap 'rm -rf "$SCRATCH"' EXIT

export PYTHONPATH="$PROJECT_DIR"

# Direction A: mint the contract WITH the tolerated-retired-key extension.
echo
echo "--- Direction A: WITH-file shape (contract includes retired keys) ---"
CONTRACT_WITH="$SCRATCH/contract_with.json"
timeout 120 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PY" "$MINTER" --repo-root "$PROJECT_DIR" > "$CONTRACT_WITH" 2>"$SCRATCH/mint.err"
MINT_RC=$?
if [ "$MINT_RC" -ne 0 ]; then
  echo "MINT FAILED (rc=$MINT_RC):"; cat "$SCRATCH/mint.err"
  echo "RESULT: FAIL"
  exit 1
fi
echo "mint rc=$MINT_RC (contract bytes: $(wc -c < "$CONTRACT_WITH"))"

# An install config.yaml carrying a retired key (the regression's trigger).
CONFIG="$SCRATCH/config.yaml"
cat > "$CONFIG" <<'EOF'
persistence:
  checkpoint_interval: 1
services:
  worker_poll_interval: 5
EOF

timeout 60 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PY" "$CHECKER" "$CONTRACT_WITH" "$CONFIG" > "$SCRATCH/with.out" 2>&1
WITH_RC=$?
echo "with-file shape rc=$WITH_RC (expect 0)"
cat "$SCRATCH/with.out"

# Direction B: revert shape — strip the tolerated retired keys (HEAD-minus).
echo
echo "--- Direction B: revert shape (retired keys stripped from contract) ---"
CONTRACT_REVERT="$SCRATCH/contract_revert.json"
"$PY" - "$CONTRACT_WITH" "$CONTRACT_REVERT" <<'PYEOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
contract = json.load(open(src))
retired = {
    "persistence": ("checkpointer_db_path", "checkpoint_interval"),
    "services": ("worker_poll_interval", "lease_heartbeat_interval_seconds"),
}
for section, keys in retired.items():
    node = contract.get(section)
    if isinstance(node, dict):
        for key in keys:
            node.pop(key, None)
json.dump(contract, open(dst, "w"))
PYEOF
STRIP_RC=$?
if [ "$STRIP_RC" -ne 0 ]; then
  echo "STRIP FAILED (rc=$STRIP_RC)"
  echo "RESULT: FAIL"
  exit 1
fi

timeout 60 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PY" "$CHECKER" "$CONTRACT_REVERT" "$CONFIG" > "$SCRATCH/revert.out" 2>&1
REVERT_RC=$?
echo "revert shape rc=$REVERT_RC (expect 3)"
cat "$SCRATCH/revert.out"

# Informational: file-absent shape yields RC=4 (unrunnable, fail-closed) —
# distinct from the RC=3 incompatible signature asserted above.
timeout 60 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PY" "$CHECKER" "$SCRATCH/no_such_contract.json" "$CONFIG" > "$SCRATCH/absent.out" 2>&1
ABSENT_RC=$?
echo
echo "info: file-absent shape rc=$ABSENT_RC (expect 4, fail-closed — distinct from RC=3)"

# ── verdict ──────────────────────────────────────────────────────────────
echo
if [ "$WITH_RC" -eq 0 ] && [ "$REVERT_RC" -eq 3 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL (with=$WITH_RC revert=$REVERT_RC)"
  exit 1
fi
