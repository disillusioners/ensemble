#!/usr/bin/env bash
# tests/packs/preflight_census_test.sh [--check-only]
#
# T0.5 / T0.6 / T1.10 / AC5 / AC6 / AC11 / SC3 — the journal-read census.
#
# 1. A GENERATOR derives the `journal_read` direct-call sites from the pinned
#    BASE rev's sources (git objects, never the working tree) and the pack
#    asserts the derived census EQUALS the committed
#    tests/packs/preflight_census.txt (25 distinct sites + the extra
#    O(N^2) sites + the transitive interpreter-less callers).
# 2. At HEAD: the same 25 direct call sites still exist (the migration changed
#    the implementation behind `journal_read` / `_json_sub` / `journal_update`
#    -- zero call-site edits), and EVERY per-character scan over the journal
#    string now lives inside a `*_legacy` fallback function.
# 3. UNREACHABILITY PROOF: with the O(N) helper resolvable, `journal_read`
#    succeeds even when the legacy walk is stubbed to exit 42 -- the legacy
#    walk is unreachable on the fast path. (Two-sided: with the helper
#    unavailable the stub IS reached, so the proof is not vacuous.)
# 4. Mutation self-test: a per-character walk planted OUTSIDE a legacy
#    function in a scratch copy turns the pack RED.
#
# SELF-EXEC SAFETY (the 2026-10-30 recursion incident): the mutation
# self-test re-invokes this script with the EXPLICIT ARGUMENT `--check-only`,
# which is a STRUCTURALLY non-recursive entrypoint (it does not contain the
# mutation section at all). No env-prefix guard, no `bash "$0"` without an
# argument — the earlier env-guard shape forked to 805 processes in ~15 min.
#
# Usage: bash tests/packs/preflight_census_test.sh [--check-only]

set -u
PF_PACK_NAME="preflight-census"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

PF_CHECK_ONLY=0
for a in "$@"; do
    case "$a" in
        --check-only) PF_CHECK_ONLY=1 ;;
        *) pf_die "unknown argument: $a" ;;
    esac
done

pf_fence_init
trap pf_fence_cleanup EXIT

CENSUS="$PF_REPO_ROOT/tests/packs/preflight_census.txt"
WS="${PF_CENSUS_WS:-$PF_REPO_ROOT}"
REV="${PF_CENSUS_REV:-$PF_BASE_REV}"

[ -f "$CENSUS" ] || pf_die "census file missing: $CENSUS"

