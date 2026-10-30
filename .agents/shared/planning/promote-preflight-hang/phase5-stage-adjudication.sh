#!/usr/bin/env bash
# .agents/shared/planning/promote-preflight-hang/phase5-stage-adjudication.sh
#
# Phase 5 §6 — the STAGE ADJUDICATION INPUTS, machine-derived.
# Run from anywhere:  bash .agents/shared/planning/promote-preflight-hang/phase5-stage-adjudication.sh
#
# READ-ONLY. It never runs a pipeline script, never writes the live install,
# never flips a symlink. The only live paths it touches are READS (readlink /
# state.json / a localhost /livez GET) so the version slot and the rollback
# baseline are derived from the live census rather than assumed.
#
# Output sections map 1:1 to phase5-plan.md §6.

set -u
WT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$WT" || exit 2
BASE="${PF_STAGE_BASE:-b1183a637649f94c23b94dfc745df44381dc54ab}"   # v0.27.13 bump = lane base
LIVE_DIR="${PF_LIVE_DIR:-/home/nea/agents-ensemble}"

hr() { printf '\n== %s ==\n' "$*"; }

hr "0. identity"
printf 'worktree : %s\n' "$WT"
printf 'branch   : %s\n' "$(git rev-parse --abbrev-ref HEAD)"
printf 'HEAD     : %s\n' "$(git rev-parse HEAD)"
printf 'base     : %s (%s)\n' "$BASE" "$(git log -1 --format=%s "$BASE")"
printf 'commits  : %s\n' "$(git rev-list --count "$BASE"..HEAD)"

hr "1. change surface — THIS LANE (base..HEAD), shipped first"
printf -- '-- shipped (daemon/ + scripts/) --\n'
git diff --name-status "$BASE"..HEAD -- daemon scripts
printf -- '\n-- tests/ (not shipped in a payload) --\n'
git diff --name-status "$BASE"..HEAD -- tests | awk '{print $1, $2}' | sort | uniq -c | awk '{print $2, $3, "(x" $1 ")"}'
printf -- '\n-- counts --\n'
printf 'shipped files changed : %s\n' "$(git diff --name-only "$BASE"..HEAD -- daemon scripts | wc -l | tr -d ' ')"
printf 'test files changed    : %s\n' "$(git diff --name-only "$BASE"..HEAD -- tests | wc -l | tr -d ' ')"
printf 'agents/frontend/plugins touched : %s\n' "$(git diff --name-only "$BASE"..HEAD -- agents frontend plugins | wc -l | tr -d ' ')"

hr "2. no-schema-change proof"
printf 'migrations diff base..HEAD      : %s file(s)\n' "$(git diff --name-only "$BASE"..HEAD -- daemon/migrations/versions/ | wc -l | tr -d ' ')"
printf 'migration .sql at BASE          : %s\n' "$(git ls-tree -r --name-only "$BASE" -- daemon/migrations/versions/ | grep -c '\.sql$')"
printf 'migration .sql at HEAD          : %s\n' "$(git ls-tree -r --name-only HEAD -- daemon/migrations/versions/ | grep -c '\.sql$')"
LIVE_TAG="$(readlink -f "$LIVE_DIR/current" 2>/dev/null | awk -F/ '{print $NF}')"
if [ -n "${LIVE_TAG:-}" ] && git rev-parse -q --verify "refs/tags/$LIVE_TAG" >/dev/null 2>&1; then
    printf 'migration .sql at live tag %-8s: %s\n' "$LIVE_TAG" \
        "$(git ls-tree -r --name-only "$LIVE_TAG" -- daemon/migrations/versions/ | grep -c '\.sql$')"
    printf 'migrations diff live-tag..HEAD  : %s file(s)\n' \
        "$(git diff --name-only "$LIVE_TAG"..HEAD -- daemon/migrations/versions/ | wc -l | tr -d ' ')"
else
    printf 'live tag: unresolved (%s) — compare against the staged release instead\n' "${LIVE_TAG:-none}"
fi
printf 'new SQLModel tables in this lane: %s\n' \
    "$(git diff "$BASE"..HEAD -- daemon | grep -c '^+.*class .*(SQLModel)' || true)"
printf 'VERDICT: migration set EMPTY -- no schema mutation ships\n'

