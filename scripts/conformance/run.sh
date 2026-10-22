#!/usr/bin/env bash
# The remote-hands CONFORMANCE KIT entry point (SPEC §10 area runner).
#
#   ./scripts/conformance/run.sh                                # self-consistency (in-kit center)
#   CONFORMANCE_CENTER_URL=wss://host:port/api/mcps-gateway/ws \
#   CONFORMANCE_TOKEN=... CONFORMANCE_CA_PEM=/path/ca.pem \
#   CONFORMANCE_MACHINE_ID=... CONFORMANCE_EXPECTED_AGENT=... \
#     ./scripts/conformance/run.sh                              # pointed at YOUR center
#
# The five pointing keys (+ the two begin-hook keys and the strict knob —
# eight in total, pinned by the kit) are documented in README.md.
#
# WHY --confcutdir=tests/conformance IS LOAD-BEARING: the repo-root
# conftest installs an mcp-SDK ``sys.modules`` mock and an autouse
# ``State`` patch.  The kit must not inherit the daemon lane's
# scaffolding — with the cutdir, none of the root conftest is collected.
#
# Teardown: the kit is IN-PROCESS (the self-host center lives inside the
# pytest process), so the trap kills the pytest process (and, when the
# operator's begin hook spawns an external center, its process group) on
# TERM/INT/EXIT.  Nothing is left listening.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1

RUN_TIMEOUT="${CONFORMANCE_RUN_TIMEOUT:-600}"

if [[ -n "${CONFORMANCE_CENTER_URL:-}" ]]; then
  echo "conformance kit: POINTED at ${CONFORMANCE_CENTER_URL}"
  if [[ -z "${CONFORMANCE_BEGIN_CMD:-}" && -z "${CONFORMANCE_BEGIN_URL:-}" ]]; then
    echo "conformance kit: NO begin hook — the session-dependent areas will FAIL FAST (B8a)."
    echo "                 Machine-facing areas run normally. See README.md 'Begin hook'."
  fi
else
  echo "conformance kit: SELF-CONSISTENCY MODE — no CONFORMANCE_CENTER_URL set."
  echo "                 The kit boots its own IN-KIT reference center and tests ITSELF."
  echo "                 This proves the kit, NOT your center. Point it with CONFORMANCE_CENTER_URL."
fi

# The house invocation: uv run python -m pytest (bare pytest is broken in this repo).
ARGS=(tests/conformance -p no:cacheprovider --confcutdir=tests/conformance -m conformance)

timeout "$RUN_TIMEOUT" uv run python -m pytest "${ARGS[@]}" "$@" &
pytest_pid=$!

cleanup() {
  local rc=$?
  # Kill the whole pytest process group when we own it (the begin hook's
  # spawned center, if any, lives under it).
  kill -- "-${pytest_pid}" 2>/dev/null || kill "${pytest_pid}" 2>/dev/null || true
  wait "${pytest_pid}" 2>/dev/null || true
  # Prove nothing the run owned is still listening (best effort — the
  # self-host center is in-process and dies with pytest).
  if command -v lsof >/dev/null 2>&1 && kill -0 "${pytest_pid}" 2>/dev/null; then
    lsof -p "${pytest_pid}" -iTCP -sTCP:LISTEN 2>/dev/null || true
  fi
  exit "${rc}"
}
trap cleanup INT TERM EXIT

wait "${pytest_pid}"
rc=$?
trap - INT TERM EXIT
exit "${rc}"
