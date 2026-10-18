#!/usr/bin/env bash
# E2E journey BOOT stage — live-view-passcode (2026-10-18).
# Adapted from the PROVEN evidence/state-b-dry-run.sh. Deltas ONLY:
#   * port 8081 (task-mandated; verified free)
#   * unique root name journeyroot1 (+ explicit tmp-images root) — fresh
#     rows guaranteed in the shared dev DB
#   * leaves the daemon RUNNING on exit (cleaned by cleanup.sh)
#   * mints a self-signed cert for the local TLS edge (browser lane)
# FENCING: all mutable state under one mktemp root; escape-guarded;
# never resolves live-shaped/HOME-derived install paths; never touches
# the pre-existing dirty file .agents/shared/planning/designer-agent/.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../journey/scripts
EV="$(cd "$HERE/.." && pwd)"                            # .../journey
WT="$(cd "$HERE/../../../../../../.." && pwd)"          # worktree root
if [ ! -f "$WT/.env" ] || [ ! -d "$WT/daemon" ]; then
  echo "FENCE FAIL: worktree resolution landed on $WT (no .env/daemon)"; exit 99
fi
echo "== worktree: $WT"
cd "$WT"

# ── 0. fence ─────────────────────────────────────────────────────────────
DRILL_TMP="$(mktemp -d /tmp/lvjourney.XXXXXX)"
case "$DRILL_TMP" in /tmp/lvjourney.*) ;; *) echo "FENCE FAIL: $DRILL_TMP"; exit 99;; esac

# ── 1. fenced env ────────────────────────────────────────────────────────
set -a; source "$WT/.env"; set +a

# MANDATORY pre-boot assert (task binding): dev DB only.
if [ "${POSTGRES_DB:-}" != "ensemble_dev" ]; then
  echo "ABORT: POSTGRES_DB='${POSTGRES_DB:-<unset>}' != ensemble_dev"; exit 91
fi
echo "== pre-boot assert OK: POSTGRES_DB=ensemble_dev"

export DAEMON_PORT=8081
# config.yaml substitutes ${PORT:-8088} for daemon.port — ambient PORT in a
# shell must NOT leak (this run's shell carries PORT=9797). Pin both knobs.
export PORT=8081
export HOST=0.0.0.0
export ENSEMBLE_DATA_DIR="$DRILL_TMP/data"
mkdir -p "$ENSEMBLE_DATA_DIR"

FERNET_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export SYSTEM_ENCRYPTION_KEY="$FERNET_KEY"

# AUTH-ON State-B shape (proven); anonymous_view_paths UNSET.
export ENSEMBLE_AUTH_ENABLED=true
export ENSEMBLE_AUTH_CLIENT_ID=drill-client
export ENSEMBLE_AUTH_CLIENT_SECRET=drill-secret
export ENSEMBLE_AUTH_ISSUER=https://drill.invalid
export ENSEMBLE_AUTH_ALLOWED_ORIGINS="http://127.0.0.1:8081 https://127.0.0.1:8443"

# Drill-local roots: UNIQUE root journeyroot1 + the tmp-images substrate.
VIEWS_ROOT="$DRILL_TMP/roots/journeyroot1"
mkdir -p "$VIEWS_ROOT"
ROOTS_JSON="$(printf '{"journeyroot1": {"type": "filesystem", "path": "%s"}, "tmp-images": {"type": "tmp_images"}}' "$VIEWS_ROOT")"
export ENSEMBLE_LIVE_VIEWS_ROOTS="$ROOTS_JSON"

# ── 2. fixtures ──────────────────────────────────────────────────────────
cat > "$VIEWS_ROOT/hello.md" <<'EOF'
# Journey Hello

A **markdown** live view for the passcode E2E customer journey.

- bullet one
- bullet two

Inline `code` and a [link](https://example.com).
EOF
cat > "$VIEWS_ROOT/diagram.mmd" <<'EOF'
flowchart TD
    A[Customer] -->|opens link| B{Passcode gate}
    B -->|correct code| C[View served]
    B -->|wrong code| D[Rejected]
    D --> B
EOF
echo "== fixtures written: hello.md diagram.mmd"

# ── 3. port-free check (8081 only) ───────────────────────────────────────
if ss -ltn "sport = :8081" | grep -q LISTEN; then echo "PORT 8081 NOT FREE — aborting"; exit 98; fi
echo "== port 8081 free"

# ── 4. self-signed cert for the local TLS edge (browser lane) ────────────
.venv/bin/python - "$DRILL_TMP" <<'PY'
import sys, datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import ipaddress
tmp = sys.argv[1]
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
open(f"{tmp}/edge-key.pem","wb").write(key.private_bytes(serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
open(f"{tmp}/edge-cert.pem","wb").write(cert.public_bytes(serialization.Encoding.PEM))
print("== edge cert minted")
PY
[ $? -eq 0 ] || { echo "CERT MINT FAILED"; exit 96; }

# ── 5. state file (lives INSIDE the fence; evidence dir gets a pointer) ──
cat > "$DRILL_TMP/state.env" <<EOF
DRILL_TMP='$DRILL_TMP'
PORT=8081
EDGE_PORT=8443
FERNET_KEY='$FERNET_KEY'
ROOTS_JSON='$ROOTS_JSON'
ENSEMBLE_DATA_DIR='$ENSEMBLE_DATA_DIR'
VIEWS_ROOT='$VIEWS_ROOT'
EOF
echo "$DRILL_TMP" > "$EV/.drill-tmp"

# ── 6. bounded boot ──────────────────────────────────────────────────────
BOOT_LOG="$EV/boot.log"
: > "$BOOT_LOG"
setsid .venv/bin/python -m daemon >> "$BOOT_LOG" 2>&1 &
BOOT_PID=$!
echo "$BOOT_PID" > "$DRILL_TMP/boot-pid"
echo "== booting daemon pid=$BOOT_PID (log: journey/boot.log)"

UP=0
for i in $(seq 1 90); do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:8081/views/livez"; then UP=1; break; fi
  if ! kill -0 "$BOOT_PID" 2>/dev/null; then echo "== BOOT DIED after ${i}s"; break; fi
  sleep 1
done
if [ "$UP" != "1" ]; then
  echo "== daemon did not come up in 90s — tail of boot log:"
  tail -40 "$BOOT_LOG"
  exit 97   # cleanup.sh handles shutdown; do not auto-kill here (idempotent guard there)
fi
echo "== daemon UP (waited ${i}s)"

# Corroborating boot-log lines (public path set + substrate)
grep -m1 "derived public_path_set" "$BOOT_LOG" || echo "WARN: public_path_set line not found"
grep -m1 "passcode substrate ready" "$BOOT_LOG" || echo "WARN: substrate line not found"
echo "== BOOT STAGE COMPLETE (daemon left running; cleanup.sh owns shutdown)"
