#!/usr/bin/env bash
# tests/packs/preflight_journal_parity_test.sh
#
# SC2 / AC1 / AC2 / AC3 / AC12 / AC13 / AC14 / AC17 — the DIFFERENTIAL
# harness for the O(N) journal read helper (scripts/upgrade/journal_read.py).
#
# The oracle is the BASE bash scanner itself, extracted VERBATIM from the
# pinned rev's lib.sh (`PF_BASE_REV`), not re-typed and not re-read. Every
# assertion is old-vs-new on the same input:
#
#   verdict parity          rc identical, stdout BYTE-identical, stderr class
#                           identical (the NUL-drop warning is a bash script
#                           name/line artifact => matched by class)
#   `sub` parity            _json_sub output bytes identical
#   `update` parity         the resulting JOURNAL BYTES identical, including
#                           the duplicate-key corruption (BASE-bug-preserving)
#   adjacency legs          duplicate-key exact bytes, NUL-adjacent-to-backslash,
#                           4-byte-emoji escape skip, trailing-newline strip
#   locale invariance       case 22 under C / C.UTF-8 / en_US.UTF-8
#   single read             structural (one open() in the read path) +
#                           concurrent atomic-replace race (never a torn view)
#
# Usage: bash tests/packs/preflight_journal_parity_test.sh

set -u
PF_PACK_NAME="preflight-journal-parity"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

HELPER="$PF_REPO_ROOT/scripts/upgrade/journal_read.py"
PY=/usr/bin/python3
ORACLE="$PF_SB/oracle.sh"
pf_base_oracle "$ORACLE"
[ -f "$HELPER" ] || pf_die "helper missing: $HELPER"

printf '%s\n' "== oracle: BASE bodies from $PF_BASE_REV =="
printf '%s\n' "== helper: $HELPER ($($PY "$HELPER" --probe)) =="

# ── helpers -----------------------------------------------------------------

# base_verify <fixture> -> sets BV_RC / BV_SHA / BV_LEN / BV_CLASS / BV_OUT
base_verify() {
    local f="$1" out="$PF_SB/bv.out" err="$PF_SB/bv.err"
    "$ORACLE" verify "$f" > "$out" 2> "$err"
    BV_RC=$?
    BV_LEN="$(pf_bytes "$out")"
    BV_SHA="$(pf_sha256 "$out")"
    BV_CLASS="$(pf_stderr_class "$err")"
    BV_STDERR="$(pf_stderr_without_nul_warning "$err")"
}

# new_verify <fixture> -> sets NV_*
new_verify() {
    local f="$1" out="$PF_SB/nv.out" err="$PF_SB/nv.err"
    "$PY" "$HELPER" verify "$f" > "$out" 2> "$err"
    NV_RC=$?
    NV_LEN="$(pf_bytes "$out")"
    NV_SHA="$(pf_sha256 "$out")"
    NV_CLASS="$(pf_stderr_class "$err")"
    NV_STDERR="$(pf_stderr_without_nul_warning "$err")"
}

cmp_verify() {
    local f="$1" name; name="$(basename "$f" .json)"
    base_verify "$f"
    new_verify "$f"
    if [ "$BV_RC" = "$NV_RC" ] && [ "$BV_SHA" = "$NV_SHA" ] && [ "$BV_CLASS" = "$NV_CLASS" ] \
       && [ "$BV_STDERR" = "$NV_STDERR" ]; then
        pf_ok "verify parity: $name (rc=$BV_RC len=$BV_LEN out=${BV_SHA:0:12} stderr=$BV_CLASS)"
    else
        pf_bad "verify parity: $name  BASE(rc=$BV_RC len=$BV_LEN sha=${BV_SHA:0:12} cls=$BV_CLASS) vs NEW(rc=$NV_RC len=$NV_LEN sha=${NV_SHA:0:12} cls=$NV_CLASS) stderr[$BV_STDERR|$NV_STDERR]"
    fi
}

# ── phase A: verdict + stdout-byte parity (22 corpus + G5 = 23) --------------

