#!/usr/bin/env bash
# Test Pack: metrics_fe_dual_side_test
# The FE-only T1 metrics dashboard (wi-32a99618) gate — Tier 1.
#
# DUAL-SIDE: the pack runs in the BASE worktree and in the HEAD worktree
# with the IDENTICAL command shape; only the file LIST differs by
# construction (the two-class declaration of the blast-radius recipe):
#
#   D1 (lane-owned, HEAD-only) — the lane's own FE specs, 32 declared:
#       src/app/models/metrics.model.spec.ts              (metrics-fe-models)
#       src/app/services/metrics.service.spec.ts         (metrics-fe-service)
#       src/app/components/charts/house-svg-renderer/*.spec.ts
#                                                          (metrics-fe-house-svg-renderer)
#       src/app/components/charts/renderer-registry.spec.ts
#                                                          (metrics-fe-renderer-registry)
#       src/app/pages/metrics/**/*.spec.ts                (metrics-fe-page)
#       src/app/pages/metrics/metrics.bindings.pins.spec.ts(metrics-fe-bindings-pins)
#       src/app/app.resources-retirement.spec.ts           (metrics-fe-route-retirement)
#     A base-side run of these is IMPOSSIBLE BY CONSTRUCTION (the files do
#     not exist at the base) — the absence is recorded as
#     SKIPPED-BY-CONSTRUCTION, never as a pass and never as a failure.
#
#   D2 (dual-side comparable) — the route-table + models-barrel blast
#     radius, 4 declared (both revs) + the DERIVED set (run time, never
#     hand-maintained): the specs that import the MODELS BARREL (the
#     barrel gained an `export * from './metrics.model'` — a name
#     collision would break any of them), plus the specs that READ the
#     route table.
#
# MEMBERSHIP RULE (the H-2 closure): D2_DECLARED admits ONLY files that
# exist at BOTH revs — a HEAD-only suite belongs to D1 (or to the derived
# D2_DERIVED set, which is resolved per rev). Adding a HEAD-only file to
# D2_DECLARED would make jest SILENTLY DROP it at the base rev and
# reintroduce exactly the false-green class this pack now closes. The
# route-table replacement coverage (app.resources-retirement.spec.ts)
# rides D1 and is ALSO picked up by the D2_DERIVED grep at HEAD.
#
# DECLARED-FILE EXISTENCE ASSERT: jest SILENTLY DROPS a positional that
# does not resolve to a test file, so a stale declared path made a leg
# pass while the suite it names never ran anywhere. Every declared file
# is now asserted to exist BEFORE its leg runs — D1_FILES on the HEAD
# leg, D2_DECLARED on BOTH legs (that is the list's membership
# invariant). A missing file is reported BY NAME and becomes a FAILURES
# increment — never a silent pass. (D2_DERIVED needs no assertion: its
# entries are grep hits over existing files, so they exist by
# construction.)
#
# Legs (each one prints its EXIT CODE and an error summary):
#   1  D1 jest            (HEAD-only)
#   2  D2 declared jest   (both legs)
#   3  D2 derived jest    (both legs — the widened supplementary leg)
#   4  scoped type-check  (HEAD-only) — the P5-M9 surface: the availability
#      union is WIDENED, and a narrowed union breaks this compile.
#   5  ng build --configuration development  — the AOT leg
#   6  ng build --configuration production   — the ONLY configuration that
#      carries `budgets` + `outputHashing` (AM-23). Any budget WARN is a
#      RECORDED FINDING, never an auto-fix and never a silent pass.
#   7  package.json dependency diff — the D-1 EXPECTED-DEPENDENCY-SET
#      guard: the dependency delta against BASE_REF must be EXACTLY
#      `chart.js` (dependencies) + `jest-canvas-mock` (devDependencies),
#      asserted BY NAME (never by a raw line count).
#
# FENCES: the FE has no daemon and no database; the pack never installs
# dependencies (a missing node_modules is a LOUD FAIL, never a green
# skip), never starts a daemon, and never touches a live port.
#
# Exit codes: 0 = PASS, 1 = FAIL, 124 = TIMEOUT (the self-cap).
set -uo pipefail
# errexit is deliberately OFF: each leg captures its own exit code and
# failures accumulate in FAILURES (the pack never dies mid-run). The OFF
# state is asserted EXPLICITLY here rather than inherited from the
# caller's shell — that is what makes the mid-script `set +e` no-ops
# provably dead (L-13) and safe to sweep.
set +e

