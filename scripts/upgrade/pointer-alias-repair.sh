#!/usr/bin/env bash
# pointer-alias-repair.sh — idempotent operator repair for
# $INSTALL_DIR/releases/current (the releases/current alias pointer).
#
# FENCES: touches ONLY releases/current. NO state.json writes. NO deletions.
#         NO daemon restart. NO promote.sh. Idempotent. Named non-zero
#         refusal reasons. Writes NO journal rows.
set -euo pipefail

INSTALL="/home/nea/agents-ensemble"
PORT=""
SKIP_LIVEZ="${SKIP_LIVEZ:-0}"

usage() {
    echo "usage: $0 [--install-dir DIR] [--port N] [--skip-livez]" >&2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir) INSTALL="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --skip-livez) SKIP_LIVEZ=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

REL="$INSTALL/releases"
PTR="$REL/current"
JOURNAL="$REL/state.json"
TOP="$INSTALL/current"

[ -d "$INSTALL" ] || { echo "FAIL: install dir missing: $INSTALL"; exit 1; }
[ -f "$JOURNAL" ] || { echo "FAIL: journal missing: $JOURNAL"; exit 1; }

# Resolve port from --port / env / .env / default
if [ -z "$PORT" ]; then
    if [ -n "${PORT_ENV:-}" ]; then PORT="$PORT_ENV"; fi
fi
if [ -z "$PORT" ] && [ -f "$INSTALL/.env" ]; then
    PORT="$(grep -E '^PORT=' "$INSTALL/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
fi
[ -z "$PORT" ] && PORT="${PORT:-9797}"
case "$PORT" in
    ''|*[!0-9]*) echo "FAIL: invalid port '$PORT'"; exit 1 ;;
esac

WANT="$(python3 - "$JOURNAL" <<'PY'
import json,sys
try:
    j=json.load(open(sys.argv[1]))
    print(j.get("current") or "")
except Exception:
    print("")
PY
)"
[ -n "$WANT" ] || { echo "FAIL: journal current empty"; exit 1; }
[ -d "$REL/$WANT" ] || { echo "FAIL: releases/$WANT dir missing"; exit 1; }

if [ "$SKIP_LIVEZ" != "1" ]; then
    LIVEZ_VER="$(curl -sS -m 5 "http://127.0.0.1:${PORT}/livez" 2>/dev/null | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("version",""))
except Exception:
    print("")' 2>/dev/null || true)"
    WANT_NUM="${WANT#v}"
    [ "$LIVEZ_VER" = "$WANT_NUM" ] || { echo "FAIL: livez version '${LIVEZ_VER:-<empty>}' != journal current '$WANT_NUM'"; exit 1; }
fi

echo "PRECONDITIONS-PROVEN: journal current=$WANT; releases/$WANT is a dir; livez version=${LIVEZ_VER:-skipped} == journal current"

STATE_SHA_BEFORE="$(sha256sum "$JOURNAL" | cut -d' ' -f1)"
TOP_BEFORE="$(readlink "$TOP" 2>/dev/null || echo '(absent)')"

if [ -L "$PTR" ]; then
    OLD_TARGET="$(readlink "$PTR")"
    OLD_STAT="$(stat -c 'owner=%U:%G mtime=%y inode=%i' "$PTR" 2>/dev/null || stat -f 'owner=%Su:%Sg mtime=%Sm inode=%i' "$PTR")"
else
    OLD_TARGET="(absent)"; OLD_STAT="(absent)"
fi
echo "RECEIPT-PRE: path=$PTR old_target=$OLD_TARGET $OLD_STAT"

CUR_TARGET="$(readlink "$PTR" 2>/dev/null || true)"
if [ "$CUR_TARGET" = "$WANT" ] && [ -d "$PTR/" ]; then
    echo "IDEMPOTENT-NOOP: releases/current already -> $WANT (inode $(stat -c %i "$PTR" 2>/dev/null || stat -f %i "$PTR"))"
    echo "FENCE-CHECK: state.json sha $STATE_SHA_BEFORE (untouched); top-level current -> $TOP_BEFORE (untouched)"
    exit 0
fi

# Platform dispatch for mv (rename(2) discipline)
MV_ARGS=""
case "$(uname -s)" in
    Darwin|*BSD*|*bsd*) MV_ARGS="-h -f" ;;
    Linux|GNU*|*GNU*) MV_ARGS="-T -f" ;;
    *) echo "FAIL: unrecognized platform '$(uname -s)'"; exit 1 ;;
esac

TMP="$REL/current.repair.$$"
trap 'rm -f "$TMP"' EXIT
ln -sfn "$WANT" "$TMP" || { echo "FAIL: ln failed"; exit 1; }
# shellcheck disable=SC2086
mv $MV_ARGS "$TMP" "$PTR" || { echo "FAIL: mv failed"; rm -f "$TMP"; exit 1; }
trap - EXIT

NEW_TARGET="$(readlink "$PTR")"
[ "$NEW_TARGET" = "$WANT" ] || { echo "FAIL: post-flip readlink '$NEW_TARGET' != '$WANT'"; exit 1; }
[ -d "$PTR/" ] || { echo "FAIL: pointer does not resolve to a dir"; exit 1; }
STATE_SHA_AFTER="$(sha256sum "$JOURNAL" | cut -d' ' -f1)"
TOP_AFTER="$(readlink "$TOP" 2>/dev/null || echo '(absent)')"
[ "$STATE_SHA_BEFORE" = "$STATE_SHA_AFTER" ] || { echo "FAIL: state.json changed — fence violated"; exit 1; }
[ "$TOP_BEFORE" = "$TOP_AFTER" ] || { echo "FAIL: top-level current changed — fence violated"; exit 1; }
echo "RECEIPT-POST: path=$PTR new_target=$NEW_TARGET $(stat -c 'owner=%U:%G mtime=%y inode=%i' "$PTR" 2>/dev/null || stat -f 'owner=%Su:%Sg mtime=%Sm inode=%i' "$PTR")"
echo "FENCE-PROVEN: state.json sha unchanged ($STATE_SHA_AFTER); top-level current -> $TOP_AFTER unchanged; nothing deleted"
echo "REPAIRED: releases/current -> $WANT == journal current; resolves to existing dir; livez=${LIVEZ_VER:-skipped}"
