#!/usr/bin/env bash
# GATE E — live fence capture (PRE / MID / POST). READ-ONLY.
set -uo pipefail
LABEL="$1"
RUN=/tmp/plane-pm-smoke-2026-11-02
STATE=/home/nea/agents-ensemble/releases/state.json
{
  echo "=== LIVE FENCE $LABEL ($(date -u +%FT%TZ)) ==="
  echo "state.json sha256 : $(sha256sum "$STATE" | awk '{print $1}')"
  echo "current symlink   : $(readlink /home/nea/agents-ensemble/current)"
  echo "live :9797 pid    : $(ss -ltnp 2>/dev/null | grep -E ':9797\b' | grep -oE 'pid=[0-9]+' | head -1 | tr -d 'pid=')"
  echo "demo :7979 pid    : $(ss -ltnp 2>/dev/null | grep -E ':7979\b' | grep -oE 'pid=[0-9]+' | head -1 | tr -d 'pid=')"
  echo "live livez        : $(curl -s -m 5 http://127.0.0.1:9797/livez)"
  echo "demo livez        : $(curl -s -m 5 http://127.0.0.1:7979/livez)"
} | tee "$RUN/fence-${LABEL}.txt"
