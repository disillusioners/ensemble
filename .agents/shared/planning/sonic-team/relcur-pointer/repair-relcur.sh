#!/usr/bin/env bash
# SCOPE-2 LIVE REPAIR — re-point /home/nea/agents-ensemble/releases/current
# FENCES: touches ONLY releases/current. NO state.json writes. NO deletions.
#         NO daemon restart. NO promote.sh. Idempotent.
set -euo pipefail
INSTALL=/home/nea/agents-ensemble
REL="$INSTALL/releases"
PTR="$REL/current"
JOURNAL="$REL/state.json"
TOP="$INSTALL/current"

WANT="$(python3 - "$JOURNAL" <<'PY'
import json,sys
j=json.load(open(sys.argv[1]))
print(j.get("current") or "")
PY
)"
[ -n "$WANT" ] || { echo "FAIL: journal current empty"; exit 1; }
[ -d "$REL/$WANT" ] || { echo "FAIL: releases/$WANT dir missing"; exit 1; }
LIVEZ_VER="$(curl -sS -m 5 http://127.0.0.1:9797/livez | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' || true)"
WANT_NUM="${WANT#v}"
[ "$LIVEZ_VER" = "$WANT_NUM" ] || { echo "FAIL: livez version '$LIVEZ_VER' != journal current '$WANT_NUM'"; exit 1; }
echo "PRECONDITIONS-PROVEN: journal current=$WANT; releases/$WANT is a dir; livez version=$LIVEZ_VER == journal current"

STATE_SHA_BEFORE="$(sha256sum "$JOURNAL" | cut -d' ' -f1)"
TOP_BEFORE="$(readlink "$TOP" 2>/dev/null || echo '(absent)')"

if [ -L "$PTR" ]; then
  OLD_TARGET="$(readlink "$PTR")"
  OLD_STAT="$(stat -c 'owner=%U:%G mtime=%y inode=%i' "$PTR")"
else
  OLD_TARGET="(absent)"; OLD_STAT="(absent)"
fi
echo "RECEIPT-PRE: path=$PTR old_target=$OLD_TARGET $OLD_STAT"

CUR_TARGET="$(readlink "$PTR" 2>/dev/null || true)"
if [ "$CUR_TARGET" = "$WANT" ] && [ -d "$PTR/" ]; then
  echo "IDEMPOTENT-NOOP: releases/current already -> $WANT (inode $(stat -c %i "$PTR"))"
  echo "FENCE-CHECK: state.json sha $STATE_SHA_BEFORE (untouched); top-level current -> $TOP_BEFORE (untouched)"
  exit 0
fi

TMP="$REL/current.repair.$$"
trap 'rm -f "$TMP"' EXIT
ln -sfn "$WANT" "$TMP"
mv -Tf "$TMP" "$PTR"
trap - EXIT

NEW_TARGET="$(readlink "$PTR")"
[ "$NEW_TARGET" = "$WANT" ] || { echo "FAIL: post-flip readlink '$NEW_TARGET' != '$WANT'"; exit 1; }
[ -d "$PTR/" ] || { echo "FAIL: pointer does not resolve to a dir"; exit 1; }
STATE_SHA_AFTER="$(sha256sum "$JOURNAL" | cut -d' ' -f1)"
TOP_AFTER="$(readlink "$TOP" 2>/dev/null || echo '(absent)')"
[ "$STATE_SHA_BEFORE" = "$STATE_SHA_AFTER" ] || { echo "FAIL: state.json changed — fence violated"; exit 1; }
[ "$TOP_BEFORE" = "$TOP_AFTER" ] || { echo "FAIL: top-level current changed — fence violated"; exit 1; }
echo "RECEIPT-POST: path=$PTR new_target=$NEW_TARGET $(stat -c 'owner=%U:%G mtime=%y inode=%i' "$PTR")"
echo "FENCE-PROVEN: state.json sha unchanged ($STATE_SHA_AFTER); top-level current -> $TOP_AFTER unchanged; nothing deleted"
echo "REPAIRED: releases/current -> $WANT == journal current; resolves to existing dir; livez=$LIVEZ_VER"
