#!/usr/bin/env bash
# test/packs/upgrade_refusal_message_safety_unit_test.sh
#
# Pack: upgrade_refusal_message_safety
# Scope: the upgrade-script refusal-message quoting family —
#   scripts/upgrade/lib.sh `_verify_tip_identity` refused a non-tip staging
#   tree with a DOUBLE-QUOTED operator message that documented two commands
#   in backticks; bash EXECUTED both while assembling the message
#   (`git fetch origin` fetched; `git push origin <branch>:latest` was
#   re-parsed as `git push origin < branch > :latest` and CREATED a file
#   named `:latest` in the operator's CWD). The suite pins:
#     (a)  the refusal runs NO command and creates NO `:latest` file,
#     (a') the harness DETECTS the defect when the pre-fix quoting is
#          re-introduced (so (a) is provably non-vacuous),
#     (b)  the operator-facing text stays byte-identical to the golden
#          fixture (SHA-masked) — the remediation guidance cannot be gutted,
#     (c)  a static class gate over scripts/upgrade/**.sh.
#   Wraps exactly one suite: tests/test_upgrade_refusal_message_safety.sh
#   (no test deselection, no modification; inner exit code propagates).
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Sandbox: the suite's git fixture lives under mktemp; its "remote" is a local
# bare repo inside the same scratch dir; every git call is fenced with
# GIT_CONFIG_GLOBAL=/dev/null, GIT_CONFIG_SYSTEM=/dev/null and
# GIT_ALLOW_PROTOCOL=file. No live install, service or release dir is touched.

set -u
cd "$(dirname "$0")/../.." || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

PACK_NAME="upgrade_refusal_message_safety"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

OUT="$(mktemp)"
set -o pipefail
timeout 120s bash tests/test_upgrade_refusal_message_safety.sh 2>&1 | tee "$OUT"
RC=$?

SUMMARY="$(grep -E '^[0-9]+ (passed|failed)' "$OUT" | tr '\n' ' ')"
rm -f "$OUT"

echo
if [ -n "$SUMMARY" ]; then echo "SUMMARY: $SUMMARY"; fi

echo
if [ "$RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$RC" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (exit=${RC})"
    exit 1
fi