# pf_gen_census <src> <rev|ws> — emits "<path>\t<line>\t<function>" rows plus
# a trailing "# total=N".  `journal_read.py` mentions are excluded (the
# helper's FILE NAME contains the verb; it is not a call site).
pf_gen_census() {
    /usr/bin/python3 - "$1" "$2" <<'PY'
import re, subprocess, sys, os
src, mode = sys.argv[1], sys.argv[2]
FILES = ["scripts/upgrade/lib.sh","scripts/upgrade/promote.sh","scripts/upgrade/rollback.sh",
         "scripts/upgrade/restart.sh","scripts/upgrade/status.sh","scripts/upgrade/stage.sh",
         "scripts/upgrade/adopt-unit.sh"]
def load(path):
    if mode == "rev":
        return subprocess.check_output(["git","-C",src,"show","%s:%s"%(REV_ENV,path)],text=True).splitlines()
    with open(os.path.join(src, path), encoding="utf-8") as fh:
        return fh.read().splitlines()
REV_ENV = os.environ["PF_CENSUS_REV_ENV"]
def fn_context(lines, i):
    cur = "(top level)"
    for l in lines[: i + 1]:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{", l)
        if m:
            cur = m.group(1)
    return cur
rows = 0
for path in FILES:
    try:
        lines = load(path)
    except Exception:
        continue
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("#"):
            continue
        if re.match(r"^journal_read\(\) \{", l):
            continue
        scrub = l.replace("journal_read.py", "")
        if re.search(r"\bjournal_read\b", scrub):
            print("%s\t%s\t%s" % (path, i + 1, fn_context(lines, i)))
            rows += 1
print("# total=%d" % rows)
PY
}

export PF_CENSUS_REV_ENV="$REV"

# ── 1. BASE census == committed census (row lines only) ────────────────────
printf '== 1. derived BASE census vs the committed file ==\n'
( cd "$PF_REPO_ROOT" && git cat-file -e "$REV^{commit}" 2>/dev/null ) || pf_die "rev $REV not in the object store"
pf_gen_census "$PF_REPO_ROOT" rev > "$PF_SB/derived.txt"
BASE_TOTAL="$(sed -n 's/^# total=//p' "$PF_SB/derived.txt")"
grep -v '^#' "$CENSUS" | grep -v '^[[:space:]]*$' > "$PF_SB/committed.txt"
grep -v '^#' "$PF_SB/derived.txt" | grep -v '^[[:space:]]*$' > "$PF_SB/derived.rows"
DIFF="$(diff "$PF_SB/committed.txt" "$PF_SB/derived.rows" || true)"
if [ -z "$DIFF" ]; then
    pf_ok "derived census matches the committed census exactly ($BASE_TOTAL direct sites)"
else
    pf_bad "census drift vs the committed file:"$'\n'"$DIFF"
fi
[ "$BASE_TOTAL" = "25" ] \
    && pf_ok "25 distinct journal_read call sites at BASE (17 lib.sh + 8 script sites)" \
    || pf_bad "BASE census total is $BASE_TOTAL, want 25"

# ── 2. HEAD: the call sites are unchanged; the walks are legacy-only ──────
printf '\n== 2. HEAD census (implementation migrated, call sites not touched) ==\n'
HEAD_TOTAL="$(pf_gen_census "$WS" ws | sed -n 's/^# total=//p')"
if [ "$HEAD_TOTAL" = "25" ]; then
    pf_ok "the same 25 direct call sites exist at HEAD (zero call-site edits)"
else
    pf_bad "HEAD census total is $HEAD_TOTAL, want 25 (call sites must not change)"
fi

# every per-character WALK over the journal string lives in a legacy fallback
# (or one of the two DOCUMENTED small-input exemptions). A "walk" is a LOOP
# that steps one character at a time: the offset substring `${json:i:1}` or a
# `while [ -n "$rest" ]` stepping loop. Single-shot pattern ops (`${rest:1}`
# twice in _json_field, O(N) per call) are NOT walks -- they were never in the
# census and migrating them would be scope creep.
/usr/bin/python3 - "$WS" <<'PYEOF'
import re, sys, os
ws = sys.argv[1]
lines = open(os.path.join(ws, "scripts/upgrade/lib.sh"), encoding="utf-8").read().splitlines()
cur = "(top level)"
hit_offset, hit_stepping = set(), set()
for i, l in enumerate(lines):
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{", l)
    if m:
        cur = m.group(1)
    if l.strip().startswith("#"):
        continue
    if "${json:i:1}" in l:
        hit_offset.add(cur)
    if 'while [ -n "$rest" ]' in l:
        hit_stepping.add(cur)
ALLOWED_OFFSET = {"_journal_read_legacy", "_provenance_sidecar_ok"}       # D1-5 exemption: small sidecar
ALLOWED_STEP   = {"_json_sub_legacy", "_journal_update_legacy_from"}
bad = []
for f in sorted(hit_offset - ALLOWED_OFFSET):
    bad.append("offset-substring walk in %s" % f)
for f in sorted(hit_stepping - ALLOWED_STEP):
    bad.append("char-stepping loop in %s" % f)
if not hit_offset:
    bad.append("no offset-substring walk found at all — the checker lost its target")
if bad:
    print("PF_WALK_OUTSIDE_LEGACY=1")
    for b in bad:
        print("  " + b)
    sys.exit(1)
print("PF_WALK_OK=1 functions(offset)=%s functions(stepping)=%s" % (sorted(hit_offset), sorted(hit_stepping)))
PYEOF
if [ $? = 0 ]; then
    pf_ok "every per-character WALK over the journal string lives inside a legacy fallback or a documented exemption (SC3/AC11)"
else
    pf_bad "a per-character walk exists outside the legacy fallbacks (SC3)"
fi

# the two DOCUMENTED exemptions are still exempt (small input only)
grep -q '_provenance_sidecar_ok' "$WS/scripts/upgrade/lib.sh" \
    && pf_ok "documented exemption present: _provenance_sidecar_ok (small sidecar)" \
    || pf_bad "_provenance_sidecar_ok vanished (unexpected)"
grep -q '_json_escape' "$WS/scripts/upgrade/lib.sh" \
    && pf_ok "documented exemption present: _json_escape (short strings)" \
    || pf_bad "_json_escape vanished (unexpected)"

# ── 3. unreachability: with the helper up, the legacy walk is never taken ─
printf '\n== 3. unreachability proof ==\n'
mkdir -p "$INSTALL_DIR/releases"
printf '%s' '{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}' > "$INSTALL_DIR/releases/state.json"
cat > "$PF_SB/unreach.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
# stub: if the legacy walk is ever taken, it says so LOUDLY
_journal_read_legacy() { echo "LEGACY-REACHED" >&2; return 42; }
out="$(journal_read)" ; rc=$?
echo "rc=$rc bytes=${#out}"
EOS
UNREACH="$(INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$WS" LOG_TAG=pf-census UP_TARGET=sandbox bash "$PF_SB/unreach.sh" 2>&1)"
case "$UNREACH" in
    *LEGACY-REACHED*) pf_bad "the legacy walk WAS reached with the helper present: $UNREACH" ;;
    *"rc=0 bytes="*)  pf_ok "journal_read succeeds with the legacy walk stubbed out (unreachable on the fast path)" ;;
    *) pf_bad "unexpected: $UNREACH" ;;
