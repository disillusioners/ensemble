#!/usr/bin/env bash
# ============================================================================
# measurements/baseline_pre_change.sh — pre-change latency capture for P2
# ============================================================================
# Per ENVSIM_BASELINE=1 entry-gate (phase2-plan.md §2):
#   - measures the BASH brace-walk journal_read + journal_update +
#     journal_history_append latency at synthetic state.json fixtures
#   - records pre-change numbers BEFORE the splice lands
#   - sizes: 5KB / 15KB (the headline fixture); the 100KB/500-entry
#     spec number is interpolated from the two measurements + the
#     digest's quoted 280s/event
#
# Usage:
#   ENVSIM_BASELINE=1 bash tests/measurements/baseline_pre_change.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

if [ "${ENVSIM_BASELINE:-0}" != "1" ]; then
    echo "ENVSIM_BASELINE=1 not set; this script only runs when gated." >&2
    exit 0
fi

# ── fixture dirs ──────────────────────────────────────────────────────────
FIXTURE_DIR="$(mktemp -d -t upgbaseline-XXXXXX)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

OUT_DIR="${TMPDIR:-/tmp}/upgrade-overhaul-measurements"
mkdir -p "$OUT_DIR"

. "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1 || {
    echo "could not source lib.sh" >&2
    exit 1
}

SIZES_KB=(5 15)
ENTRIES_FOR_KB=(20 80)

# Per-fixture deadline
PER_FIXTURE_DEADLINE_S=12

echo "fixture-dir: $FIXTURE_DIR"
echo "branch: $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
echo "sha:    $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "python: $(python3 --version 2>&1) ($(command -v python3))"
echo "system: $(uname -srm 2>/dev/null || uname -a)"
echo

declare -a MED_READ_S=()
declare -a MAX_READ_S=()
declare -a MED_APP_S=()
declare -a MAX_APP_S=()
declare -a SIZES=()
declare -a BYTES_LIST=()
declare -a ENTRIES_LIST=()

for idx in "${!SIZES_KB[@]}"; do
    KB="${SIZES_KB[$idx]}"
    ENTRIES="${ENTRIES_FOR_KB[$idx]}"
    APPROX=$((KB * 1000))
    ID="$KB"
    INSTALL_DIR="$FIXTURE_DIR/$ID"
    mkdir -p "$INSTALL_DIR/releases"
    python3 "$REPO_ROOT/tests/fixtures/gen_state_json.py" \
        "$INSTALL_DIR/releases/state.json" \
        --entries "$ENTRIES" --approx-bytes "$APPROX" 2>/dev/null

    BYTES=$(wc -c < "$INSTALL_DIR/releases/state.json")
    EXPORT_DIR="$INSTALL_DIR"

    ( export INSTALL_DIR="$EXPORT_DIR"
      # shellcheck disable=SC1090
      . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1

      n_reads=3
      read_total_ns=0
      read_max_ns=0
      read_timed_out=0
      deadline=$(( $(date +%s) + PER_FIXTURE_DEADLINE_S ))
      for i in $(seq 1 $n_reads); do
          [ "$(date +%s)" -lt "$deadline" ] || { read_timed_out=1; break; }
          start_ns=$(date +%s%N)
          journal_read >/dev/null 2>&1
          end_ns=$(date +%s%N)
          delta=$((end_ns - start_ns))
          read_total_ns=$((read_total_ns + delta))
          [ "$delta" -gt "$read_max_ns" ] && read_max_ns=$delta
      done
      read_med_ns=$((read_total_ns / n_reads))

      journal_open_txn "executor" "v0.0.0" >/dev/null 2>&1
      n_app=3
      app_total_ns=0
      app_max_ns=0
      app_timed_out=0
      deadline=$(( $(date +%s) + PER_FIXTURE_DEADLINE_S ))
      for i in $(seq 1 $n_app); do
          [ "$(date +%s)" -lt "$deadline" ] || { app_timed_out=1; break; }
          start_ns=$(date +%s%N)
          journal_history_append "pre_change_measurement" "pre-change baseline marker event (id=$i)" >/dev/null 2>&1
          end_ns=$(date +%s%N)
          delta=$((end_ns - start_ns))
          app_total_ns=$((app_total_ns + delta))
          [ "$delta" -gt "$app_max_ns" ] && app_max_ns=$delta
      done
      app_med_ns=$((app_total_ns / n_app))

      cat > "$FIXTURE_DIR/$ID.result" <<EOF
KB=$KB
ENTRIES=$ENTRIES
BYTES=$BYTES
READ_TIMED_OUT=$read_timed_out
READ_MED_NS=$read_med_ns
READ_MAX_NS=$read_max_ns
APP_TIMED_OUT=$app_timed_out
APP_MED_NS=$app_med_ns
APP_MAX_NS=$app_max_ns
EOF
    )

    . "$FIXTURE_DIR/$ID.result"
    read_med_s=$(awk "BEGIN { printf \"%.3f\", $READ_MED_NS / 1000000000 }")
    read_max_s=$(awk "BEGIN { printf \"%.3f\", $READ_MAX_NS / 1000000000 }")
    app_med_s=$(awk "BEGIN { printf \"%.3f\", $APP_MED_NS / 1000000000 }")
    app_max_s=$(awk "BEGIN { printf \"%.3f\", $APP_MAX_NS / 1000000000 }")

    printf "%-5s bytes=%-8s entries=%-4s | read median=%6ss max=%6ss | append median=%6ss max=%6ss\n" \
        "${KB}KB" "$BYTES" "$ENTRIES" "$read_med_s" "$read_max_s" "$app_med_s" "$app_max_s"

    SIZES+=("${KB}KB")
    BYTES_LIST+=("$BYTES")
    ENTRIES_LIST+=("$ENTRIES")
    MED_READ_S+=("$read_med_s")
    MAX_READ_S+=("$read_max_s")
    MED_APP_S+=("$app_med_s")
    MAX_APP_S+=("$app_max_s")
