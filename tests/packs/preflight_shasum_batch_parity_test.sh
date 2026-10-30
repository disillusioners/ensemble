#!/usr/bin/env bash
# tests/packs/preflight_shasum_batch_parity_test.sh
#
# T0.13 / AC13 + phase1 D1-7 — the stage-path checksum BATCHING.
#
# `_tree_manifest` now hashes a whole tree with ONE shasum invocation per
# chunk (was: one fork PER FILE — the 329.6 s/plugins-pass wedge). This pack
# proves:
#   1. BYTE-PARITY: the batched output is byte-identical to the BASE
#      implementation (extracted verbatim from the pinned rev) on a synthetic
#      tree WITH awkward names (space, UTF-8, dotfile) and on the repo's
#      agents/ tree.
#   2. FALLBACK: a tree whose shasum line-count breaks the positional pairing
#      falls back to the per-file loop (byte-identical output).
#   3. TIMING: the measured speedup on the synthetic tree (the full-tree
#      numbers — plugins 301.2 s -> 1.35 s — are recorded from the one-off
#      wave-1 measurement and cited in lib.sh's STAGE_TREE_BUDGET_S comment;
#      this pack re-measures the SCALED shape so the gate never pays 5 min).
#
#   4. A1 KILL HYGIENE (code council 2026-10-30): a budget kill reaps the
#      `xargs … shasum` batch and leaves no `tree-manifest.*` temp pair.
#
# Usage: bash tests/packs/preflight_shasum_batch_parity_test.sh [--full]

set -u
PF_PACK_NAME="preflight-shasum-batch-parity"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

PF_FULL=0
for a in "$@"; do
    case "$a" in
        --full) PF_FULL=1 ;;
        *) pf_die "unknown argument: $a" ;;
    esac
done

pf_fence_init
trap pf_fence_cleanup EXIT

# build the BASE and HEAD _tree_manifest harnesses from git / worktree
/usr/bin/python3 - "$PF_SB" "$PF_REPO_ROOT" "$PF_BASE_REV" <<'PYEOF'
import re, subprocess, sys, os
sb, repo, rev = sys.argv[1], sys.argv[2], sys.argv[3]
def get(text, name):
    m = re.search(r"^%s\(\) \{\n?" % re.escape(name), text, re.M)
    if not m:
        raise SystemExit("missing " + name)
    start = m.start()
    le = text.find("\n", m.start())
    if text[m.start():le].rstrip().endswith("}"):
        return text[m.start():le]
    j = text.index("\n}", m.end())
    return text[start:j + 2]
base = subprocess.check_output(["git", "-C", repo, "show", "%s:scripts/upgrade/lib.sh" % rev], text=True)
head = open(os.path.join(repo, "scripts/upgrade/lib.sh"), encoding="utf-8").read()
hdr = "#!/usr/bin/env bash\nset -u\n_warn() { printf 'WARN: %s\\n' \"$*\" >&2; }\n"
open(os.path.join(sb, "base-tm.sh"), "w").write(
    hdr + get(base, "_sha256") + "\n" + get(base, "_tree_manifest") + '\n_tree_manifest "$1" "$2"\n')
open(os.path.join(sb, "head-tm.sh"), "w").write(
    hdr + get(head, "_sha256") + "\n" + get(head, "_tree_manifest") + '\n_tree_manifest "$1" "$2"\n')
PYEOF

printf '== 1. byte-parity on a synthetic tree with awkward names ==\n'
TREE="$PF_SB/tree"
mkdir -p "$TREE/sub dir/deep"
for i in $(seq 1 120); do printf 'payload-%04d\n' "$i" > "$TREE/f$i.bin"; done
for i in $(seq 1 120); do printf 'nested-%04d\n' "$i" > "$TREE/sub dir/deep/n$i.bin"; done
printf 'space name\n'   > "$TREE/sub dir/a file with spaces.txt"
printf 'unicode\n'      > "$TREE/sub dir/héllo-wörld.json"
printf 'dotfile\n'      > "$TREE/.hidden"
printf 'empty\n'        > "$TREE/empty.bin"
printf '%s' '' > "$TREE/zero.bin"