PACK_START=${SECONDS:-0}
PACK_BUDGET_S=900

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
# The dual-side BASE leg runs THIS script (the HEAD tree's copy) against a
# throwaway DETACHED base worktree — the base tree DOES carry the script
# (it landed in a33943bd7), but that copy PREDATES this lane's amendments
# to the pack (+214/-33 across the lane), so ONE script — this HEAD copy —
# must run against BOTH trees; hence the project dir is overridable.
if [ -n "${METRICS_FE_PROJECT_DIR:-}" ]; then
    PROJECT_DIR="$METRICS_FE_PROJECT_DIR"
fi
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="${METRICS_FE_LOG_DIR:-/tmp/metrics-fe-gate}"

EXPECTED_BRANCH="${EXPECTED_BRANCH:-feature/central-metrics-v1}"
BASE_REF="${BASE_REF:-origin/latest}"

D1_MARKER="src/app/app.resources-retirement.spec.ts"

mkdir -p "$LOG_DIR"

echo "=== Test Pack: metrics_fe_dual_side_test ==="
echo "PROJECT_DIR=$PROJECT_DIR"

# ─── Stage 0: rev-parse bracket (worktree drift gate) ─────────────────────
echo ""
echo "--- Stage 0: worktree bracket ---"
CURRENT_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo UNKNOWN)
START_SHA=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo UNKNOWN)
echo "BRANCH=$CURRENT_BRANCH"
echo "GIT_SHA=$START_SHA"
echo "EXPECTED_BRANCH=$EXPECTED_BRANCH"
echo "BASE_REF=$BASE_REF"
# A DETACHED worktree is the BASE leg of a dual-side comparison (the
# house recipe: two throwaway detached worktrees). ALLOW_DETACHED=1
# waives the branch-name gate for that leg ONLY; the SHA bracket still
# holds.
if [ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" ]; then
    if [ "${ALLOW_DETACHED:-0}" = "1" ] && [ "$CURRENT_BRANCH" = "HEAD" ]; then
        echo "DETACHED: the branch gate is waived for this leg (ALLOW_DETACHED=1)"
    else
        echo "RESULT: FAIL (DRIFT) — branch=$CURRENT_BRANCH expected=$EXPECTED_BRANCH"
        exit 1
    fi
fi

check_cap() {
    local now=${SECONDS:-0}
    local elapsed=$(( now - PACK_START ))
    if [ "$elapsed" -ge "$PACK_BUDGET_S" ]; then
        echo "RESULT: TIMEOUT (self-cap ${PACK_BUDGET_S}s breached at ${elapsed}s)"
        exit 124
    fi
}

# ─── Stage 0b: the dependency fence (a missing tree is a LOUD fail) ───────
echo ""
echo "--- Stage 0b: dependency fence ---"
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "node_modules MISSING under $FRONTEND_DIR"
    echo "RESULT: FAIL (MISSING-DEPENDENCIES — install them before the gate; the pack never installs)"
    exit 1
fi
for bin in jest tsc ng; do
    if [ ! -x "$FRONTEND_DIR/node_modules/.bin/$bin" ]; then
        echo "missing local bin: node_modules/.bin/$bin"
        echo "RESULT: FAIL (MISSING-DEPENDENCIES)"
        exit 1
    fi
done
echo "local bins: jest tsc ng — OK"

cd "$FRONTEND_DIR"
export CI=1

# The D1 marker decides whether this is the HEAD leg or the BASE leg.
# The marker MUST be a file this lane CREATED — it must be ABSENT at every
# base rev and PRESENT at the head. It used to be
# `src/app/models/metrics.model.spec.ts`, which the v0.28.6
# central-metrics-v1 lane had already shipped: it EXISTS at this lane's
# base (c4161335b), so the BASE leg mis-discriminated as HEAD. Two
# consequences, both now closed: (1) the base leg ran d1 against a MIXED
# set (8 inherited files present, the lane's own absent → jest silently
# dropped the absent ones — the very false-green class this pack exists to
# kill, and the existence assert turned it loud); (2) Leg 7's expected
# dependency set was forced to the HEAD's `["chart.js"]`/`["jest-canvas-mock"]`
# while the base tree's package.json delta against BASE_REF is EMPTY → a
# false "D-1 DEPENDENCY-SET BREACH".
# `src/app/app.resources-retirement.spec.ts` (this lane's Phase-7 artifact)
# is PROVEN absent at the base and present at the head.
if [ -f "$FRONTEND_DIR/$D1_MARKER" ]; then
    IS_HEAD_LEG=1
else
    IS_HEAD_LEG=0
fi
echo "LEG_KIND=$([ "$IS_HEAD_LEG" = 1 ] && echo HEAD || echo BASE)"

# ─── The file lists ───────────────────────────────────────────────────────
# D1_FILES — the lane-owned suites (32 declared). Every entry MUST exist on
# the HEAD leg (asserted below). TWO entries were ADDED on 2026-11-04 (the
# R4 closure): `timeline-series-compat.spec.ts` (the B-1 vocabulary shim's
# own spec — it sat in NO declared set, so the pack never ran it) and
# `chart-panel.anomaly-order.spec.ts` (the R1 arrival-order contract). Both
# are HEAD-only lane specs, which is exactly D1's membership rule.
# The svg-line-chart entry was REMOVED here:
# Phase 4 deleted that spec, and jest silently drops an unresolvable
# positional — the list kept naming a file that no longer ran anywhere.
D1_FILES=(
  "src/app/models/metrics.model.spec.ts"
  "src/app/services/metrics.service.spec.ts"
  "src/app/pages/metrics/metrics.component.spec.ts"
  "src/app/pages/metrics/metrics-gear-menu.spec.ts"
  "src/app/pages/metrics/metrics.bindings.pins.spec.ts"
  "src/app/pages/metrics/metrics.bundle-guard.spec.ts"
  "src/app/pages/metrics/overview/overview-tab.component.spec.ts"
  "src/app/pages/metrics/charts/charts-tab.component.spec.ts"
  "src/app/pages/metrics/charts/chart-panel.component.spec.ts"
  "src/app/pages/metrics/charts/chart-panel.anomaly-order.spec.ts"
  "src/app/pages/metrics/families/families-tab.component.spec.ts"
  "src/app/components/charts/house-svg-renderer/house-svg-renderer.spec.ts"
  "src/app/components/charts/house-svg-renderer/house-svg-engine-uniqueness.spec.ts"
  "src/app/components/charts/renderer-registry.spec.ts"
  "src/app/pages/metrics/metrics-units.spec.ts"
  "src/app/pages/metrics/metrics-range.spec.ts"
  "src/app/pages/metrics/metrics.banner-geometry.spec.ts"
  "src/app/pages/metrics/metrics.baseline.pins.spec.ts"
  "src/app/pages/metrics/metrics.door-state.spec.ts"
  "src/app/pages/metrics/metrics.tsconfig.spec.ts"
  "src/app/pages/metrics/metrics.e2e-retirement.spec.ts"
  "src/app/pages/metrics/tabs.manifest.spec.ts"
  "src/app/pages/metrics/tabs/central-tab.component.spec.ts"
  "src/app/pages/metrics/tabs/resource-tab.component.spec.ts"
  "src/app/pages/metrics/resource/anomalies/anomalies-panel.component.spec.ts"
  "src/app/pages/metrics/resource/live/live-tab.component.spec.ts"
  "src/app/pages/metrics/resource/timeline/data-gaps.spec.ts"
  "src/app/pages/metrics/resource/timeline/marker-label.spec.ts"
  "src/app/pages/metrics/resource/timeline/timeline-drilldown.spec.ts"
  "src/app/pages/metrics/resource/timeline/timeline-series-compat.spec.ts"
  "src/app/pages/metrics/resource/timeline/timeline-tab.component.spec.ts"
  "src/app/app.resources-retirement.spec.ts"
)

# D2_DECLARED — the both-revs composition (4 declared). Every entry MUST
# exist at BOTH revs (asserted on each leg). The resources-gear-menu entry
# was REMOVED here: Phase 7 deleted that spec, so at HEAD it was silently
# dropped while the base leg still ran it — the dual-side "identical
# command shape" was eroding asymmetrically.
D2_DECLARED=(
  "src/app/app.routes.spec.ts"
  "src/app/app.component.spec.ts"
  "src/app/pages/mcps-gateway/mcps-gateway.pins.spec.ts"
  "src/app/pages/maintenance/maintenance.bindings.pins.spec.ts"
)

# The DERIVED dual-side set: the specs that import the models barrel plus
# the specs that read the route table / the gear menu.
D2_DERIVED=()
while IFS= read -r spec_path; do
    D2_DERIVED+=("$FRONTEND_DIR/$spec_path")
done < <(
  {
    grep -rlE "from '[^']*/models'" src --include='*.spec.ts' 2>/dev/null || true
    grep -rlE "app\.routes|app\.ts'|app\.html'" src --include='*.spec.ts' 2>/dev/null || true
  } | sort -u
)

# ─── The declared-file existence assertion (the false-green closure) ─────
# jest SILENTLY DROPS a positional that does not resolve to a test file.
# A stale declared path therefore produced a GREEN leg whose named suite
# never executed — the defect the H-2 escalation named. Every declared
# file is asserted to exist BEFORE the leg runs; a missing file is
# reported BY NAME and becomes a FAILURES increment — never a silent pass.
assert_declared_files() {
    local leg_name="$1"
    shift
    local missing=0
    local f
    for f in "$@"; do
        if [ ! -f "$FRONTEND_DIR/$f" ]; then
            echo "${leg_name}: MISSING DECLARED SPEC ${f}"
            missing=1
        fi
    done
    echo "${leg_name}_DECLARED_FILE_COUNT=$#"
    if [ "$missing" -ne 0 ]; then
        echo "${leg_name}: FAILED (declared-file existence assertion — a declared spec does not exist)"
        return 1
    fi
    return 0
}

run_jest_leg() {
    local leg_name="$1"
    shift
    local log="$LOG_DIR/${leg_name}.log"
    local exit_code=0
    echo ""
    echo "--- ${leg_name} ---"
    if [ "$#" -eq 0 ]; then
        echo "${leg_name}: SKIPPED-BY-CONSTRUCTION (no files on this leg)"
        return 0
    fi
    timeout 600 ./node_modules/.bin/jest --ci --silent --runInBand "$@" 2>&1 | tee "$log"
    exit_code=${PIPESTATUS[0]}
    echo "${leg_name}_EXIT=$exit_code"
    echo "${leg_name}_COUNTS: $(grep -E '^[[:space:]]*Tests:' "$log" | tail -1 || true)"
    echo "${leg_name}_ERROR_SUMMARY:"
    # jest prints a failing suite as `FAIL <path>` (and `FAILED <path>` for
    # a failed test) — the OLD grep (`^(FAILED|ERROR) `) matched NEITHER,
    # so a failing leg's summary read `(none)` (the verdict itself was
    # never affected: the exit code and the `Tests:` counts are correct).
    # Both forms are matched now.
    grep -E '^(FAILED|ERROR) |^FAIL ' "$log" | sed 's/^/  /' || echo "  (none)"
    if [ ! -s "$log" ]; then
        echo "${leg_name}: FAILED (the leg produced NO artifact — a leg that never ran is not green)"
        return 1
    fi
    return "$exit_code"
}

FAILURES=0

# ─── Leg 1: D1 (lane-owned) ───────────────────────────────────────────────
if [ "$IS_HEAD_LEG" = 1 ]; then
    if assert_declared_files "d1" "${D1_FILES[@]}"; then
        run_jest_leg "d1" "${D1_FILES[@]}" || FAILURES=$((FAILURES + 1))
    else
        FAILURES=$((FAILURES + 1))
    fi
else
    echo ""
    echo "--- d1 ---"
    echo "d1: SKIPPED-BY-CONSTRUCTION (the lane's spec files do not exist at the base)"
fi

# ─── Leg 2: D2 (declared composition) ─────────────────────────────────────
# The membership invariant: a D2_DECLARED file missing at EITHER rev is a
# composition error — asserted on both legs.
if assert_declared_files "d2_declared" "${D2_DECLARED[@]}"; then
    run_jest_leg "d2_declared" "${D2_DECLARED[@]}" || FAILURES=$((FAILURES + 1))
else
    FAILURES=$((FAILURES + 1))
fi

# ─── Leg 3: D2 (derived composition — the widened supplementary leg) ──────
run_jest_leg "d2_derived" "${D2_DERIVED[@]}" || FAILURES=$((FAILURES + 1))
echo "d2_derived_file_count=${#D2_DERIVED[@]}"

# ─── Leg 4: the scoped type-check (the P5-M9 surface) ─────────────────────
echo ""
echo "--- typecheck_scoped ---"
if [ "$IS_HEAD_LEG" = 1 ]; then
    TSC_LOG="$LOG_DIR/typecheck_scoped.log"
    TSC_EXIT=0
    timeout 300 ./node_modules/.bin/tsc --noEmit -p tsconfig.metrics-fe.json 2>&1 | tee "$TSC_LOG"
    TSC_EXIT=${PIPESTATUS[0]}
    echo "typecheck_scoped_EXIT=$TSC_EXIT"
    echo "typecheck_scoped_ERROR_SUMMARY:"
    grep -E 'error TS' "$TSC_LOG" | sed 's/^/  /' || echo "  (none)"
    if [ ! -s "$TSC_LOG" ] && [ "$TSC_EXIT" != 0 ]; then
        echo "typecheck_scoped: FAILED (no artifact)"
        FAILURES=$((FAILURES + 1))
    elif [ "$TSC_EXIT" != 0 ]; then
        FAILURES=$((FAILURES + 1))
    fi
else
    echo "typecheck_scoped: SKIPPED-BY-CONSTRUCTION (the scoped tsconfig has no inputs at the base)"
fi
check_cap

# ─── Leg 5: the AOT build leg (development configuration) ─────────────────
if [ "$IS_HEAD_LEG" = 1 ]; then
    echo ""
    echo "--- build_development (the AOT leg) ---"
    BUILD_DEV_LOG="$LOG_DIR/build_development.log"
    BUILD_DEV_EXIT=0
    timeout 600 ./node_modules/.bin/ng build --configuration development 2>&1 | tee "$BUILD_DEV_LOG"
    BUILD_DEV_EXIT=${PIPESTATUS[0]}
    echo "build_development_EXIT=$BUILD_DEV_EXIT"
    echo "build_development_ERROR_SUMMARY:"
    grep -E '^✘|ERROR|error' "$BUILD_DEV_LOG" | head -20 | sed 's/^/  /' || echo "  (none)"
    if [ ! -s "$BUILD_DEV_LOG" ]; then
        echo "build_development: FAILED (no artifact)"
        FAILURES=$((FAILURES + 1))
    elif [ "$BUILD_DEV_EXIT" != 0 ]; then
        FAILURES=$((FAILURES + 1))
    fi
    check_cap

    # ─── Leg 6: the production build leg (the ONLY budgets configuration) ──
    echo ""
    echo "--- build_production (the budgets leg — AM-23) ---"
    BUILD_PROD_LOG="$LOG_DIR/build_production.log"
    BUILD_PROD_EXIT=0
    timeout 900 ./node_modules/.bin/ng build --configuration production 2>&1 | tee "$BUILD_PROD_LOG"
    BUILD_PROD_EXIT=${PIPESTATUS[0]}
    echo "build_production_EXIT=$BUILD_PROD_EXIT"
    echo "build_production_ERROR_SUMMARY:"
    grep -E '^✘|ERROR|error' "$BUILD_PROD_LOG" | head -20 | sed 's/^/  /' || echo "  (none)"
    echo "build_production_BUDGET_WARNINGS (RECORDED FINDINGS, not auto-fixed):"
    grep -E 'WARNING|budget' "$BUILD_PROD_LOG" | sed 's/^/  /' || echo "  (none)"
    if [ ! -s "$BUILD_PROD_LOG" ]; then
        echo "build_production: FAILED (no artifact)"
        FAILURES=$((FAILURES + 1))
    elif [ "$BUILD_PROD_EXIT" != 0 ]; then
        FAILURES=$((FAILURES + 1))
    fi
    check_cap
else
    echo ""
    echo "build_development / build_production: SKIPPED-BY-CONSTRUCTION (the lane's FE surface does not exist at the base)"
fi

# ─── Leg 7: the package.json dependency diff (the D-1 guard) ─────────────
#
# The D-1 AMENDMENT: the zero-diff guard is re-purposed as an
# EXPECTED-DEPENDENCY-SET guard. The FE dependency delta must be EXACTLY
# `chart.js` (dependencies) + `jest-canvas-mock` (devDependencies) — and
# the assertion is BY NAME, never by a raw line count (a transitive bump
# must not break it, and a THIRD direct dependency must break it).
#
# The BASE leg predates the lane and therefore carries NO added direct
# dependency — its expected set is empty.
echo ""
echo "--- package_json_dependency_diff (D-1 expected-dependency-set guard) ---"
DEPDIFF_LOG="$LOG_DIR/package_json_diff.log"
git -C "$PROJECT_DIR" diff "$BASE_REF" -- frontend/package.json > "$DEPDIFF_LOG" 2>&1 || true
DEPDIFF_LINES=$(wc -l < "$DEPDIFF_LOG" | tr -d ' ')
echo "package_json_diff_lines=$DEPDIFF_LINES"
cat "$DEPDIFF_LOG"

if [ "$IS_HEAD_LEG" = 1 ]; then
    EXPECTED_DEPS='["chart.js"]'
    EXPECTED_DEV_DEPS='["jest-canvas-mock"]'
else
    EXPECTED_DEPS='[]'
    EXPECTED_DEV_DEPS='[]'
fi
echo "expected_dependencies=$EXPECTED_DEPS"
echo "expected_devDependencies=$EXPECTED_DEV_DEPS"

DEPSET_EXIT=0
DEPSET_RESULT=$(node -e '
const { execFileSync } = require("child_process");
const fs = require("fs");
const [projectDir, baseRef, expectedDepsRaw, expectedDevDepsRaw] = process.argv.slice(1);
const expectedDeps = JSON.parse(expectedDepsRaw);
const expectedDevDeps = JSON.parse(expectedDevDepsRaw);
const current = JSON.parse(fs.readFileSync(projectDir + "/frontend/package.json", "utf8"));
let base = { dependencies: {}, devDependencies: {} };
try {
  base = JSON.parse(
    execFileSync("git", ["-C", projectDir, "show", baseRef + ":frontend/package.json"], {
      encoding: "utf8",
    }),
  );
} catch (err) {
  base = { dependencies: {}, devDependencies: {} };
}
const delta = (section) => {
  const now = current[section] || {};
  const was = base[section] || {};
  return {
    added: Object.keys(now).filter((name) => !(name in was)).sort(),
    removed: Object.keys(was).filter((name) => !(name in now)).sort(),
    changed: Object.keys(now).filter((name) => name in was && now[name] !== was[name]).sort(),
  };
};
const deps = delta("dependencies");
const devDeps = delta("devDependencies");
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const ok =
  same(deps.added, expectedDeps) &&
  same(devDeps.added, expectedDevDeps) &&
  deps.removed.length === 0 &&
  devDeps.removed.length === 0 &&
  deps.changed.length === 0 &&
  devDeps.changed.length === 0;
console.log(
  JSON.stringify(
    { dependencies: deps, devDependencies: devDeps, expected: { dependencies: expectedDeps, devDependencies: expectedDevDeps }, ok },
    null,
    2,
  ),
);
process.exit(ok ? 0 : 1);
' "$PROJECT_DIR" "$BASE_REF" "$EXPECTED_DEPS" "$EXPECTED_DEV_DEPS") || DEPSET_EXIT=$?
echo "package_json_dependency_set=$DEPSET_RESULT"
if [ "$DEPSET_EXIT" != "0" ]; then
    echo "RESULT: FAIL (D-1 DEPENDENCY-SET BREACH — the FE dependency delta is not exactly the expected set)"
    FAILURES=$((FAILURES + 1))
fi

# ─── Mid-run SHA drift check ───────────────────────────────────────────────
END_SHA=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo UNKNOWN)
echo ""
echo "GIT_SHA_END=$END_SHA"
if [ "$END_SHA" != "$START_SHA" ]; then
    echo "RESULT: FAIL (DRIFT-MID-RUN: $START_SHA -> $END_SHA)"
    exit 1
fi

echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
fi
echo "RESULT: FAIL (failed legs: $FAILURES)"
exit 1
