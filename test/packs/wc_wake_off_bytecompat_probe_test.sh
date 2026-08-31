#!/usr/bin/env bash
# Test Pack: wc_wake_off_bytecompat_probe_test — OFF-state byte-compat proof.
#
# Background. wc-wake-report-integrity introduces the
# ``ENSEMBLE_WC_WAKE_ENQUEUE`` kill-switch (default OFF). Per
# decisions.md D2.5-FLIP / C2-D2.5-FLIP, OFF is the instant-revert
# path: the operator must be able to flip OFF mid-incident and recover
# the exact pre-branch behavior without a code change. This pack
# ATTESTS that OFF behavior is byte-faithful to base 1f8f8ed4 across
# all three routing sites (HTTP POST /messages, agent-tool
# ``send_message``, ``job_inject``) on a WAITING_CHILDREN target.
#
# Method (mirrors ``base-evidence attribution via worktree A/B pytest
# comparison`` skill):
#
#   1. Create a git worktree pinned at base ``1f8f8ed4`` in a temp
#      directory (``$(mktemp -d)/wcbase-<pid>``).
#   2. Run the byte-compat probe against HEAD's daemon — output to
#      ``head.json``.
#   3. Run the same probe against the base worktree's daemon — output
#      to ``base.json``.
#   4. For each of the 4 captured sites (HTTP, agent_tool, job_inject
#      on WC, job_inject on IDLE), assert byte-identity between the two
#      JSON records. ANY diff is a byte-compat regression.
#   5. Print the byte-restored legacy error string (the
#      ``job_inject_idle`` site's ``error`` value) verbatim.
#
# Cross-tree import feasibility (the explicit fallback the task lists):
#   The probe's Python entry point forces ``sys.path.insert(0,
#   $DAEMON_BYTECOMPAT_ROOT)`` AND clears any cached ``daemon`` modules
#   BEFORE the import. This is proven sufficient against the venv's
#   editable-install .pth (the .pth adds the main-repo path AFTER
#   ``sys.path[0]``; PYTHONPATH alone is NOT sufficient — confirmed
#   empirically during pack development). If the resolution proof
#   fails for any run, the probe exits non-zero and the pack fails
#   loud (no silent fallback).
#
# Flag-state matrix per site (task spec):
#   ENSEMBLE_WC_WAKE_ENQUEUE unset (default OFF)
#   ENSEMBLE_WC_WAKE_ENQUEUE=0   (explicit OFF — must match unset)
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` cap on the python
#     process (probe self-guards with signal.alarm(280) internally).
#
# Exit codes (per test-pack skill):
#   0   PASS (every captured site byte-identical HEAD vs base)
#   1   FAIL (any site differs OR resolution proof failed)
#   124 TIMEOUT (per-run `timeout 280s` tripped OR inner alarm)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: wc_wake_off_bytecompat_probe_test ==="
echo "(OFF-state byte-compat proof — HEAD vs base 1f8f8ed4)"

cd "$PROJECT_DIR"

# Sanity: git worktree, the base commit, and the venv must exist.
command -v git >/dev/null || { echo "git not found"; exit 1; }
[ -x .venv/bin/python ] || { echo ".venv/bin/python missing"; exit 1; }
git -C "$PROJECT_DIR" cat-file -t 1f8f8ed4 >/dev/null 2>&1 || {
  echo "base commit 1f8f8ed4 not present in repo"; exit 1;
}

# Worktree location: a fresh tmp dir. trap removes the worktree on exit
# (success or failure) so the main checkout stays clean.
WT_DIR="$(mktemp -d -t wcbase.XXXXXX)"
cleanup() {
  rc=$?
  if [ -d "$WT_DIR" ]; then
    echo ""
    echo "── cleaning up worktree at $WT_DIR ──"
    git -C "$PROJECT_DIR" worktree remove --force "$WT_DIR" 2>&1 || true
    rm -rf "$WT_DIR"
  fi
  exit $rc
}
trap cleanup EXIT INT TERM

echo "── creating worktree at base 1f8f8ed4 → $WT_DIR ──"
git -C "$PROJECT_DIR" worktree add "$WT_DIR" 1f8f8ed4 --detach >/dev/null

# Output artifacts (captured per run + diff).
OUT_HEAD="${OUT_DIR:-/tmp}/wc-bytecompat-head.json"
OUT_BASE="${OUT_DIR:-/tmp}/wc-bytecompat-base.json"
mkdir -p "$(dirname "$OUT_HEAD")"

PY="$PROJECT_DIR/.venv/bin/python"

# ── Run 1: HEAD repo, flag UNSET ────────────────────────────────────────────
echo ""
echo "── run 1/2: HEAD repo, ENSEMBLE_WC_WAKE_ENQUEUE=<unset> ──"
unset ENSEMBLE_WC_WAKE_ENQUEUE
DAEMON_BYTECOMPAT_ROOT="$PROJECT_DIR" \
  timeout 280s "$PY" "$PROJECT_DIR/test/packs/wc_wake_off_bytecompat_probe_test.py" \
  >"$OUT_HEAD" 2>&1
rc_head_unset=$?
echo "(rc=$rc_head_unset, output → $OUT_HEAD)"

# ── Run 2: base worktree, flag UNSET ────────────────────────────────────────
echo ""
echo "── run 2/4: base worktree, ENSEMBLE_WC_WAKE_ENQUEUE=<unset> ──"
unset ENSEMBLE_WC_WAKE_ENQUEUE
DAEMON_BYTECOMPAT_ROOT="$WT_DIR" \
  timeout 280s "$PY" "$PROJECT_DIR/test/packs/wc_wake_off_bytecompat_probe_test.py" \
  >"$OUT_BASE" 2>&1
rc_base_unset=$?
echo "(rc=$rc_base_unset, output → $OUT_BASE)"

# ── Run 3: HEAD repo, flag=0 ────────────────────────────────────────────────
echo ""
echo "── run 3/4: HEAD repo, ENSEMBLE_WC_WAKE_ENQUEUE=0 ──"
export ENSEMBLE_WC_WAKE_ENQUEUE=0
DAEMON_BYTECOMPAT_ROOT="$PROJECT_DIR" \
  timeout 280s "$PY" "$PROJECT_DIR/test/packs/wc_wake_off_bytecompat_probe_test.py" \
  >"${OUT_HEAD}.zero" 2>&1
rc_head_zero=$?
echo "(rc=$rc_head_zero, output → ${OUT_HEAD}.zero)"
mv "${OUT_HEAD}.zero" "$OUT_HEAD"

# ── Run 4: base worktree, flag=0 ────────────────────────────────────────────
echo ""
echo "── run 4/4: base worktree, ENSEMBLE_WC_WAKE_ENQUEUE=0 ──"
export ENSEMBLE_WC_WAKE_ENQUEUE=0
DAEMON_BYTECOMPAT_ROOT="$WT_DIR" \
  timeout 280s "$PY" "$PROJECT_DIR/test/packs/wc_wake_off_bytecompat_probe_test.py" \
  >"${OUT_BASE}.zero" 2>&1
rc_base_zero=$?
echo "(rc=$rc_base_zero, output → ${OUT_BASE}.zero)"
mv "${OUT_BASE}.zero" "$OUT_BASE"

# ── Validate probes succeeded and resolution proofs hold ────────────────────
ALL_RC=$(( rc_head_unset + rc_base_unset + rc_head_zero + rc_base_zero ))
if [ "$ALL_RC" -ne 0 ]; then
  echo ""
  echo "RESULT: FAIL — one or more probe runs returned non-zero"
  echo "  rc_head_unset=$rc_head_unset  rc_base_unset=$rc_base_unset"
  echo "  rc_head_zero=$rc_head_zero   rc_base_zero=$rc_base_zero"
  echo ""
  echo "── HEAD output ──"
  cat "$OUT_HEAD" || true
  echo ""
  echo "── base output ──"
  cat "$OUT_BASE" || true
  exit 1
fi

# ── Byte-comparisons: per-site, per-flag-state ──────────────────────────────
#
# Use Python's json + per-site deep-diff so structural equivalence is
# asserted exactly (a value-level byte diff). Fail loud on any diff.

"$PY" - "$OUT_HEAD" "$OUT_BASE" <<'PYEOF'
import json
import sys

if len(sys.argv) != 3:
    print("usage: byte-diff.py <head.json> <base.json>", file=sys.stderr)
    sys.exit(2)

head = json.load(open(sys.argv[1]))
base = json.load(open(sys.argv[2]))

sites = ("http", "agent_tool", "job_inject_wc", "job_inject_idle")
fails = []

for site in sites:
    h = head["sites"].get(site)
    b = base["sites"].get(site)
    if h == b:
        print(f"  [BYTE-IDENTICAL] {site}")
    else:
        print(f"  [DIFF] {site}:")
        print(f"    HEAD : {json.dumps(h, default=str)}")
        print(f"    BASE : {json.dumps(b, default=str)}")
        fails.append(site)

# Print the legacy eligibility error string verbatim (the byte-restored
# deliverable the task explicitly calls out).
legacy_err = head["sites"]["job_inject_idle"].get("error")
print()
print("── legacy eligibility error string (HEAD vs base) ──")
if head["sites"]["job_inject_idle"].get("error") == base["sites"]["job_inject_idle"].get("error"):
    print(f"  IDENTICAL: {legacy_err!r}")
else:
    print(f"  HEAD : {head['sites']['job_inject_idle'].get('error')!r}")
    print(f"  BASE : {base['sites']['job_inject_idle'].get('error')!r}")
    fails.append("legacy_eligibility_error_string")

if fails:
    print(f"\nRESULT: FAIL — byte-compat broken on: {', '.join(fails)}")
    sys.exit(1)
print("\nRESULT: PASS — every site byte-identical HEAD vs base")
PYEOF
RC=$?

if [ "$RC" -eq 0 ]; then
  echo ""
  echo "(full HEAD JSON: $OUT_HEAD)"
  echo "(full base JSON: $OUT_BASE)"
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