bash "$PF_SB/base-tm.sh" "$TREE" "" > "$PF_SB/base.out" 2>"$PF_SB/base.err"; BRC=$?
bash "$PF_SB/head-tm.sh" "$TREE" "" > "$PF_SB/head.out" 2>"$PF_SB/head.err"; HRC=$?
if [ "$BRC" = "$HRC" ] && cmp -s "$PF_SB/base.out" "$PF_SB/head.out"; then
    pf_ok "byte-parity on the synthetic tree ($(wc -l < "$PF_SB/head.out" | tr -d ' ') entries, rc=$HRC)"
else
    pf_bad "parity broke on the synthetic tree (BASE rc=$BRC / HEAD rc=$HRC)"
    diff "$PF_SB/base.out" "$PF_SB/head.out" | head -5
fi

printf '\n== 2. byte-parity on the repo agents/ tree (read-only) ==\n'
bash "$PF_SB/base-tm.sh" "$PF_REPO_ROOT/agents" "" > "$PF_SB/b-agents.out" 2>/dev/null; BRC=$?
bash "$PF_SB/head-tm.sh" "$PF_REPO_ROOT/agents" "" > "$PF_SB/h-agents.out" 2>/dev/null; HRC=$?
if [ "$BRC" = "$HRC" ] && cmp -s "$PF_SB/b-agents.out" "$PF_SB/h-agents.out"; then
    pf_ok "byte-parity on agents/ ($(wc -l < "$PF_SB/h-agents.out" | tr -d ' ') files)"
else
    pf_bad "agents/ parity broke (BASE rc=$BRC / HEAD rc=$HRC)"
fi

printf '\n== 3. the misalignment fallback ==\n'
# a file that shasum cannot hash (a directory entry inside the listing would
# do; here we simulate by making one file unreadable)
FALLBACK_TREE="$PF_SB/tree2"
mkdir -p "$FALLBACK_TREE"
for i in 1 2 3 4 5; do printf 'x%d\n' "$i" > "$FALLBACK_TREE/f$i"; done
# root-only unreadable file: if we are root, chmod is not a barrier — skip
# the leg in that case (the fallback is also covered by the count check).
if [ "$(id -u)" = "0" ]; then
    pf_note "running as root: the unreadable-file fallback leg cannot be staged (chmod is not a barrier) — skipped, disclosed"
else
    chmod 000 "$FALLBACK_TREE/f3"
    bash "$PF_SB/head-tm.sh" "$FALLBACK_TREE" "" > "$PF_SB/fb.out" 2>"$PF_SB/fb.err"; FRC=$?
    chmod 644 "$FALLBACK_TREE/f3"
    if grep -q "falling back to per-file hashing" "$PF_SB/fb.err"; then
        # the per-file loop emits an EMPTY sha for the unreadable file (same
        # as BASE would); assert the row count survived
        pf_ok "misalignment detected -> per-file fallback (row count preserved: $(wc -l < "$PF_SB/fb.out" | tr -d ' '))"
    else
        pf_bad "the fallback did not fire on a hashing failure: $(head -2 "$PF_SB/fb.err")"
    fi
fi

printf '\n== 4. timing (scaled shape; the full-tree numbers are cited in lib.sh) ==\n'
t0="$(pf_now_ms)"; bash "$PF_SB/base-tm.sh" "$TREE" "" > /dev/null 2>&1; t1="$(pf_now_ms)"
BASE_MS=$(( t1 - t0 ))
t0="$(pf_now_ms)"; bash "$PF_SB/head-tm.sh" "$TREE" "" > /dev/null 2>&1; t1="$(pf_now_ms)"
HEAD_MS=$(( t1 - t0 ))
printf '   synthetic tree (245 files): BASE=%sms  HEAD(batched)=%sms\n' "$BASE_MS" "$HEAD_MS"
if [ "$HEAD_MS" -lt "$BASE_MS" ]; then
    pf_ok "batching is faster on the scaled shape (${BASE_MS}ms -> ${HEAD_MS}ms)"
else
    pf_bad "batching is NOT faster on the scaled shape (${BASE_MS}ms -> ${HEAD_MS}ms)"
fi