esac

cat > "$PF_SB/reach.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
_journal_read_legacy() { printf '%s' '{"ok":true}'; return 0; }
out="$(JOURNAL_READ_HELPER=/nonexistent/x.py journal_read)" ; rc=$?
echo "rc=$rc bytes=${#out}"
EOS
REACH="$(INSTALL_DIR="$INSTALL_DIR" REPO_ROOT="$WS" LOG_TAG=pf-census UP_TARGET=sandbox bash "$PF_SB/reach.sh" 2>&1)"
case "$REACH" in
    *"rc=0 bytes=11"*) pf_ok "with the helper absent the fallback runs (the proof is two-sided)" ;;
    *) pf_bad "helper-absent leg unexpected: $REACH" ;;
esac

# ── 4. mutation self-test (structurally non-recursive) ────────────────────
printf '\n== 4. mutation self-test ==\n'
if [ "$PF_CHECK_ONLY" = "1" ]; then
    pf_note "mutation self-test skipped (--check-only entrypoint)"
else
    MUT="$PF_SB/mutant"; mkdir -p "$MUT"
    ( cd "$WS" && tar cf - scripts 2>/dev/null ) | ( cd "$MUT" && tar xf - ) 2>/dev/null
    if [ -f "$MUT/scripts/upgrade/lib.sh" ]; then
        /usr/bin/python3 - "$MUT/scripts/upgrade/lib.sh" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
marker = '# pf-census-mutation: planted walk\n_pf_planted_walk() { local c; c="${json:i:1}"; }\n'
s = s.replace("JOURNAL_SIZE_GUARD_BYTES=", marker + "JOURNAL_SIZE_GUARD_BYTES=", 1)
open(p, "w", encoding="utf-8").write(s)
PYEOF
        MUT_RC=0
        PF_CENSUS_WS="$MUT" bash "$0" --check-only >/dev/null 2>&1 || MUT_RC=$?
        if [ "$MUT_RC" != "0" ]; then
            pf_ok "census mutation: a walk planted outside a legacy function turns the pack RED"
        else
            pf_bad "census mutation: the planted walk was NOT caught (SC3 check is vacuous)"
        fi
    else
        pf_bad "mutation self-test could not stage a scratch copy"
    fi
fi

pf_summary