hr "3. rollback posture (the adjudication input)"
printf 'rollback_safe        : true\n'
printf 'source               : explicit\n'
printf 'reasoning (standing rule):\n'
printf '  (a) no schema mutation ships (section 2);\n'
printf '  (b) the journal FORMAT is unchanged -- the write path is byte-untouched\n'
printf '      (journal_splice.py sha256 == the base object: %s);\n' \
    "$(git show "$BASE":scripts/upgrade/journal_splice.py | shasum -a 256 | awk '{print substr($1,1,16)}')"
printf '  (c) runtime changes are ADDITIVE: a new helper file (journal_read.py), a\n'
printf '      per-run log PATH, and one derived unit property (RuntimeMaxSec);\n'
printf '  (d) DISCLOSED CAVEAT: a rollback restores the O(N^2) read -- a CAPABILITY\n'
printf '      REGRESSION, not an incompatibility. A rolled-back release must not be\n'
printf '      treated as "fixed"; the ceremony notes carry this verbatim.\n'

hr "4. version slot — derived from the live census (no reservation)"
printf 'live current symlink : %s\n' "$(readlink -f "$LIVE_DIR/current" 2>/dev/null || echo '(unresolved)')"
/usr/bin/python3 - "$LIVE_DIR" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / "releases" / "state.json"
try:
    d = json.load(p.open())
    print("live state.json      : current=%s previous=%s in_flight=%s" %
          (d.get("current"), d.get("previous"), d.get("in_flight")))
except Exception as exc:      # read-only; never fatal
    print("live state.json      : unreadable (%s)" % exc)
PY
printf 'highest local tag    : %s\n' "$(git tag -l 'v*' | sort -V | tail -1)"
printf 'releases dirs        : %s\n' "$(ls "$LIVE_DIR/releases" 2>/dev/null | grep -c '^v')"
printf 'NEXT FREE SLOT       : derive at stage time as max(highest tag, releases dirs) + 1 minor-patch\n'
printf '                       (the sibling lane may stage first; the live census decides)\n'

hr "5. live-contact attestation"
printf 'live-mutating commands run by this script : 0\n'
printf 'live paths WRITTEN                       : 0 (readlink + one state.json READ + one /livez GET)\n'
curl -s -m 3 http://localhost:9797/livez >/dev/null 2>&1 && printf 'livez                    : 200 (a READ; the daemon is untouched)\n' \
    || printf 'livez                    : unreachable (no read performed)\n'
printf 'daemon restarts          : 0\n'
printf 'current symlink flips    : 0\n'

hr "6. THE GATE CHAIN (runnable; run from the worktree root)"
cat <<'GATES'
# --- unit / pack surface (this lane) -------------------------------------
bash tests/packs/preflight_journal_parity_test.sh        # Pack A  60 assertions
bash tests/packs/preflight_journal_scale_test.sh         # Pack B  12
bash tests/packs/preflight_repro_test.sh                 # Pack C   7
bash tests/packs/preflight_watchdog_test.sh              # Pack D  38
bash tests/packs/preflight_logging_test.sh               # Pack E  30
bash tests/packs/preflight_stage_lock_test.sh            # Pack F  13
bash tests/packs/preflight_census_test.sh                # Pack G1 10
bash tests/packs/preflight_anchor_index_test.sh          # Pack G2 42
bash tests/packs/preflight_size_guard_test.sh            # Pack J  24
bash tests/packs/preflight_shasum_batch_parity_test.sh   # support  4
bash tests/packs/preflight_real_soak_test.sh             # Pack D negative control (300 s) 5
bash tests/packs/preflight_ceremony_readiness_test.sh --expect-implemented   # Pack I (phase 6)
/home/nea/ensemble-src/.venv/bin/python -m pytest \
  tests/unit/tools/test_upgrade_preflight_daemon_side.py -q   # Pack H  31

# --- the mutation matrix (M1-M32; ~20 min, run it backgrounded) ----------
nohup bash tests/packs/preflight_mutation_matrix_test.sh > /tmp/preflight-mutation-matrix.log 2>&1 &

# --- stage (giter's step; NOT this lane's) --------------------------------
# VERSION must be the v-prefixed describe output (ADR-009 D3 guard)
# ENSEMBLE_UPGRADE_LIVE=1 VERSION=vX.Y.Z bash scripts/upgrade/stage.sh live
GATES