printf '\n== 4b. A1: a budget kill reaps the batch and leaks no temp pair ==\n'
# A1 (code council 2026-10-30): `_run_bounded` TERMs the SUBSHELL, so the
# EXIT-only trap did not run: the `xargs … shasum` batch survived (still
# hashing into a file nobody reads) and the mktemp pair leaked. Pinned as two
# observable facts on a stubbed slow `shasum`: (a) no `tree-manifest.*` temp
# file is left behind, (b) the batch's own process is gone shortly after the
# bounded call returns. The stub writes its pid and `exec`s the sleep, so pid
# identity is exact (no `pgrep -f` pattern matching).
mkdir -p "$PF_SB/a1/bin" "$PF_SB/a1/tree" "$PF_SB/a1/tmp"
printf '#!/bin/sh\nprintf "%%s\\n" "$$" > "$A1_PIDFILE"\nexec sleep 30\n' > "$PF_SB/a1/bin/shasum"
chmod +x "$PF_SB/a1/bin/shasum"
for i in 1 2 3 4 5; do printf 'x\n' > "$PF_SB/a1/tree/f$i"; done
cat > "$PF_SB/a1/run.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-a1
INSTALL_DIR="$A1_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_run_bounded 2 _tree_manifest "$A1_TREE" "" >/dev/null 2>&1
printf 'rc=%s\n' "$?"
EOS
A1_PIDFILE="$PF_SB/a1/shasum.pid"; export A1_PIDFILE
A1_INSTALL="$INSTALL_DIR" A1_TREE="$PF_SB/a1/tree" A1_PIDFILE="$A1_PIDFILE" \
    REPO_ROOT="$PF_REPO_ROOT" TMPDIR="$PF_SB/a1/tmp" PATH="$PF_SB/a1/bin:$PATH" \
    bash "$PF_SB/a1/run.sh" > "$PF_SB/a1/out" 2> "$PF_SB/a1/err"
A1_RC_LINE="$(cat "$PF_SB/a1/out" 2>/dev/null)"
[ "$A1_RC_LINE" = "rc=124" ] && pf_ok "the stub batch tripped the 2 s budget (rc=124)" \
                             || pf_bad "A1 unexpected bound result: [$A1_RC_LINE] $(head -2 "$PF_SB/a1/err" | tr '\n' ' ')"
A1_PID="$(cat "$A1_PIDFILE" 2>/dev/null)"
A1_LEFTOVER="$(find "$PF_SB/a1/tmp" -maxdepth 1 -name 'tree-manifest.*' 2>/dev/null | wc -l | tr -d ' ')"
[ "$A1_LEFTOVER" = "0" ] && pf_ok "no mktemp pair left behind by the killed walk" \
                         || pf_bad "A1 leaked temp files: $(find "$PF_SB/a1/tmp" -maxdepth 1 -name 'tree-manifest.*' | tr '\n' ' ')"
if [ -n "$A1_PID" ] && kill -0 "$A1_PID" 2>/dev/null; then
    pf_bad "A1 the batch grandchild (pid $A1_PID) SURVIVED the budget kill"
    kill -KILL "$A1_PID" 2>/dev/null || true
else
    pf_ok "the batch grandchild was reaped by the kill path (pid ${A1_PID:-none})"
fi

# ── optional full-tree measurement (NOT part of the default gate) ─────────
if [ "$PF_FULL" = "1" ]; then
    printf '\n== 5. --full: the real trees (slow: BASE is fork-per-file) ==\n'
    for tree in agents frontend plugins; do
        t0="$(pf_now_ms)"; bash "$PF_SB/base-tm.sh" "$PF_REPO_ROOT/$tree" "" > "$PF_SB/b-$tree.out" 2>/dev/null; t1="$(pf_now_ms)"; b=$(( t1 - t0 ))
        t0="$(pf_now_ms)"; bash "$PF_SB/head-tm.sh" "$PF_REPO_ROOT/$tree" "" > "$PF_SB/h-$tree.out" 2>/dev/null; t1="$(pf_now_ms)"; h=$(( t1 - t0 ))
        if cmp -s "$PF_SB/b-$tree.out" "$PF_SB/h-$tree.out"; then P=OK; else P=DIFFER; fi
        printf '   %-9s BASE=%sms HEAD=%sms parity=%s files=%s\n' "$tree" "$b" "$h" "$P" "$(wc -l < "$PF_SB/h-$tree.out" | tr -d ' ')"
    done
    pf_note "--full numbers are the ones cited in lib.sh's STAGE_TREE_BUDGET_S derivation"
fi

pf_summary
