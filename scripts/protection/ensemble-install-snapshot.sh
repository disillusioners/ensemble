#!/bin/bash
# ── REPO SOURCE OF TRUTH — mirror of deployed host artifact ─────────────
# Host path: /usr/local/sbin/ensemble-install-snapshot.sh
# Commission: install-dir protection layers 1+3+4 (incident 891820f3 followup,
# 2026-10-12). Edit HERE, then redeploy to the host path above — see
# scripts/protection/README.md for the deployment map + enable commands.
# ────────────────────────────────────────────────────────────────────────
# ============================================================================
# ensemble-install-snapshot.sh — Layer 3 hardlink snapshots of the live
# ensemble install tree (/home/nea/agents-ensemble).
#
# INCIDENT NOTE (2026-10-12): the install dir /home/nea/agents-ensemble was
# deleted twice in one day (≈12:14 and ≈19:11 UTC) by agent-authored test
# code (instance e312eb09, worktree ensemble-src-wt-upg-overhaul,
# tests/test_journal_splice.sh §2 — no-op HOME sandbox plus
# `rm -rf "$HOME/agents-ensemble"` cleanup; test passed 40/40 while deleting
# production). Immediate root cause = TEST SCRIPT defect in the
# upgrade-speed overhaul lane, ALREADY manually fixed by the user on
# 2026-10-12. This snapshot service is the DEFENSE-IN-DEPTH structural
# backstop against the damage class (future script regressions, agent
# mistakes, human error) — it is NOT tied to the one already-fixed script.
# Plan: /home/nea/ensemble-src/.agents/shared/protection-plan-install-dir-20261012.md
# (Layer 3; commissioned by leader 2026-10-12, install-dir protection 1+3+4).
#
# Mechanism: rsync -aHAX --link-dest=<newest prior snapshot>. Unchanged files
# become hardlinks to the previous snapshot (near-zero cost); changed files
# are fresh inodes. Deliberate isolation property: snapshot inodes are NEVER
# shared with the live tree, so in-place writes to the live tree cannot
# corrupt a snapshot, and deletion of the live tree cannot touch snapshot
# data. First snapshot is a full copy (tree ≈166 MB at commissioning).
# NOTHING is excluded: .env (secrets), releases/state.json, data/ — a
# restore reconstructs the full tree.
#
# Permissions: run as root; snapshot root /home/nea/.install-snapshots is
# root:root mode 700 BECAUSE snapshots contain .env secrets and MUST NOT be
# readable by the nea agent user.
#
# Retention: newest RETAIN=96 snapshots (96 × 30 min = 48 h at the timer
# cadence; plan floor is 24 h = 48 snapshots, target 48 h). Cadence is 30 min
# per the plan's prose (its `hourly.{0..N}` naming was a drafting artifact).
#
# Restore (runbook): stop the ensemble unit first, then as root:
#   rsync -aHAX <snap-dir>/ /home/nea/agents-ensemble/
#   chown -R <runtime-user>:<runtime-group> /home/nea/agents-ensemble
# (runtime user is nea:nea today; ensemble:ensemble after Layer 1 arms),
# then start the unit and verify livez.
# ============================================================================
set -euo pipefail

SNAP_ROOT="/home/nea/.install-snapshots"
SRC="/home/nea/agents-ensemble"
RETAIN="${RETAIN:-96}"              # 48h target at 30-min cadence
MIN_FREE_MB="${MIN_FREE_MB:-5120}"  # never fill the host while protecting it
TAG="ensemble-install-snapshot"

[ "$(id -u)" -eq 0 ] || { echo "$TAG: must run as root" >&2; exit 1; }

mkdir -p "$SNAP_ROOT"
chmod 700 "$SNAP_ROOT"
chown root:root "$SNAP_ROOT"

# Serialize overlapping runs (30-min timer + manual/first runs)
exec 9>"$SNAP_ROOT/.lock"
flock -n 9 || { echo "$TAG: previous run still active, skipping" >&2; exit 0; }

# Disk floor guard
free_mb="$(df -Pm "$SNAP_ROOT" | awk 'NR==2 {print $4}')"
if [ "$free_mb" -lt "$MIN_FREE_MB" ]; then
    logger -t "$TAG" -p daemon.crit \
        "SNAPSHOT SKIPPED: only ${free_mb}MB free (<${MIN_FREE_MB}MB floor) under ${SNAP_ROOT} — investigate"
    exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$SNAP_ROOT/snap-$stamp"

# Clear any stale staging dirs from an interrupted earlier run (our own area only)
rm -rf "$SNAP_ROOT"/.incoming-* 2>/dev/null || true

tmp="$SNAP_ROOT/.incoming-$stamp"
mkdir -p "$tmp"

# Link-dest = newest existing snapshot (names are UTC-sortable → sort = time order)
prev="$(ls -1d "$SNAP_ROOT"/snap-* 2>/dev/null | sort | tail -1 || true)"

if [ -n "$prev" ]; then
    rsync -aHAX --delete --link-dest="$prev" "$SRC/" "$tmp/"
else
    rsync -aHAX "$SRC/" "$tmp/"
fi

mv "$tmp" "$dest"   # atomic publish (same-filesystem rename)

# Retention prune — keep newest RETAIN snapshots only
ls -1d "$SNAP_ROOT"/snap-* 2>/dev/null | sort | head -n -"$RETAIN" | while read -r old; do
    rm -rf "$old"
done

new_count="$(ls -1d "$SNAP_ROOT"/snap-* 2>/dev/null | wc -l)"
size="$(du -sh "$SNAP_ROOT" 2>/dev/null | awk '{print $1}')"
logger -t "$TAG" -p daemon.info \
    "snapshot snap-$stamp ok (set=${new_count}, total=${size}, linkbase=$(basename "${prev:-none}"))"
echo "$TAG: snap-$stamp ok (set=${new_count}, total=${size}, linkbase=$(basename "${prev:-none}"))"
