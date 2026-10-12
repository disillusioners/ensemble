#!/bin/bash
# ── REPO SOURCE OF TRUTH — mirror of deployed host artifact ─────────────
# Host path: /usr/local/sbin/ensemble-treecheck.sh
# Commission: install-dir protection layers 1+3+4 (incident 891820f3 followup,
# 2026-10-12). Edit HERE, then redeploy to the host path above — see
# scripts/protection/README.md for the deployment map + enable commands.
# ────────────────────────────────────────────────────────────────────────
# ============================================================================
# ensemble-treecheck.sh — Layer 4 fast tree-loss alert for the live ensemble
# install tree.
#
# INCIDENT NOTE (2026-10-12): the install dir /home/nea/agents-ensemble was
# deleted twice in one day (≈12:14 and ≈19:11 UTC) by agent-authored test
# code (instance e312eb09, worktree ensemble-src-wt-upg-overhaul,
# tests/test_journal_splice.sh §2). Immediate root cause = TEST SCRIPT defect
# in the upgrade-speed overhaul lane, ALREADY manually fixed by the user on
# 2026-10-12. This check is the DEFENSE-IN-DEPTH structural backstop against
# the damage class (future script regressions, agent mistakes, human error)
# — NOT tied to the one already-fixed script.
# Plan: protection-plan-install-dir-20261012.md (Layer 4.2).
#
# MECHANISM DEVIATION (documented, commissioned 2026-10-12): the plan named a
# systemd .path unit on releases/state.json, but .path units CANNOT watch for
# ABSENCE of a file (PathExists= fires on presence). The faithful mechanism
# for "alert when state.json disappears" is a 60s polling timer — this
# script, invoked by ensemble-treecheck.timer. Detection latency ≤ ~65s vs
# the 2026-10-12 mean time to notice of ~6h.
#
# Alert delivery: journal CRIT via logger(1) — the commissioned minimum bar.
# A relay through the local ensemble daemon HTTP API was deliberately NOT
# built (would mutate live queues during incident followup; not "trivially
# usable" without side effects). auditd attribution is a separate sub-item
# and was NOT available on this host at commissioning (auditctl absent).
#
# Drillable by design: pass a path argument (or set ENSEMBLE_TREECHECK_PATH)
# to check a throwaway path — verification never touches the live tree.
# Exit codes: 0 = watched path present (healthy); 2 = TREE LOSS (CRIT logged).
# ============================================================================
set -euo pipefail

WATCH="${1:-${ENSEMBLE_TREECHECK_PATH:-/home/nea/agents-ensemble/releases/state.json}}"
TAG="ensemble-treecheck"

if [ ! -e "$WATCH" ]; then
    logger -t "$TAG" -p daemon.crit \
        "TREE LOSS DETECTED: ${WATCH} absent at $(date -u +%Y-%m-%dT%H:%M:%SZ) — install tree /home/nea/agents-ensemble deleted or broken. Restore path: /home/nea/.install-snapshots (Layer 3 snapshots, root-only). Plan: protection-plan-install-dir-20261012.md"
    echo "$TAG: ALERT — $WATCH absent (journal CRIT emitted)"
    exit 2
fi
exit 0