printf '\n-- A. verdict + stdout-byte parity (22 corpus cases + G5) --\n'
PF_CORPUS_COUNT=0
for f in "$PF_FIXTURES"/*.json; do
    case "$(basename "$f")" in
        G1-*|G2-*|G3-*|G4-*) continue ;;   # guard-boundary, consumed by the size-guard pack
    esac
    cmp_verify "$f"
    PF_CORPUS_COUNT=$((PF_CORPUS_COUNT + 1))
done
[ "$PF_CORPUS_COUNT" -eq 23 ] \
    && pf_ok "fixture corpus count is 23 byte-level rows (22 corpus + G5)" \
    || pf_bad "fixture corpus count is $PF_CORPUS_COUNT, want 23 (22 corpus + G5)"

# The 4 guard-boundary fixtures exist at the DEFAULT threshold size.
for f in "$PF_FIXTURES"/G1-*.json "$PF_FIXTURES"/G2-*.json "$PF_FIXTURES"/G3-*.json "$PF_FIXTURES"/G4-*.json; do
    [ -f "$f" ] || { pf_bad "missing guard fixture: $f"; continue; }
done
[ "$(pf_bytes "$PF_FIXTURES/G1-guard-threshold-minus-1.json")" = "98303" ] \
    && pf_ok "G1 is threshold-1 (98303 B)" || pf_bad "G1 size wrong"
[ "$(pf_bytes "$PF_FIXTURES/G2-guard-threshold-exact.json")" = "98304" ] \
    && pf_ok "G2 is threshold-exact (98304 B)" || pf_bad "G2 size wrong"
[ "$(pf_bytes "$PF_FIXTURES/G3-guard-threshold-plus-1.json")" = "98305" ] \
    && pf_ok "G3 is threshold+1 (98305 B)" || pf_bad "G3 size wrong"

# ── phase B: `sub` parity (_json_sub) ---------------------------------------

printf '\n-- B. `sub` parity (_json_sub: string-NAIVE, first occurrence) --\n'
sub_case() {
    local f="$1" key="$2" name; name="$(basename "$f" .json)"
    "$ORACLE" sub "$key" < "$f" > "$PF_SB/bs.out" 2>/dev/null; local brc=$?
    "$PY" "$HELPER" sub "$key" < "$f" > "$PF_SB/ns.out" 2>/dev/null; local nrc=$?
    if [ "$brc" = "$nrc" ] && [ "$(pf_sha256 "$PF_SB/bs.out")" = "$(pf_sha256 "$PF_SB/ns.out")" ]; then
        pf_ok "sub parity: $name key=$key (rc=$brc bytes=$(pf_bytes "$PF_SB/bs.out"))"
    else
        pf_bad "sub parity: $name key=$key BASE(rc=$brc bytes=$(pf_bytes "$PF_SB/bs.out")) NEW(rc=$nrc bytes=$(pf_bytes "$PF_SB/ns.out"))"
    fi
}
sub_case "$PF_FIXTURES/01-canonical-empty-journal.json" quarantined
sub_case "$PF_FIXTURES/01-canonical-empty-journal.json" rollback_window_count
sub_case "$PF_FIXTURES/02-live-shaped-journal.json" history
sub_case "$PF_FIXTURES/02-live-shaped-journal.json" in_flight
sub_case "$PF_FIXTURES/21-duplicate-key.json" x
sub_case "$PF_FIXTURES/09-braces-inside-string.json" a
# brace INSIDE a string redirects the string-naive counter — BASE behaviour
printf '%s' '{"a":{"k":"}x"},"b":2}' > "$PF_SB/naive.json"
sub_case "$PF_SB/naive.json" a
# `sub` on a key with no value / no colon
printf '%s' '{"a":1}' > "$PF_SB/noquote.json"
sub_case "$PF_SB/noquote.json" a

# ── phase C: `update` parity (journal_update bytes) -------------------------

printf '\n-- C. `update` parity (journal_update: byte-identical journal) --\n'
update_case() {
    local f="$1" field="$2" raw="$3" name; name="$(basename "$f" .json)"
    # feed both sides the SAME processed bytes (BASE's own journal_read output)
    "$ORACLE" verify "$f" > "$PF_SB/proc.json" 2>/dev/null
    if [ ! -s "$PF_SB/proc.json" ]; then
        pf_note "update parity: $name skipped (journal_read refuses this fixture)"
        return 0
    fi
    cp "$PF_SB/proc.json" "$PF_SB/oracle-journal.json"
    "$ORACLE" update "$PF_SB/oracle-journal.json" "$field" "$raw" > "$PF_SB/bu.out" 2> "$PF_SB/bu.err"
    local brc=$?
    # journal_write appends exactly one '\n'; compare on the stripped form
    PF_TMPSTRIP="$PF_SB/bu.stripped"
    printf '%s' "$(cat "$PF_SB/oracle-journal.json")" > "$PF_TMPSTRIP"
    "$PY" "$HELPER" update "$field" "$raw" < "$PF_SB/proc.json" > "$PF_SB/nu.raw" 2>/dev/null
    local nrc=$?
    # the helper distinguishes its two refusal classes by exit code (3 =
    # field absent, 4 = splice failed) so the SHELL can emit the matching
    # `_warn`; both map to BASE's single rc 1 refusal.
    case "$nrc" in 3|4) nrc=1 ;; esac
    # strip the \x1f sentinel (it preserves trailing newlines through $( ))
    PF_TMPNEW="$PF_SB/nu.stripped"
    /usr/bin/python3 - "$PF_SB/nu.raw" "$PF_TMPNEW" <<'PY'
import sys
data = open(sys.argv[1], "rb").read()
if data.endswith(b"\x1f"):
    data = data[:-1]
open(sys.argv[2], "wb").write(data)
PY
    if [ "$brc" = "1" ] || [ "$nrc" = "1" ]; then
        # refusal: BASE returns before writing (journal untouched) and the
        # helper emits nothing. Assert BOTH refusals, and that the journal
        # was not mutated by either side.
        if [ "$brc" = "$nrc" ] && [ ! -s "$PF_TMPNEW" ] \
           && [ "$(pf_sha256 "$PF_SB/oracle-journal.json")" = "$(pf_sha256 "$PF_SB/proc.json")" ]; then
            pf_ok "update parity: $name field=$field raw=$raw (both REFUSE rc=1; journal untouched)"
        else
            pf_bad "update refusal parity: $name field=$field BASE(rc=$brc) NEW(rc=$nrc) journal_mutated=$([ "$(pf_sha256 "$PF_SB/oracle-journal.json")" = "$(pf_sha256 "$PF_SB/proc.json")" ] && echo no || echo yes)"
        fi
        return 0
    fi
    if [ "$brc" = "$nrc" ] && [ "$(pf_sha256 "$PF_TMPSTRIP")" = "$(pf_sha256 "$PF_TMPNEW")" ]; then
        pf_ok "update parity: $name field=$field raw=$raw (rc=$brc bytes=$(pf_bytes "$PF_TMPNEW"))"
    else
        pf_bad "update parity: $name field=$field raw=$raw BASE(rc=$brc) NEW(rc=$nrc) BASE=[$(head -c 120 "$PF_TMPSTRIP")] NEW=[$(head -c 120 "$PF_TMPNEW")]"
    fi
}
update_case "$PF_FIXTURES/01-canonical-empty-journal.json" current '"v9.9.9"'
update_case "$PF_FIXTURES/02-live-shaped-journal.json" cooldown_until '"2026-11-01T00:00:00Z"'
update_case "$PF_FIXTURES/02-live-shaped-journal.json" in_flight 'null'
update_case "$PF_FIXTURES/07-escaped-quote-in-string.json" a '"z"'
update_case "$PF_FIXTURES/21-duplicate-key.json" x '99'
update_case "$PF_FIXTURES/22-escape-then-multibyte.json" a '"q"'
# field-absent refusal
update_case "$PF_FIXTURES/01-canonical-empty-journal.json" no_such_field '1'

# the duplicate-key corruption, asserted EXPLICITLY (BASE-bug-preserving)
printf '%s' '{"x":10,"b":2,"x":20}' > "$PF_SB/dup.json"
"$ORACLE" update "$PF_SB/dup.json" x 99 >/dev/null 2>&1
DUP_BASE="$(cat "$PF_SB/dup.json")"
WANT_DUP='{"x":10,"b":2,"x":99,"b":2,"x":20}'
[ "$DUP_BASE" = "$WANT_DUP" ] \
    && pf_ok "duplicate-key corruption reproduced (BASE-bug-preserving): $DUP_BASE" \
    || pf_bad "duplicate-key BASE bytes unexpected: $DUP_BASE"
printf '%s' '{"x":10,"b":2,"x":20}' | "$PY" "$HELPER" update x 99 2>/dev/null \
    | /usr/bin/python3 -c 'import sys;d=sys.stdin.buffer.read();sys.stdout.buffer.write(d[:-1] if d.endswith(b"\x1f") else d)' > "$PF_SB/dup.new"
DUP_NEW="$(cat "$PF_SB/dup.new")"
[ "$DUP_NEW" = "$WANT_DUP" ] \
    && pf_ok "duplicate-key corruption preserved by the port (NOT 'fixed')" \
    || pf_bad "port changed the duplicate-key bytes: $DUP_NEW"

# ── phase D: adjacency legs ------------------------------------------------

printf '\n-- D. adjacency legs (mutation-meaningful edges) --\n'

# D1. G5 direction: BASE REFUSES `{"a":"x\<NUL>"}`; a RAW-byte scan ACCEPTS.
printf '{"a":"x\\\000"}' > "$PF_SB/g5.json"
base_verify "$PF_SB/g5.json"
new_verify "$PF_SB/g5.json"
[ "$BV_RC" = "1" ] && [ "$NV_RC" = "1" ] \
    && pf_ok "G5 direction: BASE refuses AND the helper refuses (processed-bytes verdict domain)" \
    || pf_bad "G5 direction: BASE rc=$BV_RC helper rc=$NV_RC (want 1/1)"
# the mutation: a raw-byte scanner (NUL kept) ACCEPTS the very same file
/usr/bin/python3 - "$PF_SB/g5.json" <<'PY' > "$PF_SB/g5.raw.verdict"
import sys
data = open(sys.argv[1], "rb").read()
ob = cb = os_ = cs = in_str = esc = 0
for c in data:                      # RAW bytes -- NUL NOT dropped
    if esc:
        esc = 0; continue
    if c == 0x5C:
        if in_str: esc = 1
        continue
    if c == 0x22:
        in_str = 1 - in_str; continue
    if in_str: continue
    if c == 0x7B: ob += 1
    elif c == 0x7D: cb += 1
    elif c == 0x5B: os_ += 1
    elif c == 0x5D: cs += 1
print("ACCEPT" if (ob == cb and os_ == cs and in_str == 0) else "REFUSE")
PY
[ "$(cat "$PF_SB/g5.raw.verdict")" = "ACCEPT" ] \
    && pf_ok "G5 mutation check: a raw-byte scanner ACCEPTS the same bytes (the pin is mutation-meaningful)" \
    || pf_bad "G5 mutation check: raw-byte scanner did not accept — the pin would be mutation-DEAD"

# D2. backslash at EOF outside a string: accept; inside a string: refuse.
printf '{"a":1}\\' > "$PF_SB/bs-outside.json"
printf '{"a":"x\\'  > "$PF_SB/bs-inside.json"
cmp_verify "$PF_SB/bs-outside.json"
cmp_verify "$PF_SB/bs-inside.json"

# D3. 4-byte emoji after an escape inside a string (the ${rest:2} cell).
printf '{"a":"x\\\360\237\230\200y","b":2}' > "$PF_SB/emoji.json"
cmp_verify "$PF_SB/emoji.json"
update_case "$PF_SB/emoji.json" a '"q"'

# D4. every real journal ends in exactly one newline: the stdout must drop it.
printf '%s\n' "$(cat "$PF_FIXTURES/02-live-shaped-journal.json")" > "$PF_SB/nl.json"
printf '\n' >> "$PF_SB/nl.json"
base_verify "$PF_SB/nl.json"
new_verify "$PF_SB/nl.json"
[ "$BV_SHA" = "$NV_SHA" ] && [ "$NV_LEN" = "$(pf_bytes "$PF_FIXTURES/02-live-shaped-journal.json")" ] \
    && pf_ok "trailing-newline strip: stdout parity at len=$NV_LEN (raw dump would differ)" \
    || pf_bad "trailing-newline strip: BASE sha=${BV_SHA:0:12} NEW sha=${NV_SHA:0:12} len=$NV_LEN"

# ── phase E: locale invariance (case 22 + its invalid-UTF-8 sibling) --------

printf '\n-- E. locale invariance (case 22) --\n'
for loc in C C.UTF-8 en_US.UTF-8; do
    if [ "$loc" = "en_US.UTF-8" ] && ! locale -a 2>/dev/null | grep -qx 'en_US.utf8\|en_US.UTF-8'; then
        pf_note "locale $loc unavailable on this host — skipped (disclosed)"
        continue
    fi
    LC_ALL="$loc" LANG="$loc" base_verify "$PF_FIXTURES/22-escape-then-multibyte.json"
    LC_ALL="$loc" LANG="$loc" new_verify  "$PF_FIXTURES/22-escape-then-multibyte.json"
    [ "$BV_SHA" = "$NV_SHA" ] && [ "$BV_RC" = "$NV_RC" ] \
        && pf_ok "locale $loc: parity (rc=$BV_RC sha=${BV_SHA:0:12})" \
        || pf_bad "locale $loc: BASE(rc=$BV_RC sha=${BV_SHA:0:12}) NEW(rc=$NV_RC sha=${NV_SHA:0:12})"
done
# invalid-UTF-8 + escape (the char-vs-byte cell) under both locales
printf '{"a":"x\\\351y","b":2}' > "$PF_SB/inv-esc.json"
for loc in C C.UTF-8; do
    LC_ALL="$loc" LANG="$loc" base_verify "$PF_SB/inv-esc.json"
    LC_ALL="$loc" LANG="$loc" new_verify  "$PF_SB/inv-esc.json"
    [ "$BV_SHA" = "$NV_SHA" ] && [ "$BV_RC" = "$NV_RC" ] \
        && pf_ok "locale $loc invalid-UTF-8+escape: parity (rc=$BV_RC sha=${BV_SHA:0:12})" \
        || pf_bad "locale $loc invalid-UTF-8+escape: BASE(rc=$BV_RC) NEW(rc=$NV_RC)"
done

# ── phase F: single read + no torn view (AC17 / D1-16) ---------------------

printf '\n-- F. single read + atomic-replace race --\n'
READS="$(grep -c 'open(' "$HELPER" 2>/dev/null || true)"
[ "$READS" = "1" ] \
    && pf_ok "single read: exactly one open() in the helper (a planted second read turns this RED)" \
    || pf_bad "single read: found $READS open() calls in the helper (want 1)"

# concurrent atomic replace: the observed content is the OLD or the NEW
# journal, never a fragment.
A="$PF_SB/race-a.json"; B="$PF_SB/race-b.json"
printf '%s' '{"a":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' > "$A"
printf '%s' '{"b":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}' > "$B"
RACE="$PF_SB/race.json"; cp "$A" "$RACE"
( i=0; while [ $i -lt 200 ]; do
      cp "$A" "$RACE.tmp" && mv -f "$RACE.tmp" "$RACE"
      cp "$B" "$RACE.tmp" && mv -f "$RACE.tmp" "$RACE"
      i=$((i + 1))
  done ) >/dev/null 2>&1 &
WRITER=$!
RACE_OK=1
i=0
while [ $i -lt 200 ]; do
    out="$("$PY" "$HELPER" verify "$RACE" 2>/dev/null)" || { RACE_OK=0; break; }
    case "$out" in
        '{"a":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}') : ;;
        '{"b":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}') : ;;
        *) RACE_OK=0; pf_note "torn view observed: [$out]"; break ;;
    esac
    i=$((i + 1))
done
wait "$WRITER" 2>/dev/null || true
[ "$RACE_OK" = "1" ] \
    && pf_ok "no torn view across $i concurrent atomic replaces (whole old OR whole new)" \
    || pf_bad "a torn view was observed across concurrent replaces"

# ── phase G: the helper never routes through the write path ----------------

printf '\n-- G. placement invariants (D1-13 / AC15) --\n'
grep -q 'journal_splice' "$HELPER" && ! grep -qE '^(import|from).*journal_splice' "$HELPER" \
    && pf_ok "helper does not import journal_splice (read path is its own file)" \
    || pf_bad "helper imports journal_splice"
[ -z "$(cd "$PF_REPO_ROOT" && git diff --name-only -- scripts/upgrade/journal_splice.py)" ] \
    && pf_ok "scripts/upgrade/journal_splice.py is untouched by this lane" \
    || pf_bad "scripts/upgrade/journal_splice.py was modified"

pf_summary
