#!/usr/bin/env bash
# ============================================================================
# Secret-scan gate — agent-base-v2 commission (S-3 + EXP-4, 2026-10-13).
#
# Fail-closed: ANY sk-(proj|ant)- occurrence in commission-controlled
# artifact paths => exit 1. Clean => exit 0.
#
# Secret hygiene by construction: this gate prints file paths + occurrence
# COUNTS only — never matched lines.
#
# Scanned surface (EXP-2/EXP-4 scope):
#   (a) .agents/shared/planning/agent-base-v2/  (planning dir incl. evidence/)
#   (c) .agents/tester/                          (verified commission-clean)
#   (d) the 2 foreign runtime writes (inspected, NOT git-added)
#   (b) /tmp commission scratch (c4-gate-*.log, agent-base-v2-*)
# /tmp targets are skipped (noted) when absent — scratch may be wiped.
#
# Self-exclusion: this script and secret-scan-output.txt contain the
# pattern literal by necessity and are excluded from scanning.
# ============================================================================
set -u

PATTERN='sk-(proj|ant)-'
REPO="${ENSEMBLE_WT_ROOT:-/home/nea/ensemble-src-wt-agent-base-v2}"

DIR_TARGETS=(
    "$REPO/.agents/shared/planning/agent-base-v2"
    "$REPO/.agents/tester"
)
FILE_TARGETS=(
    "$REPO/.agents/shared/planning/designer-agent/install-audit.jsonl"
    "$REPO/.agents/tidier/notes.md"
    /tmp/c4-gate-boot.log
    /tmp/c4-gate-boot2.log
    /tmp/c4-gate-boot3.log
    /tmp/c4-gate-full.log
    /tmp/c4-gate-round2-tail.log
    /tmp/c4-gate-xdist.log
    /tmp/agent-base-v2-fakehome.txt
    /tmp/agent-base-v2-phase-b-start
)

EXCLUDES=(--exclude=secret-scan.sh --exclude=secret-scan-output.txt)
rc=0

echo "== secret-scan gate (pattern: $PATTERN) =="

for d in "${DIR_TARGETS[@]}"; do
    if [ ! -d "$d" ]; then
        echo "SKIP (missing dir): $d"
        continue
    fi
    n=$(timeout 300 grep -r -a -o -E "${EXCLUDES[@]}" -- "$PATTERN" "$d" 2>/dev/null | wc -l)
    if [ "$n" -gt 0 ]; then
        echo "FAIL: $d ($n occurrence(s))"
        timeout 300 grep -r -a -l -E "${EXCLUDES[@]}" -- "$PATTERN" "$d" 2>/dev/null | sed 's/^/  hit-file: /'
        rc=1
    else
        echo "OK: $d (0)"
    fi
done

for f in "${FILE_TARGETS[@]}"; do
    if [ ! -f "$f" ]; then
        echo "SKIP (missing file): $f"
        continue
    fi
    n=$(timeout 300 grep -a -o -E -- "$PATTERN" "$f" 2>/dev/null | wc -l)
    if [ "$n" -gt 0 ]; then
        echo "FAIL: $f ($n occurrence(s))"
        rc=1
    else
        echo "OK: $f (0)"
    fi
done

if [ "$rc" -eq 0 ]; then
    echo "SECRET-SCAN: CLEAN (exit 0)"
else
    echo "SECRET-SCAN: FAILED (exit 1)"
fi
exit "$rc"