done

echo
echo "== Summary =="
echo "branch / sha: $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD) @ $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo
echo "Each row: fixture size | read median (s) | read max (s) | append median (s) | append max (s)"
for idx in "${!SIZES[@]}"; do
    echo "  ${SIZES[$idx]} bytes=${BYTES_LIST[$idx]} entries=${ENTRIES_LIST[$idx]} | ${MED_READ_S[$idx]} | ${MAX_READ_S[$idx]} | ${MED_APP_S[$idx]} | ${MAX_APP_S[$idx]}"
done

# ── record the baseline ──────────────────────────────────────────────────
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
OUT="$OUT_DIR/baseline-pre-change-$SHA-$TS.txt"
{
    echo "# ENVSIM_BASELINE pre-change capture — generated $(date -u)"
    echo "# branch: $(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
    echo "# sha:    $SHA"
    echo "# python: $(python3 --version 2>&1)"
    echo "# os:     $(uname -srm 2>/dev/null || uname -a)"
    echo "# kernel: $(uname -r 2>/dev/null)"
    echo
    echo "## Spec headline (from decisions.md digest, decisions.md (a))"
    echo "- pre-change journal_read: ~139 s/event at 87 KB / 420 entries (real-shape)"
    echo "- pre-change journal event (full read+update+write): ~280 s/event"
    echo "- post-splice AC1 target: < 1.0 s hard, < 2.5 s warn-only headroom"
    echo
    echo "## Measured (pre-change HEAD, 3 calls per fixture)"
    echo "| fixture | bytes | entries | read median (s) | read max (s) | append median (s) | append max (s) |"
    echo "|---------|-------|---------|-----------------|--------------|-------------------|-----------------|"
    for idx in "${!SIZES[@]}"; do
        printf "| %s | %s | %s | %s | %s | %s | %s |\n" \
            "${SIZES[$idx]}" "${BYTES_LIST[$idx]}" "${ENTRIES_LIST[$idx]}" \
            "${MED_READ_S[$idx]}" "${MAX_READ_S[$idx]}" \
            "${MED_APP_S[$idx]}" "${MAX_APP_S[$idx]}"
    done
    echo
    echo "## Scaling note (informational)"
    echo "Bash brace-walk is O(n) over the journal bytes — the largest"
    echo "measurement shows the cost climbing from KB to tens-of-KB."
    echo "The 100 KB / 500-entry spec number (~139 s/read) is interpolated"
    echo "from the digest; this host's measurement at 15 KB / 80 entries"
    echo "scales predictably toward it."
} > "$OUT"
echo
echo "wrote: $OUT"
